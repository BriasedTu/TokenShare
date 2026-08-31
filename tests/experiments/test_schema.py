from __future__ import annotations

from dataclasses import asdict, replace
import json
from pathlib import Path

import pytest

from tokenshare.experiments.schema import (
    ACTUAL_PROVIDER_FIELDS,
    ROOT_RESULT_SCHEMA_VERSION,
    SIMULATED_RESOURCE_FIELDS,
    SOURCE_TRACE_FIELDS,
    AttemptResultV1,
    FaultObservationV1,
    RootResultV2,
    SchemaValidationError,
    ExperimentRunConfigV1,
    UnitTraceV1,
    WorkerExecutionFactV1,
    require_null_reason,
)


CONTRACT_PATH = Path(__file__).parent / "fixtures" / "authority_contract.v1.json"


def _contract() -> dict[str, object]:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def _nullable_reasons() -> dict[str, str]:
    return {
        path: "authority field not observed in this fixture"
        for path in _contract()["field_rules"]["nullable_with_reason"]
    }


def _source_attempt(**changes: object) -> AttemptResultV1:
    attempt = AttemptResultV1(
        attempt_id="attempt-0",
        unit_id="unit-0",
        planned_ai_unit_id="range_0",
        attempt_ordinal=0,
        trace_origin="protocol",
        result_kind="fixed_trace",
        provider_call_made=False,
        raw_response_present=True,
        raw_response_relative_path="responses/attempt-0.json",
        canonical_accepted=False,
        usage_status="not_applicable",
        source_response_slot_id="case-0:0:range_0:0",
        source_response_consumed=True,
        source_attempt_ordinal=0,
        source_attempt_fallback_used=False,
        source_trace_origin="protocol",
        source_result_kind="success",
        source_case_id="case-0",
        source_repeat_id=0,
        source_planned_ai_unit_id="range_0",
        source_unit_candidate_start=2,
        source_unit_candidate_end=10,
        source_latency_ms=25,
        source_total_tokens=100,
        source_cost_estimate_cny=0.01,
        call_state="not_started",
        missing_reason=_nullable_reasons(),
    )
    return replace(attempt, **changes)


def _exp3_attempt(**changes: object) -> AttemptResultV1:
    attempt = replace(
        _source_attempt(),
        source_prompt_tokens=20,
        source_prompt_cache_hit_tokens=0,
        source_prompt_cache_miss_tokens=20,
        source_completion_tokens=80,
        source_reasoning_tokens=40,
        source_pricing_version="slim_v2.pricing.2026-08-20",
        source_pricing_tier="off_peak",
        simulated_total_tokens=105,
        simulated_latency_ms=26,
        token_perturbation_factor=0.05,
        network_perturbation_factor=-0.02,
        perturbation_seed=20260820,
        perturbation_version="slim_v2.exp3_perturbation.v1",
    )
    return replace(attempt, **changes)


def _actual_attempt(**changes: object) -> AttemptResultV1:
    attempt = AttemptResultV1(
        attempt_id="attempt-0",
        unit_id="unit-0",
        planned_ai_unit_id="range_0",
        attempt_ordinal=0,
        trace_origin="protocol",
        started_at_ms=11,
        ended_at_ms=18,
        result_kind="success",
        provider_call_made=True,
        http_status=200,
        provider_latency_ms=7,
        raw_response_present=True,
        raw_response_relative_path="responses/attempt-0.json",
        parse_result="accepted",
        verifier_result="accepted",
        canonical_accepted=True,
        prompt_tokens=20,
        prompt_cache_hit_tokens=0,
        prompt_cache_miss_tokens=20,
        completion_tokens=80,
        reasoning_tokens=40,
        total_tokens=100,
        provider_request_started_at_utc="2026-08-21T01:00:00Z",
        pricing_version="slim_v2.pricing.2026-08-20",
        pricing_tier="off_peak",
        cost_estimate_cny=0.01,
        usage_status="complete",
        call_state="terminal",
        missing_reason=_nullable_reasons(),
    )
    return replace(attempt, **changes)


def _predispatch_attempt(**changes: object) -> AttemptResultV1:
    attempt = AttemptResultV1(
        attempt_id="attempt-preflight",
        unit_id="unit-0",
        planned_ai_unit_id="range_0",
        attempt_ordinal=0,
        trace_origin="protocol",
        result_kind="pre_dispatch_failure",
        provider_call_made=False,
        raw_response_present=False,
        canonical_accepted=False,
        usage_status="not_available",
        call_state="not_started",
        missing_reason=_nullable_reasons(),
    )
    return replace(attempt, **changes)


