"""Lean checker report validation bridge for Phase 4 verification."""

from __future__ import annotations

from dataclasses import dataclass

from tokenshare.core.models import ArtifactRef, JsonObject
from tokenshare.plugins.lean_proof.checker import LeanCheckerReport, LeanCheckerStatus


@dataclass(frozen=True, kw_only=True)
class LeanValidationResult:
    accepted: bool
    status: str
    layer_summary: JsonObject
    failure_summary: JsonObject | None

    def to_phase4_layer_summary(self) -> JsonObject:
        return dict(self.layer_summary)


def verify_lean_checker_report(report: LeanCheckerReport) -> LeanValidationResult:
    evidence_error = _evidence_error(report)
    if evidence_error is not None:
        return evidence_error
    if report.status == LeanCheckerStatus.ACCEPTED:
        return _accepted(report)
    if report.status == LeanCheckerStatus.REJECTED:
        return _rejected(
            "lean_checker_rejected",
            "Lean checker rejected proof artifact",
            evidence_refs=_evidence_ref_ids(report),
        )
    if report.status == LeanCheckerStatus.TIMEOUT:
        return _rejected(
            "lean_checker_timeout",
            "Lean checker timed out",
            evidence_refs=_evidence_ref_ids(report),
            failure_kind="executor_timeout",
        )
    return _rejected(
        "lean_checker_environment_error",
        "Lean checker environment or helper failed",
        evidence_refs=_evidence_ref_ids(report),
        failure_kind="executor_error",
    )


def _evidence_error(report: LeanCheckerReport) -> LeanValidationResult | None:
    if report.environment_ref is None:
        return _rejected(
            "missing_environment_ref",
            "Lean checker report is missing EnvironmentRef",
            evidence_refs=_evidence_ref_ids(report),
        )
    if report.stdout_ref is None or report.stderr_ref is None or report.report_ref is None:
        return _rejected(
            "missing_checker_logs",
            "Lean checker report is missing stdout, stderr, or report artifact",
            evidence_refs=_evidence_ref_ids(report),
        )
    if report.generated_source_ref is None:
        return _rejected(
            "missing_generated_source",
            "Lean checker report is missing generated Lean source artifact",
            evidence_refs=_evidence_ref_ids(report),
        )
    if report.status == LeanCheckerStatus.ACCEPTED and report.proof_artifact_ref is None:
        return _rejected(
            "missing_proof_artifact",
            "Accepted Lean checker report is missing proof artifact",
            evidence_refs=_evidence_ref_ids(report),
        )
    if report.status == LeanCheckerStatus.ACCEPTED and report.proof_digest is None:
        return _rejected(
            "missing_proof_digest",
            "Accepted Lean checker report is missing proof digest",
            evidence_refs=_evidence_ref_ids(report),
        )
    return None


def _accepted(report: LeanCheckerReport) -> LeanValidationResult:
    layer = _layer(
        "passed",
        "lean_checker_accepted",
        "Lean checker accepted proof artifact",
        details={
            "real_checker_evidence": True,
            "checker_status": report.status.value,
            "proof_digest": report.proof_digest,
            "environment_digest": report.environment_ref.environment_digest,
        },
        evidence_refs=_evidence_ref_ids(report),
    )
    return LeanValidationResult(
        accepted=True,
        status="passed",
        layer_summary=layer,
        failure_summary=None,
    )


def _rejected(
    reason_code: str,
    summary: str,
    *,
    evidence_refs: list[str],
    failure_kind: str = "invalid_output",
) -> LeanValidationResult:
    layer = _layer(
        "rejected",
        reason_code,
        summary,
        details={},
        evidence_refs=evidence_refs,
    )
    return LeanValidationResult(
        accepted=False,
        status="rejected",
        layer_summary=layer,
        failure_summary={
            "failure_kind": failure_kind,
            "failed_layer": "plugin_domain_check",
            "message": summary,
            "evidence_refs": evidence_refs,
        },
    )


def _layer(
    status: str,
    reason_code: str,
    summary: str,
    *,
    details: JsonObject,
    evidence_refs: list[str],
) -> JsonObject:
    return {
        "status": status,
        "reason_code": reason_code,
        "summary": summary,
        "details": details,
        "evidence_refs": evidence_refs,
        "checked_at": None,
    }


def _evidence_ref_ids(report: LeanCheckerReport) -> list[str]:
    refs: list[ArtifactRef | None] = [
        report.stdout_ref,
        report.stderr_ref,
        report.generated_source_ref,
        report.report_ref,
    ]
    if report.proof_artifact_ref is not None:
        refs.append(report.proof_artifact_ref)
    return [ref.artifact_id for ref in refs if ref is not None]
