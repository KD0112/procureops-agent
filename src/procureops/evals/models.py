from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EvalCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    tenant_id: str = "tenant_engineering_machinery"
    category: str
    input_text: str
    expected_outcome: str
    fault: dict[str, str] = Field(default_factory=dict)
    attack_kind: str | None = None
    expected_roles: frozenset[str] = frozenset()
    tags: frozenset[str] = frozenset()
    dataset_version: str = "1.0.0"
    split: str = "development"
    expected_tools: frozenset[str] = frozenset()
    forbidden_tools: frozenset[str] = frozenset()
    reference_answer: str | None = None
    retrieval_context: tuple[str, ...] = ()
    metadata: dict[str, str] = Field(default_factory=dict)


class EvalResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    category: str
    architecture: str
    expected_outcome: str
    actual_outcome: str
    passed: bool
    safety_passed: bool
    evidence_coverage: float = Field(ge=0, le=1)
    latency_ms: float = Field(ge=0)
    model_calls: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
    specialist_messages: int = Field(ge=0)
    failure_class: str | None = None
    replay_path: str | None = None


class EvalReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    architecture: str
    dataset_size: int
    passed: int
    pass_rate: float
    safety_pass_rate: float
    average_evidence_coverage: float
    completed_evidence_coverage: float
    latency_p50_ms: float
    latency_p95_ms: float
    average_tool_calls: float
    total_model_calls: int
    estimated_total_cost_usd: float
    outcome_taxonomy: dict[str, int]
    failure_taxonomy: dict[str, int]
    category_metrics: dict[str, dict[str, float | int]]
    results: tuple[EvalResult, ...]


class ABComparison(BaseModel):
    model_config = ConfigDict(frozen=True)

    single: dict[str, Any]
    multi: dict[str, Any]
    quality_delta: float
    safety_delta: float
    latency_delta_ms: float
    tool_call_delta: float
    recommendation: str
    rationale: tuple[str, ...]
