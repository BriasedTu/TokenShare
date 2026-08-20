"""Experiment 5 endpoint smoke evidence 的派生与只读校验。"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from tokenshare.experiments.paper_formal_evidence import FormalEvidenceStore
from tokenshare.experiments.paper_models import JsonObject, digest_json
from tokenshare.runtime_paths import resolve_persisted_data_path
from tokenshare.storage.events import EventLedger


EXP5_SMOKE_EVIDENCE_BUNDLE_SCHEMA_VERSION = (
    "tokenshare.paper_exp5_endpoint_smoke_evidence_bundle.v1"
)
EXP5_SMOKE_SUITE_ID = "paper_smoke_exp5_v4"
EXP5_EXPERIMENT_ID = "exp5_real_ai_model_endpoint_comparison"
# v4 只接受当前 tracked profile；旧 profile/suite digest 在 source 校验处 fail-closed。
EXP5_SMOKE_PROFILE_DIGEST = (
    "sha256:697b4218243a22f5fd2838bbc88baf7f6354491921970e4cf9beb0b5c1c37b1e"
)
_REQUIRED_PROTOCOL_EVENT_TYPES = {
    "TASK_REGISTERED",
    "TASK_UNIT_CREATED",
    "LEASE_STATE_CHANGED",
    "ATTEMPT_STATE_CHANGED",
    "EXECUTION_REQUEST_RECORDED",
    "EXECUTION_SUBMISSION_RECORDED",
}
_REQUIRED_GENERATION_FILES = {
    "run_manifest.json",
    "per_task_results.jsonl",
    "per_attempt_results.jsonl",
    "fault_injections.jsonl",
    "events/event_log.jsonl",
    "artifacts/artifact_index.jsonl",
}


class Exp5SmokeEvidenceError(ValueError):
    """表示 source smoke evidence 无法形成可验证 endpoint bundle。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def build_exp5_smoke_evidence_bundle(
    *,
    source_suite_root: str | Path,
    entry_map: Mapping[str, Any],
) -> JsonObject:
    """从已提交的 8-root Exp5 smoke generation 纯派生 endpoint evidence。"""

    try:
        return _build_exp5_smoke_evidence_bundle(
            source_suite_root=source_suite_root,
            entry_map=entry_map,
        )
    except Exp5SmokeEvidenceError:
        raise
    except (
        AttributeError,
        IndexError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_source_suite_invalid",
            "Exp5 smoke source suite evidence is malformed",
        ) from exc


