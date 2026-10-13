import copy
import json
import shutil
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from tests.support.lean_checker import RecordingLeanChecker
from tokenshare.plugins.lean_proof.checker import (
    LeanCheckerMode,
    LeanCheckerRequest,
    LeanCheckerStatus,
    check_lean_proof,
)
from tokenshare.plugins.lean_proof.descriptor import build_lean_proof_plugin_descriptor
from tokenshare.plugins.lean_proof.environment import (
    LeanEnvironmentManifest,
    build_lean_environment_ref,
)
from tokenshare.plugins.lean_proof.fixtures import default_lean_fixture_project_path
from tokenshare.plugins.lean_proof.models import (
    LeanLemmaGraphCertificate,
    LeanTheoremPayload,
    canonical_json_digest,
)
from tokenshare.plugins.lean_proof.schemas import (
    DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
    PROOF_ARTIFACT_OUTPUT_NAME,
)
from tokenshare.plugins.lean_proof.split_strategy import (
    LeanSplitHelperReport,
    LeanSplitHelperStatus,
    build_lean_split_plan,
)
from tokenshare.storage.artifacts import ArtifactStore


CREATED_AT = "2026-06-29T00:00:00Z"
CATALOG_PATH = Path("benchmarks/experiments/lean_lemma_graph_catalog.v1.jsonl")


@dataclass(frozen=True)
class LemmaGraphBundle:
    store: ArtifactStore
    manifest: LeanEnvironmentManifest
    row: dict
    certificate: LeanLemmaGraphCertificate
    split_plan: object
    parent_payload_ref: object
    node_inputs: list[dict]


@pytest.mark.lean_integration
def test_accepted_pure_logic_node_proofs_assemble_into_root_recheck(
    lemma_graph_bundle_factory,
) -> None:
    merge_policy = _merge_policy_api()
    bundle = lemma_graph_bundle_factory("lean_v2_medium_lemma_dag_01")

    result = merge_policy.merge_lean_lemma_graph_proofs(
        merge_plan=bundle.split_plan.merge_plan,
        lemma_graph_certificate=bundle.certificate,
        parent_theorem_payload_ref=bundle.parent_payload_ref,
        node_proofs=_proof_inputs(merge_policy, bundle),
        artifact_store=bundle.store,
        environment_manifest=bundle.manifest,
        merge_unit_id="unit_lean_merge_medium_logic",
        request_id="lean_lemma_graph_merge:medium_logic",
        created_at=CREATED_AT,
    )

    assert result.accepted is True
    assert result.root_checker_report.status == LeanCheckerStatus.ACCEPTED
    assert result.root_proof_artifact_ref is not None
    assert result.merge_result_ref is not None
    assert bundle.store.verify(result.merge_result_ref)
    candidate = json.loads(
        bundle.store.read_bytes(result.root_proof_candidate_ref).decode("utf-8")
    )
    proof_source = candidate["proof_source"]
    assert "have node_pure_medium_leaf_a_01 : P :=" in proof_source
    assert "have node_pure_medium_mid_b_01 : S :=" in proof_source
    assert "have node_pure_medium_root_01 : P ∨ Q :=" in proof_source
    assert (
        "exact node_pure_medium_leaf_ab_01 node_pure_medium_leaf_a_01"
        in proof_source
    )
    assert (
        "exact node_pure_medium_leaf_bc_01 node_pure_medium_mid_b_01"
        in proof_source
    )
    assert "TokenShare.LemmaGraphOracle." not in proof_source
    body = json.loads(bundle.store.read_bytes(result.merge_result_ref).decode("utf-8"))
    assert body["lemma_graph_certificate_id"] == bundle.certificate.certificate_id
    assert body["lemma_graph_certificate_digest"] == bundle.certificate.certificate_digest
    assert sorted(body["node_proof_refs"]) == sorted(
        node["node_id"] for node in bundle.node_inputs
    )
    assert body["root_checker_report_ref"]["artifact_id"] == (
        result.root_checker_report.report_ref.artifact_id
    )


