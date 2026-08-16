"""Consume Redis Streams document-ingestion jobs."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from procureops.infrastructure.streams import RedisStreamsQueue  # noqa: E402
from procureops.runtime import ProcureOpsRuntime  # noqa: E402
from procureops.worker.queue import Job  # noqa: E402
from procureops.worker.service import ProcureOpsWorker  # noqa: E402


async def run(*, loop: bool, poll_seconds: float, consumer: str) -> None:
    queue = RedisStreamsQueue.from_environment()
    if queue.backend != "redis-streams":
        raise RuntimeError("set PROCUREOPS_REDIS_URL before running the Redis Streams worker")
    runtime = ProcureOpsRuntime.create(project_root=PROJECT_ROOT)
    worker = ProcureOpsWorker(runtime=runtime, worker_id=consumer)
    try:
        while True:
            message = await queue.claim(stream="rag:ingest", consumer=consumer)
            if message is None:
                if not loop:
                    return
                await asyncio.sleep(max(0.1, poll_seconds))
                continue
            payload = message.payload
            job = Job(
                job_id=message.message_id,
                tenant_id=str(payload["tenant_id"]),
                task_id=str(payload["task_id"]),
                job_type=str(payload["job_type"]),
                payload=dict(payload["job_payload"]),
                status="leased",
                attempts=1,
                max_attempts=int(payload.get("max_attempts", 3)),
                lease_owner=consumer,
            )
            try:
                outcome = worker._process_rag_ingest(
                    job,
                    context=runtime.context(
                        tenant_id=job.tenant_id,
                        task_id=job.task_id,
                        actor_id=str(job.payload.get("actor_id", consumer)),
                        actor_roles=frozenset(job.payload.get("actor_roles", [])),
                        run_id=str(job.payload.get("run_id", job.job_id)),
                        correlation_id=job.job_id,
                    ),
                )
                await queue.ack(
                    stream="rag:ingest",
                    message_id=message.message_id,
                    consumer=consumer,
                )
                print(json.dumps({"message_id": message.message_id, **outcome}, ensure_ascii=False))
            except Exception as exc:
                await queue.retry(message=message, error=str(exc), dead_letter=True)
                await queue.ack(
                    stream="rag:ingest",
                    message_id=message.message_id,
                    consumer=consumer,
                )
                print(json.dumps({"message_id": message.message_id, "error": type(exc).__name__}))
            if not loop:
                return
    finally:
        await queue.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--consumer", default="rag-worker-1")
    args = parser.parse_args()
    asyncio.run(run(loop=args.loop, poll_seconds=args.poll_seconds, consumer=args.consumer))


if __name__ == "__main__":
    main()
