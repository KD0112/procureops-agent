from collections import Counter
from pathlib import Path

from procureops.evals.dataset import generate_cases, load_cases

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_eval_dataset_has_exact_governed_distribution() -> None:
    cases = generate_cases()
    counts = Counter(case.category for case in cases)

    assert len(cases) == 100
    assert len({case.case_id for case in cases}) == 100
    assert counts == {
        "normal": 40,
        "ambiguous": 20,
        "tool_failure": 15,
        "attack": 15,
        "approval_boundary": 10,
    }


def test_saved_eval_dataset_matches_generator() -> None:
    loaded = load_cases(
        PROJECT_ROOT / "data" / "eval_cases" / "procurement_e2e_100.jsonl"
    )

    assert loaded == generate_cases()


def test_cross_tenant_dataset_has_fixed_distribution() -> None:
    cases = load_cases(PROJECT_ROOT / "data" / "eval_cases" / "cross_tenant_it_20.jsonl")

    assert len(cases) == 20
    assert sum(case.tenant_id == "tenant_enterprise_it" for case in cases) == 19
    assert any(case.category == "cross_tenant_isolation" for case in cases)
