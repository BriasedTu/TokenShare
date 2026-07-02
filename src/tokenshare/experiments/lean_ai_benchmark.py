"""Lean proof benchmark that routes proof candidates through the AI API executor."""

from __future__ import annotations

import csv
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tokenshare.core.models import (
    Attempt,
    AttemptState,
    JsonObject,
    Lease,
    LeaseState,
    ProtocolConfig,
    TaskState,
    TaskUnit,
)
from tokenshare.executors.ai_api import AIAPIExecutor
from tokenshare.executors.ai_api_config import AIAPIExecutorConfig, load_ai_api_config
from tokenshare.executors.ai_api_transport import UrlLibSiliconFlowTransport
from tokenshare.executors.contracts import EnvironmentRef, ExecutionRequest
from tokenshare.plugins.contracts import OutputContract
from tokenshare.plugins.lean_proof.child_proof import (
    LeanChildProofResult,
    check_lean_child_proof,
)
from tokenshare.plugins.lean_proof.descriptor import build_lean_proof_plugin_descriptor
from tokenshare.plugins.lean_proof.environment import (
    LeanEnvironmentManifest,
    build_lean_environment_ref,
)
from tokenshare.plugins.lean_proof.fixtures import default_lean_fixture_project_path
from tokenshare.plugins.lean_proof.merge_policy import (
    LeanProofMergeInput,
    merge_lean_child_proofs,
)
from tokenshare.plugins.lean_proof.models import LeanTheoremPayload, canonical_json_digest
from tokenshare.plugins.lean_proof.prompt_builder import (
    PROOF_CANDIDATE_OUTPUT_NAME,
    build_lean_proof_candidate_prompt_package,
    parse_lean_proof_candidate_ai_output,
)
from tokenshare.plugins.lean_proof.schemas import (
    CHECKER_VALIDATOR_POLICY_ID,
    DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
    LEAN_PROOF_CANDIDATE_SCHEMA_VERSION,
    PLUGIN_ID,
    PLUGIN_VERSION,
    PROOF_ARTIFACT_CONTRACT_ID,
    PROOF_CANDIDATE_PARSER_ID,
    schema_ref,
)
from tokenshare.plugins.lean_proof.split_strategy import (
    LeanSplitHelperRequest,
    build_lean_split_plan,
    run_lean_split_helper,
)
from tokenshare.protocol_engine import ProtocolEngine
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger


LEAN_AI_BENCHMARK_REPORT_SCHEMA_VERSION = "phase8.lean_ai_benchmark_report.v1"
NOW = "2026-07-02T00:00:00Z"
AI_EXECUTOR_ID = "executor_lean_ai_api"
AI_EXECUTOR_VERSION = "0.1.0"

SUMMARY_COLUMNS = (
    "input_index",
    "case_id",
    "statement_source",
    "status",
    "split_supported",
    "child_count",
    "raw_output_count",
    "parsed_output_count",
    "parse_failure_count",
    "checker_accepted_count",
    "merge_success",
    "provider_attempt_count",
    "retry_count",
    "cost_estimate_total",
    "latency_ms_total",
    "models",
)


@dataclass(frozen=True)
class LeanAIBenchmarkCase:
    input_index: int
    case_id: str
    statement_source: str
    parameters_source: str
    expected_child_keys: tuple[str, ...]
    theorem_payload: LeanTheoremPayload

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": "phase8.lean_ai_benchmark_case.v1",
            "input_index": self.input_index,
            "case_id": self.case_id,
            "statement_source": self.statement_source,
            "parameters_source": self.parameters_source,
            "expected_child_keys": list(self.expected_child_keys),
            "theorem_payload": self.theorem_payload.to_dict(),
        }


