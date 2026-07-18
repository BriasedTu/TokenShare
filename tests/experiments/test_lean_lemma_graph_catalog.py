import json
from hashlib import sha256
from pathlib import Path
import pytest

from tokenshare.experiments.paper_catalog import (
    default_lean_paper_environment_manifest,
    lean_case_semantic_fingerprint as production_lean_case_semantic_fingerprint,
    load_paper_catalogs,
)


FACTOR_CATALOG = Path("benchmarks/paper/factorization_catalog.v1.jsonl")
LEAN_V1_CATALOG = Path("benchmarks/paper/lean_catalog.v1.jsonl")
LEAN_GRAPH_CATALOG = Path("benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl")


def test_medium_lemma_dag_catalog_has_real_graph_and_checker_preflight() -> None:
    manifest = _load_catalog_with_lemma_graph()
    case = _single_medium_case(manifest, topic_family="pure_logic")
    nodes = case["lemma_graph"]["nodes"]
    node_ids = {node["node_id"] for node in nodes}

    assert case["paper_difficulty"] == "medium_lemma_dag"
    assert case["topic_family"] == "pure_logic"
    assert case["topic_family_version"] == "v1"
    assert case["construction_rule_id"] == "fixed_oracle_lemma_graph.pure_logic.v1"
    assert case["oracle_package_group"] == "lean_lemma_graph_oracle.pure_logic.v1"
    assert case["proof_assembly_shape"] == "recursive_lemma_dag_required_slots.v1"
    assert case["root_theorem_payload"]["statement_source"] not in {
        "P /\\ Q",
        "P ∧ Q",
        "P ↔ Q",
    }
    assert case["expected_depth"] >= 3
    assert case["expected_leaf_count"] >= 2
    assert case["expected_ai_unit_count"] >= case["expected_leaf_count"]
    assert _is_acyclic(node_ids, case["dependency_edges"])

    root_nodes = [node for node in nodes if node["node_kind"] == "root_theorem"]
    intermediate_nodes = [
        node for node in nodes if node["node_kind"] == "intermediate_lemma"
    ]
    leaf_nodes = [node for node in nodes if node["node_kind"] == "leaf_sublemma"]

    assert len(root_nodes) == 1
    assert len(intermediate_nodes) >= 1
    assert len(leaf_nodes) >= 2
    assert root_nodes[0]["depth"] == case["expected_depth"]

    for node in nodes:
        assert {"node_id", "node_kind", "depth", "statement", "theorem_payload"} <= set(
            node
        )
        assert isinstance(node["depth"], int)
        assert node["theorem_payload"]["schema_version"] == "lean_proof.theorem_payload.v1"
        assert node["theorem_payload"]["statement_source"] == node["statement"]
        assert node["theorem_payload"]["theorem_name"]
        assert node["theorem_payload"]["imports"]

    oracle_ref = case["oracle_proof_package_ref"]
    package_path = Path(oracle_ref["source_path"])
    assert package_path.is_file()
    assert oracle_ref["content_hash"] == _sha256_file(package_path)
    assert set(oracle_ref["node_proof_sources"]) == node_ids
    assert case["environment_digest"] == (
        default_lean_paper_environment_manifest().environment_digest
    )
    assert case["preflight_status"] == "passed"

    summary = manifest.lean_lemma_graph_preflight_summary
    assert summary["status"] == "passed"
    assert summary["accepted_case_count"] >= 1
    assert summary["accepted_node_count_by_case"][case["case_id"]] == len(nodes)


def test_function_set_medium_lemma_dag_golden_case_preflight_passed() -> None:
    manifest = _load_catalog_with_lemma_graph()
    case = _single_medium_case(manifest, topic_family="function_set")

    assert case["case_id"] == "lean_v2_medium_function_set_dx_subset_chain_01"
    assert case["paper_difficulty"] == "medium_lemma_dag"
    assert case["topic_family"] == "function_set"
    assert case["topic_family_version"] == "v1"
    assert (
        case["construction_rule_id"]
        == "function_set.medium_lemma_dag.dx_subset_chain.v1"
    )
    assert (
        case["oracle_package_group"]
        == "lean_lemma_graph_oracle.function_set.v1"
    )
    assert case["proof_assembly_shape"] == "recursive_lemma_dag_required_slots.v1"
    assert case["expected_depth"] == 3
    assert case["expected_leaf_count"] == 2
    assert case["expected_ai_unit_count"] == 4
    assert case["preflight_status"] == "passed"
    _assert_checker_backed_node_sources_cover_graph(case)

    summary = manifest.lean_lemma_graph_preflight_summary
    assert summary["accepted_node_count_by_case"][case["case_id"]] == len(
        case["lemma_graph"]["nodes"]
    )


