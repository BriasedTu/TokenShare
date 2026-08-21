"""Slim V2 provider/fixed-trace 到 public ExecutionSubmission 的适配。"""

from __future__ import annotations

from dataclasses import fields, replace
import json
from typing import Any, Callable

from tokenshare.executors.contracts import ExecutionRequest, ExecutionSubmission
from tokenshare.plugins.factorization.validator import parse_factorization_ai_output
from tokenshare.plugins.lean_proof.models import LeanTheoremPayload
from tokenshare.plugins.lean_proof.prompt_builder import parse_lean_proof_candidate_ai_output
from tokenshare.storage.artifacts import ArtifactStore

from .provider import ProviderCallContextV1, call_provider_once, project_cost
from .schema import (
    AttemptResultV1,
    ProviderCallResultV1,
    ProviderEntryViewV1,
    ProviderRequestControlV1,
    UnitTraceV1,
)
from .storage import RootKeyV1, RunStore, StorageConflictError, select_trace_attempt


class ProviderConditionError(RuntimeError):
    """Provider 配置、模型身份或 journal 冲突使当前 condition fail-stop。"""

    def __init__(self, error_kind: str, message: str) -> None:
        self.error_kind = error_kind
        super().__init__(f"{error_kind}: {message}")


ProviderCaller = Callable[
    [ProviderEntryViewV1, str, ProviderRequestControlV1, ProviderCallContextV1, RunStore],
    ProviderCallResultV1,
]


