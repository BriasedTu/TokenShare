"""Lean paper experiment adapter.

该模块是实验层兼容薄壳：FULL 主路径把 paper catalog case/config 交给 Lean
runtime bridge 和 system coordinator，再从权威 ledger/artifacts 投影旧 paper result。
固定 plan 只由 Lean 插件校验并生成 certificate；AI 只生成 proof candidate。
保留的 selector 直连分支只供历史回归使用，不构成论文生命周期权威。
"""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Lock
from typing import Any, Mapping, Sequence

from tokenshare.core.models import (
    ArtifactRef,
    JsonObject,
    ProtocolConfig,
    TaskState,
    TaskUnit,
)
from tokenshare.local_runtime import (
    ExperimentPrematureMergeAttemptedPayloadV1,
    ProcessWorkerBackend,
    ProtocolExecutionScope,
    ProtocolRunCoordinator,
    ProtocolRunRequest,
    RuntimeHookObservationKind,
    RuntimeHookObservationV1,
    SequentialWorkerBackend,
    ThreadWorkerBackend,
    WorkerTerminationPolicy,
)
from tokenshare.protocol_engine import ProtocolEngine
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
from tokenshare.executors.trace_backed import (
    TraceBackedExecutor,
    TraceBackedParentStager,
    TraceDomainStageContext,
    TraceDomainStageResult,
    TraceSourceBinding,
    bind_trace_execution_request,
)
from tokenshare.experiments.paper_response_bank import PaperTraceRuntimeContext
from tokenshare.experiments.paper_catalog import (
    default_lean_paper_environment_manifest,
    lean_theorem_payload_from_case,
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
from tokenshare.experiments.paper_direct_results import (
    persist_native_online_direct_artifacts,
)
from tokenshare.experiments.paper_projection import project_paper_protocol_run
from tokenshare.experiments.paper_ablation import runtime_controls_for_mode
from tokenshare.experiments.paper_unit_commitments import (
    lean_lemma_graph_plugin_payload,
    lean_simple_plugin_payload,
)
from tokenshare.experiments.paper_runtime_clock import runtime_lifecycle_clock
from tokenshare.local_runtime.logical_scheduler import (
    LOGICAL_SOURCE_LATENCY_1X,
    LogicalSourceLatencyScheduler,
)
from tokenshare.local_runtime.contracts import (
    PreparedTraceDelivery,
    WorkerCompletionSchedule,
)
from tokenshare.experiments.paper_workers import (
    PaperAIUnit,
    project_worker_death_records,
)
from tokenshare.plugins.contracts import OutputContract
from tokenshare.plugins.lean_proof.child_proof import (
    LeanChildProofResult,
    check_lean_child_proof,
)
from tokenshare.plugins.lean_proof.checker import (
    LeanChecker,
    LeanCheckerMode,
    LeanCheckerRequest,
    LeanCheckerStatus,
    check_lean_proof,
)
from tokenshare.plugins.lean_proof.descriptor import build_lean_proof_plugin_descriptor
from tokenshare.plugins.lean_proof.environment import (
    LeanEnvironmentManifest,
    build_lean_environment_ref,
)
from tokenshare.plugins.lean_proof.fixed_plan import (
    LeanFixedDecompositionPlan,
    build_fixed_plan_certificate,
)
from tokenshare.plugins.lean_proof.merge_policy import (
    LeanLemmaGraphMergeResult,
    LeanLemmaGraphProofInput,
    LeanProofMergeInput,
    merge_lean_lemma_graph_proofs,
    merge_lean_child_proofs,
)
from tokenshare.plugins.lean_proof.models import (
    LeanLemmaGraphCertificate,
    LeanTheoremPayload,
    canonical_json_digest,
)
from tokenshare.plugins.lean_proof.prompt_builder import (
    PROOF_CANDIDATE_OUTPUT_NAME,
    build_lean_proof_candidate_prompt_package,
    parse_lean_proof_candidate_ai_output,
)
from tokenshare.plugins.lean_proof.runtime_adapter import (
    LeanExecutionBridge,
    LeanRuntimeAdapter,
    LeanRuntimeSplitBlocked,
)
from tokenshare.plugins.lean_proof.schemas import (
    CHECKER_VALIDATOR_POLICY_ID,
    DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
    LEAN_PROOF_CANDIDATE_SCHEMA_VERSION,
    LEAN_PROOF_LEMMA_NODE_TASK_TYPE,
    LEAN_PROOF_SUBGOAL_TASK_TYPE,
    PLUGIN_ID,
    PLUGIN_VERSION,
    PROOF_ARTIFACT_OUTPUT_NAME,
    PROOF_ARTIFACT_CONTRACT_ID,
    PROOF_CANDIDATE_PARSER_ID,
    schema_ref,
)
from tokenshare.plugins.lean_proof.split_strategy import (
    LeanSplitHelperReport,
    LeanSplitHelperRequest,
    LeanSplitHelperStatus,
    build_lean_split_plan,
    run_lean_split_helper,
)
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType


NOW = "2026-07-14T00:00:00Z"
AI_EXECUTOR_ID = "executor_ai_api"
AI_EXECUTOR_VERSION = "0.1.0"
FAKE_KEY_ENV = "TOKENSHARE_LEAN_PAPER_FAKE_KEY"
LEAN_V2_SCHEMA_VERSION = "tokenshare.paper_lean_lemma_graph_case.v1"
REAL_TRANSPORT_TYPES = (
    UrlLibSiliconFlowTransport,
    UrlLibOpenAITransport,
    UrlLibDeepSeekTransport,
)


@dataclass(frozen=True, kw_only=True)
class LeanPaperRunResult:
    condition: PaperExperimentCondition
    case_id: str
    task_result: PaperTaskResult
    attempt_results: tuple[PaperAttemptResult, ...]
    eligibility_report: PaperEligibilityReport
    split_summary: JsonObject
    child_results: tuple[JsonObject, ...]
    merge_summary: JsonObject
    run_evidence: JsonObject
    output_root: str
    event_records: tuple[JsonObject, ...] = ()
    fault_records: tuple[JsonObject, ...] = ()
    schema_version: str = "tokenshare.lean_paper_run.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "condition": self.condition.to_dict(),
            "case_id": self.case_id,
            "task_result": self.task_result.to_dict(),
            "attempt_results": [item.to_dict() for item in self.attempt_results],
            "eligibility_report": self.eligibility_report.to_dict(),
            "split_summary": dict(self.split_summary),
            "child_results": [dict(item) for item in self.child_results],
            "merge_summary": dict(self.merge_summary),
            "run_evidence": dict(self.run_evidence),
            "output_root": self.output_root,
            "event_records": [dict(item) for item in self.event_records],
            "fault_records": [dict(item) for item in self.fault_records],
        }


