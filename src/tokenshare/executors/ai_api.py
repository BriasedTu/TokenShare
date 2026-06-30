"""Phase 7 experimental AI API executor."""

from __future__ import annotations

import json
import os
from hashlib import sha256
from inspect import Parameter, signature
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
        parser: Callable[..., object] | None = None,
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
            selection = build_provider_selection(
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
        terminal_error: SiliconFlowProviderError | None = None
        for entry in entries_by_attempt_order(config=self._config, selection=selection):
            started = perf_counter()
            try:
                body = build_siliconflow_chat_body(
                    entry=entry,
                    prompt_text=_provider_prompt_text(prompt),
                    defaults=self._config.defaults,
                    request_limits=request.limits,
                    soft_hints=request.soft_hints or {},
                    require_json_mode=require_json_mode,
                )
                api_key = entry.resolve_api_key()
                response = self._transport.post_chat_completion(
                    entry=entry,
                    api_key=api_key,
                    body=body,
                    timeout_seconds=int(self._config.defaults.get("timeout_seconds", 30)),
                )
                final_result = parse_siliconflow_response(response)
                final_entry = entry
                attempts.append(
                    _attempt_record(entry, "succeeded", perf_counter() - started, response.status_code)
                )
                break
            except ValueError as exc:
                error_kind = "secret_missing" if "missing API key env var" in str(exc) else "config_error"
                attempts.append(
                    _attempt_record(
                        entry,
                        error_kind,
                        perf_counter() - started,
                        None,
                        message=_redact_text(str(exc), self._config),
                        extra={"api_key_env": entry.api_key_env} if error_kind == "secret_missing" else None,
                    )
                )
            except TimeoutError:
                attempts.append(_attempt_record(entry, "timeout", perf_counter() - started, None))
            except OSError as exc:
                attempts.append(
                    _attempt_record(
                        entry,
                        "connection_error",
                        perf_counter() - started,
                        None,
                        message=_redact_text(str(exc), self._config),
                    )
                )
            except SiliconFlowProviderError as exc:
                attempts.append(
                    _attempt_record(
                        entry,
                        exc.error_kind,
                        perf_counter() - started,
                        exc.http_status,
                        message=_redact_text(exc.message, self._config),
                    )
                )
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
                parsed = self._call_parser(
                    final_result.content_text,
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
                        usage_summary=_usage_summary(final_entry, final_result.usage, len(attempts)),
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
                    usage_summary=_usage_summary(final_entry, final_result.usage, len(attempts)),
                    error={"kind": "parse_failed", "reason": "plugin_parser_rejected_output"},
                )

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
    *,
    message: str | None = None,
    extra: JsonObject | None = None,
) -> JsonObject:
    record: JsonObject = {
        "entry_id": entry.entry_id,
        "model": entry.model,
        "result_kind": result_kind,
        "latency_ms": int(elapsed_seconds * 1000),
        "http_status": http_status,
    }
    if message:
        record["message"] = message[:500]
    if extra:
        record.update(extra)
    return record


def _usage_summary(entry, usage: JsonObject | None, attempt_count: int) -> JsonObject:
    base = {
        "provider_family": "siliconflow",
        "entry_id": entry.entry_id,
        "model": entry.model,
        "provider_attempt_count": attempt_count,
        "currency": entry.pricing["currency"],
        "pricing_snapshot": dict(entry.pricing),
    }
    if not usage or "prompt_tokens" not in usage or "completion_tokens" not in usage:
        return {
            **base,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "cost_estimate": None,
            "cost_estimate_status": "usage_missing",
        }
    prompt_tokens = int(usage["prompt_tokens"])
    completion_tokens = int(usage["completion_tokens"])
    input_cost = prompt_tokens / 1_000_000 * float(entry.pricing["input_per_million_tokens"])
    output_cost = completion_tokens / 1_000_000 * float(entry.pricing["output_per_million_tokens"])
    return {
        **base,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": int(usage.get("total_tokens", prompt_tokens + completion_tokens)),
        "cost_estimate": input_cost + output_cost,
        "cost_estimate_status": "estimated",
    }


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
        records.append(_attempt_record(entry, result_kind, 0.0, None, extra=extra))
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
