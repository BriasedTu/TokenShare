import json
from pathlib import Path

import pytest

import tokenshare.experiments.paper_catalog as paper_catalog_module
from tokenshare.experiments.paper_budget import plan_paper_suite
from tokenshare.experiments.paper_catalog import load_paper_catalogs
from tokenshare.experiments.paper_factorization_catalog import (
    CATALOG_GENERATOR_VERSION as FACTORIZATION_V2_GENERATOR_VERSION,
    is_prime_64,
)
from tokenshare.experiments.paper_models import PaperExperimentCondition


def test_paper_catalogs_load_30_factorization_and_30_lean_cases() -> None:
    manifest = load_paper_catalogs(
        factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
        lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
    )

    body = manifest.to_dict()

    assert body["schema_version"] == "tokenshare.paper_input_catalog_manifest.v1"
    assert body["case_count"] == 60
    assert body["domain_counts"] == {"factorization": 30, "lean_proof": 30}
    assert body["difficulty_counts"] == {
        "factorization": {"easy": 10, "medium": 10, "hard": 10},
        "lean_proof": {"easy": 10, "medium": 10, "hard": 10},
    }
    assert body["paper_difficulty_counts"] == {
        "factorization": {"easy": 10, "medium": 10, "hard": 10},
        "lean_proof": {"simple": 30, "medium_lemma_dag": 0, "hard_frontier": 0},
    }
    assert {case["paper_difficulty"] for case in manifest.lean_cases} == {"simple"}
    assert body["oracle_validation_status"] == "passed"
    assert body["lean_preflight_status"] == "passed"
    assert body["lean_preflight_summary"]["checked_case_count"] == 30
    assert body["lean_preflight_summary"]["accepted_case_count"] == 30
    assert body["lean_preflight_summary"]["environment_digest"].startswith("sha256:")
    assert {
        case["environment_digest"] for case in manifest.lean_cases
    } == {body["lean_preflight_summary"]["environment_digest"]}
    assert body["catalog_digest"].startswith("sha256:")
    assert manifest.catalog_digest == load_paper_catalogs(
        factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
        lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
    ).catalog_digest


def test_catalog_load_uses_matching_manifest_without_checker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_lean_startup(*args: object, **kwargs: object) -> object:
        raise AssertionError("普通 catalog load 不应启动 Lean 或 checker")

    monkeypatch.setattr(
        paper_catalog_module,
        "_default_lean_environment_manifest",
        unexpected_lean_startup,
    )
    monkeypatch.setattr(
        paper_catalog_module,
        "check_lean_proof",
        unexpected_lean_startup,
    )

    manifest = load_paper_catalogs(
        factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
        lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
    )

    assert manifest.lean_preflight_status == "passed"
    assert manifest.lean_preflight_summary["checked_case_count"] == 30


