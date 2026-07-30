"""Factorization paper experiment compatibility adapter.

正常 FULL 路径把 catalog case/config 交给 Factorization runtime bridge 与 system
coordinator，再从权威 ledger/artifacts 投影旧结果。保留的 selector 直连分支只供
历史回归使用；scripted/capturing transport 始终不能成为论文可采信结果。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from math import isqrt
from pathlib import Path
from threading import Lock
from typing import Any

from tokenshare.core.models import ArtifactRef, JsonObject, ProtocolConfig, TaskState, TaskUnit
from tokenshare.executors.ai_api import AIAPIExecutor
from tokenshare.executors.ai_api_config import AIAPIExecutorConfig, load_ai_api_config
from tokenshare.executors.ai_api_transport import (
    UrlLibDeepSeekTransport,
    UrlLibOpenAITransport,
    UrlLibSiliconFlowTransport,
)
from tokenshare.executors.contracts import (
    EnvironmentRef,
    ExecutionRequest,
    ExecutionSubmission,
)
from tokenshare.experiments.paper_model_identity import (
    ValidatedModelEndpointBinding,
    build_fixed_entry_executor_requirements,
    prepare_fixed_entry_execution_config,
    validate_condition_fixed_entry_identity,
    validate_fixed_entry_submission_identity,
)
from tokenshare.experiments.paper_models import (
    PaperAttemptResult,
    PaperAttemptStatus,
    PaperEligibilityReport,
    PaperExperimentCondition,
    PaperFailureKind,
    PaperFailureStage,
    PaperTaskResult,
    PaperTaskStatus,
    evaluate_paper_eligibility,
)
from tokenshare.experiments.paper_report import scan_artifact_store_for_secrets
from tokenshare.experiments.paper_projection import project_paper_protocol_run
from tokenshare.experiments.paper_ablation import runtime_controls_for_mode
from tokenshare.experiments.paper_unit_commitments import (
    factorization_range_plugin_payload,
)
from tokenshare.experiments.paper_workers import (
    PaperAIUnit,
    project_worker_death_records,
)
from tokenshare.local_runtime import (
    ProcessWorkerBackend,
    ProtocolExecutionScope,
    ProtocolRunCoordinator,
    ProtocolRunRequest,
    SequentialWorkerBackend,
    ThreadWorkerBackend,
    WorkerTerminationPolicy,
)
from tokenshare.plugins.contracts import OutputContract
from tokenshare.plugins.factorization.descriptor import build_factorization_plugin_descriptor
from tokenshare.plugins.factorization.merge_policy import (
    RangeSlotMergeInput,
    merge_required_range_results,
)
from tokenshare.plugins.factorization.runtime_adapter import (
    DETERMINISTIC_EXECUTOR_ID,
    FactorizationExecutionBridge,
    FactorizationRuntimeAdapter,
)
from tokenshare.plugins.factorization.models import (
    FactorIntegerSubject,
    FactorSearchRangeInput,
    PrimeFactorizationResult,
    RangeResult,
    RootInput,
    canonical_json_digest,
)
from tokenshare.plugins.factorization.prompt_builder import (
    build_factor_search_prompt_package,
)
from tokenshare.plugins.factorization.schemas import (
    CANDIDATE_RANGE_PARTITION_STRATEGY_ID,
    FACTOR_SEARCH_INSTRUCTION_SCHEMA_VERSION,
    FACTOR_SEARCH_RANGE_TASK_TYPE,
    FACTOR_SEARCH_RANGE_INPUT_SCHEMA_VERSION,
    PLUGIN_ID,
    PLUGIN_VERSION,
    PRIME_FACTORIZATION_RESULT_SCHEMA_VERSION,
    RANGE_RESULT_CONTRACT_ID,
    RANGE_RESULT_FOUND_FACTOR,
    RANGE_RESULT_NO_FACTOR,
    RANGE_RESULT_SCHEMA_VERSION,
    RANGE_RESULT_VALIDATOR_POLICY_ID,
    REQUESTED_OUTPUT_PRIME_FACTORIZATION,
    ROOT_INPUT_SCHEMA_VERSION,
    schema_ref,
)
from tokenshare.plugins.factorization.split_strategy import (
    FactorizationSplitPlanResult,
    build_factorization_split_plan,
    resolve_requested_child_count,
)
from tokenshare.plugins.factorization.validator import (
    build_factor_search_instruction,
    parse_factorization_ai_output,
    verify_range_result,
)
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger
from tokenshare.protocol_engine import ProtocolEngine


NOW = "2026-07-14T00:00:00Z"
AI_EXECUTOR_ID = "executor_ai_api"
AI_EXECUTOR_VERSION = "0.1.0"
FAKE_KEY_ENV = "TOKENSHARE_FACTORIZATION_PAPER_FAKE_KEY"
REAL_TRANSPORT_TYPES = (
    UrlLibSiliconFlowTransport,
    UrlLibOpenAITransport,
    UrlLibDeepSeekTransport,
)


@dataclass(frozen=True, kw_only=True)
class FactorizationPaperRunResult:
    condition: PaperExperimentCondition
    case_id: str
    task_result: PaperTaskResult
    attempt_results: tuple[PaperAttemptResult, ...]
    eligibility_report: PaperEligibilityReport
    split_summary: JsonObject
    range_results: tuple[JsonObject, ...]
    merge_summary: JsonObject
    final_prime_factors: list[JsonObject]
    run_evidence: JsonObject
    output_root: str
    event_records: tuple[JsonObject, ...] = ()
    fault_records: tuple[JsonObject, ...] = ()
    schema_version: str = "tokenshare.factorization_paper_run.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "condition": self.condition.to_dict(),
            "case_id": self.case_id,
            "task_result": self.task_result.to_dict(),
            "attempt_results": [item.to_dict() for item in self.attempt_results],
            "eligibility_report": self.eligibility_report.to_dict(),
            "split_summary": dict(self.split_summary),
            "range_results": [dict(item) for item in self.range_results],
            "merge_summary": dict(self.merge_summary),
            "final_prime_factors": [dict(item) for item in self.final_prime_factors],
            "run_evidence": dict(self.run_evidence),
            "output_root": self.output_root,
            "event_records": [dict(item) for item in self.event_records],
            "fault_records": [dict(item) for item in self.fault_records],
        }


class ScriptedFactorizationRangeTransport:
    """测试用 provider transport：仍让 AIAPIExecutor 完整保存 provider evidence。"""

    def __init__(
        self,
        *,
        force_false_negative_child_indices: set[int] | None = None,
    ) -> None:
        self.force_false_negative_child_indices = set(force_false_negative_child_indices or set())
        self.calls: list[JsonObject] = []

    def post_chat_completion(
        self,
        *,
        entry,
        api_key: str,
        body: JsonObject,
        timeout_seconds: int,
    ):
        range_fields = _extract_range_fields_from_chat_body(body)
        child_index = int(range_fields["child_index"])
        result = _scripted_range_result(
            range_fields,
            force_false_negative=child_index in self.force_false_negative_child_indices,
        )
        self.calls.append(
            {
                "entry_id": entry.entry_id,
                "model": entry.model,
                "body": json.loads(json.dumps(body, ensure_ascii=False, sort_keys=True)),
                "timeout_seconds": timeout_seconds,
                "api_key_seen": bool(api_key),
                "range_result": result,
            }
        )
        return _ProviderResponse(
            status_code=200,
            body={
                "id": f"factorization-paper-scripted-{child_index}",
                "model": entry.model,
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                result,
                                ensure_ascii=False,
                                sort_keys=True,
                            )
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 70,
                    "completion_tokens": 30,
                    "total_tokens": 100,
                },
            },
        )


class _ProviderResponse:
    def __init__(self, *, status_code: int, body: JsonObject) -> None:
        self.status_code = status_code
        self.body = body
        self.text = json.dumps(body, ensure_ascii=False)


def run_factorization_paper_case(
    *,
    case: JsonObject,
    condition: PaperExperimentCondition,
    output_root: str | Path,
    transport: Any | None = None,
    real_transport: bool,
    ai_api_config: AIAPIExecutorConfig | None = None,
    entry_id: str | None = None,
    max_tokens: int = 512,
    timeout_seconds: int = 30,
    selected_ai_unit_id: str | None = None,
    post_raw_output_hook: Any | None = None,
    ablation_mode: str | None = None,
    worker_termination_policy: WorkerTerminationPolicy | None = None,
    protocol_run_dispatcher: Any | None = None,
) -> FactorizationPaperRunResult:
    """Deprecated compatibility API for historical/selector regressions.

    正常 FULL 调用应经 ``paper_dispatcher.dispatch_paper_case()`` 进入 system
    coordinator；本函数暂时保留旧调用形状，不发出运行时 warning。
    """

    _validate_factorization_case_for_adapter(case)
    normalized_ablation_mode = _normalize_ablation_mode(ablation_mode)
    if condition.domain != "factorization":
        raise ValueError("condition domain must be factorization")
    if condition.difficulty != case["difficulty"]:
        raise ValueError("condition difficulty must match factorization case difficulty")
    if real_transport and ai_api_config is None:
        raise ValueError("real transport factorization paper runs require ai_api_config")
    _validate_real_transport_mode(
        real_transport=real_transport,
        transport=transport,
        ai_api_config=ai_api_config,
    )
    resolved_entry_id = _resolve_condition_entry_id(condition, entry_id)
    source_config = (
        ai_api_config
        if ai_api_config is not None
        else _default_scripted_config(resolved_entry_id)
    )
    validated_binding = validate_condition_fixed_entry_identity(
        condition=condition,
        source_config=source_config,
    )
    config = _prepare_config(
        source_config,
        entry_id=resolved_entry_id,
        validated_binding=validated_binding,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
    )
    secret_values = _real_secret_values(config) if real_transport else ()

    case_id = str(case["case_id"])
    root = Path(output_root)
    run_root = root / case_id
    store = ArtifactStore(run_root)
    active_transport = transport
    if active_transport is None:
        active_transport = (
            _default_real_transport(config)
            if real_transport
            else ScriptedFactorizationRangeTransport()
        )

    return _run_factorization_full_via_coordinator(
        case=case,
        condition=condition,
        run_root=run_root,
        store=store,
        active_transport=active_transport,
        real_transport=real_transport,
        config=config,
        validated_binding=validated_binding,
        secret_values=secret_values,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        post_raw_output_hook=post_raw_output_hook,
        protocol_run_dispatcher=protocol_run_dispatcher,
        ablation_mode=normalized_ablation_mode,
        worker_termination_policy=worker_termination_policy,
        selected_ai_unit_id=selected_ai_unit_id,
    )

    # 以下旧 selector lifecycle 已与当前入口隔离，仅保留历史输出 reader provenance。
    root_input_ref = _save_root_input(store, case)
    subject = _factor_integer_subject(case=case, root_input_ref=root_input_ref)
    split_plan = _build_split_plan(case=case, subject=subject)
    executor = AIAPIExecutor(
        executor_id=AI_EXECUTOR_ID,
        executor_version=AI_EXECUTOR_VERSION,
        artifact_store=store,
        config=config,
        transport=active_transport,
        parser=(
            None
            if normalized_ablation_mode == "NO_PARSER_POLICY"
            else parse_factorization_ai_output
        ),
        post_raw_output_hook=post_raw_output_hook,
    )

    indexed_ranges = list(enumerate(split_plan.partition.ranges))
    available_ai_unit_ids = tuple(
        f"range_{range_input.child_index}"
        for _index, range_input in indexed_ranges
    )
    if (
        selected_ai_unit_id is not None
        and selected_ai_unit_id not in available_ai_unit_ids
    ):
        raise ValueError(
            "selected_ai_unit_id is not present in factorization split plan"
        )
    if selected_ai_unit_id is not None:
        indexed_ranges = [
            (index, range_input)
            for index, range_input in indexed_ranges
            if f"range_{range_input.child_index}" == selected_ai_unit_id
        ]

    range_records: list[JsonObject] = []
    attempts: list[PaperAttemptResult] = []
    for index, range_input in indexed_ranges:
        request = _build_range_execution_request(
            store=store,
            case=case,
            condition=condition,
            split_plan=split_plan,
            range_input=range_input,
            index=index,
            provider_family=config.provider_family,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )
        request_ref = store.save_json(
            request.to_dict(),
            artifact_id=f"paper_request_{case_id}_{index}",
            artifact_type="ExecutionRequest",
            artifact_schema_id="phase3.execution_request",
            artifact_schema_version="v1",
            source={"kind": "factorization_paper_adapter", "case_id": case_id},
            metadata={"case_id": case_id, "child_index": index},
            created_at=NOW,
        )
        submission = executor.execute(
            request,
            submission_id=f"paper_submission_{case_id}_{index}",
            submitted_at=NOW,
        )
        usage_ref = _save_usage_artifact(
            store=store,
            case_id=case_id,
            index=index,
            submission=submission,
        )
        model_execution_record, model_execution_record_ref = (
            _save_model_execution_record(
                store=store,
                condition=condition,
                binding=validated_binding,
                prepared_config=config,
                case_id=case_id,
                request=request,
                request_ref=request_ref,
                submission=submission,
                usage_ref=usage_ref,
                paper_eligible_transport=not _is_offline_capturing_transport(
                    active_transport
                ),
            )
        )
        model_identity_mismatch = (
            model_execution_record is not None
            and model_execution_record.identity_status
            == "model_identity_mismatch"
        )
        candidate_ref = (
            None
            if model_identity_mismatch
            else submission.candidate_output_refs.get("range_result")
        )
        verification = None
        verification_body: JsonObject | None = None
        range_result_body: JsonObject | None = None
        canonical_output_ref = None
        attempt_status = _attempt_status_from_submission(submission.result_kind)
        if model_identity_mismatch:
            attempt_status = PaperAttemptStatus.MODEL_IDENTITY_MISMATCH
        if candidate_ref is not None:
            range_result_body = _read_json_ref(store, candidate_ref)
            if normalized_ablation_mode == "NO_VERIFICATION":
                canonical_output_ref = candidate_ref.to_dict()
                verification_body = {
                    "accepted": True,
                    "status": "skipped_by_ablation",
                    "layer_summary": {"verification_executed": False},
                    "failure_summary": None,
                }
            else:
                verification = verify_range_result(
                    range_result_body,
                    child_input=range_input,
                    no_factor_recheck_max_divisors=(
                        int(range_input.range_end)
                        - int(range_input.range_start)
                        + 1
                    ),
                )
                verification_body = {
                    "accepted": verification.accepted,
                    "status": verification.status,
                    "layer_summary": verification.layer_summary,
                    "failure_summary": verification.failure_summary,
                }
                if verification.accepted:
                    canonical_output_ref = candidate_ref.to_dict()
                else:
                    attempt_status = PaperAttemptStatus.VERIFICATION_REJECTED
        attempts.append(
            _paper_attempt_result(
                store=store,
                condition=condition,
                case_id=case_id,
                index=index,
                request=request,
                request_ref=request_ref,
                submission=submission,
                usage_ref=usage_ref,
                attempt_status=attempt_status,
                planned_ai_unit_id=f"range_{range_input.child_index}",
                model_execution_record_ref=model_execution_record_ref,
                error_kind_override=(
                    "model_identity_mismatch" if model_identity_mismatch else None
                ),
            )
        )
        range_records.append(
            {
                "unit_id": request.unit_id,
                "child_index": range_input.child_index,
                "range_input": range_input.to_dict(),
                "submission_result_kind": submission.result_kind,
                "range_result": range_result_body,
                "verification": (
                    verification_body
                    if verification_body is not None
                    else (
                        {
                            "accepted": False,
                            "status": "model_identity_mismatch",
                            "layer_summary": {},
                            "failure_summary": {
                                "kind": "model_identity_mismatch",
                                "reasons": list(
                                    model_execution_record.mismatch_reasons
                                ),
                            },
                        }
                        if model_execution_record is not None
                        and model_identity_mismatch
                        else {
                            "accepted": False,
                            "status": "missing_candidate",
                            "layer_summary": {},
                            "failure_summary": submission.error,
                        }
                    )
                ),
                "canonical_output_ref": canonical_output_ref,
                "model_execution_record_ref": (
                    model_execution_record_ref.to_dict()
                    if model_execution_record_ref is not None
                    else None
                ),
            }
        )
        if model_identity_mismatch:
            break

    merge_summary: JsonObject
    prime_result: PrimeFactorizationResult | None = None
    accepted_range_records = [
        item for item in range_records if item["verification"]["accepted"] is True
    ]
    all_ranges_executed = len(range_records) == len(split_plan.partition.ranges)
    merge_gate_ready = (
        all_ranges_executed
        and len(accepted_range_records) == len(range_records)
    )
    premature_merge = (
        normalized_ablation_mode == "NO_MERGE_GATE"
        and all_ranges_executed
        and not merge_gate_ready
    )
    if merge_gate_ready or premature_merge:
        try:
            slot_inputs = _slot_inputs_for_merge(split_plan, range_records)
            if normalized_ablation_mode == "NO_SLOT_INTEGRITY" and slot_inputs:
                slot_inputs[0] = replace(
                    slot_inputs[0],
                    slot_key=f"ablation_wrong_slot:{slot_inputs[0].slot_key}",
                )
            merge_policy_result = merge_required_range_results(
                merge_plan=split_plan.merge_plan,
                slot_results=slot_inputs,
                merge_unit_id=f"paper_merge_unit_{case_id}",
                created_at=NOW,
            )
            prime_result = merge_policy_result.prime_factorization_result
            merge_summary = {
                "status": "completed" if prime_result is not None else "blocked",
                "result_kind": merge_policy_result.merge_result.result_kind,
                "merge_result": merge_policy_result.merge_result.to_dict(),
                "expected_output_resolvable": (
                    merge_policy_result.expected_output_resolvable
                ),
            }
            if prime_result is not None:
                _save_prime_factorization_result(
                    store=store,
                    case_id=case_id,
                    result=prime_result,
                )
        except (KeyError, TypeError, ValueError) as exc:
            merge_summary = {
                "status": "failed",
                "result_kind": None,
                "merge_error": str(exc),
            }
    else:
        merge_summary = {
            "status": "blocked",
            "result_kind": None,
            "rejected_range_count": len(range_records) - len(accepted_range_records),
            "unexecuted_range_count": (
                len(split_plan.partition.ranges) - len(range_records)
            ),
        }

    final_prime_factors = (
        [dict(item) for item in prime_result.to_dict()["prime_factors"]]
        if prime_result is not None
        else []
    )
    accepted_validity = _prime_factors_match_oracle(final_prime_factors, case)
    merge_summary.update(
        {
            "premature_merge_attempted": premature_merge,
            "slot_integrity_violation": (
                normalized_ablation_mode == "NO_SLOT_INTEGRITY"
            ),
            "root_validity_audit_passed": accepted_validity,
        }
    )
    task_status = (
        PaperTaskStatus.COMPLETED
        if prime_result is not None and accepted_validity
        else PaperTaskStatus.FAILED
    )
    failure_stage = None
    failure_kind = None
    if task_status != PaperTaskStatus.COMPLETED:
        if len(accepted_range_records) == len(range_records):
            failure_stage = PaperFailureStage.MERGE
            failure_kind = PaperFailureKind.INTERNAL_ERROR
        else:
            failure_stage, failure_kind = _task_failure_from_child_evidence(
                attempts=attempts,
                range_records=range_records,
            )

    run_evidence = _run_evidence(
        store=store,
        real_transport=real_transport,
        transport_kind="ai_api" if real_transport else "scripted",
        secret_values=secret_values,
        transport=active_transport,
    )
    run_evidence["ablation_runtime"] = _ablation_runtime_evidence(
        mode=normalized_ablation_mode,
        attempts=attempts,
        merge_summary=merge_summary,
    )
    eligibility = evaluate_paper_eligibility(
        attempts=attempts,
        run_evidence=run_evidence,
    )
    task_result = PaperTaskResult(
        condition_id=condition.condition_id,
        repeat_id=condition.repeat_id,
        task_id=f"paper_factorization_{case_id}",
        domain="factorization",
        difficulty=str(case["difficulty"]),
        root_status=task_status,
        accepted_validity=accepted_validity,
        failure_stage=failure_stage,
        failure_kind=failure_kind,
        attempt_count=len(attempts),
        provider_attempt_count=sum(
            _provider_attempt_count(_read_json_ref(store, attempt.usage_ref))
            for attempt in attempts
            if attempt.usage_ref is not None
        ),
        wall_clock_ms=sum(attempt.latency_ms for attempt in attempts),
        total_tokens=sum(attempt.total_tokens for attempt in attempts),
        cost_estimate=sum(attempt.cost_estimate for attempt in attempts),
        cost_estimate_currency=_single_attempt_currency(attempts),
        cost_estimate_status=_combined_cost_estimate_status(attempts),
        event_refs=[],
        artifact_refs=[
            ref
            for attempt in attempts
            for ref in (
                attempt.raw_output_ref,
                attempt.model_execution_record_ref,
            )
            if ref is not None
        ],
        paper_eligible=eligibility.paper_eligible,
    )
    result = FactorizationPaperRunResult(
        condition=condition,
        case_id=case_id,
        task_result=task_result,
        attempt_results=tuple(attempts),
        eligibility_report=eligibility,
        split_summary=_split_summary(split_plan),
        range_results=tuple(range_records),
        merge_summary=merge_summary,
        final_prime_factors=final_prime_factors,
        run_evidence=run_evidence,
        output_root=run_root.as_posix(),
    )
    _write_case_outputs(run_root, result)
    return result


@dataclass(frozen=True, kw_only=True)
class _CapturedRangeCall:
    request: ExecutionRequest
    submission: ExecutionSubmission
    request_ref: ArtifactRef
    usage_ref: ArtifactRef
    model_execution_record: Any | None
    model_execution_record_ref: ArtifactRef | None


class _FixedIdentityRangeExecutor:
    """注入的 range executor policy：调用前核对 request，调用后审计身份。"""

    def __init__(
        self,
        executor: AIAPIExecutor,
        *,
        store: ArtifactStore,
        condition: PaperExperimentCondition,
        binding: ValidatedModelEndpointBinding,
        config: AIAPIExecutorConfig,
        executor_requirements: JsonObject,
        case_id: str,
        paper_eligible_transport: bool,
    ) -> None:
        self.executor = executor
        self.store = store
        self.condition = condition
        self.binding = binding
        self.config = config
        self.executor_requirements = dict(executor_requirements)
        self.case_id = case_id
        self.paper_eligible_transport = paper_eligible_transport
        self.calls: list[_CapturedRangeCall] = []
        self._calls_lock = Lock()
        self._next_call_index = 0
        self._process_call_indexes: dict[str, int] = {}

    def __getstate__(self):
        state = dict(self.__dict__)
        state.pop("_calls_lock", None)
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)
        self._calls_lock = Lock()

    def prepare_process_execution(
        self,
        request: ExecutionRequest,
        execution_index: int,
    ) -> None:
        self._process_call_indexes[request.attempt_id] = execution_index

    def export_process_result(
        self,
        request: ExecutionRequest,
        submission: ExecutionSubmission,
    ) -> _CapturedRangeCall:
        del submission
        return next(
            call
            for call in reversed(self.calls)
            if call.request.attempt_id == request.attempt_id
        )

    def ingest_process_result(self, captured: _CapturedRangeCall) -> None:
        with self._calls_lock:
            if any(
                call.request.attempt_id == captured.request.attempt_id
                for call in self.calls
            ):
                return
            self.calls.append(captured)

    def execute(self, request, *, submission_id: str, submitted_at: str):
        request_ref = self.store.save_json(
            request.to_dict(),
            artifact_id=request.request_id,
            artifact_type="ExecutionRequest",
            artifact_schema_id="phase3.execution_request",
            artifact_schema_version="v1",
            source={"kind": "protocol_engine"},
            metadata={"task_id": request.task_id, "attempt_id": request.attempt_id},
            created_at=request.created_at,
        )
        with self._calls_lock:
            index = self._process_call_indexes.pop(
                request.attempt_id,
                self._next_call_index,
            )
            self._next_call_index = max(self._next_call_index + 1, index + 1)
        requirement_mismatches = _executor_requirement_mismatches(
            request=request,
            expected=self.executor_requirements,
            executor_id=self.executor.executor_id,
            executor_version=self.executor.executor_version,
        )
        if requirement_mismatches:
            submission = ExecutionSubmission(
                submission_id=submission_id,
                request_id=request.request_id,
                task_id=request.task_id,
                unit_id=request.unit_id,
                attempt_id=request.attempt_id,
                lease_id=request.lease_id,
                fencing_token=request.fencing_token,
                executor_id=self.executor.executor_id,
                executor_version=self.executor.executor_version,
                result_kind="fatal_executor_error",
                raw_output_ref=None,
                parsed_output_ref=None,
                candidate_output_refs={},
                parse_failure_ref=None,
                log_ref=None,
                environment_ref=request.environment_ref,
                environment_summary={"runtime": "fixed_identity_policy"},
                provenance_ref=None,
                usage_summary={"provider_attempt_count": 0},
                error={
                    "kind": "executor_requirement_mismatch",
                    "reasons": requirement_mismatches,
                },
                submitted_at=submitted_at,
            )
        else:
            submission = self.executor.execute(
                request,
                submission_id=submission_id,
                submitted_at=submitted_at,
            )
        usage_ref = _save_usage_artifact(
            store=self.store,
            case_id=self.case_id,
            index=index,
            submission=submission,
        )
        if requirement_mismatches:
            # provider 尚未调用，不能伪造 provenance 或 response identity evidence。
            model_record = None
            record_ref = None
        else:
            model_record, record_ref = _save_model_execution_record(
                store=self.store,
                condition=self.condition,
                binding=self.binding,
                prepared_config=self.config,
                case_id=self.case_id,
                request=request,
                request_ref=request_ref,
                submission=submission,
                usage_ref=usage_ref,
                paper_eligible_transport=self.paper_eligible_transport,
            )
        if (
            model_record is not None
            and model_record.identity_status == "model_identity_mismatch"
        ):
            submission = replace(
                submission,
                result_kind="fatal_executor_error",
                candidate_output_refs={},
                error={
                    "kind": "model_identity_mismatch",
                    "reasons": list(model_record.mismatch_reasons),
                },
            )
        with self._calls_lock:
            self.calls.append(
                _CapturedRangeCall(
                    request=request,
                    submission=submission,
                    request_ref=request_ref,
                    usage_ref=usage_ref,
                    model_execution_record=model_record,
                    model_execution_record_ref=record_ref,
                )
            )
        return submission


def _executor_requirement_mismatches(
    *,
    request: ExecutionRequest,
    expected: JsonObject,
    executor_id: str,
    executor_version: str,
) -> list[str]:
    mismatches = [
        f"hard_requirement:{name}"
        for name, expected_value in expected.items()
        if request.hard_requirements.get(name) != expected_value
    ]
    if request.executor.get("executor_id") != executor_id:
        mismatches.append("executor_id")
    if request.executor.get("executor_version") != executor_version:
        mismatches.append("executor_version")
    return mismatches


def _fixed_entry_executor_requirements(
    *,
    config: AIAPIExecutorConfig,
    binding: ValidatedModelEndpointBinding | None,
) -> JsonObject:
    """返回可安全持久化、足以固定模型端点的调度约束。"""

    return build_fixed_entry_executor_requirements(
        config=config,
        binding=binding,
    )


def _paper_task_status_from_runtime(
    runtime_status: str,
    *,
    accepted_validity: bool,
) -> PaperTaskStatus:
    """只把协议终态投影到 paper task；非终态/未知值一律拒绝。"""

    if runtime_status == "completed":
        return (
            PaperTaskStatus.COMPLETED
            if accepted_validity
            else PaperTaskStatus.FAILED
        )
    if runtime_status == "failed":
        return PaperTaskStatus.FAILED
    raise ValueError("paper projection requires a terminal runtime status")


def _run_factorization_full_via_coordinator(
    *,
    case: JsonObject,
    condition: PaperExperimentCondition,
    run_root: Path,
    store: ArtifactStore,
    active_transport: Any,
    real_transport: bool,
    config: AIAPIExecutorConfig,
    validated_binding: ValidatedModelEndpointBinding,
    secret_values: tuple[str, ...],
    max_tokens: int,
    timeout_seconds: int,
    post_raw_output_hook: Any | None,
    protocol_run_dispatcher: Any | None,
    ablation_mode: str,
    worker_termination_policy: WorkerTerminationPolicy | None,
    selected_ai_unit_id: str | None,
) -> FactorizationPaperRunResult:
    """FULL 兼容壳：只配置 runtime、调用 coordinator、投影旧 result shape。"""

    case_id = str(case["case_id"])
    task_id = f"paper_factorization_{case_id}"
    ledger = EventLedger(run_root / "events" / f"{task_id}.jsonl")
    protocol_config = replace(
        ProtocolConfig.default(
            config_id=f"factorization_runtime_{case_id}",
            artifact_store_uri="file://artifacts",
            event_log_uri=f"file://events/{task_id}.jsonl",
            metadata={"paper_factorization": True, "case_id": case_id},
        ),
        max_retries=(
            worker_termination_policy.termination_limit + 1
            if worker_termination_policy is not None
            else 1
            if condition.experiment_id == "exp4_real_ai_protocol_ablation"
            else 2
            if post_raw_output_hook is not None
            else 0
        ),
    )
    executor_requirements = _fixed_entry_executor_requirements(
        config=config,
        binding=validated_binding,
    )
    plugin_runtime = FactorizationRuntimeAdapter(
        provider_family=config.provider_family,
        seed=condition.seed,
        protocol_config=protocol_config,
        executor_requirements=executor_requirements,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        created_at=NOW,
    )
    ai_executor = AIAPIExecutor(
        executor_id=AI_EXECUTOR_ID,
        executor_version=AI_EXECUTOR_VERSION,
        artifact_store=store,
        config=config,
        transport=active_transport,
        parser=parse_factorization_ai_output,
        post_raw_output_hook=post_raw_output_hook,
    )
    capturing_executor = _FixedIdentityRangeExecutor(
        ai_executor,
        store=store,
        condition=condition,
        binding=validated_binding,
        config=config,
        executor_requirements=executor_requirements,
        case_id=case_id,
        paper_eligible_transport=not _is_offline_capturing_transport(
            active_transport
        ),
    )
    coordinator = ProtocolRunCoordinator(
        engine=ProtocolEngine(
            event_ledger=ledger,
            protocol_config=protocol_config,
            artifact_store=store,
        ),
        artifact_store=store,
        event_ledger=ledger,
        now=lambda: NOW,
    )
    controls = runtime_controls_for_mode(ablation_mode)
    execution_bridge = FactorizationExecutionBridge(
        plugin_runtime=plugin_runtime,
        range_executor=capturing_executor,
    )
    worker_backend = (
        ProcessWorkerBackend(
            executor=execution_bridge,
            capacity=condition.worker_count,
            submitted_at=lambda: NOW,
            termination_policy=worker_termination_policy,
        )
        if worker_termination_policy is not None
        else
        SequentialWorkerBackend(
            executor=execution_bridge,
            submitted_at=lambda: NOW,
        )
        if condition.worker_count == 1
        else ThreadWorkerBackend(
            executor=execution_bridge,
            capacity=condition.worker_count,
            submitted_at=lambda: NOW,
        )
    )
    protocol_request = ProtocolRunRequest(
        run_id=f"{condition.condition_id}_{case_id}",
        root_input=case,
        plugin_runtime=plugin_runtime,
        worker_backend=worker_backend,
        mechanism_policy=controls.mechanism_policy,
        hooks=(
            post_raw_output_hook
            if callable(
                getattr(
                    post_raw_output_hook,
                    "after_parsed_candidate_persisted",
                    None,
                )
            )
            else controls.hooks
        ),
        continue_after_terminal_child_failure=True,
        execution_scope=(
            ProtocolExecutionScope()
            if selected_ai_unit_id is None
            else ProtocolExecutionScope(
                mode="selected_ai_units",
                selected_ai_unit_ids=(selected_ai_unit_id,),
            )
        ),
    )
    runtime_result = (
        coordinator.run_root(protocol_request)
        if protocol_run_dispatcher is None
        else protocol_run_dispatcher(
            coordinator=coordinator,
            request=protocol_request,
        )
    )

    runtime_events = ledger.read_all()
    request_refs_by_id = {
        str(event.payload["request_id"]): ArtifactRef.from_dict(
            event.payload["request_ref"]
        )
        for event in runtime_events
        if event.event_type == "EXECUTION_REQUEST_RECORDED"
    }
    verification_by_unit = {
        str(event.payload["unit_id"]): dict(event.payload["verification_report"])
        for event in runtime_events
        if event.event_type == "VERIFICATION_RECORDED"
    }
    submitted_candidate_refs_by_attempt = {
        str(event.payload["attempt_id"]): {
            name: ArtifactRef.from_dict(ref)
            for name, ref in _read_json_ref(
                store,
                ArtifactRef.from_dict(event.payload["submission_ref"]),
            ).get("candidate_output_refs", {}).items()
        }
        for event in runtime_events
        if event.event_type == "EXECUTION_SUBMISSION_RECORDED"
        and event.payload.get("acceptance_status") == "accepted"
    }
    canonical_range_refs_by_unit = {
        str(event.payload["unit_id"]): ArtifactRef.from_dict(
            event.payload["canonical_output_refs"]["range_result"]
        )
        for event in runtime_events
        if event.event_type == "CANONICAL_OUTPUTS_BOUND"
        and "range_result" in event.payload["canonical_output_refs"]
    }
    canonical_refs_by_attempt = {
        str(event.payload["selected_attempt_id"]): {
            name: ArtifactRef.from_dict(ref)
            for name, ref in event.payload["canonical_output_refs"].items()
        }
        for event in runtime_events
        if event.event_type == "CANONICAL_OUTPUTS_BOUND"
        and isinstance(event.payload.get("selected_attempt_id"), str)
    }
    attempts: list[PaperAttemptResult] = []
    independent_validity_by_attempt: dict[str, bool] = {}
    projection_metadata_by_unit: dict[str, JsonObject] = {}
    range_records: list[JsonObject] = []
    for index, captured in enumerate(capturing_executor.calls):
        request = captured.request
        submission = captured.submission
        request_ref = captured.request_ref
        if request_refs_by_id[request.request_id] != request_ref:
            raise ValueError("runtime request artifact diverged from executor policy")
        usage_ref = captured.usage_ref
        model_record = captured.model_execution_record
        model_record_ref = captured.model_execution_record_ref
        model_mismatch = (
            model_record is not None
            and model_record.identity_status == "model_identity_mismatch"
        )
        status = _attempt_status_from_submission(submission.result_kind)
        if model_mismatch:
            status = PaperAttemptStatus.MODEL_IDENTITY_MISMATCH
        range_input_ref = request.input_artifact_refs["range_input"]
        range_input_body = _read_json_ref(store, range_input_ref)
        projection_metadata_by_unit[request.unit_id] = {
            "planned_ai_unit_id": f"range_{range_input_body['child_index']}"
        }
        candidate_ref = submitted_candidate_refs_by_attempt.get(
            request.attempt_id,
            submission.candidate_output_refs,
        ).get("range_result")
        range_result_body = (
            _read_json_ref(store, candidate_ref) if candidate_ref is not None else None
        )
        try:
            independent_validity_by_attempt[request.attempt_id] = (
                range_result_body is not None
                and verify_range_result(
                    range_result_body,
                    child_input=FactorSearchRangeInput(**range_input_body),
                ).status
                == "passed"
            )
        except (KeyError, TypeError, ValueError):
            independent_validity_by_attempt[request.attempt_id] = False
        recorded_verification = verification_by_unit.get(request.unit_id, {})
        recorded_status = str(recorded_verification.get("status", "error"))
        if recorded_status == "rejected" and not model_mismatch:
            status = PaperAttemptStatus.VERIFICATION_REJECTED
        accepted = (
            candidate_ref is not None
            and not model_mismatch
            and recorded_status in {"passed", "accepted"}
        )
        verification_body: JsonObject
        if recorded_verification and not model_mismatch:
            layer_results = dict(recorded_verification.get("layer_results", {}))
            verification_metadata = dict(
                recorded_verification.get("metadata", {})
            )
            plugin_domain_layer = verification_metadata.get(
                "plugin_domain_layer"
            )
            verification_body = {
                "accepted": accepted,
                "status": recorded_status,
                "layer_summary": (
                    dict(plugin_domain_layer)
                    if isinstance(plugin_domain_layer, dict)
                    else layer_results
                ),
                "metadata": verification_metadata,
                "verification_environment": dict(
                    recorded_verification.get("verification_environment", {})
                ),
                "failure_summary": (
                    None
                    if accepted
                    else {
                        "kind": "verification_rejected",
                        "report_id": recorded_verification.get(
                            "verification_report_id"
                        ),
                    }
                ),
            }
        else:
            verification_body = {
                "accepted": False,
                "status": (
                    "model_identity_mismatch"
                    if model_mismatch
                    else "missing_candidate"
                ),
                "layer_summary": {},
                "failure_summary": submission.error,
            }
        attempts.append(
            _paper_attempt_result(
                store=store,
                condition=condition,
                case_id=case_id,
                index=index,
                request=request,
                request_ref=request_ref,
                submission=submission,
                usage_ref=usage_ref,
                attempt_status=status,
                planned_ai_unit_id=f"range_{range_input_body['child_index']}",
                model_execution_record_ref=model_record_ref,
                error_kind_override=(
                    "model_identity_mismatch" if model_mismatch else None
                ),
            )
        )
        range_records.append(
            {
                "unit_id": request.unit_id,
                "child_index": range_input_body["child_index"],
                "range_input": range_input_body,
                "submission_result_kind": submission.result_kind,
                "range_result": range_result_body,
                "verification": verification_body,
                "canonical_output_ref": (
                    canonical_range_refs_by_unit[request.unit_id].to_dict()
                    if accepted
                    else None
                ),
                "model_execution_record_ref": (
                    model_record_ref.to_dict() if model_record_ref is not None else None
                ),
            }
        )

    prime_ref = plugin_runtime.merge_candidate_refs.get(
        REQUESTED_OUTPUT_PRIME_FACTORIZATION
    )
    prime_body = _read_json_ref(store, prime_ref) if prime_ref is not None else None
    final_prime_factors = (
        [dict(item) for item in prime_body["prime_factors"]]
        if prime_body is not None
        else []
    )
    is_partial = runtime_result.status == "partial"
    accepted_validity = (
        None
        if is_partial
        else _prime_factors_match_oracle(final_prime_factors, case)
    )
    merge_summary: JsonObject = {
        "status": (
            "partial"
            if is_partial
            else "completed"
            if prime_ref is not None
            else "blocked"
        ),
        "result_kind": (
            "prime_factorization_result" if prime_ref is not None else None
        ),
        "expected_output_resolvable": prime_ref is not None,
        "premature_merge_attempted": False,
        "slot_integrity_violation": (
            plugin_runtime.slot_integrity_violation_applied
        ),
        "slot_binding_applied_by": (
            "local_runtime_policy"
            if plugin_runtime.slot_integrity_violation_applied
            else None
        ),
        "root_validity_audit_passed": accepted_validity,
    }
    projection = project_paper_protocol_run(
        condition=condition,
        case=case,
        runtime_result=runtime_result,
        protocol_events=runtime_events,
        artifact_store=store,
        accepted_validity=accepted_validity,
        attempt_metadata_by_unit=projection_metadata_by_unit,
    )
    attempts = list(projection.attempt_results)
    fault_records: tuple[JsonObject, ...] = ()
    if worker_termination_policy is not None:
        ai_units_by_id: dict[str, PaperAIUnit] = {}
        actual_unit_id_by_planned: dict[str, str] = {}
        provider_tokens_by_attempt_id: dict[str, int] = {}
        for captured in capturing_executor.calls:
            planned_ai_unit_id = captured.request.soft_hints.get(
                "planned_ai_unit_id"
            )
            if not isinstance(planned_ai_unit_id, str):
                continue
            actual_unit_id_by_planned[planned_ai_unit_id] = (
                captured.request.unit_id
            )
            ai_units_by_id[captured.request.unit_id] = PaperAIUnit(
                task_id=captured.request.task_id,
                unit_id=captured.request.unit_id,
                unit_kind="factorization_range",
                domain="factorization",
                metadata={"planned_ai_unit_id": planned_ai_unit_id},
            )
            provider_tokens_by_attempt_id[captured.request.attempt_id] = (
                _int_metric(
                    (captured.submission.usage_summary or {}).get(
                        "total_tokens"
                    )
                )
            )
        missing_targets = [
            planned
            for planned in worker_termination_policy.target_planned_ai_unit_ids
            if planned not in actual_unit_id_by_planned
        ]
        if missing_targets and runtime_result.status != "failed":
            raise ValueError(
                f"worker-death targets were not dispatched: {missing_targets}"
            )
        if not missing_targets:
            fault_records = project_worker_death_records(
                artifact_store=store,
                condition_id=condition.condition_id,
                repeat_id=condition.repeat_id,
                run_id=runtime_result.run_id,
                ai_units=tuple(ai_units_by_id.values()),
                selected_target_unit_ids=tuple(
                    actual_unit_id_by_planned[planned]
                    for planned in worker_termination_policy.target_planned_ai_unit_ids
                ),
                kill_point=worker_termination_policy.kill_point,
                worker_facts=worker_backend.execution_facts,
                protocol_events=runtime_events,
                coordinator_pid=os.getpid(),
                created_at=NOW,
                provider_tokens_by_attempt_id=provider_tokens_by_attempt_id,
            )
        attempts = _enrich_worker_death_attempts(
            attempts=attempts,
            captured_calls=capturing_executor.calls,
            worker_facts=worker_backend.execution_facts,
            fault_records=fault_records,
            store=store,
            condition=condition,
            case_id=case_id,
        )
    if not attempts and runtime_result.status == "failed":
        _validate_failed_protocol_projection_scope(
            condition=condition,
            runtime_events=runtime_events,
            store=store,
        )
        attempts = _failed_protocol_attempts_from_events(
            condition=condition,
            runtime_result=runtime_result,
            runtime_events=runtime_events,
            store=store,
        )
    run_evidence = _run_evidence(
        store=store,
        real_transport=real_transport,
        transport_kind="ai_api" if real_transport else "scripted",
        secret_values=secret_values,
        transport=active_transport,
    )
    run_evidence["protocol_runtime"] = {
        "run_id": runtime_result.run_id,
        "task_id": runtime_result.task_id,
        "root_unit_id": runtime_result.root_unit_id,
        "status": runtime_result.status,
        "event_count": len(runtime_result.event_refs),
        "lifecycle_coverage": projection.lifecycle_coverage,
        "generation_identity": projection.runtime_generation_identity,
        "runtime_observation": projection.runtime_observation,
        "case_id": case_id,
        "factor_position_quantile": case.get("factor_position_quantile"),
        "execution_scope": protocol_request.execution_scope.mode,
        "selected_ai_unit_ids": list(
            protocol_request.execution_scope.selected_ai_unit_ids
        ),
    }
    run_evidence["ablation_runtime"] = _ablation_runtime_evidence(
        mode=ablation_mode,
        attempts=attempts,
        merge_summary=merge_summary,
        max_retries=protocol_config.max_retries,
        submitted_candidate_refs_by_attempt=submitted_candidate_refs_by_attempt,
        canonical_refs_by_attempt=canonical_refs_by_attempt,
        independent_validity_by_attempt=independent_validity_by_attempt,
        final_validity=accepted_validity,
        hook_observations=tuple(
            dict(item)
            for item in runtime_result.summary.get(
                "runtime_hook_observations", ()
            )
        ),
    )
    eligibility = evaluate_paper_eligibility(
        attempts=attempts,
        run_evidence=run_evidence,
    )
    projection_ineligibility_reasons = projection.ineligibility_reasons
    if (
        worker_termination_policy is not None
        and attempts
        and all(attempt.paper_eligible for attempt in attempts)
    ):
        projection_ineligibility_reasons = tuple(
            reason
            for reason in projection_ineligibility_reasons
            if reason != "incomplete_attempt_evidence"
        )
    combined_reasons = tuple(
        dict.fromkeys(
            (
                *eligibility.ineligibility_reasons,
                *projection_ineligibility_reasons,
            )
        )
    )
    eligibility = replace(
        eligibility,
        paper_eligible=not combined_reasons,
        ineligibility_reasons=combined_reasons,
    )
    task_result = replace(
        projection.task_result,
        paper_eligible=eligibility.paper_eligible,
    )
    result = FactorizationPaperRunResult(
        condition=condition,
        case_id=case_id,
        task_result=task_result,
        attempt_results=tuple(attempts),
        eligibility_report=eligibility,
        split_summary=_split_summary(plugin_runtime.planned_split_plan),
        range_results=tuple(range_records),
        merge_summary=merge_summary,
        final_prime_factors=final_prime_factors,
        run_evidence=run_evidence,
        output_root=run_root.as_posix(),
        event_records=tuple(event.to_dict() for event in runtime_events),
        fault_records=fault_records,
    )
    _write_case_outputs(run_root, result)
    return result


def _enrich_worker_death_attempts(
    *,
    attempts: list[PaperAttemptResult],
    captured_calls: list[_CapturedRangeCall],
    worker_facts: tuple[Any, ...],
    fault_records: tuple[JsonObject, ...],
    store: ArtifactStore,
    condition: PaperExperimentCondition,
    case_id: str,
) -> list[PaperAttemptResult]:
    """用真实子进程 sidecar 补齐 ledger 中无 submission 的死亡 attempt。"""

    killed_facts = {
        str(fact.attempt_id): fact
        for fact in worker_facts
        if fact.result_kind == "worker_terminated" and fact.attempt_id
    }
    calls_by_attempt = {
        captured.request.attempt_id: captured for captured in captured_calls
    }
    record_ref_by_attempt = {
        str(record["dead_attempt"]["attempt_id"]): dict(record["record_ref"])
        for record in fault_records
    }
    enriched: list[PaperAttemptResult] = []
    for attempt in attempts:
        fact = killed_facts.get(attempt.attempt_id)
        captured = calls_by_attempt.get(attempt.attempt_id)
        if fact is None or captured is None:
            enriched.append(attempt)
            continue
        planned_ai_unit_id = str(
            captured.request.soft_hints["planned_ai_unit_id"]
        )
        captured_attempt = _paper_attempt_result(
            store=store,
            condition=condition,
            case_id=case_id,
            index=fact.execution_index,
            request=captured.request,
            request_ref=captured.request_ref,
            submission=captured.submission,
            usage_ref=captured.usage_ref,
            attempt_status=PaperAttemptStatus.WORKER_DIED,
            planned_ai_unit_id=planned_ai_unit_id,
            model_execution_record_ref=(
                captured.model_execution_record_ref
            ),
            error_kind_override="worker_died",
        )
        model_record_eligible = bool(
            captured.model_execution_record is not None
            and captured.model_execution_record.paper_eligible
        )
        enriched.append(
            replace(
                attempt,
                worker_id=str(fact.worker_id or attempt.worker_id),
                attempt_status=PaperAttemptStatus.WORKER_DIED,
                request_ref=captured_attempt.request_ref,
                raw_output_ref=captured_attempt.raw_output_ref,
                parsed_output_ref=captured_attempt.parsed_output_ref,
                parse_failure_ref=captured_attempt.parse_failure_ref,
                provenance_ref=captured_attempt.provenance_ref,
                usage_ref=captured_attempt.usage_ref,
                started_at=str(fact.started_at or attempt.started_at),
                ended_at=str(fact.ended_at or attempt.ended_at),
                latency_ms=captured_attempt.latency_ms,
                prompt_tokens=captured_attempt.prompt_tokens,
                completion_tokens=captured_attempt.completion_tokens,
                total_tokens=captured_attempt.total_tokens,
                cost_estimate=captured_attempt.cost_estimate,
                error_kind="worker_died",
                fault_injection_ref=record_ref_by_attempt.get(attempt.attempt_id),
                paper_eligible=model_record_eligible,
                model_execution_record_ref=(
                    captured_attempt.model_execution_record_ref
                ),
                planned_ai_unit_id=planned_ai_unit_id,
            )
        )
    return enriched


def _failed_protocol_attempts_from_events(
    *,
    condition: PaperExperimentCondition,
    runtime_result: Any,
    runtime_events: tuple[Any, ...] | list[Any],
    store: ArtifactStore,
) -> list[PaperAttemptResult]:
    """投影 provider 尚未 dispatch 前已经存在的真实协议 attempt。"""

    _validate_failed_protocol_projection_scope(
        condition=condition,
        runtime_events=runtime_events,
        store=store,
    )

    events = [
        event.to_dict() if hasattr(event, "to_dict") else dict(event)
        for event in runtime_events
    ]
    snapshots = {
        str(attempt["attempt_id"]): dict(attempt)
        for event in events
        if event.get("event_type") == "ATTEMPT_STATE_CHANGED"
        and isinstance((attempt := event.get("payload", {}).get("attempt")), dict)
        and attempt.get("attempt_id")
    }
    worker_facts = {
        str(fact.get("attempt_id")): dict(fact)
        for fact in runtime_result.summary.get("runtime_observation", {}).get(
            "worker_execution_facts", ()
        )
        if isinstance(fact, dict) and fact.get("attempt_id")
    }
    projected: list[PaperAttemptResult] = []
    for event in events:
        if event.get("event_type") != "EXECUTION_REQUEST_RECORDED":
            continue
        payload = event.get("payload", {})
        request_ref = payload.get("request_ref")
        if not isinstance(request_ref, dict):
            raise ValueError("failed protocol attempt is missing request artifact")
        request = _read_json_ref(store, ArtifactRef.from_dict(request_ref))
        executor = request.get("executor")
        if not isinstance(executor, dict):
            raise ValueError(
                "failed protocol attempt is missing persisted executor evidence"
            )
        executor_id = _required_projection_text(
            executor.get("executor_id"),
            "executor_id",
        )
        executor_type = _required_projection_text(
            executor.get("executor_type"),
            "executor_type",
        )
        attempt_id = _required_projection_text(
            request.get("attempt_id") or payload.get("attempt_id"),
            "attempt_id",
        )
        unit_id = _required_projection_text(
            request.get("unit_id") or payload.get("unit_id"),
            "unit_id",
        )
        snapshot = snapshots.get(attempt_id, {})
        fact = worker_facts.get(attempt_id, {})
        if snapshot.get("state") != "Failed":
            continue
        worker_id = _required_projection_text(
            fact.get("worker_id") or snapshot.get("client_id"),
            "worker_id",
        )
        started_at = _required_projection_text(
            fact.get("started_at")
            or snapshot.get("started_at")
            or request.get("created_at"),
            "started_at",
        )
        ended_at = _required_projection_text(
            fact.get("ended_at") or snapshot.get("finished_at"),
            "ended_at",
        )
        error_kind = _required_projection_text(
            snapshot.get("failure_kind")
            or snapshot.get("failure_reason")
            or fact.get("result_kind"),
            "failure_reason",
        )
        projected.append(
            PaperAttemptResult(
                condition_id=condition.condition_id,
                repeat_id=condition.repeat_id,
                run_id=runtime_result.run_id,
                task_id=runtime_result.task_id,
                unit_id=unit_id,
                attempt_id=attempt_id,
                worker_id=worker_id,
                provider_attempt_index=0,
                attempt_status=PaperAttemptStatus.EXECUTOR_ERROR,
                provider=None,
                model=None,
                entry_id=None,
                request_ref=dict(request_ref),
                raw_output_ref=None,
                parsed_output_ref=None,
                parse_failure_ref=None,
                provenance_ref=None,
                usage_ref=None,
                started_at=started_at,
                ended_at=ended_at,
                latency_ms=0,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                cost_estimate=0.0,
                error_kind=error_kind,
                fault_injection_ref=None,
                paper_eligible=False,
                provider_attempt_count=0,
                paper_difficulty=condition.paper_difficulty,
                planned_ai_unit_id=None,
                executor_id=executor_id,
                executor_type=executor_type,
                schema_version="tokenshare.paper_attempt_result.v2",
            )
        )
    if not projected:
        raise ValueError("failed protocol run is missing persisted attempt evidence")
    return projected


def _validate_failed_protocol_projection_scope(
    *,
    condition: PaperExperimentCondition,
    runtime_events: tuple[Any, ...] | list[Any],
    store: ArtifactStore,
) -> None:
    """仅允许 Exp3 的 deterministic root failure 进入 v2 负向投影。"""

    error_message = (
        "paper attempt v2 projection is restricted to Exp3 deterministic runtime"
    )
    if condition.experiment_id != "exp3_real_ai_fault_recovery":
        raise ValueError(error_message)
    request_events = [
        event.to_dict() if hasattr(event, "to_dict") else dict(event)
        for event in runtime_events
        if (
            event.event_type
            if hasattr(event, "event_type")
            else event.get("event_type")
        )
        == "EXECUTION_REQUEST_RECORDED"
    ]
    if not request_events:
        raise ValueError("failed protocol run is missing persisted request evidence")
    for event in request_events:
        request_ref = event.get("payload", {}).get("request_ref")
        if not isinstance(request_ref, dict):
            raise ValueError("failed protocol attempt is missing request artifact")
        request = _read_json_ref(store, ArtifactRef.from_dict(request_ref))
        executor = request.get("executor")
        if not isinstance(executor, dict):
            raise ValueError(
                "failed protocol attempt is missing persisted executor evidence"
            )
        if (
            executor.get("executor_id") != DETERMINISTIC_EXECUTOR_ID
            or executor.get("executor_type") != "deterministic_local"
        ):
            raise ValueError(error_message)


def _required_projection_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"failed protocol attempt is missing persisted {field_name} evidence"
        )
    return value


def _normalize_ablation_mode(value: str | None) -> str:
    mode = "FULL" if value is None else str(value).upper()
    supported = {
        "FULL",
        "NO_VERIFICATION",
        "NO_PARSER_POLICY",
        "NO_REQUEUE",
        "NO_MERGE_GATE",
        "NO_SLOT_INTEGRITY",
    }
    if mode not in supported:
        raise ValueError("unsupported Experiment 4 ablation mode")
    return mode


def _ablation_runtime_evidence(
    *,
    mode: str,
    attempts: list[PaperAttemptResult],
    merge_summary: JsonObject,
    max_retries: int = 0,
    submitted_candidate_refs_by_attempt: (
        dict[str, dict[str, ArtifactRef]] | None
    ) = None,
    canonical_refs_by_attempt: dict[str, dict[str, ArtifactRef]] | None = None,
    independent_validity_by_attempt: dict[str, bool] | None = None,
    final_validity: bool | None = None,
    hook_observations: tuple[JsonObject, ...] = (),
) -> JsonObject:
    submitted_candidate_refs_by_attempt = (
        submitted_candidate_refs_by_attempt or {}
    )
    canonical_refs_by_attempt = canonical_refs_by_attempt or {}
    independent_validity_by_attempt = independent_validity_by_attempt or {}
    disabled = {
        "FULL": None,
        "NO_VERIFICATION": "verification",
        "NO_PARSER_POLICY": "parser_policy",
        "NO_REQUEUE": "requeue",
        "NO_MERGE_GATE": "merge_gate",
        "NO_SLOT_INTEGRITY": "slot_integrity",
    }[mode]
    observed_mechanisms = {
        str(item.get("disabled_mechanism"))
        for item in hook_observations
        if item.get("event_type") == "EXPERIMENT_ABLATION_GATE_APPLIED"
    }
    return {
        "schema_version": "tokenshare.paper_ablation_runtime.v1",
        "mode": mode,
        "disabled_mechanism": disabled,
        "applied_before_adapter_completion": True,
        "max_retries": max_retries,
        "parser_policy_enabled": mode != "NO_PARSER_POLICY",
        "verification_enabled": mode != "NO_VERIFICATION",
        "replacement_attempts_allowed": mode != "NO_REQUEUE",
        "merge_gate_enabled": mode != "NO_MERGE_GATE",
        "slot_integrity_enabled": mode != "NO_SLOT_INTEGRITY",
        "raw_only_exposed": (
            mode == "NO_PARSER_POLICY"
            and any(attempt.raw_output_ref is not None for attempt in attempts)
            and all(
                attempt.parsed_output_ref is None
                or (
                    attempt.raw_output_ref is not None
                    and attempt.parsed_output_ref.get("content_hash")
                    == attempt.raw_output_ref.get("content_hash")
                )
                for attempt in attempts
            )
        ),
        "premature_merge_attempted": bool(
            merge_summary.get("premature_merge_attempted")
            or "merge_gate" in observed_mechanisms
        ),
        "slot_integrity_violation": bool(
            merge_summary.get("slot_integrity_violation")
            or "slot_integrity" in observed_mechanisms
        ),
        "root_validity_audit_passed": bool(
            merge_summary.get("root_validity_audit_passed")
        ),
        "attempt_observations": [
            {
                "attempt_id": attempt.attempt_id,
                "unit_id": attempt.unit_id,
                "raw_output_ref": attempt.raw_output_ref,
                "candidate_output_ref": (
                    next(
                        iter(
                            submitted_candidate_refs_by_attempt.get(
                                attempt.attempt_id,
                                {},
                            ).values()
                        ),
                        None,
                    ).to_dict()
                    if submitted_candidate_refs_by_attempt.get(attempt.attempt_id)
                    else None
                ),
                "canonical_output_refs": {
                    name: ref.to_dict()
                    for name, ref in canonical_refs_by_attempt.get(
                        attempt.attempt_id,
                        {},
                    ).items()
                },
                "independent_candidate_validity": (
                    independent_validity_by_attempt.get(attempt.attempt_id)
                ),
                "final_validity": final_validity,
            }
            for attempt in attempts
        ],
        "hook_observations": [dict(item) for item in hook_observations],
    }


def _validate_factorization_case_for_adapter(case: JsonObject) -> None:
    if case.get("schema_version") != "tokenshare.paper_factorization_case.v1":
        raise ValueError("factorization paper case schema_version mismatch")
    if case.get("split_params", {}).get("strategy_id") != CANDIDATE_RANGE_PARTITION_STRATEGY_ID:
        raise ValueError("factorization paper adapter requires candidate_range_partition.v1")
    if str(case.get("candidate_start")) != "2":
        raise ValueError("first factorization paper adapter slice requires candidate_start=2")
    if int(str(case.get("candidate_end"))) != isqrt(int(str(case["target_n"]))):
        raise ValueError("factorization paper adapter requires complete [2, floor_sqrt(n)] domain")


def _save_root_input(store: ArtifactStore, case: JsonObject):
    root_input = RootInput(
        target_n=str(case["target_n"]),
        requested_output=REQUESTED_OUTPUT_PRIME_FACTORIZATION,
        case_label=str(case["case_id"]),
        schema_version=ROOT_INPUT_SCHEMA_VERSION,
    )
    return store.save_json(
        root_input.to_dict(),
        artifact_id=f"paper_root_input_{case['case_id']}",
        artifact_type="RootInput",
        artifact_schema_id="factorization.root_input",
        artifact_schema_version="v1",
        source={"kind": "factorization_paper_adapter", "case_id": case["case_id"]},
        metadata={"case_id": case["case_id"]},
        created_at=NOW,
    )


def _factor_integer_subject(*, case: JsonObject, root_input_ref) -> FactorIntegerSubject:
    case_id = str(case["case_id"])
    return FactorIntegerSubject(
        subject_id=f"paper_factor_subject_{case_id}",
        task_id=f"paper_factorization_{case_id}",
        unit_id=f"paper_factor_root_{case_id}",
        target_n=str(case["target_n"]),
        source_kind="root_input",
        source_ref=root_input_ref.to_dict(),
        requested_output=REQUESTED_OUTPUT_PRIME_FACTORIZATION,
        created_at=NOW,
    )


def _build_split_plan(
    *,
    case: JsonObject,
    subject: FactorIntegerSubject,
) -> FactorizationSplitPlanResult:
    descriptor = build_factorization_plugin_descriptor()
    requested_child_count = resolve_requested_child_count(case["split_params"])
    return build_factorization_split_plan(
        subject=subject,
        canonical_selection_id=f"paper_canonical_root_{case['case_id']}",
        canonical_output_bundle_digest=canonical_json_digest(subject.to_dict()),
        plugin_descriptor_digest=descriptor.descriptor_digest,
        expansion_scope_hash=canonical_json_digest(
            {"task_id": subject.task_id, "unit_id": subject.unit_id}
        ),
        expansion_decision_id=f"paper_expansion_decision_{case['case_id']}",
        requested_child_count=requested_child_count,
        max_children_per_unit=max(requested_child_count, 1),
        created_at=NOW,
        min_divisor=case["candidate_start"],
        max_divisor=case["candidate_end"],
    )


def _build_range_execution_request(
    *,
    store: ArtifactStore,
    case: JsonObject,
    condition: PaperExperimentCondition,
    split_plan: FactorizationSplitPlanResult,
    range_input: FactorSearchRangeInput,
    index: int,
    provider_family: str,
    max_tokens: int,
    timeout_seconds: int,
) -> ExecutionRequest:
    case_id = str(case["case_id"])
    task_id = f"paper_factorization_{case_id}"
    child_key = f"range:{range_input.coverage_id}:{range_input.child_index}"
    unit_id = split_plan.child_unit_ids_by_logical_key[child_key]
    request_id = f"paper_request_{case_id}_{index}"
    range_input_ref = store.save_json(
        range_input.to_dict(),
        artifact_id=f"paper_range_input_{case_id}_{index}",
        artifact_type="FactorSearchRangeInput",
        artifact_schema_id="factorization.factor_search_range_input",
        artifact_schema_version="v1",
        source={"kind": "factorization_paper_adapter", "case_id": case_id},
        metadata={"case_id": case_id, "child_index": index},
        created_at=NOW,
    )
    instruction = build_factor_search_instruction(
        request_id=request_id,
        unit_id=unit_id,
        range_input=range_input,
    )
    instruction_ref = store.save_json(
        instruction.to_dict(),
        artifact_id=f"paper_factor_search_instruction_{case_id}_{index}",
        artifact_type="ExecutionInstruction",
        artifact_schema_id="factorization.factor_search_instruction",
        artifact_schema_version="v1",
        source={"kind": "factorization_paper_adapter", "case_id": case_id},
        metadata={"case_id": case_id, "child_index": index},
        created_at=NOW,
    )
    prompt = build_factor_search_prompt_package(
        request_id=request_id,
        task_id=task_id,
        unit_id=unit_id,
        range_input=range_input,
        instruction=instruction,
        created_at=NOW,
        seed=condition.seed,
    )
    prompt_ref = store.save_json(
        prompt.to_dict(),
        artifact_id=f"paper_prompt_{case_id}_{index}",
        artifact_type="PromptPackage",
        artifact_schema_id="phase3.prompt_package",
        artifact_schema_version="v1",
        source={"kind": "factorization_paper_adapter", "case_id": case_id},
        metadata={"case_id": case_id, "child_index": index},
        created_at=NOW,
    )
    descriptor = build_factorization_plugin_descriptor()
    return ExecutionRequest(
        request_id=request_id,
        task_id=task_id,
        unit_id=unit_id,
        attempt_id=f"paper_attempt_{case_id}_{index}",
        lease_id=f"paper_lease_{case_id}_{index}",
        fencing_token=f"paper_fence_{case_id}_{index}",
        plugin={
            "plugin_id": PLUGIN_ID,
            "plugin_version": PLUGIN_VERSION,
            "plugin_descriptor_digest": descriptor.descriptor_digest,
            "ai_output_parser_policy_id": "factorization.range_result.parser.v1",
        },
        executor={"executor_id": AI_EXECUTOR_ID, "executor_version": AI_EXECUTOR_VERSION},
        registry_snapshot_id=f"paper_registry_snapshot_{case_id}",
        allocation_decision={
            "decision_id": f"paper_allocation_{case_id}_{index}",
            "selected_executor_id": AI_EXECUTOR_ID,
            "eligible_executor_ids": [AI_EXECUTOR_ID],
        },
        capability_snapshot={"executor": "ai_api", "provider_family": provider_family},
        task_unit_snapshot=_range_task_unit(
            task_id=task_id,
            unit_id=unit_id,
            range_input_ref=range_input_ref,
            case=case,
            range_input_body=range_input.to_dict(),
        ).to_dict(),
        input_artifact_refs={"range_input": range_input_ref},
        output_contract=_range_output_contract(),
        hard_requirements={"executor": "ai_api", "provider_family": provider_family},
        soft_hints={
            "temperature": 0.0,
            "paper_condition_id": condition.condition_id,
            "planned_ai_unit_id": f"range_{range_input.child_index}",
            "paper_provider_attempt_index": 0,
        },
        environment_ref=_environment_ref(seed=condition.seed),
        execution_instruction_ref=instruction_ref,
        prompt_package_ref=prompt_ref,
        limits={"timeout_seconds": timeout_seconds, "max_tokens": max_tokens},
        created_at=NOW,
    )


def _range_task_unit(
    *,
    task_id: str,
    unit_id: str,
    range_input_ref,
    case: JsonObject,
    range_input_body: JsonObject,
) -> TaskUnit:
    return TaskUnit(
        unit_id=unit_id,
        task_id=task_id,
        parent_unit_id=f"paper_factor_root_{case['case_id']}",
        depth=1,
        unit_type=FACTOR_SEARCH_RANGE_TASK_TYPE,
        state=TaskState.PROCESSING,
        input_refs={"range_input": range_input_ref},
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
        environment_id="env_factorization_paper",
        environment_digest="sha256:env_factorization_paper",
        runtime="python",
        tool_versions={
            "ai_api_executor": AI_EXECUTOR_VERSION,
            "factorization_plugin": PLUGIN_VERSION,
        },
        resource_limits={"timeout_seconds": 30},
        fixture_profile_digest="sha256:factorization_paper_adapter",
        seed=seed,
        clock_policy="fixed",
        created_at=NOW,
    )


def _prepare_config(
    config: AIAPIExecutorConfig,
    *,
    entry_id: str | None,
    validated_binding: ValidatedModelEndpointBinding | None = None,
    max_tokens: int,
    timeout_seconds: int,
) -> AIAPIExecutorConfig:
    if validated_binding is not None:
        return prepare_fixed_entry_execution_config(
            source_config=config,
            binding=validated_binding,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            adapter_metadata_key="factorization_paper_adapter",
        )
    entries = list(config.entries)
    if entry_id is not None:
        entries = [entry for entry in entries if entry.entry_id == entry_id]
        if not entries:
            raise ValueError(f"missing ai api entry id: {entry_id}")
    defaults = {
        **dict(config.defaults),
        "max_tokens": max_tokens,
        "timeout_seconds": timeout_seconds,
        "temperature": 0.0,
        "max_provider_attempts": 1,
    }
    return AIAPIExecutorConfig(
        schema_version=config.schema_version,
        executor_id=config.executor_id,
        provider_family=config.provider_family,
        selection_policy=dict(config.selection_policy),
        defaults=defaults,
        entries=entries,
        local_concurrency=dict(config.local_concurrency),
        metadata={**dict(config.metadata), "factorization_paper_adapter": True},
    )


def _resolve_condition_entry_id(
    condition: PaperExperimentCondition,
    entry_id: str | None,
) -> str | None:
    if condition.model_entry_id is None:
        return entry_id
    if entry_id is not None and entry_id != condition.model_entry_id:
        raise ValueError(
            "entry_id must match condition.model_entry_id for fixed-entry paper runs"
        )
    return condition.model_entry_id


def _validate_real_transport_mode(
    *,
    real_transport: bool,
    transport: Any | None,
    ai_api_config: AIAPIExecutorConfig | None,
) -> None:
    provider_family = (
        ai_api_config.provider_family if ai_api_config is not None else None
    )
    resolved_transport = _transport_for_provider_family(
        transport,
        provider_family=provider_family,
    )
    if not real_transport:
        if isinstance(resolved_transport, REAL_TRANSPORT_TYPES):
            raise ValueError(
                f"{type(resolved_transport).__name__} is a real provider transport and requires "
                "real_transport=True"
            )
        return
    if ai_api_config is None:
        return
    if transport is None:
        return
    expected_type = _real_transport_type(ai_api_config.provider_family)
    if (
        type(resolved_transport) is not expected_type
        and not _is_offline_capturing_transport(transport)
    ):
        raise ValueError(
            "real transport factorization paper runs require "
            f"{expected_type.__name__} matching "
            f"provider_family={ai_api_config.provider_family}"
        )


def _transport_for_provider_family(
    transport: Any | None,
    *,
    provider_family: str | None,
) -> Any | None:
    if transport is None or provider_family is None:
        return transport
    resolver = getattr(
        transport,
        "tokenshare_transport_for_provider",
        None,
    )
    if not callable(resolver):
        return transport
    resolved = resolver(provider_family)
    if resolved is None:
        raise ValueError("provider transport router returned no transport")
    return resolved


def _default_real_transport(config: AIAPIExecutorConfig):
    transport_type = _real_transport_type(config.provider_family)
    return transport_type()


def _real_transport_type(provider_family: str):
    if provider_family == "siliconflow":
        return UrlLibSiliconFlowTransport
    if provider_family == "openai":
        return UrlLibOpenAITransport
    if provider_family == "deepseek":
        return UrlLibDeepSeekTransport
    raise ValueError(f"unsupported real transport provider_family: {provider_family}")


def _default_scripted_config(entry_id: str | None) -> AIAPIExecutorConfig:
    os.environ.setdefault(FAKE_KEY_ENV, "tokenshare-factorization-paper-fake-key")
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
                "max_tokens": 512,
                "temperature": 0.0,
                "top_p": 0.9,
                "stream": False,
                "max_provider_attempts": 1,
            },
            "entries": [
                {
                    "entry_id": entry_id or "factorization_paper_scripted",
                    "enabled": True,
                    "base_url": "https://api.siliconflow.cn/v1",
                    "api_key_env": FAKE_KEY_ENV,
                    "model": "TokenShare/Scripted-Factorization-Range",
                    "endpoint": "/chat/completions",
                    "supports_json_mode": True,
                    "supports_streaming": False,
                    "request_overrides": {"temperature": 0.0},
                    "pricing": {
                        "currency": "CNY",
                        "input_per_million_tokens": 1.0,
                        "output_per_million_tokens": 2.0,
                    },
                    "tags": ["factorization_paper", "scripted"],
                }
            ],
            "local_concurrency": {"max_in_flight_global": 1},
            "metadata": {"purpose": "factorization-paper-scripted-test"},
        }
    )


def _save_usage_artifact(
    *,
    store: ArtifactStore,
    case_id: str,
    index: int,
    submission,
):
    body = {
        "schema_version": "tokenshare.paper_ai_usage.v1",
        "submission_id": submission.submission_id,
        "request_id": submission.request_id,
        **dict(submission.usage_summary or {}),
    }
    return store.save_json(
        body,
        artifact_id=f"paper_usage_{case_id}_{index}",
        artifact_type="AIUsageSummary",
        artifact_schema_id="tokenshare.paper_ai_usage",
        artifact_schema_version="v1",
        source={"kind": "factorization_paper_adapter", "case_id": case_id},
        metadata={"case_id": case_id, "child_index": index},
        created_at=NOW,
    )


def _save_model_execution_record(
    *,
    store: ArtifactStore,
    condition: PaperExperimentCondition,
    binding: ValidatedModelEndpointBinding | None,
    prepared_config: AIAPIExecutorConfig,
    case_id: str,
    request: ExecutionRequest,
    request_ref: ArtifactRef,
    submission,
    usage_ref: ArtifactRef,
    paper_eligible_transport: bool,
):
    if binding is None:
        return None, None
    if submission.provenance_ref is None:
        raise ValueError("fixed-entry submission requires provenance_ref")
    provenance = _read_json_ref(store, submission.provenance_ref)
    raw_output = (
        _read_json_ref(store, submission.raw_output_ref)
        if submission.raw_output_ref is not None
        else None
    )
    record = validate_fixed_entry_submission_identity(
        expected_identity=binding.identity,
        prepared_execution_config_digest=prepared_config.config_digest,
        condition_id=condition.condition_id,
        repeat_id=condition.repeat_id,
        run_id=f"{condition.condition_id}_{case_id}",
        task_id=request.task_id,
        unit_id=request.unit_id,
        attempt_id=request.attempt_id,
        request=request.to_dict(),
        request_ref=request_ref.to_dict(),
        provenance=provenance,
        provenance_ref=submission.provenance_ref.to_dict(),
        raw_output=raw_output,
        raw_output_ref=(
            submission.raw_output_ref.to_dict()
            if submission.raw_output_ref is not None
            else None
        ),
        usage_ref=usage_ref.to_dict(),
        created_at=NOW,
    )
    if not paper_eligible_transport and record.paper_eligible:
        record = replace(record, paper_eligible=False)
    record_ref = store.save_json(
        record.to_dict(),
        artifact_id=f"paper_model_execution_{submission.submission_id}",
        artifact_type="PaperModelExecutionRecord",
        artifact_schema_id="tokenshare.paper_model_execution_record",
        artifact_schema_version="v2",
        source={
            "kind": "factorization_paper_adapter",
            "condition_id": condition.condition_id,
            "request_id": request.request_id,
        },
        metadata={
            "model_endpoint_identity_digest": (
                binding.identity.model_endpoint_identity_digest
            ),
            "identity_status": record.identity_status,
        },
        created_at=NOW,
    )
    return record, record_ref


def _save_prime_factorization_result(
    *,
    store: ArtifactStore,
    case_id: str,
    result: PrimeFactorizationResult,
) -> None:
    store.save_json(
        result.to_dict(),
        artifact_id=f"paper_prime_factorization_{case_id}",
        artifact_type="canonical_output",
        artifact_schema_id="factorization.prime_factorization_result",
        artifact_schema_version="v1",
        source={"kind": "factorization_paper_adapter", "case_id": case_id},
        metadata={
            "case_id": case_id,
            "output_name": REQUESTED_OUTPUT_PRIME_FACTORIZATION,
        },
        created_at=NOW,
    )


def _paper_attempt_result(
    *,
    store: ArtifactStore,
    condition: PaperExperimentCondition,
    case_id: str,
    index: int,
    request: ExecutionRequest,
    request_ref,
    submission,
    usage_ref,
    attempt_status: PaperAttemptStatus,
    planned_ai_unit_id: str,
    model_execution_record_ref=None,
    error_kind_override: str | None = None,
) -> PaperAttemptResult:
    usage = dict(submission.usage_summary or {})
    provenance_attempt = _last_provenance_attempt(store=store, submission=submission)
    prompt_tokens = _int_metric(usage.get("prompt_tokens"))
    completion_tokens = _int_metric(usage.get("completion_tokens"))
    total_tokens = _int_metric(usage.get("total_tokens"))
    return PaperAttemptResult(
        condition_id=condition.condition_id,
        repeat_id=condition.repeat_id,
        run_id=f"{condition.condition_id}_{case_id}",
        task_id=request.task_id,
        unit_id=request.unit_id,
        planned_ai_unit_id=planned_ai_unit_id,
        attempt_id=request.attempt_id,
        worker_id=f"worker_factorization_paper_{index}",
        provider_attempt_index=0,
        attempt_status=attempt_status,
        provider=str(usage.get("provider_family") or "siliconflow"),
        model=str(usage.get("model") or ""),
        entry_id=str(usage.get("entry_id") or ""),
        request_ref=request_ref.to_dict(),
        raw_output_ref=submission.raw_output_ref.to_dict() if submission.raw_output_ref else None,
        parsed_output_ref=(
            submission.parsed_output_ref.to_dict()
            if submission.parsed_output_ref
            else None
        ),
        parse_failure_ref=(
            submission.parse_failure_ref.to_dict()
            if submission.parse_failure_ref
            else None
        ),
        provenance_ref=(
            submission.provenance_ref.to_dict()
            if submission.provenance_ref
            else None
        ),
        usage_ref=usage_ref.to_dict(),
        started_at=NOW,
        ended_at=NOW,
        latency_ms=_int_metric(provenance_attempt.get("latency_ms")),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cost_estimate=_float_metric(usage.get("cost_estimate")),
        cost_estimate_currency=(
            str(usage["currency"]) if isinstance(usage.get("currency"), str) else None
        ),
        cost_estimate_status=(
            str(usage["cost_estimate_status"])
            if isinstance(usage.get("cost_estimate_status"), str)
            else None
        ),
        error_kind=(
            error_kind_override
            if error_kind_override is not None
            else ((submission.error or {}).get("kind") if submission.error else None)
        ),
        fault_injection_ref=None,
        paper_eligible=False,
        model_execution_record_ref=(
            model_execution_record_ref.to_dict()
            if model_execution_record_ref is not None
            else None
        ),
    )


def _last_provenance_attempt(*, store: ArtifactStore, submission) -> JsonObject:
    if submission.provenance_ref is None:
        return {}
    provenance = _read_json_ref(store, submission.provenance_ref)
    attempts = provenance.get("attempts", [])
    if not isinstance(attempts, list) or not attempts:
        return {}
    last = attempts[-1]
    return dict(last) if isinstance(last, dict) else {}


def _attempt_status_from_submission(result_kind: str) -> PaperAttemptStatus:
    if result_kind == "succeeded":
        return PaperAttemptStatus.SUCCEEDED
    if result_kind == "parse_failed":
        return PaperAttemptStatus.PARSE_FAILED
    if result_kind in {"rate_limited", "provider_error", "auth_error", "connection_error"}:
        return PaperAttemptStatus.PROVIDER_ERROR
    return PaperAttemptStatus.PROVIDER_ERROR


def _slot_inputs_for_merge(
    split_plan: FactorizationSplitPlanResult,
    range_records: list[JsonObject],
) -> list[RangeSlotMergeInput]:
    records_by_child_key = {
        f"range:{record['range_input']['coverage_id']}:{record['range_input']['child_index']}": record
        for record in range_records
    }
    slot_inputs: list[RangeSlotMergeInput] = []
    for slot in split_plan.merge_plan.required_slots:
        record = records_by_child_key[slot["source_child_logical_key"]]
        range_result = RangeResult(**record["range_result"])
        slot_inputs.append(
            RangeSlotMergeInput(
                slot_key=slot["slot_key"],
                range_result=range_result,
                canonical_output_digest=canonical_json_digest(range_result.to_dict()),
            )
        )
    return slot_inputs


def _split_summary(split_plan: FactorizationSplitPlanResult) -> JsonObject:
    return {
        "split_strategy_id": CANDIDATE_RANGE_PARTITION_STRATEGY_ID,
        "range_child_count": len(split_plan.partition.ranges),
        "coverage_id": split_plan.partition.coverage_proof.coverage_id,
        "partition_params_digest": split_plan.partition.params.params_digest,
        "ranges_digest": split_plan.partition.coverage_proof.ranges_digest,
    }


def _run_evidence(
    *,
    store: ArtifactStore,
    real_transport: bool,
    transport_kind: str,
    secret_values: tuple[str, ...],
    transport: Any | None = None,
) -> JsonObject:
    if _is_offline_capturing_transport(transport):
        real_transport = False
        transport_kind = "capturing"
        config_source = "offline_capturing_config"
    else:
        config_source = (
            "local_gitignored_config" if real_transport else "scripted_fixture"
        )
    transport_ref = store.save_json(
        {
            "schema_version": "tokenshare.paper_transport_evidence.v1",
            "real_transport": real_transport,
            "transport_kind": transport_kind,
            "config_source": config_source,
            "api_key_policy": "env_only",
            "executor_kind": "ai_api_executor",
        },
        artifact_id="paper_transport_evidence",
        artifact_type="AITransportEvidence",
        artifact_schema_id="tokenshare.paper_transport_evidence",
        artifact_schema_version="v1",
        source={"kind": "factorization_paper_adapter"},
        metadata={},
        created_at=NOW,
    )
    if secret_values:
        secret_scan = scan_artifact_store_for_secrets(
            store,
            secret_values=secret_values,
        )
    else:
        secret_scan = {
            "schema_version": "tokenshare.paper_secret_scan_report.v1",
            "status": "pending",
            "leak_count": 0,
            "secret_checked_count": 0,
            "scanned_artifact_ids": _artifact_ids(store),
            "scan_scope": "scripted_transport_not_paper_eligible",
        }
    secret_scan_ref = store.save_json(
        secret_scan,
        artifact_id="paper_secret_scan_report",
        artifact_type="SecretScanReport",
        artifact_schema_id="tokenshare.paper_secret_scan_report",
        artifact_schema_version="v1",
        source={"kind": "factorization_paper_adapter"},
        metadata={},
        created_at=NOW,
    )
    artifact_manifests = _artifact_manifests(store)
    return {
        "schema_version": "tokenshare.paper_run_evidence.v1",
        "transport_evidence": {
            "schema_version": "tokenshare.paper_transport_evidence.v1",
            "real_transport": real_transport,
            "transport_kind": transport_kind,
            "config_source": config_source,
            "api_key_policy": "env_only",
            "executor_kind": "ai_api_executor",
            "evidence_ref": transport_ref.to_dict(),
        },
        "secret_scan_report": {
            **secret_scan,
            "report_ref": secret_scan_ref.to_dict(),
        },
        "artifact_manifests": artifact_manifests,
    }


def _is_offline_capturing_transport(transport: Any | None) -> bool:
    return (
        transport is not None
        and getattr(transport, "tokenshare_offline_capturing_transport", False)
        is True
    )


def _single_attempt_currency(attempts: list[PaperAttemptResult]) -> str | None:
    currencies = {
        attempt.cost_estimate_currency
        for attempt in attempts
        if attempt.cost_estimate_currency is not None
    }
    if len(currencies) > 1:
        raise ValueError("a paper task cannot aggregate mixed cost currencies")
    return next(iter(currencies), None)


def _combined_cost_estimate_status(attempts: list[PaperAttemptResult]) -> str | None:
    statuses = {
        attempt.cost_estimate_status
        for attempt in attempts
        if attempt.cost_estimate_status is not None
    }
    if "usage_missing" in statuses:
        return "usage_missing"
    if statuses:
        return "estimated"
    return None


def _real_secret_values(config: AIAPIExecutorConfig) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            entry.resolve_api_key() for entry in config.entries if entry.enabled
        )
    )


def _artifact_manifests(store: ArtifactStore) -> list[JsonObject]:
    manifests: list[JsonObject] = []
    for path in sorted(store.artifact_dir.glob("*.manifest.json")):
        manifests.append(json.loads(path.read_text(encoding="utf-8")))
    return manifests


def _artifact_ids(store: ArtifactStore) -> list[str]:
    return [str(item["artifact_id"]) for item in _artifact_manifests(store)]


def _read_json_ref(store: ArtifactStore, ref) -> JsonObject:
    artifact_ref = ArtifactRef.from_dict(ref) if isinstance(ref, dict) else ref
    return json.loads(store.read_bytes(artifact_ref).decode("utf-8"))


def _provider_attempt_count(usage: JsonObject) -> int:
    return _int_metric(usage.get("provider_attempt_count"))


def _task_failure_from_child_evidence(
    *,
    attempts: list[PaperAttemptResult],
    range_records: list[JsonObject],
) -> tuple[PaperFailureStage, PaperFailureKind]:
    if any(
        attempt.attempt_status == PaperAttemptStatus.MODEL_IDENTITY_MISMATCH
        for attempt in attempts
    ):
        return PaperFailureStage.AUDIT, PaperFailureKind.MODEL_IDENTITY_MISMATCH
    if any(attempt.attempt_status == PaperAttemptStatus.PARSE_FAILED for attempt in attempts):
        return PaperFailureStage.PARSE, PaperFailureKind.PARSE_FAILURE
    if any(
        attempt.attempt_status == PaperAttemptStatus.EXECUTOR_ERROR
        for attempt in attempts
    ):
        return PaperFailureStage.REQUEST, PaperFailureKind.INTERNAL_ERROR
    if any(attempt.attempt_status == PaperAttemptStatus.PROVIDER_ERROR for attempt in attempts):
        return PaperFailureStage.PROVIDER, PaperFailureKind.PROVIDER_ERROR
    if any(
        record["verification"]["status"] == "missing_candidate"
        for record in range_records
    ):
        return PaperFailureStage.REQUEST, PaperFailureKind.INTERNAL_ERROR
    return PaperFailureStage.VERIFICATION, PaperFailureKind.VERIFIER_REJECTED


def _prime_factors_match_oracle(prime_factors: list[JsonObject], case: JsonObject) -> bool:
    return _normalized_factor_list(prime_factors) == _normalized_factor_list(
        case["oracle_prime_factors"]
    )


def _normalized_factor_list(values: list[JsonObject]) -> list[JsonObject]:
    counts: dict[int, int] = {}
    for item in values:
        prime = int(str(item["prime"]))
        counts[prime] = counts.get(prime, 0) + int(item["exponent"])
    return [
        {"prime": str(prime), "exponent": counts[prime]}
        for prime in sorted(counts)
    ]


def _write_case_outputs(root: Path, result: FactorizationPaperRunResult) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "run_manifest.json").write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (root / "per_task_results.jsonl").write_text(
        json.dumps(result.task_result.to_dict(), ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (root / "per_attempt_results.jsonl").write_text(
        "".join(
            json.dumps(item.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"
            for item in result.attempt_results
        ),
        encoding="utf-8",
    )


def _extract_range_fields_from_chat_body(body: JsonObject) -> JsonObject:
    messages = body.get("messages", [])
    if isinstance(messages, list):
        text = "\n".join(
            str(message.get("content", ""))
            for message in messages
            if isinstance(message, dict)
        )
    else:
        text = json.dumps(body, ensure_ascii=False)
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if not isinstance(candidate, dict):
            continue
        required = {
            "target_n",
            "range_start",
            "range_end",
            "coverage_id",
            "child_index",
            "partition_params_digest",
        }
        if required.issubset(candidate):
            return dict(candidate)
    raise ValueError("scripted transport could not find range prompt fields")


def _scripted_range_result(
    range_fields: JsonObject,
    *,
    force_false_negative: bool,
) -> JsonObject:
    target_n = int(str(range_fields["target_n"]))
    range_start = int(str(range_fields["range_start"]))
    range_end = int(str(range_fields["range_end"]))
    divisor = None
    for candidate in range(range_start, range_end + 1):
        if target_n % candidate == 0:
            divisor = candidate
            break
    if divisor is not None and not force_false_negative:
        return {
            "schema_version": RANGE_RESULT_SCHEMA_VERSION,
            "range_result_id": (
                f"range_result:{range_fields['coverage_id']}:{range_fields['child_index']}"
            ),
            "result_kind": RANGE_RESULT_FOUND_FACTOR,
            "target_n": str(target_n),
            "range_start": str(range_start),
            "range_end": str(range_end),
            "coverage_id": str(range_fields["coverage_id"]),
            "child_index": int(range_fields["child_index"]),
            "partition_params_digest": str(range_fields["partition_params_digest"]),
            "found_factor": str(divisor),
            "cofactor": str(target_n // divisor),
            "checked_divisor_count": divisor - range_start + 1,
            "executor_summary": {
                "checked_range": f"{range_start}-{range_end}",
                "scripted_transport": True,
            },
            "created_at": NOW,
        }
    return {
        "schema_version": RANGE_RESULT_SCHEMA_VERSION,
        "range_result_id": (
            f"range_result:{range_fields['coverage_id']}:{range_fields['child_index']}"
        ),
        "result_kind": RANGE_RESULT_NO_FACTOR,
        "target_n": str(target_n),
        "range_start": str(range_start),
        "range_end": str(range_end),
        "coverage_id": str(range_fields["coverage_id"]),
        "child_index": int(range_fields["child_index"]),
        "partition_params_digest": str(range_fields["partition_params_digest"]),
        "found_factor": None,
        "cofactor": None,
        "checked_divisor_count": range_end - range_start + 1,
        "executor_summary": {
            "checked_range": f"{range_start}-{range_end}",
            "scripted_transport": True,
            "forced_false_negative": force_false_negative,
        },
        "created_at": NOW,
    }


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
