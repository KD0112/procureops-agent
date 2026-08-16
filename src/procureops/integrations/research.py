from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol

from pydantic import TypeAdapter, ValidationError

from procureops.agents.research_evidence import ResearchEvidence
from procureops.harness.errors import PermanentToolError
from procureops.integrations.http import EnterpriseHTTPClient

EVIDENCE_LIST = TypeAdapter(list[ResearchEvidence])


class SupplierResearchConnector(Protocol):
    source_name: str

    def search(
        self,
        *,
        tenant_id: str,
        product_id: str,
        supplier_ids: tuple[str, ...],
        query: str,
    ) -> tuple[ResearchEvidence, ...]: ...


class LocalFileResearchConnector:
    source_name = "local_allowlisted_research"

    def __init__(self, path: Path) -> None:
        payload = json.loads(path.read_text(encoding="utf-8"))
        try:
            self.records = tuple(EVIDENCE_LIST.validate_python(payload))
        except ValidationError as exc:
            raise ValueError("invalid local supplier research evidence") from exc

    def search(
        self,
        *,
        tenant_id: str,
        product_id: str,
        supplier_ids: tuple[str, ...],
        query: str,
    ) -> tuple[ResearchEvidence, ...]:
        del query
        allowed = frozenset(supplier_ids)
        return tuple(
            item
            for item in self.records
            if item.tenant_id == tenant_id
            and item.supplier_id in allowed
            and item.product_id in {None, product_id}
        )


class HTTPResearchConnector:
    source_name = "http_allowlisted_research"

    def __init__(self, *, client: EnterpriseHTTPClient, path: str) -> None:
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("research endpoint path must be absolute")
        self.client = client
        self.path = path

    def search(
        self,
        *,
        tenant_id: str,
        product_id: str,
        supplier_ids: tuple[str, ...],
        query: str,
    ) -> tuple[ResearchEvidence, ...]:
        response = self.client.request_json(
            method="POST",
            path=self.path,
            tenant_id=tenant_id,
            json_body={
                "product_id": product_id,
                "supplier_ids": list(supplier_ids),
                "query": query,
            },
        )
        try:
            return tuple(EVIDENCE_LIST.validate_python(response.get("items")))
        except ValidationError as exc:
            raise PermanentToolError("invalid supplier research response schema") from exc


def research_connector_from_environment(project_root: Path) -> SupplierResearchConnector | None:
    profile = os.environ.get("PROCUREOPS_RESEARCH_PROFILE", "local").casefold()
    if profile == "disabled":
        return None
    if profile == "local":
        return LocalFileResearchConnector(
            project_root / "data" / "research" / "supplier_evidence_v1.json"
        )
    if profile != "http_allowlisted":
        raise ValueError("unsupported supplier research profile")
    base_url = os.environ.get("PROCUREOPS_RESEARCH_BASE_URL", "")
    api_key = os.environ.get("PROCUREOPS_RESEARCH_API_KEY", "")
    contract = os.environ.get("PROCUREOPS_RESEARCH_CONTRACT", "supplier.research.v1")
    path = os.environ.get("PROCUREOPS_RESEARCH_PATH", "/v1/supplier-evidence/search")
    if not base_url or not api_key:
        raise ValueError("allowlisted HTTP research configuration is incomplete")
    return HTTPResearchConnector(
        client=EnterpriseHTTPClient(
            system_name="supplier_research",
            base_url=base_url,
            api_key=api_key,
            contract_version=contract,
            timeout_seconds=float(
                os.environ.get("PROCUREOPS_RESEARCH_TIMEOUT_SECONDS", "5")
            ),
        ),
        path=path,
    )
