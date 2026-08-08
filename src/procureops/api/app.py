from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from procureops.agents.single import WorkflowResult
from procureops.domain.enums import TaskStatus
from procureops.domain.policy import ApprovalRequirement
from procureops.evals.replay import ReplayStore
from procureops.harness.provider_clients import (
    model_configuration_status,
    model_routes_from_environment,
)
from procureops.intake.prompts import PROMPT_SCOPE_TEXT_INTAKE
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
    architecture: str = Field(default="single", pattern="^(single|multi|multi_llm)$")


class AnswerRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)


class ApprovalDecision(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    reason: str | None = Field(default=None, max_length=500)


class MemoryProposalRequest(BaseModel):
    memory_key: str = Field(min_length=1, max_length=100)
    value: Any
    confidence: float = Field(default=1.0, ge=0, le=1)


class MemoryCorrectionRequest(BaseModel):
    value: Any


class FeedbackRequest(BaseModel):
    feedback_type: str = Field(pattern="^(correction|preference|failure|rating)$")
    summary: str = Field(min_length=1, max_length=2_000)
    correction: dict[str, Any] = Field(default_factory=dict)
    task_id: str | None = None


class PromptCandidateRequest(BaseModel):
    candidate_version: str = Field(min_length=1, max_length=100)
    prompt_text: str = Field(min_length=1, max_length=8_000)
    feedback_ids: tuple[str, ...] = Field(min_length=1, max_length=100)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)
    tenant_id: str = DEFAULT_TENANT


def actor_from_headers(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_tenant_id: Annotated[str | None, Header()] = None,
    x_actor_id: Annotated[str | None, Header()] = None,
    x_actor_roles: Annotated[str | None, Header()] = None,
) -> Actor:
    if authorization and authorization.casefold().startswith("bearer "):
        try:
            identity = request.app.state.runtime.auth.resolve(
                token=authorization.split(" ", 1)[1].strip()
            )
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        return Actor(
            tenant_id=identity.tenant_id,
            actor_id=identity.user_id,
            roles=identity.roles,
        )
    if request.app.state.allow_header_auth and all(
        (x_tenant_id, x_actor_id, x_actor_roles)
    ):
        roles = frozenset(
            role.strip() for role in str(x_actor_roles).split(",") if role.strip()
        )
        if roles:
            return Actor(
                tenant_id=str(x_tenant_id).strip(),
                actor_id=str(x_actor_id).strip(),
                roles=roles,
            )
    raise HTTPException(status_code=401, detail="bearer session required")


