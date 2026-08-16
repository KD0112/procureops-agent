"""Run a read-only stdio MCP server backed by the local enterprise projection."""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from procureops.storage import ProcureOpsRepository, SQLiteDatabase  # noqa: E402

PROTOCOL_VERSION = "2025-03-26"
TOOLS = (
    "procureops.catalog_lookup",
    "procureops.supplier_lookup",
    "procureops.logistics_quote",
)


def _tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": "Read-only tenant-scoped ProcureOps enterprise projection",
            "inputSchema": {
                "type": "object",
                "properties": {"tenant_id": {"type": "string"}},
                "required": ["tenant_id"],
                "additionalProperties": True,
            },
            "annotations": {"readOnlyHint": True, "destructiveHint": False},
        }
        for name in TOOLS
    ]


def _call(repository: ProcureOpsRepository, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    tenant_id = str(arguments.get("tenant_id", ""))
    if not tenant_id:
        raise ValueError("tenant_id is required")
    if name == "procureops.catalog_lookup":
        items = repository.search_products(
            tenant_id=tenant_id,
            query=str(arguments.get("query", "")),
            part_number=(str(arguments["part_number"]) if arguments.get("part_number") else None),
        )
    elif name == "procureops.supplier_lookup":
        items = repository.supplier_options(
            tenant_id=tenant_id,
            product_id=str(arguments.get("product_id", "")),
            required_quantity=Decimal(str(arguments.get("quantity", "0"))),
        )
    elif name == "procureops.logistics_quote":
        supplier_ids = arguments.get("supplier_ids", [])
        if not isinstance(supplier_ids, list):
            raise ValueError("supplier_ids must be a list")
        items = repository.logistics_quotes(
            tenant_id=tenant_id,
            product_id=str(arguments.get("product_id", "")),
            supplier_ids=tuple(str(item) for item in supplier_ids),
        )
    else:
        raise ValueError("tool is not allowlisted")
    return {"tenant_id": tenant_id, "items": [item.model_dump(mode="json") for item in items]}


def _response(request_id: Any, result: dict[str, Any]) -> None:
    sys.stdout.write(
        json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result})
        + "\n"
    )
    sys.stdout.flush()


def _error(request_id: Any, message: str) -> None:
    sys.stdout.write(
        json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": message}},
        )
        + "\n"
    )
    sys.stdout.flush()


def serve(repository: ProcureOpsRepository) -> None:
    for line in sys.stdin:
        try:
            request = json.loads(line)
            method = request.get("method")
            request_id = request.get("id")
            if method == "notifications/initialized":
                continue
            if method == "initialize":
                _response(
                    request_id,
                    {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "procureops-sandbox", "version": "0.6.0"},
                    },
                )
            elif method == "tools/list":
                _response(request_id, {"tools": _tool_specs()})
            elif method == "tools/call":
                params = request.get("params", {})
                payload = _call(
                    repository,
                    str(params.get("name", "")),
                    dict(params.get("arguments", {})),
                )
                _response(
                    request_id,
                    {
                        "content": [{"type": "text", "text": json.dumps(payload)}],
                        "structuredContent": payload,
                        "isError": False,
                    },
                )
            else:
                _error(request_id, "unsupported MCP method")
        except Exception as exc:
            _error(request.get("id") if isinstance(request, dict) else None, str(exc))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    repository = ProcureOpsRepository(SQLiteDatabase(args.database.resolve()))
    serve(repository)


if __name__ == "__main__":
    main()
