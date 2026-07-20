import hashlib
import json
import multiprocessing
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tokenshare.experiments.paper_formal_evidence import FormalEvidenceStore


EXPERIMENT_A = "exp-a"
EXPERIMENT_B = "exp-b"
CONDITION_A = "condition-a"
CONDITION_B = "condition-b"


def test_initialize_writes_required_formal_capturing_manifests(
    tmp_path: Path,
) -> None:
    bodies = _suite_bodies()

    FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **bodies,
        capturing=True,
    )

    required = (
        "suite_manifest.json",
        "run_budget.json",
        "input_catalog_manifest.json",
        "paper_dispatch_plans.json",
        "conditions.jsonl",
        "evidence_manifest.json",
        "experiments/exp-a/experiment_manifest.json",
        "experiments/exp-b/experiment_manifest.json",
    )
    assert all((tmp_path / path).is_file() for path in required)
    suite_manifest = _read_json(tmp_path / "suite_manifest.json")
    assert suite_manifest["status"] == "running"
    assert suite_manifest["formal"] is True
    assert suite_manifest["pilot_only"] is False
    assert suite_manifest["execution_scope"] == "formal_matrix"
    assert suite_manifest["capturing"] is True
    assert suite_manifest["regression_only"] is True
    assert suite_manifest["paper_eligible"] is False
    evidence = _read_json(tmp_path / "evidence_manifest.json")
    assert all(
        {"path", "size", "content_sha256", "record_count", "records_digest"}
        <= set(entry)
        for entry in evidence["files"]
    )


def test_initialize_promotes_matching_zero_call_plan_only_root(
    tmp_path: Path,
) -> None:
    bodies = _suite_bodies()
    plan_budget = {
        **bodies["budget"],
        "quota_preflight": {
            "plan_only": True,
            "provider_calls_made": 0,
        },
    }
    _write_json(
        tmp_path / "suite_manifest.json",
        {
            "suite_id": "paper_v1_plan",
            "status": "planned",
            "experiment_ids": bodies["suite"]["experiment_ids"],
            "provider_attempt_count": 0,
            "total_tokens": 0,
            "total_cost_estimate": 0.0,
            "budget_ref": plan_budget,
        },
    )
    _write_json(tmp_path / "run_budget.json", plan_budget)
    _write_json(
        tmp_path / "paper_dispatch_plans.json",
        {
            "schema_version": "tokenshare.paper_dispatch_plan_bundle.v1",
            "provider_calls_made": 0,
            "plans": bodies["dispatch"]["plans"],
        },
    )
    _write_json(
        tmp_path / "lean_3x3_matrix.json",
        {
            "catalog_digest": bodies["catalog"]["catalog_digest"],
            "provider_calls_made": 0,
        },
    )

    FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **bodies,
        capturing=False,
    )

    suite_manifest = _read_json(tmp_path / "suite_manifest.json")
    assert suite_manifest["status"] == "running"
    assert suite_manifest["formal"] is True
    assert _read_json(tmp_path / "run_budget.json") == bodies["budget"]
    assert _read_json(tmp_path / "paper_dispatch_plans.json") == bodies["dispatch"]
    assert (tmp_path / "lean_3x3_matrix.json").is_file()