def _valid_trace(attempt: AttemptResultV1 | None = None) -> UnitTraceV1:
    return UnitTraceV1(
        case_id="case-0",
        planned_ai_unit_id="range_0",
        domain="factorization",
        trace_origin="protocol",
        candidate_start=2,
        candidate_end=10,
        provider_family="deepseek",
        provider_entry_id="deepseek_v4_pro_exp1_baseline",
        configured_model="deepseek-v4-pro",
        requested_model="deepseek-v4-pro",
        resolved_model="deepseek-v4-pro",
        attempts=[attempt or _actual_attempt()],
    )


def _valid_root(
    *,
    experiment_id: str = "exp1",
    attempts: list[AttemptResultV1] | None = None,
) -> RootResultV2:
    is_exp1 = experiment_id == "exp1"
    is_exp2 = experiment_id == "exp2"
    is_exp4 = experiment_id == "exp4"
    return RootResultV2(
        experiment_id=experiment_id,
        condition_id=f"{experiment_id}-condition",
        case_id="case-0",
        repeat_id=0,
        domain="factorization",
        difficulty="hard",
        topic_family=None,
        position_stratum="early" if is_exp2 else None,
        mode="FULL" if is_exp4 else None,
        disabled_mechanisms=[],
        worker_count=10,
        fault_type=None,
        fault_rate=None,
        dead_worker_count=None,
        kill_progress_target_ratio=None,
        challenge_plan_id="challenge-0" if is_exp4 else None,
        challenge_family="invalid_candidate" if is_exp4 else None,
        challenge_target_planned_ai_unit_ids=["range_0"] if is_exp4 else None,
        challenge_attempt_ordinal_rule="ordinal_0" if is_exp4 else None,
        provider_family="deepseek",
        provider_entry_id="deepseek_v4_pro_exp1_baseline",
        configured_model="deepseek-v4-pro",
        requested_model="deepseek-v4-pro",
        resolved_model="deepseek-v4-pro",
        reasoning_mode="enabled",
        root_start_at_ms=10,
        root_terminal_at_ms=20,
        runtime_wall_clock_ms=10,
        trace_tail_started_at_ms=None,
        trace_tail_terminal_at_ms=None,
        trace_tail_wall_clock_ms=0 if is_exp1 else None,
        trace_tail_status="not_needed" if is_exp1 else None,
        trace_tail_target_ai_unit_ids=[] if is_exp1 else None,
        trace_tail_recorded_ai_unit_ids=[] if is_exp1 else None,
        trace_tail_success_unit_count=0 if is_exp1 else None,
        trace_tail_failure_unit_count=0 if is_exp1 else None,
        trace_tail_provider_attempt_count=0 if is_exp1 else None,
        trace_tail_total_tokens=0 if is_exp1 else None,
        trace_tail_cost_estimate_cny=0.0 if is_exp1 else None,
        preflight_status="passed",
        protocol_started=True,
        root_status="completed",
        final_result_present=True,
        verified_correct=True,
        failure_stage=None,
        failure_kind=None,
        planned_ai_unit_ids=["range_0"],
        dispatched_ai_unit_ids=["range_0"],
        completed_ai_unit_ids=["range_0"],
        unscheduled_ai_unit_ids=[],
        in_flight_ai_unit_ids_at_witness=[] if is_exp2 else None,
        observed_peak_concurrency=1 if is_exp2 else None,
        worker_execution_facts=[],
        required_slot_count=None,
        recovered_valid_canonical_slot_count=None,
        attempts=attempts or [],
        fault_target_planned_ai_unit_ids=None,
        fault_target_count=None,
        fault_observations=[],
        recovery_observations=[],
        worker_death_observations=[],
        challenge_observations=[],
        ablation_observations=[],
        missing_reason=_nullable_reasons(),
        not_applicable_reason={},
    )


