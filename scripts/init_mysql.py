"""Initialize the optional MySQL business schema."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from procureops.config import load_environment  # noqa: E402
from procureops.storage import MySQLBusinessRepository, MySQLSettings  # noqa: E402


async def main() -> None:
    load_environment(PROJECT_ROOT)
    settings = MySQLSettings.from_environment()
    if settings is None:
        raise SystemExit("PROCUREOPS_MYSQL_URL is required")
    repository = MySQLBusinessRepository(settings)
    try:
        await repository.init_schema()
        print("MySQL schema initialized")
    finally:
        await repository.close()


if __name__ == "__main__":
    asyncio.run(main())
