from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from procureops.intake.prompts import DEFAULT_TEXT_EXTRACTION_PROMPT, PROMPT_SCOPE_TEXT_INTAKE
from procureops.storage import SQLiteDatabase

COMPLIANCE_ROLE = "compliance_approver"
REQUIRED_PROMPT_MARKERS = (
    '"lines"',
    '"description"',
    '"quantity"',
    '"part_number"',
    "source_text",
    "untrusted",
    "preserve",
)
SECRET_PATTERN = re.compile(
    r"(?:api[_-]?key|password|token|credential)\s*[:=]\s*[\w./+\-=]{8,}",
    re.IGNORECASE,
)


class FeedbackRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    feedback_id: str
    user_id: str
    task_id: str | None
    feedback_type: str
    summary: str
    correction: dict[str, Any]
    source_hash: str
    status: str
    created_at: datetime
    resolved_at: datetime | None = None


class PromptCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    candidate_id: str
    scope: str
    base_version: str
    candidate_version: str
    prompt_text: str
    prompt_hash: str
    status: str
    evaluation_mode: str | None = None
    evaluation_report: dict[str, Any] | None = None
    evaluation_passed: bool | None = None
    safety_passed: bool | None = None
    proposed_by: str
    approved_by: str | None = None
    created_at: datetime
    evaluated_at: datetime | None = None
    approved_at: datetime | None = None
    rejected_at: datetime | None = None
    released_at: datetime | None = None


