from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from procureops.domain.models import RunContext, canonical_hash
from procureops.harness.audit import AuditEvent


class ReplayBundle(BaseModel):
    model_config = ConfigDict(frozen=True)

    bundle_id: str
    created_at: datetime
    context: dict[str, Any]
    outcome: str
    workflow_events: tuple[dict[str, Any], ...]
    audit_events: tuple[dict[str, Any], ...]


class ReplayStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        *,
        context: RunContext,
        outcome: str,
        workflow_events: tuple[dict[str, Any], ...],
        audit_events: tuple[AuditEvent, ...],
    ) -> Path:
        bundle = ReplayBundle(
            bundle_id=str(uuid4()),
            created_at=datetime.now(UTC),
            context=context.model_dump(mode="json"),
            outcome=outcome,
            workflow_events=workflow_events,
            audit_events=tuple(event.model_dump(mode="json") for event in audit_events),
        )
        payload = bundle.model_dump(mode="json")
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        envelope = {
            "bundle": payload,
            "bundle_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        }
        path = self.root / f"{context.run_id}.json"
        path.write_text(
            json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path

    @staticmethod
    def verify(path: Path) -> bool:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        bundle = envelope["bundle"]
        canonical = json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != envelope["bundle_sha256"]:
            return False
        for event in bundle["workflow_events"]:
            payload = json.loads(event["payload_json"])
            if canonical_hash(payload) != event["payload_hash"]:
                return False
        return True
