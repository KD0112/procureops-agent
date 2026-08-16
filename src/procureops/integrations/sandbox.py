from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from procureops.demo import seed_demo_database
from procureops.harness.errors import IdempotencyConflict
from procureops.storage import SQLiteDatabase


class SupplierOptionsRequest(BaseModel):
    product_id: str = Field(min_length=1)
    quantity: Decimal = Field(gt=0)


class LogisticsQuotesRequest(BaseModel):
    product_id: str = Field(min_length=1)
    supplier_ids: tuple[str, ...] = Field(min_length=1)


class PurchaseOrderDraftRequest(BaseModel):
    task_id: str = Field(min_length=1)
    payload: dict[str, Any]
    total_amount: Decimal = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)


def create_integration_sandbox(
    *,
    project_root: Path,
    database_path: Path,
    api_key: str | None = None,
) -> FastAPI:
    repository = seed_demo_database(
        SQLiteDatabase(database_path),
        project_root=project_root,
    )
    expected_key = api_key or os.environ.get(
        "PROCUREOPS_INTEGRATION_API_KEY",
        "local-only-not-a-secret",
    )
    app = FastAPI(
        title="ProcureOps Enterprise Integration Sandbox",
        version="1.0.0",
        description=(
            "Independent local contract simulator for ERP, supplier and logistics APIs. "
            "It is not a production vendor system."
        ),
    )

    def authorize(
        authorization: str,
        tenant_id: str,
        contract_version: str,
    ) -> None:
        if authorization != f"Bearer {expected_key}":
            raise HTTPException(status_code=401, detail="invalid service credential")
        if not tenant_id or not contract_version:
            raise HTTPException(status_code=400, detail="tenant and contract headers required")
        if not repository.tenant_exists(tenant_id):
            raise HTTPException(status_code=404, detail="tenant not found")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "kind": "local_contract_simulator"}

    @app.get("/erp/v1/catalog/search")
    def catalog_search(
        query: str,
        part_number: str = "",
        authorization: Annotated[str, Header()] = "",
        x_tenant_id: Annotated[str, Header()] = "",
        x_procureops_contract_version: Annotated[str, Header()] = "",
    ) -> dict[str, Any]:
        authorize(authorization, x_tenant_id, x_procureops_contract_version)
        items = repository.search_products(
            tenant_id=x_tenant_id,
            query=query,
            part_number=part_number or None,
        )
        return {
            "tenant_id": x_tenant_id,
            "items": [item.model_dump(mode="json") for item in items],
        }

    @app.post("/supplier/v1/options")
    def supplier_options(
        request: SupplierOptionsRequest,
        authorization: Annotated[str, Header()] = "",
        x_tenant_id: Annotated[str, Header()] = "",
        x_procureops_contract_version: Annotated[str, Header()] = "",
    ) -> dict[str, Any]:
        authorize(authorization, x_tenant_id, x_procureops_contract_version)
        items = repository.supplier_options(
            tenant_id=x_tenant_id,
            product_id=request.product_id,
            required_quantity=request.quantity,
        )
        return {
            "tenant_id": x_tenant_id,
            "items": [item.model_dump(mode="json") for item in items],
        }

    @app.post("/logistics/v1/quotes")
    def logistics_quotes(
        request: LogisticsQuotesRequest,
        authorization: Annotated[str, Header()] = "",
        x_tenant_id: Annotated[str, Header()] = "",
        x_procureops_contract_version: Annotated[str, Header()] = "",
    ) -> dict[str, Any]:
        authorize(authorization, x_tenant_id, x_procureops_contract_version)
        items = repository.logistics_quotes(
            tenant_id=x_tenant_id,
            product_id=request.product_id,
            supplier_ids=request.supplier_ids,
        )
        return {
            "tenant_id": x_tenant_id,
            "items": [item.model_dump(mode="json") for item in items],
        }

    @app.post("/erp/v1/purchase-orders/drafts")
    def purchase_order_draft(
        request: PurchaseOrderDraftRequest,
        authorization: Annotated[str, Header()] = "",
        x_tenant_id: Annotated[str, Header()] = "",
        x_procureops_contract_version: Annotated[str, Header()] = "",
        idempotency_key: Annotated[str, Header()] = "",
        x_procureops_approval_subject: Annotated[str, Header()] = "",
    ) -> dict[str, Any]:
        authorize(authorization, x_tenant_id, x_procureops_contract_version)
        if not idempotency_key:
            raise HTTPException(status_code=400, detail="Idempotency-Key required")
        if len(x_procureops_approval_subject) != 64:
            raise HTTPException(status_code=403, detail="approval subject hash required")
        try:
            try:
                repository.get_task(tenant_id=x_tenant_id, task_id=request.task_id)
            except KeyError:
                repository.create_task(
                    tenant_id=x_tenant_id,
                    created_by="procureops-service",
                    request={"source": "external_po_contract"},
                    workflow_version="sandbox-erp-v1",
                    task_id=request.task_id,
                )
            row, hit = repository.create_po_draft(
                tenant_id=x_tenant_id,
                task_id=request.task_id,
                idempotency_key=idempotency_key,
                payload={
                    **request.payload,
                    "upstream_approval_subject": x_procureops_approval_subject,
                },
                total_amount=request.total_amount,
                currency=request.currency,
            )
        except (IdempotencyConflict, KeyError) as exc:
            raise HTTPException(status_code=409, detail=type(exc).__name__) from exc
        return {
            "tenant_id": x_tenant_id,
            "external_po_draft_id": row["po_draft_id"],
            "idempotency_hit": hit,
            "status": "draft",
        }

    return app