def test_initialize_rejects_non_plan_entries_without_overwriting_them(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "supervisor" / "runner.log"
    marker.parent.mkdir()
    marker.write_text("preserve me", encoding="utf-8")

    with pytest.raises(ValueError, match="non-plan entries"):
        FormalEvidenceStore.initialize(
            output_root=tmp_path,
            **_suite_bodies(),
            capturing=False,
        )

    assert marker.read_text(encoding="utf-8") == "preserve me"
    assert not (tmp_path / "evidence_manifest.json").exists()


def test_checkpoint_preserves_artifact_and_loads_completed_task(
    tmp_path: Path,
) -> None:
    bodies = _suite_bodies()
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **bodies,
        capturing=False,
    )
    run_root = tmp_path / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    artifact = run_root / "artifacts" / "raw-response.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b'{"answer": 42}\n')
    artifact_ref = {
        "artifact_id": "raw-response",
        "experiment_id": EXPERIMENT_A,
        "condition_id": CONDITION_A,
        "repeat_id": 0,
        "task_id": "task-1",
        "path": artifact.relative_to(tmp_path).as_posix(),
        "content_hash": _sha256(artifact.read_bytes()),
    }

    store.checkpoint_root(
        experiment_id=EXPERIMENT_A,
        condition=_condition(EXPERIMENT_A, CONDITION_A),
        repeat_id=0,
        task=_task(EXPERIMENT_A, CONDITION_A, status="completed"),
        attempts=[_attempt(EXPERIMENT_A, CONDITION_A)],
        faults=[],
        events=[_event(EXPERIMENT_A, CONDITION_A)],
        artifact_refs=[artifact_ref],
    )
    store.checkpoint_root(
        experiment_id=EXPERIMENT_A,
        condition=_condition(EXPERIMENT_A, CONDITION_A),
        repeat_id=0,
        task={**_task(EXPERIMENT_A, CONDITION_A), "root_status": "failed"},
        attempts=[{**_attempt(EXPERIMENT_A, CONDITION_A), "attempt_id": "attempt-2"}],
        faults=[],
        events=[{**_event(EXPERIMENT_A, CONDITION_A), "event_id": "event-2"}],
        artifact_refs=[artifact_ref],
    )

    generation_root = _current_generation_root(run_root)
    task_records = _read_jsonl(generation_root / "per_task_results.jsonl")
    assert task_records == [_task(EXPERIMENT_A, CONDITION_A, status="completed")]
    assert (generation_root / "artifacts" / "artifact_index.jsonl").is_file()
    run_manifest = _read_json(generation_root / "run_manifest.json")
    assert run_manifest["status"] == "completed"
    assert run_manifest["task_ids"] == ["task-1"]
    assert run_manifest["completed_task_ids"] == ["task-1"]
    assert Path(tmp_path / artifact_ref["path"]).read_bytes() == b'{"answer": 42}\n'
    loaded = FormalEvidenceStore.load(
        output_root=tmp_path,
        **_expected_bodies(bodies),
    )
    assert loaded.completed_task_ids == ("task-1",)
    assert loaded.completed_task_ids_by_experiment == {EXPERIMENT_A: ("task-1",)}


@pytest.mark.parametrize(
    "deleted_relative_path",
    (
        "events/event_log.jsonl",
        "per_attempt_results.jsonl",
        "artifacts/raw-response.json",
    ),
)
def test_load_fails_closed_when_checkpoint_evidence_is_missing(
    tmp_path: Path,
    deleted_relative_path: str,
) -> None:
    bodies = _suite_bodies()
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **bodies,
        capturing=False,
    )
    run_root = tmp_path / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    artifact = run_root / "artifacts" / "raw-response.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"real provider evidence")
    store.checkpoint_root(
        experiment_id=EXPERIMENT_A,
        condition=_condition(EXPERIMENT_A, CONDITION_A),
        repeat_id=0,
        task=_task(EXPERIMENT_A, CONDITION_A, status="completed"),
        attempts=[_attempt(EXPERIMENT_A, CONDITION_A)],
        faults=[],
        events=[_event(EXPERIMENT_A, CONDITION_A)],
        artifact_refs=[
            {
                "experiment_id": EXPERIMENT_A,
                "condition_id": CONDITION_A,
                "repeat_id": 0,
                "task_id": "task-1",
                "path": artifact.relative_to(tmp_path).as_posix(),
                "content_hash": _sha256(artifact.read_bytes()),
            }
        ],
    )
    evidence_path = (
        run_root / deleted_relative_path
        if deleted_relative_path == "artifacts/raw-response.json"
        else _current_generation_root(run_root) / deleted_relative_path
    )
    evidence_path.unlink()

    with pytest.raises(ValueError, match="evidence"):
        FormalEvidenceStore.load(
            output_root=tmp_path,
            **_expected_bodies(bodies),
        )


def test_load_rejects_suite_identity_drift(tmp_path: Path) -> None:
    bodies = _suite_bodies()
    FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **bodies,
        capturing=False,
    )
    expected = _expected_bodies(bodies)
    expected["expected_request_limits"] = {"max_tokens": 2048}

    with pytest.raises(ValueError, match="identity"):
        FormalEvidenceStore.load(output_root=tmp_path, **expected)


def test_checkpoint_rejects_cross_experiment_condition_evidence(
    tmp_path: Path,
) -> None:
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **_suite_bodies(),
        capturing=False,
    )

    with pytest.raises(ValueError, match="experiment"):
        store.checkpoint_root(
            experiment_id=EXPERIMENT_B,
            condition=_condition(EXPERIMENT_A, CONDITION_A),
            repeat_id=0,
            task=_task(EXPERIMENT_A, CONDITION_A, status="completed"),
            attempts=[_attempt(EXPERIMENT_A, CONDITION_A)],
            faults=[],
            events=[_event(EXPERIMENT_A, CONDITION_A)],
            artifact_refs=[],
        )


