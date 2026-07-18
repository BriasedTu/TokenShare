"""Factorization paper experiment adapter.

该模块只做实验层编排：catalog case -> 插件确定性 range split ->
AIAPIExecutor provider attempt -> 插件 parser/verifier -> 插件 merge policy。
它不把 factorization 领域规则写进协议核心，也不把 scripted transport 标成
论文可采信真实结果。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from math import isqrt
from pathlib import Path
from typing import Any

from tokenshare.core.models import ArtifactRef, JsonObject, TaskState, TaskUnit
from tokenshare.executors.ai_api import AIAPIExecutor
from tokenshare.executors.ai_api_config import AIAPIExecutorConfig, load_ai_api_config
from tokenshare.executors.ai_api_transport import (
    UrlLibOpenAITransport,
    UrlLibSiliconFlowTransport,
)
from tokenshare.executors.contracts import EnvironmentRef, ExecutionRequest
from tokenshare.experiments.paper_model_identity import (
    ValidatedModelEndpointBinding,
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
from tokenshare.experiments.paper_unit_commitments import (
    factorization_range_plugin_payload,
)
from tokenshare.plugins.contracts import OutputContract
from tokenshare.plugins.factorization.descriptor import build_factorization_plugin_descriptor
from tokenshare.plugins.factorization.merge_policy import (
    RangeSlotMergeInput,
    merge_required_range_results,
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
)
from tokenshare.plugins.factorization.validator import (
    build_factor_search_instruction,
    parse_factorization_ai_output,
    verify_range_result,
)
from tokenshare.storage.artifacts import ArtifactStore


NOW = "2026-07-14T00:00:00Z"
AI_EXECUTOR_ID = "executor_ai_api"
AI_EXECUTOR_VERSION = "0.1.0"
FAKE_KEY_ENV = "TOKENSHARE_FACTORIZATION_PAPER_FAKE_KEY"
REAL_TRANSPORT_TYPES = (UrlLibSiliconFlowTransport, UrlLibOpenAITransport)


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
) -> FactorizationPaperRunResult:
    """Run one factorization paper catalog case through range children."""

    _validate_factorization_case_for_adapter(case)
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

    root_input_ref = _save_root_input(store, case)
    subject = _factor_integer_subject(case=case, root_input_ref=root_input_ref)
    split_plan = _build_split_plan(case=case, subject=subject)
    executor = AIAPIExecutor(
        executor_id=AI_EXECUTOR_ID,
        executor_version=AI_EXECUTOR_VERSION,
        artifact_store=store,
        config=config,
        transport=active_transport,
        parser=parse_factorization_ai_output,
    )

    range_records: list[JsonObject] = []
    attempts: list[PaperAttemptResult] = []
    for index, range_input in enumerate(split_plan.partition.ranges):
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
        range_result_body: JsonObject | None = None
        canonical_output_ref = None
        attempt_status = _attempt_status_from_submission(submission.result_kind)
        if model_identity_mismatch:
            attempt_status = PaperAttemptStatus.MODEL_IDENTITY_MISMATCH
        if candidate_ref is not None:
            range_result_body = _read_json_ref(store, candidate_ref)
            verification = verify_range_result(range_result_body, child_input=range_input)
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
                    {
                        "accepted": verification.accepted,
                        "status": verification.status,
                        "layer_summary": verification.layer_summary,
                        "failure_summary": verification.failure_summary,
                    }
                    if verification is not None
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
    if len(accepted_range_records) == len(range_records):
        merge_policy_result = merge_required_range_results(
            merge_plan=split_plan.merge_plan,
            slot_results=_slot_inputs_for_merge(split_plan, range_records),
            merge_unit_id=f"paper_merge_unit_{case_id}",
            created_at=NOW,
        )
        prime_result = merge_policy_result.prime_factorization_result
        merge_summary = {
            "status": "completed" if prime_result is not None else "blocked",
            "result_kind": merge_policy_result.merge_result.result_kind,
            "merge_result": merge_policy_result.merge_result.to_dict(),
            "expected_output_resolvable": merge_policy_result.expected_output_resolvable,
        }
        if prime_result is not None:
            _save_prime_factorization_result(store=store, case_id=case_id, result=prime_result)
    else:
        merge_summary = {
            "status": "blocked",
            "result_kind": None,
            "rejected_range_count": len(range_records) - len(accepted_range_records),
        }

    final_prime_factors = (
        [dict(item) for item in prime_result.to_dict()["prime_factors"]]
        if prime_result is not None
        else []
    )
    accepted_validity = _prime_factors_match_oracle(final_prime_factors, case)
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
    requested_child_count = int(case["split_params"]["requested_child_count"])
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
        entries = [validated_binding.selected_entry]
    else:
        entries = list(config.entries)
    if validated_binding is None and entry_id is not None:
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
    if not real_transport:
        if isinstance(transport, REAL_TRANSPORT_TYPES):
            raise ValueError(
                "UrlLibSiliconFlowTransport or UrlLibOpenAITransport requires "
                "real_transport=True"
            )
        return
    if ai_api_config is None:
        return
    if transport is None:
        return
    expected_type = _real_transport_type(ai_api_config.provider_family)
    if type(transport) is not expected_type:
        raise ValueError(
            "real transport factorization paper runs require "
            "UrlLibSiliconFlowTransport or UrlLibOpenAITransport matching "
            f"provider_family={ai_api_config.provider_family}"
        )


def _default_real_transport(config: AIAPIExecutorConfig):
    transport_type = _real_transport_type(config.provider_family)
    return transport_type()


def _real_transport_type(provider_family: str):
    if provider_family == "siliconflow":
        return UrlLibSiliconFlowTransport
    if provider_family == "openai":
        return UrlLibOpenAITransport
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
            "config_source": (
                "local_gitignored_config" if real_transport else "scripted_fixture"
            ),
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
