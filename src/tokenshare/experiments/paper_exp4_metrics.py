"""Experiment 4 persisted direct/pair facts 的纯指标 projector。"""

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


EXP4_TABLE_ID = "exp4_ablation"
EXP4_MODES = (
    "FULL",
    "NO_VERIFICATION",
    "NO_PARSER_POLICY",
    "NO_REQUEUE",
    "NO_MERGE_GATE",
)
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
EXP4_NUMERIC_FIELDS = (
    "preregistered_root_count",
    "final_result_root_count",
    "verified_correct_root_count",
    "completion_rate",
    "end_to_end_verified_success_rate",
    "paired_root_count",
    "full_success_ablation_success",
    "full_success_ablation_failure",
    "full_failure_ablation_success",
    "full_failure_ablation_failure",
    "end_to_end_success_loss_vs_full",
    "completion_loss_vs_full",
    "trace_replay_wall_clock_delta_vs_full",
    "trace_attributed_token_delta_vs_full",
    "trace_attributed_cost_delta_vs_full",
    "independently_labeled_invalid_candidate_count",
    "wrong_canonical_acceptance_count",
    "wrong_canonical_acceptance_rate",
    "raw_only_exposure_count",
    "raw_only_acceptance_count",
    "raw_only_acceptance_rate",
    "stuck_task_count",
    "stuck_task_rate",
    "premature_merge_attempt_count",
    "premature_merge_failure_count",
    "premature_merge_failure_rate",
)
_MODE_SUMMARY_FIELDS = EXP4_NUMERIC_FIELDS[:5]
_PAIR_FIELDS = EXP4_NUMERIC_FIELDS[5:15]
_MODE_SPECIFIC_FIELDS = EXP4_NUMERIC_FIELDS[15:]
_TRANSITION_FIELDS = EXP4_NUMERIC_FIELDS[5:10]
_MODE_ORDER = {mode: index for index, mode in enumerate(EXP4_MODES)}
_FULL_FORBIDDEN_MEMBER_KINDS = {
    "independently_labeled_invalid_candidate",
    "wrong_canonical_acceptance_event",
    "raw_only_exposure_event",
    "stuck_task_event",
    "premature_merge_event",
}
_FULL_FORBIDDEN_TRUE_FIELDS = {
    "verification_disabled",
    "parser_policy_disabled",
    "requeue_disabled",
    "merge_gate_disabled",
}


@dataclass(frozen=True, kw_only=True)
class Exp4PersistedObservation:
    """调用方已经从持久化 hook/candidate/root 记录 hydration 的 observation。"""

    observation_id: str
    facts: Mapping[str, object]

    def __post_init__(self) -> None:
        _non_empty(self.observation_id, "observation_id")
        if not isinstance(self.facts, Mapping):
            raise TypeError("facts must be a mapping")
        object.__setattr__(self, "facts", MappingProxyType(dict(self.facts)))


