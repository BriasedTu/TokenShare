from pathlib import Path

import pytest

from tokenshare.experiments.paper_workers import (
    PaperAIUnit,
    WorkerDeathKillPoint,
    freeze_worker_death_plan,
    record_worker_death_observation,
    validate_ai_unit_dependency_graph,
)
from tokenshare.storage.artifacts import ArtifactStore


NOW = "2026-07-15T00:00:00Z"


def test_worker_death_plan_selects_units_without_owning_lease_state() -> None:
    plan = freeze_worker_death_plan(
        condition_id="condition_worker_death",
        repeat_id=0,
        run_id="run_worker_death",
        ai_units=_multi_level_ai_units(),
        target_unit_ids=("unit_lemma_join",),
        kill_point=WorkerDeathKillPoint.PROGRESS_50,
    )

    assert plan.selected_target_unit_ids == ("unit_lemma_join",)
    assert plan.progress_percent == 50
    assert plan.dependency_graph["expected_ai_unit_count"] == 6
    source = Path("src/tokenshare/experiments/paper_workers.py").read_text(
        encoding="utf-8"
    )
    assert "LeaseManager" not in source
    assert "lease_manager.claim" not in source
    assert "lease_manager.expire" not in source


def test_worker_death_record_projects_runtime_facts_and_engine_events(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    plan = freeze_worker_death_plan(
        condition_id="condition_worker_death",
        repeat_id=0,
        run_id="run_worker_death",
        ai_units=_multi_level_ai_units(),
        target_unit_ids=("unit_lemma_join",),
        kill_point=WorkerDeathKillPoint.PROGRESS_50,
    )

    outcome = record_worker_death_observation(
        artifact_store=store,
        plan=plan,
        target_unit_id="unit_lemma_join",
        worker_fact={
            "unit_id": "unit_lemma_join",
            "attempt_id": "attempt_initial",
            "lease_id": "lease_initial",
            "worker_id": "process-worker-1",
            "worker_pid": 1001,
            "process_exitcode": -15,
            "result_kind": "worker_terminated",
            "kill_point": "progress_50",
            "started_at": NOW,
            "ended_at": "2026-07-15T00:00:01Z",
        },
        replacement_fact={
            "unit_id": "unit_lemma_join",
            "attempt_id": "attempt_replacement",
            "lease_id": "lease_replacement",
            "worker_id": "process-worker-2",
            "worker_pid": 1002,
            "process_exitcode": 0,
            "result_kind": "succeeded",
            "started_at": "2026-07-15T00:00:10Z",
            "ended_at": "2026-07-15T00:00:11Z",
        },
        protocol_events=_protocol_events(),
        coordinator_pid=999,
        created_at="2026-07-15T00:00:11Z",
    )

    body = outcome.record.to_dict()
    assert store.verify(outcome.record_ref)
    assert body["target_ai_unit"]["unit_id"] == "unit_lemma_join"
    assert body["worker_process_exitcode"] == -15
    assert body["replacement_process_exitcode"] == 0
    assert body["lease_expiry"]["trigger"] == "lease_expired"
    assert body["dead_attempt"]["attempt_id"] == "attempt_initial"
    assert body["replacement_attempt"]["attempt_id"] == "attempt_replacement"
    assert body["reassignment"]["replacement_lease_id"] == "lease_replacement"
    assert body["protocol_event_refs"] == [
        "event_initial_lease",
        "event_expired_lease",
        "event_superseded",
        "event_ready",
        "event_recovery",
        "event_replacement_lease",
    ]
    assert body["coordinator"] == {
        "schema_version": "tokenshare.paper_worker_coordinator.v1",
        "pid": 999,
        "survived": True,
        "waited_for_lease_expiry": True,
    }


@pytest.mark.parametrize(
    ("kill_point", "expected_progress"),
    [
        (WorkerDeathKillPoint.PROGRESS_25, 25),
        (WorkerDeathKillPoint.PROGRESS_50, 50),
        (WorkerDeathKillPoint.PROGRESS_75, 75),
    ],
)
def test_worker_death_plan_supports_preregistered_kill_points(
    kill_point,
    expected_progress,
) -> None:
    plan = freeze_worker_death_plan(
        condition_id=f"condition_{expected_progress}",
        repeat_id=0,
        run_id=f"run_{expected_progress}",
        ai_units=[
            PaperAIUnit(
                task_id="task_generic",
                unit_id=f"unit_target_{expected_progress}",
                unit_kind="generic_ai_unit",
                dependencies=(),
                depth=0,
            )
        ],
        target_unit_ids=(f"unit_target_{expected_progress}",),
        kill_point=kill_point,
    )

    assert plan.kill_point.value == f"progress_{expected_progress}"
    assert plan.progress_percent == expected_progress


def test_worker_death_record_rejects_missing_engine_recovery_evidence(tmp_path) -> None:
    plan = freeze_worker_death_plan(
        condition_id="condition_missing_evidence",
        repeat_id=0,
        run_id="run_missing_evidence",
        ai_units=_multi_level_ai_units(),
        target_unit_ids=("unit_lemma_join",),
        kill_point=WorkerDeathKillPoint.PROGRESS_25,
    )
    with pytest.raises(ValueError, match="engine worker-death evidence"):
        record_worker_death_observation(
            artifact_store=ArtifactStore(tmp_path),
            plan=plan,
            target_unit_id="unit_lemma_join",
            worker_fact={
                "unit_id": "unit_lemma_join",
                "attempt_id": "attempt_initial",
                "lease_id": "lease_initial",
                "worker_id": "process-worker-1",
                "worker_pid": 1001,
                "process_exitcode": -15,
                "result_kind": "worker_terminated",
            },
            replacement_fact={
                "unit_id": "unit_lemma_join",
                "attempt_id": "attempt_replacement",
                "lease_id": "lease_replacement",
                "worker_id": "process-worker-2",
                "worker_pid": 1002,
                "process_exitcode": 0,
                "result_kind": "succeeded",
            },
            protocol_events=(),
            coordinator_pid=999,
            created_at=NOW,
        )


def test_dependency_graph_validation_rejects_missing_edges_and_cycles() -> None:
    with pytest.raises(ValueError, match="missing dependency"):
        validate_ai_unit_dependency_graph(
            [
                PaperAIUnit(
                    task_id="task_generic",
                    unit_id="unit_a",
                    unit_kind="generic_ai_unit",
                    dependencies=("unit_missing",),
                    depth=1,
                )
            ]
        )

    with pytest.raises(ValueError, match="cycle"):
        validate_ai_unit_dependency_graph(
            [
                PaperAIUnit(
                    task_id="task_generic",
                    unit_id="unit_a",
                    unit_kind="generic_ai_unit",
                    dependencies=("unit_b",),
                    depth=0,
                ),
                PaperAIUnit(
                    task_id="task_generic",
                    unit_id="unit_b",
                    unit_kind="generic_ai_unit",
                    dependencies=("unit_a",),
                    depth=1,
                ),
            ]
        )

    with pytest.raises(ValueError, match="depth"):
        validate_ai_unit_dependency_graph(
            [
                PaperAIUnit(
                    task_id="task_generic",
                    unit_id="unit_leaf",
                    unit_kind="generic_ai_unit",
                    dependencies=(),
                    depth=99,
                ),
                PaperAIUnit(
                    task_id="task_generic",
                    unit_id="unit_root",
                    unit_kind="generic_ai_unit",
                    dependencies=("unit_leaf",),
                    depth=0,
                ),
            ]
        )


def _protocol_events() -> tuple[dict, ...]:
    return (
        {
            "event_id": "event_initial_lease",
            "event_type": "LEASE_STATE_CHANGED",
            "object_id": "lease_initial",
            "payload": {
                "new_state": "Active",
                "lease": {
                    "lease_id": "lease_initial",
                    "attempt_id": "attempt_initial",
                    "unit_id": "unit_lemma_join",
                    "client_id": "process-worker-1",
                    "expires_at": "2026-07-15T00:00:10Z",
                },
            },
        },
        {
            "event_id": "event_expired_lease",
            "event_type": "LEASE_STATE_CHANGED",
            "object_id": "lease_initial",
            "payload": {
                "new_state": "Expired",
                "lease": {
                    "lease_id": "lease_initial",
                    "attempt_id": "attempt_initial",
                    "unit_id": "unit_lemma_join",
                    "expires_at": "2026-07-15T00:00:10Z",
                },
            },
        },
        {
            "event_id": "event_superseded",
            "event_type": "ATTEMPT_STATE_CHANGED",
            "object_id": "attempt_initial",
            "payload": {
                "new_state": "Superseded",
                "attempt": {
                    "attempt_id": "attempt_initial",
                    "lease_id": "lease_initial",
                    "unit_id": "unit_lemma_join",
                    "client_id": "process-worker-1",
                },
            },
        },
        {
            "event_id": "event_ready",
            "event_type": "TASK_UNIT_STATE_CHANGED",
            "object_id": "unit_lemma_join",
            "payload": {"new_state": "Ready", "trigger": "lease_expired"},
        },
        {
            "event_id": "event_recovery",
            "event_type": "RECOVERY_ACTION_RECORDED",
            "object_id": "recovery_initial",
            "payload": {
                "recovery_action": {
                    "trigger": "lease_expired",
                    "unit_id": "unit_lemma_join",
                    "attempt_id": "attempt_initial",
                    "lease_id": "lease_initial",
                    "retry_allowed": True,
                }
            },
        },
        {
            "event_id": "event_replacement_lease",
            "event_type": "LEASE_STATE_CHANGED",
            "object_id": "lease_replacement",
            "payload": {
                "new_state": "Active",
                "lease": {
                    "lease_id": "lease_replacement",
                    "attempt_id": "attempt_replacement",
                    "unit_id": "unit_lemma_join",
                    "client_id": "process-worker-2",
                },
            },
        },
    )


def _multi_level_ai_units() -> list[PaperAIUnit]:
    return [
        PaperAIUnit(
            task_id="task_generic",
            unit_id="unit_sublemma_left",
            unit_kind="generic_ai_unit",
            dependencies=(),
            depth=0,
        ),
        PaperAIUnit(
            task_id="task_generic",
            unit_id="unit_sublemma_middle",
            unit_kind="generic_ai_unit",
            dependencies=(),
            depth=0,
        ),
        PaperAIUnit(
            task_id="task_generic",
            unit_id="unit_sublemma_right",
            unit_kind="generic_ai_unit",
            dependencies=(),
            depth=0,
        ),
        PaperAIUnit(
            task_id="task_generic",
            unit_id="unit_lemma_join",
            unit_kind="generic_ai_unit",
            dependencies=(
                "unit_sublemma_left",
                "unit_sublemma_middle",
                "unit_sublemma_right",
            ),
            depth=1,
        ),
        PaperAIUnit(
            task_id="task_generic",
            unit_id="unit_independent_leaf",
            unit_kind="generic_ai_unit",
            dependencies=(),
            depth=0,
        ),
        PaperAIUnit(
            task_id="task_generic",
            unit_id="unit_root_assembly",
            unit_kind="generic_ai_unit",
            dependencies=("unit_lemma_join", "unit_independent_leaf"),
            depth=2,
        ),
    ]