@pytest.mark.lean_integration
@pytest.mark.parametrize(
    "case_id, dependency_snippet, forbidden_root_oracle",
    [
        (
            "lean_v2_medium_function_set_dx_subset_chain_01",
            "exact node_function_set_medium_ac_01",
            "TokenShare.LemmaGraphOracle.",
        ),
        (
            "lean_v2_medium_induction_nat_predicate_chain_01",
            "exact node_induction_medium_bc_01 n (node_induction_medium_b_01 n)",
            "TokenShare.LemmaGraphOracle.",
        ),
    ],
)
def test_function_set_and_induction_lemma_graphs_recheck_after_assembly(
    lemma_graph_bundle_factory,
    case_id: str,
    dependency_snippet: str,
    forbidden_root_oracle: str,
) -> None:
    merge_policy = _merge_policy_api()
    bundle = lemma_graph_bundle_factory(case_id)

    result = merge_policy.merge_lean_lemma_graph_proofs(
        merge_plan=bundle.split_plan.merge_plan,
        lemma_graph_certificate=bundle.certificate,
        parent_theorem_payload_ref=bundle.parent_payload_ref,
        node_proofs=_proof_inputs(merge_policy, bundle),
        artifact_store=bundle.store,
        environment_manifest=bundle.manifest,
        merge_unit_id=f"unit_lean_merge_{_safe_id(case_id)}",
        request_id=f"lean_lemma_graph_merge:{case_id}",
        created_at=CREATED_AT,
    )

    assert result.accepted is True
    assert result.root_checker_report.status == LeanCheckerStatus.ACCEPTED
    candidate = json.loads(
        bundle.store.read_bytes(result.root_proof_candidate_ref).decode("utf-8")
    )
    assert dependency_snippet in candidate["proof_source"]
    assert forbidden_root_oracle not in candidate["proof_source"]


def test_missing_leaf_proof_blocks_lemma_graph_merge(lemma_graph_bundle_factory) -> None:
    merge_policy = _merge_policy_api()
    bundle = lemma_graph_bundle_factory("lean_v2_medium_lemma_dag_01")
    inputs = [
        proof
        for proof in _proof_inputs(merge_policy, bundle)
        if proof.node_id != "pure_medium_leaf_a_01"
    ]

    with pytest.raises(ValueError, match="missing required Lean lemma graph proof slots"):
        merge_policy.merge_lean_lemma_graph_proofs(
            merge_plan=bundle.split_plan.merge_plan,
            lemma_graph_certificate=bundle.certificate,
            parent_theorem_payload_ref=bundle.parent_payload_ref,
            node_proofs=inputs,
            artifact_store=bundle.store,
            environment_manifest=bundle.manifest,
            merge_unit_id="unit_lean_merge_missing_leaf",
            request_id="lean_lemma_graph_merge:missing_leaf",
            created_at=CREATED_AT,
        )


def test_duplicate_node_proof_blocks_lemma_graph_merge(lemma_graph_bundle_factory) -> None:
    merge_policy = _merge_policy_api()
    bundle = lemma_graph_bundle_factory("lean_v2_medium_lemma_dag_01")
    inputs = _proof_inputs(merge_policy, bundle)

    with pytest.raises(ValueError, match="duplicate Lean lemma graph proof slot"):
        merge_policy.merge_lean_lemma_graph_proofs(
            merge_plan=bundle.split_plan.merge_plan,
            lemma_graph_certificate=bundle.certificate,
            parent_theorem_payload_ref=bundle.parent_payload_ref,
            node_proofs=[*inputs, inputs[0]],
            artifact_store=bundle.store,
            environment_manifest=bundle.manifest,
            merge_unit_id="unit_lean_merge_duplicate",
            request_id="lean_lemma_graph_merge:duplicate",
            created_at=CREATED_AT,
        )


def test_unexpected_node_proof_blocks_lemma_graph_merge(lemma_graph_bundle_factory) -> None:
    merge_policy = _merge_policy_api()
    bundle = lemma_graph_bundle_factory("lean_v2_medium_lemma_dag_01")
    inputs = _proof_inputs(merge_policy, bundle)
    unexpected = replace(
        inputs[0],
        node_id="unexpected_node",
        slot_key=f"unexpected_node:{PROOF_ARTIFACT_OUTPUT_NAME}",
    )

    with pytest.raises(ValueError, match="unexpected Lean lemma graph proof slots"):
        merge_policy.merge_lean_lemma_graph_proofs(
            merge_plan=bundle.split_plan.merge_plan,
            lemma_graph_certificate=bundle.certificate,
            parent_theorem_payload_ref=bundle.parent_payload_ref,
            node_proofs=[*inputs, unexpected],
            artifact_store=bundle.store,
            environment_manifest=bundle.manifest,
            merge_unit_id="unit_lean_merge_unexpected",
            request_id="lean_lemma_graph_merge:unexpected",
            created_at=CREATED_AT,
        )


