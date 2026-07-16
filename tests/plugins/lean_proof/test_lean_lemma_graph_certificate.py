import copy

import pytest

from tokenshare.plugins.lean_proof.models import (
    LeanLemmaGraphCertificate,
    LeanTheoremPayload,
    canonical_json_digest,
)


ENVIRONMENT_DIGEST = "sha256:lemma_graph_environment"


def test_v2_lemma_graph_certificate_parses_serializes_and_has_canonical_digest() -> None:
    body = _lemma_graph_certificate_body()

    certificate = LeanLemmaGraphCertificate.from_dict(
        body,
        expected_environment_digest=ENVIRONMENT_DIGEST,
    )
    serialized = certificate.to_dict()
    reparsed = LeanLemmaGraphCertificate.from_dict(
        serialized,
        expected_environment_digest=ENVIRONMENT_DIGEST,
    )

    assert certificate.root_node_id == "root_r"
    assert serialized["certificate_schema_version"] == "lean_proof.lemma_graph_certificate.v2"
    assert serialized["topic_family"] == "pure_logic"
    assert serialized["topic_family_version"] == "v1"
    assert serialized["construction_rule_id"] == "fixed_oracle_lemma_graph.pure_logic.v1"
    assert serialized["oracle_package_group"] == "lean_lemma_graph_oracle.pure_logic.v1"
    assert serialized["proof_assembly_shape"] == "recursive_lemma_dag_required_slots.v1"
    assert serialized["certificate_digest"] == canonical_json_digest(
        {
            key: value
            for key, value in serialized.items()
            if key != "certificate_digest"
        }
    )
    assert reparsed == certificate


def test_v2_lemma_graph_certificate_digest_changes_when_topic_family_changes() -> None:
    pure_logic = LeanLemmaGraphCertificate.from_dict(
        _lemma_graph_certificate_body(topic_family="pure_logic")
    )
    induction = LeanLemmaGraphCertificate.from_dict(
        _lemma_graph_certificate_body(topic_family="induction")
    )

    assert pure_logic.certificate_digest != induction.certificate_digest


def test_v2_lemma_graph_certificate_digest_changes_when_construction_rule_changes() -> None:
    base = LeanLemmaGraphCertificate.from_dict(_lemma_graph_certificate_body())
    changed_rule = LeanLemmaGraphCertificate.from_dict(
        _lemma_graph_certificate_body(
            construction_rule_id="fixed_oracle_lemma_graph.pure_logic.alternate.v1"
        )
    )

    assert base.certificate_digest != changed_rule.certificate_digest


def test_v2_lemma_graph_certificate_rejects_invalid_topic_family() -> None:
    body = _lemma_graph_certificate_body(topic_family="topology")

    with pytest.raises(ValueError, match="topic_family"):
        LeanLemmaGraphCertificate.from_dict(body)


def test_v2_lemma_graph_certificate_rejects_unsupported_proof_assembly_shape() -> None:
    body = _lemma_graph_certificate_body(proof_assembly_shape="future_shape.v99")

    with pytest.raises(ValueError, match="proof_assembly_shape"):
        LeanLemmaGraphCertificate.from_dict(body)


def test_v2_lemma_graph_certificate_rejects_missing_construction_rule_id() -> None:
    body = _lemma_graph_certificate_body()
    del body["construction_rule_id"]

    with pytest.raises(ValueError, match="construction_rule_id"):
        LeanLemmaGraphCertificate.from_dict(body)


def test_v2_lemma_graph_certificate_rejects_missing_oracle_package_group() -> None:
    body = _lemma_graph_certificate_body()
    del body["oracle_package_group"]

    with pytest.raises(ValueError, match="oracle_package_group"):
        LeanLemmaGraphCertificate.from_dict(body)


def test_v2_lemma_graph_certificate_allows_structured_blocked_frontier_metadata() -> None:
    certificate = LeanLemmaGraphCertificate.from_dict(
        _lemma_graph_certificate_body(
            construction_rule_id="frontier_stress_no_oracle.pure_logic.v1",
            oracle_package_group="no_oracle.frontier_stress.pure_logic.v1",
            oracle_proof_package_ref=None,
            oracle_proof_package_digest=None,
            proof_assembly_shape="structured_blocked_no_oracle_frontier_stress.v1",
        )
    )

    assert certificate.oracle_proof_package_ref is None
    assert certificate.oracle_proof_package_digest is None
    assert (
        certificate.proof_assembly_shape
        == "structured_blocked_no_oracle_frontier_stress.v1"
    )


def test_v2_lemma_graph_certificate_rejects_missing_root_node_id() -> None:
    body = _lemma_graph_certificate_body()
    del body["root_node_id"]

    with pytest.raises(ValueError, match="root_node_id"):
        LeanLemmaGraphCertificate.from_dict(body)


def test_v2_lemma_graph_certificate_rejects_unknown_dependency_node() -> None:
    body = _lemma_graph_certificate_body()
    body["dependency_edges"][0]["source_node_id"] = "missing_node"

    with pytest.raises(ValueError, match="dependency edge"):
        LeanLemmaGraphCertificate.from_dict(body)