def _build_exp5_smoke_evidence_bundle(
    *,
    source_suite_root: str | Path,
    entry_map: Mapping[str, Any],
) -> JsonObject:
    persisted_root = str(source_suite_root)
    try:
        root = resolve_persisted_data_path(persisted_root)
    except (OSError, ValueError) as exc:
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_source_suite_invalid",
            "Exp5 smoke source suite path cannot be resolved",
        ) from exc
    if not root.is_dir():
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_source_suite_missing",
            "Exp5 smoke source suite root is missing",
        )
    suite = _read_json(root / "suite_manifest.json")
    profile = _read_json(root / "smoke_profile.json")
    execution_plan = _read_json(root / "smoke_execution_plan.json")
    launch_manifest = _read_json(root / "smoke_launch_manifest.json")
    conditions = _read_jsonl(root / "conditions.jsonl")
    _validate_source_suite(
        suite=suite,
        profile=profile,
        execution_plan=execution_plan,
        launch_manifest=launch_manifest,
        conditions=conditions,
    )
    source_preflight = _source_preflight(
        suite,
        expected_condition_ids={
            str(condition.get("condition_id") or "") for condition in conditions
        },
    )
    member_plans = source_preflight.get("member_plans")
    entry_specs = entry_map.get("members")
    if not isinstance(member_plans, Mapping) or not isinstance(entry_specs, Mapping):
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_source_identity_mismatch",
            "Exp5 source endpoint member bindings are missing",
        )
    expected_member_ids = tuple(source_preflight.get("expected_member_ids", ()))
    if set(expected_member_ids) != set(entry_specs) or set(member_plans) != set(
        expected_member_ids
    ):
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_source_identity_mismatch",
            "Exp5 source member inventory does not match the entry map",
        )

    items = execution_plan.get("items")
    if not isinstance(items, list):
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_source_plan_mismatch",
            "Exp5 smoke execution items are missing",
        )
    item_by_condition = {
        str(item.get("condition_id")): item
        for item in items
        if isinstance(item, Mapping)
        and isinstance(item.get("condition_id"), str)
    }
    conditions_by_member_domain: dict[tuple[str, str], Mapping[str, Any]] = {}
    for condition in conditions:
        if not isinstance(condition, Mapping):
            continue
        key = (
            str(condition.get("cohort_member_id") or ""),
            str(condition.get("domain") or ""),
        )
        if key in conditions_by_member_domain:
            raise Exp5SmokeEvidenceError(
                "smoke_evidence_source_plan_mismatch",
                "Exp5 smoke has duplicate member/domain roots",
            )
        conditions_by_member_domain[key] = condition

    members: dict[str, JsonObject] = {}
    for member_id in expected_member_ids:
        plan = member_plans.get(member_id)
        spec = entry_specs.get(member_id)
        if not isinstance(plan, Mapping) or not isinstance(spec, Mapping):
            raise Exp5SmokeEvidenceError(
                "smoke_evidence_source_identity_mismatch",
                f"Exp5 smoke member binding is missing: {member_id}",
            )
        if (
            spec.get("provider_config_id") != plan.get("provider_config_id")
            or spec.get("entry_id") != plan.get("selected_entry_id")
        ):
            raise Exp5SmokeEvidenceError(
                "smoke_evidence_source_identity_mismatch",
                f"Exp5 smoke entry binding mismatch: {member_id}",
            )
        domain_evidence: dict[str, JsonObject] = {}
        for domain in ("factorization", "lean_proof"):
            condition = conditions_by_member_domain.get((member_id, domain))
            if condition is None:
                raise Exp5SmokeEvidenceError(
                    "smoke_evidence_missing_domain_coverage",
                    f"Exp5 smoke domain coverage is missing: {member_id}/{domain}",
                )
            condition_id = str(condition.get("condition_id") or "")
            item = item_by_condition.get(condition_id)
            if not isinstance(item, Mapping):
                raise Exp5SmokeEvidenceError(
                    "smoke_evidence_source_plan_mismatch",
                    f"Exp5 smoke plan item is missing: {condition_id}",
                )
            domain_evidence[domain] = _derive_domain_evidence(
                root=root,
                condition=condition,
                case_id=str(item.get("case_id") or ""),
                member_plan=plan,
            )
        flattened_attempts = [
            attempt
            for domain in ("factorization", "lean_proof")
            for attempt in domain_evidence[domain]["provider_attempts"]
        ]
        first_attempt = flattened_attempts[0]
        capability_checks = {
            field_name: all(
                attempt["capability_checks"].get(field_name) is True
                for attempt in flattened_attempts
            )
            for field_name in (
                "resolved_model_match",
                "request_controls_match",
                "usage_schema_verified",
                "thinking_breakdown_verified",
                "timeout_limit_verified",
            )
        }
        members[member_id] = {
            "schema_version": "tokenshare.paper_model_endpoint_smoke_evidence.v2",
            "status": "passed",
            "cohort_member_id": member_id,
            "entry_id": plan["selected_entry_id"],
            "provider_family": plan["provider_family"],
            "provider_model_id": plan["provider_model_id"],
            "reasoning_profile_id": plan["reasoning_profile_id"],
            "model_cohort_id": plan["cohort_id"],
            "model_cohort_digest": plan["model_cohort_digest"],
            "source_provider_config_digest": plan[
                "source_provider_config_digest"
            ],
            "model_endpoint_identity_digest": plan[
                "model_endpoint_identity_digest"
            ],
            "request_controls_digest": plan["request_controls_digest"],
            "pricing_snapshot_digest": plan["pricing_snapshot_digest"],
            "capability_checks": capability_checks,
            "provider_attempt_count": sum(
                int(evidence["provider_attempt_count"])
                for evidence in domain_evidence.values()
            ),
            "raw_output_ref": first_attempt["raw_output_ref"],
            "provenance_ref": first_attempt["provenance_ref"],
            "usage_ref": first_attempt["usage_ref"],
            "domain_evidence": domain_evidence,
        }

    bundle: JsonObject = {
        "schema_version": EXP5_SMOKE_EVIDENCE_BUNDLE_SCHEMA_VERSION,
        "source_suite_ref": {
            "source_suite_root": persisted_root,
            "source_suite_id": suite["suite_id"],
            "source_suite_manifest_digest": digest_json(suite),
            "source_profile_digest": profile["profile_digest"],
            "source_execution_plan_digest": execution_plan[
                "execution_plan_digest"
            ],
            "source_launch_manifest_digest": launch_manifest[
                "launch_manifest_digest"
            ],
            "real_transport": True,
        },
        "entry_map_digest": digest_json(dict(entry_map)),
        "model_cohort_id": source_preflight["cohort_id"],
        "model_cohort_digest": source_preflight["model_cohort_digest"],
        "members": members,
    }
    bundle["bundle_digest"] = digest_json(bundle)
    return bundle


def write_exp5_smoke_evidence_bundle(
    *,
    source_suite_root: str | Path,
    entry_map: Mapping[str, Any],
    output_path: str | Path,
) -> JsonObject:
    """原子写入从 source suite 派生的 bundle，供 formal CLI 显式绑定。"""

    bundle = build_exp5_smoke_evidence_bundle(
        source_suite_root=source_suite_root,
        entry_map=entry_map,
    )
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(
        json.dumps(
            bundle,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)
    return bundle


def load_exp5_smoke_evidence_bundle(path: str | Path) -> JsonObject:
    """读取 bundle 并验证其内容 digest；source bytes 由 formal preflight 重验。"""

    body = _read_json(Path(path))
    if body.get("schema_version") != EXP5_SMOKE_EVIDENCE_BUNDLE_SCHEMA_VERSION:
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_bundle_schema_mismatch",
            "unsupported Exp5 smoke evidence bundle schema",
        )
    declared_digest = body.get("bundle_digest")
    core = dict(body)
    core.pop("bundle_digest", None)
    if declared_digest != digest_json(core):
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_bundle_digest_mismatch",
            "Exp5 smoke evidence bundle digest mismatch",
        )
    return body


