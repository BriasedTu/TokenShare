"""Experiment 1 canonical direct rows 的纯指标 observation projector。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Sequence

from tokenshare.experiments.paper_direct_results import PaperDirectRootResult
from tokenshare.experiments.paper_metric_contract import (
    MemberDecision,
    MetricEvaluation,
    MetricObservationBundle,
    PaperMetricContract,
    VerifiedMetricObservation,
    recompute_metric,
)
from tokenshare.experiments.paper_models import PaperDirectRootStatus, digest_json


EXP1_TABLE_ID = "exp1_feasibility"
EXP1_NUMERIC_FIELDS = (
    "preregistered_root_count",
    "final_result_root_count",
    "verified_correct_root_count",
    "completion_rate",
    "end_to_end_verified_success_rate",
    "no_final_failure_count",
    "incorrect_final_failure_count",
    "infra_invalid_failure_count",
    "failure_root_count",
    "actual_end_to_end_wall_clock_ms",
    "actual_provider_latency_ms",
    "actual_total_tokens",
    "actual_cost_estimate_cny",
)


@dataclass(frozen=True, kw_only=True)
class Exp1ActualProviderAttemptFacts:
    """已物化的实际 provider attempt facts；本对象不读取 artifact。"""

    attempt_id: str
    provider_latency_ms: int | Decimal | None
    total_tokens: int | Decimal | None
    cost_estimate_cny: int | Decimal | None
    current_provider_roles: tuple[str, ...] | None

    def __post_init__(self) -> None:
        _non_empty(self.attempt_id, "attempt_id")
        for name in (
            "provider_latency_ms",
            "total_tokens",
            "cost_estimate_cny",
        ):
            value = getattr(self, name)
            if value is not None:
                _nonnegative_number(value, name)
        if self.current_provider_roles is not None:
            roles = tuple(self.current_provider_roles)
            if any(not isinstance(role, str) or not role for role in roles):
                raise ValueError("current_provider_roles must contain non-empty strings")
            if len(set(roles)) != len(roles):
                raise ValueError("duplicate current provider role")
            object.__setattr__(self, "current_provider_roles", roles)


@dataclass(frozen=True, kw_only=True)
class Exp1HydratedDirectRow:
    """绑定 canonical direct row 与已物化 provenance facts 的最小 DTO。"""

    direct_result: PaperDirectRootResult
    root_start_at_ms: int | Decimal | None = None
    root_terminal_at_ms: int | Decimal | None = None
    actual_provider_attempts: tuple[Exp1ActualProviderAttemptFacts, ...] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.direct_result, PaperDirectRootResult):
            raise TypeError("direct_result must be PaperDirectRootResult")
        for name in ("root_start_at_ms", "root_terminal_at_ms"):
            value = getattr(self, name)
            if value is not None:
                _nonnegative_number(value, name)
        if self.actual_provider_attempts is not None:
            attempts = tuple(self.actual_provider_attempts)
            if any(
                not isinstance(value, Exp1ActualProviderAttemptFacts)
                for value in attempts
            ):
                raise TypeError(
                    "actual_provider_attempts must contain typed actual facts"
                )
            ids = tuple(value.attempt_id for value in attempts)
            if len(set(ids)) != len(ids):
                raise ValueError("duplicate actual provider attempt id within root")
            object.__setattr__(self, "actual_provider_attempts", attempts)


@dataclass(frozen=True, kw_only=True)
class Exp1ObservationCell:
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
    ) -> Exp1ObservationCell:
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


@dataclass(frozen=True, kw_only=True)
class Exp1ObservationRow:
    domain: str
    paper_difficulty: str
    topic_family: str | None
    repeat_id: int
    cells: tuple[Exp1ObservationCell, ...]
    paper_eligible: bool = False

    @property
    def metric_ids(self) -> tuple[str, ...]:
        return tuple(cell.metric_id for cell in self.cells)

    def require_cell(self, metric_id: str) -> Exp1ObservationCell:
        matches = tuple(cell for cell in self.cells if cell.metric_id == metric_id)
        if len(matches) != 1:
            raise ValueError(f"unknown Exp1 observation cell: {metric_id}")
        return matches[0]


def build_exp1_observations(
    direct_rows: Sequence[PaperDirectRootResult | Exp1HydratedDirectRow],
    contract: PaperMetricContract,
) -> tuple[Exp1ObservationRow, ...]:
    """按 Exp1 冻结 row scope 投影 contract evaluator 的 13 个 numeric cells。"""

    if not isinstance(contract, PaperMetricContract):
        raise TypeError("contract must be PaperMetricContract")
    table = contract.require_table(EXP1_TABLE_ID)
    if table.numeric_output_fields != EXP1_NUMERIC_FIELDS:
        raise ValueError("Exp1 numeric output field contract drift")
    hydrated = tuple(_hydrate(value) for value in direct_rows)
    root_ids = tuple(
        value.direct_result.preregistered_root_run_id for value in hydrated
    )
    if len(set(root_ids)) != len(root_ids):
        raise ValueError("duplicate Exp1 preregistered root run id")

    groups: dict[tuple[str, str, str | None, int], list[Exp1HydratedDirectRow]] = {}
    for value in hydrated:
        key = _group_key(value.direct_result)
        groups.setdefault(key, []).append(value)

    rows: list[Exp1ObservationRow] = []
    for key in sorted(groups, key=lambda value: (value[0], value[1], value[2] or "", value[3])):
        group = tuple(groups[key])
        bundle = _build_bundle(key, group, ())
        observations: list[VerifiedMetricObservation] = []
        cells: list[Exp1ObservationCell] = []
        for metric_id in EXP1_NUMERIC_FIELDS:
            if observations:
                bundle = _build_bundle(key, group, tuple(observations))
            evaluation = recompute_metric(
                contract,
                EXP1_TABLE_ID,
                metric_id,
                row_kind="condition_summary",
                bundle=bundle,
            )
            cells.append(Exp1ObservationCell.from_evaluation(metric_id, evaluation))
            if evaluation.value is not None and not evaluation.publish_blocked:
                metric = contract.require_metric(EXP1_TABLE_ID, metric_id)
                observations.append(
                    VerifiedMetricObservation(
                        observation_id=f"{bundle.row_identity_digest}:{metric_id}",
                        table_id=EXP1_TABLE_ID,
                        metric_id=metric_id,
                        row_kind=metric.row_kind,
                        value=evaluation.value,
                        value_domain=metric.value_domain,
                        source_member_ids=evaluation.included_member_ids,
                        evidence_verified=True,
                        row_identity_digest=bundle.row_identity_digest,
                    )
                )
        rows.append(
            Exp1ObservationRow(
                domain=key[0],
                paper_difficulty=key[1],
                topic_family=key[2],
                repeat_id=key[3],
                cells=tuple(cells),
                paper_eligible=False,
            )
        )
    return tuple(rows)


def _hydrate(
    value: PaperDirectRootResult | Exp1HydratedDirectRow,
) -> Exp1HydratedDirectRow:
    if isinstance(value, Exp1HydratedDirectRow):
        return value
    if isinstance(value, PaperDirectRootResult):
        return Exp1HydratedDirectRow(direct_result=value)
    raise TypeError("direct_rows must contain canonical or hydrated direct rows")


def _group_key(row: PaperDirectRootResult) -> tuple[str, str, str | None, int]:
    if row.experiment_id != "experiment_1":
        raise ValueError("Exp1 projector only accepts experiment_1")
    if row.evidence_class != "online_real_provider":
        raise ValueError("Exp1 projector only accepts online_real_provider")
    domain = row.condition_axes.get("domain")
    difficulty = row.condition_axes.get("difficulty")
    topic_family = row.condition_axes.get("topic_family")
    _non_empty(domain, "condition_axes.domain")
    _non_empty(difficulty, "condition_axes.difficulty")
    if topic_family is not None:
        _non_empty(topic_family, "condition_axes.topic_family")
    return domain, difficulty, topic_family, row.repeat_id


def _build_bundle(
    key: tuple[str, str, str | None, int],
    rows: tuple[Exp1HydratedDirectRow, ...],
    observations: tuple[VerifiedMetricObservation, ...],
) -> MetricObservationBundle:
    member_facts: dict[str, dict[str, object]] = {}
    infrastructure_invalid = False
    for value in rows:
        row = value.direct_result
        invalid = _publication_invalid(row)
        infrastructure_invalid = infrastructure_invalid or invalid
        failure_class = (
            "infra_invalid"
            if invalid
            else "none"
            if row.end_to_end_verified_success
            else "incorrect_final"
            if row.final_result_reference_complete
            else "no_final"
        )
        root_facts: dict[str, object] = {
            "member_kind": "preregistered_root",
            "final_result_reference_complete": row.final_result_reference_complete,
            "end_to_end_verified_success": row.end_to_end_verified_success,
            "failure_class": failure_class,
            "failure_stage": {
                "infra_invalid": "infrastructure",
                "incorrect_final": "correctness",
                "no_final": "terminal",
            }.get(failure_class, "none"),
            "failure_kind": failure_class,
            "infra_invalid": invalid,
        }
        if value.root_start_at_ms is not None:
            root_facts["root_start_at_ms"] = value.root_start_at_ms
        if value.root_terminal_at_ms is not None:
            root_facts["root_terminal_at_ms"] = value.root_terminal_at_ms
        member_facts[row.preregistered_root_run_id] = root_facts

        if value.actual_provider_attempts is None:
            missing_id = f"missing-actual-provider-attempt-facts:{row.preregistered_root_run_id}"
            member_facts[missing_id] = {"member_kind": "actual_provider_attempt"}
            continue
        for attempt in value.actual_provider_attempts:
            if attempt.attempt_id in member_facts:
                raise ValueError("duplicate Exp1 member id")
            facts: dict[str, object] = {"member_kind": "actual_provider_attempt"}
            for source_name, target_name in (
                ("provider_latency_ms", "provider_latency_ms"),
                ("total_tokens", "total_tokens"),
                ("cost_estimate_cny", "cost_estimate_cny"),
                ("current_provider_roles", "current_provider_roles"),
            ):
                fact = getattr(attempt, source_name)
                if fact is not None:
                    facts[target_name] = fact
            member_facts[attempt.attempt_id] = facts

    digest = digest_json(
        {
            "table_id": EXP1_TABLE_ID,
            "domain": key[0],
            "paper_difficulty": key[1],
            "topic_family": key[2],
            "repeat_id": key[3],
            "root_ids": [value.direct_result.preregistered_root_run_id for value in rows],
        }
    )
    return MetricObservationBundle(
        row_facts={
            "domain": key[0],
            "paper_difficulty": key[1],
            "topic_family": key[2],
            "repeat_id": key[3],
            "infra_invalid": infrastructure_invalid,
        },
        member_ids=tuple(member_facts),
        member_facts_by_id=member_facts,
        verified_observations=observations,
        row_identity_digest=digest,
    )


def _publication_invalid(row: PaperDirectRootResult) -> bool:
    return bool(
        row.ineligibility_reasons
        or not row.identity_consistent
        or not row.infrastructure_valid
        or (
            row.root_status != PaperDirectRootStatus.NOT_STARTED.value
            and not row.paper_evidence_complete
        )
    )


def _non_empty(value: object, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be non-empty")


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
    "EXP1_NUMERIC_FIELDS",
    "Exp1ActualProviderAttemptFacts",
    "Exp1HydratedDirectRow",
    "Exp1ObservationCell",
    "Exp1ObservationRow",
    "build_exp1_observations",
]
