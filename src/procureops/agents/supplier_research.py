from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

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


class BoundedSupplierResearchAgent:
    """A plan-act-observe loop that can only call the logistics read tool."""

    allowed_actions = frozenset({"logistics_quote", "finish"})

    def __init__(
        self,
        *,
        model_gateway: ModelGateway,
        tool_gateway: ToolGateway,
        max_steps: int = 3,
    ) -> None:
        if not 1 <= max_steps <= 5:
            raise ValueError("supplier research max_steps must be between 1 and 5")
        self.model_gateway = model_gateway
        self.tool_gateway = tool_gateway
        self.max_steps = max_steps
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
                            "allowed_actions": sorted(self.allowed_actions),
                            "instruction": (
                                "Choose one allowed action. Use logistics_quote before finish "
                                "unless current logistics observations are present. Never request "
                                "a write tool. Return action, supplier_id, and rationale as JSON."
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
        )