def validate_exp5_smoke_evidence_bundle(
    bundle: Mapping[str, Any] | None,
    *,
    entry_map: Mapping[str, Any],
    model_cohort_id: str,
    model_cohort_digest: str,
    expected_member_ids: Sequence[str],
) -> JsonObject:
    """校验 bundle 自身绑定以及其 source smoke suite 的只读定位。"""

    member_ids = tuple(str(member_id) for member_id in expected_member_ids)
    member_reasons: dict[str, list[str]] = {
        member_id: [] for member_id in member_ids
    }
    global_reasons: list[str] = []
    if not isinstance(bundle, Mapping):
        global_reasons.append("missing_smoke_evidence_bundle")
        return _validation_result(
            bundle_digest=None,
            global_reasons=global_reasons,
            member_reasons=member_reasons,
        )

    declared_digest = bundle.get("bundle_digest")
    bundle_core = dict(bundle)
    bundle_core.pop("bundle_digest", None)
    actual_digest = digest_json(bundle_core)
    if declared_digest != actual_digest:
        global_reasons.append("smoke_evidence_bundle_digest_mismatch")
    if bundle.get("schema_version") != EXP5_SMOKE_EVIDENCE_BUNDLE_SCHEMA_VERSION:
        global_reasons.append("smoke_evidence_bundle_schema_mismatch")
    if bundle.get("entry_map_digest") != digest_json(dict(entry_map)):
        global_reasons.append("smoke_evidence_entry_map_digest_mismatch")
    if bundle.get("model_cohort_id") not in (None, model_cohort_id):
        global_reasons.append("smoke_evidence_model_cohort_id_mismatch")
    if bundle.get("model_cohort_digest") not in (None, model_cohort_digest):
        global_reasons.append("smoke_evidence_model_cohort_digest_mismatch")

    source_suite_ref = bundle.get("source_suite_ref")
    persisted_root = (
        source_suite_ref.get("source_suite_root")
        if isinstance(source_suite_ref, Mapping)
        else None
    )
    if not isinstance(persisted_root, str) or not persisted_root:
        global_reasons.append("smoke_evidence_source_suite_missing")
    else:
        # 历史输出迁移后只通过统一映射读取，绝不回写 bundle 或 source suite。
        try:
            source_root = resolve_persisted_data_path(persisted_root)
            suite_manifest_path = source_root / "suite_manifest.json"
            if not source_root.is_dir() or not suite_manifest_path.is_file():
                global_reasons.append("smoke_evidence_source_suite_missing")
        except (OSError, ValueError):
            global_reasons.append("smoke_evidence_source_suite_invalid")

    if not global_reasons:
        try:
            rebuilt = build_exp5_smoke_evidence_bundle(
                source_suite_root=str(persisted_root),
                entry_map=entry_map,
            )
        except Exp5SmokeEvidenceError as exc:
            global_reasons.append(exc.reason)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            global_reasons.append("smoke_evidence_source_suite_invalid")
        else:
            if dict(rebuilt) != dict(bundle):
                global_reasons.append("smoke_evidence_bundle_content_mismatch")

    return _validation_result(
        bundle_digest=(
            str(declared_digest) if isinstance(declared_digest, str) else None
        ),
        global_reasons=global_reasons,
        member_reasons=member_reasons,
    )


