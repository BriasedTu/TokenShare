"""Experiment 3 trace-main 与 online recovery 的纯指标 projector。"""

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
from tokenshare.experiments.paper_models import digest_json


TRACE_TABLE_ID = "exp3_trace_robustness"
ONLINE_TABLE_ID = "exp3_online_recovery"
TRACE_SOURCE_BANK_ROLES = (
    "request_body",
    "raw_output_or_provider_failure",
    "provenance",
    "usage_status",
    "latency",
    "pricing",
    "acquisition_attempt",
    "model_record",
)
ONLINE_CURRENT_PROVIDER_ROLES = (
    "request_body",
    "raw_output_or_provider_failure",
    "provenance",
    "usage_status",
    "latency",
    "pricing",
    "provider_attempt",
    "model_record",
)
STARTED_REPLACEMENT_ROLES = (
    "fault_or_death_event",
    "new_attempt",
    "provider_dispatch",
    "raw_or_failure",
    "provenance",
    "usage",
    "model_record",
)
SUCCESSFUL_REPLACEMENT_ROLES = STARTED_REPLACEMENT_ROLES + (
    "qualified_result",
    "original_task_unit_completed",
)
_REPLACEMENT_IDENTITY_FIELDS = (
    "fault_or_death_id",
    "original_task_unit_id",
    "original_attempt_id",
    "original_attempt_ordinal",
    "replacement_task_unit_id",
    "replacement_attempt_id",
    "replacement_attempt_ordinal",
)
_DISCARDED_IDENTITY_FIELDS = (
    "source_bank_entry_id",
    "current_attempt_id",
    "fault_or_death_id",
    "rejection_or_abandonment_id",
)
EXP3_TRACE_NUMERIC_FIELDS = (
    "preregistered_root_count",
    "final_result_root_count",
    "verified_correct_root_count",
    "completion_rate",
    "end_to_end_verified_success_rate",
    "controlled_wrong_candidate_count",
    "controlled_wrong_candidate_interception_count",
    "controlled_wrong_candidate_interception_rate",
    "controlled_wrong_candidate_escape_count",
    "controlled_wrong_candidate_escape_rate",
    "started_replacement_attempt_count",
    "successful_replacement_attempt_count",
    "replacement_attempt_success_rate",
    "reassignment_count",
    "discarded_trace_tokens",
    "trace_replay_wall_clock_overhead_ms",
    "trace_attributed_token_overhead",
    "trace_attributed_cost_overhead",
    "kill_progress_error_pp",
    "kill_progress_error_signed_mean_pp",
    "kill_progress_error_signed_max_pp",
    "recovered_valid_canonical_required_slots",
    "preregistered_required_slots",
    "result_completeness_rate",
)
EXP3_ONLINE_NUMERIC_FIELDS = (
    "preregistered_root_count",
    "final_result_root_count",
    "verified_correct_root_count",
    "completion_rate",
    "end_to_end_verified_success_rate",
    "replacement_chain_count",
    "worker_death_reassignment_chain_count",
    "actual_provider_calls",
    "actual_prompt_tokens",
    "actual_completion_tokens",
    "actual_total_tokens",
    "actual_cost_estimate_cny",
    "wasted_actual_tokens",
)
_ONLINE_RESOURCE_FIELDS = EXP3_ONLINE_NUMERIC_FIELDS[7:]
_ONLINE_CHAIN_FIELDS = (
    "replacement_chain_count",
    "worker_death_reassignment_chain_count",
)


@dataclass(frozen=True, kw_only=True)
class Exp3PersistedObservation:
    """调用方已从持久化记录 hydration 的单条 observation。"""

    observation_id: str
    facts: Mapping[str, object]

    def __post_init__(self) -> None:
        _non_empty(self.observation_id, "observation_id")
        if not isinstance(self.facts, Mapping):
            raise TypeError("facts must be a mapping")
        object.__setattr__(self, "facts", MappingProxyType(dict(self.facts)))


