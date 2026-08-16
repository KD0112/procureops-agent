from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class QueueMessage:
    message_id: str
    stream: str
    payload: dict[str, Any]
    consumer: str | None = None


class InMemoryStreamQueue:
    """Offline queue that mirrors publish/claim/ack/retry semantics."""

    backend = "memory-stream"

    def __init__(self) -> None:
        self._items: list[QueueMessage] = []
        self._pending: dict[str, QueueMessage] = {}
        self._sequence = 0
        self._lock = asyncio.Lock()

    async def publish(self, *, stream: str, payload: dict[str, Any]) -> str:
        async with self._lock:
            self._sequence += 1
            message_id = f"{int(datetime.now(UTC).timestamp() * 1000)}-{self._sequence}"
            self._items.append(QueueMessage(message_id, stream, dict(payload)))
            return message_id

    async def claim(self, *, stream: str, consumer: str) -> QueueMessage | None:
        async with self._lock:
            for index, item in enumerate(self._items):
                if item.stream != stream:
                    continue
                claimed = QueueMessage(item.message_id, item.stream, item.payload, consumer)
                self._items.pop(index)
                self._pending[claimed.message_id] = claimed
                return claimed
        return None

    async def ack(self, *, message_id: str, consumer: str) -> None:
        async with self._lock:
            item = self._pending.get(message_id)
            if item is None or item.consumer != consumer:
                raise RuntimeError("consumer does not own message")
            self._pending.pop(message_id)

    async def retry(self, *, message: QueueMessage, error: str, dead_letter: bool = False) -> str:
        async with self._lock:
            self._pending.pop(message.message_id, None)
        payload = dict(message.payload)
        payload["_last_error"] = error[:500]
        if dead_letter:
            return await self.publish(stream=f"{message.stream}:dlq", payload=payload)
        return await self.publish(stream=message.stream, payload=payload)

    async def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "backend": self.backend,
            "pending": len(self._pending),
            "queued": len(self._items),
        }


class RedisStreamsQueue:
    backend = "redis-streams"

    def __init__(self, url: str, *, group: str = "procureops-workers") -> None:
        try:
            import redis.asyncio as redis  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("redis package is required for Redis Streams") from exc
        self._client = redis.from_url(url, decode_responses=True)
        self.group = group

    async def ensure_group(self, *, stream: str) -> None:
        try:
            await self._client.xgroup_create(stream, self.group, id="0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def publish(self, *, stream: str, payload: dict[str, Any]) -> str:
        await self.ensure_group(stream=stream)
        return str(await self._client.xadd(stream, {"payload": _encode(payload)}))

    async def claim(self, *, stream: str, consumer: str) -> QueueMessage | None:
        await self.ensure_group(stream=stream)
        response = await self._client.xreadgroup(
            self.group,
            consumer,
            {stream: ">"},
            count=1,
            block=1,
        )
        if not response:
            return None
        _, messages = response[0]
        message_id, fields = messages[0]
        return QueueMessage(str(message_id), stream, _decode(fields.get("payload", "")), consumer)

    async def ack(self, *, message_id: str, consumer: str, stream: str) -> None:
        del consumer
        await self._client.xack(stream, self.group, message_id)

    async def retry(self, *, message: QueueMessage, error: str, dead_letter: bool = False) -> str:
        payload = dict(message.payload)
        payload["_last_error"] = error[:500]
        return await self.publish(
            stream=f"{message.stream}:dlq" if dead_letter else message.stream,
            payload=payload,
        )

    async def health(self) -> dict[str, Any]:
        await self._client.ping()
        return {"status": "ok", "backend": self.backend, "group": self.group}

    async def close(self) -> None:
        await self._client.aclose()

    @classmethod
    def from_environment(cls) -> InMemoryStreamQueue | RedisStreamsQueue:
        url = os.getenv("PROCUREOPS_REDIS_URL", "").strip()
        return cls(url) if url else InMemoryStreamQueue()


def _encode(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _decode(payload: str) -> dict[str, Any]:
    import json

    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("stream payload must be a JSON object")
    return value
