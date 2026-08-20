"""Phase 7 experimental AI API executor."""

from __future__ import annotations

import json
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from inspect import Parameter, signature
from time import perf_counter
from typing import Any, Callable, Literal

from tokenshare.core.models import ArtifactRef, JsonObject
from tokenshare.executors.ai_api_artifacts import (
    RAW_MODEL_OUTPUT_SCHEMA_V2,
    build_raw_model_identity_fields,
)
from tokenshare.executors.ai_api_config import AIAPIExecutorConfig, AIAPIProviderEntry
from tokenshare.executors.ai_api_hard_deadline import (
    HardDeadlineDispatchOutcome,
    prepare_provider_transport_hard_deadline_session,
)
from tokenshare.executors.ai_api_request_identity import (
    PreparedOutboundRequest,
    PreparedOutboundRequestFactory,
    validate_prepared_request,
)
from tokenshare.executors.ai_api_selector import AIProviderSelection, entries_by_attempt_order
from tokenshare.executors.ai_api_transport import (
    DeepSeekProviderError,
    OpenAIProviderError,
    SiliconFlowProviderError,
    build_deepseek_chat_body,
    build_openai_chat_body,
    build_siliconflow_chat_body,
    parse_deepseek_response,
    parse_openai_response,
    parse_siliconflow_response,
)
from tokenshare.executors.contracts import (
    ExecutionRequest,
    ExecutionSubmission,
    ExecutorDescriptor,
    ExecutorStatus,
)
from tokenshare.storage.artifacts import ArtifactStore


PROVIDER_FAILURE_TAXONOMY = (
    "timeout",
    "no_response",
    "connection_error",
    "rate_limited",
    "provider_error",
    "auth_error",
    "client_error",
    "invalid_output",
)


@dataclass(frozen=True, kw_only=True)
class PreparedDispatchEvidence:
    """一次 exact wire 调用的 acquisition-only 结果；不做 parser/protocol 判定。"""

    terminal_kind: Literal["success", "provider_failure"]
    failure_kind: str | None
    raw_response_json: JsonObject | None
    content_text: str | None
    reasoning_content: str | None
    provider_response_id: str | None
    finish_reason: str | None
    usage: JsonObject | None
    latency_ms: int | None
    http_status: int | None
    resolved_model: str | None
    response_model_status: str
    error_message: str | None
    transport_call_count: int = 1
    latency_timing_source: str = "provider_transport_observed"
    hard_deadline_evidence: JsonObject | None = None


@dataclass(frozen=True, kw_only=True)
class PreparedAIAPIOutboundRequest:
    """规划期与 executor 共用的纯 wire request 结果。"""

    prepared_request: PreparedOutboundRequest
    provider_request_identity: JsonObject


class _HardDeadlinePreDispatchError(RuntimeError):
    def __init__(self, outcome: HardDeadlineDispatchOutcome) -> None:
        super().__init__(outcome.error_message or "hard-deadline pre-dispatch failure")
        self.outcome = outcome


def persist_zero_provider_attempt_provenance(
    *,
    artifact_store: ArtifactStore,
    config: AIAPIExecutorConfig,
    executor_id: str,
    submission_id: str,
    request: ExecutionRequest,
    final_result_kind: str,
    submitted_at: str,
) -> ArtifactRef:
    """为 transport 前 fail-closed terminal 持久化显式零调用 provenance。"""

    return artifact_store.save_json(
        {
            "schema_version": "phase7.ai_provider_call_provenance.v2",
            "submission_id": submission_id,
            "request_id": request.request_id,
            "provider_family": config.provider_family,
            "config_digest": config.config_digest,
            "selection_record": _empty_selection_record(
                config=config,
                request=request,
                require_json_mode=False,
            ),
            "attempts": [],
            "provider_attempt_count": 0,
            "final_entry_id": None,
            "final_result_kind": final_result_kind,
            "secret_redaction": {
                "authorization_header": False,
                "api_key_value": False,
            },
        },
        artifact_id=f"ai_provider_provenance_{submission_id}",
        artifact_type="AIProviderCallProvenance",
        artifact_schema_id="phase7.ai_provider_call_provenance",
        artifact_schema_version="v2",
        source={"kind": "ai_api_executor", "request_id": request.request_id},
        metadata={"executor_id": executor_id},
        created_at=submitted_at,
    )


def validated_current_provider_attempt_count(
    *,
    store: ArtifactStore,
    submission: Any,
    usage_ref: ArtifactRef | Mapping[str, Any],
) -> int:
    """三方核对 submission、persisted usage 与 provenance 的显式调用数。"""

    def read_json_ref(ref: ArtifactRef | Mapping[str, Any]) -> JsonObject:
        artifact_ref = ArtifactRef.from_dict(ref) if isinstance(ref, Mapping) else ref
        return json.loads(store.read_bytes(artifact_ref).decode("utf-8"))

    submission_usage = submission.usage_summary
    persisted_usage = read_json_ref(usage_ref)
    if not isinstance(submission_usage, Mapping):
        raise ValueError("provider attempt count evidence is missing")
    counts: list[int] = []
    for evidence in (submission_usage, persisted_usage):
        value = evidence.get("provider_attempt_count")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("provider attempt count evidence is missing or invalid")
        counts.append(value)
    if counts[0] != counts[1]:
        raise ValueError("provider attempt count evidence is conflicting")

    source_classes = tuple(
        evidence.get("source_usage_class")
        for evidence in (submission_usage, persisted_usage)
    )
    if any(value is not None for value in source_classes):
        if source_classes != ("trace_attribution", "trace_attribution"):
            raise ValueError(
                "provider attempt count evidence has conflicting source domain"
            )
        if counts[0] != 0 or any(
            type(evidence.get("current_provider_call_count")) is not int
            or evidence.get("current_provider_call_count") != 0
            for evidence in (submission_usage, persisted_usage)
        ):
            raise ValueError(
                "provider attempt count evidence conflicts with trace domain"
            )
        return 0

    if submission.provenance_ref is None:
        raise ValueError("provider attempt count evidence is missing provenance inventory")
    provenance = read_json_ref(submission.provenance_ref)
    provider_attempts = provenance.get("attempts")
    if (
        not isinstance(provider_attempts, Sequence)
        or isinstance(provider_attempts, (str, bytes, bytearray))
        or any(not isinstance(item, Mapping) for item in provider_attempts)
    ):
        raise ValueError("provider attempt count evidence inventory is invalid")
    provenance_count = provenance.get("provider_attempt_count")
    if (
        isinstance(provenance_count, bool)
        or not isinstance(provenance_count, int)
        or provenance_count < 0
    ):
        raise ValueError("provider attempt count evidence is missing or invalid")
    if provenance_count != counts[0]:
        raise ValueError("provider attempt count evidence is conflicting")
    return counts[0]


def prepare_ai_api_outbound_request(
    *,
    config: AIAPIExecutorConfig,
    request: ExecutionRequest,
    prompt: Mapping[str, Any],
    entry: AIAPIProviderEntry,
) -> PreparedAIAPIOutboundRequest:
    """只从冻结输入构造 exact wire bytes；不读 secret、不持久化、不发送。"""

    if not isinstance(config, AIAPIExecutorConfig):
        raise TypeError("config must be AIAPIExecutorConfig")
    if not isinstance(request, ExecutionRequest):
        raise TypeError("request must be ExecutionRequest")
    if not isinstance(entry, AIAPIProviderEntry) or entry not in config.entries:
        raise ValueError("entry must belong to the prepared provider config")
    prompt_body = dict(prompt)
    require_json_mode = _require_json_mode_constraint(prompt_body)
    build_chat_body, _parse_provider_response = _provider_adapter(
        config.provider_family
    )
    body = build_chat_body(
        entry=entry,
        prompt_text=_provider_prompt_text(prompt_body),
        defaults=config.defaults,
        request_limits=request.limits,
        soft_hints=request.soft_hints or {},
        require_json_mode=require_json_mode,
    )
    request_identity = _provider_request_identity(
        provider_family=config.provider_family,
        entry=entry,
        body=body,
    )
    prepared_request = PreparedOutboundRequestFactory.prepare(
        body_obj=body,
        base_url=entry.base_url,
        endpoint=entry.endpoint,
        provider_config_digest=config.config_digest,
        entry_id=entry.entry_id,
        configured_model=entry.model,
        effective_controls_digest=str(
            request_identity["effective_request_controls_digest"]
        ),
        plugin_id=str(request.plugin.get("plugin_id", "unknown")),
        plugin_version=str(request.plugin.get("plugin_version", "unknown")),
        prompt_profile_id=str(prompt_body.get("fixture_profile", "unknown")),
        prompt_serialization_schema=str(
            prompt_body.get("schema_version", "phase3.prompt_package.v1")
        ),
        body_serialization_schema=(
            f"{config.provider_family}.chat_completions.v1"
        ),
        case_id=_stable_case_id(request),
        planned_ai_unit_id=_stable_planned_ai_unit_id(request),
        sample_slot_index=_stable_slot_index(request, "sample_slot_index"),
        replacement_slot=_stable_slot_index(request, "replacement_slot"),
    )
    validate_prepared_request(prepared_request)
    return PreparedAIAPIOutboundRequest(
        prepared_request=prepared_request,
        provider_request_identity=request_identity,
    )


