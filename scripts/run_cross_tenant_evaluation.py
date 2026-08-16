"""Run the fixed second-tenant and isolation suite without paid models."""

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
from procureops.evals.runner import EvaluationRunner  # noqa: E402


def main() -> None:
    cases = load_cases(PROJECT_ROOT / "data" / "eval_cases" / "cross_tenant_it_20.jsonl")
    run_id = uuid4().hex[:8]
    output_root = PROJECT_ROOT / "var" / "evals" / f"cross-tenant-{run_id}"
    runner = EvaluationRunner(
        project_root=PROJECT_ROOT,
        database_path=output_root / "cross_tenant.sqlite3",
        replay_root=output_root / "replays",
        architecture="single",
        snapshot_at=datetime.now(UTC).replace(microsecond=0),
    )
    report = runner.run(cases)
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "report.json").write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    stable = PROJECT_ROOT / "reports" / "latest_cross_tenant_summary.json"
    stable.write_text(
        json.dumps(
            report.model_dump(mode="json", exclude={"results"}),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"cross-tenant: passed={report.passed}/{report.dataset_size} "
        f"safety={report.safety_pass_rate:.3f} -> {output_root}"
    )
    if report.passed != report.dataset_size or report.safety_pass_rate != 1:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
