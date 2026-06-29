import json
from dataclasses import replace
from pathlib import Path

import pytest

from tokenshare.plugins.lean_proof.checker import LeanCheckerStatus
from tokenshare.plugins.lean_proof.child_proof import check_lean_child_proof
from tokenshare.plugins.lean_proof.descriptor import build_lean_proof_plugin_descriptor
from tokenshare.plugins.lean_proof.environment import (
    LeanEnvironmentManifest,
    build_lean_environment_ref,
)
from tokenshare.plugins.lean_proof.fixtures import default_lean_fixture_project_path
from tokenshare.plugins.lean_proof.merge_policy import (
    LeanProofMergeInput,
    merge_lean_child_proofs,
)
from tokenshare.plugins.lean_proof.models import LeanTheoremPayload
from tokenshare.plugins.lean_proof.split_strategy import (
    LeanSplitHelperRequest,
    build_lean_split_plan,
    run_lean_split_helper,
)
from tokenshare.storage.artifacts import ArtifactStore


CREATED_AT = "2026-06-29T00:00:00Z"


def test_lean_merge_policy_builds_and_checks_conjunction_merge_proof(
    tmp_path: Path,
) -> None:
    store, manifest, split_plan, parent_payload_ref = _split_plan(
        tmp_path,
        statement_source="P ∧ Q",
        parameters_source="(P Q : Prop) (hP : P) (hQ : Q)",
    )
    child_results = [
        _child_result(store, manifest, split_plan, "child:left", "by\n  exact hP"),
        _child_result(store, manifest, split_plan, "child:right", "by\n  exact hQ"),
    ]

    result = merge_lean_child_proofs(
        merge_plan=split_plan.merge_plan,
        split_certificate=split_plan.certificate,
        parent_theorem_payload_ref=parent_payload_ref,
        child_proofs=[
            LeanProofMergeInput(slot_key=slot["slot_key"], child_proof=child_result)
            for slot, child_result in zip(split_plan.merge_plan.required_slots, child_results)
        ],
        artifact_store=store,
        environment_manifest=manifest,
        merge_unit_id="unit_lean_merge_conjunction",
        request_id="lean_merge_checker:conjunction",
        created_at=CREATED_AT,
    )

    assert result.accepted is True
    assert result.root_checker_report.status == LeanCheckerStatus.ACCEPTED
    assert result.merge_result_ref is not None
    assert store.verify(result.merge_result_ref)
    assert result.root_proof_artifact_ref is not None
    body = json.loads(store.read_bytes(result.merge_result_ref).decode("utf-8"))
    assert body["merge_rule_id"] == "lean_merge.conjunction_intro.v1"
    assert body["root_checker_report_ref"]["artifact_id"] == (
        result.root_checker_report.report_ref.artifact_id
    )
    assert "child:left" in body["child_proof_refs"]
    assert "child:right" in body["child_proof_refs"]
    merge_candidate = json.loads(
        store.read_bytes(result.merge_proof_candidate_ref).decode("utf-8")
    )
    assert "have child_left : P :=" in merge_candidate["proof_source"]
    assert "exact hP" in merge_candidate["proof_source"]
    assert "have child_right : Q :=" in merge_candidate["proof_source"]
    assert "exact hQ" in merge_candidate["proof_source"]
    assert "And.intro child_left child_right" in merge_candidate["proof_source"]


def test_lean_merge_policy_builds_and_checks_iff_merge_proof(tmp_path: Path) -> None:
    store, manifest, split_plan, parent_payload_ref = _split_plan(
        tmp_path,
        statement_source="P ↔ Q",
        parameters_source="(P Q : Prop) (hpq : P → Q) (hqp : Q → P)",
    )
    child_results = [
        _child_result(store, manifest, split_plan, "child:forward", "by\n  exact hpq"),
        _child_result(store, manifest, split_plan, "child:backward", "by\n  exact hqp"),
    ]

    result = merge_lean_child_proofs(
        merge_plan=split_plan.merge_plan,
        split_certificate=split_plan.certificate,
        parent_theorem_payload_ref=parent_payload_ref,
        child_proofs=[
            LeanProofMergeInput(slot_key=slot["slot_key"], child_proof=child_result)
            for slot, child_result in zip(split_plan.merge_plan.required_slots, child_results)
        ],
        artifact_store=store,
        environment_manifest=manifest,
        merge_unit_id="unit_lean_merge_iff",
        request_id="lean_merge_checker:iff",
        created_at=CREATED_AT,
    )

    assert result.accepted is True
    assert result.root_checker_report.status == LeanCheckerStatus.ACCEPTED
    body = json.loads(store.read_bytes(result.merge_result_ref).decode("utf-8"))
    assert body["merge_rule_id"] == "lean_merge.iff_intro.v1"


