from __future__ import annotations

from typing import Any

from procureops.domain.models import RunBudget
from procureops.harness.audit import InMemoryAuditSink
from procureops.harness.budget import RunBudgetLedger
from procureops.harness.errors import TransientToolError
from procureops.harness.model_gateway import ModelRequest, ModelResponse
from procureops.harness.model_router import ModelRoute, RoutedModelGateway
from procureops.harness.provider_clients import model_routes_from_environment


class ScriptedClient:
    def __init__(
        self,
        *,
        provider: str,
        outcomes: list[dict[str, Any] | Exception],
    ) -> None:
        self.provider = provider
        self.model = f"{provider}-model"
        self.outcomes = outcomes
        self.calls = 0

    def generate(self, _request: ModelRequest) -> ModelResponse:
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return ModelResponse(
            output=outcome,
            provider=self.provider,
            model=self.model,
        )


def test_router_fails_over_and_opens_primary_circuit(run_context) -> None:
    primary = ScriptedClient(
        provider="qwen",
        outcomes=[TransientToolError("temporary qwen outage")],
    )
    fallback = ScriptedClient(
        provider="deepseek",
        outcomes=[{"ok": 1}, {"ok": 2}],
    )
    audit = InMemoryAuditSink()
    router = RoutedModelGateway(
        routes=(
            ModelRoute(name="primary-qwen", client=primary),
            ModelRoute(name="fallback-deepseek", client=fallback),
        ),
        audit=audit,
        failure_threshold=1,
    )
    context = run_context.model_copy(
        update={"budget": RunBudget(max_model_calls=4, max_tool_calls=0)}
    )
    ledger = RunBudgetLedger(context)
    request = ModelRequest(purpose="test", payload={"safe": True}, response_schema="V1")

    first = router.invoke(context=context, ledger=ledger, request=request)
    second = router.invoke(context=context, ledger=ledger, request=request)

    assert first.provider == second.provider == "deepseek"
    assert primary.calls == 1
    assert fallback.calls == 2
    assert router.health()[0]["state"] == "open"
    event_types = [event.event_type for event in audit.events()]
    assert "model.route_failed_over" in event_types
    assert "model.route_skipped" in event_types
    assert "model.fallback_succeeded" in event_types


def test_environment_route_prefers_qwen_when_dashscope_is_configured(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-dashscope-key")
    monkeypatch.setenv("QWEN_TEXT_MODEL", "qwen-flash")
    monkeypatch.delenv("AGENT_TEXT_ROUTE", raising=False)

    routes = model_routes_from_environment(kind="text")

    assert routes[0].client.provider == "qwen"
