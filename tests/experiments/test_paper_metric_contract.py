from __future__ import annotations

import copy
import dataclasses
import inspect
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from tokenshare.experiments.paper_metric_contract import (
    DEFAULT_PAPER_METRIC_CONTRACT_PATH,
    MembershipStatus,
    MetricObservationBundle,
    PAPER_METRIC_CONTRACT_SCHEMA_VERSION,
    PredicateTruth,
    VerifiedMetricObservation,
    compute_metric_contract_digest,
    evaluate_invariant,
    evaluate_membership,
    load_paper_metric_contract,
    recompute_metric,
)
from tokenshare.experiments.paper_pipeline_profile import load_paper_pipeline_profile


COUNT = "count_nonnegative_integer"
PROPORTION = "proportion"
NONNEGATIVE = "nonnegative_number"
ONLINE_ROLES = (
    "request_body",
    "raw_output_or_provider_failure",
    "provenance",
    "usage_status",
    "latency",
    "pricing",
    "provider_attempt",
    "model_record",
)
TRACE_ROLES = (
    "request_body",
    "raw_output_or_provider_failure",
    "provenance",
    "usage_status",
    "latency",
    "pricing",
    "acquisition_attempt",
    "model_record",
)
ROW_IDENTITY_DIGEST = "sha256:" + "1" * 64


def test_tracked_contract_loads_against_current_pipeline_profile() -> None:
    contract = load_paper_metric_contract()
    profile = load_paper_pipeline_profile()

    assert contract.pipeline_profile_digest == profile.profile_digest


def test_exp1_contract_freezes_online_and_trace_role_routes() -> None:
    contract = load_paper_metric_contract()
    table = contract.require_table("exp1_feasibility")
    metric = contract.require_metric("exp1_feasibility", "completion_rate")

    assert table.evidence_classes == (
        "online_real_provider",
        "real_model_trace_protocol_run",
    )
    assert metric.evidence_classes == table.evidence_classes
    assert metric.required_current_provider_roles == ()
    assert metric.required_source_bank_roles == ()
    assert metric.required_roles_for("online_real_provider") == (
        ONLINE_ROLES,
        (),
    )
    assert metric.required_roles_for("real_model_trace_protocol_run") == (
        (),
        TRACE_ROLES,
    )
    with pytest.raises(ValueError, match="unsupported metric evidence class"):
        metric.required_roles_for("unknown_evidence")


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    (
        ("route_role_drift", "evidence role route matrix drift"),
        ("metric_class_drift", "metric evidence class differs from table"),
        ("flat_role_reintroduced", "mixed evidence role matrix drift"),
        ("unknown_table_class", "unsupported evidence class for table"),
    ),
)
def test_exp1_mixed_evidence_contract_mutations_fail_closed(
    tmp_path: Path,
    mutation: str,
    expected_error: str,
) -> None:
    body = _contract_body()
    table = next(
        item for item in body["tables"] if item["table_id"] == "exp1_feasibility"
    )
    metric = next(
        item
        for item in body["metrics"]
        if item["table"] == "exp1_feasibility"
        and item["metric_id"] == "completion_rate"
    )
    if mutation == "route_role_drift":
        table["evidence_role_routes"][1]["required_source_bank_roles"].pop()
    elif mutation == "metric_class_drift":
        metric["evidence_classes"] = ["online_real_provider"]
    elif mutation == "flat_role_reintroduced":
        metric["required_current_provider_roles"] = list(ONLINE_ROLES)
    else:
        table["evidence_classes"].append("unknown_evidence")
    _reseal(body)
    path = tmp_path / f"{mutation}.json"
    _write(path, body)

    with pytest.raises(ValueError, match=expected_error):
        load_paper_metric_contract(path)


EXPECTED_TABLE_FIELDS = {
    "exp1_feasibility": (
        "preregistered_root_count",
        "final_result_root_count",
        "verified_correct_root_count",
        "completion_rate",
        "end_to_end_verified_success_rate",
        "no_final_failure_count",
        "incorrect_final_failure_count",
        "infra_invalid_failure_count",
        "failure_root_count",
        "actual_end_to_end_wall_clock_ms",
        "actual_provider_latency_ms",
        "actual_total_tokens",
        "actual_cost_estimate_cny",
    ),
    "exp2_trace_scalability": (
        "preregistered_root_count",
        "final_result_root_count",
        "verified_correct_root_count",
        "completion_rate",
        "end_to_end_verified_success_rate",
        "trace_replay_wall_clock_ms",
        "bank_slot_consumption",
        "trace_attributed_tokens",
        "trace_attributed_cost",
        "trace_replay_paired_speedup",
        "trace_replay_parallel_efficiency",
        "paired_trace_token_multiplier",
        "paired_trace_cost_multiplier",
        "paired_speedup_planned_pair_count",
        "paired_speedup_eligible_pair_count",
        "paired_speedup_ineligible_pair_count",
        "trace_replay_paired_speedup_median",
        "trace_replay_paired_speedup_repeat_min",
        "trace_replay_paired_speedup_repeat_max",
        "trace_replay_paired_speedup_relative_difference",
        "planned_ai_unit_count",
        "executed_ai_unit_count",
        "unscheduled_ai_unit_count",
        "in_flight_at_witness",
        "observed_peak_concurrency",
        "worker_utilization",
    ),
    "exp2_online_concurrency": (
        "preregistered_root_count",
        "final_result_root_count",
        "verified_correct_root_count",
        "completion_rate",
        "end_to_end_verified_success_rate",
        "actual_first_provider_attempt_count",
        "actual_total_tokens",
        "actual_cost_estimate_cny",
        "actual_end_to_end_wall_clock_ms",
        "actual_provider_latency_ms",
        "provider_429_or_timeout_union_count",
        "provider_429_or_timeout_union_fraction",
    ),
    "exp3_trace_robustness": (
        "preregistered_root_count",
        "final_result_root_count",
        "verified_correct_root_count",
        "completion_rate",
        "end_to_end_verified_success_rate",
        "controlled_wrong_candidate_count",
        "controlled_wrong_candidate_interception_count",
        "controlled_wrong_candidate_interception_rate",
        "controlled_wrong_candidate_escape_count",
        "controlled_wrong_candidate_escape_rate",
        "started_replacement_attempt_count",
        "successful_replacement_attempt_count",
        "replacement_attempt_success_rate",
        "reassignment_count",
        "discarded_trace_tokens",
        "trace_replay_wall_clock_overhead_ms",
        "trace_attributed_token_overhead",
        "trace_attributed_cost_overhead",
        "kill_progress_error_pp",
        "kill_progress_error_signed_mean_pp",
        "kill_progress_error_signed_max_pp",
        "recovered_valid_canonical_required_slots",
        "preregistered_required_slots",
        "result_completeness_rate",
    ),
    "exp3_online_recovery": (
        "preregistered_root_count",
        "final_result_root_count",
        "verified_correct_root_count",
        "completion_rate",
        "end_to_end_verified_success_rate",
        "replacement_chain_count",
        "worker_death_reassignment_chain_count",
        "actual_provider_calls",
        "actual_prompt_tokens",
        "actual_completion_tokens",
        "actual_total_tokens",
        "actual_cost_estimate_cny",
        "wasted_actual_tokens",
    ),
    "exp4_ablation": (
        "preregistered_root_count",
        "final_result_root_count",
        "verified_correct_root_count",
        "completion_rate",
        "end_to_end_verified_success_rate",
        "paired_root_count",
        "full_success_ablation_success",
        "full_success_ablation_failure",
        "full_failure_ablation_success",
        "full_failure_ablation_failure",
        "end_to_end_success_loss_vs_full",
        "completion_loss_vs_full",
        "trace_replay_wall_clock_delta_vs_full",
        "trace_attributed_token_delta_vs_full",
        "trace_attributed_cost_delta_vs_full",
        "independently_labeled_invalid_candidate_count",
        "wrong_canonical_acceptance_count",
        "wrong_canonical_acceptance_rate",
        "raw_only_exposure_count",
        "raw_only_acceptance_count",
        "raw_only_acceptance_rate",
        "stuck_task_count",
        "stuck_task_rate",
        "premature_merge_attempt_count",
        "premature_merge_failure_count",
        "premature_merge_failure_rate",
    ),
    "exp5_quality": (
        "preregistered_root_count",
        "final_result_root_count",
        "verified_correct_root_count",
        "completion_rate",
        "end_to_end_verified_success_rate",
        "actual_first_provider_attempt_count",
        "first_attempt_without_verifier_accepted_candidate_count",
        "first_attempt_nonpass_rate",
        "first_attempt_provider_transport_failure_count",
        "first_attempt_parse_schema_unusable_count",
        "first_attempt_verification_checker_rejection_count",
        "first_attempt_checkable_candidate_count",
        "first_attempt_explicitly_rejected_by_verifier_count",
        "first_attempt_verification_rejection_rate",
    ),
    "exp5_resources": (
        "planned_first_attempt_ai_unit_count",
        "actual_first_provider_attempt_count",
        "first_attempt_call_coverage",
        "actual_total_tokens",
        "actual_cost_estimate_cny",
        "repeat0_wall_clock_ms",
        "repeat1_wall_clock_ms",
        "repeat2_wall_clock_ms",
        "model_wall_clock_median_ms",
        "model_wall_clock_min_ms",
        "model_wall_clock_max_ms",
        "model_wall_clock_range_ms",
    ),
}

ALL_NUMERIC_CELLS = tuple(
    (table_id, metric_id)
    for table_id, metric_ids in EXPECTED_TABLE_FIELDS.items()
    for metric_id in metric_ids
)

# 独立冻结的论文指标语义黄金矩阵；测试只把合同投影与此常量逐项比较。
GOLDEN_SOURCE_GROUPS = """
actual_provider_attempt|exp1_feasibility.actual_provider_latency_ms exp1_feasibility.actual_total_tokens exp1_feasibility.actual_cost_estimate_cny
all_preregistered_roots|exp1_feasibility.actual_end_to_end_wall_clock_ms exp2_online_concurrency.actual_end_to_end_wall_clock_ms
exp1_failure_no_final|exp1_feasibility.no_final_failure_count
exp1_failure_incorrect_final|exp1_feasibility.incorrect_final_failure_count
exp1_failure_infra_invalid|exp1_feasibility.infra_invalid_failure_count
exp1_failure_root|exp1_feasibility.failure_root_count
exp2_committed_trace_consumption|exp2_trace_scalability.bank_slot_consumption exp2_trace_scalability.trace_attributed_tokens exp2_trace_scalability.trace_attributed_cost
exp2_speedup_eligible_pair|exp2_trace_scalability.trace_replay_paired_speedup exp2_trace_scalability.trace_replay_parallel_efficiency exp2_trace_scalability.paired_speedup_eligible_pair_count exp2_trace_scalability.trace_replay_paired_speedup_median
exp2_resource_multiplier_eligible_pair|exp2_trace_scalability.paired_trace_token_multiplier exp2_trace_scalability.paired_trace_cost_multiplier
exp2_planned_pair|exp2_trace_scalability.paired_speedup_planned_pair_count
exp2_speedup_ineligible_pair|exp2_trace_scalability.paired_speedup_ineligible_pair_count
exp2_repeat_speedup_summary|exp2_trace_scalability.trace_replay_paired_speedup_repeat_min exp2_trace_scalability.trace_replay_paired_speedup_repeat_max exp2_trace_scalability.trace_replay_paired_speedup_relative_difference
exp2_planned_ai_unit|exp2_trace_scalability.planned_ai_unit_count
exp2_executed_ai_unit|exp2_trace_scalability.executed_ai_unit_count exp2_trace_scalability.worker_utilization
exp2_unscheduled_ai_unit|exp2_trace_scalability.unscheduled_ai_unit_count
exp2_online_actual_first_attempt|exp2_online_concurrency.actual_first_provider_attempt_count exp2_online_concurrency.actual_total_tokens exp2_online_concurrency.actual_cost_estimate_cny exp2_online_concurrency.actual_provider_latency_ms
exp2_first_attempt_429_or_timeout_union|exp2_online_concurrency.provider_429_or_timeout_union_count exp2_online_concurrency.provider_429_or_timeout_union_fraction
exp3_controlled_wrong_candidate|exp3_trace_robustness.controlled_wrong_candidate_count
exp3_controlled_wrong_candidate_interception|exp3_trace_robustness.controlled_wrong_candidate_interception_count
exp3_controlled_wrong_candidate_escape|exp3_trace_robustness.controlled_wrong_candidate_escape_count
exp3_replacement_attempt_started|exp3_trace_robustness.started_replacement_attempt_count
exp3_replacement_attempt_successful|exp3_trace_robustness.successful_replacement_attempt_count
exp3_reassignment|exp3_trace_robustness.reassignment_count
exp3_discarded_trace_consumption|exp3_trace_robustness.discarded_trace_tokens
exp3_trace_pair_complete|exp3_trace_robustness.trace_replay_wall_clock_overhead_ms exp3_trace_robustness.trace_attributed_token_overhead exp3_trace_robustness.trace_attributed_cost_overhead
exp3_worker_death_progress|exp3_trace_robustness.kill_progress_error_pp exp3_trace_robustness.kill_progress_error_signed_mean_pp exp3_trace_robustness.kill_progress_error_signed_max_pp
exp3_recovered_required_slot|exp3_trace_robustness.recovered_valid_canonical_required_slots
exp3_preregistered_required_slot|exp3_trace_robustness.preregistered_required_slots
exp3_online_validation_replacement_chain|exp3_online_recovery.replacement_chain_count
exp3_online_worker_death_requeue_chain|exp3_online_recovery.worker_death_reassignment_chain_count
exp3_online_actual_provider_attempt|exp3_online_recovery.actual_provider_calls exp3_online_recovery.actual_prompt_tokens exp3_online_recovery.actual_completion_tokens exp3_online_recovery.actual_total_tokens exp3_online_recovery.actual_cost_estimate_cny
exp3_online_wasted_actual_tokens|exp3_online_recovery.wasted_actual_tokens
exp4_complete_pair|exp4_ablation.paired_root_count exp4_ablation.end_to_end_success_loss_vs_full exp4_ablation.completion_loss_vs_full exp4_ablation.trace_replay_wall_clock_delta_vs_full exp4_ablation.trace_attributed_token_delta_vs_full exp4_ablation.trace_attributed_cost_delta_vs_full
exp4_full_success_ablation_success|exp4_ablation.full_success_ablation_success
exp4_full_success_ablation_failure|exp4_ablation.full_success_ablation_failure
exp4_full_failure_ablation_success|exp4_ablation.full_failure_ablation_success
exp4_full_failure_ablation_failure|exp4_ablation.full_failure_ablation_failure
exp4_independently_labeled_invalid_candidate|exp4_ablation.independently_labeled_invalid_candidate_count
exp4_wrong_canonical_acceptance|exp4_ablation.wrong_canonical_acceptance_count
exp4_raw_only_exposure|exp4_ablation.raw_only_exposure_count
exp4_raw_only_acceptance|exp4_ablation.raw_only_acceptance_count
exp4_stuck_by_disabled_requeue|exp4_ablation.stuck_task_count
exp4_mode_preregistered_root|exp4_ablation.stuck_task_rate
exp4_premature_merge_attempt|exp4_ablation.premature_merge_attempt_count
exp4_premature_merge_failure|exp4_ablation.premature_merge_failure_count
exp5_actual_provider_attempt|exp5_quality.actual_first_provider_attempt_count exp5_resources.actual_first_provider_attempt_count exp5_resources.actual_total_tokens exp5_resources.actual_cost_estimate_cny
exp5_first_attempt_nonpass|exp5_quality.first_attempt_without_verifier_accepted_candidate_count
exp5_nonpass_provider_transport_failure|exp5_quality.first_attempt_provider_transport_failure_count
exp5_nonpass_parse_schema_unusable|exp5_quality.first_attempt_parse_schema_unusable_count
exp5_nonpass_verification_checker_rejection|exp5_quality.first_attempt_verification_checker_rejection_count
exp5_first_attempt_checkable|exp5_quality.first_attempt_checkable_candidate_count
exp5_first_attempt_explicit_verifier_rejection|exp5_quality.first_attempt_explicitly_rejected_by_verifier_count
exp5_planned_ai_unit|exp5_resources.planned_first_attempt_ai_unit_count
exp5_repeat0_roots|exp5_resources.repeat0_wall_clock_ms
exp5_repeat1_roots|exp5_resources.repeat1_wall_clock_ms
exp5_repeat2_roots|exp5_resources.repeat2_wall_clock_ms
""".strip()