def dispatch_prepared_request_once(
    *,
    prepared_request: PreparedOutboundRequest,
    provider_family: str,
    transport: Any,
    api_key: str,
    timeout_seconds: int,
    on_transport_start: Callable[[], None] | None = None,
) -> PreparedDispatchEvidence:
    """发送已经冻结的 endpoint/body bytes 恰好一次并保留 provider taxonomy。"""

    prepared = validate_prepared_request(prepared_request)
    if provider_family not in {"siliconflow", "openai", "deepseek"}:
        raise ValueError(f"unsupported ai api provider_family: {provider_family}")
    if not isinstance(api_key, str) or not api_key:
        raise ValueError("resolved API key must be non-empty")
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be positive")
    _build_body, parse_provider_response = _provider_adapter(provider_family)
    exact_transport = _exact_transport_for_provider(transport, provider_family)
    started = perf_counter()
    response: Any | None = None
    deadline_outcome: HardDeadlineDispatchOutcome | None = None
    try:
        if getattr(exact_transport, "tokenshare_hard_total_deadline", False) is True:
            deadline_outcome = _dispatch_hard_deadline_through_exact_transport(
                exact_transport=exact_transport,
                provider_family=provider_family,
                api_key=api_key,
                body_bytes=prepared.body_bytes,
                normalized_absolute_endpoint=prepared.normalized_absolute_endpoint,
                content_type="application/json",
                hard_total_seconds=float(timeout_seconds),
                on_transport_start=on_transport_start,
            )
            if deadline_outcome.status != "completed":
                return _prepared_failure_evidence(
                    failure_kind=(deadline_outcome.failure_kind or "provider_error"),
                    latency_ms=deadline_outcome.provider_latency_ms,
                    http_status=deadline_outcome.http_status,
                    message=(
                        deadline_outcome.error_message
                        or "provider child returned no response"
                    ),
                    transport_call_count=deadline_outcome.transport_call_count,
                    latency_timing_source=_hard_deadline_latency_timing_source(
                        deadline_outcome
                    ),
                    hard_deadline_evidence=deadline_outcome.quiescence_evidence,
                )
            response = deadline_outcome.response
        else:
            if on_transport_start is not None:
                on_transport_start()
            response = exact_transport.post_chat_completion(
                api_key=api_key,
                body_bytes=prepared.body_bytes,
                normalized_absolute_endpoint=prepared.normalized_absolute_endpoint,
                content_type="application/json",
                timeout_seconds=timeout_seconds,
            )
        parsed = parse_provider_response(response)
    except TimeoutError:
        return _prepared_failure_evidence(
            failure_kind="timeout",
            latency_ms=int((perf_counter() - started) * 1000),
            http_status=None,
            message="provider request timed out",
            hard_deadline_evidence=(
                None
                if deadline_outcome is None
                else deadline_outcome.quiescence_evidence
            ),
        )
    except OSError as exc:
        return _prepared_failure_evidence(
            failure_kind="connection_error",
            latency_ms=int((perf_counter() - started) * 1000),
            http_status=None,
            message=_redact_single_secret(str(exc), api_key),
            hard_deadline_evidence=(
                None
                if deadline_outcome is None
                else deadline_outcome.quiescence_evidence
            ),
        )
    except (SiliconFlowProviderError, OpenAIProviderError, DeepSeekProviderError) as exc:
        failure_kind = (
            exc.error_kind
            if exc.error_kind in PROVIDER_FAILURE_TAXONOMY
            else "provider_error"
        )
        return _prepared_failure_evidence(
            failure_kind=failure_kind,
            latency_ms=(
                int((perf_counter() - started) * 1000)
                if deadline_outcome is None
                else deadline_outcome.provider_latency_ms
            ),
            http_status=exc.http_status,
            message=_redact_single_secret(exc.message, api_key),
            transport_call_count=(
                1
                if deadline_outcome is None
                else deadline_outcome.transport_call_count
            ),
            latency_timing_source=(
                "provider_transport_observed"
                if deadline_outcome is None
                else "provider_child_observed"
            ),
            hard_deadline_evidence=(
                None
                if deadline_outcome is None
                else deadline_outcome.quiescence_evidence
            ),
        )
    return PreparedDispatchEvidence(
        terminal_kind="success",
        failure_kind=None,
        raw_response_json=dict(parsed.raw_response_json),
        content_text=parsed.content_text,
        reasoning_content=getattr(parsed, "reasoning_content", None),
        provider_response_id=parsed.provider_response_id,
        finish_reason=parsed.finish_reason,
        usage=(None if parsed.usage is None else dict(parsed.usage)),
        latency_ms=(
            int((perf_counter() - started) * 1000)
            if deadline_outcome is None
            else deadline_outcome.provider_latency_ms
        ),
        http_status=(None if response is None else int(response.status_code)),
        resolved_model=getattr(parsed, "resolved_model", None),
        response_model_status=str(
            getattr(parsed, "response_model_status", "missing")
        ),
        error_message=None,
        transport_call_count=(
            1 if deadline_outcome is None else deadline_outcome.transport_call_count
        ),
        latency_timing_source=(
            "provider_transport_observed"
            if deadline_outcome is None
            else "provider_child_observed"
        ),
        hard_deadline_evidence=(
            None
            if deadline_outcome is None
            else deadline_outcome.quiescence_evidence
        ),
    )


def _prepared_failure_evidence(
    *,
    failure_kind: str,
    latency_ms: int | None,
    http_status: int | None,
    message: str,
    transport_call_count: int = 1,
    latency_timing_source: str = "provider_transport_observed",
    hard_deadline_evidence: JsonObject | None = None,
) -> PreparedDispatchEvidence:
    return PreparedDispatchEvidence(
        terminal_kind="provider_failure",
        failure_kind=failure_kind,
        raw_response_json=None,
        content_text=None,
        reasoning_content=None,
        provider_response_id=None,
        finish_reason=None,
        usage=None,
        latency_ms=(None if latency_ms is None else max(0, latency_ms)),
        http_status=http_status,
        resolved_model=None,
        response_model_status="unavailable_provider_failure",
        error_message=message[:500],
        transport_call_count=transport_call_count,
        latency_timing_source=latency_timing_source,
        hard_deadline_evidence=hard_deadline_evidence,
    )


def _redact_single_secret(message: str, secret: str) -> str:
    return message.replace(secret, "[REDACTED_API_KEY]") if secret else message


def build_ai_api_executor_descriptor(
    *,
    executor_id: str = "executor_ai_api",
    executor_version: str = "0.1.0",
    provider_family: str = "siliconflow",
) -> ExecutorDescriptor:
    if provider_family not in {"siliconflow", "openai", "deepseek"}:
        raise ValueError(f"unsupported ai api provider_family: {provider_family}")
    return ExecutorDescriptor(
        executor_id=executor_id,
        executor_type="ai_api",
        executor_version=executor_version,
        supported_request_schema_versions=[
            "phase3.execution_request.v1",
            "phase3.execution_request.v2",
        ],
        capabilities={
            "executor": "ai_api",
            "provider_family": provider_family,
            "output_modes": ["raw_text", "parsed_json", "parse_failure"],
            "provider_failover": "request_scoped_bounded",
        },
        environment_policy={
            "runtime": "python",
            "network": "optional_real_api",
            "secret_source": "environment_variables_only",
        },
        status=ExecutorStatus.AVAILABLE,
        metadata={
            "phase": "phase7",
            "adapter": f"{provider_family}_chat_completions",
            "production_platform": False,
        },
    )


