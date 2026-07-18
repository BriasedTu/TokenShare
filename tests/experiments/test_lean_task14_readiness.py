import json
from pathlib import Path

import pytest

from tokenshare.experiments.lean_paper_adapter import (
    ScriptedLeanPaperProofTransport,
    build_lean_lemma_graph_oracle_evidence,
    run_lean_paper_case,
)
from tokenshare.experiments.paper_catalog import (
    lean_case_semantic_fingerprint,
    load_paper_catalogs,
)
from tokenshare.experiments.paper_models import (
    PaperExperimentCondition,
    PaperTaskStatus,
)
from tokenshare.experiments.paper_runner import (
    _lean_cell_readiness,
    build_lean_3x3_matrix_plan,
)
from tokenshare.experiments.run_paper_experiments import main


FACTOR_CATALOG = Path("benchmarks/paper/factorization_catalog.v1.jsonl")
LEAN_CATALOG = Path("benchmarks/paper/lean_catalog.v1.jsonl")
LEAN_GRAPH_CATALOG = Path("benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl")
TASK14_READINESS_MANIFEST = Path(
    "benchmarks/paper/lean_task14_3x3_readiness.v1.json"
)


def test_task14_lean_3x3_matrix_freezes_readiness_and_digests() -> None:
    catalog = _catalog_with_lemma_graph()
    matrix = build_lean_3x3_matrix_plan(catalog_manifest=catalog)
    target_case_count = 15

    assert matrix["schema_version"] == "tokenshare.lean_3x3_matrix_plan.v1"
    assert matrix["catalog_digest"] == catalog.catalog_digest
    assert matrix["environment_digest"] == catalog.lean_preflight_summary[
        "environment_digest"
    ]
    assert matrix["matrix_digest"].startswith("sha256:")
    assert matrix["provider_calls_made"] == 0
    assert matrix["oracle_package_digests"] == {
        "lean_lemma_graph_oracle.function_set.v1": (
            "sha256:4af791228bd24f4ea6d92fed511be8eee4f29d1ba8db5c6102968e743a8047ca"
        ),
        "lean_lemma_graph_oracle.induction.v1": (
            "sha256:4af791228bd24f4ea6d92fed511be8eee4f29d1ba8db5c6102968e743a8047ca"
        ),
        "lean_lemma_graph_oracle.pure_logic.v1": (
            "sha256:4af791228bd24f4ea6d92fed511be8eee4f29d1ba8db5c6102968e743a8047ca"
        ),
    }
    assert matrix["target_case_count"] == target_case_count
    assert matrix["paper_eligible_possible_cell_count"] == 9
    assert matrix["blocked_cell_count"] == 0

    cells = {
        (cell["paper_difficulty"], cell["topic_family"]): cell
        for cell in matrix["cells"]
    }
    assert set(cells) == {
        (paper_difficulty, topic_family)
        for paper_difficulty in ("simple", "medium_lemma_dag", "hard_frontier")
        for topic_family in ("pure_logic", "function_set", "induction")
    }
    selected_case_ids = [
        case_id for cell in cells.values() for case_id in cell["case_ids"]
    ]
    assert len(selected_case_ids) == 135
    assert len(set(selected_case_ids)) == 135

    for key, cell in cells.items():
        cell = cells[key]
        assert cell["status"] == "planned"
        assert cell["paper_eligible_possible"] is True
        assert cell["catalog_case_count"] == target_case_count
        assert cell["available_case_count"] == target_case_count
        assert cell["target_case_count"] == target_case_count
        assert len(cell["case_ids"]) == target_case_count
        assert len(set(cell["case_ids"])) == target_case_count
        assert cell["blocked_reason"] is None
        assert cell["preflight_status"] == {
            "checker_backed_case_count": target_case_count,
            "status_counts": {"passed": target_case_count},
            "summary": "passed",
        }
        assert cell["readiness"]["oracle_package"] == "present"
        assert cell["readiness"]["local_checker_preflight"] == "passed"
        assert cell["readiness"]["deterministic_split_rule"] == "ready"
        assert cell["readiness"]["proof_file_assembly"] == "ready"
        assert cell["readiness"]["dependency_aware_merge"] == "ready"
        assert cell["readiness"]["root_recheck"] == "ready"
        assert 1 <= len(cell["golden_case_ids"]) <= 2
        assert set(cell["golden_case_ids"]) <= set(cell["case_ids"])
        assert cell["expected_ai_unit_count"] > 0

    assert matrix["task15_budget_input"]["target_case_count"] == target_case_count
    assert matrix["task15_budget_input"]["executable_cell_count"] == 9
    assert matrix["task15_budget_input"]["blocked_cell_count"] == 0
    assert matrix["task15_budget_input"]["selected_case_count"] == 135
    assert matrix["task15_budget_input"]["selected_case_ids_by_cell"] == {
        f"{paper_difficulty}/{topic_family}": cells[
            (paper_difficulty, topic_family)
        ]["case_ids"]
        for paper_difficulty in ("simple", "medium_lemma_dag", "hard_frontier")
        for topic_family in ("pure_logic", "function_set", "induction")
    }
    assert matrix["task15_budget_input"]["selection_digest"].startswith("sha256:")
    assert matrix["task15_budget_input"]["matrix_digest"] == matrix["matrix_digest"]
    assert matrix["task15_budget_input"]["oracle_package_digests"] == matrix[
        "oracle_package_digests"
    ]