GOLDEN_NONCOUNT_FORMULAS = """
exp1_feasibility.actual_end_to_end_wall_clock_ms|enclosing_elapsed_condition_start_to_all_roots_terminal_v1|elapsed|number_sequence:members.root_terminal_at_ms,number_sequence:members.root_start_at_ms|nonnegative_number
exp1_feasibility.actual_provider_latency_ms|sum_complete_members_v1|sum|number_sequence:members.provider_latency_ms|nonnegative_number
exp1_feasibility.actual_total_tokens|sum_complete_members_v1|sum|number_sequence:members.total_tokens|nonnegative_number
exp1_feasibility.actual_cost_estimate_cny|sum_complete_members_v1|sum|number_sequence:members.cost_estimate_cny|nonnegative_number
exp2_trace_scalability.trace_replay_wall_clock_ms|direct_observation_value_v1|direct|number:row.runtime_elapsed_ms|nonnegative_number
exp2_trace_scalability.bank_slot_consumption|count_committed_trace_consumptions_v1|count|member_set:members|count_nonnegative_integer
exp2_trace_scalability.trace_attributed_tokens|sum_source_usage_per_committed_consumption_v1|sum|number_sequence:members.source_usage_total_tokens|nonnegative_number
exp2_trace_scalability.trace_attributed_cost|sum_source_cost_per_committed_consumption_v1|sum|number_sequence:members.source_cost_estimate_cny|nonnegative_number
exp2_trace_scalability.trace_replay_paired_speedup|paired_ratio_worker1_over_workerk_v1|ratio|number_sequence:members.baseline_trace_replay_wall_clock_ms,number_sequence:members.compared_trace_replay_wall_clock_ms|ratio_unbounded
exp2_trace_scalability.trace_replay_parallel_efficiency|ratio_paired_speedup_over_configured_workers_v1|parallel_efficiency|number_sequence:members.baseline_trace_replay_wall_clock_ms,number_sequence:members.compared_trace_replay_wall_clock_ms,number_sequence:members.compared_worker_count|nonnegative_number
exp2_trace_scalability.paired_trace_token_multiplier|paired_ratio_workerk_over_worker1_v1|ratio|number_sequence:members.compared_trace_attributed_tokens,number_sequence:members.baseline_trace_attributed_tokens|ratio_unbounded
exp2_trace_scalability.paired_trace_cost_multiplier|paired_ratio_workerk_over_worker1_v1|ratio|number_sequence:members.compared_trace_attributed_cost,number_sequence:members.baseline_trace_attributed_cost|ratio_unbounded
exp2_trace_scalability.trace_replay_paired_speedup_median|median_eligible_pairs_within_repeat_v1|median_pairwise_ratio|number_sequence:members.baseline_trace_replay_wall_clock_ms,number_sequence:members.compared_trace_replay_wall_clock_ms|ratio_unbounded
exp2_trace_scalability.trace_replay_paired_speedup_repeat_min|minimum_of_two_raw_repeat_summaries_v1|minimum|number_sequence:members.repeat_speedup_value|ratio_unbounded
exp2_trace_scalability.trace_replay_paired_speedup_repeat_max|maximum_of_two_raw_repeat_summaries_v1|maximum|number_sequence:members.repeat_speedup_value|ratio_unbounded
exp2_trace_scalability.trace_replay_paired_speedup_relative_difference|relative_difference_of_two_repeat_summaries_v1|relative_range|number_sequence:members.repeat_speedup_value|ratio_unbounded
exp2_trace_scalability.in_flight_at_witness|direct_nonnegative_integer_v1|direct|nonnegative_integer:row.witness_in_flight_count|count_nonnegative_integer
exp2_trace_scalability.observed_peak_concurrency|direct_nonnegative_integer_v1|direct|nonnegative_integer:row.scheduler_peak_active_count|count_nonnegative_integer
exp2_trace_scalability.worker_utilization|ratio_executed_worker_time_over_capacity_v1|worker_utilization|number_sequence:members.busy_worker_time_ms,nonnegative_integer:row.configured_worker_count,number:observation.exp2_trace_scalability.trace_replay_wall_clock_ms|proportion
exp2_online_concurrency.actual_total_tokens|sum_complete_members_v1|sum|number_sequence:members.actual_total_tokens|nonnegative_number
exp2_online_concurrency.actual_cost_estimate_cny|sum_complete_members_v1|sum|number_sequence:members.actual_cost_estimate_cny|nonnegative_number
exp2_online_concurrency.actual_end_to_end_wall_clock_ms|enclosing_elapsed_first_dispatch_to_all_roots_terminal_v1|elapsed|number_sequence:members.root_terminal_at_ms,number_sequence:members.protocol_first_dispatch_at_ms|nonnegative_number
exp2_online_concurrency.actual_provider_latency_ms|sum_complete_members_v1|sum|number_sequence:members.provider_latency_ms|nonnegative_number
exp2_online_concurrency.provider_429_or_timeout_union_fraction|ratio_v1|ratio|number:observation.exp2_online_concurrency.provider_429_or_timeout_union_count,number:observation.exp2_online_concurrency.actual_first_provider_attempt_count|proportion
exp3_trace_robustness.controlled_wrong_candidate_interception_rate|ratio_v1|ratio|number:observation.exp3_trace_robustness.controlled_wrong_candidate_interception_count,number:observation.exp3_trace_robustness.controlled_wrong_candidate_count|proportion
exp3_trace_robustness.controlled_wrong_candidate_escape_rate|ratio_v1|ratio|number:observation.exp3_trace_robustness.controlled_wrong_candidate_escape_count,number:observation.exp3_trace_robustness.controlled_wrong_candidate_count|proportion
exp3_trace_robustness.replacement_attempt_success_rate|ratio_v1|ratio|number:observation.exp3_trace_robustness.successful_replacement_attempt_count,number:observation.exp3_trace_robustness.started_replacement_attempt_count|proportion
exp3_trace_robustness.discarded_trace_tokens|sum_source_usage_for_fault_discarded_consumptions_v1|sum|number_sequence:members.source_usage_total_tokens|nonnegative_number
exp3_trace_robustness.trace_replay_wall_clock_overhead_ms|absolute_difference_fault_minus_reference_v1|subtract|number_sequence:members.fault_trace_replay_wall_clock_ms,number_sequence:members.reference_trace_replay_wall_clock_ms|signed_number
exp3_trace_robustness.trace_attributed_token_overhead|absolute_difference_fault_minus_reference_v1|subtract|number_sequence:members.fault_trace_attributed_tokens,number_sequence:members.reference_trace_attributed_tokens|signed_number
exp3_trace_robustness.trace_attributed_cost_overhead|absolute_difference_fault_minus_reference_v1|subtract|number_sequence:members.fault_trace_attributed_cost,number_sequence:members.reference_trace_attributed_cost|signed_number
exp3_trace_robustness.kill_progress_error_pp|signed_percentage_point_error_actual_minus_target_v1|percentage_point_delta|number_sequence:members.actual_kill_progress_ratio,number_sequence:members.target_kill_progress_ratio|signed_number
exp3_trace_robustness.kill_progress_error_signed_mean_pp|signed_mean_per_death_v1|signed_mean_percentage_point_delta|number_sequence:members.actual_kill_progress_ratio,number_sequence:members.target_kill_progress_ratio|signed_number
exp3_trace_robustness.kill_progress_error_signed_max_pp|signed_max_per_death_v1|signed_max_percentage_point_delta|number_sequence:members.actual_kill_progress_ratio,number_sequence:members.target_kill_progress_ratio|signed_number
exp3_trace_robustness.result_completeness_rate|ratio_v1|ratio|number:observation.exp3_trace_robustness.recovered_valid_canonical_required_slots,number:observation.exp3_trace_robustness.preregistered_required_slots|proportion
exp3_online_recovery.actual_prompt_tokens|sum_complete_members_v1|sum|number_sequence:members.actual_prompt_tokens|nonnegative_number
exp3_online_recovery.actual_completion_tokens|sum_complete_members_v1|sum|number_sequence:members.actual_completion_tokens|nonnegative_number
exp3_online_recovery.actual_total_tokens|sum_complete_members_v1|sum|number_sequence:members.actual_total_tokens|nonnegative_number
exp3_online_recovery.actual_cost_estimate_cny|sum_complete_members_v1|sum|number_sequence:members.actual_cost_estimate_cny|nonnegative_number
exp3_online_recovery.wasted_actual_tokens|sum_complete_members_v1|sum|number_sequence:members.original_attempt_total_tokens|nonnegative_number
exp4_ablation.end_to_end_success_loss_vs_full|absolute_difference_full_minus_ablation_v1|subtract|number_sequence:members.full_end_to_end_verified_success_rate,number_sequence:members.ablation_end_to_end_verified_success_rate|signed_number
exp4_ablation.completion_loss_vs_full|absolute_difference_full_minus_ablation_v1|subtract|number_sequence:members.full_completion_rate,number_sequence:members.ablation_completion_rate|signed_number
exp4_ablation.trace_replay_wall_clock_delta_vs_full|absolute_difference_ablation_minus_full_v1|subtract|number_sequence:members.ablation_trace_replay_wall_clock_ms,number_sequence:members.full_trace_replay_wall_clock_ms|signed_number
exp4_ablation.trace_attributed_token_delta_vs_full|absolute_difference_ablation_minus_full_v1|subtract|number_sequence:members.ablation_trace_attributed_tokens,number_sequence:members.full_trace_attributed_tokens|signed_number
exp4_ablation.trace_attributed_cost_delta_vs_full|absolute_difference_ablation_minus_full_v1|subtract|number_sequence:members.ablation_trace_attributed_cost,number_sequence:members.full_trace_attributed_cost|signed_number
exp4_ablation.wrong_canonical_acceptance_rate|ratio_v1|ratio|number:observation.exp4_ablation.wrong_canonical_acceptance_count,number:observation.exp4_ablation.independently_labeled_invalid_candidate_count|proportion
exp4_ablation.raw_only_acceptance_rate|ratio_v1|ratio|number:observation.exp4_ablation.raw_only_acceptance_count,number:observation.exp4_ablation.raw_only_exposure_count|proportion
exp4_ablation.stuck_task_rate|ratio_number_over_member_count_v1|ratio|number:observation.exp4_ablation.stuck_task_count,member_set:members|proportion
exp4_ablation.premature_merge_failure_rate|ratio_v1|ratio|number:observation.exp4_ablation.premature_merge_failure_count,number:observation.exp4_ablation.premature_merge_attempt_count|proportion
exp5_quality.first_attempt_nonpass_rate|ratio_v1|ratio|number:observation.exp5_quality.first_attempt_without_verifier_accepted_candidate_count,number:observation.exp5_quality.actual_first_provider_attempt_count|proportion
exp5_quality.first_attempt_verification_rejection_rate|ratio_v1|ratio|number:observation.exp5_quality.first_attempt_explicitly_rejected_by_verifier_count,number:observation.exp5_quality.first_attempt_checkable_candidate_count|proportion
exp5_resources.first_attempt_call_coverage|ratio_v1|ratio|number:observation.exp5_resources.actual_first_provider_attempt_count,number:observation.exp5_resources.planned_first_attempt_ai_unit_count|proportion
exp5_resources.actual_total_tokens|sum_complete_members_v1|sum|number_sequence:members.actual_total_tokens|nonnegative_number
exp5_resources.actual_cost_estimate_cny|sum_complete_members_v1|sum|number_sequence:members.actual_cost_estimate_cny|nonnegative_number
exp5_resources.repeat0_wall_clock_ms|enclosing_elapsed_first_dispatch_to_all_roots_terminal_v1|elapsed|number_sequence:members.root_terminal_at_ms,number_sequence:members.protocol_first_dispatch_at_ms|nonnegative_number
exp5_resources.repeat1_wall_clock_ms|enclosing_elapsed_first_dispatch_to_all_roots_terminal_v1|elapsed|number_sequence:members.root_terminal_at_ms,number_sequence:members.protocol_first_dispatch_at_ms|nonnegative_number
exp5_resources.repeat2_wall_clock_ms|enclosing_elapsed_first_dispatch_to_all_roots_terminal_v1|elapsed|number_sequence:members.root_terminal_at_ms,number_sequence:members.protocol_first_dispatch_at_ms|nonnegative_number
exp5_resources.model_wall_clock_median_ms|median_of_three_raw_repeats_v1|median|number:observation.exp5_resources.repeat0_wall_clock_ms,number:observation.exp5_resources.repeat1_wall_clock_ms,number:observation.exp5_resources.repeat2_wall_clock_ms|nonnegative_number
exp5_resources.model_wall_clock_min_ms|minimum_of_three_raw_repeats_v1|minimum|number:observation.exp5_resources.repeat0_wall_clock_ms,number:observation.exp5_resources.repeat1_wall_clock_ms,number:observation.exp5_resources.repeat2_wall_clock_ms|nonnegative_number
exp5_resources.model_wall_clock_max_ms|maximum_of_three_raw_repeats_v1|maximum|number:observation.exp5_resources.repeat0_wall_clock_ms,number:observation.exp5_resources.repeat1_wall_clock_ms,number:observation.exp5_resources.repeat2_wall_clock_ms|nonnegative_number
exp5_resources.model_wall_clock_range_ms|max_minus_min_of_three_raw_repeats_v1|range|number:observation.exp5_resources.repeat0_wall_clock_ms,number:observation.exp5_resources.repeat1_wall_clock_ms,number:observation.exp5_resources.repeat2_wall_clock_ms|nonnegative_number
""".strip()

