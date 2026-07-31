from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from tokenshare.experiments.paper_formal_checkpoint import (
    V3_GENERATION_SCHEMA,
    compact_v3_delta_chain_to_snapshot,
    iter_v3_delta_chain,
    validate_v3_delta_chain,
    validate_v3_generation_manifest,
)


RUN_FILES = (
    "run_manifest.json",
    "per_task_results.jsonl",
    "per_attempt_results.jsonl",
    "fault_injections.jsonl",
    "events/event_log.jsonl",
    "artifacts/artifact_index.jsonl",
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _write_json(path: Path, body: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical_bytes(body) + b"\n")


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(_canonical_bytes(item) + b"\n" for item in records))


def _file_evidence(root: Path, relative_path: str) -> dict[str, Any]:
    path = root / relative_path
    content = path.read_bytes()
    if path.suffix == ".json":
        records = [json.loads(content.decode("utf-8"))]
    else:
        records = [
            json.loads(line)
            for line in content.decode("utf-8").splitlines()
            if line.strip()
        ]
    return {
        "path": relative_path,
        "size": len(content),
        "content_sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
        "record_count": len(records),
        "records_digest": _digest(records),
    }


def _records_for(
    *,
    generation_id: str,
    role: str | None,
    task_id: str | None,
) -> dict[str, list[dict[str, Any]] | dict[str, Any]]:
    if role == "root_outcome":
        assert task_id is not None
        return {
            "run_manifest.json": {
                "schema_version": "tokenshare.paper_run_evidence.v1",
                "generation_id": generation_id,
                "experiment_id": "exp1",
                "condition_id": "condition-a",
                "repeat_id": 0,
                "task_ids": [task_id],
                "completed_task_ids": [task_id],
                "status": "completed",
            },
            "per_task_results.jsonl": [
                {"task_id": task_id, "status": "completed"}
            ],
            "per_attempt_results.jsonl": [
                {"task_id": task_id, "attempt_id": f"{task_id}-attempt"}
            ],
            "fault_injections.jsonl": [],
            "events/event_log.jsonl": [
                {"task_id": task_id, "event_id": f"{task_id}-event"}
            ],
            "artifacts/artifact_index.jsonl": [
                {
                    "task_id": task_id,
                    "artifact_id": f"{task_id}-artifact",
                    "path": f"artifacts/{task_id}.bin",
                }
            ],
        }
    if role == "condition_tail_events":
        return {
            "run_manifest.json": {
                "schema_version": "tokenshare.paper_run_evidence.v1",
                "generation_id": generation_id,
                "experiment_id": "exp1",
                "condition_id": "condition-a",
                "repeat_id": 0,
                "task_ids": [],
                "completed_task_ids": [],
                "status": "running",
            },
            "per_task_results.jsonl": [],
            "per_attempt_results.jsonl": [],
            "fault_injections.jsonl": [],
            "events/event_log.jsonl": [],
            "artifacts/artifact_index.jsonl": [],
        }
    return {
        "run_manifest.json": {
            "schema_version": "tokenshare.paper_run_evidence.v1",
            "generation_id": generation_id,
            "experiment_id": "exp1",
            "condition_id": "condition-a",
            "repeat_id": 0,
            "task_ids": ["case-0"],
            "completed_task_ids": ["case-0"],
            "status": "completed",
        },
        "per_task_results.jsonl": [
            {"task_id": "case-0", "status": "completed"}
        ],
        "per_attempt_results.jsonl": [
            {"task_id": "case-0", "attempt_id": "case-0-attempt"}
        ],
        "fault_injections.jsonl": [],
        "events/event_log.jsonl": [
            {"task_id": "case-0", "event_id": "case-0-event"}
        ],
        "artifacts/artifact_index.jsonl": [
            {
                "task_id": "case-0",
                "artifact_id": "case-0-artifact",
                "path": "artifacts/case-0.bin",
            }
        ],
    }