class PromptRelease(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    release_id: str
    candidate_id: str
    scope: str
    prompt_version: str
    status: str
    previous_release_id: str | None = None
    released_by: str
    released_at: datetime
    rolled_back_by: str | None = None
    rolled_back_at: datetime | None = None


class ActivePrompt(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    scope: str
    release_id: str
    prompt_version: str
    prompt_text: str
    prompt_hash: str


class EvolutionService:
    """Governed feedback-to-release store; it never self-releases a candidate."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def bootstrap_baseline(self, *, tenant_id: str) -> ActivePrompt:
        try:
            return self.active_prompt(tenant_id=tenant_id)
        except KeyError:
            pass
        now = datetime.now(UTC).isoformat()
        candidate_id = f"baseline-{PROMPT_SCOPE_TEXT_INTAKE}"
        release_id = f"baseline-release-{PROMPT_SCOPE_TEXT_INTAKE}"
        prompt_hash = _hash_text(DEFAULT_TEXT_EXTRACTION_PROMPT)
        report = {
            "suite": "prompt_contract_v1",
            "passed": True,
            "safety_passed": True,
            "checks": {marker: True for marker in REQUIRED_PROMPT_MARKERS},
            "baseline": True,
        }
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO prompt_candidates(
                    tenant_id, candidate_id, scope, base_version,
                    candidate_version, prompt_text, prompt_hash, status,
                    evaluation_mode, evaluation_report_json,
                    evaluation_passed, safety_passed, proposed_by,
                    approved_by, created_at, evaluated_at, approved_at, released_at
                ) VALUES (?, ?, ?, 'none', '1.0.0', ?, ?, 'released',
                          'baseline_contract', ?, 1, 1, 'system-bootstrap',
                          'system-bootstrap', ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    candidate_id,
                    PROMPT_SCOPE_TEXT_INTAKE,
                    DEFAULT_TEXT_EXTRACTION_PROMPT,
                    prompt_hash,
                    json.dumps(report, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO prompt_releases(
                    tenant_id, release_id, candidate_id, scope, prompt_version,
                    status, released_by, released_at
                ) VALUES (?, ?, ?, ?, '1.0.0', 'active', 'system-bootstrap', ?)
                """,
                (tenant_id, release_id, candidate_id, PROMPT_SCOPE_TEXT_INTAKE, now),
            )
        return self.active_prompt(tenant_id=tenant_id)

    def create_feedback(
        self,
        *,
        tenant_id: str,
        user_id: str,
        feedback_type: str,
        summary: str,
        correction: dict[str, Any],
        task_id: str | None,
    ) -> FeedbackRecord:
        if feedback_type not in {"correction", "preference", "failure", "rating"}:
            raise ValueError("unsupported feedback type")
        clean_summary = summary.strip()
        if not clean_summary or len(clean_summary) > 2_000:
            raise ValueError("feedback summary must contain 1 to 2000 characters")
        created_at = datetime.now(UTC).isoformat()
        feedback_id = str(uuid4())
        source_hash = _hash_json(
            {
                "user_id": user_id,
                "task_id": task_id,
                "feedback_type": feedback_type,
                "summary": clean_summary,
                "correction": correction,
            }
        )
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO user_feedback(
                    tenant_id, feedback_id, user_id, task_id, feedback_type,
                    summary, correction_json, source_hash, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
                """,
                (
                    tenant_id,
                    feedback_id,
                    user_id,
                    task_id,
                    feedback_type,
                    clean_summary,
                    json.dumps(correction, ensure_ascii=False, sort_keys=True),
                    source_hash,
                    created_at,
                ),
            )
        return self.get_feedback(tenant_id=tenant_id, feedback_id=feedback_id)

    def list_feedback(
        self,
        *,
        tenant_id: str,
        status: str | None = None,
    ) -> tuple[FeedbackRecord, ...]:
        query = "SELECT feedback_id FROM user_feedback WHERE tenant_id=?"
        parameters: list[Any] = [tenant_id]
        if status is not None:
            query += " AND status=?"
            parameters.append(status)
        query += " ORDER BY created_at DESC"
        with self.database.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(
            self.get_feedback(tenant_id=tenant_id, feedback_id=row["feedback_id"])
            for row in rows
        )

    def get_feedback(self, *, tenant_id: str, feedback_id: str) -> FeedbackRecord:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM user_feedback WHERE tenant_id=? AND feedback_id=?",
                (tenant_id, feedback_id),
            ).fetchone()
        if row is None:
            raise KeyError("feedback not found in tenant scope")
        return FeedbackRecord(
            tenant_id=row["tenant_id"],
            feedback_id=row["feedback_id"],
            user_id=row["user_id"],
            task_id=row["task_id"],
            feedback_type=row["feedback_type"],
            summary=row["summary"],
            correction=json.loads(row["correction_json"]),
            source_hash=row["source_hash"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            resolved_at=datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None,
        )

    def propose_candidate(
        self,
        *,
        tenant_id: str,
        scope: str,
        candidate_version: str,
        prompt_text: str,
        proposed_by: str,
        feedback_ids: tuple[str, ...],
    ) -> PromptCandidate:
        if scope != PROMPT_SCOPE_TEXT_INTAKE:
            raise ValueError("unsupported prompt scope")
        if not feedback_ids:
            raise ValueError("a prompt candidate must cite at least one feedback record")
        active = self.bootstrap_baseline(tenant_id=tenant_id)
        candidate_id = str(uuid4())
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            for feedback_id in feedback_ids:
                row = connection.execute(
                    """
                    SELECT status FROM user_feedback
                    WHERE tenant_id=? AND feedback_id=?
                    """,
                    (tenant_id, feedback_id),
                ).fetchone()
                if row is None or row["status"] == "resolved":
                    raise ValueError("candidate feedback is missing or already resolved")
            connection.execute(
                """
                INSERT INTO prompt_candidates(
                    tenant_id, candidate_id, scope, base_version,
                    candidate_version, prompt_text, prompt_hash, status,
                    proposed_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?)
                """,
                (
                    tenant_id,
                    candidate_id,
                    scope,
                    active.prompt_version,
                    candidate_version.strip(),
                    prompt_text.strip(),
                    _hash_text(prompt_text.strip()),
                    proposed_by,
                    now,
                ),
            )
            connection.executemany(
                """
                INSERT INTO candidate_feedback(tenant_id, candidate_id, feedback_id)
                VALUES (?, ?, ?)
                """,
                [(tenant_id, candidate_id, feedback_id) for feedback_id in feedback_ids],
            )
            connection.executemany(
                """
                UPDATE user_feedback SET status='linked'
                WHERE tenant_id=? AND feedback_id=? AND status='open'
                """,
                [(tenant_id, feedback_id) for feedback_id in feedback_ids],
            )
        return self.get_candidate(tenant_id=tenant_id, candidate_id=candidate_id)

    def evaluate_contract(
        self,
        *,
        tenant_id: str,
        candidate_id: str,
        evaluated_by: str,
    ) -> PromptCandidate:
        candidate = self.get_candidate(tenant_id=tenant_id, candidate_id=candidate_id)
        if candidate.status != "proposed":
            raise ValueError("only proposed candidates can be evaluated")
        normalized_prompt = candidate.prompt_text.casefold()
        checks = {
            marker: marker.casefold() in normalized_prompt
            for marker in REQUIRED_PROMPT_MARKERS
        }
        length_valid = 200 <= len(candidate.prompt_text) <= 8_000
        no_embedded_secret = SECRET_PATTERN.search(candidate.prompt_text) is None
        safety_passed = checks["untrusted"] and no_embedded_secret
        passed = all(checks.values()) and length_valid and safety_passed
        report = {
            "suite": "prompt_contract_v1",
            "evaluated_by": evaluated_by,
            "checks": checks,
            "length_valid": length_valid,
            "no_embedded_secret": no_embedded_secret,
            "passed": passed,
            "safety_passed": safety_passed,
        }
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE prompt_candidates
                SET status='evaluated', evaluation_mode='contract_fake',
                    evaluation_report_json=?, evaluation_passed=?, safety_passed=?,
                    evaluated_at=?
                WHERE tenant_id=? AND candidate_id=? AND status='proposed'
                """,
                (
                    json.dumps(report, ensure_ascii=False, sort_keys=True),
                    int(passed),
                    int(safety_passed),
                    now,
                    tenant_id,
                    candidate_id,
                ),
            )
        return self.get_candidate(tenant_id=tenant_id, candidate_id=candidate_id)

    def approve_candidate(
        self,
        *,
        tenant_id: str,
        candidate_id: str,
        approved_by: str,
        actor_roles: frozenset[str],
    ) -> PromptCandidate:
        _require_compliance(actor_roles)
        candidate = self.get_candidate(tenant_id=tenant_id, candidate_id=candidate_id)
        if candidate.status != "evaluated" or not (
            candidate.evaluation_passed and candidate.safety_passed
        ):
            raise ValueError("candidate has not passed evaluation and safety gates")
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE prompt_candidates
                SET status='approved', approved_by=?, approved_at=?
                WHERE tenant_id=? AND candidate_id=? AND status='evaluated'
                """,
                (approved_by, now, tenant_id, candidate_id),
            )
        return self.get_candidate(tenant_id=tenant_id, candidate_id=candidate_id)

    def release_candidate(
        self,
        *,
        tenant_id: str,
        candidate_id: str,
        released_by: str,
        actor_roles: frozenset[str],
    ) -> PromptRelease:
        _require_compliance(actor_roles)
        candidate = self.get_candidate(tenant_id=tenant_id, candidate_id=candidate_id)
        if candidate.status != "approved":
            raise ValueError("only approved candidates can be released")
        current = self.active_prompt(tenant_id=tenant_id, scope=candidate.scope)
        release_id = str(uuid4())
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE prompt_releases SET status='superseded'
                WHERE tenant_id=? AND scope=? AND status='active'
                """,
                (tenant_id, candidate.scope),
            )
            connection.execute(
                """
                INSERT INTO prompt_releases(
                    tenant_id, release_id, candidate_id, scope, prompt_version,
                    status, previous_release_id, released_by, released_at
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    tenant_id,
                    release_id,
                    candidate_id,
                    candidate.scope,
                    candidate.candidate_version,
                    current.release_id,
                    released_by,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE prompt_candidates SET status='released', released_at=?
                WHERE tenant_id=? AND candidate_id=? AND status='approved'
                """,
                (now, tenant_id, candidate_id),
            )
            connection.execute(
                """
                UPDATE user_feedback SET status='resolved', resolved_at=?
                WHERE tenant_id=? AND feedback_id IN (
                    SELECT feedback_id FROM candidate_feedback
                    WHERE tenant_id=? AND candidate_id=?
                )
                """,
                (now, tenant_id, tenant_id, candidate_id),
            )
        return self.get_release(tenant_id=tenant_id, release_id=release_id)

    def rollback_release(
        self,
        *,
        tenant_id: str,
        release_id: str,
        rolled_back_by: str,
        actor_roles: frozenset[str],
    ) -> PromptRelease:
        _require_compliance(actor_roles)
        release = self.get_release(tenant_id=tenant_id, release_id=release_id)
        if release.status != "active" or release.previous_release_id is None:
            raise ValueError("only an active non-baseline release can be rolled back")
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE prompt_releases
                SET status='rolled_back', rolled_back_by=?, rolled_back_at=?
                WHERE tenant_id=? AND release_id=? AND status='active'
                """,
                (rolled_back_by, now, tenant_id, release_id),
            )
            connection.execute(
                """
                UPDATE prompt_releases SET status='active'
                WHERE tenant_id=? AND release_id=? AND status='superseded'
                """,
                (tenant_id, release.previous_release_id),
            )
            connection.execute(
                """
                UPDATE prompt_candidates SET status='rolled_back'
                WHERE tenant_id=? AND candidate_id=? AND status='released'
                """,
                (tenant_id, release.candidate_id),
            )
        return self.get_release(
            tenant_id=tenant_id,
            release_id=release.previous_release_id,
        )

    def active_prompt(
        self,
        *,
        tenant_id: str,
        scope: str = PROMPT_SCOPE_TEXT_INTAKE,
    ) -> ActivePrompt:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT r.tenant_id, r.scope, r.release_id, r.prompt_version,
                       c.prompt_text, c.prompt_hash
                FROM prompt_releases AS r
                JOIN prompt_candidates AS c
                  ON c.tenant_id=r.tenant_id AND c.candidate_id=r.candidate_id
                WHERE r.tenant_id=? AND r.scope=? AND r.status='active'
                """,
                (tenant_id, scope),
            ).fetchone()
        if row is None:
            raise KeyError("active prompt release not found")
        return ActivePrompt(**dict(row))

    def list_candidates(self, *, tenant_id: str) -> tuple[PromptCandidate, ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT candidate_id FROM prompt_candidates
                WHERE tenant_id=? ORDER BY created_at DESC
                """,
                (tenant_id,),
            ).fetchall()
        return tuple(
            self.get_candidate(tenant_id=tenant_id, candidate_id=row["candidate_id"])
            for row in rows
        )

    def get_candidate(self, *, tenant_id: str, candidate_id: str) -> PromptCandidate:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM prompt_candidates WHERE tenant_id=? AND candidate_id=?",
                (tenant_id, candidate_id),
            ).fetchone()
        if row is None:
            raise KeyError("prompt candidate not found in tenant scope")
        return PromptCandidate(
            tenant_id=row["tenant_id"],
            candidate_id=row["candidate_id"],
            scope=row["scope"],
            base_version=row["base_version"],
            candidate_version=row["candidate_version"],
            prompt_text=row["prompt_text"],
            prompt_hash=row["prompt_hash"],
            status=row["status"],
            evaluation_mode=row["evaluation_mode"],
            evaluation_report=(
                json.loads(row["evaluation_report_json"])
                if row["evaluation_report_json"]
                else None
            ),
            evaluation_passed=(
                bool(row["evaluation_passed"])
                if row["evaluation_passed"] is not None
                else None
            ),
            safety_passed=(
                bool(row["safety_passed"]) if row["safety_passed"] is not None else None
            ),
            proposed_by=row["proposed_by"],
            approved_by=row["approved_by"],
            created_at=datetime.fromisoformat(row["created_at"]),
            evaluated_at=_optional_datetime(row["evaluated_at"]),
            approved_at=_optional_datetime(row["approved_at"]),
            rejected_at=_optional_datetime(row["rejected_at"]),
            released_at=_optional_datetime(row["released_at"]),
        )

    def get_release(self, *, tenant_id: str, release_id: str) -> PromptRelease:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM prompt_releases WHERE tenant_id=? AND release_id=?",
                (tenant_id, release_id),
            ).fetchone()
        if row is None:
            raise KeyError("prompt release not found in tenant scope")
        values = dict(row)
        values["released_at"] = datetime.fromisoformat(row["released_at"])
        values["rolled_back_at"] = _optional_datetime(row["rolled_back_at"])
        return PromptRelease(**values)


def _require_compliance(actor_roles: frozenset[str]) -> None:
    if COMPLIANCE_ROLE not in actor_roles:
        raise PermissionError("compliance_approver role required")


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _hash_text(encoded)


def _optional_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
