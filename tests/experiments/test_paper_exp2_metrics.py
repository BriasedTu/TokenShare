"""Task 13：Experiment 2 trace-main 与 online projector。"""

from __future__ import annotations

from decimal import Decimal
from dataclasses import replace

import pytest

from tokenshare.experiments import paper_exp2_metrics
from tests.experiments.test_paper_direct_results import (
    _canonical_fixture,
    _catalog,
    _case_ref,
    _condition_manifest,
    _inventory_manifest,
    _inventory_row,
    _project,
    _replace_inventory_row,
)
from tokenshare.experiments.paper_direct_results import build_canonical_direct_evidence
from tokenshare.experiments.paper_exp2_metrics import (
    EXP2_ONLINE_NUMERIC_FIELDS,
    EXP2_TRACE_NUMERIC_FIELDS,
    ONLINE_CURRENT_PROVIDER_ROLES,
    TRACE_SOURCE_BANK_ROLES,
    Exp2AIUnitFacts,
    Exp2OnlineFirstAttemptFacts,
    Exp2OnlineHydratedRoot,
    Exp2TraceConsumptionFacts,
    Exp2TraceHydratedRoot,
    build_exp2_online_observations,
    build_exp2_trace_observations,
)
from tokenshare.experiments.paper_metric_contract import (
    capture_metric_computation_traces,
    load_paper_metric_contract,
)


def _direct_row(
    tmp_path,
    *,
    root_id: str,
    worker: int,
    evidence_class: str,
    case_id: str = "opaque-case",
    repeat: int = 0,
    sample: int = 0,
    stratum: str = "early",
    quantile: str = "front",
    correct: bool = True,
    started: bool = True,
    verdict: bool = True,
):
    row, _, _ = _inventory_row(
        root_id=root_id,
        case_id=case_id,
        worker_count=worker,
        evidence_class=evidence_class,
    )
    axes = dict(row.condition_axes)
    axes["sample_slot_index"] = sample
    condition, condition_ref = _condition_manifest(f"condition:{root_id}", axes)
    # case_id 是 opaque catalog key；stratum/quantile 仍只来自绑定 record。
    catalog, records = _catalog([(case_id, quantile, stratum)])
    row = _replace_inventory_row(
        row,
        experiment_id=(
            "experiment_2_trace"
            if evidence_class == "real_model_trace_protocol_run"
            else "experiment_2_online"
        ),
        condition_id=f"condition:{root_id}",
        preregistered_condition_ref=condition_ref,
        condition_axes=axes,
        case_id=case_id,
        preregistered_case_ref=_case_ref(catalog, records[case_id]),
        repeat_id=repeat,
    )
    if not started:
        return _project(_inventory_manifest(row), (condition,), (catalog,), {}).rows[0]
    kwargs, *_ = _canonical_fixture(
        tmp_path,
        row,
        correct=correct,
        evidence_class=evidence_class,
        include_verdict=verdict,
    )
    evidence = build_canonical_direct_evidence(**kwargs)
    return _project(
        _inventory_manifest(row),
        (condition,),
        (catalog,),
        {root_id: evidence},
    ).rows[0]


def _consumption(
    identity: str,
    *,
    tokens: int = 100,
    cost: Decimal = Decimal("1.0"),
    committed: bool = True,
):
    return Exp2TraceConsumptionFacts(
        consumption_id=identity,
        committed=committed,
        source_total_tokens=tokens,
        source_cost_estimate_cny=cost,
        source_bank_roles=TRACE_SOURCE_BANK_ROLES,
    )


def _units(prefix: str, *, planned: int = 3, executed: int = 2):
    return tuple(
        Exp2AIUnitFacts(
            unit_id=f"{prefix}:{index}",
            planned=True,
            scheduled=index < executed,
            executed=index < executed,
            busy_worker_time_ms=100 if index < executed else None,
        )
        for index in range(planned)
    )


