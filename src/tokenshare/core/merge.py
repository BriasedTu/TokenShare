"""Phase 5 merge pure protocol objects."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from typing import Any


JsonObject = dict[str, Any]


@dataclass(frozen=True)
class RequiredSlotBinding:
    """required slot 到 child canonical output 的绑定事实。"""

    slot_key: str
    slot_id: str | None
    source_child_logical_key: str
    source_child_unit_id: str
    source_output_name: str
    source_output_schema_digest: str
    canonical_selection_id: str
    canonical_event_seq: int
    canonical_output_ref: JsonObject
    canonical_output_digest: str
    canonical_output_bundle_digest: str
    selected_verification_report_id: str
    selected_attempt_id: str
    binding_source: str
    schema_version: str = "phase5.required_slot_binding.v1"

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version, "phase5.required_slot_binding.v1")
        _require_non_empty(
            {
                "slot_key": self.slot_key,
                "source_child_logical_key": self.source_child_logical_key,
                "source_child_unit_id": self.source_child_unit_id,
                "source_output_name": self.source_output_name,
                "source_output_schema_digest": self.source_output_schema_digest,
                "canonical_selection_id": self.canonical_selection_id,
                "canonical_output_digest": self.canonical_output_digest,
                "canonical_output_bundle_digest": self.canonical_output_bundle_digest,
                "selected_verification_report_id": self.selected_verification_report_id,
                "selected_attempt_id": self.selected_attempt_id,
            }
        )
        if self.binding_source != "canonical_output":
            raise ValueError("RequiredSlotBinding binding_source must be canonical_output")
        if self.canonical_event_seq <= 0:
            raise ValueError("canonical_event_seq must be positive")
        if not isinstance(self.canonical_output_ref, dict):
            raise ValueError("canonical_output_ref must be an object")
        if self.canonical_output_ref.get("artifact_type") != "canonical_output":
            raise ValueError("canonical_output_ref must reference a canonical output")

    def to_dict(self) -> JsonObject:
        return _dataclass_dict(self)


@dataclass(frozen=True)
class MergeTaskLink:
    """parent、MergePlan、merge TaskUnit 和稳定输入 bundle 的链接。"""

    merge_task_link_id: str
    task_id: str
    parent_unit_id: str
    merge_plan_id: str
    expansion_decision_id: str
    merge_unit_id: str
    merge_input_bundle_ref: JsonObject
    merge_input_bundle_digest: str
    required_slot_bindings: list[RequiredSlotBinding | JsonObject]
    required_slot_bindings_digest: str
    merge_policy_id: str
    merge_policy_version: str
    merge_policy_descriptor_digest: str
    source_merge_plan_event_seq: int
    source_task_expanded_event_seq: int
    optional_task_relation_id: str | None
    readiness_reason: str
    created_at: str
    coordinator: JsonObject
    schema_version: str = "phase5.merge_task_link.v1"

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version, "phase5.merge_task_link.v1")
        _require_non_empty(
            {
                "merge_task_link_id": self.merge_task_link_id,
                "task_id": self.task_id,
                "parent_unit_id": self.parent_unit_id,
                "merge_plan_id": self.merge_plan_id,
                "expansion_decision_id": self.expansion_decision_id,
                "merge_unit_id": self.merge_unit_id,
                "merge_input_bundle_digest": self.merge_input_bundle_digest,
                "required_slot_bindings_digest": self.required_slot_bindings_digest,
                "merge_policy_id": self.merge_policy_id,
                "merge_policy_version": self.merge_policy_version,
                "merge_policy_descriptor_digest": self.merge_policy_descriptor_digest,
                "created_at": self.created_at,
            }
        )
        if self.readiness_reason != "all_required_slots_canonical":
            raise ValueError("readiness_reason must be all_required_slots_canonical")
        if self.source_merge_plan_event_seq <= 0 or self.source_task_expanded_event_seq <= 0:
            raise ValueError("source event seq fields must be positive")
        if not isinstance(self.merge_input_bundle_ref, dict):
            raise ValueError("merge_input_bundle_ref must be an object")
        if not isinstance(self.coordinator, dict):
            raise ValueError("coordinator must be an object")

        bindings = tuple(_coerce_binding(binding) for binding in self.required_slot_bindings)
        _reject_duplicate_slots(bindings)
        expected_digest = digest_required_slot_bindings(bindings)
        if self.required_slot_bindings_digest != expected_digest:
            raise ValueError("required_slot_bindings_digest mismatch")
        object.__setattr__(self, "required_slot_bindings", list(_sort_bindings(bindings)))

    def to_dict(self) -> JsonObject:
        return _dataclass_dict(self)


@dataclass(frozen=True)
class MergeRecord:
    """merge TaskUnit canonical output 形成的 merge commitment。"""

    merge_record_id: str
    task_id: str
    parent_unit_id: str
    merge_plan_id: str
    merge_unit_id: str
    merge_task_link_id: str
    merge_input_bundle_ref: JsonObject
    merge_input_bundle_digest: str
    required_slot_bindings_digest: str
    merge_policy_id: str
    merge_policy_version: str
    merge_policy_descriptor_digest: str
    merge_policy_params_digest: str
    canonical_selection_id: str
    canonical_event_seq: int
    selected_verification_report_id: str
    selected_verification_event_seq: int
    selected_submission_id: str
    selected_submission_event_seq: int
    selected_attempt_id: str
    merge_output_bundle_digest: str
    merge_output_refs: dict[str, JsonObject]
    parent_output_mapping_digest: str
    created_at: str
    schema_version: str = "phase5.merge_record.v1"

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version, "phase5.merge_record.v1")
        _require_non_empty(
            {
                "merge_record_id": self.merge_record_id,
                "task_id": self.task_id,
                "parent_unit_id": self.parent_unit_id,
                "merge_plan_id": self.merge_plan_id,
                "merge_unit_id": self.merge_unit_id,
                "merge_task_link_id": self.merge_task_link_id,
                "merge_input_bundle_digest": self.merge_input_bundle_digest,
                "required_slot_bindings_digest": self.required_slot_bindings_digest,
                "merge_policy_id": self.merge_policy_id,
                "merge_policy_version": self.merge_policy_version,
                "merge_policy_descriptor_digest": self.merge_policy_descriptor_digest,
                "merge_policy_params_digest": self.merge_policy_params_digest,
                "canonical_selection_id": self.canonical_selection_id,
                "selected_verification_report_id": self.selected_verification_report_id,
                "selected_submission_id": self.selected_submission_id,
                "selected_attempt_id": self.selected_attempt_id,
                "merge_output_bundle_digest": self.merge_output_bundle_digest,
                "parent_output_mapping_digest": self.parent_output_mapping_digest,
                "created_at": self.created_at,
            }
        )
        for field_name in (
            "canonical_event_seq",
            "selected_verification_event_seq",
            "selected_submission_event_seq",
        ):
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} must be positive")
        if not isinstance(self.merge_input_bundle_ref, dict):
            raise ValueError("merge_input_bundle_ref must be an object")
        if not isinstance(self.merge_output_refs, dict) or not self.merge_output_refs:
            raise ValueError("merge_output_refs must be a non-empty object")

    def to_dict(self) -> JsonObject:
        return _dataclass_dict(self)


@dataclass(frozen=True)
class ExpectedOutputResolution:
    """Phase 5 v1 只支持 merge_record 来源的 expected output resolution。"""

    expected_output_resolution_id: str
    task_id: str
    owner_unit_id: str
    expected_output_id: str
    expected_output_name: str
    resolution_source_type: str
    merge_record_id: str
    merge_plan_id: str
    merge_unit_id: str
    merge_canonical_selection_id: str
    resolved_output_ref: JsonObject
    resolved_output_digest: str
    resolved_at: str
    schema_version: str = "phase5.expected_output_resolution.v1"

    def __post_init__(self) -> None:
        _require_schema_version(
            self.schema_version, "phase5.expected_output_resolution.v1"
        )
        _require_non_empty(
            {
                "expected_output_resolution_id": self.expected_output_resolution_id,
                "task_id": self.task_id,
                "owner_unit_id": self.owner_unit_id,
                "expected_output_id": self.expected_output_id,
                "expected_output_name": self.expected_output_name,
                "merge_record_id": self.merge_record_id,
                "merge_plan_id": self.merge_plan_id,
                "merge_unit_id": self.merge_unit_id,
                "merge_canonical_selection_id": self.merge_canonical_selection_id,
                "resolved_output_digest": self.resolved_output_digest,
                "resolved_at": self.resolved_at,
            }
        )
        if self.resolution_source_type != "merge_record":
            raise ValueError("ExpectedOutputResolution v1 source must be merge_record")
        if not isinstance(self.resolved_output_ref, dict):
            raise ValueError("resolved_output_ref must be an object")

    def to_dict(self) -> JsonObject:
        return _dataclass_dict(self)


def digest_merge_task_link(bindings: list[RequiredSlotBinding | JsonObject]) -> str:
    """兼容测试和 flow 的 slot bindings digest helper。"""

    return digest_required_slot_bindings(bindings)


def digest_required_slot_bindings(bindings: list[RequiredSlotBinding | JsonObject] | tuple[RequiredSlotBinding | JsonObject, ...]) -> str:
    normalized = [_coerce_binding(binding).to_dict() for binding in bindings]
    return digest_json(_sort_dicts_by(normalized, "slot_key"))


def digest_json(data: Any) -> str:
    return f"sha256:{sha256(_canonical_json(data).encode('utf-8')).hexdigest()}"


def _coerce_binding(binding: RequiredSlotBinding | JsonObject) -> RequiredSlotBinding:
    if isinstance(binding, RequiredSlotBinding):
        return binding
    if isinstance(binding, dict):
        return RequiredSlotBinding(**binding)
    raise ValueError("required_slot_bindings must contain RequiredSlotBinding objects")


def _sort_bindings(bindings: tuple[RequiredSlotBinding, ...]) -> tuple[RequiredSlotBinding, ...]:
    return tuple(sorted(bindings, key=lambda binding: binding.slot_key))


def _sort_dicts_by(items: list[JsonObject], key: str) -> list[JsonObject]:
    return sorted(items, key=lambda item: item[key])


def _reject_duplicate_slots(bindings: tuple[RequiredSlotBinding, ...]) -> None:
    seen: set[str] = set()
    for binding in bindings:
        if binding.slot_key in seen:
            raise ValueError(f"duplicate required slot: {binding.slot_key}")
        seen.add(binding.slot_key)


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
