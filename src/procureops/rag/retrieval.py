from __future__ import annotations

import json
import re
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
    normalized = text.casefold()
    latin = set(re.findall(r"[a-z0-9][a-z0-9_.-]+", normalized))
    chinese_sequences = re.findall(r"[\u4e00-\u9fff]+", normalized)
    chinese: set[str] = set()
    for sequence in chinese_sequences:
        chinese.add(sequence)
        chinese.update(sequence[index : index + 2] for index in range(len(sequence) - 1))
    return latin | chinese


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
        query_terms = _terms(query)
        query_ngrams = _char_ngrams(query)
        weights = self.config["hybrid_weights"]
        lexical_weight = float(weights["keyword"])
        semantic_weight = float(weights["semantic"])
        threshold = (
            float(minimum_score)
            if minimum_score is not None
            else float(self.config["minimum_score"])
        )
        hits: list[RetrievalHit] = []
        for chunk in self.chunks:
            metadata = chunk.document.metadata
            if metadata.tenant_id != tenant_id:
                continue
            if not actor_roles.intersection(metadata.allowed_roles):
                continue
            candidate_text = f"{chunk.heading}\n{chunk.content}"
            candidate_terms = _terms(candidate_text)
            lexical = (
                len(query_terms & candidate_terms) / len(query_terms) if query_terms else 0.0
            )
            semantic = _query_containment(query_ngrams, _char_ngrams(candidate_text))
            score = min(1.0, lexical_weight * lexical + semantic_weight * semantic)
            if score < threshold:
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
                    citation=f"{metadata.document_id}@{metadata.version}#{chunk.heading}",
                    document_sha256=chunk.document.sha256,
                )
            )
        hits.sort(key=lambda item: (-item.score, item.document_id, item.heading))
        limit = int(top_k or self.config["top_k"])
        return tuple(hits[:limit])
