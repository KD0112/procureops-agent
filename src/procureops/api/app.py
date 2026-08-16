from __future__ import annotations

import asyncio
import json
import os
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
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from procureops.agents.single import WorkflowResult
from procureops.codeops import CodeTaskRequest, RepoPilotSkill, RepoPlan
from procureops.domain.enums import TaskStatus
from procureops.domain.policy import ApprovalRequirement
from procureops.evals.replay import ReplayStore
from procureops.harness.audit import CompositeAuditSink, InMemoryAuditSink, JsonlAuditSink
from procureops.harness.budget import RunBudgetLedger
from procureops.harness.provider_clients import (
    model_configuration_status,
    model_routes_from_environment,
)
from procureops.infrastructure.cache import (
    InMemoryAsyncCache,
    RateLimiter,
    RedisAsyncCache,
    SessionStore,
    ToolResultCache,
)
from procureops.infrastructure.streams import RedisStreamsQueue
from procureops.intake.prompts import PROMPT_SCOPE_TEXT_INTAKE
from procureops.runtime import ProcureOpsRuntime
from procureops.skills import ProcurementEvidenceSkill, SkillRegistry
from procureops.storage import MySQLBusinessRepository, MySQLSettings
from procureops.worker.service import ProcureOpsWorker

DEFAULT_TENANT = "tenant_engineering_machinery"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
STATIC_ROOT = Path(__file__).with_name("static")
API_VERSION = "0.5.0"
IDENTITY_API_VERSION = "local-session-v1"
MAX_UPLOAD_FILES = 5
MAX_UPLOAD_TOTAL_BYTES = 25 * 1024 * 1024
WORKFLOW_VERSION = "1.1.0"


@dataclass(frozen=True, slots=True)
class Actor:
    tenant_id: str
    actor_id: str
    roles: frozenset[str]


class TextTaskRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    architecture: str = Field(default="single", pattern="^(single|multi|multi_llm)$")


class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    architecture: str = Field(default="single", pattern="^(single|multi|multi_llm)$")
    session_id: str | None = Field(default=None, max_length=200)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2_000)
    top_k: int = Field(default=6, ge=1, le=50)


class SkillRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2_000)
    quantity: str = Field(default="1", max_length=40)


class RepoChangeRequest(BaseModel):
    description: str = Field(min_length=1, max_length=4_000)
    requested_files: tuple[str, ...] = Field(default=(), max_length=20)
    files_to_read: tuple[str, ...] = Field(default=(), max_length=40)
    proposed_writes: dict[str, str] = Field(default_factory=dict)
    expected_sha256: dict[str, str] = Field(default_factory=dict)
    ci_output: str = Field(default="", max_length=20_000)
    test_command: str = Field(default="python -m pytest -q", max_length=300)
    commit_requested: bool = False


# `/api/skills/repo-ci-repair` intentionally reuses this bounded request
# contract. The route differs by workflow intent, not by granting extra tools.


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


