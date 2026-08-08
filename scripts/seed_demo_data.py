"""Seed deterministic synthetic operational data into the local SQLite profile."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from procureops.demo import seed_demo_database  # noqa: E402
from procureops.storage import SQLiteDatabase  # noqa: E402


def main() -> None:
    database = SQLiteDatabase(PROJECT_ROOT / "var" / "procureops.sqlite3")
    applied = database.migrate()
    seed_demo_database(database, project_root=PROJECT_ROOT)
    print(f"seeded tenant=tenant_engineering_machinery migrations={list(applied)}")


if __name__ == "__main__":
    main()
