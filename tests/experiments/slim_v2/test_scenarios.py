"""Slim V2 Experiment 2--4 场景接线的风险驱动验证。"""

from __future__ import annotations

import json
from dataclasses import replace
from math import ceil
from pathlib import Path
from typing import Callable

import pytest

from tokenshare.core.models import ArtifactRef
from tokenshare.experiments.slim_v2.execution import ProviderSubmissionAdapter
from tokenshare.experiments.slim_v2.profiles import ChallengePlanV1
from tokenshare.experiments.slim_v2.projector import RootProjectionError, project_root_result
from tokenshare.experiments.slim_v2.runtime import (
    RootAssembly,
    materialize_protocol_traces,
    run_root_slice,
)
from tokenshare.experiments.slim_v2.scenarios import (
    EXP4_MODE_DISABLED_MECHANISMS,
    build_challenge_plan,
    build_exp3_reference,
    build_scenario,
)
from tokenshare.experiments.slim_v2.schema import (
    AblationObservationV1,
    RootInventoryV1,
    SchemaValidationError,
)
from tokenshare.experiments.slim_v2.storage import RunStore
from tokenshare.local_runtime import NoOpRuntimeHooks, SequentialWorkerBackend, ThreadWorkerBackend
from tokenshare.local_runtime.contracts import RecoveryMergeContext
from tokenshare.plugins.contracts import IncompleteMergeInputError
from tokenshare.plugins.factorization.runtime_adapter import (
    FactorizationExecutionBridge,
    FactorizationRuntimeAdapter,
)
from tokenshare.plugins.lean_proof.runtime_adapter import (
    LeanExecutionBridge,
    LeanRuntimeAdapter,
)
from tokenshare.plugins.lean_proof.checker import LeanCheckerStatus
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType
from tests.experiments.slim_v2.test_answer_paths import (
    _PromptResponseFactory,
    _answer_path_factor_case,
    _entry,
)
from tests.experiments.slim_v2.test_system_vertical import (
    NOW,
    _Clock,
    _ObservationClock,
    _config,
    _lean_case,
    _test_lean_environment,
)
from tests.support.lean_checker import RecordingLeanChecker


class _ContentAwareRecordingLeanChecker(RecordingLeanChecker):
    def __init__(self) -> None:
        super().__init__()
        self.statuses: list[LeanCheckerStatus] = []

    def __call__(self, request, *, artifact_store, environment_manifest):
        proof_body = json.loads(
            artifact_store.read_bytes(request.proof_candidate_ref).decode("utf-8")
        )
        self.status = (
            LeanCheckerStatus.REJECTED
            if "nonexistent_slim_v2_identifier" in str(proof_body.get("proof_source"))
            else LeanCheckerStatus.ACCEPTED
        )
        report = super().__call__(
            request,
            artifact_store=artifact_store,
            environment_manifest=environment_manifest,
        )
        self.statuses.append(report.status)
        return report


def test_exp4_structural_contract_types_are_explicit() -> None:
    assert issubclass(IncompleteMergeInputError, ValueError)
    assert RecoveryMergeContext.__dataclass_fields__["recovered_attempt_id"]
    assert not hasattr(NoOpRuntimeHooks(), "before_recovery_merge")

    AblationObservationV1(
        disabled_mechanism="verification",
        root_checker_reached=False,
        root_check_passed=None,
        root_checker_call_count=0,
    ).validate()
    with pytest.raises(SchemaValidationError):
        AblationObservationV1(
            disabled_mechanism="verification",
            root_checker_reached=False,
            root_check_passed=None,
            root_checker_call_count=None,
        ).validate()


def _inventory(
    *,
    experiment_id: str,
    case: dict[str, object],
    domain: str,
    worker_count: int = 10,
    fault_type: str | None = None,
    fault_rate: float | None = None,
    dead_worker_count: int | None = None,
    kill_progress_target_ratio: float | None = None,
    mode: str | None = None,
    challenge_plan_id: str | None = None,
) -> RootInventoryV1:
    planned = (
        [f"range_{index}" for index in range(int(case["split_params"]["requested_child_count"]))]
        if domain == "factorization"
        else list(case["merge_plan_shape"]["dependency_order"])
    )
    row = RootInventoryV1(
        experiment_id=experiment_id,
        condition_id="opaque-condition-without-behaviour",
        case_id=str(case["case_id"]),
        repeat_id=1,
        domain=domain,
        difficulty=str(case.get("paper_difficulty", case.get("difficulty"))),
        topic_family=str(case["topic_family"]) if domain == "lean" else None,
        position_stratum="middle" if experiment_id == "exp2" else None,
        worker_count=worker_count,
        mode=mode,
        disabled_mechanisms=list(EXP4_MODE_DISABLED_MECHANISMS.get(mode, ())),
        fault_type=fault_type,
        fault_rate=fault_rate,
        dead_worker_count=dead_worker_count,
        kill_progress_target_ratio=kill_progress_target_ratio,
        provider_entry_id="deepseek-entry",
        configured_model="deepseek-v4-pro",
        planned_ai_unit_ids=planned,
        challenge_plan_id=challenge_plan_id,
    )
    row.validate()
    return row


def _acquire_factor_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, object], RunStore, _PromptResponseFactory]:
    from tokenshare.experiments.slim_v2 import provider

    case = _answer_path_factor_case()
    factory = _PromptResponseFactory("deepseek-v4-pro")
    monkeypatch.setenv("SLIM_V2_TEST_KEY", "fake-only")
    monkeypatch.setattr(provider, "_open_response", factory)
    monkeypatch.setattr(provider, "_elapsed_ms", lambda _started_ns: 100)
    artifact_store = ArtifactStore(tmp_path / "source-factor-system")
    run_store = RunStore(tmp_path / "source-run")
    config = replace(_config("source-factor"), max_retries=2)
    runtime = FactorizationRuntimeAdapter(
        provider_family="deepseek", seed=7, protocol_config=config, created_at=NOW
    )
    adapter = ProviderSubmissionAdapter(
        entry=_entry(family="deepseek", model="deepseek-v4-pro"),
        artifact_store=artifact_store,
        run_store=run_store,
        root_key=("exp1", "source", str(case["case_id"]), 0),
        domain="factorization",
    )
    clock = _Clock()
    assembly = RootAssembly(
        run_id="source-factor",
        root_input=case,
        protocol_config=config,
        artifact_store=artifact_store,
        event_ledger=EventLedger(tmp_path / "source-factor-system" / "events.jsonl"),
        plugin_runtime=runtime,
        worker_backend=ThreadWorkerBackend(
            executor=FactorizationExecutionBridge(
                plugin_runtime=runtime,
                range_executor=adapter,
            ),
            capacity=3,
            submitted_at=clock,
        ),
        now=clock,
        observation_clock=_ObservationClock(),
    )
    result = run_root_slice(assembly)
    assert result.status == "completed"
    materialize_protocol_traces(
        store=run_store,
        root_key=("exp1", "source", str(case["case_id"]), 0),
        protocol_result=result,
        submission_adapter=adapter,
        event_ledger=assembly.event_ledger,
    )
    assert {item.planned_ai_unit_id for item in adapter.attempts} == {
        "range_0",
        "range_1",
        "range_2",
    }
    return case, run_store, factory


