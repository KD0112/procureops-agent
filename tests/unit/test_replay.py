import json
from pathlib import Path

from procureops.evals.replay import ReplayStore
from procureops.harness.audit import AuditEvent


def test_replay_bundle_detects_tampering(tmp_path: Path, run_context) -> None:
    store = ReplayStore(tmp_path)
    path = store.save(
        context=run_context,
        outcome="completed",
        workflow_events=(),
        audit_events=(AuditEvent.from_context(run_context, "test.event"),),
    )

    assert store.verify(path)
    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["bundle"]["outcome"] = "tampered"
    path.write_text(json.dumps(envelope), encoding="utf-8")
    assert not store.verify(path)
