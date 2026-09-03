"""Verify frozen corpus assets and the current V1 experiment authority."""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from math import ceil
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


DEFAULT_FROZEN_SHA = "bb5e637785afb6bd5743e4d89af4c02ab0736204"
BASELINE_JSON_PATH = "Doc/SlimV2/2026-08-27-experiments-clean-extraction-baseline.json"
MANIFEST_PATH = "benchmarks/experiments/manifest.v1.json"

EXPERIMENT_ORDER = ("exp1", "exp2", "exp3", "exp4", "exp5")
EXP3_FAULT_TYPES = (
    "false_positive",
    "false_negative",
    "no_return",
    "late_submission",
    "executor_error",
)
EXP4_MODES = (
    "FULL",
    "NO_VERIFICATION",
    "NO_PARSER_POLICY",
    "NO_REQUEUE",
    "NO_MERGE_GATE",
    "NO_VERIFICATION__NO_PARSER_POLICY",
    "NO_VERIFICATION__NO_REQUEUE",
    "NO_VERIFICATION__NO_MERGE_GATE",
    "NO_PARSER_POLICY__NO_REQUEUE",
    "NO_PARSER_POLICY__NO_MERGE_GATE",
    "NO_REQUEUE__NO_MERGE_GATE",
)
EXP4_NO_REQUEUE_MODES = frozenset(mode for mode in EXP4_MODES if "NO_REQUEUE" in mode)
EXP4_DISABLED_NAMES = {
    "NO_VERIFICATION": "verification",
    "NO_PARSER_POLICY": "parser_policy",
    "NO_REQUEUE": "requeue",
    "NO_MERGE_GATE": "merge_gate",
}
CHALLENGE_FAMILIES = (
    "INVALID_PARSED_CANDIDATE",
    "PARSER_REQUIRED_CANONICAL_JSON",
    "RECOVERABLE_NO_RETURN",
    "REQUIRED_CHILD_DELAY",
)
EXP5_PROVIDER_ENTRIES = {
    "zai-org/GLM-5.2": "glm_5_2_exp5_v3",
    "Qwen/Qwen3-14B": "qwen3_14b_exp5_v3",
    "MiniMaxAI/MiniMax-M2.5": "minimax_m2_5_exp5_v3",
}


JsonObject = dict[str, Any]


@dataclass(frozen=True)
class Inventory:
    conditions: tuple[JsonObject, ...]
    roots: tuple[JsonObject, ...]
    references: tuple[JsonObject, ...]
    challenges: tuple[JsonObject, ...]


def verify_all(
    repo_root: Path,
    *,
    frozen_sha: str = DEFAULT_FROZEN_SHA,
) -> JsonObject:
    repo_root = repo_root.resolve()
    manifest = _read_json(repo_root / MANIFEST_PATH)
    _verify_manifest_header(manifest, frozen_sha)
    asset_summary = _verify_assets(repo_root, manifest, frozen_sha)
    sidecar_summary = _verify_semantic_sidecar(repo_root, manifest)
    inventory_summary = _verify_profile_inventory(repo_root, manifest)
    summary: JsonObject = {
        "status": "ok",
        "assets_checked": asset_summary,
        "semantic_sidecar": sidecar_summary,
        "inventory": inventory_summary,
    }
    return summary


def _verify_manifest_header(manifest: Mapping[str, Any], frozen_sha: str) -> None:
    if manifest.get("schema_version") != (
        "tokenshare.experiments.authoritative_corpus_manifest.v1"
    ):
        raise ValueError("unsupported corpus manifest schema")
    if manifest.get("frozen_commit_sha") != frozen_sha:
        raise ValueError("corpus manifest frozen commit mismatch")
    if manifest.get("baseline_json_path") != BASELINE_JSON_PATH:
        raise ValueError("corpus manifest baseline path mismatch")
    if manifest.get("hash_algorithm") != "sha256":
        raise ValueError("corpus manifest hash algorithm mismatch")


def _verify_assets(
    repo_root: Path,
    manifest: Mapping[str, Any],
    frozen_sha: str,
) -> JsonObject:
    assets = _require_list(manifest, "assets")
    checked = 0
    for asset in assets:
        if not isinstance(asset, dict):
            raise ValueError("asset manifest row must be an object")
        source = _require_text(asset, "frozen_path")
        target = _require_text(asset, "public_path")
        data = (repo_root / target).read_bytes()
        blob = _git_blob(repo_root, frozen_sha, source)
        if data != blob:
            raise ValueError(f"asset byte drift: {target}")
        expected_sha = _require_text(asset, "sha256")
        if sha256(data).hexdigest() != expected_sha:
            raise ValueError(f"asset sha mismatch: {target}")
        if len(data) != int(asset["byte_size"]):
            raise ValueError(f"asset byte size mismatch: {target}")
        if "record_count" in asset:
            actual = _asset_record_count(repo_root / target, _require_text(asset, "format"))
            if actual != int(asset["record_count"]):
                raise ValueError(f"asset record count mismatch: {target}")
        checked += 1
    if checked != 20:
        raise ValueError(f"expected 20 authoritative corpus assets, found {checked}")
    return {"asset_count": checked}


