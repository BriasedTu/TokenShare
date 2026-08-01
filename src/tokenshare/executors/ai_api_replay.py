"""Replay guard helpers for Phase 7 AI API artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from tokenshare.core.models import ArtifactRef
from tokenshare.executors.ai_api_artifacts import (
    RawModelIdentityEvidence,
    read_raw_model_identity_evidence,
)
from tokenshare.executors.contracts import ExecutionSubmission
from tokenshare.executors.response_bank import (
    CurrentTraceWrapper,
    ExternalBankObjectLocator,
    ResponseBankBlockedError,
    ResponseBankEntry,
    ResponseBankResolver,
)
from tokenshare.storage.artifacts import ArtifactStore


@dataclass(frozen=True, kw_only=True)
class ReplayedResponseBankTrace:
    entry: ResponseBankEntry
    attempt_ordinal: int
    locators_by_role: dict[str, ExternalBankObjectLocator]
    logical_started_at: str
    logical_finished_at: str
    source_latency_ms: int


def replay_response_bank_trace(
    *,
    external_bank_root: str | Path | None,
    wrapper: CurrentTraceWrapper,
) -> ReplayedResponseBankTrace:
    """仅从显式同 root capability 恢复 trace；不接触 provider/transport。"""

    if external_bank_root is None:
        raise ResponseBankBlockedError("replay requires explicit external bank root")
    try:
        resolver = ResponseBankResolver.open(external_bank_root)
    except (FileNotFoundError, ValueError, ResponseBankBlockedError) as exc:
        raise ResponseBankBlockedError(f"response bank root binding blocked: {exc}") from exc
    manifest = resolver.index.manifest
    if (
        wrapper.bank_root_id != manifest.bank_root_id
        or wrapper.manifest_digest != manifest.manifest_digest
        or wrapper.root_binding_marker_digest != manifest.root_binding_marker_digest
    ):
        raise ResponseBankBlockedError("response bank root binding does not match wrapper")
    try:
        entry = resolver.entry(wrapper.entry_id)
    except KeyError as exc:
        raise ResponseBankBlockedError(f"response bank entry missing: {wrapper.entry_id}") from exc
    if entry.inference_request_digest != wrapper.inference_request_digest:
        raise ResponseBankBlockedError("response bank inference request binding mismatch")
    locators_by_role = {locator.object_role: locator for locator in entry.object_locators}
    if wrapper.locator_digests != {
        role: locator.object_digest for role, locator in locators_by_role.items()
    }:
        raise ResponseBankBlockedError("response bank locator roles or digests missing")
    # 交付恢复结果前逐个流式读取，确保缺失或损坏对象不会进入 current run。
    for locator in entry.object_locators:
        try:
            resolver.read_verified(locator)
        except (ValueError, ResponseBankBlockedError) as exc:
            raise ResponseBankBlockedError(f"response bank object blocked: {exc}") from exc
    return ReplayedResponseBankTrace(
        entry=entry,
        attempt_ordinal=wrapper.attempt_ordinal,
        locators_by_role=locators_by_role,
        logical_started_at=wrapper.logical_started_at,
        logical_finished_at=wrapper.logical_finished_at,
        source_latency_ms=wrapper.source_latency_ms,
    )


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
