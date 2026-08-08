from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from procureops.api import create_app

PROJECT_ROOT = Path(__file__).resolve().parents[2]
def _bearer(token: str, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **extra}


def _local_session(client: TestClient, user_id: str) -> str:
    response = client.post(
        "/api/auth/local-session",
        json={
            "user_id": user_id,
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
        home = client.get("/").text
        assert 'type="password"' not in home
        assert "本机演示不需要密码" in home
        assert client.post(
            "/api/auth/login",
            json={"email": "buyer@procureops.local", "password": "unused"},
        ).status_code == 404
        connection = app.state.runtime.database.connect()
        try:
            local_user_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(local_users)").fetchall()
            }
        finally:
            connection.close()
        assert "password_hash" not in local_user_columns
        assert "password_salt" not in local_user_columns

        spoofed = {
            "X-Tenant-ID": "tenant_engineering_machinery",
            "X-Actor-ID": "spoofed-admin",
            "X-Actor-Roles": "compliance_approver",
        }
        assert client.get("/api/tasks", headers=spoofed).status_code == 401

        buyer_token = _local_session(client, "local-buyer")
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

        approver_token = _local_session(client, "local-approver")
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