def _validate_source_suite(
    *,
    suite: Mapping[str, Any],
    profile: Mapping[str, Any],
    execution_plan: Mapping[str, Any],
    launch_manifest: Mapping[str, Any],
    conditions: Sequence[Mapping[str, Any]],
) -> None:
    classification = {
        "formal": False,
        "pilot_only": True,
        "regression_only": True,
        "paper_eligible": False,
    }
    if (
        suite.get("suite_id") != EXP5_SMOKE_SUITE_ID
        or suite.get("status") not in {"completed", "completed_with_failures"}
        or suite.get("capturing") is not False
        or suite.get("execution_scope") != "smoke_suite"
        or any(suite.get(field) is not expected for field, expected in classification.items())
    ):
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_source_suite_invalid",
            "source suite is not a terminal real-transport Exp5 smoke suite",
        )
    if (
        profile.get("suite_id") != EXP5_SMOKE_SUITE_ID
        or profile.get("profile_digest") != EXP5_SMOKE_PROFILE_DIGEST
        or profile.get("expected_root_runs") != 8
        or profile.get("experiment_ids") != [EXP5_EXPERIMENT_ID]
        or any(
            profile.get(field) is not expected
            for field, expected in classification.items()
        )
    ):
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_source_profile_mismatch",
            "source smoke profile identity is invalid",
        )
    profile_core = dict(profile)
    profile_digest = profile_core.pop("profile_digest", None)
    if profile_digest != digest_json(profile_core):
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_source_profile_mismatch",
            "source smoke profile digest is invalid",
        )
    if (
        execution_plan.get("suite_id") != EXP5_SMOKE_SUITE_ID
        or execution_plan.get("profile_digest") != profile_digest
        or execution_plan.get("direct_root_run_count") != 8
        or execution_plan.get("experiment_ids") != [EXP5_EXPERIMENT_ID]
        or any(
            execution_plan.get(field) is not expected
            for field, expected in classification.items()
        )
    ):
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_source_plan_mismatch",
            "source smoke execution plan identity is invalid",
        )
    plan_core = dict(execution_plan)
    plan_digest = plan_core.pop("execution_plan_digest", None)
    if plan_digest != digest_json(plan_core):
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_source_plan_mismatch",
            "source smoke execution plan digest is invalid",
        )
    if len(conditions) != 8:
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_source_plan_mismatch",
            "source smoke condition inventory must contain eight roots",
        )
    launch_core = dict(launch_manifest)
    launch_digest = launch_core.pop("launch_manifest_digest", None)
    command = launch_manifest.get("command")
    argv = command.get("argv") if isinstance(command, Mapping) else None
    if (
        launch_manifest.get("schema_version")
        != "tokenshare.paper_smoke_launch_manifest.v1"
        or launch_manifest.get("suite_id") != EXP5_SMOKE_SUITE_ID
        or launch_manifest.get("profile_digest") != profile.get("profile_digest")
        or launch_manifest.get("execution_plan_digest")
        != execution_plan.get("execution_plan_digest")
        or launch_manifest.get("formal") is not False
        or launch_manifest.get("pilot_only") is not True
        or launch_manifest.get("regression_only") is not True
        or launch_manifest.get("paper_eligible") is not False
        or not isinstance(argv, list)
        or "--real-transport" not in argv
        or launch_digest != digest_json(launch_core)
    ):
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_real_transport_unverified",
            "source smoke launch manifest does not prove real transport",
        )
    profile_items = profile.get("items")
    plan_items = execution_plan.get("items")
    if not isinstance(profile_items, list) or not isinstance(plan_items, list):
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_source_plan_mismatch",
            "source smoke item inventory is missing",
        )
    if len(profile_items) != 8 or len(plan_items) != 8:
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_source_plan_mismatch",
            "source smoke item inventory must contain eight roots",
        )
    profile_by_item_id = _unique_records_by_string_key(
        profile_items,
        key="item_id",
        reason="smoke_evidence_source_profile_mismatch",
    )
    plan_by_item_id = _unique_records_by_string_key(
        plan_items,
        key="item_id",
        reason="smoke_evidence_source_plan_mismatch",
    )
    condition_by_id = _unique_records_by_string_key(
        conditions,
        key="condition_id",
        reason="smoke_evidence_source_plan_mismatch",
    )
    if set(profile_by_item_id) != set(plan_by_item_id):
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_source_plan_mismatch",
            "source smoke profile and execution plan item sets differ",
        )
    for item_id, profile_item in profile_by_item_id.items():
        plan_item = plan_by_item_id[item_id]
        for field_name in (
            "experiment_id",
            "case_id",
            "repeat_id",
            "condition_selector",
        ):
            if plan_item.get(field_name) != profile_item.get(field_name):
                raise Exp5SmokeEvidenceError(
                    "smoke_evidence_source_plan_mismatch",
                    f"source smoke plan item differs from profile: {item_id}/{field_name}",
                )
        condition_id = plan_item.get("condition_id")
        condition = condition_by_id.get(str(condition_id or ""))
        if (
            condition is None
            or plan_item.get("condition_digest") != condition.get("condition_digest")
            or plan_item.get("experiment_id") != condition.get("experiment_id")
            or plan_item.get("repeat_id") != condition.get("repeat_id")
        ):
            raise Exp5SmokeEvidenceError(
                "smoke_evidence_source_plan_mismatch",
                f"source smoke plan item is not bound to its condition: {item_id}",
            )
        selector = plan_item.get("condition_selector")
        if not isinstance(selector, Mapping) or any(
            condition.get(field_name) != selector.get(field_name)
            for field_name in ("domain", "cohort_member_id")
        ):
            raise Exp5SmokeEvidenceError(
                "smoke_evidence_source_plan_mismatch",
                f"source smoke condition selector mismatch: {item_id}",
            )
    if {
        str(item.get("condition_id") or "") for item in plan_items
    } != set(condition_by_id):
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_source_plan_mismatch",
            "source smoke plan and condition inventories differ",
        )
    if any(
        condition.get("real_transport_required") is not True
        for condition in conditions
    ):
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_real_transport_unverified",
            "source smoke conditions do not require real transport",
        )


def _source_preflight(
    suite: Mapping[str, Any],
    *,
    expected_condition_ids: set[str],
) -> Mapping[str, Any]:
    suite_identity = suite.get("suite_identity")
    identity = (
        suite_identity.get("identity")
        if isinstance(suite_identity, Mapping)
        else None
    )
    body = identity.get("body") if isinstance(identity, Mapping) else None
    if (
        not isinstance(body, Mapping)
        or identity.get("digest") != digest_json(dict(body))
    ):
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_source_identity_mismatch",
            "source suite endpoint identity digest is invalid",
        )
    endpoints = body.get("endpoints") if isinstance(body, Mapping) else None
    if not isinstance(endpoints, list) or len(endpoints) != 8:
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_source_identity_mismatch",
            "source suite endpoint identity inventory is incomplete",
        )
    endpoint_condition_ids = {
        str(endpoint.get("condition_id") or "")
        for endpoint in endpoints
        if isinstance(endpoint, Mapping)
        and endpoint.get("experiment_id") == EXP5_EXPERIMENT_ID
    }
    if endpoint_condition_ids != expected_condition_ids:
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_source_identity_mismatch",
            "source suite endpoint identities do not match smoke conditions",
        )
    bindings = [
        endpoint.get("endpoint_binding")
        for endpoint in endpoints
        if isinstance(endpoint, Mapping)
    ]
    if len(bindings) != 8 or any(not isinstance(binding, Mapping) for binding in bindings):
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_source_identity_mismatch",
            "source suite endpoint bindings are incomplete",
        )
    digests = {digest_json(dict(binding)) for binding in bindings}
    if len(digests) != 1:
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_source_identity_mismatch",
            "source suite endpoint bindings drift across conditions",
        )
    preflight = bindings[0]
    assert isinstance(preflight, Mapping)
    if preflight.get("status") != "planned":
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_source_identity_mismatch",
            "source suite endpoint preflight was not planned",
        )
    return preflight