def _trace(
    direct,
    *,
    makespan: int | None,
    consumptions=None,
    units=None,
    inflight: int = 0,
    peak: int | None = None,
):
    resolved_units = _units(direct.preregistered_root_run_id) if units is None else units
    resolved_peak = min(1, sum(unit.scheduled for unit in resolved_units)) if peak is None else peak
    return Exp2TraceHydratedRoot(
        direct_result=direct,
        persisted_logical_makespan_ms=makespan,
        trace_consumptions=(
            (_consumption(f"consumption:{direct.preregistered_root_run_id}"),)
            if consumptions is None
            else consumptions
        ),
        ai_units=resolved_units,
        in_flight_at_witness=inflight,
        observed_peak_concurrency=resolved_peak,
    )


def _attempt(
    identity: str,
    *,
    provider_429: bool = False,
    provider_timeout: bool = False,
    tokens: int | None = 10,
    cost: Decimal | None = Decimal("0.1"),
    latency: int | None = 20,
):
    return Exp2OnlineFirstAttemptFacts(
        attempt_identity=identity,
        actual_call=True,
        provider_429=provider_429,
        provider_timeout=provider_timeout,
        total_tokens=tokens,
        cost_estimate_cny=cost,
        provider_latency_ms=latency,
        current_provider_roles=ONLINE_CURRENT_PROVIDER_ROLES,
    )


def _online(direct, *, dispatch: int = 100, terminal: int = 400, attempts=()):
    return Exp2OnlineHydratedRoot(
        direct_result=direct,
        protocol_first_dispatch_at_ms=dispatch,
        root_terminal_at_ms=terminal,
        first_provider_attempts=tuple(attempts),
    )


def _trace_pair(
    tmp_path,
    *,
    compared_worker=3,
    compared_correct=True,
    compared_ms=200,
    repeat=0,
):
    suffix = "" if repeat == 0 else f":r{repeat}"
    baseline = _direct_row(
        tmp_path,
        root_id=f"trace:w1{suffix}",
        worker=1,
        evidence_class="real_model_trace_protocol_run",
        repeat=repeat,
        sample=repeat,
    )
    compared = _direct_row(
        tmp_path,
        root_id=f"trace:w{compared_worker}{suffix}",
        worker=compared_worker,
        evidence_class="real_model_trace_protocol_run",
        correct=compared_correct,
        repeat=repeat,
        sample=repeat,
    )
    return (
        _trace(baseline, makespan=600),
        _trace(compared, makespan=compared_ms),
    )


def test_speedup_requires_same_case_repeat_success_positive_times(tmp_path) -> None:
    rows = _trace_pair(tmp_path)
    repeat_one = _trace_pair(tmp_path, compared_ms=300, repeat=1)
    projection = build_exp2_trace_observations(
        rows + repeat_one, load_paper_metric_contract()
    )

    assert len(projection.pair_rows) == 2
    pair = projection.pair_rows[0]
    assert pair.pair_identity == (
        rows[0].case_record_digest,
        0,
        0,
        1,
        3,
    )
    assert pair.require_cell("trace_replay_paired_speedup").value == Decimal(3)
    summary = projection.condition_summary_rows[0]
    assert summary.require_cell("trace_replay_paired_speedup_repeat_min").value == 2
    assert summary.require_cell("trace_replay_paired_speedup_repeat_max").value == 3
    assert summary.require_cell("trace_replay_paired_speedup_relative_difference").value == Decimal("0.5")