def _verify_semantic_sidecar(
    repo_root: Path,
    manifest: Mapping[str, Any],
) -> JsonObject:
    section = _require_mapping(manifest, "semantic_authority")
    sidecar_path = repo_root / _require_text(section, "sidecar_public_path")
    sidecar = _read_json(sidecar_path)
    mapping = _require_mapping(section, "logical_to_physical_paths")
    raw_files = _require_mapping(_require_mapping(sidecar, "authority"), "raw_files")
    raw_logical = {
        "benchmarks/paper/lean_catalog.v1.jsonl",
        "benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl",
        "benchmarks/paper/lean_checker_preflight.v1.json",
    }
    if set(raw_files) != raw_logical:
        raise ValueError("semantic authority raw logical path coverage mismatch")
    for logical in raw_logical:
        physical = _require_text(mapping, logical)
        digest = "sha256:" + sha256((repo_root / physical).read_bytes()).hexdigest()
        if raw_files[logical] != digest:
            raise ValueError(f"semantic authority raw byte drift: {logical}")

    projection = _require_mapping(sidecar, "semantic_projection")
    environment = _require_mapping(_require_mapping(projection, "environment"), "files")
    checker = _require_mapping(_require_mapping(projection, "checker"), "files")
    expected_environment_keys = {
        key for key in mapping if key.startswith("fixtures/lean_proof_project/")
    }
    if set(environment) != expected_environment_keys:
        raise ValueError("semantic environment logical path coverage mismatch")
    for logical in expected_environment_keys:
        physical = _require_text(mapping, logical)
        if environment[logical] != _normalized_text_digest(repo_root / physical):
            raise ValueError(f"semantic environment drift: {logical}")
    if checker != {
        "src/tokenshare/plugins/lean_proof/checker.py": _normalized_text_digest(
            repo_root / "src/tokenshare/plugins/lean_proof/checker.py"
        )
    }:
        raise ValueError("semantic checker projection drift")
    return {
        "raw_logical_paths": len(raw_logical),
        "environment_logical_paths": len(expected_environment_keys),
    }


def _verify_profile_inventory(
    repo_root: Path,
    manifest: Mapping[str, Any],
) -> JsonObject:
    factor = _cases_by_id(repo_root / "benchmarks/experiments/factorization_catalog.v2.jsonl")
    lean = _cases_by_id(repo_root / "benchmarks/experiments/lean_lemma_graph_catalog.v1.jsonl")
    profiles = _require_mapping(manifest, "profiles")
    full_inventory: Inventory | None = None
    profile_summaries: dict[str, JsonObject] = {}
    for profile_id in ("full", "representative"):
        profile_manifest = _require_mapping(profiles, profile_id)
        selection_ids = _verify_selection_sections(
            profile_id, profile_manifest, factor, lean
        )
        inventory = _build_inventory(profile_manifest, factor, lean)
        for experiment_id in EXPERIMENT_ORDER:
            inventory_case_ids = tuple(
                sorted(
                    {
                        str(root["case_id"])
                        for root in inventory.roots
                        if root["experiment_id"] == experiment_id
                    }
                )
            )
            if inventory_case_ids != selection_ids[experiment_id]:
                raise ValueError(
                    f"{profile_id} {experiment_id} inventory case IDs mismatch"
                )
        _verify_profile_objects(
            profile_id,
            profile_manifest,
            inventory,
        )
        profile_summaries[profile_id] = {
            "conditions": len(inventory.conditions),
            "roots": len(inventory.roots),
            "references": len(inventory.references),
            "challenges": len(inventory.challenges),
        }
        if profile_id == "full":
            full_inventory = inventory
    assert full_inventory is not None
    root_identity = _verify_identity_block(
        full_inventory.roots,
        manifest["identity_expectations"]["full_root_identities"],
    )
    reference_identity = _verify_identity_block(
        full_inventory.references,
        manifest["identity_expectations"]["full_reference_identities"],
    )
    return {
        "profiles": profile_summaries,
        "full_root_identities": root_identity,
        "full_reference_identities": reference_identity,
    }


