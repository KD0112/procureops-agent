"""Evaluate the governed hybrid retriever on a versioned dataset."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from procureops.config import load_environment  # noqa: E402
from procureops.rag import SQLiteKnowledgeIndex, embedding_provider_from_environment  # noqa: E402
from procureops.rag.evaluation import (  # noqa: E402
    load_retrieval_cases,
    run_retrieval_evaluation,
    save_retrieval_report,
)
from procureops.rag.governance import scan_knowledge_base  # noqa: E402


def main() -> None:
    load_environment(PROJECT_ROOT)
    index = SQLiteKnowledgeIndex(
        path=PROJECT_ROOT / "var" / "rag" / "multi_tenant.sqlite3",
        embedding_provider=embedding_provider_from_environment(),
    )
    documents = scan_knowledge_base(PROJECT_ROOT / "knowledge")
    if not index.is_current(documents):
        index.rebuild(documents)
    cases = load_retrieval_cases(PROJECT_ROOT / "data" / "evals" / "rag_retrieval_v1.jsonl")
    report = run_retrieval_evaluation(index, cases)
    path = save_retrieval_report(
        report, PROJECT_ROOT / "reports" / "latest_rag_retrieval_eval.json"
    )
    print(
        json.dumps(
            {
                "report": str(path),
                "dataset_version": report.dataset_version,
                "case_count": report.case_count,
                "recall_at_k": report.recall_at_k,
                "mrr": report.mrr,
                "precision_at_k": report.precision_at_k,
            }
        )
    )


if __name__ == "__main__":
    main()
