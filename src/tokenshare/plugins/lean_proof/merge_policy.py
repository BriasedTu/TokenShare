"""Lean verified merge policy for child proof artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass

from tokenshare.core.expansion import MergePlan
from tokenshare.core.models import ArtifactRef, JsonObject
from tokenshare.plugins.lean_proof.checker import (
    LeanChecker,
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
    LEAN_LEMMA_GRAPH_STRUCTURED_BLOCKED_SHAPE,
    LeanLemmaGraphCertificate,
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


@dataclass(frozen=True, kw_only=True)
class LeanLemmaGraphProofInput:
    node_id: str
    slot_key: str
    node_payload_ref: ArtifactRef
    proof_candidate_ref: ArtifactRef
    checker_report: LeanCheckerReport
    context_digest: str
    theorem_payload_digest: str


@dataclass(frozen=True, kw_only=True)
class LeanLemmaGraphMergeResult:
    accepted: bool
    merge_result_ref: ArtifactRef | None
    root_checker_report: LeanCheckerReport
    root_proof_artifact_ref: ArtifactRef | None
    node_proof_refs: dict[str, ArtifactRef]
    root_proof_candidate_ref: ArtifactRef


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
    checker: LeanChecker = check_lean_proof,
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
    checker_report = checker(
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


def merge_lean_lemma_graph_proofs(
    *,
    merge_plan: MergePlan,
    lemma_graph_certificate: LeanLemmaGraphCertificate,
    parent_theorem_payload_ref: ArtifactRef,
    node_proofs: list[LeanLemmaGraphProofInput],
    artifact_store: ArtifactStore,
    environment_manifest: LeanEnvironmentManifest,
    merge_unit_id: str,
    request_id: str,
    created_at: str,
    checker: LeanChecker = check_lean_proof,
) -> LeanLemmaGraphMergeResult:
    """Assemble accepted lemma-DAG node proof artifacts and re-check the root."""

    _validate_merge_policy(merge_plan)
    if lemma_graph_certificate.proof_assembly_shape == LEAN_LEMMA_GRAPH_STRUCTURED_BLOCKED_SHAPE:
        raise ValueError(
            "structured_blocked_no_oracle_frontier_stress.v1 cannot enter "
            "checker-success Lean lemma graph merge"
        )
    if lemma_graph_certificate.environment_digest != environment_manifest.environment_digest:
        raise ValueError("Lean lemma graph certificate environment mismatch")

    required_slots = list(merge_plan.required_slots)
    _validate_lemma_graph_merge_plan_slots(
        required_slots=required_slots,
        certificate=lemma_graph_certificate,
    )
    node_proofs_by_slot = _lemma_graph_proofs_by_slot(node_proofs)
    _require_exact_lemma_graph_required_slots(required_slots, node_proofs_by_slot)
    ordered_inputs = [node_proofs_by_slot[str(slot["slot_key"])] for slot in required_slots]
    _validate_lemma_graph_node_inputs(
        required_slots=required_slots,
        certificate=lemma_graph_certificate,
        node_inputs=ordered_inputs,
        artifact_store=artifact_store,
        environment_manifest=environment_manifest,
    )

    parent_payload = LeanTheoremPayload.from_dict(
        json.loads(artifact_store.read_bytes(parent_theorem_payload_ref).decode("utf-8"))
    )
    node_proof_sources = _lemma_graph_proof_sources(ordered_inputs, artifact_store)
    node_statements = _lemma_graph_node_statements(lemma_graph_certificate)
    proof_source = _lemma_graph_merge_proof_source(
        lemma_graph_certificate,
        node_proof_sources=node_proof_sources,
        node_statements=node_statements,
    )
    proof_ref = artifact_store.save_json(
        {
            "schema_version": "lean_proof.proof_candidate.v1",
            "proof_candidate_id": f"proof_candidate:{_safe_id(request_id)}",
            "theorem_payload_digest": parent_payload.payload_digest,
            "proof_source": proof_source,
            "created_at": created_at,
        },
        artifact_id=f"{_safe_id(request_id)}_lemma_graph_merge_proof_candidate",
        artifact_type="LeanProofCandidate",
        artifact_schema_id="lean_proof.proof_candidate",
        artifact_schema_version="v1",
        source={"kind": "lean_lemma_graph_merge_policy", "request_id": request_id},
        metadata={
            "merge_unit_id": merge_unit_id,
            "certificate_id": lemma_graph_certificate.certificate_id,
            "root_node_id": lemma_graph_certificate.root_node_id,
        },
        created_at=created_at,
    )
    checker_report = checker(
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
    node_proof_refs = _lemma_graph_node_proof_refs(ordered_inputs)
    accepted = checker_report.status == LeanCheckerStatus.ACCEPTED
    merge_result_ref = None
    if accepted:
        body = _lemma_graph_merge_result_body(
            merge_unit_id=merge_unit_id,
            merge_plan=merge_plan,
            certificate=lemma_graph_certificate,
            node_inputs=ordered_inputs,
            node_proof_refs=node_proof_refs,
            root_checker_report=checker_report,
            root_proof_candidate_ref=proof_ref,
            created_at=created_at,
        )
        merge_result_ref = artifact_store.save_json(
            body,
            artifact_id=f"{_safe_id(request_id)}_lemma_graph_merge_result",
            artifact_type="LeanLemmaGraphMergeResult",
            artifact_schema_id="lean_proof.merge_result",
            artifact_schema_version="v1",
            source={"kind": "lean_lemma_graph_merge_policy", "request_id": request_id},
            metadata={
                "merge_unit_id": merge_unit_id,
                "root_node_id": lemma_graph_certificate.root_node_id,
                "output_name": PROOF_ARTIFACT_OUTPUT_NAME,
            },
            created_at=created_at,
        )
    return LeanLemmaGraphMergeResult(
        accepted=accepted,
        merge_result_ref=merge_result_ref,
        root_checker_report=checker_report,
        root_proof_artifact_ref=checker_report.proof_artifact_ref,
        node_proof_refs=node_proof_refs,
        root_proof_candidate_ref=proof_ref,
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


def _lemma_graph_proofs_by_slot(
    inputs: list[LeanLemmaGraphProofInput],
) -> dict[str, LeanLemmaGraphProofInput]:
    by_slot: dict[str, LeanLemmaGraphProofInput] = {}
    by_node: set[str] = set()
    for item in inputs:
        if not item.slot_key:
            raise ValueError("slot_key must be a non-empty string")
        if not item.node_id:
            raise ValueError("node_id must be a non-empty string")
        if item.slot_key in by_slot:
            raise ValueError(f"duplicate Lean lemma graph proof slot: {item.slot_key}")
        if item.node_id in by_node:
            raise ValueError(f"duplicate Lean lemma graph proof node: {item.node_id}")
        by_slot[item.slot_key] = item
        by_node.add(item.node_id)
    return by_slot


def _require_exact_lemma_graph_required_slots(
    required_slots: list[JsonObject],
    provided_by_slot: dict[str, LeanLemmaGraphProofInput],
) -> None:
    required = {str(slot["slot_key"]) for slot in required_slots}
    provided = set(provided_by_slot)
    unexpected = sorted(provided.difference(required))
    if unexpected:
        raise ValueError(
            "unexpected Lean lemma graph proof slots: " + ", ".join(unexpected)
        )
    missing = sorted(required.difference(provided))
    if missing:
        raise ValueError(
            "missing required Lean lemma graph proof slots: " + ", ".join(missing)
        )


def _validate_lemma_graph_merge_plan_slots(
    *,
    required_slots: list[JsonObject],
    certificate: LeanLemmaGraphCertificate,
) -> None:
    nodes_by_id = certificate.lemma_nodes_by_id
    expected_slot_keys = {
        f"{node_id}:{PROOF_ARTIFACT_OUTPUT_NAME}" for node_id in nodes_by_id
    }
    actual_slot_keys = {str(slot["slot_key"]) for slot in required_slots}
    if actual_slot_keys != expected_slot_keys:
        raise ValueError("Lean lemma graph merge plan required slots do not match certificate")
    for slot in required_slots:
        node_id = str(slot.get("source_child_logical_key", ""))
        node = nodes_by_id.get(node_id)
        if node is None:
            raise ValueError("Lean lemma graph merge plan references an unknown node")
        expected_slot_key = f"{node_id}:{PROOF_ARTIFACT_OUTPUT_NAME}"
        if slot.get("slot_key") != expected_slot_key:
            raise ValueError("Lean lemma graph merge plan slot_key mismatch")
        if slot.get("source_output_name") != PROOF_ARTIFACT_OUTPUT_NAME:
            raise ValueError("Lean lemma graph merge plan output name mismatch")
        metadata = slot.get("slot_metadata")
        if not isinstance(metadata, dict):
            raise ValueError("Lean lemma graph merge plan slot metadata is required")
        if metadata.get("node_id") != node_id:
            raise ValueError("Lean lemma graph merge plan node_id metadata mismatch")
        if metadata.get("root_node_id") != certificate.root_node_id:
            raise ValueError("Lean lemma graph merge plan root_node_id metadata mismatch")
        if metadata.get("context_digest") != node["context_digest"]:
            raise ValueError("Lean lemma graph merge plan context metadata mismatch")
        expected_payload_digest = LeanTheoremPayload.from_dict(
            dict(node["theorem_payload"])
        ).payload_digest
        if metadata.get("theorem_payload_digest") != expected_payload_digest:
            raise ValueError(
                "Lean lemma graph merge plan theorem payload metadata mismatch"
            )


def _validate_lemma_graph_node_inputs(
    *,
    required_slots: list[JsonObject],
    certificate: LeanLemmaGraphCertificate,
    node_inputs: list[LeanLemmaGraphProofInput],
    artifact_store: ArtifactStore,
    environment_manifest: LeanEnvironmentManifest,
) -> None:
    nodes_by_id = certificate.lemma_nodes_by_id
    expected_environment_digest = environment_manifest.environment_digest
    for slot, item in zip(required_slots, node_inputs, strict=True):
        node_id = str(slot["source_child_logical_key"])
        if item.node_id != node_id:
            raise ValueError("Lean lemma graph node_id mismatch")
        if item.slot_key != slot["slot_key"]:
            raise ValueError("Lean lemma graph slot_key mismatch")
        node = nodes_by_id[node_id]
        if item.context_digest != node["context_digest"]:
            raise ValueError("Lean lemma graph context digest mismatch")
        if not artifact_store.verify(item.node_payload_ref):
            raise ValueError("Lean lemma graph node payload artifact ref is missing")
        if not artifact_store.verify(item.proof_candidate_ref):
            raise ValueError("Lean lemma graph proof candidate artifact ref is missing")
        node_payload = LeanTheoremPayload.from_dict(
            json.loads(artifact_store.read_bytes(item.node_payload_ref).decode("utf-8"))
        )
        certificate_payload = LeanTheoremPayload.from_dict(dict(node["theorem_payload"]))
        if node_payload.payload_digest != certificate_payload.payload_digest:
            raise ValueError("Lean lemma graph node payload does not match certificate")
        if item.theorem_payload_digest != certificate_payload.payload_digest:
            raise ValueError("Lean lemma graph theorem payload digest mismatch")
        proof_candidate = _load_json_object(
            artifact_store.read_bytes(item.proof_candidate_ref).decode("utf-8"),
            artifact_name="Lean lemma graph proof candidate",
        )
        if proof_candidate.get("theorem_payload_digest") != certificate_payload.payload_digest:
            raise ValueError("Lean lemma graph proof candidate payload digest mismatch")
        report = item.checker_report
        if report is None or report.status != LeanCheckerStatus.ACCEPTED:
            raise ValueError("Lean lemma graph node proof missing accepted checker report")
        if (
            report.environment_ref.environment_digest != expected_environment_digest
            or report.environment_ref.environment_digest != certificate.environment_digest
        ):
            raise ValueError("Lean lemma graph node proof environment mismatch")
        if report.proof_artifact_ref is None:
            raise ValueError("Lean lemma graph node proof missing proof artifact")
        if not artifact_store.verify(report.proof_artifact_ref):
            raise ValueError("Lean lemma graph node proof artifact ref is missing")
        proof_source = artifact_store.read_bytes(report.proof_artifact_ref).decode("utf-8")
        expected_proof_digest = canonical_json_digest(
            {
                "theorem_payload_digest": certificate_payload.payload_digest,
                "proof_source": proof_source,
            }
        )
        if report.proof_digest != expected_proof_digest:
            raise ValueError("Lean lemma graph checker proof digest mismatch")


def _lemma_graph_node_statements(
    certificate: LeanLemmaGraphCertificate,
) -> dict[str, str]:
    return {
        str(node["node_id"]): str(node["theorem_payload"]["statement_source"])
        for node in certificate.lemma_nodes
    }


def _lemma_graph_proof_sources(
    inputs: list[LeanLemmaGraphProofInput],
    artifact_store: ArtifactStore,
) -> dict[str, str]:
    sources: dict[str, str] = {}
    for item in inputs:
        report = item.checker_report
        if report is None or report.proof_artifact_ref is None:
            raise ValueError("Lean lemma graph node proof missing proof artifact")
        sources[item.node_id] = artifact_store.read_bytes(report.proof_artifact_ref).decode(
            "utf-8"
        )
    return sources


def _lemma_graph_node_proof_refs(
    inputs: list[LeanLemmaGraphProofInput],
) -> dict[str, ArtifactRef]:
    refs: dict[str, ArtifactRef] = {}
    for item in inputs:
        report = item.checker_report
        if report is None or report.proof_artifact_ref is None:
            raise ValueError("Lean lemma graph node proof missing proof artifact")
        refs[item.node_id] = report.proof_artifact_ref
    return refs


def _lemma_graph_merge_proof_source(
    certificate: LeanLemmaGraphCertificate,
    *,
    node_proof_sources: dict[str, str],
    node_statements: dict[str, str],
) -> str:
    local_names = _lemma_graph_local_names(certificate)
    incoming_by_target = _lemma_graph_incoming_by_target(certificate)
    lines = ["by"]
    for node_id in _lemma_graph_topological_order(certificate):
        incoming_node_ids = tuple(incoming_by_target.get(node_id, ()))
        if incoming_node_ids:
            lines.extend(
                _have_from_lemma_graph_dependencies(
                    local_name=local_names[node_id],
                    node_id=node_id,
                    incoming_node_ids=incoming_node_ids,
                    local_names=local_names,
                    node_statements=node_statements,
                )
            )
        else:
            lines.extend(
                _have_from_lemma_graph_node(
                    local_name=local_names[node_id],
                    node_id=node_id,
                    node_proof_sources=node_proof_sources,
                    node_statements=node_statements,
                )
            )
    lines.append(f"  exact {local_names[certificate.root_node_id]}")
    return "\n".join(lines)


def _lemma_graph_incoming_by_target(
    certificate: LeanLemmaGraphCertificate,
) -> dict[str, list[str]]:
    node_ids = [str(node["node_id"]) for node in certificate.lemma_nodes]
    node_order_index = {node_id: index for index, node_id in enumerate(node_ids)}
    incoming_by_target: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for edge in certificate.dependency_edges:
        source_node_id = str(edge["source_node_id"])
        target_node_id = str(edge["target_node_id"])
        incoming_by_target.setdefault(target_node_id, []).append(source_node_id)
    for incoming in incoming_by_target.values():
        incoming.sort(key=lambda node_id: node_order_index.get(node_id, len(node_ids)))
    return incoming_by_target


def _lemma_graph_topological_order(
    certificate: LeanLemmaGraphCertificate,
) -> list[str]:
    node_order = [str(node["node_id"]) for node in certificate.lemma_nodes]
    node_order_index = {node_id: index for index, node_id in enumerate(node_order)}
    incoming_by_target: dict[str, list[str]] = {node_id: [] for node_id in node_order}
    for edge in certificate.dependency_edges:
        incoming_by_target[str(edge["target_node_id"])].append(str(edge["source_node_id"]))
    for incoming in incoming_by_target.values():
        incoming.sort(key=lambda node_id: node_order_index[node_id])

    visiting: set[str] = set()
    visited: set[str] = set()
    ordered: list[str] = []

    def visit(node_id: str) -> None:
        if node_id in visited:
            return
        if node_id in visiting:
            raise ValueError("Lean lemma graph dependency cycle detected during merge")
        visiting.add(node_id)
        for source_node_id in incoming_by_target.get(node_id, []):
            visit(source_node_id)
        visiting.remove(node_id)
        visited.add(node_id)
        ordered.append(node_id)

    visit(certificate.root_node_id)
    for node_id in node_order:
        visit(node_id)
    return ordered


def _lemma_graph_local_names(certificate: LeanLemmaGraphCertificate) -> dict[str, str]:
    names: dict[str, str] = {}
    used: set[str] = set()
    for node in certificate.lemma_nodes:
        node_id = str(node["node_id"])
        base = _lean_safe_local_name(node_id)
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        names[node_id] = candidate
        used.add(candidate)
    return names


def _lean_safe_local_name(node_id: str) -> str:
    component = "".join(
        character if character.isalnum() or character == "_" else "_"
        for character in node_id
    )
    if not component or component[0].isdigit():
        component = f"_{component}"
    return f"node_{component}"


def _have_from_lemma_graph_node(
    *,
    local_name: str,
    node_id: str,
    node_proof_sources: dict[str, str],
    node_statements: dict[str, str],
) -> list[str]:
    proof_source = node_proof_sources.get(node_id)
    statement_source = node_statements.get(node_id)
    if proof_source is None or statement_source is None:
        raise ValueError(f"missing Lean lemma graph node proof source for {node_id}")
    proof_lines = proof_source.splitlines() or [proof_source]
    first_line = proof_lines[0].strip()
    lines = [f"  have {local_name} : {statement_source} := {first_line}"]
    for line in proof_lines[1:]:
        lines.append(f"    {line.strip()}")
    return lines


def _have_from_lemma_graph_dependencies(
    *,
    local_name: str,
    node_id: str,
    incoming_node_ids: tuple[str, ...],
    local_names: dict[str, str],
    node_statements: dict[str, str],
) -> list[str]:
    statement_source = node_statements.get(node_id)
    if statement_source is None:
        raise ValueError(f"missing Lean lemma graph node statement for {node_id}")
    proof_body = _dependency_proof_body(
        target_node_id=node_id,
        target_statement=statement_source,
        incoming_node_ids=incoming_node_ids,
        local_names=local_names,
        node_statements=node_statements,
    )
    lines = [f"  have {local_name} : {statement_source} := by"]
    lines.extend(f"    {line}" for line in proof_body)
    return lines


def _dependency_proof_body(
    *,
    target_node_id: str,
    target_statement: str,
    incoming_node_ids: tuple[str, ...],
    local_names: dict[str, str],
    node_statements: dict[str, str],
) -> list[str]:
    incoming = [
        (
            node_id,
            local_names[node_id],
            node_statements.get(node_id),
        )
        for node_id in incoming_node_ids
    ]
    if any(statement is None for _, _, statement in incoming):
        raise ValueError(
            f"missing Lean lemma graph dependency statement for {target_node_id}"
        )
    typed_incoming = [
        (node_id, local_name, str(statement))
        for node_id, local_name, statement in incoming
    ]

    exact_match = _dependency_exact_statement(target_statement, typed_incoming)
    if exact_match is not None:
        return [f"exact {exact_match}"]

    simple_application = _dependency_simple_application(
        target_statement,
        typed_incoming,
    )
    if simple_application is not None:
        return [f"exact {simple_application}"]

    forall_application = _dependency_forall_application(
        target_statement,
        typed_incoming,
    )
    if forall_application is not None:
        return forall_application

    if len(typed_incoming) == 1:
        return [f"exact {typed_incoming[0][1]}"]

    raise ValueError(
        "cannot assemble dependency-aware Lean lemma graph proof for "
        f"{target_node_id}"
    )


def _dependency_exact_statement(
    target_statement: str,
    incoming: list[tuple[str, str, str]],
) -> str | None:
    for _, local_name, statement in incoming:
        if statement == target_statement:
            return local_name
    return None


def _dependency_simple_application(
    target_statement: str,
    incoming: list[tuple[str, str, str]],
) -> str | None:
    values_by_statement = {statement: local_name for _, local_name, statement in incoming}
    for _, function_local, statement in incoming:
        arrow = _split_arrow(statement)
        if arrow is None:
            continue
        antecedent, consequent = arrow
        if consequent != target_statement:
            continue
        value_local = values_by_statement.get(antecedent)
        if value_local is not None:
            return f"{function_local} {value_local}"
    return None


def _dependency_forall_application(
    target_statement: str,
    incoming: list[tuple[str, str, str]],
) -> list[str] | None:
    target_forall = _parse_forall_statement(target_statement)
    if target_forall is None:
        return None
    target_vars, target_body = target_forall
    target_arrow = _split_arrow(target_body)
    source_foralls: list[tuple[str, str, list[str], str]] = []
    for _, local_name, statement in incoming:
        parsed = _parse_forall_statement(statement)
        if parsed is None:
            continue
        vars_, body = parsed
        if vars_ == target_vars:
            source_foralls.append((local_name, statement, vars_, body))

    if target_arrow is not None:
        target_antecedent, target_consequent = target_arrow
        for first_local, _, _, first_body in source_foralls:
            first_arrow = _split_arrow(first_body)
            if first_arrow is None or first_arrow[0] != target_antecedent:
                continue
            middle = first_arrow[1]
            for second_local, _, _, second_body in source_foralls:
                second_arrow = _split_arrow(second_body)
                if second_arrow != (middle, target_consequent):
                    continue
                variable_args = " ".join(target_vars)
                hypothesis_name = _fresh_hypothesis_name(target_vars)
                return [
                    f"intro {variable_args} {hypothesis_name}",
                    (
                        f"exact {second_local} {variable_args} "
                        f"({first_local} {variable_args} {hypothesis_name})"
                    ),
                ]

    for function_local, _, _, function_body in source_foralls:
        function_arrow = _split_arrow(function_body)
        if function_arrow is None or function_arrow[1] != target_body:
            continue
        antecedent = function_arrow[0]
        for value_local, _, _, value_body in source_foralls:
            if value_body != antecedent:
                continue
            variable_args = " ".join(target_vars)
            return [
                f"intro {variable_args}",
                f"exact {function_local} {variable_args} ({value_local} {variable_args})",
            ]
    return None


def _parse_forall_statement(statement: str) -> tuple[list[str], str] | None:
    normalized = statement.strip()
    if not normalized.startswith("∀ "):
        return None
    if "," not in normalized:
        return None
    vars_part, body = normalized[2:].split(",", 1)
    variables = [item.strip() for item in vars_part.split() if item.strip()]
    if not variables:
        return None
    return variables, body.strip()


def _split_arrow(statement: str) -> tuple[str, str] | None:
    parts = statement.split(" -> ", 1)
    if len(parts) != 2:
        return None
    return parts[0].strip(), parts[1].strip()


def _fresh_hypothesis_name(variables: list[str]) -> str:
    used = set(variables)
    candidate = "h"
    suffix = 2
    while candidate in used:
        candidate = f"h{suffix}"
        suffix += 1
    return candidate


def _lemma_graph_merge_result_body(
    *,
    merge_unit_id: str,
    merge_plan: MergePlan,
    certificate: LeanLemmaGraphCertificate,
    node_inputs: list[LeanLemmaGraphProofInput],
    node_proof_refs: dict[str, ArtifactRef],
    root_checker_report: LeanCheckerReport,
    root_proof_candidate_ref: ArtifactRef,
    created_at: str,
) -> JsonObject:
    if root_checker_report.proof_artifact_ref is None or root_checker_report.report_ref is None:
        raise ValueError("accepted Lean lemma graph merge requires root checker refs")
    node_report_refs = {
        item.node_id: item.checker_report.report_ref.to_dict()
        for item in node_inputs
        if item.checker_report.report_ref is not None
    }
    node_digest_bundle = {
        node_id: {
            "proof_artifact_digest": ref.content_hash,
            "checker_report_digest": (
                item.checker_report.report_ref.content_hash
                if item.checker_report.report_ref is not None
                else None
            ),
            "proof_digest": item.checker_report.proof_digest,
        }
        for node_id, ref in sorted(node_proof_refs.items())
        for item in node_inputs
        if item.node_id == node_id
    }
    body = {
        "schema_version": LEAN_MERGE_RESULT_SCHEMA_VERSION,
        "merge_result_id": f"lean_lemma_graph_merge_result:{merge_unit_id}",
        "merge_unit_id": merge_unit_id,
        "merge_plan_id": merge_plan.merge_plan_header["merge_plan_id"],
        "merge_plan_digest": merge_plan.merge_plan_header["merge_plan_digest"],
        "lemma_graph_certificate_id": certificate.certificate_id,
        "lemma_graph_certificate_digest": certificate.certificate_digest,
        "metadata_summary": _lemma_graph_metadata_summary(certificate),
        "node_proof_refs": {
            node_id: ref.to_dict() for node_id, ref in sorted(node_proof_refs.items())
        },
        "node_checker_report_refs": node_report_refs,
        "node_proof_digest_bundle": canonical_json_digest(node_digest_bundle),
        "node_proof_digest_bundle_body": node_digest_bundle,
        "root_checker_report_ref": root_checker_report.report_ref.to_dict(),
        "root_proof_artifact_ref": root_checker_report.proof_artifact_ref.to_dict(),
        "root_proof_candidate_ref": root_proof_candidate_ref.to_dict(),
        "root_proof_digest": root_checker_report.proof_digest,
        "created_at": created_at,
    }
    body["merge_result_digest"] = canonical_json_digest(body)
    return body


def _lemma_graph_metadata_summary(certificate: LeanLemmaGraphCertificate) -> JsonObject:
    return {
        "topic_family": certificate.topic_family,
        "topic_family_version": certificate.topic_family_version,
        "construction_rule_id": certificate.construction_rule_id,
        "oracle_package_group": certificate.oracle_package_group,
        "proof_assembly_shape": certificate.proof_assembly_shape,
        "root_node_id": certificate.root_node_id,
        "environment_digest": certificate.environment_digest,
    }


def _load_json_object(text: str, *, artifact_name: str) -> JsonObject:
    try:
        body = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{artifact_name} must contain JSON") from exc
    if not isinstance(body, dict):
        raise ValueError(f"{artifact_name} must contain a JSON object")
    return body


def _safe_id(value: str) -> str:
    return "".join(character if character.isalnum() or character == "_" else "_" for character in value)
