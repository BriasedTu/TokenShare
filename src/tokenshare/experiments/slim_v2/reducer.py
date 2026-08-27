"""Slim V2 Exp1–5 唯一离线统计 reducer。

只读取冻结 inventory、committed root result 与独立 Exp3 reference。模块不读取
raw response、system event 或 artifact，也不调用 runtime、checker 或 provider。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import csv
from io import StringIO
import json
from math import floor, isfinite, sqrt
from pathlib import Path
import random
from statistics import median
from typing import Any, Callable, Iterable, Mapping, Sequence
from uuid import uuid4

from .schema import (
    LEGACY_PRICING_VERSION,
    PRICING_VERSION,
    AttemptResultV1,
    RootInventoryV1,
    RootResultV2,
)
from .storage import RunStore


BOOTSTRAP_REPLICATES = 10_000
BOOTSTRAP_SEED = 20260820
_TABLE_METADATA = {
    "ci_method": "stratified_case_cluster_percentile_bootstrap",
    "ci_level": 0.95,
    "bootstrap_replicates": BOOTSTRAP_REPLICATES,
    "bootstrap_seed": BOOTSTRAP_SEED,
    "quantile_method": "hyndman_fan_type_7",
}
_EXP5_V4_REFERENCE_TABLE_ID = "exp5_with_exp1_v4_reference"
_EXP5_V4_REFERENCE_LIVE_MODELS = (
    "zai-org/GLM-5.2",
    "Qwen/Qwen3-14B",
    "MiniMaxAI/MiniMax-M2.5",
)
_EXP5_V4_REFERENCE_SOURCE_MODEL = "deepseek-v4-flash"
_EXP5_V4_REFERENCE_WALL_CLOCK_FIELDS = (
    "repeat0_wall_clock_ms",
    "repeat1_wall_clock_ms",
    "repeat2_wall_clock_ms",
    "repeat_wall_clock_ms",
    "model_wall_clock_median_ms",
    "model_wall_clock_min_ms",
    "model_wall_clock_max_ms",
    "model_wall_clock_range_ms",
    "model_wall_clock_sample_stddev_ms",
)

# 这是 authority fixture 的有序 occurrence 合同；重复项对应不同实验/table scope。
_FORMAL_METRIC_IDS = (
    "preregistered_root_count", "final_result_root_count",
    "verified_correct_root_count", "completion_rate",
    "end_to_end_verified_success_rate", "no_final_failure_count",
    "incorrect_final_failure_count", "infra_invalid_failure_count",
    "failure_root_count", "actual_end_to_end_wall_clock_ms",
    "actual_provider_latency_ms", "actual_total_tokens",
    "actual_cost_estimate_cny", "completion_rate",
    "root_end_to_end_elapsed_ms", "root_provider_latency_ms",
    "root_total_tokens", "root_cost_estimate_cny",
    "trace_replay_wall_clock_ms", "bank_slot_consumption",
    "trace_attributed_tokens", "trace_attributed_cost",
    "trace_replay_paired_speedup", "trace_replay_parallel_efficiency",
    "paired_trace_token_multiplier", "paired_trace_cost_multiplier",
    "paired_speedup_planned_pair_count", "paired_speedup_eligible_pair_count",
    "paired_speedup_ineligible_pair_count",
    "trace_replay_paired_speedup_median",
    "trace_replay_paired_speedup_repeat_min",
    "trace_replay_paired_speedup_repeat_max",
    "trace_replay_paired_speedup_relative_difference", "planned_ai_unit_count",
    "executed_ai_unit_count", "unscheduled_ai_unit_count", "in_flight_at_witness",
    "observed_peak_concurrency", "worker_utilization", "completion_rate",
    "trace_replay_wall_clock_ms", "trace_replay_paired_speedup",
    "paired_trace_token_multiplier", "observed_peak_concurrency",
    "injected_fault_target_count", "controlled_wrong_candidate_count",
    "controlled_wrong_candidate_interception_count",
    "controlled_wrong_candidate_interception_rate",
    "controlled_wrong_candidate_escape_count",
    "controlled_wrong_candidate_escape_rate", "started_replacement_attempt_count",
    "successful_replacement_attempt_count", "replacement_attempt_success_rate",
    "reassignment_count", "discarded_simulated_trace_tokens",
    "simulated_wall_clock_overhead_ms", "simulated_token_overhead",
    "simulated_trace_attributed_cost_overhead",
    "kill_progress_error_signed_mean_pp", "kill_progress_error_signed_max_pp",
    "recovered_valid_canonical_required_slots", "preregistered_required_slots",
    "result_completeness_rate", "unrecovered_root_count", "completion_rate",
    "controlled_wrong_candidate_interception_rate",
    "replacement_attempt_success_rate", "result_completeness_rate",
    "simulated_wall_clock_overhead_ms",
    "discarded_simulated_trace_tokens_per_root",
    "kill_progress_error_signed_mean_pp", "protocol_started_root_count",
    "preflight_blocked_root_count", "protocol_start_coverage",
    "challenge_planned_root_count", "challenge_target_opportunity_count",
    "challenge_injection_count", "missed_challenge_opportunity_count",
    "challenge_applied_root_count", "challenge_application_coverage",
    "challenge_plan_mismatch_count", "no_final_rate", "incorrect_final_rate",
    "required_slot_completion_rate", "actual_execution_attempt_count",
    "source_response_slot_consumption", "planned_pair_count",
    "runtime_valid_pair_count", "runtime_invalid_pair_count",
    "full_success_ablation_success", "full_success_ablation_failure",
    "full_failure_ablation_success", "full_failure_ablation_failure",
    "ablation_failure_given_full_success_rate",
    "paired_end_to_end_success_loss_vs_full",
    "paired_completion_loss_vs_full",
    "paired_required_slot_completion_loss_vs_full",
    "trace_replay_wall_clock_delta_vs_full", "execution_attempt_delta_vs_full",
    "source_slot_consumption_delta_vs_full",
    "trace_attributed_token_delta_vs_full",
    "trace_attributed_cost_delta_vs_full",
    "independently_labeled_invalid_candidate_count",
    "invalid_candidate_verifier_rejection_count", "wrong_canonical_acceptance_count",
    "wrong_canonical_acceptance_rate",
    "root_checker_rejection_after_wrong_canonical_count", "parser_required_input_count",
    "raw_only_exposure_count", "raw_only_acceptance_count",
    "raw_only_acceptance_rate", "recoverable_no_return_count",
    "replacement_started_after_challenge_count", "valid_final_after_challenge_count",
    "valid_final_after_challenge_rate", "stuck_task_count", "stuck_task_rate",
    "merge_gate_unsatisfied_observation_count", "premature_merge_attempt_count",
    "premature_merge_failure_count", "premature_merge_failure_rate",
    "completion_rate", "required_slot_completion_rate",
    "ablation_failure_given_full_success_rate",
    "trace_replay_wall_clock_delta_vs_full", "wrong_canonical_acceptance_rate",
    "single_removal_success_loss_i", "actual_first_provider_attempt_count",
    "first_attempt_without_verifier_accepted_candidate_count",
    "first_attempt_nonpass_rate", "first_attempt_provider_transport_failure_count",
    "first_attempt_parse_schema_unusable_count",
    "first_attempt_verification_checker_rejection_count",
    "first_attempt_checkable_candidate_count",
    "first_attempt_explicitly_rejected_by_verifier_count",
    "first_attempt_verification_rejection_rate",
    "planned_first_attempt_ai_unit_count", "actual_first_provider_attempt_count",
    "first_attempt_call_coverage", "actual_total_tokens", "actual_cost_estimate_cny",
    "repeat0_wall_clock_ms", "model_wall_clock_median_ms",
    "model_wall_clock_min_ms", "model_wall_clock_max_ms",
    "model_wall_clock_range_ms", "completion_rate", "first_attempt_nonpass_rate",
    "first_attempt_verification_rejection_rate", "first_attempt_call_coverage",
    "root_actual_total_tokens", "root_actual_cost_estimate_cny",
    "repeat_wall_clock_ms",
)


def _occurrence_scope(ordinal: int) -> tuple[str, str, str]:
    """把 authority occurrence 定位到真实 table row 与公式 producer。"""

    if ordinal < 18:
        return "exp1", "cell", "_common" if ordinal < 9 or ordinal == 13 else "_reduce_exp1"
    if ordinal < 44:
        pair = ordinal in set(range(22, 33)) | {41, 42}
        return "exp2", "pair" if pair else "cell", "_reduce_exp2"
    if ordinal < 71:
        if ordinal in {55, 56, 57}:
            return "exp3", "fault_reference_pair", "_reduce_exp3"
        if ordinal == 68:
            return "exp3", "death_reference_pair", "_reduce_exp3"
        return "exp3", "cell", "_exp3_cell_metrics"
    if ordinal < 127:
        if ordinal in set(range(86, 102)) | {123, 124}:
            return "exp4", "pair", "_reduce_exp4_pairs"
        if ordinal == 126:
            return "exp4", "interaction", "_reduce_exp4_interactions"
        if ordinal in set(range(102, 121)) | {125}:
            return "exp4", "cell", "_exp4_special_metrics"
        return "exp4", "cell", "_exp4_cell"
    return "exp5", "model", "_reduce_exp5"


_FORMAL_METRIC_OCCURRENCE_BASES = tuple(
    {
        "metric_id": metric_id,
        "ordinal": ordinal,
        "table_scope": scope,
        "row_kind": row_kind,
        "formula_producer": producer,
    }
    for ordinal, metric_id in enumerate(_FORMAL_METRIC_IDS)
    for scope, row_kind, producer in (_occurrence_scope(ordinal),)
)


_COMMON_METRICS = (
    "preregistered_root_count", "scientifically_valid_root_count",
    "infrastructure_invalid_root_count", "final_result_root_count",
    "verified_correct_root_count", "completion_rate",
    "end_to_end_verified_success_rate", "no_final_failure_count",
    "incorrect_final_failure_count", "infra_invalid_failure_count",
    "failure_root_count",
)
_EXP1_METRICS = _COMMON_METRICS + (
    "actual_end_to_end_wall_clock_ms", "actual_provider_latency_ms",
    "actual_total_tokens", "actual_cost_estimate_cny", "root_end_to_end_elapsed_ms",
    "root_provider_latency_ms", "root_total_tokens", "root_cost_estimate_cny",
)
_EXP2_METRICS = _COMMON_METRICS + (
    "trace_replay_wall_clock_ms", "bank_slot_consumption", "trace_attributed_tokens",
    "trace_attributed_cost", "trace_replay_paired_speedup",
    "trace_replay_parallel_efficiency", "paired_trace_token_multiplier",
    "paired_trace_cost_multiplier", "paired_speedup_planned_pair_count",
    "paired_speedup_eligible_pair_count", "paired_speedup_ineligible_pair_count",
    "trace_replay_paired_speedup_median",
    "trace_replay_paired_speedup_repeat_min",
    "trace_replay_paired_speedup_repeat_max",
    "trace_replay_paired_speedup_relative_difference", "planned_ai_unit_count",
    "executed_ai_unit_count", "unscheduled_ai_unit_count", "in_flight_at_witness",
    "observed_peak_concurrency", "worker_utilization",
)
_EXP3_METRICS = _COMMON_METRICS + (
    "injected_fault_target_count", "controlled_wrong_candidate_count",
    "controlled_wrong_candidate_interception_count",
    "controlled_wrong_candidate_interception_rate",
    "controlled_wrong_candidate_escape_count", "controlled_wrong_candidate_escape_rate",
    "started_replacement_attempt_count", "successful_replacement_attempt_count",
    "replacement_attempt_success_rate", "reassignment_count",
    "discarded_simulated_trace_tokens", "simulated_wall_clock_overhead_ms",
    "simulated_token_overhead", "simulated_trace_attributed_cost_overhead",
    "kill_progress_error_signed_mean_pp", "kill_progress_error_signed_max_pp",
    "recovered_valid_canonical_required_slots", "preregistered_required_slots",
    "result_completeness_rate", "unrecovered_root_count",
    "discarded_simulated_trace_tokens_per_root",
)
_EXP4_METRICS = _COMMON_METRICS + (
    "protocol_started_root_count", "preflight_blocked_root_count",
    "protocol_start_coverage", "challenge_planned_root_count",
    "challenge_target_opportunity_count", "challenge_injection_count",
    "missed_challenge_opportunity_count", "challenge_applied_root_count",
    "challenge_application_coverage", "challenge_plan_mismatch_count",
    "no_final_rate", "incorrect_final_rate", "required_slot_completion_rate",
    "actual_execution_attempt_count", "source_response_slot_consumption",
    "planned_pair_count", "runtime_valid_pair_count", "runtime_invalid_pair_count",
    "full_success_ablation_success", "full_success_ablation_failure",
    "full_failure_ablation_success", "full_failure_ablation_failure",
    "ablation_failure_given_full_success_rate",
    "paired_end_to_end_success_loss_vs_full", "paired_completion_loss_vs_full",
    "paired_required_slot_completion_loss_vs_full",
    "trace_replay_wall_clock_delta_vs_full", "execution_attempt_delta_vs_full",
    "source_slot_consumption_delta_vs_full", "trace_attributed_token_delta_vs_full",
    "trace_attributed_cost_delta_vs_full",
    "independently_labeled_invalid_candidate_count",
    "invalid_candidate_verifier_rejection_count", "wrong_canonical_acceptance_count",
    "wrong_canonical_acceptance_rate",
    "root_checker_rejection_after_wrong_canonical_count", "parser_required_input_count",
    "raw_only_exposure_count", "raw_only_acceptance_count", "raw_only_acceptance_rate",
    "recoverable_no_return_count", "replacement_started_after_challenge_count",
    "valid_final_after_challenge_count", "valid_final_after_challenge_rate",
    "stuck_task_count", "stuck_task_rate",
    "merge_gate_unsatisfied_observation_count", "premature_merge_attempt_count",
    "premature_merge_failure_count", "premature_merge_failure_rate",
    "single_removal_success_loss_i", "pair_removal_success_loss_ij",
    "pair_interaction_success_penalty_ij",
    "pair_interaction_completion_penalty_ij",
    "pair_interaction_required_slot_penalty_ij", "planned_quadruple_count",
    "eligible_quadruple_count", "ineligible_quadruple_count",
)
_EXP5_METRICS = _COMMON_METRICS + (
    "actual_first_provider_attempt_count",
    "first_attempt_without_verifier_accepted_candidate_count",
    "first_attempt_nonpass_rate", "first_attempt_provider_transport_failure_count",
    "first_attempt_parse_schema_unusable_count",
    "first_attempt_verification_checker_rejection_count",
    "first_attempt_checkable_candidate_count",
    "first_attempt_explicitly_rejected_by_verifier_count",
    "first_attempt_verification_rejection_rate",
    "planned_first_attempt_ai_unit_count", "first_attempt_call_coverage",
    "actual_total_tokens", "actual_cost_estimate_cny", "repeat0_wall_clock_ms",
    "repeat1_wall_clock_ms", "repeat2_wall_clock_ms", "model_wall_clock_median_ms",
    "model_wall_clock_min_ms", "model_wall_clock_max_ms", "model_wall_clock_range_ms",
    "root_actual_total_tokens", "root_actual_cost_estimate_cny",
    "repeat_wall_clock_ms", "model_wall_clock_sample_stddev_ms",
)

_RATE_INTERVAL_METRICS = frozenset(
    {
        "completion_rate", "end_to_end_verified_success_rate",
        "controlled_wrong_candidate_interception_rate",
        "controlled_wrong_candidate_escape_rate",
        "replacement_attempt_success_rate", "result_completeness_rate",
        "no_final_rate", "incorrect_final_rate", "required_slot_completion_rate",
        "ablation_failure_given_full_success_rate",
        "wrong_canonical_acceptance_rate", "raw_only_acceptance_rate",
        "valid_final_after_challenge_rate", "stuck_task_rate",
        "premature_merge_failure_rate", "first_attempt_nonpass_rate",
        "first_attempt_verification_rejection_rate", "first_attempt_call_coverage",
    }
)
_DISTRIBUTION_METRICS = frozenset(
    {
        "root_end_to_end_elapsed_ms", "root_provider_latency_ms",
        "root_total_tokens", "root_cost_estimate_cny",
        "trace_replay_wall_clock_ms", "trace_replay_paired_speedup",
        "trace_replay_parallel_efficiency", "paired_trace_token_multiplier",
        "paired_trace_cost_multiplier", "observed_peak_concurrency",
        "worker_utilization", "simulated_wall_clock_overhead_ms",
        "simulated_token_overhead", "simulated_trace_attributed_cost_overhead",
        "discarded_simulated_trace_tokens_per_root",
        "trace_replay_wall_clock_delta_vs_full",
        "execution_attempt_delta_vs_full", "source_slot_consumption_delta_vs_full",
        "trace_attributed_token_delta_vs_full",
        "trace_attributed_cost_delta_vs_full", "root_actual_total_tokens",
        "root_actual_cost_estimate_cny",
    }
)
_MEAN_INTERVAL_METRICS = frozenset(
    {
        "paired_end_to_end_success_loss_vs_full",
        "paired_completion_loss_vs_full",
        "paired_required_slot_completion_loss_vs_full",
        "single_removal_success_loss_i", "pair_removal_success_loss_ij",
        "pair_interaction_success_penalty_ij",
        "pair_interaction_completion_penalty_ij",
        "pair_interaction_required_slot_penalty_ij",
        "kill_progress_error_signed_mean_pp",
    }
)
_INTERVAL_SUFFIXES = (
    "ci95_low", "ci95_high", "bootstrap_variance", "bootstrap_standard_error",
)
_DISTRIBUTION_SUFFIXES = (
    "median", "q25", "q75", "iqr", "sample_variance", "sample_stddev",
)


def _occurrence_evidence_fields(occurrence: Mapping[str, Any]) -> tuple[str, ...]:
    """冻结单个 formal occurrence 的实际字段合同。"""

    metric = str(occurrence["metric_id"])
    fields: list[str] = [metric]
    metadata_metric = metric
    if metric == "trace_replay_paired_speedup_median":
        metadata_metric = "trace_replay_paired_speedup"
        fields.extend(
            f"trace_replay_paired_speedup_{suffix}"
            for suffix in _DISTRIBUTION_SUFFIXES[1:]
        )
        fields.extend(f"{metric}_{suffix}" for suffix in _INTERVAL_SUFFIXES)
    elif metric in _DISTRIBUTION_METRICS:
        fields.extend(f"{metric}_{suffix}" for suffix in _DISTRIBUTION_SUFFIXES)
        fields.extend(
            f"{metric}_median_{suffix}" for suffix in _INTERVAL_SUFFIXES
        )
    elif metric in _RATE_INTERVAL_METRICS or metric in _MEAN_INTERVAL_METRICS:
        fields.extend(f"{metric}_{suffix}" for suffix in _INTERVAL_SUFFIXES)
    else:
        return tuple(fields)
    fields.extend(
        (
            f"metric_metadata.{metadata_metric}.case_cluster_count",
            f"metric_metadata.{metadata_metric}.valid_bootstrap_replicate_count",
        )
    )
    if occurrence["row_kind"] == "pair" and metric in _DISTRIBUTION_METRICS:
        if metric == "trace_replay_paired_speedup":
            fields.extend(
                (
                    "paired_speedup_planned_pair_count",
                    "paired_speedup_eligible_pair_count",
                    "paired_speedup_ineligible_pair_count",
                )
            )
        else:
            fields.extend(
                f"{metric}_{suffix}"
                for suffix in (
                    "planned_pair_count", "eligible_pair_count",
                    "ineligible_pair_count",
                )
            )
    if occurrence["row_kind"] == "interaction":
        fields.extend(
            f"{metric}_{suffix}"
            for suffix in (
                "planned_quadruple_count", "eligible_quadruple_count",
                "ineligible_quadruple_count",
            )
        )
    return tuple(dict.fromkeys(fields))


_FORMAL_METRIC_OCCURRENCES = tuple(
    {**occurrence, "evidence_fields": _occurrence_evidence_fields(occurrence)}
    for occurrence in _FORMAL_METRIC_OCCURRENCE_BASES
)


@dataclass(frozen=True, slots=True)
class _Observation:
    inventory: RootInventoryV1
    result: RootResultV2 | None
    planned_challenge_family: str | None = None


def metric_ids() -> tuple[str, ...]:
    """返回冻结 authority 中 153 个有序 metric occurrences。"""

    return tuple(item["metric_id"] for item in _FORMAL_METRIC_OCCURRENCES)


def formal_metric_occurrences() -> tuple[dict[str, Any], ...]:
    """返回带 table/row/producer 身份的冻结 formal occurrence 合同。"""

    return tuple(dict(item) for item in _FORMAL_METRIC_OCCURRENCES)


def type7_quantile(values: Sequence[float], probability: float) -> float | None:
    """Hyndman–Fan type 7 线性分位数。"""

    if not 0 <= probability <= 1:
        raise ValueError("probability must be in [0, 1]")
    normalized = sorted(float(value) for value in values if _finite(value))
    if not normalized:
        return None
    position = (len(normalized) - 1) * probability
    lower = floor(position)
    upper = min(lower + 1, len(normalized) - 1)
    fraction = position - lower
    return normalized[lower] + fraction * (normalized[upper] - normalized[lower])


def sample_variance(values: Sequence[float]) -> float | None:
    """按 n-1 分母计算样本方差；n<2 为 null。"""

    normalized = [float(value) for value in values if _finite(value)]
    if len(normalized) < 2:
        return None
    mean_value = sum(normalized) / len(normalized)
    return sum((value - mean_value) ** 2 for value in normalized) / (
        len(normalized) - 1
    )


def stratified_case_cluster_bootstrap(
    rows: Sequence[Mapping[str, Any]],
    statistic: Callable[[Sequence[Mapping[str, Any]]], float | None],
    *,
    strata_fields: Sequence[str] = (
        "domain", "difficulty", "topic_family", "position_stratum"
    ),
    replicates: int = BOOTSTRAP_REPLICATES,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """从原始观察按 strata 内 case cluster 有放回重采样。"""

    if replicates != BOOTSTRAP_REPLICATES or seed != BOOTSTRAP_SEED:
        raise ValueError("Slim V2 bootstrap replicate count and seed are frozen")
    strata: dict[tuple[Any, ...], dict[str, list[Mapping[str, Any]]]] = {}
    for row in rows:
        case_id = row.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("bootstrap rows require non-empty case_id")
        stratum = tuple(row.get(field) for field in strata_fields)
        strata.setdefault(stratum, {}).setdefault(case_id, []).append(row)
    case_cluster_count = sum(len(cases) for cases in strata.values())
    base = {
        "ci_method": _TABLE_METADATA["ci_method"],
        "ci_level": 0.95,
        "bootstrap_replicates": replicates,
        "bootstrap_seed": seed,
        "case_cluster_count": case_cluster_count,
        "valid_bootstrap_replicate_count": 0,
        "ci95_low": None,
        "ci95_high": None,
        "bootstrap_variance": None,
        "bootstrap_standard_error": None,
        "missing_reason": None,
    }
    if case_cluster_count < 5:
        base["missing_reason"] = "insufficient_case_clusters_for_interval"
        return base
    ordered = [
        (stratum, sorted(cases.items()))
        for stratum, cases in sorted(strata.items(), key=lambda item: repr(item[0]))
    ]
    rng = random.Random(seed)
    estimates: list[float] = []
    for _replicate in range(replicates):
        sampled: list[Mapping[str, Any]] = []
        for _stratum, cases in ordered:
            for _index in range(len(cases)):
                sampled.extend(cases[rng.randrange(len(cases))][1])
        try:
            estimate = statistic(sampled)
        except (ArithmeticError, TypeError, ValueError):
            estimate = None
        if _finite(estimate):
            estimates.append(float(estimate))
    base["valid_bootstrap_replicate_count"] = len(estimates)
    if len(estimates) < 9_500:
        base["missing_reason"] = "insufficient_valid_bootstrap_replicates"
        return base
    variance = sample_variance(estimates)
    base.update(
        ci95_low=type7_quantile(estimates, 0.025),
        ci95_high=type7_quantile(estimates, 0.975),
        bootstrap_variance=variance,
        bootstrap_standard_error=(sqrt(variance) if variance is not None else None),
    )
    return base


def _finite(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and isfinite(float(value))
    )


def _ratio(numerator: float | int | None, denominator: float | int | None) -> float | None:
    if not _finite(numerator) or not _finite(denominator) or float(denominator) == 0:
        return None
    return float(numerator) / float(denominator)


def _mean(values: Sequence[float]) -> float | None:
    normalized = [float(value) for value in values if _finite(value)]
    return sum(normalized) / len(normalized) if normalized else None


def _sum_nullable(values: Iterable[float | int | None]) -> float | int | None:
    materialized = list(values)
    if any(not _finite(value) for value in materialized):
        return None
    return sum(materialized) if materialized else 0


def _distribution(values: Sequence[float | int | None]) -> dict[str, Any]:
    normalized = [float(value) for value in values if _finite(value)]
    variance = sample_variance(normalized)
    result = {
        "median": type7_quantile(normalized, 0.5),
        "q25": type7_quantile(normalized, 0.25),
        "q75": type7_quantile(normalized, 0.75),
        "iqr": None,
        "sample_variance": variance,
        "sample_stddev": sqrt(variance) if variance is not None else None,
        "missing_reason": None,
    }
    if result["q25"] is not None and result["q75"] is not None:
        result["iqr"] = result["q75"] - result["q25"]
    if len(normalized) < 2:
        result["missing_reason"] = "insufficient_observations_for_sample_variance"
    return result


def _base_row(table_id: str, row_kind: str, slice_values: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "table_id": table_id,
        "row_kind": row_kind,
        "slice": dict(slice_values),
        "missing_reasons": {},
        "not_applicable_reasons": {},
        "metric_metadata": {},
        "table_metadata": dict(_TABLE_METADATA),
    }


def _apply_template(row: dict[str, Any], metric_names: Sequence[str]) -> dict[str, Any]:
    reasons = row["missing_reasons"]
    for name in metric_names:
        if name not in row:
            row[name] = None
            reasons[name] = "metric_inputs_not_present_in_this_run"
    return row


def _materialize_null_evidence(
    row: dict[str, Any],
    metric: str,
    reason: str,
) -> None:
    metadata_metric = (
        "trace_replay_paired_speedup"
        if metric == "trace_replay_paired_speedup_median"
        else metric
    )
    if metric in _DISTRIBUTION_METRICS:
        for suffix in _DISTRIBUTION_SUFFIXES:
            row.setdefault(f"{metric}_{suffix}", None)
        for suffix in _INTERVAL_SUFFIXES:
            row.setdefault(f"{metric}_median_{suffix}", None)
    elif metric == "trace_replay_paired_speedup_median":
        for suffix in _INTERVAL_SUFFIXES:
            row.setdefault(f"{metric}_{suffix}", None)
    elif metric in _RATE_INTERVAL_METRICS or metric in _MEAN_INTERVAL_METRICS:
        for suffix in _INTERVAL_SUFFIXES:
            row.setdefault(f"{metric}_{suffix}", None)
    else:
        return
    metadata = row.setdefault("metric_metadata", {}).setdefault(
        metadata_metric,
        {
            **_TABLE_METADATA,
            "case_cluster_count": 0,
            "valid_bootstrap_replicate_count": 0,
            "ci95_low": None,
            "ci95_high": None,
            "bootstrap_variance": None,
            "bootstrap_standard_error": None,
            "missing_reason": reason,
        },
    )
    if metadata.get("missing_reason") is None:
        metadata["missing_reason"] = reason


def _set_value(row: dict[str, Any], name: str, value: Any) -> None:
    row[name] = value
    row["missing_reasons"].pop(name, None)
    row["not_applicable_reasons"].pop(name, None)


def _set_missing(row: dict[str, Any], name: str, reason: str) -> None:
    row[name] = None
    row["missing_reasons"][name] = reason
    row["not_applicable_reasons"].pop(name, None)
    _materialize_null_evidence(row, name, reason)


def _set_not_applicable(row: dict[str, Any], name: str, reason: str = "not_applicable") -> None:
    row[name] = None
    row["missing_reasons"].pop(name, None)
    row["not_applicable_reasons"][name] = reason
    _materialize_null_evidence(row, name, reason)


def _set_ratio(
    row: dict[str, Any],
    name: str,
    numerator: float | int,
    denominator: float | int,
) -> None:
    if not _finite(numerator) or not _finite(denominator):
        raise ValueError(f"ratio inputs must be finite: {name}")
    if float(denominator) == 0:
        _set_missing(row, name, "zero_denominator")
        return
    _set_value(row, name, float(numerator) / float(denominator))


def _identity(observation: _Observation) -> tuple[str, str, str, int]:
    item = observation.inventory
    return (
        str(item.experiment_id), str(item.condition_id), str(item.case_id),
        int(item.repeat_id),
    )


def _exp4_challenge_index(store: RunStore) -> dict[tuple[str, int], tuple[str, str]]:
    indexed: dict[tuple[str, int], tuple[str, str]] = {}
    for plan in store.iter_exp4_challenge_rows():
        key = (plan.case_id, plan.repeat_id)
        value = (plan.challenge_plan_id, plan.challenge_family)
        if key in indexed:
            raise ValueError(f"duplicate Exp4 challenge identity: {key!r}")
        indexed[key] = value
    return indexed


def _inventory_slice_key(
    inventory: RootInventoryV1,
    experiment_id: str,
    *,
    challenge_index: Mapping[tuple[str, int], tuple[str, str]] | None = None,
) -> tuple[Any, ...]:
    """返回不会拆散 paired/repeat/quadruple arms 的最小处理 slice。"""

    if experiment_id == "exp1":
        return (
            inventory.domain,
            inventory.difficulty,
            inventory.topic_family,
            inventory.repeat_id,
        )
    if experiment_id == "exp2":
        # 两个 repeats 必须同驻，才能生成 repeat min/max/relative difference。
        return (inventory.position_stratum,)
    if experiment_id == "exp3":
        return (
            inventory.fault_type,
            inventory.fault_rate,
            inventory.dead_worker_count,
            inventory.kill_progress_target_ratio,
            inventory.domain,
            inventory.difficulty,
            inventory.topic_family,
            inventory.repeat_id,
        )
    if experiment_id == "exp4":
        plan = (challenge_index or {}).get(
            (str(inventory.case_id), int(inventory.repeat_id))
        )
        if plan is None:
            raise ValueError("missing Exp4 challenge inventory identity")
        if inventory.challenge_plan_id != plan[0]:
            raise ValueError("Exp4 root inventory challenge_plan_id mismatch")
        # 三个 repeats 和 11 modes 同驻；case 的 matched arms 永不拆散。
        return (plan[1], inventory.domain)
    if experiment_id == "exp5":
        return (inventory.configured_model,)
    raise ValueError(f"unknown experiment table: {experiment_id}")


def _experiment_slice_keys(
    store: RunStore,
    experiment_id: str,
    *,
    challenge_index: Mapping[tuple[str, int], tuple[str, str]] | None = None,
) -> tuple[tuple[Any, ...], ...]:
    """只扫描轻量 inventory，验证全实验 identity 并冻结 slice 顺序。"""

    slice_keys: set[tuple[Any, ...]] = set()
    seen: set[tuple[str, str, str, int]] = set()
    for inventory in store.iter_root_inventory_rows("roots"):
        if inventory.experiment_id != experiment_id:
            continue
        key = (
            str(inventory.experiment_id),
            str(inventory.condition_id),
            str(inventory.case_id),
            int(inventory.repeat_id),
        )
        if key in seen:
            raise ValueError(f"duplicate root inventory identity: {key!r}")
        seen.add(key)
        slice_keys.add(
            _inventory_slice_key(
                inventory,
                experiment_id,
                challenge_index=challenge_index,
            )
        )
    return tuple(sorted(slice_keys, key=repr))


def _load_experiment_slice(
    store: RunStore,
    experiment_id: str,
    slice_key: tuple[Any, ...],
    *,
    challenge_index: Mapping[tuple[str, int], tuple[str, str]] | None = None,
) -> list[_Observation]:
    """流式 join 一个当前 slice；不累积其他 slice 的 nested result。"""

    observations: list[_Observation] = []
    for inventory in store.iter_root_inventory_rows("roots"):
        if inventory.experiment_id != experiment_id:
            continue
        if _inventory_slice_key(
            inventory,
            experiment_id,
            challenge_index=challenge_index,
        ) != slice_key:
            continue
        key = (
            str(inventory.experiment_id),
            str(inventory.condition_id),
            str(inventory.case_id),
            int(inventory.repeat_id),
        )
        result = (
            store.read_root_result(*key)
            if store.root_result_path(*key).is_file()
            else None
        )
        planned_family = None
        if experiment_id == "exp4":
            planned_family = (challenge_index or {})[
                (str(inventory.case_id), int(inventory.repeat_id))
            ][1]
        observations.append(_Observation(inventory, result, planned_family))
    return observations


def _group(
    observations: Iterable[_Observation],
    key: Callable[[_Observation], tuple[Any, ...]],
) -> list[tuple[tuple[Any, ...], list[_Observation]]]:
    grouped: dict[tuple[Any, ...], list[_Observation]] = defaultdict(list)
    for observation in observations:
        grouped[key(observation)].append(observation)
    return sorted(grouped.items(), key=lambda item: repr(item[0]))


def _is_infrastructure_invalid(result: RootResultV2) -> bool:
    """统一识别不允许进入科学指标的 root 终态。"""

    return result.failure_kind == "infrastructure_invalid"


def _common(row: dict[str, Any], observations: Sequence[_Observation]) -> None:
    row["preregistered_root_count"] = len(observations)
    missing_results = [item for item in observations if item.result is None]
    results = [item.result for item in observations]
    committed = [result for result in results if result is not None]
    final_count = sum(result.final_result_present is True for result in committed)
    correct_count = sum(result.verified_correct is True for result in committed)
    committed_invalid = sum(_is_infrastructure_invalid(result) for result in committed)
    invalid = committed_invalid + len(missing_results)
    no_final = sum(result.failure_kind == "no_final" for result in committed)
    incorrect = sum(result.failure_kind == "incorrect_final" for result in committed)
    failure_origins = Counter(
        result.failure_origin or "unspecified_failure_origin"
        for result in committed
        if result.failure_kind is not None
    )
    if missing_results:
        failure_origins["missing_committed_root_result"] += len(missing_results)
    row.update(
        scientifically_valid_root_count=len(committed) - committed_invalid,
        infrastructure_invalid_root_count=invalid,
        failure_origin_counts=dict(sorted(failure_origins.items())),
        no_final_failure_count=no_final,
        incorrect_final_failure_count=incorrect,
        infra_invalid_failure_count=committed_invalid,
        failure_root_count=no_final + incorrect + committed_invalid,
    )
    if missing_results:
        for name in ("final_result_root_count", "verified_correct_root_count"):
            _set_missing(row, name, "missing_committed_root_result")
        for name in ("completion_rate", "end_to_end_verified_success_rate"):
            _set_missing(row, name, "infrastructure_invalid_root_present")
        return
    row.update(
        final_result_root_count=final_count,
        verified_correct_root_count=correct_count,
    )
    _set_ratio(row, "completion_rate", final_count, len(observations))
    _set_ratio(
        row,
        "end_to_end_verified_success_rate",
        correct_count,
        len(observations),
    )
    if invalid:
        for name in ("completion_rate", "end_to_end_verified_success_rate"):
            _set_missing(row, name, "infrastructure_invalid_root_present")
        return
    raw_rows = [
        {
            "case_id": str(result.case_id),
            "domain": result.domain,
            "difficulty": result.difficulty,
            "topic_family": result.topic_family,
            "position_stratum": result.position_stratum,
            "final": int(bool(result.final_result_present)),
            "correct": int(bool(result.verified_correct)),
        }
        for result in committed
    ]
    for name, field in (
        ("completion_rate", "final"),
        ("end_to_end_verified_success_rate", "correct"),
    ):
        interval = stratified_case_cluster_bootstrap(
            raw_rows,
            lambda sample, field=field: _ratio(
                sum(int(item[field]) for item in sample), len(sample)
            ),
        )
        for suffix in (
            "ci95_low", "ci95_high", "bootstrap_variance",
            "bootstrap_standard_error",
        ):
            row[f"{name}_{suffix}"] = interval[suffix]
        row["metric_metadata"][name] = interval


def _result_attempt_sum(
    result: RootResultV2,
    field: str,
    *,
    protocol_only: bool = False,
    first_only: bool = False,
    source_consumed_only: bool = False,
) -> float | int | None:
    attempts = list(result.attempts)
    if protocol_only:
        attempts = [item for item in attempts if item.trace_origin == "protocol"]
    if first_only:
        attempts = [item for item in attempts if item.attempt_ordinal == 0]
    if source_consumed_only:
        attempts = [item for item in attempts if item.source_response_consumed is True]
    elif field in {
        "provider_latency_ms", "total_tokens", "cost_estimate_cny",
        "prompt_tokens", "completion_tokens",
    }:
        attempts = [item for item in attempts if item.provider_call_made is True]
    values = [getattr(item, field) for item in attempts]
    return _sum_nullable(values)


def _source_consumption(result: RootResultV2) -> int:
    return sum(item.source_response_consumed is True for item in result.attempts)


def _source_sum(result: RootResultV2, field: str) -> float | int | None:
    return _result_attempt_sum(result, field, source_consumed_only=True)


def _simulated_tokens(result: RootResultV2) -> float | int | None:
    attempts = [item for item in result.attempts if item.source_response_consumed is True]
    return _sum_nullable(item.simulated_total_tokens for item in attempts)


def _simulated_attempt_cost(attempt: AttemptResultV1) -> float | None:
    required = (
        attempt.simulated_total_tokens,
        attempt.source_prompt_tokens,
        attempt.source_prompt_cache_hit_tokens,
        attempt.source_prompt_cache_miss_tokens,
    )
    if any(not _finite(value) for value in required):
        return None
    assert attempt.simulated_total_tokens is not None
    assert attempt.source_prompt_tokens is not None
    assert attempt.source_prompt_cache_hit_tokens is not None
    assert attempt.source_prompt_cache_miss_tokens is not None
    generated = attempt.simulated_total_tokens - attempt.source_prompt_tokens
    if generated < 0:
        return None
    if (
        attempt.source_pricing_version == PRICING_VERSION
        and attempt.source_pricing_tier == "flat"
    ):
        hit_rate, miss_rate, output_rate = (0.05, 1.50, 4.50)
    elif (
        attempt.source_pricing_version == LEGACY_PRICING_VERSION
        and attempt.source_pricing_tier in {"peak", "off_peak"}
    ):
        peak = attempt.source_pricing_tier == "peak"
        hit_rate, miss_rate, output_rate = (
            (0.30, 9.00, 27.00) if peak else (0.15, 4.50, 13.50)
        )
    else:
        return None
    return (
        attempt.source_prompt_cache_hit_tokens * hit_rate
        + attempt.source_prompt_cache_miss_tokens * miss_rate
        + generated * output_rate
    ) / 1_000_000


def _simulated_cost(result: RootResultV2) -> float | None:
    attempts = [item for item in result.attempts if item.source_response_consumed is True]
    return _sum_nullable(_simulated_attempt_cost(item) for item in attempts)


def _add_distribution(
    row: dict[str, Any],
    metric: str,
    observations: Sequence[Mapping[str, Any]],
    value_field: str = "value",
) -> None:
    values = [item.get(value_field) for item in observations]
    distribution = _distribution(values)
    row[metric] = distribution["median"]
    if row[metric] is None:
        _set_missing(
            row,
            metric,
            str(distribution["missing_reason"]),
        )
    for suffix in ("median", "q25", "q75", "iqr", "sample_variance", "sample_stddev"):
        source = "median" if suffix == "median" else suffix
        row[f"{metric}_{suffix}"] = distribution[source]
    interval = stratified_case_cluster_bootstrap(
        observations,
        lambda sample: type7_quantile(
            [float(item[value_field]) for item in sample if _finite(item.get(value_field))],
            0.5,
        ),
    )
    for suffix in (
        "ci95_low", "ci95_high", "bootstrap_variance", "bootstrap_standard_error",
    ):
        row[f"{metric}_{suffix}"] = interval[suffix]
        row[f"{metric}_median_{suffix}"] = interval[suffix]
    row["metric_metadata"][metric] = {**distribution, **interval}


def _add_rate_interval(
    row: dict[str, Any],
    metric: str,
    observations: Sequence[Mapping[str, Any]],
    numerator_field: str = "numerator",
    denominator_field: str = "denominator",
) -> None:
    """按 case cluster 重算 numerator/denominator，并附加冻结区间字段。"""

    interval = stratified_case_cluster_bootstrap(
        observations,
        lambda sample: _ratio(
            sum(float(item[numerator_field]) for item in sample),
            sum(float(item[denominator_field]) for item in sample),
        ),
    )
    for suffix in (
        "ci95_low", "ci95_high", "bootstrap_variance", "bootstrap_standard_error",
    ):
        row[f"{metric}_{suffix}"] = interval[suffix]
    row["metric_metadata"][metric] = interval


def _reduce_exp1(observations: Sequence[_Observation]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    grouped = _group(
        observations,
        lambda item: (
            item.inventory.domain, item.inventory.difficulty,
            item.inventory.topic_family, item.inventory.repeat_id,
        ),
    )
    for key, group in grouped:
        slice_values = dict(
            domain=key[0], difficulty=key[1], topic_family=key[2], repeat_id=key[3]
        )
        row = _base_row("exp1", "cell", slice_values)
        _common(row, group)
        if any(item.result is None for item in group):
            for name in _EXP1_METRICS[len(_COMMON_METRICS):]:
                _set_missing(row, name, "missing_committed_root_result")
            rows.append(_apply_template(row, _EXP1_METRICS))
            continue
        results = [item.result for item in group if item.result is not None]
        actual_end_to_end = _sum_nullable(
            result.runtime_wall_clock_ms for result in results
        )
        if actual_end_to_end is None:
            _set_missing(
                row,
                "actual_end_to_end_wall_clock_ms",
                "missing_root_runtime",
            )
        else:
            row["actual_end_to_end_wall_clock_ms"] = actual_end_to_end
        resource_specs = (
            ("actual_provider_latency_ms", "root_provider_latency_ms", "provider_latency_ms"),
            ("actual_total_tokens", "root_total_tokens", "total_tokens"),
            ("actual_cost_estimate_cny", "root_cost_estimate_cny", "cost_estimate_cny"),
        )
        root_elapsed = [
            {
                "case_id": str(result.case_id), "domain": result.domain,
                "difficulty": result.difficulty, "topic_family": result.topic_family,
                "position_stratum": result.position_stratum,
                "value": result.runtime_wall_clock_ms,
            }
            for result in results
        ]
        _add_distribution(row, "root_end_to_end_elapsed_ms", root_elapsed)
        for total_name, root_name, field in resource_specs:
            values = [
                _result_attempt_sum(result, field, protocol_only=True)
                for result in results
            ]
            total = _sum_nullable(values)
            if total is None and field in {"total_tokens", "cost_estimate_cny"}:
                _set_missing(row, total_name, "usage_missing")
            else:
                _set_value(row, total_name, total)
            raw = [
                {
                    "case_id": str(result.case_id), "domain": result.domain,
                    "difficulty": result.difficulty, "topic_family": result.topic_family,
                    "position_stratum": result.position_stratum, "value": value,
                }
                for result, value in zip(results, values)
            ]
            _add_distribution(row, root_name, raw)
        rows.append(_apply_template(row, _EXP1_METRICS))
    return rows


def _worker_utilization(result: RootResultV2) -> float | None:
    if not _finite(result.runtime_wall_clock_ms) or result.runtime_wall_clock_ms == 0:
        return None
    if not isinstance(result.worker_count, int) or result.worker_count <= 0:
        return None
    if not result.worker_execution_facts:
        return None
    if any(
        not _finite(fact.started_at_ms)
        or not _finite(fact.ended_at_ms)
        or int(fact.ended_at_ms) < int(fact.started_at_ms)
        for fact in result.worker_execution_facts
    ):
        return None
    busy = sum(
        int(fact.ended_at_ms) - int(fact.started_at_ms)
        for fact in result.worker_execution_facts
    )
    return busy / (result.worker_count * result.runtime_wall_clock_ms)


def _pair_reason(
    baseline: _Observation | None,
    treatment: _Observation,
    *,
    require_correct: bool = False,
) -> str | None:
    if baseline is None:
        return "missing_baseline_inventory_arm"
    if baseline.result is None or treatment.result is None:
        return "missing_committed_root_result"
    if _is_infrastructure_invalid(baseline.result) or _is_infrastructure_invalid(
        treatment.result
    ):
        return "infrastructure_invalid_root_present"
    if require_correct and (
        baseline.result.verified_correct is not True
        or treatment.result.verified_correct is not True
    ):
        return "pair_root_not_verified_correct"
    return None


def _pair_fields(
    row: dict[str, Any],
    metric: str,
    planned: int,
    values: Sequence[Mapping[str, Any]],
    reasons: Counter[str],
    *,
    use_mean: bool = False,
    missing_reason: str | None = None,
) -> None:
    prefix = metric
    row[f"{prefix}_planned_pair_count"] = planned
    row[f"{prefix}_eligible_pair_count"] = len(values)
    row[f"{prefix}_ineligible_pair_count"] = planned - len(values)
    row[f"{prefix}_ineligible_reason_counts"] = dict(sorted(reasons.items()))
    if missing_reason is not None:
        _set_missing(row, metric, missing_reason)
        return
    if use_mean:
        row[metric] = _mean([float(item["value"]) for item in values])
        interval = stratified_case_cluster_bootstrap(
            values,
            lambda sample: _mean([float(item["value"]) for item in sample]),
        )
        for suffix in (
            "ci95_low", "ci95_high", "bootstrap_variance",
            "bootstrap_standard_error",
        ):
            row[f"{metric}_{suffix}"] = interval[suffix]
        row["metric_metadata"][metric] = interval
    else:
        _add_distribution(row, metric, values)
    if not values:
        _set_missing(row, metric, "no_eligible_matched_observations")


def _reduce_exp2(observations: Sequence[_Observation]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cell_groups = _group(
        observations,
        lambda item: (
            item.inventory.worker_count, item.inventory.repeat_id,
            item.inventory.position_stratum,
        ),
    )
    for key, group in cell_groups:
        row = _base_row(
            "exp2", "cell",
            dict(worker_count=key[0], repeat_id=key[1], position_stratum=key[2]),
        )
        _common(row, group)
        if any(item.result is None for item in group):
            for name in (
                "trace_replay_wall_clock_ms", "bank_slot_consumption",
                "trace_attributed_tokens", "trace_attributed_cost",
                "planned_ai_unit_count", "executed_ai_unit_count",
                "unscheduled_ai_unit_count", "in_flight_at_witness",
                "observed_peak_concurrency", "worker_utilization",
            ):
                _set_missing(row, name, "missing_committed_root_result")
        else:
            results = [item.result for item in group if item.result is not None]
            elapsed = [
                {
                    "case_id": str(result.case_id), "domain": result.domain,
                    "difficulty": result.difficulty, "topic_family": result.topic_family,
                    "position_stratum": result.position_stratum,
                    "value": result.runtime_wall_clock_ms,
                }
                for result in results
            ]
            if any(item["value"] is None for item in elapsed):
                _set_missing(
                    row, "trace_replay_wall_clock_ms", "missing_root_runtime"
                )
            else:
                _add_distribution(row, "trace_replay_wall_clock_ms", elapsed)
            row["bank_slot_consumption"] = sum(
                _source_consumption(result) for result in results
            )
            row["trace_attributed_tokens"] = _sum_nullable(
                _source_sum(result, "source_total_tokens") for result in results
            )
            if row["trace_attributed_tokens"] is None:
                _set_missing(
                    row, "trace_attributed_tokens", "missing_source_usage"
                )
            row["trace_attributed_cost"] = _sum_nullable(
                _source_sum(result, "source_cost_estimate_cny") for result in results
            )
            if row["trace_attributed_cost"] is None:
                _set_missing(
                    row, "trace_attributed_cost", "missing_source_cost"
                )
            row["planned_ai_unit_count"] = sum(len(item.inventory.planned_ai_unit_ids) for item in group)
            row["executed_ai_unit_count"] = sum(len(result.dispatched_ai_unit_ids) for result in results)
            row["unscheduled_ai_unit_count"] = sum(len(result.unscheduled_ai_unit_ids) for result in results)
            row["in_flight_at_witness"] = _sum_nullable(
                len(result.in_flight_ai_unit_ids_at_witness)
                if result.in_flight_ai_unit_ids_at_witness is not None else None
                for result in results
            )
            if row["in_flight_at_witness"] is None:
                _set_missing(
                    row, "in_flight_at_witness", "missing_in_flight_witness"
                )
            peak_rows = [
                {
                    "case_id": str(result.case_id), "domain": result.domain,
                    "difficulty": result.difficulty, "topic_family": result.topic_family,
                    "position_stratum": result.position_stratum,
                    "value": result.observed_peak_concurrency,
                }
                for result in results
            ]
            if any(item["value"] is None for item in peak_rows):
                _set_missing(
                    row,
                    "observed_peak_concurrency",
                    "missing_worker_execution_intervals",
                )
            else:
                _add_distribution(row, "observed_peak_concurrency", peak_rows)
            utilization_rows = [
                {**peak, "value": _worker_utilization(result)}
                for peak, result in zip(peak_rows, results)
            ]
            if any(item["value"] is None for item in utilization_rows):
                _set_missing(
                    row,
                    "worker_utilization",
                    "missing_worker_execution_intervals",
                )
            else:
                _add_distribution(row, "worker_utilization", utilization_rows)
        rows.append(_apply_template(row, _EXP2_METRICS))

    indexed = {
        (
            str(item.inventory.case_id), int(item.inventory.repeat_id),
            item.inventory.position_stratum, int(item.inventory.worker_count),
        ): item
        for item in observations
    }
    pair_rows: list[dict[str, Any]] = []
    treatment_groups = _group(
        (item for item in observations if item.inventory.worker_count != 1),
        lambda item: (
            item.inventory.worker_count, item.inventory.repeat_id,
            item.inventory.position_stratum,
        ),
    )
    for key, group in treatment_groups:
        worker_count, repeat_id, position = key
        row = _base_row(
            "exp2", "pair",
            dict(worker_count=worker_count, repeat_id=repeat_id, position_stratum=position),
        )
        metric_values: dict[str, list[dict[str, Any]]] = defaultdict(list)
        reason_counts: dict[str, Counter[str]] = defaultdict(Counter)
        for treatment in group:
            baseline = indexed.get(
                (str(treatment.inventory.case_id), int(repeat_id), position, 1)
            )
            base_reason = _pair_reason(baseline, treatment)
            for metric in (
                "trace_replay_paired_speedup", "trace_replay_parallel_efficiency",
                "paired_trace_token_multiplier", "paired_trace_cost_multiplier",
            ):
                reason = base_reason
                value: float | None = None
                if reason is None:
                    assert baseline is not None and baseline.result is not None
                    assert treatment.result is not None
                    if metric in {
                        "trace_replay_paired_speedup", "trace_replay_parallel_efficiency"
                    }:
                        reason = _pair_reason(baseline, treatment, require_correct=True)
                        base_time = baseline.result.runtime_wall_clock_ms
                        treated_time = treatment.result.runtime_wall_clock_ms
                        if reason is None and (
                            not _finite(base_time) or not _finite(treated_time)
                            or float(base_time) <= 0 or float(treated_time) <= 0
                        ):
                            reason = "missing_or_nonpositive_pair_runtime"
                        if reason is None:
                            value = float(base_time) / float(treated_time)
                            if metric == "trace_replay_parallel_efficiency":
                                value /= int(worker_count)
                    else:
                        field = (
                            "source_total_tokens" if metric == "paired_trace_token_multiplier"
                            else "source_cost_estimate_cny"
                        )
                        baseline_value = _source_sum(baseline.result, field)
                        treated_value = _source_sum(treatment.result, field)
                        if (
                            not _finite(baseline_value) or not _finite(treated_value)
                            or float(baseline_value) <= 0
                        ):
                            reason = f"missing_or_nonpositive_{field}"
                        else:
                            value = float(treated_value) / float(baseline_value)
                if reason is not None:
                    reason_counts[metric][reason] += 1
                else:
                    metric_values[metric].append(
                        {
                            "case_id": str(treatment.inventory.case_id),
                            "domain": treatment.inventory.domain,
                            "difficulty": treatment.inventory.difficulty,
                            "topic_family": treatment.inventory.topic_family,
                            "position_stratum": position,
                            "value": value,
                        }
                    )
        for metric in (
            "trace_replay_paired_speedup", "trace_replay_parallel_efficiency",
            "paired_trace_token_multiplier", "paired_trace_cost_multiplier",
        ):
            _pair_fields(
                row, metric, len(group), metric_values[metric], reason_counts[metric]
            )
        row["paired_speedup_planned_pair_count"] = len(group)
        row["paired_speedup_eligible_pair_count"] = len(
            metric_values["trace_replay_paired_speedup"]
        )
        row["paired_speedup_ineligible_pair_count"] = len(group) - row[
            "paired_speedup_eligible_pair_count"
        ]
        row["paired_speedup_ineligible_reason_counts"] = dict(
            sorted(reason_counts["trace_replay_paired_speedup"].items())
        )
        row["trace_replay_paired_speedup_median"] = row[
            "trace_replay_paired_speedup"
        ]
        if row["trace_replay_paired_speedup_median"] is None:
            _set_missing(
                row,
                "trace_replay_paired_speedup_median",
                "no_eligible_matched_observations",
            )
        pair_rows.append(_apply_template(row, _EXP2_METRICS))

    repeat_groups: dict[tuple[Any, Any], dict[int, dict[str, Any]]] = defaultdict(dict)
    for row in pair_rows:
        key = (row["slice"]["position_stratum"], row["slice"]["worker_count"])
        repeat_groups[key][int(row["slice"]["repeat_id"])] = row
    for same_repeats in repeat_groups.values():
        values = [
            same_repeats.get(index, {}).get("trace_replay_paired_speedup")
            for index in (0, 1)
        ]
        complete = all(_finite(value) for value in values)
        for row in same_repeats.values():
            if not complete:
                for name in (
                    "trace_replay_paired_speedup_repeat_min",
                    "trace_replay_paired_speedup_repeat_max",
                    "trace_replay_paired_speedup_relative_difference",
                ):
                    _set_missing(row, name, "missing_repeat_pair_summary")
                continue
            numeric = [float(value) for value in values]
            _set_value(
                row, "trace_replay_paired_speedup_repeat_min", min(numeric)
            )
            _set_value(
                row, "trace_replay_paired_speedup_repeat_max", max(numeric)
            )
            _set_ratio(
                row,
                "trace_replay_paired_speedup_relative_difference",
                max(numeric) - min(numeric),
                sum(numeric) / 2,
            )
    rows.extend(pair_rows)
    return rows


def _exp3_cell_metrics(row: dict[str, Any], group: Sequence[_Observation]) -> None:
    _common(row, group)
    if any(item.result is None for item in group):
        for name in _EXP3_METRICS[len(_COMMON_METRICS):]:
            _set_missing(row, name, "missing_committed_root_result")
        return
    results = [item.result for item in group if item.result is not None]
    infrastructure_invalid_present = any(
        _is_infrastructure_invalid(result) for result in results
    )
    faults = [observation for result in results for observation in result.fault_observations]
    recoveries = [
        observation for result in results for observation in result.recovery_observations
    ]
    deaths = [
        (result, observation)
        for result in results for observation in result.worker_death_observations
    ]
    wrong = [
        item for item in faults
        if item.injected is True and item.independently_wrong is True
        and item.reached_verification is True
    ]
    row["injected_fault_target_count"] = sum(item.injected is True for item in faults)
    row["controlled_wrong_candidate_count"] = len(wrong)
    row["controlled_wrong_candidate_interception_count"] = sum(
        item.verifier_intercepted is True for item in wrong
    )
    if not infrastructure_invalid_present:
        _set_ratio(
            row,
            "controlled_wrong_candidate_interception_rate",
            row["controlled_wrong_candidate_interception_count"],
            len(wrong),
        )
    row["controlled_wrong_candidate_escape_count"] = sum(
        item.escaped_to_canonical_or_root is True for item in wrong
    )
    if not infrastructure_invalid_present:
        _set_ratio(
            row,
            "controlled_wrong_candidate_escape_rate",
            row["controlled_wrong_candidate_escape_count"],
            len(wrong),
        )
    row["started_replacement_attempt_count"] = sum(
        item.replacement_started is True for item in recoveries
    )
    row["successful_replacement_attempt_count"] = sum(
        item.replacement_succeeded is True for item in recoveries
    )
    if not infrastructure_invalid_present:
        _set_ratio(
            row,
            "replacement_attempt_success_rate",
            row["successful_replacement_attempt_count"],
            row["started_replacement_attempt_count"],
        )
    row["reassignment_count"] = sum(item.reassigned is True for item in recoveries)
    per_root_discarded: list[dict[str, Any]] = []
    for result in results:
        values = [item.discarded_total_tokens for item in result.fault_observations]
        death_ids = {
            item.original_attempt_id for item in result.worker_death_observations
            if item.original_attempt_id is not None
        }
        values.extend(
            attempt.simulated_total_tokens
            for attempt in result.attempts
            if attempt.attempt_id in death_ids and attempt.canonical_accepted is not True
        )
        per_root_discarded.append(
            {
                "case_id": str(result.case_id), "domain": result.domain,
                "difficulty": result.difficulty, "topic_family": result.topic_family,
                "position_stratum": result.position_stratum,
                "value": _sum_nullable(values),
            }
        )
    discarded_total = _sum_nullable(
        item["value"] for item in per_root_discarded
    )
    if discarded_total is None:
        _set_missing(row, "discarded_simulated_trace_tokens", "usage_missing")
    else:
        row["discarded_simulated_trace_tokens"] = discarded_total
    _add_distribution(
        row, "discarded_simulated_trace_tokens_per_root", per_root_discarded
    )
    errors = [
        100 * (
            float(observation.actual_progress_ratio)
            - float(observation.target_progress_ratio)
        )
        for _result, observation in deaths
    ]
    row["kill_progress_error_signed_mean_pp"] = _mean(errors)
    row["kill_progress_error_signed_max_pp"] = max(errors) if errors else None
    slot_inputs_complete = all(
        _finite(result.recovered_valid_canonical_slot_count)
        and _finite(result.required_slot_count)
        for result in results
    )
    if slot_inputs_complete:
        row["recovered_valid_canonical_required_slots"] = sum(
            int(result.recovered_valid_canonical_slot_count) for result in results
        )
        row["preregistered_required_slots"] = sum(
            int(result.required_slot_count) for result in results
        )
        if not infrastructure_invalid_present:
            _set_ratio(
                row,
                "result_completeness_rate",
                row["recovered_valid_canonical_required_slots"],
                row["preregistered_required_slots"],
            )
    else:
        for metric in (
            "recovered_valid_canonical_required_slots",
            "preregistered_required_slots",
            "result_completeness_rate",
        ):
            _set_missing(row, metric, "missing_required_slot_input")
        for suffix in (
            "ci95_low",
            "ci95_high",
            "bootstrap_variance",
            "bootstrap_standard_error",
        ):
            row[f"result_completeness_rate_{suffix}"] = None
        row["metric_metadata"]["result_completeness_rate"] = {
            **_TABLE_METADATA,
            "case_cluster_count": len({str(result.case_id) for result in results}),
            "valid_bootstrap_replicate_count": 0,
            "missing_reason": "missing_required_slot_input",
        }
    row["unrecovered_root_count"] = sum(
        result.verified_correct is not True for result in results
    )
    is_rate_fault = group[0].inventory.fault_type is not None
    is_false_positive = group[0].inventory.fault_type == "false_positive"
    if not is_rate_fault:
        _set_not_applicable(
            row, "injected_fault_target_count", "not_a_rate_fault_condition"
        )
    if not is_false_positive:
        for metric in (
            "controlled_wrong_candidate_count",
            "controlled_wrong_candidate_interception_count",
            "controlled_wrong_candidate_interception_rate",
            "controlled_wrong_candidate_escape_count",
            "controlled_wrong_candidate_escape_rate",
        ):
            _set_not_applicable(
                row, metric, "not_a_false_positive_fault_condition"
            )
    is_worker_death = group[0].inventory.dead_worker_count is not None
    if not errors:
        reason = (
            "missing_worker_death_observation"
            if is_worker_death else "not_a_worker_death_condition"
        )
        setter = _set_missing if is_worker_death else _set_not_applicable
        setter(row, "kill_progress_error_signed_mean_pp", reason)
        setter(row, "kill_progress_error_signed_max_pp", reason)
    rate_specs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        root_wrong = [
            item for item in result.fault_observations
            if item.injected is True and item.independently_wrong is True
            and item.reached_verification is True
        ]
        root_recoveries = list(result.recovery_observations)
        identity = {
            "case_id": str(result.case_id), "domain": result.domain,
            "difficulty": result.difficulty, "topic_family": result.topic_family,
            "position_stratum": result.position_stratum,
        }
        rate_specs["controlled_wrong_candidate_interception_rate"].append(
            {**identity,
             "numerator": sum(item.verifier_intercepted is True for item in root_wrong),
             "denominator": len(root_wrong)}
        )
        rate_specs["controlled_wrong_candidate_escape_rate"].append(
            {**identity,
             "numerator": sum(item.escaped_to_canonical_or_root is True for item in root_wrong),
             "denominator": len(root_wrong)}
        )
        rate_specs["replacement_attempt_success_rate"].append(
            {**identity,
             "numerator": sum(item.replacement_succeeded is True for item in root_recoveries),
             "denominator": sum(item.replacement_started is True for item in root_recoveries)}
        )
        if slot_inputs_complete:
            rate_specs["result_completeness_rate"].append(
                {
                    **identity,
                    "numerator": result.recovered_valid_canonical_slot_count,
                    "denominator": result.required_slot_count,
                }
            )
    if infrastructure_invalid_present:
        for metric in (
            "controlled_wrong_candidate_interception_rate",
            "controlled_wrong_candidate_escape_rate",
            "replacement_attempt_success_rate",
            "result_completeness_rate",
        ):
            if metric not in row["not_applicable_reasons"]:
                _set_missing(
                    row, metric, "infrastructure_invalid_root_present"
                )
                metadata = row["metric_metadata"].get(metric)
                if isinstance(metadata, dict):
                    metadata["missing_reason"] = (
                        "infrastructure_invalid_root_present"
                    )
    for metric, raw in rate_specs.items():
        if (
            not infrastructure_invalid_present
            and metric not in row["not_applicable_reasons"]
        ):
            _add_rate_interval(row, metric, raw)
    if errors:
        error_rows = [
            {
                "case_id": str(result.case_id), "domain": result.domain,
                "difficulty": result.difficulty, "topic_family": result.topic_family,
                "position_stratum": result.position_stratum,
                "value": 100 * (
                    float(observation.actual_progress_ratio)
                    - float(observation.target_progress_ratio)
                ),
            }
            for result, observation in deaths
        ]
        variance = sample_variance([float(item["value"]) for item in error_rows])
        row["kill_progress_error_signed_mean_pp_sample_variance"] = variance
        row["kill_progress_error_signed_mean_pp_sample_stddev"] = (
            sqrt(variance) if variance is not None else None
        )
        interval = stratified_case_cluster_bootstrap(
            error_rows,
            lambda sample: _mean([float(item["value"]) for item in sample]),
        )
        for suffix in (
            "ci95_low", "ci95_high", "bootstrap_variance",
            "bootstrap_standard_error",
        ):
            row[f"kill_progress_error_signed_mean_pp_{suffix}"] = interval[suffix]
        row["metric_metadata"]["kill_progress_error_signed_mean_pp"] = interval


def _reduce_exp3(
    observations: Sequence[_Observation],
    references: Sequence[_Observation],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    groups = _group(
        observations,
        lambda item: (
            item.inventory.fault_type, item.inventory.fault_rate,
            item.inventory.dead_worker_count, item.inventory.kill_progress_target_ratio,
            item.inventory.domain, item.inventory.difficulty,
            item.inventory.topic_family, item.inventory.repeat_id,
        ),
    )
    reference_index = {
        (str(item.inventory.case_id), int(item.inventory.repeat_id)): item
        for item in references
    }
    for key, group in groups:
        slice_values = dict(
            fault_type=key[0], fault_rate=key[1], dead_worker_count=key[2],
            kill_progress_target_ratio=key[3], domain=key[4], difficulty=key[5],
            topic_family=key[6], repeat_id=key[7],
        )
        cell = _base_row("exp3", "cell", slice_values)
        cell["resource_semantics"] = "simulated_trace_attributed"
        _exp3_cell_metrics(cell, group)
        rows.append(_apply_template(cell, _EXP3_METRICS))

        pair = _base_row(
            "exp3",
            "fault_reference_pair" if key[0] is not None else "death_reference_pair",
            slice_values,
        )
        pair["resource_semantics"] = "simulated_trace_attributed"
        _common(pair, group)
        values_by_metric: dict[str, list[dict[str, Any]]] = defaultdict(list)
        reasons_by_metric: dict[str, Counter[str]] = defaultdict(Counter)
        extractors: dict[str, Callable[[RootResultV2], float | int | None]] = {
            "simulated_wall_clock_overhead_ms": lambda result: result.runtime_wall_clock_ms,
            "simulated_token_overhead": _simulated_tokens,
            "simulated_trace_attributed_cost_overhead": _simulated_cost,
        }
        infrastructure_invalid_present = any(
            item.result is not None
            and _is_infrastructure_invalid(item.result)
            for item in group
        )
        for treatment in group:
            reference = reference_index.get(
                (str(treatment.inventory.case_id), int(treatment.inventory.repeat_id))
            )
            if (
                reference is not None
                and (
                    reference.result is None
                    or _is_infrastructure_invalid(reference.result)
                )
            ):
                infrastructure_invalid_present = True
            for metric, extractor in extractors.items():
                reason = _pair_reason(reference, treatment)
                value = None
                if reason is None:
                    assert reference is not None and reference.result is not None
                    assert treatment.result is not None
                    left = extractor(treatment.result)
                    right = extractor(reference.result)
                    if not _finite(left) or not _finite(right):
                        reason = f"missing_{metric}_pair_input"
                    else:
                        value = float(left) - float(right)
                if reason is None:
                    values_by_metric[metric].append(
                        {
                            "case_id": str(treatment.inventory.case_id),
                            "domain": treatment.inventory.domain,
                            "difficulty": treatment.inventory.difficulty,
                            "topic_family": treatment.inventory.topic_family,
                            "position_stratum": treatment.inventory.position_stratum,
                            "value": value,
                        }
                    )
                else:
                    reasons_by_metric[metric][reason] += 1
        for metric in extractors:
            _pair_fields(
                pair, metric, len(group), values_by_metric[metric],
                reasons_by_metric[metric],
                missing_reason=(
                    "infrastructure_invalid_root_present"
                    if infrastructure_invalid_present else None
                ),
            )
        rows.append(_apply_template(pair, _EXP3_METRICS))
    return rows


_MECHANISM_MODE = {
    "verification": "NO_VERIFICATION",
    "parser_policy": "NO_PARSER_POLICY",
    "requeue": "NO_REQUEUE",
    "merge_gate": "NO_MERGE_GATE",
}
_MECHANISM_PAIRS = (
    ("verification", "parser_policy"), ("verification", "requeue"),
    ("verification", "merge_gate"), ("parser_policy", "requeue"),
    ("parser_policy", "merge_gate"), ("requeue", "merge_gate"),
)


def _pair_mode(first: str, second: str) -> str:
    names = (_MECHANISM_MODE[first], _MECHANISM_MODE[second])
    canonical_order = tuple(_MECHANISM_MODE.values())
    ordered = sorted(names, key=canonical_order.index)
    return "__".join(ordered)


def _plan_signature(result: RootResultV2) -> tuple[Any, ...]:
    return (
        result.challenge_plan_id, result.challenge_family,
        tuple(result.challenge_target_planned_ai_unit_ids or ()),
        result.challenge_attempt_ordinal_rule,
    )


def _exp4_family(observation: _Observation) -> str | None:
    return (
        observation.planned_challenge_family
        if observation.planned_challenge_family is not None
        else observation.result.challenge_family
        if observation.result is not None
        else None
    )


def _exp4_runtime_reason(results: Sequence[RootResultV2 | None]) -> str | None:
    if any(result is None for result in results):
        return "missing_committed_root_result"
    committed = [result for result in results if result is not None]
    if any(_is_infrastructure_invalid(result) for result in committed):
        return "infrastructure_invalid_root_present"
    if any(result.protocol_started is not True for result in committed):
        return "preflight_blocked_invalid_ablation_path"
    signatures = {_plan_signature(result) for result in committed}
    if len(signatures) != 1:
        return "challenge_plan_mismatch"
    if any(
        any(item.opportunity is True and item.injected is not True
            for item in result.challenge_observations)
        for result in committed
    ):
        return "missed_challenge_opportunity"
    return None


def _exp4_special_metrics(row: dict[str, Any], results: Sequence[RootResultV2]) -> None:
    challenges = [item for result in results for item in result.challenge_observations]
    ablations = [item for result in results for item in result.ablation_observations]
    family = row["slice"].get("challenge_family")
    disabled = set(row["slice"].get("disabled_mechanisms") or ())
    invalid = [
        item for item in challenges
        if item.injected is True and item.candidate_independent_label == "invalid"
    ]
    row["independently_labeled_invalid_candidate_count"] = len(invalid)
    row["invalid_candidate_verifier_rejection_count"] = sum(
        item.verifier_rejected is True for item in invalid
    )
    row["wrong_canonical_acceptance_count"] = sum(
        item.wrong_canonical_accepted is True for item in ablations
    )
    _set_ratio(
        row,
        "wrong_canonical_acceptance_rate",
        row["wrong_canonical_acceptance_count"],
        len(invalid),
    )
    row["root_checker_rejection_after_wrong_canonical_count"] = sum(
        any(
            item.root_checker_rejected_after_wrong_canonical is True
            for item in result.ablation_observations
        )
        for result in results
    )
    row["parser_required_input_count"] = sum(
        item.injected is True and item.challenge_family == "PARSER_REQUIRED_CANONICAL_JSON"
        for item in challenges
    )
    row["raw_only_exposure_count"] = sum(item.raw_only_exposed is True for item in ablations)
    row["raw_only_acceptance_count"] = sum(item.raw_only_accepted is True for item in ablations)
    _set_ratio(
        row,
        "raw_only_acceptance_rate",
        row["raw_only_acceptance_count"],
        row["raw_only_exposure_count"],
    )
    injected_roots = sum(any(item.injected is True for item in result.challenge_observations) for result in results)
    no_return_roots = sum(
        any(
            item.injected is True and item.challenge_family == "RECOVERABLE_NO_RETURN"
            for item in result.challenge_observations
        )
        for result in results
    )
    row["recoverable_no_return_count"] = no_return_roots
    row["replacement_started_after_challenge_count"] = sum(
        any(item.replacement_started is True for item in result.challenge_observations)
        for result in results
    )
    row["valid_final_after_challenge_count"] = sum(
        result.verified_correct is True
        and any(item.injected is True for item in result.challenge_observations)
        for result in results
    )
    _set_ratio(
        row,
        "valid_final_after_challenge_rate",
        row["valid_final_after_challenge_count"],
        injected_roots,
    )
    row["stuck_task_count"] = sum(
        any(item.stuck_due_to_no_requeue is True for item in result.ablation_observations)
        for result in results
    )
    _set_ratio(row, "stuck_task_rate", row["stuck_task_count"], no_return_roots)
    row["merge_gate_unsatisfied_observation_count"] = sum(
        item.merge_gate_satisfied is False and bool(item.missing_required_slot_ids)
        for item in ablations
    )
    row["premature_merge_attempt_count"] = sum(
        item.premature_merge_attempted is True for item in ablations
    )
    row["premature_merge_failure_count"] = sum(
        item.premature_merge_failed is True for item in ablations
    )
    _set_ratio(
        row,
        "premature_merge_failure_rate",
        row["premature_merge_failure_count"],
        row["premature_merge_attempt_count"],
    )
    applicability = {
        "invalid": family == "INVALID_PARSED_CANDIDATE",
        "invalid_verifier": (
            family == "INVALID_PARSED_CANDIDATE" and "verification" not in disabled
        ),
        "parser": family == "PARSER_REQUIRED_CANONICAL_JSON",
        "raw": family == "PARSER_REQUIRED_CANONICAL_JSON" and "parser_policy" in disabled,
        "no_return": family == "RECOVERABLE_NO_RETURN",
        "replacement": family == "RECOVERABLE_NO_RETURN" and "requeue" not in disabled,
        "stuck": family == "RECOVERABLE_NO_RETURN" and "requeue" in disabled,
        "merge": family == "REQUIRED_CHILD_DELAY",
        "premature": family == "REQUIRED_CHILD_DELAY" and "merge_gate" in disabled,
    }
    metric_groups = {
        "invalid": (
            "independently_labeled_invalid_candidate_count",
            "wrong_canonical_acceptance_count", "wrong_canonical_acceptance_rate",
            "root_checker_rejection_after_wrong_canonical_count",
        ),
        "invalid_verifier": ("invalid_candidate_verifier_rejection_count",),
        "parser": ("parser_required_input_count",),
        "raw": (
            "raw_only_exposure_count", "raw_only_acceptance_count",
            "raw_only_acceptance_rate",
        ),
        "no_return": (
            "recoverable_no_return_count",
        ),
        "replacement": ("replacement_started_after_challenge_count",),
        "stuck": ("stuck_task_count", "stuck_task_rate"),
        "merge": ("merge_gate_unsatisfied_observation_count",),
        "premature": (
            "premature_merge_attempt_count", "premature_merge_failure_count",
            "premature_merge_failure_rate",
        ),
    }
    for group_name, metrics in metric_groups.items():
        if not applicability[group_name]:
            for metric in metrics:
                _set_not_applicable(row, metric)


def _exp4_cell(
    key: tuple[Any, ...],
    group: Sequence[_Observation],
    mismatch_keys: set[tuple[str, int]],
) -> dict[str, Any]:
    mode, family, domain, repeat_id = key
    disabled = list(group[0].inventory.disabled_mechanisms)
    row = _base_row(
        "exp4", "cell",
        dict(
            mode=mode, challenge_family=family, domain=domain, repeat_id=repeat_id,
            disabled_mechanisms=disabled,
        ),
    )
    _common(row, group)
    row["challenge_planned_root_count"] = sum(
        item.inventory.challenge_plan_id is not None for item in group
    )
    if any(item.result is None for item in group):
        for name in _EXP4_METRICS[len(_COMMON_METRICS):]:
            if name != "challenge_planned_root_count":
                _set_missing(row, name, "missing_committed_root_result")
        row["scientifically_valid_ablation_cell"] = False
        return _apply_template(row, _EXP4_METRICS)
    results = [item.result for item in group if item.result is not None]
    started = sum(result.protocol_started is True for result in results)
    row["protocol_started_root_count"] = started
    row["preflight_blocked_root_count"] = len(group) - started
    _set_ratio(row, "protocol_start_coverage", started, len(group))
    challenges = [item for result in results for item in result.challenge_observations]
    opportunities = sum(item.opportunity is True for item in challenges)
    injections = sum(item.injected is True for item in challenges)
    row["challenge_target_opportunity_count"] = opportunities
    row["challenge_injection_count"] = injections
    row["missed_challenge_opportunity_count"] = opportunities - injections
    row["challenge_applied_root_count"] = sum(
        any(item.injected is True for item in result.challenge_observations)
        for result in results
    )
    _set_ratio(
        row, "challenge_application_coverage", injections, opportunities
    )
    row["challenge_plan_mismatch_count"] = sum(
        (str(item.inventory.case_id), int(item.inventory.repeat_id)) in mismatch_keys
        for item in group
    )
    _set_ratio(
        row, "no_final_rate", row["no_final_failure_count"], len(group)
    )
    _set_ratio(
        row,
        "incorrect_final_rate",
        row["incorrect_final_failure_count"],
        len(group),
    )
    slot_inputs_complete = all(
        _finite(result.recovered_valid_canonical_slot_count)
        and _finite(result.required_slot_count)
        for result in results
    )
    recovered = (
        sum(int(result.recovered_valid_canonical_slot_count) for result in results)
        if slot_inputs_complete else None
    )
    required = (
        sum(int(result.required_slot_count) for result in results)
        if slot_inputs_complete else None
    )
    if not slot_inputs_complete:
        _set_missing(
            row, "required_slot_completion_rate", "missing_required_slot_input"
        )
    else:
        assert recovered is not None and required is not None
        _set_ratio(row, "required_slot_completion_rate", recovered, required)
    row["actual_execution_attempt_count"] = sum(len(result.attempts) for result in results)
    row["source_response_slot_consumption"] = sum(
        _source_consumption(result) for result in results
    )
    valid = (
        row["infrastructure_invalid_root_count"] == 0
        and
        row["preflight_blocked_root_count"] == 0
        and row["challenge_plan_mismatch_count"] == 0
        and row["missed_challenge_opportunity_count"] == 0
    )
    row["scientifically_valid_ablation_cell"] = valid
    if not valid:
        reason = (
            "infrastructure_invalid_root_present"
            if row["infrastructure_invalid_root_count"] else
            "preflight_blocked_invalid_ablation_path"
            if row["preflight_blocked_root_count"] else
            "challenge_plan_mismatch" if row["challenge_plan_mismatch_count"] else
            "missed_challenge_opportunity"
        )
        for metric in (
            "no_final_rate", "incorrect_final_rate", "required_slot_completion_rate"
        ):
            _set_missing(row, metric, reason)
    _exp4_special_metrics(row, results)
    if not valid:
        for metric in (
            "wrong_canonical_acceptance_rate", "raw_only_acceptance_rate",
            "valid_final_after_challenge_rate", "stuck_task_rate",
            "premature_merge_failure_rate",
        ):
            if metric not in row["not_applicable_reasons"]:
                _set_missing(row, metric, reason)
    if valid:
        raw_rates: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for result in results:
            identity = {
                "case_id": str(result.case_id), "domain": result.domain,
                "difficulty": result.difficulty, "topic_family": result.topic_family,
                "position_stratum": result.position_stratum,
            }
            raw_rates["no_final_rate"].append(
                {**identity, "numerator": int(result.failure_kind == "no_final"),
                 "denominator": 1}
            )
            raw_rates["incorrect_final_rate"].append(
                {**identity, "numerator": int(result.failure_kind == "incorrect_final"),
                 "denominator": 1}
            )
            if slot_inputs_complete and required != 0:
                raw_rates["required_slot_completion_rate"].append(
                    {
                        **identity,
                        "numerator": result.recovered_valid_canonical_slot_count,
                        "denominator": result.required_slot_count,
                    }
                )
            challenges_for_root = list(result.challenge_observations)
            ablations_for_root = list(result.ablation_observations)
            invalid_for_root = [
                item for item in challenges_for_root
                if item.injected is True and item.candidate_independent_label == "invalid"
            ]
            raw_rates["wrong_canonical_acceptance_rate"].append(
                {**identity,
                 "numerator": sum(
                     item.wrong_canonical_accepted is True
                     for item in ablations_for_root
                 ),
                 "denominator": len(invalid_for_root)}
            )
            raw_rates["raw_only_acceptance_rate"].append(
                {**identity,
                 "numerator": sum(item.raw_only_accepted is True for item in ablations_for_root),
                 "denominator": sum(item.raw_only_exposed is True for item in ablations_for_root)}
            )
            injected = any(item.injected is True for item in challenges_for_root)
            raw_rates["valid_final_after_challenge_rate"].append(
                {**identity,
                 "numerator": int(injected and result.verified_correct is True),
                 "denominator": int(injected)}
            )
            no_return = any(
                item.injected is True and item.challenge_family == "RECOVERABLE_NO_RETURN"
                for item in challenges_for_root
            )
            raw_rates["stuck_task_rate"].append(
                {**identity,
                 "numerator": int(any(item.stuck_due_to_no_requeue is True for item in ablations_for_root)),
                 "denominator": int(no_return)}
            )
            raw_rates["premature_merge_failure_rate"].append(
                {**identity,
                 "numerator": sum(item.premature_merge_failed is True for item in ablations_for_root),
                 "denominator": sum(item.premature_merge_attempted is True for item in ablations_for_root)}
            )
        for metric, raw in raw_rates.items():
            if metric not in row["not_applicable_reasons"]:
                _add_rate_interval(row, metric, raw)
    return _apply_template(row, _EXP4_METRICS)


def _exp4_pair_value(
    metric: str,
    full: RootResultV2,
    ablation: RootResultV2,
) -> tuple[float | None, str | None]:
    if metric == "paired_end_to_end_success_loss_vs_full":
        return float(bool(full.verified_correct)) - float(bool(ablation.verified_correct)), None
    if metric == "paired_completion_loss_vs_full":
        return float(bool(full.final_result_present)) - float(bool(ablation.final_result_present)), None
    if metric == "paired_required_slot_completion_loss_vs_full":
        full_ratio = _ratio(
            full.recovered_valid_canonical_slot_count, full.required_slot_count
        )
        ablation_ratio = _ratio(
            ablation.recovered_valid_canonical_slot_count, ablation.required_slot_count
        )
        if full_ratio is None or ablation_ratio is None:
            return None, "missing_required_slot_pair_input"
        return full_ratio - ablation_ratio, None
    if metric == "trace_replay_wall_clock_delta_vs_full":
        if not _finite(full.runtime_wall_clock_ms) or not _finite(ablation.runtime_wall_clock_ms):
            return None, "missing_pair_runtime"
        return float(ablation.runtime_wall_clock_ms) - float(full.runtime_wall_clock_ms), None
    if metric == "execution_attempt_delta_vs_full":
        return float(len(ablation.attempts) - len(full.attempts)), None
    if metric == "source_slot_consumption_delta_vs_full":
        return float(_source_consumption(ablation) - _source_consumption(full)), None
    field = (
        "source_total_tokens"
        if metric == "trace_attributed_token_delta_vs_full"
        else "source_cost_estimate_cny"
    )
    full_value = _source_sum(full, field)
    ablation_value = _source_sum(ablation, field)
    if not _finite(full_value) or not _finite(ablation_value):
        return None, f"missing_{field}_pair_input"
    return float(ablation_value) - float(full_value), None


def _reduce_exp4_pairs(
    observations: Sequence[_Observation],
) -> list[dict[str, Any]]:
    indexed = {
        (str(item.inventory.case_id), int(item.inventory.repeat_id), item.inventory.mode): item
        for item in observations
    }
    groups = _group(
        (item for item in observations if item.inventory.mode != "FULL"),
        lambda item: (
            item.inventory.mode,
            _exp4_family(item),
            item.inventory.domain,
            item.inventory.repeat_id,
        ),
    )
    metrics = (
        "paired_end_to_end_success_loss_vs_full", "paired_completion_loss_vs_full",
        "paired_required_slot_completion_loss_vs_full",
        "trace_replay_wall_clock_delta_vs_full", "execution_attempt_delta_vs_full",
        "source_slot_consumption_delta_vs_full", "trace_attributed_token_delta_vs_full",
        "trace_attributed_cost_delta_vs_full",
    )
    rows: list[dict[str, Any]] = []
    for key, group in groups:
        mode, family, domain, repeat_id = key
        row = _base_row(
            "exp4", "pair",
            dict(
                mode=mode, challenge_family=family, domain=domain,
                repeat_id=repeat_id,
            ),
        )
        planned = len(group)
        runtime_valid: list[tuple[_Observation, _Observation]] = []
        runtime_reasons: Counter[str] = Counter()
        for ablation in group:
            full = indexed.get(
                (str(ablation.inventory.case_id), int(ablation.inventory.repeat_id), "FULL")
            )
            reason = _exp4_runtime_reason(
                [full.result if full is not None else None, ablation.result]
            )
            if reason is None:
                assert full is not None
                runtime_valid.append((full, ablation))
            else:
                runtime_reasons[reason] += 1
        row["planned_pair_count"] = planned
        row["runtime_valid_pair_count"] = len(runtime_valid)
        row["runtime_invalid_pair_count"] = planned - len(runtime_valid)
        row["runtime_invalid_reason_counts"] = dict(sorted(runtime_reasons.items()))
        transitions = Counter()
        for full, ablation in runtime_valid:
            assert full.result is not None and ablation.result is not None
            transitions[(bool(full.result.verified_correct), bool(ablation.result.verified_correct))] += 1
        row["full_success_ablation_success"] = transitions[(True, True)]
        row["full_success_ablation_failure"] = transitions[(True, False)]
        row["full_failure_ablation_success"] = transitions[(False, True)]
        row["full_failure_ablation_failure"] = transitions[(False, False)]
        _set_ratio(
            row,
            "ablation_failure_given_full_success_rate",
            transitions[(True, False)],
            transitions[(True, True)] + transitions[(True, False)],
        )
        conditional_rows = [
            {
                "case_id": str(ablation.inventory.case_id),
                "domain": ablation.inventory.domain,
                "difficulty": ablation.inventory.difficulty,
                "topic_family": ablation.inventory.topic_family,
                "position_stratum": ablation.inventory.position_stratum,
                "numerator": int(
                    full.result is not None
                    and full.result.verified_correct is True
                    and ablation.result is not None
                    and ablation.result.verified_correct is not True
                ),
                "denominator": int(
                    full.result is not None and full.result.verified_correct is True
                ),
            }
            for full, ablation in runtime_valid
        ]
        _add_rate_interval(
            row, "ablation_failure_given_full_success_rate", conditional_rows
        )
        values_by_metric: dict[str, list[dict[str, Any]]] = defaultdict(list)
        reasons_by_metric: dict[str, Counter[str]] = defaultdict(Counter)
        for full, ablation in runtime_valid:
            assert full.result is not None and ablation.result is not None
            for metric in metrics:
                value, reason = _exp4_pair_value(metric, full.result, ablation.result)
                if reason is None:
                    values_by_metric[metric].append(
                        {
                            "case_id": str(ablation.inventory.case_id),
                            "domain": ablation.inventory.domain,
                            "difficulty": ablation.inventory.difficulty,
                            "topic_family": ablation.inventory.topic_family,
                            "position_stratum": ablation.inventory.position_stratum,
                            "value": value,
                        }
                    )
                else:
                    reasons_by_metric[metric][reason] += 1
        for metric in metrics:
            use_mean = metric in {
                "paired_end_to_end_success_loss_vs_full",
                "paired_completion_loss_vs_full",
                "paired_required_slot_completion_loss_vs_full",
            }
            _pair_fields(
                row, metric, planned, values_by_metric[metric],
                reasons_by_metric[metric] + runtime_reasons, use_mean=use_mean,
            )
        rows.append(_apply_template(row, _EXP4_METRICS))
    return rows


def _quadruple_values(
    arms: Mapping[str, RootResultV2], first: str, second: str,
) -> tuple[dict[str, float | None], dict[str, str]]:
    full = arms["FULL"]
    one = arms[_MECHANISM_MODE[first]]
    two = arms[_MECHANISM_MODE[second]]
    pair = arms[_pair_mode(first, second)]
    values: dict[str, float | None] = {
        "single_removal_success_loss_i": float(bool(full.verified_correct)) - float(bool(one.verified_correct)),
        "pair_removal_success_loss_ij": float(bool(full.verified_correct)) - float(bool(pair.verified_correct)),
        "pair_interaction_success_penalty_ij": (
            float(bool(one.verified_correct)) + float(bool(two.verified_correct))
            - float(bool(full.verified_correct)) - float(bool(pair.verified_correct))
        ),
        "pair_interaction_completion_penalty_ij": (
            float(bool(one.final_result_present)) + float(bool(two.final_result_present))
            - float(bool(full.final_result_present)) - float(bool(pair.final_result_present))
        ),
        "pair_interaction_required_slot_penalty_ij": None,
    }
    reasons: dict[str, str] = {}
    ratios = {
        name: _ratio(
            result.recovered_valid_canonical_slot_count, result.required_slot_count
        )
        for name, result in arms.items()
    }
    if all(ratios[name] is not None for name in arms):
        values["pair_interaction_required_slot_penalty_ij"] = (
            float(ratios[_MECHANISM_MODE[first]])
            + float(ratios[_MECHANISM_MODE[second]])
            - float(ratios["FULL"])
            - float(ratios[_pair_mode(first, second)])
        )
    else:
        reasons["pair_interaction_required_slot_penalty_ij"] = (
            "missing_required_slot_quadruple_input"
        )
    return values, reasons


def _reduce_exp4_interactions(
    observations: Sequence[_Observation],
) -> list[dict[str, Any]]:
    indexed = {
        (str(item.inventory.case_id), int(item.inventory.repeat_id), item.inventory.mode): item
        for item in observations
    }
    rows: list[dict[str, Any]] = []
    for first, second in _MECHANISM_PAIRS:
        pair_mode = _pair_mode(first, second)
        pair_roots = [item for item in observations if item.inventory.mode == pair_mode]
        groups = _group(
            pair_roots,
            lambda item: (
                _exp4_family(item),
                item.inventory.domain, item.inventory.repeat_id,
            ),
        )
        for key, group in groups:
            row = _base_row(
                "exp4", "interaction",
                dict(
                    mechanism_i=first, mechanism_j=second, challenge_family=key[0],
                    domain=key[1], repeat_id=key[2],
                ),
            )
            metric_names = (
                "single_removal_success_loss_i", "pair_removal_success_loss_ij",
                "pair_interaction_success_penalty_ij",
                "pair_interaction_completion_penalty_ij",
                "pair_interaction_required_slot_penalty_ij",
            )
            values: dict[str, list[dict[str, Any]]] = defaultdict(list)
            reasons: dict[str, Counter[str]] = defaultdict(Counter)
            eligible_base = 0
            for pair_observation in group:
                case = str(pair_observation.inventory.case_id)
                repeat_id = int(pair_observation.inventory.repeat_id)
                required_modes = (
                    "FULL", _MECHANISM_MODE[first], _MECHANISM_MODE[second], pair_mode
                )
                observations_by_mode = {
                    mode: indexed.get((case, repeat_id, mode)) for mode in required_modes
                }
                reason = _exp4_runtime_reason(
                    [item.result if item is not None else None
                     for item in observations_by_mode.values()]
                )
                if reason is not None:
                    for metric in metric_names:
                        reasons[metric][reason] += 1
                    continue
                arms = {
                    mode: item.result
                    for mode, item in observations_by_mode.items()
                    if item is not None and item.result is not None
                }
                quad_values, quad_reasons = _quadruple_values(arms, first, second)
                eligible_base += 1
                for metric in metric_names:
                    metric_reason = quad_reasons.get(metric)
                    if metric_reason is not None:
                        reasons[metric][metric_reason] += 1
                        continue
                    values[metric].append(
                        {
                            "case_id": case,
                            "domain": pair_observation.inventory.domain,
                            "difficulty": pair_observation.inventory.difficulty,
                            "topic_family": pair_observation.inventory.topic_family,
                            "position_stratum": pair_observation.inventory.position_stratum,
                            "value": quad_values[metric],
                        }
                    )
            row["planned_quadruple_count"] = len(group)
            row["eligible_quadruple_count"] = eligible_base
            row["ineligible_quadruple_count"] = len(group) - eligible_base
            row["ineligible_quadruple_reason_counts"] = dict(
                sorted(reasons["pair_interaction_success_penalty_ij"].items())
            )
            for metric in metric_names:
                _pair_fields(
                    row, metric, len(group), values[metric], reasons[metric], use_mean=True
                )
                row[f"{metric}_planned_quadruple_count"] = row.pop(
                    f"{metric}_planned_pair_count"
                )
                row[f"{metric}_eligible_quadruple_count"] = row.pop(
                    f"{metric}_eligible_pair_count"
                )
                row[f"{metric}_ineligible_quadruple_count"] = row.pop(
                    f"{metric}_ineligible_pair_count"
                )
                row[f"{metric}_ineligible_quadruple_reason_counts"] = row.pop(
                    f"{metric}_ineligible_reason_counts"
                )
            rows.append(_apply_template(row, _EXP4_METRICS))
    return rows


def _reduce_exp4(observations: Sequence[_Observation]) -> list[dict[str, Any]]:
    signatures: dict[tuple[str, int], set[tuple[Any, ...]]] = defaultdict(set)
    for item in observations:
        if item.result is not None:
            identity = (str(item.inventory.case_id), int(item.inventory.repeat_id))
            signatures[identity].add(_plan_signature(item.result))
            if (
                item.planned_challenge_family is not None
                and item.result.challenge_family != item.planned_challenge_family
            ):
                signatures[identity].add(("inventory_family", item.planned_challenge_family))
    mismatch_keys = {
        identity for identity, values in signatures.items() if len(values) != 1
    }
    cell_groups = _group(
        observations,
        lambda item: (
            item.inventory.mode,
            _exp4_family(item),
            item.inventory.domain,
            item.inventory.repeat_id,
        ),
    )
    rows = [_exp4_cell(key, group, mismatch_keys) for key, group in cell_groups]
    rows.extend(_reduce_exp4_pairs(observations))
    rows.extend(_reduce_exp4_interactions(observations))
    _add_repeat_descriptions(rows)
    return rows


def _add_repeat_descriptions(rows: Sequence[dict[str, Any]]) -> None:
    metrics = (
        "paired_end_to_end_success_loss_vs_full", "paired_completion_loss_vs_full",
        "paired_required_slot_completion_loss_vs_full",
        "trace_replay_wall_clock_delta_vs_full", "execution_attempt_delta_vs_full",
        "source_slot_consumption_delta_vs_full", "trace_attributed_token_delta_vs_full",
        "trace_attributed_cost_delta_vs_full", "single_removal_success_loss_i",
        "pair_removal_success_loss_ij", "pair_interaction_success_penalty_ij",
        "pair_interaction_completion_penalty_ij",
        "pair_interaction_required_slot_penalty_ij",
    )
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["row_kind"] not in {"pair", "interaction"}:
            continue
        stable_slice = tuple(
            sorted(
                (key, json.dumps(value, sort_keys=True))
                for key, value in row["slice"].items() if key != "repeat_id"
            )
        )
        grouped[(row["row_kind"], stable_slice)].append(row)
    for group in grouped.values():
        by_repeat = {int(row["slice"]["repeat_id"]): row for row in group}
        for metric in metrics:
            values = [by_repeat.get(index, {}).get(metric) for index in (0, 1, 2)]
            complete = all(_finite(value) for value in values)
            for row in group:
                for index, value in enumerate(values):
                    if _finite(value):
                        _set_value(row, f"{metric}_repeat{index}", value)
                    else:
                        _set_missing(
                            row, f"{metric}_repeat{index}", "missing_repeat_metric"
                        )
                if complete:
                    numeric = [float(value) for value in values]
                    _set_value(
                        row, f"{metric}_repeat_median", float(median(numeric))
                    )
                    _set_value(row, f"{metric}_repeat_min", min(numeric))
                    _set_value(row, f"{metric}_repeat_max", max(numeric))
                else:
                    for suffix in ("repeat_median", "repeat_min", "repeat_max"):
                        _set_missing(
                            row,
                            f"{metric}_{suffix}",
                            "missing_repeat_metric",
                        )


def _first_attempt_classification(
    result: RootResultV2,
) -> list[tuple[AttemptResultV1, bool, bool, str | None, bool]]:
    classified = []
    for attempt in result.attempts:
        if attempt.attempt_ordinal != 0 or attempt.provider_call_made is not True:
            continue
        transport = (
            not isinstance(attempt.http_status, int)
            or not 200 <= attempt.http_status < 300
            or attempt.raw_response_present is not True
            or attempt.result_kind in {
                "provider_failed", "transport_error", "executor_error",
                "timeout", "no_return",
            }
        )
        parse_usable = attempt.parse_result in {
            "accepted", "parsed", "usable", "passed", "success"
        }
        verifier_value = (
            attempt.verifier_result
            if attempt.verifier_result is not None else attempt.checker_result
        )
        parsed_unsubmitted = (
            not transport
            and parse_usable
            and attempt.result_kind == "parsed"
            and attempt.verifier_result is None
            and attempt.checker_result is None
            and attempt.missing_reason.get("attempts[].verifier_result")
            == "not_applicable_or_unavailable"
        )
        if (
            not transport
            and parse_usable
            and verifier_value is None
            and not parsed_unsubmitted
        ):
            raise ValueError(
                "missing first-attempt verification evidence: "
                f"{result.case_id}:{attempt.planned_ai_unit_id}"
            )
        checkable = not transport and parse_usable and verifier_value is not None
        passed = checkable and verifier_value in {"accepted", "passed", "success"}
        reason = (
            "transport" if transport else "parse" if not parse_usable
            else "verification" if checkable and not passed else None
        )
        classified.append(
            (attempt, bool(passed), bool(checkable), reason, parsed_unsubmitted)
        )
    return classified


def _repeat_wall(
    observations: Sequence[_Observation], repeat_id: int,
) -> int | None:
    selected = [item for item in observations if item.inventory.repeat_id == repeat_id]
    if not selected or any(item.result is None for item in selected):
        return None
    results = [item.result for item in selected if item.result is not None]
    if any(
        not _finite(result.root_start_at_ms) or not _finite(result.root_terminal_at_ms)
        for result in results
    ):
        return None
    return int(
        max(int(result.root_terminal_at_ms) for result in results)
        - min(int(result.root_start_at_ms) for result in results)
    )


def _reduce_exp5(observations: Sequence[_Observation]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    groups = _group(observations, lambda item: (item.inventory.configured_model,))
    for key, group in groups:
        row = _base_row("exp5", "model", {"configured_model": key[0]})
        _common(row, group)
        row["planned_first_attempt_ai_unit_count"] = sum(
            len(item.inventory.planned_ai_unit_ids) for item in group
        )
        if any(item.result is None for item in group):
            for name in _EXP5_METRICS[len(_COMMON_METRICS):]:
                if name != "planned_first_attempt_ai_unit_count":
                    _set_missing(row, name, "missing_committed_root_result")
            rows.append(_apply_template(row, _EXP5_METRICS))
            continue
        results = [item.result for item in group if item.result is not None]
        infrastructure_invalid_present = any(
            _is_infrastructure_invalid(result) for result in results
        )
        classified = [entry for result in results for entry in _first_attempt_classification(result)]
        actual = len(classified)
        parsed_unsubmitted_present = any(item[4] for item in classified)
        nonpass = sum(
            not passed
            for _attempt, passed, _checkable, _reason, parsed_unsubmitted in classified
            if not parsed_unsubmitted
        )
        row["actual_first_provider_attempt_count"] = actual
        if parsed_unsubmitted_present:
            _set_not_applicable(
                row,
                "first_attempt_without_verifier_accepted_candidate_count",
                "not_applicable_or_unavailable",
            )
        else:
            row["first_attempt_without_verifier_accepted_candidate_count"] = nonpass
        if parsed_unsubmitted_present:
            _set_not_applicable(
                row,
                "first_attempt_nonpass_rate",
                "not_applicable_or_unavailable",
            )
        elif not infrastructure_invalid_present:
            _set_ratio(row, "first_attempt_nonpass_rate", nonpass, actual)
        row["first_attempt_provider_transport_failure_count"] = sum(
            reason == "transport"
            for _attempt, _passed, _checkable, reason, _parsed_unsubmitted in classified
        )
        row["first_attempt_parse_schema_unusable_count"] = sum(
            reason == "parse"
            for _attempt, _passed, _checkable, reason, _parsed_unsubmitted in classified
        )
        row["first_attempt_verification_checker_rejection_count"] = sum(
            reason == "verification"
            for _attempt, _passed, _checkable, reason, _parsed_unsubmitted in classified
        )
        checkable = sum(entry[2] for entry in classified)
        rejected = sum(entry[2] and not entry[1] for entry in classified)
        row["first_attempt_checkable_candidate_count"] = checkable
        row["first_attempt_explicitly_rejected_by_verifier_count"] = rejected
        if not infrastructure_invalid_present:
            _set_ratio(
                row,
                "first_attempt_verification_rejection_rate",
                rejected,
                checkable,
            )
            _set_ratio(
                row,
                "first_attempt_call_coverage",
                actual,
                row["planned_first_attempt_ai_unit_count"],
            )
        raw_rates: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for observation, result in zip(group, results):
            root_classified = _first_attempt_classification(result)
            root_actual = len(root_classified)
            identity = {
                "case_id": str(result.case_id), "domain": result.domain,
                "difficulty": result.difficulty, "topic_family": result.topic_family,
                "position_stratum": result.position_stratum,
            }
            if not any(item[4] for item in root_classified):
                raw_rates["first_attempt_nonpass_rate"].append(
                    {**identity,
                     "numerator": sum(not item[1] for item in root_classified),
                     "denominator": root_actual}
                )
            root_checkable = sum(item[2] for item in root_classified)
            if root_checkable:
                raw_rates["first_attempt_verification_rejection_rate"].append(
                    {**identity,
                     "numerator": sum(
                         item[2] and not item[1] for item in root_classified
                     ),
                     "denominator": root_checkable}
                )
            raw_rates["first_attempt_call_coverage"].append(
                {**identity, "numerator": root_actual,
                 "denominator": len(observation.inventory.planned_ai_unit_ids)}
            )
        if infrastructure_invalid_present:
            invalid_metrics = [
                "first_attempt_verification_rejection_rate",
                "first_attempt_call_coverage",
            ]
            if not parsed_unsubmitted_present:
                invalid_metrics.insert(0, "first_attempt_nonpass_rate")
            for metric in invalid_metrics:
                _set_missing(
                    row, metric, "infrastructure_invalid_root_present"
                )
        else:
            for metric, raw in raw_rates.items():
                if (
                    metric == "first_attempt_nonpass_rate"
                    and parsed_unsubmitted_present
                ):
                    continue
                _add_rate_interval(row, metric, raw)
        root_tokens = [
            _result_attempt_sum(result, "total_tokens", first_only=True)
            for result in results
        ]
        root_costs = [
            _result_attempt_sum(result, "cost_estimate_cny", first_only=True)
            for result in results
        ]
        for metric, values in (
            ("actual_total_tokens", root_tokens),
            ("actual_cost_estimate_cny", root_costs),
        ):
            total = _sum_nullable(values)
            if total is None:
                _set_missing(row, metric, "usage_missing")
            else:
                row[metric] = total
        for metric, values in (
            ("root_actual_total_tokens", root_tokens),
            ("root_actual_cost_estimate_cny", root_costs),
        ):
            raw = [
                {
                    "case_id": str(result.case_id), "domain": result.domain,
                    "difficulty": result.difficulty, "topic_family": result.topic_family,
                    "position_stratum": result.position_stratum, "value": value,
                }
                for result, value in zip(results, values)
            ]
            _add_distribution(row, metric, raw)
        repeat_ids = {item.inventory.repeat_id for item in group}
        if repeat_ids == {0}:
            repeat0 = _repeat_wall(group, 0)
            if repeat0 is None:
                _set_missing(
                    row, "repeat0_wall_clock_ms", "missing_repeat_wall_clock"
                )
            else:
                _set_value(row, "repeat0_wall_clock_ms", repeat0)
            for repeat_id in (1, 2):
                _set_not_applicable(
                    row,
                    f"repeat{repeat_id}_wall_clock_ms",
                    "not_applicable_or_unavailable",
                )
            row["repeat_wall_clock_ms"] = [repeat0]
            if _finite(repeat0):
                _set_value(row, "model_wall_clock_median_ms", repeat0)
                _set_value(row, "model_wall_clock_min_ms", repeat0)
                _set_value(row, "model_wall_clock_max_ms", repeat0)
                _set_value(row, "model_wall_clock_range_ms", 0)
                _set_missing(
                    row,
                    "model_wall_clock_sample_stddev_ms",
                    "insufficient_observations_for_sample_variance",
                )
            else:
                for name in (
                    "model_wall_clock_median_ms", "model_wall_clock_min_ms",
                    "model_wall_clock_max_ms", "model_wall_clock_range_ms",
                    "model_wall_clock_sample_stddev_ms",
                ):
                    _set_missing(row, name, "missing_repeat_wall_clock")
        else:
            repeat_values = [_repeat_wall(group, repeat_id) for repeat_id in (0, 1, 2)]
            for repeat_id, value in enumerate(repeat_values):
                metric = f"repeat{repeat_id}_wall_clock_ms"
                if value is None:
                    _set_missing(row, metric, "missing_repeat_wall_clock")
                else:
                    row[metric] = value
            row["repeat_wall_clock_ms"] = repeat_values
            if all(_finite(value) for value in repeat_values):
                numeric = [float(value) for value in repeat_values]
                row["model_wall_clock_median_ms"] = float(median(numeric))
                row["model_wall_clock_min_ms"] = min(numeric)
                row["model_wall_clock_max_ms"] = max(numeric)
                row["model_wall_clock_range_ms"] = max(numeric) - min(numeric)
                variance = sample_variance(numeric)
                row["model_wall_clock_sample_stddev_ms"] = (
                    sqrt(variance) if variance is not None else None
                )
            else:
                for name in (
                    "model_wall_clock_median_ms", "model_wall_clock_min_ms",
                    "model_wall_clock_max_ms", "model_wall_clock_range_ms",
                    "model_wall_clock_sample_stddev_ms",
                ):
                    _set_missing(row, name, "missing_repeat_wall_clock")
        rows.append(_apply_template(row, _EXP5_METRICS))
    return rows


def _provider_calls(observations: Sequence[_Observation]) -> int:
    return sum(
        attempt.provider_call_made is True
        for item in observations if item.result is not None
        for attempt in item.result.attempts
    )


def _serialize_jsonl(rows: Sequence[Mapping[str, Any]]) -> str:
    return "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
        for row in rows
    )


def _csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return value


def _serialize_csv(rows: Sequence[Mapping[str, Any]]) -> str:
    preferred = ["table_id", "row_kind", "slice"]
    fieldnames = preferred + sorted(
        set().union(*(row.keys() for row in rows)) - set(preferred)
    ) if rows else preferred
    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({name: _csv_value(row.get(name)) for name in fieldnames})
    return stream.getvalue()


def _stable_rows(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            str(row.get("row_kind")),
            json.dumps(row.get("slice", {}), ensure_ascii=False, sort_keys=True),
        ),
    )


def _row_field(row: Mapping[str, Any], field: str) -> tuple[bool, Any]:
    value: Any = row
    for part in field.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return False, None
        value = value[part]
    return True, value


def _finalize_formal_rows(
    table_id: str,
    rows: Sequence[Mapping[str, Any]],
) -> None:
    """拒绝缺 formal 派生字段或没有直接科学原因的 null。"""

    occurrences = [
        item for item in _FORMAL_METRIC_OCCURRENCES
        if item["table_scope"] == table_id
    ]
    for row in rows:
        for occurrence in occurrences:
            if row.get("row_kind") != occurrence["row_kind"]:
                continue
            metric = str(occurrence["metric_id"])
            parent_metric = (
                "trace_replay_paired_speedup"
                if metric == "trace_replay_paired_speedup_median"
                else metric
            )
            for field in occurrence["evidence_fields"]:
                exists, value = _row_field(row, str(field))
                if not exists:
                    raise ValueError(
                        "required evidence field is absent: "
                        f"{table_id}:{row.get('row_kind')}:{field}"
                    )
                if value is not None:
                    continue
                direct_reasons = [
                    reasons[str(field)]
                    for reasons in (
                        row.get("missing_reasons", {}),
                        row.get("not_applicable_reasons", {}),
                    )
                    if str(field) in reasons
                ]
                parent_reasons = [
                    reasons[parent_metric]
                    for reasons in (
                        row.get("missing_reasons", {}),
                        row.get("not_applicable_reasons", {}),
                    )
                    if parent_metric in reasons
                ]
                metadata = row.get("metric_metadata", {}).get(parent_metric, {})
                metadata_reason = (
                    metadata.get("missing_reason")
                    if isinstance(metadata, Mapping) else None
                )
                reasons = direct_reasons or parent_reasons or (
                    [metadata_reason] if metadata_reason else []
                )
                if len(reasons) != 1 or reasons[0] == (
                    "metric_inputs_not_present_in_this_run"
                ):
                    raise ValueError(
                        "formal null lacks one scientific reason: "
                        f"{table_id}:{row.get('row_kind')}:{field}"
                    )


def _validate_formal_occurrences(
    table_id: str,
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """证明本表 formal occurrence 由真实公式产生或带权威 null 原因。"""

    evidence: list[dict[str, Any]] = []
    _finalize_formal_rows(table_id, rows)
    for occurrence in _FORMAL_METRIC_OCCURRENCES:
        if occurrence["table_scope"] != table_id:
            continue
        producer = str(occurrence["formula_producer"])
        if not callable(globals().get(producer)):
            raise ValueError(f"unknown formal metric producer: {producer}")
        candidates = [
            row for row in rows if row.get("row_kind") == occurrence["row_kind"]
        ]
        if not candidates:
            evidence.append(
                {
                    **occurrence,
                    "production_status": "authority_legal_not_observed",
                    "production_reason": (
                        "no_preregistered_rows_for_occurrence_scope"
                    ),
                }
            )
            continue
        metric = str(occurrence["metric_id"])
        if not any(
            metric in row
            and (
                row.get(metric) is not None
                or metric in row.get("missing_reasons", {})
                or metric in row.get("not_applicable_reasons", {})
            )
            and row.get("missing_reasons", {}).get(metric)
            != "metric_inputs_not_present_in_this_run"
            for row in candidates
        ):
            raise ValueError(
                "formal metric occurrence lacks a computed value or legal null: "
                f"{occurrence['ordinal']}:{table_id}:{metric}"
            )
        evidence.append(
            {
                **occurrence,
                "production_status": "computed_or_legal_null",
                "production_reason": None,
            }
        )
    return evidence


def _stage_payloads(
    payloads: Mapping[Path, str],
    staged: dict[Path, Path],
) -> None:
    """只在目标目录暂存本轮内容；公式验证完成前不发布正式表。"""

    for target, content in payloads.items():
        if target in staged:
            raise ValueError(f"duplicate staged reducer target: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.stage.tmp")
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
        staged[target] = temporary


def _cleanup_staged(staged: Mapping[Path, Path]) -> None:
    for temporary in staged.values():
        if temporary.exists():
            temporary.unlink()


def _commit_staged_metrics(
    staged: Mapping[Path, Path],
    summary_target: Path,
) -> None:
    """发布正式表，并把 summary 作为本轮完整输出的最后提交标志。"""

    if summary_target not in staged:
        raise ValueError("staged reducer output is missing summary.json")
    previous_summary: Path | None = None
    try:
        if summary_target.exists():
            previous_summary = summary_target.with_name(
                f".{summary_target.name}.{uuid4().hex}.previous.tmp"
            )
            summary_target.replace(previous_summary)
        for target in sorted(
            (path for path in staged if path != summary_target),
            key=lambda path: str(path),
        ):
            staged[target].replace(target)
        staged[summary_target].replace(summary_target)
    except Exception:
        # 一旦正式表发布中断，旧 summary 也不能继续代表混合版本。
        if summary_target.exists():
            summary_target.unlink()
        if previous_summary is not None and previous_summary.exists():
            previous_summary.unlink()
        raise
    else:
        if previous_summary is not None and previous_summary.exists():
            previous_summary.unlink()
    finally:
        _cleanup_staged(staged)


def _reduce_run_staged(
    run_dir: str | Path,
    staged: dict[Path, Path],
) -> dict[str, Any]:
    """逐表计算并暂存；调用方负责整轮输出的提交或清理。"""

    store = RunStore(run_dir)
    metrics_dir = Path(run_dir) / "metrics"
    provider_calls: dict[str, int] = {}
    paper_root_inventory_counts: dict[str, int] = {}
    table_summaries: dict[str, dict[str, Any]] = {}
    exp3_reference_inventory_count = 0
    formal_occurrence_evidence: list[dict[str, Any]] = []
    reducers: dict[str, Callable[[Sequence[_Observation]], list[dict[str, Any]]]] = {
        "exp1": _reduce_exp1,
        "exp2": _reduce_exp2,
        "exp4": _reduce_exp4,
        "exp5": _reduce_exp5,
    }
    for table_id in ("exp1", "exp2", "exp3", "exp4", "exp5"):
        challenge_index = _exp4_challenge_index(store) if table_id == "exp4" else None
        slice_keys = _experiment_slice_keys(
            store,
            table_id,
            challenge_index=challenge_index,
        )
        provider_calls[table_id] = 0
        paper_root_inventory_counts[table_id] = 0
        rows: list[dict[str, Any]] = []
        references: list[_Observation] | None = None
        if table_id == "exp3":
            references = [
                _Observation(inventory, result)
                for inventory, result in store.iter_inventory_results(
                    "exp3_references"
                )
            ]
            reference_seen: set[tuple[str, str, str, int]] = set()
            for item in references:
                key = _identity(item)
                if key in reference_seen:
                    raise ValueError(
                        f"duplicate Exp3 reference inventory identity: {key!r}"
                    )
                reference_seen.add(key)
            exp3_reference_inventory_count = len(references)
        for slice_key in slice_keys:
            observations = _load_experiment_slice(
                store,
                table_id,
                slice_key,
                challenge_index=challenge_index,
            )
            provider_calls[table_id] += _provider_calls(observations)
            paper_root_inventory_counts[table_id] += len(observations)
            if table_id == "exp3":
                assert references is not None
                rows.extend(_reduce_exp3(observations, references))
            else:
                rows.extend(reducers[table_id](observations))
            del observations
        rows = _stable_rows(rows)
        if references is not None:
            del references
        formal_occurrence_evidence.extend(
            _validate_formal_occurrences(table_id, rows)
        )
        _stage_payloads(
            {
                metrics_dir / "tables" / f"{table_id}.jsonl": _serialize_jsonl(rows),
                metrics_dir / "tables" / f"{table_id}.csv": _serialize_csv(rows),
            },
            staged,
        )
        table_summaries[table_id] = {
            "row_count": len(rows),
            "jsonl": f"tables/{table_id}.jsonl",
            "csv": f"tables/{table_id}.csv",
        }
        # 冻结规模下峰值只与当前实验/table 的 observations/rows 有关。
        del rows
        del slice_keys

    formal_occurrence_evidence.sort(key=lambda item: int(item["ordinal"]))
    if (
        len(formal_occurrence_evidence) != len(_FORMAL_METRIC_OCCURRENCES)
        or tuple(item["ordinal"] for item in formal_occurrence_evidence)
        != tuple(range(len(_FORMAL_METRIC_OCCURRENCES)))
    ):
        raise ValueError("formal metric occurrence evidence is incomplete")
    summary: dict[str, Any] = {
        "schema_version": "tokenshare.slim_v2.reducer_summary.v1",
        "formal_metric_ids": list(metric_ids()),
        "formal_metric_id_count": len(metric_ids()),
        "formal_metric_unique_id_count": len(set(metric_ids())),
        "formal_metric_occurrences": formal_occurrence_evidence,
        "provider_calls_observed": provider_calls,
        "paper_root_inventory_counts": paper_root_inventory_counts,
        "exp3_reference_inventory_count": exp3_reference_inventory_count,
        "resource_semantics": {"exp3": "simulated_trace_attributed"},
        "tables": table_summaries,
        **_TABLE_METADATA,
    }
    _stage_payloads(
        {
            metrics_dir / "summary.json": (
                json.dumps(
                    summary,
                    ensure_ascii=False,
                    sort_keys=True,
                    allow_nan=False,
                    indent=2,
                )
                + "\n"
            )
        },
        staged,
    )
    return summary


def reduce_run(run_dir: str | Path) -> dict[str, Any]:
    """从单个 run 目录生成五张普通 JSONL/CSV 表及 summary。"""

    metrics_dir = Path(run_dir) / "metrics"
    staged: dict[Path, Path] = {}
    try:
        summary = _reduce_run_staged(run_dir, staged)
        _commit_staged_metrics(staged, metrics_dir / "summary.json")
    except Exception:
        _cleanup_staged(staged)
        raise
    return summary


def _reference_comparison_quality(
    row: dict[str, Any],
    observations: Sequence[_Observation],
) -> None:
    """投影比较表所需的共同质量、首轮 token 与成本，不复用 Exp5 正式表。"""

    _common(row, observations)
    results = [item.result for item in observations if item.result is not None]
    classified = [
        entry
        for result in results
        for entry in _first_attempt_classification(result)
    ]
    actual = len(classified)
    nonpass = sum(
        not passed
        for _attempt, passed, _checkable, _reason, parsed_unsubmitted in classified
        if not parsed_unsubmitted
    )
    checkable = sum(entry[2] for entry in classified)
    rejected = sum(entry[2] and not entry[1] for entry in classified)
    row["planned_first_attempt_ai_unit_count"] = sum(
        len(item.inventory.planned_ai_unit_ids) for item in observations
    )
    row["actual_first_provider_attempt_count"] = actual
    row["first_attempt_without_verifier_accepted_candidate_count"] = nonpass
    _set_ratio(row, "first_attempt_nonpass_rate", nonpass, actual)
    row["first_attempt_provider_transport_failure_count"] = sum(
        reason == "transport"
        for _attempt, _passed, _checkable, reason, _parsed_unsubmitted in classified
    )
    row["first_attempt_parse_schema_unusable_count"] = sum(
        reason == "parse"
        for _attempt, _passed, _checkable, reason, _parsed_unsubmitted in classified
    )
    row["first_attempt_verification_checker_rejection_count"] = sum(
        reason == "verification"
        for _attempt, _passed, _checkable, reason, _parsed_unsubmitted in classified
    )
    row["first_attempt_checkable_candidate_count"] = checkable
    row["first_attempt_explicitly_rejected_by_verifier_count"] = rejected
    _set_ratio(
        row, "first_attempt_verification_rejection_rate", rejected, checkable
    )
    _set_ratio(
        row,
        "first_attempt_call_coverage",
        actual,
        row["planned_first_attempt_ai_unit_count"],
    )
    for output_name, attempt_field in (
        ("actual_first_attempt_total_tokens", "total_tokens"),
        ("actual_first_attempt_cost_estimate_cny", "cost_estimate_cny"),
    ):
        total = _sum_nullable(
            _result_attempt_sum(result, attempt_field, first_only=True)
            for result in results
        )
        if total is None:
            _set_missing(row, output_name, "usage_missing")
        else:
            _set_value(row, output_name, total)
    pricing_versions = sorted(
        {
            str(attempt.pricing_version)
            for result in results
            for attempt in result.attempts
            if (
                attempt.provider_call_made is True
                and attempt.attempt_ordinal == 0
                and attempt.pricing_version is not None
            )
        }
    )
    row["pricing_versions"] = pricing_versions


def _reference_comparison_live_observations(
    target_store: RunStore,
) -> tuple[dict[str, list[_Observation]], set[str]]:
    """读取并冻结三模型 Exp5 实测行；不读取正式 metrics 表或 provider。"""

    by_model: dict[str, list[_Observation]] = defaultdict(list)
    seen: set[tuple[str, str, str, int]] = set()
    for inventory, result in target_store.iter_inventory_results(
        "roots", experiment_id="exp5"
    ):
        identity = (
            str(inventory.experiment_id),
            str(inventory.condition_id),
            str(inventory.case_id),
            int(inventory.repeat_id),
        )
        if identity in seen:
            raise ValueError(f"duplicate Exp5 target inventory identity: {identity!r}")
        seen.add(identity)
        if result is None:
            raise ValueError("target Exp5 root result is not committed")
        if (
            inventory.repeat_id != 0
            or result.repeat_id != 0
            or result.experiment_id != "exp5"
            or result.configured_model != inventory.configured_model
        ):
            raise ValueError("target must contain committed Exp5 repeat-0 model results")
        by_model[str(inventory.configured_model)].append(_Observation(inventory, result))
    if tuple(by_model) != _EXP5_V4_REFERENCE_LIVE_MODELS:
        raise ValueError(
            "target must contain only the frozen three Exp5 models in order"
        )
    case_sets = {
        model: {str(item.inventory.case_id) for item in observations}
        for model, observations in by_model.items()
    }
    target_cases = case_sets[_EXP5_V4_REFERENCE_LIVE_MODELS[0]]
    if not target_cases or any(case_set != target_cases for case_set in case_sets.values()):
        raise ValueError("target Exp5 model case sets must match exactly")
    return by_model, target_cases


def _reference_comparison_source_observations(
    source_store: RunStore,
    target_cases: set[str],
) -> list[_Observation]:
    """只抽取 target case 对应的 Exp1 Flash V4 committed facts。"""

    by_case: dict[str, _Observation] = {}
    for inventory, result in source_store.iter_inventory_results(
        "roots", experiment_id="exp1"
    ):
        case_id = str(inventory.case_id)
        if case_id not in target_cases:
            continue
        if (
            inventory.configured_model != _EXP5_V4_REFERENCE_SOURCE_MODEL
            or inventory.repeat_id != 0
            or result is None
            or result.experiment_id != "exp1"
            or result.repeat_id != 0
            or result.configured_model != _EXP5_V4_REFERENCE_SOURCE_MODEL
        ):
            raise ValueError(
                "source must contain committed Exp1 deepseek-v4-flash repeat-0 results"
            )
        if case_id in by_case:
            raise ValueError(f"duplicate Exp1 Flash source case: {case_id}")
        if any(
            attempt.provider_call_made is True and attempt.attempt_ordinal > 0
            for attempt in result.attempts
        ):
            raise ValueError("source Exp1 Flash results must not contain ordinal>0 provider calls")
        by_case[case_id] = _Observation(inventory, result)
    if set(by_case) != target_cases:
        raise ValueError("source and target case sets must match exactly")
    return [by_case[case_id] for case_id in sorted(by_case)]


def _reference_comparison_row(
    *,
    configured_model: str,
    observation_origin: str,
    observations: Sequence[_Observation],
) -> dict[str, Any]:
    """构造一行补充对比，不将它纳入 153 个正式指标。"""

    row = _base_row(
        _EXP5_V4_REFERENCE_TABLE_ID,
        "model_reference",
        {"configured_model": configured_model},
    )
    row.update(
        configured_model=configured_model,
        observation_origin=observation_origin,
        source_experiment_id=("exp5" if observation_origin == "exp5_live" else "exp1"),
        source_repeat_id=0,
        case_ids=sorted(str(item.inventory.case_id) for item in observations),
        wall_clock_field_names=list(_EXP5_V4_REFERENCE_WALL_CLOCK_FIELDS),
    )
    _reference_comparison_quality(row, observations)
    if observation_origin == "exp5_live":
        repeat0 = _repeat_wall(observations, 0)
        if repeat0 is None:
            _set_missing(row, "repeat0_wall_clock_ms", "missing_repeat_wall_clock")
            for field in _EXP5_V4_REFERENCE_WALL_CLOCK_FIELDS[1:]:
                _set_missing(row, field, "missing_repeat_wall_clock")
        else:
            _set_value(row, "repeat0_wall_clock_ms", repeat0)
            for field in ("repeat1_wall_clock_ms", "repeat2_wall_clock_ms"):
                _set_not_applicable(row, field, "not_applicable_or_unavailable")
            _set_value(row, "repeat_wall_clock_ms", [repeat0])
            _set_value(row, "model_wall_clock_median_ms", repeat0)
            _set_value(row, "model_wall_clock_min_ms", repeat0)
            _set_value(row, "model_wall_clock_max_ms", repeat0)
            _set_value(row, "model_wall_clock_range_ms", 0)
            _set_not_applicable(
                row,
                "model_wall_clock_sample_stddev_ms",
                "insufficient_observations_for_sample_variance",
            )
    else:
        for field in _EXP5_V4_REFERENCE_WALL_CLOCK_FIELDS:
            _set_not_applicable(row, field, "not_applicable_or_unavailable")
    return row


def _commit_staged_supplemental(staged: Mapping[Path, Path], marker: Path) -> None:
    """以 JSONL 为最终提交标志发布补充表；失败时恢复同表的上一版本。"""

    if marker not in staged:
        raise ValueError("supplemental staged output is missing the JSONL marker")
    backups: dict[Path, Path] = {}
    try:
        for target in staged:
            if target.exists():
                backup = target.with_name(f".{target.name}.{uuid4().hex}.previous.tmp")
                target.replace(backup)
                backups[target] = backup
        for target in sorted((path for path in staged if path != marker), key=str):
            staged[target].replace(target)
        staged[marker].replace(marker)
    except Exception:
        for target in staged:
            if target.exists():
                target.unlink()
        for target, backup in backups.items():
            if backup.exists():
                backup.replace(target)
        raise
    else:
        for backup in backups.values():
            if backup.exists():
                backup.unlink()
    finally:
        _cleanup_staged(staged)


def reduce_exp5_with_exp1_v4_reference(
    run_dir: str | Path,
    source_run_dir: str | Path,
) -> dict[str, Any]:
    """发布 Exp5 三模型实测加 Exp1 Flash V4 历史事实的隔离补充表。

    此函数只读两份 ordinary inventory/result；不会调用 provider，也不会改写
    ``metrics/tables/exp5.*`` 或 ``summary.json``。
    """

    target_store = RunStore(run_dir)
    source_store = RunStore(source_run_dir)
    live_by_model, target_cases = _reference_comparison_live_observations(target_store)
    source_observations = _reference_comparison_source_observations(
        source_store, target_cases
    )
    rows = [
        _reference_comparison_row(
            configured_model=model,
            observation_origin="exp5_live",
            observations=live_by_model[model],
        )
        for model in _EXP5_V4_REFERENCE_LIVE_MODELS
    ]
    rows.append(
        _reference_comparison_row(
            configured_model=_EXP5_V4_REFERENCE_SOURCE_MODEL,
            observation_origin="exp1_reused_actual",
            observations=source_observations,
        )
    )
    supplemental_dir = target_store.run_dir / "metrics" / "supplemental"
    jsonl_target = supplemental_dir / f"{_EXP5_V4_REFERENCE_TABLE_ID}.jsonl"
    csv_target = supplemental_dir / f"{_EXP5_V4_REFERENCE_TABLE_ID}.csv"
    staged: dict[Path, Path] = {}
    try:
        _stage_payloads(
            {
                jsonl_target: _serialize_jsonl(rows),
                csv_target: _serialize_csv(rows),
            },
            staged,
        )
        _commit_staged_supplemental(staged, jsonl_target)
    except Exception:
        _cleanup_staged(staged)
        raise
    return {
        "schema_version": "tokenshare.slim_v2.exp5_v4_reference_comparison.v1",
        "table_id": _EXP5_V4_REFERENCE_TABLE_ID,
        "row_count": len(rows),
        "jsonl": str(jsonl_target.relative_to(target_store.run_dir)),
        "csv": str(csv_target.relative_to(target_store.run_dir)),
    }


__all__ = [
    "formal_metric_occurrences", "metric_ids", "reduce_exp5_with_exp1_v4_reference",
    "reduce_run", "sample_variance",
    "stratified_case_cluster_bootstrap", "type7_quantile",
]