def test_induction_medium_lemma_dag_golden_case_preflight_passed() -> None:
    manifest = _load_catalog_with_lemma_graph()
    case = _single_medium_case(manifest, topic_family="induction")

    assert case["case_id"] == "lean_v2_medium_induction_nat_predicate_chain_01"
    assert case["paper_difficulty"] == "medium_lemma_dag"
    assert case["topic_family"] == "induction"
    assert case["topic_family_version"] == "v1"
    assert (
        case["construction_rule_id"]
        == "induction.medium_lemma_dag.nat_predicate_chain.v1"
    )
    assert case["oracle_package_group"] == "lean_lemma_graph_oracle.induction.v1"
    assert (
        case["proof_assembly_shape"]
        == "recursive_induction_lemma_dag_required_slots.v1"
    )
    assert case["expected_depth"] == 3
    assert case["expected_leaf_count"] == 3
    assert case["expected_ai_unit_count"] == 5
    assert case["preflight_status"] == "passed"
    _assert_checker_backed_node_sources_cover_graph(case)

    summary = manifest.lean_lemma_graph_preflight_summary
    assert summary["accepted_node_count_by_case"][case["case_id"]] == len(
        case["lemma_graph"]["nodes"]
    )


def test_topic_family_counts_include_task14_catalog_pool() -> None:
    manifest = _load_catalog_with_lemma_graph()

    assert manifest.topic_family_counts["lean_proof"] == {
        "pure_logic": 85,
        "function_set": 55,
        "induction": 55,
    }
    assert manifest.paper_difficulty_topic_family_counts["lean_proof"][
        "simple"
    ] == {
        "pure_logic": 45,
        "function_set": 15,
        "induction": 15,
    }
    assert manifest.paper_difficulty_topic_family_counts["lean_proof"][
        "medium_lemma_dag"
    ] == {
        "pure_logic": 15,
        "function_set": 15,
        "induction": 15,
    }
    assert manifest.paper_difficulty_topic_family_counts["lean_proof"][
        "hard_frontier"
    ] == {
        "pure_logic": 25,
        "function_set": 25,
        "induction": 25,
    }


def test_passed_lemma_graph_cases_have_complete_node_proof_sources() -> None:
    manifest = _load_catalog_with_lemma_graph()

    for case in manifest.lean_lemma_graph_cases:
        if case["preflight_status"] == "passed":
            _assert_checker_backed_node_sources_cover_graph(case)


def test_medium_lemma_dag_preflight_rejects_environment_digest_mismatch(
    tmp_path: Path,
) -> None:
    case = _single_medium_case(_load_catalog_with_lemma_graph(), topic_family="pure_logic")
    bad_case = {**case, "environment_digest": "sha256:" + "0" * 64}
    graph_path = _write_graph_catalog(tmp_path, bad_case)

    with pytest.raises(ValueError, match="environment_digest"):
        load_paper_catalogs(
            factorization_path=FACTOR_CATALOG,
            lean_path=LEAN_V1_CATALOG,
            lean_lemma_graph_path=graph_path,
        )


def test_medium_lemma_dag_preflight_rejects_bad_oracle_package_hash(
    tmp_path: Path,
) -> None:
    case = _single_medium_case(_load_catalog_with_lemma_graph(), topic_family="function_set")
    bad_ref = {
        **case["oracle_proof_package_ref"],
        "content_hash": "sha256:" + "1" * 64,
    }
    graph_path = _write_graph_catalog(tmp_path, {**case, "oracle_proof_package_ref": bad_ref})

    with pytest.raises(ValueError, match="oracle proof package"):
        load_paper_catalogs(
            factorization_path=FACTOR_CATALOG,
            lean_path=LEAN_V1_CATALOG,
            lean_lemma_graph_path=graph_path,
        )


