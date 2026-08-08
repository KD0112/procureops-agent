from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from procureops.domain.models import RunContext


def test_harn_001_run_context_is_immutable(run_context: RunContext) -> None:
    with pytest.raises(ValidationError):
        run_context.tenant_id = "another-tenant"  # type: ignore[misc]


def test_harn_001_deadline_must_be_timezone_aware(
    run_context: RunContext,
) -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        run_context.model_copy(update={"deadline_at": datetime.now()}).model_validate(
            {
                **run_context.model_dump(),
                "deadline_at": datetime.now(),
            }
        )


def test_harn_001_expired_context_is_detected(run_context: RunContext) -> None:
    assert run_context.is_expired(run_context.deadline_at + timedelta(seconds=1))
    assert not run_context.is_expired(datetime.now(UTC))
