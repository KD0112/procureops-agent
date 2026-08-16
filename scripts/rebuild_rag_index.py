"""Rebuild the local governed hybrid RAG index from approved sources."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from procureops.config import load_environment  # noqa: E402
from procureops.rag import (  # noqa: E402
    SQLiteKnowledgeIndex,
    embedding_provider_from_environment,
)
from procureops.rag.governance import scan_knowledge_base  # noqa: E402


def main() -> None:
    load_environment(PROJECT_ROOT)
    documents = scan_knowledge_base(PROJECT_ROOT / "knowledge")
    index = SQLiteKnowledgeIndex(
        path=PROJECT_ROOT / "var" / "rag" / "multi_tenant.sqlite3",
        embedding_provider=embedding_provider_from_environment(),
    )
    chunk_count = index.rebuild(documents)
    metadata = index.metadata()
    print(
        f"rebuilt RAG documents={len(documents)} chunks={chunk_count} "
        f"model={metadata['embedding_model']} corpus={metadata['corpus_hash'][:12]}"
    )


if __name__ == "__main__":
    main()
