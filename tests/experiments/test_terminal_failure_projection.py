from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tokenshare.experiments.projector import project_root_result
from tokenshare.experiments.runtime import RootAssembly, run_root_slice
from tokenshare.local_runtime import SequentialWorkerBackend
from tokenshare.local_runtime.projection import _candidate_terminal_failure
from tokenshare.plugins.factorization.runtime_adapter import (
    FactorizationExecutionBridge,
    FactorizationRuntimeAdapter,
)
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType
from tests.experiments.test_system_vertical import (
    NOW,
    _Clock,
    _ObservationClock,
    _config,
    _factorization_case,
    _inventory,
)


def _recovery(trigger: str, attempt_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        event_type=EventType.RECOVERY_ACTION_RECORDED,
        payload={
            "recovery_action": {
                "trigger": trigger,
                "attempt_id": attempt_id,
            }
        },
    )


def _provider_failure_events(
    store: ArtifactStore,
    *,
    domain: str,
    attempt_id: str,
) -> tuple[SimpleNamespace, SimpleNamespace]:
    submission_ref = store.save_json(
        {
            "domain": domain,
            "parse_failure_ref": None,
            "error": {"kind": "rate_limited", "message": "provider unavailable"},
        },
        artifact_id=f"{domain}_{attempt_id}_provider_failure",
        artifact_type="ExecutionSubmission",
        artifact_schema_id="tokenshare.test.execution_submission",
        artifact_schema_version="v1",
        source={"kind": "test_terminal_failure_projection"},
        metadata={},
        created_at="2026-08-22T00:00:00Z",
    )
    return (
        SimpleNamespace(
            event_type=EventType.EXECUTION_SUBMISSION_RECORDED,
            payload={
                "attempt_id": attempt_id,
                "submission_ref": submission_ref.to_dict(),
            },
        ),
        _recovery("executor_error", attempt_id),
    )


@pytest.mark.parametrize("domain", ["factorization", "lean"])
@pytest.mark.parametrize(
    ("failure_path", "expected_origin"),
    [
        ("parser", "model_parse_exhausted"),
        ("verification", "model_verification_exhausted"),
        ("provider_only", "provider_transport_exhausted"),
    ],
)
def test_shared_terminal_failure_projection_preserves_cross_domain_origin_matrix(
    tmp_path: Path,
    domain: str,
    failure_path: str,
    expected_origin: str,
) -> None:
    store = ArtifactStore(tmp_path / domain / failure_path)
    attempt_id = f"{domain}_{failure_path}_attempt"
    if failure_path == "parser":
        events = (_recovery("parser_failure", attempt_id),)
    elif failure_path == "verification":
        events = (
            SimpleNamespace(
                event_type=EventType.VERIFICATION_RECORDED,
                payload={"status": "rejected", "attempt_id": attempt_id},
            ),
        )
    else:
        events = _provider_failure_events(
            store,
            domain=domain,
            attempt_id=attempt_id,
        )

    assert _candidate_terminal_failure(
        events=events,
        artifact_store=store,
    ) == {
        "failure_stage": "candidate_acquisition",
        "failure_origin": expected_origin,
        "infrastructure_invalid": False,
    }


def test_shared_terminal_failure_projection_marks_transport_and_candidate_mix(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path / "mixed")
    events = (
        *_provider_failure_events(
            store,
            domain="factorization",
            attempt_id="provider_attempt",
        ),
        _recovery("parser_failure", "parser_attempt"),
    )

    assert _candidate_terminal_failure(
        events=events,
        artifact_store=store,
    ) == {
        "failure_stage": "candidate_acquisition",
        "failure_origin": "mixed_candidate_acquisition_failure",
        "infrastructure_invalid": False,
    }


def test_worker_unknown_exception_fails_closed_as_infrastructure(
    tmp_path: Path,
) -> None:
    class _UnexpectedExecutor:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, request, *, submission_id: str, submitted_at: str):
            self.calls += 1
            raise ValueError("unexpected executor defect")

    store = ArtifactStore(tmp_path / "worker_unknown")
    ledger = EventLedger(tmp_path / "worker_unknown" / "events.jsonl")
    config = _config("worker_unknown")
    adapter = FactorizationRuntimeAdapter(
        provider_family="deepseek",
        seed=7,
        protocol_config=config,
        created_at=NOW,
    )
    executor = _UnexpectedExecutor()
    clock = _Clock()
    case = _factorization_case()
    assembly = RootAssembly(
        run_id="worker_unknown",
        root_input=case,
        protocol_config=config,
        artifact_store=store,
        event_ledger=ledger,
        plugin_runtime=adapter,
        worker_backend=SequentialWorkerBackend(
            executor=FactorizationExecutionBridge(
                plugin_runtime=adapter,
                range_executor=executor,
            ),
            submitted_at=clock,
        ),
        now=clock,
        observation_clock=_ObservationClock(),
    )
    result = run_root_slice(assembly)

    assert executor.calls == 3 * (config.max_retries + 1)
    assert result.summary["terminal_failure"] == {
        "failure_stage": "candidate_acquisition",
        "failure_origin": "unexpected_runtime_error",
        "infrastructure_invalid": True,
    }
    projected = project_root_result(
        inventory=_inventory(case=case, domain="factorization"),
        assembly=assembly,
        protocol_result=result,
        provider_family="deepseek",
        requested_model="fake-submission",
        resolved_model="fake-submission",
        reasoning_mode="not_applicable",
    )
    assert projected.failure_kind == "infrastructure_invalid"
    assert projected.failure_origin == "unexpected_runtime_error"


def test_empty_failed_projection_fails_closed_as_infrastructure(
    tmp_path: Path,
) -> None:
    assert _candidate_terminal_failure(
        events=(),
        artifact_store=ArtifactStore(tmp_path / "empty"),
    ) == {
        "failure_stage": "candidate_acquisition",
        "failure_origin": "unexpected_runtime_error",
        "infrastructure_invalid": True,
    }


def test_lease_expiry_without_submission_remains_normal_no_final(
    tmp_path: Path,
) -> None:
    assert _candidate_terminal_failure(
        events=(_recovery("lease_expired", "expired_attempt"),),
        artifact_store=ArtifactStore(tmp_path / "lease_expired"),
    ) == {
        "failure_stage": "candidate_acquisition",
        "failure_origin": "provider_transport_exhausted",
        "infrastructure_invalid": False,
    }


def test_worker_termination_exhaustion_is_scientific_child_execution_failure(
    tmp_path: Path,
) -> None:
    assert _candidate_terminal_failure(
        events=(_recovery("lease_expired", "terminated_attempt"),),
        artifact_store=ArtifactStore(tmp_path / "worker_death"),
        runtime_observation={
            "worker_execution_facts": [
                {
                    "attempt_id": "terminated_attempt",
                    "result_kind": "worker_terminated",
                }
            ]
        },
    ) == {
        "failure_stage": "child_execution",
        "failure_origin": "worker_death_exhausted",
        "infrastructure_invalid": False,
    }