def test_wrong_slot_key_blocks_lemma_graph_merge(lemma_graph_bundle_factory) -> None:
    merge_policy = _merge_policy_api()
    bundle = lemma_graph_bundle_factory("lean_v2_medium_lemma_dag_01")
    inputs = _proof_inputs(merge_policy, bundle)
    tampered = replace(inputs[0], slot_key=f"{inputs[0].node_id}:wrong_output")

    with pytest.raises(ValueError, match="unexpected Lean lemma graph proof slots"):
        merge_policy.merge_lean_lemma_graph_proofs(
            merge_plan=bundle.split_plan.merge_plan,
            lemma_graph_certificate=bundle.certificate,
            parent_theorem_payload_ref=bundle.parent_payload_ref,
            node_proofs=[tampered, *inputs[1:]],
            artifact_store=bundle.store,
            environment_manifest=bundle.manifest,
            merge_unit_id="unit_lean_merge_bad_slot",
            request_id="lean_lemma_graph_merge:bad_slot",
            created_at=CREATED_AT,
        )


@pytest.mark.parametrize(
    "field, value, expected",
    [
        ("context_digest", "sha256:wrong_context", "context"),
        ("theorem_payload_digest", "sha256:wrong_payload", "payload"),
    ],
)
def test_digest_mismatch_blocks_lemma_graph_merge(
    lemma_graph_bundle_factory,
    field: str,
    value: str,
    expected: str,
) -> None:
    merge_policy = _merge_policy_api()
    bundle = lemma_graph_bundle_factory("lean_v2_medium_lemma_dag_01")
    inputs = _proof_inputs(merge_policy, bundle)
    tampered = replace(inputs[0], **{field: value})

    with pytest.raises(ValueError, match=expected):
        merge_policy.merge_lean_lemma_graph_proofs(
            merge_plan=bundle.split_plan.merge_plan,
            lemma_graph_certificate=bundle.certificate,
            parent_theorem_payload_ref=bundle.parent_payload_ref,
            node_proofs=[tampered, *inputs[1:]],
            artifact_store=bundle.store,
            environment_manifest=bundle.manifest,
            merge_unit_id=f"unit_lean_merge_bad_{field}",
            request_id=f"lean_lemma_graph_merge:bad_{field}",
            created_at=CREATED_AT,
        )


def test_wrong_required_slot_theorem_payload_digest_blocks_lemma_graph_merge(
    lemma_graph_bundle_factory,
) -> None:
    merge_policy = _merge_policy_api()
    bundle = lemma_graph_bundle_factory("lean_v2_medium_lemma_dag_01")
    required_slots = copy.deepcopy(bundle.split_plan.merge_plan.required_slots)
    required_slots[0]["slot_metadata"]["theorem_payload_digest"] = "sha256:wrong_payload"
    tampered_merge_plan = replace(bundle.split_plan.merge_plan, required_slots=required_slots)

    with pytest.raises(ValueError, match="theorem payload"):
        merge_policy.merge_lean_lemma_graph_proofs(
            merge_plan=tampered_merge_plan,
            lemma_graph_certificate=bundle.certificate,
            parent_theorem_payload_ref=bundle.parent_payload_ref,
            node_proofs=_proof_inputs(merge_policy, bundle),
            artifact_store=bundle.store,
            environment_manifest=bundle.manifest,
            merge_unit_id="unit_lean_merge_bad_slot_payload_digest",
            request_id="lean_lemma_graph_merge:bad_slot_payload_digest",
            created_at=CREATED_AT,
        )


