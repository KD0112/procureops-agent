from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from procureops.rag.retrieval import Retriever


class RetrievalEvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    case_id: str
    tenant_id: str
    actor_roles: frozenset[str] = Field(min_length=1)
    query: str = Field(min_length=1)
    expected_document_ids: frozenset[str] = Field(min_length=1)
    k: int = Field(default=5, ge=1, le=20)


class RetrievalEvalResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    returned_document_ids: tuple[str, ...]
    recall_at_k: float = Field(ge=0, le=1)
    reciprocal_rank: float = Field(ge=0, le=1)
    precision_at_k: float = Field(ge=0, le=1)


class RetrievalEvalReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    dataset_version: str
    case_count: int
    recall_at_k: float
    mrr: float
    precision_at_k: float
    results: tuple[RetrievalEvalResult, ...]


def load_retrieval_cases(path: Path) -> tuple[RetrievalEvalCase, ...]:
    cases = tuple(
        RetrievalEvalCase.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    if not cases:
        raise ValueError("retrieval evaluation dataset is empty")
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("retrieval evaluation case_id values must be unique")
    if len({case.dataset_version for case in cases}) != 1:
        raise ValueError("retrieval evaluation dataset versions must match")
    return cases


def run_retrieval_evaluation(
    retriever: Retriever, cases: tuple[RetrievalEvalCase, ...]
) -> RetrievalEvalReport:
    if not cases:
        raise ValueError("retrieval evaluation requires at least one case")
    results: list[RetrievalEvalResult] = []
    for case in cases:
        hits = retriever.search(
            tenant_id=case.tenant_id,
            actor_roles=case.actor_roles,
            query=case.query,
            top_k=case.k,
            minimum_score=0,
        )
        returned = tuple(hit.document_id for hit in hits[: case.k])
        relevant = case.expected_document_ids.intersection(returned)
        first_rank = next(
            (
                rank
                for rank, document_id in enumerate(returned, start=1)
                if document_id in case.expected_document_ids
            ),
            None,
        )
        results.append(
            RetrievalEvalResult(
                case_id=case.case_id,
                returned_document_ids=returned,
                recall_at_k=len(relevant) / len(case.expected_document_ids),
                reciprocal_rank=1 / first_rank if first_rank is not None else 0,
                precision_at_k=len(relevant) / case.k,
            )
        )
    count = len(results)
    return RetrievalEvalReport(
        dataset_version=cases[0].dataset_version,
        case_count=count,
        recall_at_k=round(sum(item.recall_at_k for item in results) / count, 6),
        mrr=round(sum(item.reciprocal_rank for item in results) / count, 6),
        precision_at_k=round(sum(item.precision_at_k for item in results) / count, 6),
        results=tuple(results),
    )


def save_retrieval_report(report: RetrievalEvalReport, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path