@dataclass(frozen=True, kw_only=True)
class Exp3TraceConditionInput:
    condition_id: str
    fault_type: str
    repeat_id: int
    sample_slot_id: str
    observations: tuple[Exp3PersistedObservation, ...]
    infra_invalid: bool = False

    def __post_init__(self) -> None:
        _non_empty(self.condition_id, "condition_id")
        _non_empty(self.fault_type, "fault_type")
        _non_empty(self.sample_slot_id, "sample_slot_id")
        _nonnegative_int(self.repeat_id, "repeat_id")
        _bool(self.infra_invalid, "infra_invalid")
        _normalize_observations(self, "observations")


@dataclass(frozen=True, kw_only=True)
class Exp3OnlineRecoveryInput:
    recovery_source: str
    case_id: str
    repeat_id: int
    observations: tuple[Exp3PersistedObservation, ...]
    infra_invalid: bool = False

    def __post_init__(self) -> None:
        _non_empty(self.recovery_source, "recovery_source")
        _non_empty(self.case_id, "case_id")
        _nonnegative_int(self.repeat_id, "repeat_id")
        _bool(self.infra_invalid, "infra_invalid")
        _normalize_observations(self, "observations")


@dataclass(frozen=True, kw_only=True)
class Exp3ObservationCell:
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
    ) -> "Exp3ObservationCell":
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
            paper_eligible=False,
        )


class _CellLookup:
    cells: tuple[Exp3ObservationCell, ...]

    @property
    def metric_ids(self) -> tuple[str, ...]:
        return tuple(cell.metric_id for cell in self.cells)

    def require_cell(self, metric_id: str) -> Exp3ObservationCell:
        matches = tuple(cell for cell in self.cells if cell.metric_id == metric_id)
        if len(matches) != 1:
            raise ValueError(f"unknown Exp3 observation cell: {metric_id}")
        return matches[0]


@dataclass(frozen=True, kw_only=True)
class Exp3TraceObservationRow(_CellLookup):
    condition_id: str
    fault_type: str
    repeat_id: int
    sample_slot_id: str
    cells: tuple[Exp3ObservationCell, ...]
    audit_ratios: Mapping[str, Decimal]
    paper_eligible: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "audit_ratios", MappingProxyType(dict(self.audit_ratios)))


@dataclass(frozen=True, kw_only=True)
class Exp3OnlineObservationRow(_CellLookup):
    recovery_source: str
    case_id: str
    repeat_id: int
    cells: tuple[Exp3ObservationCell, ...]
    paper_eligible: bool = False


def project_exp3_trace_condition(
    value: Exp3TraceConditionInput,
    contract: PaperMetricContract,
) -> Exp3TraceObservationRow:
    """从一条冻结 Exp3 condition 的 persisted observations 投影正式 cells。"""

    if not isinstance(value, Exp3TraceConditionInput):
        raise TypeError("value must be Exp3TraceConditionInput")
    _validate_contract(contract, TRACE_TABLE_ID, EXP3_TRACE_NUMERIC_FIELDS)
    member_facts, blocked = _trace_member_facts(value)
    bundle = _bundle(
        table_id=TRACE_TABLE_ID,
        row_facts={"fault_type": value.fault_type, "infra_invalid": value.infra_invalid},
        member_facts=member_facts,
        identity={
            "condition_id": value.condition_id,
            "fault_type": value.fault_type,
            "repeat_id": value.repeat_id,
            "sample_slot_id": value.sample_slot_id,
        },
    )
    cells = _evaluate_cells(
        contract,
        TRACE_TABLE_ID,
        "condition_observation",
        EXP3_TRACE_NUMERIC_FIELDS,
        bundle,
    )
    return Exp3TraceObservationRow(
        condition_id=value.condition_id,
        fault_type=value.fault_type,
        repeat_id=value.repeat_id,
        sample_slot_id=value.sample_slot_id,
        cells=_apply_cell_blocks(cells, blocked),
        audit_ratios=_trace_audit_ratios(value, member_facts),
        paper_eligible=False,
    )


