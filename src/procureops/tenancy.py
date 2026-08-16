from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

TENANT_ID_PATTERN = re.compile(r"^tenant_[a-z0-9_]+$")


class DemoSeedConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    catalog: str
    suppliers: str
    analytics: str | None = None


class TenantDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str
    display_name: str
    industry: str
    default_currency: str
    timezone: str
    locale: str
    tenant_pack_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    status: str = "active"
    demo_seed: DemoSeedConfig


class RulesConfig(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    ruleset_id: str
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    currency: str
    approval_thresholds: tuple[dict[str, Any], ...]
    prohibit_rag_for: tuple[str, ...]


class RetrievalConfig(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    retrieval_config_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    tenant_id: str
    require_citations: bool
    deny_dynamic_facts: bool


class ToolAdapterBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    system: str
    local_adapter: str
    http_contract: str
    http_path: str


class ToolAdaptersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    adapter_pack_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    supported_profiles: tuple[str, ...]
    adapters: dict[str, ToolAdapterBinding]
    external_side_effects_default: str

    @model_validator(mode="after")
    def required_tools_are_bound(self) -> ToolAdaptersConfig:
        required = {
            "catalog_lookup",
            "supplier_lookup",
            "logistics_quote",
            "purchase_order_draft",
        }
        if set(self.adapters) != required:
            raise ValueError(f"tool adapter bindings must be exactly {sorted(required)}")
        if "local" not in self.supported_profiles:
            raise ValueError("local integration profile is required")
        return self


class TenantPack(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    root: Path
    tenant: TenantDescriptor
    rules: RulesConfig
    retrieval: RetrievalConfig
    adapters: ToolAdaptersConfig
    product_schema: dict[str, Any]

    @model_validator(mode="after")
    def identifiers_are_consistent(self) -> TenantPack:
        if self.root.name != self.tenant.tenant_id:
            raise ValueError("tenant directory must match tenant_id")
        if self.retrieval.tenant_id != self.tenant.tenant_id:
            raise ValueError("retrieval tenant_id must match tenant descriptor")
        return self

    def seed_path(self, kind: str) -> Path:
        relative = getattr(self.tenant.demo_seed, kind)
        candidate = (self.root / relative).resolve()
        data_root = self.root.parents[1].resolve()
        try:
            candidate.relative_to(data_root)
        except ValueError as exc:
            raise ValueError("demo seed path must stay inside data directory") from exc
        return candidate


class TenantPackRegistry:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._packs = {pack.tenant.tenant_id: pack for pack in self._load_all()}

    def all(self) -> tuple[TenantPack, ...]:
        return tuple(self._packs[key] for key in sorted(self._packs))

    def get(self, tenant_id: str) -> TenantPack:
        if not TENANT_ID_PATTERN.fullmatch(tenant_id):
            raise ValueError("invalid tenant_id")
        try:
            return self._packs[tenant_id]
        except KeyError as exc:
            raise KeyError(f"tenant pack not found: {tenant_id}") from exc

    def _load_all(self) -> tuple[TenantPack, ...]:
        packs = tuple(
            self._load(directory)
            for directory in sorted(self.root.iterdir())
            if directory.is_dir() and TENANT_ID_PATTERN.fullmatch(directory.name)
        )
        identifiers = [pack.tenant.tenant_id for pack in packs]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("duplicate tenant_id in tenant packs")
        return packs

    @staticmethod
    def _load(root: Path) -> TenantPack:
        def read(name: str) -> dict[str, Any]:
            path = root / name
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError(f"tenant pack file must contain an object: {path}")
            return payload

        return TenantPack(
            root=root.resolve(),
            tenant=TenantDescriptor.model_validate(read("tenant.json")),
            rules=RulesConfig.model_validate(read("rules.json")),
            retrieval=RetrievalConfig.model_validate(read("retrieval.json")),
            adapters=ToolAdaptersConfig.model_validate(read("tool_adapters.json")),
            product_schema=read("product_schema.json"),
        )
