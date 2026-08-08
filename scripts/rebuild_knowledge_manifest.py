"""Validate governed knowledge and rebuild its deterministic manifest."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from procureops.rag.governance import scan_knowledge_base  # noqa: E402

KNOWLEDGE_ROOT = PROJECT_ROOT / "knowledge"
MANIFEST_PATH = KNOWLEDGE_ROOT / "manifest.json"


def main() -> None:
    documents = scan_knowledge_base(KNOWLEDGE_ROOT)
    manifest = {
        "manifest_version": "1.0.0",
        "documents": [
            {
                "document_id": item.metadata.document_id,
                "tenant_id": item.metadata.tenant_id,
                "version": item.metadata.version,
                "classification": item.metadata.classification,
                "allowed_roles": list(item.metadata.allowed_roles),
                "path": item.path.relative_to(PROJECT_ROOT).as_posix(),
                "sha256": item.sha256,
            }
            for item in documents
        ],
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"validated {len(documents)} documents -> {MANIFEST_PATH}")


if __name__ == "__main__":
    main()