@dataclass(frozen=True, kw_only=True)
class Exp4DirectRootFacts:
    """一条预注册 root 的 persisted direct/result/resource facts。"""

    preregistered_root_run_id: str
    case_id: str
    case_record_digest: str
    sample_slot_index: int
    replacement_slot_ids: tuple[str, ...]
    final_result_reference_complete: bool
    end_to_end_verified_success: bool
    trace_replay_wall_clock_ms: int | Decimal | None
    trace_attributed_tokens: int | Decimal | None
    trace_attributed_cost: int | Decimal | None
    identity_consistent: bool
    paper_evidence_complete: bool
    infrastructure_valid: bool
    source_bank_roles: tuple[str, ...] | None
    source_bank_entry_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for value, name in (
            (self.preregistered_root_run_id, "preregistered_root_run_id"),
            (self.case_id, "case_id"),
            (self.case_record_digest, "case_record_digest"),
        ):
            _non_empty(value, name)
        _nonnegative_int(self.sample_slot_index, "sample_slot_index")
        slots = tuple(self.replacement_slot_ids)
        if any(not isinstance(value, str) or not value for value in slots):
            raise ValueError("replacement_slot_ids must contain non-empty strings")
        if len(set(slots)) != len(slots):
            raise ValueError("duplicate replacement slot id")
        object.__setattr__(self, "replacement_slot_ids", slots)
        for name in (
            "final_result_reference_complete",
            "end_to_end_verified_success",
            "identity_consistent",
            "paper_evidence_complete",
            "infrastructure_valid",
        ):
            _bool(getattr(self, name), name)
        if self.end_to_end_verified_success and not self.final_result_reference_complete:
            raise ValueError("end-to-end success requires a complete final result reference")
        for name in (
            "trace_replay_wall_clock_ms",
            "trace_attributed_tokens",
            "trace_attributed_cost",
        ):
            value = getattr(self, name)
            if value is not None:
                _nonnegative_number(value, name)
        if self.source_bank_roles is not None:
            roles = tuple(self.source_bank_roles)
            if any(not isinstance(value, str) or not value for value in roles):
                raise ValueError("source_bank_roles must contain non-empty strings")
            if len(set(roles)) != len(roles):
                raise ValueError("duplicate source bank role")
            object.__setattr__(self, "source_bank_roles", roles)
        entry_ids = tuple(self.source_bank_entry_ids)
        if any(not isinstance(value, str) or not value for value in entry_ids):
            raise ValueError("source_bank_entry_ids must contain non-empty strings")
        if len(set(entry_ids)) != len(entry_ids):
            raise ValueError("duplicate source bank entry id")
        object.__setattr__(self, "source_bank_entry_ids", entry_ids)


@dataclass(frozen=True, kw_only=True)
class Exp4ModeInput:
    """一个预注册 mode/domain/repeat scope 的 persisted facts。"""

    condition_id: str
    domain: str
    repeat_id: int
    ablation_mode: str
    roots: tuple[Exp4DirectRootFacts, ...]
    observations: tuple[Exp4PersistedObservation, ...] = ()
    identity_consistent: bool = True
    paper_evidence_complete: bool = True
    infrastructure_valid: bool = True

    def __post_init__(self) -> None:
        _non_empty(self.condition_id, "condition_id")
        _non_empty(self.domain, "domain")
        _nonnegative_int(self.repeat_id, "repeat_id")
        if self.ablation_mode not in EXP4_MODES:
            raise ValueError("unknown Experiment 4 ablation mode")
        _normalize_typed_tuple(self, "roots", Exp4DirectRootFacts)
        _normalize_typed_tuple(self, "observations", Exp4PersistedObservation)
        for name in (
            "identity_consistent",
            "paper_evidence_complete",
            "infrastructure_valid",
        ):
            _bool(getattr(self, name), name)
        observation_ids = tuple(value.observation_id for value in self.observations)
        if len(set(observation_ids)) != len(observation_ids):
            raise ValueError("duplicate persisted observation id within mode input")


@dataclass(frozen=True, kw_only=True)
class Exp4ObservationCell:
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
    ) -> "Exp4ObservationCell":
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
    cells: tuple[Exp4ObservationCell, ...]

    @property
    def metric_ids(self) -> tuple[str, ...]:
        return tuple(cell.metric_id for cell in self.cells)

    def require_cell(self, metric_id: str) -> Exp4ObservationCell:
        matches = tuple(cell for cell in self.cells if cell.metric_id == metric_id)
        if len(matches) != 1:
            raise ValueError(f"unknown Exp4 observation cell: {metric_id}")
        return matches[0]


@dataclass(frozen=True, kw_only=True)
class Exp4ModeSummaryObservation(_CellLookup):
    domain: str
    repeat_id: int
    ablation_mode: str
    preregistered_root_run_ids: tuple[str, ...]
    cells: tuple[Exp4ObservationCell, ...]
    ineligibility_reasons: tuple[str, ...]
    paper_eligible: bool = False


