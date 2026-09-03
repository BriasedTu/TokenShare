"""Read-only Experiment 1 Factorization analysis extraction."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from math import isqrt
from pathlib import Path
from statistics import median
from typing import Any
from urllib.parse import quote


RUN_SCHEMA = "tokenshare.slim_v2.run_config.v1"
ROOT_INVENTORY_SCHEMA = "tokenshare.slim_v2.root_inventory.v1"
ROOT_RESULT_SCHEMA = "tokenshare.slim_v2.root_result.v2"
PROTOCOL_MATERIAL_SCHEMA = "slim_v2.protocol_material.v1"
TRACE_SCHEMA = "tokenshare.slim_v2.unit_trace.v1"
PROVIDER_TERMINAL_SCHEMA = "slim_v2.provider_terminal.v1"
SUBMISSION_SCHEMA = "phase3.execution_submission.v1"
RANGE_RESULT_SCHEMA = "factorization.range_result.v1"
ANALYSIS_SCHEMA = "tokenshare.factorization_exp1_analysis.v1"


ROOT_FIELDS = (
    "case_id",
    "root_id",
    "experiment_id",
    "condition_id",
    "root_result_relative_path",
    "original_group",
    "partition_count",
    "target_n",
    "sqrt_floor",
    "candidate_start",
    "candidate_end",
    "candidate_domain_size",
    "is_prime",
    "smallest_factor",
    "factor_position_ratio",
    "factor_position_group",
    "factor_range_index",
    "input_scale_rank",
    "input_scale_group",
    "planned_subtask_count",
    "executed_subtask_count",
    "unscheduled_subtask_count",
    "completion",
    "protocol_task_completed",
    "protocol_started",
    "root_status",
    "final_result_exists",
    "final_verified",
    "scientifically_evaluable",
    "infra_invalid",
    "no_final",
    "incorrect_final",
    "failure_kind",
    "failure_stage",
    "failure_origin",
    "total_attempts",
    "retry_count",
    "first_attempt_nonpass_count",
    "verification_rejection_count",
    "parse_failure_count",
    "provider_failure_count",
    "provider_call_count",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "provider_latency_ms",
    "end_to_end_time_ms",
    "prompt_tokens_missing_reason",
    "completion_tokens_missing_reason",
    "total_tokens_missing_reason",
    "provider_latency_missing_reason",
    "end_to_end_time_missing_reason",
    "worker_count",
    "max_retries",
    "model_name",
    "resolved_model",
    "repeat",
    "run_id",
)


ATTEMPT_FIELDS = (
    "case_id",
    "root_id",
    "partition_count",
    "is_prime",
    "smallest_factor",
    "factor_position_group",
    "input_scale_group",
    "subtask_id",
    "range_index",
    "range_lower",
    "range_upper",
    "range_width",
    "contains_smallest_factor",
    "attempt_id",
    "attempt_ordinal",
    "replacement_of_attempt_id",
    "recovery_trigger",
    "trace_origin",
    "is_required_for_final",
    "attempt_valid",
    "raw_output_returned",
    "provider_call_made",
    "parse_success",
    "verification_passed",
    "accepted_result",
    "result_kind",
    "execution_result_kind",
    "parse_result",
    "verification_result",
    "provider_error",
    "failure_type",
    "http_status",
    "call_state",
    "usage_status",
    "prompt_tokens",
    "completion_tokens",
    "attempt_tokens",
    "attempt_latency_ms",
    "attempt_elapsed_ms",
    "attempt_tokens_missing_reason",
    "attempt_latency_missing_reason",
    "attempt_elapsed_missing_reason",
    "worker_id",
    "started_at",
    "finished_at",
    "repeat",
    "run_id",
)


def _spec(
    semantic: str,
    source_files: str | tuple[str, ...],
    source_field_paths: str | tuple[str, ...],
    *,
    kind: str = "direct",
    formula: str | None = None,
    null_handling: str = "Never null for a valid extracted row.",
) -> dict[str, object]:
    return {
        "semantic": semantic,
        "source_files": [source_files]
        if isinstance(source_files, str)
        else list(source_files),
        "source_field_paths": [source_field_paths]
        if isinstance(source_field_paths, str)
        else list(source_field_paths),
        "kind": kind,
        "formula": formula,
        "null_handling": null_handling,
    }


ROOT_FIELD_SPECS = {
    "case_id": _spec("Frozen benchmark case identity.", "inventory/roots.jsonl", "$.case_id"),
    "root_id": _spec(
        "Unique runtime-style root identity.",
        ("run.json", "inventory/roots.jsonl", "roots/exp1/*/protocol.json"),
        ("$.run_id", "$.experiment_id", "$.condition_id", "$.case_id", "$.repeat_id", "$.protocol_result.run_id"),
        kind="derived_and_cross_checked",
        formula="run_id + ':' + experiment_id + ':' + condition_id + ':' + case_id + ':' + repeat_id",
    ),
    "experiment_id": _spec("Experiment identity.", "inventory/roots.jsonl", "$.experiment_id"),
    "condition_id": _spec("Frozen Experiment 1 condition identity.", "inventory/roots.jsonl", "$.condition_id"),
    "root_result_relative_path": _spec("Run-relative committed root observation path.", "roots/exp1/*/result.json", "$ filesystem path"),
    "original_group": _spec("Original benchmark stratum label; retained only as provenance, not ordinal arithmetic difficulty.", "benchmark catalog", "$.difficulty"),
    "partition_count": _spec("Actual requested count of contiguous candidate ranges.", ("benchmark catalog", "inventory/roots.jsonl"), ("$.split_params.requested_child_count", "length($.planned_ai_unit_ids)"), kind="direct_and_cross_checked"),
    "target_n": _spec("Target integer to factor or exclude factors for.", "benchmark catalog", "$.target_n", kind="parsed_integer"),
    "sqrt_floor": _spec("Floor of the square root of target_n.", "benchmark catalog", "$.target_n", kind="derived", formula="isqrt(target_n)"),
    "candidate_start": _spec("Inclusive start of the complete candidate divisor domain.", "benchmark catalog", "$.candidate_start", kind="parsed_integer"),
    "candidate_end": _spec("Inclusive end of the complete candidate divisor domain.", "benchmark catalog", "$.candidate_end", kind="parsed_integer"),
    "candidate_domain_size": _spec("Number of candidates in [2, floor(sqrt(n))].", "benchmark catalog", ("$.target_n", "$.candidate_divisor_count"), kind="derived_and_cross_checked", formula="floor(sqrt(target_n)) - 1"),
    "is_prime": _spec("Whether the frozen oracle records a prime/no-factor case.", "benchmark catalog", ("$.factor_position_quantile", "$.oracle_prime_factors", "$.target_n"), kind="derived_and_cross_checked", formula="factor_position_quantile == 'no_factor' and oracle_prime_factors == [{prime: target_n, exponent: 1}]"),
    "smallest_factor": _spec("Smallest non-trivial factor inside the complete candidate domain for a composite case.", "benchmark catalog", "$.oracle_prime_factors[*].prime", kind="derived", formula="minimum oracle prime in [candidate_start, candidate_end]", null_handling="Null for prime/no-factor cases."),
    "factor_position_ratio": _spec("Endpoint-normalized smallest-factor position used by the frozen catalog validator.", "benchmark catalog", ("$.oracle_prime_factors[*].prime", "$.candidate_start", "$.candidate_end"), kind="derived", formula="(smallest_factor - 2) / (sqrt_floor - 2)", null_handling="Null for prime/no-factor cases."),
    "factor_position_group": _spec("Native early/middle/late/no_factor catalog stratum; never re-binned.", "benchmark catalog", "$.factor_position_quantile"),
    "factor_range_index": _spec("Zero-based planned range containing smallest_factor.", ("benchmark catalog", "factorization split rule"), ("$.split_params.requested_child_count", "partition_candidate_ranges"), kind="derived_and_cross_checked", formula="index of balanced contiguous range containing smallest_factor", null_handling="Null for prime/no-factor cases."),
    "input_scale_rank": _spec("Ascending rank by candidate-domain size among 8-range composite roots.", "derived root table", ("candidate_domain_size", "target_n", "case_id"), kind="derived", formula="1-based rank of (candidate_domain_size, target_n, case_id) among partition_count=8 and is_prime=false", null_handling="Null for prime and non-8-range roots."),
    "input_scale_group": _spec("Approximate equal-count input-scale group among 8-range composite roots.", "derived root table", "input_scale_rank", kind="derived", formula="labels[floor(3 * (rank - 1) / N)] for labels small_M,middle_M,large_M", null_handling="Null for prime and non-8-range roots."),
    "planned_subtask_count": _spec("Frozen number of planned range subtasks.", "inventory/roots.jsonl", "length($.planned_ai_unit_ids)", kind="derived"),
    "executed_subtask_count": _spec("Number of distinct planned range IDs with an actual protocol attempt.", "roots/exp1/*/result.json", "distinct($.attempts[?trace_origin='protocol'].planned_ai_unit_id)", kind="derived_and_cross_checked", formula="count distinct protocol attempt planned_ai_unit_id; checked against dispatched_ai_unit_ids"),
    "unscheduled_subtask_count": _spec("Planned ranges never scheduled before root termination.", "roots/exp1/*/result.json", "length($.unscheduled_ai_unit_ids)", kind="derived"),
    "completion": _spec("Published Experiment 1 completion numerator.", "roots/exp1/*/result.json", "$.final_result_present", kind="renamed_direct"),
    "protocol_task_completed": _spec("Whether root_status is the protocol completed state.", "roots/exp1/*/result.json", "$.root_status", kind="derived", formula="root_status == 'completed'"),
    "protocol_started": _spec("Whether the protocol lifecycle started after preflight.", "roots/exp1/*/result.json", "$.protocol_started"),
    "root_status": _spec("Committed top-level protocol status.", "roots/exp1/*/result.json", "$.root_status"),
    "final_result_exists": _spec("Whether a final result artifact exists.", "roots/exp1/*/result.json", "$.final_result_present", kind="renamed_direct"),
    "final_verified": _spec("Whether the task-specific final verification passed.", "roots/exp1/*/result.json", "$.verified_correct", kind="renamed_direct"),
    "scientifically_evaluable": _spec("Whether the root belongs to the scientific denominator.", "roots/exp1/*/result.json", "$.failure_kind", kind="derived", formula="failure_kind != 'infrastructure_invalid'"),
    "infra_invalid": _spec("Whether the committed root failure is infrastructure-invalid.", "roots/exp1/*/result.json", "$.failure_kind", kind="derived", formula="failure_kind == 'infrastructure_invalid'"),
    "no_final": _spec("Whether the committed root failure is no-final.", "roots/exp1/*/result.json", "$.failure_kind", kind="derived", formula="failure_kind == 'no_final'"),
    "incorrect_final": _spec("Whether the committed root failure is incorrect-final.", "roots/exp1/*/result.json", "$.failure_kind", kind="derived", formula="failure_kind == 'incorrect_final'"),
    "failure_kind": _spec("Committed mutually exclusive top-level failure classification.", "roots/exp1/*/result.json", "$.failure_kind", null_handling="Null for successful verified roots."),
    "failure_stage": _spec("Committed stage at which the root failed.", "roots/exp1/*/result.json", "$.failure_stage", null_handling="Null for successful verified roots."),
    "failure_origin": _spec("Committed detailed origin of root failure.", "roots/exp1/*/result.json", "$.failure_origin", null_handling="Null for successful verified roots."),
    "total_attempts": _spec("Number of actual protocol range attempts.", "roots/exp1/*/result.json", "count($.attempts[?trace_origin='protocol'])", kind="derived"),
    "retry_count": _spec("Number of replacement/retry attempts.", "roots/exp1/*/result.json", "$.attempts[*].attempt_ordinal", kind="derived_and_cross_checked", formula="count protocol attempts with attempt_ordinal > 0; each must have replacement_of_attempt_id"),
    "first_attempt_nonpass_count": _spec("Executed ranges whose ordinal-zero attempt did not pass verification.", "roots/exp1/*/result.json", ("$.attempts[*].attempt_ordinal", "$.attempts[*].verifier_result"), kind="derived", formula="count attempt_ordinal=0 and verifier_result!='passed'"),
    "verification_rejection_count": _spec("Attempts explicitly rejected by the range verifier.", "roots/exp1/*/result.json", "$.attempts[*].verifier_result", kind="derived", formula="count verifier_result='rejected'"),
    "parse_failure_count": _spec("Attempts explicitly rejected by the parser.", "roots/exp1/*/result.json", "$.attempts[*].parse_result", kind="derived", formula="count parse_result='rejected'"),
    "provider_failure_count": _spec("Attempts whose persisted acquisition result is provider_failed.", "roots/exp1/*/result.json", "$.attempts[*].result_kind", kind="derived_and_cross_checked", formula="count result_kind='provider_failed'; cross-checked with provider terminal"),
    "provider_call_count": _spec("Protocol attempts that made a provider call.", "roots/exp1/*/result.json", "$.attempts[*].provider_call_made", kind="derived", formula="count trace_origin='protocol' and provider_call_made=true"),
    "prompt_tokens": _spec("Nullable sum of actual prompt tokens over protocol provider calls.", "roots/exp1/*/result.json", "$.attempts[*].prompt_tokens", kind="derived", formula="nullable_sum(prompt_tokens where trace_origin='protocol' and provider_call_made=true)", null_handling="Null if any called attempt lacks prompt usage; zero only when provider_call_count is zero."),
    "completion_tokens": _spec("Nullable sum of actual completion tokens over protocol provider calls.", "roots/exp1/*/result.json", "$.attempts[*].completion_tokens", kind="derived", formula="nullable_sum(completion_tokens where trace_origin='protocol' and provider_call_made=true)", null_handling="Null if any called attempt lacks completion usage; zero only when provider_call_count is zero."),
    "total_tokens": _spec("Formal actual model-service total tokens for the root.", "roots/exp1/*/result.json", "$.attempts[*].total_tokens", kind="derived", formula="nullable_sum(total_tokens where trace_origin='protocol' and provider_call_made=true)", null_handling="Null if any called attempt lacks total usage; zero only when provider_call_count is zero."),
    "provider_latency_ms": _spec("Formal summed model-service/provider latency for the root.", "roots/exp1/*/result.json", "$.attempts[*].provider_latency_ms", kind="derived", formula="nullable_sum(provider_latency_ms where trace_origin='protocol' and provider_call_made=true)", null_handling="Null if any called attempt lacks provider latency; zero only when provider_call_count is zero."),
    "end_to_end_time_ms": _spec("Complete protocol root wall-clock duration.", "roots/exp1/*/result.json", "$.runtime_wall_clock_ms", kind="renamed_direct", null_handling="Null when the protocol lifecycle did not start or timing is unavailable."),
    "prompt_tokens_missing_reason": _spec("Formal root-level reason for missing prompt-token total.", "derived root table", "prompt_tokens", kind="derived", formula="'usage_missing' when prompt_tokens is null", null_handling="Null when prompt_tokens is present."),
    "completion_tokens_missing_reason": _spec("Formal root-level reason for missing completion-token total.", "derived root table", "completion_tokens", kind="derived", formula="'usage_missing' when completion_tokens is null", null_handling="Null when completion_tokens is present."),
    "total_tokens_missing_reason": _spec("Formal root-level reason for missing total-token total.", "derived root table", "total_tokens", kind="derived", formula="'usage_missing' when total_tokens is null", null_handling="Null when total_tokens is present."),
    "provider_latency_missing_reason": _spec("Reason for a missing root provider-latency total.", "derived root table", "provider_latency_ms", kind="derived", formula="'provider_latency_missing' when provider_latency_ms is null", null_handling="Null when provider_latency_ms is present."),
    "end_to_end_time_missing_reason": _spec("Committed reason for missing root wall-clock time.", "roots/exp1/*/result.json", "$.missing_reason.runtime_wall_clock_ms", null_handling="Null when end_to_end_time_ms is present."),
    "worker_count": _spec("Frozen worker-count condition for the root.", ("inventory/roots.jsonl", "roots/exp1/*/result.json"), "$.worker_count", kind="direct_and_cross_checked"),
    "max_retries": _spec("Frozen maximum replacement attempts per range.", "inventory/conditions.jsonl", "$.max_retries"),
    "model_name": _spec("Configured model name for Experiment 1.", ("inventory/roots.jsonl", "roots/exp1/*/result.json"), "$.configured_model", kind="direct_and_cross_checked"),
    "resolved_model": _spec("Provider-resolved model returned for the root.", "roots/exp1/*/result.json", "$.resolved_model", null_handling="Null when model resolution was not reached, including preflight infrastructure failure."),
    "repeat": _spec("Frozen repeat identity.", "inventory/roots.jsonl", "$.repeat_id"),
    "run_id": _spec("Global formal Full run identity.", "run.json", "$.run_id"),
}


ATTEMPT_FIELD_SPECS = {
    "case_id": _spec("Frozen benchmark case identity.", "inventory/roots.jsonl", "$.case_id"),
    "root_id": _spec("Parent root identity.", "derived root table", "root_id", kind="foreign_key"),
    "partition_count": _spec("Parent root contiguous range count.", "benchmark catalog", "$.split_params.requested_child_count"),
    "is_prime": _spec("Whether the parent root is a prime/no-factor case.", "derived root table", "is_prime", kind="foreign_key"),
    "smallest_factor": _spec("Parent root smallest non-trivial factor.", "derived root table", "smallest_factor", kind="foreign_key", null_handling="Null for prime/no-factor cases."),
    "factor_position_group": _spec("Parent root native factor-position stratum.", "benchmark catalog", "$.factor_position_quantile"),
    "input_scale_group": _spec("Parent 8-range composite input-scale group.", "derived root table", "input_scale_group", kind="foreign_key", null_handling="Null for prime and non-8-range roots."),
    "subtask_id": _spec("Frozen planned range identity.", "UnitTrace", "$.planned_ai_unit_id"),
    "range_index": _spec("Zero-based range index parsed from subtask_id.", "UnitTrace", "$.planned_ai_unit_id", kind="derived_and_cross_checked", formula="integer suffix of 'range_<index>'"),
    "range_lower": _spec("Inclusive executed candidate-range lower bound.", "UnitTrace", "$.candidate_start"),
    "range_upper": _spec("Inclusive executed candidate-range upper bound.", "UnitTrace", "$.candidate_end"),
    "range_width": _spec("Number of candidate divisors in the executed range.", "UnitTrace", ("$.candidate_start", "$.candidate_end"), kind="derived", formula="range_upper - range_lower + 1"),
    "contains_smallest_factor": _spec("Whether this range contains the parent composite's smallest factor.", ("UnitTrace", "benchmark catalog"), ("$.candidate_start", "$.candidate_end", "$.oracle_prime_factors"), kind="derived", formula="range_lower <= smallest_factor <= range_upper", null_handling="Null for prime/no-factor cases because no smallest factor exists."),
    "attempt_id": _spec("Actual protocol attempt identity.", "roots/exp1/*/result.json", "$.attempts[*].attempt_id"),
    "attempt_ordinal": _spec("Zero-based ordinal within the range execution chain.", "roots/exp1/*/result.json", "$.attempts[*].attempt_ordinal"),
    "replacement_of_attempt_id": _spec("Persisted predecessor link for a retry.", "roots/exp1/*/result.json", "$.attempts[*].replacement_of_attempt_id", null_handling="Null for ordinal-zero attempts."),
    "recovery_trigger": _spec("Persisted cause for starting a replacement attempt.", "roots/exp1/*/result.json", "$.attempts[*].recovery_trigger", null_handling="Null for ordinal-zero attempts."),
    "trace_origin": _spec("Attempt provenance; this table contains protocol attempts only.", "roots/exp1/*/result.json", "$.attempts[*].trace_origin"),
    "is_required_for_final": _spec("Counterfactual requirement of this attempt for the eventual final result.", "unavailable", "unavailable", kind="unavailable", null_handling="Always null: no authoritative persisted attempt-level fact defines this counterfactual."),
    "attempt_valid": _spec("Independent generic validity flag for an attempt.", "unavailable", "unavailable", kind="unavailable", null_handling="Always null: validity is represented by separate parse, verifier, and canonical-acceptance facts."),
    "raw_output_returned": _spec("Whether a raw provider response was persisted for the attempt.", "roots/exp1/*/result.json", "$.attempts[*].raw_response_present", kind="renamed_direct"),
    "provider_call_made": _spec("Whether this attempt made a provider call.", "roots/exp1/*/result.json", "$.attempts[*].provider_call_made"),
    "parse_success": _spec("Whether the parser produced a checkable candidate.", "roots/exp1/*/result.json", "$.attempts[*].parse_result", kind="derived", formula="true if parsed, false if rejected, null if parser was not reached", null_handling="Null when parser was not reached, including provider failures."),
    "verification_passed": _spec("Whether the range verifier passed the parsed candidate.", "roots/exp1/*/result.json", "$.attempts[*].verifier_result", kind="derived", formula="true if passed, false if rejected, null if verifier was not reached", null_handling="Null when verifier was not reached."),
    "accepted_result": _spec("Whether this attempt was bound as the subtask canonical result.", "roots/exp1/*/result.json", "$.attempts[*].canonical_accepted", kind="renamed_direct"),
    "result_kind": _spec("Parsed domain candidate kind: found_factor or no_factor_in_range.", "parsed factorization.range_result.v1 artifact", "$.result_kind", null_handling="Null when provider acquisition or parsing failed and no parsed artifact exists."),
    "execution_result_kind": _spec("Persisted acquisition/parser outcome kind.", "roots/exp1/*/result.json", "$.attempts[*].result_kind"),
    "parse_result": _spec("Persisted parser state.", "roots/exp1/*/result.json", "$.attempts[*].parse_result", null_handling="Null when parser was not reached."),
    "verification_result": _spec("Persisted verifier state.", "roots/exp1/*/result.json", "$.attempts[*].verifier_result", null_handling="Null when verifier was not reached."),
    "provider_error": _spec("Persisted provider terminal error kind.", "calls/*.terminal.json", "$.error_kind", null_handling="Null for successful provider calls."),
    "failure_type": _spec("Analysis diagnostic derived without replacing direct states.", ("roots/exp1/*/result.json", "calls/*.terminal.json"), ("$.attempts[*].result_kind", "$.attempts[*].parse_result", "$.attempts[*].verifier_result"), kind="derived", formula="provider_failure, else parse_failure, else verification_rejection, else null", null_handling="Null for attempts without a diagnosed failure."),
    "http_status": _spec("Persisted provider HTTP status.", "roots/exp1/*/result.json", "$.attempts[*].http_status", null_handling="Null when no HTTP response status exists."),
    "call_state": _spec("Persisted provider call lifecycle state.", "roots/exp1/*/result.json", "$.attempts[*].call_state"),
    "usage_status": _spec("Persisted provider usage completeness state.", "roots/exp1/*/result.json", "$.attempts[*].usage_status"),
    "prompt_tokens": _spec("Actual attempt prompt tokens.", "roots/exp1/*/result.json", "$.attempts[*].prompt_tokens", null_handling="Null when provider usage is unavailable; never imputed as zero."),
    "completion_tokens": _spec("Actual attempt completion tokens.", "roots/exp1/*/result.json", "$.attempts[*].completion_tokens", null_handling="Null when provider usage is unavailable; never imputed as zero."),
    "attempt_tokens": _spec("Actual attempt total tokens.", "roots/exp1/*/result.json", "$.attempts[*].total_tokens", kind="renamed_direct", null_handling="Null when provider usage is unavailable; never imputed as zero."),
    "attempt_latency_ms": _spec("Actual provider/model-service latency for the attempt.", "roots/exp1/*/result.json", "$.attempts[*].provider_latency_ms", kind="renamed_direct", null_handling="Null when provider latency is unavailable; never imputed as zero."),
    "attempt_elapsed_ms": _spec("Worker-observed elapsed time for this protocol attempt.", "roots/exp1/*/protocol.json", ("$.protocol_result.summary.runtime_observation.worker_execution_facts[*].started_at", "$.protocol_result.summary.runtime_observation.worker_execution_facts[*].ended_at"), kind="derived", formula="finished_at - started_at in milliseconds", null_handling="Null only if a matching worker fact or timestamp is unavailable."),
    "attempt_tokens_missing_reason": _spec("Committed reason for missing attempt total tokens.", "roots/exp1/*/result.json", "$.attempts[*].missing_reason['attempts[].total_tokens']", null_handling="Null when attempt_tokens is present."),
    "attempt_latency_missing_reason": _spec("Committed reason for missing provider latency.", "roots/exp1/*/result.json", "$.attempts[*].missing_reason['attempts[].provider_latency_ms']", null_handling="Null when attempt_latency_ms is present."),
    "attempt_elapsed_missing_reason": _spec("Reason for missing joined worker elapsed time.", "derived attempt table", "worker execution fact join", kind="derived", formula="'worker_execution_fact_or_timestamp_missing' when elapsed time cannot be computed", null_handling="Null when attempt_elapsed_ms is present."),
    "worker_id": _spec("Worker that executed the attempt.", "roots/exp1/*/protocol.json", "$.protocol_result.summary.runtime_observation.worker_execution_facts[*].worker_id"),
    "started_at": _spec("Worker execution start timestamp.", "roots/exp1/*/protocol.json", "$.protocol_result.summary.runtime_observation.worker_execution_facts[*].started_at"),
    "finished_at": _spec("Worker execution end timestamp.", "roots/exp1/*/protocol.json", "$.protocol_result.summary.runtime_observation.worker_execution_facts[*].ended_at"),
    "repeat": _spec("Frozen repeat identity.", "inventory/roots.jsonl", "$.repeat_id"),
    "run_id": _spec("Global formal Full run identity.", "run.json", "$.run_id"),
}


@dataclass(slots=True)
class AnalysisResult:
    run_id: str
    root_rows: list[dict[str, Any]]
    attempt_rows: list[dict[str, Any]]
    input_sources: tuple[Path, ...]


def partition_ranges(
    start: int,
    end: int,
    count: int,
) -> tuple[tuple[int, int], ...]:
    """Apply the frozen contiguous balanced range partition rule."""

    if start > end:
        raise ValueError("range start must not exceed range end")
    if count < 1 or count > end - start + 1:
        raise ValueError("range count must fit the non-empty candidate domain")
    domain_size = end - start + 1
    base_size, extra = divmod(domain_size, count)
    ranges: list[tuple[int, int]] = []
    next_start = start
    for index in range(count):
        width = base_size + int(index < extra)
        range_end = next_start + width - 1
        ranges.append((next_start, range_end))
        next_start = range_end + 1
    return tuple(ranges)


def nullable_sum(
    values: Iterable[int | float | None],
) -> int | float | None:
    """Match the reducer's sum: empty is zero; any missing member is missing."""

    materialized = tuple(values)
    if any(value is None for value in materialized):
        return None
    return sum(value for value in materialized if value is not None)


