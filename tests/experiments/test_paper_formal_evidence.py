import hashlib
import json
import multiprocessing
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import tokenshare.experiments.paper_formal_metrics as formal_metrics
import tokenshare.experiments.paper_formal_evidence as formal_evidence
from tokenshare.experiments.paper_formal_checkpoint import (
    iter_v3_delta_chain,
    validate_v3_delta_chain,
    validate_v3_generation_manifest,
)
from tokenshare.experiments.paper_formal_evidence import FormalEvidenceStore
from tokenshare.storage.events import EventLedger


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
    assert evidence["schema_version"] == "tokenshare.paper_evidence_manifest.v2"
    assert evidence["conditions"] == []
    assert all(
        {"path", "size", "content_sha256", "record_count", "records_digest"}
        <= set(entry)
        for entry in evidence["files"]
    )


def test_resume_archives_uncheckpointed_adapter_evidence_before_load(
    tmp_path: Path,
) -> None:
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **_suite_bodies(),
        capturing=True,
    )
    orphan_root = tmp_path / EXPERIMENT_A / "runs" / CONDITION_A / "case-1"
    orphan_file = orphan_root / "events" / "adapter.jsonl"
    orphan_file.parent.mkdir(parents=True)
    orphan_file.write_text('{"event_type":"FACILITY_FAILURE"}\n', encoding="utf-8")

    # adapter working tree 不属于 canonical evidence 闭包；显式 archive API
    # 仍可保存现场，但 load/validate 不应把它误当成论文证据。
    store._validate_evidence_manifest()

    repair = store.archive_uncheckpointed_adapter_runs()

    assert repair is not None
    assert repair["repair_kind"] == "uncheckpointed_adapter_evidence_archive"
    assert repair["archived_condition_count"] == 1
    assert not (tmp_path / EXPERIMENT_A / "runs" / CONDITION_A).exists()
    archived = repair["archived_files"][0]
    assert archived["source_path"].endswith("events/adapter.jsonl")
    assert (tmp_path / archived["archived_ref"]["path"]).read_text(
        encoding="utf-8"
    ) == '{"event_type":"FACILITY_FAILURE"}\n'
    assert repair["paper_eligible"] is False
    store._validate_evidence_manifest()


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
    with pytest.raises(ValueError, match="immutable"):
        store.checkpoint_root(
            experiment_id=EXPERIMENT_A,
            condition=_condition(EXPERIMENT_A, CONDITION_A),
            repeat_id=0,
            task={**_task(EXPERIMENT_A, CONDITION_A), "root_status": "failed"},
            attempts=[
                {**_attempt(EXPERIMENT_A, CONDITION_A), "attempt_id": "attempt-2"}
            ],
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


def test_checkpoint_writes_condition_manifest_with_committed_denominator_chain(
    tmp_path: Path,
) -> None:
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **_suite_bodies(),
        capturing=False,
    )
    _checkpoint_task(store, tmp_path, task_id="task-1")
    run_root = tmp_path / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    manifest_path = run_root / "condition_manifest.json"

    manifest = _read_json(manifest_path)
    condition = _condition(EXPERIMENT_A, CONDITION_A)
    assert manifest["schema_version"] == "tokenshare.paper_condition_evidence.v2"
    assert manifest["experiment_id"] == EXPERIMENT_A
    assert manifest["condition_id"] == CONDITION_A
    assert manifest["repeat_id"] == 0
    assert manifest["condition_identity"] == {
        "body": condition,
        "digest": _sha256(_canonical_json(condition).encode("utf-8")),
    }
    assert manifest["expected_root_count"] == 1
    assert manifest["observed_root_count"] == 1
    assert manifest["terminal_root_count"] == 1
    assert manifest["status"] == "running"
    assert manifest["terminal"] is False
    assert manifest["head_generation_kind"] == "delta"
    assert manifest["chain_generation_count"] == 1
    assert manifest["root_delta_count"] == 1
    assert manifest["condition_event_delta_count"] == 0
    assert manifest["logical_task_count"] == 1
    assert manifest["paper_eligible"] is False
    assert manifest["ineligibility_reasons"] == ["condition_incomplete"]
    current = _read_json(run_root / "CURRENT.json")
    generation_root = run_root / ".generations" / current["generation_id"]
    assert manifest["current_ref"] == _evidence_entry(
        tmp_path,
        run_root / "CURRENT.json",
    )
    assert manifest["generation_manifest_ref"] == _evidence_entry(
        tmp_path,
        generation_root / "generation_manifest.json",
    )
    assert manifest["run_manifest_ref"] == _evidence_entry(
        tmp_path,
        generation_root / "run_manifest.json",
    )
    inventory = _read_json(tmp_path / "evidence_manifest.json")
    assert inventory["conditions"] == [
        {
            "experiment_id": EXPERIMENT_A,
            "condition_id": CONDITION_A,
            "repeat_id": 0,
            "condition_manifest_ref": _evidence_entry(tmp_path, manifest_path),
            "reachable_size_bytes": manifest["reachable_size_bytes"],
        }
    ]


@pytest.mark.parametrize(
    "crash_stage",
    (
        "generation_manifest_written",
        "current_written",
        "condition_manifest_written",
        "inventory_refreshed",
    ),
)
def test_load_repairs_interrupted_checkpoint_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_stage: str,
) -> None:
    bodies = _suite_bodies()
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **bodies,
        capturing=False,
    )

    def crash_hook(*, stage: str, run_root: Path) -> None:
        del run_root
        if stage == crash_stage:
            raise RuntimeError(f"injected crash after {stage}")

    monkeypatch.setattr(
        store,
        "_checkpoint_publication_hook",
        crash_hook,
        raising=False,
    )
    with pytest.raises(RuntimeError, match="injected crash"):
        _checkpoint_task(store, tmp_path, task_id="task-1")

    loaded = FormalEvidenceStore.load(
        output_root=tmp_path,
        **_expected_bodies(bodies),
    )

    assert loaded.completed_task_keys == (
        (EXPERIMENT_A, CONDITION_A, "0", "task-1"),
    )
    run_root = tmp_path / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    assert (run_root / "condition_manifest.json").is_file()
    FormalEvidenceStore(tmp_path)._validate_evidence_manifest()


@pytest.mark.parametrize(
    "crash_stage",
    ("generation_manifest_written", "current_written"),
)
def test_load_repairs_pending_child_generation_against_existing_current(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_stage: str,
) -> None:
    bodies = _suite_bodies_with_two_roots()
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **bodies,
        capturing=False,
    )
    _checkpoint_task(store, tmp_path, task_id="task-1")

    def crash_hook(*, stage: str, run_root: Path) -> None:
        del run_root
        if stage == crash_stage:
            raise RuntimeError(f"injected crash after {stage}")

    monkeypatch.setattr(store, "_checkpoint_publication_hook", crash_hook)
    with pytest.raises(RuntimeError, match="injected crash"):
        _checkpoint_task(store, tmp_path, task_id="task-2")

    loaded = FormalEvidenceStore.load(
        output_root=tmp_path,
        **_expected_bodies(bodies),
    )

    assert loaded.completed_task_keys == (
        (EXPERIMENT_A, CONDITION_A, "0", "task-1"),
        (EXPERIMENT_A, CONDITION_A, "0", "task-2"),
    )
    run_root = tmp_path / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    assert not (run_root / "PENDING.json").exists()


