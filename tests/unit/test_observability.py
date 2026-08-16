from procureops.observability import LangfuseSettings, LangfuseTracer
from procureops.observability.langfuse import _safe_value


def test_langfuse_is_disabled_without_explicit_enablement() -> None:
    tracer = LangfuseTracer(LangfuseSettings())
    assert tracer.available is False
    with tracer.observe(name="test", input={"query": "private"}) as observation:
        observation.update(output={"ok": True})


def test_telemetry_redacts_secrets_and_hashes_io_by_default() -> None:
    value = _safe_value(
        {"api_key": "secret", "query": "customer-specific request"},
        capture_io=False,
    )
    assert value["api_key"] == "[REDACTED]"
    assert value["query"]["type"] == "redacted"
    assert "customer-specific request" not in str(value)