def test_pair_and_condition_summary_carry_exact_trace_lineage_aliases(tmp_path) -> None:
    roots = tuple(
        replace(
            root,
            trace_consumptions=tuple(
                replace(
                    value,
                    source_bank_entry_id=f"entry:{value.consumption_id}",
                )
                for value in root.trace_consumptions or ()
            ),
        )
        for root in (
            *_trace_pair(tmp_path),
            *_trace_pair(tmp_path, compared_ms=300, repeat=1),
        )
    )
    contract = load_paper_metric_contract()

    with capture_metric_computation_traces() as traces:
        build_exp2_trace_observations(roots, contract)

    pair_trace = next(
        value
        for value in traces
        if value.row_kind == "paired_worker_comparison"
        and value.metric_id == "trace_replay_paired_speedup"
        and value.bundle.member_facts_by_id[next(
            member_id
            for member_id in value.bundle.member_ids
            if value.bundle.member_facts_by_id[member_id].get("member_kind")
            == "exp2_preregistered_pair"
        )]["baseline_repeat_id"]
        == 0
    )
    pair_fact = next(
        facts
        for facts in pair_trace.bundle.member_facts_by_id.values()
        if facts.get("member_kind") == "exp2_preregistered_pair"
    )
    assert pair_fact["lineage_root_run_ids"] == ("trace:w1", "trace:w3")
    assert pair_fact["source_bank_entry_ids"] == (
        "entry:consumption:trace:w1",
        "entry:consumption:trace:w3",
    )

    condition_trace = next(
        value
        for value in traces
        if value.row_kind == "condition_summary"
        and value.metric_id == "trace_replay_paired_speedup_relative_difference"
    )
    repeat_facts = tuple(
        facts
        for facts in condition_trace.bundle.member_facts_by_id.values()
        if facts.get("member_kind") == "exp2_repeat_speedup_summary"
    )
    assert {value["repeat_speedup_value"] for value in repeat_facts} == {
        Decimal(2),
        Decimal(3),
    }
    assert {frozenset(value["lineage_root_run_ids"]) for value in repeat_facts} == {
        frozenset(("trace:w1", "trace:w3")),
        frozenset(("trace:w1:r1", "trace:w3:r1")),
    }
    assert {frozenset(value["source_bank_entry_ids"]) for value in repeat_facts} == {
        frozenset((
            "entry:consumption:trace:w1",
            "entry:consumption:trace:w3",
        )),
        frozenset((
            "entry:consumption:trace:w1:r1",
            "entry:consumption:trace:w3:r1",
        )),
    }


def test_failed_fast_root_remains_and_speedup_is_null(tmp_path) -> None:
    rows = _trace_pair(tmp_path, compared_correct=False, compared_ms=1)
    with capture_metric_computation_traces() as traces:
        projection = build_exp2_trace_observations(rows, load_paper_metric_contract())

    assert {
        root_id
        for row in projection.worker_repeat_rows
        for root_id in row.preregistered_root_run_ids
    } == {
        "trace:w1",
        "trace:w3",
    }
    excluded_speedup = projection.pair_rows[0].require_cell(
        "trace_replay_paired_speedup"
    )
    assert excluded_speedup.value is None
    assert excluded_speedup.reason == "membership_excluded"
    assert excluded_speedup.publish_blocked is False
    summary = projection.repeat_summary_rows[0]
    assert summary.require_cell("paired_speedup_planned_pair_count").value == 1
    assert summary.require_cell("paired_speedup_eligible_pair_count").value == 0
    assert summary.require_cell("paired_speedup_ineligible_pair_count").value == 1
    condition = projection.condition_summary_rows[0]
    assert all(cell.value is None for cell in condition.cells)
    assert all(cell.reason == "membership_excluded" for cell in condition.cells)
    assert all(cell.publish_blocked is False for cell in condition.cells)
    condition_trace = next(
        trace for trace in traces if trace.row_kind == "condition_summary"
    )
    condition_facts = tuple(condition_trace.bundle.member_facts_by_id.values())
    assert all(
        facts.get("member_kind") != "exp2_repeat_speedup_summary"
        for facts in condition_facts
    )
    exclusion = next(
        facts
        for facts in condition_facts
        if facts.get("member_kind") == "exp2_repeat_speedup_exclusion"
    )
    assert exclusion["closed_exclusion_reason"] == "membership_excluded"
    assert "repeat_speedup_value" not in exclusion
    assert set(exclusion["lineage_root_run_ids"]) == {"trace:w1", "trace:w3"}
    assert {
        facts["preregistered_root_run_id"]
        for facts in condition_facts
        if facts.get("member_kind") == "preregistered_root"
    } == {"trace:w1", "trace:w3"}

    invalid = _direct_row(
        tmp_path,
        root_id="trace:infra-invalid",
        worker=1,
        evidence_class="real_model_trace_protocol_run",
        verdict=False,
    )
    invalid_row = build_exp2_trace_observations(
        (_trace(invalid, makespan=10),), load_paper_metric_contract()
    ).worker_repeat_rows[0]
    assert all(cell.value is None and cell.publish_blocked for cell in invalid_row.cells)
    assert all(
        cell.audit_denominator_member_ids == ("trace:infra-invalid",)
        for cell in invalid_row.cells
    )