def test_load_does_not_promote_complete_v2_orphans_without_pending(
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
    current_before = _read_json(run_root / "CURRENT.json")
    current_root = _current_generation_root(run_root)
    for suffix in ("a", "b"):
        orphan_id = suffix * 32
        orphan_root = run_root / ".generations" / orphan_id
        shutil.copytree(current_root, orphan_root)
        orphan_run = _read_json(orphan_root / "run_manifest.json")
        orphan_run["generation_id"] = orphan_id
        _write_json(orphan_root / "run_manifest.json", orphan_run)
        orphan_manifest = _read_json(orphan_root / "generation_manifest.json")
        orphan_manifest["generation_id"] = orphan_id
        orphan_manifest["parent_generation_id"] = current_root.name
        orphan_manifest["parent_generation_manifest_digest"] = current_before[
            "generation_manifest_digest"
        ]
        _write_json(orphan_root / "generation_manifest.json", orphan_manifest)
        _rebuild_generation_manifest(orphan_root)

    loaded = FormalEvidenceStore.load(
        output_root=tmp_path,
        **_expected_bodies(bodies),
    )

    assert loaded.completed_task_ids == ("task-1",)
    assert _read_json(run_root / "CURRENT.json") == current_before


def test_v3_root_delta_publication_is_immutable_and_does_not_copy_prior_jsonl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **_suite_bodies_with_two_roots(),
        capturing=False,
    )
    first_commit = _checkpoint_task(
        store,
        tmp_path,
        task_id="task-1",
        selection_ordinal=0,
    )
    run_root = tmp_path / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    first_root = run_root / ".generations" / first_commit["generation_id"]
    first_bytes = {
        path.relative_to(first_root).as_posix(): path.read_bytes()
        for path in first_root.rglob("*")
        if path.is_file()
    }

    def reject_prior_jsonl(*args: object, **kwargs: object) -> list[object]:
        del args, kwargs
        raise AssertionError("v3 root publication must not read prior JSONL")

    monkeypatch.setattr(formal_evidence, "_generation_jsonl", reject_prior_jsonl)
    second_commit = _checkpoint_task(
        store,
        tmp_path,
        task_id="task-2",
        selection_ordinal=1,
    )

    assert first_root.is_dir()
    assert first_bytes == {
        path.relative_to(first_root).as_posix(): path.read_bytes()
        for path in first_root.rglob("*")
        if path.is_file()
    }
    second_root = run_root / ".generations" / second_commit["generation_id"]
    assert _read_jsonl(second_root / "per_task_results.jsonl")[0]["task_id"] == "task-2"
    assert len(_read_jsonl(second_root / "per_task_results.jsonl")) == 1
    second_manifest = _read_json(second_root / "generation_manifest.json")
    assert second_manifest["schema_version"] == "tokenshare.paper_checkpoint_generation.v3"
    assert second_manifest["parent_generation_id"] == first_commit["generation_id"]
    head = validate_v3_generation_manifest(second_root, second_manifest)
    assert [
        item.generation_id
        for item in iter_v3_delta_chain(run_root, head, expected_root_count=2)
    ] == [first_commit["generation_id"], second_commit["generation_id"]]
    validate_v3_delta_chain(run_root, head, expected_root_count=2)
    assert second_commit["current_digest"] == _sha256(
        _canonical_json(_read_json(run_root / "CURRENT.json")).encode("utf-8")
    )


@pytest.mark.parametrize(
    "crash_stage",
    (
        "intent_written",
        "current_written",
        "condition_manifest_written",
        "inventory_refreshed",
    ),
)
def test_v3_root_delta_repair_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_stage: str,
) -> None:
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **_suite_bodies(),
        capturing=False,
    )

    def crash_hook(*, stage: str, run_root: Path) -> None:
        del run_root
        if stage == crash_stage:
            raise RuntimeError("injected v3 crash")

    monkeypatch.setattr(store, "_checkpoint_publication_hook", crash_hook)
    with pytest.raises(RuntimeError, match="injected v3 crash"):
        _checkpoint_task(
            store,
            tmp_path,
            task_id="task-1",
            selection_ordinal=0,
        )

    repair_store = FormalEvidenceStore(tmp_path)
    repair_store.repair_interrupted_checkpoint_publication()
    repair_store.repair_interrupted_checkpoint_publication()
    run_root = tmp_path / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    assert not (run_root / "PENDING.json").exists()
    current = _read_json(run_root / "CURRENT.json")
    current_root = run_root / ".generations" / current["generation_id"]
    descriptor = validate_v3_generation_manifest(current_root)
    assert descriptor.anchor_task_id == "task-1"
    assert descriptor.selection_ordinal == 0


def test_v3_target_without_intent_remains_an_orphan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **_suite_bodies(),
        capturing=False,
    )

    def crash_hook(*, stage: str, run_root: Path) -> None:
        del run_root
        if stage == "target_written":
            raise RuntimeError("injected pre-intent crash")

    monkeypatch.setattr(store, "_checkpoint_publication_hook", crash_hook)
    with pytest.raises(RuntimeError, match="pre-intent"):
        _checkpoint_task(
            store,
            tmp_path,
            task_id="task-1",
            selection_ordinal=0,
        )

    FormalEvidenceStore(tmp_path).repair_interrupted_checkpoint_publication()
    run_root = tmp_path / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    assert not (run_root / "CURRENT.json").exists()
    assert not (run_root / "PENDING.json").exists()
    assert len(list((run_root / ".generations").iterdir())) == 1


