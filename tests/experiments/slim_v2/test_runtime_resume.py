from __future__ import annotations

from dataclasses import asdict, replace
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tokenshare.experiments.slim_v2.profiles import (
    build_inventory,
    project_root_inventory_rows,
)
from tokenshare.experiments.slim_v2.schema import (
    AblationObservationV1,
    AttemptResultV1,
    ProviderEntryViewV1,
    RootResultV1,
    UnitTraceV1,
)


def _nullable_reasons(record_type: type[Any]) -> dict[str, str]:
    return {
        path: "task6_resume_fixture_not_applicable"
        for path in record_type.nullable_leaf_paths()
    }


def _attempt(case_id: str, planned: str) -> AttemptResultV1:
    reasons = _nullable_reasons(AttemptResultV1)
    reasons.update({f"attempts[].{key}": value for key, value in reasons.items()})
    attempt = AttemptResultV1(
        attempt_id=f"resume:{planned}:0",
        unit_id=f"unit:{planned}",
        planned_ai_unit_id=planned,
        attempt_ordinal=0,
        trace_origin="protocol",
        started_at_ms=1,
        ended_at_ms=2,
        result_kind="success",
        provider_call_made=True,
        http_status=200,
        provider_latency_ms=1,
        raw_response_present=True,
        raw_response_relative_path=f"responses/{case_id}-{planned}.json",
        parse_result="accepted",
        verifier_result="passed",
        canonical_accepted=True,
        prompt_tokens=1,
        prompt_cache_hit_tokens=0,
        prompt_cache_miss_tokens=1,
        completion_tokens=1,
        reasoning_tokens=0,
        total_tokens=2,
        provider_request_started_at_utc="2026-08-22T00:00:00Z",
        pricing_version="slim_v2.pricing.2026-08-20",
        pricing_tier="off_peak",
        cost_estimate_cny=0.0,
        usage_status="usage_complete",
        call_state="terminal",
        missing_reason=reasons,
    )
    attempt.validate(experiment_id="exp1")
    return attempt


def _success(inventory: Any) -> RootResultV1:
    is_exp1 = inventory.experiment_id == "exp1"
    is_exp2 = inventory.experiment_id == "exp2"
    is_exp34 = inventory.experiment_id in {"exp3", "exp4"}
    result = RootResultV1(
        experiment_id=inventory.experiment_id,
        condition_id=inventory.condition_id,
        case_id=inventory.case_id,
        repeat_id=inventory.repeat_id,
        domain=inventory.domain,
        difficulty=inventory.difficulty,
        topic_family=inventory.topic_family,
        position_stratum=inventory.position_stratum,
        mode=inventory.mode,
        disabled_mechanisms=list(inventory.disabled_mechanisms),
        worker_count=inventory.worker_count,
        fault_type=inventory.fault_type,
        fault_rate=inventory.fault_rate,
        dead_worker_count=inventory.dead_worker_count,
        kill_progress_target_ratio=inventory.kill_progress_target_ratio,
        challenge_plan_id=inventory.challenge_plan_id,
        challenge_family=None,
        challenge_target_planned_ai_unit_ids=None,
        challenge_attempt_ordinal_rule=None,
        provider_family="deepseek",
        provider_entry_id=inventory.provider_entry_id,
        configured_model=inventory.configured_model,
        requested_model=inventory.configured_model,
        resolved_model=inventory.configured_model,
        reasoning_mode="thinking",
        root_start_at_ms=1,
        root_terminal_at_ms=2,
        runtime_wall_clock_ms=1,
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
        planned_ai_unit_ids=list(inventory.planned_ai_unit_ids),
        dispatched_ai_unit_ids=list(inventory.planned_ai_unit_ids),
        completed_ai_unit_ids=list(inventory.planned_ai_unit_ids),
        unscheduled_ai_unit_ids=[],
        in_flight_ai_unit_ids_at_witness=[] if is_exp2 else None,
        observed_peak_concurrency=1 if is_exp2 else None,
        worker_execution_facts=[],
        required_slot_count=(
            len(inventory.planned_ai_unit_ids) if is_exp34 else None
        ),
        recovered_valid_canonical_slot_count=(
            len(inventory.planned_ai_unit_ids) if is_exp34 else None
        ),
        attempts=[],
        fault_target_planned_ai_unit_ids=(
            [] if inventory.experiment_id == "exp3" else None
        ),
        fault_target_count=0 if inventory.experiment_id == "exp3" else None,
        fault_observations=[],
        recovery_observations=[],
        worker_death_observations=[],
        challenge_observations=[],
        ablation_observations=[
            AblationObservationV1(disabled_mechanism=name)
            for name in inventory.disabled_mechanisms
        ],
        missing_reason={},
        not_applicable_reason=_nullable_reasons(RootResultV1),
    )
    result.validate()
    return result


