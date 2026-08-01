"""Phase 7 experimental AI API executor."""

from __future__ import annotations

import json
import os
import random
from collections.abc import Mapping
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
from tokenshare.executors.ai_api_config import AIAPIExecutorConfig
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
    latency_ms: int
    http_status: int | None
    resolved_model: str | None
    response_model_status: str
    error_message: str | None


def dispatch_prepared_request_once(
    *,
    prepared_request: PreparedOutboundRequest,
    provider_family: str,
    transport: Any,
    api_key: str,
    timeout_seconds: int,
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
    try:
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
        )
    except OSError as exc:
        return _prepared_failure_evidence(
            failure_kind="connection_error",
            latency_ms=int((perf_counter() - started) * 1000),
            http_status=None,
            message=_redact_single_secret(str(exc), api_key),
        )
    except (SiliconFlowProviderError, OpenAIProviderError, DeepSeekProviderError) as exc:
        failure_kind = (
            exc.error_kind
            if exc.error_kind in PROVIDER_FAILURE_TAXONOMY
            else "provider_error"
        )
        return _prepared_failure_evidence(
            failure_kind=failure_kind,
            latency_ms=int((perf_counter() - started) * 1000),
            http_status=exc.http_status,
            message=_redact_single_secret(exc.message, api_key),
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
        latency_ms=int((perf_counter() - started) * 1000),
        http_status=(None if response is None else int(response.status_code)),
        resolved_model=getattr(parsed, "resolved_model", None),
        response_model_status=str(
            getattr(parsed, "response_model_status", "missing")
        ),
        error_message=None,
    )


def _prepared_failure_evidence(
    *,
    failure_kind: str,
    latency_ms: int,
    http_status: int | None,
    message: str,
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
        latency_ms=max(0, latency_ms),
        http_status=http_status,
        resolved_model=None,
        response_model_status="unavailable_provider_failure",
        error_message=message[:500],
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
        supported_request_schema_versions=["phase3.execution_request.v1"],
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
    ) -> None:
        self.executor_id = executor_id
        self.executor_version = executor_version
        self._artifact_store = artifact_store
        self._config = config
        self._transport = transport
        self._parser = parser
        self._post_raw_output_hook = post_raw_output_hook

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
        terminal_error: (
            SiliconFlowProviderError | OpenAIProviderError | DeepSeekProviderError | None
        ) = None
        build_chat_body, parse_provider_response = _provider_adapter(self._config.provider_family)
        for entry in entries_by_attempt_order(config=self._config, selection=selection):
            last_attempted_entry = entry
            started = perf_counter()
            request_identity: JsonObject | None = None
            try:
                body = build_chat_body(
                    entry=entry,
                    prompt_text=_provider_prompt_text(prompt),
                    defaults=self._config.defaults,
                    request_limits=request.limits,
                    soft_hints=request.soft_hints or {},
                    require_json_mode=require_json_mode,
                )
                request_identity = _provider_request_identity(
                    provider_family=self._config.provider_family,
                    entry=entry,
                    body=body,
                )
                prepared_request = PreparedOutboundRequestFactory.prepare(
                    body_obj=body,
                    base_url=entry.base_url,
                    endpoint=entry.endpoint,
                    provider_config_digest=self._config.config_digest,
                    entry_id=entry.entry_id,
                    configured_model=entry.model,
                    effective_controls_digest=str(
                        request_identity["effective_request_controls_digest"]
                    ),
                    plugin_id=str(request.plugin.get("plugin_id", "unknown")),
                    plugin_version=str(request.plugin.get("plugin_version", "unknown")),
                    prompt_profile_id=str(prompt.get("fixture_profile", "unknown")),
                    prompt_serialization_schema=str(
                        prompt.get("schema_version", "phase3.prompt_package.v1")
                    ),
                    body_serialization_schema=(
                        f"{self._config.provider_family}.chat_completions.v1"
                    ),
                    case_id=_stable_case_id(request),
                    planned_ai_unit_id=_stable_planned_ai_unit_id(request),
                    sample_slot_index=_stable_slot_index(
                        request, "sample_slot_index"
                    ),
                    replacement_slot=_stable_slot_index(request, "replacement_slot"),
                )
                validate_prepared_request(prepared_request)
                self._artifact_store.save_bytes(
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
                api_key = entry.resolve_api_key()
                provider_call_count += 1
                validate_prepared_request(prepared_request)
                transport = _exact_transport_for_provider(
                    self._transport, self._config.provider_family
                )
                response = transport.post_chat_completion(
                    api_key=api_key,
                    body_bytes=prepared_request.body_bytes,
                    normalized_absolute_endpoint=(
                        prepared_request.normalized_absolute_endpoint
                    ),
                    content_type="application/json",
                    timeout_seconds=int(self._config.defaults.get("timeout_seconds", 30)),
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
                        provider_request_identity=request_identity,
                    )
                )
                break
            except ValueError as exc:
                error_kind = "secret_missing" if "missing API key env var" in str(exc) else "config_error"
                attempts.append(
                    _attempt_record(
                        self._config.provider_family,
                        entry,
                        error_kind,
                        perf_counter() - started,
                        None,
                        message=_redact_text(str(exc), self._config),
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
                        message=_redact_text(str(exc), self._config),
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
                        message=_redact_text(exc.message, self._config),
                        provider_request_identity=request_identity,
                    )
                )
                if exc.error_kind in {"invalid_output", "client_error"}:
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
                        self._config,
                    ),
                    submitted_at=submitted_at,
                )
            provenance_ref = self._save_provenance(
                submission_id=submission_id,
                request=request,
                selection=selection.to_dict(),
                attempts=attempts,
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
                error={"kind": result_kind, "attempts": attempts},
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
                    message=_redact_text(str(exc), self._config),
                    submitted_at=submitted_at,
                )
                provenance_ref = self._save_provenance(
                    submission_id=submission_id,
                    request=request,
                    selection=selection.to_dict(),
                    attempts=attempts,
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

    def _save_response_provenance(
        self,
        *,
        submission_id: str,
        request: ExecutionRequest,
        selection: JsonObject,
        attempts: list[JsonObject],
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
        elif not os.environ.get(entry.api_key_env, ""):
            result_kind = "secret_missing"
            extra["api_key_env"] = entry.api_key_env
        else:
            result_kind = "not_selected"
        records.append(
            _attempt_record(config.provider_family, entry, result_kind, 0.0, None, extra=extra)
        )
    return records


def _redact_text(text: str, config: AIAPIExecutorConfig) -> str:
    redacted = text
    for entry in config.entries:
        secret = os.environ.get(entry.api_key_env, "")
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