@dataclass(frozen=True, kw_only=True)
class Exp4PairObservation(_CellLookup):
    pair_identity: tuple[str, int, int, tuple[str, ...], str]
    case_record_digest: str
    domain: str
    ablation_mode: str
    full_root_run_id: str | None
    ablation_root_run_id: str
    cells: tuple[Exp4ObservationCell, ...]
    ineligibility_reasons: tuple[str, ...]
    paper_eligible: bool = False


@dataclass(frozen=True, kw_only=True)
class Exp4ModeSpecificObservation(_CellLookup):
    domain: str
    repeat_id: int
    ablation_mode: str
    cells: tuple[Exp4ObservationCell, ...]
    ineligibility_reasons: tuple[str, ...]
    paper_eligible: bool = False


@dataclass(frozen=True, kw_only=True)
class Exp4Projection:
    mode_summary_rows: tuple[Exp4ModeSummaryObservation, ...]
    pair_rows: tuple[Exp4PairObservation, ...]
    mode_specific_rows: tuple[Exp4ModeSpecificObservation, ...]
    paper_eligible: bool = False


@dataclass(frozen=True, kw_only=True)
class _RootEntry:
    mode_input: Exp4ModeInput
    root: Exp4DirectRootFacts


def build_exp4_observations(
    mode_inputs: Sequence[Exp4ModeInput],
    contract: PaperMetricContract,
) -> Exp4Projection:
    """只消费传入的 persisted direct/hook facts，投影 Exp4 cells。"""

    _validate_contract(contract)
    inputs = tuple(mode_inputs)
    if any(not isinstance(value, Exp4ModeInput) for value in inputs):
        raise TypeError("mode_inputs must contain Exp4ModeInput")
    entries = tuple(
        _RootEntry(mode_input=value, root=root)
        for value in inputs
        for root in value.roots
    )
    root_ids = tuple(value.root.preregistered_root_run_id for value in entries)
    if len(set(root_ids)) != len(root_ids):
        raise ValueError("duplicate Exp4 preregistered root run id")

    groups: dict[tuple[str, int, str], list[Exp4ModeInput]] = {}
    for value in inputs:
        groups.setdefault((value.domain, value.repeat_id, value.ablation_mode), []).append(value)

    ablation_identity_counts: dict[tuple[object, ...], int] = {}
    for entry in entries:
        if entry.mode_input.ablation_mode != "FULL":
            identity = _ablation_identity_key(entry)
            ablation_identity_counts[identity] = ablation_identity_counts.get(identity, 0) + 1
    duplicate_ablation_root_ids = frozenset(
        entry.root.preregistered_root_run_id
        for entry in entries
        if entry.mode_input.ablation_mode != "FULL"
        and ablation_identity_counts[_ablation_identity_key(entry)] > 1
    )

    mode_rows: list[Exp4ModeSummaryObservation] = []
    specific_rows: list[Exp4ModeSpecificObservation] = []
    for key in sorted(groups, key=_group_sort_key):
        group = tuple(groups[key])
        roots = tuple(root for value in group for root in value.roots)
        member_facts = _group_member_facts(group)
        reasons = _group_ineligibility_reasons(group)
        duplicate_identity = any(
            root.preregistered_root_run_id in duplicate_ablation_root_ids
            for root in roots
        )
        if duplicate_identity:
            reasons = tuple(
                dict.fromkeys((*reasons, "duplicate_exp4_ablation_identity"))
            )
        row_facts = {
            "ablation_mode": key[2],
            "infra_invalid": bool(reasons),
        }
        identity = {
            "kind": "mode_scope",
            "domain": key[0],
            "repeat_id": key[1],
            "ablation_mode": key[2],
            "condition_ids": [value.condition_id for value in group],
        }
        bundle = _bundle(row_facts=row_facts, member_facts=member_facts, identity=identity)
        mode_cells = _evaluate_cells(contract, _MODE_SUMMARY_FIELDS, bundle)
        specific_cells = _evaluate_cells(contract, _MODE_SPECIFIC_FIELDS, bundle)
        if duplicate_identity:
            mode_cells = _block_cells(
                mode_cells, "duplicate_exp4_ablation_identity"
            )
            specific_cells = _block_cells(
                specific_cells, "duplicate_exp4_ablation_identity"
            )
        mode_rows.append(
            Exp4ModeSummaryObservation(
                domain=key[0],
                repeat_id=key[1],
                ablation_mode=key[2],
                preregistered_root_run_ids=tuple(
                    root.preregistered_root_run_id for root in roots
                ),
                cells=mode_cells,
                ineligibility_reasons=reasons,
            )
        )
        specific_rows.append(
            Exp4ModeSpecificObservation(
                domain=key[0],
                repeat_id=key[1],
                ablation_mode=key[2],
                cells=specific_cells,
                ineligibility_reasons=reasons,
            )
        )

    full_index: dict[tuple[str, int, str, int], list[_RootEntry]] = {}
    for entry in entries:
        if entry.mode_input.ablation_mode == "FULL":
            full_index.setdefault(_match_key(entry), []).append(entry)

    pair_rows = tuple(
        _build_pair(
            contract,
            entry,
            tuple(full_index.get(_match_key(entry), ())),
            duplicate_ablation_identity=(
                entry.root.preregistered_root_run_id in duplicate_ablation_root_ids
            ),
        )
        for entry in sorted(entries, key=_entry_sort_key)
        if entry.mode_input.ablation_mode != "FULL"
    )
    return Exp4Projection(
        mode_summary_rows=tuple(mode_rows),
        pair_rows=pair_rows,
        mode_specific_rows=tuple(specific_rows),
    )


