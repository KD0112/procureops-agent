from __future__ import annotations

import hashlib
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

from procureops.rag.governance import scan_knowledge_base
from procureops.rag.index import SQLiteKnowledgeIndex


class DocumentIngestionService:
    """Turn approved uploads into governed Markdown and rebuild the local RAG index."""

    def __init__(
        self,
        *,
        project_root: Path,
        var_root: Path,
        retriever: SQLiteKnowledgeIndex,
    ) -> None:
        self.project_root = project_root
        self.var_root = var_root
        self.retriever = retriever
        self.approved_root = var_root / "knowledge_uploads"
        self.staging_root = var_root / "knowledge_staging"

    def ingest(
        self,
        *,
        tenant_id: str,
        task_id: str,
        actor_id: str,
        uploads: list[dict[str, Any]],
        blob_resolver,
        approved_for_retrieval: bool,
    ) -> dict[str, Any]:
        if not uploads:
            raise ValueError("document ingestion requires at least one upload")
        target_root = self.approved_root if approved_for_retrieval else self.staging_root
        target_root = target_root / _safe_name(tenant_id)
        target_root.mkdir(parents=True, exist_ok=True)
        documents: list[dict[str, Any]] = []
        for upload in uploads:
            raw = blob_resolver(str(upload["storage_key"])).read_bytes()
            text = _extract_text(Path(str(upload["original_filename"])), raw)
            if not text.strip():
                raise ValueError(f"uploaded document is empty: {upload['original_filename']}")
            digest = hashlib.sha256(raw).hexdigest()
            document_id = f"upload-{_safe_name(tenant_id)}-{task_id}-{digest[:12]}"
            output = target_root / f"{document_id}.md"
            output.write_text(
                _governed_markdown(
                    document_id=document_id,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    filename=str(upload["original_filename"]),
                    content=text,
                    approved=approved_for_retrieval,
                ),
                encoding="utf-8",
            )
            documents.append(
                {
                    "document_id": document_id,
                    "filename": upload["original_filename"],
                    "sha256": digest,
                    "chars": len(text),
                    "status": "indexed" if approved_for_retrieval else "staged",
                }
            )
        index_chunks = 0
        if approved_for_retrieval:
            corpus = scan_knowledge_base(self.project_root / "knowledge")
            corpus.extend(scan_knowledge_base(self.approved_root))
            index_chunks = self.retriever.rebuild(corpus)
        return {
            "task_id": task_id,
            "tenant_id": tenant_id,
            "documents": documents,
            "status": "indexed" if approved_for_retrieval else "staged_for_approval",
            "index_chunks": index_chunks,
        }


def _safe_name(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in "_-" else "_" for character in value
    )


def _extract_text(path: Path, raw: bytes) -> str:
    if path.suffix.casefold() == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("pypdf is required for PDF ingestion") from exc
        reader = PdfReader(BytesIO(raw))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    return raw.decode("utf-8-sig", errors="replace")


def _governed_markdown(
    *,
    document_id: str,
    tenant_id: str,
    actor_id: str,
    filename: str,
    content: str,
    approved: bool,
) -> str:
    today = date.today()
    review_due = today + timedelta(days=365)
    status = "approved" if approved else "pending"
    return (
        "---\n"
        f"document_id: {document_id}\n"
        f"tenant_id: {tenant_id}\n"
        "document_type: uploaded_knowledge\n"
        "version: 1.0.0\n"
        f"status: {status}\n"
        f"owner: {actor_id}\n"
        f"effective_from: {today.isoformat()}\n"
        f"review_due: {review_due.isoformat()}\n"
        "classification: internal\n"
        "contains_dynamic_facts: false\n"
        "allowed_roles:\n"
        "  - procurement_operator\n"
        "  - procurement_approver\n"
        "  - compliance_approver\n"
        "source_kind: user_upload\n"
        "---\n\n"
        f"# {filename}\n\n{content.strip()}\n"
    )