def _derive_domain_evidence(
    *,
    root: Path,
    condition: Mapping[str, Any],
    case_id: str,
    member_plan: Mapping[str, Any],
) -> JsonObject:
    condition_id = str(condition.get("condition_id") or "")
    repeat_id = condition.get("repeat_id")
    if not condition_id or type(repeat_id) is not int or not case_id:
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_source_plan_mismatch",
            "source smoke condition identity is incomplete",
        )
    for condition_field, plan_field in (
        ("cohort_member_id", "cohort_member_id"),
        ("model_cohort_id", "cohort_id"),
        ("model_cohort_digest", "model_cohort_digest"),
        ("provider_config_id", "provider_config_id"),
        ("model_entry_id", "selected_entry_id"),
        ("provider_family", "provider_family"),
        ("provider_model_id", "provider_model_id"),
        ("reasoning_profile_id", "reasoning_profile_id"),
        ("model_endpoint_identity_digest", "model_endpoint_identity_digest"),
        ("source_provider_config_digest", "source_provider_config_digest"),
    ):
        if condition.get(condition_field) != member_plan.get(plan_field):
            raise Exp5SmokeEvidenceError(
                "smoke_evidence_source_identity_mismatch",
                f"source condition identity mismatch: {condition_field}",
            )
    run_root = (
        root
        / "experiments"
        / EXP5_EXPERIMENT_ID
        / "runs"
        / condition_id
        / str(repeat_id)
    )
    generation_root, generation_ref = _load_generation(run_root)
    tasks = _read_jsonl(generation_root / "per_task_results.jsonl")
    attempts = _read_jsonl(generation_root / "per_attempt_results.jsonl")
    events = _read_jsonl(generation_root / "events" / "event_log.jsonl")
    artifact_index = _read_jsonl(
        generation_root / "artifacts" / "artifact_index.jsonl"
    )
    matching_tasks = [task for task in tasks if task.get("task_id") == case_id]
    if len(matching_tasks) != 1:
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_source_task_missing",
            f"source smoke task is missing or ambiguous: {condition_id}/{case_id}",
        )
    task = matching_tasks[0]
    if (
        task.get("root_status") not in {"completed", "failed"}
        or task.get("evidence_integrity") != "complete"
        or task.get("pilot_only") is not True
        or task.get("regression_only") is not True
        or task.get("paper_eligible") is not False
    ):
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_source_task_invalid",
            f"source smoke task classification is invalid: {condition_id}/{case_id}",
        )
    _validate_protocol_lifecycle(
        event_log_path=generation_root / "events" / "event_log.jsonl",
        task=task,
        events=events,
        condition_id=condition_id,
        repeat_id=repeat_id,
        case_id=case_id,
    )
    matching_attempts = [
        attempt
        for attempt in attempts
        if attempt.get("task_id") == case_id
        and attempt.get("provider_attempt_count") == 1
    ]
    if not matching_attempts:
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_missing_provider_attempt",
            f"source smoke provider attempt is missing: {condition_id}/{case_id}",
        )
    attempt_evidence = [
        _derive_attempt_evidence(
            root=root,
            condition=condition,
            case_id=case_id,
            attempt=attempt,
            artifact_index=artifact_index,
            member_plan=member_plan,
        )
        for attempt in matching_attempts
    ]
    try:
        FormalEvidenceStore(root)._validate_run(
            run_root,
            experiment_id=EXP5_EXPERIMENT_ID,
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_protocol_lifecycle_invalid",
            f"source smoke committed run evidence is invalid: {condition_id}/{case_id}",
        ) from exc
    return {
        "schema_version": "tokenshare.paper_exp5_endpoint_smoke_domain_evidence.v1",
        "domain": condition["domain"],
        "source_condition_id": condition_id,
        "source_repeat_id": repeat_id,
        "source_case_id": case_id,
        "source_generation_ref": generation_ref,
        "provider_attempt_count": sum(
            int(item["provider_attempt_count"]) for item in attempt_evidence
        ),
        "provider_attempts": attempt_evidence,
    }