def _acquire_lean_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, object], RunStore, _PromptResponseFactory]:
    from tokenshare.experiments.slim_v2 import provider

    case = _lean_case("lean_v2_medium_lemma_dag_01")
    factory = _PromptResponseFactory("deepseek-v4-pro")
    monkeypatch.setenv("SLIM_V2_TEST_KEY", "fake-only")
    monkeypatch.setattr(provider, "_open_response", factory)
    monkeypatch.setattr(provider, "_elapsed_ms", lambda _started_ns: 100)
    artifact_store = ArtifactStore(tmp_path / "source-lean-system")
    run_store = RunStore(tmp_path / "source-lean-run")
    config = replace(_config("source-lean"), max_retries=2)
    runtime = LeanRuntimeAdapter(
        provider_family="deepseek",
        environment_manifest=_test_lean_environment(),
        checker=RecordingLeanChecker(),
        protocol_config=config,
        created_at=NOW,
    )
    adapter = ProviderSubmissionAdapter(
        entry=_entry(family="deepseek", model="deepseek-v4-pro"),
        artifact_store=artifact_store,
        run_store=run_store,
        root_key=("exp1", "source", str(case["case_id"]), 0),
        domain="lean",
    )
    clock = _Clock()
    assembly = RootAssembly(
        run_id="source-lean",
        root_input=case,
        protocol_config=config,
        artifact_store=artifact_store,
        event_ledger=EventLedger(tmp_path / "source-lean-system" / "events.jsonl"),
        plugin_runtime=runtime,
        worker_backend=SequentialWorkerBackend(
            executor=LeanExecutionBridge(
                plugin_runtime=runtime,
                proof_candidate_executor=adapter,
            ),
            submitted_at=clock,
        ),
        now=clock,
        observation_clock=_ObservationClock(),
    )
    result = run_root_slice(assembly)
    assert result.status == "completed"
    materialize_protocol_traces(
        store=run_store,
        root_key=("exp1", "source", str(case["case_id"]), 0),
        protocol_result=result,
        submission_adapter=adapter,
        event_ledger=assembly.event_ledger,
    )
    return case, run_store, factory


def _replace_natural_source_attempt_with_failure(
    *,
    source_store: RunStore,
    case_id: str,
    planned_ai_unit_id: str,
) -> None:
    trace = source_store.read_trace(case_id, 0, planned_ai_unit_id)
    source = trace.attempts[0]
    missing_reason = dict(source.missing_reason)
    for field_name in (
        "raw_response_relative_path",
        "parse_result",
        "verifier_result",
        "checker_result",
    ):
        missing_reason[f"attempts[].{field_name}"] = "source_attempt_failed"
    failed = replace(
        source,
        result_kind="source_attempt_failed",
        provider_latency_ms=0,
        raw_response_present=False,
        raw_response_relative_path=None,
        parse_result=None,
        verifier_result=None,
        checker_result=None,
        canonical_accepted=False,
        missing_reason=missing_reason,
    )
    failed_trace = replace(trace, attempts=[failed])
    failed_trace.validate()
    source_store.trace_path(case_id, 0, planned_ai_unit_id).unlink()
    source_store.write_trace(failed_trace)


def _run_factor_scenario(
    tmp_path: Path,
    *,
    inventory: RootInventoryV1,
    case: dict[str, object],
    source_store: RunStore,
    challenge_plan: ChallengePlanV1 | None = None,
    process_timeout_seconds: float = 30.0,
    before_run: Callable[[object], None] | None = None,
):
    root = tmp_path / f"{inventory.experiment_id}-{inventory.mode or inventory.fault_type or inventory.worker_count}"
    artifact_store = ArtifactStore(root)
    retries = 1 if inventory.experiment_id == "exp4" else 2
    config = replace(_config(root.name), max_retries=retries)
    runtime = FactorizationRuntimeAdapter(
        provider_family="deepseek", seed=7, protocol_config=config, created_at=NOW
    )
    clock = _Clock()
    scenario = build_scenario(
        inventory=inventory,
        root_input=case,
        source_store=source_store,
        artifact_store=artifact_store,
        plugin_runtime=runtime,
        protocol_config=config,
        submitted_at=clock,
        challenge_plan=challenge_plan,
        process_timeout_seconds=process_timeout_seconds,
    )
    assembly = RootAssembly(
        run_id=f"run-{root.name}",
        root_input=case,
        protocol_config=config,
        artifact_store=artifact_store,
        event_ledger=EventLedger(root / "events.jsonl"),
        plugin_runtime=runtime,
        worker_backend=scenario.worker_backend,
        now=clock,
        observation_clock=_ObservationClock(),
        mechanism_policy=scenario.mechanism_policy,
        submission_adapter=scenario.submission_adapter,
        hooks=scenario.hooks,
        logical_scheduler=scenario.logical_scheduler,
        trace_delay_policy="logical_source_latency_1x",
        scenario=scenario,
    )
    if before_run is not None:
        before_run(scenario)
    protocol = run_root_slice(assembly)
    projected = project_root_result(
        inventory=inventory,
        assembly=assembly,
        protocol_result=protocol,
        provider_family="deepseek",
        requested_model="deepseek-v4-pro",
        resolved_model="deepseek-v4-pro",
        reasoning_mode="thinking",
        attempts=scenario.submission_adapter.attempts,
        scenario=scenario,
    )
    return scenario, assembly, protocol, projected


