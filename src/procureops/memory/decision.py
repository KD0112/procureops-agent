from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from procureops.domain.procurement import LogisticsQuote, SupplierOption

SUPPORTED_STRATEGIES = frozenset({"总成本", "交期", "质量"})
RISK_ORDER = {"low": 0, "medium": 1, "high": 2}


class SupplierSelectionDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    selected: SupplierOption
    strategy: str
    strategy_source: str
    logistics_quote: LogisticsQuote
    ranked_supplier_ids: tuple[str, ...]
    reason: str


class PreferenceDecisionEngine:
    """Apply confirmed preferences only inside a policy-safe deterministic boundary."""

    def select_supplier(
        self,
        *,
        options: tuple[SupplierOption, ...],
        logistics: tuple[LogisticsQuote, ...],
        quantity: Decimal,
        confirmed_preferences: dict[str, object],
        explicit_strategy: str | None = None,
    ) -> SupplierSelectionDecision:
        approved = tuple(item for item in options if item.approved)
        if not approved:
            raise ValueError("no approved supplier options")
        logistics_by_supplier = {item.supplier_id: item for item in logistics}
        eligible = tuple(
            item for item in approved if item.supplier_id in logistics_by_supplier
        )
        if not eligible:
            raise ValueError("approved supplier options have no valid logistics facts")
        if explicit_strategy is not None:
            strategy = self._validate_strategy(explicit_strategy)
            strategy_source = "explicit_task_input"
        else:
            raw_strategy = confirmed_preferences.get(
                "preferred_supplier_strategy",
                "总成本",
            )
            strategy = self._validate_strategy(str(raw_strategy))
            strategy_source = (
                "confirmed_memory"
                if "preferred_supplier_strategy" in confirmed_preferences
                else "tenant_default"
            )

        def total_cost(option: SupplierOption) -> Decimal:
            logistics_quote = logistics_by_supplier[option.supplier_id]
            net_amount = option.unit_price * quantity
            return (
                net_amount
                + net_amount * option.tax_rate
                + logistics_quote.shipping_cost
            )

        if strategy == "交期":
            ranked = sorted(
                eligible,
                key=lambda item: (
                    logistics_by_supplier[item.supplier_id].lead_time_days,
                    total_cost(item),
                    item.supplier_id,
                ),
            )
        elif strategy == "质量":
            ranked = sorted(
                eligible,
                key=lambda item: (
                    RISK_ORDER.get(item.risk_level, 99),
                    total_cost(item),
                    item.supplier_id,
                ),
            )
        else:
            ranked = sorted(
                eligible,
                key=lambda item: (total_cost(item), item.supplier_id),
            )
        selected = ranked[0]
        selected_logistics = logistics_by_supplier[selected.supplier_id]
        return SupplierSelectionDecision(
            selected=selected,
            strategy=strategy,
            strategy_source=strategy_source,
            logistics_quote=selected_logistics,
            ranked_supplier_ids=tuple(item.supplier_id for item in ranked),
            reason=(
                f"selected {selected.supplier_id} by {strategy}; "
                f"lead_time_days={selected_logistics.lead_time_days}; "
                f"bounded_to_approved_suppliers=true"
            ),
        )

    @staticmethod
    def _validate_strategy(value: str) -> str:
        if value not in SUPPORTED_STRATEGIES:
            return "总成本"
        return value
