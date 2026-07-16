"""Frozen paper catalog loading and validation."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from math import isqrt
from pathlib import Path
from typing import Any

from tokenshare.experiments.paper_models import (
    LEAN_PAPER_DIFFICULTIES,
    LEAN_TOPIC_FAMILIES,
    PAPER_DIFFICULTIES,
    PAPER_DIFFICULTY_VALUES,
    PAPER_DOMAINS,
    JsonObject,
    digest_json,
)
from tokenshare.plugins.lean_proof.checker import (
    LeanCheckerMode,
    LeanCheckerRequest,
    LeanCheckerStatus,
    check_lean_proof,
)
from tokenshare.plugins.lean_proof.environment import (
    LeanEnvironmentManifest,
    build_lean_environment_ref,
)
from tokenshare.plugins.lean_proof.fixtures import default_lean_fixture_project_path
from tokenshare.plugins.lean_proof.models import LeanTheoremPayload
from tokenshare.plugins.lean_proof.preflight import run_lean_preflight
from tokenshare.storage.artifacts import ArtifactStore


DIFFICULTIES = PAPER_DIFFICULTIES
LEAN_V2_SCHEMA_VERSION = "tokenshare.paper_lean_lemma_graph_case.v1"
LEAN_PROOF_ASSEMBLY_SHAPES = (
    "recursive_lemma_dag_required_slots.v1",
    "recursive_induction_lemma_dag_required_slots.v1",
    "structured_blocked_no_oracle_frontier_stress.v1",
)
CREATED_AT = "2026-07-14T00:00:00Z"
LEAN_RESOURCE_LIMITS: JsonObject = {"timeout_seconds": 30, "max_output_bytes": 65536}
LEAN_VERSION = "Lean (version 4.8.0, x86_64-w64-windows-gnu, commit df668f00e6c0, Release)"
LAKE_VERSION = "Lake version 5.0.0-df668f0 (Lean version 4.8.0)"
_LEAN_PREFLIGHT_CACHE: dict[str, JsonObject] = {}
_LEAN_LEMMA_GRAPH_PREFLIGHT_CACHE: dict[str, JsonObject] = {}


@dataclass(frozen=True, kw_only=True)
class PaperInputCatalogManifest:
    catalog_id: str
    catalog_version: str
    catalog_digest: str
    generator_version: str
    case_count: int
    domain_counts: dict[str, int]
    difficulty_counts: dict[str, dict[str, int]]
    paper_difficulty_counts: dict[str, dict[str, int]]
    topic_family_counts: dict[str, dict[str, int]]
    paper_difficulty_topic_family_counts: dict[str, dict[str, dict[str, int]]]
    oracle_validation_status: str
    lean_preflight_status: str
    lean_preflight_summary: JsonObject
    lean_lemma_graph_preflight_summary: JsonObject
    created_at: str
    source_files: list[str]
    factorization_cases: tuple[JsonObject, ...]
    lean_cases: tuple[JsonObject, ...]
    lean_lemma_graph_cases: tuple[JsonObject, ...] = ()
    schema_version: str = "tokenshare.paper_input_catalog_manifest.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "catalog_id": self.catalog_id,
            "catalog_version": self.catalog_version,
            "catalog_digest": self.catalog_digest,
            "generator_version": self.generator_version,
            "case_count": self.case_count,
            "domain_counts": dict(self.domain_counts),
            "difficulty_counts": {
                domain: dict(counts)
                for domain, counts in self.difficulty_counts.items()
            },
            "paper_difficulty_counts": {
                domain: dict(counts)
                for domain, counts in self.paper_difficulty_counts.items()
            },
            "topic_family_counts": {
                domain: dict(counts)
                for domain, counts in self.topic_family_counts.items()
            },
            "paper_difficulty_topic_family_counts": {
                domain: {
                    paper_difficulty: dict(topic_counts)
                    for paper_difficulty, topic_counts in counts.items()
                }
                for domain, counts in self.paper_difficulty_topic_family_counts.items()
            },
            "oracle_validation_status": self.oracle_validation_status,
            "lean_preflight_status": self.lean_preflight_status,
            "lean_preflight_summary": dict(self.lean_preflight_summary),
            "lean_lemma_graph_preflight_summary": dict(
                self.lean_lemma_graph_preflight_summary
            ),
            "created_at": self.created_at,
            "source_files": list(self.source_files),
        }

    def cases_for(
        self,
        *,
        domain: str,
        difficulty: str | None = None,
        paper_difficulty: str | None = None,
        topic_family: str | None = None,
    ) -> tuple[JsonObject, ...]:
        if domain not in PAPER_DOMAINS:
            raise ValueError("domain must be factorization or lean_proof")
        if difficulty is not None and difficulty not in DIFFICULTIES:
            raise ValueError("difficulty must be easy, medium, or hard")
        if paper_difficulty is not None and paper_difficulty not in PAPER_DIFFICULTY_VALUES:
            raise ValueError("paper_difficulty is not supported")
        if topic_family is not None and topic_family not in LEAN_TOPIC_FAMILIES:
            raise ValueError("topic_family is not supported")
        cases = (
            self.factorization_cases
            if domain == "factorization"
            else self.lean_cases + self.lean_lemma_graph_cases
        )
        return tuple(
            case
            for case in cases
            if (difficulty is None or case["difficulty"] == difficulty)
            and (
                paper_difficulty is None
                or case.get("paper_difficulty") == paper_difficulty
            )
            and (topic_family is None or case.get("topic_family") == topic_family)
        )


def load_paper_catalogs(
    *,
    factorization_path: str | Path,
    lean_path: str | Path,
    lean_lemma_graph_path: str | Path | None = None,
) -> PaperInputCatalogManifest:
    factor_path = Path(factorization_path)
    lean_catalog_path = Path(lean_path)
    factorization_cases = tuple(
        _with_factorization_paper_difficulty(case)
        for case in _load_jsonl(factor_path)
    )
    lean_cases = tuple(
        _with_lean_v1_paper_difficulty(case)
        for case in _load_jsonl(lean_catalog_path)
    )
    lean_lemma_graph_cases = (
        tuple(_load_lean_lemma_graph_cases(Path(lean_lemma_graph_path)))
        if lean_lemma_graph_path is not None
        else ()
    )

    _validate_unique_case_ids(factorization_cases + lean_cases + lean_lemma_graph_cases)
    for case in factorization_cases:
        _validate_factorization_case(case)
    for case in lean_cases:
        _validate_lean_case(case)
    for case in lean_lemma_graph_cases:
        _validate_lean_lemma_graph_case(case)
    _validate_distribution("factorization", factorization_cases)
    _validate_distribution("lean_proof", lean_cases)
    lean_preflight_summary = _run_lean_catalog_preflight(lean_cases)
    expected_lean_environment_digest = str(lean_preflight_summary["environment_digest"])
    _validate_lean_environment_digest(
        lean_cases,
        expected_digest=expected_lean_environment_digest,
    )
    _validate_lean_lemma_graph_environment_digest(
        lean_lemma_graph_cases,
        expected_digest=expected_lean_environment_digest,
    )
    lean_lemma_graph_preflight_summary = _run_lean_lemma_graph_preflight(
        lean_lemma_graph_cases,
        expected_environment_digest=expected_lean_environment_digest,
    )

    body = {
        "catalog_id": "tokenshare.paper.catalog",
        "catalog_version": "v1",
        "factorization_cases": factorization_cases,
        "lean_cases": lean_cases,
        "lean_lemma_graph_cases": lean_lemma_graph_cases,
    }
    domain_counts = {
        "factorization": len(factorization_cases),
        "lean_proof": len(lean_cases) + len(lean_lemma_graph_cases),
    }
    all_lean_cases = lean_cases + lean_lemma_graph_cases
    difficulty_counts = {
        "factorization": _difficulty_counts(factorization_cases),
        "lean_proof": _difficulty_counts(all_lean_cases),
    }
    paper_difficulty_counts = {
        "factorization": _paper_difficulty_counts(factorization_cases, domain="factorization"),
        "lean_proof": _paper_difficulty_counts(all_lean_cases, domain="lean_proof"),
    }
    topic_family_counts = {
        "factorization": {},
        "lean_proof": _topic_family_counts(all_lean_cases),
    }
    paper_difficulty_topic_family_counts = {
        "factorization": {},
        "lean_proof": _paper_difficulty_topic_family_counts(all_lean_cases),
    }
    source_files = [factor_path.as_posix(), lean_catalog_path.as_posix()]
    if lean_lemma_graph_path is not None:
        source_files.append(Path(lean_lemma_graph_path).as_posix())
    return PaperInputCatalogManifest(
        catalog_id="tokenshare.paper.catalog",
        catalog_version="v1",
        catalog_digest=digest_json(body),
        generator_version="tokenshare.paper_catalog.static.v1",
        case_count=len(factorization_cases) + len(lean_cases) + len(lean_lemma_graph_cases),
        domain_counts=domain_counts,
        difficulty_counts=difficulty_counts,
        paper_difficulty_counts=paper_difficulty_counts,
        topic_family_counts=topic_family_counts,
        paper_difficulty_topic_family_counts=paper_difficulty_topic_family_counts,
        oracle_validation_status="passed",
        lean_preflight_status=str(lean_preflight_summary["status"]),
        lean_preflight_summary=lean_preflight_summary,
        lean_lemma_graph_preflight_summary=lean_lemma_graph_preflight_summary,
        created_at=CREATED_AT,
        source_files=source_files,
        factorization_cases=factorization_cases,
        lean_cases=lean_cases,
        lean_lemma_graph_cases=lean_lemma_graph_cases,
    )


def default_lean_paper_environment_manifest() -> LeanEnvironmentManifest:
    return _default_lean_environment_manifest()


def estimated_ai_units_for_case(case: JsonObject) -> int:
    if "expected_ai_unit_count" in case:
        return _positive_int(case["expected_ai_unit_count"], "expected_ai_unit_count")
    if case["schema_version"] == "tokenshare.paper_factorization_case.v1":
        split_params = case.get("split_params", {})
        requested = int(split_params.get("requested_child_count", 1))
        return max(1, requested)
    return max(1, int(case.get("expected_child_count", 1)))


def _with_factorization_paper_difficulty(case: JsonObject) -> JsonObject:
    enriched = dict(case)
    enriched.setdefault("paper_difficulty", enriched.get("difficulty"))
    return enriched


def _with_lean_v1_paper_difficulty(case: JsonObject) -> JsonObject:
    enriched = dict(case)
    enriched["paper_difficulty"] = "simple"
    enriched["topic_family"] = "pure_logic"
    enriched["topic_family_version"] = "shallow_v1"
    return enriched


def _load_lean_lemma_graph_cases(path: Path) -> list[JsonObject]:
    return [dict(case) for case in _load_jsonl(path)]


def _load_jsonl(path: Path) -> list[JsonObject]:
    if not path.exists():
        raise FileNotFoundError(path)
    rows: list[JsonObject] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number} is not valid JSON") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{line_number} must be a JSON object")
        rows.append(row)
    return rows


def _validate_unique_case_ids(cases: tuple[JsonObject, ...]) -> None:
    seen: set[str] = set()
    for case in cases:
        case_id = str(case.get("case_id", ""))
        if case_id in seen:
            raise ValueError(f"duplicate case_id: {case_id}")
        seen.add(case_id)


def _validate_distribution(domain: str, cases: tuple[JsonObject, ...]) -> None:
    if len(cases) != 30:
        raise ValueError(f"{domain} catalog must contain exactly 30 cases")
    counts = _difficulty_counts(cases)
    expected = {difficulty: 10 for difficulty in DIFFICULTIES}
    if counts != expected:
        raise ValueError(f"{domain} catalog must contain 10 cases per difficulty")


def _difficulty_counts(cases: tuple[JsonObject, ...]) -> dict[str, int]:
    return {
        difficulty: sum(1 for case in cases if case.get("difficulty") == difficulty)
        for difficulty in DIFFICULTIES
    }


def _paper_difficulty_counts(cases: tuple[JsonObject, ...], *, domain: str) -> dict[str, int]:
    values = DIFFICULTIES if domain == "factorization" else LEAN_PAPER_DIFFICULTIES
    return {
        paper_difficulty: sum(
            1 for case in cases if case.get("paper_difficulty") == paper_difficulty
        )
        for paper_difficulty in values
    }


def _topic_family_counts(cases: tuple[JsonObject, ...]) -> dict[str, int]:
    return {
        topic_family: sum(1 for case in cases if case.get("topic_family") == topic_family)
        for topic_family in LEAN_TOPIC_FAMILIES
    }


def _paper_difficulty_topic_family_counts(
    cases: tuple[JsonObject, ...],
) -> dict[str, dict[str, int]]:
    return {
        paper_difficulty: {
            topic_family: sum(
                1
                for case in cases
                if case.get("paper_difficulty") == paper_difficulty
                and case.get("topic_family") == topic_family
            )
            for topic_family in LEAN_TOPIC_FAMILIES
        }
        for paper_difficulty in LEAN_PAPER_DIFFICULTIES
    }


def _validate_factorization_case(case: JsonObject) -> None:
    _require_schema(case, "tokenshare.paper_factorization_case.v1")
    for field_name in (
        "case_id",
        "target_n",
        "oracle_prime_factors",
        "candidate_start",
        "candidate_end",
        "candidate_divisor_count",
        "factor_position_quantile",
        "difficulty",
        "split_params",
        "source_seed",
    ):
        if field_name not in case:
            raise ValueError(f"factorization case missing {field_name}")
    target_n = _decimal(case["target_n"], "target_n", min_value=2)
    candidate_start = _decimal(case["candidate_start"], "candidate_start", min_value=2)
    candidate_end = _decimal(case["candidate_end"], "candidate_end", min_value=2)
    if candidate_start > candidate_end:
        raise ValueError("candidate_start must be <= candidate_end")
    if candidate_end > isqrt(target_n):
        raise ValueError("candidate_end must be <= floor_sqrt(target_n)")
    if int(case["candidate_divisor_count"]) != candidate_end - candidate_start + 1:
        raise ValueError("candidate_divisor_count does not match candidate range")
    if case["difficulty"] not in DIFFICULTIES:
        raise ValueError("invalid difficulty")
    if case.get("paper_difficulty") != case["difficulty"]:
        raise ValueError("factorization paper_difficulty must match difficulty")
    factors = case["oracle_prime_factors"]
    if not isinstance(factors, list) or not factors:
        raise ValueError("oracle_prime_factors must be a non-empty list")
    product = 1
    for factor in factors:
        if not isinstance(factor, dict):
            raise ValueError("oracle prime factor must be an object")
        prime = _decimal(factor.get("prime"), "oracle prime", min_value=2)
        exponent = int(factor.get("exponent"))
        if exponent < 1:
            raise ValueError("oracle exponent must be >= 1")
        if not _is_prime(prime):
            raise ValueError("oracle prime factor must be prime")
        product *= prime**exponent
    if product != target_n:
        raise ValueError("oracle prime factors do not multiply to target_n")
    if not isinstance(case["split_params"], dict):
        raise ValueError("split_params must be an object")
    if isinstance(case["source_seed"], bool) or not isinstance(case["source_seed"], int):
        raise ValueError("source_seed must be an integer")


def _validate_lean_case(case: JsonObject) -> None:
    _require_schema(case, "tokenshare.paper_lean_case.v1")
    required = (
        "case_id",
        "theorem_payload",
        "difficulty",
        "expected_split_kind",
        "expected_child_count",
        "minimum_proof_steps",
        "context_item_count",
        "oracle_proof_ref",
        "environment_digest",
    )
    for field_name in required:
        if field_name not in case:
            raise ValueError(f"lean case missing {field_name}")
    if case["difficulty"] not in DIFFICULTIES:
        raise ValueError("invalid difficulty")
    if case.get("paper_difficulty") != "simple":
        raise ValueError("Lean v1 paper_difficulty must be simple")
    payload = case["theorem_payload"]
    if not isinstance(payload, dict):
        raise ValueError("theorem_payload must be an object")
    for field_name in ("schema_version", "theorem_name", "parameters_source", "statement_source"):
        if not payload.get(field_name):
            raise ValueError(f"theorem_payload missing {field_name}")
    if payload["schema_version"] != "lean_proof.theorem_payload.v1":
        raise ValueError("invalid theorem_payload schema_version")
    if case["expected_split_kind"] not in {"conjunction", "iff"}:
        raise ValueError("expected_split_kind must be conjunction or iff")
    if int(case["expected_child_count"]) < 1:
        raise ValueError("expected_child_count must be positive")
    minimum_steps = int(case["minimum_proof_steps"])
    if case["difficulty"] == "easy" and minimum_steps < 1:
        raise ValueError("easy Lean cases require at least 1 proof step")
    if case["difficulty"] == "medium" and minimum_steps < 2:
        raise ValueError("medium Lean cases require at least 2 proof steps")
    if case["difficulty"] == "hard" and minimum_steps < 4:
        raise ValueError("hard Lean cases require at least 4 proof steps")
    if int(case["context_item_count"]) < 0:
        raise ValueError("context_item_count must be non-negative")
    proof_ref = case["oracle_proof_ref"]
    if not isinstance(proof_ref, dict) or proof_ref.get("preflight_status") != "passed":
        raise ValueError("oracle_proof_ref must record passed preflight")
    if not isinstance(proof_ref.get("proof_source"), str) or not proof_ref["proof_source"]:
        raise ValueError("oracle_proof_ref must include proof_source")
    _require_digest("environment_digest", case["environment_digest"])


def _validate_lean_lemma_graph_case(case: JsonObject) -> None:
    _require_schema(case, LEAN_V2_SCHEMA_VERSION)
    required = (
        "schema_version",
        "case_id",
        "domain",
        "difficulty",
        "paper_difficulty",
        "topic_family",
        "topic_family_version",
        "construction_rule_id",
        "oracle_package_group",
        "proof_assembly_shape",
        "root_theorem_payload",
        "lemma_graph",
        "dependency_edges",
        "expected_depth",
        "expected_leaf_count",
        "expected_ai_unit_count",
        "merge_plan_shape",
        "oracle_proof_package_ref",
        "environment_digest",
        "preflight_status",
    )
    for field_name in required:
        if field_name not in case:
            raise ValueError(f"Lean v2 case missing {field_name}")
    if case["domain"] != "lean_proof":
        raise ValueError("Lean v2 domain must be lean_proof")
    if case["difficulty"] not in DIFFICULTIES:
        raise ValueError("invalid difficulty")
    if case["paper_difficulty"] not in LEAN_PAPER_DIFFICULTIES:
        raise ValueError("invalid Lean v2 paper_difficulty")
    if case["topic_family"] not in LEAN_TOPIC_FAMILIES:
        raise ValueError("invalid Lean v2 topic_family")
    for field_name in (
        "topic_family_version",
        "construction_rule_id",
        "oracle_package_group",
    ):
        if not isinstance(case[field_name], str) or not case[field_name]:
            raise ValueError(f"Lean v2 case missing {field_name}")
    if case["proof_assembly_shape"] not in LEAN_PROOF_ASSEMBLY_SHAPES:
        raise ValueError("unsupported proof_assembly_shape")
    payload = case["root_theorem_payload"]
    if not isinstance(payload, dict):
        raise ValueError("root_theorem_payload must be an object")
    for field_name in ("schema_version", "theorem_name", "parameters_source", "statement_source"):
        if not payload.get(field_name):
            raise ValueError(f"root_theorem_payload missing {field_name}")
    if payload["schema_version"] != "lean_proof.theorem_payload.v1":
        raise ValueError("invalid root_theorem_payload schema_version")
    _require_digest("environment_digest", case["environment_digest"])
    if not isinstance(case["merge_plan_shape"], dict) or not case["merge_plan_shape"]:
        raise ValueError("merge_plan_shape must be a non-empty object")
    preflight_status = str(case["preflight_status"])
    oracle_ref = case.get("oracle_proof_package_ref")
    if case["paper_difficulty"] == "medium_lemma_dag":
        if not isinstance(case.get("dependency_edges"), list) or not case["dependency_edges"]:
            raise ValueError("medium_lemma_dag requires dependency_edges")
        if not isinstance(oracle_ref, dict) or not oracle_ref:
            raise ValueError("medium_lemma_dag requires oracle_proof_package_ref")
        if not preflight_status:
            raise ValueError("medium_lemma_dag requires preflight_status")
        if preflight_status != "passed":
            raise ValueError("medium_lemma_dag requires passed preflight_status")
        if not case.get("environment_digest"):
            raise ValueError("medium_lemma_dag requires environment_digest")
    if case["paper_difficulty"] == "hard_frontier" and not oracle_ref:
        if preflight_status not in {"structured_blocked", "frontier_stress"}:
            raise ValueError(
                "hard_frontier without oracle_proof_package_ref must be "
                "structured_blocked or frontier_stress"
            )
    graph_stats = _validate_lemma_graph(case["lemma_graph"], case["dependency_edges"])
    expected_depth = _positive_int(case["expected_depth"], "expected_depth")
    expected_leaf_count = _positive_int(case["expected_leaf_count"], "expected_leaf_count")
    expected_ai_unit_count = _positive_int(
        case["expected_ai_unit_count"],
        "expected_ai_unit_count",
    )
    if expected_depth != graph_stats["depth"]:
        raise ValueError("expected_depth does not match lemma graph")
    if expected_leaf_count != graph_stats["leaf_count"]:
        raise ValueError("expected_leaf_count does not match lemma graph")
    node_count = graph_stats["node_count"]
    if not (expected_leaf_count <= expected_ai_unit_count <= node_count):
        raise ValueError("expected_ai_unit_count is inconsistent with lemma graph")
    if case["paper_difficulty"] == "medium_lemma_dag":
        if graph_stats["root_count"] != 1:
            raise ValueError("medium_lemma_dag requires exactly one root theorem node")
        if graph_stats["intermediate_count"] < 1:
            raise ValueError("medium_lemma_dag requires an intermediate lemma node")
        if graph_stats["leaf_kind_count"] < 2:
            raise ValueError("medium_lemma_dag requires at least two leaf sublemma nodes")
        if graph_stats["max_declared_depth"] != expected_depth:
            raise ValueError("node depth does not match expected_depth")


def _validate_lemma_graph(lemma_graph: Any, dependency_edges: Any) -> dict[str, int]:
    if not isinstance(lemma_graph, dict):
        raise ValueError("lemma_graph must be an object")
    nodes = lemma_graph.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("lemma_graph.nodes must be a non-empty list")
    node_ids: list[str] = []
    node_kinds: dict[str, str] = {}
    declared_depths: dict[str, int] = {}
    for node in nodes:
        if (
            not isinstance(node, dict)
            or not isinstance(node.get("node_id"), str)
            or not node["node_id"]
        ):
            raise ValueError("lemma_graph.nodes must contain node_id")
        for field_name in ("node_kind", "depth", "statement", "theorem_payload"):
            if field_name not in node:
                raise ValueError(f"lemma_graph node missing {field_name}")
        if node["node_kind"] not in {
            "root_theorem",
            "intermediate_lemma",
            "leaf_sublemma",
        }:
            raise ValueError("lemma_graph node has invalid node_kind")
        if isinstance(node["depth"], bool) or not isinstance(node["depth"], int) or node["depth"] < 1:
            raise ValueError("lemma_graph node depth must be a positive integer")
        if not isinstance(node["statement"], str) or not node["statement"]:
            raise ValueError("lemma_graph node statement must be a non-empty string")
        payload = node["theorem_payload"]
        if not isinstance(payload, dict):
            raise ValueError("lemma_graph node theorem_payload must be an object")
        for field_name in (
            "schema_version",
            "theorem_name",
            "imports",
            "parameters_source",
            "statement_source",
        ):
            if field_name not in payload:
                raise ValueError(f"lemma_graph node theorem_payload missing {field_name}")
        if payload["schema_version"] != "lean_proof.theorem_payload.v1":
            raise ValueError("invalid lemma graph theorem_payload schema_version")
        if payload["statement_source"] != node["statement"]:
            raise ValueError("lemma graph node statement must match theorem_payload")
        node_ids.append(node["node_id"])
        node_kinds[node["node_id"]] = str(node["node_kind"])
        declared_depths[node["node_id"]] = int(node["depth"])
    if len(set(node_ids)) != len(node_ids):
        raise ValueError("lemma_graph.nodes must have unique node_id values")
    node_id_set = set(node_ids)
    if not isinstance(dependency_edges, list):
        raise ValueError("dependency_edges must be a list")

    outgoing: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    incoming_count: dict[str, int] = {node_id: 0 for node_id in node_ids}
    for edge in dependency_edges:
        if not isinstance(edge, dict):
            raise ValueError("dependency_edges entries must be objects")
        source_id = str(edge.get("source_node_id") or "")
        target_id = str(edge.get("target_node_id") or "")
        if source_id not in node_id_set or target_id not in node_id_set:
            raise ValueError("dependency_edges must reference lemma_graph.nodes")
        if source_id == target_id:
            raise ValueError("dependency_edges must form a DAG")
        if declared_depths[target_id] <= declared_depths[source_id]:
            raise ValueError("dependency_edges must increase declared node depth")
        outgoing[source_id].append(target_id)
        incoming_count[target_id] += 1

    ready = [node_id for node_id in node_ids if incoming_count[node_id] == 0]
    topo_order: list[str] = []
    depth_by_node = {node_id: 1 for node_id in node_ids}
    while ready:
        node_id = ready.pop(0)
        topo_order.append(node_id)
        for target_id in outgoing[node_id]:
            depth_by_node[target_id] = max(
                depth_by_node[target_id],
                depth_by_node[node_id] + 1,
            )
            incoming_count[target_id] -= 1
            if incoming_count[target_id] == 0:
                ready.append(target_id)
    if len(topo_order) != len(node_ids):
        raise ValueError("dependency_edges must form a DAG")
    leaf_count = sum(
        1
        for node_id in node_ids
        if not any(node_id in targets for targets in outgoing.values())
    )
    return {
        "node_count": len(node_ids),
        "leaf_count": leaf_count,
        "depth": max(depth_by_node.values()),
        "root_count": sum(1 for kind in node_kinds.values() if kind == "root_theorem"),
        "intermediate_count": sum(
            1 for kind in node_kinds.values() if kind == "intermediate_lemma"
        ),
        "leaf_kind_count": sum(1 for kind in node_kinds.values() if kind == "leaf_sublemma"),
        "max_declared_depth": max(declared_depths.values()),
    }


def _validate_lean_environment_digest(
    lean_cases: tuple[JsonObject, ...],
    *,
    expected_digest: str,
) -> None:
    for case in lean_cases:
        if case["environment_digest"] != expected_digest:
            raise ValueError(
                "Lean case environment_digest does not match checker environment"
            )


def _validate_lean_lemma_graph_environment_digest(
    lean_lemma_graph_cases: tuple[JsonObject, ...],
    *,
    expected_digest: str,
) -> None:
    for case in lean_lemma_graph_cases:
        if case["environment_digest"] != expected_digest:
            raise ValueError(
                "Lean lemma graph case environment_digest does not match checker environment"
            )


def _run_lean_catalog_preflight(lean_cases: tuple[JsonObject, ...]) -> JsonObject:
    environment_manifest = _default_lean_environment_manifest()
    cache_key = digest_json(
        {
            "lean_cases": lean_cases,
            "environment_digest": environment_manifest.environment_digest,
        }
    )
    cached = _LEAN_PREFLIGHT_CACHE.get(cache_key)
    if cached is not None:
        return dict(cached)

    accepted_reports: list[JsonObject] = []
    with tempfile.TemporaryDirectory(prefix="tokenshare_paper_lean_preflight_") as root:
        artifact_store = ArtifactStore(Path(root))
        for case in lean_cases:
            report = _check_lean_catalog_case(
                case,
                artifact_store=artifact_store,
                environment_manifest=environment_manifest,
            )
            if report["status"] != LeanCheckerStatus.ACCEPTED.value:
                excerpt = str(report["diagnostics"].get("combined_excerpt", ""))[:600]
                raise ValueError(
                    f"Lean preflight rejected case {case['case_id']}: {excerpt}"
                )
            accepted_reports.append(report)

    summary: JsonObject = {
        "schema_version": "tokenshare.paper_lean_preflight_summary.v1",
        "status": "passed",
        "checked_case_count": len(lean_cases),
        "accepted_case_count": len(accepted_reports),
        "checker_mode": LeanCheckerMode.DIRECT_PROOF.value,
        "environment_digest": environment_manifest.environment_digest,
        "proof_digest_bundle": digest_json(
            [
                {
                    "case_id": report["case_id"],
                    "status": report["status"],
                    "proof_digest": report["proof_digest"],
                    "normalized_theorem_digest": report["normalized_theorem_digest"],
                }
                for report in accepted_reports
            ]
        ),
    }
    _LEAN_PREFLIGHT_CACHE[cache_key] = dict(summary)
    return summary


def _run_lean_lemma_graph_preflight(
    lean_lemma_graph_cases: tuple[JsonObject, ...],
    *,
    expected_environment_digest: str,
) -> JsonObject:
    if not lean_lemma_graph_cases:
        return {
            "schema_version": "tokenshare.paper_lean_lemma_graph_preflight_summary.v1",
            "status": "not_applicable",
            "checked_case_count": 0,
            "accepted_case_count": 0,
            "blocked_case_count": 0,
            "accepted_node_count_by_case": {},
            "environment_digest": expected_environment_digest,
        }
    environment_manifest = _default_lean_environment_manifest()
    if environment_manifest.environment_digest != expected_environment_digest:
        raise ValueError("Lean lemma graph preflight environment digest drift")
    cache_key = digest_json(
        {
            "lean_lemma_graph_cases": lean_lemma_graph_cases,
            "environment_digest": environment_manifest.environment_digest,
        }
    )
    cached = _LEAN_LEMMA_GRAPH_PREFLIGHT_CACHE.get(cache_key)
    if cached is not None:
        return dict(cached)

    accepted_node_count_by_case: dict[str, int] = {}
    blocked_case_count = 0
    with tempfile.TemporaryDirectory(prefix="tokenshare_paper_lean_lemma_graph_preflight_") as root:
        artifact_store = ArtifactStore(Path(root))
        for case in lean_lemma_graph_cases:
            if (
                case["paper_difficulty"] == "hard_frontier"
                and case.get("oracle_proof_package_ref") is None
            ):
                blocked_case_count += 1
                continue
            if case["preflight_status"] != "passed":
                raise ValueError("Lean lemma graph proof case requires passed preflight_status")
            oracle_ref = _validate_lean_lemma_graph_oracle_ref(case)
            _build_lean_oracle_package(oracle_ref, environment_manifest=environment_manifest)
            node_proof_sources = dict(oracle_ref["node_proof_sources"])
            accepted_count = 0
            for node in case["lemma_graph"]["nodes"]:
                node_id = str(node["node_id"])
                proof_source = node_proof_sources.get(node_id)
                if not isinstance(proof_source, str) or not proof_source:
                    raise ValueError("oracle proof package missing node proof source")
                report = _check_lean_lemma_graph_node(
                    case,
                    node,
                    proof_source=proof_source,
                    artifact_store=artifact_store,
                    environment_manifest=environment_manifest,
                )
                if report["status"] != LeanCheckerStatus.ACCEPTED.value:
                    excerpt = str(report["diagnostics"].get("combined_excerpt", ""))[:600]
                    raise ValueError(
                        "Lean lemma graph preflight rejected "
                        f"case {case['case_id']} node {node_id}: {excerpt}"
                    )
                accepted_count += 1
            accepted_node_count_by_case[str(case["case_id"])] = accepted_count

    summary: JsonObject = {
        "schema_version": "tokenshare.paper_lean_lemma_graph_preflight_summary.v1",
        "status": "passed",
        "checked_case_count": len(lean_lemma_graph_cases),
        "accepted_case_count": len(accepted_node_count_by_case),
        "blocked_case_count": blocked_case_count,
        "accepted_node_count_by_case": accepted_node_count_by_case,
        "checker_mode": LeanCheckerMode.DIRECT_PROOF.value,
        "environment_digest": environment_manifest.environment_digest,
    }
    _LEAN_LEMMA_GRAPH_PREFLIGHT_CACHE[cache_key] = dict(summary)
    return summary


def _validate_lean_lemma_graph_oracle_ref(case: JsonObject) -> JsonObject:
    oracle_ref = case.get("oracle_proof_package_ref")
    if not isinstance(oracle_ref, dict) or not oracle_ref:
        raise ValueError("medium_lemma_dag requires oracle proof package")
    for field_name in ("kind", "package_id", "source_path", "content_hash", "node_proof_sources"):
        if field_name not in oracle_ref:
            raise ValueError(f"oracle proof package missing {field_name}")
    if oracle_ref["kind"] != "fixed_oracle_package":
        raise ValueError("oracle proof package kind must be fixed_oracle_package")
    source_path = Path(str(oracle_ref["source_path"]))
    if not source_path.is_file():
        raise ValueError("oracle proof package source_path does not exist")
    actual_hash = _file_digest(source_path)
    if oracle_ref["content_hash"] != actual_hash:
        raise ValueError("oracle proof package content_hash mismatch")
    node_ids = {str(node["node_id"]) for node in case["lemma_graph"]["nodes"]}
    node_proof_sources = oracle_ref["node_proof_sources"]
    if not isinstance(node_proof_sources, dict):
        raise ValueError("oracle proof package node_proof_sources must be an object")
    if set(node_proof_sources) != node_ids:
        raise ValueError("oracle proof package node_proof_sources must cover every graph node")
    return dict(oracle_ref)


def _build_lean_oracle_package(
    oracle_ref: JsonObject,
    *,
    environment_manifest: LeanEnvironmentManifest,
) -> None:
    source_path = Path(str(oracle_ref["source_path"])).resolve()
    project_root = Path(environment_manifest.project_root).resolve()
    try:
        relative = source_path.relative_to(project_root)
    except ValueError as exc:
        raise ValueError("oracle proof package source_path must be inside Lean project") from exc
    module_name = ".".join(relative.with_suffix("").parts)
    completed = subprocess.run(
        [environment_manifest.lake_executable, "build", module_name],
        cwd=environment_manifest.project_root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=int(LEAN_RESOURCE_LIMITS["timeout_seconds"]),
        env=_lean_subprocess_env(environment_manifest),
        check=False,
    )
    if completed.returncode != 0:
        excerpt = (completed.stdout + completed.stderr)[:1200]
        raise ValueError(f"oracle proof package build failed: {excerpt}")


def _check_lean_lemma_graph_node(
    case: JsonObject,
    node: JsonObject,
    *,
    proof_source: str,
    artifact_store: ArtifactStore,
    environment_manifest: LeanEnvironmentManifest,
) -> JsonObject:
    theorem_payload = _lean_theorem_payload_from_payload(
        node["theorem_payload"],
        case_id=str(case["case_id"]),
        node_id=str(node["node_id"]),
    )
    artifact_prefix = _safe_artifact_id(f"{case['case_id']}_{node['node_id']}")
    theorem_ref = artifact_store.save_json(
        theorem_payload.to_dict(),
        artifact_id=f"{artifact_prefix}_theorem_payload",
        artifact_type="LeanTheoremPayload",
        artifact_schema_id="lean_proof.theorem_payload",
        artifact_schema_version="v1",
        source={
            "kind": "paper_lemma_graph_preflight",
            "case_id": str(case["case_id"]),
            "node_id": str(node["node_id"]),
        },
        metadata={"theorem_name": theorem_payload.theorem_name},
        created_at=CREATED_AT,
    )
    proof_ref = artifact_store.save_json(
        {
            "schema_version": "lean_proof.proof_candidate.v1",
            "proof_candidate_id": (
                f"proof_candidate:{case['case_id']}:{node['node_id']}:oracle_preflight"
            ),
            "theorem_payload_digest": theorem_payload.payload_digest,
            "proof_source": proof_source,
            "created_at": CREATED_AT,
        },
        artifact_id=f"{artifact_prefix}_oracle_proof_candidate",
        artifact_type="LeanProofCandidate",
        artifact_schema_id="lean_proof.proof_candidate",
        artifact_schema_version="v1",
        source={
            "kind": "paper_lemma_graph_preflight",
            "case_id": str(case["case_id"]),
            "node_id": str(node["node_id"]),
        },
        metadata={"theorem_name": theorem_payload.theorem_name},
        created_at=CREATED_AT,
    )
    report = check_lean_proof(
        LeanCheckerRequest(
            request_id=f"paper_lemma_graph_preflight:{case['case_id']}:{node['node_id']}",
            theorem_payload_ref=theorem_ref,
            proof_candidate_ref=proof_ref,
            environment_ref=build_lean_environment_ref(environment_manifest),
            checker_mode=LeanCheckerMode.DIRECT_PROOF,
            timeout_seconds=int(LEAN_RESOURCE_LIMITS["timeout_seconds"]),
            max_output_bytes=int(LEAN_RESOURCE_LIMITS["max_output_bytes"]),
            created_at=CREATED_AT,
        ),
        artifact_store=artifact_store,
        environment_manifest=environment_manifest,
    )
    return {
        "case_id": str(case["case_id"]),
        "node_id": str(node["node_id"]),
        "status": report.status.value,
        "proof_digest": report.proof_digest,
        "normalized_theorem_digest": report.normalized_theorem_digest,
        "diagnostics": report.diagnostics,
    }


def _check_lean_catalog_case(
    case: JsonObject,
    *,
    artifact_store: ArtifactStore,
    environment_manifest: LeanEnvironmentManifest,
) -> JsonObject:
    theorem_payload = _lean_theorem_payload_from_case(case)
    case_id = str(case["case_id"])
    theorem_ref = artifact_store.save_json(
        theorem_payload.to_dict(),
        artifact_id=_safe_artifact_id(f"{case_id}_theorem_payload"),
        artifact_type="LeanTheoremPayload",
        artifact_schema_id="lean_proof.theorem_payload",
        artifact_schema_version="v1",
        source={"kind": "paper_catalog_preflight", "case_id": case_id},
        metadata={"theorem_name": theorem_payload.theorem_name},
        created_at=CREATED_AT,
    )
    proof_ref = artifact_store.save_json(
        {
            "schema_version": "lean_proof.proof_candidate.v1",
            "proof_candidate_id": f"proof_candidate:{case_id}:oracle_preflight",
            "theorem_payload_digest": theorem_payload.payload_digest,
            "proof_source": case["oracle_proof_ref"]["proof_source"],
            "created_at": CREATED_AT,
        },
        artifact_id=_safe_artifact_id(f"{case_id}_oracle_proof_candidate"),
        artifact_type="LeanProofCandidate",
        artifact_schema_id="lean_proof.proof_candidate",
        artifact_schema_version="v1",
        source={"kind": "paper_catalog_preflight", "case_id": case_id},
        metadata={"theorem_name": theorem_payload.theorem_name},
        created_at=CREATED_AT,
    )
    report = check_lean_proof(
        LeanCheckerRequest(
            request_id=f"paper_catalog_preflight:{case_id}",
            theorem_payload_ref=theorem_ref,
            proof_candidate_ref=proof_ref,
            environment_ref=build_lean_environment_ref(environment_manifest),
            checker_mode=LeanCheckerMode.DIRECT_PROOF,
            timeout_seconds=int(LEAN_RESOURCE_LIMITS["timeout_seconds"]),
            max_output_bytes=int(LEAN_RESOURCE_LIMITS["max_output_bytes"]),
            created_at=CREATED_AT,
        ),
        artifact_store=artifact_store,
        environment_manifest=environment_manifest,
    )
    return {
        "case_id": case_id,
        "status": report.status.value,
        "proof_digest": report.proof_digest,
        "normalized_theorem_digest": report.normalized_theorem_digest,
        "diagnostics": report.diagnostics,
    }


def lean_theorem_payload_from_case(case: JsonObject) -> LeanTheoremPayload:
    payload = case["theorem_payload"]
    return LeanTheoremPayload(
        theorem_id=f"lean_theorem:{case['case_id']}",
        theorem_name=payload["theorem_name"],
        imports=list(payload.get("imports", ["Init"])),
        namespace=payload.get("namespace", "TokenSharePaperCatalog"),
        open_namespaces=list(payload.get("open_namespaces", [])),
        options=dict(payload.get("options", {})),
        parameters_source=str(payload.get("parameters_source", "")),
        statement_source=payload["statement_source"],
        theorem_source=payload.get("theorem_source"),
        proof_candidate_ref=None,
        library_context=dict(
            payload.get(
                "library_context",
                {
                    "project": "tokenshare_lean",
                    "module": "TokenSharePaperCatalog",
                    "case_id": case["case_id"],
                },
            )
        ),
        decomposition_policy=dict(
            payload.get(
                "decomposition_policy",
                {
                    "policy_id": "lean_proof.deterministic_tactic_split.v1",
                    "allowed_rules": ["conjunction", "iff", "intro"],
                    "max_depth": 1,
                    "max_children": int(case["expected_child_count"]),
                    "unsupported_policy": "return_unsupported",
                },
            )
        ),
        resource_limits=dict(payload.get("resource_limits", LEAN_RESOURCE_LIMITS)),
    )


def _lean_theorem_payload_from_payload(
    payload: JsonObject,
    *,
    case_id: str,
    node_id: str,
) -> LeanTheoremPayload:
    return LeanTheoremPayload(
        theorem_id=f"lean_theorem:{case_id}:{node_id}",
        theorem_name=payload["theorem_name"],
        imports=list(payload.get("imports", ["Init"])),
        namespace=payload.get("namespace", "TokenSharePaperCatalog"),
        open_namespaces=list(payload.get("open_namespaces", [])),
        options=dict(payload.get("options", {})),
        parameters_source=str(payload.get("parameters_source", "")),
        statement_source=payload["statement_source"],
        theorem_source=payload.get("theorem_source"),
        proof_candidate_ref=None,
        library_context=dict(
            payload.get(
                "library_context",
                {
                    "project": "tokenshare_lean",
                    "module": "TokenSharePaperLemmaGraph",
                    "case_id": case_id,
                    "node_id": node_id,
                },
            )
        ),
        decomposition_policy=dict(
            payload.get(
                "decomposition_policy",
                {
                    "policy_id": "lean_proof.deterministic_tactic_split.v1",
                    "allowed_rules": ["fixed_oracle_lemma_graph"],
                    "max_depth": 0,
                    "max_children": 0,
                    "unsupported_policy": "return_unsupported",
                },
            )
        ),
        resource_limits=dict(payload.get("resource_limits", LEAN_RESOURCE_LIMITS)),
    )


def _lean_theorem_payload_from_case(case: JsonObject) -> LeanTheoremPayload:
    return lean_theorem_payload_from_case(case)


def _default_lean_environment_manifest() -> LeanEnvironmentManifest:
    tools_root = Path.home() / "AppData" / "Local" / "TokenShare" / "LeanToolchain"
    elan_bin = tools_root / "elan-home" / "bin"
    preflight = run_lean_preflight(
        project_root=default_lean_fixture_project_path(),
        lean_executable=elan_bin / "lean.exe",
        lake_executable=elan_bin / "lake.exe",
        lean_version=LEAN_VERSION,
        lake_version=LAKE_VERSION,
        resource_limits=LEAN_RESOURCE_LIMITS,
        created_at=CREATED_AT,
    )
    if not preflight.ready or preflight.environment_manifest is None:
        raise ValueError(f"Lean preflight blocked: {preflight.blocked_reason}")
    return preflight.environment_manifest

def _require_schema(case: JsonObject, schema_version: str) -> None:
    if case.get("schema_version") != schema_version:
        raise ValueError(f"schema_version must be {schema_version}")


def _decimal(value: Any, field_name: str, *, min_value: int) -> int:
    if not isinstance(value, str) or not value.isdecimal():
        raise ValueError(f"{field_name} must be a decimal string")
    parsed = int(value)
    if parsed < min_value:
        raise ValueError(f"{field_name} must be >= {min_value}")
    return parsed


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _require_digest(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        raise ValueError(f"{field_name} must be a sha256 digest")


def _is_prime(value: int) -> bool:
    if value < 2:
        return False
    if value % 2 == 0:
        return value == 2
    divisor = 3
    while divisor * divisor <= value:
        if value % divisor == 0:
            return False
        divisor += 2
    return True


def _file_digest(path: Path) -> str:
    return f"sha256:{sha256(path.read_bytes()).hexdigest()}"


def _lean_subprocess_env(manifest: LeanEnvironmentManifest) -> dict[str, str]:
    env = dict(os.environ)
    lean_exe = Path(manifest.lean_executable)
    elan_home = lean_exe.parent.parent
    env["ELAN_HOME"] = str(elan_home)
    env["PATH"] = f"{lean_exe.parent}{os.pathsep}{env.get('PATH', '')}"
    return env


def _safe_artifact_id(value: str) -> str:
    return "".join(
        character if character.isalnum() or character == "_" else "_"
        for character in value
    )