def test_medium_lemma_dag_preflight_rejects_bad_node_oracle_proof(
    tmp_path: Path,
) -> None:
    case = _single_medium_case(_load_catalog_with_lemma_graph(), topic_family="induction")
    bad_ref = {
        **case["oracle_proof_package_ref"],
        "node_proof_sources": {
            node["node_id"]: "by\n  exact False.elim (by contradiction)"
            for node in case["lemma_graph"]["nodes"]
        },
    }
    graph_path = _write_graph_catalog(tmp_path, {**case, "oracle_proof_package_ref": bad_ref})

    with pytest.raises(ValueError, match="Lean lemma graph preflight rejected"):
        load_paper_catalogs(
            factorization_path=FACTOR_CATALOG,
            lean_path=LEAN_V1_CATALOG,
            lean_lemma_graph_path=graph_path,
        )


def test_hard_frontier_catalog_has_checker_backed_task14_pool() -> None:
    manifest = _load_catalog_with_lemma_graph()
    hard_cases = [
        case
        for case in manifest.lean_lemma_graph_cases
        if case["paper_difficulty"] == "hard_frontier"
    ]
    checker_backed = [
        case
        for case in hard_cases
        if case["preflight_status"] == "passed"
        and isinstance(case.get("oracle_proof_package_ref"), dict)
    ]

    assert len(hard_cases) == 75
    assert len(checker_backed) == 45
    assert {
        topic_family: sum(
            1 for case in checker_backed if case["topic_family"] == topic_family
        )
        for topic_family in ("pure_logic", "function_set", "induction")
    } == {"pure_logic": 15, "function_set": 15, "induction": 15}
    assert {
        topic_family: sum(
            1
            for case in hard_cases
            if case["topic_family"] == topic_family
            and case["preflight_status"] == "structured_blocked"
        )
        for topic_family in ("pure_logic", "function_set", "induction")
    } == {"pure_logic": 10, "function_set": 10, "induction": 10}
    assert manifest.lean_lemma_graph_preflight_summary["blocked_case_count"] == 30
    for case in checker_backed:
        assert case["difficulty"] == "hard"
        assert case["catalog_pool_role"] == "task14_checker_backed_pool"
        assert case["construction_rule_id"].startswith("hard_frontier.")
        assert case["preflight_status"] == "passed"
        _assert_checker_backed_node_sources_cover_graph(case)


def test_task14_selected_lemma_graph_cells_have_distinct_semantic_fingerprints() -> None:
    manifest = _load_catalog_with_lemma_graph()

    for paper_difficulty in ("simple", "medium_lemma_dag", "hard_frontier"):
        for topic_family in ("function_set", "induction"):
            cases = [
                case
                for case in manifest.lean_lemma_graph_cases
                if case["paper_difficulty"] == paper_difficulty
                and case["topic_family"] == topic_family
                and case["preflight_status"] == "passed"
                and isinstance(case.get("oracle_proof_package_ref"), dict)
            ][:15]
            fingerprints = [production_lean_case_semantic_fingerprint(case) for case in cases]

            assert len(cases) == 15
            assert len(set(fingerprints)) == 15, (
                "semantic duplicate: "
                f"{paper_difficulty}/{topic_family} selected rows must be 15 "
                "distinct theorem/DAG fingerprints after ignoring identity fields"
            )


def test_task14_hard_frontier_fingerprints_are_not_medium_renames() -> None:
    manifest = _load_catalog_with_lemma_graph()

    for topic_family in ("pure_logic", "function_set", "induction"):
        medium_fingerprints = {
            production_lean_case_semantic_fingerprint(case)
            for case in manifest.lean_lemma_graph_cases
            if case["paper_difficulty"] == "medium_lemma_dag"
            and case["topic_family"] == topic_family
            and case["preflight_status"] == "passed"
        }
        hard_fingerprints = {
            production_lean_case_semantic_fingerprint(case)
            for case in manifest.lean_lemma_graph_cases
            if case["paper_difficulty"] == "hard_frontier"
            and case["topic_family"] == topic_family
            and case["preflight_status"] == "passed"
            and isinstance(case.get("oracle_proof_package_ref"), dict)
        }

        assert len(hard_fingerprints) == 15
        assert hard_fingerprints.isdisjoint(medium_fingerprints), (
            "hard-is-medium: hard_frontier "
            f"{topic_family} roots must not be relabeled medium_lemma_dag "
            "fingerprints"
        )