GOLDEN_NONDEFAULT_GATES = """
exp2_paired_worker_row|exp2_trace_scalability.trace_replay_paired_speedup exp2_trace_scalability.trace_replay_parallel_efficiency exp2_trace_scalability.paired_trace_token_multiplier exp2_trace_scalability.paired_trace_cost_multiplier
exp2_repeat_summary_row|exp2_trace_scalability.paired_speedup_planned_pair_count exp2_trace_scalability.paired_speedup_eligible_pair_count exp2_trace_scalability.paired_speedup_ineligible_pair_count exp2_trace_scalability.trace_replay_paired_speedup_median
exp4_all_five_mode_summary_row|exp4_ablation.preregistered_root_count exp4_ablation.final_result_root_count exp4_ablation.verified_correct_root_count exp4_ablation.completion_rate exp4_ablation.end_to_end_verified_success_rate
exp4_paired_transition_row|exp4_ablation.paired_root_count exp4_ablation.full_success_ablation_success exp4_ablation.full_success_ablation_failure exp4_ablation.full_failure_ablation_success exp4_ablation.full_failure_ablation_failure exp4_ablation.end_to_end_success_loss_vs_full exp4_ablation.completion_loss_vs_full exp4_ablation.trace_replay_wall_clock_delta_vs_full exp4_ablation.trace_attributed_token_delta_vs_full exp4_ablation.trace_attributed_cost_delta_vs_full
exp4_mode_no_verification_row|exp4_ablation.independently_labeled_invalid_candidate_count exp4_ablation.wrong_canonical_acceptance_count exp4_ablation.wrong_canonical_acceptance_rate
exp4_mode_no_parser_policy_row|exp4_ablation.raw_only_exposure_count exp4_ablation.raw_only_acceptance_count exp4_ablation.raw_only_acceptance_rate
exp4_mode_no_requeue_row|exp4_ablation.stuck_task_count exp4_ablation.stuck_task_rate
exp4_mode_no_merge_gate_row|exp4_ablation.premature_merge_attempt_count exp4_ablation.premature_merge_failure_count exp4_ablation.premature_merge_failure_rate
exp5_model_summary_row|exp5_quality.preregistered_root_count exp5_quality.final_result_root_count exp5_quality.verified_correct_root_count exp5_quality.completion_rate exp5_quality.end_to_end_verified_success_rate exp5_quality.actual_first_provider_attempt_count exp5_quality.first_attempt_without_verifier_accepted_candidate_count exp5_quality.first_attempt_nonpass_rate exp5_quality.first_attempt_provider_transport_failure_count exp5_quality.first_attempt_parse_schema_unusable_count exp5_quality.first_attempt_verification_checker_rejection_count exp5_quality.first_attempt_checkable_candidate_count exp5_quality.first_attempt_explicitly_rejected_by_verifier_count exp5_quality.first_attempt_verification_rejection_rate exp5_resources.planned_first_attempt_ai_unit_count exp5_resources.actual_first_provider_attempt_count exp5_resources.first_attempt_call_coverage exp5_resources.actual_total_tokens exp5_resources.actual_cost_estimate_cny exp5_resources.repeat0_wall_clock_ms exp5_resources.repeat1_wall_clock_ms exp5_resources.repeat2_wall_clock_ms exp5_resources.model_wall_clock_median_ms exp5_resources.model_wall_clock_min_ms exp5_resources.model_wall_clock_max_ms exp5_resources.model_wall_clock_range_ms
""".strip()

GOLDEN_FALSE_POSITIVE_METRICS = frozenset(
    {
        "exp3_trace_robustness.controlled_wrong_candidate_count",
        "exp3_trace_robustness.controlled_wrong_candidate_interception_count",
        "exp3_trace_robustness.controlled_wrong_candidate_interception_rate",
        "exp3_trace_robustness.controlled_wrong_candidate_escape_count",
        "exp3_trace_robustness.controlled_wrong_candidate_escape_rate",
    }
)
GOLDEN_WORKER_DEATH_METRICS = frozenset(
    {
        "exp3_trace_robustness.kill_progress_error_pp",
        "exp3_trace_robustness.kill_progress_error_signed_mean_pp",
        "exp3_trace_robustness.kill_progress_error_signed_max_pp",
        "exp3_trace_robustness.recovered_valid_canonical_required_slots",
        "exp3_trace_robustness.preregistered_required_slots",
        "exp3_trace_robustness.result_completeness_rate",
    }
)
GOLDEN_MAIN_PAIR_METRICS = frozenset(
    {
        "exp2_trace_scalability.trace_replay_paired_speedup",
        "exp2_trace_scalability.trace_replay_parallel_efficiency",
        "exp2_trace_scalability.paired_trace_token_multiplier",
        "exp2_trace_scalability.paired_trace_cost_multiplier",
    }
)
GOLDEN_EXP3_PAIR_METRICS = frozenset(
    {
        "exp3_trace_robustness.trace_replay_wall_clock_overhead_ms",
        "exp3_trace_robustness.trace_attributed_token_overhead",
        "exp3_trace_robustness.trace_attributed_cost_overhead",
    }
)
GOLDEN_EXP4_PAIR_METRICS = frozenset(
    {
        "exp4_ablation.paired_root_count",
        "exp4_ablation.full_success_ablation_success",
        "exp4_ablation.full_success_ablation_failure",
        "exp4_ablation.full_failure_ablation_success",
        "exp4_ablation.full_failure_ablation_failure",
        "exp4_ablation.end_to_end_success_loss_vs_full",
        "exp4_ablation.completion_loss_vs_full",
        "exp4_ablation.trace_replay_wall_clock_delta_vs_full",
        "exp4_ablation.trace_attributed_token_delta_vs_full",
        "exp4_ablation.trace_attributed_cost_delta_vs_full",
    }
)

GOLDEN_EXTRA_NULL_POLICIES = """
incomplete_pair|null_and_publish_blocked|incomplete_pair|exp2_trace_scalability.trace_replay_paired_speedup exp2_trace_scalability.trace_replay_parallel_efficiency exp2_trace_scalability.paired_trace_token_multiplier exp2_trace_scalability.paired_trace_cost_multiplier exp2_trace_scalability.paired_speedup_eligible_pair_count exp2_trace_scalability.paired_speedup_ineligible_pair_count exp2_trace_scalability.trace_replay_paired_speedup_median exp4_ablation.paired_root_count exp4_ablation.full_success_ablation_success exp4_ablation.full_success_ablation_failure exp4_ablation.full_failure_ablation_success exp4_ablation.full_failure_ablation_failure exp4_ablation.end_to_end_success_loss_vs_full exp4_ablation.completion_loss_vs_full exp4_ablation.trace_replay_wall_clock_delta_vs_full exp4_ablation.trace_attributed_token_delta_vs_full exp4_ablation.trace_attributed_cost_delta_vs_full exp4_ablation.independently_labeled_invalid_candidate_count exp4_ablation.wrong_canonical_acceptance_count exp4_ablation.wrong_canonical_acceptance_rate exp4_ablation.raw_only_exposure_count exp4_ablation.raw_only_acceptance_count exp4_ablation.raw_only_acceptance_rate exp4_ablation.stuck_task_count exp4_ablation.stuck_task_rate exp4_ablation.premature_merge_attempt_count exp4_ablation.premature_merge_failure_count exp4_ablation.premature_merge_failure_rate
insufficient_values|null_and_publish_blocked|insufficient_values|exp2_trace_scalability.trace_replay_paired_speedup_median exp2_trace_scalability.trace_replay_paired_speedup_repeat_min exp2_trace_scalability.trace_replay_paired_speedup_repeat_max exp3_trace_robustness.kill_progress_error_signed_mean_pp exp3_trace_robustness.kill_progress_error_signed_max_pp exp5_resources.model_wall_clock_median_ms exp5_resources.model_wall_clock_min_ms exp5_resources.model_wall_clock_max_ms exp5_resources.model_wall_clock_range_ms
membership_excluded|null_with_recorded_exclusion|membership_excluded|exp1_feasibility.failure_root_count exp2_trace_scalability.trace_replay_paired_speedup exp2_trace_scalability.trace_replay_parallel_efficiency exp2_trace_scalability.paired_trace_token_multiplier exp2_trace_scalability.paired_trace_cost_multiplier exp2_trace_scalability.paired_speedup_eligible_pair_count exp2_trace_scalability.paired_speedup_ineligible_pair_count exp2_trace_scalability.trace_replay_paired_speedup_median exp2_trace_scalability.trace_replay_paired_speedup_repeat_min exp2_trace_scalability.trace_replay_paired_speedup_repeat_max exp2_trace_scalability.trace_replay_paired_speedup_relative_difference exp3_trace_robustness.controlled_wrong_candidate_count exp3_trace_robustness.controlled_wrong_candidate_interception_count exp3_trace_robustness.controlled_wrong_candidate_interception_rate exp3_trace_robustness.controlled_wrong_candidate_escape_count exp3_trace_robustness.controlled_wrong_candidate_escape_rate exp3_trace_robustness.started_replacement_attempt_count exp3_trace_robustness.successful_replacement_attempt_count exp3_trace_robustness.replacement_attempt_success_rate exp3_trace_robustness.reassignment_count exp3_trace_robustness.trace_replay_wall_clock_overhead_ms exp3_trace_robustness.trace_attributed_token_overhead exp3_trace_robustness.trace_attributed_cost_overhead exp3_online_recovery.replacement_chain_count exp3_online_recovery.worker_death_reassignment_chain_count exp3_online_recovery.wasted_actual_tokens exp4_ablation.paired_root_count exp4_ablation.full_success_ablation_success exp4_ablation.full_success_ablation_failure exp4_ablation.full_failure_ablation_success exp4_ablation.full_failure_ablation_failure exp4_ablation.end_to_end_success_loss_vs_full exp4_ablation.completion_loss_vs_full exp4_ablation.trace_replay_wall_clock_delta_vs_full exp4_ablation.trace_attributed_token_delta_vs_full exp4_ablation.trace_attributed_cost_delta_vs_full exp4_ablation.independently_labeled_invalid_candidate_count exp4_ablation.wrong_canonical_acceptance_count exp4_ablation.wrong_canonical_acceptance_rate exp4_ablation.raw_only_exposure_count exp4_ablation.raw_only_acceptance_count exp4_ablation.raw_only_acceptance_rate exp4_ablation.stuck_task_count exp4_ablation.stuck_task_rate exp4_ablation.premature_merge_attempt_count exp4_ablation.premature_merge_failure_count exp4_ablation.premature_merge_failure_rate exp5_quality.first_attempt_provider_transport_failure_count exp5_quality.first_attempt_parse_schema_unusable_count exp5_quality.first_attempt_verification_checker_rejection_count
inconsistent_worker_time_evidence|null_and_publish_blocked|inconsistent_worker_time_evidence|exp2_trace_scalability.worker_utilization
not_applicable|null_with_explicit_reason|not_applicable|exp1_feasibility.no_final_failure_count exp1_feasibility.incorrect_final_failure_count exp1_feasibility.infra_invalid_failure_count exp2_trace_scalability.paired_speedup_planned_pair_count exp5_resources.repeat0_wall_clock_ms exp5_resources.repeat1_wall_clock_ms exp5_resources.repeat2_wall_clock_ms
not_applicable|null_with_explicit_reason|not_applicable_non_false_positive|exp3_trace_robustness.controlled_wrong_candidate_count exp3_trace_robustness.controlled_wrong_candidate_interception_count exp3_trace_robustness.controlled_wrong_candidate_interception_rate exp3_trace_robustness.controlled_wrong_candidate_escape_count exp3_trace_robustness.controlled_wrong_candidate_escape_rate
not_applicable|null_with_explicit_reason|not_applicable_rate_fault|exp3_trace_robustness.kill_progress_error_pp exp3_trace_robustness.kill_progress_error_signed_mean_pp exp3_trace_robustness.kill_progress_error_signed_max_pp exp3_trace_robustness.recovered_valid_canonical_required_slots exp3_trace_robustness.preregistered_required_slots exp3_trace_robustness.result_completeness_rate
not_applicable|null_with_explicit_reason|not_applicable_wrong_mode|exp4_ablation.paired_root_count exp4_ablation.full_success_ablation_success exp4_ablation.full_success_ablation_failure exp4_ablation.full_failure_ablation_success exp4_ablation.full_failure_ablation_failure exp4_ablation.end_to_end_success_loss_vs_full exp4_ablation.completion_loss_vs_full exp4_ablation.trace_replay_wall_clock_delta_vs_full exp4_ablation.trace_attributed_token_delta_vs_full exp4_ablation.trace_attributed_cost_delta_vs_full exp4_ablation.independently_labeled_invalid_candidate_count exp4_ablation.wrong_canonical_acceptance_count exp4_ablation.wrong_canonical_acceptance_rate exp4_ablation.raw_only_exposure_count exp4_ablation.raw_only_acceptance_count exp4_ablation.raw_only_acceptance_rate exp4_ablation.stuck_task_count exp4_ablation.stuck_task_rate exp4_ablation.premature_merge_attempt_count exp4_ablation.premature_merge_failure_count exp4_ablation.premature_merge_failure_rate
zero_denominator|null_and_publish_blocked|zero_denominator|exp1_feasibility.completion_rate exp1_feasibility.end_to_end_verified_success_rate exp2_trace_scalability.completion_rate exp2_trace_scalability.end_to_end_verified_success_rate exp2_trace_scalability.trace_replay_paired_speedup exp2_trace_scalability.trace_replay_parallel_efficiency exp2_trace_scalability.paired_trace_token_multiplier exp2_trace_scalability.paired_trace_cost_multiplier exp2_trace_scalability.trace_replay_paired_speedup_median exp2_trace_scalability.trace_replay_paired_speedup_relative_difference exp2_online_concurrency.completion_rate exp2_online_concurrency.end_to_end_verified_success_rate exp2_online_concurrency.provider_429_or_timeout_union_fraction exp3_trace_robustness.completion_rate exp3_trace_robustness.end_to_end_verified_success_rate exp3_trace_robustness.controlled_wrong_candidate_interception_rate exp3_trace_robustness.controlled_wrong_candidate_escape_rate exp3_trace_robustness.result_completeness_rate exp3_online_recovery.completion_rate exp3_online_recovery.end_to_end_verified_success_rate exp4_ablation.completion_rate exp4_ablation.end_to_end_verified_success_rate exp5_quality.completion_rate exp5_quality.end_to_end_verified_success_rate exp5_quality.first_attempt_nonpass_rate exp5_quality.first_attempt_verification_rejection_rate exp5_resources.first_attempt_call_coverage
zero_denominator|null_with_explicit_reason|zero_denominator|exp2_trace_scalability.worker_utilization
zero_denominator|null_with_explicit_reason|not_applicable_no_started_replacement|exp3_trace_robustness.replacement_attempt_success_rate
zero_denominator|null_with_explicit_reason|zero_denominator|exp4_ablation.wrong_canonical_acceptance_rate exp4_ablation.raw_only_acceptance_rate exp4_ablation.stuck_task_rate exp4_ablation.premature_merge_failure_rate
""".strip()





















def _contract_body() -> dict[str, Any]:
    return json.loads(DEFAULT_PAPER_METRIC_CONTRACT_PATH.read_text(encoding="utf-8"))


def _reseal(body: dict[str, Any]) -> None:
    body["contract_digest"] = compute_metric_contract_digest(body)


def _write(path: Path, body: dict[str, Any]) -> None:
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")


def _observation(
    table_id: str,
    metric_id: str,
    value: int | Decimal,
    value_domain: str = COUNT,
) -> VerifiedMetricObservation:
    metric = load_paper_metric_contract().require_metric(table_id, metric_id)
    return VerifiedMetricObservation(
        observation_id=f"obs:{table_id}:{metric_id}",
        table_id=table_id,
        metric_id=metric_id,
        row_kind=metric.row_kind,
        value=value,
        value_domain=value_domain,
        source_member_ids=("source-1",),
        evidence_verified=True,
        row_identity_digest=ROW_IDENTITY_DIGEST,
        recompute_only=True,
        paper_eligible=False,
    )


def _bundle(
    *,
    row_facts: dict[str, Any] | None = None,
    member_facts: dict[str, dict[str, Any]] | None = None,
    observations: tuple[VerifiedMetricObservation, ...] = (),
) -> MetricObservationBundle:
    facts = member_facts or {"source-1": {"member_kind": "audit_anchor"}}
    aligned_observations = tuple(
        dataclasses.replace(
            observation,
            source_member_ids=tuple(facts),
            row_identity_digest=ROW_IDENTITY_DIGEST,
        )
        for observation in observations
    )
    return MetricObservationBundle(
        row_facts={"infra_invalid": False, **(row_facts or {})},
        member_ids=tuple(facts),
        member_facts_by_id=facts,
        verified_observations=aligned_observations,
        row_identity_digest=ROW_IDENTITY_DIGEST,
    )


def _assert_deeply_immutable(value: Any) -> None:
    if dataclasses.is_dataclass(value):
        for field in dataclasses.fields(value):
            _assert_deeply_immutable(getattr(value, field.name))
    elif isinstance(value, tuple):
        for item in value:
            _assert_deeply_immutable(item)
    else:
        assert not isinstance(value, (dict, list, set))