def _preflight_blocked_root() -> RootResultV2:
    reasons = _nullable_reasons()
    reasons.update(
        {
            "root_start_at_ms": "protocol_lifecycle_not_started",
            "root_terminal_at_ms": "protocol_lifecycle_not_started",
            "runtime_wall_clock_ms": "protocol_lifecycle_not_started",
            "resolved_model": "model_resolution_not_reached",
        }
    )
    return replace(
        _valid_root(experiment_id="exp4"),
        resolved_model=None,
        root_start_at_ms=None,
        root_terminal_at_ms=None,
        runtime_wall_clock_ms=None,
        preflight_status="blocked",
        protocol_started=False,
        root_status="infrastructure_error",
        final_result_present=False,
        verified_correct=False,
        failure_stage="preflight",
        failure_kind="infrastructure_invalid",
        failure_origin="preflight_unavailable",
        missing_reason=reasons,
    )


def test_root_result_normalized_leaf_paths_equal_authority_contract() -> None:
    contract = _contract()
    expected = contract["metric_authority_leaf_paths"]

    assert len(expected) == 187
    assert len(expected) == len(set(expected))
    assert set(RootResultV2.normalized_leaf_paths(authority_only=True)) == set(expected)
    assert len(contract["formal_metric_ids"]) == 153
    assert {
        "resolved_model",
        "root_start_at_ms",
        "root_terminal_at_ms",
        "runtime_wall_clock_ms",
    } <= set(contract["field_rules"]["nullable_with_reason"])

    blocked = _preflight_blocked_root()
    blocked.validate()
    with pytest.raises(SchemaValidationError, match="protocol_lifecycle_not_started"):
        replace(blocked, root_start_at_ms=0).validate()
    with pytest.raises(SchemaValidationError, match="root timing fields"):
        replace(blocked, root_terminal_at_ms=0).validate()
    with pytest.raises(SchemaValidationError, match="model_resolution_not_reached"):
        replace(
            blocked,
            missing_reason={**blocked.missing_reason, "resolved_model": "unknown"},
        ).validate()
    with pytest.raises(SchemaValidationError, match="resolved_model"):
        replace(blocked, final_result_present=True).validate()
    with pytest.raises(SchemaValidationError, match="verified_correct"):
        replace(blocked, verified_correct=True).validate()
    with pytest.raises(SchemaValidationError, match="incorrect_final"):
        replace(blocked, failure_kind="incorrect_final").validate()
    with pytest.raises(SchemaValidationError, match="verified_correct"):
        replace(
            _valid_root(),
            final_result_present=False,
            verified_correct=True,
        ).validate()


def test_failed_root_requires_frozen_kind_and_diagnostic_origin() -> None:
    failed = replace(
        _valid_root(),
        root_status="failed",
        final_result_present=False,
        verified_correct=False,
        failure_stage="candidate_acquisition",
    )
    with pytest.raises(SchemaValidationError, match="failure_kind"):
        replace(failed, failure_kind=None, failure_origin=None).validate()
    with pytest.raises(SchemaValidationError, match="failure_origin"):
        replace(failed, failure_kind="no_final", failure_origin=None).validate()
    with pytest.raises(SchemaValidationError, match="failure_origin"):
        replace(
            _preflight_blocked_root(),
            failure_origin="   ",
        ).validate()


def test_full_schema_leaf_paths_equal_authority_plus_operational_contract() -> None:
    contract = _contract()
    authority = set(contract["metric_authority_leaf_paths"])
    operational = set(contract["slim_operational_leaf_paths"])
    expected = authority | operational

    assert authority.isdisjoint(operational)
    assert set(RootResultV2.normalized_leaf_paths()) == expected

    with pytest.raises(SchemaValidationError, match="precedes"):
        replace(
            _valid_root(),
            worker_execution_facts=[
                WorkerExecutionFactV1(
                    worker_id="worker-0",
                    started_at_ms=20,
                    ended_at_ms=10,
                    result_kind="completed",
                )
            ],
        ).validate()
    with pytest.raises(SchemaValidationError, match="attempt_id"):
        replace(
            _valid_root(experiment_id="exp3"),
            fault_observations=[
                FaultObservationV1(
                    attempt_id="",
                    fault_type="false_positive",
                    target_planned_ai_unit_id="range_0",
                    injected=True,
                )
            ],
        ).validate()