def project_exp3_online_recovery(
    value: Exp3OnlineRecoveryInput,
    contract: PaperMetricContract,
) -> Exp3OnlineObservationRow:
    """从在线检查的当次 provider observations 投影 recovery cells。"""

    if not isinstance(value, Exp3OnlineRecoveryInput):
        raise TypeError("value must be Exp3OnlineRecoveryInput")
    _validate_contract(contract, ONLINE_TABLE_ID, EXP3_ONLINE_NUMERIC_FIELDS)
    member_facts: dict[str, Mapping[str, object]] = {
        observation.observation_id: dict(observation.facts)
        for observation in value.observations
    }
    blocked = _online_identity_blocks(value, member_facts)
    bundle = _bundle(
        table_id=ONLINE_TABLE_ID,
        row_facts={"infra_invalid": value.infra_invalid},
        member_facts=member_facts,
        identity={
            "recovery_source": value.recovery_source,
            "case_id": value.case_id,
            "repeat_id": value.repeat_id,
        },
    )
    cells = _evaluate_cells(
        contract,
        ONLINE_TABLE_ID,
        "online_recovery_summary",
        EXP3_ONLINE_NUMERIC_FIELDS,
        bundle,
    )
    return Exp3OnlineObservationRow(
        recovery_source=value.recovery_source,
        case_id=value.case_id,
        repeat_id=value.repeat_id,
        cells=_apply_cell_blocks(
            _block_incomplete_online_roles(cells, member_facts), blocked
        ),
        paper_eligible=False,
    )


def _trace_member_facts(
    value: Exp3TraceConditionInput,
) -> tuple[
    dict[str, Mapping[str, object]],
    Mapping[str, tuple[str, tuple[str, ...]]],
]:
    started_by_identity: dict[tuple[object, ...], list[str]] = {}
    started_by_attempt: dict[object, list[str]] = {}
    successful: list[tuple[str, Mapping[str, object]]] = []
    for observation in value.observations:
        facts = observation.facts
        if facts.get("member_kind") != "replacement_attempt":
            continue
        roles = tuple(facts.get("ordered_evidence_roles", ()))
        identity = _replacement_identity(facts)
        if roles == STARTED_REPLACEMENT_ROLES and identity is not None:
            started_by_identity.setdefault(identity, []).append(observation.observation_id)
            started_by_attempt.setdefault(facts.get("replacement_attempt_id"), []).append(
                observation.observation_id
            )
        elif roles == SUCCESSFUL_REPLACEMENT_ROLES:
            successful.append((observation.observation_id, facts))

    success_errors: list[tuple[str, str]] = []
    success_by_identity: dict[tuple[object, ...], list[str]] = {}
    for observation_id, facts in successful:
        identity = _replacement_identity(facts)
        if identity is None:
            success_errors.append(("mismatched_successful_replacement", observation_id))
            continue
        starters = started_by_identity.get(identity, ())
        if len(starters) != 1:
            reason = (
                "mismatched_successful_replacement"
                if facts.get("replacement_attempt_id") in started_by_attempt
                else "orphan_successful_replacement"
            )
            success_errors.append((reason, observation_id))
            continue
        success_by_identity.setdefault(identity, []).append(observation_id)
    for observation_ids in success_by_identity.values():
        if len(observation_ids) > 1:
            success_errors.extend(
                ("duplicate_successful_replacement", observation_id)
                for observation_id in observation_ids
            )

    projected: dict[str, Mapping[str, object]] = {}
    discarded_errors: list[tuple[str, str]] = []
    for observation in value.observations:
        facts = dict(observation.facts)
        if facts.get("member_kind") == "exp3_trace_pair":
            same_slot = (
                facts.get("fault_sample_slot_id") == value.sample_slot_id
                and facts.get("reference_sample_slot_id") == value.sample_slot_id
            )
            facts["pair_evidence_complete"] = bool(
                facts.get("pair_evidence_complete") is True and same_slot
            )
        discarded_reason = _discarded_trace_error(facts)
        if discarded_reason is not None:
            discarded_errors.append((discarded_reason, observation.observation_id))
        projected[observation.observation_id] = facts
    blocked: dict[str, tuple[str, tuple[str, ...]]] = {}
    if success_errors:
        reason, _ = success_errors[0]
        member_ids = tuple(observation_id for _, observation_id in success_errors)
        for metric_id in (
            "successful_replacement_attempt_count",
            "replacement_attempt_success_rate",
        ):
            blocked[metric_id] = (reason, member_ids)
    if discarded_errors:
        reason, _ = discarded_errors[0]
        blocked["discarded_trace_tokens"] = (
            reason,
            tuple(observation_id for _, observation_id in discarded_errors),
        )
    return projected, blocked