def attempt_failure_type(
    result_kind: str | None,
    parse_result: str | None,
    verifier_result: str | None,
) -> str | None:
    """Return the documented analysis diagnostic without replacing raw states."""

    if result_kind == "provider_failed":
        return "provider_failure"
    if parse_result == "rejected":
        return "parse_failure"
    if verifier_result == "rejected":
        return "verification_rejection"
    return None


def assign_input_scale_groups(rows: list[dict[str, Any]]) -> None:
    """Assign deterministic rank tertiles to 8-range composite roots only."""

    selected = sorted(
        (
            row
            for row in rows
            if row["partition_count"] == 8 and not row["is_prime"]
        ),
        key=lambda row: (
            row["candidate_domain_size"],
            row["target_n"],
            row["case_id"],
        ),
    )
    labels = ("small_M", "middle_M", "large_M")
    for row in rows:
        row["input_scale_rank"] = None
        row["input_scale_group"] = None
    if not selected:
        return
    for rank, row in enumerate(selected, start=1):
        row["input_scale_rank"] = rank
        row["input_scale_group"] = labels[3 * (rank - 1) // len(selected)]


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"expected JSON object at {path}:{line_number}")
            rows.append(value)
    return rows


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _require_schema(record: dict[str, Any], expected: str, path: Path) -> None:
    _require(
        record.get("schema_version") == expected,
        f"unexpected schema in {path}: {record.get('schema_version')!r} != {expected!r}",
    )