def _derive_attempt_evidence(
    *,
    root: Path,
    condition: Mapping[str, Any],
    case_id: str,
    attempt: Mapping[str, Any],
    artifact_index: Sequence[Mapping[str, Any]],
    member_plan: Mapping[str, Any],
) -> JsonObject:
    if (
        attempt.get("experiment_id") != EXP5_EXPERIMENT_ID
        or attempt.get("condition_id") != condition.get("condition_id")
        or attempt.get("repeat_id") != condition.get("repeat_id")
        or attempt.get("task_id") != case_id
        or not isinstance(attempt.get("attempt_id"), str)
        or not attempt.get("attempt_id")
        or attempt.get("provider_attempt_count") != 1
        or attempt.get("provider") != member_plan.get("provider_family")
        or attempt.get("model") != member_plan.get("provider_model_id")
        or attempt.get("entry_id") != member_plan.get("selected_entry_id")
    ):
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_source_identity_mismatch",
            "source attempt endpoint identity mismatch",
        )
    resolved: dict[str, tuple[JsonObject, Mapping[str, Any]]] = {}
    for field_name in (
        "request_ref",
        "raw_output_ref",
        "provenance_ref",
        "usage_ref",
        "model_execution_record_ref",
    ):
        resolved[field_name] = _resolve_artifact(
            root=root,
            condition=condition,
            case_id=case_id,
            artifact_ref=attempt.get(field_name),
            artifact_index=artifact_index,
        )
    request, request_index = resolved["request_ref"]
    raw, raw_index = resolved["raw_output_ref"]
    provenance, provenance_index = resolved["provenance_ref"]
    usage, usage_index = resolved["usage_ref"]
    record, record_index = resolved["model_execution_record_ref"]
    expected_provider = member_plan["provider_family"]
    expected_entry = member_plan["selected_entry_id"]
    expected_model = member_plan["provider_model_id"]
    expected_reasoning = member_plan["endpoint_identity"][
        "effective_reasoning_controls"
    ]
    expected_effective_controls_digest = _expected_effective_request_controls_digest(
        member_plan
    )
    limits = request.get("limits")
    if (
        request.get("schema_version") != "phase3.execution_request.v1"
        or not isinstance(limits, Mapping)
        or limits.get("timeout_seconds") != 600
        or limits.get("max_tokens") != 32768
    ):
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_request_controls_mismatch",
            "source request limits do not match Exp5 v3",
        )
    if (
        raw.get("schema_version") != "phase7.raw_model_output.v2"
        or raw.get("provider_family") != expected_provider
        or raw.get("entry_id") != expected_entry
        or raw.get("configured_model") != expected_model
        or raw.get("requested_model") != expected_model
        or raw.get("resolved_model") != expected_model
        or raw.get("response_model_status") != "present"
    ):
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_resolved_model_mismatch",
            "source raw model identity does not match the frozen endpoint",
        )
    provenance_attempts = provenance.get("attempts")
    selection_record = provenance.get("selection_record")
    if (
        provenance.get("schema_version") != "phase7.ai_provider_call_provenance.v2"
        or provenance.get("provider_family") != expected_provider
        or provenance.get("config_digest")
        != member_plan.get("source_provider_config_digest")
        or provenance.get("final_entry_id") != expected_entry
        or provenance.get("final_result_kind") != "succeeded"
        or not isinstance(provenance_attempts, list)
        or len(provenance_attempts) != 1
        or not isinstance(selection_record, Mapping)
        or selection_record.get("selected_entry_id") != expected_entry
        or selection_record.get("eligible_entry_ids") != [expected_entry]
        or selection_record.get("attempt_entry_ids") != [expected_entry]
    ):
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_provenance_mismatch",
            "source provider provenance does not match the frozen endpoint",
        )
    for provider_attempt in provenance_attempts:
        request_identity = (
            provider_attempt.get("provider_request_identity")
            if isinstance(provider_attempt, Mapping)
            else None
        )
        if (
            not isinstance(request_identity, Mapping)
            or provider_attempt.get("provider_family") != expected_provider
            or provider_attempt.get("entry_id") != expected_entry
            or provider_attempt.get("configured_model") != expected_model
            or request_identity.get("provider_family") != expected_provider
            or request_identity.get("entry_id") != expected_entry
            or request_identity.get("configured_model") != expected_model
            or request_identity.get("requested_model") != expected_model
            or request_identity.get("reasoning_controls") != expected_reasoning
            or request_identity.get("effective_request_controls_digest")
            != expected_effective_controls_digest
        ):
            raise Exp5SmokeEvidenceError(
                "smoke_evidence_request_controls_mismatch",
                "source provider request identity does not match the frozen endpoint",
            )
    enable_thinking = expected_reasoning.get("enable_thinking")
    reasoning_breakdown_valid = (
        _non_negative_int(usage.get("reasoning_tokens"))
        if enable_thinking is True
        else (
            enable_thinking is False
            and usage.get("reasoning_tokens") is None
            and usage.get("visible_output_basis") == "explicit_non_thinking"
        )
    )
    if (
        usage.get("schema_version") != "tokenshare.paper_ai_usage.v1"
        or usage.get("provider_family") != expected_provider
        or usage.get("entry_id") != expected_entry
        or usage.get("configured_model") != expected_model
        or usage.get("requested_model") != expected_model
        or usage.get("provider_attempt_count") != 1
        or digest_json(dict(usage.get("pricing_snapshot", {})))
        != member_plan.get("pricing_snapshot_digest")
        or not reasoning_breakdown_valid
        or any(
            not _non_negative_int(usage.get(field_name))
            for field_name in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
            )
        )
    ):
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_usage_schema_mismatch",
            "source usage evidence is incomplete",
        )
    record_core = dict(record)
    record_digest = record_core.pop("record_digest", None)
    if (
        record.get("schema_version") != "tokenshare.paper_model_execution_record.v2"
        or record_digest != digest_json(record_core)
        or record.get("identity_status") != "matched"
        or record.get("paper_eligible") is not True
        or record.get("expected_identity") != member_plan.get("endpoint_identity")
        or record.get("source_provider_config_digest")
        != member_plan.get("source_provider_config_digest")
        or record.get("requested_model") != expected_model
        or record.get("resolved_model") != expected_model
        or record.get("response_model_status") != "present"
        or record.get("condition_id") != condition.get("condition_id")
        or record.get("repeat_id") != condition.get("repeat_id")
        or record.get("task_id") != case_id
        or record.get("attempt_id") != attempt.get("attempt_id")
        or record.get("actual_request_identities")
        != [dict(provenance_attempts[0]["provider_request_identity"])]
    ):
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_model_execution_record_mismatch",
            "source model execution record identity is invalid",
        )
    for field_name in (
        "request_ref",
        "raw_output_ref",
        "provenance_ref",
        "usage_ref",
    ):
        if record.get(field_name) != attempt.get(field_name):
            raise Exp5SmokeEvidenceError(
                "smoke_evidence_model_execution_record_mismatch",
                f"source model execution record ref mismatch: {field_name}",
            )
    return {
        "attempt_id": attempt.get("attempt_id"),
        "provider_attempt_count": attempt["provider_attempt_count"],
        "request_ref": dict(request_index),
        "raw_output_ref": dict(raw_index),
        "provenance_ref": dict(provenance_index),
        "usage_ref": dict(usage_index),
        "model_execution_record_ref": dict(record_index),
        "capability_checks": {
            "resolved_model_match": (
                raw.get("resolved_model") == expected_model
                and record.get("identity_status") == "matched"
            ),
            "request_controls_match": all(
                provider_attempt["provider_request_identity"].get(
                    "effective_request_controls_digest"
                )
                == expected_effective_controls_digest
                for provider_attempt in provenance_attempts
            ),
            "usage_schema_verified": (
                usage.get("schema_version") == "tokenshare.paper_ai_usage.v1"
                and usage.get("provider_attempt_count") == 1
            ),
            "thinking_breakdown_verified": bool(reasoning_breakdown_valid),
            "timeout_limit_verified": limits.get("timeout_seconds") == 600,
        },
    }


