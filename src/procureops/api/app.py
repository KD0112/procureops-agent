from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from procureops.agents.single import WorkflowResult
from procureops.domain.enums import TaskStatus
from procureops.domain.policy import ApprovalRequirement
from procureops.evals.replay import ReplayStore
from procureops.runtime import ProcureOpsRuntime
from procureops.worker.service import ProcureOpsWorker

DEFAULT_TENANT = "tenant_engineering_machinery"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
STATIC_ROOT = Path(__file__).with_name("static")


@dataclass(frozen=True, slots=True)
class Actor:
    tenant_id: str
    actor_id: str
    roles: frozenset[str]


class TextTaskRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)


class AnswerRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)


class ApprovalDecision(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    reason: str | None = Field(default=None, max_length=500)


def actor_from_headers(
    x_tenant_id: Annotated[str, Header()] = DEFAULT_TENANT,
    x_actor_id: Annotated[str, Header()] = "local-buyer",
    x_actor_roles: Annotated[str, Header()] = "procurement_operator",
) -> Actor:
    roles = frozenset(role.strip() for role in x_actor_roles.split(",") if role.strip())
    if not roles:
        raise HTTPException(status_code=400, detail="at least one actor role is required")
    return Actor(tenant_id=x_tenant_id.strip(), actor_id=x_actor_id.strip(), roles=roles)


def create_app(
    *,
    project_root: Path = PROJECT_ROOT,
    database_path: Path | None = None,
    var_root: Path | None = None,
) -> FastAPI:
    runtime = ProcureOpsRuntime.create(
        project_root=project_root,
        database_path=database_path,
        var_root=var_root,
    )
    app = FastAPI(
        title="ProcureOps Agent API",
        version="0.2.0",
        description="Task-first local procurement workbench backed by the governed Harness.",
    )
    app.state.runtime = runtime
    app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")

    @app.get("/", include_in_schema=False)
    def workbench() -> FileResponse:
        return FileResponse(STATIC_ROOT / "index.html")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "storage": "sqlite", "worker": "durable-local"}

    @app.post("/api/tasks/text", status_code=status.HTTP_202_ACCEPTED)
    def create_text_task(
        request: TextTaskRequest,
        actor: Annotated[Actor, Depends(actor_from_headers)],
    ) -> dict[str, Any]:
        task_id = str(uuid4())
        runtime.repository.create_task(
            tenant_id=actor.tenant_id,
            created_by=actor.actor_id,
            request={"source_type": "text", "preview": request.text[:200]},
            workflow_version="1.0.0",
            task_id=task_id,
        )
        job, reused = runtime.queue.enqueue(
            tenant_id=actor.tenant_id,
            task_id=task_id,
            job_type="process_intake",
            payload=_job_payload(
                actor=actor,
                source={"kind": "text", "text": request.text, "artifact_id": task_id},
            ),
            idempotency_key=f"intake:{task_id}:v1",
        )
        return {"task_id": task_id, "job_id": job.job_id, "reused": reused}

    @app.post("/api/tasks/upload", status_code=status.HTTP_202_ACCEPTED)
    async def create_upload_task(
        file: Annotated[UploadFile, File()],
        actor: Annotated[Actor, Depends(actor_from_headers)],
    ) -> dict[str, Any]:
        task_id = str(uuid4())
        data = await file.read(runtime.blobs.max_bytes + 1)
        if len(data) > runtime.blobs.max_bytes:
            raise HTTPException(status_code=413, detail="upload exceeds 10 MiB")
        runtime.repository.create_task(
            tenant_id=actor.tenant_id,
            created_by=actor.actor_id,
            request={"source_type": "upload", "filename": file.filename},
            workflow_version="1.0.0",
            task_id=task_id,
        )
        try:
            stored = runtime.blobs.save(
                tenant_id=actor.tenant_id,
                task_id=task_id,
                filename=file.filename or "upload.bin",
                content_type=file.content_type or "application/octet-stream",
                data=data,
            )
        except ValueError as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        upload_id = runtime.repository.add_upload(
            tenant_id=actor.tenant_id,
            task_id=task_id,
            original_filename=stored.original_filename,
            storage_key=stored.storage_key,
            content_type=stored.content_type,
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
        )
        job, reused = runtime.queue.enqueue(
            tenant_id=actor.tenant_id,
            task_id=task_id,
            job_type="process_intake",
            payload=_job_payload(
                actor=actor,
                source={"kind": "upload", "storage_key": stored.storage_key},
            ),
            idempotency_key=f"intake:{task_id}:v1",
        )
        return {
            "task_id": task_id,
            "job_id": job.job_id,
            "upload_id": upload_id,
            "sha256": stored.sha256,
            "reused": reused,
        }

    @app.get("/api/tasks")
    def list_tasks(
        actor: Annotated[Actor, Depends(actor_from_headers)],
        limit: int = 100,
    ) -> dict[str, Any]:
        safe_limit = min(max(limit, 1), 200)
        rows = runtime.repository.list_tasks(tenant_id=actor.tenant_id, limit=safe_limit)
        return {"items": [_clean_task_row(row) for row in rows]}

    @app.get("/api/tasks/{task_id}")
    def task_detail(
        task_id: str,
        actor: Annotated[Actor, Depends(actor_from_headers)],
    ) -> dict[str, Any]:
        return _task_detail(runtime, actor=actor, task_id=task_id)

    @app.post("/api/tasks/{task_id}/answers", status_code=status.HTTP_202_ACCEPTED)
    def answer_task(
        task_id: str,
        request: AnswerRequest,
        actor: Annotated[Actor, Depends(actor_from_headers)],
    ) -> dict[str, Any]:
        task = _get_task(runtime, actor=actor, task_id=task_id)
        if TaskStatus(task.status) is not TaskStatus.NEEDS_INPUT:
            raise HTTPException(status_code=409, detail="task is not waiting for input")
        next_version = task.version + 1
        job, reused = runtime.queue.enqueue(
            tenant_id=actor.tenant_id,
            task_id=task_id,
            job_type="process_intake",
            payload=_job_payload(
                actor=actor,
                source={
                    "kind": "text",
                    "text": request.text,
                    "artifact_id": f"answer-{task_id}-{next_version}",
                },
            ),
            idempotency_key=f"answer:{task_id}:v{next_version}",
        )
        runtime.repository.append_workflow_event(
            tenant_id=actor.tenant_id,
            task_id=task_id,
            event_type="user.input_supplied",
            payload={"actor_id": actor.actor_id, "answer_length": len(request.text)},
        )
        return {"task_id": task_id, "job_id": job.job_id, "reused": reused}

    @app.post("/api/tasks/{task_id}/approval", status_code=status.HTTP_202_ACCEPTED)
    def decide_approval(
        task_id: str,
        request: ApprovalDecision,
        actor: Annotated[Actor, Depends(actor_from_headers)],
    ) -> dict[str, Any]:
        task = _get_task(runtime, actor=actor, task_id=task_id)
        if TaskStatus(task.status) is not TaskStatus.AWAITING_APPROVAL:
            raise HTTPException(status_code=409, detail="task is not awaiting approval")
        if request.decision == "reject":
            rejected = runtime.repository.transition_task(
                tenant_id=actor.tenant_id,
                task_id=task_id,
                target=TaskStatus.FAILED_TERMINAL,
                expected_version=task.version,
            )
            runtime.repository.append_workflow_event(
                tenant_id=actor.tenant_id,
                task_id=task_id,
                event_type="approval.rejected",
                payload={
                    "actor_id": actor.actor_id,
                    "roles": sorted(actor.roles),
                    "reason": request.reason,
                },
            )
            return {"task_id": task_id, "status": rejected.status, "decision": "reject"}

        approval_event = _latest_approval_event(runtime, actor=actor, task_id=task_id)
        approval_payload = json.loads(approval_event["payload_json"])
        requirement = ApprovalRequirement.model_validate(approval_payload["approval_requirement"])
        result = WorkflowResult(
            task_id=task_id,
            status=TaskStatus.AWAITING_APPROVAL,
            approval_requirement=requirement,
            approval_subject=approval_payload["tool_arguments"],
        )
        context = runtime.context(
            tenant_id=actor.tenant_id,
            task_id=task_id,
            actor_id=actor.actor_id,
            actor_roles=actor.roles,
            run_id=f"approval-{uuid4().hex}",
            correlation_id=f"approval-{task_id}",
        )
        try:
            grant = runtime.agent(audit=_NullAudit()).issue_approval(
                context=context,
                result=result,
                approved_by=actor.actor_id,
                approved_by_roles=actor.roles,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        job, reused = runtime.queue.enqueue(
            tenant_id=actor.tenant_id,
            task_id=task_id,
            job_type="resume_approval",
            payload={
                "actor_id": "local-worker",
                "actor_roles": ["procurement_operator"],
                "approval": grant.model_dump(mode="json"),
            },
            idempotency_key=f"approval:{grant.approval_id}",
        )
        return {
            "task_id": task_id,
            "job_id": job.job_id,
            "approval_id": grant.approval_id,
            "reused": reused,
        }

    @app.post("/api/admin/worker/run-once")
    def run_worker_once(
        actor: Annotated[Actor, Depends(actor_from_headers)],
    ) -> dict[str, Any]:
        if "procurement_operator" not in actor.roles:
            raise HTTPException(status_code=403, detail="procurement_operator role required")
        outcome = ProcureOpsWorker(runtime=runtime, worker_id="api-local-worker").run_once()
        return {"processed": outcome is not None, "outcome": outcome}

    @app.get("/api/tasks/{task_id}/po")
    def task_po(
        task_id: str,
        actor: Annotated[Actor, Depends(actor_from_headers)],
    ) -> dict[str, Any]:
        _get_task(runtime, actor=actor, task_id=task_id)
        draft = runtime.repository.po_draft_for_task(tenant_id=actor.tenant_id, task_id=task_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="PO draft not found")
        draft["payload"] = json.loads(draft.pop("payload_json"))
        return draft

    @app.get("/api/tasks/{task_id}/audit")
    def task_audit(
        task_id: str,
        actor: Annotated[Actor, Depends(actor_from_headers)],
    ) -> dict[str, Any]:
        _get_task(runtime, actor=actor, task_id=task_id)
        events = []
        if runtime.audit_path.exists():
            for line in runtime.audit_path.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                if event.get("tenant_id") == actor.tenant_id and event.get("task_id") == task_id:
                    events.append(event)
        return {"items": events}

    @app.get("/api/replays/{run_id}")
    def replay(
        run_id: str,
        actor: Annotated[Actor, Depends(actor_from_headers)],
    ) -> dict[str, Any]:
        if not run_id.replace("-", "").replace("_", "").isalnum():
            raise HTTPException(status_code=400, detail="invalid run id")
        path = runtime.replay_root / f"{run_id}.json"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="replay not found")
        envelope = json.loads(path.read_text(encoding="utf-8"))
        bundle = envelope.get("bundle", {})
        if bundle.get("context", {}).get("tenant_id") != actor.tenant_id:
            raise HTTPException(status_code=404, detail="replay not found")
        return {**envelope, "verified": ReplayStore.verify(path)}

    @app.get("/api/evaluations/latest")
    def latest_evaluations(
        actor: Annotated[Actor, Depends(actor_from_headers)],
    ) -> dict[str, Any]:
        if "procurement_operator" not in actor.roles:
            raise HTTPException(status_code=403, detail="procurement_operator role required")
        reports = project_root / "reports"
        names = (
            "latest_single_summary.json",
            "latest_multi_summary.json",
            "latest_ab_comparison.json",
            "latest_live_model_eval.json",
            "latest_live_vision_smoke.json",
        )
        return {
            name.removesuffix(".json"): json.loads((reports / name).read_text(encoding="utf-8"))
            for name in names
            if (reports / name).is_file()
        }

    return app