def test_exact_zero_persisted_trace_duration_is_unblocked_null_utilization(
    tmp_path,
) -> None:
    direct = _direct_row(
        tmp_path,
        root_id="trace:zero-duration",
        worker=3,
        evidence_class="real_model_trace_protocol_run",
    )
    row = build_exp2_trace_observations(
        (
            _trace(
                direct,
                makespan=0,
                units=tuple(
                    replace(unit, busy_worker_time_ms=0)
                    for unit in _units("zero-duration", planned=2, executed=2)
                ),
            ),
        ),
        load_paper_metric_contract(),
    ).worker_repeat_rows[0]

    assert row.require_cell("trace_replay_wall_clock_ms").value == 0
    utilization = row.require_cell("worker_utilization")
    assert utilization.value is None
    assert utilization.reason == "zero_denominator"
    assert utilization.publish_blocked is False


def test_zero_elapsed_with_positive_busy_time_blocks_inconsistent_utilization(
    tmp_path,
) -> None:
    direct = _direct_row(
        tmp_path,
        root_id="trace:inconsistent-worker-time",
        worker=3,
        evidence_class="real_model_trace_protocol_run",
    )
    row = build_exp2_trace_observations(
        (
            _trace(
                direct,
                makespan=0,
                units=_units("inconsistent-worker-time", planned=2, executed=2),
            ),
        ),
        load_paper_metric_contract(),
    ).worker_repeat_rows[0]

    assert row.require_cell("trace_replay_wall_clock_ms").value == 0
    utilization = row.require_cell("worker_utilization")
    assert utilization.value is None
    assert utilization.reason == "inconsistent_worker_time_evidence"
    assert utilization.publish_blocked is True


def test_blocked_repeat_speedup_remains_blocked_in_condition_summary(tmp_path) -> None:
    baseline, _ = _trace_pair(tmp_path)
    invalid = _direct_row(
        tmp_path,
        root_id="trace:blocked-condition",
        worker=3,
        evidence_class="real_model_trace_protocol_run",
        verdict=False,
    )
    with capture_metric_computation_traces() as traces:
        projection = build_exp2_trace_observations(
            (baseline, _trace(invalid, makespan=200)),
            load_paper_metric_contract(),
        )

    repeat_median = projection.repeat_summary_rows[0].require_cell(
        "trace_replay_paired_speedup_median"
    )
    assert repeat_median.value is None and repeat_median.publish_blocked
    assert all(
        cell.value is None and cell.publish_blocked
        for cell in projection.condition_summary_rows[0].cells
    )
    condition_trace = next(
        trace for trace in traces if trace.row_kind == "condition_summary"
    )
    blocked_summary = next(
        facts
        for facts in condition_trace.bundle.member_facts_by_id.values()
        if facts.get("member_kind") == "exp2_repeat_speedup_summary"
    )
    assert "repeat_speedup_value" not in blocked_summary
    assert blocked_summary["upstream_blocked_reason"] == repeat_median.reason
    assert set(blocked_summary["lineage_root_run_ids"]) == {
        "trace:w1",
        "trace:blocked-condition",
    }


def test_hard50_strata_come_only_from_digest_bound_case_refs(tmp_path) -> None:
    direct = _direct_row(
        tmp_path,
        root_id="trace:stratum",
        worker=1,
        evidence_class="real_model_trace_protocol_run",
        case_id="misleading_late_999",
        stratum="early",
        quantile="front",
        started=False,
    )
    row = build_exp2_trace_observations(
        (_trace(direct, makespan=10, consumptions=(), units=()),),
        load_paper_metric_contract(),
    ).worker_repeat_rows[0]

    assert row.position_stratum == "early"
    assert row.factor_position_quantiles == ("front",)
    assert row.case_record_digests == (
        direct.preregistered_case_ref["case_record_digest"],
    )


