from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from procureops.api import create_app

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEMO_PASSWORD = "ProcureOps-Demo-2026!"


def _bearer(token: str, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **extra}


def _login(client: TestClient, email: str) -> str:
    response = client.post(
        "/api/auth/login",
        json={
            "email": email,
            "password": DEMO_PASSWORD,
            "tenant_id": "tenant_engineering_machinery",
        },
    )
    assert response.status_code == 200
    return response.json()["token"]


def test_server_side_identity_maker_checker_and_outbox_recovery(tmp_path: Path) -> None:
    app = create_app(
        project_root=PROJECT_ROOT,
        database_path=tmp_path / "identity.sqlite3",
        var_root=tmp_path / "var",
    )
    with TestClient(app) as client:
        spoofed = {
            "X-Tenant-ID": "tenant_engineering_machinery",
            "X-Actor-ID": "spoofed-admin",
            "X-Actor-Roles": "compliance_approver",
        }
        assert client.get("/api/tasks", headers=spoofed).status_code == 401

        buyer_token = _login(client, "buyer@procureops.local")
        buyer_headers = _bearer(
            buyer_token,
            **{"X-Actor-Roles": "compliance_approver"},
        )
        identity = client.get("/api/auth/me", headers=buyer_headers).json()
        assert identity["actor_id"] == "local-buyer"
        assert identity["roles"] == ["procurement_operator"]

        created = client.post(
            "/api/tasks/text",
            headers=buyer_headers,
            json={"text": "DEMO-HYD-PUMP-001,液压泵,1,件,EX200-A"},
        )
        assert created.status_code == 202
        task_id = created.json()["task_id"]
        outbox_event_id = created.json()["outbox_event_id"]
        outbox = client.get("/api/admin/outbox", headers=buyer_headers).json()["items"]
        assert outbox[-1]["status"] == "dispatched"

        runtime = app.state.runtime
        with runtime.database.transaction() as connection:
            connection.execute(
                "UPDATE outbox_events SET status='dispatching' WHERE event_id=?",
                (outbox_event_id,),
            )
        runtime.outbox.dispatch_pending()
        assert len(
            runtime.queue.jobs_for_task(
                tenant_id="tenant_engineering_machinery",
                task_id=task_id,
            )
        ) == 1

        processed = client.post(
            "/api/admin/worker/run-once",
            headers=buyer_headers,
        ).json()
        assert processed["outcome"]["task_status"] == "awaiting_approval"
        assert client.post(
            f"/api/tasks/{task_id}/approval",
            headers=buyer_headers,
            json={"decision": "approve"},
        ).status_code == 403

        approver_token = _login(client, "approver@procureops.local")
        approved = client.post(
            f"/api/tasks/{task_id}/approval",
            headers=_bearer(approver_token),
            json={"decision": "approve"},
        )
        assert approved.status_code == 202
        completed = client.post(
            "/api/admin/worker/run-once",
            headers=buyer_headers,
        ).json()
        assert completed["outcome"]["task_status"] == "completed"

        assert client.post("/api/auth/logout", headers=buyer_headers).status_code == 204
        assert client.get("/api/auth/me", headers=buyer_headers).status_code == 401
