"""Run the real MySQL/Redis/FastAPI infrastructure smoke test.

Prerequisites:
    docker compose -f docker-compose.infra.yml up -d
    copy .env.example .env and set PROCUREOPS_QUEUE_BACKEND=redis-streams
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from procureops.api import create_app  # noqa: E402
from procureops.config import load_environment  # noqa: E402
from procureops.infrastructure import RedisAsyncCache, RedisStreamsQueue  # noqa: E402
from procureops.storage import MySQLBusinessRepository, MySQLSettings  # noqa: E402


async def check_mysql() -> dict[str, object]:
    settings = MySQLSettings.from_environment()
    if settings is None:
        raise RuntimeError("PROCUREOPS_MYSQL_URL is required")
    repository = MySQLBusinessRepository(settings)
    try:
        await repository.init_schema()
        await repository.seed_demo_catalog()
        health = await repository.health()
        rows = await repository.search_catalog(tenant_id="demo-tenant", query="Bearing")
        if not rows:
            raise AssertionError("MySQL JOIN catalog query returned no rows")
        task_id = f"smoke-{uuid.uuid4().hex[:12]}"
        await repository.create_task_with_outbox(
            tenant_id="demo-tenant",
            task_id=task_id,
            actor_id="smoke-runner",
            request={"source": "smoke_infra"},
        )
        return {"health": health, "join_rows": len(rows), "transaction_task_id": task_id}
    finally:
        await repository.close()


async def check_redis() -> dict[str, object]:
    cache = RedisAsyncCache.from_environment()
    if not isinstance(cache, RedisAsyncCache):
        raise RuntimeError("PROCUREOPS_REDIS_URL is required for the real Redis smoke test")
    queue = RedisStreamsQueue.from_environment()
    if queue is None:
        raise RuntimeError("PROCUREOPS_REDIS_URL is required for Redis Streams")
    stream = "procureops:smoke"
    consumer = f"smoke-{uuid.uuid4().hex[:8]}"
    message_id = await queue.publish(stream=stream, payload={"kind": "smoke"})
    claimed = await queue.claim(stream=stream, consumer=consumer)
    if claimed is None or claimed.payload.get("kind") != "smoke":
        raise AssertionError("Redis Streams message was not claimed")
    await queue.ack(stream=stream, message_id=claimed.message_id, consumer=consumer)
    await cache.set("smoke:ttl", {"ok": True}, ttl_seconds=30)
    cached = await cache.get("smoke:ttl")
    await queue.close()
    await cache.close()
    return {
        "cache": cached,
        "stream_message_id": claimed.message_id,
        "published_message_id": message_id,
    }


def check_api() -> dict[str, object]:
    app = create_app(project_root=PROJECT_ROOT)
    with TestClient(app) as client:
        response = client.get("/api/readiness")
        response.raise_for_status()
        payload = response.json()
    if payload.get("status") != "ok":
        raise AssertionError(f"API is not ready: {payload}")
    return payload


async def main() -> None:
    load_environment(PROJECT_ROOT)
    mysql = await check_mysql()
    redis = await check_redis()
    api = check_api()
    print({"mysql": mysql, "redis": redis, "api": api})
    print("enterprise infrastructure smoke: PASS")


if __name__ == "__main__":
    asyncio.run(main())