class AIAPIExecutor:
    def __init__(
        self,
        *,
        executor_id: str,
        executor_version: str,
        artifact_store: ArtifactStore,
        config: AIAPIExecutorConfig,
        transport,
        parser: Callable[..., object] | None = None,
        post_raw_output_hook: Callable[..., Mapping[str, Any] | None] | None = None,
        resolved_secret_observer: Callable[[str], None] | None = None,
    ) -> None:
        self.executor_id = executor_id
        self.executor_version = executor_version
        self._artifact_store = artifact_store
        self._config = config
        self._transport = transport
        self._parser = parser
        self._post_raw_output_hook = post_raw_output_hook
        self._resolved_secret_observer = resolved_secret_observer

    def execute(
        self,
        request: ExecutionRequest,
        *,
        submission_id: str,
        submitted_at: str,
    ) -> ExecutionSubmission:
        required_provider_family = request.hard_requirements.get("provider_family")
        if (
            required_provider_family is not None
            and required_provider_family != self._config.provider_family
        ):
            provenance_ref = self._save_provenance(
                submission_id=submission_id,
                request=request,
                selection=_empty_selection_record(
                    config=self._config,
                    request=request,
                    require_json_mode=False,
                ),
                attempts=[],
                provider_attempt_count=0,
                final_entry_id=None,
                final_result_kind="executor_error",
                submitted_at=submitted_at,
            )
            return self._submission(
                request=request,
                submission_id=submission_id,
                submitted_at=submitted_at,
                result_kind="executor_error",
                raw_output_ref=None,
                parsed_output_ref=None,
                candidate_output_refs={},
                parse_failure_ref=None,
                provenance_ref=provenance_ref,
                usage_summary={"provider_attempt_count": 0},
                error={
                    "kind": "executor_error",
                    "reason": "provider_requirement_mismatch",
                    "required_provider_family": required_provider_family,
                    "config_provider_family": self._config.provider_family,
                },
            )
        if request.prompt_package_ref is None:
            raise ValueError("AI API executor requires prompt_package_ref")
        prompt = json.loads(self._artifact_store.read_bytes(request.prompt_package_ref).decode("utf-8"))
        try:
            require_json_mode = _require_json_mode_constraint(prompt)
        except ValueError as exc:
            provenance_ref = self._save_provenance(
                submission_id=submission_id,
                request=request,
                selection={
                    "schema_version": "phase7.ai_provider_selection.v1",
                    "eligible_entry_ids": [],
                    "selected_entry_id": None,
                    "attempt_entry_ids": [],
                },
                attempts=[],
                provider_attempt_count=0,
                final_entry_id=None,
                final_result_kind="executor_error",
                submitted_at=submitted_at,
            )
            return self._submission(
                request=request,
                submission_id=submission_id,
                submitted_at=submitted_at,
                result_kind="executor_error",
                raw_output_ref=None,
                parsed_output_ref=None,
                candidate_output_refs={},
                parse_failure_ref=None,
                provenance_ref=provenance_ref,
                usage_summary={"provider_attempt_count": 0},
                error={
                    "kind": "executor_error",
                    "reason": "invalid_prompt_package",
                    "message": str(exc),
                },
            )
        try:
            selection = _build_pre_secret_provider_selection(
                config=self._config,
                request_id=request.request_id,
                environment_seed=request.environment_ref.seed,
                require_json_mode=require_json_mode,
            )
        except ValueError as exc:
            attempts = _selection_failure_records(self._config, require_json_mode=require_json_mode)
            provenance_ref = self._save_provenance(
                submission_id=submission_id,
                request=request,
                selection=_empty_selection_record(
                    config=self._config,
                    request=request,
                    require_json_mode=require_json_mode,
                ),
                attempts=attempts,
                provider_attempt_count=0,
                final_entry_id=None,
                final_result_kind="executor_error",
                submitted_at=submitted_at,
            )
            return self._submission(
                request=request,
                submission_id=submission_id,
                submitted_at=submitted_at,
                result_kind="executor_error",
                raw_output_ref=None,
                parsed_output_ref=None,
                candidate_output_refs={},
                parse_failure_ref=None,
                provenance_ref=provenance_ref,
                usage_summary={"provider_attempt_count": 0},
                error={
                    "kind": "executor_error",
                    "reason": "no_eligible_entries",
                    "message": str(exc),
                    "selection_rejections": attempts,
                },
            )
        attempts: list[JsonObject] = []
        final_result = None
        final_entry = None
        final_request_identity: JsonObject | None = None
        last_attempted_entry = None
        last_request_identity: JsonObject | None = None
        provider_call_count = 0
        resolved_secret_values: list[str] = []
        terminal_error: (
            SiliconFlowProviderError | OpenAIProviderError | DeepSeekProviderError | None
        ) = None
        _build_chat_body, parse_provider_response = _provider_adapter(
            self._config.provider_family
        )
        for entry in entries_by_attempt_order(config=self._config, selection=selection):
            last_attempted_entry = entry
            started = perf_counter()
            request_identity: JsonObject | None = None
            prepared_request_ref: ArtifactRef | None = None
            prepared_hook_completed = False
            dispatch_intent_recorded = False
            hard_deadline_outcome: HardDeadlineDispatchOutcome | None = None
            try:
                planned_request = prepare_ai_api_outbound_request(
                    config=self._config,
                    request=request,
                    prompt=prompt,
                    entry=entry,
                )
                prepared_request = planned_request.prepared_request
                request_identity = dict(planned_request.provider_request_identity)
                prepared_request_ref = self._artifact_store.save_bytes(
                    prepared_request.body_bytes,
                    artifact_id=(
                        f"prepared_outbound_{_safe_artifact_part(request.request_id)}_"
                        f"{_safe_artifact_part(entry.entry_id)}"
                    ),
                    artifact_type="PreparedOutboundRequest",
                    media_type="application/json",
                    artifact_schema_id="tokenshare.prepared_outbound_request",
                    artifact_schema_version="v1",
                    source={"kind": "ai_api_executor", "request_id": request.request_id},
                    metadata=prepared_request.provenance_dict(),
                    created_at=submitted_at,
                )
                validate_prepared_request(prepared_request)
                request_identity = {
                    **request_identity,
                    "prepared_request": prepared_request.provenance_dict(),
                }
                last_request_identity = request_identity
                self._invoke_lifecycle_method(
                    "after_prepared_dispatch",
                    artifact_store=self._artifact_store,
                    request=request,
                    submission_id=submission_id,
                    prepared_request_ref=prepared_request_ref,
                    prepared_request=prepared_request,
                    provider_request_identity=request_identity,
                    provider_family=self._config.provider_family,
                    model=entry.model,
                    entry_id=entry.entry_id,
                    submitted_at=submitted_at,
                )
                prepared_hook_completed = True
                api_key = entry.resolve_api_key()
                transport = _exact_transport_for_provider(
                    self._transport, self._config.provider_family
                )
                if api_key not in resolved_secret_values:
                    resolved_secret_values.append(api_key)
                if self._resolved_secret_observer is not None:
                    self._resolved_secret_observer(api_key)
                validate_prepared_request(prepared_request)
                timeout_seconds = int(
                    self._config.defaults.get("timeout_seconds", 30)
                )

                def record_provider_dispatch() -> None:
                    nonlocal dispatch_intent_recorded, provider_call_count
                    self._invoke_lifecycle_method(
                        "before_provider_dispatch",
                        artifact_store=self._artifact_store,
                        request=request,
                        submission_id=submission_id,
                        prepared_request_ref=prepared_request_ref,
                        prepared_request=prepared_request,
                        provider_request_identity=request_identity,
                        provider_family=self._config.provider_family,
                        model=entry.model,
                        entry_id=entry.entry_id,
                        provider_call_count=provider_call_count,
                        submitted_at=submitted_at,
                    )
                    dispatch_intent_recorded = True
                    provider_call_count += 1

                if getattr(
                    transport, "tokenshare_hard_total_deadline", False
                ) is True:
                    hard_deadline_outcome = _dispatch_hard_deadline_through_exact_transport(
                        exact_transport=transport,
                        provider_family=self._config.provider_family,
                        api_key=api_key,
                        body_bytes=prepared_request.body_bytes,
                        normalized_absolute_endpoint=(
                            prepared_request.normalized_absolute_endpoint
                        ),
                        content_type="application/json",
                        hard_total_seconds=float(timeout_seconds),
                        on_transport_start=record_provider_dispatch,
                    )
                    if hard_deadline_outcome.status == "pre_dispatch_failure":
                        raise _HardDeadlinePreDispatchError(hard_deadline_outcome)
                    if hard_deadline_outcome.status != "completed":
                        error_type = {
                            "deepseek": DeepSeekProviderError,
                            "openai": OpenAIProviderError,
                            "siliconflow": SiliconFlowProviderError,
                        }[self._config.provider_family]
                        raise error_type(
                            error_kind=(
                                hard_deadline_outcome.failure_kind
                                or "provider_error"
                            ),
                            http_status=hard_deadline_outcome.http_status,
                            message=(
                                hard_deadline_outcome.error_message
                                or "provider child returned no response"
                            ),
                        )
                    response = hard_deadline_outcome.response
                else:
                    record_provider_dispatch()
                    response = transport.post_chat_completion(
                        api_key=api_key,
                        body_bytes=prepared_request.body_bytes,
                        normalized_absolute_endpoint=(
                            prepared_request.normalized_absolute_endpoint
                        ),
                        content_type="application/json",
                        timeout_seconds=timeout_seconds,
                    )
                final_result = parse_provider_response(response)
                final_entry = entry
                final_request_identity = request_identity
                attempts.append(
                    _attempt_record(
                        self._config.provider_family,
                        entry,
                        "succeeded",
                        perf_counter() - started,
                        response.status_code,
                        extra=_hard_deadline_attempt_extra(
                            hard_deadline_outcome
                        ),
                        provider_request_identity=request_identity,
                    )
                )
                break
            except _HardDeadlinePreDispatchError as exc:
                hard_deadline_outcome = exc.outcome
                if prepared_hook_completed and prepared_request_ref is not None:
                    self._invoke_lifecycle_method(
                        "after_pre_transport_abort",
                        artifact_store=self._artifact_store,
                        request=request,
                        submission_id=submission_id,
                        prepared_request_ref=prepared_request_ref,
                        provider_request_identity=request_identity,
                        provider_family=self._config.provider_family,
                        model=entry.model,
                        entry_id=entry.entry_id,
                        abort_kind="executor_error",
                        provider_call_count=provider_call_count,
                        submitted_at=submitted_at,
                    )
                attempts.append(
                    _attempt_record(
                        self._config.provider_family,
                        entry,
                        "executor_error",
                        perf_counter() - started,
                        None,
                        message=_redact_text(str(exc), resolved_secret_values),
                        extra=_hard_deadline_attempt_extra(hard_deadline_outcome),
                        provider_request_identity=request_identity,
                    )
                )
                break
            except ValueError as exc:
                error_kind = "secret_missing" if "missing API key env var" in str(exc) else "config_error"
                if (
                    prepared_hook_completed
                    and not dispatch_intent_recorded
                    and prepared_request_ref is not None
                ):
                    self._invoke_lifecycle_method(
                        "after_pre_transport_abort",
                        artifact_store=self._artifact_store,
                        request=request,
                        submission_id=submission_id,
                        prepared_request_ref=prepared_request_ref,
                        provider_request_identity=request_identity,
                        provider_family=self._config.provider_family,
                        model=entry.model,
                        entry_id=entry.entry_id,
                        abort_kind=error_kind,
                        provider_call_count=provider_call_count,
                        submitted_at=submitted_at,
                    )
                attempts.append(
                    _attempt_record(
                        self._config.provider_family,
                        entry,
                        error_kind,
                        perf_counter() - started,
                        None,
                        message=_redact_text(str(exc), resolved_secret_values),
                        extra={"api_key_env": entry.api_key_env} if error_kind == "secret_missing" else None,
                        provider_request_identity=request_identity,
                    )
                )
            except TimeoutError:
                attempts.append(
                    _attempt_record(
                        self._config.provider_family,
                        entry,
                        "timeout",
                        perf_counter() - started,
                        None,
                        provider_request_identity=request_identity,
                    )
                )
            except OSError as exc:
                attempts.append(
                    _attempt_record(
                        self._config.provider_family,
                        entry,
                        "connection_error",
                        perf_counter() - started,
                        None,
                        message=_redact_text(str(exc), resolved_secret_values),
                        provider_request_identity=request_identity,
                    )
                )
            except (SiliconFlowProviderError, OpenAIProviderError, DeepSeekProviderError) as exc:
                attempts.append(
                    _attempt_record(
                        self._config.provider_family,
                        entry,
                        exc.error_kind,
                        perf_counter() - started,
                        exc.http_status,
                        message=_redact_text(exc.message, resolved_secret_values),
                        extra=_hard_deadline_attempt_extra(
                            hard_deadline_outcome
                        ),
                        provider_request_identity=request_identity,
                    )
                )
                if exc.error_kind in {
                    "invalid_output",
                    "client_error",
                    "no_response",
                }:
                    terminal_error = exc
                    break
        if final_result is None or final_entry is None or final_request_identity is None:
            last_result_kind = (
                str(attempts[-1].get("result_kind")) if attempts else ""
            )
            result_kind = (
                terminal_error.error_kind
                if terminal_error is not None
                else last_result_kind
                if last_result_kind
                in {
                    "timeout",
                    "no_response",
                    "connection_error",
                    "rate_limited",
                    "provider_error",
                    "auth_error",
                }
                else "executor_error"
            )
            parse_failure_ref = None
            if result_kind == "invalid_output":
                parse_failure_ref = self._save_parse_failure(
                    submission_id=submission_id,
                    request=request,
                    raw_output_ref=None,
                    reason="provider_response_invalid",
                    message=_redact_text(
                        terminal_error.message if terminal_error is not None else result_kind,
                        resolved_secret_values,
                    ),
                    submitted_at=submitted_at,
                )
            provider_failure_ref = None
            if provider_call_count > 0 and result_kind in PROVIDER_FAILURE_TAXONOMY:
                provider_failure_ref = self._save_provider_failure(
                    submission_id=submission_id,
                    request=request,
                    entry=last_attempted_entry,
                    result_kind=result_kind,
                    terminal_attempt=(attempts[-1] if attempts else {}),
                    submitted_at=submitted_at,
                )
            provenance_ref = self._save_provenance(
                submission_id=submission_id,
                request=request,
                selection=selection.to_dict(),
                attempts=attempts,
                provider_attempt_count=provider_call_count,
                final_entry_id=None,
                final_result_kind=result_kind,
                submitted_at=submitted_at,
            )
            if last_attempted_entry is None:
                failure_usage_summary: JsonObject = {
                    "provider_family": self._config.provider_family,
                    "provider_attempt_count": provider_call_count,
                    "cost_estimate": None,
                    "cost_estimate_status": "usage_missing",
                    "cost_estimate_basis": "usage_missing",
                }
            else:
                failure_usage_summary = _usage_summary(
                    self._config.provider_family,
                    last_attempted_entry,
                    requested_model=str(
                        (last_request_identity or {}).get(
                            "requested_model", last_attempted_entry.model
                        )
                    ),
                    usage=None,
                    attempt_count=provider_call_count,
                    enable_thinking=_request_identity_enable_thinking(
                        last_request_identity
                    ),
                )
            provider_terminal_usage_ref = None
            if provider_failure_ref is not None:
                provider_terminal_usage_ref = self._save_provider_terminal_usage(
                    submission_id=submission_id,
                    request=request,
                    provider_failure_ref=provider_failure_ref,
                    provenance_ref=provenance_ref,
                    usage_summary=failure_usage_summary,
                    submitted_at=submitted_at,
                )
                self._invoke_lifecycle_method(
                    "after_provider_failure_persisted",
                    artifact_store=self._artifact_store,
                    request=request,
                    submission_id=submission_id,
                    provider_failure_ref=provider_failure_ref,
                    provenance_ref=provenance_ref,
                    usage_ref=provider_terminal_usage_ref,
                    provider_family=self._config.provider_family,
                    model=last_attempted_entry.model,
                    entry_id=last_attempted_entry.entry_id,
                    failure_kind=result_kind,
                    http_status=(attempts[-1].get("http_status") if attempts else None),
                    usage_summary=failure_usage_summary,
                    submitted_at=submitted_at,
                )
            error: JsonObject = {"kind": result_kind, "attempts": attempts}
            if provider_failure_ref is not None and provider_terminal_usage_ref is not None:
                error.update(
                    {
                        "provider_failure_ref": provider_failure_ref.to_dict(),
                        "provider_terminal_usage_ref": (
                            provider_terminal_usage_ref.to_dict()
                        ),
                    }
                )
            return self._submission(
                request=request,
                submission_id=submission_id,
                submitted_at=submitted_at,
                result_kind=result_kind,
                raw_output_ref=None,
                parsed_output_ref=None,
                candidate_output_refs={},
                parse_failure_ref=parse_failure_ref,
                provenance_ref=provenance_ref,
                usage_summary=failure_usage_summary,
                error=error,
            )

        raw_ref = self._artifact_store.save_json(
            {
                "schema_version": RAW_MODEL_OUTPUT_SCHEMA_V2,
                "submission_id": submission_id,
                "request_id": request.request_id,
                "provider_family": self._config.provider_family,
                "entry_id": final_entry.entry_id,
                **build_raw_model_identity_fields(
                    configured_model=final_entry.model,
                    requested_model=str(final_request_identity["requested_model"]),
                    raw_response_json=final_result.raw_response_json,
                ),
                "provider_response_id": final_result.provider_response_id,
                "content_text": final_result.content_text,
                "reasoning_content": getattr(final_result, "reasoning_content", None),
                "raw_response_json": final_result.raw_response_json,
                "finish_reason": final_result.finish_reason,
                "usage": final_result.usage,
                "created_at": submitted_at,
            },
            artifact_id=f"raw_model_output_{submission_id}",
            artifact_type="RawModelOutput",
            artifact_schema_id="phase7.raw_model_output",
            artifact_schema_version="v2",
            source={"kind": "ai_api_executor", "request_id": request.request_id},
            metadata={"executor_id": self.executor_id, "entry_id": final_entry.entry_id},
            created_at=submitted_at,
        )
        parser_input_text = final_result.content_text
        usage_summary = _usage_summary(
            self._config.provider_family,
            final_entry,
            requested_model=str(final_request_identity["requested_model"]),
            usage=final_result.usage,
            attempt_count=provider_call_count,
            enable_thinking=_request_identity_enable_thinking(
                final_request_identity
            ),
        )
        hook_result_kind: str | None = None
        if self._post_raw_output_hook is not None:
            response_provenance_ref = self._save_response_provenance(
                submission_id=submission_id,
                request=request,
                selection=selection.to_dict(),
                attempts=attempts,
                provider_attempt_count=provider_call_count,
                final_entry_id=final_entry.entry_id,
                raw_output_ref=raw_ref,
                submitted_at=submitted_at,
            )
            response_usage_ref = self._save_response_usage(
                submission_id=submission_id,
                request=request,
                raw_output_ref=raw_ref,
                usage_summary=usage_summary,
                submitted_at=submitted_at,
            )
            hook_result = self._post_raw_output_hook(
                artifact_store=self._artifact_store,
                request=request,
                submission_id=submission_id,
                raw_output_ref=raw_ref,
                provenance_ref=response_provenance_ref,
                usage_ref=response_usage_ref,
                provider_family=self._config.provider_family,
                model=final_entry.model,
                entry_id=final_entry.entry_id,
                content_text=final_result.content_text,
                usage_summary=usage_summary,
                submitted_at=submitted_at,
            )
            if hook_result is not None:
                if not isinstance(hook_result, Mapping):
                    raise ValueError("post_raw_output_hook must return a mapping or None")
                replacement_text = hook_result.get("content_text")
                if replacement_text is not None and not isinstance(replacement_text, str):
                    raise ValueError("post_raw_output_hook content_text must be a string")
                if isinstance(replacement_text, str):
                    parser_input_text = replacement_text
                result_kind_value = hook_result.get("result_kind")
                if result_kind_value is not None:
                    hook_result_kind = str(result_kind_value)
        if hook_result_kind in {
            "no_return",
            "late_submission",
            "executor_error",
        }:
            provenance_ref = self._save_provenance(
                submission_id=submission_id,
                request=request,
                selection=selection.to_dict(),
                attempts=attempts,
                provider_attempt_count=provider_call_count,
                final_entry_id=final_entry.entry_id,
                final_result_kind=hook_result_kind,
                submitted_at=submitted_at,
            )
            return self._submission(
                request=request,
                submission_id=submission_id,
                submitted_at=submitted_at,
                result_kind=hook_result_kind,
                raw_output_ref=raw_ref,
                parsed_output_ref=None,
                candidate_output_refs={},
                parse_failure_ref=None,
                provenance_ref=provenance_ref,
                usage_summary=usage_summary,
                error={"kind": hook_result_kind, "source": "post_raw_output_hook"},
            )
        parsed_ref = None
        candidate_refs: dict[str, ArtifactRef] = {}
        parse_failure_ref = None
        result_kind = "succeeded"
        if self._parser is not None:
            try:
                parsed = self._call_parser(
                    parser_input_text,
                    raw_output_ref=raw_ref,
                    submitted_at=submitted_at,
                )
                if _plugin_parse_failed(parsed):
                    parse_failure_ref = self._save_plugin_parse_failure(
                        parsed,
                        submission_id=submission_id,
                        request=request,
                        submitted_at=submitted_at,
                    )
                    provenance_ref = self._save_provenance(
                        submission_id=submission_id,
                        request=request,
                        selection=selection.to_dict(),
                        attempts=attempts,
                        provider_attempt_count=provider_call_count,
                        final_entry_id=final_entry.entry_id,
                        final_result_kind="parse_failed",
                        submitted_at=submitted_at,
                    )
                    return self._submission(
                        request=request,
                        submission_id=submission_id,
                        submitted_at=submitted_at,
                        result_kind="parse_failed",
                        raw_output_ref=raw_ref,
                        parsed_output_ref=None,
                        candidate_output_refs={},
                        parse_failure_ref=parse_failure_ref,
                        provenance_ref=provenance_ref,
                        usage_summary=_usage_summary(
                            self._config.provider_family,
                            final_entry,
                            requested_model=str(
                                final_request_identity["requested_model"]
                            ),
                            usage=final_result.usage,
                            attempt_count=provider_call_count,
                            enable_thinking=_request_identity_enable_thinking(
                                final_request_identity
                            ),
                        ),
                        error={"kind": "parse_failed", "reason": "plugin_parser_rejected_output"},
                    )
                parsed_ref, candidate_refs = self._save_parser_success(
                    parsed,
                    submission_id=submission_id,
                    request=request,
                    raw_output_ref=raw_ref,
                    submitted_at=submitted_at,
                )
            except Exception as exc:
                parse_failure_ref = self._save_parse_failure(
                    submission_id=submission_id,
                    request=request,
                    raw_output_ref=raw_ref,
                    reason="plugin_parser_rejected_output",
                    message=_redact_text(str(exc), resolved_secret_values),
                    submitted_at=submitted_at,
                )
                provenance_ref = self._save_provenance(
                    submission_id=submission_id,
                    request=request,
                    selection=selection.to_dict(),
                    attempts=attempts,
                    provider_attempt_count=provider_call_count,
                    final_entry_id=final_entry.entry_id,
                    final_result_kind="parse_failed",
                    submitted_at=submitted_at,
                )
                return self._submission(
                    request=request,
                    submission_id=submission_id,
                    submitted_at=submitted_at,
                    result_kind="parse_failed",
                    raw_output_ref=raw_ref,
                    parsed_output_ref=None,
                    candidate_output_refs={},
                    parse_failure_ref=parse_failure_ref,
                    provenance_ref=provenance_ref,
                    usage_summary=_usage_summary(
                        self._config.provider_family,
                        final_entry,
                        requested_model=str(
                            final_request_identity["requested_model"]
                        ),
                        usage=final_result.usage,
                        attempt_count=provider_call_count,
                        enable_thinking=_request_identity_enable_thinking(
                            final_request_identity
                        ),
                    ),
                    error={"kind": "parse_failed", "reason": "plugin_parser_rejected_output"},
                )

        provenance_ref = self._save_provenance(
            submission_id=submission_id,
            request=request,
            selection=selection.to_dict(),
            attempts=attempts,
            provider_attempt_count=provider_call_count,
            final_entry_id=final_entry.entry_id,
            final_result_kind=result_kind,
            submitted_at=submitted_at,
        )
        return self._submission(
            request=request,
            submission_id=submission_id,
            submitted_at=submitted_at,
            result_kind=result_kind,
            raw_output_ref=raw_ref,
            parsed_output_ref=parsed_ref,
            candidate_output_refs=candidate_refs,
            parse_failure_ref=parse_failure_ref,
            provenance_ref=provenance_ref,
            usage_summary=usage_summary,
            error=None,
        )

    def _call_parser(
        self,
        raw_text: str,
        *,
        raw_output_ref: ArtifactRef,
        submitted_at: str,
    ) -> object:
        assert self._parser is not None
        if _parser_accepts_context(self._parser):
            return self._parser(
                raw_text,
                raw_output_ref_summary=_artifact_ref_summary(raw_output_ref),
                created_at=submitted_at,
            )
        return self._parser(raw_text)

    def _save_parser_success(
        self,
        parsed: object,
        *,
        submission_id: str,
        request: ExecutionRequest,
        raw_output_ref: ArtifactRef,
        submitted_at: str,
    ) -> tuple[ArtifactRef | None, dict[str, ArtifactRef]]:
        if _plugin_parse_succeeded(parsed):
            parsed_body = getattr(parsed, "parsed_artifact_body", None)
            if not isinstance(parsed_body, dict):
                raise ValueError("plugin parser result missing parsed_artifact_body")
            schema_id = str(getattr(parsed, "parsed_artifact_schema_id", None) or "phase7.parsed_model_output")
            schema_version = str(getattr(parsed, "parsed_artifact_schema_version", None) or "v1")
            parsed_ref = self._artifact_store.save_json(
                parsed_body,
                artifact_id=f"parsed_model_output_{submission_id}",
                artifact_type="ParsedModelOutput",
                artifact_schema_id=schema_id,
                artifact_schema_version=schema_version,
                source={"kind": "ai_api_executor", "raw_output_ref": raw_output_ref.to_dict()},
                metadata={"executor_id": self.executor_id},
                created_at=submitted_at,
            )
            candidate_bodies = getattr(parsed, "candidate_output_artifact_bodies", None)
            if not isinstance(candidate_bodies, dict):
                raise ValueError("plugin parser result missing candidate_output_artifact_bodies")
            candidate_refs: dict[str, ArtifactRef] = {}
            for output_name, candidate_body in candidate_bodies.items():
                if not isinstance(candidate_body, dict):
                    raise ValueError("plugin parser candidate output body must be an object")
                output_key = str(output_name)
                candidate_refs[output_key] = self._artifact_store.save_json(
                    candidate_body,
                    artifact_id=f"candidate_output_{submission_id}_{_safe_artifact_part(output_key)}",
                    artifact_type="CandidateOutput",
                    artifact_schema_id=schema_id,
                    artifact_schema_version=schema_version,
                    source={"kind": "ai_api_executor", "raw_output_ref": raw_output_ref.to_dict()},
                    metadata={"executor_id": self.executor_id, "output_name": output_key},
                    created_at=submitted_at,
                )
            return parsed_ref, candidate_refs

        if not isinstance(parsed, dict):
            raise ValueError("plugin parser returned non-object parsed output")
        parsed_ref = self._artifact_store.save_json(
            parsed,
            artifact_id=f"parsed_model_output_{submission_id}",
            artifact_type="ParsedModelOutput",
            artifact_schema_id="phase7.parsed_model_output",
            artifact_schema_version="v1",
            source={"kind": "ai_api_executor", "raw_output_ref": raw_output_ref.to_dict()},
            metadata={"executor_id": self.executor_id},
            created_at=submitted_at,
        )
        return parsed_ref, {name: parsed_ref for name in request.output_contract.required_outputs}

    def _save_plugin_parse_failure(
        self,
        parsed: object,
        *,
        submission_id: str,
        request: ExecutionRequest,
        submitted_at: str,
    ) -> ArtifactRef:
        failure_body = getattr(parsed, "parse_failure_artifact_body", None)
        if not isinstance(failure_body, dict):
            return self._save_parse_failure(
                submission_id=submission_id,
                request=request,
                raw_output_ref=None,
                reason="plugin_parser_rejected_output",
                message="plugin parser returned parse failure without artifact body",
                submitted_at=submitted_at,
            )
        schema_id, schema_version = _schema_id_and_version(
            failure_body.get("schema_version"),
            default_schema_id="phase7.parse_failure_report",
            default_schema_version="v1",
        )
        return self._artifact_store.save_json(
            failure_body,
            artifact_id=f"parse_failure_{submission_id}",
            artifact_type="ParseFailureReport",
            artifact_schema_id=schema_id,
            artifact_schema_version=schema_version,
            source={"kind": "ai_api_executor", "request_id": request.request_id},
            metadata={"executor_id": self.executor_id},
            created_at=submitted_at,
        )

    def _save_provenance(
        self,
        *,
        submission_id: str,
        request: ExecutionRequest,
        selection: JsonObject,
        attempts: list[JsonObject],
        provider_attempt_count: int,
        final_entry_id: str | None,
        final_result_kind: str,
        submitted_at: str,
    ) -> ArtifactRef:
        return self._artifact_store.save_json(
            {
                "schema_version": "phase7.ai_provider_call_provenance.v2",
                "submission_id": submission_id,
                "request_id": request.request_id,
                "provider_family": self._config.provider_family,
                "config_digest": self._config.config_digest,
                "selection_record": selection,
                "attempts": attempts,
                "provider_attempt_count": provider_attempt_count,
                "final_entry_id": final_entry_id,
                "final_result_kind": final_result_kind,
                "secret_redaction": {
                    "authorization_header": False,
                    "api_key_value": False,
                },
            },
            artifact_id=f"ai_provider_provenance_{submission_id}",
            artifact_type="AIProviderCallProvenance",
            artifact_schema_id="phase7.ai_provider_call_provenance",
            artifact_schema_version="v2",
            source={"kind": "ai_api_executor", "request_id": request.request_id},
            metadata={"executor_id": self.executor_id},
            created_at=submitted_at,
        )

    def _save_provider_failure(
        self,
        *,
        submission_id: str,
        request: ExecutionRequest,
        entry: AIAPIProviderEntry,
        result_kind: str,
        terminal_attempt: Mapping[str, Any],
        submitted_at: str,
    ) -> ArtifactRef:
        """保存真实 transport 的 terminal failure；不伪造 raw response。"""

        return self._artifact_store.save_json(
            {
                "schema_version": "phase7.ai_provider_failure.v1",
                "submission_id": submission_id,
                "request_id": request.request_id,
                "provider_family": self._config.provider_family,
                "entry_id": entry.entry_id,
                "configured_model": entry.model,
                "failure_kind": result_kind,
                "http_status": terminal_attempt.get("http_status"),
                "message": terminal_attempt.get("message"),
                "provider_request_identity": terminal_attempt.get(
                    "provider_request_identity"
                ),
                "raw_output_ref": None,
                "lifecycle_stage": "provider_transport_terminal_failure",
            },
            artifact_id=f"ai_provider_failure_{submission_id}",
            artifact_type="AIProviderFailure",
            artifact_schema_id="phase7.ai_provider_failure",
            artifact_schema_version="v1",
            source={"kind": "ai_api_executor", "request_id": request.request_id},
            metadata={"executor_id": self.executor_id, "entry_id": entry.entry_id},
            created_at=submitted_at,
        )

    def _save_provider_terminal_usage(
        self,
        *,
        submission_id: str,
        request: ExecutionRequest,
        provider_failure_ref: ArtifactRef,
        provenance_ref: ArtifactRef,
        usage_summary: JsonObject,
        submitted_at: str,
    ) -> ArtifactRef:
        """保存 failure 专用 nullable usage；不扩宽 response usage v1。"""

        return self._artifact_store.save_json(
            {
                "schema_version": "phase7.ai_provider_terminal_usage.v1",
                "submission_id": submission_id,
                "request_id": request.request_id,
                "provider_failure_ref": provider_failure_ref.to_dict(),
                "provenance_ref": provenance_ref.to_dict(),
                "usage_summary": dict(usage_summary),
                "lifecycle_stage": "provider_failure_persisted_before_callback",
            },
            artifact_id=f"ai_provider_terminal_usage_{submission_id}",
            artifact_type="AIProviderTerminalUsage",
            artifact_schema_id="phase7.ai_provider_terminal_usage",
            artifact_schema_version="v1",
            source={"kind": "ai_api_executor", "request_id": request.request_id},
            metadata={"executor_id": self.executor_id},
            created_at=submitted_at,
        )

    def _invoke_lifecycle_method(self, method_name: str, **context: Any) -> Any:
        hook = self._post_raw_output_hook
        method = getattr(hook, method_name, None)
        return method(**context) if callable(method) else None

    def _save_response_provenance(
        self,
        *,
        submission_id: str,
        request: ExecutionRequest,
        selection: JsonObject,
        attempts: list[JsonObject],
        provider_attempt_count: int,
        final_entry_id: str,
        raw_output_ref: ArtifactRef,
        submitted_at: str,
    ) -> ArtifactRef:
        """为实验 post-raw hook 保存 parser 前 provider provenance。"""

        return self._artifact_store.save_json(
            {
                "schema_version": "phase7.ai_provider_response_provenance.v1",
                "submission_id": submission_id,
                "request_id": request.request_id,
                "provider_family": self._config.provider_family,
                "config_digest": self._config.config_digest,
                "selection_record": selection,
                "attempts": attempts,
                "provider_attempt_count": provider_attempt_count,
                "final_entry_id": final_entry_id,
                "raw_output_ref": raw_output_ref.to_dict(),
                "lifecycle_stage": "provider_response_persisted_before_parser",
            },
            artifact_id=f"ai_provider_response_provenance_{submission_id}",
            artifact_type="AIProviderResponseProvenance",
            artifact_schema_id="phase7.ai_provider_response_provenance",
            artifact_schema_version="v1",
            source={"kind": "ai_api_executor", "request_id": request.request_id},
            metadata={"executor_id": self.executor_id},
            created_at=submitted_at,
        )

    def _save_response_usage(
        self,
        *,
        submission_id: str,
        request: ExecutionRequest,
        raw_output_ref: ArtifactRef,
        usage_summary: JsonObject,
        submitted_at: str,
    ) -> ArtifactRef:
        """为实验 post-raw hook 保存 parser 前 usage snapshot。"""

        return self._artifact_store.save_json(
            {
                "schema_version": "phase7.ai_provider_response_usage.v1",
                "submission_id": submission_id,
                "request_id": request.request_id,
                "raw_output_ref": raw_output_ref.to_dict(),
                "usage_summary": usage_summary,
                "lifecycle_stage": "provider_response_persisted_before_parser",
            },
            artifact_id=f"ai_provider_response_usage_{submission_id}",
            artifact_type="AIProviderResponseUsage",
            artifact_schema_id="phase7.ai_provider_response_usage",
            artifact_schema_version="v1",
            source={"kind": "ai_api_executor", "request_id": request.request_id},
            metadata={"executor_id": self.executor_id},
            created_at=submitted_at,
        )

    def _save_parse_failure(
        self,
        *,
        submission_id: str,
        request: ExecutionRequest,
        raw_output_ref: ArtifactRef | None,
        reason: str,
        message: str,
        submitted_at: str,
    ) -> ArtifactRef:
        return self._artifact_store.save_json(
            {
                "schema_version": "phase7.parse_failure_report.v1",
                "submission_id": submission_id,
                "request_id": request.request_id,
                "raw_output_ref": raw_output_ref.to_dict() if raw_output_ref is not None else None,
                "reason": reason,
                "message": message,
            },
            artifact_id=f"parse_failure_{submission_id}",
            artifact_type="ParseFailureReport",
            artifact_schema_id="phase7.parse_failure_report",
            artifact_schema_version="v1",
            source={"kind": "ai_api_executor", "request_id": request.request_id},
            metadata={"executor_id": self.executor_id},
            created_at=submitted_at,
        )

    def _submission(
        self,
        *,
        request: ExecutionRequest,
        submission_id: str,
        submitted_at: str,
        result_kind: str,
        raw_output_ref: ArtifactRef | None,
        parsed_output_ref: ArtifactRef | None,
        candidate_output_refs: dict[str, ArtifactRef],
        parse_failure_ref: ArtifactRef | None,
        provenance_ref: ArtifactRef | None,
        usage_summary: JsonObject,
        error: JsonObject | None,
    ) -> ExecutionSubmission:
        return ExecutionSubmission(
            submission_id=submission_id,
            request_id=request.request_id,
            task_id=request.task_id,
            unit_id=request.unit_id,
            attempt_id=request.attempt_id,
            lease_id=request.lease_id,
            fencing_token=request.fencing_token,
            executor_id=self.executor_id,
            executor_version=self.executor_version,
            result_kind=result_kind,
            raw_output_ref=raw_output_ref,
            parsed_output_ref=parsed_output_ref,
            candidate_output_refs=candidate_output_refs,
            parse_failure_ref=parse_failure_ref,
            log_ref=None,
            environment_ref=request.environment_ref,
            environment_summary={
                "runtime": request.environment_ref.runtime,
                "provider_family": self._config.provider_family,
                "config_digest": self._config.config_digest,
            },
            provenance_ref=provenance_ref,
            usage_summary=usage_summary,
            error=error,
            submitted_at=submitted_at,
        )