def test_task14_matrix_selected_ids_have_distinct_semantic_fingerprints() -> None:
    catalog = _catalog_with_lemma_graph()
    matrix = build_lean_3x3_matrix_plan(catalog_manifest=catalog)
    cases_by_id = {
        str(case["case_id"]): case
        for case in catalog.lean_cases + catalog.lean_lemma_graph_cases
    }

    for cell in matrix["cells"]:
        cell_key = f"{cell['paper_difficulty']}/{cell['topic_family']}"
        selected_cases = [cases_by_id[case_id] for case_id in cell["case_ids"]]
        fingerprints = [
            lean_case_semantic_fingerprint(case) for case in selected_cases
        ]

        assert len(selected_cases) == cell["target_case_count"] == 15
        assert len(set(fingerprints)) == 15, (
            "semantic duplicate: "
            f"{cell_key} selected case IDs must resolve to 15 distinct "
            "semantic theorem/DAG fingerprints"
        )
        assert cell.get("semantic_fingerprint_count") == 15
        assert cell.get("semantic_fingerprint_digest", "").startswith("sha256:")

    hard_by_topic = {
        cell["topic_family"]: {
            lean_case_semantic_fingerprint(cases_by_id[case_id])
            for case_id in cell["case_ids"]
        }
        for cell in matrix["cells"]
        if cell["paper_difficulty"] == "hard_frontier"
    }
    medium_by_topic = {
        cell["topic_family"]: {
            lean_case_semantic_fingerprint(cases_by_id[case_id])
            for case_id in cell["case_ids"]
        }
        for cell in matrix["cells"]
        if cell["paper_difficulty"] == "medium_lemma_dag"
    }
    for topic_family, hard_fingerprints in hard_by_topic.items():
        assert hard_fingerprints.isdisjoint(medium_by_topic[topic_family]), (
            "hard-is-medium: hard_frontier "
            f"{topic_family} selected IDs must not be relabeled "
            "medium_lemma_dag semantics"
        )


def test_task14_matrix_golden_cases_have_end_to_end_evidence() -> None:
    catalog = _catalog_with_lemma_graph()
    matrix = build_lean_3x3_matrix_plan(catalog_manifest=catalog)

    for cell in matrix["cells"]:
        cell_key = f"{cell['paper_difficulty']}/{cell['topic_family']}"
        evidence_by_id = cell.get("golden_evidence_by_case_id")
        assert isinstance(evidence_by_id, dict) and evidence_by_id, (
            f"missing golden evidence: {cell_key} must freeze explicit "
            "split/proof-file/checker/merge/root-recheck evidence"
        )
        assert set(cell["golden_case_ids"]) == set(evidence_by_id)
        for case_id, evidence in evidence_by_id.items():
            assert case_id in cell["case_ids"]
            assert evidence["evidence_source"] == "local_oracle_lemma_graph"
            assert evidence["deterministic_split"] == "passed"
            assert evidence["child_proof_file_construction"] == "passed"
            assert evidence["checker_preflight"] == "passed"
            assert evidence["dependency_aware_merge"] == "passed"
            assert evidence["root_recheck"] == "passed"
            assert evidence["provider_calls_made"] == 0
            assert evidence["split_certificate_digest"].startswith("sha256:")
            assert evidence["split_certificate_ref"]["content_hash"].startswith("sha256:")
            assert evidence["merge_result_ref"]["content_hash"].startswith("sha256:")
            assert evidence["root_checker_report_ref"]["content_hash"].startswith("sha256:")
            assert evidence["root_proof_artifact_ref"]["content_hash"].startswith("sha256:")
            assert evidence["node_checker_report_refs"]
            assert evidence["node_proof_artifact_refs"]


