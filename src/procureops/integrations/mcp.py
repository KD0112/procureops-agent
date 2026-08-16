from __future__ import annotations

import json
import subprocess
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from procureops.harness.errors import PermanentToolError, TransientToolError
from procureops.integrations.adapters import (
    LOGISTICS_LIST,
    PRODUCT_LIST,
    SUPPLIER_LIST,
    EnterpriseIntegrationSuite,
)
from procureops.storage import ProcureOpsRepository
from procureops.tenancy import TenantPack


class MCPServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_.-]+$")
    command: tuple[str, ...] = Field(min_length=1, max_length=20)
    allowed_tools: frozenset[str] = Field(min_length=1, max_length=50)
    cwd: Path | None = None
    timeout_seconds: float = Field(default=5.0, gt=0, le=30)


class MCPToolBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    server: str
    tool: str


class MCPIntegrationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    servers: dict[str, MCPServerConfig]
    bindings: dict[str, MCPToolBinding]

    @model_validator(mode="after")
    def validate_read_only_bindings(self) -> MCPIntegrationConfig:
        required = {"catalog_lookup", "supplier_lookup", "logistics_quote"}
        if set(self.bindings) != required:
            raise ValueError(f"MCP bindings must be exactly {sorted(required)}")
        for logical_name, binding in self.bindings.items():
            server = self.servers.get(binding.server)
            if server is None:
                raise ValueError(f"unknown MCP server for {logical_name}")
            if server.name != binding.server:
                raise ValueError("MCP server key must match its configured name")
            if binding.tool not in server.allowed_tools:
                raise ValueError(f"MCP tool is not allowlisted for {logical_name}")
        return self

    @classmethod
    def from_file(cls, path: Path) -> MCPIntegrationConfig:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls.model_validate(payload)

    @classmethod
    def sandbox(cls, *, command: tuple[str, ...]) -> MCPIntegrationConfig:
        tools = frozenset(
            {
                "procureops.catalog_lookup",
                "procureops.supplier_lookup",
                "procureops.logistics_quote",
            }
        )
        return cls(
            servers={
                "procureops_sandbox": MCPServerConfig(
                    name="procureops_sandbox",
                    command=command,
                    allowed_tools=tools,
                )
            },
            bindings={
                "catalog_lookup": MCPToolBinding(
                    server="procureops_sandbox", tool="procureops.catalog_lookup"
                ),
                "supplier_lookup": MCPToolBinding(
                    server="procureops_sandbox", tool="procureops.supplier_lookup"
                ),
                "logistics_quote": MCPToolBinding(
                    server="procureops_sandbox", tool="procureops.logistics_quote"
                ),
            },
        )


class MCPTransport(Protocol):
    def call_tool(
        self,
        *,
        server: MCPServerConfig,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]: ...


