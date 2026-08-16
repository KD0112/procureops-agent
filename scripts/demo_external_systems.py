"""Run one IT task through the configured ERP/supplier/logistics HTTP adapters."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from procureops.harness.audit import JsonlAuditSink  # noqa: E402
from procureops.intake import IntakeService  # noqa: E402
from procureops.runtime import ProcureOpsRuntime  # noqa: E402


def main() -> None:
    runtime = ProcureOpsRuntime.create(
        project_root=PROJECT_ROOT,
        database_path=PROJECT_ROOT / "var" / "external_demo.sqlite3",
        var_root=PROJECT_ROOT / "var" / "external_demo",
    )
    if runtime.integrations.status().profile not in {"http_sandbox", "http_enterprise"}:
        raise SystemExit(
            "Set PROCUREOPS_INTEGRATION_PROFILE=http_sandbox or http_enterprise first."
        )
    task_id = f"external-demo-{uuid4().hex[:10]}"
    context = runtime.context(
        tenant_id="tenant_enterprise_it",
        task_id=task_id,
        actor_id="external-demo-buyer",
        actor_roles=frozenset({"procurement_operator"}),
        run_id=f"run-{task_id}",
        correlation_id=f"corr-{task_id}",
    )
    agent = runtime.agent(
        audit=JsonlAuditSink(runtime.audit_path),
        context=context,
    )
    pending = agent.start(
        context=context,
        intake=IntakeService().from_text(
            "IT-LAP-DEV-14 | 研发笔记本 | 1 | 台",
            artifact_id="external-system-demo.txt",
        ),
    )
    if pending.approval_requirement is None:
        raise SystemExit("expected the task to pause for approval")
    approval = agent.issue_approval(
        context=context,
        result=pending,
        approved_by="external-demo-approver",
        approved_by_roles=pending.approval_requirement.required_roles,
    )
    completed = agent.resume(context=context, approval=approval)
    payload = json.loads(completed.po_draft["payload_json"])
    print(
        json.dumps(
            {
                "task_id": task_id,
                "status": completed.status,
                "integration_profile": runtime.integrations.status().profile,
                "external_receipt": payload.get("external_receipt"),
                "evidence_count": len(
                    runtime.repository.evidence_for_task(
                        tenant_id=context.tenant_id,
                        task_id=task_id,
                    )
                ),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