class _PredicateConflict(Exception):
    pass


def _assign_fact(facts: dict[str, Any], field: str, value: Any) -> None:
    if field in facts and facts[field] != value:
        raise _PredicateConflict(f"{field}: {facts[field]!r} != {value!r}")
    facts[field] = value


def _merge_predicate_facts(
    predicate: dict[str, Any], facts: dict[str, Any], *, truth: bool
) -> None:
    """构造使合同谓词为真/假的最小事实，用于覆盖全部已注册公式。"""
    op = predicate["op"]
    args = predicate.get("args", [])
    if op == "not":
        _merge_predicate_facts(args[0], facts, truth=not truth)
        return
    if op == "all":
        if truth:
            for arg in args:
                _merge_predicate_facts(arg, facts, truth=True)
            return
        for arg in args:
            candidate = copy.deepcopy(facts)
            try:
                _merge_predicate_facts(arg, candidate, truth=False)
            except _PredicateConflict:
                continue
            facts.clear()
            facts.update(candidate)
            return
        raise _PredicateConflict("cannot make all predicate false")
    if op == "any":
        if truth:
            for arg in args:
                candidate = copy.deepcopy(facts)
                try:
                    _merge_predicate_facts(arg, candidate, truth=True)
                except _PredicateConflict:
                    continue
                facts.clear()
                facts.update(candidate)
                return
            raise _PredicateConflict("cannot make any predicate true")
        for arg in args:
            _merge_predicate_facts(arg, facts, truth=False)
        return

    field = predicate.get("field")
    other_field = predicate.get("other_field")
    if op == "field_equals":
        value = predicate["value"] if truth else "__not_expected__"
        _assign_fact(facts, field, value)
    elif op == "field_in":
        value = predicate["values"][0] if truth else "__outside_allowed_set__"
        _assign_fact(facts, field, value)
    elif op == "field_true":
        _assign_fact(facts, field, truth)
    elif op == "field_false":
        _assign_fact(facts, field, not truth)
    elif op == "field_nonempty_string":
        _assign_fact(facts, field, "identity" if truth else "")
    elif op in {"fields_equal", "fields_not_equal"}:
        should_equal = truth if op == "fields_equal" else not truth
        left = facts.get(field)
        right = facts.get(other_field)
        if should_equal:
            shared = left if left is not None else right if right is not None else "same"
            _assign_fact(facts, field, shared)
            _assign_fact(facts, other_field, shared)
        else:
            left_value = left if left is not None else "left"
            right_value = right if right is not None else "right"
            if left_value == right_value:
                raise _PredicateConflict("existing fields cannot be made unequal")
            _assign_fact(facts, field, left_value)
            _assign_fact(facts, other_field, right_value)
    elif op == "integer_successor":
        if truth:
            _assign_fact(facts, other_field, 0)
            _assign_fact(facts, field, 1)
        else:
            _assign_fact(facts, other_field, 0)
            _assign_fact(facts, field, 2)
    elif op == "number_positive":
        _assign_fact(facts, field, 2 if truth else 0)
    elif op == "ordered_roles_exact":
        _assign_fact(
            facts,
            field,
            tuple(predicate["values"]) if truth else ("wrong_role",),
        )
    else:  # pragma: no cover - 新谓词必须显式进入测试生成器
        raise AssertionError(f"unsupported predicate op in test fixture: {op}")


def _membership_facts(body: dict[str, Any], membership_id: str) -> dict[str, Any]:
    membership = next(
        item for item in body["memberships"] if item["membership_id"] == membership_id
    )
    facts: dict[str, Any] = {}
    _merge_predicate_facts(membership["predicate"], facts, truth=True)
    kind = facts.get("member_kind")
    if kind == "preregistered_root":
        facts.setdefault("complete_final_result_reference", True)
        facts.setdefault("four_gate_end_to_end_success", True)
        facts.setdefault("end_to_end_verified_success", True)
        facts.setdefault("failure_class", "none")
        facts.setdefault("failure_stage", "none")
        facts.setdefault("failure_kind", "none")
        if membership_id == "exp1_failure_root":
            facts["end_to_end_verified_success"] = False
            facts["failure_class"] = "no_final"
            facts["failure_stage"] = "terminal"
            facts["failure_kind"] = "no_final"
    if kind == "exp2_preregistered_pair":
        facts.setdefault("baseline_worker_count", 1)
        facts.setdefault("compared_worker_count", 3)
        facts.setdefault("baseline_case_record_digest", "case")
        facts.setdefault("compared_case_record_digest", "case")
        facts.setdefault("baseline_repeat_id", 0)
        facts.setdefault("compared_repeat_id", 0)
        facts.setdefault("baseline_sample_slot_index", 0)
        facts.setdefault("compared_sample_slot_index", 0)
        facts.setdefault("baseline_end_to_end_verified_success", True)
        facts.setdefault("compared_end_to_end_verified_success", True)
        facts.setdefault("baseline_time_evidence_complete", True)
        facts.setdefault("compared_time_evidence_complete", True)
        facts.setdefault("closed_exclusion_reason", None)
        facts.setdefault("baseline_trace_replay_wall_clock_ms", 4)
        facts.setdefault("compared_trace_replay_wall_clock_ms", 2)
    if kind == "exp4_paired_root":
        facts.setdefault("pair_evidence_complete", True)
        facts.setdefault("full_success", True)
        facts.setdefault("ablation_success", True)
    if kind == "exp5_provider_attempt":
        facts.setdefault("actual_call", True)
        facts.setdefault("provider_transport_failure", False)
        facts.setdefault("parse_schema_unusable", False)
        facts.setdefault("verification_checker_rejected", False)
    facts["current_provider_roles"] = ONLINE_ROLES
    facts["source_bank_roles"] = TRACE_ROLES
    return facts


def _execution_bundle_for_metric(
    body: dict[str, Any], table_id: str, metric_id: str
) -> MetricObservationBundle:
    metric = next(
        item
        for item in body["metrics"]
        if item["table"] == table_id and item["metric_id"] == metric_id
    )
    row_facts = {
        "infra_invalid": False,
        "evidence_class": metric["evidence_classes"][0],
    }
    row_gate = next(
        item
        for item in body["memberships"]
        if item["membership_id"] == metric["row_gate_membership_id"]
    )
    _merge_predicate_facts(row_gate["predicate"], row_facts, truth=True)
    applicability = metric["applicability"]
    if applicability["kind"] == "field_in":
        row_facts[applicability["field"]] = applicability["values"][0]

    source = metric["source_membership_id"]
    member_facts: dict[str, dict[str, Any]] = {
        "source-1": (
            _membership_facts(body, source)
            if source is not None
            else {
                "member_kind": "audit_anchor",
                "current_provider_roles": ONLINE_ROLES,
                "source_bank_roles": TRACE_ROLES,
            }
        )
    }
    if metric["formula"]["op"] == "relative_range" or metric["formula_id"] in {
        "minimum_of_two_raw_repeat_summaries_v1",
        "maximum_of_two_raw_repeat_summaries_v1",
    }:
        member_facts["source-2"] = copy.deepcopy(member_facts["source-1"])

    for operand in metric["formula"]["operands"]:
        operand_id = operand["id"]
        if operand_id.startswith("row."):
            row_facts[operand_id.removeprefix("row.")] = 2
        elif operand_id.startswith("members."):
            field = operand_id.removeprefix("members.")
            for facts in member_facts.values():
                facts.setdefault(field, 2)

    observations = []
    metrics_by_key = {
        (item["table"], item["metric_id"]): item for item in body["metrics"]
    }
    for operand in metric["formula"]["operands"]:
        if not operand["id"].startswith("observation."):
            continue
        _, dependency_table, dependency_metric = operand["id"].split(".", 2)
        dependency = metrics_by_key[(dependency_table, dependency_metric)]
        value: int | Decimal = (
            1
            if dependency["value_domain"] == COUNT
            else Decimal("1")
        )
        observations.append(
            _observation(
                dependency_table,
                dependency_metric,
                value,
                dependency["value_domain"],
            )
        )
    return _bundle(
        row_facts=row_facts,
        member_facts=member_facts,
        observations=tuple(observations),
    )


def test_contract_covers_every_numeric_cell_in_metrics_matrix() -> None:
    contract = load_paper_metric_contract()
    assert contract.schema_version == PAPER_METRIC_CONTRACT_SCHEMA_VERSION
    assert {table.table_id: table.numeric_output_fields for table in contract.tables} == (
        EXPECTED_TABLE_FIELDS
    )
    assert len(contract.metrics) == sum(map(len, EXPECTED_TABLE_FIELDS.values()))
    assert len({(metric.table, metric.metric_id) for metric in contract.metrics}) == (
        len(contract.metrics)
    )
    for table_id, fields in EXPECTED_TABLE_FIELDS.items():
        contract.validate_numeric_output_fields(table_id, fields)
        for field in fields:
            metric = contract.require_metric(table_id, field)
            assert metric.source_membership_id is None or metric.source_membership_id
            assert metric.row_gate_membership_id
            assert metric.value_domain in {
                COUNT,
                PROPORTION,
                "ratio_unbounded",
                NONNEGATIVE,
                "signed_number",
            }


def test_contract_matches_the_independent_140_cell_semantic_golden_matrix() -> None:
    body = _contract_body()
    all_keys = tuple(f"{table}.{metric}" for table, metric in ALL_NUMERIC_CELLS)
    metrics_by_key = {
        f"{metric['table']}.{metric['metric_id']}": metric
        for metric in body["metrics"]
    }
    assert len(all_keys) == 140
    assert tuple(metrics_by_key) == all_keys

    def parse_groups(specification: str) -> dict[str, str]:
        groups: dict[str, str] = {}
        for line in specification.splitlines():
            label, member_text = line.split("|", 1)
            for key in member_text.split():
                assert key in metrics_by_key
                assert key not in groups
                groups[key] = label
        return groups

    # source selector：三类 root selector 是跨表通用定义，其余均由手写语义组给出。
    expected_sources: dict[str, str | None] = dict.fromkeys(all_keys)
    for key in all_keys:
        if key.endswith(".preregistered_root_count"):
            expected_sources[key] = "all_preregistered_roots"
        elif key.endswith(".final_result_root_count"):
            expected_sources[key] = "roots_with_complete_final_result_reference"
        elif key.endswith(".verified_correct_root_count"):
            expected_sources[key] = "roots_with_four_gate_end_to_end_success"
    source_exceptions = parse_groups(GOLDEN_SOURCE_GROUPS)
    assert all(expected_sources[key] is None for key in source_exceptions)
    expected_sources.update(source_exceptions)
    assert {
        key: metric["source_membership_id"] for key, metric in metrics_by_key.items()
    } == expected_sources

    # formula：count-members 是默认原语；root rate 与非 count 公式独立展开。
    expected_formulas: dict[
        str, tuple[str, str, tuple[tuple[str, str], ...], str]
    ] = {
        key: (
            "count_members_v1",
            "count",
            (("member_set", "members"),),
            COUNT,
        )
        for key in all_keys
    }
    for table_id, fields in EXPECTED_TABLE_FIELDS.items():
        if "completion_rate" in fields:
            expected_formulas[f"{table_id}.completion_rate"] = (
                "ratio_v1",
                "ratio",
                (
                    ("number", f"observation.{table_id}.final_result_root_count"),
                    ("number", f"observation.{table_id}.preregistered_root_count"),
                ),
                PROPORTION,
            )
            expected_formulas[f"{table_id}.end_to_end_verified_success_rate"] = (
                "ratio_v1",
                "ratio",
                (
                    ("number", f"observation.{table_id}.verified_correct_root_count"),
                    ("number", f"observation.{table_id}.preregistered_root_count"),
                ),
                PROPORTION,
            )
    formula_exceptions: dict[
        str, tuple[str, str, tuple[tuple[str, str], ...], str]
    ] = {}
    for line in GOLDEN_NONCOUNT_FORMULAS.splitlines():
        key, formula_id, op, operand_text, value_domain = line.split("|")
        assert key in metrics_by_key
        assert key not in formula_exceptions
        operands = tuple(
            tuple(operand.split(":", 1)) for operand in operand_text.split(",")
        )
        formula_exceptions[key] = (formula_id, op, operands, value_domain)
    expected_formulas.update(formula_exceptions)
    actual_formulas = {
        key: (
            metric["formula_id"],
            metric["formula"]["op"],
            tuple(
                (operand["kind"], operand["id"])
                for operand in metric["formula"]["operands"]
            ),
            metric["value_domain"],
        )
        for key, metric in metrics_by_key.items()
    }
    assert actual_formulas == expected_formulas

    # row kind 与 row gate 是独立维度；少数混合表只列出语义例外。
    expected_row_kinds = {
        key: (
            "condition_summary"
            if key.startswith("exp1_feasibility.")
            else "worker_repeat_observation"
            if key.startswith("exp2_trace_scalability.")
            else "worker_condition_summary"
            if key.startswith("exp2_online_concurrency.")
            else "condition_observation"
            if key.startswith("exp3_trace_robustness.")
            else "online_recovery_summary"
            if key.startswith("exp3_online_recovery.")
            else "model_summary"
            if key.startswith(("exp5_quality.", "exp5_resources."))
            else "mode_specific"
        )
        for key in all_keys
    }
    for key in GOLDEN_MAIN_PAIR_METRICS:
        expected_row_kinds[key] = "paired_worker_comparison"
    for metric_id in (
        "paired_speedup_planned_pair_count",
        "paired_speedup_eligible_pair_count",
        "paired_speedup_ineligible_pair_count",
        "trace_replay_paired_speedup_median",
    ):
        expected_row_kinds[f"exp2_trace_scalability.{metric_id}"] = "repeat_summary"
    for metric_id in (
        "trace_replay_paired_speedup_repeat_min",
        "trace_replay_paired_speedup_repeat_max",
        "trace_replay_paired_speedup_relative_difference",
    ):
        expected_row_kinds[f"exp2_trace_scalability.{metric_id}"] = (
            "condition_summary"
        )
    for metric_id in (
        "kill_progress_error_signed_mean_pp",
        "kill_progress_error_signed_max_pp",
    ):
        expected_row_kinds[f"exp3_trace_robustness.{metric_id}"] = (
            "worker_death_summary"
        )
    for metric_id in EXPECTED_TABLE_FIELDS["exp4_ablation"][:5]:
        expected_row_kinds[f"exp4_ablation.{metric_id}"] = "mode_summary"
    for key in GOLDEN_EXP4_PAIR_METRICS:
        expected_row_kinds[key] = "paired_transition"
    assert {
        key: metric["row_kind"] for key, metric in metrics_by_key.items()
    } == expected_row_kinds

    expected_gates = dict.fromkeys(all_keys, "row_gate_publication_valid")
    expected_gates.update(parse_groups(GOLDEN_NONDEFAULT_GATES))
    assert {
        key: metric["row_gate_membership_id"]
        for key, metric in metrics_by_key.items()
    } == expected_gates

    expected_applicability = {
        key: {
            "kind": "always",
            "field": None,
            "values": [],
            "not_applicable_reason": None,
        }
        for key in all_keys
    }
    for key in GOLDEN_FALSE_POSITIVE_METRICS:
        expected_applicability[key] = {
            "kind": "field_in",
            "field": "fault_type",
            "values": ["false_positive"],
            "not_applicable_reason": "not_applicable_non_false_positive",
        }
    for key in GOLDEN_WORKER_DEATH_METRICS:
        expected_applicability[key] = {
            "kind": "field_in",
            "field": "fault_type",
            "values": ["worker_death"],
            "not_applicable_reason": "not_applicable_rate_fault",
        }
    assert {
        key: metric["applicability"] for key, metric in metrics_by_key.items()
    } == expected_applicability

    expected_pair_keys: dict[str, tuple[str, ...]] = dict.fromkeys(all_keys, ())
    main_pair_key = (
        "case_record_digest",
        "repeat_id",
        "sample_slot_index",
        "baseline_worker_count",
        "compared_worker_count",
    )
    exp3_pair_key = (
        "case_record_digest",
        "repeat_id",
        "sample_slot_index",
        "fault_or_death_condition",
        "paired_trace_reference",
    )
    exp4_pair_key = (
        "case_record_digest",
        "repeat_id",
        "sample_slot_index",
        "FULL",
        "ablation_mode",
    )
    for key in GOLDEN_MAIN_PAIR_METRICS:
        expected_pair_keys[key] = main_pair_key
    for key in GOLDEN_EXP3_PAIR_METRICS:
        expected_pair_keys[key] = exp3_pair_key
    for key in GOLDEN_EXP4_PAIR_METRICS:
        expected_pair_keys[key] = exp4_pair_key
    assert {
        key: tuple(metric["pair_key"]) for key, metric in metrics_by_key.items()
    } == expected_pair_keys

    expected_nulls = {
        key: {
            "infra_invalid": ("null_and_publish_blocked", "infra_invalid"),
            "missing_required_evidence": (
                "null_and_publish_blocked",
                "missing_required_evidence",
            ),
        }
        for key in all_keys
    }
    for line in GOLDEN_EXTRA_NULL_POLICIES.splitlines():
        trigger, action, reason, member_text = line.split("|", 3)
        for key in member_text.split():
            assert key in metrics_by_key
            assert trigger not in expected_nulls[key]
            expected_nulls[key][trigger] = (action, reason)
    actual_nulls = {
        key: {
            rule["trigger"]: (rule["action"], rule["reason"])
            for rule in metric["null"]
        }
        for key, metric in metrics_by_key.items()
    }
    assert actual_nulls == expected_nulls