@pytest.mark.parametrize(
    ("relative_path", "replacement"),
    (
        ("run_budget.json", {"budget_digest": "sha256:" + "9" * 64}),
        ("input_catalog_manifest.json", {"catalog_digest": "sha256:" + "9" * 64}),
        (
            "paper_dispatch_plans.json",
            {
                "schema_version": "tokenshare.paper_dispatch.v1",
                "plans": [],
            },
        ),
        (
            "conditions.jsonl",
            [
                {
                    "experiment_id": EXPERIMENT_A,
                    "condition_id": "condition-tampered",
                    "repeat_id": 0,
                }
            ],
        ),
    ),
)
def test_load_binds_frozen_identity_to_real_suite_files(
    tmp_path: Path,
    relative_path: str,
    replacement: object,
) -> None:
    bodies = _suite_bodies()
    FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **bodies,
        capturing=False,
    )
    path = tmp_path / relative_path
    if relative_path.endswith(".jsonl"):
        _write_jsonl(path, replacement)
    else:
        _write_json(path, replacement)
    _rebuild_evidence_manifest(tmp_path)

    with pytest.raises(ValueError, match="identity"):
        FormalEvidenceStore.load(
            output_root=tmp_path,
            **_expected_bodies(bodies),
        )


def test_completed_checkpoint_requires_artifact_evidence(tmp_path: Path) -> None:
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **_suite_bodies(),
        capturing=False,
    )

    with pytest.raises(ValueError, match="artifact"):
        store.checkpoint_root(
            experiment_id=EXPERIMENT_A,
            condition=_condition(EXPERIMENT_A, CONDITION_A),
            repeat_id=0,
            task=_task(EXPERIMENT_A, CONDITION_A, status="completed"),
            attempts=[_attempt(EXPERIMENT_A, CONDITION_A)],
            faults=[],
            events=[_event(EXPERIMENT_A, CONDITION_A)],
            artifact_refs=[],
        )


def test_checkpoint_rejects_artifact_from_another_run(tmp_path: Path) -> None:
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **_suite_bodies(),
        capturing=False,
    )
    artifact = (
        tmp_path
        / "experiments"
        / EXPERIMENT_A
        / "runs"
        / CONDITION_B
        / "1"
        / "artifacts"
        / "raw.json"
    )
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"wrong run")

    with pytest.raises(ValueError, match="artifact"):
        store.checkpoint_root(
            experiment_id=EXPERIMENT_A,
            condition=_condition(EXPERIMENT_A, CONDITION_A),
            repeat_id=0,
            task=_task(EXPERIMENT_A, CONDITION_A, status="completed"),
            attempts=[_attempt(EXPERIMENT_A, CONDITION_A)],
            faults=[],
            events=[_event(EXPERIMENT_A, CONDITION_A)],
            artifact_refs=[
                {
                    "experiment_id": EXPERIMENT_A,
                    "condition_id": CONDITION_A,
                    "repeat_id": 0,
                    "task_id": "task-1",
                    "path": artifact.relative_to(tmp_path).as_posix(),
                    "content_hash": _sha256(artifact.read_bytes()),
                }
            ],
        )


@pytest.mark.parametrize("record_kind", ("task", "attempt", "event", "fault"))
@pytest.mark.parametrize(
    "missing_field",
    ("experiment_id", "condition_id", "repeat_id", "task_id"),
)
def test_checkpoint_requires_complete_record_context(
    tmp_path: Path,
    record_kind: str,
    missing_field: str,
) -> None:
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **_suite_bodies(),
        capturing=False,
    )
    task = _task(EXPERIMENT_A, CONDITION_A, status="failed")
    attempt = _attempt(EXPERIMENT_A, CONDITION_A)
    event = _event(EXPERIMENT_A, CONDITION_A)
    fault = _fault(EXPERIMENT_A, CONDITION_A)
    selected = {
        "task": task,
        "attempt": attempt,
        "event": event,
        "fault": fault,
    }[record_kind]
    selected.pop(missing_field)

    with pytest.raises(ValueError, match="identity|requires|task_id"):
        store.checkpoint_root(
            experiment_id=EXPERIMENT_A,
            condition=_condition(EXPERIMENT_A, CONDITION_A),
            repeat_id=0,
            task=task,
            attempts=[attempt],
            faults=[fault],
            events=[event],
            artifact_refs=[],
        )


