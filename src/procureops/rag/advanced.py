"""Small-to-big retrieval with optional HNSW/IVF-PQ and a deterministic reranker.

The module is deliberately dependency-tolerant.  ``hnswlib`` and ``faiss`` are
optional acceleration backends; when they are not installed the same API uses
an exact cosine scan and reports that fact in ``diagnostics``.  This keeps local
tests reproducible while making the production upgrade path explicit.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

from procureops.rag.governance import KnowledgeDocument
from procureops.rag.index import EmbeddingProvider, _cosine
from procureops.rag.retrieval import (
    RetrievalHit,
    _bm25_scores,
    _chunks,
    _term_list,
)


@dataclass(frozen=True, slots=True)
class ParentChildChunk:
    child_id: str
    parent_id: str
    document: KnowledgeDocument
    heading: str
    content: str
    parent_content: str


@dataclass(frozen=True, slots=True)
class RAGDiagnostics:
    requested_backend: str
    actual_backend: str
    fallback_reason: str | None
    child_count: int
    parent_count: int
    candidate_count: int
    returned_count: int
    filtered_noise_count: int


@dataclass(frozen=True, slots=True)
class RetrievalMetrics:
    recall_at_k: float
    precision_at_k: float
    mrr: float
    ndcg_at_k: float
    duplicate_rate: float


class SmallToBigChunker:
    """Create retrievable child windows while returning their larger parent."""

    def __init__(self, *, parent_chars: int = 1800, child_chars: int = 450, overlap: int = 80):
        if not 0 < overlap < child_chars <= parent_chars:
            raise ValueError("require 0 < overlap < child_chars <= parent_chars")
        self.parent_chars = parent_chars
        self.child_chars = child_chars
        self.overlap = overlap

    def split(self, document: KnowledgeDocument) -> list[ParentChildChunk]:
        result: list[ParentChildChunk] = []
        for section in _chunks(document):
            # Keep a whole section as the parent when it is short; long sections
            # become a sequence of deterministic parent windows.
            parents = self._window(section.content, self.parent_chars, self.overlap)
            for parent_number, parent in enumerate(parents):
                parent_id = self._id(document, section.heading, parent_number, parent)
                children = self._window(parent, self.child_chars, self.overlap)
                for child_number, child in enumerate(children):
                    child_id = self._id(document, section.heading, child_number, child, parent_id)
                    result.append(
                        ParentChildChunk(
                            child_id=child_id,
                            parent_id=parent_id,
                            document=document,
                            heading=section.heading,
                            content=child,
                            parent_content=parent,
                        )
                    )
        return result

    @staticmethod
    def _window(text: str, width: int, overlap: int) -> list[str]:
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return []
        if len(text) <= width:
            return [text]
        step = width - overlap
        return [
            text[start : start + width].strip()
            for start in range(0, len(text), step)
            if text[start : start + width].strip()
        ]

    @staticmethod
    def _id(
        document: KnowledgeDocument, heading: str, number: int, text: str, parent: str = ""
    ) -> str:
        raw = (
            f"{document.metadata.document_id}:{document.metadata.version}:"
            f"{heading}:{parent}:{number}:{text}"
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class NoiseFilter:
    """Remove boilerplate and near-duplicate chunks before ANN indexing."""

    _boilerplate = re.compile(
        r"^(page\s*\d+|confidential|all rights reserved|table of contents|目录|版权所有)\s*$",
        re.IGNORECASE,
    )

    def apply(self, chunks: Sequence[ParentChildChunk]) -> tuple[list[ParentChildChunk], int]:
        seen: set[str] = set()
        clean: list[ParentChildChunk] = []
        removed = 0
        for chunk in chunks:
            text = re.sub(r"\s+", " ", chunk.content).strip()
            fingerprint = re.sub(r"[^\w\u4e00-\u9fff]", "", text.casefold())
            if len(fingerprint) < 24 or self._boilerplate.fullmatch(text) or fingerprint in seen:
                removed += 1
                continue
            seen.add(fingerprint)
            clean.append(chunk)
        return clean, removed


class VectorIndex:
    """ANN facade with honest exact-search fallback."""

    def __init__(self, *, backend: str = "hnsw", hnsw_ef: int = 64, ivf_nlist: int = 32):
        if backend not in {"exact", "hnsw", "ivf-pq"}:
            raise ValueError("backend must be exact, hnsw, or ivf-pq")
        self.requested_backend = backend
        self.actual_backend = "exact"
        self.fallback_reason: str | None = None
        self.hnsw_ef = hnsw_ef
        self.ivf_nlist = ivf_nlist
        self.vectors: list[list[float]] = []
        self._engine = None
        self._engine_kind: str | None = None

    def build(self, vectors: Sequence[Sequence[float]]) -> None:
        self.vectors = [list(vector) for vector in vectors]
        self.actual_backend = "exact"
        self.fallback_reason = None
        self._engine = None
        self._engine_kind = None
        if not self.vectors or self.requested_backend == "exact":
            return
        if self.requested_backend == "hnsw":
            try:
                import hnswlib  # type: ignore[import-not-found]
                import numpy as np  # type: ignore[import-not-found]

                engine = hnswlib.Index(space="cosine", dim=len(self.vectors[0]))
                engine.init_index(max_elements=len(self.vectors), ef_construction=200, M=16)
                engine.add_items(np.asarray(self.vectors, dtype="float32"))
                engine.set_ef(max(self.hnsw_ef, 10))
                self._engine = engine
                self._engine_kind = "hnswlib"
                self.actual_backend = "hnsw"
            except ImportError as hnsw_exc:
                try:
                    import faiss  # type: ignore[import-not-found]
                    import numpy as np  # type: ignore[import-not-found]

                    dimension = len(self.vectors[0])
                    engine = faiss.IndexHNSWFlat(dimension, 16, faiss.METRIC_INNER_PRODUCT)
                    engine.hnsw.efConstruction = 200
                    engine.hnsw.efSearch = max(self.hnsw_ef, 10)
                    matrix = np.asarray(self.vectors, dtype="float32")
                    faiss.normalize_L2(matrix)
                    engine.add(matrix)
                    self._engine = engine
                    self._engine_kind = "faiss"
                    self.actual_backend = "hnsw"
                    self.fallback_reason = (
                        f"hnswlib unavailable ({hnsw_exc.name}); using faiss HNSW"
                    )
                except ImportError as faiss_exc:
                    self.fallback_reason = (
                        f"optional HNSW dependencies unavailable: {hnsw_exc.name}, "
                        f"{faiss_exc.name}"
                    )
        else:
            try:
                import faiss  # type: ignore[import-not-found]
                import numpy as np  # type: ignore[import-not-found]

                dimension = len(self.vectors[0])
                nlist = min(self.ivf_nlist, max(1, len(self.vectors) // 4))
                if len(self.vectors) < max(64, nlist * 4):
                    raise RuntimeError("too few vectors to train IVF-PQ safely")
                m = max(1, min(16, dimension // 8))
                quantizer = faiss.IndexFlatIP(dimension)
                engine = faiss.IndexIVFPQ(quantizer, dimension, nlist, m, 8)
                matrix = np.asarray(self.vectors, dtype="float32")
                engine.train(matrix)
                engine.add(matrix)
                engine.nprobe = min(8, nlist)
                self._engine = engine
                self.actual_backend = "ivf-pq"
            except (ImportError, RuntimeError, ValueError) as exc:
                self.fallback_reason = f"IVF-PQ fallback: {exc}"

    def search(self, query: Sequence[float], *, top_k: int) -> list[tuple[int, float]]:
        if not self.vectors or top_k <= 0:
            return []
        if self.actual_backend == "hnsw":
            import numpy as np  # type: ignore[import-not-found]

            if self._engine_kind == "faiss":
                query_matrix = np.asarray([query], dtype="float32")
                import faiss  # type: ignore[import-not-found]

                faiss.normalize_L2(query_matrix)
                scores, labels = self._engine.search(
                    query_matrix, min(top_k, len(self.vectors))
                )
                return [
                    (int(index), max(0.0, float(score)))
                    for index, score in zip(labels[0], scores[0], strict=True)
                    if index >= 0
                ]
            labels, distances = self._engine.knn_query(
                np.asarray([query], dtype="float32"), k=min(top_k, len(self.vectors))
            )
            return [
                (int(index), max(0.0, 1.0 - float(distance)))
                for index, distance in zip(labels[0], distances[0], strict=True)
            ]
        if self.actual_backend == "ivf-pq":
            import numpy as np  # type: ignore[import-not-found]

            scores, labels = self._engine.search(
                np.asarray([query], dtype="float32"), min(top_k, len(self.vectors))
            )
            return [
                (int(index), max(0.0, float(score)))
                for index, score in zip(labels[0], scores[0], strict=True)
                if index >= 0
            ]
        scored = [
            (index, max(0.0, _cosine(query, vector))) for index, vector in enumerate(self.vectors)
        ]
        return sorted(scored, key=lambda item: (-item[1], item[0]))[:top_k]


class AdvancedRetriever:
    """Tenant/role-filtered small-to-big hybrid retrieval pipeline."""

    def __init__(
        self,
        *,
        documents: Sequence[KnowledgeDocument],
        embedding_provider: EmbeddingProvider,
        backend: str = "hnsw",
        chunker: SmallToBigChunker | None = None,
    ):
        self.documents = list(documents)
        self.embedding_provider = embedding_provider
        self.chunker = chunker or SmallToBigChunker()
        self.index = VectorIndex(backend=backend)
        self.chunks: list[ParentChildChunk] = []
        self._vectors: list[list[float]] = []
        self.diagnostics = RAGDiagnostics(backend, "exact", None, 0, 0, 0, 0, 0)

    def build(
        self, *, tenant_id: str | None = None, actor_roles: frozenset[str] | None = None
    ) -> RAGDiagnostics:
        authorized_documents = [
            document
            for document in self.documents
            if (tenant_id is None or document.metadata.tenant_id == tenant_id)
            and (not actor_roles or actor_roles.intersection(document.metadata.allowed_roles))
        ]
        raw = [chunk for document in authorized_documents for chunk in self.chunker.split(document)]
        self.chunks, removed = NoiseFilter().apply(raw)
        vectors = self.embedding_provider.embed(
            [f"{chunk.heading}\n{chunk.content}" for chunk in self.chunks]
        )
        self._vectors = [list(vector) for vector in vectors]
        self.index.build(vectors)
        parent_count = len({chunk.parent_id for chunk in self.chunks})
        self.diagnostics = RAGDiagnostics(
            self.index.requested_backend,
            self.index.actual_backend,
            self.index.fallback_reason,
            len(self.chunks),
            parent_count,
            0,
            0,
            removed,
        )
        return self.diagnostics

    def search(
        self, *, tenant_id: str, actor_roles: frozenset[str], query: str, top_k: int = 6
    ) -> tuple[RetrievalHit, ...]:
        authorized = [
            chunk
            for chunk in self.chunks
            if chunk.document.metadata.tenant_id == tenant_id
            and actor_roles.intersection(chunk.document.metadata.allowed_roles)
        ]
        if not authorized:
            return ()
        texts = [f"{chunk.heading}\n{chunk.content}" for chunk in authorized]
        lexical = _bm25_scores(query, texts)
        query_vector = self.embedding_provider.embed([query])[0]
        # The built index can contain a wider corpus. Restrict candidate search
        # to authorized chunks for tenant isolation; exact scoring remains the
        # deterministic fallback used in the no-native-dependency profile.
        global_indices = [index for index, chunk in enumerate(self.chunks) if chunk in authorized]
        local_by_global = {
            global_index: local_index for local_index, global_index in enumerate(global_indices)
        }
        vector_scores = [0.0] * len(authorized)
        vector_order: list[int] = []
        for global_index, score in self.index.search(query_vector, top_k=max(20, top_k * 4)):
            local_index = local_by_global.get(global_index)
            if local_index is not None:
                vector_scores[local_index] = score
                vector_order.append(local_index)
        # If the index was built for a different scope, complete the authorized
        # candidate set with exact scores while retaining the ANN results first.
        for local_index, global_index in enumerate(global_indices):
            if local_index not in vector_order:
                vector_scores[local_index] = max(
                    0.0, _cosine(query_vector, self._vectors[global_index])
                )
                vector_order.append(local_index)
        vector_order = vector_order[: max(20, top_k * 4)]
        lexical_order = sorted(
            (i for i, score in enumerate(lexical) if score > 0), key=lambda i: (-lexical[i], i)
        )[: max(20, top_k * 4)]
        candidate_ids = list(dict.fromkeys(lexical_order + vector_order))
        lexical_rank = {index: rank for rank, index in enumerate(lexical_order, 1)}
        vector_rank = {index: rank for rank, index in enumerate(vector_order, 1)}
        reranked: list[tuple[float, int]] = []
        query_terms = set(_term_list(query))
        for index in candidate_ids:
            text_terms = set(_term_list(texts[index]))
            coverage = len(query_terms & text_terms) / len(query_terms) if query_terms else 0.0
            phrase_bonus = 0.15 if query.casefold().strip() in texts[index].casefold() else 0.0
            rrf = (0.5 / (60 + lexical_rank.get(index, 9999))) + (
                0.5 / (60 + vector_rank.get(index, 9999))
            )
            reranked.append(
                (
                    min(
                        1.0, coverage * 0.55 + vector_scores[index] * 0.25 + rrf * 20 + phrase_bonus
                    ),
                    index,
                )
            )
        reranked.sort(key=lambda item: (-item[0], authorized[item[1]].child_id))
        results: list[RetrievalHit] = []
        seen_parents: set[str] = set()
        for score, index in reranked:
            chunk = authorized[index]
            if chunk.parent_id in seen_parents:
                continue
            seen_parents.add(chunk.parent_id)
            metadata = chunk.document.metadata
            results.append(
                RetrievalHit(
                    document_id=metadata.document_id,
                    tenant_id=metadata.tenant_id,
                    heading=chunk.heading,
                    content=chunk.parent_content,
                    score=round(max(0.0, min(1.0, score)), 6),
                    lexical_score=round(
                        min(1.0, lexical[index] / max(1.0, max(lexical, default=0.0))),
                        6,
                    ),
                    semantic_score=round(vector_scores[index], 6),
                    bm25_rank=lexical_rank.get(index),
                    vector_rank=vector_rank.get(index),
                    rrf_score=round(min(1.0, score), 6),
                    citation=f"{metadata.document_id}@{metadata.version}#{chunk.heading}#{chunk.parent_id}",
                    document_sha256=chunk.document.sha256,
                )
            )
            if len(results) >= top_k:
                break
        self.diagnostics = RAGDiagnostics(
            self.diagnostics.requested_backend,
            self.diagnostics.actual_backend,
            self.diagnostics.fallback_reason,
            self.diagnostics.child_count,
            self.diagnostics.parent_count,
            len(candidate_ids),
            len(results),
            self.diagnostics.filtered_noise_count,
        )
        return tuple(results)


def evaluate_retrieval(
    results: Sequence[Sequence[RetrievalHit]],
    relevant_document_ids: Sequence[set[str]],
    *,
    k: int = 5,
) -> RetrievalMetrics:
    if len(results) != len(relevant_document_ids):
        raise ValueError("results and labels must have the same length")
    if not results:
        return RetrievalMetrics(0.0, 0.0, 0.0, 0.0, 0.0)
    recalls: list[float] = []
    precisions: list[float] = []
    reciprocal_ranks: list[float] = []
    ndcgs: list[float] = []
    duplicate_rates: list[float] = []
    for hits, relevant in zip(results, relevant_document_ids, strict=True):
        top = list(hits[:k])
        ids = [hit.document_id for hit in top]
        relevant_ranks = [rank for rank, item in enumerate(top, 1) if item.document_id in relevant]
        recalls.append(1.0 if relevant_ranks else 0.0)
        precisions.append(sum(item.document_id in relevant for item in top) / k)
        reciprocal_ranks.append(1.0 / relevant_ranks[0] if relevant_ranks else 0.0)
        dcg = sum((1.0 / math.log2(rank + 1)) for rank in relevant_ranks)
        ideal = sum((1.0 / math.log2(rank + 1)) for rank in range(1, min(k, len(relevant)) + 1))
        ndcgs.append(dcg / ideal if ideal else 0.0)
        duplicate_rates.append(1.0 - len(set(ids)) / len(ids) if ids else 0.0)
    return RetrievalMetrics(
        sum(recalls) / len(recalls),
        sum(precisions) / len(precisions),
        sum(reciprocal_ranks) / len(reciprocal_ranks),
        sum(ndcgs) / len(ndcgs),
        sum(duplicate_rates) / len(duplicate_rates),
    )