class ScriptedLeanPaperProofTransport:
    """测试用 provider transport：仍让 AIAPIExecutor 完整保存 provider evidence。"""

    def __init__(
        self,
        *,
        proof_sources_by_statement: dict[str, str] | None = None,
        model: str = "TokenShare/Scripted-Lean-Paper-Prover",
    ) -> None:
        self.proof_sources_by_statement = dict(proof_sources_by_statement or {})
        self.model = model
        self.calls: list[JsonObject] = []

    def post_chat_completion(
        self,
        *,
        api_key: str,
        body_bytes: bytes,
        normalized_absolute_endpoint: str,
        content_type: str,
        timeout_seconds: int,
    ):
        body = json.loads(body_bytes.decode("utf-8"))
        user_prompt = _user_prompt(body)
        theorem_payload_digest = _prompt_value(
            user_prompt,
            label="Theorem payload digest",
        )
        statement_source = _prompt_value(user_prompt, label="Statement source")
        proof_candidate_id = _expected_proof_candidate_id(user_prompt)
        if statement_source in self.proof_sources_by_statement:
            proof_source = self.proof_sources_by_statement[statement_source]
        else:
            proof_source = _scripted_proof_source(statement_source)
        content = json.dumps(
            {
                "schema_version": LEAN_PROOF_CANDIDATE_SCHEMA_VERSION,
                "proof_candidate_id": proof_candidate_id,
                "theorem_payload_digest": theorem_payload_digest,
                "proof_source": proof_source,
                "created_at": NOW,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        self.calls.append(
            {
                "timeout_seconds": timeout_seconds,
                "api_key_seen": bool(api_key),
                "statement_source": statement_source,
                "body_bytes": body_bytes,
                "normalized_absolute_endpoint": normalized_absolute_endpoint,
                "content_type": content_type,
                "proof_source": proof_source,
            }
        )
        return _ProviderResponse(
            status_code=200,
            body={
                "id": f"lean-paper-scripted-{len(self.calls)}",
                "model": self.model,
                "choices": [
                    {
                        "message": {"content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 60,
                    "completion_tokens": 30,
                    "total_tokens": 90,
                },
            },
        )


class _ProviderResponse:
    def __init__(self, *, status_code: int, body: JsonObject) -> None:
        self.status_code = status_code
        self.body = body
        self.text = json.dumps(body, ensure_ascii=False)


def run_lean_paper_case(
    *,
    case: JsonObject,
    condition: PaperExperimentCondition,
    output_root: str | Path,
    transport: Any | None = None,
    real_transport: bool,
    ai_api_config: AIAPIExecutorConfig | None = None,
    entry_id: str | None = None,
    max_tokens: int = 1024,
    timeout_seconds: int = 30,
    selected_ai_unit_id: str | None = None,
    post_raw_output_hook: Any | None = None,
    ablation_mode: str | None = None,
    worker_termination_policy: WorkerTerminationPolicy | None = None,
    checker: LeanChecker | None = None,
    protocol_run_dispatcher: Any | None = None,
    trace_context: PaperTraceRuntimeContext | None = None,
) -> LeanPaperRunResult:
    """Deprecated compatibility API for historical/selector regressions.

    正常 FULL 调用应经 ``paper_dispatcher.dispatch_paper_case()`` 进入 system
    coordinator；本函数暂时保留旧调用形状，不发出运行时 warning。
    """

    normalized_ablation_mode = _normalize_ablation_mode(ablation_mode)
    active_checker = checker or check_lean_proof
    is_lemma_graph_case = case.get("schema_version") == LEAN_V2_SCHEMA_VERSION
    if is_lemma_graph_case:
        _validate_lean_lemma_graph_case_for_adapter(case)
    else:
        _validate_lean_case_for_adapter(case)
    resolved_entry_id = _resolve_condition_entry_id(condition, entry_id)
    if condition.domain != "lean_proof":
        raise ValueError("condition domain must be lean_proof")
    if condition.difficulty != case["difficulty"]:
        raise ValueError("condition difficulty must match Lean case difficulty")
    paper_metadata = _validated_condition_paper_metadata(
        condition=condition,
        case=case,
    )
    if real_transport and ai_api_config is None:
        raise ValueError("real transport Lean paper runs require ai_api_config")
    _validate_real_transport_mode(
        real_transport=real_transport,
        transport=transport,
        ai_api_config=ai_api_config,
    )
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
    secret_collector = _TransientSecretCollector()
    if selected_ai_unit_id is not None or not (
            is_lemma_graph_case
            and case.get("preflight_status") == "structured_blocked"
    ):
        case_id = str(case["case_id"])
        run_root = Path(output_root) / case_id
        store = ArtifactStore(run_root)
        active_transport = transport
        if active_transport is None:
            active_transport = (
                _default_real_transport(config)
                if real_transport
                else ScriptedLeanPaperProofTransport()
            )
        return _run_lean_full_via_coordinator(
            case=case,
            condition=condition,
            run_root=run_root,
            store=store,
            active_transport=active_transport,
            real_transport=real_transport,
            config=config,
            validated_binding=validated_binding,
            secret_collector=secret_collector,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            post_raw_output_hook=post_raw_output_hook,
            checker=active_checker,
            paper_metadata=paper_metadata,
            protocol_run_dispatcher=protocol_run_dispatcher,
            ablation_mode=normalized_ablation_mode,
            worker_termination_policy=worker_termination_policy,
            selected_ai_unit_id=selected_ai_unit_id,
            trace_context=trace_context,
        )
    # 仅无 selected scope 的历史 structured-blocked reader 保留旧投影；
    # 当前 CLI/dispatcher 的 selected-unit 路径始终在上方进入 coordinator。
    if is_lemma_graph_case:
        return _run_lean_lemma_graph_paper_case(
            case=case,
            condition=condition,
            output_root=output_root,
            transport=transport,
            real_transport=real_transport,
            config=config,
            secret_collector=secret_collector,
            validated_binding=validated_binding,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            selected_ai_unit_id=selected_ai_unit_id,
            post_raw_output_hook=post_raw_output_hook,
            ablation_mode=normalized_ablation_mode,
            checker=active_checker,
        )

    case_id = str(case["case_id"])
    root = Path(output_root)
    run_root = root / case_id
    store = ArtifactStore(run_root)
    ledger = EventLedger(run_root / "events" / "event_log.jsonl")
    active_transport = transport
    if active_transport is None:
        active_transport = (
            _default_real_transport(config)
            if real_transport
            else ScriptedLeanPaperProofTransport()
        )
    environment_manifest = default_lean_paper_environment_manifest()

    parent_payload = lean_theorem_payload_from_case(case)
    parent_payload_ref = _save_parent_payload(store=store, case_id=case_id, payload=parent_payload)
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
    split_summary = _split_summary(split_report)
    if split_report.certificate is None or split_report.certificate.split_kind == "unsupported":
        return _blocked_split_result(
            case=case,
            condition=condition,
            run_root=run_root,
            store=store,
            split_summary=split_summary,
            real_transport=real_transport,
            secret_values=secret_collector.snapshot(),
        )

    split_plan = build_lean_split_plan(
        split_report=split_report,
        artifact_store=store,
        task_id=f"paper_lean_{case_id}",
        parent_unit_id=f"paper_lean_root_{case_id}",
        canonical_selection_id=f"paper_lean_canonical_root_{case_id}",
        canonical_output_bundle_digest=parent_payload_ref.content_hash,
        plugin_descriptor_digest=build_lean_proof_plugin_descriptor().descriptor_digest,
        expansion_scope_hash=canonical_json_digest(
            {
                "case_id": case_id,
                "parent_payload_digest": parent_payload_ref.content_hash,
            }
        ),
        expansion_decision_id=f"paper_lean_expansion_decision_{case_id}",
        created_at=NOW,
    )
    attempts: list[PaperAttemptResult] = []
    child_records: list[JsonObject] = []
    child_proofs: list[LeanProofMergeInput] = []
    slot_by_child_key = {
        str(slot["source_child_logical_key"]): str(slot["slot_key"])
        for slot in split_plan.merge_plan.required_slots
    }
    indexed_children = list(
        enumerate(sorted(split_plan.child_payload_refs_by_logical_key.items()))
    )
    available_ai_unit_ids = tuple(
        f"child_{index}" for index, _child in indexed_children
    )
    if (
        selected_ai_unit_id is not None
        and selected_ai_unit_id not in available_ai_unit_ids
    ):
        raise ValueError("selected_ai_unit_id is not present in Lean split plan")
    if selected_ai_unit_id is not None:
        indexed_children = [
            (index, child)
            for index, child in indexed_children
            if f"child_{index}" == selected_ai_unit_id
        ]
    for index, (child_key, child_payload_ref) in indexed_children:
        child_result = _run_child_attempt(
            case=case,
            condition=condition,
            child_key=child_key,
            child_payload_ref=child_payload_ref,
            split_certificate=split_plan.certificate,
            store=store,
            ledger=ledger,
            config=config,
            validated_binding=validated_binding,
            transport=active_transport,
            paper_eligible_transport=not _is_offline_capturing_transport(
                active_transport
            ),
            index=index,
            timeout_seconds=timeout_seconds,
            max_tokens=max_tokens,
            environment_manifest=environment_manifest,
            post_raw_output_hook=post_raw_output_hook,
            secret_collector=secret_collector,
            ablation_mode=normalized_ablation_mode,
            checker=active_checker,
        )
        attempts.append(child_result["attempt"])
        child_records.append(child_result["record"])
        proof_result = child_result["proof_result"]
        if proof_result is not None and proof_result.merge_ready:
            child_proofs.append(
                LeanProofMergeInput(
                    slot_key=slot_by_child_key[child_key],
                    child_proof=proof_result,
                )
            )
        if child_result["model_identity_mismatch"]:
            break

    if normalized_ablation_mode == "NO_SLOT_INTEGRITY" and child_proofs:
        child_proofs[0] = replace(
            child_proofs[0],
            slot_key=f"ablation_wrong_slot:{child_proofs[0].slot_key}",
        )
    merge_summary = _merge_children_if_ready(
        split_plan=split_plan,
        parent_payload_ref=parent_payload_ref,
        child_proofs=child_proofs,
        store=store,
        environment_manifest=environment_manifest,
        case_id=case_id,
        force=normalized_ablation_mode == "NO_MERGE_GATE",
        checker=active_checker,
    )
    accepted_validity = merge_summary.get("root_checker_accepted") is True
    task_status = (
        PaperTaskStatus.COMPLETED if accepted_validity else PaperTaskStatus.FAILED
    )
    failure_stage = None
    failure_kind = None
    if task_status != PaperTaskStatus.COMPLETED:
        failure_stage, failure_kind = _task_failure_from_child_evidence(
            attempts=attempts,
            child_records=child_records,
            merge_summary=merge_summary,
        )

    run_evidence = _run_evidence(
        store=store,
        real_transport=real_transport,
        transport_kind="ai_api" if real_transport else "scripted",
        secret_values=secret_collector.snapshot(),
        transport=active_transport,
    )
    run_evidence["ablation_runtime"] = _lean_ablation_runtime_evidence(
        mode=normalized_ablation_mode,
        attempts=attempts,
        merge_summary=merge_summary,
        root_validity=accepted_validity,
    )
    eligibility = _evaluate_lean_paper_eligibility(
        attempts=attempts,
        run_evidence=run_evidence,
        checker=active_checker,
    )
    task_result = PaperTaskResult(
        condition_id=condition.condition_id,
        repeat_id=condition.repeat_id,
        task_id=f"paper_lean_{case_id}",
        domain="lean_proof",
        difficulty=str(case["difficulty"]),
        **paper_metadata,
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
        artifact_refs=_task_artifact_refs(attempts=attempts, child_records=child_records, merge_summary=merge_summary),
        paper_eligible=eligibility.paper_eligible,
    )
    result = LeanPaperRunResult(
        condition=condition,
        case_id=case_id,
        task_result=task_result,
        attempt_results=tuple(attempts),
        eligibility_report=eligibility,
        split_summary=split_summary,
        child_results=tuple(child_records),
        merge_summary=merge_summary,
        run_evidence=run_evidence,
        output_root=run_root.as_posix(),
    )
    _write_case_outputs(run_root, result)
    return result


@dataclass(frozen=True, kw_only=True)
class _CapturedLeanCall:
    request: ExecutionRequest
    submission: ExecutionSubmission
    request_ref: ArtifactRef
    usage_ref: ArtifactRef
    model_execution_record: Any | None
    model_execution_record_ref: ArtifactRef | None
    resolved_secret_values: tuple[str, ...] = ()


class _TransientSecretCollector:
    """仅在当前 root 内存中收集实际 resolve 的 key；绝不持久化。"""

    def __init__(self) -> None:
        self._values: list[str] = []
        self._lock = Lock()

    def __repr__(self) -> str:
        return "<_TransientSecretCollector redacted>"

    def __getstate__(self):
        return {"_values": list(self._values)}

    def __setstate__(self, state):
        self._values = list(state.get("_values", ()))
        self._lock = Lock()

    def observe(self, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError("resolved secret must be non-empty")
        with self._lock:
            if value not in self._values:
                self._values.append(value)

    def snapshot(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._values)


@dataclass(frozen=True, kw_only=True)
class _CapturedLeanTraceDelivery:
    request: ExecutionRequest
    delivery: PreparedTraceDelivery


class _TraceLeanExecutorRecorder:
    def __init__(self, executor: TraceBackedExecutor) -> None:
        self._executor = executor
        self.calls: list[_CapturedLeanTraceDelivery] = []
        self._calls_lock = Lock()

    @property
    def provider_call_count(self) -> int:
        return 0

    def __getstate__(self):
        state = dict(self.__dict__)
        state.pop("_calls_lock", None)
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)
        self._calls_lock = Lock()

    def execute(
        self,
        request: ExecutionRequest,
        *,
        submission_id: str,
        submitted_at: str,
    ) -> PreparedTraceDelivery:
        delivery = self._executor.execute(
            request,
            submission_id=submission_id,
            submitted_at=submitted_at,
        )
        with self._calls_lock:
            self.calls.append(_CapturedLeanTraceDelivery(request=request, delivery=delivery))
        return delivery

    def export_process_result(
        self,
        request: ExecutionRequest,
        submission: PreparedTraceDelivery,
    ) -> _CapturedLeanTraceDelivery:
        del submission
        return next(
            item for item in reversed(self.calls)
            if item.request.attempt_id == request.attempt_id
        )

    def ingest_process_result(self, captured: _CapturedLeanTraceDelivery) -> None:
        with self._calls_lock:
            if any(
                item.request.attempt_id == captured.request.attempt_id
                for item in self.calls
            ):
                return
            self.calls.append(captured)

class LeanTraceDomainStage:
    """在 parent 内运行正式 Lean parser、checker 与 canonical promotion。"""

    def __init__(self, plugin_runtime: LeanRuntimeAdapter) -> None:
        self._plugin_runtime = plugin_runtime
        self.requests_by_attempt: dict[str, ExecutionRequest] = {}
        self.deliveries_by_attempt: dict[str, PreparedTraceDelivery] = {}
        self.submissions_by_attempt: dict[str, ExecutionSubmission] = {}

    def stage(self, context: TraceDomainStageContext) -> TraceDomainStageResult:
        self.requests_by_attempt[context.request.attempt_id] = context.request
        self.deliveries_by_attempt[context.request.attempt_id] = context.delivery
        request = context.request
        payload_ref = request.input_artifact_refs.get(
            "lemma_theorem_payload",
            request.input_artifact_refs.get("child_theorem_payload"),
        )
        if payload_ref is None:
            raise ValueError("Lean trace proof request requires theorem payload input")
        theorem_payload = LeanTheoremPayload.from_dict(
            json.loads(context.artifact_store.read_bytes(payload_ref).decode("utf-8"))
        )
        parsed = parse_lean_proof_candidate_ai_output(
            context.parser_input_text,
            theorem_payload=theorem_payload,
            raw_output_ref_summary=context.current_wrapper_ref.to_dict(),
            created_at=context.created_at,
        )
        digest_key = context.delivery.delivery_digest.removeprefix("sha256:")
        source = {
            "kind": "lean_trace_parent_parser",
            "delivery_digest": context.delivery.delivery_digest,
        }
        if not parsed.succeeded:
            failure_ref = context.artifact_store.save_json(
                dict(parsed.parse_failure_artifact_body or {}),
                artifact_id=f"lean_trace_parse_failure_{digest_key}",
                artifact_type="LeanProofParseFailure",
                artifact_schema_id="lean_proof.parse_failure",
                artifact_schema_version="v1",
                source=source,
                metadata={"attempt_id": context.delivery.attempt_id},
                created_at=context.created_at,
            )
            return TraceDomainStageResult(parser_result_ref=failure_ref)
        candidate_body = dict(
            parsed.candidate_output_artifact_bodies[PROOF_CANDIDATE_OUTPUT_NAME]
        )
        candidate_ref = context.artifact_store.save_json(
            candidate_body,
            artifact_id=f"lean_trace_candidate_{digest_key}",
            artifact_type="LeanProofCandidate",
            artifact_schema_id=parsed.parsed_artifact_schema_id,
            artifact_schema_version=parsed.parsed_artifact_schema_version,
            source=source,
            metadata={"attempt_id": context.delivery.attempt_id},
            created_at=context.created_at,
        )
        staged_submission = ExecutionSubmission(
            submission_id=f"trace_parser_submission_{digest_key}",
            request_id=request.request_id,
            task_id=request.task_id,
            unit_id=request.unit_id,
            attempt_id=request.attempt_id,
            lease_id=request.lease_id,
            fencing_token=request.fencing_token,
            executor_id=str(request.executor["executor_id"]),
            executor_version=str(request.executor["executor_version"]),
            result_kind="succeeded",
            raw_output_ref=context.current_wrapper_ref,
            parsed_output_ref=candidate_ref,
            candidate_output_refs={PROOF_CANDIDATE_OUTPUT_NAME: candidate_ref},
            parse_failure_ref=None,
            log_ref=None,
            environment_ref=request.environment_ref,
            environment_summary={"runtime": "trace_backed_parent_parser"},
            provenance_ref=None,
            usage_summary={
                "provider_attempt_count": 0,
                "current_provider_call_count": 0,
                "source_usage_class": "trace_attribution",
            },
            error=None,
            submitted_at=context.created_at,
        )
        normalized = self._plugin_runtime.normalize_proof_submission(
            staged_submission,
            request=request,
        )
        self.submissions_by_attempt[request.attempt_id] = normalized
        report = self._plugin_runtime.checker_report_for_request(request.request_id)
        checker_refs = (
            ()
            if report is None or report.report_ref is None
            else (report.report_ref,)
        )
        canonical_ref = normalized.candidate_output_refs.get(
            PROOF_ARTIFACT_OUTPUT_NAME
        )
        return TraceDomainStageResult(
            parser_result_ref=candidate_ref,
            verifier_checker_refs=checker_refs,
            canonical_ref=canonical_ref,
        )


def bind_lean_trace_request(
    request: ExecutionRequest,
    binding: TraceSourceBinding,
) -> ExecutionRequest:
    """Lean adapter 在 dispatch 前冻结 planned proof unit/source binding。"""

    planned = dict(request.soft_hints or {}).get("planned_ai_unit_id")
    if planned is not None and planned != binding.planned_ai_unit_id:
        raise ValueError("Lean planned AI unit does not match trace binding")
    return bind_trace_execution_request(request, binding)


class LeanTraceRuntimeAdapter:
    """为正式 Lean runtime 的 proof unit 注入预冻结 binding。"""

    def __init__(
        self,
        *,
        plugin_runtime: LeanRuntimeAdapter,
        bindings: Sequence[TraceSourceBinding],
    ) -> None:
        self._plugin_runtime = plugin_runtime
        self._bindings = _lean_trace_bindings_by_planned_unit(bindings)

    def __getattr__(self, name: str):
        return getattr(self._plugin_runtime, name)

    def build_execution_request(self, unit, *, attempt, lease) -> ExecutionRequest:
        request = self._plugin_runtime.build_execution_request(
            unit,
            attempt=attempt,
            lease=lease,
        )
        planned_ai_unit_id = self._plugin_runtime.planned_ai_unit_id(unit)
        if planned_ai_unit_id is None:
            return request
        try:
            binding = self._bindings[planned_ai_unit_id]
        except KeyError as exc:
            raise ValueError(f"no Lean trace binding for {planned_ai_unit_id}") from exc
        return bind_lean_trace_request(
            replace(request, attempt_ordinal=attempt.attempt_ordinal),
            binding,
        )


def _lean_trace_bindings_by_planned_unit(
    bindings: Sequence[TraceSourceBinding],
) -> dict[str, TraceSourceBinding]:
    result: dict[str, TraceSourceBinding] = {}
    for binding in bindings:
        if binding.planned_ai_unit_id in result:
            raise ValueError("Lean trace bindings must have unique planned units")
        result[binding.planned_ai_unit_id] = binding
    if not result:
        raise ValueError("Lean trace bindings must be non-empty")
    return result


class LeanTraceExecutionBridge:
    """Lean proof child 只 prepare trace；root/merge 仍走正式 deterministic bridge。"""

    def __init__(self, *, plugin_runtime: LeanRuntimeAdapter, trace_executor) -> None:
        self._trace_executor = trace_executor
        self._deterministic_bridge = LeanExecutionBridge(
            plugin_runtime=plugin_runtime,
            proof_candidate_executor=trace_executor,
        )

    def execute(self, request, *, submission_id: str, submitted_at: str):
        if str(request.task_unit_snapshot["unit_type"]) in {
            LEAN_PROOF_SUBGOAL_TASK_TYPE,
            LEAN_PROOF_LEMMA_NODE_TASK_TYPE,
        }:
            return self._trace_executor.execute(
                request,
                submission_id=submission_id,
                submitted_at=submitted_at,
            )
        return self._deterministic_bridge.execute(
            request,
            submission_id=submission_id,
            submitted_at=submitted_at,
        )

    def worker_completion_schedule(
        self,
        request: ExecutionRequest,
        submission: ExecutionSubmission | PreparedTraceDelivery | None,
        failure_kind: str | None,
    ) -> WorkerCompletionSchedule | None:
        del failure_kind
        if isinstance(submission, PreparedTraceDelivery):
            return WorkerCompletionSchedule(
                source_latency_ms=submission.source_latency_ms,
                attempt_ordinal=submission.attempt_ordinal,
            )
        return WorkerCompletionSchedule(
            source_latency_ms=0,
            attempt_ordinal=request.attempt_ordinal,
        )

    def export_process_result(
        self,
        request: ExecutionRequest,
        submission: ExecutionSubmission | PreparedTraceDelivery,
    ) -> _CapturedLeanTraceDelivery | None:
        if not isinstance(submission, PreparedTraceDelivery):
            return None
        return self._trace_executor.export_process_result(request, submission)

    def ingest_process_result(
        self,
        captured: _CapturedLeanTraceDelivery | None,
    ) -> None:
        if captured is not None:
            self._trace_executor.ingest_process_result(captured)


class _FixedIdentityLeanExecutor:
    """每个 proof request 绑定 payload parser，并审计固定 model endpoint。"""

    def __init__(
        self,
        *,
        store: ArtifactStore,
        condition: PaperExperimentCondition,
        binding: ValidatedModelEndpointBinding,
        config: AIAPIExecutorConfig,
        executor_requirements: JsonObject,
        case_id: str,
        transport: Any,
        paper_eligible_transport: bool,
        post_raw_output_hook: Any | None,
        secret_collector: _TransientSecretCollector,
    ) -> None:
        self.store = store
        self.condition = condition
        self.binding = binding
        self.config = config
        self.executor_requirements = dict(executor_requirements)
        self.case_id = case_id
        self.transport = transport
        self.paper_eligible_transport = paper_eligible_transport
        self.post_raw_output_hook = post_raw_output_hook
        self.secret_collector = secret_collector
        self.calls: list[_CapturedLeanCall] = []
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
    ) -> _CapturedLeanCall:
        del submission
        return next(
            call
            for call in reversed(self.calls)
            if call.request.attempt_id == request.attempt_id
        )

    def ingest_process_result(self, captured: _CapturedLeanCall) -> None:
        for secret in captured.resolved_secret_values:
            self.secret_collector.observe(secret)
        with self._calls_lock:
            if any(
                call.request.attempt_id == captured.request.attempt_id
                for call in self.calls
            ):
                return
            self.calls.append(captured)

    def execute(
        self,
        request: ExecutionRequest,
        *,
        submission_id: str,
        submitted_at: str,
    ) -> ExecutionSubmission:
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
        mismatches = _lean_executor_requirement_mismatches(
            request=request,
            expected=self.executor_requirements,
        )
        if mismatches:
            submission = ExecutionSubmission(
                submission_id=submission_id,
                request_id=request.request_id,
                task_id=request.task_id,
                unit_id=request.unit_id,
                attempt_id=request.attempt_id,
                lease_id=request.lease_id,
                fencing_token=request.fencing_token,
                executor_id=AI_EXECUTOR_ID,
                executor_version=AI_EXECUTOR_VERSION,
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
                    "reasons": mismatches,
                },
                submitted_at=submitted_at,
            )
        else:
            payload_ref = request.input_artifact_refs.get(
                "lemma_theorem_payload",
                request.input_artifact_refs.get("child_theorem_payload"),
            )
            if payload_ref is None:
                raise ValueError("Lean proof request requires theorem payload input")
            payload = LeanTheoremPayload.from_dict(
                _read_json_ref(self.store, payload_ref)
            )
            executor = AIAPIExecutor(
                executor_id=AI_EXECUTOR_ID,
                executor_version=AI_EXECUTOR_VERSION,
                artifact_store=self.store,
                config=self.config,
                transport=self.transport,
                parser=lambda raw, *, raw_output_ref_summary, created_at: parse_lean_proof_candidate_ai_output(
                    raw,
                    theorem_payload=payload,
                    raw_output_ref_summary=raw_output_ref_summary,
                    created_at=created_at,
                ),
                post_raw_output_hook=self.post_raw_output_hook,
                resolved_secret_observer=self.secret_collector.observe,
            )
            submission = executor.execute(
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
        if mismatches:
            model_record = None
            model_record_ref = None
        else:
            model_record, model_record_ref = _save_model_execution_record(
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
                _CapturedLeanCall(
                    request=request,
                    submission=submission,
                    request_ref=request_ref,
                    usage_ref=usage_ref,
                    model_execution_record=model_record,
                    model_execution_record_ref=model_record_ref,
                    resolved_secret_values=self.secret_collector.snapshot(),
                )
            )
        return submission


def _trace_lean_calls_from_events(
    *,
    requests: Mapping[str, ExecutionRequest],
    deliveries: Mapping[str, PreparedTraceDelivery],
    staged_submissions: Mapping[str, ExecutionSubmission],
    runtime_events: Sequence[Any],
    request_refs_by_id: dict[str, ArtifactRef],
    store: ArtifactStore,
) -> list[_CapturedLeanCall]:
    committed_attempt_ids = [
        str(event.payload["attempt_id"])
        for event in runtime_events
        if event.event_type == "TRACE_DELIVERY_COMMITTED.v1"
    ]
    result: list[_CapturedLeanCall] = []
    for attempt_id in committed_attempt_ids:
        request = requests[attempt_id]
        delivery = deliveries[attempt_id]
        submission = staged_submissions[attempt_id]
        submission = replace(
            submission,
            usage_summary={
                **dict(submission.usage_summary or {}),
                "entry_id": delivery.entry_id,
                "current_provider_call_count": 0,
            },
        )
        usage_ref = store.save_json(
            dict(submission.usage_summary or {}),
            artifact_id=f"trace_current_usage_{request.attempt_id}",
            artifact_type="TraceCurrentUsage",
            artifact_schema_id="tokenshare.trace_current_usage",
            artifact_schema_version="v1",
            source={"kind": "trace_protocol_projection"},
            metadata={"attempt_id": request.attempt_id},
            created_at=submission.submitted_at,
        )
        result.append(
            _CapturedLeanCall(
                request=request,
                submission=submission,
                request_ref=request_refs_by_id[request.request_id],
                usage_ref=usage_ref,
                model_execution_record=None,
                model_execution_record_ref=None,
            )
        )
    return result


def _run_lean_full_via_coordinator(
    *,
    case: JsonObject,
    condition: PaperExperimentCondition,
    run_root: Path,
    store: ArtifactStore,
    active_transport: Any,
    real_transport: bool,
    config: AIAPIExecutorConfig,
    validated_binding: ValidatedModelEndpointBinding,
    secret_collector: _TransientSecretCollector,
    max_tokens: int,
    timeout_seconds: int,
    post_raw_output_hook: Any | None,
    checker: LeanChecker,
    paper_metadata: JsonObject,
    protocol_run_dispatcher: Any | None,
    ablation_mode: str,
    worker_termination_policy: WorkerTerminationPolicy | None,
    selected_ai_unit_id: str | None,
    trace_context: PaperTraceRuntimeContext | None,
) -> LeanPaperRunResult:
    """FULL 兼容壳：配置 Lean runtime，调用 coordinator，再投影旧 result。"""

    case_id = str(case["case_id"])
    task_id = f"paper_lean_{case_id}"
    ledger = EventLedger(run_root / "events" / "event_log.jsonl")
    protocol_config = replace(
        ProtocolConfig.default(
            config_id=f"lean_runtime_{case_id}",
            artifact_store_uri="file://artifacts",
            event_log_uri="file://events/event_log.jsonl",
            metadata={"paper_lean": True, "case_id": case_id},
        ),
        max_retries=(
            worker_termination_policy.termination_limit + 1
            if worker_termination_policy is not None
            else max(
                len(binding.replacements) - 1
                for binding in trace_context.bindings
            )
            if trace_context is not None
            else 1
            if condition.experiment_id == "exp4_real_ai_protocol_ablation"
            else 2
            if post_raw_output_hook is not None
            else 0
        ),
    )
    environment_manifest = default_lean_paper_environment_manifest()
    executor_requirements = _lean_fixed_entry_executor_requirements(
        config=config,
        binding=validated_binding,
    )
    lifecycle_clock = runtime_lifecycle_clock(
        real_transport=real_transport,
        transport=active_transport,
        deterministic_now=NOW,
        provider_family=config.provider_family,
        trace_delay_policy=(
            LOGICAL_SOURCE_LATENCY_1X if trace_context is not None else None
        ),
    )
    plugin_runtime = LeanRuntimeAdapter(
        provider_family=config.provider_family,
        environment_manifest=environment_manifest,
        checker=checker,
        seed=condition.seed,
        protocol_config=protocol_config,
        executor_requirements=executor_requirements,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        created_at=lifecycle_clock(),
        lifecycle_clock=lifecycle_clock,
    )
    trace_scheduler = None
    if trace_context is None:
        capturing_executor = _FixedIdentityLeanExecutor(
            store=store,
            condition=condition,
            binding=validated_binding,
            config=config,
            executor_requirements=executor_requirements,
            case_id=case_id,
            transport=active_transport,
            paper_eligible_transport=not _is_offline_capturing_transport(
                active_transport
            ),
            post_raw_output_hook=post_raw_output_hook,
            secret_collector=secret_collector,
        )
        runtime_adapter = plugin_runtime
        execution_bridge = LeanExecutionBridge(
            plugin_runtime=plugin_runtime,
            proof_candidate_executor=capturing_executor,
        )
        trace_delivery_stager = None
    else:
        trace_scheduler = LogicalSourceLatencyScheduler(start_ms=0)
        trace_scheduler.bind_wall_clock_origin(NOW)
        runtime_adapter = LeanTraceRuntimeAdapter(
            plugin_runtime=plugin_runtime,
            bindings=trace_context.bindings,
        )
        trace_executor = _TraceLeanExecutorRecorder(
            TraceBackedExecutor(
                resolver=trace_context.resolver,
                bindings=trace_context.bindings,
                current_run_id=f"{condition.condition_id}_{case_id}",
            )
        )
        capturing_executor = trace_executor
        execution_bridge = LeanTraceExecutionBridge(
            plugin_runtime=plugin_runtime,
            trace_executor=trace_executor,
        )
        trace_domain_stage = LeanTraceDomainStage(plugin_runtime)
        trace_delivery_stager = TraceBackedParentStager(
            resolver=trace_context.resolver,
            bindings=trace_context.bindings,
            domain_stage=trace_domain_stage,
        )
    coordinator = ProtocolRunCoordinator(
        engine=ProtocolEngine(
            event_ledger=ledger,
            protocol_config=protocol_config,
            artifact_store=store,
        ),
        artifact_store=store,
        event_ledger=ledger,
        now=lifecycle_clock,
        observation_clock=lifecycle_clock,
        trace_delivery_stager=trace_delivery_stager,
    )
    controls = runtime_controls_for_mode(ablation_mode)
    worker_backend = (
        ProcessWorkerBackend(
            executor=execution_bridge,
            capacity=condition.worker_count,
            submitted_at=lifecycle_clock,
            termination_policy=worker_termination_policy,
        )
        if worker_termination_policy is not None
        else
        SequentialWorkerBackend(
            executor=execution_bridge,
            submitted_at=lifecycle_clock,
        )
        if condition.worker_count == 1
        else ThreadWorkerBackend(
            executor=execution_bridge,
            capacity=condition.worker_count,
            submitted_at=lifecycle_clock,
        )
    )
    protocol_request = ProtocolRunRequest(
        run_id=f"{condition.condition_id}_{case_id}",
        root_input=case,
        plugin_runtime=runtime_adapter,
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
        trace_delay_policy=(
            LOGICAL_SOURCE_LATENCY_1X if trace_context is not None else None
        ),
        logical_scheduler=trace_scheduler,
    )
    try:
        runtime_result = (
            coordinator.run_root(protocol_request)
            if protocol_run_dispatcher is None
            else protocol_run_dispatcher(
                coordinator=coordinator,
                request=protocol_request,
            )
        )
    except LeanRuntimeSplitBlocked as exc:
        return _blocked_split_result(
            case=case,
            condition=condition,
            run_root=run_root,
            store=store,
            split_summary=_split_summary(exc.split_report),
            real_transport=real_transport,
            secret_values=secret_collector.snapshot(),
        )

    split_plan = plugin_runtime.planned_split_plan
    certificate = split_plan.certificate
    certificate_ref = ArtifactRef.from_dict(
        split_plan.proposal.promotion_guard_evidence[
            "lean_split_certificate_ref"
        ]
    )
    runtime_events = ledger.read_all()
    request_refs_by_id = {
        str(event.payload["request_id"]): ArtifactRef.from_dict(
            event.payload["request_ref"]
        )
        for event in runtime_events
        if event.event_type == "EXECUTION_REQUEST_RECORDED"
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
    canonical_refs_by_attempt = {
        str(event.payload["selected_attempt_id"]): {
            name: ArtifactRef.from_dict(ref)
            for name, ref in event.payload["canonical_output_refs"].items()
        }
        for event in runtime_events
        if event.event_type == "CANONICAL_OUTPUTS_BOUND"
        and isinstance(event.payload.get("selected_attempt_id"), str)
    }
    captured_calls = (
        _trace_lean_calls_from_events(
            requests=trace_domain_stage.requests_by_attempt,
            deliveries=trace_domain_stage.deliveries_by_attempt,
            staged_submissions=trace_domain_stage.submissions_by_attempt,
            runtime_events=runtime_events,
            request_refs_by_id=request_refs_by_id,
            store=store,
        )
        if trace_context is not None
        else list(capturing_executor.calls)
    )
    attempts: list[PaperAttemptResult] = []
    independent_validity_by_attempt: dict[str, bool] = {}
    projection_metadata_by_unit: dict[str, JsonObject] = {}
    child_records: list[JsonObject] = []
    nodes_by_id = (
        {str(node["node_id"]): node for node in case["lemma_graph"]["nodes"]}
        if isinstance(certificate, LeanLemmaGraphCertificate)
        else {}
    )
    for index, captured in enumerate(captured_calls):
        request = captured.request
        submission = captured.submission
        logical_key = str(
            request.task_unit_snapshot["metadata"]["child_logical_key"]
        )
        checker_report = plugin_runtime.checker_report_for_request(
            request.request_id
        )
        model_mismatch = (
            captured.model_execution_record is not None
            and captured.model_execution_record.identity_status
            == "model_identity_mismatch"
        )
        attempt_status = _attempt_status_from_submission(submission.result_kind)
        if model_mismatch:
            attempt_status = PaperAttemptStatus.MODEL_IDENTITY_MISMATCH
        elif (
            checker_report is not None
            and checker_report.status != LeanCheckerStatus.ACCEPTED
        ):
            attempt_status = PaperAttemptStatus.CHECKER_REJECTED
        planned_ai_unit_id = str(request.soft_hints["planned_ai_unit_id"])
        projection_metadata_by_unit[request.unit_id] = {
            "planned_ai_unit_id": planned_ai_unit_id,
            "lemma_node_id": (
                logical_key
                if isinstance(certificate, LeanLemmaGraphCertificate)
                else None
            ),
            "slot_key": (
                f"{logical_key}:{PROOF_ARTIFACT_OUTPUT_NAME}"
                if isinstance(certificate, LeanLemmaGraphCertificate)
                else None
            ),
            "dependency_path": list(
                request.soft_hints.get("dependency_path", [])
            ),
        }
        attempts.append(
            _paper_attempt_result(
                store=store,
                condition=condition,
                case_id=case_id,
                index=index,
                request=request,
                request_ref=captured.request_ref,
                submission=submission,
                usage_ref=captured.usage_ref,
                attempt_status=attempt_status,
                planned_ai_unit_id=planned_ai_unit_id,
                **paper_metadata,
                lemma_node_id=(
                    logical_key
                    if isinstance(certificate, LeanLemmaGraphCertificate)
                    else None
                ),
                slot_key=(
                    f"{logical_key}:{PROOF_ARTIFACT_OUTPUT_NAME}"
                    if isinstance(certificate, LeanLemmaGraphCertificate)
                    else None
                ),
                dependency_path=list(
                    request.soft_hints.get("dependency_path", [])
                ),
                model_execution_record_ref=(
                    captured.model_execution_record_ref
                ),
                error_kind_override=(
                    "model_identity_mismatch" if model_mismatch else None
                ),
            )
        )
        protocol_candidate_refs = submitted_candidate_refs_by_attempt.get(
            request.attempt_id,
            submission.candidate_output_refs,
        )
        proof_candidate_ref = protocol_candidate_refs.get(
            PROOF_CANDIDATE_OUTPUT_NAME
        ) or protocol_candidate_refs.get(PROOF_ARTIFACT_OUTPUT_NAME)
        protocol_submission = replace(
            submission,
            candidate_output_refs=dict(protocol_candidate_refs),
        )
        payload_name = (
            "lemma_theorem_payload"
            if isinstance(certificate, LeanLemmaGraphCertificate)
            else "child_theorem_payload"
        )
        payload_ref = request.input_artifact_refs[payload_name]
        payload = LeanTheoremPayload.from_dict(_read_json_ref(store, payload_ref))
        if isinstance(certificate, LeanLemmaGraphCertificate):
            node = nodes_by_id[logical_key]
            record = _lemma_node_record(
                case=case,
                certificate=certificate,
                node=node,
                slot_key=f"{logical_key}:{PROOF_ARTIFACT_OUTPUT_NAME}",
                dependency_path=list(
                    request.soft_hints.get("dependency_path", [])
                ),
                node_payload=payload,
                node_payload_ref=payload_ref,
                submission=protocol_submission,
                proof_candidate_ref=proof_candidate_ref,
                checker_report=checker_report,
            )
        else:
            proof_input = plugin_runtime.proof_input_for_logical_key(logical_key)
            record = _child_record(
                child_key=logical_key,
                child_payload=payload,
                child_payload_ref=payload_ref,
                submission=protocol_submission,
                proof_candidate_ref=proof_candidate_ref,
                proof_result=(
                    proof_input
                    if isinstance(proof_input, LeanChildProofResult)
                    else None
                ),
            )
        independent_validity_by_attempt[request.attempt_id] = bool(
            record["checker"]["accepted"]
        )
        record["model_execution_record_ref"] = (
            captured.model_execution_record_ref.to_dict()
            if captured.model_execution_record_ref is not None
            else None
        )
        record["model_identity_status"] = (
            captured.model_execution_record.identity_status
            if captured.model_execution_record is not None
            else None
        )
        child_records.append(record)

    merge_summary = _runtime_lean_merge_summary(
        plugin_runtime=plugin_runtime,
        split_plan=split_plan,
        runtime_status=runtime_result.status,
    )
    is_partial = runtime_result.status == "partial"
    if is_partial:
        merge_summary = {**merge_summary, "status": "partial"}
    accepted_validity = (
        None
        if is_partial
        else merge_summary.get("root_checker_accepted") is True
    )
    split_summary = _runtime_lean_split_summary(
        case=case,
        split_plan=split_plan,
        certificate_ref=certificate_ref,
    )
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
        actual_unit_id_by_planned = {
            str(captured.request.soft_hints["planned_ai_unit_id"]): (
                captured.request.unit_id
            )
            for captured in captured_calls
        }
        dependency_sources_by_planned: dict[str, tuple[str, ...]] = {}
        if isinstance(certificate, LeanLemmaGraphCertificate):
            for planned_ai_unit_id in actual_unit_id_by_planned:
                dependency_sources_by_planned[planned_ai_unit_id] = tuple(
                    str(edge["source_node_id"])
                    for edge in certificate.dependency_edges
                    if str(edge["target_node_id"]) == planned_ai_unit_id
                )
        depth_by_planned: dict[str, int] = {}

        def planned_depth(planned_ai_unit_id: str) -> int:
            if planned_ai_unit_id in depth_by_planned:
                return depth_by_planned[planned_ai_unit_id]
            sources = dependency_sources_by_planned.get(
                planned_ai_unit_id, ()
            )
            depth = (
                max(planned_depth(source) for source in sources) + 1
                if sources
                else 0
            )
            depth_by_planned[planned_ai_unit_id] = depth
            return depth

        ai_units_by_id: dict[str, PaperAIUnit] = {}
        provider_tokens_by_attempt_id: dict[str, int] = {}
        for captured in captured_calls:
            request = captured.request
            planned_ai_unit_id = str(
                request.soft_hints["planned_ai_unit_id"]
            )
            dependency_path = tuple(
                str(item)
                for item in request.soft_hints.get("dependency_path", ())
            )
            ai_units_by_id[request.unit_id] = PaperAIUnit(
                task_id=request.task_id,
                unit_id=request.unit_id,
                unit_kind=(
                    "lean_lemma_node"
                    if isinstance(certificate, LeanLemmaGraphCertificate)
                    else "lean_subgoal"
                ),
                dependencies=tuple(
                    actual_unit_id_by_planned[source]
                    for source in dependency_sources_by_planned.get(
                        planned_ai_unit_id, ()
                    )
                ),
                depth=planned_depth(planned_ai_unit_id),
                domain="lean_proof",
                metadata={
                    "planned_ai_unit_id": planned_ai_unit_id,
                    "lemma_node_id": request.soft_hints.get("lemma_node_id"),
                    "dependency_path": list(dependency_path),
                },
            )
            provider_tokens_by_attempt_id[request.attempt_id] = _int_metric(
                (captured.submission.usage_summary or {}).get("total_tokens")
            )
        missing_targets = [
            planned
            for planned in worker_termination_policy.target_planned_ai_unit_ids
            if planned not in actual_unit_id_by_planned
        ]
        if missing_targets:
            raise ValueError(
                f"worker-death targets were not dispatched: {missing_targets}"
            )
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
            worker_facts=_lean_worker_death_projection_facts(
                worker_backend.execution_facts,
                runtime_events,
            ),
            protocol_events=runtime_events,
            coordinator_pid=os.getpid(),
            created_at=NOW,
            provider_tokens_by_attempt_id=provider_tokens_by_attempt_id,
        )
        attempts = _enrich_lean_worker_death_attempts(
            attempts=attempts,
            captured_calls=captured_calls,
            worker_facts=worker_backend.execution_facts,
            fault_records=fault_records,
            store=store,
            condition=condition,
            case_id=case_id,
            paper_metadata=paper_metadata,
        )
    run_evidence = _run_evidence(
        store=store,
        real_transport=real_transport,
        transport_kind="ai_api" if real_transport else "scripted",
        secret_values=secret_collector.snapshot(),
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
        "execution_scope": protocol_request.execution_scope.mode,
        "selected_ai_unit_ids": list(
            protocol_request.execution_scope.selected_ai_unit_ids
        ),
    }
    if isinstance(certificate, LeanLemmaGraphCertificate):
        run_evidence["lean_lemma_graph"] = _lemma_graph_run_metadata(
            case=case,
            certificate=certificate,
            certificate_ref=certificate_ref,
            split_plan=split_plan,
        )
    run_evidence["ablation_runtime"] = _lean_ablation_runtime_evidence(
        mode=ablation_mode,
        attempts=attempts,
        merge_summary=merge_summary,
        root_validity=accepted_validity,
        max_retries=protocol_config.max_retries,
        submitted_candidate_refs_by_attempt=submitted_candidate_refs_by_attempt,
        canonical_refs_by_attempt=canonical_refs_by_attempt,
        independent_validity_by_attempt=independent_validity_by_attempt,
        hook_observations=_parse_runtime_hook_observations(
            runtime_result.summary.get("runtime_hook_observations", ())
        ),
    )
    final_value = merge_summary.get("merge_result_ref")
    checker_value = merge_summary.get("root_checker_report_ref")
    if attempts:
        completed_direct = (
            isinstance(final_value, Mapping)
            and isinstance(checker_value, Mapping)
            and accepted_validity is not None
        )
        direct_bundle = persist_native_online_direct_artifacts(
            artifact_store=store,
            execution_id=runtime_result.run_id,
            task_id=runtime_result.task_id,
            root_unit_id=runtime_result.root_unit_id,
            final_result_ref=(
                ArtifactRef.from_dict(final_value) if completed_direct else None
            ),
            oracle_verdict_ref=(
                ArtifactRef.from_dict(checker_value) if completed_direct else None
            ),
            oracle_kind="lean_checker" if completed_direct else None,
            oracle_correct=accepted_validity if completed_direct else None,
            oracle_fact=(
                {
                    "case_id": case_id,
                    "root_checker_accepted": accepted_validity,
                }
                if completed_direct
                else None
            ),
            current_provider_object_refs=(
                _native_online_provider_sources(attempts)
                if real_transport
                else None
            ),
            parser_object_refs=_native_parser_sources(attempts),
        )
        run_evidence["paper_direct_native_artifacts"] = direct_bundle.to_dict()
    eligibility = _evaluate_lean_paper_eligibility(
        attempts=attempts,
        run_evidence=run_evidence,
        checker=checker,
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
    result = LeanPaperRunResult(
        condition=condition,
        case_id=case_id,
        task_result=task_result,
        attempt_results=tuple(attempts),
        eligibility_report=eligibility,
        split_summary=split_summary,
        child_results=tuple(child_records),
        merge_summary=merge_summary,
        run_evidence=run_evidence,
        output_root=run_root.as_posix(),
        event_records=tuple(event.to_dict() for event in runtime_events),
        fault_records=fault_records,
    )
    _write_case_outputs(run_root, result)
    return result


def _native_online_provider_sources(
    attempts: Sequence[PaperAttemptResult],
) -> dict[str, tuple[ArtifactRef, ...]]:
    """把 native executor refs 分组交给唯一 direct builder。"""

    grouped: dict[str, list[ArtifactRef]] = {
        role: []
        for role in (
            "request_body",
            "raw_output_or_provider_failure",
            "provenance",
            "usage_status",
            "latency",
            "pricing",
            "provider_attempt",
            "model_record",
        )
    }
    for attempt in attempts:
        request_ref = ArtifactRef.from_dict(attempt.request_ref)
        provenance_ref = ArtifactRef.from_dict(attempt.provenance_ref)
        usage_ref = ArtifactRef.from_dict(attempt.usage_ref)
        raw_value = (
            attempt.raw_output_ref
            or attempt.parse_failure_ref
            or attempt.provenance_ref
        )
        values = {
            "request_body": request_ref,
            "raw_output_or_provider_failure": ArtifactRef.from_dict(raw_value),
            "provenance": provenance_ref,
            "usage_status": usage_ref,
            "latency": provenance_ref,
            "pricing": usage_ref,
            "provider_attempt": provenance_ref,
            "model_record": ArtifactRef.from_dict(
                attempt.model_execution_record_ref or attempt.usage_ref
            ),
        }
        for role, ref in values.items():
            grouped[role].append(ref)
    if any(not values for values in grouped.values()):
        raise ValueError("native online provider evidence is incomplete")
    return {role: tuple(values) for role, values in grouped.items()}


def _native_parser_sources(
    attempts: Sequence[PaperAttemptResult],
) -> dict[str, tuple[ArtifactRef, ...]]:
    """把 parser 既有事实按 direct ABI 角色投影，不重新解析。"""

    grouped: dict[str, list[ArtifactRef]] = {
        "parser_result": [],
        "parse_failure": [],
    }
    for attempt in attempts:
        for role, value in (
            ("parser_result", attempt.parsed_output_ref),
            ("parse_failure", attempt.parse_failure_ref),
        ):
            if value is not None:
                grouped[role].append(ArtifactRef.from_dict(value))
    return {
        role: tuple(values)
        for role, values in grouped.items()
        if values
    }


def _lean_worker_death_projection_facts(
    worker_facts: Sequence[Any],
    runtime_events: Sequence[Any],
) -> tuple[JsonObject, ...]:
    """只把 parent 已提交的 prepared delivery 投影为 replacement 成功。"""

    committed_attempt_ids = {
        str(event.payload["attempt_id"])
        for event in runtime_events
        if event.event_type == "TRACE_DELIVERY_COMMITTED.v1"
    }
    result: list[JsonObject] = []
    for fact in worker_facts:
        body = dict(fact.to_dict())
        if (
            body.get("result_kind") == "prepared_trace_delivery"
            and str(body.get("attempt_id")) in committed_attempt_ids
        ):
            body["result_kind"] = "succeeded"
        result.append(body)
    return tuple(result)


def _enrich_lean_worker_death_attempts(
    *,
    attempts: list[PaperAttemptResult],
    captured_calls: list[_CapturedLeanCall],
    worker_facts: tuple[Any, ...],
    fault_records: tuple[JsonObject, ...],
    store: ArtifactStore,
    condition: PaperExperimentCondition,
    case_id: str,
    paper_metadata: JsonObject,
) -> list[PaperAttemptResult]:
    """用子进程 sidecar 补齐未进入 submission ledger 的死亡 proof attempt。"""

    killed_facts = {
        str(fact.attempt_id): fact
        for fact in worker_facts
        if fact.result_kind == "worker_terminated" and fact.attempt_id
    }
    calls_by_attempt = {
        captured.request.attempt_id: captured for captured in captured_calls
    }
    planned_by_unit = {
        captured.request.unit_id: str(
            captured.request.soft_hints["planned_ai_unit_id"]
        )
        for captured in captured_calls
        if isinstance(
            captured.request.soft_hints.get("planned_ai_unit_id"),
            str,
        )
    }
    record_ref_by_attempt = {
        str(record["dead_attempt"]["attempt_id"]): dict(record["record_ref"])
        for record in fault_records
    }
    enriched: list[PaperAttemptResult] = []
    for attempt in attempts:
        fact = killed_facts.get(attempt.attempt_id)
        captured = calls_by_attempt.get(attempt.attempt_id)
        if fact is None:
            if captured is None:
                enriched.append(attempt)
            else:
                usage = dict(captured.submission.usage_summary or {})
                enriched.append(
                    replace(
                        attempt,
                        entry_id=str(usage.get("entry_id") or "unknown"),
                        planned_ai_unit_id=planned_by_unit.get(attempt.unit_id),
                    )
                )
            continue
        if captured is None:
            enriched.append(
                replace(
                    attempt,
                    worker_id=str(fact.worker_id or attempt.worker_id),
                    attempt_status=PaperAttemptStatus.WORKER_DIED,
                    started_at=str(fact.started_at or attempt.started_at),
                    ended_at=str(fact.ended_at or attempt.ended_at),
                    error_kind="worker_died",
                    fault_injection_ref=record_ref_by_attempt.get(
                        attempt.attempt_id
                    ),
                    paper_eligible=False,
                    planned_ai_unit_id=planned_by_unit.get(attempt.unit_id),
                )
            )
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
            paper_difficulty=paper_metadata.get("paper_difficulty"),
            topic_family=paper_metadata.get("topic_family"),
            topic_family_version=paper_metadata.get("topic_family_version"),
            construction_rule_id=paper_metadata.get("construction_rule_id"),
            oracle_package_group=paper_metadata.get("oracle_package_group"),
            proof_assembly_shape=paper_metadata.get("proof_assembly_shape"),
            lemma_node_id=attempt.lemma_node_id,
            slot_key=attempt.slot_key,
            dependency_path=list(attempt.dependency_path or ()),
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
                cost_estimate_status=captured_attempt.cost_estimate_status,
                error_kind="worker_died",
                fault_injection_ref=record_ref_by_attempt.get(attempt.attempt_id),
                paper_eligible=model_record_eligible,
                model_execution_record_ref=(
                    captured_attempt.model_execution_record_ref
                ),
                planned_ai_unit_id=planned_ai_unit_id,
                schema_version=captured_attempt.schema_version,
            )
        )
    return enriched


def _runtime_lean_merge_summary(
    *,
    plugin_runtime: LeanRuntimeAdapter,
    split_plan,
    runtime_status: str,
) -> JsonObject:
    if runtime_status != "completed":
        return {
            "status": "blocked",
            "required_slot_count": len(split_plan.merge_plan.required_slots),
            "merge_ready_child_count": sum(
                plugin_runtime.proof_input_for_logical_key(str(key)) is not None
                for key in split_plan.child_unit_ids_by_logical_key
            ),
            "slot_integrity_violation": (
                plugin_runtime.slot_integrity_violation_applied
            ),
            "slot_binding_applied_by": (
                "local_runtime_policy"
                if plugin_runtime.slot_integrity_violation_applied
                else None
            ),
        }
    result = plugin_runtime.merge_result
    common = {
        "status": "completed" if result.accepted else "failed",
        "root_checker_accepted": result.accepted,
        "environment_digest": (
            result.root_checker_report.environment_ref.environment_digest
        ),
        "merge_result_ref": (
            result.merge_result_ref.to_dict()
            if result.merge_result_ref is not None
            else None
        ),
        "root_checker_report_ref": (
            result.root_checker_report.report_ref.to_dict()
            if result.root_checker_report.report_ref is not None
            else None
        ),
        "root_proof_artifact_ref": (
            result.root_proof_artifact_ref.to_dict()
            if result.root_proof_artifact_ref is not None
            else None
        ),
        "slot_integrity_violation": (
            plugin_runtime.slot_integrity_violation_applied
        ),
        "slot_binding_applied_by": (
            "local_runtime_policy"
            if plugin_runtime.slot_integrity_violation_applied
            else None
        ),
    }
    if isinstance(result, LeanLemmaGraphMergeResult):
        return {
            **common,
            "root_proof_candidate_ref": result.root_proof_candidate_ref.to_dict(),
            "node_proof_refs": {
                key: ref.to_dict()
                for key, ref in sorted(result.node_proof_refs.items())
            },
        }
    return {
        **common,
        "merge_rule_id": result.merge_rule_id,
        "merge_proof_candidate_ref": result.merge_proof_candidate_ref.to_dict(),
        "child_proof_refs": {
            key: ref.to_dict()
            for key, ref in sorted(result.child_proof_refs.items())
        },
    }


def _runtime_lean_split_summary(
    *,
    case: JsonObject,
    split_plan,
    certificate_ref: ArtifactRef,
) -> JsonObject:
    certificate = split_plan.certificate
    if isinstance(certificate, LeanLemmaGraphCertificate):
        return _lemma_graph_split_summary(
            case=case,
            certificate=certificate,
            certificate_ref=certificate_ref,
            split_plan=split_plan,
        )
    return {
        "split_status": "succeeded",
        "split_rule_id": certificate.rule_id,
        "split_kind": certificate.split_kind,
        "child_count": len(certificate.child_goals),
        "certificate_ref": certificate_ref.to_dict(),
        "report_ref": None,
        "proposal_digest": split_plan.proposal.proposal_header["proposal_digest"],
        "merge_plan_digest": split_plan.merge_plan.merge_plan_header[
            "merge_plan_digest"
        ],
    }


def _lean_fixed_entry_executor_requirements(
    *,
    config: AIAPIExecutorConfig,
    binding: ValidatedModelEndpointBinding | None,
) -> JsonObject:
    return build_fixed_entry_executor_requirements(
        config=config,
        binding=binding,
    )


def _lean_executor_requirement_mismatches(
    *,
    request: ExecutionRequest,
    expected: JsonObject,
) -> list[str]:
    mismatches = [
        f"hard_requirement:{name}"
        for name, expected_value in expected.items()
        if request.hard_requirements.get(name) != expected_value
    ]
    if request.executor.get("executor_id") != AI_EXECUTOR_ID:
        mismatches.append("executor_id")
    if request.executor.get("executor_version") != AI_EXECUTOR_VERSION:
        mismatches.append("executor_version")
    return mismatches


def build_lean_lemma_graph_oracle_evidence(
    *,
    case: JsonObject,
    output_root: str | Path | None = None,
    checker: LeanChecker | None = None,
) -> JsonObject:
    """Run a checker-backed lemma graph through local oracle split/merge evidence."""

    active_checker = checker or check_lean_proof
    if output_root is None:
        with tempfile.TemporaryDirectory(prefix="tokenshare_task14_golden_") as tmp_dir:
            return _build_lean_lemma_graph_oracle_evidence(
                case=case,
                output_root=Path(tmp_dir),
                checker=active_checker,
            )
    return _build_lean_lemma_graph_oracle_evidence(
        case=case,
        output_root=Path(output_root),
        checker=active_checker,
    )


def _build_lean_lemma_graph_oracle_evidence(
    *,
    case: JsonObject,
    output_root: Path,
    checker: LeanChecker,
) -> JsonObject:
    _validate_lean_lemma_graph_case_for_adapter(case)
    oracle_ref = case.get("oracle_proof_package_ref")
    if not isinstance(oracle_ref, dict):
        raise ValueError("Task 14 golden evidence requires an oracle proof package")
    if case.get("preflight_status") != "passed":
        raise ValueError("Task 14 golden evidence requires passed preflight")
    proof_sources = {
        str(node_id): str(proof_source)
        for node_id, proof_source in dict(oracle_ref["node_proof_sources"]).items()
    }

    case_id = str(case["case_id"])
    run_root = output_root / _safe_id(case_id)
    store = ArtifactStore(run_root)
    environment_manifest = default_lean_paper_environment_manifest()
    root_node_id = str(case["merge_plan_shape"]["root_node_id"])
    parent_payload = _lean_lemma_graph_payload_from_body(
        case["root_theorem_payload"],
        case_id=case_id,
        node_id=root_node_id,
    )
    parent_payload_ref = _save_parent_payload(
        store=store,
        case_id=case_id,
        payload=parent_payload,
    )
    certificate = build_fixed_plan_certificate(
        plan=LeanFixedDecompositionPlan.from_catalog_case(case),
        parent_theorem_payload_ref=parent_payload_ref,
        environment_manifest=environment_manifest,
    )
    certificate_ref = _save_lemma_graph_certificate(
        store=store,
        case=case,
        certificate=certificate,
    )
    split_report = LeanSplitHelperReport(
        report_id=f"lean_split_helper_report:{case_id}:task14_golden",
        request_id=f"task14_golden_split_request_{case_id}",
        status=LeanSplitHelperStatus.SUCCEEDED,
        exit_code=0,
        generated_source_ref=None,
        helper_stdout_ref=None,
        helper_stderr_ref=None,
        certificate_ref=certificate_ref,
        report_ref=None,
        certificate=certificate,
        diagnostics={"source": "task14_local_oracle_evidence"},
        environment_ref=build_lean_environment_ref(environment_manifest),
        command_summary={"kind": "catalog_deterministic_certificate"},
        duration_ms=0,
        helper_stdout_excerpt="",
        helper_stderr_excerpt="",
    )
    split_plan = build_lean_split_plan(
        split_report=split_report,
        artifact_store=store,
        task_id=f"task14_golden_lean_{case_id}",
        parent_unit_id=f"task14_golden_lean_root_{case_id}",
        canonical_selection_id=f"task14_golden_canonical_root_{case_id}",
        canonical_output_bundle_digest=parent_payload_ref.content_hash,
        plugin_descriptor_digest=build_lean_proof_plugin_descriptor().descriptor_digest,
        expansion_scope_hash=canonical_json_digest(
            {
                "case_id": case_id,
                "parent_payload_digest": parent_payload_ref.content_hash,
                "certificate_digest": certificate.certificate_digest,
            }
        ),
        expansion_decision_id=f"task14_golden_expansion_decision_{case_id}",
        created_at=NOW,
    )

    slot_by_node = {
        str(slot["source_child_logical_key"]): str(slot["slot_key"])
        for slot in split_plan.merge_plan.required_slots
    }
    nodes_by_id = {str(node["node_id"]): node for node in certificate.lemma_nodes}
    node_inputs: list[LeanLemmaGraphProofInput] = []
    node_checker_report_refs: dict[str, JsonObject] = {}
    node_proof_artifact_refs: dict[str, JsonObject] = {}
    node_proof_candidate_refs: dict[str, JsonObject] = {}
    for node_id in _lemma_graph_topological_order(case):
        node = nodes_by_id[node_id]
        if node_id not in proof_sources:
            raise ValueError(f"Task 14 oracle proof missing node: {node_id}")
        node_payload_ref = split_plan.child_payload_refs_by_logical_key[node_id]
        node_payload = LeanTheoremPayload.from_dict(dict(node["theorem_payload"]))
        proof_candidate_ref = store.save_json(
            {
                "schema_version": "lean_proof.proof_candidate.v1",
                "proof_candidate_id": (
                    f"proof_candidate:task14_golden:{case_id}:{node_id}"
                ),
                "theorem_payload_digest": node_payload.payload_digest,
                "proof_source": proof_sources[node_id],
                "created_at": NOW,
            },
            artifact_id=(
                f"task14_golden_{_safe_id(case_id)}_{_safe_id(node_id)}"
                "_proof_candidate"
            ),
            artifact_type="LeanProofCandidate",
            artifact_schema_id="lean_proof.proof_candidate",
            artifact_schema_version="v1",
            source={
                "kind": "task14_local_oracle_evidence",
                "case_id": case_id,
                "node_id": node_id,
            },
            metadata={"node_id": node_id, "case_id": case_id},
            created_at=NOW,
        )
        checker_report = checker(
            LeanCheckerRequest(
                request_id=f"task14_golden_checker_{case_id}_{_safe_id(node_id)}",
                theorem_payload_ref=node_payload_ref,
                proof_candidate_ref=proof_candidate_ref,
                environment_ref=build_lean_environment_ref(environment_manifest),
                checker_mode=LeanCheckerMode.CHILD_PROOF,
                timeout_seconds=int(node_payload.resource_limits["timeout_seconds"]),
                max_output_bytes=int(node_payload.resource_limits["max_output_bytes"]),
                created_at=NOW,
            ),
            artifact_store=store,
            environment_manifest=environment_manifest,
        )
        if checker_report.status != LeanCheckerStatus.ACCEPTED:
            raise ValueError(f"Task 14 oracle proof checker rejected node: {node_id}")
        if checker_report.report_ref is None or checker_report.proof_artifact_ref is None:
            raise ValueError("Task 14 accepted node proof missing checker artifacts")
        node_checker_report_refs[node_id] = checker_report.report_ref.to_dict()
        node_proof_artifact_refs[node_id] = checker_report.proof_artifact_ref.to_dict()
        node_proof_candidate_refs[node_id] = proof_candidate_ref.to_dict()
        node_inputs.append(
            LeanLemmaGraphProofInput(
                node_id=node_id,
                slot_key=slot_by_node[node_id],
                node_payload_ref=node_payload_ref,
                proof_candidate_ref=proof_candidate_ref,
                checker_report=checker_report,
                context_digest=str(node["context_digest"]),
                theorem_payload_digest=node_payload.payload_digest or "",
            )
        )

    merge_result = merge_lean_lemma_graph_proofs(
        merge_plan=split_plan.merge_plan,
        lemma_graph_certificate=certificate,
        parent_theorem_payload_ref=parent_payload_ref,
        node_proofs=node_inputs,
        artifact_store=store,
        environment_manifest=environment_manifest,
        merge_unit_id=f"task14_golden_lemma_graph_merge_unit_{case_id}",
        request_id=f"task14_golden_lemma_graph_merge_request_{case_id}",
        created_at=NOW,
        checker=checker,
    )
    if not merge_result.accepted:
        raise ValueError("Task 14 oracle merge/root checker rejected root")
    if (
        merge_result.merge_result_ref is None
        or merge_result.root_checker_report.report_ref is None
        or merge_result.root_proof_artifact_ref is None
    ):
        raise ValueError("Task 14 accepted merge missing root artifacts")

    return {
        "schema_version": "tokenshare.lean_task14_golden_evidence.v1",
        "evidence_source": "local_oracle_lemma_graph",
        "case_id": case_id,
        "environment_digest": environment_manifest.environment_digest,
        "oracle_package_digest": str(oracle_ref["content_hash"]),
        "split_certificate_digest": certificate.certificate_digest,
        "split_certificate_ref": certificate_ref.to_dict(),
        "deterministic_split": "passed",
        "child_proof_file_construction": "passed",
        "checker_preflight": "passed",
        "dependency_aware_merge": "passed",
        "root_recheck": "passed",
        "node_checker_report_refs": dict(sorted(node_checker_report_refs.items())),
        "node_proof_candidate_refs": dict(sorted(node_proof_candidate_refs.items())),
        "node_proof_artifact_refs": dict(sorted(node_proof_artifact_refs.items())),
        "merge_result_ref": merge_result.merge_result_ref.to_dict(),
        "root_checker_report_ref": (
            merge_result.root_checker_report.report_ref.to_dict()
        ),
        "root_proof_artifact_ref": merge_result.root_proof_artifact_ref.to_dict(),
        "provider_calls_made": 0,
    }


def _run_lean_lemma_graph_paper_case(
    *,
    case: JsonObject,
    condition: PaperExperimentCondition,
    output_root: str | Path,
    transport: Any | None,
    real_transport: bool,
    config: AIAPIExecutorConfig,
    secret_collector: _TransientSecretCollector,
    validated_binding: ValidatedModelEndpointBinding | None,
    max_tokens: int,
    timeout_seconds: int,
    selected_ai_unit_id: str | None,
    post_raw_output_hook: Any | None,
    ablation_mode: str,
    checker: LeanChecker,
) -> LeanPaperRunResult:
    paper_metadata = _validated_condition_paper_metadata(
        condition=condition,
        case=case,
    )
    case_id = str(case["case_id"])
    root = Path(output_root)
    run_root = root / case_id
    store = ArtifactStore(run_root)
    active_transport = transport
    if active_transport is None:
        active_transport = (
            _default_real_transport(config)
            if real_transport
            else ScriptedLeanPaperProofTransport()
        )
    environment_manifest = default_lean_paper_environment_manifest()

    if (
        case["paper_difficulty"] == "hard_frontier"
        and case.get("oracle_proof_package_ref") is None
    ):
        return _blocked_lemma_graph_frontier_result(
            case=case,
            condition=condition,
            run_root=run_root,
            store=store,
            real_transport=real_transport,
            secret_values=secret_collector.snapshot(),
        )

    root_node_id = str(case["merge_plan_shape"]["root_node_id"])
    parent_payload = _lean_lemma_graph_payload_from_body(
        case["root_theorem_payload"],
        case_id=case_id,
        node_id=root_node_id,
    )
    parent_payload_ref = _save_parent_payload(
        store=store,
        case_id=case_id,
        payload=parent_payload,
    )
    certificate = build_fixed_plan_certificate(
        plan=LeanFixedDecompositionPlan.from_catalog_case(case),
        parent_theorem_payload_ref=parent_payload_ref,
        environment_manifest=environment_manifest,
    )
    certificate_ref = _save_lemma_graph_certificate(
        store=store,
        case=case,
        certificate=certificate,
    )
    split_report = LeanSplitHelperReport(
        report_id=f"lean_split_helper_report:{case_id}:lemma_graph",
        request_id=f"paper_lean_lemma_graph_split_request_{case_id}",
        status=LeanSplitHelperStatus.SUCCEEDED,
        exit_code=0,
        generated_source_ref=None,
        helper_stdout_ref=None,
        helper_stderr_ref=None,
        certificate_ref=certificate_ref,
        report_ref=None,
        certificate=certificate,
        diagnostics={"source": "paper_lemma_graph_catalog"},
        environment_ref=build_lean_environment_ref(environment_manifest),
        command_summary={"kind": "catalog_deterministic_certificate"},
        duration_ms=0,
        helper_stdout_excerpt="",
        helper_stderr_excerpt="",
    )
    split_plan = build_lean_split_plan(
        split_report=split_report,
        artifact_store=store,
        task_id=f"paper_lean_{case_id}",
        parent_unit_id=f"paper_lean_root_{case_id}",
        canonical_selection_id=f"paper_lean_canonical_root_{case_id}",
        canonical_output_bundle_digest=parent_payload_ref.content_hash,
        plugin_descriptor_digest=build_lean_proof_plugin_descriptor().descriptor_digest,
        expansion_scope_hash=canonical_json_digest(
            {
                "case_id": case_id,
                "parent_payload_digest": parent_payload_ref.content_hash,
                "certificate_digest": certificate.certificate_digest,
            }
        ),
        expansion_decision_id=f"paper_lean_expansion_decision_{case_id}",
        created_at=NOW,
    )
    split_summary = _lemma_graph_split_summary(
        case=case,
        certificate=certificate,
        certificate_ref=certificate_ref,
        split_plan=split_plan,
    )

    attempts: list[PaperAttemptResult] = []
    child_records: list[JsonObject] = []
    node_proofs: list[LeanLemmaGraphProofInput] = []
    nodes_by_id = {str(node["node_id"]): node for node in certificate.lemma_nodes}
    indexed_node_ids = list(enumerate(_lemma_graph_topological_order(case)))
    available_ai_unit_ids = tuple(node_id for _index, node_id in indexed_node_ids)
    if (
        selected_ai_unit_id is not None
        and selected_ai_unit_id not in available_ai_unit_ids
    ):
        raise ValueError(
            "selected_ai_unit_id is not present in Lean lemma graph split plan"
        )
    if selected_ai_unit_id is not None:
        indexed_node_ids = [
            (index, node_id)
            for index, node_id in indexed_node_ids
            if node_id == selected_ai_unit_id
        ]
    for index, node_id in indexed_node_ids:
        node = nodes_by_id[node_id]
        node_payload_ref = split_plan.child_payload_refs_by_logical_key[node_id]
        node_result = _run_lemma_graph_node_attempt(
            case=case,
            condition=condition,
            certificate=certificate,
            node=node,
            node_payload_ref=node_payload_ref,
            store=store,
            config=config,
            validated_binding=validated_binding,
            transport=active_transport,
            paper_eligible_transport=not _is_offline_capturing_transport(
                active_transport
            ),
            index=index,
            timeout_seconds=timeout_seconds,
            max_tokens=max_tokens,
            environment_manifest=environment_manifest,
            post_raw_output_hook=post_raw_output_hook,
            secret_collector=secret_collector,
            ablation_mode=ablation_mode,
            checker=checker,
        )
        attempts.append(node_result["attempt"])
        child_records.append(node_result["record"])
        proof_input = node_result["proof_input"]
        if proof_input is not None:
            node_proofs.append(proof_input)
        if node_result["model_identity_mismatch"]:
            break

    if ablation_mode == "NO_SLOT_INTEGRITY" and node_proofs:
        node_proofs[0] = replace(
            node_proofs[0],
            slot_key=f"ablation_wrong_slot:{node_proofs[0].slot_key}",
        )
    merge_summary = _merge_lemma_graph_if_ready(
        split_plan=split_plan,
        certificate=certificate,
        parent_payload_ref=parent_payload_ref,
        node_proofs=node_proofs,
        store=store,
        environment_manifest=environment_manifest,
        case_id=case_id,
        force=ablation_mode == "NO_MERGE_GATE",
        checker=checker,
    )
    accepted_validity = merge_summary.get("root_checker_accepted") is True
    task_status = (
        PaperTaskStatus.COMPLETED if accepted_validity else PaperTaskStatus.FAILED
    )
    failure_stage = None
    failure_kind = None
    if task_status != PaperTaskStatus.COMPLETED:
        failure_stage, failure_kind = _task_failure_from_child_evidence(
            attempts=attempts,
            child_records=child_records,
            merge_summary=merge_summary,
        )

    run_evidence = _run_evidence(
        store=store,
        real_transport=real_transport,
        transport_kind="ai_api" if real_transport else "scripted",
        secret_values=secret_collector.snapshot(),
        transport=active_transport,
    )
    run_evidence["lean_lemma_graph"] = _lemma_graph_run_metadata(
        case=case,
        certificate=certificate,
        certificate_ref=certificate_ref,
        split_plan=split_plan,
    )
    run_evidence["ablation_runtime"] = _lean_ablation_runtime_evidence(
        mode=ablation_mode,
        attempts=attempts,
        merge_summary=merge_summary,
        root_validity=accepted_validity,
    )
    eligibility = _evaluate_lean_paper_eligibility(
        attempts=attempts,
        run_evidence=run_evidence,
        checker=checker,
    )
    task_result = PaperTaskResult(
        condition_id=condition.condition_id,
        repeat_id=condition.repeat_id,
        task_id=f"paper_lean_{case_id}",
        domain="lean_proof",
        difficulty=str(case["difficulty"]),
        **paper_metadata,
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
        artifact_refs=_task_artifact_refs(
            attempts=attempts,
            child_records=child_records,
            merge_summary=merge_summary,
        ),
        paper_eligible=eligibility.paper_eligible,
    )
    result = LeanPaperRunResult(
        condition=condition,
        case_id=case_id,
        task_result=task_result,
        attempt_results=tuple(attempts),
        eligibility_report=eligibility,
        split_summary=split_summary,
        child_results=tuple(child_records),
        merge_summary=merge_summary,
        run_evidence=run_evidence,
        output_root=run_root.as_posix(),
    )
    _write_case_outputs(run_root, result)
    return result


def _normalize_ablation_mode(value: str | None) -> str:
    mode = "FULL" if value is None else str(value).upper()
    if mode not in {
        "FULL",
        "NO_VERIFICATION",
        "NO_PARSER_POLICY",
        "NO_REQUEUE",
        "NO_MERGE_GATE",
        "NO_SLOT_INTEGRITY",
    }:
        raise ValueError("unsupported Experiment 4 ablation mode")
    return mode


def _parse_runtime_hook_observations(
    value: object,
) -> tuple[RuntimeHookObservationV1, ...]:
    if not isinstance(value, (list, tuple)):
        raise TypeError("runtime_hook_observations must be a list or tuple")
    return tuple(RuntimeHookObservationV1.from_dict(item) for item in value)


def _lean_ablation_runtime_evidence(
    *,
    mode: str,
    attempts: list[PaperAttemptResult],
    merge_summary: JsonObject,
    root_validity: bool | None,
    max_retries: int = 0,
    submitted_candidate_refs_by_attempt: (
        dict[str, dict[str, ArtifactRef]] | None
    ) = None,
    canonical_refs_by_attempt: dict[str, dict[str, ArtifactRef]] | None = None,
    independent_validity_by_attempt: dict[str, bool] | None = None,
    hook_observations: tuple[RuntimeHookObservationV1, ...] = (),
) -> JsonObject:
    submitted_candidate_refs_by_attempt = (
        submitted_candidate_refs_by_attempt or {}
    )
    canonical_refs_by_attempt = canonical_refs_by_attempt or {}
    independent_validity_by_attempt = independent_validity_by_attempt or {}
    premature_payloads = tuple(
        item.payload
        for item in hook_observations
        if item.kind
        is RuntimeHookObservationKind.EXPERIMENT_PREMATURE_MERGE_ATTEMPTED
        and isinstance(item.payload, ExperimentPrematureMergeAttemptedPayloadV1)
    )
    return {
        "schema_version": "tokenshare.paper_ablation_runtime.v1",
        "mode": mode,
        "disabled_mechanism": {
            "FULL": None,
            "NO_VERIFICATION": "verification",
            "NO_PARSER_POLICY": "parser_policy",
            "NO_REQUEUE": "requeue",
            "NO_MERGE_GATE": "merge_gate",
            "NO_SLOT_INTEGRITY": "slot_integrity",
        }[mode],
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
            or premature_payloads
        ),
        "slot_integrity_violation": bool(
            merge_summary.get("slot_integrity_violation")
        ),
        "root_validity_audit_passed": root_validity,
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
                "final_validity": root_validity,
            }
            for attempt in attempts
        ],
        "hook_observations": [item.to_dict() for item in hook_observations],
    }