def test_recompute_accepts_only_one_nonempty_observation_bundle() -> None:
    contract = load_paper_metric_contract()
    signature = inspect.signature(recompute_metric)
    assert "bundle" in signature.parameters
    assert "context" not in signature.parameters
    assert "operand_values" not in signature.parameters

    with pytest.raises(TypeError):
        recompute_metric(  # type: ignore[call-arg]
            contract,
            "exp1_feasibility",
            "preregistered_root_count",
            row_kind="condition_summary",
            context={},
            operand_values={},
        )
    with pytest.raises(ValueError, match="MetricObservationBundle"):
        recompute_metric(
            contract,
            "exp1_feasibility",
            "preregistered_root_count",
            row_kind="condition_summary",
            bundle=None,  # type: ignore[arg-type]
        )

    empty = MetricObservationBundle(
        row_facts={}, member_ids=(), member_facts_by_id={}, verified_observations=()
    )
    blocked = recompute_metric(
        contract,
        "exp1_feasibility",
        "preregistered_root_count",
        row_kind="condition_summary",
        bundle=empty,
    )
    assert blocked.value is None and blocked.publish_blocked
    assert blocked.reason == "empty_observation_bundle"


@pytest.mark.parametrize(
    "metric_id",
    (
        "trace_replay_paired_speedup_repeat_min",
        "trace_replay_paired_speedup_repeat_max",
        "trace_replay_paired_speedup_relative_difference",
    ),
)
def test_exp2_condition_repeat_null_distinguishes_exclusion_from_missing_evidence(
    metric_id: str,
) -> None:
    contract = load_paper_metric_contract()
    root = {
        "member_kind": "preregistered_root",
        "preregistered_root_run_id": "root-1",
        "final_result_reference_complete": True,
        "end_to_end_verified_success": False,
    }
    excluded = recompute_metric(
        contract,
        "exp2_trace_scalability",
        metric_id,
        row_kind="condition_summary",
        bundle=_bundle(
            member_facts={
                "root-1": root,
                "repeat-1": {
                    "member_kind": "exp2_repeat_speedup_exclusion",
                    "closed_exclusion_reason": "membership_excluded",
                    "lineage_root_run_ids": ("root-1",),
                },
            }
        ),
    )
    assert excluded.value is None
    assert excluded.reason == "membership_excluded"
    assert excluded.publish_blocked is False

    blocked = recompute_metric(
        contract,
        "exp2_trace_scalability",
        metric_id,
        row_kind="condition_summary",
        bundle=_bundle(
            member_facts={
                "root-1": root,
                "repeat-1": {
                    "member_kind": "exp2_repeat_speedup_summary",
                    "upstream_blocked_reason": "missing_repeat_speedup_evidence",
                    "lineage_root_run_ids": ("root-1",),
                },
            }
        ),
    )
    assert blocked.value is None
    assert blocked.reason == "missing_required_member_evidence"
    assert blocked.publish_blocked is True


def test_bundle_rejects_empty_duplicate_unbound_or_shared_member_facts() -> None:
    with pytest.raises(ValueError, match="non-empty string"):
        MetricObservationBundle(
            row_facts={},
            member_ids=("",),
            member_facts_by_id={"": {}},
            verified_observations=(),
        )
    with pytest.raises(ValueError, match="duplicate member id"):
        MetricObservationBundle(
            row_facts={},
            member_ids=("a", "a"),
            member_facts_by_id={"a": {}},
            verified_observations=(),
        )
    with pytest.raises(ValueError, match="exactly match member_ids"):
        MetricObservationBundle(
            row_facts={},
            member_ids=("a", "b"),
            member_facts_by_id={"a": {}},
            verified_observations=(),
        )
    shared: dict[str, Any] = {"member_kind": "root"}
    with pytest.raises(ValueError, match="independent facts"):
        MetricObservationBundle(
            row_facts={},
            member_ids=("a", "b"),
            member_facts_by_id={"a": shared, "b": shared},
            verified_observations=(),
        )


def test_predicate_missing_propagates_through_all_any_not() -> None:
    contract = load_paper_metric_contract()

    any_missing = evaluate_membership(contract, "exp1_failure_root", {})
    assert any_missing.status is MembershipStatus.BLOCKED_MISSING_EVIDENCE
    assert any_missing.predicate_truth is PredicateTruth.MISSING

    all_missing = evaluate_membership(
        contract,
        "exp5_nonpass_parse_schema_unusable",
        {"parse_schema_unusable": True},
    )
    assert all_missing.status is MembershipStatus.BLOCKED_MISSING_EVIDENCE

    included = evaluate_membership(
        contract,
        "exp5_nonpass_parse_schema_unusable",
        {
            "member_kind": "exp5_provider_attempt",
            "actual_call": True,
            "provider_transport_failure": False,
            "parse_schema_unusable": True,
        },
    )
    assert included.status is MembershipStatus.INCLUDED

    excluded = evaluate_membership(
        contract,
        "exp5_nonpass_parse_schema_unusable",
        {
            "member_kind": "exp5_provider_attempt",
            "actual_call": True,
            "provider_transport_failure": True,
            "parse_schema_unusable": True,
        },
    )
    assert excluded.status is MembershipStatus.EXCLUDED


def test_membership_blocks_when_any_member_evidence_is_missing() -> None:
    contract = load_paper_metric_contract()
    bundle = _bundle(
        member_facts={
            "root-1": {"member_kind": "preregistered_root"},
            "root-2": {},
        }
    )
    result = recompute_metric(
        contract,
        "exp1_feasibility",
        "preregistered_root_count",
        row_kind="condition_summary",
        bundle=bundle,
    )
    assert result.value is None and result.publish_blocked

    assert result.reason == "missing_required_member_evidence"
    assert result.blocked_member_ids == ("root-2",)


@pytest.mark.parametrize("value", [-1, 1.0, True])
def test_count_observation_rejects_negative_float_and_bool(value: Any) -> None:
    with pytest.raises(ValueError, match="nonnegative integer"):
        _observation("exp1_feasibility", "failure_root_count", value)


def test_proportion_rejects_numerator_greater_than_denominator() -> None:
    contract = load_paper_metric_contract()
    bundle = _bundle(
        row_facts={"evidence_class": "online_real_provider"},
        observations=(
            _observation("exp1_feasibility", "final_result_root_count", 7),
            _observation("exp1_feasibility", "preregistered_root_count", 4),
            _observation("exp1_feasibility", "no_final_failure_count", 0),
            _observation("exp1_feasibility", "incorrect_final_failure_count", 0),
            _observation("exp1_feasibility", "infra_invalid_failure_count", 0),
            _observation("exp1_feasibility", "failure_root_count", 0),
        )
    )
    result = recompute_metric(
        contract,
        "exp1_feasibility",
        "completion_rate",
        row_kind="condition_summary",
        bundle=bundle,
    )
    assert result.value is None and result.publish_blocked
    assert result.reason == "proportion_numerator_exceeds_denominator"


def test_required_invariants_gate_recomputation_and_use_verified_counts_only() -> None:
    contract = load_paper_metric_contract()
    base_observations = (
        _observation("exp2_trace_scalability", "paired_speedup_eligible_pair_count", 2),
        _observation("exp2_trace_scalability", "paired_speedup_ineligible_pair_count", 1),
        _observation("exp2_trace_scalability", "paired_speedup_planned_pair_count", 3),
    )
    satisfied = evaluate_invariant(
        contract,
        "exp2_pair_membership_partition",
        _bundle(
            member_facts={
                "eligible": _exp2_pair(speedup=2, compared_success=True),
                "ineligible": _exp2_pair(
                    speedup=2,
                    compared_success=False,
                    exclusion_reason="compared_not_end_to_end_success",
                ),
            },
            observations=base_observations,
        ),
    )
    assert satisfied.satisfied

    incomplete = _exp2_pair(
        speedup=2,
        compared_success=False,
        exclusion_reason="compared_not_end_to_end_success",
    )
    del incomplete["closed_exclusion_reason"]
    missing = evaluate_invariant(
        contract,
        "exp2_pair_membership_partition",
        _bundle(member_facts={"incomplete": incomplete}, observations=base_observations),
    )
    assert not missing.satisfied and missing.publish_blocked
    assert missing.violation_reason == "missing_required_member_evidence"

    wrong = evaluate_invariant(
        contract,
        "exp2_pair_membership_partition",
        _bundle(
            member_facts={
                "open": _exp2_pair(
                    speedup=2, compared_success=False, exclusion_reason=None
                )
            },
            observations=(
                base_observations[0],
                base_observations[1],
                _observation(
                    "exp2_trace_scalability",
                    "paired_speedup_planned_pair_count",
                    4,
                ),
            )
        ),
    )
    assert not wrong.satisfied and wrong.publish_blocked
    assert wrong.violation_reason == "exp2_pair_memberships_do_not_partition_planned_pairs"

    with pytest.raises(ValueError, match="MetricObservationBundle"):
        evaluate_invariant(  # type: ignore[arg-type]
            contract,
            "exp2_pair_membership_partition",
            {"eligible": 2, "ineligible": 1, "planned": 3},
        )


def _exp2_pair(
    *, speedup: int, compared_success: bool, exclusion_reason: str | None = None
) -> dict[str, Any]:
    return {
        "member_kind": "exp2_preregistered_pair",
        "baseline_worker_count": 1,
        "compared_worker_count": 7,
        "baseline_case_record_digest": "case-a",
        "compared_case_record_digest": "case-a",
        "baseline_repeat_id": 0,
        "compared_repeat_id": 0,
        "baseline_sample_slot_index": 0,
        "compared_sample_slot_index": 0,
        "baseline_end_to_end_verified_success": True,
        "compared_end_to_end_verified_success": compared_success,
        "baseline_time_evidence_complete": True,
        "compared_time_evidence_complete": True,
        "baseline_trace_replay_wall_clock_ms": 1200,
        "compared_trace_replay_wall_clock_ms": 1200 // speedup,
        "paired_speedup": speedup,
        "closed_exclusion_reason": exclusion_reason,
    }


def test_exp2_source_and_row_gate_memberships_are_complementary_and_compute_real_median() -> None:
    contract = load_paper_metric_contract()
    metrics = {
        metric_id: contract.require_metric("exp2_trace_scalability", metric_id)
        for metric_id in (
            "paired_speedup_planned_pair_count",
            "paired_speedup_eligible_pair_count",
            "paired_speedup_ineligible_pair_count",
            "trace_replay_paired_speedup_median",
        )
    }
    assert metrics["paired_speedup_eligible_pair_count"].source_membership_id == (
        "exp2_speedup_eligible_pair"
    )
    assert metrics["paired_speedup_eligible_pair_count"].row_gate_membership_id == (
        "exp2_repeat_summary_row"
    )
    assert metrics["paired_speedup_ineligible_pair_count"].source_membership_id == (
        "exp2_speedup_ineligible_pair"
    )

    observations = (
        _observation("exp2_trace_scalability", "paired_speedup_eligible_pair_count", 2),
        _observation("exp2_trace_scalability", "paired_speedup_ineligible_pair_count", 1),
        _observation("exp2_trace_scalability", "paired_speedup_planned_pair_count", 3),
    )
    bundle = _bundle(
        row_facts={"summary_kind": "repeat"},
        member_facts={
            "pair-1": _exp2_pair(speedup=2, compared_success=True),
            "pair-2": _exp2_pair(speedup=4, compared_success=True),
            "pair-3": _exp2_pair(
                speedup=3,
                compared_success=False,
                exclusion_reason="compared_not_end_to_end_success",
            ),
        },
        observations=observations,
    )
    assert recompute_metric(
        contract,
        "exp2_trace_scalability",
        "paired_speedup_eligible_pair_count",
        row_kind="repeat_summary",
        bundle=bundle,
    ).value == 2
    assert recompute_metric(
        contract,
        "exp2_trace_scalability",
        "paired_speedup_ineligible_pair_count",
        row_kind="repeat_summary",
        bundle=bundle,
    ).value == 1
    assert recompute_metric(
        contract,
        "exp2_trace_scalability",
        "trace_replay_paired_speedup_median",
        row_kind="repeat_summary",
        bundle=bundle,
    ).value == Decimal(3)

    missing_invariant = recompute_metric(
        contract,
        "exp2_trace_scalability",
        "trace_replay_paired_speedup_median",
        row_kind="repeat_summary",
        bundle=_bundle(
            row_facts={"summary_kind": "repeat"},
            member_facts={
                "pair-1": {
                    key: value
                    for key, value in _exp2_pair(
                        speedup=2,
                        compared_success=False,
                        exclusion_reason="compared_not_end_to_end_success",
                    ).items()
                    if key != "closed_exclusion_reason"
                }
            },
            observations=observations[:2],
        ),
    )
    assert missing_invariant.value is None and missing_invariant.publish_blocked
    assert missing_invariant.reason == "incomplete_pair"


def test_exp2_absolute_resources_multipliers_and_429_timeout_union_are_frozen() -> None:
    contract = load_paper_metric_contract()
    assert contract.require_metric(
        "exp2_trace_scalability", "bank_slot_consumption"
    ).formula_id == "count_committed_trace_consumptions_v1"
    assert contract.require_metric(
        "exp2_trace_scalability", "trace_attributed_tokens"
    ).formula_id == "sum_source_usage_per_committed_consumption_v1"
    assert contract.require_metric(
        "exp2_trace_scalability", "paired_trace_token_multiplier"
    ).value_domain == "ratio_unbounded"
    union = contract.require_metric(
        "exp2_online_concurrency", "provider_429_or_timeout_union_fraction"
    )
    assert union.value_domain == PROPORTION
    assert union.source_membership_id == "exp2_first_attempt_429_or_timeout_union"


