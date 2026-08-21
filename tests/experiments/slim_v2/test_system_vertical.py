from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest

from tokenshare.core.models import ProtocolConfig
from tokenshare.executors.contracts import ExecutionSubmission
from tokenshare.experiments.slim_v2.projector import (
    RootProjectionError,
    project_root_result,
)
from tokenshare.experiments.slim_v2.runtime import RootAssembly, run_root_slice
from tokenshare.experiments.slim_v2.schema import RootInventoryV1
from tokenshare.local_runtime import (
    ProtocolRunCoordinator,
    SequentialWorkerBackend,
)
from tokenshare.plugins.factorization.models import RangeResult
from tokenshare.plugins.factorization.runtime_adapter import (
    FactorizationExecutionBridge,
    FactorizationRuntimeAdapter,
)
from tokenshare.plugins.factorization.schemas import (
    RANGE_RESULT_FOUND_FACTOR,
    RANGE_RESULT_NO_FACTOR,
    REQUESTED_OUTPUT_PRIME_FACTORIZATION,
)
from tokenshare.plugins.lean_proof.checker import LeanCheckerMode, LeanCheckerStatus
from tokenshare.plugins.lean_proof.environment import LeanEnvironmentManifest
from tokenshare.plugins.lean_proof.prompt_builder import PROOF_CANDIDATE_OUTPUT_NAME
from tokenshare.plugins.lean_proof.runtime_adapter import (
    LeanExecutionBridge,
    LeanRuntimeAdapter,
)
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType

from tests.support.lean_checker import RecordingLeanChecker


NOW = "2026-08-21T00:00:00Z"
LEAN_CATALOG_PATH = Path("benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl")


class _Clock:
    def __init__(self) -> None:
        self._value = datetime(2026, 8, 21, tzinfo=UTC)

    def __call__(self) -> str:
        value = self._value
        self._value += timedelta(seconds=1)
        return value.isoformat().replace("+00:00", "Z")


class _ObservationClock:
    def __init__(self) -> None:
        self._value = datetime(2026, 8, 21, microsecond=600, tzinfo=UTC)

    def __call__(self) -> str:
        value = self._value
        self._value += timedelta(microseconds=400)
        return value.isoformat().replace("+00:00", "Z")


class _RangeSubmissionFake:
    """只替换 submission 产生端；领域 bridge/verifier 保持真实。"""

    def __init__(self, store: ArtifactStore, *, corrupt_binding: bool = False) -> None:
        self._store = store
        self._corrupt_binding = corrupt_binding
        self.requests: list[object] = []

    def execute(self, request, *, submission_id: str, submitted_at: str):
        self.requests.append(request)
        body = json.loads(
            self._store.read_bytes(request.input_artifact_refs["range_input"]).decode(
                "utf-8"
            )
        )
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
        range_result = RangeResult(
            range_result_id=f"range_result_{request.attempt_id}",
            target_n=body["target_n"],
            range_start=body["range_start"],
            range_end=body["range_end"],
            coverage_id=(
                f"{body['coverage_id']}:wrong"
                if self._corrupt_binding
                else body["coverage_id"]
            ),
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
                "executor": "test_local_fake_submission",
                "bounded_range_only": True,
                "checked_start": body["range_start"],
                "checked_end": (
                    str(divisor) if divisor is not None else body["range_end"]
                ),
            },
            created_at=submitted_at,
        )
        output_ref = self._store.save_json(
            range_result.to_dict(),
            artifact_id=range_result.range_result_id,
            artifact_type="canonical_output",
            artifact_schema_id="factorization.range_result",
            artifact_schema_version="v1",
            source={"kind": "test_local_fake_submission"},
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
            executor_id=str(request.executor["executor_id"]),
            executor_version=str(request.executor["executor_version"]),
            result_kind="succeeded",
            raw_output_ref=output_ref,
            parsed_output_ref=output_ref,
            candidate_output_refs={"range_result": output_ref},
            parse_failure_ref=None,
            log_ref=None,
            environment_ref=request.environment_ref,
            environment_summary={"runtime": "test_local"},
            provenance_ref=None,
            usage_summary={"provider_attempt_count": 0},
            error=None,
            submitted_at=submitted_at,
        )


