"""Phase 4 验证和 canonical selection 的纯规则。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Iterable

from tokenshare.core.models import ArtifactRef, JsonObject, _json_value


REQUIRED_VERIFICATION_LAYERS = (
    "schema_check",
    "artifact_integrity_check",
    "required_output_coverage_check",
    "evidence_reference_check",
    "plugin_domain_check",
    "audit_check",
)

ALLOWED_VERIFICATION_STATUSES = {"passed", "accepted", "rejected", "error"}
ALLOWED_VERIFICATION_LAYER_STATUSES = {"passed", "rejected", "error", "skipped"}


@dataclass(frozen=True)
class VerificationReport:
    """已校验的 Phase 4 verification report body。

    ``eligible_for_canonical`` 始终从 status 和 layer results 派生，
    调用方不能把失败报告强行标成可参与 canonical selection。
    """

    verification_report_id: str
    task_id: str
    unit_id: str
    attempt_id: str
    submission_id: str
    submission_event_seq: int
    candidate_output_bundle_digest: str
    candidate_output_refs: dict[str, ArtifactRef]
    required_output_names: list[str]
    output_contract_id: str
    validator_policy_id: str
    plugin_id: str
    plugin_version: str
    plugin_descriptor_digest: str
    status: str
    eligible_for_canonical: bool
    layer_results: JsonObject
    failure_summary: JsonObject | None
    verification_environment: JsonObject
    verifier: JsonObject
    started_at: str
    completed_at: str
    metadata: JsonObject | None = None
    schema_version: str = "phase4.verification_report.v1"

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version, "phase4.verification_report.v1")
        _validate_verification_report_shape(self.status, self.layer_results)
        derived = _derive_eligible_for_canonical(self.status, self.layer_results)
        if self.eligible_for_canonical and not derived:
            raise ValueError("eligible_for_canonical must be derived from passed layers")
        object.__setattr__(self, "eligible_for_canonical", derived)

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "verification_report_id": self.verification_report_id,
            "task_id": self.task_id,
            "unit_id": self.unit_id,
            "attempt_id": self.attempt_id,
            "submission_id": self.submission_id,
            "submission_event_seq": self.submission_event_seq,
            "candidate_output_bundle_digest": self.candidate_output_bundle_digest,
            "candidate_output_refs": _json_value(self.candidate_output_refs),
            "required_output_names": list(self.required_output_names),
            "output_contract_id": self.output_contract_id,
            "validator_policy_id": self.validator_policy_id,
            "plugin_id": self.plugin_id,
            "plugin_version": self.plugin_version,
            "plugin_descriptor_digest": self.plugin_descriptor_digest,
            "status": self.status,
            "eligible_for_canonical": self.eligible_for_canonical,
            "layer_results": _json_value(self.layer_results),
            "failure_summary": _json_value(self.failure_summary),
            "verification_environment": _json_value(self.verification_environment),
            "verifier": _json_value(self.verifier),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "metadata": _json_value(self.metadata or {}),
        }


@dataclass(frozen=True)
class CanonicalSelection:
    """first_verified_bundle canonical binding 的逻辑对象。"""

    canonical_selection_id: str
    task_id: str
    unit_id: str
    selection_policy: str
    selection_policy_version: str
    selected_verification_report_id: str
    selected_verification_event_seq: int
    selected_submission_id: str
    selected_submission_event_seq: int
    selected_attempt_id: str
    canonical_output_bundle_digest: str
    canonical_output_refs: dict[str, ArtifactRef]
    eligible_report_ids_considered: list[str]
    selection_reason: str
    bound_at: str
    metadata: JsonObject | None = None
    schema_version: str = "phase4.canonical_selection.v1"

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version, "phase4.canonical_selection.v1")

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "canonical_selection_id": self.canonical_selection_id,
            "task_id": self.task_id,
            "unit_id": self.unit_id,
            "selection_policy": self.selection_policy,
            "selection_policy_version": self.selection_policy_version,
            "selected_verification_report_id": self.selected_verification_report_id,
            "selected_verification_event_seq": self.selected_verification_event_seq,
            "selected_submission_id": self.selected_submission_id,
            "selected_submission_event_seq": self.selected_submission_event_seq,
            "selected_attempt_id": self.selected_attempt_id,
            "canonical_output_bundle_digest": self.canonical_output_bundle_digest,
            "canonical_output_refs": _json_value(self.canonical_output_refs),
            "eligible_report_ids_considered": list(self.eligible_report_ids_considered),
            "selection_reason": self.selection_reason,
            "bound_at": self.bound_at,
            "metadata": _json_value(self.metadata or {}),
        }


def build_verification_report(
    *,
    verification_report_id: str,
    task_id: str,
    unit_id: str,
    attempt_id: str,
    submission_id: str,
    submission_event_seq: int,
    candidate_output_refs: dict[str, ArtifactRef],
    required_output_names: list[str],
    output_contract_id: str,
    validator_policy_id: str,
    plugin_id: str,
    plugin_version: str,
    plugin_descriptor_digest: str,
    status: str,
    expected_artifact_hashes: dict[str, str],
    required_evidence_ref_ids: list[str],
    available_evidence_ref_ids: list[str],
    plugin_domain_status: str,
    audit_status: str,
    verification_environment: JsonObject,
    verifier: JsonObject,
    started_at: str,
    completed_at: str,
    metadata: JsonObject | None = None,
) -> VerificationReport:
    """从纯验证输入构造 report。

    该 helper 只表达 Phase 4 通用检查，不触碰 artifact storage、
    plugin runtime、protocol engine 或 ledger state。
    """

    layer_results = {
        "schema_check": _layer("passed", "schema_present", "candidate output structure checked"),
        "artifact_integrity_check": _artifact_integrity_layer(
            candidate_output_refs, expected_artifact_hashes
        ),
        "required_output_coverage_check": _required_outputs_layer(
            candidate_output_refs, required_output_names
        ),
        "evidence_reference_check": _evidence_layer(
            required_evidence_ref_ids, available_evidence_ref_ids
        ),
        "plugin_domain_check": _layer(
            plugin_domain_status,
            "plugin_domain_result",
            f"plugin domain check {plugin_domain_status}",
        ),
        "audit_check": _layer(audit_status, "audit_result", f"audit check {audit_status}"),
    }
    failure_summary = _first_failure_summary(layer_results, status)
    return VerificationReport(
        verification_report_id=verification_report_id,
        task_id=task_id,
        unit_id=unit_id,
        attempt_id=attempt_id,
        submission_id=submission_id,
        submission_event_seq=submission_event_seq,
        candidate_output_bundle_digest=digest_json(candidate_output_refs),
        candidate_output_refs=dict(candidate_output_refs),
        required_output_names=list(required_output_names),
        output_contract_id=output_contract_id,
        validator_policy_id=validator_policy_id,
        plugin_id=plugin_id,
        plugin_version=plugin_version,
        plugin_descriptor_digest=plugin_descriptor_digest,
        status=status,
        eligible_for_canonical=False,
        layer_results=layer_results,
        failure_summary=failure_summary,
        verification_environment=dict(verification_environment),
        verifier=dict(verifier),
        started_at=started_at,
        completed_at=completed_at,
        metadata=metadata or {},
    )


def select_first_verified_bundle(
    *,
    task_id: str,
    unit_id: str,
    verification_event_reports: Iterable[tuple[int, VerificationReport]],
    bound_at: str,
    selection_id: str | None = None,
    metadata: JsonObject | None = None,
) -> CanonicalSelection:
    """为一个 TaskUnit 选择最早落账的 eligible verification event。"""

    eligible = [
        (event_seq, report)
        for event_seq, report in verification_event_reports
        if report.task_id == task_id
        and report.unit_id == unit_id
        and report.eligible_for_canonical
        and report.status in {"passed", "accepted"}
    ]
    if not eligible:
        raise ValueError("no eligible verification reports")
    eligible.sort(key=lambda item: item[0])
    selected_event_seq, selected_report = eligible[0]
    return CanonicalSelection(
        canonical_selection_id=selection_id or f"canonical_selection:{task_id}:{unit_id}",
        task_id=task_id,
        unit_id=unit_id,
        selection_policy="first_verified_bundle",
        selection_policy_version="v1",
        selected_verification_report_id=selected_report.verification_report_id,
        selected_verification_event_seq=selected_event_seq,
        selected_submission_id=selected_report.submission_id,
        selected_submission_event_seq=selected_report.submission_event_seq,
        selected_attempt_id=selected_report.attempt_id,
        canonical_output_bundle_digest=selected_report.candidate_output_bundle_digest,
        canonical_output_refs=dict(selected_report.candidate_output_refs),
        eligible_report_ids_considered=[report.verification_report_id for _, report in eligible],
        selection_reason="earliest_eligible_verification_event_seq",
        bound_at=bound_at,
        metadata=metadata or {},
    )


def digest_json(data: Any) -> str:
    """为协议 JSON 形状数据返回稳定 sha256 digest。"""

    encoded = json.dumps(
        _json_value(data),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


def _derive_eligible_for_canonical(status: str, layer_results: JsonObject) -> bool:
    if status not in {"passed", "accepted"}:
        return False
    return all(
        layer_results.get(layer_name, {}).get("status") == "passed"
        for layer_name in REQUIRED_VERIFICATION_LAYERS
    )


def _validate_verification_report_shape(status: str, layer_results: JsonObject) -> None:
    if status not in ALLOWED_VERIFICATION_STATUSES:
        raise ValueError(f"invalid verification report status: {status}")
    for layer_name in REQUIRED_VERIFICATION_LAYERS:
        if layer_name not in layer_results:
            raise ValueError(f"verification layer missing required layer: {layer_name}")
        layer = layer_results[layer_name]
        if not isinstance(layer, dict):
            raise ValueError(f"verification layer must be an object: {layer_name}")
        layer_status = layer.get("status")
        if layer_status not in ALLOWED_VERIFICATION_LAYER_STATUSES:
            raise ValueError(
                f"invalid verification layer status for {layer_name}: {layer_status}"
            )


def _require_schema_version(actual: str, expected: str) -> None:
    if actual != expected:
        raise ValueError(f"invalid schema_version: expected {expected}, got {actual}")


def _layer(status: str, reason_code: str, summary: str) -> JsonObject:
    return {
        "status": status,
        "reason_code": reason_code,
        "summary": summary,
        "evidence_refs": [],
        "checked_at": None,
    }


def _required_outputs_layer(
    candidate_output_refs: dict[str, ArtifactRef], required_output_names: list[str]
) -> JsonObject:
    missing = [name for name in required_output_names if name not in candidate_output_refs]
    if missing:
        return {
            "status": "rejected",
            "reason_code": "missing_required_output",
            "summary": f"missing required outputs: {', '.join(missing)}",
            "evidence_refs": [],
            "checked_at": None,
        }
    return _layer("passed", "required_outputs_present", "required outputs present")


def _artifact_integrity_layer(
    candidate_output_refs: dict[str, ArtifactRef], expected_artifact_hashes: dict[str, str]
) -> JsonObject:
    for output_name, artifact_ref in candidate_output_refs.items():
        expected_hash = expected_artifact_hashes.get(output_name)
        if expected_hash is not None and expected_hash != artifact_ref.content_hash:
            return {
                "status": "rejected",
                "reason_code": "artifact_digest_mismatch",
                "summary": f"artifact digest mismatch for output {output_name}",
                "evidence_refs": [artifact_ref.artifact_id],
                "checked_at": None,
            }
    return _layer("passed", "artifact_digests_match", "artifact digests match expected values")


def _evidence_layer(
    required_evidence_ref_ids: list[str], available_evidence_ref_ids: list[str]
) -> JsonObject:
    available = set(available_evidence_ref_ids)
    missing = [ref_id for ref_id in required_evidence_ref_ids if ref_id not in available]
    if missing:
        return {
            "status": "rejected",
            "reason_code": "missing_evidence_ref",
            "summary": f"missing evidence refs: {', '.join(missing)}",
            "evidence_refs": missing,
            "checked_at": None,
        }
    return _layer("passed", "evidence_refs_present", "required evidence refs are present")


def _first_failure_summary(layer_results: JsonObject, status: str) -> JsonObject | None:
    if status == "error":
        return {
            "failure_kind": "verification_error",
            "failed_layer": None,
            "message": "verification status is error",
            "evidence_refs": [],
        }
    for layer_name in REQUIRED_VERIFICATION_LAYERS:
        layer = layer_results.get(layer_name, {})
        if layer.get("status") != "passed":
            return {
                "failure_kind": layer.get("reason_code", "verification_failed"),
                "failed_layer": layer_name,
                "message": layer.get("summary", "verification layer failed"),
                "evidence_refs": list(layer.get("evidence_refs", [])),
            }
    return None
