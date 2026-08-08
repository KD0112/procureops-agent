from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from procureops.domain.procurement import CostLine, CostSummary, SupplierOption

MONEY = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def calculate_line_cost(
    *,
    line_number: int,
    quantity: Decimal,
    option: SupplierOption,
    freight_override: Decimal | None = None,
) -> CostLine:
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if option.available_quantity < quantity:
        raise ValueError("supplier inventory is insufficient")
    net_amount = money(quantity * option.unit_price)
    tax_amount = money(net_amount * option.tax_rate)
    freight = money(option.freight if freight_override is None else freight_override)
    return CostLine(
        line_number=line_number,
        product_id=option.product_id,
        supplier_id=option.supplier_id,
        quotation_id=option.quotation_id,
        quantity=quantity,
        unit_price=money(option.unit_price),
        net_amount=net_amount,
        tax_amount=tax_amount,
        freight=freight,
        total_amount=money(net_amount + tax_amount + freight),
    )


def summarize_costs(lines: list[CostLine], *, currency: str) -> CostSummary:
    if not lines:
        raise ValueError("at least one cost line is required")
    return CostSummary(
        currency=currency,
        lines=tuple(lines),
        net_amount=money(sum((line.net_amount for line in lines), Decimal(0))),
        tax_amount=money(sum((line.tax_amount for line in lines), Decimal(0))),
        freight=money(sum((line.freight for line in lines), Decimal(0))),
        total_amount=money(sum((line.total_amount for line in lines), Decimal(0))),
    )