def test_lean_merge_policy_rejects_missing_required_child_proof(tmp_path: Path) -> None:
    store, manifest, split_plan, parent_payload_ref = _split_plan(
        tmp_path,
        statement_source="P ∧ Q",
        parameters_source="(P Q : Prop) (hP : P) (hQ : Q)",
    )
    child_result = _child_result(store, manifest, split_plan, "child:left", "by\n  exact hP")

    with pytest.raises(ValueError, match="missing required Lean proof slots"):
        merge_lean_child_proofs(
            merge_plan=split_plan.merge_plan,
            split_certificate=split_plan.certificate,
            parent_theorem_payload_ref=parent_payload_ref,
            child_proofs=[
                LeanProofMergeInput(
                    slot_key=split_plan.merge_plan.required_slots[0]["slot_key"],
                    child_proof=child_result,
                )
            ],
            artifact_store=store,
            environment_manifest=manifest,
            merge_unit_id="unit_lean_merge_missing",
            request_id="lean_merge_checker:missing",
            created_at=CREATED_AT,
        )


def test_lean_merge_policy_rejects_child_proof_from_different_environment_or_context(
    tmp_path: Path,
) -> None:
    store, manifest, split_plan, parent_payload_ref = _split_plan(
        tmp_path,
        statement_source="P ∧ Q",
        parameters_source="(P Q : Prop) (hP : P) (hQ : Q)",
    )
    left = _child_result(store, manifest, split_plan, "child:left", "by\n  exact hP")
    right = _child_result(store, manifest, split_plan, "child:right", "by\n  exact hQ")
    bad_report = replace(
        right.checker_report,
        environment_ref=replace(
            right.checker_report.environment_ref,
            environment_digest="sha256:different_environment",
        ),
    )
    bad_right = replace(right, checker_report=bad_report)

    with pytest.raises(ValueError, match="environment"):
        merge_lean_child_proofs(
            merge_plan=split_plan.merge_plan,
            split_certificate=split_plan.certificate,
            parent_theorem_payload_ref=parent_payload_ref,
            child_proofs=[
                LeanProofMergeInput(
                    slot_key=split_plan.merge_plan.required_slots[0]["slot_key"],
                    child_proof=left,
                ),
                LeanProofMergeInput(
                    slot_key=split_plan.merge_plan.required_slots[1]["slot_key"],
                    child_proof=bad_right,
                ),
            ],
            artifact_store=store,
            environment_manifest=manifest,
            merge_unit_id="unit_lean_merge_bad_env",
            request_id="lean_merge_checker:bad_env",
            created_at=CREATED_AT,
        )


def _split_plan(
    tmp_path: Path,
    *,
    statement_source: str,
    parameters_source: str,
):
    store = ArtifactStore(tmp_path)
    manifest = _environment_manifest()
    payload = _theorem_payload(
        statement_source=statement_source,
        parameters_source=parameters_source,
    )
    payload_ref = store.save_json(
        payload.to_dict(),
        artifact_id="lean_parent_payload",
        artifact_type="LeanTheoremPayload",
        artifact_schema_id="lean_proof.theorem_payload",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={"theorem_name": payload.theorem_name},
        created_at=CREATED_AT,
    )
    split_report = run_lean_split_helper(
        LeanSplitHelperRequest(
            request_id="lean_split_request:merge_flow",
            theorem_payload_ref=payload_ref,
            environment_ref=build_lean_environment_ref(manifest),
            timeout_seconds=30,
            max_output_bytes=65536,
            created_at=CREATED_AT,
        ),
        artifact_store=store,
        environment_manifest=manifest,
    )
    split_plan = build_lean_split_plan(
        split_report=split_report,
        artifact_store=store,
        task_id="task_lean",
        parent_unit_id="unit_lean",
        canonical_selection_id="canonical_selection:task_lean:unit_lean",
        canonical_output_bundle_digest="sha256:lean_canonical_bundle",
        plugin_descriptor_digest=build_lean_proof_plugin_descriptor().descriptor_digest,
        expansion_scope_hash="sha256:lean_scope",
        expansion_decision_id="expansion_decision:lean_scope",
        created_at=CREATED_AT,
    )
    return store, manifest, split_plan, payload_ref