def test_hard50_selection_reordering_does_not_change_quantile_or_position_stratum(
    tmp_path,
) -> None:
    left = _direct_row(
        tmp_path,
        root_id="trace:left",
        worker=1,
        evidence_class="real_model_trace_protocol_run",
        case_id="z-case",
        stratum="middle",
        quantile="center",
        started=False,
    )
    right = _direct_row(
        tmp_path,
        root_id="trace:right",
        worker=1,
        evidence_class="real_model_trace_protocol_run",
        case_id="a-case",
        stratum="middle",
        quantile="center",
        started=False,
    )
    contract = load_paper_metric_contract()
    forward = build_exp2_trace_observations(
        (_trace(left, makespan=10, consumptions=(), units=()), _trace(right, makespan=10, consumptions=(), units=())),
        contract,
    )
    reverse = build_exp2_trace_observations(
        tuple(reversed((_trace(left, makespan=10, consumptions=(), units=()), _trace(right, makespan=10, consumptions=(), units=())))),
        contract,
    )

    assert [
        (row.position_stratum, row.factor_position_quantiles)
        for row in forward.worker_repeat_rows
    ] == [
        (row.position_stratum, row.factor_position_quantiles)
        for row in reverse.worker_repeat_rows
    ]


def test_case_id_shape_cannot_influence_stratum(tmp_path) -> None:
    values = tuple(
        _trace(
            _direct_row(
                tmp_path,
                root_id=f"trace:opaque:{index}",
                worker=1,
                evidence_class="real_model_trace_protocol_run",
                case_id=case_id,
                stratum="no_factor",
                quantile="tail",
                started=False,
            ),
            makespan=10,
            consumptions=(),
            units=(),
        )
        for index, case_id in enumerate(("early_000", "late_999", "no_digits"))
    )

    result = build_exp2_trace_observations(values, load_paper_metric_contract())

    assert {
        row.position_stratum for row in result.worker_repeat_rows
    } == {"no_factor"}


def test_logical_makespan_drives_trace_wallclock(tmp_path) -> None:
    direct = _direct_row(
        tmp_path,
        root_id="trace:clock",
        worker=1,
        evidence_class="real_model_trace_protocol_run",
    )
    row = build_exp2_trace_observations(
        (_trace(direct, makespan=600),),
        load_paper_metric_contract(),
    ).worker_repeat_rows[0]

    assert row.require_cell("trace_replay_wall_clock_ms").value == 600
    assert "provider_latency_ms" not in vars(row)
    assert "cpu_duration_ms" not in vars(row)


def test_absolute_bank_slot_consumption_trace_attributed_tokens_and_cost(tmp_path) -> None:
    direct = _direct_row(
        tmp_path,
        root_id="trace:resource",
        worker=1,
        evidence_class="real_model_trace_protocol_run",
    )
    row = build_exp2_trace_observations(
        (
            _trace(
                direct,
                makespan=600,
                consumptions=(
                    _consumption("c1", tokens=100, cost=Decimal("1.25")),
                    _consumption("c2", tokens=50, cost=Decimal("0.75")),
                    _consumption("staged", committed=False, tokens=999, cost=Decimal("9")),
                ),
            ),
        ),
        load_paper_metric_contract(),
    ).worker_repeat_rows[0]

    assert row.require_cell("bank_slot_consumption").value == 2
    assert row.require_cell("trace_attributed_tokens").value == 150
    assert row.require_cell("trace_attributed_cost").value == Decimal(2)

    missing_roles = Exp2TraceConsumptionFacts(
        consumption_id="missing-roles",
        committed=True,
        source_total_tokens=1,
        source_cost_estimate_cny=Decimal("0.1"),
        source_bank_roles=None,
    )
    blocked = build_exp2_trace_observations(
        (_trace(direct, makespan=600, consumptions=(missing_roles,)),),
        load_paper_metric_contract(),
    ).worker_repeat_rows[0]
    assert blocked.require_cell("trace_attributed_tokens").value is None
    assert blocked.require_cell("trace_attributed_tokens").publish_blocked is True