def test_wrong_environment_digest_blocks_lemma_graph_merge(
    lemma_graph_bundle_factory,
) -> None:
    merge_policy = _merge_policy_api()
    bundle = lemma_graph_bundle_factory("lean_v2_medium_lemma_dag_01")
    inputs = _proof_inputs(merge_policy, bundle)
    bad_report = replace(
        inputs[0].checker_report,
        environment_ref=replace(
            inputs[0].checker_report.environment_ref,
            environment_digest="sha256:wrong_environment",
        ),
    )
    tampered = replace(inputs[0], checker_report=bad_report)

    with pytest.raises(ValueError, match="environment"):
        merge_policy.merge_lean_lemma_graph_proofs(
            merge_plan=bundle.split_plan.merge_plan,
            lemma_graph_certificate=bundle.certificate,
            parent_theorem_payload_ref=bundle.parent_payload_ref,
            node_proofs=[tampered, *inputs[1:]],
            artifact_store=bundle.store,
            environment_manifest=bundle.manifest,
            merge_unit_id="unit_lean_merge_bad_environment",
            request_id="lean_lemma_graph_merge:bad_environment",
            created_at=CREATED_AT,
        )


def test_rejected_checker_report_blocks_lemma_graph_merge(
    lemma_graph_bundle_factory,
) -> None:
    merge_policy = _merge_policy_api()
    bundle = lemma_graph_bundle_factory("lean_v2_medium_lemma_dag_01")
    inputs = _proof_inputs(merge_policy, bundle)
    bad_report = replace(
        inputs[0].checker_report,
        status=LeanCheckerStatus.REJECTED,
        proof_artifact_ref=None,
    )
    tampered = replace(inputs[0], checker_report=bad_report)

    with pytest.raises(ValueError, match="accepted checker report"):
        merge_policy.merge_lean_lemma_graph_proofs(
            merge_plan=bundle.split_plan.merge_plan,
            lemma_graph_certificate=bundle.certificate,
            parent_theorem_payload_ref=bundle.parent_payload_ref,
            node_proofs=[tampered, *inputs[1:]],
            artifact_store=bundle.store,
            environment_manifest=bundle.manifest,
            merge_unit_id="unit_lean_merge_rejected_report",
            request_id="lean_lemma_graph_merge:rejected_report",
            created_at=CREATED_AT,
        )


def test_missing_proof_artifact_ref_blocks_lemma_graph_merge(
    lemma_graph_bundle_factory,
) -> None:
    merge_policy = _merge_policy_api()
    bundle = lemma_graph_bundle_factory("lean_v2_medium_lemma_dag_01")
    inputs = _proof_inputs(merge_policy, bundle)
    bad_report = replace(inputs[0].checker_report, proof_artifact_ref=None)
    tampered = replace(inputs[0], checker_report=bad_report)

    with pytest.raises(ValueError, match="proof artifact"):
        merge_policy.merge_lean_lemma_graph_proofs(
            merge_plan=bundle.split_plan.merge_plan,
            lemma_graph_certificate=bundle.certificate,
            parent_theorem_payload_ref=bundle.parent_payload_ref,
            node_proofs=[tampered, *inputs[1:]],
            artifact_store=bundle.store,
            environment_manifest=bundle.manifest,
            merge_unit_id="unit_lean_merge_missing_artifact",
            request_id="lean_lemma_graph_merge:missing_artifact",
            created_at=CREATED_AT,
        )


def test_structured_blocked_no_oracle_frontier_cannot_merge_success(
    lemma_graph_bundle_factory,
) -> None:
    merge_policy = _merge_policy_api()
    bundle = lemma_graph_bundle_factory("lean_v2_hard_frontier_01", check_nodes=False)

    with pytest.raises(ValueError, match="structured_blocked_no_oracle_frontier_stress"):
        merge_policy.merge_lean_lemma_graph_proofs(
            merge_plan=bundle.split_plan.merge_plan,
            lemma_graph_certificate=bundle.certificate,
            parent_theorem_payload_ref=bundle.parent_payload_ref,
            node_proofs=[],
            artifact_store=bundle.store,
            environment_manifest=bundle.manifest,
            merge_unit_id="unit_lean_merge_blocked_frontier",
            request_id="lean_lemma_graph_merge:blocked_frontier",
            created_at=CREATED_AT,
        )


@pytest.fixture
def lemma_graph_bundle_factory(tmp_path_factory, request):
    cache: dict[tuple[str, bool], LemmaGraphBundle] = {}
    real_lean = request.node.get_closest_marker("lean_integration") is not None

    def factory(case_id: str, *, check_nodes: bool = True) -> LemmaGraphBundle:
        key = (case_id, check_nodes)
        if key not in cache:
            tmp_path = tmp_path_factory.mktemp(_safe_id(case_id))
            cache[key] = _build_bundle(
                tmp_path, _catalog_row(case_id), check_nodes=check_nodes, real_lean=real_lean,
            )
        return cache[key]

    return factory


