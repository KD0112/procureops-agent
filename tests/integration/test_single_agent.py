import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from procureops.agents.single import SingleAgentWorkflow, default_policy
from procureops.domain.enums import TaskStatus
from procureops.domain.models import ApprovalGrant, RunContext
from procureops.harness.audit import InMemoryAuditSink
from procureops.harness.errors import ApprovalRequired
from procureops.harness.tool_gateway import ToolGateway
from procureops.intake import IntakeService
from procureops.memory import MemoryService
from procureops.rag import GovernedRetriever
from procureops.storage import ProcureOpsRepository
from procureops.tools import register_procurement_tools


def build_agent(repository: ProcureOpsRepository) -> SingleAgentWorkflow:
    gateway = ToolGateway(audit=InMemoryAuditSink())
    register_procurement_tools(gateway, repository)
    return SingleAgentWorkflow(
        repository=repository,
        tool_gateway=gateway,
        policy=default_policy(Path(__file__).resolve().parents[2]),
    )


def test_single_agent_happy_path_pauses_and_resumes_after_approval(
    repository: ProcureOpsRepository,
    run_context: RunContext,
) -> None:
    agent = build_agent(repository)
    intake = IntakeService().from_text(
        "液压泵 DEMO-HYD-PUMP-001 x2 台",
        artifact_id="request.txt",
    )

    pending = agent.start(context=run_context, intake=intake)

    assert pending.status == TaskStatus.AWAITING_APPROVAL
    assert pending.approval_requirement is not None
    assert pending.approval_requirement.required_roles == {"procurement_operator"}
    assert pending.cost_summary is not None
    assert pending.cost_summary.total_amount > 0
    evidence = repository.evidence_for_task(
        tenant_id=run_context.tenant_id,
        task_id=run_context.task_id,
    )
    assert {item["field_name"] for item in evidence} >= {
        "part_number",
        "matched_product_id",
        "unit_price",
        "available_quantity",
        "selected_supplier_id",
    }

    approval = agent.issue_approval(
        context=run_context,
        result=pending,
        approved_by="buyer-001",
        approved_by_roles=frozenset({"procurement_operator"}),
    )
    completed = agent.resume(context=run_context, approval=approval)

    assert completed.status == TaskStatus.COMPLETED
    assert completed.po_draft is not None
    repeated = agent.resume(context=run_context, approval=approval)
    assert repeated.po_draft["po_draft_id"] == completed.po_draft["po_draft_id"]


def test_single_agent_rejects_wrong_approver_role(
    repository: ProcureOpsRepository,
    run_context: RunContext,
) -> None:
    agent = build_agent(repository)
    pending = agent.start(
        context=run_context,
        intake=IntakeService().from_text(
            "滤芯 DEMO-FLT-KIT-001 x1 套",
            artifact_id="request.txt",
        ),
    )

    with pytest.raises(PermissionError, match="required approver roles"):
        agent.issue_approval(
            context=run_context,
            result=pending,
            approved_by="requester-001",
            approved_by_roles=frozenset({"requester"}),
        )


def test_single_agent_asks_for_missing_catalog_identity(
    repository: ProcureOpsRepository,
    run_context: RunContext,
) -> None:
    agent = build_agent(repository)
    intake = IntakeService().from_text(
        "UNKNOWN-001 | 不明配件 | 2 | 件",
        artifact_id="request.txt",
    )

    result = agent.start(context=run_context, intake=intake)

    assert result.status == TaskStatus.NEEDS_INPUT
    assert "补充" in result.questions[0]


def test_expired_approval_is_rejected_by_tool_gateway(
    repository: ProcureOpsRepository,
    run_context: RunContext,
) -> None:
    agent = build_agent(repository)
    pending = agent.start(
        context=run_context,
        intake=IntakeService().from_text(
            "滤芯 DEMO-FLT-KIT-001 x1 套",
            artifact_id="request.txt",
        ),
    )
    now = datetime.now(UTC)
    approval = ApprovalGrant.bind(
        approval_id="expired-approval",
        tenant_id=run_context.tenant_id,
        task_id=run_context.task_id,
        action="purchase_order_draft",
        subject=pending.approval_subject,
        approved_by="buyer-001",
        approved_by_roles=frozenset({"procurement_operator"}),
        approved_at=now - timedelta(minutes=2),
        expires_at=now - timedelta(minutes=1),
    )
    expired_context = run_context.model_copy(
        update={"deadline_at": datetime.now(UTC) + timedelta(minutes=5)}
    )

    with pytest.raises(ApprovalRequired, match="approval"):
        agent.resume(context=expired_context, approval=approval)


def test_single_agent_uses_acl_rag_citations_and_confirmed_memory(
    repository: ProcureOpsRepository,
    run_context: RunContext,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    memory = MemoryService(repository.database)
    candidate = memory.propose(
        tenant_id=run_context.tenant_id,
        user_id=run_context.actor_id,
        memory_key="preferred_delivery_window",
        value="工作日上午",
        confidence=0.95,
        proposed_by="single_agent_v1",
    )
    memory.confirm(
        tenant_id=run_context.tenant_id,
        user_id=run_context.actor_id,
        record_id=candidate.record_id,
        confirmed_by=run_context.actor_id,
    )
    gateway = ToolGateway(audit=InMemoryAuditSink())
    register_procurement_tools(gateway, repository)
    agent = SingleAgentWorkflow(
        repository=repository,
        tool_gateway=gateway,
        policy=default_policy(project_root),
        retriever=GovernedRetriever(
            knowledge_root=project_root / "knowledge",
            retrieval_config=(
                project_root
                / "data"
                / "tenant_packs"
                / "tenant_engineering_machinery"
                / "retrieval.json"
            ),
        ),
        memory_service=memory,
    )

    pending = agent.start(
        context=run_context,
        intake=IntakeService().from_text(
            "液压泵 DEMO-HYD-PUMP-001 x2 台",
            artifact_id="request.txt",
        ),
    )
    approval = agent.issue_approval(
        context=run_context,
        result=pending,
        approved_by=run_context.actor_id,
        approved_by_roles=pending.approval_requirement.required_roles,
    )
    completed = agent.resume(context=run_context, approval=approval)
    payload = json.loads(completed.po_draft["payload_json"])

    assert payload["rag_citations"]
    assert payload["confirmed_user_preferences"] == {
        "preferred_delivery_window": "工作日上午"
    }
    evidence_fields = {
        item["field_name"]
        for item in repository.evidence_for_task(
            tenant_id=run_context.tenant_id,
            task_id=run_context.task_id,
        )
    }
    assert "rag_context" in evidence_fields
    assert "memory.preferred_delivery_window" in evidence_fields