def _replacement_identity(
    facts: Mapping[str, object],
) -> tuple[object, ...] | None:
    for field in _REPLACEMENT_IDENTITY_FIELDS:
        value = facts.get(field)
        if field.endswith("_ordinal"):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return None
        elif not isinstance(value, str) or not value:
            return None
    if not isinstance(facts.get("fault_or_death_id"), str) or not facts.get(
        "fault_or_death_id"
    ):
        return None
    linked_pairs = (
        ("fault_or_death_task_unit_id", "original_task_unit_id"),
        ("fault_or_death_attempt_id", "original_attempt_id"),
        ("new_attempt_fault_or_death_id", "fault_or_death_id"),
        ("new_attempt_task_unit_id", "replacement_task_unit_id"),
        ("new_attempt_id", "replacement_attempt_id"),
        ("new_attempt_ordinal", "replacement_attempt_ordinal"),
        ("replacement_task_unit_id", "original_task_unit_id"),
    )
    if any(facts.get(left) != facts.get(right) for left, right in linked_pairs):
        return None
    if facts.get("replacement_attempt_id") == facts.get("original_attempt_id"):
        return None
    if facts.get("replacement_attempt_ordinal") != facts.get("original_attempt_ordinal") + 1:
        return None
    return tuple(facts[field] for field in _REPLACEMENT_IDENTITY_FIELDS)


def _discarded_trace_error(facts: Mapping[str, object]) -> str | None:
    if (
        facts.get("member_kind") != "exp3_discarded_trace_consumption"
        or facts.get("discarded_after_fault") is not True
    ):
        return None
    if facts.get("canonical_excluded") is not True:
        return "discarded_trace_not_canonically_excluded"
    if facts.get("source_usage_total_tokens") is None:
        return "discarded_trace_missing_source_usage"
    for field in _DISCARDED_IDENTITY_FIELDS:
        if not isinstance(facts.get(field), str) or not facts.get(field):
            return f"discarded_trace_missing_identity:{field}"
    exclusion_id = facts.get("canonical_exclusion_rejection_or_abandonment_id")
    if (
        not isinstance(exclusion_id, str)
        or not exclusion_id
        or exclusion_id != facts.get("rejection_or_abandonment_id")
    ):
        return "discarded_trace_exclusion_backlink_mismatch"
    return None