def _verify_profile_objects(
    profile_id: str,
    profile_manifest: Mapping[str, Any],
    inventory: Inventory,
) -> None:
    expected_conditions = _require_mapping(profile_manifest, "condition_objects")
    expected_challenges = _require_mapping(profile_manifest, "challenge_objects")
    expected_provider = _require_mapping(profile_manifest, "provider_bound_objects")
    if _items_sha256(inventory.conditions) != expected_conditions["items_sha256"]:
        raise ValueError(f"{profile_id} condition object digest mismatch")
    if len(inventory.conditions) != expected_conditions["count"]:
        raise ValueError(f"{profile_id} condition count mismatch")
    if _items_sha256(inventory.challenges) != expected_challenges["items_sha256"]:
        raise ValueError(f"{profile_id} challenge object digest mismatch")
    if len(inventory.challenges) != expected_challenges["count"]:
        raise ValueError(f"{profile_id} challenge count mismatch")
    provider_bounds = _provider_bound_objects(inventory)
    if _items_sha256(provider_bounds) != expected_provider["items_sha256"]:
        raise ValueError(f"{profile_id} provider bound object digest mismatch")
    if len(provider_bounds) != expected_provider["count"]:
        raise ValueError(f"{profile_id} provider bound object count mismatch")
    if sum(row["provider_call_upper"] for row in provider_bounds) != (
        expected_provider["total_provider_call_upper"]
    ):
        raise ValueError(f"{profile_id} provider upper mismatch")
    profile_object = _require_mapping(profile_manifest, "profile_object")
    if profile_manifest["profile_object_sha256"] != sha256(
        _canonical_bytes(profile_object)
    ).hexdigest():
        raise ValueError(f"{profile_id} profile object digest mismatch")
    if profile_manifest["plan_object_sha256"] != sha256(
        _canonical_bytes(_plan_object(profile_id, inventory))
    ).hexdigest():
        raise ValueError(f"{profile_id} plan object digest mismatch")
    expected_summary = _require_mapping(profile_manifest, "summary")
    actual_summary = {
        "condition_counts": dict(
            Counter(item["experiment_id"] for item in inventory.conditions)
        ),
        "execution_root_count": len(inventory.roots) + len(inventory.references),
        "paper_root_count": len(inventory.roots),
        "paper_root_counts": dict(
            Counter(item["experiment_id"] for item in inventory.roots)
        ),
        "reference_root_counts": dict(
            Counter(item["experiment_id"] for item in inventory.references)
        ),
    }
    if actual_summary != expected_summary:
        raise ValueError(f"{profile_id} summary mismatch")


def _verify_selection_sections(
    profile_id: str,
    profile_manifest: Mapping[str, Any],
    factor: Mapping[str, JsonObject],
    lean: Mapping[str, JsonObject],
) -> dict[str, tuple[str, ...]]:
    selections = _require_mapping(profile_manifest, "selection_case_ids")
    selection_ids: dict[str, tuple[str, ...]] = {}
    for experiment_id in EXPERIMENT_ORDER:
        section = _require_mapping(selections, experiment_id)
        ordered = tuple(str(item) for item in _require_list(section, "ordered_case_ids"))
        selection_ids[experiment_id] = ordered
        if len(ordered) != int(section["count"]):
            raise ValueError(f"{profile_id} {experiment_id} selection count mismatch")
        if len(ordered) != len(set(ordered)):
            raise ValueError(f"{profile_id} {experiment_id} selection duplicates")
        missing = [case_id for case_id in ordered if case_id not in factor and case_id not in lean]
        if missing:
            raise ValueError(
                f"{profile_id} {experiment_id} selection missing cases: {missing[:3]}"
            )
        if _items_sha256(list(ordered)) != section["ordered_case_ids_sha256"]:
            raise ValueError(f"{profile_id} {experiment_id} selection sha mismatch")

    build_sections = _require_mapping(profile_manifest, "inventory_build_case_ids")
    build_ids: dict[str, tuple[str, ...]] = {}
    for name, raw_ids in build_sections.items():
        if not isinstance(raw_ids, list):
            raise ValueError(f"{profile_id} {name} inventory build IDs must be a list")
        ordered = tuple(str(item) for item in raw_ids)
        build_ids[str(name)] = ordered
        if len(ordered) != len(set(ordered)):
            raise ValueError(f"{profile_id} {name} inventory build duplicates")
        catalog = (
            factor
            if str(name).endswith("_factorization")
            else lean
            if str(name).endswith("_lean")
            else factor | lean
        )
        missing = [case_id for case_id in ordered if case_id not in catalog]
        if missing:
            raise ValueError(
                f"{profile_id} {name} inventory build missing cases: {missing[:3]}"
            )

    if profile_id == "full":
        build_names_by_experiment = {
            "exp1": ("exp1_factorization", "exp1_lean"),
            "exp2": ("exp2_factorization",),
            "exp3": ("exp3_factorization", "exp3_lean"),
            "exp4": ("exp4_factorization", "exp4_lean"),
            "exp5": ("exp5_factorization", "exp5_lean"),
        }
        build_selection_ids = {
            experiment_id: tuple(
                case_id
                for name in names
                for case_id in build_ids[name]
            )
            for experiment_id, names in build_names_by_experiment.items()
        }
    else:
        representative = build_ids["representative"]
        build_selection_ids = {
            "exp1": representative,
            "exp2": representative[:2],
            "exp4": representative,
        }
    for experiment_id, ordered in build_selection_ids.items():
        if tuple(sorted(ordered)) != selection_ids[experiment_id]:
            raise ValueError(
                f"{profile_id} {experiment_id} inventory build selection mismatch"
            )
    return selection_ids