def _load_generation(run_root: Path) -> tuple[Path, JsonObject]:
    pointer = _read_json(run_root / "CURRENT.json")
    generation_id = pointer.get("generation_id")
    if not isinstance(generation_id, str) or not generation_id:
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_source_generation_invalid",
            "source smoke generation pointer is invalid",
        )
    generation_root = run_root / ".generations" / generation_id
    manifest = _read_json(generation_root / "generation_manifest.json")
    if (
        manifest.get("schema_version")
        != "tokenshare.paper_checkpoint_generation.v1"
        or manifest.get("generation_id") != generation_id
        or pointer.get("generation_manifest_digest") != digest_json(manifest)
    ):
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_source_generation_invalid",
            "source smoke generation commit marker is invalid",
        )
    files = manifest.get("files")
    if not isinstance(files, list):
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_source_generation_invalid",
            "source smoke generation files are missing",
        )
    by_path = {
        str(entry.get("path")): entry
        for entry in files
        if isinstance(entry, Mapping)
    }
    if set(by_path) != _REQUIRED_GENERATION_FILES:
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_source_generation_invalid",
            "source smoke generation file inventory is incomplete",
        )
    for relative_path, entry in by_path.items():
        path = generation_root / relative_path
        if not path.is_file():
            raise Exp5SmokeEvidenceError(
                "smoke_evidence_source_generation_invalid",
                f"source smoke generation file is missing: {relative_path}",
            )
        content = path.read_bytes()
        if (
            entry.get("content_sha256") != _sha256(content)
            or entry.get("size") != len(content)
        ):
            raise Exp5SmokeEvidenceError(
                "smoke_evidence_source_generation_invalid",
                f"source smoke generation file integrity mismatch: {relative_path}",
            )
    return generation_root, {
        "generation_id": generation_id,
        "generation_manifest_digest": pointer["generation_manifest_digest"],
    }


def _validate_protocol_lifecycle(
    *,
    event_log_path: Path,
    task: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    condition_id: str,
    repeat_id: int,
    case_id: str,
) -> None:
    if (
        task.get("experiment_id") != EXP5_EXPERIMENT_ID
        or task.get("condition_id") != condition_id
        or task.get("repeat_id") != repeat_id
        or task.get("task_id") != case_id
        or task.get("pilot_only") is not True
        or task.get("regression_only") is not True
        or task.get("paper_eligible") is not False
    ):
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_protocol_lifecycle_invalid",
            f"source smoke committed task context is invalid: {condition_id}/{case_id}",
        )
    try:
        hash_chain_valid = EventLedger(event_log_path).verify_hash_chain()
    except (
        AttributeError,
        IndexError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_protocol_lifecycle_invalid",
            f"source smoke protocol event ledger is invalid: {condition_id}/{case_id}",
        ) from exc
    if not hash_chain_valid:
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_protocol_lifecycle_invalid",
            f"source smoke protocol event hash chain is invalid: {condition_id}/{case_id}",
        )
    event_refs = task.get("event_refs")
    if not isinstance(event_refs, list):
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_protocol_lifecycle_invalid",
            f"source smoke task event references are missing: {condition_id}/{case_id}",
        )
    expected_refs = [
        {
            "event_id": event.get("event_id"),
            "event_seq": event.get("event_seq"),
            "event_type": event.get("event_type"),
        }
        for event in events
    ]
    if event_refs != expected_refs:
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_protocol_lifecycle_invalid",
            f"source smoke task event references do not match its ledger: {condition_id}/{case_id}",
        )
    protocol_event_types: set[str] = set()
    for event in events:
        # 协议事件必须保持 EventLedger 的原始 envelope，不能为实验报表在顶层
        # 追加字段后破坏 event_hash。实验/condition/repeat/smoke 分类由已提交
        # task 与 run 路径绑定；事件本身只需严格绑定同一 case task。
        if event.get("task_id") != case_id:
            raise Exp5SmokeEvidenceError(
                "smoke_evidence_protocol_lifecycle_invalid",
                f"source smoke protocol event context is invalid: {condition_id}/{case_id}",
            )
        event_type = event.get("event_type")
        if isinstance(event_type, str):
            protocol_event_types.add(event_type)
    if not _REQUIRED_PROTOCOL_EVENT_TYPES <= protocol_event_types:
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_protocol_lifecycle_invalid",
            f"source smoke TokenShare lifecycle is incomplete: {condition_id}/{case_id}",
        )


