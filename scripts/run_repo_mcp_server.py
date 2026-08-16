"""Read-only MCP server exposing a bounded repository inspection profile."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from procureops.codeops.policy import RepoPolicy  # noqa: E402
from procureops.codeops.tools import RepoInspector  # noqa: E402
from procureops.codeops.workspace import RepoWorkspace  # noqa: E402

PROTOCOL_VERSION = "2025-03-26"
SERVER_NAME = "procureops-repo-readonly"
TOOLS = ("repo_tree", "repo_read", "repo_search", "repo_diff")


def _tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "repo_tree",
            "description": "List non-sensitive files in the repository",
            "inputSchema": {"type": "object", "properties": {}},
            "annotations": {"readOnlyHint": True, "destructiveHint": False},
        },
        {
            "name": "repo_read",
            "description": "Read one UTF-8 text file within the repository",
            "inputSchema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            "annotations": {"readOnlyHint": True, "destructiveHint": False},
        },
        {
            "name": "repo_search",
            "description": "Search UTF-8 text files within the repository",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "required": ["query"],
            },
            "annotations": {"readOnlyHint": True, "destructiveHint": False},
        },
        {
            "name": "repo_diff",
            "description": "Read the current git diff without changing the repository",
            "inputSchema": {"type": "object", "properties": {}},
            "annotations": {"readOnlyHint": True, "destructiveHint": False},
        },
    ]


def _repo_root() -> Path:
    root = Path(os.getenv("PROCUREOPS_REPO_ROOT", str(PROJECT_ROOT))).resolve()
    if not root.is_dir():
        raise ValueError("PROCUREOPS_REPO_ROOT must be an existing directory")
    return root


def _git_diff(root: Path, policy: RepoPolicy) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "diff", "--no-ext-diff", "--unified=3"],
            capture_output=True,
            timeout=policy.command_timeout_seconds,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"diff": "", "available": False}
    return {
        "diff": completed.stdout.decode("utf-8", errors="replace")[-100_000:],
        "available": completed.returncode == 0,
    }


def _call(
    root: Path,
    inspector: RepoInspector,
    policy: RepoPolicy,
    name: str,
    args: dict[str, Any],
):
    if name == "repo_tree":
        return {"items": inspector.tree(max_entries=int(args.get("max_entries", 200)))}
    if name == "repo_read":
        path = str(args.get("path", ""))
        return {"path": path, "content": policy.read_text(root, path)}
    if name == "repo_search":
        return {
            "items": inspector.search(
                query=str(args.get("query", "")),
                max_results=int(args.get("max_results", 50)),
            )
        }
    if name == "repo_diff":
        return _git_diff(root, policy)
    raise ValueError("tool is not allowlisted")


def _response(request_id: Any, result: dict[str, Any]) -> None:
    sys.stdout.write(
        json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}) + "\n"
    )
    sys.stdout.flush()


def _error(request_id: Any, message: str) -> None:
    sys.stdout.write(
        json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": message}}
        )
        + "\n"
    )
    sys.stdout.flush()


def serve(root: Path) -> None:
    policy = RepoPolicy()
    workspace = RepoWorkspace(
        workspace_id="mcp-readonly",
        source_root=root,
        path=root,
        baseline={},
    )
    inspector = RepoInspector(workspace=workspace, policy=policy)
    for line in sys.stdin:
        request: dict[str, Any] = {}
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
                        "serverInfo": {"name": SERVER_NAME, "version": "0.1.0"},
                    },
                )
            elif method == "tools/list":
                _response(request_id, {"tools": _tool_specs()})
            elif method == "tools/call":
                params = request.get("params", {})
                payload = _call(
                    _repo_root(),
                    inspector,
                    policy,
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
            _error(request.get("id"), str(exc))


def main() -> None:
    serve(_repo_root())


if __name__ == "__main__":
    main()