def _verify_identity_block(
    roots: Iterable[Mapping[str, Any]],
    manifest_block: Mapping[str, Any],
) -> JsonObject:
    if manifest_block["schema_version"] != "tokenshare.experiments.identity_set.v1":
        raise ValueError("identity schema version mismatch")
    fields = tuple(_require_list(manifest_block, "identity_fields"))
    sort_fields = tuple(_require_list(manifest_block, "sort_fields"))
    items = [
        {field: root[field] for field in fields}
        for root in roots
    ]
    items.sort(key=lambda item: tuple(item[field] for field in sort_fields))
    canonical = _canonical_bytes(items)
    actual_sha = sha256(canonical).hexdigest()
    if items != _require_list(manifest_block, "items"):
        raise ValueError("identity items mismatch")
    if len(items) != manifest_block["count"]:
        raise ValueError("identity count mismatch")
    if len(canonical) != manifest_block["canonical_byte_length"]:
        raise ValueError("identity canonical byte length mismatch")
    if actual_sha != manifest_block["items_sha256"]:
        raise ValueError("identity sha mismatch")
    return {
        "count": len(items),
        "canonical_byte_length": len(canonical),
        "items_sha256": actual_sha,
    }


def _build_inventory(
    profile_manifest: Mapping[str, Any],
    factor: Mapping[str, JsonObject],
    lean: Mapping[str, JsonObject],
) -> Inventory:
    selections = _require_mapping(profile_manifest, "selection_case_ids")
    build_ids = _require_mapping(profile_manifest, "inventory_build_case_ids")
    profile = _require_mapping(profile_manifest, "profile_object")
    controls = _require_mapping(profile, "experiments")
    profile_id = _require_text(profile, "profile_id")
    conditions: list[JsonObject] = []
    roots: list[JsonObject] = []
    references: list[JsonObject] = []

    def selected(experiment_id: str) -> tuple[str, ...]:
        section = _require_mapping(selections, experiment_id)
        return tuple(str(item) for item in _require_list(section, "ordered_case_ids"))

    def build_selected(name: str) -> tuple[str, ...]:
        return tuple(str(item) for item in _require_list(build_ids, name))

    def add_condition(experiment_id: str, **dimensions: Any) -> JsonObject:
        item_controls = _require_mapping(controls, experiment_id)
        item = {
            "condition_id": _canonical_condition_id(experiment_id, dimensions),
            "experiment_id": experiment_id,
            "domain": str(dimensions.get("domain", "mixed")),
            "difficulty": dimensions.get("difficulty"),
            "topic_family": dimensions.get("topic"),
            "repeat_id": int(dimensions.get("repeat", 0)),
            "worker_count": int(
                dimensions.get("worker", item_controls["worker_counts"][0])
            ),
            "max_retries": int(item_controls["max_retries"]),
            "continue_after_terminal_child_failure": bool(
                item_controls["continue_after_terminal_child_failure"]
            ),
            "source_repeat_id": item_controls["source_repeat_id"],
            "position_stratum": dimensions.get("position"),
            "fault_type": dimensions.get("fault"),
            "fault_rate_percent": dimensions.get("rate"),
            "dead_worker_count": dimensions.get("dead"),
            "kill_progress_percent": dimensions.get("progress"),
            "mode": dimensions.get("mode"),
            "model_id": dimensions.get("model"),
        }
        conditions.append(item)
        return item

    def add_root(
        condition: Mapping[str, Any],
        case_id: str,
        *,
        is_paper_denominator: bool = True,
        challenge_plan_id: str | None = None,
    ) -> None:
        roots.append(
            _root_row(
                condition,
                case_id,
                len(roots),
                factor,
                lean,
                is_paper_denominator=is_paper_denominator,
                challenge_plan_id=challenge_plan_id,
            )
        )

    if profile_id == "full":
        exp1_ids = build_selected("exp1_factorization") + build_selected("exp1_lean")
    else:
        exp1_ids = build_selected("representative")
    exp1_groups: dict[tuple[str, str, str | None], list[str]] = {}
    for case_id in exp1_ids:
        domain, difficulty, topic, _count = _case_data(case_id, factor, lean)
        exp1_groups.setdefault((domain, difficulty, topic), []).append(case_id)
    for (domain, difficulty, topic), case_ids in exp1_groups.items():
        dimensions: dict[str, Any] = {
            "domain": domain,
            "difficulty": difficulty,
            "repeat": 0,
        }
        if topic is not None:
            dimensions["topic"] = topic
        condition = add_condition("exp1", **dimensions)
        for case_id in case_ids:
            add_root(condition, case_id)

    exp2_ids = (
        build_selected("exp2_factorization")
        if profile_id == "full"
        else build_selected("representative")[:2]
    )
    for worker in _require_mapping(controls, "exp2")["worker_counts"]:
        for repeat_id in (0, 1):
            by_position: dict[str, list[str]] = {}
            for case_id in exp2_ids:
                position = str(factor[case_id]["factor_position_quantile"])
                by_position.setdefault(position, []).append(case_id)
            for position, case_ids in by_position.items():
                condition = add_condition(
                    "exp2",
                    worker=worker,
                    repeat=repeat_id,
                    position=position,
                    domain="factorization",
                )
                for case_id in case_ids:
                    add_root(condition, case_id)

    if profile_id == "full":
        exp3_ids = build_selected("exp3_factorization") + build_selected("exp3_lean")
        factor_by_difficulty: dict[str, list[str]] = {}
        lean_by_topic: dict[str, list[str]] = {}
        for case_id in exp3_ids:
            if case_id in factor:
                factor_by_difficulty.setdefault(
                    str(factor[case_id]["paper_difficulty"]), []
                ).append(case_id)
            else:
                lean_by_topic[str(lean[case_id]["topic_family"])] = [case_id]
        rate_groups = (
            [
                ("factorization", key, None, value, (1, 5, 10, 25, 50, 100))
                for key, value in factor_by_difficulty.items()
            ]
            + [
                ("lean", None, key, value, (10, 50, 100))
                for key, value in lean_by_topic.items()
            ]
        )
        for domain, difficulty, topic, case_ids, rates in rate_groups:
            for fault in EXP3_FAULT_TYPES:
                for rate in rates:
                    for repeat_id in (0, 1):
                        dimensions = {
                            "domain": domain,
                            "fault": fault,
                            "rate": rate,
                            "repeat": repeat_id,
                        }
                        if difficulty is not None:
                            dimensions["difficulty"] = difficulty
                        if topic is not None:
                            dimensions["topic"] = topic
                        condition = add_condition("exp3", **dimensions)
                        for case_id in case_ids:
                            add_root(condition, case_id)
        death_groups = [
            ("factorization", key, None, value)
            for key, value in factor_by_difficulty.items()
        ] + [
            ("lean", None, key, value)
            for key, value in lean_by_topic.items()
        ]
        for domain, difficulty, topic, case_ids in death_groups:
            for dead in (1, 3):
                for progress in (25, 50, 75):
                    for repeat_id in (0, 1):
                        dimensions = {
                            "domain": domain,
                            "dead": dead,
                            "progress": progress,
                            "repeat": repeat_id,
                        }
                        if difficulty is not None:
                            dimensions["difficulty"] = difficulty
                        if topic is not None:
                            dimensions["topic"] = topic
                        condition = add_condition("exp3", **dimensions)
                        for case_id in case_ids:
                            add_root(condition, case_id)
        reference_ids = exp3_ids
        reference_repeats = (0, 1)
    else:
        factor_case = "factor_v2_hard_145"
        lean_case = "lean_v2_simple_pure_logic_direct_prop_01"
        for fault in EXP3_FAULT_TYPES:
            condition = add_condition(
                "exp3",
                domain="factorization",
                difficulty="hard",
                fault=fault,
                rate=100,
                repeat=0,
            )
            add_root(condition, factor_case)
        for dead, progress in ((1, 25), (3, 75)):
            condition = add_condition(
                "exp3",
                domain="factorization",
                difficulty="hard",
                dead=dead,
                progress=progress,
                repeat=0,
            )
            add_root(condition, factor_case)
        condition = add_condition(
            "exp3",
            domain="lean",
            topic="pure_logic",
            fault="false_positive",
            rate=100,
            repeat=0,
        )
        add_root(condition, lean_case)
        reference_ids = (factor_case, lean_case)
        reference_repeats = (0,)

    for repeat_id in reference_repeats:
        for case_id in reference_ids:
            domain, difficulty, topic, _count = _case_data(case_id, factor, lean)
            dimensions = {"domain": domain}
            if domain == "factorization":
                dimensions["difficulty"] = difficulty
            else:
                dimensions["topic"] = topic
            dimensions["repeat"] = repeat_id
            item_controls = _require_mapping(controls, "exp3")
            ref_condition = {
                "condition_id": _condition_id("exp3-reference", **dimensions),
                "experiment_id": "exp3",
                "domain": domain,
                "difficulty": difficulty,
                "topic_family": topic,
                "repeat_id": repeat_id,
                "worker_count": 10,
                "max_retries": int(item_controls["max_retries"]),
                "continue_after_terminal_child_failure": False,
                "source_repeat_id": 0,
                "position_stratum": None,
                "fault_type": None,
                "fault_rate_percent": None,
                "dead_worker_count": None,
                "kill_progress_percent": None,
                "mode": None,
                "model_id": None,
            }
            references.append(
                _root_row(
                    ref_condition,
                    case_id,
                    len(references),
                    factor,
                    lean,
                    is_paper_denominator=False,
                )
            )

    exp4_ids = (
        build_selected("exp4_factorization") + build_selected("exp4_lean")
        if profile_id == "full"
        else build_selected("representative")
    )
    challenges = _challenge_plans(profile_id, exp4_ids, factor, lean, profile_manifest)
    plans_by_repeat: dict[int, list[JsonObject]] = {}
    for plan in challenges:
        plans_by_repeat.setdefault(int(plan["repeat_id"]), []).append(plan)
    for mode in EXP4_MODES:
        for repeat_id, plans in plans_by_repeat.items():
            condition = add_condition("exp4", mode=mode, repeat=repeat_id, domain="mixed")
            for plan in plans:
                add_root(
                    condition,
                    str(plan["case_id"]),
                    challenge_plan_id=str(plan["challenge_plan_id"]),
                )

    exp5_ids = (
        build_selected("exp5_factorization") + build_selected("exp5_lean")
        if profile_id == "full"
        else ("factor_v2_hard_145",)
    )
    exp5_groups: dict[tuple[str, str | None], list[str]] = {}
    for case_id in exp5_ids:
        domain, _difficulty, topic, _count = _case_data(case_id, factor, lean)
        exp5_groups.setdefault((domain, topic), []).append(case_id)
    model_ids = tuple(EXP5_PROVIDER_ENTRIES)
    for repeat_id in _require_mapping(controls, "exp5")["repeat_ids"]:
        for model_id in model_ids:
            for (domain, topic), case_ids in exp5_groups.items():
                dimensions = {
                    "model": model_id,
                    "repeat": repeat_id,
                    "domain": domain,
                    "difficulty": "hard",
                }
                if topic is not None:
                    dimensions["topic"] = topic
                condition = add_condition("exp5", **dimensions)
                for case_id in case_ids:
                    add_root(condition, case_id)

    return Inventory(
        conditions=tuple(conditions),
        roots=tuple(roots),
        references=tuple(references),
        challenges=challenges,
    )