def _run_lean_scenario(
    tmp_path: Path,
    *,
    inventory: RootInventoryV1,
    case: dict[str, object],
    source_store: RunStore,
    challenge_plan: ChallengePlanV1 | None = None,
    checker: RecordingLeanChecker | None = None,
    process_timeout_seconds: float = 30.0,
):
    root = tmp_path / (
        f"{inventory.experiment_id}-"
        f"{inventory.mode or inventory.fault_type or inventory.worker_count}"
    )
    artifacts = ArtifactStore(root)
    config = replace(
        _config(root.name),
        max_retries=1 if inventory.experiment_id == "exp4" else 2,
    )
    checker = checker or RecordingLeanChecker()
    runtime = LeanRuntimeAdapter(
        provider_family="deepseek",
        environment_manifest=_test_lean_environment(),
        checker=checker,
        protocol_config=config,
        created_at=NOW,
    )
    clock = _Clock()
    scenario = build_scenario(
        inventory=inventory,
        root_input=case,
        source_store=source_store,
        artifact_store=artifacts,
        plugin_runtime=runtime,
        protocol_config=config,
        submitted_at=clock,
        challenge_plan=challenge_plan,
        process_timeout_seconds=process_timeout_seconds,
    )
    assembly = RootAssembly(
        run_id=f"run-{root.name}",
        root_input=case,
        protocol_config=config,
        artifact_store=artifacts,
        event_ledger=EventLedger(root / "events.jsonl"),
        plugin_runtime=runtime,
        worker_backend=scenario.worker_backend,
        now=clock,
        observation_clock=_ObservationClock(),
        mechanism_policy=scenario.mechanism_policy,
        submission_adapter=scenario.submission_adapter,
        hooks=scenario.hooks,
        logical_scheduler=scenario.logical_scheduler,
        trace_delay_policy="logical_source_latency_1x",
        scenario=scenario,
    )
    protocol = run_root_slice(assembly)
    row = project_root_result(
        inventory=inventory,
        assembly=assembly,
        protocol_result=protocol,
        provider_family="deepseek",
        requested_model="deepseek-v4-pro",
        resolved_model="deepseek-v4-pro",
        reasoning_mode="thinking",
        attempts=scenario.submission_adapter.attempts,
        scenario=scenario,
    )
    return scenario, assembly, protocol, row, checker


def test_exp2_six_workers_use_thread_facts_logical_time_and_early_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case, source_store, transport = _acquire_factor_source(tmp_path, monkeypatch)
    source_transport_count = len(transport.responses)
    rows = []
    for worker_count in (1, 3, 7, 10, 30, 50):
        inventory = _inventory(
            experiment_id="exp2",
            case=case,
            domain="factorization",
            worker_count=worker_count,
        )
        rows.append(
            _run_factor_scenario(
                tmp_path / f"worker-{worker_count}",
                inventory=inventory,
                case=case,
                source_store=source_store,
            )[-1]
        )

    assert len(transport.responses) == source_transport_count
    assert all(row.verified_correct for row in rows)
    assert all(row.attempts and all(not item.provider_call_made for item in row.attempts) for row in rows)
    assert rows[0].unscheduled_ai_unit_ids == ["range_2"]
    assert rows[0].runtime_wall_clock_ms > rows[1].runtime_wall_clock_ms
    assert [row.worker_count for row in rows] == [1, 3, 7, 10, 30, 50]
    assert all(row.worker_execution_facts for row in rows)
    assert all(row.observed_peak_concurrency <= row.worker_count for row in rows)


def test_thread_backend_preserves_lean_fixed_dag_checker_and_canonical_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case, source_store, _transport = _acquire_lean_source(tmp_path, monkeypatch)
    inventory = _inventory(
        experiment_id="exp2", case=case, domain="lean", worker_count=3
    )
    root = tmp_path / "lean-thread"
    artifacts = ArtifactStore(root)
    config = replace(_config("lean-thread"), max_retries=2)
    checker = RecordingLeanChecker()
    runtime = LeanRuntimeAdapter(
        provider_family="deepseek",
        environment_manifest=_test_lean_environment(),
        checker=checker,
        protocol_config=config,
        created_at=NOW,
    )
    clock = _Clock()
    scenario = build_scenario(
        inventory=inventory,
        root_input=case,
        source_store=source_store,
        artifact_store=artifacts,
        plugin_runtime=runtime,
        protocol_config=config,
        submitted_at=clock,
    )
    assembly = RootAssembly(
        run_id="lean-thread",
        root_input=case,
        protocol_config=config,
        artifact_store=artifacts,
        event_ledger=EventLedger(root / "events.jsonl"),
        plugin_runtime=runtime,
        worker_backend=scenario.worker_backend,
        now=clock,
        observation_clock=_ObservationClock(),
        submission_adapter=scenario.submission_adapter,
        hooks=scenario.hooks,
        logical_scheduler=scenario.logical_scheduler,
        trace_delay_policy="logical_source_latency_1x",
    )
    protocol = run_root_slice(assembly)
    events = assembly.event_ledger.read_all()
    projected = project_root_result(
        inventory=inventory,
        assembly=assembly,
        protocol_result=protocol,
        provider_family="deepseek",
        requested_model="deepseek-v4-pro",
        resolved_model="deepseek-v4-pro",
        reasoning_mode="thinking",
        attempts=scenario.submission_adapter.attempts,
        scenario=scenario,
    )

    assert isinstance(scenario.worker_backend, ThreadWorkerBackend)
    assert projected.verified_correct
    assert len(projected.attempts) == len(inventory.planned_ai_unit_ids)
    assert all(item.checker_result == "accepted" for item in projected.attempts)
    assert sum(event.event_type == EventType.CANONICAL_OUTPUTS_BOUND for event in events) >= len(projected.attempts)
    assert checker.requests


