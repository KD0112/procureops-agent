from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from procureops.api import create_app

PROJECT_ROOT = Path(__file__).resolve().parents[2]
HEADERS = {
    "X-Tenant-Id": "tenant_commerce_ops",
    "X-Actor-Id": "commerce-buyer",
    "X-Actor-Roles": "procurement_operator",
}


def test_commerce_sql_rag_and_prefetch_contract(tmp_path: Path):
    app = create_app(
        project_root=PROJECT_ROOT,
        database_path=tmp_path / "api.sqlite3",
        var_root=tmp_path / "var",
        allow_header_auth=True,
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/commerce/insights",
            headers=HEADERS,
            json={"query": "按商品看退货率，退款政策怎么要求？"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["analytics"]["intent"] == "return_rate"
        assert payload["execution_contract"]["writes"] == "disabled"
        assert payload["policy_evidence"]
        assert payload["provenance"]["status"] == "synthetic_demo"

        debug = client.post(
            "/api/search/diagnostics",
            headers=HEADERS,
            json={"query": "退款政策", "top_k": 4},
        )
        assert debug.status_code == 200
        assert "bm25_score" in debug.json()["pipeline"]["explanations"][0]

        advanced = client.post(
            "/api/search",
            headers=HEADERS,
            json={"query": "退款政策", "top_k": 4, "pipeline": "advanced"},
        )
        assert advanced.status_code == 200
        assert advanced.json()["items"]


def test_api_errors_have_request_id_and_stable_shape(tmp_path: Path):
    app = create_app(
        project_root=PROJECT_ROOT,
        database_path=tmp_path / "api.sqlite3",
        var_root=tmp_path / "var",
        allow_header_auth=True,
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/search",
            headers={
                "X-Tenant-Id": "tenant_commerce_ops",
                "X-Actor-Id": "commerce-buyer",
                "X-Actor-Roles": "procurement_operator",
                "X-Request-ID": "req-test-001",
            },
            json={"query": ""},
        )
        assert response.status_code == 422
        assert response.headers["X-Request-ID"] == "req-test-001"
        assert response.json()["error_code"] == "REQUEST_VALIDATION_ERROR"