def _merge_policy_api():
    from tokenshare.plugins.lean_proof import merge_policy

    missing = [
        name
        for name in ("LeanLemmaGraphProofInput", "merge_lean_lemma_graph_proofs")
        if not hasattr(merge_policy, name)
    ]
    if missing:
        raise AssertionError("Lean lemma-DAG merge API is not implemented: " + ", ".join(missing))
    return merge_policy


def _proof_inputs(merge_policy, bundle: LemmaGraphBundle):
    return [
        merge_policy.LeanLemmaGraphProofInput(
            node_id=item["node_id"],
            slot_key=item["slot_key"],
            node_payload_ref=item["node_payload_ref"],
            proof_candidate_ref=item["proof_candidate_ref"],
            checker_report=item["checker_report"],
            context_digest=item["context_digest"],
            theorem_payload_digest=item["theorem_payload_digest"],
        )
        for item in bundle.node_inputs
    ]


def _build_bundle(
    tmp_path: Path, row: dict, *, check_nodes: bool, real_lean: bool,
) -> LemmaGraphBundle:
    store = ArtifactStore(tmp_path)
    manifest = _environment_manifest(tmp_path, real_lean=real_lean)
    parent_payload = _payload_from_catalog(
        row["root_theorem_payload"],
        case_id=row["case_id"],
        node_id=row["merge_plan_shape"]["root_node_id"],
    )
    parent_ref = store.save_json(
        parent_payload.to_dict(),
        artifact_id=f"parent_payload_{_safe_id(row['case_id'])}",
        artifact_type="LeanTheoremPayload",
        artifact_schema_id="lean_proof.theorem_payload",
        artifact_schema_version="v1",
        source={"kind": "test", "case_id": row["case_id"]},
        metadata={"theorem_name": parent_payload.theorem_name},
        created_at=CREATED_AT,
    )
    certificate = LeanLemmaGraphCertificate.from_dict(
        _certificate_body(row, parent_ref, manifest),
        expected_environment_digest=manifest.environment_digest,
    )
    certificate_ref = store.save_json(
        certificate.to_dict(),
        artifact_id=f"certificate_{_safe_id(row['case_id'])}",
        artifact_type="LeanLemmaGraphCertificate",
        artifact_schema_id="lean_proof.lemma_graph_certificate",
        artifact_schema_version="v2",
        source={"kind": "test", "case_id": row["case_id"]},
        metadata={"root_node_id": certificate.root_node_id},
        created_at=CREATED_AT,
    )
    split_report = LeanSplitHelperReport(
        report_id=f"lean_split_helper_report:{row['case_id']}",
        request_id=f"lean_split_request:{row['case_id']}",
        status=LeanSplitHelperStatus.SUCCEEDED,
        exit_code=0,
        generated_source_ref=None,
        helper_stdout_ref=None,
        helper_stderr_ref=None,
        certificate_ref=certificate_ref,
        report_ref=None,
        certificate=certificate,
        diagnostics={},
        environment_ref=build_lean_environment_ref(manifest),
        command_summary={},
        duration_ms=0,
        helper_stdout_excerpt="",
        helper_stderr_excerpt="",
    )
    split_plan = build_lean_split_plan(
        split_report=split_report,
        artifact_store=store,
        task_id=f"task_{_safe_id(row['case_id'])}",
        parent_unit_id=f"unit_{_safe_id(row['case_id'])}",
        canonical_selection_id=f"canonical_selection:{row['case_id']}",
        canonical_output_bundle_digest="sha256:lean_canonical_bundle",
        plugin_descriptor_digest=build_lean_proof_plugin_descriptor().descriptor_digest,
        expansion_scope_hash="sha256:lean_scope",
        expansion_decision_id=f"expansion_decision:{row['case_id']}",
        created_at=CREATED_AT,
    )
    node_inputs = (
        _accepted_node_inputs(
            row, certificate, split_plan, store, manifest,
            checker=check_lean_proof if real_lean else RecordingLeanChecker(),
        )
        if check_nodes
        else []
    )
    return LemmaGraphBundle(
        store=store,
        manifest=manifest,
        row=row,
        certificate=certificate,
        split_plan=split_plan,
        parent_payload_ref=parent_ref,
        node_inputs=node_inputs,
    )


