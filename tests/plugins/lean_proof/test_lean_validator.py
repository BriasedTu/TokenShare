from dataclasses import replace
from pathlib import Path

from tokenshare.core.verification import build_verification_report
from tokenshare.plugins.lean_proof.checker import LeanCheckerReport, LeanCheckerStatus
from tokenshare.plugins.lean_proof.descriptor import build_lean_proof_plugin_descriptor
from tokenshare.plugins.lean_proof.environment import (
    LeanEnvironmentManifest,
    build_lean_environment_ref,
)
from tokenshare.plugins.lean_proof.fixtures import default_lean_fixture_project_path
from tokenshare.plugins.lean_proof.validator import verify_lean_checker_report
from tokenshare.storage.artifacts import ArtifactStore


CREATED_AT = "2026-06-29T00:00:00Z"


def test_lean_validator_maps_accepted_checker_report_to_passed_verification(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    checker_report = _checker_report(store, status=LeanCheckerStatus.ACCEPTED)

    validation = verify_lean_checker_report(checker_report)

    assert validation.accepted is True
    assert validation.status == "passed"
    assert validation.layer_summary["status"] == "passed"
    assert validation.layer_summary["reason_code"] == "lean_checker_accepted"
    assert validation.layer_summary["details"]["real_checker_evidence"] is True

    descriptor = build_lean_proof_plugin_descriptor()
    phase4_report = build_verification_report(
        verification_report_id="verification_lean_valid",
        task_id="task_lean",
        unit_id="unit_lean",
        attempt_id="attempt_lean",
        submission_id="submission_lean",
        submission_event_seq=1,
        candidate_output_refs={"lean_proof_artifact": checker_report.proof_artifact_ref},
        required_output_names=["lean_proof_artifact"],
        output_contract_id="lean_proof.proof_artifact.contract.v1",
        validator_policy_id="lean_proof.checker.validator.v1",
        plugin_id="lean_proof",
        plugin_version="0.1.0",
        plugin_descriptor_digest=descriptor.descriptor_digest,
        status="passed",
        expected_artifact_hashes={
            "lean_proof_artifact": checker_report.proof_artifact_ref.content_hash
        },
        required_evidence_ref_ids=[
            checker_report.stdout_ref.artifact_id,
            checker_report.stderr_ref.artifact_id,
        ],
        available_evidence_ref_ids=validation.layer_summary["evidence_refs"],
        plugin_domain_status=validation.layer_summary["status"],
        audit_status="passed",
        verification_environment=checker_report.environment_ref.to_dict(),
        verifier={"kind": "lean_checker", "validator_policy_id": "lean_proof.checker.validator.v1"},
        started_at=CREATED_AT,
        completed_at=CREATED_AT,
    )

    assert phase4_report.eligible_for_canonical is True


def test_lean_validator_maps_rejected_checker_report_to_invalid_output(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    checker_report = _checker_report(store, status=LeanCheckerStatus.REJECTED)

    validation = verify_lean_checker_report(checker_report)

    assert validation.accepted is False
    assert validation.status == "rejected"
    assert validation.layer_summary["reason_code"] == "lean_checker_rejected"
    assert validation.failure_summary == {
        "failure_kind": "invalid_output",
        "failed_layer": "plugin_domain_check",
        "message": "Lean checker rejected proof artifact",
        "evidence_refs": [
            checker_report.stdout_ref.artifact_id,
            checker_report.stderr_ref.artifact_id,
            checker_report.generated_source_ref.artifact_id,
            checker_report.report_ref.artifact_id,
        ],
    }


def test_lean_validator_requires_environment_ref_and_checker_log_refs(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    accepted = _checker_report(store, status=LeanCheckerStatus.ACCEPTED)

    no_environment = replace(accepted, environment_ref=None)  # type: ignore[arg-type]
    no_logs = replace(accepted, stdout_ref=None)
    no_proof = replace(accepted, proof_artifact_ref=None)

    assert verify_lean_checker_report(no_environment).layer_summary["reason_code"] == (
        "missing_environment_ref"
    )
    assert verify_lean_checker_report(no_logs).layer_summary["reason_code"] == (
        "missing_checker_logs"
    )
    assert verify_lean_checker_report(no_proof).layer_summary["reason_code"] == (
        "missing_proof_artifact"
    )


def _checker_report(
    store: ArtifactStore,
    *,
    status: LeanCheckerStatus,
) -> LeanCheckerReport:
    stdout_ref = store.save_bytes(
        b"",
        artifact_id=f"{status.value}_stdout",
        artifact_type="LeanCheckerStdout",
        media_type="text/plain",
        artifact_schema_id="lean_proof.checker_log",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={},
        created_at=CREATED_AT,
    )
    stderr_ref = store.save_bytes(
        b"error: rejected" if status == LeanCheckerStatus.REJECTED else b"",
        artifact_id=f"{status.value}_stderr",
        artifact_type="LeanCheckerStderr",
        media_type="text/plain",
        artifact_schema_id="lean_proof.checker_log",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={},
        created_at=CREATED_AT,
    )
    source_ref = store.save_bytes(
        b"theorem one_eq_one : 1 = 1 := by\n  rfl\n",
        artifact_id=f"{status.value}_source",
        artifact_type="LeanGeneratedSource",
        media_type="text/x-lean",
        artifact_schema_id="lean_proof.generated_source",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={},
        created_at=CREATED_AT,
    )
    report_ref = store.save_json(
        {
            "schema_version": "lean_proof.checker_report.v1",
            "status": status.value,
        },
        artifact_id=f"{status.value}_report",
        artifact_type="LeanCheckerReport",
        artifact_schema_id="lean_proof.checker_report",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={},
        created_at=CREATED_AT,
    )
    proof_ref = None
    if status == LeanCheckerStatus.ACCEPTED:
        proof_ref = store.save_bytes(
            b"by\n  rfl\n",
            artifact_id=f"{status.value}_proof",
            artifact_type="LeanProofArtifact",
            media_type="text/x-lean",
            artifact_schema_id="lean_proof.proof_artifact",
            artifact_schema_version="v1",
            source={"kind": "test"},
            metadata={},
            created_at=CREATED_AT,
        )

    return LeanCheckerReport(
        report_id=f"lean_checker_report:{status.value}",
        request_id=f"lean_checker_request:{status.value}",
        status=status,
        exit_code=0 if status == LeanCheckerStatus.ACCEPTED else 1,
        stdout_ref=stdout_ref,
        stderr_ref=stderr_ref,
        generated_source_ref=source_ref,
        proof_artifact_ref=proof_ref,
        report_ref=report_ref,
        diagnostics={"combined_excerpt": "error" if status == LeanCheckerStatus.REJECTED else ""},
        normalized_theorem_digest="sha256:theorem",
        proof_digest="sha256:proof" if proof_ref else None,
        environment_ref=build_lean_environment_ref(_environment_manifest()),
        command_summary={"executable": "lake", "args": ["env", "lean", "<generated_source>"]},
        duration_ms=10,
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