def test_task14_local_oracle_golden_evidence_runs_without_provider(
    tmp_path: Path,
) -> None:
    catalog = _catalog_with_lemma_graph()
    case = next(
        case
        for case in catalog.cases_for(
            domain="lean_proof",
            paper_difficulty="hard_frontier",
            topic_family="function_set",
        )
        if case["preflight_status"] == "passed"
        and case["case_id"] == "lean_v2_hard_frontier_function_set_checker_01"
    )

    evidence = build_lean_lemma_graph_oracle_evidence(
        case=case,
        output_root=tmp_path,
    )

    assert evidence["evidence_source"] == "local_oracle_lemma_graph"
    assert evidence["provider_calls_made"] == 0
    assert evidence["deterministic_split"] == "passed"
    assert evidence["child_proof_file_construction"] == "passed"
    assert evidence["checker_preflight"] == "passed"
    assert evidence["dependency_aware_merge"] == "passed"
    assert evidence["root_recheck"] == "passed"
    assert len(evidence["node_checker_report_refs"]) == case["expected_ai_unit_count"]
    assert evidence["merge_result_ref"]["content_hash"].startswith("sha256:")
    assert evidence["root_checker_report_ref"]["content_hash"].startswith("sha256:")
    assert evidence["root_proof_artifact_ref"]["content_hash"].startswith("sha256:")


def test_task14_readiness_blocks_when_golden_evidence_missing() -> None:
    case = _checker_backed_v2_case("golden_case_01")

    readiness = _lean_cell_readiness(
        cases=(case,),
        checker_backed_cases=(case,),
        blocked_reason=None,
        golden_case_ids=["golden_case_01"],
        golden_evidence_by_case_id={},
    )

    assert readiness["golden_evidence"] == "missing_golden_evidence"
    assert readiness["deterministic_split_rule"] == "blocked"
    assert readiness["proof_file_assembly"] == "blocked"
    assert readiness["local_checker_preflight"] == "blocked"
    assert readiness["dependency_aware_merge"] == "blocked"
    assert readiness["root_recheck"] == "blocked"
    assert readiness["blocker"] == "missing_golden_evidence"
    assert readiness["provider_calls_made"] == 0


def test_task14_readiness_blocks_when_golden_case_is_not_selected() -> None:
    case = _checker_backed_v2_case("selected_case_01")

    readiness = _lean_cell_readiness(
        cases=(case,),
        checker_backed_cases=(case,),
        blocked_reason=None,
        golden_case_ids=["missing_case_01"],
        golden_evidence_by_case_id={
            "missing_case_01": _passed_golden_evidence("missing_case_01")
        },
    )

    assert readiness["golden_evidence"] == "missing_golden_evidence"
    assert readiness["blocker"] == "missing_golden_evidence"
    assert readiness["provider_calls_made"] == 0


def test_task14_readiness_blocks_when_golden_case_is_not_checker_backed() -> None:
    checker_backed_case = _checker_backed_v2_case("selected_case_01")
    non_checker_golden = _checker_backed_v2_case("golden_case_01")
    non_checker_golden["preflight_status"] = "failed"

    readiness = _lean_cell_readiness(
        cases=(checker_backed_case, non_checker_golden),
        checker_backed_cases=(checker_backed_case,),
        blocked_reason=None,
        golden_case_ids=["golden_case_01"],
        golden_evidence_by_case_id={
            "golden_case_01": _passed_golden_evidence("golden_case_01")
        },
    )

    assert readiness["golden_evidence"] == "missing_golden_evidence"
    assert readiness["blocker"] == "missing_golden_evidence"
    assert readiness["provider_calls_made"] == 0


