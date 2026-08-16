from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from procureops.rag.governance import KnowledgeDocument
from procureops.rag.retrieval import (
    KnowledgeChunk,
    RetrievalHit,
    _bm25_scores,
    _chunks,
    _rrf_rankings,
    _terms,
)


class EmbeddingProvider(Protocol):
    provider: str
    model: str
    dimensions: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class HashingEmbeddingProvider:
    """Offline deterministic dense baseline; replace with a semantic provider in real evals."""

    provider = "local"
    model = "feature-hashing-v1"

    def __init__(self, *, dimensions: int = 256) -> None:
        if dimensions < 32:
            raise ValueError("embedding dimensions must be at least 32")
        self.dimensions = dimensions

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for term in _terms(text):
            digest = hashlib.sha256(term.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector


class SQLiteKnowledgeIndex:
    """Persistent tenant-aware hybrid index for the local no-Docker profile."""

    def __init__(
        self,
        *,
        path: Path,
        embedding_provider: EmbeddingProvider,
        lexical_weight: float = 0.5,
        vector_weight: float = 0.5,
    ) -> None:
        if not math.isclose(lexical_weight + vector_weight, 1.0):
            raise ValueError("hybrid weights must sum to 1")
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.embedding_provider = embedding_provider
        self.lexical_weight = lexical_weight
        self.vector_weight = vector_weight
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS index_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS knowledge_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    document_version TEXT NOT NULL,
                    allowed_roles_json TEXT NOT NULL,
                    heading TEXT NOT NULL,
                    content TEXT NOT NULL,
                    search_terms TEXT NOT NULL,
                    document_sha256 TEXT NOT NULL,
                    embedding_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_tenant
                ON knowledge_chunks(tenant_id);
                """
            )

    def rebuild(self, documents: list[KnowledgeDocument]) -> int:
        chunks = [chunk for document in documents for chunk in _chunks(document)]
        texts = [f"{chunk.heading}\n{chunk.content}" for chunk in chunks]
        vectors = self.embedding_provider.embed(texts)
        if len(vectors) != len(chunks):
            raise ValueError("embedding provider returned the wrong vector count")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM knowledge_chunks")
            for chunk, text, vector in zip(chunks, texts, vectors, strict=True):
                self._insert_chunk(connection, chunk, text, vector)
            metadata = {
                "index_version": "2.0.0",
                "corpus_hash": _corpus_hash(documents),
                "embedding_provider": self.embedding_provider.provider,
                "embedding_model": self.embedding_provider.model,
                "embedding_dimensions": str(self.embedding_provider.dimensions),
                "document_count": str(len(documents)),
                "chunk_count": str(len(chunks)),
                "lexical_algorithm": "bm25",
                "fusion_algorithm": "rrf",
                "rrf_k": "60",
            }
            connection.execute("DELETE FROM index_metadata")
            connection.executemany(
                "INSERT INTO index_metadata(key, value) VALUES (?, ?)",
                sorted(metadata.items()),
            )
        return len(chunks)

    @staticmethod
    def _insert_chunk(
        connection: sqlite3.Connection,
        chunk: KnowledgeChunk,
        text: str,
        vector: list[float],
    ) -> None:
        metadata = chunk.document.metadata
        chunk_id = hashlib.sha256(
            f"{metadata.document_id}:{metadata.version}:{chunk.heading}:{text}".encode()
        ).hexdigest()
        connection.execute(
            """
            INSERT INTO knowledge_chunks(
                chunk_id, document_id, tenant_id, document_version,
                allowed_roles_json, heading, content, search_terms,
                document_sha256, embedding_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chunk_id,
                metadata.document_id,
                metadata.tenant_id,
                metadata.version,
                json.dumps(sorted(metadata.allowed_roles), ensure_ascii=False),
                chunk.heading,
                chunk.content,
                " ".join(sorted(_terms(text))),
                chunk.document.sha256,
                json.dumps(vector, separators=(",", ":")),
            ),
        )

    def is_current(self, documents: list[KnowledgeDocument]) -> bool:
        metadata = self.metadata()
        return (
            metadata.get("corpus_hash") == _corpus_hash(documents)
            and metadata.get("embedding_provider") == self.embedding_provider.provider
            and metadata.get("embedding_model") == self.embedding_provider.model
            and metadata.get("embedding_dimensions")
            == str(self.embedding_provider.dimensions)
            and metadata.get("fusion_algorithm") == "rrf"
        )

    def metadata(self) -> dict[str, str]:
        with self._connect() as connection:
            rows = connection.execute("SELECT key, value FROM index_metadata").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def search(
        self,
        *,
        tenant_id: str,
        actor_roles: frozenset[str],
        query: str,
        top_k: int | None = None,
        minimum_score: float | None = None,
    ) -> tuple[RetrievalHit, ...]:
        if not tenant_id or not actor_roles:
            return ()
        query_vector = self.embedding_provider.embed([query])[0]
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM knowledge_chunks WHERE tenant_id=?
                ORDER BY document_id, heading, chunk_id
                """,
                (tenant_id,),
            ).fetchall()
        authorized_rows = []
        for row in rows:
            allowed_roles = frozenset(json.loads(row["allowed_roles_json"]))
            if actor_roles.intersection(allowed_roles):
                authorized_rows.append(row)
        texts = [f"{row['heading']}\n{row['content']}" for row in authorized_rows]
        bm25_scores = _bm25_scores(query, texts)
        dense_scores = [
            max(0.0, _cosine(query_vector, json.loads(row["embedding_json"])))
            for row in authorized_rows
        ]
        bm25_ranks, vector_ranks, fused_scores = _rrf_rankings(
            bm25_scores,
            dense_scores,
            lexical_weight=self.lexical_weight,
            semantic_weight=self.vector_weight,
        )
        max_bm25 = max(bm25_scores, default=0.0)
        hits: list[RetrievalHit] = []
        threshold = 0.0 if minimum_score is None else minimum_score
        for index, row in enumerate(authorized_rows):
            lexical = bm25_scores[index] / max_bm25 if max_bm25 else 0.0
            normalized_dense = dense_scores[index]
            score = fused_scores.get(index, 0.0)
            if score <= 0 or score < threshold:
                continue
            hits.append(
                RetrievalHit(
                    document_id=row["document_id"],
                    tenant_id=row["tenant_id"],
                    heading=row["heading"],
                    content=row["content"],
                    score=round(min(1.0, score), 6),
                    lexical_score=round(lexical, 6),
                    semantic_score=round(normalized_dense, 6),
                    bm25_rank=bm25_ranks.get(index),
                    vector_rank=vector_ranks.get(index),
                    rrf_score=round(score, 6),
                    citation=(
                        f"{row['document_id']}@{row['document_version']}#{row['heading']}"
                    ),
                    document_sha256=row["document_sha256"],
                )
            )
        hits.sort(
            key=lambda item: (
                -item.score,
                -item.lexical_score,
                -item.semantic_score,
                item.document_id,
                item.heading,
            )
        )
        return tuple(hits[: (top_k or 6)])


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("embedding dimensions do not match")
    return sum(a * b for a, b in zip(left, right, strict=True))


def _corpus_hash(documents: list[KnowledgeDocument]) -> str:
    payload = "\n".join(
        f"{item.metadata.document_id}:{item.metadata.version}:{item.sha256}"
        for item in sorted(documents, key=lambda document: document.metadata.document_id)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