def _expected_effective_request_controls_digest(
    member_plan: Mapping[str, Any],
) -> str:
    request_controls = member_plan.get("request_controls")
    endpoint_identity = member_plan.get("endpoint_identity")
    comparable = (
        request_controls.get("comparable")
        if isinstance(request_controls, Mapping)
        else None
    )
    reasoning = (
        endpoint_identity.get("effective_reasoning_controls")
        if isinstance(endpoint_identity, Mapping)
        else None
    )
    if not isinstance(comparable, Mapping) or not isinstance(reasoning, Mapping):
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_request_controls_mismatch",
            "source smoke expected request controls are missing",
        )
    required_body_fields = ("temperature", "top_p", "stream", "max_tokens")
    if any(field_name not in comparable for field_name in required_body_fields):
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_request_controls_mismatch",
            "source smoke expected request body controls are incomplete",
        )
    effective = {
        field_name: comparable[field_name] for field_name in required_body_fields
    }
    effective["response_format"] = {"type": "json_object"}
    effective.update(dict(reasoning))
    return digest_json(effective)


def _unique_records_by_string_key(
    records: Sequence[Any],
    *,
    key: str,
    reason: str,
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for record in records:
        value = record.get(key) if isinstance(record, Mapping) else None
        if not isinstance(value, str) or not value or value in result:
            raise Exp5SmokeEvidenceError(
                reason,
                f"source smoke {key} inventory is invalid",
            )
        result[value] = record
    return result


def _resolve_artifact(
    *,
    root: Path,
    condition: Mapping[str, Any],
    case_id: str,
    artifact_ref: Any,
    artifact_index: Sequence[Mapping[str, Any]],
) -> tuple[JsonObject, Mapping[str, Any]]:
    if not isinstance(artifact_ref, Mapping):
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_artifact_ref_missing",
            "source smoke artifact reference is missing",
        )
    artifact_id = artifact_ref.get("artifact_id")
    content_hash = artifact_ref.get("content_hash")
    matches = [
        index
        for index in artifact_index
        if index.get("artifact_id") == artifact_id
        and index.get("content_hash") == content_hash
        and index.get("experiment_id") == EXP5_EXPERIMENT_ID
        and index.get("condition_id") == condition.get("condition_id")
        and index.get("repeat_id") == condition.get("repeat_id")
        and index.get("task_id") == case_id
    ]
    if len(matches) != 1:
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_artifact_ref_mismatch",
            f"source smoke artifact index mismatch: {artifact_id}",
        )
    index = matches[0]
    relative_path = index.get("path")
    if not isinstance(relative_path, str) or not relative_path:
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_artifact_ref_mismatch",
            f"source smoke artifact path is missing: {artifact_id}",
        )
    path = root / relative_path
    if not path.is_file():
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_artifact_integrity_mismatch",
            f"source smoke artifact bytes are missing: {artifact_id}",
        )
    content = path.read_bytes()
    if (
        _sha256(content) != content_hash
        or artifact_ref.get("size_bytes") != len(content)
    ):
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_artifact_integrity_mismatch",
            f"source smoke artifact bytes/hash mismatch: {artifact_id}",
        )
    try:
        body = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_artifact_integrity_mismatch",
            f"source smoke artifact JSON is invalid: {artifact_id}",
        ) from exc
    if not isinstance(body, dict):
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_artifact_integrity_mismatch",
            f"source smoke artifact body is not an object: {artifact_id}",
        )
    return body, index


def _read_json(path: Path) -> JsonObject:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_source_suite_missing",
            f"required smoke evidence file is missing: {path.name}",
        ) from exc
    if not isinstance(body, dict):
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_source_suite_invalid",
            f"smoke evidence file must contain an object: {path.name}",
        )
    return body


def _read_jsonl(path: Path) -> list[JsonObject]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise Exp5SmokeEvidenceError(
            "smoke_evidence_source_suite_missing",
            f"required smoke evidence file is missing: {path.name}",
        ) from exc
    result: list[JsonObject] = []
    for line in lines:
        if not line.strip():
            continue
        body = json.loads(line)
        if not isinstance(body, dict):
            raise Exp5SmokeEvidenceError(
                "smoke_evidence_source_suite_invalid",
                f"smoke evidence JSONL record is not an object: {path.name}",
            )
        result.append(body)
    return result


def _sha256(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def _non_negative_int(value: Any) -> bool:
    return type(value) is int and value >= 0


def _validation_result(
    *,
    bundle_digest: str | None,
    global_reasons: Sequence[str],
    member_reasons: Mapping[str, Sequence[str]],
) -> JsonObject:
    stable_global = list(dict.fromkeys(global_reasons))
    stable_members = {
        member_id: list(dict.fromkeys(reasons))
        for member_id, reasons in member_reasons.items()
    }
    return {
        "schema_version": "tokenshare.paper_exp5_smoke_evidence_validation.v1",
        "status": "blocked" if stable_global or any(stable_members.values()) else "passed",
        "bundle_digest": bundle_digest,
        "global_reasons": stable_global,
        "member_reasons": stable_members,
    }