def create_app(
    *,
    project_root: Path = PROJECT_ROOT,
    database_path: Path | None = None,
    var_root: Path | None = None,
    allow_header_auth: bool = False,
) -> FastAPI:
    runtime = ProcureOpsRuntime.create(
        project_root=project_root,
        database_path=database_path,
        var_root=var_root,
    )
    app = FastAPI(
        title="ProcureOps Agent API",
        version="0.4.0",
        description=(
            "Task-first procurement workbench with governed memory, evolution, "
            "multi-agent experiments, and Harness controls."
        ),
    )
    app.state.runtime = runtime
    app.state.allow_header_auth = allow_header_auth
    app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")

    @app.get("/", include_in_schema=False)
    def workbench() -> FileResponse:
        return FileResponse(STATIC_ROOT / "index.html")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "storage": "sqlite", "worker": "durable-local"}

    @app.post("/api/auth/login")
    def login(request: LoginRequest) -> dict[str, Any]:
        try:
            session = runtime.auth.login(
                email=request.email,
                password=request.password,
                tenant_id=request.tenant_id,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        return session.model_dump(mode="json")

    @app.get("/api/auth/me")
    def current_identity(
        actor: Annotated[Actor, Depends(actor_from_headers)],
    ) -> dict[str, Any]:
        return {
            "tenant_id": actor.tenant_id,
            "actor_id": actor.actor_id,
            "roles": sorted(actor.roles),
        }

    @app.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
    def logout(
        authorization: Annotated[str, Header()],
        _actor: Annotated[Actor, Depends(actor_from_headers)],
    ) -> None:
        runtime.auth.logout(token=authorization.split(" ", 1)[1].strip())

    @app.post("/api/tasks/text", status_code=status.HTTP_202_ACCEPTED)
    def create_text_task(
        request: TextTaskRequest,
        actor: Annotated[Actor, Depends(actor_from_headers)],
    ) -> dict[str, Any]:
        _ensure_architecture_ready(request.architecture)
        task_id = str(uuid4())
        job_payload = _job_payload(
            actor=actor,
            source={"kind": "text", "text": request.text, "artifact_id": task_id},
            architecture=request.architecture,
        )
        _task, outbox_event_id, _upload_id = runtime.repository.create_task_with_outbox(
            tenant_id=actor.tenant_id,
            created_by=actor.actor_id,
            request={
                "source_type": "text",
                "preview": request.text[:200],
                "architecture": request.architecture,
            },
            workflow_version="1.0.0",
            task_id=task_id,
            job_payload=job_payload,
            idempotency_key=f"intake:{task_id}:v1",
        )
        job = runtime.outbox.dispatch(event_id=outbox_event_id)
        if job is None:
            raise HTTPException(status_code=503, detail="outbox delivery is pending")
        return {
            "task_id": task_id,
            "job_id": job.job_id,
            "outbox_event_id": outbox_event_id,
            "reused": False,
        }

    @app.post("/api/tasks/upload", status_code=status.HTTP_202_ACCEPTED)
    async def create_upload_task(
        file: Annotated[UploadFile, File()],
        actor: Annotated[Actor, Depends(actor_from_headers)],
        architecture: Annotated[str, Form()] = "single",
    ) -> dict[str, Any]:
        _validate_architecture(architecture)
        _ensure_architecture_ready(architecture)
        task_id = str(uuid4())
        data = await file.read(runtime.blobs.max_bytes + 1)
        if len(data) > runtime.blobs.max_bytes:
            raise HTTPException(status_code=413, detail="upload exceeds 10 MiB")
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
        job_payload = _job_payload(
            actor=actor,
            source={"kind": "upload", "storage_key": stored.storage_key},
            architecture=architecture,
        )
        _task, outbox_event_id, upload_id = runtime.repository.create_task_with_outbox(
            tenant_id=actor.tenant_id,
            created_by=actor.actor_id,
            request={
                "source_type": "upload",
                "filename": file.filename,
                "architecture": architecture,
            },
            workflow_version="1.0.0",
            task_id=task_id,
            job_payload=job_payload,
            idempotency_key=f"intake:{task_id}:v1",
            upload={
                "original_filename": stored.original_filename,
                "storage_key": stored.storage_key,
                "content_type": stored.content_type,
                "size_bytes": stored.size_bytes,
                "sha256": stored.sha256,
            },
        )
        job = runtime.outbox.dispatch(event_id=outbox_event_id)
        if job is None:
            raise HTTPException(status_code=503, detail="outbox delivery is pending")
        return {
            "task_id": task_id,
            "job_id": job.job_id,
            "upload_id": upload_id,
            "outbox_event_id": outbox_event_id,
            "sha256": stored.sha256,
            "reused": False,
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
        outbox_event_id, reused = runtime.outbox.stage_work(
            tenant_id=actor.tenant_id,
            task_id=task_id,
            job_type="process_intake",
            job_payload=_job_payload(
                actor=actor,
                source={
                    "kind": "text",
                    "text": request.text,
                    "artifact_id": f"answer-{task_id}-{next_version}",
                },
                architecture=str(task.request.get("architecture", "single")),
            ),
            idempotency_key=f"answer:{task_id}:v{next_version}",
        )
        job = runtime.outbox.dispatch(event_id=outbox_event_id)
        if job is None:
            raise HTTPException(status_code=503, detail="outbox delivery is pending")
        runtime.repository.append_workflow_event(
            tenant_id=actor.tenant_id,
            task_id=task_id,
            event_type="user.input_supplied",
            payload={"actor_id": actor.actor_id, "answer_length": len(request.text)},
        )
        return {
            "task_id": task_id,
            "job_id": job.job_id,
            "outbox_event_id": outbox_event_id,
            "reused": reused,
        }

    @app.post("/api/tasks/{task_id}/approval", status_code=status.HTTP_202_ACCEPTED)
    def decide_approval(
        task_id: str,
        request: ApprovalDecision,
        actor: Annotated[Actor, Depends(actor_from_headers)],
    ) -> dict[str, Any]:
        task = _get_task(runtime, actor=actor, task_id=task_id)
        if TaskStatus(task.status) is not TaskStatus.AWAITING_APPROVAL:
            raise HTTPException(status_code=409, detail="task is not awaiting approval")
        if runtime.repository.task_created_by(
            tenant_id=actor.tenant_id,
            task_id=task_id,
        ) == actor.actor_id:
            raise HTTPException(
                status_code=403,
                detail="maker-checker violation: task creator cannot approve or reject",
            )
        approval_event = _latest_approval_event(runtime, actor=actor, task_id=task_id)
        approval_payload = json.loads(approval_event["payload_json"])
        requirement = ApprovalRequirement.model_validate(
            approval_payload["approval_requirement"]
        )
        if not requirement.required_roles.issubset(actor.roles):
            raise HTTPException(status_code=403, detail="required approval role missing")
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
        outbox_event_id, reused = runtime.outbox.stage_work(
            tenant_id=actor.tenant_id,
            task_id=task_id,
            job_type="resume_approval",
            job_payload={
                "actor_id": "local-worker",
                "actor_roles": ["procurement_operator"],
                "architecture": str(task.request.get("architecture", "single")),
                "approval": grant.model_dump(mode="json"),
            },
            idempotency_key=f"approval:{grant.approval_id}",
        )
        job = runtime.outbox.dispatch(event_id=outbox_event_id)
        if job is None:
            raise HTTPException(status_code=503, detail="outbox delivery is pending")
        return {
            "task_id": task_id,
            "job_id": job.job_id,
            "approval_id": grant.approval_id,
            "outbox_event_id": outbox_event_id,
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

    @app.get("/api/admin/outbox")
    def outbox_status(
        actor: Annotated[Actor, Depends(actor_from_headers)],
    ) -> dict[str, Any]:
        _require_role(actor, "procurement_operator")
        return {"items": list(runtime.outbox.events(tenant_id=actor.tenant_id))}

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
            "latest_llm_multi_summary.json",
            "latest_llm_ab_comparison.json",
            "latest_live_model_eval.json",
            "latest_live_vision_smoke.json",
        )
        return {
            name.removesuffix(".json"): json.loads((reports / name).read_text(encoding="utf-8"))
            for name in names
            if (reports / name).is_file()
        }

    @app.get("/api/models/status")
    def model_status(
        actor: Annotated[Actor, Depends(actor_from_headers)],
    ) -> dict[str, Any]:
        _require_role(actor, "procurement_operator")
        return {
            "live_models_enabled": _live_models_enabled(),
            "text": model_configuration_status(kind="text"),
            "vision": model_configuration_status(kind="vision"),
            "routes": {
                kind: _public_model_routes(kind)
                for kind in ("text", "vision")
            },
            "qwen_switch": {
                "text_provider_value": "qwen",
                "vision_provider_value": "qwen",
                "required_secret": "DASHSCOPE_API_KEY",
            },
        }

    @app.get("/api/memory")
    def list_memory(
        actor: Annotated[Actor, Depends(actor_from_headers)],
        memory_status: str | None = None,
    ) -> dict[str, Any]:
        records = runtime.memory.list_records(
            tenant_id=actor.tenant_id,
            user_id=actor.actor_id,
            status=memory_status,
        )
        return {
            "items": [item.model_dump(mode="json") for item in records],
            "access_events": list(
                runtime.memory.access_events(
                    tenant_id=actor.tenant_id,
                    user_id=actor.actor_id,
                )
            ),
        }

    @app.post("/api/memory/candidates", status_code=status.HTTP_201_CREATED)
    def propose_memory(
        request: MemoryProposalRequest,
        actor: Annotated[Actor, Depends(actor_from_headers)],
    ) -> dict[str, Any]:
        try:
            record = runtime.memory.propose(
                tenant_id=actor.tenant_id,
                user_id=actor.actor_id,
                memory_key=request.memory_key,
                value=request.value,
                confidence=request.confidence,
                proposed_by=actor.actor_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return record.model_dump(mode="json")

    @app.post("/api/memory/{record_id}/confirm")
    def confirm_memory(
        record_id: str,
        actor: Annotated[Actor, Depends(actor_from_headers)],
    ) -> dict[str, Any]:
        try:
            record = runtime.memory.confirm(
                tenant_id=actor.tenant_id,
                user_id=actor.actor_id,
                record_id=record_id,
                confirmed_by=actor.actor_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return record.model_dump(mode="json")

    @app.patch("/api/memory/{record_id}")
    def correct_memory(
        record_id: str,
        request: MemoryCorrectionRequest,
        actor: Annotated[Actor, Depends(actor_from_headers)],
    ) -> dict[str, Any]:
        try:
            record = runtime.memory.correct(
                tenant_id=actor.tenant_id,
                user_id=actor.actor_id,
                record_id=record_id,
                new_value=request.value,
                corrected_by=actor.actor_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return record.model_dump(mode="json")

    @app.delete("/api/memory/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_memory(
        record_id: str,
        actor: Annotated[Actor, Depends(actor_from_headers)],
    ) -> None:
        try:
            runtime.memory.delete(
                tenant_id=actor.tenant_id,
                user_id=actor.actor_id,
                record_id=record_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/governance")
    def governance_overview(
        actor: Annotated[Actor, Depends(actor_from_headers)],
    ) -> dict[str, Any]:
        active = runtime.evolution.bootstrap_baseline(tenant_id=actor.tenant_id)
        return {
            "active_prompt": active.model_dump(mode="json"),
            "feedback": [
                item.model_dump(mode="json")
                for item in runtime.evolution.list_feedback(tenant_id=actor.tenant_id)
            ],
            "candidates": [
                item.model_dump(mode="json")
                for item in runtime.evolution.list_candidates(tenant_id=actor.tenant_id)
            ],
            "release_policy": "offline_eval_then_compliance_approval",
        }

    @app.post("/api/governance/feedback", status_code=status.HTTP_201_CREATED)
    def create_feedback(
        request: FeedbackRequest,
        actor: Annotated[Actor, Depends(actor_from_headers)],
    ) -> dict[str, Any]:
        if request.task_id is not None:
            _get_task(runtime, actor=actor, task_id=request.task_id)
        record = runtime.evolution.create_feedback(
            tenant_id=actor.tenant_id,
            user_id=actor.actor_id,
            task_id=request.task_id,
            feedback_type=request.feedback_type,
            summary=request.summary,
            correction=request.correction,
        )
        return record.model_dump(mode="json")

    @app.post(
        "/api/governance/prompt-candidates",
        status_code=status.HTTP_201_CREATED,
    )
    def propose_prompt_candidate(
        request: PromptCandidateRequest,
        actor: Annotated[Actor, Depends(actor_from_headers)],
    ) -> dict[str, Any]:
        _require_role(actor, "procurement_operator")
        try:
            candidate = runtime.evolution.propose_candidate(
                tenant_id=actor.tenant_id,
                scope=PROMPT_SCOPE_TEXT_INTAKE,
                candidate_version=request.candidate_version,
                prompt_text=request.prompt_text,
                proposed_by=actor.actor_id,
                feedback_ids=request.feedback_ids,
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return candidate.model_dump(mode="json")

    @app.post("/api/governance/prompt-candidates/{candidate_id}/evaluate")
    def evaluate_prompt_candidate(
        candidate_id: str,
        actor: Annotated[Actor, Depends(actor_from_headers)],
    ) -> dict[str, Any]:
        _require_role(actor, "procurement_operator")
        try:
            candidate = runtime.evolution.evaluate_contract(
                tenant_id=actor.tenant_id,
                candidate_id=candidate_id,
                evaluated_by=actor.actor_id,
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return candidate.model_dump(mode="json")

    @app.post("/api/governance/prompt-candidates/{candidate_id}/approve")
    def approve_prompt_candidate(
        candidate_id: str,
        actor: Annotated[Actor, Depends(actor_from_headers)],
    ) -> dict[str, Any]:
        try:
            candidate = runtime.evolution.approve_candidate(
                tenant_id=actor.tenant_id,
                candidate_id=candidate_id,
                approved_by=actor.actor_id,
                actor_roles=actor.roles,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return candidate.model_dump(mode="json")

    @app.post("/api/governance/prompt-candidates/{candidate_id}/release")
    def release_prompt_candidate(
        candidate_id: str,
        actor: Annotated[Actor, Depends(actor_from_headers)],
    ) -> dict[str, Any]:
        try:
            release = runtime.evolution.release_candidate(
                tenant_id=actor.tenant_id,
                candidate_id=candidate_id,
                released_by=actor.actor_id,
                actor_roles=actor.roles,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return release.model_dump(mode="json")

    @app.post("/api/governance/releases/{release_id}/rollback")
    def rollback_prompt_release(
        release_id: str,
        actor: Annotated[Actor, Depends(actor_from_headers)],
    ) -> dict[str, Any]:
        try:
            release = runtime.evolution.rollback_release(
                tenant_id=actor.tenant_id,
                release_id=release_id,
                rolled_back_by=actor.actor_id,
                actor_roles=actor.roles,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return release.model_dump(mode="json")

    return app


def _job_payload(
    *,
    actor: Actor,
    source: dict[str, Any],
    architecture: str = "single",
) -> dict[str, Any]:
    return {
        "actor_id": actor.actor_id,
        "actor_roles": sorted(actor.roles),
        "source": source,
        "architecture": architecture,
        "run_id": f"api-{uuid4().hex}",
    }


def _get_task(runtime: ProcureOpsRuntime, *, actor: Actor, task_id: str):
    try:
        return runtime.repository.get_task(tenant_id=actor.tenant_id, task_id=task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc


def _require_role(actor: Actor, role: str) -> None:
    if role not in actor.roles:
        raise HTTPException(status_code=403, detail=f"{role} role required")


def _validate_architecture(architecture: str) -> None:
    if architecture not in {"single", "multi", "multi_llm"}:
        raise HTTPException(status_code=422, detail="invalid agent architecture")


def _ensure_architecture_ready(architecture: str) -> None:
    if architecture != "multi_llm":
        return
    if not _live_models_enabled():
        raise HTTPException(
            status_code=409,
            detail="multi_llm requires PROCUREOPS_ENABLE_LIVE_MODELS=1",
        )
    if not model_configuration_status(kind="text")["configured"]:
        raise HTTPException(status_code=409, detail="text model configuration is incomplete")


def _live_models_enabled() -> bool:
    import os

    return os.environ.get("PROCUREOPS_ENABLE_LIVE_MODELS", "0") == "1"


def _public_model_routes(kind: str) -> list[dict[str, str]]:
    try:
        routes = model_routes_from_environment(kind=kind)
    except RuntimeError:
        return []
    return [
        {
            "route": route.name,
            "provider": route.client.provider,
            "model": route.client.model,
        }
        for route in routes
    ]


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
