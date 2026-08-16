from __future__ import annotations

import sys
from pathlib import Path

from procureops.integrations.mcp import (
    MCPIntegrationConfig,
    MCPReadOnlyEnterpriseIntegrationSuite,
    StdioMCPTransport,
)
from procureops.tenancy import TenantPackRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_stdio_mcp_sandbox_performs_handshake_and_read_tool(repository) -> None:
    pack = TenantPackRegistry(PROJECT_ROOT / "data" / "tenant_packs").get(
        "tenant_engineering_machinery"
    )
    config = MCPIntegrationConfig.sandbox(
        command=(
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "run_mcp_sandbox.py"),
            "--database",
            str(repository.database.path),
        )
    )
    suite = MCPReadOnlyEnterpriseIntegrationSuite(
        repository=repository,
        pack=pack,
        config=config,
        transport=StdioMCPTransport(),
    )

    items = suite.catalog_lookup(
        tenant_id=pack.tenant.tenant_id,
        query="DEMO-HYD-PUMP-001",
        part_number="DEMO-HYD-PUMP-001",
    )

    assert items[0]["sku"] == "DEMO-HYD-PUMP-001"
    assert items[0]["source_system"] == "mcp:procureops_sandbox"
