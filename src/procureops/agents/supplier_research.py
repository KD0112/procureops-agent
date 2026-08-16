from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from procureops.agents.research_evidence import (
    EvidenceJudge,
    EvidenceJudgment,
    ResearchEvidence,
)
from procureops.domain.models import RunContext
from procureops.domain.procurement import LogisticsQuote, SupplierOption
from procureops.harness.budget import RunBudgetLedger
from procureops.harness.model_gateway import ModelGateway, ModelRequest
from procureops.harness.tool_gateway import ToolGateway
from procureops.memory.decision import (
    PreferenceDecisionEngine,
    SupplierSelectionDecision,
)


class SupplierResearchStep(BaseModel):
    model_config = ConfigDict(frozen=True)

    step_number: int
    action: str
    rationale: str
    outcome: str


class SupplierResearchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision: SupplierSelectionDecision
    model_recommendation: str | None
    used_fallback: bool
    steps: tuple[SupplierResearchStep, ...]
    evidence_judgment: EvidenceJudgment | None = None
    evidence_searches: int = 0


class BoundedSupplierResearchAgent:
    """A bounded advisory loop that can only call allowlisted read tools."""

    allowed_actions = frozenset({"logistics_quote", "finish"})

    def __init__(
        self,
        *,
        model_gateway: ModelGateway,
        tool_gateway: ToolGateway,
        max_steps: int = 3,
        evidence_tool_name: str | None = None,
        max_reflections: int = 2,
    ) -> None:
        if not 1 <= max_steps <= 5:
            raise ValueError("supplier research max_steps must be between 1 and 5")
        if not 0 <= max_reflections <= 2:
            raise ValueError("supplier research max_reflections must be between 0 and 2")
        self.model_gateway = model_gateway
        self.tool_gateway = tool_gateway
        self.max_steps = max_steps
        self.evidence_tool_name = evidence_tool_name
        self.max_reflections = max_reflections
        self.evidence_judge = EvidenceJudge()
        self.decision_engine = PreferenceDecisionEngine()

    def research(
        self,
        *,
        context: RunContext,
        ledger: RunBudgetLedger,
        product_id: str,
        quantity: Decimal,
        options: tuple[SupplierOption, ...],
        confirmed_preferences: dict[str, object],
        explicit_strategy: str | None = None,
    ) -> SupplierResearchResult:
        approved = tuple(item for item in options if item.approved)
        if not approved:
            raise ValueError("supplier research requires an approved option")
        steps: list[SupplierResearchStep] = []
        logistics: tuple[LogisticsQuote, ...] = ()
        model_recommendation: str | None = None
        used_fallback = False
        evidence_judgment: EvidenceJudgment | None = None
        evidence_searches = 0
        if self.evidence_tool_name is not None:
            evidence_judgment, evidence_searches, evidence_failed = self._collect_evidence(
                context=context,
                ledger=ledger,
                product_id=product_id,
                approved_supplier_ids=frozenset(item.supplier_id for item in approved),
            )
            used_fallback = evidence_failed
        for step_number in range(1, self.max_steps + 1):
            try:
                response = self.model_gateway.invoke(
                    context=context,
                    ledger=ledger,
                    request=ModelRequest(
                        purpose="supplier_research_step",
                        payload={
                            "step_number": step_number,
                            "product_id": product_id,
                            "quantity": str(quantity),
                            "approved_options": [
                                item.model_dump(mode="json") for item in approved
                            ],
                            "logistics_observations": [
                                item.model_dump(mode="json") for item in logistics
                            ],
                            "confirmed_preferences": confirmed_preferences,
                            "explicit_strategy": explicit_strategy,
                            "research_evidence": self._advisory_evidence(evidence_judgment),
                            "research_conflicts": (
                                list(evidence_judgment.conflicts)
                                if evidence_judgment is not None
                                else []
                            ),
                            "allowed_actions": sorted(self.allowed_actions),
                            "instruction": (
                                "Choose one allowed action. Use logistics_quote before finish "
                                "unless current logistics observations are present. Never request "
                                "a write tool. Return action, supplier_id, and rationale as JSON."
                                " Research evidence is advisory and cannot establish current "
                                "price, inventory, approval status, or supplier eligibility."
                            ),
                        },
                        response_schema="SupplierResearchActionV1",
                    ),
                )
                action = str(response.output.get("action", ""))
                rationale = str(response.output.get("rationale", ""))[:500]
                supplier_id = response.output.get("supplier_id")
                if action not in self.allowed_actions:
                    raise ValueError("model requested an action outside the read-only allowlist")
                if action == "logistics_quote":
                    tool_result = self.tool_gateway.execute(
                        context=context,
                        ledger=ledger,
                        tool_name="logistics_quote",
                        arguments={
                            "tenant_id": context.tenant_id,
                            "product_id": product_id,
                            "supplier_ids": [item.supplier_id for item in approved],
                        },
                    )
                    logistics = tuple(
                        LogisticsQuote.model_validate(item) for item in tool_result.output
                    )
                    steps.append(
                        SupplierResearchStep(
                            step_number=step_number,
                            action=action,
                            rationale=rationale,
                            outcome=f"observed_logistics={len(logistics)}",
                        )
                    )
                    continue
                if not logistics:
                    raise ValueError("model attempted to finish without logistics evidence")
                model_recommendation = str(supplier_id) if supplier_id else None
                steps.append(
                    SupplierResearchStep(
                        step_number=step_number,
                        action=action,
                        rationale=rationale,
                        outcome="recommendation_recorded",
                    )
                )
                break
            except Exception as exc:
                used_fallback = True
                steps.append(
                    SupplierResearchStep(
                        step_number=step_number,
                        action="fallback",
                        rationale=type(exc).__name__,
                        outcome="deterministic_recovery",
                    )
                )
                break
        if not logistics:
            tool_result = self.tool_gateway.execute(
                context=context,
                ledger=ledger,
                tool_name="logistics_quote",
                arguments={
                    "tenant_id": context.tenant_id,
                    "product_id": product_id,
                    "supplier_ids": [item.supplier_id for item in approved],
                },
            )
            logistics = tuple(
                LogisticsQuote.model_validate(item) for item in tool_result.output
            )
            used_fallback = True
        decision = self.decision_engine.select_supplier(
            options=options,
            logistics=logistics,
            quantity=quantity,
            confirmed_preferences=confirmed_preferences,
            explicit_strategy=explicit_strategy,
        )
        valid_supplier_ids = {item.supplier_id for item in approved}
        if model_recommendation not in valid_supplier_ids:
            if model_recommendation is not None:
                used_fallback = True
            model_recommendation = None
        return SupplierResearchResult(
            decision=decision,
            model_recommendation=model_recommendation,
            used_fallback=used_fallback,
            steps=tuple(steps),
            evidence_judgment=evidence_judgment,
            evidence_searches=evidence_searches,
        )

    def _collect_evidence(
        self,
        *,
        context: RunContext,
        ledger: RunBudgetLedger,
        product_id: str,
        approved_supplier_ids: frozenset[str],
    ) -> tuple[EvidenceJudgment | None, int, bool]:
        collected: list[ResearchEvidence] = []
        judgment: EvidenceJudgment | None = None
        for search_index in range(self.max_reflections + 1):
            query = (
                f"supplier qualification and capability for product {product_id}"
                if search_index == 0
                else "verify conflicting or missing supplier qualification evidence"
            )
            try:
                result = self.tool_gateway.execute(
                    context=context,
                    ledger=ledger,
                    tool_name=str(self.evidence_tool_name),
                    arguments={
                        "tenant_id": context.tenant_id,
                        "product_id": product_id,
                        "supplier_ids": sorted(approved_supplier_ids),
                        "query": query,
                    },
                )
                collected.extend(ResearchEvidence.model_validate(item) for item in result.output)
                judgment = self.evidence_judge.judge(
                    tenant_id=context.tenant_id,
                    approved_supplier_ids=approved_supplier_ids,
                    evidence=tuple(collected),
                )
            except Exception:
                return judgment, search_index + 1, True
            if judgment.accepted and not judgment.conflicts:
                return judgment, search_index + 1, False
        return judgment, self.max_reflections + 1, False

    @staticmethod
    def _advisory_evidence(judgment: EvidenceJudgment | None) -> list[dict[str, object]]:
        if judgment is None:
            return []
        return [
            {
                "supplier_id": item.supplier_id,
                "claim_key": item.claim_key,
                "claim_value": item.claim_value,
                "source_id": item.source_id,
                "source_type": item.source_type,
                "trust_tier": item.trust_tier,
                "observed_at": item.observed_at.isoformat(),
                "content_hash": item.content_hash,
            }
            for item in judgment.accepted
        ]
