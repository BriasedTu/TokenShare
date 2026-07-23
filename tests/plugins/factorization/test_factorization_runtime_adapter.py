from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tokenshare.core.models import ArtifactRef, ProtocolConfig
from tokenshare.executors.contracts import ExecutionSubmission
from tokenshare.local_runtime import (
    ProtocolRunCoordinator,
    ProtocolRunRequest,
    SequentialWorkerBackend,
)
from tokenshare.plugins.factorization.runtime_adapter import (
    FactorizationExecutionBridge,
    FactorizationRuntimeAdapter,
)
import tokenshare.plugins.factorization.runtime_adapter as runtime_adapter_module
from tokenshare.plugins.factorization.models import RangeResult
from tokenshare.plugins.factorization.schemas import (
    RANGE_RESULT_FOUND_FACTOR,
    RANGE_RESULT_NO_FACTOR,
)
from tokenshare.protocol_engine import ProtocolEngine
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType


def _case() -> dict[str, object]:
    return {
        "schema_version": "tokenshare.paper_factorization_case.v1",
        "case_id": "factor_runtime_91",
        "difficulty": "easy",
        "target_n": "91",
        "candidate_start": "2",
        "candidate_end": "9",
        "split_params": {
            "strategy_id": "factorization.candidate_range_partition.v1",
            "requested_child_count": 3,
        },
        "oracle_prime_factors": [
            {"prime": "7", "exponent": 1},
            {"prime": "13", "exponent": 1},
        ],
    }


def test_plan_units_are_real_factorization_child_snapshots(tmp_path: Path) -> None:
    adapter = FactorizationRuntimeAdapter(
        provider_family="siliconflow",
        seed=7,
    )

    units = adapter.plan_units(
        _case(),
        artifact_store=ArtifactStore(tmp_path),
    )

    assert len(units) == 3
    assert all(unit.unit_type == "factor_search_range" for unit in units)
    assert all(unit.parent_unit_id == "paper_factor_root_factor_runtime_91" for unit in units)
    assert all(unit.required_capabilities["executor"] == "mock_ai" for unit in units)
    assert [unit.plugin_payload["summary"]["child_index"] for unit in units] == [0, 1, 2]


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 22, tzinfo=UTC)

    def __call__(self) -> str:
        value = self.value
        self.value += timedelta(seconds=1)
        return value.isoformat().replace("+00:00", "Z")