def _write_generation(
    run_root: Path,
    *,
    generation_id: str,
    generation_kind: str,
    delta_role: str | None,
    selection_ordinal: int | None,
    anchor_task_id: str | None,
    parent: dict[str, Any] | None = None,
) -> tuple[Path, dict[str, Any]]:
    root = run_root / ".generations" / generation_id
    records = _records_for(
        generation_id=generation_id,
        role=delta_role,
        task_id=anchor_task_id,
    )
    for relative_path in RUN_FILES:
        body = records[relative_path]
        if relative_path.endswith(".json"):
            assert isinstance(body, dict)
            _write_json(root / relative_path, body)
        else:
            assert isinstance(body, list)
            _write_jsonl(root / relative_path, body)
    is_snapshot = generation_kind == "snapshot"
    manifest = {
        "schema_version": V3_GENERATION_SCHEMA,
        "generation_id": generation_id,
        "generation_kind": generation_kind,
        "delta_role": delta_role,
        "selection_ordinal": selection_ordinal,
        "anchor_task_id": anchor_task_id,
        "parent_generation_id": None if parent is None else parent["generation_id"],
        "parent_generation_manifest_digest": (
            None if parent is None else _digest(parent)
        ),
        "compacted_from_head_generation_id": (
            "delta-head" if is_snapshot else None
        ),
        "compacted_from_head_generation_manifest_digest": (
            "sha256:" + "1" * 64 if is_snapshot else None
        ),
        "compacted_chain_digest": "sha256:" + "2" * 64 if is_snapshot else None,
        "compacted_generation_count": 2 if is_snapshot else 0,
        "files": [_file_evidence(root, path) for path in RUN_FILES],
    }
    _write_json(root / "generation_manifest.json", manifest)
    return root, manifest


def _rebuild_manifest(root: Path, manifest: dict[str, Any]) -> None:
    manifest["files"] = [_file_evidence(root, path) for path in RUN_FILES]
    _write_json(root / "generation_manifest.json", manifest)


@pytest.mark.parametrize(
    "kind,role,ordinal,anchor",
    [
        ("delta", "root_outcome", 0, "case-0"),
        ("delta", "condition_tail_events", None, "case-0"),
        ("snapshot", None, None, None),
    ],
)
def test_v3_manifest_shape_accepts_valid_kinds(
    tmp_path: Path,
    kind: str,
    role: str | None,
    ordinal: int | None,
    anchor: str | None,
) -> None:
    root, manifest = _write_generation(
        tmp_path,
        generation_id=f"generation-{kind}-{role or 'none'}",
        generation_kind=kind,
        delta_role=role,
        selection_ordinal=ordinal,
        anchor_task_id=anchor,
    )

    descriptor = validate_v3_generation_manifest(root, manifest)

    assert descriptor.generation_kind == kind
    assert descriptor.delta_role == role


@pytest.mark.parametrize(
    "mutation",
    [
        lambda body: body.pop("generation_kind"),
        lambda body: body.__setitem__("extra", True),
        lambda body: body.__setitem__("parent_generation_id", "missing-parent"),
        lambda body: body.__setitem__("delta_role", "root_outcome"),
        lambda body: body.__setitem__("selection_ordinal", 0),
        lambda body: body.__setitem__("anchor_task_id", "case-0"),
        lambda body: body.__setitem__("compacted_generation_count", 0),
    ],
)
def test_v3_snapshot_manifest_shape_fails_closed(
    tmp_path: Path,
    mutation: Any,
) -> None:
    root, manifest = _write_generation(
        tmp_path,
        generation_id="snapshot-a",
        generation_kind="snapshot",
        delta_role=None,
        selection_ordinal=None,
        anchor_task_id=None,
    )
    mutated = copy.deepcopy(manifest)
    mutation(mutated)

    with pytest.raises(ValueError):
        validate_v3_generation_manifest(root, mutated)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda body: body.__setitem__("delta_role", None),
        lambda body: body.__setitem__("selection_ordinal", None),
        lambda body: body.__setitem__("selection_ordinal", -1),
        lambda body: body.__setitem__("anchor_task_id", None),
        lambda body: body.__setitem__("compacted_chain_digest", "sha256:" + "3" * 64),
        lambda body: body.__setitem__("compacted_generation_count", 1),
    ],
)
def test_v3_root_delta_manifest_shape_fails_closed(
    tmp_path: Path,
    mutation: Any,
) -> None:
    root, manifest = _write_generation(
        tmp_path,
        generation_id="delta-a",
        generation_kind="delta",
        delta_role="root_outcome",
        selection_ordinal=0,
        anchor_task_id="case-0",
    )
    mutated = copy.deepcopy(manifest)
    mutation(mutated)

    with pytest.raises(ValueError):
        validate_v3_generation_manifest(root, mutated)