def _build_pair(
    contract: PaperMetricContract,
    ablation: _RootEntry,
    full_candidates: tuple[_RootEntry, ...],
    *,
    duplicate_ablation_identity: bool,
) -> Exp4PairObservation:
    full = full_candidates[0] if len(full_candidates) == 1 else None
    reasons: list[str] = []
    if duplicate_ablation_identity:
        reasons.append("duplicate_exp4_ablation_identity")
    if len(full_candidates) != 1:
        reasons.append("missing_unique_exp4_full")
    reasons.extend(_entry_ineligibility_reasons(ablation))
    if full is not None:
        reasons.extend(_entry_ineligibility_reasons(full))
        if full.root.case_record_digest != ablation.root.case_record_digest:
            reasons.append("case_record_digest_mismatch")

    pair_complete = not reasons
    pair_member_id = f"exp4-pair:{ablation.root.preregistered_root_run_id}"
    facts: dict[str, object] = {
        "member_kind": "exp4_paired_root",
        "pair_evidence_complete": pair_complete,
        "full_success": False if full is None else full.root.end_to_end_verified_success,
        "ablation_success": ablation.root.end_to_end_verified_success,
        "full_end_to_end_verified_success_rate": 0 if full is None else int(full.root.end_to_end_verified_success),
        "ablation_end_to_end_verified_success_rate": int(ablation.root.end_to_end_verified_success),
        "full_completion_rate": 0 if full is None else int(full.root.final_result_reference_complete),
        "ablation_completion_rate": int(ablation.root.final_result_reference_complete),
    }
    if full is not None:
        _add_complete_pair_resource_facts(facts, full.root, ablation.root)
    pair_roots = tuple(
        root
        for root in (
            None if full is None else full.root,
            ablation.root,
        )
        if root is not None
    )
    facts["lineage_root_run_ids"] = tuple(
        root.preregistered_root_run_id for root in pair_roots
    )
    facts["source_bank_entry_ids"] = tuple(
        dict.fromkeys(
            entry_id
            for root in pair_roots
            for entry_id in root.source_bank_entry_ids
        )
    )
    member_facts = {
        root.preregistered_root_run_id: _root_lineage_facts(root)
        for root in pair_roots
    }
    member_facts[pair_member_id] = facts
    identity = {
        "kind": "pair",
        "case_id": ablation.root.case_id,
        "case_record_digest": ablation.root.case_record_digest,
        "domain": ablation.mode_input.domain,
        "repeat_id": ablation.mode_input.repeat_id,
        "sample_slot_index": ablation.root.sample_slot_index,
        "replacement_slot_ids": ablation.root.replacement_slot_ids,
        "ablation_mode": ablation.mode_input.ablation_mode,
    }
    bundle = _bundle(
        row_facts={
            "ablation_mode": ablation.mode_input.ablation_mode,
            "infra_invalid": any(reason.endswith("infrastructure_invalid") for reason in reasons),
            "pair_evidence_complete": pair_complete,
        },
        member_facts=member_facts,
        identity=identity,
    )
    if pair_complete:
        bundle = _with_transition_seeds(contract, bundle, pair_member_id, facts)
    cells = _evaluate_cells(contract, _PAIR_FIELDS, bundle)
    if duplicate_ablation_identity:
        cells = _block_cells(cells, "duplicate_exp4_ablation_identity")
    return Exp4PairObservation(
        pair_identity=(
            ablation.root.case_id,
            ablation.mode_input.repeat_id,
            ablation.root.sample_slot_index,
            ablation.root.replacement_slot_ids,
            ablation.mode_input.ablation_mode,
        ),
        case_record_digest=ablation.root.case_record_digest,
        domain=ablation.mode_input.domain,
        ablation_mode=ablation.mode_input.ablation_mode,
        full_root_run_id=None if full is None else full.root.preregistered_root_run_id,
        ablation_root_run_id=ablation.root.preregistered_root_run_id,
        cells=cells,
        ineligibility_reasons=tuple(dict.fromkeys(reasons)),
    )


