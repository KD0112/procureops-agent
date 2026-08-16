from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Mapping
from typing import Any


class AsyncToolExecutor:
    """Run independent read-only tools concurrently without blocking FastAPI."""

    def __init__(self, *, max_concurrency: int = 4, timeout_seconds: float = 15.0) -> None:
        if max_concurrency < 1 or timeout_seconds <= 0:
            raise ValueError("invalid async executor limits")
        self.max_concurrency = max_concurrency
        self.timeout_seconds = timeout_seconds

    async def gather(
        self,
        calls: Mapping[str, Callable[[], Any]],
        *,
        fail_fast: bool = False,
    ) -> dict[str, Any]:
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def run_one(name: str, call: Callable[[], Any]) -> tuple[str, Any]:
            async with semaphore:
                async with asyncio.timeout(self.timeout_seconds):
                    if inspect.iscoroutinefunction(call):
                        value = await call()
                    else:
                        value = await asyncio.to_thread(call)
                    if inspect.isawaitable(value):
                        value = await value
                    return name, value

        tasks = [asyncio.create_task(run_one(name, call)) for name, call in calls.items()]
        results = await asyncio.gather(*tasks, return_exceptions=not fail_fast)
        output: dict[str, Any] = {}
        for name, result in zip(calls, results, strict=True):
            if isinstance(result, Exception):
                output[name] = {"ok": False, "error": type(result).__name__}
            else:
                _, value = result
                output[name] = {"ok": True, "value": value}
        return output