def _challenge_plans(
    profile_id: str,
    exp4_ids: Sequence[str],
    factor: Mapping[str, JsonObject],
    lean: Mapping[str, JsonObject],
    profile_manifest: Mapping[str, Any],
) -> tuple[JsonObject, ...]:
    target_rules = {
        "INVALID_PARSED_CANDIDATE": ("stable_first_planned_unit", "ordinal_0"),
        "PARSER_REQUIRED_CANONICAL_JSON": (
            "stable_first_planned_unit",
            "every_attempt",
        ),
        "RECOVERABLE_NO_RETURN": ("stable_first_planned_unit", "ordinal_0"),
    }

    def target_rule(family: str, case_id: str, domain: str) -> tuple[str, str]:
        if family != "REQUIRED_CHILD_DELAY":
            return target_rules[family]
        if domain == "lean":
            return "last_required_terminal_slot", "ordinal_0"
        row = factor[case_id]
        prime_factors = row["oracle_prime_factors"]
        is_prime = (
            isinstance(prime_factors, list)
            and len(prime_factors) == 1
            and prime_factors[0]["prime"] == row["target_n"]
        )
        return (
            "last_required_range" if is_prime else "all_true_divisor_ranges",
            "ordinal_0",
        )

    if profile_id == "representative":
        rows = tuple(
            tuple(row)
            for row in _require_list(profile_manifest, "representative_challenge_rows")
        )
    else:
        factor_ids = tuple(case_id for case_id in exp4_ids if case_id in factor)
        lean_ids = tuple(case_id for case_id in exp4_ids if case_id in lean)
        factor_cells = sorted(
            (
                (case_id, repeat_id, str(factor[case_id]["paper_difficulty"]))
                for case_id in factor_ids
                for repeat_id in (0, 1, 2)
            ),
            key=lambda item: (item[2], item[0], item[1]),
        )
        lean_cells = sorted(
            (
                (
                    case_id,
                    repeat_id,
                    str(lean[case_id]["paper_difficulty"]),
                    str(lean[case_id]["topic_family"]),
                )
                for case_id in lean_ids
                for repeat_id in (0, 1, 2)
            ),
            key=lambda item: (item[2], item[3], item[0], item[1]),
        )
        factor_assignments = tuple(
            (
                case_id,
                repeat_id,
                CHALLENGE_FAMILIES[index % len(CHALLENGE_FAMILIES)],
            )
            for index, (case_id, repeat_id, _difficulty) in enumerate(factor_cells)
        )
        lean_cycle = (
            "REQUIRED_CHILD_DELAY",
            "INVALID_PARSED_CANDIDATE",
            "PARSER_REQUIRED_CANONICAL_JSON",
            "RECOVERABLE_NO_RETURN",
        )
        lean_assignments = tuple(
            (case_id, repeat_id, lean_cycle[index % len(lean_cycle)])
            for index, (case_id, repeat_id, _difficulty, _topic) in enumerate(lean_cells)
        )
        rows = tuple(
            (
                case_id,
                repeat_id,
                family,
                *target_rule(family, case_id, "factorization"),
            )
            for case_id, repeat_id, family in factor_assignments
        ) + tuple(
            (
                case_id,
                repeat_id,
                family,
                *target_rule(family, case_id, "lean"),
            )
            for case_id, repeat_id, family in lean_assignments
        )
    return tuple(
        {
            "challenge_plan_id": f"exp4-challenge-{index:03d}",
            "case_id": str(case_id),
            "repeat_id": int(repeat_id),
            "challenge_family": str(family),
            "target_rule": str(target),
            "attempt_rule": str(attempt),
        }
        for index, (case_id, repeat_id, family, target, attempt) in enumerate(rows)
    )