@pytest.mark.parametrize(
    "fault_type",
    ["false_positive", "false_negative", "no_return", "late_submission", "executor_error"],
)
def test_exp3_rate_faults_are_ordinal_zero_once_and_replacements_are_unmodified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault_type: str,
) -> None:
    case, source_store, transport = _acquire_factor_source(tmp_path, monkeypatch)
    source_transport_count = len(transport.responses)
    inventory = _inventory(
        experiment_id="exp3",
        case=case,
        domain="factorization",
        fault_type=fault_type,
        fault_rate=0.34,
    )
    scenario, _assembly, _protocol, row = _run_factor_scenario(
        tmp_path,
        inventory=inventory,
        case=case,
        source_store=source_store,
    )

    assert len(transport.responses) == source_transport_count
    assert scenario.provider_call_count == 0
    assert row.fault_target_count == 2
    assert len(row.fault_target_planned_ai_unit_ids) == 2
    assert row.fault_observations
    assert all(item.injected for item in row.fault_observations)
    assert all(
        attempt.attempt_ordinal == 0
        for attempt in row.attempts
        if attempt.planned_ai_unit_id in row.fault_target_planned_ai_unit_ids
        and attempt.result_kind in {fault_type, "fault_injected"}
    )
    assert any(item.replacement_started for item in row.recovery_observations)
    assert all(item.provider_call_made is False for item in row.attempts)
    assert all(item.simulated_latency_ms is not None for item in row.attempts)
    assert all(item.source_reasoning_tokens <= item.source_completion_tokens for item in row.attempts)
    if fault_type in {"false_positive", "false_negative"}:
        records = [
            item
            for item in scenario.hooks.injection_records
            if item["kind"] == fault_type
        ]
        assert records
        observations = {
            item.attempt_id: item
            for item in row.fault_observations
            if item.fault_type == fault_type
        }
        for record in records:
            observation = observations[record["attempt_id"]]
            assert observation.reached_verification is True
            assert observation.verifier_intercepted is True
            if fault_type == "false_positive":
                assert record["candidate_content_before"] != record["candidate_content_after"]
                assert observation.independently_wrong is True
            else:
                assert record["candidate_content_before"] == record["candidate_content_after"]
                assert record["candidate_output_refs_before"] == record["candidate_output_refs_after"]
                assert observation.independently_wrong is False
                replacement = next(
                    item
                    for item in row.attempts
                    if item.replacement_of_attempt_id == record["attempt_id"]
                )
                original = next(
                    item for item in row.attempts if item.attempt_id == record["attempt_id"]
                )
                assert replacement.source_response_slot_id == original.source_response_slot_id
                assert not any(
                    item["attempt_id"] == replacement.attempt_id
                    for item in records
                )


@pytest.mark.parametrize("fault_type", ["false_positive", "false_negative"])
def test_exp3_lean_candidate_faults_reach_checker_then_use_unmodified_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault_type: str,
) -> None:
    case, source_store, transport = _acquire_lean_source(tmp_path, monkeypatch)
    source_transport_count = len(transport.responses)
    inventory = _inventory(
        experiment_id="exp3",
        case=case,
        domain="lean",
        fault_type=fault_type,
        fault_rate=0.34,
    )

    checker = _ContentAwareRecordingLeanChecker()
    scenario, _assembly, _protocol, row, checker = _run_lean_scenario(
        tmp_path,
        inventory=inventory,
        case=case,
        source_store=source_store,
        checker=checker,
    )

    assert len(transport.responses) == source_transport_count
    assert scenario.provider_call_count == 0
    assert checker.requests
    assert all(item.provider_call_made is False for item in row.attempts)
    records = [
        item
        for item in scenario.hooks.injection_records
        if item["kind"] == fault_type
    ]
    assert records, (
        row.fault_target_planned_ai_unit_ids,
        [(item.planned_ai_unit_id, item.attempt_ordinal, item.result_kind) for item in row.attempts],
        scenario.hooks.boundary_records,
        _protocol.summary,
    )
    observations = {
        item.attempt_id: item
        for item in row.fault_observations
        if item.fault_type == fault_type
    }
    for record in records:
        assert record["attempt_ordinal"] == 0
        observation = observations[record["attempt_id"]]
        assert observation.reached_verification is True
        assert observation.verifier_intercepted is True
        original = next(
            item for item in row.attempts if item.attempt_id == record["attempt_id"]
        )
        assert original.checker_result == (
            "accepted" if fault_type == "false_negative" else "proof_rejected"
        )
        replacement = next(
            item
            for item in row.attempts
            if item.replacement_of_attempt_id == record["attempt_id"]
        )
        assert replacement.source_response_slot_id == original.source_response_slot_id
        assert not any(
            item["attempt_id"] == replacement.attempt_id for item in records
        )
        if fault_type == "false_positive":
            assert LeanCheckerStatus.REJECTED in checker.statuses
            assert record["candidate_content_before"] != record["candidate_content_after"]
            assert observation.independently_wrong is True
        else:
            assert record["candidate_content_before"] == record["candidate_content_after"]
            assert record["candidate_output_refs_before"] == record["candidate_output_refs_after"]
            assert observation.independently_wrong is False


def test_exp3_reference_and_real_process_worker_death_keep_auxiliary_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case, source_store, transport = _acquire_factor_source(tmp_path, monkeypatch)
    source_transport_count = len(transport.responses)
    death_inventory = _inventory(
        experiment_id="exp3",
        case=case,
        domain="factorization",
        dead_worker_count=1,
        kill_progress_target_ratio=0.5,
    )
    reference_inventory = build_exp3_reference(death_inventory)
    assert reference_inventory.case_id == death_inventory.case_id
    assert reference_inventory.repeat_id == death_inventory.repeat_id
    assert reference_inventory.fault_type is None
    assert reference_inventory.dead_worker_count is None

    reference_scenario, _a, _p, reference = _run_factor_scenario(
        tmp_path / "reference",
        inventory=reference_inventory,
        case=case,
        source_store=source_store,
    )
    death_scenario, _a, _p, death = _run_factor_scenario(
        tmp_path / "death",
        inventory=death_inventory,
        case=case,
        source_store=source_store,
        process_timeout_seconds=20.0,
    )

    assert len(transport.responses) == source_transport_count
    assert reference_scenario.auxiliary_reference is True
    assert death_scenario.auxiliary_reference is False
    assert reference.fault_observations == []
    assert death_scenario.backend_kind == "process"
    assert death.worker_death_observations
    assert all(item.pid is not None and item.exit_code is not None for item in death.worker_death_observations)
    assert any(item.replacement_started for item in death.recovery_observations)
    assert death.runtime_wall_clock_ms >= reference.runtime_wall_clock_ms