def test_attempt_and_trace_schema_keep_actual_source_and_simulated_fields_distinct() -> None:
    attempt_fields = set(AttemptResultV1.field_names())

    assert ACTUAL_PROVIDER_FIELDS <= attempt_fields
    assert SOURCE_TRACE_FIELDS <= attempt_fields
    assert SIMULATED_RESOURCE_FIELDS <= attempt_fields
    assert ACTUAL_PROVIDER_FIELDS.isdisjoint(SOURCE_TRACE_FIELDS)
    assert ACTUAL_PROVIDER_FIELDS.isdisjoint(SIMULATED_RESOURCE_FIELDS)
    assert SOURCE_TRACE_FIELDS.isdisjoint(SIMULATED_RESOURCE_FIELDS)
    assert {
        "attempts[].total_tokens",
        "attempts[].source_total_tokens",
        "attempts[].simulated_total_tokens",
        "attempts[].provider_latency_ms",
        "attempts[].source_latency_ms",
        "attempts[].simulated_latency_ms",
    } <= set(UnitTraceV1.normalized_leaf_paths())

    invalid_attempt = replace(_actual_attempt(), attempt_id="")
    with pytest.raises(SchemaValidationError, match="attempt_id"):
        _valid_trace(invalid_attempt).validate()
    with pytest.raises(SchemaValidationError, match="RFC3339"):
        replace(
            _source_attempt(),
            provider_request_started_at_utc="2026-08-21",
        ).validate()
    with pytest.raises(SchemaValidationError, match="UTC"):
        replace(
            _source_attempt(),
            provider_request_started_at_utc="2026-08-21T09:00:00+08:00",
        ).validate()
    with pytest.raises(SchemaValidationError, match="call_state"):
        replace(_source_attempt(), call_state="in_flight").validate_provider_mode(
            "exp2"
        )
    with pytest.raises(SchemaValidationError, match="simulated_total_tokens"):
        replace(_source_attempt(), simulated_total_tokens=101).validate_provider_mode(
            "exp2"
        )
    with pytest.raises(SchemaValidationError, match="simulated_total_tokens"):
        replace(_exp3_attempt(), simulated_total_tokens=None).validate_provider_mode(
            "exp3"
        )
    with pytest.raises(SchemaValidationError, match="source_response_slot_id"):
        _valid_trace(_source_attempt()).validate()
    with pytest.raises(SchemaValidationError, match="contiguous"):
        replace(
            _valid_trace(),
            attempts=[
                _actual_attempt(),
                replace(
                    _actual_attempt(),
                    attempt_id="attempt-2",
                    attempt_ordinal=2,
                ),
            ],
        ).validate()
    with pytest.raises(SchemaValidationError, match="at most 3"):
        replace(
            _valid_trace(),
            attempts=[
                replace(
                    _actual_attempt(),
                    attempt_id=f"attempt-{ordinal}",
                    attempt_ordinal=ordinal,
                )
                for ordinal in range(4)
            ],
        ).validate()

    _valid_root(attempts=[_actual_attempt()]).validate()
    _valid_root(attempts=[_predispatch_attempt()]).validate()
    _valid_root(
        experiment_id="exp5",
        attempts=[_predispatch_attempt()],
    ).validate()
    with pytest.raises(SchemaValidationError, match="simulated_total_tokens"):
        _valid_root(
            experiment_id="exp5",
            attempts=[replace(_actual_attempt(), simulated_total_tokens=1)],
        ).validate()