def _run_child_attempt(
    *,
    case: JsonObject,
    condition: PaperExperimentCondition,
    child_key: str,
    child_payload_ref: ArtifactRef,
    split_certificate,
    store: ArtifactStore,
    ledger: EventLedger,
    config: AIAPIExecutorConfig,
    validated_binding: ValidatedModelEndpointBinding | None,
    transport: Any,
    paper_eligible_transport: bool,
    index: int,
    timeout_seconds: int,
    max_tokens: int,
    environment_manifest: LeanEnvironmentManifest,
    post_raw_output_hook: Any | None,
    secret_collector: _TransientSecretCollector,
    ablation_mode: str,
    checker: LeanChecker,
) -> JsonObject:
    del ledger
    case_id = str(case["case_id"])
    child_payload = LeanTheoremPayload.from_dict(
        json.loads(store.read_bytes(child_payload_ref).decode("utf-8"))
    )
    request = _build_child_execution_request(
        store=store,
        case=case,
        condition=condition,
        child_key=child_key,
        child_payload=child_payload,
        child_payload_ref=child_payload_ref,
        index=index,
        provider_family=config.provider_family,
        timeout_seconds=timeout_seconds,
        max_tokens=max_tokens,
    )
    request_ref = store.save_json(
        request.to_dict(),
        artifact_id=f"paper_lean_request_{case_id}_{index}",
        artifact_type="ExecutionRequest",
        artifact_schema_id="phase3.execution_request",
        artifact_schema_version="v1",
        source={"kind": "lean_paper_adapter", "case_id": case_id},
        metadata={"case_id": case_id, "child_logical_key": child_key},
        created_at=NOW,
    )
    executor = AIAPIExecutor(
        executor_id=AI_EXECUTOR_ID,
        executor_version=AI_EXECUTOR_VERSION,
        artifact_store=store,
        config=config,
        transport=transport,
        parser=(
            None
            if ablation_mode == "NO_PARSER_POLICY"
            else lambda raw, *, raw_output_ref_summary, created_at: parse_lean_proof_candidate_ai_output(
                raw,
                theorem_payload=child_payload,
                raw_output_ref_summary=raw_output_ref_summary,
                created_at=created_at,
            )
        ),
        post_raw_output_hook=post_raw_output_hook,
        resolved_secret_observer=secret_collector.observe,
    )
    submission = executor.execute(
        request,
        submission_id=f"paper_lean_submission_{case_id}_{_safe_id(child_key)}",
        submitted_at=NOW,
    )
    usage_ref = _save_usage_artifact(
        store=store,
        case_id=case_id,
        index=index,
        submission=submission,
    )
    model_execution_record, model_execution_record_ref = _save_model_execution_record(
        store=store,
        condition=condition,
        binding=validated_binding,
        prepared_config=config,
        case_id=case_id,
        request=request,
        request_ref=request_ref,
        submission=submission,
        usage_ref=usage_ref,
        paper_eligible_transport=paper_eligible_transport,
    )
    model_identity_mismatch = (
        model_execution_record is not None
        and model_execution_record.identity_status == "model_identity_mismatch"
    )
    proof_candidate_ref = (
        None
        if model_identity_mismatch
        else submission.candidate_output_refs.get(PROOF_CANDIDATE_OUTPUT_NAME)
    )
    proof_result: LeanChildProofResult | None = None
    attempt_status = _attempt_status_from_submission(submission.result_kind)
    if model_identity_mismatch:
        attempt_status = PaperAttemptStatus.MODEL_IDENTITY_MISMATCH
    if proof_candidate_ref is not None and ablation_mode != "NO_VERIFICATION":
        proof_result = check_lean_child_proof(
            child_logical_key=child_key,
            split_certificate=split_certificate,
            child_payload_ref=child_payload_ref,
            proof_candidate_ref=proof_candidate_ref,
            artifact_store=store,
            environment_manifest=environment_manifest,
            request_id=f"paper_lean_child_checker_{case_id}_{_safe_id(child_key)}",
            created_at=NOW,
            checker=checker,
        )
        if not proof_result.accepted:
            attempt_status = PaperAttemptStatus.CHECKER_REJECTED
    attempt = _paper_attempt_result(
        store=store,
        condition=condition,
        case_id=case_id,
        index=index,
        request=request,
        request_ref=request_ref,
        submission=submission,
        usage_ref=usage_ref,
        attempt_status=attempt_status,
        planned_ai_unit_id=f"child_{index}",
        paper_difficulty=condition.paper_difficulty,
        topic_family=condition.topic_family,
        topic_family_version=condition.topic_family_version,
        construction_rule_id=condition.construction_rule_id,
        oracle_package_group=condition.oracle_package_group,
        proof_assembly_shape=condition.proof_assembly_shape,
        model_execution_record_ref=model_execution_record_ref,
        error_kind_override=(
            "model_identity_mismatch" if model_identity_mismatch else None
        ),
    )
    record = _child_record(
        child_key=child_key,
        child_payload=child_payload,
        child_payload_ref=child_payload_ref,
        submission=submission,
        proof_candidate_ref=proof_candidate_ref,
        proof_result=proof_result,
    )
    record["model_execution_record_ref"] = (
        model_execution_record_ref.to_dict()
        if model_execution_record_ref is not None
        else None
    )
    record["model_identity_status"] = (
        model_execution_record.identity_status
        if model_execution_record is not None
        else None
    )
    if proof_candidate_ref is not None and ablation_mode == "NO_VERIFICATION":
        record["canonical_output_ref"] = proof_candidate_ref.to_dict()
        record["verification"] = {
            "accepted": True,
            "status": "skipped_by_ablation",
            "checker_executed": False,
        }
    return {
        "attempt": attempt,
        "record": record,
        "proof_result": proof_result,
        "model_identity_mismatch": model_identity_mismatch,
    }


