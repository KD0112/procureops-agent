from __future__ import annotations

from procureops.evals.live_model import DEFAULT_LIVE_CASES, run_live_model_eval
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
