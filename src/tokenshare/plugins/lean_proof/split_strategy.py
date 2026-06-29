"""Lean split-helper subprocess bridge and Phase 4 proposal mapping."""

from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from time import monotonic
from typing import Any

from tokenshare.core.expansion import (
    DecompositionProposal,
    MergePlan,
    digest_decomposition_proposal_body,
    digest_merge_plan_body,
)
from tokenshare.core.models import ArtifactRef, JsonObject
from tokenshare.core.verification import digest_json
from tokenshare.executors.contracts import EnvironmentRef
from tokenshare.plugins.lean_proof.environment import LeanEnvironmentManifest
from tokenshare.plugins.lean_proof.models import (
    LeanSplitCertificate,
    LeanTheoremPayload,
    canonical_json_digest,
)
from tokenshare.plugins.lean_proof.schemas import (
    CHECKER_VALIDATOR_POLICY_ID,
    DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
    LEAN_CHILD_THEOREM_PAYLOAD_SCHEMA_VERSION,
    LEAN_PROOF_ARTIFACT_SCHEMA_VERSION,
    LEAN_SPLIT_CERTIFICATE_SCHEMA_VERSION,
    PLUGIN_ID,
    PLUGIN_VERSION,
    PROOF_ARTIFACT_CONTRACT_ID,
    PROOF_ARTIFACT_OUTPUT_NAME,
    VERIFIED_MERGE_POLICY_ID,
    schema_ref,
)
from tokenshare.storage.artifacts import ArtifactStore


class LeanSplitHelperStatus(str, Enum):
    SUCCEEDED = "succeeded"
    UNSUPPORTED = "unsupported"
    TIMEOUT = "timeout"
    ENVIRONMENT_ERROR = "environment_error"
    HELPER_ERROR = "helper_error"


_SUPPORTED_MERGE_RULE_IDS = {
    "lean_merge.conjunction_intro.v1",
    "lean_merge.iff_intro.v1",
}
_RULE_POLICY_NAMES = {
    "lean_split.conjunction_goal.v1": "conjunction",
    "lean_split.iff_goal.v1": "iff",
    "lean_split.implication_intro.v1": "intro",
    "lean_split.forall_intro.v1": "intro",
}


@dataclass(frozen=True)
class LeanSplitHelperRequest:
    request_id: str
    theorem_payload_ref: ArtifactRef
    environment_ref: EnvironmentRef
    timeout_seconds: int
    max_output_bytes: int
    created_at: str
    schema_version: str = "lean_proof.split_request.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "theorem_payload_ref": self.theorem_payload_ref.to_dict(),
            "environment_ref": self.environment_ref.to_dict(),
            "timeout_seconds": self.timeout_seconds,
            "max_output_bytes": self.max_output_bytes,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class LeanSplitHelperReport:
    report_id: str
    request_id: str
    status: LeanSplitHelperStatus
    exit_code: int | None
    generated_source_ref: ArtifactRef | None
    helper_stdout_ref: ArtifactRef | None
    helper_stderr_ref: ArtifactRef | None
    certificate_ref: ArtifactRef | None
    report_ref: ArtifactRef | None
    certificate: LeanSplitCertificate | None
    diagnostics: JsonObject
    environment_ref: EnvironmentRef
    command_summary: JsonObject
    duration_ms: int
    helper_stdout_excerpt: str
    helper_stderr_excerpt: str
    schema_version: str = "lean_proof.split_helper_report.v1"

    def to_dict(self, *, include_report_ref: bool = True) -> JsonObject:
        body = {
            "schema_version": self.schema_version,
            "report_id": self.report_id,
            "request_id": self.request_id,
            "status": self.status.value,
            "exit_code": self.exit_code,
            "generated_source_ref": _json_value(self.generated_source_ref),
            "helper_stdout_ref": _json_value(self.helper_stdout_ref),
            "helper_stderr_ref": _json_value(self.helper_stderr_ref),
            "certificate_ref": _json_value(self.certificate_ref),
            "certificate": _json_value(self.certificate),
            "diagnostics": _json_value(self.diagnostics),
            "environment_ref": self.environment_ref.to_dict(),
            "command_summary": _json_value(self.command_summary),
            "duration_ms": self.duration_ms,
            "helper_stdout_excerpt": self.helper_stdout_excerpt,
            "helper_stderr_excerpt": self.helper_stderr_excerpt,
        }
        if include_report_ref:
            body["report_ref"] = _json_value(self.report_ref)
        return body


@dataclass(frozen=True, kw_only=True)
class LeanSplitPlanResult:
    certificate: LeanSplitCertificate
    proposal: DecompositionProposal
    merge_plan: MergePlan
    child_payload_refs_by_logical_key: dict[str, ArtifactRef]
    child_unit_ids_by_logical_key: dict[str, str]

    def to_dict(self) -> JsonObject:
        return {
            "certificate": self.certificate.to_dict(),
            "proposal": self.proposal.to_dict(),
            "merge_plan": self.merge_plan.to_dict(),
            "child_payload_refs_by_logical_key": _json_value(
                self.child_payload_refs_by_logical_key
            ),
            "child_unit_ids_by_logical_key": dict(self.child_unit_ids_by_logical_key),
        }


