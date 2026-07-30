from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from itertools import combinations

import pytest

from tokenshare.experiments.paper_exp5_model_comparison import (
    EXP5_V3_SEQUENCE_PLAN,
    EXP5_V3_SEQUENCE_PLAN_DIGEST,
    EXP5_V3_STRATUM_ORDER,
    load_exp5_v3_selection,
)
from tokenshare.experiments.paper_exp5_statistics import (
    EXP5_BOOTSTRAP_RESAMPLES,
    EXP5_BOOTSTRAP_SEED,
    build_exp5_order_and_concurrency_rows,
    build_exp5_paired_comparison_rows,
    holm_adjust_p_values,
)


MODEL_ORDERS = EXP5_V3_SEQUENCE_PLAN
MODEL_IDS = MODEL_ORDERS[0]
MODEL_A, MODEL_B, MODEL_C, MODEL_D = MODEL_IDS
EXP5_SELECTION = load_exp5_v3_selection()
EXP5_SELECTION_DIGEST = str(EXP5_SELECTION["selection_digest"])
EXP5_SELECTION_PARENT_CATALOG_DIGEST = str(
    EXP5_SELECTION["parent_catalog"]["catalog_digest"]
)
CASES = tuple(
    (case_id, str(stratum["stratum_id"]))
    for stratum in EXP5_SELECTION["strata"]
    for case_id in stratum["ordered_case_ids"]
)
CASE_STRATUM = dict(CASES)
CASE_REPEAT_COUNT = len(CASES) * 3
FIRST_FACTOR_CASE = next(
    case_id
    for case_id, stratum_id in CASES
    if stratum_id == "factorization:hard"
)
FIRST_INDUCTION_CASE = next(
    case_id
    for case_id, stratum_id in CASES
    if stratum_id == "lean_proof:hard:induction"
)


def test_exp5_paired_statistics_use_case_repeat_pairs_and_exact_holm() -> None:
    records = _case_records()

    first = build_exp5_paired_comparison_rows(
        records,
        bootstrap_resamples=128,
    )
    second = build_exp5_paired_comparison_rows(
        records,
        bootstrap_resamples=128,
    )

    assert first == second
    assert EXP5_BOOTSTRAP_SEED == 5005
    assert EXP5_BOOTSTRAP_RESAMPLES == 10_000
    completion_rows = [
        row
        for row in first
        if row["metric"] == "root_completion"
        and row["stratum_id"] == "overall"
    ]
    assert len(completion_rows) == 6
    assert {
        row["stratum_id"]
        for row in first
        if row["metric"] == "root_completion"
    } == {
        "overall",
        *EXP5_V3_STRATUM_ORDER,
    }
    assert {
        (row["model_a"], row["model_b"]) for row in completion_rows
    } == set(combinations(MODEL_IDS, 2))

    a_d = _comparison(
        first,
        metric="root_completion",
        stratum_id="overall",
        model_a=MODEL_A,
        model_b=MODEL_D,
    )
    assert a_d["pairing_denominator"] == CASE_REPEAT_COUNT
    assert a_d["paired_sample_size"] == CASE_REPEAT_COUNT
    assert a_d["model_a_estimate"] == 1.0
    assert a_d["model_b_estimate"] == 0.0
    assert a_d["paired_difference"] == 1.0
    assert a_d["raw_p_value"] == 2.0 ** (1 - CASE_REPEAT_COUNT)
    assert a_d["holm_adjusted_p_value"] == (
        6.0 * 2.0 ** (1 - CASE_REPEAT_COUNT)
    )
    assert a_d["method"] == (
        "paired_mean_difference_exact_sign_flip_"
        "case_cluster_bootstrap_ci"
    )
    assert a_d["effect_direction"] == "model_a_higher"
    assert a_d["bootstrap_seed"] == 5005
    assert a_d["bootstrap_resamples"] == 128

    a_b_tokens = _comparison(
        first,
        metric="total_tokens",
        stratum_id="overall",
        model_a=MODEL_A,
        model_b=MODEL_B,
    )
    assert a_b_tokens["pairing_denominator"] == CASE_REPEAT_COUNT
    assert a_b_tokens["paired_sample_size"] == CASE_REPEAT_COUNT
    assert a_b_tokens["paired_difference"] == 10.0
    assert a_b_tokens["method"] == (
        "paired_median_difference_case_cluster_bootstrap_ci"
    )
    assert a_b_tokens["raw_p_value"] is None
    assert a_b_tokens["holm_adjusted_p_value"] is None
    assert a_b_tokens["p_value_applicability"] == (
        "not_preregistered_for_continuous_metric"
    )

    assert holm_adjust_p_values(
        (0.03125, 0.125, 0.25, 0.25, 0.5, 1.0)
    ) == (0.1875, 0.625, 1.0, 1.0, 1.0, 1.0)


