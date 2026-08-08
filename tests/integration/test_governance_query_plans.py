from procureops.evolution import EvolutionService
from procureops.storage import ProcureOpsRepository


def test_memory_and_feedback_workbench_queries_use_tenant_indexes(
    repository: ProcureOpsRepository,
) -> None:
    EvolutionService(repository.database).bootstrap_baseline(
        tenant_id="tenant_engineering_machinery"
    )
    memory_plan = repository.database.explain_query_plan(
        """
        SELECT record_id FROM memory_records
        WHERE tenant_id=? AND user_id=? AND status='confirmed' AND expires_at>?
        ORDER BY confirmed_at
        """,
        ("tenant_engineering_machinery", "buyer-001", "2026-01-01T00:00:00+00:00"),
    )
    feedback_plan = repository.database.explain_query_plan(
        """
        SELECT feedback_id FROM user_feedback
        WHERE tenant_id=? AND status=? ORDER BY created_at
        """,
        ("tenant_engineering_machinery", "open"),
    )

    assert any("idx_memory_active" in detail for detail in memory_plan)
    assert any("idx_feedback_workbench" in detail for detail in feedback_plan)