def _root_key(record: dict[str, Any]) -> tuple[str, str, str, int]:
    return (
        str(record["experiment_id"]),
        str(record["condition_id"]),
        str(record["case_id"]),
        int(record["repeat_id"]),
    )


def _root_id(run_id: str, key: tuple[str, str, str, int]) -> str:
    experiment_id, condition_id, case_id, repeat_id = key
    return f"{run_id}:{experiment_id}:{condition_id}:{case_id}:{repeat_id}"


def _range_index(planned_ai_unit_id: str) -> int:
    prefix = "range_"
    _require(
        planned_ai_unit_id.startswith(prefix)
        and planned_ai_unit_id[len(prefix) :].isdecimal(),
        f"invalid Factorization planned range identity: {planned_ai_unit_id!r}",
    )
    return int(planned_ai_unit_id[len(prefix) :])


def _tri_state(value: str | None, passed: str, rejected: str) -> bool | None:
    if value == passed:
        return True
    if value == rejected:
        return False
    if value is None:
        return None
    raise ValueError(f"unexpected state {value!r}; expected {passed!r}, {rejected!r}, or null")


def _elapsed_ms(started_at: str | None, ended_at: str | None) -> float | None:
    if started_at is None or ended_at is None:
        return None
    started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    ended = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
    elapsed = (ended - started).total_seconds() * 1000
    _require(elapsed >= 0, "worker execution ended before it started")
    return round(elapsed, 3)