def _with_transition_seeds(
    contract: PaperMetricContract,
    bundle: MetricObservationBundle,
    member_id: str,
    facts: Mapping[str, object],
) -> MetricObservationBundle:
    full_success = bool(facts["full_success"])
    ablation_success = bool(facts["ablation_success"])
    values = {
        "paired_root_count": 1,
        "full_success_ablation_success": int(full_success and ablation_success),
        "full_success_ablation_failure": int(full_success and not ablation_success),
        "full_failure_ablation_success": int(not full_success and ablation_success),
        "full_failure_ablation_failure": int(not full_success and not ablation_success),
    }
    seeds = tuple(
        _verified_observation(
            contract,
            metric_id,
            value,
            bundle.row_identity_digest,
            (member_id,),
        )
        for metric_id, value in values.items()
    )
    return MetricObservationBundle(
        row_facts=bundle.row_facts,
        member_ids=bundle.member_ids,
        member_facts_by_id=bundle.member_facts_by_id,
        verified_observations=seeds,
        row_identity_digest=bundle.row_identity_digest,
    )


def _evaluate_cells(
    contract: PaperMetricContract,
    metric_ids: Sequence[str],
    bundle: MetricObservationBundle,
) -> tuple[Exp4ObservationCell, ...]:
    observations = {
        (value.table_id, value.metric_id): value
        for value in bundle.verified_observations
    }
    cells: list[Exp4ObservationCell] = []
    for metric_id in metric_ids:
        metric = contract.require_metric(EXP4_TABLE_ID, metric_id)
        current = MetricObservationBundle(
            row_facts=bundle.row_facts,
            member_ids=bundle.member_ids,
            member_facts_by_id=bundle.member_facts_by_id,
            verified_observations=tuple(observations.values()),
            row_identity_digest=bundle.row_identity_digest,
        )
        evaluation = recompute_metric(
            contract,
            EXP4_TABLE_ID,
            metric_id,
            row_kind=metric.row_kind,
            bundle=current,
        )
        cells.append(Exp4ObservationCell.from_evaluation(metric_id, evaluation))
        if evaluation.value is not None and not evaluation.publish_blocked:
            observations[(EXP4_TABLE_ID, metric_id)] = _verified_observation(
                contract,
                metric_id,
                evaluation.value,
                bundle.row_identity_digest,
                evaluation.included_member_ids,
            )
    return tuple(cells)


