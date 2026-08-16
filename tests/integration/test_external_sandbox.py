from pathlib import Path

from fastapi.testclient import TestClient

from procureops.integrations.sandbox import create_integration_sandbox

PROJECT_ROOT = Path(__file__).resolve().parents[2]
HEADERS = {
    "Authorization": "Bearer integration-test-key",
    "X-Tenant-ID": "tenant_enterprise_it",
    "X-ProcureOps-Contract-Version": "sandbox-test-v1",
}


def test_sandbox_exposes_separate_erp_supplier_and_logistics_contracts(
    tmp_path: Path,
) -> None:
    app = create_integration_sandbox(
        project_root=PROJECT_ROOT,
        database_path=tmp_path / "sandbox.sqlite3",
        api_key="integration-test-key",
    )
    with TestClient(app) as client:
        unknown_tenant = client.get(
            "/erp/v1/catalog/search",
            params={"query": "研发笔记本"},
            headers={**HEADERS, "X-Tenant-ID": "tenant_unknown"},
        )
        assert unknown_tenant.status_code == 404
        catalog = client.get(
            "/erp/v1/catalog/search",
            params={"query": "研发笔记本", "part_number": "IT-LAP-DEV-14"},
            headers=HEADERS,
        )
        assert catalog.status_code == 200
        product_id = catalog.json()["items"][0]["product_id"]

        suppliers = client.post(
            "/supplier/v1/options",
            json={"product_id": product_id, "quantity": "2"},
            headers=HEADERS,
        )
        assert suppliers.status_code == 200
        approved = [item for item in suppliers.json()["items"] if item["approved"]]
        assert len(approved) == 2

        logistics = client.post(
            "/logistics/v1/quotes",
            json={
                "product_id": product_id,
                "supplier_ids": [item["supplier_id"] for item in approved],
            },
            headers=HEADERS,
        )
        assert logistics.status_code == 200
        assert len(logistics.json()["items"]) == 2


def test_sandbox_po_draft_requires_auth_approval_hash_and_is_idempotent(
    tmp_path: Path,
) -> None:
    app = create_integration_sandbox(
        project_root=PROJECT_ROOT,
        database_path=tmp_path / "sandbox.sqlite3",
        api_key="integration-test-key",
    )
    request = {
        "task_id": "external-task-1",
        "payload": {"lines": [{"sku": "IT-LAP-DEV-14", "quantity": 2}]},
        "total_amount": "16000.00",
        "currency": "CNY",
    }
    write_headers = {
        **HEADERS,
        "Idempotency-Key": "external-po-1",
        "X-ProcureOps-Approval-Subject": "a" * 64,
    }
    with TestClient(app) as client:
        assert client.post(
            "/erp/v1/purchase-orders/drafts",
            json=request,
            headers={**write_headers, "Authorization": "Bearer wrong"},
        ).status_code == 401
        first = client.post(
            "/erp/v1/purchase-orders/drafts",
            json=request,
            headers=write_headers,
        )
        second = client.post(
            "/erp/v1/purchase-orders/drafts",
            json=request,
            headers=write_headers,
        )
        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["idempotency_hit"] is True
        assert second.json()["external_po_draft_id"] == first.json()["external_po_draft_id"]
