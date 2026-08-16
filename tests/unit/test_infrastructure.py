import asyncio
from time import monotonic

from procureops.harness.async_execution import AsyncToolExecutor
from procureops.infrastructure.cache import (
    InMemoryAsyncCache,
    RateLimiter,
    SessionStore,
    ToolResultCache,
)
from procureops.infrastructure.streams import InMemoryStreamQueue
from procureops.skills import ProcurementEvidenceSkill, SkillRegistry


def test_cache_tenant_isolation_session_ttl_and_rate_limit() -> None:
    async def scenario() -> None:
        cache = InMemoryAsyncCache()
        sessions = SessionStore(cache, ttl_seconds=10)
        tool_cache = ToolResultCache(cache, ttl_seconds=10)
        await sessions.put(tenant_id="tenant-a", session_id="s1", value={"value": "a"})
        assert await sessions.get(tenant_id="tenant-a", session_id="s1") == {"value": "a"}
        assert await sessions.get(tenant_id="tenant-b", session_id="s1") is None
        arguments = {"query": "pump"}
        await tool_cache.put(
            tenant_id="tenant-a", tool_name="catalog", arguments=arguments, value=[1]
        )
        assert (
            await tool_cache.get(tenant_id="tenant-b", tool_name="catalog", arguments=arguments)
            is None
        )

        limiter = RateLimiter(cache, limit=2, window_seconds=10)
        assert await limiter.allow(tenant_id="tenant-a", actor_id="buyer")
        assert await limiter.allow(tenant_id="tenant-a", actor_id="buyer")
        assert not await limiter.allow(tenant_id="tenant-a", actor_id="buyer")

    asyncio.run(scenario())


def test_stream_queue_claim_ack_and_dead_letter() -> None:
    async def scenario() -> None:
        queue = InMemoryStreamQueue()
        message_id = await queue.publish(stream="rag:ingest", payload={"task_id": "t1"})
        message = await queue.claim(stream="rag:ingest", consumer="worker-1")
        assert message is not None and message.message_id == message_id
        await queue.ack(message_id=message_id, consumer="worker-1")
        retry_id = await queue.retry(message=message, error="parse failed", dead_letter=True)
        assert retry_id
        health = await queue.health()
        assert health["queued"] == 1

    asyncio.run(scenario())


def test_async_executor_runs_independent_calls_and_handles_errors() -> None:
    async def scenario() -> None:
        started = monotonic()

        async def slow(value: str) -> str:
            await asyncio.sleep(0.05)
            return value

        result = await AsyncToolExecutor(timeout_seconds=1).gather(
            {"a": lambda: slow("a"), "b": lambda: slow("b")}
        )
        assert monotonic() - started < 0.15
        assert result["a"] == {"ok": True, "value": "a"}

    asyncio.run(scenario())


def test_procurement_skill_registry_returns_structured_evidence() -> None:
    async def scenario() -> None:
        async def catalog_lookup(**_: object) -> list[dict[str, object]]:
            return [{"product_id": "p1", "name": "pump"}]

        async def supplier_lookup(**_: object) -> list[dict[str, object]]:
            return [{"supplier_id": "s1", "approved": True}]

        async def logistics_quote(**_: object) -> list[dict[str, object]]:
            return [{"quote_id": "l1", "fee": "20"}]

        registry = SkillRegistry()
        registry.register(
            "procurement_evidence",
            ProcurementEvidenceSkill(catalog_lookup, supplier_lookup, logistics_quote),
        )
        result = await registry.execute(
            "procurement_evidence", tenant_id="tenant-a", query="pump", quantity="2"
        )
        assert result.status == "matched"
        assert result.evidence_count == 3

    asyncio.run(scenario())
