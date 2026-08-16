from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx

from procureops.agents.single import SingleAgentWorkflow, policy_for_tenant
from procureops.domain.enums import TaskStatus
from procureops.domain.models import RunBudget, RunContext
from procureops.harness.audit import InMemoryAuditSink
from procureops.harness.tool_gateway import ToolGateway
from procureops.intake import IntakeService
from procureops.integrations import EnterpriseHTTPClient, HTTPEnterpriseIntegrationSuite
from procureops.storage import ProcureOpsRepository
from procureops.tenancy import TenantPackRegistry
from procureops.tools import register_procurement_tools

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TENANT_ID = "tenant_enterprise_it"


def test_workflow_uses_http_erp_supplier_logistics_and_projects_external_po(
    repository: ProcureOpsRepository,
) -> None:
    def erp_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/catalog/search"):
            candidates = repository.search_products(
                tenant_id=TENANT_ID,
                query=request.url.params["query"],
                part_number=request.url.params["part_number"],
            )
            return httpx.Response(
                200,
                json={
                    "tenant_id": TENANT_ID,
                    "items": [item.model_dump(mode="json") for item in candidates],
                },
            )
        assert request.headers["idempotency-key"].startswith(f"po:{TENANT_ID}:")
        assert len(request.headers["x-procureops-approval-subject"]) == 64
        return httpx.Response(
            200,
            json={
                "tenant_id": TENANT_ID,
                "external_po_draft_id": "ERP-PO-DRAFT-001",
            },
        )

    def supplier_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        options = repository.supplier_options(
            tenant_id=TENANT_ID,
            product_id=body["product_id"],
            required_quantity=Decimal(body["quantity"]),
        )
        return httpx.Response(
            200,
            json={
                "tenant_id": TENANT_ID,
                "items": [item.model_dump(mode="json") for item in options],
            },
        )

    def logistics_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        quotes = repository.logistics_quotes(
            tenant_id=TENANT_ID,
            product_id=body["product_id"],
            supplier_ids=tuple(body["supplier_ids"]),
        )
        return httpx.Response(
            200,
            json={
                "tenant_id": TENANT_ID,
                "items": [item.model_dump(mode="json") for item in quotes],
            },
        )

    pack = TenantPackRegistry(PROJECT_ROOT / "data" / "tenant_packs").get(TENANT_ID)
    suite = HTTPEnterpriseIntegrationSuite(
        repository=repository,
        pack=pack,
        profile="http_sandbox",
        erp=_client("erp", "erp-v1", erp_handler),
        supplier=_client("supplier", "supplier-v1", supplier_handler),
        logistics=_client("logistics", "logistics-v1", logistics_handler),
    )
    audit = InMemoryAuditSink()
    gateway = ToolGateway(audit=audit)
    register_procurement_tools(gateway, repository, integrations=suite)
    workflow = SingleAgentWorkflow(
        repository=repository,
        tool_gateway=gateway,
        policy=policy_for_tenant(PROJECT_ROOT, TENANT_ID),
    )
    context = RunContext(
        run_id="http-run-1",
        task_id="http-task-1",
        tenant_id=TENANT_ID,
        actor_id="it-buyer",
        actor_roles=frozenset({"procurement_operator"}),
        workflow_version="1.1.0",
        prompt_version="1.0.0",
        model_policy_version="1.0.0",
        rule_set_version="1.0.0",
        tenant_pack_version="1.0.0",
        deadline_at=datetime.now(UTC) + timedelta(minutes=5),
        budget=RunBudget(max_tool_calls=12),
        correlation_id="http-corr-1",
    )

    pending = workflow.start(
        context=context,
        intake=IntakeService().from_text(
            "IT-LAP-DEV-14 | 研发笔记本 | 1 | 台",
            artifact_id="http-request.txt",
        ),
    )
    assert pending.status == TaskStatus.AWAITING_APPROVAL
    evidence = repository.evidence_for_task(tenant_id=TENANT_ID, task_id=context.task_id)
    assert any(item["source_type"] == "external_system_tool" for item in evidence)

    approval = workflow.issue_approval(
        context=context,
        result=pending,
        approved_by="it-approver",
        approved_by_roles=pending.approval_requirement.required_roles,
    )
    completed = workflow.resume(context=context, approval=approval)

    assert completed.status == TaskStatus.COMPLETED
    payload = json.loads(completed.po_draft["payload_json"])
    assert payload["external_receipt"]["external_po_draft_id"] == "ERP-PO-DRAFT-001"
    assert payload["external_receipt"]["profile"] == "http_sandbox"


def _client(
    system_name: str,
    contract_version: str,
    handler,
) -> EnterpriseHTTPClient:
    return EnterpriseHTTPClient(
        system_name=system_name,
        base_url=f"https://{system_name}.example.test",
        api_key="integration-test-key",
        contract_version=contract_version,
        transport=httpx.MockTransport(handler),
    )