def test_exp3_replacement_success_and_recovery_source_are_identity_bound() -> None:
    contract = load_paper_metric_contract()
    common = {
        "member_kind": "replacement_attempt",
        "recovery_source_kind": "validation_replacement",
        "fault_or_death_id": "fault-1",
        "fault_or_death_task_unit_id": "unit-1",
        "fault_or_death_attempt_id": "attempt-1",
        "original_task_unit_id": "unit-1",
        "replacement_task_unit_id": "unit-1",
        "original_attempt_id": "attempt-1",
        "replacement_attempt_id": "attempt-2",
        "original_attempt_ordinal": 0,
        "replacement_attempt_ordinal": 1,
        "new_attempt_fault_or_death_id": "fault-1",
        "new_attempt_task_unit_id": "unit-1",
        "new_attempt_id": "attempt-2",
        "new_attempt_ordinal": 1,
        "replacement_result_qualified": True,
        "original_task_unit_completed": True,
        "replacement_result_task_unit_id": "unit-1",
        "completed_task_unit_id": "unit-1",
        "ordered_evidence_roles": (
            "fault_or_death_event",
            "new_attempt",
            "provider_dispatch",
            "raw_or_failure",
            "provenance",
            "usage",
            "model_record",
            "qualified_result",
            "original_task_unit_completed",
        ),
    }
    successful = evaluate_membership(
        contract, "exp3_replacement_attempt_successful", common
    )
    assert successful.status is MembershipStatus.INCLUDED
    assert evaluate_membership(
        contract,
        "exp3_replacement_attempt_successful",
        {**common, "replacement_result_qualified": False},
    ).status is MembershipStatus.EXCLUDED
    assert evaluate_membership(
        contract,
        "exp3_replacement_attempt_successful",
        {key: value for key, value in common.items() if key != "original_task_unit_completed"},
    ).status is MembershipStatus.BLOCKED_MISSING_EVIDENCE

    validation = evaluate_membership(
        contract, "exp3_online_validation_replacement_chain", common
    )
    assert validation.status is MembershipStatus.INCLUDED
    death = evaluate_membership(
        contract,
        "exp3_online_worker_death_requeue_chain",
        {**common, "recovery_source_kind": "worker_death_requeue"},
    )
    assert death.status is MembershipStatus.INCLUDED


def test_exp3_wasted_tokens_require_discarded_real_dispatch_and_new_attempt_chain() -> None:
    contract = load_paper_metric_contract()
    actual = {
        "member_kind": "online_recovery_original_attempt",
        "recovery_source_kind": "worker_death_requeue",
        "original_attempt_id": "attempt-1",
        "replacement_attempt_id": "attempt-2",
        "original_task_unit_id": "unit-1",
        "replacement_task_unit_id": "unit-1",
        "original_attempt_ordinal": 0,
        "replacement_attempt_ordinal": 1,
        "original_provider_dispatch_succeeded": True,
        "original_attempt_discarded_after_fault": True,
        "original_attempt_total_tokens": 120,
        "fault_or_death_at_ms": 20,
        "replacement_attempt_created_at_ms": 21,
        "replacement_provider_dispatch_at_ms": 22,
        "replacement_has_independent_raw_or_failure": True,
        "replacement_has_independent_provenance": True,
        "replacement_has_independent_usage": True,
        "original_provider_response_id": "resp-1",
        "replacement_provider_response_id": "resp-2",
        "current_provider_roles": ONLINE_ROLES,
        "ordered_evidence_roles": (
            "fault_or_death_event",
            "new_attempt",
            "provider_dispatch",
            "raw_or_failure",
            "provenance",
            "usage",
            "model_record",
        ),
    }
    result = recompute_metric(
        contract,
        "exp3_online_recovery",
        "wasted_actual_tokens",
        row_kind="online_recovery_summary",
        bundle=_bundle(member_facts={"attempt-1": actual}),
    )
    assert result.value == Decimal(120)

    prefetched = recompute_metric(
        contract,
        "exp3_online_recovery",
        "wasted_actual_tokens",
        row_kind="online_recovery_summary",
        bundle=_bundle(
            member_facts={
                "attempt-1": {**actual, "original_provider_dispatch_succeeded": False}
            }
        ),
    )
    assert prefetched.value == Decimal(0)

    missing_chain = recompute_metric(
        contract,
        "exp3_online_recovery",
        "wasted_actual_tokens",
        row_kind="online_recovery_summary",
        bundle=_bundle(
            member_facts={
                "attempt-1": {
                    key: value
                    for key, value in actual.items()
                    if key != "replacement_has_independent_usage"
                }
            }
        ),
    )
    assert missing_chain.value is None and missing_chain.publish_blocked


def test_exp4_modes_row_kinds_denominators_and_specialized_selectors_are_exact() -> None:
    contract = load_paper_metric_contract()
    table = contract.require_table("exp4_ablation")
    assert table.row_kinds == ("mode_summary", "paired_transition", "mode_specific")

    for metric_id in (
        "preregistered_root_count",
        "final_result_root_count",
        "verified_correct_root_count",
        "completion_rate",
        "end_to_end_verified_success_rate",
    ):
        metric = contract.require_metric(table.table_id, metric_id)
        assert metric.row_kind == "mode_summary"
        assert metric.row_gate_membership_id == "exp4_all_five_mode_summary_row"

    for metric_id in (
        "full_success_ablation_success",
        "full_success_ablation_failure",
        "full_failure_ablation_success",
        "full_failure_ablation_failure",
    ):
        assert contract.require_metric(
            table.table_id, metric_id
        ).row_kind == "paired_transition"

    exact_modes = {
        "independently_labeled_invalid_candidate_count": "NO_VERIFICATION",
        "wrong_canonical_acceptance_count": "NO_VERIFICATION",
        "raw_only_exposure_count": "NO_PARSER_POLICY",
        "raw_only_acceptance_count": "NO_PARSER_POLICY",
        "stuck_task_count": "NO_REQUEUE",
        "premature_merge_attempt_count": "NO_MERGE_GATE",
    }
    for metric_id, mode in exact_modes.items():
        metric = contract.require_metric(table.table_id, metric_id)
        assert metric.row_kind == "mode_specific"
        assert contract.require_membership(metric.row_gate_membership_id).mode_value == mode

    wrong_mode = recompute_metric(
        contract,
        table.table_id,
        "raw_only_exposure_count",
        row_kind="mode_specific",
        bundle=_bundle(
            row_facts={"ablation_mode": "NO_REQUEUE"},
            member_facts={
                "event-1": {
                    "member_kind": "raw_only_exposure_event",
                    "parser_policy_disabled": True,
                    "raw_candidate_exposed": True,
                }
            },
        ),
    )
    assert wrong_mode.value is None and not wrong_mode.publish_blocked
    assert wrong_mode.reason == "not_applicable_wrong_mode"


def test_exp5_has_exactly_two_model_summary_tables_and_rejects_repeat_rows() -> None:
    contract = load_paper_metric_contract()
    exp5_tables = tuple(
        table for table in contract.tables if table.experiment_id == "experiment_5"
    )
    assert tuple(table.table_id for table in exp5_tables) == (
        "exp5_quality",
        "exp5_resources",
    )
    assert all(table.row_kinds == ("model_summary",) for table in exp5_tables)

    resources = contract.require_table("exp5_resources")
    assert resources.numeric_output_fields == EXPECTED_TABLE_FIELDS["exp5_resources"]
    with pytest.raises(ValueError, match="row kind mismatch"):
        recompute_metric(
            contract,
            resources.table_id,
            "repeat0_wall_clock_ms",
            row_kind="repeat_observation",
            bundle=_bundle(row_facts={"max_retries": 0}),
        )


def test_exp5_resources_sum_three_repeats_and_compute_explicit_wallclock_summary() -> None:
    contract = load_paper_metric_contract()
    observations = (
        _observation("exp5_resources", "planned_first_attempt_ai_unit_count", 6),
        _observation("exp5_resources", "actual_first_provider_attempt_count", 3),
        _observation("exp5_resources", "repeat0_wall_clock_ms", 100, NONNEGATIVE),
        _observation("exp5_resources", "repeat1_wall_clock_ms", 300, NONNEGATIVE),
        _observation("exp5_resources", "repeat2_wall_clock_ms", 200, NONNEGATIVE),
    )
    members = {
        "attempt-0": {
            "member_kind": "exp5_provider_attempt",
            "repeat_id": 0,
            "planned_call": True,
            "actual_call": True,
            "actual_total_tokens": 10,
            "actual_cost_estimate_cny": Decimal("0.1"),
            "current_provider_roles": ONLINE_ROLES,
        },
        "attempt-1": {
            "member_kind": "exp5_provider_attempt",
            "repeat_id": 1,
            "planned_call": True,
            "actual_call": True,
            "actual_total_tokens": 20,
            "actual_cost_estimate_cny": Decimal("0.2"),
            "current_provider_roles": ONLINE_ROLES,
        },
        "attempt-2": {
            "member_kind": "exp5_provider_attempt",
            "repeat_id": 2,
            "planned_call": True,
            "actual_call": True,
            "actual_total_tokens": 30,
            "actual_cost_estimate_cny": Decimal("0.3"),
            "current_provider_roles": ONLINE_ROLES,
        },
        "root-0": {
            "member_kind": "exp5_preregistered_root",
            "repeat_id": 0,
            "protocol_first_dispatch_at_ms": 10,
            "root_terminal_at_ms": 110,
        },
        "root-0b": {
            "member_kind": "exp5_preregistered_root",
            "repeat_id": 0,
            "protocol_first_dispatch_at_ms": 10,
            "root_terminal_at_ms": 90,
        },
        "root-1": {
            "member_kind": "exp5_preregistered_root",
            "repeat_id": 1,
            "protocol_first_dispatch_at_ms": 20,
            "root_terminal_at_ms": 320,
        },
        "root-2": {
            "member_kind": "exp5_preregistered_root",
            "repeat_id": 2,
            "protocol_first_dispatch_at_ms": 30,
            "root_terminal_at_ms": 230,
        },
    }
    bundle = _bundle(
        row_facts={
            "max_retries": 0,
            "replacement_attempts_allowed": False,
            "model_endpoint_id": "model-a",
        },
        member_facts=members,
        observations=observations,
    )
    expected = {
        "actual_total_tokens": Decimal(60),
        "actual_cost_estimate_cny": Decimal("0.6"),
        "first_attempt_call_coverage": Decimal("0.5"),
        "repeat0_wall_clock_ms": Decimal(100),
        "repeat1_wall_clock_ms": Decimal(300),
        "repeat2_wall_clock_ms": Decimal(200),
        "model_wall_clock_median_ms": Decimal(200),
        "model_wall_clock_min_ms": Decimal(100),
        "model_wall_clock_max_ms": Decimal(300),
        "model_wall_clock_range_ms": Decimal(200),
    }
    for metric_id, expected_value in expected.items():
        result = recompute_metric(
            contract,
            "exp5_resources",
            metric_id,
            row_kind="model_summary",
            bundle=bundle,
        )
        assert result.value == expected_value, metric_id


def test_exp1_enclosing_elapsed_usage_and_failure_partition_block_on_missing_evidence() -> None:
    contract = load_paper_metric_contract()
    assert contract.require_table("exp1_feasibility").row_scope == (
        "domain_x_difficulty_x_topic_family_x_repeat"
    )
    assert all(
        metric.row_scope == "domain_x_difficulty_x_topic_family_x_repeat"
        for metric in contract.metrics
        if metric.table == "exp1_feasibility"
    )
    observations = (
        _observation("exp1_feasibility", "no_final_failure_count", 1),
        _observation("exp1_feasibility", "incorrect_final_failure_count", 1),
        _observation("exp1_feasibility", "infra_invalid_failure_count", 0),
        _observation("exp1_feasibility", "failure_root_count", 2),
    )
    members = {
        "root-1": {
            "member_kind": "preregistered_root",
            "root_start_at_ms": 100,
                "root_terminal_at_ms": 500,
                "end_to_end_verified_success": False,
                "failure_class": "no_final",
            "failure_stage": "terminal",
            "failure_kind": "no_final",
            "infra_invalid": False,
        },
        "root-2": {
            "member_kind": "preregistered_root",
            "root_start_at_ms": 200,
                "root_terminal_at_ms": 550,
                "end_to_end_verified_success": False,
                "failure_class": "incorrect_final",
            "failure_stage": "correctness",
            "failure_kind": "incorrect_final",
            "infra_invalid": False,
        },
        "attempt-1": {
            "member_kind": "actual_provider_attempt",
            "provider_latency_ms": 10,
            "total_tokens": 20,
            "cost_estimate_cny": Decimal("0.2"),
            "current_provider_roles": ONLINE_ROLES,
        },
        "attempt-2": {
            "member_kind": "actual_provider_attempt",
            "provider_latency_ms": 15,
            "total_tokens": 30,
            "cost_estimate_cny": Decimal("0.3"),
            "current_provider_roles": ONLINE_ROLES,
        },
    }
    bundle = _bundle(
        row_facts={
            "domain": "factorization",
            "paper_difficulty": "hard",
            "topic_family": None,
            "repeat_id": 0,
            "evidence_class": "online_real_provider",
        },
        member_facts=members,
        observations=observations,
    )
    expected = {
        "actual_end_to_end_wall_clock_ms": Decimal(450),
        "actual_provider_latency_ms": Decimal(25),
        "actual_total_tokens": Decimal(50),
        "actual_cost_estimate_cny": Decimal("0.5"),
    }
    for metric_id, value in expected.items():
        assert recompute_metric(
            contract,
            "exp1_feasibility",
            metric_id,
            row_kind="condition_summary",
            bundle=bundle,
        ).value == value

    partial = copy.deepcopy(members)
    del partial["attempt-2"]["total_tokens"]
    missing_usage = recompute_metric(
        contract,
        "exp1_feasibility",
        "actual_total_tokens",
        row_kind="condition_summary",
        bundle=_bundle(
            row_facts={"evidence_class": "online_real_provider"},
            member_facts=partial,
            observations=observations,
        ),
    )
    assert missing_usage.value is None and missing_usage.publish_blocked

    missing_roles = copy.deepcopy(members)
    del missing_roles["attempt-2"]["current_provider_roles"]
    role_blocked = recompute_metric(
        contract,
        "exp1_feasibility",
        "actual_total_tokens",
        row_kind="condition_summary",
        bundle=_bundle(
            row_facts={"evidence_class": "online_real_provider"},
            member_facts=missing_roles,
            observations=observations,
        ),
    )
    assert role_blocked.value is None and role_blocked.publish_blocked
    assert role_blocked.reason == "missing_current_provider_roles:attempt-2"

    missing_time = copy.deepcopy(members)
    del missing_time["root-2"]["root_terminal_at_ms"]
    elapsed = recompute_metric(
        contract,
        "exp1_feasibility",
        "actual_end_to_end_wall_clock_ms",
        row_kind="condition_summary",
        bundle=_bundle(
            row_facts={"evidence_class": "online_real_provider"},
            member_facts=missing_time,
            observations=observations,
        ),
    )
    assert elapsed.value is None and elapsed.publish_blocked

    infra = recompute_metric(
        contract,
        "exp1_feasibility",
        "failure_root_count",
        row_kind="condition_summary",
        bundle=_bundle(
            row_facts={
                "infra_invalid": True,
                "evidence_class": "online_real_provider",
            },
            member_facts=members,
            observations=observations,
        ),
    )
    assert infra.value is None and infra.publish_blocked
    assert infra.audit_denominator_member_ids