def test_exp5_paired_statistics_disclose_metric_missingness() -> None:
    records = _case_records()
    target = next(
        row
        for row in records
        if row["case_id"] == FIRST_FACTOR_CASE
        and row["repeat_id"] == 0
        and row["cohort_member_id"] == MODEL_A
    )
    target["total_tokens"] = None
    target["total_tokens_unavailable_reason"] = "provider_usage_missing"

    rows = build_exp5_paired_comparison_rows(
        records,
        bootstrap_resamples=32,
    )
    comparison = _comparison(
        rows,
        metric="total_tokens",
        stratum_id="overall",
        model_a=MODEL_A,
        model_b=MODEL_B,
    )

    assert comparison["pairing_denominator"] == CASE_REPEAT_COUNT
    assert comparison["paired_sample_size"] == CASE_REPEAT_COUNT - 1
    assert comparison["missing_model_a_count"] == 1
    assert comparison["missing_model_b_count"] == 0
    assert comparison["missing_both_count"] == 0
    assert comparison["missingness_reasons"] == ["provider_usage_missing"]


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("duplicate", "duplicate case-repeat-model record"),
        ("orphan", "incomplete four-model pairing"),
        ("noneligible", "non-eligible case record"),
        ("overlap", "model arm windows overlap"),
    ),
)
def test_exp5_statistics_fail_closed_on_invalid_pairing_or_order(
    mutation: str,
    message: str,
) -> None:
    records = _case_records()
    if mutation == "duplicate":
        records.append(deepcopy(records[0]))
    elif mutation == "orphan":
        records.pop(0)
    elif mutation == "noneligible":
        records[0]["paper_eligible"] = False
    else:
        for row in records:
            if row["repeat_id"] == 0 and row["cohort_member_id"] == MODEL_B:
                for field_name in (
                    "condition_started_at",
                    "condition_ended_at",
                ):
                    timestamp = datetime.fromisoformat(str(row[field_name]))
                    row[field_name] = (timestamp - timedelta(minutes=10)).isoformat()

    with pytest.raises(ValueError, match=message):
        build_exp5_paired_comparison_rows(
            records,
            bootstrap_resamples=16,
        )