def test_v3_manifest_rejects_file_set_or_integrity_drift(tmp_path: Path) -> None:
    root, manifest = _write_generation(
        tmp_path,
        generation_id="delta-a",
        generation_kind="delta",
        delta_role="root_outcome",
        selection_ordinal=0,
        anchor_task_id="case-0",
    )
    missing = copy.deepcopy(manifest)
    missing["files"].pop()
    with pytest.raises(ValueError, match="required evidence"):
        validate_v3_generation_manifest(root, missing)

    tampered = copy.deepcopy(manifest)
    tampered["files"][0]["record_count"] += 1
    with pytest.raises(ValueError, match="integrity"):
        validate_v3_generation_manifest(root, tampered)


def test_v3_condition_tail_rejects_non_event_records(tmp_path: Path) -> None:
    root, manifest = _write_generation(
        tmp_path,
        generation_id="tail-a",
        generation_kind="delta",
        delta_role="condition_tail_events",
        selection_ordinal=None,
        anchor_task_id="case-0",
    )
    _write_jsonl(root / "per_task_results.jsonl", [{"task_id": "case-0"}])
    drifted = copy.deepcopy(manifest)
    drifted["files"] = [_file_evidence(root, path) for path in RUN_FILES]

    with pytest.raises(ValueError, match="tail event delta"):
        validate_v3_generation_manifest(root, drifted)


