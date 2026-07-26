"""从权威 ledger/artifact 引用派生通用 runtime 视图。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from collections.abc import Mapping, Sequence

from tokenshare.core.models import ArtifactRef, JsonObject
from tokenshare.local_runtime.contracts import ProtocolRunResult
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType


@dataclass(frozen=True, kw_only=True)
class RuntimeUnitView:
    unit_id: str
    state: str
    attempt_ids: tuple[str, ...]
    canonical_artifact_ids: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class RuntimeAttemptView:
    attempt_id: str
    unit_id: str
    state: str


def build_runtime_observation(
    *,
    run_id: str,
    runtime_started_at: str,
    runtime_ended_at: str,
    planned_ai_unit_ids: Sequence[str],
    dispatched_ai_unit_ids: Sequence[str],
    completed_ai_unit_ids: Sequence[str],
    worker_execution_facts: Sequence[Mapping[str, Any]],
    in_flight_ai_unit_ids_at_witness: Sequence[str] = (),
    witness_observed_at: str | None = None,
) -> JsonObject:
    """规范化真实 execution clock、AI-unit inventory 与 worker intervals。"""

    started = _timestamp(runtime_started_at)
    ended = _timestamp(runtime_ended_at)
    if ended < started:
        raise ValueError("runtime observation ended before it started")
    planned = _unique_strings(planned_ai_unit_ids, "planned_ai_unit_ids")
    dispatched = _unique_strings(
        dispatched_ai_unit_ids,
        "dispatched_ai_unit_ids",
    )
    completed = _unique_strings(
        completed_ai_unit_ids,
        "completed_ai_unit_ids",
    )
    in_flight = _unique_strings(
        in_flight_ai_unit_ids_at_witness,
        "in_flight_ai_unit_ids_at_witness",
    )
    if not set(dispatched).issubset(planned):
        raise ValueError("dispatched AI units must belong to the planned inventory")
    if not set(completed).issubset(set(dispatched)):
        raise ValueError("completed AI units must belong to the dispatched inventory")
    if not set(in_flight).issubset(set(dispatched)):
        raise ValueError("in-flight AI units must belong to the dispatched inventory")
    facts = [dict(fact) for fact in worker_execution_facts]
    return {
        "schema_version": "tokenshare.protocol_runtime_observation.v1",
        "run_id": run_id,
        "runtime_started_at": runtime_started_at,
        "runtime_ended_at": runtime_ended_at,
        "runtime_wall_clock_ms": (
            (ended - started).total_seconds() * 1000.0
        ),
        "planned_ai_unit_ids": list(planned),
        "dispatched_ai_unit_ids": list(dispatched),
        "completed_ai_unit_ids": list(completed),
        "unscheduled_ai_unit_ids": [
            unit_id for unit_id in planned if unit_id not in set(dispatched)
        ],
        "in_flight_ai_unit_ids_at_witness": list(in_flight),
        "witness_observed_at": witness_observed_at,
        "worker_execution_facts": facts,
        "observed_peak_concurrency": _observed_peak_concurrency(facts),
    }


def project_protocol_run(
    *,
    run_id: str,
    task_id: str,
    root_unit_id: str,
    event_ledger: EventLedger,
    artifact_store: ArtifactStore,
    runtime_observation: Mapping[str, Any] | None = None,
) -> ProtocolRunResult:
    """只从 ledger 事实派生结果，并仅返回 event/artifact 引用及摘要。"""

    events = [event for event in event_ledger.read_all() if event.task_id == task_id]
    units: dict[str, JsonObject] = {}
    attempts: dict[str, JsonObject] = {}
    attempts_by_unit: dict[str, list[str]] = {}
    canonical_artifacts: dict[str, list[str]] = {}
    artifact_refs: dict[tuple[str, str], ArtifactRef] = {}

    for event in events:
        unit = event.payload.get("task_unit")
        if isinstance(unit, dict) and isinstance(unit.get("unit_id"), str):
            units[unit["unit_id"]] = unit
        attempt = event.payload.get("attempt")
        if isinstance(attempt, dict) and isinstance(attempt.get("attempt_id"), str):
            attempts[attempt["attempt_id"]] = attempt
            unit_id = attempt.get("unit_id")
            if isinstance(unit_id, str):
                attempts_by_unit.setdefault(unit_id, [])
                if attempt["attempt_id"] not in attempts_by_unit[unit_id]:
                    attempts_by_unit[unit_id].append(attempt["attempt_id"])
        if event.event_type == EventType.CANONICAL_OUTPUTS_BOUND:
            selection = event.payload.get("canonical_selection")
            if isinstance(selection, dict) and isinstance(selection.get("unit_id"), str):
                canonical_artifacts[selection["unit_id"]] = [
                    value.get("artifact_id")
                    for value in selection.get("canonical_output_refs", {}).values()
                    if isinstance(value, dict) and isinstance(value.get("artifact_id"), str)
                ]
        for ref in _artifact_refs(event.payload):
            if not artifact_store.verify(ref):
                raise ValueError(
                    "artifact reference verification failed: "
                    f"{ref.artifact_id}"
                )
            artifact_refs[(ref.artifact_id, ref.content_hash)] = ref

    unit_views = tuple(
        RuntimeUnitView(
            unit_id=unit_id,
            state=str(unit["state"]),
            attempt_ids=tuple(attempts_by_unit.get(unit_id, ())),
            canonical_artifact_ids=tuple(canonical_artifacts.get(unit_id, ())),
        )
        for unit_id, unit in sorted(units.items())
    )
    attempt_views = tuple(
        RuntimeAttemptView(
            attempt_id=attempt_id,
            unit_id=str(attempt["unit_id"]),
            state=str(attempt["state"]),
        )
        for attempt_id, attempt in sorted(attempts.items())
    )
    root_state = str(units.get(root_unit_id, {}).get("state", "Pending"))
    status = root_state.lower()
    summary: JsonObject = {
        "event_count": len(events),
        "artifact_count": len(artifact_refs),
        "unit_state_counts": dict(Counter(view.state for view in unit_views)),
        "attempt_state_counts": dict(Counter(view.state for view in attempt_views)),
        "units": [
            {
                "unit_id": view.unit_id,
                "state": view.state,
                "attempt_ids": list(view.attempt_ids),
                "canonical_artifact_ids": list(view.canonical_artifact_ids),
            }
            for view in unit_views
        ],
        "attempts": [
            {
                "attempt_id": view.attempt_id,
                "unit_id": view.unit_id,
                "state": view.state,
            }
            for view in attempt_views
        ],
    }
    if runtime_observation is not None:
        summary["runtime_observation"] = dict(runtime_observation)
    return ProtocolRunResult(
        run_id=run_id,
        task_id=task_id,
        root_unit_id=root_unit_id,
        status=status,
        event_refs=tuple(
            {
                "event_id": event.event_id,
                "event_seq": event.event_seq,
                "event_type": (
                    event.event_type.value
                    if isinstance(event.event_type, EventType)
                    else str(event.event_type)
                ),
            }
            for event in events
        ),
        artifact_refs=tuple(
            artifact_refs[key] for key in sorted(artifact_refs)
        ),
        summary=summary,
    )


def _unique_strings(values: Sequence[str], field_name: str) -> tuple[str, ...]:
    normalized = tuple(values)
    if any(not isinstance(value, str) or not value for value in normalized):
        raise ValueError(f"{field_name} must contain non-empty strings")
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field_name} must contain unique values")
    return normalized


def _observed_peak_concurrency(
    worker_execution_facts: Sequence[Mapping[str, Any]],
) -> int:
    boundaries: list[tuple[datetime, int]] = []
    for fact in worker_execution_facts:
        started_at = fact.get("started_at")
        ended_at = fact.get("ended_at")
        if not isinstance(started_at, str) or not isinstance(ended_at, str):
            continue
        started = _timestamp(started_at)
        ended = _timestamp(ended_at)
        if ended < started:
            raise ValueError("worker execution fact ended before it started")
        boundaries.append((started, 1))
        boundaries.append((ended, -1))
    active = 0
    peak = 0
    for _timestamp_value, delta in sorted(
        boundaries,
        key=lambda item: (item[0], item[1]),
    ):
        active += delta
        peak = max(peak, active)
    return peak


def _timestamp(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("runtime observation timestamp is invalid") from exc


def _artifact_refs(value: Any):
    if isinstance(value, dict):
        if _looks_like_artifact_ref_candidate(value):
            if not _looks_like_artifact_ref(value):
                raise ValueError("malformed artifact reference in ledger")
            try:
                yield ArtifactRef.from_dict(value)
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("malformed artifact reference in ledger") from exc
            return
        for item in value.values():
            yield from _artifact_refs(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _artifact_refs(item)


def _looks_like_artifact_ref_candidate(value: dict[str, Any]) -> bool:
    return "artifact_id" in value and bool(
        {
            "artifact_type",
            "uri",
            "content_hash",
            "artifact_schema_id",
        }
        & set(value)
    )


def _looks_like_artifact_ref(value: dict[str, Any]) -> bool:
    return {
        "artifact_id",
        "artifact_type",
        "uri",
        "content_hash",
        "size_bytes",
        "media_type",
        "artifact_schema_id",
        "artifact_schema_version",
        "source",
        "metadata",
        "created_at",
    } <= set(value)
