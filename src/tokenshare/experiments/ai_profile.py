"""AI executor profile suite for factorization experiment outputs."""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any

from tokenshare.core.models import Attempt, AttemptState, Lease, LeaseState, TaskState, TaskUnit
from tokenshare.executors.ai_api import AIAPIExecutor
from tokenshare.executors.ai_api_config import AIAPIExecutorConfig, load_ai_api_config
from tokenshare.executors.ai_api_transport import UrlLibSiliconFlowTransport
from tokenshare.executors.contracts import EnvironmentRef, ExecutionRequest
from tokenshare.experiments.metrics import copy_event_log, final_correctness_for_factorization
from tokenshare.experiments.models import digest_json
from tokenshare.plugins.contracts import OutputContract
from tokenshare.plugins.factorization.descriptor import build_factorization_plugin_descriptor
from tokenshare.plugins.factorization.fixtures import run_factorization_fixture_flow
from tokenshare.plugins.factorization.models import FactorSearchRangeInput, RangeResult
from tokenshare.plugins.factorization.prompt_builder import build_factor_search_prompt_package
from tokenshare.plugins.factorization.schemas import (
    PLUGIN_ID,
    PLUGIN_VERSION,
    RANGE_RESULT_CONTRACT_ID,
    RANGE_RESULT_FOUND_FACTOR,
    RANGE_RESULT_NO_FACTOR,
    RANGE_RESULT_SCHEMA_VERSION,
    REQUESTED_OUTPUT_PRIME_FACTORIZATION,
    schema_ref,
)
from tokenshare.plugins.factorization.validator import (
    build_factor_search_instruction,
    parse_factorization_ai_output,
)
from tokenshare.protocol_engine import ProtocolEngine
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger


NOW = "2026-06-30T00:00:00Z"
AI_EXECUTOR_ID = "executor_ai_api"
AI_EXECUTOR_VERSION = "0.1.0"

AI_PROFILE_SUMMARY_COLUMNS = (
    "experiment_id",
    "case_id",
    "executor_profile",
    "status",
    "final_correctness",
    "parser_success_rate",
    "raw_output_count",
    "parsed_output_count",
    "parse_failure_count",
    "provider_attempt_count",
    "retry_count",
    "cost_estimate_total",
    "latency_ms_total",
    "providers",
    "models",
)