def test_v3_delta_chain_is_oldest_first_and_allows_out_of_order_ordinals(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run"
    _, first = _write_generation(
        run_root,
        generation_id="delta-2",
        generation_kind="delta",
        delta_role="root_outcome",
        selection_ordinal=2,
        anchor_task_id="case-2",
    )
    _, second = _write_generation(
        run_root,
        generation_id="delta-0",
        generation_kind="delta",
        delta_role="root_outcome",
        selection_ordinal=0,
        anchor_task_id="case-0",
        parent=first,
    )
    tail_root, tail = _write_generation(
        run_root,
        generation_id="tail",
        generation_kind="delta",
        delta_role="condition_tail_events",
        selection_ordinal=None,
        anchor_task_id="case-2",
        parent=second,
    )
    head = validate_v3_generation_manifest(tail_root, tail)

    chain = list(iter_v3_delta_chain(run_root, head, expected_root_count=3))

    assert [item.generation_id for item in chain] == ["delta-2", "delta-0", "tail"]


def test_v3_delta_chain_fails_on_missing_or_tampered_parent(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    parent_root, parent = _write_generation(
        run_root,
        generation_id="parent",
        generation_kind="delta",
        delta_role="root_outcome",
        selection_ordinal=0,
        anchor_task_id="case-0",
    )
    child_root, child = _write_generation(
        run_root,
        generation_id="child",
        generation_kind="delta",
        delta_role="root_outcome",
        selection_ordinal=1,
        anchor_task_id="case-1",
        parent=parent,
    )
    head = validate_v3_generation_manifest(child_root, child)
    (parent_root / "generation_manifest.json").unlink()
    with pytest.raises(ValueError, match="parent.*missing"):
        list(iter_v3_delta_chain(run_root, head, expected_root_count=2))

    _write_json(parent_root / "generation_manifest.json", parent)
    parent["anchor_task_id"] = "case-drift"
    _write_json(parent_root / "generation_manifest.json", parent)
    with pytest.raises(ValueError, match="parent.*digest"):
        list(iter_v3_delta_chain(run_root, head, expected_root_count=2))


def test_v3_delta_chain_detects_cycle_before_following_revisited_parent(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run"
    _, first = _write_generation(
        run_root,
        generation_id="first",
        generation_kind="delta",
        delta_role="root_outcome",
        selection_ordinal=0,
        anchor_task_id="case-0",
    )
    second_root, second = _write_generation(
        run_root,
        generation_id="second",
        generation_kind="delta",
        delta_role="root_outcome",
        selection_ordinal=1,
        anchor_task_id="case-1",
        parent=first,
    )
    first_root = run_root / ".generations" / "first"
    first["parent_generation_id"] = "second"
    first["parent_generation_manifest_digest"] = _digest(second)
    _write_json(first_root / "generation_manifest.json", first)
    head = validate_v3_generation_manifest(first_root, first)

    with pytest.raises(ValueError, match="cycle"):
        list(iter_v3_delta_chain(run_root, head, expected_root_count=2))


@pytest.mark.parametrize("drift", ["duplicate_task", "second_tail", "wrong_anchor"])
def test_v3_delta_chain_logical_rules_fail_closed(
    tmp_path: Path,
    drift: str,
) -> None:
    run_root = tmp_path / "run"
    _, first = _write_generation(
        run_root,
        generation_id="first",
        generation_kind="delta",
        delta_role="root_outcome",
        selection_ordinal=0,
        anchor_task_id="case-0",
    )
    second_task = "case-0" if drift == "duplicate_task" else "case-1"
    second_ordinal = 0 if drift == "duplicate_task" else 1
    _, second = _write_generation(
        run_root,
        generation_id="second",
        generation_kind="delta",
        delta_role="root_outcome",
        selection_ordinal=second_ordinal,
        anchor_task_id=second_task,
        parent=first,
    )
    _, tail = _write_generation(
        run_root,
        generation_id="tail",
        generation_kind="delta",
        delta_role="condition_tail_events",
        selection_ordinal=None,
        anchor_task_id="case-0" if drift == "wrong_anchor" else "case-1",
        parent=second,
    )
    head_manifest = tail
    if drift == "second_tail":
        tail2_root, head_manifest = _write_generation(
            run_root,
            generation_id="tail-2",
            generation_kind="delta",
            delta_role="condition_tail_events",
            selection_ordinal=None,
            anchor_task_id="case-1",
            parent=tail,
        )
    else:
        tail2_root = run_root / ".generations" / "tail"
    head = validate_v3_generation_manifest(tail2_root, head_manifest)

    with pytest.raises(ValueError):
        validate_v3_delta_chain(run_root, head, expected_root_count=2)


def test_v3_snapshot_validation_streams_jsonl_without_read_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, manifest = _write_generation(
        tmp_path,
        generation_id="snapshot-a",
        generation_kind="snapshot",
        delta_role=None,
        selection_ordinal=None,
        anchor_task_id=None,
    )
    original = Path.read_bytes

    def reject_jsonl_read_bytes(path: Path) -> bytes:
        if path.suffix == ".jsonl":
            raise AssertionError("snapshot JSONL must be validated as a stream")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", reject_jsonl_read_bytes)

    descriptor = validate_v3_generation_manifest(root, manifest)

    assert descriptor.generation_kind == "snapshot"


def test_v3_delta_chain_rejects_cross_condition_parent(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    parent_root, parent = _write_generation(
        run_root,
        generation_id="parent",
        generation_kind="delta",
        delta_role="root_outcome",
        selection_ordinal=0,
        anchor_task_id="case-0",
    )
    parent_run = json.loads((parent_root / "run_manifest.json").read_text("utf-8"))
    parent_run["condition_id"] = "condition-b"
    _write_json(parent_root / "run_manifest.json", parent_run)
    _rebuild_manifest(parent_root, parent)
    child_root, child = _write_generation(
        run_root,
        generation_id="child",
        generation_kind="delta",
        delta_role="root_outcome",
        selection_ordinal=1,
        anchor_task_id="case-1",
        parent=parent,
    )
    head = validate_v3_generation_manifest(child_root, child)

    with pytest.raises(ValueError, match="identity"):
        list(iter_v3_delta_chain(run_root, head, expected_root_count=2))


@pytest.mark.parametrize(
    ("relative_path", "identity_field"),
    [
        ("per_attempt_results.jsonl", "attempt_id"),
        ("events/event_log.jsonl", "event_id"),
    ],
)
def test_v3_delta_chain_rejects_duplicate_record_identity(
    tmp_path: Path,
    relative_path: str,
    identity_field: str,
) -> None:
    run_root = tmp_path / "run"
    first_root, first = _write_generation(
        run_root,
        generation_id="first",
        generation_kind="delta",
        delta_role="root_outcome",
        selection_ordinal=0,
        anchor_task_id="case-0",
    )
    if relative_path == "events/event_log.jsonl":
        first_event = json.loads(
            (first_root / relative_path).read_text("utf-8").splitlines()[0]
        )
        first_event["schema_version"] = "LedgerEvent.v1"
        first_event["event_hash"] = "sha256:" + "9" * 64
        _write_jsonl(first_root / relative_path, [first_event])
        _rebuild_manifest(first_root, first)
    second_root, second = _write_generation(
        run_root,
        generation_id="second",
        generation_kind="delta",
        delta_role="root_outcome",
        selection_ordinal=1,
        anchor_task_id="case-1",
        parent=first,
    )
    first_record = json.loads(
        (first_root / relative_path).read_text("utf-8").splitlines()[0]
    )
    second_record = json.loads(
        (second_root / relative_path).read_text("utf-8").splitlines()[0]
    )
    if relative_path == "events/event_log.jsonl":
        second_record["schema_version"] = "LedgerEvent.v1"
        second_record["event_hash"] = "sha256:" + "9" * 64
    else:
        second_record[identity_field] = first_record[identity_field]
    _write_jsonl(second_root / relative_path, [second_record])
    _rebuild_manifest(second_root, second)
    head = validate_v3_generation_manifest(second_root, second)

    with pytest.raises(ValueError, match="duplicate.*identity"):
        validate_v3_delta_chain(run_root, head, expected_root_count=2)


def test_sqlite_compaction_writes_selection_ordered_terminal_snapshot(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run"
    prior: dict[str, Any] | None = None
    for generation_id, ordinal, task_id in (
        ("root-two", 2, "case-2"),
        ("root-zero", 0, "case-0"),
        ("root-one", 1, "case-1"),
    ):
        _, prior = _write_generation(
            run_root,
            generation_id=generation_id,
            generation_kind="delta",
            delta_role="root_outcome",
            selection_ordinal=ordinal,
            anchor_task_id=task_id,
            parent=prior,
        )
    tail_root, tail = _write_generation(
        run_root,
        generation_id="tail",
        generation_kind="delta",
        delta_role="condition_tail_events",
        selection_ordinal=None,
        anchor_task_id="case-2",
        parent=prior,
    )
    _write_jsonl(
        tail_root / "events" / "event_log.jsonl",
        [{"event_id": "merge", "event_type": "MERGE_GATE_COMPLETED"}],
    )
    _rebuild_manifest(tail_root, tail)
    root_zero = run_root / ".generations" / "root-zero"
    _write_jsonl(
        root_zero / "per_attempt_results.jsonl",
        [
            {"task_id": "case-0", "attempt_id": "z-first"},
            {"task_id": "case-0", "attempt_id": "a-second"},
        ],
    )
    root_zero_manifest = json.loads(
        (root_zero / "generation_manifest.json").read_text(encoding="utf-8")
    )
    _rebuild_manifest(root_zero, root_zero_manifest)
    root_one = run_root / ".generations" / "root-one"
    root_one_manifest = json.loads(
        (root_one / "generation_manifest.json").read_text(encoding="utf-8")
    )
    root_one_manifest["parent_generation_manifest_digest"] = _digest(
        root_zero_manifest
    )
    _write_json(root_one / "generation_manifest.json", root_one_manifest)
    tail["parent_generation_manifest_digest"] = _digest(root_one_manifest)
    _write_json(tail_root / "generation_manifest.json", tail)
    head = validate_v3_generation_manifest(tail_root, tail)

    result = compact_v3_delta_chain_to_snapshot(
        run_root,
        head,
        target_root=run_root / ".generations" / "snapshot",
        generation_id="snapshot",
        expected_root_count=3,
        temp_parent=tmp_path,
    )

    snapshot_root = run_root / ".generations" / "snapshot"
    assert [
        item["task_id"]
        for item in _read_jsonl(snapshot_root / "per_task_results.jsonl")
    ] == ["case-0", "case-1", "case-2"]
    assert [
        item["event_id"]
        for item in _read_jsonl(snapshot_root / "events" / "event_log.jsonl")
    ] == ["case-0-event", "case-1-event", "case-2-event", "merge"]
    assert [
        item["attempt_id"]
        for item in _read_jsonl(snapshot_root / "per_attempt_results.jsonl")
        if item["task_id"] == "case-0"
    ] == ["z-first", "a-second"]
    assert result["status"] == "completed"
    assert result["terminal_root_count"] == 3
    descriptor = validate_v3_generation_manifest(
        snapshot_root,
        result["manifest"],
    )
    assert descriptor.generation_kind == "snapshot"
    assert descriptor.compacted_generation_count == 4
    assert not list(tmp_path.glob(".tokenshare-v3-compact-*"))


def test_v3_chain_scopes_artifact_identity_by_task(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run"
    first_root, first = _write_generation(
        run_root,
        generation_id="first",
        generation_kind="delta",
        delta_role="root_outcome",
        selection_ordinal=0,
        anchor_task_id="case-0",
    )
    second_root, second = _write_generation(
        run_root,
        generation_id="second",
        generation_kind="delta",
        delta_role="root_outcome",
        selection_ordinal=1,
        anchor_task_id="case-1",
        parent=first,
    )
    first_artifact = _read_jsonl(
        first_root / "artifacts" / "artifact_index.jsonl"
    )[0]
    second_artifact = _read_jsonl(
        second_root / "artifacts" / "artifact_index.jsonl"
    )[0]
    second_artifact["path"] = first_artifact["path"]
    _write_jsonl(
        second_root / "artifacts" / "artifact_index.jsonl",
        [second_artifact],
    )
    _rebuild_manifest(second_root, second)
    head = validate_v3_generation_manifest(second_root, second)
    validate_v3_delta_chain(run_root, head, expected_root_count=2)

    second_artifact["artifact_id"] = first_artifact["artifact_id"]
    _write_jsonl(
        second_root / "artifacts" / "artifact_index.jsonl",
        [second_artifact],
    )
    _rebuild_manifest(second_root, second)
    head = validate_v3_generation_manifest(second_root, second)
    validate_v3_delta_chain(run_root, head, expected_root_count=2)

    duplicate = dict(second_artifact)
    duplicate["path"] = "artifacts/conflicting.bin"
    _write_jsonl(
        second_root / "artifacts" / "artifact_index.jsonl",
        [second_artifact, duplicate],
    )
    _rebuild_manifest(second_root, second)
    head = validate_v3_generation_manifest(second_root, second)
    with pytest.raises(ValueError, match="duplicate artifact identity"):
        validate_v3_delta_chain(run_root, head, expected_root_count=2)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