class ScriptedLeanProofTransport:
    """Deterministic transport that still exercises the real AIAPIExecutor path."""

    def __init__(self, *, model: str = "tokenshare-scripted-lean-prover") -> None:
        self.model = model
        self.calls: list[JsonObject] = []

    def post_chat_completion(
        self,
        *,
        entry,
        api_key: str,
        body: JsonObject,
        timeout_seconds: int,
    ):
        self.calls.append(
            {
                "entry_id": entry.entry_id,
                "model": entry.model,
                "timeout_seconds": timeout_seconds,
                "api_key_seen": bool(api_key),
                "body": json.loads(json.dumps(body, sort_keys=True)),
            }
        )
        user_prompt = _user_prompt(body)
        theorem_payload_digest = _prompt_value(
            user_prompt,
            field_name="theorem_payload_digest",
            label="Theorem payload digest",
        )
        statement_source = _prompt_value(
            user_prompt,
            field_name="statement_source",
            label="Statement source",
        )
        proof_source = _scripted_proof_source(statement_source)
        content = json.dumps(
            {
                "schema_version": LEAN_PROOF_CANDIDATE_SCHEMA_VERSION,
                "proof_candidate_id": (
                    f"proof_candidate:scripted:{len(self.calls)}"
                ),
                "theorem_payload_digest": theorem_payload_digest,
                "proof_source": proof_source,
                "created_at": NOW,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return _ProviderResponse(
            status_code=200,
            body={
                "id": f"lean-ai-scripted-response-{len(self.calls)}",
                "model": self.model,
                "choices": [
                    {
                        "message": {"content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 40,
                    "completion_tokens": 20,
                    "total_tokens": 60,
                },
            },
        )


class _ProviderResponse:
    def __init__(self, *, status_code: int, body: JsonObject) -> None:
        self.status_code = status_code
        self.body = body
        self.text = json.dumps(body, ensure_ascii=False)


def generate_lean_ai_benchmark_cases(*, count: int = 50) -> tuple[LeanAIBenchmarkCase, ...]:
    """Return the 50-task benchmark matched to the current Lean split-helper slice."""

    all_specs = _conjunction_specs() + _iff_specs()
    if count < 1 or count > len(all_specs):
        raise ValueError("count must be between 1 and 50")
    return tuple(
        _case_from_spec(index=index, spec=spec)
        for index, spec in enumerate(all_specs[:count])
    )


def run_lean_ai_benchmark_suite(
    *,
    output_root: str | Path,
    count: int = 50,
    seed: int = 1,
    ai_api_config: AIAPIExecutorConfig | None = None,
    transport: Any | None = None,
    real_transport: bool = False,
) -> JsonObject:
    """Run Lean proof tasks with AI-generated child proof candidates."""

    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    cases = generate_lean_ai_benchmark_cases(count=count)
    os.environ.setdefault("TOKENSHARE_LEAN_AI_FAKE_KEY", "tokenshare-lean-ai-fake-key")
    config = ai_api_config or _default_ai_config()
    active_transport = transport or (UrlLibSiliconFlowTransport() if real_transport else ScriptedLeanProofTransport())
    environment_manifest = _default_environment_manifest()

    results = [
        _run_one_case(
            case=case,
            output_dir=root / "runs" / f"{case.input_index:03d}_{case.case_id}",
            seed=seed,
            config=config,
            transport=active_transport,
            environment_manifest=environment_manifest,
        )
        for case in cases
    ]

    catalog_path = root / "task_catalog.json"
    settings_path = root / "lean_ai_50_settings.json"
    results_path = root / "per_task_results.jsonl"
    summary_path = root / "per_task_summary.csv"
    report_path = root / "batch_report.json"
    catalog_path.write_text(
        json.dumps(
            {
                "schema_version": "phase8.lean_ai_benchmark_catalog.v1",
                "cases": [case.to_dict() for case in cases],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    _write_settings(
        path=settings_path,
        output_root=root,
        count=count,
        seed=seed,
        config=config,
        real_transport=real_transport,
        catalog_path=catalog_path,
        summary_path=summary_path,
        report_path=report_path,
    )
    _write_jsonl(results_path, results)
    _write_summary(summary_path, results)
    report = _batch_report(
        results=results,
        requested_count=count,
        seed=seed,
        real_transport=real_transport,
        settings_path=settings_path,
        catalog_path=catalog_path,
        results_path=results_path,
        summary_path=summary_path,
        report_path=report_path,
    )
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report


def _run_one_case(
    *,
    case: LeanAIBenchmarkCase,
    output_dir: Path,
    seed: int,
    config: AIAPIExecutorConfig,
    transport: Any,
    environment_manifest: LeanEnvironmentManifest,
) -> JsonObject:
    output_dir.mkdir(parents=True, exist_ok=True)
    store = ArtifactStore(output_dir)
    ledger = EventLedger(output_dir / "events" / "event_log.jsonl")
    engine = ProtocolEngine(
        event_ledger=ledger,
        protocol_config=ProtocolConfig.default(
            config_id=f"config_lean_ai_{case.input_index}",
            artifact_store_uri=f"file://{store.artifact_dir.as_posix()}",
            event_log_uri=f"file://{ledger.path.as_posix()}",
            metadata={"suite": "lean_ai_50"},
        ),
        artifact_store=store,
    )
    parent_payload_ref = store.save_json(
        case.theorem_payload.to_dict(),
        artifact_id=f"parent_theorem_payload_{case.input_index:03d}",
        artifact_type="LeanTheoremPayload",
        artifact_schema_id="lean_proof.theorem_payload",
        artifact_schema_version="v1",
        source={"kind": "lean_ai_benchmark", "case_id": case.case_id},
        metadata={"theorem_name": case.theorem_payload.theorem_name},
        created_at=NOW,
    )
    split_report = run_lean_split_helper(
        LeanSplitHelperRequest(
            request_id=f"lean_ai_split_request_{case.input_index:03d}",
            theorem_payload_ref=parent_payload_ref,
            environment_ref=build_lean_environment_ref(environment_manifest),
            timeout_seconds=int(case.theorem_payload.resource_limits["timeout_seconds"]),
            max_output_bytes=int(case.theorem_payload.resource_limits["max_output_bytes"]),
            created_at=NOW,
        ),
        artifact_store=store,
        environment_manifest=environment_manifest,
    )
    split_supported = (
        split_report.certificate is not None
        and split_report.certificate.split_kind != "unsupported"
    )
    child_results: list[JsonObject] = []
    child_proofs: list[LeanProofMergeInput] = []
    merge_result_ref = None
    root_checker_report_ref = None
    root_proof_artifact_ref = None
    merge_success = False
    merge_error = None

    if split_supported:
        split_plan = build_lean_split_plan(
            split_report=split_report,
            artifact_store=store,
            task_id=f"task_lean_ai_{case.input_index:03d}",
            parent_unit_id=f"unit_lean_ai_parent_{case.input_index:03d}",
            canonical_selection_id=f"canonical_lean_ai_parent_{case.input_index:03d}",
            canonical_output_bundle_digest=parent_payload_ref.content_hash,
            plugin_descriptor_digest=build_lean_proof_plugin_descriptor().descriptor_digest,
            expansion_scope_hash=canonical_json_digest(
                {
                    "case_id": case.case_id,
                    "parent_payload_digest": parent_payload_ref.content_hash,
                }
            ),
            expansion_decision_id=f"expansion_lean_ai_{case.input_index:03d}",
            created_at=NOW,
        )
        for child_key, child_payload_ref in split_plan.child_payload_refs_by_logical_key.items():
            child_result = _run_child_ai_proof(
                case=case,
                child_key=child_key,
                child_payload_ref=child_payload_ref,
                split_certificate=split_plan.certificate,
                store=store,
                ledger=ledger,
                engine=engine,
                seed=seed,
                config=config,
                transport=transport,
                environment_manifest=environment_manifest,
            )
            child_results.append(child_result["summary"])
            if child_result["proof_input"] is not None:
                child_proofs.append(child_result["proof_input"])
        if len(child_proofs) == len(split_plan.merge_plan.required_slots):
            try:
                merge_result = merge_lean_child_proofs(
                    merge_plan=split_plan.merge_plan,
                    split_certificate=split_plan.certificate,
                    parent_theorem_payload_ref=parent_payload_ref,
                    child_proofs=child_proofs,
                    artifact_store=store,
                    environment_manifest=environment_manifest,
                    merge_unit_id=f"unit_lean_ai_merge_{case.input_index:03d}",
                    request_id=f"lean_ai_merge_request_{case.input_index:03d}",
                    created_at=NOW,
                )
                merge_success = merge_result.accepted
                merge_result_ref = (
                    merge_result.merge_result_ref.to_dict()
                    if merge_result.merge_result_ref is not None
                    else None
                )
                root_checker_report_ref = (
                    merge_result.root_checker_report.report_ref.to_dict()
                    if merge_result.root_checker_report.report_ref is not None
                    else None
                )
                root_proof_artifact_ref = (
                    merge_result.root_proof_artifact_ref.to_dict()
                    if merge_result.root_proof_artifact_ref is not None
                    else None
                )
            except ValueError as exc:
                merge_error = str(exc)

    raw_output_count = sum(int(item["raw_output_ref"] is not None) for item in child_results)
    parsed_output_count = sum(int(item["parsed_output_ref"] is not None) for item in child_results)
    parse_failure_count = sum(int(item["parse_failure_ref"] is not None) for item in child_results)
    checker_accepted_count = sum(int(item["checker_accepted"]) for item in child_results)
    provider_attempt_count = sum(int(item["provider_attempt_count"]) for item in child_results)
    cost_estimate_total = sum(float(item["cost_estimate"] or 0.0) for item in child_results)
    latency_ms_total = sum(int(item["latency_ms_total"]) for item in child_results)
    child_count = len(child_results)
    status = (
        "passed"
        if split_supported
        and child_count > 0
        and parsed_output_count == child_count
        and checker_accepted_count == child_count
        and merge_success
        else "failed"
    )
    models = sorted(
        {
            str(model)
            for item in child_results
            for model in item.get("models", [])
            if model
        }
    )
    return {
        "schema_version": "phase8.lean_ai_benchmark_task_result.v1",
        "input_index": case.input_index,
        "case_id": case.case_id,
        "statement_source": case.statement_source,
        "status": status,
        "split_supported": split_supported,
        "split_status": split_report.status.value,
        "split_rule_id": split_report.certificate.rule_id if split_report.certificate else None,
        "child_count": child_count,
        "expected_child_keys": list(case.expected_child_keys),
        "child_results": child_results,
        "raw_output_count": raw_output_count,
        "parsed_output_count": parsed_output_count,
        "parse_failure_count": parse_failure_count,
        "checker_accepted_count": checker_accepted_count,
        "checker_success_rate": checker_accepted_count / child_count if child_count else 0.0,
        "parser_success_rate": parsed_output_count / child_count if child_count else 0.0,
        "merge_success": merge_success,
        "merge_error": merge_error,
        "merge_result_ref": merge_result_ref,
        "root_checker_report_ref": root_checker_report_ref,
        "root_proof_artifact_ref": root_proof_artifact_ref,
        "provider_attempt_count": provider_attempt_count,
        "retry_count": max(0, provider_attempt_count - child_count),
        "cost_estimate_total": cost_estimate_total,
        "latency_ms_total": latency_ms_total,
        "models": models,
        "event_log_path": ledger.path.as_posix(),
        "artifact_root_path": store.artifact_dir.as_posix(),
        "environment_digest": environment_manifest.environment_digest,
    }


def _run_child_ai_proof(
    *,
    case: LeanAIBenchmarkCase,
    child_key: str,
    child_payload_ref,
    split_certificate,
    store: ArtifactStore,
    ledger: EventLedger,
    engine: ProtocolEngine,
    seed: int,
    config: AIAPIExecutorConfig,
    transport: Any,
    environment_manifest: LeanEnvironmentManifest,
) -> dict[str, Any]:
    child_payload = LeanTheoremPayload.from_dict(
        json.loads(store.read_bytes(child_payload_ref).decode("utf-8"))
    )
    request = _build_ai_request(
        case=case,
        child_key=child_key,
        child_payload=child_payload,
        child_payload_ref=child_payload_ref,
        store=store,
        seed=seed,
    )
    request_flow = engine.record_execution_request(
        request=request,
        correlation_id=f"corr_lean_ai_request_{case.input_index}_{_safe_id(child_key)}",
    )
    executor = AIAPIExecutor(
        executor_id=AI_EXECUTOR_ID,
        executor_version=AI_EXECUTOR_VERSION,
        artifact_store=store,
        config=config,
        transport=transport,
        parser=lambda raw, *, raw_output_ref_summary, created_at: parse_lean_proof_candidate_ai_output(
            raw,
            theorem_payload=child_payload,
            raw_output_ref_summary=raw_output_ref_summary,
            created_at=created_at,
        ),
    )
    submission = executor.execute(
        request,
        submission_id=f"submission_lean_ai_{case.input_index:03d}_{_safe_id(child_key)}",
        submitted_at=NOW,
    )
    attempt, lease = _attempt_and_lease(request)
    engine.record_execution_submission(
        submission=submission,
        attempt=attempt,
        lease=lease,
        correlation_id=f"corr_lean_ai_submission_{case.input_index}_{_safe_id(child_key)}",
        causation_event_id=request_flow.event.event_id,
    )
    proof_candidate_ref = submission.candidate_output_refs.get(PROOF_CANDIDATE_OUTPUT_NAME)
    child_proof_result: LeanChildProofResult | None = None
    if proof_candidate_ref is not None:
        child_proof_result = check_lean_child_proof(
            child_logical_key=child_key,
            split_certificate=split_certificate,
            child_payload_ref=child_payload_ref,
            proof_candidate_ref=proof_candidate_ref,
            artifact_store=store,
            environment_manifest=environment_manifest,
            request_id=f"lean_ai_child_checker_{case.input_index:03d}_{_safe_id(child_key)}",
            created_at=NOW,
        )
    usage = submission.usage_summary or {}
    latency_ms_total = _submission_latency_ms(store, submission.provenance_ref)
    summary = {
        "child_logical_key": child_key,
        "statement_source": child_payload.statement_source,
        "raw_output_ref": submission.raw_output_ref.to_dict()
        if submission.raw_output_ref is not None
        else None,
        "parsed_output_ref": submission.parsed_output_ref.to_dict()
        if submission.parsed_output_ref is not None
        else None,
        "candidate_output_ref": proof_candidate_ref.to_dict()
        if proof_candidate_ref is not None
        else None,
        "parse_failure_ref": submission.parse_failure_ref.to_dict()
        if submission.parse_failure_ref is not None
        else None,
        "checker_accepted": (
            child_proof_result.accepted if child_proof_result is not None else False
        ),
        "checker_report_ref": (
            child_proof_result.checker_report.report_ref.to_dict()
            if child_proof_result is not None
            and child_proof_result.checker_report is not None
            and child_proof_result.checker_report.report_ref is not None
            else None
        ),
        "failure_kind": (
            child_proof_result.failure_kind if child_proof_result is not None else submission.result_kind
        ),
        "provider_attempt_count": int(usage.get("provider_attempt_count") or 0),
        "cost_estimate": usage.get("cost_estimate"),
        "latency_ms_total": latency_ms_total,
        "models": [usage["model"]] if isinstance(usage.get("model"), str) else [],
    }
    return {
        "summary": summary,
        "proof_input": (
            LeanProofMergeInput(
                slot_key=f"{child_key}:lean_proof_artifact",
                child_proof=child_proof_result,
            )
            if child_proof_result is not None and child_proof_result.merge_ready
            else None
        ),
    }


def _build_ai_request(
    *,
    case: LeanAIBenchmarkCase,
    child_key: str,
    child_payload: LeanTheoremPayload,
    child_payload_ref,
    store: ArtifactStore,
    seed: int,
) -> ExecutionRequest:
    safe_child = _safe_id(child_key)
    task_id = f"task_lean_ai_{case.input_index:03d}"
    unit_id = f"unit_lean_ai_{case.input_index:03d}_{safe_child}"
    request_id = f"request_lean_ai_{case.input_index:03d}_{safe_child}"
    prompt = build_lean_proof_candidate_prompt_package(
        request_id=request_id,
        task_id=task_id,
        unit_id=unit_id,
        theorem_payload=child_payload,
        created_at=NOW,
        seed=seed,
    )
    prompt_ref = store.save_json(
        prompt.to_dict(),
        artifact_id=f"prompt_{case.input_index:03d}_{safe_child}",
        artifact_type="PromptPackage",
        artifact_schema_id="phase3.prompt_package",
        artifact_schema_version="v1",
        source={"kind": "lean_ai_benchmark", "case_id": case.case_id},
        metadata={"child_logical_key": child_key},
        created_at=NOW,
    )
    descriptor = build_lean_proof_plugin_descriptor()
    return ExecutionRequest(
        request_id=request_id,
        task_id=task_id,
        unit_id=unit_id,
        attempt_id=f"attempt_lean_ai_{case.input_index:03d}_{safe_child}",
        lease_id=f"lease_lean_ai_{case.input_index:03d}_{safe_child}",
        fencing_token=f"fence_lean_ai_{case.input_index:03d}_{safe_child}",
        plugin={
            "plugin_id": PLUGIN_ID,
            "plugin_version": PLUGIN_VERSION,
            "plugin_descriptor_digest": descriptor.descriptor_digest,
            "ai_output_parser_policy_id": PROOF_CANDIDATE_PARSER_ID,
        },
        executor={
            "executor_id": AI_EXECUTOR_ID,
            "executor_version": AI_EXECUTOR_VERSION,
        },
        registry_snapshot_id="registry_snapshot_lean_ai_benchmark",
        allocation_decision={
            "decision_id": f"allocation_lean_ai_{case.input_index:03d}_{safe_child}",
            "selected_executor_id": AI_EXECUTOR_ID,
            "eligible_executor_ids": [AI_EXECUTOR_ID],
        },
        capability_snapshot={"executor": "ai_api", "provider_family": "siliconflow"},
        task_unit_snapshot=_task_unit(task_id=task_id, unit_id=unit_id).to_dict(),
        input_artifact_refs={"child_theorem_payload": child_payload_ref},
        output_contract=_proof_candidate_output_contract(),
        hard_requirements={"executor": "ai_api", "provider_family": "siliconflow"},
        soft_hints={"temperature": 0.0},
        environment_ref=_ai_environment_ref(seed=seed),
        execution_instruction_ref=None,
        prompt_package_ref=prompt_ref,
        limits={"timeout_seconds": 30, "max_tokens": 1024},
        created_at=NOW,
    )


def _proof_candidate_output_contract() -> OutputContract:
    return OutputContract(
        output_contract_id=PROOF_ARTIFACT_CONTRACT_ID,
        required_outputs=[PROOF_CANDIDATE_OUTPUT_NAME],
        output_schema_refs={
            PROOF_CANDIDATE_OUTPUT_NAME: schema_ref(LEAN_PROOF_CANDIDATE_SCHEMA_VERSION)
        },
        raw_output_policy={"allowed": True, "media_type": "application/json"},
        parsed_output_schema_ref=schema_ref(LEAN_PROOF_CANDIDATE_SCHEMA_VERSION),
    )


def _task_unit(*, task_id: str, unit_id: str) -> TaskUnit:
    return TaskUnit(
        unit_id=unit_id,
        task_id=task_id,
        parent_unit_id=None,
        depth=1,
        unit_type="lean_proof_subgoal",
        state=TaskState.PROCESSING,
        input_refs={},
        canonical_output_refs={},
        required_capabilities={"executor": "ai_api", "lean_proof": True},
        weight=1.0,
        budget_limit=None,
        deadline=None,
        plugin_payload={"validator_policy_id": CHECKER_VALIDATOR_POLICY_ID},
        metadata={"suite": "lean_ai_50"},
        created_at=NOW,
        updated_at=NOW,
    )


def _attempt_and_lease(request: ExecutionRequest) -> tuple[Attempt, Lease]:
    attempt = Attempt(
        attempt_id=request.attempt_id,
        task_id=request.task_id,
        unit_id=request.unit_id,
        lease_id=request.lease_id,
        client_id=AI_EXECUTOR_ID,
        state=AttemptState.RUNNING,
        attempt_kind="exclusive",
        created_at=NOW,
        started_at=NOW,
        finished_at=None,
        input_artifact_refs=request.input_artifact_refs,
        candidate_output_refs=None,
        metadata={"suite": "lean_ai_50"},
    )
    lease = Lease(
        lease_id=request.lease_id,
        task_id=request.task_id,
        unit_id=request.unit_id,
        client_id=AI_EXECUTOR_ID,
        attempt_id=request.attempt_id,
        state=LeaseState.ACTIVE,
        issued_at=NOW,
        expires_at="2026-07-02T00:30:00Z",
        fencing_token=request.fencing_token,
        last_heartbeat_at=None,
        heartbeat_count=0,
        lease_kind="exclusive",
        terminated_at=None,
        terminated_reason=None,
        metadata={"suite": "lean_ai_50"},
    )
    return attempt, lease


def _ai_environment_ref(*, seed: int) -> EnvironmentRef:
    return EnvironmentRef(
        environment_id="env_lean_ai_api",
        environment_digest="sha256:env_lean_ai_api",
        runtime="python",
        tool_versions={"ai_api_executor": AI_EXECUTOR_VERSION},
        resource_limits={"timeout_seconds": 30, "max_tokens": 1024},
        fixture_profile_digest="sha256:lean_ai_50",
        seed=seed,
        clock_policy="fixed",
        created_at=NOW,
    )


def _default_environment_manifest() -> LeanEnvironmentManifest:
    tools_root = Path.home() / "AppData" / "Local" / "TokenShare" / "LeanToolchain"
    elan_home = tools_root / "elan-home"
    return LeanEnvironmentManifest.from_project(
        project_root=default_lean_fixture_project_path(),
        lean_executable=elan_home / "bin" / "lean.exe",
        lake_executable=elan_home / "bin" / "lake.exe",
        lean_version=(
            "Lean (version 4.8.0, x86_64-w64-windows-gnu, "
            "commit df668f00e6c0, Release)"
        ),
        lake_version="Lake version 5.0.0-df668f0 (Lean version 4.8.0)",
        resource_limits={"timeout_seconds": 30, "max_output_bytes": 65536},
        created_at=NOW,
    )


def _default_ai_config() -> AIAPIExecutorConfig:
    return load_ai_api_config(
        {
            "schema_version": "phase7.ai_api_executor_config.v1",
            "executor_id": AI_EXECUTOR_ID,
            "provider_family": "siliconflow",
            "selection_policy": {
                "kind": "uniform_random_without_weights",
                "seed_source": "request_or_environment_seed",
            },
            "defaults": {
                "timeout_seconds": 30,
                "max_tokens": 1024,
                "temperature": 0.0,
                "top_p": 0.9,
                "stream": False,
                "max_provider_attempts": 1,
            },
            "entries": [
                {
                    "entry_id": "lean_ai_scripted",
                    "enabled": True,
                    "base_url": "https://api.siliconflow.cn/v1",
                    "api_key_env": "TOKENSHARE_LEAN_AI_FAKE_KEY",
                    "model": "tokenshare-scripted-lean-prover",
                    "endpoint": "/chat/completions",
                    "supports_json_mode": True,
                    "supports_streaming": False,
                    "request_overrides": {"temperature": 0.0},
                    "pricing": {
                        "currency": "CNY",
                        "input_per_million_tokens": 1.0,
                        "output_per_million_tokens": 2.0,
                        "observed_at": "2026-07-02",
                        "source_note": "lean ai benchmark scripted price",
                    },
                    "tags": ["lean_ai", "fixture"],
                }
            ],
            "local_concurrency": {"max_in_flight_global": 1},
            "metadata": {"purpose": "lean-ai-50-benchmark"},
        }
    )


def _case_from_spec(*, index: int, spec: JsonObject) -> LeanAIBenchmarkCase:
    case_id = str(spec["case_id"])
    statement_source = str(spec["statement_source"])
    parameters_source = str(spec["parameters_source"])
    expected_child_keys = tuple(str(item) for item in spec["expected_child_keys"])
    payload = LeanTheoremPayload(
        theorem_id=f"lean_theorem:{case_id}",
        theorem_name=case_id,
        imports=["Init"],
        namespace="TokenShareGenerated",
        open_namespaces=[],
        options={},
        parameters_source=parameters_source,
        statement_source=statement_source,
        theorem_source=None,
        proof_candidate_ref=None,
        library_context={
            "project": "tokenshare_lean",
            "module": "TokenShareGenerated.LeanAI50",
            "benchmark_case_id": case_id,
        },
        decomposition_policy={
            "policy_id": DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
            "allowed_rules": ["conjunction", "iff", "intro"],
            "max_depth": 4,
            "max_children": 8,
            "unsupported_policy": "return_unsupported",
        },
        resource_limits={"timeout_seconds": 30, "max_output_bytes": 65536},
    )
    return LeanAIBenchmarkCase(
        input_index=index,
        case_id=case_id,
        statement_source=statement_source,
        parameters_source=parameters_source,
        expected_child_keys=expected_child_keys,
        theorem_payload=payload,
    )


def _conjunction_specs() -> list[JsonObject]:
    base = "(P Q : Prop) (hP : P) (hQ : Q)"
    extras = [
        ("lean_ai_conj_plain", ""),
        ("lean_ai_conj_extra_prop", "(R : Prop) (hR : R)"),
        ("lean_ai_conj_chain_to_q", "(R : Prop) (hPR : P → R) (hRQ : R → Q)"),
        ("lean_ai_conj_pair_context", "(R S : Prop) (hRS : R ∧ S)"),
        ("lean_ai_conj_two_irrelevant_facts", "(R S : Prop) (hR : R) (hS : S)"),
        ("lean_ai_conj_type_predicate", "(A : Type) (x : A) (R : A → Prop) (hx : R x)"),
        ("lean_ai_conj_nat_eq", "(n m : Nat) (hEq : n = m)"),
        ("lean_ai_conj_nat_reflexive_noise", "(n : Nat) (hNat : n = n)"),
        ("lean_ai_conj_bool_noise", "(b : Bool) (hBool : b = b)"),
        ("lean_ai_conj_list_noise", "(xs : List Nat) (hList : xs = xs)"),
        ("lean_ai_conj_option_noise", "(o : Option Nat) (hOpt : o = o)"),
        ("lean_ai_conj_not_context", "(R : Prop) (hNot : ¬ R → P)"),
        ("lean_ai_conj_false_context", "(R : Prop) (hContra : R → False)"),
        ("lean_ai_conj_iff_context", "(R : Prop) (hPR : P ↔ R)"),
        ("lean_ai_conj_three_prop_chain", "(R S T : Prop) (hRST : R → S → T)"),
        ("lean_ai_conj_nat_prop_mix", "(n : Nat) (R : Prop) (hMix : R → n = n)"),
        ("lean_ai_conj_type_equality", "(A : Type) (a b : A) (hSame : a = b)"),
        ("lean_ai_conj_function_noise", "(f : Nat → Nat) (n : Nat) (hFun : f n = f n)"),
        ("lean_ai_conj_predicate_noise", "(R : Nat → Prop) (n : Nat) (hRn : R n)"),
        ("lean_ai_conj_nested_and_noise", "(R : Prop) (hAnd : P ∧ R)"),
        ("lean_ai_conj_or_noise", "(R : Prop) (hOr : P ∨ R)"),
        ("lean_ai_conj_q_equiv_noise", "(R : Prop) (hQR : Q → R) (hRQ : R → Q)"),
        (
            "lean_ai_conj_two_predicates",
            "(A : Type) (p q : A → Prop) (x : A) (hp : p x) (hq : q x)",
        ),
        ("lean_ai_conj_three_nat_eqs", "(n m k : Nat) (h1 : n = m) (h2 : m = k)"),
        (
            "lean_ai_conj_dense_context",
            "(R S : Prop) (h1 : P → R) (h2 : Q → S) (h3 : R → S → P)",
        ),
    ]
    return [
        {
            "case_id": case_id,
            "parameters_source": _join_params(base, extra),
            "statement_source": "P ∧ Q",
            "expected_child_keys": ("child:left", "child:right"),
        }
        for case_id, extra in extras
    ]


def _iff_specs() -> list[JsonObject]:
    base = "(P Q : Prop) (hpq : P → Q) (hqp : Q → P)"
    extras = [
        ("lean_ai_iff_plain", ""),
        ("lean_ai_iff_extra_prop", "(R : Prop) (hR : R)"),
        ("lean_ai_iff_forward_chain_noise", "(R : Prop) (hPR : P → R) (hRQ : R → Q)"),
        ("lean_ai_iff_reverse_chain_noise", "(R : Prop) (hRP : R → P) (hQR : Q → R)"),
        ("lean_ai_iff_pair_context", "(R S : Prop) (hRS : R ∧ S)"),
        ("lean_ai_iff_or_context", "(R S : Prop) (hEither : R ∨ S)"),
        ("lean_ai_iff_nat_noise", "(n : Nat) (hn : n = n)"),
        ("lean_ai_iff_nat_eq_noise", "(n m : Nat) (hEq : n = m)"),
        ("lean_ai_iff_type_eq_noise", "(A : Type) (x : A) (hX : x = x)"),
        ("lean_ai_iff_function_noise", "(f : Nat → Nat) (n : Nat) (hf : f n = f n)"),
        ("lean_ai_iff_predicate_noise", "(R : Nat → Prop) (n : Nat) (hRn : R n)"),
        ("lean_ai_iff_not_context", "(R : Prop) (hNot : ¬ R → P)"),
        ("lean_ai_iff_false_context", "(R : Prop) (hImpossible : R → False)"),
        ("lean_ai_iff_p_bridge_noise", "(R : Prop) (hPIffR : P ↔ R)"),
        ("lean_ai_iff_q_bridge_noise", "(R : Prop) (hQIffR : Q ↔ R)"),
        ("lean_ai_iff_type_predicate", "(A : Type) (r : A → Prop) (x : A) (hr : r x)"),
        ("lean_ai_iff_list_eq_noise", "(xs ys : List Nat) (hxs : xs = ys)"),
        ("lean_ai_iff_option_noise", "(o : Option Nat) (ho : o = o)"),
        ("lean_ai_iff_bool_noise", "(b : Bool) (hb : b = b)"),
        ("lean_ai_iff_three_prop_chain", "(R S T : Prop) (hRST : R → S → T)"),
        ("lean_ai_iff_p_to_q_chain", "(R : Prop) (hChain1 : P → R) (hChain2 : R → Q)"),
        ("lean_ai_iff_q_to_p_chain", "(R : Prop) (hChain1 : Q → R) (hChain2 : R → P)"),
        (
            "lean_ai_iff_two_predicates",
            "(A : Type) (p q : A → Prop) (x : A) (hp : p x) (hq : q x)",
        ),
        ("lean_ai_iff_three_nat_eqs", "(n m k : Nat) (h1 : n = m) (h2 : m = k)"),
        (
            "lean_ai_iff_dense_context",
            "(R S : Prop) (h1 : R → P) (h2 : S → Q) (h3 : P → S)",
        ),
    ]
    return [
        {
            "case_id": case_id,
            "parameters_source": _join_params(base, extra),
            "statement_source": "P ↔ Q",
            "expected_child_keys": ("child:forward", "child:backward"),
        }
        for case_id, extra in extras
    ]


def _join_params(base: str, extra: str) -> str:
    return base if not extra else f"{base} {extra}"


def _write_settings(
    *,
    path: Path,
    output_root: Path,
    count: int,
    seed: int,
    config: AIAPIExecutorConfig,
    real_transport: bool,
    catalog_path: Path,
    summary_path: Path,
    report_path: Path,
) -> None:
    body = {
        "schema_version": "phase8.lean_ai_benchmark_settings.v1",
        "requested_count": count,
        "seed": seed,
        "real_transport": real_transport,
        "output_root": output_root.as_posix(),
        "catalog_path": catalog_path.as_posix(),
        "summary_csv_path": summary_path.as_posix(),
        "suite_report_path": report_path.as_posix(),
        "request_limits": {"timeout_seconds": 30, "max_tokens": 1024},
        "prompt_constraints": {"requires_json_mode": True, "strict_json_only": True},
        "parser_policy": {
            "parser_id": PROOF_CANDIDATE_PARSER_ID,
            "parse_required": True,
            "raw_only_allowed": False,
        },
        "lean_split_helper_slice": {
            "supported_statement_sources": ["P ∧ Q", "P ↔ Q"],
            "supported_merge_rules": [
                "lean_merge.conjunction_intro.v1",
                "lean_merge.iff_intro.v1",
            ],
        },
        "ai_api_config": {
            "config_digest": config.config_digest,
            "provider_family": config.provider_family,
            "executor_id": config.executor_id,
            "selection_policy": dict(config.selection_policy),
            "defaults": dict(config.defaults),
            "entry_count": len(config.entries),
            "enabled_entry_ids": [entry.entry_id for entry in config.entries if entry.enabled],
            "entries": [entry.to_safe_dict() for entry in config.entries],
            "metadata": dict(config.metadata),
        },
    }
    path.write_text(
        json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _batch_report(
    *,
    results: list[JsonObject],
    requested_count: int,
    seed: int,
    real_transport: bool,
    settings_path: Path,
    catalog_path: Path,
    results_path: Path,
    summary_path: Path,
    report_path: Path,
) -> JsonObject:
    attempted_count = len(results)
    passed_count = sum(1 for item in results if item["status"] == "passed")
    child_count = sum(int(item["child_count"]) for item in results)
    parsed_output_count = sum(int(item["parsed_output_count"]) for item in results)
    checker_accepted_count = sum(int(item["checker_accepted_count"]) for item in results)
    return {
        "schema_version": LEAN_AI_BENCHMARK_REPORT_SCHEMA_VERSION,
        "seed": seed,
        "real_transport": real_transport,
        "requested_count": requested_count,
        "attempted_count": attempted_count,
        "passed_count": passed_count,
        "failed_count": attempted_count - passed_count,
        "merge_success_count": sum(1 for item in results if item["merge_success"]),
        "split_supported_count": sum(1 for item in results if item["split_supported"]),
        "raw_output_count": sum(int(item["raw_output_count"]) for item in results),
        "parsed_output_count": parsed_output_count,
        "parse_failure_count": sum(int(item["parse_failure_count"]) for item in results),
        "checker_accepted_count": checker_accepted_count,
        "parser_success_rate": parsed_output_count / child_count if child_count else 0.0,
        "checker_success_rate": checker_accepted_count / child_count if child_count else 0.0,
        "provider_attempt_count": sum(int(item["provider_attempt_count"]) for item in results),
        "retry_count": sum(int(item["retry_count"]) for item in results),
        "cost_estimate_total": sum(float(item["cost_estimate_total"]) for item in results),
        "latency_ms_total": sum(int(item["latency_ms_total"]) for item in results),
        "settings_path": settings_path.as_posix(),
        "catalog_path": catalog_path.as_posix(),
        "per_task_results_path": results_path.as_posix(),
        "summary_csv_path": summary_path.as_posix(),
        "suite_report_path": report_path.as_posix(),
    }


def _write_jsonl(path: Path, rows: list[JsonObject]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_summary(path: Path, rows: list[JsonObject]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(SUMMARY_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow({column: _csv_value(row.get(column)) for column in SUMMARY_COLUMNS})


def _submission_latency_ms(store: ArtifactStore, provenance_ref) -> int:
    if provenance_ref is None:
        return 0
    try:
        body = json.loads(store.read_bytes(provenance_ref).decode("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return 0
    attempts = body.get("attempts", [])
    if not isinstance(attempts, list):
        return 0
    total = 0
    for attempt in attempts:
        if isinstance(attempt, dict):
            total += int(attempt.get("latency_ms") or 0)
    return total


def _user_prompt(body: JsonObject) -> str:
    messages = body.get("messages", [])
    if not isinstance(messages, list):
        return ""
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "user":
            return str(message.get("content", ""))
    return ""


def _prompt_value(prompt: str, *, field_name: str, label: str) -> str:
    line_match = re.search(rf"^{re.escape(label)}:\s*(.+)$", prompt, flags=re.MULTILINE)
    if line_match:
        return line_match.group(1).strip()
    json_match = re.search(rf'"{re.escape(field_name)}"\s*:\s*"([^"]+)"', prompt)
    if json_match:
        return json_match.group(1)
    raise ValueError(f"scripted Lean proof transport could not find {field_name}")


def _scripted_proof_source(statement_source: str) -> str:
    if statement_source == "P":
        return "by\n  exact hP"
    if statement_source == "Q":
        return "by\n  exact hQ"
    if statement_source == "P → Q":
        return "by\n  exact hpq"
    if statement_source == "Q → P":
        return "by\n  exact hqp"
    raise ValueError(f"unsupported scripted Lean proof statement: {statement_source}")


def _csv_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return "|".join(str(item) for item in value)
    if value is None:
        return ""
    return str(value)


def _safe_id(value: str) -> str:
    return "".join(character if character.isalnum() or character == "_" else "_" for character in value)