def run_lean_split_helper(
    request: LeanSplitHelperRequest,
    *,
    artifact_store: ArtifactStore,
    environment_manifest: LeanEnvironmentManifest,
) -> LeanSplitHelperReport:
    _verify_environment_matches_request(request, environment_manifest)
    theorem_payload = LeanTheoremPayload.from_dict(
        json.loads(artifact_store.read_bytes(request.theorem_payload_ref).decode("utf-8"))
    )
    generated_source = _render_split_helper_source(request, theorem_payload)
    generated_source_ref = artifact_store.save_bytes(
        generated_source.encode("utf-8"),
        artifact_id=_artifact_id(request.request_id, "split_helper_source.lean"),
        artifact_type="LeanSplitHelperGeneratedSource",
        media_type="text/x-lean",
        artifact_schema_id="lean_proof.split_helper_source",
        artifact_schema_version="v1",
        source={"kind": "lean_split_helper", "request_id": request.request_id},
        metadata={"theorem_name": theorem_payload.theorem_name},
        created_at=request.created_at,
    )

    started = monotonic()
    stdout = ""
    stderr = ""
    exit_code: int | None = None
    raw_certificate_body: JsonObject | None = None
    status = LeanSplitHelperStatus.HELPER_ERROR
    command = [
        environment_manifest.lake_executable,
        "env",
        "lean",
        str(_temporary_source_path(request.request_id)),
    ]
    try:
        _build_helper_project(
            environment_manifest=environment_manifest,
            timeout_seconds=request.timeout_seconds,
            max_output_bytes=request.max_output_bytes,
        )
        with tempfile.TemporaryDirectory(prefix="tokenshare_lean_split_") as temp_dir:
            source_path = Path(temp_dir) / "TokenShareGeneratedSplit.lean"
            source_path.write_text(generated_source, encoding="utf-8")
            command[-1] = str(source_path)
            completed = subprocess.run(
                command,
                cwd=environment_manifest.project_root,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=request.timeout_seconds,
                env=_subprocess_env(environment_manifest),
                check=False,
            )
            exit_code = completed.returncode
            stdout = completed.stdout[: request.max_output_bytes]
            stderr = completed.stderr[: request.max_output_bytes]
            if completed.returncode == 0:
                raw_certificate_body = _extract_json_object(stdout)
                status = LeanSplitHelperStatus.SUCCEEDED
            else:
                status = LeanSplitHelperStatus.HELPER_ERROR
    except subprocess.TimeoutExpired as exc:
        stdout = _output_text(exc.stdout)[: request.max_output_bytes]
        stderr = _output_text(exc.stderr)[: request.max_output_bytes]
        status = LeanSplitHelperStatus.TIMEOUT
        exit_code = None

    duration_ms = int((monotonic() - started) * 1000)
    stdout_ref = _save_text_artifact(
        artifact_store,
        stdout,
        request=request,
        suffix="split_helper_stdout.txt",
        artifact_type="LeanSplitHelperStdout",
    )
    stderr_ref = _save_text_artifact(
        artifact_store,
        stderr,
        request=request,
        suffix="split_helper_stderr.txt",
        artifact_type="LeanSplitHelperStderr",
    )

    certificate = None
    certificate_ref = None
    raw_certificate_text = ""
    if raw_certificate_body is not None:
        enriched_body = _enrich_certificate_body(
            raw_certificate_body,
            request=request,
            theorem_payload=theorem_payload,
            helper_stdout_ref=stdout_ref,
            helper_stderr_ref=stderr_ref,
        )
        checked_body = _apply_bridge_checks_to_certificate_body(
            enriched_body,
            theorem_payload=theorem_payload,
            environment_manifest=environment_manifest,
            timeout_seconds=request.timeout_seconds,
            max_output_bytes=request.max_output_bytes,
        )
        certificate = LeanSplitCertificate.from_dict(checked_body)
        if status == LeanSplitHelperStatus.SUCCEEDED and certificate.split_kind == "unsupported":
            status = LeanSplitHelperStatus.UNSUPPORTED
        certificate_ref = artifact_store.save_json(
            certificate.to_dict(),
            artifact_id=_artifact_id(request.request_id, "split_certificate.json"),
            artifact_type="LeanSplitCertificate",
            artifact_schema_id="lean_proof.split_certificate",
            artifact_schema_version="v1",
            source={"kind": "lean_split_helper", "request_id": request.request_id},
            metadata={
                "split_kind": certificate.split_kind,
                "rule_id": certificate.rule_id,
            },
            created_at=request.created_at,
        )
        raw_certificate_text = json.dumps(
            certificate.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    report_without_ref = LeanSplitHelperReport(
        report_id=f"lean_split_helper_report:{_safe_id(request.request_id)}",
        request_id=request.request_id,
        status=status,
        exit_code=exit_code,
        generated_source_ref=generated_source_ref,
        helper_stdout_ref=stdout_ref,
        helper_stderr_ref=stderr_ref,
        certificate_ref=certificate_ref,
        report_ref=None,
        certificate=certificate,
        diagnostics={
            "stdout_excerpt": stdout[:4000],
            "stderr_excerpt": stderr[:4000],
            "combined_excerpt": (stdout + stderr)[:8000],
        },
        environment_ref=request.environment_ref,
        command_summary={
            "executable": environment_manifest.lake_executable,
            "args": ["env", "lean", "<generated_split_helper_source>"],
            "cwd": environment_manifest.project_root,
        },
        duration_ms=duration_ms,
        helper_stdout_excerpt=raw_certificate_text or stdout[:4000],
        helper_stderr_excerpt=stderr[:4000],
    )
    report_ref = artifact_store.save_json(
        report_without_ref.to_dict(include_report_ref=False),
        artifact_id=_artifact_id(request.request_id, "split_helper_report.json"),
        artifact_type="LeanSplitHelperReport",
        artifact_schema_id="lean_proof.split_helper_report",
        artifact_schema_version="v1",
        source={"kind": "lean_split_helper", "request_id": request.request_id},
        metadata={"status": status.value},
        created_at=request.created_at,
    )
    return LeanSplitHelperReport(
        **{
            **report_without_ref.__dict__,
            "report_ref": report_ref,
        }
    )


def build_lean_split_plan(
    *,
    split_report: LeanSplitHelperReport,
    artifact_store: ArtifactStore,
    task_id: str,
    parent_unit_id: str,
    canonical_selection_id: str,
    canonical_output_bundle_digest: str,
    plugin_descriptor_digest: str,
    expansion_scope_hash: str,
    expansion_decision_id: str,
    created_at: str,
    executor_decomposition_authority_ref: ArtifactRef | None = None,
) -> LeanSplitPlanResult:
    if executor_decomposition_authority_ref is not None:
        raise ValueError("AI output cannot define Lean decomposition")
    if split_report.certificate is None or split_report.certificate_ref is None:
        raise ValueError("Lean split plan requires a split certificate artifact")
    certificate = split_report.certificate
    if certificate.split_kind == "unsupported":
        raise ValueError("unsupported Lean split certificate cannot create a split plan")
    if certificate.split_kind not in {"single_child", "all_required_children"}:
        raise ValueError("Lean split plan requires child goals")
    if certificate.parent_theorem_payload_ref is None:
        raise ValueError("Lean split certificate requires parent theorem payload ref")

    parent_payload = LeanTheoremPayload.from_dict(
        json.loads(
            artifact_store.read_bytes(certificate.parent_theorem_payload_ref).decode("utf-8")
        )
    )
    _validate_certificate_against_parent_policy(certificate, parent_payload)
    child_payload_refs = _save_child_payloads(
        certificate=certificate,
        parent_payload=parent_payload,
        artifact_store=artifact_store,
        split_request_id=split_report.request_id,
        created_at=created_at,
    )

    proposal = _build_proposal(
        certificate=certificate,
        certificate_ref=split_report.certificate_ref,
        child_payload_refs=child_payload_refs,
        task_id=task_id,
        parent_unit_id=parent_unit_id,
        canonical_selection_id=canonical_selection_id,
        canonical_output_bundle_digest=canonical_output_bundle_digest,
        plugin_descriptor_digest=plugin_descriptor_digest,
        expansion_scope_hash=expansion_scope_hash,
        created_at=created_at,
        proposal_id="lean_decomposition_proposal_pending",
        proposal_digest="sha256:pending_proposal_digest",
    )
    proposal_digest = digest_decomposition_proposal_body(proposal)
    proposal_id = f"lean_decomposition_proposal_{proposal_digest.removeprefix('sha256:')}"
    proposal = _build_proposal(
        certificate=certificate,
        certificate_ref=split_report.certificate_ref,
        child_payload_refs=child_payload_refs,
        task_id=task_id,
        parent_unit_id=parent_unit_id,
        canonical_selection_id=canonical_selection_id,
        canonical_output_bundle_digest=canonical_output_bundle_digest,
        plugin_descriptor_digest=plugin_descriptor_digest,
        expansion_scope_hash=expansion_scope_hash,
        created_at=created_at,
        proposal_id=proposal_id,
        proposal_digest=proposal_digest,
    )

    child_unit_ids = {
        child["child_logical_key"]: _derive_phase4_child_unit_id(
            proposal_digest=proposal_digest,
            parent_unit_id=parent_unit_id,
            child_logical_key=child["child_logical_key"],
        )
        for child in certificate.child_goals
    }
    merge_plan = _build_merge_plan(
        certificate=certificate,
        task_id=task_id,
        parent_unit_id=parent_unit_id,
        canonical_selection_id=canonical_selection_id,
        plugin_descriptor_digest=plugin_descriptor_digest,
        expansion_decision_id=expansion_decision_id,
        proposal_id=proposal_id,
        child_unit_ids_by_logical_key=child_unit_ids,
        created_at=created_at,
        merge_plan_id="lean_merge_plan_pending",
        merge_plan_digest="sha256:pending_merge_plan_digest",
    )
    merge_plan_digest = digest_merge_plan_body(merge_plan)
    merge_plan_id = f"lean_merge_plan_{merge_plan_digest.removeprefix('sha256:')}"
    merge_plan = _build_merge_plan(
        certificate=certificate,
        task_id=task_id,
        parent_unit_id=parent_unit_id,
        canonical_selection_id=canonical_selection_id,
        plugin_descriptor_digest=plugin_descriptor_digest,
        expansion_decision_id=expansion_decision_id,
        proposal_id=proposal_id,
        child_unit_ids_by_logical_key=child_unit_ids,
        created_at=created_at,
        merge_plan_id=merge_plan_id,
        merge_plan_digest=merge_plan_digest,
    )

    return LeanSplitPlanResult(
        certificate=certificate,
        proposal=proposal,
        merge_plan=merge_plan,
        child_payload_refs_by_logical_key=child_payload_refs,
        child_unit_ids_by_logical_key=child_unit_ids,
    )


def _render_split_helper_source(
    request: LeanSplitHelperRequest,
    theorem_payload: LeanTheoremPayload,
) -> str:
    return "\n".join(
        [
            "import TokenShare.Helper",
            "",
            "#eval IO.println (TokenShare.splitCertificateJson",
            f"  {_lean_string_literal(request.request_id)}",
            f"  {_lean_string_literal(theorem_payload.payload_digest or '')}",
            f"  {_lean_string_literal(theorem_payload.theorem_name)}",
            f"  {_lean_string_literal(theorem_payload.parameters_source)}",
            f"  {_lean_string_literal(theorem_payload.statement_source)})",
            "",
        ]
    )


def _enrich_certificate_body(
    raw_body: JsonObject,
    *,
    request: LeanSplitHelperRequest,
    theorem_payload: LeanTheoremPayload,
    helper_stdout_ref: ArtifactRef,
    helper_stderr_ref: ArtifactRef,
) -> JsonObject:
    body = dict(raw_body)
    body["parent_theorem_payload_ref"] = request.theorem_payload_ref.to_dict()
    body["helper_stdout_ref"] = helper_stdout_ref.to_dict()
    body["helper_stderr_ref"] = helper_stderr_ref.to_dict()
    body["normalized_parent_goal_digest"] = canonical_json_digest(
        {
            "theorem_payload_digest": theorem_payload.payload_digest,
            "statement_source": theorem_payload.statement_source,
            "parameters_source": theorem_payload.parameters_source,
        }
    )
    child_goals = []
    for child in body.get("child_goals", []):
        child_body = dict(child)
        child_body["child_payload_digest"] = _child_payload(
            theorem_payload,
            child_body,
        ).payload_digest
        child_goals.append(child_body)
    body["child_goals"] = child_goals
    return body


def _apply_bridge_checks_to_certificate_body(
    body: JsonObject,
    *,
    theorem_payload: LeanTheoremPayload,
    environment_manifest: LeanEnvironmentManifest,
    timeout_seconds: int,
    max_output_bytes: int,
) -> JsonObject:
    if body.get("split_kind") == "unsupported":
        return body

    policy_failure = _policy_failure_reason(body, theorem_payload)
    if policy_failure is not None:
        return _unsupported_certificate_body(body, reason=policy_failure)

    merge_skeleton = body.get("merge_skeleton")
    merge_rule_id = (
        merge_skeleton.get("merge_rule_id") if isinstance(merge_skeleton, dict) else None
    )
    if merge_rule_id not in _SUPPORTED_MERGE_RULE_IDS:
        return _unsupported_certificate_body(body, reason="unsupported_merge_rule")

    elaboration_failure = _parent_goal_elaboration_failure(
        theorem_payload,
        environment_manifest=environment_manifest,
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
    )
    if elaboration_failure is not None:
        return _unsupported_certificate_body(
            body,
            reason="parent_goal_elaboration_failed",
            diagnostics={"elaboration_failure": elaboration_failure},
        )

    return body


def _unsupported_certificate_body(
    body: JsonObject,
    *,
    reason: str,
    diagnostics: JsonObject | None = None,
) -> JsonObject:
    existing_diagnostics = dict(body.get("diagnostics", {}))
    existing_diagnostics.update(diagnostics or {})
    return {
        **body,
        "rule_id": "lean_split.unsupported.v1",
        "rule_trace": [
            {"rule_id": "lean_split.unsupported.v1", "unsupported_reason": reason}
        ],
        "split_kind": "unsupported",
        "child_goals": [],
        "merge_skeleton": None,
        "unsupported_reason": reason,
        "diagnostics": existing_diagnostics,
    }


def _policy_failure_reason(
    certificate_body: JsonObject,
    theorem_payload: LeanTheoremPayload,
) -> str | None:
    policy = theorem_payload.decomposition_policy
    allowed_rules = set(str(item) for item in policy.get("allowed_rules", []))
    rule_name = _rule_policy_name(str(certificate_body.get("rule_id", "")))
    if rule_name is None:
        return "unsupported_rule"
    if rule_name not in allowed_rules:
        return "rule_disallowed_by_policy"
    if int(policy.get("max_depth", 0)) < 1:
        return "max_depth_exceeded"
    child_count = len(certificate_body.get("child_goals", []))
    if child_count > int(policy.get("max_children", 0)):
        return "max_children_exceeded"
    return None


def _validate_certificate_against_parent_policy(
    certificate: LeanSplitCertificate,
    parent_payload: LeanTheoremPayload,
) -> None:
    policy = parent_payload.decomposition_policy
    rule_name = _rule_policy_name(certificate.rule_id)
    if rule_name is None:
        raise ValueError("Lean split certificate uses unsupported split rule")
    allowed_rules = set(str(item) for item in policy.get("allowed_rules", []))
    if rule_name not in allowed_rules:
        raise ValueError("Lean split certificate rule disallowed by parent policy")
    if int(policy.get("max_depth", 0)) < 1:
        raise ValueError("Lean split certificate exceeds max_depth")
    if len(certificate.child_goals) > int(policy.get("max_children", 0)):
        raise ValueError("Lean split certificate exceeds max_children")
    merge_skeleton = certificate.merge_skeleton or {}
    if merge_skeleton.get("merge_rule_id") not in _SUPPORTED_MERGE_RULE_IDS:
        raise ValueError("Lean split certificate uses unsupported merge rule")


def _rule_policy_name(rule_id: str) -> str | None:
    return _RULE_POLICY_NAMES.get(rule_id)


def _parent_goal_elaboration_failure(
    theorem_payload: LeanTheoremPayload,
    *,
    environment_manifest: LeanEnvironmentManifest,
    timeout_seconds: int,
    max_output_bytes: int,
) -> JsonObject | None:
    source = _render_parent_elaboration_source(theorem_payload)
    with tempfile.TemporaryDirectory(prefix="tokenshare_lean_elab_") as temp_dir:
        source_path = Path(temp_dir) / "TokenShareGeneratedElaboration.lean"
        source_path.write_text(source, encoding="utf-8")
        try:
            completed = subprocess.run(
                [environment_manifest.lake_executable, "env", "lean", str(source_path)],
                cwd=environment_manifest.project_root,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=timeout_seconds,
                env=_subprocess_env(environment_manifest),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "exit_code": None,
                "stdout_excerpt": _output_text(exc.stdout)[:max_output_bytes],
                "stderr_excerpt": _output_text(exc.stderr)[:max_output_bytes],
                "timed_out": True,
            }
    if completed.returncode == 0:
        return None
    return {
        "exit_code": completed.returncode,
        "stdout_excerpt": completed.stdout[:max_output_bytes],
        "stderr_excerpt": completed.stderr[:max_output_bytes],
        "timed_out": False,
    }


def _render_parent_elaboration_source(payload: LeanTheoremPayload) -> str:
    lines: list[str] = [f"import {item}" for item in payload.imports]
    for key, value in sorted(payload.options.items()):
        if isinstance(value, bool):
            lean_value = "true" if value else "false"
        else:
            lean_value = str(value)
        lines.append(f"set_option {key} {lean_value}")
    if payload.namespace:
        lines.append(f"namespace {payload.namespace}")
    for namespace in payload.open_namespaces:
        lines.append(f"open {namespace}")
    parameters = f" {payload.parameters_source}" if payload.parameters_source else ""
    lines.append(
        f"axiom __tokenshare_split_parent_elaboration{parameters} : "
        f"{payload.statement_source}"
    )
    if payload.namespace:
        lines.append(f"end {payload.namespace}")
    lines.append("")
    return "\n".join(lines)


def _save_child_payloads(
    *,
    certificate: LeanSplitCertificate,
    parent_payload: LeanTheoremPayload,
    artifact_store: ArtifactStore,
    split_request_id: str,
    created_at: str,
) -> dict[str, ArtifactRef]:
    refs: dict[str, ArtifactRef] = {}
    for child in certificate.child_goals:
        child_payload = _child_payload(parent_payload, child)
        if child_payload.payload_digest != child["child_payload_digest"]:
            raise ValueError("child payload digest mismatch")
        child_key = child["child_logical_key"]
        refs[child_key] = artifact_store.save_json(
            child_payload.to_dict(),
            artifact_id=_artifact_id(split_request_id, f"child_{child_key}.json"),
            artifact_type="LeanChildTheoremPayload",
            artifact_schema_id="lean_proof.child_theorem_payload",
            artifact_schema_version="v1",
            source={"kind": "lean_split_helper", "request_id": split_request_id},
            metadata={
                "child_logical_key": child_key,
                "context_digest": child["context_digest"],
            },
            created_at=created_at,
        )
    return refs


def _child_payload(parent_payload: LeanTheoremPayload, child: JsonObject) -> LeanTheoremPayload:
    return LeanTheoremPayload(
        theorem_id=f"{parent_payload.theorem_id}:{child['child_logical_key']}",
        theorem_name=child["theorem_name"],
        imports=list(parent_payload.imports),
        namespace=parent_payload.namespace,
        open_namespaces=list(parent_payload.open_namespaces),
        options=dict(parent_payload.options),
        parameters_source=child["parameters_source"],
        statement_source=child["statement_source"],
        theorem_source=None,
        proof_candidate_ref=None,
        library_context={
            **dict(parent_payload.library_context),
            "parent_theorem_id": parent_payload.theorem_id,
            "parent_payload_digest": parent_payload.payload_digest,
            "child_logical_key": child["child_logical_key"],
            "schema_version": LEAN_CHILD_THEOREM_PAYLOAD_SCHEMA_VERSION,
        },
        decomposition_policy=dict(parent_payload.decomposition_policy),
        resource_limits=dict(parent_payload.resource_limits),
    )


def _build_proposal(
    *,
    certificate: LeanSplitCertificate,
    certificate_ref: ArtifactRef,
    child_payload_refs: dict[str, ArtifactRef],
    task_id: str,
    parent_unit_id: str,
    canonical_selection_id: str,
    canonical_output_bundle_digest: str,
    plugin_descriptor_digest: str,
    expansion_scope_hash: str,
    created_at: str,
    proposal_id: str,
    proposal_digest: str,
) -> DecompositionProposal:
    merge_slots = [_proposal_merge_slot(child) for child in certificate.child_goals]
    return DecompositionProposal(
        proposal_header={
            "proposal_id": proposal_id,
            "proposal_schema_version": "phase4.decomposition_proposal.v1",
            "task_id": task_id,
            "parent_unit_id": parent_unit_id,
            "canonical_selection_id": canonical_selection_id,
            "canonical_output_bundle_digest": canonical_output_bundle_digest,
            "plugin_id": PLUGIN_ID,
            "plugin_version": PLUGIN_VERSION,
            "plugin_descriptor_digest": plugin_descriptor_digest,
            "split_strategy_id": DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
            "split_strategy_params_digest": certificate.certificate_digest,
            "expansion_scope_hash": expansion_scope_hash,
            "proposal_digest": proposal_digest,
            "created_at": created_at,
        },
        child_specs=[
            _child_spec(child, child_payload_refs[child["child_logical_key"]])
            for child in certificate.child_goals
        ],
        dependency_edges=[],
        expected_outputs=[
            {
                "output_name": PROOF_ARTIFACT_OUTPUT_NAME,
                "schema_ref": schema_ref(LEAN_PROOF_ARTIFACT_SCHEMA_VERSION),
                "resolution_kind": "merge_plan_output",
                "child_key": None,
                "child_output_name": None,
                "merge_slot_id": merge_slots[0]["slot_id"],
                "merge_slot_policy": "all_required_slots",
                "merge_slot_count": len(merge_slots),
                "merge_slot_keys": [slot["slot_id"] for slot in merge_slots],
                "required": True,
            }
        ],
        merge_slots=merge_slots,
        promotion_guard_evidence={
            "typed_io_checked": True,
            "independently_schedulable_checked": True,
            "validator_policy_checked": True,
            "output_contract_checked": True,
            "no_freeform_thought_checked": True,
            "max_depth_checked": True,
            "max_children_checked": True,
            "lean_split_certificate_ref": certificate_ref.to_dict(),
            "lean_split_certificate_digest": certificate.certificate_digest,
            "lean_rule_id": certificate.rule_id,
        },
    )


def _child_spec(child: JsonObject, child_payload_ref: ArtifactRef) -> JsonObject:
    return {
        "child_logical_key": child["child_logical_key"],
        "unit_type": "lean_proof_subgoal",
        "input_bindings": {
            "child_theorem_payload": {
                "kind": "artifact_ref",
                "artifact_ref": child_payload_ref.to_dict(),
                "body_digest": child["child_payload_digest"],
                "context_digest": child["context_digest"],
            }
        },
        "required_outputs": [PROOF_ARTIFACT_OUTPUT_NAME],
        "output_contract_refs": {
            PROOF_ARTIFACT_OUTPUT_NAME: {
                "output_contract_id": PROOF_ARTIFACT_CONTRACT_ID,
                "schema_ref": schema_ref(LEAN_PROOF_ARTIFACT_SCHEMA_VERSION),
            }
        },
        "validator_policy_id": CHECKER_VALIDATOR_POLICY_ID,
        "budget_limit": None,
        "deadline": None,
        "weight": 1.0,
        "required_capabilities": {
            "executor": "deterministic_local_lean_checker",
            "lean_checker": True,
        },
        "plugin_payload": {
            "schema_version": "lean_proof.subgoal_plugin_payload.v1",
            "summary": {
                "child_logical_key": child["child_logical_key"],
                "context_digest": child["context_digest"],
                "child_payload_digest": child["child_payload_digest"],
                "source_rule_id": child.get("source_rule_id"),
            },
            "validation_requirements": {
                "checker_required": True,
                "environment_ref_required": True,
                "context_digest_required": True,
                "split_certificate_child_required": True,
            },
        },
        "promotion_guard_ref": None,
    }


def _proposal_merge_slot(child: JsonObject) -> JsonObject:
    slot_key = _slot_key(child)
    return {
        "slot_id": slot_key,
        "child_key": child["child_logical_key"],
        "child_output_name": PROOF_ARTIFACT_OUTPUT_NAME,
        "schema_ref": schema_ref(LEAN_PROOF_ARTIFACT_SCHEMA_VERSION),
        "required": True,
        "missing_policy": "block_merge",
    }


def _build_merge_plan(
    *,
    certificate: LeanSplitCertificate,
    task_id: str,
    parent_unit_id: str,
    canonical_selection_id: str,
    plugin_descriptor_digest: str,
    expansion_decision_id: str,
    proposal_id: str,
    child_unit_ids_by_logical_key: dict[str, str],
    created_at: str,
    merge_plan_id: str,
    merge_plan_digest: str,
) -> MergePlan:
    required_slots = [
        _required_slot(
            child=child,
            child_unit_id=child_unit_ids_by_logical_key[child["child_logical_key"]],
        )
        for child in certificate.child_goals
    ]
    result_schema_ref = schema_ref(LEAN_PROOF_ARTIFACT_SCHEMA_VERSION)
    plugin_defined_body = _merge_plugin_defined_body(certificate, required_slots)
    return MergePlan(
        merge_plan_header={
            "merge_plan_id": merge_plan_id,
            "merge_plan_schema_version": "phase4.merge_plan.v1",
            "task_id": task_id,
            "parent_unit_id": parent_unit_id,
            "canonical_selection_id": canonical_selection_id,
            "decomposition_proposal_id": proposal_id,
            "expansion_decision_id": expansion_decision_id,
            "created_by_plugin_id": PLUGIN_ID,
            "created_by_plugin_version": PLUGIN_VERSION,
            "merge_plan_digest": merge_plan_digest,
            "created_at": created_at,
        },
        merge_policy_ref={
            "plugin_id": PLUGIN_ID,
            "plugin_version": PLUGIN_VERSION,
            "merge_policy_id": VERIFIED_MERGE_POLICY_ID,
            "merge_policy_version": "v1",
            "merge_policy_descriptor_digest": plugin_descriptor_digest,
            "merge_policy_params_digest": certificate.certificate_digest,
        },
        required_slots=required_slots,
        parent_output_mapping=[
            {
                "parent_output_name": PROOF_ARTIFACT_OUTPUT_NAME,
                "resolution_kind": "merge_plan_output",
                "merge_slot_keys": [slot["slot_key"] for slot in required_slots],
                "result_schema_ref": result_schema_ref,
                "result_schema_digest": canonical_json_digest(result_schema_ref),
            }
        ],
        hash_recording_requirements={
            "record_child_canonical_output_digest": True,
            "record_slot_source_artifact_digest": True,
            "record_merge_input_bundle_digest": True,
        },
        merge_validation_requirements={
            "all_required_slots_canonical": True,
            "slot_schema_check_required": True,
            "merged_output_schema_check_required": True,
            "plugin_merge_validator_policy_id": CHECKER_VALIDATOR_POLICY_ID,
        },
        plugin_payload={
            "plugin_defined_schema_ref": schema_ref("lean_proof.merge_plan_plugin_payload.v1"),
            "plugin_defined_body_digest": canonical_json_digest(plugin_defined_body),
            "plugin_defined_body": plugin_defined_body,
        },
    )


def _required_slot(*, child: JsonObject, child_unit_id: str) -> JsonObject:
    output_schema_ref = schema_ref(LEAN_PROOF_ARTIFACT_SCHEMA_VERSION)
    return {
        "slot_key": _slot_key(child),
        "source_child_logical_key": child["child_logical_key"],
        "source_child_unit_id": child_unit_id,
        "source_output_name": PROOF_ARTIFACT_OUTPUT_NAME,
        "output_schema_ref": output_schema_ref,
        "output_schema_digest": canonical_json_digest(output_schema_ref),
        "required": True,
        "missing_policy": "block_merge",
    }


def _merge_plugin_defined_body(
    certificate: LeanSplitCertificate,
    required_slots: list[JsonObject],
) -> JsonObject:
    merge_skeleton = certificate.merge_skeleton or {}
    return {
        "schema_version": "lean_proof.merge_plan_plugin_payload.v1",
        "summary": {
            "merge_policy": VERIFIED_MERGE_POLICY_ID,
            "split_certificate_id": certificate.split_certificate_id,
            "split_certificate_digest": certificate.certificate_digest,
            "merge_rule_id": merge_skeleton.get("merge_rule_id"),
            "required_slot_count": len(required_slots),
            "required_slot_keys": [slot["slot_key"] for slot in required_slots],
            "merge_skeleton_digest": digest_json(merge_skeleton),
        },
        "validation_requirements": {
            "all_required_child_proofs_canonical": True,
            "child_context_digest_check_required": True,
            "environment_ref_compatibility_required": True,
            "root_merge_proof_checker_required": True,
        },
    }


def _extract_json_object(stdout: str) -> JsonObject:
    for line in reversed([item.strip() for item in stdout.splitlines() if item.strip()]):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            raise ValueError("Lean split helper output must be a JSON object")
        if parsed.get("schema_version") != LEAN_SPLIT_CERTIFICATE_SCHEMA_VERSION:
            raise ValueError("Lean split helper output has wrong schema_version")
        return parsed
    raise ValueError("Lean split helper did not output a JSON certificate")


def _build_helper_project(
    *,
    environment_manifest: LeanEnvironmentManifest,
    timeout_seconds: int,
    max_output_bytes: int,
) -> None:
    completed = subprocess.run(
        [environment_manifest.lake_executable, "build"],
        cwd=environment_manifest.project_root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout_seconds,
        env=_subprocess_env(environment_manifest),
        check=False,
    )
    if completed.returncode != 0:
        output = (completed.stdout + completed.stderr)[:max_output_bytes]
        raise ValueError("Lean helper project build failed: " + output[:4000])


def _verify_environment_matches_request(
    request: LeanSplitHelperRequest,
    manifest: LeanEnvironmentManifest,
) -> None:
    if request.environment_ref.environment_digest != manifest.environment_digest:
        raise ValueError("environment_ref digest does not match LeanEnvironmentManifest")


def _save_text_artifact(
    store: ArtifactStore,
    text: str,
    *,
    request: LeanSplitHelperRequest,
    suffix: str,
    artifact_type: str,
) -> ArtifactRef:
    return store.save_bytes(
        text.encode("utf-8"),
        artifact_id=_artifact_id(request.request_id, suffix),
        artifact_type=artifact_type,
        media_type="text/plain",
        artifact_schema_id="lean_proof.split_helper_log",
        artifact_schema_version="v1",
        source={"kind": "lean_split_helper", "request_id": request.request_id},
        metadata={},
        created_at=request.created_at,
    )


def _subprocess_env(manifest: LeanEnvironmentManifest) -> dict[str, str]:
    import os

    env = dict(os.environ)
    lean_exe = Path(manifest.lean_executable)
    elan_home = lean_exe.parent.parent
    env["ELAN_HOME"] = str(elan_home)
    env["PATH"] = f"{lean_exe.parent}{os.pathsep}{env.get('PATH', '')}"
    return env


def _temporary_source_path(request_id: str) -> Path:
    return Path(f"{_safe_id(request_id)}.lean")


def _artifact_id(request_id: str, suffix: str) -> str:
    return f"{_safe_id(request_id)}_{_safe_id(suffix)}"


def _slot_key(child: JsonObject) -> str:
    return f"{child['child_logical_key']}:{PROOF_ARTIFACT_OUTPUT_NAME}"


def _derive_phase4_child_unit_id(
    *,
    proposal_digest: str,
    parent_unit_id: str,
    child_logical_key: str,
) -> str:
    return (
        f"unit_{_stable_id_component(parent_unit_id)}_"
        f"{_stable_id_component(proposal_digest.removeprefix('sha256:'))}_"
        f"{_stable_id_component(child_logical_key)}"
    )


def _stable_id_component(value: str) -> str:
    return "".join(
        character if character.isalnum() or character == "_" else "_" for character in value
    )


def _safe_id(value: str) -> str:
    return "".join(character if character.isalnum() or character == "_" else "_" for character in value)


def _lean_string_literal(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _output_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, ArtifactRef):
        return value.to_dict()
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value