def _hard_deadline_attempt_extra(
    outcome: HardDeadlineDispatchOutcome | None,
) -> JsonObject | None:
    if outcome is None:
        return None
    return {
        "latency_ms": outcome.provider_latency_ms,
        "latency_timing_source": _hard_deadline_latency_timing_source(outcome),
        "transport_call_count": outcome.transport_call_count,
        "hard_deadline_evidence": dict(outcome.quiescence_evidence),
    }


def _hard_deadline_latency_timing_source(
    outcome: HardDeadlineDispatchOutcome,
) -> str:
    if outcome.status == "pre_dispatch_failure":
        return "unavailable_pre_dispatch"
    if outcome.provider_latency_ms is None:
        return "unknown_no_response"
    return "provider_child_observed"


def _attempt_record(
    provider_family: str,
    entry,
    result_kind: str,
    elapsed_seconds: float,
    http_status: int | None,
    *,
    message: str | None = None,
    extra: JsonObject | None = None,
    provider_request_identity: JsonObject | None = None,
) -> JsonObject:
    record: JsonObject = {
        "provider_family": provider_family,
        "entry_id": entry.entry_id,
        "configured_model": entry.model,
        "result_kind": result_kind,
        "latency_ms": int(elapsed_seconds * 1000),
        "http_status": http_status,
    }
    if message:
        record["message"] = message[:500]
    if extra:
        record.update(extra)
    if provider_request_identity is not None:
        record["provider_request_identity"] = dict(provider_request_identity)
    return record