def _block_cells(
    cells: tuple[Exp4ObservationCell, ...], reason: str
) -> tuple[Exp4ObservationCell, ...]:
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


def _verified_observation(
    contract: PaperMetricContract,
    metric_id: str,
    value: Decimal | int,
    row_identity_digest: str,
    source_member_ids: tuple[str, ...],
) -> VerifiedMetricObservation:
    metric = contract.require_metric(EXP4_TABLE_ID, metric_id)
    return VerifiedMetricObservation(
        observation_id=f"{row_identity_digest}:{metric_id}",
        table_id=EXP4_TABLE_ID,
        metric_id=metric_id,
        row_kind=metric.row_kind,
        value=value,
        value_domain=metric.value_domain,
        source_member_ids=source_member_ids,
        evidence_verified=True,
        row_identity_digest=row_identity_digest,
    )


def _group_member_facts(
    group: tuple[Exp4ModeInput, ...],
) -> dict[str, Mapping[str, object]]:
    members: dict[str, Mapping[str, object]] = {}
    for input_index, value in enumerate(group):
        for root in value.roots:
            members[root.preregistered_root_run_id] = _root_lineage_facts(root)
        for observation in value.observations:
            member_id = f"observation:{input_index}:{value.condition_id}:{observation.observation_id}"
            members[member_id] = dict(observation.facts)
    return members


def _root_lineage_facts(root: Exp4DirectRootFacts) -> Mapping[str, object]:
    return {
        "member_kind": "preregistered_root",
        "preregistered_root_run_id": root.preregistered_root_run_id,
        "final_result_reference_complete": root.final_result_reference_complete,
        "end_to_end_verified_success": root.end_to_end_verified_success,
        "source_bank_roles": root.source_bank_roles,
        "source_bank_entry_ids": root.source_bank_entry_ids,
    }


def _group_ineligibility_reasons(
    group: tuple[Exp4ModeInput, ...],
) -> tuple[str, ...]:
    reasons: list[str] = []
    for value in group:
        reasons.extend(_input_ineligibility_reasons(value))
        for root in value.roots:
            reasons.extend(_root_ineligibility_reasons(root))
    return tuple(dict.fromkeys(reasons))


def _entry_ineligibility_reasons(entry: _RootEntry) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            (*_input_ineligibility_reasons(entry.mode_input), *_root_ineligibility_reasons(entry.root))
        )
    )


def _input_ineligibility_reasons(value: Exp4ModeInput) -> tuple[str, ...]:
    reasons: list[str] = []
    if not value.identity_consistent:
        reasons.append(f"{value.ablation_mode.lower()}_identity_inconsistent")
    if not value.paper_evidence_complete:
        reasons.append(f"{value.ablation_mode.lower()}_evidence_incomplete")
    if not value.infrastructure_valid:
        reasons.append(f"{value.ablation_mode.lower()}_infrastructure_invalid")
    if value.ablation_mode == "FULL" and any(
        _is_forbidden_full_observation(observation) for observation in value.observations
    ):
        reasons.append("full_forbidden_mechanism_hook_evidence")
    return tuple(reasons)


def _root_ineligibility_reasons(root: Exp4DirectRootFacts) -> tuple[str, ...]:
    reasons: list[str] = []
    if not root.identity_consistent:
        reasons.append("root_identity_inconsistent")
    if not root.paper_evidence_complete:
        reasons.append("root_evidence_incomplete")
    if not root.infrastructure_valid:
        reasons.append("root_infrastructure_invalid")
    if root.source_bank_roles != TRACE_SOURCE_BANK_ROLES:
        reasons.append("root_source_bank_roles_incomplete")
    return tuple(reasons)


def _is_forbidden_full_observation(value: Exp4PersistedObservation) -> bool:
    if value.facts.get("member_kind") in _FULL_FORBIDDEN_MEMBER_KINDS:
        return True
    return any(value.facts.get(field) is True for field in _FULL_FORBIDDEN_TRUE_FIELDS)