class ProviderSubmissionAdapter:
    """协议 retry 之外只取得一个 provider outcome 并转为 public submission。"""

    def __init__(
        self,
        *,
        entry: ProviderEntryViewV1,
        artifact_store: ArtifactStore,
        run_store: RunStore,
        root_key: RootKeyV1,
        domain: str,
        caller: ProviderCaller = call_provider_once,
    ) -> None:
        if domain not in {"factorization", "lean"}:
            raise ValueError("domain must be factorization or lean")
        self.entry = entry
        self.artifact_store = artifact_store
        self.run_store = run_store
        self.root_key = root_key
        self.domain = domain
        self._caller = caller
        self.requests: list[ExecutionRequest] = []
        self.attempts: list[AttemptResultV1] = []
        self.outcomes: list[ProviderCallResultV1] = []
        self.errors: list[str] = []
        self._attempts_by_planned: dict[str, list[AttemptResultV1]] = {}
        self.condition_error: ProviderConditionError | None = None

    def execute(
        self,
        request: ExecutionRequest,
        *,
        submission_id: str,
        submitted_at: str,
    ) -> ExecutionSubmission:
        if self.condition_error is not None:
            raise self.condition_error
        planned = _planned_id(request)
        prompt = _read_json(self.artifact_store, request.prompt_package_ref)
        prompt_text = prompt.get("prompt_text")
        if not isinstance(prompt_text, str):
            raise ValueError("prompt package lacks prompt_text")
        controls = {
            "exp1": (600.0, 300_000),
            "exp5": (600.0, 100_000),
        }
        try:
            timeout_seconds, max_tokens = controls[self.root_key[0]]
        except KeyError as exc:
            raise ValueError("provider submissions are only valid for Exp1/Exp5") from exc
        control = ProviderRequestControlV1(
            timeout_seconds=timeout_seconds,
            max_tokens=max_tokens,
            require_json_mode=bool(prompt.get("constraints", {}).get("requires_json_mode", True)),
        )
        context = ProviderCallContextV1(
            call_key=_call_key(self.root_key, planned, request.attempt_ordinal),
            root_key=self.root_key,
            planned_ai_unit_id=planned,
            attempt_ordinal=request.attempt_ordinal,
        )
        try:
            outcome = self._caller(self.entry, prompt_text, control, context, self.run_store)
        except StorageConflictError as exc:
            self.condition_error = ProviderConditionError(
                "provider_journal_conflict",
                str(exc),
            )
            raise self.condition_error from exc
        self.outcomes.append(outcome)
        if outcome.error_kind in {
            "provider_configuration_invalid",
            "provider_model_mismatch",
        }:
            self.condition_error = ProviderConditionError(
                outcome.error_kind,
                outcome.error_message or "provider condition failed",
            )
            raise self.condition_error
        self.requests.append(request)
        try:
            submission, parse_result = _submission_from_content(
                request=request,
                submission_id=submission_id,
                submitted_at=submitted_at,
                artifact_store=self.artifact_store,
                domain=self.domain,
                outcome=outcome,
            )
            attempt = _provider_attempt(
                request=request,
                planned=planned,
                context=context,
                outcome=outcome,
                parse_result=parse_result,
                trace_origin="protocol",
                provider_family=str(self.entry.provider_family),
            )
        except Exception as exc:
            self.errors.append(f"{type(exc).__name__}: {exc}")
            raise
        self.attempts.append(attempt)
        self._attempts_by_planned.setdefault(planned, []).append(attempt)
        return submission

    def attempts_for(self, planned_ai_unit_id: str) -> tuple[AttemptResultV1, ...]:
        return tuple(self._attempts_by_planned.get(planned_ai_unit_id, ()))

    def apply_protocol_facts(
        self,
        *,
        canonical_attempt_ids: set[str],
        verification_status: dict[str, str],
    ) -> None:
        """只把 engine/ledger 已拥有的 canonical/checker facts 投影回 ordinary attempts。"""

        projected: list[AttemptResultV1] = []
        by_planned: dict[str, list[AttemptResultV1]] = {}
        for attempt in self.attempts:
            status = verification_status.get(str(attempt.attempt_id))
            updated = replace(
                attempt,
                canonical_accepted=attempt.attempt_id in canonical_attempt_ids,
                verifier_result=(status if self.domain == "factorization" else attempt.verifier_result),
                checker_result=(status if self.domain == "lean" else attempt.checker_result),
            )
            fact_field = (
                "attempts[].verifier_result"
                if self.domain == "factorization"
                else "attempts[].checker_result"
            )
            updated = replace(updated, missing_reason={
                key: value
                for key, value in updated.missing_reason.items()
                if not (status is not None and key == fact_field)
            })
            updated.validate(experiment_id="exp1")
            projected.append(updated)
            by_planned.setdefault(str(updated.planned_ai_unit_id), []).append(updated)
        self.attempts = projected
        self._attempts_by_planned = by_planned

    def record_tail_evaluation(
        self,
        *,
        planned_ai_unit_id: str,
        attempt_ordinal: int,
        accepted: bool,
        reached_domain_check: bool,
        started_at_ms: int,
        ended_at_ms: int,
    ) -> None:
        attempts = self._attempts_by_planned.get(planned_ai_unit_id, [])
        matches = [item for item in attempts if item.attempt_ordinal == attempt_ordinal]
        if len(matches) != 1:
            raise ValueError("tail evaluation has no unique provider attempt")
        current = matches[0]
        status = "passed" if accepted else "rejected" if reached_domain_check else "not_reached"
        field_name = "verifier_result" if self.domain == "factorization" else "checker_result"
        updated = replace(
            current,
            started_at_ms=started_at_ms,
            ended_at_ms=ended_at_ms,
            **{field_name: status},
        )
        resolved_keys = {
            f"attempts[].{field_name}",
            "attempts[].started_at_ms",
            "attempts[].ended_at_ms",
        }
        updated = replace(
            updated,
            missing_reason={
                key: value
                for key, value in updated.missing_reason.items()
                if key not in resolved_keys
            },
        )
        updated.validate(experiment_id="exp1")
        self.attempts[self.attempts.index(current)] = updated
        attempts[attempts.index(current)] = updated

    def build_trace(self, planned_ai_unit_id: str, *, trace_origin: str) -> UnitTraceV1:
        attempts = self._attempts_by_planned.get(planned_ai_unit_id)
        if not attempts:
            raise ValueError(f"no provider attempts for {planned_ai_unit_id}")
        requests = [request for request in self.requests if _planned_id(request) == planned_ai_unit_id]
        request = requests[0]
        candidate_start, candidate_end, lemma, dependency = _semantics(
            self.artifact_store, request, self.domain
        )
        natural = [replace(item, trace_origin=trace_origin) for item in attempts]
        trace = UnitTraceV1(
            case_id=self.root_key[2],
            source_repeat_id=0,
            planned_ai_unit_id=planned_ai_unit_id,
            domain=self.domain,
            trace_origin=trace_origin,
            candidate_start=candidate_start,
            candidate_end=candidate_end,
            lemma_node_id=lemma,
            dependency_path=dependency,
            provider_family=self.entry.provider_family,
            provider_entry_id=self.entry.entry_id,
            configured_model=self.entry.configured_model,
            requested_model=self.entry.configured_model,
            resolved_model=self.entry.configured_model,
            attempts=natural,
        )
        trace.validate()
        return trace