def test_v3_duplicate_terminal_root_is_exactly_idempotent_and_drift_fails(
    tmp_path: Path,
) -> None:
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **_suite_bodies(),
        capturing=False,
    )
    first = _checkpoint_task(
        store,
        tmp_path,
        task_id="task-1",
        selection_ordinal=0,
    )
    duplicate = _checkpoint_task(
        store,
        tmp_path,
        task_id="task-1",
        selection_ordinal=0,
    )
    assert duplicate == first

    run_root = tmp_path / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    artifact = run_root / "artifacts" / "task-1.bin"
    with pytest.raises(ValueError, match="immutable|conflict|drift"):
        store.checkpoint_root(
            experiment_id=EXPERIMENT_A,
            condition=_condition(EXPERIMENT_A, CONDITION_A),
            repeat_id=0,
            selection_ordinal=0,
            task={
                **_task(
                    EXPERIMENT_A,
                    CONDITION_A,
                    status="failed",
                    task_id="task-1",
                ),
                "paper_eligible": False,
            },
            attempts=[_attempt(EXPERIMENT_A, CONDITION_A, task_id="task-1")],
            faults=[],
            events=[_event(EXPERIMENT_A, CONDITION_A, task_id="task-1")],
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


def test_v3_condition_tail_is_one_event_only_delta_and_exactly_idempotent(
    tmp_path: Path,
) -> None:
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **_suite_bodies_with_two_roots(),
        capturing=False,
    )
    _checkpoint_task(store, tmp_path, task_id="task-1", selection_ordinal=0)
    _checkpoint_task(store, tmp_path, task_id="task-2", selection_ordinal=1)

    commit = store.checkpoint_condition_tail_events(
        experiment_id=EXPERIMENT_A,
        condition=_condition(EXPERIMENT_A, CONDITION_A),
        repeat_id=0,
        anchor_task_id="task-2",
        events=[],
    )
    duplicate = store.checkpoint_condition_tail_events(
        experiment_id=EXPERIMENT_A,
        condition=_condition(EXPERIMENT_A, CONDITION_A),
        repeat_id=0,
        anchor_task_id="task-2",
        events=[],
    )

    assert duplicate == commit
    run_root = tmp_path / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    tail_root = run_root / ".generations" / commit["generation_id"]
    tail_manifest = _read_json(tail_root / "generation_manifest.json")
    assert tail_manifest["delta_role"] == "condition_tail_events"
    for relative_path in (
        "per_task_results.jsonl",
        "per_attempt_results.jsonl",
        "fault_injections.jsonl",
        "artifacts/artifact_index.jsonl",
    ):
        assert (tail_root / relative_path).read_bytes() == b""
    head = validate_v3_generation_manifest(tail_root, tail_manifest)
    chain = validate_v3_delta_chain(run_root, head, expected_root_count=2)
    assert [item.delta_role for item in chain] == [
        "root_outcome",
        "root_outcome",
        "condition_tail_events",
    ]
    condition_manifest = _read_json(run_root / "condition_manifest.json")
    assert condition_manifest["condition_event_delta_count"] == 1
    assert condition_manifest["logical_task_count"] == 2
    assert condition_manifest["terminal"] is False

    with pytest.raises(ValueError, match="immutable|conflict|drift"):
        store.checkpoint_condition_tail_events(
            experiment_id=EXPERIMENT_A,
            condition=_condition(EXPERIMENT_A, CONDITION_A),
            repeat_id=0,
            anchor_task_id="task-2",
            events=[
                {
                    "experiment_id": EXPERIMENT_A,
                    "condition_id": CONDITION_A,
                    "repeat_id": 0,
                    "task_id": "task-2",
                    "event_id": "merge-gate-1",
                    "event_type": "MERGE_GATE_OPENED",
                }
            ],
        )


def test_v3_terminal_snapshot_publication_is_standalone_and_loadable(
    tmp_path: Path,
) -> None:
    bodies = _suite_bodies_with_two_roots()
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **bodies,
        capturing=False,
    )
    _checkpoint_task(store, tmp_path, task_id="task-2", selection_ordinal=1)
    _checkpoint_task(store, tmp_path, task_id="task-1", selection_ordinal=0)
    store.checkpoint_condition_tail_events(
        experiment_id=EXPERIMENT_A,
        condition=_condition(EXPERIMENT_A, CONDITION_A),
        repeat_id=0,
        anchor_task_id="task-2",
        events=[],
    )

    commit = store.compact_condition_snapshot(
        experiment_id=EXPERIMENT_A,
        condition=_condition(EXPERIMENT_A, CONDITION_A),
        repeat_id=0,
        temp_parent=tmp_path.parent,
    )

    run_root = tmp_path / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    generation_root = _current_generation_root(run_root)
    descriptor = validate_v3_generation_manifest(generation_root)
    condition = _read_json(run_root / "condition_manifest.json")
    assert descriptor.generation_kind == "snapshot"
    assert commit["generation_id"] == descriptor.generation_id
    assert [
        item["task_id"]
        for item in _read_jsonl(generation_root / "per_task_results.jsonl")
    ] == ["task-1", "task-2"]
    assert condition["head_generation_kind"] == "snapshot"
    assert condition["chain_generation_count"] == 1
    assert condition["root_delta_count"] == 2
    assert condition["condition_event_delta_count"] == 1
    assert condition["terminal"] is True
    assert len(list((run_root / ".generations").iterdir())) == 1
    loaded = FormalEvidenceStore.load(
        output_root=tmp_path,
        **_expected_bodies(bodies),
    )
    assert loaded.completed_task_ids == ("task-1", "task-2")


@pytest.mark.parametrize(
    "crash_stage",
    (
        "compaction_intent_written",
        "current_snapshot_written",
        "condition_terminal_written",
        "inventory_terminal_written",
    ),
)
def test_v3_terminal_snapshot_pending_repairs_every_publication_seam(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_stage: str,
) -> None:
    bodies = _suite_bodies_with_two_roots()
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **bodies,
        capturing=False,
    )
    _checkpoint_task(store, tmp_path, task_id="task-1", selection_ordinal=0)
    _checkpoint_task(store, tmp_path, task_id="task-2", selection_ordinal=1)
    store.checkpoint_condition_tail_events(
        experiment_id=EXPERIMENT_A,
        condition=_condition(EXPERIMENT_A, CONDITION_A),
        repeat_id=0,
        anchor_task_id="task-2",
        events=[],
    )

    def crash_hook(*, stage: str, run_root: Path) -> None:
        del run_root
        if stage == crash_stage:
            raise RuntimeError(f"injected crash after {stage}")

    monkeypatch.setattr(store, "_checkpoint_publication_hook", crash_hook)
    with pytest.raises(RuntimeError, match="injected crash"):
        store.compact_condition_snapshot(
            experiment_id=EXPERIMENT_A,
            condition=_condition(EXPERIMENT_A, CONDITION_A),
            repeat_id=0,
            temp_parent=tmp_path.parent,
        )

    loaded = FormalEvidenceStore.load(
        output_root=tmp_path,
        **_expected_bodies(bodies),
    )

    run_root = tmp_path / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    descriptor = validate_v3_generation_manifest(
        _current_generation_root(run_root)
    )
    assert loaded.completed_task_ids == ("task-1", "task-2")
    assert descriptor.generation_kind == "snapshot"
    assert not (run_root / "PENDING.json").exists()
    assert len(list((run_root / ".generations").iterdir())) == 1


def test_v3_snapshot_repair_keeps_intent_and_parents_on_condition_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies = _suite_bodies_with_two_roots()
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **bodies,
        capturing=False,
    )
    _checkpoint_task(store, tmp_path, task_id="task-1", selection_ordinal=0)
    _checkpoint_task(store, tmp_path, task_id="task-2", selection_ordinal=1)
    store.checkpoint_condition_tail_events(
        experiment_id=EXPERIMENT_A,
        condition=_condition(EXPERIMENT_A, CONDITION_A),
        repeat_id=0,
        anchor_task_id="task-2",
        events=[],
    )

    def crash_hook(*, stage: str, run_root: Path) -> None:
        del run_root
        if stage == "condition_terminal_written":
            raise RuntimeError("injected condition terminal crash")

    monkeypatch.setattr(store, "_checkpoint_publication_hook", crash_hook)
    with pytest.raises(RuntimeError, match="injected condition"):
        store.compact_condition_snapshot(
            experiment_id=EXPERIMENT_A,
            condition=_condition(EXPERIMENT_A, CONDITION_A),
            repeat_id=0,
            temp_parent=tmp_path.parent,
        )
    run_root = tmp_path / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    pending_before = (run_root / "PENDING.json").read_bytes()
    generations_before = {
        path.name for path in (run_root / ".generations").iterdir()
    }
    condition_path = run_root / "condition_manifest.json"
    condition = _read_json(condition_path)
    condition["root_delta_count"] = 1
    _write_json(condition_path, condition)

    with pytest.raises(ValueError, match="root_delta_count mismatch"):
        FormalEvidenceStore.load(
            output_root=tmp_path,
            **_expected_bodies(bodies),
        )

    assert (run_root / "PENDING.json").read_bytes() == pending_before
    assert {
        path.name for path in (run_root / ".generations").iterdir()
    } == generations_before


