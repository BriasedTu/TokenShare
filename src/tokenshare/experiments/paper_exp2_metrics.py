"""Experiment 2 trace-main 与 online evidence 的纯指标 projector。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping, Sequence

from tokenshare.experiments.paper_direct_results import PaperDirectRootResult
from tokenshare.experiments.paper_metric_contract import (
    MemberDecision,
    MetricEvaluation,
    MetricObservationBundle,
    PaperMetricContract,
    VerifiedMetricObservation,
    recompute_metric,
)
from tokenshare.experiments.paper_models import PaperDirectRootStatus, digest_json


TRACE_TABLE_ID = "exp2_trace_scalability"
ONLINE_TABLE_ID = "exp2_online_concurrency"
EXP2_WORKER_COUNTS = (1, 3, 7, 10, 30, 50)
_POSITION_ORDER = {"early": 0, "middle": 1, "late": 2, "no_factor": 3}
TRACE_SOURCE_BANK_ROLES = (
    "request_body",
    "raw_output_or_provider_failure",
    "provenance",
    "usage_status",
    "latency",
    "pricing",
    "acquisition_attempt",
    "model_record",
)
ONLINE_CURRENT_PROVIDER_ROLES = (
    "request_body",
    "raw_output_or_provider_failure",
    "provenance",
    "usage_status",
    "latency",
    "pricing",
    "provider_attempt",
    "model_record",
)
EXP2_TRACE_NUMERIC_FIELDS = (
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
)
EXP2_ONLINE_NUMERIC_FIELDS = (
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
)
_TRACE_ROOT_FIELDS = tuple(
    value
    for value in EXP2_TRACE_NUMERIC_FIELDS
    if value
    in {
        "preregistered_root_count",
        "final_result_root_count",
        "verified_correct_root_count",
        "completion_rate",
        "end_to_end_verified_success_rate",
        "trace_replay_wall_clock_ms",
        "bank_slot_consumption",
        "trace_attributed_tokens",
        "trace_attributed_cost",
        "planned_ai_unit_count",
        "executed_ai_unit_count",
        "unscheduled_ai_unit_count",
        "in_flight_at_witness",
        "observed_peak_concurrency",
        "worker_utilization",
    }
)
_TRACE_PAIR_FIELDS = EXP2_TRACE_NUMERIC_FIELDS[9:13]
_TRACE_REPEAT_FIELDS = EXP2_TRACE_NUMERIC_FIELDS[13:17]
_TRACE_CONDITION_FIELDS = EXP2_TRACE_NUMERIC_FIELDS[17:20]


@dataclass(frozen=True, kw_only=True)
class Exp2TraceConsumptionFacts:
    """一个已 hydration 的 current committed-consumption/source attribution fact。"""

    consumption_id: str
    committed: bool
    source_total_tokens: int | Decimal | None
    source_cost_estimate_cny: int | Decimal | None
    source_bank_roles: tuple[str, ...] | None

    def __post_init__(self) -> None:
        _non_empty(self.consumption_id, "consumption_id")
        if type(self.committed) is not bool:
            raise ValueError("committed must be a bool")
        for name in ("source_total_tokens", "source_cost_estimate_cny"):
            value = getattr(self, name)
            if value is not None:
                _nonnegative_number(value, name)
        _normalize_roles(self, "source_bank_roles")


@dataclass(frozen=True, kw_only=True)
class Exp2AIUnitFacts:
    """从已持久化 runtime observation hydration 的 AI-unit scheduling fact。"""

    unit_id: str
    planned: bool
    scheduled: bool
    executed: bool
    busy_worker_time_ms: int | Decimal | None

    def __post_init__(self) -> None:
        _non_empty(self.unit_id, "unit_id")
        if any(type(getattr(self, name)) is not bool for name in ("planned", "scheduled", "executed")):
            raise ValueError("AI-unit scheduling flags must be bools")
        if self.executed and not self.scheduled:
            raise ValueError("executed AI unit must be scheduled")
        if self.scheduled and not self.planned:
            raise ValueError("scheduled AI unit must be planned")
        if self.busy_worker_time_ms is not None:
            _nonnegative_number(self.busy_worker_time_ms, "busy_worker_time_ms")
        if self.executed != (self.busy_worker_time_ms is not None):
            raise ValueError("executed AI unit requires one busy time and no other unit may have it")


@dataclass(frozen=True, kw_only=True)
class Exp2TraceHydratedRoot:
    """Canonical direct row 加持久化 logical/scheduling/consumption facts。"""

    direct_result: PaperDirectRootResult
    persisted_logical_makespan_ms: int | Decimal | None
    trace_consumptions: tuple[Exp2TraceConsumptionFacts, ...] | None
    ai_units: tuple[Exp2AIUnitFacts, ...] | None
    in_flight_at_witness: int | None
    observed_peak_concurrency: int | None

    def __post_init__(self) -> None:
        _validate_direct(self.direct_result, "experiment_2_trace", "real_model_trace_protocol_run")
        if self.persisted_logical_makespan_ms is not None:
            _nonnegative_number(self.persisted_logical_makespan_ms, "persisted_logical_makespan_ms")
        _normalize_typed_tuple(self, "trace_consumptions", Exp2TraceConsumptionFacts)
        _normalize_typed_tuple(self, "ai_units", Exp2AIUnitFacts)
        for name in ("in_flight_at_witness", "observed_peak_concurrency"):
            value = getattr(self, name)
            if value is not None:
                _nonnegative_int(value, name)
        worker = _worker_count(self.direct_result)
        scheduled = None if self.ai_units is None else sum(unit.scheduled for unit in self.ai_units)
        if self.observed_peak_concurrency is not None and (
            self.observed_peak_concurrency > worker
            or self.observed_peak_concurrency > 20
            or (scheduled is not None and self.observed_peak_concurrency > scheduled)
        ):
            raise ValueError("observed peak exceeds worker, dispatched, or graph-width bound")
        if self.in_flight_at_witness is not None:
            if (
                self.in_flight_at_witness > worker
                or self.in_flight_at_witness > 20
                or (scheduled is None and self.in_flight_at_witness > 0)
                or (scheduled is not None and self.in_flight_at_witness > scheduled)
                or (
                    self.observed_peak_concurrency is None
                    and self.in_flight_at_witness > 0
                )
                or (
                    self.observed_peak_concurrency is not None
                    and self.in_flight_at_witness > self.observed_peak_concurrency
                )
            ):
                raise ValueError(
                    "in-flight witness exceeds worker, dispatched, graph-width, or peak bound"
                )
        _case_facts(self.direct_result)

    @property
    def case_record_digest(self) -> str:
        return _case_facts(self.direct_result)[0]


@dataclass(frozen=True, kw_only=True)
class Exp2OnlineFirstAttemptFacts:
    """在线检查中已物化的 actual first-provider-attempt facts。"""

    attempt_identity: str
    actual_call: bool
    provider_429: bool
    provider_timeout: bool
    total_tokens: int | Decimal | None
    cost_estimate_cny: int | Decimal | None
    provider_latency_ms: int | Decimal | None
    current_provider_roles: tuple[str, ...] | None

    def __post_init__(self) -> None:
        _non_empty(self.attempt_identity, "attempt_identity")
        if any(type(getattr(self, name)) is not bool for name in ("actual_call", "provider_429", "provider_timeout")):
            raise ValueError("online attempt flags must be bools")
        for name in ("total_tokens", "cost_estimate_cny", "provider_latency_ms"):
            value = getattr(self, name)
            if value is not None:
                _nonnegative_number(value, name)
        _normalize_roles(self, "current_provider_roles")


@dataclass(frozen=True, kw_only=True)
class Exp2OnlineHydratedRoot:
    direct_result: PaperDirectRootResult
    protocol_first_dispatch_at_ms: int | Decimal | None
    root_terminal_at_ms: int | Decimal | None
    first_provider_attempts: tuple[Exp2OnlineFirstAttemptFacts, ...] | None

    def __post_init__(self) -> None:
        _validate_direct(self.direct_result, "experiment_2_online", "online_real_provider")
        for name in ("protocol_first_dispatch_at_ms", "root_terminal_at_ms"):
            value = getattr(self, name)
            if value is not None:
                _nonnegative_number(value, name)
        if self.protocol_first_dispatch_at_ms is not None and self.root_terminal_at_ms is not None:
            if Decimal(str(self.root_terminal_at_ms)) < Decimal(str(self.protocol_first_dispatch_at_ms)):
                raise ValueError("online root terminal precedes first dispatch")
        _normalize_typed_tuple(self, "first_provider_attempts", Exp2OnlineFirstAttemptFacts)
        _case_facts(self.direct_result)


@dataclass(frozen=True, kw_only=True)
class Exp2ObservationCell:
    metric_id: str
    value: Decimal | int | None
    reason: str | None
    publish_blocked: bool
    membership: tuple[MemberDecision, ...]
    included_member_ids: tuple[str, ...]
    excluded_member_ids: tuple[str, ...]
    blocked_member_ids: tuple[str, ...]
    audit_denominator_member_ids: tuple[str, ...]
    paper_eligible: bool = False

    @classmethod
    def from_evaluation(cls, metric_id: str, evaluation: MetricEvaluation) -> "Exp2ObservationCell":
        return cls(
            metric_id=metric_id,
            value=evaluation.value,
            reason=evaluation.reason,
            publish_blocked=evaluation.publish_blocked,
            membership=evaluation.member_decisions,
            included_member_ids=evaluation.included_member_ids,
            excluded_member_ids=evaluation.excluded_member_ids,
            blocked_member_ids=evaluation.blocked_member_ids,
            audit_denominator_member_ids=evaluation.audit_denominator_member_ids,
            paper_eligible=False,
        )


class _CellLookup:
    cells: tuple[Exp2ObservationCell, ...]

    @property
    def metric_ids(self) -> tuple[str, ...]:
        return tuple(cell.metric_id for cell in self.cells)

    def require_cell(self, metric_id: str) -> Exp2ObservationCell:
        matches = tuple(cell for cell in self.cells if cell.metric_id == metric_id)
        if len(matches) != 1:
            raise ValueError(f"unknown Exp2 observation cell: {metric_id}")
        return matches[0]


@dataclass(frozen=True, kw_only=True)
class Exp2TraceWorkerRepeatObservation(_CellLookup):
    preregistered_root_run_ids: tuple[str, ...]
    case_record_digests: tuple[str, ...]
    factor_position_quantiles: tuple[str, ...]
    position_stratum: str
    repeat_id: int
    worker_count: int
    cells: tuple[Exp2ObservationCell, ...]
    paper_eligible: bool = False


@dataclass(frozen=True, kw_only=True)
class Exp2TracePairObservation(_CellLookup):
    pair_identity: tuple[str, int, int, int, int]
    baseline_root_run_id: str | None
    compared_root_run_id: str
    position_stratum: str
    cells: tuple[Exp2ObservationCell, ...]
    paper_eligible: bool = False


@dataclass(frozen=True, kw_only=True)
class Exp2TraceRepeatSummary(_CellLookup):
    worker_count: int
    repeat_id: int
    position_stratum: str
    cells: tuple[Exp2ObservationCell, ...]
    paper_eligible: bool = False


@dataclass(frozen=True, kw_only=True)
class Exp2TraceConditionSummary(_CellLookup):
    worker_count: int
    position_stratum: str
    cells: tuple[Exp2ObservationCell, ...]
    paper_eligible: bool = False


@dataclass(frozen=True, kw_only=True)
class Exp2TraceProjection:
    worker_repeat_rows: tuple[Exp2TraceWorkerRepeatObservation, ...]
    pair_rows: tuple[Exp2TracePairObservation, ...]
    repeat_summary_rows: tuple[Exp2TraceRepeatSummary, ...]
    condition_summary_rows: tuple[Exp2TraceConditionSummary, ...]
    paper_eligible: bool = False


@dataclass(frozen=True, kw_only=True)
class Exp2OnlineObservationRow(_CellLookup):
    worker_count: int
    case_position: str
    repeat_id: int
    cells: tuple[Exp2ObservationCell, ...]
    paper_eligible: bool = False


@dataclass(frozen=True, kw_only=True)
class Exp2OnlineTracePostBankCheck:
    status: str
    same_case_intersection_count_by_worker: Mapping[int, int]
    severe_worker_counts: tuple[int, ...]
    severe_reasons_by_worker: Mapping[int, tuple[str, ...]]
    paper_eligible: bool = False


@dataclass(frozen=True, kw_only=True)
class Exp2OnlineProjection:
    rows: tuple[Exp2OnlineObservationRow, ...]
    post_bank_check: Exp2OnlineTracePostBankCheck
    paper_eligible: bool = False


@dataclass(frozen=True, kw_only=True)
class _PairInput:
    observation: Exp2TracePairObservation
    facts: Mapping[str, object]
    root_facts: Mapping[str, Mapping[str, object]]
    infra_invalid: bool


def build_exp2_trace_observations(
    hydrated_roots: Sequence[Exp2TraceHydratedRoot],
    contract: PaperMetricContract,
) -> Exp2TraceProjection:
    """只消费 hydrated canonical facts，投影 Exp2 trace 的 26 个合同 cells。"""

    _validate_contract(contract)
    roots = tuple(hydrated_roots)
    if any(not isinstance(value, Exp2TraceHydratedRoot) for value in roots):
        raise TypeError("hydrated_roots must contain Exp2TraceHydratedRoot")
    root_ids = tuple(value.direct_result.preregistered_root_run_id for value in roots)
    if len(set(root_ids)) != len(root_ids):
        raise ValueError("duplicate Exp2 trace preregistered root run id")
    identity_index: dict[tuple[str, int, int, int], Exp2TraceHydratedRoot] = {}
    worker_repeat_groups: dict[
        tuple[int, int, str], list[Exp2TraceHydratedRoot]
    ] = {}
    for value in sorted(roots, key=lambda item: item.direct_result.preregistered_root_run_id):
        row = value.direct_result
        digest, _, stratum = _case_facts(row)
        sample = _sample_slot(row)
        worker = _worker_count(row)
        identity = (digest, row.repeat_id, sample, worker)
        if identity in identity_index:
            raise ValueError("duplicate Exp2 trace case/repeat/sample/worker identity")
        identity_index[identity] = value
        worker_repeat_groups.setdefault(
            (worker, row.repeat_id, stratum), []
        ).append(value)

    worker_repeat_rows: list[Exp2TraceWorkerRepeatObservation] = []
    for key in sorted(worker_repeat_groups, key=_worker_repeat_sort_key):
        values = tuple(
            sorted(
                worker_repeat_groups[key],
                key=lambda item: item.direct_result.preregistered_root_run_id,
            )
        )
        case_facts = tuple(_case_facts(value.direct_result) for value in values)
        worker_repeat_rows.append(
            Exp2TraceWorkerRepeatObservation(
                preregistered_root_run_ids=tuple(
                    value.direct_result.preregistered_root_run_id for value in values
                ),
                case_record_digests=tuple(value[0] for value in case_facts),
                factor_position_quantiles=tuple(value[1] for value in case_facts),
                position_stratum=key[2],
                repeat_id=key[1],
                worker_count=key[0],
                cells=_evaluate_cells(
                    contract,
                    TRACE_TABLE_ID,
                    "worker_repeat_observation",
                    _TRACE_ROOT_FIELDS,
                    _trace_worker_repeat_bundle(key, values),
                ),
            )
        )

    pair_inputs: list[_PairInput] = []
    for compared_identity, compared in sorted(identity_index.items()):
        digest, repeat, sample, worker = compared_identity
        if worker == 1:
            continue
        baseline = identity_index.get((digest, repeat, sample, 1))
        pair_inputs.append(_build_pair(contract, baseline, compared))

    repeat_rows: list[Exp2TraceRepeatSummary] = []
    repeat_groups: dict[tuple[int, int, str], list[_PairInput]] = {}
    for value in pair_inputs:
        worker = value.observation.pair_identity[-1]
        repeat = value.observation.pair_identity[1]
        repeat_groups.setdefault((worker, repeat, value.observation.position_stratum), []).append(value)
    repeat_values: dict[tuple[int, int, str], Decimal | None] = {}
    for key in sorted(repeat_groups):
        values = repeat_groups[key]
        bundle = _pair_collection_bundle(key, values, summary_kind="repeat")
        cells = _evaluate_cells(contract, TRACE_TABLE_ID, "repeat_summary", _TRACE_REPEAT_FIELDS, bundle)
        row = Exp2TraceRepeatSummary(worker_count=key[0], repeat_id=key[1], position_stratum=key[2], cells=cells)
        repeat_rows.append(row)
        median = row.require_cell("trace_replay_paired_speedup_median").value
        repeat_values[key] = Decimal(median) if median is not None else None

    condition_rows: list[Exp2TraceConditionSummary] = []
    condition_keys = sorted({(worker, stratum) for worker, _, stratum in repeat_groups})
    for worker, stratum in condition_keys:
        selected = tuple((key, repeat_values[key]) for key in sorted(repeat_values) if key[0] == worker and key[2] == stratum)
        facts: dict[str, Mapping[str, object]] = {}
        invalid = False
        for key, median in selected:
            member_id = f"repeat-summary:w{worker}:r{key[1]}:{stratum}"
            item: dict[str, object] = {"member_kind": "exp2_repeat_speedup_summary"}
            if median is not None:
                item["repeat_speedup_value"] = median
            facts[member_id] = item
            invalid = invalid or any(value.infra_invalid for value in repeat_groups[key])
        bundle = _bundle(
            row_facts={"infra_invalid": invalid},
            member_facts=facts,
            identity={"kind": "condition", "worker": worker, "stratum": stratum},
        )
        condition_rows.append(
            Exp2TraceConditionSummary(
                worker_count=worker,
                position_stratum=stratum,
                cells=_evaluate_cells(contract, TRACE_TABLE_ID, "condition_summary", _TRACE_CONDITION_FIELDS, bundle),
            )
        )
    return Exp2TraceProjection(
        worker_repeat_rows=tuple(worker_repeat_rows),
        pair_rows=tuple(value.observation for value in pair_inputs),
        repeat_summary_rows=tuple(repeat_rows),
        condition_summary_rows=tuple(condition_rows),
    )


def build_exp2_online_observations(
    hydrated_roots: Sequence[Exp2OnlineHydratedRoot],
    contract: PaperMetricContract,
    *,
    complete_main_trace: Exp2TraceProjection | None = None,
    main_trace_complete: bool = False,
) -> Exp2OnlineProjection:
    """按 worker/case-position/repeat 投影 online 12 cells。"""

    _validate_contract(contract)
    roots = tuple(hydrated_roots)
    if any(not isinstance(value, Exp2OnlineHydratedRoot) for value in roots):
        raise TypeError("hydrated_roots must contain Exp2OnlineHydratedRoot")
    root_ids = tuple(value.direct_result.preregistered_root_run_id for value in roots)
    if len(set(root_ids)) != len(root_ids):
        raise ValueError("duplicate Exp2 online preregistered root run id")
    groups: dict[tuple[int, str, int], list[Exp2OnlineHydratedRoot]] = {}
    for value in roots:
        groups.setdefault(
            (
                _worker_count(value.direct_result),
                _case_facts(value.direct_result)[2],
                value.direct_result.repeat_id,
            ),
            [],
        ).append(value)
    rows: list[Exp2OnlineObservationRow] = []
    for key in sorted(groups, key=_online_scope_sort_key):
        bundle = _online_bundle(key, tuple(groups[key]))
        rows.append(
            Exp2OnlineObservationRow(
                worker_count=key[0],
                case_position=key[1],
                repeat_id=key[2],
                cells=_evaluate_cells(contract, ONLINE_TABLE_ID, "worker_condition_summary", EXP2_ONLINE_NUMERIC_FIELDS, bundle),
            )
        )
    return Exp2OnlineProjection(
        rows=tuple(rows),
        post_bank_check=_post_bank_check(
            roots,
            complete_main_trace=complete_main_trace,
            main_trace_complete=main_trace_complete,
            minimum_intersection=contract.controls.exp2_online_trace_same_case_intersection_min,
            ratio_min=contract.controls.severe_speedup_ratio_min,
            ratio_max=contract.controls.severe_speedup_ratio_max,
            opposed_high=contract.controls.severe_opposed_trend_high,
            opposed_low=contract.controls.severe_opposed_trend_low,
        ),
    )


def _validate_contract(contract: PaperMetricContract) -> None:
    if not isinstance(contract, PaperMetricContract):
        raise TypeError("contract must be PaperMetricContract")
    contract.validate_numeric_output_fields(TRACE_TABLE_ID, EXP2_TRACE_NUMERIC_FIELDS)
    contract.validate_numeric_output_fields(ONLINE_TABLE_ID, EXP2_ONLINE_NUMERIC_FIELDS)


def _trace_root_bundle(value: Exp2TraceHydratedRoot) -> MetricObservationBundle:
    row = value.direct_result
    member_facts: dict[str, Mapping[str, object]] = {
        row.preregistered_root_run_id: _root_member_facts(row)
    }
    if value.trace_consumptions is None:
        member_facts[f"missing-consumptions:{row.preregistered_root_run_id}"] = {
            "member_kind": "committed_trace_consumption",
            "committed": True,
        }
    else:
        for consumption in value.trace_consumptions:
            facts: dict[str, object] = {
                "member_kind": "committed_trace_consumption",
                "committed": consumption.committed,
            }
            if consumption.source_total_tokens is not None:
                facts["source_usage_total_tokens"] = consumption.source_total_tokens
            if consumption.source_cost_estimate_cny is not None:
                facts["source_cost_estimate_cny"] = consumption.source_cost_estimate_cny
            if consumption.source_bank_roles is not None:
                facts["source_bank_roles"] = consumption.source_bank_roles
            _insert_member(member_facts, consumption.consumption_id, facts)
    if value.ai_units is None:
        member_facts[f"missing-ai-units:{row.preregistered_root_run_id}"] = {
            "member_kind": "exp2_ai_unit"
        }
    else:
        for unit in value.ai_units:
            facts = {
                "member_kind": "exp2_ai_unit",
                "planned": unit.planned,
                "scheduled": unit.scheduled,
                "executed": unit.executed,
            }
            if unit.busy_worker_time_ms is not None:
                facts["busy_worker_time_ms"] = unit.busy_worker_time_ms
            _insert_member(member_facts, unit.unit_id, facts)
    row_facts: dict[str, object] = {
        "infra_invalid": _publication_invalid(row),
        "configured_worker_count": _worker_count(row),
    }
    if value.persisted_logical_makespan_ms is not None:
        row_facts["runtime_elapsed_ms"] = value.persisted_logical_makespan_ms
    if value.in_flight_at_witness is not None:
        row_facts["witness_in_flight_count"] = value.in_flight_at_witness
    if value.observed_peak_concurrency is not None:
        row_facts["scheduler_peak_active_count"] = value.observed_peak_concurrency
    return _bundle(
        row_facts=row_facts,
        member_facts=member_facts,
        identity={"kind": "trace-root", "root": row.preregistered_root_run_id},
    )


def _trace_worker_repeat_bundle(
    key: tuple[int, int, str],
    roots: tuple[Exp2TraceHydratedRoot, ...],
) -> MetricObservationBundle:
    member_facts: dict[str, Mapping[str, object]] = {}
    invalid = False
    for value in roots:
        bundle = _trace_root_bundle(value)
        for member_id in bundle.member_ids:
            _insert_member(
                member_facts,
                member_id,
                bundle.member_facts_by_id[member_id],
            )
        invalid = invalid or _publication_invalid(value.direct_result)
    row_facts: dict[str, object] = {
        "infra_invalid": invalid,
        "configured_worker_count": key[0],
    }
    if all(value.persisted_logical_makespan_ms is not None for value in roots):
        row_facts["runtime_elapsed_ms"] = sum(
            (
                Decimal(str(value.persisted_logical_makespan_ms))
                for value in roots
            ),
            Decimal(0),
        )
    if all(value.in_flight_at_witness is not None for value in roots):
        row_facts["witness_in_flight_count"] = max(
            value.in_flight_at_witness for value in roots
        )
    if all(value.observed_peak_concurrency is not None for value in roots):
        row_facts["scheduler_peak_active_count"] = max(
            value.observed_peak_concurrency for value in roots
        )
    return _bundle(
        row_facts=row_facts,
        member_facts=member_facts,
        identity={
            "kind": "trace-worker-repeat",
            "worker": key[0],
            "repeat": key[1],
            "position_stratum": key[2],
        },
    )


def _build_pair(
    contract: PaperMetricContract,
    baseline: Exp2TraceHydratedRoot | None,
    compared: Exp2TraceHydratedRoot,
) -> _PairInput:
    row = compared.direct_result
    digest, _, stratum = _case_facts(row)
    repeat = row.repeat_id
    sample = _sample_slot(row)
    worker = _worker_count(row)
    identity = (digest, repeat, sample, 1, worker)
    baseline_row = None if baseline is None else baseline.direct_result
    facts: dict[str, object] = {
        "member_kind": "exp2_preregistered_pair",
        "baseline_worker_count": 1,
        "compared_worker_count": worker,
        "baseline_case_record_digest": digest,
        "compared_case_record_digest": digest,
        "baseline_repeat_id": repeat,
        "compared_repeat_id": repeat,
        "baseline_sample_slot_index": sample,
        "compared_sample_slot_index": sample,
        "baseline_end_to_end_verified_success": False if baseline is None else baseline_row.end_to_end_verified_success,
        "compared_end_to_end_verified_success": row.end_to_end_verified_success,
        "baseline_time_evidence_complete": baseline is not None and baseline.persisted_logical_makespan_ms is not None,
        "compared_time_evidence_complete": compared.persisted_logical_makespan_ms is not None,
        "baseline_trace_replay_wall_clock_ms": 0 if baseline is None or baseline.persisted_logical_makespan_ms is None else baseline.persisted_logical_makespan_ms,
        "compared_trace_replay_wall_clock_ms": 0 if compared.persisted_logical_makespan_ms is None else compared.persisted_logical_makespan_ms,
    }
    baseline_resource = _resource_totals(baseline)
    compared_resource = _resource_totals(compared)
    facts.update(
        {
            "baseline_resource_evidence_complete": baseline_resource is not None,
            "compared_resource_evidence_complete": compared_resource is not None,
            "baseline_trace_attributed_tokens": 0 if baseline_resource is None else baseline_resource[0],
            "baseline_trace_attributed_cost": 0 if baseline_resource is None else baseline_resource[1],
            "compared_trace_attributed_tokens": 0 if compared_resource is None else compared_resource[0],
            "compared_trace_attributed_cost": 0 if compared_resource is None else compared_resource[1],
        }
    )
    facts["closed_exclusion_reason"] = _closed_pair_exclusion(baseline, compared)
    root_facts = {row.preregistered_root_run_id: _root_member_facts(row)}
    if baseline_row is not None:
        root_facts[baseline_row.preregistered_root_run_id] = _root_member_facts(baseline_row)
    member_id = _pair_member_id(identity)
    members: dict[str, Mapping[str, object]] = {**root_facts, member_id: facts}
    invalid = _publication_invalid(row) or (baseline_row is not None and _publication_invalid(baseline_row))
    bundle = _bundle(
        row_facts={"infra_invalid": invalid, "pair_evidence_complete": baseline is not None},
        member_facts=members,
        identity={"kind": "pair", "pair": identity},
    )
    observation = Exp2TracePairObservation(
        pair_identity=identity,
        baseline_root_run_id=None if baseline_row is None else baseline_row.preregistered_root_run_id,
        compared_root_run_id=row.preregistered_root_run_id,
        position_stratum=stratum,
        cells=_evaluate_cells(contract, TRACE_TABLE_ID, "paired_worker_comparison", _TRACE_PAIR_FIELDS, bundle),
    )
    return _PairInput(observation=observation, facts=facts, root_facts=root_facts, infra_invalid=invalid)


def _pair_collection_bundle(
    key: tuple[int, int, str],
    values: Sequence[_PairInput],
    *,
    summary_kind: str,
) -> MetricObservationBundle:
    facts: dict[str, Mapping[str, object]] = {}
    invalid = False
    for value in values:
        for member_id, member in value.root_facts.items():
            facts.setdefault(member_id, member)
        _insert_member(facts, _pair_member_id(value.observation.pair_identity), value.facts)
        invalid = invalid or value.infra_invalid
    return _bundle(
        row_facts={"infra_invalid": invalid, "summary_kind": summary_kind},
        member_facts=facts,
        identity={"kind": "repeat", "worker": key[0], "repeat": key[1], "stratum": key[2]},
    )


def _online_bundle(
    key: tuple[int, str, int],
    roots: tuple[Exp2OnlineHydratedRoot, ...],
) -> MetricObservationBundle:
    facts: dict[str, Mapping[str, object]] = {}
    invalid = False
    attempts = _unique_online_attempts(roots)
    for value in roots:
        row = value.direct_result
        root_facts = dict(_root_member_facts(row))
        if value.protocol_first_dispatch_at_ms is not None:
            root_facts["protocol_first_dispatch_at_ms"] = value.protocol_first_dispatch_at_ms
        if value.root_terminal_at_ms is not None:
            root_facts["root_terminal_at_ms"] = value.root_terminal_at_ms
        facts[row.preregistered_root_run_id] = root_facts
        invalid = invalid or _publication_invalid(row)
        if value.first_provider_attempts is None:
            facts[f"missing-online-attempts:{row.preregistered_root_run_id}"] = {
                "member_kind": "exp2_online_first_provider_attempt"
            }
    for attempt in attempts:
        if not attempt.actual_call:
            continue
        item: dict[str, object] = {
            "member_kind": "exp2_online_first_provider_attempt",
            "actual_call": attempt.actual_call,
            "provider_429": attempt.provider_429,
            "provider_timeout": attempt.provider_timeout,
        }
        for source_name, target_name in (
            ("total_tokens", "actual_total_tokens"),
            ("cost_estimate_cny", "actual_cost_estimate_cny"),
            ("provider_latency_ms", "provider_latency_ms"),
            ("current_provider_roles", "current_provider_roles"),
        ):
            value = getattr(attempt, source_name)
            if value is not None:
                item[target_name] = value
        _insert_member(facts, attempt.attempt_identity, item)
    return _bundle(
        row_facts={"infra_invalid": invalid},
        member_facts=facts,
        identity={
            "kind": "online-worker-position",
            "worker": key[0],
            "case_position": key[1],
            "repeat": key[2],
        },
    )


def _worker_repeat_sort_key(key: tuple[int, int, str]) -> tuple[int, int, int, str]:
    return (key[0], key[1], _POSITION_ORDER.get(key[2], len(_POSITION_ORDER)), key[2])


def _online_scope_sort_key(key: tuple[int, str, int]) -> tuple[int, int, str, int]:
    return (key[0], _POSITION_ORDER.get(key[1], len(_POSITION_ORDER)), key[1], key[2])


def _unique_online_attempts(
    roots: Sequence[Exp2OnlineHydratedRoot],
) -> tuple[Exp2OnlineFirstAttemptFacts, ...]:
    by_id: dict[str, Exp2OnlineFirstAttemptFacts] = {}
    for root in roots:
        for attempt in root.first_provider_attempts or ():
            previous = by_id.get(attempt.attempt_identity)
            if previous is None:
                by_id[attempt.attempt_identity] = attempt
                continue
            stable_previous = (
                previous.actual_call,
                previous.total_tokens,
                previous.cost_estimate_cny,
                previous.provider_latency_ms,
                previous.current_provider_roles,
            )
            stable_current = (
                attempt.actual_call,
                attempt.total_tokens,
                attempt.cost_estimate_cny,
                attempt.provider_latency_ms,
                attempt.current_provider_roles,
            )
            if stable_previous != stable_current:
                raise ValueError("conflicting duplicate online first-attempt facts")
            by_id[attempt.attempt_identity] = Exp2OnlineFirstAttemptFacts(
                attempt_identity=attempt.attempt_identity,
                actual_call=attempt.actual_call,
                provider_429=previous.provider_429 or attempt.provider_429,
                provider_timeout=previous.provider_timeout or attempt.provider_timeout,
                total_tokens=attempt.total_tokens,
                cost_estimate_cny=attempt.cost_estimate_cny,
                provider_latency_ms=attempt.provider_latency_ms,
                current_provider_roles=attempt.current_provider_roles,
            )
    return tuple(by_id[key] for key in sorted(by_id))


def _post_bank_check(
    online_roots: Sequence[Exp2OnlineHydratedRoot],
    *,
    complete_main_trace: Exp2TraceProjection | None,
    main_trace_complete: bool,
    minimum_intersection: int,
    ratio_min: Decimal,
    ratio_max: Decimal,
    opposed_high: Decimal,
    opposed_low: Decimal,
) -> Exp2OnlineTracePostBankCheck:
    if not main_trace_complete or complete_main_trace is None:
        return Exp2OnlineTracePostBankCheck(
            status="not_evaluated_pre_bank",
            same_case_intersection_count_by_worker={},
            severe_worker_counts=(),
            severe_reasons_by_worker={},
        )
    online_index: dict[tuple[str, int, int], Decimal] = {}
    for value in online_roots:
        if value.protocol_first_dispatch_at_ms is None or value.root_terminal_at_ms is None:
            continue
        row = value.direct_result
        if not row.end_to_end_verified_success:
            continue
        elapsed = Decimal(str(value.root_terminal_at_ms)) - Decimal(str(value.protocol_first_dispatch_at_ms))
        if elapsed <= 0:
            continue
        online_index[(_case_facts(row)[0], row.repeat_id, _worker_count(row))] = elapsed
    trace_speeds = {
        (row.pair_identity[0], row.pair_identity[1], row.pair_identity[4]): Decimal(cell.value)
        for row in complete_main_trace.pair_rows
        if (cell := row.require_cell("trace_replay_paired_speedup")).value is not None
    }
    intersections: dict[int, int] = {}
    severe: dict[int, tuple[str, ...]] = {}
    insufficient = False
    present_workers = sorted(
        ({key[2] for key in trace_speeds} | {key[2] for key in online_index})
        - {1}
    )
    if not present_workers:
        insufficient = True
    for worker in present_workers:
        comparisons: list[tuple[Decimal, Decimal]] = []
        for (case_digest, repeat, candidate_worker), trace_speed in trace_speeds.items():
            if candidate_worker != worker:
                continue
            baseline = online_index.get((case_digest, repeat, 1))
            compared = online_index.get((case_digest, repeat, worker))
            if baseline is not None and compared is not None and compared > 0:
                comparisons.append((trace_speed, baseline / compared))
        intersections[worker] = len(comparisons)
        reasons: list[str] = []
        if len(comparisons) < minimum_intersection:
            insufficient = True
        for trace_speed, online_speed in comparisons:
            ratio = online_speed / trace_speed
            if ratio < ratio_min or ratio > ratio_max:
                reasons.append("speedup_ratio_outside_frozen_range")
            if (trace_speed >= opposed_high and online_speed <= opposed_low) or (
                online_speed >= opposed_high and trace_speed <= opposed_low
            ):
                reasons.append("opposed_speedup_trends")
        if reasons:
            severe[worker] = tuple(dict.fromkeys(reasons))
    return Exp2OnlineTracePostBankCheck(
        status=(
            "severe_divergence"
            if severe
            else "insufficient_intersection"
            if insufficient
            else "evaluated_post_bank"
        ),
        same_case_intersection_count_by_worker=intersections,
        severe_worker_counts=tuple(sorted(severe)),
        severe_reasons_by_worker=severe,
    )


def _evaluate_cells(
    contract: PaperMetricContract,
    table_id: str,
    row_kind: str,
    metric_ids: Sequence[str],
    bundle: MetricObservationBundle,
) -> tuple[Exp2ObservationCell, ...]:
    observations: list[VerifiedMetricObservation] = []
    cells: list[Exp2ObservationCell] = []
    for metric_id in metric_ids:
        current = MetricObservationBundle(
            row_facts=bundle.row_facts,
            member_ids=bundle.member_ids,
            member_facts_by_id=bundle.member_facts_by_id,
            verified_observations=tuple(observations),
            row_identity_digest=bundle.row_identity_digest,
        )
        evaluation = recompute_metric(contract, table_id, metric_id, row_kind=row_kind, bundle=current)
        cells.append(Exp2ObservationCell.from_evaluation(metric_id, evaluation))
        if evaluation.value is not None and not evaluation.publish_blocked:
            metric = contract.require_metric(table_id, metric_id)
            observations.append(
                VerifiedMetricObservation(
                    observation_id=f"{bundle.row_identity_digest}:{metric_id}",
                    table_id=table_id,
                    metric_id=metric_id,
                    row_kind=row_kind,
                    value=evaluation.value,
                    value_domain=metric.value_domain,
                    source_member_ids=evaluation.included_member_ids,
                    evidence_verified=True,
                    row_identity_digest=bundle.row_identity_digest,
                )
            )
    return tuple(cells)


def _bundle(
    *,
    row_facts: Mapping[str, object],
    member_facts: Mapping[str, Mapping[str, object]],
    identity: Mapping[str, object],
) -> MetricObservationBundle:
    table_id = (
        ONLINE_TABLE_ID
        if str(identity.get("kind", "")).startswith("online")
        else TRACE_TABLE_ID
    )
    return MetricObservationBundle(
        row_facts=row_facts,
        member_ids=tuple(member_facts),
        member_facts_by_id=member_facts,
        row_identity_digest=digest_json({"table_id": table_id, **identity}),
    )


def _root_member_facts(row: PaperDirectRootResult) -> Mapping[str, object]:
    return {
        "member_kind": "preregistered_root",
        "final_result_reference_complete": row.final_result_reference_complete,
        "end_to_end_verified_success": row.end_to_end_verified_success,
    }


def _resource_totals(value: Exp2TraceHydratedRoot | None) -> tuple[Decimal, Decimal] | None:
    if value is None or value.trace_consumptions is None:
        return None
    committed = tuple(item for item in value.trace_consumptions if item.committed)
    if any(
        item.source_total_tokens is None
        or item.source_cost_estimate_cny is None
        or item.source_bank_roles != TRACE_SOURCE_BANK_ROLES
        for item in committed
    ):
        return None
    return (
        sum((Decimal(str(item.source_total_tokens)) for item in committed), Decimal(0)),
        sum((Decimal(str(item.source_cost_estimate_cny)) for item in committed), Decimal(0)),
    )


def _closed_pair_exclusion(
    baseline: Exp2TraceHydratedRoot | None,
    compared: Exp2TraceHydratedRoot,
) -> str:
    if baseline is None or not baseline.direct_result.end_to_end_verified_success:
        return "baseline_not_end_to_end_success"
    if not compared.direct_result.end_to_end_verified_success:
        return "compared_not_end_to_end_success"
    if baseline.persisted_logical_makespan_ms is None:
        return "baseline_time_evidence_incomplete"
    if compared.persisted_logical_makespan_ms is None:
        return "compared_time_evidence_incomplete"
    if Decimal(str(baseline.persisted_logical_makespan_ms)) <= 0:
        return "baseline_time_not_positive"
    if Decimal(str(compared.persisted_logical_makespan_ms)) <= 0:
        return "compared_time_not_positive"
    return "pair_is_eligible"


def _pair_member_id(identity: tuple[str, int, int, int, int]) -> str:
    return "pair:" + digest_json(list(identity))[7:]


def _case_facts(row: PaperDirectRootResult) -> tuple[str, str, str]:
    ref = row.preregistered_case_ref
    expected = {
        "schema_version",
        "catalog_digest",
        "case_record_digest",
        "case_axes_digest",
        "factor_position_quantile",
        "position_stratum",
    }
    if set(ref) != expected:
        raise ValueError("Exp2 preregistered_case_ref schema drift")
    digest = ref["case_record_digest"]
    quantile = ref["factor_position_quantile"]
    stratum = ref["position_stratum"]
    _non_empty(digest, "case_record_digest")
    _non_empty(quantile, "factor_position_quantile")
    _non_empty(stratum, "position_stratum")
    return digest, quantile, stratum


def _sample_slot(row: PaperDirectRootResult) -> int:
    value = row.condition_axes.get("sample_slot_index")
    _nonnegative_int(value, "sample_slot_index")
    return value


def _worker_count(row: PaperDirectRootResult) -> int:
    value = row.condition_axes.get("worker_count")
    _nonnegative_int(value, "worker_count")
    if value not in EXP2_WORKER_COUNTS:
        raise ValueError("unsupported Exp2 worker count")
    return value


def _validate_direct(row: object, experiment_id: str, evidence_class: str) -> None:
    if not isinstance(row, PaperDirectRootResult):
        raise TypeError("direct_result must be PaperDirectRootResult")
    if row.experiment_id != experiment_id or row.evidence_class != evidence_class:
        raise ValueError("Exp2 direct result experiment/evidence class mismatch")
    if row.condition_axes.get("domain") != "factorization" or row.condition_axes.get("difficulty") != "hard":
        raise ValueError("Exp2 projector only accepts Factorization hard roots")


def _publication_invalid(row: PaperDirectRootResult) -> bool:
    return bool(
        row.ineligibility_reasons
        or not row.identity_consistent
        or not row.infrastructure_valid
        or (
            row.root_status != PaperDirectRootStatus.NOT_STARTED.value
            and not row.paper_evidence_complete
        )
    )


def _insert_member(
    members: dict[str, Mapping[str, object]],
    member_id: str,
    facts: Mapping[str, object],
) -> None:
    _non_empty(member_id, "member_id")
    if member_id in members:
        raise ValueError("duplicate Exp2 member id")
    members[member_id] = facts


def _normalize_roles(instance: object, field_name: str) -> None:
    roles = getattr(instance, field_name)
    if roles is None:
        return
    roles = tuple(roles)
    if any(not isinstance(role, str) or not role for role in roles) or len(set(roles)) != len(roles):
        raise ValueError(f"{field_name} must contain unique non-empty strings")
    object.__setattr__(instance, field_name, roles)


def _normalize_typed_tuple(instance: object, field_name: str, item_type: type) -> None:
    values = getattr(instance, field_name)
    if values is None:
        return
    values = tuple(values)
    if any(not isinstance(value, item_type) for value in values):
        raise TypeError(f"{field_name} contains an invalid fact")
    identity_field = {
        Exp2TraceConsumptionFacts: "consumption_id",
        Exp2AIUnitFacts: "unit_id",
        Exp2OnlineFirstAttemptFacts: "attempt_identity",
    }[item_type]
    ids = tuple(getattr(value, identity_field) for value in values)
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate identity in {field_name}")
    object.__setattr__(instance, field_name, values)


def _non_empty(value: object, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be non-empty")


def _nonnegative_int(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")


def _nonnegative_number(value: object, name: str) -> None:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a nonnegative number")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a nonnegative number") from exc
    if not number.is_finite() or number < 0:
        raise ValueError(f"{name} must be a nonnegative number")


__all__ = [
    "EXP2_ONLINE_NUMERIC_FIELDS",
    "EXP2_TRACE_NUMERIC_FIELDS",
    "EXP2_WORKER_COUNTS",
    "ONLINE_CURRENT_PROVIDER_ROLES",
    "TRACE_SOURCE_BANK_ROLES",
    "Exp2AIUnitFacts",
    "Exp2ObservationCell",
    "Exp2OnlineFirstAttemptFacts",
    "Exp2OnlineHydratedRoot",
    "Exp2OnlineObservationRow",
    "Exp2OnlineProjection",
    "Exp2OnlineTracePostBankCheck",
    "Exp2TraceConditionSummary",
    "Exp2TraceConsumptionFacts",
    "Exp2TraceHydratedRoot",
    "Exp2TracePairObservation",
    "Exp2TraceProjection",
    "Exp2TraceRepeatSummary",
    "Exp2TraceWorkerRepeatObservation",
    "build_exp2_online_observations",
    "build_exp2_trace_observations",
]
