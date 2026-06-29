"""Child proof checker flow for Lean split certificates."""

from __future__ import annotations

import json
from dataclasses import dataclass

from tokenshare.core.models import ArtifactRef, JsonObject
from tokenshare.plugins.lean_proof.checker import (
    LeanCheckerMode,
    LeanCheckerReport,
    LeanCheckerRequest,
    LeanCheckerStatus,
    check_lean_proof,
)
from tokenshare.plugins.lean_proof.environment import (
    LeanEnvironmentManifest,
    build_lean_environment_ref,
)
from tokenshare.plugins.lean_proof.models import LeanSplitCertificate, LeanTheoremPayload
from tokenshare.storage.artifacts import ArtifactStore


@dataclass(frozen=True, kw_only=True)
class LeanChildProofResult:
    child_logical_key: str
    accepted: bool
    merge_ready: bool
    context_digest: str | None
    child_payload_ref: ArtifactRef
    proof_candidate_ref: ArtifactRef
    checker_report: LeanCheckerReport | None
    failure_kind: str | None
    failure_summary: JsonObject | None


def check_lean_child_proof(
    *,
    child_logical_key: str,
    split_certificate: LeanSplitCertificate,
    child_payload_ref: ArtifactRef,
    proof_candidate_ref: ArtifactRef,
    artifact_store: ArtifactStore,
    environment_manifest: LeanEnvironmentManifest,
    request_id: str,
    created_at: str,
) -> LeanChildProofResult:
    """Check one child proof against certificate-bound child payload metadata."""

    child = _certificate_child(split_certificate, child_logical_key)
    if child is None:
        return _failed_without_checker(
            child_logical_key=child_logical_key,
            child_payload_ref=child_payload_ref,
            proof_candidate_ref=proof_candidate_ref,
            failure_kind="child_not_in_split_certificate",
            message="child_logical_key is not present in Lean split certificate",
        )

    payload = LeanTheoremPayload.from_dict(
        json.loads(artifact_store.read_bytes(child_payload_ref).decode("utf-8"))
    )
    proof_body = json.loads(artifact_store.read_bytes(proof_candidate_ref).decode("utf-8"))
    failure_kind = _payload_binding_failure(child=child, payload=payload, proof_body=proof_body)
    if failure_kind is not None:
        return _failed_without_checker(
            child_logical_key=child_logical_key,
            child_payload_ref=child_payload_ref,
            proof_candidate_ref=proof_candidate_ref,
            failure_kind=failure_kind,
            message="child theorem payload or proof candidate is not bound to split certificate",
            context_digest=child.get("context_digest"),
        )

    report = check_lean_proof(
        LeanCheckerRequest(
            request_id=request_id,
            theorem_payload_ref=child_payload_ref,
            proof_candidate_ref=proof_candidate_ref,
            environment_ref=build_lean_environment_ref(environment_manifest),
            checker_mode=LeanCheckerMode.CHILD_PROOF,
            timeout_seconds=int(payload.resource_limits["timeout_seconds"]),
            max_output_bytes=int(payload.resource_limits["max_output_bytes"]),
            created_at=created_at,
        ),
        artifact_store=artifact_store,
        environment_manifest=environment_manifest,
    )
    accepted = report.status == LeanCheckerStatus.ACCEPTED
    return LeanChildProofResult(
        child_logical_key=child_logical_key,
        accepted=accepted,
        merge_ready=accepted,
        context_digest=str(child["context_digest"]),
        child_payload_ref=child_payload_ref,
        proof_candidate_ref=proof_candidate_ref,
        checker_report=report,
        failure_kind=None if accepted else "lean_checker_rejected",
        failure_summary=None
        if accepted
        else {
            "failure_kind": "lean_checker_rejected",
            "message": "Lean checker rejected child proof",
            "checker_status": report.status.value,
            "evidence_refs": _report_evidence_refs(report),
        },
    )


def _certificate_child(
    certificate: LeanSplitCertificate,
    child_logical_key: str,
) -> JsonObject | None:
    for child in certificate.child_goals:
        if child.get("child_logical_key") == child_logical_key:
            return child
    return None


def _payload_binding_failure(
    *,
    child: JsonObject,
    payload: LeanTheoremPayload,
    proof_body: JsonObject,
) -> str | None:
    if payload.payload_digest != child["child_payload_digest"]:
        return "child_payload_digest_mismatch"
    library_context = payload.library_context
    if library_context.get("child_logical_key") != child["child_logical_key"]:
        return "child_context_mismatch"
    if proof_body.get("theorem_payload_digest") != payload.payload_digest:
        return "proof_candidate_payload_digest_mismatch"
    return None


def _failed_without_checker(
    *,
    child_logical_key: str,
    child_payload_ref: ArtifactRef,
    proof_candidate_ref: ArtifactRef,
    failure_kind: str,
    message: str,
    context_digest: str | None = None,
) -> LeanChildProofResult:
    return LeanChildProofResult(
        child_logical_key=child_logical_key,
        accepted=False,
        merge_ready=False,
        context_digest=context_digest,
        child_payload_ref=child_payload_ref,
        proof_candidate_ref=proof_candidate_ref,
        checker_report=None,
        failure_kind=failure_kind,
        failure_summary={
            "failure_kind": failure_kind,
            "message": message,
            "evidence_refs": [child_payload_ref.artifact_id, proof_candidate_ref.artifact_id],
        },
    )


def _report_evidence_refs(report: LeanCheckerReport) -> list[str]:
    refs = [
        report.stdout_ref,
        report.stderr_ref,
        report.generated_source_ref,
        report.report_ref,
        report.proof_artifact_ref,
    ]
    return [ref.artifact_id for ref in refs if ref is not None]