class StdioMCPTransport:
    """One-shot stdio MCP transport with a full initialize/list/call exchange."""

    protocol_version = "2025-03-26"

    def call_tool(
        self,
        *,
        server: MCPServerConfig,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if tool_name not in server.allowed_tools:
            raise PermanentToolError("MCP tool is outside the server-owned allowlist")
        requests = (
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": self.protocol_version,
                    "capabilities": {},
                    "clientInfo": {"name": "procureops", "version": "0.6.0"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            },
        )
        stdin = "".join(
            json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n"
            for item in requests
        )
        try:
            completed = subprocess.run(
                list(server.command),
                cwd=server.cwd,
                input=stdin,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=server.timeout_seconds,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TransientToolError(f"MCP server {server.name} timed out") from exc
        except OSError as exc:
            raise TransientToolError(f"MCP server {server.name} could not start") from exc
        if completed.returncode != 0:
            raise TransientToolError(f"MCP server {server.name} exited unexpectedly")
        responses: dict[int, dict[str, Any]] = {}
        try:
            for line in completed.stdout.splitlines():
                message = json.loads(line)
                if isinstance(message, dict) and isinstance(message.get("id"), int):
                    responses[message["id"]] = message
        except (TypeError, ValueError) as exc:
            raise PermanentToolError(f"MCP server {server.name} returned invalid JSON-RPC") from exc
        initialize = self._result(responses.get(1), server.name, "initialize")
        if initialize.get("protocolVersion") != self.protocol_version:
            raise PermanentToolError(f"MCP server {server.name} protocol version mismatch")
        tools_result = self._result(responses.get(2), server.name, "tools/list")
        advertised = {
            item.get("name") for item in tools_result.get("tools", []) if isinstance(item, dict)
        }
        if tool_name not in advertised:
            raise PermanentToolError(f"MCP server {server.name} did not advertise {tool_name}")
        call_result = self._result(responses.get(3), server.name, "tools/call")
        if call_result.get("isError") is True:
            raise PermanentToolError(f"MCP tool {tool_name} returned an error")
        structured = call_result.get("structuredContent")
        if isinstance(structured, dict):
            return structured
        content = call_result.get("content")
        if isinstance(content, list) and content and isinstance(content[0], dict):
            try:
                parsed = json.loads(str(content[0].get("text", "")))
            except ValueError as exc:
                raise PermanentToolError("MCP tool returned invalid text content") from exc
            if isinstance(parsed, dict):
                return parsed
        raise PermanentToolError("MCP tool response is missing structured content")

    @staticmethod
    def _result(
        response: dict[str, Any] | None, server_name: str, operation: str
    ) -> dict[str, Any]:
        if response is None or response.get("jsonrpc") != "2.0":
            raise PermanentToolError(f"MCP server {server_name} omitted {operation} response")
        if "error" in response:
            raise PermanentToolError(f"MCP server {server_name} rejected {operation}")
        result = response.get("result")
        if not isinstance(result, dict):
            raise PermanentToolError(f"MCP server {server_name} returned invalid {operation}")
        return result


class MCPReadOnlyEnterpriseIntegrationSuite(EnterpriseIntegrationSuite):
    profile = "mcp_readonly"

    def __init__(
        self,
        *,
        repository: ProcureOpsRepository,
        pack: TenantPack,
        config: MCPIntegrationConfig,
        transport: MCPTransport,
        profile: str = "mcp_readonly",
    ) -> None:
        super().__init__(repository=repository, pack=pack)
        if profile not in {"mcp_sandbox", "mcp_readonly"}:
            raise ValueError("invalid MCP integration profile")
        self.profile = profile
        self.config = config
        self.transport = transport

    def catalog_lookup(
        self, *, tenant_id: str, query: str, part_number: str | None
    ) -> list[dict[str, Any]]:
        return self._items(
            logical_name="catalog_lookup",
            tenant_id=tenant_id,
            arguments={"query": query, "part_number": part_number},
            adapter=PRODUCT_LIST,
            label="catalog",
        )

    def supplier_lookup(
        self, *, tenant_id: str, product_id: str, quantity: Decimal
    ) -> list[dict[str, Any]]:
        return self._items(
            logical_name="supplier_lookup",
            tenant_id=tenant_id,
            arguments={"product_id": product_id, "quantity": str(quantity)},
            adapter=SUPPLIER_LIST,
            label="supplier options",
        )

    def logistics_quote(
        self, *, tenant_id: str, product_id: str, supplier_ids: tuple[str, ...]
    ) -> list[dict[str, Any]]:
        return self._items(
            logical_name="logistics_quote",
            tenant_id=tenant_id,
            arguments={"product_id": product_id, "supplier_ids": list(supplier_ids)},
            adapter=LOGISTICS_LIST,
            label="logistics quotes",
        )

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
        del tenant_id, task_id, idempotency_key, payload, total_amount, currency
        del approval_subject_hash
        raise PermanentToolError("purchase_order_draft is not exposed through MCP")

    def _items(
        self,
        *,
        logical_name: str,
        tenant_id: str,
        arguments: dict[str, Any],
        adapter: TypeAdapter,
        label: str,
    ) -> list[dict[str, Any]]:
        if not tenant_id:
            raise PermanentToolError("tenant_id is required for MCP calls")
        binding = self.config.bindings[logical_name]
        server = self.config.servers[binding.server]
        response = self.transport.call_tool(
            server=server,
            tool_name=binding.tool,
            arguments={"tenant_id": tenant_id, **arguments},
        )
        if response.get("tenant_id") != tenant_id:
            raise PermanentToolError(f"MCP {label} response tenant mismatch")
        try:
            items = adapter.validate_python(response.get("items"))
        except ValidationError as exc:
            raise PermanentToolError(f"invalid MCP {label} response schema") from exc
        return [
            item.model_copy(
                update={
                    "source_system": f"mcp:{server.name}",
                    "source_locator": f"mcp:{server.name}:{binding.tool}",
                }
            ).model_dump(mode="json")
            for item in items
        ]
