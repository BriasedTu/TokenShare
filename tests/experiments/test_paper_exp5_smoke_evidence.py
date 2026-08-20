import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from tokenshare.experiments.paper_model_policy import (
    build_model_endpoint_cohort_preflight,
    load_model_endpoint_cohort,
    load_model_entry_map,
    load_provider_config_map,
)
from tokenshare.experiments.paper_models import digest_json
from tokenshare.storage.events import EventLedger


COHORT_PATH = Path("benchmarks/paper/model_comparison_cohort.v3.json")
ENTRY_MAP_PATH = Path("benchmarks/paper/model_comparison_entry_map.v3.json")
CONFIG_PATH = Path("benchmarks/paper/exp5_siliconflow_provider_config.v3.json")
PROFILE_PATH = Path("benchmarks/paper/paper_smoke_exp5_profile.v4.json")
EXPERIMENT_ID = "exp5_real_ai_model_endpoint_comparison"


def test_build_bundle_derives_four_members_and_two_verified_domains(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments import paper_exp5_smoke_evidence

    build_exp5_smoke_evidence_bundle = getattr(
        paper_exp5_smoke_evidence,
        "build_exp5_smoke_evidence_bundle",
        None,
    )
    assert callable(build_exp5_smoke_evidence_bundle), "production bundle builder is missing"
    write_bundle = getattr(
        paper_exp5_smoke_evidence,
        "write_exp5_smoke_evidence_bundle",
        None,
    )
    load_bundle = getattr(
        paper_exp5_smoke_evidence,
        "load_exp5_smoke_evidence_bundle",
        None,
    )
    assert callable(write_bundle), "production bundle writer is missing"
    assert callable(load_bundle), "production bundle loader is missing"

    cohort, entry_map, provider_configs, bootstrap = _policy_inputs(monkeypatch)
    source_root = _write_smoke_suite(
        tmp_path / "paper_smoke_exp5_v4",
        preflight=bootstrap,
    )

    bundle = build_exp5_smoke_evidence_bundle(
        source_suite_root=source_root,
        entry_map=entry_map,
    )

    assert bundle["source_suite_ref"]["source_suite_id"] == "paper_smoke_exp5_v4"
    assert bundle["source_suite_ref"]["real_transport"] is True
    assert bundle["bundle_digest"].startswith("sha256:")
    assert set(bundle["members"]) == set(entry_map["members"])
    assert all(
        set(member["domain_evidence"]) == {"factorization", "lean_proof"}
        and member["provider_attempt_count"] == 2
        and all(member["capability_checks"].values())
        for member in bundle["members"].values()
    )
    bundle_path = source_root / "audit" / "exp5_endpoint_smoke_evidence.json"
    written = write_bundle(
        source_suite_root=source_root,
        entry_map=entry_map,
        output_path=bundle_path,
    )
    assert written == bundle
    assert load_bundle(bundle_path) == bundle

    formal = build_model_endpoint_cohort_preflight(
        cohort=cohort,
        entry_map=entry_map,
        provider_configs=provider_configs,
        smoke_evidence_bundle=bundle,
        pricing_freshness_as_of="2026-08-15",
    )
    assert formal["status"] == "planned"
    assert formal["smoke_evidence_bundle_digest"] == bundle["bundle_digest"]


def test_formal_preflight_detects_stale_source_artifact_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments import paper_exp5_smoke_evidence

    build_exp5_smoke_evidence_bundle = getattr(
        paper_exp5_smoke_evidence,
        "build_exp5_smoke_evidence_bundle",
        None,
    )
    assert callable(build_exp5_smoke_evidence_bundle), "production bundle builder is missing"

    cohort, entry_map, provider_configs, bootstrap = _policy_inputs(monkeypatch)
    source_root = _write_smoke_suite(
        tmp_path / "paper_smoke_exp5_v4",
        preflight=bootstrap,
    )
    bundle = build_exp5_smoke_evidence_bundle(
        source_suite_root=source_root,
        entry_map=entry_map,
    )
    raw_path = next(source_root.glob("experiments/*/runs/*/*/artifacts/*/raw_*"))
    raw_path.write_bytes(raw_path.read_bytes() + b"\n")

    formal = build_model_endpoint_cohort_preflight(
        cohort=cohort,
        entry_map=entry_map,
        provider_configs=provider_configs,
        smoke_evidence_bundle=bundle,
        pricing_freshness_as_of="2026-08-15",
    )

    assert formal["status"] == "blocked"
    assert all(
        "smoke_evidence_artifact_integrity_mismatch" in plan["blocked_reasons"]
        for plan in formal["member_plans"].values()
    )


def test_validator_maps_unresolvable_persisted_path_to_blocked_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments.paper_exp5_smoke_evidence import (
        validate_exp5_smoke_evidence_bundle,
    )

    cohort, entry_map, _provider_configs, _bootstrap = _policy_inputs(monkeypatch)
    monkeypatch.setenv("TOKENSHARE_DATA_ROOT", "relative-data-root")
    bundle = {
        "schema_version": "tokenshare.paper_exp5_endpoint_smoke_evidence_bundle.v1",
        "source_suite_ref": {"source_suite_root": "outputs/missing-smoke"},
        "entry_map_digest": digest_json(entry_map),
        "model_cohort_id": cohort["cohort_id"],
        "model_cohort_digest": cohort["model_cohort_digest"],
        "members": {},
    }
    bundle["bundle_digest"] = digest_json(bundle)

    validation = validate_exp5_smoke_evidence_bundle(
        bundle,
        entry_map=entry_map,
        model_cohort_id=cohort["cohort_id"],
        model_cohort_digest=cohort["model_cohort_digest"],
        expected_member_ids=tuple(entry_map["members"]),
    )

    assert validation["status"] == "blocked"
    assert validation["global_reasons"] == [
        "smoke_evidence_source_suite_invalid"
    ]


def test_build_bundle_rejects_event_inventory_without_tokenshare_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments.paper_exp5_smoke_evidence import (
        Exp5SmokeEvidenceError,
        build_exp5_smoke_evidence_bundle,
    )

    _cohort, entry_map, _provider_configs, bootstrap = _policy_inputs(monkeypatch)
    source_root = _write_smoke_suite(tmp_path / "smoke", preflight=bootstrap)
    generation_root = _first_generation_root(source_root)
    _write_jsonl(
        generation_root / "events" / "event_log.jsonl",
        [
            {
                "experiment_id": EXPERIMENT_ID,
                "condition_id": generation_root.parents[2].name,
                "repeat_id": 0,
                "task_id": _first_task_id(generation_root),
                "event_id": "completed_without_protocol",
                "event_type": "TASK_COMPLETED",
            }
        ],
    )
    _refresh_generation_commit(generation_root)

    with pytest.raises(Exp5SmokeEvidenceError) as exc_info:
        build_exp5_smoke_evidence_bundle(
            source_suite_root=source_root,
            entry_map=entry_map,
        )

    assert exc_info.value.reason == "smoke_evidence_protocol_lifecycle_invalid"


def test_build_bundle_rejects_request_control_digest_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments.paper_exp5_smoke_evidence import (
        Exp5SmokeEvidenceError,
        build_exp5_smoke_evidence_bundle,
    )

    _cohort, entry_map, _provider_configs, bootstrap = _policy_inputs(monkeypatch)
    source_root = _write_smoke_suite(tmp_path / "smoke", preflight=bootstrap)
    generation_root = _first_generation_root(source_root)
    _rewrite_provenance_controls_digest(
        source_root=source_root,
        generation_root=generation_root,
        replacement="sha256:" + "0" * 64,
    )

    with pytest.raises(Exp5SmokeEvidenceError) as exc_info:
        build_exp5_smoke_evidence_bundle(
            source_suite_root=source_root,
            entry_map=entry_map,
        )

    assert exc_info.value.reason == "smoke_evidence_request_controls_mismatch"


def test_build_bundle_rejects_noncanonical_profile_even_with_valid_self_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments.paper_exp5_smoke_evidence import (
        Exp5SmokeEvidenceError,
        build_exp5_smoke_evidence_bundle,
    )

    _cohort, entry_map, _provider_configs, bootstrap = _policy_inputs(monkeypatch)
    source_root = _write_smoke_suite(tmp_path / "smoke", preflight=bootstrap)
    profile = _read_json(source_root / "smoke_profile.json")
    profile["profile_version"] = "locally_rewritten"
    profile["profile_digest"] = digest_json(
        {key: value for key, value in profile.items() if key != "profile_digest"}
    )
    _write_json(source_root / "smoke_profile.json", profile)
    plan = _read_json(source_root / "smoke_execution_plan.json")
    plan["profile_digest"] = profile["profile_digest"]
    plan["execution_plan_digest"] = digest_json(
        {key: value for key, value in plan.items() if key != "execution_plan_digest"}
    )
    _write_json(source_root / "smoke_execution_plan.json", plan)

    with pytest.raises(Exp5SmokeEvidenceError) as exc_info:
        build_exp5_smoke_evidence_bundle(
            source_suite_root=source_root,
            entry_map=entry_map,
        )

    assert exc_info.value.reason == "smoke_evidence_source_profile_mismatch"


def test_build_bundle_rejects_suite_identity_digest_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments.paper_exp5_smoke_evidence import (
        Exp5SmokeEvidenceError,
        build_exp5_smoke_evidence_bundle,
    )

    _cohort, entry_map, _provider_configs, bootstrap = _policy_inputs(monkeypatch)
    source_root = _write_smoke_suite(tmp_path / "smoke", preflight=bootstrap)
    suite = _read_json(source_root / "suite_manifest.json")
    suite["suite_identity"]["identity"]["digest"] = "sha256:" + "0" * 64
    _write_json(source_root / "suite_manifest.json", suite)

    with pytest.raises(Exp5SmokeEvidenceError) as exc_info:
        build_exp5_smoke_evidence_bundle(
            source_suite_root=source_root,
            entry_map=entry_map,
        )

    assert exc_info.value.reason == "smoke_evidence_source_identity_mismatch"


def test_build_bundle_rejects_cross_condition_task_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments.paper_exp5_smoke_evidence import (
        Exp5SmokeEvidenceError,
        build_exp5_smoke_evidence_bundle,
    )

    _cohort, entry_map, _provider_configs, bootstrap = _policy_inputs(monkeypatch)
    source_root = _write_smoke_suite(tmp_path / "smoke", preflight=bootstrap)
    generation_root = _first_generation_root(source_root)
    tasks = _read_jsonl(generation_root / "per_task_results.jsonl")
    tasks[0]["condition_id"] = "another_condition"
    _write_jsonl(generation_root / "per_task_results.jsonl", tasks)
    _refresh_generation_commit(generation_root)

    with pytest.raises(Exp5SmokeEvidenceError) as exc_info:
        build_exp5_smoke_evidence_bundle(
            source_suite_root=source_root,
            entry_map=entry_map,
        )

    assert exc_info.value.reason == "smoke_evidence_protocol_lifecycle_invalid"


def test_build_bundle_rejects_launch_without_explicit_real_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments.paper_exp5_smoke_evidence import (
        Exp5SmokeEvidenceError,
        build_exp5_smoke_evidence_bundle,
    )

    _cohort, entry_map, _provider_configs, bootstrap = _policy_inputs(monkeypatch)
    source_root = _write_smoke_suite(tmp_path / "smoke", preflight=bootstrap)
    launch = _read_json(source_root / "smoke_launch_manifest.json")
    launch["command"]["argv"].remove("--real-transport")
    launch["launch_manifest_digest"] = digest_json(
        {key: value for key, value in launch.items() if key != "launch_manifest_digest"}
    )
    _write_json(source_root / "smoke_launch_manifest.json", launch)

    with pytest.raises(Exp5SmokeEvidenceError) as exc_info:
        build_exp5_smoke_evidence_bundle(
            source_suite_root=source_root,
            entry_map=entry_map,
        )

    assert exc_info.value.reason == "smoke_evidence_real_transport_unverified"


def test_build_bundle_accepts_completed_with_failures_when_all_endpoints_are_proven(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments.paper_exp5_smoke_evidence import (
        build_exp5_smoke_evidence_bundle,
    )

    _cohort, entry_map, _provider_configs, bootstrap = _policy_inputs(monkeypatch)
    source_root = _write_smoke_suite(tmp_path / "smoke", preflight=bootstrap)
    suite = _read_json(source_root / "suite_manifest.json")
    suite["status"] = "completed_with_failures"
    _write_json(source_root / "suite_manifest.json", suite)

    bundle = build_exp5_smoke_evidence_bundle(
        source_suite_root=source_root,
        entry_map=entry_map,
    )

    assert bundle["bundle_digest"].startswith("sha256:")


def test_build_bundle_maps_malformed_member_plan_to_structured_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments.paper_exp5_smoke_evidence import (
        Exp5SmokeEvidenceError,
        build_exp5_smoke_evidence_bundle,
    )

    _cohort, entry_map, _provider_configs, bootstrap = _policy_inputs(monkeypatch)
    source_root = _write_smoke_suite(tmp_path / "smoke", preflight=bootstrap)
    suite = _read_json(source_root / "suite_manifest.json")
    identity = suite["suite_identity"]["identity"]
    for endpoint in identity["body"]["endpoints"]:
        endpoint["endpoint_binding"]["member_plans"][
            "glm_5_2_siliconflow"
        ].pop("pricing_snapshot_digest", None)
    identity["digest"] = digest_json(identity["body"])
    _write_json(source_root / "suite_manifest.json", suite)

    with pytest.raises(Exp5SmokeEvidenceError) as exc_info:
        build_exp5_smoke_evidence_bundle(
            source_suite_root=source_root,
            entry_map=entry_map,
        )

    assert exc_info.value.reason == "smoke_evidence_usage_schema_mismatch"


def _policy_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key")
    cohort = load_model_endpoint_cohort(COHORT_PATH)
    entry_map = load_model_entry_map(ENTRY_MAP_PATH)
    provider_configs = load_provider_config_map({"siliconflow": CONFIG_PATH})
    bootstrap = build_model_endpoint_cohort_preflight(
        cohort=cohort,
        entry_map=entry_map,
        provider_configs=provider_configs,
        require_smoke_evidence=False,
        pricing_freshness_as_of="2026-08-15",
    )
    assert bootstrap["status"] == "planned"
    return cohort, entry_map, provider_configs, bootstrap


def _write_smoke_suite(root: Path, *, preflight: dict[str, Any]) -> Path:
    root.mkdir()
    profile = _read_json(PROFILE_PATH)
    conditions: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    endpoint_records: list[dict[str, Any]] = []
    for profile_item in profile["items"]:
        selector = profile_item["condition_selector"]
        member_id = selector["cohort_member_id"]
        domain = selector["domain"]
        plan = preflight["member_plans"][member_id]
        condition_id = f"smoke_{member_id}_{domain}"
        case_id = profile_item["case_id"]
        condition = {
            "schema_version": "tokenshare.paper_condition.v3",
            "experiment_id": EXPERIMENT_ID,
            "condition_id": condition_id,
            "condition_digest": "sha256:" + hashlib.sha256(
                condition_id.encode("utf-8")
            ).hexdigest(),
            "repeat_id": 0,
            "domain": domain,
            "real_transport_required": True,
            "cohort_member_id": member_id,
            "model_cohort_id": preflight["cohort_id"],
            "model_cohort_digest": preflight["model_cohort_digest"],
            "provider_config_id": plan["provider_config_id"],
            "model_entry_id": plan["selected_entry_id"],
            "provider_family": plan["provider_family"],
            "provider_model_id": plan["provider_model_id"],
            "reasoning_profile_id": plan["reasoning_profile_id"],
            "model_endpoint_identity_digest": plan[
                "model_endpoint_identity_digest"
            ],
            "source_provider_config_digest": plan[
                "source_provider_config_digest"
            ],
        }
        conditions.append(condition)
        items.append(
            {
                **profile_item,
                "condition_id": condition_id,
                "condition_digest": condition["condition_digest"],
                "selection_id": "exp5:v3:test",
                "selection_digest": "sha256:" + "8" * 64,
            }
        )
        endpoint_records.append(
            {
                "experiment_id": EXPERIMENT_ID,
                "condition_id": condition_id,
                "endpoint_binding": preflight,
            }
        )
        _write_generation(
            root=root,
            condition=condition,
            case_id=case_id,
            member_plan=plan,
        )

    plan = {
        "schema_version": "tokenshare.paper_smoke_execution_plan.v1",
        "suite_id": "paper_smoke_exp5_v4",
        "profile_digest": profile["profile_digest"],
        "direct_root_run_count": 8,
        "experiment_ids": [EXPERIMENT_ID],
        "formal": False,
        "pilot_only": True,
        "regression_only": True,
        "paper_eligible": False,
        "items": items,
    }
    plan["execution_plan_digest"] = digest_json(plan)
    suite = {
        "schema_version": "tokenshare.paper_formal_runner.v1",
        "suite_id": "paper_smoke_exp5_v4",
        "status": "completed",
        "capturing": False,
        "execution_scope": "smoke_suite",
        "formal": False,
        "pilot_only": True,
        "regression_only": True,
        "paper_eligible": False,
        "suite_identity": {
            "identity": {
                "body": {
                    "schema_version": "tokenshare.paper_formal_runner_identity.v1",
                    "endpoints": endpoint_records,
                },
            },
            "dispatch": {
                "body": {
                    "plans": [
                        {
                            "experiment_id": EXPERIMENT_ID,
                            "conditions": conditions,
                            "selections": [
                                {"ordered_case_ids": [item["case_id"]]}
                                for item in items
                            ],
                        }
                    ]
                }
            },
        },
    }
    suite["suite_identity"]["identity"]["digest"] = digest_json(
        suite["suite_identity"]["identity"]["body"]
    )
    launch_manifest = {
        "schema_version": "tokenshare.paper_smoke_launch_manifest.v1",
        "suite_id": "paper_smoke_exp5_v4",
        "profile_digest": profile["profile_digest"],
        "execution_plan_digest": plan["execution_plan_digest"],
        "formal": False,
        "pilot_only": True,
        "regression_only": True,
        "paper_eligible": False,
        "command": {"argv": ["--smoke-profile", "profile.json", "--real-transport"]},
    }
    launch_manifest["launch_manifest_digest"] = digest_json(launch_manifest)
    _write_json(root / "smoke_profile.json", profile)
    _write_json(root / "smoke_execution_plan.json", plan)
    _write_json(root / "smoke_launch_manifest.json", launch_manifest)
    _write_json(root / "suite_manifest.json", suite)
    _write_jsonl(root / "conditions.jsonl", conditions)
    return root


def _write_generation(
    *,
    root: Path,
    condition: dict[str, Any],
    case_id: str,
    member_plan: dict[str, Any],
) -> None:
    condition_id = condition["condition_id"]
    run_root = root / "experiments" / EXPERIMENT_ID / "runs" / condition_id / "0"
    generation_id = f"generation_{condition_id}"
    generation_root = run_root / ".generations" / generation_id
    artifact_root = run_root / "artifacts" / case_id
    artifact_root.mkdir(parents=True)
    attempt_id = f"attempt_{condition_id}"
    comparable_controls = member_plan["request_controls"]["comparable"]
    effective_request_controls = {
        field_name: comparable_controls[field_name]
        for field_name in ("temperature", "top_p", "stream", "max_tokens")
    }
    effective_request_controls["response_format"] = {"type": "json_object"}
    effective_request_controls.update(
        member_plan["endpoint_identity"]["effective_reasoning_controls"]
    )
    request_identity = {
        "schema_version": "phase7.provider_request_identity.v2",
        "provider_family": member_plan["provider_family"],
        "entry_id": member_plan["selected_entry_id"],
        "configured_model": member_plan["provider_model_id"],
        "requested_model": member_plan["provider_model_id"],
        "effective_request_controls_digest": digest_json(
            effective_request_controls
        ),
        "reasoning_controls": member_plan["endpoint_identity"][
            "effective_reasoning_controls"
        ],
    }
    request_ref, request_index = _write_artifact(
        root=root,
        artifact_root=artifact_root,
        condition=condition,
        case_id=case_id,
        artifact_id=f"request_{condition_id}",
        artifact_type="ExecutionRequest",
        schema_version="phase3.execution_request.v1",
        body={
            "schema_version": "phase3.execution_request.v1",
            "request_id": f"request_{condition_id}",
            "limits": {"timeout_seconds": 600, "max_tokens": 32768},
        },
    )
    raw_ref, raw_index = _write_artifact(
        root=root,
        artifact_root=artifact_root,
        condition=condition,
        case_id=case_id,
        artifact_id=f"raw_{condition_id}",
        artifact_type="RawModelOutput",
        schema_version="phase7.raw_model_output.v2",
        body={
            "schema_version": "phase7.raw_model_output.v2",
            "provider_family": member_plan["provider_family"],
            "entry_id": member_plan["selected_entry_id"],
            "configured_model": member_plan["provider_model_id"],
            "requested_model": member_plan["provider_model_id"],
            "resolved_model": member_plan["provider_model_id"],
            "response_model_status": "present",
        },
    )
    provenance_ref, provenance_index = _write_artifact(
        root=root,
        artifact_root=artifact_root,
        condition=condition,
        case_id=case_id,
        artifact_id=f"provenance_{condition_id}",
        artifact_type="AIProviderCallProvenance",
        schema_version="phase7.ai_provider_call_provenance.v2",
        body={
            "schema_version": "phase7.ai_provider_call_provenance.v2",
            "provider_family": member_plan["provider_family"],
            "config_digest": member_plan["source_provider_config_digest"],
            "final_entry_id": member_plan["selected_entry_id"],
            "final_result_kind": "succeeded",
            "selection_record": {
                "selected_entry_id": member_plan["selected_entry_id"],
                "eligible_entry_ids": [member_plan["selected_entry_id"]],
                "attempt_entry_ids": [member_plan["selected_entry_id"]],
            },
            "attempts": [
                {
                    "provider_family": member_plan["provider_family"],
                    "entry_id": member_plan["selected_entry_id"],
                    "configured_model": member_plan["provider_model_id"],
                    "result_kind": "succeeded",
                    "provider_request_identity": request_identity,
                }
            ],
        },
    )
    enable_thinking = member_plan["endpoint_identity"][
        "effective_reasoning_controls"
    ].get("enable_thinking")
    usage_ref, usage_index = _write_artifact(
        root=root,
        artifact_root=artifact_root,
        condition=condition,
        case_id=case_id,
        artifact_id=f"usage_{condition_id}",
        artifact_type="AIUsageSummary",
        schema_version="tokenshare.paper_ai_usage.v1",
        body={
            "schema_version": "tokenshare.paper_ai_usage.v1",
            "provider_family": member_plan["provider_family"],
            "entry_id": member_plan["selected_entry_id"],
            "configured_model": member_plan["provider_model_id"],
            "requested_model": member_plan["provider_model_id"],
            "provider_attempt_count": 1,
            "prompt_tokens": 10,
            "completion_tokens": 4,
            "reasoning_tokens": 2 if enable_thinking is not False else None,
            "visible_output_basis": (
                "provider_reasoning_breakdown"
                if enable_thinking is not False
                else "explicit_non_thinking"
            ),
            "total_tokens": 14,
            "pricing_snapshot": member_plan["pricing_snapshot"],
        },
    )
    record_body = {
        "schema_version": "tokenshare.paper_model_execution_record.v2",
        "condition_id": condition_id,
        "repeat_id": 0,
        "task_id": case_id,
        "attempt_id": attempt_id,
        "expected_identity": member_plan["endpoint_identity"],
        "source_provider_config_digest": member_plan[
            "source_provider_config_digest"
        ],
        "prepared_execution_config_digest": digest_json(
            {"entry_id": member_plan["selected_entry_id"]}
        ),
        "requested_model": member_plan["provider_model_id"],
        "resolved_model": member_plan["provider_model_id"],
        "response_model_status": "present",
        "identity_status": "matched",
        "mismatch_reasons": [],
        "paper_eligible": True,
        "actual_request_identities": [request_identity],
        "actual_provider_attempts": [
            {
                "provider_family": member_plan["provider_family"],
                "entry_id": member_plan["selected_entry_id"],
                "configured_model": member_plan["provider_model_id"],
                "result_kind": "succeeded",
            }
        ],
        "request_ref": request_ref,
        "raw_output_ref": raw_ref,
        "provenance_ref": provenance_ref,
        "usage_ref": usage_ref,
    }
    record_body["record_digest"] = digest_json(record_body)
    record_ref, record_index = _write_artifact(
        root=root,
        artifact_root=artifact_root,
        condition=condition,
        case_id=case_id,
        artifact_id=f"model_record_{condition_id}",
        artifact_type="PaperModelExecutionRecord",
        schema_version="tokenshare.paper_model_execution_record.v2",
        body=record_body,
    )
    attempt = {
        "schema_version": "tokenshare.paper_attempt_result.v1",
        "experiment_id": EXPERIMENT_ID,
        "condition_id": condition_id,
        "repeat_id": 0,
        "task_id": case_id,
        "attempt_id": attempt_id,
        "attempt_status": "succeeded",
        "provider": member_plan["provider_family"],
        "model": member_plan["provider_model_id"],
        "entry_id": member_plan["selected_entry_id"],
        "provider_attempt_count": 1,
        "request_ref": request_ref,
        "raw_output_ref": raw_ref,
        "provenance_ref": provenance_ref,
        "usage_ref": usage_ref,
        "model_execution_record_ref": record_ref,
    }
    task = {
        "schema_version": "tokenshare.paper_task_result.v1",
        "experiment_id": EXPERIMENT_ID,
        "condition_id": condition_id,
        "repeat_id": 0,
        "task_id": case_id,
        "domain": condition["domain"],
        "root_status": "completed",
        "paper_eligible": False,
        "pilot_only": True,
        "regression_only": True,
        "evidence_integrity": "complete",
    }
    events = _protocol_event_records(
        generation_root=generation_root,
        condition=condition,
        case_id=case_id,
    )
    task["event_refs"] = [
        {
            "event_id": event["event_id"],
            "event_seq": event["event_seq"],
            "event_type": event["event_type"],
        }
        for event in events
    ]
    event_hashes = [event["event_hash"] for event in events]
    task["protocol_event_ledger"] = {
        "schema_version": "tokenshare.protocol_event_ledger_mapping.v1",
        "case_task_id": case_id,
        "protocol_task_ids": [case_id],
        "event_count": len(events),
        "event_hashes": event_hashes,
        "first_event_hash": event_hashes[0],
        "last_event_hash": event_hashes[-1],
    }
    files = {
        "run_manifest.json": {
            "schema_version": "tokenshare.paper_run_evidence.v1",
            "generation_id": generation_id,
            "experiment_id": EXPERIMENT_ID,
            "condition_id": condition_id,
            "repeat_id": 0,
            "status": "completed",
            "task_ids": [case_id],
            "completed_task_ids": [case_id],
        },
        "per_task_results.jsonl": [task],
        "per_attempt_results.jsonl": [attempt],
        "fault_injections.jsonl": [],
        "events/event_log.jsonl": events,
        "artifacts/artifact_index.jsonl": [
            request_index,
            raw_index,
            provenance_index,
            usage_index,
            record_index,
        ],
    }
    manifest_files: list[dict[str, Any]] = []
    for relative_path, body in files.items():
        path = generation_root / relative_path
        if relative_path.endswith(".jsonl"):
            _write_jsonl(path, body)
        else:
            _write_json(path, body)
        content = path.read_bytes()
        manifest_files.append(_generation_file_evidence(path, relative_path))
    generation_manifest = {
        "schema_version": "tokenshare.paper_checkpoint_generation.v1",
        "generation_id": generation_id,
        "files": manifest_files,
    }
    _write_json(generation_root / "generation_manifest.json", generation_manifest)
    _write_json(
        run_root / "CURRENT.json",
        {
            "schema_version": "tokenshare.paper_checkpoint_current.v1",
            "generation_id": generation_id,
            "generation_manifest_digest": digest_json(generation_manifest),
        },
    )


def _protocol_event_records(
    *,
    generation_root: Path,
    condition: dict[str, Any],
    case_id: str,
) -> list[dict[str, Any]]:
    event_path = generation_root / "events" / "event_log.jsonl"
    required_types = (
        "TASK_REGISTERED",
        "TASK_UNIT_CREATED",
        "LEASE_STATE_CHANGED",
        "ATTEMPT_STATE_CHANGED",
        "EXECUTION_REQUEST_RECORDED",
        "EXECUTION_SUBMISSION_RECORDED",
    )
    ledger = EventLedger(event_path)
    events: list[dict[str, Any]] = []
    for index, event_type in enumerate(required_types, start=1):
        event = ledger.append(
            event_type=event_type,
            object_type="TaskUnit",
            object_id=f"unit_{case_id}",
            payload={"test_sequence": index},
            idempotency_key=f"{condition['condition_id']}:{event_type}",
            task_id=case_id,
            occurred_at="2026-07-31T00:00:00Z",
        ).to_dict()
        events.append(event)
    return events


def _write_artifact(
    *,
    root: Path,
    artifact_root: Path,
    condition: dict[str, Any],
    case_id: str,
    artifact_id: str,
    artifact_type: str,
    schema_version: str,
    body: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    content = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    path = artifact_root / artifact_id
    path.write_bytes(content)
    content_hash = _sha256(content)
    ref = {
        "schema_version": "ArtifactRef.v1",
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "uri": f"artifacts/{artifact_id}",
        "content_hash": content_hash,
        "size_bytes": len(content),
        "media_type": "application/json",
        "artifact_schema_id": schema_version.rsplit(".v", 1)[0],
        "artifact_schema_version": schema_version.rsplit(".", 1)[-1],
        "source": {"kind": "fixture"},
        "metadata": {},
        "created_at": "2026-07-31T00:00:00Z",
    }
    index = {
        "experiment_id": EXPERIMENT_ID,
        "condition_id": condition["condition_id"],
        "repeat_id": 0,
        "task_id": case_id,
        "artifact_id": artifact_id,
        "path": path.relative_to(root).as_posix(),
        "content_hash": content_hash,
        "size_bytes": len(content),
        "source_artifact_ref": ref,
    }
    return ref, index


def _first_generation_root(source_root: Path) -> Path:
    current_path = next(source_root.glob("experiments/*/runs/*/*/CURRENT.json"))
    current = _read_json(current_path)
    return current_path.parent / ".generations" / current["generation_id"]


def _first_task_id(generation_root: Path) -> str:
    return str(_read_jsonl(generation_root / "per_task_results.jsonl")[0]["task_id"])


def _refresh_generation_commit(generation_root: Path) -> None:
    manifest = _read_json(generation_root / "generation_manifest.json")
    refreshed_files = []
    for entry in manifest["files"]:
        path = generation_root / entry["path"]
        refreshed_files.append(_generation_file_evidence(path, entry["path"]))
    manifest["files"] = refreshed_files
    _write_json(generation_root / "generation_manifest.json", manifest)
    _write_json(
        generation_root.parents[1] / "CURRENT.json",
        {
            "schema_version": "tokenshare.paper_checkpoint_current.v1",
            "generation_id": generation_root.name,
            "generation_manifest_digest": digest_json(manifest),
        },
    )


def _rewrite_provenance_controls_digest(
    *,
    source_root: Path,
    generation_root: Path,
    replacement: str,
) -> None:
    attempts = _read_jsonl(generation_root / "per_attempt_results.jsonl")
    indexes = _read_jsonl(
        generation_root / "artifacts" / "artifact_index.jsonl"
    )
    attempt = attempts[0]
    provenance_index = next(
        item
        for item in indexes
        if item["artifact_id"] == attempt["provenance_ref"]["artifact_id"]
    )
    provenance_path = source_root / provenance_index["path"]
    provenance = _read_json(provenance_path)
    provenance["attempts"][0]["provider_request_identity"][
        "effective_request_controls_digest"
    ] = replacement
    _write_json(provenance_path, provenance, trailing_newline=False)
    provenance_hash = _sha256(provenance_path.read_bytes())
    provenance_index["content_hash"] = provenance_hash
    attempt["provenance_ref"]["content_hash"] = provenance_hash

    record_index = next(
        item
        for item in indexes
        if item["artifact_id"]
        == attempt["model_execution_record_ref"]["artifact_id"]
    )
    record_path = source_root / record_index["path"]
    record = _read_json(record_path)
    record["provenance_ref"] = attempt["provenance_ref"]
    record["actual_request_identities"][0][
        "effective_request_controls_digest"
    ] = replacement
    record["record_digest"] = digest_json(
        {key: value for key, value in record.items() if key != "record_digest"}
    )
    _write_json(record_path, record, trailing_newline=False)
    record_hash = _sha256(record_path.read_bytes())
    record_index["content_hash"] = record_hash
    attempt["model_execution_record_ref"]["content_hash"] = record_hash

    _write_jsonl(generation_root / "per_attempt_results.jsonl", attempts)
    _write_jsonl(
        generation_root / "artifacts" / "artifact_index.jsonl",
        indexes,
    )
    _refresh_generation_commit(generation_root)


def _read_json(path: Path) -> dict[str, Any]:
    body = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(body, dict)
    return body


def _generation_file_evidence(path: Path, relative_path: str) -> dict[str, Any]:
    content = path.read_bytes()
    records = (
        [json.loads(content.decode("utf-8"))]
        if path.suffix == ".json"
        else [
            json.loads(line)
            for line in content.decode("utf-8").splitlines()
            if line.strip()
        ]
    )
    return {
        "path": relative_path,
        "size": len(content),
        "content_sha256": _sha256(content),
        "record_count": len(records),
        "records_digest": digest_json(records),
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        body
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
        for body in [json.loads(line)]
    ]


def _write_json(
    path: Path,
    body: Any,
    *,
    trailing_newline: bool = True,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + ("\n" if trailing_newline else ""),
        encoding="utf-8",
    )


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
            for record in records
        ),
        encoding="utf-8",
    )


def _sha256(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()
