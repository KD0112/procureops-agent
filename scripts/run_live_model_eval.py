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
    DEFAULT_LIVE_CASES,
    run_live_model_eval,
    save_live_model_report,
)
from procureops.harness.provider_clients import client_from_environment  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "latest_live_model_eval.json",
    )
    args = parser.parse_args()
    if not 1 <= args.limit <= len(DEFAULT_LIVE_CASES):
        raise SystemExit(f"--limit must be between 1 and {len(DEFAULT_LIVE_CASES)}")
    load_environment(PROJECT_ROOT)
    report = run_live_model_eval(
        client=client_from_environment(kind="text"),
        cases=DEFAULT_LIVE_CASES[: args.limit],
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
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
