from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from procureops.domain.models import RunContext


class AuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    event_type: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    run_id: str
    task_id: str
    tenant_id: str
    actor_id: str
    correlation_id: str
    payload_hash: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_context(
        cls,
        context: RunContext,
        event_type: str,
        *,
        payload_hash: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AuditEvent:
        return cls(
            event_type=event_type,
            run_id=context.run_id,
            task_id=context.task_id,
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            correlation_id=context.correlation_id,
            payload_hash=payload_hash,
            metadata=metadata or {},
        )


class AuditSink(Protocol):
    def append(self, event: AuditEvent) -> None: ...


class InMemoryAuditSink:
    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._lock = threading.Lock()

    def append(self, event: AuditEvent) -> None:
        with self._lock:
            self._events.append(event)

    def events(self) -> tuple[AuditEvent, ...]:
        with self._lock:
            return tuple(self._events)


class JsonlAuditSink:
    """Append-only local audit sink. Production storage will enforce DB constraints."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, event: AuditEvent) -> None:
        serialized = json.dumps(event.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        with self._lock, self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized + "\n")

