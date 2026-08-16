from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol


class AsyncCache(Protocol):
    backend: str

    async def get(self, key: str) -> Any | None: ...

    async def set(self, key: str, value: Any, *, ttl_seconds: int) -> None: ...

    async def delete(self, key: str) -> None: ...

    async def incr(self, key: str, *, ttl_seconds: int) -> int: ...

    async def health(self) -> dict[str, Any]: ...


class InMemoryAsyncCache:
    """Deterministic fallback for tests and offline development."""

    backend = "memory"

    def __init__(self) -> None:
        self._items: dict[str, tuple[float, Any]] = {}

    async def get(self, key: str) -> Any | None:
        item = self._items.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at <= time.monotonic():
            self._items.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: Any, *, ttl_seconds: int) -> None:
        if ttl_seconds < 1:
            raise ValueError("ttl_seconds must be positive")
        self._items[key] = (time.monotonic() + ttl_seconds, value)

    async def delete(self, key: str) -> None:
        self._items.pop(key, None)

    async def incr(self, key: str, *, ttl_seconds: int) -> int:
        now = time.monotonic()
        item = self._items.get(key)
        if item is None or item[0] <= now:
            self._items[key] = (now + ttl_seconds, 1)
            return 1
        expires_at, current = item
        value = int(current) + 1
        self._items[key] = (expires_at, value)
        return value

    async def health(self) -> dict[str, Any]:
        return {"status": "ok", "backend": self.backend, "keys": len(self._items)}


class RedisAsyncCache:
    """Redis adapter with JSON values and explicit tenant-aware key helpers."""

    backend = "redis"

    def __init__(self, url: str, *, namespace: str = "procureops") -> None:
        try:
            import redis.asyncio as redis  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("redis package is required for REDIS_URL") from exc
        self._client = redis.from_url(url, decode_responses=True)
        self.namespace = namespace

    def _key(self, key: str) -> str:
        return f"{self.namespace}:{key}"

    async def get(self, key: str) -> Any | None:
        raw = await self._client.get(self._key(key))
        return None if raw is None else json.loads(raw)

    async def set(self, key: str, value: Any, *, ttl_seconds: int) -> None:
        if ttl_seconds < 1:
            raise ValueError("ttl_seconds must be positive")
        await self._client.set(
            self._key(key), json.dumps(value, ensure_ascii=False), ex=ttl_seconds
        )

    async def delete(self, key: str) -> None:
        await self._client.delete(self._key(key))

    async def incr(self, key: str, *, ttl_seconds: int) -> int:
        full_key = self._key(key)
        value = int(await self._client.incr(full_key))
        if value == 1:
            await self._client.expire(full_key, ttl_seconds)
        return value

    async def health(self) -> dict[str, Any]:
        await self._client.ping()
        return {"status": "ok", "backend": self.backend}

    async def close(self) -> None:
        await self._client.aclose()

    @classmethod
    def from_environment(cls) -> AsyncCache:
        url = os.getenv("PROCUREOPS_REDIS_URL", "").strip()
        if not url:
            return InMemoryAsyncCache()
        return cls(url)


def tenant_key(tenant_id: str, category: str, identifier: str) -> str:
    if not tenant_id or not category or not identifier:
        raise ValueError("tenant_id, category and identifier are required")
    return f"tenant:{tenant_id}:{category}:{identifier}"


class SessionStore:
    def __init__(self, cache: AsyncCache, *, ttl_seconds: int = 1800) -> None:
        self.cache = cache
        self.ttl_seconds = ttl_seconds

    async def get(self, *, tenant_id: str, session_id: str) -> dict[str, Any] | None:
        value = await self.cache.get(tenant_key(tenant_id, "session", session_id))
        return dict(value) if isinstance(value, Mapping) else None

    async def put(self, *, tenant_id: str, session_id: str, value: Mapping[str, Any]) -> None:
        await self.cache.set(
            tenant_key(tenant_id, "session", session_id),
            dict(value),
            ttl_seconds=self.ttl_seconds,
        )


class ToolResultCache:
    def __init__(self, cache: AsyncCache, *, ttl_seconds: int = 120) -> None:
        self.cache = cache
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def key(*, tenant_id: str, tool_name: str, arguments: Mapping[str, Any]) -> str:
        payload = json.dumps(dict(arguments), ensure_ascii=False, sort_keys=True, default=str)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
        return tenant_key(tenant_id, "tool", f"{tool_name}:{digest}")

    async def get(
        self, *, tenant_id: str, tool_name: str, arguments: Mapping[str, Any]
    ) -> Any | None:
        return await self.cache.get(
            self.key(tenant_id=tenant_id, tool_name=tool_name, arguments=arguments)
        )

    async def put(
        self, *, tenant_id: str, tool_name: str, arguments: Mapping[str, Any], value: Any
    ) -> None:
        await self.cache.set(
            self.key(tenant_id=tenant_id, tool_name=tool_name, arguments=arguments),
            value,
            ttl_seconds=self.ttl_seconds,
        )


@dataclass(slots=True)
class RateLimiter:
    cache: AsyncCache
    limit: int = 60
    window_seconds: int = 60

    async def allow(self, *, tenant_id: str, actor_id: str) -> bool:
        key = tenant_key(tenant_id, "rate", actor_id)
        return await self.cache.incr(key, ttl_seconds=self.window_seconds) <= self.limit