@pytest.mark.parametrize(
    ("target", "field_name", "replacement"),
    (
        ("suite", "formal", False),
        (EXPERIMENT_A, "formal", False),
        (EXPERIMENT_A, "capturing", True),
        (EXPERIMENT_A, "condition_ids", ["condition-tampered"]),
        (EXPERIMENT_A, "dispatch_plan_digest", "sha256:" + "9" * 64),
    ),
)
def test_load_validates_suite_and_experiment_manifest_identity(
    tmp_path: Path,
    target: str,
    field_name: str,
    replacement: object,
) -> None:
    bodies = _suite_bodies()
    FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **bodies,
        capturing=False,
    )
    path = (
        tmp_path / "suite_manifest.json"
        if target == "suite"
        else tmp_path / "experiments" / target / "experiment_manifest.json"
    )
    manifest = _read_json(path)
    manifest[field_name] = replacement
    _write_json(path, manifest)
    _rebuild_evidence_manifest(tmp_path)

    with pytest.raises(ValueError, match="manifest|identity"):
        FormalEvidenceStore.load(
            output_root=tmp_path,
            **_expected_bodies(bodies),
        )


def test_load_rejects_missing_declared_experiment_manifest(tmp_path: Path) -> None:
    bodies = _suite_bodies()
    FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **bodies,
        capturing=False,
    )
    (tmp_path / "experiments" / EXPERIMENT_A / "experiment_manifest.json").unlink()
    _rebuild_evidence_manifest(tmp_path)

    with pytest.raises(ValueError, match="experiment manifest"):
        FormalEvidenceStore.load(
            output_root=tmp_path,
            **_expected_bodies(bodies),
        )


def test_load_rejects_undeclared_experiment_root(tmp_path: Path) -> None:
    bodies = _suite_bodies()
    FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **bodies,
        capturing=False,
    )
    _write_json(
        tmp_path / "experiments" / "exp-extra" / "experiment_manifest.json",
        {"experiment_id": "exp-extra"},
    )
    _rebuild_evidence_manifest(tmp_path)

    with pytest.raises(ValueError, match="undeclared experiment"):
        FormalEvidenceStore.load(
            output_root=tmp_path,
            **_expected_bodies(bodies),
        )


def test_checkpoint_publishes_one_complete_generation(tmp_path: Path) -> None:
    bodies = _suite_bodies()
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **bodies,
        capturing=False,
    )
    _checkpoint_task(store, tmp_path, task_id="task-1")
    run_root = tmp_path / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    current = _read_json(run_root / "CURRENT.json")
    generation_root = _current_generation_root(run_root)

    assert current["generation_manifest_digest"] == _sha256(
        _canonical_json(
            _read_json(generation_root / "generation_manifest.json")
        ).encode("utf-8")
    )
    assert len(list((run_root / ".generations").iterdir())) == 1
    assert all((generation_root / path).is_file() for path in _run_files())


def test_load_ignores_incomplete_noncurrent_generation_residue(
    tmp_path: Path,
) -> None:
    bodies = _suite_bodies()
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **bodies,
        capturing=False,
    )
    _checkpoint_task(store, tmp_path, task_id="task-1")
    run_root = tmp_path / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    interrupted = run_root / ".generations" / "interrupted-generation"
    interrupted.mkdir(parents=True)
    (interrupted / "partial.tmp-data").write_bytes(b"partial")
    _rebuild_evidence_manifest(tmp_path)

    loaded = FormalEvidenceStore.load(
        output_root=tmp_path,
        **_expected_bodies(bodies),
    )

    assert loaded.completed_task_ids == ("task-1",)
    assert interrupted.is_dir()


