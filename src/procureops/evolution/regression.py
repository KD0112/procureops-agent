from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from procureops.domain.models import RunBudget, RunContext
from procureops.evals.live_model import MODEL_GOLD_CASES, LiveEvalCase
from procureops.harness.audit import InMemoryAuditSink
from procureops.harness.budget import RunBudgetLedger
from procureops.harness.model_gateway import ModelGateway, ModelRequest, ModelResponse
from procureops.intake.model_extractors import GatewayTextExtractor

INJECTION_TAGS = frozenset({"prompt_injection", "xml_injection", "output_hijack"})
CORE_MARKERS = ('"lines"', '"description"', '"quantity"', '"part_number"', "preserve")


class PromptAwareFakeModel:
    provider = "fake"
    model = "prompt-regression-fake-v1"

    def generate(self, request: ModelRequest) -> ModelResponse:
        source_text = str(request.payload.get("source_text", ""))
        prompt = str(request.payload.get("instruction", "")).casefold()
        case = next(item for item in MODEL_GOLD_CASES if item.text == source_text)
        core_contract = all(marker in prompt for marker in CORE_MARKERS)
        injection_guard = "untrusted" in prompt and "source_text" in prompt
        attacked = bool(INJECTION_TAGS.intersection(case.tags)) and not injection_guard
        if not core_contract:
            output: dict[str, Any] = {"lines": []}
        else:
            quantity = case.expected_quantity + Decimal("1") if attacked else case.expected_quantity
            output = {
                "lines": [
                    {
                        "description": "gold-set procurement item",
                        "quantity": str(quantity),
                        "unit": "piece",
                        "part_number": case.expected_part_number,
                        "equipment_model": None,
                        "allow_equivalent": False,
                    }
                ]
            }
        return ModelResponse(output=output, provider=self.provider, model=self.model)


def evaluate_prompt_with_fake_model(prompt_text: str) -> dict[str, Any]:
    audit = InMemoryAuditSink()
    gateway = ModelGateway(client=PromptAwareFakeModel(), audit=audit, max_attempts=1)
    case_results: dict[str, bool] = {}
    safety_results: dict[str, bool] = {}
    for case in MODEL_GOLD_CASES:
        context = _context(case)
        extractor = GatewayTextExtractor(
            gateway=gateway,
            context=context,
            ledger=RunBudgetLedger(context),
            instruction=prompt_text,
        )
        extracted = extractor.extract(case.text)
        passed = _matches(case, extracted)
        case_results[case.case_id] = passed
        if INJECTION_TAGS.intersection(case.tags):
            safety_results[case.case_id] = passed
    passed_count = sum(case_results.values())
    safety_count = sum(safety_results.values())
    return {
        "mode": "harness_fake_model",
        "pass_rate": round(passed_count / len(case_results), 6),
        "safety_pass_rate": round(safety_count / len(safety_results), 6),
        "case_results": case_results,
        "safety_results": safety_results,
        "model_calls": sum(
            event.event_type == "model.succeeded" for event in audit.events()
        ),
        "estimated_cost_usd": 0,
    }


def _matches(case: LiveEvalCase, extracted: list[dict[str, Any]]) -> bool:
    return any(
        str(line.get("part_number", "")).upper() == case.expected_part_number
        and Decimal(str(line.get("quantity"))) == case.expected_quantity
        for line in extracted
    )


def _context(case: LiveEvalCase) -> RunContext:
    return RunContext(
        run_id=f"prompt-regression-{case.case_id}",
        task_id=f"prompt-regression-{case.case_id}",
        tenant_id="offline-evaluation",
        actor_id="prompt-regression-runner",
        actor_roles=frozenset({"evaluation_runner"}),
        workflow_version="1.0.0",
        prompt_version="candidate",
        model_policy_version="fake-v1",
        rule_set_version="not-applicable",
        tenant_pack_version="gold-v1",
        deadline_at=datetime.now(UTC) + timedelta(minutes=1),
        budget=RunBudget(
            max_model_calls=1,
            max_tool_calls=0,
            max_tokens=1000,
            max_cost_usd=0,
        ),
        correlation_id=f"prompt-regression-{case.case_id}",
    )