def test_nullable_fields_require_missing_or_not_applicable_reason() -> None:
    contract = _contract()
    expected_required = set(contract["field_rules"]["required"])
    expected_nullable = set(contract["field_rules"]["nullable_with_reason"])

    assert set(RootResultV2.required_leaf_paths()) == expected_required
    assert set(RootResultV2.nullable_leaf_paths()) == expected_nullable
    with pytest.raises(SchemaValidationError, match="provider_latency_ms"):
        require_null_reason("attempts[].provider_latency_ms", None, {}, {})

    require_null_reason(
        "attempts[].provider_latency_ms",
        None,
        {"attempts[].provider_latency_ms": "provider omitted latency"},
        {},
    )
    require_null_reason(
        "attempts[].provider_latency_ms",
        None,
        {},
        {"attempts[].provider_latency_ms": "fixed trace has no actual provider call"},
    )

    invalid_attempt = _source_attempt()
    invalid_attempt.missing_reason.pop("attempts[].checker_result")
    with pytest.raises(SchemaValidationError, match="checker_result"):
        invalid_attempt.validate()

    assert ExperimentRunConfigV1.field_names() == (
        "schema_version",
        "run_id",
        "profile_id",
        "experiment_ids",
        "source_run_dir",
        "exp1_provider_config_path",
        "exp5_provider_config_path",
        "local_secret_config_path",
        "pricing_versions",
        "ordinary_parallel_backend_kind",
        "response_max_bytes",
        "reducer_workers",
    )
    valid_config = ExperimentRunConfigV1(
        run_id="run-0",
        profile_id="representative",
        experiment_ids=["exp1", "exp3", "exp5"],
        source_run_dir="source-exp1",
        ordinary_parallel_backend_kind="thread",
    )
    valid_config.validate()
    assert valid_config.pricing_versions == {
        "exp1": "slim_v2.pricing.2026-08-23",
        "exp5": "slim_v2.pricing.2026-08-20",
    }
    with pytest.raises(SchemaValidationError, match="subsequence"):
        replace(valid_config, experiment_ids=["exp3", "exp1"]).validate()
    for field_name, invalid_value in (
        ("pricing_versions", {"exp1": "slim_v2.pricing.2026-08-20"}),
        ("ordinary_parallel_backend_kind", "process"),
        ("response_max_bytes", 16 * 1024 * 1024 + 1),
        ("reducer_workers", 2),
    ):
        with pytest.raises(SchemaValidationError, match=field_name):
            replace(valid_config, **{field_name: invalid_value}).validate()
    with pytest.raises(SchemaValidationError, match="source_run_dir"):
        replace(
            valid_config,
            experiment_ids=["exp1", "exp5"],
            source_run_dir="unexpected-source",
        ).validate()
    ExperimentRunConfigV1(
        run_id="run-all",
        profile_id="full",
        experiment_ids=["exp1", "exp2", "exp3", "exp4", "exp5"],
        source_run_dir=None,
        ordinary_parallel_backend_kind="thread",
    ).validate()


def test_exp2_to_exp4_actual_provider_fields_are_null_and_provider_call_is_false() -> None:
    attempt = _source_attempt()

    attempt.validate_provider_mode("exp2")
    _exp3_attempt().validate_provider_mode("exp3")
    attempt.validate_provider_mode("exp4")

    with pytest.raises(SchemaValidationError, match="provider_call_made"):
        replace(attempt, provider_call_made=True).validate_provider_mode("exp2")
    with pytest.raises(SchemaValidationError, match="total_tokens"):
        replace(attempt, total_tokens=1).validate_provider_mode("exp4")
    for missing_field in (
        "source_response_slot_id",
        "source_attempt_ordinal",
        "source_attempt_fallback_used",
        "source_trace_origin",
        "source_result_kind",
        "source_case_id",
        "source_repeat_id",
        "source_planned_ai_unit_id",
    ):
        with pytest.raises(SchemaValidationError, match=missing_field):
            replace(attempt, **{missing_field: None}).validate_provider_mode("exp2")
    with pytest.raises(SchemaValidationError, match="source_response_consumed"):
        replace(attempt, source_response_consumed=False).validate_provider_mode("exp3")
    with pytest.raises(SchemaValidationError, match="source_case_id"):
        _valid_root(
            experiment_id="exp2",
            attempts=[replace(attempt, source_case_id="different-case")],
        ).validate()
    with pytest.raises(SchemaValidationError, match="source_planned_ai_unit_id"):
        _valid_root(
            experiment_id="exp2",
            attempts=[replace(attempt, source_planned_ai_unit_id="other-unit")],
        ).validate()
    with pytest.raises(SchemaValidationError, match="Factorization source semantics"):
        _valid_root(
            experiment_id="exp2",
            attempts=[replace(attempt, source_unit_candidate_start=None)],
        ).validate()