def test_concurrent_checkpoints_are_serialized_without_lost_tasks(
    tmp_path: Path,
) -> None:
    bodies = _suite_bodies()
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **bodies,
        capturing=False,
    )
    task_ids = tuple(f"task-{index}" for index in range(1, 5))

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(_checkpoint_task, store, tmp_path, task_id=task_id)
            for task_id in task_ids
        ]
        for future in futures:
            future.result()

    loaded = FormalEvidenceStore.load(
        output_root=tmp_path,
        **_expected_bodies(bodies),
    )
    assert loaded.completed_task_ids == task_ids


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    (
        ("size", -1),
        ("content_sha256", "sha256:" + "9" * 64),
        ("record_count", 999),
        ("records_digest", "sha256:" + "8" * 64),
    ),
)
def test_load_rejects_generation_manifest_metadata_tampering(
    tmp_path: Path,
    field_name: str,
    replacement: object,
) -> None:
    bodies = _suite_bodies()
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **bodies,
        capturing=False,
    )
    _checkpoint_task(store, tmp_path, task_id="task-1")
    run_root = tmp_path / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    generation_root = _current_generation_root(run_root)
    generation_manifest_path = generation_root / "generation_manifest.json"
    generation_manifest = _read_json(generation_manifest_path)
    generation_manifest["files"][0][field_name] = replacement
    _write_json(generation_manifest_path, generation_manifest)
    _rewrite_current_digest(run_root, generation_manifest)
    _rebuild_evidence_manifest(tmp_path)

    with pytest.raises(ValueError, match="generation|evidence"):
        FormalEvidenceStore.load(
            output_root=tmp_path,
            **_expected_bodies(bodies),
        )


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    (
        ("task_ids", ["other-task"]),
        ("completed_task_ids", []),
        ("status", "running"),
    ),
)
def test_load_rejects_run_manifest_derived_state_drift(
    tmp_path: Path,
    field_name: str,
    replacement: object,
) -> None:
    bodies = _suite_bodies()
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **bodies,
        capturing=False,
    )
    _checkpoint_task(store, tmp_path, task_id="task-1")
    run_root = tmp_path / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    generation_root = _current_generation_root(run_root)
    run_manifest_path = generation_root / "run_manifest.json"
    run_manifest = _read_json(run_manifest_path)
    run_manifest[field_name] = replacement
    _write_json(run_manifest_path, run_manifest)
    generation_manifest = _rebuild_generation_manifest(generation_root)
    _rewrite_current_digest(run_root, generation_manifest)
    _rebuild_evidence_manifest(tmp_path)

    with pytest.raises(ValueError, match="run manifest"):
        FormalEvidenceStore.load(
            output_root=tmp_path,
            **_expected_bodies(bodies),
        )


@pytest.mark.parametrize("tamper_kind", ("suite_id", "experiment_ids"))
def test_load_rejects_synchronized_suite_manifest_identity_tampering(
    tmp_path: Path,
    tamper_kind: str,
) -> None:
    bodies = _suite_bodies()
    FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **bodies,
        capturing=False,
    )
    suite_path = tmp_path / "suite_manifest.json"
    suite_manifest = _read_json(suite_path)
    if tamper_kind == "suite_id":
        suite_manifest["suite_id"] = "formal-suite-tampered"
        for experiment_id in (EXPERIMENT_A, EXPERIMENT_B):
            experiment_path = (
                tmp_path
                / "experiments"
                / experiment_id
                / "experiment_manifest.json"
            )
            experiment_manifest = _read_json(experiment_path)
            experiment_manifest["suite_id"] = "formal-suite-tampered"
            _write_json(experiment_path, experiment_manifest)
    else:
        suite_manifest["experiment_ids"] = [EXPERIMENT_B, EXPERIMENT_A]
    _write_json(suite_path, suite_manifest)
    _rebuild_evidence_manifest(tmp_path)

    with pytest.raises(ValueError, match="suite manifest.*identity"):
        FormalEvidenceStore.load(
            output_root=tmp_path,
            **_expected_bodies(bodies),
        )


@pytest.mark.parametrize(
    "manifest_kind",
    ("run_manifest", "generation_manifest", "current"),
)
def test_load_rejects_checkpoint_schema_version_tampering(
    tmp_path: Path,
    manifest_kind: str,
) -> None:
    bodies = _suite_bodies()
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **bodies,
        capturing=False,
    )
    _checkpoint_task(store, tmp_path, task_id="task-1")
    run_root = tmp_path / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    generation_root = _current_generation_root(run_root)
    if manifest_kind == "run_manifest":
        path = generation_root / "run_manifest.json"
        manifest = _read_json(path)
        manifest["schema_version"] = "tokenshare.paper_run_evidence.tampered"
        _write_json(path, manifest)
        generation_manifest = _rebuild_generation_manifest(generation_root)
        _rewrite_current_digest(run_root, generation_manifest)
    elif manifest_kind == "generation_manifest":
        path = generation_root / "generation_manifest.json"
        generation_manifest = _read_json(path)
        generation_manifest["schema_version"] = (
            "tokenshare.paper_checkpoint_generation.tampered"
        )
        _write_json(path, generation_manifest)
        _rewrite_current_digest(run_root, generation_manifest)
    else:
        path = run_root / "CURRENT.json"
        current = _read_json(path)
        current["schema_version"] = "tokenshare.paper_checkpoint_current.tampered"
        _write_json(path, current)
    _rebuild_evidence_manifest(tmp_path)

    with pytest.raises(ValueError, match="schema"):
        FormalEvidenceStore.load(
            output_root=tmp_path,
            **_expected_bodies(bodies),
        )


