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
from procureops.rag.retrieval import GovernedRetriever, RetrievalHit, Retriever

__all__ = [
    "AdvancedRetriever",
    "DocumentIngestionService",
    "GovernedRetriever",
    "HashingEmbeddingProvider",
    "KnowledgeDocument",
    "KnowledgeMetadata",
    "NoiseFilter",
    "OpenAICompatibleEmbeddingProvider",
    "RAGDiagnostics",
    "RetrievalHit",
    "RetrievalMetrics",
    "Retriever",
    "SQLiteKnowledgeIndex",
    "SmallToBigChunker",
    "VectorIndex",
    "embedding_provider_from_environment",
    "evaluate_retrieval",
    "load_knowledge_document",
    "scan_knowledge_base",
]
