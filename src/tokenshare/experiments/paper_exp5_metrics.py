"""Experiment 5 persisted facts 的纯 quality/resources projector。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Mapping, Sequence

from tokenshare.experiments.paper_metric_contract import (
    MemberDecision,
    MetricEvaluation,
    MetricObservationBundle,
    PaperMetricContract,
    VerifiedMetricObservation,
    recompute_metric,
)
from tokenshare.experiments.paper_model_identity import PaperModelEndpointIdentity
from tokenshare.experiments.paper_models import digest_json


QUALITY_TABLE_ID = "exp5_quality"
RESOURCE_TABLE_ID = "exp5_resources"
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
ENDPOINT_SERVING_CONFOUNDING_CAPTION = (
    "endpoint_and_serving_profile_are_confounding_factors"
)
EXP5_QUALITY_FIELDS = (
    "preregistered_root_count",
    "final_result_root_count",
    "verified_correct_root_count",
    "completion_rate",
    "end_to_end_verified_success_rate",
    "actual_first_provider_attempt_count",
    "first_attempt_without_verifier_accepted_candidate_count",
    "first_attempt_nonpass_rate",
    "first_attempt_provider_transport_failure_count",
    "first_attempt_parse_schema_unusable_count",
    "first_attempt_verification_checker_rejection_count",
    "first_attempt_checkable_candidate_count",
    "first_attempt_explicitly_rejected_by_verifier_count",
    "first_attempt_verification_rejection_rate",
)
EXP5_RESOURCE_FIELDS = (
    "planned_first_attempt_ai_unit_count",
    "actual_first_provider_attempt_count",
    "first_attempt_call_coverage",
    "actual_total_tokens",
    "actual_cost_estimate_cny",
    "repeat0_wall_clock_ms",
    "repeat1_wall_clock_ms",
    "repeat2_wall_clock_ms",
    "model_wall_clock_median_ms",
    "model_wall_clock_min_ms",
    "model_wall_clock_max_ms",
    "model_wall_clock_range_ms",
)


@dataclass(frozen=True, kw_only=True)
class Exp5PreregisteredRootFacts:
    preregistered_root_run_id: str
    root_dispatched_at_ms: int | Decimal | None
    root_terminal_at_ms: int | Decimal | None
    final_result_reference_complete: bool
    end_to_end_verified_success: bool

    def __post_init__(self) -> None:
        _non_empty(self.preregistered_root_run_id, "preregistered_root_run_id")
        _optional_nonnegative_number(self.root_dispatched_at_ms, "root_dispatched_at_ms")
        _optional_nonnegative_number(self.root_terminal_at_ms, "root_terminal_at_ms")
        _bool(self.final_result_reference_complete, "final_result_reference_complete")
        _bool(self.end_to_end_verified_success, "end_to_end_verified_success")
        if self.end_to_end_verified_success and not self.final_result_reference_complete:
            raise ValueError("verified success requires a complete final result reference")


@dataclass(frozen=True, kw_only=True)
class Exp5PlannedAIUnitFacts:
    unit_id: str
    planned_call: bool

    def __post_init__(self) -> None:
        _non_empty(self.unit_id, "unit_id")
        _bool(self.planned_call, "planned_call")


@dataclass(frozen=True, kw_only=True)
class Exp5FirstProviderAttemptFacts:
    """每个 AI unit 至多一条 persisted first provider attempt fact。"""

    attempt_id: str
    planned_ai_unit_id: str
    actual_call: bool
    provider_transport_failure: bool
    parse_schema_unusable: bool
    verification_checker_rejected: bool
    verifier_accepted_candidate: bool
    actual_total_tokens: int | Decimal | None
    actual_cost_estimate_cny: int | Decimal | None
    current_provider_roles: tuple[str, ...] | None

    def __post_init__(self) -> None:
        _non_empty(self.attempt_id, "attempt_id")
        _non_empty(self.planned_ai_unit_id, "planned_ai_unit_id")
        for field in (
            "actual_call",
            "provider_transport_failure",
            "parse_schema_unusable",
            "verification_checker_rejected",
            "verifier_accepted_candidate",
        ):
            _bool(getattr(self, field), field)
        _optional_nonnegative_number(self.actual_total_tokens, "actual_total_tokens")
        _optional_nonnegative_number(
            self.actual_cost_estimate_cny, "actual_cost_estimate_cny"
        )
        if self.current_provider_roles is not None:
            roles = tuple(self.current_provider_roles)
            if any(not isinstance(role, str) or not role for role in roles):
                raise ValueError("current_provider_roles must contain non-empty strings")
            object.__setattr__(self, "current_provider_roles", roles)
        failures = (
            self.provider_transport_failure,
            self.parse_schema_unusable,
            self.verification_checker_rejected,
        )
        if self.actual_call and self.verifier_accepted_candidate and any(failures):
            raise ValueError("accepted first attempt cannot also be a nonpass")
        if self.actual_call and not self.verifier_accepted_candidate and not any(failures):
            raise ValueError("first-attempt nonpass requires one persisted reason signal")


@dataclass(frozen=True, kw_only=True)
class Exp5ModelRepeatFacts:
    """一个 model×repeat 已 hydration 的 root/attempt/resource facts。"""

    model_arm_id: str
    repeat_id: int
    frozen_identity: PaperModelEndpointIdentity | None
    observed_identity: PaperModelEndpointIdentity | None
    persisted_model_endpoint_identity_digest: str | None
    protocol_first_dispatch_at_ms: int | Decimal | None
    preregistered_roots: tuple[Exp5PreregisteredRootFacts, ...]
    planned_ai_units: tuple[Exp5PlannedAIUnitFacts, ...]
    first_provider_attempts: tuple[Exp5FirstProviderAttemptFacts, ...]
    max_retries: int
    replacement_attempts_allowed: bool
    infrastructure_valid: bool = True

    def __post_init__(self) -> None:
        _non_empty(self.model_arm_id, "model_arm_id")
        if isinstance(self.repeat_id, bool) or self.repeat_id not in (0, 1, 2):
            raise ValueError("repeat_id must be one of 0, 1, 2")
        if self.frozen_identity is not None and not isinstance(
            self.frozen_identity, PaperModelEndpointIdentity
        ):
            raise TypeError("frozen_identity must be PaperModelEndpointIdentity or None")
        if self.observed_identity is not None and not isinstance(
            self.observed_identity, PaperModelEndpointIdentity
        ):
            raise TypeError("observed_identity must be PaperModelEndpointIdentity or None")
        if self.persisted_model_endpoint_identity_digest is not None:
            _non_empty(
                self.persisted_model_endpoint_identity_digest,
                "persisted_model_endpoint_identity_digest",
            )
        _optional_nonnegative_number(
            self.protocol_first_dispatch_at_ms, "protocol_first_dispatch_at_ms"
        )
        _typed_tuple(self, "preregistered_roots", Exp5PreregisteredRootFacts)
        _typed_tuple(self, "planned_ai_units", Exp5PlannedAIUnitFacts)
        _typed_tuple(
            self, "first_provider_attempts", Exp5FirstProviderAttemptFacts
        )
        if isinstance(self.max_retries, bool) or not isinstance(self.max_retries, int):
            raise ValueError("max_retries must be an integer")
        _bool(self.replacement_attempts_allowed, "replacement_attempts_allowed")
        _bool(self.infrastructure_valid, "infrastructure_valid")


@dataclass(frozen=True, kw_only=True)
class Exp5ObservationCell:
    metric_id: str
    value: Decimal | int | None
    reason: str | None
    publish_blocked: bool
    membership: tuple[MemberDecision, ...]
    included_member_ids: tuple[str, ...]
    excluded_member_ids: tuple[str, ...]
    blocked_member_ids: tuple[str, ...]
    audit_denominator_member_ids: tuple[str, ...]
    paper_eligible: bool = False

    @classmethod
    def from_evaluation(
        cls, metric_id: str, evaluation: MetricEvaluation
    ) -> "Exp5ObservationCell":
        return cls(
            metric_id=metric_id,
            value=evaluation.value,
            reason=evaluation.reason,
            publish_blocked=evaluation.publish_blocked,
            membership=evaluation.member_decisions,
            included_member_ids=evaluation.included_member_ids,
            excluded_member_ids=evaluation.excluded_member_ids,
            blocked_member_ids=evaluation.blocked_member_ids,
            audit_denominator_member_ids=evaluation.audit_denominator_member_ids,
        )


@dataclass(frozen=True, kw_only=True)
class Exp5ObservationRow:
    model_arm_ids: tuple[str, ...]
    model_endpoint_identity: PaperModelEndpointIdentity | None
    cells: tuple[Exp5ObservationCell, ...]
    ineligibility_reasons: tuple[str, ...]
    paper_eligible: bool = False

    @property
    def metric_ids(self) -> tuple[str, ...]:
        return tuple(cell.metric_id for cell in self.cells)

    def require_cell(self, metric_id: str) -> Exp5ObservationCell:
        matches = tuple(cell for cell in self.cells if cell.metric_id == metric_id)
        if len(matches) != 1:
            raise ValueError(f"unknown Exp5 observation cell: {metric_id}")
        return matches[0]


@dataclass(frozen=True, kw_only=True)
class Exp5SummaryTablePayload:
    table_id: str
    numeric_output_fields: tuple[str, ...]
    rows: tuple[Exp5ObservationRow, ...]
    caption_metadata: Mapping[str, object]
    paper_eligible: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "caption_metadata", MappingProxyType(dict(self.caption_metadata))
        )


@dataclass(frozen=True, kw_only=True)
class Exp5Projection:
    table_payloads: tuple[Exp5SummaryTablePayload, Exp5SummaryTablePayload]
    paper_eligible: bool = False


def build_exp5_observations(
    repeat_facts: Sequence[Exp5ModelRepeatFacts],
    contract: PaperMetricContract,
) -> Exp5Projection:
    """按冻结 endpoint identity 汇总并生成恰好两个 ordered draft payload。"""

    _validate_contract(contract)
    values = tuple(repeat_facts)
    if any(not isinstance(value, Exp5ModelRepeatFacts) for value in values):
        raise TypeError("repeat_facts must contain Exp5ModelRepeatFacts")

    groups: dict[tuple[object, ...], list[Exp5ModelRepeatFacts]] = {}
    for value in values:
        key: tuple[object, ...]
        if value.frozen_identity is None:
            key = ("missing_identity", value.model_arm_id)
        else:
            key = (
                "canonical_identity",
                value.frozen_identity.model_endpoint_identity_digest,
                value.frozen_identity.source_provider_config_digest,
            )
        groups.setdefault(key, []).append(value)

    quality_rows: list[Exp5ObservationRow] = []
    resource_rows: list[Exp5ObservationRow] = []
    identities: list[PaperModelEndpointIdentity] = []
    for key in sorted(groups, key=lambda item: tuple(str(part) for part in item)):
        group = tuple(sorted(groups[key], key=lambda item: item.repeat_id))
        identity = group[0].frozen_identity
        if identity is not None:
            identities.append(identity)
        reasons = _ineligibility_reasons(group)
        bundle = _build_bundle(group, reasons)
        quality_cells = _evaluate_cells(
            contract, QUALITY_TABLE_ID, EXP5_QUALITY_FIELDS, bundle
        )
        resource_cells = _evaluate_cells(
            contract, RESOURCE_TABLE_ID, EXP5_RESOURCE_FIELDS, bundle
        )
        invalid_timeline_repeats = _invalid_timeline_repeat_ids(group)
        if invalid_timeline_repeats:
            resource_cells = _block_invalid_timeline_cells(
                resource_cells, invalid_timeline_repeats
            )
        if reasons:
            quality_cells = _block_cells(quality_cells, reasons[0])
            resource_cells = _block_cells(resource_cells, reasons[0])
        common = {
            "model_arm_ids": tuple(sorted({value.model_arm_id for value in group})),
            "model_endpoint_identity": identity,
            "ineligibility_reasons": reasons,
        }
        quality_rows.append(Exp5ObservationRow(cells=quality_cells, **common))
        resource_rows.append(Exp5ObservationRow(cells=resource_cells, **common))

    metadata = {
        "endpoint_serving_confounding_caption": (
            contract.controls.exp5_endpoint_serving_confounding_caption
        ),
        "model_endpoint_identities": tuple(identity.to_dict() for identity in identities),
    }
    payloads = (
        Exp5SummaryTablePayload(
            table_id=QUALITY_TABLE_ID,
            numeric_output_fields=EXP5_QUALITY_FIELDS,
            rows=tuple(quality_rows),
            caption_metadata=metadata,
        ),
        Exp5SummaryTablePayload(
            table_id=RESOURCE_TABLE_ID,
            numeric_output_fields=EXP5_RESOURCE_FIELDS,
            rows=tuple(resource_rows),
            caption_metadata=metadata,
        ),
    )
    return Exp5Projection(table_payloads=payloads)


def _ineligibility_reasons(
    group: tuple[Exp5ModelRepeatFacts, ...],
) -> tuple[str, ...]:
    reasons: list[str] = []
    repeat_ids = tuple(value.repeat_id for value in group)
    if sorted(repeat_ids) != [0, 1, 2]:
        reasons.append("missing_or_duplicate_exp5_repeat")
    if any(value.frozen_identity is None for value in group):
        reasons.append("model_endpoint_identity_missing")
    elif any(value.observed_identity is None for value in group):
        reasons.append("model_endpoint_identity_missing")
    elif any(value.observed_identity != value.frozen_identity for value in group):
        reasons.append("model_endpoint_identity_mismatch")
    elif any(
        value.persisted_model_endpoint_identity_digest is None for value in group
    ):
        reasons.append("model_endpoint_identity_digest_missing")
    elif any(
        value.persisted_model_endpoint_identity_digest
        != value.observed_identity.model_endpoint_identity_digest
        for value in group
        if value.observed_identity is not None
    ):
        reasons.append("model_endpoint_identity_digest_mismatch")
    if any(not value.infrastructure_valid for value in group):
        reasons.append("infrastructure_invalid")
    if any(value.max_retries != 0 for value in group):
        reasons.append("exp5_retry_forbidden")
    if any(value.replacement_attempts_allowed for value in group):
        reasons.append("replacement_attempts_forbidden")

    root_ids = tuple(
        root.preregistered_root_run_id
        for value in group
        for root in value.preregistered_roots
    )
    attempt_ids = tuple(
        attempt.attempt_id for value in group for attempt in value.first_provider_attempts
    )
    if len(set(root_ids)) != len(root_ids):
        reasons.append("duplicate_preregistered_root_run_id")
    if len(set(attempt_ids)) != len(attempt_ids):
        reasons.append("duplicate_first_provider_attempt_id")
    for value in group:
        all_unit_ids = tuple(unit.unit_id for unit in value.planned_ai_units)
        planned_ids = tuple(
            unit.unit_id for unit in value.planned_ai_units if unit.planned_call
        )
        if len(set(all_unit_ids)) != len(all_unit_ids):
            reasons.append("duplicate_planned_ai_unit_id")
        actual_unit_ids = tuple(
            attempt.planned_ai_unit_id
            for attempt in value.first_provider_attempts
            if attempt.actual_call
        )
        if len(set(actual_unit_ids)) != len(actual_unit_ids):
            reasons.append("multiple_provider_attempts_for_ai_unit")
        if not set(actual_unit_ids) <= set(planned_ids):
            reasons.append("provider_attempt_without_planned_ai_unit")
    return tuple(dict.fromkeys(reasons))


def _invalid_timeline_repeat_ids(
    group: tuple[Exp5ModelRepeatFacts, ...],
) -> frozenset[int]:
    invalid: set[int] = set()
    for value in group:
        first_dispatch = value.protocol_first_dispatch_at_ms
        if first_dispatch is None or not value.preregistered_roots:
            invalid.add(value.repeat_id)
            continue
        protocol_time = _number(first_dispatch, "protocol_first_dispatch_at_ms")
        for root in value.preregistered_roots:
            if root.root_dispatched_at_ms is None or root.root_terminal_at_ms is None:
                invalid.add(value.repeat_id)
                break
            root_dispatch = _number(root.root_dispatched_at_ms, "root_dispatched_at_ms")
            terminal = _number(root.root_terminal_at_ms, "root_terminal_at_ms")
            if not protocol_time <= root_dispatch <= terminal or terminal < protocol_time:
                invalid.add(value.repeat_id)
                break
    return frozenset(invalid)


def _block_invalid_timeline_cells(
    cells: tuple[Exp5ObservationCell, ...], invalid_repeats: frozenset[int]
) -> tuple[Exp5ObservationCell, ...]:
    blocked_ids = {
        *(f"repeat{repeat_id}_wall_clock_ms" for repeat_id in invalid_repeats),
        "model_wall_clock_median_ms",
        "model_wall_clock_min_ms",
        "model_wall_clock_max_ms",
        "model_wall_clock_range_ms",
    }
    return tuple(
        replace(
            cell,
            value=None,
            reason="invalid_or_incomplete_exp5_repeat_timeline",
            publish_blocked=True,
            paper_eligible=False,
        )
        if cell.metric_id in blocked_ids
        else cell
        for cell in cells
    )


def _build_bundle(
    group: tuple[Exp5ModelRepeatFacts, ...], reasons: tuple[str, ...]
) -> MetricObservationBundle:
    members: dict[str, Mapping[str, object]] = {}
    for value in group:
        for root in value.preregistered_roots:
            members[f"quality-root:{root.preregistered_root_run_id}"] = {
                "member_kind": "preregistered_root",
                "preregistered_root_run_id": root.preregistered_root_run_id,
                "final_result_reference_complete": root.final_result_reference_complete,
                "end_to_end_verified_success": root.end_to_end_verified_success,
            }
            clock_facts: dict[str, object] = {
                "member_kind": "exp5_preregistered_root",
                "preregistered_root_run_id": root.preregistered_root_run_id,
                "repeat_id": value.repeat_id,
            }
            if root.root_terminal_at_ms is not None:
                clock_facts["root_terminal_at_ms"] = root.root_terminal_at_ms
            if value.protocol_first_dispatch_at_ms is not None:
                clock_facts["protocol_first_dispatch_at_ms"] = (
                    value.protocol_first_dispatch_at_ms
                )
            members[f"clock-root:{root.preregistered_root_run_id}"] = clock_facts
        for unit in value.planned_ai_units:
            members[f"unit:{value.repeat_id}:{unit.unit_id}"] = {
                "member_kind": "exp5_planned_ai_unit",
                "planned_call": unit.planned_call,
            }
        for attempt in value.first_provider_attempts:
            attempt_facts: dict[str, object] = {
                "member_kind": "exp5_provider_attempt",
                "provider_attempt_id": attempt.attempt_id,
                "actual_call": attempt.actual_call,
                "provider_transport_failure": attempt.provider_transport_failure,
                "parse_schema_unusable": attempt.parse_schema_unusable,
                "verification_checker_rejected": (
                    attempt.verification_checker_rejected
                ),
            }
            if attempt.actual_total_tokens is not None:
                attempt_facts["actual_total_tokens"] = attempt.actual_total_tokens
            if attempt.actual_cost_estimate_cny is not None:
                attempt_facts["actual_cost_estimate_cny"] = (
                    attempt.actual_cost_estimate_cny
                )
            if attempt.current_provider_roles is not None:
                attempt_facts["current_provider_roles"] = (
                    attempt.current_provider_roles
                )
            members[f"attempt:{value.repeat_id}:{attempt.attempt_id}"] = attempt_facts

    identity = group[0].frozen_identity
    return MetricObservationBundle(
        row_facts={
            "infra_invalid": bool(reasons),
            "max_retries": max((value.max_retries for value in group), default=0),
            "replacement_attempts_allowed": any(
                value.replacement_attempts_allowed for value in group
            ),
        },
        member_ids=tuple(members),
        member_facts_by_id=members,
        row_identity_digest=digest_json(
            {
                "table_scope": "exp5_model_endpoint_summary",
                "identity": None if identity is None else identity.to_dict(),
                "model_arm_ids": sorted({value.model_arm_id for value in group}),
            }
        ),
    )


def _evaluate_cells(
    contract: PaperMetricContract,
    table_id: str,
    metric_ids: tuple[str, ...],
    bundle: MetricObservationBundle,
) -> tuple[Exp5ObservationCell, ...]:
    observations: dict[tuple[str, str], VerifiedMetricObservation] = {}
    cells: list[Exp5ObservationCell] = []
    for metric_id in metric_ids:
        metric = contract.require_metric(table_id, metric_id)
        current = replace(bundle, verified_observations=tuple(observations.values()))
        evaluation = recompute_metric(
            contract,
            table_id,
            metric_id,
            row_kind=metric.row_kind,
            bundle=current,
        )
        cells.append(Exp5ObservationCell.from_evaluation(metric_id, evaluation))
        if evaluation.value is not None and not evaluation.publish_blocked:
            observations[(table_id, metric_id)] = VerifiedMetricObservation(
                observation_id=f"{bundle.row_identity_digest}:{table_id}:{metric_id}",
                table_id=table_id,
                metric_id=metric_id,
                row_kind=metric.row_kind,
                value=evaluation.value,
                value_domain=metric.value_domain,
                source_member_ids=evaluation.included_member_ids,
                evidence_verified=True,
                row_identity_digest=bundle.row_identity_digest,
            )
    return tuple(cells)


def _block_cells(
    cells: tuple[Exp5ObservationCell, ...], reason: str
) -> tuple[Exp5ObservationCell, ...]:
    return tuple(
        replace(
            cell,
            value=None,
            reason=reason,
            publish_blocked=True,
            paper_eligible=False,
        )
        for cell in cells
    )


def _validate_contract(contract: PaperMetricContract) -> None:
    if not isinstance(contract, PaperMetricContract):
        raise TypeError("contract must be PaperMetricContract")
    if contract.require_table(QUALITY_TABLE_ID).numeric_output_fields != EXP5_QUALITY_FIELDS:
        raise ValueError("Exp5 quality numeric field contract drift")
    if contract.require_table(RESOURCE_TABLE_ID).numeric_output_fields != EXP5_RESOURCE_FIELDS:
        raise ValueError("Exp5 resource numeric field contract drift")
    controls = contract.controls
    if (
        controls.exp5_summary_table_count != 2
        or controls.exp5_repeat_count != 3
        or controls.exp5_max_retries != 0
        or controls.exp5_first_nonpass_reason_priority
        != (
            "provider_transport_failure",
            "parse_schema_unusable",
            "verification_checker_rejection",
        )
        or controls.exp5_endpoint_serving_confounding_caption
        != ENDPOINT_SERVING_CONFOUNDING_CAPTION
    ):
        raise ValueError("Exp5 frozen contract controls drift")


def _typed_tuple(instance: object, field: str, expected: type) -> None:
    values = tuple(getattr(instance, field))
    if any(not isinstance(value, expected) for value in values):
        raise TypeError(f"{field} must contain {expected.__name__}")
    object.__setattr__(instance, field, values)


def _non_empty(value: object, field: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be non-empty")


def _bool(value: object, field: str) -> None:
    if type(value) is not bool:
        raise ValueError(f"{field} must be a bool")


def _number(value: object, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a nonnegative number")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be a nonnegative number") from exc
    if not number.is_finite() or number < 0:
        raise ValueError(f"{field} must be a nonnegative number")
    return number


def _optional_nonnegative_number(value: object, field: str) -> None:
    if value is not None:
        _number(value, field)


__all__ = [
    "CURRENT_PROVIDER_ROLES",
    "ENDPOINT_SERVING_CONFOUNDING_CAPTION",
    "EXP5_QUALITY_FIELDS",
    "EXP5_RESOURCE_FIELDS",
    "Exp5FirstProviderAttemptFacts",
    "Exp5ModelRepeatFacts",
    "Exp5ObservationCell",
    "Exp5ObservationRow",
    "Exp5PlannedAIUnitFacts",
    "Exp5PreregisteredRootFacts",
    "Exp5Projection",
    "Exp5SummaryTablePayload",
    "build_exp5_observations",
]
