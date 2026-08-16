"""Generate the versioned 200-case agent quality dataset."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from procureops.evals.dataset import generate_extended_cases, save_cases  # noqa: E402


def main() -> None:
    destination = PROJECT_ROOT / "data" / "evals" / "agent_quality_v3.jsonl"
    cases = generate_extended_cases()
    save_cases(destination, cases)
    print(f"wrote {len(cases)} cases to {destination}")


if __name__ == "__main__":
    main()
