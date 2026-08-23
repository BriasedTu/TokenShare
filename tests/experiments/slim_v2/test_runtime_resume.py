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
    RootResultV2,
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


def _lean_pre_dispatch_trace(case_id: str, planned: str) -> UnitTraceV1:
    present_nullable = {
        "trace_origin",
        "started_at_ms",
        "ended_at_ms",
        "parse_result",
        "checker_result",
    }
    attempt = AttemptResultV1(
        attempt_id=f"predispatch:{planned}:0",
        unit_id=f"unit:{planned}",
        planned_ai_unit_id=planned,
        attempt_ordinal=0,
        trace_origin="coverage_tail",
        started_at_ms=1,
        ended_at_ms=1,
        result_kind="pre_dispatch_failure",
        provider_call_made=False,
        raw_response_present=False,
        parse_result="not_reached",
        checker_result="not_reached",
        canonical_accepted=False,
        usage_status="not_available",
        call_state="not_started",
        missing_reason={
            f"attempts[].{path}": "pre_dispatch_not_reached"
            for path in AttemptResultV1.nullable_leaf_paths()
            if path not in present_nullable
        },
    )
    trace = UnitTraceV1(
        case_id=case_id,
        planned_ai_unit_id=planned,
        domain="lean",
        trace_origin="coverage_tail",
        lemma_node_id=planned,
        dependency_path=[planned],
        provider_family="deepseek",
        provider_entry_id="deepseek-entry",
        configured_model="deepseek-model",
        requested_model="deepseek-model",
        resolved_model="deepseek-model",
        attempts=[attempt],
    )
    trace.validate()
    return trace


def _success(inventory: Any) -> RootResultV2:
    is_exp1 = inventory.experiment_id == "exp1"
    is_exp2 = inventory.experiment_id == "exp2"
    is_exp34 = inventory.experiment_id in {"exp3", "exp4"}
    result = RootResultV2(
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
        not_applicable_reason=_nullable_reasons(RootResultV2),
    )
    result.validate()
    return result


