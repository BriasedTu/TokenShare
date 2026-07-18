"""Experiment 5 fixed-entry model-provider endpoint comparison module."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Any

from tokenshare.executors.ai_api_artifacts import read_raw_model_identity_evidence
from tokenshare.experiments.paper_experiment_contracts import (
    ExperimentSummaryRows,
    FrozenCaseSelection,
    PaperExecutionContext,
)
from tokenshare.experiments.paper_model_identity import PaperModelEndpointIdentity
from tokenshare.experiments.paper_model_policy import (
    PAPER_MODEL_ENDPOINT_COHORT_ID,
    PAPER_MODEL_ENDPOINT_COHORT_MEMBER_IDS,
    PAPER_MODEL_ENDPOINT_COHORT_MEMBERS,
)
from tokenshare.experiments.paper_models import (
    JsonObject,
    PaperConditionResult,
    PaperExperimentCondition,
    PaperModelExecutionRecord,
    UNSUPPORTED_PAPER_TRANSPORTS,
    digest_json,
)


EXP5_EXPERIMENT_ID = "exp5_real_ai_model_endpoint_comparison"
EXP5_SUITE_VERSION = "paper_v1"
EXP5_CATALOG_VERSION = "v1"
EXP5_REPEAT_COUNT = 3
EXP5_WORKER_COUNT = 10
EXP5_SEED_FAMILY = (5001, 5002, 5003)
EXP5_TASKS_PER_CONDITION = 5
EXP5_EXPECTED_CONDITION_COUNT = 54
EXP5_EXPECTED_ROOT_RUNS = 270
EXP5_INCOMPLETE_COHORT_REASON = "incomplete_model_cohort"
EXP5_MIXED_TOPIC_FAMILY_MARKER = "mixed_topic_family"

FACTOR_PAPER_DIFFICULTIES = ("easy", "medium", "hard")
LEAN_PAPER_DIFFICULTIES = ("simple", "medium_lemma_dag", "hard_frontier")
LEAN_TOPIC_FAMILIES = ("pure_logic", "function_set", "induction")
LEAN_CONDITION_DIFFICULTY = {
    "simple": "easy",
    "medium_lemma_dag": "medium",
    "hard_frontier": "hard",
}
LEAN_TOPIC_ALLOCATIONS = {
    "simple": {"pure_logic": 2, "function_set": 2, "induction": 1},
    "medium_lemma_dag": {"pure_logic": 1, "function_set": 2, "induction": 2},
    "hard_frontier": {"pure_logic": 2, "function_set": 1, "induction": 2},
}


@dataclass(frozen=True, kw_only=True)
class Exp5CohortGate:
    member_plans: tuple[JsonObject, ...]
    blocked_reasons: tuple[str, ...]

    @property
    def is_blocked(self) -> bool:
        return bool(self.blocked_reasons)

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": "tokenshare.paper_exp5_cohort_gate.v1",
            "experiment_id": EXP5_EXPERIMENT_ID,
            "status": "blocked" if self.is_blocked else "planned",
            "blocked_reason": (
                EXP5_INCOMPLETE_COHORT_REASON if self.is_blocked else None
            ),
            "ineligibility_reasons": (
                [EXP5_INCOMPLETE_COHORT_REASON] if self.is_blocked else []
            ),
            "cohort_validation_reasons": list(self.blocked_reasons),
            "member_count": len(self.member_plans),
            "pilot_only": False,
            "paper_eligible_possible": not self.is_blocked,
            "paper_eligible": False,
            "provider_attempt_count": 0,
            "provider_calls_made": 0,
        }


class Exp5MixedLeanCaseSelection(FrozenCaseSelection):
    """Frozen five-task Lean slice shared with Experiments 2 and 4."""

    def __post_init__(self) -> None:
        if self.domain != "lean_proof" or self.topic_family is not None:
            super().__post_init__()
            return
        if self.schema_version != "tokenshare.paper_frozen_case_selection.v1":
            raise ValueError(
                "schema_version must be tokenshare.paper_frozen_case_selection.v1"
            )
        for field_name in (
            "selection_id",
            "experiment_id",
            "suite_version",
            "catalog_version",
            "paper_difficulty",
        ):
            _require_non_empty_string(field_name, getattr(self, field_name))
        if self.experiment_id != EXP5_EXPERIMENT_ID:
            raise ValueError("selection must belong to Experiment 5")
        if self.paper_difficulty not in LEAN_PAPER_DIFFICULTIES:
            raise ValueError("invalid Experiment 5 Lean paper_difficulty")
        _require_complete_digest("catalog_digest", self.catalog_digest)
        if not isinstance(self.paper_eligible_required, bool):
            raise ValueError("paper_eligible_required must be a bool")
        if self.blocked_reason is not None:
            raise ValueError("Experiment 5 mixed Lean selection cannot be blocked")
        if (
            isinstance(self.expected_ai_unit_count, bool)
            or not isinstance(self.expected_ai_unit_count, int)
            or self.expected_ai_unit_count < 1
        ):
            raise ValueError("expected_ai_unit_count must be an integer >= 1")
        ordered_case_ids = _normalize_case_ids(self.ordered_case_ids)
        if len(ordered_case_ids) != EXP5_TASKS_PER_CONDITION:
            raise ValueError("Experiment 5 Lean selection must contain 5 roots")
        object.__setattr__(self, "ordered_case_ids", ordered_case_ids)
        object.__setattr__(
            self,
            "topic_family_marker",
            EXP5_MIXED_TOPIC_FAMILY_MARKER,
        )
        object.__setattr__(
            self,
            "topic_family_counts",
            dict(LEAN_TOPIC_ALLOCATIONS[self.paper_difficulty]),
        )

    def _body(self, *, include_digest: bool) -> JsonObject:
        body = super()._body(include_digest=include_digest)
        if self.domain == "lean_proof" and self.topic_family is None:
            body["topic_family_marker"] = self.topic_family_marker
            body["topic_family_counts"] = dict(self.topic_family_counts)
        return body


class Experiment5ModelComparisonModule:
    """Gate B module for the preregistered three-endpoint comparison."""

    def expand_conditions(
        self,
        context: PaperExecutionContext,
    ) -> tuple[PaperExperimentCondition, ...]:
        return expand_exp5_conditions(context)

    def freeze_case_selections(
        self,
        context: PaperExecutionContext,
        conditions: tuple[PaperExperimentCondition, ...],
    ) -> tuple[FrozenCaseSelection, ...]:
        return freeze_exp5_case_selections(context, conditions)

    def run_condition(
        self,
        context: PaperExecutionContext,
        condition: PaperExperimentCondition,
        selection: FrozenCaseSelection,
    ) -> PaperConditionResult:
        canonical_condition = _canonical_condition(context, condition)
        canonical_selection = _selection_for_condition(context, canonical_condition)
        if _selection_body(selection) != _selection_body(canonical_selection):
            raise ValueError("selection does not match canonical Experiment 5 slice")
        result = context.execution_callback(
            context=context,
            condition=canonical_condition,
            selection=canonical_selection,
            experiment_id=EXP5_EXPERIMENT_ID,
        )
        if not isinstance(result, PaperConditionResult):
            raise ValueError("execution callback must return PaperConditionResult")
        if result.condition_id != canonical_condition.condition_id:
            raise ValueError("execution callback returned a mismatched condition_id")
        return result

    def summarize(self, evidence: Any) -> ExperimentSummaryRows:
        return summarize_exp5_model_comparison(evidence)


Exp5ModelComparisonModule = Experiment5ModelComparisonModule


def assess_exp5_cohort(context: PaperExecutionContext) -> Exp5CohortGate:
    return _validate_cohort_preflight(context.approved_endpoint_binding)


def build_exp5_single_member_pilot_plan(
    context: PaperExecutionContext,
    *,
    cohort_member_id: str,
) -> JsonObject:
    """Return a zero-call single-endpoint plan that can never become formal evidence."""

    if cohort_member_id not in PAPER_MODEL_ENDPOINT_COHORT_MEMBER_IDS:
        raise ValueError("unknown Experiment 5 cohort member")
    preflight = context.approved_endpoint_binding
    member_plans = _mapping(_mapping(preflight).get("member_plans"))
    plan = member_plans.get(cohort_member_id)
    if not isinstance(plan, Mapping):
        raise ValueError(EXP5_INCOMPLETE_COHORT_REASON)
    reasons = _single_member_plan_reasons(
        plan=plan,
        cohort_member_id=cohort_member_id,
        cohort_digest=_mapping(preflight).get("model_cohort_digest"),
    )
    if reasons:
        raise ValueError(
            f"{EXP5_INCOMPLETE_COHORT_REASON}: {', '.join(reasons)}"
        )
    return {
        "schema_version": "tokenshare.paper_exp5_single_member_pilot_plan.v1",
        "experiment_id": EXP5_EXPERIMENT_ID,
        "status": "planned",
        "pilot_only": True,
        "paper_eligible": False,
        "ineligibility_reasons": ["single_member_pilot"],
        "cohort_member_id": cohort_member_id,
        "provider_config_id": plan["provider_config_id"],
        "selected_entry_id": plan["selected_entry_id"],
        "provider_family": plan["provider_family"],
        "provider_model_id": plan["provider_model_id"],
        "reasoning_profile_id": plan["reasoning_profile_id"],
        "model_cohort_digest": plan["model_cohort_digest"],
        "source_provider_config_digest": plan["source_provider_config_digest"],
        "model_endpoint_identity_digest": plan[
            "model_endpoint_identity_digest"
        ],
        "condition_count": 2 * 3 * EXP5_REPEAT_COUNT,
        "root_run_count": 2
        * 3
        * EXP5_REPEAT_COUNT
        * EXP5_TASKS_PER_CONDITION,
        "provider_attempt_count": 0,
        "provider_calls_made": 0,
    }


def expand_exp5_conditions(
    context: PaperExecutionContext,
) -> tuple[PaperExperimentCondition, ...]:
    gate = assess_exp5_cohort(context)
    if gate.is_blocked:
        return ()
    catalog_digest = _catalog_digest(context.catalog)
    conditions: list[PaperExperimentCondition] = []
    for member_plan in gate.member_plans:
        for repeat_id, seed in enumerate(EXP5_SEED_FAMILY):
            for paper_difficulty in FACTOR_PAPER_DIFFICULTIES:
                conditions.append(
                    _condition(
                        member_plan=member_plan,
                        domain="factorization",
                        difficulty=paper_difficulty,
                        paper_difficulty=paper_difficulty,
                        repeat_id=repeat_id,
                        seed=seed,
                        catalog_digest=catalog_digest,
                    )
                )
            for paper_difficulty in LEAN_PAPER_DIFFICULTIES:
                conditions.append(
                    _condition(
                        member_plan=member_plan,
                        domain="lean_proof",
                        difficulty=LEAN_CONDITION_DIFFICULTY[paper_difficulty],
                        paper_difficulty=paper_difficulty,
                        repeat_id=repeat_id,
                        seed=seed,
                        catalog_digest=catalog_digest,
                    )
                )
    if len(conditions) != EXP5_EXPECTED_CONDITION_COUNT:
        raise ValueError("Experiment 5 condition count drift")
    return tuple(conditions)


def freeze_exp5_case_selections(
    context: PaperExecutionContext,
    conditions: Sequence[PaperExperimentCondition],
) -> tuple[FrozenCaseSelection, ...]:
    canonical_conditions = expand_exp5_conditions(context)
    if tuple(condition.condition_digest for condition in conditions) != tuple(
        condition.condition_digest for condition in canonical_conditions
    ):
        raise ValueError("Experiment 5 condition order or identity drift")
    _validate_shared_slice(context.catalog)
    selections = tuple(
        _selection_for_condition(context, condition)
        for condition in canonical_conditions
    )
    count_exp5_root_runs(canonical_conditions, selections)
    return selections


def count_exp5_root_runs(
    conditions: Sequence[PaperExperimentCondition],
    selections: Sequence[FrozenCaseSelection],
) -> int:
    if len(conditions) != len(selections):
        raise ValueError("conditions and selections must have the same length")
    total = 0
    for condition, selection in zip(conditions, selections, strict=True):
        _validate_selection_shape(condition, selection)
        if selection.is_executable:
            total += len(selection.ordered_case_ids)
    if conditions and total != EXP5_EXPECTED_ROOT_RUNS:
        raise ValueError("Experiment 5 root-run count drift")
    return total


def build_exp5_model_execution_rows(evidence: Any) -> tuple[JsonObject, ...]:
    """Join persisted model identity records with raw response and usage facts."""

    rows = tuple(_model_execution_row(item) for item in _execution_items(evidence))
    seen: set[tuple[str, int, str, str, str, str]] = set()
    for row in rows:
        identity = (
            str(row["condition_id"]),
            int(row["repeat_id"]),
            str(row["run_id"]),
            str(row["task_id"]),
            str(row["unit_id"]),
            str(row["attempt_id"]),
        )
        if identity in seen:
            raise ValueError("duplicate Experiment 5 model execution record")
        seen.add(identity)
    return rows


def summarize_exp5_model_comparison(evidence: Any) -> ExperimentSummaryRows:
    return ExperimentSummaryRows(
        experiment_id=EXP5_EXPERIMENT_ID,
        rows=build_exp5_model_execution_rows(evidence),
    )


def expand_conditions(
    context: PaperExecutionContext,
) -> tuple[PaperExperimentCondition, ...]:
    return expand_exp5_conditions(context)


def freeze_case_selections(
    context: PaperExecutionContext,
    conditions: tuple[PaperExperimentCondition, ...],
) -> tuple[FrozenCaseSelection, ...]:
    return freeze_exp5_case_selections(context, conditions)


def run_condition(
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
    selection: FrozenCaseSelection,
) -> PaperConditionResult:
    return Experiment5ModelComparisonModule().run_condition(
        context,
        condition,
        selection,
    )


def summarize(evidence: Any) -> ExperimentSummaryRows:
    return summarize_exp5_model_comparison(evidence)


def _execution_items(evidence: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(evidence, Mapping):
        items = evidence.get("model_execution_records", ())
    else:
        items = evidence
    if not isinstance(items, (list, tuple)):
        raise ValueError("model_execution_records must be a list or tuple")
    if any(not isinstance(item, Mapping) for item in items):
        raise ValueError("model execution evidence items must be mappings")
    return tuple(items)


def _model_execution_row(item: Mapping[str, Any]) -> JsonObject:
    record_body = item.get("record", item)
    if not isinstance(record_body, Mapping):
        raise ValueError("model execution record must be a mapping")
    schema_version = record_body.get("schema_version")
    if schema_version not in {
        "tokenshare.paper_model_execution_record.v1",
        "tokenshare.paper_model_execution_record.v2",
    }:
        raise ValueError(f"unsupported model execution record schema: {schema_version}")
    _validate_record_digest(record_body)
    record = (
        _v2_record_from_mapping(record_body)
        if schema_version == "tokenshare.paper_model_execution_record.v2"
        else None
    )
    expected_identity = record_body.get("expected_identity")
    if not isinstance(expected_identity, Mapping):
        raise ValueError("model execution record requires expected_identity")
    configured_model = _required_row_string(
        "expected_identity.provider_model_id",
        expected_identity.get("provider_model_id"),
    )
    expected_provider = _required_row_string(
        "expected_identity.provider_family",
        expected_identity.get("provider_family"),
    )
    expected_entry = _required_row_string(
        "expected_identity.selected_entry_id",
        expected_identity.get("selected_entry_id"),
    )
    expected_controls = expected_identity.get("effective_reasoning_controls")
    if not isinstance(expected_controls, Mapping):
        raise ValueError("expected identity requires reasoning controls")

    raw_output = item.get("raw_output")
    if raw_output is not None and not isinstance(raw_output, Mapping):
        raise ValueError("raw_output must be a persisted mapping")
    if record_body.get("raw_output_ref") is not None and raw_output is None:
        raise ValueError("persisted raw output body is required for identity join")
    raw_identity = (
        read_raw_model_identity_evidence(raw_output)
        if isinstance(raw_output, Mapping)
        else None
    )

    failure_reasons = [str(reason) for reason in record_body.get("mismatch_reasons", ())]
    if isinstance(raw_output, Mapping):
        raw_schema = raw_output.get("schema_version")
        if (
            raw_schema == "phase7.raw_model_output.v2"
            and raw_output.get("provider_family") != expected_provider
        ):
            failure_reasons.append("raw_provider_identity_mismatch")
        if (
            raw_schema == "phase7.raw_model_output.v2"
            and raw_output.get("entry_id") != expected_entry
        ):
            failure_reasons.append("raw_entry_identity_mismatch")
    if schema_version == "tokenshare.paper_model_execution_record.v1":
        failure_reasons.append("historical_v1_identity")
        requested_model = raw_identity.requested_model if raw_identity is not None else None
        resolved_model = raw_identity.resolved_model if raw_identity is not None else None
        response_model_status = (
            raw_identity.response_model_status if raw_identity is not None else "unavailable"
        )
    else:
        assert record is not None
        requested_model = record.requested_model
        resolved_model = record.resolved_model
        response_model_status = record.response_model_status
        if raw_identity is not None and (
            requested_model != raw_identity.requested_model
            or resolved_model != raw_identity.resolved_model
            or response_model_status != raw_identity.response_model_status
        ):
            raise ValueError("model execution record/raw output identity is inconsistent")

    if raw_identity is not None:
        if (
            raw_identity.configured_model is not None
            and raw_identity.configured_model != configured_model
        ):
            failure_reasons.append("configured_model_mismatch")
        if (
            raw_identity.requested_model is not None
            and raw_identity.requested_model != configured_model
        ):
            failure_reasons.append("requested_model_mismatch")
    if resolved_model is None and response_model_status != "unavailable":
        failure_reasons.append("missing_resolved_model")
    elif resolved_model is not None and resolved_model != configured_model:
        failure_reasons.append("resolved_model_mismatch")

    request_identities = _mapping_sequence(
        record_body.get("actual_request_identities"),
        "actual_request_identities",
    )
    provider_attempts = _mapping_sequence(
        record_body.get("actual_provider_attempts"),
        "actual_provider_attempts",
    )
    if (
        schema_version == "tokenshare.paper_model_execution_record.v2"
        and record_body.get("identity_status") == "matched"
    ):
        if not request_identities:
            failure_reasons.append("missing_request_identity")
        if not provider_attempts:
            failure_reasons.append("missing_provider_attempt_identity")
    actual_controls: dict[str, Any] = {}
    request_controls_digests: list[str] = []
    for request_identity in request_identities:
        provider = request_identity.get("provider_family")
        entry_id = request_identity.get("entry_id")
        if provider != expected_provider or entry_id != expected_entry:
            failure_reasons.append("endpoint_failover")
        actual_configured = request_identity.get(
            "configured_model",
            request_identity.get("model"),
        )
        actual_requested = request_identity.get(
            "requested_model",
            request_identity.get("model"),
        )
        if actual_configured != configured_model or actual_requested != configured_model:
            failure_reasons.append("requested_model_mismatch")
        controls = request_identity.get("reasoning_controls")
        if not isinstance(controls, Mapping):
            failure_reasons.append("reasoning_controls_missing")
            continue
        actual_controls = dict(controls)
        if not _reasoning_controls_match(
            provider_family=expected_provider,
            expected=dict(expected_controls),
            actual=actual_controls,
        ):
            failure_reasons.append("reasoning_drift")
        controls_digest = request_identity.get(
            "effective_request_controls_digest"
        )
        if not _is_complete_digest(controls_digest):
            failure_reasons.append("invalid_effective_request_controls_digest")
        else:
            request_controls_digests.append(str(controls_digest))
    if len(set(request_controls_digests)) > 1:
        failure_reasons.append("request_controls_drift")
    for provider_attempt in provider_attempts:
        if (
            provider_attempt.get("provider_family") != expected_provider
            or provider_attempt.get("entry_id") != expected_entry
        ):
            failure_reasons.append("endpoint_failover")
        actual_model = provider_attempt.get(
            "configured_model",
            provider_attempt.get("model"),
        )
        if actual_model != configured_model:
            failure_reasons.append("configured_model_mismatch")

    attempt = item.get("attempt", {})
    if not isinstance(attempt, Mapping):
        raise ValueError("attempt join body must be a mapping")
    _validate_required_attempt_fields(attempt, item=item)
    _validate_attempt_identity(record_body, attempt)
    task_paper_eligible, task_eligibility_reason = _task_paper_eligibility(
        item,
        record_body,
    )
    if task_eligibility_reason is not None:
        failure_reasons.append(task_eligibility_reason)
    transport_kind = _required_row_string("transport_kind", item.get("transport_kind"))
    if transport_kind != "ai_api" or transport_kind in UNSUPPORTED_PAPER_TRANSPORTS:
        failure_reasons.append("unsupported_transport")
    model_policy = _required_row_string("model_policy", item.get("model_policy"))
    if model_policy != "fixed_entry":
        failure_reasons.append("invalid_model_policy")
    pilot_only = item.get("pilot_only")
    if not isinstance(pilot_only, bool):
        raise ValueError("pilot_only must be a bool")
    if attempt.get("provider") != expected_provider:
        failure_reasons.append("attempt_provider_identity_mismatch")
    if attempt.get("model") != configured_model:
        failure_reasons.append("attempt_model_identity_mismatch")
    if attempt.get("entry_id") != expected_entry:
        failure_reasons.append("attempt_entry_identity_mismatch")
    record_ref = item.get("record_ref")
    expected_attempt_refs = {
        "request_ref": record_body.get("request_ref"),
        "raw_output_ref": record_body.get("raw_output_ref"),
        "provenance_ref": record_body.get("provenance_ref"),
        "usage_ref": record_body.get("usage_ref"),
        "model_execution_record_ref": record_ref,
    }
    if any(
        attempt.get(field_name) != expected_ref
        for field_name, expected_ref in expected_attempt_refs.items()
    ):
        failure_reasons.append("attempt_artifact_ref_mismatch")
    provider_errors = _provider_errors(item, attempt, provider_attempts)
    if provider_errors:
        failure_reasons.append("provider_error")
    if pilot_only:
        failure_reasons.append("pilot_only")
    stable_reasons = tuple(dict.fromkeys(failure_reasons))
    paper_eligible = (
        schema_version == "tokenshare.paper_model_execution_record.v2"
        and record_body.get("identity_status") == "matched"
        and record_body.get("paper_eligible") is True
        and task_paper_eligible
        and not stable_reasons
    )

    return {
        "schema_version": "tokenshare.paper_exp5_model_execution_row.v1",
        "experiment_id": EXP5_EXPERIMENT_ID,
        "condition_id": record_body["condition_id"],
        "repeat_id": record_body["repeat_id"],
        "run_id": record_body["run_id"],
        "task_id": record_body["task_id"],
        "unit_id": record_body["unit_id"],
        "attempt_id": record_body["attempt_id"],
        "model_policy": model_policy,
        "model_cohort_id": expected_identity.get("model_cohort_id"),
        "model_cohort_digest": expected_identity.get("model_cohort_digest"),
        "cohort_member_id": expected_identity.get("cohort_member_id"),
        "provider_config_id": expected_identity.get("provider_config_id"),
        "selected_entry_id": expected_entry,
        "provider_family": expected_provider,
        "configured_model": configured_model,
        "requested_model": requested_model,
        "resolved_model": resolved_model,
        "response_model_status": response_model_status,
        "reasoning_profile_id": expected_identity.get("reasoning_profile_id"),
        "expected_reasoning_controls": dict(expected_controls),
        "reasoning_controls": actual_controls,
        "effective_request_controls_digests": request_controls_digests,
        "source_provider_config_digest": record_body[
            "source_provider_config_digest"
        ],
        "prepared_execution_config_digest": record_body[
            "prepared_execution_config_digest"
        ],
        "request_ref": record_body.get("request_ref"),
        "provenance_ref": record_body.get("provenance_ref"),
        "raw_output_ref": record_body.get("raw_output_ref"),
        "usage_ref": record_body.get("usage_ref"),
        "model_execution_record_digest": record_body.get("record_digest"),
        "identity_status": record_body.get("identity_status"),
        "worker_id": attempt.get("worker_id"),
        "provider_attempt_index": _non_negative_int(
            attempt["provider_attempt_index"],
            "provider_attempt_index",
        ),
        "attempt_status": attempt.get("attempt_status"),
        "provider": attempt.get("provider"),
        "model": attempt.get("model"),
        "entry_id": attempt.get("entry_id"),
        "parsed_output_ref": attempt.get("parsed_output_ref"),
        "parse_failure_ref": attempt.get("parse_failure_ref"),
        "fault_injection_ref": attempt.get("fault_injection_ref"),
        "model_execution_record_ref": record_ref,
        "started_at": attempt.get("started_at"),
        "ended_at": attempt.get("ended_at"),
        "latency_ms": _non_negative_int(attempt["latency_ms"], "latency_ms"),
        "prompt_tokens": _non_negative_int(
            attempt["prompt_tokens"],
            "prompt_tokens",
        ),
        "completion_tokens": _non_negative_int(
            attempt["completion_tokens"],
            "completion_tokens",
        ),
        "total_tokens": _non_negative_int(
            attempt["total_tokens"],
            "total_tokens",
        ),
        "cost_estimate": _non_negative_number(
            attempt["cost_estimate"],
            "cost_estimate",
        ),
        "provider_errors": provider_errors,
        "error_kind": attempt.get("error_kind"),
        "failure_reasons": list(stable_reasons),
        "transport_kind": transport_kind,
        "pilot_only": pilot_only,
        "attempt_paper_eligible": attempt["paper_eligible"],
        "task_paper_eligible": task_paper_eligible,
        "paper_eligible": paper_eligible,
    }


def _validate_record_digest(body: Mapping[str, Any]) -> None:
    observed = body.get("record_digest")
    _require_complete_digest("record_digest", observed)
    canonical = {key: value for key, value in body.items() if key != "record_digest"}
    if observed != digest_json(canonical):
        raise ValueError("model execution record digest mismatch")


def _v2_record_from_mapping(body: Mapping[str, Any]) -> PaperModelExecutionRecord:
    try:
        return PaperModelExecutionRecord(
            schema_version=str(body["schema_version"]),
            condition_id=str(body["condition_id"]),
            repeat_id=body["repeat_id"],
            run_id=str(body["run_id"]),
            task_id=str(body["task_id"]),
            unit_id=str(body["unit_id"]),
            attempt_id=str(body["attempt_id"]),
            expected_identity=dict(body["expected_identity"]),
            source_provider_config_digest=str(
                body["source_provider_config_digest"]
            ),
            prepared_execution_config_digest=str(
                body["prepared_execution_config_digest"]
            ),
            request_ref=dict(body["request_ref"]),
            provenance_ref=dict(body["provenance_ref"]),
            raw_output_ref=(
                dict(body["raw_output_ref"])
                if body.get("raw_output_ref") is not None
                else None
            ),
            usage_ref=dict(body["usage_ref"]),
            actual_request_identities=list(body["actual_request_identities"]),
            actual_provider_attempts=list(body["actual_provider_attempts"]),
            requested_model=body.get("requested_model"),
            resolved_model=body.get("resolved_model"),
            response_model_status=str(body["response_model_status"]),
            identity_status=str(body["identity_status"]),
            mismatch_reasons=list(body["mismatch_reasons"]),
            paper_eligible=body["paper_eligible"],
            created_at=str(body["created_at"]),
        )
    except (KeyError, TypeError) as exc:
        raise ValueError("invalid v2 model execution record") from exc


def _validate_required_attempt_fields(
    attempt: Mapping[str, Any],
    *,
    item: Mapping[str, Any],
) -> None:
    required_fields = (
        "condition_id",
        "repeat_id",
        "run_id",
        "task_id",
        "unit_id",
        "attempt_id",
        "worker_id",
        "provider_attempt_index",
        "attempt_status",
        "provider",
        "model",
        "entry_id",
        "request_ref",
        "raw_output_ref",
        "provenance_ref",
        "usage_ref",
        "model_execution_record_ref",
        "started_at",
        "ended_at",
        "latency_ms",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cost_estimate",
        "paper_eligible",
    )
    for field_name in required_fields:
        if field_name not in attempt:
            raise ValueError(f"formal Experiment 5 attempt requires {field_name}")
    for field_name in (
        "condition_id",
        "run_id",
        "task_id",
        "unit_id",
        "attempt_id",
        "worker_id",
        "attempt_status",
        "provider",
        "model",
        "entry_id",
        "started_at",
        "ended_at",
    ):
        _required_row_string(field_name, attempt[field_name])
    _non_negative_int(attempt["repeat_id"], "repeat_id")
    _non_negative_int(
        attempt["provider_attempt_index"],
        "provider_attempt_index",
    )
    for field_name in (
        "latency_ms",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
    ):
        _non_negative_int(attempt[field_name], field_name)
    _non_negative_number(attempt["cost_estimate"], "cost_estimate")
    if not isinstance(attempt["paper_eligible"], bool):
        raise ValueError("paper_eligible must be a bool")
    for field_name in (
        "request_ref",
        "provenance_ref",
        "usage_ref",
        "model_execution_record_ref",
    ):
        if not isinstance(attempt[field_name], Mapping):
            raise ValueError(f"{field_name} must be an artifact reference")
    if attempt["raw_output_ref"] is not None and not isinstance(
        attempt["raw_output_ref"], Mapping
    ):
        raise ValueError("raw_output_ref must be an artifact reference or null")
    if not isinstance(item.get("record_ref"), Mapping):
        raise ValueError("model_execution_record_ref requires persisted record_ref")


def _validate_attempt_identity(
    record: Mapping[str, Any],
    attempt: Mapping[str, Any],
) -> None:
    for field_name in (
        "condition_id",
        "repeat_id",
        "run_id",
        "task_id",
        "unit_id",
        "attempt_id",
    ):
        if attempt[field_name] != record[field_name]:
            raise ValueError(f"attempt {field_name} does not match model record")


def _task_paper_eligibility(
    item: Mapping[str, Any],
    record: Mapping[str, Any],
) -> tuple[bool, str | None]:
    task = item.get("task")
    if not isinstance(task, Mapping):
        return False, "task_eligibility_missing"
    for field_name in ("condition_id", "repeat_id", "task_id"):
        if field_name not in task:
            raise ValueError(f"formal Experiment 5 task requires {field_name}")
        if task[field_name] != record[field_name]:
            raise ValueError(f"task {field_name} does not match model record")
    value = task.get("paper_eligible")
    if not isinstance(value, bool):
        return False, "task_eligibility_missing"
    if not value:
        return False, "task_not_paper_eligible"
    return True, None


def _mapping_sequence(value: Any, field_name: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a list or tuple")
    if any(not isinstance(item, Mapping) for item in value):
        raise ValueError(f"{field_name} entries must be mappings")
    return tuple(value)


def _provider_errors(
    item: Mapping[str, Any],
    attempt: Mapping[str, Any],
    provider_attempts: Sequence[Mapping[str, Any]],
) -> list[str]:
    if "provider_errors" not in item:
        raise ValueError("formal Experiment 5 join requires provider_errors")
    value = item["provider_errors"]
    if not isinstance(value, (list, tuple)):
        raise ValueError("provider_errors must be a list or tuple")
    errors = [str(item) for item in value if str(item)]
    if not errors and attempt.get("error_kind"):
        errors.append(str(attempt["error_kind"]))
    if not errors:
        errors.extend(
            str(provider_attempt.get("result_kind"))
            for provider_attempt in provider_attempts
            if provider_attempt.get("result_kind") not in {None, "succeeded"}
        )
    return list(dict.fromkeys(errors))


def _reasoning_controls_match(
    *,
    provider_family: str,
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
) -> bool:
    if dict(expected) == dict(actual):
        return True
    return (
        provider_family == "siliconflow"
        and dict(expected) == {}
        and dict(actual) == {"enable_thinking": False}
    )


def _required_row_string(field_name: str, value: Any) -> str:
    _require_non_empty_string(field_name, value)
    return str(value)


def _non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be an integer >= 0")
    return value


def _non_negative_number(value: Any, field_name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{field_name} must be a number >= 0")
    return float(value)


def _condition(
    *,
    member_plan: Mapping[str, Any],
    domain: str,
    difficulty: str,
    paper_difficulty: str,
    repeat_id: int,
    seed: int,
    catalog_digest: str,
) -> PaperExperimentCondition:
    member_id = str(member_plan["cohort_member_id"])
    return PaperExperimentCondition(
        schema_version="tokenshare.paper_condition.v2",
        experiment_id=EXP5_EXPERIMENT_ID,
        condition_id=(
            f"exp5_{member_id}_{domain}_{paper_difficulty}"
            f"_w{EXP5_WORKER_COUNT}_r{repeat_id}"
        ),
        domain=domain,
        difficulty=difficulty,
        paper_difficulty=paper_difficulty,
        topic_family=None,
        topic_family_version=(
            None if domain == "factorization" else "exp2_shared_2_2_1_v1"
        ),
        worker_count=EXP5_WORKER_COUNT,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        model_cohort_id=PAPER_MODEL_ENDPOINT_COHORT_ID,
        cohort_member_id=member_id,
        provider_config_id=str(member_plan["provider_config_id"]),
        model_entry_id=str(member_plan["selected_entry_id"]),
        provider_family=str(member_plan["provider_family"]),
        provider_model_id=str(member_plan["provider_model_id"]),
        reasoning_profile_id=str(member_plan["reasoning_profile_id"]),
        model_cohort_digest=str(member_plan["model_cohort_digest"]),
        source_provider_config_digest=str(
            member_plan["source_provider_config_digest"]
        ),
        model_endpoint_identity_digest=str(
            member_plan["model_endpoint_identity_digest"]
        ),
        repeat_id=repeat_id,
        seed=seed,
        catalog_digest=catalog_digest,
        real_transport_required=True,
        paper_eligible_required=True,
    )


def _selection_for_condition(
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
) -> FrozenCaseSelection:
    exp2 = _shared_exp2_slice(context.catalog)
    if condition.domain == "factorization":
        cases = _case_list(
            _mapping(exp2.get("factorization")).get(condition.paper_difficulty)
        )
        if len(cases) != EXP5_TASKS_PER_CONDITION:
            raise ValueError("Experiment 5 factorization slice must contain 5 roots")
        for case in cases:
            split_params = _mapping(case.get("split_params"))
            nested_parallelism_valid = (
                case.get("parallelism_scope") == "within_root_range_children"
            )
            formal_parallelism_valid = (
                split_params.get("strategy_id")
                == "factorization.candidate_range_partition.v1"
                and split_params.get("range_policy") == "contiguous"
            )
            if not nested_parallelism_valid and not formal_parallelism_valid:
                raise ValueError(
                    "Experiment 5 factorization requires range-child protocol tasks"
                )
        return FrozenCaseSelection(
            selection_id=f"exp5:factorization:{condition.paper_difficulty}:v1",
            experiment_id=EXP5_EXPERIMENT_ID,
            suite_version=_catalog_string(context.catalog, "suite_version"),
            catalog_version=_catalog_string(context.catalog, "catalog_version"),
            domain="factorization",
            paper_difficulty=str(condition.paper_difficulty),
            topic_family=None,
            ordered_case_ids=tuple(_case_id(case) for case in cases),
            catalog_digest=_catalog_digest(context.catalog),
            expected_ai_unit_count=sum(
                _case_expected_ai_unit_count(case) for case in cases
            ),
            paper_eligible_required=True,
        )

    topics = _mapping(
        _mapping(exp2.get("lean_proof")).get(condition.paper_difficulty)
    )
    cases: list[Mapping[str, Any]] = []
    for topic_family in LEAN_TOPIC_FAMILIES:
        topic_cases = _case_list(topics.get(topic_family))
        expected_count = LEAN_TOPIC_ALLOCATIONS[str(condition.paper_difficulty)][
            topic_family
        ]
        if len(topic_cases) != expected_count:
            raise ValueError("Experiment 5 Lean slice must preserve Exp2 2/2/1 IDs")
        cases.extend(topic_cases)
    return Exp5MixedLeanCaseSelection(
        selection_id=f"exp5:lean_proof:{condition.paper_difficulty}:v1",
        experiment_id=EXP5_EXPERIMENT_ID,
        suite_version=_catalog_string(context.catalog, "suite_version"),
        catalog_version=_catalog_string(context.catalog, "catalog_version"),
        domain="lean_proof",
        paper_difficulty=str(condition.paper_difficulty),
        topic_family=None,
        ordered_case_ids=tuple(_case_id(case) for case in cases),
        catalog_digest=_catalog_digest(context.catalog),
        expected_ai_unit_count=sum(
            _case_expected_ai_unit_count(case) for case in cases
        ),
        paper_eligible_required=True,
    )


def _canonical_condition(
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
) -> PaperExperimentCondition:
    canonical_by_id = {
        candidate.condition_id: candidate
        for candidate in expand_exp5_conditions(context)
    }
    canonical = canonical_by_id.get(condition.condition_id)
    if canonical is None or canonical.condition_digest != condition.condition_digest:
        raise ValueError("condition does not match canonical Experiment 5 identity")
    return canonical


def _validate_selection_shape(
    condition: PaperExperimentCondition,
    selection: FrozenCaseSelection,
) -> None:
    if selection.experiment_id != EXP5_EXPERIMENT_ID:
        raise ValueError("selection must belong to Experiment 5")
    if selection.domain != condition.domain:
        raise ValueError("selection domain does not match condition")
    if selection.paper_difficulty != condition.paper_difficulty:
        raise ValueError("selection difficulty does not match condition")
    if len(selection.ordered_case_ids) != EXP5_TASKS_PER_CONDITION:
        raise ValueError("Experiment 5 selection must contain exactly 5 roots")
    if selection.is_blocked:
        raise ValueError("formal Experiment 5 condition cannot use blocked selection")
    if condition.domain == "lean_proof":
        if not isinstance(selection, Exp5MixedLeanCaseSelection):
            raise ValueError("Experiment 5 Lean selection must retain mixed-topic metadata")
        if dict(selection.topic_family_counts) != LEAN_TOPIC_ALLOCATIONS[
            str(condition.paper_difficulty)
        ]:
            raise ValueError("Experiment 5 Lean topic allocation drift")


def _selection_body(selection: FrozenCaseSelection) -> JsonObject:
    return selection.to_dict()


def _validate_shared_slice(catalog: Any) -> None:
    exp2 = _shared_exp2_slice(catalog)
    expected_body = _slice_digest_body(exp2)
    catalog_mapping = _catalog_view(catalog)
    for section_name in ("exp4", "exp5"):
        section = catalog_mapping.get(section_name)
        if section is None:
            continue
        section_mapping = _mapping(section)
        observed_body = _slice_digest_body(section_mapping)
        if observed_body != expected_body:
            raise ValueError(f"Experiment 5 {section_name}/Exp2 slice drift")
        declared_digest = section_mapping.get("slice_digest")
        if declared_digest is not None and declared_digest != digest_json(observed_body):
            raise ValueError(f"Experiment 5 {section_name} slice digest drift")


def _slice_digest_body(section: Mapping[str, Any]) -> JsonObject:
    factorization = _mapping(section.get("factorization"))
    lean = _mapping(section.get("lean_proof"))
    return {
        "factorization": {
            difficulty: [
                _case_id(case)
                for case in _case_list(factorization.get(difficulty))
            ]
            for difficulty in FACTOR_PAPER_DIFFICULTIES
        },
        "lean_proof": {
            paper_difficulty: {
                topic_family: [
                    _case_id(case)
                    for case in _case_list(
                        _mapping(lean.get(paper_difficulty)).get(topic_family)
                    )
                ]
                for topic_family in LEAN_TOPIC_FAMILIES
            }
            for paper_difficulty in LEAN_PAPER_DIFFICULTIES
        },
    }


def _validate_cohort_preflight(value: Any) -> Exp5CohortGate:
    reasons: list[str] = []
    if not isinstance(value, Mapping):
        return Exp5CohortGate(
            member_plans=(),
            blocked_reasons=("missing_cohort_preflight",),
        )
    if (
        value.get("schema_version")
        != "tokenshare.paper_model_endpoint_cohort_preflight.v1"
    ):
        reasons.append("unsupported_cohort_preflight_schema")
    if value.get("status") != "planned":
        reasons.append("cohort_not_planned")
    if value.get("paper_eligible_possible") is not True:
        reasons.append("cohort_not_paper_eligible")
    if value.get("provider_calls_made") != 0:
        reasons.append("preflight_provider_calls_made")
    if value.get("cohort_id") != PAPER_MODEL_ENDPOINT_COHORT_ID:
        reasons.append("cohort_id_mismatch")
    cohort_digest = value.get("model_cohort_digest")
    if not _is_complete_digest(cohort_digest):
        reasons.append("invalid_model_cohort_digest")
    expected_ids = tuple(PAPER_MODEL_ENDPOINT_COHORT_MEMBER_IDS)
    actual_expected_ids = value.get("expected_member_ids")
    if not isinstance(actual_expected_ids, (list, tuple)) or tuple(
        actual_expected_ids
    ) != expected_ids:
        reasons.append("cohort_member_order_mismatch")
    member_plans = value.get("member_plans")
    if not isinstance(member_plans, Mapping) or set(member_plans) != set(expected_ids):
        reasons.append("cohort_member_set_mismatch")
        return Exp5CohortGate(
            member_plans=(),
            blocked_reasons=tuple(dict.fromkeys(reasons)),
        )

    normalized_plans: list[JsonObject] = []
    namespaces: set[tuple[str, str]] = set()
    for member_id in expected_ids:
        plan = member_plans.get(member_id)
        if not isinstance(plan, Mapping):
            reasons.append(f"{member_id}:missing_member_plan")
            continue
        expected = PAPER_MODEL_ENDPOINT_COHORT_MEMBERS[member_id]
        required_strings = (
            "cohort_id",
            "model_cohort_digest",
            "cohort_member_id",
            "provider_config_id",
            "selected_entry_id",
            "provider_family",
            "provider_model_id",
            "reasoning_profile_id",
            "source_provider_config_digest",
            "model_endpoint_identity_digest",
        )
        if plan.get("status") != "planned" or plan.get("blocked_reasons") not in (
            [],
            (),
            None,
        ):
            reasons.append(f"{member_id}:member_not_planned")
        if any(
            not isinstance(plan.get(field_name), str)
            or not str(plan.get(field_name)).strip()
            for field_name in required_strings
        ):
            reasons.append(f"{member_id}:incomplete_member_identity")
            continue
        if (
            plan["cohort_id"] != PAPER_MODEL_ENDPOINT_COHORT_ID
            or plan["model_cohort_digest"] != cohort_digest
            or plan["cohort_member_id"] != member_id
            or plan["provider_family"] != expected["provider_family"]
            or plan["provider_model_id"] != expected["provider_model_id"]
            or plan["reasoning_profile_id"] != expected["reasoning_profile_id"]
        ):
            reasons.append(f"{member_id}:member_identity_mismatch")
        if not _is_complete_digest(plan["source_provider_config_digest"]):
            reasons.append(f"{member_id}:invalid_source_config_digest")
        if not _is_complete_digest(plan["model_endpoint_identity_digest"]):
            reasons.append(f"{member_id}:invalid_endpoint_identity_digest")
        namespace = (str(plan["provider_config_id"]), str(plan["selected_entry_id"]))
        if namespace in namespaces:
            reasons.append("entry_namespace_conflict")
        namespaces.add(namespace)
        endpoint = plan.get("endpoint_identity")
        endpoint_identity = _endpoint_identity_from_mapping(endpoint)
        if endpoint_identity is None:
            reasons.append(f"{member_id}:incomplete_reasoning_identity")
        else:
            if endpoint_identity.to_dict() != dict(endpoint):
                reasons.append(f"{member_id}:endpoint_identity_digest_mismatch")
            for field_name in (
                "model_cohort_id",
                "model_cohort_digest",
                "cohort_member_id",
                "provider_config_id",
                "selected_entry_id",
                "provider_family",
                "provider_model_id",
                "reasoning_profile_id",
                "source_provider_config_digest",
                "model_endpoint_identity_digest",
            ):
                plan_field = "cohort_id" if field_name == "model_cohort_id" else field_name
                if endpoint_identity.to_dict()[field_name] != plan[plan_field]:
                    reasons.append(f"{member_id}:{field_name}_mismatch")
            controls = dict(endpoint_identity.effective_reasoning_controls)
            if endpoint_identity.provider_family == "openai":
                if controls != {"reasoning_effort": "high"}:
                    reasons.append(f"{member_id}:reasoning_controls_mismatch")
            elif controls not in ({}, {"enable_thinking": False}):
                reasons.append(f"{member_id}:reasoning_controls_mismatch")
        normalized_plans.append(dict(plan))

    return Exp5CohortGate(
        member_plans=tuple(normalized_plans) if not reasons else (),
        blocked_reasons=tuple(dict.fromkeys(reasons)),
    )


def _single_member_plan_reasons(
    *,
    plan: Mapping[str, Any],
    cohort_member_id: str,
    cohort_digest: Any,
) -> tuple[str, ...]:
    reasons: list[str] = []
    expected = PAPER_MODEL_ENDPOINT_COHORT_MEMBERS[cohort_member_id]
    required_fields = (
        "cohort_id",
        "model_cohort_digest",
        "cohort_member_id",
        "provider_config_id",
        "selected_entry_id",
        "provider_family",
        "provider_model_id",
        "reasoning_profile_id",
        "source_provider_config_digest",
        "model_endpoint_identity_digest",
    )
    if plan.get("status") != "planned":
        reasons.append("member_not_planned")
    if any(
        not isinstance(plan.get(field_name), str)
        or not str(plan.get(field_name)).strip()
        for field_name in required_fields
    ):
        return ("incomplete_member_identity",)
    if (
        plan["cohort_id"] != PAPER_MODEL_ENDPOINT_COHORT_ID
        or plan["model_cohort_digest"] != cohort_digest
        or plan["cohort_member_id"] != cohort_member_id
        or plan["provider_family"] != expected["provider_family"]
        or plan["provider_model_id"] != expected["provider_model_id"]
        or plan["reasoning_profile_id"] != expected["reasoning_profile_id"]
    ):
        reasons.append("member_identity_mismatch")
    if not _is_complete_digest(plan["source_provider_config_digest"]):
        reasons.append("invalid_source_config_digest")
    if not _is_complete_digest(plan["model_endpoint_identity_digest"]):
        reasons.append("invalid_endpoint_identity_digest")
    endpoint = plan.get("endpoint_identity")
    identity = _endpoint_identity_from_mapping(endpoint)
    if identity is None:
        reasons.append("incomplete_reasoning_identity")
        return tuple(dict.fromkeys(reasons))
    identity_body = identity.to_dict()
    if identity_body != dict(endpoint):
        reasons.append("endpoint_identity_digest_mismatch")
    plan_field_by_identity_field = {
        "model_cohort_id": "cohort_id",
        "model_cohort_digest": "model_cohort_digest",
        "cohort_member_id": "cohort_member_id",
        "provider_config_id": "provider_config_id",
        "selected_entry_id": "selected_entry_id",
        "provider_family": "provider_family",
        "provider_model_id": "provider_model_id",
        "reasoning_profile_id": "reasoning_profile_id",
        "source_provider_config_digest": "source_provider_config_digest",
        "model_endpoint_identity_digest": "model_endpoint_identity_digest",
    }
    for identity_field, plan_field in plan_field_by_identity_field.items():
        if identity_body[identity_field] != plan[plan_field]:
            reasons.append(f"{identity_field}_mismatch")
    controls = dict(identity.effective_reasoning_controls)
    if identity.provider_family == "openai":
        if controls != {"reasoning_effort": "high"}:
            reasons.append("reasoning_controls_mismatch")
    elif controls not in ({}, {"enable_thinking": False}):
        reasons.append("reasoning_controls_mismatch")
    return tuple(dict.fromkeys(reasons))


def _endpoint_identity_from_mapping(value: Any) -> PaperModelEndpointIdentity | None:
    if not isinstance(value, Mapping):
        return None
    if value.get("schema_version") != "tokenshare.paper_model_endpoint_identity.v1":
        return None
    try:
        controls = value["effective_reasoning_controls"]
        if not isinstance(controls, Mapping):
            return None
        return PaperModelEndpointIdentity(
            schema_version=str(value["schema_version"]),
            model_cohort_id=str(value["model_cohort_id"]),
            model_cohort_digest=str(value["model_cohort_digest"]),
            cohort_member_id=str(value["cohort_member_id"]),
            provider_config_id=str(value["provider_config_id"]),
            selected_entry_id=str(value["selected_entry_id"]),
            provider_family=str(value["provider_family"]),
            provider_model_id=str(value["provider_model_id"]),
            reasoning_profile_id=str(value["reasoning_profile_id"]),
            effective_reasoning_controls=dict(controls),
            source_provider_config_digest=str(
                value["source_provider_config_digest"]
            ),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _shared_exp2_slice(catalog: Any) -> Mapping[str, Any]:
    catalog_view = _catalog_view(catalog)
    exp2 = catalog_view.get("exp2")
    if isinstance(exp2, Mapping):
        return exp2
    if all(
        field_name in catalog_view
        for field_name in (
            "factorization_cases",
            "lean_cases",
            "lean_lemma_graph_cases",
        )
    ):
        return _formal_exp2_slice(catalog_view)
    raise ValueError("Experiment 5 requires the shared Experiment 2 slice")


def _formal_exp2_slice(catalog: Mapping[str, Any]) -> JsonObject:
    factorization_cases = _formal_catalog_cases(catalog, domain="factorization")
    lean_cases = _formal_catalog_cases(catalog, domain="lean_proof")
    factorization: dict[str, list[Mapping[str, Any]]] = {}
    for difficulty in FACTOR_PAPER_DIFFICULTIES:
        selected = [
            case
            for case in factorization_cases
            if case.get("paper_difficulty", case.get("difficulty")) == difficulty
        ][:EXP5_TASKS_PER_CONDITION]
        if len(selected) != EXP5_TASKS_PER_CONDITION:
            raise ValueError(
                "formal factorization catalog must expose 5 roots per difficulty"
            )
        factorization[difficulty] = selected

    readiness = _validated_readiness_selection(catalog)
    cases_by_id = {_case_id(case): case for case in lean_cases}
    lean: dict[str, dict[str, list[Mapping[str, Any]]]] = {}
    for paper_difficulty in LEAN_PAPER_DIFFICULTIES:
        by_topic: dict[str, list[Mapping[str, Any]]] = {}
        for topic_family, expected_count in LEAN_TOPIC_ALLOCATIONS[
            paper_difficulty
        ].items():
            cell_key = f"{paper_difficulty}/{topic_family}"
            selected_ids = readiness["selected_case_ids_by_cell"][cell_key][
                :expected_count
            ]
            selected_cases: list[Mapping[str, Any]] = []
            for case_id in selected_ids:
                case = cases_by_id.get(case_id)
                if case is None:
                    raise ValueError(
                        "Lean readiness selection references unknown catalog case"
                    )
                if (
                    case.get("paper_difficulty") != paper_difficulty
                    or case.get("topic_family") != topic_family
                ):
                    raise ValueError("Lean readiness selection metadata drift")
                selected_cases.append(case)
            if len(selected_cases) != expected_count:
                raise ValueError("Experiment 5 Lean topic slice count drift")
            by_topic[topic_family] = selected_cases
        lean[paper_difficulty] = by_topic
    return {
        "factorization": factorization,
        "lean_proof": lean,
        "readiness_selection_digest": readiness["selection_digest"],
    }


def _formal_catalog_cases(
    catalog: Mapping[str, Any],
    *,
    domain: str,
) -> tuple[Mapping[str, Any], ...]:
    if domain == "factorization":
        raw_cases = catalog.get("factorization_cases")
    else:
        raw_cases = tuple(catalog.get("lean_cases", ())) + tuple(
            catalog.get("lean_lemma_graph_cases", ())
        )
    cases = _case_list(raw_cases)
    case_ids = tuple(_case_id(case) for case in cases)
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("formal paper catalog contains duplicate case IDs")
    return cases


def _validated_readiness_selection(catalog: Mapping[str, Any]) -> JsonObject:
    readiness = catalog.get("task15_budget_input")
    if readiness is None:
        wrapper = catalog.get("lean_task14_readiness")
        if isinstance(wrapper, Mapping):
            readiness = wrapper.get("task15_budget_input")
    if not isinstance(readiness, Mapping):
        raise ValueError("Lean readiness selection is not formal/executable")
    catalog_digest = _catalog_digest(catalog)
    if (
        readiness.get("schema_version")
        != "tokenshare.lean_task15_budget_input.v1"
        or readiness.get("target_case_count") != 15
        or readiness.get("selected_case_count") != 135
        or readiness.get("executable_cell_count") != 9
        or readiness.get("blocked_cell_count") != 0
        or readiness.get("provider_calls_made") != 0
        or readiness.get("catalog_digest") != catalog_digest
    ):
        raise ValueError("Lean readiness selection is not formal/executable")
    expected_cells = {
        f"{difficulty}/{topic_family}"
        for difficulty in LEAN_PAPER_DIFFICULTIES
        for topic_family in LEAN_TOPIC_FAMILIES
    }
    selected_by_cell = readiness.get("selected_case_ids_by_cell")
    if not isinstance(selected_by_cell, Mapping) or set(selected_by_cell) != expected_cells:
        raise ValueError("Lean readiness selection cell matrix drift")
    normalized_selected: dict[str, list[str]] = {}
    seen_ids: set[str] = set()
    for cell_key in sorted(expected_cells):
        case_ids = _normalize_case_ids(selected_by_cell[cell_key])
        if len(case_ids) != 15 or any(case_id in seen_ids for case_id in case_ids):
            raise ValueError("Lean readiness must freeze 15 unique cases per cell")
        seen_ids.update(case_ids)
        normalized_selected[cell_key] = list(case_ids)
    semantic_by_cell = _digest_lists_by_cell(
        readiness.get("semantic_fingerprint_digests_by_cell"),
        expected_cells=expected_cells,
        expected_count=15,
    )
    golden_value = readiness.get("golden_case_ids_by_cell")
    if not isinstance(golden_value, Mapping) or set(golden_value) != expected_cells:
        raise ValueError("Lean readiness golden-case matrix drift")
    golden_by_cell: dict[str, list[str]] = {}
    for cell_key in sorted(expected_cells):
        golden_ids = _normalize_case_ids(golden_value[cell_key])
        if not golden_ids or any(
            case_id not in normalized_selected[cell_key] for case_id in golden_ids
        ):
            raise ValueError("Lean readiness golden-case provenance drift")
        golden_by_cell[cell_key] = list(golden_ids)
    oracle_digests = readiness.get("oracle_package_digests")
    if not isinstance(oracle_digests, Mapping) or not oracle_digests or any(
        not _is_complete_digest(value) for value in oracle_digests.values()
    ):
        raise ValueError("Lean readiness oracle package digest drift")
    environment_digest = readiness.get("environment_digest")
    _require_complete_digest("environment_digest", environment_digest)
    selection_digest = digest_json(
        {
            "schema_version": "tokenshare.lean_task14_selected_cases.v1",
            "catalog_digest": catalog_digest,
            "environment_digest": environment_digest,
            "oracle_package_digests": dict(oracle_digests),
            "target_case_count": 15,
            "selected_case_ids_by_cell": normalized_selected,
            "semantic_fingerprint_digests_by_cell": semantic_by_cell,
            "golden_case_ids_by_cell": golden_by_cell,
        }
    )
    if (
        readiness.get("selection_digest") != selection_digest
        or readiness.get("catalog_slice_digest") != selection_digest
    ):
        raise ValueError("Lean readiness selection digest mismatch")
    return {**dict(readiness), "selected_case_ids_by_cell": normalized_selected}


def _digest_lists_by_cell(
    value: Any,
    *,
    expected_cells: set[str],
    expected_count: int,
) -> dict[str, list[str]]:
    if not isinstance(value, Mapping) or set(value) != expected_cells:
        raise ValueError("Lean readiness semantic fingerprint matrix drift")
    normalized: dict[str, list[str]] = {}
    for cell_key in sorted(expected_cells):
        digests = list(value[cell_key]) if isinstance(value[cell_key], (list, tuple)) else []
        if len(digests) != expected_count or any(
            not _is_complete_digest(digest) for digest in digests
        ):
            raise ValueError("Lean readiness semantic fingerprint digest drift")
        normalized[cell_key] = digests
    return normalized


def _catalog_view(catalog: Any) -> Mapping[str, Any]:
    if isinstance(catalog, Mapping):
        return catalog
    fields = (
        "catalog_id",
        "catalog_version",
        "catalog_digest",
        "suite_version",
        "factorization_cases",
        "lean_cases",
        "lean_lemma_graph_cases",
        "task15_budget_input",
        "lean_task14_readiness",
        "exp2",
        "exp4",
        "exp5",
    )
    view = {
        field_name: getattr(catalog, field_name)
        for field_name in fields
        if hasattr(catalog, field_name)
    }
    if not view:
        raise ValueError("catalog must expose formal paper catalog fields")
    return view


def _catalog_digest(catalog: Any) -> str:
    catalog_view = _catalog_view(catalog)
    value = catalog_view.get("catalog_digest")
    _require_complete_digest("catalog_digest", value)
    if all(
        field_name in catalog_view
        for field_name in (
            "factorization_cases",
            "lean_cases",
            "lean_lemma_graph_cases",
        )
    ):
        digest_body = {
            "catalog_id": str(
                catalog_view.get("catalog_id") or "tokenshare.paper.catalog"
            ),
            "catalog_version": str(
                catalog_view.get("catalog_version") or EXP5_CATALOG_VERSION
            ),
            "factorization_cases": list(catalog_view["factorization_cases"]),
            "lean_cases": list(catalog_view["lean_cases"]),
            "lean_lemma_graph_cases": list(
                catalog_view["lean_lemma_graph_cases"]
            ),
        }
        if digest_json(digest_body) != value:
            raise ValueError("formal paper catalog digest mismatch")
    return str(value)


def _catalog_string(catalog: Any, field_name: str) -> str:
    value = _catalog_view(catalog).get(field_name)
    if value is None and field_name == "suite_version":
        value = EXP5_SUITE_VERSION
    _require_non_empty_string(field_name, value)
    return str(value)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _case_list(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    if any(not isinstance(case, Mapping) for case in value):
        raise ValueError("catalog cases must be mappings")
    return tuple(value)


def _case_id(case: Mapping[str, Any]) -> str:
    value = case.get("case_id")
    _require_non_empty_string("case_id", value)
    return str(value)


def _positive_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("expected_ai_unit_count must be a positive integer")
    return value


def _case_expected_ai_unit_count(case: Mapping[str, Any]) -> int:
    if "expected_ai_unit_count" in case:
        return _positive_int(case.get("expected_ai_unit_count"))
    if case.get("schema_version") == "tokenshare.paper_factorization_case.v1":
        return _positive_int(
            _mapping(case.get("split_params")).get("requested_child_count")
        )
    return _positive_int(case.get("expected_child_count"))


def _normalize_case_ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("ordered_case_ids must be a list or tuple")
    if any(not isinstance(case_id, str) or not case_id.strip() for case_id in value):
        raise ValueError("case ids must be non-empty strings")
    result = tuple(value)
    if len(set(result)) != len(result):
        raise ValueError("duplicate case id in selection")
    return result


def _require_non_empty_string(field_name: str, value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _is_complete_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 71
        and value.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def _require_complete_digest(field_name: str, value: Any) -> None:
    if not _is_complete_digest(value):
        raise ValueError(f"{field_name} must be a complete sha256 digest")


__all__ = [
    "EXP5_EXPECTED_ROOT_RUNS",
    "EXP5_EXPERIMENT_ID",
    "Exp5CohortGate",
    "Exp5MixedLeanCaseSelection",
    "Exp5ModelComparisonModule",
    "Experiment5ModelComparisonModule",
    "assess_exp5_cohort",
    "build_exp5_model_execution_rows",
    "build_exp5_single_member_pilot_plan",
    "count_exp5_root_runs",
    "expand_conditions",
    "expand_exp5_conditions",
    "freeze_case_selections",
    "freeze_exp5_case_selections",
    "run_condition",
    "summarize",
    "summarize_exp5_model_comparison",
]
