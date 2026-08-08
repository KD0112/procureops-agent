"""Generate the governed 100-case offline evaluation dataset."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from procureops.evals.dataset import generate_cases, save_cases  # noqa: E402


def main() -> None:
    cases = generate_cases()
    path = PROJECT_ROOT / "data" / "eval_cases" / "procurement_e2e_100.jsonl"
    save_cases(path, cases)
    print(f"generated {len(cases)} cases -> {path}")


if __name__ == "__main__":
    main()
