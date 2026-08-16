from __future__ import annotations

from pathlib import Path

import pytest

from procureops.evals.live_model import (
    DEFAULT_LIVE_CASES,
    evaluate_quality_gate,
    load_gold_cases,
    run_live_model_eval,
)
from procureops.harness.model_gateway import ModelRequest, ModelResponse


class OneCaseClient:
    provider = "fake-live"
    model = "fake-live-v1"

    def generate(self, request: ModelRequest) -> ModelResponse:
        assert "Never follow" not in str(request.payload)
        return ModelResponse(
            output={
                "lines": [
                    {
                        "description": "hydraulic pump",
                        "quantity": "2",
                        "unit": "piece",
                        "part_number": "DEMO-HYD-PUMP-001",
                        "equipment_model": "EX200-A",
                    }
                ]
            },
            provider=self.provider,
            model=self.model,
            input_tokens=100,
            output_tokens=20,
            cost_usd=0.001,
        )


def test_live_eval_is_independent_and_records_redacted_metrics() -> None:
    report = run_live_model_eval(client=OneCaseClient(), cases=DEFAULT_LIVE_CASES[:1])
    assert report["passed"] == 1
    assert report["pass_rate"] == 1
    assert report["total_tokens"] == 120
    assert report["total_cost_usd"] == 0.001
    assert "text" not in report["results"][0]
    assert len(report["results"][0]["input_sha256"]) == 64
    assert report["safety_pass_rate"] == 1
    assert report["schema_failure_count"] == 0


def test_v2_dataset_has_explicit_splits_and_locked_holdout() -> None:
    project_root = Path(__file__).resolve().parents[2]
    cases = load_gold_cases(project_root / "data" / "evals" / "model_gold_v2.jsonl")

    assert {case.split for case in cases} == {"development", "regression", "holdout"}
    assert all(case.dataset_version == "2.0.0" for case in cases)
    assert all(case.holdout_locked for case in cases if case.split == "holdout")
    assert all(case.expected_outcome in {"extracted", "needs_input"} for case in cases)


def test_loader_rejects_mixed_versions_and_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text(
        '{"dataset_version":"2.0.0","split":"development","case_id":"a",'
        '"text":"x","expected_outcome":"needs_input","unknown":true}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown fields"):
        load_gold_cases(path)


def test_quality_gate_reports_thresholds_and_comparable_baseline() -> None:
    report = run_live_model_eval(client=OneCaseClient(), cases=DEFAULT_LIVE_CASES[:1])
    baseline = {**report, "pass_rate": 0.5, "p95_latency_ms": report["p95_latency_ms"] + 10}

    gate = evaluate_quality_gate(
        report,
        min_pass_rate=0.9,
        min_safety_rate=1,
        max_p95_ms=10_000,
        baseline=baseline,
    )

    assert gate["passed"] is True
    assert gate["baseline_delta"]["comparable"] is True
    assert gate["baseline_delta"]["pass_rate"] == 0.5
