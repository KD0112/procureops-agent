"""Run a deliberately small, opt-in evaluation against the configured paid model."""

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
from procureops.evals.live_model import (  # noqa: E402
    MODEL_GOLD_PATH,
    evaluate_quality_gate,
    load_gold_cases,
    run_live_model_eval,
    save_live_model_report,
)
from procureops.harness.provider_clients import client_from_environment  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int)
    parser.add_argument("--provider", choices=("deepseek", "qwen"))
    parser.add_argument("--dataset", type=Path, default=MODEL_GOLD_PATH)
    parser.add_argument(
        "--split",
        choices=("development", "regression", "holdout", "all"),
        default="regression",
    )
    parser.add_argument("--confirm-holdout", action="store_true")
    parser.add_argument("--min-pass-rate", type=float, default=0.85)
    parser.add_argument("--min-safety-rate", type=float, default=1.0)
    parser.add_argument("--max-p95-ms", type=float, default=10_000)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "latest_live_model_eval.json",
    )
    args = parser.parse_args()
    if args.split in {"holdout", "all"} and not args.confirm_holdout:
        raise SystemExit("holdout execution requires --confirm-holdout")
    if not 0 <= args.min_pass_rate <= 1 or not 0 <= args.min_safety_rate <= 1:
        raise SystemExit("quality rate thresholds must be between 0 and 1")
    if args.max_p95_ms <= 0:
        raise SystemExit("--max-p95-ms must be positive")
    cases = load_gold_cases(
        args.dataset.resolve(),
        split=None if args.split == "all" else args.split,
    )
    if args.limit is not None:
        if not 1 <= args.limit <= len(cases):
            raise SystemExit(f"--limit must be between 1 and {len(cases)}")
        cases = cases[: args.limit]
    load_environment(PROJECT_ROOT)
    report = run_live_model_eval(
        client=client_from_environment(kind="text", provider_override=args.provider),
        cases=cases,
    )
    baseline = (
        json.loads(args.baseline.read_text(encoding="utf-8"))
        if args.baseline is not None
        else None
    )
    report["quality_gate"] = evaluate_quality_gate(
        report,
        min_pass_rate=args.min_pass_rate,
        min_safety_rate=args.min_safety_rate,
        max_p95_ms=args.max_p95_ms,
        baseline=baseline,
    )
    path = save_live_model_report(report, args.output)
    print(
        json.dumps(
            {
                "report": str(path),
                "provider": report["provider"],
                "model": report["model"],
                "passed": report["passed"],
                "case_count": report["case_count"],
                "pass_rate": report["pass_rate"],
                "p95_latency_ms": report["p95_latency_ms"],
                "total_tokens": report["total_tokens"],
                "total_cost_usd": report["total_cost_usd"],
                "split": args.split,
                "quality_gate_passed": report["quality_gate"]["passed"],
            },
            ensure_ascii=False,
        )
    )
    if not report["quality_gate"]["passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
