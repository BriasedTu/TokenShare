"""Experiment 3 fault/recovery module.

本模块只冻结 Prompt E 的实验矩阵、case selection 和 summary 契约。
真实 provider 调用、共享 runner 接入、CSV/report 写盘留给 Gate C owner。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from tokenshare.experiments.paper_experiment_contracts import (
    ExperimentSummaryRows,
    FrozenCaseSelection,
    PaperExecutionContext,
)
from tokenshare.experiments.paper_faults import PaperFaultType, select_fault_targets
from tokenshare.experiments.paper_models import (
    JsonObject,
    LEAN_TOPIC_FAMILIES,
    PaperConditionResult,
    PaperExperimentCondition,
    PaperStatus,
    digest_json,
)


EXP3_EXPERIMENT_ID = "exp3_real_ai_fault_recovery"
EXP3_SCHEMA_VERSION = "tokenshare.paper_exp3_fault_recovery.v1"
PAPER_CONDITION_V2 = "tokenshare.paper_condition.v2"

BASELINE_PROVIDER_FAMILY = "siliconflow"
BASELINE_PROVIDER_MODEL_ID = "zai-org/GLM-5.2"
BASELINE_MODEL_ENTRY_ID = "glm_5_2_exp1_baseline"
BASELINE_REASONING_PROFILE_ID = "temperature_0_thinking_false"

RATE_FAULT_TYPES = tuple(fault.value for fault in PaperFaultType)
FACTOR_RATE_FAULT_RATES_PERCENT = (0, 1, 5, 10, 25, 50, 100)
LEAN_RATE_FAULT_RATES_PERCENT = (0, 10, 50, 100)
REPEAT_IDS = (0, 1, 2)

WORKER_DEATH_WORKER_COUNT = 10
WORKER_DEATH_COUNTS = (1, 3)
WORKER_DEATH_KILL_PROGRESS_PERCENT = (25, 50, 75)
RATE_FAULT_TARGET_SEED = 300300

_FACTOR_DIFFICULTIES = ("easy", "medium", "hard")
_RATE_FACTOR_SELECTION_ID = "exp3_rate_fault_factorization_slice_v1"


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


class Experiment3FaultRecoveryModule:
    """Gate B compatible wrapper for Prompt E."""

    def expand_conditions(
        self,
        context: PaperExecutionContext,
    ) -> tuple[PaperExperimentCondition, ...]:
        return expand_exp3_conditions(catalog_digest=_catalog_digest(context.catalog))

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
        _validate_baseline_condition(condition)
        if selection.is_blocked:
            return PaperConditionResult(
                condition_id=condition.condition_id,
                status=PaperStatus.BLOCKED,
                repeat_count=1,
                task_count=0,
                completed_root_count=0,
                failed_root_count=0,
                blocked_root_count=1,
                provider_attempt_count=0,
                metrics_ref=None,
            )
        return context.execution_callback(condition=condition, selection=selection)

    def summarize(self, evidence: Mapping[str, Any]) -> ExperimentSummaryRows:
        return summarize_exp3(evidence)


def expand_exp3_conditions(
    *,
    catalog_digest: str = "sha256:" + "0" * 64,
) -> tuple[PaperExperimentCondition, ...]:
    _validate_complete_digest("catalog_digest", catalog_digest)
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
                    )
                )
    for topic_family in LEAN_TOPIC_FAMILIES:
        for fault_type in RATE_FAULT_TYPES:
            for rate_percent in LEAN_RATE_FAULT_RATES_PERCENT:
                for repeat_id in REPEAT_IDS:
                    conditions.append(
                        _condition_from_key(
                            _ConditionKey(
                                matrix_kind="rate_fault",
                                domain="lean_proof",
                                task_slice_key=topic_family,
                                difficulty="medium",
                                paper_difficulty="medium_lemma_dag",
                                topic_family=topic_family,
                                fault_type=fault_type,
                                fault_rate_percent=rate_percent,
                                repeat_id=repeat_id,
                            ),
                            catalog_digest=catalog_digest,
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
    for condition, selection in zip(conditions, selections, strict=True):
        _validate_baseline_condition(condition)
        expected_case_ids = _expected_case_ids_for_condition(condition, catalog=catalog)
        if selection.is_blocked:
            continue
        if tuple(selection.ordered_case_ids) != expected_case_ids:
            raise ValueError(
                f"slice drift for {condition.condition_id}: "
                f"{tuple(selection.ordered_case_ids)!r} != {expected_case_ids!r}"
            )
        if selection.paper_eligible_required is not True:
            raise ValueError("Exp3 selections must require paper eligibility")


def summarize_exp3(evidence: Mapping[str, Any]) -> ExperimentSummaryRows:
    body = _require_mapping(evidence, "evidence")
    rows: list[JsonObject] = []
    for run in _require_sequence(body.get("rate_fault_runs", ()), "rate_fault_runs"):
        rows.append(_summarize_rate_fault_run(_require_mapping(run, "rate_fault_run")))
    for run in _require_sequence(
        body.get("worker_death_runs", ()),
        "worker_death_runs",
    ):
        rows.append(_summarize_worker_death_run(_require_mapping(run, "worker_death_run")))
    return ExperimentSummaryRows(experiment_id=EXP3_EXPERIMENT_ID, rows=tuple(rows))


def _condition_from_key(
    key: _ConditionKey,
    *,
    catalog_digest: str,
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
        provider_family=BASELINE_PROVIDER_FAMILY,
        provider_model_id=BASELINE_PROVIDER_MODEL_ID,
        model_entry_id=BASELINE_MODEL_ENTRY_ID,
        reasoning_profile_id=BASELINE_REASONING_PROFILE_ID,
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
            f"exp3_rate_fault_lean__{key.topic_family}__{key.fault_type}"
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
        topic_family, fault_type, rate_part, repeat_part = parts[1:]
        return _ConditionKey(
            matrix_kind="rate_fault",
            domain="lean_proof",
            task_slice_key=topic_family,
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
    try:
        case_ids = _expected_case_ids_for_condition(condition, catalog=catalog)
        expected_ai_units = _expected_ai_unit_count(case_ids, catalog=catalog)
        return FrozenCaseSelection(
            selection_id=_selection_id_for_key(key),
            experiment_id=EXP3_EXPERIMENT_ID,
            suite_version=suite_version,
            catalog_version=catalog_version,
            domain=key.domain,
            paper_difficulty=key.paper_difficulty,
            topic_family=key.topic_family,
            ordered_case_ids=case_ids,
            catalog_digest=catalog_digest,
            expected_ai_unit_count=expected_ai_units,
            paper_eligible_required=True,
        )
    except (KeyError, TypeError, ValueError):
        return FrozenCaseSelection(
            selection_id=_selection_id_for_key(key),
            experiment_id=EXP3_EXPERIMENT_ID,
            suite_version=suite_version,
            catalog_version=catalog_version,
            domain=key.domain,
            paper_difficulty=key.paper_difficulty,
            topic_family=key.topic_family,
            ordered_case_ids=(),
            catalog_digest=catalog_digest,
            expected_ai_unit_count=0,
            paper_eligible_required=True,
            blocked_reason="missing_exp3_catalog_slice",
        )


def _selection_id_for_key(key: _ConditionKey) -> str:
    if key.matrix_kind == "rate_fault" and key.domain == "factorization":
        return _RATE_FACTOR_SELECTION_ID
    if key.matrix_kind == "rate_fault" and key.domain == "lean_proof":
        return f"exp3_rate_fault_lean_{key.task_slice_key}_slice_v1"
    if key.matrix_kind == "worker_death" and key.domain == "factorization":
        return f"exp3_worker_death_factorization_{key.task_slice_key}_slice_v1"
    return f"exp3_worker_death_lean_{key.task_slice_key}_slice_v1"


def _expected_case_ids_for_condition(
    condition: PaperExperimentCondition,
    *,
    catalog: Mapping[str, Any],
) -> tuple[str, ...]:
    key = _parse_condition_id(condition.condition_id)
    if key.matrix_kind == "rate_fault" and key.domain == "factorization":
        return _case_tuple(catalog["exp3_rate_fault_factorization_case_ids"])
    if key.matrix_kind == "rate_fault" and key.domain == "lean_proof":
        return _case_tuple(
            _mapping(catalog["exp3_rate_fault_lean_case_ids_by_topic"])[
                str(key.topic_family)
            ]
        )
    if key.matrix_kind == "worker_death" and key.domain == "factorization":
        return _case_tuple(
            _mapping(
                catalog["exp3_worker_death_factorization_case_ids_by_difficulty"]
            )[key.task_slice_key]
        )
    return _case_tuple(
        _mapping(catalog["exp3_worker_death_lean_case_ids_by_topic"])[
            key.task_slice_key
        ]
    )


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


def _summarize_rate_fault_run(run: Mapping[str, Any]) -> JsonObject:
    _validate_attempt_model_entries(run)
    _validate_fault_records(run)
    matched_baseline = _required_str(run, "matched_baseline_condition_id")
    expected_baseline = run.get("expected_baseline_condition_id")
    if expected_baseline is not None and expected_baseline != matched_baseline:
        raise ValueError("baseline mismatch for Experiment 3 rate-fault run")
    task_count = _positive_int(run, "task_count")
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
        "domain": _required_str(run, "domain"),
        "fault_type": _required_str(run, "fault_type"),
        "fault_rate_percent": _non_negative_int(run, "fault_rate_percent"),
        "repeat_id": _non_negative_int(run, "repeat_id"),
        "task_count": task_count,
        "completed_root_count": _non_negative_int(run, "completed_root_count"),
        "matched_baseline_condition_id": matched_baseline,
        "detected_fault_count": _non_negative_int(run, "detected_fault_count"),
        "false_accept_count": _non_negative_int(run, "false_accept_count"),
        "recovered_root_count": _non_negative_int(run, "recovered_root_count"),
        "detection_rate": _rate(run, "detected_fault_count", task_count),
        "false_accept_rate": _rate(run, "false_accept_count", task_count),
        "recovery_rate": _rate(run, "recovered_root_count", task_count),
        "completion_rate": _rate(run, "completed_root_count", task_count),
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
        "original_output_refs": _json_list(run.get("original_output_refs")),
        "mutated_output_refs": _json_list(run.get("mutated_output_refs")),
        "provider_tokens_attributed_by_mutation": 0,
        "transport_kind": _required_str(run, "transport_kind"),
        "paper_eligible": _bool(run, "paper_eligible"),
    }
    _reject_non_finite_row(row)
    return row


def _summarize_worker_death_run(run: Mapping[str, Any]) -> JsonObject:
    _validate_attempt_model_entries(run)
    target_dead = _non_negative_int(run, "target_dead_worker_count")
    actual_dead = _non_negative_int(run, "actual_dead_worker_count")
    required_slots = _positive_int(run, "required_slot_count")
    recovered_slots = _non_negative_int(run, "recovered_slot_count")
    if recovered_slots > required_slots:
        raise ValueError("recovered_slot_count must not exceed required_slot_count")
    completeness = recovered_slots / required_slots
    target_progress = _non_negative_int(run, "target_kill_progress_percent")
    actual_progress = _non_negative_int(run, "actual_kill_progress_percent")
    mismatch_reason = None
    if actual_dead != target_dead:
        mismatch_reason = "actual_dead_count_mismatch"
    elif actual_progress != target_progress:
        mismatch_reason = "actual_kill_progress_mismatch"

    row: JsonObject = {
        "schema_version": "tokenshare.paper_exp3_worker_death_summary_row.v1",
        "experiment_id": EXP3_EXPERIMENT_ID,
        "summary_kind": "worker_death",
        "condition_id": _required_str(run, "condition_id"),
        "domain": _required_str(run, "domain"),
        "repeat_id": _non_negative_int(run, "repeat_id"),
        "task_count": _positive_int(run, "task_count"),
        "target_dead_worker_count": target_dead,
        "actual_dead_worker_count": actual_dead,
        "target_kill_progress_percent": target_progress,
        "actual_kill_progress_percent": actual_progress,
        "coordinator_continued": _bool(run, "coordinator_continued"),
        "required_slot_count": required_slots,
        "recovered_slot_count": recovered_slots,
        "result_completeness_rate": completeness,
        "root_output_complete": _bool(run, "root_output_complete"),
        "accepted_validity": _bool(run, "accepted_validity"),
        "recovery_latency_ms": _non_negative_int(run, "recovery_latency_ms"),
        "retry_count": _non_negative_int(run, "retry_count"),
        "reassignment_count": _non_negative_int(run, "reassignment_count"),
        "wall_clock_ms": _number(run, "wall_clock_ms"),
        "total_tokens": _number(run, "total_tokens"),
        "cost_estimate": _number(run, "cost_estimate"),
        "condition_included": mismatch_reason is None,
        "run_status": "failed" if mismatch_reason else "completed",
        "failure_reason": mismatch_reason,
        "transport_kind": _required_str(run, "transport_kind"),
        "paper_eligible": _bool(run, "paper_eligible"),
    }
    _reject_non_finite_row(row)
    return row


def _validate_baseline_condition(condition: PaperExperimentCondition) -> None:
    if condition.experiment_id != EXP3_EXPERIMENT_ID:
        raise ValueError("condition experiment_id is not Exp3")
    if (
        condition.model_entry_id != BASELINE_MODEL_ENTRY_ID
        or condition.provider_family != BASELINE_PROVIDER_FAMILY
        or condition.provider_model_id != BASELINE_PROVIDER_MODEL_ID
    ):
        raise ValueError("model failover is forbidden for Experiment 3")


def _validate_attempt_model_entries(run: Mapping[str, Any]) -> None:
    attempts = _require_sequence(run.get("attempts", ()), "attempts")
    for attempt in attempts:
        attempt_body = _require_mapping(attempt, "attempt")
        if attempt_body.get("entry_id") != BASELINE_MODEL_ENTRY_ID:
            raise ValueError("model failover is forbidden for Experiment 3")


def _validate_fault_records(run: Mapping[str, Any]) -> None:
    records = _require_sequence(run.get("fault_records", ()), "fault_records")
    for record in records:
        body = _require_mapping(record, "fault_record")
        injection_point = _required_str(body, "injection_point")
        if not (
            injection_point.startswith("after_raw_output")
            or injection_point.startswith("after_parsed_candidate")
        ):
            raise ValueError("fault injection must occur after raw persistence")
        for field_name in (
            "original_raw_output_ref",
            "original_output_ref",
            "mutated_output_ref",
        ):
            if not isinstance(body.get(field_name), Mapping):
                raise ValueError(f"{field_name} is required in fault record")
        if _non_negative_int(body, "provider_tokens_attributed") != 0:
            raise ValueError("synthetic mutation must not attribute provider tokens")


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


def _case_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("case ids must be a sequence")
    result = tuple(str(item) for item in value)
    if not result or any(not item for item in result):
        raise ValueError("case ids must be non-empty")
    if len(set(result)) != len(result):
        raise ValueError("case ids must be unique")
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
    "Experiment3FaultRecoveryModule",
    "build_exp3_plan_manifest",
    "expand_exp3_conditions",
    "freeze_exp3_case_selections",
    "summarize_exp3",
    "validate_exp3_condition_matrix",
]
