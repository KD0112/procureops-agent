from datetime import UTC, datetime, timedelta
from pathlib import Path

from procureops.agents.single import SingleAgentWorkflow, policy_for_tenant
from procureops.domain.enums import TaskStatus
from procureops.domain.models import RunBudget, RunContext
from procureops.harness.audit import InMemoryAuditSink
from procureops.harness.tool_gateway import ToolGateway
from procureops.intake import IntakeService
from procureops.rag import HashingEmbeddingProvider, SQLiteKnowledgeIndex
from procureops.storage import ProcureOpsRepository
from procureops.tools import register_procurement_tools

PROJECT_ROOT = Path(__file__).resolve().parents[2]
IT_TENANT = "tenant_enterprise_it"


def _context(task_id: str) -> RunContext:
    return RunContext(
        run_id=f"run-{task_id}",
        task_id=task_id,
        tenant_id=IT_TENANT,
        actor_id="it-buyer-001",
        actor_roles=frozenset({"procurement_operator"}),
        workflow_version="1.1.0",
        prompt_version="1.0.0",
        model_policy_version="1.0.0",
        rule_set_version="1.0.0",
        tenant_pack_version="1.0.0",
        deadline_at=datetime.now(UTC) + timedelta(minutes=5),
        budget=RunBudget(max_tool_calls=12),
        correlation_id=f"corr-{task_id}",
    )


def test_it_tenant_reuses_same_workflow_and_remains_isolated(
    repository: ProcureOpsRepository,
    tmp_path: Path,
) -> None:
    gateway = ToolGateway(audit=InMemoryAuditSink())
    register_procurement_tools(gateway, repository)
    retriever = SQLiteKnowledgeIndex(
        path=tmp_path / "knowledge.sqlite3",
        embedding_provider=HashingEmbeddingProvider(dimensions=256),
    )
    from procureops.rag.governance import scan_knowledge_base

    retriever.rebuild(scan_knowledge_base(PROJECT_ROOT / "knowledge"))
    workflow = SingleAgentWorkflow(
        repository=repository,
        tool_gateway=gateway,
        policy=policy_for_tenant(PROJECT_ROOT, IT_TENANT),
        retriever=retriever,
    )
    context = _context("it-task-001")

    pending = workflow.start(
        context=context,
        intake=IntakeService().from_text(
            "IT-LAP-DEV-14 | 研发笔记本 | 2 | 台",
            artifact_id="it-request.txt",
        ),
    )

    assert pending.status == TaskStatus.AWAITING_APPROVAL
    assert pending.cost_summary is not None
    assert pending.cost_summary.currency == "CNY"
    assert repository.search_products(
        tenant_id="tenant_engineering_machinery",
        query="研发笔记本",
        part_number="IT-LAP-DEV-14",
    ) == ()
    citations = {
        item["source_id"]
        for item in repository.evidence_for_task(tenant_id=IT_TENANT, task_id=context.task_id)
        if item["field_name"] == "rag_context"
    }
    assert citations
    assert all(item.startswith("IT-") for item in citations)

    approval = workflow.issue_approval(
        context=context,
        result=pending,
        approved_by="it-approver-001",
        approved_by_roles=pending.approval_requirement.required_roles,
    )
    completed = workflow.resume(context=context, approval=approval)

    assert completed.status == TaskStatus.COMPLETED
    assert completed.po_draft is not None
    assert completed.po_draft["tenant_id"] == IT_TENANT
