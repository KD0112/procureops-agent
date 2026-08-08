from decimal import Decimal

import pytest

from procureops.domain.enums import TaskStatus
from procureops.domain.procurement import ProcurementLine
from procureops.harness.errors import IdempotencyConflict
from procureops.storage import ProcureOpsRepository


def test_repository_enforces_tenant_scope_and_optimistic_version(
    repository: ProcureOpsRepository,
) -> None:
    task = repository.create_task(
        tenant_id="tenant_engineering_machinery",
        created_by="buyer-001",
        request={"source": "test"},
        workflow_version="1.0.0",
        task_id="task-repo-001",
    )
    transitioned = repository.transition_task(
        tenant_id=task.tenant_id,
        task_id=task.task_id,
        target=TaskStatus.INGESTING,
        expected_version=task.version,
    )

    assert transitioned.version == 2
    with pytest.raises(KeyError):
        repository.get_task(tenant_id="tenant-other", task_id=task.task_id)
    with pytest.raises(RuntimeError, match="version conflict"):
        repository.transition_task(
            tenant_id=task.tenant_id,
            task_id=task.task_id,
            target=TaskStatus.MATCHING,
            expected_version=task.version,
        )


def test_catalog_supplier_and_evidence_are_operational_data(
    repository: ProcureOpsRepository,
) -> None:
    task = repository.create_task(
        tenant_id="tenant_engineering_machinery",
        created_by="buyer-001",
        request={"source": "test"},
        workflow_version="1.0.0",
        task_id="task-repo-002",
    )
    repository.replace_task_items(
        tenant_id=task.tenant_id,
        task_id=task.task_id,
        lines=[
            ProcurementLine(
                line_number=1,
                description="液压泵",
                quantity=Decimal("2"),
                unit="台",
                part_number="DEMO-HYD-PUMP-001",
            )
        ],
    )
    candidates = repository.search_products(
        tenant_id=task.tenant_id,
        query="液压泵",
        part_number="DEMO-HYD-PUMP-001",
    )
    assert candidates[0].score == Decimal("1")
    repository.select_product(
        tenant_id=task.tenant_id,
        task_id=task.task_id,
        line_number=1,
        candidate=candidates[0],
    )
    options = repository.supplier_options(
        tenant_id=task.tenant_id,
        product_id=candidates[0].product_id,
        required_quantity=Decimal("2"),
    )

    assert options
    assert options[0].approved
    assert all(option.valid_until for option in options)


def test_po_draft_has_database_backed_idempotency(
    repository: ProcureOpsRepository,
) -> None:
    task = repository.create_task(
        tenant_id="tenant_engineering_machinery",
        created_by="buyer-001",
        request={"source": "test"},
        workflow_version="1.0.0",
        task_id="task-repo-003",
    )
    payload = {"supplier_id": "supplier-alpha", "lines": [{"sku": "DEMO-1"}]}
    first, first_hit = repository.create_po_draft(
        tenant_id=task.tenant_id,
        task_id=task.task_id,
        idempotency_key="po-task-repo-003",
        payload=payload,
        total_amount=Decimal("100.00"),
        currency="CNY",
    )
    second, second_hit = repository.create_po_draft(
        tenant_id=task.tenant_id,
        task_id=task.task_id,
        idempotency_key="po-task-repo-003",
        payload=payload,
        total_amount=Decimal("100.00"),
        currency="CNY",
    )

    assert not first_hit
    assert second_hit
    assert first["po_draft_id"] == second["po_draft_id"]
    with pytest.raises(IdempotencyConflict):
        repository.create_po_draft(
            tenant_id=task.tenant_id,
            task_id=task.task_id,
            idempotency_key="po-task-repo-003",
            payload={"supplier_id": "supplier-beta"},
            total_amount=Decimal("100.00"),
            currency="CNY",
        )