def test_exp3_worker_death_policy_uses_progress_reachable_target_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factor_case, factor_source, factor_transport = _acquire_factor_source(
        tmp_path / "factor-source", monkeypatch
    )
    lean_case, lean_source, lean_transport = _acquire_lean_source(
        tmp_path / "lean-source", monkeypatch
    )
    factor_transport_count = len(factor_transport.responses)
    lean_transport_count = len(lean_transport.responses)

    for domain, case, source_store in (
        ("factorization", factor_case, factor_source),
        ("lean", lean_case, lean_source),
    ):
        for dead_worker_count in (1, 3):
            for ratio in (0.25, 0.5, 0.75):
                inventory = _inventory(
                    experiment_id="exp3",
                    case=case,
                    domain=domain,
                    dead_worker_count=dead_worker_count,
                    kill_progress_target_ratio=ratio,
                )
                config = replace(
                    _config(f"death-policy-{domain}-{dead_worker_count}-{ratio}"),
                    max_retries=2,
                )
                runtime = (
                    FactorizationRuntimeAdapter(
                        provider_family="deepseek",
                        seed=7,
                        protocol_config=config,
                        created_at=NOW,
                    )
                    if domain == "factorization"
                    else LeanRuntimeAdapter(
                        provider_family="deepseek",
                        environment_manifest=_test_lean_environment(),
                        checker=RecordingLeanChecker(),
                        protocol_config=config,
                        created_at=NOW,
                    )
                )
                scenario = build_scenario(
                    inventory=inventory,
                    root_input=case,
                    source_store=source_store,
                    artifact_store=ArtifactStore(
                        tmp_path
                        / "policy"
                        / domain
                        / f"dead-{dead_worker_count}-progress-{ratio}"
                    ),
                    plugin_runtime=runtime,
                    protocol_config=config,
                    submitted_at=_Clock(),
                )
                policy = scenario.worker_backend._termination_policy
                ordered = tuple(sorted(inventory.planned_ai_unit_ids))
                expected = ordered[ceil(ratio * len(ordered)) - 1]
                if domain == "factorization":
                    target_n = int(case["target_n"])
                    witnesses = []
                    for planned in ordered:
                        trace = source_store.read_trace(
                            str(case["case_id"]), 0, planned
                        )
                        assert trace.candidate_start is not None
                        assert trace.candidate_end is not None
                        if any(
                            target_n % candidate == 0
                            for candidate in range(
                                trace.candidate_start,
                                trace.candidate_end + 1,
                            )
                        ):
                            witnesses.append(planned)
                    if witnesses:
                        expected = witnesses[0]
                assert policy.target_planned_ai_unit_ids == (expected,)
                assert policy.termination_limit == dead_worker_count
                assert policy.target_progress_ratio == ratio

    assert len(factor_transport.responses) == factor_transport_count
    assert len(lean_transport.responses) == lean_transport_count


def test_exp3_factor_process_realizes_frozen_death_count_across_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case, source_store, transport = _acquire_factor_source(tmp_path, monkeypatch)
    source_transport_count = len(transport.responses)

    p75 = _inventory(
        experiment_id="exp3",
        case=case,
        domain="factorization",
        dead_worker_count=1,
        kill_progress_target_ratio=0.75,
    )
    p75_scenario, _assembly, _protocol, p75_row = _run_factor_scenario(
        tmp_path / "p75",
        inventory=p75,
        case=case,
        source_store=source_store,
        process_timeout_seconds=20.0,
    )
    assert p75_row.worker_death_observations
    assert all(
        item.actual_progress_ratio >= item.target_progress_ratio
        for item in p75_row.worker_death_observations
    )
    assert p75_scenario.provider_call_count == 0

    for ratio in (0.25, 0.5, 0.75):
        terminal = _inventory(
            experiment_id="exp3",
            case=case,
            domain="factorization",
            dead_worker_count=3,
            kill_progress_target_ratio=ratio,
        )
        terminal_scenario, _assembly, terminal_protocol, terminal_row = (
            _run_factor_scenario(
                tmp_path / f"terminal-{ratio}",
                inventory=terminal,
                case=case,
                source_store=source_store,
                process_timeout_seconds=20.0,
            )
        )
        assert terminal_protocol.status == "failed", ratio
        assert terminal_row.root_status == "failed"
        assert terminal_row.final_result_present is False
        assert terminal_row.failure_stage == "child_execution"
        assert terminal_row.failure_origin == "worker_death_exhausted"
        assert len(terminal_row.worker_death_observations) == 3
        assert all(
            item.pid is not None
            and item.exit_code is not None
            and item.actual_progress_ratio >= ratio
            for item in terminal_row.worker_death_observations
        )
        assert sum(
            item.replacement_started for item in terminal_row.recovery_observations
        ) == 2
        assert terminal_scenario.provider_call_count == 0
        assert all(item.provider_call_made is False for item in terminal_row.attempts)
    assert len(transport.responses) == source_transport_count


def test_exp3_lean_process_transfers_state_and_p75_dead3_is_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case, source_store, transport = _acquire_lean_source(tmp_path, monkeypatch)
    source_transport_count = len(transport.responses)

    recovered = _inventory(
        experiment_id="exp3",
        case=case,
        domain="lean",
        dead_worker_count=1,
        kill_progress_target_ratio=0.25,
    )
    recovered_scenario, recovered_assembly, recovered_protocol, recovered_row, _ = (
        _run_lean_scenario(
            tmp_path / "recovered",
            inventory=recovered,
            case=case,
            source_store=source_store,
            process_timeout_seconds=20.0,
        )
    )
    assert recovered_protocol.status == "completed"
    assert recovered_row.verified_correct is True
    assert recovered_row.worker_death_observations
    assert any(item.replacement_started for item in recovered_row.recovery_observations)
    assert recovered_scenario.submission_adapter.attempts
    assert recovered_assembly.plugin_runtime.merge_result.root_checker_report is not None
    terminated_attempt_ids = {
        item.original_attempt_id for item in recovered_row.worker_death_observations
    }
    assert terminated_attempt_ids <= {
        item.attempt_id for item in recovered_row.attempts
    }
    assert all(
        item.checker_result == "accepted"
        for item in recovered_row.attempts
        if item.attempt_id not in terminated_attempt_ids
    )

    terminal = _inventory(
        experiment_id="exp3",
        case=case,
        domain="lean",
        dead_worker_count=3,
        kill_progress_target_ratio=0.75,
    )
    terminal_scenario, _assembly, terminal_protocol, terminal_row, _ = (
        _run_lean_scenario(
            tmp_path / "terminal",
            inventory=terminal,
            case=case,
            source_store=source_store,
            process_timeout_seconds=20.0,
        )
    )
    assert terminal_protocol.status == "failed"
    assert terminal_row.final_result_present is False
    assert terminal_row.failure_stage == "child_execution"
    assert terminal_row.failure_origin == "worker_death_exhausted"
    assert len(terminal_row.worker_death_observations) == 3
    assert terminal_scenario.submission_adapter.attempts
    assert terminal_scenario.provider_call_count == 0
    assert len(transport.responses) == source_transport_count
    assert all(item.provider_call_made is False for item in terminal_row.attempts)


