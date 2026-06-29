import json
import subprocess
from pathlib import Path

import pytest

from tokenshare.plugins.lean_proof.fixtures import run_lean_direct_proof_fixture_flow
from tokenshare.plugins.lean_proof.replay_evidence import (
    LeanReplayEvidenceError,
    verify_lean_replay_evidence,
)


def test_lean_replay_evidence_reads_checker_artifacts_without_running_lean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = run_lean_direct_proof_fixture_flow(tmp_path)

    def fail_subprocess_run(*args, **kwargs):
        raise AssertionError("replay evidence guard must not run Lean subprocess")

    monkeypatch.setattr(subprocess, "run", fail_subprocess_run)

    evidence = verify_lean_replay_evidence(
        artifact_root=result.store.artifact_dir,
        checker_report_ref=result.checker_report.report_ref,
        expected_environment_digest=result.environment_manifest.environment_digest,
    )

    assert evidence.accepted is True
    assert evidence.replay_no_checker_call is True
    assert evidence.environment_digest == result.environment_manifest.environment_digest
    assert evidence.checker_report_ref.artifact_id == result.checker_report.report_ref.artifact_id
    assert result.checker_report.stdout_ref.artifact_id in evidence.required_artifact_ids
    assert result.checker_report.stderr_ref.artifact_id in evidence.required_artifact_ids
    assert result.checker_report.proof_artifact_ref.artifact_id in evidence.required_artifact_ids


def test_lean_replay_evidence_fails_when_checker_log_artifact_is_missing(
    tmp_path: Path,
) -> None:
    result = run_lean_direct_proof_fixture_flow(tmp_path)
    result.store.read_bytes(result.checker_report.stdout_ref)
    (result.store.artifact_dir / result.checker_report.stdout_ref.artifact_id).unlink()

    with pytest.raises(LeanReplayEvidenceError, match="missing checker log artifact"):
        verify_lean_replay_evidence(
            artifact_root=result.store.artifact_dir,
            checker_report_ref=result.checker_report.report_ref,
            expected_environment_digest=result.environment_manifest.environment_digest,
        )


def test_lean_replay_evidence_fails_on_environment_digest_mismatch(
    tmp_path: Path,
) -> None:
    result = run_lean_direct_proof_fixture_flow(tmp_path)

    with pytest.raises(LeanReplayEvidenceError, match="environment digest mismatch"):
        verify_lean_replay_evidence(
            artifact_root=result.store.artifact_dir,
            checker_report_ref=result.checker_report.report_ref,
            expected_environment_digest="sha256:different_environment",
        )


def test_lean_replay_evidence_fails_when_report_body_is_missing_log_ref(
    tmp_path: Path,
) -> None:
    result = run_lean_direct_proof_fixture_flow(tmp_path)
    report_body = json.loads(result.store.read_bytes(result.checker_report.report_ref).decode("utf-8"))
    report_body["stdout_ref"] = None
    missing_log_report_ref = result.store.save_json(
        report_body,
        artifact_id="checker_report_missing_log_ref",
        artifact_type="LeanCheckerReport",
        artifact_schema_id="lean_proof.checker_report",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={"case": "missing_log_ref"},
        created_at="2026-06-29T00:00:00Z",
    )

    with pytest.raises(LeanReplayEvidenceError, match="missing checker log ref"):
        verify_lean_replay_evidence(
            artifact_root=result.store.artifact_dir,
            checker_report_ref=missing_log_report_ref,
            expected_environment_digest=result.environment_manifest.environment_digest,
        )
