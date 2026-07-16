"""Paper real-AI experiment schemas and stable JSON helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from typing import Any


JsonObject = dict[str, Any]
PAPER_DOMAINS = ("factorization", "lean_proof")
PAPER_DIFFICULTIES = ("easy", "medium", "hard")
LEAN_PAPER_DIFFICULTIES = ("simple", "medium_lemma_dag", "hard_frontier")
PAPER_DIFFICULTY_VALUES = PAPER_DIFFICULTIES + LEAN_PAPER_DIFFICULTIES
LEAN_TOPIC_FAMILIES = ("pure_logic", "function_set", "induction")
PAPER_MODEL_POLICIES = ("fixed_entry",)
UNSUPPORTED_PAPER_TRANSPORTS = frozenset({"scripted", "fake", "deterministic", "mock"})


class PaperStatus(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_FAILURES = "completed_with_failures"
    BLOCKED = "blocked"
    BUDGET_EXHAUSTED = "budget_exhausted"
    FAILED = "failed"


class PaperTaskStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    TIMEOUT = "timeout"
    BUDGET_EXHAUSTED = "budget_exhausted"
    INELIGIBLE = "ineligible"


class PaperAttemptStatus(str, Enum):
    SUCCEEDED = "succeeded"
    PROVIDER_ERROR = "provider_error"
    PARSE_FAILED = "parse_failed"
    VERIFICATION_REJECTED = "verification_rejected"
    CHECKER_REJECTED = "checker_rejected"
    LEASE_EXPIRED = "lease_expired"
    LATE_REJECTED = "late_rejected"
    WORKER_DIED = "worker_died"
    CANCELLED_BY_BUDGET = "cancelled_by_budget"


class PaperFailureStage(str, Enum):
    CATALOG = "catalog"
    SPLIT = "split"
    REQUEST = "request"
    PROVIDER = "provider"
    PARSE = "parse"
    VERIFICATION = "verification"
    CHECKER = "checker"
    CANONICAL = "canonical"
    MERGE = "merge"
    SETTLEMENT = "settlement"
    METRICS = "metrics"
    AUDIT = "audit"


class PaperFailureKind(str, Enum):
    PROVIDER_ERROR = "provider_error"
    RATE_LIMITED = "rate_limited"
    PARSE_FAILURE = "parse_failure"
    VERIFIER_REJECTED = "verifier_rejected"
    CHECKER_REJECTED = "checker_rejected"
    LEASE_EXPIRED = "lease_expired"
    LATE_SUBMISSION = "late_submission"
    NO_REQUEUE = "no_requeue"
    PREMATURE_MERGE = "premature_merge"
    SLOT_MISMATCH = "slot_mismatch"
    BUDGET_LIMIT = "budget_limit"
    UNSUPPORTED_WORKER_LEVEL = "unsupported_worker_level"
    MISSING_MODEL_ENTRY = "missing_model_entry"
    SECRET_LEAK = "secret_leak"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, kw_only=True)
class PaperExperimentCondition:
    experiment_id: str
    condition_id: str
    domain: str
    difficulty: str
    paper_difficulty: str | None = None
    topic_family: str | None = None
    topic_family_version: str | None = None
    construction_rule_id: str | None = None
    oracle_package_group: str | None = None
    proof_assembly_shape: str | None = None
    worker_count: int
    fault_type: str
    fault_rate: float
    ablation_mode: str
    model_policy: str
    model_cohort_id: str | None = None
    cohort_member_id: str | None = None
    model_entry_id: str | None = None
    provider_family: str | None = None
    provider_model_id: str | None = None
    reasoning_profile_id: str | None = None
    model_cohort_digest: str | None = None
    repeat_id: int
    seed: int
    catalog_digest: str
    real_transport_required: bool = True
    paper_eligible_required: bool = True
    schema_version: str = "tokenshare.paper_condition.v1"

    def __post_init__(self) -> None:
        for field_name in (
            "experiment_id",
            "condition_id",
            "domain",
            "difficulty",
            "fault_type",
            "ablation_mode",
            "model_policy",
        ):
            _require_non_empty(field_name, getattr(self, field_name))
        _require_integer("worker_count", self.worker_count, min_value=1)
        _require_integer("repeat_id", self.repeat_id, min_value=0)
        _require_integer("seed", self.seed, min_value=0)
        if self.domain not in PAPER_DOMAINS:
            raise ValueError("domain must be factorization or lean_proof")
        if self.difficulty not in PAPER_DIFFICULTIES:
            raise ValueError("difficulty must be easy, medium, or hard")
        _resolve_paper_difficulty(self.domain, self.difficulty, self.paper_difficulty)
        _validate_optional_topic_family(self.domain, self.topic_family)
        _validate_model_policy(self.model_policy)
        for field_name in (
            "topic_family_version",
            "construction_rule_id",
            "oracle_package_group",
            "proof_assembly_shape",
            "model_cohort_id",
            "cohort_member_id",
            "model_entry_id",
            "provider_family",
            "provider_model_id",
            "reasoning_profile_id",
        ):
            _validate_optional_non_empty(field_name, getattr(self, field_name))
        if self.model_cohort_digest is not None:
            _require_digest("model_cohort_digest", self.model_cohort_digest)
        if not isinstance(self.fault_rate, (float, int)) or self.fault_rate < 0:
            raise ValueError("fault_rate must be a non-negative number")
        _require_digest("catalog_digest", self.catalog_digest)

    @property
    def condition_digest(self) -> str:
        return digest_json(self._body(include_digest=False))

    def to_dict(self) -> JsonObject:
        return self._body(include_digest=True)

    def _body(self, *, include_digest: bool) -> JsonObject:
        body: JsonObject = {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "condition_id": self.condition_id,
            "domain": self.domain,
            "difficulty": self.difficulty,
            "paper_difficulty": _resolve_paper_difficulty(
                self.domain,
                self.difficulty,
                self.paper_difficulty,
            ),
            "topic_family": self.topic_family,
            "topic_family_version": self.topic_family_version,
            "construction_rule_id": self.construction_rule_id,
            "oracle_package_group": self.oracle_package_group,
            "proof_assembly_shape": self.proof_assembly_shape,
            "worker_count": self.worker_count,
            "fault_type": self.fault_type,
            "fault_rate": float(self.fault_rate),
            "ablation_mode": self.ablation_mode,
            "model_policy": self.model_policy,
            "model_cohort_id": self.model_cohort_id,
            "cohort_member_id": self.cohort_member_id,
            "model_entry_id": self.model_entry_id,
            "provider_family": self.provider_family,
            "provider_model_id": self.provider_model_id,
            "reasoning_profile_id": self.reasoning_profile_id,
            "model_cohort_digest": self.model_cohort_digest,
            "repeat_id": self.repeat_id,
            "seed": self.seed,
            "catalog_digest": self.catalog_digest,
            "real_transport_required": self.real_transport_required,
            "paper_eligible_required": self.paper_eligible_required,
        }
        if include_digest:
            body["condition_digest"] = self.condition_digest
        return body


@dataclass(frozen=True, kw_only=True)
class PaperSuiteResult:
    suite_id: str
    status: PaperStatus | str
    output_root: str
    started_at: str
    ended_at: str | None
    experiment_ids: list[str] | tuple[str, ...]
    condition_count: int
    run_count: int
    task_count: int
    provider_attempt_count: int
    total_tokens: int
    total_cost_estimate: float
    paper_eligible: bool
    eligibility_report_ref: JsonObject | None
    budget_ref: JsonObject | None
    metrics_refs: list[JsonObject] | tuple[JsonObject, ...]
    audit_refs: list[JsonObject] | tuple[JsonObject, ...]
    error_summary: list[JsonObject] | tuple[JsonObject, ...]
    model_policy_preflight: JsonObject | None = None
    model_endpoint_cohort_preflight: JsonObject | None = None
    schema_version: str = "tokenshare.paper_suite_result.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "suite_id": self.suite_id,
            "status": _status_value("status", PaperStatus, self.status),
            "output_root": self.output_root,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "experiment_ids": list(self.experiment_ids),
            "condition_count": self.condition_count,
            "run_count": self.run_count,
            "task_count": self.task_count,
            "provider_attempt_count": self.provider_attempt_count,
            "total_tokens": self.total_tokens,
            "total_cost_estimate": float(self.total_cost_estimate),
            "paper_eligible": self.paper_eligible,
            "eligibility_report_ref": _json_value(self.eligibility_report_ref),
            "budget_ref": _json_value(self.budget_ref),
            "metrics_refs": _json_value(list(self.metrics_refs)),
            "audit_refs": _json_value(list(self.audit_refs)),
            "error_summary": _json_value(list(self.error_summary)),
            "model_policy_preflight": _json_value(self.model_policy_preflight),
            "model_endpoint_cohort_preflight": _json_value(
                self.model_endpoint_cohort_preflight
            ),
        }


@dataclass(frozen=True, kw_only=True)
class PaperExperimentResult:
    experiment_id: str
    status: PaperStatus | str
    condition_ids: list[str] | tuple[str, ...]
    run_count: int
    task_count: int
    completion_rate: float
    accepted_validity_rate: float
    total_tokens: int
    total_cost_estimate: float
    summary_ref: JsonObject | None
    schema_version: str = "tokenshare.paper_experiment_result.v1"

    def __post_init__(self) -> None:
        _status_value("status", PaperStatus, self.status)

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "status": _status_value("status", PaperStatus, self.status),
            "condition_ids": list(self.condition_ids),
            "run_count": self.run_count,
            "task_count": self.task_count,
            "completion_rate": float(self.completion_rate),
            "accepted_validity_rate": float(self.accepted_validity_rate),
            "total_tokens": self.total_tokens,
            "total_cost_estimate": float(self.total_cost_estimate),
            "summary_ref": _json_value(self.summary_ref),
        }


@dataclass(frozen=True, kw_only=True)
class PaperConditionResult:
    condition_id: str
    status: PaperStatus | str
    repeat_count: int
    task_count: int
    completed_root_count: int
    failed_root_count: int
    blocked_root_count: int
    provider_attempt_count: int
    metrics_ref: JsonObject | None
    schema_version: str = "tokenshare.paper_condition_result.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "condition_id": self.condition_id,
            "status": _status_value("status", PaperStatus, self.status),
            "repeat_count": self.repeat_count,
            "task_count": self.task_count,
            "completed_root_count": self.completed_root_count,
            "failed_root_count": self.failed_root_count,
            "blocked_root_count": self.blocked_root_count,
            "provider_attempt_count": self.provider_attempt_count,
            "metrics_ref": _json_value(self.metrics_ref),
        }


@dataclass(frozen=True, kw_only=True)
class PaperRunResult:
    condition_id: str
    repeat_id: int
    run_id: str
    status: PaperStatus | str
    run_manifest_ref: JsonObject | None
    per_task_results_ref: JsonObject | None
    per_attempt_results_ref: JsonObject | None
    fault_injections_ref: JsonObject | None
    event_log_ref: JsonObject | None
    artifact_root: str
    paper_eligible: bool
    ineligibility_reasons: list[str] | tuple[str, ...]
    schema_version: str = "tokenshare.paper_run_result.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "condition_id": self.condition_id,
            "repeat_id": self.repeat_id,
            "run_id": self.run_id,
            "status": _status_value("status", PaperStatus, self.status),
            "run_manifest_ref": _json_value(self.run_manifest_ref),
            "per_task_results_ref": _json_value(self.per_task_results_ref),
            "per_attempt_results_ref": _json_value(self.per_attempt_results_ref),
            "fault_injections_ref": _json_value(self.fault_injections_ref),
            "event_log_ref": _json_value(self.event_log_ref),
            "artifact_root": self.artifact_root,
            "paper_eligible": self.paper_eligible,
            "ineligibility_reasons": list(self.ineligibility_reasons),
        }


@dataclass(frozen=True, kw_only=True)
class PaperTaskResult:
    condition_id: str
    repeat_id: int
    task_id: str
    domain: str
    difficulty: str
    paper_difficulty: str | None = None
    topic_family: str | None = None
    topic_family_version: str | None = None
    construction_rule_id: str | None = None
    oracle_package_group: str | None = None
    proof_assembly_shape: str | None = None
    root_status: PaperTaskStatus | str
    accepted_validity: bool | None
    failure_stage: PaperFailureStage | str | None
    failure_kind: PaperFailureKind | str | None
    attempt_count: int
    provider_attempt_count: int
    wall_clock_ms: int
    total_tokens: int
    cost_estimate: float
    event_refs: list[JsonObject] | tuple[JsonObject, ...]
    artifact_refs: list[JsonObject] | tuple[JsonObject, ...]
    paper_eligible: bool
    schema_version: str = "tokenshare.paper_task_result.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "condition_id": self.condition_id,
            "repeat_id": self.repeat_id,
            "task_id": self.task_id,
            "domain": self.domain,
            "difficulty": self.difficulty,
            "paper_difficulty": _resolve_paper_difficulty(
                self.domain,
                self.difficulty,
                self.paper_difficulty,
            ),
            "topic_family": self.topic_family,
            "topic_family_version": self.topic_family_version,
            "construction_rule_id": self.construction_rule_id,
            "oracle_package_group": self.oracle_package_group,
            "proof_assembly_shape": self.proof_assembly_shape,
            "root_status": _status_value("root_status", PaperTaskStatus, self.root_status),
            "accepted_validity": self.accepted_validity,
            "failure_stage": _optional_status_value(
                "failure_stage",
                PaperFailureStage,
                self.failure_stage,
            ),
            "failure_kind": _optional_status_value(
                "failure_kind",
                PaperFailureKind,
                self.failure_kind,
            ),
            "attempt_count": self.attempt_count,
            "provider_attempt_count": self.provider_attempt_count,
            "wall_clock_ms": self.wall_clock_ms,
            "total_tokens": self.total_tokens,
            "cost_estimate": float(self.cost_estimate),
            "event_refs": _json_value(list(self.event_refs)),
            "artifact_refs": _json_value(list(self.artifact_refs)),
            "paper_eligible": self.paper_eligible,
        }


@dataclass(frozen=True, kw_only=True)
class PaperAttemptResult:
    condition_id: str
    repeat_id: int
    run_id: str
    task_id: str
    unit_id: str
    attempt_id: str
    worker_id: str
    provider_attempt_index: int
    attempt_status: PaperAttemptStatus | str
    provider: str
    model: str
    entry_id: str
    request_ref: JsonObject | None
    raw_output_ref: JsonObject | None
    parsed_output_ref: JsonObject | None
    parse_failure_ref: JsonObject | None
    provenance_ref: JsonObject | None
    usage_ref: JsonObject | None
    started_at: str
    ended_at: str
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_estimate: float
    error_kind: str | None
    fault_injection_ref: JsonObject | None
    paper_eligible: bool
    paper_difficulty: str | None = None
    topic_family: str | None = None
    topic_family_version: str | None = None
    construction_rule_id: str | None = None
    oracle_package_group: str | None = None
    proof_assembly_shape: str | None = None
    lemma_node_id: str | None = None
    slot_key: str | None = None
    dependency_path: list[str] | tuple[str, ...] | None = None
    schema_version: str = "tokenshare.paper_attempt_result.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "condition_id": self.condition_id,
            "repeat_id": self.repeat_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "unit_id": self.unit_id,
            "attempt_id": self.attempt_id,
            "worker_id": self.worker_id,
            "provider_attempt_index": self.provider_attempt_index,
            "attempt_status": _status_value(
                "attempt_status",
                PaperAttemptStatus,
                self.attempt_status,
            ),
            "provider": self.provider,
            "model": self.model,
            "entry_id": self.entry_id,
            "request_ref": _json_value(self.request_ref),
            "raw_output_ref": _json_value(self.raw_output_ref),
            "parsed_output_ref": _json_value(self.parsed_output_ref),
            "parse_failure_ref": _json_value(self.parse_failure_ref),
            "provenance_ref": _json_value(self.provenance_ref),
            "usage_ref": _json_value(self.usage_ref),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "latency_ms": self.latency_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_estimate": float(self.cost_estimate),
            "error_kind": self.error_kind,
            "fault_injection_ref": _json_value(self.fault_injection_ref),
            "paper_eligible": self.paper_eligible,
            "paper_difficulty": self.paper_difficulty,
            "topic_family": self.topic_family,
            "topic_family_version": self.topic_family_version,
            "construction_rule_id": self.construction_rule_id,
            "oracle_package_group": self.oracle_package_group,
            "proof_assembly_shape": self.proof_assembly_shape,
            "lemma_node_id": self.lemma_node_id,
            "slot_key": self.slot_key,
            "dependency_path": (
                list(self.dependency_path)
                if self.dependency_path is not None
                else None
            ),
        }


@dataclass(frozen=True, kw_only=True)
class PaperEligibilityReport:
    paper_eligible: bool
    ineligibility_reasons: list[str] | tuple[str, ...]
    checked_attempt_count: int
    real_transport: bool
    transport_kind: str
    secret_scan_passed: bool
    schema_version: str = "tokenshare.paper_eligibility_report.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "paper_eligible": self.paper_eligible,
            "ineligibility_reasons": list(self.ineligibility_reasons),
            "checked_attempt_count": self.checked_attempt_count,
            "real_transport": self.real_transport,
            "transport_kind": self.transport_kind,
            "secret_scan_passed": self.secret_scan_passed,
        }


@dataclass(frozen=True, kw_only=True)
class PaperBudgetResult:
    budget_digest: str
    planned_experiments: list[str] | tuple[str, ...]
    planned_conditions: int
    planned_root_runs: int
    planned_ai_units: int
    max_provider_attempts: int
    token_upper_bound: int
    cost_upper_bound: float
    wall_clock_estimate: float
    quota_preflight: JsonObject
    rate_limit_preflight: JsonObject
    disk_estimate: JsonObject
    status: PaperStatus | str
    schema_version: str = "tokenshare.paper_budget_result.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "budget_digest": self.budget_digest,
            "planned_experiments": list(self.planned_experiments),
            "planned_conditions": self.planned_conditions,
            "planned_root_runs": self.planned_root_runs,
            "planned_ai_units": self.planned_ai_units,
            "max_provider_attempts": self.max_provider_attempts,
            "token_upper_bound": self.token_upper_bound,
            "cost_upper_bound": float(self.cost_upper_bound),
            "wall_clock_estimate": float(self.wall_clock_estimate),
            "quota_preflight": _json_value(self.quota_preflight),
            "rate_limit_preflight": _json_value(self.rate_limit_preflight),
            "disk_estimate": _json_value(self.disk_estimate),
            "status": _status_value("status", PaperStatus, self.status),
        }


def digest_json(data: Any) -> str:
    encoded = json.dumps(
        _json_value(data),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


def evaluate_paper_eligibility(
    *,
    attempts: list[PaperAttemptResult | JsonObject] | tuple[PaperAttemptResult | JsonObject, ...],
    run_evidence: JsonObject,
) -> PaperEligibilityReport:
    attempt_bodies = [_attempt_body(attempt) for attempt in attempts]
    evidence = dict(run_evidence)
    artifact_inventory = _artifact_inventory(evidence)
    transport_evidence = _object_or_empty(evidence.get("transport_evidence"))
    secret_scan_report = _object_or_empty(evidence.get("secret_scan_report"))
    real_transport = transport_evidence.get("real_transport") is True
    transport_kind = str(transport_evidence.get("transport_kind") or "")
    secret_scan_passed = (
        secret_scan_report.get("status") == "passed"
        and int(secret_scan_report.get("leak_count", 1)) == 0
        and _positive_int(secret_scan_report.get("secret_checked_count"))
    )
    scanned_artifact_ids = {
        str(artifact_id)
        for artifact_id in secret_scan_report.get("scanned_artifact_ids", [])
    }
    reasons: list[str] = []

    if not transport_evidence:
        reasons.append("transport_evidence_missing")
    elif not _ref_verified(transport_evidence.get("evidence_ref"), artifact_inventory):
        reasons.append("transport_evidence_ref_unverified")
    if not real_transport:
        reasons.append("real_transport_required")
    if transport_kind in UNSUPPORTED_PAPER_TRANSPORTS:
        reasons.append(f"unsupported_transport:{transport_kind}")
    if transport_kind and transport_kind != "ai_api":
        reasons.append(f"unsupported_transport:{transport_kind}")
    if transport_evidence.get("config_source") != "local_gitignored_config":
        reasons.append("transport_config_source_not_local_gitignored")
    if transport_evidence.get("api_key_policy") != "env_only":
        reasons.append("transport_api_key_policy_not_env_only")
    if transport_evidence.get("executor_kind") != "ai_api_executor":
        reasons.append("transport_executor_not_ai_api")
    if not secret_scan_report:
        reasons.append("secret_scan_report_missing")
    elif not _ref_verified(secret_scan_report.get("report_ref"), artifact_inventory):
        reasons.append("secret_scan_report_ref_unverified")
    if not secret_scan_passed:
        reasons.append("secret_scan_failed")
    if not attempt_bodies:
        reasons.append("missing_provider_attempt")

    for attempt in attempt_bodies:
        reasons.extend(
            _attempt_ineligibility_reasons(
                attempt,
                artifact_inventory=artifact_inventory,
                scanned_artifact_ids=scanned_artifact_ids,
            )
        )

    unique_reasons = tuple(dict.fromkeys(reasons))
    return PaperEligibilityReport(
        paper_eligible=not unique_reasons,
        ineligibility_reasons=unique_reasons,
        checked_attempt_count=len(attempt_bodies),
        real_transport=real_transport,
        transport_kind=transport_kind,
        secret_scan_passed=secret_scan_passed,
    )


def _status_value(field_name: str, enum_type: type[Enum], value: Any) -> str:
    try:
        return enum_type(value).value
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid status") from exc


def _optional_status_value(field_name: str, enum_type: type[Enum], value: Any) -> str | None:
    if value is None:
        return None
    return _status_value(field_name, enum_type, value)


def _attempt_body(attempt: PaperAttemptResult | JsonObject) -> JsonObject:
    if isinstance(attempt, PaperAttemptResult):
        return attempt.to_dict()
    return dict(attempt)


def _attempt_ineligibility_reasons(
    attempt: JsonObject,
    *,
    artifact_inventory: dict[str, JsonObject],
    scanned_artifact_ids: set[str],
) -> list[str]:
    attempt_id = str(attempt.get("attempt_id") or "unknown")
    reasons: list[str] = []
    for field_name in ("provider", "model", "entry_id"):
        if not isinstance(attempt.get(field_name), str) or not attempt.get(field_name):
            reasons.append(f"attempt:{attempt_id}:missing_{field_name}")
    for field_name in ("provider_attempt_index", "latency_ms"):
        if not _non_negative_int(attempt.get(field_name)):
            reasons.append(f"attempt:{attempt_id}:missing_{field_name}")
    for field_name in ("prompt_tokens", "completion_tokens"):
        if not _non_negative_int(attempt.get(field_name)):
            reasons.append(f"attempt:{attempt_id}:missing_{field_name}")
    for field_name in ("started_at", "ended_at"):
        if not isinstance(attempt.get(field_name), str) or not attempt.get(field_name):
            reasons.append(f"attempt:{attempt_id}:missing_{field_name}")

    _check_required_artifact_ref(
        attempt,
        "request_ref",
        attempt_id,
        reasons,
        artifact_inventory=artifact_inventory,
        scanned_artifact_ids=scanned_artifact_ids,
        allowed_types={"ExecutionRequest", "PromptPackage"},
        allowed_source_kinds=None,
    )
    _check_required_artifact_ref(
        attempt,
        "raw_output_ref",
        attempt_id,
        reasons,
        artifact_inventory=artifact_inventory,
        scanned_artifact_ids=scanned_artifact_ids,
        allowed_types={"RawModelOutput"},
        allowed_source_kinds={"ai_api_executor"},
    )
    _check_required_artifact_ref(
        attempt,
        "provenance_ref",
        attempt_id,
        reasons,
        artifact_inventory=artifact_inventory,
        scanned_artifact_ids=scanned_artifact_ids,
        allowed_types={"AIProviderCallProvenance"},
        allowed_source_kinds={"ai_api_executor"},
    )
    _check_required_artifact_ref(
        attempt,
        "usage_ref",
        attempt_id,
        reasons,
        artifact_inventory=artifact_inventory,
        scanned_artifact_ids=scanned_artifact_ids,
        allowed_types={"AIUsageSummary", "UsageSummary"},
        allowed_source_kinds=None,
    )
    has_parsed = _check_optional_artifact_ref(
        attempt,
        "parsed_output_ref",
        attempt_id,
        reasons,
        artifact_inventory=artifact_inventory,
        scanned_artifact_ids=scanned_artifact_ids,
        allowed_types={"ParsedModelOutput", "CandidateOutput"},
        allowed_source_kinds={"ai_api_executor"},
    )
    has_parse_failure = _check_optional_artifact_ref(
        attempt,
        "parse_failure_ref",
        attempt_id,
        reasons,
        artifact_inventory=artifact_inventory,
        scanned_artifact_ids=scanned_artifact_ids,
        allowed_types={"ParseFailureReport"},
        allowed_source_kinds={"ai_api_executor"},
    )
    if not (has_parsed or has_parse_failure):
        reasons.append(f"attempt:{attempt_id}:missing_parsed_output_or_parse_failure_ref")
    if not _positive_int(attempt.get("total_tokens")):
        reasons.append(f"attempt:{attempt_id}:missing_total_tokens")
    if (
        _non_negative_int(attempt.get("prompt_tokens"))
        and _non_negative_int(attempt.get("completion_tokens"))
        and _positive_int(attempt.get("total_tokens"))
        and attempt["prompt_tokens"] + attempt["completion_tokens"] != attempt["total_tokens"]
    ):
        reasons.append(f"attempt:{attempt_id}:token_breakdown_mismatch")
    if not _non_negative_number(attempt.get("cost_estimate")):
        reasons.append(f"attempt:{attempt_id}:missing_cost_estimate")
    return reasons


def _check_required_artifact_ref(
    attempt: JsonObject,
    field_name: str,
    attempt_id: str,
    reasons: list[str],
    *,
    artifact_inventory: dict[str, JsonObject],
    scanned_artifact_ids: set[str],
    allowed_types: set[str],
    allowed_source_kinds: set[str] | None,
) -> bool:
    ref = attempt.get(field_name)
    if not _has_complete_artifact_ref(ref):
        reasons.append(f"attempt:{attempt_id}:missing_{field_name}")
        return False
    return _check_artifact_evidence(
        ref,
        field_name,
        attempt_id,
        reasons,
        artifact_inventory=artifact_inventory,
        scanned_artifact_ids=scanned_artifact_ids,
        allowed_types=allowed_types,
        allowed_source_kinds=allowed_source_kinds,
    )


def _check_optional_artifact_ref(
    attempt: JsonObject,
    field_name: str,
    attempt_id: str,
    reasons: list[str],
    *,
    artifact_inventory: dict[str, JsonObject],
    scanned_artifact_ids: set[str],
    allowed_types: set[str],
    allowed_source_kinds: set[str] | None,
) -> bool:
    ref = attempt.get(field_name)
    if ref is None:
        return False
    if not _has_complete_artifact_ref(ref):
        reasons.append(f"attempt:{attempt_id}:missing_{field_name}")
        return False
    return _check_artifact_evidence(
        ref,
        field_name,
        attempt_id,
        reasons,
        artifact_inventory=artifact_inventory,
        scanned_artifact_ids=scanned_artifact_ids,
        allowed_types=allowed_types,
        allowed_source_kinds=allowed_source_kinds,
    )


def _check_artifact_evidence(
    ref: JsonObject,
    field_name: str,
    attempt_id: str,
    reasons: list[str],
    *,
    artifact_inventory: dict[str, JsonObject],
    scanned_artifact_ids: set[str],
    allowed_types: set[str],
    allowed_source_kinds: set[str] | None,
) -> bool:
    artifact_id = str(ref["artifact_id"])
    manifest = artifact_inventory.get(artifact_id)
    verified = _ref_verified(ref, artifact_inventory)
    if not verified:
        reasons.append(f"attempt:{attempt_id}:unverified_{field_name}")
        return False
    if manifest is not None and manifest.get("artifact_type") not in allowed_types:
        reasons.append(f"attempt:{attempt_id}:invalid_{field_name}_type")
        verified = False
    if allowed_source_kinds is not None:
        source = _object_or_empty(manifest.get("source") if manifest is not None else None)
        if source.get("kind") not in allowed_source_kinds:
            reasons.append(f"attempt:{attempt_id}:invalid_{field_name}_source")
            verified = False
    if artifact_id not in scanned_artifact_ids:
        reasons.append(f"attempt:{attempt_id}:unscanned_{field_name}")
        verified = False
    return verified


def _artifact_inventory(evidence: JsonObject) -> dict[str, JsonObject]:
    manifests = evidence.get("artifact_manifests", [])
    if isinstance(manifests, dict):
        candidates = manifests.values()
    elif isinstance(manifests, list):
        candidates = manifests
    else:
        candidates = []
    inventory: dict[str, JsonObject] = {}
    for candidate in candidates:
        if isinstance(candidate, dict) and _has_complete_artifact_ref(candidate):
            inventory[str(candidate["artifact_id"])] = dict(candidate)
    return inventory


def _ref_verified(value: Any, inventory: dict[str, JsonObject]) -> bool:
    if not _has_complete_artifact_ref(value):
        return False
    manifest = inventory.get(str(value["artifact_id"]))
    if manifest is None:
        return False
    return (
        manifest.get("content_hash") == value.get("content_hash")
        and manifest.get("artifact_type") == value.get("artifact_type")
        and manifest.get("artifact_schema_id") == value.get("artifact_schema_id")
    )


def _has_complete_artifact_ref(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    required = (
        "artifact_id",
        "artifact_type",
        "content_hash",
        "artifact_schema_id",
        "source",
    )
    if not all(value.get(field_name) for field_name in required):
        return False
    return (
        isinstance(value.get("source"), dict)
        and isinstance(value["content_hash"], str)
        and value["content_hash"].startswith("sha256:")
    )


def _object_or_empty(value: Any) -> JsonObject:
    return dict(value) if isinstance(value, dict) else {}


def _positive_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value > 0


def _non_negative_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _non_negative_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (float, int))
        and value >= 0
    )


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _resolve_paper_difficulty(
    domain: str,
    difficulty: str,
    paper_difficulty: str | None,
) -> str:
    resolved = difficulty if paper_difficulty is None else paper_difficulty
    if not isinstance(resolved, str) or not resolved:
        raise ValueError("paper_difficulty must be a non-empty string")
    if resolved not in PAPER_DIFFICULTY_VALUES:
        raise ValueError(
            "paper_difficulty must be easy, medium, hard, simple, "
            "medium_lemma_dag, or hard_frontier"
        )
    if domain == "factorization" and resolved not in PAPER_DIFFICULTIES:
        raise ValueError("factorization paper_difficulty must be easy, medium, or hard")
    return resolved


def _validate_optional_topic_family(domain: str, topic_family: str | None) -> None:
    if topic_family is None:
        return
    if not isinstance(topic_family, str) or not topic_family:
        raise ValueError("topic_family must be a non-empty string")
    if domain == "lean_proof" and topic_family not in LEAN_TOPIC_FAMILIES:
        raise ValueError("topic_family must be pure_logic, function_set, or induction")


def _validate_optional_non_empty(field_name: str, value: str | None) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")


def _validate_model_policy(value: str) -> None:
    if value not in PAPER_MODEL_POLICIES:
        raise ValueError("model_policy must be fixed_entry")


def _require_non_empty(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_integer(field_name: str, value: int, *, min_value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < min_value:
        raise ValueError(f"{field_name} must be an integer >= {min_value}")


def _require_digest(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        raise ValueError(f"{field_name} must be a sha256 digest")
