"""Lean paper experiment adapter.

该模块只做实验层编排：paper catalog case -> Lean 插件确定性 split helper ->
AIAPIExecutor provider attempt -> Lean proof parser -> 本地 Lean checker ->
Lean merge/root recheck。它不让 AI 决定拆分，也不把 scripted transport 标成
论文可采信真实结果。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tokenshare.core.models import (
    ArtifactRef,
    JsonObject,
    TaskState,
    TaskUnit,
)
from tokenshare.executors.ai_api import AIAPIExecutor
from tokenshare.executors.ai_api_config import AIAPIExecutorConfig, load_ai_api_config
from tokenshare.executors.ai_api_transport import UrlLibSiliconFlowTransport
from tokenshare.executors.contracts import EnvironmentRef, ExecutionRequest
from tokenshare.experiments.paper_catalog import (
    default_lean_paper_environment_manifest,
    lean_theorem_payload_from_case,
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
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger


NOW = "2026-07-14T00:00:00Z"
AI_EXECUTOR_ID = "executor_ai_api"
AI_EXECUTOR_VERSION = "0.1.0"
FAKE_KEY_ENV = "TOKENSHARE_LEAN_PAPER_FAKE_KEY"


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
        entry,
        api_key: str,
        body: JsonObject,
        timeout_seconds: int,
    ):
        user_prompt = _user_prompt(body)
        theorem_payload_digest = _prompt_value(
            user_prompt,
            label="Theorem payload digest",
        )
        statement_source = _prompt_value(user_prompt, label="Statement source")
        proof_candidate_id = _expected_proof_candidate_id(user_prompt)
        proof_source = self.proof_sources_by_statement.get(
            statement_source,
            _scripted_proof_source(statement_source),
        )
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
                "entry_id": entry.entry_id,
                "model": entry.model,
                "timeout_seconds": timeout_seconds,
                "api_key_seen": bool(api_key),
                "statement_source": statement_source,
                "body": json.loads(json.dumps(body, ensure_ascii=False, sort_keys=True)),
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
) -> LeanPaperRunResult:
    """Run one Lean paper catalog case through split children and checker merge."""

    _validate_lean_case_for_adapter(case)
    if condition.domain != "lean_proof":
        raise ValueError("condition domain must be lean_proof")
    if condition.difficulty != case["difficulty"]:
        raise ValueError("condition difficulty must match Lean case difficulty")
    if real_transport and ai_api_config is None:
        raise ValueError("real transport Lean paper runs require ai_api_config")
    if real_transport and transport is not None and type(transport) is not UrlLibSiliconFlowTransport:
        raise ValueError("real transport Lean paper runs require UrlLibSiliconFlowTransport")
    if not real_transport and isinstance(transport, UrlLibSiliconFlowTransport):
        raise ValueError("UrlLibSiliconFlowTransport requires real_transport=True")

    case_id = str(case["case_id"])
    root = Path(output_root)
    run_root = root / case_id
    store = ArtifactStore(run_root)
    ledger = EventLedger(run_root / "events" / "event_log.jsonl")
    config = _prepare_config(
        ai_api_config if ai_api_config is not None else _default_scripted_config(entry_id),
        entry_id=entry_id,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
    )
    active_transport = transport
    if active_transport is None:
        active_transport = UrlLibSiliconFlowTransport() if real_transport else ScriptedLeanPaperProofTransport()
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
    for index, (child_key, child_payload_ref) in enumerate(
        sorted(split_plan.child_payload_refs_by_logical_key.items())
    ):
        child_result = _run_child_attempt(
            case=case,
            condition=condition,
            child_key=child_key,
            child_payload_ref=child_payload_ref,
            split_certificate=split_plan.certificate,
            store=store,
            ledger=ledger,
            config=config,
            transport=active_transport,
            index=index,
            timeout_seconds=timeout_seconds,
            max_tokens=max_tokens,
            environment_manifest=environment_manifest,
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

    merge_summary = _merge_children_if_ready(
        split_plan=split_plan,
        parent_payload_ref=parent_payload_ref,
        child_proofs=child_proofs,
        store=store,
        environment_manifest=environment_manifest,
        case_id=case_id,
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
    )
    eligibility = evaluate_paper_eligibility(
        attempts=attempts,
        run_evidence=run_evidence,
    )
    task_result = PaperTaskResult(
        condition_id=condition.condition_id,
        repeat_id=condition.repeat_id,
        task_id=f"paper_lean_{case_id}",
        domain="lean_proof",
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
    transport: Any,
    index: int,
    timeout_seconds: int,
    max_tokens: int,
    environment_manifest: LeanEnvironmentManifest,
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
        parser=lambda raw, *, raw_output_ref_summary, created_at: parse_lean_proof_candidate_ai_output(
            raw,
            theorem_payload=child_payload,
            raw_output_ref_summary=raw_output_ref_summary,
            created_at=created_at,
        ),
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
    proof_candidate_ref = submission.candidate_output_refs.get(PROOF_CANDIDATE_OUTPUT_NAME)
    proof_result: LeanChildProofResult | None = None
    attempt_status = _attempt_status_from_submission(submission.result_kind)
    if proof_candidate_ref is not None:
        proof_result = check_lean_child_proof(
            child_logical_key=child_key,
            split_certificate=split_certificate,
            child_payload_ref=child_payload_ref,
            proof_candidate_ref=proof_candidate_ref,
            artifact_store=store,
            environment_manifest=environment_manifest,
            request_id=f"paper_lean_child_checker_{case_id}_{_safe_id(child_key)}",
            created_at=NOW,
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
    )
    record = _child_record(
        child_key=child_key,
        child_payload=child_payload,
        child_payload_ref=child_payload_ref,
        submission=submission,
        proof_candidate_ref=proof_candidate_ref,
        proof_result=proof_result,
    )
    return {"attempt": attempt, "record": record, "proof_result": proof_result}


def _build_child_execution_request(
    *,
    store: ArtifactStore,
    case: JsonObject,
    condition: PaperExperimentCondition,
    child_key: str,
    child_payload: LeanTheoremPayload,
    child_payload_ref: ArtifactRef,
    index: int,
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
        capability_snapshot={"executor": "ai_api", "provider_family": "siliconflow"},
        task_unit_snapshot=_child_task_unit(
            task_id=task_id,
            unit_id=unit_id,
            child_payload_ref=child_payload_ref,
            case=case,
        ).to_dict(),
        input_artifact_refs={"child_theorem_payload": child_payload_ref},
        output_contract=_proof_candidate_output_contract(),
        hard_requirements={"executor": "ai_api", "provider_family": "siliconflow"},
        soft_hints={"temperature": 0.0, "paper_condition_id": condition.condition_id},
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
        plugin_payload={
            "schema_version": "lean_proof.subgoal_plugin_payload.v1",
            "case_id": case["case_id"],
            "validator_policy_id": CHECKER_VALIDATOR_POLICY_ID,
        },
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
    max_tokens: int,
    timeout_seconds: int,
) -> AIAPIExecutorConfig:
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
        error_kind=(submission.error or {}).get("kind") if submission.error else None,
        fault_injection_ref=None,
        paper_eligible=False,
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
) -> JsonObject:
    if len(child_proofs) != len(split_plan.merge_plan.required_slots):
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
        )
    except ValueError as exc:
        return {
            "status": "failed",
            "merge_error": str(exc),
            "root_checker_accepted": False,
            "environment_digest": environment_manifest.environment_digest,
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
) -> LeanPaperRunResult:
    run_evidence = _run_evidence(
        store=store,
        real_transport=real_transport,
        transport_kind="ai_api" if real_transport else "scripted",
    )
    eligibility = evaluate_paper_eligibility(attempts=[], run_evidence=run_evidence)
    task_result = PaperTaskResult(
        condition_id=condition.condition_id,
        repeat_id=condition.repeat_id,
        task_id=f"paper_lean_{case['case_id']}",
        domain="lean_proof",
        difficulty=str(case["difficulty"]),
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
) -> JsonObject:
    transport_ref = store.save_json(
        {
            "schema_version": "tokenshare.paper_transport_evidence.v1",
            "real_transport": real_transport,
            "transport_kind": transport_kind,
            "config_source": (
                "local_gitignored_config" if real_transport else "scripted_fixture"
            ),
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
    scanned_ids = _artifact_ids(store)
    secret_scan_ref = store.save_json(
        {
            "schema_version": "tokenshare.paper_secret_scan_report.v1",
            "status": "pending",
            "leak_count": 0,
            "secret_checked_count": 0,
            "scanned_artifact_ids": scanned_ids,
            "scan_scope": "not_wired_in_lean_adapter_slice",
        },
        artifact_id="paper_secret_scan_report",
        artifact_type="SecretScanReport",
        artifact_schema_id="tokenshare.paper_secret_scan_report",
        artifact_schema_version="v1",
        source={"kind": "lean_paper_adapter"},
        metadata={},
        created_at=NOW,
    )
    artifact_manifests = _artifact_manifests(store)
    scanned_ids = _artifact_ids(store)
    return {
        "schema_version": "tokenshare.paper_run_evidence.v1",
        "transport_evidence": {
            "schema_version": "tokenshare.paper_transport_evidence.v1",
            "real_transport": real_transport,
            "transport_kind": transport_kind,
            "config_source": (
                "local_gitignored_config" if real_transport else "scripted_fixture"
            ),
            "api_key_policy": "env_only",
            "executor_kind": "ai_api_executor",
            "evidence_ref": transport_ref.to_dict(),
        },
        "secret_scan_report": {
            "schema_version": "tokenshare.paper_secret_scan_report.v1",
            "status": "pending",
            "leak_count": 0,
            "secret_checked_count": 0,
            "scanned_artifact_ids": scanned_ids,
            "scan_scope": "not_wired_in_lean_adapter_slice",
            "report_ref": secret_scan_ref.to_dict(),
        },
        "artifact_manifests": artifact_manifests,
    }


def _attempt_status_from_submission(result_kind: str) -> PaperAttemptStatus:
    if result_kind == "succeeded":
        return PaperAttemptStatus.SUCCEEDED
    if result_kind == "parse_failed":
        return PaperAttemptStatus.PARSE_FAILED
    if result_kind in {"rate_limited", "provider_error", "auth_error", "connection_error"}:
        return PaperAttemptStatus.PROVIDER_ERROR
    return PaperAttemptStatus.PROVIDER_ERROR


def _task_failure_from_child_evidence(
    *,
    attempts: list[PaperAttemptResult],
    child_records: list[JsonObject],
    merge_summary: JsonObject,
) -> tuple[PaperFailureStage, PaperFailureKind]:
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
        for ref in (attempt.raw_output_ref, attempt.parsed_output_ref, attempt.parse_failure_ref):
            if ref is not None:
                refs.append(ref)
    for record in child_records:
        for ref in (
            record["checker"].get("report_ref"),
            record["checker"].get("proof_artifact_ref"),
        ):
            if ref is not None:
                refs.append(ref)
    for key in (
        "merge_result_ref",
        "merge_proof_candidate_ref",
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
