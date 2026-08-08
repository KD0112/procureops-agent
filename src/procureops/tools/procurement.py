from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from procureops.domain.enums import ActionKind, RiskLevel
from procureops.harness.errors import PermanentToolError, TransientToolError
from procureops.harness.tool_gateway import ToolDefinition, ToolGateway
from procureops.storage import ProcureOpsRepository


def register_procurement_tools(
    gateway: ToolGateway,
    repository: ProcureOpsRepository,
    *,
    faults: Mapping[str, str] | None = None,
) -> None:
    fault_counts: dict[str, int] = {}

    def maybe_raise_fault(tool_name: str) -> None:
        fault = (faults or {}).get(tool_name)
        fault_counts[tool_name] = fault_counts.get(tool_name, 0) + 1
        if fault == "transient_once" and fault_counts[tool_name] == 1:
            raise TransientToolError(f"synthetic transient fault: {tool_name}")
        if fault == "permanent":
            raise PermanentToolError(f"synthetic permanent fault: {tool_name}")

    def catalog_lookup(arguments: Mapping[str, Any]) -> list[dict[str, Any]]:
        maybe_raise_fault("catalog_lookup")
        candidates = repository.search_products(
            tenant_id=str(arguments["tenant_id"]),
            query=str(arguments["query"]),
            part_number=(
                str(arguments["part_number"]) if arguments.get("part_number") else None
            ),
        )
        return [item.model_dump(mode="json") for item in candidates]

    def supplier_lookup(arguments: Mapping[str, Any]) -> list[dict[str, Any]]:
        maybe_raise_fault("supplier_lookup")
        options = repository.supplier_options(
            tenant_id=str(arguments["tenant_id"]),
            product_id=str(arguments["product_id"]),
            required_quantity=Decimal(str(arguments["quantity"])),
        )
        return [item.model_dump(mode="json") for item in options]

    def purchase_order_draft(arguments: Mapping[str, Any]) -> dict[str, Any]:
        maybe_raise_fault("purchase_order_draft")
        row, database_hit = repository.create_po_draft(
            tenant_id=str(arguments["tenant_id"]),
            task_id=str(arguments["task_id"]),
            idempotency_key=str(arguments["po_idempotency_key"]),
            payload=dict(arguments["payload"]),
            total_amount=Decimal(str(arguments["total_amount"])),
            currency=str(arguments["currency"]),
        )
        return {"po_draft": row, "database_idempotency_hit": database_hit}

    def logistics_quote(arguments: Mapping[str, Any]) -> list[dict[str, Any]]:
        maybe_raise_fault("logistics_quote")
        raw_supplier_ids = arguments.get("supplier_ids")
        if not isinstance(raw_supplier_ids, list) or not raw_supplier_ids:
            raise PermanentToolError("logistics_quote requires supplier_ids")
        quotes = repository.logistics_quotes(
            tenant_id=str(arguments["tenant_id"]),
            product_id=str(arguments["product_id"]),
            supplier_ids=tuple(str(item) for item in raw_supplier_ids),
        )
        return [item.model_dump(mode="json") for item in quotes]

    gateway.register(
        ToolDefinition(
            name="logistics_quote",
            handler=logistics_quote,
            risk_level=RiskLevel.R0_READ_ONLY,
            action_kind=ActionKind.READ,
            max_attempts=2,
            tenant_argument="tenant_id",
        )
    )
    gateway.register(
        ToolDefinition(
            name="catalog_lookup",
            handler=catalog_lookup,
            risk_level=RiskLevel.R0_READ_ONLY,
            action_kind=ActionKind.READ,
            max_attempts=2,
            tenant_argument="tenant_id",
        )
    )
    gateway.register(
        ToolDefinition(
            name="supplier_lookup",
            handler=supplier_lookup,
            risk_level=RiskLevel.R0_READ_ONLY,
            action_kind=ActionKind.READ,
            max_attempts=2,
            tenant_argument="tenant_id",
        )
    )
    gateway.register(
        ToolDefinition(
            name="purchase_order_draft",
            handler=purchase_order_draft,
            risk_level=RiskLevel.R3_FINANCIAL_OR_LEGAL,
            action_kind=ActionKind.FINANCIAL,
            required_any_roles=frozenset({"procurement_operator"}),
            tenant_argument="tenant_id",
        )
    )
