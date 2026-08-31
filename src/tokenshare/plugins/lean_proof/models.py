"""Pure data models for the real Lean proof plugin."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from tokenshare.core.models import ArtifactRef, JsonObject
from tokenshare.plugins.lean_proof.schemas import (
    DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
    LEAN_FIXTURE_MANIFEST_SCHEMA_VERSION,
    LEAN_SPLIT_CERTIFICATE_SCHEMA_VERSION,
    LEAN_THEOREM_PAYLOAD_SCHEMA_VERSION,
    PROOF_ARTIFACT_OUTPUT_NAME,
)


LEAN_LEMMA_GRAPH_CERTIFICATE_SCHEMA_VERSION = "lean_proof.lemma_graph_certificate.v2"
LEAN_LEMMA_GRAPH_TOPIC_FAMILIES = ("pure_logic", "function_set", "induction")
LEAN_LEMMA_GRAPH_PROOF_ASSEMBLY_SHAPES = (
    "recursive_lemma_dag_required_slots.v1",
    "recursive_induction_lemma_dag_required_slots.v1",
    "structured_blocked_no_oracle_frontier_stress.v1",
)
LEAN_LEMMA_GRAPH_STRUCTURED_BLOCKED_SHAPE = (
    "structured_blocked_no_oracle_frontier_stress.v1"
)


@dataclass(frozen=True, kw_only=True)
class LeanTheoremPayload:
    theorem_id: str
    theorem_name: str
    imports: list[str]
    namespace: str | None
    open_namespaces: list[str]
    options: JsonObject
    statement_source: str
    library_context: JsonObject
    decomposition_policy: JsonObject
    resource_limits: JsonObject
    parameters_source: str = ""
    theorem_source: str | None = None
    proof_candidate_ref: JsonObject | None = None
    payload_digest: str | None = None
    schema_version: str = LEAN_THEOREM_PAYLOAD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version, LEAN_THEOREM_PAYLOAD_SCHEMA_VERSION)
        _require_non_empty("theorem_id", self.theorem_id)
        _require_lean_name("theorem_name", self.theorem_name)
        _require_non_empty_string_list("imports", self.imports)
        if self.namespace is not None:
            _require_lean_name("namespace", self.namespace)
        _require_string_list("open_namespaces", self.open_namespaces)
        for namespace in self.open_namespaces:
            _require_lean_name("open_namespaces", namespace)
        _require_json_object("options", self.options)
        if not isinstance(self.parameters_source, str):
            raise TypeError("parameters_source must be a string")
        _require_non_empty("statement_source", self.statement_source)
        if self.theorem_source is not None and not isinstance(self.theorem_source, str):
            raise TypeError("theorem_source must be a string or null")
        if self.proof_candidate_ref is not None:
            _require_json_object("proof_candidate_ref", self.proof_candidate_ref)
        _require_json_object("library_context", self.library_context)
        _require_decomposition_policy(self.decomposition_policy)
        _require_resource_limits(self.resource_limits)
        _set_or_check_digest(self, "payload_digest", self._digest_body())

    def to_dict(self) -> JsonObject:
        body = self._digest_body()
        body["payload_digest"] = self.payload_digest
        return body

    @classmethod
    def from_dict(cls, data: JsonObject) -> "LeanTheoremPayload":
        return cls(
            schema_version=data.get("schema_version", LEAN_THEOREM_PAYLOAD_SCHEMA_VERSION),
            theorem_id=data["theorem_id"],
            theorem_name=data["theorem_name"],
            imports=list(data["imports"]),
            namespace=data.get("namespace"),
            open_namespaces=list(data.get("open_namespaces", [])),
            options=dict(data["options"]),
            parameters_source=data.get("parameters_source", ""),
            statement_source=data["statement_source"],
            theorem_source=data.get("theorem_source"),
            proof_candidate_ref=data.get("proof_candidate_ref"),
            library_context=dict(data["library_context"]),
            decomposition_policy=dict(data["decomposition_policy"]),
            resource_limits=dict(data["resource_limits"]),
            payload_digest=data.get("payload_digest"),
        )

    def _digest_body(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "theorem_id": self.theorem_id,
            "theorem_name": self.theorem_name,
            "imports": list(self.imports),
            "namespace": self.namespace,
            "open_namespaces": list(self.open_namespaces),
            "options": _json_value(self.options),
            "parameters_source": self.parameters_source,
            "statement_source": self.statement_source,
            "theorem_source": self.theorem_source,
            "proof_candidate_ref": _json_value(self.proof_candidate_ref),
            "library_context": _json_value(self.library_context),
            "decomposition_policy": _json_value(self.decomposition_policy),
            "resource_limits": _json_value(self.resource_limits),
        }


@dataclass(frozen=True, kw_only=True)
class LeanFixtureManifest:
    project_root: str
    toolchain_file: str
    toolchain_file_digest: str
    lakefile: str
    lakefile_digest: str
    helper_sources: dict[str, str]
    helper_sources_digest: str
    fixture_cases: dict[str, JsonObject]
    manifest_digest: str | None = None
    schema_version: str = LEAN_FIXTURE_MANIFEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version, LEAN_FIXTURE_MANIFEST_SCHEMA_VERSION)
        _require_non_empty("project_root", self.project_root)
        _require_non_empty("toolchain_file", self.toolchain_file)
        _require_digest("toolchain_file_digest", self.toolchain_file_digest)
        _require_non_empty("lakefile", self.lakefile)
        _require_digest("lakefile_digest", self.lakefile_digest)
        if not isinstance(self.helper_sources, dict) or not self.helper_sources:
            raise ValueError("helper_sources must be a non-empty object")
        for path, digest in self.helper_sources.items():
            _require_non_empty("helper_sources key", path)
            _require_digest("helper_sources digest", digest)
        _require_digest("helper_sources_digest", self.helper_sources_digest)
        if not isinstance(self.fixture_cases, dict) or not self.fixture_cases:
            raise ValueError("fixture_cases must be a non-empty object")
        _set_or_check_digest(self, "manifest_digest", self._digest_body())

    def to_dict(self) -> JsonObject:
        body = self._digest_body()
        body["manifest_digest"] = self.manifest_digest
        return body

    @classmethod
    def from_dict(cls, data: JsonObject) -> "LeanFixtureManifest":
        return cls(
            schema_version=data.get(
                "schema_version",
                LEAN_FIXTURE_MANIFEST_SCHEMA_VERSION,
            ),
            project_root=data["project_root"],
            toolchain_file=data["toolchain_file"],
            toolchain_file_digest=data["toolchain_file_digest"],
            lakefile=data["lakefile"],
            lakefile_digest=data["lakefile_digest"],
            helper_sources=dict(data["helper_sources"]),
            helper_sources_digest=data["helper_sources_digest"],
            fixture_cases={str(key): dict(value) for key, value in data["fixture_cases"].items()},
            manifest_digest=data.get("manifest_digest"),
        )

    def _digest_body(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "project_root": self.project_root,
            "toolchain_file": self.toolchain_file,
            "toolchain_file_digest": self.toolchain_file_digest,
            "lakefile": self.lakefile,
            "lakefile_digest": self.lakefile_digest,
            "helper_sources": dict(sorted(self.helper_sources.items())),
            "helper_sources_digest": self.helper_sources_digest,
            "fixture_cases": _json_value(self.fixture_cases),
        }


@dataclass(frozen=True, kw_only=True)
class LeanSplitCertificate:
    split_certificate_id: str
    parent_theorem_payload_ref: ArtifactRef | None
    normalized_parent_goal_digest: str
    policy_id: str
    rule_id: str
    rule_trace: list[JsonObject]
    split_kind: str
    child_goals: list[JsonObject]
    merge_skeleton: JsonObject | None
    unsupported_reason: str | None
    helper_stdout_ref: ArtifactRef | None
    helper_stderr_ref: ArtifactRef | None
    diagnostics: JsonObject
    certificate_digest: str | None = None
    schema_version: str = LEAN_SPLIT_CERTIFICATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version, LEAN_SPLIT_CERTIFICATE_SCHEMA_VERSION)
        _require_non_empty("split_certificate_id", self.split_certificate_id)
        _require_digest("normalized_parent_goal_digest", self.normalized_parent_goal_digest)
        if self.policy_id != DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID:
            raise ValueError("policy_id must be lean_proof.deterministic_tactic_split.v1")
        _require_non_empty("rule_id", self.rule_id)
        if not isinstance(self.rule_trace, list) or any(
            not isinstance(item, dict) for item in self.rule_trace
        ):
            raise ValueError("rule_trace must be a list of objects")
        if self.split_kind not in {
            "complete",
            "single_child",
            "all_required_children",
            "unsupported",
        }:
            raise ValueError(f"invalid split_kind: {self.split_kind}")
        _require_json_object("diagnostics", self.diagnostics)
        _validate_child_goals(self.child_goals, self.split_kind)
        if self.split_kind == "unsupported":
            _require_non_empty("unsupported_reason", self.unsupported_reason or "")
        elif self.merge_skeleton is None:
            raise ValueError("supported split certificate requires merge_skeleton")
        if self.merge_skeleton is not None:
            _require_json_object("merge_skeleton", self.merge_skeleton)
        _set_or_check_digest(self, "certificate_digest", self._digest_body())

    def to_dict(self) -> JsonObject:
        body = self._digest_body()
        body["certificate_digest"] = self.certificate_digest
        return body

    @classmethod
    def from_dict(cls, data: JsonObject) -> "LeanSplitCertificate":
        return cls(
            schema_version=data.get(
                "schema_version",
                LEAN_SPLIT_CERTIFICATE_SCHEMA_VERSION,
            ),
            split_certificate_id=data["split_certificate_id"],
            parent_theorem_payload_ref=_artifact_ref_or_none(
                data.get("parent_theorem_payload_ref")
            ),
            normalized_parent_goal_digest=data["normalized_parent_goal_digest"],
            policy_id=data["policy_id"],
            rule_id=data["rule_id"],
            rule_trace=[dict(item) for item in data.get("rule_trace", [])],
            split_kind=data["split_kind"],
            child_goals=[dict(item) for item in data.get("child_goals", [])],
            merge_skeleton=(
                dict(data["merge_skeleton"]) if data.get("merge_skeleton") is not None else None
            ),
            unsupported_reason=data.get("unsupported_reason"),
            helper_stdout_ref=_artifact_ref_or_none(data.get("helper_stdout_ref")),
            helper_stderr_ref=_artifact_ref_or_none(data.get("helper_stderr_ref")),
            diagnostics=dict(data.get("diagnostics", {})),
            certificate_digest=data.get("certificate_digest"),
        )

    def _digest_body(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "split_certificate_id": self.split_certificate_id,
            "parent_theorem_payload_ref": _json_value(self.parent_theorem_payload_ref),
            "normalized_parent_goal_digest": self.normalized_parent_goal_digest,
            "policy_id": self.policy_id,
            "rule_id": self.rule_id,
            "rule_trace": _json_value(self.rule_trace),
            "split_kind": self.split_kind,
            "child_goals": _json_value(self.child_goals),
            "merge_skeleton": _json_value(self.merge_skeleton),
            "unsupported_reason": self.unsupported_reason,
            "helper_stdout_ref": _json_value(self.helper_stdout_ref),
            "helper_stderr_ref": _json_value(self.helper_stderr_ref),
            "diagnostics": _json_value(self.diagnostics),
        }


@dataclass(frozen=True, kw_only=True)
class LeanLemmaGraphCertificate:
    certificate_id: str
    parent_theorem_payload_ref: ArtifactRef | None
    normalized_parent_goal_digest: str
    policy_id: str
    rule_id: str
    topic_family: str
    topic_family_version: str
    construction_rule_id: str
    oracle_package_group: str
    proof_assembly_shape: str
    root_node_id: str
    lemma_nodes: list[JsonObject]
    dependency_edges: list[JsonObject]
    merge_nodes: list[JsonObject]
    environment_digest: str
    oracle_proof_package_ref: JsonObject | None
    oracle_proof_package_digest: str | None
    diagnostics: JsonObject
    certificate_digest: str | None = None
    certificate_schema_version: str = LEAN_LEMMA_GRAPH_CERTIFICATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(
            self.certificate_schema_version,
            LEAN_LEMMA_GRAPH_CERTIFICATE_SCHEMA_VERSION,
        )
        _require_non_empty("certificate_id", self.certificate_id)
        _require_digest("normalized_parent_goal_digest", self.normalized_parent_goal_digest)
        if self.policy_id != DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID:
            raise ValueError("policy_id must be lean_proof.deterministic_tactic_split.v1")
        _require_non_empty("rule_id", self.rule_id)
        _require_topic_family(self.topic_family)
        _require_non_empty("topic_family_version", self.topic_family_version)
        _require_non_empty("construction_rule_id", self.construction_rule_id)
        _require_non_empty("oracle_package_group", self.oracle_package_group)
        _require_proof_assembly_shape(self.proof_assembly_shape)
        _require_non_empty("root_node_id", self.root_node_id)
        _require_digest("environment_digest", self.environment_digest)
        if (
            self.proof_assembly_shape == LEAN_LEMMA_GRAPH_STRUCTURED_BLOCKED_SHAPE
            and self.oracle_proof_package_ref is not None
        ):
            raise ValueError(
                "structured_blocked_no_oracle_frontier_stress.v1 cannot carry "
                "oracle_proof_package_ref"
            )
        if self.oracle_proof_package_ref is not None:
            _require_json_object("oracle_proof_package_ref", self.oracle_proof_package_ref)
            if self.oracle_proof_package_digest is None:
                raise ValueError("oracle_proof_package_digest is required with oracle_proof_package_ref")
            _require_digest("oracle_proof_package_digest", self.oracle_proof_package_digest)
            ref_digest = self.oracle_proof_package_ref.get("content_hash")
            if ref_digest is not None and ref_digest != self.oracle_proof_package_digest:
                raise ValueError("oracle_proof_package_digest must match oracle_proof_package_ref")
        elif self.oracle_proof_package_digest is not None:
            raise ValueError("oracle_proof_package_digest requires oracle_proof_package_ref")
        _require_json_object("diagnostics", self.diagnostics)
        _validate_lemma_graph_nodes(self.lemma_nodes, self.root_node_id)
        _validate_lemma_graph_dependency_edges(self.lemma_nodes, self.dependency_edges)
        _validate_lemma_graph_merge_nodes(self.lemma_nodes, self.merge_nodes)
        _set_or_check_digest(self, "certificate_digest", self._digest_body())

    def to_dict(self) -> JsonObject:
        body = self._digest_body()
        body["certificate_digest"] = self.certificate_digest
        return body

    @classmethod
    def from_dict(
        cls,
        data: JsonObject,
        *,
        expected_environment_digest: str | None = None,
    ) -> "LeanLemmaGraphCertificate":
        _require_fields(
            data,
            (
                "certificate_id",
                "normalized_parent_goal_digest",
                "policy_id",
                "rule_id",
                "topic_family",
                "topic_family_version",
                "construction_rule_id",
                "oracle_package_group",
                "proof_assembly_shape",
                "root_node_id",
                "lemma_nodes",
                "dependency_edges",
                "environment_digest",
            ),
            "lemma graph certificate",
        )
        certificate = cls(
            certificate_schema_version=data.get(
                "certificate_schema_version",
                LEAN_LEMMA_GRAPH_CERTIFICATE_SCHEMA_VERSION,
            ),
            certificate_id=data["certificate_id"],
            parent_theorem_payload_ref=_artifact_ref_or_none(
                data.get("parent_theorem_payload_ref")
            ),
            normalized_parent_goal_digest=data["normalized_parent_goal_digest"],
            policy_id=data["policy_id"],
            rule_id=data["rule_id"],
            topic_family=data["topic_family"],
            topic_family_version=data["topic_family_version"],
            construction_rule_id=data["construction_rule_id"],
            oracle_package_group=data["oracle_package_group"],
            proof_assembly_shape=data["proof_assembly_shape"],
            root_node_id=data["root_node_id"],
            lemma_nodes=[dict(item) for item in data.get("lemma_nodes", [])],
            dependency_edges=[dict(item) for item in data.get("dependency_edges", [])],
            merge_nodes=[dict(item) for item in data.get("merge_nodes", [])],
            environment_digest=data["environment_digest"],
            oracle_proof_package_ref=(
                dict(data["oracle_proof_package_ref"])
                if data.get("oracle_proof_package_ref") is not None
                else None
            ),
            oracle_proof_package_digest=data.get("oracle_proof_package_digest"),
            diagnostics=dict(data.get("diagnostics", {})),
            certificate_digest=data.get("certificate_digest"),
        )
        if (
            expected_environment_digest is not None
            and certificate.environment_digest != expected_environment_digest
        ):
            raise ValueError("environment_digest does not match expected environment")
        return certificate

    @property
    def lemma_nodes_by_id(self) -> dict[str, JsonObject]:
        return {str(node["node_id"]): node for node in self.lemma_nodes}

    def _digest_body(self) -> JsonObject:
        return {
            "certificate_schema_version": self.certificate_schema_version,
            "certificate_id": self.certificate_id,
            "parent_theorem_payload_ref": _json_value(self.parent_theorem_payload_ref),
            "normalized_parent_goal_digest": self.normalized_parent_goal_digest,
            "policy_id": self.policy_id,
            "rule_id": self.rule_id,
            "topic_family": self.topic_family,
            "topic_family_version": self.topic_family_version,
            "construction_rule_id": self.construction_rule_id,
            "oracle_package_group": self.oracle_package_group,
            "proof_assembly_shape": self.proof_assembly_shape,
            "root_node_id": self.root_node_id,
            "lemma_nodes": _json_value(self.lemma_nodes),
            "dependency_edges": _json_value(self.dependency_edges),
            "merge_nodes": _json_value(self.merge_nodes),
            "environment_digest": self.environment_digest,
            "oracle_proof_package_ref": _json_value(self.oracle_proof_package_ref),
            "oracle_proof_package_digest": self.oracle_proof_package_digest,
            "diagnostics": _json_value(self.diagnostics),
        }


def canonical_json_digest(data: Any) -> str:
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return f"sha256:{sha256(encoded).hexdigest()}"


def _set_or_check_digest(instance: object, field_name: str, data: Any) -> None:
    expected = canonical_json_digest(data)
    current = getattr(instance, field_name)
    if current is None:
        object.__setattr__(instance, field_name, expected)
        return
    if current != expected:
        raise ValueError(f"{field_name} must match canonical JSON digest")


def _require_schema(actual: str, expected: str) -> None:
    if actual != expected:
        raise ValueError(f"schema_version must be {expected}")


def _require_non_empty(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_lean_name(field_name: str, value: str) -> None:
    _require_non_empty(field_name, value)
    parts = value.split(".")
    if any(not part or not part.replace("_", "a").isalnum() or part[0].isdigit() for part in parts):
        raise ValueError(f"{field_name} must be a Lean identifier path")


def _require_string_list(field_name: str, value: list[str]) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a list of strings")


def _require_non_empty_string_list(field_name: str, value: list[str]) -> None:
    _require_string_list(field_name, value)
    if not value or any(not item for item in value):
        raise ValueError(f"{field_name} must be a non-empty list of non-empty strings")


def _require_json_object(field_name: str, value: JsonObject) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object")


def _require_decomposition_policy(value: JsonObject) -> None:
    _require_json_object("decomposition_policy", value)
    allowed_rules = value.get("allowed_rules")
    if not isinstance(allowed_rules, list) or any(not isinstance(item, str) for item in allowed_rules):
        raise ValueError("decomposition_policy.allowed_rules must be a list of strings")
    for key in ("max_depth", "max_children"):
        item = value.get(key)
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise ValueError(f"decomposition_policy.{key} must be a non-negative integer")


def _require_resource_limits(value: JsonObject) -> None:
    _require_json_object("resource_limits", value)
    for key in ("timeout_seconds", "max_output_bytes"):
        item = value.get(key)
        if isinstance(item, bool) or not isinstance(item, int) or item < 1:
            raise ValueError(f"resource_limits.{key} must be a positive integer")


def _require_digest(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        raise ValueError(f"{field_name} must be a sha256 digest")


def _require_topic_family(value: str) -> None:
    if value not in LEAN_LEMMA_GRAPH_TOPIC_FAMILIES:
        allowed = ", ".join(LEAN_LEMMA_GRAPH_TOPIC_FAMILIES)
        raise ValueError(f"topic_family must be one of: {allowed}")


def _require_proof_assembly_shape(value: str) -> None:
    if value not in LEAN_LEMMA_GRAPH_PROOF_ASSEMBLY_SHAPES:
        allowed = ", ".join(LEAN_LEMMA_GRAPH_PROOF_ASSEMBLY_SHAPES)
        raise ValueError(f"proof_assembly_shape must be one of: {allowed}")


def _artifact_ref_or_none(value: Any) -> ArtifactRef | None:
    if value is None:
        return None
    if isinstance(value, ArtifactRef):
        return value
    if isinstance(value, dict):
        return ArtifactRef.from_dict(value)
    raise ValueError("artifact ref field must be an object or null")


def _validate_child_goals(child_goals: list[JsonObject], split_kind: str) -> None:
    if not isinstance(child_goals, list) or any(not isinstance(item, dict) for item in child_goals):
        raise ValueError("child_goals must be a list of objects")
    if split_kind in {"single_child", "all_required_children"} and not child_goals:
        raise ValueError("supported split certificate requires child_goals")
    if split_kind in {"complete", "unsupported"} and child_goals:
        raise ValueError(f"{split_kind} split certificate cannot carry child_goals")
    seen: set[str] = set()
    required = {
        "child_logical_key",
        "theorem_name",
        "parameters_source",
        "statement_source",
        "context_digest",
        "child_payload_digest",
        "required_output_name",
    }
    for child in child_goals:
        missing = sorted(required.difference(child))
        if missing:
            raise ValueError("child goal missing required field: " + ", ".join(missing))
        key = child["child_logical_key"]
        if not isinstance(key, str) or not key:
            raise ValueError("child_logical_key must be a non-empty string")
        if key in seen:
            raise ValueError(f"duplicate child_logical_key: {key}")
        seen.add(key)
        _require_lean_name("child theorem_name", child["theorem_name"])
        if not isinstance(child["parameters_source"], str):
            raise ValueError("parameters_source must be a string")
        _require_non_empty("statement_source", child["statement_source"])
        _require_digest("context_digest", child["context_digest"])
        _require_digest("child_payload_digest", child["child_payload_digest"])
        if child["required_output_name"] != PROOF_ARTIFACT_OUTPUT_NAME:
            raise ValueError("child required_output_name must be lean_proof_artifact")


def _validate_lemma_graph_nodes(nodes: list[JsonObject], root_node_id: str) -> None:
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("lemma_nodes must be a non-empty list")
    seen: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError("lemma_nodes must be a list of objects")
        _require_fields(
            node,
            (
                "node_id",
                "node_kind",
                "depth",
                "required_output_name",
                "context_digest",
                "theorem_payload",
            ),
            "lemma_node",
        )
        node_id = node["node_id"]
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("node_id must be a non-empty string")
        if node_id in seen:
            raise ValueError(f"duplicate node_id: {node_id}")
        seen.add(node_id)
        _require_non_empty("node_kind", node["node_kind"])
        if type(node["depth"]) is not int or node["depth"] < 0:
            raise ValueError("lemma node depth must be a non-negative integer")
        if node["required_output_name"] != PROOF_ARTIFACT_OUTPUT_NAME:
            raise ValueError("lemma node required_output_name must be lean_proof_artifact")
        _require_digest("lemma node context_digest", node["context_digest"])
        _require_json_object("lemma node theorem_payload", node["theorem_payload"])
    if root_node_id not in seen:
        raise ValueError("root_node_id must reference a lemma node")


def _validate_lemma_graph_dependency_edges(
    nodes: list[JsonObject],
    edges: list[JsonObject],
) -> None:
    if not isinstance(edges, list):
        raise ValueError("dependency_edges must be a list")
    node_ids = {str(node["node_id"]) for node in nodes}
    seen_edges: set[tuple[str, str]] = set()
    out_edges: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError("dependency_edges must be a list of objects")
        _require_fields(edge, ("source_node_id", "target_node_id"), "dependency edge")
        source = edge["source_node_id"]
        target = edge["target_node_id"]
        if not isinstance(source, str) or not isinstance(target, str) or not source or not target:
            raise ValueError("dependency edge source_node_id and target_node_id are required")
        if source not in node_ids or target not in node_ids:
            raise ValueError("dependency edge source and target must reference existing nodes")
        edge_key = (source, target)
        if edge_key in seen_edges:
            raise ValueError(f"duplicate dependency edge: {source}->{target}")
        seen_edges.add(edge_key)
        out_edges[source].append(target)
    _validate_acyclic_node_graph(out_edges)


def _validate_lemma_graph_merge_nodes(
    nodes: list[JsonObject],
    merge_nodes: list[JsonObject],
) -> None:
    if not isinstance(merge_nodes, list):
        raise ValueError("merge_nodes must be a list")
    node_ids = {str(node["node_id"]) for node in nodes}
    seen: set[str] = set()
    for merge_node in merge_nodes:
        if not isinstance(merge_node, dict):
            raise ValueError("merge_nodes must be a list of objects")
        _require_fields(merge_node, ("node_id",), "merge_node")
        node_id = merge_node["node_id"]
        if node_id not in node_ids:
            raise ValueError("merge_node node_id must reference an existing node")
        if node_id in seen:
            raise ValueError(f"duplicate merge node: {node_id}")
        seen.add(node_id)
        required_inputs = merge_node.get("required_input_node_ids", [])
        if not isinstance(required_inputs, list) or any(
            not isinstance(item, str) for item in required_inputs
        ):
            raise ValueError("merge_node required_input_node_ids must be a list of strings")
        missing_inputs = sorted(set(required_inputs).difference(node_ids))
        if missing_inputs:
            raise ValueError(
                "merge_node required_input_node_ids must reference existing nodes: "
                + ", ".join(missing_inputs)
            )


def _validate_acyclic_node_graph(out_edges: dict[str, list[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise ValueError("lemma graph dependency edges contain a cycle")
        if node_id in visited:
            return
        visiting.add(node_id)
        for target_id in out_edges.get(node_id, []):
            visit(target_id)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in out_edges:
        visit(node_id)


def _require_fields(data: JsonObject, required_fields: tuple[str, ...], context: str) -> None:
    missing = sorted(set(required_fields).difference(data))
    if missing:
        raise ValueError(f"{context} missing required field: " + ", ".join(missing))


def _json_value(value: Any) -> Any:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value
