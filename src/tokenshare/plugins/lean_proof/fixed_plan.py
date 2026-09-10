"""预注册 Lean lemma-DAG 的纯插件校验与 certificate 构造。"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

from tokenshare.core.models import ArtifactRef, JsonObject
from tokenshare.plugins.lean_proof.environment import (
    LEAN_ENVIRONMENT_AUTHORITY_BRIDGE_SCHEMA_VERSION,
    LeanEnvironmentManifest,
    build_lean_environment_ref,
)
from tokenshare.plugins.lean_proof.models import (
    LEAN_LEMMA_GRAPH_CHECKED_BODIES_SHAPE,
    LEAN_LEMMA_GRAPH_STRUCTURED_BLOCKED_SHAPE,
    LeanLemmaGraphCertificate,
    LeanTheoremPayload,
    canonical_json_digest,
)
from tokenshare.plugins.lean_proof.schemas import (
    DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
    PROOF_ARTIFACT_OUTPUT_NAME,
)
from tokenshare.plugins.lean_proof.semantic_authority import (
    LEAN_SEMANTIC_AUTHORITY_SCHEMA_VERSION,
)


LEAN_FIXED_DECOMPOSITION_PLAN_SCHEMA_VERSION = (
    "lean_proof.fixed_decomposition_plan.v1"
)
LEAN_LEMMA_GRAPH_CASE_SCHEMA_VERSION = (
    "tokenshare.paper_lean_lemma_graph_case.v1"
)
LEAN_LEMMA_GRAPH_RULE_ID = "lean_split.lemma_graph_dag.v2"


@dataclass(frozen=True, kw_only=True)
class LeanFixedDecompositionPlan:
    """Catalog 预写 plan 的规范化插件对象；它不包含 AI 拆分决定。"""

    plan_id: str
    case_id: str
    paper_difficulty: str
    topic_family: str
    topic_family_version: str
    construction_rule_id: str
    oracle_package_group: str
    proof_assembly_shape: str
    root_theorem_payload_body: JsonObject
    lemma_nodes: list[JsonObject]
    dependency_edges: list[JsonObject]
    merge_plan_shape: JsonObject
    expected_depth: int
    expected_leaf_count: int
    expected_ai_unit_count: int
    environment_digest: str
    preflight_status: str
    oracle_proof_package_ref: JsonObject | None
    plan_digest: str | None = None
    schema_version: str = LEAN_FIXED_DECOMPOSITION_PLAN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != LEAN_FIXED_DECOMPOSITION_PLAN_SCHEMA_VERSION:
            raise ValueError("Lean fixed decomposition plan schema_version mismatch")
        for name in (
            "plan_id",
            "case_id",
            "paper_difficulty",
            "topic_family",
            "topic_family_version",
            "construction_rule_id",
            "oracle_package_group",
            "proof_assembly_shape",
            "environment_digest",
            "preflight_status",
        ):
            _require_non_empty(name, getattr(self, name))
        _require_digest("environment_digest", self.environment_digest)
        _require_object("root_theorem_payload", self.root_theorem_payload_body)
        _require_object("merge_plan_shape", self.merge_plan_shape)
        for name in (
            "expected_depth",
            "expected_leaf_count",
            "expected_ai_unit_count",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        _validate_graph(self)
        _validate_limits(self)
        _validate_oracle(self)
        expected = canonical_json_digest(self._digest_body())
        if self.plan_digest is None:
            object.__setattr__(self, "plan_digest", expected)
        elif self.plan_digest != expected:
            raise ValueError("plan_digest must match canonical JSON digest")

    @classmethod
    def from_catalog_case(cls, case: JsonObject) -> "LeanFixedDecompositionPlan":
        if not isinstance(case, dict):
            raise TypeError("Lean fixed decomposition plan requires a catalog object")
        if case.get("schema_version") != LEAN_LEMMA_GRAPH_CASE_SCHEMA_VERSION:
            raise ValueError("Lean fixed decomposition plan catalog schema mismatch")
        if case.get("domain") != "lean_proof":
            raise ValueError("Lean fixed decomposition plan domain must be lean_proof")
        _require_fields(
            case,
            (
                "case_id",
                "paper_difficulty",
                "topic_family",
                "topic_family_version",
                "construction_rule_id",
                "oracle_package_group",
                "proof_assembly_shape",
                "root_theorem_payload",
                "lemma_graph",
                "dependency_edges",
                "merge_plan_shape",
                "expected_depth",
                "expected_leaf_count",
                "expected_ai_unit_count",
                "environment_digest",
                "preflight_status",
            ),
            "Lean fixed decomposition catalog case",
        )
        lemma_graph = case["lemma_graph"]
        _require_object("lemma_graph", lemma_graph)
        nodes = lemma_graph.get("nodes")
        if not isinstance(nodes, list):
            raise ValueError("lemma_graph.nodes must be a list")
        return cls(
            plan_id=f"lean_fixed_plan:{case['case_id']}",
            case_id=str(case["case_id"]),
            paper_difficulty=str(case["paper_difficulty"]),
            topic_family=str(case["topic_family"]),
            topic_family_version=str(case["topic_family_version"]),
            construction_rule_id=str(case["construction_rule_id"]),
            oracle_package_group=str(case["oracle_package_group"]),
            proof_assembly_shape=str(case["proof_assembly_shape"]),
            root_theorem_payload_body=copy.deepcopy(case["root_theorem_payload"]),
            lemma_nodes=copy.deepcopy(nodes),
            dependency_edges=copy.deepcopy(case["dependency_edges"]),
            merge_plan_shape=copy.deepcopy(case["merge_plan_shape"]),
            expected_depth=case["expected_depth"],
            expected_leaf_count=case["expected_leaf_count"],
            expected_ai_unit_count=case["expected_ai_unit_count"],
            environment_digest=str(case["environment_digest"]),
            preflight_status=str(case["preflight_status"]),
            oracle_proof_package_ref=copy.deepcopy(
                case.get("oracle_proof_package_ref")
            ),
        )

    @property
    def root_node_id(self) -> str:
        return str(self.merge_plan_shape["root_node_id"])

    @property
    def node_ids(self) -> tuple[str, ...]:
        return tuple(str(node["node_id"]) for node in self.lemma_nodes)

    def parent_theorem_payload(self) -> LeanTheoremPayload:
        return _payload_from_body(
            self.root_theorem_payload_body,
            case_id=self.case_id,
            node_id=self.root_node_id,
        )

    def node_theorem_payload(self, node_id: str) -> LeanTheoremPayload:
        for node in self.lemma_nodes:
            if node.get("node_id") == node_id:
                body = copy.deepcopy(node["theorem_payload"])
                if self.proof_assembly_shape == LEAN_LEMMA_GRAPH_CHECKED_BODIES_SHAPE:
                    base = self.root_theorem_payload_body.get("parameters_source", "")
                    if body.get("parameters_source", "") != base:
                        raise ValueError("mathematical DAG node parameter context differs from root")
                    if (node_id == self.root_node_id and body["statement_source"]
                            != self.root_theorem_payload_body["statement_source"]):
                        raise ValueError("mathematical DAG root statement differs from original theorem")
                    for field in ("imports", "open_namespaces", "namespace", "options"):
                        if body.get(field) != self.root_theorem_payload_body.get(field):
                            raise ValueError("mathematical DAG node import context differs from root")
                    if body.get("theorem_source") or body.get("proof_candidate_ref"):
                        raise ValueError("mathematical DAG payload cannot contain answer sources")
                    for item in self.lemma_nodes:
                        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(item["node_id"])):
                            raise ValueError("mathematical DAG node IDs must be Lean-safe ASCII identifiers")
                    incoming = _incoming_by_target(self.node_ids, self.dependency_edges)
                    by_id = {item["node_id"]: item for item in self.lemma_nodes}
                    bindings = [
                        f"(node_{source} : {by_id[source]['theorem_payload']['statement_source']})"
                        for source in incoming[node_id]
                    ]
                    body["parameters_source"] = " ".join([base, *bindings]).strip()
                    body.pop("payload_digest", None)
                return _payload_from_body(
                    body,
                    case_id=self.case_id,
                    node_id=node_id,
                )
        raise ValueError(f"unknown Lean fixed-plan node: {node_id}")

    def topological_order(self) -> tuple[str, ...]:
        return tuple(str(item) for item in self.merge_plan_shape["dependency_order"])

    def dependency_path(self, node_id: str) -> tuple[str, ...]:
        if node_id not in self.node_ids:
            raise ValueError(f"unknown Lean fixed-plan node: {node_id}")
        incoming = _incoming_by_target(self.node_ids, self.dependency_edges)
        path: list[str] = []
        seen: set[str] = set()

        def visit(current: str) -> None:
            if current in seen:
                return
            for source in incoming[current]:
                visit(source)
            seen.add(current)
            path.append(current)

        visit(node_id)
        return tuple(path)

    def to_dict(self) -> JsonObject:
        body = self._digest_body()
        body["plan_digest"] = self.plan_digest
        return body

    def _digest_body(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "case_id": self.case_id,
            "paper_difficulty": self.paper_difficulty,
            "topic_family": self.topic_family,
            "topic_family_version": self.topic_family_version,
            "construction_rule_id": self.construction_rule_id,
            "oracle_package_group": self.oracle_package_group,
            "proof_assembly_shape": self.proof_assembly_shape,
            "root_theorem_payload": copy.deepcopy(
                self.root_theorem_payload_body
            ),
            "lemma_nodes": copy.deepcopy(self.lemma_nodes),
            "dependency_edges": copy.deepcopy(self.dependency_edges),
            "merge_plan_shape": copy.deepcopy(self.merge_plan_shape),
            "expected_depth": self.expected_depth,
            "expected_leaf_count": self.expected_leaf_count,
            "expected_ai_unit_count": self.expected_ai_unit_count,
            "environment_digest": self.environment_digest,
            "preflight_status": self.preflight_status,
            "oracle_proof_package_ref": copy.deepcopy(
                self.oracle_proof_package_ref
            ),
        }


def checked_plan_environment_digest(manifest: LeanEnvironmentManifest) -> str:
    """Bind mathematical plans to locked content and tools, independent of checkout path."""
    return canonical_json_digest({
        "schema_version": "lean_proof.checked_plan_environment.v1",
        **{name: getattr(manifest, name) for name in (
            "lean_version", "lake_version", "toolchain_file_digest", "lakefile_digest",
            "import_set_digest", "helper_sources_digest", "resource_limits",
        )},
    })


def build_fixed_plan_certificate(
    *,
    plan: LeanFixedDecompositionPlan,
    parent_theorem_payload_ref: ArtifactRef,
    environment_manifest: LeanEnvironmentManifest,
) -> LeanLemmaGraphCertificate:
    """由插件校验后的固定 plan 生成既有 v2 lemma-DAG certificate。"""

    certificate_environment_digest = str(environment_manifest.environment_digest)
    environment_authority_bridge: JsonObject | None = None
    if plan.proof_assembly_shape == LEAN_LEMMA_GRAPH_CHECKED_BODIES_SHAPE:
        if plan.environment_digest != checked_plan_environment_digest(environment_manifest):
            raise ValueError("mathematical fixed plan locked environment digest mismatch")
    elif plan.environment_digest != certificate_environment_digest:
        environment_ref = build_lean_environment_ref(
            environment_manifest,
            expected_authority_environment_digest=plan.environment_digest,
        )
        environment_authority_bridge = _environment_authority_bridge(
            plan=plan,
            runtime_environment_digest=certificate_environment_digest,
            tool_versions=environment_ref.tool_versions,
        )
    nodes: list[JsonObject] = []
    for node in plan.lemma_nodes:
        node_id = str(node["node_id"])
        payload = plan.node_theorem_payload(node_id)
        nodes.append(
            {
                "node_id": node_id,
                "node_kind": str(node["node_kind"]),
                "depth": int(node["depth"]),
                "required_output_name": PROOF_ARTIFACT_OUTPUT_NAME,
                "context_digest": _node_context_digest(
                    case_id=plan.case_id,
                    node_id=node_id,
                    payload=payload,
                ),
                "theorem_payload": payload.to_dict(),
            }
        )
    oracle_ref = copy.deepcopy(plan.oracle_proof_package_ref)
    oracle_digest = (
        str(oracle_ref["content_hash"])
        if isinstance(oracle_ref, dict)
        else None
    )
    certificate = LeanLemmaGraphCertificate.from_dict(
        {
            "certificate_schema_version": (
                "lean_proof.lemma_graph_certificate.v2"
            ),
            "certificate_id": f"lean_lemma_graph_certificate:{plan.case_id}",
            "parent_theorem_payload_ref": parent_theorem_payload_ref.to_dict(),
            "normalized_parent_goal_digest": canonical_json_digest(
                {
                    "case_id": plan.case_id,
                    "root_node_id": plan.root_node_id,
                    "paper_difficulty": plan.paper_difficulty,
                }
            ),
            "policy_id": DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
            "rule_id": LEAN_LEMMA_GRAPH_RULE_ID,
            "topic_family": plan.topic_family,
            "topic_family_version": plan.topic_family_version,
            "construction_rule_id": plan.construction_rule_id,
            "oracle_package_group": plan.oracle_package_group,
            "proof_assembly_shape": plan.proof_assembly_shape,
            "root_node_id": plan.root_node_id,
            "lemma_nodes": nodes,
            "dependency_edges": copy.deepcopy(plan.dependency_edges),
            "merge_nodes": _merge_nodes(plan),
            "environment_digest": certificate_environment_digest,
            "oracle_proof_package_ref": oracle_ref,
            "oracle_proof_package_digest": oracle_digest,
            "diagnostics": {
                "source": "lean_fixed_decomposition_plan",
                "plan_id": plan.plan_id,
                "plan_digest": plan.plan_digest,
                "preflight_status": plan.preflight_status,
                "fixed_plan_environment_digest": plan.environment_digest,
                **(
                    {}
                    if environment_authority_bridge is None
                    else {
                        "environment_authority_bridge": environment_authority_bridge
                    }
                ),
            },
        },
        expected_environment_digest=certificate_environment_digest,
    )
    return certificate


def _environment_authority_bridge(
    *,
    plan: LeanFixedDecompositionPlan,
    runtime_environment_digest: str,
    tool_versions: JsonObject,
) -> JsonObject:
    semantic_authority_schema_version = tool_versions.get(
        "semantic_authority_schema_version"
    )
    if semantic_authority_schema_version != LEAN_SEMANTIC_AUTHORITY_SCHEMA_VERSION:
        raise ValueError("Lean environment authority bridge schema version mismatch")
    digests = {
        field_name: tool_versions.get(field_name)
        for field_name in (
            "authority_environment_digest",
            "runtime_environment_digest",
            "semantic_environment_digest",
            "sidecar_digest",
        )
    }
    for field_name, digest in digests.items():
        _require_digest(field_name, digest)
    if digests["authority_environment_digest"] != plan.environment_digest:
        raise ValueError("Lean environment authority bridge does not match fixed plan")
    if digests["runtime_environment_digest"] != runtime_environment_digest:
        raise ValueError("Lean environment authority bridge runtime digest mismatch")
    return {
        "schema_version": LEAN_ENVIRONMENT_AUTHORITY_BRIDGE_SCHEMA_VERSION,
        "semantic_authority_schema_version": semantic_authority_schema_version,
        **digests,
    }


def _validate_graph(plan: LeanFixedDecompositionPlan) -> None:
    if not isinstance(plan.lemma_nodes, list) or not plan.lemma_nodes:
        raise ValueError("lemma_nodes must be a non-empty list")
    node_ids: list[str] = []
    root_ids: list[str] = []
    declared_depths: dict[str, int] = {}
    for node in plan.lemma_nodes:
        _require_object("lemma node", node)
        _require_fields(
            node,
            ("node_id", "node_kind", "depth", "theorem_payload"),
            "lemma node",
        )
        node_id = node["node_id"]
        _require_non_empty("node_id", node_id)
        if node_id in node_ids:
            raise ValueError(f"duplicate node_id: {node_id}")
        node_ids.append(node_id)
        if node["node_kind"] == "root_theorem":
            root_ids.append(node_id)
        if type(node["depth"]) is not int or node["depth"] < 1:
            raise ValueError("lemma node depth must be a positive integer")
        declared_depths[node_id] = node["depth"]
        _require_object("lemma node theorem_payload", node["theorem_payload"])
        plan.node_theorem_payload(node_id)
    if root_ids != [plan.root_node_id]:
        raise ValueError(
            "Lean fixed decomposition plan requires exactly one root_theorem "
            "matching merge_plan_shape.root_node_id"
        )

    incoming = _incoming_by_target(tuple(node_ids), plan.dependency_edges)
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    seen_edges: set[tuple[str, str]] = set()
    for edge in plan.dependency_edges:
        source = str(edge["source_node_id"])
        target = str(edge["target_node_id"])
        key = (source, target)
        if key in seen_edges:
            raise ValueError(f"duplicate dependency edge: {source}->{target}")
        seen_edges.add(key)
        outgoing[source].append(target)
    ordered = _topological_order(tuple(node_ids), incoming, outgoing)
    sinks = [node_id for node_id in node_ids if not outgoing[node_id]]
    if sinks != [plan.root_node_id]:
        raise ValueError("Lean fixed decomposition plan requires one reachable root sink")

    computed_depths: dict[str, int] = {}
    for node_id in ordered:
        computed_depths[node_id] = (
            1
            if not incoming[node_id]
            else 1 + max(computed_depths[source] for source in incoming[node_id])
        )
    if declared_depths != computed_depths:
        raise ValueError("lemma node depth does not match dependency DAG depth")
    if max(computed_depths.values()) != plan.expected_depth:
        raise ValueError("expected_depth does not match dependency DAG")
    leaf_count = sum(1 for node_id in node_ids if not incoming[node_id])
    if leaf_count != plan.expected_leaf_count:
        raise ValueError("expected_leaf_count does not match dependency DAG")
    if len(node_ids) != plan.expected_ai_unit_count:
        raise ValueError("expected_ai_unit_count does not match lemma node count")

    shape = plan.merge_plan_shape
    if plan.proof_assembly_shape == LEAN_LEMMA_GRAPH_STRUCTURED_BLOCKED_SHAPE:
        _require_fields(
            shape,
            ("kind", "root_node_id", "required_slots", "stress_policy"),
            "merge_plan_shape",
        )
        required_slots = shape["required_slots"]
        if (
            not isinstance(required_slots, list)
            or required_slots != incoming[plan.root_node_id]
        ):
            raise ValueError(
                "blocked merge_plan_shape.required_slots must equal root dependencies"
            )
        return
    _require_fields(
        shape,
        ("kind", "root_node_id", "required_slots", "dependency_order"),
        "merge_plan_shape",
    )
    required_slots = shape["required_slots"]
    dependency_order = shape["dependency_order"]
    if not isinstance(required_slots, list) or required_slots != ordered:
        raise ValueError(
            "merge_plan_shape.required_slots must equal the complete topological order"
        )
    if not isinstance(dependency_order, list) or dependency_order != ordered:
        raise ValueError(
            "merge_plan_shape.dependency_order must equal the graph topological order"
        )


def _validate_limits(plan: LeanFixedDecompositionPlan) -> None:
    parent = plan.parent_theorem_payload()
    policy = parent.decomposition_policy
    limits = {
        "max_depth": plan.expected_depth,
        "max_nodes": plan.expected_ai_unit_count,
        "max_leaf_count": plan.expected_leaf_count,
    }
    for name, actual in limits.items():
        configured = policy.get(name)
        if type(configured) is not int or configured < actual:
            raise ValueError(f"Lean fixed decomposition plan exceeds {name}")
    incoming = _incoming_by_target(plan.node_ids, plan.dependency_edges)
    if len(incoming[plan.root_node_id]) > int(policy.get("max_children", -1)):
        raise ValueError("Lean fixed decomposition plan exceeds root max_children")
    for node_id in plan.node_ids:
        node_policy = plan.node_theorem_payload(node_id).decomposition_policy
        if len(incoming[node_id]) > int(node_policy.get("max_children", -1)):
            raise ValueError(
                f"Lean fixed decomposition plan exceeds node max_children: {node_id}"
            )


def _validate_oracle(plan: LeanFixedDecompositionPlan) -> None:
    oracle = plan.oracle_proof_package_ref
    if plan.proof_assembly_shape == LEAN_LEMMA_GRAPH_STRUCTURED_BLOCKED_SHAPE:
        if oracle is not None:
            raise ValueError("structured blocked fixed plan cannot carry oracle package")
        if plan.preflight_status not in {"structured_blocked", "frontier_stress"}:
            raise ValueError("structured blocked fixed plan requires blocked preflight")
        return
    if plan.preflight_status != "passed":
        raise ValueError("checker-backed fixed plan requires passed preflight_status")
    _require_object("oracle_proof_package_ref", oracle)
    _require_fields(
        oracle,
        ("content_hash", "kind", "package_id", "node_proof_sources"),
        "oracle_proof_package_ref",
    )
    _require_digest("oracle_proof_package_ref.content_hash", oracle["content_hash"])
    sources = oracle["node_proof_sources"]
    if not isinstance(sources, dict):
        raise ValueError("oracle proof package node_proof_sources must be an object")
    expected = set(plan.node_ids)
    actual = set(str(item) for item in sources)
    if actual != expected:
        raise ValueError("oracle proof package node set must match fixed plan nodes")
    for node_id, proof_source in sources.items():
        if not isinstance(proof_source, str) or not proof_source.strip():
            raise ValueError(f"oracle proof package node source is empty: {node_id}")


def _incoming_by_target(
    node_ids: tuple[str, ...],
    edges: list[JsonObject],
) -> dict[str, list[str]]:
    if not isinstance(edges, list):
        raise ValueError("dependency_edges must be a list")
    known = set(node_ids)
    incoming: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for edge in edges:
        _require_object("dependency edge", edge)
        _require_fields(
            edge,
            ("source_node_id", "target_node_id"),
            "dependency edge",
        )
        source = edge["source_node_id"]
        target = edge["target_node_id"]
        if source not in known or target not in known:
            raise ValueError("dependency edge must reference fixed-plan nodes")
        if source == target:
            raise ValueError("dependency edge cannot reference itself")
        incoming[target].append(source)
    order_index = {node_id: index for index, node_id in enumerate(node_ids)}
    for sources in incoming.values():
        sources.sort(key=lambda item: order_index[item])
    return incoming


def _topological_order(
    node_ids: tuple[str, ...],
    incoming: dict[str, list[str]],
    outgoing: dict[str, list[str]],
) -> list[str]:
    order_index = {node_id: index for index, node_id in enumerate(node_ids)}
    remaining = {node_id: len(incoming[node_id]) for node_id in node_ids}
    ready = [node_id for node_id in node_ids if remaining[node_id] == 0]
    ordered: list[str] = []
    while ready:
        ready.sort(key=lambda item: order_index[item])
        node_id = ready.pop(0)
        ordered.append(node_id)
        for target in outgoing[node_id]:
            remaining[target] -= 1
            if remaining[target] == 0:
                ready.append(target)
    if len(ordered) != len(node_ids):
        raise ValueError("Lean fixed decomposition plan dependency graph has a cycle")
    return ordered


def _merge_nodes(plan: LeanFixedDecompositionPlan) -> list[JsonObject]:
    incoming = _incoming_by_target(plan.node_ids, plan.dependency_edges)
    return [
        {
            "node_id": node_id,
            "merge_kind": (
                "root_dependency_proof_unit"
                if node_id == plan.root_node_id
                else "dependency_proof_unit"
            ),
            "required_input_node_ids": list(incoming[node_id]),
        }
        for node_id in plan.node_ids
        if incoming[node_id]
    ]


def _payload_from_body(
    payload_body: JsonObject,
    *,
    case_id: str,
    node_id: str,
) -> LeanTheoremPayload:
    body = {
        "schema_version": "lean_proof.theorem_payload.v1",
        "theorem_id": f"lean_lemma_graph:{case_id}:{node_id}",
        "imports": ["Init"],
        "namespace": "TokenSharePaperLemmaGraph",
        "open_namespaces": [],
        "options": {},
        "parameters_source": "",
        "theorem_source": None,
        "proof_candidate_ref": None,
        "library_context": {
            "project": "tokenshare_lean",
            "module": "TokenShare.LemmaGraphOracle",
            "case_id": case_id,
            "node_id": node_id,
        },
        "decomposition_policy": {
            "policy_id": DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
            "allowed_rules": ["fixed_oracle_lemma_graph"],
            "max_depth": 4,
            "max_children": 8,
            "max_nodes": 16,
            "max_leaf_count": 8,
            "unsupported_policy": "return_unsupported",
        },
        "resource_limits": {"timeout_seconds": 30, "max_output_bytes": 65536},
        **copy.deepcopy(payload_body),
    }
    return LeanTheoremPayload.from_dict(body)


def _node_context_digest(
    *,
    case_id: str,
    node_id: str,
    payload: LeanTheoremPayload,
) -> str:
    return canonical_json_digest(
        {
            "case_id": case_id,
            "node_id": node_id,
            "theorem_payload_digest": payload.payload_digest,
        }
    )


def _require_fields(
    data: JsonObject,
    fields: tuple[str, ...],
    context: str,
) -> None:
    missing = sorted(set(fields).difference(data))
    if missing:
        raise ValueError(f"{context} missing required field: {', '.join(missing)}")


def _require_object(name: str, value: Any) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")


def _require_non_empty(name: str, value: Any) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")


def _require_digest(name: str, value: Any) -> None:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        raise ValueError(f"{name} must be a sha256 digest")
