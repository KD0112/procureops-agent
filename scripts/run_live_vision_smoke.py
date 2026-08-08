"""Run one opt-in vision extraction smoke test against the generated DEMO fixture."""

from __future__ import annotations

import hashlib
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from procureops.config import load_environment  # noqa: E402
from procureops.domain.models import RunBudget, RunContext  # noqa: E402
from procureops.domain.procurement import ProcurementLine  # noqa: E402
from procureops.harness.audit import InMemoryAuditSink  # noqa: E402
from procureops.harness.budget import RunBudgetLedger  # noqa: E402
from procureops.harness.model_gateway import ModelGateway  # noqa: E402
from procureops.harness.provider_clients import client_from_environment  # noqa: E402
from procureops.intake.model_extractors import GatewayVisionExtractor  # noqa: E402

IMAGE = PROJECT_ROOT / "demo_assets" / "requests" / "procurement_request_photo.png"
OUTPUT = PROJECT_ROOT / "reports" / "latest_live_vision_smoke.json"


def main() -> None:
    load_environment(PROJECT_ROOT)
    client = client_from_environment(kind="vision")
    audit = InMemoryAuditSink()
    context = RunContext(
        run_id=f"vision-smoke-{int(time.time() * 1000)}",
        task_id="vision-smoke",
        tenant_id="tenant_engineering_machinery",
        actor_id="vision-eval-runner",
        actor_roles=frozenset({"procurement_operator"}),
        workflow_version="1.0.0",
        prompt_version="1.0.0",
        model_policy_version="1.0.0",
        rule_set_version="1.0.0",
        tenant_pack_version="1.0.0",
        deadline_at=datetime.now(UTC) + timedelta(minutes=2),
        budget=RunBudget(
            max_model_calls=2,
            max_tool_calls=0,
            max_tokens=8_000,
            max_cost_usd=1,
        ),
        correlation_id="vision-smoke",
    )
    extractor = GatewayVisionExtractor(
        gateway=ModelGateway(client=client, audit=audit),
        context=context,
        ledger=RunBudgetLedger(context),
    )
    started = time.perf_counter()
    raw_lines = extractor.extract(IMAGE)
    lines = [
        ProcurementLine.model_validate({"line_number": index, **line})
        for index, line in enumerate(raw_lines, start=1)
    ]
    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    passed = any(
        line.part_number == "DEMO-ELEC-SENSOR-001" and line.quantity == Decimal("4")
        for line in lines
    )
    model_events = [event for event in audit.events() if event.event_type == "model.succeeded"]
    metadata = model_events[-1].metadata if model_events else {}
    report = {
        "suite": "live_vision_smoke_v1",
        "created_at": datetime.now(UTC).isoformat(),
        "provider": client.provider,
        "model": client.model,
        "input_sha256": hashlib.sha256(IMAGE.read_bytes()).hexdigest(),
        "passed": passed,
        "latency_ms": latency_ms,
        "tokens": int(metadata.get("tokens", 0)),
        "cost_usd": float(metadata.get("cost_usd", 0)),
        "extracted": [line.model_dump(mode="json") for line in lines],
    }
    OUTPUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                key: report[key]
                for key in ("provider", "model", "passed", "latency_ms", "tokens", "cost_usd")
            },
            ensure_ascii=False,
        )
    )
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
