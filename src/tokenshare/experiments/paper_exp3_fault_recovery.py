"""Experiment 3 fault/recovery module.

本模块只冻结 Prompt E 的实验矩阵、case selection 和 summary 契约。
真实 provider 调用、共享 runner 接入、CSV/report 写盘留给 Gate C owner。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from tokenshare.core.models import ArtifactRef
from tokenshare.experiments.paper_experiment_contracts import (
    ExperimentSummaryRows,
    FrozenCaseSelection,
    PaperExecutionContext,
)
from tokenshare.experiments.paper_faults import (
    FaultInjectionOutcome,
    FaultTargetDescriptor,
    inject_post_ai_fault,
    select_fault_targets,
)
from tokenshare.experiments.paper_models import (
    JsonObject,
    LEAN_TOPIC_FAMILIES,
    PaperAttemptResult,
    PaperConditionResult,
    PaperExperimentCondition,
    PaperStatus,
    digest_json,
    evaluate_paper_eligibility,
)
from tokenshare.experiments.paper_workers import WORKER_DEATH_RECORD_SCHEMA_VERSION
from tokenshare.storage.artifacts import ArtifactStore


EXP3_EXPERIMENT_ID = "exp3_real_ai_fault_recovery"
EXP3_SCHEMA_VERSION = "tokenshare.paper_exp3_fault_recovery.v1"
EXP3_FAULT_EVIDENCE_SCHEMA_VERSION = "tokenshare.paper_exp3_fault_evidence.v1"
PAPER_CONDITION_V2 = "tokenshare.paper_condition.v2"

BASELINE_PROVIDER_FAMILY = "siliconflow"
BASELINE_PROVIDER_MODEL_ID = "zai-org/GLM-5.2"
BASELINE_MODEL_ENTRY_ID = "glm_5_2_exp1_baseline"
BASELINE_PROVIDER_CONFIG_ID = "exp1_baseline_siliconflow"
BASELINE_REASONING_PROFILE_ID = "temperature_0_thinking_false"
BASELINE_REQUEST_LIMIT_POLICY = {
    "max_tokens": 1024,
    "timeout_seconds": 30,
    "max_provider_attempts": 1,
    "temperature": 0.0,
    "enable_thinking": False,
}

RATE_FAULT_TYPES = (
    "false_positive",
    "false_negative",
    "no_return",
    "late_submission",
    "executor_error",
)
VALID_FAULT_INJECTION_POINTS = frozenset(
    {
        "after_parsed_candidate_before_verification",
        "after_raw_output_before_submission",
        "after_raw_output_late_submission",
        "after_raw_output_before_parser_bridge",
    }
)
FAULT_INJECTION_POINT_BY_TYPE = {
    "false_positive": "after_parsed_candidate_before_verification",
    "false_negative": "after_parsed_candidate_before_verification",
    "no_return": "after_raw_output_before_submission",
    "late_submission": "after_raw_output_late_submission",
    "executor_error": "after_raw_output_before_parser_bridge",
}
FACTOR_RATE_FAULT_RATES_PERCENT = (0, 1, 5, 10, 25, 50, 100)
LEAN_RATE_FAULT_RATES_PERCENT = (0, 10, 50, 100)
REPEAT_IDS = (0, 1, 2)

WORKER_DEATH_WORKER_COUNT = 10
WORKER_DEATH_COUNTS = (1, 3)
WORKER_DEATH_KILL_PROGRESS_PERCENT = (25, 50, 75)
RATE_FAULT_TARGET_SEED = 300300
EXPECTED_ROOT_RUN_COUNTS = {
    "rate_fault_factorization": 525,
    "rate_fault_lean_proof": 180,
    "worker_death": 108,
    "total": 813,
}

_FACTOR_DIFFICULTIES = ("easy", "medium", "hard")
_RATE_FACTOR_SELECTION_ID = "exp3_rate_fault_factorization_slice_v1"


class _MissingCatalogSlice(ValueError):
    pass


@dataclass(frozen=True, kw_only=True)
class _ConditionKey:
    matrix_kind: str
    domain: str
    task_slice_key: str
    difficulty: str
    paper_difficulty: str
    topic_family: str | None
    fault_type: str
    fault_rate_percent: int
    repeat_id: int
    dead_worker_count: int | None = None
    kill_progress_percent: int | None = None


@dataclass(frozen=True, kw_only=True)
class Exp3FaultInjectionOutcome:
    primitive_outcome: FaultInjectionOutcome
    record: JsonObject
    record_ref: ArtifactRef
    mutation_provenance_ref: ArtifactRef


class Exp3MixedLeanCaseSelection(FrozenCaseSelection):
    """Exp3 rate-fault Lean slices contain one root from each topic family."""

    def __post_init__(self) -> None:
        if self.domain != "lean_proof" or self.topic_family is not None:
            super().__post_init__()
            return
        if self.paper_difficulty != "medium_lemma_dag":
            raise ValueError("Exp3 mixed Lean selection must use medium_lemma_dag")
        for field_name in (
            "selection_id",
            "experiment_id",
            "suite_version",
            "catalog_version",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field_name} must be a non-empty string")
        _validate_complete_digest("catalog_digest", self.catalog_digest)
        if self.paper_eligible_required is not True:
            raise ValueError("Exp3 selection must require paper eligibility")
        if self.blocked_reason is not None:
            if (
                not isinstance(self.blocked_reason, str)
                or not self.blocked_reason
                or tuple(self.ordered_case_ids)
                or self.expected_ai_unit_count != 0
            ):
                raise ValueError("invalid blocked Exp3 mixed Lean selection")
            object.__setattr__(self, "ordered_case_ids", ())
            return
        case_ids = _case_tuple(self.ordered_case_ids)
        if len(case_ids) != 3:
            raise ValueError("Exp3 mixed Lean selection must contain 3 cases")
        object.__setattr__(self, "ordered_case_ids", case_ids)
        if (
            isinstance(self.expected_ai_unit_count, bool)
            or not isinstance(self.expected_ai_unit_count, int)
            or self.expected_ai_unit_count < 1
        ):
            raise ValueError("expected_ai_unit_count must be an integer >= 1")


class Experiment3FaultRecoveryModule:
    """Gate B compatible wrapper for Prompt E."""

    def expand_conditions(
        self,
        context: PaperExecutionContext,
    ) -> tuple[PaperExperimentCondition, ...]:
        return expand_exp3_conditions(
            catalog_digest=_catalog_digest(context.catalog),
            endpoint_identity=_validate_approved_endpoint_binding(context),
        )

    def freeze_case_selections(
        self,
        context: PaperExecutionContext,
        conditions: tuple[PaperExperimentCondition, ...],
    ) -> tuple[FrozenCaseSelection, ...]:
        return freeze_exp3_case_selections(context, conditions)

    def run_condition(
        self,
        context: PaperExecutionContext,
        condition: PaperExperimentCondition,
        selection: FrozenCaseSelection,
    ) -> PaperConditionResult:
        canonical_condition = _canonical_condition_for(context, condition)
        _validate_canonical_selection(
            context=context,
            condition=canonical_condition,
            selection=selection,
        )
        if selection.is_blocked:
            return PaperConditionResult(
                condition_id=canonical_condition.condition_id,
                status=PaperStatus.BLOCKED,
                repeat_count=1,
                task_count=0,
                completed_root_count=0,
                failed_root_count=0,
                blocked_root_count=1,
                provider_attempt_count=0,
                metrics_ref=None,
            )
        result = context.execution_callback(
            condition=canonical_condition,
            selection=selection,
        )
        if not isinstance(result, PaperConditionResult):
            raise ValueError("execution callback must return PaperConditionResult")
        if result.condition_id != canonical_condition.condition_id:
            raise ValueError("execution callback returned a mismatched condition_id")
        return result

    def summarize(self, evidence: Mapping[str, Any]) -> ExperimentSummaryRows:
        return summarize_exp3(evidence)


def inject_exp3_post_ai_fault(
    *,
    artifact_store: ArtifactStore,
    attempt: PaperAttemptResult,
    fault_type: str,
    seed: int,
    created_at: str,
    lease_deadline_at: str | None = None,
    submitted_at: str | None = None,
    fault_target: FaultTargetDescriptor | JsonObject | None = None,
) -> Exp3FaultInjectionOutcome:
    raw_ref = _required_persisted_artifact_ref(
        artifact_store,
        attempt.raw_output_ref,
        "fault injection requires persisted raw output",
    )
    provenance_ref = _required_persisted_artifact_ref(
        artifact_store,
        attempt.provenance_ref,
        "fault injection requires persisted provenance",
    )
    injected_at = _parse_timestamp(created_at, "created_at")
    for field_name, artifact_ref in (
        ("raw output", raw_ref),
        ("provenance", provenance_ref),
    ):
        if _parse_timestamp(artifact_ref.created_at, f"{field_name} created_at") > injected_at:
            raise ValueError(f"{field_name} must be persisted before fault injection")

    primitive_outcome = inject_post_ai_fault(
        artifact_store=artifact_store,
        attempt=attempt,
        fault_type=fault_type,
        seed=seed,
        created_at=created_at,
        lease_deadline_at=lease_deadline_at,
        submitted_at=submitted_at,
        fault_target=fault_target,
    )
    primitive_record = primitive_outcome.record.to_dict()
    mutation_provenance_ref = artifact_store.save_json(
        {
            "schema_version": "tokenshare.paper_exp3_mutation_provenance.v1",
            "fault_id": primitive_record["fault_id"],
            "condition_id": attempt.condition_id,
            "repeat_id": attempt.repeat_id,
            "attempt_id": attempt.attempt_id,
            "original_provenance_ref": provenance_ref.to_dict(),
            "primitive_fault_record_ref": primitive_outcome.record_ref.to_dict(),
            "mutated_output_ref": primitive_outcome.mutated_output_ref.to_dict(),
            "provider_tokens_attributed": 0,
            "created_at": created_at,
        },
        artifact_id=f"{primitive_record['fault_id']}_exp3_mutation_provenance",
        artifact_type="FaultMutationProvenance",
        artifact_schema_id="tokenshare.paper_exp3_mutation_provenance",
        artifact_schema_version="v1",
        source={
            "kind": "paper_exp3_fault_adapter",
            "fault_id": primitive_record["fault_id"],
        },
        metadata={
            "condition_id": attempt.condition_id,
            "attempt_id": attempt.attempt_id,
        },
        created_at=created_at,
    )
    record: JsonObject = {
        **primitive_record,
        "exp3_evidence_schema_version": EXP3_FAULT_EVIDENCE_SCHEMA_VERSION,
        "primitive_fault_record_ref": primitive_outcome.record_ref.to_dict(),
        "original_provenance_ref": provenance_ref.to_dict(),
        "mutated_provenance_ref": mutation_provenance_ref.to_dict(),
        "raw_persisted_at": raw_ref.created_at,
        "provenance_persisted_at": provenance_ref.created_at,
        "injected_at": created_at,
        "mutation_provenance_persisted_at": mutation_provenance_ref.created_at,
    }
    record_ref = artifact_store.save_json(
        record,
        artifact_id=f"{primitive_record['fault_id']}_exp3_evidence",
        artifact_type="Exp3FaultInjectionEvidence",
        artifact_schema_id="tokenshare.paper_exp3_fault_evidence",
        artifact_schema_version="v1",
        source={
            "kind": "paper_exp3_fault_adapter",
            "fault_id": primitive_record["fault_id"],
        },
        metadata={
            "condition_id": attempt.condition_id,
            "attempt_id": attempt.attempt_id,
        },
        created_at=created_at,
    )
    return Exp3FaultInjectionOutcome(
        primitive_outcome=primitive_outcome,
        record=record,
        record_ref=record_ref,
        mutation_provenance_ref=mutation_provenance_ref,
    )


def expand_exp3_conditions(
    *,
    catalog_digest: str = "sha256:" + "0" * 64,
    endpoint_identity: Mapping[str, Any] | None = None,
) -> tuple[PaperExperimentCondition, ...]:
    _validate_complete_digest("catalog_digest", catalog_digest)
    identity = _normalized_endpoint_identity(endpoint_identity)
    conditions: list[PaperExperimentCondition] = []
    for fault_type in RATE_FAULT_TYPES:
        for rate_percent in FACTOR_RATE_FAULT_RATES_PERCENT:
            for repeat_id in REPEAT_IDS:
                conditions.append(
                    _condition_from_key(
                        _ConditionKey(
                            matrix_kind="rate_fault",
                            domain="factorization",
                            task_slice_key="factorization",
                            difficulty="medium",
                            paper_difficulty="medium",
                            topic_family=None,
                            fault_type=fault_type,
                            fault_rate_percent=rate_percent,
                            repeat_id=repeat_id,
                        ),
                        catalog_digest=catalog_digest,
                        endpoint_identity=identity,
                    )
                )
    for fault_type in RATE_FAULT_TYPES:
        for rate_percent in LEAN_RATE_FAULT_RATES_PERCENT:
            for repeat_id in REPEAT_IDS:
                conditions.append(
                    _condition_from_key(
                        _ConditionKey(
                            matrix_kind="rate_fault",
                            domain="lean_proof",
                            task_slice_key="all_topics",
                            difficulty="medium",
                            paper_difficulty="medium_lemma_dag",
                            topic_family=None,
                            fault_type=fault_type,
                            fault_rate_percent=rate_percent,
                            repeat_id=repeat_id,
                        ),
                        catalog_digest=catalog_digest,
                        endpoint_identity=identity,
                    )
                )
    for difficulty in _FACTOR_DIFFICULTIES:
        for dead_count in WORKER_DEATH_COUNTS:
            for kill_progress in WORKER_DEATH_KILL_PROGRESS_PERCENT:
                for repeat_id in REPEAT_IDS:
                    conditions.append(
                        _condition_from_key(
                            _ConditionKey(
                                matrix_kind="worker_death",
                                domain="factorization",
                                task_slice_key=difficulty,
                                difficulty=difficulty,
                                paper_difficulty=difficulty,
                                topic_family=None,
                                fault_type="worker_death",
                                fault_rate_percent=0,
                                repeat_id=repeat_id,
                                dead_worker_count=dead_count,
                                kill_progress_percent=kill_progress,
                            ),
                            catalog_digest=catalog_digest,
                            endpoint_identity=identity,
                        )
                    )
    for topic_family in LEAN_TOPIC_FAMILIES:
        for dead_count in WORKER_DEATH_COUNTS:
            for kill_progress in WORKER_DEATH_KILL_PROGRESS_PERCENT:
                for repeat_id in REPEAT_IDS:
                    conditions.append(
                        _condition_from_key(
                            _ConditionKey(
                                matrix_kind="worker_death",
                                domain="lean_proof",
                                task_slice_key=topic_family,
                                difficulty="medium",
                                paper_difficulty="medium_lemma_dag",
                                topic_family=topic_family,
                                fault_type="worker_death",
                                fault_rate_percent=0,
                                repeat_id=repeat_id,
                                dead_worker_count=dead_count,
                                kill_progress_percent=kill_progress,
                            ),
                            catalog_digest=catalog_digest,
                            endpoint_identity=identity,
                        )
                    )
    return tuple(conditions)


def freeze_exp3_case_selections(
    context: PaperExecutionContext,
    conditions: Sequence[PaperExperimentCondition],
) -> tuple[FrozenCaseSelection, ...]:
    catalog = _catalog_mapping(context.catalog)
    selections = tuple(
        _selection_for_condition(condition, catalog=catalog)
        for condition in conditions
    )
    validate_exp3_condition_matrix(conditions, selections, catalog=catalog)
    return selections


def build_exp3_plan_manifest(
    conditions: Sequence[PaperExperimentCondition],
    selections: Sequence[FrozenCaseSelection],
    *,
    catalog: Mapping[str, Any],
) -> JsonObject:
    validate_exp3_condition_matrix(conditions, selections, catalog=catalog)
    root_counts = {
        "rate_fault_factorization": 0,
        "rate_fault_lean_proof": 0,
        "worker_death": 0,
        "total": 0,
    }
    target_rows: list[JsonObject] = []
    ai_units_by_case_id = _ai_units_by_case_id(catalog)

    for condition, selection in zip(conditions, selections, strict=True):
        key = _parse_condition_id(condition.condition_id)
        case_count = len(selection.ordered_case_ids)
        if key.matrix_kind == "rate_fault" and key.domain == "factorization":
            root_counts["rate_fault_factorization"] += case_count
        elif key.matrix_kind == "rate_fault" and key.domain == "lean_proof":
            root_counts["rate_fault_lean_proof"] += case_count
        elif key.matrix_kind == "worker_death":
            root_counts["worker_death"] += case_count
        root_counts["total"] += case_count

        if key.matrix_kind == "rate_fault":
            unit_ids = tuple(
                unit_id
                for case_id in selection.ordered_case_ids
                for unit_id in ai_units_by_case_id[str(case_id)]
            )
            selected_targets = select_fault_targets(
                unit_ids,
                fault_rate=key.fault_rate_percent / 100.0,
                seed=RATE_FAULT_TARGET_SEED,
            )
            target_rows.append(
                {
                    "schema_version": "tokenshare.paper_exp3_fault_target_manifest.v1",
                    "condition_id": condition.condition_id,
                    "matrix_kind": key.matrix_kind,
                    "domain": key.domain,
                    "topic_family": key.topic_family,
                    "topic_family_slice": (
                        list(LEAN_TOPIC_FAMILIES)
                        if key.domain == "lean_proof"
                        and key.task_slice_key == "all_topics"
                        else (
                            [key.topic_family]
                            if key.topic_family is not None
                            else []
                        )
                    ),
                    "fault_type": key.fault_type,
                    "fault_rate_percent": key.fault_rate_percent,
                    "repeat_id": key.repeat_id,
                    "selection_id": selection.selection_id,
                    "selection_digest": selection.selection_digest,
                    "ordered_case_ids": list(selection.ordered_case_ids),
                    "candidate_ai_unit_ids": list(unit_ids),
                    "selected_target_ai_unit_ids": list(selected_targets),
                    "target_seed": RATE_FAULT_TARGET_SEED,
                    "provider_tokens_attributed_by_mutation": 0,
                }
            )
    if not any(selection.is_blocked for selection in selections):
        if root_counts != EXPECTED_ROOT_RUN_COUNTS:
            raise ValueError("root-run total drift for Experiment 3 matrix")

    return {
        "schema_version": EXP3_SCHEMA_VERSION,
        "experiment_id": EXP3_EXPERIMENT_ID,
        "condition_count": len(conditions),
        "selection_count": len(selections),
        "root_run_counts": root_counts,
        "provider_calls_made": 0,
        "baseline_model": {
            "provider_family": BASELINE_PROVIDER_FAMILY,
            "provider_model_id": BASELINE_PROVIDER_MODEL_ID,
            "model_entry_id": BASELINE_MODEL_ENTRY_ID,
            "reasoning_profile_id": BASELINE_REASONING_PROFILE_ID,
        },
        "rate_fault": {
            "fault_types": list(RATE_FAULT_TYPES),
            "factorization_rates_percent": list(FACTOR_RATE_FAULT_RATES_PERCENT),
            "lean_rates_percent": list(LEAN_RATE_FAULT_RATES_PERCENT),
            "repeats": list(REPEAT_IDS),
        },
        "worker_death": {
            "worker_count": WORKER_DEATH_WORKER_COUNT,
            "dead_worker_count_targets": list(WORKER_DEATH_COUNTS),
            "kill_progress_targets_percent": list(
                WORKER_DEATH_KILL_PROGRESS_PERCENT
            ),
            "repeats": list(REPEAT_IDS),
        },
        "fault_target_manifest": target_rows,
    }


def validate_exp3_condition_matrix(
    conditions: Sequence[PaperExperimentCondition],
    selections: Sequence[FrozenCaseSelection],
    *,
    catalog: Mapping[str, Any],
) -> None:
    if len(conditions) != len(selections):
        raise ValueError("conditions and selections must have matching length")
    catalog = _catalog_mapping(catalog)
    catalog_digest = _catalog_digest(catalog)
    condition_ids = [condition.condition_id for condition in conditions]
    if len(set(condition_ids)) != len(condition_ids):
        raise ValueError("duplicate condition in Exp3 condition matrix")
    if conditions:
        _validate_baseline_condition(conditions[0])
    endpoint_identity = _identity_from_condition(conditions[0]) if conditions else None
    expected_conditions = expand_exp3_conditions(
        catalog_digest=catalog_digest,
        endpoint_identity=endpoint_identity,
    )
    expected_condition_ids = tuple(condition.condition_id for condition in expected_conditions)
    if tuple(condition_ids) != expected_condition_ids:
        raise ValueError("condition matrix drift for Experiment 3")
    for condition, expected_condition, selection in zip(
        conditions,
        expected_conditions,
        selections,
        strict=True,
    ):
        _validate_baseline_condition(condition)
        if condition.catalog_digest != catalog_digest:
            raise ValueError("catalog digest drift for Experiment 3 condition")
        _validate_condition_matches_expected(condition, expected_condition)
        if selection.catalog_digest != catalog_digest:
            raise ValueError("selection catalog digest drift for Experiment 3")
        try:
            expected_case_ids = _expected_case_ids_for_condition(
                condition,
                catalog=catalog,
            )
        except _MissingCatalogSlice:
            if selection.is_blocked and selection.blocked_reason == (
                "missing_exp3_catalog_slice"
            ):
                continue
            raise
        if selection.is_blocked:
            raise ValueError("blocked selection conflicts with available catalog slice")
        if tuple(selection.ordered_case_ids) != expected_case_ids:
            raise ValueError(
                f"slice drift for {condition.condition_id}: "
                f"{tuple(selection.ordered_case_ids)!r} != {expected_case_ids!r}"
            )
        expected_ai_units = _expected_ai_unit_count(expected_case_ids, catalog=catalog)
        if selection.expected_ai_unit_count != expected_ai_units:
            raise ValueError("selection AI-unit count drift for Experiment 3")
        if selection.paper_eligible_required is not True:
            raise ValueError("Exp3 selections must require paper eligibility")
    if not any(selection.is_blocked for selection in selections):
        root_counts = _root_run_counts(conditions, selections)
        if root_counts != EXPECTED_ROOT_RUN_COUNTS:
            raise ValueError("root-run total drift for Experiment 3 matrix")


def _root_run_counts(
    conditions: Sequence[PaperExperimentCondition],
    selections: Sequence[FrozenCaseSelection],
) -> dict[str, int]:
    counts = {
        "rate_fault_factorization": 0,
        "rate_fault_lean_proof": 0,
        "worker_death": 0,
        "total": 0,
    }
    for condition, selection in zip(conditions, selections, strict=True):
        key = _parse_condition_id(condition.condition_id)
        case_count = len(selection.ordered_case_ids)
        if key.matrix_kind == "rate_fault" and key.domain == "factorization":
            counts["rate_fault_factorization"] += case_count
        elif key.matrix_kind == "rate_fault" and key.domain == "lean_proof":
            counts["rate_fault_lean_proof"] += case_count
        elif key.matrix_kind == "worker_death":
            counts["worker_death"] += case_count
        counts["total"] += case_count
    return counts


def _validate_condition_matches_expected(
    condition: PaperExperimentCondition,
    expected_condition: PaperExperimentCondition,
) -> None:
    actual_body = condition.to_dict()
    expected_body = expected_condition.to_dict()
    actual_body.pop("condition_digest", None)
    expected_body.pop("condition_digest", None)
    if actual_body != expected_body:
        raise ValueError("condition field drift for Experiment 3")


def _canonical_condition_for(
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
) -> PaperExperimentCondition:
    expected_by_id = {
        expected.condition_id: expected
        for expected in expand_exp3_conditions(
            catalog_digest=_catalog_digest(context.catalog),
            endpoint_identity=_validate_approved_endpoint_binding(context),
        )
    }
    canonical = expected_by_id.get(condition.condition_id)
    if canonical is None or condition.condition_digest != canonical.condition_digest:
        raise ValueError("condition does not match canonical Experiment 3 condition")
    return canonical


def _validate_canonical_selection(
    *,
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
    selection: FrozenCaseSelection,
) -> None:
    canonical = _selection_for_condition(
        condition,
        catalog=_catalog_mapping(context.catalog),
    )
    if _selection_contract_body(selection) != _selection_contract_body(canonical):
        raise ValueError("selection does not match canonical Experiment 3 selection")


def _selection_contract_body(selection: FrozenCaseSelection) -> JsonObject:
    return {
        "schema_version": selection.schema_version,
        "selection_id": selection.selection_id,
        "experiment_id": selection.experiment_id,
        "suite_version": selection.suite_version,
        "catalog_version": selection.catalog_version,
        "domain": selection.domain,
        "paper_difficulty": selection.paper_difficulty,
        "topic_family": selection.topic_family,
        "ordered_case_ids": list(selection.ordered_case_ids),
        "catalog_digest": selection.catalog_digest,
        "expected_ai_unit_count": selection.expected_ai_unit_count,
        "paper_eligible_required": selection.paper_eligible_required,
        "blocked_reason": selection.blocked_reason,
        "selection_digest": selection.selection_digest,
    }


def summarize_exp3(evidence: Mapping[str, Any]) -> ExperimentSummaryRows:
    body = _require_mapping(evidence, "evidence")
    rate_runs = tuple(
        _require_mapping(run, "rate_fault_run")
        for run in _require_sequence(body.get("rate_fault_runs", ()), "rate_fault_runs")
    )
    worker_runs = tuple(
        _require_mapping(run, "worker_death_run")
        for run in _require_sequence(
            body.get("worker_death_runs", ()),
            "worker_death_runs",
        )
    )
    for run in (*rate_runs, *worker_runs):
        transport_kind = run.get("transport_kind")
        if run.get("paper_eligible") is True and transport_kind != "ai_api":
            raise ValueError(f"{transport_kind} transport cannot be paper eligible")
    matrix_complete = _summary_matrix_complete(
        body,
        rate_runs=rate_runs,
        worker_runs=worker_runs,
    )
    if any(run.get("paper_eligible") is True for run in (*rate_runs, *worker_runs)):
        if not matrix_complete:
            raise ValueError(
                "paper-eligible Experiment 3 summary requires a complete formal matrix"
            )
    rows: list[JsonObject] = []
    for run in rate_runs:
        rows.append(
            _summarize_rate_fault_run(run, matrix_complete=matrix_complete)
        )
    for run in worker_runs:
        rows.append(
            _summarize_worker_death_run(run, matrix_complete=matrix_complete)
        )
    return ExperimentSummaryRows(experiment_id=EXP3_EXPERIMENT_ID, rows=tuple(rows))


def _summary_matrix_complete(
    evidence: Mapping[str, Any],
    *,
    rate_runs: Sequence[Mapping[str, Any]],
    worker_runs: Sequence[Mapping[str, Any]],
) -> bool:
    required = ("conditions", "selections", "catalog")
    present = tuple(field_name in evidence for field_name in required)
    if not any(present):
        return False
    if not all(present):
        raise ValueError("formal matrix evidence must include conditions, selections, and catalog")
    conditions = _require_sequence(evidence.get("conditions"), "conditions")
    selections = _require_sequence(evidence.get("selections"), "selections")
    if not all(isinstance(item, PaperExperimentCondition) for item in conditions):
        raise ValueError("formal matrix conditions must be PaperExperimentCondition values")
    if not all(isinstance(item, FrozenCaseSelection) for item in selections):
        raise ValueError("formal matrix selections must be FrozenCaseSelection values")
    catalog = _require_mapping(evidence.get("catalog"), "catalog")
    validate_exp3_condition_matrix(
        conditions,
        selections,
        catalog=catalog,
    )
    plan = build_exp3_plan_manifest(conditions, selections, catalog=catalog)
    expected_target_manifest_by_id = {
        row["condition_id"]: row for row in plan["fault_target_manifest"]
    }
    runs = (*rate_runs, *worker_runs)
    condition_ids = tuple(condition.condition_id for condition in conditions)
    run_ids = tuple(_required_str(run, "condition_id") for run in runs)
    if len(set(run_ids)) != len(run_ids):
        raise ValueError("duplicate Experiment 3 summary condition run")
    if set(run_ids) != set(condition_ids):
        raise ValueError("Experiment 3 summary does not contain the complete formal matrix")
    expected_by_id = {
        condition.condition_id: len(selection.ordered_case_ids)
        for condition, selection in zip(conditions, selections, strict=True)
    }
    condition_by_id = {
        condition.condition_id: condition for condition in conditions
    }
    selection_by_id = {
        condition.condition_id: selection
        for condition, selection in zip(conditions, selections, strict=True)
    }
    rate_run_ids = {_required_str(run, "condition_id") for run in rate_runs}
    worker_run_ids = {_required_str(run, "condition_id") for run in worker_runs}
    for run in runs:
        condition_id = _required_str(run, "condition_id")
        if _positive_int(run, "task_count") != expected_by_id[condition_id]:
            raise ValueError("Experiment 3 summary task slice/root-run drift")
        condition = condition_by_id[condition_id]
        _validate_formal_condition_evidence(
            run,
            condition=condition,
            selection=selection_by_id[condition_id],
        )
        key = _parse_condition_id(condition_id)
        if (
            run.get("domain") != condition.domain
            or run.get("fault_type") != condition.fault_type
            or run.get("fault_rate_percent") != int(condition.fault_rate * 100)
            or run.get("repeat_id") != condition.repeat_id
            or (key.matrix_kind == "rate_fault") != (condition_id in rate_run_ids)
            or (key.matrix_kind == "worker_death") != (condition_id in worker_run_ids)
        ):
            raise ValueError("summary run does not match canonical formal condition")
        if key.matrix_kind == "worker_death" and (
            run.get("target_dead_worker_count") != key.dead_worker_count
            or run.get("target_kill_progress_percent")
            != key.kill_progress_percent
        ):
            raise ValueError("summary run does not match canonical formal condition")
        if key.matrix_kind == "rate_fault":
            actual_manifest = _require_mapping(
                run.get("fault_target_manifest"),
                "fault_target_manifest",
            )
            expected_manifest = expected_target_manifest_by_id[condition_id]
            if any(
                actual_manifest.get(field_name) != expected_value
                for field_name, expected_value in expected_manifest.items()
            ):
                raise ValueError("summary run does not match frozen target manifest")
    counts = {
        "rate_fault_factorization": sum(
            _positive_int(run, "task_count")
            for run in rate_runs
            if run.get("domain") == "factorization"
        ),
        "rate_fault_lean_proof": sum(
            _positive_int(run, "task_count")
            for run in rate_runs
            if run.get("domain") == "lean_proof"
        ),
        "worker_death": sum(_positive_int(run, "task_count") for run in worker_runs),
    }
    counts["total"] = sum(counts.values())
    if counts != EXPECTED_ROOT_RUN_COUNTS:
        raise ValueError("Experiment 3 summary root-run total drift")
    return True


def _validate_formal_condition_evidence(
    run: Mapping[str, Any],
    *,
    condition: PaperExperimentCondition,
    selection: FrozenCaseSelection,
) -> None:
    evidence = _require_mapping(
        run.get("condition_evidence"),
        "condition_evidence",
    )
    expected = {
        "condition_id": condition.condition_id,
        "condition_digest": condition.condition_digest,
        "selection_digest": selection.selection_digest,
        "ordered_case_ids": list(selection.ordered_case_ids),
        "repeat_id": condition.repeat_id,
        "seed": condition.seed,
        "worker_count": condition.worker_count,
        "catalog_digest": condition.catalog_digest,
        "provider_config_id": condition.provider_config_id,
        "model_entry_id": condition.model_entry_id,
        "provider_family": condition.provider_family,
        "provider_model_id": condition.provider_model_id,
        "reasoning_profile_id": condition.reasoning_profile_id,
        "source_provider_config_digest": condition.source_provider_config_digest,
        "model_endpoint_identity_digest": condition.model_endpoint_identity_digest,
    }
    if any(evidence.get(field_name) != value for field_name, value in expected.items()):
        raise ValueError("formal condition evidence does not match frozen plan")
    if _normalized_request_limit_policy(
        _require_mapping(evidence.get("request_limits"), "request_limits")
    ) != BASELINE_REQUEST_LIMIT_POLICY:
        raise ValueError("formal condition evidence request policy mismatch")


def _condition_from_key(
    key: _ConditionKey,
    *,
    catalog_digest: str,
    endpoint_identity: Mapping[str, Any],
) -> PaperExperimentCondition:
    return PaperExperimentCondition(
        schema_version=PAPER_CONDITION_V2,
        experiment_id=EXP3_EXPERIMENT_ID,
        condition_id=_condition_id(key),
        domain=key.domain,
        difficulty=key.difficulty,
        paper_difficulty=key.paper_difficulty,
        topic_family=key.topic_family,
        worker_count=(
            WORKER_DEATH_WORKER_COUNT
            if key.matrix_kind == "worker_death"
            else 10
        ),
        fault_type=key.fault_type,
        fault_rate=key.fault_rate_percent / 100.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        provider_config_id=str(endpoint_identity["provider_config_id"]),
        provider_family=BASELINE_PROVIDER_FAMILY,
        provider_model_id=BASELINE_PROVIDER_MODEL_ID,
        model_entry_id=BASELINE_MODEL_ENTRY_ID,
        reasoning_profile_id=BASELINE_REASONING_PROFILE_ID,
        source_provider_config_digest=str(
            endpoint_identity["source_provider_config_digest"]
        ),
        model_endpoint_identity_digest=str(
            endpoint_identity["model_endpoint_identity_digest"]
        ),
        repeat_id=key.repeat_id,
        seed=_condition_seed(key),
        catalog_digest=catalog_digest,
        real_transport_required=True,
        paper_eligible_required=True,
    )


def _condition_id(key: _ConditionKey) -> str:
    if key.matrix_kind == "rate_fault" and key.domain == "factorization":
        return (
            f"exp3_rate_fault_factorization__{key.fault_type}"
            f"__r{key.fault_rate_percent}__rep{key.repeat_id}"
        )
    if key.matrix_kind == "rate_fault" and key.domain == "lean_proof":
        return (
            f"exp3_rate_fault_lean__{key.task_slice_key}__{key.fault_type}"
            f"__r{key.fault_rate_percent}__rep{key.repeat_id}"
        )
    if key.matrix_kind == "worker_death" and key.domain == "factorization":
        return (
            f"exp3_worker_death_factorization__{key.task_slice_key}"
            f"__dead{key.dead_worker_count}__p{key.kill_progress_percent}"
            f"__rep{key.repeat_id}"
        )
    return (
        f"exp3_worker_death_lean__{key.task_slice_key}"
        f"__dead{key.dead_worker_count}__p{key.kill_progress_percent}"
        f"__rep{key.repeat_id}"
    )


def _condition_seed(key: _ConditionKey) -> int:
    digest = digest_json(
        {
            "schema_version": "tokenshare.paper_exp3_condition_seed.v1",
            "condition_id": _condition_id(key),
        }
    )
    return 330000 + int(digest.removeprefix("sha256:")[:8], 16) % 100000


def _parse_condition_id(condition_id: str) -> _ConditionKey:
    parts = condition_id.split("__")
    if len(parts) == 4 and parts[0] == "exp3_rate_fault_factorization":
        fault_type, rate_part, repeat_part = parts[1:]
        return _ConditionKey(
            matrix_kind="rate_fault",
            domain="factorization",
            task_slice_key="factorization",
            difficulty="medium",
            paper_difficulty="medium",
            topic_family=None,
            fault_type=fault_type,
            fault_rate_percent=_int_suffix(rate_part, "r"),
            repeat_id=_int_suffix(repeat_part, "rep"),
        )
    if len(parts) == 5 and parts[0] == "exp3_rate_fault_lean":
        task_slice_key, fault_type, rate_part, repeat_part = parts[1:]
        topic_family = None if task_slice_key == "all_topics" else task_slice_key
        return _ConditionKey(
            matrix_kind="rate_fault",
            domain="lean_proof",
            task_slice_key=task_slice_key,
            difficulty="medium",
            paper_difficulty="medium_lemma_dag",
            topic_family=topic_family,
            fault_type=fault_type,
            fault_rate_percent=_int_suffix(rate_part, "r"),
            repeat_id=_int_suffix(repeat_part, "rep"),
        )
    if len(parts) == 5 and parts[0] == "exp3_worker_death_factorization":
        difficulty, dead_part, progress_part, repeat_part = parts[1:]
        return _ConditionKey(
            matrix_kind="worker_death",
            domain="factorization",
            task_slice_key=difficulty,
            difficulty=difficulty,
            paper_difficulty=difficulty,
            topic_family=None,
            fault_type="worker_death",
            fault_rate_percent=0,
            repeat_id=_int_suffix(repeat_part, "rep"),
            dead_worker_count=_int_suffix(dead_part, "dead"),
            kill_progress_percent=_int_suffix(progress_part, "p"),
        )
    if len(parts) == 5 and parts[0] == "exp3_worker_death_lean":
        topic_family, dead_part, progress_part, repeat_part = parts[1:]
        return _ConditionKey(
            matrix_kind="worker_death",
            domain="lean_proof",
            task_slice_key=topic_family,
            difficulty="medium",
            paper_difficulty="medium_lemma_dag",
            topic_family=topic_family,
            fault_type="worker_death",
            fault_rate_percent=0,
            repeat_id=_int_suffix(repeat_part, "rep"),
            dead_worker_count=_int_suffix(dead_part, "dead"),
            kill_progress_percent=_int_suffix(progress_part, "p"),
        )
    raise ValueError(f"unrecognized Exp3 condition_id: {condition_id}")


def _int_suffix(value: str, prefix: str) -> int:
    if not value.startswith(prefix):
        raise ValueError(f"expected {prefix} suffix in {value}")
    return int(value.removeprefix(prefix))


def _selection_for_condition(
    condition: PaperExperimentCondition,
    *,
    catalog: Mapping[str, Any],
) -> FrozenCaseSelection:
    key = _parse_condition_id(condition.condition_id)
    catalog_digest = _catalog_digest(catalog)
    catalog_version = str(catalog.get("catalog_version") or "v1")
    suite_version = str(catalog.get("suite_version") or "paper_v1")
    selection_type = (
        Exp3MixedLeanCaseSelection
        if key.matrix_kind == "rate_fault"
        and key.domain == "lean_proof"
        and key.task_slice_key == "all_topics"
        else FrozenCaseSelection
    )
    try:
        case_ids = _expected_case_ids_for_condition(condition, catalog=catalog)
    except _MissingCatalogSlice:
        return selection_type(
            selection_id=_selection_id_for_key(key),
            experiment_id=EXP3_EXPERIMENT_ID,
            suite_version=suite_version,
            catalog_version=catalog_version,
            domain=key.domain,
            paper_difficulty=key.paper_difficulty,
            topic_family=_selection_topic_family_for_key(key),
            ordered_case_ids=(),
            catalog_digest=catalog_digest,
            expected_ai_unit_count=0,
            paper_eligible_required=True,
            blocked_reason="missing_exp3_catalog_slice",
        )
    expected_ai_units = _expected_ai_unit_count(case_ids, catalog=catalog)
    return selection_type(
        selection_id=_selection_id_for_key(key),
        experiment_id=EXP3_EXPERIMENT_ID,
        suite_version=suite_version,
        catalog_version=catalog_version,
        domain=key.domain,
        paper_difficulty=key.paper_difficulty,
        topic_family=_selection_topic_family_for_key(key),
        ordered_case_ids=case_ids,
        catalog_digest=catalog_digest,
        expected_ai_unit_count=expected_ai_units,
        paper_eligible_required=True,
    )


def _selection_id_for_key(key: _ConditionKey) -> str:
    if key.matrix_kind == "rate_fault" and key.domain == "factorization":
        return _RATE_FACTOR_SELECTION_ID
    if key.matrix_kind == "rate_fault" and key.domain == "lean_proof":
        return f"exp3_rate_fault_lean_{key.task_slice_key}_slice_v1"
    if key.matrix_kind == "worker_death" and key.domain == "factorization":
        return f"exp3_worker_death_factorization_{key.task_slice_key}_slice_v1"
    return f"exp3_worker_death_lean_{key.task_slice_key}_slice_v1"


def _selection_topic_family_for_key(key: _ConditionKey) -> str | None:
    if key.domain != "lean_proof":
        return None
    if key.topic_family is not None:
        return key.topic_family
    return None


def _expected_case_ids_for_condition(
    condition: PaperExperimentCondition,
    *,
    catalog: Mapping[str, Any],
) -> tuple[str, ...]:
    key = _parse_condition_id(condition.condition_id)
    if key.matrix_kind == "rate_fault" and key.domain == "factorization":
        case_ids = _case_tuple(
            _required_catalog_value(
                catalog,
                "exp3_rate_fault_factorization_case_ids",
            )
        )
        if len(case_ids) != 5:
            raise ValueError("factorization rate-fault slice must contain 5 cases")
        return case_ids
    if key.matrix_kind == "rate_fault" and key.domain == "lean_proof":
        by_topic = _mapping(
            _required_catalog_value(
                catalog,
                "exp3_rate_fault_lean_case_ids_by_topic",
            )
        )
        if key.task_slice_key == "all_topics":
            selected: list[str] = []
            for topic_family in LEAN_TOPIC_FAMILIES:
                topic_case_ids = _case_tuple(
                    _required_catalog_value(by_topic, topic_family)
                )
                if len(topic_case_ids) != 1:
                    raise ValueError(
                        "Lean rate-fault slice must contain exactly one case "
                        "per topic family"
                    )
                selected.extend(topic_case_ids)
            if len(selected) != 3:
                raise ValueError("Lean rate-fault slice must contain 3 cases")
            return tuple(selected)
        return _case_tuple(_required_catalog_value(by_topic, str(key.topic_family)))
    if key.matrix_kind == "worker_death" and key.domain == "factorization":
        case_ids = _case_tuple(
            _required_catalog_value(
                _mapping(
                    _required_catalog_value(
                        catalog,
                        "exp3_worker_death_factorization_case_ids_by_difficulty",
                    )
                ),
                key.task_slice_key,
            )
        )
        if len(case_ids) != 1:
            raise ValueError("worker-death factorization slice must contain 1 case")
        return case_ids
    case_ids = _case_tuple(
        _required_catalog_value(
            _mapping(
                _required_catalog_value(
                    catalog,
                    "exp3_worker_death_lean_case_ids_by_topic",
                )
            ),
            key.task_slice_key,
        )
    )
    if len(case_ids) != 1:
        raise ValueError("worker-death Lean slice must contain 1 case")
    return case_ids


def _expected_ai_unit_count(
    case_ids: Sequence[str],
    *,
    catalog: Mapping[str, Any],
) -> int:
    ai_units_by_case = _ai_units_by_case_id(catalog)
    count = 0
    for case_id in case_ids:
        units = ai_units_by_case[case_id]
        if not units:
            raise ValueError(f"case has no AI units: {case_id}")
        count += len(units)
    return count


def _summarize_rate_fault_run(
    run: Mapping[str, Any],
    *,
    matrix_complete: bool,
) -> JsonObject:
    transport_kind, _attempts_claimed_real = _validate_attempt_transport(run)
    ineligibility_reasons = _validate_attempt_model_entries(run)
    attempts_real = transport_kind == "ai_api" and not ineligibility_reasons
    _validate_attempt_aggregates(run)
    matched_baseline = _required_str(run, "matched_baseline_condition_id")
    expected_baseline = _required_str(run, "expected_baseline_condition_id")
    if expected_baseline != matched_baseline:
        raise ValueError("baseline mismatch for Experiment 3 rate-fault run")
    _validate_baseline_comparison(run)
    task_count = _positive_int(run, "task_count")
    domain = _required_str(run, "domain")
    fault_type = _required_str(run, "fault_type")
    fault_rate_percent = _non_negative_int(run, "fault_rate_percent")
    _validate_rate_fault_membership(
        domain=domain,
        fault_type=fault_type,
        fault_rate_percent=fault_rate_percent,
    )
    injected_fault_count = _non_negative_int(run, "injected_fault_count")
    detected_fault_count = _non_negative_int(run, "detected_fault_count")
    false_accept_count = _non_negative_int(run, "false_accept_count")
    recoverable_fault_target_count = _non_negative_int(
        run,
        "recoverable_fault_target_count",
    )
    recovered_fault_target_count = _non_negative_int(
        run,
        "recovered_fault_target_count",
    )
    _require_numerator_leq_denominator(
        "detected_fault_count",
        detected_fault_count,
        "injected_fault_count",
        injected_fault_count,
    )
    _require_numerator_leq_denominator(
        "false_accept_count",
        false_accept_count,
        "injected_fault_count",
        injected_fault_count,
    )
    _require_numerator_leq_denominator(
        "recovered_fault_target_count",
        recovered_fault_target_count,
        "recoverable_fault_target_count",
        recoverable_fault_target_count,
    )
    completed_root_count = _non_negative_int(run, "completed_root_count")
    _require_numerator_leq_denominator(
        "completed_root_count",
        completed_root_count,
        "task_count",
        task_count,
    )
    original_refs = _json_list(run.get("original_output_refs"))
    mutated_refs = _json_list(run.get("mutated_output_refs"))
    _validate_fault_records(
        run,
        condition_id=_required_str(run, "condition_id"),
        repeat_id=_non_negative_int(run, "repeat_id"),
        fault_type=fault_type,
        injected_fault_count=injected_fault_count,
        original_output_refs=original_refs,
        mutated_output_refs=mutated_refs,
    )
    detection = _denominator_rate(
        detected_fault_count,
        injected_fault_count,
        zero_applicability="zero_fault_denominator",
    )
    false_accept = _denominator_rate(
        false_accept_count,
        injected_fault_count,
        zero_applicability="zero_fault_denominator",
    )
    recovery = _denominator_rate(
        recovered_fault_target_count,
        recoverable_fault_target_count,
        zero_applicability="zero_recoverable_denominator",
    )
    wall_clock = _number(run, "wall_clock_ms")
    baseline_wall_clock = _number(run, "baseline_wall_clock_ms")
    tokens = _number(run, "total_tokens")
    baseline_tokens = _number(run, "baseline_total_tokens")
    cost = _number(run, "cost_estimate")
    baseline_cost = _number(run, "baseline_cost_estimate")
    wall_clock_overhead = _overhead(wall_clock, baseline_wall_clock)
    token_overhead = _overhead(tokens, baseline_tokens)
    cost_overhead = _overhead(cost, baseline_cost)

    row: JsonObject = {
        "schema_version": "tokenshare.paper_exp3_rate_fault_summary_row.v1",
        "experiment_id": EXP3_EXPERIMENT_ID,
        "summary_kind": "rate_fault",
        "condition_id": _required_str(run, "condition_id"),
        "domain": domain,
        "fault_type": fault_type,
        "fault_rate_percent": fault_rate_percent,
        "repeat_id": _non_negative_int(run, "repeat_id"),
        "task_count": task_count,
        "completed_root_count": completed_root_count,
        "matched_baseline_condition_id": matched_baseline,
        "expected_baseline_condition_id": expected_baseline,
        "injected_fault_count": injected_fault_count,
        "detected_fault_count": detected_fault_count,
        "false_accept_count": false_accept_count,
        "recoverable_fault_target_count": recoverable_fault_target_count,
        "recovered_fault_target_count": recovered_fault_target_count,
        "detection_rate": detection["rate"],
        "detection_applicability": detection["applicability"],
        "false_accept_rate": false_accept["rate"],
        "false_accept_applicability": false_accept["applicability"],
        "recovery_rate": recovery["rate"],
        "recovery_applicability": recovery["applicability"],
        "completion_rate": completed_root_count / task_count,
        "recovery_latency_ms": _non_negative_int(run, "recovery_latency_ms"),
        "retry_count": _non_negative_int(run, "retry_count"),
        "reassignment_count": _non_negative_int(run, "reassignment_count"),
        "wasted_actual_tokens": _non_negative_int(run, "wasted_actual_tokens"),
        "wall_clock_overhead_ms": wall_clock_overhead["delta"],
        "wall_clock_overhead_ratio": wall_clock_overhead["ratio"],
        "wall_clock_overhead_applicability": wall_clock_overhead["applicability"],
        "token_overhead": token_overhead["delta"],
        "token_overhead_ratio": token_overhead["ratio"],
        "token_overhead_applicability": token_overhead["applicability"],
        "cost_overhead": cost_overhead["delta"],
        "cost_overhead_ratio": cost_overhead["ratio"],
        "cost_overhead_applicability": cost_overhead["applicability"],
        "original_output_refs": original_refs,
        "mutated_output_refs": mutated_refs,
        "provider_tokens_attributed_by_mutation": 0,
        "transport_kind": transport_kind,
        "ineligibility_reasons": list(ineligibility_reasons),
        "paper_eligible": (
            _bool(run, "paper_eligible")
            and matrix_complete
            and attempts_real
            and transport_kind == "ai_api"
        ),
    }
    _reject_non_finite_row(row)
    return row


def _summarize_worker_death_run(
    run: Mapping[str, Any],
    *,
    matrix_complete: bool,
) -> JsonObject:
    transport_kind, _attempts_claimed_real = _validate_attempt_transport(run)
    ineligibility_reasons = _validate_attempt_model_entries(run)
    attempts_real = transport_kind == "ai_api" and not ineligibility_reasons
    _validate_attempt_aggregates(run)
    condition_id = _required_str(run, "condition_id")
    domain = _required_str(run, "domain")
    _validate_domain(domain)
    repeat_id = _non_negative_int(run, "repeat_id")
    target_dead = _non_negative_int(run, "target_dead_worker_count")
    actual_dead = _non_negative_int(run, "actual_dead_worker_count")
    required_slots = _positive_int(run, "required_slot_count")
    recovered_slots = _non_negative_int(run, "recovered_slot_count")
    if recovered_slots > required_slots:
        raise ValueError("recovered_slot_count must not exceed required_slot_count")
    completeness = recovered_slots / required_slots
    wall_clock = _number(run, "wall_clock_ms")
    baseline_wall_clock = _number(run, "baseline_wall_clock_ms")
    tokens = _number(run, "total_tokens")
    baseline_tokens = _number(run, "baseline_total_tokens")
    cost = _number(run, "cost_estimate")
    baseline_cost = _number(run, "baseline_cost_estimate")
    wall_clock_overhead = _overhead(wall_clock, baseline_wall_clock)
    token_overhead = _overhead(tokens, baseline_tokens)
    cost_overhead = _overhead(cost, baseline_cost)
    target_progress = _non_negative_int(run, "target_kill_progress_percent")
    actual_progress = _non_negative_int(run, "actual_kill_progress_percent")
    if target_dead not in WORKER_DEATH_COUNTS:
        raise ValueError("unsupported frozen worker-death count")
    if target_progress not in WORKER_DEATH_KILL_PROGRESS_PERCENT:
        raise ValueError("unsupported frozen worker-death kill progress")
    worker_death_record_refs = _validate_worker_death_records(
        run,
        condition_id=condition_id,
        repeat_id=repeat_id,
        actual_dead_worker_count=actual_dead,
        target_kill_progress_percent=target_progress,
        actual_kill_progress_percent=actual_progress,
    )
    matched_baseline = _required_str(run, "matched_baseline_condition_id")
    expected_baseline = _required_str(run, "expected_baseline_condition_id")
    if expected_baseline != matched_baseline:
        raise ValueError("baseline mismatch for Experiment 3 worker-death run")
    _validate_baseline_comparison(run)
    coordinator_continued = _bool(run, "coordinator_continued")
    root_output_complete = recovered_slots == required_slots
    verifier_evidence = _require_mapping(
        run.get("verifier_evidence"),
        "verifier_evidence",
    )
    _validate_artifact_ref(
        verifier_evidence.get("artifact_ref"),
        "verifier_evidence.artifact_ref",
    )
    if _bool(verifier_evidence, "root_output_complete") != root_output_complete:
        raise ValueError("verifier root completeness contradicts recovered slots")
    accepted_validity = (
        root_output_complete
        and _bool(verifier_evidence, "accepted_validity")
    )
    mismatch_reason = None
    if actual_dead != target_dead:
        mismatch_reason = "actual_dead_count_mismatch"
    elif actual_progress != target_progress:
        mismatch_reason = "actual_kill_progress_mismatch"
    elif not coordinator_continued:
        mismatch_reason = "coordinator_not_continued"
    elif not root_output_complete:
        mismatch_reason = "incomplete_recovery"
    elif not accepted_validity:
        mismatch_reason = "accepted_validity_failed"

    row: JsonObject = {
        "schema_version": "tokenshare.paper_exp3_worker_death_summary_row.v1",
        "experiment_id": EXP3_EXPERIMENT_ID,
        "summary_kind": "worker_death",
        "condition_id": condition_id,
        "domain": domain,
        "repeat_id": repeat_id,
        "task_count": _positive_int(run, "task_count"),
        "target_dead_worker_count": target_dead,
        "actual_dead_worker_count": actual_dead,
        "target_kill_progress_percent": target_progress,
        "actual_kill_progress_percent": actual_progress,
        "coordinator_continued": coordinator_continued,
        "required_slot_count": required_slots,
        "recovered_slot_count": recovered_slots,
        "result_completeness_rate": completeness,
        "root_output_complete": root_output_complete,
        "accepted_validity": accepted_validity,
        "recovery_latency_ms": _non_negative_int(run, "recovery_latency_ms"),
        "retry_count": _non_negative_int(run, "retry_count"),
        "reassignment_count": _non_negative_int(run, "reassignment_count"),
        "matched_baseline_condition_id": matched_baseline,
        "expected_baseline_condition_id": expected_baseline,
        "worker_death_record_refs": worker_death_record_refs,
        "wall_clock_ms": wall_clock,
        "total_tokens": tokens,
        "cost_estimate": cost,
        "wall_clock_overhead_ms": wall_clock_overhead["delta"],
        "wall_clock_overhead_ratio": wall_clock_overhead["ratio"],
        "wall_clock_overhead_applicability": wall_clock_overhead["applicability"],
        "token_overhead": token_overhead["delta"],
        "token_overhead_ratio": token_overhead["ratio"],
        "token_overhead_applicability": token_overhead["applicability"],
        "cost_overhead": cost_overhead["delta"],
        "cost_overhead_ratio": cost_overhead["ratio"],
        "cost_overhead_applicability": cost_overhead["applicability"],
        "condition_included": mismatch_reason is None,
        "run_status": "failed" if mismatch_reason else "completed",
        "failure_reason": mismatch_reason,
        "transport_kind": transport_kind,
        "ineligibility_reasons": list(ineligibility_reasons),
        "paper_eligible": (
            _bool(run, "paper_eligible")
            and matrix_complete
            and attempts_real
            and transport_kind == "ai_api"
            and mismatch_reason is None
        ),
    }
    _reject_non_finite_row(row)
    return row


def _validate_baseline_condition(condition: PaperExperimentCondition) -> None:
    if condition.experiment_id != EXP3_EXPERIMENT_ID:
        raise ValueError("condition experiment_id is not Exp3")
    if (
        condition.provider_config_id != BASELINE_PROVIDER_CONFIG_ID
        or condition.model_entry_id != BASELINE_MODEL_ENTRY_ID
        or condition.provider_family != BASELINE_PROVIDER_FAMILY
        or condition.provider_model_id != BASELINE_PROVIDER_MODEL_ID
        or condition.reasoning_profile_id != BASELINE_REASONING_PROFILE_ID
    ):
        raise ValueError("model failover is forbidden for Experiment 3")
    _validate_complete_digest(
        "source_provider_config_digest",
        condition.source_provider_config_digest,
    )
    _validate_complete_digest(
        "model_endpoint_identity_digest",
        condition.model_endpoint_identity_digest,
    )


def _identity_from_condition(
    condition: PaperExperimentCondition,
) -> JsonObject:
    return {
        "provider_config_id": condition.provider_config_id,
        "selected_entry_id": condition.model_entry_id,
        "model_entry_id": condition.model_entry_id,
        "provider_family": condition.provider_family,
        "provider_model_id": condition.provider_model_id,
        "reasoning_profile_id": condition.reasoning_profile_id,
        "source_provider_config_digest": condition.source_provider_config_digest,
        "model_endpoint_identity_digest": condition.model_endpoint_identity_digest,
    }


def _normalized_endpoint_identity(
    endpoint_identity: Mapping[str, Any] | None,
) -> JsonObject:
    if endpoint_identity is None:
        return {
            "provider_config_id": BASELINE_PROVIDER_CONFIG_ID,
            "selected_entry_id": BASELINE_MODEL_ENTRY_ID,
            "model_entry_id": BASELINE_MODEL_ENTRY_ID,
            "provider_family": BASELINE_PROVIDER_FAMILY,
            "provider_model_id": BASELINE_PROVIDER_MODEL_ID,
            "reasoning_profile_id": BASELINE_REASONING_PROFILE_ID,
            "source_provider_config_digest": "sha256:" + "0" * 64,
            "model_endpoint_identity_digest": "sha256:" + "0" * 64,
        }
    identity = dict(endpoint_identity)
    expected = {
        "provider_config_id": BASELINE_PROVIDER_CONFIG_ID,
        "selected_entry_id": BASELINE_MODEL_ENTRY_ID,
        "model_entry_id": BASELINE_MODEL_ENTRY_ID,
        "provider_family": BASELINE_PROVIDER_FAMILY,
        "provider_model_id": BASELINE_PROVIDER_MODEL_ID,
        "reasoning_profile_id": BASELINE_REASONING_PROFILE_ID,
    }
    if any(identity.get(field_name) != value for field_name, value in expected.items()):
        raise ValueError("approved endpoint binding mismatch for Experiment 3")
    for field_name in (
        "source_provider_config_digest",
        "model_endpoint_identity_digest",
    ):
        _validate_complete_digest(field_name, identity.get(field_name))
    return identity


def _validate_approved_endpoint_binding(
    context: PaperExecutionContext,
) -> JsonObject:
    binding = _require_mapping(
        context.approved_endpoint_binding,
        "approved_endpoint_binding",
    )
    identity = _normalized_endpoint_identity(binding)
    binding_policy = _normalized_request_limit_policy(
        _require_mapping(binding.get("request_limits"), "binding request_limits")
    )
    context_policy = _normalized_request_limit_policy(context.request_limits)
    if binding_policy != BASELINE_REQUEST_LIMIT_POLICY:
        raise ValueError("approved request limit policy mismatch for Experiment 3")
    if context_policy != binding_policy:
        raise ValueError("request limit policy drift for Experiment 3")
    identity["request_limits"] = binding_policy
    return identity


def _normalized_request_limit_policy(value: Mapping[str, Any]) -> JsonObject:
    if set(value) != set(BASELINE_REQUEST_LIMIT_POLICY):
        raise ValueError("request limit policy contains missing or unknown fields")
    result: JsonObject = {}
    for field_name in ("max_tokens", "timeout_seconds", "max_provider_attempts"):
        item = value.get(field_name)
        if isinstance(item, bool) or not isinstance(item, int) or item < 1:
            raise ValueError(f"{field_name} must be a positive integer")
        result[field_name] = item
    temperature = value.get("temperature")
    if (
        isinstance(temperature, bool)
        or not isinstance(temperature, (int, float))
        or float(temperature) != 0.0
    ):
        raise ValueError("temperature must be 0.0 for Experiment 3")
    if value.get("enable_thinking") is not False:
        raise ValueError("enable_thinking must be false for Experiment 3")
    result["temperature"] = 0.0
    result["enable_thinking"] = False
    return result


def _validate_attempt_model_entries(run: Mapping[str, Any]) -> tuple[str, ...]:
    comparison_by_group = {
        "attempts": _require_mapping(
            run.get("condition_evidence"),
            "condition_evidence",
        ),
        "baseline_attempts": _require_mapping(
            run.get("baseline_evidence"),
            "baseline_evidence",
        ),
    }
    all_attempts: list[JsonObject] = []
    ineligibility_reasons: list[str] = []
    for group_name, comparison in comparison_by_group.items():
        attempts = _require_sequence(run.get(group_name, ()), group_name)
        if not attempts:
            raise ValueError("attempt evidence is required for Experiment 3 summary")
        expected_request_limits = _normalized_request_limit_policy(
            _require_mapping(comparison.get("request_limits"), "request_limits")
        )
        for attempt in attempts:
            attempt_body = dict(_require_mapping(attempt, "attempt"))
            if (
                attempt_body.get("entry_id") != BASELINE_MODEL_ENTRY_ID
                or attempt_body.get("provider") != BASELINE_PROVIDER_FAMILY
                or attempt_body.get("model") != BASELINE_PROVIDER_MODEL_ID
                or attempt_body.get("reasoning_profile_id")
                != BASELINE_REASONING_PROFILE_ID
            ):
                raise ValueError("model failover is forbidden for Experiment 3")
            if (
                attempt_body.get("condition_id") != comparison.get("condition_id")
                or attempt_body.get("repeat_id") != comparison.get("repeat_id")
                or attempt_body.get("source_provider_config_digest")
                != comparison.get("source_provider_config_digest")
                or attempt_body.get("model_endpoint_identity_digest")
                != comparison.get("model_endpoint_identity_digest")
                or attempt_body.get("provider_config_id")
                != comparison.get("provider_config_id")
                or _normalized_request_limit_policy(
                    _require_mapping(
                        attempt_body.get("request_limits"),
                        "attempt request_limits",
                    )
                )
                != expected_request_limits
            ):
                raise ValueError("attempt model identity mismatch")
            if attempt_body.get("transport_kind") == "ai_api":
                ineligibility_reasons.extend(
                    _real_attempt_artifact_reasons(attempt_body)
                )
                if attempt_body.get("paper_eligible") is not True:
                    ineligibility_reasons.append(
                        f"attempt:{attempt_body.get('attempt_id')}:not_paper_eligible"
                    )
            all_attempts.append(attempt_body)
    if run.get("transport_kind") == "ai_api":
        raw_eligibility_evidence = run.get("paper_eligibility_evidence")
        if raw_eligibility_evidence is None:
            eligibility_evidence: Mapping[str, Any] = {}
        else:
            eligibility_evidence = _require_mapping(
                raw_eligibility_evidence,
                "paper_eligibility_evidence",
            )
        report = evaluate_paper_eligibility(
            attempts=all_attempts,
            run_evidence=dict(eligibility_evidence),
        )
        ineligibility_reasons.extend(report.ineligibility_reasons)
    else:
        ineligibility_reasons.append(
            f"unsupported_transport:{run.get('transport_kind')}"
        )
    return tuple(dict.fromkeys(ineligibility_reasons))


def _real_attempt_artifact_reasons(attempt: Mapping[str, Any]) -> list[str]:
    attempt_id = str(attempt.get("attempt_id") or "unknown")
    reasons: list[str] = []
    for field_name in (
        "request_ref",
        "raw_output_ref",
        "provenance_ref",
        "usage_ref",
        "model_execution_record_ref",
    ):
        try:
            _validate_complete_artifact_ref(attempt.get(field_name), field_name)
        except ValueError:
            reasons.append(f"attempt:{attempt_id}:missing_{field_name}")
    if attempt.get("parsed_output_ref") is None and attempt.get("parse_failure_ref") is None:
        reasons.append(
            f"attempt:{attempt_id}:missing_parsed_output_or_parse_failure_ref"
        )
    if attempt.get("parsed_output_ref") is not None:
        try:
            _validate_complete_artifact_ref(
                attempt.get("parsed_output_ref"),
                "parsed_output_ref",
            )
        except ValueError:
            reasons.append(f"attempt:{attempt_id}:missing_parsed_output_ref")
    if attempt.get("parse_failure_ref") is not None:
        try:
            _validate_complete_artifact_ref(
                attempt.get("parse_failure_ref"),
                "parse_failure_ref",
            )
        except ValueError:
            reasons.append(f"attempt:{attempt_id}:missing_parse_failure_ref")
    raw_record = attempt.get("model_execution_record")
    if not isinstance(raw_record, Mapping):
        reasons.append(f"attempt:{attempt_id}:missing_model_execution_record")
        return reasons
    record = raw_record
    expected_record_digest = _required_str(record, "record_digest")
    _validate_complete_digest("record_digest", expected_record_digest)
    record_body = dict(record)
    record_body.pop("record_digest", None)
    if digest_json(record_body) != expected_record_digest:
        raise ValueError("model execution record digest mismatch")
    record_ref = _require_mapping(
        attempt.get("model_execution_record_ref"),
        "model_execution_record_ref",
    )
    if record_ref.get("record_digest") != expected_record_digest:
        raise ValueError("model execution record ref digest mismatch")
    if (
        record.get("schema_version") != "tokenshare.paper_model_execution_record.v2"
        or record.get("attempt_id") != attempt.get("attempt_id")
        or record.get("condition_id") != attempt.get("condition_id")
        or record.get("repeat_id") != attempt.get("repeat_id")
        or record.get("identity_status") != "matched"
        or record.get("paper_eligible") is not True
        or record.get("source_provider_config_digest")
        != attempt.get("source_provider_config_digest")
        or record.get("requested_model") != BASELINE_PROVIDER_MODEL_ID
        or record.get("resolved_model") != BASELINE_PROVIDER_MODEL_ID
    ):
        raise ValueError("model execution record identity mismatch")
    return reasons


def _validate_complete_artifact_ref(value: Any, field_name: str) -> JsonObject:
    body = _validate_artifact_ref(value, field_name)
    for required_field in (
        "artifact_type",
        "content_hash",
        "artifact_schema_id",
    ):
        _required_str(body, required_field)
    _validate_complete_digest(f"{field_name}.content_hash", body.get("content_hash"))
    return body


def _validate_attempt_transport(run: Mapping[str, Any]) -> tuple[str, bool]:
    transport_kind = _required_str(run, "transport_kind")
    attempts_real = True
    for group_name in ("attempts", "baseline_attempts"):
        attempts = _require_sequence(run.get(group_name, ()), group_name)
        if not attempts:
            label = "attempt" if group_name == "attempts" else "baseline attempt"
            raise ValueError(f"{label} evidence is required")
        for attempt in attempts:
            body = _require_mapping(attempt, "attempt")
            attempt_transport = body.get("transport_kind")
            if attempt_transport is None and transport_kind != "ai_api":
                attempt_transport = transport_kind
            if attempt_transport != transport_kind:
                raise ValueError("attempt transport conflicts with run transport")
            if attempt_transport != "ai_api" or body.get("paper_eligible") is not True:
                attempts_real = False
    return transport_kind, attempts_real


def _validate_attempt_aggregates(run: Mapping[str, Any]) -> None:
    _validate_attempt_group_aggregate(
        run,
        group_name="attempts",
        total_tokens_field="total_tokens",
        cost_field="cost_estimate",
        wasted_tokens_field="wasted_actual_tokens",
    )
    _validate_attempt_group_aggregate(
        run,
        group_name="baseline_attempts",
        total_tokens_field="baseline_total_tokens",
        cost_field="baseline_cost_estimate",
        wasted_tokens_field=None,
    )


def _validate_attempt_group_aggregate(
    run: Mapping[str, Any],
    *,
    group_name: str,
    total_tokens_field: str,
    cost_field: str,
    wasted_tokens_field: str | None,
) -> None:
    attempts = _require_sequence(run.get(group_name, ()), group_name)
    if not attempts:
        raise ValueError(f"{group_name} evidence is required")
    attempt_ids: set[str] = set()
    total_tokens = 0
    total_cost = 0.0
    wasted_tokens = 0
    for attempt in attempts:
        body = _require_mapping(attempt, "attempt")
        attempt_id = _required_str(body, "attempt_id")
        if attempt_id in attempt_ids:
            raise ValueError("duplicate attempt evidence")
        attempt_ids.add(attempt_id)
        total_tokens += _non_negative_int(body, "total_tokens")
        total_cost += _number(body, "cost_estimate")
        if wasted_tokens_field is not None:
            wasted_tokens += _non_negative_int(body, "wasted_actual_tokens")
    if total_tokens != _non_negative_int(run, total_tokens_field):
        raise ValueError(f"{total_tokens_field} does not match attempt usage evidence")
    if not math.isclose(
        total_cost,
        _number(run, cost_field),
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise ValueError(f"{cost_field} does not match attempt usage evidence")
    if (
        wasted_tokens_field is not None
        and wasted_tokens != _non_negative_int(run, wasted_tokens_field)
    ):
        raise ValueError(
            f"{wasted_tokens_field} does not match attempt usage evidence"
        )


def _validate_baseline_comparison(run: Mapping[str, Any]) -> None:
    condition = _require_mapping(run.get("condition_evidence"), "condition_evidence")
    baseline = _require_mapping(run.get("baseline_evidence"), "baseline_evidence")
    expected_baseline = _require_mapping(
        run.get("expected_baseline_evidence"),
        "expected_baseline_evidence",
    )
    if condition.get("condition_id") != run.get("condition_id"):
        raise ValueError("condition evidence id mismatch")
    if baseline.get("condition_id") != run.get("matched_baseline_condition_id"):
        raise ValueError("baseline comparison condition id mismatch")
    if expected_baseline.get("condition_id") != run.get(
        "expected_baseline_condition_id"
    ):
        raise ValueError("expected baseline evidence condition id mismatch")
    if dict(expected_baseline) != dict(baseline):
        raise ValueError(
            "expected baseline evidence mismatch in baseline comparison"
        )
    for body in (condition, baseline):
        for field_name in (
            "condition_digest",
            "selection_digest",
            "catalog_digest",
            "source_provider_config_digest",
            "model_endpoint_identity_digest",
        ):
            _validate_complete_digest(field_name, body.get(field_name))
        _case_tuple(body.get("ordered_case_ids"))
        _non_negative_int(body, "repeat_id")
        _required_int(body, "seed")
        _positive_int(body, "worker_count")
        _normalized_request_limit_policy(
            _require_mapping(body.get("request_limits"), "request_limits")
        )
        for field_name in (
            "provider_config_id",
            "model_entry_id",
            "provider_family",
            "provider_model_id",
            "reasoning_profile_id",
            "prompt_version",
            "parser_version",
            "plugin_version",
            "executor_version",
        ):
            _required_str(body, field_name)
    comparison_fields = (
        "selection_digest",
        "ordered_case_ids",
        "repeat_id",
        "seed",
        "worker_count",
        "catalog_digest",
        "provider_config_id",
        "model_entry_id",
        "provider_family",
        "provider_model_id",
        "reasoning_profile_id",
        "source_provider_config_digest",
        "model_endpoint_identity_digest",
        "request_limits",
        "prompt_version",
        "parser_version",
        "plugin_version",
        "executor_version",
    )
    if any(
        condition.get(field_name) != baseline.get(field_name)
        for field_name in comparison_fields
    ):
        raise ValueError("baseline comparison evidence mismatch")


def _validate_fault_records(
    run: Mapping[str, Any],
    *,
    condition_id: str,
    repeat_id: int,
    fault_type: str,
    injected_fault_count: int,
    original_output_refs: Sequence[Mapping[str, Any]],
    mutated_output_refs: Sequence[Mapping[str, Any]],
) -> None:
    records = _require_sequence(run.get("fault_records", ()), "fault_records")
    selected_target_ids = _validate_fault_target_manifest(
        run,
        condition_id=condition_id,
        repeat_id=repeat_id,
        fault_type=fault_type,
        injected_fault_count=injected_fault_count,
    )
    if injected_fault_count == 0:
        if records or original_output_refs or mutated_output_refs:
            raise ValueError(
                "zero injected faults must not declare fault records or mutation refs"
            )
        return
    if not records:
        raise ValueError("fault record evidence is required for injected faults")
    if len(records) != injected_fault_count:
        raise ValueError("fault record count must equal injected_fault_count")
    if len(original_output_refs) != injected_fault_count:
        raise ValueError("original output ref count must equal injected_fault_count")
    if len(mutated_output_refs) != injected_fault_count:
        raise ValueError("mutated output ref count must equal injected_fault_count")
    expected_original_ref_digests = _artifact_ref_digests(
        original_output_refs,
        "original_output_refs",
    )
    expected_mutated_ref_digests = _artifact_ref_digests(
        mutated_output_refs,
        "mutated_output_refs",
    )
    actual_original_ref_digests: list[str] = []
    actual_mutated_ref_digests: list[str] = []
    target_keys: set[tuple[str, str]] = set()
    for record in records:
        body = _require_mapping(record, "fault_record")
        injection_point = _required_str(body, "injection_point")
        if injection_point not in VALID_FAULT_INJECTION_POINTS:
            raise ValueError("fault injection must occur after raw persistence")
        if injection_point != FAULT_INJECTION_POINT_BY_TYPE[fault_type]:
            raise ValueError("fault injection point does not match fault type")
        if body.get("condition_id") != condition_id:
            raise ValueError("fault record condition_id mismatch")
        if body.get("repeat_id") != repeat_id:
            raise ValueError("fault record repeat_id mismatch")
        if body.get("fault_type") != fault_type:
            raise ValueError("fault record fault_type mismatch")
        if _required_int(body, "seed") != RATE_FAULT_TARGET_SEED:
            raise ValueError("fault record seed does not match target manifest")
        target_context = _require_mapping(
            body.get("target_context"),
            "fault_record.target_context",
        )
        target_selection_digest = _required_str(body, "target_selection_digest")
        _validate_complete_digest(
            "target_selection_digest",
            target_selection_digest,
        )
        if target_selection_digest != digest_json(target_context):
            raise ValueError("fault record target selection digest mismatch")
        target_key = (
            _required_str(body, "unit_id"),
            _required_str(body, "attempt_id"),
        )
        if target_context.get("unit_id") != target_key[0]:
            raise ValueError("fault record target context unit mismatch")
        if target_key in target_keys:
            raise ValueError("fault records must reference distinct targets")
        target_keys.add(target_key)
        for field_name in (
            "original_raw_output_ref",
            "original_provenance_ref",
            "original_output_ref",
            "mutated_output_ref",
            "mutated_provenance_ref",
        ):
            _validate_artifact_ref(body.get(field_name), field_name)
        actual_original_ref_digests.append(
            digest_json(body["original_output_ref"])
        )
        actual_mutated_ref_digests.append(
            digest_json(body["mutated_output_ref"])
        )
        raw_persisted_at = _parse_timestamp(
            body.get("raw_persisted_at"),
            "raw_persisted_at",
        )
        provenance_persisted_at = _parse_timestamp(
            body.get("provenance_persisted_at"),
            "provenance_persisted_at",
        )
        injected_at = _parse_timestamp(body.get("injected_at"), "injected_at")
        mutation_persisted_at = _parse_timestamp(
            body.get("mutation_provenance_persisted_at"),
            "mutation_provenance_persisted_at",
        )
        if raw_persisted_at > injected_at or provenance_persisted_at > injected_at:
            raise ValueError("raw/provenance must be persisted before fault injection")
        if mutation_persisted_at < injected_at:
            raise ValueError("mutation provenance cannot predate fault injection")
        if body.get("canonical_pollution") is not False:
            raise ValueError("fault injection must not pollute canonical output")
        if _non_negative_int(body, "provider_tokens_attributed") != 0:
            raise ValueError("synthetic mutation must not attribute provider tokens")
    if sorted(actual_original_ref_digests) != sorted(expected_original_ref_digests):
        raise ValueError("fault record original_output_ref does not match summary")
    if sorted(actual_mutated_ref_digests) != sorted(expected_mutated_ref_digests):
        raise ValueError("fault record mutated_output_ref does not match summary")
    if {unit_id for unit_id, _attempt_id in target_keys} != set(selected_target_ids):
        raise ValueError("fault records do not match frozen target manifest")


def _validate_fault_target_manifest(
    run: Mapping[str, Any],
    *,
    condition_id: str,
    repeat_id: int,
    fault_type: str,
    injected_fault_count: int,
) -> tuple[str, ...]:
    manifest = _require_mapping(
        run.get("fault_target_manifest"),
        "fault_target_manifest",
    )
    if (
        manifest.get("condition_id") != condition_id
        or manifest.get("fault_type") != fault_type
        or manifest.get("fault_rate_percent") != run.get("fault_rate_percent")
        or manifest.get("repeat_id") != repeat_id
    ):
        raise ValueError("fault target manifest condition mismatch")
    condition_evidence = _require_mapping(
        run.get("condition_evidence"),
        "condition_evidence",
    )
    if manifest.get("selection_digest") != condition_evidence.get("selection_digest"):
        raise ValueError("fault target manifest selection digest mismatch")
    if manifest.get("target_seed") != RATE_FAULT_TARGET_SEED:
        raise ValueError("fault target manifest seed mismatch")
    selected = _string_tuple(
        manifest.get("selected_target_ai_unit_ids"),
        "selected_target_ai_unit_ids",
    )
    if len(selected) != injected_fault_count:
        raise ValueError("fault target manifest count mismatch")
    return selected


def _artifact_ref_digests(
    values: Sequence[Mapping[str, Any]],
    field_name: str,
) -> list[str]:
    digests = [
        digest_json(_validate_artifact_ref(value, field_name))
        for value in values
    ]
    if len(set(digests)) != len(digests):
        raise ValueError("summary artifact refs must be unique")
    return digests


def _validate_artifact_ref(value: Any, field_name: str) -> JsonObject:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} artifact ref must be a mapping")
    body = dict(value)
    artifact_id = body.get("artifact_id")
    if not isinstance(artifact_id, str) or not artifact_id:
        raise ValueError(f"{field_name} artifact ref must include artifact_id")
    return body


def _required_persisted_artifact_ref(
    artifact_store: ArtifactStore,
    value: Any,
    message: str,
) -> ArtifactRef:
    if not isinstance(value, Mapping):
        raise ValueError(message)
    try:
        artifact_ref = ArtifactRef.from_dict(dict(value))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(message) from exc
    if not artifact_store.verify(artifact_ref):
        raise ValueError(message)
    return artifact_ref


def _parse_timestamp(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc


def _validate_worker_death_records(
    run: Mapping[str, Any],
    *,
    condition_id: str,
    repeat_id: int,
    actual_dead_worker_count: int,
    target_kill_progress_percent: int,
    actual_kill_progress_percent: int,
) -> list[JsonObject]:
    records = _require_sequence(
        run.get("worker_death_records", ()),
        "worker_death_records",
    )
    record_refs = _json_list(run.get("worker_death_record_refs", []))
    if actual_dead_worker_count > 0 and not records:
        raise ValueError("worker death record evidence is required")
    if len(records) != actual_dead_worker_count:
        raise ValueError(
            "worker death record count must equal actual_dead_worker_count"
        )
    if len(record_refs) != actual_dead_worker_count:
        raise ValueError(
            "worker death record ref count must equal actual_dead_worker_count"
        )
    if len(set(digest_json(ref) for ref in record_refs)) != len(record_refs):
        raise ValueError("worker death record refs must be unique")
    expected_record_digests: list[str] = []
    actual_record_digests: list[str] = []
    worker_evidence_keys: set[tuple[str, str, str, int]] = set()
    for record, record_ref in zip(records, record_refs, strict=True):
        body = _require_mapping(record, "worker_death_record")
        record_digest = digest_json(body)
        ref_body = _validate_artifact_ref(record_ref, "worker_death_record_ref")
        ref_record_digest = _required_str(ref_body, "record_digest")
        _validate_complete_digest("record_digest", ref_record_digest)
        if ref_record_digest != record_digest:
            raise ValueError("worker death record ref digest mismatch")
        actual_record_digests.append(ref_record_digest)
        if body.get("schema_version") != WORKER_DEATH_RECORD_SCHEMA_VERSION:
            raise ValueError("worker death record schema drift")
        if body.get("condition_id") != condition_id:
            raise ValueError("worker death record condition_id mismatch")
        if body.get("repeat_id") != repeat_id:
            raise ValueError("worker death record repeat_id mismatch")
        if body.get("kill_point") != f"progress_{target_kill_progress_percent}":
            raise ValueError("worker death record kill point mismatch")
        progress_before_kill = _non_negative_int(body, "progress_before_kill")
        if progress_before_kill < target_kill_progress_percent:
            raise ValueError("worker death record progress is before kill target")
        if progress_before_kill != actual_kill_progress_percent:
            raise ValueError(
                "actual kill progress must match worker death record evidence"
            )
        worker_pid = _positive_int(body, "worker_pid")
        replacement_worker_pid = _positive_int(body, "replacement_worker_pid")
        if worker_pid == replacement_worker_pid:
            raise ValueError("worker death record must use independent workers")
        worker_exitcode = _required_int(body, "worker_process_exitcode")
        if worker_exitcode == 0:
            raise ValueError("worker death record must show killed worker exit")
        replacement_exitcode = _required_int(body, "replacement_process_exitcode")
        if replacement_exitcode != 0:
            raise ValueError("worker death record replacement worker did not complete")
        coordinator = _require_mapping(
            body.get("coordinator"),
            "worker_death_record.coordinator",
        )
        if (
            coordinator.get("survived") is not True
            or coordinator.get("waited_for_lease_expiry") is not True
        ):
            raise ValueError("worker death record coordinator did not continue")
        lease_expiry = _require_mapping(
            body.get("lease_expiry"),
            "worker_death_record.lease_expiry",
        )
        if lease_expiry.get("trigger") != "lease_expired":
            raise ValueError("worker death record must include lease expiry")
        dead_attempt = _require_mapping(
            body.get("dead_attempt"),
            "worker_death_record.dead_attempt",
        )
        replacement_attempt = _require_mapping(
            body.get("replacement_attempt"),
            "worker_death_record.replacement_attempt",
        )
        if dead_attempt.get("role") != "killed_worker":
            raise ValueError("worker death record dead attempt role mismatch")
        if replacement_attempt.get("role") != "replacement_worker":
            raise ValueError("worker death record replacement attempt role mismatch")
        if dead_attempt.get("attempt_id") == replacement_attempt.get("attempt_id"):
            raise ValueError("worker death record replacement attempt must be distinct")
        if dead_attempt.get("worker_id") != body.get("worker_id"):
            raise ValueError("worker death record dead attempt worker mismatch")
        if dead_attempt.get("worker_pid") != worker_pid:
            raise ValueError("worker death record dead attempt pid mismatch")
        if dead_attempt.get("process_exitcode") != worker_exitcode:
            raise ValueError("worker death record dead attempt exit mismatch")
        if replacement_attempt.get("worker_id") != body.get("replacement_worker_id"):
            raise ValueError(
                "worker death record replacement attempt worker mismatch"
            )
        if replacement_attempt.get("worker_pid") != replacement_worker_pid:
            raise ValueError("worker death record replacement attempt pid mismatch")
        if replacement_attempt.get("process_exitcode") != replacement_exitcode:
            raise ValueError("worker death record replacement attempt exit mismatch")
        reassignment = _require_mapping(
            body.get("reassignment"),
            "worker_death_record.reassignment",
        )
        if reassignment.get("from_worker_id") != body.get("worker_id"):
            raise ValueError("worker death record reassignment mismatch")
        if reassignment.get("original_attempt_id") != dead_attempt.get("attempt_id"):
            raise ValueError("worker death record reassignment mismatch")
        if reassignment.get("to_worker_id") != body.get("replacement_worker_id"):
            raise ValueError("worker death record reassignment mismatch")
        if reassignment.get("replacement_attempt_id") != replacement_attempt.get(
            "attempt_id"
        ):
            raise ValueError("worker death record reassignment mismatch")
        if reassignment.get("replacement_lease_id") != replacement_attempt.get(
            "lease_id"
        ):
            raise ValueError("worker death record reassignment mismatch")
        if body.get("canonical_pollution") is not False:
            raise ValueError("worker death record must not pollute canonical output")
        if _non_negative_int(body, "provider_tokens_attributed") != 0:
            raise ValueError("worker death record must not attribute provider tokens")
        evidence_key = (
            _required_str(body, "task_id"),
            _required_str(body, "worker_id"),
            _required_str(dead_attempt, "attempt_id"),
            worker_pid,
        )
        if evidence_key in worker_evidence_keys:
            raise ValueError("distinct worker death records are required")
        worker_evidence_keys.add(evidence_key)
        expected_record_digests.append(record_digest)
    if len(set(expected_record_digests)) != len(expected_record_digests):
        raise ValueError("distinct worker death records are required")
    if actual_record_digests != expected_record_digests:
        raise ValueError("worker death record ref digest mismatch")
    return record_refs


def _validate_rate_fault_membership(
    *,
    domain: str,
    fault_type: str,
    fault_rate_percent: int,
) -> None:
    _validate_domain(domain)
    if fault_type not in RATE_FAULT_TYPES:
        raise ValueError("unsupported frozen fault type")
    allowed_rates = (
        FACTOR_RATE_FAULT_RATES_PERCENT
        if domain == "factorization"
        else LEAN_RATE_FAULT_RATES_PERCENT
    )
    if fault_rate_percent not in allowed_rates:
        raise ValueError("unsupported frozen rate")


def _validate_domain(domain: str) -> None:
    if domain not in ("factorization", "lean_proof"):
        raise ValueError("domain must be factorization or lean_proof")


def _require_numerator_leq_denominator(
    numerator_name: str,
    numerator: int,
    denominator_name: str,
    denominator: int,
) -> None:
    if numerator > denominator:
        raise ValueError(f"{numerator_name} must not exceed {denominator_name}")


def _denominator_rate(
    numerator: int,
    denominator: int,
    *,
    zero_applicability: str,
) -> JsonObject:
    if denominator == 0:
        return {"rate": None, "applicability": zero_applicability}
    return {"rate": numerator / denominator, "applicability": "matched_denominator"}


def _overhead(value: float, baseline: float) -> JsonObject:
    delta = value - baseline
    if baseline == 0:
        return {
            "delta": delta,
            "ratio": None,
            "applicability": "zero_baseline_denominator",
        }
    return {
        "delta": delta,
        "ratio": delta / baseline,
        "applicability": "matched_baseline",
    }


def _rate(run: Mapping[str, Any], field_name: str, task_count: int) -> float:
    return _non_negative_int(run, field_name) / task_count


def _catalog_mapping(value: Any) -> Mapping[str, Any]:
    return _require_mapping(value, "catalog")


def _catalog_digest(catalog: Mapping[str, Any]) -> str:
    value = catalog.get("catalog_digest")
    _validate_complete_digest("catalog_digest", value)
    return value


def _validate_complete_digest(field_name: str, value: Any) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{field_name} must be a complete sha256 digest")


def _ai_units_by_case_id(catalog: Mapping[str, Any]) -> Mapping[str, tuple[str, ...]]:
    raw = _mapping(catalog["ai_units_by_case_id"])
    normalized: dict[str, tuple[str, ...]] = {}
    for case_id, values in raw.items():
        normalized[str(case_id)] = _case_tuple(values)
    return normalized


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("expected mapping")
    return value


def _required_catalog_value(catalog: Mapping[str, Any], key: str) -> Any:
    try:
        return catalog[key]
    except KeyError as exc:
        raise _MissingCatalogSlice(f"missing Exp3 catalog slice: {key}") from exc


def _case_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("case ids must be a sequence")
    result = tuple(str(item) for item in value)
    if not result or any(not item for item in result):
        raise ValueError("case ids must be non-empty")
    if len(set(result)) != len(result):
        raise ValueError("case ids must be unique")
    return result


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a sequence")
    result = tuple(str(item) for item in value)
    if any(not item for item in result):
        raise ValueError(f"{field_name} values must be non-empty")
    if len(set(result)) != len(result):
        raise ValueError(f"{field_name} values must be unique")
    return result


def _require_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    return value


def _require_sequence(value: Any, field_name: str) -> Sequence[Any]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a sequence")
    return value


def _required_str(body: Mapping[str, Any], field_name: str) -> str:
    value = body.get(field_name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _non_negative_int(body: Mapping[str, Any], field_name: str) -> int:
    value = body.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be an integer >= 0")
    return value


def _required_int(body: Mapping[str, Any], field_name: str) -> int:
    value = body.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _positive_int(body: Mapping[str, Any], field_name: str) -> int:
    value = _non_negative_int(body, field_name)
    if value <= 0:
        raise ValueError(f"{field_name} must be > 0")
    return value


def _number(body: Mapping[str, Any], field_name: str) -> float:
    value = body.get(field_name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be numeric")
    if not math.isfinite(float(value)):
        raise ValueError(f"{field_name} must be finite")
    return float(value)


def _bool(body: Mapping[str, Any], field_name: str) -> bool:
    value = body.get(field_name)
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a bool")
    return value


def _json_list(value: Any) -> list[JsonObject]:
    if not isinstance(value, list):
        raise ValueError("expected JSON list")
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("JSON list items must be objects")
    return [dict(item) for item in value]


def _reject_non_finite_row(row: Mapping[str, Any]) -> None:
    for field_name, value in row.items():
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{field_name} must be finite")


__all__ = [
    "BASELINE_MODEL_ENTRY_ID",
    "BASELINE_PROVIDER_MODEL_ID",
    "EXP3_EXPERIMENT_ID",
    "Exp3FaultInjectionOutcome",
    "Experiment3FaultRecoveryModule",
    "build_exp3_plan_manifest",
    "expand_exp3_conditions",
    "freeze_exp3_case_selections",
    "inject_exp3_post_ai_fault",
    "summarize_exp3",
    "validate_exp3_condition_matrix",
]