def test_multiprocess_checkpoints_share_one_exclusive_output_lock(
    tmp_path: Path,
) -> None:
    bodies = _suite_bodies()
    FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **bodies,
        capturing=False,
    )
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    processes = [
        context.Process(
            target=_checkpoint_in_process,
            args=(tmp_path.as_posix(), f"process-task-{index}", start),
        )
        for index in (1, 2)
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    loaded = FormalEvidenceStore.load(
        output_root=tmp_path,
        **_expected_bodies(bodies),
    )
    assert loaded.completed_task_ids == ("process-task-1", "process-task-2")


@pytest.mark.parametrize("residual_id", ("old-complete", "new-unpublished"))
def test_load_ignores_complete_noncurrent_generation_residue(
    tmp_path: Path,
    residual_id: str,
) -> None:
    bodies = _suite_bodies()
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **bodies,
        capturing=False,
    )
    _checkpoint_task(store, tmp_path, task_id="task-1")
    run_root = tmp_path / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    current_root = _current_generation_root(run_root)
    residual_root = run_root / ".generations" / residual_id
    shutil.copytree(current_root, residual_root)
    residual_run = _read_json(residual_root / "run_manifest.json")
    residual_run["generation_id"] = residual_id
    _write_json(residual_root / "run_manifest.json", residual_run)
    _rebuild_generation_manifest(residual_root)

    loaded = FormalEvidenceStore.load(
        output_root=tmp_path,
        **_expected_bodies(bodies),
    )
    assert loaded.completed_task_ids == ("task-1",)


@pytest.mark.parametrize("damage", ("missing", "incomplete"))
def test_load_rejects_missing_or_incomplete_current_generation(
    tmp_path: Path,
    damage: str,
) -> None:
    bodies = _suite_bodies()
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **bodies,
        capturing=False,
    )
    _checkpoint_task(store, tmp_path, task_id="task-1")
    run_root = tmp_path / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    generation_root = _current_generation_root(run_root)
    if damage == "missing":
        shutil.rmtree(generation_root)
    else:
        (generation_root / "run_manifest.json").unlink()
    _rebuild_evidence_manifest(tmp_path)

    with pytest.raises(ValueError, match="generation|evidence"):
        FormalEvidenceStore.load(
            output_root=tmp_path,
            **_expected_bodies(bodies),
        )


@pytest.mark.parametrize(
    ("manifest_kind", "mutation_kind"),
    (
        ("run_manifest", "extra"),
        ("run_manifest", "type"),
        ("generation_manifest", "extra"),
        ("generation_manifest", "type"),
        ("current", "extra"),
        ("current", "type"),
    ),
)
def test_load_rejects_checkpoint_manifest_key_or_type_drift(
    tmp_path: Path,
    manifest_kind: str,
    mutation_kind: str,
) -> None:
    bodies = _suite_bodies()
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **bodies,
        capturing=False,
    )
    _checkpoint_task(store, tmp_path, task_id="task-1")
    run_root = tmp_path / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    generation_root = _current_generation_root(run_root)
    if manifest_kind == "run_manifest":
        path = generation_root / "run_manifest.json"
        manifest = _read_json(path)
        if mutation_kind == "extra":
            manifest["unexpected"] = True
        else:
            manifest["task_ids"] = "task-1"
        _write_json(path, manifest)
        generation_manifest = _rebuild_generation_manifest(generation_root)
        _rewrite_current_digest(run_root, generation_manifest)
    elif manifest_kind == "generation_manifest":
        path = generation_root / "generation_manifest.json"
        generation_manifest = _read_json(path)
        if mutation_kind == "extra":
            generation_manifest["unexpected"] = True
        else:
            generation_manifest["files"] = {}
        _write_json(path, generation_manifest)
        _rewrite_current_digest(run_root, generation_manifest)
    else:
        path = run_root / "CURRENT.json"
        current = _read_json(path)
        if mutation_kind == "extra":
            current["unexpected"] = True
        else:
            current["generation_manifest_digest"] = 7
        _write_json(path, current)
    _rebuild_evidence_manifest(tmp_path)

    with pytest.raises(ValueError, match="keys|type"):
        FormalEvidenceStore.load(
            output_root=tmp_path,
            **_expected_bodies(bodies),
        )


