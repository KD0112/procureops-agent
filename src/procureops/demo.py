from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from procureops.storage import ProcureOpsRepository, SQLiteDatabase
from procureops.tenancy import TenantPackRegistry


def build_operational_snapshots(
    products: list[dict[str, Any]],
    *,
    suppliers: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    observed_at = (now or datetime.now(UTC)).replace(microsecond=0)
    quote_expiry = observed_at + timedelta(days=30)
    inventory_expiry = observed_at + timedelta(days=1)
    quotations: list[dict[str, str]] = []
    inventory: list[dict[str, str]] = []
    for index, product in enumerate(products, start=1):
        base = Decimal(str(product.get("demo_unit_price", 180 + index * 137)))
        supplier_profiles = suppliers or [
            {
                "supplier_id": "supplier-alpha",
                "price_multiplier": "1.00",
                "freight": "45.00",
                "inventory_base": 20,
            },
            {
                "supplier_id": "supplier-beta",
                "price_multiplier": "0.97",
                "freight": "80.00",
                "inventory_base": 20,
            },
            {
                "supplier_id": "supplier-unapproved",
                "price_multiplier": "0.80",
                "freight": "20.00",
                "inventory_base": 20,
            },
        ]
        for supplier in supplier_profiles:
            supplier_id = str(supplier["supplier_id"])
            multiplier = Decimal(str(supplier.get("price_multiplier", "1.00")))
            freight = Decimal(str(supplier.get("freight", "0.00")))
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
                    "quantity": str(int(supplier.get("inventory_base", 20)) + index * 5),
                    "observed_at": observed_at.isoformat(),
                    "valid_until": inventory_expiry.isoformat(),
                }
            )
    return quotations, inventory


def build_logistics_snapshots(
    products: list[dict[str, Any]],
    *,
    suppliers: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> list[dict[str, str | int]]:
    observed_at = (now or datetime.now(UTC)).replace(microsecond=0)
    valid_until = observed_at + timedelta(days=7)
    snapshots: list[dict[str, str | int]] = []
    profiles = suppliers or [
        {
            "supplier_id": "supplier-alpha",
            "shipping_method": "standard_freight",
            "lead_time_days": 3,
            "shipping_cost": "45.00",
        },
        {
            "supplier_id": "supplier-beta",
            "shipping_method": "priority_freight",
            "lead_time_days": 1,
            "shipping_cost": "80.00",
        },
        {
            "supplier_id": "supplier-unapproved",
            "shipping_method": "economy_freight",
            "lead_time_days": 5,
            "shipping_cost": "20.00",
        },
    ]
    for index, product in enumerate(products, start=1):
        for supplier in profiles:
            supplier_id = str(supplier["supplier_id"])
            method = str(supplier.get("shipping_method", "standard_freight"))
            lead_time = int(supplier.get("lead_time_days", 3))
            shipping_cost = Decimal(str(supplier.get("shipping_cost", "0.00")))
            snapshots.append(
                {
                    "logistics_quote_id": f"lq-{index:02d}-{supplier_id}",
                    "supplier_id": supplier_id,
                    "product_id": str(product["product_id"]),
                    "shipping_method": method,
                    "lead_time_days": lead_time,
                    "shipping_cost": str(shipping_cost),
                    "observed_at": observed_at.isoformat(),
                    "valid_until": valid_until.isoformat(),
                }
            )
    return snapshots


def seed_demo_database(
    database: SQLiteDatabase,
    *,
    project_root: Path,
    now: datetime | None = None,
) -> ProcureOpsRepository:
    database.migrate()
    repository = ProcureOpsRepository(database)
    registry = TenantPackRegistry(project_root / "data" / "tenant_packs")
    for pack in registry.all():
        products = json.loads(pack.seed_path("catalog").read_text(encoding="utf-8"))
        suppliers = json.loads(pack.seed_path("suppliers").read_text(encoding="utf-8"))
        quotations, inventory = build_operational_snapshots(
            products,
            suppliers=suppliers,
            now=now,
        )
        logistics = build_logistics_snapshots(
            products,
            suppliers=suppliers,
            now=now,
        )
        repository.seed_tenant(
            tenant=pack.tenant.model_dump(mode="json"),
            products=products,
            suppliers=suppliers,
            quotations=quotations,
            inventory=inventory,
            logistics=logistics,
        )
    return repository