def _provider_request_identity(
    *,
    provider_family: str,
    entry,
    body: JsonObject,
) -> JsonObject:
    control_keys = (
        "stream",
        "temperature",
        "top_p",
        "max_tokens",
        "response_format",
        "reasoning_effort",
        "enable_thinking",
        "thinking_budget",
        "thinking",
    )
    effective_controls = {
        key: body[key]
        for key in control_keys
        if key in body
    }
    reasoning_controls = {
        key: effective_controls[key]
        for key in (
            "reasoning_effort",
            "enable_thinking",
            "thinking_budget",
            "thinking",
        )
        if key in effective_controls
    }
    encoded_controls = json.dumps(
        effective_controls,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "schema_version": "phase7.provider_request_identity.v2",
        "provider_family": provider_family,
        "entry_id": entry.entry_id,
        "configured_model": entry.model,
        "requested_model": _required_request_model(body),
        "reasoning_controls": reasoning_controls,
        "effective_request_controls_digest": (
            f"sha256:{sha256(encoded_controls).hexdigest()}"
        ),
    }


def _usage_summary(
    provider_family: str,
    entry,
    *,
    requested_model: str,
    usage: JsonObject | None,
    attempt_count: int,
    enable_thinking: bool | None = None,
) -> JsonObject:
    base = {
        "provider_family": provider_family,
        "entry_id": entry.entry_id,
        "model": entry.model,
        "configured_model": entry.model,
        "requested_model": requested_model,
        "provider_attempt_count": attempt_count,
        "currency": entry.pricing["currency"],
        "pricing_snapshot": dict(entry.pricing),
    }
    usage_body: Mapping[str, Any] = usage or {}
    invalid_fields: set[str] = set()
    prompt_tokens, prompt_status = _usage_int_field(usage_body, "prompt_tokens")
    completion_tokens, completion_status = _usage_int_field(
        usage_body,
        "completion_tokens",
    )
    completion_details = usage_body.get("completion_tokens_details")
    reasoning_body = (
        completion_details
        if isinstance(completion_details, Mapping)
        else usage_body
    )
    reasoning_tokens, reasoning_status = _usage_int_field(
        reasoning_body,
        "reasoning_tokens",
    )
    total_tokens, total_status = _usage_int_field(usage_body, "total_tokens")
    if total_status == "missing" and prompt_tokens is not None and completion_tokens is not None:
        total_tokens = prompt_tokens + completion_tokens
    cache_hit, cache_hit_status = _usage_int_field(
        usage_body,
        "prompt_cache_hit_tokens",
    )
    cache_miss, cache_miss_status = _usage_int_field(
        usage_body,
        "prompt_cache_miss_tokens",
    )
    for field_name, status in (
        ("prompt_tokens", prompt_status),
        ("completion_tokens", completion_status),
        ("reasoning_tokens", reasoning_status),
        ("total_tokens", total_status),
        ("prompt_cache_hit_tokens", cache_hit_status),
        ("prompt_cache_miss_tokens", cache_miss_status),
    ):
        if status == "invalid":
            invalid_fields.add(field_name)
    visible_output_tokens, visible_output_basis = _visible_output_usage(
        completion_tokens=completion_tokens,
        reasoning_tokens=reasoning_tokens,
        enable_thinking=enable_thinking,
        reasoning_invalid=reasoning_status == "invalid",
    )
    if prompt_tokens is None or completion_tokens is None:
        usage_status = (
            "usage_invalid"
            if "invalid" in {prompt_status, completion_status}
            else "usage_missing"
        )
        return {
            **base,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "reasoning_tokens": reasoning_tokens,
            "visible_output_tokens": visible_output_tokens,
            "visible_output_basis": visible_output_basis,
            "prompt_cache_hit_tokens": cache_hit,
            "prompt_cache_miss_tokens": cache_miss,
            "usage_invalid_fields": sorted(invalid_fields),
            "cost_estimate": None,
            "cost_estimate_status": usage_status,
            "cost_estimate_basis": usage_status,
        }
    if "input_per_million_tokens" in entry.pricing:
        input_cost = (
            prompt_tokens
            / 1_000_000
            * float(entry.pricing["input_per_million_tokens"])
        )
        estimate_basis = "legacy_single_input_rate"
    elif cache_hit is not None and cache_miss is not None:
        input_cost = (
            cache_hit
            / 1_000_000
            * float(entry.pricing["cached_input_per_million_tokens"])
            + cache_miss
            / 1_000_000
            * float(entry.pricing["uncached_input_per_million_tokens"])
        )
        estimate_basis = "provider_cache_breakdown"
    else:
        input_cost = (
            prompt_tokens
            / 1_000_000
            * float(entry.pricing["uncached_input_per_million_tokens"])
        )
        estimate_basis = "all_prompt_tokens_uncached"
    output_cost = completion_tokens / 1_000_000 * float(entry.pricing["output_per_million_tokens"])
    return {
        **base,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "reasoning_tokens": reasoning_tokens,
        "visible_output_tokens": visible_output_tokens,
        "visible_output_basis": visible_output_basis,
        "prompt_cache_hit_tokens": cache_hit,
        "prompt_cache_miss_tokens": cache_miss,
        "usage_invalid_fields": sorted(invalid_fields),
        "cost_estimate": input_cost + output_cost,
        "cost_estimate_status": "estimated",
        "cost_estimate_basis": estimate_basis,
    }


