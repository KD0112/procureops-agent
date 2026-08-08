from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from procureops.storage import ProcureOpsRepository, SQLiteDatabase


def build_operational_snapshots(
    products: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    observed_at = (now or datetime.now(UTC)).replace(microsecond=0)
    quote_expiry = observed_at + timedelta(days=30)
    inventory_expiry = observed_at + timedelta(days=1)
    quotations: list[dict[str, str]] = []
    inventory: list[dict[str, str]] = []
    for index, product in enumerate(products, start=1):
        base = Decimal(180 + index * 137)
        for supplier_id, multiplier, freight in (
            ("supplier-alpha", Decimal("1.00"), Decimal("45.00")),
            ("supplier-beta", Decimal("0.97"), Decimal("80.00")),
            ("supplier-unapproved", Decimal("0.80"), Decimal("20.00")),
        ):
            quotations.append(
                {
                    "quotation_id": f"q-{index:02d}-{supplier_id}",
                    "supplier_id": supplier_id,
                    "product_id": str(product["product_id"]),
                    "unit_price": str((base * multiplier).quantize(Decimal("0.01"))),
                    "currency": "CNY",
                    "tax_rate": "0.13",
                    "freight": str(freight),
                    "observed_at": observed_at.isoformat(),
                    "valid_until": quote_expiry.isoformat(),
                }
            )
            inventory.append(
                {
                    "supplier_id": supplier_id,
                    "product_id": str(product["product_id"]),
                    "quantity": str(20 + index * 5),
                    "observed_at": observed_at.isoformat(),
                    "valid_until": inventory_expiry.isoformat(),
                }
            )
    return quotations, inventory


def seed_demo_database(
    database: SQLiteDatabase,
    *,
    project_root: Path,
    now: datetime | None = None,
) -> ProcureOpsRepository:
    data_root = project_root / "data"
    tenant = json.loads(
        (
            data_root
            / "tenant_packs"
            / "tenant_engineering_machinery"
            / "tenant.json"
        ).read_text(encoding="utf-8")
    )
    products = json.loads((data_root / "demo" / "catalog.json").read_text(encoding="utf-8"))
    suppliers = json.loads(
        (data_root / "demo" / "suppliers.json").read_text(encoding="utf-8")
    )
    quotations, inventory = build_operational_snapshots(products, now=now)
    database.migrate()
    repository = ProcureOpsRepository(database)
    repository.seed_tenant(
        tenant=tenant,
        products=products,
        suppliers=suppliers,
        quotations=quotations,
        inventory=inventory,
    )
    return repository
