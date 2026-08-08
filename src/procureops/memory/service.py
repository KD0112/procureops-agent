from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from procureops.storage import SQLiteDatabase

FORBIDDEN_KEY_PATTERNS = (
    r"password",
    r"api[_-]?key",
    r"token",
    r"credential",
    r"bank",
    r"card",
    r"national[_-]?id",
    r"身份证",
    r"密码",
    r"银行卡",
)
PROTECTED_POLICY_KEYS = frozenset(
    {
        "approval_threshold",
        "approved_supplier_only",
        "currency",
        "policy",
        "risk_level",
    }
)
MEMORY_INJECTION_PATTERNS = (
    r"ignore\s+(?:all\s+)?(?:previous|system)",
    r"<\/?system>",
    r"绕过.{0,12}(?:审批|规则|权限)",
    r"忽略.{0,12}(?:系统|规则|审批)",
    r"(?:直接|自动).{0,8}(?:下单|批准|放行)",
)
MAX_MEMORY_VALUE_BYTES = 2048


class MemoryIntegrityError(RuntimeError):
    pass


class MemoryRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    record_id: str
    tenant_id: str
    user_id: str
    memory_key: str
    value: Any
    status: str
    sensitivity: str
    confidence: float = Field(ge=0, le=1)
    proposed_by: str
    confirmed_by: str | None = None
    replaces_record_id: str | None = None
    created_at: datetime
    confirmed_at: datetime | None = None
    expires_at: datetime
    deleted_at: datetime | None = None
    source_hash: str | None = None
    integrity_hash: str