def _online_identity_blocks(
    value: Exp3OnlineRecoveryInput,
    member_facts: Mapping[str, Mapping[str, object]],
) -> Mapping[str, tuple[str, tuple[str, ...]]]:
    anchors = tuple(
        (member_id, facts)
        for member_id, facts in member_facts.items()
        if facts.get("member_kind") == "exp3_online_recovery_identity"
    )
    provider_members = tuple(
        (member_id, facts)
        for member_id, facts in member_facts.items()
        if facts.get("member_kind")
        in {
            "online_recovery_original_attempt",
            "online_recovery_replacement_attempt",
        }
    )
    blocked: dict[str, tuple[str, tuple[str, ...]]] = {}
    anchor_error: str | None = None
    anchor_id = "missing-online-recovery-anchor"
    anchor: Mapping[str, object] = {}
    links: Mapping[str, tuple[str, str, str, str, str, str]] = {}
    if len(anchors) != 1:
        anchor_error = "online_recovery_anchor_cardinality"
    else:
        anchor_id, anchor = anchors[0]
        if (
            anchor.get("case_id") != value.case_id
            or anchor.get("recovery_source") != value.recovery_source
            or not isinstance(anchor.get("current_replacement_attempt_id"), str)
            or not anchor.get("current_replacement_attempt_id")
        ):
            anchor_error = "online_recovery_anchor_identity_mismatch"
        else:
            links, anchor_error = _provider_link_index(
                anchor.get("provider_object_links")
            )

    provider_error = anchor_error
    provider_error_ids: list[str] = [anchor_id] if anchor_error else []
    if provider_error is None:
        provider_error, provider_error_ids = _validate_provider_members(
            value,
            anchor,
            links,
            provider_members,
        )
    if provider_error is None:
        invalid_role_ids = [
            member_id
            for member_id, facts in provider_members
            if tuple(facts.get("current_provider_roles", ()))
            != ONLINE_CURRENT_PROVIDER_ROLES
        ]
        if invalid_role_ids:
            provider_error = "provider_member_roles_incomplete"
            provider_error_ids = invalid_role_ids
    if provider_error is not None:
        reason = f"invalid_online_provider_identity:{provider_error}"
        member_ids = tuple(provider_error_ids) or (anchor_id,)
        for metric_id in _ONLINE_RESOURCE_FIELDS:
            blocked[metric_id] = (reason, member_ids)

    chain_errors: list[str] = []
    current_attempt = anchor.get("current_replacement_attempt_id")
    if anchor_error is not None:
        chain_errors.append(anchor_id)
    else:
        for member_id, facts in member_facts.items():
            if (
                facts.get("member_kind") == "replacement_attempt"
                and tuple(facts.get("ordered_evidence_roles", ()))
                == SUCCESSFUL_REPLACEMENT_ROLES
                and (
                    facts.get("replacement_attempt_id") != current_attempt
                    or facts.get("recovery_source_kind") != value.recovery_source
                    or _replacement_identity(facts) is None
                )
            ):
                chain_errors.append(member_id)
    if chain_errors:
        for metric_id in _ONLINE_CHAIN_FIELDS:
            blocked[metric_id] = (
                "invalid_online_recovery_chain_identity",
                tuple(chain_errors),
            )
    return blocked


def _provider_link_index(
    raw_links: object,
) -> tuple[
    Mapping[str, tuple[str, str, str, str, str, str]],
    str | None,
]:
    if not isinstance(raw_links, (tuple, list)):
        return {}, "provider_object_links_missing"
    normalized: list[tuple[str, str, str, str, str, str]] = []
    for raw in raw_links:
        if (
            not isinstance(raw, (tuple, list))
            or len(raw) != 6
            or any(not isinstance(item, str) or not item for item in raw)
        ):
            return {}, "provider_object_link_invalid"
        normalized.append(tuple(raw))  # type: ignore[arg-type]
    for index in range(6):
        values = tuple(link[index] for link in normalized)
        if len(set(values)) != len(values):
            return {}, "provider_object_link_duplicate"
    return {link[0]: link for link in normalized}, None