def test_retired_alias_table_registry_and_claim_role_projection_fail_closed() -> None:
    contract = load_paper_metric_contract()
    for alias in ("raw_only_count", "premature_merge_count"):
        assert alias in contract.retired_metric_ids
        with pytest.raises(ValueError, match="retired metric id"):
            contract.validate_registry_metric_keys((("exp4_ablation", alias),))

    with pytest.raises(ValueError, match="requires composite"):
        contract.validate_registry_metric_keys(("completion_rate",))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unknown paper metric definition"):
        contract.require_metric("exp5_resources", "completion_rate")
    contract.validate_registry_metric_keys(
        (("exp1_feasibility", "completion_rate"),)
    )

    with pytest.raises(ValueError, match="primary claim role required"):
        contract.validate_main_claim_projection(
            (("exp3_trace_robustness", "replacement_attempt_success_rate"),)
        )
    with pytest.raises(ValueError, match="main table claim role required"):
        contract.validate_main_table_projection(
            (("exp3_trace_robustness", "kill_progress_error_pp"),)
        )


def test_loader_is_closed_duplicate_safe_nonfinite_safe_immutable_and_digest_bound(
    tmp_path: Path,
) -> None:
    contract = load_paper_metric_contract()
    _assert_deeply_immutable(contract)
    assert contract.provider_calls == 0

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":"x","nested":{"a":1,"a":2}}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_paper_metric_contract(duplicate)

    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_text('{"value":NaN}', encoding="utf-8")
    with pytest.raises(ValueError, match="non-finite JSON constant"):
        load_paper_metric_contract(nonfinite)

    body = _contract_body()
    body["unknown"] = True
    _reseal(body)
    closed = tmp_path / "closed.json"
    _write(closed, body)
    with pytest.raises(ValueError, match="schema drift"):
        load_paper_metric_contract(closed)

    body = _contract_body()
    body["tables"][0]["numeric_output_fields"].append("rogue")
    _reseal(body)
    drift = tmp_path / "drift.json"
    _write(drift, body)
    with pytest.raises(ValueError, match="uncontracted numeric output field: rogue"):
        load_paper_metric_contract(drift)

    body = _contract_body()
    raw_only = next(
        metric
        for metric in body["metrics"]
        if metric["table"] == "exp4_ablation"
        and metric["metric_id"] == "raw_only_acceptance_count"
    )
    raw_only["deprecated_aliases"] = ["raw_only_count"]
    _reseal(body)
    retired_alias = tmp_path / "retired-alias.json"
    _write(retired_alias, body)
    with pytest.raises(ValueError, match="retired metric alias cannot be mapped"):
        load_paper_metric_contract(retired_alias)

    body = _contract_body()
    body["contract_digest"] = "sha256:" + "0" * 64
    digest = tmp_path / "digest.json"
    _write(digest, body)
    with pytest.raises(ValueError, match="digest drift"):
        load_paper_metric_contract(digest)


def test_formula_matrix_has_only_evaluator_executable_operand_paths() -> None:
    contract = load_paper_metric_contract()
    allowed_row_primitives = {
        "runtime_elapsed_ms",
        "witness_in_flight_count",
        "scheduler_peak_active_count",
        "configured_worker_count",
    }
    for metric in contract.metrics:
        key = f"{metric.table}.{metric.metric_id}"
        for operand in metric.formula.operands:
            if metric.formula.op == "count":
                assert (operand.kind, operand.id) == ("member_set", "members"), key
            if operand.kind == "number_sequence":
                assert operand.id.startswith("members."), key
            if operand.id.startswith("row."):
                leaf = operand.id.removeprefix("row.")
                assert leaf in allowed_row_primitives, key
                assert not leaf.endswith("_numerator_membership"), key
                assert "speedup" not in leaf, key


def test_all_memberships_are_referenced_and_cross_experiment_selectors_are_forbidden() -> None:
    contract = load_paper_metric_contract()
    references = {
        membership_id
        for metric in contract.metrics
        for membership_id in (
            metric.source_membership_id,
            metric.row_gate_membership_id,
        )
        if membership_id is not None
    }
    assert {membership.membership_id for membership in contract.memberships} == references
    for metric in contract.metrics:
        selector = metric.source_membership_id or ""
        if metric.table.startswith(("exp2_", "exp3_")):
            assert not selector.startswith("exp5_"), (
                f"{metric.table}.{metric.metric_id} -> {selector}"
            )


def test_reviewer_counterexamples_use_real_selectors_and_executable_formulas() -> None:
    contract = load_paper_metric_contract()
    controlled_wrong = recompute_metric(
        contract,
        "exp3_trace_robustness",
        "controlled_wrong_candidate_count",
        row_kind="condition_observation",
        bundle=_bundle(
            row_facts={"fault_type": "false_positive"},
            member_facts={"root": {"member_kind": "preregistered_root"}},
        ),
    )
    assert controlled_wrong.value == 0

    exp2_attempt = {
        "member_kind": "exp2_online_first_provider_attempt",
        "actual_call": True,
        "current_provider_roles": ONLINE_ROLES,
    }
    actual_first = recompute_metric(
        contract,
        "exp2_online_concurrency",
        "actual_first_provider_attempt_count",
        row_kind="worker_condition_summary",
        bundle=_bundle(member_facts={"attempt": exp2_attempt}),
    )
    assert actual_first.value == 1

    discarded = {
        f"trace-{index}": {
            "member_kind": "exp3_discarded_trace_consumption",
            "discarded_after_fault": True,
            "source_usage_total_tokens": tokens,
            "source_bank_roles": TRACE_ROLES,
        }
        for index, tokens in enumerate((10, 20))
    }
    discarded_tokens = recompute_metric(
        contract,
        "exp3_trace_robustness",
        "discarded_trace_tokens",
        row_kind="condition_observation",
        bundle=_bundle(member_facts=discarded),
    )
    assert discarded_tokens.value == Decimal(30)

    transition = recompute_metric(
        contract,
        "exp4_ablation",
        "full_success_ablation_failure",
        row_kind="paired_transition",
        bundle=_bundle(
            row_facts={
                "ablation_mode": "NO_VERIFICATION",
                "pair_evidence_complete": True,
            },
            member_facts={
                "pair": {
                    "member_kind": "exp4_paired_root",
                    "pair_evidence_complete": True,
                    "full_success": True,
                    "ablation_success": False,
                }
            },
        ),
    )
    assert transition.value == 1

    direct_integer = recompute_metric(
        contract,
        "exp2_trace_scalability",
        "in_flight_at_witness",
        row_kind="worker_repeat_observation",
        bundle=_bundle(row_facts={"witness_in_flight_count": 3}),
    )
    assert direct_integer.value == 3
    assert isinstance(direct_integer.value, int)


def test_exp2_pair_missing_evidence_blocks_and_median_ignores_self_reported_speedup() -> None:
    contract = load_paper_metric_contract()
    pair = _exp2_pair(speedup=999, compared_success=True)
    del pair["baseline_time_evidence_complete"]
    missing = recompute_metric(
        contract,
        "exp2_trace_scalability",
        "trace_replay_paired_speedup",
        row_kind="paired_worker_comparison",
        bundle=_bundle(member_facts={"pair": pair}),
    )
    assert missing.value is None and missing.publish_blocked
    assert missing.reason == "incomplete_pair"

    members = {
        "pair-1": {
            **_exp2_pair(speedup=999, compared_success=True),
            "baseline_trace_replay_wall_clock_ms": 1200,
            "compared_trace_replay_wall_clock_ms": 600,
        },
        "pair-2": {
            **_exp2_pair(speedup=999, compared_success=True),
            "baseline_trace_replay_wall_clock_ms": 900,
            "compared_trace_replay_wall_clock_ms": 300,
            "baseline_sample_slot_index": 1,
            "compared_sample_slot_index": 1,
        },
    }
    result = recompute_metric(
        contract,
        "exp2_trace_scalability",
        "trace_replay_paired_speedup_median",
        row_kind="repeat_summary",
        bundle=_bundle(row_facts={"summary_kind": "repeat"}, member_facts=members),
    )
    assert result.value == Decimal("2.5")


def test_required_invariant_uses_current_member_partition_not_supplied_observations() -> None:
    contract = load_paper_metric_contract()
    open_pair = _exp2_pair(speedup=2, compared_success=False, exclusion_reason=None)
    claimed_partition = (
        _observation("exp2_trace_scalability", "paired_speedup_eligible_pair_count", 1),
        _observation("exp2_trace_scalability", "paired_speedup_ineligible_pair_count", 0),
        _observation("exp2_trace_scalability", "paired_speedup_planned_pair_count", 1),
    )
    result = evaluate_invariant(
        contract,
        "exp2_pair_membership_partition",
        _bundle(member_facts={"open": open_pair}, observations=claimed_partition),
    )
    assert not result.satisfied and result.publish_blocked
    assert result.violation_reason == "exp2_pair_memberships_do_not_partition_planned_pairs"


def test_exp4_zero_denominator_is_explicit_na_not_publication_block() -> None:
    contract = load_paper_metric_contract()
    observations = (
        _observation("exp4_ablation", "raw_only_acceptance_count", 0),
        _observation("exp4_ablation", "raw_only_exposure_count", 0),
    )
    result = recompute_metric(
        contract,
        "exp4_ablation",
        "raw_only_acceptance_rate",
        row_kind="mode_specific",
        bundle=_bundle(
            row_facts={"ablation_mode": "NO_PARSER_POLICY"},
            observations=observations,
        ),
    )
    assert result.value is None and not result.publish_blocked
    assert result.reason == "zero_denominator"


def test_exp5_gate_forbids_retries_and_replacement_attempts() -> None:
    contract = load_paper_metric_contract()
    result = recompute_metric(
        contract,
        "exp5_resources",
        "planned_first_attempt_ai_unit_count",
        row_kind="model_summary",
        bundle=_bundle(
            row_facts={"max_retries": 0, "replacement_attempts_allowed": True},
            member_facts={
                "attempt": {
                    "member_kind": "exp5_provider_attempt",
                    "planned_call": True,
                    "actual_call": False,
                    "current_provider_roles": ONLINE_ROLES,
                }
            },
        ),
    )
    assert result.value is None and result.publish_blocked
    assert result.reason == "replacement_attempts_forbidden"


def test_bundle_rejects_non_json_mutable_leaves() -> None:
    class MutableLeaf:
        pass

    for value in (bytearray(b"x"), MutableLeaf(), {"not", "json"}):
        with pytest.raises(ValueError, match="immutable JSON-like"):
            _bundle(row_facts={"bad": value})


def test_producer_consumer_flow_is_closed_over_canonical_runtime_evidence() -> None:
    body = _contract_body()
    for metric in body["metrics"]:
        experiment = metric["table"].split("_", 1)[0]
        assert metric["producer"] == {
            "evidence_origin": "canonical_runtime_persisted_evidence",
            "metric_builder": f"paper_{experiment}_metrics",
        }
        assert metric["consumer"] == {
            "stages": [
                f"paper_{experiment}_metrics",
                "paper_metric_registry",
                "paper_metric_observations",
            ],
            "renderer_table": metric["table"],
        }


@pytest.mark.parametrize(
    ("table_id", "metric_id"),
    ALL_NUMERIC_CELLS,
    ids=(f"{table}.{metric}" for table, metric in ALL_NUMERIC_CELLS),
)
def test_every_registered_numeric_cell_executes_through_the_evaluator(
    table_id: str, metric_id: str
) -> None:
    contract = load_paper_metric_contract()
    metric = contract.require_metric(table_id, metric_id)
    result = recompute_metric(
        contract,
        table_id,
        metric_id,
        row_kind=metric.row_kind,
        bundle=_execution_bundle_for_metric(_contract_body(), table_id, metric_id),
    )
    assert result.value is not None, (
        f"{table_id}.{metric_id}: reason={result.reason}, "
        f"blocked={result.publish_blocked}, members={result.member_decisions}"
    )
    assert result.paper_eligible is False


def test_heterogeneous_bundle_preserves_root_candidate_attempt_and_slot_identity() -> None:
    body = _contract_body()
    facts = {
        "root": _membership_facts(body, "all_preregistered_roots"),
        "candidate": _membership_facts(body, "exp3_controlled_wrong_candidate"),
        "attempt": _membership_facts(body, "exp2_online_actual_first_attempt"),
        "slot": _membership_facts(body, "exp3_recovered_required_slot"),
    }
    contract = load_paper_metric_contract()
    cells = (
        (
            "exp1_feasibility",
            "preregistered_root_count",
            "condition_summary",
            None,
        ),
        (
            "exp3_trace_robustness",
            "controlled_wrong_candidate_count",
            "condition_observation",
            "false_positive",
        ),
        (
            "exp2_online_concurrency",
            "actual_first_provider_attempt_count",
            "worker_condition_summary",
            None,
        ),
        (
            "exp3_trace_robustness",
            "recovered_valid_canonical_required_slots",
            "condition_observation",
            "worker_death",
        ),
    )
    for table_id, metric_id, row_kind, fault_type in cells:
        result = recompute_metric(
            contract,
            table_id,
            metric_id,
            row_kind=row_kind,
            bundle=_bundle(
                row_facts={
                    **({"fault_type": fault_type} if fault_type else {}),
                    **(
                        {"evidence_class": "online_real_provider"}
                        if table_id == "exp1_feasibility"
                        else {}
                    ),
                },
                member_facts=facts,
            ),
        )
        assert result.value == 1, f"{table_id}.{metric_id}: {result}"
        assert result.included_member_ids == (
            {
                "preregistered_root_count": "root",
                "controlled_wrong_candidate_count": "candidate",
                "actual_first_provider_attempt_count": "attempt",
                "recovered_valid_canonical_required_slots": "slot",
            }[metric_id],
        )


def test_loader_rejects_unexecutable_formula_paths_and_observation_cycles(
    tmp_path: Path,
) -> None:
    body = _contract_body()
    metric = next(
        item
        for item in body["metrics"]
        if item["table"] == "exp1_feasibility"
        and item["metric_id"] == "preregistered_root_count"
    )
    metric["formula"]["operands"] = [
        {"kind": "member_set", "id": "row.runtime_elapsed_ms"}
    ]
    metric["numerator"] = metric["formula"]["operands"][0]
    _reseal(body)
    bad_count = tmp_path / "bad-count-path.json"
    _write(bad_count, body)
    with pytest.raises(ValueError, match="unexecutable count formula"):
        load_paper_metric_contract(bad_count)

    body = _contract_body()
    completion = next(
        item
        for item in body["metrics"]
        if item["table"] == "exp1_feasibility"
        and item["metric_id"] == "completion_rate"
    )
    success = next(
        item
        for item in body["metrics"]
        if item["table"] == "exp1_feasibility"
        and item["metric_id"] == "end_to_end_verified_success_rate"
    )
    completion["formula"]["operands"][0]["id"] = (
        "observation.exp1_feasibility.end_to_end_verified_success_rate"
    )
    completion["numerator"] = completion["formula"]["operands"][0]
    success["formula"]["operands"][0]["id"] = (
        "observation.exp1_feasibility.completion_rate"
    )
    success["numerator"] = success["formula"]["operands"][0]
    _reseal(body)
    cycle = tmp_path / "observation-cycle.json"
    _write(cycle, body)
    with pytest.raises(ValueError, match="metric dependency cycle"):
        load_paper_metric_contract(cycle)

    body = _contract_body()
    median = next(
        item
        for item in body["metrics"]
        if item["table"] == "exp5_resources"
        and item["metric_id"] == "model_wall_clock_median_ms"
    )
    median["formula"]["operands"][0]["id"] = (
        "observation.exp5_quality.completion_rate"
    )
    median["numerator"] = median["formula"]["operands"][0]
    _reseal(body)
    cross_row = tmp_path / "cross-row-dependency.json"
    _write(cross_row, body)
    with pytest.raises(ValueError, match="cross-row observation dependency"):
        load_paper_metric_contract(cross_row)


