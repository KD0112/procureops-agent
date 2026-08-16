"""Smoke-test the read-only repository MCP profile through the real stdio transport."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from procureops.integrations.mcp import MCPServerConfig, StdioMCPTransport  # noqa: E402


def main() -> None:
    server = MCPServerConfig(
        name="repo_readonly",
        command=(sys.executable, str(PROJECT_ROOT / "scripts" / "run_repo_mcp_server.py")),
        allowed_tools=frozenset({"repo_tree", "repo_read", "repo_search", "repo_diff"}),
        cwd=PROJECT_ROOT,
        timeout_seconds=15.0,
    )
    transport = StdioMCPTransport()
    tree = transport.call_tool(server=server, tool_name="repo_tree", arguments={})
    readme = transport.call_tool(
        server=server,
        tool_name="repo_read",
        arguments={"path": "README.md"},
    )
    search = transport.call_tool(
        server=server,
        tool_name="repo_search",
        arguments={"query": "ToolGateway", "max_results": 5},
    )
    if "README.md" not in tree.get("items", []):
        raise SystemExit("repo_tree did not return README.md")
    if "ProcureOps Agent" not in readme.get("content", ""):
        raise SystemExit("repo_read returned unexpected content")
    if not search.get("items"):
        raise SystemExit("repo_search returned no known match")
    print("repo MCP smoke: PASS")


if __name__ == "__main__":
    main()
