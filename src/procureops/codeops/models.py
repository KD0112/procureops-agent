from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CodeTaskRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    description: str = Field(min_length=1, max_length=4_000)
    requested_files: tuple[str, ...] = Field(default=(), max_length=20)
    ci_output: str = Field(default="", max_length=20_000)
    test_command: str = Field(default="python -m pytest -q", max_length=300)
    commit_requested: bool = False


class RepoPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    rationale: str = Field(min_length=1, max_length=4_000)
    files_to_read: tuple[str, ...] = Field(default=(), max_length=40)
    proposed_writes: dict[str, str] = Field(default_factory=dict)
    expected_sha256: dict[str, str] = Field(default_factory=dict)
    test_command: str = Field(default="python -m pytest -q", max_length=300)
    commit_requested: bool = False


class RepoPilotResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str
    status: str = Field(pattern="^(planned|passed|failed|needs_approval|blocked)$")
    workspace_id: str
    workspace_path: str
    description: str
    diagnosis: dict[str, Any] = Field(default_factory=dict)
    workflow: tuple[str, ...] = ()
    files_inspected: tuple[str, ...] = ()
    files_changed: tuple[str, ...] = ()
    diff: str = ""
    diff_sha256: str | None = None
    test_result: dict[str, Any] = Field(default_factory=dict)
    blocked_reason: str | None = None
    audit_event_count: int = Field(default=0, ge=0)