def test_v2_lemma_graph_certificate_rejects_dependency_cycle() -> None:
    body = _lemma_graph_certificate_body()
    body["dependency_edges"].append(
        {"source_node_id": "root_r", "target_node_id": "leaf_p"}
    )

    with pytest.raises(ValueError, match="cycle"):
        LeanLemmaGraphCertificate.from_dict(body)


def test_v2_lemma_graph_certificate_rejects_duplicate_node_id() -> None:
    body = _lemma_graph_certificate_body()
    duplicate = copy.deepcopy(body["lemma_nodes"][0])
    body["lemma_nodes"].append(duplicate)

    with pytest.raises(ValueError, match="duplicate node_id"):
        LeanLemmaGraphCertificate.from_dict(body)


def test_v2_lemma_graph_certificate_rejects_environment_digest_mismatch() -> None:
    body = _lemma_graph_certificate_body()

    with pytest.raises(ValueError, match="environment_digest"):
        LeanLemmaGraphCertificate.from_dict(
            body,
            expected_environment_digest="sha256:different_environment",
        )


def _lemma_graph_certificate_body(**overrides):
    nodes = [
        _node("leaf_p", depth=1, statement_source="P", max_depth=0, max_children=0),
        _node(
            "leaf_p_to_q",
            depth=1,
            statement_source="P -> Q",
            max_depth=0,
            max_children=0,
        ),
        _node("intermediate_q", depth=2, statement_source="Q", max_depth=1, max_children=2),
        _node(
            "leaf_q_to_r",
            depth=1,
            statement_source="Q -> R",
            max_depth=0,
            max_children=0,
        ),
        _node("root_r", depth=3, statement_source="R", max_depth=2, max_children=2),
    ]
    body = {
        "certificate_schema_version": "lean_proof.lemma_graph_certificate.v2",
        "certificate_id": "lean_lemma_graph_certificate:medium_logic",
        "parent_theorem_payload_ref": None,
        "normalized_parent_goal_digest": "sha256:parent_goal",
        "policy_id": "lean_proof.deterministic_tactic_split.v1",
        "rule_id": "lean_split.lemma_graph_dag.v2",
        "topic_family": "pure_logic",
        "topic_family_version": "v1",
        "construction_rule_id": "fixed_oracle_lemma_graph.pure_logic.v1",
        "oracle_package_group": "lean_lemma_graph_oracle.pure_logic.v1",
        "proof_assembly_shape": "recursive_lemma_dag_required_slots.v1",
        "root_node_id": "root_r",
        "lemma_nodes": nodes,
        "dependency_edges": [
            {"source_node_id": "leaf_p", "target_node_id": "intermediate_q"},
            {"source_node_id": "leaf_p_to_q", "target_node_id": "intermediate_q"},
            {"source_node_id": "intermediate_q", "target_node_id": "root_r"},
            {"source_node_id": "leaf_q_to_r", "target_node_id": "root_r"},
        ],
        "merge_nodes": [
            {
                "node_id": "intermediate_q",
                "merge_kind": "dependency_proof_unit",
                "required_input_node_ids": ["leaf_p", "leaf_p_to_q"],
            },
            {
                "node_id": "root_r",
                "merge_kind": "root_dependency_proof_unit",
                "required_input_node_ids": ["intermediate_q", "leaf_q_to_r"],
            },
        ],
        "environment_digest": ENVIRONMENT_DIGEST,
        "oracle_proof_package_ref": {
            "kind": "fixed_oracle_package",
            "package_id": "oracle:medium_logic",
            "content_hash": "sha256:oracle_package",
        },
        "oracle_proof_package_digest": "sha256:oracle_package",
        "diagnostics": {"source": "test"},
    }
    body.update(overrides)
    return body


def _node(
    node_id: str,
    *,
    depth: int,
    statement_source: str,
    max_depth: int,
    max_children: int,
):
    payload = LeanTheoremPayload(
        theorem_id=f"lean_lemma_graph_node:{node_id}",
        theorem_name=f"medium_logic_{node_id}",
        imports=["Init"],
        namespace="TokenShareGenerated",
        open_namespaces=[],
        options={},
        parameters_source="(P Q R : Prop) (hP : P) (hpq : P -> Q) (hqr : Q -> R)",
        statement_source=statement_source,
        theorem_source=None,
        proof_candidate_ref=None,
        library_context={
            "project": "tokenshare_lean",
            "module": "TokenShareGenerated.LemmaGraph",
            "lemma_node_id": node_id,
        },
        decomposition_policy={
            "policy_id": "lean_proof.deterministic_tactic_split.v1",
            "allowed_rules": ["fixed_oracle_lemma_graph"],
            "max_depth": max_depth,
            "max_children": max_children,
            "max_nodes": 8,
            "max_leaf_count": 4,
            "unsupported_policy": "return_unsupported",
        },
        resource_limits={"timeout_seconds": 30, "max_output_bytes": 65536},
    )
    return {
        "node_id": node_id,
        "node_kind": "root_theorem" if node_id == "root_r" else "lemma",
        "depth": depth,
        "required_output_name": "lean_proof_artifact",
        "context_digest": f"sha256:ctx_{node_id}",
        "theorem_payload": payload.to_dict(),
    }
