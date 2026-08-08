from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from procureops.agents import LLMSupervisorWorkflow, SingleAgentWorkflow, SupervisorWorkflow
from procureops.agents.single import default_policy
from procureops.auth import AuthService
from procureops.config import load_environment
from procureops.demo import seed_demo_database
from procureops.domain.models import RunBudget, RunContext
from procureops.evolution import EvolutionService
from procureops.harness.audit import AuditSink
from procureops.harness.provider_clients import routed_gateway_from_environment
from procureops.harness.tool_gateway import ToolGateway
from procureops.memory import MemoryService
from procureops.rag import HashingEmbeddingProvider, SQLiteKnowledgeIndex
from procureops.rag.governance import scan_knowledge_base
from procureops.storage import ProcureOpsRepository, SQLiteDatabase
from procureops.storage.blobs import LocalBlobStore
from procureops.tools import register_procurement_tools
from procureops.worker.outbox import SQLiteOutbox
from procureops.worker.queue import SQLiteWorkQueue


@dataclass(slots=True)
class ProcureOpsRuntime:
    project_root: Path
    database: SQLiteDatabase
    repository: ProcureOpsRepository
    queue: SQLiteWorkQueue
    outbox: SQLiteOutbox
    blobs: LocalBlobStore
    retriever: SQLiteKnowledgeIndex
    memory: MemoryService
    auth: AuthService
    evolution: EvolutionService
    audit_path: Path
    replay_root: Path

    @classmethod
    def create(
        cls,
        *,
        project_root: Path,
        database_path: Path | None = None,
        var_root: Path | None = None,
    ) -> ProcureOpsRuntime:
        project_root = project_root.resolve()
        load_environment(project_root)
        runtime_root = (var_root or project_root / "var").resolve()
        runtime_root.mkdir(parents=True, exist_ok=True)
        database = SQLiteDatabase(database_path or runtime_root / "procureops.sqlite3")
        repository = seed_demo_database(database, project_root=project_root)
        retriever = SQLiteKnowledgeIndex(
            path=runtime_root / "rag" / "engineering_machinery.sqlite3",
            embedding_provider=HashingEmbeddingProvider(dimensions=256),
        )
        documents = scan_knowledge_base(project_root / "knowledge")
        if not retriever.is_current(documents):
            retriever.rebuild(documents)
        evolution = EvolutionService(database)
        evolution.bootstrap_baseline(tenant_id="tenant_engineering_machinery")
        auth = AuthService(database)
        auth.bootstrap_demo_users(
            tenant_id="tenant_engineering_machinery",
        )
        database.optimize()
        queue = SQLiteWorkQueue(database)
        return cls(
            project_root=project_root,
            database=database,
            repository=repository,
            queue=queue,
            outbox=SQLiteOutbox(database, queue),
            blobs=LocalBlobStore(runtime_root / "uploads"),
            retriever=retriever,
            memory=MemoryService(database),
            auth=auth,
            evolution=evolution,
            audit_path=runtime_root / "audit.jsonl",
            replay_root=runtime_root / "replays" / "api",
        )

    def agent(
        self,
        *,
        audit: AuditSink,
        architecture: str = "single",
        context: RunContext | None = None,
    ) -> SingleAgentWorkflow | SupervisorWorkflow | LLMSupervisorWorkflow:
        if architecture not in {"single", "multi", "multi_llm"}:
            raise ValueError("architecture must be single, multi, or multi_llm")
        gateway = ToolGateway(audit=audit)
        register_procurement_tools(gateway, self.repository)
        common = {
            "repository": self.repository,
            "tool_gateway": gateway,
            "policy": default_policy(self.project_root),
            "retriever": self.retriever,
            "memory_service": self.memory,
        }
        if architecture == "multi":
            return SupervisorWorkflow(**common)
        if architecture == "multi_llm":
            if context is None:
                raise ValueError("multi_llm architecture requires a run context")
            load_environment(self.project_root)
            if os.environ.get("PROCUREOPS_ENABLE_LIVE_MODELS", "0") != "1":
                raise PermissionError(
                    "multi_llm requires PROCUREOPS_ENABLE_LIVE_MODELS=1"
                )
            return LLMSupervisorWorkflow(
                **common,
                context=context,
                model_gateway=routed_gateway_from_environment(
                    kind="text",
                    audit=audit,
                ),
            )
        return SingleAgentWorkflow(
            **common,
        )

    def context(
        self,
        *,
        tenant_id: str,
        task_id: str,
        actor_id: str,
        actor_roles: frozenset[str],
        run_id: str,
        correlation_id: str,
    ) -> RunContext:
        prompt = self.evolution.bootstrap_baseline(tenant_id=tenant_id)
        return RunContext(
            run_id=run_id,
            task_id=task_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
            actor_roles=actor_roles,
            workflow_version="1.0.0",
            prompt_version=prompt.prompt_version,
            model_policy_version="1.0.0",
            rule_set_version="1.0.0",
            tenant_pack_version="1.0.0",
            deadline_at=datetime.now(UTC) + timedelta(minutes=5),
            budget=RunBudget(
                max_model_calls=16,
                max_tool_calls=12,
                max_tokens=16_000,
                max_cost_usd=1,
            ),
            correlation_id=correlation_id,
        )
