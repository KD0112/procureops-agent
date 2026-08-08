from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from procureops.domain.models import ApprovalGrant, RunContext


def make_approval(run_context: RunContext, subject: dict[str, object]) -> ApprovalGrant:
    now = datetime.now(UTC)
    return ApprovalGrant.bind(
        approval_id="approval-001",
        tenant_id=run_context.tenant_id,
        task_id=run_context.task_id,
        action="purchase_order_draft",
        subject=subject,
        approved_by="manager-001",
        approved_at=now,
        expires_at=now + timedelta(minutes=30),
    )


def test_harn_006_approval_is_bound_to_exact_subject(run_context: RunContext) -> None:
    approved_subject = {"supplier_id": "s-01", "total": "9800.00"}
    approval = make_approval(run_context, approved_subject)

    assert approval.authorizes(
        context=run_context,
        action="purchase_order_draft",
        subject=approved_subject,
    )
    assert not approval.authorizes(
        context=run_context,
        action="purchase_order_draft",
        subject={"supplier_id": "s-02", "total": "9800.00"},
    )


def test_harn_006_approval_cannot_cross_tenant(run_context: RunContext) -> None:
    subject = {"supplier_id": "s-01"}
    approval = make_approval(run_context, subject)
    other_context = run_context.model_copy(update={"tenant_id": "tenant-b"})

    assert not approval.authorizes(
        context=other_context,
        action="purchase_order_draft",
        subject=subject,
    )


def test_harn_006_approval_expiry_must_follow_approval(run_context: RunContext) -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="later than"):
        ApprovalGrant.bind(
            approval_id="approval-001",
            tenant_id=run_context.tenant_id,
            task_id=run_context.task_id,
            action="purchase_order_draft",
            subject={},
            approved_by="manager-001",
            approved_at=now,
            expires_at=now,
        )