class _LeanSubmissionFake:
    """产生 proof candidate，但不替代真实 Lean checker/merge 边界。"""

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store
        self.requests: list[object] = []

    def execute(self, request, *, submission_id: str, submitted_at: str):
        self.requests.append(request)
        payload_ref = request.input_artifact_refs.get(
            "lemma_theorem_payload",
            request.input_artifact_refs.get("child_theorem_payload"),
        )
        payload = json.loads(self._store.read_bytes(payload_ref).decode("utf-8"))
        candidate_ref = self._store.save_json(
            {
                "schema_version": "lean_proof.proof_candidate.v1",
                "proof_candidate_id": f"candidate:{submission_id}",
                "theorem_payload_digest": payload["payload_digest"],
                "proof_source": "by\n  trivial",
                "created_at": submitted_at,
            },
            artifact_id=f"candidate_{submission_id}",
            artifact_type="LeanProofCandidate",
            artifact_schema_id="lean_proof.proof_candidate",
            artifact_schema_version="v1",
            source={"kind": "test_local_fake_submission"},
            metadata={"output_name": PROOF_CANDIDATE_OUTPUT_NAME},
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
            executor_id=str(request.executor["executor_id"]),
            executor_version=str(request.executor["executor_version"]),
            result_kind="succeeded",
            raw_output_ref=candidate_ref,
            parsed_output_ref=candidate_ref,
            candidate_output_refs={PROOF_CANDIDATE_OUTPUT_NAME: candidate_ref},
            parse_failure_ref=None,
            log_ref=None,
            environment_ref=request.environment_ref,
            environment_summary={"runtime": "test_local"},
            provenance_ref=None,
            usage_summary={"provider_attempt_count": 0},
            error=None,
            submitted_at=submitted_at,
        )


