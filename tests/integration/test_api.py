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
APPROVER_HEADERS = {
    **HEADERS,
    "X-Actor-ID": "integration-approver",
}


def _client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(
            project_root=PROJECT_ROOT,
            database_path=tmp_path / "api.sqlite3",
            var_root=tmp_path / "var",
            allow_header_auth=True,
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
        assert pending["permissions"]["can_approve"] is False
        assert pending["permissions"]["approval_block_reason"] == "maker_checker"
        assert {item["field_name"] for item in pending["evidence"]} >= {
            "part_number",
            "matched_product_id",
            "unit_price",
        }

        checker_view = client.get(
            f"/api/tasks/{task_id}", headers=APPROVER_HEADERS
        ).json()
        assert checker_view["permissions"]["can_approve"] is True
        assert checker_view["permissions"]["approval_block_reason"] is None

        approved = client.post(
            f"/api/tasks/{task_id}/approval",
            headers=APPROVER_HEADERS,
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


def test_task_event_stream_is_tenant_scoped_resumable_and_redacted(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        created = client.post(
            "/api/tasks/text",
            headers=HEADERS,
            json={"text": "DEMO-HYD-PUMP-001,hydraulic pump,1,item,EX200-A"},
        )
        task_id = created.json()["task_id"]
        assert _run_worker(client)["outcome"]["task_status"] == "awaiting_approval"
        rejected = client.post(
            f"/api/tasks/{task_id}/approval",
            headers=APPROVER_HEADERS,
            json={"decision": "reject", "reason": "stream test"},
        )
        assert rejected.status_code == 202

        repository = client.app.state.runtime.repository
        repository.append_workflow_event(
            tenant_id=HEADERS["X-Tenant-ID"],
            task_id=task_id,
            event_type="test.sensitive",
            payload={
                "stage": "verification",
                "instruction": "hidden prompt text",
                "nested": {"api_key": "hidden secret"},
            },
        )
        last_sequence = repository.workflow_events(
            tenant_id=HEADERS["X-Tenant-ID"], task_id=task_id
        )[-1]["sequence"]

        with client.stream(
            "GET",
            f"/api/tasks/{task_id}/events/stream",
            headers={**HEADERS, "Last-Event-ID": str(last_sequence - 1)},
        ) as response:
            body = "".join(response.iter_text())

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert f"id: {last_sequence}" in body
        assert "event: test.sensitive" in body
        assert '"stage":"verification"' in body
        assert "hidden prompt text" not in body
        assert "hidden secret" not in body

        other_tenant = {**HEADERS, "X-Tenant-ID": "tenant-other"}
        assert (
            client.get(
                f"/api/tasks/{task_id}/events/stream", headers=other_tenant
            ).status_code
            == 404
        )


def test_api_multi_upload_merges_duplicate_pdf_and_excel_evidence(tmp_path: Path) -> None:
    pdf = PROJECT_ROOT / "demo_assets" / "requests" / "procurement_request.pdf"
    workbook = PROJECT_ROOT / "demo_assets" / "requests" / "procurement_request.xlsx"
    with _client(tmp_path) as client:
        created = client.post(
            "/api/tasks/upload",
            headers=HEADERS,
            files=[
                ("file", (pdf.name, pdf.read_bytes(), "application/pdf")),
                (
                    "file",
                    (
                        workbook.name,
                        workbook.read_bytes(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    ),
                ),
            ],
        )

        assert created.status_code == 202
        payload = created.json()
        assert len(payload["upload_ids"]) == 2
        assert _run_worker(client)["outcome"]["task_status"] == "awaiting_approval"
        detail = client.get(f"/api/tasks/{payload['task_id']}", headers=HEADERS).json()
        assert [item["requested_part_number"] for item in detail["items"]] == [
            "DEMO-HYD-PUMP-001",
            "DEMO-FLT-KIT-001",
        ]
        assert [item["original_filename"] for item in detail["uploads"]] == [
            pdf.name,
            workbook.name,
        ]
        evidence_sources = {item["source_id"] for item in detail["evidence"]}
        assert {pdf.name, workbook.name} <= evidence_sources


def test_api_multi_upload_conflict_fails_closed_before_supplier_tools(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        created = client.post(
            "/api/tasks/upload",
            headers=HEADERS,
            files=[
                (
                    "file",
                    (
                        "request-a.txt",
                        b"DEMO-HYD-PUMP-001,hydraulic pump,1,item,EX200-A",
                        "text/plain",
                    ),
                ),
                (
                    "file",
                    (
                        "request-b.txt",
                        b"DEMO-HYD-PUMP-001,hydraulic pump,2,item,EX200-A",
                        "text/plain",
                    ),
                ),
            ],
        )

        task_id = created.json()["task_id"]
        assert _run_worker(client)["outcome"]["task_status"] == "needs_input"
        detail = client.get(f"/api/tasks/{task_id}", headers=HEADERS).json()
        assert detail["po_draft"] is None
        assert all(item["selected_supplier_id"] is None for item in detail["items"])
        assert not any(
            event["event_type"] == "supplier.selection_decided"
            for event in detail["events"]
        )


def test_api_multi_upload_enforces_file_count_limit(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.post(
            "/api/tasks/upload",
            headers=HEADERS,
            files=[
                ("file", (f"request-{index}.txt", b"x", "text/plain"))
                for index in range(6)
            ],
        )

    assert response.status_code == 413
    assert "at most 5 files" in response.json()["detail"]


def test_task_delete_is_tenant_scoped_soft_archive_with_audit(tmp_path: Path) -> None:
    owner_headers = {
        **HEADERS,
        "X-Actor-ID": "task-owner",
        "X-Actor-Roles": "procurement_operator",
    }
    other_headers = {
        **HEADERS,
        "X-Actor-ID": "other-operator",
        "X-Actor-Roles": "procurement_operator",
    }
    with _client(tmp_path) as client:
        created = client.post(
            "/api/tasks/text",
            headers=owner_headers,
            json={"text": "DEMO-HYD-PUMP-001,hydraulic pump,1,item,EX200-A"},
        )
        task_id = created.json()["task_id"]

        assert client.delete(f"/api/tasks/{task_id}", headers=other_headers).status_code == 403
        assert client.delete(f"/api/tasks/{task_id}", headers=owner_headers).status_code == 204
        assert client.get(f"/api/tasks/{task_id}", headers=owner_headers).status_code == 404
        assert client.get("/api/tasks", headers=owner_headers).json()["items"] == []

        runtime = client.app.state.runtime
        with runtime.database.connect() as connection:
            archived = connection.execute(
                "SELECT deleted_at, deleted_by FROM procurement_tasks "
                "WHERE tenant_id=? AND task_id=?",
                (HEADERS["X-Tenant-ID"], task_id),
            ).fetchone()
            job = connection.execute(
                "SELECT status, last_error_class FROM work_queue "
                "WHERE tenant_id=? AND task_id=?",
                (HEADERS["X-Tenant-ID"], task_id),
            ).fetchone()
            event = connection.execute(
                "SELECT event_type FROM workflow_events "
                "WHERE tenant_id=? AND task_id=? ORDER BY sequence DESC LIMIT 1",
                (HEADERS["X-Tenant-ID"], task_id),
            ).fetchone()
        assert archived["deleted_at"]
        assert archived["deleted_by"] == "task-owner"
        assert job["status"] == "dead_letter"
        assert job["last_error_class"] == "TaskArchived"
        assert event["event_type"] == "task.archived"


def test_health_exposes_frontend_compatibility_contract(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["api_version"] == "0.5.0"
    assert payload["identity_api"] == "local-session-v1"


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


def test_api_memory_candidate_requires_confirmation_and_is_used_next_task(
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        created = client.post(
            "/api/tasks/text",
            headers=HEADERS,
            json={
                "text": (
                    "DEMO-HYD-PUMP-001,液压泵,1,件,EX200-A。"
                    "以后送货请安排在工作日上午"
                )
            },
        )
        task_id = created.json()["task_id"]
        _run_worker(client)
        memories = client.get("/api/memory", headers=HEADERS).json()["items"]
        assert len(memories) == 1
        assert memories[0]["status"] == "candidate"
        confirmed = client.post(
            f"/api/memory/{memories[0]['record_id']}/confirm",
            headers=HEADERS,
        )
        assert confirmed.status_code == 200

        second = client.post(
            "/api/tasks/text",
            headers=HEADERS,
            json={"text": "DEMO-FLT-KIT-001,保养滤芯包,1,套,SVC-2000H-A"},
        )
        second_id = second.json()["task_id"]
        _run_worker(client)
        detail = client.get(f"/api/tasks/{second_id}", headers=HEADERS).json()
        assert any(
            item["field_name"] == "memory.preferred_delivery_window"
            for item in detail["evidence"]
        )
        assert task_id != second_id


def test_api_governed_prompt_release_requires_eval_and_compliance(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        feedback = client.post(
            "/api/governance/feedback",
            headers=HEADERS,
            json={
                "feedback_type": "correction",
                "summary": "中文数量需要稳定转换",
                "correction": {"expected": "2"},
            },
        ).json()
        overview = client.get("/api/governance", headers=HEADERS).json()
        baseline_prompt = overview["active_prompt"]["prompt_text"]
        proposed = client.post(
            "/api/governance/prompt-candidates",
            headers=HEADERS,
            json={
                "candidate_version": "1.1.0-demo",
                "prompt_text": baseline_prompt + " Convert Chinese numbers exactly.",
                "feedback_ids": [feedback["feedback_id"]],
            },
        )
        assert proposed.status_code == 201
        candidate_id = proposed.json()["candidate_id"]
        assert client.post(
            f"/api/governance/prompt-candidates/{candidate_id}/evaluate",
            headers=HEADERS,
        ).json()["evaluation_passed"] is True

        operator_only = {**HEADERS, "X-Actor-Roles": "procurement_operator"}
        assert client.post(
            f"/api/governance/prompt-candidates/{candidate_id}/approve",
            headers=operator_only,
        ).status_code == 403
        assert client.post(
            f"/api/governance/prompt-candidates/{candidate_id}/approve",
            headers=APPROVER_HEADERS,
        ).status_code == 200
        release = client.post(
            f"/api/governance/prompt-candidates/{candidate_id}/release",
            headers=APPROVER_HEADERS,
        )
        assert release.status_code == 200
        assert client.get("/api/governance", headers=HEADERS).json()[
            "active_prompt"
        ]["prompt_version"] == "1.1.0-demo"


def test_api_deterministic_multi_agent_persists_diagnosable_trace(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        created = client.post(
            "/api/tasks/text",
            headers=HEADERS,
            json={
                "text": "DEMO-HYD-PUMP-001,液压泵,1,件,EX200-A",
                "architecture": "multi",
            },
        )
        assert created.status_code == 202
        task_id = created.json()["task_id"]
        outcome = _run_worker(client)["outcome"]
        assert outcome["architecture"] == "multi"
        assert outcome["specialist_messages"] == 4
        detail = client.get(f"/api/tasks/{task_id}", headers=HEADERS).json()
        traces = [
            event for event in detail["events"] if event["event_type"] == "supervisor.trace"
        ]
        assert traces[0]["payload"]["architecture"] == "multi"
        assert len(traces[0]["payload"]["messages"]) == 4


def test_api_model_multi_agent_requires_explicit_live_model_opt_in(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("PROCUREOPS_ENABLE_LIVE_MODELS", "0")
    with _client(tmp_path) as client:
        response = client.post(
            "/api/tasks/text",
            headers=HEADERS,
            json={
                "text": "DEMO-HYD-PUMP-001,液压泵,1,件,EX200-A",
                "architecture": "multi_llm",
            },
        )

        assert response.status_code == 409
        assert "PROCUREOPS_ENABLE_LIVE_MODELS=1" in response.json()["detail"]


def test_api_lists_and_runs_second_tenant_with_server_side_membership(
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        tenants = client.get("/api/tenants")
        assert tenants.status_code == 200
        assert {item["tenant_id"] for item in tenants.json()["items"]} == {
            "tenant_engineering_machinery",
            "tenant_enterprise_it",
            "tenant_commerce_ops",
        }
        buyer_session = client.post(
            "/api/auth/local-session",
            json={"user_id": "local-buyer", "tenant_id": "tenant_enterprise_it"},
        ).json()
        buyer_headers = {"Authorization": f"Bearer {buyer_session['token']}"}
        created = client.post(
            "/api/tasks/text",
            headers=buyer_headers,
            json={"text": "IT-LAP-DEV-14 | 研发笔记本 | 2 | 台"},
        )
        assert created.status_code == 202
        task_id = created.json()["task_id"]
        processed = client.post("/api/admin/worker/run-once", headers=buyer_headers)
        assert processed.json()["outcome"]["task_status"] == "awaiting_approval"

        approver_session = client.post(
            "/api/auth/local-session",
            json={"user_id": "local-approver", "tenant_id": "tenant_enterprise_it"},
        ).json()
        approver_headers = {"Authorization": f"Bearer {approver_session['token']}"}
        approved = client.post(
            f"/api/tasks/{task_id}/approval",
            headers=approver_headers,
            json={"decision": "approve"},
        )
        assert approved.status_code == 202
        completed = client.post("/api/admin/worker/run-once", headers=approver_headers)
        assert completed.json()["outcome"]["task_status"] == "completed"
        detail = client.get(f"/api/tasks/{task_id}", headers=buyer_headers).json()
        assert detail["po_draft"]["tenant_id"] == "tenant_enterprise_it"