def test_hard_frontier_without_oracle_still_structured_blocks_in_tmp_fixture(
    tmp_path: Path,
) -> None:
    case = _single_medium_case(_load_catalog_with_lemma_graph(), topic_family="pure_logic")
    case["case_id"] = "lean_v2_hard_frontier_no_oracle_tmp"
    case["paper_difficulty"] = "hard_frontier"
    case["difficulty"] = "hard"
    case["construction_rule_id"] = "frontier_stress_no_oracle.pure_logic.v1"
    case["oracle_package_group"] = "no_oracle.frontier_stress.pure_logic.v1"
    case["oracle_proof_package_ref"] = None
    case["preflight_status"] = "structured_blocked"
    case["proof_assembly_shape"] = "structured_blocked_no_oracle_frontier_stress.v1"
    case["structured_blocked_reason"] = "missing_oracle_proof_package"
    graph_path = _write_graph_catalog(tmp_path, case)

    manifest = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_V1_CATALOG,
        lean_lemma_graph_path=graph_path,
    )

    assert manifest.lean_lemma_graph_preflight_summary["blocked_case_count"] == 1
    assert manifest.lean_lemma_graph_cases[0]["preflight_status"] == "structured_blocked"
    assert manifest.lean_lemma_graph_cases[0]["oracle_proof_package_ref"] is None


def test_lean_v2_rejects_unsupported_proof_assembly_shape(tmp_path: Path) -> None:
    case = _single_medium_case(_load_catalog_with_lemma_graph(), topic_family="pure_logic")
    graph_path = _write_graph_catalog(
        tmp_path,
        {**case, "proof_assembly_shape": "unsupported_shape.v1"},
    )

    with pytest.raises(ValueError, match="proof_assembly_shape"):
        load_paper_catalogs(
            factorization_path=FACTOR_CATALOG,
            lean_path=LEAN_V1_CATALOG,
            lean_lemma_graph_path=graph_path,
        )


def _load_catalog_with_lemma_graph():
    return load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_V1_CATALOG,
        lean_lemma_graph_path=LEAN_GRAPH_CATALOG,
    )


def _single_medium_case(manifest, *, topic_family: str) -> dict:
    seed_case_ids = {
        "pure_logic": "lean_v2_medium_lemma_dag_01",
        "function_set": "lean_v2_medium_function_set_dx_subset_chain_01",
        "induction": "lean_v2_medium_induction_nat_predicate_chain_01",
    }
    cases = [
        case
        for case in manifest.lean_lemma_graph_cases
        if case["paper_difficulty"] == "medium_lemma_dag"
        and case["topic_family"] == topic_family
    ]
    assert len(cases) == 15
    matches = [case for case in cases if case["case_id"] == seed_case_ids[topic_family]]
    assert len(matches) == 1
    return dict(matches[0])


def _assert_checker_backed_node_sources_cover_graph(case: dict) -> None:
    nodes = case["lemma_graph"]["nodes"]
    node_ids = {node["node_id"] for node in nodes}
    oracle_ref = case["oracle_proof_package_ref"]
    assert oracle_ref["kind"] == "fixed_oracle_package"
    assert Path(oracle_ref["source_path"]).is_file()
    assert oracle_ref["content_hash"] == _sha256_file(Path(oracle_ref["source_path"]))
    assert set(oracle_ref["node_proof_sources"]) == node_ids
    for node_id, proof_source in oracle_ref["node_proof_sources"].items():
        assert node_id in node_ids
        assert isinstance(proof_source, str)
        assert proof_source.startswith("by\n")


def _write_graph_catalog(tmp_path: Path, *cases: dict) -> Path:
    path = tmp_path / "lean_lemma_graph_catalog.v1.jsonl"
    path.write_text(
        "\n".join(json.dumps(case, ensure_ascii=False, sort_keys=True) for case in cases)
        + "\n",
        encoding="utf-8",
    )
    return path


def _sha256_file(path: Path) -> str:
    return f"sha256:{sha256(path.read_bytes()).hexdigest()}"


def _is_acyclic(node_ids: set[str], edges: list[dict]) -> bool:
    outgoing = {node_id: [] for node_id in node_ids}
    incoming = {node_id: 0 for node_id in node_ids}
    for edge in edges:
        source = edge["source_node_id"]
        target = edge["target_node_id"]
        if source not in node_ids or target not in node_ids:
            return False
        outgoing[source].append(target)
        incoming[target] += 1

    ready = [node_id for node_id, count in incoming.items() if count == 0]
    seen = []
    while ready:
        node_id = ready.pop(0)
        seen.append(node_id)
        for target in outgoing[node_id]:
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
    return len(seen) == len(node_ids)
