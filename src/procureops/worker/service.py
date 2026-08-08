from __future__ import annotations

import os
from typing import Any
from uuid import uuid4

from procureops.config import load_environment
from procureops.domain.models import ApprovalGrant
from procureops.evals.replay import ReplayStore
from procureops.harness.audit import (
    CompositeAuditSink,
    InMemoryAuditSink,
    JsonlAuditSink,
)
from procureops.harness.budget import RunBudgetLedger
from procureops.harness.errors import PermanentToolError, TransientToolError
from procureops.harness.model_gateway import ModelGateway
from procureops.harness.provider_clients import client_from_environment
from procureops.intake import IntakeService
from procureops.intake.model_extractors import GatewayTextExtractor, GatewayVisionExtractor
from procureops.runtime import ProcureOpsRuntime
from procureops.worker.queue import Job


class ProcureOpsWorker:
    def __init__(self, *, runtime: ProcureOpsRuntime, worker_id: str | None = None) -> None:
        self.runtime = runtime
        self.worker_id = worker_id or f"local-worker-{uuid4().hex[:8]}"

    def run_once(self) -> dict[str, Any] | None:
        job = self.runtime.queue.claim(worker_id=self.worker_id)
        if job is None:
            return None
        try:
            outcome = self._process(job)
        except PermanentToolError as exc:
            status = self.runtime.queue.fail(
                job=job,
                worker_id=self.worker_id,
                error=exc,
                retryable=False,
            )
            return {"job_id": job.job_id, "queue_status": status, "error": type(exc).__name__}
        except TransientToolError as exc:
            status = self.runtime.queue.fail(
                job=job,
                worker_id=self.worker_id,
                error=exc,
                retryable=True,
            )
            return {"job_id": job.job_id, "queue_status": status, "error": type(exc).__name__}
        except Exception as exc:
            status = self.runtime.queue.fail(
                job=job,
                worker_id=self.worker_id,
                error=exc,
                retryable=True,
            )
            return {"job_id": job.job_id, "queue_status": status, "error": type(exc).__name__}
        self.runtime.queue.succeed(job_id=job.job_id, worker_id=self.worker_id)
        return {"job_id": job.job_id, "queue_status": "succeeded", **outcome}

    def _process(self, job: Job) -> dict[str, Any]:
        payload = job.payload
        memory_audit = InMemoryAuditSink()
        audit = CompositeAuditSink(memory_audit, JsonlAuditSink(self.runtime.audit_path))
        context = self.runtime.context(
            tenant_id=job.tenant_id,
            task_id=job.task_id,
            actor_id=str(payload.get("actor_id", "local-worker")),
            actor_roles=frozenset(payload.get("actor_roles", ["procurement_operator"])),
            run_id=str(payload.get("run_id", f"job-{job.job_id}")),
            correlation_id=str(payload.get("correlation_id", job.job_id)),
        )
        agent = self.runtime.agent(audit=audit)
        if job.job_type == "process_intake":
            intake = self._intake(payload, context=context, audit=audit)
            result = agent.start(context=context, intake=intake)
        elif job.job_type == "resume_approval":
            approval = ApprovalGrant.model_validate(payload["approval"])
            result = agent.resume(context=context, approval=approval)
        else:
            raise PermanentToolError(f"unsupported job type: {job.job_type}")
        events = self.runtime.repository.workflow_events(
            tenant_id=job.tenant_id,
            task_id=job.task_id,
        )
        replay = ReplayStore(self.runtime.replay_root).save(
            context=context,
            outcome=result.status.value,
            workflow_events=events,
            audit_events=memory_audit.events(),
        )
        return {
            "task_id": job.task_id,
            "task_status": result.status.value,
            "replay": replay.name,
        }

    def _intake(self, payload: dict[str, Any], *, context, audit):
        source = payload["source"]
        if source["kind"] == "text":
            service = self._intake_service(
                source_mode="text",
                context=context,
                audit=audit,
            )
            return service.from_text(
                str(source["text"]),
                artifact_id=str(source.get("artifact_id", "api-text")),
            )
        if source["kind"] == "upload":
            path = self.runtime.blobs.resolve(str(source["storage_key"]))
            service = self._intake_service(
                source_mode="vision"
                if path.suffix.casefold() in {".png", ".jpg", ".jpeg", ".webp"}
                else "deterministic",
                context=context,
                audit=audit,
            )
            return service.from_file(path)
        raise PermanentToolError(f"unsupported intake source: {source['kind']}")

    def _intake_service(self, *, source_mode: str, context, audit) -> IntakeService:
        load_environment(self.runtime.project_root)
        if os.environ.get("PROCUREOPS_ENABLE_LIVE_MODELS", "0") != "1":
            return IntakeService()
        ledger = RunBudgetLedger(context)
        text_extractor = None
        vision_extractor = None
        if source_mode == "text":
            text_extractor = GatewayTextExtractor(
                gateway=ModelGateway(
                    client=client_from_environment(kind="text"),
                    audit=audit,
                ),
                context=context,
                ledger=ledger,
            )
        elif source_mode == "vision":
            vision_extractor = GatewayVisionExtractor(
                gateway=ModelGateway(
                    client=client_from_environment(kind="vision"),
                    audit=audit,
                ),
                context=context,
                ledger=ledger,
            )
        return IntakeService(
            vision_extractor=vision_extractor,
            text_extractor=text_extractor,
        )