def _job_payload(*, actor: Actor, source: dict[str, Any]) -> dict[str, Any]:
    return {
        "actor_id": actor.actor_id,
        "actor_roles": sorted(actor.roles),
        "source": source,
        "run_id": f"api-{uuid4().hex}",
    }


def _get_task(runtime: ProcureOpsRuntime, *, actor: Actor, task_id: str):
    try:
        return runtime.repository.get_task(tenant_id=actor.tenant_id, task_id=task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc


def _clean_task_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "request_json"}


def _latest_approval_event(
    runtime: ProcureOpsRuntime, *, actor: Actor, task_id: str
) -> dict[str, Any]:
    matching = [
        event
        for event in runtime.repository.workflow_events(tenant_id=actor.tenant_id, task_id=task_id)
        if event["event_type"] == "approval.requested"
    ]
    if not matching:
        raise HTTPException(status_code=409, detail="approval request event not found")
    return matching[-1]


def _task_detail(runtime: ProcureOpsRuntime, *, actor: Actor, task_id: str) -> dict[str, Any]:
    task = _get_task(runtime, actor=actor, task_id=task_id)
    events = runtime.repository.workflow_events(tenant_id=actor.tenant_id, task_id=task_id)
    normalized_events = [
        {**event, "payload": json.loads(event["payload_json"])} for event in events
    ]
    for event in normalized_events:
        event.pop("payload_json", None)
    approval = next(
        (
            event["payload"]
            for event in reversed(normalized_events)
            if event["event_type"] == "approval.requested"
        ),
        None,
    )
    po = runtime.repository.po_draft_for_task(tenant_id=actor.tenant_id, task_id=task_id)
    if po is not None:
        po = {**po, "payload": json.loads(po["payload_json"])}
        po.pop("payload_json", None)
    return {
        "task": task.model_dump(mode="json"),
        "items": runtime.repository.task_items(tenant_id=actor.tenant_id, task_id=task_id),
        "evidence": runtime.repository.evidence_for_task(
            tenant_id=actor.tenant_id, task_id=task_id
        ),
        "events": normalized_events,
        "uploads": runtime.repository.uploads_for_task(tenant_id=actor.tenant_id, task_id=task_id),
        "jobs": [
            job.model_dump(mode="json")
            for job in runtime.queue.jobs_for_task(tenant_id=actor.tenant_id, task_id=task_id)
        ],
        "pending_approval": approval if task.status == TaskStatus.AWAITING_APPROVAL else None,
        "po_draft": po,
    }


class _NullAudit:
    def append(self, _event: Any) -> None:
        return None


app = create_app()
