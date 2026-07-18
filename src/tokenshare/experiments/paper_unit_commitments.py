"""Deterministic per-AI-unit commitments for paper execution plans."""

from __future__ import annotations

import copy
import json
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Any

from tokenshare.core.models import ArtifactRef, JsonObject, TaskState, TaskUnit
from tokenshare.experiments.paper_models import digest_json
from tokenshare.plugins.factorization.descriptor import (
    build_factorization_plugin_descriptor,
)
from tokenshare.plugins.factorization.models import (
    FactorIntegerSubject,
    FactorSearchRangeInput,
    RootInput,
    canonical_json_digest as factorization_digest_json,
)
from tokenshare.plugins.factorization.prompt_builder import (
    build_factor_search_prompt_package,
)
from tokenshare.plugins.factorization.schemas import (
    FACTOR_SEARCH_RANGE_INPUT_SCHEMA_VERSION,
    FACTOR_SEARCH_RANGE_TASK_TYPE,
    REQUESTED_OUTPUT_PRIME_FACTORIZATION,
    ROOT_INPUT_SCHEMA_VERSION,
)
from tokenshare.plugins.factorization.split_strategy import (
    build_factorization_split_plan,
)
from tokenshare.plugins.factorization.validator import (
    build_factor_search_instruction,
)
from tokenshare.plugins.lean_proof.descriptor import build_lean_proof_plugin_descriptor
from tokenshare.plugins.lean_proof.environment import build_lean_environment_ref
from tokenshare.plugins.lean_proof.models import (
    LeanLemmaGraphCertificate,
    LeanTheoremPayload,
    canonical_json_digest as lean_digest_json,
)
from tokenshare.plugins.lean_proof.prompt_builder import (
    PROOF_CANDIDATE_OUTPUT_NAME,
    build_lean_proof_candidate_prompt_package,
)
from tokenshare.plugins.lean_proof.schemas import (
    CHECKER_VALIDATOR_POLICY_ID,
    DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
    PROOF_ARTIFACT_OUTPUT_NAME,
)
from tokenshare.plugins.lean_proof.split_strategy import (
    LeanSplitHelperReport,
    LeanSplitHelperRequest,
    LeanSplitHelperStatus,
    build_lean_split_plan,
    run_lean_split_helper,
)
from tokenshare.storage.artifacts import ArtifactStore


NOW = "2026-07-14T00:00:00Z"
AI_UNIT_BINDING_SCHEMA_VERSION = "tokenshare.paper_ai_unit_binding.v1"
TASK_UNIT_COMMITMENT_SCHEMA_VERSION = (
    "tokenshare.paper_task_unit_snapshot_commitment.v1"
)
DOMAIN_COMMITMENT_SCHEMA_VERSION = "tokenshare.paper_domain_unit_commitment.v1"
REQUEST_ARTIFACT_COMMITMENT_SCHEMA_VERSION = (
    "tokenshare.paper_request_artifact_commitment.v1"
)
LEAN_V2_SCHEMA_VERSION = "tokenshare.paper_lean_lemma_graph_case.v1"
_LEAN_SIMPLE_SPLIT_CACHE: dict[str, tuple[JsonObject, ...]] = {}


def build_case_ai_unit_bindings(
    case: JsonObject,
    *,
    seed: int | None = None,
    include_request_artifacts: bool = False,
) -> list[JsonObject]:
    """Build approved per-unit bindings from deterministic split/catalog data."""

    if _case_domain(case) == "factorization":
        return _factorization_case_bindings(
            case,
            seed=seed,
            include_request_artifacts=include_request_artifacts,
        )
    if case["schema_version"] == "tokenshare.paper_lean_case.v1":
        return _lean_simple_case_bindings(
            case,
            seed=seed,
            include_request_artifacts=include_request_artifacts,
        )
    if case["schema_version"] == LEAN_V2_SCHEMA_VERSION:
        return _lean_lemma_graph_case_bindings(
            case,
            seed=seed,
            include_request_artifacts=include_request_artifacts,
        )
    raise ValueError("paper AI-unit binding case schema is not supported")


def build_ai_unit_binding_from_request(
    *,
    planned_ai_unit_id: str,
    request_body: JsonObject,
    store: ArtifactStore,
    include_request_artifacts: bool,
) -> JsonObject:
    """Recompute a binding from persisted request/snapshot/input artifacts."""

    task_unit_snapshot = _object(request_body.get("task_unit_snapshot"))
    unit_id = _required_string(request_body.get("unit_id"), "request unit_id")
    domain_commitment = _domain_commitment_from_request(
        planned_ai_unit_id=planned_ai_unit_id,
        request_body=request_body,
        task_unit_snapshot=task_unit_snapshot,
        store=store,
    )
    request_artifact_commitment = (
        _request_artifact_commitment_from_request(
            request_body=request_body,
            store=store,
        )
        if include_request_artifacts
        else None
    )
    return build_ai_unit_binding(
        planned_ai_unit_id=planned_ai_unit_id,
        unit_id=unit_id,
        task_unit_snapshot=task_unit_snapshot,
        domain_unit_commitment=domain_commitment,
        request_artifact_commitment=request_artifact_commitment,
    )


