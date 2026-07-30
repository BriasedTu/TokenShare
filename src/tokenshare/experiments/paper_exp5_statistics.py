"""Experiment 5 v3 的预注册配对统计与顺序敏感性汇总。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
import math
import random
from statistics import median
from typing import Any, Callable

from tokenshare.experiments.paper_exp5_model_comparison import (
    EXP5_V3_SEQUENCE_PLAN,
    EXP5_V3_SEQUENCE_PLAN_DIGEST,
    EXP5_V3_STRATUM_ORDER,
    load_exp5_v3_selection,
)


EXP5_BOOTSTRAP_SEED = 5005
EXP5_BOOTSTRAP_RESAMPLES = 10_000
EXP5_EXPECTED_MODEL_COUNT = 4
EXP5_EXPECTED_REPEAT_IDS = (0, 1, 2)
EXP5_GLOBAL_IN_FLIGHT_LIMIT = 3
EXP5_MODEL_ORDER = EXP5_V3_SEQUENCE_PLAN[0]
EXP5_STRATUM_ORDER = EXP5_V3_STRATUM_ORDER


@dataclass(frozen=True)
class _Metric:
    metric_id: str
    field_name: str
    kind: str


_METRICS = (
    _Metric("root_completion", "root_completed", "binary"),
    _Metric("accepted_validity", "accepted_validity", "binary"),
    _Metric("total_tokens", "total_tokens", "continuous"),
    _Metric("provider_latency_ms", "provider_latency_ms", "continuous"),
)


def holm_adjust_p_values(
    raw_p_values: Sequence[int | float],
) -> tuple[float, ...]:
    """按预注册的 step-down Holm 算法校正一个检验族。"""

    normalized: list[float] = []
    for value in raw_p_values:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or not 0.0 <= float(value) <= 1.0
        ):
            raise ValueError("raw p-values must be finite numbers from 0 through 1")
        normalized.append(float(value))
    ordered = sorted(enumerate(normalized), key=lambda item: item[1])
    running = 0.0
    adjusted = [0.0] * len(normalized)
    for rank, (index, p_value) in enumerate(ordered):
        candidate = min(1.0, (len(ordered) - rank) * p_value)
        running = max(running, candidate)
        adjusted[index] = running
    return tuple(adjusted)


def build_exp5_paired_comparison_rows(
    records: Sequence[Mapping[str, Any]],
    *,
    bootstrap_seed: int = EXP5_BOOTSTRAP_SEED,
    bootstrap_resamples: int = EXP5_BOOTSTRAP_RESAMPLES,
) -> tuple[dict[str, Any], ...]:
    """生成四模型六 pair 的 root-level 配对比较行。"""

    normalized, models, groups, _ = _validate_records(records)
    seed = _exact_non_negative_int(bootstrap_seed, "bootstrap_seed")
    resamples = _exact_positive_int(bootstrap_resamples, "bootstrap_resamples")
    pairs = tuple(combinations(models, 2))
    strata = ("overall",) + EXP5_STRATUM_ORDER

    result: list[dict[str, Any]] = []
    for metric in _METRICS:
        for stratum_id in strata:
            stratum_groups = [
                group
                for group in groups.values()
                if stratum_id == "overall"
                or next(iter(group.values()))["stratum_id"] == stratum_id
            ]
            stratum_rows: list[dict[str, Any]] = []
            for model_a, model_b in pairs:
                stratum_rows.append(
                    _paired_row(
                        metric=metric,
                        stratum_id=stratum_id,
                        model_a=model_a,
                        model_b=model_b,
                        groups=stratum_groups,
                        bootstrap_seed=seed,
                        bootstrap_resamples=resamples,
                    )
                )
            if metric.kind == "binary":
                applicable_rows = [
                    row
                    for row in stratum_rows
                    if row["raw_p_value"] is not None
                ]
                adjusted = holm_adjust_p_values(
                    tuple(float(row["raw_p_value"]) for row in applicable_rows)
                )
                for row, adjusted_p_value in zip(
                    applicable_rows,
                    adjusted,
                    strict=True,
                ):
                    row["holm_adjusted_p_value"] = adjusted_p_value
            result.extend(stratum_rows)
    return tuple(result)


def build_exp5_order_and_concurrency_rows(
    records: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """生成 arm 顺序审计和仅作 secondary 的 order-slot 汇总。"""

    normalized, models, _, order_inventory = _validate_records(records)
    result: list[dict[str, Any]] = []
    for repeat_id in EXP5_EXPECTED_REPEAT_IDS:
        arms = order_inventory[repeat_id]
        global_peak = max(int(arm["observed_peak_concurrency"]) for arm in arms)
        previous_end: datetime | None = None
        for arm in arms:
            result.append(
                {
                    "schema_version": "tokenshare.paper_exp5_order_audit.v1",
                    "row_scope": "arm_audit",
                    "analysis_role": "audit",
                    "repeat_id": repeat_id,
                    "cohort_member_id": arm["cohort_member_id"],
                    "order_slot": arm["order_slot"],
                    "predecessor_member_id": arm["predecessor_member_id"],
                    "arm_started_at": _isoformat(arm["arm_started_at"]),
                    "arm_ended_at": _isoformat(arm["arm_ended_at"]),
                    "previous_arm_ended_at": (
                        _isoformat(previous_end)
                        if previous_end is not None
                        else None
                    ),
                    "arm_overlap": False,
                    "condition_count": len(arm["condition_ids"]),
                    "condition_ids": list(arm["condition_ids"]),
                    "global_peak_in_flight": global_peak,
                    "global_in_flight_limit": EXP5_GLOBAL_IN_FLIGHT_LIMIT,
                    "global_capacity_compliant": (
                        global_peak <= EXP5_GLOBAL_IN_FLIGHT_LIMIT
                    ),
                }
            )
            previous_end = arm["arm_ended_at"]

    for model_id in models:
        for order_slot in range(1, EXP5_EXPECTED_MODEL_COUNT + 1):
            slot_records = [
                row
                for row in normalized
                if row["cohort_member_id"] == model_id
                and row["order_slot"] == order_slot
            ]
            if not slot_records:
                continue
            result.append(
                _order_sensitivity_row(
                    model_id=model_id,
                    order_slot=order_slot,
                    records=slot_records,
                )
            )
    return tuple(result)


def _paired_row(
    *,
    metric: _Metric,
    stratum_id: str,
    model_a: str,
    model_b: str,
    groups: Sequence[Mapping[str, Mapping[str, Any]]],
    bootstrap_seed: int,
    bootstrap_resamples: int,
) -> dict[str, Any]:
    paired: list[tuple[str, float, float]] = []
    missing_a = 0
    missing_b = 0
    missing_both = 0
    missingness_reasons: set[str] = set()
    for group in groups:
        row_a = group[model_a]
        row_b = group[model_b]
        value_a = row_a[metric.field_name]
        value_b = row_b[metric.field_name]
        if value_a is None or value_b is None:
            if value_a is None and value_b is None:
                missing_both += 1
            elif value_a is None:
                missing_a += 1
            else:
                missing_b += 1
            if value_a is None:
                missingness_reasons.add(
                    str(
                        row_a.get(f"{metric.field_name}_unavailable_reason")
                        or f"{metric.field_name}_missing"
                    )
                )
            if value_b is None:
                missingness_reasons.add(
                    str(
                        row_b.get(f"{metric.field_name}_unavailable_reason")
                        or f"{metric.field_name}_missing"
                    )
                )
            continue
        paired.append(
            (
                str(row_a["case_id"]),
                float(value_a),
                float(value_b),
            )
        )

    differences = [value_a - value_b for _, value_a, value_b in paired]
    statistic = _mean if metric.kind == "binary" else _median
    estimate = statistic(differences) if differences else None
    ci_low, ci_high = _cluster_bootstrap_ci(
        paired,
        statistic=statistic,
        seed=bootstrap_seed,
        resamples=bootstrap_resamples,
    )
    model_a_values = [value_a for _, value_a, _ in paired]
    model_b_values = [value_b for _, _, value_b in paired]
    model_estimator = _mean if metric.kind == "binary" else _median
    raw_p_value = (
        _exact_binary_sign_flip_p_value(differences)
        if metric.kind == "binary" and paired
        else None
    )
    domain, topic_family = _stratum_fields(stratum_id)
    return {
        "schema_version": "tokenshare.paper_exp5_paired_comparison.v1",
        "metric": metric.metric_id,
        "metric_kind": metric.kind,
        "stratum_id": stratum_id,
        "domain": domain,
        "topic_family": topic_family,
        "model_a": model_a,
        "model_b": model_b,
        "pairing_key": "case_id×repeat_id",
        "pairing_denominator": len(groups),
        "paired_sample_size": len(paired),
        "paired_case_count": len({case_id for case_id, _, _ in paired}),
        "model_a_estimate": (
            model_estimator(model_a_values) if model_a_values else None
        ),
        "model_b_estimate": (
            model_estimator(model_b_values) if model_b_values else None
        ),
        "paired_difference": estimate,
        "ci_95_low": ci_low,
        "ci_95_high": ci_high,
        "confidence_level": 0.95,
        "method": (
            "paired_mean_difference_exact_sign_flip_"
            "case_cluster_bootstrap_ci"
            if metric.kind == "binary"
            else "paired_median_difference_case_cluster_bootstrap_ci"
        ),
        "raw_p_value": raw_p_value,
        "holm_adjusted_p_value": None,
        "p_value_applicability": (
            "insufficient_paired_observations"
            if not paired
            else (
                "applicable"
                if metric.kind == "binary"
                else "not_preregistered_for_continuous_metric"
            )
        ),
        "effect_direction": _effect_direction(estimate),
        "missing_model_a_count": missing_a,
        "missing_model_b_count": missing_b,
        "missing_both_count": missing_both,
        "missingness_reasons": sorted(missingness_reasons),
        "bootstrap_cluster": "case_id",
        "bootstrap_seed": bootstrap_seed,
        "bootstrap_resamples": bootstrap_resamples,
    }


def _validate_records(
    records: Sequence[Mapping[str, Any]],
) -> tuple[
    tuple[dict[str, Any], ...],
    tuple[str, ...],
    dict[tuple[str, int], dict[str, dict[str, Any]]],
    dict[int, list[dict[str, Any]]],
]:
    if isinstance(records, (str, bytes)) or not isinstance(records, Sequence):
        raise ValueError("Experiment 5 case records must be a sequence")
    selection = load_exp5_v3_selection()
    expected_selection_digest = str(selection["selection_digest"])
    parent_catalog = selection["parent_catalog"]
    if not isinstance(parent_catalog, Mapping):
        raise ValueError("tracked Experiment 5 parent catalog is invalid")
    expected_parent_catalog_digest = str(parent_catalog["catalog_digest"])
    expected_case_strata = {
        str(case_id): str(stratum["stratum_id"])
        for stratum in selection["strata"]
        for case_id in stratum["ordered_case_ids"]
    }
    expected_case_ids = set(expected_case_strata)
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    for raw_record in records:
        if not isinstance(raw_record, Mapping):
            raise ValueError("Experiment 5 case records must be mappings")
        record = dict(raw_record)
        case_id = _required_string(record.get("case_id"), "case_id")
        model_id = _required_string(
            record.get("cohort_member_id"),
            "cohort_member_id",
        )
        repeat_id = _exact_non_negative_int(record.get("repeat_id"), "repeat_id")
        record["case_id"] = case_id
        record["cohort_member_id"] = model_id
        record["repeat_id"] = repeat_id
        record["condition_id"] = _required_string(
            record.get("condition_id"),
            "condition_id",
        )
        record["stratum_id"] = _required_string(
            record.get("stratum_id"),
            "stratum_id",
        )
        if case_id not in expected_case_ids:
            raise ValueError("Experiment 5 records do not match tracked case inventory")
        if record["stratum_id"] != expected_case_strata[case_id]:
            raise ValueError("Experiment 5 tracked case-to-stratum mapping drift")
        if (
            record.get("sequence_plan_digest")
            != EXP5_V3_SEQUENCE_PLAN_DIGEST
        ):
            raise ValueError("Exp5 v3 sequence plan digest is not pre-registered")
        if record.get("exp5_selection_digest") != expected_selection_digest:
            raise ValueError("Exp5 v3 selection digest is not pre-registered")
        if (
            record.get("exp5_selection_parent_catalog_digest")
            != expected_parent_catalog_digest
        ):
            raise ValueError("Exp5 v3 parent catalog digest is not pre-registered")
        if record.get("paper_eligible") is not True:
            raise ValueError("non-eligible case record")
        key = (case_id, repeat_id, model_id)
        if key in seen:
            raise ValueError("duplicate case-repeat-model record")
        seen.add(key)
        for metric in _METRICS:
            value = record.get(metric.field_name)
            if value is None:
                record[metric.field_name] = None
            elif metric.kind == "binary":
                if not isinstance(value, bool):
                    raise ValueError(f"{metric.field_name} must be a bool or null")
            elif (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or float(value) < 0
            ):
                raise ValueError(
                    f"{metric.field_name} must be a non-negative number or null"
                )
        normalized.append(record)

    if {str(row["case_id"]) for row in normalized} != expected_case_ids:
        raise ValueError("Experiment 5 records do not match tracked case inventory")
    models = EXP5_MODEL_ORDER
    if {
        str(row["cohort_member_id"]) for row in normalized
    } != set(models):
        raise ValueError(
            "Experiment 5 statistics require the pre-registered model cohort"
        )
    repeats = tuple(sorted({int(row["repeat_id"]) for row in normalized}))
    if repeats != EXP5_EXPECTED_REPEAT_IDS:
        raise ValueError("Experiment 5 statistics require repeats 0, 1, and 2")
    if {
        str(row["stratum_id"]) for row in normalized
    } != set(EXP5_STRATUM_ORDER):
        raise ValueError("Experiment 5 statistics require the pre-registered strata")
    groups: dict[tuple[str, int], dict[str, dict[str, Any]]] = defaultdict(dict)
    for record in normalized:
        groups[(record["case_id"], record["repeat_id"])][
            record["cohort_member_id"]
        ] = record
    model_set = set(models)
    for group in groups.values():
        if set(group) != model_set:
            raise ValueError("incomplete four-model pairing")
        if len({str(row["stratum_id"]) for row in group.values()}) != 1:
            raise ValueError("paired model rows disagree on stratum")
    repeats_by_case: dict[str, set[int]] = defaultdict(set)
    for record in normalized:
        repeats_by_case[str(record["case_id"])].add(int(record["repeat_id"]))
    if any(
        tuple(sorted(case_repeats)) != EXP5_EXPECTED_REPEAT_IDS
        for case_repeats in repeats_by_case.values()
    ):
        raise ValueError("each Experiment 5 case must cover all three repeats")
    order_inventory = _validate_order_inventory(normalized, models)
    return tuple(normalized), models, dict(groups), order_inventory


def _validate_order_inventory(
    records: Sequence[Mapping[str, Any]],
    models: Sequence[str],
) -> dict[int, list[dict[str, Any]]]:
    metadata: dict[tuple[int, str], dict[str, Any]] = {}
    conditions: dict[tuple[int, str, str], dict[str, Any]] = {}
    for record in records:
        repeat_id = int(record["repeat_id"])
        model_id = str(record["cohort_member_id"])
        order_slot = _exact_positive_int(record.get("order_slot"), "order_slot")
        if order_slot > EXP5_EXPECTED_MODEL_COUNT:
            raise ValueError("order_slot must be from 1 through 4")
        predecessor = record.get("predecessor_member_id")
        if predecessor is not None:
            predecessor = _required_string(
                predecessor,
                "predecessor_member_id",
            )
        member_key = (repeat_id, model_id)
        member_metadata = {
            "order_slot": order_slot,
            "predecessor_member_id": predecessor,
        }
        if member_key in metadata and metadata[member_key] != member_metadata:
            raise ValueError("inconsistent model order metadata")
        metadata[member_key] = member_metadata

        started_at = _timestamp(
            record.get("condition_started_at"),
            "condition_started_at",
        )
        ended_at = _timestamp(
            record.get("condition_ended_at"),
            "condition_ended_at",
        )
        if ended_at < started_at:
            raise ValueError("condition time window ends before it starts")
        observed_peak = _exact_non_negative_int(
            record.get("observed_peak_concurrency"),
            "observed_peak_concurrency",
        )
        if observed_peak > EXP5_GLOBAL_IN_FLIGHT_LIMIT:
            raise ValueError("global peak in-flight exceeds 3")
        condition_key = (repeat_id, model_id, str(record["condition_id"]))
        condition_metadata = {
            "started_at": started_at,
            "ended_at": ended_at,
            "observed_peak_concurrency": observed_peak,
        }
        if (
            condition_key in conditions
            and conditions[condition_key] != condition_metadata
        ):
            raise ValueError("inconsistent condition time window")
        conditions[condition_key] = condition_metadata

    inventory: dict[int, list[dict[str, Any]]] = {}
    for repeat_id in EXP5_EXPECTED_REPEAT_IDS:
        expected_order = EXP5_V3_SEQUENCE_PLAN[repeat_id]
        member_rows: list[dict[str, Any]] = []
        for model_id in models:
            member_metadata = metadata.get((repeat_id, model_id))
            if member_metadata is None:
                raise ValueError("incomplete model order inventory")
            expected_slot = expected_order.index(model_id) + 1
            expected_predecessor = (
                expected_order[expected_slot - 2]
                if expected_slot > 1
                else None
            )
            if member_metadata != {
                "order_slot": expected_slot,
                "predecessor_member_id": expected_predecessor,
            }:
                raise ValueError(
                    "model order does not match the pre-registered model order"
                )
            member_conditions = [
                value
                for (condition_repeat, condition_model, _), value in conditions.items()
                if condition_repeat == repeat_id and condition_model == model_id
            ]
            ordered_conditions = sorted(
                member_conditions,
                key=lambda value: (value["started_at"], value["ended_at"]),
            )
            previous_condition_end: datetime | None = None
            for condition in ordered_conditions:
                if (
                    previous_condition_end is not None
                    and condition["started_at"] < previous_condition_end
                ):
                    raise ValueError(
                        "condition windows overlap within model arm"
                    )
                previous_condition_end = condition["ended_at"]
            condition_ids = sorted(
                condition_id
                for condition_repeat, condition_model, condition_id in conditions
                if condition_repeat == repeat_id and condition_model == model_id
            )
            member_rows.append(
                {
                    **member_metadata,
                    "cohort_member_id": model_id,
                    "condition_ids": condition_ids,
                    "arm_started_at": min(
                        value["started_at"] for value in ordered_conditions
                    ),
                    "arm_ended_at": max(
                        value["ended_at"] for value in ordered_conditions
                    ),
                    "observed_peak_concurrency": max(
                        value["observed_peak_concurrency"]
                        for value in ordered_conditions
                    ),
                }
            )
        member_rows.sort(key=lambda row: int(row["order_slot"]))
        if tuple(row["cohort_member_id"] for row in member_rows) != expected_order:
            raise ValueError(
                "model order does not match the pre-registered model order"
            )
        previous_model: str | None = None
        previous_end: datetime | None = None
        for row in member_rows:
            if row["predecessor_member_id"] != previous_model:
                raise ValueError("model predecessor chain is invalid")
            if (
                previous_end is not None
                and row["arm_started_at"] < previous_end
            ):
                raise ValueError("model arm windows overlap")
            previous_model = str(row["cohort_member_id"])
            previous_end = row["arm_ended_at"]
        inventory[repeat_id] = member_rows
    return inventory


def _order_sensitivity_row(
    *,
    model_id: str,
    order_slot: int,
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": "tokenshare.paper_exp5_order_sensitivity.v1",
        "row_scope": "order_sensitivity",
        "analysis_role": "secondary",
        "cohort_member_id": model_id,
        "order_slot": order_slot,
        "repeat_ids": sorted({int(row["repeat_id"]) for row in records}),
        "case_repeat_denominator": len(records),
    }
    for metric in _METRICS:
        values = [
            float(row[metric.field_name])
            for row in records
            if row[metric.field_name] is not None
        ]
        result[f"{metric.metric_id}_sample_size"] = len(values)
        result[f"{metric.metric_id}_missing_count"] = len(records) - len(values)
        result[
            f"{metric.metric_id}_{'rate' if metric.kind == 'binary' else 'median'}"
        ] = (
            (_mean(values) if metric.kind == "binary" else _median(values))
            if values
            else None
        )
    return result


def _cluster_bootstrap_ci(
    paired: Sequence[tuple[str, float, float]],
    *,
    statistic: Callable[[Sequence[float]], float],
    seed: int,
    resamples: int,
) -> tuple[float | None, float | None]:
    if not paired:
        return None, None
    by_case: dict[str, list[float]] = defaultdict(list)
    for case_id, value_a, value_b in paired:
        by_case[case_id].append(value_a - value_b)
    case_ids = sorted(by_case)
    rng = random.Random(seed)
    estimates: list[float] = []
    for _ in range(resamples):
        sampled_differences: list[float] = []
        for _ in case_ids:
            sampled_case = case_ids[rng.randrange(len(case_ids))]
            sampled_differences.extend(by_case[sampled_case])
        estimates.append(statistic(sampled_differences))
    return _quantile(estimates, 0.025), _quantile(estimates, 0.975)


def _exact_binary_sign_flip_p_value(differences: Sequence[float]) -> float:
    non_zero = [difference for difference in differences if difference != 0.0]
    if not non_zero:
        return 1.0
    observed = abs(
        sum(1 if difference > 0.0 else -1 for difference in non_zero)
    )
    count = len(non_zero)
    extreme_assignments = sum(
        math.comb(count, positives)
        for positives in range(count + 1)
        if abs(2 * positives - count) >= observed
    )
    return extreme_assignments / (2**count)


def _stratum_fields(stratum_id: str) -> tuple[str | None, str | None]:
    if stratum_id == "overall":
        return None, None
    values = stratum_id.split(":")
    if values[0] == "factorization":
        return "factorization", None
    return "lean_proof", values[-1]


def _effect_direction(value: float | None) -> str:
    if value is None or value == 0.0:
        return "no_difference"
    return "model_a_higher" if value > 0.0 else "model_b_higher"


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _median(values: Sequence[float]) -> float:
    return float(median(values))


def _quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    fraction = position - lower
    return float(
        ordered[lower] * (1.0 - fraction)
        + ordered[upper] * fraction
    )


def _required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _exact_non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _exact_positive_int(value: Any, field_name: str) -> int:
    result = _exact_non_negative_int(value, field_name)
    if result < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return result


def _timestamp(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if timestamp.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")
    return timestamp


def _isoformat(value: datetime) -> str:
    return value.isoformat()


__all__ = [
    "EXP5_BOOTSTRAP_RESAMPLES",
    "EXP5_BOOTSTRAP_SEED",
    "build_exp5_order_and_concurrency_rows",
    "build_exp5_paired_comparison_rows",
    "holm_adjust_p_values",
]
