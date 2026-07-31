"""Formal paper generation v3 的不可变 checkpoint schema 与父链校验。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from typing import Any, Callable, Iterator, Literal, Mapping, Sequence


V3_GENERATION_SCHEMA = "tokenshare.paper_checkpoint_generation.v3"
V3_RUN_FILES = (
    "run_manifest.json",
    "per_task_results.jsonl",
    "per_attempt_results.jsonl",
    "fault_injections.jsonl",
    "events/event_log.jsonl",
    "artifacts/artifact_index.jsonl",
)

_SUCCESS_STATUSES = {"accepted", "completed", "success", "succeeded"}
_TERMINAL_STATUSES = _SUCCESS_STATUSES | {
    "blocked",
    "budget_exhausted",
    "failed",
    "ineligible",
    "not_started",
    "partial",
    "timeout",
    "worker_died",
}

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_GENERATION_KEYS = {
    "schema_version",
    "generation_id",
    "generation_kind",
    "delta_role",
    "selection_ordinal",
    "anchor_task_id",
    "parent_generation_id",
    "parent_generation_manifest_digest",
    "compacted_from_head_generation_id",
    "compacted_from_head_generation_manifest_digest",
    "compacted_chain_digest",
    "compacted_generation_count",
    "files",
}
_FILE_EVIDENCE_KEYS = {
    "path",
    "size",
    "content_sha256",
    "record_count",
    "records_digest",
}
_RUN_MANIFEST_KEYS = {
    "schema_version",
    "generation_id",
    "experiment_id",
    "condition_id",
    "repeat_id",
    "task_ids",
    "completed_task_ids",
    "status",
}


@dataclass(frozen=True, slots=True)
class V3GenerationDescriptor:
    generation_id: str
    generation_kind: Literal["delta", "snapshot"]
    delta_role: Literal["root_outcome", "condition_tail_events"] | None
    selection_ordinal: int | None
    anchor_task_id: str | None
    manifest_digest: str
    parent_generation_id: str | None
    parent_generation_manifest_digest: str | None
    compacted_from_head_generation_id: str | None
    compacted_from_head_generation_manifest_digest: str | None
    compacted_chain_digest: str | None
    compacted_generation_count: int
    generation_root: Path
    experiment_id: str
    condition_id: str
    repeat_id: int | str


def validate_v3_generation_manifest(
    generation_root: Path,
    manifest: Mapping[str, Any] | None = None,
    *,
    expected_experiment_id: str | None = None,
    expected_condition_id: str | None = None,
    expected_repeat_id: int | str | None = None,
) -> V3GenerationDescriptor:
    """严格验证一个 v3 generation 目录并返回不可变描述符。"""

    generation_root = Path(generation_root)
    body = (
        _read_json(generation_root / "generation_manifest.json")
        if manifest is None
        else dict(manifest)
    )
    _require_exact_keys(body, _GENERATION_KEYS, "v3 generation manifest")
    if body.get("schema_version") != V3_GENERATION_SCHEMA:
        raise ValueError("v3 generation schema version mismatch")

    generation_id = _require_id(body.get("generation_id"), "generation_id")
    if generation_root.name != generation_id:
        raise ValueError("v3 generation identity mismatch")
    generation_kind = body.get("generation_kind")
    if generation_kind not in {"delta", "snapshot"}:
        raise ValueError("v3 generation kind is invalid")

    parent_id = _optional_id(
        body.get("parent_generation_id"),
        "parent_generation_id",
    )
    parent_digest = _optional_digest(
        body.get("parent_generation_manifest_digest"),
        "parent_generation_manifest_digest",
    )
    if (parent_id is None) != (parent_digest is None):
        raise ValueError("v3 generation parent binding is invalid")

    compacted_head_id = _optional_id(
        body.get("compacted_from_head_generation_id"),
        "compacted_from_head_generation_id",
    )
    compacted_head_digest = _optional_digest(
        body.get("compacted_from_head_generation_manifest_digest"),
        "compacted_from_head_generation_manifest_digest",
    )
    compacted_chain_digest = _optional_digest(
        body.get("compacted_chain_digest"),
        "compacted_chain_digest",
    )
    compacted_count = _require_nonnegative_int(
        body.get("compacted_generation_count"),
        "compacted_generation_count",
    )

    delta_role = body.get("delta_role")
    selection_ordinal = body.get("selection_ordinal")
    anchor_task_id = body.get("anchor_task_id")
    if generation_kind == "delta":
        if delta_role not in {"root_outcome", "condition_tail_events"}:
            raise ValueError("v3 delta role is invalid")
        if any(
            value is not None
            for value in (
                compacted_head_id,
                compacted_head_digest,
                compacted_chain_digest,
            )
        ) or compacted_count != 0:
            raise ValueError("v3 delta compacted binding is invalid")
        anchor_task_id = _require_id(anchor_task_id, "anchor_task_id")
        if delta_role == "root_outcome":
            selection_ordinal = _require_nonnegative_int(
                selection_ordinal,
                "selection_ordinal",
            )
        elif selection_ordinal is not None:
            raise ValueError("v3 condition tail selection ordinal must be null")
    else:
        if delta_role is not None or selection_ordinal is not None or anchor_task_id is not None:
            raise ValueError("v3 snapshot delta fields must be null")
        if parent_id is not None or parent_digest is not None:
            raise ValueError("v3 snapshot parent binding must be null")
        if (
            compacted_head_id is None
            or compacted_head_digest is None
            or compacted_chain_digest is None
            or compacted_count <= 0
        ):
            raise ValueError("v3 snapshot compacted binding is invalid")

    files = body.get("files")
    if not isinstance(files, list):
        raise ValueError("v3 generation files type must be a list")
    file_records = _validate_file_evidence_inventory(
        generation_root,
        files,
        retain_jsonl_records=generation_kind == "delta",
    )
    experiment_id, condition_id, repeat_id = _validate_kind_file_shapes(
        generation_root=generation_root,
        generation_id=generation_id,
        generation_kind=generation_kind,
        delta_role=delta_role,
        selection_ordinal=selection_ordinal,
        anchor_task_id=anchor_task_id,
        file_records=file_records,
    )
    if expected_experiment_id is not None and experiment_id != expected_experiment_id:
        raise ValueError("v3 generation experiment identity mismatch")
    if expected_condition_id is not None and condition_id != expected_condition_id:
        raise ValueError("v3 generation condition identity mismatch")
    if expected_repeat_id is not None and repeat_id != expected_repeat_id:
        raise ValueError("v3 generation repeat identity mismatch")

    return V3GenerationDescriptor(
        generation_id=generation_id,
        generation_kind=generation_kind,
        delta_role=delta_role,
        selection_ordinal=selection_ordinal,
        anchor_task_id=anchor_task_id,
        manifest_digest=_digest_json(body),
        parent_generation_id=parent_id,
        parent_generation_manifest_digest=parent_digest,
        compacted_from_head_generation_id=compacted_head_id,
        compacted_from_head_generation_manifest_digest=compacted_head_digest,
        compacted_chain_digest=compacted_chain_digest,
        compacted_generation_count=compacted_count,
        generation_root=generation_root,
        experiment_id=experiment_id,
        condition_id=condition_id,
        repeat_id=repeat_id,
    )


def iter_v3_delta_chain(
    run_root: Path,
    head: V3GenerationDescriptor,
    *,
    expected_root_count: int,
) -> Iterator[V3GenerationDescriptor]:
    """从 CURRENT head 沿父指针校验，并按 oldest→newest 返回 delta。"""

    expected_root_count = _require_nonnegative_int(
        expected_root_count,
        "expected_root_count",
    )
    if head.generation_kind != "delta":
        raise ValueError("v3 delta chain head must be a delta")
    run_root = Path(run_root)
    visited: set[str] = set()
    reversed_chain: list[V3GenerationDescriptor] = []
    current = head
    while True:
        if current.generation_id in visited:
            raise ValueError("checkpoint generation chain contains a cycle")
        visited.add(current.generation_id)
        reversed_chain.append(current)
        if len(reversed_chain) > expected_root_count + 1:
            raise ValueError("checkpoint generation chain exceeds frozen root count")
        parent_id = current.parent_generation_id
        if parent_id is None:
            break
        if parent_id in visited:
            raise ValueError("checkpoint generation chain contains a cycle")
        parent_root = run_root / ".generations" / parent_id
        manifest_path = parent_root / "generation_manifest.json"
        if not manifest_path.is_file():
            raise ValueError("checkpoint generation parent is missing")
        parent_body = _read_json(manifest_path)
        if _digest_json(parent_body) != current.parent_generation_manifest_digest:
            raise ValueError("checkpoint generation parent manifest digest mismatch")
        parent = validate_v3_generation_manifest(
            parent_root,
            parent_body,
            expected_experiment_id=head.experiment_id,
            expected_condition_id=head.condition_id,
            expected_repeat_id=head.repeat_id,
        )
        if parent.generation_kind != "delta":
            raise ValueError("checkpoint delta chain parent must be a delta")
        current = parent
    yield from reversed(reversed_chain)


def validate_v3_delta_chain(
    run_root: Path,
    head: V3GenerationDescriptor,
    *,
    expected_root_count: int,
) -> tuple[V3GenerationDescriptor, ...]:
    """验证 chain 的 root/ordinal 唯一性与唯一 condition closure。"""

    chain = tuple(
        iter_v3_delta_chain(
            run_root,
            head,
            expected_root_count=expected_root_count,
        )
    )
    roots_by_ordinal: dict[int, str] = {}
    task_ids: set[str] = set()
    closure: V3GenerationDescriptor | None = None
    stable_record_identities: dict[str, set[Any]] = {
        "attempt": set(),
        "fault": set(),
        "event": set(),
        "artifact": set(),
    }
    for index, generation in enumerate(chain):
        if generation.delta_role == "root_outcome":
            assert generation.selection_ordinal is not None
            assert generation.anchor_task_id is not None
            if generation.selection_ordinal in roots_by_ordinal:
                raise ValueError("checkpoint delta chain contains duplicate selection ordinal")
            if generation.anchor_task_id in task_ids:
                raise ValueError("checkpoint delta chain contains duplicate task")
            if generation.selection_ordinal >= expected_root_count:
                raise ValueError("checkpoint delta selection ordinal exceeds frozen roots")
            roots_by_ordinal[generation.selection_ordinal] = generation.anchor_task_id
            task_ids.add(generation.anchor_task_id)
            for label, relative_path in (
                ("attempt", "per_attempt_results.jsonl"),
                ("fault", "fault_injections.jsonl"),
                ("event", "events/event_log.jsonl"),
                ("artifact", "artifacts/artifact_index.jsonl"),
            ):
                for record in _iter_jsonl_records(
                    generation.generation_root / relative_path
                ):
                    identity = _stable_record_identity(label, record)
                    if identity in stable_record_identities[label]:
                        raise ValueError(
                            f"checkpoint delta chain contains duplicate {label} identity"
                        )
                    stable_record_identities[label].add(identity)
            continue
        if closure is not None:
            raise ValueError("checkpoint delta chain contains a second condition closure")
        if index != len(chain) - 1:
            raise ValueError("checkpoint condition closure must be the chain head")
        closure = generation

    if closure is not None:
        expected_ordinals = set(range(expected_root_count))
        if set(roots_by_ordinal) != expected_ordinals:
            raise ValueError("checkpoint condition closure requires all frozen roots")
        last_task_id = roots_by_ordinal[expected_root_count - 1]
        if closure.anchor_task_id != last_task_id:
            raise ValueError("checkpoint condition closure anchor is invalid")
    return chain


def compact_v3_delta_chain_to_snapshot(
    run_root: Path,
    head: V3GenerationDescriptor,
    *,
    target_root: Path,
    generation_id: str,
    expected_root_count: int,
    temp_parent: Path | None = None,
    stage_hook: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """用临时SQLite流式折叠完整delta chain并写自包含snapshot。"""

    run_root = Path(run_root)
    target_root = Path(target_root)
    generation_id = _require_id(generation_id, "generation_id")
    if target_root.name != generation_id:
        raise ValueError("v3 snapshot target identity mismatch")
    chain = validate_v3_delta_chain(
        run_root,
        head,
        expected_root_count=expected_root_count,
    )
    roots = [item for item in chain if item.delta_role == "root_outcome"]
    closures = [
        item for item in chain if item.delta_role == "condition_tail_events"
    ]
    if len(roots) != expected_root_count or len(closures) != 1:
        raise ValueError("terminal snapshot requires all roots and one closure")
    if set(item.selection_ordinal for item in roots) != set(
        range(expected_root_count)
    ):
        raise ValueError("terminal snapshot selection inventory is incomplete")
    if target_root.exists():
        raise ValueError("terminal snapshot target already exists")

    temp_dir_parent = Path(temp_parent) if temp_parent is not None else run_root.parent
    temp_dir_parent.mkdir(parents=True, exist_ok=True)
    hook = stage_hook or (lambda stage: None)
    with tempfile.TemporaryDirectory(
        prefix=".tokenshare-v3-compact-",
        dir=str(temp_dir_parent),
    ) as work:
        connection = sqlite3.connect(Path(work) / "records.sqlite3")
        try:
            _initialize_compaction_schema(connection)
            hook("schema_initialized")
            for descriptor in chain:
                _ingest_delta_generation(
                    connection,
                    descriptor,
                    expected_root_count=expected_root_count,
                )
            connection.commit()
            hook("chain_ingested")
            snapshot_records = _write_snapshot_files_from_sqlite(
                connection,
                target_root=target_root,
                generation_id=generation_id,
                experiment_id=head.experiment_id,
                condition_id=head.condition_id,
                repeat_id=head.repeat_id,
                expected_root_count=expected_root_count,
            )
            hook("snapshot_files_written")
        finally:
            connection.close()

    chain_inventory = [
        {
            "generation_id": descriptor.generation_id,
            "generation_manifest_digest": descriptor.manifest_digest,
        }
        for descriptor in chain
    ]
    manifest = {
        "schema_version": V3_GENERATION_SCHEMA,
        "generation_id": generation_id,
        "generation_kind": "snapshot",
        "delta_role": None,
        "selection_ordinal": None,
        "anchor_task_id": None,
        "parent_generation_id": None,
        "parent_generation_manifest_digest": None,
        "compacted_from_head_generation_id": head.generation_id,
        "compacted_from_head_generation_manifest_digest": head.manifest_digest,
        "compacted_chain_digest": _digest_json(chain_inventory),
        "compacted_generation_count": len(chain),
        "files": [
            _file_evidence(target_root, target_root / relative_path, retain_records=False)[0]
            for relative_path in V3_RUN_FILES
        ],
    }
    _write_json(target_root / "generation_manifest.json", manifest)
    validate_v3_generation_manifest(
        target_root,
        manifest,
        expected_experiment_id=head.experiment_id,
        expected_condition_id=head.condition_id,
        expected_repeat_id=head.repeat_id,
    )
    return {
        "manifest": manifest,
        "chain": chain,
        "logical_records_digest": _digest_json(
            [
                {
                    "path": entry["path"],
                    "record_count": entry["record_count"],
                    "records_digest": entry["records_digest"],
                }
                for entry in manifest["files"]
            ]
        ),
        **snapshot_records,
    }


def _initialize_compaction_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE records (
            kind TEXT NOT NULL,
            selection_ordinal INTEGER NOT NULL,
            tail_rank INTEGER NOT NULL,
            within_sequence INTEGER NOT NULL,
            stable_identity TEXT NOT NULL,
            body TEXT NOT NULL,
            UNIQUE(kind, stable_identity)
        );
        CREATE INDEX records_order
        ON records(kind, selection_ordinal, tail_rank, within_sequence, stable_identity);
        """
    )


