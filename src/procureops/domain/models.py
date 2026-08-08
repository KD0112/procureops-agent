from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def canonical_hash(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class RunBudget(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_model_calls: int = Field(default=8, ge=0)
    max_tool_calls: int = Field(default=24, ge=0)
    max_tokens: int = Field(default=32_000, ge=0)
    max_cost_usd: float = Field(default=2.0, ge=0)


class RunContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    actor_roles: frozenset[str]
    workflow_version: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    model_policy_version: str = Field(min_length=1)
    rule_set_version: str = Field(min_length=1)
    tenant_pack_version: str = Field(min_length=1)
    deadline_at: datetime
    budget: RunBudget = Field(default_factory=RunBudget)
    correlation_id: str = Field(min_length=1)

    @field_validator("deadline_at")
    @classmethod
    def deadline_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("deadline_at must be timezone-aware")
        return value.astimezone(UTC)

    def is_expired(self, now: datetime | None = None) -> bool:
        current = now or datetime.now(UTC)
        return current.astimezone(UTC) >= self.deadline_at


class ApprovalGrant(BaseModel):
    model_config = ConfigDict(frozen=True)

    approval_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    subject_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved_by: str = Field(min_length=1)
    approved_by_roles: frozenset[str] = frozenset()
    approved_at: datetime
    expires_at: datetime

    @field_validator("approved_at", "expires_at")
    @classmethod
    def approval_times_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("approval timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def expiry_must_follow_approval(self) -> ApprovalGrant:
        if self.expires_at <= self.approved_at:
            raise ValueError("expires_at must be later than approved_at")
        return self

    @classmethod
    def bind(
        cls,
        *,
        approval_id: str,
        tenant_id: str,
        task_id: str,
        action: str,
        subject: Any,
        approved_by: str,
        approved_by_roles: frozenset[str] = frozenset(),
        approved_at: datetime,
        expires_at: datetime,
    ) -> ApprovalGrant:
        return cls(
            approval_id=approval_id,
            tenant_id=tenant_id,
            task_id=task_id,
            action=action,
            subject_hash=canonical_hash(subject),
            approved_by=approved_by,
            approved_by_roles=approved_by_roles,
            approved_at=approved_at,
            expires_at=expires_at,
        )

    def authorizes(
        self,
        *,
        context: RunContext,
        action: str,
        subject: Any,
        now: datetime | None = None,
    ) -> bool:
        current = now or datetime.now(UTC)
        return all(
            (
                self.tenant_id == context.tenant_id,
                self.task_id == context.task_id,
                self.action == action,
                self.subject_hash == canonical_hash(subject),
                current.astimezone(UTC) < self.expires_at.astimezone(UTC),
            )
        )


class EvidenceRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidence_id: str
    tenant_id: str
    source_type: str
    source_id: str
    locator: str
    observed_at: datetime
    valid_until: datetime | None = None
    confidence: float = Field(ge=0, le=1)
    producer: str