def test_v3_repair_validates_multiple_pending_snapshots_before_unlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies = _suite_bodies()
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **bodies,
        capturing=False,
    )
    _checkpoint_task(store, tmp_path, task_id="task-1", selection_ordinal=0)
    run_b = tmp_path / "experiments" / EXPERIMENT_B / "runs" / CONDITION_B / "0"
    artifact_b = run_b / "artifacts" / "task-b.bin"
    artifact_b.parent.mkdir(parents=True, exist_ok=True)
    artifact_b.write_bytes(b"evidence:task-b")
    store.checkpoint_root(
        experiment_id=EXPERIMENT_B,
        condition=_condition(EXPERIMENT_B, CONDITION_B),
        repeat_id=0,
        selection_ordinal=0,
        task=_task(EXPERIMENT_B, CONDITION_B, task_id="task-b"),
        attempts=[_attempt(EXPERIMENT_B, CONDITION_B, task_id="task-b")],
        faults=[],
        events=[_event(EXPERIMENT_B, CONDITION_B, task_id="task-b")],
        artifact_refs=[
            {
                "experiment_id": EXPERIMENT_B,
                "condition_id": CONDITION_B,
                "repeat_id": 0,
                "task_id": "task-b",
                "path": artifact_b.relative_to(tmp_path).as_posix(),
                "content_hash": _sha256(artifact_b.read_bytes()),
            }
        ],
    )
    for experiment_id, condition_id, anchor in (
        (EXPERIMENT_A, CONDITION_A, "task-1"),
        (EXPERIMENT_B, CONDITION_B, "task-b"),
    ):
        store.checkpoint_condition_tail_events(
            experiment_id=experiment_id,
            condition=_condition(experiment_id, condition_id),
            repeat_id=0,
            anchor_task_id=anchor,
            events=[],
        )

    def crash_hook(*, stage: str, run_root: Path) -> None:
        del run_root
        if stage == "current_snapshot_written":
            raise RuntimeError("injected current snapshot crash")

    monkeypatch.setattr(store, "_checkpoint_publication_hook", crash_hook)
    for experiment_id, condition_id in (
        (EXPERIMENT_A, CONDITION_A),
        (EXPERIMENT_B, CONDITION_B),
    ):
        with pytest.raises(RuntimeError, match="injected current"):
            store.compact_condition_snapshot(
                experiment_id=experiment_id,
                condition=_condition(experiment_id, condition_id),
                repeat_id=0,
                temp_parent=tmp_path.parent,
            )

    loaded = FormalEvidenceStore.load(
        output_root=tmp_path,
        **_expected_bodies(bodies),
    )

    assert loaded.completed_task_ids_by_experiment == {
        EXPERIMENT_A: ("task-1",),
        EXPERIMENT_B: ("task-b",),
    }
    assert not list(tmp_path.glob("experiments/*/runs/*/*/PENDING.json"))


@pytest.mark.parametrize("crash_stage", ("intent_written", "current_written"))
def test_v3_condition_tail_pending_exact_shape_and_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_stage: str,
) -> None:
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **_suite_bodies_with_two_roots(),
        capturing=False,
    )
    _checkpoint_task(store, tmp_path, task_id="task-1", selection_ordinal=0)
    _checkpoint_task(store, tmp_path, task_id="task-2", selection_ordinal=1)

    def crash_hook(*, stage: str, run_root: Path) -> None:
        del run_root
        if stage == crash_stage:
            raise RuntimeError("injected tail crash")

    monkeypatch.setattr(store, "_checkpoint_publication_hook", crash_hook)
    with pytest.raises(RuntimeError, match="tail crash"):
        store.checkpoint_condition_tail_events(
            experiment_id=EXPERIMENT_A,
            condition=_condition(EXPERIMENT_A, CONDITION_A),
            repeat_id=0,
            anchor_task_id="task-2",
            events=[],
        )

    run_root = tmp_path / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    pending = _read_json(run_root / "PENDING.json")
    assert set(pending) == formal_evidence._PENDING_V2_KEYS
    assert pending["publication_kind"] == "condition_tail_events"
    assert pending["task_id"] is None
    assert pending["selection_ordinal"] is None
    FormalEvidenceStore(tmp_path).repair_interrupted_checkpoint_publication()
    current_root = _current_generation_root(run_root)
    descriptor = validate_v3_generation_manifest(current_root)
    assert descriptor.delta_role == "condition_tail_events"


@pytest.mark.parametrize(
    "crash_stage",
    (
        "intent_written",
        "current_written",
        "condition_manifest_written",
        "inventory_refreshed",
    ),
)
def test_v3_child_root_repair_preserves_exact_prior_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_stage: str,
) -> None:
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **_suite_bodies_with_two_roots(),
        capturing=False,
    )
    first = _checkpoint_task(
        store,
        tmp_path,
        task_id="task-1",
        selection_ordinal=0,
    )

    def crash_hook(*, stage: str, run_root: Path) -> None:
        del run_root
        if stage == crash_stage:
            raise RuntimeError("injected child crash")

    monkeypatch.setattr(store, "_checkpoint_publication_hook", crash_hook)
    with pytest.raises(RuntimeError, match="child crash"):
        _checkpoint_task(
            store,
            tmp_path,
            task_id="task-2",
            selection_ordinal=1,
        )
    FormalEvidenceStore(tmp_path).repair_interrupted_checkpoint_publication()
    run_root = tmp_path / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    current_root = _current_generation_root(run_root)
    head = validate_v3_generation_manifest(current_root)
    chain = validate_v3_delta_chain(run_root, head, expected_root_count=2)
    assert [item.anchor_task_id for item in chain] == ["task-1", "task-2"]
    assert (run_root / ".generations" / first["generation_id"]).is_dir()


