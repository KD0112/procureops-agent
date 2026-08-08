from __future__ import annotations

from procureops.domain.enums import TaskStatus

ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.DRAFT: frozenset({TaskStatus.INGESTING}),
    TaskStatus.INGESTING: frozenset({TaskStatus.NEEDS_INPUT, TaskStatus.MATCHING}),
    TaskStatus.NEEDS_INPUT: frozenset({TaskStatus.INGESTING, TaskStatus.MATCHING}),
    TaskStatus.MATCHING: frozenset({TaskStatus.NEEDS_INPUT, TaskStatus.SOURCING}),
    TaskStatus.SOURCING: frozenset(
        {TaskStatus.NEEDS_INPUT, TaskStatus.CALCULATING, TaskStatus.FAILED_RETRYABLE}
    ),
    TaskStatus.CALCULATING: frozenset({TaskStatus.RISK_REVIEW}),
    TaskStatus.RISK_REVIEW: frozenset(
        {TaskStatus.AWAITING_APPROVAL, TaskStatus.APPROVED, TaskStatus.FAILED_TERMINAL}
    ),
    TaskStatus.AWAITING_APPROVAL: frozenset(
        {TaskStatus.APPROVED, TaskStatus.FAILED_TERMINAL}
    ),
    TaskStatus.APPROVED: frozenset({TaskStatus.PO_DRAFTED}),
    TaskStatus.PO_DRAFTED: frozenset({TaskStatus.COMPLETED}),
    TaskStatus.FAILED_RETRYABLE: frozenset({TaskStatus.SOURCING}),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED_TERMINAL: frozenset(),
}


class InvalidStateTransition(ValueError):
    pass


class ProcurementStateMachine:
    @staticmethod
    def ensure_allowed(current: TaskStatus, target: TaskStatus) -> None:
        if target not in ALLOWED_TRANSITIONS[current]:
            raise InvalidStateTransition(f"cannot transition from {current} to {target}")

    @classmethod
    def transition(cls, current: TaskStatus, target: TaskStatus) -> TaskStatus:
        cls.ensure_allowed(current, target)
        return target
