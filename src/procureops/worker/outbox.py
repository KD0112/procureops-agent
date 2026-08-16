from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from procureops.domain.models import canonical_hash
from procureops.harness.errors import IdempotencyConflict
from procureops.storage import SQLiteDatabase
from procureops.worker.queue import Job, SQLiteWorkQueue


class SQLiteOutbox:
    """Transactional intent log that delivers idempotently into the local work queue."""

    def __init__(self, database: SQLiteDatabase, queue: SQLiteWorkQueue) -> None:
        self.database = database
        self.queue = queue

    @staticmethod
    def stage(
        connection: Any,
        *,
        tenant_id: str,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> str:
        payload_hash = canonical_hash(payload)
        existing = connection.execute(
            "SELECT event_id, payload_hash FROM outbox_events "
            "WHERE tenant_id=? AND idempotency_key=?",
            (tenant_id, idempotency_key),
        ).fetchone()
        if existing is not None:
            if existing["payload_hash"] != payload_hash:
                raise IdempotencyConflict(
                    "outbox idempotency key reused with different payload"
                )
            return str(existing["event_id"])
        event_id = str(uuid4())
        connection.execute(
            """
            INSERT INTO outbox_events(
                event_id, tenant_id, aggregate_type, aggregate_id,
                event_type, payload_json, payload_hash, idempotency_key,
                status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                event_id,
                tenant_id,
                aggregate_type,
                aggregate_id,
                event_type,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                payload_hash,
                idempotency_key,
                datetime.now(UTC).isoformat(),
            ),
        )
        return event_id

    def stage_work(
        self,
        *,
        tenant_id: str,
        task_id: str,
        job_type: str,
        job_payload: dict[str, Any],
        idempotency_key: str,
        max_attempts: int = 3,
    ) -> tuple[str, bool]:
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT event_id FROM outbox_events "
                "WHERE tenant_id=? AND idempotency_key=?",
                (tenant_id, idempotency_key),
            ).fetchone()
            event_id = self.stage(
                connection,
                tenant_id=tenant_id,
                aggregate_type="procurement_task",
                aggregate_id=task_id,
                event_type="work.requested",
                payload={
                    "job_type": job_type,
                    "job_payload": job_payload,
                    "max_attempts": max_attempts,
                },
                idempotency_key=idempotency_key,
            )
        return event_id, existing is not None

    def dispatch_pending(self, *, limit: int = 100) -> tuple[Job, ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT event_id FROM outbox_events
                WHERE status IN ('pending', 'dispatching')
                ORDER BY created_at, event_id LIMIT ?
                """,
                (limit,),
            ).fetchall()
        delivered: list[Job] = []
        for row in rows:
            job = self.dispatch(event_id=row["event_id"])
            if job is not None:
                delivered.append(job)
        return tuple(delivered)

    def dispatch(self, *, event_id: str) -> Job | None:
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM outbox_events WHERE event_id=?",
                (event_id,),
            ).fetchone()
            if row is None:
                raise KeyError("outbox event not found")
            if row["status"] == "dispatched":
                return self.queue.job_for_idempotency(
                    tenant_id=row["tenant_id"],
                    idempotency_key=row["idempotency_key"],
                )
            connection.execute(
                """
                UPDATE outbox_events
                SET status='dispatching', attempts=attempts+1, last_error_class=NULL
                WHERE event_id=? AND status IN ('pending', 'dispatching')
                """,
                (event_id,),
            )
        payload = json.loads(row["payload_json"])
        try:
            job, _reused = self.queue.enqueue(
                tenant_id=row["tenant_id"],
                task_id=row["aggregate_id"],
                job_type=str(payload["job_type"]),
                payload=dict(payload["job_payload"]),
                idempotency_key=row["idempotency_key"],
                max_attempts=int(payload.get("max_attempts", 3)),
            )
        except Exception as exc:
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    UPDATE outbox_events SET status='pending', last_error_class=?
                    WHERE event_id=? AND status='dispatching'
                    """,
                    (type(exc).__name__, event_id),
                )
            raise
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE outbox_events SET status='dispatched', dispatched_at=?
                WHERE event_id=? AND status='dispatching'
                """,
                (datetime.now(UTC).isoformat(), event_id),
            )
        return job

    def event_payload(self, *, event_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM outbox_events WHERE event_id=?",
                (event_id,),
            ).fetchone()
        if row is None:
            raise KeyError("outbox event not found")
        return {
            "event_id": str(row["event_id"]),
            "tenant_id": str(row["tenant_id"]),
            "task_id": str(row["aggregate_id"]),
            "idempotency_key": str(row["idempotency_key"]),
            **json.loads(row["payload_json"]),
        }

    def mark_dispatched(self, *, event_id: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE outbox_events
                SET status='dispatched', dispatched_at=?
                WHERE event_id=? AND status IN ('pending', 'dispatching')
                """,
                (datetime.now(UTC).isoformat(), event_id),
            )

    def events(self, *, tenant_id: str) -> tuple[dict[str, Any], ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT event_id, aggregate_type, aggregate_id, event_type,
                       idempotency_key, status, attempts, last_error_class,
                       created_at, dispatched_at
                FROM outbox_events WHERE tenant_id=?
                ORDER BY created_at, event_id
                """,
                (tenant_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)