def _validate_provider_members(
    value: Exp3OnlineRecoveryInput,
    anchor: Mapping[str, object],
    links: Mapping[str, tuple[str, str, str, str, str, str]],
    provider_members: tuple[tuple[str, Mapping[str, object]], ...],
) -> tuple[str | None, list[str]]:
    identity_fields = (
        "provider_attempt_id",
        "provider_response_id",
        "raw_or_failure_id",
        "provenance_id",
        "usage_id",
        "model_record_id",
    )
    current_attempt = anchor["current_replacement_attempt_id"]
    attempts: list[str] = []
    invalid_ids: list[str] = []
    for member_id, facts in provider_members:
        if (
            facts.get("case_id") != value.case_id
            or facts.get("recovery_source") != value.recovery_source
            or facts.get("current_replacement_attempt_id") != current_attempt
        ):
            invalid_ids.append(member_id)
            continue
        identity = tuple(facts.get(field) for field in identity_fields)
        if any(not isinstance(item, str) or not item for item in identity):
            invalid_ids.append(member_id)
            continue
        attempt_id = identity[0]
        attempts.append(attempt_id)
        if links.get(attempt_id) != identity:
            invalid_ids.append(member_id)
            continue
        if (
            facts.get("member_kind") == "online_recovery_replacement_attempt"
            and attempt_id != current_attempt
        ):
            invalid_ids.append(member_id)
            continue
        original_response_id = facts.get("original_provider_response_id")
        if original_response_id is not None and original_response_id != identity[1]:
            invalid_ids.append(member_id)
    if invalid_ids:
        return "provider_member_link_mismatch", invalid_ids
    if not links or not provider_members:
        return "current_replacement_provider_missing", [
            member_id for member_id, _ in provider_members
        ]
    if len(set(attempts)) != len(attempts):
        return "provider_attempt_member_duplicate", [member_id for member_id, _ in provider_members]
    if set(attempts) != set(links):
        return "provider_member_orphan_or_missing", [member_id for member_id, _ in provider_members]
    if current_attempt not in links or current_attempt not in attempts:
        return "current_replacement_provider_missing", [member_id for member_id, _ in provider_members]
    return None, []


def _trace_audit_ratios(
    value: Exp3TraceConditionInput,
    member_facts: Mapping[str, Mapping[str, object]],
) -> Mapping[str, Decimal]:
    eligible = tuple(
        facts
        for facts in member_facts.values()
        if facts.get("member_kind") == "exp3_trace_pair"
        and facts.get("pair_evidence_complete") is True
        and facts.get("fault_sample_slot_id") == value.sample_slot_id
        and facts.get("reference_sample_slot_id") == value.sample_slot_id
    )
    if len(eligible) != 1:
        return {}
    pair = eligible[0]
    names = (
        (
            "trace_replay_wall_clock_ratio_audit",
            "fault_trace_replay_wall_clock_ms",
            "reference_trace_replay_wall_clock_ms",
        ),
        (
            "trace_attributed_token_ratio_audit",
            "fault_trace_attributed_tokens",
            "reference_trace_attributed_tokens",
        ),
        (
            "trace_attributed_cost_ratio_audit",
            "fault_trace_attributed_cost",
            "reference_trace_attributed_cost",
        ),
    )
    result: dict[str, Decimal] = {}
    for output_name, fault_name, reference_name in names:
        fault = _decimal_or_none(pair.get(fault_name))
        reference = _decimal_or_none(pair.get(reference_name))
        if fault is not None and reference is not None and reference > 0:
            result[output_name] = fault / reference
    return result


def _block_incomplete_online_roles(
    cells: tuple[Exp3ObservationCell, ...],
    member_facts: Mapping[str, Mapping[str, object]],
) -> tuple[Exp3ObservationCell, ...]:
    """Contract 的通用 provider check 不覆盖 chain member，projector 在此补齐。"""

    result: list[Exp3ObservationCell] = []
    for cell in cells:
        invalid = tuple(
            member_id
            for member_id in cell.included_member_ids
            if member_facts[member_id].get("member_kind") == "replacement_attempt"
            and tuple(member_facts[member_id].get("current_provider_roles", ()))
            != ONLINE_CURRENT_PROVIDER_ROLES
        )
        if not invalid:
            result.append(cell)
            continue
        result.append(
            replace(
                cell,
                value=None,
                reason=f"invalid_current_provider_roles:{invalid[0]}",
                publish_blocked=True,
                blocked_member_ids=tuple(dict.fromkeys((*cell.blocked_member_ids, *invalid))),
            )
        )
    return tuple(result)


def _apply_cell_blocks(
    cells: tuple[Exp3ObservationCell, ...],
    blocked: Mapping[str, tuple[str, tuple[str, ...]]],
) -> tuple[Exp3ObservationCell, ...]:
    result: list[Exp3ObservationCell] = []
    for cell in cells:
        decision = blocked.get(cell.metric_id)
        if decision is None:
            result.append(cell)
            continue
        reason, member_ids = decision
        result.append(
            replace(
                cell,
                value=None,
                reason=reason,
                publish_blocked=True,
                blocked_member_ids=tuple(
                    dict.fromkeys((*cell.blocked_member_ids, *member_ids))
                ),
            )
        )
    return tuple(result)