def build_ai_unit_binding(
    *,
    planned_ai_unit_id: str,
    unit_id: str,
    task_unit_snapshot: JsonObject,
    domain_unit_commitment: JsonObject,
    request_artifact_commitment: JsonObject | None = None,
) -> JsonObject:
    snapshot_commitment = task_unit_snapshot_commitment(task_unit_snapshot)
    core_body: JsonObject = {
        "schema_version": AI_UNIT_BINDING_SCHEMA_VERSION,
        "planned_ai_unit_id": planned_ai_unit_id,
        "unit_id": unit_id,
        "task_unit_snapshot_commitment": snapshot_commitment,
        "task_unit_snapshot_digest": digest_json(snapshot_commitment),
        "domain_unit_commitment": _json_copy(domain_unit_commitment),
        "domain_unit_commitment_digest": digest_json(domain_unit_commitment),
    }
    body = {
        **core_body,
        "core_binding_digest": digest_json(core_body),
    }
    if request_artifact_commitment is not None:
        body["request_artifact_commitment"] = _json_copy(request_artifact_commitment)
        body["request_artifact_commitment_digest"] = digest_json(
            request_artifact_commitment
        )
    body["binding_digest"] = digest_json(body)
    return body


def validate_ai_unit_binding(binding: JsonObject) -> None:
    if binding.get("schema_version") != AI_UNIT_BINDING_SCHEMA_VERSION:
        raise ValueError("paper execution plan AI unit commitment schema is invalid")
    for field_name in ("planned_ai_unit_id", "unit_id"):
        _required_string(binding.get(field_name), field_name)
    snapshot_commitment = _object(binding.get("task_unit_snapshot_commitment"))
    if binding.get("task_unit_snapshot_digest") != digest_json(snapshot_commitment):
        raise ValueError("paper task unit snapshot commitment digest is invalid")
    domain_commitment = _object(binding.get("domain_unit_commitment"))
    if binding.get("domain_unit_commitment_digest") != digest_json(domain_commitment):
        raise ValueError("paper domain commitment digest is invalid")
    core_body: JsonObject = {
        "schema_version": binding["schema_version"],
        "planned_ai_unit_id": binding["planned_ai_unit_id"],
        "unit_id": binding["unit_id"],
        "task_unit_snapshot_commitment": snapshot_commitment,
        "task_unit_snapshot_digest": binding["task_unit_snapshot_digest"],
        "domain_unit_commitment": domain_commitment,
        "domain_unit_commitment_digest": binding["domain_unit_commitment_digest"],
    }
    if binding.get("core_binding_digest") != digest_json(core_body):
        raise ValueError("paper AI unit core commitment digest is invalid")
    request_commitment = binding.get("request_artifact_commitment")
    if request_commitment is not None:
        request_commitment = _object(request_commitment)
        if binding.get("request_artifact_commitment_digest") != digest_json(
            request_commitment
        ):
            raise ValueError("paper request artifact commitment digest is invalid")
    body_without_digest = {
        key: _json_copy(value)
        for key, value in binding.items()
        if key != "binding_digest"
    }
    if binding.get("binding_digest") != digest_json(body_without_digest):
        raise ValueError("paper AI unit binding digest is invalid")


def factorization_range_plugin_payload(
    *,
    case: JsonObject,
    range_input_body: JsonObject,
) -> JsonObject:
    return {
        "schema_version": FACTOR_SEARCH_RANGE_INPUT_SCHEMA_VERSION,
        "case_id": str(case["case_id"]),
        "commitment_schema_version": DOMAIN_COMMITMENT_SCHEMA_VERSION,
        "logical_range_index": int(range_input_body["child_index"]),
        "range_start": str(range_input_body["range_start"]),
        "range_end": str(range_input_body["range_end"]),
        "coverage_id": str(range_input_body["coverage_id"]),
        "child_index": int(range_input_body["child_index"]),
        "child_count": int(range_input_body["child_count"]),
        "partition_params_digest": str(range_input_body["partition_params_digest"]),
        "range_digest": str(range_input_body["range_digest"]),
        "range_payload_digest": digest_json(range_input_body),
    }


def lean_simple_plugin_payload(
    *,
    case: JsonObject,
    child_logical_key: str,
    child_payload_body: JsonObject,
) -> JsonObject:
    return {
        "schema_version": "lean_proof.subgoal_plugin_payload.v1",
        "case_id": str(case["case_id"]),
        "validator_policy_id": CHECKER_VALIDATOR_POLICY_ID,
        "commitment_schema_version": DOMAIN_COMMITMENT_SCHEMA_VERSION,
        "child_logical_key": child_logical_key,
        "theorem_id": str(child_payload_body["theorem_id"]),
        "theorem_name": str(child_payload_body["theorem_name"]),
        "theorem_payload_digest": str(child_payload_body["payload_digest"]),
        "theorem_payload_body_digest": digest_json(child_payload_body),
    }


def lean_lemma_graph_plugin_payload(
    *,
    case: JsonObject,
    node: JsonObject,
    slot_key: str,
    dependency_path: list[str],
    node_payload_body: JsonObject,
) -> JsonObject:
    node_id = str(node["node_id"])
    return {
        "schema_version": "lean_proof.lemma_graph_node_plugin_payload.v2",
        "summary": {
            "case_id": str(case["case_id"]),
            "node_id": node_id,
            "node_kind": str(node["node_kind"]),
            "depth": int(node["depth"]),
            "root_node_id": str(case["merge_plan_shape"]["root_node_id"]),
            "slot_key": slot_key,
            "dependency_path": list(dependency_path),
            "paper_difficulty": str(case["paper_difficulty"]),
            "topic_family": str(case["topic_family"]),
            "topic_family_version": str(case["topic_family_version"]),
            "construction_rule_id": str(case["construction_rule_id"]),
            "oracle_package_group": str(case["oracle_package_group"]),
            "proof_assembly_shape": str(case["proof_assembly_shape"]),
            "context_digest": _lemma_graph_node_context_digest(
                case_id=str(case["case_id"]),
                node_id=node_id,
                payload_digest=str(node_payload_body["payload_digest"]),
            ),
            "dependency_edges_digest": digest_json(case["dependency_edges"]),
            "theorem_payload_digest": str(node_payload_body["payload_digest"]),
            "theorem_payload_body_digest": digest_json(node_payload_body),
        },
        "validation_requirements": {
            "checker_required": True,
            "environment_ref_required": True,
            "context_digest_required": True,
            "lemma_graph_certificate_node_required": True,
            "dependency_aware_slot_integrity_required": True,
            "ai_decomposition_forbidden": True,
        },
    }