class MemoryService:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def propose(
        self,
        *,
        tenant_id: str,
        user_id: str,
        memory_key: str,
        value: Any,
        confidence: float,
        proposed_by: str,
        ttl: timedelta = timedelta(days=90),
        source_hash: str | None = None,
    ) -> MemoryRecord:
        try:
            self._validate_key(memory_key)
            self._validate_value(value)
        except ValueError:
            self._audit_access(
                tenant_id=tenant_id,
                user_id=user_id,
                record_id=None,
                action="rejected",
                decision="validation_rejected",
                metadata={"memory_key": memory_key, "proposed_by": proposed_by},
            )
            raise
        if ttl <= timedelta(0) or ttl > timedelta(days=365):
            raise ValueError("memory TTL must be between 1 second and 365 days")
        now = datetime.now(UTC)
        normalized_source_hash = source_hash or self._source_hash(
            memory_key=memory_key,
            value=value,
            proposed_by=proposed_by,
        )
        with self.database.connect() as connection:
            existing = connection.execute(
                """
                SELECT record_id FROM memory_records
                WHERE tenant_id=? AND user_id=? AND memory_key=? AND source_hash=?
                  AND status IN ('candidate', 'confirmed') AND expires_at>?
                ORDER BY created_at DESC LIMIT 1
                """,
                (
                    tenant_id,
                    user_id,
                    memory_key,
                    normalized_source_hash,
                    now.isoformat(),
                ),
            ).fetchone()
        if existing is not None:
            return self.get(
                tenant_id=tenant_id,
                user_id=user_id,
                record_id=existing["record_id"],
            )
        record_id = str(uuid4())
        created_at = now.isoformat()
        expires_at = (now + ttl).isoformat()
        value_json = json.dumps(value, ensure_ascii=False, sort_keys=True)
        integrity_hash = self._integrity_hash(
            record_id=record_id,
            tenant_id=tenant_id,
            user_id=user_id,
            memory_key=memory_key,
            value_json=value_json,
            proposed_by=proposed_by,
            replaces_record_id=None,
            created_at=created_at,
            expires_at=expires_at,
            source_hash=normalized_source_hash,
        )
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO memory_records(
                    record_id, tenant_id, user_id, memory_key, value_json,
                    status, sensitivity, confidence, proposed_by,
                    created_at, expires_at, source_hash, integrity_hash
                ) VALUES (?, ?, ?, ?, ?, 'candidate', 'non_sensitive', ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    tenant_id,
                    user_id,
                    memory_key,
                    value_json,
                    str(confidence),
                    proposed_by,
                    created_at,
                    expires_at,
                    normalized_source_hash,
                    integrity_hash,
                ),
            )
            self._insert_access_event(
                connection,
                tenant_id=tenant_id,
                user_id=user_id,
                record_id=record_id,
                action="propose",
                decision="candidate_created",
                metadata={"memory_key": memory_key, "proposed_by": proposed_by},
            )
        return self.get(tenant_id=tenant_id, user_id=user_id, record_id=record_id)

    def confirm(
        self,
        *,
        tenant_id: str,
        user_id: str,
        record_id: str,
        confirmed_by: str,
    ) -> MemoryRecord:
        now = datetime.now(UTC)
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE memory_records
                SET status='confirmed', confirmed_by=?, confirmed_at=?
                WHERE tenant_id=? AND user_id=? AND record_id=?
                  AND status='candidate' AND expires_at>?
                """,
                (
                    confirmed_by,
                    now.isoformat(),
                    tenant_id,
                    user_id,
                    record_id,
                    now.isoformat(),
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("memory candidate is missing, expired, or not confirmable")
            self._insert_access_event(
                connection,
                tenant_id=tenant_id,
                user_id=user_id,
                record_id=record_id,
                action="confirm",
                decision="confirmed",
                metadata={"confirmed_by": confirmed_by},
            )
        return self.get(tenant_id=tenant_id, user_id=user_id, record_id=record_id)

    def correct(
        self,
        *,
        tenant_id: str,
        user_id: str,
        record_id: str,
        new_value: Any,
        corrected_by: str,
        ttl: timedelta = timedelta(days=90),
    ) -> MemoryRecord:
        original = self.get(tenant_id=tenant_id, user_id=user_id, record_id=record_id)
        if original.status != "confirmed":
            raise ValueError("only confirmed memory can be corrected")
        self._validate_value(new_value)
        now = datetime.now(UTC)
        replacement_id = str(uuid4())
        created_at = now.isoformat()
        expires_at = (now + ttl).isoformat()
        value_json = json.dumps(new_value, ensure_ascii=False, sort_keys=True)
        integrity_hash = self._integrity_hash(
            record_id=replacement_id,
            tenant_id=tenant_id,
            user_id=user_id,
            memory_key=original.memory_key,
            value_json=value_json,
            proposed_by=corrected_by,
            replaces_record_id=record_id,
            created_at=created_at,
            expires_at=expires_at,
            source_hash=None,
        )
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE memory_records SET status='corrected'
                WHERE tenant_id=? AND user_id=? AND record_id=? AND status='confirmed'
                """,
                (tenant_id, user_id, record_id),
            )
            connection.execute(
                """
                INSERT INTO memory_records(
                    record_id, tenant_id, user_id, memory_key, value_json,
                    status, sensitivity, confidence, proposed_by, confirmed_by,
                    replaces_record_id, created_at, confirmed_at, expires_at,
                    integrity_hash
                ) VALUES (?, ?, ?, ?, ?, 'confirmed', 'non_sensitive', '1.0',
                          ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    replacement_id,
                    tenant_id,
                    user_id,
                    original.memory_key,
                    value_json,
                    corrected_by,
                    corrected_by,
                    record_id,
                    created_at,
                    now.isoformat(),
                    expires_at,
                    integrity_hash,
                ),
            )
            self._insert_access_event(
                connection,
                tenant_id=tenant_id,
                user_id=user_id,
                record_id=replacement_id,
                action="correct",
                decision="replacement_confirmed",
                metadata={
                    "corrected_by": corrected_by,
                    "replaces_record_id": record_id,
                },
            )
        return self.get(tenant_id=tenant_id, user_id=user_id, record_id=replacement_id)

    def delete(
        self,
        *,
        tenant_id: str,
        user_id: str,
        record_id: str,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE memory_records SET status='deleted', deleted_at=?
                WHERE tenant_id=? AND user_id=? AND record_id=?
                  AND status IN ('candidate', 'confirmed')
                """,
                (now, tenant_id, user_id, record_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("memory is missing or already inactive")
            self._insert_access_event(
                connection,
                tenant_id=tenant_id,
                user_id=user_id,
                record_id=record_id,
                action="delete",
                decision="soft_deleted",
                metadata={},
            )

    def active_preferences(
        self,
        *,
        tenant_id: str,
        user_id: str,
        at: datetime | None = None,
    ) -> dict[str, Any]:
        records = self.active_records(tenant_id=tenant_id, user_id=user_id, at=at)
        return {
            record.memory_key: record.value
            for record in records
            if record.memory_key not in PROTECTED_POLICY_KEYS
        }

    def list_records(
        self,
        *,
        tenant_id: str,
        user_id: str,
        status: str | None = None,
    ) -> tuple[MemoryRecord, ...]:
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE memory_records SET status='expired'
                WHERE tenant_id=? AND user_id=?
                  AND status IN ('candidate', 'confirmed') AND expires_at<=?
                """,
                (tenant_id, user_id, now),
            )
            query = (
                "SELECT record_id FROM memory_records "
                "WHERE tenant_id=? AND user_id=?"
            )
            parameters: list[Any] = [tenant_id, user_id]
            if status is not None:
                query += " AND status=?"
                parameters.append(status)
            query += " ORDER BY created_at DESC"
            rows = connection.execute(query, parameters).fetchall()
        return tuple(
            self.get(
                tenant_id=tenant_id,
                user_id=user_id,
                record_id=row["record_id"],
            )
            for row in rows
        )

    def active_records(
        self,
        *,
        tenant_id: str,
        user_id: str,
        at: datetime | None = None,
    ) -> tuple[MemoryRecord, ...]:
        now = (at or datetime.now(UTC)).isoformat()
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT record_id FROM memory_records
                WHERE tenant_id=? AND user_id=? AND status='confirmed'
                  AND expires_at>? ORDER BY confirmed_at
                """,
                (tenant_id, user_id, now),
            ).fetchall()
        return tuple(
            self.get(
                tenant_id=tenant_id,
                user_id=user_id,
                record_id=row["record_id"],
            )
            for row in rows
        )

    def get(self, *, tenant_id: str, user_id: str, record_id: str) -> MemoryRecord:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM memory_records
                WHERE tenant_id=? AND user_id=? AND record_id=?
                """,
                (tenant_id, user_id, record_id),
            ).fetchone()
        if row is None:
            raise KeyError("memory record not found in user and tenant scope")
        expected_integrity_hash = self._integrity_hash(
            record_id=row["record_id"],
            tenant_id=row["tenant_id"],
            user_id=row["user_id"],
            memory_key=row["memory_key"],
            value_json=row["value_json"],
            proposed_by=row["proposed_by"],
            replaces_record_id=row["replaces_record_id"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            source_hash=row["source_hash"],
        )
        integrity_hash = row["integrity_hash"]
        if integrity_hash is None:
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE memory_records SET integrity_hash=? WHERE record_id=? "
                    "AND integrity_hash IS NULL",
                    (expected_integrity_hash, record_id),
                )
            integrity_hash = expected_integrity_hash
        elif not hmac.compare_digest(integrity_hash, expected_integrity_hash):
            self._audit_access(
                tenant_id=tenant_id,
                user_id=user_id,
                record_id=record_id,
                action="rejected",
                decision="integrity_mismatch",
                metadata={"memory_key": row["memory_key"]},
            )
            raise MemoryIntegrityError("memory integrity verification failed")
        self._audit_access(
            tenant_id=tenant_id,
            user_id=user_id,
            record_id=record_id,
            action="read",
            decision="integrity_verified",
            metadata={"memory_key": row["memory_key"], "status": row["status"]},
        )
        return MemoryRecord(
            record_id=row["record_id"],
            tenant_id=row["tenant_id"],
            user_id=row["user_id"],
            memory_key=row["memory_key"],
            value=json.loads(row["value_json"]),
            status=row["status"],
            sensitivity=row["sensitivity"],
            confidence=float(row["confidence"]),
            proposed_by=row["proposed_by"],
            confirmed_by=row["confirmed_by"],
            replaces_record_id=row["replaces_record_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            confirmed_at=(
                datetime.fromisoformat(row["confirmed_at"])
                if row["confirmed_at"]
                else None
            ),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            deleted_at=(
                datetime.fromisoformat(row["deleted_at"]) if row["deleted_at"] else None
            ),
            source_hash=row["source_hash"],
            integrity_hash=integrity_hash,
        )

    def access_events(
        self,
        *,
        tenant_id: str,
        user_id: str,
    ) -> tuple[dict[str, Any], ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT event_id, record_id, action, decision, metadata_hash, occurred_at
                FROM memory_access_events
                WHERE tenant_id=? AND user_id=?
                ORDER BY occurred_at, event_id
                """,
                (tenant_id, user_id),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    @staticmethod
    def _validate_key(memory_key: str) -> None:
        normalized = memory_key.casefold().strip()
        if not normalized:
            raise ValueError("memory key is required")
        if normalized in PROTECTED_POLICY_KEYS:
            raise ValueError("memory cannot override procurement policy")
        if any(re.search(pattern, normalized) for pattern in FORBIDDEN_KEY_PATTERNS):
            raise ValueError("sensitive memory key is prohibited")

    @staticmethod
    def _validate_value(value: Any) -> None:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(encoded.encode("utf-8")) > MAX_MEMORY_VALUE_BYTES:
            raise ValueError("memory value exceeds the governed size limit")
        if any(re.search(pattern, encoded, re.IGNORECASE) for pattern in MEMORY_INJECTION_PATTERNS):
            raise ValueError("memory value contains instruction-like content")

    @staticmethod
    def _source_hash(*, memory_key: str, value: Any, proposed_by: str) -> str:
        payload = json.dumps(
            {"key": memory_key, "value": value, "proposed_by": proposed_by},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _integrity_hash(**fields: Any) -> str:
        encoded = json.dumps(
            fields,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _audit_access(
        self,
        *,
        tenant_id: str,
        user_id: str,
        record_id: str | None,
        action: str,
        decision: str,
        metadata: dict[str, Any],
    ) -> None:
        with self.database.transaction() as connection:
            self._insert_access_event(
                connection,
                tenant_id=tenant_id,
                user_id=user_id,
                record_id=record_id,
                action=action,
                decision=decision,
                metadata=metadata,
            )

    @staticmethod
    def _insert_access_event(
        connection: Any,
        *,
        tenant_id: str,
        user_id: str,
        record_id: str | None,
        action: str,
        decision: str,
        metadata: dict[str, Any],
    ) -> None:
        metadata_hash = hashlib.sha256(
            json.dumps(
                metadata,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        connection.execute(
            """
            INSERT INTO memory_access_events(
                event_id, tenant_id, user_id, record_id, action,
                decision, metadata_hash, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                tenant_id,
                user_id,
                record_id,
                action,
                decision,
                metadata_hash,
                datetime.now(UTC).isoformat(),
            ),
        )