def _root_row(
    condition: Mapping[str, Any],
    case_id: str,
    ordinal: int,
    factor: Mapping[str, JsonObject],
    lean: Mapping[str, JsonObject],
    *,
    is_paper_denominator: bool,
    challenge_plan_id: str | None = None,
) -> JsonObject:
    domain, difficulty, topic, unit_count = _case_data(case_id, factor, lean)
    kind = "paper" if is_paper_denominator else "reference"
    return {
        "root_run_id": f"{condition['experiment_id']}|{kind}|{ordinal:05d}|{case_id}",
        "experiment_id": condition["experiment_id"],
        "condition_id": condition["condition_id"],
        "case_id": case_id,
        "repeat_id": condition["repeat_id"],
        "domain": domain,
        "difficulty": difficulty,
        "topic_family": topic,
        "worker_count": condition["worker_count"],
        "max_retries": condition["max_retries"],
        "continue_after_terminal_child_failure": (
            condition["continue_after_terminal_child_failure"]
        ),
        "source_repeat_id": condition["source_repeat_id"],
        "planned_ai_unit_count": unit_count,
        "is_paper_denominator": is_paper_denominator,
        "challenge_plan_id": challenge_plan_id,
    }


def _provider_bound_objects(inventory: Inventory) -> list[JsonObject]:
    roots_by_experiment = {
        experiment_id: tuple(
            root for root in inventory.roots if root["experiment_id"] == experiment_id
        )
        for experiment_id in EXPERIMENT_ORDER
    }
    conditions_by_id = {
        condition["condition_id"]: condition for condition in inventory.conditions
    }
    planned = {
        experiment_id: sum(root["planned_ai_unit_count"] for root in roots)
        for experiment_id, roots in roots_by_experiment.items()
    }
    protocol_upper = {
        "exp1": planned["exp1"] * 3,
        "exp2": planned["exp2"] * 3,
        "exp3": sum(
            root["planned_ai_unit_count"]
            * (
                4
                if conditions_by_id[root["condition_id"]]["dead_worker_count"] is not None
                else 3
            )
            for root in roots_by_experiment["exp3"]
        ),
        "exp4": sum(
            root["planned_ai_unit_count"]
            * (
                1
                if conditions_by_id[root["condition_id"]]["mode"]
                in EXP4_NO_REQUEUE_MODES
                else 2
            )
            for root in roots_by_experiment["exp4"]
        ),
        "exp5": planned["exp5"],
    }
    provider_upper = {
        "exp1": planned["exp1"] * 3,
        "exp2": 0,
        "exp3": 0,
        "exp4": 0,
        "exp5": planned["exp5"],
    }
    references = tuple(inventory.references)
    return [
        {
            "experiment_id": experiment_id,
            "paper_root_count": len(roots_by_experiment[experiment_id]),
            "reference_root_count": len(references) if experiment_id == "exp3" else 0,
            "planned_first_attempt_ai_units": planned[experiment_id],
            "protocol_execution_attempt_upper": protocol_upper[experiment_id],
            "provider_call_upper": provider_upper[experiment_id],
        }
        for experiment_id in EXPERIMENT_ORDER
    ]


