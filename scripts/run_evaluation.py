"""Run single/multi Agent evaluations and write an A/B report."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from procureops.evals.dataset import load_cases  # noqa: E402
from procureops.evals.runner import EvaluationRunner, compare_reports  # noqa: E402
from procureops.harness.model_gateway import FakeModel  # noqa: E402


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    cases_path = PROJECT_ROOT / "data" / "eval_cases" / "procurement_e2e_100.jsonl"
    cases = load_cases(cases_path)
    run_id = uuid4().hex[:8]
    output_root = PROJECT_ROOT / "var" / "evals" / run_id
    snapshot_at = datetime.now(UTC).replace(microsecond=0)
    runners = {}
    fake_specialist_outputs = {
        f"specialist_review_{phase}": {
            "decision": "advisory_ok",
            "facts": {"bounded_review": True, "phase": phase},
        }
        for phase in ("intake", "catalog", "supplier", "policy")
    }
    architectures = ("single", "multi", "multi_llm")
    for architecture in architectures:
        runners[architecture] = EvaluationRunner(
            project_root=PROJECT_ROOT,
            database_path=output_root / f"{architecture}.sqlite3",
            replay_root=output_root / "replays" / architecture,
            architecture=architecture,
            snapshot_at=snapshot_at,
            model_client=(
                FakeModel(fake_specialist_outputs) if architecture == "multi_llm" else None
            ),
        )
    result_lists = {architecture: [] for architecture in architectures}
    for index, case in enumerate(cases):
        order = architectures if index % 2 == 0 else tuple(reversed(architectures))
        for architecture in order:
            result_lists[architecture].append(runners[architecture].run_case(case))
        if (index + 1) % 25 == 0:
            print(f"completed paired cases={index + 1}/{len(cases)}")

    reports = {}
    for architecture in architectures:
        report = runners[architecture].summarize(tuple(result_lists[architecture]))
        reports[architecture] = report
        write_json(
            output_root / f"{architecture}.json",
            report.model_dump(mode="json"),
        )
        print(
            f"{architecture}: passed={report.passed}/{report.dataset_size} "
            f"safety={report.safety_pass_rate:.3f} p95={report.latency_p95_ms:.1f}ms"
        )
    comparison = compare_reports(reports["single"], reports["multi"])
    llm_comparison = compare_reports(reports["single"], reports["multi_llm"])
    write_json(output_root / "ab_comparison.json", comparison.model_dump(mode="json"))
    write_json(
        output_root / "llm_ab_comparison.json",
        llm_comparison.model_dump(mode="json"),
    )
    stable_reports = PROJECT_ROOT / "reports"
    write_json(
        stable_reports / "latest_single_summary.json",
        reports["single"].model_dump(mode="json", exclude={"results"}),
    )
    write_json(
        stable_reports / "latest_multi_summary.json",
        reports["multi"].model_dump(mode="json", exclude={"results"}),
    )
    write_json(
        stable_reports / "latest_llm_multi_summary.json",
        reports["multi_llm"].model_dump(mode="json", exclude={"results"}),
    )
    write_json(
        stable_reports / "latest_ab_comparison.json",
        comparison.model_dump(mode="json"),
    )
    write_json(
        stable_reports / "latest_llm_ab_comparison.json",
        llm_comparison.model_dump(mode="json"),
    )
    print(f"recommendation={comparison.recommendation} -> {output_root}")


if __name__ == "__main__":
    main()
