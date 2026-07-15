import pytest

from tokenshare.experiments.paper_workers import (
    PaperAIUnit,
    WorkerDeathKillPoint,
    run_worker_death_harness,
    validate_ai_unit_dependency_graph,
)
from tokenshare.storage.artifacts import ArtifactStore


NOW = "2026-07-15T00:00:00Z"


def test_worker_death_harness_reassigns_generic_ai_unit_from_dependency_graph(
    tmp_path,
) -> None:
    store = ArtifactStore(tmp_path)

    outcome = run_worker_death_harness(
        artifact_store=store,
        condition_id="condition_worker_death",
        repeat_id=0,
        run_id="run_worker_death",
        ai_units=_multi_level_ai_units(),
        target_unit_id="unit_lemma_join",
        kill_point=WorkerDeathKillPoint.PROGRESS_50,
        started_at=NOW,
        process_tick_seconds=0.01,
    )

    body = outcome.record.to_dict()

    assert store.verify(outcome.record_ref)
    assert body["schema_version"] == "tokenshare.paper_worker_death.v1"
    assert body["condition_id"] == "condition_worker_death"
    assert body["run_id"] == "run_worker_death"
    assert body["target_ai_unit"]["unit_id"] == "unit_lemma_join"
    assert body["target_ai_unit"]["dependencies"] == [
        "unit_sublemma_left",
        "unit_sublemma_middle",
        "unit_sublemma_right",
    ]
    assert body["dependency_graph"]["schema_version"] == (
        "tokenshare.paper_ai_dependency_graph.v1"
    )
    assert body["dependency_graph"]["expected_ai_unit_count"] == 6
    assert body["dependency_graph"]["max_depth"] == 2
    assert {
        "source_unit_id": "unit_sublemma_middle",
        "target_unit_id": "unit_lemma_join",
    } in body["dependency_graph"]["dependency_edges"]
    assert body["kill_point"] == "progress_50"
    assert body["progress_before_kill"] >= 50
    assert body["worker_id"] != body["replacement_worker_id"]
    assert body["worker_pid"] != body["replacement_worker_pid"]
    assert body["worker_process_exitcode"] not in (0, None)
    assert body["coordinator"]["survived"] is True
    assert body["lease_expiry"]["trigger"] == "lease_expired"
    assert body["lease_expiry"]["expired_at"] == body["initial_lease"]["expires_at"]
    assert body["dead_attempt"]["attempt_id"] != body["replacement_attempt"]["attempt_id"]
    assert body["dead_attempt"]["unit_id"] == "unit_lemma_join"
    assert body["replacement_attempt"]["unit_id"] == "unit_lemma_join"
    assert body["replacement_attempt"]["worker_id"] == body["replacement_worker_id"]
    assert body["reassignment"]["replacement_attempt_id"] == (
        body["replacement_attempt"]["attempt_id"]
    )
    assert body["canonical_pollution"] is False
    assert body["provider_tokens_attributed"] == 0


@pytest.mark.parametrize(
    ("kill_point", "expected_progress"),
    [
        (WorkerDeathKillPoint.PROGRESS_25, 25),
        (WorkerDeathKillPoint.PROGRESS_50, 50),
        (WorkerDeathKillPoint.PROGRESS_75, 75),
    ],
)
def test_worker_death_harness_supports_required_kill_points(
    tmp_path,
    kill_point,
    expected_progress,
) -> None:
    store = ArtifactStore(tmp_path)

    outcome = run_worker_death_harness(
        artifact_store=store,
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
        target_unit_id=f"unit_target_{expected_progress}",
        kill_point=kill_point,
        started_at=NOW,
        process_tick_seconds=0.01,
    )

    body = outcome.record.to_dict()

    assert body["kill_point"] == f"progress_{expected_progress}"
    assert body["progress_before_kill"] >= expected_progress
    assert body["worker_process_exitcode"] not in (0, None)
    assert body["replacement_process_exitcode"] == 0


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