def test_v3_pending_rejects_third_current_without_mutating_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **_suite_bodies_with_two_roots(),
        capturing=False,
    )
    _checkpoint_task(store, tmp_path, task_id="task-1", selection_ordinal=0)

    def crash_hook(*, stage: str, run_root: Path) -> None:
        del run_root
        if stage == "intent_written":
            raise RuntimeError("injected child intent crash")

    monkeypatch.setattr(store, "_checkpoint_publication_hook", crash_hook)
    with pytest.raises(RuntimeError, match="intent crash"):
        _checkpoint_task(store, tmp_path, task_id="task-2", selection_ordinal=1)
    run_root = tmp_path / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    _write_json(
        run_root / "CURRENT.json",
        {
            "schema_version": "tokenshare.paper_checkpoint_current.v1",
            "generation_id": "third-current",
            "generation_manifest_digest": "sha256:" + "8" * 64,
        },
    )
    before = {
        path.relative_to(run_root).as_posix(): path.read_bytes()
        for path in run_root.rglob("*")
        if path.is_file()
    }

    with pytest.raises(ValueError, match="prior CURRENT conflict"):
        FormalEvidenceStore(tmp_path).repair_interrupted_checkpoint_publication()

    assert before == {
        path.relative_to(run_root).as_posix(): path.read_bytes()
        for path in run_root.rglob("*")
        if path.is_file()
    }


def test_v3_exact_duplicate_survives_new_store_instance(tmp_path: Path) -> None:
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **_suite_bodies(),
        capturing=False,
    )
    first = _checkpoint_task(
        store,
        tmp_path,
        task_id="task-1",
        selection_ordinal=0,
    )

    duplicate = _checkpoint_task(
        FormalEvidenceStore(tmp_path),
        tmp_path,
        task_id="task-1",
        selection_ordinal=0,
    )

    assert duplicate == first


def test_v3_nonhead_duplicate_survives_new_store_without_new_generation(
    tmp_path: Path,
) -> None:
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **_suite_bodies_with_two_roots(),
        capturing=False,
    )
    first = _checkpoint_task(
        store,
        tmp_path,
        task_id="task-1",
        selection_ordinal=0,
    )
    _checkpoint_task(
        store,
        tmp_path,
        task_id="task-2",
        selection_ordinal=1,
    )
    run_root = tmp_path / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    generations_before = {
        path.name for path in (run_root / ".generations").iterdir()
    }
    current_before = (run_root / "CURRENT.json").read_bytes()

    resumed = FormalEvidenceStore(tmp_path)
    duplicate = _checkpoint_task(
        resumed,
        tmp_path,
        task_id="task-1",
        selection_ordinal=0,
    )

    assert duplicate == first
    assert {
        path.name for path in (run_root / ".generations").iterdir()
    } == generations_before
    assert (run_root / "CURRENT.json").read_bytes() == current_before

    artifact = run_root / "artifacts" / "task-1.bin"
    with pytest.raises(ValueError, match="immutable"):
        resumed.checkpoint_root(
            experiment_id=EXPERIMENT_A,
            condition=_condition(EXPERIMENT_A, CONDITION_A),
            repeat_id=0,
            selection_ordinal=0,
            task={
                **_task(
                    EXPERIMENT_A,
                    CONDITION_A,
                    status="failed",
                    task_id="task-1",
                ),
                "paper_eligible": False,
            },
            attempts=[_attempt(EXPERIMENT_A, CONDITION_A, task_id="task-1")],
            faults=[],
            events=[_event(EXPERIMENT_A, CONDITION_A, task_id="task-1")],
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
    assert {
        path.name for path in (run_root / ".generations").iterdir()
    } == generations_before


def test_v3_checkpoint_hot_path_does_not_scan_suite_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **_suite_bodies_with_two_roots(),
        capturing=False,
    )
    original = Path.rglob

    def reject_suite_scan(path: Path, pattern: str):
        if path.resolve(strict=False) == tmp_path.resolve(strict=False):
            raise AssertionError("checkpoint hot path must not scan the suite root")
        return original(path, pattern)

    monkeypatch.setattr(Path, "rglob", reject_suite_scan)

    _checkpoint_task(store, tmp_path, task_id="task-1", selection_ordinal=0)
    _checkpoint_task(store, tmp_path, task_id="task-2", selection_ordinal=1)


def test_load_accepts_committed_v1_generation_without_condition_manifest(
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
    generation_root = _current_generation_root(run_root)
    generation_manifest_path = generation_root / "generation_manifest.json"
    generation_manifest = _read_json(generation_manifest_path)
    generation_manifest = {
        "schema_version": "tokenshare.paper_checkpoint_generation.v1",
        "generation_id": generation_manifest["generation_id"],
        "files": generation_manifest["files"],
    }
    _write_json(generation_manifest_path, generation_manifest)
    _rewrite_current_digest(run_root, generation_manifest)
    (run_root / "condition_manifest.json").unlink()
    _rebuild_evidence_manifest(tmp_path)

    loaded = FormalEvidenceStore.load(
        output_root=tmp_path,
        **_expected_bodies(bodies),
    )

    assert loaded.completed_task_ids == ("task-1",)


def test_load_rejects_condition_manifest_denominator_tamper(
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
    condition_path = run_root / "condition_manifest.json"
    condition_manifest = _read_json(condition_path)
    condition_manifest["expected_root_count"] = 2
    _write_json(condition_path, condition_manifest)
    FormalEvidenceStore(tmp_path)._refresh_evidence_manifest(run_root=run_root)

    with pytest.raises(ValueError, match="condition manifest evidence mismatch"):
        FormalEvidenceStore.load(
            output_root=tmp_path,
            **_expected_bodies(bodies),
        )


def test_failed_terminal_root_keeps_condition_evidence_paper_eligible(
    tmp_path: Path,
) -> None:
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **_suite_bodies(),
        capturing=False,
    )
    run_root = tmp_path / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    artifact = run_root / "artifacts" / "failed.bin"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"failed-but-complete")
    store.checkpoint_root(
        experiment_id=EXPERIMENT_A,
        condition=_condition(EXPERIMENT_A, CONDITION_A),
        repeat_id=0,
        task=_task(EXPERIMENT_A, CONDITION_A, status="failed"),
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

    condition_manifest = _read_json(run_root / "condition_manifest.json")
    assert condition_manifest["status"] == "running"
    assert condition_manifest["terminal"] is False
    assert condition_manifest["paper_eligible"] is False
    assert condition_manifest["ineligibility_reasons"] == ["condition_incomplete"]


def test_missing_task_paper_eligibility_flag_fails_condition_closed(
    tmp_path: Path,
) -> None:
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **_suite_bodies(),
        capturing=False,
    )
    task = _task(EXPERIMENT_A, CONDITION_A)
    task.pop("paper_eligible")
    run_root = tmp_path / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    artifact = run_root / "artifacts" / "missing-flag.bin"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"missing-flag")
    store.checkpoint_root(
        experiment_id=EXPERIMENT_A,
        condition=_condition(EXPERIMENT_A, CONDITION_A),
        repeat_id=0,
        task=task,
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

    condition_manifest = _read_json(run_root / "condition_manifest.json")
    assert condition_manifest["paper_eligible"] is False
    assert condition_manifest["ineligibility_reasons"] == [
        "condition_incomplete",
        "task_ineligible",
    ]


def test_load_rejects_missing_reachable_nested_artifact_index_row(
    tmp_path: Path,
) -> None:
    bodies = _suite_bodies()
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **bodies,
        capturing=False,
    )
    run_root = tmp_path / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    artifact_root = run_root / "artifacts" / "task-1"
    artifact_root.mkdir(parents=True)

    def source_ref(
        *,
        artifact_id: str,
        uri: str,
        body: bytes,
    ) -> dict[str, object]:
        return {
            "schema_version": "ArtifactRef.v1",
            "artifact_id": artifact_id,
            "artifact_type": "ClosureEvidence",
            "uri": uri,
            "content_hash": _sha256(body),
            "size_bytes": len(body),
            "media_type": "application/json",
            "artifact_schema_id": "test.closure_evidence",
            "artifact_schema_version": "v1",
            "source": {},
            "metadata": {},
            "created_at": "2026-07-20T00:00:00Z",
        }

    child_body = b'{"child":true}'
    child_source_ref = source_ref(
        artifact_id="child",
        uri="adapter/artifacts/child.json",
        body=child_body,
    )
    root_body = _canonical_json({"child_ref": child_source_ref}).encode("utf-8")
    root_source_ref = source_ref(
        artifact_id="root",
        uri="adapter/artifacts/root.json",
        body=root_body,
    )
    root_path = artifact_root / "root.json"
    child_path = artifact_root / "child.json"
    root_path.write_bytes(root_body)
    child_path.write_bytes(child_body)

    def index_ref(
        *,
        source: dict[str, object],
        path: Path,
    ) -> dict[str, object]:
        return {
            "artifact_id": source["artifact_id"],
            "experiment_id": EXPERIMENT_A,
            "condition_id": CONDITION_A,
            "repeat_id": 0,
            "task_id": "task-1",
            "path": path.relative_to(tmp_path).as_posix(),
            "content_hash": source["content_hash"],
            "size_bytes": source["size_bytes"],
            "source_uri": source["uri"],
            "source_artifact_ref": source,
        }

    store.checkpoint_root(
        experiment_id=EXPERIMENT_A,
        condition=_condition(EXPERIMENT_A, CONDITION_A),
        repeat_id=0,
        task={
            **_task(EXPERIMENT_A, CONDITION_A, status="completed"),
            "artifact_refs": [root_source_ref],
        },
        attempts=[_attempt(EXPERIMENT_A, CONDITION_A)],
        faults=[],
        events=[_event(EXPERIMENT_A, CONDITION_A)],
        artifact_refs=[
            index_ref(source=root_source_ref, path=root_path),
            index_ref(source=child_source_ref, path=child_path),
        ],
    )

    generation_root = _current_generation_root(run_root)
    artifact_index_path = generation_root / "artifacts" / "artifact_index.jsonl"
    _write_jsonl(
        artifact_index_path,
        [
            record
            for record in _read_jsonl(artifact_index_path)
            if record.get("artifact_id") != "child"
        ],
    )
    generation_manifest = _rebuild_generation_manifest(generation_root)
    _rewrite_current_digest(run_root, generation_manifest)
    _rebuild_evidence_manifest(tmp_path)

    with pytest.raises(ValueError, match="reachable artifact closure"):
        FormalEvidenceStore.load(
            output_root=tmp_path,
            **_expected_bodies(bodies),
        )