def test_paired_trace_token_and_cost_multipliers_use_w1_denominators(tmp_path) -> None:
    baseline, compared = _trace_pair(tmp_path)
    baseline = _trace(
        baseline.direct_result,
        makespan=600,
        consumptions=(_consumption("w1", tokens=100, cost=Decimal("2")),),
    )
    compared = _trace(
        compared.direct_result,
        makespan=200,
        consumptions=(_consumption("wk", tokens=250, cost=Decimal("3")),),
    )
    pair = build_exp2_trace_observations(
        (baseline, compared), load_paper_metric_contract()
    ).pair_rows[0]

    assert pair.require_cell("paired_trace_token_multiplier").value == Decimal("2.5")
    assert pair.require_cell("paired_trace_cost_multiplier").value == Decimal("1.5")


def test_pair_nullable_resources_are_not_zero_filled_and_keep_denominator(
    tmp_path,
) -> None:
    baseline, compared = _trace_pair(tmp_path)
    compared = _trace(
        compared.direct_result,
        makespan=200,
        consumptions=(
            Exp2TraceConsumptionFacts(
                consumption_id="missing-provider-usage",
                committed=True,
                source_total_tokens=None,
                source_cost_estimate_cny=None,
                source_bank_roles=TRACE_SOURCE_BANK_ROLES,
            ),
        ),
    )

    pair_input = paper_exp2_metrics._build_pair(
        load_paper_metric_contract(), baseline, compared
    )
    facts = pair_input.facts
    assert facts["compared_resource_total_unknown"] is True
    assert facts["compared_trace_attributed_tokens_known_total"] == 0
    assert facts["compared_trace_attributed_tokens_missing_attempt_count"] == 1
    assert facts["compared_trace_attributed_cost_known_total"] == Decimal(0)
    assert facts["compared_trace_attributed_cost_missing_attempt_count"] == 1
    assert "compared_trace_attributed_tokens" not in facts
    assert "compared_trace_attributed_cost" not in facts

    projection = build_exp2_trace_observations(
        (baseline, compared), load_paper_metric_contract()
    )
    assert len(projection.pair_rows) == 1
    pair = projection.pair_rows[0]
    token_cell = pair.require_cell("paired_trace_token_multiplier")
    cost_cell = pair.require_cell("paired_trace_cost_multiplier")
    assert token_cell.value is None
    assert cost_cell.value is None
    assert token_cell.paper_eligible is False
    assert cost_cell.paper_eligible is False
    assert set(token_cell.audit_denominator_member_ids) == {
        baseline.direct_result.preregistered_root_run_id,
        compared.direct_result.preregistered_root_run_id,
    }
    assert set(cost_cell.audit_denominator_member_ids) == set(
        token_cell.audit_denominator_member_ids
    )


def test_trace_parallel_efficiency(tmp_path) -> None:
    pair = build_exp2_trace_observations(
        _trace_pair(tmp_path, compared_worker=3), load_paper_metric_contract()
    ).pair_rows[0]

    assert pair.require_cell("trace_replay_paired_speedup").value == Decimal(3)
    assert pair.require_cell("trace_replay_parallel_efficiency").value == Decimal(1)


def test_planned_executed_unscheduled_inflight_peak_utilization(tmp_path) -> None:
    direct = _direct_row(
        tmp_path,
        root_id="trace:schedule",
        worker=3,
        evidence_class="real_model_trace_protocol_run",
    )
    row = build_exp2_trace_observations(
        (
            _trace(
                direct,
                makespan=200,
                units=_units("schedule", planned=3, executed=2),
                inflight=1,
                peak=2,
            ),
        ),
        load_paper_metric_contract(),
    ).worker_repeat_rows[0]

    assert row.require_cell("planned_ai_unit_count").value == 3
    assert row.require_cell("executed_ai_unit_count").value == 2
    assert row.require_cell("unscheduled_ai_unit_count").value == 1
    assert row.require_cell("in_flight_at_witness").value == 1
    assert row.require_cell("observed_peak_concurrency").value == 2
    assert row.require_cell("worker_utilization").value == Decimal(1) / Decimal(3)