class FixedTraceSubmissionAdapter:
    """只从 typed Exp1 trace 选择回答；无 transport/provider fallback。"""

    def __init__(
        self,
        *,
        source_store: RunStore,
        artifact_store: ArtifactStore,
        case_id: str,
        domain: str,
        provider_entry_id: str,
        configured_model: str,
        provider_family: str = "deepseek",
    ) -> None:
        self.source_store = source_store
        self.artifact_store = artifact_store
        self.case_id = case_id
        self.domain = domain
        self.provider_entry_id = provider_entry_id
        self.configured_model = configured_model
        self.provider_family = provider_family
        self.attempts: list[AttemptResultV1] = []

    def execute(
        self,
        request: ExecutionRequest,
        *,
        submission_id: str,
        submitted_at: str,
    ) -> ExecutionSubmission:
        planned = _planned_id(request)
        trace = self.source_store.read_trace(self.case_id, 0, planned)
        if (
            trace.domain != self.domain
            or trace.provider_family != self.provider_family
            or trace.provider_entry_id != self.provider_entry_id
            or trace.configured_model != self.configured_model
            or trace.requested_model != self.configured_model
            or trace.resolved_model != self.configured_model
        ):
            raise ValueError("fixed trace identity differs from current request")
        _check_semantics(self.artifact_store, request, trace)
        selected = select_trace_attempt(trace, request.attempt_ordinal)
        source = selected.attempt
        if not source.raw_response_relative_path:
            outcome = _failed_source_outcome(trace, source)
        else:
            document = self.source_store.read_relative_response(source.raw_response_relative_path)
            body = document.get("body")
            content = _response_content(body)
            outcome = ProviderCallResultV1(
                ok=isinstance(content, str),
                content_text=content if isinstance(content, str) else None,
                reasoning_content=None,
                raw_response_json=body,
                prompt_tokens=source.prompt_tokens,
                prompt_cache_hit_tokens=source.prompt_cache_hit_tokens,
                prompt_cache_miss_tokens=source.prompt_cache_miss_tokens,
                completion_tokens=source.completion_tokens,
                reasoning_tokens=source.reasoning_tokens,
                total_tokens=source.total_tokens,
                provider_request_started_at_utc=source.provider_request_started_at_utc,
                provider_latency_ms=source.provider_latency_ms,
                configured_model=trace.configured_model,
                requested_model=trace.requested_model,
                resolved_model=trace.resolved_model,
                provider_response_id=None,
                finish_reason=None,
                http_status=source.http_status,
                error_kind=None if isinstance(content, str) else "source_response_invalid",
                error_message=None if isinstance(content, str) else "source response content absent",
                usage_status=source.usage_status,
            )
            outcome.validate()
        submission, _parse_result = _submission_from_content(
            request=request,
            submission_id=submission_id,
            submitted_at=submitted_at,
            artifact_store=self.artifact_store,
            domain=self.domain,
            outcome=outcome,
        )
        replay_attempt = _fixed_attempt(request, planned, trace, selected)
        replay_attempt.validate(experiment_id="exp2")
        self.attempts.append(replay_attempt)
        return replace(
            submission,
            usage_summary={
                **dict(submission.usage_summary or {}),
                "provider_attempt_count": 0,
                "provider_call_made": False,
                "source_attempt_ordinal": selected.source_attempt_ordinal,
                "source_attempt_fallback_used": selected.source_attempt_fallback_used,
                "source_result_kind": source.result_kind,
                "source_latency_ms": source.provider_latency_ms,
                "source_total_tokens": source.total_tokens,
                "source_cost_estimate_cny": source.cost_estimate_cny,
                "source_pricing_version": source.pricing_version,
                "source_pricing_tier": source.pricing_tier,
            },
        )