def _ingest_delta_generation(
    connection: sqlite3.Connection,
    descriptor: V3GenerationDescriptor,
    *,
    expected_root_count: int,
) -> None:
    is_tail = descriptor.delta_role == "condition_tail_events"
    ordinal = (
        expected_root_count - 1
        if is_tail
        else int(descriptor.selection_ordinal)
    )
    paths = (
        ("task", "per_task_results.jsonl"),
        ("attempt", "per_attempt_results.jsonl"),
        ("fault", "fault_injections.jsonl"),
        ("event", "events/event_log.jsonl"),
        ("artifact", "artifacts/artifact_index.jsonl"),
    )
    for kind, relative_path in paths:
        for sequence, record in enumerate(
            _iter_jsonl_records(descriptor.generation_root / relative_path)
        ):
            if kind == "task":
                identity = _require_id(record.get("task_id"), "task_id")
            else:
                identity = _stable_record_identity(kind, record)
            try:
                connection.execute(
                    """
                    INSERT INTO records(
                        kind, selection_ordinal, tail_rank,
                        within_sequence, stable_identity, body
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        kind,
                        ordinal,
                        int(is_tail),
                        sequence,
                        json.dumps(identity, ensure_ascii=False, sort_keys=True),
                        _canonical_bytes(record).decode("utf-8"),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError(
                    f"terminal snapshot contains duplicate {kind} identity"
                ) from error


def _write_snapshot_files_from_sqlite(
    connection: sqlite3.Connection,
    *,
    target_root: Path,
    generation_id: str,
    experiment_id: str,
    condition_id: str,
    repeat_id: int | str,
    expected_root_count: int,
) -> dict[str, Any]:
    target_root.mkdir(parents=True, exist_ok=False)
    output_paths = {
        "task": "per_task_results.jsonl",
        "attempt": "per_attempt_results.jsonl",
        "fault": "fault_injections.jsonl",
        "event": "events/event_log.jsonl",
        "artifact": "artifacts/artifact_index.jsonl",
    }
    tasks: list[dict[str, Any]] = []
    for kind, relative_path in output_paths.items():
        path = target_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        order = (
            "selection_ordinal, tail_rank, within_sequence"
            if kind == "event"
            else "selection_ordinal, within_sequence, stable_identity"
        )
        with temporary.open("wb") as stream:
            cursor = connection.execute(
                f"SELECT body FROM records WHERE kind = ? ORDER BY {order}",
                (kind,),
            )
            for (body_text,) in cursor:
                body = json.loads(body_text)
                if kind == "task":
                    tasks.append(body)
                stream.write(body_text.encode("utf-8") + b"\n")
        os.replace(temporary, path)
    if len(tasks) != expected_root_count:
        raise ValueError("terminal snapshot task denominator mismatch")
    task_ids = [str(task.get("task_id")) for task in tasks]
    if len(set(task_ids)) != len(task_ids):
        raise ValueError("terminal snapshot contains duplicate task")
    statuses = [
        str(task.get("root_status", task.get("status", ""))).lower()
        for task in tasks
    ]
    if any(status not in _TERMINAL_STATUSES for status in statuses):
        raise ValueError("terminal snapshot contains a non-terminal task")
    completed_task_ids = [
        task_id
        for task_id, status in zip(task_ids, statuses, strict=True)
        if status in _SUCCESS_STATUSES
    ]
    if len(completed_task_ids) == len(task_ids):
        status = "completed"
    elif "budget_exhausted" in statuses:
        status = "budget_exhausted"
    elif set(statuses) == {"blocked"}:
        status = "blocked"
    else:
        status = "completed_with_failures"
    _write_json(
        target_root / "run_manifest.json",
        {
            "schema_version": "tokenshare.paper_run_evidence.v1",
            "generation_id": generation_id,
            "experiment_id": experiment_id,
            "condition_id": condition_id,
            "repeat_id": repeat_id,
            "task_ids": task_ids,
            "completed_task_ids": completed_task_ids,
            "status": status,
        },
    )
    return {
        "task_ids": task_ids,
        "completed_task_ids": completed_task_ids,
        "terminal_root_count": len(tasks),
        "status": status,
    }


def _write_json(path: Path, body: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(_canonical_bytes(body) + b"\n")
    os.replace(temporary, path)


def _validate_file_evidence_inventory(
    generation_root: Path,
    entries: Sequence[Any],
    *,
    retain_jsonl_records: bool,
) -> dict[str, list[dict[str, Any]]]:
    seen: set[str] = set()
    records_by_path: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError("v3 generation evidence entry is invalid")
        _require_exact_keys(entry, _FILE_EVIDENCE_KEYS, "v3 file evidence")
        relative_path = entry.get("path")
        if not isinstance(relative_path, str) or relative_path not in V3_RUN_FILES:
            raise ValueError("v3 generation evidence path is invalid")
        if relative_path in seen:
            raise ValueError("v3 generation evidence path is duplicated")
        seen.add(relative_path)
        path = generation_root / relative_path
        if not path.is_file():
            raise ValueError("v3 generation evidence is missing")
        expected, records = _file_evidence(
            generation_root,
            path,
            retain_records=retain_jsonl_records or path.suffix == ".json",
        )
        if dict(entry) != expected:
            raise ValueError("v3 generation evidence integrity mismatch")
        records_by_path[relative_path] = records
    if seen != set(V3_RUN_FILES):
        raise ValueError("v3 generation required evidence mismatch")
    actual_files = {
        path.relative_to(generation_root).as_posix()
        for path in generation_root.rglob("*")
        if path.is_file() and not path.name.endswith(".tmp")
    }
    if actual_files != set(V3_RUN_FILES) | {"generation_manifest.json"}:
        raise ValueError("v3 generation contains incomplete evidence")
    return records_by_path


def _validate_kind_file_shapes(
    *,
    generation_root: Path,
    generation_id: str,
    generation_kind: str,
    delta_role: str | None,
    selection_ordinal: int | None,
    anchor_task_id: str | None,
    file_records: Mapping[str, list[dict[str, Any]]],
) -> tuple[str, str, int | str]:
    run_records = file_records["run_manifest.json"]
    if len(run_records) != 1:
        raise ValueError("v3 generation run manifest record count is invalid")
    run_manifest = run_records[0]
    _require_exact_keys(run_manifest, _RUN_MANIFEST_KEYS, "v3 run manifest")
    if run_manifest.get("schema_version") != "tokenshare.paper_run_evidence.v1":
        raise ValueError("v3 run manifest schema version mismatch")
    if run_manifest.get("generation_id") != generation_id:
        raise ValueError("v3 generation run manifest identity mismatch")
    experiment_id = _require_id(run_manifest.get("experiment_id"), "experiment_id")
    condition_id = _require_id(run_manifest.get("condition_id"), "condition_id")
    repeat_id = run_manifest.get("repeat_id")
    if isinstance(repeat_id, bool) or not isinstance(repeat_id, (int, str)):
        raise ValueError("v3 generation repeat identity is invalid")
    if isinstance(repeat_id, str):
        _require_id(repeat_id, "repeat_id")
    for field_name in ("task_ids", "completed_task_ids"):
        value = run_manifest.get(field_name)
        if not isinstance(value, list) or any(
            not isinstance(item, str) for item in value
        ):
            raise ValueError(f"v3 run manifest {field_name} type is invalid")
    if not isinstance(run_manifest.get("status"), str):
        raise ValueError("v3 run manifest status type is invalid")
    if generation_kind == "snapshot":
        tasks = list(
            _iter_jsonl_records(generation_root / "per_task_results.jsonl")
        )
        task_ids = [
            _require_id(task.get("task_id"), "task_id") for task in tasks
        ]
        if len(set(task_ids)) != len(task_ids):
            raise ValueError("v3 snapshot contains duplicate task")
        statuses = [
            str(task.get("root_status", task.get("status", ""))).lower()
            for task in tasks
        ]
        if any(status not in _TERMINAL_STATUSES for status in statuses):
            raise ValueError("v3 snapshot contains a non-terminal task")
        expected_completed = [
            task_id
            for task_id, status in zip(task_ids, statuses, strict=True)
            if status in _SUCCESS_STATUSES
        ]
        expected_status = (
            "completed"
            if len(expected_completed) == len(task_ids)
            else (
                "budget_exhausted"
                if "budget_exhausted" in statuses
                else "blocked"
                if set(statuses) == {"blocked"}
                else "completed_with_failures"
            )
        )
        if run_manifest.get("task_ids") != task_ids:
            raise ValueError("v3 snapshot run manifest task inventory is invalid")
        if run_manifest.get("completed_task_ids") != expected_completed:
            raise ValueError(
                "v3 snapshot run manifest completed task inventory is invalid"
            )
        if run_manifest.get("status") != expected_status:
            raise ValueError("v3 snapshot run manifest status is invalid")
        task_id_set = set(task_ids)
        for label, relative_path in (
            ("attempt", "per_attempt_results.jsonl"),
            ("fault", "fault_injections.jsonl"),
            ("artifact", "artifacts/artifact_index.jsonl"),
        ):
            if any(
                record.get("task_id") not in task_id_set
                for record in _iter_jsonl_records(
                    generation_root / relative_path
                )
            ):
                raise ValueError(f"v3 snapshot {label} evidence crosses task identity")
        for event in _iter_jsonl_records(
            generation_root / "events" / "event_log.jsonl"
        ):
            if event.get("schema_version") in {"LedgerEvent.v1", "LedgerEvent.v2"}:
                continue
            event_task_id = event.get("task_id")
            event_type = event.get("event_type")
            if event_task_id not in task_id_set and not (
                isinstance(event_type, str) and event_type.startswith("MERGE_GATE_")
            ):
                raise ValueError("v3 snapshot event evidence crosses task identity")
        return experiment_id, condition_id, repeat_id

    tasks = file_records["per_task_results.jsonl"]
    attempts = file_records["per_attempt_results.jsonl"]
    faults = file_records["fault_injections.jsonl"]
    events = file_records["events/event_log.jsonl"]
    artifacts = file_records["artifacts/artifact_index.jsonl"]
    if delta_role == "condition_tail_events":
        if any((tasks, attempts, faults, artifacts)):
            raise ValueError("v3 condition tail event delta contains non-event records")
        if run_manifest.get("task_ids") != [] or run_manifest.get("completed_task_ids") != []:
            raise ValueError("v3 condition tail event delta declares tasks")
        if run_manifest.get("status") != "running":
            raise ValueError("v3 condition tail run manifest status is invalid")
        for event in events:
            event_type = event.get("event_type")
            if not isinstance(event_type, str) or not event_type.startswith("MERGE_GATE_"):
                raise ValueError("v3 condition tail event delta contains an invalid event")
        return experiment_id, condition_id, repeat_id

    assert anchor_task_id is not None
    assert selection_ordinal is not None
    if len(tasks) != 1 or tasks[0].get("task_id") != anchor_task_id:
        raise ValueError("v3 root delta task does not match its anchor")
    if not attempts or not events:
        raise ValueError("v3 root delta requires attempt and event evidence")
    if run_manifest.get("task_ids") != [anchor_task_id]:
        raise ValueError("v3 root delta run manifest task inventory is invalid")
    task_status = str(
        tasks[0].get("root_status", tasks[0].get("status", "failed"))
    ).lower()
    success = task_status in {"accepted", "completed", "success", "succeeded"}
    expected_completed = [anchor_task_id] if success else []
    if run_manifest.get("completed_task_ids") != expected_completed:
        raise ValueError(
            "v3 root delta run manifest completed task inventory is invalid"
        )
    expected_status = (
        "completed"
        if success
        else (
            "budget_exhausted"
            if task_status == "budget_exhausted"
            else "blocked" if task_status == "blocked" else "completed_with_failures"
        )
    )
    if run_manifest.get("status") != expected_status:
        raise ValueError("v3 root delta run manifest status is invalid")
    for label, records in (
        ("attempt", attempts),
        ("fault", faults),
        ("artifact", artifacts),
    ):
        if any(record.get("task_id") != anchor_task_id for record in records):
            raise ValueError(f"v3 root delta {label} evidence crosses task identity")
    for event in events:
        if event.get("schema_version") in {"LedgerEvent.v1", "LedgerEvent.v2"}:
            continue
        if event.get("task_id") != anchor_task_id:
            raise ValueError("v3 root delta event evidence crosses task identity")
    return experiment_id, condition_id, repeat_id


def _file_evidence(
    root: Path,
    path: Path,
    *,
    retain_records: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if path.suffix == ".json":
        content = path.read_bytes()
        decoded = json.loads(content.decode("utf-8"))
        records: list[dict[str, Any]] = [decoded]
        if not isinstance(decoded, dict):
            raise ValueError("v3 generation JSON record must be an object")
        return (
            {
                "path": path.relative_to(root).as_posix(),
                "size": len(content),
                "content_sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
                "record_count": 1,
                "records_digest": _digest_json(records),
            },
            records,
        )

    content_hash = hashlib.sha256()
    records_hash = hashlib.sha256()
    records_hash.update(b"[")
    size = 0
    record_count = 0
    records = []
    try:
        with path.open("rb") as stream:
            for raw_line in stream:
                size += len(raw_line)
                content_hash.update(raw_line)
                if not raw_line.strip():
                    continue
                record = json.loads(raw_line.decode("utf-8"))
                if not isinstance(record, dict):
                    raise ValueError("v3 generation JSONL record must be an object")
                if record_count:
                    records_hash.update(b",")
                records_hash.update(_canonical_bytes(record))
                record_count += 1
                if retain_records:
                    records.append(record)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid checkpoint JSONL: {path}") from error
    records_hash.update(b"]")
    return (
        {
            "path": path.relative_to(root).as_posix(),
            "size": size,
            "content_sha256": "sha256:" + content_hash.hexdigest(),
            "record_count": record_count,
            "records_digest": "sha256:" + records_hash.hexdigest(),
        },
        records,
    )


def _iter_jsonl_records(path: Path) -> Iterator[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("v3 generation JSONL record must be an object")
                yield record
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid checkpoint JSONL: {path}") from error


def _stable_record_identity(label: str, record: Mapping[str, Any]) -> Any:
    if label == "attempt":
        field_name = "attempt_id"
    elif label == "fault":
        task_id = record.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("checkpoint fault task identity is missing")
        fault_identity = record.get("fault_injection_id")
        if not isinstance(fault_identity, str) or not fault_identity:
            dead_attempt = record.get("dead_attempt")
            fault_identity = (
                dead_attempt.get("attempt_id")
                if isinstance(dead_attempt, Mapping)
                else record.get("attempt_id")
            )
        if not isinstance(fault_identity, str) or not fault_identity:
            raise ValueError("checkpoint fault stable identity is missing")
        return (task_id, fault_identity)
    elif label == "artifact":
        task_id = record.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("checkpoint artifact task identity is missing")
        artifact_identity = (
            record.get("artifact_id")
            if isinstance(record.get("artifact_id"), str)
            else record.get("path")
        )
        if not isinstance(artifact_identity, str) or not artifact_identity:
            raise ValueError("checkpoint artifact stable identity is missing")
        return (task_id, artifact_identity)
    elif record.get("schema_version") in {"LedgerEvent.v1", "LedgerEvent.v2"}:
        field_name = "event_hash"
    else:
        event_id = record.get("event_id")
        task_id = record.get("task_id")
        event_type = record.get("event_type")
        if (
            isinstance(event_type, str)
            and event_type.startswith("MERGE_GATE_")
            and isinstance(event_id, str)
            and event_id
        ):
            return ("condition", event_id)
        if not isinstance(event_id, str) or not event_id or not isinstance(task_id, str):
            raise ValueError("checkpoint event stable identity is missing")
        return (task_id, event_id)
    value = record.get(field_name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"checkpoint {label} stable identity is missing")
    return value


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid checkpoint JSON: {path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"checkpoint JSON must be an object: {path}")
    return value


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _require_exact_keys(
    body: Mapping[str, Any],
    expected: set[str],
    label: str,
) -> None:
    if set(body) != expected:
        raise ValueError(f"{label} keys mismatch")


def _require_id(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} must be a path-safe identifier")
    return value


def _optional_id(value: Any, field_name: str) -> str | None:
    return None if value is None else _require_id(value, field_name)


def _optional_digest(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _DIGEST_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} must be a sha256 digest")
    return value


def _require_nonnegative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value
