from __future__ import annotations

import copy
import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Iterator, Mapping, Sequence

from tokenshare.experiments.paper_pipeline_profile import load_paper_pipeline_profile


PAPER_METRIC_CONTRACT_SCHEMA_VERSION = "tokenshare.paper_metric_contract.v1"
PAPER_METRIC_CONTRACT_ID = "epd027_paper_metric_contract.v1"
PAPER_METRIC_CONTRACT_VERSION = 1
PAPER_METRIC_CONTRACT_DIGEST = (
    "sha256:b72307e727e28f5233de934df28e1701cf46ffb711b99aa845ea6f8580694b0e"
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PAPER_METRIC_CONTRACT_PATH = (
    _REPOSITORY_ROOT / "benchmarks" / "paper" / "paper_metric_contract.v1.json"
)

OPERAND_KINDS = frozenset(
    {
        "number",
        "nonnegative_integer",
        "number_sequence",
        "member_set",
        "timestamp_ms",
        "verified_count",
    }
)
FORMULA_OPERATIONS = frozenset(
    {
        "count",
        "direct",
        "elapsed",
        "maximum",
        "median",
        "median_pairwise_ratio",
        "minimum",
        "percentage_point_delta",
        "parallel_efficiency",
        "range",
        "relative_range",
        "ratio",
        "signed_max",
        "signed_max_percentage_point_delta",
        "signed_mean",
        "signed_mean_percentage_point_delta",
        "subtract",
        "sum",
        "worker_utilization",
    }
)
NULL_TRIGGERS = frozenset(
    {
        "incomplete_pair",
        "infra_invalid",
        "insufficient_values",
        "membership_excluded",
        "missing_required_evidence",
        "not_applicable",
        "zero_denominator",
    }
)
NULL_ACTIONS = frozenset(
    {
        "null_and_publish_blocked",
        "null_with_explicit_reason",
        "null_with_recorded_exclusion",
    }
)
CLAIM_ROLES = frozenset({"primary", "secondary", "auxiliary", "audit_only"})
PREDICATE_OPERATIONS = frozenset(
    {
        "all",
        "always",
        "any",
        "field_equals",
        "field_false",
        "field_in",
        "field_true",
        "fields_equal",
        "fields_not_equal",
        "integer_successor",
        "not",
        "field_nonempty_string",
        "number_positive",
        "ordered_roles_exact",
    }
)
INVARIANT_OPERATIONS = frozenset({"sum_equals"})

VALUE_DOMAINS = frozenset(
    {
        "count_nonnegative_integer",
        "proportion",
        "ratio_unbounded",
        "nonnegative_number",
        "signed_number",
    }
)


class PredicateTruth(str, Enum):
    TRUE = "true"
    FALSE = "false"
    MISSING = "missing"


class MembershipStatus(str, Enum):
    INCLUDED = "included"
    EXCLUDED = "excluded"
    BLOCKED_MISSING_EVIDENCE = "blocked_missing_evidence"

_ONLINE_REAL_PROVIDER_ROLES = (
    "request_body",
    "raw_output_or_provider_failure",
    "provenance",
    "usage_status",
    "latency",
    "pricing",
    "provider_attempt",
    "model_record",
)
_REAL_MODEL_TRACE_SOURCE_ROLES = (
    "request_body",
    "raw_output_or_provider_failure",
    "provenance",
    "usage_status",
    "latency",
    "pricing",
    "acquisition_attempt",
    "model_record",
)
_EVIDENCE_CLASSES = frozenset(
    {"online_real_provider", "real_model_trace_protocol_run"}
)
_TABLE_IDS = (
    "exp1_feasibility",
    "exp2_trace_scalability",
    "exp2_online_concurrency",
    "exp3_trace_robustness",
    "exp3_online_recovery",
    "exp4_ablation",
    "exp5_quality",
    "exp5_resources",
)
_MAIN_PAIRED_WORKER_KEY = (
    "case_record_digest",
    "repeat_id",
    "sample_slot_index",
    "baseline_worker_count",
    "compared_worker_count",
)
_EXP3_TRACE_PAIR_KEY = (
    "case_record_digest",
    "repeat_id",
    "sample_slot_index",
    "fault_or_death_condition",
    "paired_trace_reference",
)
_EXP4_ABLATION_PAIR_KEY = (
    "case_record_digest",
    "repeat_id",
    "sample_slot_index",
    "FULL",
    "ablation_mode",
)
_EXP3_FALSE_POSITIVE_METRIC_IDS = frozenset(
    {
        "controlled_wrong_candidate_count",
        "controlled_wrong_candidate_interception_count",
        "controlled_wrong_candidate_interception_rate",
        "controlled_wrong_candidate_escape_count",
        "controlled_wrong_candidate_escape_rate",
    }
)
_EXP3_WORKER_DEATH_METRIC_IDS = frozenset(
    {
        "kill_progress_error_pp",
        "kill_progress_error_signed_mean_pp",
        "kill_progress_error_signed_max_pp",
        "recovered_valid_canonical_required_slots",
        "preregistered_required_slots",
        "result_completeness_rate",
    }
)

_RETIRED_METRIC_IDS = (
    "accepted_validity_rate",
    "throughput",
    "throughput_roots_per_second",
    "efficiency",
    "recovery_rate",
    "recovery_latency",
    "recovery_latency_ms",
    "exposed_error_count",
    "escaped_error_count",
    "error_escape_rate",
    "wrong_canonical_count",
    "raw_only_count",
    "stuck_count",
    "premature_merge_count",
    "paired_sample_size",
    "paired_case_count",
    "paired_difference",
    "actual_provider_attempt_count",
    "retry_count",
    "retry_success_rate",
    "model_pairwise_significance",
    "model_pairwise_rank",
    "composite_model_score",
)


@dataclass(frozen=True, kw_only=True)
class _FormulaFamily:
    op: str
    operand_kinds: tuple[str, ...]
    exact_sequence_length: int | None = None
    minimum_sequence_length: int | None = None


_COUNT = _FormulaFamily(op="count", operand_kinds=("member_set",))
_DIRECT = _FormulaFamily(op="direct", operand_kinds=("number",))
_DIRECT_INTEGER = _FormulaFamily(
    op="direct", operand_kinds=("nonnegative_integer",)
)
_SUM = _FormulaFamily(op="sum", operand_kinds=("number_sequence",))
_RATIO = _FormulaFamily(op="ratio", operand_kinds=("number", "number"))
_PAIRED_RATIO = _FormulaFamily(
    op="ratio", operand_kinds=("number_sequence", "number_sequence")
)
_PAIRED_SUBTRACT = _FormulaFamily(
    op="subtract", operand_kinds=("number_sequence", "number_sequence")
)
_ELAPSED = _FormulaFamily(
    op="elapsed", operand_kinds=("number_sequence", "number_sequence")
)
_FORMULA_FAMILIES = {
    "absolute_difference_ablation_minus_full_v1": _PAIRED_SUBTRACT,
    "absolute_difference_fault_minus_reference_v1": _PAIRED_SUBTRACT,
    "absolute_difference_full_minus_ablation_v1": _PAIRED_SUBTRACT,
    "count_committed_trace_consumptions_v1": _COUNT,
    "count_members_v1": _COUNT,
    "direct_observation_value_v1": _DIRECT,
    "direct_nonnegative_integer_v1": _DIRECT_INTEGER,
    "enclosing_elapsed_condition_start_to_all_roots_terminal_v1": _ELAPSED,
    "enclosing_elapsed_first_dispatch_to_all_roots_terminal_v1": _ELAPSED,
    "max_minus_min_of_three_raw_repeats_v1": _FormulaFamily(
        op="range", operand_kinds=("number", "number", "number")
    ),
    "maximum_of_three_raw_repeats_v1": _FormulaFamily(
        op="maximum", operand_kinds=("number", "number", "number")
    ),
    "maximum_of_two_raw_repeat_summaries_v1": _FormulaFamily(
        op="maximum", operand_kinds=("number_sequence",), exact_sequence_length=2
    ),
    "median_eligible_pairs_within_repeat_v1": _FormulaFamily(
        op="median_pairwise_ratio",
        operand_kinds=("number_sequence", "number_sequence"),
        minimum_sequence_length=1,
    ),
    "median_of_three_raw_repeats_v1": _FormulaFamily(
        op="median", operand_kinds=("number", "number", "number")
    ),
    "minimum_of_three_raw_repeats_v1": _FormulaFamily(
        op="minimum", operand_kinds=("number", "number", "number")
    ),
    "minimum_of_two_raw_repeat_summaries_v1": _FormulaFamily(
        op="minimum", operand_kinds=("number_sequence",), exact_sequence_length=2
    ),
    "paired_ratio_worker1_over_workerk_v1": _PAIRED_RATIO,
    "paired_ratio_workerk_over_worker1_v1": _PAIRED_RATIO,
    "ratio_executed_worker_time_over_capacity_v1": _FormulaFamily(
        op="worker_utilization",
        operand_kinds=("number_sequence", "nonnegative_integer", "number"),
    ),
    "ratio_paired_speedup_over_configured_workers_v1": _FormulaFamily(
        op="parallel_efficiency",
        operand_kinds=("number_sequence", "number_sequence", "number_sequence"),
    ),
    "ratio_unique_union_over_actual_first_attempts_v1": _RATIO,
    "ratio_v1": _RATIO,
    "relative_difference_of_two_repeat_summaries_v1": _FormulaFamily(
        op="relative_range",
        operand_kinds=("number_sequence",),
        exact_sequence_length=2,
    ),
    "ratio_number_over_member_count_v1": _FormulaFamily(
        op="ratio", operand_kinds=("number", "member_set")
    ),
    "signed_max_per_death_v1": _FormulaFamily(
        op="signed_max_percentage_point_delta",
        operand_kinds=("number_sequence", "number_sequence"),
        minimum_sequence_length=1,
    ),
    "signed_mean_per_death_v1": _FormulaFamily(
        op="signed_mean_percentage_point_delta",
        operand_kinds=("number_sequence", "number_sequence"),
        minimum_sequence_length=1,
    ),
    "signed_percentage_point_error_actual_minus_target_v1": _FormulaFamily(
        op="percentage_point_delta",
        operand_kinds=("number_sequence", "number_sequence"),
    ),
    "sum_actual_provider_attempt_latency_v1": _SUM,
    "sum_complete_members_v1": _SUM,
    "sum_source_cost_per_committed_consumption_v1": _SUM,
    "sum_source_usage_for_fault_discarded_consumptions_v1": _SUM,
    "sum_source_usage_per_committed_consumption_v1": _SUM,
}


@dataclass(frozen=True)
class OperandRef:
    kind: str
    id: str


@dataclass(frozen=True)
class FormulaSpec:
    op: str
    operands: tuple[OperandRef, ...]


@dataclass(frozen=True)
class NullRule:
    trigger: str
    action: str
    reason: str


@dataclass(frozen=True, kw_only=True)
class PredicateSpec:
    op: str
    args: tuple[PredicateSpec, ...]
    field: str | None
    other_field: str | None
    value: str | int | bool | None
    values: tuple[str | int | bool | None, ...]
    failure_reason: str | None


@dataclass(frozen=True, kw_only=True)
class MembershipSpec:
    membership_id: str
    predicate: PredicateSpec
    membership_kind: str = "source_selector"
    mode_value: str | None = None


@dataclass(frozen=True, kw_only=True)
class VerifiedMetricObservation:
    """测试/重算依赖输入；不授予 production paper eligibility。"""
    observation_id: str
    table_id: str
    metric_id: str
    row_kind: str
    value: int | Decimal
    value_domain: str
    source_member_ids: tuple[str, ...]
    evidence_verified: bool
    row_identity_digest: str
    recompute_only: bool = True
    paper_eligible: bool = False

    def __post_init__(self) -> None:
        for value, name in (
            (self.observation_id, "observation_id"),
            (self.table_id, "table_id"),
            (self.metric_id, "metric_id"),
            (self.row_kind, "row_kind"),
        ):
            _non_empty_string(value, name)
        if self.value_domain not in VALUE_DOMAINS:
            raise ValueError("unknown observation value domain")
        if self.value_domain == "count_nonnegative_integer":
            _coerce_nonnegative_integer(self.value, self.metric_id)
        else:
            number = _coerce_number(self.value, self.metric_id)
            if self.value_domain == "proportion" and not (
                Decimal(0) <= number <= Decimal(1)
            ):
                raise ValueError(f"proportion observation out of range: {self.metric_id}")
            if self.value_domain in {"ratio_unbounded", "nonnegative_number"} and (
                number < 0
            ):
                raise ValueError(
                    f"nonnegative observation required: {self.metric_id}"
                )
        if not isinstance(self.source_member_ids, tuple):
            object.__setattr__(
                self, "source_member_ids", _string_tuple(self.source_member_ids, "source_member_ids")
            )
        else:
            _string_tuple(self.source_member_ids, "source_member_ids")
        if not isinstance(self.evidence_verified, bool):
            raise ValueError("evidence_verified must be a bool")
        _digest(self.row_identity_digest, "row_identity_digest")
        if self.recompute_only is not True or self.paper_eligible is not False:
            raise ValueError(
                "verified dependency observations must be recompute_only and not paper eligible"
            )


@dataclass(frozen=True, kw_only=True)
class MetricObservationBundle:
    """合同 evaluator 输入；producer 必须读取 canonical runtime 的持久化结果。"""
    row_facts: Mapping[str, Any]
    member_ids: tuple[str, ...]
    member_facts_by_id: Mapping[str, Mapping[str, Any]]
    verified_observations: tuple[VerifiedMetricObservation, ...] = ()
    row_identity_digest: str = "sha256:" + "0" * 64

    def __post_init__(self) -> None:
        if not isinstance(self.row_facts, Mapping):
            raise ValueError("row_facts must be a mapping")
        _digest(self.row_identity_digest, "row_identity_digest")
        member_ids = tuple(self.member_ids)
        for member_id in member_ids:
            _non_empty_string(member_id, "member id")
        if len(set(member_ids)) != len(member_ids):
            raise ValueError("duplicate member id")
        if not isinstance(self.member_facts_by_id, Mapping):
            raise ValueError("member_facts_by_id must be a mapping")
        if set(self.member_facts_by_id) != set(member_ids):
            raise ValueError("member_facts_by_id keys must exactly match member_ids")
        original_facts = [self.member_facts_by_id[item] for item in member_ids]
        if len({id(item) for item in original_facts}) != len(original_facts):
            raise ValueError("each member id requires independent facts")
        frozen_member_facts: dict[str, Mapping[str, Any]] = {}
        for member_id, facts in zip(member_ids, original_facts, strict=True):
            if not isinstance(facts, Mapping):
                raise ValueError("member facts must be mappings")
            frozen_member_facts[member_id] = _deep_freeze_mapping(facts)
        observations = tuple(self.verified_observations)
        if not all(isinstance(item, VerifiedMetricObservation) for item in observations):
            raise ValueError("verified_observations must contain VerifiedMetricObservation")
        observation_keys = tuple((item.table_id, item.metric_id) for item in observations)
        if len(set(observation_keys)) != len(observation_keys):
            raise ValueError("duplicate verified observation key")
        for observation in observations:
            if observation.row_identity_digest != self.row_identity_digest:
                raise ValueError("observation row identity digest mismatch")
            if not set(observation.source_member_ids) <= set(member_ids):
                raise ValueError("observation source members are outside the bundle")
        object.__setattr__(self, "row_facts", _deep_freeze_mapping(self.row_facts))
        object.__setattr__(self, "member_ids", member_ids)
        object.__setattr__(
            self, "member_facts_by_id", MappingProxyType(frozen_member_facts)
        )
        object.__setattr__(self, "verified_observations", observations)

    @property
    def is_empty(self) -> bool:
        return not self.row_facts and not self.member_ids and not self.verified_observations

    def observation(self, table_id: str, metric_id: str) -> VerifiedMetricObservation | None:
        matches = tuple(
            item
            for item in self.verified_observations
            if item.table_id == table_id and item.metric_id == metric_id
        )
        return matches[0] if len(matches) == 1 else None


@dataclass(frozen=True, kw_only=True)
class InvariantSpec:
    invariant_id: str
    table: str
    op: str
    operands: tuple[OperandRef, ...]
    target: OperandRef
    violation_reason: str
    required_for_publication: bool = True


@dataclass(frozen=True, kw_only=True)
class MetricContractControls:
    infra_invalid_denominator_policy: str
    infra_invalid_publish_policy: str
    exp2_429_or_timeout_union_fraction_max: Decimal
    exp2_union_denominator: str
    exp2_union_identity: str
    exp2_online_trace_same_case_intersection_min: int
    severe_speedup_ratio_min: Decimal
    severe_speedup_ratio_max: Decimal
    severe_opposed_trend_high: Decimal
    severe_opposed_trend_low: Decimal
    exp3_comparison_kind: str
    exp3_ratio_outputs_audit_only: bool
    exp4_full_is_separate_baseline: bool
    exp5_summary_table_count: int
    exp5_repeat_count: int
    exp5_max_retries: int
    exp5_first_nonpass_reason_priority: tuple[str, ...]
    exp5_endpoint_serving_confounding_caption: str


@dataclass(frozen=True, kw_only=True)
class MetricTableDefinition:
    table_id: str
    experiment_id: str
    output_path: str
    evidence_classes: tuple[str, ...]
    row_scope: str
    row_kinds: tuple[str, ...]
    numeric_output_fields: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class ProducerBoundary:
    evidence_origin: str
    metric_builder: str


@dataclass(frozen=True, kw_only=True)
class ConsumerFlow:
    stages: tuple[str, ...]
    renderer_table: str


@dataclass(frozen=True, kw_only=True)
class ApplicabilitySpec:
    kind: str
    field: str | None
    values: tuple[str, ...]
    not_applicable_reason: str | None


@dataclass(frozen=True, kw_only=True)
class MetricDefinition:
    metric_id: str
    formula_id: str
    formula: FormulaSpec
    evidence_classes: tuple[str, ...]
    required_current_provider_roles: tuple[str, ...]
    required_source_bank_roles: tuple[str, ...]
    row_scope: str
    row_kind: str
    numerator: OperandRef | None
    denominator: OperandRef | None
    null_rules: tuple[NullRule, ...]
    pair_key: tuple[str, ...]
    applicability: ApplicabilitySpec
    source_membership_id: str | None
    row_gate_membership_id: str
    value_domain: str
    required_invariant_ids: tuple[str, ...]
    claim_role: str
    producer: ProducerBoundary
    consumer: ConsumerFlow
    table: str
    deprecated_aliases: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class MetricEvaluation:
    value: Decimal | int | None
    reason: str | None = None
    publish_blocked: bool = False
    exclusion_reason: str | None = None
    included_member_ids: tuple[str, ...] = ()
    excluded_member_ids: tuple[str, ...] = ()
    blocked_member_ids: tuple[str, ...] = ()
    audit_denominator_member_ids: tuple[str, ...] = ()
    member_decisions: tuple[MemberDecision, ...] = ()
    paper_eligible: bool = False


@dataclass(frozen=True, kw_only=True)
class MetricComputationTrace:
    """真实 contract evaluator 返回值与其不可变输入的 scoped 捕获。"""

    trace_id: str
    contract_id: str
    contract_digest: str
    pipeline_profile_id: str
    pipeline_profile_digest: str
    table_id: str
    metric_id: str
    formula_id: str
    row_kind: str
    row_identity_digest: str
    cell_identity_digest: str
    bundle: MetricObservationBundle
    evaluation: MetricEvaluation
    schema_version: str = "tokenshare.metric_computation_trace.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "tokenshare.metric_computation_trace.v1":
            raise ValueError("unsupported metric computation trace schema")
        for value, field_name in (
            (self.contract_id, "contract_id"),
            (self.pipeline_profile_id, "pipeline_profile_id"),
            (self.table_id, "table_id"),
            (self.metric_id, "metric_id"),
            (self.formula_id, "formula_id"),
            (self.row_kind, "row_kind"),
        ):
            _non_empty_string(value, field_name)
        for value, field_name in (
            (self.trace_id, "trace_id"),
            (self.contract_digest, "contract_digest"),
            (self.pipeline_profile_digest, "pipeline_profile_digest"),
            (self.row_identity_digest, "row_identity_digest"),
            (self.cell_identity_digest, "cell_identity_digest"),
        ):
            _digest(value, field_name)
        if not isinstance(self.bundle, MetricObservationBundle):
            raise TypeError("metric trace bundle must be MetricObservationBundle")
        if not isinstance(self.evaluation, MetricEvaluation):
            raise TypeError("metric trace evaluation must be MetricEvaluation")
        if self.row_identity_digest != self.bundle.row_identity_digest:
            raise ValueError("metric trace row identity crosses captured bundle")
        if self.trace_id != self.cell_identity_digest:
            raise ValueError("metric trace cell identity digest mismatch")


_METRIC_TRACE_CAPTURE: ContextVar[list[MetricComputationTrace] | None] = ContextVar(
    "tokenshare_metric_trace_capture",
    default=None,
)


class _IdentityBoundMemberDecisions(tuple):
    """Projector 会原样复制 membership；此 marker 只在 capture scope 内存在。"""

    def __new__(
        cls,
        values: Sequence["MemberDecision"],
        *,
        row_identity_digest: str,
        cell_identity_digest: str,
    ) -> "_IdentityBoundMemberDecisions":
        instance = super().__new__(cls, values)
        instance.row_identity_digest = row_identity_digest
        instance.cell_identity_digest = cell_identity_digest
        return instance


def metric_cell_identity_from_membership(
    membership: object,
) -> tuple[str, str] | None:
    """读取 capture scope 传给真实 projector cell 的 canonical identity。"""

    if not isinstance(membership, _IdentityBoundMemberDecisions):
        return None
    return membership.row_identity_digest, membership.cell_identity_digest


@contextmanager
def capture_metric_computation_traces() -> Iterator[list[MetricComputationTrace]]:
    """只在显式 scope 内收集真实 evaluator 返回，不改变计算语义。"""

    if _METRIC_TRACE_CAPTURE.get() is not None:
        raise RuntimeError("nested metric computation capture is not supported")
    traces: list[MetricComputationTrace] = []
    token = _METRIC_TRACE_CAPTURE.set(traces)
    try:
        yield traces
    finally:
        _METRIC_TRACE_CAPTURE.reset(token)


@dataclass(frozen=True, kw_only=True)
class MemberDecision:
    member_id: str
    status: MembershipStatus
    reason: str | None = None


@dataclass(frozen=True, kw_only=True)
class MembershipEvaluation:
    status: MembershipStatus
    predicate_truth: PredicateTruth
    exclusion_reason: str | None = None

    @property
    def included(self) -> bool:
        return self.status is MembershipStatus.INCLUDED


@dataclass(frozen=True, kw_only=True)
class InvariantEvaluation:
    satisfied: bool
    violation_reason: str | None = None
    publish_blocked: bool = False


@dataclass(frozen=True, kw_only=True)
class PaperMetricContract:
    schema_version: str
    contract_id: str
    contract_version: int
    pipeline_profile_id: str
    pipeline_profile_digest: str
    controls: MetricContractControls
    tables: tuple[MetricTableDefinition, ...]
    memberships: tuple[MembershipSpec, ...]
    invariants: tuple[InvariantSpec, ...]
    metrics: tuple[MetricDefinition, ...]
    retired_metric_ids: tuple[str, ...]
    renderer_only_derived_metrics_forbidden: bool
    contract_digest: str
    provider_calls: int = 0

    @property
    def numeric_output_field_count(self) -> int:
        return sum(len(table.numeric_output_fields) for table in self.tables)

    def require_table(self, table_id: str) -> MetricTableDefinition:
        matches = tuple(table for table in self.tables if table.table_id == table_id)
        if len(matches) != 1:
            raise ValueError(f"unknown paper metric table: {table_id}")
        return matches[0]

    def require_metric(self, table_id: str, metric_id: str) -> MetricDefinition:
        matches = tuple(
            metric
            for metric in self.metrics
            if metric.table == table_id and metric.metric_id == metric_id
        )
        if len(matches) != 1:
            raise ValueError(f"unknown paper metric definition: {table_id}.{metric_id}")
        return matches[0]

    def require_membership(self, membership_id: str) -> MembershipSpec:
        matches = tuple(
            item for item in self.memberships if item.membership_id == membership_id
        )
        if len(matches) != 1:
            raise ValueError(f"unknown membership: {membership_id}")
        return matches[0]

    def require_invariant(self, invariant_id: str) -> InvariantSpec:
        matches = tuple(
            item for item in self.invariants if item.invariant_id == invariant_id
        )
        if len(matches) != 1:
            raise ValueError(f"unknown invariant: {invariant_id}")
        return matches[0]

    def validate_numeric_output_fields(
        self, table_id: str, numeric_output_fields: Sequence[str]
    ) -> None:
        table = self.require_table(table_id)
        actual = _string_tuple(
            numeric_output_fields, f"renderer numeric fields for {table_id}"
        )
        for field in actual:
            if field not in table.numeric_output_fields:
                raise ValueError(f"uncontracted numeric output field: {field}")
        for field in table.numeric_output_fields:
            if field not in actual:
                raise ValueError(f"missing contracted numeric output field: {field}")
        if actual != table.numeric_output_fields:
            raise ValueError(f"numeric output field order drift: {table_id}")

    def validate_registry_metric_keys(
        self, metric_keys: Iterable[tuple[str, str]]
    ) -> None:
        live = {(metric.table, metric.metric_id) for metric in self.metrics}
        retired = set(self.retired_metric_ids)
        for key in metric_keys:
            if not isinstance(key, tuple) or len(key) != 2:
                raise ValueError("metric registry requires composite (table_id, metric_id) keys")
            table_id = _non_empty_string(key[0], "registry table id")
            metric_id = _non_empty_string(key[1], "registry metric id")
            if metric_id in retired:
                raise ValueError(
                    f"retired metric id at registry boundary: {table_id}.{metric_id}"
                )
            if (table_id, metric_id) not in live:
                raise ValueError(
                    f"uncontracted metric key at registry boundary: {table_id}.{metric_id}"
                )

    def validate_registry_metric_ids(self, metric_ids: Iterable[str]) -> None:
        del metric_ids
        raise ValueError("metric registry requires composite (table_id, metric_id) keys")

    def validate_main_claim_projection(
        self, metric_keys: Iterable[tuple[str, str]]
    ) -> None:
        keys = tuple(metric_keys)
        self.validate_registry_metric_keys(keys)
        for table_id, metric_id in keys:
            if self.require_metric(table_id, metric_id).claim_role != "primary":
                raise ValueError(
                    f"primary claim role required: {table_id}.{metric_id}"
                )

    def validate_main_table_projection(
        self, metric_keys: Iterable[tuple[str, str]]
    ) -> None:
        keys = tuple(metric_keys)
        self.validate_registry_metric_keys(keys)
        for table_id, metric_id in keys:
            if self.require_metric(table_id, metric_id).claim_role not in {
                "primary",
                "secondary",
            }:
                raise ValueError(
                    f"main table claim role required: {table_id}.{metric_id}"
                )


def compute_metric_contract_digest(value: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(value))
    body.pop("contract_digest", None)
    payload = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def load_paper_metric_contract(
    path: str | Path = DEFAULT_PAPER_METRIC_CONTRACT_PATH,
) -> PaperMetricContract:
    contract_path = Path(path)
    try:
        value = json.loads(
            contract_path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_object_pairs,
            parse_constant=_reject_nonfinite_json_constant,
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("unable to load paper metric contract") from exc
    body = _mapping(value, "paper metric contract")
    _require_exact_keys(
        body,
        {
            "schema_version",
            "contract_id",
            "contract_version",
            "pipeline_profile_id",
            "pipeline_profile_digest",
            "controls",
            "tables",
            "memberships",
            "invariants",
            "metrics",
            "retired_metric_ids",
            "renderer_only_derived_metrics_forbidden",
            "contract_digest",
        },
        "paper metric contract",
    )
    if body.get("schema_version") != PAPER_METRIC_CONTRACT_SCHEMA_VERSION:
        raise ValueError("paper metric contract schema version drift")
    if body.get("contract_id") != PAPER_METRIC_CONTRACT_ID:
        raise ValueError("paper metric contract id drift")
    version = _integer(body, "contract_version")
    if version != PAPER_METRIC_CONTRACT_VERSION:
        raise ValueError("paper metric contract version drift")

    pipeline = load_paper_pipeline_profile()
    pipeline_profile_id = _non_empty_string(
        body.get("pipeline_profile_id"), "pipeline_profile_id"
    )
    pipeline_profile_digest = _digest(
        body.get("pipeline_profile_digest"), "pipeline_profile_digest"
    )
    if pipeline_profile_id != pipeline.profile_id:
        raise ValueError("paper metric contract pipeline profile id drift")
    if pipeline_profile_digest != pipeline.profile_digest:
        raise ValueError("paper metric contract pipeline profile digest drift")

    controls = _load_controls(_mapping(body.get("controls"), "controls"), pipeline)
    tables = _load_tables(body.get("tables"))
    memberships = _load_memberships(body.get("memberships"))
    invariants = _load_invariants(body.get("invariants"), tables)
    metrics = _load_metrics(body.get("metrics"), tables, memberships, invariants)
    retired_metric_ids = _string_tuple(
        body.get("retired_metric_ids"), "retired_metric_ids"
    )
    if retired_metric_ids != _RETIRED_METRIC_IDS:
        raise ValueError("retired paper metric ids drift")
    renderer_forbidden = _boolean(body, "renderer_only_derived_metrics_forbidden")
    if not renderer_forbidden:
        raise ValueError("renderer-only derived metrics must be forbidden")

    _validate_metric_inventory(tables, metrics, retired_metric_ids)
    _validate_executable_metric_graph(metrics, memberships)
    _validate_invariant_inventory(invariants)
    stored_digest = _digest(body.get("contract_digest"), "contract_digest")
    computed_digest = compute_metric_contract_digest(body)
    if stored_digest != computed_digest:
        raise ValueError("paper metric contract digest drift")
    if computed_digest != PAPER_METRIC_CONTRACT_DIGEST:
        raise ValueError("paper metric contract canonical controls drift")

    return PaperMetricContract(
        schema_version=PAPER_METRIC_CONTRACT_SCHEMA_VERSION,
        contract_id=PAPER_METRIC_CONTRACT_ID,
        contract_version=version,
        pipeline_profile_id=pipeline_profile_id,
        pipeline_profile_digest=pipeline_profile_digest,
        controls=controls,
        tables=tables,
        memberships=memberships,
        invariants=invariants,
        metrics=metrics,
        retired_metric_ids=retired_metric_ids,
        renderer_only_derived_metrics_forbidden=True,
        contract_digest=computed_digest,
        provider_calls=0,
    )


def _load_controls(value: Mapping[str, Any], pipeline: Any) -> MetricContractControls:
    expected_keys = {
        "infra_invalid_denominator_policy",
        "infra_invalid_publish_policy",
        "exp2_429_or_timeout_union_fraction_max",
        "exp2_union_denominator",
        "exp2_union_identity",
        "exp2_online_trace_same_case_intersection_min",
        "severe_speedup_ratio_min",
        "severe_speedup_ratio_max",
        "severe_opposed_trend_high",
        "severe_opposed_trend_low",
        "exp3_comparison_kind",
        "exp3_ratio_outputs_audit_only",
        "exp4_full_is_separate_baseline",
        "exp5_summary_table_count",
        "exp5_repeat_count",
        "exp5_max_retries",
        "exp5_first_nonpass_reason_priority",
        "exp5_endpoint_serving_confounding_caption",
    }
    _require_exact_keys(value, expected_keys, "controls")
    controls = MetricContractControls(
        infra_invalid_denominator_policy=_non_empty_string(
            value.get("infra_invalid_denominator_policy"),
            "infra_invalid_denominator_policy",
        ),
        infra_invalid_publish_policy=_non_empty_string(
            value.get("infra_invalid_publish_policy"), "infra_invalid_publish_policy"
        ),
        exp2_429_or_timeout_union_fraction_max=_decimal_string(
            value.get("exp2_429_or_timeout_union_fraction_max"),
            "exp2_429_or_timeout_union_fraction_max",
        ),
        exp2_union_denominator=_non_empty_string(
            value.get("exp2_union_denominator"), "exp2_union_denominator"
        ),
        exp2_union_identity=_non_empty_string(
            value.get("exp2_union_identity"), "exp2_union_identity"
        ),
        exp2_online_trace_same_case_intersection_min=_integer(
            value, "exp2_online_trace_same_case_intersection_min"
        ),
        severe_speedup_ratio_min=_decimal_string(
            value.get("severe_speedup_ratio_min"), "severe_speedup_ratio_min"
        ),
        severe_speedup_ratio_max=_decimal_string(
            value.get("severe_speedup_ratio_max"), "severe_speedup_ratio_max"
        ),
        severe_opposed_trend_high=_decimal_string(
            value.get("severe_opposed_trend_high"), "severe_opposed_trend_high"
        ),
        severe_opposed_trend_low=_decimal_string(
            value.get("severe_opposed_trend_low"), "severe_opposed_trend_low"
        ),
        exp3_comparison_kind=_non_empty_string(
            value.get("exp3_comparison_kind"), "exp3_comparison_kind"
        ),
        exp3_ratio_outputs_audit_only=_boolean(
            value, "exp3_ratio_outputs_audit_only"
        ),
        exp4_full_is_separate_baseline=_boolean(
            value, "exp4_full_is_separate_baseline"
        ),
        exp5_summary_table_count=_integer(value, "exp5_summary_table_count"),
        exp5_repeat_count=_integer(value, "exp5_repeat_count"),
        exp5_max_retries=_integer(value, "exp5_max_retries"),
        exp5_first_nonpass_reason_priority=_string_tuple(
            value.get("exp5_first_nonpass_reason_priority"),
            "exp5_first_nonpass_reason_priority",
        ),
        exp5_endpoint_serving_confounding_caption=_non_empty_string(
            value.get("exp5_endpoint_serving_confounding_caption"),
            "exp5_endpoint_serving_confounding_caption",
        ),
    )
    expected = MetricContractControls(
        infra_invalid_denominator_policy="retain_all_preregistered_root_inventory",
        infra_invalid_publish_policy="null_and_blocked",
        exp2_429_or_timeout_union_fraction_max=(
            pipeline.metric_controls.exp2_429_or_timeout_union_fraction_max
        ),
        exp2_union_denominator=pipeline.metric_controls.exp2_union_denominator,
        exp2_union_identity=pipeline.metric_controls.exp2_union_identity,
        exp2_online_trace_same_case_intersection_min=(
            pipeline.metric_controls.exp2_online_trace_same_case_intersection_min
        ),
        severe_speedup_ratio_min=pipeline.metric_controls.severe_speedup_ratio_min,
        severe_speedup_ratio_max=pipeline.metric_controls.severe_speedup_ratio_max,
        severe_opposed_trend_high=pipeline.metric_controls.severe_opposed_trend_high,
        severe_opposed_trend_low=pipeline.metric_controls.severe_opposed_trend_low,
        exp3_comparison_kind="paired_trace_reference",
        exp3_ratio_outputs_audit_only=True,
        exp4_full_is_separate_baseline=True,
        exp5_summary_table_count=2,
        exp5_repeat_count=3,
        exp5_max_retries=0,
        exp5_first_nonpass_reason_priority=(
            "provider_transport_failure",
            "parse_schema_unusable",
            "verification_checker_rejection",
        ),
        exp5_endpoint_serving_confounding_caption=(
            "endpoint_and_serving_profile_are_confounding_factors"
        ),
    )
    if controls != expected:
        raise ValueError("paper metric contract controls drift")
    return controls


def _load_tables(value: Any) -> tuple[MetricTableDefinition, ...]:
    items = _array(value, "tables")
    tables: list[MetricTableDefinition] = []
    for index, item in enumerate(items):
        row = _mapping(item, f"tables[{index}]")
        _require_exact_keys(
            row,
            {
                "table_id",
                "experiment_id",
                "output_path",
                "evidence_classes",
                "row_scope",
                "row_kinds",
                "numeric_output_fields",
            },
            f"tables[{index}]",
        )
        table_id = _non_empty_string(row.get("table_id"), "table_id")
        declared_numeric_fields = _string_tuple(
            row.get("numeric_output_fields"), "numeric_output_fields"
        )
        table = MetricTableDefinition(
            table_id=table_id,
            experiment_id=_non_empty_string(
                row.get("experiment_id"), "experiment_id"
            ),
            output_path=_non_empty_string(row.get("output_path"), "output_path"),
            evidence_classes=_string_tuple(
                row.get("evidence_classes"), "evidence_classes"
            ),
            row_scope=_non_empty_string(row.get("row_scope"), "row_scope"),
            row_kinds=_string_tuple(row.get("row_kinds"), "row_kinds"),
            numeric_output_fields=declared_numeric_fields,
        )
        if not table.row_kinds:
            raise ValueError(f"table row kinds must not be empty: {table.table_id}")
        if len(table.evidence_classes) != 1 or table.evidence_classes[0] not in (
            _EVIDENCE_CLASSES
        ):
            raise ValueError(f"unsupported evidence class for table: {table.table_id}")
        if not table.output_path.startswith("metrics/"):
            raise ValueError(f"paper metric output path drift: {table.table_id}")
        tables.append(table)
    result = tuple(tables)
    if tuple(table.table_id for table in result) != _TABLE_IDS:
        raise ValueError("paper metric table inventory drift")
    if len({table.output_path for table in result}) != len(result):
        raise ValueError("paper metric output path must be unique")
    return result


def _load_memberships(value: Any) -> tuple[MembershipSpec, ...]:
    items = _array(value, "memberships")
    result: list[MembershipSpec] = []
    for index, item in enumerate(items):
        row = _mapping(item, f"memberships[{index}]")
        _require_exact_keys(
            row,
            {"membership_id", "membership_kind", "mode_value", "predicate"},
            f"memberships[{index}]",
        )
        membership_kind = _non_empty_string(
            row.get("membership_kind"), "membership_kind"
        )
        if membership_kind not in {"source_selector", "row_gate"}:
            raise ValueError(f"unsupported membership kind: {membership_kind}")
        mode_value = _nullable_string(row.get("mode_value"), "mode_value")
        if membership_kind == "source_selector" and mode_value is not None:
            raise ValueError("source selector cannot declare mode_value")
        result.append(
            MembershipSpec(
                membership_id=_non_empty_string(
                    row.get("membership_id"), "membership_id"
                ),
                predicate=_load_predicate(
                    row.get("predicate"), f"memberships[{index}].predicate"
                ),
                membership_kind=membership_kind,
                mode_value=mode_value,
            )
        )
    parsed = tuple(result)
    if len({item.membership_id for item in parsed}) != len(parsed):
        raise ValueError("membership ids must be unique")
    return parsed


def _load_invariants(
    value: Any, tables: tuple[MetricTableDefinition, ...]
) -> tuple[InvariantSpec, ...]:
    items = _array(value, "invariants")
    table_ids = {table.table_id for table in tables}
    result: list[InvariantSpec] = []
    for index, item in enumerate(items):
        row = _mapping(item, f"invariants[{index}]")
        _require_exact_keys(
            row,
            {
                "invariant_id",
                "table",
                "op",
                "operands",
                "target",
                "violation_reason",
                "required_for_publication",
            },
            f"invariants[{index}]",
        )
        table = _non_empty_string(row.get("table"), "invariant table")
        if table not in table_ids:
            raise ValueError(f"unknown invariant table: {table}")
        op = _non_empty_string(row.get("op"), "invariant op")
        if op not in INVARIANT_OPERATIONS:
            raise ValueError(f"unsupported invariant op: {op}")
        operands = tuple(
            _load_operand_ref(item, f"invariants[{index}].operands")
            for item in _array(row.get("operands"), "invariant operands")
        )
        target = _load_operand_ref(row.get("target"), "invariant target")
        if not operands or any(
            ref.kind != "verified_count" for ref in (*operands, target)
        ):
            raise ValueError("invariant operands must be verified count refs")
        for ref in (*operands, target):
            if "." not in ref.id or ref.id.split(".", 1)[0] != table:
                raise ValueError("invariant refs must target the invariant table")
        result.append(
            InvariantSpec(
                invariant_id=_non_empty_string(
                    row.get("invariant_id"), "invariant_id"
                ),
                table=table,
                op=op,
                operands=operands,
                target=target,
                violation_reason=_non_empty_string(
                    row.get("violation_reason"), "violation_reason"
                ),
                required_for_publication=_boolean(
                    row, "required_for_publication"
                ),
            )
        )
    parsed = tuple(result)
    if len({item.invariant_id for item in parsed}) != len(parsed):
        raise ValueError("invariant ids must be unique")
    return parsed


def _load_predicate(value: Any, field_name: str) -> PredicateSpec:
    row = _mapping(value, field_name)
    _require_exact_keys(
        row,
        {
            "op",
            "args",
            "field",
            "other_field",
            "value",
            "values",
            "failure_reason",
        },
        field_name,
    )
    op = _non_empty_string(row.get("op"), f"{field_name}.op")
    if op not in PREDICATE_OPERATIONS:
        raise ValueError(f"unsupported predicate op: {op}")
    args = tuple(
        _load_predicate(item, f"{field_name}.args")
        for item in _array(row.get("args"), f"{field_name}.args")
    )
    field = _nullable_string(row.get("field"), f"{field_name}.field")
    other_field = _nullable_string(
        row.get("other_field"), f"{field_name}.other_field"
    )
    scalar = _json_scalar(row.get("value"), f"{field_name}.value")
    values = tuple(
        _json_scalar(item, f"{field_name}.values")
        for item in _array(row.get("values"), f"{field_name}.values")
    )
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name}.values must not contain duplicates")
    failure_reason = _nullable_string(
        row.get("failure_reason"), f"{field_name}.failure_reason"
    )
    parsed = PredicateSpec(
        op=op,
        args=args,
        field=field,
        other_field=other_field,
        value=scalar,
        values=values,
        failure_reason=failure_reason,
    )
    _validate_predicate_shape(parsed, field_name)
    return parsed


def _validate_predicate_shape(predicate: PredicateSpec, field_name: str) -> None:
    op = predicate.op
    if op == "always":
        valid = (
            not predicate.args
            and predicate.field is None
            and predicate.other_field is None
            and predicate.value is None
            and not predicate.values
            and predicate.failure_reason is None
        )
    elif op in {"all", "any"}:
        valid = (
            bool(predicate.args)
            and predicate.field is None
            and predicate.other_field is None
            and predicate.value is None
            and not predicate.values
        )
    elif op == "not":
        valid = (
            len(predicate.args) == 1
            and predicate.field is None
            and predicate.other_field is None
            and predicate.value is None
            and not predicate.values
        )
    elif op in {
        "field_true",
        "field_false",
        "field_nonempty_string",
        "number_positive",
    }:
        valid = (
            predicate.field is not None
            and not predicate.args
            and predicate.other_field is None
            and predicate.value is None
            and not predicate.values
        )
    elif op == "field_equals":
        valid = (
            predicate.field is not None
            and not predicate.args
            and predicate.other_field is None
            and not predicate.values
        )
    elif op in {"field_in", "ordered_roles_exact"}:
        valid = (
            predicate.field is not None
            and bool(predicate.values)
            and not predicate.args
            and predicate.other_field is None
            and predicate.value is None
        )
    elif op in {"fields_equal", "fields_not_equal", "integer_successor"}:
        valid = (
            predicate.field is not None
            and predicate.other_field is not None
            and not predicate.args
            and predicate.value is None
            and not predicate.values
        )
    else:  # pragma: no cover - op闭集已在上层拦截
        valid = False
    if not valid:
        raise ValueError(f"predicate shape drift: {field_name}.{op}")


def _load_formula(value: Any, formula_id: str, field_name: str) -> FormulaSpec:
    row = _mapping(value, field_name)
    _require_exact_keys(row, {"op", "operands"}, field_name)
    op = _non_empty_string(row.get("op"), f"{field_name}.op")
    if op not in FORMULA_OPERATIONS:
        raise ValueError(f"unsupported formula op: {op}")
    operands = tuple(
        _load_operand_ref(item, f"{field_name}.operands")
        for item in _array(row.get("operands"), f"{field_name}.operands")
    )
    family = _FORMULA_FAMILIES.get(formula_id)
    if family is None:
        raise ValueError(f"unknown formula family: {formula_id}")
    if len(operands) != len(family.operand_kinds):
        raise ValueError(f"formula arity drift: {formula_id}")
    if tuple(ref.kind for ref in operands) != family.operand_kinds:
        raise ValueError(f"formula operand kind drift: {formula_id}")
    if op != family.op:
        raise ValueError(f"formula op drift: {formula_id}")
    return FormulaSpec(op=op, operands=operands)


def _load_operand_ref(value: Any, field_name: str) -> OperandRef:
    row = _mapping(value, field_name)
    _require_exact_keys(row, {"kind", "id"}, field_name)
    kind = _non_empty_string(row.get("kind"), f"{field_name}.kind")
    if kind not in OPERAND_KINDS:
        raise ValueError(f"unsupported operand kind: {kind}")
    return OperandRef(
        kind=kind,
        id=_non_empty_string(row.get("id"), f"{field_name}.id"),
    )


def _load_nullable_operand_ref(value: Any, field_name: str) -> OperandRef | None:
    if value is None:
        return None
    return _load_operand_ref(value, field_name)


def _load_null_rules(value: Any, field_name: str) -> tuple[NullRule, ...]:
    result: list[NullRule] = []
    for index, item in enumerate(_array(value, field_name)):
        row = _mapping(item, f"{field_name}[{index}]")
        _require_exact_keys(
            row, {"trigger", "action", "reason"}, f"{field_name}[{index}]"
        )
        trigger = _non_empty_string(row.get("trigger"), "null trigger")
        action = _non_empty_string(row.get("action"), "null action")
        if trigger not in NULL_TRIGGERS:
            raise ValueError(f"unsupported null trigger: {trigger}")
        if action not in NULL_ACTIONS:
            raise ValueError(f"unsupported null action: {action}")
        result.append(
            NullRule(
                trigger=trigger,
                action=action,
                reason=_non_empty_string(row.get("reason"), "null reason"),
            )
        )
    parsed = tuple(result)
    if not parsed or len({rule.trigger for rule in parsed}) != len(parsed):
        raise ValueError(f"{field_name} triggers must be non-empty and unique")
    return parsed


def _load_applicability(value: Any, field_name: str) -> ApplicabilitySpec:
    row = _mapping(value, field_name)
    _require_exact_keys(
        row,
        {"kind", "field", "values", "not_applicable_reason"},
        field_name,
    )
    kind = _non_empty_string(row.get("kind"), f"{field_name}.kind")
    field = _nullable_string(row.get("field"), f"{field_name}.field")
    values = _string_tuple(row.get("values"), f"{field_name}.values")
    reason = _nullable_string(
        row.get("not_applicable_reason"),
        f"{field_name}.not_applicable_reason",
    )
    if kind == "always":
        if field is not None or values or reason is not None:
            raise ValueError(f"always applicability must be empty: {field_name}")
    elif kind == "field_in":
        if field is None or not values or reason is None:
            raise ValueError(f"field_in applicability is incomplete: {field_name}")
        if len(set(values)) != len(values):
            raise ValueError(f"duplicate applicability value: {field_name}")
    else:
        raise ValueError(f"unsupported applicability kind: {kind}")
    return ApplicabilitySpec(
        kind=kind,
        field=field,
        values=values,
        not_applicable_reason=reason,
    )


def _load_metrics(
    value: Any,
    tables: tuple[MetricTableDefinition, ...],
    memberships: tuple[MembershipSpec, ...],
    invariants: tuple[InvariantSpec, ...],
) -> tuple[MetricDefinition, ...]:
    items = _array(value, "metrics")
    table_by_id = {table.table_id: table for table in tables}
    membership_by_id = {item.membership_id: item for item in memberships}
    invariant_by_id = {item.invariant_id: item for item in invariants}
    result: list[MetricDefinition] = []
    seen_keys: set[tuple[str, str]] = set()
    for index, item in enumerate(items):
        row = _mapping(item, f"metrics[{index}]")
        _require_exact_keys(
            row,
            {
                "metric_id",
                "formula_id",
                "formula",
                "evidence_classes",
                "required_current_provider_roles",
                "required_source_bank_roles",
                "row_scope",
                "row_kind",
                "numerator",
                "denominator",
                "null",
                "pair_key",
                "applicability",
                "source_membership_id",
                "row_gate_membership_id",
                "value_domain",
                "required_invariant_ids",
                "claim_role",
                "producer",
                "consumer",
                "table",
                "deprecated_aliases",
            },
            f"metrics[{index}]",
        )
        metric_id = _non_empty_string(row.get("metric_id"), "metric_id")
        table_id = _non_empty_string(row.get("table"), "table")
        if table_id not in table_by_id:
            raise ValueError(f"unknown paper metric table: {table_id}")
        key = (table_id, metric_id)
        if key in seen_keys:
            raise ValueError("paper metric definition keys must be unique")
        seen_keys.add(key)
        table = table_by_id[table_id]
        formula_id = _non_empty_string(row.get("formula_id"), "formula_id")
        formula = _load_formula(
            row.get("formula"), formula_id, f"metrics[{index}].formula"
        )
        source_membership_id = _nullable_string(
            row.get("source_membership_id"), "source_membership_id"
        )
        if source_membership_id is not None:
            source_membership = membership_by_id.get(source_membership_id)
            if source_membership is None:
                raise ValueError(f"unknown membership: {source_membership_id}")
            if source_membership.membership_kind != "source_selector":
                raise ValueError(f"source membership is not a selector: {source_membership_id}")
        row_gate_membership_id = _non_empty_string(
            row.get("row_gate_membership_id"), "row_gate_membership_id"
        )
        row_gate = membership_by_id.get(row_gate_membership_id)
        if row_gate is None:
            raise ValueError(f"unknown row gate membership: {row_gate_membership_id}")
        if row_gate.membership_kind != "row_gate":
            raise ValueError(f"metric row gate is not a row gate: {row_gate_membership_id}")
        required_invariant_ids = _string_tuple(
            row.get("required_invariant_ids"), "required_invariant_ids"
        )
        for invariant_id in required_invariant_ids:
            invariant = invariant_by_id.get(invariant_id)
            if invariant is None:
                raise ValueError(f"unknown required invariant: {invariant_id}")
            if invariant.table != table_id or not invariant.required_for_publication:
                raise ValueError(f"invalid publication invariant: {table_id}.{invariant_id}")
        metric = MetricDefinition(
            metric_id=metric_id,
            formula_id=formula_id,
            formula=formula,
            evidence_classes=_string_tuple(
                row.get("evidence_classes"), "evidence_classes"
            ),
            required_current_provider_roles=_string_tuple(
                row.get("required_current_provider_roles"),
                "required_current_provider_roles",
            ),
            required_source_bank_roles=_string_tuple(
                row.get("required_source_bank_roles"), "required_source_bank_roles"
            ),
            row_scope=_non_empty_string(row.get("row_scope"), "row_scope"),
            row_kind=_non_empty_string(row.get("row_kind"), "row_kind"),
            numerator=_load_nullable_operand_ref(row.get("numerator"), "numerator"),
            denominator=_load_nullable_operand_ref(
                row.get("denominator"), "denominator"
            ),
            null_rules=_load_null_rules(
                row.get("null"), f"metric null {table_id}.{metric_id}"
            ),
            pair_key=_string_tuple(row.get("pair_key"), "pair_key"),
            applicability=_load_applicability(
                row.get("applicability"),
                f"metrics[{index}].applicability",
            ),
            source_membership_id=source_membership_id,
            row_gate_membership_id=row_gate_membership_id,
            value_domain=_non_empty_string(row.get("value_domain"), "value_domain"),
            required_invariant_ids=required_invariant_ids,
            claim_role=_non_empty_string(row.get("claim_role"), "claim_role"),
            producer=_load_producer_boundary(
                row.get("producer"), table_id=table_id
            ),
            consumer=_load_consumer_flow(
                row.get("consumer"), table_id=table_id
            ),
            table=table_id,
            deprecated_aliases=_string_tuple(
                row.get("deprecated_aliases"), "deprecated_aliases"
            ),
        )
        _validate_metric_shape(metric, table)
        result.append(metric)
    return tuple(result)


def _load_producer_boundary(value: Any, *, table_id: str) -> ProducerBoundary:
    row = _mapping(value, "producer")
    _require_exact_keys(row, {"evidence_origin", "metric_builder"}, "producer")
    experiment = table_id.split("_", 1)[0]
    result = ProducerBoundary(
        evidence_origin=_non_empty_string(
            row.get("evidence_origin"), "producer.evidence_origin"
        ),
        metric_builder=_non_empty_string(
            row.get("metric_builder"), "producer.metric_builder"
        ),
    )
    expected = ProducerBoundary(
        evidence_origin="canonical_runtime_persisted_evidence",
        metric_builder=f"paper_{experiment}_metrics",
    )
    if result != expected:
        raise ValueError(f"producer boundary drift: {table_id}")
    return result


def _load_consumer_flow(value: Any, *, table_id: str) -> ConsumerFlow:
    row = _mapping(value, "consumer")
    _require_exact_keys(row, {"stages", "renderer_table"}, "consumer")
    experiment = table_id.split("_", 1)[0]
    result = ConsumerFlow(
        stages=_string_tuple(row.get("stages"), "consumer.stages"),
        renderer_table=_non_empty_string(
            row.get("renderer_table"), "consumer.renderer_table"
        ),
    )
    expected = ConsumerFlow(
        stages=(
            f"paper_{experiment}_metrics",
            "paper_metric_registry",
            "paper_metric_observations",
        ),
        renderer_table=table_id,
    )
    if result != expected:
        raise ValueError(f"consumer flow drift: {table_id}")
    return result


def _validate_metric_shape(
    metric: MetricDefinition, table: MetricTableDefinition
) -> None:
    if metric.evidence_classes != table.evidence_classes:
        raise ValueError(
            f"metric evidence class differs from table: {table.table_id}.{metric.metric_id}"
        )
    if metric.evidence_classes == ("online_real_provider",):
        expected_current_roles = (
            ()
            if metric.source_membership_id == "exp5_planned_ai_unit"
            else _ONLINE_REAL_PROVIDER_ROLES
        )
        if (
            metric.required_current_provider_roles != expected_current_roles
            or metric.required_source_bank_roles
        ):
            raise ValueError(
                f"online provider role matrix drift: {table.table_id}.{metric.metric_id}"
            )
    elif metric.evidence_classes == ("real_model_trace_protocol_run",):
        if (
            metric.required_current_provider_roles
            or metric.required_source_bank_roles != _REAL_MODEL_TRACE_SOURCE_ROLES
        ):
            raise ValueError(
                f"trace source role matrix drift: {table.table_id}.{metric.metric_id}"
            )
    if metric.row_scope != table.row_scope:
        raise ValueError(
            f"metric row scope differs from table: {table.table_id}.{metric.metric_id}"
        )
    if metric.row_kind not in table.row_kinds:
        raise ValueError(
            f"metric row kind incompatible with table: {table.table_id}.{metric.metric_id}"
        )
    if metric.claim_role not in CLAIM_ROLES:
        raise ValueError(f"unsupported claim role: {metric.claim_role}")
    if metric.numerator != metric.formula.operands[0]:
        raise ValueError(f"formula numerator drift: {table.table_id}.{metric.metric_id}")
    expected_denominator = (
        metric.formula.operands[1] if len(metric.formula.operands) == 2 else None
    )
    if metric.denominator != expected_denominator:
        raise ValueError(
            f"formula denominator drift: {table.table_id}.{metric.metric_id}"
        )
    triggers = {rule.trigger for rule in metric.null_rules}
    if not {"missing_required_evidence", "infra_invalid"} <= triggers:
        raise ValueError(
            f"global null rules missing: {table.table_id}.{metric.metric_id}"
        )
    zero_denominator_ops = {
        "ratio",
        "median_pairwise_ratio",
        "parallel_efficiency",
        "relative_range",
        "worker_utilization",
    }
    if metric.formula.op in zero_denominator_ops:
        if "zero_denominator" not in triggers:
            raise ValueError(
                f"zero-denominator rule missing: {table.table_id}.{metric.metric_id}"
            )
    elif "zero_denominator" in triggers:
        raise ValueError(
            f"zero-denominator rule forbidden for {metric.formula.op}: "
            f"{table.table_id}.{metric.metric_id}"
        )
    if metric.value_domain not in VALUE_DOMAINS:
        raise ValueError(f"unsupported value domain: {table.table_id}.{metric.metric_id}")
    if not metric.row_gate_membership_id:
        raise ValueError(f"missing row gate membership: {table.table_id}.{metric.metric_id}")
    if len(set(metric.required_invariant_ids)) != len(metric.required_invariant_ids):
        raise ValueError(f"duplicate required invariant: {table.table_id}.{metric.metric_id}")
    if metric.applicability.kind == "field_in":
        rules = tuple(
            rule for rule in metric.null_rules if rule.trigger == "not_applicable"
        )
        if len(rules) != 1 or rules[0] != NullRule(
            trigger="not_applicable",
            action="null_with_explicit_reason",
            reason=metric.applicability.not_applicable_reason or "",
        ):
            raise ValueError(
                f"applicability null rule drift: {table.table_id}.{metric.metric_id}"
            )

    metric_key = (metric.table, metric.metric_id)
    if (
        metric_key[0] == "exp3_trace_robustness"
        and metric_key[1] in _EXP3_FALSE_POSITIVE_METRIC_IDS
    ):
        expected_applicability = ApplicabilitySpec(
            kind="field_in",
            field="fault_type",
            values=("false_positive",),
            not_applicable_reason="not_applicable_non_false_positive",
        )
    elif (
        metric_key[0] == "exp3_trace_robustness"
        and metric_key[1] in _EXP3_WORKER_DEATH_METRIC_IDS
    ):
        expected_applicability = ApplicabilitySpec(
            kind="field_in",
            field="fault_type",
            values=("worker_death",),
            not_applicable_reason="not_applicable_rate_fault",
        )
    else:
        expected_applicability = ApplicabilitySpec(
            kind="always",
            field=None,
            values=(),
            not_applicable_reason=None,
        )
    if metric.applicability != expected_applicability:
        raise ValueError(
            f"applicability metric drift: {table.table_id}.{metric.metric_id}"
        )

    if metric.row_kind == "paired_worker_comparison":
        expected_pair_key = _MAIN_PAIRED_WORKER_KEY
    elif (
        metric.table == "exp3_trace_robustness"
        and metric.source_membership_id == "exp3_trace_pair_complete"
        and metric.formula.op == "subtract"
    ):
        expected_pair_key = _EXP3_TRACE_PAIR_KEY
    elif metric.row_kind == "paired_transition":
        expected_pair_key = _EXP4_ABLATION_PAIR_KEY
    else:
        expected_pair_key = ()
    if metric.pair_key != expected_pair_key:
        raise ValueError(f"pair key drift: {table.table_id}.{metric.metric_id}")


def _validate_metric_inventory(
    tables: tuple[MetricTableDefinition, ...],
    metrics: tuple[MetricDefinition, ...],
    retired_metric_ids: tuple[str, ...],
) -> None:
    keys = tuple((metric.table, metric.metric_id) for metric in metrics)
    if len(set(keys)) != len(keys):
        raise ValueError("paper metric definition keys must be unique")
    retired = set(retired_metric_ids)
    aliases: dict[str, tuple[str, str]] = {}
    for metric in metrics:
        if metric.metric_id in retired:
            raise ValueError(f"retired metric is live: {metric.metric_id}")
        for alias in metric.deprecated_aliases:
            if alias in {"raw_only_count", "premature_merge_count"}:
                raise ValueError(f"retired metric alias cannot be mapped: {alias}")
            if alias not in retired:
                raise ValueError(f"unknown deprecated alias: {metric.table}.{alias}")
            if alias in aliases:
                raise ValueError(f"deprecated alias has multiple replacements: {alias}")
            aliases[alias] = (metric.table, metric.metric_id)
    for table in tables:
        definitions = tuple(
            metric.metric_id for metric in metrics if metric.table == table.table_id
        )
        for field in table.numeric_output_fields:
            if field not in definitions:
                raise ValueError(f"uncontracted numeric output field: {field}")
        for field in definitions:
            if field not in table.numeric_output_fields:
                raise ValueError(f"undeclared metric definition: {table.table_id}.{field}")
        if definitions != table.numeric_output_fields:
            raise ValueError(f"paper metric definition order drift: {table.table_id}")


def _validate_executable_metric_graph(
    metrics: tuple[MetricDefinition, ...],
    memberships: tuple[MembershipSpec, ...],
) -> None:
    metric_by_key = {(metric.table, metric.metric_id): metric for metric in metrics}
    references = {
        membership_id
        for metric in metrics
        for membership_id in (
            metric.source_membership_id,
            metric.row_gate_membership_id,
        )
        if membership_id is not None
    }
    declared = {membership.membership_id for membership in memberships}
    if references != declared:
        unused = sorted(declared - references)
        missing = sorted(references - declared)
        raise ValueError(
            f"membership reachability drift: unused={unused}, missing={missing}"
        )

    row_primitives = {
        "runtime_elapsed_ms": "number",
        "witness_in_flight_count": "nonnegative_integer",
        "scheduler_peak_active_count": "nonnegative_integer",
        "configured_worker_count": "nonnegative_integer",
    }
    dependency_graph: dict[tuple[str, str], set[tuple[str, str]]] = {
        key: set() for key in metric_by_key
    }
    for key, metric in metric_by_key.items():
        if metric.formula.op == "count" and metric.formula.operands != (
            OperandRef(kind="member_set", id="members"),
        ):
            raise ValueError(f"unexecutable count formula: {metric.table}.{metric.metric_id}")
        for operand in metric.formula.operands:
            if operand.kind == "number_sequence" and not operand.id.startswith(
                "members."
            ):
                raise ValueError(
                    f"unexecutable sequence operand: {metric.table}.{metric.metric_id}"
                )
            if operand.id.startswith("members."):
                leaf = operand.id.removeprefix("members.")
                if not leaf or leaf == "paired_speedup":
                    raise ValueError(
                        f"derived member operand forbidden: {metric.table}.{metric.metric_id}"
                    )
            elif operand.id.startswith("row."):
                leaf = operand.id.removeprefix("row.")
                if row_primitives.get(leaf) != operand.kind:
                    raise ValueError(
                        f"undeclared row primitive: {metric.table}.{metric.metric_id}.{leaf}"
                    )
                if leaf == metric.metric_id or leaf.endswith("_numerator_membership"):
                    raise ValueError(
                        f"derived row operand forbidden: {metric.table}.{metric.metric_id}"
                    )
            elif operand.id.startswith("observation."):
                parts = operand.id.split(".", 2)
                if len(parts) != 3:
                    raise ValueError(
                        f"invalid observation dependency: {metric.table}.{metric.metric_id}"
                    )
                dependency_key = (parts[1], parts[2])
                dependency = metric_by_key.get(dependency_key)
                if dependency is None:
                    raise ValueError(
                        f"unknown observation dependency: {metric.table}.{metric.metric_id}"
                    )
                if dependency.table != metric.table or dependency.row_kind != metric.row_kind:
                    raise ValueError(
                        f"cross-row observation dependency: {metric.table}.{metric.metric_id}"
                    )
                dependency_graph[key].add(dependency_key)
            elif not (operand.kind == "member_set" and operand.id == "members"):
                raise ValueError(
                    f"undeclared operand source: {metric.table}.{metric.metric_id}"
                )

    visiting: set[tuple[str, str]] = set()
    visited: set[tuple[str, str]] = set()

    def visit(key: tuple[str, str]) -> None:
        if key in visiting:
            raise ValueError(f"metric dependency cycle: {key[0]}.{key[1]}")
        if key in visited:
            return
        visiting.add(key)
        for dependency in dependency_graph[key]:
            visit(dependency)
        visiting.remove(key)
        visited.add(key)

    for key in dependency_graph:
        visit(key)


def _validate_invariant_inventory(invariants: tuple[InvariantSpec, ...]) -> None:
    required = {
        "exp1_failure_class_partition",
        "exp2_pair_membership_partition",
        "exp4_transition_partition",
        "exp5_first_nonpass_reason_partition",
    }
    if {item.invariant_id for item in invariants} != required:
        raise ValueError("paper metric invariant inventory drift")


def _reject_duplicate_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return value


def _array(value: Any, field_name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be an array")
    return value


def _require_exact_keys(
    value: Mapping[str, Any], expected: set[str], field_name: str
) -> None:
    if set(value) != expected:
        raise ValueError(f"{field_name} schema drift")


def _non_empty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _nullable_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return _non_empty_string(value, field_name)


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be an array of strings")
    result = tuple(_non_empty_string(item, field_name) for item in value)
    if len(set(result)) != len(result):
        raise ValueError(f"{field_name} must not contain duplicates")
    return result


def _integer(value: Mapping[str, Any], field_name: str) -> int:
    item = value.get(field_name)
    if isinstance(item, bool) or not isinstance(item, int):
        raise ValueError(f"{field_name} must be an integer")
    return item


def _boolean(value: Mapping[str, Any], field_name: str) -> bool:
    item = value.get(field_name)
    if not isinstance(item, bool):
        raise ValueError(f"{field_name} must be a bool")
    return item


def _decimal_string(value: Any, field_name: str) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a decimal string")
    try:
        result = Decimal(value)
    except Exception as exc:
        raise ValueError(f"{field_name} must be a decimal string") from exc
    if not result.is_finite():
        raise ValueError(f"{field_name} must be a finite decimal string")
    return result


def _digest(value: Any, field_name: str) -> str:
    result = _non_empty_string(value, field_name)
    if (
        len(result) != 71
        or not result.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in result[7:])
    ):
        raise ValueError(f"{field_name} must be a complete digest")
    return result


def _json_scalar(value: Any, field_name: str) -> str | int | bool | None:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise ValueError(f"{field_name} must be a JSON scalar")


def _coerce_number(value: Any, field_name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise ValueError(f"number required: {field_name}")
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError(f"finite number required: {field_name}")
    return result


def recompute_metric(
    contract: PaperMetricContract,
    table_id: str,
    metric_id: str,
    *,
    row_kind: str,
    bundle: MetricObservationBundle,
) -> MetricEvaluation:
    """重算一个合同指标，并在显式 scope 内捕获原始输入和返回值。"""

    evaluation = _recompute_metric_uncaptured(
        contract,
        table_id,
        metric_id,
        row_kind=row_kind,
        bundle=bundle,
    )
    collector = _METRIC_TRACE_CAPTURE.get()
    if collector is not None:
        metric = contract.require_metric(table_id, metric_id)
        identity = {
            "schema_version": "tokenshare.metric_computation_trace_identity.v1",
            "contract_digest": contract.contract_digest,
            "pipeline_profile_digest": contract.pipeline_profile_digest,
            "table_id": table_id,
            "metric_id": metric_id,
            "formula_id": metric.formula_id,
            "row_kind": row_kind,
            "row_identity_digest": bundle.row_identity_digest,
        }
        encoded = json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        cell_identity_digest = "sha256:" + hashlib.sha256(encoded).hexdigest()
        evaluation = replace(
            evaluation,
            member_decisions=_IdentityBoundMemberDecisions(
                evaluation.member_decisions,
                row_identity_digest=bundle.row_identity_digest,
                cell_identity_digest=cell_identity_digest,
            ),
        )
        collector.append(
            MetricComputationTrace(
                trace_id=cell_identity_digest,
                contract_id=contract.contract_id,
                contract_digest=contract.contract_digest,
                pipeline_profile_id=contract.pipeline_profile_id,
                pipeline_profile_digest=contract.pipeline_profile_digest,
                table_id=table_id,
                metric_id=metric_id,
                formula_id=metric.formula_id,
                row_kind=row_kind,
                row_identity_digest=bundle.row_identity_digest,
                cell_identity_digest=cell_identity_digest,
                bundle=bundle,
                evaluation=evaluation,
            )
        )
    return evaluation


def _recompute_metric_uncaptured(
    contract: PaperMetricContract,
    table_id: str,
    metric_id: str,
    *,
    row_kind: str,
    bundle: MetricObservationBundle,
) -> MetricEvaluation:
    """只从一个不可变 observation bundle 重算一个合同指标。"""
    if not isinstance(bundle, MetricObservationBundle):
        raise ValueError("recompute_metric requires one MetricObservationBundle")
    metric = contract.require_metric(table_id, metric_id)
    if row_kind != metric.row_kind:
        raise ValueError(
            f"metric row kind mismatch: {table_id}.{metric_id} expects "
            f"{metric.row_kind}, got {row_kind}"
        )
    if bundle.is_empty:
        return _apply_null_rule(
            metric, "missing_required_evidence", "empty_observation_bundle"
        )

    audit_denominator = tuple(
        member_id
        for member_id in bundle.member_ids
        if bundle.member_facts_by_id[member_id].get("member_kind")
        in {"preregistered_root", "exp5_preregistered_root"}
    )
    row_gate = evaluate_membership(
        contract, metric.row_gate_membership_id, bundle.row_facts
    )
    if row_gate.status is MembershipStatus.BLOCKED_MISSING_EVIDENCE:
        trigger = (
            "incomplete_pair"
            if metric.row_gate_membership_id == "exp2_paired_worker_row"
            else "missing_required_evidence"
        )
        return _apply_null_rule(
            metric,
            trigger,
            "incomplete_pair" if trigger == "incomplete_pair" else "missing_required_row_evidence",
            audit_denominator_member_ids=audit_denominator,
        )
    if row_gate.status is MembershipStatus.EXCLUDED:
        reason = row_gate.exclusion_reason or "not_applicable"
        if reason == "wrong_ablation_mode":
            reason = "not_applicable_wrong_mode"
        trigger = (
            "infra_invalid"
            if reason == "infra_invalid"
            else "incomplete_pair"
            if reason == "incomplete_pair"
            else "missing_required_evidence"
            if reason in {"exp5_retry_forbidden", "replacement_attempts_forbidden"}
            else "not_applicable"
        )
        return _apply_null_rule(
            metric,
            trigger,
            reason,
            exclusion_reason=reason,
            audit_denominator_member_ids=audit_denominator,
        )

    if metric.applicability.kind == "field_in":
        field = metric.applicability.field
        assert field is not None
        if field not in bundle.row_facts:
            return _apply_null_rule(
                metric,
                "missing_required_evidence",
                f"missing_applicability_evidence:{field}",
                audit_denominator_member_ids=audit_denominator,
            )
        applicability_value = bundle.row_facts[field]
        if not isinstance(applicability_value, str) or not applicability_value:
            return _apply_null_rule(
                metric,
                "missing_required_evidence",
                f"invalid_applicability_evidence:{field}",
                audit_denominator_member_ids=audit_denominator,
            )
        if field == "fault_type" and applicability_value not in {
            "false_positive",
            "false_negative",
            "no_return",
            "late_submission",
            "executor_error",
            "worker_death",
        }:
            return _apply_null_rule(
                metric,
                "missing_required_evidence",
                f"invalid_applicability_evidence:{field}",
                audit_denominator_member_ids=audit_denominator,
            )
        if applicability_value not in metric.applicability.values:
            return _apply_null_rule(
                metric,
                "not_applicable",
                metric.applicability.not_applicable_reason,
                audit_denominator_member_ids=audit_denominator,
            )

    included: list[str] = []
    excluded: list[str] = []
    blocked_members: list[str] = []
    decisions: list[MemberDecision] = []
    if metric.source_membership_id is not None:
        for member_id in bundle.member_ids:
            membership = evaluate_membership(
                contract,
                metric.source_membership_id,
                bundle.member_facts_by_id[member_id],
            )
            if membership.status is MembershipStatus.INCLUDED:
                included.append(member_id)
            elif membership.status is MembershipStatus.EXCLUDED:
                excluded.append(member_id)
            else:
                blocked_members.append(member_id)
            decisions.append(
                MemberDecision(
                    member_id=member_id,
                    status=membership.status,
                    reason=membership.exclusion_reason,
                )
            )
        if blocked_members:
            pair_selector = metric.source_membership_id in {
                "exp2_speedup_eligible_pair",
                "exp2_speedup_ineligible_pair",
                "exp2_resource_multiplier_eligible_pair",
            }
            return _apply_null_rule(
                metric,
                "incomplete_pair" if pair_selector else "missing_required_evidence",
                "incomplete_pair" if pair_selector else "missing_required_member_evidence",
                included_member_ids=tuple(included),
                excluded_member_ids=tuple(excluded),
                blocked_member_ids=tuple(blocked_members),
                audit_denominator_member_ids=audit_denominator,
                member_decisions=tuple(decisions),
            )
    else:
        decisions.extend(
            MemberDecision(
                member_id=member_id,
                status=MembershipStatus.EXCLUDED,
                reason="metric_has_no_source_membership",
            )
            for member_id in bundle.member_ids
        )

    role_failure = _validate_required_roles(metric, bundle, tuple(included))
    if role_failure is not None:
        return _apply_null_rule(
            metric,
            "missing_required_evidence",
            role_failure,
            included_member_ids=tuple(included),
            excluded_member_ids=tuple(excluded),
            audit_denominator_member_ids=audit_denominator,
            member_decisions=tuple(decisions),
        )

    try:
        operands = tuple(
            _resolve_bundle_operand(ref, contract, metric, bundle, tuple(included))
            for ref in metric.formula.operands
        )
        value = _execute_formula(metric, operands)
        value = _validate_metric_value_domain(metric, value, operands)
    except (_MissingEvidence, ValueError) as exc:
        detail = exc.reason if isinstance(exc, _MissingEvidence) else str(exc)
        if (
            metric.formula.op == "median_pairwise_ratio"
            and detail == "insufficient_values"
        ):
            detail = "incomplete_pair"
        trigger = (
            detail
            if detail in {"zero_denominator", "incomplete_pair"}
            else "missing_required_evidence"
        )
        return _apply_null_rule(
            metric,
            trigger,
            detail,
            included_member_ids=tuple(included),
            excluded_member_ids=tuple(excluded),
            audit_denominator_member_ids=audit_denominator,
            member_decisions=tuple(decisions),
        )

    for invariant_id in metric.required_invariant_ids:
        invariant = evaluate_invariant(contract, invariant_id, bundle)
        if not invariant.satisfied:
            return _apply_null_rule(
                metric,
                "missing_required_evidence",
                invariant.violation_reason or "required_invariant_failed",
                included_member_ids=tuple(included),
                excluded_member_ids=tuple(excluded),
                audit_denominator_member_ids=audit_denominator,
                member_decisions=tuple(decisions),
            )
    return MetricEvaluation(
        value=value,
        included_member_ids=tuple(included),
        excluded_member_ids=tuple(excluded),
        audit_denominator_member_ids=audit_denominator,
        member_decisions=tuple(decisions),
        paper_eligible=False,
    )


def evaluate_membership(
    contract: PaperMetricContract,
    membership_id: str,
    facts: Mapping[str, Any],
) -> MembershipEvaluation:
    if not isinstance(facts, Mapping):
        raise ValueError("membership facts must be a mapping")
    membership = contract.require_membership(membership_id)
    result = _evaluate_predicate(membership.predicate, facts)
    if result.status is MembershipStatus.INCLUDED and membership_id == (
        "exp3_online_wasted_actual_tokens"
    ):
        ordered = (
            facts.get("fault_or_death_at_ms"),
            facts.get("replacement_attempt_created_at_ms"),
            facts.get("replacement_provider_dispatch_at_ms"),
        )
        if any(item is None for item in ordered):
            return MembershipEvaluation(
                status=MembershipStatus.BLOCKED_MISSING_EVIDENCE,
                predicate_truth=PredicateTruth.MISSING,
                exclusion_reason="missing_recovery_timeline",
            )
        try:
            numbers = tuple(_coerce_number(item, "recovery timeline") for item in ordered)
        except ValueError:
            return MembershipEvaluation(
                status=MembershipStatus.EXCLUDED,
                predicate_truth=PredicateTruth.FALSE,
                exclusion_reason="invalid_recovery_timeline",
            )
        if not numbers[0] < numbers[1] < numbers[2]:
            return MembershipEvaluation(
                status=MembershipStatus.EXCLUDED,
                predicate_truth=PredicateTruth.FALSE,
                exclusion_reason="recovery_attempt_not_after_fault",
            )
    return result


def evaluate_invariant(
    contract: PaperMetricContract,
    invariant_id: str,
    bundle: MetricObservationBundle,
) -> InvariantEvaluation:
    if not isinstance(bundle, MetricObservationBundle):
        raise ValueError("evaluate_invariant requires one MetricObservationBundle")
    invariant = contract.require_invariant(invariant_id)
    values: list[int] = []
    for ref in (*invariant.operands, invariant.target):
        if ref.kind != "verified_count" or "." not in ref.id:
            return InvariantEvaluation(
                satisfied=False,
                violation_reason="invalid_invariant_observation_ref",
                publish_blocked=True,
            )
        table_id, metric_id = ref.id.split(".", 1)
        metric = contract.require_metric(table_id, metric_id)
        if (
            metric.formula.op != "count"
            or metric.value_domain != "count_nonnegative_integer"
            or metric.source_membership_id is None
        ):
            return InvariantEvaluation(
                satisfied=False,
                violation_reason="invalid_invariant_count_metric",
                publish_blocked=True,
            )
        included = 0
        for member_id in bundle.member_ids:
            membership = evaluate_membership(
                contract,
                metric.source_membership_id,
                bundle.member_facts_by_id[member_id],
            )
            if membership.status is MembershipStatus.BLOCKED_MISSING_EVIDENCE:
                return InvariantEvaluation(
                    satisfied=False,
                    violation_reason="missing_required_member_evidence",
                    publish_blocked=True,
                )
            if membership.status is MembershipStatus.INCLUDED:
                included += 1
        values.append(included)
    satisfied = sum(values[:-1]) == values[-1]
    return InvariantEvaluation(
        satisfied=satisfied,
        violation_reason=None if satisfied else invariant.violation_reason,
        publish_blocked=not satisfied,
    )


def _evaluate_predicate(
    predicate: PredicateSpec, facts: Mapping[str, Any]
) -> MembershipEvaluation:
    op = predicate.op
    if op == "always":
        return _membership_result(PredicateTruth.TRUE, None)
    if op in {"all", "any"}:
        results = tuple(_evaluate_predicate(item, facts) for item in predicate.args)
        truths = tuple(item.predicate_truth for item in results)
        if op == "all":
            if PredicateTruth.FALSE in truths:
                first = next(item for item in results if item.predicate_truth is PredicateTruth.FALSE)
                return _membership_result(
                    PredicateTruth.FALSE,
                    first.exclusion_reason or predicate.failure_reason,
                )
            if PredicateTruth.MISSING in truths:
                first = next(item for item in results if item.predicate_truth is PredicateTruth.MISSING)
                return _membership_result(
                    PredicateTruth.MISSING,
                    first.exclusion_reason or predicate.failure_reason,
                )
            return _membership_result(PredicateTruth.TRUE, None)
        if PredicateTruth.TRUE in truths:
            return _membership_result(PredicateTruth.TRUE, None)
        if PredicateTruth.MISSING in truths:
            first = next(item for item in results if item.predicate_truth is PredicateTruth.MISSING)
            return _membership_result(
                PredicateTruth.MISSING,
                first.exclusion_reason or predicate.failure_reason,
            )
        reason = predicate.failure_reason or next(
            (item.exclusion_reason for item in results if item.exclusion_reason), None
        )
        return _membership_result(PredicateTruth.FALSE, reason)
    if op == "not":
        child = _evaluate_predicate(predicate.args[0], facts)
        if child.predicate_truth is PredicateTruth.MISSING:
            return _membership_result(PredicateTruth.MISSING, child.exclusion_reason)
        if child.predicate_truth is PredicateTruth.TRUE:
            return _membership_result(PredicateTruth.FALSE, predicate.failure_reason)
        return _membership_result(PredicateTruth.TRUE, None)

    field = predicate.field
    assert field is not None
    if field not in facts:
        return _membership_result(
            PredicateTruth.MISSING,
            predicate.failure_reason or f"missing_{field}",
        )
    value = facts[field]
    matched: bool
    if op == "field_true":
        matched = value is True
    elif op == "field_false":
        matched = value is False
    elif op == "field_equals":
        matched = type(value) is type(predicate.value) and value == predicate.value
    elif op == "field_in":
        matched = any(type(value) is type(item) and value == item for item in predicate.values)
    elif op == "field_nonempty_string":
        matched = isinstance(value, str) and bool(value)
    elif op in {"fields_equal", "fields_not_equal", "integer_successor"}:
        other = predicate.other_field
        assert other is not None
        if other not in facts:
            return _membership_result(
                PredicateTruth.MISSING,
                predicate.failure_reason or f"missing_{other}",
            )
        other_value = facts[other]
        if op == "fields_equal":
            matched = type(value) is type(other_value) and value == other_value
        elif op == "fields_not_equal":
            matched = type(value) is type(other_value) and value != other_value
        else:
            matched = (
                isinstance(value, int) and not isinstance(value, bool)
                and isinstance(other_value, int) and not isinstance(other_value, bool)
                and value == other_value + 1
            )
    elif op == "number_positive":
        try:
            matched = _coerce_number(value, field) > 0
        except ValueError:
            matched = False
    elif op == "ordered_roles_exact":
        matched = isinstance(value, (list, tuple)) and tuple(value) == predicate.values
    else:
        raise ValueError(f"unsupported predicate op: {op}")
    return _membership_result(
        PredicateTruth.TRUE if matched else PredicateTruth.FALSE,
        None if matched else predicate.failure_reason,
    )


def _membership_result(
    truth: PredicateTruth, reason: str | None
) -> MembershipEvaluation:
    status = {
        PredicateTruth.TRUE: MembershipStatus.INCLUDED,
        PredicateTruth.FALSE: MembershipStatus.EXCLUDED,
        PredicateTruth.MISSING: MembershipStatus.BLOCKED_MISSING_EVIDENCE,
    }[truth]
    return MembershipEvaluation(
        status=status, predicate_truth=truth, exclusion_reason=reason
    )


class _MissingEvidence(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _resolve_bundle_operand(
    ref: OperandRef,
    contract: PaperMetricContract,
    metric: MetricDefinition,
    bundle: MetricObservationBundle,
    included_member_ids: tuple[str, ...],
) -> Any:
    if ref.kind == "member_set" and ref.id == "members":
        return included_member_ids
    if ref.id.startswith("members."):
        field = ref.id[len("members."):]
        values: list[Decimal] = []
        for member_id in included_member_ids:
            facts = bundle.member_facts_by_id[member_id]
            if field not in facts:
                raise _MissingEvidence(f"missing_member_field:{member_id}:{field}")
            try:
                values.append(_coerce_number(facts[field], field))
            except ValueError as exc:
                raise _MissingEvidence(f"invalid_member_field:{member_id}:{field}") from exc
        return tuple(values)
    if ref.id.startswith("row."):
        field = ref.id[len("row."):]
        if field not in bundle.row_facts:
            raise _MissingEvidence(f"missing_row_field:{field}")
        if ref.kind == "nonnegative_integer":
            try:
                return _coerce_nonnegative_integer(bundle.row_facts[field], field)
            except ValueError as exc:
                raise _MissingEvidence(f"invalid_row_field:{field}") from exc
        try:
            return _coerce_number(bundle.row_facts[field], field)
        except ValueError as exc:
            raise _MissingEvidence(f"invalid_row_field:{field}") from exc
    if ref.id.startswith("observation."):
        remainder = ref.id[len("observation."):]
        if "." not in remainder:
            raise _MissingEvidence("invalid_observation_path")
        table_id, metric_id = remainder.split(".", 1)
        dependency = contract.require_metric(table_id, metric_id)
        observation = bundle.observation(table_id, metric_id)
        if (
            observation is None
            or not observation.evidence_verified
            or observation.recompute_only is not True
            or observation.paper_eligible is not False
            or observation.row_identity_digest != bundle.row_identity_digest
            or observation.row_kind != dependency.row_kind
            or observation.value_domain != dependency.value_domain
            or not set(observation.source_member_ids) <= set(bundle.member_ids)
        ):
            raise _MissingEvidence(f"missing_verified_observation:{table_id}.{metric_id}")
        return _coerce_number(observation.value, observation.metric_id)
    raise _MissingEvidence(f"undeclared_operand_path:{ref.id}")


def _execute_formula(metric: MetricDefinition, operands: tuple[Any, ...]) -> Decimal | int:
    op = metric.formula.op
    family = _FORMULA_FAMILIES[metric.formula_id]
    if family.exact_sequence_length is not None:
        sequence = operands[0]
        if not isinstance(sequence, tuple) or len(sequence) != family.exact_sequence_length:
            raise _MissingEvidence("insufficient_values")
    if family.minimum_sequence_length is not None:
        sequence = operands[0]
        if not isinstance(sequence, tuple) or len(sequence) < family.minimum_sequence_length:
            raise _MissingEvidence("insufficient_values")
    if op == "count":
        return len(operands[0])
    if op == "direct":
        return operands[0]
    if op == "sum":
        return sum(operands[0], Decimal(0))
    if op == "ratio":
        numerator, denominator = operands
        if isinstance(denominator, tuple) and not isinstance(numerator, tuple):
            denominator = len(denominator)
        elif isinstance(numerator, tuple) and isinstance(denominator, tuple):
            if len(numerator) != 1 or len(denominator) != 1:
                raise _MissingEvidence("pair_cardinality_not_one")
            numerator, denominator = numerator[0], denominator[0]
        if denominator == 0:
            raise _MissingEvidence("zero_denominator")
        return numerator / denominator
    if op == "subtract":
        left, right = operands
        if isinstance(left, tuple) and isinstance(right, tuple):
            if len(left) != 1 or len(right) != 1:
                raise _MissingEvidence("pair_cardinality_not_one")
            left, right = left[0], right[0]
        return left - right
    if op == "elapsed":
        terminal_values, start_values = operands
        if not terminal_values or not start_values:
            raise _MissingEvidence("missing_required_time_evidence")
        value = max(terminal_values) - min(start_values)
        if value < 0:
            raise _MissingEvidence("invalid_negative_elapsed")
        return value
    if op == "percentage_point_delta":
        actual, target = operands
        if not isinstance(actual, tuple) or not isinstance(target, tuple):
            raise _MissingEvidence("invalid_paired_percentage_point_inputs")
        if len(actual) != 1 or len(target) != 1:
            raise _MissingEvidence("pair_cardinality_not_one")
        return Decimal(100) * (actual[0] - target[0])
    if op == "median_pairwise_ratio":
        numerators, denominators = operands
        if len(numerators) != len(denominators) or not numerators:
            raise _MissingEvidence("incomplete_pair")
        if any(value == 0 for value in denominators):
            raise _MissingEvidence("zero_denominator")
        ratios = tuple(
            numerator / denominator
            for numerator, denominator in zip(numerators, denominators, strict=True)
        )
        ordered = tuple(sorted(ratios))
        middle = len(ordered) // 2
        return ordered[middle] if len(ordered) % 2 else (
            ordered[middle - 1] + ordered[middle]
        ) / Decimal(2)
    if op == "parallel_efficiency":
        baseline, compared, workers = operands
        if not (len(baseline) == len(compared) == len(workers) == 1):
            raise _MissingEvidence("pair_cardinality_not_one")
        if compared[0] == 0 or workers[0] == 0:
            raise _MissingEvidence("zero_denominator")
        return baseline[0] / compared[0] / workers[0]
    if op == "relative_range":
        sequence = tuple(sorted(operands[0]))
        if not sequence:
            raise _MissingEvidence("insufficient_values")
        if sequence[0] == 0:
            raise _MissingEvidence("zero_denominator")
        return (sequence[-1] - sequence[0]) / sequence[0]
    if op == "worker_utilization":
        busy_times, configured_workers, elapsed = operands
        denominator = configured_workers * elapsed
        if denominator == 0:
            raise _MissingEvidence("zero_denominator")
        return sum(busy_times, Decimal(0)) / denominator
    if op in {
        "signed_mean_percentage_point_delta",
        "signed_max_percentage_point_delta",
    }:
        actual, target = operands
        if len(actual) != len(target) or not actual:
            raise _MissingEvidence("incomplete_pair")
        deltas = tuple(
            Decimal(100) * (left - right)
            for left, right in zip(actual, target, strict=True)
        )
        if op == "signed_max_percentage_point_delta":
            return max(deltas)
        return sum(deltas, Decimal(0)) / Decimal(len(deltas))
    if op in {"median", "minimum", "maximum", "range", "signed_mean", "signed_max"}:
        sequence = operands[0] if len(operands) == 1 and isinstance(operands[0], tuple) else operands
        if not sequence:
            raise _MissingEvidence("insufficient_values")
        ordered = tuple(sorted(sequence))
        if op == "median":
            middle = len(ordered) // 2
            return ordered[middle] if len(ordered) % 2 else (
                ordered[middle - 1] + ordered[middle]
            ) / Decimal(2)
        if op == "minimum":
            return ordered[0]
        if op in {"maximum", "signed_max"}:
            return ordered[-1]
        if op == "range":
            return ordered[-1] - ordered[0]
        return sum(sequence, Decimal(0)) / Decimal(len(sequence))
    raise _MissingEvidence(f"unsupported_formula_operation:{op}")


def _validate_metric_value_domain(
    metric: MetricDefinition,
    value: Decimal | int,
    operands: tuple[Any, ...],
) -> Decimal | int:
    if metric.value_domain == "count_nonnegative_integer":
        return _coerce_nonnegative_integer(value, metric.metric_id)
    number = _coerce_number(value, metric.metric_id)
    if metric.value_domain == "proportion":
        if number < 0:
            raise _MissingEvidence("negative_proportion_numerator")
        if number > 1:
            raise _MissingEvidence("proportion_numerator_exceeds_denominator")
    elif metric.value_domain == "ratio_unbounded":
        # `_execute_formula` 已按 scalar/member-set/paired-sequence 三种 ratio
        # 形态检查分母；此处只验证最终值域，不能把 sequence 当作标量比较。
        if number < 0:
            raise _MissingEvidence("negative_unbounded_ratio")
    elif metric.value_domain == "nonnegative_number" and number < 0:
        raise _MissingEvidence("negative_nonnegative_metric")
    return number


def _validate_required_roles(
    metric: MetricDefinition,
    bundle: MetricObservationBundle,
    included_member_ids: tuple[str, ...],
) -> str | None:
    online_member_kinds = {
        "actual_provider_attempt", "actual_first_provider_attempt",
        "exp2_online_first_provider_attempt",
        "exp5_provider_attempt", "online_recovery_original_attempt",
        "online_recovery_replacement_attempt",
    }
    trace_member_kinds = {
        "committed_trace_consumption",
        "trace_consumption",
        "exp3_discarded_trace_consumption",
    }
    if metric.required_current_provider_roles:
        for member_id in included_member_ids:
            facts = bundle.member_facts_by_id[member_id]
            if facts.get("member_kind") not in online_member_kinds:
                continue
            roles = facts.get("current_provider_roles")
            if roles is None:
                return f"missing_current_provider_roles:{member_id}"
            if not isinstance(roles, (tuple, list)) or tuple(roles) != metric.required_current_provider_roles:
                return f"invalid_current_provider_roles:{member_id}"
    if metric.required_source_bank_roles:
        for member_id in included_member_ids:
            facts = bundle.member_facts_by_id[member_id]
            if facts.get("member_kind") not in trace_member_kinds:
                continue
            roles = facts.get("source_bank_roles")
            if roles is None:
                return f"missing_source_bank_roles:{member_id}"
            if not isinstance(roles, (tuple, list)) or tuple(roles) != metric.required_source_bank_roles:
                return f"invalid_source_bank_roles:{member_id}"
    return None


def _apply_null_rule(
    metric: MetricDefinition,
    trigger: str,
    detail: str | None = None,
    *,
    exclusion_reason: str | None = None,
    included_member_ids: tuple[str, ...] = (),
    excluded_member_ids: tuple[str, ...] = (),
    blocked_member_ids: tuple[str, ...] = (),
    audit_denominator_member_ids: tuple[str, ...] = (),
    member_decisions: tuple[MemberDecision, ...] = (),
) -> MetricEvaluation:
    rules = tuple(rule for rule in metric.null_rules if rule.trigger == trigger)
    if len(rules) != 1:
        if trigger == "incomplete_pair":
            rules = tuple(
                rule
                for rule in metric.null_rules
                if rule.trigger == "missing_required_evidence"
            )
        if len(rules) != 1:
            raise ValueError(
                f"missing null rule {trigger}: {metric.table}.{metric.metric_id}"
            )
    rule = rules[0]
    return MetricEvaluation(
        value=None,
        reason=(
            rule.reason
            if rule.action == "null_with_explicit_reason"
            else detail or rule.reason
        ),
        publish_blocked=rule.action == "null_and_publish_blocked",
        exclusion_reason=exclusion_reason,
        included_member_ids=included_member_ids,
        excluded_member_ids=excluded_member_ids,
        blocked_member_ids=blocked_member_ids,
        audit_denominator_member_ids=audit_denominator_member_ids,
        member_decisions=member_decisions,
        paper_eligible=False,
    )


def _coerce_nonnegative_integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a nonnegative integer")
    return value


def _deep_freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(
        {
            _non_empty_string(key, "fact key"): _deep_freeze(item)
            for key, item in value.items()
        }
    )


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _deep_freeze_mapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, float) and (value != value or value in {float("inf"), float("-inf")}):
        raise ValueError("non-finite bundle fact")
    if value is None or isinstance(value, (str, bool, int, float, Decimal)):
        return value
    raise ValueError("bundle facts must contain immutable JSON-like or Decimal leaves")


__all__ = [
    "ApplicabilitySpec",
    "ConsumerFlow",
    "DEFAULT_PAPER_METRIC_CONTRACT_PATH",
    "FormulaSpec",
    "InvariantEvaluation",
    "InvariantSpec",
    "MembershipEvaluation",
    "MembershipStatus",
    "MembershipSpec",
    "MemberDecision",
    "MetricContractControls",
    "MetricComputationTrace",
    "MetricDefinition",
    "MetricEvaluation",
    "MetricTableDefinition",
    "NullRule",
    "OperandRef",
    "PAPER_METRIC_CONTRACT_DIGEST",
    "PAPER_METRIC_CONTRACT_ID",
    "PAPER_METRIC_CONTRACT_SCHEMA_VERSION",
    "PAPER_METRIC_CONTRACT_VERSION",
    "PaperMetricContract",
    "MetricObservationBundle",
    "PredicateTruth",
    "PredicateSpec",
    "ProducerBoundary",
    "VerifiedMetricObservation",
    "compute_metric_contract_digest",
    "capture_metric_computation_traces",
    "evaluate_invariant",
    "evaluate_membership",
    "load_paper_metric_contract",
    "recompute_metric",
]
