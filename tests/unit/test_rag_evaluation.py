from __future__ import annotations

from pathlib import Path

import pytest

from procureops.rag.evaluation import (
    load_retrieval_cases,
    run_retrieval_evaluation,
    save_retrieval_report,
)
from procureops.rag.retrieval import RetrievalHit


class FakeRetriever:
    def search(self, **kwargs):
        del kwargs
        return (
            RetrievalHit(
                document_id="doc-a",
                tenant_id="tenant_test",
                heading="A",
                content="A",
                score=1,
                lexical_score=1,
                semantic_score=1,
                bm25_rank=1,
                vector_rank=1,
                rrf_score=1,
                citation="doc-a@1.0.0#A",
                document_sha256="a" * 64,
            ),
            RetrievalHit(
                document_id="doc-x",
                tenant_id="tenant_test",
                heading="X",
                content="X",
                score=0.5,
                lexical_score=0.5,
                semantic_score=0.5,
                citation="doc-x@1.0.0#X",
                document_sha256="b" * 64,
            ),
        )


def test_retrieval_evaluation_reports_recall_mrr_and_precision(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text(
        '{"dataset_version":"1.0.0","case_id":"case-1","tenant_id":"tenant_test",'
        '"actor_roles":["reader"],"query":"a","expected_document_ids":["doc-a"],"k":2}\n',
        encoding="utf-8",
    )
    cases = load_retrieval_cases(path)

    report = run_retrieval_evaluation(FakeRetriever(), cases)

    assert report.recall_at_k == 1
    assert report.mrr == 1
    assert report.precision_at_k == 0.5
    assert report.dataset_version == "1.0.0"

    saved = save_retrieval_report(report, tmp_path / "reports" / "rag.json")
    assert saved.is_file()
    assert '"recall_at_k": 1.0' in saved.read_text(encoding="utf-8")


def test_retrieval_evaluation_rejects_empty_and_inconsistent_datasets(
    tmp_path: Path,
) -> None:
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        load_retrieval_cases(empty)
    with pytest.raises(ValueError, match="at least one"):
        run_retrieval_evaluation(FakeRetriever(), ())

    duplicate = tmp_path / "duplicate.jsonl"
    line = (
        '{"dataset_version":"1.0.0","case_id":"same","tenant_id":"tenant_test",'
        '"actor_roles":["reader"],"query":"a","expected_document_ids":["doc-a"],"k":2}'
    )
    duplicate.write_text(f"{line}\n{line}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unique"):
        load_retrieval_cases(duplicate)

    mixed = tmp_path / "mixed.jsonl"
    mixed.write_text(
        f'{line}\n{line.replace("1.0.0", "2.0.0").replace("same", "other")}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="versions"):
        load_retrieval_cases(mixed)
