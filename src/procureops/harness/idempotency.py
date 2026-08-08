from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from procureops.harness.errors import IdempotencyConflict

T = TypeVar("T")


@dataclass(frozen=True)
class IdempotencyRecord:
    request_hash: str
    result: Any


class InMemoryIdempotencyStore:
    """Atomic single-process reference implementation used by unit tests."""

    def __init__(self) -> None:
        self._records: dict[str, IdempotencyRecord] = {}
        self._in_progress: dict[str, str] = {}
        self._lock = threading.Lock()

    def execute_once(
        self,
        *,
        key: str,
        request_hash: str,
        operation: Callable[[], T],
    ) -> tuple[T, bool]:
        if not key:
            raise ValueError("idempotency key is required")
        with self._lock:
            existing = self._records.get(key)
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise IdempotencyConflict("idempotency key reused with different request")
                return existing.result, True
            pending_hash = self._in_progress.get(key)
            if pending_hash is not None:
                if pending_hash != request_hash:
                    raise IdempotencyConflict("idempotency key is in progress for another request")
                raise IdempotencyConflict("identical request is already in progress")
            self._in_progress[key] = request_hash

        try:
            result = operation()
        except Exception:
            with self._lock:
                self._in_progress.pop(key, None)
            raise

        with self._lock:
            self._records[key] = IdempotencyRecord(request_hash=request_hash, result=result)
            self._in_progress.pop(key, None)
        return result, False

