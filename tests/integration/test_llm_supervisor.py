from pathlib import Path

from procureops.agents import LLMSupervisorWorkflow
from procureops.agents.single import default_policy
from procureops.domain.models import RunBudget
from procureops.harness.audit import InMemoryAuditSink
from procureops.harness.model_gateway import FakeModel, ModelGateway
from procureops.harness.tool_gateway import ToolGateway
from procureops.intake import IntakeService
from procureops.memory import MemoryService
from procureops.storage import ProcureOpsRepository
from procureops.tools import register_procurement_tools

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_llm_supervisor_calls_four_advisory_specialists_through_harness(
    repository: ProcureOpsRepository,
    run_context,
) -> None:
    outputs = {
        f"specialist_review_{phase}": {
            "decision": f"reviewed_{phase}",
            "facts": {"bounded": True},
        }
        for phase in ("intake", "catalog", "supplier", "policy")
    }
    client = FakeModel(outputs)
    audit = InMemoryAuditSink()
    tool_gateway = ToolGateway(audit=audit)
    register_procurement_tools(tool_gateway, repository)
    context = run_context.model_copy(
        update={"budget": RunBudget(max_model_calls=8, max_tool_calls=12)}
    )
    workflow = LLMSupervisorWorkflow(
        repository=repository,
        tool_gateway=tool_gateway,
        model_gateway=ModelGateway(client=client, audit=audit),
        policy=default_policy(PROJECT_ROOT),
        context=context,
        memory_service=MemoryService(repository.database),
    )

    result = workflow.start(
        context=context,
        intake=IntakeService().from_text(
            "DEMO-HYD-PUMP-001,液压泵,1,件,EX200-A",
            artifact_id="llm-supervisor.txt",
        ),
    )

    assert result.status.value == "awaiting_approval"
    assert [item.phase for item in workflow.trace.messages] == [
        "intake",
        "catalog",
        "supplier",
        "policy",
    ]
    assert len(client.calls) == 4
    assert all(item.decision.startswith("reviewed_") for item in workflow.trace.messages)
    assert sum(event.event_type == "model.succeeded" for event in audit.events()) == 4


def test_llm_specialist_failure_is_diagnosable_but_cannot_block_authoritative_flow(
    repository: ProcureOpsRepository,
    run_context,
) -> None:
    client = FakeModel({})
    audit = InMemoryAuditSink()
    tool_gateway = ToolGateway(audit=audit)
    register_procurement_tools(tool_gateway, repository)
    context = run_context.model_copy(
        update={"budget": RunBudget(max_model_calls=8, max_tool_calls=12)}
    )
    workflow = LLMSupervisorWorkflow(
        repository=repository,
        tool_gateway=tool_gateway,
        model_gateway=ModelGateway(client=client, audit=audit),
        policy=default_policy(PROJECT_ROOT),
        context=context,
    )

    result = workflow.start(
        context=context,
        intake=IntakeService().from_text(
            "DEMO-HYD-PUMP-001,液压泵,1,件,EX200-A",
            artifact_id="llm-supervisor-failure.txt",
        ),
    )

    assert result.status.value == "awaiting_approval"
    assert all(item.decision == "advisory_unavailable" for item in workflow.trace.messages)
    assert all(
        item.facts["error_class"] == "PermanentToolError"
        for item in workflow.trace.messages
    )