@pytest.mark.parametrize(
    ("unscheduled", "tail_requests", "include_predispatch", "message"),
    [
        (["range_0"], {"range_1": {}}, False, "outside unscheduled"),
        (
            ["range_0", "range_1", "range_2"],
            {"range_1": {}},
            True,
            "does not close",
        ),
    ],
)
def test_protocol_snapshot_write_rejects_open_tail_request_sets(
    unscheduled: list[str],
    tail_requests: dict[str, dict[str, object]],
    include_predispatch: bool,
    message: str,
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2.storage import RunStore

    store = RunStore(tmp_path / "open-tail-write")
    with pytest.raises(ValueError, match=message):
        store.write_root_protocol_snapshot(
            "exp1",
            "condition",
            "case",
            0,
            protocol_result={
                "summary": {
                    "runtime_observation": {
                        "unscheduled_ai_unit_ids": unscheduled,
                    }
                }
            },
            traces=(
                [_lean_pre_dispatch_trace("case", "range_0")]
                if include_predispatch
                else []
            ),
            tail_requests=tail_requests,
        )


def test_protocol_snapshot_read_rejects_open_tail_request_set(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2.storage import (
        PROTOCOL_SNAPSHOT_SCHEMA_VERSION,
        RunStore,
    )

    store = RunStore(tmp_path / "open-tail-read")
    key = ("exp1", "condition", "case", 0)
    store.write_root_protocol(
        *key,
        {
            "schema_version": PROTOCOL_SNAPSHOT_SCHEMA_VERSION,
            "root_key": list(key),
            "protocol_result": {
                "summary": {
                    "runtime_observation": {
                        "unscheduled_ai_unit_ids": [
                            "range_0",
                            "range_1",
                            "range_2",
                        ],
                    }
                }
            },
            "traces": [asdict(_lean_pre_dispatch_trace("case", "range_0"))],
            "tail_requests": {"range_1": {}},
            "protocol_projection": None,
        },
    )

    with pytest.raises(ValueError, match="does not close"):
        store.read_root_protocol_snapshot(*key)


def test_non_exp1_snapshot_does_not_require_coverage_tail_closure(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2.storage import RunStore

    rows = project_root_inventory_rows(build_inventory("representative"))
    inventory = next(row for row in rows.roots if row.experiment_id == "exp2")
    projection = _success(inventory)
    store = RunStore(tmp_path / "exp2-no-tail")
    key = (
        inventory.experiment_id,
        inventory.condition_id,
        inventory.case_id,
        inventory.repeat_id,
    )
    store.write_root_protocol_snapshot(
        *key,
        protocol_result={
            "summary": {
                "runtime_observation": {
                    "unscheduled_ai_unit_ids": list(inventory.planned_ai_unit_ids),
                }
            }
        },
        traces=(),
        tail_requests={},
        protocol_projection=projection,
    )

    snapshot = store.read_root_protocol_snapshot(*key)
    assert snapshot.protocol_projection == projection
    assert snapshot.traces == ()
    assert snapshot.tail_requests == {}


def test_pre_flash_exp2_terminal_ledger_reprojects_once_without_transport(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """唯一旧 cohort 的 terminal ledger 只能纯投影，不得重放协议或 provider。"""

    from tokenshare.experiments.slim_v2 import runtime
    from tokenshare.experiments.slim_v2.cli import _CliRootContext
    from tokenshare.experiments.slim_v2.schema import (
        PRE_FLASH_CONFIGURED_MODEL,
        PRE_FLASH_EXP2_ROOT_KEY,
        PRE_FLASH_PROVIDER_ENTRY_ID,
        PRE_FLASH_REPRESENTATIVE_RUN_ID,
        RootInventoryV1,
    )
    from tokenshare.experiments.slim_v2.storage import RunStore
    from tests.experiments.slim_v2.test_scenarios import (
        _acquire_factor_source,
        _replace_source_attempt_latency,
    )

    # 这里的 source 仅为临时 typed fixture；实际恢复始终从同一 run 的 trace 读取。
    case, store, _factory = _acquire_factor_source(tmp_path, monkeypatch)
    source_case_id = str(case["case_id"])
    case = {**case, "case_id": PRE_FLASH_EXP2_ROOT_KEY[2]}
    planned = ["range_0", "range_1", "range_2"]
    # 将临时 source 的普通 trace 身份变为冻结 key，不复制任何 provider response 内容。
    for target in planned:
        original = store.read_trace(source_case_id, 0, target)
        store.write_trace(replace(
            original,
            case_id=PRE_FLASH_EXP2_ROOT_KEY[2],
            provider_entry_id=PRE_FLASH_PROVIDER_ENTRY_ID,
            configured_model=PRE_FLASH_CONFIGURED_MODEL,
            requested_model=PRE_FLASH_CONFIGURED_MODEL,
            resolved_model=PRE_FLASH_CONFIGURED_MODEL,
        ))
    _replace_source_attempt_latency(
        source_store=store,
        case_id=PRE_FLASH_EXP2_ROOT_KEY[2],
        planned_ai_unit_id="range_0",
        latency_ms=625_812,
    )
    inventory = RootInventoryV1(
        experiment_id="exp2",
        condition_id=PRE_FLASH_EXP2_ROOT_KEY[1],
        case_id=PRE_FLASH_EXP2_ROOT_KEY[2],
        repeat_id=0,
        domain="factorization",
        difficulty="hard",
        topic_family=None,
        position_stratum="late",
        worker_count=1,
        mode=None,
        disabled_mechanisms=[],
        fault_type=None,
        fault_rate=None,
        dead_worker_count=None,
        kill_progress_target_ratio=None,
        provider_entry_id=PRE_FLASH_PROVIDER_ENTRY_ID,
        configured_model=PRE_FLASH_CONFIGURED_MODEL,
        planned_ai_unit_ids=planned,
        challenge_plan_id=None,
    )
    inventory.validate()
    context = _CliRootContext(
        run_id=PRE_FLASH_REPRESENTATIVE_RUN_ID,
        profile_id="representative",
        inventory=inventory,
        root_input=case,
        run_store=store,
        source_run_dir=store.run_dir,
        challenge_plan=None,
        is_reference=False,
        provider_entries={},
        max_retries=2,
        continue_after_terminal_child_failure=False,
        protocol_execution_attempt_upper=9,
        provider_call_upper=0,
    )
    assembly = runtime._build_root_assembly(context)
    terminal = runtime.run_root_slice(assembly)
    assert terminal.status == "failed"
    late_submissions = [
        event for event in assembly.event_ledger.read_all()
        if event.event_type.value == "EXECUTION_SUBMISSION_RECORDED"
        and event.payload.get("acceptance_status") == "rejected"
        and event.payload.get("rejection_reason") == "lease_deadline_exceeded"
    ]
    assert len(late_submissions) == 3
    assert {event.payload.get("result_kind") for event in late_submissions} == {
        "succeeded"
    }
    key = PRE_FLASH_EXP2_ROOT_KEY
    assert not store.root_protocol_path(*key).exists()
    assert not store.root_result_path(*key).exists()
    event_path = store.system_root_directory(*key) / "events.jsonl"
    event_before = event_path.read_bytes()
    calls_before = {
        path.relative_to(store.run_dir).as_posix(): path.read_bytes()
        for path in (store.run_dir / "calls").rglob("*")
        if path.is_file()
    }
    responses_before = {
        path.relative_to(store.run_dir).as_posix(): path.read_bytes()
        for path in (store.run_dir / "responses").rglob("*")
        if path.is_file()
    }
    traces_before = {
        path.relative_to(store.run_dir).as_posix(): path.read_bytes()
        for path in (store.run_dir / "traces").rglob("*")
        if path.is_file()
    }
    monkeypatch.setattr(
        runtime,
        "call_provider_once",
        lambda **_kwargs: pytest.fail("terminal ledger recovery must not call provider"),
    )

    result = runtime.reproject_pre_flash_exp2_terminal_root_context(context)

    assert result.root_status == "failed"
    assert result.failure_kind == "no_final"
    assert [item.attempt_ordinal for item in result.attempts] == [0, 1, 2]
    assert all(item.provider_call_made is False for item in result.attempts)
    assert event_path.read_bytes() == event_before
    assert {
        path.relative_to(store.run_dir).as_posix(): path.read_bytes()
        for path in (store.run_dir / "calls").rglob("*")
        if path.is_file()
    } == calls_before
    assert {
        path.relative_to(store.run_dir).as_posix(): path.read_bytes()
        for path in (store.run_dir / "responses").rglob("*")
        if path.is_file()
    } == responses_before
    assert {
        path.relative_to(store.run_dir).as_posix(): path.read_bytes()
        for path in (store.run_dir / "traces").rglob("*")
        if path.is_file()
    } == traces_before
    assert store.root_protocol_path(*key).is_file()
    assert store.write_root_result(result) == "written"
    with pytest.raises(ValueError, match="without snapshot or result"):
        runtime.reproject_pre_flash_exp2_terminal_root_context(context)


def test_pre_flash_terminal_reprojection_rejects_duplicate_submission_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """重复 terminal submission 不能被窄恢复静默折叠。"""

    from tokenshare.experiments.slim_v2 import runtime
    from tokenshare.experiments.slim_v2.cli import _CliRootContext
    from tokenshare.experiments.slim_v2.schema import (
        PRE_FLASH_CONFIGURED_MODEL,
        PRE_FLASH_EXP2_ROOT_KEY,
        PRE_FLASH_PROVIDER_ENTRY_ID,
        PRE_FLASH_REPRESENTATIVE_RUN_ID,
        RootInventoryV1,
    )
    from tokenshare.storage.events import EventType
    from tests.experiments.slim_v2.test_scenarios import (
        _acquire_factor_source,
        _replace_source_attempt_latency,
    )

    case, store, _factory = _acquire_factor_source(tmp_path, monkeypatch)
    source_case_id = str(case["case_id"])
    case = {**case, "case_id": PRE_FLASH_EXP2_ROOT_KEY[2]}
    planned = ["range_0", "range_1", "range_2"]
    for target in planned:
        original = store.read_trace(source_case_id, 0, target)
        store.write_trace(replace(
            original,
            case_id=PRE_FLASH_EXP2_ROOT_KEY[2],
            provider_entry_id=PRE_FLASH_PROVIDER_ENTRY_ID,
            configured_model=PRE_FLASH_CONFIGURED_MODEL,
            requested_model=PRE_FLASH_CONFIGURED_MODEL,
            resolved_model=PRE_FLASH_CONFIGURED_MODEL,
        ))
    _replace_source_attempt_latency(
        source_store=store,
        case_id=PRE_FLASH_EXP2_ROOT_KEY[2],
        planned_ai_unit_id="range_0",
        latency_ms=625_812,
    )
    inventory = RootInventoryV1(
        experiment_id="exp2",
        condition_id=PRE_FLASH_EXP2_ROOT_KEY[1],
        case_id=PRE_FLASH_EXP2_ROOT_KEY[2],
        repeat_id=0,
        domain="factorization",
        difficulty="hard",
        topic_family=None,
        position_stratum="late",
        worker_count=1,
        mode=None,
        disabled_mechanisms=[],
        fault_type=None,
        fault_rate=None,
        dead_worker_count=None,
        kill_progress_target_ratio=None,
        provider_entry_id=PRE_FLASH_PROVIDER_ENTRY_ID,
        configured_model=PRE_FLASH_CONFIGURED_MODEL,
        planned_ai_unit_ids=planned,
        challenge_plan_id=None,
    )
    inventory.validate()
    context = _CliRootContext(
        run_id=PRE_FLASH_REPRESENTATIVE_RUN_ID,
        profile_id="representative",
        inventory=inventory,
        root_input=case,
        run_store=store,
        source_run_dir=store.run_dir,
        challenge_plan=None,
        is_reference=False,
        provider_entries={},
        max_retries=2,
        continue_after_terminal_child_failure=False,
        protocol_execution_attempt_upper=9,
        provider_call_upper=0,
    )
    assembly = runtime._build_root_assembly(context)
    terminal = runtime.run_root_slice(assembly)
    assert terminal.status == "failed"
    submission = next(
        event for event in assembly.event_ledger.read_all()
        if event.event_type == EventType.EXECUTION_SUBMISSION_RECORDED
        and event.payload.get("acceptance_status") == "rejected"
        and event.payload.get("rejection_reason") == "lease_deadline_exceeded"
    )
    attempt_id = str(submission.payload["attempt_id"])
    assembly.event_ledger.append(
        event_type=EventType.EXECUTION_SUBMISSION_RECORDED,
        object_type=submission.object_type,
        object_id=f"{submission.object_id}:duplicate",
        payload=dict(submission.payload),
        idempotency_key=f"test:duplicate-submission:{attempt_id}",
        task_id=submission.task_id,
        actor=dict(submission.actor),
        correlation_id=submission.correlation_id,
        causation_event_id=submission.causation_event_id,
        occurred_at=submission.occurred_at,
    )
    assert sum(
        event.event_type == EventType.EXECUTION_SUBMISSION_RECORDED
        and event.payload.get("attempt_id") == attempt_id
        for event in assembly.event_ledger.read_all()
    ) == 2
    key = PRE_FLASH_EXP2_ROOT_KEY
    event_path = store.system_root_directory(*key) / "events.jsonl"
    event_before = event_path.read_bytes()
    calls_before = {
        path.relative_to(store.run_dir).as_posix(): path.read_bytes()
        for path in (store.run_dir / "calls").rglob("*")
        if path.is_file()
    }
    responses_before = {
        path.relative_to(store.run_dir).as_posix(): path.read_bytes()
        for path in (store.run_dir / "responses").rglob("*")
        if path.is_file()
    }
    traces_before = {
        path.relative_to(store.run_dir).as_posix(): path.read_bytes()
        for path in (store.run_dir / "traces").rglob("*")
        if path.is_file()
    }
    monkeypatch.setattr(
        runtime,
        "call_provider_once",
        lambda **_kwargs: pytest.fail("terminal ledger recovery must not call provider"),
    )

    with pytest.raises(ValueError, match="duplicate terminal submission identity"):
        runtime.reproject_pre_flash_exp2_terminal_root_context(context)

    assert event_path.read_bytes() == event_before
    assert {
        path.relative_to(store.run_dir).as_posix(): path.read_bytes()
        for path in (store.run_dir / "calls").rglob("*")
        if path.is_file()
    } == calls_before
    assert {
        path.relative_to(store.run_dir).as_posix(): path.read_bytes()
        for path in (store.run_dir / "responses").rglob("*")
        if path.is_file()
    } == responses_before
    assert {
        path.relative_to(store.run_dir).as_posix(): path.read_bytes()
        for path in (store.run_dir / "traces").rglob("*")
        if path.is_file()
    } == traces_before
    assert not store.root_protocol_path(*key).exists()
    assert not store.root_result_path(*key).exists()


def test_pre_flash_terminal_reprojection_rejects_nonterminal_context(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2 import runtime
    from tokenshare.experiments.slim_v2.cli import _CliRootContext
    from tokenshare.experiments.slim_v2.schema import (
        PRE_FLASH_CONFIGURED_MODEL,
        PRE_FLASH_EXP2_ROOT_KEY,
        PRE_FLASH_PROVIDER_ENTRY_ID,
        PRE_FLASH_REPRESENTATIVE_RUN_ID,
        RootInventoryV1,
    )
    from tokenshare.experiments.slim_v2.storage import RunStore

    inventory = RootInventoryV1(
        experiment_id="exp2", condition_id=PRE_FLASH_EXP2_ROOT_KEY[1],
        case_id=PRE_FLASH_EXP2_ROOT_KEY[2], repeat_id=0, domain="factorization",
        difficulty="hard", topic_family=None, position_stratum="late",
        worker_count=1, mode=None, disabled_mechanisms=[], fault_type=None,
        fault_rate=None, dead_worker_count=None, kill_progress_target_ratio=None,
        provider_entry_id=PRE_FLASH_PROVIDER_ENTRY_ID,
        configured_model=PRE_FLASH_CONFIGURED_MODEL,
        planned_ai_unit_ids=["range_0"], challenge_plan_id=None,
    )
    context = _CliRootContext(
        run_id=PRE_FLASH_REPRESENTATIVE_RUN_ID, profile_id="representative",
        inventory=inventory, root_input={"case_id": inventory.case_id},
        run_store=RunStore(tmp_path / "empty"), source_run_dir=tmp_path / "empty",
        challenge_plan=None, is_reference=False, provider_entries={}, max_retries=2,
        continue_after_terminal_child_failure=False,
        protocol_execution_attempt_upper=3, provider_call_upper=0,
    )

    with pytest.raises(ValueError, match="terminal ledger"):
        runtime.reproject_pre_flash_exp2_terminal_root_context(context)
    assert not context.run_store.root_protocol_path(*PRE_FLASH_EXP2_ROOT_KEY).exists()


def test_pre_flash_resume_selection_is_exact_and_never_relaxes_other_configs(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2 import cli
    from tokenshare.experiments.slim_v2.schema import (
        LEGACY_PRICING_VERSION,
        PRE_FLASH_REPRESENTATIVE_RUN_ID,
    )
    from tokenshare.experiments.slim_v2.storage import RunStore

    store = RunStore(tmp_path / "exact")
    store.write_run_config(
        {
            "schema_version": "tokenshare.slim_v2.run_config.v1",
            "run_id": PRE_FLASH_REPRESENTATIVE_RUN_ID,
            "profile_id": "representative",
            "experiment_ids": ["exp1", "exp2", "exp3", "exp4", "exp5"],
            "source_run_dir": None,
            "exp1_provider_config_path": "benchmarks/paper/exp1_baseline_provider_config.v3.json",
            "exp5_provider_config_path": "benchmarks/paper/exp5_siliconflow_provider_config.v3.json",
            "local_secret_config_path": "local/ai_api_smoke.local.json",
            "pricing_version": LEGACY_PRICING_VERSION,
            "ordinary_parallel_backend_kind": "thread",
            "response_max_bytes": 16 * 1024 * 1024,
            "reducer_workers": 1,
        }
    )
    assert cli._is_pre_flash_representative_resume_request(
        run_id=PRE_FLASH_REPRESENTATIVE_RUN_ID,
        profile_id="representative",
        experiment_ids=("exp1", "exp2", "exp3", "exp4", "exp5"),
        source_run_dir=None,
    )
    cli._validate_pre_flash_representative_resume_config(store)
    assert not cli._is_pre_flash_representative_resume_request(
        run_id="another-run",
        profile_id="representative",
        experiment_ids=("exp1", "exp2", "exp3", "exp4", "exp5"),
        source_run_dir=None,
    )

    malformed = RunStore(tmp_path / "malformed")
    malformed.write_run_config({
        **store.read_run_config(),
        "pricing_version": "slim_v2.pricing.2026-08-23",
    })
    with pytest.raises(RuntimeError, match="exact frozen cohort"):
        cli._validate_pre_flash_representative_resume_config(malformed)


def test_protocol_only_snapshot_may_precede_tail_preparation(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2.storage import RunStore

    trace = UnitTraceV1(
        case_id="case",
        planned_ai_unit_id="range_0",
        domain="factorization",
        trace_origin="protocol",
        candidate_start=2,
        candidate_end=3,
        provider_family="deepseek",
        provider_entry_id="deepseek-entry",
        configured_model="deepseek-model",
        requested_model="deepseek-model",
        resolved_model="deepseek-model",
        attempts=[_attempt("case", "range_0")],
    )
    store = RunStore(tmp_path / "protocol-only-before-tail")
    key = ("exp1", "condition", "case", 0)
    store.write_root_protocol_snapshot(
        *key,
        protocol_result={
            "summary": {
                "runtime_observation": {
                    "unscheduled_ai_unit_ids": ["range_1"],
                }
            }
        },
        traces=[trace],
    )

    snapshot = store.read_root_protocol_snapshot(*key)
    assert snapshot.traces == (trace,)
    assert snapshot.tail_requests == {}
    assert snapshot.protocol_projection is None


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
    assert [
        (attempt.planned_ai_unit_id, attempt.attempt_ordinal, attempt.trace_origin)
        for attempt in resumed.attempts
    ] == [
        ("range_0", 0, "protocol"),
        ("range_1", 0, "protocol"),
        ("range_2", 0, "coverage_tail"),
    ]
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


def test_non_tail_exp1_protocol_resume_never_constructs_provider_or_coverage_tail(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2 import cli, provider, runtime
    from tokenshare.experiments.slim_v2.storage import RunStore
    from tests.experiments.slim_v2.test_answer_paths import (
        _PromptResponseFactory,
        _answer_path_factor_case,
        _entry,
    )
    from tests.experiments.slim_v2.test_system_vertical import _inventory

    entry = _entry(family="deepseek", model="deepseek-v4-pro")
    factory = _PromptResponseFactory(entry.configured_model)
    monkeypatch.setenv("SLIM_V2_TEST_KEY", "secret")
    monkeypatch.setattr(provider, "_open_response", factory)
    case = _answer_path_factor_case()
    inventory = replace(
        _inventory(case=case, domain="factorization"),
        condition_id="non_tail_resume",
        provider_entry_id=entry.entry_id,
        configured_model=entry.configured_model,
        planned_ai_unit_ids=["range_0", "range_1", "range_2"],
    )
    inventory.validate()
    store = RunStore(tmp_path / "run")
    context = SimpleNamespace(
        run_id="non-tail-resume",
        profile_id="representative",
        inventory=inventory,
        root_input=case,
        run_store=store,
        source_run_dir=None,
        challenge_plan=None,
        is_reference=False,
        provider_entries={entry.entry_id: entry},
        max_retries=2,
        continue_after_terminal_child_failure=True,
        protocol_execution_attempt_upper=9,
        provider_call_upper=9,
        coverage_tail_required_by_downstream=False,
    )
    monkeypatch.setattr(
        runtime,
        "run_coverage_tail",
        lambda **_kwargs: pytest.fail("non-tail root invoked coverage tail"),
    )

    result = runtime.execute_root_context(context)

    assert result.unscheduled_ai_unit_ids
    assert result.trace_tail_status == "not_required_by_downstream"
    assert result.trace_tail_target_ai_unit_ids == []
    assert result.trace_tail_recorded_ai_unit_ids == []
    assert result.trace_tail_provider_attempt_count == 0
    assert result.trace_tail_total_tokens == 0
    assert result.trace_tail_cost_estimate_cny == 0.0
    assert cli._protocol_tail_pending(store, inventory) is False
    assert cli._protocol_tail_requires_provider(store, inventory) is False

    protocol = store.read_root_protocol(
        inventory.experiment_id,
        inventory.condition_id,
        inventory.case_id,
        inventory.repeat_id,
    )
    monkeypatch.setattr(
        runtime,
        "_provider_entry",
        lambda _context: pytest.fail("non-tail resume constructed provider entry"),
    )
    resumed = runtime.resume_exp1_root_context(context, protocol)

    assert resumed == result
    assert len(factory.responses) == len(result.attempts)


def test_non_tail_blocked_exp1_persists_policy_and_resumes_without_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """设施阻断不改变非来源root的明确not-required政策事实。"""

    from tokenshare.experiments.slim_v2 import cli, provider, runtime
    from tokenshare.experiments.slim_v2.storage import RunStore
    from tests.experiments.slim_v2.test_answer_paths import (
        _answer_path_factor_case,
        _entry,
    )
    from tests.experiments.slim_v2.test_system_vertical import _inventory

    entry = _entry(family="deepseek", model="deepseek-v4-pro")
    monkeypatch.delenv("SLIM_V2_TEST_KEY", raising=False)
    monkeypatch.setattr(
        provider,
        "_open_response",
        lambda *_args, **_kwargs: pytest.fail(
            "blocked non-tail root reached provider transport"
        ),
    )
    case = _answer_path_factor_case()
    inventory = replace(
        _inventory(case=case, domain="factorization"),
        condition_id="blocked_non_tail_resume",
        provider_entry_id=entry.entry_id,
        configured_model=entry.configured_model,
        planned_ai_unit_ids=["range_0", "range_1", "range_2"],
    )
    inventory.validate()
    store = RunStore(tmp_path / "run")
    context = SimpleNamespace(
        run_id="blocked-non-tail-resume",
        profile_id="representative",
        inventory=inventory,
        root_input=case,
        run_store=store,
        source_run_dir=None,
        challenge_plan=None,
        is_reference=False,
        provider_entries={entry.entry_id: entry},
        max_retries=2,
        continue_after_terminal_child_failure=True,
        protocol_execution_attempt_upper=9,
        provider_call_upper=9,
        coverage_tail_required_by_downstream=False,
    )
    monkeypatch.setattr(
        runtime,
        "run_coverage_tail",
        lambda **_kwargs: pytest.fail("blocked non-tail root invoked coverage tail"),
    )

    result = runtime.execute_root_context(context)

    assert result.failure_kind == "infrastructure_invalid"
    assert result.failure_origin == "provider_configuration_invalid"
    assert result.trace_tail_status == "not_required_by_downstream"
    assert result.trace_tail_target_ai_unit_ids == []
    assert result.trace_tail_recorded_ai_unit_ids == []
    assert result.trace_tail_wall_clock_ms == 0
    assert result.trace_tail_provider_attempt_count == 0
    assert result.trace_tail_total_tokens == 0
    assert result.trace_tail_cost_estimate_cny == 0.0
    assert cli._protocol_tail_pending(store, inventory) is False
    assert cli._protocol_tail_requires_provider(store, inventory) is False

    protocol = store.read_root_protocol(
        inventory.experiment_id,
        inventory.condition_id,
        inventory.case_id,
        inventory.repeat_id,
    )
    monkeypatch.setattr(
        runtime,
        "_provider_entry",
        lambda _context: pytest.fail("blocked non-tail resume constructed provider entry"),
    )
    assert runtime.resume_exp1_root_context(context, protocol) == result


def test_completed_factor_tail_resume_matches_fresh_attempts_and_resources(
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
        condition_id="task5_completed_tail_resume",
        provider_entry_id=entry.entry_id,
        configured_model=model,
        planned_ai_unit_ids=["range_0", "range_1", "range_2"],
    )
    inventory.validate()
    store = RunStore(tmp_path / "run")
    context = SimpleNamespace(
        run_id="task5-completed-tail",
        profile_id="representative",
        inventory=inventory,
        root_input=case,
        source_run_dir=None,
        challenge_plan=None,
        is_reference=False,
        provider_entries={entry.entry_id: entry},
        max_retries=2,
        continue_after_terminal_child_failure=True,
        protocol_execution_attempt_upper=9,
        provider_call_upper=9,
        run_store=store,
        coverage_tail_required_by_downstream=True,
    )

    fresh = runtime.execute_root_context(context)
    call_count = len(factory.responses)
    assert call_count == 3
    protocol_snapshot = store.read_root_protocol(
        inventory.experiment_id,
        inventory.condition_id,
        inventory.case_id,
        inventory.repeat_id,
    )
    resumed = runtime.resume_exp1_root_context(context, protocol_snapshot)

    assert [
        (attempt.planned_ai_unit_id, attempt.attempt_ordinal, attempt.trace_origin)
        for attempt in fresh.attempts
    ] == [
        ("range_0", 0, "protocol"),
        ("range_1", 0, "protocol"),
        ("range_2", 0, "coverage_tail"),
    ]
    assert asdict(resumed) == asdict(fresh)
    assert len(factory.responses) == call_count


def test_normal_parse_exhaustion_completes_unscheduled_coverage_tail(
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
        "requested_child_count": 3,
    }
    entry = _entry(family="deepseek", model="deepseek-v4-pro")
    inventory = replace(
        _inventory(case=case, domain="factorization"),
        condition_id="task6_started_runtime_failure",
        provider_entry_id=entry.entry_id,
        configured_model=entry.configured_model,
        planned_ai_unit_ids=["range_0", "range_1", "range_2"],
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
        provider_call_upper=9,
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
    result = runtime.execute_root_context(context)

    assert result.protocol_started is True
    assert result.root_status == "failed"
    assert result.failure_stage == "candidate_acquisition"
    assert result.failure_kind == "no_final"
    assert result.failure_origin == "model_parse_exhausted"
    assert len(responses) == 9
    assert result.planned_ai_unit_ids == ["range_0", "range_1", "range_2"]
    assert result.dispatched_ai_unit_ids == ["range_0"]
    assert result.completed_ai_unit_ids == []
    assert result.unscheduled_ai_unit_ids == ["range_1", "range_2"]
    assert len(result.attempts) == 9
    assert [
        (attempt.planned_ai_unit_id, attempt.attempt_ordinal, attempt.trace_origin)
        for attempt in result.attempts
    ] == [
        *(("range_0", ordinal, "protocol") for ordinal in range(3)),
        *(("range_1", ordinal, "coverage_tail") for ordinal in range(3)),
        *(("range_2", ordinal, "coverage_tail") for ordinal in range(3)),
    ]
    assert result.trace_tail_target_ai_unit_ids == ["range_1", "range_2"]
    assert result.trace_tail_recorded_ai_unit_ids == ["range_1", "range_2"]
    assert result.trace_tail_failure_unit_count == 2
    snapshot = store.read_root_protocol_snapshot(
        inventory.experiment_id,
        inventory.condition_id,
        inventory.case_id,
        inventory.repeat_id,
    )
    assert snapshot.protocol_result["summary"]["terminal_failure"] == {
        "failure_stage": "candidate_acquisition",
        "failure_origin": "model_parse_exhausted",
        "infrastructure_invalid": False,
    }
    assert "slim_runtime_failure" not in snapshot.protocol_result["summary"]
    from tokenshare.experiments.slim_v2.storage import select_trace_attempt

    traces = {
        planned: store.read_trace(inventory.case_id, 0, planned)
        for planned in inventory.planned_ai_unit_ids
    }
    assert [attempt.attempt_ordinal for attempt in traces["range_0"].attempts] == [
        0, 1, 2,
    ]
    assert all(traces[planned].trace_origin == "coverage_tail" for planned in ("range_1", "range_2"))
    assert all(select_trace_attempt(trace, 0).attempt is not None for trace in traces.values())


def test_lean_parse_exhaustion_completes_unscheduled_coverage_tail(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2 import cli, provider, runtime
    from tokenshare.experiments.slim_v2.execution import FixedTraceSubmissionAdapter
    from tokenshare.experiments.slim_v2.storage import RunStore, select_trace_attempt
    from tokenshare.plugins.lean_proof.runtime_adapter import LeanRuntimeAdapter
    from tokenshare.storage.artifacts import ArtifactStore
    from tests.experiments.slim_v2.test_answer_paths import (
        _FakeResponse,
        _entry,
        _response_body,
    )
    from tests.experiments.slim_v2.test_system_vertical import (
        _inventory,
        _lean_case,
        _test_lean_environment,
    )
    from tests.support.lean_checker import RecordingLeanChecker

    case = _lean_case("lean_v2_medium_lemma_dag_01")
    planned = [str(node["node_id"]) for node in case["lemma_graph"]["nodes"]]
    entry = _entry(family="deepseek", model="deepseek-v4-pro")
    inventory = replace(
        _inventory(case=case, domain="lean"),
        condition_id="lean_parse_tail",
        provider_entry_id=entry.entry_id,
        configured_model=entry.configured_model,
        planned_ai_unit_ids=planned,
    )
    inventory.validate()
    store = RunStore(tmp_path / "lean-parse-tail")
    checker = RecordingLeanChecker()
    runtimes: list[LeanRuntimeAdapter] = []

    def plugin_runtime(*, context, protocol_config, provider_family):
        adapter = LeanRuntimeAdapter(
            provider_family=provider_family,
            seed=20260820,
            protocol_config=protocol_config,
            created_at="2026-08-22T00:00:00Z",
            environment_manifest=_test_lean_environment(),
            checker=checker,
        )
        runtimes.append(adapter)
        return adapter

    responses: list[_FakeResponse] = []

    def invalid_response(request: object, timeout_seconds: float) -> _FakeResponse:
        response = _FakeResponse(
            _response_body(content="{}", model=entry.configured_model)
        )
        responses.append(response)
        return response

    monkeypatch.setenv("SLIM_V2_TEST_KEY", "secret")
    monkeypatch.setattr(provider, "_open_response", invalid_response)
    monkeypatch.setattr(runtime, "_plugin_runtime", plugin_runtime)
    monkeypatch.setattr(
        runtime,
        "check_lean_proof",
        lambda *_args, **_kwargs: pytest.fail("parse failure reached Lean tail checker"),
    )
    context = SimpleNamespace(
        run_id="lean-parse-tail",
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
        provider_call_upper=3 * len(planned),
    )
    result = runtime.execute_root_context(context)

    assert result.failure_kind == "no_final"
    assert result.failure_origin == "model_parse_exhausted"
    assert result.unscheduled_ai_unit_ids == planned[1:]
    assert result.trace_tail_target_ai_unit_ids == planned[1:]
    assert result.trace_tail_recorded_ai_unit_ids == planned[1:]
    assert result.trace_tail_provider_attempt_count == 6
    assert len(responses) == 9
    assert checker.modes == []
    traces = {
        item: store.read_trace(inventory.case_id, 0, item) for item in planned
    }
    assert all(
        select_trace_attempt(trace, 0).attempt is not None
        for trace in traces.values()
    )
    dependency_paths = {
        planned[2]: [planned[0], planned[1], planned[2]],
        planned[4]: planned,
    }
    for item, dependency_path in dependency_paths.items():
        trace = traces[item]
        assert trace.trace_origin == "coverage_tail"
        assert trace.lemma_node_id == item
        assert trace.dependency_path == dependency_path
        assert len(trace.attempts) == 1
        attempt = trace.attempts[0]
        assert attempt.attempt_ordinal == 0
        assert attempt.provider_call_made is False
        assert attempt.result_kind == "pre_dispatch_failure"
        assert attempt.parse_result == "not_reached"
        assert attempt.checker_result == "not_reached"
    provider_attempts = [
        attempt
        for trace in traces.values()
        for attempt in trace.attempts
        if trace.trace_origin == "coverage_tail"
        and attempt.provider_call_made is True
    ]
    assert result.trace_tail_provider_attempt_count == len(provider_attempts)
    assert result.trace_tail_total_tokens == sum(
        int(attempt.total_tokens) for attempt in provider_attempts
    )
    expected_cost = (
        sum(float(attempt.cost_estimate_cny) for attempt in provider_attempts)
        if all(
            attempt.cost_estimate_cny is not None for attempt in provider_attempts
        )
        else None
    )
    assert result.trace_tail_cost_estimate_cny == expected_cost
    assert runtimes[0].dependency_path_for_planned_unit(planned[2]) == (
        dependency_paths[planned[2]]
    )

    snapshot = store.read_root_protocol_snapshot(
        inventory.experiment_id,
        inventory.condition_id,
        inventory.case_id,
        inventory.repeat_id,
    )
    source_request = runtime._execution_request_from_document(
        snapshot.tail_requests[planned[1]]
    )
    source_request = replace(
        source_request,
        request_id="exp2-predispatch-source-request",
        attempt_id="exp2-predispatch-source-attempt",
        attempt_ordinal=0,
        soft_hints={
            **dict(source_request.soft_hints or {}),
            "planned_ai_unit_id": planned[2],
            "lemma_node_id": planned[2],
            "dependency_path": dependency_paths[planned[2]],
        },
    )
    fixed = FixedTraceSubmissionAdapter(
        source_store=store,
        artifact_store=ArtifactStore(
            store.system_root_directory(
                inventory.experiment_id,
                inventory.condition_id,
                inventory.case_id,
                inventory.repeat_id,
            )
        ),
        case_id=inventory.case_id,
        domain="lean",
        provider_entry_id=entry.entry_id,
        configured_model=entry.configured_model,
        experiment_id="exp2",
    )
    source_submission = fixed.execute(
        source_request,
        submission_id="exp2-predispatch-source-submission",
        submitted_at="2026-08-22T00:00:00Z",
    )
    assert source_submission.usage_summary["provider_call_made"] is False
    assert source_submission.usage_summary["source_result_kind"] == (
        "pre_dispatch_failure"
    )
    assert fixed.attempts[0].provider_call_made is False
    assert fixed.attempts[0].source_lemma_node_id == planned[2]
    assert fixed.attempts[0].source_dependency_path == dependency_paths[planned[2]]

    invalid_trace = replace(
        traces[planned[2]],
        attempts=[replace(traces[planned[2]].attempts[0], provider_call_made=True)],
    )
    with pytest.raises(ValueError, match="coverage-tail trace is invalid"):
        RunStore(tmp_path / "invalid-mixed-tail").write_root_protocol_snapshot(
            inventory.experiment_id,
            inventory.condition_id,
            inventory.case_id,
            inventory.repeat_id,
            protocol_result=snapshot.protocol_result,
            traces=[invalid_trace],
            tail_requests={},
        )

    crash_store = RunStore(tmp_path / "snapshot-only-predispatch-tail")
    protocol_summary = dict(snapshot.protocol_result["summary"])
    protocol_summary["runtime_observation"] = {
        **dict(protocol_summary["runtime_observation"]),
        "unscheduled_ai_unit_ids": [planned[2]],
    }
    crash_store.write_root_protocol_snapshot(
        inventory.experiment_id,
        inventory.condition_id,
        inventory.case_id,
        inventory.repeat_id,
        protocol_result={
            **dict(snapshot.protocol_result),
            "summary": protocol_summary,
        },
        traces=[traces[planned[2]]],
        tail_requests={},
    )
    assert cli._protocol_tail_pending(crash_store, inventory) is True
    assert cli._protocol_tail_requires_provider(crash_store, inventory) is False

    for item in planned[1:]:
        store.trace_path(inventory.case_id, 0, item).unlink()
    response_count = len(responses)
    resumed = runtime.resume_exp1_root_context(
        context,
        store.read_root_protocol(
            inventory.experiment_id,
            inventory.condition_id,
            inventory.case_id,
            inventory.repeat_id,
        ),
    )
    assert len(responses) == response_count
    assert resumed.trace_tail_target_ai_unit_ids == planned[1:]
    assert resumed.trace_tail_recorded_ai_unit_ids == planned[1:]
    assert all(
        select_trace_attempt(
            store.read_trace(inventory.case_id, 0, item),
            0,
        ).attempt
        is not None
        for item in planned
    )


def test_lean_checker_infrastructure_terminal_blocks_coverage_tail() -> None:
    from tokenshare.experiments.slim_v2.runtime import coverage_tail_blocked

    assert coverage_tail_blocked(
        {
            "terminal_failure": {
                "failure_stage": "candidate_verification",
                "failure_origin": "checker_environment_error",
                "infrastructure_invalid": True,
            }
        }
    ) is True


@pytest.mark.parametrize(
    "blocked_summary",
    [
        {
            "slim_runtime_failure": {
                "failure_stage": "runtime",
                "failure_origin": "unexpected_runtime_error",
            }
        },
        {
            "terminal_failure": {
                "failure_stage": "candidate_verification",
                "failure_origin": "checker_environment_error",
                "infrastructure_invalid": True,
            }
        },
    ],
)
def test_infrastructure_snapshot_resume_never_requires_provider(
    blocked_summary: dict[str, object],
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2 import cli
    from tokenshare.experiments.slim_v2.storage import RunStore
    from tests.experiments.slim_v2.test_answer_paths import _answer_path_factor_case
    from tests.experiments.slim_v2.test_system_vertical import _inventory

    inventory = replace(
        _inventory(case=_answer_path_factor_case(), domain="factorization"),
        condition_id="blocked_snapshot_resume",
    )
    store = RunStore(tmp_path / "blocked-snapshot-resume")
    store.write_root_protocol_snapshot(
        inventory.experiment_id,
        inventory.condition_id,
        inventory.case_id,
        inventory.repeat_id,
        protocol_result={"summary": blocked_summary},
        traces=[],
        tail_requests={},
    )

    assert cli._protocol_tail_pending(store, inventory) is False
    assert cli._protocol_tail_requires_provider(store, inventory) is False


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
