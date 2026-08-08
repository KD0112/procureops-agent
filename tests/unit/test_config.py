from pathlib import Path

from procureops.config import load_environment, public_environment_snapshot


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
