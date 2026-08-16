from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from procureops.integrations.adapters import (
    EnterpriseIntegrationSuite,
    HTTPEnterpriseIntegrationSuite,
    SQLiteEnterpriseIntegrationSuite,
)
from procureops.integrations.http import EnterpriseHTTPClient
from procureops.integrations.mcp import (
    MCPIntegrationConfig,
    MCPReadOnlyEnterpriseIntegrationSuite,
    StdioMCPTransport,
)
from procureops.storage import ProcureOpsRepository
from procureops.tenancy import TenantPackRegistry


@dataclass(frozen=True, slots=True)
class IntegrationStatus:
    profile: str
    enabled_systems: tuple[str, ...]
    production_credentials_configured: bool


class IntegrationSuiteFactory:
    def __init__(
        self,
        *,
        repository: ProcureOpsRepository,
        tenants: TenantPackRegistry,
        profile: str | None = None,
    ) -> None:
        self.repository = repository
        self.tenants = tenants
        self.profile = profile or os.environ.get("PROCUREOPS_INTEGRATION_PROFILE", "local")
        if self.profile not in {
            "local",
            "http_sandbox",
            "http_enterprise",
            "mcp_sandbox",
            "mcp_readonly",
        }:
            raise ValueError("unsupported integration profile")

    def for_tenant(self, tenant_id: str) -> EnterpriseIntegrationSuite:
        pack = self.tenants.get(tenant_id)
        if self.profile not in pack.adapters.supported_profiles:
            raise ValueError(f"integration profile is not supported by {tenant_id}")
        if self.profile == "local":
            return SQLiteEnterpriseIntegrationSuite(repository=self.repository, pack=pack)
        if self.profile in {"mcp_sandbox", "mcp_readonly"}:
            config = self._mcp_config(pack)
            return MCPReadOnlyEnterpriseIntegrationSuite(
                repository=self.repository,
                pack=pack,
                config=config,
                transport=StdioMCPTransport(),
                profile=self.profile,
            )
        sandbox = self.profile == "http_sandbox"
        default_url = "http://127.0.0.1:8101" if sandbox else ""
        key = os.environ.get(
            "PROCUREOPS_INTEGRATION_API_KEY",
            "local-only-not-a-secret" if sandbox else "",
        )
        timeout = float(os.environ.get("PROCUREOPS_INTEGRATION_TIMEOUT_SECONDS", "3"))
        erp = EnterpriseHTTPClient(
            system_name="erp",
            base_url=os.environ.get("PROCUREOPS_ERP_BASE_URL", default_url),
            api_key=key,
            contract_version=pack.adapters.adapters["catalog_lookup"].http_contract,
            timeout_seconds=timeout,
        )
        supplier = EnterpriseHTTPClient(
            system_name="supplier_network",
            base_url=os.environ.get("PROCUREOPS_SUPPLIER_BASE_URL", default_url),
            api_key=key,
            contract_version=pack.adapters.adapters["supplier_lookup"].http_contract,
            timeout_seconds=timeout,
        )
        logistics = EnterpriseHTTPClient(
            system_name="logistics",
            base_url=os.environ.get("PROCUREOPS_LOGISTICS_BASE_URL", default_url),
            api_key=key,
            contract_version=pack.adapters.adapters["logistics_quote"].http_contract,
            timeout_seconds=timeout,
        )
        return HTTPEnterpriseIntegrationSuite(
            repository=self.repository,
            pack=pack,
            profile=self.profile,
            erp=erp,
            supplier=supplier,
            logistics=logistics,
        )

    def status(self) -> IntegrationStatus:
        if self.profile.startswith("mcp_"):
            return IntegrationStatus(
                profile=self.profile,
                enabled_systems=("mcp_readonly",),
                production_credentials_configured=bool(
                    os.environ.get("PROCUREOPS_MCP_CONFIG")
                ),
            )
        configured = all(
            os.environ.get(name)
            for name in (
                "PROCUREOPS_ERP_BASE_URL",
                "PROCUREOPS_SUPPLIER_BASE_URL",
                "PROCUREOPS_LOGISTICS_BASE_URL",
                "PROCUREOPS_INTEGRATION_API_KEY",
            )
        )
        return IntegrationStatus(
            profile=self.profile,
            enabled_systems=("erp", "supplier_network", "logistics"),
            production_credentials_configured=bool(configured),
        )

    def _mcp_config(self, pack) -> MCPIntegrationConfig:
        if self.profile == "mcp_sandbox":
            project_root = pack.root.parents[2]
            return MCPIntegrationConfig.sandbox(
                command=(
                    sys.executable,
                    str(project_root / "scripts" / "run_mcp_sandbox.py"),
                    "--database",
                    str(self.repository.database.path),
                )
            )
        config_path = os.environ.get("PROCUREOPS_MCP_CONFIG")
        if not config_path:
            raise ValueError("PROCUREOPS_MCP_CONFIG is required for mcp_readonly")
        return MCPIntegrationConfig.from_file(Path(config_path).expanduser().resolve())