def test_exp4_failed_source_before_parser_is_valid_no_final_actual_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case, source_store, transport = _acquire_factor_source(tmp_path, monkeypatch)
    source_transport_count = len(transport.responses)
    _replace_natural_source_attempt_with_failure(
        source_store=source_store,
        case_id=str(case["case_id"]),
        planned_ai_unit_id="range_0",
    )
    plan = ChallengePlanV1(
        challenge_plan_id="plan-invalid-failed-source",
        case_id=str(case["case_id"]),
        repeat_id=1,
        challenge_family="INVALID_PARSED_CANDIDATE",
        target_rule="stable_first_planned_unit",
        attempt_rule="ordinal_0",
    )
    inventory = _inventory(
        experiment_id="exp4",
        case=case,
        domain="factorization",
        mode="NO_VERIFICATION__NO_PARSER_POLICY",
        challenge_plan_id=plan.challenge_plan_id,
    )

    scenario, _assembly, protocol, row = _run_factor_scenario(
        tmp_path / "failed-source",
        inventory=inventory,
        case=case,
        source_store=source_store,
        challenge_plan=plan,
    )

    assert len(transport.responses) == source_transport_count
    assert scenario.provider_call_count == 0
    assert protocol.status == "failed"
    assert row.protocol_started is True
    assert row.final_result_present is False
    assert row.verified_correct is False
    assert row.failure_stage == "candidate_acquisition"
    assert row.failure_kind == "no_final"
    assert row.failure_origin == "provider_transport_exhausted"
    assert any(item.source_result_kind == "source_attempt_failed" for item in row.attempts)
    assert all(item.provider_call_made is False for item in row.attempts)
    assert len(row.challenge_observations) == 1
    challenge = row.challenge_observations[0]
    assert challenge.opportunity is False
    assert challenge.injected is False
    by_mechanism = {
        item.disabled_mechanism: item for item in row.ablation_observations
    }
    parser = by_mechanism["parser_policy"]
    assert parser.disabled_mechanism == "parser_policy"
    assert parser.route_status is None
    assert (
        row.missing_reason["ablation_observations[].parser_policy_route"]
        == "missing_actual_route_evidence"
    )
    verification = by_mechanism["verification"]
    assert verification.route_status is None
    assert verification.root_checker_reached is False
    assert verification.root_check_passed is None
    assert verification.root_checker_call_count == 0
    assert (
        row.missing_reason["ablation_observations[].verification_route"]
        == "missing_actual_route_evidence"
    )


def test_exp4_raw_passthrough_cannot_replace_missing_actual_challenge_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case, source_store, transport = _acquire_factor_source(tmp_path, monkeypatch)
    source_transport_count = len(transport.responses)
    plan = ChallengePlanV1(
        challenge_plan_id="plan-invalid-no-actual-record",
        case_id=str(case["case_id"]),
        repeat_id=1,
        challenge_family="INVALID_PARSED_CANDIDATE",
        target_rule="stable_first_planned_unit",
        attempt_rule="ordinal_0",
    )
    inventory = _inventory(
        experiment_id="exp4",
        case=case,
        domain="factorization",
        mode="NO_PARSER_POLICY",
        challenge_plan_id=plan.challenge_plan_id,
    )

    def suppress_actual_record(scenario: object) -> None:
        scenario.challenge_controller._record = lambda **_kwargs: None

    with pytest.raises(
        RootProjectionError,
        match="lacks persisted actual injection evidence",
    ):
        _run_factor_scenario(
            tmp_path,
            inventory=inventory,
            case=case,
            source_store=source_store,
            challenge_plan=plan,
            before_run=suppress_actual_record,
        )
    assert len(transport.responses) == source_transport_count


