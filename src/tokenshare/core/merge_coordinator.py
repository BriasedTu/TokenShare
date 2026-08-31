"""Phase 5 merge task creation coordinator."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

from tokenshare.core.expansion import MergePlan
from tokenshare.core.merge import (
    MergeTaskLink,
    RequiredSlotBinding,
    digest_required_slot_bindings,
)
from tokenshare.core.models import ArtifactRef, ProtocolConfig, TaskRelation, TaskState, TaskUnit
from tokenshare.core.task_graph import TaskGraph
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventDraft, EventLedger, EventType, LedgerEvent


JsonObject = dict[str, object]


@dataclass(frozen=True)
class BatchView:
    batch_id: str
    events: tuple[LedgerEvent, ...]
    task_expanded_visible: bool = True
    schema_version: str = "phase5.batch_view.v1"

    @property
    def has_final_marker(self) -> bool:
        return self.is_complete and self.task_expanded_visible and self.events[-1].event_type == EventType.TASK_EXPANDED

    @property
    def is_complete(self) -> bool:
        if not self.events:
            return False
        batch_sizes = {event.batch_size for event in self.events}
        batch_ids = {event.batch_id for event in self.events}
        batch_size = next(iter(batch_sizes)) if len(batch_sizes) == 1 else None
        return (
            len(batch_sizes) == 1
            and None not in batch_sizes
            and len(batch_ids) == 1
            and batch_size is not None
            and len(self.events) == batch_size
        )


@dataclass(frozen=True)
class MergeTaskCreationFlowResult:
    merge_task_link: MergeTaskLink
    merge_task_unit: TaskUnit
    merge_input_bundle_ref: ArtifactRef
    events: tuple[LedgerEvent, ...]
    optional_task_relation: TaskRelation | None = None


class MergeCoordinator:
    """Create merge tasks once Phase 4 expansion facts are fully visible."""

    def __init__(
        self,
        *,
        event_ledger: EventLedger,
        artifact_store: ArtifactStore,
        protocol_config: ProtocolConfig,
    ) -> None:
        self._event_ledger = event_ledger
        self._artifact_store = artifact_store
        self._protocol_config = protocol_config

    def create_ready_merge_tasks(
        self,
        *,
        task_id: str,
        graph: TaskGraph,
        merge_plan_events: list[LedgerEvent],
        expansion_batches: list[BatchView],
        canonical_events: list[LedgerEvent],
        now: str,
        coordinator_id: str,
        correlation_id: str,
        readiness_decision: JsonObject | None = None,
    ) -> list[MergeTaskCreationFlowResult]:
        """Create merge TaskUnits for visible accepted MergePlan facts."""

        selected_child_unit_ids = _selected_child_unit_ids(readiness_decision)
        visible_batches = {batch.batch_id: batch for batch in expansion_batches}
        canonical_by_unit_id = _canonical_events_by_unit_id(canonical_events)
        results: list[MergeTaskCreationFlowResult] = []

        for merge_plan_event in merge_plan_events:
            if merge_plan_event.event_type != EventType.MERGE_PLAN_RECORDED:
                continue
            merge_plan_id = _merge_plan_id_from_event(merge_plan_event)
            expansion_batch = visible_batches.get(merge_plan_event.batch_id or "")
            if expansion_batch is None:
                raise ValueError("projection inconsistent: expansion_batch not visible")
            if not expansion_batch.is_complete:
                raise ValueError("projection inconsistent: incomplete expansion_batch")
            if not expansion_batch.has_final_marker:
                raise ValueError("projection inconsistent: TASK_EXPANDED marker not visible")

            merge_plan = _load_merge_plan(
                artifact_store=self._artifact_store,
                merge_plan_event=merge_plan_event,
            )
            bindings = _build_required_slot_bindings(
                merge_plan=merge_plan,
                canonical_by_unit_id=canonical_by_unit_id,
                selected_child_unit_ids=selected_child_unit_ids,
            )
            merge_input_bundle_ref = self._stage_merge_input_bundle(
                task_id=task_id,
                parent_unit_id=merge_plan.merge_plan_header["parent_unit_id"],
                merge_plan=merge_plan,
                required_slot_bindings=bindings,
                readiness_decision=readiness_decision,
                coordinator_id=coordinator_id,
                now=now,
            )
            merge_task_link = _build_merge_task_link(
                merge_plan=merge_plan,
                merge_plan_event=merge_plan_event,
                expansion_batch=expansion_batch,
                merge_input_bundle_ref=merge_input_bundle_ref,
                required_slot_bindings=bindings,
                readiness_decision=readiness_decision,
                now=now,
                coordinator_id=coordinator_id,
            )
            merge_unit = _build_merge_unit(
                graph=graph,
                merge_plan=merge_plan,
                merge_input_bundle_ref=merge_input_bundle_ref,
                now=now,
            )
            drafts = _build_merge_task_creation_drafts(
                task_id=task_id,
                merge_plan=merge_plan,
                merge_plan_event=merge_plan_event,
                merge_task_link=merge_task_link,
                merge_unit=merge_unit,
                merge_input_bundle_ref=merge_input_bundle_ref,
                required_slot_bindings=bindings,
                readiness_decision=readiness_decision,
                now=now,
                correlation_id=correlation_id,
            )
            batch_id = f"merge_task_creation_batch:{merge_plan_id}"
            batch_events = self._event_ledger.append_batch(drafts, batch_id=batch_id)
            results.append(
                MergeTaskCreationFlowResult(
                    merge_task_link=merge_task_link,
                    merge_task_unit=merge_unit,
                    merge_input_bundle_ref=merge_input_bundle_ref,
                    events=batch_events,
                    optional_task_relation=None,
                )
            )

        return results

    def _stage_merge_input_bundle(
        self,
        *,
        task_id: str,
        parent_unit_id: str,
        merge_plan: MergePlan,
        required_slot_bindings: list[RequiredSlotBinding] | None = None,
        canonical_selection: object | None = None,
        readiness_decision: JsonObject | None = None,
        coordinator_id: str,
        now: str,
    ) -> ArtifactRef:
        """Persist the merge input bundle before the authoritative marker."""

        if required_slot_bindings is None:
            required_slot_bindings = []
        bundle_schema_version = (
            "phase5.merge_input_bundle.v2"
            if readiness_decision is not None
            else "phase5.merge_input_bundle.v1"
        )
        bundle_payload = {
            "schema_version": bundle_schema_version,
            "task_id": task_id,
            "parent_unit_id": parent_unit_id,
            "merge_plan_id": merge_plan.merge_plan_header["merge_plan_id"],
            "expansion_decision_id": merge_plan.merge_plan_header["expansion_decision_id"],
            "merge_policy_ref": merge_plan.merge_policy_ref,
            "parent_output_mapping": merge_plan.parent_output_mapping,
            "required_slot_bindings": [binding.to_dict() for binding in required_slot_bindings],
            "required_slot_bindings_digest": digest_required_slot_bindings(required_slot_bindings),
            "readiness_decision": readiness_decision,
            "created_at": now,
            "created_by": {
                "coordinator_id": coordinator_id,
                "coordinator_version": "1",
            },
        }
        if canonical_selection is not None:
            bundle_payload["canonical_selection_id"] = getattr(
                canonical_selection,
                "canonical_selection_id",
                None,
            )
        try:
            return self._artifact_store.save_json(
                bundle_payload,
                artifact_id=f"merge_input_bundle:{merge_plan.merge_plan_header['merge_plan_id']}",
                artifact_type="MergeInputBundle",
                artifact_schema_id="phase5.merge_input_bundle",
                artifact_schema_version=(
                    "v2" if readiness_decision is not None else "v1"
                ),
                source={"kind": "merge_coordinator", "stage": "staged"},
                metadata={
                    "task_id": task_id,
                    "parent_unit_id": parent_unit_id,
                    "merge_plan_id": merge_plan.merge_plan_header["merge_plan_id"],
                },
                created_at=now,
            )
        except ValueError as exc:
            message = str(exc)
            if "artifact_id already exists with different content" in message:
                raise ValueError(
                    "merge task creation conflict: merge_input_bundle already exists with different content"
                ) from exc
            raise


def _build_required_slot_bindings(
    *,
    merge_plan: MergePlan,
    canonical_by_unit_id: dict[str, LedgerEvent],
    selected_child_unit_ids: tuple[str, ...] | None = None,
) -> list[RequiredSlotBinding]:
    bindings: list[RequiredSlotBinding] = []
    selected = (
        None
        if selected_child_unit_ids is None
        else set(selected_child_unit_ids)
    )
    for slot in merge_plan.required_slots:
        child_unit_id = slot["source_child_unit_id"]
        if selected is not None and child_unit_id not in selected:
            continue
        canonical_event = canonical_by_unit_id.get(child_unit_id)
        if canonical_event is None:
            raise ValueError("required slots not canonical")
        canonical_selection = canonical_event.payload.get("canonical_selection")
        if not isinstance(canonical_selection, dict):
            raise ValueError("canonical output missing canonical selection")
        output_name = slot["source_output_name"]
        canonical_output_refs = canonical_selection.get("canonical_output_refs", {})
        if output_name not in canonical_output_refs:
            raise ValueError("required slots not canonical")
        canonical_output_ref_data = canonical_output_refs[output_name]
        if not isinstance(canonical_output_ref_data, dict):
            raise ValueError("canonical output ref must be an object")
        if canonical_output_ref_data.get("artifact_type") != "canonical_output":
            raise ValueError("required slot must reference a canonical output")
        canonical_output_ref = ArtifactRef.from_dict(canonical_output_ref_data)
        bindings.append(
            RequiredSlotBinding(
                slot_key=slot["slot_key"],
                slot_id=slot.get("slot_id"),
                source_child_logical_key=slot["source_child_logical_key"],
                source_child_unit_id=child_unit_id,
                source_output_name=output_name,
                source_output_schema_digest=slot["output_schema_digest"],
                canonical_selection_id=canonical_selection["canonical_selection_id"],
                canonical_event_seq=canonical_event.event_seq,
                canonical_output_ref=canonical_output_ref.to_dict(),
                canonical_output_digest=canonical_output_ref.content_hash,
                canonical_output_bundle_digest=canonical_selection["canonical_output_bundle_digest"],
                selected_verification_report_id=canonical_selection["selected_verification_report_id"],
                selected_attempt_id=canonical_selection["selected_attempt_id"],
                binding_source="canonical_output",
            )
        )
    if selected is not None:
        bound = {binding.source_child_unit_id for binding in bindings}
        missing = sorted(selected.difference(bound))
        if missing:
            raise ValueError(
                "readiness selected child units do not resolve merge slots: "
                + ", ".join(missing)
            )
    return bindings


def _build_merge_task_link(
    *,
    merge_plan: MergePlan,
    merge_plan_event: LedgerEvent,
    expansion_batch: BatchView,
    merge_input_bundle_ref: ArtifactRef,
    required_slot_bindings: list[RequiredSlotBinding],
    readiness_decision: JsonObject | None,
    now: str,
    coordinator_id: str,
) -> MergeTaskLink:
    merge_plan_id = merge_plan.merge_plan_header["merge_plan_id"]
    merge_unit_id = f"merge_unit:{merge_plan_id}"
    merge_task_link = MergeTaskLink(
        merge_task_link_id=f"merge_task_link:{merge_plan_id}",
        task_id=merge_plan.merge_plan_header["task_id"],
        parent_unit_id=merge_plan.merge_plan_header["parent_unit_id"],
        merge_plan_id=merge_plan_id,
        expansion_decision_id=merge_plan.merge_plan_header["expansion_decision_id"],
        merge_unit_id=merge_unit_id,
        merge_input_bundle_ref=merge_input_bundle_ref.to_dict(),
        merge_input_bundle_digest=merge_input_bundle_ref.content_hash,
        required_slot_bindings=required_slot_bindings,
        required_slot_bindings_digest=digest_required_slot_bindings(required_slot_bindings),
        merge_policy_id=merge_plan.merge_policy_ref["merge_policy_id"],
        merge_policy_version=merge_plan.merge_policy_ref["merge_policy_version"],
        merge_policy_descriptor_digest=merge_plan.merge_policy_ref["merge_policy_descriptor_digest"],
        source_merge_plan_event_seq=merge_plan_event.event_seq,
        source_task_expanded_event_seq=expansion_batch.events[-1].event_seq,
        optional_task_relation_id=None,
        readiness_reason=(
            "all_required_slots_canonical"
            if readiness_decision is None
            else str(readiness_decision["reason"])
        ),
        created_at=now,
        coordinator={"coordinator_id": coordinator_id, "coordinator_version": "1"},
        readiness_decision=readiness_decision,
        schema_version=(
            "phase5.merge_task_link.v1"
            if readiness_decision is None
            else "phase5.merge_task_link.v2"
        ),
    )
    return merge_task_link


def _build_merge_unit(
    *,
    graph: TaskGraph,
    merge_plan: MergePlan,
    merge_input_bundle_ref: ArtifactRef,
    now: str,
) -> TaskUnit:
    parent_unit_id = merge_plan.merge_plan_header["parent_unit_id"]
    parent_unit = graph.units[parent_unit_id]
    return TaskUnit(
        unit_id=f"merge_unit:{merge_plan.merge_plan_header['merge_plan_id']}",
        task_id=merge_plan.merge_plan_header["task_id"],
        parent_unit_id=parent_unit_id,
        depth=parent_unit.depth + 1,
        unit_type="merge",
        state=TaskState.READY,
        input_refs={"merge_input_bundle": merge_input_bundle_ref},
        canonical_output_refs={},
        required_capabilities={"executor": "local"},
        weight=1.0,
        budget_limit=None,
        deadline=None,
        plugin_payload={},
        metadata={
            "merge_plan_id": merge_plan.merge_plan_header["merge_plan_id"],
        },
        created_at=now,
        updated_at=now,
    )


def _build_merge_task_creation_drafts(
    *,
    task_id: str,
    merge_plan: MergePlan,
    merge_plan_event: LedgerEvent,
    merge_task_link: MergeTaskLink,
    merge_unit: TaskUnit,
    merge_input_bundle_ref: ArtifactRef,
    required_slot_bindings: list[RequiredSlotBinding],
    readiness_decision: JsonObject | None,
    now: str,
    correlation_id: str,
) -> tuple[EventDraft, ...]:
    record_version = "v2" if readiness_decision is not None else "v1"
    task_unit_payload = {
        "schema_version": f"phase5.merge_task_unit_created.{record_version}",
        "task_unit": merge_unit.to_dict(),
        "merge_task_link_id": merge_task_link.merge_task_link_id,
        "merge_plan_id": merge_task_link.merge_plan_id,
        "expansion_decision_id": merge_task_link.expansion_decision_id,
        "merge_input_bundle_ref": merge_input_bundle_ref.to_dict(),
        "merge_input_bundle_digest": merge_input_bundle_ref.content_hash,
        "required_slot_bindings_digest": merge_task_link.required_slot_bindings_digest,
        "required_slot_count": len(required_slot_bindings),
        "canonical_event_seqs": [binding.canonical_event_seq for binding in required_slot_bindings],
        "readiness_reason": merge_task_link.readiness_reason,
        "readiness_decision": readiness_decision,
        "created_at": now,
    }
    link_payload = {
        "schema_version": f"phase5.merge_task_link_record.{record_version}",
        "merge_task_link": merge_task_link.to_dict(),
        "task_id": task_id,
        "parent_unit_id": merge_task_link.parent_unit_id,
        "merge_plan_id": merge_task_link.merge_plan_id,
        "expansion_decision_id": merge_task_link.expansion_decision_id,
        "merge_unit_id": merge_task_link.merge_unit_id,
        "merge_input_bundle_ref": merge_input_bundle_ref.to_dict(),
        "merge_input_bundle_digest": merge_input_bundle_ref.content_hash,
        "required_slot_bindings_digest": merge_task_link.required_slot_bindings_digest,
        "required_slot_count": len(required_slot_bindings),
        "canonical_event_seqs": [binding.canonical_event_seq for binding in required_slot_bindings],
        "readiness_reason": merge_task_link.readiness_reason,
        "readiness_decision": readiness_decision,
        "created_at": now,
    }
    return (
        EventDraft(
            event_type=EventType.TASK_UNIT_CREATED,
            object_type="TaskUnit",
            object_id=merge_unit.unit_id,
            task_id=task_id,
            actor={"kind": "merge_coordinator"},
            correlation_id=correlation_id,
            causation_event_id=merge_plan_event.event_id,
            idempotency_key=f"task_unit:create:{merge_unit.unit_id}",
            payload=task_unit_payload,
            occurred_at=now,
        ),
        EventDraft(
            event_type=EventType.MERGE_TASK_LINK_RECORDED,
            object_type="MergeTaskLink",
            object_id=merge_task_link.merge_task_link_id,
            task_id=task_id,
            actor={"kind": "merge_coordinator"},
            correlation_id=correlation_id,
            causation_event_id=merge_plan_event.event_id,
            idempotency_key=f"merge_task_link:{merge_task_link.merge_plan_id}",
            payload=link_payload,
            occurred_at=now,
        ),
    )


def _canonical_events_by_unit_id(events: list[LedgerEvent]) -> dict[str, LedgerEvent]:
    canonical_events: dict[str, LedgerEvent] = {}
    for event in events:
        if event.event_type != EventType.CANONICAL_OUTPUTS_BOUND:
            continue
        canonical_selection = event.payload.get("canonical_selection")
        if not isinstance(canonical_selection, dict):
            continue
        unit_id = canonical_selection.get("unit_id")
        if isinstance(unit_id, str):
            canonical_events[unit_id] = event
    return canonical_events


def _selected_child_unit_ids(
    readiness_decision: JsonObject | None,
) -> tuple[str, ...] | None:
    if readiness_decision is None:
        return None
    if (
        readiness_decision.get("schema_version")
        != "tokenshare.merge_readiness_decision.v1"
        or readiness_decision.get("status") != "ready"
    ):
        raise ValueError("merge task creation requires a ready v1 decision")
    selected = readiness_decision.get("selected_child_unit_ids")
    if (
        not isinstance(selected, list)
        or not selected
        or any(not isinstance(item, str) or not item for item in selected)
        or len(set(selected)) != len(selected)
    ):
        raise ValueError("ready decision must select unique child unit IDs")
    return tuple(selected)


def _load_merge_plan(*, artifact_store: ArtifactStore, merge_plan_event: LedgerEvent) -> MergePlan:
    merge_plan_ref = merge_plan_event.payload.get("merge_plan_ref")
    if not isinstance(merge_plan_ref, dict):
        raise ValueError("merge plan ref missing")
    artifact_ref = ArtifactRef.from_dict(merge_plan_ref)
    if not artifact_store.verify(artifact_ref):
        raise ValueError("merge plan artifact digest mismatch")
    merge_plan_payload = json.loads(artifact_store.read_bytes(artifact_ref).decode("utf-8"))
    merge_plan = MergePlan(**merge_plan_payload)
    if merge_plan.merge_plan_header["merge_plan_id"] != merge_plan_event.payload.get("merge_plan_id"):
        raise ValueError("merge plan id mismatch")
    return merge_plan


def _merge_plan_id_from_event(merge_plan_event: LedgerEvent) -> str:
    merge_plan_id = merge_plan_event.payload.get("merge_plan_id")
    if not isinstance(merge_plan_id, str):
        raise ValueError("merge plan id missing")
    return merge_plan_id