def test_observation_alignment_and_metric_output_decisions_are_immutable() -> None:
    observation = _observation(
        "exp1_feasibility", "preregistered_root_count", 1
    )
    with pytest.raises(ValueError, match="row identity digest mismatch"):
        MetricObservationBundle(
            row_facts={"infra_invalid": False},
            member_ids=("source-1",),
            member_facts_by_id={"source-1": {"member_kind": "audit_anchor"}},
            verified_observations=(observation,),
            row_identity_digest="sha256:" + "2" * 64,
        )

    wrong_row = dataclasses.replace(observation, row_kind="wrong_row_kind")
    result = recompute_metric(
        load_paper_metric_contract(),
        "exp1_feasibility",
        "completion_rate",
        row_kind="condition_summary",
        bundle=_bundle(
            row_facts={"evidence_class": "online_real_provider"},
            observations=(
                wrong_row,
                _observation("exp1_feasibility", "final_result_root_count", 1),
            )
        ),
    )
    assert result.value is None and result.publish_blocked
    assert result.reason == (
        "missing_verified_observation:exp1_feasibility.preregistered_root_count"
    )
    assert result.paper_eligible is False
    assert isinstance(result.member_decisions, tuple)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.member_decisions[0].reason = "changed"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.paper_eligible = True  # type: ignore[misc]

    wrong_domain = dataclasses.replace(observation, value_domain=NONNEGATIVE)
    wrong_domain_result = recompute_metric(
        load_paper_metric_contract(),
        "exp1_feasibility",
        "completion_rate",
        row_kind="condition_summary",
        bundle=_bundle(
            row_facts={"evidence_class": "online_real_provider"},
            observations=(
                wrong_domain,
                _observation("exp1_feasibility", "final_result_root_count", 1),
            )
        ),
    )
    assert wrong_domain_result.value is None
    assert wrong_domain_result.publish_blocked


@pytest.mark.parametrize(
    "fault_type",
    (
        "false_negative",
        "no_return",
        "late_submission",
        "executor_error",
        "worker_death",
    ),
)
def test_exp3_false_positive_metrics_are_explicit_na_for_other_conditions(
    fault_type: str,
) -> None:
    contract = load_paper_metric_contract()
    body = _contract_body()
    result = recompute_metric(
        contract,
        "exp3_trace_robustness",
        "controlled_wrong_candidate_count",
        row_kind="condition_observation",
        bundle=_bundle(
            row_facts={"fault_type": fault_type},
            member_facts={
                "candidate": _membership_facts(
                    body, "exp3_controlled_wrong_candidate"
                )
            },
        ),
    )
    assert result.value is None
    assert result.reason == "not_applicable_non_false_positive"
    assert not result.publish_blocked


def test_exp3_applicability_missing_evidence_blocks_and_worker_metrics_are_death_only() -> None:
    contract = load_paper_metric_contract()
    body = _contract_body()
    controlled_facts = _membership_facts(body, "exp3_controlled_wrong_candidate")
    false_positive = recompute_metric(
        contract,
        "exp3_trace_robustness",
        "controlled_wrong_candidate_count",
        row_kind="condition_observation",
        bundle=_bundle(
            row_facts={"fault_type": "false_positive"},
            member_facts={"candidate": controlled_facts},
        ),
    )
    assert false_positive.value == 1

    missing_kind = recompute_metric(
        contract,
        "exp3_trace_robustness",
        "controlled_wrong_candidate_count",
        row_kind="condition_observation",
        bundle=_bundle(member_facts={"candidate": controlled_facts}),
    )
    assert missing_kind.value is None and missing_kind.publish_blocked
    assert missing_kind.reason == "missing_applicability_evidence:fault_type"

    slot_facts = _membership_facts(body, "exp3_preregistered_required_slot")
    rate_fault = recompute_metric(
        contract,
        "exp3_trace_robustness",
        "preregistered_required_slots",
        row_kind="condition_observation",
        bundle=_bundle(
            row_facts={"fault_type": "false_negative"},
            member_facts={"slot": slot_facts},
        ),
    )
    assert rate_fault.value is None
    assert rate_fault.reason == "not_applicable_rate_fault"
    assert not rate_fault.publish_blocked

    death = recompute_metric(
        contract,
        "exp3_trace_robustness",
        "preregistered_required_slots",
        row_kind="condition_observation",
        bundle=_bundle(
            row_facts={"fault_type": "worker_death"},
            member_facts={"slot": slot_facts},
        ),
    )
    assert death.value == 1


def test_exp3_retry_exhausted_replacement_counts_as_started_and_reassigned() -> None:
    contract = load_paper_metric_contract()
    retry_exhausted = {
        "member_kind": "replacement_attempt",
        "fault_or_death_id": "fault-1",
        "fault_or_death_task_unit_id": "unit-1",
        "fault_or_death_attempt_id": "attempt-1",
        "original_task_unit_id": "unit-1",
        "replacement_task_unit_id": "unit-1",
        "original_attempt_id": "attempt-1",
        "replacement_attempt_id": "attempt-2",
        "original_attempt_ordinal": 0,
        "replacement_attempt_ordinal": 1,
        "new_attempt_fault_or_death_id": "fault-1",
        "new_attempt_task_unit_id": "unit-1",
        "new_attempt_id": "attempt-2",
        "new_attempt_ordinal": 1,
        "original_worker_id": "worker-1",
        "replacement_worker_id": "worker-2",
        "ordered_evidence_roles": (
            "fault_or_death_event",
            "new_attempt",
            "provider_dispatch",
            "raw_or_failure",
            "provenance",
            "usage",
            "model_record",
        ),
        "replacement_result_qualified": False,
        "original_task_unit_completed": False,
    }
    bundle = _bundle(member_facts={"retry-exhausted": retry_exhausted})

    started = recompute_metric(
        contract,
        "exp3_trace_robustness",
        "started_replacement_attempt_count",
        row_kind="condition_observation",
        bundle=bundle,
    )
    successful = recompute_metric(
        contract,
        "exp3_trace_robustness",
        "successful_replacement_attempt_count",
        row_kind="condition_observation",
        bundle=bundle,
    )
    reassigned = recompute_metric(
        contract,
        "exp3_trace_robustness",
        "reassignment_count",
        row_kind="condition_observation",
        bundle=bundle,
    )

    assert started.value == 1
    assert successful.value == 0
    assert reassigned.value == 1

    mismatched = dict(retry_exhausted)
    mismatched["new_attempt_fault_or_death_id"] = "fault-unrelated"
    mismatched_bundle = _bundle(member_facts={"mismatched": mismatched})
    for metric_id in (
        "started_replacement_attempt_count",
        "reassignment_count",
    ):
        result = recompute_metric(
            contract,
            "exp3_trace_robustness",
            metric_id,
            row_kind="condition_observation",
            bundle=mismatched_bundle,
        )
        assert result.value == 0
        assert result.excluded_member_ids == ("mismatched",)


@pytest.mark.parametrize(
    "fault_type",
    (
        "false_positive",
        "false_negative",
        "no_return",
        "late_submission",
        "executor_error",
    ),
)
def test_exp3_worker_death_metrics_are_explicit_na_for_every_rate_fault(
    fault_type: str,
) -> None:
    body = _contract_body()
    result = recompute_metric(
        load_paper_metric_contract(),
        "exp3_trace_robustness",
        "preregistered_required_slots",
        row_kind="condition_observation",
        bundle=_bundle(
            row_facts={"fault_type": fault_type},
            member_facts={
                "slot": _membership_facts(body, "exp3_preregistered_required_slot")
            },
        ),
    )
    assert result.value is None
    assert result.reason == "not_applicable_rate_fault"
    assert not result.publish_blocked


def test_exp3_replacement_success_zero_started_is_legal_explicit_na() -> None:
    result = recompute_metric(
        load_paper_metric_contract(),
        "exp3_trace_robustness",
        "replacement_attempt_success_rate",
        row_kind="condition_observation",
        bundle=_bundle(
            row_facts={"fault_type": "false_negative"},
            observations=(
                _observation(
                    "exp3_trace_robustness",
                    "successful_replacement_attempt_count",
                    0,
                ),
                _observation(
                    "exp3_trace_robustness", "started_replacement_attempt_count", 0
                ),
            ),
        ),
    )
    assert result.value is None
    assert result.reason == "not_applicable_no_started_replacement"
    assert not result.publish_blocked


def test_exp1_failure_root_is_independent_and_unclassified_failure_blocks_partition() -> None:
    contract = load_paper_metric_contract()
    unclassified = {
        "member_kind": "preregistered_root",
        "end_to_end_verified_success": False,
        "failure_class": "unclassified",
        "failure_stage": "unknown",
        "failure_kind": "unknown",
        "current_provider_roles": ONLINE_ROLES,
    }
    failure = evaluate_membership(contract, "exp1_failure_root", unclassified)
    assert failure.status is MembershipStatus.INCLUDED
    invariant = evaluate_invariant(
        contract,
        "exp1_failure_class_partition",
        _bundle(member_facts={"root": unclassified}),
    )
    assert not invariant.satisfied and invariant.publish_blocked
    assert invariant.violation_reason == (
        "exp1_failure_classes_not_mutually_exclusive_and_exhaustive"
    )

    result = recompute_metric(
        contract,
        "exp1_feasibility",
        "failure_root_count",
        row_kind="condition_summary",
        bundle=_bundle(
            row_facts={"evidence_class": "online_real_provider"},
            member_facts={"root": unclassified},
        ),
    )
    assert result.value is None and result.publish_blocked

    inconsistent = {
        **unclassified,
        "failure_class": "no_final",
        "failure_stage": "correctness",
        "failure_kind": "no_final",
    }
    inconsistent_invariant = evaluate_invariant(
        contract,
        "exp1_failure_class_partition",
        _bundle(member_facts={"root": inconsistent}),
    )
    assert not inconsistent_invariant.satisfied
    assert inconsistent_invariant.publish_blocked


def test_exp5_planned_inventory_is_distinct_from_actual_provider_attempts() -> None:
    contract = load_paper_metric_contract()
    facts = {
        "planned-1": {"member_kind": "exp5_planned_ai_unit", "planned_call": True},
        "planned-2": {"member_kind": "exp5_planned_ai_unit", "planned_call": True},
        "actual-1": {
            "member_kind": "exp5_provider_attempt",
            "actual_call": True,
            "actual_total_tokens": 11,
            "actual_cost_estimate_cny": Decimal("0.5"),
            "current_provider_roles": ONLINE_ROLES,
        },
    }
    bundle = _bundle(
        row_facts={"max_retries": 0, "replacement_attempts_allowed": False},
        member_facts=facts,
    )
    planned = recompute_metric(
        contract,
        "exp5_resources",
        "planned_first_attempt_ai_unit_count",
        row_kind="model_summary",
        bundle=bundle,
    )
    actual = recompute_metric(
        contract,
        "exp5_resources",
        "actual_first_provider_attempt_count",
        row_kind="model_summary",
        bundle=bundle,
    )
    tokens = recompute_metric(
        contract,
        "exp5_resources",
        "actual_total_tokens",
        row_kind="model_summary",
        bundle=bundle,
    )
    assert planned.value == 2
    assert actual.value == 1
    assert tokens.value == Decimal(11)

    coverage = recompute_metric(
        contract,
        "exp5_resources",
        "first_attempt_call_coverage",
        row_kind="model_summary",
        bundle=_bundle(
            row_facts={"max_retries": 0, "replacement_attempts_allowed": False},
            observations=(
                _observation(
                    "exp5_resources", "actual_first_provider_attempt_count", 1
                ),
                _observation(
                    "exp5_resources", "planned_first_attempt_ai_unit_count", 2
                ),
            ),
        ),
    )
    assert coverage.value == Decimal("0.5")


@pytest.mark.parametrize(
    ("table_id", "metric_id", "value_domain"),
    (
        ("exp5_resources", "repeat0_wall_clock_ms", NONNEGATIVE),
        ("exp1_feasibility", "completion_rate", PROPORTION),
        ("exp2_trace_scalability", "trace_replay_paired_speedup", "ratio_unbounded"),
    ),
)
def test_verified_observation_rejects_negative_nonnegative_domains(
    table_id: str, metric_id: str, value_domain: str
) -> None:
    with pytest.raises(ValueError, match="nonnegative|proportion"):
        _observation(table_id, metric_id, Decimal("-0.1"), value_domain)


def test_verified_observation_allows_negative_signed_domain() -> None:
    observation = _observation(
        "exp3_trace_robustness",
        "trace_replay_wall_clock_overhead_ms",
        Decimal("-2"),
        "signed_number",
    )
    assert observation.value == Decimal("-2")


def test_verified_observation_rejects_proportion_above_one() -> None:
    with pytest.raises(ValueError, match="proportion observation out of range"):
        _observation(
            "exp1_feasibility",
            "completion_rate",
            Decimal("1.01"),
            PROPORTION,
        )


def test_main_pair_key_is_frozen_and_nonpaired_rows_have_no_mechanical_key(
    tmp_path: Path,
) -> None:
    expected = (
        "case_record_digest",
        "repeat_id",
        "sample_slot_index",
        "baseline_worker_count",
        "compared_worker_count",
    )
    contract = load_paper_metric_contract()
    paired = contract.require_metric(
        "exp2_trace_scalability", "trace_replay_paired_speedup"
    )
    assert paired.pair_key == expected
    assert contract.require_metric(
        "exp2_trace_scalability", "completion_rate"
    ).pair_key == ()
    assert contract.require_metric(
        "exp2_trace_scalability", "trace_replay_paired_speedup_median"
    ).pair_key == ()

    body = _contract_body()
    metric = next(
        item
        for item in body["metrics"]
        if item["table"] == "exp2_trace_scalability"
        and item["metric_id"] == "trace_replay_paired_speedup"
    )
    metric["pair_key"] = ["case_record_digest"]
    _reseal(body)
    drift = tmp_path / "pair-key-drift.json"
    _write(drift, body)
    with pytest.raises(ValueError, match="pair key drift"):
        load_paper_metric_contract(drift)

    body = _contract_body()
    nonpaired = next(
        item
        for item in body["metrics"]
        if item["table"] == "exp2_trace_scalability"
        and item["metric_id"] == "completion_rate"
    )
    nonpaired["pair_key"] = list(expected)
    _reseal(body)
    mechanical = tmp_path / "nonpaired-mechanical-key.json"
    _write(mechanical, body)
    with pytest.raises(ValueError, match="pair key drift"):
        load_paper_metric_contract(mechanical)


def test_exp3_applicability_is_declared_in_the_contract() -> None:
    body = _contract_body()
    by_key = {
        (item["table"], item["metric_id"]): item for item in body["metrics"]
    }
    assert by_key[
        ("exp3_trace_robustness", "controlled_wrong_candidate_count")
    ]["applicability"] == {
        "kind": "field_in",
        "field": "fault_type",
        "values": ["false_positive"],
        "not_applicable_reason": "not_applicable_non_false_positive",
    }
    assert by_key[
        ("exp3_trace_robustness", "preregistered_required_slots")
    ]["applicability"] == {
        "kind": "field_in",
        "field": "fault_type",
        "values": ["worker_death"],
        "not_applicable_reason": "not_applicable_rate_fault",
    }


def test_loader_rejects_validly_shaped_applicability_on_the_wrong_metric(
    tmp_path: Path,
) -> None:
    body = _contract_body()
    metric = next(
        item
        for item in body["metrics"]
        if item["table"] == "exp3_trace_robustness"
        and item["metric_id"] == "controlled_wrong_candidate_count"
    )
    metric["applicability"] = {
        "kind": "field_in",
        "field": "fault_type",
        "values": ["worker_death"],
        "not_applicable_reason": "not_applicable_rate_fault",
    }
    not_applicable = next(
        rule for rule in metric["null"] if rule["trigger"] == "not_applicable"
    )
    not_applicable["reason"] = "not_applicable_rate_fault"
    _reseal(body)
    path = tmp_path / "applicability-wrong-metric.json"
    _write(path, body)

    with pytest.raises(ValueError, match="applicability metric drift"):
        load_paper_metric_contract(path)
