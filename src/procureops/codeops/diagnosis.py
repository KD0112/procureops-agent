"""Deterministic, read-only CI failure diagnosis for RepoPilot.

The model/planner may use this result to propose a patch, but the diagnosis
never executes the CI text as code and never changes repository state.
"""

from __future__ import annotations

import re
from typing import Any

_MAX_CI_OUTPUT = 20_000
_TEST_RE = re.compile(r"\bFAILED\s+([^\s-]+(?:::[^\s-]+)*)", re.IGNORECASE)
_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|token|password|secret|authorization)(\s*[:=]\s*)[^\s,;]+"
)


def diagnose_ci_output(ci_output: str) -> dict[str, Any]:
    """Classify common CI failures without trusting or executing log content."""

    text = str(ci_output or "").strip()
    if not text:
        return {
            "status": "no_input",
            "failure_kind": "unknown",
            "summary": "No CI output was supplied.",
            "failed_tests": [],
            "evidence": [],
            "repair_hints": ["Provide the failing CI output before proposing a patch."],
            "actionability": "needs_input",
        }
    text = text[-_MAX_CI_OUTPUT:]
    lower = text.casefold()
    failed_tests = _unique(_TEST_RE.findall(text))[:20]
    evidence = _evidence(text)

    if _looks_passed(lower) and not failed_tests:
        return _result(
            status="passed",
            failure_kind="none",
            summary="The supplied CI excerpt does not show a failing check.",
            failed_tests=failed_tests,
            evidence=evidence,
            repair_hints=[
                "Do not create a repair patch until a reproducible failure is available."
            ],
            actionability="no_repair",
        )
    if "syntaxerror" in lower or "indentationerror" in lower:
        kind = "syntax_error"
        hints = [
            "Read the exact file and line from CI, then propose the smallest syntax-only patch."
        ]
    elif "modulenotfounderror" in lower or "importerror" in lower or "dependency" in lower:
        kind = "dependency_error"
        hints = [
            "Verify the declared dependency and environment first; do not silently edit lockfiles."
        ]
    elif "timeout" in lower or "timed out" in lower or "timeoutexceeded" in lower:
        kind = "timeout"
        hints = [
            "Inspect the slow test or external I/O; do not increase the timeout without evidence."
        ]
    elif "ruff" in lower or re.search(r"\b[efw]\d{3}\b", lower) or "lint" in lower:
        kind = "lint_failure"
        hints = [
            (
                "Read the reported file and line, apply a narrow lint fix, then "
                "rerun the same lint command."
            )
        ]
    elif failed_tests or "assertionerror" in lower or "test failed" in lower:
        kind = "test_failure"
        hints = [
            (
                "Read the failing test and implementation, then run the smallest "
                "relevant test before the full gate."
            )
        ]
    else:
        kind = "unknown"
        hints = [
            "Collect a reproducible CI command and a bounded log excerpt before proposing a repair."
        ]

    actionability = (
        "patch_candidate"
        if kind in {"test_failure", "syntax_error", "lint_failure"}
        else "needs_environment"
    )
    return _result(
        status="failed",
        failure_kind=kind,
        summary=f"Detected {kind.replace('_', ' ')} in the supplied CI excerpt.",
        failed_tests=failed_tests,
        evidence=evidence,
        repair_hints=hints,
        actionability=actionability,
    )


def _result(**values: Any) -> dict[str, Any]:
    return {"source": "ci_output", **values}


def _looks_passed(lower: str) -> bool:
    return any(
        marker in lower
        for marker in ("passed", "pass rate: 100", "exit code: 0", "0 failed")
    ) and not any(marker in lower for marker in ("failed", "error", "traceback"))


def _evidence(text: str) -> list[str]:
    lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        lower = line.casefold()
        if not line:
            continue
        if any(
            marker in lower
            for marker in (
                "failed",
                "error",
                "traceback",
                "assertion",
                "timeout",
                "ruff",
                "syntax",
                "import",
                "module",
            )
        ):
            lines.append(_SECRET_RE.sub(r"\1\2[REDACTED]", line[:500]))
    return _unique(lines)[:8]


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
