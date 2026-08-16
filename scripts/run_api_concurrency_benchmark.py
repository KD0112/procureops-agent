"""Measure API retrieval concurrency, cache behavior, and error rate locally."""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from procureops.api import create_app  # noqa: E402

HEADERS = {
    "X-Tenant-Id": "tenant_commerce_ops",
    "X-Actor-Id": "benchmark-runner",
    "X-Actor-Roles": "procurement_operator",
}
QUERIES = ("退货率", "区域销售额", "商品销售额", "退款政策")


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * quantile))
    return round(ordered[index], 3)


async def run(*, requests: int, concurrency: int) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="procureops-api-bench-") as directory:
        root = Path(directory)
        app = create_app(
            project_root=PROJECT_ROOT,
            database_path=root / "api.sqlite3",
            var_root=root / "var",
            allow_header_auth=True,
        )
        app.state.rate_limiter.limit = requests + 10
        semaphore = asyncio.Semaphore(concurrency)

        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://benchmark.local",
                timeout=30,
            ) as client:

                async def one(index: int) -> dict[str, object]:
                    async with semaphore:
                        query = QUERIES[index % len(QUERIES)]
                        started = time.perf_counter()
                        response = await client.post(
                            "/api/search",
                            headers=HEADERS,
                            json={"query": query, "top_k": 4},
                        )
                        elapsed = (time.perf_counter() - started) * 1000
                        payload = response.json()
                        return {
                            "status_code": response.status_code,
                            "latency_ms": elapsed,
                            "cache": payload.get("cache"),
                            "error_code": payload.get("error_code"),
                        }

                results = await asyncio.gather(*(one(index) for index in range(requests)))
        finally:
            app.state.runtime.commerce.close()
    latencies = [float(item["latency_ms"]) for item in results]
    successes = [item for item in results if item["status_code"] == 200]
    return {
        "profile": "offline_fastapi_asgi",
        "requests": requests,
        "concurrency": concurrency,
        "success_rate": round(len(successes) / requests, 4) if requests else 0.0,
        "error_count": requests - len(successes),
        "cache_hit_rate": round(
            sum(item["cache"] == "hit" for item in successes) / len(successes), 4
        )
        if successes
        else 0.0,
        "latency_ms": {
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "max": round(max(latencies, default=0.0), 3),
            "mean": round(statistics.fmean(latencies), 3) if latencies else 0.0,
        },
        "limitations": [
            "ASGI in-process benchmark; it does not measure network, TLS, or multi-worker effects.",
            (
                "Use Docker Redis/MySQL and a representative load profile "
                "before making production claims."
            ),
        ],
    }


def main() -> None:
    payload = asyncio.run(run(requests=24, concurrency=6))
    destination = PROJECT_ROOT / "reports" / "latest_api_concurrency_benchmark.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"wrote API concurrency benchmark to {destination}")


if __name__ == "__main__":
    main()
