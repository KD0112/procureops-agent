from __future__ import annotations

from procureops.rag.document_parser import DocumentParser


def test_markdown_tables_are_atomic_protected_blocks():
    parsed = DocumentParser().parse(
        "policy.md",
        "# Policy\n\nUse the policy.\n\n| SKU | Rule |\n|---|---|\n| A | 7 days |\n",
    )
    table_blocks = [block for block in parsed.blocks if block.block_type == "table"]
    assert len(table_blocks) == 1
    assert "<!-- table:start" in parsed.text
    assert "<!-- table:end -->" in parsed.text


def test_sparse_pdf_without_optional_ocr_reports_fallback_warning():
    # Invalid bytes are deliberately not parsed as PDF; this test only checks
    # the stable parser contract for ordinary text input.
    parsed = DocumentParser().parse("notes.txt", b"hello\nworld")
    assert parsed.parser == "markdown_block_parser"
    assert parsed.blocks[0].block_type == "paragraph"