def _optional_usage_int(body: Mapping[str, Any], field: str) -> int | None:
    value, _status = _usage_int_field(body, field)
    return value


def _usage_int_field(
    body: Mapping[str, Any],
    field: str,
) -> tuple[int | None, str]:
    if field not in body:
        return None, "missing"
    value = body[field]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None, "invalid"
    return value, "valid"


def _visible_output_usage(
    *,
    completion_tokens: int | None,
    reasoning_tokens: int | None,
    enable_thinking: bool | None,
    reasoning_invalid: bool = False,
) -> tuple[int | None, str]:
    if completion_tokens is None:
        return None, "completion_usage_unavailable"
    if reasoning_invalid:
        return None, "invalid_usage_breakdown"
    if reasoning_tokens is not None:
        if reasoning_tokens > completion_tokens:
            return None, "invalid_usage_breakdown"
        return (
            completion_tokens - reasoning_tokens,
            "provider_reasoning_breakdown",
        )
    if enable_thinking is False:
        return completion_tokens, "explicit_non_thinking"
    return None, "reasoning_breakdown_unavailable"


def _request_identity_enable_thinking(
    provider_request_identity: Mapping[str, Any] | None,
) -> bool | None:
    if not isinstance(provider_request_identity, Mapping):
        return None
    reasoning_controls = provider_request_identity.get("reasoning_controls")
    if not isinstance(reasoning_controls, Mapping):
        return None
    enable_thinking = reasoning_controls.get("enable_thinking")
    return enable_thinking if isinstance(enable_thinking, bool) else None


