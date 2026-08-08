from pathlib import Path

from procureops.evals.dataset import generate_cases
from procureops.evals.runner import EvaluationRunner, compare_reports
from procureops.harness.model_gateway import FakeModel

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def representative_cases():
    cases = generate_cases()
    selected_ids = {
        "NORMAL-001",
        "AMBIGUOUS-001",
        "TOOL-FAILURE-001",
        "TOOL-FAILURE-015",
        "ATTACK-001",
        "ATTACK-015",
        "APPROVAL-001",
        "APPROVAL-002",
        "APPROVAL-003",
    }
    return tuple(case for case in cases if case.case_id in selected_ids)


def build_runner(tmp_path: Path, architecture: str) -> EvaluationRunner:
    return EvaluationRunner(
        project_root=PROJECT_ROOT,
        database_path=tmp_path / f"{architecture}.sqlite3",
        replay_root=tmp_path / "replays" / architecture,
        architecture=architecture,
    )


def test_end_to_end_evaluation_covers_failures_attacks_and_approval(
    tmp_path: Path,
) -> None:
    report = build_runner(tmp_path, "single").run(representative_cases())

    assert report.dataset_size == 9
    assert report.pass_rate == 1
    assert report.safety_pass_rate == 1
    assert report.total_model_calls == 0
    assert all(result.replay_path for result in report.results if result.replay_path)


def test_multi_agent_uses_specialists_but_is_not_retained_without_gain(
    tmp_path: Path,
) -> None:
    cases = representative_cases()
    single = build_runner(tmp_path / "a", "single").run(cases)
    multi = build_runner(tmp_path / "b", "multi").run(cases)
    comparison = compare_reports(single, multi)

    assert multi.pass_rate == single.pass_rate == 1
    assert any(result.specialist_messages > 0 for result in multi.results)
    assert all(result.specialist_messages == 0 for result in single.results)
    assert comparison.recommendation == "prefer_single_agent"


def test_model_multi_agent_is_measured_with_fake_model_without_paid_api(
    tmp_path: Path,
) -> None:
    outputs = {
        f"specialist_review_{phase}": {
            "decision": "advisory_ok",
            "facts": {"phase": phase},
        }
        for phase in ("intake", "catalog", "supplier", "policy")
    }
    runner = EvaluationRunner(
        project_root=PROJECT_ROOT,
        database_path=tmp_path / "multi-llm.sqlite3",
        replay_root=tmp_path / "replays" / "multi-llm",
        architecture="multi_llm",
        model_client=FakeModel(outputs),
    )
    report = runner.run(representative_cases())

    assert report.pass_rate == 1
    assert report.safety_pass_rate == 1
    assert report.total_model_calls > 0
    assert report.estimated_total_cost_usd == 0
    assert any(result.specialist_messages == 4 for result in report.results)
