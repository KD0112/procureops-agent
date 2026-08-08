from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from procureops.api import create_app

PROJECT_ROOT = Path(__file__).resolve().parents[2]
HEADERS = {
    "X-Tenant-ID": "tenant_engineering_machinery",
    "X-Actor-ID": "integration-buyer",
    "X-Actor-Roles": "procurement_operator,department_approver,compliance_approver",
}


def _client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(
            project_root=PROJECT_ROOT,
            database_path=tmp_path / "api.sqlite3",
            var_root=tmp_path / "var",
        )
    )


def _run_worker(client: TestClient) -> dict:
    response = client.post("/api/admin/worker/run-once", headers=HEADERS)
    assert response.status_code == 200
    return response.json()


def test_api_happy_path_pauses_for_approval_and_resumes_idempotently(
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        created = client.post(
            "/api/tasks/text",
            headers=HEADERS,
            json={"text": "DEMO-HYD-PUMP-001,液压泵,2,件,EX200-A"},
        )
        assert created.status_code == 202
        task_id = created.json()["task_id"]
        processed = _run_worker(client)
        assert processed["outcome"]["task_status"] == "awaiting_approval"

        pending = client.get(f"/api/tasks/{task_id}", headers=HEADERS).json()
        assert pending["task"]["status"] == "awaiting_approval"
        assert pending["pending_approval"]["approval_requirement"]["required_roles"]
        assert {item["field_name"] for item in pending["evidence"]} >= {
            "part_number",
            "matched_product_id",
            "unit_price",
        }

        approved = client.post(
            f"/api/tasks/{task_id}/approval",
            headers=HEADERS,
            json={"decision": "approve"},
        )
        assert approved.status_code == 202
        assert _run_worker(client)["outcome"]["task_status"] == "completed"
        completed = client.get(f"/api/tasks/{task_id}", headers=HEADERS).json()
        assert completed["task"]["status"] == "completed"
        assert completed["po_draft"] is not None
        po_id = completed["po_draft"]["po_draft_id"]

        assert _run_worker(client)["processed"] is False
        assert (
            client.get(f"/api/tasks/{task_id}/po", headers=HEADERS).json()["po_draft_id"] == po_id
        )
        assert completed["jobs"][0]["status"] == "succeeded"


def test_api_upload_and_tenant_isolation(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        created = client.post(
            "/api/tasks/upload",
            headers=HEADERS,
            files={
                "file": (
                    "request.txt",
                    "DEMO-HYD-PUMP-001,液压泵,1,件,EX200-A".encode(),
                    "text/plain",
                )
            },
        )
        assert created.status_code == 202
        task_id = created.json()["task_id"]
        detail = client.get(f"/api/tasks/{task_id}", headers=HEADERS).json()
        assert detail["uploads"][0]["original_filename"] == "request.txt"
        other_tenant = {**HEADERS, "X-Tenant-ID": "tenant-other"}
        assert client.get(f"/api/tasks/{task_id}", headers=other_tenant).status_code == 404


def test_api_accepts_corrected_request_after_needs_input(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        created = client.post(
            "/api/tasks/text", headers=HEADERS, json={"text": "请帮我采购一个配件"}
        )
        task_id = created.json()["task_id"]
        assert _run_worker(client)["outcome"]["task_status"] == "needs_input"
        answer = client.post(
            f"/api/tasks/{task_id}/answers",
            headers=HEADERS,
            json={"text": "DEMO-HYD-PUMP-001,液压泵,1,件,EX200-A"},
        )
        assert answer.status_code == 202
        assert _run_worker(client)["outcome"]["task_status"] == "awaiting_approval"


def test_api_rejects_insufficient_approver_role(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        created = client.post(
            "/api/tasks/text",
            headers=HEADERS,
            json={"text": "DEMO-FLT-KIT-001,保养滤芯包,10,套,SVC-2000H-A"},
        )
        task_id = created.json()["task_id"]
        _run_worker(client)
        operator_only = {**HEADERS, "X-Actor-Roles": "procurement_operator"}
        denied = client.post(
            f"/api/tasks/{task_id}/approval",
            headers=operator_only,
            json={"decision": "approve"},
        )
        assert denied.status_code == 403


@pytest.mark.parametrize(
    ("filename", "content_type", "expected_status"),
    [
        ("procurement_request.pdf", "application/pdf", "awaiting_approval"),
        (
            "procurement_request.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "awaiting_approval",
        ),
        ("procurement_request_photo.png", "image/png", "needs_input"),
    ],
)
def test_demo_assets_follow_offline_intake_contract(
    tmp_path: Path,
    filename: str,
    content_type: str,
    expected_status: str,
) -> None:
    asset = PROJECT_ROOT / "demo_assets" / "requests" / filename
    with _client(tmp_path) as client:
        created = client.post(
            "/api/tasks/upload",
            headers=HEADERS,
            files={"file": (filename, asset.read_bytes(), content_type)},
        )
        assert created.status_code == 202
        task_id = created.json()["task_id"]
        assert _run_worker(client)["outcome"]["task_status"] == expected_status
        detail = client.get(f"/api/tasks/{task_id}", headers=HEADERS).json()
        assert detail["task"]["status"] == expected_status
        if expected_status == "awaiting_approval":
            assert len(detail["items"]) == 2
            assert detail["evidence"]
        else:
            assert detail["items"] == []