def test_online_429_timeout_union_uses_unique_first_attempts_per_worker(tmp_path) -> None:
    left = _direct_row(
        tmp_path,
        root_id="online:left",
        worker=3,
        evidence_class="online_real_provider",
    )
    right = _direct_row(
        tmp_path,
        root_id="online:right",
        worker=3,
        evidence_class="online_real_provider",
        case_id="right-case",
    )
    rows = (
        _online(left, attempts=(_attempt("a", provider_429=True), _attempt("b"))),
        _online(
            right,
            dispatch=150,
            terminal=500,
            attempts=(
                _attempt("a", provider_timeout=True),
                _attempt("c", provider_429=True, provider_timeout=True),
                _attempt("d", tokens=None, cost=None, latency=None),
            ),
        ),
    )
    row = build_exp2_online_observations(
        rows, load_paper_metric_contract()
    ).rows[0]

    assert row.require_cell("actual_first_provider_attempt_count").value == 4
    assert row.require_cell("provider_429_or_timeout_union_count").value == 2
    assert row.require_cell("provider_429_or_timeout_union_fraction").value == Decimal("0.5")
    assert row.require_cell("actual_end_to_end_wall_clock_ms").value == 400
    assert row.require_cell("actual_total_tokens").value is None
    assert row.require_cell("actual_cost_estimate_cny").value is None
    assert row.require_cell("actual_provider_latency_ms").value is None


def test_online_trace_intersection_and_severe_rule_are_post_bank_only(tmp_path) -> None:
    online_roots = []
    trace_roots = []
    for index, case_id in enumerate(("case-a", "case-b", "case-c")):
        for worker, online_ms, trace_ms in ((1, 600, 600), (3, 200, 200)):
            online_direct = _direct_row(
                tmp_path,
                root_id=f"online:{case_id}:w{worker}",
                worker=worker,
                evidence_class="online_real_provider",
                case_id=case_id,
            )
            trace_direct = _direct_row(
                tmp_path,
                root_id=f"trace:{case_id}:w{worker}",
                worker=worker,
                evidence_class="real_model_trace_protocol_run",
                case_id=case_id,
            )
            online_roots.append(
                _online(
                    online_direct,
                    dispatch=index * 1000,
                    terminal=index * 1000 + online_ms,
                    attempts=(_attempt(f"online-attempt:{case_id}:w{worker}"),),
                )
            )
            trace_roots.append(_trace(trace_direct, makespan=trace_ms))
    contract = load_paper_metric_contract()
    before = build_exp2_online_observations(tuple(online_roots), contract)
    trace = build_exp2_trace_observations(tuple(trace_roots), contract)
    after = build_exp2_online_observations(
        tuple(online_roots),
        contract,
        complete_main_trace=trace,
        main_trace_complete=True,
    )

    assert before.post_bank_check.status == "not_evaluated_pre_bank"
    assert after.post_bank_check.same_case_intersection_count_by_worker[3] == 3
    assert after.post_bank_check.severe_worker_counts == ()


def test_no_throughput_or_efficiency_alias() -> None:
    contract = load_paper_metric_contract()
    assert len(EXP2_TRACE_NUMERIC_FIELDS) == 26
    assert len(EXP2_ONLINE_NUMERIC_FIELDS) == 12
    assert contract.require_table("exp2_trace_scalability").numeric_output_fields == EXP2_TRACE_NUMERIC_FIELDS
    assert contract.require_table("exp2_online_concurrency").numeric_output_fields == EXP2_ONLINE_NUMERIC_FIELDS
    assert "trace_replay_parallel_efficiency" in EXP2_TRACE_NUMERIC_FIELDS
    assert not {
        "throughput",
        "throughput_roots_per_second",
        "efficiency",
    } & set(EXP2_TRACE_NUMERIC_FIELDS)