def test_factorization_root_runs_once_through_real_system_vertical_and_projects_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _factorization_case()
    store = ArtifactStore(tmp_path / "factorization")
    ledger = EventLedger(tmp_path / "factorization" / "events.jsonl")
    config = _config("factorization")
    adapter = FactorizationRuntimeAdapter(
        provider_family="siliconflow",
        seed=7,
        protocol_config=config,
        created_at=NOW,
    )
    fake = _RangeSubmissionFake(store)
    protocol_clock = _Clock()
    assembly = RootAssembly(
        run_id="task1_factorization_root",
        root_input=case,
        protocol_config=config,
        artifact_store=store,
        event_ledger=ledger,
        plugin_runtime=adapter,
        worker_backend=SequentialWorkerBackend(
            executor=FactorizationExecutionBridge(
                plugin_runtime=adapter,
                range_executor=fake,
            ),
            submitted_at=protocol_clock,
        ),
        now=protocol_clock,
        observation_clock=_ObservationClock(),
    )
    calls = _count_run_root(monkeypatch)

    protocol_result = run_root_slice(assembly)

    assert calls == ["task1_factorization_root"]
    assert protocol_result.status == "completed"
    assert len(fake.requests) == 3
    events = ledger.read_all()
    _assert_protocol_chain(
        events,
        root_unit_id=protocol_result.root_unit_id,
        expected_child_unit_ids=tuple(request.unit_id for request in fake.requests),
    )
    prime_ref = adapter.merge_candidate_refs[REQUESTED_OUTPUT_PRIME_FACTORIZATION]
    prime_body = json.loads(store.read_bytes(prime_ref).decode("utf-8"))
    product = 1
    for factor in prime_body["prime_factors"]:
        product *= int(factor["prime"]) ** int(factor["exponent"])
    assert prime_body["product_check_passed"] is True
    assert product == int(case["target_n"])

    root_result = _project_read_only(
        inventory=_inventory(case=case, domain="factorization"),
        assembly=assembly,
        protocol_result=protocol_result,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert calls == ["task1_factorization_root"]
    assert root_result.root_status == "completed"
    assert root_result.final_result_present is True
    assert root_result.verified_correct is True
    assert root_result.planned_ai_unit_ids == ["range_0", "range_1", "range_2"]
    assert root_result.dispatched_ai_unit_ids == ["range_0", "range_1", "range_2"]
    assert root_result.completed_ai_unit_ids == ["range_0", "range_1", "range_2"]
    assert root_result.unscheduled_ai_unit_ids == []
    assert root_result.runtime_wall_clock_ms == 0
    assert root_result.attempts == []


def test_projector_preserves_ledger_confirmed_natural_child_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = {
        **_factorization_case(),
        "case_id": "task1_factor_91_rejected",
        "split_params": {
            "strategy_id": "factorization.candidate_range_partition.v1",
            "requested_child_count": 1,
        },
    }
    store = ArtifactStore(tmp_path / "rejected")
    ledger = EventLedger(tmp_path / "rejected" / "events.jsonl")
    config = _config("factorization_rejected")
    adapter = FactorizationRuntimeAdapter(
        provider_family="siliconflow",
        seed=7,
        protocol_config=config,
        created_at=NOW,
    )
    fake = _RangeSubmissionFake(store, corrupt_binding=True)
    protocol_clock = _Clock()
    assembly = RootAssembly(
        run_id="task1_factorization_rejected",
        root_input=case,
        protocol_config=config,
        artifact_store=store,
        event_ledger=ledger,
        plugin_runtime=adapter,
        worker_backend=SequentialWorkerBackend(
            executor=FactorizationExecutionBridge(
                plugin_runtime=adapter,
                range_executor=fake,
            ),
            submitted_at=protocol_clock,
        ),
        now=protocol_clock,
        observation_clock=_ObservationClock(),
    )
    calls = _count_run_root(monkeypatch)

    protocol_result = run_root_slice(assembly)

    assert calls == ["task1_factorization_rejected"]
    assert protocol_result.status == "failed"
    assert len(fake.requests) == config.max_retries + 1
    events = ledger.read_all()
    assert not any(event.event_type == EventType.MERGE_RECORDED for event in events)
    assert len(
        [
            event
            for event in events
            if event.event_type == EventType.VERIFICATION_RECORDED
            and event.payload.get("status") == "rejected"
        ]
    ) == config.max_retries + 1

    ledger_read_all = ledger.read_all
    with monkeypatch.context() as projection_gap:
        projection_gap.setattr(
            ledger,
            "read_all",
            lambda: [
                event
                for event in ledger_read_all()
                if event.event_type != EventType.VERIFICATION_RECORDED
            ],
        )
        with pytest.raises(
            RootProjectionError,
            match="verification/checker rejection fact",
        ):
            project_root_result(
                inventory=_inventory(case=case, domain="factorization"),
                assembly=assembly,
                protocol_result=protocol_result,
                provider_family="siliconflow",
                requested_model="fake-submission",
                resolved_model="fake-submission",
                reasoning_mode="not_applicable",
            )

    root_result = _project_read_only(
        inventory=_inventory(case=case, domain="factorization"),
        assembly=assembly,
        protocol_result=protocol_result,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        artifact_reads_required=False,
    )

    assert calls == ["task1_factorization_rejected"]
    assert root_result.root_status == "failed"
    assert root_result.final_result_present is False
    assert root_result.verified_correct is False
    assert root_result.failure_stage == "child_verification"
    assert root_result.failure_kind == "no_final"
    assert root_result.required_slot_count == 1
    assert root_result.recovered_valid_canonical_slot_count == 0
    assert root_result.dispatched_ai_unit_ids == ["range_0"]
    assert root_result.completed_ai_unit_ids == []
    assert root_result.unscheduled_ai_unit_ids == []


def test_lean_fixed_dag_root_runs_once_through_real_system_vertical_and_projects_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _lean_case("lean_v2_medium_lemma_dag_01")
    store = ArtifactStore(tmp_path / "lean")
    ledger = EventLedger(tmp_path / "lean" / "events.jsonl")
    config = _config("lean")
    checker = RecordingLeanChecker()
    adapter = LeanRuntimeAdapter(
        provider_family="siliconflow",
        environment_manifest=_test_lean_environment(),
        checker=checker,
        protocol_config=config,
        created_at=NOW,
    )
    fake = _LeanSubmissionFake(store)
    protocol_clock = _Clock()
    assembly = RootAssembly(
        run_id="task1_lean_fixed_dag_root",
        root_input=case,
        protocol_config=config,
        artifact_store=store,
        event_ledger=ledger,
        plugin_runtime=adapter,
        worker_backend=SequentialWorkerBackend(
            executor=LeanExecutionBridge(
                plugin_runtime=adapter,
                proof_candidate_executor=fake,
            ),
            submitted_at=protocol_clock,
        ),
        now=protocol_clock,
        observation_clock=_ObservationClock(),
    )
    calls = _count_run_root(monkeypatch)

    protocol_result = run_root_slice(assembly)

    assert calls == ["task1_lean_fixed_dag_root"]
    assert protocol_result.status == "completed"
    assert len(fake.requests) == case["expected_ai_unit_count"]
    assert checker.modes.count(LeanCheckerMode.CHILD_PROOF) == case[
        "expected_ai_unit_count"
    ]
    assert checker.modes[-1] == LeanCheckerMode.MERGE_PROOF
    assert adapter.merge_result.accepted is True
    assert adapter.merge_result.root_checker_report.status == LeanCheckerStatus.ACCEPTED
    assert adapter.merge_result.root_proof_artifact_ref is not None
    events = ledger.read_all()
    _assert_protocol_chain(
        events,
        root_unit_id=protocol_result.root_unit_id,
        expected_child_unit_ids=tuple(request.unit_id for request in fake.requests),
    )

    root_result = _project_read_only(
        inventory=_inventory(case=case, domain="lean"),
        assembly=assembly,
        protocol_result=protocol_result,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert calls == ["task1_lean_fixed_dag_root"]
    assert root_result.root_status == "completed"
    assert root_result.final_result_present is True
    assert root_result.verified_correct is True
    assert len(root_result.planned_ai_unit_ids) == case["expected_ai_unit_count"]
    assert root_result.attempts == []


def _project_read_only(
    *,
    inventory,
    assembly,
    protocol_result,
    tmp_path: Path,
    monkeypatch,
    artifact_reads_required: bool = True,
):
    events_before = assembly.event_ledger.read_all()
    files_before = _file_snapshot(tmp_path)
    ledger_read_all = assembly.event_ledger.read_all
    store_read_bytes = assembly.artifact_store.read_bytes
    read_counts = {"ledger": 0, "artifact": 0}

    def counted_ledger_read_all():
        read_counts["ledger"] += 1
        return ledger_read_all()

    def counted_store_read_bytes(ref):
        read_counts["artifact"] += 1
        return store_read_bytes(ref)

    monkeypatch.setattr(assembly.event_ledger, "read_all", counted_ledger_read_all)
    monkeypatch.setattr(assembly.artifact_store, "read_bytes", counted_store_read_bytes)
    projected = project_root_result(
        inventory=inventory,
        assembly=assembly,
        protocol_result=protocol_result,
        provider_family="siliconflow",
        requested_model="fake-submission",
        resolved_model="fake-submission",
        reasoning_mode="not_applicable",
    )
    projected.validate()
    assert read_counts["ledger"] == 1
    assert (read_counts["artifact"] > 0) is artifact_reads_required
    assert ledger_read_all() == events_before
    assert _file_snapshot(tmp_path) == files_before
    assert projected.trace_tail_provider_attempt_count == 0
    return projected


def _count_run_root(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    real_run_root = ProtocolRunCoordinator.run_root

    def counted(coordinator, request):
        calls.append(request.run_id)
        return real_run_root(coordinator, request)

    monkeypatch.setattr(ProtocolRunCoordinator, "run_root", counted)
    return calls


def _assert_protocol_chain(
    events,
    *,
    root_unit_id: str,
    expected_child_unit_ids: tuple[str, ...],
) -> None:
    event_types = [event.event_type for event in events]
    for event_type in (
        EventType.TASK_REGISTERED,
        EventType.TASK_UNIT_CREATED,
        EventType.EXECUTION_REQUEST_RECORDED,
        EventType.EXECUTION_SUBMISSION_RECORDED,
        EventType.VERIFICATION_RECORDED,
        EventType.CANONICAL_OUTPUTS_BOUND,
        EventType.MERGE_RECORDED,
        EventType.SETTLEMENT_RECORDED,
    ):
        assert event_type in event_types
    merge_events = [
        event for event in events if event.event_type == EventType.MERGE_RECORDED
    ]
    assert len(merge_events) == 1
    merge_event = merge_events[0]
    merge_unit_id = merge_event.payload["merge_unit_id"]
    verification_events = [
        event for event in events if event.event_type == EventType.VERIFICATION_RECORDED
    ]
    child_verifications = [
        event
        for event in verification_events
        if event.payload["unit_id"] not in {root_unit_id, merge_unit_id}
    ]
    assert [
        event.payload["unit_id"] for event in child_verifications
    ] == list(expected_child_unit_ids)
    merge_verifications = [
        event
        for event in verification_events
        if event.payload["unit_id"] == merge_unit_id
    ]
    assert len(merge_verifications) == 1
    assert merge_verifications[0].payload["eligible_for_canonical"] is True
    assert any(
        event.event_type == EventType.CANONICAL_OUTPUTS_BOUND
        and event.event_seq == merge_event.payload["canonical_event_seq"]
        and event.payload["unit_id"] == merge_unit_id
        for event in events
    )
    assert any(
        event.event_type == EventType.TASK_UNIT_STATE_CHANGED
        and event.payload.get("new_state") == "Completed"
        for event in events
    )


def _config(domain: str) -> ProtocolConfig:
    return ProtocolConfig.default(
        config_id=f"task1_{domain}_vertical",
        artifact_store_uri=f"file://{domain}/artifacts",
        event_log_uri=f"file://{domain}/events.jsonl",
    )


def _factorization_case() -> dict[str, object]:
    return {
        "schema_version": "tokenshare.paper_factorization_case.v1",
        "case_id": "task1_factor_169",
        "difficulty": "easy",
        "target_n": "169",
        "candidate_start": "2",
        "candidate_end": "13",
        "split_params": {
            "strategy_id": "factorization.candidate_range_partition.v1",
            "requested_child_count": 3,
        },
        "oracle_prime_factors": [
            {"prime": "13", "exponent": 2},
        ],
    }


def _lean_case(case_id: str) -> dict[str, object]:
    for line in LEAN_CATALOG_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            case = json.loads(line)
            if case["case_id"] == case_id:
                return case
    raise AssertionError(f"missing Lean fixed-DAG case {case_id}")


def _test_lean_environment() -> LeanEnvironmentManifest:
    tools_root = (
        Path.home()
        / "AppData"
        / "Local"
        / "TokenShare"
        / "LeanToolchain"
        / "elan-home"
        / "bin"
    )
    return LeanEnvironmentManifest.from_project(
        project_root=Path("fixtures/lean_proof_project"),
        lean_executable=tools_root / "lean.exe",
        lake_executable=tools_root / "lake.exe",
        lean_version=(
            "Lean (version 4.8.0, x86_64-w64-windows-gnu, "
            "commit df668f00e6c0, Release)"
        ),
        lake_version="Lake version 5.0.0-df668f0 (Lean version 4.8.0)",
        resource_limits={"timeout_seconds": 30, "max_output_bytes": 65536},
        created_at="2026-07-14T00:00:00Z",
    )


def _inventory(*, case: dict[str, object], domain: str) -> RootInventoryV1:
    inventory = RootInventoryV1(
        experiment_id="exp1",
        condition_id=f"task1_{domain}_vertical",
        case_id=str(case["case_id"]),
        repeat_id=0,
        domain=domain,
        difficulty=str(case.get("paper_difficulty", case.get("difficulty"))),
        topic_family=(str(case["topic_family"]) if domain == "lean" else None),
        position_stratum=None,
        worker_count=1,
        mode=None,
        disabled_mechanisms=[],
        fault_type=None,
        fault_rate=None,
        dead_worker_count=None,
        kill_progress_target_ratio=None,
        provider_entry_id="test-local",
        configured_model="fake-submission",
        planned_ai_unit_ids=[],
        challenge_plan_id=None,
    )
    inventory.validate()
    return inventory


def _file_snapshot(root: Path) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            (str(path.relative_to(root)), sha256(path.read_bytes()).hexdigest())
            for path in root.rglob("*")
            if path.is_file()
        )
    )
