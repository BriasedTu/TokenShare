"""Phase 5 contribution and sandbox settlement pure objects."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from enum import Enum
from hashlib import sha256
from typing import Any, Iterable

from tokenshare.storage.events import EventLedger, EventType, LedgerEvent


JsonObject = dict[str, Any]


class ContributionState(str, Enum):
    """Contribution lifecycle strings serialized into Phase 5 events."""

    PENDING = "Pending"
    ELIGIBLE = "Eligible"
    INVALIDATED = "Invalidated"
    SETTLED = "Settled"


CONTRIBUTION_KINDS = {"complete_canonical", "expand_canonical", "merge_canonical"}


@dataclass(frozen=True)
class ContributionRecord:
    """settlement 只按 canonical / accepted facts 计数的贡献记录。"""

    contribution_id: str
    task_id: str
    unit_id: str
    kind: str
    state: ContributionState | str
    source_attempt_id: str
    source_client_id: str
    canonical_selection_id: str
    canonical_event_seq: int
    verification_report_id: str
    verification_event_seq: int
    source_decision_id: str | None
    merge_record_id: str | None
    source_batch_id: str
    source_terminal_event_seq: int
    reward_weight: int
    created_at: str
    updated_at: str
    schema_version: str = "phase5.contribution_record.v1"

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version, "phase5.contribution_record.v1")
        state = ContributionState(self.state)
        object.__setattr__(self, "state", state)
        _require_non_empty(
            {
                "contribution_id": self.contribution_id,
                "task_id": self.task_id,
                "unit_id": self.unit_id,
                "kind": self.kind,
                "source_attempt_id": self.source_attempt_id,
                "source_client_id": self.source_client_id,
                "canonical_selection_id": self.canonical_selection_id,
                "verification_report_id": self.verification_report_id,
                "source_batch_id": self.source_batch_id,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
            }
        )
        if self.kind not in CONTRIBUTION_KINDS:
            raise ValueError(f"invalid contribution kind: {self.kind}")
        if self.kind in {"complete_canonical", "expand_canonical"} and not self.source_decision_id:
            raise ValueError(f"{self.kind} requires source_decision_id")
        if self.kind == "merge_canonical" and not self.merge_record_id:
            raise ValueError("merge_canonical requires merge_record_id")
        for field_name in (
            "canonical_event_seq",
            "verification_event_seq",
            "source_terminal_event_seq",
        ):
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} must be positive")
        if self.reward_weight <= 0:
            raise ValueError("reward_weight must be positive")

    def to_dict(self) -> JsonObject:
        return _dataclass_dict(self)


@dataclass(frozen=True)
class ContributionFlowResult:
    contribution: ContributionRecord
    event: LedgerEvent


class ContributionCoordinator:
    """Derive Phase 5 contribution records from complete canonical source batches."""

    def __init__(self, *, event_ledger: EventLedger) -> None:
        self._event_ledger = event_ledger

    def record_canonical_contributions(
        self,
        *,
        task_id: str,
        completion_batches: list[Any],
        expansion_batches: list[Any],
        merge_resolution_batches: list[Any],
        now: str,
        correlation_id: str,
    ) -> list[ContributionFlowResult]:
        events = tuple(self._event_ledger.read_all())
        results: list[ContributionFlowResult] = []
        for batch in completion_batches:
            contribution, source_event = _contribution_from_completion_batch(
                task_id=task_id,
                batch=batch,
                events=events,
            )
            results.append(
                self._append_creation_event(
                    contribution=contribution,
                    source_event=source_event,
                    now=now,
                    correlation_id=correlation_id,
                )
            )
        for batch in expansion_batches:
            contribution, source_event = _contribution_from_expansion_batch(
                task_id=task_id,
                batch=batch,
                events=events,
            )
            results.append(
                self._append_creation_event(
                    contribution=contribution,
                    source_event=source_event,
                    now=now,
                    correlation_id=correlation_id,
                )
            )
        for batch in merge_resolution_batches:
            contribution, source_event = _contribution_from_merge_resolution_batch(
                task_id=task_id,
                batch=batch,
                events=events,
            )
            results.append(
                self._append_creation_event(
                    contribution=contribution,
                    source_event=source_event,
                    now=now,
                    correlation_id=correlation_id,
                )
            )
        return results

    def _append_creation_event(
        self,
        *,
        contribution: ContributionRecord,
        source_event: LedgerEvent,
        now: str,
        correlation_id: str,
    ) -> ContributionFlowResult:
        new_state = ContributionState(contribution.state)
        event = self._event_ledger.append(
            event_type=EventType.CONTRIBUTION_STATE_CHANGED,
            object_type="ContributionRecord",
            object_id=contribution.contribution_id,
            task_id=contribution.task_id,
            actor={"kind": "contribution_coordinator"},
            correlation_id=correlation_id,
            causation_event_id=source_event.event_id,
            idempotency_key=(
                f"contribution:create:{contribution.contribution_id}:"
                f"{new_state.value}"
            ),
            payload={
                "schema_version": "phase5.contribution_state_changed.v1",
                "contribution": contribution.to_dict(),
                "old_state": None,
                "new_state": new_state.value,
                "reason": "canonical_contribution_created",
                "task_id": contribution.task_id,
                "unit_id": contribution.unit_id,
                "kind": contribution.kind,
                "canonical_selection_id": contribution.canonical_selection_id,
                "canonical_event_seq": contribution.canonical_event_seq,
                "source_batch_id": contribution.source_batch_id,
                "source_terminal_event_seq": contribution.source_terminal_event_seq,
                "changed_at": contribution.updated_at,
            },
            occurred_at=now,
        )
        recorded = event.payload.get("contribution")
        contribution_record = (
            ContributionRecord(**recorded)
            if isinstance(recorded, dict)
            else contribution
        )
        return ContributionFlowResult(contribution=contribution_record, event=event)


@dataclass(frozen=True)
class SettlementEntry:
    """一次 root-level settlement 的单项 reward 分配。"""

    settlement_entry_id: str
    contribution_id: str
    task_id: str
    unit_id: str
    kind: str
    source_client_id: str
    reward_weight: int
    reward_units: int
    rounding_remainder_rank: int
    reason: str
    schema_version: str = "phase5.settlement_entry.v1"

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version, "phase5.settlement_entry.v1")
        _require_non_empty(
            {
                "settlement_entry_id": self.settlement_entry_id,
                "contribution_id": self.contribution_id,
                "task_id": self.task_id,
                "unit_id": self.unit_id,
                "kind": self.kind,
                "source_client_id": self.source_client_id,
                "reason": self.reason,
            }
        )
        if self.kind not in CONTRIBUTION_KINDS:
            raise ValueError(f"invalid contribution kind: {self.kind}")
        if self.reward_weight <= 0:
            raise ValueError("reward_weight must be positive")
        if self.reward_units < 0:
            raise ValueError("reward_units must be non-negative")
        if self.rounding_remainder_rank < 0:
            raise ValueError("rounding_remainder_rank must be non-negative")

    def to_dict(self) -> JsonObject:
        return _dataclass_dict(self)


@dataclass(frozen=True)
class SettlementRecord:
    """settlement batch final marker 的逻辑对象。"""

    settlement_record_id: str
    task_id: str
    root_unit_id: str
    root_completion_event_seq: int
    settlement_policy_id: str
    settlement_policy_version: str
    root_budget: int
    scale: str
    total_reward: int
    entry_count: int
    settlement_entries_digest: str
    settlement_entries_ref: JsonObject
    settlement_summary: JsonObject
    created_at: str
    schema_version: str = "phase5.settlement_record.v1"

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version, "phase5.settlement_record.v1")
        _require_non_empty(
            {
                "settlement_record_id": self.settlement_record_id,
                "task_id": self.task_id,
                "root_unit_id": self.root_unit_id,
                "settlement_policy_id": self.settlement_policy_id,
                "settlement_policy_version": self.settlement_policy_version,
                "scale": self.scale,
                "settlement_entries_digest": self.settlement_entries_digest,
                "created_at": self.created_at,
            }
        )
        if self.root_completion_event_seq <= 0:
            raise ValueError("root_completion_event_seq must be positive")
        if self.root_budget < 0:
            raise ValueError("root_budget must be non-negative")
        if self.total_reward != self.root_budget:
            raise ValueError("total_reward must equal root_budget")
        if self.entry_count <= 0:
            raise ValueError("entry_count must be positive")
        if not isinstance(self.settlement_entries_ref, dict):
            raise ValueError("settlement_entries_ref must be an object")
        if not isinstance(self.settlement_summary, dict):
            raise ValueError("settlement_summary must be an object")

    def to_dict(self) -> JsonObject:
        return _dataclass_dict(self)


@dataclass(frozen=True)
class SubtreePruneRecord:
    """父节点完成后 post-completion pruning 的 audit marker。"""

    subtree_prune_id: str
    task_id: str
    parent_unit_id: str
    parent_completed_event_seq: int
    pruning_policy_id: str
    pruning_policy_version: str
    pruning_policy_plugin_id: str
    pruning_policy_descriptor_digest: str
    policy_source_type: str
    policy_source_id: str
    policy_source_event_seq: int
    cancelled_unit_count: int
    cancelled_unit_ids_digest: str
    preserved_completed_unit_count: int
    reason: str
    created_at: str
    schema_version: str = "phase5.subtree_prune_record.v1"

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version, "phase5.subtree_prune_record.v1")
        _require_non_empty(
            {
                "subtree_prune_id": self.subtree_prune_id,
                "task_id": self.task_id,
                "parent_unit_id": self.parent_unit_id,
                "pruning_policy_id": self.pruning_policy_id,
                "pruning_policy_version": self.pruning_policy_version,
                "pruning_policy_plugin_id": self.pruning_policy_plugin_id,
                "pruning_policy_descriptor_digest": self.pruning_policy_descriptor_digest,
                "policy_source_type": self.policy_source_type,
                "policy_source_id": self.policy_source_id,
                "cancelled_unit_ids_digest": self.cancelled_unit_ids_digest,
                "reason": self.reason,
                "created_at": self.created_at,
            }
        )
        if self.policy_source_type not in {"merge_policy", "merge_plan"}:
            raise ValueError("invalid policy_source_type")
        for field_name in ("parent_completed_event_seq", "policy_source_event_seq"):
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} must be positive")
        if self.cancelled_unit_count < 0:
            raise ValueError("cancelled_unit_count must be non-negative")
        if self.preserved_completed_unit_count < 0:
            raise ValueError("preserved_completed_unit_count must be non-negative")

    def to_dict(self) -> JsonObject:
        return _dataclass_dict(self)


def transition_contribution(
    contribution: ContributionRecord,
    *,
    new_state: ContributionState | str,
    changed_at: str,
    reason: str,
    source_batch_kind: str | None = None,
) -> ContributionRecord:
    """校验并返回 contribution 状态推进后的不可变副本。"""

    target_state = ContributionState(new_state)
    old_state = ContributionState(contribution.state)
    if old_state == ContributionState.SETTLED:
        raise ValueError("Settled contribution cannot transition again")
    if (old_state, target_state) not in {
        (ContributionState.PENDING, ContributionState.ELIGIBLE),
        (ContributionState.PENDING, ContributionState.INVALIDATED),
        (ContributionState.ELIGIBLE, ContributionState.INVALIDATED),
        (ContributionState.ELIGIBLE, ContributionState.SETTLED),
    }:
        raise ValueError(f"illegal Contribution transition: {old_state.value} -> {target_state.value}")
    if target_state == ContributionState.SETTLED:
        if source_batch_kind != "settlement_batch" and reason != "settlement_batch":
            raise ValueError("Eligible -> Settled requires settlement_batch")
    return replace(contribution, state=target_state, updated_at=changed_at)


def build_sandbox_equal_weight_settlement_entries(
    *,
    task_id: str,
    root_unit_id: str,
    root_completion_event_seq: int,
    eligible_contributions: list[ContributionRecord],
    root_budget: int,
    settlement_policy_id: str,
    settlement_policy_version: str,
    scale: str,
    created_at: str,
) -> list[SettlementEntry]:
    """按 Phase 5 sandbox formula 确定性分配整数 reward。"""

    del settlement_policy_id, settlement_policy_version, scale, created_at
    if root_completion_event_seq <= 0:
        raise ValueError("root_completion_event_seq must be positive")
    if root_budget < 0:
        raise ValueError("root_budget must be non-negative")
    contributions = sorted(
        [
            contribution
            for contribution in eligible_contributions
            if contribution.state == ContributionState.ELIGIBLE
            and contribution.source_terminal_event_seq <= root_completion_event_seq
        ],
        key=lambda contribution: contribution.contribution_id,
    )
    if not contributions:
        raise ValueError("settlement requires at least one eligible contribution")
    total_weight = sum(contribution.reward_weight for contribution in contributions)
    base_rewards = [
        (root_budget * contribution.reward_weight) // total_weight
        for contribution in contributions
    ]
    remainder = root_budget - sum(base_rewards)
    settlement_record_id = f"settlement:{task_id}:{root_unit_id}:{root_completion_event_seq}"
    entries: list[SettlementEntry] = []
    for index, (contribution, base_reward) in enumerate(
        zip(contributions, base_rewards, strict=True)
    ):
        reward_units = base_reward + (1 if index < remainder else 0)
        entries.append(
            SettlementEntry(
                settlement_entry_id=(
                    f"settlement_entry:{settlement_record_id}:{contribution.contribution_id}"
                ),
                contribution_id=contribution.contribution_id,
                task_id=contribution.task_id,
                unit_id=contribution.unit_id,
                kind=contribution.kind,
                source_client_id=contribution.source_client_id,
                reward_weight=contribution.reward_weight,
                reward_units=reward_units,
                rounding_remainder_rank=index,
                reason="eligible_contribution_at_root_completion",
            )
        )
    return entries


def digest_contribution(contribution: ContributionRecord) -> str:
    return digest_json(contribution.to_dict())


def digest_settlement_entries(entries: list[SettlementEntry]) -> str:
    return digest_json(
        sorted(
            [entry.to_dict() for entry in entries],
            key=lambda entry: entry["contribution_id"],
        )
    )


def digest_json(data: Any) -> str:
    return f"sha256:{sha256(_canonical_json(data).encode('utf-8')).hexdigest()}"


def _contribution_from_completion_batch(
    *, task_id: str, batch: Any, events: tuple[LedgerEvent, ...]
) -> tuple[ContributionRecord, LedgerEvent]:
    batch_events = _validated_named_batch_events(batch, "completion_batch")
    if len(batch_events) != 2:
        raise ValueError("projection inconsistent: incomplete completion_batch")
    decision_event, terminal_event = batch_events
    if (
        decision_event.event_type != EventType.EXPANSION_DECISION_RECORDED
        or decision_event.payload.get("action") != "complete"
        or terminal_event.event_type != EventType.TASK_UNIT_STATE_CHANGED
        or terminal_event.payload.get("new_state") != "Completed"
    ):
        raise ValueError("projection inconsistent: incomplete completion_batch")
    return (
        _contribution_from_decision_event(
            kind="complete_canonical",
            state=ContributionState.ELIGIBLE,
            task_id=task_id,
            decision_event=decision_event,
            source_terminal_event=terminal_event,
            source_batch_id=batch.batch_id,
            events=events,
        ),
        terminal_event,
    )


def _contribution_from_expansion_batch(
    *, task_id: str, batch: Any, events: tuple[LedgerEvent, ...]
) -> tuple[ContributionRecord, LedgerEvent]:
    batch_events = _validated_named_batch_events(batch, "expansion_batch")
    decision_event = _single_event_of_type(
        batch_events, EventType.EXPANSION_DECISION_RECORDED
    )
    terminal_event = batch_events[-1]
    if (
        getattr(batch, "task_expanded_visible", True) is not True
        or decision_event.payload.get("action") != "expand"
        or terminal_event.event_type != EventType.TASK_EXPANDED
    ):
        raise ValueError("projection inconsistent: incomplete expansion_batch")
    return (
        _contribution_from_decision_event(
            kind="expand_canonical",
            state=ContributionState.PENDING,
            task_id=task_id,
            decision_event=decision_event,
            source_terminal_event=terminal_event,
            source_batch_id=batch.batch_id,
            events=events,
        ),
        terminal_event,
    )


def _contribution_from_merge_resolution_batch(
    *, task_id: str, batch: Any, events: tuple[LedgerEvent, ...]
) -> tuple[ContributionRecord, LedgerEvent]:
    batch_events = _validated_named_batch_events(batch, "merge_resolution_batch")
    merge_event = batch_events[0]
    if (
        len(batch_events) < 2
        or merge_event.event_type != EventType.MERGE_RECORDED
        or any(
            event.event_type != EventType.EXPECTED_OUTPUT_RESOLVED
            for event in batch_events[1:]
        )
    ):
        raise ValueError("projection inconsistent: incomplete merge_resolution_batch")
    merge_record = merge_event.payload.get("merge_record")
    if not isinstance(merge_record, dict):
        raise ValueError("merge_record payload missing")
    unit_id = _required_string(merge_record, "merge_unit_id")
    canonical_selection_id = _required_string(merge_record, "canonical_selection_id")
    selected_attempt_id = _required_string(merge_record, "selected_attempt_id")
    source_client_id = _source_client_id(
        events=events,
        selected_attempt_id=selected_attempt_id,
    )
    created_at = merge_event.occurred_at
    contribution = ContributionRecord(
        contribution_id=_contribution_id(
            kind="merge_canonical",
            task_id=task_id,
            unit_id=unit_id,
            canonical_selection_id=canonical_selection_id,
        ),
        task_id=task_id,
        unit_id=unit_id,
        kind="merge_canonical",
        state=ContributionState.ELIGIBLE,
        source_attempt_id=selected_attempt_id,
        source_client_id=source_client_id,
        canonical_selection_id=canonical_selection_id,
        canonical_event_seq=int(merge_record["canonical_event_seq"]),
        verification_report_id=_required_string(
            merge_record, "selected_verification_report_id"
        ),
        verification_event_seq=int(merge_record["selected_verification_event_seq"]),
        source_decision_id=None,
        merge_record_id=_required_string(merge_record, "merge_record_id"),
        source_batch_id=batch.batch_id,
        source_terminal_event_seq=merge_event.event_seq,
        reward_weight=1,
        created_at=created_at,
        updated_at=created_at,
    )
    return contribution, merge_event


def _contribution_from_decision_event(
    *,
    kind: str,
    state: ContributionState,
    task_id: str,
    decision_event: LedgerEvent,
    source_terminal_event: LedgerEvent,
    source_batch_id: str,
    events: tuple[LedgerEvent, ...],
) -> ContributionRecord:
    canonical_selection_id = _required_string(
        decision_event.payload, "canonical_selection_id"
    )
    canonical_event = _canonical_event_for_selection(
        events=events,
        canonical_selection_id=canonical_selection_id,
    )
    canonical_selection = canonical_event.payload.get("canonical_selection")
    if not isinstance(canonical_selection, dict):
        raise ValueError("canonical selection payload missing")
    selected_attempt_id = _required_string(canonical_selection, "selected_attempt_id")
    source_client_id = _source_client_id(
        events=events,
        selected_attempt_id=selected_attempt_id,
    )
    unit_id = _required_string(decision_event.payload, "unit_id")
    created_at = source_terminal_event.occurred_at
    return ContributionRecord(
        contribution_id=_contribution_id(
            kind=kind,
            task_id=task_id,
            unit_id=unit_id,
            canonical_selection_id=canonical_selection_id,
        ),
        task_id=task_id,
        unit_id=unit_id,
        kind=kind,
        state=state,
        source_attempt_id=selected_attempt_id,
        source_client_id=source_client_id,
        canonical_selection_id=canonical_selection_id,
        canonical_event_seq=canonical_event.event_seq,
        verification_report_id=_required_string(
            canonical_selection, "selected_verification_report_id"
        ),
        verification_event_seq=int(
            canonical_selection["selected_verification_event_seq"]
        ),
        source_decision_id=decision_event.object_id,
        merge_record_id=None,
        source_batch_id=source_batch_id,
        source_terminal_event_seq=source_terminal_event.event_seq,
        reward_weight=1,
        created_at=created_at,
        updated_at=created_at,
    )


def _validated_batch_events(batch: Any) -> tuple[LedgerEvent, ...]:
    batch_id = getattr(batch, "batch_id", None)
    events = tuple(getattr(batch, "events", ()))
    if not batch_id or not events:
        raise ValueError("projection inconsistent: incomplete batch")
    batch_sizes = {event.batch_size for event in events}
    batch_ids = {event.batch_id for event in events}
    indexes = [event.batch_index for event in events]
    batch_size = next(iter(batch_sizes)) if len(batch_sizes) == 1 else None
    if (
        len(batch_ids) != 1
        or batch_id not in batch_ids
        or batch_size is None
        or batch_size != len(events)
        or indexes != list(range(1, len(events) + 1))
    ):
        raise ValueError("projection inconsistent: incomplete batch")
    return events


def _validated_named_batch_events(batch: Any, batch_kind: str) -> tuple[LedgerEvent, ...]:
    try:
        return _validated_batch_events(batch)
    except ValueError as error:
        raise ValueError(f"projection inconsistent: incomplete {batch_kind}") from error


def _single_event_of_type(
    events: tuple[LedgerEvent, ...], event_type: EventType
) -> LedgerEvent:
    matches = [event for event in events if event.event_type == event_type]
    if len(matches) != 1:
        raise ValueError(f"projection inconsistent: expected one {event_type.value}")
    return matches[0]


def _canonical_event_for_selection(
    *, events: Iterable[LedgerEvent], canonical_selection_id: str
) -> LedgerEvent:
    for event in events:
        if event.event_type != EventType.CANONICAL_OUTPUTS_BOUND:
            continue
        selection = event.payload.get("canonical_selection")
        if (
            isinstance(selection, dict)
            and selection.get("canonical_selection_id") == canonical_selection_id
        ):
            return event
    raise ValueError("canonical selection event missing for contribution")


def _source_client_id(
    *, events: Iterable[LedgerEvent], selected_attempt_id: str
) -> str:
    for event in reversed(tuple(events)):
        if event.event_type != EventType.ATTEMPT_STATE_CHANGED:
            continue
        if event.object_id != selected_attempt_id:
            continue
        attempt = event.payload.get("attempt")
        if isinstance(attempt, dict):
            client_id = attempt.get("client_id")
            if isinstance(client_id, str) and client_id:
                return client_id
    raise ValueError("selected canonical attempt client missing for contribution")


def _contribution_id(
    *, kind: str, task_id: str, unit_id: str, canonical_selection_id: str
) -> str:
    return f"contribution:{kind}:{task_id}:{unit_id}:{canonical_selection_id}"


def _required_string(source: JsonObject, key: str) -> str:
    value = source.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} is required")
    return value


def _require_schema_version(actual: str, expected: str) -> None:
    if actual != expected:
        raise ValueError(f"invalid schema_version: expected {expected}, got {actual}")


def _require_non_empty(values: dict[str, Any]) -> None:
    for field_name, value in values.items():
        if value is None or value == "":
            raise ValueError(f"{field_name} is required")


def _dataclass_dict(instance: Any) -> JsonObject:
    return {
        "schema_version": instance.schema_version,
        **{
            key: _json_value(value)
            for key, value in instance.__dict__.items()
            if key != "schema_version"
        },
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


def _canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
