"""Phase 5 contribution and sandbox settlement pure objects."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from enum import Enum
from hashlib import sha256
from typing import Any


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
