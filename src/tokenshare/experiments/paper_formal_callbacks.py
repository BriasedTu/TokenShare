"""Experiment-specific runtime strategies for formal paper execution."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from tokenshare.core.models import ArtifactRef
from tokenshare.experiments.paper_exp5_model_comparison import (
    build_exp5_model_execution_rows,
)
from tokenshare.experiments.paper_models import digest_json
from tokenshare.experiments.paper_online_checks import (
    CapabilityCallPlan,
    CapabilityLifecycleEvidence,
    CurrentProviderAttemptEvidence,
    Exp2OnlineConditionRef,
    Exp3OnlineRootRef,
    TypedEvidenceRef,
    freeze_paper_online_checks_plan,
    produce_capability_online_evidence as _produce_capability_online_evidence,
    produce_exp3_online_recovery_evidence as _produce_exp3_online_recovery_evidence,
)
from tokenshare.experiments.paper_workers import (
    PaperAIUnit,
    WorkerDeathKillPoint,
    freeze_worker_death_plan,
    record_worker_death_observation,
)
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.local_runtime.logical_scheduler import (
    LOGICAL_SOURCE_LATENCY_1X,
    ONLINE_REAL_TIME,
    LogicalSourceLatencyScheduler,
)
from tokenshare.local_runtime.contracts import GateDirective


@dataclass(frozen=True, kw_only=True)
class FormalStrategyResult:
    ordered_case_ids: tuple[str, ...]
    outcomes: tuple[Any, ...]
    events: tuple[dict[str, Any], ...]
    metrics: dict[str, Any]
    status: str = "completed"
    fault_records: tuple[dict[str, Any], ...] = ()
    replacement_attempts: tuple[Any, ...] = ()
    model_execution_records: tuple[dict[str, Any], ...] = ()
    persisted_event_refs: tuple[ArtifactRef, ...] = ()
    schema_version: str = "tokenshare.paper_formal_strategy.v1"


@dataclass(frozen=True, kw_only=True)
class RuntimeTimingPolicy:
    trace_delay_policy: str
    logical_scheduler: LogicalSourceLatencyScheduler | None
    current_provider_call_count: int


class PaperOnlineProviderEvidenceCallback:
    """在 AI executor 已持久化 raw/provenance/usage 后保存 typed projection objects。"""

    def __init__(self, *, attempt_ordinal: int | None, scope_identity: Mapping[str, Any]):
        if attempt_ordinal is not None and (
            isinstance(attempt_ordinal, bool)
            or not isinstance(attempt_ordinal, int)
            or attempt_ordinal < 0
        ):
            raise ValueError("attempt_ordinal must be a nonnegative integer")
        self._attempt_ordinal = attempt_ordinal
        self._scope_identity = dict(scope_identity)
        self._captures: dict[str, CurrentProviderAttemptEvidence] = {}
        self._store: ArtifactStore | None = None
        self._candidate_refs_by_attempt: dict[str, dict[str, ArtifactRef]] = {}
        self._submission_by_protocol_attempt: dict[str, str] = {}
        self._request_id_by_protocol_attempt: dict[str, str] = {}
        self._rejection_ref: TypedEvidenceRef | None = None
        self._requeue_ref: TypedEvidenceRef | None = None
        self._replacement_attempt_ref: TypedEvidenceRef | None = None

    @classmethod
    def for_capability_call(
        cls, call: CapabilityCallPlan
    ) -> "PaperOnlineProviderEvidenceCallback":
        plan = freeze_paper_online_checks_plan()
        try:
            index = plan.capability_calls.index(call)
        except ValueError as exc:
            raise ValueError("capability call is not in authoritative plan") from exc
        return cls(
            attempt_ordinal=call.attempt_ordinal,
            scope_identity={
                "scope_kind": "capability",
                "plan_digest": plan.plan_digest,
                "profile_digest": plan.profile_digest,
                "budget_digest": plan.budget_digest,
                "call_index": index,
                "domain": call.domain,
                "phase": call.phase,
                "case_id": call.case_id,
                "planned_ai_unit_id": call.planned_ai_unit_id,
                "controlled_rejection": call.controlled_rejection,
            },
        )

    @classmethod
    def for_capability_domain(
        cls, domain: str
    ) -> "PaperOnlineProviderEvidenceCallback":
        plan = freeze_paper_online_checks_plan()
        matching = tuple(call for call in plan.capability_calls if call.domain == domain)
        if len(matching) != 2:
            raise ValueError("capability domain is not authoritative")
        return cls(
            attempt_ordinal=None,
            scope_identity={
                "scope_kind": "capability_domain",
                "domain": domain,
                "plan_digest": plan.plan_digest,
                "profile_digest": plan.profile_digest,
                "budget_digest": plan.budget_digest,
            },
        )

    @classmethod
    def for_exp2_condition(
        cls, condition_ref: Exp2OnlineConditionRef
    ) -> "PaperOnlineProviderEvidenceCallback":
        plan = freeze_paper_online_checks_plan()
        if condition_ref not in plan.exp2_condition_refs:
            raise ValueError("Exp2 condition ref is not authoritative")
        return cls(
            attempt_ordinal=0,
            scope_identity={
                "scope_kind": "exp2_online",
                "condition_id": condition_ref.condition_id,
                "condition_digest": condition_ref.condition_digest,
                "plan_digest": plan.plan_digest,
                "profile_digest": plan.profile_digest,
                "budget_digest": plan.budget_digest,
            },
        )

    @classmethod
    def for_exp3_attempt(
        cls, condition_ref: Exp3OnlineRootRef, *, attempt_ordinal: int
    ) -> "PaperOnlineProviderEvidenceCallback":
        plan = freeze_paper_online_checks_plan()
        if condition_ref not in plan.exp3_root_refs:
            raise ValueError("Exp3 condition ref is not authoritative")
        return cls(
            attempt_ordinal=attempt_ordinal,
            scope_identity={
                "scope_kind": "exp3_online",
                "condition_id": condition_ref.condition_id,
                "condition_digest": condition_ref.condition_digest,
                "plan_digest": plan.plan_digest,
                "profile_digest": plan.profile_digest,
                "budget_digest": plan.budget_digest,
            },
        )

    def __call__(self, **context: Any) -> None:
        store = context.get("artifact_store")
        request = context.get("request")
        submission_id = str(context.get("submission_id") or "")
        submitted_at = str(context.get("submitted_at") or "")
        if not isinstance(store, ArtifactStore):
            raise TypeError("official callback requires ArtifactStore")
        self._store = store
        for name in ("raw_output_ref", "provenance_ref", "usage_ref"):
            if not isinstance(context.get(name), ArtifactRef) or not store.verify(context[name]):
                raise ValueError(f"official callback requires verified {name}")
        if not submission_id or not submitted_at:
            raise ValueError("official callback requires submission identity and time")
        protocol_attempt_id = str(getattr(request, "attempt_id", "") or "")
        unit_id = str(getattr(request, "unit_id", "") or "")
        if not protocol_attempt_id or not unit_id:
            raise ValueError("official callback request/attempt identity mismatch")
        self._submission_by_protocol_attempt[protocol_attempt_id] = submission_id
        self._request_id_by_protocol_attempt[protocol_attempt_id] = str(
            getattr(request, "request_id", "") or ""
        )
        attempt_ordinal = self._attempt_ordinal
        scope_identity = dict(self._scope_identity)
        if scope_identity.get("scope_kind") == "capability_domain":
            ordinal_value = getattr(request, "attempt_ordinal", None)
            if isinstance(ordinal_value, bool) or not isinstance(ordinal_value, int):
                raise ValueError("official capability callback requires persisted ordinal")
            attempt_ordinal = ordinal_value
            plan = freeze_paper_online_checks_plan()
            matches = tuple(
                (index, call)
                for index, call in enumerate(plan.capability_calls)
                if call.domain == scope_identity["domain"]
                and call.attempt_ordinal == attempt_ordinal
            )
            if len(matches) != 1:
                raise ValueError("official capability attempt ordinal is not planned")
            call_index, call = matches[0]
            scope_identity = {
                "scope_kind": "capability",
                "plan_digest": plan.plan_digest,
                "profile_digest": plan.profile_digest,
                "budget_digest": plan.budget_digest,
                "call_index": call_index,
                "domain": call.domain,
                "phase": call.phase,
                "case_id": call.case_id,
                "planned_ai_unit_id": call.planned_ai_unit_id,
                "controlled_rejection": call.controlled_rejection,
            }
        if attempt_ordinal is None:
            raise ValueError("official callback attempt ordinal is unresolved")
        provenance_body = _read_callback_json(store, context["provenance_ref"])
        usage_body = _read_callback_json(store, context["usage_ref"])
        attempts = provenance_body.get("attempts")
        if not isinstance(attempts, list) or not attempts or not isinstance(attempts[-1], Mapping):
            raise ValueError("official callback provenance has no provider attempt")
        provider_attempt = dict(attempts[-1])
        request_identity = provider_attempt.get("provider_request_identity")
        if not isinstance(request_identity, Mapping):
            raise ValueError("official callback provenance has no request identity")
        usage_summary = usage_body.get("usage_summary")
        if not isinstance(usage_summary, Mapping):
            raise ValueError("official callback usage snapshot is invalid")
        prompt_ref = getattr(request, "prompt_package_ref", None)
        if not isinstance(prompt_ref, ArtifactRef) or not store.verify(prompt_ref):
            raise ValueError("official callback requires verified prompt package")

        common = {
            "submission_id": submission_id,
            "attempt_id": submission_id,
            "attempt_ordinal": attempt_ordinal,
            "unit_id": unit_id,
            "protocol_attempt_id": protocol_attempt_id,
            "occurred_at": submitted_at,
        }
        refs: dict[str, ArtifactRef] = {}
        refs["attempt"] = _save_online_callback_object(store, "attempt", submission_id, common, submitted_at)
        refs["request_body"] = _save_online_callback_object(
            store,
            "request_body",
            submission_id,
            {**common, "prompt_package_ref": prompt_ref.to_dict(), "provider_request_identity": dict(request_identity)},
            submitted_at,
        )
        refs["provider_dispatch"] = _save_online_callback_object(
            store, "provider_dispatch", submission_id, {**common, "provider_request_identity": dict(request_identity)}, submitted_at
        )
        refs["provider_attempt"] = _save_online_callback_object(
            store,
            "provider_attempt",
            submission_id,
            {**common, "actual_call": True, "provider_attempt_count": usage_summary.get("provider_attempt_count"), "provider_attempt": provider_attempt},
            submitted_at,
        )
        refs["latency"] = _save_online_callback_object(
            store, "latency", submission_id, {**common, "provider_latency_ms": provider_attempt.get("latency_ms")}, submitted_at
        )
        refs["pricing"] = _save_online_callback_object(
            store,
            "pricing",
            submission_id,
            {**common, "pricing_snapshot": usage_summary.get("pricing_snapshot"), "cost_estimate": usage_summary.get("cost_estimate")},
            submitted_at,
        )
        refs["model_record"] = _save_online_callback_object(
            store,
            "model_record",
            submission_id,
            {**common, "provider": context.get("provider_family"), "model": context.get("model"), "entry_id": context.get("entry_id")},
            submitted_at,
        )
        refs.update(
            {
                "raw_or_failure": context["raw_output_ref"],
                "provenance": context["provenance_ref"],
                "usage": context["usage_ref"],
            }
        )
        callback_ref = _save_online_callback_object(
            store,
            "callback_record",
            submission_id,
            {**common, "scope_identity": scope_identity, "object_refs": {role: ref.to_dict() for role, ref in refs.items()}},
            submitted_at,
        )
        self._captures[submission_id] = CurrentProviderAttemptEvidence(
            attempt_id=submission_id,
            attempt_ordinal=attempt_ordinal,
            unit_id=unit_id,
            provider=str(context.get("provider_family") or ""),
            model=str(context.get("model") or ""),
            entry_id=str(context.get("entry_id") or ""),
            submitted_at=submitted_at,
            attempt_ref=TypedEvidenceRef(role="attempt", artifact_ref=refs["attempt"]),
            request_body_ref=TypedEvidenceRef(role="request_body", artifact_ref=refs["request_body"]),
            dispatch_ref=TypedEvidenceRef(role="provider_dispatch", artifact_ref=refs["provider_dispatch"]),
            provider_attempt_ref=TypedEvidenceRef(role="provider_attempt", artifact_ref=refs["provider_attempt"]),
            raw_or_failure_ref=TypedEvidenceRef(role="raw_or_failure", artifact_ref=refs["raw_or_failure"]),
            provenance_ref=TypedEvidenceRef(role="provenance", artifact_ref=refs["provenance"]),
            usage_ref=TypedEvidenceRef(role="usage", artifact_ref=refs["usage"]),
            latency_ref=TypedEvidenceRef(role="latency", artifact_ref=refs["latency"]),
            pricing_ref=TypedEvidenceRef(role="pricing", artifact_ref=refs["pricing"]),
            model_record_ref=TypedEvidenceRef(role="model_record", artifact_ref=refs["model_record"]),
            callback_record_ref=TypedEvidenceRef(role="callback_record", artifact_ref=callback_ref),
        )

    def require_capture(self, submission_id: str) -> CurrentProviderAttemptEvidence:
        try:
            return self._captures[submission_id]
        except KeyError as exc:
            raise ValueError("official callback capture is not persisted") from exc

    def require_capability_captures(self) -> tuple[CurrentProviderAttemptEvidence, ...]:
        values = tuple(sorted(self._captures.values(), key=lambda item: item.attempt_ordinal))
        if tuple(item.attempt_ordinal for item in values) != (0, 1):
            raise ValueError("official capability lifecycle requires ordinals 0 and 1")
        return values

    def require_capability_lifecycle(self) -> CapabilityLifecycleEvidence:
        captures = self.require_capability_captures()
        if (
            self._rejection_ref is None
            or self._requeue_ref is None
            or self._replacement_attempt_ref is None
        ):
            raise ValueError("official capability verifier/checker/requeue lifecycle is incomplete")
        domain = str(self._scope_identity.get("domain") or "")
        return CapabilityLifecycleEvidence(
            domain=domain,
            rejection_kind=(
                "verification_rejected"
                if domain == "factorization"
                else "checker_rejected"
            ),
            initial_attempt_id=captures[0].attempt_id,
            initial_raw_ref=TypedEvidenceRef(
                role="initial_source_raw",
                artifact_ref=captures[0].raw_or_failure_ref.artifact_ref,
            ),
            rejection_ref=self._rejection_ref,
            requeue_ref=self._requeue_ref,
            replacement_attempt_id=captures[1].attempt_id,
            replacement_attempt_ordinal=captures[1].attempt_ordinal,
            replacement_attempt_ref=self._replacement_attempt_ref,
        )

    def after_raw_output_persisted(self, context: Any) -> None:
        return None

    def after_parsed_candidate_persisted(self, context: Any) -> None:
        refs = getattr(context, "candidate_output_refs", None)
        attempt_id = str(getattr(context, "attempt_id", "") or "")
        if attempt_id and isinstance(refs, Mapping) and all(
            isinstance(ref, ArtifactRef) for ref in refs.values()
        ):
            self._candidate_refs_by_attempt[attempt_id] = dict(refs)
        return None

    def before_parser(self, context: Any) -> None:
        return None

    def before_verification(self, context: Any) -> None:
        attempt = getattr(context, "attempt", None)
        protocol_attempt_id = str(getattr(attempt, "attempt_id", "") or "")
        ordinal = getattr(attempt, "attempt_ordinal", None)
        if ordinal == 1 and self._requeue_ref is not None:
            capture = self._captures.get(
                self._submission_by_protocol_attempt.get(protocol_attempt_id, "")
            )
            if capture is not None and capture.attempt_ref is not None:
                self._replacement_attempt_ref = TypedEvidenceRef(
                    role="new_attempt", artifact_ref=capture.attempt_ref.artifact_ref
                )
        return None

    def before_requeue(self, context: Any) -> GateDirective | None:
        if self._scope_identity.get("scope_kind") != "capability_domain":
            return None
        if getattr(context, "trigger", None) != "verification_rejected":
            return None
        store = self._store
        attempt = getattr(context, "attempt", None)
        protocol_attempt_id = str(getattr(attempt, "attempt_id", "") or "")
        submission_id = self._submission_by_protocol_attempt.get(protocol_attempt_id, "")
        if not isinstance(store, ArtifactStore) or not submission_id:
            raise ValueError("official capability rejection has no persisted provider attempt")
        capture = self._captures.get(submission_id)
        candidates = self._candidate_refs_by_attempt.get(protocol_attempt_id, {})
        decision = getattr(context, "decision", None)
        if capture is not None and capture.attempt_ordinal == 1:
            return GateDirective(stop=True)
        if (
            capture is None
            or capture.attempt_ordinal != 0
            or capture.raw_or_failure_ref is None
            or not candidates
            or not isinstance(decision, Mapping)
            or decision.get("retry_allowed") is not True
            or decision.get("retry_count") != 1
        ):
            raise ValueError("official capability rejection/requeue identity mismatch")
        domain = str(self._scope_identity["domain"])
        rejection_kind = (
            "verification_rejected" if domain == "factorization" else "checker_rejected"
        )
        occurred_at = str(
            getattr(attempt, "finished_at", None)
            or getattr(attempt, "submitted_at", None)
            or capture.submitted_at
        )
        checker_request_id = None
        if domain == "lean_proof":
            request_id = self._request_id_by_protocol_attempt.get(protocol_attempt_id, "")
            checker_request_id = f"checker_{_safe_online_artifact_part(request_id)}"
            checker_artifact_id = (
                "".join(
                    character if character.isalnum() or character == "_" else "_"
                    for character in checker_request_id
                )
                + "_checker_report_json"
            )
            domain_rejection_ref = store.load_artifact_ref(checker_artifact_id)
            checker_report = json.loads(
                store.read_bytes(domain_rejection_ref).decode("utf-8")
            )
            if (
                domain_rejection_ref.artifact_type != "LeanCheckerReport"
                or checker_report.get("status") != "rejected"
                or checker_report.get("request_id") != checker_request_id
            ):
                raise ValueError("official Lean checker rejection artifact mismatch")
        else:
            domain_rejection_ref = store.save_json(
                {
                    "schema_version": "tokenshare.factor_verifier_rejection.v1",
                    "domain": domain,
                    "rejection_kind": rejection_kind,
                    "submission_id": submission_id,
                    "protocol_attempt_id": protocol_attempt_id,
                    "attempt_ordinal": 0,
                    "raw_output_ref": capture.raw_or_failure_ref.artifact_ref.to_dict(),
                    "candidate_output_refs": {
                        name: ref.to_dict() for name, ref in candidates.items()
                    },
                    "attempt_snapshot": attempt.to_dict(),
                    "occurred_at": occurred_at,
                },
                artifact_id=(
                    "factor_verifier_rejection_"
                    f"{_safe_online_artifact_part(submission_id)}"
                ),
                artifact_type="FactorVerifierRejection",
                artifact_schema_id="tokenshare.factor_verifier_rejection",
                artifact_schema_version="v1",
                source={
                    "kind": "factorization_runtime_official_verifier",
                    "protocol_attempt_id": protocol_attempt_id,
                },
                metadata={"status": "rejected", "attempt_ordinal": 0},
                created_at=occurred_at,
            )
        rejection = _save_online_callback_object(
            store,
            "controlled_rejection",
            submission_id,
            {
                "submission_id": submission_id,
                "attempt_id": submission_id,
                "protocol_attempt_id": protocol_attempt_id,
                "attempt_ordinal": 0,
                "unit_id": capture.unit_id,
                "occurred_at": occurred_at,
                "domain": domain,
                "rejection_kind": rejection_kind,
                "raw_output_ref": capture.raw_or_failure_ref.artifact_ref.to_dict(),
                "candidate_output_refs": {
                    name: ref.to_dict() for name, ref in candidates.items()
                },
                "domain_rejection_ref": domain_rejection_ref.to_dict(),
                "checker_request_id": checker_request_id,
                "attempt_snapshot": attempt.to_dict(),
                "recovery_event_refs": list(getattr(context, "recovery_event_refs", ())),
            },
            occurred_at,
        )
        self._rejection_ref = TypedEvidenceRef(
            role="controlled_rejection", artifact_ref=rejection
        )
        requeue = _save_online_callback_object(
            store,
            "requeue_decision",
            submission_id,
            {
                "submission_id": submission_id,
                "attempt_id": submission_id,
                "protocol_attempt_id": protocol_attempt_id,
                "attempt_ordinal": 0,
                "unit_id": capture.unit_id,
                "occurred_at": occurred_at,
                "domain": domain,
                "initial_attempt_id": submission_id,
                "expected_replacement_ordinal": 1,
                "decision": dict(decision),
                "rejection_ref": rejection.to_dict(),
            },
            occurred_at,
        )
        self._requeue_ref = TypedEvidenceRef(
            role="requeue_decision", artifact_ref=requeue
        )
        return None

    def before_merge(self, context: Any) -> None:
        return None

    def on_unit_progress(self, context: Any) -> None:
        return None


def produce_capability_online_evidence(**kwargs: Any) -> Any:
    """从本模块的 official callback capture 投影 capability online evidence。"""

    return _produce_capability_online_evidence(**kwargs)


def produce_exp3_online_recovery_evidence(**kwargs: Any) -> Any:
    """从 official callback 与 strategy persisted events 投影 Exp3 evidence。"""

    return _produce_exp3_online_recovery_evidence(**kwargs)


def runtime_timing_policy(*, evidence_class: str) -> RuntimeTimingPolicy:
    """把 evidence class 显式绑定到 logical 或在线 real-time 时钟域。"""

    if evidence_class == "real_model_trace_protocol_run":
        scheduler = LogicalSourceLatencyScheduler(start_ms=0)
        scheduler.bind_wall_clock_origin("2026-07-14T00:00:00Z")
        return RuntimeTimingPolicy(
            trace_delay_policy=LOGICAL_SOURCE_LATENCY_1X,
            logical_scheduler=scheduler,
            current_provider_call_count=0,
        )
    if evidence_class == "online_real_provider":
        return RuntimeTimingPolicy(
            trace_delay_policy=ONLINE_REAL_TIME,
            logical_scheduler=None,
            current_provider_call_count=1,
        )
    raise ValueError("unsupported paper runtime evidence class")


@dataclass
class ScheduledConditionAccumulator:
    """只保留condition指标所需compact facts，不持有完整root outcome。"""

    worker_count: int
    case_ids: list[str]
    events: list[dict[str, Any]]
    intervals: list[tuple[str, str]]
    dependency_edges: list[dict[str, Any]]
    missing_runtime_case_count: int = 0
    total_runtime_record_count: int = 0
    dependency_fact_count: int = 0
    critical_path_sum_ms: float = 0.0
    succeeded_unit_count: int = 0
    condition_started_at: str | None = None
    condition_ended_at: str | None = None
    provider_latency_sum_ms: float = 0.0
    provider_latency_observed: bool = False
    provider_zero_call_count: int = 0
    provider_latency_missing_reason: str | None = None
    provider_error_count: int = 0

    @classmethod
    def create(cls, worker_count: int) -> "ScheduledConditionAccumulator":
        return cls(
            worker_count=worker_count,
            case_ids=[],
            events=[],
            intervals=[],
            dependency_edges=[],
        )

    def observe(self, case_id: str, outcome: Any) -> None:
        records = _runtime_records(outcome)
        self.case_ids.append(case_id)
        self.events.extend(_protocol_events(outcome))
        if not records:
            self.missing_runtime_case_count += 1
        else:
            window = _runtime_window(outcome, records)
            if window is None:
                self.missing_runtime_case_count += 1
            else:
                started_at, ended_at = window
                if self.condition_started_at is None or _timestamp(
                    started_at
                ) < _timestamp(self.condition_started_at):
                    self.condition_started_at = started_at
                if self.condition_ended_at is None or _timestamp(
                    ended_at
                ) > _timestamp(self.condition_ended_at):
                    self.condition_ended_at = ended_at
        self.total_runtime_record_count += len(records)
        dependency_count = sum(
            bool(record["_dependency_evidence_present"])
            for record in records
        )
        self.dependency_fact_count += dependency_count
        if records and dependency_count == len(records):
            self.critical_path_sum_ms += _critical_path_ms(records)
        for record in records:
            self.intervals.append((record["started_at"], record["ended_at"]))
            self.succeeded_unit_count += int(
                record.get("result_kind") == "succeeded"
            )
            if record["_dependency_evidence_present"]:
                self.dependency_edges.extend(
                    {
                        "case_id": case_id,
                        "source_unit_id": dependency,
                        "target_unit_id": record["unit_id"],
                    }
                    for dependency in record["dependencies"]
                )
        provider_attempt_count = _field(outcome, "provider_attempt_count")
        latency_ms = _field(outcome, "provider_latency_ms")
        if (
            isinstance(provider_attempt_count, int)
            and not isinstance(provider_attempt_count, bool)
            and provider_attempt_count == 0
        ):
            self.provider_zero_call_count += 1
        elif (
            isinstance(latency_ms, (int, float))
            and not isinstance(latency_ms, bool)
            and latency_ms >= 0
        ):
            self.provider_latency_sum_ms += float(latency_ms)
            self.provider_latency_observed = True
        else:
            reason = _field(outcome, "provider_latency_unavailable_reason")
            self.provider_latency_missing_reason = (
                str(reason)
                if isinstance(reason, str) and reason
                else "missing_provider_latency_evidence"
            )
        self.provider_error_count += int(
            _field(outcome, "provider_error_kind") is not None
        )

    def finish(self, *, outcomes: tuple[Any, ...]) -> FormalStrategyResult:
        case_count = len(self.case_ids)
        complete_runtime = self.missing_runtime_case_count == 0
        observed_slots = (
            _observed_parallel_slots(
                tuple(
                    (_timestamp(started), _timestamp(ended))
                    for started, ended in self.intervals
                )
            )
            if complete_runtime
            else None
        )
        if observed_slots is not None and observed_slots > self.worker_count:
            raise ValueError("runtime facts exceed configured worker capacity")
        wall_clock_ms = (
            round(
                (
                    _timestamp(self.condition_ended_at)
                    - _timestamp(self.condition_started_at)
                )
                * 1000.0,
                3,
            )
            if self.condition_started_at is not None
            and self.condition_ended_at is not None
            and complete_runtime
            else None
        )
        dependency_complete = (
            complete_runtime
            and self.total_runtime_record_count > 0
            and self.dependency_fact_count == self.total_runtime_record_count
        )
        critical_status = (
            "complete"
            if dependency_complete
            else "unavailable"
            if not complete_runtime or self.dependency_fact_count == 0
            else "incomplete"
        )
        critical_reason = (
            None
            if dependency_complete
            else "missing_worker_execution_facts"
            if not complete_runtime
            else "missing_protocol_dependency_evidence"
            if self.dependency_fact_count == 0
            else "incomplete_protocol_dependency_evidence"
        )
        runtime_status = (
            "complete"
            if complete_runtime
            else "unavailable"
            if self.missing_runtime_case_count == case_count
            else "incomplete"
        )
        runtime_reason = (
            None
            if complete_runtime
            else "missing_worker_execution_facts"
            if self.missing_runtime_case_count == case_count
            else "incomplete_worker_execution_facts"
        )
        if self.provider_latency_missing_reason is not None:
            provider_latency: float | None = None
            provider_status = "incomplete"
            provider_reason = self.provider_latency_missing_reason
        elif self.provider_latency_observed:
            provider_latency = self.provider_latency_sum_ms
            provider_status = "complete"
            provider_reason = None
        elif case_count and self.provider_zero_call_count == case_count:
            provider_latency = 0.0
            provider_status = "not_applicable"
            provider_reason = "no_provider_attempts"
        else:
            provider_latency = None
            provider_status = "incomplete"
            provider_reason = "missing_provider_latency_evidence"
        return FormalStrategyResult(
            ordered_case_ids=tuple(self.case_ids),
            outcomes=outcomes,
            events=tuple(self.events),
            metrics={
                "worker_count": self.worker_count,
                "observed_max_parallel_slots": observed_slots,
                "condition_started_at": self.condition_started_at,
                "condition_ended_at": self.condition_ended_at,
                "wall_clock_ms": wall_clock_ms,
                "critical_path_ms": (
                    round(self.critical_path_sum_ms, 3)
                    if dependency_complete
                    else None
                ),
                "critical_path_evidence_status": critical_status,
                "critical_path_unavailable_reason": critical_reason,
                "provider_latency_sum_ms": provider_latency,
                "provider_latency_evidence_status": provider_status,
                "provider_latency_unavailable_reason": provider_reason,
                "provider_error_count": self.provider_error_count,
                "throughput_completed_units_per_second": (
                    self.succeeded_unit_count / (wall_clock_ms / 1000.0)
                    if wall_clock_ms is not None and wall_clock_ms > 0
                    else None
                ),
                "dependency_edges": self.dependency_edges,
                "applicability": "supported",
                "runtime_evidence_status": runtime_status,
                "runtime_evidence_unavailable_reason": runtime_reason,
            },
        )


@dataclass(frozen=True, kw_only=True)
class FormalAblationResult:
    mode: str
    runtime_flags: dict[str, bool]
    events: tuple[dict[str, Any], ...]
    metrics: dict[str, Any]
    schema_version: str = "tokenshare.paper_formal_ablation_strategy.v1"


def run_exp1_normal_strategy(
    *,
    ordered_case_ids: Sequence[str],
    execute_case: Callable[[str, int], Any],
) -> FormalStrategyResult:
    """按冻结顺序执行完整 Exp1 selection。"""

    return run_scheduled_cases(
        ordered_case_ids=ordered_case_ids,
        worker_count=1,
        execute_case=execute_case,
    )


def run_scheduled_cases(
    *,
    ordered_case_ids: Sequence[str],
    worker_count: int,
    execute_case: Callable[[str, int], Any],
    supported_worker_counts: Sequence[int] | None = None,
    should_continue_after_case: Callable[[str, Any], bool] | None = None,
    on_case_complete: Callable[[str, Any], None] | None = None,
    retain_outcomes: bool = True,
) -> FormalStrategyResult:
    """把 worker_count 传给每个 root runtime，并从 unit 事实派生指标。"""

    case_ids = tuple(str(case_id) for case_id in ordered_case_ids)
    if worker_count < 1:
        raise ValueError("worker_count must be positive")
    if supported_worker_counts is not None and worker_count not in {
        int(item) for item in supported_worker_counts
    }:
        return FormalStrategyResult(
            ordered_case_ids=case_ids,
            outcomes=(),
            events=(),
            metrics={
                "worker_count": worker_count,
                "observed_max_parallel_slots": 0,
                "wall_clock_ms": 0,
                "critical_path_ms": 0,
                "critical_path_evidence_status": "not_applicable",
                "critical_path_unavailable_reason": None,
                "provider_latency_sum_ms": 0,
                "provider_latency_evidence_status": "not_applicable",
                "provider_latency_unavailable_reason": "no_provider_attempts",
                "provider_error_count": 0,
                "applicability": "unsupported_worker_level",
                "runtime_evidence_status": "not_applicable",
                "runtime_evidence_unavailable_reason": None,
            },
            status="unsupported_worker_level",
        )
    if not case_ids:
        return FormalStrategyResult(
            ordered_case_ids=(),
            outcomes=(),
            events=(),
            metrics={
                "worker_count": worker_count,
                "observed_max_parallel_slots": 0,
                "wall_clock_ms": 0,
                "critical_path_ms": 0,
                "critical_path_evidence_status": "not_applicable",
                "critical_path_unavailable_reason": None,
                "provider_latency_sum_ms": 0,
                "provider_latency_evidence_status": "not_applicable",
                "provider_latency_unavailable_reason": "no_provider_attempts",
                "provider_error_count": 0,
                "applicability": "empty_selection",
                "runtime_evidence_status": "not_applicable",
                "runtime_evidence_unavailable_reason": None,
            },
        )

    if not isinstance(retain_outcomes, bool):
        raise ValueError("retain_outcomes must be a boolean")
    if on_case_complete is not None or not retain_outcomes:
        accumulator = ScheduledConditionAccumulator.create(worker_count)
        retained: list[Any] = []
        for case_id in case_ids:
            outcome = execute_case(case_id, worker_count)
            accumulator.observe(case_id, outcome)
            if on_case_complete is not None:
                on_case_complete(case_id, outcome)
            if retain_outcomes:
                retained.append(outcome)
            should_continue = not (
                should_continue_after_case is not None
                and not should_continue_after_case(case_id, outcome)
            )
            if not retain_outcomes:
                del outcome
            if not should_continue:
                break
        return accumulator.finish(outcomes=tuple(retained))

    executed_case_ids: list[str] = []
    outcome_values: list[Any] = []
    for case_id in case_ids:
        outcome = execute_case(case_id, worker_count)
        executed_case_ids.append(case_id)
        outcome_values.append(outcome)
        if (
            should_continue_after_case is not None
            and not should_continue_after_case(case_id, outcome)
        ):
            break
    case_ids = tuple(executed_case_ids)
    ordered_outcomes = tuple(outcome_values)
    records_by_case = {
        case_id: _runtime_records(outcome)
        for case_id, outcome in zip(case_ids, ordered_outcomes, strict=True)
    }
    windows_by_case = {
        case_id: _runtime_window(outcome, records_by_case[case_id])
        for case_id, outcome in zip(case_ids, ordered_outcomes, strict=True)
    }
    all_records = tuple(
        record for case_id in case_ids for record in records_by_case[case_id]
    )
    ordered_events = tuple(
        event
        for outcome in ordered_outcomes
        for event in _protocol_events(outcome)
    )
    missing_case_ids = tuple(
        case_id for case_id in case_ids if not records_by_case[case_id]
    )
    complete_runtime_evidence = not missing_case_ids
    intervals = tuple(
        (_timestamp(record["started_at"]), _timestamp(record["ended_at"]))
        for record in all_records
    )
    observed_max_slots = (
        _observed_parallel_slots(intervals) if complete_runtime_evidence else None
    )
    if observed_max_slots is not None and observed_max_slots > worker_count:
        raise ValueError("runtime facts exceed configured worker capacity")
    condition_window = (
        (
            min(
                (window for window in windows_by_case.values() if window is not None),
                key=lambda window: _timestamp(window[0]),
            )[0],
            max(
                (window for window in windows_by_case.values() if window is not None),
                key=lambda window: _timestamp(window[1]),
            )[1],
        )
        if complete_runtime_evidence
        else None
    )
    wall_clock_ms = (
        round(
            (
                _timestamp(condition_window[1])
                - _timestamp(condition_window[0])
            )
            * 1000.0,
            3,
        )
        if condition_window is not None
        else None
    )
    dependency_fact_count = sum(
        record["_dependency_evidence_present"] for record in all_records
    )
    dependency_evidence_complete = (
        complete_runtime_evidence
        and bool(all_records)
        and dependency_fact_count == len(all_records)
    )
    critical_path_evidence_status = (
        "complete"
        if dependency_evidence_complete
        else "unavailable"
        if not complete_runtime_evidence or dependency_fact_count == 0
        else "incomplete"
    )
    critical_path_unavailable_reason = (
        None
        if dependency_evidence_complete
        else (
            "missing_worker_execution_facts"
            if not complete_runtime_evidence
            else "missing_protocol_dependency_evidence"
            if dependency_fact_count == 0
            else "incomplete_protocol_dependency_evidence"
        )
    )
    critical_path_ms = (
        round(
            sum(
                _critical_path_ms(records_by_case[case_id])
                for case_id in case_ids
            ),
            3,
        )
        if dependency_evidence_complete
        else None
    )
    runtime_evidence_status = (
        "complete"
        if complete_runtime_evidence
        else "unavailable"
        if len(missing_case_ids) == len(case_ids)
        else "incomplete"
    )
    runtime_evidence_unavailable_reason = (
        None
        if complete_runtime_evidence
        else "missing_worker_execution_facts"
        if len(missing_case_ids) == len(case_ids)
        else "incomplete_worker_execution_facts"
    )
    (
        provider_latency_sum_ms,
        provider_latency_evidence_status,
        provider_latency_unavailable_reason,
    ) = _provider_latency_aggregate(
        ordered_outcomes
    )
    provider_error_count = sum(
        _field(outcome, "provider_error_kind") is not None
        for outcome in ordered_outcomes
    )
    return FormalStrategyResult(
        ordered_case_ids=case_ids,
        outcomes=ordered_outcomes,
        events=ordered_events,
        metrics={
            "worker_count": worker_count,
            "observed_max_parallel_slots": observed_max_slots,
            "condition_started_at": (
                condition_window[0]
                if condition_window is not None
                else None
            ),
            "condition_ended_at": (
                condition_window[1]
                if condition_window is not None
                else None
            ),
            "wall_clock_ms": wall_clock_ms,
            "critical_path_ms": critical_path_ms,
            "critical_path_evidence_status": critical_path_evidence_status,
            "critical_path_unavailable_reason": critical_path_unavailable_reason,
            "provider_latency_sum_ms": provider_latency_sum_ms,
            "provider_latency_evidence_status": provider_latency_evidence_status,
            "provider_latency_unavailable_reason": (
                provider_latency_unavailable_reason
            ),
            "provider_error_count": provider_error_count,
            "throughput_completed_units_per_second": (
                sum(record.get("result_kind") == "succeeded" for record in all_records)
                / (wall_clock_ms / 1000.0)
                if wall_clock_ms is not None and wall_clock_ms > 0
                else None
            ),
            "dependency_edges": [
                {
                    "case_id": case_id,
                    "source_unit_id": dependency,
                    "target_unit_id": record["unit_id"],
                }
                for case_id in case_ids
                for record in records_by_case[case_id]
                for dependency in (
                    record["dependencies"]
                    if record["_dependency_evidence_present"]
                    else ()
                )
            ],
            "applicability": "supported",
            "runtime_evidence_status": runtime_evidence_status,
            "runtime_evidence_unavailable_reason": (
                runtime_evidence_unavailable_reason
            ),
        },
    )


def run_exp3_post_ai_strategy(
    *,
    artifact_root: str | Path | None = None,
    condition_ref: Exp3OnlineRootRef | None = None,
    attempts: Sequence[Any],
    selected_target_ai_unit_ids: Sequence[str],
    fault_type: str,
    inject_fault: Callable[[Any, str], Mapping[str, Any]],
    execute_replacement: Callable[[Any], Any] | None,
    approved_identity: Mapping[str, Any],
) -> FormalStrategyResult:
    """对已持久化 provider output 注入 Exp3 fault，并按需恢复。"""

    selected = {str(item) for item in selected_target_ai_unit_ids}
    events: list[dict[str, Any]] = []
    faults: list[dict[str, Any]] = []
    replacements: list[Any] = []
    for attempt in attempts:
        unit_id = str(_required_field(attempt, "unit_id"))
        if unit_id not in selected:
            continue
        raw_ref = _artifact_ref_body(_required_field(attempt, "raw_output_ref"))
        provenance_ref = _artifact_ref_body(_required_field(attempt, "provenance_ref"))
        attempt_time = str(_field(attempt, "submitted_at") or _utc_now())
        events.append(
            {
                "event_type": "EXPERIMENT_PROVIDER_RAW_OBSERVED",
                "attempt_id": _required_field(attempt, "attempt_id"),
                "unit_id": unit_id,
                "raw_output_ref": dict(raw_ref),
                "provenance_ref": dict(provenance_ref),
                "occurred_at": attempt_time,
                **_exp3_condition_event_identity(condition_ref),
            }
        )
        fault = dict(inject_fault(attempt, fault_type))
        fault.setdefault("attempt_id", _required_field(attempt, "attempt_id"))
        fault.setdefault("unit_id", unit_id)
        fault.setdefault("fault_type", fault_type)
        faults.append(fault)
        events.append(
            {
                "event_type": "EXPERIMENT_FAULT_INJECTED",
                "attempt_id": _required_field(attempt, "attempt_id"),
                "unit_id": unit_id,
                "fault_type": fault_type,
                "occurred_at": attempt_time,
                **_exp3_condition_event_identity(condition_ref),
            }
        )
        if fault.get("requires_replacement") is not True:
            continue
        if execute_replacement is None:
            events.append(
                {
                    "event_type": "EXPERIMENT_REPLACEMENT_REQUIRED",
                    "attempt_id": _required_field(attempt, "attempt_id"),
                    "unit_id": unit_id,
                    "occurred_at": attempt_time,
                    **_exp3_condition_event_identity(condition_ref),
                }
            )
            continue
        replacement = execute_replacement(attempt)
        _validate_fixed_identity(replacement, approved_identity)
        replacements.append(replacement)
        replacement_time = str(
            _field(replacement, "submitted_at") or attempt_time
        )
        events.extend(
            (
                {
                    "event_type": "EXPERIMENT_REPLACEMENT_ATTEMPT_STARTED",
                    "attempt_id": _required_field(replacement, "attempt_id"),
                    "unit_id": unit_id,
                    "attempt_ordinal": _field(replacement, "attempt_ordinal"),
                    "occurred_at": replacement_time,
                    **_exp3_condition_event_identity(condition_ref),
                },
                {
                    "event_type": "EXPERIMENT_REPLACEMENT_ACCEPTED",
                    "attempt_id": _required_field(replacement, "attempt_id"),
                    "unit_id": unit_id,
                    "attempt_ordinal": _field(replacement, "attempt_ordinal"),
                    "occurred_at": replacement_time,
                    **_exp3_condition_event_identity(condition_ref),
                },
            )
        )
    persisted_event_refs = (
        _persist_formal_strategy_events(ArtifactStore(Path(artifact_root)), events)
        if artifact_root is not None
        else ()
    )
    return FormalStrategyResult(
        ordered_case_ids=(),
        outcomes=tuple(attempts),
        events=tuple(events),
        metrics={
            "selected_target_count": len(selected),
            "injected_fault_count": len(faults),
            "replacement_attempt_count": len(replacements),
        },
        fault_records=tuple(faults),
        replacement_attempts=tuple(replacements),
        persisted_event_refs=persisted_event_refs,
    )


def run_exp3_worker_death_strategy(
    *,
    artifact_root: str | Path,
    condition_id: str,
    repeat_id: int,
    task_id: str,
    ai_unit_ids: Sequence[str],
    dead_worker_count: int,
    kill_point: WorkerDeathKillPoint | str,
    started_at: str,
    process_tick_seconds: float = 0.01,
    runtime_observations: Sequence[Mapping[str, Any]] | None = None,
) -> FormalStrategyResult:
    """冻结 kill plan，并只从 runtime/backend 事实投影实验记录。"""

    del process_tick_seconds
    unit_ids = tuple(str(item) for item in ai_unit_ids)
    if dead_worker_count < 1 or dead_worker_count > len(unit_ids):
        raise ValueError("dead_worker_count exceeds available AI units")
    units = tuple(
        PaperAIUnit(
            task_id=task_id,
            unit_id=unit_id,
            unit_kind="formal_adapter_ai_unit",
            dependencies=(),
            depth=0,
        )
        for unit_id in unit_ids
    )
    plan = freeze_worker_death_plan(
        condition_id=condition_id,
        repeat_id=repeat_id,
        run_id=f"{condition_id}-{task_id}-worker-death",
        ai_units=units,
        target_unit_ids=unit_ids[:dead_worker_count],
        kill_point=kill_point,
    )
    plan_body = plan.to_dict()
    plan_event = {
        "event_type": "EXPERIMENT_WORKER_DEATH_PLAN_FROZEN",
        "condition_id": condition_id,
        "task_id": task_id,
        "plan_digest": digest_json(plan_body),
        "plan": plan_body,
        "selected_target_unit_ids": list(plan.selected_target_unit_ids),
        "occurred_at": started_at,
    }
    if runtime_observations is None:
        return FormalStrategyResult(
            ordered_case_ids=(task_id,),
            outcomes=(),
            events=(plan_event,),
            metrics={
                "worker_death_target_count": dead_worker_count,
                "worker_death_count": 0,
                "replacement_attempt_count": 0,
                "coordinator_survived": False,
                "applicability": "awaiting_runtime_evidence",
            },
            status="planned",
        )
    observations = tuple(runtime_observations)
    if len(observations) != dead_worker_count:
        raise ValueError("runtime observation count must match dead_worker_count")
    store = ArtifactStore(Path(artifact_root))
    records: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = [plan_event]
    replacements: list[dict[str, Any]] = []
    for target_unit_id, observation in zip(
        plan.selected_target_unit_ids,
        observations,
        strict=True,
    ):
        outcome = record_worker_death_observation(
            artifact_store=store,
            plan=plan,
            target_unit_id=target_unit_id,
            worker_fact=_required_field(observation, "worker_fact"),
            replacement_fact=_required_field(observation, "replacement_fact"),
            protocol_events=_required_field(observation, "protocol_events"),
            coordinator_pid=int(_required_field(observation, "coordinator_pid")),
            created_at=str(_field(observation, "created_at") or started_at),
        )
        record = outcome.record.to_dict()
        record["record_ref"] = outcome.record_ref.to_dict()
        records.append(record)
        replacements.append(dict(record["replacement_attempt"]))
        events.append(
            {
                "event_type": "EXPERIMENT_WORKER_DEATH_OBSERVED",
                "condition_id": condition_id,
                "task_id": task_id,
                "unit_id": target_unit_id,
                "attempt_id": record["dead_attempt"]["attempt_id"],
                "record_ref": outcome.record_ref.to_dict(),
                "protocol_event_refs": list(record["protocol_event_refs"]),
                "occurred_at": record["killed_at"],
            }
        )
        replacement = record["replacement_attempt"]
        original_replacement = _required_field(observation, "replacement_fact")
        replacement_ordinal = _field(original_replacement, "attempt_ordinal")
        if replacement_ordinal is not None:
            events.append(
                {
                    "event_type": "EXPERIMENT_REPLACEMENT_ATTEMPT_STARTED",
                    "condition_id": condition_id,
                    "task_id": task_id,
                    "unit_id": target_unit_id,
                    "attempt_id": replacement["attempt_id"],
                    "attempt_ordinal": replacement_ordinal,
                    "occurred_at": replacement["started_at"],
                }
            )
    persisted_event_refs = _persist_formal_strategy_events(store, events)
    return FormalStrategyResult(
        ordered_case_ids=(task_id,),
        outcomes=(),
        events=tuple(events),
        metrics={
            "worker_death_count": len(records),
            "replacement_attempt_count": len(records),
            "coordinator_survived": all(
                record["coordinator"]["survived"] is True for record in records
            ),
            "applicability": "runtime_evidence_observed",
        },
        fault_records=tuple(records),
        replacement_attempts=tuple(replacements),
        persisted_event_refs=persisted_event_refs,
    )


def run_exp4_ablation_strategy(
    *,
    mode: str,
    adapter_observation: Mapping[str, Any],
) -> FormalAblationResult:
    """只在 Experiment 4 wrapper 边界改变指定机制。"""

    normalized_mode = str(mode).upper()
    supported = {
        "FULL",
        "NO_VERIFICATION",
        "NO_PARSER_POLICY",
        "NO_REQUEUE",
        "NO_MERGE_GATE",
    }
    if normalized_mode not in supported:
        raise ValueError("unsupported Experiment 4 mode")
    flags = {
        "default_protocol_behavior": normalized_mode == "FULL",
        "wrong_canonical_exposed": normalized_mode == "NO_VERIFICATION",
        "raw_only_exposed": normalized_mode == "NO_PARSER_POLICY",
        "stuck_after_rejection": normalized_mode == "NO_REQUEUE",
        "premature_merge_attempted": normalized_mode == "NO_MERGE_GATE",
        "deterministic_validity_audit_retained": True,
    }
    applicable = normalized_mode != "FULL" and _ablation_applicable(
        normalized_mode,
        adapter_observation,
    )
    exposed = int(applicable)
    escaped = int(
        applicable
        and normalized_mode
        in {"NO_VERIFICATION", "NO_PARSER_POLICY"}
        and adapter_observation.get("deterministic_validity") is False
    )
    event = {
        "event_type": "EXPERIMENT_ABLATION_OBSERVED",
        "mode": normalized_mode,
        "disabled_mechanism": _disabled_mechanism(normalized_mode),
        "applicable": applicable,
        "protocol_event_refs": [
            dict(ref)
            for ref in adapter_observation.get("protocol_event_refs", ())
            if isinstance(ref, Mapping)
        ],
        "artifact_refs": [
            dict(ref)
            for ref in adapter_observation.get("artifact_refs", ())
            if isinstance(ref, Mapping)
        ],
        "deterministic_validity": adapter_observation.get(
            "deterministic_validity"
        ),
    }
    return FormalAblationResult(
        mode=normalized_mode,
        runtime_flags=flags,
        events=(event,),
        metrics={
            "applicable": applicable,
            "exposed_error_count": exposed,
            "escaped_error_count": escaped,
            "final_deterministic_validity": adapter_observation.get(
                "deterministic_validity"
            ),
        },
    )


def run_exp5_identity_strategy(
    *,
    attempts: Sequence[Any],
    approved_identity: Mapping[str, Any],
    condition_id: str,
    cohort_member_id: str,
    adapter_root: str | Path,
    task: Mapping[str, Any],
    transport_kind: str,
    model_policy: str,
    pilot_only: bool,
) -> FormalStrategyResult:
    """从 adapter 持久化的 v2 identity artifacts 构造严格 join 输入。"""

    store = ArtifactStore(adapter_root)
    records: list[dict[str, Any]] = []
    for attempt in attempts:
        _validate_fixed_identity(attempt, approved_identity)
        attempt_body = _json_mapping(attempt, "Experiment 5 attempt")
        record_ref = _required_artifact_ref(
            attempt_body,
            "model_execution_record_ref",
        )
        record_body = _read_json_artifact(store, record_ref)
        if (
            record_body.get("schema_version")
            != "tokenshare.paper_model_execution_record.v2"
        ):
            raise ValueError(
                "formal Experiment 5 requires persisted model execution v2"
            )
        if record_body.get("condition_id") != condition_id:
            raise ValueError("model execution record condition_id mismatch")
        expected_identity = record_body.get("expected_identity")
        if (
            not isinstance(expected_identity, Mapping)
            or expected_identity.get("cohort_member_id") != cohort_member_id
        ):
            raise ValueError("model execution record cohort member mismatch")
        raw_ref = _optional_artifact_ref(attempt_body, "raw_output_ref")
        request_ref = _required_artifact_ref(attempt_body, "request_ref")
        provenance_ref = _required_artifact_ref(attempt_body, "provenance_ref")
        usage_ref = _required_artifact_ref(attempt_body, "usage_ref")
        records.append(
            {
                "record": record_body,
                "record_ref": record_ref,
                "raw_output": (
                    _read_json_artifact(store, raw_ref)
                    if raw_ref is not None
                    else None
                ),
                "request": _read_json_artifact(store, request_ref),
                "provenance": _read_json_artifact(store, provenance_ref),
                "usage": _read_json_artifact(store, usage_ref),
                "attempt": attempt_body,
                "task": dict(task),
                "transport_kind": transport_kind,
                "model_policy": model_policy,
                "provider_errors": [],
                "pilot_only": pilot_only,
                "formal_strict_join": True,
            }
        )
    rows = build_exp5_model_execution_rows(
        {"model_execution_records": records}
    )
    return FormalStrategyResult(
        ordered_case_ids=(),
        outcomes=tuple(attempts),
        events=(),
        metrics={
            "model_execution_record_count": len(records),
            "identity_status_by_attempt": {
                str(row["attempt_id"]): str(row["identity_status"])
                for row in rows
            },
        },
        model_execution_records=tuple(records),
    )


def finalize_exp5_identity_evidence(
    *,
    attempts: Sequence[Any],
    task: Mapping[str, Any],
    identity_status_by_attempt: Mapping[str, str],
    cohort_member_id: str,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...], bool]:
    """把身份审计结论转成不可误报成功的 task/attempt 终态。"""

    identity_mismatch = any(
        status == "model_identity_mismatch"
        for status in identity_status_by_attempt.values()
    )
    enriched_attempts = tuple(
        {
            **_json_mapping(attempt, "Experiment 5 attempt"),
            "cohort_member_id": cohort_member_id,
            "model_identity_audit": identity_status_by_attempt[
                str(_required_field(attempt, "attempt_id"))
            ],
            **(
                {
                    "attempt_status": "model_identity_mismatch",
                    "error_kind": "model_identity_mismatch",
                    "paper_eligible": False,
                }
                if identity_status_by_attempt[
                    str(_required_field(attempt, "attempt_id"))
                ]
                == "model_identity_mismatch"
                else {}
            ),
        }
        for attempt in attempts
    )
    task_body = dict(task)
    task_body["cohort_member_id"] = cohort_member_id
    if identity_mismatch:
        task_body.update(
            {
                "root_status": "ineligible",
                "error_kind": "model_identity_mismatch",
                "paper_eligible": False,
            }
        )
    return task_body, enriched_attempts, identity_mismatch


def exp5_identity_fail_stop_required(adapter_result: Any) -> bool:
    """只让实际持久化的 resolved-model 身份失败触发 condition-local 停机。"""

    if adapter_result is None:
        return False
    attempts = _field(adapter_result, "attempt_results")
    if attempts is None:
        attempts = _field(adapter_result, "attempts")
    return any(
        _field(attempt, "model_identity_audit") == "model_identity_mismatch"
        for attempt in (attempts or ())
    )


def _ablation_applicable(mode: str, observation: Mapping[str, Any]) -> bool:
    field_by_mode = {
        "NO_VERIFICATION": "candidate_rejected",
        "NO_PARSER_POLICY": "parse_failed",
        "NO_REQUEUE": "replacement_created",
        "NO_MERGE_GATE": "merge_gate_blocked",
    }
    field = field_by_mode.get(mode)
    if field is None:
        return False
    return observation.get(field) is True


def _disabled_mechanism(mode: str) -> str | None:
    return {
        "FULL": None,
        "NO_VERIFICATION": "verification",
        "NO_PARSER_POLICY": "parser_policy",
        "NO_REQUEUE": "requeue",
        "NO_MERGE_GATE": "merge_gate",
    }[mode]


def _validate_fixed_identity(attempt: Any, approved: Mapping[str, Any]) -> None:
    expected = {
        "provider": approved.get("provider", approved.get("provider_family")),
        "model": approved.get("model", approved.get("provider_model_id")),
        "entry_id": approved.get("entry_id", approved.get("model_entry_id")),
    }
    for field_name, expected_value in expected.items():
        if expected_value is None:
            raise ValueError(f"approved identity is missing {field_name}")
        if _field(attempt, field_name) != expected_value:
            raise ValueError(f"model failover detected for {field_name}")


def _runtime_records(outcome: Any) -> tuple[dict[str, Any], ...]:
    raw_records = _field(outcome, "runtime_records") or ()
    if not raw_records:
        observation = _runtime_observation(outcome)
        raw_records = (
            observation.get("worker_execution_facts", ())
            if observation is not None
            else ()
        )
    if not isinstance(raw_records, Sequence) or isinstance(
        raw_records,
        (str, bytes),
    ):
        raise ValueError("runtime records must be a sequence")
    records: list[dict[str, Any]] = []
    for raw in raw_records:
        if isinstance(raw, Mapping):
            record = dict(raw)
        else:
            to_dict = getattr(raw, "to_dict", None)
            if not callable(to_dict) or not isinstance(to_dict(), Mapping):
                raise TypeError("runtime record must be a mapping or expose to_dict")
            record = dict(to_dict())
        unit_id = record.get("unit_id")
        if not isinstance(unit_id, str) or not unit_id:
            raise ValueError("runtime record unit_id is required")
        for timestamp_field in ("started_at", "ended_at"):
            if not isinstance(record.get(timestamp_field), str):
                raise ValueError(f"runtime record {timestamp_field} is required")
            _timestamp(record[timestamp_field])
        if _timestamp(record["ended_at"]) < _timestamp(record["started_at"]):
            raise ValueError("runtime record ended_at precedes started_at")
        dependency_evidence_present = "dependencies" in record
        dependencies = record.get("dependencies", ())
        if not isinstance(dependencies, (list, tuple)):
            raise ValueError("runtime record dependencies must be a sequence")
        record["dependencies"] = [str(item) for item in dependencies]
        record["_dependency_evidence_present"] = dependency_evidence_present
        records.append(record)
    unit_counts: dict[str, int] = {}
    for record in records:
        unit_id = str(record["unit_id"])
        unit_counts[unit_id] = unit_counts.get(unit_id, 0) + 1
    seen_node_ids: set[str] = set()
    for record in records:
        attempt_id = record.get("attempt_id")
        if attempt_id is not None and (
            not isinstance(attempt_id, str) or not attempt_id
        ):
            raise ValueError("runtime record attempt_id must be a non-empty string")
        unit_id = str(record["unit_id"])
        if attempt_id is None and unit_counts[unit_id] > 1:
            raise ValueError(
                "repeated runtime unit records require unique attempt_id values"
            )
        node_id = str(attempt_id or unit_id)
        if node_id in seen_node_ids:
            raise ValueError("runtime record attempt identity must be unique")
        seen_node_ids.add(node_id)
        record["_runtime_node_id"] = node_id
    return tuple(records)


def _runtime_observation(outcome: Any) -> dict[str, Any] | None:
    candidates = (
        _field(outcome, "runtime_observation"),
        _field(_field(outcome, "task"), "runtime_observation"),
        _field(
            _field(
                _field(_field(outcome, "adapter_result"), "run_evidence"),
                "protocol_runtime",
            ),
            "runtime_observation",
        ),
    )
    raw = next((item for item in candidates if item is not None), None)
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("runtime observation must be a mapping")
    observation = dict(raw)
    schema_version = observation.get("schema_version")
    if schema_version not in (None, "tokenshare.protocol_runtime_observation.v1"):
        raise ValueError("runtime observation schema is unsupported")
    facts = observation.get("worker_execution_facts")
    if not isinstance(facts, Sequence) or isinstance(facts, (str, bytes)):
        raise ValueError("runtime observation worker facts are required")
    return observation


def _runtime_window(
    outcome: Any,
    records: Sequence[Mapping[str, Any]],
) -> tuple[str, str] | None:
    if not records:
        return None
    observation = _runtime_observation(outcome)
    if observation is not None:
        started_at = observation.get("runtime_started_at")
        ended_at = observation.get("runtime_ended_at")
        if not isinstance(started_at, str) or not isinstance(ended_at, str):
            raise ValueError("runtime observation window is required")
        if _timestamp(ended_at) < _timestamp(started_at):
            raise ValueError("runtime observation ended before it started")
        return started_at, ended_at
    return (
        min(records, key=lambda record: _timestamp(str(record["started_at"])))[
            "started_at"
        ],
        max(records, key=lambda record: _timestamp(str(record["ended_at"])))[
            "ended_at"
        ],
    )


def _protocol_events(outcome: Any) -> tuple[dict[str, Any], ...]:
    raw_events = _field(outcome, "protocol_events") or ()
    events: list[dict[str, Any]] = []
    for raw in raw_events:
        if isinstance(raw, Mapping):
            events.append(dict(raw))
            continue
        to_dict = getattr(raw, "to_dict", None)
        if not callable(to_dict):
            raise TypeError("protocol event must be a mapping or expose to_dict")
        body = to_dict()
        if not isinstance(body, Mapping):
            raise TypeError("protocol event to_dict must return a mapping")
        events.append(dict(body))
    return tuple(events)


def _observed_parallel_slots(intervals: Sequence[tuple[float, float]]) -> int:
    boundaries = sorted(
        (
            boundary
            for started_at, ended_at in intervals
            for boundary in ((started_at, 1), (ended_at, -1))
        ),
        key=lambda item: (item[0], item[1]),
    )
    active = 0
    observed = 0
    for _timestamp_value, delta in boundaries:
        active += delta
        observed = max(observed, active)
    return observed


def _critical_path_ms(records: Sequence[Mapping[str, Any]]) -> float:
    if not records:
        return 0.0
    if any(record.get("_dependency_evidence_present") is not True for record in records):
        raise ValueError("critical path requires explicit protocol dependency evidence")
    by_id = {str(record["_runtime_node_id"]): record for record in records}
    records_by_unit: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        records_by_unit.setdefault(str(record["unit_id"]), []).append(record)
    for unit_records in records_by_unit.values():
        unit_records.sort(
            key=lambda record: (
                int(record["execution_index"])
                if isinstance(record.get("execution_index"), int)
                and not isinstance(record.get("execution_index"), bool)
                else 2**63 - 1,
                _timestamp(str(record["started_at"])),
                str(record["_runtime_node_id"]),
            )
        )
    dependency_nodes: dict[str, tuple[str, ...]] = {}
    for unit_records in records_by_unit.values():
        previous: Mapping[str, Any] | None = None
        for record in unit_records:
            node_id = str(record["_runtime_node_id"])
            current_started_at = _timestamp(str(record["started_at"]))
            resolved: list[str] = []
            if (
                previous is not None
                and _timestamp(str(previous["ended_at"])) <= current_started_at
            ):
                resolved.append(str(previous["_runtime_node_id"]))
            for dependency in record["dependencies"]:
                if dependency in by_id:
                    candidate = by_id[dependency]
                    if candidate is record:
                        raise ValueError("runtime dependency graph contains a cycle")
                else:
                    candidates = [
                        candidate
                        for candidate in records_by_unit.get(dependency, ())
                        if candidate is not record
                        and _timestamp(str(candidate["ended_at"]))
                        <= current_started_at
                    ]
                    if not candidates:
                        raise ValueError(
                            "runtime dependency graph references an unknown unit"
                        )
                    candidate = max(
                        candidates,
                        key=lambda item: (
                            _timestamp(str(item["ended_at"])),
                            str(item["_runtime_node_id"]),
                        ),
                    )
                candidate_id = str(candidate["_runtime_node_id"])
                if candidate_id not in resolved:
                    resolved.append(candidate_id)
            dependency_nodes[node_id] = tuple(resolved)
            previous = record
    memo: dict[str, float] = {}
    visiting: set[str] = set()

    def visit(node_id: str) -> float:
        if node_id in memo:
            return memo[node_id]
        if node_id in visiting:
            raise ValueError("runtime dependency graph contains a cycle")
        visiting.add(node_id)
        record = by_id[node_id]
        dependency_costs = [
            visit(dependency) for dependency in dependency_nodes[node_id]
        ]
        duration_ms = round(
            (
                _timestamp(str(record["ended_at"]))
                - _timestamp(str(record["started_at"]))
            )
            * 1000.0,
            3,
        )
        visiting.remove(node_id)
        memo[node_id] = duration_ms + max(dependency_costs, default=0.0)
        return memo[node_id]

    return max(visit(node_id) for node_id in by_id)


def _timestamp(value: str) -> float:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _field(value: Any, field_name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(field_name)
    return getattr(value, field_name, None)


def _required_field(value: Any, field_name: str) -> Any:
    result = _field(value, field_name)
    if result is None:
        raise ValueError(f"required field is missing: {field_name}")
    return result


def _json_mapping(value: Any, label: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        body = to_dict()
        if isinstance(body, Mapping):
            return {str(key): item for key, item in body.items()}
    if is_dataclass(value):
        body = asdict(value)
        if isinstance(body, Mapping):
            return {str(key): item for key, item in body.items()}
    raise ValueError(f"{label} must be a JSON mapping")


def _required_artifact_ref(
    body: Mapping[str, Any],
    field_name: str,
) -> dict[str, Any]:
    value = body.get(field_name)
    if not isinstance(value, Mapping):
        raise ValueError(f"formal Experiment 5 requires {field_name}")
    return dict(value)


def _optional_artifact_ref(
    body: Mapping[str, Any],
    field_name: str,
) -> dict[str, Any] | None:
    value = body.get(field_name)
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"formal Experiment 5 {field_name} must be a ref")
    return dict(value)


def _read_json_artifact(
    store: ArtifactStore,
    ref_body: Mapping[str, Any],
) -> dict[str, Any]:
    ref = ArtifactRef.from_dict(dict(ref_body))
    if not store.verify(ref):
        raise ValueError("formal Experiment 5 artifact verification failed")
    try:
        body = json.loads(store.read_bytes(ref).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("formal Experiment 5 artifact must be JSON") from exc
    if not isinstance(body, dict):
        raise ValueError("formal Experiment 5 artifact body must be an object")
    return body


def _read_callback_json(store: ArtifactStore, ref: ArtifactRef) -> dict[str, Any]:
    if not store.verify(ref):
        raise ValueError("official callback artifact verification failed")
    try:
        body = json.loads(store.read_bytes(ref).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("official callback artifact must be JSON") from exc
    if not isinstance(body, dict):
        raise ValueError("official callback artifact must be a JSON object")
    return body


def _save_online_callback_object(
    store: ArtifactStore,
    role: str,
    submission_id: str,
    body: Mapping[str, Any],
    created_at: str,
) -> ArtifactRef:
    payload = {
        "schema_version": f"tokenshare.paper_online_{role}.v1",
        "evidence_role": role,
        **dict(body),
    }
    safe_role = _safe_online_artifact_part(role)
    safe_submission = _safe_online_artifact_part(submission_id)
    return store.save_json(
        payload,
        artifact_id=f"paper_online_{safe_role}_{safe_submission}",
        artifact_type="PaperOnlineEvidenceObject",
        artifact_schema_id=f"tokenshare.paper_online_{role}",
        artifact_schema_version="v1",
        source={"kind": "paper_online_provider_callback", "submission_id": submission_id},
        metadata={"evidence_role": role},
        created_at=created_at,
    )


def _persist_formal_strategy_events(
    store: ArtifactStore,
    events: Sequence[Mapping[str, Any]],
) -> tuple[ArtifactRef, ...]:
    refs: list[ArtifactRef] = []
    for index, event in enumerate(events):
        body = dict(event)
        occurred_at = str(body.get("occurred_at") or _utc_now())
        body["occurred_at"] = occurred_at
        event_digest = digest_json(body).removeprefix("sha256:")[:20]
        refs.append(
            store.save_json(
                {
                    "schema_version": "tokenshare.paper_formal_strategy_event.v1",
                    **body,
                },
                artifact_id=f"paper_formal_strategy_event_{index}_{event_digest}",
                artifact_type="PaperFormalStrategyEvent",
                artifact_schema_id="tokenshare.paper_formal_strategy_event",
                artifact_schema_version="v1",
                source={"kind": "paper_formal_strategy"},
                metadata={"event_type": str(body.get("event_type") or "")},
                created_at=occurred_at,
            )
        )
    return tuple(refs)


def _artifact_ref_body(value: Any) -> dict[str, Any]:
    if isinstance(value, TypedEvidenceRef):
        return value.artifact_ref.to_dict()
    if isinstance(value, ArtifactRef):
        return value.to_dict()
    if isinstance(value, Mapping):
        return dict(value)
    raise ValueError("fault injection requires persisted raw and provenance refs")


def _exp3_condition_event_identity(
    condition_ref: Exp3OnlineRootRef | None,
) -> dict[str, Any]:
    if condition_ref is None:
        return {}
    return {
        "condition_id": condition_ref.condition_id,
        "condition_digest": condition_ref.condition_digest,
        "profile_digest": condition_ref.profile_digest,
        "budget_digest": condition_ref.budget_digest,
        "online_plan_digest": condition_ref.plan_digest,
        "case_id": condition_ref.case_id,
    }


def _safe_online_artifact_part(value: str) -> str:
    normalized = "".join(
        character if character.isalnum() or character in {"-", "_"} else "_"
        for character in value
    ).strip("_")
    return normalized or "object"


def _provider_latency_aggregate(
    outcomes: Sequence[Any],
) -> tuple[float | None, str, str | None]:
    """把 zero-call 与缺失真实 latency 分开，避免缺失值进入求和。"""

    latency_sum_ms = 0.0
    observed_latency = False
    explicit_zero_call_count = 0
    missing_reason: str | None = None
    for outcome in outcomes:
        provider_attempt_count = _field(outcome, "provider_attempt_count")
        latency_ms = _field(outcome, "provider_latency_ms")
        if (
            isinstance(provider_attempt_count, int)
            and not isinstance(provider_attempt_count, bool)
            and provider_attempt_count == 0
        ):
            explicit_zero_call_count += 1
            continue
        if (
            isinstance(latency_ms, (int, float))
            and not isinstance(latency_ms, bool)
            and latency_ms >= 0
        ):
            latency_sum_ms += float(latency_ms)
            observed_latency = True
            continue
        outcome_reason = _field(outcome, "provider_latency_unavailable_reason")
        missing_reason = (
            str(outcome_reason)
            if isinstance(outcome_reason, str) and outcome_reason
            else "missing_provider_latency_evidence"
        )
    if missing_reason is not None:
        return None, "incomplete", missing_reason
    if observed_latency:
        return latency_sum_ms, "complete", None
    if outcomes and explicit_zero_call_count == len(outcomes):
        return 0.0, "not_applicable", "no_provider_attempts"
    return None, "incomplete", "missing_provider_latency_evidence"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
