"""Run the offline Lost-in-the-middle harness and save a measurable report."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DAY1_ROOT = PROJECT_ROOT.parent / "day1" / "project2"
for import_root in (DAY1_ROOT, PROJECT_ROOT / "src"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from context_experiments import (  # noqa: E402
    Evidence,
    pack_position_aware,
    position_sweep_summary,
    run_lost_middle_experiment,
)

from procureops.evals.quality import write_json  # noqa: E402


def _edge_reader(context: str, _question: str) -> bool:
    lines = context.splitlines()
    return bool(lines and ("Needle fact" in lines[0] or "Needle fact" in lines[-1]))


def main() -> None:
    baseline = run_lost_middle_experiment(_edge_reader, item_count=9)
    evidence = [Evidence("target", "Needle fact: delivery deadline is 2026-09-30.", 1.0, True)]
    evidence.extend(
        Evidence(f"noise-{index}", f"Unrelated catalogue paragraph {index}.", 0.05)
        for index in range(8)
    )
    packed = pack_position_aware(evidence)
    position_aware_context = "\n".join(item.text for item in packed)
    position_aware_hit = _edge_reader(position_aware_context, "What is the delivery deadline?")
    payload = {
        "item_count": 9,
        "positions": list(baseline.accuracy_by_position),
        "baseline": position_sweep_summary(baseline),
        "position_aware": {
            "target_at_position": next(
                index for index, item in enumerate(packed) if item.evidence_id == "target"
            ),
            "answer_accuracy": 1.0 if position_aware_hit else 0.0,
        },
        "interpretation": (
            "The offline reader intentionally models a position-sensitive consumer. "
            "This validates the metric and packing harness, not a real LLM quality claim."
        ),
    }
    destination = PROJECT_ROOT / "reports" / "latest_lost_middle_benchmark.json"
    write_json(destination, payload)
    print(f"wrote Lost-in-the-middle benchmark to {destination}")


if __name__ == "__main__":
    main()
