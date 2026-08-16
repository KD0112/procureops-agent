from pathlib import Path

import pytest

from procureops.config import (
    api_port_from_environment,
    load_environment,
    public_environment_snapshot,
)


def test_harn_001_referenced_env_loads_without_copying_secrets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    legacy = tmp_path / "legacy.env"
    legacy.write_text("DEEPSEEK_API_KEY=test-secret\nAGENT_TEXT_PROVIDER=fake\n")
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text(
        f"PROCUREOPS_ENV_FILE={legacy}\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("PROCUREOPS_ENV_FILE", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_TEXT_PROVIDER", raising=False)

    loaded = load_environment(project)

    assert loaded["DEEPSEEK_API_KEY"] == "test-secret"
    assert public_environment_snapshot()["DEEPSEEK_API_KEY"] == "***redacted***"
    assert public_environment_snapshot()["AGENT_TEXT_PROVIDER"] == "fake"


def test_harn_001_os_environment_has_precedence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_TEXT_PROVIDER", "from-os")
    (tmp_path / ".env").write_text(
        "AGENT_TEXT_PROVIDER=from-file\n",
        encoding="utf-8",
    )

    load_environment(tmp_path)

    assert public_environment_snapshot()["AGENT_TEXT_PROVIDER"] == "from-os"


def test_api_port_defaults_to_conflict_free_8030(monkeypatch) -> None:
    monkeypatch.delenv("PROCUREOPS_API_PORT", raising=False)

    assert api_port_from_environment() == 8030


def test_api_port_can_be_overridden_and_rejects_invalid_values(monkeypatch) -> None:
    monkeypatch.setenv("PROCUREOPS_API_PORT", "9123")
    assert api_port_from_environment() == 9123

    monkeypatch.setenv("PROCUREOPS_API_PORT", "70000")
    with pytest.raises(ValueError, match="between 1 and 65535"):
        api_port_from_environment()
