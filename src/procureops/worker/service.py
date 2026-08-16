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
from procureops.harness.provider_clients import routed_gateway_from_environment
from procureops.intake import (
    IntakeService,
    merge_intake_results,
    relabel_intake_artifact,
)
from procureops.intake.model_extractors import GatewayTextExtractor, GatewayVisionExtractor
from procureops.memory import detect_preference_candidates
from procureops.rag.ingestion import DocumentIngestionService
from procureops.runtime import ProcureOpsRuntime
from procureops.worker.queue import Job


class ProcureOpsWorker:
    def __init__(self, *, runtime: ProcureOpsRuntime, worker_id: str | None = None) -> None:
        self.runtime = runtime
        self.worker_id = worker_id or f"local-worker-{uuid4().hex[:8]}"

    def run_once(self) -> dict[str, Any] | None:
        self.runtime.outbox.dispatch_pending()
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
        audit = CompositeAuditSink(
            memory_audit,
            JsonlAuditSink(self.runtime.audit_path),
            self.runtime.observability.audit_sink(),
        )
        context = self.runtime.context(
            tenant_id=job.tenant_id,
            task_id=job.task_id,
            actor_id=str(payload.get("actor_id", "local-worker")),
            actor_roles=frozenset(payload.get("actor_roles", ["procurement_operator"])),
            run_id=str(payload.get("run_id", f"job-{job.job_id}")),
            correlation_id=str(payload.get("correlation_id", job.job_id)),
        )
        task = self.runtime.repository.get_task(
            tenant_id=job.tenant_id,
            task_id=job.task_id,
        )
        if job.job_type == "rag_ingest":
            return self._process_rag_ingest(job, context=context)
        architecture = str(
            payload.get("architecture", task.request.get("architecture", "single"))
        )
        agent = self.runtime.agent(
            audit=audit,
            architecture=architecture,
            context=context,
        )
        if job.job_type == "process_intake":
            intake = self._intake(payload, context=context, audit=audit)
            self._propose_memory_candidates(payload=payload, context=context)
            result = agent.start(context=context, intake=intake)
        elif job.job_type == "resume_approval":
            approval = ApprovalGrant.model_validate(payload["approval"])
            result = agent.resume(context=context, approval=approval)
        else:
            raise PermanentToolError(f"unsupported job type: {job.job_type}")
        trace = getattr(agent, "trace", None)
        if trace is not None and (trace.messages or trace.unknown_phases):
            self.runtime.repository.append_workflow_event(
                tenant_id=job.tenant_id,
                task_id=job.task_id,
                event_type="supervisor.trace",
                payload={
                    "architecture": architecture,
                    "messages": [
                        message.model_dump(mode="json") for message in trace.messages
                    ],
                    "unknown_phases": list(trace.unknown_phases),
                    "authoritative_workflow": "single_deterministic_v1",
                },
            )
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
            "architecture": architecture,
            "specialist_messages": len(trace.messages) if trace is not None else 0,
            "replay": replay.name,
        }

    def _process_rag_ingest(self, job: Job, *, context) -> dict[str, Any]:
        source = job.payload.get("source") or {}
        service = DocumentIngestionService(
            project_root=self.runtime.project_root,
            var_root=self.runtime.var_root,
            retriever=self.runtime.retriever,
        )
        outcome = service.ingest(
            tenant_id=job.tenant_id,
            task_id=job.task_id,
            actor_id=context.actor_id,
            uploads=list(source.get("items") or []),
            blob_resolver=self.runtime.blobs.resolve,
            approved_for_retrieval=bool(source.get("approved_for_retrieval", False)),
        )
        self.runtime.repository.append_workflow_event(
            tenant_id=job.tenant_id,
            task_id=job.task_id,
            event_type="rag_ingest.completed",
            payload=outcome,
        )
        return {"task_id": job.task_id, "task_status": outcome["status"], **outcome}

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
            return self._intake_upload(
                source,
                context=context,
                audit=audit,
            )
        if source["kind"] == "uploads":
            items = tuple(source.get("items", ()))
            if not items:
                raise PermanentToolError("upload bundle is empty")
            shared_ledger = RunBudgetLedger(context)
            results = tuple(
                self._intake_upload(
                    item,
                    context=context,
                    audit=audit,
                    ledger=shared_ledger,
                )
                for item in items
            )
            return merge_intake_results(
                results,
                artifact_id=str(source.get("artifact_id", f"bundle-{context.task_id}")),
            )
        raise PermanentToolError(f"unsupported intake source: {source['kind']}")

    def _intake_upload(self, source: dict[str, Any], *, context, audit, ledger=None):
        path = self.runtime.blobs.resolve(str(source["storage_key"]))
        service = self._intake_service(
            source_mode=(
                "vision"
                if path.suffix.casefold() in {".png", ".jpg", ".jpeg", ".webp"}
                else "deterministic"
            ),
            context=context,
            audit=audit,
            ledger=ledger,
        )
        result = service.from_file(path)
        original_filename = str(source.get("original_filename") or path.name)
        return relabel_intake_artifact(result, artifact_id=original_filename)

    def _intake_service(
        self,
        *,
        source_mode: str,
        context,
        audit,
        ledger: RunBudgetLedger | None = None,
    ) -> IntakeService:
        load_environment(self.runtime.project_root)
        if os.environ.get("PROCUREOPS_ENABLE_LIVE_MODELS", "0") != "1":
            return IntakeService()
        ledger = ledger or RunBudgetLedger(context)
        text_extractor = None
        vision_extractor = None
        if source_mode == "text":
            active_prompt = self.runtime.evolution.active_prompt(
                tenant_id=context.tenant_id
            )
            text_extractor = GatewayTextExtractor(
                gateway=routed_gateway_from_environment(
                    kind="text",
                    audit=audit,
                ),
                context=context,
                ledger=ledger,
                instruction=active_prompt.prompt_text,
            )
        elif source_mode == "vision":
            vision_extractor = GatewayVisionExtractor(
                gateway=routed_gateway_from_environment(
                    kind="vision",
                    audit=audit,
                ),
                context=context,
                ledger=ledger,
            )
        return IntakeService(
            vision_extractor=vision_extractor,
            text_extractor=text_extractor,
        )

    def _propose_memory_candidates(self, *, payload: dict[str, Any], context) -> None:
        source = payload.get("source", {})
        if source.get("kind") != "text":
            return
        proposed_ids: list[str] = []
        for candidate in detect_preference_candidates(str(source.get("text", ""))):
            record = self.runtime.memory.propose(
                tenant_id=context.tenant_id,
                user_id=context.actor_id,
                memory_key=candidate.memory_key,
                value=candidate.value,
                confidence=candidate.confidence,
                proposed_by="preference_detector_v1",
                source_hash=candidate.source_hash,
            )
            proposed_ids.append(record.record_id)
        if proposed_ids:
            self.runtime.repository.append_workflow_event(
                tenant_id=context.tenant_id,
                task_id=context.task_id,
                event_type="memory.candidate_proposed",
                payload={
                    "record_ids": proposed_ids,
                    "count": len(proposed_ids),
                    "requires_user_confirmation": True,
                },
            )
