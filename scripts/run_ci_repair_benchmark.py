"""Run the deterministic CI-diagnosis -> repair -> test -> diff -> approval demo."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from procureops.codeops import (  # noqa: E402
    CodeTaskRequest,
    RepoPilotSkill,
    RepoPlan,
    diagnose_ci_output,
)
from procureops.domain.models import RunBudget, RunContext  # noqa: E402
from procureops.harness.audit import InMemoryAuditSink  # noqa: E402

CASES = (
    ("test_failure", "FAILED tests/test_app.py::test_total - AssertionError"),
    ("syntax_error", "File app.py, line 4\nSyntaxError: invalid syntax"),
    ("dependency_error", "ModuleNotFoundError: No module named 'pandas'"),
    ("lint_failure", "ruff check app.py\napp.py:4:1: F401 unused import"),
    ("timeout", "pytest timed out after 30 seconds"),
)


def _context(task_id: str) -> RunContext:
    return RunContext(
        run_id=f"ci-repair-{task_id}",
        task_id=task_id,
        tenant_id="tenant_engineering_machinery",
        actor_id="benchmark",
        actor_roles=frozenset({"procurement_operator"}),
        workflow_version="code-agent-ci-repair-v1",
        prompt_version="offline",
        model_policy_version="offline",
        rule_set_version="offline",
        tenant_pack_version="offline",
        deadline_at=datetime.now(UTC) + timedelta(minutes=2),
        budget=RunBudget(
            max_model_calls=0,
            max_tool_calls=12,
            max_tokens=0,
            max_cost_usd=0,
        ),
        correlation_id=f"ci-correlation-{task_id}",
    )


def _fixture(root: Path) -> None:
    (root / "tests").mkdir(parents=True)
    (root / "hello.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    (root / "tests" / "test_hello.py").write_text(
        "from hello import value\n\n\ndef test_value():\n    assert value() == 2\n",
        encoding="utf-8",
    )


async def _repair(root: Path) -> dict[str, Any]:
    before = (root / "hello.py").read_bytes()
    result = await RepoPilotSkill(
        source_root=root,
        var_root=root.parent / "var",
        audit=InMemoryAuditSink(),
        context=_context("repair-demo"),
    ).run(
        task=CodeTaskRequest(
            task_id="repair-demo",
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
    return {
        "status": result.status,
        "diagnosis": result.diagnosis,
        "workflow": list(result.workflow),
        "test_returncode": result.test_result.get("returncode"),
        "diff_sha256": result.diff_sha256,
        "source_unchanged": (root / "hello.py").read_bytes() == before,
    }


def _markdown(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    return "\n".join(
        [
            "# CI Repair Harness Benchmark",
            "",
            f"- Dataset: `{report['cases']}` deterministic diagnosis cases + 1 repair flow",
            f"- Run at: `{report['generated_at']}`",
            "- Type: offline, deterministic Harness benchmark; not an LLM code-generation score.",
            "",
            "| Metric | Value |",
            "|---|---:|",
            *[f"| {key} | {value} |" for key, value in metrics.items()],
            "",
            "## Verified workflow",
            "",
            (
                "`CI output -> read-only diagnosis -> structured patch -> isolated "
                "workspace -> test gate -> diff hash -> human approval stop`"
            ),
            "",
            (
                "The source repository remains unchanged. A `needs_approval` result "
                "means the candidate diff and passing test result are ready for a "
                "human decision; this profile does not auto-commit or push."
            ),
            "",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("reports/latest_ci_repair_benchmark"))
    args = parser.parse_args()
    output_base = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
    diagnosis_results = []
    for expected, text in CASES:
        actual = diagnose_ci_output(text)
        diagnosis_results.append(
            {
                "expected": expected,
                "actual": actual["failure_kind"],
                "status_ok": actual["failure_kind"] == expected,
            }
        )
    with tempfile.TemporaryDirectory(prefix="procureops-ci-repair-") as temporary:
        root = Path(temporary) / "source"
        root.mkdir()
        _fixture(root)
        repair = asyncio.run(_repair(root))
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "cases": len(CASES),
        "metrics": {
            "diagnosis_accuracy": round(
                sum(item["status_ok"] for item in diagnosis_results) / len(diagnosis_results), 3
            ),
            "repair_test_gate": float(repair["test_returncode"] == 0),
            "approval_boundary": float(repair["status"] == "needs_approval"),
            "source_isolation": float(repair["source_unchanged"]),
        },
        "diagnosis_cases": diagnosis_results,
        "repair": repair,
    }
    output_base.parent.mkdir(parents=True, exist_ok=True)
    output_base.with_suffix(".json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    output_base.with_suffix(".md").write_text(_markdown(report), encoding="utf-8", newline="\n")
    print(_markdown(report))


if __name__ == "__main__":
    main()