def _field_missing_reason(attempt: dict[str, Any], field_name: str) -> str | None:
    if attempt.get(field_name) is not None:
        return None
    missing_reason = attempt.get("missing_reason") or {}
    value = missing_reason.get(f"attempts[].{field_name}")
    return str(value) if value is not None else "missing_without_recorded_reason"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _catalog_facts(case: dict[str, Any]) -> dict[str, Any]:
    target_n = int(case["target_n"])
    sqrt_floor = isqrt(target_n)
    candidate_start = int(case["candidate_start"])
    candidate_end = int(case["candidate_end"])
    candidate_domain_size = sqrt_floor - 1
    _require(candidate_start == 2, f"{case['case_id']}: candidate_start is not 2")
    _require(
        candidate_end == sqrt_floor,
        f"{case['case_id']}: candidate_end does not equal floor(sqrt(target_n))",
    )
    _require(
        int(case["candidate_divisor_count"]) == candidate_domain_size,
        f"{case['case_id']}: candidate_divisor_count contradicts complete domain",
    )
    oracle = case["oracle_prime_factors"]
    _require(isinstance(oracle, list) and oracle, f"{case['case_id']}: invalid oracle")
    position_group = str(case["factor_position_quantile"])
    is_prime = position_group == "no_factor"
    in_range_factors = sorted(
        int(item["prime"])
        for item in oracle
        if candidate_start <= int(item["prime"]) <= candidate_end
    )
    if is_prime:
        _require(
            oracle == [{"exponent": 1, "prime": str(target_n)}]
            or oracle == [{"prime": str(target_n), "exponent": 1}],
            f"{case['case_id']}: no_factor oracle does not identify target_n as prime",
        )
        _require(not in_range_factors, f"{case['case_id']}: prime case has in-range factor")
        smallest_factor = None
        position_ratio = None
    else:
        _require(
            len(in_range_factors) == 1,
            f"{case['case_id']}: composite case does not have exactly one in-range oracle factor",
        )
        smallest_factor = in_range_factors[0]
        denominator = candidate_end - candidate_start
        _require(denominator > 0, f"{case['case_id']}: degenerate candidate domain")
        numerator = smallest_factor - candidate_start
        position_ratio = numerator / denominator
        if 3 * numerator <= denominator:
            recomputed_group = "early"
        elif 3 * numerator <= 2 * denominator:
            recomputed_group = "middle"
        else:
            recomputed_group = "late"
        _require(
            recomputed_group == position_group,
            f"{case['case_id']}: native factor-position group contradicts oracle ratio",
        )
    partition_count = int(case["split_params"]["requested_child_count"])
    planned_ranges = partition_ranges(candidate_start, candidate_end, partition_count)
    factor_range_index = None
    if smallest_factor is not None:
        factor_range_index = next(
            index
            for index, (lower, upper) in enumerate(planned_ranges)
            if lower <= smallest_factor <= upper
        )
    return {
        "target_n": target_n,
        "sqrt_floor": sqrt_floor,
        "candidate_start": candidate_start,
        "candidate_end": candidate_end,
        "candidate_domain_size": candidate_domain_size,
        "is_prime": is_prime,
        "smallest_factor": smallest_factor,
        "factor_position_ratio": position_ratio,
        "factor_position_group": position_group,
        "partition_count": partition_count,
        "planned_ranges": planned_ranges,
        "factor_range_index": factor_range_index,
    }