def _accepted_node_inputs(
    row: dict,
    certificate: LeanLemmaGraphCertificate,
    split_plan,
    store: ArtifactStore,
    manifest: LeanEnvironmentManifest,
    *,
    checker,
) -> list[dict]:
    proof_sources = row["oracle_proof_package_ref"]["node_proof_sources"]
    result = []
    for node in certificate.lemma_nodes:
        node_id = node["node_id"]
        node_payload_ref = split_plan.child_payload_refs_by_logical_key[node_id]
        node_payload = LeanTheoremPayload.from_dict(
            json.loads(store.read_bytes(node_payload_ref).decode("utf-8"))
        )
        proof_candidate_ref = _save_proof_candidate(
            store,
            artifact_id=f"proof_candidate_{_safe_id(row['case_id'])}_{_safe_id(node_id)}",
            payload=node_payload,
            proof_source=proof_sources[node_id],
        )
        checker_report = checker(
            LeanCheckerRequest(
                request_id=f"lean_node_checker:{row['case_id']}:{node_id}",
                theorem_payload_ref=node_payload_ref,
                proof_candidate_ref=proof_candidate_ref,
                environment_ref=build_lean_environment_ref(manifest),
                checker_mode=LeanCheckerMode.CHILD_PROOF,
                timeout_seconds=int(node_payload.resource_limits["timeout_seconds"]),
                max_output_bytes=int(node_payload.resource_limits["max_output_bytes"]),
                created_at=CREATED_AT,
            ),
            artifact_store=store,
            environment_manifest=manifest,
        )
        assert checker_report.status == LeanCheckerStatus.ACCEPTED
        assert checker_report.proof_artifact_ref is not None
        result.append(
            {
                "node_id": node_id,
                "slot_key": f"{node_id}:{PROOF_ARTIFACT_OUTPUT_NAME}",
                "node_payload_ref": node_payload_ref,
                "proof_candidate_ref": proof_candidate_ref,
                "checker_report": checker_report,
                "context_digest": node["context_digest"],
                "theorem_payload_digest": node_payload.payload_digest,
            }
        )
    return result


def _certificate_body(row: dict, parent_ref, manifest: LeanEnvironmentManifest) -> dict:
    nodes = []
    for node in row["lemma_graph"]["nodes"]:
        payload = _payload_from_catalog(
            node["theorem_payload"],
            case_id=row["case_id"],
            node_id=node["node_id"],
        )
        nodes.append(
            {
                "node_id": node["node_id"],
                "node_kind": node["node_kind"],
                "depth": node["depth"],
                "required_output_name": PROOF_ARTIFACT_OUTPUT_NAME,
                "context_digest": _node_context_digest(row["case_id"], node["node_id"], payload),
                "theorem_payload": payload.to_dict(),
            }
        )
    root_node_id = row["merge_plan_shape"]["root_node_id"]
    oracle_ref = row.get("oracle_proof_package_ref")
    oracle_digest = oracle_ref.get("content_hash") if oracle_ref is not None else None
    return {
        "certificate_schema_version": "lean_proof.lemma_graph_certificate.v2",
        "certificate_id": f"lean_lemma_graph_certificate:{row['case_id']}",
        "parent_theorem_payload_ref": parent_ref.to_dict(),
        "normalized_parent_goal_digest": canonical_json_digest(
            {
                "case_id": row["case_id"],
                "root_node_id": root_node_id,
                "paper_difficulty": row["paper_difficulty"],
            }
        ),
        "policy_id": DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
        "rule_id": "lean_split.lemma_graph_dag.v2",
        "topic_family": row["topic_family"],
        "topic_family_version": row["topic_family_version"],
        "construction_rule_id": row["construction_rule_id"],
        "oracle_package_group": row["oracle_package_group"],
        "proof_assembly_shape": row["proof_assembly_shape"],
        "root_node_id": root_node_id,
        "lemma_nodes": nodes,
        "dependency_edges": list(row["dependency_edges"]),
        "merge_nodes": _merge_nodes(row),
        "environment_digest": manifest.environment_digest,
        "oracle_proof_package_ref": copy.deepcopy(oracle_ref),
        "oracle_proof_package_digest": oracle_digest,
        "diagnostics": {"source": "test"},
    }


