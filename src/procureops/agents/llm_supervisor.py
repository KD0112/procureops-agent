from __future__ import annotations

from typing import Any

from procureops.agents.multi import SpecialistMessage, SupervisorTrace
from procureops.agents.single import SingleAgentWorkflow, WorkflowResult
from procureops.agents.supplier_research import BoundedSupplierResearchAgent
from procureops.domain.models import ApprovalGrant, RunContext
from procureops.domain.policy import ProcurementPolicy
from procureops.harness.budget import RunBudgetLedger
from procureops.harness.model_gateway import ModelGateway, ModelRequest
from procureops.harness.tool_gateway import ToolGateway
from procureops.intake import IntakeResult
from procureops.memory import MemoryService
from procureops.rag import Retriever
from procureops.storage import ProcureOpsRepository

KNOWN_PHASES = frozenset({"intake", "catalog", "supplier", "policy"})


class LLMSupervisorWorkflow:
    """LLM specialists provide advisory reviews; deterministic code remains authoritative."""

    def __init__(
        self,
        *,
        repository: ProcureOpsRepository,
        tool_gateway: ToolGateway,
        model_gateway: ModelGateway,
        policy: ProcurementPolicy,
        context: RunContext,
        retriever: Retriever | None = None,
        memory_service: MemoryService | None = None,
    ) -> None:
        self.trace = SupervisorTrace()
        self.context = context
        self.model_gateway = model_gateway
        self.ledger = RunBudgetLedger(context)
        supplier_researcher = BoundedSupplierResearchAgent(
            model_gateway=model_gateway,
            tool_gateway=tool_gateway,
        )
        self.workflow = SingleAgentWorkflow(
            repository=repository,
            tool_gateway=tool_gateway,
            policy=policy,
            phase_observer=self._review_phase,
            retriever=retriever,
            memory_service=memory_service,
            supplier_researcher=supplier_researcher,
            run_ledger=self.ledger,
        )

    def _review_phase(self, phase: str, payload: dict[str, Any]) -> None:
        if phase not in KNOWN_PHASES:
            self.trace.unknown_phases.append(phase)
            return
        purpose = f"specialist_review_{phase}"
        try:
            response = self.model_gateway.invoke(
                context=self.context,
                ledger=self.ledger,
                request=ModelRequest(
                    purpose=purpose,
                    payload={
                        "phase": phase,
                        "authoritative_facts": payload,
                        "instruction": (
                            "Review only the supplied facts. Return JSON with decision and "
                            "facts. Never invent price, inventory, supplier status, policy, "
                            "or approval. This review is advisory and cannot execute tools."
                        ),
                    },
                    response_schema="SpecialistReviewV1",
                ),
            )
            decision = response.output.get("decision")
            facts = response.output.get("facts")
            if not isinstance(decision, str) or not isinstance(facts, dict):
                raise ValueError("specialist response violates SpecialistReviewV1")
            message = SpecialistMessage(
                specialist=f"llm_{phase}_specialist",
                phase=phase,
                decision=decision,
                facts=facts,
            )
        except Exception as exc:
            message = SpecialistMessage(
                specialist=f"llm_{phase}_specialist",
                phase=phase,
                decision="advisory_unavailable",
                facts={"error_class": type(exc).__name__},
            )
        self.trace.messages.append(message)

    def start(self, *, context: RunContext, intake: IntakeResult) -> WorkflowResult:
        if context != self.context:
            raise ValueError("LLM supervisor context cannot change during a run")
        return self.workflow.start(context=context, intake=intake)

    def issue_approval(self, **kwargs: Any) -> ApprovalGrant:
        return self.workflow.issue_approval(**kwargs)

    def resume(self, **kwargs: Any) -> WorkflowResult:
        return self.workflow.resume(**kwargs)
