from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from procureops.domain.models import canonical_hash
from procureops.harness.errors import IdempotencyConflict
from procureops.storage import SQLiteDatabase


class Job(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: str
    tenant_id: str
    task_id: str
    job_type: str
    payload: dict[str, Any]
    status: str
    attempts: int
    max_attempts: int
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None


class SQLiteWorkQueue:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def enqueue(
        self,
        *,
        tenant_id: str,
        task_id: str,
        job_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
        max_attempts: int = 3,
        available_at: datetime | None = None,
    ) -> tuple[Job, bool]:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        payload_hash = canonical_hash(payload)
        now = datetime.now(UTC)
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM work_queue WHERE tenant_id=? AND idempotency_key=?",
                (tenant_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["payload_hash"] != payload_hash:
                    raise IdempotencyConflict("queue idempotency key reused with different payload")
                return _job(existing), True
            job_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO work_queue(
                    job_id, tenant_id, task_id, job_type, payload_json,
                    payload_hash, idempotency_key, status, max_attempts,
                    available_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
                """,
                (
                    job_id,
                    tenant_id,
                    task_id,
                    job_type,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    payload_hash,
                    idempotency_key,
                    max_attempts,
                    (available_at or now).isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM work_queue WHERE job_id=?",
                (job_id,),
            ).fetchone()
        return _job(row), False

    def claim(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 60,
        now: datetime | None = None,
    ) -> Job | None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        current = now or datetime.now(UTC)
        expires = current + timedelta(seconds=lease_seconds)
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM work_queue
                WHERE attempts < max_attempts AND (
                    (status IN ('pending', 'retry') AND available_at <= ?)
                    OR (status='leased' AND lease_expires_at <= ?)
                )
                ORDER BY created_at, job_id
                LIMIT 1
                """,
                (current.isoformat(), current.isoformat()),
            ).fetchone()
            if row is None:
                return None
            cursor = connection.execute(
                """
                UPDATE work_queue
                SET status='leased', attempts=attempts+1, lease_owner=?,
                    lease_expires_at=?, updated_at=?
                WHERE job_id=? AND (
                    status IN ('pending', 'retry')
                    OR (status='leased' AND lease_expires_at <= ?)
                )
                """,
                (
                    worker_id,
                    expires.isoformat(),
                    current.isoformat(),
                    row["job_id"],
                    current.isoformat(),
                ),
            )
            if cursor.rowcount != 1:
                return None
            claimed = connection.execute(
                "SELECT * FROM work_queue WHERE job_id=?",
                (row["job_id"],),
            ).fetchone()
        return _job(claimed)

    def succeed(self, *, job_id: str, worker_id: str) -> None:
        self._finish(
            job_id=job_id,
            worker_id=worker_id,
            status="succeeded",
            error=None,
            available_at=None,
        )

    def fail(
        self,
        *,
        job: Job,
        worker_id: str,
        error: Exception,
        retryable: bool,
        now: datetime | None = None,
    ) -> str:
        current = now or datetime.now(UTC)
        exhausted = job.attempts >= job.max_attempts
        status = "retry" if retryable and not exhausted else "dead_letter"
        backoff_seconds = min(300, 2 ** max(0, job.attempts - 1))
        self._finish(
            job_id=job.job_id,
            worker_id=worker_id,
            status=status,
            error=error,
            available_at=(
                current + timedelta(seconds=backoff_seconds) if status == "retry" else None
            ),
        )
        return status

    def _finish(
        self,
        *,
        job_id: str,
        worker_id: str,
        status: str,
        error: Exception | None,
        available_at: datetime | None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE work_queue
                SET status=?, lease_owner=NULL, lease_expires_at=NULL,
                    available_at=COALESCE(?, available_at),
                    last_error_class=?, last_error_message=?, updated_at=?
                WHERE job_id=? AND status='leased' AND lease_owner=?
                """,
                (
                    status,
                    available_at.isoformat() if available_at else None,
                    type(error).__name__ if error else None,
                    str(error)[:500] if error else None,
                    now,
                    job_id,
                    worker_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("worker does not own an active lease")

    def jobs_for_task(self, *, tenant_id: str, task_id: str) -> tuple[Job, ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM work_queue
                WHERE tenant_id=? AND task_id=? ORDER BY created_at, job_id
                """,
                (tenant_id, task_id),
            ).fetchall()
        return tuple(_job(row) for row in rows)

    def job_for_idempotency(
        self,
        *,
        tenant_id: str,
        idempotency_key: str,
    ) -> Job | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM work_queue WHERE tenant_id=? AND idempotency_key=?",
                (tenant_id, idempotency_key),
            ).fetchone()
        return _job(row) if row is not None else None


def _job(row) -> Job:
    return Job(
        job_id=row["job_id"],
        tenant_id=row["tenant_id"],
        task_id=row["task_id"],
        job_type=row["job_type"],
        payload=json.loads(row["payload_json"]),
        status=row["status"],
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        lease_owner=row["lease_owner"],
        lease_expires_at=(
            datetime.fromisoformat(row["lease_expires_at"]) if row["lease_expires_at"] else None
        ),
    )