def _submission_refs_by_attempt(
    protocol: dict[str, Any],
    attempt_ids: set[str],
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ref in protocol["protocol_result"]["artifact_refs"]:
        if ref.get("artifact_schema_id") != "phase3.execution_submission":
            continue
        attempt_id = str((ref.get("metadata") or {}).get("attempt_id", ""))
        if attempt_id in attempt_ids:
            grouped[attempt_id].append(ref)
    refs: dict[str, dict[str, Any]] = {}
    for attempt_id in attempt_ids:
        _require(
            len(grouped[attempt_id]) == 1,
            f"attempt {attempt_id} has {len(grouped[attempt_id])} submission artifact refs",
        )
        refs[attempt_id] = grouped[attempt_id][0]
    return refs


def _parsed_result_kind(
    *,
    system_root: Path,
    submission_ref: dict[str, Any],
    attempt: dict[str, Any],
) -> str | None:
    submission_path = system_root / str(submission_ref["uri"])
    submission = _read_json(submission_path)
    _require_schema(submission, SUBMISSION_SCHEMA, submission_path)
    _require(
        submission.get("attempt_id") == attempt["attempt_id"],
        f"submission attempt identity mismatch: {submission_path}",
    )
    parsed_ref = submission.get("parsed_output_ref")
    if parsed_ref is None:
        _require(
            attempt.get("parse_result") != "parsed",
            f"parsed attempt lacks parsed output artifact: {attempt['attempt_id']}",
        )
        return None
    _require(
        attempt.get("parse_result") == "parsed",
        f"unparsed attempt unexpectedly has parsed output artifact: {attempt['attempt_id']}",
    )
    parsed_path = system_root / str(parsed_ref["uri"])
    parsed = _read_json(parsed_path)
    _require_schema(parsed, RANGE_RESULT_SCHEMA, parsed_path)
    result_kind = parsed.get("result_kind")
    _require(
        result_kind in {"found_factor", "no_factor_in_range"},
        f"unexpected Factorization parsed result kind in {parsed_path}: {result_kind!r}",
    )
    return str(result_kind)


def extract_analysis(*, run_dir: Path, catalog_path: Path) -> AnalysisResult:
    """Join existing formal Experiment 1 Factorization records without mutation."""

    run_dir = run_dir.resolve(strict=True)
    catalog_path = catalog_path.resolve(strict=True)
    run_path = run_dir / "run.json"
    root_inventory_path = run_dir / "inventory" / "roots.jsonl"
    condition_inventory_path = run_dir / "inventory" / "conditions.jsonl"
    exp1_metrics_path = run_dir / "metrics" / "tables" / "exp1.jsonl"
    run_config = _read_json(run_path)
    _require_schema(run_config, RUN_SCHEMA, run_path)
    run_id = str(run_config["run_id"])

    catalog_rows = _read_jsonl(catalog_path)
    catalog_by_case: dict[str, dict[str, Any]] = {}
    for case in catalog_rows:
        case_id = str(case["case_id"])
        _require(case_id not in catalog_by_case, f"duplicate catalog case: {case_id}")
        catalog_by_case[case_id] = case

    conditions: dict[str, dict[str, Any]] = {}
    for condition in _read_jsonl(condition_inventory_path):
        condition_id = str(condition["condition_id"])
        _require(condition_id not in conditions, f"duplicate condition: {condition_id}")
        conditions[condition_id] = condition

    inventory_rows = [
        row
        for row in _read_jsonl(root_inventory_path)
        if row.get("experiment_id") == "exp1" and row.get("domain") == "factorization"
    ]
    inventory_keys: set[tuple[str, str, str, int]] = set()
    for inventory in inventory_rows:
        _require_schema(inventory, ROOT_INVENTORY_SCHEMA, root_inventory_path)
        key = _root_key(inventory)
        _require(key not in inventory_keys, f"duplicate frozen root inventory member: {key}")
        inventory_keys.add(key)

    result_paths_by_key: dict[tuple[str, str, str, int], Path] = {}
    results_by_key: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    for result_path in (run_dir / "roots" / "exp1").glob("*/result.json"):
        result = _read_json(result_path)
        if result.get("domain") != "factorization":
            continue
        _require_schema(result, ROOT_RESULT_SCHEMA, result_path)
        key = _root_key(result)
        _require(key not in results_by_key, f"duplicate committed root result: {key}")
        results_by_key[key] = result
        result_paths_by_key[key] = result_path

    _require(
        set(results_by_key) == inventory_keys,
        "committed Factorization root result identities do not match frozen inventory",
    )

    root_rows: list[dict[str, Any]] = []
    attempt_rows: list[dict[str, Any]] = []
    for inventory in inventory_rows:
        key = _root_key(inventory)
        experiment_id, condition_id, case_id, repeat_id = key
        result = results_by_key[key]
        result_path = result_paths_by_key[key]
        _require(case_id in catalog_by_case, f"missing catalog case: {case_id}")
        _require(condition_id in conditions, f"missing frozen condition: {condition_id}")
        condition = conditions[condition_id]
        facts = _catalog_facts(catalog_by_case[case_id])
        planned_ids = [str(value) for value in inventory["planned_ai_unit_ids"]]
        _require(
            facts["partition_count"] == len(planned_ids),
            f"{case_id}: catalog partition count contradicts frozen planned units",
        )
        _require(
            planned_ids == [f"range_{index}" for index in range(len(planned_ids))],
            f"{case_id}: frozen planned range IDs are not contiguous",
        )
        _require(result.get("worker_count") == inventory.get("worker_count"), f"{case_id}: worker_count mismatch")
        _require(result.get("configured_model") == inventory.get("configured_model"), f"{case_id}: configured_model mismatch")

        protocol_attempts = [
            attempt
            for attempt in result["attempts"]
            if attempt.get("trace_origin") == "protocol"
        ]
        attempts_by_id = {str(attempt["attempt_id"]): attempt for attempt in protocol_attempts}
        _require(
            len(attempts_by_id) == len(protocol_attempts),
            f"{case_id}: duplicate protocol attempt identity",
        )
        attempts_by_unit: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for attempt in protocol_attempts:
            attempts_by_unit[str(attempt["planned_ai_unit_id"])].append(attempt)
        executed_ids = set(attempts_by_unit)
        dispatched_ids = {str(value) for value in result["dispatched_ai_unit_ids"]}
        unscheduled_ids = {str(value) for value in result["unscheduled_ai_unit_ids"]}
        _require(executed_ids == dispatched_ids, f"{case_id}: attempts contradict dispatched units")
        _require(
            set(planned_ids) == executed_ids | unscheduled_ids
            and not executed_ids.intersection(unscheduled_ids),
            f"{case_id}: executed and unscheduled units do not partition the plan",
        )
        for planned_id, unit_attempts in attempts_by_unit.items():
            ordered = sorted(unit_attempts, key=lambda item: int(item["attempt_ordinal"]))
            _require(
                [int(item["attempt_ordinal"]) for item in ordered]
                == list(range(len(ordered))),
                f"{case_id}/{planned_id}: attempt ordinals are not contiguous",
            )
            for attempt in ordered[1:]:
                _require(
                    attempt.get("replacement_of_attempt_id") is not None,
                    f"{attempt['attempt_id']}: retry lacks predecessor link",
                )

        root_identity = _root_id(run_id, key)
        protocol_path = result_path.with_name("protocol.json")
        traces_by_unit: dict[str, dict[str, Any]] = {}
        worker_facts_by_attempt: dict[str, list[dict[str, Any]]] = defaultdict(list)
        submission_refs: dict[str, dict[str, Any]] = {}
        system_root = run_dir / "system" / "exp1" / result_path.parent.name
        if result["protocol_started"]:
            _require(protocol_path.is_file(), f"started root lacks protocol material: {case_id}")
            protocol = _read_json(protocol_path)
            _require_schema(protocol, PROTOCOL_MATERIAL_SCHEMA, protocol_path)
            _require(tuple(protocol["root_key"]) == key, f"{case_id}: protocol root key mismatch")
            _require(
                protocol["protocol_result"]["run_id"] == root_identity,
                f"{case_id}: protocol run identity mismatch",
            )
            embedded_traces = {
                str(trace["planned_ai_unit_id"]): trace
                for trace in protocol["traces"]
                if trace.get("trace_origin") == "protocol"
            }
            _require(set(embedded_traces) == executed_ids, f"{case_id}: trace unit identities mismatch")
            for planned_id, embedded_trace in embedded_traces.items():
                trace_path = (
                    run_dir
                    / "traces"
                    / "exp1"
                    / case_id
                    / str(repeat_id)
                    / f"{planned_id}.json"
                )
                trace = _read_json(trace_path)
                _require_schema(trace, TRACE_SCHEMA, trace_path)
                _require(trace == embedded_trace, f"{case_id}/{planned_id}: trace material mismatch")
                range_index = _range_index(planned_id)
                expected_range = facts["planned_ranges"][range_index]
                _require(
                    (trace["candidate_start"], trace["candidate_end"]) == expected_range,
                    f"{case_id}/{planned_id}: trace range contradicts frozen partition",
                )
                trace_attempt_ids = {str(item["attempt_id"]) for item in trace["attempts"]}
                root_attempt_ids = {
                    str(item["attempt_id"]) for item in attempts_by_unit[planned_id]
                }
                _require(
                    trace_attempt_ids == root_attempt_ids,
                    f"{case_id}/{planned_id}: trace attempts contradict root observation",
                )
                traces_by_unit[planned_id] = trace
            runtime_observation = protocol["protocol_result"]["summary"]["runtime_observation"]
            for worker_fact in runtime_observation["worker_execution_facts"]:
                attempt_id = str(worker_fact.get("attempt_id", ""))
                if attempt_id in attempts_by_id:
                    worker_facts_by_attempt[attempt_id].append(worker_fact)
            submission_refs = _submission_refs_by_attempt(protocol, set(attempts_by_id))
        else:
            _require(not protocol_path.exists(), f"unstarted root unexpectedly has protocol material: {case_id}")
            _require(not protocol_attempts, f"unstarted root unexpectedly has protocol attempts: {case_id}")

        called_attempts = [
            attempt for attempt in protocol_attempts if attempt.get("provider_call_made") is True
        ]
        prompt_tokens = nullable_sum(attempt.get("prompt_tokens") for attempt in called_attempts)
        completion_tokens = nullable_sum(
            attempt.get("completion_tokens") for attempt in called_attempts
        )
        total_tokens = nullable_sum(attempt.get("total_tokens") for attempt in called_attempts)
        provider_latency = nullable_sum(
            attempt.get("provider_latency_ms") for attempt in called_attempts
        )
        failure_kind = result.get("failure_kind")
        root_row = {
            "case_id": case_id,
            "root_id": root_identity,
            "experiment_id": experiment_id,
            "condition_id": condition_id,
            "root_result_relative_path": result_path.relative_to(run_dir).as_posix(),
            "original_group": str(catalog_by_case[case_id]["difficulty"]),
            "partition_count": facts["partition_count"],
            "target_n": facts["target_n"],
            "sqrt_floor": facts["sqrt_floor"],
            "candidate_start": facts["candidate_start"],
            "candidate_end": facts["candidate_end"],
            "candidate_domain_size": facts["candidate_domain_size"],
            "is_prime": facts["is_prime"],
            "smallest_factor": facts["smallest_factor"],
            "factor_position_ratio": facts["factor_position_ratio"],
            "factor_position_group": facts["factor_position_group"],
            "factor_range_index": facts["factor_range_index"],
            "input_scale_rank": None,
            "input_scale_group": None,
            "planned_subtask_count": len(planned_ids),
            "executed_subtask_count": len(executed_ids),
            "unscheduled_subtask_count": len(unscheduled_ids),
            "completion": bool(result["final_result_present"]),
            "protocol_task_completed": result["root_status"] == "completed",
            "protocol_started": bool(result["protocol_started"]),
            "root_status": result["root_status"],
            "final_result_exists": bool(result["final_result_present"]),
            "final_verified": bool(result["verified_correct"]),
            "scientifically_evaluable": failure_kind != "infrastructure_invalid",
            "infra_invalid": failure_kind == "infrastructure_invalid",
            "no_final": failure_kind == "no_final",
            "incorrect_final": failure_kind == "incorrect_final",
            "failure_kind": failure_kind,
            "failure_stage": result.get("failure_stage"),
            "failure_origin": result.get("failure_origin"),
            "total_attempts": len(protocol_attempts),
            "retry_count": sum(int(attempt["attempt_ordinal"]) > 0 for attempt in protocol_attempts),
            "first_attempt_nonpass_count": sum(
                int(attempt["attempt_ordinal"]) == 0
                and attempt.get("verifier_result") != "passed"
                for attempt in protocol_attempts
            ),
            "verification_rejection_count": sum(
                attempt.get("verifier_result") == "rejected" for attempt in protocol_attempts
            ),
            "parse_failure_count": sum(
                attempt.get("parse_result") == "rejected" for attempt in protocol_attempts
            ),
            "provider_failure_count": sum(
                attempt.get("result_kind") == "provider_failed" for attempt in protocol_attempts
            ),
            "provider_call_count": len(called_attempts),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "provider_latency_ms": provider_latency,
            "end_to_end_time_ms": result.get("runtime_wall_clock_ms"),
            "prompt_tokens_missing_reason": "usage_missing" if prompt_tokens is None else None,
            "completion_tokens_missing_reason": "usage_missing"
            if completion_tokens is None
            else None,
            "total_tokens_missing_reason": "usage_missing" if total_tokens is None else None,
            "provider_latency_missing_reason": "provider_latency_missing"
            if provider_latency is None
            else None,
            "end_to_end_time_missing_reason": (result.get("missing_reason") or {}).get(
                "runtime_wall_clock_ms"
            )
            if result.get("runtime_wall_clock_ms") is None
            else None,
            "worker_count": int(inventory["worker_count"]),
            "max_retries": int(condition["max_retries"]),
            "model_name": inventory["configured_model"],
            "resolved_model": result.get("resolved_model"),
            "repeat": repeat_id,
            "run_id": run_id,
        }
        _require(set(root_row) == set(ROOT_FIELDS), f"{case_id}: root row schema drift")
        root_rows.append(root_row)

        for planned_id in planned_ids:
            if planned_id not in executed_ids:
                continue
            trace = traces_by_unit[planned_id]
            range_index = _range_index(planned_id)
            range_lower = int(trace["candidate_start"])
            range_upper = int(trace["candidate_end"])
            for attempt in sorted(
                attempts_by_unit[planned_id],
                key=lambda item: int(item["attempt_ordinal"]),
            ):
                attempt_id = str(attempt["attempt_id"])
                _require(
                    len(worker_facts_by_attempt[attempt_id]) == 1,
                    f"{attempt_id}: expected exactly one worker execution fact",
                )
                worker_fact = worker_facts_by_attempt[attempt_id][0]
                started_at = worker_fact.get("started_at")
                finished_at = worker_fact.get("ended_at")
                elapsed_ms = _elapsed_ms(started_at, finished_at)
                call_key = (
                    f"exp1:{condition_id}:{case_id}:{repeat_id}:"
                    f"{planned_id}:{attempt['attempt_ordinal']}"
                )
                terminal_path = run_dir / "calls" / f"{quote(call_key, safe='')}.terminal.json"
                terminal = _read_json(terminal_path)
                _require_schema(terminal, PROVIDER_TERMINAL_SCHEMA, terminal_path)
                _require(terminal.get("call_key") == call_key, f"{attempt_id}: call key mismatch")
                for field_name in ("provider_call_made", "http_status", "usage_status"):
                    _require(
                        terminal.get(field_name) == attempt.get(field_name),
                        f"{attempt_id}: provider terminal {field_name} mismatch",
                    )
                terminal_result = terminal.get("result") or {}
                for terminal_name, attempt_name in (
                    ("provider_latency_ms", "provider_latency_ms"),
                    ("total_tokens", "total_tokens"),
                ):
                    _require(
                        terminal_result.get(terminal_name) == attempt.get(attempt_name),
                        f"{attempt_id}: provider terminal {terminal_name} mismatch",
                    )
                provider_error = terminal.get("error_kind")
                _require(
                    (provider_error is not None)
                    == (attempt.get("result_kind") == "provider_failed"),
                    f"{attempt_id}: provider error and acquisition result disagree",
                )
                parsed_kind = _parsed_result_kind(
                    system_root=system_root,
                    submission_ref=submission_refs[attempt_id],
                    attempt=attempt,
                )
                attempt_row = {
                    "case_id": case_id,
                    "root_id": root_identity,
                    "partition_count": facts["partition_count"],
                    "is_prime": facts["is_prime"],
                    "smallest_factor": facts["smallest_factor"],
                    "factor_position_group": facts["factor_position_group"],
                    "input_scale_group": None,
                    "subtask_id": planned_id,
                    "range_index": range_index,
                    "range_lower": range_lower,
                    "range_upper": range_upper,
                    "range_width": range_upper - range_lower + 1,
                    "contains_smallest_factor": None
                    if facts["smallest_factor"] is None
                    else range_lower <= facts["smallest_factor"] <= range_upper,
                    "attempt_id": attempt_id,
                    "attempt_ordinal": int(attempt["attempt_ordinal"]),
                    "replacement_of_attempt_id": attempt.get("replacement_of_attempt_id"),
                    "recovery_trigger": attempt.get("recovery_trigger"),
                    "trace_origin": attempt["trace_origin"],
                    "is_required_for_final": None,
                    "attempt_valid": None,
                    "raw_output_returned": bool(attempt["raw_response_present"]),
                    "provider_call_made": bool(attempt["provider_call_made"]),
                    "parse_success": _tri_state(attempt.get("parse_result"), "parsed", "rejected"),
                    "verification_passed": _tri_state(
                        attempt.get("verifier_result"), "passed", "rejected"
                    ),
                    "accepted_result": bool(attempt["canonical_accepted"]),
                    "result_kind": parsed_kind,
                    "execution_result_kind": attempt.get("result_kind"),
                    "parse_result": attempt.get("parse_result"),
                    "verification_result": attempt.get("verifier_result"),
                    "provider_error": provider_error,
                    "failure_type": attempt_failure_type(
                        attempt.get("result_kind"),
                        attempt.get("parse_result"),
                        attempt.get("verifier_result"),
                    ),
                    "http_status": attempt.get("http_status"),
                    "call_state": attempt.get("call_state"),
                    "usage_status": attempt.get("usage_status"),
                    "prompt_tokens": attempt.get("prompt_tokens"),
                    "completion_tokens": attempt.get("completion_tokens"),
                    "attempt_tokens": attempt.get("total_tokens"),
                    "attempt_latency_ms": attempt.get("provider_latency_ms"),
                    "attempt_elapsed_ms": elapsed_ms,
                    "attempt_tokens_missing_reason": _field_missing_reason(
                        attempt, "total_tokens"
                    ),
                    "attempt_latency_missing_reason": _field_missing_reason(
                        attempt, "provider_latency_ms"
                    ),
                    "attempt_elapsed_missing_reason": None
                    if elapsed_ms is not None
                    else "worker_execution_fact_or_timestamp_missing",
                    "worker_id": worker_fact.get("worker_id"),
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "repeat": repeat_id,
                    "run_id": run_id,
                }
                _require(
                    set(attempt_row) == set(ATTEMPT_FIELDS),
                    f"{attempt_id}: attempt row schema drift",
                )
                attempt_rows.append(attempt_row)

    assign_input_scale_groups(root_rows)
    scale_groups = {row["root_id"]: row["input_scale_group"] for row in root_rows}
    for attempt_row in attempt_rows:
        attempt_row["input_scale_group"] = scale_groups[attempt_row["root_id"]]
    return AnalysisResult(
        run_id=run_id,
        root_rows=root_rows,
        attempt_rows=attempt_rows,
        input_sources=(
            run_path,
            root_inventory_path,
            condition_inventory_path,
            catalog_path,
            exp1_metrics_path,
        ),
    )


def _outcome_name(row: dict[str, Any]) -> str:
    if row["infra_invalid"]:
        return "infra_invalid"
    if row["no_final"]:
        return "no_final"
    if row["incorrect_final"]:
        return "incorrect_final"
    if row["final_verified"]:
        return "verified"
    return "unclassified"


def _distribution(values: Iterable[int | float]) -> dict[str, int | float | None]:
    materialized = sorted(values)
    if not materialized:
        return {"count": 0, "minimum": None, "median": None, "maximum": None}
    return {
        "count": len(materialized),
        "minimum": materialized[0],
        "median": median(materialized),
        "maximum": materialized[-1],
    }


def _published_factorization_rows(run_dir: Path) -> dict[str, dict[str, Any]]:
    table_path = run_dir / "metrics" / "tables" / "exp1.jsonl"
    rows: dict[str, dict[str, Any]] = {}
    for row in _read_jsonl(table_path):
        slice_value = row["slice"]
        if slice_value.get("domain") == "factorization":
            rows[str(slice_value["difficulty"])] = row
    _require(set(rows) == {"easy", "medium", "hard"}, "published exp1 table lacks Factorization cells")
    return rows


def validate_analysis(
    result: AnalysisResult,
    *,
    run_dir: Path,
) -> dict[str, object]:
    """Build consistency facts and compare exact reducer cells without mutation."""

    run_dir = run_dir.resolve(strict=True)
    root_rows = result.root_rows
    attempt_rows = result.attempt_rows
    group_order = ("easy", "medium", "hard")
    expected_partitions = {"easy": 2, "medium": 4, "hard": 8}
    group_composition: dict[str, dict[str, Any]] = {}
    position_outcomes: dict[str, dict[str, dict[str, int]]] = {}
    candidate_domain_distributions: dict[str, dict[str, int | float | None]] = {}
    target_n_distributions: dict[str, dict[str, int | float | None]] = {}
    for group in group_order:
        rows = [row for row in root_rows if row["original_group"] == group]
        positions = Counter(str(row["factor_position_group"]) for row in rows)
        outcomes = Counter(_outcome_name(row) for row in rows)
        partitions = sorted({int(row["partition_count"]) for row in rows})
        group_composition[group] = {
            "roots": len(rows),
            "partition_counts": partitions,
            "composite": sum(not row["is_prime"] for row in rows),
            "prime": sum(bool(row["is_prime"]) for row in rows),
            "early": positions["early"],
            "middle": positions["middle"],
            "late": positions["late"],
            "no_factor": positions["no_factor"],
            "verified": outcomes["verified"],
            "no_final": outcomes["no_final"],
            "incorrect_final": outcomes["incorrect_final"],
            "infra_invalid": outcomes["infra_invalid"],
        }
        position_outcomes[group] = {}
        for position in ("early", "middle", "late", "no_factor"):
            position_rows = [row for row in rows if row["factor_position_group"] == position]
            position_counts = Counter(_outcome_name(row) for row in position_rows)
            position_outcomes[group][position] = {
                "roots": len(position_rows),
                "verified": position_counts["verified"],
                "no_final": position_counts["no_final"],
                "incorrect_final": position_counts["incorrect_final"],
                "infra_invalid": position_counts["infra_invalid"],
            }
        candidate_domain_distributions[group] = _distribution(
            int(row["candidate_domain_size"]) for row in rows
        )
        target_n_distributions[group] = _distribution(int(row["target_n"]) for row in rows)

    unique_ranges: dict[tuple[str, str], int] = {}
    for row in attempt_rows:
        key = (str(row["root_id"]), str(row["subtask_id"]))
        width = int(row["range_width"])
        if key in unique_ranges:
            _require(unique_ranges[key] == width, f"range width changed across retries: {key}")
        unique_ranges[key] = width
    range_width_by_partition = {
        str(partition): _distribution(
            width
            for (root_id, _), width in unique_ranges.items()
            if next(row for row in root_rows if row["root_id"] == root_id)["partition_count"]
            == partition
        )
        for partition in (2, 4, 8)
    }

    attempt_behavior_by_partition: dict[str, dict[str, int]] = {}
    for partition in (2, 4, 8):
        roots = [row for row in root_rows if row["partition_count"] == partition]
        attempts = [row for row in attempt_rows if row["partition_count"] == partition]
        attempt_behavior_by_partition[str(partition)] = {
            "attempts": len(attempts),
            "retries": sum(int(row["attempt_ordinal"]) > 0 for row in attempts),
            "executed_subtasks": sum(int(row["executed_subtask_count"]) for row in roots),
            "first_attempt_nonpass": sum(
                int(row["first_attempt_nonpass_count"]) for row in roots
            ),
            "verification_rejections": sum(
                row["verification_result"] == "rejected" for row in attempts
            ),
            "parse_failures": sum(row["parse_result"] == "rejected" for row in attempts),
            "provider_failures": sum(
                row["execution_result_kind"] == "provider_failed" for row in attempts
            ),
            "accepted_results": sum(bool(row["accepted_result"]) for row in attempts),
        }

    input_scale_rows = [
        row for row in root_rows if row["partition_count"] == 8 and not row["is_prime"]
    ]
    input_scale_groups: dict[str, dict[str, int | float | None]] = {}
    for label in ("small_M", "middle_M", "large_M"):
        rows = [row for row in input_scale_rows if row["input_scale_group"] == label]
        input_scale_groups[label] = _distribution(
            int(row["candidate_domain_size"]) for row in rows
        )

    eight_range_no_final = [
        {
            "case_id": row["case_id"],
            "is_prime": row["is_prime"],
            "factor_position_group": row["factor_position_group"],
            "candidate_domain_size": row["candidate_domain_size"],
        }
        for row in root_rows
        if row["partition_count"] == 8 and row["no_final"]
    ]

    published_rows = _published_factorization_rows(run_dir)
    metric_names = (
        "preregistered_root_count",
        "scientifically_valid_root_count",
        "final_result_root_count",
        "verified_correct_root_count",
        "no_final_failure_count",
        "incorrect_final_failure_count",
        "infra_invalid_failure_count",
        "actual_total_tokens",
        "actual_end_to_end_wall_clock_ms",
        "actual_provider_latency_ms",
    )
    formal_comparison: dict[str, dict[str, object]] = {}
    for group in group_order:
        rows = [row for row in root_rows if row["original_group"] == group]
        recomputed = {
            "preregistered_root_count": len(rows),
            "scientifically_valid_root_count": sum(
                bool(row["scientifically_evaluable"]) for row in rows
            ),
            "final_result_root_count": sum(bool(row["final_result_exists"]) for row in rows),
            "verified_correct_root_count": sum(bool(row["final_verified"]) for row in rows),
            "no_final_failure_count": sum(bool(row["no_final"]) for row in rows),
            "incorrect_final_failure_count": sum(bool(row["incorrect_final"]) for row in rows),
            "infra_invalid_failure_count": sum(bool(row["infra_invalid"]) for row in rows),
            "actual_total_tokens": nullable_sum(row["total_tokens"] for row in rows),
            "actual_end_to_end_wall_clock_ms": nullable_sum(
                row["end_to_end_time_ms"] for row in rows
            ),
            "actual_provider_latency_ms": nullable_sum(
                row["provider_latency_ms"] for row in rows
            ),
        }
        published = {name: published_rows[group].get(name) for name in metric_names}
        formal_comparison[group] = {
            "published": published,
            "recomputed": recomputed,
            "matches": published == recomputed,
        }

    root_ids = [str(row["root_id"]) for row in root_rows]
    attempt_ids = [str(row["attempt_id"]) for row in attempt_rows]
    scale_counts = Counter(str(row["input_scale_group"]) for row in input_scale_rows)
    checks = {
        "factorization_root_count_is_300": len(root_rows) == 300,
        "root_ids_are_unique": len(set(root_ids)) == len(root_ids),
        "case_repeat_identities_are_unique": len(
            {(row["case_id"], row["repeat"]) for row in root_rows}
        )
        == len(root_rows),
        "three_original_groups_have_100_roots": all(
            group_composition[group]["roots"] == 100 for group in group_order
        ),
        "original_groups_map_to_2_4_8_partitions": all(
            group_composition[group]["partition_counts"] == [expected_partitions[group]]
            for group in group_order
        ),
        "attempt_ids_are_unique": len(set(attempt_ids)) == len(attempt_ids),
        "attempt_rows_match_root_attempt_totals": len(attempt_rows)
        == sum(int(row["total_attempts"]) for row in root_rows),
        "planned_equals_executed_plus_unscheduled": all(
            int(row["planned_subtask_count"])
            == int(row["executed_subtask_count"]) + int(row["unscheduled_subtask_count"])
            for row in root_rows
        ),
        "input_scale_composite_count_is_94": len(input_scale_rows) == 94,
        "input_scale_groups_are_32_31_31": scale_counts
        == Counter({"small_M": 32, "middle_M": 31, "large_M": 31}),
        "all_formal_exp1_factorization_cells_match": all(
            bool(value["matches"]) for value in formal_comparison.values()
        ),
        "unavailable_attempt_fields_are_null": all(
            row["is_required_for_final"] is None and row["attempt_valid"] is None
            for row in attempt_rows
        ),
        "resource_missing_reasons_match_nulls": all(
            (row["total_tokens"] is None)
            == (row["total_tokens_missing_reason"] is not None)
            and (row["provider_latency_ms"] is None)
            == (row["provider_latency_missing_reason"] is not None)
            for row in root_rows
        ),
    }
    missing_values = {
        "root_total_tokens": sum(row["total_tokens"] is None for row in root_rows),
        "root_provider_latency_ms": sum(
            row["provider_latency_ms"] is None for row in root_rows
        ),
        "root_end_to_end_time_ms": sum(
            row["end_to_end_time_ms"] is None for row in root_rows
        ),
        "attempt_tokens": sum(row["attempt_tokens"] is None for row in attempt_rows),
        "attempt_latency_ms": sum(
            row["attempt_latency_ms"] is None for row in attempt_rows
        ),
        "attempt_elapsed_ms": sum(
            row["attempt_elapsed_ms"] is None for row in attempt_rows
        ),
        "attempt_result_kind": sum(row["result_kind"] is None for row in attempt_rows),
    }
    return {
        "overall_consistent": all(checks.values()),
        "checks": checks,
        "root_row_count": len(root_rows),
        "attempt_row_count": len(attempt_rows),
        "group_composition": group_composition,
        "position_outcomes": position_outcomes,
        "candidate_domain_size_distribution": candidate_domain_distributions,
        "target_n_distribution": target_n_distributions,
        "input_scale_groups": input_scale_groups,
        "range_width_by_partition": range_width_by_partition,
        "attempt_behavior_by_partition": attempt_behavior_by_partition,
        "formal_exp1_comparison": formal_comparison,
        "eight_range_no_final_cases": eight_range_no_final,
        "eight_range_no_final_prime_count": sum(
            bool(row["is_prime"]) for row in eight_range_no_final
        ),
        "eight_range_no_final_composite_count": sum(
            not row["is_prime"] for row in eight_range_no_final
        ),
        "missing_values": missing_values,
    }


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            _require(set(row) == set(fields), f"CSV row fields do not match {path.name}")
            writer.writerow({field: _csv_value(row[field]) for field in fields})
    temporary.replace(path)


def _write_text(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def _column_metadata(
    fields: tuple[str, ...],
    specs: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    _require(set(fields) == set(specs), "field metadata does not exactly cover CSV columns")
    return [{"name": field, **specs[field]} for field in fields]


def _validation_report(validation: dict[str, object], run_id: str) -> str:
    groups = validation["group_composition"]
    formal = validation["formal_exp1_comparison"]
    missing = validation["missing_values"]
    lines = [
        "# Experiment 1 Factorization extraction validation",
        "",
        f"- Source run: `{run_id}`",
        f"- Overall consistency: `{'PASS' if validation['overall_consistent'] else 'FAIL'}`",
        f"- Root rows: `{validation['root_row_count']}`",
        f"- Actual protocol attempt rows: `{validation['attempt_row_count']}`",
        "- No experiment or provider call was made by this extraction.",
        "",
        "## Root composition and outcomes",
        "",
        "| Original group | Partitions | Roots | Composite | Prime | Early | Middle | Late | No factor | Verified | No final | Incorrect final | Infra invalid |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for group in ("easy", "medium", "hard"):
        value = groups[group]
        lines.append(
            f"| {group} | {','.join(map(str, value['partition_counts']))} | {value['roots']} | "
            f"{value['composite']} | {value['prime']} | {value['early']} | {value['middle']} | "
            f"{value['late']} | {value['no_factor']} | {value['verified']} | {value['no_final']} | "
            f"{value['incorrect_final']} | {value['infra_invalid']} |"
        )
    lines.extend(
        [
            "",
            "The original labels are retained as benchmark provenance only. In this formal selection they map to 2, 4, and 8 contiguous ranges; they are not interpreted here as ordinal arithmetic difficulty.",
            "",
            "## Published Experiment 1 cell reproduction",
            "",
            "| Original group | All compared cells match |",
            "|---|---:|",
        ]
    )
    for group in ("easy", "medium", "hard"):
        lines.append(f"| {group} | {str(formal[group]['matches']).lower()} |")
    lines.extend(
        [
            "",
            "Compared cells: preregistered and scientifically valid roots, final-result and verified roots, no-final, incorrect-final and infrastructure-invalid failures, actual total tokens, summed root wall-clock, and provider latency.",
            "",
            "## Eight-range no-final check",
            "",
            f"The 8-range group has `{validation['eight_range_no_final_composite_count']}` composite no-final roots and `{validation['eight_range_no_final_prime_count']}` prime no-final roots. The six no-final roots are therefore not the six prime roots.",
            "",
            "| Case | Prime | Native position | Candidate-domain size |",
            "|---|---:|---|---:|",
        ]
    )
    for row in validation["eight_range_no_final_cases"]:
        lines.append(
            f"| {row['case_id']} | {str(row['is_prime']).lower()} | "
            f"{row['factor_position_group']} | {row['candidate_domain_size']} |"
        )
    lines.extend(
        [
            "",
            "## Missing values",
            "",
            "CSV nulls are empty cells. They are not converted to zero. A root with zero provider calls may have a formal resource sum of zero; `provider_call_count` distinguishes that case from missing usage.",
            "",
            "| Field | Missing rows |",
            "|---|---:|",
        ]
    )
    for field, count in missing.items():
        lines.append(f"| {field} | {count} |")
    lines.extend(
        [
            "",
            "`is_required_for_final` and `attempt_valid` are unavailable in the persisted facts and are intentionally null for every attempt row.",
            "",
            "## Detailed machine-readable checks",
            "",
        ]
    )
    for name, passed in validation["checks"].items():
        lines.append(f"- `{name}`: `{'PASS' if passed else 'FAIL'}`")
    return "\n".join(lines) + "\n"


def _validate_output_path(
    run_dir: Path, catalog_path: Path, output_dir: Path
) -> None:
    """Reject writes into the read-only source or tracked result namespace."""

    resolved_run_dir = run_dir.resolve(strict=True)
    catalog_path = catalog_path.resolve(strict=True)
    resolved_output = output_dir.resolve(strict=False)
    _require(
        resolved_output != resolved_run_dir
        and not resolved_output.is_relative_to(resolved_run_dir),
        "output directory must not be the read-only source run or one of its descendants",
    )
    public_repo = catalog_path.parents[2]
    analysis_outputs = (
        public_repo / "TokenShareData" / "outputs" / "experiments"
    ).resolve(strict=False)
    _require(
        resolved_output != analysis_outputs
        and resolved_output.is_relative_to(analysis_outputs),
        "output directory must be a new run inside the local analysis output namespace",
    )
    tracked_results = (public_repo / "results" / "experiments").resolve(
        strict=False
    )
    _require(
        resolved_output != tracked_results
        and not resolved_output.is_relative_to(tracked_results),
        "output directory must not be tracked official results",
    )


def write_analysis(
    result: AnalysisResult,
    validation: dict[str, object],
    *,
    run_dir: Path,
    catalog_path: Path,
    output_dir: Path,
) -> tuple[Path, ...]:
    """Write only new analysis artifacts outside source and tracked results."""

    logical_run_dir = run_dir.absolute()
    resolved_run_dir = run_dir.resolve(strict=True)
    catalog_path = catalog_path.resolve(strict=True)
    _validate_output_path(run_dir, catalog_path, output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    root_csv = output_dir / "factorization_exp1_root_analysis.csv"
    attempt_csv = output_dir / "factorization_exp1_attempt_analysis.csv"
    metadata_path = output_dir / "metadata.json"
    report_path = output_dir / "factorization_exp1_validation_report.md"
    _write_csv(root_csv, ROOT_FIELDS, result.root_rows)
    _write_csv(attempt_csv, ATTEMPT_FIELDS, result.attempt_rows)

    input_sources = [
        {"path": str(path), "sha256": _sha256(path), "size_bytes": path.stat().st_size}
        for path in result.input_sources
    ]
    metadata = {
        "schema_version": ANALYSIS_SCHEMA,
        "run_id": result.run_id,
        "logical_run_directory": str(logical_run_dir),
        "resolved_read_only_run_directory": str(resolved_run_dir),
        "catalog_path": str(catalog_path),
        "output_directory": str(output_dir.absolute()),
        "row_granularity": {
            root_csv.name: "one row per frozen Experiment 1 Factorization root",
            attempt_csv.name: "one row per actual protocol range-subtask attempt; no synthetic unscheduled rows",
        },
        "row_counts": {
            root_csv.name: len(result.root_rows),
            attempt_csv.name: len(result.attempt_rows),
        },
        "csv_null_encoding": "empty field",
        "boolean_encoding": "lowercase true/false; empty only when nullable",
        "input_sources": input_sources,
        "output_sha256": {
            root_csv.name: _sha256(root_csv),
            attempt_csv.name: _sha256(attempt_csv),
        },
        "formal_definitions": {
            "completion": "final_result_present; the published Experiment 1 completion numerator",
            "final_verified": "verified_correct from task-specific final verification",
            "scientifically_evaluable": "committed root and failure_kind != infrastructure_invalid; no_final and incorrect_final remain evaluable",
            "infra_invalid": "failure_kind == infrastructure_invalid",
            "total_tokens": "nullable sum over protocol attempts with provider_call_made=true; any missing called usage makes the root total null; no calls sums to zero",
            "end_to_end_time_ms": "runtime_wall_clock_ms = root_terminal_at_ms - root_start_at_ms for the protocol lifecycle",
            "provider_latency_ms": "nullable sum over protocol attempts with provider_call_made=true; any missing called latency makes the root total null; no calls sums to zero",
        },
        "unavailable_fields": {
            "is_required_for_final": "no persisted attempt-level counterfactual requirement fact",
            "attempt_valid": "no independent generic validity fact; use parse_success, verification_passed, and accepted_result",
        },
        "columns": {
            root_csv.name: _column_metadata(ROOT_FIELDS, ROOT_FIELD_SPECS),
            attempt_csv.name: _column_metadata(ATTEMPT_FIELDS, ATTEMPT_FIELD_SPECS),
        },
        "validation": validation,
    }
    _write_text(metadata_path, json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    _write_text(report_path, _validation_report(validation, result.run_id))
    return root_csv, attempt_csv, metadata_path, report_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract read-only Experiment 1 Factorization analysis tables."
    )
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = extract_analysis(run_dir=args.run_dir, catalog_path=args.catalog)
    validation = validate_analysis(result, run_dir=args.run_dir)
    output_paths = write_analysis(
        result,
        validation,
        run_dir=args.run_dir,
        catalog_path=args.catalog,
        output_dir=args.output_dir,
    )
    print(
        json.dumps(
            {
                "run_id": result.run_id,
                "root_rows": len(result.root_rows),
                "attempt_rows": len(result.attempt_rows),
                "overall_consistent": validation["overall_consistent"],
                "outputs": [str(path) for path in output_paths],
            },
            ensure_ascii=False,
        )
    )
    return 0


__all__ = [
    "ATTEMPT_FIELDS",
    "AnalysisResult",
    "ROOT_FIELDS",
    "assign_input_scale_groups",
    "attempt_failure_type",
    "extract_analysis",
    "main",
    "nullable_sum",
    "partition_ranges",
    "validate_analysis",
    "write_analysis",
]


if __name__ == "__main__":
    raise SystemExit(main())