def _submission_from_content(
    *,
    request: ExecutionRequest,
    submission_id: str,
    submitted_at: str,
    artifact_store: ArtifactStore,
    domain: str,
    outcome: ProviderCallResultV1,
) -> tuple[ExecutionSubmission, str | None]:
    raw_ref = None
    parsed_ref = None
    candidate_refs: dict[str, Any] = {}
    failure_ref = None
    parse_status: str | None = None
    error = None
    result_kind = "failed"
    if outcome.raw_response_json is not None:
        raw_ref = artifact_store.save_json(
            {"provider_response": outcome.raw_response_json, "content_text": outcome.content_text},
            artifact_id=f"slim_raw_{_safe(submission_id)}",
            artifact_type="RawModelOutput",
            artifact_schema_id="tokenshare.slim_v2.raw_model_output",
            artifact_schema_version="v1",
            source={"kind": "slim_v2_provider"},
            metadata={},
            created_at=submitted_at,
        )
    if outcome.ok:
        parse = _parse_domain(
            domain=domain,
            request=request,
            artifact_store=artifact_store,
            content=outcome.content_text,
            raw_ref=raw_ref,
            created_at=submitted_at,
        )
        parse_status = "parsed" if parse.succeeded else "rejected"
        if parse.succeeded:
            for output_name, body in parse.candidate_output_artifact_bodies.items():
                ref = artifact_store.save_json(
                    body,
                    artifact_id=f"slim_candidate_{_safe(submission_id)}_{_safe(output_name)}",
                    artifact_type="parsed_output",
                    artifact_schema_id=str(parse.parsed_artifact_schema_id),
                    artifact_schema_version=str(parse.parsed_artifact_schema_version),
                    source={"kind": "slim_v2_domain_parser", "raw_output_ref": raw_ref.to_dict()},
                    metadata={"output_name": output_name},
                    created_at=submitted_at,
                )
                candidate_refs[output_name] = ref
                if parsed_ref is None:
                    parsed_ref = ref
            result_kind = "succeeded"
        else:
            failure_ref = artifact_store.save_json(
                dict(parse.parse_failure_artifact_body or {"failure_kind": "parse_rejected"}),
                artifact_id=f"slim_parse_failure_{_safe(submission_id)}",
                artifact_type="ParseFailureReport",
                artifact_schema_id="tokenshare.slim_v2.parse_failure",
                artifact_schema_version="v1",
                source={"kind": "slim_v2_domain_parser"},
                metadata={},
                created_at=submitted_at,
            )
            error = {"kind": "parse_rejected", "message": "domain parser rejected provider content"}
    else:
        error = {"kind": outcome.error_kind or "provider_failed", "message": outcome.error_message or "provider call failed"}
    usage = {
        "provider_attempt_count": 1,
        "provider_call_made": True,
        "provider_latency_ms": outcome.provider_latency_ms,
        "prompt_tokens": outcome.prompt_tokens,
        "prompt_cache_hit_tokens": outcome.prompt_cache_hit_tokens,
        "prompt_cache_miss_tokens": outcome.prompt_cache_miss_tokens,
        "completion_tokens": outcome.completion_tokens,
        "reasoning_tokens": outcome.reasoning_tokens,
        "total_tokens": outcome.total_tokens,
        "usage_status": outcome.usage_status,
    }
    return ExecutionSubmission(
        submission_id=submission_id,
        request_id=request.request_id,
        task_id=request.task_id,
        unit_id=request.unit_id,
        attempt_id=request.attempt_id,
        lease_id=request.lease_id,
        fencing_token=request.fencing_token,
        executor_id=str(request.executor["executor_id"]),
        executor_version=str(request.executor["executor_version"]),
        result_kind=result_kind,
        raw_output_ref=raw_ref,
        parsed_output_ref=parsed_ref,
        candidate_output_refs=candidate_refs,
        parse_failure_ref=failure_ref,
        log_ref=None,
        environment_ref=request.environment_ref,
        environment_summary={"runtime": "slim_v2"},
        provenance_ref=None,
        usage_summary=usage,
        error=error,
        submitted_at=submitted_at,
    ), parse_status