def test_exp5_order_sensitivity_is_secondary_and_audits_each_arm() -> None:
    rows = build_exp5_order_and_concurrency_rows(_case_records())

    arm_rows = [row for row in rows if row["row_scope"] == "arm_audit"]
    sensitivity_rows = [
        row for row in rows if row["row_scope"] == "order_sensitivity"
    ]
    assert len(arm_rows) == 12
    assert all(row["arm_overlap"] is False for row in arm_rows)
    assert all(row["global_peak_in_flight"] == 3 for row in arm_rows)
    assert all(row["global_capacity_compliant"] is True for row in arm_rows)
    assert len(sensitivity_rows) == 12
    assert all(row["analysis_role"] == "secondary" for row in sensitivity_rows)
    assert all(
        row["case_repeat_denominator"] == len(CASES)
        for row in sensitivity_rows
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("unknown_member", "pre-registered model cohort"),
        ("unknown_stratum", "tracked case-to-stratum mapping"),
        ("missing_stratum", "tracked case inventory"),
        ("alternate_order", "pre-registered model order"),
        ("fake_sequence_digest", "sequence plan digest"),
        ("missing_case_repeat", "all three repeats"),
    ),
)
def test_exp5_statistics_require_exact_preregistered_v3_identity(
    mutation: str,
    message: str,
) -> None:
    records = _case_records()
    if mutation == "unknown_member":
        for row in records:
            if row["cohort_member_id"] == MODEL_D:
                row["cohort_member_id"] = "unregistered_model"
            if row["predecessor_member_id"] == MODEL_D:
                row["predecessor_member_id"] = "unregistered_model"
    elif mutation == "unknown_stratum":
        records[0]["stratum_id"] = "lean_proof:hard:unknown_topic"
    elif mutation == "missing_stratum":
        records = [
            row
            for row in records
            if row["stratum_id"] != "lean_proof:hard:induction"
        ]
    elif mutation == "alternate_order":
        _assign_repeat_order(records, repeat_id=0, order=MODEL_ORDERS[1])
    elif mutation == "fake_sequence_digest":
        records[0]["sequence_plan_digest"] = "f" * 64
    else:
        records = [
            row
            for row in records
            if not (
                row["case_id"] == FIRST_INDUCTION_CASE
                and row["repeat_id"] == 2
            )
        ]

    with pytest.raises(ValueError, match=message):
        build_exp5_paired_comparison_rows(
            records,
            bootstrap_resamples=16,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("wrong_case_stratum", "tracked case-to-stratum mapping"),
        ("common_missing_case", "tracked case inventory"),
        ("fake_selection_digest", "selection digest"),
        ("fake_parent_catalog_digest", "parent catalog digest"),
    ),
)
def test_exp5_statistics_require_tracked_v3_selection_inventory(
    mutation: str,
    message: str,
) -> None:
    records = _case_records()
    if mutation == "wrong_case_stratum":
        wrong_stratum = next(
            stratum_id
            for stratum_id in EXP5_V3_STRATUM_ORDER
            if stratum_id != CASE_STRATUM[str(records[0]["case_id"])]
        )
        records[0]["stratum_id"] = wrong_stratum
    elif mutation == "common_missing_case":
        missing_case_id = str(records[0]["case_id"])
        records = [
            row for row in records if row["case_id"] != missing_case_id
        ]
    elif mutation == "fake_selection_digest":
        records[0]["exp5_selection_digest"] = "sha256:" + "0" * 64
    else:
        records[0]["exp5_selection_parent_catalog_digest"] = (
            "sha256:" + "0" * 64
        )

    with pytest.raises(ValueError, match=message):
        build_exp5_paired_comparison_rows(
            records,
            bootstrap_resamples=16,
        )


def test_exp5_statistics_reject_overlapping_conditions_within_model_arm() -> None:
    records = _case_records()
    target_model = MODEL_ORDERS[0][0]
    target_conditions = sorted(
        {
            str(row["condition_id"])
            for row in records
            if row["repeat_id"] == 0
            and row["cohort_member_id"] == target_model
        }
    )
    first_condition, second_condition = target_conditions[:2]
    first_start = next(
        str(row["condition_started_at"])
        for row in records
        if row["condition_id"] == first_condition
    )
    for row in records:
        if row["condition_id"] == second_condition:
            row["condition_started_at"] = first_start

    with pytest.raises(ValueError, match="condition windows overlap within model arm"):
        build_exp5_order_and_concurrency_rows(records)


def test_exp5_binary_p_values_are_inapplicable_without_paired_observations() -> None:
    records = _case_records()
    for row in records:
        if row["cohort_member_id"] in {MODEL_A, MODEL_B}:
            row["root_completed"] = None
            row["root_completed_unavailable_reason"] = "root_status_missing"

    rows = build_exp5_paired_comparison_rows(records, bootstrap_resamples=16)
    unavailable = _comparison(
        rows,
        metric="root_completion",
        stratum_id="overall",
        model_a=MODEL_A,
        model_b=MODEL_B,
    )
    applicable = _comparison(
        rows,
        metric="root_completion",
        stratum_id="overall",
        model_a=MODEL_C,
        model_b=MODEL_D,
    )

    assert unavailable["paired_sample_size"] == 0
    assert unavailable["raw_p_value"] is None
    assert unavailable["holm_adjusted_p_value"] is None
    assert unavailable["p_value_applicability"] == (
        "insufficient_paired_observations"
    )
    assert applicable["paired_sample_size"] == CASE_REPEAT_COUNT
    assert applicable["raw_p_value"] is not None
    assert applicable["holm_adjusted_p_value"] == applicable["raw_p_value"]