def task_unit_snapshot_commitment(snapshot: JsonObject) -> JsonObject:
    return {
        "schema_version": TASK_UNIT_COMMITMENT_SCHEMA_VERSION,
        "task_id": snapshot.get("task_id"),
        "unit_id": snapshot.get("unit_id"),
        "parent_unit_id": snapshot.get("parent_unit_id"),
        "depth": snapshot.get("depth"),
        "unit_type": snapshot.get("unit_type"),
        "input_refs": _json_copy(snapshot.get("input_refs", {})),
        "plugin_payload": _json_copy(snapshot.get("plugin_payload", {})),
        "metadata": _json_copy(snapshot.get("metadata", {})),
    }


def artifact_body_commitment(
    *,
    artifact_key: str,
    ref: JsonObject,
    body: JsonObject,
) -> JsonObject:
    return {
        "artifact_key": artifact_key,
        "artifact_id": ref.get("artifact_id"),
        "artifact_type": ref.get("artifact_type"),
        "artifact_schema_id": ref.get("artifact_schema_id"),
        "artifact_schema_version": ref.get("artifact_schema_version"),
        "content_hash": ref.get("content_hash"),
        "size_bytes": ref.get("size_bytes"),
        "body_digest": digest_json(body),
    }


def _factorization_case_bindings(
    case: JsonObject,
    *,
    seed: int | None,
    include_request_artifacts: bool,
) -> list[JsonObject]:
    case_id = str(case["case_id"])
    root_input = RootInput(
        target_n=str(case["target_n"]),
        requested_output=REQUESTED_OUTPUT_PRIME_FACTORIZATION,
        case_label=case_id,
        schema_version=ROOT_INPUT_SCHEMA_VERSION,
    )
    root_ref = _artifact_ref_for_json(
        root_input.to_dict(),
        artifact_id=f"paper_root_input_{case_id}",
        artifact_type="RootInput",
        artifact_schema_id="factorization.root_input",
        artifact_schema_version="v1",
        source={"kind": "factorization_paper_adapter", "case_id": case_id},
        metadata={"case_id": case_id},
        created_at=NOW,
    )
    subject = FactorIntegerSubject(
        subject_id=f"paper_factor_subject_{case_id}",
        task_id=f"paper_factorization_{case_id}",
        unit_id=f"paper_factor_root_{case_id}",
        target_n=str(case["target_n"]),
        source_kind="root_input",
        source_ref=root_ref,
        requested_output=REQUESTED_OUTPUT_PRIME_FACTORIZATION,
        created_at=NOW,
    )
    requested_child_count = int(case["split_params"]["requested_child_count"])
    split_plan = build_factorization_split_plan(
        subject=subject,
        canonical_selection_id=f"paper_canonical_root_{case_id}",
        canonical_output_bundle_digest=factorization_digest_json(subject.to_dict()),
        plugin_descriptor_digest=(
            build_factorization_plugin_descriptor().descriptor_digest
        ),
        expansion_scope_hash=factorization_digest_json(
            {"task_id": subject.task_id, "unit_id": subject.unit_id}
        ),
        expansion_decision_id=f"paper_expansion_decision_{case_id}",
        requested_child_count=requested_child_count,
        max_children_per_unit=max(requested_child_count, 1),
        created_at=NOW,
        min_divisor=case["candidate_start"],
        max_divisor=case["candidate_end"],
    )
    bindings: list[JsonObject] = []
    for index, range_input in enumerate(split_plan.partition.ranges):
        range_body = range_input.to_dict()
        child_key = f"range:{range_input.coverage_id}:{range_input.child_index}"
        unit_id = split_plan.child_unit_ids_by_logical_key[child_key]
        range_ref = _artifact_ref_for_json(
            range_body,
            artifact_id=f"paper_range_input_{case_id}_{index}",
            artifact_type="FactorSearchRangeInput",
            artifact_schema_id="factorization.factor_search_range_input",
            artifact_schema_version="v1",
            source={"kind": "factorization_paper_adapter", "case_id": case_id},
            metadata={"case_id": case_id, "child_index": index},
            created_at=NOW,
        )
        snapshot = _factorization_task_unit_snapshot(
            task_id=f"paper_factorization_{case_id}",
            unit_id=unit_id,
            range_input_ref=range_ref,
            case=case,
            range_input_body=range_body,
        )
        planned_ai_unit_id = f"range_{range_input.child_index}"
        request_commitment = (
            _factorization_request_artifact_commitment(
                case=case,
                range_input=range_input,
                range_input_ref=range_ref,
                unit_id=unit_id,
                index=index,
                seed=seed,
            )
            if include_request_artifacts
            else None
        )
        bindings.append(
            build_ai_unit_binding(
                planned_ai_unit_id=planned_ai_unit_id,
                unit_id=unit_id,
                task_unit_snapshot=snapshot,
                domain_unit_commitment=_factorization_domain_commitment(
                    planned_ai_unit_id=planned_ai_unit_id,
                    task_unit_snapshot=snapshot,
                    range_input_body=range_body,
                ),
                request_artifact_commitment=request_commitment,
            )
        )
    return bindings


