from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any

from pydantic import TypeAdapter, ValidationError

from procureops.domain.models import canonical_hash
from procureops.domain.procurement import LogisticsQuote, ProductCandidate, SupplierOption
from procureops.harness.errors import PermanentToolError
from procureops.integrations.http import EnterpriseHTTPClient
from procureops.storage import ProcureOpsRepository
from procureops.tenancy import TenantPack

PRODUCT_LIST = TypeAdapter(list[ProductCandidate])
SUPPLIER_LIST = TypeAdapter(list[SupplierOption])
LOGISTICS_LIST = TypeAdapter(list[LogisticsQuote])


class EnterpriseIntegrationSuite(ABC):
    profile: str

    def __init__(self, *, repository: ProcureOpsRepository, pack: TenantPack) -> None:
        self.repository = repository
        self.pack = pack

    @abstractmethod
    def catalog_lookup(
        self,
        *,
        tenant_id: str,
        query: str,
        part_number: str | None,
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def supplier_lookup(
        self,
        *,
        tenant_id: str,
        product_id: str,
        quantity: Decimal,
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def logistics_quote(
        self,
        *,
        tenant_id: str,
        product_id: str,
        supplier_ids: tuple[str, ...],
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def purchase_order_draft(
        self,
        *,
        tenant_id: str,
        task_id: str,
        idempotency_key: str,
        payload: dict[str, Any],
        total_amount: Decimal,
        currency: str,
        approval_subject_hash: str,
    ) -> dict[str, Any]: ...


class SQLiteEnterpriseIntegrationSuite(EnterpriseIntegrationSuite):
    profile = "local"

    def catalog_lookup(
        self,
        *,
        tenant_id: str,
        query: str,
        part_number: str | None,
    ) -> list[dict[str, Any]]:
        items = self.repository.search_products(
            tenant_id=tenant_id,
            query=query,
            part_number=part_number,
        )
        return [
            item.model_copy(
                update={
                    "source_system": "local_erp_projection",
                    "source_locator": f"products:{item.product_id}",
                }
            ).model_dump(mode="json")
            for item in items
        ]

    def supplier_lookup(
        self,
        *,
        tenant_id: str,
        product_id: str,
        quantity: Decimal,
    ) -> list[dict[str, Any]]:
        items = self.repository.supplier_options(
            tenant_id=tenant_id,
            product_id=product_id,
            required_quantity=quantity,
        )
        return [
            item.model_copy(
                update={
                    "source_system": "local_supplier_projection",
                    "source_locator": f"quotation:{item.quotation_id}",
                }
            ).model_dump(mode="json")
            for item in items
        ]

    def logistics_quote(
        self,
        *,
        tenant_id: str,
        product_id: str,
        supplier_ids: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        items = self.repository.logistics_quotes(
            tenant_id=tenant_id,
            product_id=product_id,
            supplier_ids=supplier_ids,
        )
        return [
            item.model_copy(
                update={
                    "source_system": "local_logistics_projection",
                    "source_locator": f"logistics_quotes:{item.logistics_quote_id}",
                }
            ).model_dump(mode="json")
            for item in items
        ]

    def purchase_order_draft(
        self,
        *,
        tenant_id: str,
        task_id: str,
        idempotency_key: str,
        payload: dict[str, Any],
        total_amount: Decimal,
        currency: str,
        approval_subject_hash: str,
    ) -> dict[str, Any]:
        del approval_subject_hash
        row, database_hit = self.repository.create_po_draft(
            tenant_id=tenant_id,
            task_id=task_id,
            idempotency_key=idempotency_key,
            payload=payload,
            total_amount=total_amount,
            currency=currency,
        )
        return {
            "po_draft": row,
            "database_idempotency_hit": database_hit,
            "integration_profile": self.profile,
            "source_system": "local_erp_projection",
        }


class HTTPEnterpriseIntegrationSuite(EnterpriseIntegrationSuite):
    def __init__(
        self,
        *,
        repository: ProcureOpsRepository,
        pack: TenantPack,
        profile: str,
        erp: EnterpriseHTTPClient,
        supplier: EnterpriseHTTPClient,
        logistics: EnterpriseHTTPClient,
    ) -> None:
        super().__init__(repository=repository, pack=pack)
        if profile not in {"http_sandbox", "http_enterprise"}:
            raise ValueError("invalid HTTP integration profile")
        self.profile = profile
        self.erp = erp
        self.supplier = supplier
        self.logistics = logistics

    def catalog_lookup(
        self,
        *,
        tenant_id: str,
        query: str,
        part_number: str | None,
    ) -> list[dict[str, Any]]:
        binding = self.pack.adapters.adapters["catalog_lookup"]
        response = self.erp.request_json(
            method="GET",
            path=binding.http_path,
            tenant_id=tenant_id,
            query={"query": query, "part_number": part_number or ""},
            contract_version=binding.http_contract,
        )
        items = self._validate(PRODUCT_LIST, response.get("items"), "ERP catalog")
        return [
            item.model_copy(
                update={
                    "source_system": f"erp:{self.profile}",
                    "source_locator": f"{binding.http_contract}:{item.product_id}",
                }
            ).model_dump(mode="json")
            for item in items
        ]

    def supplier_lookup(
        self,
        *,
        tenant_id: str,
        product_id: str,
        quantity: Decimal,
    ) -> list[dict[str, Any]]:
        binding = self.pack.adapters.adapters["supplier_lookup"]
        response = self.supplier.request_json(
            method="POST",
            path=binding.http_path,
            tenant_id=tenant_id,
            json_body={"product_id": product_id, "quantity": str(quantity)},
            contract_version=binding.http_contract,
        )
        items = self._validate(SUPPLIER_LIST, response.get("items"), "supplier options")
        return [
            item.model_copy(
                update={
                    "source_system": f"supplier_network:{self.profile}",
                    "source_locator": f"{binding.http_contract}:{item.quotation_id}",
                }
            ).model_dump(mode="json")
            for item in items
        ]

    def logistics_quote(
        self,
        *,
        tenant_id: str,
        product_id: str,
        supplier_ids: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        binding = self.pack.adapters.adapters["logistics_quote"]
        response = self.logistics.request_json(
            method="POST",
            path=binding.http_path,
            tenant_id=tenant_id,
            json_body={"product_id": product_id, "supplier_ids": list(supplier_ids)},
            contract_version=binding.http_contract,
        )
        items = self._validate(LOGISTICS_LIST, response.get("items"), "logistics quotes")
        return [
            item.model_copy(
                update={
                    "source_system": f"logistics:{self.profile}",
                    "source_locator": (
                        f"{binding.http_contract}:{item.logistics_quote_id}"
                    ),
                }
            ).model_dump(mode="json")
            for item in items
        ]

    def purchase_order_draft(
        self,
        *,
        tenant_id: str,
        task_id: str,
        idempotency_key: str,
        payload: dict[str, Any],
        total_amount: Decimal,
        currency: str,
        approval_subject_hash: str,
    ) -> dict[str, Any]:
        binding = self.pack.adapters.adapters["purchase_order_draft"]
        response = self.erp.request_json(
            method="POST",
            path=binding.http_path,
            tenant_id=tenant_id,
            json_body={
                "task_id": task_id,
                "payload": payload,
                "total_amount": str(total_amount),
                "currency": currency,
            },
            idempotency_key=idempotency_key,
            approval_subject_hash=approval_subject_hash,
            contract_version=binding.http_contract,
        )
        external_id = response.get("external_po_draft_id")
        if not isinstance(external_id, str) or not external_id:
            raise PermanentToolError("ERP PO response is missing external_po_draft_id")
        projected_payload = {
            **payload,
            "external_receipt": {
                "system": "erp",
                "profile": self.profile,
                "external_po_draft_id": external_id,
                "contract": binding.http_contract,
                "response_hash": canonical_hash(response),
            },
        }
        row, database_hit = self.repository.create_po_draft(
            tenant_id=tenant_id,
            task_id=task_id,
            idempotency_key=idempotency_key,
            payload=projected_payload,
            total_amount=total_amount,
            currency=currency,
        )
        return {
            "po_draft": row,
            "database_idempotency_hit": database_hit,
            "integration_profile": self.profile,
            "source_system": "erp",
            "external_po_draft_id": external_id,
        }

    @staticmethod
    def _validate(adapter: TypeAdapter, payload: Any, label: str) -> list[Any]:
        try:
            return adapter.validate_python(payload)
        except ValidationError as exc:
            raise PermanentToolError(f"invalid {label} response schema") from exc