def test_fresh_process_protocol_resume_uses_typed_files_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2 import runtime
    from tokenshare.experiments.slim_v2.storage import RunStore

    root = next(
        item
        for item in project_root_inventory_rows(
            build_inventory("representative")
        ).roots
        if item.experiment_id == "exp1" and item.domain == "factorization"
    )
    key = (root.experiment_id, root.condition_id, root.case_id, root.repeat_id)
    planned = root.planned_ai_unit_ids[0]
    trace = UnitTraceV1(
        case_id=root.case_id,
        planned_ai_unit_id=planned,
        domain=root.domain,
        trace_origin="protocol",
        candidate_start=2,
        candidate_end=3,
        lemma_node_id=None,
        dependency_path=None,
        provider_family="deepseek",
        provider_entry_id=root.provider_entry_id,
        configured_model=root.configured_model,
        requested_model=root.configured_model,
        resolved_model=root.configured_model,
        attempts=[_attempt(root.case_id, planned)],
    )
    trace.validate()
    first = RunStore(tmp_path / "run")
    expected = _success(root)
    first.write_root_protocol_snapshot(
        *key,
        protocol_result={
            "run_id": "task6-fresh-process",
            "status": "completed",
            "summary": {
                "slim_condition_failure": {
                    "failure_stage": "provider_call",
                    "failure_kind": "resolved_model_mismatch",
                }
            },
        },
        traces=[trace],
        protocol_projection=expected,
    )

    # 模拟新的 Python 进程：不保留 assembly/adapter，并删掉可由 protocol 重建的 trace。
    fresh = RunStore(first.run_dir)
    monkeypatch.setattr(
        runtime,
        "call_provider_once",
        lambda *args, **kwargs: pytest.fail("protocol resume must not call provider"),
    )
    context = SimpleNamespace(inventory=root, run_store=fresh)
    actual = runtime.resume_exp1_root_context(
        context,
        fresh.read_root_protocol(*key),
    )

    assert asdict(actual) == asdict(expected)
    restored = fresh.read_trace(root.case_id, 0, planned)
    assert asdict(restored) == asdict(trace)


def test_typed_terminal_and_response_are_reused_without_second_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2 import runtime
    from tokenshare.experiments.slim_v2 import provider
    from tokenshare.experiments.slim_v2.provider import (
        ProviderCallContextV1,
        call_provider_once,
    )
    from tokenshare.experiments.slim_v2.storage import RunStore
    from tests.experiments.slim_v2.test_answer_paths import (
        _FakeResponse,
        _control,
        _entry,
        _response_body,
    )

    store = RunStore(tmp_path / "run")
    context = ProviderCallContextV1(
        call_key="exp1|condition|case|0|range_0|0",
        root_key=("exp1", "condition", "case", 0),
        planned_ai_unit_id="range_0",
        attempt_ordinal=0,
    )
    entry = _entry(family="deepseek", model="deepseek-v4-pro")
    response = _FakeResponse(_response_body(model="deepseek-v4-pro"))
    monkeypatch.setenv("SLIM_V2_TEST_KEY", "secret")
    monkeypatch.setattr(provider, "_open_response", lambda *args: response)
    result = call_provider_once(entry, "prompt", _control(), context, store)
    terminal = json.loads(
        store.call_terminal_path(context.call_key).read_text(encoding="utf-8")
    )
    assert terminal["result"]["raw_response_json"] is None
    assert {path.name for path in (store.run_dir / "calls").iterdir()} == {
        store.call_intent_path(context.call_key).name,
        store.call_terminal_path(context.call_key).name,
    }
    calls: list[str] = []

    actual = runtime._call_provider_with_resume(
        entry,
        "prompt",
        _control(),
        context,
        RunStore(store.run_dir),
        provider_call=lambda *args: calls.append("called"),
    )

    assert actual == result
    assert calls == []


