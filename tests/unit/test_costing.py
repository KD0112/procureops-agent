from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from procureops.domain.costing import calculate_line_cost, summarize_costs
from procureops.domain.procurement import SupplierOption


def option(*, available: str = "10") -> SupplierOption:
    now = datetime.now(UTC)
    return SupplierOption(
        supplier_id="supplier-alpha",
        supplier_name="合成供应商甲",
        approved=True,
        risk_level="low",
        quotation_id="q-001",
        product_id="p-001",
        unit_price=Decimal("19.995"),
        currency="CNY",
        tax_rate=Decimal("0.13"),
        freight=Decimal("10.005"),
        available_quantity=Decimal(available),
        observed_at=now,
        valid_until=now + timedelta(days=1),
    )


def test_decimal_costing_uses_half_up_money_rounding() -> None:
    line = calculate_line_cost(
        line_number=1,
        quantity=Decimal("3"),
        option=option(),
    )

    assert line.net_amount == Decimal("59.99")
    assert line.tax_amount == Decimal("7.80")
    assert line.freight == Decimal("10.01")
    assert line.total_amount == Decimal("77.80")
    summary = summarize_costs([line], currency="CNY")
    assert summary.total_amount == Decimal("77.80")


def test_costing_rejects_insufficient_inventory() -> None:
    with pytest.raises(ValueError, match="insufficient"):
        calculate_line_cost(
            line_number=1,
            quantity=Decimal("3"),
            option=option(available="2"),
        )


def test_line_cost_accepts_authoritative_logistics_freight_override() -> None:
    line = calculate_line_cost(
        line_number=1,
        quantity=Decimal("2"),
        option=option(),
        freight_override=Decimal("45.004"),
    )

    assert line.freight == Decimal("45.00")
    assert line.total_amount == line.net_amount + line.tax_amount + Decimal("45.00")
