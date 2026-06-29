import json
from pathlib import Path

from tokenshare.plugins.lean_proof.checker import LeanCheckerStatus
from tokenshare.plugins.lean_proof.environment import (
    LeanEnvironmentManifest,
    build_lean_environment_ref,
)
from tokenshare.plugins.lean_proof.fixtures import default_lean_fixture_project_path
from tokenshare.plugins.lean_proof.models import LeanTheoremPayload
from tokenshare.plugins.lean_proof.split_strategy import (
    LeanSplitHelperRequest,
    build_lean_split_plan,
    run_lean_split_helper,
)
from tokenshare.plugins.lean_proof.child_proof import check_lean_child_proof
from tokenshare.plugins.lean_proof.descriptor import build_lean_proof_plugin_descriptor
from tokenshare.storage.artifacts import ArtifactStore


CREATED_AT = "2026-06-29T00:00:00Z"


def test_lean_child_goal_payload_can_be_checked_independently(tmp_path: Path) -> None:
    store, manifest, split_plan = _split_plan(tmp_path, statement_source="P ∧ Q")
    child_key = "child:left"
    proof_ref = _save_proof_candidate(
        store,
        artifact_id="proof_candidate_child_left",
        child_payload_ref=split_plan.child_payload_refs_by_logical_key[child_key],
        proof_source="by\n  exact hP",
    )

    result = check_lean_child_proof(
        child_logical_key=child_key,
        split_certificate=split_plan.certificate,
        child_payload_ref=split_plan.child_payload_refs_by_logical_key[child_key],
        proof_candidate_ref=proof_ref,
        artifact_store=store,
        environment_manifest=manifest,
        request_id="lean_child_checker:left",
        created_at=CREATED_AT,
    )

    assert result.accepted is True
    assert result.merge_ready is True
    assert result.context_digest == "sha256:lean_context_left"
    assert result.checker_report.status == LeanCheckerStatus.ACCEPTED
    assert result.checker_report.proof_artifact_ref is not None
    assert store.verify(result.checker_report.proof_artifact_ref)


def test_lean_child_proof_context_digest_must_match_split_certificate(
    tmp_path: Path,
) -> None:
    store, manifest, split_plan = _split_plan(tmp_path, statement_source="P ∧ Q")
    child_key = "child:left"
    child_payload_ref = split_plan.child_payload_refs_by_logical_key[child_key]
    original = json.loads(store.read_bytes(child_payload_ref).decode("utf-8"))
    tampered_payload = LeanTheoremPayload(
        **{
            key: value
            for key, value in original.items()
            if key not in {"schema_version", "payload_digest", "library_context"}
        },
        library_context={
            **original["library_context"],
            "child_logical_key": "child:tampered",
        },
    )
    tampered_ref = store.save_json(
        tampered_payload.to_dict(),
        artifact_id="tampered_child_payload",
        artifact_type="LeanChildTheoremPayload",
        artifact_schema_id="lean_proof.child_theorem_payload",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={"child_logical_key": child_key},
        created_at=CREATED_AT,
    )
    proof_ref = _save_proof_candidate(
        store,
        artifact_id="proof_candidate_tampered",
        child_payload_ref=tampered_ref,
        proof_source="by\n  exact hP",
    )

    result = check_lean_child_proof(
        child_logical_key=child_key,
        split_certificate=split_plan.certificate,
        child_payload_ref=tampered_ref,
        proof_candidate_ref=proof_ref,
        artifact_store=store,
        environment_manifest=manifest,
        request_id="lean_child_checker:tampered",
        created_at=CREATED_AT,
    )

    assert result.accepted is False
    assert result.merge_ready is False
    assert result.checker_report is None
    assert result.failure_kind == "child_payload_digest_mismatch"


def test_lean_child_proof_rejection_blocks_merge(tmp_path: Path) -> None:
    store, manifest, split_plan = _split_plan(tmp_path, statement_source="P ∧ Q")
    child_key = "child:left"
    proof_ref = _save_proof_candidate(
        store,
        artifact_id="proof_candidate_child_left_bad",
        child_payload_ref=split_plan.child_payload_refs_by_logical_key[child_key],
        proof_source="by\n  exact hQ",
    )

    result = check_lean_child_proof(
        child_logical_key=child_key,
        split_certificate=split_plan.certificate,
        child_payload_ref=split_plan.child_payload_refs_by_logical_key[child_key],
        proof_candidate_ref=proof_ref,
        artifact_store=store,
        environment_manifest=manifest,
        request_id="lean_child_checker:left_bad",
        created_at=CREATED_AT,
    )

    assert result.accepted is False
    assert result.merge_ready is False
    assert result.checker_report is not None
    assert result.checker_report.status == LeanCheckerStatus.REJECTED
    assert result.failure_kind == "lean_checker_rejected"


def _split_plan(tmp_path: Path, *, statement_source: str):
    store = ArtifactStore(tmp_path)
    manifest = _environment_manifest()
    payload = _theorem_payload(statement_source=statement_source)
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
            request_id="lean_split_request:child_flow",
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
    return store, manifest, split_plan


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
        "parameters_source": "(P Q : Prop) (hP : P) (hQ : Q)",
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
