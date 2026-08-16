"""Latency decomposition shared by local and live model benchmarks."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

from procureops.evals.quality import latency_summary


@dataclass(frozen=True, slots=True)
class LatencySample:
    case_id: str
    queue_ms: float = 0.0
    memory_ms: float = 0.0
    retrieval_ms: float = 0.0
    tool_ms: float = 0.0
    model_prefill_ms: float = 0.0
    model_decode_ms: float = 0.0
    total_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def summarize_latency(samples: Iterable[LatencySample]) -> dict[str, Any]:
    rows = tuple(sample.as_dict() for sample in samples)
    result = latency_summary(rows)
    for field in (
        "queue_ms",
        "memory_ms",
        "retrieval_ms",
        "tool_ms",
        "model_prefill_ms",
        "model_decode_ms",
    ):
        values = [float(row[field]) for row in rows]
        result[f"{field}_average"] = round(sum(values) / len(values), 3) if values else 0.0
    result["input_tokens"] = sum(int(row["input_tokens"]) for row in rows)
    result["output_tokens"] = sum(int(row["output_tokens"]) for row in rows)
    return result
