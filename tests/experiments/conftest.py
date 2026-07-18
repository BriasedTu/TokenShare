from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tokenshare.experiments.paper_models import digest_json
from tokenshare.experiments.paper_unit_commitments import (
    build_ai_unit_binding_from_request,
    factorization_range_plugin_payload,
    lean_simple_plugin_payload,
)
from tokenshare.storage.artifacts import ArtifactStore


NOW = "2026-07-17T00:00:00Z"
SUITE_STARTED_AT = "2026-07-17T00:00:00.000000Z"
SUITE_FINISHED_AT = "2026-07-17T00:00:04.000000Z"


@pytest.fixture
def complete_paper_evidence_suite(tmp_path: Path) -> dict[str, Any]:
    """构造可独立复算的双领域 pilot evidence，不依赖 provider。"""

    suite_root = tmp_path / "paper_exp1_metrics_fixture"
    suite_root.mkdir()
    budget_digest = "sha256:" + "1" * 64
    profile_digest = "sha256:" + "2" * 64
    catalog_digest = "sha256:" + "3" * 64
    cases = (
        {
            "condition_id": "factor_condition",
            "run_id": "factor_run",
            "case_id": "factor_easy_01",
            "task_id": "paper_factorization_factor_easy_01",
            "domain": "factorization",
            "difficulty": "easy",
            "paper_difficulty": "easy",
            "topic_family": None,
            "root_status": "completed",
            "accepted_validity": True,
            "failure_stage": None,
            "failure_kind": None,
            "attempt_status": "succeeded",
            "tokens": 11,
            "latency_ms": 5,
            "cost": 0.01,
            "event_started_at": "2026-07-17T00:00:01.000000Z",
            "event_terminal_at": "2026-07-17T00:00:01.020000Z",
        },
        {
            "condition_id": "lean_condition",
            "run_id": "lean_run",
            "case_id": "lean_easy_01",
            "task_id": "paper_lean_lean_easy_01",
            "domain": "lean_proof",
            "difficulty": "easy",
            "paper_difficulty": "simple",
            "topic_family": "pure_logic",
            "root_status": "failed",
            "accepted_validity": False,
            "failure_stage": "checker",
            "failure_kind": "checker_rejected",
            "attempt_status": "checker_rejected",
            "tokens": 13,
            "latency_ms": 7,
            "cost": 0.02,
            "event_started_at": "2026-07-17T00:00:02.000000Z",
            "event_terminal_at": "2026-07-17T00:00:02.030000Z",
        },
    )
    blocked = {
        "condition_id": "blocked_condition",
        "run_id": "blocked_run",
        "case_id": "lean_v2_frontier_no_oracle_01",
        "task_id": "paper_lean_lean_v2_frontier_no_oracle_01",
        "domain": "lean_proof",
        "difficulty": "hard",
        "paper_difficulty": "hard_frontier",
        "topic_family": "pure_logic",
    }

    conditions: list[dict[str, Any]] = []
    planned_tasks: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    artifact_index: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = [
        _event(
            "suite_started",
            "suite_started",
            recorded_at=SUITE_STARTED_AT,
        )
    ]

    for ordinal, case in enumerate(cases):
        condition = _condition(case, catalog_digest=catalog_digest)
        conditions.append(condition)
        artifact_root = suite_root / "runs" / str(case["run_id"])
        artifact_bundle = _attempt_artifacts(artifact_root, case)
        refs = artifact_bundle["refs"]
        planned_tasks.append(
            _planned_task(
                case,
                ordinal=ordinal,
                ai_unit_binding=artifact_bundle["ai_unit_binding"],
            )
        )
        attempt = {
            "schema_version": "tokenshare.paper_attempt_result.v1",
            "condition_id": case["condition_id"],
            "repeat_id": 0,
            "run_id": case["run_id"],
            "task_id": case["task_id"],
            "unit_id": _unit_id(case),
            "planned_ai_unit_id": _planned_ai_unit_id(case),
            "attempt_id": f"{case['case_id']}_attempt_0",
            "worker_id": f"worker_{case['domain']}_0",
            "provider_attempt_index": 0,
            "attempt_status": case["attempt_status"],
            "provider": "siliconflow",
            "model": "zai-org/GLM-5.2",
            "entry_id": "glm_5_2_exp1_baseline",
            "request_ref": refs["request_ref"],
            "raw_output_ref": refs["raw_output_ref"],
            "parsed_output_ref": refs["parsed_output_ref"],
            "parse_failure_ref": None,
            "provenance_ref": refs["provenance_ref"],
            "usage_ref": refs["usage_ref"],
            "model_execution_record_ref": refs["model_execution_record_ref"],
            "started_at": NOW,
            "ended_at": NOW,
            "latency_ms": case["latency_ms"],
            "prompt_tokens": case["tokens"] - 3,
            "completion_tokens": 3,
            "total_tokens": case["tokens"],
            "cost_estimate": case["cost"],
            "error_kind": (
                "checker_rejected"
                if case["attempt_status"] == "checker_rejected"
                else None
            ),
            "fault_injection_ref": None,
            "paper_eligible": True,
        }
        attempts.append(attempt)
        task = {
            "schema_version": "tokenshare.paper_task_result.v1",
            "condition_id": case["condition_id"],
            "repeat_id": 0,
            "task_id": case["task_id"],
            "domain": case["domain"],
            "difficulty": case["difficulty"],
            "paper_difficulty": case["paper_difficulty"],
            "topic_family": case["topic_family"],
            "root_status": case["root_status"],
            "accepted_validity": case["accepted_validity"],
            "failure_stage": case["failure_stage"],
            "failure_kind": case["failure_kind"],
            "attempt_count": 1,
            "provider_attempt_count": 1,
            "wall_clock_ms": case["latency_ms"],
            "total_tokens": case["tokens"],
            "cost_estimate": case["cost"],
            "event_refs": [],
            "artifact_refs": [
                refs["raw_output_ref"],
                refs["model_execution_record_ref"],
            ],
            "paper_eligible": True,
        }
        tasks.append(task)
        runs.append(
            _run(
                case,
                artifact_root=artifact_root,
                status=(
                    "completed" if case["root_status"] == "completed" else "failed"
                ),
                paper_eligible=True,
                transport_evidence={
                    "real_transport": True,
                    "transport_kind": "ai_api",
                },
            )
        )
        for ref in refs.values():
            artifact_index.append(
                {
                    "schema_version": "tokenshare.paper_artifact_index_record.v1",
                    "run_id": case["run_id"],
                    "task_id": case["task_id"],
                    "artifact_ref": ref,
                }
            )
        events.extend(
            [
                _event(
                    f"{case['run_id']}:started",
                    "task_started",
                    run_id=str(case["run_id"]),
                    task_id=str(case["task_id"]),
                    recorded_at=str(case["event_started_at"]),
                ),
                _event(
                    f"{case['run_id']}:completed",
                    "task_completed",
                    run_id=str(case["run_id"]),
                    task_id=str(case["task_id"]),
                    recorded_at=str(case["event_terminal_at"]),
                    detail={
                        "root_status": case["root_status"],
                        "provider_attempt_count": 1,
                        "total_tokens": case["tokens"],
                        "cost_estimate": case["cost"],
                    },
                ),
            ]
        )

    blocked_condition = _condition(blocked, catalog_digest=catalog_digest)
    conditions.append(blocked_condition)
    planned_tasks.append(
        _planned_task(blocked, ordinal=len(planned_tasks), structured_blocked=True)
    )
    blocked_task = {
        "schema_version": "tokenshare.paper_task_result.v1",
        "condition_id": blocked["condition_id"],
        "repeat_id": 0,
        "task_id": blocked["task_id"],
        "domain": blocked["domain"],
        "difficulty": blocked["difficulty"],
        "paper_difficulty": blocked["paper_difficulty"],
        "topic_family": blocked["topic_family"],
        "root_status": "blocked",
        "accepted_validity": False,
        "failure_stage": None,
        "failure_kind": None,
        "attempt_count": 0,
        "provider_attempt_count": 0,
        "wall_clock_ms": 0,
        "total_tokens": 0,
        "cost_estimate": 0.0,
        "event_refs": [],
        "artifact_refs": [],
        "paper_eligible": False,
    }
    tasks.append(blocked_task)
    blocked_root = suite_root / "runs" / str(blocked["run_id"])
    blocked_root.mkdir(parents=True)
    runs.append(
        _run(
            blocked,
            artifact_root=blocked_root,
            status="blocked",
            paper_eligible=False,
            transport_evidence={
                "real_transport": False,
                "transport_kind": "not_called_structured_blocked",
            },
            execution_status="structured_blocked",
            ineligibility_reasons=["missing_oracle_proof_package"],
        )
    )
    events.extend(
        [
            _event(
                f"{blocked['run_id']}:blocked",
                "task_blocked",
                run_id=str(blocked["run_id"]),
                task_id=str(blocked["task_id"]),
                recorded_at="2026-07-17T00:00:03.000000Z",
            ),
            _event(
                "suite_finished:fixture",
                "suite_finished",
                recorded_at=SUITE_FINISHED_AT,
            ),
        ]
    )

    execution_plan = {
        "schema_version": "tokenshare.paper_exp1_pilot_execution_plan.v1",
        "suite_id": suite_root.name,
        "budget_digest": budget_digest,
        "profile_digest": profile_digest,
        "catalog_digest": catalog_digest,
        "planned_conditions": len(conditions),
        "planned_root_runs": len(planned_tasks),
        "planned_ai_units": len(attempts),
        "conditions": conditions,
        "tasks": planned_tasks,
        "provider_calls_made_before_execution": 0,
        "baseline_entry_id": "glm_5_2_exp1_baseline",
        "source_provider_config_digest": "sha256:" + "4" * 64,
        "model_endpoint_identity_digest": "sha256:" + "5" * 64,
        "hard_limits": {
            "provider_attempt_limit": 22,
            "token_limit": 97280,
            "cost_limit": 0.116736,
        },
        "stop_policy": "stop_after_current_task",
        "pilot_only": True,
    }
    execution_plan["execution_plan_digest"] = digest_json(execution_plan)

    _write_json(suite_root / "execution_plan.json", execution_plan)
    _write_jsonl(suite_root / "conditions.jsonl", conditions)
    _write_jsonl(suite_root / "run_results.jsonl", runs)
    _write_jsonl(suite_root / "per_task_results.jsonl", tasks)
    _write_jsonl(suite_root / "per_attempt_results.jsonl", attempts)
    _write_jsonl(suite_root / "events" / "event_log.jsonl", events)
    _write_jsonl(
        suite_root / "artifacts" / "artifact_index.jsonl", artifact_index
    )
    _write_json(
        suite_root / "suite_manifest.json",
        {
            "schema_version": "tokenshare.paper_exp1_pilot_execution_result.v1",
            "suite_id": suite_root.name,
            "status": "completed_with_failures",
            "output_root": suite_root.as_posix(),
            "budget_digest": budget_digest,
            "profile_digest": profile_digest,
            "catalog_digest": catalog_digest,
            "condition_count": len(conditions),
            "run_count": len(runs),
            "task_count": len(tasks),
            "blocked_run_count": 1,
            "provider_attempt_count": len(attempts),
            "provider_calls_made": len(attempts),
            "total_tokens": sum(int(item["total_tokens"]) for item in attempts),
            "total_cost_estimate": sum(
                float(item["cost_estimate"]) for item in attempts
            ),
            "replayed_run_count": 0,
            "paper_eligible": True,
            "stop_reason": None,
            "hard_limits": execution_plan["hard_limits"],
            "started_at": SUITE_STARTED_AT,
            "ended_at": SUITE_FINISHED_AT,
            "pilot_only": True,
        },
    )
    _refresh_evidence_manifest(suite_root)
    return {
        "root": suite_root,
        "refresh_manifest": lambda: _refresh_evidence_manifest(suite_root),
    }


