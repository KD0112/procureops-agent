from datetime import UTC, datetime, timedelta

import pytest

from procureops.memory import MemoryService
from procureops.memory.candidates import detect_preference_candidates
from procureops.storage import ProcureOpsRepository


def memory_service(repository: ProcureOpsRepository) -> MemoryService:
    return MemoryService(repository.database)


def test_memory_requires_confirmation_and_is_user_tenant_isolated(
    repository: ProcureOpsRepository,
) -> None:
    service = memory_service(repository)
    candidate = service.propose(
        tenant_id="tenant_engineering_machinery",
        user_id="buyer-001",
        memory_key="preferred_delivery_window",
        value="工作日上午",
        confidence=0.9,
        proposed_by="single_agent_v1",
    )

    assert candidate.status == "candidate"
    assert service.active_preferences(
        tenant_id=candidate.tenant_id,
        user_id=candidate.user_id,
    ) == {}
    confirmed = service.confirm(
        tenant_id=candidate.tenant_id,
        user_id=candidate.user_id,
        record_id=candidate.record_id,
        confirmed_by="buyer-001",
    )
    assert confirmed.status == "confirmed"
    assert service.active_preferences(
        tenant_id=candidate.tenant_id,
        user_id=candidate.user_id,
    )["preferred_delivery_window"] == "工作日上午"
    assert service.active_preferences(
        tenant_id=candidate.tenant_id,
        user_id="buyer-other",
    ) == {}
    with pytest.raises(KeyError):
        service.get(
            tenant_id="tenant-other",
            user_id=candidate.user_id,
            record_id=candidate.record_id,
        )


def test_memory_correction_and_deletion_preserve_history(
    repository: ProcureOpsRepository,
) -> None:
    service = memory_service(repository)
    candidate = service.propose(
        tenant_id="tenant_engineering_machinery",
        user_id="buyer-001",
        memory_key="preferred_supplier_note",
        value="优先比较交期",
        confidence=0.8,
        proposed_by="single_agent_v1",
    )
    confirmed = service.confirm(
        tenant_id=candidate.tenant_id,
        user_id=candidate.user_id,
        record_id=candidate.record_id,
        confirmed_by="buyer-001",
    )
    corrected = service.correct(
        tenant_id=confirmed.tenant_id,
        user_id=confirmed.user_id,
        record_id=confirmed.record_id,
        new_value="优先比较总成本",
        corrected_by="buyer-001",
    )

    assert corrected.replaces_record_id == confirmed.record_id
    assert service.get(
        tenant_id=confirmed.tenant_id,
        user_id=confirmed.user_id,
        record_id=confirmed.record_id,
    ).status == "corrected"
    service.delete(
        tenant_id=corrected.tenant_id,
        user_id=corrected.user_id,
        record_id=corrected.record_id,
    )
    assert service.active_preferences(
        tenant_id=corrected.tenant_id,
        user_id=corrected.user_id,
    ) == {}


@pytest.mark.parametrize(
    "memory_key",
    ["api_key", "bank_card", "身份证号码", "approval_threshold"],
)
def test_memory_rejects_sensitive_and_policy_keys(
    repository: ProcureOpsRepository,
    memory_key: str,
) -> None:
    with pytest.raises(ValueError):
        memory_service(repository).propose(
            tenant_id="tenant_engineering_machinery",
            user_id="buyer-001",
            memory_key=memory_key,
            value="should-not-store",
            confidence=1,
            proposed_by="single_agent_v1",
        )


def test_expired_memory_is_not_used(repository: ProcureOpsRepository) -> None:
    service = memory_service(repository)
    candidate = service.propose(
        tenant_id="tenant_engineering_machinery",
        user_id="buyer-001",
        memory_key="preferred_delivery_window",
        value="下午",
        confidence=1,
        proposed_by="single_agent_v1",
        ttl=timedelta(seconds=1),
    )
    service.confirm(
        tenant_id=candidate.tenant_id,
        user_id=candidate.user_id,
        record_id=candidate.record_id,
        confirmed_by="buyer-001",
    )

    assert service.active_preferences(
        tenant_id=candidate.tenant_id,
        user_id=candidate.user_id,
        at=datetime.now(UTC) + timedelta(seconds=2),
    ) == {}


def test_memory_candidate_deduplicates_same_source_and_can_be_listed(
    repository: ProcureOpsRepository,
) -> None:
    service = memory_service(repository)
    first = service.propose(
        tenant_id="tenant_engineering_machinery",
        user_id="buyer-001",
        memory_key="preferred_delivery_window",
        value="工作日上午",
        confidence=0.92,
        proposed_by="preference_detector_v1",
        source_hash="same-source-hash",
    )
    second = service.propose(
        tenant_id="tenant_engineering_machinery",
        user_id="buyer-001",
        memory_key="preferred_delivery_window",
        value="工作日上午",
        confidence=0.92,
        proposed_by="preference_detector_v1",
        source_hash="same-source-hash",
    )

    assert second.record_id == first.record_id
    assert service.list_records(
        tenant_id=first.tenant_id,
        user_id=first.user_id,
        status="candidate",
    ) == (first,)


def test_safe_preference_detector_only_proposes_whitelisted_memory() -> None:
    candidates = detect_preference_candidates(
        "请采购 DEMO-HYD-PUMP-001 两件。以后送货请安排在工作日上午,"
        "供应商优先比较总成本。我的 api_key 是 should-not-store。"
    )

    assert {(item.memory_key, item.value) for item in candidates} == {
        ("preferred_delivery_window", "工作日上午"),
        ("preferred_supplier_strategy", "总成本"),
    }
