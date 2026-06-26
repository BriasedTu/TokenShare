"""Phase 4 expansion 纯对象和校验 helper。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tokenshare.core.models import JsonObject, ProtocolConfig, TaskState, _json_value
from tokenshare.core.task_graph import TaskGraph
from tokenshare.core.verification import digest_json


PROPOSAL_HEADER_REQUIRED_FIELDS = (
    "proposal_id",
    "proposal_schema_version",
    "task_id",
    "parent_unit_id",
    "canonical_selection_id",
    "canonical_output_bundle_digest",
    "plugin_id",
    "plugin_version",
    "plugin_descriptor_digest",
    "split_strategy_id",
    "split_strategy_params_digest",
    "expansion_scope_hash",
    "proposal_digest",
    "created_at",
)

CHILD_SPEC_REQUIRED_FIELDS = (
    "child_logical_key",
    "unit_type",
    "input_bindings",
    "required_outputs",
    "output_contract_refs",
    "validator_policy_id",
    "weight",
    "required_capabilities",
    "plugin_payload",
)

DEPENDENCY_EDGE_REQUIRED_FIELDS = (
    "edge_logical_key",
    "source_child_key",
    "target_child_key",
    "source_output_name",
    "target_input_name",
    "relation_type",
)

EXPECTED_OUTPUT_REQUIRED_FIELDS = (
    "output_name",
    "schema_ref",
    "resolution_kind",
    "required",
)

MERGE_SLOT_REQUIRED_FIELDS = (
    "slot_id",
    "child_key",
    "child_output_name",
    "schema_ref",
    "required",
    "missing_policy",
)

MERGE_PLAN_HEADER_REQUIRED_FIELDS = (
    "merge_plan_id",
    "merge_plan_schema_version",
    "task_id",
    "parent_unit_id",
    "canonical_selection_id",
    "decomposition_proposal_id",
    "expansion_decision_id",
    "created_by_plugin_id",
    "created_by_plugin_version",
    "merge_plan_digest",
    "created_at",
)

MERGE_POLICY_REF_REQUIRED_FIELDS = (
    "plugin_id",
    "plugin_version",
    "merge_policy_id",
    "merge_policy_version",
    "merge_policy_descriptor_digest",
    "merge_policy_params_digest",
)

REQUIRED_SLOT_FIELDS = (
    "slot_key",
    "source_child_logical_key",
    "source_child_unit_id",
    "source_output_name",
    "output_schema_ref",
    "output_schema_digest",
    "required",
    "missing_policy",
)

PARENT_OUTPUT_MAPPING_FIELDS = (
    "parent_output_name",
    "resolution_kind",
    "merge_slot_keys",
    "result_schema_ref",
    "result_schema_digest",
)

HASH_RECORDING_REQUIREMENT_FIELDS = (
    "record_child_canonical_output_digest",
    "record_slot_source_artifact_digest",
    "record_merge_input_bundle_digest",
)

MERGE_VALIDATION_REQUIREMENT_FIELDS = (
    "all_required_slots_canonical",
    "slot_schema_check_required",
    "merged_output_schema_check_required",
    "plugin_merge_validator_policy_id",
)

MERGE_PLUGIN_PAYLOAD_FIELDS = (
    "plugin_defined_schema_ref",
    "plugin_defined_body_digest",
    "plugin_defined_body",
)

EXPAND_EVIDENCE_FIELDS = (
    "proposal_id",
    "proposal_digest",
    "merge_plan_id",
    "merge_plan_digest",
    "child_count",
    "relation_count",
    "expected_output_count",
    "required_merge_slot_count",
)

AUTHORITATIVE_PLUGIN_PAYLOAD_KEYS = {
    "state",
    "initial_state",
    "desired_state",
    "task_state",
    "attempt_state",
    "resolution_status",
    "canonical_output_refs",
    "canonical_outputs_by_unit_id",
    "canonical_selection_id",
    "canonical_output_bundle_digest",
    "expected_output_refs",
    "merge_readiness",
}


@dataclass(frozen=True)
class SplitStrategyInvocation:
    """一次 plugin split-strategy 调用的审计记录。"""

    invocation_id: str
    invocation_attempt_no: int
    expansion_scope_hash: str
    task_id: str
    unit_id: str
    canonical_selection_id: str
    canonical_output_bundle_digest: str
    plugin_id: str
    plugin_version: str
    plugin_descriptor_digest: str
    split_strategy_id: str
    split_strategy_params_digest: str
    status: str
    started_at: str
    completed_at: str
    result_action: str | None = None
    result_digest: str | None = None
    error_kind: str | None = None
    error_summary: str | None = None
    metadata: JsonObject | None = None
    schema_version: str = "phase4.split_strategy_invocation.v1"

    def __post_init__(self) -> None:
        _require_schema_version(
            self.schema_version,
            "phase4.split_strategy_invocation.v1",
        )
        if self.status not in {"succeeded", "failed", "invalid_result"}:
            raise ValueError(f"invalid split invocation status: {self.status}")
        if self.status == "succeeded":
            if self.result_action not in {"complete", "expand"}:
                raise ValueError("succeeded invocation requires result_action")
            if not self.result_digest:
                raise ValueError("succeeded invocation requires result_digest")

    def to_dict(self) -> JsonObject:
        return _dataclass_dict(self)


@dataclass(frozen=True)
class SplitStrategyResult:
    """accepted decision 记录前的 plugin split-strategy 返回对象。"""

    action: str
    expansion_scope_hash: str
    split_strategy_identity: JsonObject
    complete: JsonObject | None
    expand: JsonObject | None
    generation_evidence: JsonObject
    created_at: str
    schema_version: str = "phase4.split_strategy_result.v1"

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version, "phase4.split_strategy_result.v1")
        if self.action not in {"complete", "expand"}:
            raise ValueError(f"invalid split strategy action: {self.action}")
        if self.complete is not None and self.expand is not None:
            raise ValueError("complete and expand bodies are mutually exclusive")
        if self.action == "complete" and self.complete is None:
            raise ValueError("complete action requires complete body")
        if self.action == "complete" and self.expand is not None:
            raise ValueError("complete action cannot carry expand body")
        if self.action == "expand" and self.expand is None:
            raise ValueError("expand action requires expand body")
        if self.action == "expand" and self.complete is not None:
            raise ValueError("expand action cannot carry complete body")
        if self.expand is not None:
            if not self.expand.get("proposal_digest") or not self.expand.get("merge_plan_digest"):
                raise ValueError("expand body requires proposal_digest and merge_plan_digest")

    def to_dict(self) -> JsonObject:
        return _dataclass_dict(self)


@dataclass(frozen=True)
class DecompositionProposal:
    """graph mutation 前需要校验的 plugin-generated proposal body。"""

    proposal_header: JsonObject
    child_specs: list[JsonObject]
    dependency_edges: list[JsonObject]
    expected_outputs: list[JsonObject]
    merge_slots: list[JsonObject]
    promotion_guard_evidence: JsonObject
    schema_version: str = "phase4.decomposition_proposal.v1"

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version, "phase4.decomposition_proposal.v1")
        _validate_proposal_header(self.proposal_header)
        _validate_child_specs(self.child_specs)
        _validate_dependency_edges(self.child_specs, self.dependency_edges)
        _validate_merge_slots(self.child_specs, self.merge_slots)
        _validate_expected_outputs(self.child_specs, self.merge_slots, self.expected_outputs)
        _validate_promotion_guard(self.promotion_guard_evidence)

    @property
    def child_specs_by_key(self) -> dict[str, JsonObject]:
        return {spec["child_logical_key"]: spec for spec in self.child_specs}

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "proposal_header": _json_value(self.proposal_header),
            "child_specs": _json_value(self.child_specs),
            "dependency_edges": _json_value(self.dependency_edges),
            "expected_outputs": _json_value(self.expected_outputs),
            "merge_slots": _json_value(self.merge_slots),
            "promotion_guard_evidence": _json_value(self.promotion_guard_evidence),
        }


@dataclass(frozen=True)
class ExpansionDecision:
    """已接受的 complete 或 expand decision body。"""

    expansion_decision_id: str
    task_id: str
    unit_id: str
    canonical_selection_id: str
    canonical_output_bundle_digest: str
    expansion_scope_hash: str
    action: str
    plugin_id: str
    plugin_version: str
    plugin_descriptor_digest: str
    split_strategy_id: str
    split_strategy_params_digest: str
    source_invocation_id: str
    action_body: JsonObject
    decided_at: str
    proposal_id: str | None = None
    proposal_digest: str | None = None
    merge_plan_id: str | None = None
    merge_plan_digest: str | None = None
    schema_version: str = "phase4.expansion_decision.v1"

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version, "phase4.expansion_decision.v1")
        if self.action not in {"complete", "expand"}:
            raise ValueError(f"invalid expansion action: {self.action}")
        if self.action == "complete":
            if any(
                value is not None
                for value in (
                    self.proposal_id,
                    self.proposal_digest,
                    self.merge_plan_id,
                    self.merge_plan_digest,
                )
            ):
                raise ValueError("complete decision cannot carry proposal or merge plan refs")
            if "completion_evidence" not in self.action_body:
                raise ValueError("complete decision requires completion_evidence")
        if self.action == "expand":
            if not all(
                (
                    self.proposal_id,
                    self.proposal_digest,
                    self.merge_plan_id,
                    self.merge_plan_digest,
                )
            ):
                raise ValueError("expand decision requires proposal and merge plan refs")
            if set(self.action_body.keys()) != {"expand_evidence"}:
                raise ValueError("expand decision action_body must contain only expand_evidence")
            _validate_expand_evidence(self)

    def to_dict(self) -> JsonObject:
        return _dataclass_dict(self)


@dataclass(frozen=True)
class MergePlan:
    """某次 expansion 的 plugin merge-policy 实例契约。"""

    merge_plan_header: JsonObject
    merge_policy_ref: JsonObject
    required_slots: list[JsonObject]
    parent_output_mapping: list[JsonObject]
    hash_recording_requirements: JsonObject
    merge_validation_requirements: JsonObject
    plugin_payload: JsonObject
    schema_version: str = "phase4.merge_plan.v1"

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version, "phase4.merge_plan.v1")
        _validate_merge_plan_header(self.merge_plan_header)
        _validate_merge_policy_ref(self.merge_policy_ref)
        _validate_required_slots(self.required_slots)
        _validate_parent_output_mapping(self.parent_output_mapping, self.required_slots)
        _validate_hash_recording_requirements(self.hash_recording_requirements)
        _validate_merge_validation_requirements(self.merge_validation_requirements)
        _validate_merge_plugin_payload(self.plugin_payload)

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "merge_plan_header": _json_value(self.merge_plan_header),
            "merge_policy_ref": _json_value(self.merge_policy_ref),
            "required_slots": _json_value(self.required_slots),
            "parent_output_mapping": _json_value(self.parent_output_mapping),
            "hash_recording_requirements": _json_value(self.hash_recording_requirements),
            "merge_validation_requirements": _json_value(self.merge_validation_requirements),
            "plugin_payload": _json_value(self.plugin_payload),
        }


@dataclass(frozen=True)
class ExpectedOutputRef:
    """`TASK_EXPANDED` 可见后派生的 Phase 4 output future。"""

    expected_output_id: str
    task_id: str
    owner_unit_id: str
    output_name: str
    schema_ref: JsonObject
    resolution_kind: str
    resolution_status: str
    canonical_selection_id: str
    canonical_output_bundle_digest: str
    source_proposal_id: str
    source_expansion_decision_id: str
    created_event_seq: int
    child_unit_id: str | None = None
    child_output_name: str | None = None
    merge_plan_id: str | None = None
    resolved_event_seq: int | None = None
    schema_version: str = "phase4.expected_output_ref.v1"

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version, "phase4.expected_output_ref.v1")

    @classmethod
    def from_expected_output(
        cls,
        *,
        expected_output: JsonObject,
        task_id: str,
        owner_unit_id: str,
        canonical_selection_id: str,
        canonical_output_bundle_digest: str,
        source_proposal_id: str,
        source_expansion_decision_id: str,
        created_event_seq: int,
        logical_position: int,
        child_unit_ids_by_key: dict[str, str],
        merge_plan_id: str | None,
    ) -> "ExpectedOutputRef":
        resolution_kind = expected_output["resolution_kind"]
        child_unit_id = None
        child_output_name = None
        resolved_merge_plan_id = None
        if resolution_kind == "child_output":
            child_key = expected_output.get("child_key")
            child_output_name = expected_output.get("child_output_name")
            if child_key not in child_unit_ids_by_key:
                raise ValueError("child_key must resolve to child_unit_id")
            if not child_output_name:
                raise ValueError("child_output_name is required for child_output")
            child_unit_id = child_unit_ids_by_key[child_key]
        if resolution_kind == "merge_plan_output":
            if not merge_plan_id:
                raise ValueError("merge_plan_id is required for merge_plan_output")
            resolved_merge_plan_id = merge_plan_id
        if resolution_kind not in {
            "direct_parent_output",
            "child_output",
            "merge_plan_output",
        }:
            raise ValueError(f"invalid expected output resolution_kind: {resolution_kind}")
        return cls(
            expected_output_id=(
                f"expected_output:{source_proposal_id}:{owner_unit_id}:"
                f"{expected_output['output_name']}:{logical_position}"
            ),
            task_id=task_id,
            owner_unit_id=owner_unit_id,
            output_name=expected_output["output_name"],
            schema_ref=dict(expected_output["schema_ref"]),
            resolution_kind=resolution_kind,
            resolution_status="expected",
            child_unit_id=child_unit_id,
            child_output_name=child_output_name,
            merge_plan_id=resolved_merge_plan_id,
            canonical_selection_id=canonical_selection_id,
            canonical_output_bundle_digest=canonical_output_bundle_digest,
            source_proposal_id=source_proposal_id,
            source_expansion_decision_id=source_expansion_decision_id,
            created_event_seq=created_event_seq,
            resolved_event_seq=None,
        )

    def to_dict(self) -> JsonObject:
        return _dataclass_dict(self)


def derive_child_initial_state(
    *,
    proposal: DecompositionProposal,
    child_logical_key: str,
    graph: TaskGraph,
    parent_canonical_output_refs: dict[str, Any],
) -> TaskState:
    """根据 input/dependency 满足情况派生 child 初始状态。"""

    del graph
    child_specs_by_key = proposal.child_specs_by_key
    if child_logical_key not in child_specs_by_key:
        raise ValueError(f"unknown child logical key: {child_logical_key}")
    if _incoming_dependency_edges(proposal, child_logical_key):
        return TaskState.BLOCKED

    child_spec = child_specs_by_key[child_logical_key]
    for binding in child_spec.get("input_bindings", {}).values():
        binding_kind = binding.get("kind")
        if binding_kind == "parent_output":
            if binding.get("output_name") not in parent_canonical_output_refs:
                raise ValueError("parent output binding missing canonical output")
        elif binding_kind in {"dependency_output", "child_output"}:
            return TaskState.BLOCKED
        elif binding_kind in {"artifact_ref", "constant"}:
            continue
        else:
            return TaskState.BLOCKED
    return TaskState.READY


def validate_decomposition_proposal_limits(
    proposal: DecompositionProposal,
    *,
    protocol_config: ProtocolConfig,
    parent_depth: int,
    existing_unit_count: int,
    parent_required_output_names: list[str],
    max_children_per_strategy: int | None = None,
) -> None:
    """校验需要外部 graph/config 上下文的 proposal 限制。"""

    child_count = len(proposal.child_specs)
    max_children = protocol_config.max_children_per_unit
    if max_children_per_strategy is not None:
        max_children = min(max_children, max_children_per_strategy)
    if child_count > max_children:
        raise ValueError("child count exceeds configured limit")
    if parent_depth + 1 > protocol_config.max_depth:
        raise ValueError("proposal exceeds max_depth")
    if existing_unit_count + child_count > protocol_config.max_total_units:
        raise ValueError("proposal exceeds max_total_units")

    expected_required_outputs = {
        expected_output["output_name"]
        for expected_output in proposal.expected_outputs
        if expected_output.get("required") is True
    }
    missing_parent_outputs = [
        output_name
        for output_name in parent_required_output_names
        if output_name not in expected_required_outputs
    ]
    if missing_parent_outputs:
        raise ValueError(
            "proposal missing parent required output coverage: "
            + ", ".join(missing_parent_outputs)
        )


def digest_decomposition_proposal_body(proposal: DecompositionProposal) -> str:
    """计算 proposal 正文 digest，排除自引用的 id/digest 头字段。"""

    data = proposal.to_dict()
    header = dict(data["proposal_header"])
    header.pop("proposal_id", None)
    header.pop("proposal_digest", None)
    data["proposal_header"] = header
    return digest_json(data)


def digest_merge_plan_body(merge_plan: MergePlan) -> str:
    """计算 merge plan 正文 digest，排除自引用的 id/digest 头字段。"""

    data = merge_plan.to_dict()
    header = dict(data["merge_plan_header"])
    header.pop("merge_plan_id", None)
    header.pop("merge_plan_digest", None)
    data["merge_plan_header"] = header
    return digest_json(data)


def _validate_expand_evidence(decision: ExpansionDecision) -> None:
    evidence = decision.action_body.get("expand_evidence")
    if not isinstance(evidence, dict):
        raise ValueError("expand decision requires expand_evidence")
    missing = sorted(set(EXPAND_EVIDENCE_FIELDS).difference(evidence))
    if missing:
        raise ValueError("expand_evidence missing required fields: " + ", ".join(missing))
    extra = sorted(set(evidence).difference(EXPAND_EVIDENCE_FIELDS))
    if extra:
        raise ValueError("expand_evidence has unexpected fields: " + ", ".join(extra))
    for field_name, expected in (
        ("proposal_id", decision.proposal_id),
        ("proposal_digest", decision.proposal_digest),
        ("merge_plan_id", decision.merge_plan_id),
        ("merge_plan_digest", decision.merge_plan_digest),
    ):
        if evidence.get(field_name) != expected:
            raise ValueError(f"expand_evidence {field_name} mismatch")
    for field_name in (
        "child_count",
        "relation_count",
        "expected_output_count",
        "required_merge_slot_count",
    ):
        value = evidence.get(field_name)
        if type(value) is not int or value < 0:
            raise ValueError(f"expand_evidence {field_name} must be a non-negative integer")


def _validate_merge_plan_header(header: JsonObject) -> None:
    _require_fields(header, MERGE_PLAN_HEADER_REQUIRED_FIELDS, "merge_plan_header")
    if header.get("merge_plan_schema_version") != "phase4.merge_plan.v1":
        raise ValueError("invalid merge plan schema version")


def _validate_merge_policy_ref(ref: JsonObject) -> None:
    _require_fields(ref, MERGE_POLICY_REF_REQUIRED_FIELDS, "merge_policy_ref")


def _validate_required_slots(slots: list[JsonObject]) -> None:
    if not slots:
        raise ValueError("required_slots must not be empty")
    seen: set[str] = set()
    for slot in slots:
        _require_fields(slot, REQUIRED_SLOT_FIELDS, "required_slot")
        slot_key = slot.get("slot_key")
        if not slot_key:
            raise ValueError("required slot_key is required")
        if slot_key in seen:
            raise ValueError(f"duplicate required slot: {slot_key}")
        seen.add(slot_key)
        if slot.get("required") is not True:
            raise ValueError("MergePlan slots must be required in Phase 4")
        if slot.get("missing_policy") != "block_merge":
            raise ValueError("MergePlan required slots must use block_merge")
        if not isinstance(slot.get("output_schema_ref"), dict):
            raise ValueError("required_slot output_schema_ref must be an object")


def _validate_parent_output_mapping(
    mappings: list[JsonObject], slots: list[JsonObject]
) -> None:
    if not mappings:
        raise ValueError("parent_output_mapping must not be empty")
    slot_keys = {slot["slot_key"] for slot in slots}
    seen_outputs: set[str] = set()
    for mapping in mappings:
        _require_fields(mapping, PARENT_OUTPUT_MAPPING_FIELDS, "parent_output_mapping")
        output_name = mapping.get("parent_output_name")
        if not output_name:
            raise ValueError("parent_output_name is required")
        if output_name in seen_outputs:
            raise ValueError(f"duplicate parent output mapping: {output_name}")
        seen_outputs.add(output_name)
        if mapping.get("resolution_kind") != "merge_plan_output":
            raise ValueError("parent output mapping must use merge_plan_output")
        merge_slot_keys = mapping.get("merge_slot_keys")
        if not isinstance(merge_slot_keys, list) or not merge_slot_keys:
            raise ValueError("parent output mapping requires merge_slot_keys")
        if any(slot_key not in slot_keys for slot_key in merge_slot_keys):
            raise ValueError("parent output mapping merge_slot_keys must exist")
        if not isinstance(mapping.get("result_schema_ref"), dict):
            raise ValueError("parent output mapping result_schema_ref must be an object")


def _validate_hash_recording_requirements(requirements: JsonObject) -> None:
    _require_fields(
        requirements,
        HASH_RECORDING_REQUIREMENT_FIELDS,
        "hash_recording_requirements",
    )
    for field_name in HASH_RECORDING_REQUIREMENT_FIELDS:
        if not isinstance(requirements.get(field_name), bool):
            raise ValueError("hash recording requirements must be boolean")


def _validate_merge_validation_requirements(requirements: JsonObject) -> None:
    _require_fields(
        requirements,
        MERGE_VALIDATION_REQUIREMENT_FIELDS,
        "merge_validation_requirements",
    )
    for field_name in (
        "all_required_slots_canonical",
        "slot_schema_check_required",
        "merged_output_schema_check_required",
    ):
        if not isinstance(requirements.get(field_name), bool):
            raise ValueError("merge validation requirements must be boolean")
    if not requirements.get("plugin_merge_validator_policy_id"):
        raise ValueError("plugin_merge_validator_policy_id is required")


def _validate_merge_plugin_payload(payload: JsonObject) -> None:
    if not isinstance(payload, dict):
        raise ValueError("plugin_payload must be an object")
    if "optional_slots" in payload:
        raise ValueError("optional_slots are not supported in Phase 4 MergePlan")
    _validate_plugin_payload_no_authoritative_state(payload)
    _require_fields(payload, MERGE_PLUGIN_PAYLOAD_FIELDS, "plugin_payload")
    if not isinstance(payload.get("plugin_defined_schema_ref"), dict):
        raise ValueError("plugin_defined_schema_ref must be an object")
    if not isinstance(payload.get("plugin_defined_body"), dict):
        raise ValueError("plugin_defined_body must be an object")


def _validate_plugin_payload_no_authoritative_state(payload: JsonObject) -> None:
    blocked_keys = sorted(AUTHORITATIVE_PLUGIN_PAYLOAD_KEYS.intersection(payload))
    if blocked_keys:
        raise ValueError(
            "plugin_payload must not specify state or output resolution authority: "
            + ", ".join(blocked_keys)
        )


def _require_schema_version(actual: str, expected: str) -> None:
    if actual != expected:
        raise ValueError(f"invalid schema_version: expected {expected}, got {actual}")


def _dataclass_dict(instance: Any) -> JsonObject:
    return {
        "schema_version": instance.schema_version,
        **{
            key: _json_value(value)
            for key, value in instance.__dict__.items()
            if key != "schema_version"
        },
    }


def _validate_proposal_header(header: JsonObject) -> None:
    _require_fields(header, PROPOSAL_HEADER_REQUIRED_FIELDS, "proposal_header")
    if header.get("proposal_schema_version") != "phase4.decomposition_proposal.v1":
        raise ValueError("invalid proposal schema version")


def _validate_child_specs(child_specs: list[JsonObject]) -> None:
    seen: set[str] = set()
    for spec in child_specs:
        _require_fields(spec, CHILD_SPEC_REQUIRED_FIELDS, "child_spec")
        key = spec.get("child_logical_key")
        if not key:
            raise ValueError("child logical key is required")
        if key in seen:
            raise ValueError(f"duplicate child logical key: {key}")
        seen.add(key)
        if not isinstance(spec.get("input_bindings"), dict):
            raise ValueError("child input_bindings must be an object")
        required_outputs = spec.get("required_outputs")
        if not isinstance(required_outputs, list) or not required_outputs:
            raise ValueError("child required_outputs must be a non-empty list")
        output_contract_refs = spec.get("output_contract_refs")
        if not isinstance(output_contract_refs, dict):
            raise ValueError("child output_contract_refs must be an object")
        missing_contracts = [
            output_name
            for output_name in required_outputs
            if output_name not in output_contract_refs
        ]
        if missing_contracts:
            raise ValueError("child output_contract_refs must cover required_outputs")
        if not isinstance(spec.get("required_capabilities"), dict):
            raise ValueError("child required_capabilities must be an object")
        plugin_payload = spec.get("plugin_payload", {})
        if not isinstance(plugin_payload, dict):
            raise ValueError("child plugin_payload must be an object")
        _validate_plugin_payload_no_authoritative_state(plugin_payload)
        if spec.get("weight", 0) <= 0:
            raise ValueError("child weight must be positive")


def _validate_dependency_edges(
    child_specs: list[JsonObject], dependency_edges: list[JsonObject]
) -> None:
    child_specs_by_key = {spec["child_logical_key"]: spec for spec in child_specs}
    seen_edge_keys: set[str] = set()
    seen_target_inputs: set[tuple[str, str]] = {
        (spec["child_logical_key"], input_name)
        for spec in child_specs
        for input_name in spec.get("input_bindings", {})
    }
    out_edges: dict[str, list[str]] = {key: [] for key in child_specs_by_key}
    for edge in dependency_edges:
        _require_fields(edge, DEPENDENCY_EDGE_REQUIRED_FIELDS, "dependency_edge")
        edge_key = edge.get("edge_logical_key")
        if not edge_key:
            raise ValueError("dependency edge logical key is required")
        if edge_key in seen_edge_keys:
            raise ValueError(f"duplicate edge logical key: {edge_key}")
        seen_edge_keys.add(edge_key)
        if edge.get("relation_type") != "depends_on_output":
            raise ValueError("dependency edge relation_type must be depends_on_output")
        source_key = edge.get("source_child_key")
        target_key = edge.get("target_child_key")
        if source_key not in child_specs_by_key or target_key not in child_specs_by_key:
            raise ValueError("dependency edge source and target must exist")
        source_outputs = set(child_specs_by_key[source_key].get("required_outputs", []))
        if edge.get("source_output_name") not in source_outputs:
            raise ValueError("dependency edge source output must be declared by source child")
        if not edge.get("target_input_name"):
            raise ValueError("dependency edge target_input_name is required")
        target_input = (target_key, edge.get("target_input_name"))
        if target_input in seen_target_inputs:
            raise ValueError("duplicate target input binding")
        seen_target_inputs.add(target_input)
        out_edges[source_key].append(target_key)
    _validate_acyclic(out_edges)


def _validate_acyclic(out_edges: dict[str, list[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(key: str) -> None:
        if key in visiting:
            raise ValueError("proposal child graph contains a cycle")
        if key in visited:
            return
        visiting.add(key)
        for target_key in out_edges.get(key, []):
            visit(target_key)
        visiting.remove(key)
        visited.add(key)

    for key in out_edges:
        visit(key)


def _validate_merge_slots(child_specs: list[JsonObject], merge_slots: list[JsonObject]) -> None:
    child_specs_by_key = {spec["child_logical_key"]: spec for spec in child_specs}
    seen_slots: set[str] = set()
    for slot in merge_slots:
        _require_fields(slot, MERGE_SLOT_REQUIRED_FIELDS, "merge_slot")
        slot_id = slot.get("slot_id")
        if not slot_id:
            raise ValueError("merge slot_id is required")
        if slot_id in seen_slots:
            raise ValueError(f"duplicate merge slot: {slot_id}")
        seen_slots.add(slot_id)
        if slot.get("required") is not True:
            raise ValueError("Phase 4 merge slots must be required")
        if slot.get("missing_policy") != "block_merge":
            raise ValueError("Phase 4 merge slot missing_policy must be block_merge")
        child_key = slot.get("child_key")
        if child_key not in child_specs_by_key:
            raise ValueError("merge slot child_key must exist")
        child_outputs = set(child_specs_by_key[child_key].get("required_outputs", []))
        if slot.get("child_output_name") not in child_outputs:
            raise ValueError("merge slot child output must be declared")


def _validate_expected_outputs(
    child_specs: list[JsonObject],
    merge_slots: list[JsonObject],
    expected_outputs: list[JsonObject],
) -> None:
    if not expected_outputs:
        raise ValueError("expected_outputs must not be empty")
    child_specs_by_key = {spec["child_logical_key"]: spec for spec in child_specs}
    merge_slot_ids = {slot["slot_id"] for slot in merge_slots}
    seen_names: set[str] = set()
    for expected_output in expected_outputs:
        _require_fields(expected_output, EXPECTED_OUTPUT_REQUIRED_FIELDS, "expected_output")
        output_name = expected_output.get("output_name")
        if output_name in seen_names:
            raise ValueError(f"duplicate expected output: {output_name}")
        seen_names.add(output_name)
        resolution_kind = expected_output.get("resolution_kind")
        if resolution_kind not in {
            "direct_parent_output",
            "child_output",
            "merge_plan_output",
        }:
            raise ValueError("invalid expected output resolution_kind")
        if not isinstance(expected_output.get("required"), bool):
            raise ValueError("expected output required must be boolean")
        if not isinstance(expected_output.get("schema_ref"), dict):
            raise ValueError("expected output schema_ref must be an object")
        if resolution_kind == "child_output":
            child_key = expected_output.get("child_key")
            if child_key not in child_specs_by_key:
                raise ValueError("expected output child_key must exist")
            child_outputs = set(child_specs_by_key[child_key].get("required_outputs", []))
            if expected_output.get("child_output_name") not in child_outputs:
                raise ValueError("expected output child_output_name must be declared")
        if resolution_kind == "merge_plan_output":
            merge_slot_id = expected_output.get("merge_slot_id")
            if merge_slot_id is None:
                raise ValueError("expected_output missing required field: merge_slot_id")
            if merge_slot_id not in merge_slot_ids:
                raise ValueError("expected output merge_slot_id must exist")


def _validate_promotion_guard(guard: JsonObject) -> None:
    required_checks = (
        "typed_io_checked",
        "independently_schedulable_checked",
        "validator_policy_checked",
        "output_contract_checked",
        "no_freeform_thought_checked",
        "max_depth_checked",
        "max_children_checked",
    )
    for check_name in required_checks:
        if guard.get(check_name) is not True:
            if check_name == "no_freeform_thought_checked":
                raise ValueError("freeform thought cannot be promoted to TaskUnit")
            raise ValueError(f"promotion guard failed: {check_name}")


def _require_fields(data: JsonObject, required_fields: tuple[str, ...], context: str) -> None:
    for field_name in required_fields:
        if field_name not in data:
            raise ValueError(f"{context} missing required field: {field_name}")


def _incoming_dependency_edges(
    proposal: DecompositionProposal, child_logical_key: str
) -> list[JsonObject]:
    return [
        edge
        for edge in proposal.dependency_edges
        if edge.get("target_child_key") == child_logical_key
        and edge.get("relation_type") == "depends_on_output"
    ]
