from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from procureops.codeops.diagnosis import diagnose_ci_output
from procureops.codeops.policy import RepoPolicy
from procureops.codeops.workspace import RepoWorkspace, WorkspaceManager
from procureops.domain.enums import ActionKind, RiskLevel
from procureops.harness.budget import RunBudgetLedger
from procureops.harness.errors import PermanentToolError
from procureops.harness.tool_gateway import ToolDefinition, ToolExecutionResult, ToolGateway


class RepoInspector:
    def __init__(self, *, workspace: RepoWorkspace, policy: RepoPolicy):
        self.workspace = workspace
        self.policy = policy

    def tree(self, *, max_entries: int = 200) -> list[str]:
        result: list[str] = []
        for path in sorted(self.workspace.path.rglob("*")):
            if len(result) >= min(max_entries, self.policy.max_search_results):
                break
            if path.is_file():
                relative = path.relative_to(self.workspace.path).as_posix()
                try:
                    self.policy.resolve(self.workspace.path, relative)
                except PermissionError:
                    continue
                result.append(relative)
        return result

    def search(self, *, query: str, max_results: int = 50) -> list[dict[str, Any]]:
        if not query.strip():
            raise ValueError("query is required")
        results: list[dict[str, Any]] = []
        needle = query.casefold()
        for relative in self.tree(max_entries=self.policy.max_search_results):
            if len(results) >= min(max_results, self.policy.max_search_results):
                break
            try:
                content = self.policy.read_text(self.workspace.path, relative)
            except (UnicodeDecodeError, OSError, PermissionError):
                continue
            for line_number, line in enumerate(content.splitlines(), 1):
                if needle in line.casefold():
                    results.append({"path": relative, "line": line_number, "text": line[:500]})
        return results


def register_repo_tools(
    gateway: ToolGateway,
    *,
    workspace: RepoWorkspace,
    manager: WorkspaceManager,
    policy: RepoPolicy,
) -> None:
    inspector = RepoInspector(workspace=workspace, policy=policy)

    gateway.register(
        ToolDefinition(
            name="repo_tree",
            handler=lambda arguments: inspector.tree(
                max_entries=int(arguments.get("max_entries", 200))
            ),
            risk_level=RiskLevel.R0_READ_ONLY,
            action_kind=ActionKind.READ,
        )
    )
    gateway.register(
        ToolDefinition(
            name="repo_read",
            handler=lambda arguments: {
                "path": str(arguments["path"]),
                "content": policy.read_text(workspace.path, str(arguments["path"])),
            },
            risk_level=RiskLevel.R0_READ_ONLY,
            action_kind=ActionKind.READ,
        )
    )
    gateway.register(
        ToolDefinition(
            name="repo_search",
            handler=lambda arguments: inspector.search(
                query=str(arguments["query"]),
                max_results=int(arguments.get("max_results", 50)),
            ),
            risk_level=RiskLevel.R0_READ_ONLY,
            action_kind=ActionKind.READ,
        )
    )
    gateway.register(
        ToolDefinition(
            name="repo_diff",
            handler=lambda _arguments: {"diff": manager.diff(workspace)},
            risk_level=RiskLevel.R0_READ_ONLY,
            action_kind=ActionKind.READ,
        )
    )
    gateway.register(
        ToolDefinition(
            name="repo_diagnose_ci",
            handler=lambda arguments: diagnose_ci_output(
                str(arguments.get("ci_output", ""))
            ),
            risk_level=RiskLevel.R0_READ_ONLY,
            action_kind=ActionKind.READ,
        )
    )
    gateway.register(
        ToolDefinition(
            name="repo_write_file",
            handler=lambda arguments: {
                "path": str(arguments["path"]),
                "sha256": policy.write_text(
                    workspace.path,
                    str(arguments["path"]),
                    str(arguments["content"]),
                    expected_sha256=(
                        str(arguments["expected_sha256"])
                        if arguments.get("expected_sha256") is not None
                        else None
                    ),
                ),
            },
            risk_level=RiskLevel.R1_INTERNAL_DRAFT,
            action_kind=ActionKind.WRITE_DRAFT,
        )
    )
    gateway.register(
        ToolDefinition(
            name="repo_run_tests",
            handler=lambda arguments: _run_command(
                policy=policy,
                workspace=workspace,
                raw_command=str(arguments.get("command", "python -m pytest -q")),
            ),
            risk_level=RiskLevel.R1_INTERNAL_DRAFT,
            action_kind=ActionKind.WRITE_DRAFT,
        )
    )
    gateway.register(
        ToolDefinition(
            name="repo_commit",
            handler=lambda _arguments: _commit_disabled(workspace.path),
            risk_level=RiskLevel.R2_EXTERNAL_REVERSIBLE,
            action_kind=ActionKind.EXTERNAL_WRITE,
            max_attempts=1,
        )
    )


class RepoToolSession:
    def __init__(
        self,
        *,
        gateway: ToolGateway,
        context,
        ledger: RunBudgetLedger,
    ) -> None:
        self.gateway = gateway
        self.context = context
        self.ledger = ledger

    def execute(
        self,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        approval=None,
        idempotency_key: str | None = None,
    ) -> ToolExecutionResult:
        return self.gateway.execute(
            context=self.context,
            ledger=self.ledger,
            tool_name=tool_name,
            arguments=dict(arguments or {}),
            approval=approval,
            idempotency_key=idempotency_key,
        )


def _run_command(
    *, policy: RepoPolicy, workspace: RepoWorkspace, raw_command: str
) -> dict[str, Any]:
    command = policy.command(raw_command)
    env = {
        key: value
        for key, value in os.environ.items()
        if key.casefold()
        in {
            "path",
            "systemroot",
            "temp",
            "tmp",
            "pathext",
            "userprofile",
            "homedrive",
            "homepath",
            "appdata",
            "localappdata",
        }
    }
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    try:
        completed = subprocess.run(
            command,
            cwd=workspace.path,
            capture_output=True,
            timeout=policy.command_timeout_seconds,
            check=False,
            shell=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            "command": command,
            "returncode": 124,
            "stdout": "",
            "stderr": "test command timed out",
            "timed_out": True,
        }
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": _decode_output(completed.stdout)[-12_000:],
        "stderr": _decode_output(completed.stderr)[-12_000:],
        "timed_out": False,
    }


def _decode_output(value: bytes | None) -> str:
    return (value or b"").decode("utf-8", errors="replace")


def _commit_disabled(root: Path) -> dict[str, Any]:
    del root
    raise PermanentToolError(
        "commit is disabled in the copy workspace; approval and a git-backed adapter are required"
    )
