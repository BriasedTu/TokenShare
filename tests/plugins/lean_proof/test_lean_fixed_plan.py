from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tokenshare.experiments.paper_catalog import (
    default_lean_paper_environment_manifest,
)
from tokenshare.plugins.lean_proof.fixed_plan import (
    LEAN_FIXED_DECOMPOSITION_PLAN_SCHEMA_VERSION,
    LeanFixedDecompositionPlan,
    build_fixed_plan_certificate,
)
from tokenshare.plugins.lean_proof.schemas import PROOF_ARTIFACT_OUTPUT_NAME
from tokenshare.storage.artifacts import ArtifactStore


CATALOG_PATH = Path("benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl")
CREATED_AT = "2026-07-22T00:00:00Z"


def test_fixed_plan_preserves_catalog_nodes_edges_merge_shape_and_builds_v2_certificate(
    tmp_path: Path,
) -> None:
    case = _case("lean_v2_medium_lemma_dag_01")
    plan = LeanFixedDecompositionPlan.from_catalog_case(case)

    assert plan.schema_version == LEAN_FIXED_DECOMPOSITION_PLAN_SCHEMA_VERSION
    assert plan.lemma_nodes == case["lemma_graph"]["nodes"]
    assert plan.dependency_edges == case["dependency_edges"]
    assert plan.merge_plan_shape == case["merge_plan_shape"]
    assert plan.plan_digest.startswith("sha256:")

    store = ArtifactStore(tmp_path)
    parent_payload = plan.parent_theorem_payload()
    parent_ref = store.save_json(
        parent_payload.to_dict(),
        artifact_id="fixed_plan_parent_payload",
        artifact_type="LeanTheoremPayload",
        artifact_schema_id="lean_proof.theorem_payload",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={"case_id": plan.case_id},
        created_at=CREATED_AT,
    )
    manifest = default_lean_paper_environment_manifest()
    certificate = build_fixed_plan_certificate(
        plan=plan,
        parent_theorem_payload_ref=parent_ref,
        environment_manifest=manifest,
    )

    assert certificate.rule_id == "lean_split.lemma_graph_dag.v2"
    assert certificate.root_node_id == case["merge_plan_shape"]["root_node_id"]
    assert certificate.environment_digest == manifest.environment_digest
    assert [node["node_id"] for node in certificate.lemma_nodes] == [
        node["node_id"] for node in case["lemma_graph"]["nodes"]
    ]
    assert all(
        node["required_output_name"] == PROOF_ARTIFACT_OUTPUT_NAME
        for node in certificate.lemma_nodes
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("cycle", "cycle"),
        ("second_root", "exactly one root"),
        ("missing_slot", "required_slots"),
        ("wrong_count", "expected_ai_unit_count"),
        ("wrong_depth", "expected_depth"),
        ("wrong_leaf_count", "expected_leaf_count"),
        ("missing_oracle_node", "oracle proof package node"),
    ],
)
def test_fixed_plan_rejects_catalog_graph_contract_drift(
    mutation: str,
    message: str,
) -> None:
    case = copy.deepcopy(_case("lean_v2_medium_lemma_dag_01"))
    nodes = case["lemma_graph"]["nodes"]
    if mutation == "cycle":
        case["dependency_edges"].append(
            {
                "source_node_id": case["merge_plan_shape"]["root_node_id"],
                "target_node_id": nodes[0]["node_id"],
            }
        )
    elif mutation == "second_root":
        nodes[0]["node_kind"] = "root_theorem"
    elif mutation == "missing_slot":
        case["merge_plan_shape"]["required_slots"].pop()
    elif mutation == "wrong_count":
        case["expected_ai_unit_count"] += 1
    elif mutation == "wrong_depth":
        case["expected_depth"] += 1
    elif mutation == "wrong_leaf_count":
        case["expected_leaf_count"] += 1
    elif mutation == "missing_oracle_node":
        case["oracle_proof_package_ref"]["node_proof_sources"].pop(
            nodes[0]["node_id"]
        )
    else:  # pragma: no cover - parametrization exhausts the cases.
        raise AssertionError(mutation)

    with pytest.raises(ValueError, match=message):
        LeanFixedDecompositionPlan.from_catalog_case(case)


def test_fixed_plan_certificate_rejects_environment_digest_drift(
    tmp_path: Path,
) -> None:
    case = copy.deepcopy(_case("lean_v2_medium_lemma_dag_01"))
    case["environment_digest"] = "sha256:wrong_environment"
    plan = LeanFixedDecompositionPlan.from_catalog_case(case)
    store = ArtifactStore(tmp_path)
    parent_ref = store.save_json(
        plan.parent_theorem_payload().to_dict(),
        artifact_id="fixed_plan_wrong_environment_parent",
        artifact_type="LeanTheoremPayload",
        artifact_schema_id="lean_proof.theorem_payload",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={},
        created_at=CREATED_AT,
    )

    with pytest.raises(ValueError, match="environment_digest"):
        build_fixed_plan_certificate(
            plan=plan,
            parent_theorem_payload_ref=parent_ref,
            environment_manifest=default_lean_paper_environment_manifest(),
        )


def test_structured_blocked_fixed_plan_can_omit_oracle_package() -> None:
    case = next(
        row
        for row in _cases()
        if row["proof_assembly_shape"]
        == "structured_blocked_no_oracle_frontier_stress.v1"
    )

    plan = LeanFixedDecompositionPlan.from_catalog_case(case)

    assert plan.oracle_proof_package_ref is None
    assert plan.preflight_status == "structured_blocked"


def _case(case_id: str) -> dict:
    return next(row for row in _cases() if row["case_id"] == case_id)


def _cases() -> list[dict]:
    return [
        json.loads(line)
        for line in CATALOG_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
