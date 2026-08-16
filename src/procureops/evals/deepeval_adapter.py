"""Optional DeepEval bridge for answer quality and RAG judge metrics."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar


@dataclass(frozen=True, slots=True)
class DeepEvalScore:
    case_id: str
    metric: str
    score: float | None
    reason: str | None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "metric": self.metric,
            "score": self.score,
            "reason": self.reason,
            "error": self.error,
        }


class DeepEvalAdapter:
    """Keep the main app independent from DeepEval's optional import tree."""

    METRIC_CLASSES: ClassVar[dict[str, str]] = {
        "answer_relevancy": "AnswerRelevancyMetric",
        "faithfulness": "FaithfulnessMetric",
        "contextual_relevancy": "ContextualRelevancyMetric",
        "contextual_precision": "ContextualPrecisionMetric",
        "contextual_recall": "ContextualRecallMetric",
    }

    @staticmethod
    def available() -> bool:
        try:
            import deepeval  # noqa: F401
        except ImportError:
            return False
        return True

    @staticmethod
    def _imports() -> tuple[Any, Any]:
        try:
            from deepeval.metrics import (  # type: ignore[import-not-found]
                AnswerRelevancyMetric,
                ContextualPrecisionMetric,
                ContextualRecallMetric,
                ContextualRelevancyMetric,
                FaithfulnessMetric,
            )
            from deepeval.test_case import LLMTestCase  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                'DeepEval is not installed; run `pip install -e ".[quality]"`'
            ) from exc
        return (
            {
                "AnswerRelevancyMetric": AnswerRelevancyMetric,
                "FaithfulnessMetric": FaithfulnessMetric,
                "ContextualRelevancyMetric": ContextualRelevancyMetric,
                "ContextualPrecisionMetric": ContextualPrecisionMetric,
                "ContextualRecallMetric": ContextualRecallMetric,
            },
            LLMTestCase,
        )

    @classmethod
    def build_test_case(cls, row: Mapping[str, Any]) -> Any:
        _metric_classes, test_case_class = cls._imports()
        values: dict[str, Any] = {
            "input": str(row.get("input", "")),
            "actual_output": str(row.get("actual_output", "")),
        }
        for key in ("expected_output", "retrieval_context", "context"):
            if row.get(key) is not None:
                values[key] = row[key]
        return test_case_class(**values)

    @classmethod
    def build_metrics(
        cls, names: Iterable[str], *, threshold: float = 0.7
    ) -> dict[str, Any]:
        metric_classes, _ = cls._imports()
        metrics: dict[str, Any] = {}
        for name in names:
            class_name = cls.METRIC_CLASSES.get(name)
            if class_name is None:
                raise ValueError(f"unsupported DeepEval metric: {name}")
            metric_class = metric_classes.get(class_name)
            if metric_class is None:
                raise RuntimeError(f"DeepEval installation lacks {class_name}")
            try:
                metrics[name] = metric_class(threshold=threshold, include_reason=True)
            except Exception as exc:
                raise RuntimeError(
                    "DeepEval metric initialization failed; configure the judge model "
                    "(for example OPENAI_API_KEY or a custom DeepEval model) before running."
                ) from exc
        return metrics

    @staticmethod
    def _measure(metric: Any, test_case: Any) -> None:
        result = metric.measure(test_case)
        if inspect.isawaitable(result):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(result)
            else:
                raise RuntimeError("run DeepEval synchronously outside an active event loop")

    @classmethod
    def evaluate_rows(
        cls,
        rows: Iterable[Mapping[str, Any]],
        *,
        metric_names: Iterable[str] = ("answer_relevancy", "faithfulness"),
        threshold: float = 0.7,
    ) -> list[DeepEvalScore]:
        metrics = cls.build_metrics(metric_names, threshold=threshold)
        scores: list[DeepEvalScore] = []
        for row in rows:
            case_id = str(row.get("case_id", "unknown"))
            test_case = cls.build_test_case(row)
            for name, metric in metrics.items():
                try:
                    cls._measure(metric, test_case)
                    raw_score = getattr(metric, "score", None)
                    score = float(raw_score) if raw_score is not None else None
                    reason = getattr(metric, "reason", None)
                    scores.append(
                        DeepEvalScore(
                            case_id=case_id,
                            metric=name,
                            score=score,
                            reason=str(reason) if reason else None,
                        )
                    )
                except Exception as exc:
                    scores.append(
                        DeepEvalScore(
                            case_id=case_id,
                            metric=name,
                            score=None,
                            reason=None,
                            error=type(exc).__name__,
                        )
                    )
        return scores
