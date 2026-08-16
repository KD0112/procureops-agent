from dataclasses import replace
from pathlib import Path

from procureops.rag import HashingEmbeddingProvider, SQLiteKnowledgeIndex
from procureops.rag.governance import scan_knowledge_base

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_persistent_rag_index_rebuild_search_acl_and_staleness(tmp_path: Path) -> None:
    documents = scan_knowledge_base(PROJECT_ROOT / "knowledge")
    index = SQLiteKnowledgeIndex(
        path=tmp_path / "rag.sqlite3",
        embedding_provider=HashingEmbeddingProvider(dimensions=64),
    )

    chunks = index.rebuild(documents)

    assert chunks > len(documents)
    assert index.is_current(documents)
    metadata = index.metadata()
    assert metadata["document_count"] == str(len(documents))
    assert metadata["embedding_model"] == "feature-hashing-v1"
    hits = index.search(
        tenant_id="tenant_engineering_machinery",
        actor_roles=frozenset({"procurement_operator"}),
        query="液压泵 主泵",
        minimum_score=0.1,
    )
    assert hits
    assert hits[0].citation
    assert hits[0].rrf_score == hits[0].score
    assert hits[0].bm25_rank is not None or hits[0].vector_rank is not None
    assert metadata["fusion_algorithm"] == "rrf"
    assert index.search(
        tenant_id="tenant-other",
        actor_roles=frozenset({"procurement_operator"}),
        query="液压泵",
    ) == ()
    assert index.search(
        tenant_id="tenant_engineering_machinery",
        actor_roles=frozenset({"anonymous"}),
        query="液压泵",
    ) == ()

    changed = [replace(documents[0], sha256="0" * 64), *documents[1:]]
    assert not index.is_current(changed)


def test_hashing_embedding_is_deterministic_and_normalized() -> None:
    provider = HashingEmbeddingProvider(dimensions=64)
    first, second = provider.embed(["液压泵 主泵", "液压泵 主泵"])

    assert first == second
    assert round(sum(value * value for value in first), 6) == 1


def test_bm25_prefers_rare_exact_term_and_rrf_exposes_both_rankings(tmp_path: Path) -> None:
    documents = scan_knowledge_base(PROJECT_ROOT / "knowledge")
    index = SQLiteKnowledgeIndex(
        path=tmp_path / "rag.sqlite3",
        embedding_provider=HashingEmbeddingProvider(dimensions=64),
    )
    index.rebuild(documents)

    hits = index.search(
        tenant_id="tenant_enterprise_it",
        actor_roles=frozenset({"procurement_operator"}),
        query="IT-LAP-DEV-14",
        top_k=3,
        minimum_score=0,
    )

    assert hits[0].document_id == "IT-GUIDE-CATALOG-001"
    assert hits[0].bm25_rank == 1
    assert 0 < hits[0].lexical_score <= 1
    assert 0 < hits[0].rrf_score <= 1
