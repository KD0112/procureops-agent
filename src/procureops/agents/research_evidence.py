from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ResearchEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str
    supplier_id: str
    product_id: str | None = None
    source_id: str
    source_type: str
    locator: str
    observed_at: datetime
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    claim_key: str
    claim_value: str
    claim: str = Field(min_length=1, max_length=2_000)
    relevance: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    trust_tier: Literal["authoritative", "partner", "public"]


class RejectedEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str
    supplier_id: str
    reason: str


class EvidenceJudgment(BaseModel):
    model_config = ConfigDict(frozen=True)

    accepted: tuple[ResearchEvidence, ...]
    rejected: tuple[RejectedEvidence, ...]
    conflicts: tuple[str, ...]


class EvidenceJudge:
    _injection_pattern = re.compile(
        r"ignore\s+(all\s+)?previous|system>|developer>|tool_call|"
        r"绕过|忽略.{0,8}(规则|指令)|直接.{0,4}(下单|批准)",
        re.IGNORECASE,
    )
    _dynamic_markers = (
        "price",
        "inventory",
        "stock",
        "lead_time",
        "approval_status",
        "supplier_status",
        "current_quote",
    )

    def __init__(self, *, minimum_relevance: float = 0.6, minimum_confidence: float = 0.5):
        self.minimum_relevance = minimum_relevance
        self.minimum_confidence = minimum_confidence

    def judge(
        self,
        *,
        tenant_id: str,
        approved_supplier_ids: frozenset[str],
        evidence: tuple[ResearchEvidence, ...],
    ) -> EvidenceJudgment:
        accepted: list[ResearchEvidence] = []
        rejected: list[RejectedEvidence] = []
        seen: set[tuple[str, str]] = set()
        for item in evidence:
            reason = self._rejection_reason(
                item=item,
                tenant_id=tenant_id,
                approved_supplier_ids=approved_supplier_ids,
                seen=seen,
            )
            seen.add((item.source_id, item.content_hash))
            if reason is not None:
                rejected.append(
                    RejectedEvidence(
                        source_id=item.source_id,
                        supplier_id=item.supplier_id,
                        reason=reason,
                    )
                )
            else:
                accepted.append(item)
        values_by_claim: dict[tuple[str, str], set[str]] = {}
        for item in accepted:
            key = (item.supplier_id, item.claim_key.casefold())
            values_by_claim.setdefault(key, set()).add(item.claim_value.casefold().strip())
        conflicts = tuple(
            sorted(
                f"{supplier_id}:{claim_key}"
                for (supplier_id, claim_key), values in values_by_claim.items()
                if len(values) > 1
            )
        )
        return EvidenceJudgment(
            accepted=tuple(accepted),
            rejected=tuple(rejected),
            conflicts=conflicts,
        )

    def _rejection_reason(
        self,
        *,
        item: ResearchEvidence,
        tenant_id: str,
        approved_supplier_ids: frozenset[str],
        seen: set[tuple[str, str]],
    ) -> str | None:
        if item.tenant_id != tenant_id:
            return "tenant_mismatch"
        if item.supplier_id not in approved_supplier_ids:
            return "supplier_not_approved"
        if (item.source_id, item.content_hash) in seen:
            return "duplicate"
        if any(marker in item.claim_key.casefold() for marker in self._dynamic_markers):
            return "dynamic_fact_prohibited"
        if self._injection_pattern.search(item.claim):
            return "prompt_injection"
        if item.relevance < self.minimum_relevance:
            return "low_relevance"
        if item.confidence < self.minimum_confidence:
            return "low_confidence"
        return None
