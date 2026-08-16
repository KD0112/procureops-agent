"""Run the 200-case quality set and write measurable benchmark artifacts."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
for import_root in (SRC_ROOT, SCRIPTS_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from run_evaluation import supplier_research_output  # noqa: E402

from procureops.evals.dataset import load_cases  # noqa: E402
from procureops.evals.quality import (  # noqa: E402
    compare_metrics,
    dataset_summary,
    report_metrics,
    write_json,
)
from procureops.evals.runner import EvaluationRunner, compare_reports  # noqa: E402
from procureops.harness.model_gateway import FakeModel  # noqa: E402


def _fake_outputs() -> dict[str, object]:
    outputs: dict[str, object] = {
        f"specialist_review_{phase}": {
            "decision": "advisory_ok",
            "facts": {"bounded_review": True, "phase": phase},
        }
        for phase in ("intake", "catalog", "supplier", "policy")
    }
    outputs["supplier_research_step"] = supplier_research_output
    return outputs


def _markdown(
    *,
    summary: dict[str, object],
    reports: dict[str, dict[str, object]],
    comparisons: dict[str, object],
) -> str:
    lines = [
        "# Agent quality benchmark (measured)",
        "",
        "> This file is generated from a local deterministic run. "
        "It contains measured values, not a claim about production traffic.",
        "",
        f"- Dataset size: `{summary['dataset_size']}`",
        f"- Dataset versions: `{', '.join(summary['dataset_versions'])}`",
        f"- Splits: `{summary['splits']}`",
        "",
        "## Architecture metrics",
        "",
        "| Architecture | Success | Safety | Evidence | P50 ms | P95 ms | "
        "Avg tools | Model calls | Cost USD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for architecture, report in reports.items():
        metrics = report_metrics(report)
        lines.append(
            "| {architecture} | {task_success_rate:.3f} | {safety_pass_rate:.3f} | "
            "{evidence_coverage:.3f} | {latency_p50_ms:.1f} | {latency_p95_ms:.1f} | "
            "{average_tool_calls:.2f} | {total_model_calls} | "
            "{estimated_total_cost_usd:.4f} |".format(
                architecture=architecture,
                **metrics,
            )
        )
    lines.extend(
        [
            "",
            "## Baseline versus multi-agent",
            "",
            "| Metric | Baseline single | Multi | Delta | Relative delta |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for key, values in comparisons["single_vs_multi"].items():
        lines.append(
            f"| {key} | {values['baseline']} | {values['optimized']} | "
            f"{values['delta']} | {values['relative_delta']} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- Use `task_success_rate`, `safety_pass_rate`, and "
            "`evidence_coverage` as quality gates.",
            "- Treat P95 latency, average tool calls, and cost as regression constraints.",
            "- Run DeepEval separately with real `actual_output` and retrieval context; "
            "the harness status is not an answer-quality substitute.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    dataset_path = PROJECT_ROOT / "data" / "evals" / "agent_quality_v3.jsonl"
    cases = load_cases(dataset_path)
    run_id = uuid4().hex[:8]
    output_root = PROJECT_ROOT / "var" / "evals" / f"quality-{run_id}"
    snapshot_at = datetime.now(UTC).replace(microsecond=0)
    architectures = ("single", "multi", "multi_llm")
    runners = {
        architecture: EvaluationRunner(
            project_root=PROJECT_ROOT,
            database_path=output_root / f"{architecture}.sqlite3",
            replay_root=output_root / "replays" / architecture,
            architecture=architecture,
            snapshot_at=snapshot_at,
            model_client=FakeModel(_fake_outputs())
            if architecture == "multi_llm"
            else None,
        )
        for architecture in architectures
    }
    result_lists = {architecture: [] for architecture in architectures}
    for index, case in enumerate(cases):
        order = architectures if index % 2 == 0 else tuple(reversed(architectures))
        for architecture in order:
            result_lists[architecture].append(runners[architecture].run_case(case))
        if (index + 1) % 25 == 0:
            print(f"completed paired cases={index + 1}/{len(cases)}")

    reports = {
        architecture: runners[architecture].summarize(tuple(result_lists[architecture]))
        for architecture in architectures
    }
    output_root.mkdir(parents=True, exist_ok=True)
    for architecture, report in reports.items():
        write_json(output_root / f"{architecture}.json", report.model_dump(mode="json"))
        print(
            f"{architecture}: passed={report.passed}/{report.dataset_size} "
            f"safety={report.safety_pass_rate:.3f} p95={report.latency_p95_ms:.1f}ms"
        )
    comparison = compare_reports(reports["single"], reports["multi"])
    llm_comparison = compare_reports(reports["single"], reports["multi_llm"])
    summary = dataset_summary(cases)
    payload = {
        "run_id": run_id,
        "dataset": str(dataset_path),
        "dataset_summary": summary,
        "architectures": {
            name: report.model_dump(mode="json") for name, report in reports.items()
        },
        "metrics": {name: report_metrics(report) for name, report in reports.items()},
        "comparisons": {
            "single_vs_multi": compare_metrics(reports["single"], reports["multi"]),
            "single_vs_multi_llm": compare_metrics(reports["single"], reports["multi_llm"]),
            "harness_single_vs_multi": comparison.model_dump(mode="json"),
            "harness_single_vs_multi_llm": llm_comparison.model_dump(mode="json"),
        },
        "artifacts": {"full_reports": str(output_root)},
    }
    stable_json = PROJECT_ROOT / "reports" / "latest_quality_benchmark.json"
    stable_md = PROJECT_ROOT / "reports" / "latest_quality_benchmark.md"
    write_json(stable_json, payload)
    stable_md.parent.mkdir(parents=True, exist_ok=True)
    stable_md.write_text(
        _markdown(
            summary=summary,
            reports={name: report.model_dump(mode="json") for name, report in reports.items()},
            comparisons=payload["comparisons"],
        ),
        encoding="utf-8",
    )
    print(f"wrote benchmark artifacts to {stable_json} and {stable_md}")


if __name__ == "__main__":
    main()
