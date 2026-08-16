from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from procureops.agents import LLMSupervisorWorkflow, SingleAgentWorkflow, SupervisorWorkflow
from procureops.agents.single import policy_for_tenant
from procureops.auth import AuthService
from procureops.commerce import CommerceAnalyticsStore
from procureops.config import load_environment
from procureops.demo import seed_demo_database
from procureops.domain.models import RunBudget, RunContext
from procureops.evolution import EvolutionService
from procureops.harness.audit import AuditSink
from procureops.harness.provider_clients import routed_gateway_from_environment
from procureops.harness.tool_gateway import ToolGateway
from procureops.integrations import IntegrationSuiteFactory
from procureops.integrations.research import research_connector_from_environment
from procureops.memory import MemoryService
from procureops.observability import LangfuseTracer
from procureops.rag import (
    AdvancedRetriever,
    SQLiteKnowledgeIndex,
    embedding_provider_from_environment,
)
from procureops.rag.governance import scan_knowledge_base
from procureops.storage import ProcureOpsRepository, SQLiteDatabase
from procureops.storage.blobs import LocalBlobStore
from procureops.tenancy import TenantPackRegistry
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
    advanced_retriever: AdvancedRetriever
    memory: MemoryService
    auth: AuthService
    evolution: EvolutionService
    tenants: TenantPackRegistry
    integrations: IntegrationSuiteFactory
    audit_path: Path
    replay_root: Path
    var_root: Path
    observability: LangfuseTracer
    commerce: CommerceAnalyticsStore

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
        tenants = TenantPackRegistry(project_root / "data" / "tenant_packs")
        commerce_seed = (
            project_root
            / "data"
            / "tenant_packs"
            / "tenant_commerce_ops"
            / "seed"
            / "analytics.json"
        )
        commerce = CommerceAnalyticsStore(
            runtime_root / "commerce.sqlite3",
            seed_path=commerce_seed if commerce_seed.exists() else None,
        )
        embedding_provider = embedding_provider_from_environment()
        retriever = SQLiteKnowledgeIndex(
            path=runtime_root / "rag" / "multi_tenant.sqlite3",
            embedding_provider=embedding_provider,
        )
        documents = scan_knowledge_base(project_root / "knowledge")
        uploaded_knowledge_root = runtime_root / "knowledge_uploads"
        if uploaded_knowledge_root.exists():
            documents.extend(scan_knowledge_base(uploaded_knowledge_root))
        if not retriever.is_current(documents):
            retriever.rebuild(documents)
        advanced_retriever = AdvancedRetriever(
            documents=documents,
            embedding_provider=embedding_provider,
            backend=os.getenv("PROCUREOPS_RAG_ANN_BACKEND", "hnsw").strip().casefold()
            or "hnsw",
        )
        advanced_retriever.build()
        evolution = EvolutionService(database)
        auth = AuthService(database)
        for pack in tenants.all():
            evolution.bootstrap_baseline(tenant_id=pack.tenant.tenant_id)
            auth.bootstrap_demo_users(tenant_id=pack.tenant.tenant_id)
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
            advanced_retriever=advanced_retriever,
            memory=MemoryService(database),
            auth=auth,
            evolution=evolution,
            tenants=tenants,
            integrations=IntegrationSuiteFactory(
                repository=repository,
                tenants=tenants,
            ),
            audit_path=runtime_root / "audit.jsonl",
            replay_root=runtime_root / "replays" / "api",
            var_root=runtime_root,
            observability=LangfuseTracer.from_environment(),
            commerce=commerce,
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
        tenant_id = context.tenant_id if context is not None else "tenant_engineering_machinery"
        gateway = ToolGateway(audit=audit)
        research_connector = research_connector_from_environment(self.project_root)
        register_procurement_tools(
            gateway,
            self.repository,
            integrations=self.integrations.for_tenant(tenant_id),
            research_connector=research_connector,
        )
        common = {
            "repository": self.repository,
            "tool_gateway": gateway,
            "policy": policy_for_tenant(self.project_root, tenant_id),
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
                supplier_evidence_tool_name=(
                    "supplier_evidence_search" if research_connector is not None else None
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
        pack = self.tenants.get(tenant_id)
        prompt = self.evolution.bootstrap_baseline(tenant_id=tenant_id)
        return RunContext(
            run_id=run_id,
            task_id=task_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
            actor_roles=actor_roles,
            workflow_version="1.1.0",
            prompt_version=prompt.prompt_version,
            model_policy_version="1.0.0",
            rule_set_version=pack.rules.version,
            tenant_pack_version=pack.tenant.tenant_pack_version,
            deadline_at=datetime.now(UTC) + timedelta(minutes=5),
            budget=RunBudget(
                max_model_calls=16,
                max_tool_calls=12,
                max_tokens=16_000,
                max_cost_usd=1,
            ),
            correlation_id=correlation_id,
        )