def test_exp4_mode_blind_challenges_and_all_eleven_real_policies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case, source_store, transport = _acquire_factor_source(tmp_path, monkeypatch)
    source_transport_count = len(transport.responses)
    families = (
        "INVALID_PARSED_CANDIDATE",
        "PARSER_REQUIRED_CANONICAL_JSON",
        "RECOVERABLE_NO_RETURN",
        "REQUIRED_CHILD_DELAY",
    )
    plans = {
            family: ChallengePlanV1(
                challenge_plan_id=f"plan-{family.lower()}",
                case_id=str(case["case_id"]),
                repeat_id=1,
                challenge_family=family,
                target_rule=(
                    "all_true_divisor_ranges"
                    if family == "REQUIRED_CHILD_DELAY"
                    else "stable_first_planned_unit"
                ),
            attempt_rule="every_attempt" if family == "PARSER_REQUIRED_CANONICAL_JSON" else "ordinal_0",
        )
        for family in families
    }
    resolved: dict[str, list[object]] = {family: [] for family in families}
    rows = []
    rows_by_key = {}
    scenarios_by_key = {}
    protocols_by_key = {}
    expected_semantics = {
        "INVALID_PARSED_CANDIDATE": False,
        "PARSER_REQUIRED_CANONICAL_JSON": True,
        "RECOVERABLE_NO_RETURN": None,
        "REQUIRED_CHILD_DELAY": None,
    }
    for family in families:
        plan = plans[family]
        for mode in EXP4_MODE_DISABLED_MECHANISMS:
            inventory = _inventory(
                experiment_id="exp4",
                case=case,
                domain="factorization",
                mode=mode,
                challenge_plan_id=plan.challenge_plan_id,
            )
            scenario, _assembly, _protocol, row = _run_factor_scenario(
                tmp_path / family / mode,
                inventory=inventory,
                case=case,
                source_store=source_store,
                challenge_plan=plan,
            )
            rows.append(row)
            rows_by_key[(family, mode)] = row
            scenarios_by_key[(family, mode)] = scenario
            protocols_by_key[(family, mode)] = _protocol
            resolved[family].append(scenario.challenge_plan)
            assert not hasattr(scenario.challenge_controller, "mode")
            assert not hasattr(scenario.challenge_controller, "disabled_mechanisms")
            assert not hasattr(scenario.challenge_controller, "mechanism_policy")
            disabled = EXP4_MODE_DISABLED_MECHANISMS[mode]
            assert len(row.ablation_observations) == len(disabled)
            assert {item.disabled_mechanism for item in row.ablation_observations} == set(disabled)
            if len(disabled) == 2:
                policy_values = {
                    "verification": scenario.mechanism_policy.verification_enabled,
                    "parser_policy": scenario.mechanism_policy.parser_policy_enabled,
                    "requeue": scenario.mechanism_policy.replacement_attempts_allowed,
                    "merge_gate": scenario.mechanism_policy.merge_gate_enabled,
                }
                assert sum(not policy_values[item] for item in disabled) == 2
            assert row.challenge_observations, (
                getattr(scenario.challenge_controller, "injection_records", None),
                row.failure_stage,
                row.failure_kind,
                _protocol.summary,
            )
            parser_disabled = "parser_policy" in disabled
            if family == "INVALID_PARSED_CANDIDATE" and parser_disabled:
                assert all(
                    item.opportunity is False and item.injected is False
                    for item in row.challenge_observations
                )
                assert {
                    item.source_semantics_preserved
                    for item in row.challenge_observations
                } == {None}
            else:
                assert all(item.injected for item in row.challenge_observations)
                assert {
                    item.source_semantics_preserved
                    for item in row.challenge_observations
                } == {expected_semantics[family]}
            if expected_semantics[family] is None or (
                family == "INVALID_PARSED_CANDIDATE" and parser_disabled
            ):
                assert (
                    row.not_applicable_reason[
                        "challenge_observations[].source_semantics_preserved"
                    ]
                    == "candidate_semantics_not_observed_for_result_suppression"
                )

    assert all(
        all(item == family_plans[0] for item in family_plans)
        for family_plans in resolved.values()
    )

    assert len(transport.responses) == source_transport_count
    assert set(item.challenge_family for item in rows) == set(families)
    assert all(item.protocol_started for item in rows)
    assert all(item.challenge_observations for item in rows)
    assert all(item.provider_call_made is False for row in rows for item in row.attempts)
    assert all(
        rows_by_key[(family, "FULL")].ablation_observations == []
        for family in families
    )
    parser_modes = [
        mode
        for mode, disabled in EXP4_MODE_DISABLED_MECHANISMS.items()
        if "parser_policy" in disabled
    ]
    assert all(
        next(
            item
            for item in rows_by_key[("PARSER_REQUIRED_CANONICAL_JSON", mode)].ablation_observations
            if item.disabled_mechanism == "parser_policy"
        ).raw_only_exposed
        is True
        for mode in parser_modes
    )
    assert all(
        next(
            item
            for item in rows_by_key[("PARSER_REQUIRED_CANONICAL_JSON", mode)].ablation_observations
            if item.disabled_mechanism == "parser_policy"
        ).domain_parser_call_count
        == 0
        for mode in parser_modes
    )
    verification_modes = [
        mode
        for mode, disabled in EXP4_MODE_DISABLED_MECHANISMS.items()
        if "verification" in disabled
    ]
    assert all(
        next(
            item
            for item in rows_by_key[("INVALID_PARSED_CANDIDATE", mode)].ablation_observations
            if item.disabled_mechanism == "verification"
        ).plugin_verify_submission_call_count
        == 0
        for mode in verification_modes
    )
    requeue_modes = [
        mode
        for mode, disabled in EXP4_MODE_DISABLED_MECHANISMS.items()
        if "requeue" in disabled
    ]
    for mode in requeue_modes:
        requeue_observation = next(
            item
            for item in rows_by_key[("RECOVERABLE_NO_RETURN", mode)].ablation_observations
            if item.disabled_mechanism == "requeue"
        )
        assert requeue_observation.route_status in {
            "applied",
            "preempted_by_merge_first",
            None,
        }
        if requeue_observation.route_status is None:
            assert (
                rows_by_key[("RECOVERABLE_NO_RETURN", mode)].missing_reason[
                    "ablation_observations[].requeue_route"
                ]
                == "missing_actual_route_evidence"
            )
    r_only = next(
        item
        for item in rows_by_key[("RECOVERABLE_NO_RETURN", "NO_REQUEUE")]
        .ablation_observations
        if item.disabled_mechanism == "requeue"
    )
    assert r_only.route_status == "applied"
    assert r_only.stuck_due_to_no_requeue is True
    merge_only = rows_by_key[("REQUIRED_CHILD_DELAY", "NO_MERGE_GATE")]
    merge_observation = merge_only.ablation_observations[0]
    assert merge_observation.merge_gate_satisfied is False
    assert merge_observation.missing_required_slot_ids
    assert merge_observation.premature_merge_attempted is True
    assert merge_observation.premature_merge_failed is True
    factor_verification = next(
        item
        for item in rows_by_key[("INVALID_PARSED_CANDIDATE", "NO_VERIFICATION")]
        .ablation_observations
        if item.disabled_mechanism == "verification"
    )
    assert factor_verification.root_checker_reached is False
    assert factor_verification.root_check_passed is None
    assert factor_verification.root_checker_call_count == 0
    assert (
        "ablation_observations[].root_checker_reached"
        not in rows_by_key[
            ("INVALID_PARSED_CANDIDATE", "NO_VERIFICATION")
        ].missing_reason
    )
    full_delay = rows_by_key[("REQUIRED_CHILD_DELAY", "FULL")]
    assert full_delay.final_result_present is True
    assert full_delay.verified_correct is True
    full_merge_records = scenarios_by_key[
        ("REQUIRED_CHILD_DELAY", "FULL")
    ].route_observer.merge_records
    assert [item["gate_satisfied"] for item in full_merge_records][-2:] == [
        False,
        True,
    ]
    assert not scenarios_by_key[
        ("REQUIRED_CHILD_DELAY", "FULL")
    ].route_observer.premature_merge_observations
    merge_modes = [
        mode
        for mode, disabled in EXP4_MODE_DISABLED_MECHANISMS.items()
        if "merge_gate" in disabled
    ]
    for mode in merge_modes:
        premature = scenarios_by_key[
            ("REQUIRED_CHILD_DELAY", mode)
        ].route_observer.premature_merge_observations
        assert len(premature) == 1
        assert premature[0].missing_required_slot_ids
        assert premature[0].plugin_outcome == "rejected_incomplete_input"
        assert premature[0].root_checker_reached is False
        assert premature[0].root_check_passed is None
    key_dual = rows_by_key[("REQUIRED_CHILD_DELAY", "NO_REQUEUE__NO_MERGE_GATE")]
    assert key_dual.final_result_present is False
    assert any(
        item.disabled_mechanism == "requeue"
        and item.route_status == "preempted_by_merge_first"
        and item.stuck_due_to_no_requeue is False
        for item in key_dual.ablation_observations
    )
    first_plan = build_challenge_plan(
        inventory=_inventory(
            experiment_id="exp4",
            case=case,
            domain="factorization",
            mode="FULL",
            challenge_plan_id=plans[families[0]].challenge_plan_id,
        ),
        root_input=case,
        source_store=source_store,
        plan=plans[families[0]],
    )
    assert first_plan.target_planned_ai_unit_ids == ("range_0",)


