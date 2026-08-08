from __future__ import annotations

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
    ) -> MemoryRecord:
        self._validate_key(memory_key)
        if ttl <= timedelta(0) or ttl > timedelta(days=365):
            raise ValueError("memory TTL must be between 1 second and 365 days")
        now = datetime.now(UTC)
        record_id = str(uuid4())
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO memory_records(
                    record_id, tenant_id, user_id, memory_key, value_json,
                    status, sensitivity, confidence, proposed_by,
                    created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, 'candidate', 'non_sensitive', ?, ?, ?, ?)
                """,
                (
                    record_id,
                    tenant_id,
                    user_id,
                    memory_key,
                    json.dumps(value, ensure_ascii=False, sort_keys=True),
                    str(confidence),
                    proposed_by,
                    now.isoformat(),
                    (now + ttl).isoformat(),
                ),
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
        now = datetime.now(UTC)
        replacement_id = str(uuid4())
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
                    replaces_record_id, created_at, confirmed_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, 'confirmed', 'non_sensitive', '1.0',
                          ?, ?, ?, ?, ?, ?)
                """,
                (
                    replacement_id,
                    tenant_id,
                    user_id,
                    original.memory_key,
                    json.dumps(new_value, ensure_ascii=False, sort_keys=True),
                    corrected_by,
                    corrected_by,
                    record_id,
                    now.isoformat(),
                    now.isoformat(),
                    (now + ttl).isoformat(),
                ),
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
        )

    @staticmethod
    def _validate_key(memory_key: str) -> None:
        normalized = memory_key.casefold().strip()
        if not normalized:
            raise ValueError("memory key is required")
        if normalized in PROTECTED_POLICY_KEYS:
            raise ValueError("memory cannot override procurement policy")
        if any(re.search(pattern, normalized) for pattern in FORBIDDEN_KEY_PATTERNS):
            raise ValueError("sensitive memory key is prohibited")
