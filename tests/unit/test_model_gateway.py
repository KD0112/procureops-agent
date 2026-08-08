import pytest

from procureops.domain.models import RunBudget, RunContext
from procureops.harness.audit import InMemoryAuditSink
from procureops.harness.budget import RunBudgetLedger
from procureops.harness.errors import BudgetExceeded, PermanentToolError
from procureops.harness.model_gateway import FakeModel, ModelGateway, ModelRequest


def test_harn_002_fake_model_is_deterministic_and_audited(
    run_context: RunContext,
) -> None:
    audit = InMemoryAuditSink()
    fake = FakeModel({"extract_lines": {"lines": [{"part_number": "A-01"}]}})
    gateway = ModelGateway(client=fake, audit=audit)
    response = gateway.invoke(
        context=run_context,
        ledger=RunBudgetLedger(run_context),
        request=ModelRequest(
            purpose="extract_lines",
            payload={"text": "A-01 x 2"},
            response_schema="ProcurementLinesV1",
        ),
    )

    assert response.output["lines"][0]["part_number"] == "A-01"
    assert [event.event_type for event in audit.events()] == [
        "model.started",
        "model.succeeded",
    ]


def test_harn_002_unscripted_fake_model_call_fails_closed(
    run_context: RunContext,
) -> None:
    gateway = ModelGateway(client=FakeModel({}), audit=InMemoryAuditSink())
    with pytest.raises(PermanentToolError, match="no FakeModel output"):
        gateway.invoke(
            context=run_context,
            ledger=RunBudgetLedger(run_context),
            request=ModelRequest(
                purpose="unknown",
                payload={},
                response_schema="UnknownV1",
            ),
        )


def test_harn_003_model_budget_is_enforced(run_context: RunContext) -> None:
    limited = run_context.model_copy(
        update={"budget": RunBudget(max_model_calls=0, max_tool_calls=1)}
    )
    gateway = ModelGateway(
        client=FakeModel({"extract": {"ok": True}}),
        audit=InMemoryAuditSink(),
    )
    with pytest.raises(BudgetExceeded, match="model call"):
        gateway.invoke(
            context=limited,
            ledger=RunBudgetLedger(limited),
            request=ModelRequest(
                purpose="extract",
                payload={},
                response_schema="ExtractionV1",
            ),
        )


def test_harn_002_model_retries_only_transient_failure(run_context: RunContext) -> None:
    from procureops.harness.errors import TransientToolError
    from procureops.harness.model_gateway import ModelResponse

    class FlakyClient:
        provider = "fake"
        model = "flaky-v1"

        def __init__(self) -> None:
            self.calls = 0

        def generate(self, request):
            self.calls += 1
            if self.calls == 1:
                raise TransientToolError("rate limited")
            return ModelResponse(output={"ok": True}, provider=self.provider, model=self.model)

    client = FlakyClient()
    audit = InMemoryAuditSink()
    response = ModelGateway(client=client, audit=audit).invoke(
        context=run_context,
        ledger=RunBudgetLedger(run_context),
        request=ModelRequest(
            purpose="extract",
            payload={},
            response_schema="ExtractionV1",
        ),
    )

    assert response.output == {"ok": True}
    assert client.calls == 2
    assert [event.event_type for event in audit.events()] == [
        "model.started",
        "model.retrying",
        "model.started",
        "model.succeeded",
    ]
