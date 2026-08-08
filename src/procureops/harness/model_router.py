from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from procureops.domain.models import RunContext, canonical_hash
from procureops.harness.audit import AuditEvent, AuditSink
from procureops.harness.budget import RunBudgetLedger
from procureops.harness.errors import (
    BudgetExceeded,
    DeadlineExceeded,
    PermanentToolError,
    TransientToolError,
)
from procureops.harness.model_gateway import (
    ModelClient,
    ModelGateway,
    ModelRequest,
    ModelResponse,
)


@dataclass(frozen=True, slots=True)
class ModelRoute:
    name: str
    client: ModelClient


@dataclass(slots=True)
class _CircuitState:
    consecutive_failures: int = 0
    opened_at: datetime | None = None


class RoutedModelGateway:
    """Provider routing with bounded fallback and an in-process circuit breaker."""

    def __init__(
        self,
        *,
        routes: Sequence[ModelRoute],
        audit: AuditSink,
        failure_threshold: int = 2,
        recovery_timeout: timedelta = timedelta(seconds=30),
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not routes:
            raise ValueError("at least one model route is required")
        if failure_threshold < 1:
            raise ValueError("failure threshold must be positive")
        self.routes = tuple(routes)
        self.audit = audit
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.now = now or (lambda: datetime.now(UTC))
        self._states = {route.name: _CircuitState() for route in self.routes}

    def invoke(
        self,
        *,
        context: RunContext,
        ledger: RunBudgetLedger,
        request: ModelRequest,
    ) -> ModelResponse:
        payload_hash = canonical_hash(request.payload)
        attempted: list[str] = []
        last_error: Exception | None = None
        for route in self.routes:
            if not self._available(route.name):
                self._audit_route(
                    context,
                    "model.route_skipped",
                    payload_hash,
                    route,
                    {"reason": "circuit_open"},
                )
                continue
            attempted.append(route.name)
            self._audit_route(
                context,
                "model.route_selected",
                payload_hash,
                route,
                {"route_index": len(attempted) - 1},
            )
            try:
                response = ModelGateway(
                    client=route.client,
                    audit=self.audit,
                    max_attempts=1,
                ).invoke(context=context, ledger=ledger, request=request)
            except (BudgetExceeded, DeadlineExceeded):
                raise
            except (TransientToolError, PermanentToolError) as exc:
                last_error = exc
                self._record_failure(route.name)
                self._audit_route(
                    context,
                    "model.route_failed_over",
                    payload_hash,
                    route,
                    {"error_type": type(exc).__name__},
                )
                continue
            self._reset(route.name)
            if len(attempted) > 1:
                self._audit_route(
                    context,
                    "model.fallback_succeeded",
                    payload_hash,
                    route,
                    {"attempted_routes": attempted},
                )
            return response
        if last_error is not None:
            raise last_error
        raise TransientToolError("all model routes have an open circuit")

    def health(self) -> tuple[dict[str, object], ...]:
        now = self.now()
        return tuple(
            {
                "route": route.name,
                "provider": route.client.provider,
                "model": route.client.model,
                "state": "closed" if self._available(route.name, at=now) else "open",
                "consecutive_failures": self._states[route.name].consecutive_failures,
            }
            for route in self.routes
        )

    def _available(self, name: str, *, at: datetime | None = None) -> bool:
        state = self._states[name]
        if state.opened_at is None:
            return True
        current = at or self.now()
        if current - state.opened_at >= self.recovery_timeout:
            state.opened_at = None
            state.consecutive_failures = 0
            return True
        return False

    def _record_failure(self, name: str) -> None:
        state = self._states[name]
        state.consecutive_failures += 1
        if state.consecutive_failures >= self.failure_threshold:
            state.opened_at = self.now()

    def _reset(self, name: str) -> None:
        state = self._states[name]
        state.consecutive_failures = 0
        state.opened_at = None

    def _audit_route(
        self,
        context: RunContext,
        event_type: str,
        payload_hash: str,
        route: ModelRoute,
        metadata: dict[str, object],
    ) -> None:
        self.audit.append(
            AuditEvent.from_context(
                context,
                event_type,
                payload_hash=payload_hash,
                metadata={
                    "route": route.name,
                    "provider": route.client.provider,
                    "model": route.client.model,
                    **metadata,
                },
            )
        )
