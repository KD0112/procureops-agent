from decimal import Decimal

from procureops.agents.supplier_research import BoundedSupplierResearchAgent
from procureops.harness.audit import InMemoryAuditSink
from procureops.harness.budget import RunBudgetLedger
from procureops.harness.model_gateway import FakeModel, ModelGateway
from procureops.harness.tool_gateway import ToolGateway
from procureops.memory.decision import PreferenceDecisionEngine
from procureops.storage import ProcureOpsRepository
from procureops.tools import register_procurement_tools


def test_logistics_quote_is_dynamic_tenant_scoped_tool_fact(
    repository: ProcureOpsRepository,
    run_context,
) -> None:
    audit = InMemoryAuditSink()
    gateway = ToolGateway(audit=audit)
    register_procurement_tools(gateway, repository)

    result = gateway.execute(
        context=run_context,
        ledger=RunBudgetLedger(run_context),
        tool_name="logistics_quote",
        arguments={
            "tenant_id": run_context.tenant_id,
            "product_id": "p-hyd-pump-001",
            "supplier_ids": ["supplier-alpha", "supplier-beta"],
        },
    )

    assert len(result.output) == 2
    assert all(item["lead_time_days"] >= 0 for item in result.output)
    assert all(Decimal(item["shipping_cost"]) >= 0 for item in result.output)
    assert all(item["observed_at"] and item["valid_until"] for item in result.output)
    assert any(event.event_type == "tool.succeeded" for event in audit.events())


def test_confirmed_preference_changes_only_bounded_supplier_ranking(
    repository: ProcureOpsRepository,
) -> None:
    options = repository.supplier_options(
        tenant_id="tenant_engineering_machinery",
        product_id="p-hyd-pump-001",
        required_quantity=Decimal("1"),
    )
    logistics = repository.logistics_quotes(
        tenant_id="tenant_engineering_machinery",
        product_id="p-hyd-pump-001",
        supplier_ids=tuple(item.supplier_id for item in options),
    )
    engine = PreferenceDecisionEngine()

    cheapest = engine.select_supplier(
        options=options,
        logistics=logistics,
        quantity=Decimal("1"),
        confirmed_preferences={"preferred_supplier_strategy": "总成本"},
    )
    fastest = engine.select_supplier(
        options=options,
        logistics=logistics,
        quantity=Decimal("1"),
        confirmed_preferences={"preferred_supplier_strategy": "交期"},
    )
    explicit = engine.select_supplier(
        options=options,
        logistics=logistics,
        quantity=Decimal("1"),
        confirmed_preferences={"preferred_supplier_strategy": "交期"},
        explicit_strategy="总成本",
    )

    assert cheapest.selected.supplier_id == "supplier-alpha"
    assert fastest.selected.supplier_id == "supplier-beta"
    assert fastest.strategy_source == "confirmed_memory"
    assert explicit.selected.supplier_id == "supplier-alpha"
    assert explicit.strategy_source == "explicit_task_input"


def test_bounded_supplier_agent_selects_only_read_tool_and_returns_validated_decision(
    repository: ProcureOpsRepository,
    run_context,
) -> None:
    audit = InMemoryAuditSink()
    gateway = ToolGateway(audit=audit)
    register_procurement_tools(gateway, repository)
    model = FakeModel(
        {
            "supplier_research_step": [
                {
                    "action": "logistics_quote",
                    "supplier_id": None,
                    "rationale": "need current delivery facts",
                },
                {
                    "action": "finish",
                    "supplier_id": "supplier-beta",
                    "rationale": "fastest approved supplier",
                },
            ]
        }
    )
    agent = BoundedSupplierResearchAgent(
        model_gateway=ModelGateway(client=model, audit=audit),
        tool_gateway=gateway,
    )
    options = repository.supplier_options(
        tenant_id=run_context.tenant_id,
        product_id="p-hyd-pump-001",
        required_quantity=Decimal("1"),
    )

    result = agent.research(
        context=run_context,
        ledger=RunBudgetLedger(run_context),
        product_id="p-hyd-pump-001",
        quantity=Decimal("1"),
        options=options,
        confirmed_preferences={"preferred_supplier_strategy": "交期"},
    )

    assert result.decision.selected.supplier_id == "supplier-beta"
    assert result.model_recommendation == "supplier-beta"
    assert result.used_fallback is False
    assert [step.action for step in result.steps] == ["logistics_quote", "finish"]
    assert {event.metadata.get("tool_name") for event in audit.events()} >= {
        "logistics_quote"
    }


def test_bounded_supplier_agent_cannot_escalate_to_write_tool(
    repository: ProcureOpsRepository,
    run_context,
) -> None:
    audit = InMemoryAuditSink()
    gateway = ToolGateway(audit=audit)
    register_procurement_tools(gateway, repository)
    model = FakeModel(
        {
            "supplier_research_step": {
                "action": "purchase_order_draft",
                "supplier_id": "supplier-alpha",
                "rationale": "attempted escalation",
            }
        }
    )
    agent = BoundedSupplierResearchAgent(
        model_gateway=ModelGateway(client=model, audit=audit),
        tool_gateway=gateway,
    )
    options = repository.supplier_options(
        tenant_id=run_context.tenant_id,
        product_id="p-hyd-pump-001",
        required_quantity=Decimal("1"),
    )

    result = agent.research(
        context=run_context,
        ledger=RunBudgetLedger(run_context),
        product_id="p-hyd-pump-001",
        quantity=Decimal("1"),
        options=options,
        confirmed_preferences={},
    )

    assert result.used_fallback is True
    assert result.decision.selected.supplier_id == "supplier-alpha"
    started_tools = {
        event.metadata.get("tool_name")
        for event in audit.events()
        if event.event_type == "tool.started"
    }
    assert started_tools == {"logistics_quote"}
