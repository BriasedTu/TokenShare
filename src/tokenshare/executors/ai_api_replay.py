"""Replay guard helpers for Phase 7 AI API artifacts."""

from __future__ import annotations

from tokenshare.core.models import ArtifactRef
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
