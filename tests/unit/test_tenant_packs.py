from pathlib import Path

import pytest

from procureops.tenancy import TenantPackRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_registry_discovers_versioned_tenant_packs() -> None:
    registry = TenantPackRegistry(PROJECT_ROOT / "data" / "tenant_packs")

    packs = registry.all()

    assert {pack.tenant.tenant_id for pack in packs} == {
        "tenant_engineering_machinery",
        "tenant_enterprise_it",
        "tenant_commerce_ops",
    }
    for pack in packs:
        assert pack.tenant.tenant_pack_version
        assert pack.rules.version
        assert pack.adapters.adapter_pack_version
        assert pack.retrieval.tenant_id == pack.tenant.tenant_id
        assert set(pack.adapters.adapters) == {
            "catalog_lookup",
            "supplier_lookup",
            "logistics_quote",
            "purchase_order_draft",
        }


def test_registry_fails_closed_for_unknown_or_traversal_tenant() -> None:
    registry = TenantPackRegistry(PROJECT_ROOT / "data" / "tenant_packs")

    with pytest.raises(KeyError, match="tenant pack"):
        registry.get("tenant_missing")
    with pytest.raises(ValueError, match="tenant_id"):
        registry.get("../knowledge")