def _parse_domain(
    *, domain: str, request: ExecutionRequest, artifact_store: ArtifactStore,
    content: str | None, raw_ref: Any, created_at: str,
) -> Any:
    summary = raw_ref.to_dict() if raw_ref is not None else {}
    if domain == "factorization":
        return parse_factorization_ai_output(content, raw_output_ref_summary=summary, created_at=created_at)
    payload_ref = request.input_artifact_refs.get(
        "lemma_theorem_payload", request.input_artifact_refs.get("child_theorem_payload")
    )
    if payload_ref is None:
        raise ValueError("Lean request lacks theorem payload")
    payload = LeanTheoremPayload.from_dict(_read_json(artifact_store, payload_ref))
    return parse_lean_proof_candidate_ai_output(
        content, theorem_payload=payload, raw_output_ref_summary=summary, created_at=created_at
    )


def _provider_attempt(
    *, request: ExecutionRequest, planned: str, context: ProviderCallContextV1,
    outcome: ProviderCallResultV1, parse_result: str | None, trace_origin: str,
    provider_family: str,
) -> AttemptResultV1:
    pricing = project_cost(
        provider_family=provider_family,
        configured_model=outcome.configured_model,
        provider_request_started_at_utc=outcome.provider_request_started_at_utc,
        prompt_tokens=outcome.prompt_tokens,
        prompt_cache_hit_tokens=outcome.prompt_cache_hit_tokens,
        prompt_cache_miss_tokens=outcome.prompt_cache_miss_tokens,
        completion_tokens=outcome.completion_tokens,
    )
    values = _blank_attempt()
    values.update(
        attempt_id=request.attempt_id,
        unit_id=request.unit_id,
        planned_ai_unit_id=planned,
        attempt_ordinal=request.attempt_ordinal,
        trace_origin=trace_origin,
        result_kind=("provider_failed" if not outcome.ok else "parsed" if parse_result == "parsed" else "parse_rejected"),
        provider_call_made=True,
        http_status=outcome.http_status,
        provider_latency_ms=outcome.provider_latency_ms,
        raw_response_present=outcome.raw_response_json is not None,
        raw_response_relative_path=(
            context_store_path(context.call_key) if outcome.raw_response_json is not None else None
        ),
        parse_result=parse_result,
        canonical_accepted=False,
        prompt_tokens=outcome.prompt_tokens,
        prompt_cache_hit_tokens=outcome.prompt_cache_hit_tokens,
        prompt_cache_miss_tokens=outcome.prompt_cache_miss_tokens,
        completion_tokens=outcome.completion_tokens,
        reasoning_tokens=outcome.reasoning_tokens,
        total_tokens=outcome.total_tokens,
        provider_request_started_at_utc=outcome.provider_request_started_at_utc,
        pricing_version=pricing.pricing_version,
        pricing_tier=pricing.pricing_tier,
        cost_estimate_cny=pricing.cost_estimate_cny,
        usage_status=outcome.usage_status,
        call_state="terminal",
    )
    return _make_attempt(values, experiment_id="exp1")


def context_store_path(call_key: str) -> str:
    from urllib.parse import quote
    return f"responses/{quote(call_key, safe='')}.json"


def _fixed_attempt(request: ExecutionRequest, planned: str, trace: UnitTraceV1, selected: Any) -> AttemptResultV1:
    source = selected.attempt
    values = _blank_attempt()
    values.update(
        attempt_id=request.attempt_id,
        unit_id=request.unit_id,
        planned_ai_unit_id=planned,
        attempt_ordinal=request.attempt_ordinal,
        trace_origin=None,
        result_kind=source.result_kind,
        provider_call_made=False,
        raw_response_present=False,
        canonical_accepted=False,
        usage_status="source_trace",
        source_response_slot_id=f"{trace.case_id}:0:{planned}:{selected.source_attempt_ordinal}",
        source_response_consumed=True,
        source_attempt_ordinal=selected.source_attempt_ordinal,
        source_attempt_fallback_used=selected.source_attempt_fallback_used,
        source_trace_origin=trace.trace_origin,
        source_result_kind=source.result_kind,
        source_case_id=trace.case_id,
        source_repeat_id=0,
        source_planned_ai_unit_id=planned,
        source_unit_candidate_start=trace.candidate_start,
        source_unit_candidate_end=trace.candidate_end,
        source_lemma_node_id=trace.lemma_node_id,
        source_dependency_path=trace.dependency_path,
        source_latency_ms=source.provider_latency_ms,
        source_total_tokens=source.total_tokens,
        source_cost_estimate_cny=source.cost_estimate_cny,
        source_prompt_tokens=source.prompt_tokens,
        source_prompt_cache_hit_tokens=source.prompt_cache_hit_tokens,
        source_prompt_cache_miss_tokens=source.prompt_cache_miss_tokens,
        source_completion_tokens=source.completion_tokens,
        source_reasoning_tokens=source.reasoning_tokens,
        source_pricing_version=source.pricing_version,
        source_pricing_tier=source.pricing_tier,
        call_state="not_started",
    )
    return _make_attempt(values, experiment_id="exp2")