def _lean_simple_case_bindings(
    case: JsonObject,
    *,
    seed: int | None,
    include_request_artifacts: bool,
) -> list[JsonObject]:
    case_id = str(case["case_id"])
    records = _lean_simple_child_records(case)
    bindings: list[JsonObject] = []
    for index, record in enumerate(records):
        child_key = str(record["child_key"])
        child_payload_body = _object(record["payload_body"])
        child_payload_ref = _object(record["payload_ref"])
        unit_id = f"paper_lean_{case_id}_{_safe_id(child_key)}"
        snapshot = _lean_simple_task_unit_snapshot(
            task_id=f"paper_lean_{case_id}",
            unit_id=unit_id,
            child_payload_ref=child_payload_ref,
            case=case,
            child_logical_key=child_key,
            child_payload_body=child_payload_body,
        )
        planned_ai_unit_id = f"child_{index}"
        request_commitment = (
            _lean_request_artifact_commitment(
                case_id=case_id,
                request_id=f"paper_lean_request_{case_id}_{_safe_id(child_key)}",
                task_id=f"paper_lean_{case_id}",
                unit_id=unit_id,
                payload_key="child_theorem_payload",
                payload_ref=child_payload_ref,
                payload_body=child_payload_body,
                artifact_id=f"paper_lean_prompt_{case_id}_{_safe_id(child_key)}",
                seed=None if seed is None else seed + index,
                metadata={"child_logical_key": child_key},
            )
            if include_request_artifacts
            else None
        )
        bindings.append(
            build_ai_unit_binding(
                planned_ai_unit_id=planned_ai_unit_id,
                unit_id=unit_id,
                task_unit_snapshot=snapshot,
                domain_unit_commitment=_lean_simple_domain_commitment(
                    planned_ai_unit_id=planned_ai_unit_id,
                    task_unit_snapshot=snapshot,
                    child_payload_body=child_payload_body,
                ),
                request_artifact_commitment=request_commitment,
            )
        )
    return bindings


def _lean_lemma_graph_case_bindings(
    case: JsonObject,
    *,
    seed: int | None,
    include_request_artifacts: bool,
) -> list[JsonObject]:
    case_id = str(case["case_id"])
    records = _lean_lemma_graph_node_records(case)
    nodes_by_id = {str(node["node_id"]): node for node in case["lemma_graph"]["nodes"]}
    bindings: list[JsonObject] = []
    for index, node_id in enumerate(_lemma_graph_topological_order(case)):
        record = records[node_id]
        node = nodes_by_id[node_id]
        payload_body = _object(record["payload_body"])
        payload_ref = _object(record["payload_ref"])
        slot_key = f"{node_id}:{PROOF_ARTIFACT_OUTPUT_NAME}"
        dependency_path = _dependency_path_to_node(case, node_id)
        unit_id = f"paper_lean_{case_id}_{_safe_id(node_id)}"
        snapshot = _lean_lemma_task_unit_snapshot(
            task_id=f"paper_lean_{case_id}",
            unit_id=unit_id,
            node_payload_ref=payload_ref,
            case=case,
            node=node,
            slot_key=slot_key,
            dependency_path=dependency_path,
            node_payload_body=payload_body,
        )
        request_commitment = (
            _lean_request_artifact_commitment(
                case_id=case_id,
                request_id=f"paper_lean_request_{case_id}_{_safe_id(node_id)}",
                task_id=f"paper_lean_{case_id}",
                unit_id=unit_id,
                payload_key="lemma_theorem_payload",
                payload_ref=payload_ref,
                payload_body=payload_body,
                artifact_id=f"paper_lean_prompt_{case_id}_{_safe_id(node_id)}",
                seed=None if seed is None else seed + index,
                metadata={
                    "lemma_node_id": node_id,
                    "slot_key": slot_key,
                    "dependency_path": dependency_path,
                    "paper_difficulty": case["paper_difficulty"],
                    "topic_family": case["topic_family"],
                },
            )
            if include_request_artifacts
            else None
        )
        bindings.append(
            build_ai_unit_binding(
                planned_ai_unit_id=node_id,
                unit_id=unit_id,
                task_unit_snapshot=snapshot,
                domain_unit_commitment=_lean_lemma_domain_commitment(
                    planned_ai_unit_id=node_id,
                    task_unit_snapshot=snapshot,
                    node_payload_body=payload_body,
                ),
                request_artifact_commitment=request_commitment,
            )
        )
    return bindings


def _factorization_task_unit_snapshot(
    *,
    task_id: str,
    unit_id: str,
    range_input_ref: JsonObject,
    case: JsonObject,
    range_input_body: JsonObject,
) -> JsonObject:
    return TaskUnit(
        unit_id=unit_id,
        task_id=task_id,
        parent_unit_id=f"paper_factor_root_{case['case_id']}",
        depth=1,
        unit_type=FACTOR_SEARCH_RANGE_TASK_TYPE,
        state=TaskState.PROCESSING,
        input_refs={"range_input": ArtifactRef.from_dict(range_input_ref)},
        canonical_output_refs={},
        required_capabilities={"executor": "ai_api", "bounded_factor_search": True},
        weight=1.0,
        budget_limit=None,
        deadline=None,
        plugin_payload=factorization_range_plugin_payload(
            case=case,
            range_input_body=range_input_body,
        ),
        metadata={"paper_factorization": True, "case_id": case["case_id"]},
        created_at=NOW,
        updated_at=NOW,
    ).to_dict()


