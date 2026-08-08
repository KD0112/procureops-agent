from __future__ import annotations

import threading

from procureops.domain.models import RunContext
from procureops.harness.errors import BudgetExceeded


class RunBudgetLedger:
    def __init__(self, context: RunContext) -> None:
        self.context = context
        self.model_calls = 0
        self.tool_calls = 0
        self.tokens = 0
        self.cost_usd = 0.0
        self._lock = threading.Lock()

    def charge_model_call(self) -> None:
        with self._lock:
            if self.model_calls + 1 > self.context.budget.max_model_calls:
                raise BudgetExceeded("model call budget exceeded")
            self.model_calls += 1

    def charge_tool_call(self) -> None:
        with self._lock:
            if self.tool_calls + 1 > self.context.budget.max_tool_calls:
                raise BudgetExceeded("tool call budget exceeded")
            self.tool_calls += 1

    def charge_usage(self, *, tokens: int, cost_usd: float) -> None:
        if tokens < 0 or cost_usd < 0:
            raise ValueError("usage cannot be negative")
        with self._lock:
            new_tokens = self.tokens + tokens
            new_cost = self.cost_usd + cost_usd
            if new_tokens > self.context.budget.max_tokens:
                raise BudgetExceeded("token budget exceeded")
            if new_cost > self.context.budget.max_cost_usd:
                raise BudgetExceeded("cost budget exceeded")
            self.tokens = new_tokens
            self.cost_usd = new_cost

    def snapshot(self) -> dict[str, int | float]:
        with self._lock:
            return {
                "model_calls": self.model_calls,
                "tool_calls": self.tool_calls,
                "tokens": self.tokens,
                "cost_usd": round(self.cost_usd, 8),
            }