def _run_lemma_graph_node_attempt(
    *,
    case: JsonObject,
    condition: PaperExperimentCondition,
    certificate: LeanLemmaGraphCertificate,
    node: JsonObject,
    node_payload_ref: ArtifactRef,
    store: ArtifactStore,
    config: AIAPIExecutorConfig,
    validated_binding: ValidatedModelEndpointBinding | None,
    transport: Any,
    paper_eligible_transport: bool,
    index: int,
    timeout_seconds: int,
    max_tokens: int,
    environment_manifest: LeanEnvironmentManifest,
    post_raw_output_hook: Any | None,
    secret_collector: _TransientSecretCollector,
    ablation_mode: str,
    checker: LeanChecker,
) -> JsonObject:
    case_id = str(case["case_id"])
    node_id = str(node["node_id"])
    node_payload = LeanTheoremPayload.from_dict(
        json.loads(store.read_bytes(node_payload_ref).decode("utf-8"))
    )
    slot_key = f"{node_id}:{PROOF_ARTIFACT_OUTPUT_NAME}"
    dependency_path = _dependency_path_to_node(case, node_id)
    request = _build_lemma_node_execution_request(
        store=store,
        case=case,
        condition=condition,
        node=node,
        node_payload=node_payload,
        node_payload_ref=node_payload_ref,
        slot_key=slot_key,
        dependency_path=dependency_path,
        index=index,
        provider_family=config.provider_family,
        timeout_seconds=timeout_seconds,
        max_tokens=max_tokens,
    )
    request_ref = store.save_json(
        request.to_dict(),
        artifact_id=f"paper_lean_request_{case_id}_{_safe_id(node_id)}",
        artifact_type="ExecutionRequest",
        artifact_schema_id="phase3.execution_request",
        artifact_schema_version="v1",
        source={"kind": "lean_paper_adapter", "case_id": case_id},
        metadata={
            "case_id": case_id,
            "lemma_node_id": node_id,
            "slot_key": slot_key,
            "dependency_path": dependency_path,
            "paper_difficulty": condition.paper_difficulty,
            "topic_family": condition.topic_family,
            "topic_family_version": condition.topic_family_version,
            "construction_rule_id": condition.construction_rule_id,
            "oracle_package_group": condition.oracle_package_group,
            "proof_assembly_shape": condition.proof_assembly_shape,
            "construction_rule_id": case["construction_rule_id"],
            "oracle_package_group": case["oracle_package_group"],
            "proof_assembly_shape": case["proof_assembly_shape"],
        },
        created_at=NOW,
    )
    executor = AIAPIExecutor(
        executor_id=AI_EXECUTOR_ID,
        executor_version=AI_EXECUTOR_VERSION,
        artifact_store=store,
        config=config,
        transport=transport,
        parser=(
            None
            if ablation_mode == "NO_PARSER_POLICY"
            else lambda raw, *, raw_output_ref_summary, created_at: parse_lean_proof_candidate_ai_output(
                raw,
                theorem_payload=node_payload,
                raw_output_ref_summary=raw_output_ref_summary,
                created_at=created_at,
            )
        ),
        post_raw_output_hook=post_raw_output_hook,
        resolved_secret_observer=secret_collector.observe,
    )
    submission = executor.execute(
        request,
        submission_id=f"paper_lean_submission_{case_id}_{_safe_id(node_id)}",
        submitted_at=NOW,
    )
    usage_ref = _save_usage_artifact(
        store=store,
        case_id=case_id,
        index=index,
        submission=submission,
    )
    model_execution_record, model_execution_record_ref = _save_model_execution_record(
        store=store,
        condition=condition,
        binding=validated_binding,
        prepared_config=config,
        case_id=case_id,
        request=request,
        request_ref=request_ref,
        submission=submission,
        usage_ref=usage_ref,
        paper_eligible_transport=paper_eligible_transport,
    )
    model_identity_mismatch = (
        model_execution_record is not None
        and model_execution_record.identity_status == "model_identity_mismatch"
    )
    proof_candidate_ref = (
        None
        if model_identity_mismatch
        else submission.candidate_output_refs.get(PROOF_CANDIDATE_OUTPUT_NAME)
    )
    checker_report = None
    attempt_status = _attempt_status_from_submission(submission.result_kind)
    if model_identity_mismatch:
        attempt_status = PaperAttemptStatus.MODEL_IDENTITY_MISMATCH
    if proof_candidate_ref is not None and ablation_mode != "NO_VERIFICATION":
        checker_report = checker(
            LeanCheckerRequest(
                request_id=f"paper_lean_lemma_node_checker_{case_id}_{_safe_id(node_id)}",
                theorem_payload_ref=node_payload_ref,
                proof_candidate_ref=proof_candidate_ref,
                environment_ref=build_lean_environment_ref(environment_manifest),
                checker_mode=LeanCheckerMode.CHILD_PROOF,
                timeout_seconds=int(node_payload.resource_limits["timeout_seconds"]),
                max_output_bytes=int(node_payload.resource_limits["max_output_bytes"]),
                created_at=NOW,
            ),
            artifact_store=store,
            environment_manifest=environment_manifest,
        )
        if checker_report.status != LeanCheckerStatus.ACCEPTED:
            attempt_status = PaperAttemptStatus.CHECKER_REJECTED
    attempt = _paper_attempt_result(
        store=store,
        condition=condition,
        case_id=case_id,
        index=index,
        request=request,
        request_ref=request_ref,
        submission=submission,
        usage_ref=usage_ref,
        attempt_status=attempt_status,
        planned_ai_unit_id=node_id,
        paper_difficulty=condition.paper_difficulty,
        topic_family=condition.topic_family,
        topic_family_version=condition.topic_family_version,
        construction_rule_id=condition.construction_rule_id,
        oracle_package_group=condition.oracle_package_group,
        proof_assembly_shape=condition.proof_assembly_shape,
        lemma_node_id=node_id,
        slot_key=slot_key,
        dependency_path=dependency_path,
        model_execution_record_ref=model_execution_record_ref,
        error_kind_override=(
            "model_identity_mismatch" if model_identity_mismatch else None
        ),
    )
    proof_input = None
    if (
        proof_candidate_ref is not None
        and checker_report is not None
        and checker_report.status == LeanCheckerStatus.ACCEPTED
    ):
        proof_input = LeanLemmaGraphProofInput(
            node_id=node_id,
            slot_key=slot_key,
            node_payload_ref=node_payload_ref,
            proof_candidate_ref=proof_candidate_ref,
            checker_report=checker_report,
            context_digest=str(node["context_digest"]),
            theorem_payload_digest=node_payload.payload_digest or "",
        )
    record = _lemma_node_record(
        case=case,
        certificate=certificate,
        node=node,
        slot_key=slot_key,
        dependency_path=dependency_path,
        node_payload=node_payload,
        node_payload_ref=node_payload_ref,
        submission=submission,
        proof_candidate_ref=proof_candidate_ref,
        checker_report=checker_report,
    )
    record["model_execution_record_ref"] = (
        model_execution_record_ref.to_dict()
        if model_execution_record_ref is not None
        else None
    )
    record["model_identity_status"] = (
        model_execution_record.identity_status
        if model_execution_record is not None
        else None
    )
    if proof_candidate_ref is not None and ablation_mode == "NO_VERIFICATION":
        record["canonical_output_ref"] = proof_candidate_ref.to_dict()
        record["verification"] = {
            "accepted": True,
            "status": "skipped_by_ablation",
            "checker_executed": False,
        }
    return {
        "attempt": attempt,
        "record": record,
        "proof_input": proof_input,
        "model_identity_mismatch": model_identity_mismatch,
    }


