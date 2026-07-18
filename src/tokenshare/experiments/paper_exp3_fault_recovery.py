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
from tokenshare.experiments.paper_faults import select_fault_targets
from tokenshare.experiments.paper_models import (
    JsonObject,
    LEAN_TOPIC_FAMILIES,
    PaperConditionResult,
    PaperExperimentCondition,
    PaperStatus,
    digest_json,
)
from tokenshare.experiments.paper_workers import WORKER_DEATH_RECORD_SCHEMA_VERSION


EXP3_EXPERIMENT_ID = "exp3_real_ai_fault_recovery"
EXP3_SCHEMA_VERSION = "tokenshare.paper_exp3_fault_recovery.v1"
PAPER_CONDITION_V2 = "tokenshare.paper_condition.v2"

BASELINE_PROVIDER_FAMILY = "siliconflow"
BASELINE_PROVIDER_MODEL_ID = "zai-org/GLM-5.2"
BASELINE_MODEL_ENTRY_ID = "glm_5_2_exp1_baseline"
BASELINE_REASONING_PROFILE_ID = "temperature_0_thinking_false"

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
    expected_conditions = expand_exp3_conditions(catalog_digest=catalog_digest)
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
    try:
        case_ids = _expected_case_ids_for_condition(condition, catalog=catalog)
    except _MissingCatalogSlice:
        return FrozenCaseSelection(
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
    return FrozenCaseSelection(
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
    # Gate B FrozenCaseSelection 目前要求 Lean selection 带单个 topic_family。
    # Exp3 的 rate-fault selection 是三 topic 组合 slice，这里用首个 topic
    # 作为稳定 anchor；完整 topic slice 在 manifest 中显式冻结。
    return LEAN_TOPIC_FAMILIES[0]


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


def _summarize_rate_fault_run(run: Mapping[str, Any]) -> JsonObject:
    _validate_attempt_model_entries(run)
    matched_baseline = _required_str(run, "matched_baseline_condition_id")
    expected_baseline = _required_str(run, "expected_baseline_condition_id")
    if expected_baseline != matched_baseline:
        raise ValueError("baseline mismatch for Experiment 3 rate-fault run")
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
        "transport_kind": _required_str(run, "transport_kind"),
        "paper_eligible": _bool(run, "paper_eligible"),
    }
    _reject_non_finite_row(row)
    return row


def _summarize_worker_death_run(run: Mapping[str, Any]) -> JsonObject:
    _validate_attempt_model_entries(run)
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
    coordinator_continued = _bool(run, "coordinator_continued")
    mismatch_reason = None
    if actual_dead != target_dead:
        mismatch_reason = "actual_dead_count_mismatch"
    elif actual_progress != target_progress:
        mismatch_reason = "actual_kill_progress_mismatch"
    elif not coordinator_continued:
        mismatch_reason = "coordinator_not_continued"

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
        "root_output_complete": _bool(run, "root_output_complete"),
        "accepted_validity": _bool(run, "accepted_validity"),
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
        or condition.reasoning_profile_id != BASELINE_REASONING_PROFILE_ID
    ):
        raise ValueError("model failover is forbidden for Experiment 3")


def _validate_attempt_model_entries(run: Mapping[str, Any]) -> None:
    attempts = _require_sequence(run.get("attempts", ()), "attempts")
    if not attempts:
        raise ValueError("attempt evidence is required for Experiment 3 summary")
    for attempt in attempts:
        attempt_body = _require_mapping(attempt, "attempt")
        if (
            attempt_body.get("entry_id") != BASELINE_MODEL_ENTRY_ID
            or attempt_body.get("provider") != BASELINE_PROVIDER_FAMILY
            or attempt_body.get("model") != BASELINE_PROVIDER_MODEL_ID
            or attempt_body.get("reasoning_profile_id")
            != BASELINE_REASONING_PROFILE_ID
        ):
            raise ValueError("model failover is forbidden for Experiment 3")


def _validate_fault_records(
    run: Mapping[str, Any],
    *,
    injected_fault_count: int,
    original_output_refs: Sequence[Mapping[str, Any]],
    mutated_output_refs: Sequence[Mapping[str, Any]],
) -> None:
    records = _require_sequence(run.get("fault_records", ()), "fault_records")
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
    for record in records:
        body = _require_mapping(record, "fault_record")
        injection_point = _required_str(body, "injection_point")
        if injection_point not in VALID_FAULT_INJECTION_POINTS:
            raise ValueError("fault injection must occur after raw persistence")
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
        if _non_negative_int(body, "provider_tokens_attributed") != 0:
            raise ValueError("synthetic mutation must not attribute provider tokens")
    if sorted(actual_original_ref_digests) != sorted(expected_original_ref_digests):
        raise ValueError("fault record original_output_ref does not match summary")
    if sorted(actual_mutated_ref_digests) != sorted(expected_mutated_ref_digests):
        raise ValueError("fault record mutated_output_ref does not match summary")


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
    "Experiment3FaultRecoveryModule",
    "build_exp3_plan_manifest",
    "expand_exp3_conditions",
    "freeze_exp3_case_selections",
    "summarize_exp3",
    "validate_exp3_condition_matrix",
]
