"""Run a no-LLM local happy path with a simulated human approval."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from procureops.agents.single import SingleAgentWorkflow, default_policy  # noqa: E402
from procureops.demo import seed_demo_database  # noqa: E402
from procureops.domain.models import RunContext  # noqa: E402
from procureops.harness.audit import JsonlAuditSink  # noqa: E402
from procureops.harness.tool_gateway import ToolGateway  # noqa: E402
from procureops.intake import IntakeService  # noqa: E402
from procureops.memory import MemoryService  # noqa: E402
from procureops.rag import HashingEmbeddingProvider, SQLiteKnowledgeIndex  # noqa: E402
from procureops.rag.governance import scan_knowledge_base  # noqa: E402
from procureops.storage import SQLiteDatabase  # noqa: E402
from procureops.tools import register_procurement_tools  # noqa: E402


def main() -> None:
    demo_id = uuid4().hex[:8]
    database = SQLiteDatabase(PROJECT_ROOT / "var" / "procureops.sqlite3")
    repository = seed_demo_database(database, project_root=PROJECT_ROOT)
    gateway = ToolGateway(
        audit=JsonlAuditSink(PROJECT_ROOT / "var" / "audit.jsonl")
    )
    register_procurement_tools(gateway, repository)
    context = RunContext(
        run_id=f"demo-run-{demo_id}",
        task_id=f"demo-task-{demo_id}",
        tenant_id="tenant_engineering_machinery",
        actor_id="demo-buyer",
        actor_roles=frozenset({"procurement_operator"}),
        workflow_version="1.0.0",
        prompt_version="1.0.0",
        model_policy_version="1.0.0",
        rule_set_version="1.0.0",
        tenant_pack_version="1.0.0",
        deadline_at=datetime.now(UTC) + timedelta(minutes=5),
        correlation_id=f"demo-corr-{demo_id}",
    )
    rag_index = SQLiteKnowledgeIndex(
        path=PROJECT_ROOT / "var" / "rag" / "engineering_machinery.sqlite3",
        embedding_provider=HashingEmbeddingProvider(dimensions=256),
    )
    documents = scan_knowledge_base(PROJECT_ROOT / "knowledge")
    if not rag_index.is_current(documents):
        rag_index.rebuild(documents)
    workflow = SingleAgentWorkflow(
        repository=repository,
        tool_gateway=gateway,
        policy=default_policy(PROJECT_ROOT),
        retriever=rag_index,
        memory_service=MemoryService(database),
    )
    intake = IntakeService().from_text(
        "DEMO-HYD-PUMP-001 | 液压泵 | 2 | 台",
        artifact_id="demo-request.txt",
    )
    pending = workflow.start(context=context, intake=intake)
    print(
        json.dumps(
            {
                "task_id": pending.task_id,
                "status": pending.status,
                "total_amount": str(pending.cost_summary.total_amount),
                "required_roles": sorted(pending.approval_requirement.required_roles),
            },
            ensure_ascii=False,
        )
    )
    approval = workflow.issue_approval(
        context=context,
        result=pending,
        approved_by="demo-human-approver",
        approved_by_roles=pending.approval_requirement.required_roles,
    )
    completed = workflow.resume(context=context, approval=approval)
    print(
        json.dumps(
            {
                "task_id": completed.task_id,
                "status": completed.status,
                "po_draft_id": completed.po_draft["po_draft_id"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