def _build_lemma_node_execution_request(
    *,
    store: ArtifactStore,
    case: JsonObject,
    condition: PaperExperimentCondition,
    node: JsonObject,
    node_payload: LeanTheoremPayload,
    node_payload_ref: ArtifactRef,
    slot_key: str,
    dependency_path: list[str],
    index: int,
    provider_family: str,
    timeout_seconds: int,
    max_tokens: int,
) -> ExecutionRequest:
    case_id = str(case["case_id"])
    node_id = str(node["node_id"])
    safe_node = _safe_id(node_id)
    task_id = f"paper_lean_{case_id}"
    unit_id = f"paper_lean_{case_id}_{safe_node}"
    request_id = f"paper_lean_request_{case_id}_{safe_node}"
    prompt = build_lean_proof_candidate_prompt_package(
        request_id=request_id,
        task_id=task_id,
        unit_id=unit_id,
        theorem_payload=node_payload,
        created_at=NOW,
        seed=condition.seed + index,
        planned_ai_unit_id=node_id,
    )
    prompt_ref = store.save_json(
        prompt.to_dict(),
        artifact_id=f"paper_lean_prompt_{case_id}_{safe_node}",
        artifact_type="PromptPackage",
        artifact_schema_id="phase3.prompt_package",
        artifact_schema_version="v1",
        source={"kind": "lean_paper_adapter", "case_id": case_id},
        metadata={
            "lemma_node_id": node_id,
            "slot_key": slot_key,
            "dependency_path": dependency_path,
            "paper_difficulty": condition.paper_difficulty,
            "topic_family": condition.topic_family,
        },
        created_at=NOW,
    )
    descriptor = build_lean_proof_plugin_descriptor()
    return ExecutionRequest(
        request_id=request_id,
        task_id=task_id,
        unit_id=unit_id,
        attempt_id=f"paper_lean_attempt_{case_id}_{safe_node}",
        lease_id=f"paper_lean_lease_{case_id}_{safe_node}",
        fencing_token=f"paper_lean_fence_{case_id}_{safe_node}",
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
        registry_snapshot_id="registry_snapshot_lean_paper",
        allocation_decision={
            "decision_id": f"paper_lean_allocation_{case_id}_{safe_node}",
            "selected_executor_id": AI_EXECUTOR_ID,
            "eligible_executor_ids": [AI_EXECUTOR_ID],
        },
        capability_snapshot={"executor": "ai_api", "provider_family": provider_family},
        task_unit_snapshot=_lemma_node_task_unit(
            task_id=task_id,
            unit_id=unit_id,
            node_payload_ref=node_payload_ref,
            case=case,
            node=node,
            slot_key=slot_key,
            dependency_path=dependency_path,
            node_payload_body=node_payload.to_dict(),
        ).to_dict(),
        input_artifact_refs={"lemma_theorem_payload": node_payload_ref},
        output_contract=_proof_candidate_output_contract(),
        hard_requirements={"executor": "ai_api", "provider_family": provider_family},
        soft_hints={
            "temperature": 0.0,
            "paper_condition_id": condition.condition_id,
            "paper_difficulty": condition.paper_difficulty,
            "topic_family": condition.topic_family,
            "topic_family_version": condition.topic_family_version,
            "construction_rule_id": condition.construction_rule_id,
            "oracle_package_group": condition.oracle_package_group,
            "proof_assembly_shape": condition.proof_assembly_shape,
            "lemma_node_id": node_id,
            "slot_key": slot_key,
            "dependency_path": dependency_path,
            "planned_ai_unit_id": node_id,
            "paper_provider_attempt_index": 0,
        },
        environment_ref=_ai_environment_ref(seed=condition.seed + index),
        execution_instruction_ref=None,
        prompt_package_ref=prompt_ref,
        limits={"timeout_seconds": timeout_seconds, "max_tokens": max_tokens},
        created_at=NOW,
    )


