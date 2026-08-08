from itertools import pairwise

import pytest

from procureops.domain.enums import TaskStatus
from procureops.workflows.state_machine import (
    InvalidStateTransition,
    ProcurementStateMachine,
)


def test_state_machine_allows_declared_path() -> None:
    path = [
        TaskStatus.DRAFT,
        TaskStatus.INGESTING,
        TaskStatus.MATCHING,
        TaskStatus.SOURCING,
        TaskStatus.CALCULATING,
        TaskStatus.RISK_REVIEW,
        TaskStatus.APPROVED,
        TaskStatus.PO_DRAFTED,
        TaskStatus.COMPLETED,
    ]
    for current, target in pairwise(path):
        assert ProcurementStateMachine.transition(current, target) == target


def test_state_machine_rejects_skipping_approval_review() -> None:
    with pytest.raises(InvalidStateTransition):
        ProcurementStateMachine.transition(TaskStatus.MATCHING, TaskStatus.PO_DRAFTED)