def test_checkpoint_preserves_protocol_ledger_body_and_verifies_its_hash_chain(
    tmp_path: Path,
) -> None:
    bodies = _suite_bodies()
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **bodies,
        capturing=False,
    )
    source_ledger = EventLedger(
        tmp_path.parent / f"{tmp_path.name}-source-event-log.jsonl"
    )
    source_event = source_ledger.append(
        event_type="TASK_REGISTERED",
        object_type="Task",
        object_id="paper_factorization_case-1",
        payload={"root_task_id": "paper_factorization_case-1"},
        idempotency_key="register-paper-factorization-case-1",
        task_id="paper_factorization_case-1",
        occurred_at="2026-07-31T00:00:00Z",
    ).to_dict()

    store.checkpoint_root(
        experiment_id=EXPERIMENT_A,
        condition=_condition(EXPERIMENT_A, CONDITION_A),
        repeat_id=0,
        task=_task(EXPERIMENT_A, CONDITION_A, status="failed"),
        attempts=[_attempt(EXPERIMENT_A, CONDITION_A)],
        faults=[],
        events=[source_event],
        artifact_refs=[],
    )

    run_root = tmp_path / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    generation_root = _current_generation_root(run_root)
    stored_events = _read_jsonl(generation_root / "events" / "event_log.jsonl")
    stored_tasks = _read_jsonl(generation_root / "per_task_results.jsonl")

    assert stored_events == [source_event]
    assert EventLedger(
        generation_root / "events" / "event_log.jsonl"
    ).verify_hash_chain()
    assert stored_tasks[0]["protocol_event_ledger"]["case_task_id"] == "task-1"
    assert stored_tasks[0]["protocol_event_ledger"]["event_hashes"] == [
        source_event["event_hash"]
    ]
    FormalEvidenceStore.load(
        output_root=tmp_path,
        **_expected_bodies(bodies),
    )


def test_checkpoint_rejects_protocol_event_whose_body_does_not_match_hash(
    tmp_path: Path,
) -> None:
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **_suite_bodies(),
        capturing=False,
    )
    source_ledger = EventLedger(
        tmp_path.parent / f"{tmp_path.name}-corrupt-source-event-log.jsonl"
    )
    corrupt_event = source_ledger.append(
        event_type="TASK_REGISTERED",
        object_type="Task",
        object_id="paper_factorization_case-1",
        payload={"root_task_id": "paper_factorization_case-1"},
        idempotency_key="register-paper-factorization-case-1",
        task_id="paper_factorization_case-1",
        occurred_at="2026-07-31T00:00:00Z",
    ).to_dict()
    corrupt_event["task_id"] = "case-1"

    with pytest.raises(ValueError, match="protocol event hash chain"):
        store.checkpoint_root(
            experiment_id=EXPERIMENT_A,
            condition=_condition(EXPERIMENT_A, CONDITION_A),
            repeat_id=0,
            task=_task(EXPERIMENT_A, CONDITION_A, status="failed"),
            attempts=[_attempt(EXPERIMENT_A, CONDITION_A)],
            faults=[],
            events=[corrupt_event],
            artifact_refs=[],
        )

    assert not (
        tmp_path
        / "experiments"
        / EXPERIMENT_A
        / "runs"
        / CONDITION_A
        / "0"
        / "CURRENT.json"
    ).exists()


