"""Governed retrieval contracts."""

from procureops.rag.advanced import (
    AdvancedRetriever,
    NoiseFilter,
    RAGDiagnostics,
    RetrievalMetrics,
    SmallToBigChunker,
    VectorIndex,
    evaluate_retrieval,
)
from procureops.rag.document_parser import DocumentBlock, DocumentParser, ParsedDocument
from procureops.rag.embeddings import (
    OpenAICompatibleEmbeddingProvider,
    embedding_provider_from_environment,
)
from procureops.rag.governance import (
    KnowledgeDocument,
    KnowledgeMetadata,
    load_knowledge_document,
    scan_knowledge_base,
)
from procureops.rag.index import HashingEmbeddingProvider, SQLiteKnowledgeIndex
from procureops.rag.ingestion import DocumentIngestionService
from procureops.rag.prefetch import PrefetchDecision, decide_prefetch
from procureops.rag.retrieval import GovernedRetriever, RetrievalHit, Retriever

__all__ = [
    "AdvancedRetriever",
    "DocumentBlock",
    "DocumentIngestionService",
    "DocumentParser",
    "GovernedRetriever",
    "HashingEmbeddingProvider",
    "KnowledgeDocument",
    "KnowledgeMetadata",
    "NoiseFilter",
    "OpenAICompatibleEmbeddingProvider",
    "ParsedDocument",
    "PrefetchDecision",
    "RAGDiagnostics",
    "RetrievalHit",
    "RetrievalMetrics",
    "Retriever",
    "SQLiteKnowledgeIndex",
    "SmallToBigChunker",
    "VectorIndex",
    "decide_prefetch",
    "embedding_provider_from_environment",
    "evaluate_retrieval",
    "load_knowledge_document",
    "scan_knowledge_base",
]