def test_task14_readiness_blocks_when_too_many_golden_cases_are_frozen() -> None:
    cases = tuple(
        _checker_backed_v2_case(case_id)
        for case_id in ("golden_case_01", "golden_case_02", "golden_case_03")
    )

    readiness = _lean_cell_readiness(
        cases=cases,
        checker_backed_cases=cases,
        blocked_reason=None,
        golden_case_ids=["golden_case_01", "golden_case_02", "golden_case_03"],
        golden_evidence_by_case_id={
            case["case_id"]: _passed_golden_evidence(case["case_id"])
            for case in cases
        },
    )

    assert readiness["golden_evidence"] == "missing_golden_evidence"
    assert readiness["blocker"] == "missing_golden_evidence"
    assert readiness["provider_calls_made"] == 0


@pytest.mark.parametrize(
    "evidence_stage, readiness_field",
    [
        ("deterministic_split", "deterministic_split_rule"),
        ("child_proof_file_construction", "proof_file_assembly"),
        ("checker_preflight", "local_checker_preflight"),
        ("dependency_aware_merge", "dependency_aware_merge"),
        ("root_recheck", "root_recheck"),
    ],
)
def test_task14_readiness_blocks_when_any_golden_stage_fails(
    evidence_stage: str,
    readiness_field: str,
) -> None:
    case = _checker_backed_v2_case("golden_case_01")
    evidence = _passed_golden_evidence("golden_case_01")
    evidence[evidence_stage] = "failed"

    readiness = _lean_cell_readiness(
        cases=(case,),
        checker_backed_cases=(case,),
        blocked_reason=None,
        golden_case_ids=["golden_case_01"],
        golden_evidence_by_case_id={"golden_case_01": evidence},
    )

    assert readiness["golden_evidence"] == "blocked"
    assert readiness[readiness_field] == "blocked"
    assert readiness["blocker"] == "missing_golden_evidence"
    assert readiness["provider_calls_made"] == 0


@pytest.mark.parametrize(
    "topic_family, expected_ai_units, expected_split_kind",
    [
        ("function_set", 1, "recursive_lemma_dag"),
        ("induction", 1, "recursive_induction_lemma_dag"),
    ],
)
def test_task14_simple_v2_cases_run_split_assembly_merge_and_root_recheck(
    tmp_path: Path,
    topic_family: str,
    expected_ai_units: int,
    expected_split_kind: str,
) -> None:
    catalog = _catalog_with_lemma_graph()
    case = next(
        case
        for case in catalog.cases_for(
            domain="lean_proof",
            paper_difficulty="simple",
            topic_family=topic_family,
        )
        if case["schema_version"] == "tokenshare.paper_lean_lemma_graph_case.v1"
    )
    condition = _condition_for_case(catalog.catalog_digest, case)
    transport = ScriptedLeanPaperProofTransport(
        proof_sources_by_statement=_oracle_sources_by_statement(case)
    )

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="lean_paper_scripted",
    )

    assert result.task_result.root_status == PaperTaskStatus.COMPLETED
    assert result.task_result.paper_difficulty == "simple"
    assert result.task_result.topic_family == topic_family
    assert result.task_result.provider_attempt_count == expected_ai_units
    assert len(transport.calls) == expected_ai_units
    assert result.split_summary["split_status"] == "succeeded"
    assert result.split_summary["split_kind"] == expected_split_kind
    assert result.merge_summary["root_checker_accepted"] is True
    assert result.merge_summary["root_checker_report_ref"]
    assert result.merge_summary["root_proof_artifact_ref"]
    assert all(item["checker"]["accepted"] is True for item in result.child_results)