def test_two_root_protocol_ledgers_keep_independent_seq_one_chains(
    tmp_path: Path,
) -> None:
    bodies = _suite_bodies_with_two_roots()
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **bodies,
        capturing=False,
    )
    source_events: dict[str, dict[str, object]] = {}
    for case_task_id in ("task-1", "task-2"):
        protocol_task_id = f"paper_factorization_{case_task_id}"
        source_ledger = EventLedger(
            tmp_path.parent / f"{tmp_path.name}-{case_task_id}-event-log.jsonl"
        )
        source_event = source_ledger.append(
            event_type="TASK_REGISTERED",
            object_type="Task",
            object_id=protocol_task_id,
            payload={"root_task_id": protocol_task_id},
            idempotency_key=f"register-{protocol_task_id}",
            task_id=protocol_task_id,
            occurred_at=(
                "2026-07-31T00:00:00Z"
                if case_task_id == "task-1"
                else "2026-07-31T00:00:01Z"
            ),
        ).to_dict()
        source_events[case_task_id] = source_event
        store.checkpoint_root(
            experiment_id=EXPERIMENT_A,
            condition=_condition(EXPERIMENT_A, CONDITION_A),
            repeat_id=0,
            task=_task(
                EXPERIMENT_A,
                CONDITION_A,
                status="failed",
                task_id=case_task_id,
            ),
            attempts=[
                _attempt(
                    EXPERIMENT_A,
                    CONDITION_A,
                    task_id=case_task_id,
                )
            ],
            faults=[],
            events=[source_event],
            artifact_refs=[],
        )

    run_root = tmp_path / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    generation_root = _current_generation_root(run_root)
    head = validate_v3_generation_manifest(generation_root)
    chain = list(
        iter_v3_delta_chain(run_root, head, expected_root_count=2)
    )
    tasks = [
        task
        for descriptor in chain
        for task in _read_jsonl(
            descriptor.generation_root / "per_task_results.jsonl"
        )
    ]
    events = [
        event
        for descriptor in chain
        for event in _read_jsonl(
            descriptor.generation_root / "events" / "event_log.jsonl"
        )
    ]

    assert [event["event_seq"] for event in events] == [1, 1]
    assert [event["event_id"] for event in events] == [
        "event_000000000001",
        "event_000000000001",
    ]
    assert len({event["event_hash"] for event in events}) == 2
    assert formal_metrics._records_for_task(events, tasks[0], tasks) == [
        source_events["task-1"]
    ]
    assert formal_metrics._records_for_task(events, tasks[1], tasks) == [
        source_events["task-2"]
    ]
    loaded = FormalEvidenceStore.load(
        output_root=tmp_path,
        **_expected_bodies(bodies),
    )
    assert loaded.completed_task_keys == (
        (EXPERIMENT_A, CONDITION_A, "0", "task-1"),
        (EXPERIMENT_A, CONDITION_A, "0", "task-2"),
    )


def test_checkpoint_rejects_conflicting_experiment_event_in_same_context(
    tmp_path: Path,
) -> None:
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **_suite_bodies(),
        capturing=False,
    )
    common = {
        "experiment_id": EXPERIMENT_A,
        "condition_id": CONDITION_A,
        "repeat_id": 0,
        "task_id": "task-1",
        "event_id": "experiment-event-1",
        "event_type": "EXPERIMENT_FAULT_OBSERVED",
    }
    store.checkpoint_root(
        experiment_id=EXPERIMENT_A,
        condition=_condition(EXPERIMENT_A, CONDITION_A),
        repeat_id=0,
        task=_task(EXPERIMENT_A, CONDITION_A, status="failed"),
        attempts=[_attempt(EXPERIMENT_A, CONDITION_A)],
        faults=[],
        events=[{**common, "detail": "original"}],
        artifact_refs=[],
    )

    with pytest.raises(ValueError, match="immutable"):
        store.checkpoint_root(
            experiment_id=EXPERIMENT_A,
            condition=_condition(EXPERIMENT_A, CONDITION_A),
            repeat_id=0,
            task=_task(EXPERIMENT_A, CONDITION_A, status="failed"),
            attempts=[
                {
                    **_attempt(EXPERIMENT_A, CONDITION_A),
                    "attempt_id": "attempt-2",
                }
            ],
            faults=[],
            events=[{**common, "detail": "mutated"}],
            artifact_refs=[],
        )


def test_shared_root_reference_freezes_current_generation_and_zero_usage(
    tmp_path: Path,
) -> None:
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **_suite_bodies(),
        capturing=False,
    )
    _checkpoint_task(store, tmp_path, task_id="task-1")

    reference = store.build_shared_root_reference(
        source_experiment_id=EXPERIMENT_A,
        case_id="task-1",
        source_repeat_id=0,
        expected_condition_identity={"repeat_id": 0},
    )
    repeated = store.build_shared_root_reference(
        source_experiment_id=EXPERIMENT_A,
        case_id="task-1",
        source_repeat_id=0,
        expected_condition_identity={"repeat_id": 0},
    )

    assert reference == repeated
    assert reference["schema_version"] == (
        "tokenshare.paper_exp1_shared_reference.v1"
    )
    assert reference["source_suite_id"] == "formal-suite-1"
    assert reference["source_experiment_id"] == EXPERIMENT_A
    assert reference["source_condition_id"] == CONDITION_A
    assert reference["source_case_id"] == "task-1"
    assert reference["source_task_id"] == "task-1"
    assert reference["source_repeat_id"] == 0
    assert reference["source_generation_id"]
    assert reference["source_generation_manifest_digest"].startswith("sha256:")
    assert reference["source_task_record_hash"].startswith("sha256:")
    assert reference["source_hash"].startswith("sha256:")
    assert reference["source_versions"] == _execution_version_identity()
    assert reference["source_attempt_refs"]
    assert reference["source_event_refs"]
    assert reference["source_artifact_refs"]
    assert reference["source_root_status"] == "completed"
    assert reference["evidence_integrity"] == "complete"
    assert reference["baseline_comparison_eligible"] is True
    assert reference["provider_calls_made"] == 0
    assert reference["prompt_tokens"] == 0
    assert reference["completion_tokens"] == 0
    assert reference["total_tokens"] == 0
    assert reference["cost_estimate"] == 0.0