def _evaluate_cells(
    contract: PaperMetricContract,
    table_id: str,
    row_kind: str,
    metric_ids: Sequence[str],
    bundle: MetricObservationBundle,
) -> tuple[Exp3ObservationCell, ...]:
    observations: list[VerifiedMetricObservation] = []
    cells: list[Exp3ObservationCell] = []
    for metric_id in metric_ids:
        metric = contract.require_metric(table_id, metric_id)
        effective_row_kind = (
            metric.row_kind
            if table_id == TRACE_TABLE_ID
            else row_kind
        )
        current = MetricObservationBundle(
            row_facts=bundle.row_facts,
            member_ids=bundle.member_ids,
            member_facts_by_id=bundle.member_facts_by_id,
            verified_observations=tuple(observations),
            row_identity_digest=bundle.row_identity_digest,
        )
        evaluation = recompute_metric(
            contract,
            table_id,
            metric_id,
            row_kind=effective_row_kind,
            bundle=current,
        )
        cells.append(Exp3ObservationCell.from_evaluation(metric_id, evaluation))
        if evaluation.value is not None and not evaluation.publish_blocked:
            observations.append(
                VerifiedMetricObservation(
                    observation_id=f"{bundle.row_identity_digest}:{metric_id}",
                    table_id=table_id,
                    metric_id=metric_id,
                    row_kind=effective_row_kind,
                    value=evaluation.value,
                    value_domain=metric.value_domain,
                    source_member_ids=evaluation.included_member_ids,
                    evidence_verified=True,
                    row_identity_digest=bundle.row_identity_digest,
                )
            )
    return tuple(cells)


def _bundle(
    *,
    table_id: str,
    row_facts: Mapping[str, object],
    member_facts: Mapping[str, Mapping[str, object]],
    identity: Mapping[str, object],
) -> MetricObservationBundle:
    return MetricObservationBundle(
        row_facts=row_facts,
        member_ids=tuple(member_facts),
        member_facts_by_id=member_facts,
        row_identity_digest=digest_json({"table_id": table_id, **identity}),
    )


def _validate_contract(
    contract: PaperMetricContract,
    table_id: str,
    expected_fields: tuple[str, ...],
) -> None:
    if not isinstance(contract, PaperMetricContract):
        raise TypeError("contract must be PaperMetricContract")
    if contract.require_table(table_id).numeric_output_fields != expected_fields:
        raise ValueError(f"{table_id} numeric output field contract drift")


def _normalize_observations(instance: object, field_name: str) -> None:
    observations = tuple(getattr(instance, field_name))
    if any(not isinstance(value, Exp3PersistedObservation) for value in observations):
        raise TypeError(f"{field_name} must contain persisted observations")
    ids = tuple(value.observation_id for value in observations)
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate identity in {field_name}")
    object.__setattr__(instance, field_name, observations)


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and number >= 0 else None


def _non_empty(value: object, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be non-empty")


def _nonnegative_int(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")


def _bool(value: object, name: str) -> None:
    if type(value) is not bool:
        raise ValueError(f"{name} must be a bool")


__all__ = [
    "EXP3_ONLINE_NUMERIC_FIELDS",
    "EXP3_TRACE_NUMERIC_FIELDS",
    "ONLINE_CURRENT_PROVIDER_ROLES",
    "STARTED_REPLACEMENT_ROLES",
    "SUCCESSFUL_REPLACEMENT_ROLES",
    "TRACE_SOURCE_BANK_ROLES",
    "Exp3ObservationCell",
    "Exp3OnlineObservationRow",
    "Exp3OnlineRecoveryInput",
    "Exp3PersistedObservation",
    "Exp3TraceConditionInput",
    "Exp3TraceObservationRow",
    "project_exp3_online_recovery",
    "project_exp3_trace_condition",
]