class ScriptedSiliconFlowTransport:
    """Deterministic transport that still exercises the real AIAPIExecutor path."""

    def __init__(self, contents: list[str], *, model: str = "Qwen/Qwen2.5-7B-Instruct") -> None:
        self._contents = list(contents)
        self._model = model
        self.calls: list[dict[str, Any]] = []

    def post_chat_completion(
        self,
        *,
        entry,
        api_key: str,
        body: dict[str, Any],
        timeout_seconds: int,
    ):
        self.calls.append(
            {
                "entry_id": entry.entry_id,
                "model": entry.model,
                "body": json.loads(json.dumps(body, sort_keys=True)),
                "timeout_seconds": timeout_seconds,
                "api_key_seen": bool(api_key),
            }
        )
        if not self._contents:
            raise AssertionError("scripted transport has no remaining response")
        index = len(self.calls)
        return _ProviderResponse(
            status_code=200,
            body={
                "id": f"ai-profile-response-{index}",
                "model": self._model,
                "choices": [
                    {
                        "message": {"content": self._contents.pop(0)},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 10,
                    "total_tokens": 30,
                },
            },
        )


class _ProviderResponse:
    def __init__(self, *, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self.body = body
        self.text = json.dumps(body, ensure_ascii=False)


def run_ai_profile_suite(
    *,
    output_root: str | Path,
    seed: int = 1,
    ai_api_config: AIAPIExecutorConfig | None = None,
    transport: Any | None = None,
    real_transport: bool = False,
) -> dict[str, Any]:
    """Run deterministic and AI API profile cases and write comparison reports."""

    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("TOKENSHARE_AI_PROFILE_FAKE_KEY", "tokenshare-ai-profile-fake-key")
    deterministic = _run_deterministic_semiprime(root / "deterministic_semiprime_range_flow")
    config = ai_api_config or _default_ai_profile_config()
    ai_success = _run_ai_semiprime_profile(
        output_dir=root / "ai_api_semiprime_range_flow",
        config=config,
        seed=seed,
        transport=transport,
        real_transport=real_transport,
    )
    ai_parse_failure = _run_ai_parse_failure_profile(
        output_dir=root / "ai_api_parse_failure_raw_only",
        config=config,
        seed=seed,
        transport=transport,
        real_transport=real_transport,
    )
    profiles = [deterministic, ai_success, ai_parse_failure]
    summary_path = root / "ai_profile_summary.csv"
    report_path = root / "ai_profile_suite_report.json"
    settings_path = root / "ai_profile_settings.json"
    _write_summary_csv(summary_path, profiles)
    _write_ai_profile_settings(
        settings_path=settings_path,
        output_root=root,
        seed=seed,
        ai_api_config=config,
        real_transport=real_transport,
        summary_csv_path=summary_path,
        suite_report_path=report_path,
    )
    report = {
        "schema_version": "phase8.ai_profile_suite_report.v1",
        "seed": seed,
        "total_profiles": len(profiles),
        "summary_csv_path": summary_path.as_posix(),
        "suite_report_path": report_path.as_posix(),
        "settings_path": settings_path.as_posix(),
        "profiles": profiles,
        "deterministic_vs_ai_api": {
            "semiprime_range_flow": _comparison(
                deterministic=deterministic,
                ai_profile=ai_success,
            )
        },
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report


def _run_deterministic_semiprime(output_dir: Path) -> dict[str, Any]:
    fixture_dir = output_dir / "fixture_flow"
    result = run_factorization_fixture_flow(
        fixture_dir,
        target_n=91,
        requested_child_count=4,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    copied_log = copy_event_log(result.ledger.path, output_dir / "events" / "event_log.jsonl")
    factors = (
        [
            item["prime"]
            for item in result.prime_factorization_result.to_dict()["prime_factors"]
            for _ in range(int(item["exponent"]))
        ]
        if result.prime_factorization_result is not None
        else []
    )
    return {
        "schema_version": "phase8.ai_profile_case_report.v1",
        "experiment_id": "exp1_factorization_e2e",
        "case_id": "deterministic_semiprime_range_flow",
        "fixture_name": "semiprime_range_flow",
        "executor_profile": "deterministic_local",
        "status": "passed",
        "target_n": "91",
        "prime_factors": factors,
        "final_correctness": final_correctness_for_factorization("91", factors),
        "parser_success_rate": 1.0,
        "raw_output_count": 0,
        "parsed_output_count": len(result.range_executions),
        "parse_failure_count": 0,
        "provider_attempt_count": 0,
        "retry_count": 0,
        "cost_estimate_total": 0,
        "latency_ms_total": 0,
        "providers": [],
        "models": [],
        "raw_output_refs": [],
        "parsed_output_refs": [
            item.range_output_ref.to_dict() for item in result.range_executions
        ],
        "parse_failure_refs": [],
        "event_log_ref": copied_log,
        "artifact_root_path": result.store.artifact_dir.as_posix(),
    }


def _run_ai_semiprime_profile(
    *,
    output_dir: Path,
    config: AIAPIExecutorConfig,
    seed: int,
    transport: Any | None,
    real_transport: bool,
) -> dict[str, Any]:
    config = _strict_arithmetic_config(config)
    range_inputs = _semiprime_range_inputs()
    scripted_outputs = [
        json.dumps(_range_result_body(range_input), ensure_ascii=False, sort_keys=True)
        for range_input in range_inputs
    ]
    active_transport = _transport(
        transport=transport,
        scripted_outputs=scripted_outputs,
        real_transport=real_transport,
    )
    execution = _execute_ai_range_profiles(
        output_dir=output_dir,
        config=config,
        transport=active_transport,
        range_inputs=range_inputs,
        seed=seed,
        case_id="ai_api_semiprime_range_flow",
    )
    factors = _factors_from_successful_outputs(execution["range_result_bodies"])
    execution.update(
        {
            "experiment_id": "exp1_factorization_e2e",
            "case_id": "ai_api_semiprime_range_flow",
            "fixture_name": "semiprime_range_flow",
            "status": "passed" if final_correctness_for_factorization("91", factors) else "failed",
            "target_n": "91",
            "prime_factors": factors,
            "final_correctness": final_correctness_for_factorization("91", factors),
        }
    )
    return _case_report_from_execution(execution)


def _run_ai_parse_failure_profile(
    *,
    output_dir: Path,
    config: AIAPIExecutorConfig,
    seed: int,
    transport: Any | None,
    real_transport: bool,
) -> dict[str, Any]:
    # raw-only is a controlled failure injection profile. Even during a real-transport
    # suite, this case must preserve the invalid raw text stimulus instead of asking
    # a real model to satisfy the normal strict JSON prompt.
    active_transport = transport or ScriptedSiliconFlowTransport(["I found factor 7."])
    execution = _execute_ai_range_profiles(
        output_dir=output_dir,
        config=config,
        transport=active_transport,
        range_inputs=(_semiprime_range_inputs()[1],),
        seed=seed,
        case_id="ai_api_parse_failure_raw_only",
    )
    execution.update(
        {
            "experiment_id": "exp2_failure_recovery",
            "case_id": "ai_api_parse_failure_raw_only",
            "fixture_name": "parse_failure_raw_only_forbidden",
            "status": "passed" if execution["parse_failure_count"] == 1 else "failed",
            "target_n": "91",
            "prime_factors": [],
            "final_correctness": False,
        }
    )
    return _case_report_from_execution(execution)


def _execute_ai_range_profiles(
    *,
    output_dir: Path,
    config: AIAPIExecutorConfig,
    transport: Any,
    range_inputs: tuple[FactorSearchRangeInput, ...],
    seed: int,
    case_id: str,
) -> dict[str, Any]:
    store = ArtifactStore(output_dir)
    ledger = EventLedger(output_dir / "events" / "event_log.jsonl")
    engine = ProtocolEngine(
        event_ledger=ledger,
        protocol_config=_protocol_config(output_dir),
        artifact_store=store,
    )
    submissions = []
    range_result_bodies: list[dict[str, Any]] = []
    for index, range_input in enumerate(range_inputs, start=1):
        request = _build_ai_execution_request(
            store=store,
            range_input=range_input,
            seed=seed,
            case_id=case_id,
            index=index,
        )
        request_flow = engine.record_execution_request(
            request=request,
            correlation_id=f"corr_ai_profile_request_{case_id}_{index}",
        )
        executor = AIAPIExecutor(
            executor_id=AI_EXECUTOR_ID,
            executor_version=AI_EXECUTOR_VERSION,
            artifact_store=store,
            config=config,
            transport=transport,
            parser=lambda raw, *, raw_output_ref_summary, created_at: parse_factorization_ai_output(
                raw,
                raw_output_ref_summary=raw_output_ref_summary,
                created_at=created_at,
            ),
        )
        submission = executor.execute(
            request,
            submission_id=f"submission_{case_id}_{index}",
            submitted_at=NOW,
        )
        attempt, lease = _attempt_and_lease(request)
        engine.record_execution_submission(
            submission=submission,
            attempt=attempt,
            lease=lease,
            correlation_id=f"corr_ai_profile_submission_{case_id}_{index}",
            causation_event_id=request_flow.event.event_id,
        )
        submissions.append(submission)
        if submission.candidate_output_refs.get("range_result") is not None:
            body = json.loads(
                store.read_bytes(submission.candidate_output_refs["range_result"]).decode("utf-8")
            )
            range_result_bodies.append(body)

    copied_log = copy_event_log(ledger.path, output_dir / "copied_events" / "event_log.jsonl")
    usage = _usage_from_submissions(store, submissions)
    raw_refs = [item.raw_output_ref.to_dict() for item in submissions if item.raw_output_ref is not None]
    parsed_refs = [
        item.parsed_output_ref.to_dict()
        for item in submissions
        if item.parsed_output_ref is not None
    ]
    parse_failure_refs = [
        item.parse_failure_ref.to_dict()
        for item in submissions
        if item.parse_failure_ref is not None
    ]
    provider_attempt_count = usage["provider_attempt_count"]
    return {
        "schema_version": "phase8.ai_profile_case_report.v1",
        "executor_profile": "ai_api",
        "raw_output_count": len(raw_refs),
        "parsed_output_count": len(parsed_refs),
        "parse_failure_count": len(parse_failure_refs),
        "parser_success_rate": (
            len(parsed_refs) / len(submissions) if submissions else 0.0
        ),
        "provider_attempt_count": provider_attempt_count,
        "retry_count": max(0, provider_attempt_count - len(submissions)),
        "cost_estimate_total": usage["cost_estimate_total"],
        "latency_ms_total": usage["latency_ms_total"],
        "usage": {
            "prompt_tokens": usage["prompt_tokens"],
            "completion_tokens": usage["completion_tokens"],
            "total_tokens": usage["total_tokens"],
        },
        "providers": sorted(usage["providers"]),
        "models": sorted(usage["models"]),
        "raw_output_refs": raw_refs,
        "parsed_output_refs": parsed_refs,
        "parse_failure_refs": parse_failure_refs,
        "event_log_ref": copied_log,
        "artifact_root_path": store.artifact_dir.as_posix(),
        "range_result_bodies": range_result_bodies,
    }


def _case_report_from_execution(execution: dict[str, Any]) -> dict[str, Any]:
    body = dict(execution)
    body.pop("range_result_bodies", None)
    return body


def _build_ai_execution_request(
    *,
    store: ArtifactStore,
    range_input: FactorSearchRangeInput,
    seed: int,
    case_id: str,
    index: int,
) -> ExecutionRequest:
    task_id = f"task_ai_profile_{case_id}"
    unit_id = f"unit_ai_profile_{case_id}_{index}"
    request_id = f"request_{case_id}_{index}"
    instruction = build_factor_search_instruction(
        request_id=request_id,
        unit_id=unit_id,
        range_input=range_input,
    )
    instruction_ref = store.save_json(
        instruction.to_dict(),
        artifact_id=f"instruction_{case_id}_{index}",
        artifact_type="ExecutionInstruction",
        artifact_schema_id="factorization.factor_search_instruction",
        artifact_schema_version="v1",
        source={"kind": "ai_profile_suite"},
        metadata={"case_id": case_id},
        created_at=NOW,
    )
    prompt = build_factor_search_prompt_package(
        request_id=request_id,
        task_id=task_id,
        unit_id=unit_id,
        range_input=range_input,
        instruction=instruction,
        created_at=NOW,
        seed=seed,
    )
    prompt_body = prompt.to_dict()
    prompt_body["constraints"] = {
        **prompt_body["constraints"],
        "requires_json_mode": True,
        "format": "json",
    }
    prompt_ref = store.save_json(
        prompt_body,
        artifact_id=f"prompt_{case_id}_{index}",
        artifact_type="PromptPackage",
        artifact_schema_id="phase3.prompt_package",
        artifact_schema_version="v1",
        source={"kind": "ai_profile_suite"},
        metadata={"case_id": case_id},
        created_at=NOW,
    )
    descriptor = build_factorization_plugin_descriptor()
    return ExecutionRequest(
        request_id=request_id,
        task_id=task_id,
        unit_id=unit_id,
        attempt_id=f"attempt_{case_id}_{index}",
        lease_id=f"lease_{case_id}_{index}",
        fencing_token=f"fence_{case_id}_{index}",
        plugin={
            "plugin_id": PLUGIN_ID,
            "plugin_version": PLUGIN_VERSION,
            "plugin_descriptor_digest": descriptor.descriptor_digest,
            "ai_output_parser_policy_id": "factorization.range_result.parser.v1",
        },
        executor={
            "executor_id": AI_EXECUTOR_ID,
            "executor_version": AI_EXECUTOR_VERSION,
        },
        registry_snapshot_id="registry_snapshot_ai_profile",
        allocation_decision={
            "decision_id": f"allocation_{case_id}_{index}",
            "selected_executor_id": AI_EXECUTOR_ID,
            "eligible_executor_ids": [AI_EXECUTOR_ID],
        },
        capability_snapshot={"executor": "ai_api", "provider_family": "siliconflow"},
        task_unit_snapshot=_task_unit(task_id=task_id, unit_id=unit_id).to_dict(),
        input_artifact_refs={},
        output_contract=_range_output_contract(),
        hard_requirements={"executor": "ai_api", "provider_family": "siliconflow"},
        soft_hints={"temperature": 0.0},
        environment_ref=_environment_ref(seed=seed),
        execution_instruction_ref=instruction_ref,
        prompt_package_ref=prompt_ref,
        limits={"timeout_seconds": 30, "max_tokens": 1024},
        created_at=NOW,
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
        metadata={"ai_profile": True},
    )
    lease = Lease(
        lease_id=request.lease_id,
        task_id=request.task_id,
        unit_id=request.unit_id,
        client_id=AI_EXECUTOR_ID,
        attempt_id=request.attempt_id,
        state=LeaseState.ACTIVE,
        issued_at=NOW,
        expires_at="2026-06-30T00:30:00Z",
        fencing_token=request.fencing_token,
        last_heartbeat_at=None,
        heartbeat_count=0,
        lease_kind="exclusive",
        terminated_at=None,
        terminated_reason=None,
        metadata={"ai_profile": True},
    )
    return attempt, lease


def _usage_from_submissions(store: ArtifactStore, submissions: list[Any]) -> dict[str, Any]:
    provider_attempt_count = 0
    cost_estimate_total = 0.0
    latency_ms_total = 0
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    providers: set[str] = set()
    models: set[str] = set()
    for submission in submissions:
        usage = submission.usage_summary or {}
        provider_attempt_count += _int_metric(usage.get("provider_attempt_count"))
        cost_estimate_total += _float_metric(usage.get("cost_estimate"))
        latency_ms_total += _latency_from_provenance(store, submission)
        prompt_tokens += _int_metric(usage.get("prompt_tokens"))
        completion_tokens += _int_metric(usage.get("completion_tokens"))
        total_tokens += _int_metric(usage.get("total_tokens"))
        provider = usage.get("provider_family")
        model = usage.get("model")
        if isinstance(provider, str) and provider:
            providers.add(provider)
        if isinstance(model, str) and model:
            models.add(model)
    return {
        "provider_attempt_count": provider_attempt_count,
        "cost_estimate_total": cost_estimate_total,
        "latency_ms_total": latency_ms_total,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "providers": providers,
        "models": models,
    }


def _latency_from_provenance(store: ArtifactStore, submission: Any) -> int:
    if submission.provenance_ref is None:
        return 0
    try:
        body = json.loads(store.read_bytes(submission.provenance_ref).decode("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return 0
    attempts = body.get("attempts", [])
    if not isinstance(attempts, list):
        return 0
    return sum(
        _int_metric(attempt.get("latency_ms"))
        for attempt in attempts
        if isinstance(attempt, dict)
    )


def _factors_from_successful_outputs(range_results: list[dict[str, Any]]) -> list[str]:
    for body in range_results:
        if body.get("result_kind") == RANGE_RESULT_FOUND_FACTOR:
            factor = body.get("found_factor")
            cofactor = body.get("cofactor")
            if isinstance(factor, str) and isinstance(cofactor, str):
                return sorted([factor, cofactor], key=int)
    return []


def _comparison(
    *,
    deterministic: dict[str, Any],
    ai_profile: dict[str, Any],
) -> dict[str, Any]:
    det_correct = bool(deterministic["final_correctness"])
    ai_correct = bool(ai_profile["final_correctness"])
    return {
        "deterministic_case_id": deterministic["case_id"],
        "ai_api_case_id": ai_profile["case_id"],
        "deterministic_correctness": det_correct,
        "ai_api_correctness": ai_correct,
        "correctness_delta": int(ai_correct) - int(det_correct),
        "deterministic_parser_success_rate": deterministic["parser_success_rate"],
        "ai_api_parser_success_rate": ai_profile["parser_success_rate"],
        "parser_success_rate_delta": (
            ai_profile["parser_success_rate"] - deterministic["parser_success_rate"]
        ),
        "deterministic_retry_count": deterministic["retry_count"],
        "ai_api_retry_count": ai_profile["retry_count"],
        "retry_delta": ai_profile["retry_count"] - deterministic["retry_count"],
        "deterministic_cost": deterministic["cost_estimate_total"],
        "ai_api_cost": ai_profile["cost_estimate_total"],
        "cost_delta": ai_profile["cost_estimate_total"] - deterministic["cost_estimate_total"],
        "deterministic_latency_ms_total": deterministic["latency_ms_total"],
        "ai_api_latency_ms_total": ai_profile["latency_ms_total"],
        "latency_delta_ms": (
            ai_profile["latency_ms_total"] - deterministic["latency_ms_total"]
        ),
    }


def _semiprime_range_inputs() -> tuple[FactorSearchRangeInput, ...]:
    params_digest = digest_json(
        {
            "strategy_id": "factorization.candidate_range_partition.v1",
            "target_n": "91",
            "requested_child_count": 4,
        }
    )
    coverage_id = "coverage_ai_profile_91"
    ranges = [("2", "3"), ("4", "5"), ("6", "7"), ("8", "9")]
    return tuple(
        FactorSearchRangeInput(
            target_n="91",
            range_start=start,
            range_end=end,
            coverage_id=coverage_id,
            child_index=index,
            child_count=len(ranges),
            partition_params_digest=params_digest,
        )
        for index, (start, end) in enumerate(ranges)
    )


def _range_result_body(range_input: FactorSearchRangeInput) -> dict[str, Any]:
    target = int(range_input.target_n)
    start = int(range_input.range_start)
    end = int(range_input.range_end)
    found = next((value for value in range(start, end + 1) if target % value == 0), None)
    if found is None:
        return RangeResult(
            range_result_id=f"range_result:{range_input.coverage_id}:{range_input.child_index}",
            result_kind=RANGE_RESULT_NO_FACTOR,
            target_n=range_input.target_n,
            range_start=range_input.range_start,
            range_end=range_input.range_end,
            coverage_id=range_input.coverage_id,
            child_index=range_input.child_index,
            partition_params_digest=range_input.partition_params_digest,
            found_factor=None,
            cofactor=None,
            checked_divisor_count=end - start + 1,
            executor_summary={"executor": "scripted_ai_api_profile"},
            created_at=NOW,
        ).to_dict()
    return RangeResult(
        range_result_id=f"range_result:{range_input.coverage_id}:{range_input.child_index}",
        result_kind=RANGE_RESULT_FOUND_FACTOR,
        target_n=range_input.target_n,
        range_start=range_input.range_start,
        range_end=range_input.range_end,
        coverage_id=range_input.coverage_id,
        child_index=range_input.child_index,
        partition_params_digest=range_input.partition_params_digest,
        found_factor=str(found),
        cofactor=str(target // found),
        checked_divisor_count=(found - start) + 1,
        executor_summary={"executor": "scripted_ai_api_profile"},
        created_at=NOW,
    ).to_dict()


def _default_ai_profile_config() -> AIAPIExecutorConfig:
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
                    "entry_id": "ai_profile_qwen",
                    "enabled": True,
                    "base_url": "https://api.siliconflow.cn/v1",
                    "api_key_env": "TOKENSHARE_AI_PROFILE_FAKE_KEY",
                    "model": "Qwen/Qwen2.5-7B-Instruct",
                    "endpoint": "/chat/completions",
                    "supports_json_mode": True,
                    "supports_streaming": False,
                    "request_overrides": {"temperature": 0.0},
                    "pricing": {
                        "currency": "CNY",
                        "input_per_million_tokens": 1.0,
                        "output_per_million_tokens": 2.0,
                        "observed_at": "2026-06-30",
                        "source_note": "ai profile fixture price",
                    },
                    "tags": ["ai_profile", "fixture"],
                }
            ],
            "local_concurrency": {"max_in_flight_global": 1},
            "metadata": {"purpose": "phase8-ai-profile-suite"},
        }
    )


def _strict_arithmetic_config(config: AIAPIExecutorConfig) -> AIAPIExecutorConfig:
    kept_entries = [
        entry
        for entry in config.entries
        if not _known_strict_arithmetic_mismatch_model(entry)
    ]
    if not kept_entries or len(kept_entries) == len(config.entries):
        return config
    return AIAPIExecutorConfig(
        schema_version=config.schema_version,
        executor_id=config.executor_id,
        provider_family=config.provider_family,
        selection_policy=dict(config.selection_policy),
        defaults=dict(config.defaults),
        entries=kept_entries,
        local_concurrency=dict(config.local_concurrency),
        metadata={
            **dict(config.metadata),
            "strict_arithmetic_model_filter": {
                "excluded_entry_ids": [
                    entry.entry_id for entry in config.entries if entry not in kept_entries
                ],
                "reason": "strict factorization JSON profile requires reliable arithmetic execution",
            },
        },
    )


def _known_strict_arithmetic_mismatch_model(entry) -> bool:
    model = entry.model.lower()
    tags = {tag.lower() for tag in entry.tags}
    return "qwen3" in model or "qwen" in tags


def _transport(
    *,
    transport: Any | None,
    scripted_outputs: list[str],
    real_transport: bool,
) -> Any:
    if transport is not None:
        return transport
    if real_transport:
        return UrlLibSiliconFlowTransport()
    return ScriptedSiliconFlowTransport(scripted_outputs)


def _protocol_config(output_dir: Path):
    from tokenshare.core.models import ProtocolConfig

    return ProtocolConfig.default(
        config_id="config_ai_profile",
        artifact_store_uri=f"file://{output_dir.as_posix()}/artifacts",
        event_log_uri=f"file://{(output_dir / 'events' / 'event_log.jsonl').as_posix()}",
        metadata={"suite": "ai_profile"},
    )


def _range_output_contract() -> OutputContract:
    return OutputContract(
        output_contract_id=RANGE_RESULT_CONTRACT_ID,
        required_outputs=["range_result"],
        output_schema_refs={"range_result": schema_ref(RANGE_RESULT_SCHEMA_VERSION)},
        raw_output_policy={"allowed": True, "media_type": "application/json"},
        parsed_output_schema_ref=schema_ref(RANGE_RESULT_SCHEMA_VERSION),
    )


def _environment_ref(*, seed: int) -> EnvironmentRef:
    return EnvironmentRef(
        environment_id="env_ai_profile",
        environment_digest="sha256:env_ai_profile",
        runtime="python",
        tool_versions={"ai_api_executor": AI_EXECUTOR_VERSION},
        resource_limits={"timeout_seconds": 30},
        fixture_profile_digest="sha256:ai_profile_fixture",
        seed=seed,
        clock_policy="fixed",
        created_at=NOW,
    )


def _task_unit(*, task_id: str, unit_id: str) -> TaskUnit:
    return TaskUnit(
        unit_id=unit_id,
        task_id=task_id,
        parent_unit_id=None,
        depth=1,
        unit_type="factor_search_range",
        state=TaskState.PROCESSING,
        input_refs={},
        canonical_output_refs={},
        required_capabilities={"executor": "ai_api", "factorization": True},
        weight=1.0,
        budget_limit=None,
        deadline=None,
        plugin_payload={"requested_output": REQUESTED_OUTPUT_PRIME_FACTORIZATION},
        metadata={"ai_profile": True},
        created_at=NOW,
        updated_at=NOW,
    )


def _write_summary_csv(path: Path, profiles: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(AI_PROFILE_SUMMARY_COLUMNS))
        writer.writeheader()
        for profile in profiles:
            writer.writerow(
                {
                    column: _csv_value(profile.get(column))
                    for column in AI_PROFILE_SUMMARY_COLUMNS
                }
            )


def _write_ai_profile_settings(
    *,
    settings_path: Path,
    output_root: Path,
    seed: int,
    ai_api_config: AIAPIExecutorConfig,
    real_transport: bool,
    summary_csv_path: Path,
    suite_report_path: Path,
) -> None:
    filtered_config = _strict_arithmetic_config(ai_api_config)
    range_inputs = [item.to_dict() for item in _semiprime_range_inputs()]
    body = {
        "schema_version": "phase8.ai_profile_settings.v1",
        "seed": seed,
        "real_transport": real_transport,
        "output_root": output_root.as_posix(),
        "summary_csv_path": summary_csv_path.as_posix(),
        "suite_report_path": suite_report_path.as_posix(),
        "settings_path": settings_path.as_posix(),
        "request_limits": {"timeout_seconds": 30, "max_tokens": 1024},
        "soft_hints": {"temperature": 0.0},
        "prompt_constraints": {"requires_json_mode": True, "format": "json"},
        "parser_policy": {
            "parser_id": "factorization.range_result.parser.v1",
            "parse_required": True,
            "raw_only_allowed": False,
        },
        "ai_api_config": {
            "config_digest": ai_api_config.config_digest,
            "provider_family": ai_api_config.provider_family,
            "executor_id": ai_api_config.executor_id,
            "selection_policy": dict(ai_api_config.selection_policy),
            "defaults": dict(ai_api_config.defaults),
            "entry_count": len(ai_api_config.entries),
            "enabled_entry_ids": [
                entry.entry_id for entry in ai_api_config.entries if entry.enabled
            ],
            "strict_arithmetic_entry_ids": [
                entry.entry_id for entry in filtered_config.entries
            ],
            "entries": [entry.to_safe_dict() for entry in ai_api_config.entries],
            "metadata": dict(ai_api_config.metadata),
        },
        "profile_settings": [
            {
                "case_id": "deterministic_semiprime_range_flow",
                "experiment_id": "exp1_factorization_e2e",
                "executor_profile": "deterministic_local",
                "fixture_name": "semiprime_range_flow",
                "target_n": "91",
                "requested_child_count": 4,
            },
            {
                "case_id": "ai_api_semiprime_range_flow",
                "experiment_id": "exp1_factorization_e2e",
                "executor_profile": "ai_api",
                "fixture_name": "semiprime_range_flow",
                "target_n": "91",
                "range_inputs": range_inputs,
            },
            {
                "case_id": "ai_api_parse_failure_raw_only",
                "experiment_id": "exp2_failure_recovery",
                "executor_profile": "ai_api",
                "fixture_name": "parse_failure_raw_only_forbidden",
                "target_n": "91",
                "range_inputs": [range_inputs[1]],
                "failure_stimulus": {
                    "kind": "raw_text",
                    "content_digest": digest_json("I found factor 7."),
                },
            },
        ],
    }
    settings_path.write_text(
        json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _csv_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return "|".join(str(item) for item in value)
    if value is None:
        return ""
    return str(value)


def _int_metric(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float_metric(value: Any) -> float:
    if isinstance(value, bool) or value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
