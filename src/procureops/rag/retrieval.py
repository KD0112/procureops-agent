from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from procureops.rag.governance import KnowledgeDocument, scan_knowledge_base


class RetrievalHit(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: str
    tenant_id: str
    heading: str
    content: str
    score: float = Field(ge=0, le=1)
    lexical_score: float = Field(ge=0, le=1)
    semantic_score: float = Field(ge=0, le=1)
    bm25_rank: int | None = Field(default=None, ge=1)
    vector_rank: int | None = Field(default=None, ge=1)
    rrf_score: float = Field(default=0, ge=0, le=1)
    citation: str
    document_sha256: str


class Retriever(Protocol):
    def search(
        self,
        *,
        tenant_id: str,
        actor_roles: frozenset[str],
        query: str,
        top_k: int | None = None,
        minimum_score: float | None = None,
    ) -> tuple[RetrievalHit, ...]: ...


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    document: KnowledgeDocument
    heading: str
    content: str


def _terms(text: str) -> set[str]:
    return set(_term_list(text))


def _term_list(text: str) -> list[str]:
    normalized = text.casefold()
    latin = re.findall(r"[a-z0-9][a-z0-9_.-]+", normalized)
    chinese_sequences = re.findall(r"[\u4e00-\u9fff]+", normalized)
    chinese: list[str] = []
    for sequence in chinese_sequences:
        chinese.append(sequence)
        chinese.extend(sequence[index : index + 2] for index in range(len(sequence) - 1))
    return latin + chinese


def _bm25_scores(
    query: str, documents: list[str], *, k1: float = 1.5, b: float = 0.75
) -> list[float]:
    query_terms = set(_term_list(query))
    if not query_terms or not documents:
        return [0.0] * len(documents)
    tokenized = [_term_list(document) for document in documents]
    average_length = sum(len(tokens) for tokens in tokenized) / len(tokenized)
    document_frequency = {
        term: sum(term in set(tokens) for tokens in tokenized) for term in query_terms
    }
    scores: list[float] = []
    for tokens in tokenized:
        frequencies = Counter(tokens)
        length_ratio = len(tokens) / average_length if average_length else 0.0
        score = 0.0
        for term in query_terms:
            frequency = frequencies[term]
            if not frequency:
                continue
            frequency_docs = document_frequency[term]
            inverse_frequency = math.log(
                1 + (len(tokenized) - frequency_docs + 0.5) / (frequency_docs + 0.5)
            )
            score += inverse_frequency * (
                frequency * (k1 + 1)
                / (frequency + k1 * (1 - b + b * length_ratio))
            )
        scores.append(score)
    return scores


def _rrf_rankings(
    lexical_scores: list[float],
    semantic_scores: list[float],
    *,
    lexical_weight: float,
    semantic_weight: float,
    rrf_k: int = 60,
) -> tuple[dict[int, int], dict[int, int], dict[int, float]]:
    lexical_order = sorted(
        (index for index, score in enumerate(lexical_scores) if score > 0),
        key=lambda index: (-lexical_scores[index], index),
    )
    semantic_order = sorted(
        (index for index, score in enumerate(semantic_scores) if score > 0),
        key=lambda index: (-semantic_scores[index], index),
    )
    lexical_ranks = {index: rank for rank, index in enumerate(lexical_order, start=1)}
    semantic_ranks = {index: rank for rank, index in enumerate(semantic_order, start=1)}
    maximum = (lexical_weight + semantic_weight) / (rrf_k + 1)
    fused: dict[int, float] = {}
    for index in set(lexical_ranks) | set(semantic_ranks):
        raw = 0.0
        if index in lexical_ranks:
            raw += lexical_weight / (rrf_k + lexical_ranks[index])
        if index in semantic_ranks:
            raw += semantic_weight / (rrf_k + semantic_ranks[index])
        fused[index] = raw / maximum if maximum else 0.0
    return lexical_ranks, semantic_ranks, fused


def _char_ngrams(text: str, size: int = 3) -> set[str]:
    normalized = re.sub(r"\s+", "", text.casefold())
    if len(normalized) <= size:
        return {normalized} if normalized else set()
    return {normalized[index : index + size] for index in range(len(normalized) - size + 1)}


def _query_containment(query: set[str], candidate: set[str]) -> float:
    if not query or not candidate:
        return 0.0
    return len(query & candidate) / len(query)


def _chunks(document: KnowledgeDocument) -> list[KnowledgeChunk]:
    chunks: list[KnowledgeChunk] = []
    heading = document.metadata.document_id
    body: list[str] = []
    for line in document.body.splitlines():
        if line.startswith("#"):
            if body:
                chunks.append(
                    KnowledgeChunk(
                        document=document,
                        heading=heading,
                        content="\n".join(body).strip(),
                    )
                )
            heading = line.lstrip("#").strip()
            body = []
        else:
            body.append(line)
    if body:
        chunks.append(
            KnowledgeChunk(
                document=document,
                heading=heading,
                content="\n".join(body).strip(),
            )
        )
    return [chunk for chunk in chunks if chunk.content]


class GovernedRetriever:
    """Deterministic hybrid retrieval with tenant and role filtering before scoring."""

    def __init__(self, *, knowledge_root: Path, retrieval_config: Path) -> None:
        self.config = json.loads(retrieval_config.read_text(encoding="utf-8"))
        self.documents = scan_knowledge_base(knowledge_root)
        self.chunks = [chunk for document in self.documents for chunk in _chunks(document)]

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
        query_ngrams = _char_ngrams(query)
        weights = self.config["hybrid_weights"]
        lexical_weight = float(weights["keyword"])
        semantic_weight = float(weights["semantic"])
        threshold = (
            float(minimum_score)
            if minimum_score is not None
            else float(self.config["minimum_score"])
        )
        authorized = [
            chunk
            for chunk in self.chunks
            if chunk.document.metadata.tenant_id == tenant_id
            and actor_roles.intersection(chunk.document.metadata.allowed_roles)
        ]
        texts = [f"{chunk.heading}\n{chunk.content}" for chunk in authorized]
        lexical_scores = _bm25_scores(query, texts)
        semantic_scores = [
            _query_containment(query_ngrams, _char_ngrams(text)) for text in texts
        ]
        lexical_ranks, semantic_ranks, fused = _rrf_rankings(
            lexical_scores,
            semantic_scores,
            lexical_weight=lexical_weight,
            semantic_weight=semantic_weight,
        )
        max_lexical = max(lexical_scores, default=0.0)
        hits: list[RetrievalHit] = []
        for index, chunk in enumerate(authorized):
            metadata = chunk.document.metadata
            lexical = lexical_scores[index] / max_lexical if max_lexical else 0.0
            semantic = semantic_scores[index]
            score = fused.get(index, 0.0)
            if score <= 0 or score < threshold:
                continue
            hits.append(
                RetrievalHit(
                    document_id=metadata.document_id,
                    tenant_id=metadata.tenant_id,
                    heading=chunk.heading,
                    content=chunk.content,
                    score=round(score, 6),
                    lexical_score=round(lexical, 6),
                    semantic_score=round(semantic, 6),
                    bm25_rank=lexical_ranks.get(index),
                    vector_rank=semantic_ranks.get(index),
                    rrf_score=round(score, 6),
                    citation=f"{metadata.document_id}@{metadata.version}#{chunk.heading}",
                    document_sha256=chunk.document.sha256,
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
        limit = int(top_k or self.config["top_k"])
        return tuple(hits[:limit])