def test_external_expected_suite_rejects_fully_synchronized_identity_forgery(
    tmp_path: Path,
) -> None:
    bodies = _suite_bodies()
    FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **bodies,
        capturing=False,
    )
    suite_path = tmp_path / "suite_manifest.json"
    suite_manifest = _read_json(suite_path)
    forged_suite_id = "formal-suite-forged"
    suite_manifest["suite_id"] = forged_suite_id
    suite_manifest["suite_identity"]["suite"]["body"]["suite_id"] = forged_suite_id
    forged_body = suite_manifest["suite_identity"]["suite"]["body"]
    suite_manifest["suite_identity"]["suite"]["digest"] = _sha256(
        _canonical_json(forged_body).encode("utf-8")
    )
    _write_json(suite_path, suite_manifest)
    for experiment_id in (EXPERIMENT_A, EXPERIMENT_B):
        experiment_path = (
            tmp_path / "experiments" / experiment_id / "experiment_manifest.json"
        )
        experiment_manifest = _read_json(experiment_path)
        experiment_manifest["suite_id"] = forged_suite_id
        _write_json(experiment_path, experiment_manifest)
    _rebuild_evidence_manifest(tmp_path)

    with pytest.raises(ValueError, match="suite identity drift"):
        FormalEvidenceStore.load(
            output_root=tmp_path,
            **_expected_bodies(bodies),
        )


def _suite_bodies() -> dict[str, object]:
    conditions = [
        _condition(EXPERIMENT_A, CONDITION_A),
        _condition(EXPERIMENT_B, CONDITION_B),
    ]
    return {
        "suite": {
            "schema_version": "tokenshare.paper_suite.v1",
            "suite_id": "formal-suite-1",
            "experiment_ids": [EXPERIMENT_A, EXPERIMENT_B],
        },
        "dispatch": {
            "schema_version": "tokenshare.paper_dispatch.v1",
            "plans": [
                {"experiment_id": EXPERIMENT_A, "conditions": conditions[:1]},
                {"experiment_id": EXPERIMENT_B, "conditions": conditions[1:]},
            ],
        },
        "budget": {"budget_digest": "sha256:" + "1" * 64},
        "catalog": {"catalog_digest": "sha256:" + "2" * 64},
        "identity": {"endpoint_digest": "sha256:" + "3" * 64},
        "request_limits": {"max_tokens": 1024},
        "hard_limits": {"max_provider_attempts": 2},
    }


def _expected_bodies(bodies: dict[str, object]) -> dict[str, object]:
    return {f"expected_{name}": body for name, body in bodies.items()}


def _condition(experiment_id: str, condition_id: str) -> dict[str, object]:
    return {
        "experiment_id": experiment_id,
        "condition_id": condition_id,
        "repeat_id": 0,
    }


def _task(
    experiment_id: str,
    condition_id: str,
    *,
    status: str = "completed",
    task_id: str = "task-1",
) -> dict[str, object]:
    return {
        "experiment_id": experiment_id,
        "condition_id": condition_id,
        "repeat_id": 0,
        "task_id": task_id,
        "root_status": status,
    }


def _attempt(
    experiment_id: str,
    condition_id: str,
    *,
    task_id: str = "task-1",
) -> dict[str, object]:
    return {
        "experiment_id": experiment_id,
        "condition_id": condition_id,
        "repeat_id": 0,
        "task_id": task_id,
        "attempt_id": f"attempt-{task_id}",
        "attempt_status": "success",
    }


def _event(
    experiment_id: str,
    condition_id: str,
    *,
    task_id: str = "task-1",
) -> dict[str, object]:
    return {
        "experiment_id": experiment_id,
        "condition_id": condition_id,
        "repeat_id": 0,
        "task_id": task_id,
        "event_id": f"event-{task_id}",
        "event_type": "TASK_COMPLETED",
    }


def _fault(
    experiment_id: str,
    condition_id: str,
    *,
    task_id: str = "task-1",
) -> dict[str, object]:
    return {
        "experiment_id": experiment_id,
        "condition_id": condition_id,
        "repeat_id": 0,
        "task_id": task_id,
        "fault_injection_id": f"fault-{task_id}",
        "fault_type": "none",
    }


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _sha256(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _write_json(path: Path, body: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_canonical_json(body) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, records: object) -> None:
    assert isinstance(records, list)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(_canonical_json(record) + "\n" for record in records),
        encoding="utf-8",
    )