def _blank_attempt() -> dict[str, Any]:
    return {item.name: None for item in fields(AttemptResultV1) if item.name != "missing_reason"}


def _make_attempt(values: dict[str, Any], *, experiment_id: str) -> AttemptResultV1:
    reasons = {
        f"attempts[].{name}": "not_applicable_or_unavailable"
        for name, value in values.items()
        if value is None
    }
    attempt = AttemptResultV1(**values, missing_reason=reasons)
    attempt.validate(experiment_id=experiment_id)
    return attempt


def _failed_source_outcome(trace: UnitTraceV1, source: AttemptResultV1) -> ProviderCallResultV1:
    outcome = ProviderCallResultV1(
        ok=False, content_text=None, reasoning_content=None, raw_response_json=None,
        prompt_tokens=source.prompt_tokens, prompt_cache_hit_tokens=source.prompt_cache_hit_tokens,
        prompt_cache_miss_tokens=source.prompt_cache_miss_tokens, completion_tokens=source.completion_tokens,
        reasoning_tokens=source.reasoning_tokens, total_tokens=source.total_tokens,
        provider_request_started_at_utc=source.provider_request_started_at_utc,
        provider_latency_ms=source.provider_latency_ms, configured_model=trace.configured_model,
        requested_model=trace.requested_model, resolved_model=trace.resolved_model,
        provider_response_id=None, finish_reason=None, http_status=source.http_status,
        error_kind="source_attempt_failed", error_message="selected source attempt has no response",
        usage_status=source.usage_status,
    )
    outcome.validate()
    return outcome


def _planned_id(request: ExecutionRequest) -> str:
    planned = (request.soft_hints or {}).get("planned_ai_unit_id")
    if not isinstance(planned, str) or not planned:
        raise ValueError("ExecutionRequest lacks planned_ai_unit_id")
    return planned


def _semantics(store: ArtifactStore, request: ExecutionRequest, domain: str) -> tuple[int | None, int | None, str | None, list[str] | None]:
    if domain == "factorization":
        body = _read_json(store, request.input_artifact_refs["range_input"])
        return int(body["range_start"]), int(body["range_end"]), None, None
    hints = request.soft_hints or {}
    return None, None, str(hints["lemma_node_id"]), list(hints["dependency_path"])


def _check_semantics(store: ArtifactStore, request: ExecutionRequest, trace: UnitTraceV1) -> None:
    start, end, lemma, dependency = _semantics(store, request, trace.domain)
    if (trace.candidate_start, trace.candidate_end, trace.lemma_node_id, trace.dependency_path) != (start, end, lemma, dependency):
        raise ValueError("fixed trace ordinary semantics differ from current request")


def _read_json(store: ArtifactStore, ref: Any) -> dict[str, Any]:
    if ref is None:
        raise ValueError("required artifact ref is absent")
    value = json.loads(store.read_bytes(ref).decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("artifact JSON must be an object")
    return value


def _response_content(body: Any) -> Any:
    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None


def _call_key(root: RootKeyV1, planned: str, ordinal: int) -> str:
    return ":".join((*root[:3], str(root[3]), planned, str(ordinal)))


def _safe(value: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in value)


__all__ = [
    "FixedTraceSubmissionAdapter",
    "ProviderConditionError",
    "ProviderSubmissionAdapter",
]
