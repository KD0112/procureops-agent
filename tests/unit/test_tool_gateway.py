from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from procureops.domain.enums import ActionKind, RiskLevel
from procureops.domain.models import ApprovalGrant, RunContext
from procureops.harness.audit import InMemoryAuditSink
from procureops.harness.budget import RunBudgetLedger
from procureops.harness.errors import (
    ApprovalRequired,
    AuthorizationDenied,
    IdempotencyConflict,
    PermanentToolError,
    ProhibitedAction,
    TransientToolError,
)
from procureops.harness.tool_gateway import ToolDefinition, ToolGateway


def approval_for(
    context: RunContext,
    action: str,
    subject: Mapping[str, Any],
) -> ApprovalGrant:
    now = datetime.now(UTC)
    return ApprovalGrant.bind(
        approval_id="approval-001",
        tenant_id=context.tenant_id,
        task_id=context.task_id,
        action=action,
        subject=dict(subject),
        approved_by="manager-001",
        approved_at=now,
        expires_at=now + timedelta(minutes=30),
    )


def test_harn_004_rbac_denies_before_handler_call(run_context: RunContext) -> None:
    calls = 0

    def handler(_: Mapping[str, Any]) -> dict[str, bool]:
        nonlocal calls
        calls += 1
        return {"ok": True}

    gateway = ToolGateway(audit=InMemoryAuditSink())
    gateway.register(
        ToolDefinition(
            name="compliance_report",
            handler=handler,
            risk_level=RiskLevel.R0_READ_ONLY,
            action_kind=ActionKind.READ,
            required_any_roles=frozenset({"compliance_approver"}),
        )
    )

    with pytest.raises(AuthorizationDenied):
        gateway.execute(
            context=run_context,
            ledger=RunBudgetLedger(run_context),
            tool_name="compliance_report",
            arguments={},
        )
    assert calls == 0


def test_harn_006_high_risk_tool_requires_matching_approval(
    run_context: RunContext,
) -> None:
    gateway = ToolGateway(audit=InMemoryAuditSink())
    gateway.register(
        ToolDefinition(
            name="purchase_order_draft",
            handler=lambda args: {"draft": dict(args)},
            risk_level=RiskLevel.R3_FINANCIAL_OR_LEGAL,
            action_kind=ActionKind.FINANCIAL,
        )
    )
    args = {"supplier_id": "s-01", "total": "9800.00"}

    with pytest.raises(ApprovalRequired):
        gateway.execute(
            context=run_context,
            ledger=RunBudgetLedger(run_context),
            tool_name="purchase_order_draft",
            arguments=args,
            idempotency_key="po-001",
        )

    wrong_approval = approval_for(
        run_context,
        "purchase_order_draft",
        {"supplier_id": "s-01", "total": "9900.00"},
    )
    with pytest.raises(ApprovalRequired):
        gateway.execute(
            context=run_context,
            ledger=RunBudgetLedger(run_context),
            tool_name="purchase_order_draft",
            arguments=args,
            approval=wrong_approval,
            idempotency_key="po-001",
        )


def test_harn_005_write_is_idempotent_and_collision_is_rejected(
    run_context: RunContext,
) -> None:
    calls = 0

    def handler(args: Mapping[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"draft_number": "PO-DRAFT-001", **args}

    audit = InMemoryAuditSink()
    gateway = ToolGateway(audit=audit)
    gateway.register(
        ToolDefinition(
            name="save_internal_draft",
            handler=handler,
            risk_level=RiskLevel.R1_INTERNAL_DRAFT,
            action_kind=ActionKind.WRITE_DRAFT,
        )
    )
    ledger = RunBudgetLedger(run_context)
    args = {"supplier_id": "s-01"}

    first = gateway.execute(
        context=run_context,
        ledger=ledger,
        tool_name="save_internal_draft",
        arguments=args,
        idempotency_key="draft-001",
    )
    second = gateway.execute(
        context=run_context,
        ledger=ledger,
        tool_name="save_internal_draft",
        arguments=args,
        idempotency_key="draft-001",
    )

    assert calls == 1
    assert not first.idempotency_hit
    assert second.idempotency_hit
    with pytest.raises(IdempotencyConflict):
        gateway.execute(
            context=run_context,
            ledger=ledger,
            tool_name="save_internal_draft",
            arguments={"supplier_id": "s-02"},
            idempotency_key="draft-001",
        )


def test_harn_004_read_tool_retries_only_classified_transient_failure(
    run_context: RunContext,
) -> None:
    calls = 0

    def flaky(_: Mapping[str, Any]) -> dict[str, bool]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TransientToolError("temporary timeout")
        return {"available": True}

    gateway = ToolGateway(audit=InMemoryAuditSink())
    gateway.register(
        ToolDefinition(
            name="inventory_lookup",
            handler=flaky,
            risk_level=RiskLevel.R0_READ_ONLY,
            action_kind=ActionKind.READ,
            max_attempts=2,
        )
    )
    result = gateway.execute(
        context=run_context,
        ledger=RunBudgetLedger(run_context),
        tool_name="inventory_lookup",
        arguments={"sku": "P-01"},
    )

    assert result.attempts == 2
    assert calls == 2


def test_harn_004_unknown_failure_fails_closed_without_retry(
    run_context: RunContext,
) -> None:
    calls = 0

    def broken(_: Mapping[str, Any]) -> None:
        nonlocal calls
        calls += 1
        raise KeyError("unexpected")

    gateway = ToolGateway(audit=InMemoryAuditSink())
    gateway.register(
        ToolDefinition(
            name="supplier_lookup",
            handler=broken,
            risk_level=RiskLevel.R0_READ_ONLY,
            action_kind=ActionKind.READ,
            max_attempts=3,
        )
    )
    with pytest.raises(PermanentToolError, match="unclassified"):
        gateway.execute(
            context=run_context,
            ledger=RunBudgetLedger(run_context),
            tool_name="supplier_lookup",
            arguments={"supplier_id": "s-01"},
        )
    assert calls == 1


def test_harn_004_prohibited_action_never_reaches_handler(
    run_context: RunContext,
) -> None:
    gateway = ToolGateway(audit=InMemoryAuditSink())
    gateway.register(
        ToolDefinition(
            name="delete_audit_log",
            handler=lambda _: pytest.fail("handler must not run"),
            risk_level=RiskLevel.R4_PROHIBITED,
            action_kind=ActionKind.DESTRUCTIVE,
        )
    )
    with pytest.raises(ProhibitedAction):
        gateway.execute(
            context=run_context,
            ledger=RunBudgetLedger(run_context),
            tool_name="delete_audit_log",
            arguments={},
            idempotency_key="delete-001",
        )


def test_harn_004_tool_tenant_argument_must_match_context(
    run_context: RunContext,
) -> None:
    gateway = ToolGateway(audit=InMemoryAuditSink())
    gateway.register(
        ToolDefinition(
            name="tenant_catalog",
            handler=lambda _: {"ok": True},
            risk_level=RiskLevel.R0_READ_ONLY,
            action_kind=ActionKind.READ,
            tenant_argument="tenant_id",
        )
    )

    with pytest.raises(AuthorizationDenied, match="tenant argument"):
        gateway.execute(
            context=run_context,
            ledger=RunBudgetLedger(run_context),
            tool_name="tenant_catalog",
            arguments={"tenant_id": "tenant-other"},
        )
