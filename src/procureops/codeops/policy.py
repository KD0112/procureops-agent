from __future__ import annotations

import hashlib
import os
import shlex
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class RepoPolicy(BaseModel):
    """Filesystem and command boundary for a repository task."""

    model_config = ConfigDict(frozen=True)

    max_read_bytes: int = Field(default=200_000, ge=1_000, le=2_000_000)
    max_write_bytes: int = Field(default=200_000, ge=1_000, le=2_000_000)
    max_search_results: int = Field(default=100, ge=1, le=1_000)
    command_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    denied_parts: frozenset[str] = frozenset(
        {
            ".git",
            ".env",
            ".venv",
            ".deepeval",
            ".pytest_cache",
            ".ruff_cache",
            ".coverage",
            "__pycache__",
            "reports",
            "var",
            "work",
            "secrets",
            "credentials",
            "id_rsa",
            "id_ed25519",
        }
    )

    def resolve(self, root: Path, relative_path: str, *, write: bool = False) -> Path:
        candidate = Path(relative_path)
        if candidate.is_absolute():
            raise PermissionError("absolute repository paths are not allowed")
        if not relative_path.strip() or any(part in self.denied_parts for part in candidate.parts):
            raise PermissionError("repository path is outside the allowed coding scope")
        root_resolved = root.resolve()
        target = (root_resolved / candidate).resolve()
        try:
            target.relative_to(root_resolved)
        except ValueError as exc:
            raise PermissionError("repository path escapes the workspace") from exc
        if write and target.name.casefold() in {".env", "secrets.json", "credentials.json"}:
            raise PermissionError("sensitive files are never writable")
        return target

    def read_text(self, root: Path, relative_path: str) -> str:
        target = self.resolve(root, relative_path)
        if not target.is_file():
            raise FileNotFoundError(relative_path)
        if target.stat().st_size > self.max_read_bytes:
            raise PermissionError("file exceeds the repository read limit")
        return target.read_text(encoding="utf-8")

    def write_text(
        self,
        root: Path,
        relative_path: str,
        content: str,
        *,
        expected_sha256: str | None = None,
    ) -> str:
        if len(content.encode("utf-8")) > self.max_write_bytes:
            raise PermissionError("file exceeds the repository write limit")
        target = self.resolve(root, relative_path, write=True)
        current_hash = None
        if target.exists():
            current_hash = hashlib.sha256(target.read_bytes()).hexdigest()
            if expected_sha256 != current_hash:
                raise PermissionError("stale or missing expected_sha256 for existing file")
        elif expected_sha256 is not None:
            raise PermissionError("expected_sha256 was supplied for a new file")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
        return hashlib.sha256(target.read_bytes()).hexdigest()

    def command(self, raw_command: str) -> list[str]:
        if not raw_command.strip() or any(
            token in raw_command for token in (";", "&&", "||", "`", "$")
        ):
            raise PermissionError("compound shell commands are not allowed")
        tokens = shlex.split(raw_command, posix=os.name != "nt")
        if not tokens:
            raise PermissionError("empty test command")
        executable = Path(tokens[0]).name.casefold()
        allowed = False
        if executable in {"pytest", "ruff"}:
            allowed = True
        if executable in {"python", "python.exe", Path(sys.executable).name.casefold()}:
            allowed = len(tokens) >= 3 and tokens[1] == "-m" and tokens[2] in {
                "pytest",
                "ruff",
                "compileall",
            }
        if not allowed:
            raise PermissionError("only pytest, ruff and compileall commands are allowed")
        if executable in {"python", "python.exe"}:
            tokens[0] = sys.executable
        return tokens
