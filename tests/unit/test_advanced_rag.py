from datetime import date
from pathlib import Path

from procureops.rag import (
    AdvancedRetriever,
    HashingEmbeddingProvider,
    KnowledgeMetadata,
    RetrievalMetrics,
    evaluate_retrieval,
)
from procureops.rag.governance import KnowledgeDocument


def _document(document_id: str, body: str) -> KnowledgeDocument:
    metadata = KnowledgeMetadata(
        document_id=document_id,
        tenant_id="tenant-a",
        document_type="guide",
        version="1.0.0",
        status="approved",
        owner="qa",
        effective_from=date(2026, 1, 1),
        review_due=date(2027, 1, 1),
        classification="internal",
        contains_dynamic_facts=False,
        allowed_roles=("procurement_operator",),
        source_kind="test",
    )
    return KnowledgeDocument(Path(f"{document_id}.md"), metadata, body, "0" * 64)


def test_small_to_big_ann_fallback_rerank_and_noise_filter() -> None:
    delivery_body = " ".join(
        f"The standard delivery deadline is 2026-09-30. "
        f"Receiving address confirmation step {index}."
        for index in range(20)
    )
    document = _document(
        "delivery-guide",
        f"# Noise\nPage 1\n# Delivery\n{delivery_body}",
    )
    retriever = AdvancedRetriever(
        documents=[document],
        embedding_provider=HashingEmbeddingProvider(dimensions=64),
        backend="ivf-pq",
    )
    diagnostics = retriever.build(
        tenant_id="tenant-a", actor_roles=frozenset({"procurement_operator"})
    )
    assert diagnostics.child_count > diagnostics.parent_count
    assert diagnostics.filtered_noise_count >= 1
    assert diagnostics.requested_backend == "ivf-pq"
    assert diagnostics.actual_backend in {"ivf-pq", "exact"}
    hits = retriever.search(
        tenant_id="tenant-a",
        actor_roles=frozenset({"procurement_operator"}),
        query="standard delivery deadline 2026-09-30",
    )
    assert hits
    assert "2026-09-30" in hits[0].content
    assert "#" in hits[0].citation


def test_retrieval_metrics_include_mrr_ndcg_and_duplicate_rate() -> None:
    document = _document("doc", "# H\nRelevant delivery deadline.")
    retriever = AdvancedRetriever(
        documents=[document],
        embedding_provider=HashingEmbeddingProvider(dimensions=64),
        backend="exact",
    )
    retriever.build()
    hits = retriever.search(
        tenant_id="tenant-a",
        actor_roles=frozenset({"procurement_operator"}),
        query="delivery deadline",
    )
    metrics = evaluate_retrieval([hits], [{"doc"}], k=3)
    assert isinstance(metrics, RetrievalMetrics)
    assert metrics.recall_at_k == 1
    assert metrics.mrr == 1
    assert metrics.ndcg_at_k == 1
    assert metrics.duplicate_rate == 0