def _build_child_execution_request(
    *,
    store: ArtifactStore,
    case: JsonObject,
    condition: PaperExperimentCondition,
    child_key: str,
    child_payload: LeanTheoremPayload,
    child_payload_ref: ArtifactRef,
    index: int,
    provider_family: str,
    timeout_seconds: int,
    max_tokens: int,
) -> ExecutionRequest:
    case_id = str(case["case_id"])
    safe_child = _safe_id(child_key)
    task_id = f"paper_lean_{case_id}"
    unit_id = f"paper_lean_{case_id}_{safe_child}"
    request_id = f"paper_lean_request_{case_id}_{safe_child}"
    prompt = build_lean_proof_candidate_prompt_package(
        request_id=request_id,
        task_id=task_id,
        unit_id=unit_id,
        theorem_payload=child_payload,
        created_at=NOW,
        seed=condition.seed + index,
        planned_ai_unit_id=child_key,
    )
    prompt_ref = store.save_json(
        prompt.to_dict(),
        artifact_id=f"paper_lean_prompt_{case_id}_{safe_child}",
        artifact_type="PromptPackage",
        artifact_schema_id="phase3.prompt_package",
        artifact_schema_version="v1",
        source={"kind": "lean_paper_adapter", "case_id": case_id},
        metadata={"child_logical_key": child_key},
        created_at=NOW,
    )
    descriptor = build_lean_proof_plugin_descriptor()
    return ExecutionRequest(
        request_id=request_id,
        task_id=task_id,
        unit_id=unit_id,
        attempt_id=f"paper_lean_attempt_{case_id}_{safe_child}",
        lease_id=f"paper_lean_lease_{case_id}_{safe_child}",
        fencing_token=f"paper_lean_fence_{case_id}_{safe_child}",
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
        registry_snapshot_id="registry_snapshot_lean_paper",
        allocation_decision={
            "decision_id": f"paper_lean_allocation_{case_id}_{safe_child}",
            "selected_executor_id": AI_EXECUTOR_ID,
            "eligible_executor_ids": [AI_EXECUTOR_ID],
        },
        capability_snapshot={"executor": "ai_api", "provider_family": provider_family},
        task_unit_snapshot=_child_task_unit(
            task_id=task_id,
            unit_id=unit_id,
            child_payload_ref=child_payload_ref,
            case=case,
            child_logical_key=child_key,
            child_payload_body=child_payload.to_dict(),
        ).to_dict(),
        input_artifact_refs={"child_theorem_payload": child_payload_ref},
        output_contract=_proof_candidate_output_contract(),
        hard_requirements={"executor": "ai_api", "provider_family": provider_family},
        soft_hints={
            "temperature": 0.0,
            "paper_condition_id": condition.condition_id,
            "planned_ai_unit_id": f"child_{index}",
            "paper_provider_attempt_index": 0,
            "paper_difficulty": condition.paper_difficulty,
            "topic_family": condition.topic_family,
            "topic_family_version": condition.topic_family_version,
            "construction_rule_id": condition.construction_rule_id,
            "oracle_package_group": condition.oracle_package_group,
            "proof_assembly_shape": condition.proof_assembly_shape,
        },
        environment_ref=_ai_environment_ref(seed=condition.seed + index),
        execution_instruction_ref=None,
        prompt_package_ref=prompt_ref,
        limits={"timeout_seconds": timeout_seconds, "max_tokens": max_tokens},
        created_at=NOW,
    )