class _RangeExecutor:
    def __init__(self, store: ArtifactStore, *, force_no_factor: bool = False) -> None:
        self.store = store
        self.force_no_factor = force_no_factor
        self.unit_ids: list[str] = []

    def execute(self, request, *, submission_id: str, submitted_at: str):
        assert request.task_unit_snapshot["unit_type"] == "factor_search_range"
        self.unit_ids.append(request.unit_id)
        range_ref = request.input_artifact_refs["range_input"]
        body = json.loads(self.store.read_bytes(range_ref).decode("utf-8"))
        divisor = None
        if not self.force_no_factor:
            divisor = next(
                (
                    value
                    for value in range(
                        int(body["range_start"]), int(body["range_end"]) + 1
                    )
                    if int(body["target_n"]) % value == 0
                ),
                None,
            )
        result = RangeResult(
            range_result_id=f"range_result_{request.attempt_id}",
            target_n=body["target_n"],
            range_start=body["range_start"],
            range_end=body["range_end"],
            coverage_id=body["coverage_id"],
            child_index=body["child_index"],
            partition_params_digest=body["partition_params_digest"],
            result_kind=(
                RANGE_RESULT_FOUND_FACTOR
                if divisor is not None
                else RANGE_RESULT_NO_FACTOR
            ),
            found_factor=str(divisor) if divisor is not None else None,
            cofactor=(
                str(int(body["target_n"]) // divisor)
                if divisor is not None
                else None
            ),
            checked_divisor_count=(
                divisor - int(body["range_start"]) + 1
                if divisor is not None
                else int(body["range_end"]) - int(body["range_start"]) + 1
            ),
            executor_summary={
                "executor": "test_bounded_search",
                "bounded_range_only": True,
                "checked_start": body["range_start"],
                "checked_end": (
                    str(divisor) if divisor is not None else body["range_end"]
                ),
            },
            created_at=submitted_at,
        )
        output_ref = self.store.save_json(
            result.to_dict(),
            artifact_id=result.range_result_id,
            artifact_type="canonical_output",
            artifact_schema_id="factorization.range_result",
            artifact_schema_version="v1",
            source={"kind": "factorization_runtime_test"},
            metadata={"output_name": "range_result"},
            created_at=submitted_at,
        )
        return ExecutionSubmission(
            submission_id=submission_id,
            request_id=request.request_id,
            task_id=request.task_id,
            unit_id=request.unit_id,
            attempt_id=request.attempt_id,
            lease_id=request.lease_id,
            fencing_token=request.fencing_token,
            executor_id="executor_ai_api",
            executor_version="0.1.0",
            result_kind="succeeded",
            raw_output_ref=output_ref,
            parsed_output_ref=output_ref,
            candidate_output_refs={"range_result": output_ref},
            parse_failure_ref=None,
            log_ref=None,
            environment_ref=request.environment_ref,
            environment_summary={"runtime": "pytest"},
            provenance_ref=None,
            usage_summary={"provider_attempt_count": 1},
            error=None,
            submitted_at=submitted_at,
        )


def test_coordinator_records_full_protocol_chain_for_every_range_child(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path)
    ledger = EventLedger(tmp_path / "events" / "factor_runtime_91.jsonl")
    config = ProtocolConfig.default(
        config_id="factor_runtime_config",
        artifact_store_uri="file://artifacts",
        event_log_uri="file://events/factor_runtime_91.jsonl",
    )
    adapter = FactorizationRuntimeAdapter(
        provider_family="siliconflow",
        seed=7,
        protocol_config=config,
    )
    range_executor = _RangeExecutor(store)
    clock = _Clock()
    planned_snapshots = {
        unit.unit_id: unit.to_dict()
        for unit in adapter.plan_units(_case(), artifact_store=store)
    }

    result = ProtocolRunCoordinator(
        engine=ProtocolEngine(
            event_ledger=ledger,
            protocol_config=config,
            artifact_store=store,
        ),
        artifact_store=store,
        event_ledger=ledger,
        now=clock,
    ).run_root(
        ProtocolRunRequest(
            run_id="factor_runtime_91",
            root_input=_case(),
            plugin_runtime=adapter,
            worker_backend=SequentialWorkerBackend(
                executor=FactorizationExecutionBridge(
                    plugin_runtime=adapter,
                    range_executor=range_executor,
                ),
                submitted_at=clock,
            ),
        )
    )

    assert result.status == "completed"
    events = ledger.read_all()
    child_ids = {
        event.payload["task_unit"]["unit_id"]
        for event in events
        if event.event_type == EventType.TASK_UNIT_CREATED
        and event.payload["task_unit"]["unit_type"] == "factor_search_range"
    }
    assert child_ids == set(range_executor.unit_ids)
    actual_snapshots = {
        event.payload["task_unit"]["unit_id"]: event.payload["task_unit"]
        for event in events
        if event.event_type == EventType.TASK_UNIT_CREATED
        and event.payload["task_unit"]["unit_type"] == "factor_search_range"
    }
    assert actual_snapshots == planned_snapshots
    assert len(child_ids) == 3
    for child_id in child_ids:
        assert any(
            event.event_type == EventType.LEASE_STATE_CHANGED
            and event.payload.get("new_state") == "Active"
            and event.payload["lease"]["unit_id"] == child_id
            and event.payload.get("scheduling_decision", {}).get("unit_id") == child_id
            for event in events
        )
        for event_type in (
            EventType.EXECUTION_REQUEST_RECORDED,
            EventType.EXECUTION_SUBMISSION_RECORDED,
            EventType.VERIFICATION_RECORDED,
            EventType.CANONICAL_OUTPUTS_BOUND,
        ):
            assert any(
                event.event_type == event_type
                and event.payload.get("unit_id") == child_id
                for event in events
            )
    assert any(event.event_type == EventType.MERGE_RECORDED for event in events)
    assert any(
        event.event_type == EventType.TASK_UNIT_STATE_CHANGED
        and event.object_id == result.root_unit_id
        and event.payload.get("new_state") == "Completed"
        for event in events
    )
    assert any(event.event_type == EventType.SETTLEMENT_RECORDED for event in events)


def test_false_negative_verification_binds_recorded_submission_and_request_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ArtifactStore(tmp_path)
    ledger = EventLedger(tmp_path / "events" / "factor_runtime_rejected.jsonl")
    config = replace(
        ProtocolConfig.default(
            config_id="factor_runtime_rejected_config",
            artifact_store_uri="file://artifacts",
            event_log_uri="file://events/factor_runtime_rejected.jsonl",
        ),
        max_retries=0,
    )
    adapter = FactorizationRuntimeAdapter(
        provider_family="siliconflow",
        seed=7,
        protocol_config=config,
    )
    verifier_calls: list[dict[str, object]] = []
    original_verify_range_result = runtime_adapter_module.verify_range_result

    def verify_range_result_spy(
        candidate,
        *,
        child_input,
        no_factor_recheck_max_divisors,
    ):
        verifier_calls.append(
            {
                "candidate": candidate,
                "child_input": child_input.to_dict(),
                "no_factor_recheck_max_divisors": no_factor_recheck_max_divisors,
            }
        )
        return original_verify_range_result(
            candidate,
            child_input=child_input,
            no_factor_recheck_max_divisors=no_factor_recheck_max_divisors,
        )

    monkeypatch.setattr(
        runtime_adapter_module,
        "verify_range_result",
        verify_range_result_spy,
    )
    clock = _Clock()
    coordinator = ProtocolRunCoordinator(
        engine=ProtocolEngine(
            event_ledger=ledger,
            protocol_config=config,
            artifact_store=store,
        ),
        artifact_store=store,
        event_ledger=ledger,
        now=clock,
    )

    with pytest.raises(RuntimeError, match="child unit failed after retry limit"):
        coordinator.run_root(
            ProtocolRunRequest(
                run_id="factor_runtime_rejected",
                root_input=_case(),
                plugin_runtime=adapter,
                worker_backend=SequentialWorkerBackend(
                    executor=FactorizationExecutionBridge(
                        plugin_runtime=adapter,
                        range_executor=_RangeExecutor(store, force_no_factor=True),
                    ),
                    submitted_at=clock,
                ),
            )
        )

    events = ledger.read_all()
    rejected = next(
        event
        for event in events
        if event.event_type == EventType.VERIFICATION_RECORDED
        and event.payload["status"] == "rejected"
    )
    rejected_unit_id = rejected.payload["unit_id"]
    rejected_submission = next(
        event
        for event in events
        if event.event_type == EventType.EXECUTION_SUBMISSION_RECORDED
        and event.payload["submission_id"] == rejected.payload["submission_id"]
    )
    submission_body = json.loads(
        store.read_bytes(
            ArtifactRef.from_dict(rejected_submission.payload["submission_ref"])
        ).decode("utf-8")
    )
    candidate_ref = ArtifactRef.from_dict(
        submission_body["candidate_output_refs"]["range_result"]
    )
    candidate_body = json.loads(store.read_bytes(candidate_ref).decode("utf-8"))
    request_event = next(
        event
        for event in events
        if event.event_type == EventType.EXECUTION_REQUEST_RECORDED
        and event.payload["request_id"] == submission_body["request_id"]
    )
    request_body = json.loads(
        store.read_bytes(ArtifactRef.from_dict(request_event.payload["request_ref"])).decode(
            "utf-8"
        )
    )
    range_input_ref_body = request_body["input_artifact_refs"]["range_input"]
    range_input_body = json.loads(
        store.read_bytes(ArtifactRef.from_dict(range_input_ref_body)).decode("utf-8")
    )
    rejected_call = next(
        call for call in verifier_calls if call["candidate"] == candidate_body
    )

    assert rejected_call["child_input"] == range_input_body
    assert rejected_call["no_factor_recheck_max_divisors"] == (
        int(range_input_body["range_end"]) - int(range_input_body["range_start"]) + 1
    )
    assert (
        rejected.payload["verification_report"]["metadata"][
            "verification_input_refs"
        ]["range_input"]
        == range_input_ref_body
    )
    assert any(
        event.event_type == EventType.RECOVERY_ACTION_RECORDED
        and event.payload["recovery_action"]["unit_id"] == rejected_unit_id
        for event in events
    )
    assert any(
        event.event_type == EventType.TASK_UNIT_STATE_CHANGED
        and event.object_id == rejected_unit_id
        and event.payload["task_unit"]["state"] == "Failed"
        for event in events
    )
    assert not any(
        event.event_type == EventType.CANONICAL_OUTPUTS_BOUND
        and event.payload["unit_id"] == rejected_unit_id
        for event in events
    )
    assert not any(event.event_type == EventType.MERGE_RECORDED for event in events)
    assert not any(
        event.event_type == EventType.TASK_UNIT_STATE_CHANGED
        and event.object_id == "paper_factor_root_factor_runtime_91"
        and event.payload.get("new_state") == "Completed"
        for event in events
    )


def test_runtime_executor_identity_matches_scheduled_unit_kind(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    ledger = EventLedger(tmp_path / "events" / "factor_runtime_identity.jsonl")
    config = ProtocolConfig.default(
        config_id="factor_runtime_identity_config",
        artifact_store_uri="file://artifacts",
        event_log_uri="file://events/factor_runtime_identity.jsonl",
    )
    adapter = FactorizationRuntimeAdapter(
        provider_family="siliconflow",
        seed=7,
        protocol_config=config,
    )
    plan = adapter.plan_root(_case(), artifact_store=store)
    descriptors_by_id = {
        descriptor.executor_id: descriptor
        for descriptor in (
            adapter.deterministic_executor_descriptor,
            adapter.executor_descriptor,
        )
    }
    clients_by_id = {client.client_id: client for client in plan.clients}
    assert {
        client.executor_type for client in clients_by_id.values()
    } == {"deterministic_local", "ai_api"}
    clock = _Clock()
    ProtocolRunCoordinator(
        engine=ProtocolEngine(
            event_ledger=ledger,
            protocol_config=config,
            artifact_store=store,
        ),
        artifact_store=store,
        event_ledger=ledger,
        now=clock,
    ).run_root(
        ProtocolRunRequest(
            run_id="factor_runtime_identity",
            root_input=_case(),
            plugin_runtime=adapter,
            worker_backend=SequentialWorkerBackend(
                executor=FactorizationExecutionBridge(
                    plugin_runtime=adapter,
                    range_executor=_RangeExecutor(store),
                ),
                submitted_at=clock,
            ),
        )
    )

    events = ledger.read_all()
    registry_event = next(
        event
        for event in events
        if event.event_type == EventType.REGISTRY_SNAPSHOT_RECORDED
    )
    assert {
        entry["executor_id"] for entry in registry_event.payload["executor_entries"]
    } == {"executor_factorization_runtime", "executor_ai_api"}
    for request_event in (
        event
        for event in events
        if event.event_type == EventType.EXECUTION_REQUEST_RECORDED
    ):
        request_body = json.loads(
            store.read_bytes(
                ArtifactRef.from_dict(request_event.payload["request_ref"])
            ).decode("utf-8")
        )
        submission_event = next(
            event
            for event in events
            if event.event_type == EventType.EXECUTION_SUBMISSION_RECORDED
            and event.payload["request_id"] == request_body["request_id"]
        )
        submission_body = json.loads(
            store.read_bytes(
                ArtifactRef.from_dict(submission_event.payload["submission_ref"])
            ).decode("utf-8")
        )
        scheduling_event = next(
            event
            for event in events
            if event.event_type == EventType.LEASE_STATE_CHANGED
            and event.payload.get("scheduling_decision", {}).get("unit_id")
            == request_body["unit_id"]
        )
        is_range = (
            request_body["task_unit_snapshot"]["unit_type"]
            == "factor_search_range"
        )
        expected_executor_id = (
            "executor_ai_api" if is_range else "executor_factorization_runtime"
        )
        expected_executor_type = "ai_api" if is_range else "deterministic_local"
        expected_client_id = (
            "worker_factorization_ai_factor_runtime_91"
            if is_range
            else "worker_factorization_deterministic_factor_runtime_91"
        )

        assert scheduling_event.payload["scheduling_decision"]["client_id"] == (
            expected_client_id
        )
        client = clients_by_id[expected_client_id]
        descriptor = descriptors_by_id[expected_executor_id]
        assert client.executor_type == descriptor.executor_type
        assert client.executor_id == descriptor.executor_id
        assert client.executor_version == descriptor.executor_version
        assert request_body["executor"]["executor_id"] == expected_executor_id
        assert request_body["executor"]["executor_type"] == expected_executor_type
        assert request_body["executor"]["executor_id"] == client.executor_id
        assert request_body["executor"]["executor_type"] == client.executor_type
        assert request_body["executor"]["executor_version"] == client.executor_version
        assert request_body["hard_requirements"]["executor"] == (
            "ai_api" if is_range else "deterministic_local"
        )
        assert submission_body["executor_id"] == expected_executor_id
        assert submission_body["executor_id"] == client.executor_id
        assert submission_body["executor_version"] == request_body["executor"][
            "executor_version"
        ]


def test_plan_units_match_runtime_with_custom_clock_and_child_limit(
    tmp_path: Path,
) -> None:
    created_at = "2026-07-22T12:34:56Z"
    config = replace(
        ProtocolConfig.default(
            config_id="factor_runtime_custom_plan",
            artifact_store_uri="file://artifacts",
            event_log_uri="file://events/factor_runtime_custom_plan.jsonl",
        ),
        max_children_per_unit=2,
    )
    adapter = FactorizationRuntimeAdapter(
        provider_family="siliconflow",
        seed=7,
        protocol_config=config,
        created_at=created_at,
    )
    planned_units = adapter.plan_units(
        _case(),
        artifact_store=ArtifactStore(tmp_path / "planned"),
    )

    store = ArtifactStore(tmp_path / "runtime")
    ledger = EventLedger(
        tmp_path / "runtime" / "events" / "factor_runtime_custom_plan.jsonl"
    )
    clock = _Clock()
    ProtocolRunCoordinator(
        engine=ProtocolEngine(
            event_ledger=ledger,
            protocol_config=config,
            artifact_store=store,
        ),
        artifact_store=store,
        event_ledger=ledger,
        now=clock,
    ).run_root(
        ProtocolRunRequest(
            run_id="factor_runtime_custom_plan",
            root_input=_case(),
            plugin_runtime=adapter,
            worker_backend=SequentialWorkerBackend(
                executor=FactorizationExecutionBridge(
                    plugin_runtime=adapter,
                    range_executor=_RangeExecutor(store),
                ),
                submitted_at=clock,
            ),
        )
    )
    actual_units = tuple(
        event.payload["task_unit"]
        for event in ledger.read_all()
        if event.event_type == EventType.TASK_UNIT_CREATED
        and event.payload["task_unit"]["unit_type"] == "factor_search_range"
    )

    assert len(planned_units) == 2
    assert all(unit.created_at == created_at for unit in planned_units)
    assert [unit.to_dict() for unit in planned_units] == list(actual_units)