def test_task14_plan_only_cli_freezes_lean_3x3_zero_call_input(
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

    assert exit_code == 0
    budget = json.loads((tmp_path / "run_budget.json").read_text(encoding="utf-8"))
    matrix = json.loads(
        (tmp_path / "lean_3x3_matrix.json").read_text(encoding="utf-8")
    )
    assert budget["quota_preflight"]["provider_calls_made"] == 0
    assert budget["quota_preflight"]["lean_3x3_matrix"]["provider_calls_made"] == 0
    assert matrix["provider_calls_made"] == 0
    assert matrix["target_case_count"] == 15
    assert matrix["paper_eligible_possible_cell_count"] == 9
    assert matrix["blocked_cell_count"] == 0
    assert matrix["task15_budget_input"]["target_case_count"] == 15
    assert matrix["task15_budget_input"]["selected_case_count"] == 135
    assert len(
        {
            case_id
            for case_ids in matrix["task15_budget_input"][
                "selected_case_ids_by_cell"
            ].values()
            for case_id in case_ids
        }
    ) == 135
    assert matrix["task15_boundary"] == {
        "formal_exp1_started": False,
        "exp2_to_exp5_started": False,
        "real_ai_api_calls_allowed": False,
        "provider_calls_made": 0,
    }


def test_task14_static_readiness_manifest_matches_generated_matrix() -> None:
    catalog = _catalog_with_lemma_graph()
    matrix = build_lean_3x3_matrix_plan(catalog_manifest=catalog)
    frozen = json.loads(TASK14_READINESS_MANIFEST.read_text(encoding="utf-8"))

    assert frozen["schema_version"] == "tokenshare.lean_task14_3x3_readiness.v1"
    assert frozen["catalog_version"] == "lean_lemma_graph_catalog.v1"
    assert frozen["catalog_digest"] == matrix["catalog_digest"]
    assert frozen["environment_digest"] == matrix["environment_digest"]
    assert frozen["matrix_digest"] == matrix["matrix_digest"]
    assert frozen["provider_calls_made"] == 0
    assert frozen["target_case_count"] == 15
    assert frozen["paper_eligible_possible_cell_count"] == 9
    assert frozen["blocked_cell_count"] == 0
    assert frozen["task15_budget_input"]["target_case_count"] == 15
    assert frozen["task15_budget_input"]["selected_case_count"] == 135
    assert frozen["task15_budget_input"] == matrix["task15_budget_input"]
    assert frozen["executable_cell_map"] == {
        "simple/pure_logic": True,
        "simple/function_set": True,
        "simple/induction": True,
        "medium_lemma_dag/pure_logic": True,
        "medium_lemma_dag/function_set": True,
        "medium_lemma_dag/induction": True,
        "hard_frontier/pure_logic": True,
        "hard_frontier/function_set": True,
        "hard_frontier/induction": True,
    }


def _catalog_with_lemma_graph():
    return load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
        lean_lemma_graph_path=LEAN_GRAPH_CATALOG,
    )


def _checker_backed_v2_case(case_id: str) -> dict:
    return {
        "case_id": case_id,
        "schema_version": "tokenshare.paper_lean_lemma_graph_case.v1",
        "paper_difficulty": "medium_lemma_dag",
        "preflight_status": "passed",
        "oracle_proof_package_ref": {
            "content_hash": "sha256:test",
            "node_proof_sources": {"node": "by\n  exact trivial"},
        },
    }


def _passed_golden_evidence(case_id: str) -> dict:
    return {
        "case_id": case_id,
        "evidence_source": "local_oracle_lemma_graph",
        "deterministic_split": "passed",
        "child_proof_file_construction": "passed",
        "checker_preflight": "passed",
        "dependency_aware_merge": "passed",
        "root_recheck": "passed",
        "provider_calls_made": 0,
    }


def _condition_for_case(catalog_digest: str, case: dict) -> PaperExperimentCondition:
    return PaperExperimentCondition(
        experiment_id="exp1_real_ai_feasibility",
        condition_id=f"task14_lean_{case['paper_difficulty']}_{case['case_id']}_r0",
        domain="lean_proof",
        difficulty=case["difficulty"],
        paper_difficulty=case.get("paper_difficulty"),
        topic_family=case.get("topic_family"),
        topic_family_version=case.get("topic_family_version"),
        construction_rule_id=case.get("construction_rule_id"),
        oracle_package_group=case.get("oracle_package_group"),
        proof_assembly_shape=case.get("proof_assembly_shape"),
        worker_count=10,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=1,
        catalog_digest=catalog_digest,
    )


def _oracle_sources_by_statement(case: dict) -> dict[str, str]:
    proof_sources_by_node = case["oracle_proof_package_ref"]["node_proof_sources"]
    return {
        node["theorem_payload"]["statement_source"]: proof_sources_by_node[
            node["node_id"]
        ]
        for node in case["lemma_graph"]["nodes"]
    }
