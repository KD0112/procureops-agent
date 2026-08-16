from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from procureops.codeops.models import CodeTaskRequest, RepoPilotResult, RepoPlan
from procureops.codeops.policy import RepoPolicy
from procureops.codeops.tools import RepoToolSession, register_repo_tools
from procureops.codeops.workspace import WorkspaceManager
from procureops.harness.audit import AuditSink
from procureops.harness.budget import RunBudgetLedger
from procureops.harness.errors import ApprovalRequired, PermanentToolError
from procureops.harness.tool_gateway import ToolGateway


@dataclass(slots=True)
class RepoPilotSkill:
    """A bounded coding workflow; an LLM may produce RepoPlan, but cannot bypass it."""

    source_root: Path
    var_root: Path
    audit: AuditSink
    context: Any
    policy: RepoPolicy | None = None

    async def run(self, *, task: CodeTaskRequest, plan: RepoPlan) -> RepoPilotResult:
        policy = self.policy or RepoPolicy()
        manager = WorkspaceManager(
            source_root=self.source_root,
            workspace_root=self.var_root / "codeops" / "workspaces",
            policy=policy,
        )
        workspace = manager.create(task.task_id)
        gateway = ToolGateway(audit=self.audit)
        register_repo_tools(
            gateway,
            workspace=workspace,
            manager=manager,
            policy=policy,
        )
        session = RepoToolSession(
            gateway=gateway,
            context=self.context,
            ledger=RunBudgetLedger(self.context),
        )
        inspected: list[str] = []
        changed: list[str] = []
        current_hashes: dict[str, str | None] = {}
        diagnosis: dict[str, Any] = {}
        workflow: list[str] = []
        try:
            if task.ci_output.strip():
                diagnosis_result = await asyncio.to_thread(
                    session.execute,
                    "repo_diagnose_ci",
                    {"ci_output": task.ci_output},
                    idempotency_key=f"diagnose:{workspace.workspace_id}",
                )
                diagnosis = dict(diagnosis_result.output)
                workflow.append("diagnose_ci")
            workflow.append("plan_validated")
            for relative in tuple(dict.fromkeys((*task.requested_files, *plan.files_to_read))):
                await asyncio.to_thread(session.execute, "repo_read", {"path": relative})
                inspected.append(relative)
                current_hashes[relative] = hashlib.sha256(
                    policy.resolve(workspace.path, relative).read_bytes()
                ).hexdigest()
            for relative, content in plan.proposed_writes.items():
                if relative not in current_hashes:
                    target = policy.resolve(workspace.path, relative)
                    if target.exists():
                        await asyncio.to_thread(
                            session.execute, "repo_read", {"path": relative}
                        )
                        inspected.append(relative)
                        current_hashes[relative] = hashlib.sha256(
                            target.read_bytes()
                        ).hexdigest()
                    else:
                        # New files have no baseline hash. The path is still
                        # checked by RepoPolicy and the write stays in the
                        # disposable workspace.
                        current_hashes[relative] = None
                arguments = {"path": relative, "content": content}
                expected_sha256 = plan.expected_sha256.get(relative, current_hashes[relative])
                if expected_sha256 is not None:
                    arguments["expected_sha256"] = expected_sha256
                await asyncio.to_thread(
                    session.execute,
                    "repo_write_file",
                    arguments,
                    idempotency_key=f"write:{workspace.workspace_id}:{relative}",
                )
                changed.append(relative)
            if changed:
                workflow.append("workspace_patch")
            test_command = plan.test_command or task.test_command
            test_key = hashlib.sha256(test_command.encode()).hexdigest()[:16]
            test_result = await asyncio.to_thread(
                session.execute,
                "repo_run_tests",
                {"command": test_command},
                idempotency_key=f"test:{workspace.workspace_id}:{test_key}",
            )
            normalized_test = dict(test_result.output)
            workflow.append("test_gate")
            diff_result = await asyncio.to_thread(session.execute, "repo_diff")
            diff = str(diff_result.output.get("diff", ""))
            diff_sha256 = hashlib.sha256(diff.encode("utf-8")).hexdigest()
            workflow.append("diff_review")
            status = "passed" if normalized_test.get("returncode") == 0 else "failed"
            blocked_reason = None
            commit_requested = task.commit_requested or plan.commit_requested
            if commit_requested:
                if status != "passed":
                    blocked_reason = "commit is blocked until the test gate passes"
                else:
                    try:
                        await asyncio.to_thread(session.execute, "repo_commit", {})
                        status = "needs_approval"
                    except ApprovalRequired:
                        status = "needs_approval"
                        blocked_reason = (
                            "human approval is required for this exact workspace and diff"
                        )
                    workflow.append("human_approval_gate")
            return RepoPilotResult(
                task_id=task.task_id,
                status=status,
                workspace_id=workspace.workspace_id,
                workspace_path=str(workspace.path),
                description=task.description,
                diagnosis=diagnosis,
                workflow=tuple(workflow),
                files_inspected=tuple(inspected),
                files_changed=tuple(changed),
                diff=diff,
                diff_sha256=diff_sha256,
                test_result=normalized_test,
                blocked_reason=blocked_reason,
                audit_event_count=_audit_count(self.audit),
            )
        except (PermissionError, FileNotFoundError, PermanentToolError) as exc:
            return RepoPilotResult(
                task_id=task.task_id,
                status="blocked",
                workspace_id=workspace.workspace_id,
                workspace_path=str(workspace.path),
                description=task.description,
                diagnosis=diagnosis,
                workflow=tuple(workflow),
                files_inspected=tuple(inspected),
                files_changed=tuple(changed),
                blocked_reason=str(exc),
                audit_event_count=_audit_count(self.audit),
            )


def _audit_count(audit: AuditSink) -> int:
    events = getattr(audit, "events", None)
    if not callable(events):
        return 0
    return len(events())
