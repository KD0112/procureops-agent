"""Send one privacy-safe real trace to Langfuse when credentials are configured."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from procureops.config import load_environment  # noqa: E402
from procureops.observability import LangfuseSettings, LangfuseTracer  # noqa: E402

OUTPUT = PROJECT_ROOT / "reports" / "latest_langfuse_trace.json"


def write_report(payload: dict[str, object]) -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    load_environment(PROJECT_ROOT)
    settings = LangfuseSettings.from_environment()
    base = {
        "run_at": datetime.now(UTC).isoformat(),
        "environment": settings.environment,
        "release": settings.release,
        "base_url": settings.base_url,
        "capture_io": settings.capture_io,
        "credentials_present": bool(settings.public_key and settings.secret_key),
        "required_environment": [
            "LANGFUSE_ENABLED=1",
            "LANGFUSE_PUBLIC_KEY",
            "LANGFUSE_SECRET_KEY",
        ],
    }
    if not settings.configured:
        payload = {
            **base,
            "status": "BLOCKED_MISSING_CREDENTIALS",
            "trace_sent": False,
            "message": "Langfuse credentials are not configured; no trace was fabricated.",
        }
        write_report(payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2
    tracer = LangfuseTracer(settings)
    if not tracer.available:
        payload = {
            **base,
            "status": "BLOCKED_SDK_UNAVAILABLE",
            "trace_sent": False,
            "message": "Langfuse is configured but the SDK is unavailable.",
        }
        write_report(payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2
    with tracer.observe(
        name="smoke.commerce_ops",
        as_type="agent",
        input={"query": "退款政策与退货率"},
        metadata={
            "tenant_id": "tenant_commerce_ops",
            "source": "langfuse_trace_smoke",
            "dataset_version": "commerce-demo-v1",
        },
    ) as observation:
        observation.update(
            output={
                "status": "evidence_checked",
                "writes": "disabled",
                "citation_count": 1,
            }
        )
        observation.score(
            name="smoke.evidence_gate",
            value=1.0,
            comment="Prefetch/evidence gate completed without capturing raw business input.",
        )
    tracer.flush()
    payload = {
        **base,
        "status": "SENT_TO_LANGFUSE_SDK",
        "trace_sent": True,
        "verification": "SDK flush completed; verify the trace in the configured Langfuse project.",
    }
    write_report(payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
