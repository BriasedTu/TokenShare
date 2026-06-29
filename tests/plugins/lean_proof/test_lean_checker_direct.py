import json
from pathlib import Path

import pytest

from tokenshare.plugins.lean_proof.checker import (
    LeanCheckerMode,
    LeanCheckerRequest,
    LeanCheckerStatus,
    check_lean_proof,
)
from tokenshare.plugins.lean_proof.environment import (
    LeanEnvironmentManifest,
    build_lean_environment_ref,
)
from tokenshare.plugins.lean_proof.fixtures import default_lean_fixture_project_path
from tokenshare.plugins.lean_proof.models import LeanTheoremPayload
from tokenshare.storage.artifacts import ArtifactStore


CREATED_AT = "2026-06-29T00:00:00Z"


def test_lean_checker_accepts_valid_direct_proof_with_real_environment(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    manifest = _environment_manifest()
    theorem_payload = _theorem_payload(statement_source="1 = 1")
    theorem_ref = store.save_json(
        theorem_payload.to_dict(),
        artifact_id="lean_theorem_one_eq_one",
        artifact_type="LeanTheoremPayload",
        artifact_schema_id="lean_proof.theorem_payload",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={"theorem_name": "one_eq_one"},
        created_at=CREATED_AT,
    )
    proof_ref = store.save_json(
        {
            "schema_version": "lean_proof.proof_candidate.v1",
            "proof_candidate_id": "proof_candidate:one_eq_one",
            "theorem_payload_digest": theorem_payload.payload_digest,
            "proof_source": "by\n  rfl",
            "created_at": CREATED_AT,
        },
        artifact_id="proof_candidate_one_eq_one",
        artifact_type="LeanProofCandidate",
        artifact_schema_id="lean_proof.proof_candidate",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={"theorem_name": "one_eq_one"},
        created_at=CREATED_AT,
    )

    report = check_lean_proof(
        LeanCheckerRequest(
            request_id="lean_checker_request:valid",
            theorem_payload_ref=theorem_ref,
            proof_candidate_ref=proof_ref,
            environment_ref=build_lean_environment_ref(manifest),
            checker_mode=LeanCheckerMode.DIRECT_PROOF,
            timeout_seconds=30,
            max_output_bytes=65536,
            created_at=CREATED_AT,
        ),
        artifact_store=store,
        environment_manifest=manifest,
    )

    assert report.status == LeanCheckerStatus.ACCEPTED
    assert report.exit_code == 0
    assert report.proof_artifact_ref is not None
    assert report.stdout_ref is not None
    assert report.stderr_ref is not None
    assert report.generated_source_ref is not None
    assert report.report_ref is not None
    assert store.verify(report.proof_artifact_ref)
    assert store.verify(report.generated_source_ref)
    assert store.verify(report.report_ref)
    report_body = json.loads(store.read_bytes(report.report_ref).decode("utf-8"))
    assert report_body["status"] == "accepted"
    assert report_body["environment_ref"]["environment_digest"] == manifest.environment_digest
    generated = store.read_bytes(report.generated_source_ref).decode("utf-8")
    assert "theorem one_eq_one : 1 = 1 := by" in generated
    assert "rfl" in generated


def test_lean_checker_rejects_invalid_proof_and_persists_logs(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    manifest = _environment_manifest()
    theorem_payload = _theorem_payload(statement_source="1 = 1")
    theorem_ref = store.save_json(
        theorem_payload.to_dict(),
        artifact_id="lean_theorem_invalid",
        artifact_type="LeanTheoremPayload",
        artifact_schema_id="lean_proof.theorem_payload",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={"theorem_name": "one_eq_one"},
        created_at=CREATED_AT,
    )
    proof_ref = store.save_json(
        {
            "schema_version": "lean_proof.proof_candidate.v1",
            "proof_candidate_id": "proof_candidate:invalid",
            "theorem_payload_digest": theorem_payload.payload_digest,
            "proof_source": "by\n  exact False.elim (by contradiction)",
            "created_at": CREATED_AT,
        },
        artifact_id="proof_candidate_invalid",
        artifact_type="LeanProofCandidate",
        artifact_schema_id="lean_proof.proof_candidate",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={"theorem_name": "one_eq_one"},
        created_at=CREATED_AT,
    )

    report = check_lean_proof(
        LeanCheckerRequest(
            request_id="lean_checker_request:invalid",
            theorem_payload_ref=theorem_ref,
            proof_candidate_ref=proof_ref,
            environment_ref=build_lean_environment_ref(manifest),
            checker_mode=LeanCheckerMode.DIRECT_PROOF,
            timeout_seconds=30,
            max_output_bytes=65536,
            created_at=CREATED_AT,
        ),
        artifact_store=store,
        environment_manifest=manifest,
    )

    assert report.status == LeanCheckerStatus.REJECTED
    assert report.exit_code != 0
    assert report.proof_artifact_ref is None
    assert report.stdout_ref is not None
    assert report.stderr_ref is not None
    assert report.generated_source_ref is not None
    assert report.report_ref is not None
    combined_log = (
        store.read_bytes(report.stdout_ref).decode("utf-8")
        + store.read_bytes(report.stderr_ref).decode("utf-8")
    )
    assert "error" in combined_log.lower()
    report_body = json.loads(store.read_bytes(report.report_ref).decode("utf-8"))
    assert report_body["status"] == "rejected"
    assert "error" in report_body["diagnostics"]["combined_excerpt"].lower()


def test_lean_checker_rejects_sorry_even_when_lean_returns_zero(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    manifest = _environment_manifest()
    theorem_payload = _theorem_payload(statement_source="1 = 2")
    theorem_ref = store.save_json(
        theorem_payload.to_dict(),
        artifact_id="lean_theorem_sorry",
        artifact_type="LeanTheoremPayload",
        artifact_schema_id="lean_proof.theorem_payload",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={"theorem_name": "one_eq_one"},
        created_at=CREATED_AT,
    )
    proof_ref = store.save_json(
        {
            "schema_version": "lean_proof.proof_candidate.v1",
            "proof_candidate_id": "proof_candidate:sorry",
            "theorem_payload_digest": theorem_payload.payload_digest,
            "proof_source": "by\n  sorry",
            "created_at": CREATED_AT,
        },
        artifact_id="proof_candidate_sorry",
        artifact_type="LeanProofCandidate",
        artifact_schema_id="lean_proof.proof_candidate",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={"theorem_name": "one_eq_one"},
        created_at=CREATED_AT,
    )

    report = check_lean_proof(
        LeanCheckerRequest(
            request_id="lean_checker_request:sorry",
            theorem_payload_ref=theorem_ref,
            proof_candidate_ref=proof_ref,
            environment_ref=build_lean_environment_ref(manifest),
            checker_mode=LeanCheckerMode.DIRECT_PROOF,
            timeout_seconds=30,
            max_output_bytes=65536,
            created_at=CREATED_AT,
        ),
        artifact_store=store,
        environment_manifest=manifest,
    )

    assert report.status == LeanCheckerStatus.REJECTED
    assert report.proof_artifact_ref is None
    assert report.proof_digest is None
    assert report.diagnostics["failure_kind"] == "forbidden_proof_placeholder"
    assert "sorry" in report.diagnostics["combined_excerpt"].lower()


def test_lean_checker_requires_schema_and_payload_digest(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    manifest = _environment_manifest()
    theorem_payload = _theorem_payload(statement_source="1 = 1")
    theorem_ref = store.save_json(
        theorem_payload.to_dict(),
        artifact_id="lean_theorem_malformed_candidate",
        artifact_type="LeanTheoremPayload",
        artifact_schema_id="lean_proof.theorem_payload",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={"theorem_name": "one_eq_one"},
        created_at=CREATED_AT,
    )
    proof_ref = store.save_json(
        {
            "schema_version": "lean_proof.proof_candidate.v1",
            "proof_candidate_id": "proof_candidate:missing_digest",
            "proof_source": "by\n  rfl",
            "created_at": CREATED_AT,
        },
        artifact_id="proof_candidate_missing_digest",
        artifact_type="LeanProofCandidate",
        artifact_schema_id="lean_proof.proof_candidate",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={"theorem_name": "one_eq_one"},
        created_at=CREATED_AT,
    )

    with pytest.raises(ValueError, match="theorem_payload_digest"):
        check_lean_proof(
            LeanCheckerRequest(
                request_id="lean_checker_request:missing_digest",
                theorem_payload_ref=theorem_ref,
                proof_candidate_ref=proof_ref,
                environment_ref=build_lean_environment_ref(manifest),
                checker_mode=LeanCheckerMode.DIRECT_PROOF,
                timeout_seconds=30,
                max_output_bytes=65536,
                created_at=CREATED_AT,
            ),
            artifact_store=store,
            environment_manifest=manifest,
        )


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


def _theorem_payload(*, statement_source: str) -> LeanTheoremPayload:
    return LeanTheoremPayload(
        theorem_id="lean_theorem:one_eq_one",
        theorem_name="one_eq_one",
        imports=["Init"],
        namespace="TokenShareGenerated",
        open_namespaces=[],
        options={},
        parameters_source="",
        statement_source=statement_source,
        theorem_source=None,
        proof_candidate_ref=None,
        library_context={
            "project": "tokenshare_lean",
            "module": "TokenShareGenerated.Direct",
        },
        decomposition_policy={
            "policy_id": "lean_proof.deterministic_tactic_split.v1",
            "allowed_rules": ["leaf_close"],
            "max_depth": 0,
            "max_children": 0,
            "unsupported_policy": "return_unsupported",
        },
        resource_limits={"timeout_seconds": 30, "max_output_bytes": 65536},
    )