def _required_request_model(body: JsonObject) -> str:
    model = body.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("provider request body model must be a non-empty string")
    return model


def _provider_adapter(provider_family: str):
    if provider_family == "siliconflow":
        return build_siliconflow_chat_body, parse_siliconflow_response
    if provider_family == "openai":
        return build_openai_chat_body, parse_openai_response
    if provider_family == "deepseek":
        return build_deepseek_chat_body, parse_deepseek_response
    raise ValueError(f"unsupported ai api provider_family: {provider_family}")


def _parser_accepts_context(parser: Callable[..., object]) -> bool:
    try:
        parameters = signature(parser).parameters.values()
    except (TypeError, ValueError):
        return False
    for parameter in parameters:
        if parameter.kind == Parameter.VAR_KEYWORD:
            return True
        if parameter.name in {"raw_output_ref_summary", "created_at"}:
            return True
    return False


def _plugin_parse_succeeded(parsed: object) -> bool:
    return getattr(parsed, "succeeded", None) is True


def _plugin_parse_failed(parsed: object) -> bool:
    return getattr(parsed, "succeeded", None) is False


def _artifact_ref_summary(ref: ArtifactRef) -> JsonObject:
    return {
        "artifact_id": ref.artifact_id,
        "content_hash": ref.content_hash,
        "artifact_schema_id": ref.artifact_schema_id,
        "artifact_schema_version": ref.artifact_schema_version,
    }


