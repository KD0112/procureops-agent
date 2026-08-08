from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def load_environment(project_root: Path = PROJECT_ROOT) -> dict[str, str]:
    """Load local settings, then an optional private legacy env without overwriting OS vars."""
    loaded: dict[str, str] = {}
    local_values = _parse_env_file(project_root / ".env")
    for key, value in local_values.items():
        if key not in os.environ:
            os.environ[key] = value
            loaded[key] = value

    referenced = os.environ.get("PROCUREOPS_ENV_FILE")
    if referenced:
        referenced_path = Path(referenced).expanduser()
        for key, value in _parse_env_file(referenced_path).items():
            if key not in os.environ:
                os.environ[key] = value
                loaded[key] = value
    return loaded


def public_environment_snapshot() -> dict[str, str]:
    """Return non-secret diagnostics suitable for logs and support bundles."""
    secret_markers = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
    prefixes = (
        "PROCUREOPS_",
        "AGENT_",
        "DEEPSEEK_",
        "ZHIPU_",
        "QWEN_",
        "DASHSCOPE_",
    )
    snapshot: dict[str, str] = {}
    for key, value in os.environ.items():
        if not key.startswith(prefixes):
            continue
        snapshot[key] = (
            "***redacted***" if any(marker in key for marker in secret_markers) else value
        )
    return snapshot
