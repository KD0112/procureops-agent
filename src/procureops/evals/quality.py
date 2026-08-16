"""Deterministic quality, cost, and dataset metrics for repeatable benchmarks."""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from procureops.evals.models import EvalCase, EvalReport


def _value(report: EvalReport | Mapping[str, Any], key: str, default: Any = 0) -> Any:
    if isinstance(report, Mapping):
        return report.get(key, default)
    return getattr(report, key, default)


def dataset_summary(cases: Iterable[EvalCase]) -> dict[str, Any]:
    selected = tuple(cases)
    categories = Counter(case.category for case in selected)
    splits = Counter(case.split for case in selected)
    tags = Counter(tag for case in selected for tag in case.tags)
    return {
        "dataset_size": len(selected),
        "dataset_versions": sorted({case.dataset_version for case in selected}),
        "categories": dict(sorted(categories.items())),
        "splits": dict(sorted(splits.items())),
        "top_tags": dict(tags.most_common()),
        "fields": {
            "expected_tools": sum(bool(case.expected_tools) for case in selected),
            "forbidden_tools": sum(bool(case.forbidden_tools) for case in selected),
            "metadata": sum(bool(case.metadata) for case in selected),
            "retrieval_context": sum(bool(case.retrieval_context) for case in selected),
        },
    }


def report_metrics(report: EvalReport | Mapping[str, Any]) -> dict[str, float | int]:
    """Normalize the existing harness report to interview-friendly KPIs."""
    return {
        "dataset_size": int(_value(report, "dataset_size")),
        "task_success_rate": float(_value(report, "pass_rate")),
        "safety_pass_rate": float(_value(report, "safety_pass_rate")),
        "evidence_coverage": float(_value(report, "completed_evidence_coverage")),
        "latency_p50_ms": float(_value(report, "latency_p50_ms")),
        "latency_p95_ms": float(_value(report, "latency_p95_ms")),
        "average_tool_calls": float(_value(report, "average_tool_calls")),
        "total_model_calls": int(_value(report, "total_model_calls")),
        "estimated_total_cost_usd": float(_value(report, "estimated_total_cost_usd")),
    }


def compare_metrics(
    baseline: EvalReport | Mapping[str, Any], optimized: EvalReport | Mapping[str, Any]
) -> dict[str, dict[str, float | int]]:
    left = report_metrics(baseline)
    right = report_metrics(optimized)
    result: dict[str, dict[str, float | int]] = {}
    for key, before in left.items():
        after = right[key]
        delta = after - before
        relative = delta / before if isinstance(before, (int, float)) and before else 0.0
        result[key] = {
            "baseline": before,
            "optimized": after,
            "delta": round(delta, 6),
            "relative_delta": round(relative, 6),
        }
    return result


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    index = max(0, math.ceil(len(values) * quantile) - 1)
    return values[index]


def latency_summary(samples: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = tuple(samples)
    total_values = sorted(float(row.get("total_ms", 0)) for row in rows)
    return {
        "sample_count": len(rows),
        "total_ms_p50": round(_percentile(total_values, 0.50), 3),
        "total_ms_p95": round(_percentile(total_values, 0.95), 3),
        "total_ms_p99": round(_percentile(total_values, 0.99), 3),
        "average_total_ms": round(sum(total_values) / len(total_values), 3)
        if total_values
        else 0.0,
        "cache_hit_rate": round(
            sum(bool(row.get("cache_hit")) for row in rows) / len(rows), 6
        )
        if rows
        else 0.0,
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(serialized, encoding="utf-8")