def _child_result(store, manifest, split_plan, child_key: str, proof_source: str):
    proof_ref = _save_proof_candidate(
        store,
        artifact_id=f"proof_candidate_{child_key.replace(':', '_')}",
        child_payload_ref=split_plan.child_payload_refs_by_logical_key[child_key],
        proof_source=proof_source,
    )
    result = check_lean_child_proof(
        child_logical_key=child_key,
        split_certificate=split_plan.certificate,
        child_payload_ref=split_plan.child_payload_refs_by_logical_key[child_key],
        proof_candidate_ref=proof_ref,
        artifact_store=store,
        environment_manifest=manifest,
        request_id=f"lean_child_checker:{child_key}",
        created_at=CREATED_AT,
    )
    assert result.accepted is True
    return result


def _environment_manifest() -> LeanEnvironmentManifest:
    tools_root = Path.home() / "AppData" / "Local" / "TokenShare" / "LeanToolchain"
    elan_home = tools_root / "elan-home"
    return LeanEnvironmentManifest.from_project(
        project_root=default_lean_fixture_project_path(),
        lean_executable=elan_home / "bin" / "lean.exe",
        lake_executable=elan_home / "bin" / "lake.exe",
        lean_version="Lean (version 4.8.0, x86_64-w64-windows-gnu, commit df668f00e6c0, Release)",
        lake_version="Lake version 5.0.0-df668f0 (Lean version 4.8.0)",
        resource_limits={"timeout_seconds": 30, "max_output_bytes": 65536},
        created_at=CREATED_AT,
    )


def _theorem_payload(**overrides) -> LeanTheoremPayload:
    values = {
        "theorem_id": "lean_theorem:split_fixture",
        "theorem_name": "split_fixture",
        "imports": ["Init"],
        "namespace": "TokenShareGenerated",
        "open_namespaces": [],
        "options": {},
        "parameters_source": "(P Q : Prop)",
        "statement_source": "P ∧ Q",
        "theorem_source": None,
        "proof_candidate_ref": None,
        "library_context": {
            "project": "tokenshare_lean",
            "module": "TokenShareGenerated.Split",
        },
        "decomposition_policy": {
            "policy_id": "lean_proof.deterministic_tactic_split.v1",
            "allowed_rules": ["conjunction", "iff", "intro"],
            "max_depth": 4,
            "max_children": 8,
            "unsupported_policy": "return_unsupported",
        },
        "resource_limits": {"timeout_seconds": 30, "max_output_bytes": 65536},
    }
    values.update(overrides)
    return LeanTheoremPayload(**values)


def _save_proof_candidate(
    store: ArtifactStore,
    *,
    artifact_id: str,
    child_payload_ref,
    proof_source: str,
):
    payload = json.loads(store.read_bytes(child_payload_ref).decode("utf-8"))
    return store.save_json(
        {
            "schema_version": "lean_proof.proof_candidate.v1",
            "proof_candidate_id": f"proof_candidate:{artifact_id}",
            "theorem_payload_digest": payload["payload_digest"],
            "proof_source": proof_source,
            "created_at": CREATED_AT,
        },
        artifact_id=artifact_id,
        artifact_type="LeanProofCandidate",
        artifact_schema_id="lean_proof.proof_candidate",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={"theorem_name": payload["theorem_name"]},
        created_at=CREATED_AT,
    )
