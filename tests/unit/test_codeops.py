from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest

from procureops.codeops import (
    CodeTaskRequest,
    RepoPilotSkill,
    RepoPlan,
    RepoPolicy,
    WorkspaceManager,
    diagnose_ci_output,
)
from procureops.codeops.tools import register_repo_tools
from procureops.harness.audit import InMemoryAuditSink
from procureops.harness.budget import RunBudgetLedger
from procureops.harness.errors import ApprovalRequired
from procureops.harness.tool_gateway import ToolGateway


def test_workspace_write_isolated_and_diff_is_recoverable(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "hello.py").write_text("print('before')\n", encoding="utf-8")
    manager = WorkspaceManager(source_root=source, workspace_root=tmp_path / "workspaces")
    workspace = manager.create("task-001")

    policy = RepoPolicy()
    policy.write_text(
        workspace.path,
        "hello.py",
        "print('after')\n",
        expected_sha256=hashlib.sha256(
            (workspace.path / "hello.py").read_bytes()
        ).hexdigest(),
    )

    assert (source / "hello.py").read_text(encoding="utf-8") == "print('before')\n"
    assert "print('after')" in manager.diff(workspace)
    manager.release(workspace)
    assert not workspace.path.exists()


def test_policy_blocks_sensitive_paths_and_shell_chaining() -> None:
    policy = RepoPolicy()
    with pytest.raises(PermissionError):
        policy.resolve(Path("."), ".env")
    with pytest.raises(PermissionError):
        policy.command("python -m pytest -q && whoami")


def test_ci_diagnosis_is_read_only_and_redacts_secrets() -> None:
    result = diagnose_ci_output(
        "FAILED tests/test_hello.py::test_value - AssertionError\n"
        "api_key=super-secret-value"
    )

    assert result["status"] == "failed"
    assert result["failure_kind"] == "test_failure"
    assert result["failed_tests"] == ["tests/test_hello.py::test_value"]
    assert all("super-secret-value" not in item for item in result["evidence"])


def test_repo_pilot_runs_ci_diagnosis_before_patch_and_diff(tmp_path: Path, run_context) -> None:
    source = tmp_path / "source"
    (source / "tests").mkdir(parents=True)
    (source / "hello.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    (source / "tests" / "test_hello.py").write_text(
        "from hello import value\n\n\ndef test_value():\n    assert value() == 2\n",
        encoding="utf-8",
    )
    result = asyncio.run(
        RepoPilotSkill(
            source_root=source,
            var_root=tmp_path / "var",
            audit=InMemoryAuditSink(),
            context=run_context,
        ).run(
            task=CodeTaskRequest(
                task_id="task-ci-repair",
                description="repair the failing CI assertion",
                requested_files=("hello.py",),
                ci_output="FAILED tests/test_hello.py::test_value - AssertionError",
                commit_requested=True,
            ),
            plan=RepoPlan(
                rationale="apply the smallest safe patch",
                files_to_read=("hello.py",),
                proposed_writes={"hello.py": "def value():\n    return 2\n"},
                test_command="python -m pytest -q",
                commit_requested=True,
            ),
        )
    )

    assert result.status == "needs_approval"
    assert result.diagnosis["failure_kind"] == "test_failure"
    assert result.workflow == (
        "diagnose_ci",
        "plan_validated",
        "workspace_patch",
        "test_gate",
        "diff_review",
        "human_approval_gate",
    )
    assert result.diff_sha256
    assert "return 2" in result.diff
    assert "return 1" in (source / "hello.py").read_text(encoding="utf-8")


def test_workspace_excludes_environment_and_dependency_artifacts(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "work" / "node_modules").mkdir(parents=True)
    source.joinpath(".env").write_text("SECRET=do-not-copy", encoding="utf-8")
    source.joinpath("work", "node_modules", "large.js").write_text(
        "artifact", encoding="utf-8"
    )
    source.joinpath("README.md").write_text("safe", encoding="utf-8")

    workspace = WorkspaceManager(
        source_root=source,
        workspace_root=tmp_path / "workspaces",
    ).create("task-safe-copy")

    assert not (workspace.path / ".env").exists()
    assert not (workspace.path / "work").exists()
    assert (workspace.path / "README.md").exists()


def test_repo_commit_requires_approval_before_handler(run_context, tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "hello.py").write_text("print('ok')\n", encoding="utf-8")
    manager = WorkspaceManager(source_root=source, workspace_root=tmp_path / "workspaces")
    workspace = manager.create("task-commit")
    audit = InMemoryAuditSink()
    gateway = ToolGateway(audit=audit)
    register_repo_tools(
        gateway,
        workspace=workspace,
        manager=manager,
        policy=RepoPolicy(),
    )

    with pytest.raises(ApprovalRequired):
        gateway.execute(
            context=run_context,
            ledger=RunBudgetLedger(run_context),
            tool_name="repo_commit",
            arguments={},
        )


def test_repo_pilot_writes_only_workspace_and_runs_tests(tmp_path: Path, run_context) -> None:
    source = tmp_path / "source"
    (source / "tests").mkdir(parents=True)
    (source / "hello.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    (source / "tests" / "test_hello.py").write_text(
        "from hello import value\n\n\ndef test_value():\n    assert value() == 2\n",
        encoding="utf-8",
    )
    audit = InMemoryAuditSink()
    skill = RepoPilotSkill(
        source_root=source,
        var_root=tmp_path / "var",
        audit=audit,
        context=run_context,
    )
    result = asyncio.run(
        skill.run(
            task=CodeTaskRequest(
                task_id="task-codeops",
                description="fix the return value",
                requested_files=("hello.py",),
            ),
            plan=RepoPlan(
                rationale="update the deterministic fixture",
                files_to_read=("hello.py",),
                proposed_writes={"hello.py": "def value():\n    return 2\n"},
            ),
        )
    )
    assert result.status == "passed"
    assert result.files_changed == ("hello.py",)
    assert "return 2" in result.diff
    assert "return 1" in (source / "hello.py").read_text(encoding="utf-8")