def _child_task_unit(
    *,
    task_id: str,
    unit_id: str,
    child_payload_ref: ArtifactRef,
    case: JsonObject,
    child_logical_key: str,
    child_payload_body: JsonObject,
) -> TaskUnit:
    return TaskUnit(
        unit_id=unit_id,
        task_id=task_id,
        parent_unit_id=f"paper_lean_root_{case['case_id']}",
        depth=1,
        unit_type="lean_proof_subgoal",
        state=TaskState.PROCESSING,
        input_refs={"child_theorem_payload": child_payload_ref},
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


def _ai_environment_ref(*, seed: int) -> EnvironmentRef:
    return EnvironmentRef(
        environment_id="env_lean_paper_ai_api",
        environment_digest="sha256:env_lean_paper_ai_api",
        runtime="python",
        tool_versions={"ai_api_executor": AI_EXECUTOR_VERSION},
        resource_limits={"timeout_seconds": 30, "max_tokens": 1024},
        fixture_profile_digest="sha256:lean_paper_adapter",
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
            adapter_metadata_key="lean_paper_adapter",
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
        metadata={**dict(config.metadata), "lean_paper_adapter": True},
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
            "real transport Lean paper runs require "
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
    os.environ.setdefault(FAKE_KEY_ENV, "tokenshare-lean-paper-fake-key")
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
                    "entry_id": entry_id or "lean_paper_scripted",
                    "enabled": True,
                    "base_url": "https://api.siliconflow.cn/v1",
                    "api_key_env": FAKE_KEY_ENV,
                    "model": "TokenShare/Scripted-Lean-Paper-Prover",
                    "endpoint": "/chat/completions",
                    "supports_json_mode": True,
                    "supports_streaming": False,
                    "request_overrides": {"temperature": 0.0},
                    "pricing": {
                        "currency": "CNY",
                        "input_per_million_tokens": 1.0,
                        "output_per_million_tokens": 2.0,
                    },
                    "tags": ["lean_paper", "scripted"],
                }
            ],
            "local_concurrency": {"max_in_flight_global": 1},
            "metadata": {"purpose": "lean-paper-scripted-test"},
        }
    )


def _validate_lean_case_for_adapter(case: JsonObject) -> None:
    if case.get("schema_version") != "tokenshare.paper_lean_case.v1":
        raise ValueError("Lean paper case schema_version mismatch")
    if case.get("expected_split_kind") not in {"conjunction", "iff"}:
        raise ValueError("Lean paper adapter currently requires conjunction or iff split")
    if int(case.get("expected_child_count", 0)) < 1:
        raise ValueError("Lean paper adapter requires at least one expected child")


def _validated_condition_paper_metadata(
    *,
    condition: PaperExperimentCondition,
    case: JsonObject,
) -> JsonObject:
    """只从已冻结 condition 传播论文分层元数据，并与 catalog case 双向核对。"""

    metadata: JsonObject = {}
    for field_name in (
        "paper_difficulty",
        "topic_family",
        "topic_family_version",
        "construction_rule_id",
        "oracle_package_group",
        "proof_assembly_shape",
    ):
        condition_value = getattr(condition, field_name)
        case_value = case.get(field_name)
        if condition_value != case_value:
            raise ValueError(
                f"condition {field_name} must match frozen Lean case metadata"
            )
        metadata[field_name] = condition_value
    if not isinstance(metadata["paper_difficulty"], str) or not metadata[
        "paper_difficulty"
    ]:
        raise ValueError("Lean paper condition requires paper_difficulty")
    if not isinstance(metadata["topic_family"], str) or not metadata["topic_family"]:
        raise ValueError("Lean paper condition requires topic_family")
    return metadata


def _validate_lean_lemma_graph_case_for_adapter(case: JsonObject) -> None:
    if case.get("schema_version") != LEAN_V2_SCHEMA_VERSION:
        raise ValueError("Lean lemma graph paper case schema_version mismatch")
    if case.get("domain") != "lean_proof":
        raise ValueError("Lean lemma graph paper case domain must be lean_proof")
    paper_difficulty = case.get("paper_difficulty")
    if paper_difficulty not in {"simple", "medium_lemma_dag", "hard_frontier"}:
        raise ValueError(
            "Lean lemma graph adapter requires simple, medium_lemma_dag, or hard_frontier"
        )
    for field_name in (
        "case_id",
        "difficulty",
        "topic_family",
        "topic_family_version",
        "construction_rule_id",
        "oracle_package_group",
        "proof_assembly_shape",
        "root_theorem_payload",
        "lemma_graph",
        "dependency_edges",
        "merge_plan_shape",
        "environment_digest",
    ):
        if field_name not in case:
            raise ValueError(f"Lean lemma graph case missing {field_name}")
    nodes = case.get("lemma_graph", {}).get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("Lean lemma graph case requires lemma_graph.nodes")
    if paper_difficulty in {"simple", "medium_lemma_dag"}:
        if case.get("preflight_status") != "passed":
            raise ValueError(f"{paper_difficulty} requires passed preflight_status")
        if not isinstance(case.get("oracle_proof_package_ref"), dict):
            raise ValueError(
                f"{paper_difficulty} requires oracle proof package metadata"
            )
    if (
        paper_difficulty == "hard_frontier"
        and case.get("oracle_proof_package_ref") is None
        and case.get("preflight_status") not in {"structured_blocked", "frontier_stress"}
    ):
        raise ValueError("hard_frontier no-oracle case requires structured blocked preflight")


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


def _save_lemma_graph_certificate(
    *,
    store: ArtifactStore,
    case: JsonObject,
    certificate: LeanLemmaGraphCertificate,
) -> ArtifactRef:
    return store.save_json(
        certificate.to_dict(),
        artifact_id=f"paper_lean_lemma_graph_certificate_{_safe_id(str(case['case_id']))}",
        artifact_type="LeanLemmaGraphCertificate",
        artifact_schema_id="lean_proof.lemma_graph_certificate",
        artifact_schema_version="v2",
        source={"kind": "lean_paper_adapter", "case_id": str(case["case_id"])},
        metadata={
            "case_id": str(case["case_id"]),
            "root_node_id": certificate.root_node_id,
            "paper_difficulty": case["paper_difficulty"],
            "topic_family": case["topic_family"],
        },
        created_at=NOW,
    )


def _lemma_graph_split_summary(
    *,
    case: JsonObject,
    certificate: LeanLemmaGraphCertificate,
    certificate_ref: ArtifactRef,
    split_plan,
) -> JsonObject:
    return {
        "split_status": "succeeded",
        "split_rule_id": certificate.rule_id,
        "split_kind": str(case["expected_split_kind"]),
        "child_count": len(certificate.lemma_nodes),
        "lemma_node_count": len(certificate.lemma_nodes),
        "dependency_edge_count": len(certificate.dependency_edges),
        "root_node_id": certificate.root_node_id,
        "certificate_ref": certificate_ref.to_dict(),
        "certificate_digest": certificate.certificate_digest,
        "proposal_digest": split_plan.proposal.proposal_header["proposal_digest"],
        "merge_plan_digest": split_plan.merge_plan.merge_plan_header["merge_plan_digest"],
        "paper_difficulty": case["paper_difficulty"],
        "topic_family": case["topic_family"],
        "construction_rule_id": case["construction_rule_id"],
        "oracle_package_group": case["oracle_package_group"],
        "proof_assembly_shape": case["proof_assembly_shape"],
    }


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
        incoming_by_target[str(edge["target_node_id"])].append(str(edge["source_node_id"]))
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
        incoming_by_target[str(edge["target_node_id"])].append(str(edge["source_node_id"]))
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


def _lemma_node_task_unit(
    *,
    task_id: str,
    unit_id: str,
    node_payload_ref: ArtifactRef,
    case: JsonObject,
    node: JsonObject,
    slot_key: str,
    dependency_path: list[str],
    node_payload_body: JsonObject,
) -> TaskUnit:
    node_id = str(node["node_id"])
    return TaskUnit(
        unit_id=unit_id,
        task_id=task_id,
        parent_unit_id=f"paper_lean_root_{case['case_id']}",
        depth=int(node["depth"]),
        unit_type="lean_proof_lemma_node",
        state=TaskState.PROCESSING,
        input_refs={"lemma_theorem_payload": node_payload_ref},
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
    )


def _lemma_node_record(
    *,
    case: JsonObject,
    certificate: LeanLemmaGraphCertificate,
    node: JsonObject,
    slot_key: str,
    dependency_path: list[str],
    node_payload: LeanTheoremPayload,
    node_payload_ref: ArtifactRef,
    submission,
    proof_candidate_ref: ArtifactRef | None,
    checker_report,
) -> JsonObject:
    node_id = str(node["node_id"])
    accepted = (
        checker_report is not None
        and checker_report.status == LeanCheckerStatus.ACCEPTED
    )
    return {
        "child_logical_key": node_id,
        "lemma_node_id": node_id,
        "unit_type": "lean_proof_lemma_node",
        "node_kind": node["node_kind"],
        "depth": node["depth"],
        "slot_key": slot_key,
        "dependency_path": dependency_path,
        "statement_source": node_payload.statement_source,
        "node_payload_ref": node_payload_ref.to_dict(),
        "child_payload_ref": node_payload_ref.to_dict(),
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
        "checker": {
            "accepted": accepted,
            "failure_kind": (
                None
                if accepted
                else (
                    checker_report.status.value
                    if checker_report is not None
                    else None
                )
            ),
            "environment_digest": (
                checker_report.environment_ref.environment_digest
                if checker_report is not None
                else None
            ),
            "report_ref": (
                checker_report.report_ref.to_dict()
                if checker_report is not None and checker_report.report_ref is not None
                else None
            ),
            "proof_artifact_ref": (
                checker_report.proof_artifact_ref.to_dict()
                if checker_report is not None and checker_report.proof_artifact_ref is not None
                else None
            ),
        },
        "certificate_id": certificate.certificate_id,
        "certificate_digest": certificate.certificate_digest,
        "paper_difficulty": case["paper_difficulty"],
        "topic_family": case["topic_family"],
        "topic_family_version": case["topic_family_version"],
        "construction_rule_id": case["construction_rule_id"],
        "oracle_package_group": case["oracle_package_group"],
        "proof_assembly_shape": case["proof_assembly_shape"],
    }


def _merge_lemma_graph_if_ready(
    *,
    split_plan,
    certificate: LeanLemmaGraphCertificate,
    parent_payload_ref: ArtifactRef,
    node_proofs: list[LeanLemmaGraphProofInput],
    store: ArtifactStore,
    environment_manifest: LeanEnvironmentManifest,
    case_id: str,
    force: bool = False,
    checker: LeanChecker = check_lean_proof,
) -> JsonObject:
    required_slot_count = len(split_plan.merge_plan.required_slots)
    if len(node_proofs) != required_slot_count and not force:
        return {
            "status": "blocked",
            "required_slot_count": required_slot_count,
            "merge_ready_child_count": len(node_proofs),
            "root_checker_accepted": False,
            "reason": "missing_or_rejected_lemma_node_proof",
        }
    try:
        result = merge_lean_lemma_graph_proofs(
            merge_plan=split_plan.merge_plan,
            lemma_graph_certificate=certificate,
            parent_theorem_payload_ref=parent_payload_ref,
            node_proofs=node_proofs,
            artifact_store=store,
            environment_manifest=environment_manifest,
            merge_unit_id=f"paper_lean_lemma_graph_merge_unit_{case_id}",
            request_id=f"paper_lean_lemma_graph_merge_request_{case_id}",
            created_at=NOW,
            checker=checker,
        )
    except ValueError as exc:
        return {
            "status": "failed",
            "merge_error": str(exc),
            "root_checker_accepted": False,
            "environment_digest": environment_manifest.environment_digest,
            "premature_merge_attempted": force,
        }
    return {
        "status": "completed" if result.accepted else "failed",
        "root_checker_accepted": result.accepted,
        "environment_digest": result.root_checker_report.environment_ref.environment_digest,
        "merge_rule_id": certificate.rule_id,
        "merge_result_ref": (
            result.merge_result_ref.to_dict()
            if result.merge_result_ref is not None
            else None
        ),
        "root_proof_candidate_ref": result.root_proof_candidate_ref.to_dict(),
        "merge_proof_candidate_ref": result.root_proof_candidate_ref.to_dict(),
        "root_checker_report_ref": (
            result.root_checker_report.report_ref.to_dict()
            if result.root_checker_report.report_ref is not None
            else None
        ),
        "root_proof_artifact_ref": (
            result.root_proof_artifact_ref.to_dict()
            if result.root_proof_artifact_ref is not None
            else None
        ),
        "node_proof_refs": {
            node_id: ref.to_dict()
            for node_id, ref in sorted(result.node_proof_refs.items())
        },
    }


def _lemma_graph_run_metadata(
    *,
    case: JsonObject,
    certificate: LeanLemmaGraphCertificate,
    certificate_ref: ArtifactRef,
    split_plan,
) -> JsonObject:
    return {
        "schema_version": "tokenshare.paper_lean_lemma_graph_run_metadata.v1",
        "case_id": case["case_id"],
        "preflight_status": case.get("preflight_status"),
        "paper_difficulty": case["paper_difficulty"],
        "topic_family": case["topic_family"],
        "topic_family_version": case["topic_family_version"],
        "construction_rule_id": case["construction_rule_id"],
        "oracle_package_group": case["oracle_package_group"],
        "proof_assembly_shape": case["proof_assembly_shape"],
        "root_node_id": certificate.root_node_id,
        "lemma_node_count": len(certificate.lemma_nodes),
        "dependency_edge_count": len(certificate.dependency_edges),
        "certificate_ref": certificate_ref.to_dict(),
        "certificate_digest": certificate.certificate_digest,
        "merge_plan_digest": split_plan.merge_plan.merge_plan_header["merge_plan_digest"],
        "proposal_digest": split_plan.proposal.proposal_header["proposal_digest"],
        "node_ids": [str(node["node_id"]) for node in certificate.lemma_nodes],
        "dependency_edges": copy.deepcopy(case["dependency_edges"]),
    }