def _canonical_json(body: object) -> str:
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _rebuild_evidence_manifest(root: Path) -> None:
    entries = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "evidence_manifest.json":
            continue
        content = path.read_bytes()
        relative_path = path.relative_to(root).as_posix()
        records = None
        artifact_payload = (
            "/artifacts/" in f"/{relative_path}"
            and not relative_path.endswith("artifact_index.jsonl")
        )
        if not artifact_payload and path.suffix == ".json":
            records = [json.loads(content.decode("utf-8"))]
        elif path.suffix == ".jsonl":
            records = [
                json.loads(line)
                for line in content.decode("utf-8").splitlines()
                if line.strip()
            ]
        entries.append(
            {
                "path": relative_path,
                "size": len(content),
                "content_sha256": _sha256(content),
                "record_count": None if records is None else len(records),
                "records_digest": (
                    None
                    if records is None
                    else _sha256(_canonical_json(records).encode("utf-8"))
                ),
            }
        )
    _write_json(
        root / "evidence_manifest.json",
        {
            "schema_version": "tokenshare.paper_evidence_manifest.v1",
            "files": entries,
        },
    )


def _checkpoint_task(
    store: FormalEvidenceStore,
    root: Path,
    *,
    task_id: str,
) -> None:
    run_root = root / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    artifact = run_root / "artifacts" / f"{task_id}.bin"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(f"evidence:{task_id}".encode("utf-8"))
    store.checkpoint_root(
        experiment_id=EXPERIMENT_A,
        condition=_condition(EXPERIMENT_A, CONDITION_A),
        repeat_id=0,
        task=_task(
            EXPERIMENT_A,
            CONDITION_A,
            status="completed",
            task_id=task_id,
        ),
        attempts=[_attempt(EXPERIMENT_A, CONDITION_A, task_id=task_id)],
        faults=[],
        events=[_event(EXPERIMENT_A, CONDITION_A, task_id=task_id)],
        artifact_refs=[
            {
                "experiment_id": EXPERIMENT_A,
                "condition_id": CONDITION_A,
                "repeat_id": 0,
                "task_id": task_id,
                "path": artifact.relative_to(root).as_posix(),
                "content_hash": _sha256(artifact.read_bytes()),
            }
        ],
    )


def _checkpoint_in_process(root: str, task_id: str, start: object) -> None:
    store = FormalEvidenceStore(Path(root))
    assert start.wait(timeout=10)
    _checkpoint_task(store, Path(root), task_id=task_id)


def _current_generation_root(run_root: Path) -> Path:
    current = _read_json(run_root / "CURRENT.json")
    return run_root / ".generations" / str(current["generation_id"])


def _run_files() -> tuple[str, ...]:
    return (
        "run_manifest.json",
        "per_task_results.jsonl",
        "per_attempt_results.jsonl",
        "fault_injections.jsonl",
        "events/event_log.jsonl",
        "artifacts/artifact_index.jsonl",
        "generation_manifest.json",
    )


def _rebuild_generation_manifest(generation_root: Path) -> dict[str, object]:
    files = [
        _evidence_entry(generation_root, generation_root / relative_path)
        for relative_path in _run_files()
        if relative_path != "generation_manifest.json"
    ]
    generation_manifest = {
        "schema_version": "tokenshare.paper_checkpoint_generation.v1",
        "generation_id": generation_root.name,
        "files": files,
    }
    _write_json(generation_root / "generation_manifest.json", generation_manifest)
    return generation_manifest


def _rewrite_current_digest(
    run_root: Path,
    generation_manifest: dict[str, object],
) -> None:
    current = _read_json(run_root / "CURRENT.json")
    current["generation_manifest_digest"] = _sha256(
        _canonical_json(generation_manifest).encode("utf-8")
    )
    _write_json(run_root / "CURRENT.json", current)


def _evidence_entry(root: Path, path: Path) -> dict[str, object]:
    content = path.read_bytes()
    relative_path = path.relative_to(root).as_posix()
    records = None
    if path.suffix == ".json":
        records = [json.loads(content.decode("utf-8"))]
    elif path.suffix == ".jsonl":
        records = [
            json.loads(line)
            for line in content.decode("utf-8").splitlines()
            if line.strip()
        ]
    return {
        "path": relative_path,
        "size": len(content),
        "content_sha256": _sha256(content),
        "record_count": None if records is None else len(records),
        "records_digest": (
            None
            if records is None
            else _sha256(_canonical_json(records).encode("utf-8"))
        ),
    }
