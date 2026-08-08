from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from procureops.demo import seed_demo_database
from procureops.domain.models import RunBudget, RunContext
from procureops.storage import ProcureOpsRepository, SQLiteDatabase

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def run_context() -> RunContext:
    return RunContext(
        run_id="run-001",
        task_id="task-001",
        tenant_id="tenant_engineering_machinery",
        actor_id="buyer-001",
        actor_roles=frozenset({"procurement_operator"}),
        workflow_version="1.0.0",
        prompt_version="1.0.0",
        model_policy_version="1.0.0",
        rule_set_version="1.0.0",
        tenant_pack_version="1.0.0",
        deadline_at=datetime.now(UTC) + timedelta(minutes=10),
        budget=RunBudget(
            max_model_calls=2,
            max_tool_calls=5,
            max_tokens=100,
            max_cost_usd=0.1,
        ),
        correlation_id="corr-001",
    )


@pytest.fixture
def repository(tmp_path: Path) -> ProcureOpsRepository:
    database = SQLiteDatabase(tmp_path / "procureops.sqlite3")
    return seed_demo_database(database, project_root=PROJECT_ROOT)
