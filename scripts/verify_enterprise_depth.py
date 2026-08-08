"""Verify migrations, SQLite integrity, and index-backed enterprise queries."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from procureops.runtime import ProcureOpsRuntime  # noqa: E402


def main() -> None:
    runtime = ProcureOpsRuntime.create(project_root=PROJECT_ROOT)
    database = runtime.database
    plans = {
        "logistics_lookup": database.explain_query_plan(
            "SELECT * FROM logistics_quotes WHERE tenant_id=? AND product_id=? "
            "AND valid_until>? AND supplier_id IN (?, ?)",
            ("tenant", "product", "2026-01-01", "supplier-a", "supplier-b"),
        ),
        "active_memory": database.explain_query_plan(
            "SELECT record_id FROM memory_records WHERE tenant_id=? AND user_id=? "
            "AND status='confirmed' AND expires_at>? ORDER BY confirmed_at",
            ("tenant", "user", "2026-01-01"),
        ),
        "auth_session": database.explain_query_plan(
            "SELECT * FROM auth_sessions WHERE token_hash=? AND expires_at>? "
            "AND revoked_at IS NULL",
            ("hash", "2026-01-01"),
        ),
        "outbox_dispatch": database.explain_query_plan(
            "SELECT event_id FROM outbox_events WHERE status IN "
            "('pending','dispatching') ORDER BY created_at, event_id LIMIT ?",
            (100,),
        ),
    }
    connection = database.connect()
    try:
        integrity_check = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_violations = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        migrations = [
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]
    finally:
        connection.close()
    database.optimize()
    required_index_signals = {
        "logistics_lookup": "idx_logistics_lookup",
        "active_memory": "idx_memory_active",
        "auth_session": "auth_sessions",
        "outbox_dispatch": "idx_outbox_dispatch",
    }
    index_checks = {
        name: signal in " ".join(details)
        for name, signal in required_index_signals.items()
        for details in (plans[name],)
    }
    report = {
        "suite": "enterprise_depth_sqlite_v1",
        "created_at": datetime.now(UTC).isoformat(),
        "database": "sqlite",
        "integrity_check": integrity_check,
        "foreign_key_violations": foreign_key_violations,
        "migrations": migrations,
        "query_plans": {name: list(details) for name, details in plans.items()},
        "index_checks": index_checks,
        "passed": (
            integrity_check == "ok"
            and foreign_key_violations == 0
            and all(index_checks.values())
            and migrations[-1:] == ["006_enterprise_depth"]
        ),
    }
    output = PROJECT_ROOT / "reports" / "latest_sqlite_verification.json"
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