def _merge_nodes(row: dict) -> list[dict]:
    incoming: dict[str, list[str]] = {
        node["node_id"]: [] for node in row["lemma_graph"]["nodes"]
    }
    for edge in row["dependency_edges"]:
        incoming[edge["target_node_id"]].append(edge["source_node_id"])
    root_node_id = row["merge_plan_shape"]["root_node_id"]
    return [
        {
            "node_id": node_id,
            "merge_kind": (
                "root_dependency_proof_unit"
                if node_id == root_node_id
                else "dependency_proof_unit"
            ),
            "required_input_node_ids": sources,
        }
        for node_id, sources in incoming.items()
        if sources
    ]


def _payload_from_catalog(payload_body: dict, *, case_id: str, node_id: str) -> LeanTheoremPayload:
    body = {
        "theorem_id": f"lean_lemma_graph:{case_id}:{node_id}",
        "namespace": "TokenSharePaperLemmaGraph",
        "open_namespaces": [],
        "options": {},
        "theorem_source": None,
        "proof_candidate_ref": None,
        "library_context": {
            "project": "tokenshare_lean",
            "module": "TokenShare.LemmaGraphOracle",
            "case_id": case_id,
            "node_id": node_id,
        },
        "decomposition_policy": {
            "policy_id": DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
            "allowed_rules": ["fixed_oracle_lemma_graph"],
            "max_depth": 4,
            "max_children": 8,
            "max_nodes": 16,
            "max_leaf_count": 8,
            "unsupported_policy": "return_unsupported",
        },
        "resource_limits": {"timeout_seconds": 30, "max_output_bytes": 65536},
        **copy.deepcopy(payload_body),
    }
    return LeanTheoremPayload.from_dict(body)


def _save_proof_candidate(
    store: ArtifactStore,
    *,
    artifact_id: str,
    payload: LeanTheoremPayload,
    proof_source: str,
):
    return store.save_json(
        {
            "schema_version": "lean_proof.proof_candidate.v1",
            "proof_candidate_id": f"proof_candidate:{artifact_id}",
            "theorem_payload_digest": payload.payload_digest,
            "proof_source": proof_source,
            "created_at": CREATED_AT,
        },
        artifact_id=artifact_id,
        artifact_type="LeanProofCandidate",
        artifact_schema_id="lean_proof.proof_candidate",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={"theorem_name": payload.theorem_name},
        created_at=CREATED_AT,
    )


def _node_context_digest(case_id: str, node_id: str, payload: LeanTheoremPayload) -> str:
    return canonical_json_digest(
        {
            "case_id": case_id,
            "node_id": node_id,
            "theorem_payload_digest": payload.payload_digest,
        }
    )


def _catalog_row(case_id: str) -> dict:
    for line in CATALOG_PATH.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["case_id"] == case_id:
            return row
    raise AssertionError(f"missing catalog case: {case_id}")


def _environment_manifest(tmp_path: Path, *, real_lean: bool) -> LeanEnvironmentManifest:
    tools_root = Path.home() / "AppData" / "Local" / "TokenShare" / "LeanToolchain"
    elan_home = tools_root / "elan-home"
    project_root = (
        _prepared_lean_project(tmp_path, lake_executable=elan_home / "bin" / "lake.exe")
        if real_lean else default_lean_fixture_project_path()
    )
    return LeanEnvironmentManifest.from_project(
        project_root=project_root,
        lean_executable=elan_home / "bin" / "lean.exe",
        lake_executable=elan_home / "bin" / "lake.exe",
        lean_version="Lean (version 4.8.0, x86_64-w64-windows-gnu, commit df668f00e6c0, Release)",
        lake_version="Lake version 5.0.0-df668f0 (Lean version 4.8.0)",
        resource_limits={"timeout_seconds": 30, "max_output_bytes": 65536},
        created_at=CREATED_AT,
    )


def _prepared_lean_project(tmp_path: Path, *, lake_executable: Path) -> Path:
    project_root = tmp_path / "lean_proof_project_runtime"
    if not project_root.exists():
        shutil.copytree(default_lean_fixture_project_path(), project_root)
    completed = subprocess.run(
        [str(lake_executable), "build"],
        cwd=project_root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        pytest.fail(
            "Lean fixture project build failed: "
            + (completed.stdout + completed.stderr)[:2000]
        )
    return project_root


def _safe_id(value: str) -> str:
    return "".join(character if character.isalnum() or character == "_" else "_" for character in value)