def _blocked_lemma_graph_frontier_result(
    *,
    case: JsonObject,
    condition: PaperExperimentCondition,
    run_root: Path,
    store: ArtifactStore,
    real_transport: bool,
    secret_values: tuple[str, ...],
) -> LeanPaperRunResult:
    paper_metadata = _validated_condition_paper_metadata(
        condition=condition,
        case=case,
    )
    run_evidence = _run_evidence(
        store=store,
        real_transport=real_transport,
        transport_kind="ai_api" if real_transport else "scripted",
        secret_values=secret_values,
    )
    run_evidence["lean_lemma_graph"] = {
        "schema_version": "tokenshare.paper_lean_lemma_graph_run_metadata.v1",
        "case_id": case["case_id"],
        "preflight_status": case.get("preflight_status"),
        "paper_difficulty": case["paper_difficulty"],
        "topic_family": case["topic_family"],
        "topic_family_version": case["topic_family_version"],
        "construction_rule_id": case["construction_rule_id"],
        "oracle_package_group": case["oracle_package_group"],
        "proof_assembly_shape": case["proof_assembly_shape"],
        "root_node_id": case["merge_plan_shape"]["root_node_id"],
        "lemma_node_count": len(case["lemma_graph"]["nodes"]),
        "dependency_edge_count": len(case["dependency_edges"]),
        "structured_blocked": True,
    }
    eligibility = evaluate_paper_eligibility(attempts=[], run_evidence=run_evidence)
    task_result = PaperTaskResult(
        condition_id=condition.condition_id,
        repeat_id=condition.repeat_id,
        task_id=f"paper_lean_{case['case_id']}",
        domain="lean_proof",
        difficulty=str(case["difficulty"]),
        **paper_metadata,
        root_status=PaperTaskStatus.BLOCKED,
        accepted_validity=False,
        failure_stage=PaperFailureStage.CATALOG,
        failure_kind=PaperFailureKind.INTERNAL_ERROR,
        attempt_count=0,
        provider_attempt_count=0,
        wall_clock_ms=0,
        total_tokens=0,
        cost_estimate=0.0,
        event_refs=[],
        artifact_refs=[],
        paper_eligible=eligibility.paper_eligible,
    )
    result = LeanPaperRunResult(
        condition=condition,
        case_id=str(case["case_id"]),
        task_result=task_result,
        attempt_results=(),
        eligibility_report=eligibility,
        split_summary={
            "split_status": "blocked",
            "split_kind": str(case["expected_split_kind"]),
            "child_count": 0,
            "lemma_node_count": len(case["lemma_graph"]["nodes"]),
            "dependency_edge_count": len(case["dependency_edges"]),
            "root_node_id": case["merge_plan_shape"]["root_node_id"],
            "paper_difficulty": case["paper_difficulty"],
            "topic_family": case["topic_family"],
            "proof_assembly_shape": case["proof_assembly_shape"],
            "reason": "structured_blocked_no_oracle_frontier_stress",
        },
        child_results=(),
        merge_summary={
            "status": "blocked",
            "reason": "structured_blocked_no_oracle_frontier_stress",
            "root_checker_accepted": False,
            "required_slot_count": 0,
            "merge_ready_child_count": 0,
        },
        run_evidence=run_evidence,
        output_root=run_root.as_posix(),
    )
    _write_case_outputs(run_root, result)
    return result


def _save_parent_payload(
    *,
    store: ArtifactStore,
    case_id: str,
    payload: LeanTheoremPayload,
) -> ArtifactRef:
    return store.save_json(
        payload.to_dict(),
        artifact_id=f"paper_lean_parent_payload_{case_id}",
        artifact_type="LeanTheoremPayload",
        artifact_schema_id="lean_proof.theorem_payload",
        artifact_schema_version="v1",
        source={"kind": "lean_paper_adapter", "case_id": case_id},
        metadata={"theorem_name": payload.theorem_name},
        created_at=NOW,
    )


def _save_usage_artifact(
    *,
    store: ArtifactStore,
    case_id: str,
    index: int,
    submission,
) -> ArtifactRef:
    body = {
        "schema_version": "tokenshare.paper_ai_usage.v1",
        "submission_id": submission.submission_id,
        "request_id": submission.request_id,
        **dict(submission.usage_summary or {}),
    }
    return store.save_json(
        body,
        artifact_id=f"paper_lean_usage_{case_id}_{index}",
        artifact_type="AIUsageSummary",
        artifact_schema_id="tokenshare.paper_ai_usage",
        artifact_schema_version="v1",
        source={"kind": "lean_paper_adapter", "case_id": case_id},
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
            "kind": "lean_paper_adapter",
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


def _paper_attempt_result(
    *,
    store: ArtifactStore,
    condition: PaperExperimentCondition,
    case_id: str,
    index: int,
    request: ExecutionRequest,
    request_ref: ArtifactRef,
    submission,
    usage_ref: ArtifactRef,
    attempt_status: PaperAttemptStatus,
    planned_ai_unit_id: str,
    paper_difficulty: str | None = None,
    topic_family: str | None = None,
    topic_family_version: str | None = None,
    construction_rule_id: str | None = None,
    oracle_package_group: str | None = None,
    proof_assembly_shape: str | None = None,
    lemma_node_id: str | None = None,
    slot_key: str | None = None,
    dependency_path: list[str] | None = None,
    model_execution_record_ref: ArtifactRef | None = None,
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
        worker_id=f"worker_lean_paper_{index}",
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
        paper_difficulty=paper_difficulty,
        topic_family=topic_family,
        topic_family_version=topic_family_version,
        construction_rule_id=construction_rule_id,
        oracle_package_group=oracle_package_group,
        proof_assembly_shape=proof_assembly_shape,
        lemma_node_id=lemma_node_id,
        slot_key=slot_key,
        dependency_path=dependency_path,
    )


def _child_record(
    *,
    child_key: str,
    child_payload: LeanTheoremPayload,
    child_payload_ref: ArtifactRef,
    submission,
    proof_candidate_ref: ArtifactRef | None,
    proof_result: LeanChildProofResult | None,
) -> JsonObject:
    checker_report = proof_result.checker_report if proof_result is not None else None
    return {
        "child_logical_key": child_key,
        "statement_source": child_payload.statement_source,
        "child_payload_ref": child_payload_ref.to_dict(),
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
        "checker": {
            "accepted": proof_result.accepted if proof_result is not None else False,
            "failure_kind": proof_result.failure_kind if proof_result is not None else None,
            "environment_digest": (
                checker_report.environment_ref.environment_digest
                if checker_report is not None
                else None
            ),
            "report_ref": (
                checker_report.report_ref.to_dict()
                if checker_report is not None and checker_report.report_ref is not None
                else None
            ),
            "proof_artifact_ref": (
                checker_report.proof_artifact_ref.to_dict()
                if checker_report is not None and checker_report.proof_artifact_ref is not None
                else None
            ),
        },
    }


def _merge_children_if_ready(
    *,
    split_plan,
    parent_payload_ref: ArtifactRef,
    child_proofs: list[LeanProofMergeInput],
    store: ArtifactStore,
    environment_manifest: LeanEnvironmentManifest,
    case_id: str,
    force: bool = False,
    checker: LeanChecker = check_lean_proof,
) -> JsonObject:
    if (
        len(child_proofs) != len(split_plan.merge_plan.required_slots)
        and not force
    ):
        return {
            "status": "blocked",
            "required_slot_count": len(split_plan.merge_plan.required_slots),
            "merge_ready_child_count": len(child_proofs),
        }
    try:
        result = merge_lean_child_proofs(
            merge_plan=split_plan.merge_plan,
            split_certificate=split_plan.certificate,
            parent_theorem_payload_ref=parent_payload_ref,
            child_proofs=child_proofs,
            artifact_store=store,
            environment_manifest=environment_manifest,
            merge_unit_id=f"paper_lean_merge_unit_{case_id}",
            request_id=f"paper_lean_merge_request_{case_id}",
            created_at=NOW,
            checker=checker,
        )
    except ValueError as exc:
        return {
            "status": "failed",
            "merge_error": str(exc),
            "root_checker_accepted": False,
            "environment_digest": environment_manifest.environment_digest,
            "premature_merge_attempted": force,
        }
    return {
        "status": "completed" if result.accepted else "failed",
        "root_checker_accepted": result.accepted,
        "environment_digest": result.root_checker_report.environment_ref.environment_digest,
        "merge_rule_id": result.merge_rule_id,
        "merge_result_ref": (
            result.merge_result_ref.to_dict()
            if result.merge_result_ref is not None
            else None
        ),
        "merge_proof_candidate_ref": result.merge_proof_candidate_ref.to_dict(),
        "root_checker_report_ref": (
            result.root_checker_report.report_ref.to_dict()
            if result.root_checker_report.report_ref is not None
            else None
        ),
        "root_proof_artifact_ref": (
            result.root_proof_artifact_ref.to_dict()
            if result.root_proof_artifact_ref is not None
            else None
        ),
        "child_proof_refs": {
            child_key: ref.to_dict()
            for child_key, ref in sorted(result.child_proof_refs.items())
        },
    }


def _blocked_split_result(
    *,
    case: JsonObject,
    condition: PaperExperimentCondition,
    run_root: Path,
    store: ArtifactStore,
    split_summary: JsonObject,
    real_transport: bool,
    secret_values: tuple[str, ...],
) -> LeanPaperRunResult:
    paper_metadata = _validated_condition_paper_metadata(
        condition=condition,
        case=case,
    )
    run_evidence = _run_evidence(
        store=store,
        real_transport=real_transport,
        transport_kind="ai_api" if real_transport else "scripted",
        secret_values=secret_values,
    )
    eligibility = evaluate_paper_eligibility(attempts=[], run_evidence=run_evidence)
    task_result = PaperTaskResult(
        condition_id=condition.condition_id,
        repeat_id=condition.repeat_id,
        task_id=f"paper_lean_{case['case_id']}",
        domain="lean_proof",
        difficulty=str(case["difficulty"]),
        **paper_metadata,
        root_status=PaperTaskStatus.BLOCKED,
        accepted_validity=False,
        failure_stage=PaperFailureStage.SPLIT,
        failure_kind=PaperFailureKind.INTERNAL_ERROR,
        attempt_count=0,
        provider_attempt_count=0,
        wall_clock_ms=0,
        total_tokens=0,
        cost_estimate=0.0,
        event_refs=[],
        artifact_refs=[],
        paper_eligible=False,
    )
    result = LeanPaperRunResult(
        condition=condition,
        case_id=str(case["case_id"]),
        task_result=task_result,
        attempt_results=(),
        eligibility_report=eligibility,
        split_summary=split_summary,
        child_results=(),
        merge_summary={"status": "blocked", "reason": "split_unsupported"},
        run_evidence=run_evidence,
        output_root=run_root.as_posix(),
    )
    _write_case_outputs(run_root, result)
    return result


def _split_summary(split_report) -> JsonObject:
    certificate = split_report.certificate
    return {
        "split_status": split_report.status.value,
        "split_rule_id": certificate.rule_id if certificate is not None else None,
        "split_kind": certificate.split_kind if certificate is not None else None,
        "child_count": len(certificate.child_goals) if certificate is not None else 0,
        "certificate_ref": (
            split_report.certificate_ref.to_dict()
            if split_report.certificate_ref is not None
            else None
        ),
        "report_ref": (
            split_report.report_ref.to_dict()
            if split_report.report_ref is not None
            else None
        ),
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
        source={"kind": "lean_paper_adapter"},
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
        source={"kind": "lean_paper_adapter"},
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


def _attempt_status_from_submission(result_kind: str) -> PaperAttemptStatus:
    if result_kind == "succeeded":
        return PaperAttemptStatus.SUCCEEDED
    if result_kind == "parse_failed":
        return PaperAttemptStatus.PARSE_FAILED
    if result_kind in {"rate_limited", "provider_error", "auth_error", "connection_error"}:
        return PaperAttemptStatus.PROVIDER_ERROR
    return PaperAttemptStatus.PROVIDER_ERROR


def _evaluate_lean_paper_eligibility(
    *,
    attempts: list[PaperAttemptResult],
    run_evidence: JsonObject,
    checker: LeanChecker,
) -> PaperEligibilityReport:
    report = evaluate_paper_eligibility(
        attempts=attempts,
        run_evidence=run_evidence,
    )
    if checker is check_lean_proof:
        return report
    reasons = tuple(
        dict.fromkeys(
            (*report.ineligibility_reasons, "non_production_checker_backend")
        )
    )
    return replace(
        report,
        paper_eligible=False,
        ineligibility_reasons=reasons,
    )


def _task_failure_from_child_evidence(
    *,
    attempts: list[PaperAttemptResult],
    child_records: list[JsonObject],
    merge_summary: JsonObject,
) -> tuple[PaperFailureStage, PaperFailureKind]:
    if any(
        attempt.attempt_status == PaperAttemptStatus.MODEL_IDENTITY_MISMATCH
        for attempt in attempts
    ):
        return PaperFailureStage.AUDIT, PaperFailureKind.MODEL_IDENTITY_MISMATCH
    if any(attempt.attempt_status == PaperAttemptStatus.PARSE_FAILED for attempt in attempts):
        return PaperFailureStage.PARSE, PaperFailureKind.PARSE_FAILURE
    if any(attempt.attempt_status == PaperAttemptStatus.PROVIDER_ERROR for attempt in attempts):
        return PaperFailureStage.PROVIDER, PaperFailureKind.PROVIDER_ERROR
    if any(attempt.attempt_status == PaperAttemptStatus.CHECKER_REJECTED for attempt in attempts):
        return PaperFailureStage.CHECKER, PaperFailureKind.CHECKER_REJECTED
    if any(record["checker"]["accepted"] is False for record in child_records):
        return PaperFailureStage.CHECKER, PaperFailureKind.CHECKER_REJECTED
    if merge_summary.get("root_checker_accepted") is False:
        return PaperFailureStage.CHECKER, PaperFailureKind.CHECKER_REJECTED
    return PaperFailureStage.MERGE, PaperFailureKind.INTERNAL_ERROR


def _task_artifact_refs(
    *,
    attempts: list[PaperAttemptResult],
    child_records: list[JsonObject],
    merge_summary: JsonObject,
) -> list[JsonObject]:
    refs: list[JsonObject] = []
    for attempt in attempts:
        for ref in (
            attempt.raw_output_ref,
            attempt.parsed_output_ref,
            attempt.parse_failure_ref,
            attempt.model_execution_record_ref,
        ):
            if ref is not None:
                refs.append(ref)
    for record in child_records:
        for ref in (
            record.get("node_payload_ref"),
            record.get("child_payload_ref"),
            record.get("candidate_output_ref"),
            record["checker"].get("report_ref"),
            record["checker"].get("proof_artifact_ref"),
        ):
            if ref is not None:
                refs.append(ref)
    for key in (
        "merge_result_ref",
        "merge_proof_candidate_ref",
        "root_proof_candidate_ref",
        "root_checker_report_ref",
        "root_proof_artifact_ref",
    ):
        ref = merge_summary.get(key)
        if ref is not None:
            refs.append(ref)
    return refs


def _last_provenance_attempt(*, store: ArtifactStore, submission) -> JsonObject:
    if submission.provenance_ref is None:
        return {}
    provenance = _read_json_ref(store, submission.provenance_ref)
    attempts = provenance.get("attempts", [])
    if not isinstance(attempts, list) or not attempts:
        return {}
    last = attempts[-1]
    return dict(last) if isinstance(last, dict) else {}


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


def _user_prompt(body: JsonObject) -> str:
    messages = body.get("messages", [])
    if not isinstance(messages, list):
        return ""
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "user":
            return str(message.get("content", ""))
    return ""


def _prompt_value(prompt: str, *, label: str) -> str:
    match = re.search(rf"^{re.escape(label)}:\s*(.+)$", prompt, flags=re.MULTILINE)
    if match:
        return match.group(1).strip()
    raise ValueError(f"scripted Lean paper proof transport could not find {label}")


def _expected_proof_candidate_id(prompt: str) -> str:
    match = re.search(
        r"^Use this exact proof_candidate_id:\s*(.+)$",
        prompt,
        flags=re.MULTILINE,
    )
    if match:
        return match.group(1).strip()
    raise ValueError("scripted Lean paper proof transport could not find proof_candidate_id")


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


def _write_case_outputs(root: Path, result: LeanPaperRunResult) -> None:
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


def _safe_id(value: str) -> str:
    return "".join(character if character.isalnum() or character == "_" else "_" for character in value)


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
