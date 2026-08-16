"""Run optional DeepEval metrics against real model outputs in JSONL form.

Each line must contain case_id, input, actual_output and may contain
expected_output and retrieval_context. The script deliberately refuses to
turn harness status strings into fake answer-quality scores.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from procureops.evals.deepeval_adapter import DeepEvalAdapter  # noqa: E402
from procureops.evals.quality import write_json  # noqa: E402
from procureops.observability import LangfuseTracer  # noqa: E402


def _rows(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument(
        "--metrics",
        nargs="+",
        default=["answer_relevancy", "faithfulness"],
    )
    parser.add_argument("--threshold", type=float, default=0.7)
    parser.add_argument("--output", type=Path, default=Path("reports/latest_deepeval.json"))
    args = parser.parse_args()
    input_path = args.input if args.input.is_absolute() else PROJECT_ROOT / args.input
    rows = _rows(input_path)
    if not rows:
        raise SystemExit("DeepEval input is empty")
    try:
        scores = DeepEvalAdapter.evaluate_rows(
            rows,
            metric_names=args.metrics,
            threshold=args.threshold,
        )
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    grouped: dict[str, list[dict[str, object]]] = {}
    for score in scores:
        grouped.setdefault(score.metric, []).append(score.as_dict())
    payload = {
        "input": str(input_path),
        "case_count": len(rows),
        "metrics": args.metrics,
        "threshold": args.threshold,
        "scores": grouped,
    }
    output_path = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
    write_json(output_path, payload)
    tracer = LangfuseTracer.from_environment()
    with tracer.observe(
        name="evaluation.deepeval",
        as_type="evaluator",
        input={"case_count": len(rows), "metrics": args.metrics},
        metadata={"source": str(input_path)},
    ) as observation:
        valid_scores = [score.score for score in scores if score.score is not None]
        if valid_scores:
            observation.score(
                name="deepeval.mean_score",
                value=sum(valid_scores) / len(valid_scores),
            )
        observation.update(output={"score_count": len(scores), "output": str(output_path)})
    print(f"wrote {len(scores)} DeepEval scores to {output_path}")


if __name__ == "__main__":
    main()
