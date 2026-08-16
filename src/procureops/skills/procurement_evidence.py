from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


class AsyncLookup(Protocol):
    def __call__(self, **kwargs: Any) -> Awaitable[list[dict[str, Any]]]: ...


class ProcurementEvidenceResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: str
    product: dict[str, Any] | None = None
    suppliers: tuple[dict[str, Any], ...] = ()
    logistics: tuple[dict[str, Any], ...] = ()
    status: str = Field(pattern="^(matched|needs_input|no_match)$")
    evidence_count: int = Field(ge=0)
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProcurementEvidenceSkill:
    """Read-only procurement workflow that can use local, HTTP or MCP tools."""

    catalog_lookup: AsyncLookup
    supplier_lookup: AsyncLookup
    logistics_quote: AsyncLookup

    async def run(
        self, *, tenant_id: str, query: str, quantity: Decimal | str = "1"
    ) -> ProcurementEvidenceResult:
        if not tenant_id or not query.strip():
            raise ValueError("tenant_id and query are required")
        products = await self.catalog_lookup(tenant_id=tenant_id, query=query, part_number=None)
        if not products:
            return ProcurementEvidenceResult(
                query=query, status="no_match", evidence_count=0, warnings=("no product match",)
            )
        product = products[0]
        product_id = str(product.get("product_id", ""))
        if not product_id:
            return ProcurementEvidenceResult(
                query=query,
                status="needs_input",
                evidence_count=1,
                warnings=("product id missing",),
            )
        suppliers = await self.supplier_lookup(
            tenant_id=tenant_id, product_id=product_id, quantity=Decimal(str(quantity))
        )
        approved = tuple(item for item in suppliers if item.get("approved", True))
        if not approved:
            return ProcurementEvidenceResult(
                query=query,
                product=product,
                status="needs_input",
                evidence_count=1 + len(suppliers),
                warnings=("no approved supplier",),
            )
        supplier_ids = tuple(
            str(item["supplier_id"]) for item in approved if item.get("supplier_id")
        )
        logistics = await self.logistics_quote(
            tenant_id=tenant_id, product_id=product_id, supplier_ids=supplier_ids
        )
        warnings: list[str] = []
        if not logistics:
            warnings.append("logistics quote unavailable")
        return ProcurementEvidenceResult(
            query=query,
            product=product,
            suppliers=approved,
            logistics=tuple(logistics),
            status="matched",
            evidence_count=1 + len(approved) + len(logistics),
            warnings=tuple(warnings),
        )

    @classmethod
    def from_tool_gateway(cls, *, gateway, context, ledger) -> ProcurementEvidenceSkill:
        async def call(tool_name: str, arguments: Mapping[str, Any]) -> list[dict[str, Any]]:
            result = await asyncio.to_thread(
                gateway.execute,
                context=context,
                ledger=ledger,
                tool_name=tool_name,
                arguments=dict(arguments),
            )
            if not isinstance(result.output, list):
                raise TypeError(f"{tool_name} returned a non-list output")
            return [dict(item) for item in result.output if isinstance(item, Mapping)]

        return cls(
            catalog_lookup=lambda **kwargs: call("catalog_lookup", kwargs),
            supplier_lookup=lambda **kwargs: call("supplier_lookup", kwargs),
            logistics_quote=lambda **kwargs: call("logistics_quote", kwargs),
        )