def _lean_simple_task_unit_snapshot(
    *,
    task_id: str,
    unit_id: str,
    child_payload_ref: JsonObject,
    case: JsonObject,
    child_logical_key: str,
    child_payload_body: JsonObject,
) -> JsonObject:
    return TaskUnit(
        unit_id=unit_id,
        task_id=task_id,
        parent_unit_id=f"paper_lean_root_{case['case_id']}",
        depth=1,
        unit_type="lean_proof_subgoal",
        state=TaskState.PROCESSING,
        input_refs={"child_theorem_payload": ArtifactRef.from_dict(child_payload_ref)},
        canonical_output_refs={},
        required_capabilities={"executor": "ai_api", "lean_proof": True},
        weight=1.0,
        budget_limit=None,
        deadline=None,
        plugin_payload=lean_simple_plugin_payload(
            case=case,
            child_logical_key=child_logical_key,
            child_payload_body=child_payload_body,
        ),
        metadata={"paper_lean": True, "case_id": case["case_id"]},
        created_at=NOW,
        updated_at=NOW,
    ).to_dict()


def _lean_lemma_task_unit_snapshot(
    *,
    task_id: str,
    unit_id: str,
    node_payload_ref: JsonObject,
    case: JsonObject,
    node: JsonObject,
    slot_key: str,
    dependency_path: list[str],
    node_payload_body: JsonObject,
) -> JsonObject:
    node_id = str(node["node_id"])
    return TaskUnit(
        unit_id=unit_id,
        task_id=task_id,
        parent_unit_id=f"paper_lean_root_{case['case_id']}",
        depth=int(node["depth"]),
        unit_type="lean_proof_lemma_node",
        state=TaskState.PROCESSING,
        input_refs={"lemma_theorem_payload": ArtifactRef.from_dict(node_payload_ref)},
        canonical_output_refs={},
        required_capabilities={"executor": "ai_api", "lean_proof": True},
        weight=1.0,
        budget_limit=None,
        deadline=None,
        plugin_payload=lean_lemma_graph_plugin_payload(
            case=case,
            node=node,
            slot_key=slot_key,
            dependency_path=dependency_path,
            node_payload_body=node_payload_body,
        ),
        metadata={
            "paper_lean": True,
            "case_id": case["case_id"],
            "lemma_node_id": node_id,
            "slot_key": slot_key,
            "dependency_path": dependency_path,
        },
        created_at=NOW,
        updated_at=NOW,
    ).to_dict()


def _factorization_request_artifact_commitment(
    *,
    case: JsonObject,
    range_input: FactorSearchRangeInput,
    range_input_ref: JsonObject,
    unit_id: str,
    index: int,
    seed: int | None,
) -> JsonObject:
    case_id = str(case["case_id"])
    request_id = f"paper_request_{case_id}_{index}"
    instruction = build_factor_search_instruction(
        request_id=request_id,
        unit_id=unit_id,
        range_input=range_input,
    )
    instruction_body = instruction.to_dict()
    instruction_ref = _artifact_ref_for_json(
        instruction_body,
        artifact_id=f"paper_factor_search_instruction_{case_id}_{index}",
        artifact_type="ExecutionInstruction",
        artifact_schema_id="factorization.factor_search_instruction",
        artifact_schema_version="v1",
        source={"kind": "factorization_paper_adapter", "case_id": case_id},
        metadata={"case_id": case_id, "child_index": index},
        created_at=NOW,
    )
    prompt_body = build_factor_search_prompt_package(
        request_id=request_id,
        task_id=f"paper_factorization_{case_id}",
        unit_id=unit_id,
        range_input=range_input,
        instruction=instruction,
        created_at=NOW,
        seed=seed,
    ).to_dict()
    prompt_ref = _artifact_ref_for_json(
        prompt_body,
        artifact_id=f"paper_prompt_{case_id}_{index}",
        artifact_type="PromptPackage",
        artifact_schema_id="phase3.prompt_package",
        artifact_schema_version="v1",
        source={"kind": "factorization_paper_adapter", "case_id": case_id},
        metadata={"case_id": case_id, "child_index": index},
        created_at=NOW,
    )
    return _request_artifact_commitment(
        input_artifacts={
            "range_input": (range_input_ref, range_input.to_dict()),
        },
        prompt_ref=prompt_ref,
        prompt_body=prompt_body,
        instruction_ref=instruction_ref,
        instruction_body=instruction_body,
    )


def _lean_request_artifact_commitment(
    *,
    case_id: str,
    request_id: str,
    task_id: str,
    unit_id: str,
    payload_key: str,
    payload_ref: JsonObject,
    payload_body: JsonObject,
    artifact_id: str,
    seed: int | None,
    metadata: JsonObject,
) -> JsonObject:
    prompt_body = build_lean_proof_candidate_prompt_package(
        request_id=request_id,
        task_id=task_id,
        unit_id=unit_id,
        theorem_payload=LeanTheoremPayload.from_dict(payload_body),
        created_at=NOW,
        seed=seed,
    ).to_dict()
    prompt_ref = _artifact_ref_for_json(
        prompt_body,
        artifact_id=artifact_id,
        artifact_type="PromptPackage",
        artifact_schema_id="phase3.prompt_package",
        artifact_schema_version="v1",
        source={"kind": "lean_paper_adapter", "case_id": case_id},
        metadata=metadata,
        created_at=NOW,
    )
    return _request_artifact_commitment(
        input_artifacts={payload_key: (payload_ref, payload_body)},
        prompt_ref=prompt_ref,
        prompt_body=prompt_body,
        instruction_ref=None,
        instruction_body=None,
    )


