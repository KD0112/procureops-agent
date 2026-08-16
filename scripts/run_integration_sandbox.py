from __future__ import annotations

import sys
from pathlib import Path

import uvicorn

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from procureops.integrations.sandbox import create_integration_sandbox  # noqa: E402

if __name__ == "__main__":
    app = create_integration_sandbox(
        project_root=PROJECT_ROOT,
        database_path=PROJECT_ROOT / "var" / "integration_sandbox.sqlite3",
    )
    uvicorn.run(app, host="127.0.0.1", port=8101)
