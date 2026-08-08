"""Validation and inventory of governed knowledge documents."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class KnowledgeMetadata(BaseModel):
    """Required governance metadata for every retrievable document."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    document_type: str = Field(min_length=1)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    status: str
    owner: str = Field(min_length=1)
    effective_from: date
    review_due: date
    classification: str
    contains_dynamic_facts: bool
    allowed_roles: tuple[str, ...]
    source_kind: str

    @field_validator("status")
    @classmethod
    def approved_only(cls, value: str) -> str:
        if value != "approved":
            raise ValueError("only approved documents may enter the retrieval corpus")
        return value

    @field_validator("contains_dynamic_facts")
    @classmethod
    def no_dynamic_facts(cls, value: bool) -> bool:
        if value:
            raise ValueError("dynamic facts must come from a database or tool")
        return value

    @field_validator("classification")
    @classmethod
    def supported_classification(cls, value: str) -> str:
        if value not in {"public", "internal", "confidential"}:
            raise ValueError("unsupported information classification")
        return value


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    path: Path
    metadata: KnowledgeMetadata
    body: str
    sha256: str


def _split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise ValueError("knowledge document must start with YAML front matter")
    try:
        _, raw_metadata, body = text.split("---\n", 2)
    except ValueError as exc:
        raise ValueError("knowledge document has malformed YAML front matter") from exc
    parsed = yaml.safe_load(raw_metadata)
    if not isinstance(parsed, dict):
        raise ValueError("knowledge metadata must be a mapping")
    return parsed, body.strip()


def load_knowledge_document(path: Path) -> KnowledgeDocument:
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    metadata_dict, body = _split_front_matter(text)
    if not body:
        raise ValueError(f"knowledge document is empty: {path}")
    metadata = KnowledgeMetadata.model_validate(metadata_dict)
    return KnowledgeDocument(
        path=path,
        metadata=metadata,
        body=body,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def scan_knowledge_base(root: Path) -> list[KnowledgeDocument]:
    documents = []
    for path in sorted(root.rglob("*.md")):
        if path.name.casefold() == "readme.md":
            continue
        documents.append(load_knowledge_document(path))

    document_ids = [item.metadata.document_id for item in documents]
    if len(document_ids) != len(set(document_ids)):
        raise ValueError("knowledge document_id values must be unique")
    return documents
