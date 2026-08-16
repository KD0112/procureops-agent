"""Start the local sandbox briefly and run an actual HTTP procurement smoke test."""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from demo_external_systems import main as run_demo  # noqa: E402

from procureops.integrations.sandbox import create_integration_sandbox  # noqa: E402


def main() -> None:
    app = create_integration_sandbox(
        project_root=PROJECT_ROOT,
        database_path=PROJECT_ROOT / "var" / "smoke_integration_sandbox.sqlite3",
    )
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=8101, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(40):
            try:
                if httpx.get("http://127.0.0.1:8101/health", timeout=0.5).is_success:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            raise RuntimeError("integration sandbox did not become ready")
        os.environ.update(
            {
                "PROCUREOPS_INTEGRATION_PROFILE": "http_sandbox",
                "PROCUREOPS_ERP_BASE_URL": "http://127.0.0.1:8101",
                "PROCUREOPS_SUPPLIER_BASE_URL": "http://127.0.0.1:8101",
                "PROCUREOPS_LOGISTICS_BASE_URL": "http://127.0.0.1:8101",
                "PROCUREOPS_INTEGRATION_API_KEY": "local-only-not-a-secret",
            }
        )
        run_demo()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