def _request_artifact_commitment(
    *,
    input_artifacts: dict[str, tuple[JsonObject, JsonObject]],
    prompt_ref: JsonObject | None,
    prompt_body: JsonObject | None,
    instruction_ref: JsonObject | None,
    instruction_body: JsonObject | None,
) -> JsonObject:
    body: JsonObject = {
        "schema_version": REQUEST_ARTIFACT_COMMITMENT_SCHEMA_VERSION,
        "input_artifacts": {
            key: artifact_body_commitment(
                artifact_key=key,
                ref=ref,
                body=artifact_body,
            )
            for key, (ref, artifact_body) in sorted(input_artifacts.items())
        },
        "prompt_package": (
            artifact_body_commitment(
                artifact_key="prompt_package",
                ref=prompt_ref,
                body=prompt_body,
            )
            if prompt_ref is not None and prompt_body is not None
            else None
        ),
        "execution_instruction": (
            artifact_body_commitment(
                artifact_key="execution_instruction",
                ref=instruction_ref,
                body=instruction_body,
            )
            if instruction_ref is not None and instruction_body is not None
            else None
        ),
    }
    return body


def _request_artifact_commitment_from_request(
    *,
    request_body: JsonObject,
    store: ArtifactStore,
) -> JsonObject:
    input_artifacts: dict[str, tuple[JsonObject, JsonObject]] = {}
    input_refs = _object(request_body.get("input_artifact_refs"))
    for key, ref in sorted(input_refs.items()):
        if not isinstance(ref, dict):
            raise ValueError("paper request input artifact reference is invalid")
        input_artifacts[str(key)] = (ref, _read_artifact_json(store=store, ref=ref))
    prompt_ref = request_body.get("prompt_package_ref")
    prompt_body = (
        _read_artifact_json(store=store, ref=prompt_ref)
        if isinstance(prompt_ref, dict)
        else None
    )
    instruction_ref = request_body.get("execution_instruction_ref")
    instruction_body = (
        _read_artifact_json(store=store, ref=instruction_ref)
        if isinstance(instruction_ref, dict)
        else None
    )
    return _request_artifact_commitment(
        input_artifacts=input_artifacts,
        prompt_ref=prompt_ref if isinstance(prompt_ref, dict) else None,
        prompt_body=prompt_body,
        instruction_ref=instruction_ref if isinstance(instruction_ref, dict) else None,
        instruction_body=instruction_body,
    )


def _domain_commitment_from_request(
    *,
    planned_ai_unit_id: str,
    request_body: JsonObject,
    task_unit_snapshot: JsonObject,
    store: ArtifactStore,
) -> JsonObject:
    input_refs = _object(request_body.get("input_artifact_refs"))
    if "range_input" in input_refs:
        range_input_body = _read_artifact_json(store=store, ref=input_refs["range_input"])
        return _factorization_domain_commitment(
            planned_ai_unit_id=planned_ai_unit_id,
            task_unit_snapshot=task_unit_snapshot,
            range_input_body=range_input_body,
        )
    if "child_theorem_payload" in input_refs:
        child_payload_body = _read_artifact_json(
            store=store,
            ref=input_refs["child_theorem_payload"],
        )
        return _lean_simple_domain_commitment(
            planned_ai_unit_id=planned_ai_unit_id,
            task_unit_snapshot=task_unit_snapshot,
            child_payload_body=child_payload_body,
        )
    if "lemma_theorem_payload" in input_refs:
        node_payload_body = _read_artifact_json(
            store=store,
            ref=input_refs["lemma_theorem_payload"],
        )
        return _lean_lemma_domain_commitment(
            planned_ai_unit_id=planned_ai_unit_id,
            task_unit_snapshot=task_unit_snapshot,
            node_payload_body=node_payload_body,
        )
    raise ValueError("paper domain commitment input artifact is missing")


def _factorization_domain_commitment(
    *,
    planned_ai_unit_id: str,
    task_unit_snapshot: JsonObject,
    range_input_body: JsonObject,
) -> JsonObject:
    plugin_payload = _object(task_unit_snapshot.get("plugin_payload"))
    return {
        "schema_version": DOMAIN_COMMITMENT_SCHEMA_VERSION,
        "domain": "factorization",
        "commitment_kind": "factorization_range.v1",
        "planned_ai_unit_id": planned_ai_unit_id,
        "task_id": task_unit_snapshot.get("task_id"),
        "unit_id": task_unit_snapshot.get("unit_id"),
        "unit_type": task_unit_snapshot.get("unit_type"),
        "case_id": plugin_payload.get("case_id"),
        "logical_range_index": int(range_input_body["child_index"]),
        "range_start": str(range_input_body["range_start"]),
        "range_end": str(range_input_body["range_end"]),
        "target_n": str(range_input_body["target_n"]),
        "coverage_id": str(range_input_body["coverage_id"]),
        "child_index": int(range_input_body["child_index"]),
        "child_count": int(range_input_body["child_count"]),
        "partition_params_digest": str(range_input_body["partition_params_digest"]),
        "range_digest": str(range_input_body["range_digest"]),
        "range_payload_digest": digest_json(range_input_body),
        "snapshot_range_payload_digest": plugin_payload.get("range_payload_digest"),
    }


