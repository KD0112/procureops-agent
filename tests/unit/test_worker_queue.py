from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from procureops.harness.errors import IdempotencyConflict
from procureops.storage import ProcureOpsRepository
from procureops.worker.queue import SQLiteWorkQueue


def _task(repository: ProcureOpsRepository, task_id: str = "queue-task") -> None:
    repository.create_task(
        tenant_id="tenant_engineering_machinery",
        created_by="buyer",
        request={"source_type": "text"},
        workflow_version="1.0.0",
        task_id=task_id,
    )


def test_queue_is_idempotent_and_rejects_payload_collision(
    repository: ProcureOpsRepository,
) -> None:
    _task(repository)
    queue = SQLiteWorkQueue(repository.database)
    first, reused = queue.enqueue(
        tenant_id="tenant_engineering_machinery",
        task_id="queue-task",
        job_type="process_intake",
        payload={"source": {"kind": "text", "text": "one"}},
        idempotency_key="intake:queue-task:v1",
    )
    repeated, reused_again = queue.enqueue(
        tenant_id="tenant_engineering_machinery",
        task_id="queue-task",
        job_type="process_intake",
        payload={"source": {"kind": "text", "text": "one"}},
        idempotency_key="intake:queue-task:v1",
    )
    assert reused is False
    assert reused_again is True
    assert repeated.job_id == first.job_id
    with pytest.raises(IdempotencyConflict):
        queue.enqueue(
            tenant_id="tenant_engineering_machinery",
            task_id="queue-task",
            job_type="process_intake",
            payload={"source": {"kind": "text", "text": "changed"}},
            idempotency_key="intake:queue-task:v1",
        )


def test_expired_lease_is_recovered_and_terminal_failures_dead_letter(
    repository: ProcureOpsRepository,
) -> None:
    _task(repository, "lease-task")
    queue = SQLiteWorkQueue(repository.database)
    queue.enqueue(
        tenant_id="tenant_engineering_machinery",
        task_id="lease-task",
        job_type="process_intake",
        payload={"source": {"kind": "text", "text": "one"}},
        idempotency_key="intake:lease-task:v1",
        max_attempts=2,
    )
    now = datetime.now(UTC)
    first = queue.claim(worker_id="worker-a", lease_seconds=1, now=now)
    assert first is not None
    assert queue.claim(worker_id="worker-b", now=now) is None
    recovered = queue.claim(worker_id="worker-b", now=now + timedelta(seconds=2))
    assert recovered is not None
    assert recovered.job_id == first.job_id
    assert recovered.attempts == 2
    outcome = queue.fail(
        job=recovered,
        worker_id="worker-b",
        error=RuntimeError("permanent"),
        retryable=False,
        now=now + timedelta(seconds=2),
    )
    assert outcome == "dead_letter"
    assert (
        queue.jobs_for_task(tenant_id="tenant_engineering_machinery", task_id="lease-task")[
            0
        ].status
        == "dead_letter"
    )
