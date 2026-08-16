from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProcurementLine(BaseModel):
    model_config = ConfigDict(frozen=True)

    line_number: int = Field(ge=1)
    description: str = Field(min_length=1)
    quantity: Decimal = Field(gt=0)
    unit: str = Field(min_length=1)
    part_number: str | None = None
    equipment_model: str | None = None
    allow_equivalent: bool = False


class ProductCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    product_id: str
    sku: str
    name: str
    category: str
    unit: str
    score: Decimal = Field(ge=0, le=1)
    match_reasons: tuple[str, ...]
    source_system: str = "operational_database"
    source_locator: str = "products"


class SupplierOption(BaseModel):
    model_config = ConfigDict(frozen=True)

    supplier_id: str
    supplier_name: str
    approved: bool
    risk_level: str
    quotation_id: str
    product_id: str
    unit_price: Decimal = Field(ge=0)
    currency: str
    tax_rate: Decimal = Field(ge=0)
    freight: Decimal = Field(ge=0)
    available_quantity: Decimal = Field(ge=0)
    observed_at: datetime
    valid_until: datetime
    source_system: str = "operational_database"
    source_locator: str = "quotations"


class LogisticsQuote(BaseModel):
    model_config = ConfigDict(frozen=True)

    logistics_quote_id: str
    supplier_id: str
    product_id: str
    shipping_method: str
    lead_time_days: int = Field(ge=0)
    shipping_cost: Decimal = Field(ge=0)
    observed_at: datetime
    valid_until: datetime
    source_system: str = "operational_database"
    source_locator: str = "logistics_quotes"


class CostLine(BaseModel):
    model_config = ConfigDict(frozen=True)

    line_number: int
    product_id: str
    supplier_id: str
    quotation_id: str
    quantity: Decimal
    unit_price: Decimal
    net_amount: Decimal
    tax_amount: Decimal
    freight: Decimal
    total_amount: Decimal


class CostSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    currency: str
    lines: tuple[CostLine, ...]
    net_amount: Decimal
    tax_amount: Decimal
    freight: Decimal
    total_amount: Decimal


class TaskSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    task_id: str
    status: str
    version: int
    request: dict[str, Any]
