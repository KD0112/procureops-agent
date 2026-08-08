"""Run the local lease-based worker once or continuously."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from procureops.runtime import ProcureOpsRuntime  # noqa: E402
from procureops.worker.service import ProcureOpsWorker  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    args = parser.parse_args()
    runtime = ProcureOpsRuntime.create(project_root=PROJECT_ROOT)
    worker = ProcureOpsWorker(runtime=runtime)
    while True:
        result = worker.run_once()
        if result is not None:
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        if not args.loop:
            break
        if result is None:
            time.sleep(max(0.1, args.poll_seconds))


if __name__ == "__main__":
    main()
