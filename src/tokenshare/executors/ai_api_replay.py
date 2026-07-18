"""Replay guard helpers for Phase 7 AI API artifacts."""

from __future__ import annotations

import json

from tokenshare.core.models import ArtifactRef
from tokenshare.executors.ai_api_artifacts import (
    RawModelIdentityEvidence,
    read_raw_model_identity_evidence,
)
from tokenshare.executors.contracts import ExecutionSubmission
from tokenshare.storage.artifacts import ArtifactStore


def verify_ai_api_submission_artifacts(
    artifact_store: ArtifactStore,
    submission: ExecutionSubmission,
) -> bool:
    required_refs: list[ArtifactRef] = []
    if submission.raw_output_ref is not None:
        required_refs.append(submission.raw_output_ref)
    if submission.parsed_output_ref is not None:
        required_refs.append(submission.parsed_output_ref)
    if submission.parse_failure_ref is not None:
        required_refs.append(submission.parse_failure_ref)
    if submission.provenance_ref is not None:
        required_refs.append(submission.provenance_ref)
    if submission.result_kind in {"succeeded", "parse_failed"} and submission.raw_output_ref is None:
        raise FileNotFoundError("missing AI API artifact: raw_output_ref")
    if submission.provenance_ref is None:
        raise FileNotFoundError("missing AI API artifact: provenance_ref")
    for artifact_ref in required_refs:
        if not artifact_store.verify(artifact_ref):
            raise FileNotFoundError(f"missing AI API artifact: {artifact_ref.artifact_id}")
    return True


def read_ai_api_submission_model_identity(
    artifact_store: ArtifactStore,
    submission: ExecutionSubmission,
) -> RawModelIdentityEvidence | None:
    """不接触 config、secret、executor 或 transport，只读取持久化模型身份。"""

    verify_ai_api_submission_artifacts(artifact_store, submission)
    if submission.raw_output_ref is None:
        return None
    raw_ref = submission.raw_output_ref
    if (
        raw_ref.artifact_type != "RawModelOutput"
        or raw_ref.artifact_schema_id != "phase7.raw_model_output"
        or raw_ref.artifact_schema_version not in {"v1", "v2"}
    ):
        raise ValueError("invalid RawModelOutput artifact ref")
    raw_output = json.loads(
        artifact_store.read_bytes(raw_ref).decode("utf-8")
    )
    if not isinstance(raw_output, dict):
        raise ValueError("RawModelOutput must be a JSON object")
    evidence = read_raw_model_identity_evidence(raw_output)
    expected_ref_version = "v1" if evidence.schema_version.endswith(".v1") else "v2"
    if raw_ref.artifact_schema_version != expected_ref_version:
        raise ValueError("RawModelOutput artifact ref version does not match body schema")
    return evidence