class LocalSessionRequest(BaseModel):
    user_id: str = Field(
        pattern="^(local-buyer|local-approver|local-compliance)$"
    )
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
        version=API_VERSION,
        description=(
            "Task-first procurement workbench with governed memory, evolution, "
            "multi-agent experiments, and Harness controls."
        ),
    )
    app.state.runtime = runtime
    app.state.allow_header_auth = allow_header_auth
    try:
        app.state.cache = RedisAsyncCache.from_environment()
        app.state.cache_error = None
    except RuntimeError as exc:
        app.state.cache = InMemoryAsyncCache()
        app.state.cache_error = str(exc)
    app.state.session_store = SessionStore(app.state.cache)
    app.state.tool_cache = ToolResultCache(app.state.cache)
    app.state.rate_limiter = RateLimiter(app.state.cache)
    app.state.skill_registry = SkillRegistry()
    app.state.queue_backend = os.getenv("PROCUREOPS_QUEUE_BACKEND", "sqlite").strip().casefold()
    app.state.stream_queue = (
        RedisStreamsQueue.from_environment()
        if app.state.queue_backend == "redis-streams"
        else None
    )
    app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")

    @app.get("/", include_in_schema=False)
    def workbench() -> FileResponse:
        return FileResponse(STATIC_ROOT / "index.html")

    @app.get("/health")
    def health() -> dict[str, Any]:
        integration = runtime.integrations.status()
        return {
            "status": "ok",
            "api_version": API_VERSION,
            "identity_api": IDENTITY_API_VERSION,
            "storage": "sqlite",
            "worker": "durable-local",
            "integration_profile": integration.profile,
            "external_systems": list(integration.enabled_systems),
            "cache_backend": app.state.cache.backend,
            "mysql_configured": MySQLSettings.from_environment() is not None,
            "queue_backend": app.state.queue_backend,
        }

    @app.get("/api/readiness")
    async def readiness() -> dict[str, Any]:
        checks: dict[str, Any] = {"cache": {"status": "unknown"}}
        try:
            checks["cache"] = await app.state.cache.health()
        except Exception as exc:
            checks["cache"] = {"status": "error", "error": type(exc).__name__}
        mysql_settings = MySQLSettings.from_environment()
        if mysql_settings is None:
            checks["mysql"] = {"status": "not_configured"}
        else:
            mysql = None
            try:
                mysql = MySQLBusinessRepository(mysql_settings)
                checks["mysql"] = await mysql.health()
            except Exception as exc:
                checks["mysql"] = {"status": "error", "error": type(exc).__name__}
            finally:
                if mysql is not None:
                    await mysql.close()
        if app.state.queue_backend == "redis-streams" and app.state.stream_queue is not None:
            try:
                checks["queue"] = await app.state.stream_queue.health()
            except Exception as exc:
                checks["queue"] = {"status": "error", "error": type(exc).__name__}
        status_value = "ok" if all(
            item.get("status") in {"ok", "not_configured"} for item in checks.values()
        ) else "degraded"
        return {"status": status_value, "checks": checks}

    @app.get("/api/tenants")
    def list_tenants() -> dict[str, Any]:
        return {
            "items": [
                {
                    "tenant_id": pack.tenant.tenant_id,
                    "display_name": pack.tenant.display_name,
                    "industry": pack.tenant.industry,
                    "tenant_pack_version": pack.tenant.tenant_pack_version,
                }
                for pack in runtime.tenants.all()
            ],
            "integration_profile": runtime.integrations.status().profile,
        }

    @app.post("/api/auth/local-session")
    def create_local_session(request: LocalSessionRequest) -> dict[str, Any]:
        try:
            session = runtime.auth.create_local_session(
                user_id=request.user_id,
                tenant_id=request.tenant_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
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
        _task, outbox_event_id, _upload_ids = runtime.repository.create_task_with_outbox(
            tenant_id=actor.tenant_id,
            created_by=actor.actor_id,
            request={
                "source_type": "text",
                "preview": request.text[:200],
                "architecture": request.architecture,
            },
            workflow_version=WORKFLOW_VERSION,
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

    @app.post("/api/chat", status_code=status.HTTP_202_ACCEPTED)
    async def chat(
        request: ChatRequest,
        actor: Annotated[Actor, Depends(actor_from_headers)],
    ) -> dict[str, Any]:
        with runtime.observability.observe(
            name="api.chat",
            as_type="agent",
            input={"text": request.text, "architecture": request.architecture},
            metadata={"tenant_id": actor.tenant_id, "actor_id": actor.actor_id},
        ) as observation:
            if not await app.state.rate_limiter.allow(
                tenant_id=actor.tenant_id, actor_id=actor.actor_id
            ):
                raise HTTPException(status_code=429, detail="chat rate limit exceeded")
            result = create_text_task(
                TextTaskRequest(text=request.text, architecture=request.architecture), actor
            )
            session_id = request.session_id or result["task_id"]
            await app.state.session_store.put(
                tenant_id=actor.tenant_id,
                session_id=session_id,
                value={"last_task_id": result["task_id"], "last_text": request.text[:500]},
            )
            response = {**result, "session_id": session_id, "api": "chat"}
            observation.update(output={"task_id": result["task_id"], "session_id": session_id})
            return response

    @app.post("/api/search")
    async def search(
        request: SearchRequest,
        actor: Annotated[Actor, Depends(actor_from_headers)],
    ) -> dict[str, Any]:
        with runtime.observability.observe(
            name="api.search",
            as_type="retriever",
            input={"query": request.query, "top_k": request.top_k},
            metadata={"tenant_id": actor.tenant_id, "actor_id": actor.actor_id},
        ) as observation:
            if not await app.state.rate_limiter.allow(
                tenant_id=actor.tenant_id, actor_id=actor.actor_id
            ):
                raise HTTPException(status_code=429, detail="search rate limit exceeded")
            arguments = {"query": request.query, "top_k": request.top_k}
            cached = await app.state.tool_cache.get(
                tenant_id=actor.tenant_id, tool_name="rag_search", arguments=arguments
            )
            if cached is not None:
                observation.update(output={"cache": "hit", "hit_count": len(cached)})
                return {"items": cached, "cache": "hit"}
            hits = await asyncio.to_thread(
                runtime.retriever.search,
                tenant_id=actor.tenant_id,
                actor_roles=actor.roles,
                query=request.query,
                top_k=request.top_k,
                minimum_score=0,
            )
            items = [hit.model_dump(mode="json") for hit in hits]
            await app.state.tool_cache.put(
                tenant_id=actor.tenant_id,
                tool_name="rag_search",
                arguments=arguments,
                value=items,
            )
            observation.update(output={"cache": "miss", "hit_count": len(items)})
            return {"items": items, "cache": "miss"}

    @app.get("/api/skills")
    def list_skills() -> dict[str, Any]:
        return {
            "items": ["procurement_evidence", "repo_change_review"],
            "execution": "SkillRegistry -> ToolGateway",
        }

    @app.post("/api/skills/procurement-evidence")
    async def procurement_evidence_skill(
        request: SkillRequest,
        actor: Annotated[Actor, Depends(actor_from_headers)],
    ) -> dict[str, Any]:
        audit = InMemoryAuditSink()
        context = runtime.context(
            tenant_id=actor.tenant_id,
            task_id=f"skill-{uuid4().hex}",
            actor_id=actor.actor_id,
            actor_roles=actor.roles,
            run_id=f"skill-run-{uuid4().hex}",
            correlation_id=f"skill-correlation-{uuid4().hex}",
        )
        agent = runtime.agent(audit=audit, architecture="single", context=context)
        skill = ProcurementEvidenceSkill.from_tool_gateway(
            gateway=agent.tool_gateway,
            context=context,
            ledger=RunBudgetLedger(context),
        )
        registry = SkillRegistry()
        registry.register("procurement_evidence", skill)
        result = await registry.execute(
            "procurement_evidence",
            tenant_id=actor.tenant_id,
            query=request.query,
            quantity=request.quantity,
        )
        return {"skill": "procurement_evidence", "result": result.model_dump(mode="json")}

    @app.post("/api/skills/repo-change-review")
    @app.post("/api/skills/repo-ci-repair")
    async def repo_change_review_skill(
        request: RepoChangeRequest,
        actor: Annotated[Actor, Depends(actor_from_headers)],
    ) -> dict[str, Any]:
        task_id = f"code-task-{uuid4().hex}"
        context = runtime.context(
            tenant_id=actor.tenant_id,
            task_id=task_id,
            actor_id=actor.actor_id,
            actor_roles=actor.roles,
            run_id=f"code-run-{uuid4().hex}",
            correlation_id=f"code-correlation-{uuid4().hex}",
        )
        memory_audit = InMemoryAuditSink()
        audit = CompositeAuditSink(
            memory_audit,
            JsonlAuditSink(runtime.audit_path),
            runtime.observability.audit_sink(),
        )
        skill = RepoPilotSkill(
            source_root=runtime.project_root,
            var_root=runtime.var_root,
            audit=audit,
            context=context,
        )
        registry = SkillRegistry()
        registry.register("repo_change_review", skill)
        task = CodeTaskRequest(
            task_id=task_id,
            description=request.description,
            requested_files=request.requested_files,
            ci_output=request.ci_output,
            test_command=request.test_command,
            commit_requested=request.commit_requested,
        )
        plan = RepoPlan(
            rationale=request.description,
            files_to_read=request.files_to_read,
            proposed_writes=request.proposed_writes,
            expected_sha256=request.expected_sha256,
            test_command=request.test_command,
            commit_requested=request.commit_requested,
        )
        with runtime.observability.observe(
            name="skill.repo_change_review",
            as_type="agent",
            context=context,
            input={"task_id": task_id, "description": request.description},
            metadata={
                "tenant_id": actor.tenant_id,
                "actor_id": actor.actor_id,
                "requested_file_count": len(request.requested_files),
                "proposed_write_count": len(request.proposed_writes),
            },
        ) as observation:
            result = await registry.execute(
                "repo_change_review",
                task=task,
                plan=plan,
            )
            observation.update(
                output={
                    "status": result.status,
                    "workspace_id": result.workspace_id,
                    "files_changed": len(result.files_changed),
                }
            )
        skill_name = "repo_ci_repair" if request.ci_output.strip() else "repo_change_review"
        return {"skill": skill_name, "result": result.model_dump(mode="json")}

    @app.post("/api/tasks/upload", status_code=status.HTTP_202_ACCEPTED)
    async def create_upload_task(
        file: Annotated[list[UploadFile], File()],
        actor: Annotated[Actor, Depends(actor_from_headers)],
        architecture: Annotated[str, Form()] = "single",
        ingest_mode: Annotated[str, Form()] = "intake",
        approved_for_retrieval: Annotated[bool, Form()] = False,
    ) -> dict[str, Any]:
        _validate_architecture(architecture)
        _ensure_architecture_ready(architecture)
        if ingest_mode not in {"intake", "rag"}:
            raise HTTPException(status_code=422, detail="invalid ingest_mode")
        if approved_for_retrieval and "compliance_approver" not in actor.roles:
            raise HTTPException(status_code=403, detail="compliance_approver role required")
        if not file:
            raise HTTPException(status_code=422, detail="at least one upload is required")
        if len(file) > MAX_UPLOAD_FILES:
            raise HTTPException(
                status_code=413,
                detail=f"upload bundle accepts at most {MAX_UPLOAD_FILES} files",
            )
        task_id = str(uuid4())
        prepared: list[tuple[UploadFile, bytes]] = []
        total_bytes = 0
        for item in file:
            data = await item.read(runtime.blobs.max_bytes + 1)
            if len(data) > runtime.blobs.max_bytes:
                raise HTTPException(status_code=413, detail="upload exceeds 10 MiB")
            total_bytes += len(data)
            prepared.append((item, data))
        if total_bytes > MAX_UPLOAD_TOTAL_BYTES:
            raise HTTPException(status_code=413, detail="upload bundle exceeds 25 MiB")
        try:
            for item, data in prepared:
                runtime.blobs.validate_upload(
                    filename=item.filename or "upload.bin",
                    data=data,
                )
            stored_files = tuple(
                runtime.blobs.save(
                    tenant_id=actor.tenant_id,
                    task_id=task_id,
                    filename=item.filename or "upload.bin",
                    content_type=item.content_type or "application/octet-stream",
                    data=data,
                )
                for item, data in prepared
            )
        except ValueError as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        original_filenames = [stored.original_filename for stored in stored_files]
        job_payload = _job_payload(
            actor=actor,
            source={
                "kind": "uploads",
                "artifact_id": f"bundle-{task_id}",
                "approved_for_retrieval": approved_for_retrieval,
                "items": [
                    {
                        "storage_key": stored.storage_key,
                        "original_filename": stored.original_filename,
                    }
                    for stored in stored_files
                ],
            },
            architecture=architecture,
        )
        _task, outbox_event_id, upload_ids = runtime.repository.create_task_with_outbox(
            tenant_id=actor.tenant_id,
            created_by=actor.actor_id,
            request={
                "source_type": "upload_bundle" if len(stored_files) > 1 else "upload",
                "filename": (
                    original_filenames[0]
                    if len(original_filenames) == 1
                    else f"{len(original_filenames)} 个附件"
                ),
                "filenames": original_filenames,
                "attachment_count": len(original_filenames),
                "architecture": architecture,
            },
            workflow_version=WORKFLOW_VERSION,
            task_id=task_id,
            job_payload=job_payload,
            idempotency_key=f"intake:{task_id}:v1",
            job_type="rag_ingest" if ingest_mode == "rag" else "process_intake",
            uploads=tuple(
                {
                    "original_filename": stored.original_filename,
                    "storage_key": stored.storage_key,
                    "content_type": stored.content_type,
                    "size_bytes": stored.size_bytes,
                    "sha256": stored.sha256,
                }
                for stored in stored_files
            ),
        )
        job_type = "rag_ingest" if ingest_mode == "rag" else "process_intake"
        if job_type == "rag_ingest" and app.state.queue_backend == "redis-streams":
            if getattr(app.state.stream_queue, "backend", "") != "redis-streams":
                raise HTTPException(status_code=503, detail="Redis Streams is not configured")
            event_payload = runtime.outbox.event_payload(event_id=outbox_event_id)
            stream_message_id = await app.state.stream_queue.publish(
                stream="rag:ingest", payload=event_payload
            )
            runtime.outbox.mark_dispatched(event_id=outbox_event_id)
            job_id = stream_message_id
        else:
            job = runtime.outbox.dispatch(event_id=outbox_event_id)
            if job is None:
                raise HTTPException(status_code=503, detail="outbox delivery is pending")
            job_id = job.job_id
        return {
            "task_id": task_id,
            "job_id": job_id,
            "upload_id": upload_ids[0],
            "upload_ids": list(upload_ids),
            "outbox_event_id": outbox_event_id,
            "sha256": stored_files[0].sha256,
            "sha256s": [stored.sha256 for stored in stored_files],
            "reused": False,
            "queue_backend": app.state.queue_backend if ingest_mode == "rag" else "sqlite",
        }

    @app.post("/api/documents", status_code=status.HTTP_202_ACCEPTED)
    async def create_document_task(
        file: Annotated[list[UploadFile], File()],
        actor: Annotated[Actor, Depends(actor_from_headers)],
        architecture: Annotated[str, Form()] = "single",
        approved_for_retrieval: Annotated[bool, Form()] = False,
    ) -> dict[str, Any]:
        result = await create_upload_task(
            file=file,
            actor=actor,
            architecture=architecture,
            ingest_mode="rag",
            approved_for_retrieval=approved_for_retrieval,
        )
        return {**result, "pipeline": "document_to_rag"}

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

    @app.get("/api/tasks/{task_id}/events/stream")
    async def task_event_stream(
        task_id: str,
        request: Request,
        actor: Annotated[Actor, Depends(actor_from_headers)],
        last_event_id: Annotated[str | None, Header()] = None,
    ) -> StreamingResponse:
        task = _get_task(runtime, actor=actor, task_id=task_id)
        try:
            cursor = int(last_event_id or "0")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Last-Event-ID must be an integer") from exc
        if cursor < 0:
            raise HTTPException(status_code=400, detail="Last-Event-ID must be non-negative")

        async def stream():
            nonlocal cursor, task
            idle_polls = 0
            while True:
                if await request.is_disconnected():
                    break
                events = await asyncio.to_thread(
                    runtime.repository.workflow_events_after,
                    tenant_id=actor.tenant_id,
                    task_id=task_id,
                    after_sequence=cursor,
                    limit=100,
                )
                if events:
                    idle_polls = 0
                    for event in events:
                        cursor = int(event["sequence"])
                        yield _encode_sse_event(_public_workflow_event(event))
                    continue
                task = await asyncio.to_thread(
                    runtime.repository.get_task,
                    tenant_id=actor.tenant_id,
                    task_id=task_id,
                )
                if TaskStatus(task.status) in {
                    TaskStatus.COMPLETED,
                    TaskStatus.FAILED_TERMINAL,
                }:
                    break
                idle_polls += 1
                if idle_polls % 15 == 0:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    @app.delete("/api/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
    def archive_task(
        task_id: str,
        actor: Annotated[Actor, Depends(actor_from_headers)],
    ) -> None:
        _get_task(runtime, actor=actor, task_id=task_id)
        created_by = runtime.repository.task_created_by(
            tenant_id=actor.tenant_id,
            task_id=task_id,
        )
        if created_by != actor.actor_id and "compliance_approver" not in actor.roles:
            raise HTTPException(
                status_code=403,
                detail="only the task creator or compliance approver may archive a task",
            )
        try:
            runtime.repository.archive_task(
                tenant_id=actor.tenant_id,
                task_id=task_id,
                deleted_by=actor.actor_id,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

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
            grant = runtime.agent(audit=_NullAudit(), context=context).issue_approval(
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


_SENSITIVE_EVENT_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "chain_of_thought",
        "credential",
        "instruction",
        "messages",
        "model_input",
        "password",
        "prompt",
        "rationale",
        "reasoning",
        "secret",
        "token",
    }
)


def _redact_event_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if key.casefold() in _SENSITIVE_EVENT_KEYS
                else _redact_event_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_event_value(item) for item in value]
    return value


def _public_workflow_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "sequence": event["sequence"],
        "event_id": event["event_id"],
        "event_type": event["event_type"],
        "payload_hash": event["payload_hash"],
        "payload": _redact_event_value(json.loads(event["payload_json"])),
        "occurred_at": event["occurred_at"],
    }


def _encode_sse_event(event: dict[str, Any]) -> str:
    payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event['sequence']}\nevent: {event['event_type']}\ndata: {payload}\n\n"


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
    created_by = runtime.repository.task_created_by(
        tenant_id=actor.tenant_id,
        task_id=task_id,
    )
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
    can_approve = False
    approval_block_reason: str | None = None
    if task.status == TaskStatus.AWAITING_APPROVAL and approval is not None:
        required_roles = set(
            approval["approval_requirement"].get("required_roles", [])
        )
        if created_by == actor.actor_id:
            approval_block_reason = "maker_checker"
        elif not required_roles.intersection(actor.roles):
            approval_block_reason = "required_role_missing"
        else:
            can_approve = True
    return {
        "task": task.model_dump(mode="json"),
        "permissions": {
            "can_archive": (
                created_by == actor.actor_id
                or "compliance_approver" in actor.roles
            ),
            "can_approve": can_approve,
            "approval_block_reason": approval_block_reason,
        },
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
