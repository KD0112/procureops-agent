from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

ALLOWED_EXTENSIONS = frozenset(
    {".txt", ".md", ".csv", ".pdf", ".xlsx", ".xlsm", ".png", ".jpg", ".jpeg", ".webp"}
)


@dataclass(frozen=True, slots=True)
class StoredBlob:
    storage_key: str
    path: Path
    original_filename: str
    content_type: str
    size_bytes: int
    sha256: str


class LocalBlobStore:
    """Tenant-scoped local object-store profile with atomic writes."""

    def __init__(self, root: Path, *, max_bytes: int = 10 * 1024 * 1024) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes

    def save(
        self,
        *,
        tenant_id: str,
        task_id: str,
        filename: str,
        content_type: str,
        data: bytes,
    ) -> StoredBlob:
        if len(data) > self.max_bytes:
            raise ValueError(f"upload exceeds {self.max_bytes} bytes")
        safe_tenant = _safe_segment(tenant_id)
        safe_task = _safe_segment(task_id)
        safe_filename = _safe_filename(filename)
        suffix = Path(safe_filename).suffix.casefold()
        if suffix not in ALLOWED_EXTENSIONS:
            raise ValueError(f"unsupported upload extension: {suffix}")
        storage_name = f"{uuid4().hex}-{safe_filename}"
        directory = (self.root / safe_tenant / safe_task).resolve()
        if self.root not in directory.parents:
            raise ValueError("upload path escaped blob root")
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / storage_name
        temporary = directory / f".{storage_name}.tmp"
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(destination)
        storage_key = destination.relative_to(self.root).as_posix()
        return StoredBlob(
            storage_key=storage_key,
            path=destination,
            original_filename=filename,
            content_type=content_type or "application/octet-stream",
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )

    def resolve(self, storage_key: str) -> Path:
        candidate = (self.root / storage_key).resolve()
        if self.root != candidate and self.root not in candidate.parents:
            raise ValueError("storage key escaped blob root")
        if not candidate.is_file():
            raise FileNotFoundError(storage_key)
        return candidate


def _safe_segment(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]", "_", value.strip())
    if not normalized or normalized in {".", ".."}:
        raise ValueError("invalid storage path segment")
    return normalized


def _safe_filename(value: str) -> str:
    name = Path(value).name
    stem = re.sub(r"[^A-Za-z0-9_.\-\u4e00-\u9fff]", "_", name)
    if not stem or stem in {".", ".."}:
        raise ValueError("invalid upload filename")
    return stem[:160]