def _plan_object(profile_id: str, inventory: Inventory) -> JsonObject:
    experiments = {
        row["experiment_id"]: row for row in _provider_bound_objects(inventory)
    }
    online_upper = sum(row["provider_call_upper"] for row in experiments.values())
    reference_planned = sum(
        root["planned_ai_unit_count"] for root in inventory.references
    )
    reference_attempt_upper = reference_planned * 3
    estimated_response_bytes = 1024**2
    hard_response_bytes = 16 * 1024**2
    trace_attempt_upper = (
        sum(
            row["protocol_execution_attempt_upper"]
            for row in experiments.values()
        )
        + reference_attempt_upper
    )

    def disk_bytes(response_bound: int) -> int:
        subtotal = (
            online_upper * (2 * response_bound + 24 * 1024)
            + (len(inventory.roots) + len(inventory.references)) * 64 * 1024
            + trace_attempt_upper * 12 * 1024
        )
        return ceil(1.25 * subtotal)

    return {
        "profile_id": profile_id,
        "experiments": experiments,
        "paper_root_count": len(inventory.roots),
        "execution_root_count": len(inventory.roots) + len(inventory.references),
        "online_provider_call_upper": online_upper,
        "exp3_reference_planned_first_attempt_ai_units": reference_planned,
        "exp3_reference_protocol_execution_attempt_upper": reference_attempt_upper,
        "estimated_response_bytes": estimated_response_bytes,
        "hard_response_bytes": hard_response_bytes,
        "estimate_bytes": disk_bytes(estimated_response_bytes),
        "hard_upper_bytes": disk_bytes(hard_response_bytes),
        "online_response_artifact_hard_upper_bytes": ceil(
            1.25
            * online_upper
            * (2 * hard_response_bytes + 24 * 1024)
        ),
        "per_root_free_space_margin_bytes": max(
            512 * 1024**2,
            4 * estimated_response_bytes,
        ),
    }


def _case_data(
    case_id: str,
    factor: Mapping[str, JsonObject],
    lean: Mapping[str, JsonObject],
) -> tuple[str, str, str | None, int]:
    if case_id in factor:
        row = factor[case_id]
        return (
            "factorization",
            str(row["paper_difficulty"]),
            None,
            int(row["split_params"]["requested_child_count"]),
        )
    row = lean[case_id]
    order = row["merge_plan_shape"]["dependency_order"]
    if not isinstance(order, list) or not order:
        raise ValueError(f"Lean dependency order missing: {case_id}")
    if len(order) != len(set(order)):
        raise ValueError(f"Lean dependency order is not unique: {case_id}")
    if len(order) != int(row["expected_ai_unit_count"]):
        raise ValueError(f"Lean planned unit count mismatch: {case_id}")
    return (
        "lean",
        str(row["paper_difficulty"]),
        str(row["topic_family"]),
        len(order),
    )


