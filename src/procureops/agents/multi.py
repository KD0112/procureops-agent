from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from procureops.agents.single import SingleAgentWorkflow, WorkflowResult
from procureops.domain.models import ApprovalGrant, RunContext
from procureops.domain.policy import ProcurementPolicy
from procureops.harness.tool_gateway import ToolGateway
from procureops.intake import IntakeResult
from procureops.memory import MemoryService
from procureops.rag import Retriever
from procureops.storage import ProcureOpsRepository


class SpecialistMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    specialist: str
    phase: str
    decision: str
    facts: dict[str, Any]


class SpecialistAgent(Protocol):
    name: str
    phase: str

    def review(self, payload: dict[str, Any]) -> SpecialistMessage: ...


class IntakeAgent:
    name = "intake_agent"
    phase = "intake"

    def review(self, payload: dict[str, Any]) -> SpecialistMessage:
        decision = "needs_input" if payload["questions"] else "structured"
        return SpecialistMessage(
            specialist=self.name,
            phase=self.phase,
            decision=decision,
            facts={"line_count": payload["line_count"]},
        )


class CatalogMatcherAgent:
    name = "catalog_matcher"
    phase = "catalog"

    def review(self, payload: dict[str, Any]) -> SpecialistMessage:
        candidates = payload["candidates"]
        decision = "candidate_found" if candidates else "missing_candidate"
        return SpecialistMessage(
            specialist=self.name,
            phase=self.phase,
            decision=decision,
            facts={
                "line_number": payload["line_number"],
                "candidate_count": len(candidates),
                "top_score": candidates[0]["score"] if candidates else None,
            },
        )


class SupplierResearchAgent:
    name = "supplier_research_agent"
    phase = "supplier"

    def review(self, payload: dict[str, Any]) -> SpecialistMessage:
        options = payload["options"]
        approved = [item for item in options if item["approved"]]
        return SpecialistMessage(
            specialist=self.name,
            phase=self.phase,
            decision="approved_option_found" if approved else "no_approved_option",
            facts={
                "line_number": payload["line_number"],
                "option_count": len(options),
                "approved_option_count": len(approved),
            },
        )


class PolicyRiskAgent:
    name = "policy_risk_agent"
    phase = "policy"

    def review(self, payload: dict[str, Any]) -> SpecialistMessage:
        requirement = payload["requirement"]
        return SpecialistMessage(
            specialist=self.name,
            phase=self.phase,
            decision="approval_required",
            facts={
                "required_roles": requirement["required_roles"],
                "total_amount": requirement["total_amount"],
                "evidence_count": payload["evidence_count"],
            },
        )


@dataclass(slots=True)
class SupervisorTrace:
    messages: list[SpecialistMessage] = field(default_factory=list)
    unknown_phases: list[str] = field(default_factory=list)


class SupervisorWorkflow:
    """Supervisor plus bounded specialists using the same authoritative workflow."""

    def __init__(
        self,
        *,
        repository: ProcureOpsRepository,
        tool_gateway: ToolGateway,
        policy: ProcurementPolicy,
        retriever: Retriever | None = None,
        memory_service: MemoryService | None = None,
    ) -> None:
        self.trace = SupervisorTrace()
        self.specialists: dict[str, SpecialistAgent] = {
            agent.phase: agent
            for agent in (
                IntakeAgent(),
                CatalogMatcherAgent(),
                SupplierResearchAgent(),
                PolicyRiskAgent(),
            )
        }
        self.workflow = SingleAgentWorkflow(
            repository=repository,
            tool_gateway=tool_gateway,
            policy=policy,
            phase_observer=self._route,
            retriever=retriever,
            memory_service=memory_service,
        )

    def _route(self, phase: str, payload: dict[str, Any]) -> None:
        specialist = self.specialists.get(phase)
        if specialist is None:
            self.trace.unknown_phases.append(phase)
            return
        self.trace.messages.append(specialist.review(payload))

    def start(self, *, context: RunContext, intake: IntakeResult) -> WorkflowResult:
        return self.workflow.start(context=context, intake=intake)

    def issue_approval(self, **kwargs: Any) -> ApprovalGrant:
        return self.workflow.issue_approval(**kwargs)

    def resume(self, **kwargs: Any) -> WorkflowResult:
        return self.workflow.resume(**kwargs)