def test_exp4_lean_structural_parser_verifier_and_merge_routes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case, source_store, _transport = _acquire_lean_source(tmp_path, monkeypatch)

    def run(family: str, mode: str, target_rule: str):
        plan = ChallengePlanV1(
            challenge_plan_id=f"lean-{family.lower()}-{mode.lower()}",
            case_id=str(case["case_id"]),
            repeat_id=1,
            challenge_family=family,
            target_rule=target_rule,
            attempt_rule=(
                "every_attempt"
                if family == "PARSER_REQUIRED_CANONICAL_JSON"
                else "ordinal_0"
            ),
        )
        inventory = _inventory(
            experiment_id="exp4",
            case=case,
            domain="lean",
            mode=mode,
            challenge_plan_id=plan.challenge_plan_id,
        )
        return _run_lean_scenario(
            tmp_path / family / mode,
            inventory=inventory,
            case=case,
            source_store=source_store,
            challenge_plan=plan,
        )

    parser = run(
        "PARSER_REQUIRED_CANONICAL_JSON",
        "NO_PARSER_POLICY",
        "stable_first_planned_unit",
    )
    parser_observation = parser[3].ablation_observations[0]
    assert parser_observation.domain_parser_call_count == 0
    assert parser_observation.raw_only_exposed is True

    verifier = run(
        "INVALID_PARSED_CANDIDATE",
        "NO_VERIFICATION",
        "stable_first_planned_unit",
    )
    verifier_observation = verifier[3].ablation_observations[0]
    assert verifier_observation.domain_child_checker_call_count == 0
    assert verifier_observation.plugin_verify_submission_call_count == 0
    synthetic_reports = [
        event.payload["verification_report"]
        for event in verifier[1].event_ledger.read_all()
        if event.event_type == EventType.VERIFICATION_RECORDED
        and event.payload["verification_report"]["validator_policy_id"]
        == "runtime_ablation_no_verification.v1"
    ]
    assert synthetic_reports
    assert all(
        report["verifier"]
        == {
            "verifier_id": "runtime_ablation_no_verification",
            "verifier_version": "v1",
        }
        and report["metadata"]["domain_verifier_invoked"] is False
        for report in synthetic_reports
    )
    unchecked_submissions = [
        json.loads(
            verifier[1].artifact_store.read_bytes(
                ArtifactRef.from_dict(event.payload["submission_ref"])
            )
        )
        for event in verifier[1].event_ledger.read_all()
        if event.event_type == EventType.EXECUTION_SUBMISSION_RECORDED
        and event.payload.get("unit_id") != f"paper_lean_root_{case['case_id']}"
    ]
    assert unchecked_submissions
    assert all(
        set(item["candidate_output_refs"]) == {"lean_proof_artifact"}
        for item in unchecked_submissions
    )
    provenance_ref = ArtifactRef.from_dict(unchecked_submissions[0]["provenance_ref"])
    unchecked = json.loads(verifier[1].artifact_store.read_bytes(provenance_ref))
    assert unchecked["domain_verifier_invoked"] is False
    assert unchecked["proof_candidate_ref"]["artifact_schema_id"] == (
        "lean_proof.proof_candidate"
    )

    combined = run(
        "INVALID_PARSED_CANDIDATE",
        "NO_VERIFICATION__NO_PARSER_POLICY",
        "stable_first_planned_unit",
    )
    combined_by_mechanism = {
        item.disabled_mechanism: item for item in combined[3].ablation_observations
    }
    assert combined_by_mechanism["parser_policy"].domain_parser_call_count == 0
    assert combined_by_mechanism["verification"].domain_child_checker_call_count == 0
    combined_submissions = [
        json.loads(
            combined[1].artifact_store.read_bytes(
                ArtifactRef.from_dict(event.payload["submission_ref"])
            )
        )
        for event in combined[1].event_ledger.read_all()
        if event.event_type == EventType.EXECUTION_SUBMISSION_RECORDED
    ]
    raw_passthrough = [
        item
        for item in combined_submissions
        if "lean_proof_artifact" in item["candidate_output_refs"]
    ]
    assert raw_passthrough
    assert all(
        item["candidate_output_refs"]["lean_proof_artifact"]
        == item["raw_output_ref"]
        and item["provenance_ref"] is None
        for item in raw_passthrough
    )

    merge = run(
        "REQUIRED_CHILD_DELAY",
        "NO_MERGE_GATE",
        "last_required_terminal_slot",
    )
    assert len(merge[0].route_observer.premature_merge_observations) == 1
    premature = merge[0].route_observer.premature_merge_observations[0]
    assert premature.plugin_error_kind == "IncompleteMergeInputError"
    assert premature.root_checker_reached is False
    assert premature.root_check_passed is None