def _canonical_condition_id(experiment_id: str, dimensions: Mapping[str, Any]) -> str:
    if experiment_id == "exp1":
        ordered = {
            "domain": dimensions["domain"],
            "difficulty": dimensions["difficulty"],
        }
        if "topic" in dimensions:
            ordered["topic"] = dimensions["topic"]
    elif experiment_id == "exp2":
        ordered = {
            "worker": dimensions["worker"],
            "repeat": dimensions["repeat"],
            "position": dimensions["position"],
        }
    elif experiment_id == "exp3":
        ordered = {"domain": dimensions["domain"]}
        stratum_key = "difficulty" if "difficulty" in dimensions else "topic"
        ordered[stratum_key] = dimensions[stratum_key]
        if "fault" in dimensions:
            ordered.update(
                fault=dimensions["fault"],
                rate=dimensions["rate"],
                repeat=dimensions["repeat"],
            )
        else:
            ordered.update(
                dead=dimensions["dead"],
                progress=dimensions["progress"],
                repeat=dimensions["repeat"],
            )
    elif experiment_id == "exp4":
        ordered = {
            "mode": dimensions["mode"],
            "repeat": dimensions["repeat"],
        }
    elif experiment_id == "exp5":
        topic = dimensions.get("topic")
        stratum = (
            "factorization_hard"
            if dimensions["domain"] == "factorization"
            else f"lean_hard_{topic}"
        )
        ordered = {
            "model": dimensions["model"],
            "repeat": dimensions["repeat"],
            "stratum": stratum,
        }
    else:
        raise ValueError(f"unknown experiment for condition ID: {experiment_id}")
    return _condition_id(experiment_id, **ordered)


def _condition_id(experiment_id: str, **dimensions: Any) -> str:
    encoded = "|".join(f"{key}={value}" for key, value in dimensions.items())
    return f"{experiment_id}|{encoded}"


def _disabled_mechanisms(mode: str | None) -> list[str]:
    if mode in (None, "FULL"):
        return []
    return [EXP4_DISABLED_NAMES[part] for part in mode.split("__")]


def _cases_by_id(path: Path) -> dict[str, JsonObject]:
    rows = _read_jsonl(path)
    result = {str(row["case_id"]): row for row in rows}
    if len(result) != len(rows):
        raise ValueError(f"duplicate case_id in {path}")
    return result


def _read_jsonl(path: Path) -> tuple[JsonObject, ...]:
    rows: list[JsonObject] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"JSONL row must be an object: {path}:{line_number}")
        rows.append(row)
    if not rows:
        raise ValueError(f"empty JSONL corpus: {path}")
    return tuple(rows)


def _asset_record_count(path: Path, file_format: str) -> int:
    if file_format == "jsonl":
        return len(_read_jsonl(path))
    body = _read_json(path)
    entries = body.get("entries")
    if isinstance(entries, list):
        return len(entries)
    return 0


def _read_json(path: Path) -> JsonObject:
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return body


def _read_frozen_json(repo_root: Path, frozen_sha: str, path: str) -> JsonObject:
    body = json.loads(_git_blob(repo_root, frozen_sha, path))
    if not isinstance(body, dict):
        raise ValueError(f"frozen JSON root must be an object: {path}")
    return body


def _git_blob(repo_root: Path, commit: str, path: str) -> bytes:
    return subprocess.check_output(
        ["git", "-C", str(repo_root), "cat-file", "blob", f"{commit}:{path}"]
    )


def _items_sha256(items: Sequence[Mapping[str, Any]]) -> str:
    return sha256(_canonical_bytes(list(items))).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _normalized_text_digest(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return "sha256:" + sha256(normalized.encode("utf-8")).hexdigest()


def _require_mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    item = value.get(key)
    if not isinstance(item, dict):
        raise ValueError(f"{key} must be an object")
    return item


def _require_list(value: Mapping[str, Any], key: str) -> list[Any]:
    item = value.get(key)
    if not isinstance(item, list):
        raise ValueError(f"{key} must be a list")
    return item


def _require_text(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValueError(f"{key} must be a non-empty string")
    return item


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--frozen-sha", default=DEFAULT_FROZEN_SHA)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    summary = verify_all(args.repo_root, frozen_sha=args.frozen_sha)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print(
            "authoritative corpus ok: "
            f"assets={summary['assets_checked']['asset_count']} "
            f"root_identities={summary['inventory']['full_root_identities']['count']} "
            "root_identity_sha="
            f"{summary['inventory']['full_root_identities']['items_sha256']} "
            "reference_identities="
            f"{summary['inventory']['full_reference_identities']['count']} "
            "reference_identity_sha="
            f"{summary['inventory']['full_reference_identities']['items_sha256']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
