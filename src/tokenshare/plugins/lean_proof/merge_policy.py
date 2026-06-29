"""Lean verified merge policy for child proof artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass

from tokenshare.core.expansion import MergePlan
from tokenshare.core.models import ArtifactRef, JsonObject
from tokenshare.plugins.lean_proof.checker import (
    LeanCheckerMode,
    LeanCheckerReport,
    LeanCheckerRequest,
    LeanCheckerStatus,
    check_lean_proof,
)
from tokenshare.plugins.lean_proof.child_proof import LeanChildProofResult
from tokenshare.plugins.lean_proof.environment import (
    LeanEnvironmentManifest,
    build_lean_environment_ref,
)
from tokenshare.plugins.lean_proof.models import (
    LeanSplitCertificate,
    LeanTheoremPayload,
    canonical_json_digest,
)
from tokenshare.plugins.lean_proof.schemas import (
    LEAN_MERGE_RESULT_SCHEMA_VERSION,
    PROOF_ARTIFACT_OUTPUT_NAME,
    VERIFIED_MERGE_POLICY_ID,
)
from tokenshare.storage.artifacts import ArtifactStore


@dataclass(frozen=True, kw_only=True)
class LeanProofMergeInput:
    slot_key: str
    child_proof: LeanChildProofResult


@dataclass(frozen=True, kw_only=True)
class LeanProofMergeResult:
    accepted: bool
    merge_result_ref: ArtifactRef | None
    root_checker_report: LeanCheckerReport
    root_proof_artifact_ref: ArtifactRef | None
    merge_rule_id: str
    child_proof_refs: dict[str, ArtifactRef]
    merge_proof_candidate_ref: ArtifactRef


def merge_lean_child_proofs(
    *,
    merge_plan: MergePlan,
    split_certificate: LeanSplitCertificate,
    parent_theorem_payload_ref: ArtifactRef,
    child_proofs: list[LeanProofMergeInput],
    artifact_store: ArtifactStore,
    environment_manifest: LeanEnvironmentManifest,
    merge_unit_id: str,
    request_id: str,
    created_at: str,
) -> LeanProofMergeResult:
    """Build a root merge proof from child proof evidence and re-check it."""

    _validate_merge_policy(merge_plan)
    required_slots = list(merge_plan.required_slots)
    child_proofs_by_slot = _proofs_by_slot(child_proofs)
    _require_exact_required_slots(required_slots, child_proofs_by_slot)
    ordered_inputs = [child_proofs_by_slot[str(slot["slot_key"])] for slot in required_slots]
    _validate_child_inputs(
        required_slots=required_slots,
        split_certificate=split_certificate,
        child_inputs=ordered_inputs,
        environment_manifest=environment_manifest,
    )

    parent_payload = LeanTheoremPayload.from_dict(
        json.loads(artifact_store.read_bytes(parent_theorem_payload_ref).decode("utf-8"))
    )
    merge_rule_id = _merge_rule_id(split_certificate)
    child_proof_sources = _child_proof_sources(ordered_inputs, artifact_store)
    child_statements = _child_statements(split_certificate)
    proof_source = _merge_proof_source(
        merge_rule_id,
        child_proof_sources=child_proof_sources,
        child_statements=child_statements,
    )
    proof_ref = artifact_store.save_json(
        {
            "schema_version": "lean_proof.proof_candidate.v1",
            "proof_candidate_id": f"proof_candidate:{_safe_id(request_id)}",
            "theorem_payload_digest": parent_payload.payload_digest,
            "proof_source": proof_source,
            "created_at": created_at,
        },
        artifact_id=f"{_safe_id(request_id)}_merge_proof_candidate",
        artifact_type="LeanProofCandidate",
        artifact_schema_id="lean_proof.proof_candidate",
        artifact_schema_version="v1",
        source={"kind": "lean_merge_policy", "request_id": request_id},
        metadata={"merge_rule_id": merge_rule_id, "merge_unit_id": merge_unit_id},
        created_at=created_at,
    )
    checker_report = check_lean_proof(
        LeanCheckerRequest(
            request_id=request_id,
            theorem_payload_ref=parent_theorem_payload_ref,
            proof_candidate_ref=proof_ref,
            environment_ref=build_lean_environment_ref(environment_manifest),
            checker_mode=LeanCheckerMode.MERGE_PROOF,
            timeout_seconds=int(parent_payload.resource_limits["timeout_seconds"]),
            max_output_bytes=int(parent_payload.resource_limits["max_output_bytes"]),
            created_at=created_at,
        ),
        artifact_store=artifact_store,
        environment_manifest=environment_manifest,
    )
    child_proof_refs = _child_proof_refs(ordered_inputs)
    accepted = checker_report.status == LeanCheckerStatus.ACCEPTED
    merge_result_ref = None
    if accepted:
        body = _merge_result_body(
            merge_unit_id=merge_unit_id,
            merge_plan=merge_plan,
            split_certificate=split_certificate,
            merge_rule_id=merge_rule_id,
            child_proof_refs=child_proof_refs,
            root_checker_report=checker_report,
            created_at=created_at,
        )
        merge_result_ref = artifact_store.save_json(
            body,
            artifact_id=f"{_safe_id(request_id)}_merge_result",
            artifact_type="LeanMergeResult",
            artifact_schema_id="lean_proof.merge_result",
            artifact_schema_version="v1",
            source={"kind": "lean_merge_policy", "request_id": request_id},
            metadata={
                "merge_unit_id": merge_unit_id,
                "merge_rule_id": merge_rule_id,
                "output_name": PROOF_ARTIFACT_OUTPUT_NAME,
            },
            created_at=created_at,
        )
    return LeanProofMergeResult(
        accepted=accepted,
        merge_result_ref=merge_result_ref,
        root_checker_report=checker_report,
        root_proof_artifact_ref=checker_report.proof_artifact_ref,
        merge_rule_id=merge_rule_id,
        child_proof_refs=child_proof_refs,
        merge_proof_candidate_ref=proof_ref,
    )


def _validate_merge_policy(merge_plan: MergePlan) -> None:
    if merge_plan.merge_policy_ref.get("merge_policy_id") != VERIFIED_MERGE_POLICY_ID:
        raise ValueError("merge_plan must use Lean verified merge policy")


def _proofs_by_slot(inputs: list[LeanProofMergeInput]) -> dict[str, LeanProofMergeInput]:
    by_slot: dict[str, LeanProofMergeInput] = {}
    for item in inputs:
        if not item.slot_key:
            raise ValueError("slot_key must be a non-empty string")
        if item.slot_key in by_slot:
            raise ValueError(f"duplicate Lean proof slot: {item.slot_key}")
        by_slot[item.slot_key] = item
    return by_slot


def _require_exact_required_slots(
    required_slots: list[JsonObject],
    provided_by_slot: dict[str, LeanProofMergeInput],
) -> None:
    required = {str(slot["slot_key"]) for slot in required_slots}
    provided = set(provided_by_slot)
    missing = sorted(required.difference(provided))
    if missing:
        raise ValueError("missing required Lean proof slots: " + ", ".join(missing))
    unexpected = sorted(provided.difference(required))
    if unexpected:
        raise ValueError("unexpected Lean proof slots: " + ", ".join(unexpected))


def _validate_child_inputs(
    *,
    required_slots: list[JsonObject],
    split_certificate: LeanSplitCertificate,
    child_inputs: list[LeanProofMergeInput],
    environment_manifest: LeanEnvironmentManifest,
) -> None:
    certificate_children = {
        str(child["child_logical_key"]): child for child in split_certificate.child_goals
    }
    expected_environment_digest = environment_manifest.environment_digest
    for slot, item in zip(required_slots, child_inputs, strict=True):
        child_result = item.child_proof
        child_key = str(slot["source_child_logical_key"])
        if child_result.child_logical_key != child_key:
            raise ValueError("Lean child proof slot context mismatch")
        certificate_child = certificate_children.get(child_key)
        if certificate_child is None:
            raise ValueError("Lean child proof missing from split certificate")
        if child_result.context_digest != certificate_child["context_digest"]:
            raise ValueError("Lean child proof context mismatch")
        if not child_result.accepted or not child_result.merge_ready:
            raise ValueError("Lean child proof is not merge-ready")
        report = child_result.checker_report
        if report is None or report.status != LeanCheckerStatus.ACCEPTED:
            raise ValueError("Lean child proof is missing accepted checker report")
        if report.environment_ref.environment_digest != expected_environment_digest:
            raise ValueError("Lean child proof environment mismatch")
        if report.proof_artifact_ref is None:
            raise ValueError("Lean child proof missing proof artifact")


def _merge_rule_id(split_certificate: LeanSplitCertificate) -> str:
    skeleton = split_certificate.merge_skeleton or {}
    rule_id = skeleton.get("merge_rule_id")
    if not isinstance(rule_id, str) or not rule_id:
        raise ValueError("Lean split certificate missing merge_rule_id")
    return rule_id


def _merge_proof_source(
    merge_rule_id: str,
    *,
    child_proof_sources: dict[str, str],
    child_statements: dict[str, str],
) -> str:
    if merge_rule_id == "lean_merge.conjunction_intro.v1":
        return "\n".join(
            [
                "by",
                *_have_from_child(
                    local_name="child_left",
                    child_key="child:left",
                    child_proof_sources=child_proof_sources,
                    child_statements=child_statements,
                ),
                *_have_from_child(
                    local_name="child_right",
                    child_key="child:right",
                    child_proof_sources=child_proof_sources,
                    child_statements=child_statements,
                ),
                "  exact And.intro child_left child_right",
            ]
        )
    if merge_rule_id == "lean_merge.iff_intro.v1":
        return "\n".join(
            [
                "by",
                *_have_from_child(
                    local_name="child_forward",
                    child_key="child:forward",
                    child_proof_sources=child_proof_sources,
                    child_statements=child_statements,
                ),
                *_have_from_child(
                    local_name="child_backward",
                    child_key="child:backward",
                    child_proof_sources=child_proof_sources,
                    child_statements=child_statements,
                ),
                "  exact Iff.intro child_forward child_backward",
            ]
        )
    raise ValueError(f"unsupported Lean merge rule: {merge_rule_id}")


def _have_from_child(
    *,
    local_name: str,
    child_key: str,
    child_proof_sources: dict[str, str],
    child_statements: dict[str, str],
) -> list[str]:
    proof_source = child_proof_sources.get(child_key)
    statement_source = child_statements.get(child_key)
    if proof_source is None or statement_source is None:
        raise ValueError(f"missing Lean child proof source for {child_key}")
    proof_lines = proof_source.splitlines() or [proof_source]
    first_line = proof_lines[0].strip()
    lines = [f"  have {local_name} : {statement_source} := {first_line}"]
    for line in proof_lines[1:]:
        lines.append(f"    {line.strip()}")
    return lines


def _child_proof_sources(
    inputs: list[LeanProofMergeInput],
    artifact_store: ArtifactStore,
) -> dict[str, str]:
    sources: dict[str, str] = {}
    for item in inputs:
        report = item.child_proof.checker_report
        if report is None or report.proof_artifact_ref is None:
            raise ValueError("Lean child proof missing proof artifact")
        sources[item.child_proof.child_logical_key] = artifact_store.read_bytes(
            report.proof_artifact_ref
        ).decode("utf-8")
    return sources


def _child_statements(split_certificate: LeanSplitCertificate) -> dict[str, str]:
    return {
        str(child["child_logical_key"]): str(child["statement_source"])
        for child in split_certificate.child_goals
    }


def _child_proof_refs(inputs: list[LeanProofMergeInput]) -> dict[str, ArtifactRef]:
    refs: dict[str, ArtifactRef] = {}
    for item in inputs:
        report = item.child_proof.checker_report
        if report is None or report.proof_artifact_ref is None:
            raise ValueError("Lean child proof missing proof artifact")
        refs[item.child_proof.child_logical_key] = report.proof_artifact_ref
    return refs


def _merge_result_body(
    *,
    merge_unit_id: str,
    merge_plan: MergePlan,
    split_certificate: LeanSplitCertificate,
    merge_rule_id: str,
    child_proof_refs: dict[str, ArtifactRef],
    root_checker_report: LeanCheckerReport,
    created_at: str,
) -> JsonObject:
    if root_checker_report.proof_artifact_ref is None or root_checker_report.report_ref is None:
        raise ValueError("accepted Lean merge requires root proof and checker report refs")
    body = {
        "schema_version": LEAN_MERGE_RESULT_SCHEMA_VERSION,
        "merge_result_id": f"lean_merge_result:{merge_unit_id}",
        "merge_unit_id": merge_unit_id,
        "merge_plan_id": merge_plan.merge_plan_header["merge_plan_id"],
        "merge_plan_digest": merge_plan.merge_plan_header["merge_plan_digest"],
        "split_certificate_id": split_certificate.split_certificate_id,
        "split_certificate_digest": split_certificate.certificate_digest,
        "merge_rule_id": merge_rule_id,
        "child_proof_refs": {
            child_key: ref.to_dict() for child_key, ref in sorted(child_proof_refs.items())
        },
        "child_proof_digest_bundle": canonical_json_digest(
            {
                child_key: ref.content_hash
                for child_key, ref in sorted(child_proof_refs.items())
            }
        ),
        "root_checker_report_ref": root_checker_report.report_ref.to_dict(),
        "root_proof_artifact_ref": root_checker_report.proof_artifact_ref.to_dict(),
        "root_proof_digest": root_checker_report.proof_digest,
        "created_at": created_at,
    }
    body["merge_result_digest"] = canonical_json_digest(body)
    return body


def _safe_id(value: str) -> str:
    return "".join(character if character.isalnum() or character == "_" else "_" for character in value)