def test_shared_root_reference_rejects_version_drift_and_does_not_zero_missing_usage(
    tmp_path: Path,
) -> None:
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **_suite_bodies(),
        capturing=False,
    )
    task = {
        **_task(EXPERIMENT_A, CONDITION_A),
        "provider_attempt_count": 1,
    }
    attempt = _attempt(EXPERIMENT_A, CONDITION_A)
    for field_name in (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cost_estimate",
        "cost_estimate_currency",
        "cost_estimate_status",
    ):
        attempt.pop(field_name, None)
    artifact = (
        tmp_path
        / "experiments"
        / EXPERIMENT_A
        / "runs"
        / CONDITION_A
        / "0"
        / "artifacts"
        / "missing-usage-source.bin"
    )
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"missing usage source")
    store.checkpoint_root(
        experiment_id=EXPERIMENT_A,
        condition=_condition(EXPERIMENT_A, CONDITION_A),
        repeat_id=0,
        task=task,
        attempts=[attempt],
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

    with pytest.raises(ValueError, match="version identity mismatch"):
        store.build_shared_root_reference(
            source_experiment_id=EXPERIMENT_A,
            case_id="task-1",
            source_repeat_id=0,
            expected_condition_identity={"repeat_id": 0},
            expected_source_versions={
                **_execution_version_identity(),
                "split_profile_digest": "sha256:" + "9" * 64,
            },
        )

    reference = store.build_shared_root_reference(
        source_experiment_id=EXPERIMENT_A,
        case_id="task-1",
        source_repeat_id=0,
        expected_condition_identity={"repeat_id": 0},
        expected_source_versions=_execution_version_identity(),
    )
    assert reference["source_usage"]["usage_complete"] is False
    assert reference["source_usage"]["usage_missing_provider_attempt_count"] == 1
    assert reference["source_usage"]["prompt_tokens"] is None
    assert reference["source_usage"]["cost_estimate"] is None
    assert reference["baseline_comparison_eligible"] is False
    assert reference["baseline_unavailable_reason"] == "source_exp1_usage_incomplete"


def test_shared_root_reference_accepts_complete_experimental_failure(
    tmp_path: Path,
) -> None:
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **_suite_bodies(),
        capturing=False,
    )
    run_root = tmp_path / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    artifact = run_root / "artifacts" / "task-1.bin"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"complete failed evidence")
    store.checkpoint_root(
        experiment_id=EXPERIMENT_A,
        condition=_condition(EXPERIMENT_A, CONDITION_A),
        repeat_id=0,
        task={
            **_task(EXPERIMENT_A, CONDITION_A, status="failed"),
            "case_id": "task-1",
            "outcome_status": "failed_experimental",
            "evidence_integrity": "complete",
        },
        attempts=[
            {
                **_attempt(EXPERIMENT_A, CONDITION_A),
                "attempt_status": "failed",
                "total_tokens": 17,
                "cost_estimate": 0.25,
            }
        ],
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

    reference = store.build_shared_root_reference(
        source_experiment_id=EXPERIMENT_A,
        case_id="task-1",
        source_repeat_id=0,
        expected_condition_identity={"repeat_id": 0},
    )

    assert reference["source_root_status"] == "failed"
    assert reference["evidence_integrity"] == "complete"
    assert reference["baseline_comparison_eligible"] is False
    assert reference["baseline_unavailable_reason"] == (
        "source_exp1_failed_experimental"
    )
    assert reference["source_usage"] == {
        "provider_attempt_count": 1,
        "expected_provider_attempt_count": 1,
        "prompt_tokens": 4,
        "completion_tokens": 6,
        "total_tokens": 17,
        "cost_estimate": 0.25,
        "usage_complete": True,
        "usage_missing_provider_attempt_count": 0,
        "cost_estimate_status": "estimated",
        "cost_estimate_currency": "USD",
    }


def test_shared_root_reference_fails_closed_on_identity_or_generation_corruption(
    tmp_path: Path,
) -> None:
    store = FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **_suite_bodies(),
        capturing=False,
    )
    _checkpoint_task(store, tmp_path, task_id="task-1")

    with pytest.raises(ValueError, match="identity"):
        store.build_shared_root_reference(
            source_experiment_id=EXPERIMENT_A,
            case_id="task-1",
            source_repeat_id=0,
            expected_condition_identity={"repeat_id": 1},
        )

    run_root = tmp_path / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    generation_root = _current_generation_root(run_root)
    task_path = generation_root / "per_task_results.jsonl"
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace("completed", "failed"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="integrity"):
        store.build_shared_root_reference(
            source_experiment_id=EXPERIMENT_A,
            case_id="task-1",
            source_repeat_id=0,
            expected_condition_identity={"repeat_id": 0},
        )


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
    bodies = _suite_bodies_with_root_ids(
        tuple(f"task-{index}" for index in range(1, 5))
    )
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
    bodies = _suite_bodies_with_root_ids(
        ("process-task-1", "process-task-2")
    )
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
                {
                    "experiment_id": EXPERIMENT_A,
                    "conditions": conditions[:1],
                    "selections": [{"ordered_case_ids": ["task-1"]}],
                },
                {
                    "experiment_id": EXPERIMENT_B,
                    "conditions": conditions[1:],
                    "selections": [{"ordered_case_ids": ["task-b"]}],
                },
            ],
        },
        "budget": {"budget_digest": "sha256:" + "1" * 64},
        "catalog": {"catalog_digest": "sha256:" + "2" * 64},
        "identity": {"endpoint_digest": "sha256:" + "3" * 64},
        "request_limits": {"max_tokens": 1024},
        "hard_limits": {"max_provider_attempts": 2},
    }


def _suite_bodies_with_two_roots() -> dict[str, object]:
    return _suite_bodies_with_root_ids(("task-1", "task-2"))


def _suite_bodies_with_root_ids(root_ids: tuple[str, ...]) -> dict[str, object]:
    bodies = _suite_bodies()
    dispatch = bodies["dispatch"]
    assert isinstance(dispatch, dict)
    plans = dispatch["plans"]
    assert isinstance(plans, list)
    plans[0]["selections"] = [{"ordered_case_ids": list(root_ids)}]
    plans[1]["selections"] = [{"ordered_case_ids": ["task-b"]}]
    return bodies


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
        "paper_eligible": True,
        "provider_attempt_count": 1,
        "execution_version_identity": _execution_version_identity(),
        "runtime_generation_identity": _runtime_generation_identity(),
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
        "provider_attempt_count": 1,
        "prompt_tokens": 4,
        "completion_tokens": 6,
        "total_tokens": 10,
        "cost_estimate": 0.1,
        "cost_estimate_currency": "USD",
        "cost_estimate_status": "estimated",
        "model_execution_record_ref": {"content_hash": "sha256:" + "7" * 64},
    }


def _execution_version_identity() -> dict[str, object]:
    return {
        "schema_version": "tokenshare.paper_execution_version_identity.v1",
        "plugin_version": "plugin-test-v1",
        "parser_version": "parser-test-v1",
        "verifier_version": "verifier-test-v1",
        "executor_version": "executor-test-v1",
        "prompt_version": "prompt-test-v1",
        "split_profile_digest": "sha256:" + "6" * 64,
        "runtime_generation_schema_version": (
            "tokenshare.paper_runtime_generation_identity.v1"
        ),
        "runtime_generation_identity_digest": _sha256(
            _canonical_json(_runtime_generation_identity()).encode("utf-8")
        ),
    }


def _runtime_generation_identity() -> dict[str, object]:
    return {
        "schema_version": "tokenshare.paper_runtime_generation_identity.v1",
        "run_id": "test-run",
        "task_id": "task-1",
        "root_unit_id": "root-task-1",
        "ledger_digest": "sha256:" + "4" * 64,
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
    selection_ordinal: int | None = None,
) -> dict[str, object]:
    run_root = root / "experiments" / EXPERIMENT_A / "runs" / CONDITION_A / "0"
    artifact = run_root / "artifacts" / f"{task_id}.bin"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(f"evidence:{task_id}".encode("utf-8"))
    return store.checkpoint_root(
        experiment_id=EXPERIMENT_A,
        condition=_condition(EXPERIMENT_A, CONDITION_A),
        repeat_id=0,
        selection_ordinal=selection_ordinal,
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
    prior_manifest_path = generation_root / "generation_manifest.json"
    prior_manifest = (
        _read_json(prior_manifest_path)
        if prior_manifest_path.is_file()
        else {}
    )
    files = [
        _evidence_entry(generation_root, generation_root / relative_path)
        for relative_path in _run_files()
        if relative_path != "generation_manifest.json"
    ]
    generation_manifest = {
        **prior_manifest,
        "schema_version": prior_manifest.get(
            "schema_version",
            "tokenshare.paper_checkpoint_generation.v2",
        ),
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
