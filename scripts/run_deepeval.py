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

from procureops.config import load_environment  # noqa: E402
from procureops.evals.deepeval_adapter import DeepEvalAdapter  # noqa: E402
from procureops.evals.quality import write_json  # noqa: E402
from procureops.observability import LangfuseTracer  # noqa: E402


def _rows(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _judge_model(provider: str):
    """Build a real OpenAI-compatible judge from local environment settings."""

    load_environment(PROJECT_ROOT)
    import os

    from deepeval.models import OpenAIModel

    candidates = {
        "openai": ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"),
        "deepseek": ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL"),
        "zhipu": ("ZHIPU_API_KEY", "ZHIPU_BASE_URL", "ZHIPU_MODEL"),
    }
    names = [provider] if provider != "auto" else ["openai", "deepseek", "zhipu"]
    for name in names:
        key_name, base_name, model_name = candidates[name]
        api_key = os.getenv(key_name, "").strip()
        if not api_key:
            continue
        model = os.getenv(model_name, "").strip()
        base_url = os.getenv(base_name, "").strip() or None
        if not model:
            raise SystemExit(f"{key_name} exists but {model_name} is missing")
        return OpenAIModel(
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=0,
        ), name, model
    raise SystemExit(
        "No judge credentials found. Set OPENAI_API_KEY, DEEPSEEK_API_KEY, or ZHIPU_API_KEY."
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument(
        "--metrics",
        nargs="+",
        default=["answer_relevancy", "faithfulness"],
    )
    parser.add_argument("--threshold", type=float, default=0.7)
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Evaluate only the first N rows; 0 evaluates the complete input.",
    )
    parser.add_argument(
        "--judge-provider",
        choices=["auto", "openai", "deepseek", "zhipu"],
        default="auto",
        help="Use a real OpenAI-compatible judge; auto prefers OpenAI then DeepSeek then Zhipu.",
    )
    parser.add_argument("--output", type=Path, default=Path("reports/latest_deepeval.json"))
    args = parser.parse_args()
    input_path = args.input if args.input.is_absolute() else PROJECT_ROOT / args.input
    rows = _rows(input_path)
    if args.limit < 0:
        raise SystemExit("--limit must be non-negative")
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        raise SystemExit("DeepEval input is empty")
    judge_model, judge_provider, judge_model_name = _judge_model(args.judge_provider)
    try:
        scores = DeepEvalAdapter.evaluate_rows(
            rows,
            metric_names=args.metrics,
            threshold=args.threshold,
            judge_model=judge_model,
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
        "judge_provider": judge_provider,
        "judge_model": judge_model_name,
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
