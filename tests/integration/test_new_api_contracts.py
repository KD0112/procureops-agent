from pathlib import Path

from fastapi.testclient import TestClient

from procureops.api import create_app
from procureops.worker.service import ProcureOpsWorker

PROJECT_ROOT = Path(__file__).resolve().parents[2]
HEADERS = {
    "X-Tenant-Id": "tenant_engineering_machinery",
    "X-Actor-Id": "local-buyer",
    "X-Actor-Roles": "procurement_operator",
}


def test_chat_search_readiness_and_skill_contracts(tmp_path: Path) -> None:
    app = create_app(
        project_root=PROJECT_ROOT,
        database_path=tmp_path / "api.sqlite3",
        var_root=tmp_path / "var",
        allow_header_auth=True,
    )
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["cache_backend"] == "memory"

        readiness = client.get("/api/readiness")
        assert readiness.status_code == 200
        assert readiness.json()["checks"]["mysql"]["status"] == "not_configured"

        chat = client.post("/api/chat", headers=HEADERS, json={"text": "请采购一个液压泵"})
        assert chat.status_code == 202
        assert chat.json()["session_id"]

        search = client.post("/api/search", headers=HEADERS, json={"query": "液压泵", "top_k": 3})
        assert search.status_code == 200
        assert search.json()["cache"] in {"hit", "miss"}

        skills = client.get("/api/skills")
        assert skills.status_code == 200
        assert "procurement_evidence" in skills.json()["items"]
        skill_result = client.post(
            "/api/skills/procurement-evidence",
            headers=HEADERS,
            json={"query": "no-such-product", "quantity": "1"},
        )
        assert skill_result.status_code == 200
        assert skill_result.json()["result"]["status"] in {"no_match", "needs_input", "matched"}

        repo_skill = client.post(
            "/api/skills/repo-change-review",
            headers=HEADERS,
            json={
                "description": "检查项目源码是否可以编译",
                "files_to_read": ["README.md"],
                "test_command": "python -m compileall -q src",
            },
        )
        assert repo_skill.status_code == 200
        assert repo_skill.json()["result"]["status"] == "passed"
        assert repo_skill.json()["result"]["workspace_id"].startswith("code-task-")

        document = client.post(
            "/api/documents",
            headers=HEADERS,
            files={
                "file": (
                    "manual.md",
                    b"# Delivery\nDelivery deadline is 2026-09-30.",
                    "text/markdown",
                )
            },
        )
        assert document.status_code == 202
        assert document.json()["pipeline"] == "document_to_rag"
        worker = ProcureOpsWorker(runtime=app.state.runtime, worker_id="test-rag-worker")
        outcome = None
        for _ in range(3):
            candidate = worker.run_once()
            if candidate and candidate.get("task_id") == document.json()["task_id"]:
                outcome = candidate
                break
        assert outcome is not None
        assert outcome["task_status"] == "staged_for_approval"


def test_ci_repair_skill_returns_diagnosis_diff_and_approval_gate(tmp_path: Path) -> None:
    app = create_app(
        project_root=PROJECT_ROOT,
        database_path=tmp_path / "api.sqlite3",
        var_root=tmp_path / "var",
        allow_header_auth=True,
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/skills/repo-ci-repair",
            headers=HEADERS,
            json={
                "description": "repair a failing CI check in an isolated workspace",
                "ci_output": "FAILED tests/test_hello.py::test_value - AssertionError",
                "files_to_read": ["README.md"],
                "proposed_writes": {"docs/ci-repair-demo.md": "CI repair candidate\n"},
                "test_command": "python -m compileall -q src",
                "commit_requested": True,
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["skill"] == "repo_ci_repair"
        result = payload["result"]
        assert result["status"] == "needs_approval"
        assert result["diagnosis"]["failure_kind"] == "test_failure"
        assert result["workflow"][-1] == "human_approval_gate"
        assert result["diff_sha256"]
        assert "docs/ci-repair-demo.md" in result["files_changed"]
