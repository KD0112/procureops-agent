"""Measure local baseline/advanced RAG latency and retrieval metrics."""

from __future__ import annotations

import sys
from pathlib import Path
from time import perf_counter

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from procureops.evals.performance import LatencySample, summarize_latency  # noqa: E402
from procureops.evals.quality import write_json  # noqa: E402
from procureops.rag import (  # noqa: E402
    AdvancedRetriever,
    HashingEmbeddingProvider,
    SQLiteKnowledgeIndex,
    evaluate_retrieval,
)
from procureops.rag.governance import scan_knowledge_base  # noqa: E402

TENANT_ID = "tenant_engineering_machinery"
ROLES = frozenset({"procurement_operator"})
QUERIES = (
    ("catalog matching part number", {"EM-GUIDE-CATALOG-001"}),
    ("approval threshold purchase order", {"EM-POL-APPROVAL-001"}),
    ("supplier governance evidence", {"EM-POL-SUPPLIER-001"}),
    ("quotation freshness expiry", {"EM-POL-QUOTE-001"}),
    ("memory governance candidate conflict", {"EM-POL-MEMORY-001"}),
    ("procurement policy approval", {"EM-POL-PROCUREMENT-001"}),
    ("evidence audit source citation", {"EM-POL-EVIDENCE-001"}),
)


def _run_search(
    search, *, name: str, repetitions: int = 3
) -> tuple[list[LatencySample], object]:
    samples: list[LatencySample] = []
    results = []
    for query_index, (query, relevant) in enumerate(QUERIES):
        for repetition in range(repetitions):
            started = perf_counter()
            hits = search(query)
            elapsed = (perf_counter() - started) * 1000
            results.append((hits, relevant))
            samples.append(
                LatencySample(
                    case_id=f"{name}-{query_index + 1:02d}-{repetition + 1}",
                    retrieval_ms=elapsed,
                    total_ms=elapsed,
                )
            )
    retrieval_metrics = evaluate_retrieval(
        [hits for hits, _ in results],
        [relevant for _, relevant in results],
        k=5,
    )
    return samples, retrieval_metrics


def main() -> None:
    documents = scan_knowledge_base(PROJECT_ROOT / "knowledge")
    baseline = SQLiteKnowledgeIndex(
        path=PROJECT_ROOT / "var" / "benchmarks" / "rag-baseline.sqlite3",
        embedding_provider=HashingEmbeddingProvider(dimensions=256),
    )
    baseline.rebuild(documents)
    advanced = AdvancedRetriever(
        documents=documents,
        embedding_provider=HashingEmbeddingProvider(dimensions=256),
        backend="hnsw",
    )
    diagnostics = advanced.build(tenant_id=TENANT_ID, actor_roles=ROLES)
    baseline_samples, baseline_metrics = _run_search(
        lambda query: baseline.search(
            tenant_id=TENANT_ID,
            actor_roles=ROLES,
            query=query,
            top_k=5,
            minimum_score=0,
        ),
        name="baseline",
    )
    advanced_samples, advanced_metrics = _run_search(
        lambda query: advanced.search(
            tenant_id=TENANT_ID,
            actor_roles=ROLES,
            query=query,
            top_k=5,
        ),
        name="advanced",
    )
    payload = {
        "profile": "local_hashing_embedding",
        "query_count": len(QUERIES),
        "repetitions": 3,
        "baseline": {
            "latency": summarize_latency(baseline_samples),
            "retrieval": baseline_metrics.__dict__
            if hasattr(baseline_metrics, "__dict__")
            else {
                "recall_at_k": baseline_metrics.recall_at_k,
                "precision_at_k": baseline_metrics.precision_at_k,
                "mrr": baseline_metrics.mrr,
                "ndcg_at_k": baseline_metrics.ndcg_at_k,
                "duplicate_rate": baseline_metrics.duplicate_rate,
            },
        },
        "advanced": {
            "latency": summarize_latency(advanced_samples),
            "retrieval": {
                "recall_at_k": advanced_metrics.recall_at_k,
                "precision_at_k": advanced_metrics.precision_at_k,
                "mrr": advanced_metrics.mrr,
                "ndcg_at_k": advanced_metrics.ndcg_at_k,
                "duplicate_rate": advanced_metrics.duplicate_rate,
            },
            "diagnostics": {
                "requested_backend": diagnostics.requested_backend,
                "actual_backend": diagnostics.actual_backend,
                "fallback_reason": diagnostics.fallback_reason,
                "child_count": diagnostics.child_count,
                "parent_count": diagnostics.parent_count,
                "filtered_noise_count": diagnostics.filtered_noise_count,
            },
        },
        "interpretation": (
            "This is a local deterministic harness using hashing embeddings. "
            "Use a production embedding model and a larger labeled query set "
            "before claiming quality gains."
        ),
    }
    destination = PROJECT_ROOT / "reports" / "latest_rag_latency_benchmark.json"
    write_json(destination, payload)
    print(f"wrote RAG latency benchmark to {destination}")
    print(
        f"backend={diagnostics.actual_backend} "
        f"p95={payload['advanced']['latency']['total_ms_p95']:.3f}ms"
    )


if __name__ == "__main__":
    main()
