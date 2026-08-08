from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from procureops.agents.supplier_research import BoundedSupplierResearchAgent
from procureops.domain.costing import calculate_line_cost, summarize_costs
from procureops.domain.enums import TaskStatus
from procureops.domain.models import ApprovalGrant, RunContext
from procureops.domain.policy import ApprovalRequirement, ProcurementPolicy
from procureops.domain.procurement import (
    CostSummary,
    LogisticsQuote,
    ProductCandidate,
    SupplierOption,
)
from procureops.harness.budget import RunBudgetLedger
from procureops.harness.tool_gateway import ToolGateway
from procureops.intake import IntakeResult
from procureops.memory import MemoryService, PreferenceDecisionEngine
from procureops.rag import RetrievalHit, Retriever
from procureops.storage import ProcureOpsRepository


class WorkflowResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str
    status: TaskStatus
    questions: tuple[str, ...] = ()
    approval_requirement: ApprovalRequirement | None = None
    approval_subject: dict[str, Any] | None = None
    cost_summary: CostSummary | None = None
    po_draft: dict[str, Any] | None = None


class SingleAgentWorkflow:
    """One bounded agent that orchestrates typed tools and deterministic state."""

    def __init__(
        self,
        *,
        repository: ProcureOpsRepository,
        tool_gateway: ToolGateway,
        policy: ProcurementPolicy,
        phase_observer: Callable[[str, dict[str, Any]], None] | None = None,
        retriever: Retriever | None = None,
        memory_service: MemoryService | None = None,
        supplier_researcher: BoundedSupplierResearchAgent | None = None,
        run_ledger: RunBudgetLedger | None = None,
    ) -> None:
        self.repository = repository
        self.tool_gateway = tool_gateway
        self.policy = policy
        self.phase_observer = phase_observer
        self.retriever = retriever
        self.memory_service = memory_service
        self.supplier_researcher = supplier_researcher
        self.run_ledger = run_ledger
        self.preference_engine = PreferenceDecisionEngine()

    def start(
        self,
        *,
        context: RunContext,
        intake: IntakeResult,
    ) -> WorkflowResult:
        try:
            task = self.repository.get_task(
                tenant_id=context.tenant_id,
                task_id=context.task_id,
            )
        except KeyError:
            task = self.repository.create_task(
                tenant_id=context.tenant_id,
                created_by=context.actor_id,
                request={
                    "artifact_id": intake.artifact_id,
                    "source_type": intake.source_type,
                    "source_sha256": intake.source_sha256,
                },
                workflow_version=context.workflow_version,
                task_id=context.task_id,
            )
        if TaskStatus(task.status) not in {TaskStatus.DRAFT, TaskStatus.NEEDS_INPUT}:
            raise ValueError(f"task cannot accept new intake from status {task.status}")
        self._emit(
            "intake",
            {"line_count": len(intake.lines), "questions": list(intake.questions)},
        )
        task = self._transition(task.status, task.version, context, TaskStatus.INGESTING)
        self.repository.replace_task_items(
            tenant_id=context.tenant_id,
            task_id=context.task_id,
            lines=list(intake.lines),
        )
        items = {
            int(row["line_number"]): row
            for row in self.repository.task_items(
                tenant_id=context.tenant_id,
                task_id=context.task_id,
            )
        }
        for item in intake.evidence:
            row = items.get(item.line_number or -1)
            self.repository.add_evidence(
                tenant_id=context.tenant_id,
                task_id=context.task_id,
                item_id=str(row["item_id"]) if row else None,
                field_name=item.field_name,
                value=(
                    intake.lines[item.line_number - 1].model_dump().get(item.field_name)
                    if item.line_number
                    else intake.source_sha256
                ),
                source_type=item.source_type,
                source_id=item.source_id,
                locator=item.locator,
                observed_at=datetime.now(UTC),
                valid_until=None,
                confidence=Decimal(str(item.confidence)),
                producer="intake_service_v1",
            )
        rag_hits = self._retrieve_context(context=context, intake=intake)
        for hit in rag_hits:
            self.repository.add_evidence(
                tenant_id=context.tenant_id,
                task_id=context.task_id,
                item_id=None,
                field_name="rag_context",
                value=hit.document_sha256,
                source_type="rag_document",
                source_id=hit.document_id,
                locator=hit.citation,
                observed_at=datetime.now(UTC),
                valid_until=None,
                confidence=Decimal(str(hit.score)),
                producer="governed_retriever_v1",
            )
        if intake.questions or not intake.lines:
            task = self._transition(
                task.status,
                task.version,
                context,
                TaskStatus.NEEDS_INPUT,
            )
            return WorkflowResult(
                task_id=context.task_id,
                status=TaskStatus(task.status),
                questions=intake.questions,
            )

        memory_preferences = self._confirmed_memory(context)
        task = self._transition(task.status, task.version, context, TaskStatus.MATCHING)
        ledger = self.run_ledger or RunBudgetLedger(context)
        matched: list[tuple[dict[str, Any], ProductCandidate]] = []
        questions: list[str] = []
        for line in intake.lines:
            response = self.tool_gateway.execute(
                context=context,
                ledger=ledger,
                tool_name="catalog_lookup",
                arguments={
                    "tenant_id": context.tenant_id,
                    "query": line.description,
                    "part_number": line.part_number,
                },
            )
            candidates = [ProductCandidate.model_validate(item) for item in response.output]
            self._emit(
                "catalog",
                {
                    "line_number": line.line_number,
                    "candidates": [item.model_dump(mode="json") for item in candidates],
                },
            )
            if not candidates or candidates[0].score < Decimal("0.90"):
                questions.append(
                    f"第 {line.line_number} 行无法唯一匹配，请补充零件号或设备铭牌。"
                )
                continue
            if (
                len(candidates) > 1
                and candidates[0].score - candidates[1].score < Decimal("0.05")
            ):
                questions.append(f"第 {line.line_number} 行存在接近候选，需要人工选择。")
                continue
            candidate = candidates[0]
            row = items[line.line_number]
            self.repository.select_product(
                tenant_id=context.tenant_id,
                task_id=context.task_id,
                line_number=line.line_number,
                candidate=candidate,
            )
            self.repository.add_evidence(
                tenant_id=context.tenant_id,
                task_id=context.task_id,
                item_id=str(row["item_id"]),
                field_name="matched_product_id",
                value=candidate.product_id,
                source_type="database",
                source_id=candidate.product_id,
                locator=f"products:{candidate.product_id}",
                observed_at=datetime.now(UTC),
                valid_until=None,
                confidence=candidate.score,
                producer="catalog_lookup_v1",
            )
            matched.append((row, candidate))
        if questions:
            task = self._transition(
                task.status,
                task.version,
                context,
                TaskStatus.NEEDS_INPUT,
            )
            return WorkflowResult(
                task_id=context.task_id,
                status=TaskStatus(task.status),
                questions=tuple(questions),
            )

        task = self._transition(task.status, task.version, context, TaskStatus.SOURCING)
        cost_lines = []
        for line, (row, candidate) in zip(intake.lines, matched, strict=True):
            response = self.tool_gateway.execute(
                context=context,
                ledger=ledger,
                tool_name="supplier_lookup",
                arguments={
                    "tenant_id": context.tenant_id,
                    "product_id": candidate.product_id,
                    "quantity": str(line.quantity),
                },
            )
            options = [SupplierOption.model_validate(item) for item in response.output]
            self._emit(
                "supplier",
                {
                    "line_number": line.line_number,
                    "options": [item.model_dump(mode="json") for item in options],
                },
            )
            approved_options = [item for item in options if item.approved]
            if not approved_options:
                task = self._transition(
                    task.status,
                    task.version,
                    context,
                    TaskStatus.NEEDS_INPUT,
                )
                return WorkflowResult(
                    task_id=context.task_id,
                    status=TaskStatus(task.status),
                    questions=(f"第 {line.line_number} 行没有有效的准入供应商报价。",),
                )
            research_result = None
            if self.supplier_researcher is not None:
                research_result = self.supplier_researcher.research(
                    context=context,
                    ledger=ledger,
                    product_id=candidate.product_id,
                    quantity=line.quantity,
                    options=tuple(options),
                    confirmed_preferences=memory_preferences,
                    explicit_strategy=task.request.get("supplier_strategy"),
                )
                decision = research_result.decision
            else:
                logistics_response = self.tool_gateway.execute(
                    context=context,
                    ledger=ledger,
                    tool_name="logistics_quote",
                    arguments={
                        "tenant_id": context.tenant_id,
                        "product_id": candidate.product_id,
                        "supplier_ids": [item.supplier_id for item in approved_options],
                    },
                )
                logistics = tuple(
                    LogisticsQuote.model_validate(item)
                    for item in logistics_response.output
                )
                decision = self.preference_engine.select_supplier(
                    options=tuple(options),
                    logistics=logistics,
                    quantity=line.quantity,
                    confirmed_preferences=memory_preferences,
                    explicit_strategy=task.request.get("supplier_strategy"),
                )
            selected = decision.selected
            self.repository.select_supplier(
                tenant_id=context.tenant_id,
                task_id=context.task_id,
                line_number=line.line_number,
                option=selected,
            )
            for field_name, value in (
                ("unit_price", selected.unit_price),
                ("tax_rate", selected.tax_rate),
                ("available_quantity", selected.available_quantity),
                ("selected_supplier_id", selected.supplier_id),
            ):
                self.repository.add_evidence(
                    tenant_id=context.tenant_id,
                    task_id=context.task_id,
                    item_id=str(row["item_id"]),
                    field_name=field_name,
                    value=value,
                    source_type="database_tool",
                    source_id=selected.quotation_id,
                    locator=f"quotation:{selected.quotation_id}",
                    observed_at=selected.observed_at,
                    valid_until=selected.valid_until,
                    confidence=Decimal("1"),
                    producer="supplier_lookup_v1",
                )
            self.repository.add_evidence(
                tenant_id=context.tenant_id,
                task_id=context.task_id,
                item_id=str(row["item_id"]),
                field_name="logistics_lead_time_days",
                value=decision.logistics_quote.lead_time_days,
                source_type="database_tool",
                source_id=decision.logistics_quote.logistics_quote_id,
                locator=(
                    f"logistics_quotes:{decision.logistics_quote.logistics_quote_id}"
                ),
                observed_at=decision.logistics_quote.observed_at,
                valid_until=decision.logistics_quote.valid_until,
                confidence=Decimal("1"),
                producer="logistics_quote_v1",
            )
            self.repository.add_evidence(
                tenant_id=context.tenant_id,
                task_id=context.task_id,
                item_id=str(row["item_id"]),
                field_name="freight",
                value=decision.logistics_quote.shipping_cost,
                source_type="database_tool",
                source_id=decision.logistics_quote.logistics_quote_id,
                locator=(
                    f"logistics_quotes:{decision.logistics_quote.logistics_quote_id}"
                ),
                observed_at=decision.logistics_quote.observed_at,
                valid_until=decision.logistics_quote.valid_until,
                confidence=Decimal("1"),
                producer="logistics_quote_v1",
            )
            self.repository.add_evidence(
                tenant_id=context.tenant_id,
                task_id=context.task_id,
                item_id=str(row["item_id"]),
                field_name="supplier_selection_strategy",
                value=decision.strategy,
                source_type=decision.strategy_source,
                source_id=(
                    context.actor_id
                    if decision.strategy_source == "confirmed_memory"
                    else context.tenant_id
                ),
                locator=f"preference_strategy:{decision.strategy_source}",
                observed_at=datetime.now(UTC),
                valid_until=None,
                confidence=Decimal("1"),
                producer="preference_decision_engine_v1",
            )
            self.repository.append_workflow_event(
                tenant_id=context.tenant_id,
                task_id=context.task_id,
                event_type="supplier.selection_decided",
                payload={
                    "line_number": line.line_number,
                    "selected_supplier_id": selected.supplier_id,
                    "strategy": decision.strategy,
                    "strategy_source": decision.strategy_source,
                    "ranked_supplier_ids": list(decision.ranked_supplier_ids),
                    "logistics_quote_id": (
                        decision.logistics_quote.logistics_quote_id
                    ),
                    "lead_time_days": decision.logistics_quote.lead_time_days,
                    "model_recommendation": (
                        research_result.model_recommendation
                        if research_result is not None
                        else None
                    ),
                    "used_fallback": (
                        research_result.used_fallback
                        if research_result is not None
                        else False
                    ),
                    "research_steps": (
                        [item.model_dump(mode="json") for item in research_result.steps]
                        if research_result is not None
                        else []
                    ),
                },
            )
            cost_lines.append(
                calculate_line_cost(
                    line_number=line.line_number,
                    quantity=line.quantity,
                    option=selected,
                    freight_override=decision.logistics_quote.shipping_cost,
                )
            )

        task = self._transition(task.status, task.version, context, TaskStatus.CALCULATING)
        summary = summarize_costs(cost_lines, currency="CNY")
        task = self._transition(task.status, task.version, context, TaskStatus.RISK_REVIEW)
        requirement = self.policy.approval_requirement(
            total_amount=summary.total_amount,
            currency=summary.currency,
            action="purchase_order_draft",
        )
        self._emit(
            "policy",
            {
                "requirement": requirement.model_dump(mode="json"),
                "evidence_count": len(
                    self.repository.evidence_for_task(
                        tenant_id=context.tenant_id,
                        task_id=context.task_id,
                    )
                ),
            },
        )
        po_arguments = {
            "tenant_id": context.tenant_id,
            "task_id": context.task_id,
            "po_idempotency_key": f"po:{context.tenant_id}:{context.task_id}:v1",
            "payload": {
                "task_id": context.task_id,
                "lines": [line.model_dump(mode="json") for line in summary.lines],
                "evidence_count": len(
                    self.repository.evidence_for_task(
                        tenant_id=context.tenant_id,
                        task_id=context.task_id,
                    )
                ),
                "rag_citations": [hit.citation for hit in rag_hits],
                "confirmed_user_preferences": memory_preferences,
            },
            "total_amount": str(summary.total_amount),
            "currency": summary.currency,
        }
        self.repository.append_workflow_event(
            tenant_id=context.tenant_id,
            task_id=context.task_id,
            event_type="approval.requested",
            payload={
                "tool_arguments": po_arguments,
                "approval_requirement": requirement.model_dump(mode="json"),
            },
        )
        task = self._transition(
            task.status,
            task.version,
            context,
            TaskStatus.AWAITING_APPROVAL,
        )
        return WorkflowResult(
            task_id=context.task_id,
            status=TaskStatus(task.status),
            approval_requirement=requirement,
            approval_subject=po_arguments,
            cost_summary=summary,
        )

    def issue_approval(
        self,
        *,
        context: RunContext,
        result: WorkflowResult,
        approved_by: str,
        approved_by_roles: frozenset[str],
        ttl: timedelta = timedelta(minutes=30),
    ) -> ApprovalGrant:
        if result.approval_requirement is None or result.approval_subject is None:
            raise ValueError("workflow result has no pending approval")
        now = datetime.now(UTC)
        grant = ApprovalGrant.bind(
            approval_id=str(uuid4()),
            tenant_id=context.tenant_id,
            task_id=context.task_id,
            action=result.approval_requirement.action,
            subject=result.approval_subject,
            approved_by=approved_by,
            approved_by_roles=approved_by_roles,
            approved_at=now,
            expires_at=now + ttl,
        )
        self.policy.validate_grant_roles(grant, result.approval_requirement)
        return grant

    def resume(
        self,
        *,
        context: RunContext,
        approval: ApprovalGrant,
    ) -> WorkflowResult:
        task = self.repository.get_task(
            tenant_id=context.tenant_id,
            task_id=context.task_id,
        )
        if TaskStatus(task.status) == TaskStatus.COMPLETED:
            return WorkflowResult(
                task_id=context.task_id,
                status=TaskStatus.COMPLETED,
                po_draft=self.repository.po_draft_for_task(
                    tenant_id=context.tenant_id,
                    task_id=context.task_id,
                ),
            )
        event = self._latest_approval_event(context)
        payload = json.loads(event["payload_json"])
        arguments = payload["tool_arguments"]
        requirement = ApprovalRequirement.model_validate(payload["approval_requirement"])
        self.policy.validate_grant_roles(approval, requirement)
        if TaskStatus(task.status) == TaskStatus.AWAITING_APPROVAL:
            self.repository.save_approval(approval)
            task = self._transition(
                task.status,
                task.version,
                context,
                TaskStatus.APPROVED,
            )
        if TaskStatus(task.status) == TaskStatus.APPROVED:
            result = self.tool_gateway.execute(
                context=context,
                ledger=RunBudgetLedger(context),
                tool_name="purchase_order_draft",
                arguments=arguments,
                approval=approval,
                idempotency_key=str(arguments["po_idempotency_key"]),
            )
            self.repository.append_workflow_event(
                tenant_id=context.tenant_id,
                task_id=context.task_id,
                event_type="po.drafted",
                payload=result.output,
            )
            task = self._transition(
                task.status,
                task.version,
                context,
                TaskStatus.PO_DRAFTED,
            )
        if TaskStatus(task.status) == TaskStatus.PO_DRAFTED:
            task = self._transition(
                task.status,
                task.version,
                context,
                TaskStatus.COMPLETED,
            )
        return WorkflowResult(
            task_id=context.task_id,
            status=TaskStatus(task.status),
            po_draft=self.repository.po_draft_for_task(
                tenant_id=context.tenant_id,
                task_id=context.task_id,
            ),
        )

    def _latest_approval_event(self, context: RunContext) -> dict[str, Any]:
        events = self.repository.workflow_events(
            tenant_id=context.tenant_id,
            task_id=context.task_id,
        )
        matching = [event for event in events if event["event_type"] == "approval.requested"]
        if not matching:
            raise RuntimeError("approval request event not found")
        return matching[-1]

    def _transition(
        self,
        current_status: str,
        version: int,
        context: RunContext,
        target: TaskStatus,
    ):
        snapshot = self.repository.transition_task(
            tenant_id=context.tenant_id,
            task_id=context.task_id,
            target=target,
            expected_version=version,
        )
        self.repository.append_workflow_event(
            tenant_id=context.tenant_id,
            task_id=context.task_id,
            event_type="task.transitioned",
            payload={"from": current_status, "to": target, "version": snapshot.version},
        )
        return snapshot

    def _emit(self, phase: str, payload: dict[str, Any]) -> None:
        if self.phase_observer is not None:
            self.phase_observer(phase, payload)

    def _retrieve_context(
        self,
        *,
        context: RunContext,
        intake: IntakeResult,
    ) -> tuple[RetrievalHit, ...]:
        if self.retriever is None or not intake.lines:
            return ()
        query = " ".join(line.description for line in intake.lines)
        return self.retriever.search(
            tenant_id=context.tenant_id,
            actor_roles=context.actor_roles,
            query=query,
            minimum_score=0.2,
        )

    def _confirmed_memory(self, context: RunContext) -> dict[str, Any]:
        if self.memory_service is None:
            return {}
        records = self.memory_service.active_records(
            tenant_id=context.tenant_id,
            user_id=context.actor_id,
        )
        preferences: dict[str, Any] = {}
        for record in records:
            preferences[record.memory_key] = record.value
            self.repository.add_evidence(
                tenant_id=context.tenant_id,
                task_id=context.task_id,
                item_id=None,
                field_name=f"memory.{record.memory_key}",
                value=record.value,
                source_type="confirmed_memory",
                source_id=record.record_id,
                locator=f"memory:{record.record_id}",
                observed_at=record.confirmed_at or record.created_at,
                valid_until=record.expires_at,
                confidence=Decimal(str(record.confidence)),
                producer="memory_service_v1",
            )
        return preferences


def default_policy(project_root: Path) -> ProcurementPolicy:
    return ProcurementPolicy.from_file(
        project_root
        / "data"
        / "tenant_packs"
        / "tenant_engineering_machinery"
        / "rules.json"
    )
