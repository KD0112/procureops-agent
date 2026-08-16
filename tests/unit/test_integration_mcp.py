from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from procureops.harness.errors import PermanentToolError
from procureops.integrations.mcp import (
    MCPIntegrationConfig,
    MCPReadOnlyEnterpriseIntegrationSuite,
)


class FakeMCPTransport:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def call_tool(self, *, server, tool_name, arguments):
        self.calls.append((server.name, tool_name, arguments))
        return self.payload


def _config() -> MCPIntegrationConfig:
    return MCPIntegrationConfig.model_validate(
        {
            "servers": {
                "erp": {
                    "name": "erp",
                    "command": ["python", "server.py"],
                    "allowed_tools": [
                        "catalog.search",
                        "supplier.options",
                        "logistics.quotes",
                    ],
                }
            },
            "bindings": {
                "catalog_lookup": {"server": "erp", "tool": "catalog.search"},
                "supplier_lookup": {"server": "erp", "tool": "supplier.options"},
                "logistics_quote": {"server": "erp", "tool": "logistics.quotes"},
            },
        }
    )


def test_mcp_suite_binds_tenant_and_validates_typed_response(repository, tmp_path) -> None:
    del tmp_path
    transport = FakeMCPTransport(
        {
            "tenant_id": "tenant_engineering_machinery",
            "items": [
                {
                    "product_id": "p-hyd-pump-001",
                    "sku": "DEMO-HYD-PUMP-001",
                    "name": "hydraulic pump",
                    "category": "hydraulics",
                    "unit": "item",
                    "score": "1",
                    "match_reasons": ["exact_sku"],
                }
            ],
        }
    )
    suite = MCPReadOnlyEnterpriseIntegrationSuite(
        repository=repository,
        pack=_tenant_pack(),
        config=_config(),
        transport=transport,
    )

    items = suite.catalog_lookup(
        tenant_id="tenant_engineering_machinery",
        query="hydraulic pump",
        part_number="DEMO-HYD-PUMP-001",
    )

    assert items[0]["source_system"] == "mcp:erp"
    assert transport.calls[0][1] == "catalog.search"
    assert transport.calls[0][2]["tenant_id"] == "tenant_engineering_machinery"


def test_mcp_suite_fails_closed_on_tenant_mismatch_and_write(repository) -> None:
    suite = MCPReadOnlyEnterpriseIntegrationSuite(
        repository=repository,
        pack=_tenant_pack(),
        config=_config(),
        transport=FakeMCPTransport({"tenant_id": "tenant_other", "items": []}),
    )

    with pytest.raises(PermanentToolError, match="tenant mismatch"):
        suite.catalog_lookup(
            tenant_id="tenant_engineering_machinery", query="pump", part_number=None
        )
    with pytest.raises(PermanentToolError, match="not exposed through MCP"):
        suite.purchase_order_draft(
            tenant_id="tenant_engineering_machinery",
            task_id="task-1",
            idempotency_key="po:1",
            payload={},
            total_amount=Decimal("1"),
            currency="CNY",
            approval_subject_hash="a" * 64,
        )


def _tenant_pack():
    from pathlib import Path

    from procureops.tenancy import TenantPackRegistry

    root = Path(__file__).resolve().parents[2]
    return TenantPackRegistry(root / "data" / "tenant_packs").get(
        "tenant_engineering_machinery"
    )
