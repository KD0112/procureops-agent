from pathlib import Path

from procureops.evals.dataset import generate_extended_cases, load_cases
from procureops.evals.quality import dataset_summary, report_metrics

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_extended_quality_dataset_has_locked_shape() -> None:
    cases = generate_extended_cases()
    summary = dataset_summary(cases)
    assert len(cases) == 200
    assert summary["dataset_versions"] == ["3.0.0"]
    assert summary["splits"] == {"development": 120, "holdout": 20, "regression": 60}
    assert summary["categories"]["memory_regression"] == 25
    assert summary["categories"]["rag_noise"] == 20
    assert summary["fields"]["expected_tools"] == 35


def test_extended_dataset_artifact_matches_generator() -> None:
    loaded = load_cases(PROJECT_ROOT / "data" / "evals" / "agent_quality_v3.jsonl")
    assert loaded == generate_extended_cases()


def test_report_metrics_normalizes_existing_report_shape() -> None:
    metrics = report_metrics(
        {
            "dataset_size": 2,
            "pass_rate": 0.5,
            "safety_pass_rate": 1.0,
            "completed_evidence_coverage": 0.75,
            "latency_p50_ms": 10,
            "latency_p95_ms": 20,
            "average_tool_calls": 3,
            "total_model_calls": 4,
            "estimated_total_cost_usd": 0.1,
        }
    )
    assert metrics["task_success_rate"] == 0.5
    assert metrics["latency_p95_ms"] == 20.0