def test_schema_contains_no_forbidden_authority_fields() -> None:
    forbidden_fragments = {
        "budget",
        "receipt",
        "digest",
        "lineage",
        "evidence",
        "publication",
        "paper" + "_eligibility",
        "response" + "_bank",
        "prepared_identity",
        "hard_deadline",
    }
    leaf_paths = RootResultV2.normalized_leaf_paths()

    assert not {
        path
        for path in leaf_paths
        if any(fragment in path.lower() for fragment in forbidden_fragments)
    }
    assert "attempts[].source_response_slot_id" in leaf_paths
    assert "challenge_plan_id" in leaf_paths

    exp1_with_tail = replace(
        _valid_root(),
        trace_tail_started_at_ms=30,
        trace_tail_terminal_at_ms=40,
        trace_tail_wall_clock_ms=10,
        trace_tail_status="completed",
        trace_tail_target_ai_unit_ids=["range_1"],
        trace_tail_recorded_ai_unit_ids=["range_1"],
        trace_tail_success_unit_count=1,
        trace_tail_failure_unit_count=0,
        trace_tail_provider_attempt_count=1,
        trace_tail_total_tokens=50,
        trace_tail_cost_estimate_cny=0.005,
    )
    exp1_with_tail.validate()
    replace(
        exp1_with_tail,
        trace_tail_success_unit_count=0,
        trace_tail_failure_unit_count=1,
        trace_tail_provider_attempt_count=0,
        trace_tail_total_tokens=0,
        trace_tail_cost_estimate_cny=0.0,
    ).validate()
    with pytest.raises(SchemaValidationError, match="retry cap"):
        replace(exp1_with_tail, trace_tail_provider_attempt_count=4).validate()
    with pytest.raises(SchemaValidationError, match="recorded"):
        replace(exp1_with_tail, trace_tail_recorded_ai_unit_ids=[]).validate()
    with pytest.raises(SchemaValidationError, match="success.*failure"):
        replace(exp1_with_tail, trace_tail_success_unit_count=0).validate()
    with pytest.raises(SchemaValidationError, match="tail fields"):
        replace(_valid_root(), trace_tail_total_tokens=None).validate()

    non_tail_exp1 = replace(
        _valid_root(),
        trace_tail_status="not_required_by_downstream",
        unscheduled_ai_unit_ids=["range_1"],
    )
    non_tail_exp1.validate()
    with pytest.raises(SchemaValidationError, match="not-required"):
        replace(
            non_tail_exp1,
            trace_tail_target_ai_unit_ids=["range_1"],
        ).validate()

    exp4 = _valid_root(experiment_id="exp4")
    exp4.validate()
    with pytest.raises(SchemaValidationError, match="challenge_plan_id"):
        replace(exp4, challenge_plan_id=None).validate()
    with pytest.raises(SchemaValidationError, match="challenge target"):
        replace(exp4, challenge_target_planned_ai_unit_ids=[]).validate()
    with pytest.raises(SchemaValidationError, match="fault_type"):
        replace(exp4, fault_type="false_positive").validate()
    with pytest.raises(SchemaValidationError, match="fault observations"):
        replace(
            exp4,
            fault_observations=[
                FaultObservationV1(
                    attempt_id="attempt-0",
                    fault_type="false_positive",
                    target_planned_ai_unit_id="range_0",
                    injected=True,
                )
            ],
        ).validate()