def test_catalog_load_rejects_stale_manifest_without_running_checker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = json.loads(
        paper_catalog_module.DEFAULT_LEAN_CATALOG_PREFLIGHT_MANIFEST_PATH.read_text(
            encoding="utf-8"
        )
    )
    body["entries"][0]["entry_key"] = "sha256:" + "0" * 64
    stale_path = tmp_path / "stale_lean_checker_preflight.v1.json"
    stale_path.write_text(
        json.dumps(body, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    def unexpected_lean_startup(*args: object, **kwargs: object) -> object:
        raise AssertionError("stale manifest 必须 fail closed，不能回退启动 checker")

    monkeypatch.setattr(
        paper_catalog_module,
        "_default_lean_environment_manifest",
        unexpected_lean_startup,
    )
    monkeypatch.setattr(
        paper_catalog_module,
        "check_lean_proof",
        unexpected_lean_startup,
    )

    with pytest.raises(ValueError, match="Lean catalog preflight manifest is stale"):
        load_paper_catalogs(
            factorization_path=Path(
                "benchmarks/paper/factorization_catalog.v1.jsonl"
            ),
            lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
            lean_preflight_manifest_path=stale_path,
        )


def test_paper_catalog_loads_the_frozen_500_root_factorization_v2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(paper_catalog_module, "_is_prime", is_prime_64)

    manifest = load_paper_catalogs(
        factorization_path=Path("benchmarks/paper/factorization_catalog.v2.jsonl"),
        lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
    )

    body = manifest.to_dict()
    assert body["catalog_version"] == "v2"
    assert body["generator_version"] == FACTORIZATION_V2_GENERATOR_VERSION
    assert body["case_count"] == 530
    assert body["domain_counts"] == {"factorization": 500, "lean_proof": 30}
    assert body["difficulty_counts"]["factorization"] == {
        "easy": 167,
        "medium": 167,
        "hard": 166,
    }
    assert len({case["target_n"] for case in manifest.factorization_cases}) == 500
    assert [case["catalog_ordinal"] for case in manifest.factorization_cases] == list(
        range(500)
    )


def test_loads_legal_lean_v2_lemma_dag_catalog_fixture() -> None:
    manifest = load_paper_catalogs(
        factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
        lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
        lean_lemma_graph_path=Path("benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl"),
    )

    body = manifest.to_dict()

    assert body["domain_counts"]["lean_proof"] == 195
    assert body["paper_difficulty_counts"]["lean_proof"] == {
        "simple": 75,
        "medium_lemma_dag": 45,
        "hard_frontier": 75,
    }
    assert len(manifest.lean_lemma_graph_cases) == 165
    assert {
        "lean_v2_medium_lemma_dag_01",
        "lean_v2_medium_function_set_dx_subset_chain_01",
        "lean_v2_medium_induction_nat_predicate_chain_01",
        "lean_v2_hard_frontier_01",
    } <= {case["case_id"] for case in manifest.lean_lemma_graph_cases}
    assert manifest.topic_family_counts["lean_proof"]["pure_logic"] == 85
    assert manifest.topic_family_counts["lean_proof"]["function_set"] == 55
    assert manifest.topic_family_counts["lean_proof"]["induction"] == 55
    assert manifest.paper_difficulty_topic_family_counts["lean_proof"][
        "medium_lemma_dag"
    ]["pure_logic"] == 15
    assert manifest.paper_difficulty_topic_family_counts["lean_proof"][
        "medium_lemma_dag"
    ]["function_set"] == 15
    assert manifest.paper_difficulty_topic_family_counts["lean_proof"][
        "medium_lemma_dag"
    ]["induction"] == 15
    assert manifest.paper_difficulty_topic_family_counts["lean_proof"][
        "hard_frontier"
    ] == {"pure_logic": 25, "function_set": 25, "induction": 25}


@pytest.mark.parametrize(
    "field_name",
    (
        "topic_family",
        "construction_rule_id",
        "oracle_package_group",
        "proof_assembly_shape",
    ),
)
def test_lean_v2_rejects_missing_required_topic_provenance_field(
    tmp_path: Path,
    field_name: str,
) -> None:
    case = _valid_medium_lemma_dag_case()
    case.pop(field_name)
    v2_path = _write_lemma_graph_catalog(tmp_path, case)

    with pytest.raises(ValueError, match=field_name):
        load_paper_catalogs(
            factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
            lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
            lean_lemma_graph_path=v2_path,
        )


def test_lean_v2_medium_rejects_missing_dependency_edges(tmp_path: Path) -> None:
    case = _valid_medium_lemma_dag_case()
    case.pop("dependency_edges")
    v2_path = _write_lemma_graph_catalog(tmp_path, case)

    with pytest.raises(ValueError, match="dependency_edges"):
        load_paper_catalogs(
            factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
            lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
            lean_lemma_graph_path=v2_path,
        )


def test_lean_v2_medium_rejects_missing_oracle_proof_package_ref(tmp_path: Path) -> None:
    case = _valid_medium_lemma_dag_case()
    case.pop("oracle_proof_package_ref")
    v2_path = _write_lemma_graph_catalog(tmp_path, case)

    with pytest.raises(ValueError, match="oracle_proof_package_ref"):
        load_paper_catalogs(
            factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
            lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
            lean_lemma_graph_path=v2_path,
        )


def test_lean_v2_medium_rejects_missing_environment_digest(tmp_path: Path) -> None:
    case = _valid_medium_lemma_dag_case()
    case.pop("environment_digest")
    v2_path = _write_lemma_graph_catalog(tmp_path, case)

    with pytest.raises(ValueError, match="environment_digest"):
        load_paper_catalogs(
            factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
            lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
            lean_lemma_graph_path=v2_path,
        )


def test_budget_estimation_uses_lean_v2_expected_ai_unit_count(tmp_path: Path) -> None:
    v2_path = _write_lemma_graph_catalog(tmp_path, _valid_medium_lemma_dag_case())
    catalog = load_paper_catalogs(
        factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
        lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
        lean_lemma_graph_path=v2_path,
    )
    conditions = (
        PaperExperimentCondition(
            experiment_id="exp1_real_ai_feasibility",
            condition_id="exp1_lean_medium_lemma_dag_repeat0",
            domain="lean_proof",
            difficulty="medium",
            paper_difficulty="medium_lemma_dag",
            topic_family="pure_logic",
            worker_count=10,
            fault_type="none",
            fault_rate=0.0,
            ablation_mode="FULL",
            model_policy="fixed_entry",
            repeat_id=0,
            seed=1,
            catalog_digest=catalog.catalog_digest,
        ),
    )

    budget = plan_paper_suite(
        catalog_manifest=catalog,
        conditions=conditions,
        max_provider_attempts_per_ai_unit=1,
        token_upper_bound_per_provider_attempt=100,
        cost_upper_bound_per_provider_attempt=0.01,
        plan_only=True,
    )

    assert budget.planned_root_runs == 1
    assert budget.planned_ai_units == 5


def test_budget_selection_filters_lean_medium_by_topic_family() -> None:
    catalog = load_paper_catalogs(
        factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
        lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
        lean_lemma_graph_path=Path("benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl"),
    )
    condition = PaperExperimentCondition(
        experiment_id="exp1_real_ai_feasibility",
        condition_id="exp1_lean_function_set_medium_lemma_dag_repeat0",
        domain="lean_proof",
        difficulty="medium",
        paper_difficulty="medium_lemma_dag",
        topic_family="function_set",
        worker_count=10,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=1,
        catalog_digest=catalog.catalog_digest,
    )

    budget = plan_paper_suite(
        catalog_manifest=catalog,
        conditions=(condition,),
        max_provider_attempts_per_ai_unit=1,
        token_upper_bound_per_provider_attempt=100,
        cost_upper_bound_per_provider_attempt=0.01,
        plan_only=True,
    )

    assert budget.planned_root_runs == 15
    assert budget.planned_ai_units == 60


def test_hard_frontier_without_oracle_cannot_claim_passed_preflight(tmp_path: Path) -> None:
    case = _valid_medium_lemma_dag_case()
    case["case_id"] = "lean_v2_hard_frontier_bad"
    case["paper_difficulty"] = "hard_frontier"
    case["difficulty"] = "hard"
    case["oracle_proof_package_ref"] = None
    case["preflight_status"] = "passed"
    v2_path = _write_lemma_graph_catalog(tmp_path, case)

    with pytest.raises(ValueError, match="hard_frontier"):
        load_paper_catalogs(
            factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
            lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
            lean_lemma_graph_path=v2_path,
        )


def test_manifest_outputs_topic_family_counts() -> None:
    manifest = load_paper_catalogs(
        factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
        lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
        lean_lemma_graph_path=Path("benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl"),
    )
    body = manifest.to_dict()

    assert body["topic_family_counts"]["lean_proof"]["pure_logic"] == 85
    assert body["topic_family_counts"]["lean_proof"]["function_set"] == 55
    assert body["topic_family_counts"]["lean_proof"]["induction"] == 55


def test_manifest_outputs_paper_difficulty_topic_family_counts() -> None:
    manifest = load_paper_catalogs(
        factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
        lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
        lean_lemma_graph_path=Path("benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl"),
    )
    body = manifest.to_dict()

    assert body["paper_difficulty_topic_family_counts"]["lean_proof"] == {
        "simple": {"pure_logic": 45, "function_set": 15, "induction": 15},
        "medium_lemma_dag": {"pure_logic": 15, "function_set": 15, "induction": 15},
        "hard_frontier": {"pure_logic": 25, "function_set": 25, "induction": 25},
    }


def test_catalog_topic_family_distribution_separates_shallow_and_lemma_graph_cases() -> None:
    manifest = load_paper_catalogs(
        factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
        lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
        lean_lemma_graph_path=Path("benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl"),
    )

    shallow_cases = manifest.cases_for(
        domain="lean_proof",
        paper_difficulty="simple",
        topic_family="pure_logic",
    )
    medium_cases = manifest.cases_for(
        domain="lean_proof",
        paper_difficulty="medium_lemma_dag",
    )
    hard_cases = manifest.cases_for(
        domain="lean_proof",
        paper_difficulty="hard_frontier",
    )

    assert len(shallow_cases) == 45
    assert {case["schema_version"] for case in shallow_cases} == {
        "tokenshare.paper_lean_case.v1",
        "tokenshare.paper_lean_lemma_graph_case.v1",
    }
    assert {case["topic_family"] for case in medium_cases} == {
        "pure_logic",
        "function_set",
        "induction",
    }
    assert len(hard_cases) == 75
    assert {case["topic_family"] for case in hard_cases} == {
        "pure_logic",
        "function_set",
        "induction",
    }
    checker_backed_hard = [
        case
        for case in hard_cases
        if case["preflight_status"] == "passed"
        and isinstance(case.get("oracle_proof_package_ref"), dict)
    ]
    blocked_hard = [
        case
        for case in hard_cases
        if case["preflight_status"] == "structured_blocked"
        and case["oracle_proof_package_ref"] is None
    ]
    assert len(checker_backed_hard) == 45
    assert len(blocked_hard) == 30


def test_cases_for_filters_lean_v2_by_paper_difficulty_and_topic_family() -> None:
    manifest = load_paper_catalogs(
        factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
        lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
        lean_lemma_graph_path=Path("benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl"),
    )

    cases = manifest.cases_for(
        domain="lean_proof",
        paper_difficulty="medium_lemma_dag",
        topic_family="pure_logic",
    )

    assert len(cases) == 15
    assert cases[0]["case_id"] == "lean_v2_medium_lemma_dag_01"


def test_cases_for_filters_function_set_medium_lemma_dag() -> None:
    manifest = load_paper_catalogs(
        factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
        lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
        lean_lemma_graph_path=Path("benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl"),
    )

    cases = manifest.cases_for(
        domain="lean_proof",
        paper_difficulty="medium_lemma_dag",
        topic_family="function_set",
    )

    assert len(cases) == 15
    assert cases[0]["case_id"] == "lean_v2_medium_function_set_dx_subset_chain_01"


def test_cases_for_filters_induction_medium_lemma_dag() -> None:
    manifest = load_paper_catalogs(
        factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
        lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
        lean_lemma_graph_path=Path("benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl"),
    )

    cases = manifest.cases_for(
        domain="lean_proof",
        paper_difficulty="medium_lemma_dag",
        topic_family="induction",
    )

    assert len(cases) == 15
    assert cases[0]["case_id"] == "lean_v2_medium_induction_nat_predicate_chain_01"


def test_cases_for_does_not_mix_shallow_v1_medium_into_medium_lemma_dag() -> None:
    manifest = load_paper_catalogs(
        factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
        lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
        lean_lemma_graph_path=Path("benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl"),
    )

    cases = manifest.cases_for(
        domain="lean_proof",
        difficulty="medium",
        paper_difficulty="medium_lemma_dag",
    )

    assert len(cases) == 45
    assert {
        "lean_v2_medium_lemma_dag_01",
        "lean_v2_medium_function_set_dx_subset_chain_01",
        "lean_v2_medium_induction_nat_predicate_chain_01",
    } <= {case["case_id"] for case in cases}
    assert {case["topic_family"] for case in cases} == {
        "pure_logic",
        "function_set",
        "induction",
    }
    assert all(case["paper_difficulty"] == "medium_lemma_dag" for case in cases)


def test_factorization_catalog_preflight_rejects_wrong_oracle_product(tmp_path: Path) -> None:
    factorization_path = tmp_path / "bad_factorization.jsonl"
    lean_path = Path("benchmarks/paper/lean_catalog.v1.jsonl")
    bad_case = {
        "schema_version": "tokenshare.paper_factorization_case.v1",
        "case_id": "bad_factor",
        "target_n": "899",
        "oracle_prime_factors": [{"prime": "13", "exponent": 1}, {"prime": "19", "exponent": 1}],
        "candidate_start": "2",
        "candidate_end": "17",
        "candidate_divisor_count": 16,
        "factor_position_quantile": "middle",
        "difficulty": "easy",
        "split_params": {"requested_child_count": 4},
        "source_seed": 1,
    }
    factorization_path.write_text(json.dumps(bad_case) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="oracle"):
        load_paper_catalogs(factorization_path=factorization_path, lean_path=lean_path)


def test_catalog_loader_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    source_path = Path("benchmarks/paper/lean_catalog.v1.jsonl")
    rows = source_path.read_text(encoding="utf-8").splitlines()
    duplicate_path = tmp_path / "duplicate_lean.jsonl"
    duplicate_path.write_text("\n".join([rows[0], rows[0]]) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate case_id"):
        load_paper_catalogs(
            factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
            lean_path=duplicate_path,
        )


def test_lean_catalog_loader_rejects_bad_oracle_as_stale_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = Path("benchmarks/paper/lean_catalog.v1.jsonl")
    rows = source_path.read_text(encoding="utf-8").splitlines()
    first = json.loads(rows[0])
    first["oracle_proof_ref"] = {
        **first["oracle_proof_ref"],
        "proof_source": "by\n  exact False.elim (by contradiction)",
    }
    bad_path = tmp_path / "bad_lean_catalog.jsonl"
    bad_path.write_text(
        json.dumps(first, ensure_ascii=False, sort_keys=True)
        + "\n"
        + "\n".join(rows[1:])
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        paper_catalog_module,
        "check_lean_proof",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("stale oracle evidence must not run checker")
        ),
    )

    with pytest.raises(
        ValueError,
        match="manifest is stale.*direct:lean_easy_01",
    ):
        load_paper_catalogs(
            factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
            lean_path=bad_path,
        )


def test_lean_catalog_rejects_environment_digest_drift(tmp_path: Path) -> None:
    source_path = Path("benchmarks/paper/lean_catalog.v1.jsonl")
    rows = source_path.read_text(encoding="utf-8").splitlines()
    first = json.loads(rows[0])
    first["environment_digest"] = "sha256:" + "0" * 64
    bad_path = tmp_path / "bad_lean_environment_digest.jsonl"
    bad_path.write_text(
        json.dumps(first, ensure_ascii=False, sort_keys=True)
        + "\n"
        + "\n".join(rows[1:])
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="environment_digest"):
        load_paper_catalogs(
            factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
            lean_path=bad_path,
        )


def test_catalog_case_lookup_rejects_unknown_domain_or_difficulty() -> None:
    manifest = load_paper_catalogs(
        factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
        lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
    )

    with pytest.raises(ValueError, match="domain"):
        manifest.cases_for(domain="lean-proof", difficulty="easy")
    with pytest.raises(ValueError, match="difficulty"):
        manifest.cases_for(domain="lean_proof", difficulty="all")


def _write_lemma_graph_catalog(tmp_path: Path, *cases: dict) -> Path:
    path = tmp_path / "lean_lemma_graph_catalog.v1.jsonl"
    path.write_text(
        "\n".join(json.dumps(case, ensure_ascii=False, sort_keys=True) for case in cases)
        + "\n",
        encoding="utf-8",
    )
    return path


def _valid_medium_lemma_dag_case() -> dict:
    rows = [
        json.loads(line)
        for line in Path("benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    for row in rows:
        if row["paper_difficulty"] == "medium_lemma_dag":
            return json.loads(json.dumps(row))
    raise AssertionError("missing medium_lemma_dag fixture")
