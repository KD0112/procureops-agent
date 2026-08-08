from pathlib import Path

from procureops.rag.governance import scan_knowledge_base

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_harn_010_all_knowledge_is_approved_static_and_tenant_scoped() -> None:
    documents = scan_knowledge_base(PROJECT_ROOT / "knowledge")

    assert documents
    assert all(item.metadata.status == "approved" for item in documents)
    assert all(not item.metadata.contains_dynamic_facts for item in documents)
    assert all(item.metadata.tenant_id for item in documents)
    assert len({item.metadata.document_id for item in documents}) == len(documents)


def test_rag_dynamic_operational_facts_are_declared_out_of_scope() -> None:
    rules_path = (
        PROJECT_ROOT
        / "data"
        / "tenant_packs"
        / "tenant_engineering_machinery"
        / "rules.json"
    )
    content = rules_path.read_text(encoding="utf-8")

    for field in (
        "current_price",
        "current_inventory",
        "current_logistics_eta",
        "order_status",
    ):
        assert field in content
