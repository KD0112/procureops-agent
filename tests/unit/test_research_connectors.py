from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from procureops.harness.errors import PermanentToolError
from procureops.integrations.research import (
    HTTPResearchConnector,
    LocalFileResearchConnector,
    research_connector_from_environment,
)


def _record(**updates: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "tenant_id": "tenant_engineering_machinery",
        "supplier_id": "supplier-alpha",
        "product_id": None,
        "source_id": "registry-alpha",
        "source_type": "authoritative_registry",
        "locator": "registry://quality/alpha",
        "observed_at": "2026-08-01T00:00:00Z",
        "content_hash": "a" * 64,
        "claim_key": "quality_certification",
        "claim_value": "valid",
        "claim": "Certification is valid.",
        "relevance": 0.9,
        "confidence": 0.9,
        "trust_tier": "authoritative",
    }
    payload.update(updates)
    return payload


def test_local_research_connector_validates_and_filters(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    path.write_text(
        json.dumps(
            [
                _record(),
                _record(
                    supplier_id="supplier-beta",
                    source_id="registry-beta",
                    content_hash="b" * 64,
                    product_id="other-product",
                ),
            ]
        ),
        encoding="utf-8",
    )
    connector = LocalFileResearchConnector(path)

    results = connector.search(
        tenant_id="tenant_engineering_machinery",
        product_id="p-hyd-pump-001",
        supplier_ids=("supplier-alpha", "supplier-beta"),
        query="qualification",
    )

    assert [item.supplier_id for item in results] == ["supplier-alpha"]

    path.write_text('[{"bad":true}]', encoding="utf-8")
    with pytest.raises(ValueError, match="invalid local"):
        LocalFileResearchConnector(path)


class FakeHTTPClient:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items
        self.request: dict[str, Any] | None = None

    def request_json(self, **kwargs):
        self.request = kwargs
        return {
            "tenant_id": kwargs["tenant_id"],
            "items": self.items,
        }


def test_http_research_connector_uses_fixed_path_and_validates_schema() -> None:
    client = FakeHTTPClient([_record()])
    connector = HTTPResearchConnector(client=client, path="/v1/research")

    results = connector.search(
        tenant_id="tenant_engineering_machinery",
        product_id="p-hyd-pump-001",
        supplier_ids=("supplier-alpha",),
        query="qualification",
    )

    assert len(results) == 1
    assert client.request is not None
    assert client.request["path"] == "/v1/research"
    assert client.request["json_body"]["supplier_ids"] == ["supplier-alpha"]
    with pytest.raises(ValueError, match="absolute"):
        HTTPResearchConnector(client=client, path="https://untrusted.example/research")

    invalid = HTTPResearchConnector(client=FakeHTTPClient([{"bad": True}]), path="/v1/research")
    with pytest.raises(PermanentToolError, match="schema"):
        invalid.search(
            tenant_id="tenant_engineering_machinery",
            product_id="p-hyd-pump-001",
            supplier_ids=("supplier-alpha",),
            query="qualification",
        )


def test_research_connector_environment_profiles(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PROCUREOPS_RESEARCH_PROFILE", "disabled")
    assert research_connector_from_environment(tmp_path) is None

    monkeypatch.setenv("PROCUREOPS_RESEARCH_PROFILE", "unsupported")
    with pytest.raises(ValueError, match="unsupported"):
        research_connector_from_environment(tmp_path)

    monkeypatch.setenv("PROCUREOPS_RESEARCH_PROFILE", "http_allowlisted")
    monkeypatch.delenv("PROCUREOPS_RESEARCH_BASE_URL", raising=False)
    monkeypatch.delenv("PROCUREOPS_RESEARCH_API_KEY", raising=False)
    with pytest.raises(ValueError, match="incomplete"):
        research_connector_from_environment(tmp_path)

    monkeypatch.setenv("PROCUREOPS_RESEARCH_BASE_URL", "https://research.example.test")
    monkeypatch.setenv("PROCUREOPS_RESEARCH_API_KEY", "test-secret")
    connector = research_connector_from_environment(tmp_path)
    assert isinstance(connector, HTTPResearchConnector)