def _provider_prompt_text(prompt: JsonObject) -> str:
    prompt_text = str(prompt["prompt_text"])
    sections = [prompt_text]
    for key, title in (
        ("input_summary", "Authoritative PromptPackage input_summary"),
        ("output_schema", "Authoritative PromptPackage output_schema"),
        ("constraints", "Authoritative PromptPackage constraints"),
    ):
        value = prompt.get(key, {})
        sections.append(f"{title}:")
        sections.append(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    sections.append(
        "When structured PromptPackage fields conflict with prose, use the structured fields. "
        "Return only the required JSON object."
    )
    return "\n\n".join(sections)


def _schema_id_and_version(
    schema_version: object,
    *,
    default_schema_id: str,
    default_schema_version: str,
) -> tuple[str, str]:
    if not isinstance(schema_version, str) or "." not in schema_version:
        return default_schema_id, default_schema_version
    schema_id, version = schema_version.rsplit(".", 1)
    if not schema_id or not version:
        return default_schema_id, default_schema_version
    return schema_id, version


def _safe_artifact_part(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in {"_", "-"} else "_"
        for character in value
    )


def _empty_selection_record(
    *,
    config: AIAPIExecutorConfig,
    request: ExecutionRequest,
    require_json_mode: bool,
) -> JsonObject:
    seed_material = f"{config.config_digest}|{request.request_id}|{request.environment_ref.seed}"
    seed_digest = f"sha256:{sha256(seed_material.encode('utf-8')).hexdigest()}"
    return {
        "schema_version": "phase7.ai_provider_selection.v1",
        "selection_policy_id": str(config.selection_policy.get("kind", "unknown")),
        "eligible_entry_ids": [],
        "selected_entry_id": None,
        "attempt_entry_ids": [],
        "random_seed_material_digest": seed_digest,
        "selection_index": None,
        "require_json_mode": require_json_mode,
    }


def _selection_failure_records(
    config: AIAPIExecutorConfig,
    *,
    require_json_mode: bool,
) -> list[JsonObject]:
    records: list[JsonObject] = []
    for entry in config.entries:
        extra: JsonObject = {}
        if not entry.enabled:
            result_kind = "disabled"
        elif require_json_mode and not entry.supports_json_mode:
            result_kind = "json_mode_unsupported"
        else:
            result_kind = "not_selected"
        records.append(
            _attempt_record(config.provider_family, entry, result_kind, 0.0, None, extra=extra)
        )
    return records


def _redact_text(text: str, secret_values: Sequence[str]) -> str:
    redacted = text
    for secret in secret_values:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED_API_KEY]")
    return redacted


def _require_json_mode_constraint(prompt: JsonObject) -> bool:
    constraints = prompt.get("constraints", {})
    if not isinstance(constraints, dict):
        raise ValueError("prompt constraints must be an object")
    value = constraints.get("requires_json_mode", False)
    if not isinstance(value, bool):
        raise ValueError("prompt constraints.requires_json_mode must be a boolean")
    return value


def _stable_case_id(request: ExecutionRequest) -> str:
    hints = request.soft_hints or {}
    snapshot = request.task_unit_snapshot
    metadata = snapshot.get("metadata", {})
    for value in (
        hints.get("case_id"),
        metadata.get("case_id") if isinstance(metadata, Mapping) else None,
        snapshot.get("task_id"),
        request.task_id,
    ):
        if isinstance(value, str) and value:
            return value
    return request.task_id


def _stable_planned_ai_unit_id(request: ExecutionRequest) -> str:
    value = (request.soft_hints or {}).get("planned_ai_unit_id")
    return str(value) if isinstance(value, str) and value else request.unit_id


def _stable_slot_index(request: ExecutionRequest, field: str) -> int:
    value = (request.soft_hints or {}).get(field, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _build_pre_secret_provider_selection(
    *,
    config: AIAPIExecutorConfig,
    request_id: str,
    environment_seed: int | None,
    require_json_mode: bool,
) -> AIProviderSelection:
    """只按静态配置选 entry；secret 必须等 prepared artifact 后才读取。"""

    eligible = [
        entry
        for entry in config.entries
        if entry.enabled and (not require_json_mode or entry.supports_json_mode)
    ]
    if not eligible:
        raise ValueError("no eligible ai api entries")
    seed_material = f"{config.config_digest}|{request_id}|{environment_seed}"
    seed_digest = f"sha256:{sha256(seed_material.encode('utf-8')).hexdigest()}"
    ordered = list(eligible)
    random.Random(seed_digest).shuffle(ordered)
    max_attempts = int(config.defaults.get("max_provider_attempts", len(ordered)))
    ordered = ordered[: max(1, min(max_attempts, len(ordered)))]
    eligible_ids = [entry.entry_id for entry in eligible]
    return AIProviderSelection(
        selection_policy_id=str(config.selection_policy["kind"]),
        eligible_entry_ids=eligible_ids,
        selected_entry_id=ordered[0].entry_id,
        attempt_entry_ids=[entry.entry_id for entry in ordered],
        random_seed_material_digest=seed_digest,
        selection_index=eligible_ids.index(ordered[0].entry_id),
    )


def _exact_transport_for_provider(transport: Any, provider_family: str) -> Any:
    resolver = getattr(transport, "tokenshare_transport_for_provider", None)
    return resolver(provider_family) if callable(resolver) else transport


def _dispatch_hard_deadline_through_exact_transport(
    *,
    exact_transport: Any,
    provider_family: str,
    api_key: str,
    body_bytes: bytes,
    normalized_absolute_endpoint: str,
    content_type: str,
    hard_total_seconds: float,
    on_transport_start: Callable[[], None] | None = None,
) -> HardDeadlineDispatchOutcome:
    """ready ack 后才穿过 exact/accounted transport，且该边界恰好一次。"""

    session = prepare_provider_transport_hard_deadline_session(
        provider_family=provider_family,
        api_key=api_key,
        body_bytes=body_bytes,
        normalized_absolute_endpoint=normalized_absolute_endpoint,
        content_type=content_type,
        hard_total_seconds=hard_total_seconds,
    )
    try:
        if session.pre_dispatch_outcome is not None:
            return session.pre_dispatch_outcome
        if on_transport_start is not None:
            on_transport_start()
        outcome = exact_transport.post_chat_completion(
            api_key=api_key,
            body_bytes=body_bytes,
            normalized_absolute_endpoint=normalized_absolute_endpoint,
            content_type=content_type,
            timeout_seconds=int(hard_total_seconds),
            hard_deadline_session=session,
        )
        if type(outcome) is not HardDeadlineDispatchOutcome:
            raise TypeError("hard-deadline exact transport returned invalid outcome")
        return outcome
    finally:
        session.close()
