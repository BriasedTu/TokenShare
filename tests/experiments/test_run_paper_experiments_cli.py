import json
from pathlib import Path

from tokenshare.experiments.run_paper_experiments import main


def test_paper_cli_plan_only_writes_budget_and_suite_manifest(tmp_path: Path) -> None:
    exit_code = main(
        [
            "--output-root",
            str(tmp_path),
            "--experiments",
            "exp1",
            "--plan-only",
            "--worker-levels",
            "10",
            "--repeats",
            "1",
            "--seed-family",
            "1",
        ]
    )

    suite_manifest_path = tmp_path / "suite_manifest.json"
    budget_path = tmp_path / "run_budget.json"
    assert exit_code == 0
    assert suite_manifest_path.exists()
    assert budget_path.exists()
    suite = json.loads(suite_manifest_path.read_text(encoding="utf-8"))
    budget = json.loads(budget_path.read_text(encoding="utf-8"))
    assert suite["schema_version"] == "tokenshare.paper_suite_result.v1"
    assert suite["status"] == "planned"
    assert suite["paper_eligible"] is False
    assert budget["planned_experiments"] == ["exp1_real_ai_feasibility"]
    assert budget["quota_preflight"]["provider_calls_made"] == 0


def test_paper_cli_plan_only_outputs_blocked_aware_lean_3x3_matrix(
    tmp_path: Path,
) -> None:
    exit_code = main(
        [
            "--output-root",
            str(tmp_path),
            "--experiments",
            "exp1",
            "--plan-only",
            "--worker-levels",
            "10",
            "--repeats",
            "1",
            "--seed-family",
            "1",
        ]
    )

    matrix_path = tmp_path / "lean_3x3_matrix.json"
    budget_path = tmp_path / "run_budget.json"
    assert exit_code == 0
    assert matrix_path.exists()
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    budget = json.loads(budget_path.read_text(encoding="utf-8"))

    assert matrix["schema_version"] == "tokenshare.lean_3x3_matrix_plan.v1"
    assert matrix["cell_count"] == 9
    assert matrix["target_case_count"] == 10
    assert budget["quota_preflight"]["provider_calls_made"] == 0
    assert budget["quota_preflight"]["lean_3x3_matrix"]["cell_count"] == 9

    cells = {
        (cell["paper_difficulty"], cell["topic_family"]): cell
        for cell in matrix["cells"]
    }
    assert set(cells) == {
        (paper_difficulty, topic_family)
        for paper_difficulty in ("simple", "medium_lemma_dag", "hard_frontier")
        for topic_family in ("pure_logic", "function_set", "induction")
    }

    simple_pure_logic = cells[("simple", "pure_logic")]
    assert simple_pure_logic["available_case_count"] == 30
    assert simple_pure_logic["expected_ai_unit_count"] == 60
    assert simple_pure_logic["preflight_status"]["status_counts"] == {"passed": 30}
    assert simple_pure_logic["paper_eligible_possible"] is True
    assert simple_pure_logic["blocked_reason"] is None

    simple_function_set = cells[("simple", "function_set")]
    assert simple_function_set["status"] == "blocked"
    assert simple_function_set["available_case_count"] == 0
    assert simple_function_set["paper_eligible_possible"] is False
    assert simple_function_set["blocked_reason"] == "insufficient_catalog"

    medium_cells = {
        topic_family: cells[("medium_lemma_dag", topic_family)]
        for topic_family in ("pure_logic", "function_set", "induction")
    }
    assert {
        topic_family: cell["available_case_count"]
        for topic_family, cell in medium_cells.items()
    } == {"pure_logic": 1, "function_set": 1, "induction": 1}
    assert {
        topic_family: cell["expected_ai_unit_count"]
        for topic_family, cell in medium_cells.items()
    } == {"pure_logic": 5, "function_set": 4, "induction": 5}
    assert all(cell["status"] == "blocked" for cell in medium_cells.values())
    assert all(
        cell["blocked_reason"] == "insufficient_catalog"
        for cell in medium_cells.values()
    )

    hard_pure_logic = cells[("hard_frontier", "pure_logic")]
    assert hard_pure_logic["catalog_case_count"] == 1
    assert hard_pure_logic["available_case_count"] == 0
    assert hard_pure_logic["expected_ai_unit_count"] == 4
    assert hard_pure_logic["preflight_status"]["status_counts"] == {
        "structured_blocked": 1
    }
    assert hard_pure_logic["preflight_status"]["summary"] == "structured_blocked"
    assert hard_pure_logic["paper_eligible_possible"] is False
    assert hard_pure_logic["blocked_reason"] == "insufficient_catalog"

    assert matrix["topic_family_expected_ai_unit_counts"] == {
        "pure_logic": 69,
        "function_set": 4,
        "induction": 5,
    }


def test_paper_cli_plan_only_does_not_count_shallow_v1_as_medium_or_hard(
    tmp_path: Path,
) -> None:
    exit_code = main(
        [
            "--output-root",
            str(tmp_path),
            "--experiments",
            "exp1",
            "--plan-only",
        ]
    )

    assert exit_code == 0
    matrix = json.loads(
        (tmp_path / "lean_3x3_matrix.json").read_text(encoding="utf-8")
    )
    cells = {
        (cell["paper_difficulty"], cell["topic_family"]): cell
        for cell in matrix["cells"]
    }

    assert cells[("medium_lemma_dag", "pure_logic")]["available_case_count"] == 1
    assert cells[("medium_lemma_dag", "function_set")]["available_case_count"] == 1
    assert cells[("medium_lemma_dag", "induction")]["available_case_count"] == 1
    assert cells[("hard_frontier", "pure_logic")]["available_case_count"] == 0
    assert cells[("hard_frontier", "function_set")]["available_case_count"] == 0
    assert cells[("hard_frontier", "induction")]["available_case_count"] == 0


def test_paper_cli_rejects_formal_run_without_real_transport(tmp_path: Path) -> None:
    exit_code = main(["--output-root", str(tmp_path), "--experiments", "exp1"])

    suite = json.loads((tmp_path / "suite_manifest.json").read_text(encoding="utf-8"))
    assert exit_code == 1
    assert suite["status"] == "blocked"
    assert suite["error_summary"][0]["failure_kind"] == "missing_real_transport"


def test_paper_cli_requires_budget_digest_for_formal_run(tmp_path: Path) -> None:
    exit_code = main(
        [
            "--output-root",
            str(tmp_path),
            "--experiments",
            "exp1",
            "--real-transport",
        ]
    )

    suite = json.loads((tmp_path / "suite_manifest.json").read_text(encoding="utf-8"))
    assert exit_code == 2
    assert suite["status"] == "blocked"
    assert suite["error_summary"][0]["failure_kind"] == "missing_budget_approval"
