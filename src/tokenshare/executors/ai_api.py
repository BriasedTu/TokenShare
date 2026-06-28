"""Phase 7 experimental AI API executor."""

from __future__ import annotations

import json
from time import perf_counter
from typing import Callable

from tokenshare.core.models import ArtifactRef, JsonObject
from tokenshare.executors.ai_api_config import AIAPIExecutorConfig
from tokenshare.executors.ai_api_selector import build_provider_selection, entries_by_attempt_order
from tokenshare.executors.ai_api_transport import (
    SiliconFlowProviderError,
    build_siliconflow_chat_body,
    parse_siliconflow_response,
)
from tokenshare.executors.contracts import (
    ExecutionRequest,
    ExecutionSubmission,
    ExecutorDescriptor,
    ExecutorStatus,
)
from tokenshare.storage.artifacts import ArtifactStore


def build_ai_api_executor_descriptor(
    *,
    executor_id: str = "executor_ai_api",
    executor_version: str = "0.1.0",
) -> ExecutorDescriptor:
    return ExecutorDescriptor(
        executor_id=executor_id,
        executor_type="ai_api",
        executor_version=executor_version,
        supported_request_schema_versions=["phase3.execution_request.v1"],
        capabilities={
            "executor": "ai_api",
            "provider_family": "siliconflow",
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
            "adapter": "siliconflow_chat_completions",
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
        parser: Callable[[str], JsonObject] | None = None,
    ) -> None:
        self.executor_id = executor_id
        self.executor_version = executor_version
        self._artifact_store = artifact_store
        self._config = config
        self._transport = transport
        self._parser = parser

    def execute(
        self,
        request: ExecutionRequest,
        *,
        submission_id: str,
        submitted_at: str,
    ) -> ExecutionSubmission:
        if request.prompt_package_ref is None:
            raise ValueError("AI API executor requires prompt_package_ref")
        prompt = json.loads(self._artifact_store.read_bytes(request.prompt_package_ref).decode("utf-8"))
        require_json_mode = bool(prompt.get("constraints", {}).get("requires_json_mode"))
        selection = build_provider_selection(
            config=self._config,
            request_id=request.request_id,
            environment_seed=request.environment_ref.seed,
            require_json_mode=require_json_mode,
        )
        attempts: list[JsonObject] = []
        final_result = None
        final_entry = None
        terminal_error: SiliconFlowProviderError | None = None
        for entry in entries_by_attempt_order(config=self._config, selection=selection):
            started = perf_counter()
            try:
                body = build_siliconflow_chat_body(
                    entry=entry,
                    prompt_text=str(prompt["prompt_text"]),
                    defaults=self._config.defaults,
                    request_limits=request.limits,
                    soft_hints=request.soft_hints or {},
                    require_json_mode=require_json_mode,
                )
                response = self._transport.post_chat_completion(
                    entry=entry,
                    api_key=entry.resolve_api_key(),
                    body=body,
                    timeout_seconds=int(self._config.defaults.get("timeout_seconds", 30)),
                )
                final_result = parse_siliconflow_response(response)
                final_entry = entry
                attempts.append(
                    _attempt_record(entry, "succeeded", perf_counter() - started, response.status_code)
                )
                break
            except TimeoutError:
                attempts.append(_attempt_record(entry, "timeout", perf_counter() - started, None))
            except SiliconFlowProviderError as exc:
                attempts.append(_attempt_record(entry, exc.error_kind, perf_counter() - started, exc.http_status))
                if exc.error_kind in {"invalid_output", "client_error"}:
                    terminal_error = exc
                    break
        if final_result is None or final_entry is None:
            result_kind = terminal_error.error_kind if terminal_error is not None else "executor_error"
            parse_failure_ref = None
            if result_kind == "invalid_output":
                parse_failure_ref = self._save_parse_failure(
                    submission_id=submission_id,
                    request=request,
                    raw_output_ref=None,
                    reason="provider_response_invalid",
                    message=terminal_error.message if terminal_error is not None else result_kind,
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
                usage_summary={"provider_attempt_count": len(attempts)},
                error={"kind": result_kind, "attempts": attempts},
            )

        raw_ref = self._artifact_store.save_json(
            {
                "schema_version": "phase7.raw_model_output.v1",
                "submission_id": submission_id,
                "request_id": request.request_id,
                "provider_family": "siliconflow",
                "entry_id": final_entry.entry_id,
                "model": final_result.model or final_entry.model,
                "provider_response_id": final_result.provider_response_id,
                "content_text": final_result.content_text,
                "raw_response_json": final_result.raw_response_json,
                "finish_reason": final_result.finish_reason,
                "usage": final_result.usage,
                "created_at": submitted_at,
            },
            artifact_id=f"raw_model_output_{submission_id}",
            artifact_type="RawModelOutput",
            artifact_schema_id="phase7.raw_model_output",
            artifact_schema_version="v1",
            source={"kind": "ai_api_executor", "request_id": request.request_id},
            metadata={"executor_id": self.executor_id, "entry_id": final_entry.entry_id},
            created_at=submitted_at,
        )
        parsed_ref = None
        candidate_refs: dict[str, ArtifactRef] = {}
        parse_failure_ref = None
        result_kind = "succeeded"
        if self._parser is not None:
            try:
                parsed = self._parser(final_result.content_text)
            except Exception as exc:
                parse_failure_ref = self._save_parse_failure(
                    submission_id=submission_id,
                    request=request,
                    raw_output_ref=raw_ref,
                    reason="plugin_parser_rejected_output",
                    message=str(exc),
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
                    usage_summary=_usage_summary(final_entry, final_result.usage, len(attempts)),
                    error={"kind": "parse_failed", "reason": "plugin_parser_rejected_output"},
                )
            parsed_ref = self._artifact_store.save_json(
                parsed,
                artifact_id=f"parsed_model_output_{submission_id}",
                artifact_type="ParsedModelOutput",
                artifact_schema_id="phase7.parsed_model_output",
                artifact_schema_version="v1",
                source={"kind": "ai_api_executor", "raw_output_ref": raw_ref.to_dict()},
                metadata={"executor_id": self.executor_id},
                created_at=submitted_at,
            )
            candidate_refs = {name: parsed_ref for name in request.output_contract.required_outputs}

        usage_summary = _usage_summary(final_entry, final_result.usage, len(attempts))
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
                "schema_version": "phase7.ai_provider_call_provenance.v1",
                "submission_id": submission_id,
                "request_id": request.request_id,
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
                "provider_family": "siliconflow",
                "config_digest": self._config.config_digest,
            },
            provenance_ref=provenance_ref,
            usage_summary=usage_summary,
            error=error,
            submitted_at=submitted_at,
        )


def _attempt_record(
    entry,
    result_kind: str,
    elapsed_seconds: float,
    http_status: int | None,
) -> JsonObject:
    return {
        "entry_id": entry.entry_id,
        "model": entry.model,
        "result_kind": result_kind,
        "latency_ms": int(elapsed_seconds * 1000),
        "http_status": http_status,
    }


def _usage_summary(entry, usage: JsonObject, attempt_count: int) -> JsonObject:
    prompt_tokens = int(usage.get("prompt_tokens", 0))
    completion_tokens = int(usage.get("completion_tokens", 0))
    input_cost = prompt_tokens / 1_000_000 * float(entry.pricing["input_per_million_tokens"])
    output_cost = completion_tokens / 1_000_000 * float(entry.pricing["output_per_million_tokens"])
    return {
        "provider_family": "siliconflow",
        "entry_id": entry.entry_id,
        "model": entry.model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": int(usage.get("total_tokens", prompt_tokens + completion_tokens)),
        "provider_attempt_count": attempt_count,
        "cost_estimate": input_cost + output_cost,
        "currency": entry.pricing["currency"],
        "pricing_snapshot": dict(entry.pricing),
        "cost_estimate_status": "estimated",
    }