def test_exp5_binary_all_zero_differences_remain_applicable() -> None:
    records = _case_records()
    for row in records:
        row["root_completed"] = True

    rows = build_exp5_paired_comparison_rows(records, bootstrap_resamples=16)
    comparison = _comparison(
        rows,
        metric="root_completion",
        stratum_id="overall",
        model_a=MODEL_A,
        model_b=MODEL_B,
    )

    assert comparison["paired_sample_size"] == CASE_REPEAT_COUNT
    assert comparison["raw_p_value"] == 1.0
    assert comparison["holm_adjusted_p_value"] == 1.0
    assert comparison["p_value_applicability"] == "applicable"


def _case_records() -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    token_offset = {
        MODEL_A: 30,
        MODEL_B: 20,
        MODEL_C: 10,
        MODEL_D: 0,
    }
    origin = datetime(2026, 7, 30, tzinfo=timezone.utc)
    for repeat_id, order in MODEL_ORDERS.items():
        predecessor: str | None = None
        for order_slot, model_id in enumerate(order, start=1):
            arm_start = origin + timedelta(
                days=repeat_id,
                minutes=order_slot * 10,
            )
            for case_index, (case_id, stratum_id) in enumerate(CASES):
                metric_index = repeat_id * len(CASES) + case_index
                stratum_index = EXP5_V3_STRATUM_ORDER.index(stratum_id)
                condition_start = arm_start + timedelta(seconds=stratum_index * 2)
                condition_end = condition_start + timedelta(seconds=1)
                records.append(
                    {
                        "case_id": case_id,
                        "repeat_id": repeat_id,
                        "cohort_member_id": model_id,
                        "condition_id": (
                            f"r{repeat_id}-{model_id}-{stratum_id}"
                        ),
                        "stratum_id": stratum_id,
                        "root_completed": _completion(model_id, metric_index),
                        "accepted_validity": _validity(model_id, metric_index),
                        "total_tokens": 100
                        + metric_index
                        + token_offset[model_id],
                        "provider_latency_ms": 1_000
                        + metric_index
                        + token_offset[model_id],
                        "paper_eligible": True,
                        "order_slot": order_slot,
                        "predecessor_member_id": predecessor,
                        "sequence_plan_digest": EXP5_V3_SEQUENCE_PLAN_DIGEST,
                        "exp5_selection_digest": EXP5_SELECTION_DIGEST,
                        "exp5_selection_parent_catalog_digest": (
                            EXP5_SELECTION_PARENT_CATALOG_DIGEST
                        ),
                        "condition_started_at": condition_start.isoformat(),
                        "condition_ended_at": condition_end.isoformat(),
                        "observed_peak_concurrency": 3,
                    }
                )
            predecessor = model_id
    return records


def _completion(model_id: str, metric_index: int) -> bool:
    if model_id == MODEL_A:
        return True
    if model_id == MODEL_B:
        return metric_index % 2 == 0
    if model_id == MODEL_C:
        return metric_index % 3 == 0
    return False


def _validity(model_id: str, metric_index: int) -> bool:
    divisor = {
        MODEL_A: 5,
        MODEL_B: 3,
        MODEL_C: 4,
        MODEL_D: CASE_REPEAT_COUNT + 1,
    }[model_id]
    return model_id != MODEL_D and metric_index % divisor != 0


def _assign_repeat_order(
    records: list[dict[str, object]],
    *,
    repeat_id: int,
    order: tuple[str, ...],
) -> None:
    origin = datetime(2026, 7, 30, tzinfo=timezone.utc)
    for row in records:
        if row["repeat_id"] != repeat_id:
            continue
        model_id = str(row["cohort_member_id"])
        order_slot = order.index(model_id) + 1
        stratum_index = EXP5_V3_STRATUM_ORDER.index(str(row["stratum_id"]))
        condition_start = origin + timedelta(
            days=repeat_id,
            minutes=order_slot * 10,
            seconds=stratum_index * 2,
        )
        row["order_slot"] = order_slot
        row["predecessor_member_id"] = (
            order[order_slot - 2] if order_slot > 1 else None
        )
        row["condition_started_at"] = condition_start.isoformat()
        row["condition_ended_at"] = (
            condition_start + timedelta(seconds=1)
        ).isoformat()


def _comparison(
    rows: tuple[dict[str, object], ...],
    *,
    metric: str,
    stratum_id: str,
    model_a: str,
    model_b: str,
) -> dict[str, object]:
    return next(
        row
        for row in rows
        if row["metric"] == metric
        and row["stratum_id"] == stratum_id
        and row["model_a"] == model_a
        and row["model_b"] == model_b
    )
