"""Offline evaluation, replay and architecture comparison."""

from procureops.evals.models import EvalCase, EvalReport, EvalResult
from procureops.evals.quality import compare_metrics, dataset_summary, report_metrics
from procureops.evals.runner import EvaluationRunner, compare_reports

__all__ = [
    "EvalCase",
    "EvalReport",
    "EvalResult",
    "EvaluationRunner",
    "compare_metrics",
    "compare_reports",
    "dataset_summary",
    "report_metrics",
]
