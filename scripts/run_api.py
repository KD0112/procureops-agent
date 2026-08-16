from __future__ import annotations

import sys
from pathlib import Path

import uvicorn

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

if __name__ == "__main__":
    from procureops.config import api_port_from_environment

    uvicorn.run(
        "procureops.api.app:app",
        host="127.0.0.1",
        port=api_port_from_environment(),
        reload=False,
    )
