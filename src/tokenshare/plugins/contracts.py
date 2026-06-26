"""Phase 3 plugin-side protocol contracts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from tokenshare.core.models import ArtifactRef, JsonObject


@dataclass(frozen=True)
class OutputContract:
    """Execution output shape requested from an executor.

    Phase 3 only states what must be produced. It does not validate whether the
    returned candidate is correct; that belongs to Phase 4 verification.
    """

    output_contract_id: str
    required_outputs: list[str]
    output_schema_refs: dict[str, JsonObject]
    raw_output_policy: JsonObject
    optional_outputs: list[str] | None = None
    parsed_output_schema_ref: JsonObject | None = None
    candidate_bundle_schema_ref: JsonObject | None = None
    parse_failure_schema_ref: JsonObject | None = None
    schema_version: str = "phase3.output_contract.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "output_contract_id": self.output_contract_id,
            "required_outputs": list(self.required_outputs),
            "optional_outputs": list(self.optional_outputs or []),
            "output_schema_refs": _json_value(self.output_schema_refs),
            "raw_output_policy": _json_value(self.raw_output_policy),
            "parsed_output_schema_ref": _json_value(self.parsed_output_schema_ref),
            "candidate_bundle_schema_ref": _json_value(self.candidate_bundle_schema_ref),
            "parse_failure_schema_ref": _json_value(self.parse_failure_schema_ref),
        }


@dataclass(frozen=True)
class SplitStrategyContract:
    """Versioned plugin-owned split strategy declaration.

    The descriptor declares which strategy IDs a TaskSpec may reference. Runtime
    candidates from executors can only be inputs to these plugin-owned rules.
    """

    split_strategy_id: str
    params_schema_ref: JsonObject
    allowed_unit_types: list[str]
    child_input_port_schema_refs: dict[str, JsonObject]
    child_output_contract_refs: dict[str, JsonObject]
    validator_policy_id: str
    merge_policy_id: str
    durable_subgoal_policy: JsonObject
    candidate_artifact_policy: JsonObject
    max_children_per_expansion: int | None = None
    schema_version: str = "phase3.split_strategy_contract.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "split_strategy_id": self.split_strategy_id,
            "params_schema_ref": _json_value(self.params_schema_ref),
            "allowed_unit_types": list(self.allowed_unit_types),
            "child_input_port_schema_refs": _json_value(self.child_input_port_schema_refs),
            "child_output_contract_refs": _json_value(self.child_output_contract_refs),
            "validator_policy_id": self.validator_policy_id,
            "merge_policy_id": self.merge_policy_id,
            "durable_subgoal_policy": _json_value(self.durable_subgoal_policy),
            "candidate_artifact_policy": _json_value(self.candidate_artifact_policy),
            "max_children_per_expansion": self.max_children_per_expansion,
        }


@dataclass(frozen=True)
class PluginDescriptor:
    """Versioned plugin capability descriptor.

    The descriptor is stored as an artifact before a run is frozen. Registry
    snapshots and execution requests carry only refs, digests, and summaries.
    """

    plugin_id: str
    plugin_version: str
    supported_task_types: list[str]
    input_contract: JsonObject
    output_contracts: dict[str, OutputContract]
    execution_contracts: dict[str, JsonObject]
    split_strategies: dict[str, SplitStrategyContract] | None = None
    validator_policy_id: str | None = None
    merge_policy_id: str | None = None
    metadata: JsonObject | None = None
    schema_version: str = "phase3.plugin_descriptor.v1"

    @property
    def descriptor_digest(self) -> str:
        return _sha256_json(self._body(include_digest=False))

    def to_dict(self) -> JsonObject:
        return self._body(include_digest=True)

    def _body(self, *, include_digest: bool) -> JsonObject:
        body = {
            "schema_version": self.schema_version,
            "plugin_id": self.plugin_id,
            "plugin_version": self.plugin_version,
            "supported_task_types": list(self.supported_task_types),
            "input_contract": _json_value(self.input_contract),
            "output_contracts": {
                name: contract.to_dict() for name, contract in self.output_contracts.items()
            },
            "execution_contracts": _json_value(self.execution_contracts),
            "split_strategies": {
                strategy_id: strategy.to_dict()
                for strategy_id, strategy in (self.split_strategies or {}).items()
            },
            "validator_policy_id": self.validator_policy_id,
            "merge_policy_id": self.merge_policy_id,
            "metadata": _json_value(self.metadata or {}),
        }
        if include_digest:
            body["descriptor_digest"] = self.descriptor_digest
        return body


def _json_value(value: Any) -> Any:
    if isinstance(value, ArtifactRef):
        return value.to_dict()
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


def _sha256_json(data: JsonObject) -> str:
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return f"sha256:{sha256(encoded).hexdigest()}"