def _interrupted_intent(
    entry: ProviderEntryViewV1,
    context: Any,
    *,
    owner_pid: int | None,
) -> dict[str, Any]:
    value = {
        "call_key": context.call_key,
        "root_key": list(context.root_key),
        "planned_ai_unit_id": context.planned_ai_unit_id,
        "attempt_ordinal": context.attempt_ordinal,
        "provider_family": entry.provider_family,
        "provider_entry_id": entry.entry_id,
        "configured_model": entry.configured_model,
        "provider_request_started_at_utc": "2026-08-22T00:00:00Z",
    }
    if owner_pid is not None:
        value["owner_pid"] = owner_pid
    return value


def test_interrupted_response_is_parsed_offline_and_terminalized(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2 import runtime
    from tokenshare.experiments.slim_v2.provider import ProviderCallContextV1
    from tokenshare.experiments.slim_v2.storage import RunStore
    from tests.experiments.slim_v2.test_answer_paths import (
        _control,
        _entry,
        _response_body,
    )

    entry = _entry(family="deepseek", model="deepseek-v4-pro")
    context = ProviderCallContextV1(
        "interrupted-response",
        ("exp1", "condition", "case", 0),
        "range_0",
        0,
    )
    store = RunStore(tmp_path / "run")
    store.write_call_intent(
        context.call_key,
        _interrupted_intent(entry, context, owner_pid=999_999_999),
    )
    store.write_response(
        context.call_key,
        {
            "http_status": 200,
            "body": json.loads(_response_body(model=entry.configured_model)),
            "provider_latency_ms": 7,
        },
    )
    calls: list[str] = []

    result = runtime._call_provider_with_resume(
        entry,
        "prompt",
        _control(),
        context,
        store,
        provider_call=lambda *args: calls.append("called"),
    )

    assert result.ok is True
    assert result.provider_latency_ms == 7
    assert result.raw_response_json is not None
    assert calls == []
    assert store.call_terminal_path(context.call_key).is_file()


def test_dead_intent_becomes_unknown_and_only_next_ordinal_calls_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2 import provider, runtime
    from tokenshare.experiments.slim_v2.provider import (
        ProviderCallContextV1,
        call_provider_once,
    )
    from tokenshare.experiments.slim_v2.storage import RunStore
    from tests.experiments.slim_v2.test_answer_paths import (
        _FakeResponse,
        _control,
        _entry,
        _response_body,
    )

    entry = _entry(family="deepseek", model="deepseek-v4-pro")
    first = ProviderCallContextV1(
        "dead-intent-0",
        ("exp1", "condition", "case", 0),
        "range_0",
        0,
    )
    store = RunStore(tmp_path / "run")
    store.write_call_intent(
        first.call_key,
        _interrupted_intent(entry, first, owner_pid=None),
    )
    transport_calls: list[int] = []

    unknown = runtime._call_provider_with_resume(
        entry,
        "prompt",
        _control(),
        first,
        store,
        provider_call=lambda *args: transport_calls.append(0),
    )
    assert unknown.error_kind == "unknown_transport_outcome"
    assert transport_calls == []
    assert store.call_terminal_path(first.call_key).is_file()

    second = ProviderCallContextV1(
        "dead-intent-1",
        first.root_key,
        first.planned_ai_unit_id,
        1,
    )
    monkeypatch.setenv("SLIM_V2_TEST_KEY", "secret")

    def open_once(*args: object) -> _FakeResponse:
        transport_calls.append(1)
        return _FakeResponse(_response_body(model=entry.configured_model))

    monkeypatch.setattr(provider, "_open_response", open_once)
    recovered = runtime._call_provider_with_resume(
        entry,
        "prompt",
        _control(),
        second,
        store,
        provider_call=call_provider_once,
    )
    assert recovered.ok is True
    assert transport_calls == [1]


def test_live_intent_owner_refuses_resume_without_terminal(tmp_path: Path) -> None:
    from tokenshare.experiments.slim_v2 import runtime
    from tokenshare.experiments.slim_v2.provider import ProviderCallContextV1
    from tokenshare.experiments.slim_v2.storage import RunStore, StorageConflictError
    from tests.experiments.slim_v2.test_answer_paths import _control, _entry

    entry = _entry(family="deepseek", model="deepseek-v4-pro")
    context = ProviderCallContextV1(
        "live-intent",
        ("exp1", "condition", "case", 0),
        "range_0",
        0,
    )
    store = RunStore(tmp_path / "run")
    store.write_call_intent(
        context.call_key,
        _interrupted_intent(entry, context, owner_pid=os.getpid()),
    )

    with pytest.raises(StorageConflictError, match="active owner"):
        runtime._call_provider_with_resume(
            entry,
            "prompt",
            _control(),
            context,
            store,
            provider_call=lambda *args: pytest.fail("live intent reached provider"),
        )
    assert not store.call_terminal_path(context.call_key).exists()


def test_fresh_protocol_resume_calls_only_the_missing_factor_tail_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2 import provider, runtime
    from tokenshare.experiments.slim_v2.storage import RunStore
    from tests.experiments.slim_v2.test_answer_paths import (
        _PromptResponseFactory,
        _answer_path_factor_case,
        _entry,
    )
    from tests.experiments.slim_v2.test_system_vertical import _inventory

    model = "deepseek-v4-pro"
    entry = _entry(family="deepseek", model=model)
    factory = _PromptResponseFactory(model)
    monkeypatch.setenv("SLIM_V2_TEST_KEY", "secret")
    monkeypatch.setattr(provider, "_open_response", factory)
    case = _answer_path_factor_case()
    inventory = replace(
        _inventory(case=case, domain="factorization"),
        condition_id="task6_missing_tail_resume",
        provider_entry_id=entry.entry_id,
        configured_model=model,
        planned_ai_unit_ids=["range_0", "range_1", "range_2"],
    )
    inventory.validate()
    store = RunStore(tmp_path / "run")
    context_values = {
        "run_id": "task6-missing-tail",
        "profile_id": "representative",
        "inventory": inventory,
        "root_input": case,
        "source_run_dir": None,
        "challenge_plan": None,
        "is_reference": False,
        "provider_entries": {entry.entry_id: entry},
        "max_retries": 2,
        "continue_after_terminal_child_failure": True,
        "protocol_execution_attempt_upper": 9,
        "provider_call_upper": 9,
    }
    production_tail = runtime.run_coverage_tail

    def stop_after_protocol(**kwargs: object) -> object:
        raise RuntimeError("simulated process exit after protocol checkpoint")

    monkeypatch.setattr(runtime, "run_coverage_tail", stop_after_protocol)
    with pytest.raises(RuntimeError, match="simulated process exit"):
        runtime.execute_root_context(
            SimpleNamespace(**context_values, run_store=store)
        )
    monkeypatch.setattr(runtime, "run_coverage_tail", production_tail)

    # protocol 已原子落盘，但 coverage-tail 从未 dispatch。
    planned = "range_2"
    assert len(factory.responses) == 2
    assert not store.trace_path(inventory.case_id, 0, planned).exists()

    fresh = RunStore(store.run_dir)
    fresh_context = SimpleNamespace(**context_values, run_store=fresh)
    before = len(factory.responses)
    protocol_snapshot = fresh.read_root_protocol(
        inventory.experiment_id,
        inventory.condition_id,
        inventory.case_id,
        inventory.repeat_id,
    )
    resumed = runtime.resume_exp1_root_context(
        fresh_context,
        protocol_snapshot,
    )
    fresh.write_root_result(resumed)

    assert len(factory.responses) == before + 1
    assert resumed.trace_tail_target_ai_unit_ids == [planned]
    assert resumed.trace_tail_recorded_ai_unit_ids == [planned]
    assert resumed.trace_tail_provider_attempt_count == 1
    assert fresh.read_trace(inventory.case_id, 0, planned).attempts[0].verifier_result == "passed"
    assert asdict(fresh.read_root_result(
        inventory.experiment_id,
        inventory.condition_id,
        inventory.case_id,
        inventory.repeat_id,
    )) == asdict(resumed)

    again = runtime.resume_exp1_root_context(fresh_context, protocol_snapshot)
    assert asdict(again) == asdict(resumed)
    assert len(factory.responses) == before + 1
    assert not list((fresh.run_dir / "calls").glob("*.outcome.json"))


def test_started_online_runtime_failure_uses_inventory_and_never_runs_tail(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2 import provider, runtime
    from tokenshare.experiments.slim_v2.storage import RunStore
    from tests.experiments.slim_v2.test_answer_paths import (
        _FakeResponse,
        _answer_path_factor_case,
        _entry,
        _response_body,
    )
    from tests.experiments.slim_v2.test_system_vertical import _inventory

    case = _answer_path_factor_case()
    case["split_params"] = {
        "strategy_id": "factorization.candidate_range_partition.v1",
        "requested_child_count": 1,
    }
    entry = _entry(family="deepseek", model="deepseek-v4-pro")
    inventory = replace(
        _inventory(case=case, domain="factorization"),
        condition_id="task6_started_runtime_failure",
        provider_entry_id=entry.entry_id,
        configured_model=entry.configured_model,
        planned_ai_unit_ids=["range_0"],
    )
    inventory.validate()
    store = RunStore(tmp_path / "started-runtime-failure")
    context = SimpleNamespace(
        run_id="task6-started-runtime-failure",
        profile_id="representative",
        inventory=inventory,
        root_input=case,
        run_store=store,
        source_run_dir=None,
        challenge_plan=None,
        is_reference=False,
        provider_entries={entry.entry_id: entry},
        max_retries=2,
        continue_after_terminal_child_failure=False,
        protocol_execution_attempt_upper=3,
        provider_call_upper=3,
    )
    responses: list[_FakeResponse] = []

    def invalid_response(request: object, timeout_seconds: float) -> _FakeResponse:
        response = _FakeResponse(
            _response_body(content="{}", model=entry.configured_model)
        )
        responses.append(response)
        return response

    monkeypatch.setenv("SLIM_V2_TEST_KEY", "secret")
    monkeypatch.setattr(
        provider,
        "_open_response",
        invalid_response,
    )
    monkeypatch.setattr(
        runtime,
        "run_coverage_tail",
        lambda *args, **kwargs: pytest.fail(
            "runtime failure must not run coverage tail"
        ),
    )

    result = runtime.execute_root_context(context)

    assert result.protocol_started is True
    assert result.root_status == "failed"
    assert result.failure_stage == "protocol_runtime"
    assert result.failure_kind == "infrastructure_invalid"
    assert len(responses) == 3
    assert result.planned_ai_unit_ids == ["range_0"]
    assert result.dispatched_ai_unit_ids == ["range_0"]
    assert result.completed_ai_unit_ids == []
    assert result.unscheduled_ai_unit_ids == []
    assert len(result.attempts) == 3
    snapshot = store.read_root_protocol_snapshot(
        inventory.experiment_id,
        inventory.condition_id,
        inventory.case_id,
        inventory.repeat_id,
    )
    runtime_failure = snapshot.protocol_result["summary"]["slim_runtime_failure"]
    assert runtime_failure["failure_kind"] == "infrastructure_invalid"
    assert runtime_failure["error_kind"] == "RuntimeError"
    trace = store.read_trace(inventory.case_id, 0, "range_0")
    assert [attempt.attempt_ordinal for attempt in trace.attempts] == [0, 1, 2]


def test_tail_request_snapshot_round_trips_only_public_execution_dto(
) -> None:
    from tokenshare.experiments.slim_v2 import runtime
    from tokenshare.core.models import ArtifactRef
    from tokenshare.executors.contracts import EnvironmentRef, ExecutionRequest
    from tokenshare.plugins.contracts import OutputContract

    ref = ArtifactRef(
        artifact_id="task6-request-ref",
        artifact_type="fixture",
        uri="artifact://task6-request-ref",
        content_hash="sha256:" + "1" * 64,
        size_bytes=1,
        media_type="application/json",
        artifact_schema_id="task6.fixture",
        artifact_schema_version="v1",
        source={"kind": "task6_test"},
        metadata={},
        created_at="2026-08-22T00:00:00Z",
    )
    environment = EnvironmentRef(
        environment_id="task6-env",
        environment_digest="sha256:" + "2" * 64,
        runtime="python",
        tool_versions={},
        resource_limits={},
        fixture_profile_digest="sha256:" + "3" * 64,
        seed=1,
        clock_policy="fixed",
        created_at="2026-08-22T00:00:00Z",
    )
    request = ExecutionRequest(
        request_id="task6-tail-request",
        task_id="task6-task",
        unit_id="task6-unit",
        attempt_id="task6-attempt",
        lease_id="task6-lease",
        fencing_token="task6-fence",
        plugin={},
        executor={},
        registry_snapshot_id="task6-registry",
        allocation_decision={},
        capability_snapshot={},
        task_unit_snapshot={},
        input_artifact_refs={"input": ref},
        output_contract=OutputContract(
            output_contract_id="task6-output",
            required_outputs=["answer"],
            output_schema_refs={},
            raw_output_policy={},
        ),
        hard_requirements={},
        soft_hints={"planned_ai_unit_id": "range_0"},
        environment_ref=environment,
        execution_instruction_ref=ref,
        prompt_package_ref=ref,
        limits={"max_tokens": 1},
        created_at="2026-08-22T00:00:00Z",
    )

    restored = runtime._execution_request_from_document(request.to_dict())

    assert restored.to_dict() == request.to_dict()


def test_run_and_reference_writers_are_public_atomic_contracts(tmp_path: Path) -> None:
    from tokenshare.experiments.slim_v2.storage import RunStore

    store = RunStore(tmp_path / "run")
    store.write_run_config({"run_id": "task6", "profile_id": "representative"})
    assert store.read_run_config()["run_id"] == "task6"

    root = next(
        item
        for item in project_root_inventory_rows(
            build_inventory("representative")
        ).exp3_references
    )
    result = _success(root)
    store.write_exp3_reference_result(result)
    assert store.exp3_reference_result_path(
        result.condition_id,
        result.case_id,
        result.repeat_id,
    ).is_file()


def test_production_fixed_root_uses_system_runtime_with_zero_new_provider_calls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2 import runtime
    from tokenshare.experiments.slim_v2.storage import RunStore
    from tests.experiments.slim_v2.test_scenarios import (
        _acquire_factor_source,
        _inventory,
    )

    case, source, response_factory = _acquire_factor_source(tmp_path, monkeypatch)
    inventory = _inventory(
        experiment_id="exp2",
        case=case,
        domain="factorization",
        worker_count=3,
    )
    before = len(response_factory.responses)
    context = SimpleNamespace(
        run_id="task6-fixed-production",
        profile_id="representative",
        inventory=inventory,
        root_input=case,
        run_store=RunStore(tmp_path / "target-run"),
        source_run_dir=source.run_dir,
        challenge_plan=None,
        is_reference=False,
        provider_entries={},
        max_retries=2,
        continue_after_terminal_child_failure=True,
        protocol_execution_attempt_upper=len(inventory.planned_ai_unit_ids) * 3,
        provider_call_upper=0,
    )

    result = runtime.execute_root_context(context)

    assert result.experiment_id == "exp2"
    assert result.root_status == "completed"
    assert len(response_factory.responses) == before
    protocol_path = context.run_store.root_protocol_path(
        inventory.experiment_id,
        inventory.condition_id,
        inventory.case_id,
        inventory.repeat_id,
    )
    assert protocol_path.is_file()
    fresh = RunStore(context.run_store.run_dir)
    fresh_context_values = vars(context).copy()
    fresh_context_values["run_store"] = fresh
    fresh_context = SimpleNamespace(**fresh_context_values)
    monkeypatch.setattr(
        runtime,
        "run_root_slice",
        lambda *args, **kwargs: pytest.fail("resume must not run the root again"),
    )
    monkeypatch.setattr(
        runtime,
        "call_provider_once",
        lambda *args, **kwargs: pytest.fail("resume must not call provider"),
    )

    resumed = runtime.resume_root_context(
        fresh_context,
        fresh.read_root_protocol(
            inventory.experiment_id,
            inventory.condition_id,
            inventory.case_id,
            inventory.repeat_id,
        ),
    )

    assert asdict(resumed) == asdict(result)
    system_dir = context.run_store.system_root_directory(
        inventory.experiment_id,
        inventory.condition_id,
        inventory.case_id,
        inventory.repeat_id,
    )
    assert (system_dir / "events.jsonl").is_file()
    assert any(path.is_file() for path in (system_dir / "artifacts").iterdir())
    assert not (system_dir / "artifacts" / "artifacts").exists()


def test_exp5_keeps_ten_workers_but_limits_provider_calls_to_three(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Lock
    from time import sleep

    from tokenshare.experiments.slim_v2 import provider, runtime
    from tokenshare.experiments.slim_v2.provider import ProviderCallContextV1
    from tokenshare.experiments.slim_v2.storage import RunStore
    from tests.experiments.slim_v2.test_answer_paths import (
        _FakeResponse,
        _control,
        _entry,
        _response_body,
    )

    root = next(
        item
        for item in project_root_inventory_rows(
            build_inventory("representative")
        ).roots
        if item.experiment_id == "exp5"
    )
    assert root.worker_count == 10

    lock = Lock()
    active = 0
    observed_peak = 0

    def blocking_transport(*args: object) -> _FakeResponse:
        nonlocal active, observed_peak
        with lock:
            active += 1
            observed_peak = max(observed_peak, active)
        try:
            sleep(0.05)
            return _FakeResponse(_response_body(model="Qwen/Qwen3-14B"))
        finally:
            with lock:
                active -= 1

    monkeypatch.setenv("SLIM_V2_TEST_KEY", "secret")
    monkeypatch.setattr(provider, "_open_response", blocking_transport)
    entry = _entry(family="siliconflow", model="Qwen/Qwen3-14B")
    store = RunStore(tmp_path / "run")
    caller = runtime._provider_caller_for_experiment("exp5")

    with ThreadPoolExecutor(max_workers=root.worker_count) as pool:
        futures = [
            pool.submit(
                caller,
                entry,
                "prompt",
                _control(),
                ProviderCallContextV1(
                    call_key=f"exp5-permit-{index}",
                    root_key=("exp5", "condition", "case", 0),
                    planned_ai_unit_id=f"range_{index}",
                    attempt_ordinal=0,
                ),
                store,
            )
            for index in range(root.worker_count)
        ]
        results = [future.result() for future in futures]

    assert all(result.ok for result in results)
    assert 1 < observed_peak <= 3