def _condition(case: dict[str, Any], *, catalog_digest: str) -> dict[str, Any]:
    return {
        "schema_version": "tokenshare.paper_condition.v1",
        "experiment_id": "exp1_real_ai_feasibility",
        "condition_id": case["condition_id"],
        "domain": case["domain"],
        "difficulty": case["difficulty"],
        "paper_difficulty": case["paper_difficulty"],
        "topic_family": case["topic_family"],
        "worker_count": 10,
        "fault_type": "none",
        "fault_rate": 0.0,
        "ablation_mode": "FULL",
        "model_policy": "fixed_entry",
        "repeat_id": 0,
        "seed": 1701,
        "catalog_digest": catalog_digest,
        "paper_eligible_required": True,
        "real_transport_required": True,
    }


def _planned_task(
    case: dict[str, Any],
    *,
    ordinal: int,
    structured_blocked: bool = False,
    ai_unit_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ai_units = [] if structured_blocked else [_planned_ai_unit_id(case)]
    ai_unit_bindings = [] if structured_blocked else [dict(ai_unit_binding or {})]
    return {
        "schema_version": "tokenshare.paper_exp1_pilot_task_plan.v1",
        "ordinal": ordinal,
        "condition_id": case["condition_id"],
        "repeat_id": 0,
        "run_id": case["run_id"],
        "task_id": case["task_id"],
        "case_id": case["case_id"],
        "domain": case["domain"],
        "difficulty": case["difficulty"],
        "paper_difficulty": case["paper_difficulty"],
        "topic_family": case["topic_family"],
        "execution_status": (
            "structured_blocked" if structured_blocked else "executable"
        ),
        "blocked_reason": (
            "missing_oracle_proof_package" if structured_blocked else None
        ),
        "ai_units": ai_units,
        "ai_unit_bindings": ai_unit_bindings,
        "provider_attempt_upper_bound": 0 if structured_blocked else 1,
        "token_upper_bound": 0 if structured_blocked else 1024,
        "cost_upper_bound": 0.0 if structured_blocked else 0.05,
        "request_limits": {},
        "split_profile": {},
    }


def _run(
    case: dict[str, Any],
    *,
    artifact_root: Path,
    status: str,
    paper_eligible: bool,
    transport_evidence: dict[str, Any],
    execution_status: str = "executable",
    ineligibility_reasons: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "tokenshare.paper_run_result.v1",
        "condition_id": case["condition_id"],
        "repeat_id": 0,
        "run_id": case["run_id"],
        "case_id": case["case_id"],
        "execution_status": execution_status,
        "status": status,
        "run_manifest_ref": None,
        "per_task_results_ref": {"path": "per_task_results.jsonl"},
        "per_attempt_results_ref": {"path": "per_attempt_results.jsonl"},
        "fault_injections_ref": None,
        "event_log_ref": {"path": "events/event_log.jsonl"},
        "artifact_root": artifact_root.as_posix(),
        "transport_evidence": transport_evidence,
        "paper_eligible": paper_eligible,
        "ineligibility_reasons": ineligibility_reasons or [],
    }


def _attempt_artifacts(
    artifact_root: Path, case: dict[str, Any]
) -> dict[str, Any]:
    store = ArtifactStore(artifact_root)
    planned_ai_unit_id = _planned_ai_unit_id(case)
    unit_id = _unit_id(case)
    request_id = f"{case['case_id']}_request_0"
    attempt_id = f"{case['case_id']}_attempt_0"
    input_artifact_key, input_ref, input_body = _unit_input_artifact(
        store=store,
        case=case,
    )
    task_unit_snapshot = _task_unit_snapshot(
        case=case,
        unit_id=unit_id,
        planned_ai_unit_id=planned_ai_unit_id,
        input_artifact_key=input_artifact_key,
        input_ref=input_ref,
        input_body=input_body,
    )
    prompt_ref = _save_fixture_artifact(
        store=store,
        artifact_id=f"{case['case_id']}_prompt_package_0",
        artifact_type="PromptPackage",
        body={
            "schema_version": "phase3.prompt_package.v1",
            "request_id": request_id,
            "task_id": case["task_id"],
            "unit_id": unit_id,
            "prompt_contract": "fixture-only",
            "input_body_digest": digest_json(input_body),
        },
    )
    instruction_ref = _save_fixture_artifact(
        store=store,
        artifact_id=f"{case['case_id']}_execution_instruction_0",
        artifact_type="ExecutionInstruction",
        body={
            "schema_version": "phase3.execution_instruction.v1",
            "request_id": request_id,
            "task_id": case["task_id"],
            "unit_id": unit_id,
            "instruction_contract": "fixture-only",
            "input_body_digest": digest_json(input_body),
        },
    )
    request_body = {
        "schema_version": "phase3.execution_request.v1",
        "request_id": request_id,
        "attempt_id": attempt_id,
        "task_id": case["task_id"],
        "unit_id": unit_id,
        "input_artifact_refs": {input_artifact_key: input_ref},
        "prompt_package_ref": prompt_ref,
        "execution_instruction_ref": instruction_ref,
        "soft_hints": {
            "paper_condition_id": case["condition_id"],
            "planned_ai_unit_id": planned_ai_unit_id,
            "paper_provider_attempt_index": 0,
        },
        "task_unit_snapshot": task_unit_snapshot,
    }
    bodies = {
        "request_ref": request_body,
        "raw_output_ref": {
            "schema_version": "phase7.raw_model_output.v2",
            "configured_model": "zai-org/GLM-5.2",
            "requested_model": "zai-org/GLM-5.2",
            "resolved_model": "zai-org/GLM-5.2",
            "response_model_status": "present",
        },
        "parsed_output_ref": {"schema_version": "test.parsed_output.v1"},
        "provenance_ref": {
            "schema_version": "phase7.ai_api_provenance.v2",
            "transport_kind": "ai_api",
            "real_transport": True,
            "provider_attempts": [{"provider": "siliconflow"}],
        },
        "usage_ref": {
            "schema_version": "phase7.ai_api_usage.v1",
            "total_tokens": case["tokens"],
            "cost_estimate": case["cost"],
        },
        "model_execution_record_ref": {
            "schema_version": "tokenshare.paper_model_execution_record.v2",
            "identity_status": "matched",
            "paper_eligible": True,
        },
    }
    refs: dict[str, dict[str, Any]] = {}
    for field_name, body in bodies.items():
        artifact_id = f"{case['case_id']}_{field_name}"
        ref = store.save_json(
            body,
            artifact_id=artifact_id,
            artifact_type=field_name.removesuffix("_ref"),
            artifact_schema_id=str(body["schema_version"]).rsplit(".v", 1)[0],
            artifact_schema_version=str(body["schema_version"]),
            source={"case_id": case["case_id"]},
            metadata={},
            created_at=NOW,
        )
        refs[field_name] = ref.to_dict()
    refs["input_artifact_ref"] = input_ref
    refs["prompt_package_ref"] = prompt_ref
    refs["execution_instruction_ref"] = instruction_ref
    return {
        "refs": refs,
        "ai_unit_binding": build_ai_unit_binding_from_request(
            planned_ai_unit_id=planned_ai_unit_id,
            request_body=request_body,
            store=store,
            include_request_artifacts=True,
        ),
    }


def _unit_input_artifact(
    *,
    store: ArtifactStore,
    case: dict[str, Any],
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    if case["domain"] == "factorization":
        range_input = {
            "schema_version": "factorization.factor_search_range_input.v1",
            "target_n": "221",
            "range_start": "13",
            "range_end": "17",
            "coverage_id": f"coverage:{case['case_id']}:0",
            "child_index": 0,
            "child_count": 1,
            "partition_params_digest": "sha256:" + "6" * 64,
        }
        range_input["range_digest"] = digest_json(range_input)
        ref = _save_fixture_artifact(
            store=store,
            artifact_id=f"{case['case_id']}_range_input_0",
            artifact_type="FactorSearchRangeInput",
            body=range_input,
        )
        return "range_input", ref, range_input

    theorem_body = {
        "schema_version": "lean_proof.theorem_payload.v1",
        "theorem_id": f"fixture:{case['case_id']}:child0",
        "theorem_name": f"{case['case_id']}_child_0",
        "imports": ["Init"],
        "namespace": "TokenShareFixture",
        "open_namespaces": [],
        "options": {},
        "parameters_source": "",
        "theorem_source": "theorem fixture_child : True := by trivial",
        "proof_candidate_ref": None,
        "library_context": {"case_id": case["case_id"]},
        "decomposition_policy": {"policy_id": "fixture"},
        "resource_limits": {"timeout_seconds": 30, "max_output_bytes": 65536},
    }
    theorem_body["payload_digest"] = digest_json(theorem_body)
    ref = _save_fixture_artifact(
        store=store,
        artifact_id=f"{case['case_id']}_child_theorem_payload_0",
        artifact_type="LeanTheoremPayload",
        body=theorem_body,
    )
    return "child_theorem_payload", ref, theorem_body


def _task_unit_snapshot(
    *,
    case: dict[str, Any],
    unit_id: str,
    planned_ai_unit_id: str,
    input_artifact_key: str,
    input_ref: dict[str, Any],
    input_body: dict[str, Any],
) -> dict[str, Any]:
    if case["domain"] == "factorization":
        plugin_payload = factorization_range_plugin_payload(
            case=case,
            range_input_body=input_body,
        )
        unit_type = "factorization_range"
    else:
        plugin_payload = lean_simple_plugin_payload(
            case=case,
            child_logical_key="child:0",
            child_payload_body=input_body,
        )
        unit_type = "lean_simple_child"
    return {
        "task_id": case["task_id"],
        "unit_id": unit_id,
        "parent_unit_id": f"{case['case_id']}_root_unit",
        "depth": 1,
        "unit_type": unit_type,
        "input_refs": {input_artifact_key: input_ref},
        "plugin_payload": plugin_payload,
        "metadata": {
            "case_id": case["case_id"],
            "planned_ai_unit_id": planned_ai_unit_id,
        },
    }


def _planned_ai_unit_id(case: dict[str, Any]) -> str:
    return f"{case['case_id']}_planned_ai_unit_0"


def _unit_id(case: dict[str, Any]) -> str:
    return f"{case['case_id']}_protocol_unit_0"


def _save_fixture_artifact(
    *,
    store: ArtifactStore,
    artifact_id: str,
    artifact_type: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    ref = store.save_json(
        body,
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        artifact_schema_id=str(body["schema_version"]).rsplit(".v", 1)[0],
        artifact_schema_version=str(body["schema_version"]),
        source={"case_id": artifact_id},
        metadata={},
        created_at=NOW,
    )
    return ref.to_dict()


def _event(
    event_id: str,
    event_type: str,
    *,
    run_id: str | None = None,
    task_id: str | None = None,
    recorded_at: str = NOW,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "tokenshare.paper_orchestrator_event.v1",
        "event_id": event_id,
        "event_type": event_type,
        "suite_id": "paper_exp1_metrics_fixture",
        "run_id": run_id,
        "task_id": task_id,
        "detail": (
            {"stop_reason": None}
            if event_type == "suite_finished"
            else dict(detail or {})
        ),
        "recorded_at": recorded_at,
    }


def _refresh_evidence_manifest(suite_root: Path) -> None:
    plan = json.loads((suite_root / "execution_plan.json").read_text(encoding="utf-8"))
    files = {}
    for relative_path in (
        "execution_plan.json",
        "conditions.jsonl",
        "run_results.jsonl",
        "per_task_results.jsonl",
        "per_attempt_results.jsonl",
        "events/event_log.jsonl",
        "artifacts/artifact_index.jsonl",
    ):
        path = suite_root / relative_path
        records = (
            [json.loads(path.read_text(encoding="utf-8"))]
            if path.suffix == ".json"
            else [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        )
        files[relative_path] = {
            "record_count": len(records),
            "records_digest": digest_json(records),
        }
    manifest = {
        "schema_version": "tokenshare.paper_execution_evidence_manifest.v1",
        "execution_plan_digest": plan["execution_plan_digest"],
        "files": files,
    }
    manifest["evidence_manifest_digest"] = digest_json(manifest)
    _write_json(suite_root / "evidence_manifest.json", manifest)


def _write_json(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )
