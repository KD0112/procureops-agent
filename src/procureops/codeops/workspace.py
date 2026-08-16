from __future__ import annotations

import difflib
import shutil
from dataclasses import dataclass
from pathlib import Path

from procureops.codeops.policy import RepoPolicy


@dataclass(frozen=True, slots=True)
class RepoWorkspace:
    workspace_id: str
    source_root: Path
    path: Path
    baseline: dict[str, str]


class WorkspaceManager:
    """Create a disposable copy so the coding Agent never edits the source tree."""

    def __init__(
        self,
        *,
        source_root: Path,
        workspace_root: Path,
        policy: RepoPolicy | None = None,
    ):
        self.source_root = source_root.resolve()
        self.workspace_root = workspace_root.resolve()
        self.policy = policy or RepoPolicy()
        if not self.source_root.is_dir():
            raise FileNotFoundError(self.source_root)
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    def create(self, workspace_id: str) -> RepoWorkspace:
        if not workspace_id or any(part in workspace_id for part in ("/", "\\", "..")):
            raise ValueError("workspace_id must be a simple task identifier")
        destination = (self.workspace_root / workspace_id).resolve()
        try:
            destination.relative_to(self.workspace_root)
        except ValueError as exc:
            raise PermissionError("workspace destination escapes workspace root") from exc
        if destination.exists():
            raise FileExistsError(destination)
        shutil.copytree(
            self.source_root,
            destination,
            ignore=shutil.ignore_patterns(
                ".git",
                ".venv",
                ".pytest_cache",
                ".ruff_cache",
                ".deepeval",
                "__pycache__",
                "*.pyc",
                ".coverage",
                ".env",
                ".env.*",
                "var",
                "reports",
                "work",
                "node_modules",
            ),
        )
        return RepoWorkspace(
            workspace_id=workspace_id,
            source_root=self.source_root,
            path=destination,
            baseline=self._snapshot(destination),
        )

    def diff(self, workspace: RepoWorkspace) -> str:
        current = self._snapshot(workspace.path)
        paths = sorted(set(workspace.baseline) | set(current))
        chunks: list[str] = []
        for relative in paths:
            before = workspace.baseline.get(relative, "").splitlines(keepends=True)
            after = current.get(relative, "").splitlines(keepends=True)
            if before == after:
                continue
            chunks.extend(
                difflib.unified_diff(
                    before,
                    after,
                    fromfile=f"a/{relative}",
                    tofile=f"b/{relative}",
                )
            )
        return "".join(chunks)

    def release(self, workspace: RepoWorkspace) -> None:
        target = workspace.path.resolve()
        try:
            target.relative_to(self.workspace_root)
        except ValueError as exc:
            raise PermissionError("refusing to remove a path outside workspace root") from exc
        if target == self.workspace_root or not target.exists():
            raise PermissionError("invalid workspace cleanup target")
        shutil.rmtree(target)

    def _snapshot(self, root: Path) -> dict[str, str]:
        snapshot: dict[str, str] = {}
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            try:
                safe = self.policy.resolve(root, relative)
            except PermissionError:
                continue
            if safe.stat().st_size > self.policy.max_read_bytes:
                continue
            try:
                snapshot[relative] = safe.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
        return snapshot