def _add_complete_pair_resource_facts(
    facts: dict[str, object],
    full: Exp4DirectRootFacts,
    ablation: Exp4DirectRootFacts,
) -> None:
    for target, full_value, ablation_value in (
        (
            "trace_replay_wall_clock_ms",
            full.trace_replay_wall_clock_ms,
            ablation.trace_replay_wall_clock_ms,
        ),
        (
            "trace_attributed_tokens",
            full.trace_attributed_tokens,
            ablation.trace_attributed_tokens,
        ),
        (
            "trace_attributed_cost",
            full.trace_attributed_cost,
            ablation.trace_attributed_cost,
        ),
    ):
        if full_value is not None and ablation_value is not None:
            facts[f"full_{target}"] = full_value
            facts[f"ablation_{target}"] = ablation_value


def _match_key(entry: _RootEntry) -> tuple[str, int, str, int]:
    """按预注册 case/repeat/sample 配对；实际 replacement 消费属于结果事实。"""

    return (
        entry.mode_input.domain,
        entry.mode_input.repeat_id,
        entry.root.case_id,
        entry.root.sample_slot_index,
    )


def _ablation_identity_key(entry: _RootEntry) -> tuple[object, ...]:
    return (*_match_key(entry), entry.mode_input.ablation_mode)


def _entry_sort_key(value: _RootEntry) -> tuple[object, ...]:
    return (
        value.mode_input.domain,
        value.mode_input.repeat_id,
        value.root.case_id,
        value.root.sample_slot_index,
        value.root.replacement_slot_ids,
        _MODE_ORDER[value.mode_input.ablation_mode],
        value.root.preregistered_root_run_id,
    )


def _group_sort_key(value: tuple[str, int, str]) -> tuple[object, ...]:
    return value[0], value[1], _MODE_ORDER[value[2]]


def _bundle(
    *,
    row_facts: Mapping[str, object],
    member_facts: Mapping[str, Mapping[str, object]],
    identity: Mapping[str, object],
) -> MetricObservationBundle:
    return MetricObservationBundle(
        row_facts=row_facts,
        member_ids=tuple(member_facts),
        member_facts_by_id=member_facts,
        row_identity_digest=digest_json({"table_id": EXP4_TABLE_ID, **identity}),
    )


def _validate_contract(contract: PaperMetricContract) -> None:
    if not isinstance(contract, PaperMetricContract):
        raise TypeError("contract must be PaperMetricContract")
    table = contract.require_table(EXP4_TABLE_ID)
    if table.numeric_output_fields != EXP4_NUMERIC_FIELDS:
        raise ValueError("Exp4 numeric output field contract drift")


def _normalize_typed_tuple(instance: object, field_name: str, item_type: type) -> None:
    values = tuple(getattr(instance, field_name))
    if any(not isinstance(value, item_type) for value in values):
        raise TypeError(f"{field_name} must contain {item_type.__name__}")
    object.__setattr__(instance, field_name, values)


def _non_empty(value: object, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be non-empty")


def _nonnegative_int(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")


def _bool(value: object, name: str) -> None:
    if type(value) is not bool:
        raise ValueError(f"{name} must be a bool")


def _nonnegative_number(value: object, name: str) -> None:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a nonnegative number")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a nonnegative number") from exc
    if not number.is_finite() or number < 0:
        raise ValueError(f"{name} must be a nonnegative number")


__all__ = [
    "EXP4_MODES",
    "EXP4_NUMERIC_FIELDS",
    "TRACE_SOURCE_BANK_ROLES",
    "Exp4DirectRootFacts",
    "Exp4ModeInput",
    "Exp4ModeSpecificObservation",
    "Exp4ModeSummaryObservation",
    "Exp4ObservationCell",
    "Exp4PairObservation",
    "Exp4PersistedObservation",
    "Exp4Projection",
    "build_exp4_observations",
]