def test_trace_worker_repeat_scope_groups_all_roots_with_fixed_denominator(
    tmp_path,
) -> None:
    direct_rows = (
        _direct_row(
            tmp_path,
            root_id="trace:scope:success",
            worker=3,
            evidence_class="real_model_trace_protocol_run",
            case_id="scope-success",
        ),
        _direct_row(
            tmp_path,
            root_id="trace:scope:failed",
            worker=3,
            evidence_class="real_model_trace_protocol_run",
            case_id="scope-failed",
            correct=False,
        ),
        _direct_row(
            tmp_path,
            root_id="trace:scope:not-started",
            worker=3,
            evidence_class="real_model_trace_protocol_run",
            case_id="scope-not-started",
            started=False,
        ),
    )
    projection = build_exp2_trace_observations(
        tuple(
            _trace(row, makespan=makespan)
            for row, makespan in zip(direct_rows, (600, 1, 50), strict=True)
        ),
        load_paper_metric_contract(),
    )

    assert len(projection.worker_repeat_rows) == 1
    row = projection.worker_repeat_rows[0]
    assert row.worker_count == 3
    assert row.repeat_id == 0
    assert row.position_stratum == "early"
    assert row.preregistered_root_run_ids == (
        "trace:scope:failed",
        "trace:scope:not-started",
        "trace:scope:success",
    )
    assert row.require_cell("preregistered_root_count").value == 3
    assert row.require_cell("final_result_root_count").value == 2
    assert row.require_cell("verified_correct_root_count").value == 1
    assert row.require_cell("completion_rate").value == Decimal(2) / Decimal(3)
    assert row.require_cell("end_to_end_verified_success_rate").value == Decimal(1) / Decimal(3)


def test_online_worker_case_position_repeat_scope_keeps_four_positions_separate(
    tmp_path,
) -> None:
    roots = []
    positions = ("early", "middle", "late", "no_factor")
    for index, position in enumerate(positions):
        direct = _direct_row(
            tmp_path,
            root_id=f"online:position:{position}",
            worker=3,
            evidence_class="online_real_provider",
            case_id=f"position-{position}",
            stratum=position,
            quantile=f"q{index}",
        )
        roots.append(
            _online(
                direct,
                dispatch=1000 * index,
                terminal=1000 * index + 100 + index,
                attempts=(
                    _attempt(
                        f"attempt:{position}",
                        provider_429=position == "early",
                        tokens=10 + index,
                    ),
                ),
            )
        )

    projection = build_exp2_online_observations(
        tuple(roots), load_paper_metric_contract()
    )

    assert [row.case_position for row in projection.rows] == list(positions)
    for index, row in enumerate(projection.rows):
        assert row.worker_count == 3
        assert row.repeat_id == 0
        assert row.require_cell("preregistered_root_count").value == 1
        assert row.require_cell("actual_first_provider_attempt_count").value == 1
        assert row.require_cell("actual_total_tokens").value == 10 + index
        assert row.require_cell("actual_end_to_end_wall_clock_ms").value == 100 + index
        assert row.require_cell("provider_429_or_timeout_union_count").value == (
            1 if row.case_position == "early" else 0
        )


def test_inflight_witness_rejects_impossible_state_and_accepts_boundary(
    tmp_path,
) -> None:
    direct = _direct_row(
        tmp_path,
        root_id="trace:inflight-bound",
        worker=3,
        evidence_class="real_model_trace_protocol_run",
    )
    units = _units("inflight", planned=20, executed=20)

    with pytest.raises(ValueError, match="in-flight witness exceeds"):
        _trace(direct, makespan=1000, units=units, inflight=20, peak=2)

    boundary = _trace(
        direct,
        makespan=1000,
        units=units,
        inflight=2,
        peak=2,
    )
    assert boundary.in_flight_at_witness == 2
    assert boundary.observed_peak_concurrency == 2