def _lean_simple_domain_commitment(
    *,
    planned_ai_unit_id: str,
    task_unit_snapshot: JsonObject,
    child_payload_body: JsonObject,
) -> JsonObject:
    plugin_payload = _object(task_unit_snapshot.get("plugin_payload"))
    return {
        "schema_version": DOMAIN_COMMITMENT_SCHEMA_VERSION,
        "domain": "lean_proof",
        "commitment_kind": "lean_simple_child.v1",
        "planned_ai_unit_id": planned_ai_unit_id,
        "task_id": task_unit_snapshot.get("task_id"),
        "unit_id": task_unit_snapshot.get("unit_id"),
        "unit_type": task_unit_snapshot.get("unit_type"),
        "case_id": plugin_payload.get("case_id"),
        "child_logical_key": plugin_payload.get("child_logical_key"),
        "theorem_id": str(child_payload_body["theorem_id"]),
        "theorem_name": str(child_payload_body["theorem_name"]),
        "theorem_payload_digest": str(child_payload_body["payload_digest"]),
        "theorem_payload_body_digest": digest_json(child_payload_body),
        "snapshot_theorem_payload_digest": plugin_payload.get(
            "theorem_payload_digest"
        ),
    }


def _lean_lemma_domain_commitment(
    *,
    planned_ai_unit_id: str,
    task_unit_snapshot: JsonObject,
    node_payload_body: JsonObject,
) -> JsonObject:
    plugin_payload = _object(task_unit_snapshot.get("plugin_payload"))
    summary = _object(plugin_payload.get("summary"))
    return {
        "schema_version": DOMAIN_COMMITMENT_SCHEMA_VERSION,
        "domain": "lean_proof",
        "commitment_kind": "lean_lemma_dag_node.v1",
        "planned_ai_unit_id": planned_ai_unit_id,
        "task_id": task_unit_snapshot.get("task_id"),
        "unit_id": task_unit_snapshot.get("unit_id"),
        "unit_type": task_unit_snapshot.get("unit_type"),
        "case_id": summary.get("case_id"),
        "node_id": summary.get("node_id"),
        "slot_key": summary.get("slot_key"),
        "dependency_path": _json_copy(summary.get("dependency_path", [])),
        "context_digest": summary.get("context_digest"),
        "dependency_edges_digest": summary.get("dependency_edges_digest"),
        "theorem_id": str(node_payload_body["theorem_id"]),
        "theorem_name": str(node_payload_body["theorem_name"]),
        "theorem_payload_digest": str(node_payload_body["payload_digest"]),
        "theorem_payload_body_digest": digest_json(node_payload_body),
        "snapshot_theorem_payload_digest": summary.get("theorem_payload_digest"),
    }


def _lean_simple_child_records(case: JsonObject) -> tuple[JsonObject, ...]:
    cache_key = digest_json(case)
    cached = _LEAN_SIMPLE_SPLIT_CACHE.get(cache_key)
    if cached is not None:
        return tuple(_json_copy(item) for item in cached)

    from tokenshare.experiments.paper_catalog import (
        default_lean_paper_environment_manifest,
        lean_theorem_payload_from_case,
    )

    case_id = str(case["case_id"])
    with tempfile.TemporaryDirectory(prefix="tokenshare_paper_unit_commitment_") as root:
        store = ArtifactStore(Path(root))
        environment_manifest = default_lean_paper_environment_manifest()
        parent_payload = lean_theorem_payload_from_case(case)
        parent_payload_ref = store.save_json(
            parent_payload.to_dict(),
            artifact_id=f"paper_lean_parent_payload_{case_id}",
            artifact_type="LeanTheoremPayload",
            artifact_schema_id="lean_proof.theorem_payload",
            artifact_schema_version="v1",
            source={"kind": "lean_paper_adapter", "case_id": case_id},
            metadata={"theorem_name": parent_payload.theorem_name},
            created_at=NOW,
        )
        split_report = run_lean_split_helper(
            LeanSplitHelperRequest(
                request_id=f"paper_lean_split_request_{case_id}",
                theorem_payload_ref=parent_payload_ref,
                environment_ref=build_lean_environment_ref(environment_manifest),
                timeout_seconds=int(parent_payload.resource_limits["timeout_seconds"]),
                max_output_bytes=int(parent_payload.resource_limits["max_output_bytes"]),
                created_at=NOW,
            ),
            artifact_store=store,
            environment_manifest=environment_manifest,
        )
        if split_report.certificate is None or split_report.certificate_ref is None:
            raise ValueError("Lean simple AI-unit commitment requires split certificate")
        split_plan = build_lean_split_plan(
            split_report=split_report,
            artifact_store=store,
            task_id=f"paper_lean_{case_id}",
            parent_unit_id=f"paper_lean_root_{case_id}",
            canonical_selection_id=f"paper_lean_canonical_root_{case_id}",
            canonical_output_bundle_digest=parent_payload_ref.content_hash,
            plugin_descriptor_digest=build_lean_proof_plugin_descriptor().descriptor_digest,
            expansion_scope_hash=lean_digest_json(
                {
                    "case_id": case_id,
                    "parent_payload_digest": parent_payload_ref.content_hash,
                }
            ),
            expansion_decision_id=f"paper_lean_expansion_decision_{case_id}",
            created_at=NOW,
        )
        records = []
        for child_key, child_payload_ref in sorted(
            split_plan.child_payload_refs_by_logical_key.items()
        ):
            child_payload_body = _read_artifact_json(
                store=store,
                ref=child_payload_ref.to_dict(),
            )
            records.append(
                {
                    "child_key": child_key,
                    "payload_ref": child_payload_ref.to_dict(),
                    "payload_body": child_payload_body,
                }
            )
    _LEAN_SIMPLE_SPLIT_CACHE[cache_key] = tuple(_json_copy(item) for item in records)
    return tuple(records)


