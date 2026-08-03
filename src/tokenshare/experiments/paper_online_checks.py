"""EPD-027 L3 online checks 的冻结计划与 typed evidence producer。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from tokenshare.core.models import ArtifactRef
from tokenshare.experiments.paper_models import digest_json
from tokenshare.experiments.paper_pipeline_profile import (
    load_paper_pipeline_profile,
)
from tokenshare.storage.artifacts import ArtifactStore


CURRENT_PROVIDER_ROLES = (
    "request_body",
    "raw_output_or_provider_failure",
    "provenance",
    "usage_status",
    "latency",
    "pricing",
    "provider_attempt",
    "model_record",
)
EXP3_RECOVERY_CHAIN_ROLES = (
    "fault_or_death_event",
    "new_attempt",
    "provider_dispatch",
    "provider_attempt",
    "raw_or_failure",
    "provenance",
    "usage",
    "model_record",
)
CAPABILITY_REPLACEMENT_ROLES = (
    "initial_source_raw",
    "controlled_rejection",
    "requeue_decision",
    "new_attempt",
    "provider_dispatch",
    "provider_attempt",
    "raw_or_failure",
    "provenance",
    "usage",
    "latency",
    "pricing",
    "model_record",
)


@dataclass(frozen=True, kw_only=True)
class TypedEvidenceRef:
    """对已由正式 runtime 持久化的对象做只读 typed 引用。"""

    role: str
    artifact_ref: ArtifactRef

    def __post_init__(self) -> None:
        _non_empty(self.role, "role")
        if not isinstance(self.artifact_ref, ArtifactRef):
            raise TypeError("artifact_ref must be an ArtifactRef")

    @property
    def object_id(self) -> str:
        return self.artifact_ref.artifact_id

    @property
    def object_digest(self) -> str:
        return self.artifact_ref.content_hash

    @property
    def occurred_at(self) -> str:
        return self.artifact_ref.created_at


@dataclass(frozen=True, kw_only=True)
class EvidenceInput:
    """直接输入；证据不完整时只能是 blocked/null。"""

    value: object | None
    blocked: bool
    reason: str | None
    source_ref_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.blocked) is not bool:
            raise ValueError("blocked must be a bool")
        if self.blocked != (self.value is None):
            raise ValueError("blocked evidence input must be null and only blocked may be null")
        if self.blocked:
            _non_empty(self.reason, "reason")
        elif self.reason is not None:
            raise ValueError("available evidence input cannot carry a blocked reason")
        if any(not isinstance(item, str) or not item for item in self.source_ref_ids):
            raise ValueError("source_ref_ids must contain non-empty strings")


@dataclass(frozen=True, kw_only=True)
class CurrentProviderAttemptEvidence:
    """一笔当次 provider attempt 的 current objects 与原始 resource inputs。"""

    attempt_id: str
    attempt_ordinal: int
    unit_id: str
    provider: str
    model: str
    entry_id: str
    submitted_at: str
    attempt_ref: TypedEvidenceRef | None
    request_body_ref: TypedEvidenceRef | None
    dispatch_ref: TypedEvidenceRef | None
    provider_attempt_ref: TypedEvidenceRef | None
    raw_or_failure_ref: TypedEvidenceRef | None
    provenance_ref: TypedEvidenceRef | None
    usage_ref: TypedEvidenceRef | None
    latency_ref: TypedEvidenceRef | None
    pricing_ref: TypedEvidenceRef | None
    model_record_ref: TypedEvidenceRef | None
    callback_record_ref: TypedEvidenceRef | None

    def __post_init__(self) -> None:
        for name in (
            "attempt_id",
            "unit_id",
            "provider",
            "model",
            "entry_id",
            "submitted_at",
        ):
            _non_empty(getattr(self, name), name)
        _timestamp(self.submitted_at)
        _nonnegative_int(self.attempt_ordinal, "attempt_ordinal")
        for name in (
            "attempt_ref",
            "request_body_ref",
            "dispatch_ref",
            "provider_attempt_ref",
            "raw_or_failure_ref",
            "provenance_ref",
            "usage_ref",
            "latency_ref",
            "pricing_ref",
            "model_record_ref",
            "callback_record_ref",
        ):
            value = getattr(self, name)
            if value is not None and not isinstance(value, TypedEvidenceRef):
                raise TypeError(f"{name} must be a TypedEvidenceRef")
    @property
    def raw_output_ref(self) -> Mapping[str, object] | None:
        return (
            None
            if self.raw_or_failure_ref is None
            else self.raw_or_failure_ref.artifact_ref.to_dict()
        )

    @property
    def current_provider_roles(self) -> tuple[str, ...]:
        refs = (
            (self.request_body_ref, "request_body"),
            (self.raw_or_failure_ref, "raw_or_failure"),
            (self.provenance_ref, "provenance"),
            (self.usage_ref, "usage"),
            (self.latency_ref, "latency"),
            (self.pricing_ref, "pricing"),
            (self.provider_attempt_ref, "provider_attempt"),
            (self.model_record_ref, "model_record"),
        )
        return tuple(
            provider_role
            for provider_role, (ref, evidence_role) in zip(
                CURRENT_PROVIDER_ROLES, refs
            )
            if ref is not None and ref.role == evidence_role
        )

    @property
    def complete_current_provider_roles(self) -> bool:
        return self.current_provider_roles == CURRENT_PROVIDER_ROLES


@dataclass(frozen=True, kw_only=True)
class CurrentProviderResourceInput:
    attempt_id: str
    actual_provider_call: EvidenceInput
    prompt_tokens: EvidenceInput
    completion_tokens: EvidenceInput
    total_tokens: EvidenceInput
    cost_estimate_cny: EvidenceInput


@dataclass(frozen=True, kw_only=True)
class CapabilityCallPlan:
    domain: str
    case_id: str
    planned_ai_unit_id: str
    phase: str
    attempt_ordinal: int
    controlled_rejection: str | None
    preserve_initial_source_raw: bool


@dataclass(frozen=True, kw_only=True)
class Exp2OnlineConditionRef:
    condition_id: str
    condition_digest: str
    profile_digest: str
    budget_digest: str
    plan_digest: str
    worker_count: int
    case_position: str
    case_id: str
    repeat_id: int


@dataclass(frozen=True, kw_only=True)
class Exp3OnlineRootRef:
    condition_id: str
    condition_digest: str
    profile_digest: str
    budget_digest: str
    plan_digest: str
    case_id: str
    check_kind: str
    repeat_id: int
    provider_calls_upper: int


@dataclass(frozen=True, kw_only=True)
class PaperOnlineChecksPlan:
    profile_digest: str
    budget_digest: str
    plan_digest: str
    capability_calls: tuple[CapabilityCallPlan, ...]
    capability_calls_exact: int
    exp2_condition_refs: tuple[Exp2OnlineConditionRef, ...]
    exp2_calls_upper: int
    exp3_root_refs: tuple[Exp3OnlineRootRef, ...]
    exp3_calls_upper: int
    max_concurrent_roots: int
    schema_version: str = "tokenshare.paper_online_checks_plan.v1"


@dataclass(frozen=True, kw_only=True)
class Exp2OnlineDirectEvidence:
    condition_ref: Exp2OnlineConditionRef
    direct_row: Mapping[str, object]
    direct_row_digest: str
    current_provider_attempts: tuple[CurrentProviderAttemptEvidence, ...]
    provider_inputs: tuple[CurrentProviderResourceInput, ...]
    current_provider_roles: tuple[str, ...]
    eligibility_input: EvidenceInput
    table_id: str = "exp2_online_concurrency"
    main_trace_table_eligible: bool = False
    schema_version: str = "tokenshare.exp2_online_direct_evidence.v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "direct_row", MappingProxyType(dict(self.direct_row)))


@dataclass(frozen=True, kw_only=True)
class OrderedEvidenceRole:
    role: str
    ref: TypedEvidenceRef | None
    blocked: bool
    reason: str | None


@dataclass(frozen=True, kw_only=True)
class CapabilityReplacementEvidence:
    domain: str
    initial_source_raw_ref: TypedEvidenceRef | None
    ordered_replacement_evidence: tuple[OrderedEvidenceRole, ...]
    provider_inputs: tuple[CurrentProviderResourceInput, ...]
    eligibility_input: EvidenceInput


@dataclass(frozen=True, kw_only=True)
class CapabilityLifecycleEvidence:
    domain: str
    rejection_kind: str
    initial_attempt_id: str
    initial_raw_ref: TypedEvidenceRef
    rejection_ref: TypedEvidenceRef
    requeue_ref: TypedEvidenceRef
    replacement_attempt_id: str
    replacement_attempt_ordinal: int
    replacement_attempt_ref: TypedEvidenceRef


@dataclass(frozen=True, kw_only=True)
class CapabilityOnlineEvidence:
    profile_digest: str
    budget_digest: str
    plan_digest: str
    replacements: tuple[CapabilityReplacementEvidence, ...]
    schema_version: str = "tokenshare.capability_online_evidence.v1"


@dataclass(frozen=True, kw_only=True)
class DiscardedCurrentInput:
    attempt_id: str
    fault_or_death_ref: TypedEvidenceRef
    discarded_input_ref: TypedEvidenceRef
    raw_or_failure_ref: TypedEvidenceRef | None
    usage_ref: TypedEvidenceRef | None
    total_tokens: EvidenceInput
    cost_estimate_cny: EvidenceInput


@dataclass(frozen=True, kw_only=True)
class Exp3OnlineRecoveryEvidence:
    condition_ref: Exp3OnlineRootRef
    recovery_source: str
    original_attempt_id: str
    original_attempt_ordinal: int
    replacement_attempt_id: str
    replacement_attempt_ordinal: int
    initial_source_raw_ref: TypedEvidenceRef | None
    ordered_replacement_evidence: tuple[OrderedEvidenceRole, ...]
    provider_inputs: tuple[CurrentProviderResourceInput, ...]
    discarded_current_inputs: tuple[DiscardedCurrentInput, ...]
    eligibility_input: EvidenceInput
    schema_version: str = "tokenshare.exp3_online_recovery_evidence.v1"


def freeze_paper_online_checks_plan() -> PaperOnlineChecksPlan:
    """只从已验证的 tracked profile 冻结调用顺序，不执行任何调用。"""

    current = load_paper_pipeline_profile()
    capability = current.capability
    calls = (
        CapabilityCallPlan(
            domain="factorization",
            case_id=capability.factor.case_id,
            planned_ai_unit_id=capability.factor.stable_planned_ai_unit_id,
            phase="controlled_initial_rejection",
            attempt_ordinal=0,
            controlled_rejection="verification_rejected",
            preserve_initial_source_raw=True,
        ),
        CapabilityCallPlan(
            domain="factorization",
            case_id=capability.factor.case_id,
            planned_ai_unit_id=capability.factor.stable_planned_ai_unit_id,
            phase="replacement",
            attempt_ordinal=1,
            controlled_rejection=None,
            preserve_initial_source_raw=True,
        ),
        CapabilityCallPlan(
            domain="lean_proof",
            case_id=capability.lean.case_id,
            planned_ai_unit_id=capability.lean.stable_planned_ai_unit_id,
            phase="controlled_initial_rejection",
            attempt_ordinal=0,
            controlled_rejection="checker_rejected",
            preserve_initial_source_raw=True,
        ),
        CapabilityCallPlan(
            domain="lean_proof",
            case_id=capability.lean.case_id,
            planned_ai_unit_id=capability.lean.stable_planned_ai_unit_id,
            phase="replacement",
            attempt_ordinal=1,
            controlled_rejection=None,
            preserve_initial_source_raw=True,
        ),
    )
    exp2_bodies = tuple(
        {
            "condition_id": value.condition_id,
            "condition_digest": value.condition_digest,
            "worker_count": value.worker_count,
            "case_position": value.case_position,
            "case_id": value.case_id,
            "repeat_id": value.repeat_id,
        }
        for value in current.exp2_conditions
    )
    exp3_bodies = tuple(
        {
            "condition_id": f"epd027_exp3_online_{value.check_kind}_r{value.repeat_id}",
            "condition_digest": digest_json(value.to_dict()),
            "case_id": value.case_id,
            "check_kind": value.check_kind,
            "repeat_id": value.repeat_id,
            "provider_calls_upper": value.provider_calls_upper,
        }
        for value in current.exp3_cases
    )
    plan_digest = digest_json(
        {
            "schema_version": "tokenshare.paper_online_checks_plan.v1",
            "profile_digest": current.profile_digest,
            "budget_digest": current.budget_digest,
            "capability_calls": [call.__dict__ for call in calls],
            "exp2_condition_refs": exp2_bodies,
            "exp3_root_refs": exp3_bodies,
            "max_concurrent_roots": current.max_concurrent_roots,
        }
    )
    exp2_refs = tuple(
        Exp2OnlineConditionRef(
            **body,
            profile_digest=current.profile_digest,
            budget_digest=current.budget_digest,
            plan_digest=plan_digest,
        )
        for body in exp2_bodies
    )
    exp3_refs = tuple(
        Exp3OnlineRootRef(
            **body,
            profile_digest=current.profile_digest,
            budget_digest=current.budget_digest,
            plan_digest=plan_digest,
        )
        for body in exp3_bodies
    )
    if len(calls) != current.capability_calls_exact:
        raise ValueError("capability call plan drift")
    return PaperOnlineChecksPlan(
        profile_digest=current.profile_digest,
        budget_digest=current.budget_digest,
        plan_digest=plan_digest,
        capability_calls=calls,
        capability_calls_exact=current.capability_calls_exact,
        exp2_condition_refs=exp2_refs,
        exp2_calls_upper=current.exp2_calls_upper,
        exp3_root_refs=exp3_refs,
        exp3_calls_upper=current.exp3_calls_upper,
        max_concurrent_roots=current.max_concurrent_roots,
    )


def produce_exp2_online_direct_evidence(
    *,
    artifact_store: ArtifactStore,
    plan: PaperOnlineChecksPlan,
    direct_rows: Sequence[Mapping[str, object]],
    current_provider_attempts_by_condition: Mapping[
        str, Sequence[CurrentProviderAttemptEvidence]
    ],
) -> tuple[Exp2OnlineDirectEvidence, ...]:
    """把 canonical direct/current objects 绑定到 online-only condition refs。"""

    if not isinstance(artifact_store, ArtifactStore):
        raise TypeError("artifact_store must be an ArtifactStore")
    _require_authoritative_plan(plan)
    rows = tuple(direct_rows)
    if len(rows) != len(plan.exp2_condition_refs):
        raise ValueError("Exp2 online direct rows must cover all 24 frozen conditions")
    by_id = {ref.condition_id: ref for ref in plan.exp2_condition_refs}
    if tuple(row.get("condition_id") for row in rows) != tuple(by_id):
        raise ValueError("Exp2 online direct row order or condition coverage drift")
    result: list[Exp2OnlineDirectEvidence] = []
    for row in rows:
        condition_id = str(row.get("condition_id"))
        ref = by_id[condition_id]
        if (
            row.get("condition_digest") != ref.condition_digest
            or row.get("profile_digest") != plan.profile_digest
            or row.get("budget_digest") != plan.budget_digest
            or row.get("plan_digest") != plan.plan_digest
            or row.get("evidence_class") != "online_real_provider"
            or row.get("case_id") != ref.case_id
            or row.get("repeat_id") != ref.repeat_id
        ):
            raise ValueError("Exp2 online direct row identity drift")
        attempts = tuple(current_provider_attempts_by_condition.get(condition_id, ()))
        if any(not isinstance(value, CurrentProviderAttemptEvidence) for value in attempts):
            raise TypeError("current provider attempts must be typed")
        hydrated = tuple(
            _hydrate_current_provider_attempt(artifact_store, value)
            for value in attempts
        )
        complete = (
            bool(attempts)
            and all(value[0] for value in hydrated)
            and all(
                _capture_scope_matches(artifact_store, attempt, ref)
                for attempt in attempts
            )
        )
        source_ids = _attempt_source_ids(attempts)
        result.append(
            Exp2OnlineDirectEvidence(
                condition_ref=ref,
                direct_row=row,
                direct_row_digest=digest_json(dict(row)),
                current_provider_attempts=attempts,
                provider_inputs=tuple(value[1] for value in hydrated),
                current_provider_roles=(CURRENT_PROVIDER_ROLES if complete else ()),
                eligibility_input=_available(True, source_ids)
                if complete
                else _blocked("missing_current_provider_role", source_ids),
            )
        )
    return tuple(result)


def produce_capability_online_evidence(
    *,
    artifact_store: ArtifactStore,
    captures: Sequence[CurrentProviderAttemptEvidence],
    lifecycle_evidence_by_domain: Mapping[str, CapabilityLifecycleEvidence] | None = None,
    artifact_stores_by_domain: Mapping[str, ArtifactStore] | None = None,
) -> CapabilityOnlineEvidence:
    """从四次 official post-raw callback capture 生成 capability evidence。"""

    if not isinstance(artifact_store, ArtifactStore):
        raise TypeError("artifact_store must be an ArtifactStore")
    plan = freeze_paper_online_checks_plan()
    values = tuple(captures)
    if len(values) != plan.capability_calls_exact or any(
        not isinstance(value, CurrentProviderAttemptEvidence) for value in values
    ):
        raise ValueError("capability evidence requires exact four typed captures")
    stores = dict(artifact_stores_by_domain or {})
    lifecycles = dict(lifecycle_evidence_by_domain or {})
    domains = tuple(call.domain for call in plan.capability_calls)
    hydrated = tuple(
        _hydrate_current_provider_attempt(stores.get(domain, artifact_store), value)
        for domain, value in zip(domains, values)
    )
    replacements: list[CapabilityReplacementEvidence] = []
    for pair_index, (initial_index, replacement_index) in enumerate(((0, 1), (2, 3))):
        initial = values[initial_index]
        replacement = values[replacement_index]
        expected_initial = plan.capability_calls[initial_index]
        expected_replacement = plan.capability_calls[replacement_index]
        domain_store = stores.get(expected_initial.domain, artifact_store)
        lifecycle = lifecycles.get(expected_initial.domain)
        reasons: list[str] = []
        if initial.attempt_ordinal != 0 or replacement.attempt_ordinal != 1:
            reasons.append("capability_attempt_ordinal_drift")
        if initial.attempt_id == replacement.attempt_id:
            reasons.append("capability_replacement_attempt_not_distinct")
        if not _capture_capability_call_matches(
            domain_store, initial, plan.plan_digest, initial_index, expected_initial
        ) or not _capture_capability_call_matches(
            domain_store,
            replacement,
            plan.plan_digest,
            replacement_index,
            expected_replacement,
        ):
            reasons.append("capability_callback_identity_mismatch")
        if not hydrated[initial_index][0] or not hydrated[replacement_index][0]:
            reasons.append("capability_current_provider_objects_incomplete")
        if not _capability_lifecycle_matches(
            domain_store,
            lifecycle,
            expected_initial,
            initial,
            replacement,
        ):
            reasons.append("capability_official_lifecycle_incomplete_or_mismatched")
        chain_refs = (
            _retag(initial.raw_or_failure_ref, "initial_source_raw"),
            None if lifecycle is None else lifecycle.rejection_ref,
            None if lifecycle is None else lifecycle.requeue_ref,
            None if lifecycle is None else lifecycle.replacement_attempt_ref,
            replacement.dispatch_ref,
            replacement.provider_attempt_ref,
            replacement.raw_or_failure_ref,
            replacement.provenance_ref,
            replacement.usage_ref,
            replacement.latency_ref,
            replacement.pricing_ref,
            replacement.model_record_ref,
        )
        chain = tuple(
            OrderedEvidenceRole(
                role=role,
                ref=ref,
                blocked=ref is None or ref.role != role,
                reason=(
                    "missing_capability_replacement_role"
                    if ref is None
                    else "capability_replacement_role_mismatch"
                    if ref.role != role
                    else None
                ),
            )
            for role, ref in zip(CAPABILITY_REPLACEMENT_ROLES, chain_refs)
        )
        if any(item.blocked for item in chain):
            reasons.append("capability_replacement_chain_incomplete")
        source_ids = tuple(
            item.ref.object_id for item in chain if item.ref is not None
        )
        replacements.append(
            CapabilityReplacementEvidence(
                domain=expected_initial.domain,
                initial_source_raw_ref=initial.raw_or_failure_ref,
                ordered_replacement_evidence=chain,
                provider_inputs=(
                    hydrated[initial_index][1],
                    hydrated[replacement_index][1],
                ),
                eligibility_input=(
                    _blocked(reasons[0], source_ids)
                    if reasons
                    else _available(True, source_ids)
                ),
            )
        )
    return CapabilityOnlineEvidence(
        profile_digest=plan.profile_digest,
        budget_digest=plan.budget_digest,
        plan_digest=plan.plan_digest,
        replacements=tuple(replacements),
    )


def produce_exp3_online_recovery_evidence(
    *,
    artifact_store: ArtifactStore,
    condition_ref: Exp3OnlineRootRef,
    strategy_result: object,
    original_attempt: CurrentProviderAttemptEvidence,
    replacement_attempt: CurrentProviderAttemptEvidence,
) -> Exp3OnlineRecoveryEvidence:
    """只消费 official callback captures 与已持久化 strategy events。"""

    if not isinstance(artifact_store, ArtifactStore):
        raise TypeError("artifact_store must be an ArtifactStore")
    _require_authoritative_condition_ref(condition_ref)
    for name, value in (
        ("original_attempt", original_attempt),
        ("replacement_attempt", replacement_attempt),
    ):
        if not isinstance(value, CurrentProviderAttemptEvidence):
            raise TypeError(f"{name} must be typed")
    event_refs = tuple(getattr(strategy_result, "persisted_event_refs", ()))
    if any(not isinstance(ref, ArtifactRef) for ref in event_refs):
        raise TypeError("strategy persisted_event_refs must contain ArtifactRef")
    events = tuple(
        (ref, _read_json_ref(artifact_store, ref)) for ref in event_refs
    )
    expected_event_type = (
        "EXPERIMENT_FAULT_INJECTED"
        if condition_ref.check_kind == "false_positive"
        else "EXPERIMENT_WORKER_DEATH_OBSERVED"
    )
    fault_event = _single_strategy_event(events, {expected_event_type})
    worker_plan_event = (
        _single_strategy_event(events, {"EXPERIMENT_WORKER_DEATH_PLAN_FROZEN"})
        if condition_ref.check_kind == "worker_death"
        else None
    )
    new_attempt_event = _single_strategy_event(
        events,
        {"EXPERIMENT_REPLACEMENT_ATTEMPT_STARTED"},
    )
    fault_or_death_ref = (
        None
        if fault_event is None
        else TypedEvidenceRef(role="fault_or_death_event", artifact_ref=fault_event[0])
    )
    chain_refs = (
        fault_or_death_ref,
        _retag(replacement_attempt.attempt_ref, "new_attempt"),
        replacement_attempt.dispatch_ref,
        replacement_attempt.provider_attempt_ref,
        replacement_attempt.raw_or_failure_ref,
        replacement_attempt.provenance_ref,
        replacement_attempt.usage_ref,
        replacement_attempt.model_record_ref,
    )
    chain = tuple(
        OrderedEvidenceRole(
            role=role,
            ref=ref,
            blocked=ref is None or ref.role != role,
            reason=(
                "missing_required_recovery_role"
                if ref is None
                else "recovery_role_type_mismatch"
                if ref.role != role
                else None
            ),
        )
        for role, ref in zip(EXP3_RECOVERY_CHAIN_ROLES, chain_refs)
    )
    reasons: list[str] = []
    if original_attempt.attempt_id == replacement_attempt.attempt_id:
        reasons.append("replacement_attempt_not_distinct")
    if replacement_attempt.attempt_ordinal != original_attempt.attempt_ordinal + 1:
        reasons.append("replacement_attempt_ordinal_not_incremented")
    if new_attempt_event is None or new_attempt_event[1].get(
        "attempt_id"
    ) != replacement_attempt.attempt_id:
        reasons.append("new_attempt_event_identity_mismatch")
    if any(item.blocked for item in chain):
        reasons.append("missing_required_recovery_role")
    complete_refs = tuple(ref for ref in chain_refs if ref is not None)
    if len(complete_refs) == len(chain_refs) and not _ordered_by_time(complete_refs):
        reasons.append("recovery_evidence_time_order_invalid")
    original_complete, original_input = _hydrate_current_provider_attempt(
        artifact_store, original_attempt
    )
    replacement_complete, replacement_input = _hydrate_current_provider_attempt(
        artifact_store, replacement_attempt
    )
    if not original_complete:
        reasons.append("original_current_provider_roles_incomplete")
    if not replacement_complete:
        reasons.append("replacement_current_provider_roles_incomplete")
    if not _capture_scope_matches(
        artifact_store, original_attempt, condition_ref
    ) or not _capture_scope_matches(
        artifact_store, replacement_attempt, condition_ref
    ):
        reasons.append("callback_scope_identity_mismatch")
    if fault_event is not None:
        fault_body = fault_event[1]
        if (
            fault_body.get("attempt_id") != original_attempt.attempt_id
            or fault_body.get("unit_id") != original_attempt.unit_id
        ):
            reasons.append("fault_or_death_event_identity_mismatch")
        if condition_ref.check_kind == "false_positive":
            if (
                fault_body.get("fault_type") != "false_positive"
                or fault_body.get("condition_id") != condition_ref.condition_id
                or fault_body.get("condition_digest") != condition_ref.condition_digest
                or fault_body.get("profile_digest") != condition_ref.profile_digest
                or fault_body.get("budget_digest") != condition_ref.budget_digest
                or fault_body.get("online_plan_digest") != condition_ref.plan_digest
                or fault_body.get("case_id") != condition_ref.case_id
            ):
                reasons.append("false_positive_condition_identity_mismatch")
        elif not _worker_death_event_matches(
            artifact_store,
            condition_ref,
            fault_body,
            None if worker_plan_event is None else worker_plan_event[1],
            original_attempt,
            replacement_attempt,
            new_attempt_event,
        ):
            reasons.append("worker_death_condition_or_kill_plan_mismatch")

    provider_inputs = (original_input, replacement_input)
    discarded_inputs: tuple[DiscardedCurrentInput, ...] = ()
    if fault_or_death_ref is not None:
        discarded_inputs = (
            DiscardedCurrentInput(
                attempt_id=original_attempt.attempt_id,
                fault_or_death_ref=fault_or_death_ref,
                discarded_input_ref=fault_or_death_ref,
                raw_or_failure_ref=original_attempt.raw_or_failure_ref,
                usage_ref=original_attempt.usage_ref,
                total_tokens=provider_inputs[0].total_tokens,
                cost_estimate_cny=provider_inputs[0].cost_estimate_cny,
            ),
        )
    else:
        reasons.append("missing_persisted_fault_or_death_event")
    source_ids = tuple(ref.object_id for ref in complete_refs) + _attempt_source_ids(
        (original_attempt, replacement_attempt)
    )
    eligibility = (
        _blocked(reasons[0], source_ids) if reasons else _available(True, source_ids)
    )
    return Exp3OnlineRecoveryEvidence(
        condition_ref=condition_ref,
        recovery_source=(
            "persisted_fault_event"
            if fault_event is not None
            and fault_event[1].get("event_type") == "EXPERIMENT_FAULT_INJECTED"
            else "persisted_worker_death_record"
            if fault_event is not None
            else "unresolved_persisted_recovery_source"
        ),
        original_attempt_id=original_attempt.attempt_id,
        original_attempt_ordinal=original_attempt.attempt_ordinal,
        replacement_attempt_id=replacement_attempt.attempt_id,
        replacement_attempt_ordinal=replacement_attempt.attempt_ordinal,
        initial_source_raw_ref=original_attempt.raw_or_failure_ref,
        ordered_replacement_evidence=chain,
        provider_inputs=provider_inputs,
        discarded_current_inputs=discarded_inputs,
        eligibility_input=eligibility,
    )


def _hydrate_current_provider_attempt(
    store: ArtifactStore,
    value: CurrentProviderAttemptEvidence,
) -> tuple[bool, CurrentProviderResourceInput]:
    typed_refs = tuple(
        ref
        for ref in (
            value.attempt_ref,
            value.request_body_ref,
            value.dispatch_ref,
            value.provider_attempt_ref,
            value.raw_or_failure_ref,
            value.provenance_ref,
            value.usage_ref,
            value.latency_ref,
            value.pricing_ref,
            value.model_record_ref,
            value.callback_record_ref,
        )
        if ref is not None
    )
    bodies: dict[str, Mapping[str, object]] = {}
    verified = True
    for ref in typed_refs:
        try:
            body = _read_typed_payload(store, ref)
        except ValueError:
            verified = False
            continue
        bodies[ref.role] = body
        if body.get("submission_id") != value.attempt_id:
            verified = False
    required_roles = {
        "attempt",
        "request_body",
        "provider_dispatch",
        "provider_attempt",
        "raw_or_failure",
        "provenance",
        "usage",
        "latency",
        "pricing",
        "model_record",
        "callback_record",
    }
    complete = verified and required_roles == set(bodies)
    unique_refs = len({ref.artifact_ref.artifact_id for ref in typed_refs}) == len(
        typed_refs
    )
    complete = complete and unique_refs
    callback = bodies.get("callback_record", {})
    if complete:
        expected = {
            role: bodies[role].get("schema_version")
            for role in required_roles - {"callback_record"}
        }
        linked = callback.get("object_refs")
        if not isinstance(linked, Mapping):
            complete = False
        else:
            for ref in typed_refs:
                if ref.role == "callback_record":
                    continue
                linked_ref = linked.get(ref.role)
                if not isinstance(linked_ref, Mapping) or linked_ref.get(
                    "content_hash"
                ) != ref.artifact_ref.content_hash:
                    complete = False
            complete = complete and all(expected.values())
    usage_body = bodies.get("usage", {})
    usage_summary = usage_body.get("usage_summary")
    usage = usage_summary if isinstance(usage_summary, Mapping) else {}
    provider_body = bodies.get("provider_attempt", {})
    actual_call = provider_body.get("actual_call")
    provider_refs = tuple(
        ref
        for ref in (
            value.request_body_ref,
            value.dispatch_ref,
            value.provider_attempt_ref,
            value.raw_or_failure_ref,
            value.provenance_ref,
            value.model_record_ref,
        )
        if ref is not None
    )
    usage_refs = tuple(ref for ref in (value.usage_ref,) if ref is not None)
    pricing_refs = tuple(ref for ref in (value.pricing_ref,) if ref is not None)
    provider_ready = complete and actual_call is True
    usage_ready = provider_ready and usage.get("cost_estimate_status") in {
        "estimated",
        "usage_missing",
        "usage_invalid",
    }
    pricing_ready = usage_ready and bodies.get("pricing", {}).get(
        "pricing_snapshot"
    ) == usage.get("pricing_snapshot")
    if complete:
        resource = CurrentProviderResourceInput(
            attempt_id=value.attempt_id,
            actual_provider_call=(
                _available(1, _ref_ids(provider_refs))
                if provider_ready
                else _blocked(
                    "missing_current_provider_call_object", _ref_ids(provider_refs)
                )
            ),
            prompt_tokens=_number_input(
                usage.get("prompt_tokens"),
                usage_ready,
                "missing_current_usage_object",
                usage_refs,
            ),
            completion_tokens=_number_input(
                usage.get("completion_tokens"),
                usage_ready,
                "missing_current_usage_object",
                usage_refs,
            ),
            total_tokens=_number_input(
                usage.get("total_tokens"),
                usage_ready,
                "missing_current_usage_object",
                usage_refs,
            ),
            cost_estimate_cny=_number_input(
                usage.get("cost_estimate"),
                pricing_ready,
                "missing_current_usage_or_pricing_object",
                usage_refs + pricing_refs,
            ),
        )
    else:
        resource = CurrentProviderResourceInput(
            attempt_id=value.attempt_id,
            actual_provider_call=_blocked(
                "invalid_or_incomplete_current_provider_objects",
                _ref_ids(provider_refs),
            ),
            prompt_tokens=_blocked(
                "invalid_or_incomplete_current_provider_objects",
                _ref_ids(usage_refs),
            ),
            completion_tokens=_blocked(
                "invalid_or_incomplete_current_provider_objects",
                _ref_ids(usage_refs),
            ),
            total_tokens=_blocked(
                "invalid_or_incomplete_current_provider_objects",
                _ref_ids(usage_refs),
            ),
            cost_estimate_cny=_blocked(
                "invalid_or_incomplete_current_provider_objects",
                _ref_ids(usage_refs + pricing_refs),
            ),
        )
    resource_complete = all(
        not item.blocked
        for item in (
            resource.actual_provider_call,
            resource.prompt_tokens,
            resource.completion_tokens,
            resource.total_tokens,
            resource.cost_estimate_cny,
        )
    )
    return complete and resource_complete, resource


def _number_input(
    value: object | None,
    evidence_ready: bool,
    reason: str,
    refs: Sequence[TypedEvidenceRef],
) -> EvidenceInput:
    if evidence_ready and value is not None:
        return _available(value, _ref_ids(refs))
    return _blocked(reason if not evidence_ready else "missing_current_numeric_input", _ref_ids(refs))


def _require_authoritative_plan(plan: PaperOnlineChecksPlan) -> None:
    if not isinstance(plan, PaperOnlineChecksPlan) or plan != freeze_paper_online_checks_plan():
        raise ValueError("authoritative online-check plan mismatch")


def _require_authoritative_condition_ref(ref: Exp3OnlineRootRef) -> None:
    if not isinstance(ref, Exp3OnlineRootRef) or ref not in freeze_paper_online_checks_plan().exp3_root_refs:
        raise ValueError("authoritative online-check condition ref mismatch")


def _read_json_ref(store: ArtifactStore, ref: ArtifactRef) -> Mapping[str, object]:
    if not store.verify(ref):
        raise ValueError("persisted online evidence object verification failed")
    try:
        body = json.loads(store.read_bytes(ref).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("persisted online evidence object must be JSON") from exc
    if not isinstance(body, Mapping):
        raise ValueError("persisted online evidence object must be a mapping")
    return dict(body)


def _read_typed_payload(
    store: ArtifactStore, ref: TypedEvidenceRef
) -> Mapping[str, object]:
    body = _read_json_ref(store, ref.artifact_ref)
    if body.get("evidence_role") not in {None, ref.role}:
        raise ValueError("persisted online evidence role mismatch")
    return body


def _single_strategy_event(
    events: Sequence[tuple[ArtifactRef, Mapping[str, object]]],
    event_types: set[str],
) -> tuple[ArtifactRef, Mapping[str, object]] | None:
    matches = tuple(item for item in events if item[1].get("event_type") in event_types)
    return matches[0] if len(matches) == 1 else None


def _worker_death_event_matches(
    store: ArtifactStore,
    condition_ref: Exp3OnlineRootRef,
    event: Mapping[str, object],
    plan_event: Mapping[str, object] | None,
    original: CurrentProviderAttemptEvidence,
    replacement: CurrentProviderAttemptEvidence,
    new_attempt_event: tuple[ArtifactRef, Mapping[str, object]] | None,
) -> bool:
    record_ref_body = event.get("record_ref")
    if not isinstance(record_ref_body, Mapping) or not isinstance(plan_event, Mapping):
        return False
    try:
        record_ref = ArtifactRef.from_dict(dict(record_ref_body))
        record = _read_json_ref(store, record_ref)
    except (TypeError, ValueError):
        return False
    plan_body = plan_event.get("plan")
    replacement_snapshot = record.get("replacement_attempt")
    dead_snapshot = record.get("dead_attempt")
    return bool(
        event.get("condition_id") == condition_ref.condition_id
        and event.get("task_id") == condition_ref.case_id
        and plan_event.get("condition_id") == condition_ref.condition_id
        and plan_event.get("task_id") == condition_ref.case_id
        and isinstance(plan_body, Mapping)
        and plan_event.get("plan_digest") == digest_json(dict(plan_body))
        and plan_body.get("condition_id") == condition_ref.condition_id
        and plan_body.get("repeat_id") == condition_ref.repeat_id
        and plan_body.get("task_id") == condition_ref.case_id
        and record.get("condition_id") == condition_ref.condition_id
        and record.get("repeat_id") == condition_ref.repeat_id
        and record.get("task_id") == condition_ref.case_id
        and record.get("kill_point") == plan_body.get("kill_point")
        and record.get("dependency_graph") == plan_body.get("dependency_graph")
        and isinstance(dead_snapshot, Mapping)
        and dead_snapshot.get("attempt_id") == original.attempt_id
        and isinstance(replacement_snapshot, Mapping)
        and replacement_snapshot.get("attempt_id") == replacement.attempt_id
        and new_attempt_event is not None
        and new_attempt_event[1].get("attempt_ordinal")
        == replacement.attempt_ordinal
    )


def _capture_scope_matches(
    store: ArtifactStore,
    capture: CurrentProviderAttemptEvidence,
    condition_ref: Exp2OnlineConditionRef | Exp3OnlineRootRef,
) -> bool:
    if capture.callback_record_ref is None:
        return False
    try:
        body = _read_typed_payload(store, capture.callback_record_ref)
    except ValueError:
        return False
    scope = body.get("scope_identity")
    expected_scope = (
        "exp2_online"
        if isinstance(condition_ref, Exp2OnlineConditionRef)
        else "exp3_online"
    )
    return bool(
        isinstance(scope, Mapping)
        and scope.get("scope_kind") == expected_scope
        and scope.get("condition_id") == condition_ref.condition_id
        and scope.get("condition_digest") == condition_ref.condition_digest
        and scope.get("profile_digest") == condition_ref.profile_digest
        and scope.get("budget_digest") == condition_ref.budget_digest
        and scope.get("plan_digest") == condition_ref.plan_digest
    )


def _capture_capability_call_matches(
    store: ArtifactStore,
    capture: CurrentProviderAttemptEvidence,
    plan_digest: str,
    call_index: int,
    expected: CapabilityCallPlan,
) -> bool:
    if capture.callback_record_ref is None:
        return False
    try:
        body = _read_typed_payload(store, capture.callback_record_ref)
    except ValueError:
        return False
    scope = body.get("scope_identity")
    return bool(
        isinstance(scope, Mapping)
        and scope.get("scope_kind") == "capability"
        and scope.get("plan_digest") == plan_digest
        and scope.get("profile_digest")
        == freeze_paper_online_checks_plan().profile_digest
        and scope.get("budget_digest")
        == freeze_paper_online_checks_plan().budget_digest
        and scope.get("call_index") == call_index
        and scope.get("domain") == expected.domain
        and scope.get("phase") == expected.phase
        and scope.get("case_id") == expected.case_id
        and scope.get("planned_ai_unit_id") == expected.planned_ai_unit_id
        and scope.get("controlled_rejection") == expected.controlled_rejection
    )


def _capability_lifecycle_matches(
    store: ArtifactStore,
    lifecycle: CapabilityLifecycleEvidence | None,
    expected: CapabilityCallPlan,
    initial: CurrentProviderAttemptEvidence,
    replacement: CurrentProviderAttemptEvidence,
) -> bool:
    expected_rejection = {
        "factorization": "verification_rejected",
        "lean_proof": "checker_rejected",
    }[expected.domain]
    if (
        not isinstance(lifecycle, CapabilityLifecycleEvidence)
        or lifecycle.domain != expected.domain
        or lifecycle.rejection_kind != expected_rejection
        or lifecycle.initial_attempt_id != initial.attempt_id
        or lifecycle.initial_raw_ref != _retag(initial.raw_or_failure_ref, "initial_source_raw")
        or lifecycle.replacement_attempt_id != replacement.attempt_id
        or lifecycle.replacement_attempt_ordinal != 1
        or lifecycle.replacement_attempt_ref
        != _retag(replacement.attempt_ref, "new_attempt")
    ):
        return False
    try:
        rejection = _read_typed_payload(store, lifecycle.rejection_ref)
        requeue = _read_typed_payload(store, lifecycle.requeue_ref)
        domain_rejection_ref = ArtifactRef.from_dict(rejection["domain_rejection_ref"])
        domain_rejection = _read_json_ref(store, domain_rejection_ref)
        replacement_attempt_body = _read_json_ref(
            store, lifecycle.replacement_attempt_ref.artifact_ref
        )
    except (KeyError, TypeError, ValueError):
        return False
    return bool(
        lifecycle.rejection_ref.role == "controlled_rejection"
        and lifecycle.requeue_ref.role == "requeue_decision"
        and lifecycle.replacement_attempt_ref.role == "new_attempt"
        and rejection.get("domain") == expected.domain
        and rejection.get("rejection_kind") == expected_rejection
        and rejection.get("attempt_id") == initial.attempt_id
        and isinstance(rejection.get("raw_output_ref"), Mapping)
        and rejection["raw_output_ref"].get("content_hash")
        == initial.raw_or_failure_ref.artifact_ref.content_hash
        and isinstance(rejection.get("candidate_output_refs"), Mapping)
        and bool(rejection["candidate_output_refs"])
        and _domain_rejection_matches(
            expected=expected,
            initial=initial,
            rejection=rejection,
            domain_rejection_ref=domain_rejection_ref,
            domain_rejection=domain_rejection,
        )
        and requeue.get("domain") == expected.domain
        and requeue.get("initial_attempt_id") == initial.attempt_id
        and requeue.get("expected_replacement_ordinal") == 1
        and isinstance(requeue.get("rejection_ref"), Mapping)
        and requeue["rejection_ref"].get("content_hash")
        == lifecycle.rejection_ref.artifact_ref.content_hash
        and replacement_attempt_body.get("submission_id") == replacement.attempt_id
        and replacement_attempt_body.get("attempt_ordinal") == 1
    )


def _domain_rejection_matches(
    *,
    expected: CapabilityCallPlan,
    initial: CurrentProviderAttemptEvidence,
    rejection: Mapping[str, object],
    domain_rejection_ref: ArtifactRef,
    domain_rejection: Mapping[str, object],
) -> bool:
    attempt_snapshot = rejection.get("attempt_snapshot")
    if (
        not isinstance(attempt_snapshot, Mapping)
        or attempt_snapshot.get("state") != "Rejected"
        or attempt_snapshot.get("attempt_ordinal") != 0
        or attempt_snapshot.get("failure_reason") != "plugin domain check rejected"
    ):
        return False
    if expected.domain == "factorization":
        return bool(
            domain_rejection_ref.artifact_type == "FactorVerifierRejection"
            and domain_rejection.get("schema_version")
            == "tokenshare.factor_verifier_rejection.v1"
            and domain_rejection.get("domain") == "factorization"
            and domain_rejection.get("rejection_kind") == "verification_rejected"
            and domain_rejection.get("submission_id") == initial.attempt_id
            and domain_rejection.get("protocol_attempt_id")
            == rejection.get("protocol_attempt_id")
            and domain_rejection.get("raw_output_ref") == rejection.get("raw_output_ref")
            and domain_rejection.get("candidate_output_refs")
            == rejection.get("candidate_output_refs")
        )
    return bool(
        domain_rejection_ref.artifact_type == "LeanCheckerReport"
        and domain_rejection.get("schema_version") == "lean_proof.checker_report.v1"
        and domain_rejection.get("status") == "rejected"
        and domain_rejection.get("request_id") == rejection.get("checker_request_id")
        and isinstance(domain_rejection.get("generated_source_ref"), Mapping)
        and domain_rejection.get("proof_artifact_ref") is None
    )


def _retag(ref: TypedEvidenceRef | None, role: str) -> TypedEvidenceRef | None:
    return None if ref is None else TypedEvidenceRef(role=role, artifact_ref=ref.artifact_ref)


def _available(value: object, source_ref_ids: Sequence[str]) -> EvidenceInput:
    return EvidenceInput(
        value=value,
        blocked=False,
        reason=None,
        source_ref_ids=tuple(source_ref_ids),
    )


def _blocked(reason: str, source_ref_ids: Sequence[str]) -> EvidenceInput:
    return EvidenceInput(
        value=None,
        blocked=True,
        reason=reason,
        source_ref_ids=tuple(source_ref_ids),
    )


def _attempt_source_ids(
    attempts: Sequence[CurrentProviderAttemptEvidence],
) -> tuple[str, ...]:
    refs: list[TypedEvidenceRef] = []
    for value in attempts:
        refs.extend(
            ref
            for ref in (
                value.attempt_ref,
                value.request_body_ref,
                value.dispatch_ref,
                value.provider_attempt_ref,
                value.raw_or_failure_ref,
                value.provenance_ref,
                value.usage_ref,
                value.latency_ref,
                value.pricing_ref,
                value.model_record_ref,
                value.callback_record_ref,
            )
            if ref is not None
        )
    return _ref_ids(refs)


def _ref_ids(refs: Sequence[TypedEvidenceRef]) -> tuple[str, ...]:
    return tuple(ref.object_id for ref in refs)


def _ordered_by_time(refs: Sequence[TypedEvidenceRef]) -> bool:
    values = tuple(_timestamp(ref.occurred_at) for ref in refs)
    return all(left <= right for left, right in zip(values, values[1:]))


def _timestamp(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("occurred_at must be an ISO-8601 timestamp") from exc


def _non_empty(value: object, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be non-empty")


def _digest(value: object, name: str) -> None:
    if (
        not isinstance(value, str)
        or not value.startswith("sha256:")
        or len(value) != 71
    ):
        raise ValueError(f"{name} must be a sha256 digest")
    try:
        int(value[7:], 16)
    except ValueError as exc:
        raise ValueError(f"{name} must be a sha256 digest") from exc


def _nonnegative_int(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")


def _nonnegative_decimal(value: object, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a decimal") from exc
    if not result.is_finite() or result < 0:
        raise ValueError(f"{name} must be nonnegative")
    return result


__all__ = [
    "CAPABILITY_REPLACEMENT_ROLES",
    "CURRENT_PROVIDER_ROLES",
    "EXP3_RECOVERY_CHAIN_ROLES",
    "CapabilityCallPlan",
    "CapabilityLifecycleEvidence",
    "CapabilityOnlineEvidence",
    "CapabilityReplacementEvidence",
    "CurrentProviderAttemptEvidence",
    "CurrentProviderResourceInput",
    "DiscardedCurrentInput",
    "EvidenceInput",
    "Exp2OnlineConditionRef",
    "Exp2OnlineDirectEvidence",
    "Exp3OnlineRecoveryEvidence",
    "Exp3OnlineRootRef",
    "OrderedEvidenceRole",
    "PaperOnlineChecksPlan",
    "TypedEvidenceRef",
    "freeze_paper_online_checks_plan",
    "produce_capability_online_evidence",
    "produce_exp2_online_direct_evidence",
    "produce_exp3_online_recovery_evidence",
]