def test_run_store_round_trips_and_scans_root_result_without_overwrite(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.storage import (
        RunStore,
        StorageConflictError,
        scan_resume,
    )

    store = RunStore(tmp_path)
    result = _valid_root()
    root_key = ("exp1", "exp1-condition", "case-0", 0)

    assert store.write_root_result(result) == "written"
    result_path = store.root_result_path(*root_key)
    assert result_path.parts[-4] == "roots"
    assert result_path.parts[-3] == "exp1"
    assert result_path.name == "result.json"
    assert store.read_root_result(*root_key) == result
    assert scan_resume(tmp_path).committed_root_keys == frozenset({root_key})
    assert store.write_root_result(result) == "skipped"

    with pytest.raises(StorageConflictError, match="conflicting ordinary file"):
        store.write_root_result(
            replace(
                result,
                verified_correct=False,
                failure_stage="domain_root_recheck",
                failure_kind="incorrect_final",
            )
        )

    assert store.write_root_protocol(*root_key, {"status": "completed"}) == "written"
    view = scan_resume(tmp_path)
    assert view.protocol_root_keys == frozenset({root_key})

    orphan_temp = store.root_result_path(*root_key).with_name(
        ".result.json.interrupted.tmp"
    )
    orphan_temp.write_text('{"partial":true}', encoding="utf-8")
    assert scan_resume(tmp_path) == view


def test_root_result_writer_and_protocol_projection_emit_strict_v2(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.storage import RunStore

    store = RunStore(tmp_path)
    result = _valid_root(experiment_id="exp2")
    root_key = ("exp2", "exp2-condition", "case-0", 0)

    assert ROOT_RESULT_SCHEMA_VERSION == "tokenshare.slim_v2.root_result.v2"
    assert result.failure_origin is None
    store.write_root_result(result)
    written = json.loads(
        store.root_result_path(*root_key).read_text(encoding="utf-8")
    )
    assert written["schema_version"] == ROOT_RESULT_SCHEMA_VERSION
    assert "failure_origin" in written
    assert written["failure_origin"] is None
    assert store.read_root_result(*root_key) == result

    store.write_root_protocol_snapshot(
        *root_key,
        protocol_result={"summary": {"runtime_observation": {}}},
        traces=[],
        protocol_projection=result,
    )
    protocol = store.read_root_protocol(*root_key)
    assert protocol["protocol_projection"]["schema_version"] == (
        ROOT_RESULT_SCHEMA_VERSION
    )
    assert "failure_origin" in protocol["protocol_projection"]
    assert protocol["protocol_projection"]["failure_origin"] is None


def test_root_result_writer_rejects_verified_failure(tmp_path: Path) -> None:
    from tokenshare.experiments.storage import RunStore

    store = RunStore(tmp_path)
    result = replace(_valid_root(), failure_kind="incorrect_final")
    root_key = ("exp1", "exp1-condition", "case-0", 0)

    with pytest.raises(SchemaValidationError, match="verified_correct"):
        store.write_root_result(result)
    assert not store.root_result_path(*root_key).exists()


def test_root_result_reader_explicitly_rejects_v1(tmp_path: Path) -> None:
    from tokenshare.experiments.storage import RunStore

    store = RunStore(tmp_path)
    result = _valid_root()
    root_key = ("exp1", "exp1-condition", "case-0", 0)
    document = asdict(result)
    document["schema_version"] = "tokenshare.slim_v2.root_result.v1"
    path = store.root_result_path(*root_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="RootResultV2 ordinary schema_version"):
        store.read_root_result(*root_key)


def test_root_result_reader_rejects_missing_failure_origin(tmp_path: Path) -> None:
    from tokenshare.experiments.storage import RunStore

    store = RunStore(tmp_path)
    result = _valid_root()
    root_key = ("exp1", "exp1-condition", "case-0", 0)
    document = asdict(result)
    document["schema_version"] = "tokenshare.slim_v2.root_result.v2"
    document.pop("failure_origin")
    path = store.root_result_path(*root_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="RootResultV2 ordinary fields"):
        store.read_root_result(*root_key)


def test_scan_resume_records_trace_terminal_and_intent_only_call_facts(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.storage import RunStore, scan_resume

    store = RunStore(tmp_path)
    trace = _valid_trace()

    assert store.write_trace(trace) == "written"
    assert store.trace_path("case-0", 0, "range_0").relative_to(tmp_path) == Path(
        "traces/exp1/case-0/0/range_0.json"
    )
    assert store.read_trace("case-0", 0, "range_0") == trace
    assert store.write_call_intent("call-live", {"request": "pending"}) == "written"
    assert store.write_call_intent("call-done", {"request": "sent"}) == "written"
    assert store.write_call_terminal("call-done", {"result": "ok"}) == "written"
    assert store.call_terminal_path("call-done").relative_to(tmp_path) == Path(
        "calls/call-done.terminal.json"
    )

    view = scan_resume(tmp_path)
    assert view.trace_keys == frozenset({("case-0", 0, "range_0")})
    assert view.terminal_call_keys == frozenset({"call-done"})
    assert view.nonterminal_call_keys == frozenset({"call-live"})


def test_select_trace_attempt_uses_exact_or_last_natural_ordinal() -> None:
    from tokenshare.experiments.storage import select_trace_attempt

    attempts = [
        _actual_attempt(),
        replace(
            _actual_attempt(),
            attempt_id="attempt-1",
            attempt_ordinal=1,
        ),
    ]
    trace = replace(_valid_trace(), attempts=attempts)

    exact = select_trace_attempt(trace, 0)
    assert exact.attempt == attempts[0]
    assert exact.source_attempt_ordinal == 0
    assert exact.source_attempt_fallback_used is False

    fallback = select_trace_attempt(trace, 2)
    assert fallback.attempt == attempts[1]
    assert fallback.source_attempt_ordinal == 1
    assert fallback.source_attempt_fallback_used is True

    with pytest.raises(TypeError, match="UnitTraceV1"):
        select_trace_attempt({"attempts": attempts}, 0)  # type: ignore[arg-type]