def _lean_lemma_graph_node_records(case: JsonObject) -> dict[str, JsonObject]:
    case_id = str(case["case_id"])
    records: dict[str, JsonObject] = {}
    split_request_id = f"paper_lean_lemma_graph_split_request_{case_id}"
    for node in case["lemma_graph"]["nodes"]:
        node_id = str(node["node_id"])
        payload = _lean_lemma_graph_payload_from_body(
            node["theorem_payload"],
            case_id=case_id,
            node_id=node_id,
        )
        ref = _artifact_ref_for_json(
            payload.to_dict(),
            artifact_id=(
                f"{_safe_id(split_request_id)}_"
                f"{_safe_id(f'lemma_node_{node_id}.json')}"
            ),
            artifact_type="LeanLemmaGraphNodePayload",
            artifact_schema_id="lean_proof.lemma_graph_node_payload",
            artifact_schema_version="v2",
            source={
                "kind": "lean_lemma_graph_certificate",
                "request_id": split_request_id,
            },
            metadata={
                "node_id": node_id,
                "root_node_id": case["merge_plan_shape"]["root_node_id"],
                "context_digest": _lemma_graph_node_context_digest(
                    case_id=case_id,
                    node_id=node_id,
                    payload_digest=str(payload.payload_digest),
                ),
            },
            created_at=NOW,
        )
        records[node_id] = {
            "payload_ref": ref,
            "payload_body": payload.to_dict(),
        }
    return records


def _lean_lemma_graph_payload_from_body(
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


def _lemma_graph_node_context_digest(
    *,
    case_id: str,
    node_id: str,
    payload_digest: str,
) -> str:
    return lean_digest_json(
        {
            "case_id": case_id,
            "node_id": node_id,
            "theorem_payload_digest": payload_digest,
        }
    )


def _lemma_graph_topological_order(case: JsonObject) -> list[str]:
    declared_order = case.get("merge_plan_shape", {}).get("dependency_order")
    node_ids = [str(node["node_id"]) for node in case["lemma_graph"]["nodes"]]
    if isinstance(declared_order, list) and declared_order:
        order = [str(item) for item in declared_order]
        if set(order) == set(node_ids) and len(order) == len(node_ids):
            return order
    order_index = {node_id: index for index, node_id in enumerate(node_ids)}
    incoming_by_target: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for edge in case["dependency_edges"]:
        incoming_by_target[str(edge["target_node_id"])].append(
            str(edge["source_node_id"])
        )
    for incoming in incoming_by_target.values():
        incoming.sort(key=lambda node_id: order_index[node_id])
    visiting: set[str] = set()
    visited: set[str] = set()
    ordered: list[str] = []

    def visit(node_id: str) -> None:
        if node_id in visited:
            return
        if node_id in visiting:
            raise ValueError("Lean lemma graph dependency cycle detected")
        visiting.add(node_id)
        for source_node_id in incoming_by_target.get(node_id, []):
            visit(source_node_id)
        visiting.remove(node_id)
        visited.add(node_id)
        ordered.append(node_id)

    visit(str(case["merge_plan_shape"]["root_node_id"]))
    for node_id in node_ids:
        visit(node_id)
    return ordered


def _dependency_path_to_node(case: JsonObject, node_id: str) -> list[str]:
    incoming_by_target: dict[str, list[str]] = {
        str(node["node_id"]): [] for node in case["lemma_graph"]["nodes"]
    }
    node_order = {
        str(node["node_id"]): index
        for index, node in enumerate(case["lemma_graph"]["nodes"])
    }
    for edge in case["dependency_edges"]:
        incoming_by_target[str(edge["target_node_id"])].append(
            str(edge["source_node_id"])
        )
    for sources in incoming_by_target.values():
        sources.sort(key=lambda item: node_order[item])
    path: list[str] = []
    seen: set[str] = set()

    def visit(current_node_id: str) -> None:
        if current_node_id in seen:
            return
        for source_node_id in incoming_by_target.get(current_node_id, []):
            visit(source_node_id)
        seen.add(current_node_id)
        path.append(current_node_id)

    visit(node_id)
    return path


def _artifact_ref_for_json(
    body: JsonObject,
    *,
    artifact_id: str,
    artifact_type: str,
    artifact_schema_id: str,
    artifact_schema_version: str,
    source: JsonObject,
    metadata: JsonObject,
    created_at: str,
) -> JsonObject:
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return ArtifactRef(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        uri=f"artifacts/{artifact_id}",
        content_hash=f"sha256:{sha256(encoded).hexdigest()}",
        size_bytes=len(encoded),
        media_type="application/json",
        artifact_schema_id=artifact_schema_id,
        artifact_schema_version=artifact_schema_version,
        source=source,
        metadata=metadata,
        created_at=created_at,
    ).to_dict()


def _read_artifact_json(*, store: ArtifactStore, ref: Any) -> JsonObject:
    artifact_ref = ArtifactRef.from_dict(ref)
    if not store.verify(artifact_ref):
        raise ValueError("paper committed artifact integrity verification failed")
    body = json.loads(store.read_bytes(artifact_ref).decode("utf-8"))
    if not isinstance(body, dict):
        raise ValueError("paper committed artifact body must be an object")
    return body


def _case_domain(case: JsonObject) -> str:
    if case["schema_version"] == "tokenshare.paper_factorization_case.v1":
        return "factorization"
    return "lean_proof"


def _safe_id(value: str) -> str:
    return "".join(
        character if character.isalnum() or character == "_" else "_"
        for character in value
    )


def _object(value: Any) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError("paper AI-unit commitment expected a JSON object")
    return dict(value)


def _required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"paper AI-unit commitment missing {field_name}")
    return value


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))
