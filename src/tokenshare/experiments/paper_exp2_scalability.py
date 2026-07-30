"""Experiment 2 worker scalability module built on Gate B contracts."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from tokenshare.experiments.paper_experiment_contracts import (
    ExperimentSummaryRows,
    FrozenCaseSelection,
    FrozenCaseSelectionBatch,
    FrozenConditionSelectionBinding,
    PaperExecutionContext,
    canonical_contract_digest,
)
from tokenshare.experiments.paper_models import (
    EXP1_TO_EXP4_DEEPSEEK_MAX_TOKENS,
    EXP1_TO_EXP4_DEEPSEEK_TIMEOUT_SECONDS,
    LEAN_TOPIC_FAMILIES,
    PaperConditionResult,
    PaperExperimentCondition,
    digest_json,
)


EXP2_EXPERIMENT_ID = "exp2_real_ai_scalability"
EXP2_SUITE_VERSION = "paper_v1"
EXP2_CATALOG_VERSION = "v1"
EXP2_FACTOR_CASE_COUNTS_BY_DIFFICULTY = {
    "easy": 167,
    "medium": 167,
    "hard": 166,
}
EXP2_V1_EXPECTED_ROOT_RUNS = 60
EXP2_EXPECTED_ROOT_RUNS = 1_992
EXP2_SPLIT_PROFILE_ID = "factorization.exp2_contiguous_20way.v1"
EXP2_SPLIT_PROFILE_REQUESTED_CHILD_COUNT = 20
MANDATORY_WORKER_LEVELS = (1, 3, 7, 10, 30, 50)
OPTIONAL_WORKER_LEVELS: tuple[int, ...] = ()
EXP2_REPEATS = 2
EXP2_SEED_BASE = 2000

BASELINE_PROVIDER_FAMILY = "deepseek"
BASELINE_PROVIDER_MODEL_ID = "deepseek-v4-pro"
BASELINE_MODEL_ENTRY_ID = "deepseek_v4_pro_exp1_baseline"
BASELINE_PROVIDER_CONFIG_ID = "exp1_baseline_deepseek"
BASELINE_REASONING_PROFILE_ID = "high"
BASELINE_REQUEST_LIMIT_POLICY = {
    "max_tokens": EXP1_TO_EXP4_DEEPSEEK_MAX_TOKENS,
    "timeout_seconds": EXP1_TO_EXP4_DEEPSEEK_TIMEOUT_SECONDS,
    "max_provider_attempts": 1,
    "stream": False,
    "thinking": {"type": "enabled"},
    "reasoning_effort": "high",
}

FACTOR_PAPER_DIFFICULTIES = ("easy", "medium", "hard")
LEAN_PAPER_DIFFICULTIES = ("simple", "medium_lemma_dag", "hard_frontier")
LEAN_CONDITION_DIFFICULTY = {
    "simple": "easy",
    "medium_lemma_dag": "medium",
    "hard_frontier": "hard",
}
LEAN_TOPIC_ALLOCATIONS = {
    "simple": {"pure_logic": 2, "function_set": 2, "induction": 1},
    "medium_lemma_dag": {"pure_logic": 1, "function_set": 2, "induction": 2},
    "hard_frontier": {"pure_logic": 2, "function_set": 1, "induction": 2},
}
LEAN_BATCH_SELECTION_TOPIC_FAMILY_MARKER = "mixed_topic_family"


class Exp2MixedLeanCaseSelection(FrozenCaseSelection):
    """Exp2 Lean batches are mixed-topic selections, not single-topic rows."""

    def __post_init__(self) -> None:
        if self.domain != "lean_proof" or self.topic_family is not None:
            super().__post_init__()
            return
        if self.schema_version != "tokenshare.paper_frozen_case_selection.v1":
            raise ValueError(
                "schema_version must be tokenshare.paper_frozen_case_selection.v1"
            )
        for field_name in (
            "selection_id",
            "experiment_id",
            "suite_version",
            "catalog_version",
            "domain",
            "paper_difficulty",
        ):
            _require_non_empty_string(field_name, getattr(self, field_name))
        if self.experiment_id != EXP2_EXPERIMENT_ID:
            raise ValueError("selection must belong to Experiment 2")
        if self.paper_difficulty not in LEAN_PAPER_DIFFICULTIES:
            raise ValueError("Lean paper_difficulty is not valid for Experiment 2")
        _require_complete_digest("catalog_digest", self.catalog_digest)
        if not isinstance(self.paper_eligible_required, bool):
            raise ValueError("paper_eligible_required must be a bool")
        if self.blocked_reason is not None:
            raise ValueError("Experiment 2 mixed Lean selection cannot be blocked")
        _require_non_negative_int_value(
            "expected_ai_unit_count",
            self.expected_ai_unit_count,
        )
        if self.expected_ai_unit_count < 1:
            raise ValueError(
                "executable selection must declare ordered case ids and AI units"
            )
        normalized_case_ids = _normalize_ordered_case_ids(self.ordered_case_ids)
        if not normalized_case_ids:
            raise ValueError(
                "executable selection must declare ordered case ids and AI units"
            )
        object.__setattr__(self, "ordered_case_ids", normalized_case_ids)
        object.__setattr__(
            self,
            "topic_family_marker",
            LEAN_BATCH_SELECTION_TOPIC_FAMILY_MARKER,
        )
        object.__setattr__(
            self,
            "topic_family_counts",
            _topic_family_counts(normalized_case_ids),
        )

    def _body(self, *, include_digest: bool) -> dict[str, Any]:
        body = super()._body(include_digest=include_digest)
        for field_name in (
            "catalog_source_kind",
            "slice_digest",
            "split_metadata_digest",
            "case_expected_ai_unit_counts",
            "readiness_selection_digest",
        ):
            if hasattr(self, field_name):
                value = getattr(self, field_name)
                body[field_name] = dict(value) if isinstance(value, Mapping) else value
        if self.domain == "lean_proof" and self.topic_family is None:
            body["topic_family_marker"] = getattr(
                self,
                "topic_family_marker",
                LEAN_BATCH_SELECTION_TOPIC_FAMILY_MARKER,
            )
            body["topic_family_counts"] = dict(
                getattr(self, "topic_family_counts", {})
            )
        return body


@dataclass(frozen=True, kw_only=True)
class Exp2FactorizationCaseSelection(FrozenCaseSelection):
    split_profile_id: str = EXP2_SPLIT_PROFILE_ID

    def __post_init__(self) -> None:
        if self.split_profile_id != EXP2_SPLIT_PROFILE_ID:
            raise ValueError("Experiment 2 factorization split profile drift")
        super().__post_init__()

    def _body(self, *, include_digest: bool) -> dict[str, Any]:
        body = {
            **super()._body(include_digest=include_digest),
            "split_profile_id": self.split_profile_id,
        }
        for field_name in (
            "catalog_source_kind",
            "slice_digest",
            "split_metadata_digest",
            "case_expected_ai_unit_counts",
        ):
            if hasattr(self, field_name):
                value = getattr(self, field_name)
                body[field_name] = (
                    dict(value) if isinstance(value, Mapping) else value
                )
        return body


class Experiment2ScalabilityModule:
    def expand_conditions(
        self,
        context: PaperExecutionContext,
    ) -> tuple[PaperExperimentCondition, ...]:
        return expand_exp2_conditions(context)

    def freeze_case_selections(
        self,
        context: PaperExecutionContext,
        conditions: tuple[PaperExperimentCondition, ...],
    ) -> FrozenCaseSelectionBatch:
        return freeze_exp2_case_selections(context, conditions)

    def run_condition(
        self,
        context: PaperExecutionContext,
        condition: PaperExperimentCondition,
        selection: FrozenCaseSelection,
    ) -> PaperConditionResult:
        canonical_condition = _canonical_condition_for(context, condition)
        _validate_canonical_selection(context, canonical_condition, selection)
        return context.execution_callback(
            context=context,
            condition=canonical_condition,
            selection=selection,
            experiment_id=EXP2_EXPERIMENT_ID,
        )

    def summarize(self, evidence: Any) -> ExperimentSummaryRows:
        return summarize_exp2_scalability(evidence)


def expand_exp2_conditions(
    context: PaperExecutionContext,
) -> tuple[PaperExperimentCondition, ...]:
    catalog_digest = _catalog_digest(context)
    endpoint_binding = _validate_approved_endpoint_binding(context)
    conditions: list[PaperExperimentCondition] = []
    for worker_count in MANDATORY_WORKER_LEVELS:
        for repeat_id in range(EXP2_REPEATS):
            conditions.append(
                _condition(
                    domain="factorization",
                    difficulty="hard",
                    paper_difficulty="hard",
                    topic_family=None,
                    worker_count=worker_count,
                    repeat_id=repeat_id,
                    catalog_digest=catalog_digest,
                    endpoint_binding=endpoint_binding,
                )
            )
    return tuple(conditions)


def freeze_exp2_case_selections(
    context: PaperExecutionContext,
    conditions: Sequence[PaperExperimentCondition],
) -> FrozenCaseSelectionBatch:
    bindings: list[FrozenConditionSelectionBinding] = []
    for condition in conditions:
        validate_exp2_condition(context, condition)
        selection = _factorization_selection(context, condition)
        bindings.append(
            FrozenConditionSelectionBinding.from_condition(condition, selection)
        )
    return FrozenCaseSelectionBatch(bindings)


def count_exp2_root_runs(
    conditions: Sequence[PaperExperimentCondition],
    selections: Sequence[FrozenCaseSelection],
) -> int:
    if len(conditions) != len(selections):
        raise ValueError("conditions and selections must have the same length")
    total = 0
    for condition, selection in zip(conditions, selections, strict=True):
        _validate_selection_condition_shape(selection, condition)
        if selection.is_executable:
            total += len(selection.ordered_case_ids)
    return total


def evaluate_exp2_optional_worker_levels(
    context: PaperExecutionContext,
) -> tuple[dict[str, Any], ...]:
    preflight = _mapping(_catalog(context).get("optional_worker_preflight", {}))
    rows: list[dict[str, Any]] = []
    for worker_count in OPTIONAL_WORKER_LEVELS:
        entry = _mapping(preflight.get(str(worker_count), preflight.get(worker_count, {})))
        reasons: list[str] = []
        if _non_negative_int(entry.get("ai_unit_count_available")) < worker_count:
            reasons.append("ai_unit")
        if entry.get("quota_status") != "passed":
            reasons.append("quota")
        if entry.get("real_worker_preflight_status") != "passed":
            reasons.append("real_worker_preflight")
        rows.append(
            {
                "schema_version": "tokenshare.paper_exp2_worker_level_support.v1",
                "experiment_id": EXP2_EXPERIMENT_ID,
                "worker_count": worker_count,
                "status": "supported" if not reasons else "unsupported_worker_level",
                "unsupported_reasons": reasons,
                "provider_calls_made": 0,
            }
        )
    return tuple(rows)


def validate_exp2_condition(
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
) -> PaperExperimentCondition:
    endpoint_binding = _validate_approved_endpoint_binding(context)
    if condition.experiment_id != EXP2_EXPERIMENT_ID:
        raise ValueError("condition must belong to Experiment 2 scalability")
    if condition.worker_count not in MANDATORY_WORKER_LEVELS:
        supported_optional = {
            row["worker_count"]
            for row in evaluate_exp2_optional_worker_levels(context)
            if row["status"] == "supported"
        }
        if condition.worker_count not in supported_optional:
            raise ValueError("unsupported_worker_level")
    if condition.fault_type != "none" or float(condition.fault_rate) != 0.0:
        raise ValueError("Experiment 2 conditions must not inject faults")
    if condition.ablation_mode != "FULL":
        raise ValueError("Experiment 2 conditions must use FULL ablation mode")
    if condition.model_policy != "fixed_entry":
        raise ValueError("Experiment 2 requires fixed_entry model policy")
    if (
        condition.model_entry_id != BASELINE_MODEL_ENTRY_ID
        or condition.provider_config_id != BASELINE_PROVIDER_CONFIG_ID
        or condition.provider_family != BASELINE_PROVIDER_FAMILY
        or condition.provider_model_id != BASELINE_PROVIDER_MODEL_ID
    ):
        raise ValueError("Experiment 2 requires DeepSeek-V4-Pro baseline model identity")
    if condition.reasoning_profile_id != BASELINE_REASONING_PROFILE_ID:
        raise ValueError("Experiment 2 reasoning_profile_id must remain fixed")
    if (
        condition.source_provider_config_digest
        != endpoint_binding["source_provider_config_digest"]
        or condition.model_endpoint_identity_digest
        != endpoint_binding["model_endpoint_identity_digest"]
    ):
        raise ValueError("Experiment 2 condition endpoint binding digest mismatch")
    if condition.domain == "factorization":
        if condition.paper_difficulty not in FACTOR_PAPER_DIFFICULTIES:
            raise ValueError("factorization difficulty must be easy, medium, or hard")
        if condition.topic_family is not None:
            raise ValueError("factorization condition must not declare topic_family")
    else:
        raise ValueError("Experiment 2 formal matrix is factorization hard-only")
    return _canonical_condition_for(
        context,
        condition,
        endpoint_binding=endpoint_binding,
        validate_fields=False,
    )


def _canonical_condition_for(
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
    *,
    endpoint_binding: Mapping[str, Any] | None = None,
    validate_fields: bool = True,
) -> PaperExperimentCondition:
    if validate_fields:
        return validate_exp2_condition(context, condition)
    if condition.repeat_id not in range(EXP2_REPEATS):
        raise ValueError("condition does not match canonical Experiment 2 condition")
    expected_difficulty = str(condition.paper_difficulty)
    canonical = _condition(
        domain=condition.domain,
        difficulty=expected_difficulty,
        paper_difficulty=str(condition.paper_difficulty),
        topic_family=None,
        worker_count=condition.worker_count,
        repeat_id=condition.repeat_id,
        catalog_digest=_catalog_digest(context),
        endpoint_binding=endpoint_binding
        if endpoint_binding is not None
        else _validate_approved_endpoint_binding(context),
    )
    if condition.condition_digest != canonical.condition_digest:
        raise ValueError("condition does not match canonical Experiment 2 condition")
    return canonical


def summarize_exp2_scalability(evidence: Any) -> ExperimentSummaryRows:
    records = _condition_records(evidence)
    repeat_rows = [_summarize_condition(record) for record in records]
    baseline_rows = {
        _baseline_key(row): row
        for row in repeat_rows
        if row["worker_count"] == 1
    }
    scored_rows: list[dict[str, Any]] = []
    for row in repeat_rows:
        row = dict(row)
        wall_clock_ms = row["wall_clock_ms"]
        if wall_clock_ms > 0:
            row["throughput_completed_roots_per_second"] = (
                row["completed_root_count"] / (wall_clock_ms / 1000.0)
            )
        else:
            row["throughput_completed_roots_per_second"] = None
        baseline_row = baseline_rows.get(_baseline_key(row))
        baseline = baseline_row["wall_clock_ms"] if baseline_row is not None else None
        if baseline is None:
            row["speedup"] = None
            row["efficiency"] = None
            row["speedup_applicability"] = "missing_worker_1_baseline"
        elif baseline <= 0:
            row["speedup"] = None
            row["efficiency"] = None
            row["speedup_applicability"] = "zero_baseline_denominator"
        elif wall_clock_ms <= 0:
            row["speedup"] = None
            row["efficiency"] = None
            row["speedup_applicability"] = "zero_worker_wall_clock"
        else:
            row["speedup"] = baseline / wall_clock_ms
            row["efficiency"] = row["speedup"] / row["worker_count"]
            row["speedup_applicability"] = "matched_baseline"
        row["rate_limit_sensitivity"] = (
            "rate_limited"
            if row["rate_limited_429_count"] > 0
            else "not_rate_limited"
        )
        row["matched_baseline_rate_limited_429_count"] = (
            baseline_row["rate_limited_429_count"]
            if baseline_row is not None
            else None
        )
        row["included_in_rate_limit_excluded_view"] = (
            row["rate_limited_429_count"] == 0
            and baseline_row is not None
            and baseline_row["rate_limited_429_count"] == 0
        )
        scored_rows.append(row)
    completed_rows = _aggregate_repeat_rows(scored_rows)
    completed_rows = _apply_formal_matrix_audit(completed_rows)
    return ExperimentSummaryRows(
        experiment_id=EXP2_EXPERIMENT_ID,
        rows=tuple(completed_rows),
    )


def _condition(
    *,
    domain: str,
    difficulty: str,
    paper_difficulty: str,
    topic_family: str | None,
    worker_count: int,
    repeat_id: int,
    catalog_digest: str,
    endpoint_binding: Mapping[str, Any],
) -> PaperExperimentCondition:
    topic_part = f"_{topic_family}" if topic_family else ""
    condition_id = (
        f"exp2_{domain}_{paper_difficulty}{topic_part}"
        f"_w{worker_count}_r{repeat_id}"
    )
    return PaperExperimentCondition(
        experiment_id=EXP2_EXPERIMENT_ID,
        condition_id=condition_id,
        domain=domain,
        difficulty=difficulty,
        paper_difficulty=paper_difficulty,
        topic_family=topic_family,
        worker_count=worker_count,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        provider_config_id=BASELINE_PROVIDER_CONFIG_ID,
        model_entry_id=BASELINE_MODEL_ENTRY_ID,
        provider_family=BASELINE_PROVIDER_FAMILY,
        provider_model_id=BASELINE_PROVIDER_MODEL_ID,
        reasoning_profile_id=BASELINE_REASONING_PROFILE_ID,
        model_cohort_id=endpoint_binding.get("model_cohort_id"),
        model_cohort_digest=endpoint_binding.get("model_cohort_digest"),
        cohort_member_id=endpoint_binding.get("cohort_member_id"),
        source_provider_config_digest=str(
            endpoint_binding["source_provider_config_digest"]
        ),
        model_endpoint_identity_digest=str(
            endpoint_binding["model_endpoint_identity_digest"]
        ),
        repeat_id=repeat_id,
        seed=EXP2_SEED_BASE + repeat_id,
        catalog_digest=catalog_digest,
    )


def _factorization_selection(
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
) -> FrozenCaseSelection:
    available_cases = tuple(
        case
        for case in _catalog_cases(context, domain="factorization")
        if case.get("paper_difficulty", case.get("difficulty"))
        == condition.paper_difficulty
    )
    catalog_version = str(_catalog(context).get("catalog_version") or "v1")
    expected_case_count = _factor_case_count(
        catalog_version,
        str(condition.paper_difficulty),
    )
    cases = (
        available_cases[:expected_case_count]
        if catalog_version == "v1"
        else available_cases
    )
    if len(cases) != expected_case_count:
        raise ValueError(
            "Experiment 2 factorization selection count drift for frozen catalog"
        )
    expected_ai_units = 0
    case_ids: list[str] = []
    for case in cases:
        split_params = _mapping(case.get("split_params"))
        if (
            split_params.get("strategy_id")
            != "factorization.candidate_range_partition.v1"
            or split_params.get("range_policy") != "contiguous"
        ):
            raise ValueError(
                "factorization scaling must use within-root range children"
            )
        case_ids.append(_case_id(case))
        expected_ai_units += EXP2_SPLIT_PROFILE_REQUESTED_CHILD_COUNT
    return _selection(
        context,
        condition,
        selection_id=(
            f"{EXP2_EXPERIMENT_ID}:factorization:{condition.paper_difficulty}"
        ),
        ordered_case_ids=case_ids,
        expected_ai_unit_count=expected_ai_units,
        cases=cases,
        split_profile_id=EXP2_SPLIT_PROFILE_ID,
    )


def _lean_selection(
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
) -> FrozenCaseSelection:
    readiness = _validated_readiness_selection(context)
    cases_by_id = {
        _case_id(case): case
        for case in _catalog_cases(context, domain="lean_proof")
    }
    cases: list[Mapping[str, Any]] = []
    for topic_family, expected_count in LEAN_TOPIC_ALLOCATIONS[
        str(condition.paper_difficulty)
    ].items():
        cell_key = f"{condition.paper_difficulty}/{topic_family}"
        selected_ids = tuple(readiness["selected_case_ids_by_cell"][cell_key])[
            :expected_count
        ]
        if len(selected_ids) != expected_count:
            raise ValueError("Experiment 2 Lean topic slice count drift")
        for case_id in selected_ids:
            case = cases_by_id.get(str(case_id))
            if case is None:
                raise ValueError("Lean readiness selection references unknown catalog case")
            if (
                case.get("paper_difficulty") != condition.paper_difficulty
                or case.get("topic_family") != topic_family
            ):
                raise ValueError("Lean readiness selection metadata drift")
            cases.append(case)
    expected_ai_units = sum(
        _case_expected_ai_unit_count(case) for case in cases
    )
    if len(cases) != 5:
        raise ValueError("Experiment 2 Lean selection must contain 5 roots")
    return _selection(
        context,
        condition,
        selection_id=(
            f"{EXP2_EXPERIMENT_ID}:lean_proof:"
            f"{condition.paper_difficulty}:topic_mixed"
        ),
        ordered_case_ids=[_case_id(case) for case in cases],
        expected_ai_unit_count=expected_ai_units,
        cases=cases,
        selection_topic_family=None,
        mixed_topic_family_counts=LEAN_TOPIC_ALLOCATIONS[
            str(condition.paper_difficulty)
        ],
        readiness_selection_digest=str(readiness["selection_digest"]),
    )


def _selection(
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
    *,
    selection_id: str,
    ordered_case_ids: Sequence[str],
    expected_ai_unit_count: int,
    cases: Sequence[Mapping[str, Any]],
    selection_topic_family: str | None = None,
    mixed_topic_family_counts: Mapping[str, int] | None = None,
    readiness_selection_digest: str | None = None,
    split_profile_id: str | None = None,
) -> FrozenCaseSelection:
    catalog = _catalog(context)
    selection_type = (
        Exp2FactorizationCaseSelection
        if condition.domain == "factorization"
        else Exp2MixedLeanCaseSelection
    )
    selection_kwargs: dict[str, Any] = {}
    if split_profile_id is not None:
        selection_kwargs["split_profile_id"] = split_profile_id
    selection = selection_type(
        selection_id=selection_id,
        experiment_id=EXP2_EXPERIMENT_ID,
        suite_version=str(catalog.get("suite_version") or EXP2_SUITE_VERSION),
        catalog_version=str(catalog.get("catalog_version") or EXP2_CATALOG_VERSION),
        domain=condition.domain,
        paper_difficulty=str(condition.paper_difficulty),
        topic_family=selection_topic_family
        if selection_topic_family is not None
        else condition.topic_family,
        ordered_case_ids=tuple(ordered_case_ids),
        catalog_digest=_catalog_digest(context),
        expected_ai_unit_count=expected_ai_unit_count,
        paper_eligible_required=True,
        **selection_kwargs,
    )
    case_unit_counts = {
        _case_id(case): (
            EXP2_SPLIT_PROFILE_REQUESTED_CHILD_COUNT
            if condition.domain == "factorization"
            else _case_expected_ai_unit_count(case)
        )
        for case in cases
    }
    split_metadata = [
        {
            **_case_split_metadata(case),
            **(
                {"split_profile_id": split_profile_id}
                if split_profile_id is not None
                else {}
            ),
        }
        for case in cases
    ]
    object.__setattr__(selection, "catalog_source_kind", "formal_paper_catalog")
    object.__setattr__(
        selection,
        "slice_digest",
        digest_json(
            {
                "catalog_digest": _catalog_digest(context),
                "ordered_case_ids": list(ordered_case_ids),
                "cases": [dict(case) for case in cases],
            }
        ),
    )
    object.__setattr__(
        selection,
        "split_metadata_digest",
        digest_json(split_metadata),
    )
    object.__setattr__(
        selection,
        "case_expected_ai_unit_counts",
        case_unit_counts,
    )
    if readiness_selection_digest is not None:
        object.__setattr__(
            selection,
            "readiness_selection_digest",
            readiness_selection_digest,
        )
    if condition.domain == "lean_proof":
        object.__setattr__(
            selection,
            "topic_family_counts",
            dict(mixed_topic_family_counts or {}),
        )
    if condition.domain == "factorization":
        object.__setattr__(selection, "split_profile_id", EXP2_SPLIT_PROFILE_ID)
    return selection


def _validate_selection_condition_shape(
    selection: FrozenCaseSelection,
    condition: PaperExperimentCondition,
) -> None:
    if selection.experiment_id != EXP2_EXPERIMENT_ID:
        raise ValueError("selection must belong to Experiment 2")
    if selection.domain != condition.domain:
        raise ValueError("selection domain does not match condition")
    if selection.paper_difficulty != condition.paper_difficulty:
        raise ValueError("selection difficulty does not match condition")
    if (
        condition.domain == "factorization"
        and selection.topic_family != condition.topic_family
    ):
        raise ValueError("selection topic_family does not match condition")
    if condition.domain == "lean_proof":
        if selection.topic_family is not None:
            raise ValueError("Lean Experiment 2 selection must use mixed topic_family")
        if (
            getattr(selection, "topic_family_marker", None)
            != LEAN_BATCH_SELECTION_TOPIC_FAMILY_MARKER
        ):
            raise ValueError("Lean selection topic_family marker is invalid")
        expected_counts = LEAN_TOPIC_ALLOCATIONS[str(condition.paper_difficulty)]
        if dict(getattr(selection, "topic_family_counts", {})) != expected_counts:
            raise ValueError("Lean selection topic_family counts drift")
    if selection.is_blocked:
        raise ValueError("Experiment 2 executable condition cannot use blocked selection")


def _validate_canonical_selection(
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
    selection: FrozenCaseSelection,
) -> None:
    _validate_selection_condition_shape(selection, condition)
    canonical = (
        _factorization_selection(context, condition)
        if condition.domain == "factorization"
        else _lean_selection(context, condition)
    )
    observed = _selection_contract_body(selection)
    expected = _selection_contract_body(canonical)
    if observed != expected:
        raise ValueError("selection does not match canonical frozen selection")


def _selection_contract_body(selection: FrozenCaseSelection) -> dict[str, Any]:
    return selection.to_dict()


def _condition_records(evidence: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(evidence, Mapping):
        records = evidence.get("condition_evidence", ())
    else:
        records = evidence
    if not isinstance(records, (list, tuple)):
        raise ValueError("condition_evidence must be a list or tuple")
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError("condition evidence rows must be mappings")
    return tuple(records)


def _summarize_condition(
    record: Mapping[str, Any],
) -> dict[str, Any]:
    condition = _validated_summary_condition(_mapping(record.get("condition")))
    selection = _mapping(record.get("selection"))
    tasks = _tasks(record.get("tasks"))
    _validate_formal_summary_provenance(record)
    case_unit_counts = _validate_summary_selection(condition, selection, tasks)
    _validate_optional_worker_preflight(
        record,
        condition=condition,
        selection=selection,
    )
    transport_kind = _validated_transport_kind(record, tasks)
    task_audits = tuple(
        _validate_task_and_attempt_evidence(
            condition=condition,
            task=task,
            record_transport=transport_kind,
            expected_ai_unit_count=case_unit_counts[_summary_task_case_id(task)],
        )
        for task in tasks
    )
    observed_worker_ids = {
        worker_id
        for audit in task_audits
        for worker_id in audit["worker_ids"]
    }
    expected_worker_count = min(
        _positive_int(condition.get("worker_count")),
        _positive_int(selection.get("expected_ai_unit_count")),
    )
    if len(observed_worker_ids) != expected_worker_count:
        raise ValueError(
            "worker execution commitment does not match frozen worker/AI-unit inventory"
        )
    task_count = len(tasks)
    completed_root_count = sum(1 for task in tasks if task.get("root_status") == "completed")
    accepted_validity_count = sum(
        1 for task in tasks if task.get("accepted_validity") is True
    )
    wall_clock_ms, batch_timing_status = _condition_wall_clock_ms(record, tasks)
    batch_bounds = _authoritative_batch_bounds(record)
    critical_path_ms = max(
        (
            _task_critical_path_ms(
                task,
                batch_started_at_ms=(
                    batch_bounds[0]
                    if batch_bounds is not None
                    else _task_evidence_origin_ms(task)
                ),
            )
            for task in tasks
        ),
        default=0,
    )
    provider_latency_sum_ms = sum(_task_provider_latency_sum(task) for task in tasks)
    total_tokens = sum(_non_negative_int(task.get("total_tokens")) for task in tasks)
    total_cost = sum(_non_negative_number(task.get("cost_estimate")) for task in tasks)
    rate_limited_429_count = sum(
        _non_negative_int(task.get("rate_limited_429_count")) for task in tasks
    )
    retry_count = sum(_non_negative_int(task.get("retry_count")) for task in tasks)
    paper_eligible_task_count = sum(bool(audit["paper_eligible"]) for audit in task_audits)
    completed_child_proof_count = sum(
        int(audit["completed_ai_unit_count"])
        for audit in task_audits
        if condition.get("domain") == "lean_proof"
    )
    all_tasks_eligible = task_count > 0 and paper_eligible_task_count == task_count
    paper_eligible = (
        all_tasks_eligible
        and record.get("paper_eligible") is True
        and batch_timing_status == "authoritative"
    )
    condition_id = str(condition.get("condition_id") or "")
    domain = str(condition.get("domain") or "")
    paper_difficulty = str(condition.get("paper_difficulty") or condition.get("difficulty") or "")
    topic_family = condition.get("topic_family")
    worker_count = _positive_int(condition.get("worker_count"))
    repeat_id = _non_negative_int(condition.get("repeat_id"))
    child_proof_throughput = (
        completed_child_proof_count / (wall_clock_ms / 1000.0)
        if condition.get("domain") == "lean_proof" and wall_clock_ms > 0
        else None
    )
    return {
        "experiment_id": EXP2_EXPERIMENT_ID,
        "condition_id": condition_id,
        "condition_ids": (condition_id,),
        "domain": domain,
        "paper_difficulty": paper_difficulty,
        "topic_family": topic_family,
        "worker_count": worker_count,
        "repeat_id": repeat_id,
        "repeat_ids": (repeat_id,),
        "selection_digest": str(selection.get("selection_digest") or ""),
        "catalog_version": str(selection.get("catalog_version") or ""),
        "task_batch_id": str(selection.get("selection_id") or ""),
        "ordered_case_ids": tuple(selection.get("ordered_case_ids", ())),
        "topic_family_counts": _topic_family_counts(selection.get("ordered_case_ids", ())),
        "case_count": task_count,
        "root_run_count": task_count,
        "completed_root_count": completed_root_count,
        "completed_child_proof_count": completed_child_proof_count,
        "child_proof_throughput_per_second": child_proof_throughput,
        "completion_rate": _ratio(completed_root_count, task_count),
        "accepted_validity_count": accepted_validity_count,
        "accepted_validity_rate": _ratio(accepted_validity_count, task_count),
        "wall_clock_ms": wall_clock_ms,
        "batch_timing_status": batch_timing_status,
        "critical_path_ms": critical_path_ms,
        "provider_latency_sum_ms": provider_latency_sum_ms,
        "total_tokens": total_tokens,
        "cost_estimate": total_cost,
        "rate_limited_429_count": rate_limited_429_count,
        "retry_count": retry_count,
        "transport_kind": transport_kind,
        "paper_eligible": paper_eligible,
        "paper_eligible_task_count": paper_eligible_task_count,
        "ineligible_task_count": task_count - paper_eligible_task_count,
    }


def _validated_summary_condition(
    condition: Mapping[str, Any],
) -> Mapping[str, Any]:
    expected_keys = set(PaperExperimentCondition.__dataclass_fields__) | {
        "condition_digest"
    }
    if set(condition) != expected_keys:
        raise ValueError("summary condition body is incomplete or contains unknown fields")
    condition_body = {
        field_name: condition[field_name]
        for field_name in PaperExperimentCondition.__dataclass_fields__
    }
    parsed = PaperExperimentCondition(**condition_body)
    if dict(condition) != parsed.to_dict():
        raise ValueError("summary condition digest mismatch")
    if parsed.experiment_id != EXP2_EXPERIMENT_ID:
        raise ValueError("summary condition is not an Experiment 2 condition")
    if parsed.worker_count not in MANDATORY_WORKER_LEVELS + OPTIONAL_WORKER_LEVELS:
        raise ValueError("summary condition has unsupported worker_count")
    if parsed.repeat_id not in range(EXP2_REPEATS):
        raise ValueError("summary condition has unexpected repeat_id")
    if parsed.seed != EXP2_SEED_BASE + parsed.repeat_id:
        raise ValueError("summary condition seed drift")
    expected_id = (
        f"exp2_{parsed.domain}_{parsed.paper_difficulty}"
        f"_w{parsed.worker_count}_r{parsed.repeat_id}"
    )
    if parsed.condition_id != expected_id:
        raise ValueError("summary condition_id drift")
    expected_difficulty = (
        str(parsed.paper_difficulty)
        if parsed.domain == "factorization"
        else LEAN_CONDITION_DIFFICULTY.get(str(parsed.paper_difficulty))
    )
    if parsed.difficulty != expected_difficulty or parsed.topic_family is not None:
        raise ValueError("summary condition difficulty/topic drift")
    if (
        parsed.fault_type != "none"
        or float(parsed.fault_rate) != 0.0
        or parsed.ablation_mode != "FULL"
        or parsed.model_policy != "fixed_entry"
        or parsed.provider_config_id != BASELINE_PROVIDER_CONFIG_ID
        or parsed.model_entry_id != BASELINE_MODEL_ENTRY_ID
        or parsed.provider_family != BASELINE_PROVIDER_FAMILY
        or parsed.provider_model_id != BASELINE_PROVIDER_MODEL_ID
        or parsed.reasoning_profile_id != BASELINE_REASONING_PROFILE_ID
        or parsed.real_transport_required is not True
        or parsed.paper_eligible_required is not True
    ):
        raise ValueError("summary condition fixed controls drift")
    _require_complete_digest("catalog_digest", parsed.catalog_digest)
    _require_complete_digest(
        "source_provider_config_digest",
        parsed.source_provider_config_digest,
    )
    _require_complete_digest(
        "model_endpoint_identity_digest",
        parsed.model_endpoint_identity_digest,
    )
    return parsed.to_dict()


def _validate_formal_summary_provenance(record: Mapping[str, Any]) -> None:
    if record.get("pilot_only") is not False or record.get("formal") is not True:
        raise ValueError("pilot output cannot enter formal Experiment 2 summary")


def _validate_summary_selection(
    condition: Mapping[str, Any],
    selection: Mapping[str, Any],
    tasks: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    ordered_case_ids = _normalize_ordered_case_ids(selection.get("ordered_case_ids"))
    expected_case_count = _factor_case_count(
        str(selection.get("catalog_version")),
        "hard",
    )
    if len(ordered_case_ids) != expected_case_count:
        raise ValueError(
            "Experiment 2 summary selection must contain the frozen hard roots"
        )
    if tuple(_summary_task_case_id(task) for task in tasks) != ordered_case_ids:
        raise ValueError("actual task IDs must match frozen ordered_case_ids")
    if (
        selection.get("experiment_id") != EXP2_EXPERIMENT_ID
        or selection.get("domain") != condition.get("domain")
        or selection.get("paper_difficulty") != condition.get("paper_difficulty")
        or selection.get("topic_family") is not None
        or selection.get("catalog_digest") != condition.get("catalog_digest")
        or selection.get("paper_eligible_required") is not True
        or selection.get("blocked_reason") is not None
        or selection.get("execution_status") != "executable"
        or selection.get("paper_eligible_possible") is not True
    ):
        raise ValueError("summary selection does not match its condition")
    expected_selection_id = (
        f"{EXP2_EXPERIMENT_ID}:factorization:{condition['paper_difficulty']}"
    )
    if selection.get("selection_id") != expected_selection_id:
        raise ValueError("summary selection_id drift")
    generated_fields = {
        "selection_digest",
        "case_selection_digest",
        "execution_status",
        "paper_eligible_possible",
        "provider_calls_made",
    }
    required_commitment_fields = {
        "catalog_source_kind",
        "slice_digest",
        "split_metadata_digest",
        "case_expected_ai_unit_counts",
    }
    if not required_commitment_fields.issubset(selection):
        raise ValueError("summary selection is missing Exp2 slice commitments")
    if selection.get("catalog_source_kind") != "formal_paper_catalog":
        raise ValueError("summary selection catalog source drift")
    _require_complete_digest("slice_digest", selection.get("slice_digest"))
    _require_complete_digest(
        "split_metadata_digest",
        selection.get("split_metadata_digest"),
    )
    raw_case_unit_counts = _mapping(selection.get("case_expected_ai_unit_counts"))
    if set(raw_case_unit_counts) != set(ordered_case_ids):
        raise ValueError("summary selection AI-unit commitment IDs drift")
    case_unit_counts = {
        case_id: _positive_int(raw_case_unit_counts[case_id])
        for case_id in ordered_case_ids
    }
    if sum(case_unit_counts.values()) != _positive_int(
        selection.get("expected_ai_unit_count")
    ):
        raise ValueError("summary selection AI-unit commitment count drift")
    if (
        selection.get("split_profile_id") != EXP2_SPLIT_PROFILE_ID
        or set(case_unit_counts.values())
        != {EXP2_SPLIT_PROFILE_REQUESTED_CHILD_COUNT}
    ):
        raise ValueError("summary selection split profile drift")
    selection_digest_body = {
        field_name: value
        for field_name, value in selection.items()
        if field_name not in generated_fields
    }
    if selection.get("selection_digest") != canonical_contract_digest(
        selection_digest_body
    ):
        raise ValueError("summary selection_digest mismatch")
    return case_unit_counts


def _validate_task_and_attempt_evidence(
    *,
    condition: Mapping[str, Any],
    task: Mapping[str, Any],
    record_transport: str,
    expected_ai_unit_count: int,
) -> dict[str, Any]:
    task_id = _require_non_empty_string("task_id", task.get("task_id"))
    if task.get("condition_id") != condition.get("condition_id"):
        raise ValueError("task condition_id does not match condition evidence")
    if task.get("repeat_id") != condition.get("repeat_id"):
        raise ValueError("task repeat_id does not match condition evidence")
    if (
        task.get("domain") != condition.get("domain")
        or task.get("paper_difficulty") != condition.get("paper_difficulty")
    ):
        raise ValueError("task domain/difficulty identity mismatch")
    if (
        "model_entry_id" in task
        and task.get("model_entry_id") != condition.get("model_entry_id")
    ):
        raise ValueError("task model identity mismatch")
    task_transport = _optional_transport_kind(task.get("transport_kind"))
    if task_transport != record_transport:
        raise ValueError("record/task transport conflict")
    ai_units = _list_of_mappings(task.get("ai_units"))
    if len(ai_units) != expected_ai_unit_count:
        raise ValueError("task AI-unit inventory does not match frozen selection")
    unit_ids = tuple(
        _require_non_empty_string("unit_id", unit.get("unit_id"))
        for unit in ai_units
    )
    if len(set(unit_ids)) != len(unit_ids):
        raise ValueError("duplicate AI unit evidence")
    if condition.get("domain") == "factorization":
        _validate_factorization_parallelism(task, ai_units)
    attempts = _list_of_mappings(task.get("attempts"))
    if not attempts:
        raise ValueError("attempt evidence is required for every task")
    covered_units: set[str] = set()
    attempt_ids: set[str] = set()
    all_attempts_eligible = True
    worker_ids: set[str] = set()
    usage_total_tokens = 0
    usage_total_cost = 0.0
    for attempt in attempts:
        attempt_id = _require_non_empty_string(
            "attempt_id",
            attempt.get("attempt_id"),
        )
        if attempt_id in attempt_ids:
            raise ValueError("duplicate attempt evidence")
        attempt_ids.add(attempt_id)
        unit_id = _require_non_empty_string("unit_id", attempt.get("unit_id"))
        if unit_id not in unit_ids:
            raise ValueError("attempt references an unknown AI unit")
        covered_units.add(unit_id)
        attempt_transport = (
            _optional_transport_kind(attempt.get("transport_kind"))
            if "transport_kind" in attempt
            else task_transport
        )
        if attempt_transport != task_transport:
            raise ValueError("attempt transport conflicts with task transport")
        if attempt_transport != "ai_api":
            all_attempts_eligible = False
        if (
            attempt.get("condition_id") != condition.get("condition_id")
            or attempt.get("repeat_id") != condition.get("repeat_id")
            or attempt.get("task_id") != task_id
        ):
            raise ValueError("attempt task/condition identity mismatch")
        if (
            not _identity_field_matches(
                attempt,
                canonical_field="provider",
                aliases=("provider_family",),
                expected=BASELINE_PROVIDER_FAMILY,
            )
            or not _identity_field_matches(
                attempt,
                canonical_field="model",
                aliases=("provider_model_id",),
                expected=BASELINE_PROVIDER_MODEL_ID,
            )
            or not _identity_field_matches(
                attempt,
                canonical_field="entry_id",
                aliases=("model_entry_id",),
                expected=BASELINE_MODEL_ENTRY_ID,
            )
        ):
            raise ValueError("attempt model identity mismatch")
        optional_identity_fields = {
            "reasoning_profile_id": BASELINE_REASONING_PROFILE_ID,
            "source_provider_config_digest": condition.get(
                "source_provider_config_digest"
            ),
            "model_endpoint_identity_digest": condition.get(
                "model_endpoint_identity_digest"
            ),
        }
        if any(
            field_name in attempt and attempt.get(field_name) != expected
            for field_name, expected in optional_identity_fields.items()
        ):
            raise ValueError("attempt model identity mismatch")
        try:
            worker_id = _require_non_empty_string(
                "worker_id",
                attempt.get("worker_id"),
            )
        except ValueError as exc:
            raise ValueError("worker execution evidence is incomplete") from exc
        worker_ids.add(worker_id)
        if attempt.get("paper_eligible") is not True:
            all_attempts_eligible = False
        usage_total_tokens += _non_negative_int(attempt.get("total_tokens"))
        usage_total_cost += _non_negative_number(attempt.get("cost_estimate"))
    if covered_units != set(unit_ids):
        raise ValueError("attempt evidence must cover every AI unit")
    if task_transport == "ai_api":
        if usage_total_tokens != _non_negative_int(task.get("total_tokens")):
            raise ValueError("attempt usage token total conflicts with task evidence")
        if abs(usage_total_cost - _non_negative_number(task.get("cost_estimate"))) > 1e-9:
            raise ValueError("attempt usage cost conflicts with task evidence")
    return {
        "paper_eligible": (
            task.get("paper_eligible") is True
            and task_transport == "ai_api"
            and all_attempts_eligible
        ),
        "worker_ids": tuple(sorted(worker_ids)),
        "completed_ai_unit_count": (
            len(covered_units) if task.get("root_status") == "completed" else 0
        ),
    }


def _validate_optional_worker_preflight(
    record: Mapping[str, Any],
    *,
    condition: Mapping[str, Any],
    selection: Mapping[str, Any],
) -> None:
    worker_count = _positive_int(condition.get("worker_count"))
    if worker_count not in OPTIONAL_WORKER_LEVELS:
        return
    if _non_negative_int(selection.get("expected_ai_unit_count")) < worker_count:
        raise ValueError(
            "optional worker preflight cannot exceed frozen batch AI-unit inventory"
        )
    manifest = record.get("optional_worker_preflight")
    if not isinstance(manifest, Mapping):
        raise ValueError("optional worker preflight manifest is required")
    if (
        manifest.get("schema_version")
        != "tokenshare.paper_exp2_optional_worker_preflight.v1"
        or manifest.get("worker_count") != worker_count
        or _non_negative_int(manifest.get("ai_unit_count_available")) < worker_count
        or manifest.get("quota_status") != "passed"
        or manifest.get("real_worker_preflight_status") != "passed"
        or manifest.get("status") != "supported"
        or manifest.get("provider_calls_made") != 0
    ):
        raise ValueError("optional worker preflight manifest is not approved")
    digest_body = {
        key: value
        for key, value in manifest.items()
        if key not in {"preflight_digest", "preflight_ref"}
    }
    if manifest.get("preflight_digest") != digest_json(digest_body):
        raise ValueError("optional worker preflight digest mismatch")


def _summary_task_case_id(task: Mapping[str, Any]) -> str:
    value = task.get("case_id", task.get("task_id"))
    return _require_non_empty_string("case_id", value)


def _identity_field_matches(
    body: Mapping[str, Any],
    *,
    canonical_field: str,
    aliases: Sequence[str],
    expected: str,
) -> bool:
    if body.get(canonical_field) != expected:
        return False
    return all(body.get(alias) == expected for alias in aliases if alias in body)


def _validate_factorization_parallelism(
    task: Mapping[str, Any],
    ai_units: Sequence[Mapping[str, Any]],
) -> None:
    root_case_id = _summary_task_case_id(task)
    observed_scopes = {
        str(task[field_name])
        for field_name in ("actual_parallelism_scope", "parallelism_scope")
        if field_name in task
    }
    if observed_scopes != {"within_root_range_children"}:
        raise ValueError("factorization must execute within-root range children")
    if task.get("root_task_id") != root_case_id or len(ai_units) < 2:
        raise ValueError("factorization AI units must belong to the same factorization root")
    ranges: list[tuple[int, int]] = []
    unit_ids: set[str] = set()
    for unit in ai_units:
        unit_id = str(unit.get("unit_id") or "")
        unit_ids.add(unit_id)
        if (
            unit.get("root_task_id") != root_case_id
            or unit.get("unit_kind") != "factorization_range_child"
        ):
            raise ValueError(
                "factorization AI units must belong to the same factorization root"
            )
        if unit.get("plugin_generated") is not True:
            raise ValueError("factorization range children must be plugin generated")
        range_start = _non_negative_int(unit.get("range_start"))
        range_end = _non_negative_int(unit.get("range_end"))
        if range_end < range_start:
            raise ValueError(
                "factorization contiguous range partition has an invalid child range"
            )
        ranges.append((range_start, range_end))
    candidate_start = _non_negative_int(task.get("candidate_start"))
    candidate_end = _non_negative_int(task.get("candidate_end"))
    ordered_ranges = sorted(ranges)
    if (
        candidate_end < candidate_start
        or ordered_ranges[0][0] != candidate_start
        or ordered_ranges[-1][1] != candidate_end
        or any(
            current[1] + 1 != following[0]
            for current, following in zip(
                ordered_ranges,
                ordered_ranges[1:],
                strict=False,
            )
        )
    ):
        raise ValueError("factorization children must form a contiguous range partition")
    merge_gates = _list_of_mappings(task.get("merge_gates"))
    if not merge_gates or not any(
        set(str(item) for item in gate.get("dependencies", ())) == unit_ids
        for gate in merge_gates
    ):
        raise ValueError("factorization merge evidence must depend on all range children")


def _validated_transport_kind(
    record: Mapping[str, Any],
    tasks: Sequence[Mapping[str, Any]],
) -> str:
    record_transport = _optional_transport_kind(record.get("transport_kind"))
    task_transports: list[str] = []
    for task in tasks:
        task_transport = _optional_transport_kind(task.get("transport_kind"))
        if task.get("paper_eligible") is True and task_transport != "ai_api":
            if task_transport:
                raise ValueError(f"{task_transport} transport cannot be paper eligible")
            raise ValueError("missing transport cannot be paper eligible")
        if task_transport:
            task_transports.append(task_transport)
    unique_task_transports = tuple(sorted(set(task_transports)))
    if record_transport and unique_task_transports:
        if unique_task_transports != (record_transport,):
            raise ValueError("record/task transport conflict")
    if record_transport:
        if (
            record.get("paper_eligible") is True
            and record_transport != "ai_api"
        ):
            raise ValueError(f"{record_transport} transport cannot be paper eligible")
        return record_transport
    if len(unique_task_transports) == 1:
        return unique_task_transports[0]
    if unique_task_transports:
        return "mixed"
    return ""


def _optional_transport_kind(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("transport_kind must be a string")
    return value


def _aggregate_repeat_rows(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_aggregate_key(row)].append(row)
    aggregated: list[dict[str, Any]] = []
    for key in sorted(grouped, key=lambda item: tuple(str(part) for part in item)):
        group = sorted(grouped[key], key=lambda row: row["repeat_id"])
        base = dict(group[0])
        condition_ids = tuple(row["condition_id"] for row in group)
        repeat_ids = tuple(row["repeat_id"] for row in group)
        repeat_audit = _repeat_set_audit(group)
        task_count = sum(row["case_count"] for row in group)
        completed = sum(row["completed_root_count"] for row in group)
        completed_child_proofs = sum(
            row["completed_child_proof_count"] for row in group
        )
        accepted = sum(row["accepted_validity_count"] for row in group)
        paper_eligible_tasks = sum(row["paper_eligible_task_count"] for row in group)
        rate_limit_excluded_rows = [
            row for row in group if row["included_in_rate_limit_excluded_view"]
        ]
        base.update(
            {
                "condition_id": condition_ids[0]
                if len(condition_ids) == 1
                else _summary_condition_id(group[0]),
                "condition_ids": condition_ids,
                "repeat_id": repeat_ids[0] if len(repeat_ids) == 1 else None,
                "repeat_ids": repeat_ids,
                "repeat_count": len(set(repeat_ids)),
                "expected_repeat_ids": tuple(range(EXP2_REPEATS)),
                "repeat_set_status": repeat_audit["status"],
                "missing_repeat_ids": repeat_audit["missing_repeat_ids"],
                "duplicate_repeat_ids": repeat_audit["duplicate_repeat_ids"],
                "unexpected_repeat_ids": repeat_audit["unexpected_repeat_ids"],
                "case_count": group[0]["case_count"],
                "root_run_count": task_count,
                "expected_root_run_count": repeat_audit["expected_root_run_count"],
                "completed_root_count": completed,
                "completed_child_proof_count": completed_child_proofs,
                "child_proof_throughput_per_second": _median(
                    _numeric_values(
                        row["child_proof_throughput_per_second"] for row in group
                    )
                ),
                "child_proof_throughput_median_per_second": _median(
                    _numeric_values(
                        row["child_proof_throughput_per_second"] for row in group
                    )
                ),
                "child_proof_throughput_iqr_per_second": _iqr(
                    _numeric_values(
                        row["child_proof_throughput_per_second"] for row in group
                    )
                ),
                "completion_rate": _ratio(completed, task_count),
                "accepted_validity_count": accepted,
                "accepted_validity_rate": _ratio(accepted, task_count),
                "paper_eligible_task_count": paper_eligible_tasks,
                "ineligible_task_count": task_count - paper_eligible_tasks,
                "paper_eligible": all(row["paper_eligible"] for row in group)
                and repeat_audit["status"] == "complete",
                "wall_clock_ms": _median(row["wall_clock_ms"] for row in group),
                "wall_clock_median_ms": _median(
                    row["wall_clock_ms"] for row in group
                ),
                "wall_clock_iqr_ms": _iqr(row["wall_clock_ms"] for row in group),
                "critical_path_ms": _median(
                    row["critical_path_ms"] for row in group
                ),
                "critical_path_median_ms": _median(
                    row["critical_path_ms"] for row in group
                ),
                "critical_path_iqr_ms": _iqr(
                    row["critical_path_ms"] for row in group
                ),
                "provider_latency_sum_ms": sum(
                    row["provider_latency_sum_ms"] for row in group
                ),
                "total_tokens": sum(row["total_tokens"] for row in group),
                "total_tokens_median": _median(row["total_tokens"] for row in group),
                "total_tokens_iqr": _iqr(row["total_tokens"] for row in group),
                "cost_estimate": sum(row["cost_estimate"] for row in group),
                "cost_median": _median(row["cost_estimate"] for row in group),
                "cost_iqr": _iqr(row["cost_estimate"] for row in group),
                "rate_limited_429_count": sum(
                    row["rate_limited_429_count"] for row in group
                ),
                "matched_baseline_rate_limited_429_count": sum(
                    int(row["matched_baseline_rate_limited_429_count"] or 0)
                    for row in group
                ),
                "retry_count": sum(row["retry_count"] for row in group),
                "throughput_completed_roots_per_second": _median(
                    _numeric_values(
                        row["throughput_completed_roots_per_second"] for row in group
                    )
                ),
                "throughput_iqr": _iqr(
                    _numeric_values(
                        row["throughput_completed_roots_per_second"] for row in group
                    )
                ),
                "speedup": _median(_numeric_values(row["speedup"] for row in group)),
                "speedup_median": _median(
                    _numeric_values(row["speedup"] for row in group)
                ),
                "speedup_iqr": _iqr(_numeric_values(row["speedup"] for row in group)),
                "efficiency": _median(
                    _numeric_values(row["efficiency"] for row in group)
                ),
                "efficiency_median": _median(
                    _numeric_values(row["efficiency"] for row in group)
                ),
                "efficiency_iqr": _iqr(
                    _numeric_values(row["efficiency"] for row in group)
                ),
                "rate_limit_excluded_repeat_count": len(
                    {row["repeat_id"] for row in rate_limit_excluded_rows}
                ),
                "rate_limit_excluded_root_run_count": sum(
                    row["case_count"] for row in rate_limit_excluded_rows
                ),
                "rate_limit_excluded_wall_clock_median_ms": _median(
                    row["wall_clock_ms"] for row in rate_limit_excluded_rows
                ),
                "rate_limit_excluded_wall_clock_iqr_ms": _iqr(
                    row["wall_clock_ms"] for row in rate_limit_excluded_rows
                ),
                "rate_limit_excluded_speedup_median": _median(
                    _numeric_values(
                        row["speedup"] for row in rate_limit_excluded_rows
                    )
                ),
                "rate_limit_excluded_speedup_iqr": _iqr(
                    _numeric_values(
                        row["speedup"] for row in rate_limit_excluded_rows
                    )
                ),
                "rate_limit_excluded_efficiency_median": _median(
                    _numeric_values(
                        row["efficiency"] for row in rate_limit_excluded_rows
                    )
                ),
                "rate_limit_excluded_efficiency_iqr": _iqr(
                    _numeric_values(
                        row["efficiency"] for row in rate_limit_excluded_rows
                    )
                ),
                "rate_limit_excluded_throughput_median": _median(
                    _numeric_values(
                        row["throughput_completed_roots_per_second"]
                        for row in rate_limit_excluded_rows
                    )
                ),
                "rate_limit_excluded_throughput_iqr": _iqr(
                    _numeric_values(
                        row["throughput_completed_roots_per_second"]
                        for row in rate_limit_excluded_rows
                    )
                ),
            }
        )
        if base["rate_limited_429_count"] > 0:
            base["rate_limit_sensitivity"] = "rate_limited"
        elif base["matched_baseline_rate_limited_429_count"] > 0:
            base["rate_limit_sensitivity"] = "matched_baseline_rate_limited"
        else:
            base["rate_limit_sensitivity"] = "not_rate_limited"
        base["included_in_rate_limit_excluded_view"] = (
            bool(group)
            and all(row["included_in_rate_limit_excluded_view"] for row in group)
        )
        if all(row["speedup_applicability"] == "matched_baseline" for row in group):
            base["speedup_applicability"] = "matched_baseline"
        else:
            base["speedup_applicability"] = next(
                row["speedup_applicability"]
                for row in group
                if row["speedup_applicability"] != "matched_baseline"
            )
        aggregated.append(base)
    return aggregated


def _apply_formal_matrix_audit(
    rows: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    expected_group_keys = {
        ("factorization", "hard", worker_count)
        for worker_count in MANDATORY_WORKER_LEVELS
    }
    mandatory_rows = [
        row
        for row in rows
        if (
            row.get("domain"),
            row.get("paper_difficulty"),
            row.get("worker_count"),
        )
        in expected_group_keys
    ]
    observed_group_keys = {
        (
            row.get("domain"),
            row.get("paper_difficulty"),
            row.get("worker_count"),
        )
        for row in mandatory_rows
    }
    condition_ids = tuple(
        str(condition_id)
        for row in mandatory_rows
        for condition_id in row.get("condition_ids", ())
    )
    root_run_count = sum(
        _non_negative_int(row.get("root_run_count")) for row in mandatory_rows
    )
    expected_condition_ids = {
        _expected_formal_condition_id(
            domain=domain,
            paper_difficulty=paper_difficulty,
            worker_count=worker_count,
            repeat_id=repeat_id,
        )
        for domain, paper_difficulty, worker_count in expected_group_keys
        for repeat_id in range(EXP2_REPEATS)
    }
    catalog_versions = {
        str(row.get("catalog_version")) for row in mandatory_rows
    }
    catalog_version = (
        next(iter(catalog_versions)) if len(catalog_versions) == 1 else ""
    )
    expected_root_run_count = _expected_root_runs(catalog_version)
    matrix_complete = (
        observed_group_keys == expected_group_keys
        and len(mandatory_rows) == len(expected_group_keys)
        and all(row.get("repeat_set_status") == "complete" for row in mandatory_rows)
        and all(
            row.get("root_run_count")
            == _expected_group_root_runs(
                catalog_version=catalog_version,
                domain=str(row.get("domain")),
                paper_difficulty=str(row.get("paper_difficulty")),
            )
            for row in mandatory_rows
        )
        and len(condition_ids) == len(set(condition_ids))
        and set(condition_ids) == expected_condition_ids
        and root_run_count == expected_root_run_count
    )
    audit = {
        "formal_matrix_status": "complete" if matrix_complete else "incomplete",
        "formal_matrix_group_count": len(observed_group_keys),
        "expected_formal_matrix_group_count": len(expected_group_keys),
        "formal_matrix_condition_count": len(set(condition_ids)),
        "expected_formal_matrix_condition_count": len(expected_condition_ids),
        "formal_matrix_root_run_count": root_run_count,
        "expected_formal_matrix_root_run_count": expected_root_run_count,
    }
    audited_rows: list[dict[str, Any]] = []
    for row in rows:
        audited = {**row, **audit}
        audited["paper_eligible"] = bool(row.get("paper_eligible")) and matrix_complete
        audited_rows.append(audited)
    return audited_rows


def _factor_case_count(catalog_version: str, paper_difficulty: str) -> int:
    if catalog_version == "v1":
        return 5
    if catalog_version == "v2":
        try:
            return EXP2_FACTOR_CASE_COUNTS_BY_DIFFICULTY[paper_difficulty]
        except KeyError as exc:
            raise ValueError("unsupported Factorization paper difficulty") from exc
    raise ValueError("Experiment 2 supports only catalog v1 or v2")


def _expected_group_root_runs(
    *,
    catalog_version: str,
    domain: str,
    paper_difficulty: str,
) -> int:
    per_condition = (
        _factor_case_count(catalog_version, paper_difficulty)
        if domain == "factorization"
        else 5
    )
    return per_condition * EXP2_REPEATS


def _expected_root_runs(catalog_version: str) -> int:
    if catalog_version == "v1":
        return EXP2_V1_EXPECTED_ROOT_RUNS
    if catalog_version == "v2":
        return EXP2_EXPECTED_ROOT_RUNS
    raise ValueError("Experiment 2 supports only catalog v1 or v2")


def _expected_formal_condition_id(
    *,
    domain: str,
    paper_difficulty: str,
    worker_count: int,
    repeat_id: int,
) -> str:
    return (
        f"exp2_{domain}_{paper_difficulty}_w{worker_count}_r{repeat_id}"
    )


def _aggregate_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("domain"),
        row.get("paper_difficulty"),
        row.get("topic_family"),
        row.get("worker_count"),
        row.get("selection_digest"),
        row.get("task_batch_id"),
    )


def _repeat_set_audit(group: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    expected_repeat_ids = tuple(range(EXP2_REPEATS))
    repeat_counts = Counter(row["repeat_id"] for row in group)
    missing_repeat_ids = tuple(
        repeat_id
        for repeat_id in expected_repeat_ids
        if repeat_id not in repeat_counts
    )
    duplicate_repeat_ids = tuple(
        repeat_id
        for repeat_id, count in sorted(repeat_counts.items())
        if count > 1
    )
    unexpected_repeat_ids = tuple(
        repeat_id
        for repeat_id in sorted(repeat_counts)
        if repeat_id not in expected_repeat_ids
    )
    expected_case_count = _positive_int(group[0]["case_count"]) if group else 0
    expected_root_run_count = expected_case_count * EXP2_REPEATS
    root_run_count = sum(_non_negative_int(row["case_count"]) for row in group)
    same_ordered_cases = all(
        tuple(row.get("ordered_case_ids", ()))
        == tuple(group[0].get("ordered_case_ids", ()))
        for row in group
    )
    status = (
        "complete"
        if (
            not missing_repeat_ids
            and not duplicate_repeat_ids
            and not unexpected_repeat_ids
            and root_run_count == expected_root_run_count
            and expected_case_count == 5
            and same_ordered_cases
        )
        else "incomplete_or_duplicate"
    )
    return {
        "status": status,
        "missing_repeat_ids": missing_repeat_ids,
        "duplicate_repeat_ids": duplicate_repeat_ids,
        "unexpected_repeat_ids": unexpected_repeat_ids,
        "expected_root_run_count": expected_root_run_count,
    }


def _summary_condition_id(row: Mapping[str, Any]) -> str:
    topic_part = f"_{row['topic_family']}" if row.get("topic_family") else ""
    return (
        f"exp2_summary_{row['domain']}_{row['paper_difficulty']}"
        f"{topic_part}_w{row['worker_count']}"
    )


def _condition_wall_clock_ms(
    record: Mapping[str, Any],
    tasks: Sequence[Mapping[str, Any]],
) -> tuple[int, str]:
    has_batch_start = "batch_started_at_ms" in record
    has_batch_end = "batch_ended_at_ms" in record
    if has_batch_start != has_batch_end:
        raise ValueError("batch timing evidence must include both start and end")
    if has_batch_start:
        batch_started = _non_negative_int(record.get("batch_started_at_ms"))
        batch_ended = _non_negative_int(record.get("batch_ended_at_ms"))
        if batch_ended < batch_started:
            raise ValueError("batch_ended_at_ms must be >= batch_started_at_ms")
        for task in tasks:
            task_started, task_ended = _time_bounds_ms(task)
            if task_started < batch_started or task_ended > batch_ended:
                raise ValueError("task timestamps fall outside authoritative batch bounds")
            for unit in _list_of_mappings(task.get("ai_units")):
                _require_interval_within_batch(
                    unit,
                    batch_started=batch_started,
                    batch_ended=batch_ended,
                    label="AI-unit",
                )
            for attempt in _list_of_mappings(task.get("attempts")):
                _require_interval_within_batch(
                    attempt,
                    batch_started=batch_started,
                    batch_ended=batch_ended,
                    label="attempt",
                )
            for gate in _list_of_mappings(task.get("merge_gates")):
                _require_interval_within_batch(
                    gate,
                    batch_started=batch_started,
                    batch_ended=batch_ended,
                    label="merge gate",
                )
        return batch_ended - batch_started, "authoritative"
    starts: list[int] = []
    ends: list[int] = []
    for task in tasks:
        starts.append(_non_negative_int(task.get("started_at_ms")))
        ends.append(_non_negative_int(task.get("ended_at_ms")))
    if not starts or not ends:
        return 0, "missing_authoritative_batch_bounds"
    return max(ends) - min(starts), "missing_authoritative_batch_bounds"


def _authoritative_batch_bounds(
    record: Mapping[str, Any],
) -> tuple[int, int] | None:
    if "batch_started_at_ms" not in record or "batch_ended_at_ms" not in record:
        return None
    return (
        _non_negative_int(record.get("batch_started_at_ms")),
        _non_negative_int(record.get("batch_ended_at_ms")),
    )


def _task_evidence_origin_ms(task: Mapping[str, Any]) -> int:
    starts = [_time_bounds_ms(task)[0]]
    for field_name in ("ai_units", "attempts", "merge_gates"):
        starts.extend(
            _time_bounds_ms(value)[0]
            for value in _list_of_mappings(task.get(field_name))
        )
    return min(starts)


def _require_interval_within_batch(
    value: Mapping[str, Any],
    *,
    batch_started: int,
    batch_ended: int,
    label: str,
) -> None:
    started, ended = _time_bounds_ms(value)
    if started < batch_started or ended > batch_ended:
        raise ValueError(f"{label} timestamps fall outside authoritative batch bounds")


def _task_critical_path_ms(
    task: Mapping[str, Any],
    *,
    batch_started_at_ms: int,
) -> int:
    nodes: dict[str, tuple[int, int, tuple[str, ...]]] = {}
    attempts_by_unit: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for attempt in _list_of_mappings(task.get("attempts")):
        attempts_by_unit[str(attempt.get("unit_id") or "")].append(attempt)
    for unit in _list_of_mappings(task.get("ai_units")):
        unit_id = str(unit.get("unit_id") or "")
        if not unit_id:
            raise ValueError("ai unit must declare unit_id")
        started, ended = _time_bounds_ms(unit)
        unit_attempts = attempts_by_unit.get(unit_id, [])
        if not unit_attempts:
            raise ValueError("critical path requires attempt timestamps for every AI unit")
        for attempt in unit_attempts:
            attempt_started, attempt_ended = _time_bounds_ms(attempt)
            if attempt_started < started or attempt_ended > ended:
                raise ValueError("attempt timestamps must be contained by AI-unit timestamps")
        dependencies = tuple(str(item) for item in unit.get("dependencies", ()))
        nodes[unit_id] = (started, ended, dependencies)
    for gate in _list_of_mappings(task.get("merge_gates")):
        gate_id = str(gate.get("gate_id") or "")
        if not gate_id:
            raise ValueError("merge gate must declare gate_id")
        started, ended = _time_bounds_ms(gate)
        dependencies = tuple(str(item) for item in gate.get("dependencies", ()))
        nodes[gate_id] = (started, ended, dependencies)
    if not nodes:
        return _non_negative_int(task.get("ended_at_ms")) - _non_negative_int(
            task.get("started_at_ms")
        )
    cache: dict[str, tuple[int, int]] = {}
    visiting: set[str] = set()

    def visit(node_id: str) -> tuple[int, int]:
        if node_id in cache:
            return cache[node_id]
        if node_id in visiting:
            raise ValueError("dependency graph contains cycle")
        if node_id not in nodes:
            raise ValueError(f"missing dependency node: {node_id}")
        visiting.add(node_id)
        started, ended, dependencies = nodes[node_id]
        if dependencies:
            dependency_paths = [visit(dependency) for dependency in dependencies]
            dependency_ends = [
                nodes[dependency][1]
                for dependency in dependencies
                if dependency in nodes
            ]
            if dependency_ends and started < max(dependency_ends):
                raise ValueError("dependent node starts before dependency ends")
            best_start, _best_duration = max(
                (
                    (path_start, ended - path_start)
                    for path_start, _path_duration in dependency_paths
                ),
                key=lambda item: item[1],
            )
            path_start = best_start
        else:
            if started < batch_started_at_ms:
                raise ValueError("critical path node starts before authoritative batch")
            path_start = batch_started_at_ms
        visiting.remove(node_id)
        cache[node_id] = (path_start, ended - path_start)
        return cache[node_id]

    return max(visit(node_id)[1] for node_id in nodes)


def _task_provider_latency_sum(task: Mapping[str, Any]) -> int:
    units = _list_of_mappings(task.get("ai_units"))
    if not units:
        return _non_negative_int(task.get("provider_latency_ms"))
    return sum(_non_negative_int(unit.get("provider_latency_ms")) for unit in units)


def _time_bounds_ms(value: Mapping[str, Any]) -> tuple[int, int]:
    started = _non_negative_int(value.get("started_at_ms"))
    ended = _non_negative_int(value.get("ended_at_ms"))
    if ended < started:
        raise ValueError("ended_at_ms must be >= started_at_ms")
    return started, ended


def _baseline_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("domain"),
        row.get("paper_difficulty"),
        row.get("topic_family"),
        row.get("repeat_id"),
        row.get("selection_digest"),
    )


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _median(values: Iterable[float | int]) -> float | int | None:
    ordered = sorted(values)
    if not ordered:
        return None
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2


def _iqr(values: Iterable[float | int]) -> float | int | None:
    ordered = sorted(values)
    if len(ordered) < 2:
        return 0 if ordered else None
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        lower = ordered[:midpoint]
        upper = ordered[midpoint + 1 :]
    else:
        lower = ordered[:midpoint]
        upper = ordered[midpoint:]
    q1 = _median(lower)
    q3 = _median(upper)
    if q1 is None or q3 is None:
        return 0
    return q3 - q1


def _numeric_values(values: Iterable[Any]) -> tuple[float | int, ...]:
    return tuple(value for value in values if isinstance(value, (float, int)))


def _topic_family_counts(case_ids: Any) -> dict[str, int]:
    if not isinstance(case_ids, (list, tuple)):
        return {}
    counts: Counter[str] = Counter()
    for case_id in case_ids:
        case_id_string = str(case_id)
        for topic_family in LEAN_TOPIC_FAMILIES:
            if f"_{topic_family}_" in case_id_string:
                counts[topic_family] += 1
                break
    return dict(counts)


def _catalog(context: PaperExecutionContext) -> Mapping[str, Any]:
    if isinstance(context.catalog, Mapping):
        return context.catalog
    fields = (
        "catalog_id",
        "catalog_version",
        "catalog_digest",
        "factorization_cases",
        "lean_cases",
        "lean_lemma_graph_cases",
        "task15_budget_input",
        "lean_task14_readiness",
        "optional_worker_preflight",
    )
    catalog = {
        field_name: getattr(context.catalog, field_name)
        for field_name in fields
        if hasattr(context.catalog, field_name)
    }
    if not catalog:
        raise ValueError("catalog must expose formal paper catalog fields")
    return catalog


def _catalog_cases(
    context: PaperExecutionContext,
    *,
    domain: str,
) -> tuple[Mapping[str, Any], ...]:
    catalog = _catalog(context)
    if domain == "factorization":
        raw_cases = catalog.get("factorization_cases", ())
    else:
        raw_cases = tuple(catalog.get("lean_cases", ())) + tuple(
            catalog.get("lean_lemma_graph_cases", ())
        )
    if not isinstance(raw_cases, (list, tuple)):
        raise ValueError("formal paper catalog cases must be a list or tuple")
    cases = tuple(_mapping(case) for case in raw_cases)
    case_ids = tuple(_case_id(case) for case in cases)
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("formal paper catalog contains duplicate case IDs")
    return cases


def _catalog_digest(context: PaperExecutionContext) -> str:
    catalog = _catalog(context)
    digest = _require_complete_digest("catalog_digest", catalog.get("catalog_digest"))
    if all(
        field_name in catalog
        for field_name in (
            "factorization_cases",
            "lean_cases",
            "lean_lemma_graph_cases",
        )
    ):
        digest_body = {
            "catalog_id": str(catalog.get("catalog_id") or "tokenshare.paper.catalog"),
            "catalog_version": str(
                catalog.get("catalog_version") or EXP2_CATALOG_VERSION
            ),
            "factorization_cases": list(catalog["factorization_cases"]),
            "lean_cases": list(catalog["lean_cases"]),
            "lean_lemma_graph_cases": list(catalog["lean_lemma_graph_cases"]),
        }
        if digest_json(digest_body) != digest:
            raise ValueError("formal paper catalog digest mismatch")
    return digest


def _validated_readiness_selection(
    context: PaperExecutionContext,
) -> Mapping[str, Any]:
    catalog = _catalog(context)
    readiness = catalog.get("task15_budget_input")
    if readiness is None:
        readiness_wrapper = catalog.get("lean_task14_readiness")
        if isinstance(readiness_wrapper, Mapping):
            readiness = readiness_wrapper.get("task15_budget_input")
    readiness = _mapping(readiness)
    if (
        readiness.get("schema_version")
        != "tokenshare.lean_task15_budget_input.v1"
        or readiness.get("target_case_count") != 15
        or readiness.get("selected_case_count") != 135
        or readiness.get("executable_cell_count") != 9
        or readiness.get("blocked_cell_count") != 0
        or readiness.get("provider_calls_made") != 0
        or readiness.get("catalog_digest") != _catalog_digest(context)
    ):
        raise ValueError("Lean readiness selection is not formal/executable")
    selected_by_cell = _mapping(readiness.get("selected_case_ids_by_cell"))
    expected_cells = {
        f"{difficulty}/{topic_family}"
        for difficulty in LEAN_PAPER_DIFFICULTIES
        for topic_family in LEAN_TOPIC_FAMILIES
    }
    if set(selected_by_cell) != expected_cells:
        raise ValueError("Lean readiness selection cell matrix drift")
    normalized_selected: dict[str, list[str]] = {}
    seen_ids: set[str] = set()
    lean_cases_by_id = {
        _case_id(case): case for case in _catalog_cases(context, domain="lean_proof")
    }
    readiness_ai_unit_count = 0
    for cell_key in sorted(expected_cells):
        case_ids = _normalize_ordered_case_ids(selected_by_cell[cell_key])
        if len(case_ids) != 15 or any(case_id in seen_ids for case_id in case_ids):
            raise ValueError("Lean readiness selection must freeze 15 unique cases per cell")
        paper_difficulty, topic_family = cell_key.split("/", 1)
        for case_id in case_ids:
            case = lean_cases_by_id.get(case_id)
            if case is None:
                raise ValueError(
                    "Lean readiness selection references unknown catalog case"
                )
            if (
                case.get("paper_difficulty") != paper_difficulty
                or case.get("topic_family") != topic_family
            ):
                raise ValueError("Lean readiness selection metadata drift")
            readiness_ai_unit_count += _case_expected_ai_unit_count(case)
        seen_ids.update(case_ids)
        normalized_selected[cell_key] = list(case_ids)
    if readiness.get("expected_ai_unit_count") != readiness_ai_unit_count:
        raise ValueError("Lean readiness AI-unit commitment drift")
    semantic_fingerprints = _digest_lists_by_cell(
        readiness.get("semantic_fingerprint_digests_by_cell"),
        expected_cells=expected_cells,
    )
    golden_case_ids = _mapping(readiness.get("golden_case_ids_by_cell"))
    normalized_golden: dict[str, list[str]] = {}
    for cell_key in sorted(expected_cells):
        ids = _normalize_ordered_case_ids(golden_case_ids.get(cell_key))
        if not ids or any(case_id not in normalized_selected[cell_key] for case_id in ids):
            raise ValueError("Lean readiness golden-case provenance drift")
        normalized_golden[cell_key] = list(ids)
    oracle_digests = _mapping(readiness.get("oracle_package_digests"))
    if not oracle_digests or any(
        not _is_complete_digest_value(value) for value in oracle_digests.values()
    ):
        raise ValueError("Lean readiness oracle package digest drift")
    expected_selection_digest = digest_json(
        {
            "schema_version": "tokenshare.lean_task14_selected_cases.v1",
            "catalog_digest": _catalog_digest(context),
            "environment_digest": _require_complete_digest(
                "environment_digest",
                readiness.get("environment_digest"),
            ),
            "oracle_package_digests": dict(oracle_digests),
            "target_case_count": 15,
            "selected_case_ids_by_cell": normalized_selected,
            "semantic_fingerprint_digests_by_cell": semantic_fingerprints,
            "golden_case_ids_by_cell": normalized_golden,
        }
    )
    if (
        readiness.get("selection_digest") != expected_selection_digest
        or readiness.get("catalog_slice_digest") != expected_selection_digest
    ):
        raise ValueError("Lean readiness selection digest mismatch")
    normalized = dict(readiness)
    normalized["selected_case_ids_by_cell"] = normalized_selected
    return normalized


def _digest_lists_by_cell(
    value: Any,
    *,
    expected_cells: set[str],
) -> dict[str, list[str]]:
    by_cell = _mapping(value)
    if set(by_cell) != expected_cells:
        raise ValueError("Lean readiness semantic fingerprint matrix drift")
    normalized: dict[str, list[str]] = {}
    for cell_key in sorted(expected_cells):
        values = by_cell[cell_key]
        if not isinstance(values, (list, tuple)) or len(values) != 15:
            raise ValueError("Lean readiness semantic fingerprint count drift")
        digests = [str(digest) for digest in values]
        if any(not _is_complete_digest_value(digest) for digest in digests):
            raise ValueError("Lean readiness semantic fingerprint digest drift")
        normalized[cell_key] = digests
    return normalized


def _is_complete_digest_value(value: Any) -> bool:
    try:
        _require_complete_digest("digest", value)
    except ValueError:
        return False
    return True


def _case_expected_ai_unit_count(case: Mapping[str, Any]) -> int:
    if "expected_ai_unit_count" in case:
        return _positive_int(case.get("expected_ai_unit_count"))
    if case.get("schema_version") == "tokenshare.paper_factorization_case.v1":
        return _positive_int(_mapping(case.get("split_params")).get("requested_child_count"))
    return _positive_int(case.get("expected_child_count"))


def _case_split_metadata(case: Mapping[str, Any]) -> dict[str, Any]:
    if case.get("schema_version") == "tokenshare.paper_factorization_case.v1":
        return {
            "case_id": _case_id(case),
            "candidate_start": case.get("candidate_start"),
            "candidate_end": case.get("candidate_end"),
            "split_params": dict(_mapping(case.get("split_params"))),
        }
    oracle_proof_ref = case.get("oracle_proof_ref")
    return {
        "case_id": _case_id(case),
        "expected_ai_unit_count": _case_expected_ai_unit_count(case),
        "expected_split_kind": case.get("expected_split_kind"),
        "dependency_edges": list(case.get("dependency_edges", ())),
        "environment_digest": case.get("environment_digest"),
        "oracle_preflight_status": (
            oracle_proof_ref.get("preflight_status")
            if isinstance(oracle_proof_ref, Mapping)
            else None
        ),
    }


def _validate_approved_endpoint_binding(
    context: PaperExecutionContext,
) -> Mapping[str, Any]:
    binding = context.approved_endpoint_binding
    identity = _field(binding, "identity") or binding
    expected_fields = {
        "provider_config_id": BASELINE_PROVIDER_CONFIG_ID,
        "selected_entry_id": BASELINE_MODEL_ENTRY_ID,
        "provider_family": BASELINE_PROVIDER_FAMILY,
        "provider_model_id": BASELINE_PROVIDER_MODEL_ID,
        "reasoning_profile_id": BASELINE_REASONING_PROFILE_ID,
    }
    mismatches: list[str] = []
    for field_name, expected in expected_fields.items():
        actual = _field(identity, field_name)
        if actual != expected:
            mismatches.append(field_name)
    model_entry_id = _field(identity, "model_entry_id")
    if model_entry_id is not None and model_entry_id != BASELINE_MODEL_ENTRY_ID:
        mismatches.append("model_entry_id")
    source_digest = _binding_digest_field(
        identity,
        "source_provider_config_digest",
        mismatches,
    )
    endpoint_digest = _binding_digest_field(
        identity,
        "model_endpoint_identity_digest",
        mismatches,
    )
    if mismatches:
        fields = ", ".join(dict.fromkeys(mismatches))
        raise ValueError(f"approved endpoint binding mismatch: {fields}")

    try:
        context_request_limits = _normalized_request_limit_policy(context.request_limits)
    except ValueError as exc:
        raise ValueError(
            "request controls and request limit policy drift from approved "
            "endpoint binding"
        ) from exc

    binding_controls = _field(binding, "request_controls")
    if binding_controls is not None:
        try:
            normalized_binding_controls = _normalized_request_limit_policy(
                _required_mapping(binding_controls, "approved binding request_controls")
            )
        except ValueError as exc:
            raise ValueError("approved endpoint binding mismatch: request_controls") from exc
        if normalized_binding_controls != context_request_limits:
            raise ValueError(
                "request controls and request limit policy drift from approved "
                "endpoint binding"
            )

    binding_request_limits = _field(binding, "request_limits")
    if binding_request_limits is not None:
        try:
            normalized_binding_limits = _normalized_request_limit_policy(
                _required_mapping(binding_request_limits, "approved binding request_limits")
            )
        except ValueError as exc:
            raise ValueError("approved request limit policy mismatch") from exc
        if normalized_binding_limits != context_request_limits:
            raise ValueError("approved request limit policy mismatch")

    effective_reasoning = _field(identity, "effective_reasoning_controls")
    if effective_reasoning is not None:
        controls = _required_mapping(
            effective_reasoning,
            "approved endpoint effective_reasoning_controls",
        )
        if (
            set(controls) != {"thinking", "reasoning_effort"}
            or any(
                field_name not in {"thinking", "reasoning_effort"}
                or value
                != {
                    "thinking": BASELINE_REQUEST_LIMIT_POLICY["thinking"],
                    "reasoning_effort": BASELINE_REQUEST_LIMIT_POLICY[
                        "reasoning_effort"
                    ],
                }[field_name]
                for field_name, value in controls.items()
            )
        ):
            raise ValueError("approved endpoint binding mismatch: request_controls")

    if context_request_limits != BASELINE_REQUEST_LIMIT_POLICY:
        raise ValueError(
            "request controls and request limit policy drift from approved "
            "endpoint binding"
        )

    return {
        "provider_config_id": BASELINE_PROVIDER_CONFIG_ID,
        "selected_entry_id": BASELINE_MODEL_ENTRY_ID,
        "model_entry_id": BASELINE_MODEL_ENTRY_ID,
        "provider_family": BASELINE_PROVIDER_FAMILY,
        "provider_model_id": BASELINE_PROVIDER_MODEL_ID,
        "reasoning_profile_id": BASELINE_REASONING_PROFILE_ID,
        "model_cohort_id": _field(identity, "model_cohort_id"),
        "model_cohort_digest": _field(identity, "model_cohort_digest"),
        "cohort_member_id": _field(identity, "cohort_member_id"),
        "source_provider_config_digest": source_digest,
        "model_endpoint_identity_digest": endpoint_digest,
        "request_controls": context_request_limits,
        "request_limits": context_request_limits,
    }


def _binding_digest_field(
    binding: Any,
    field_name: str,
    mismatches: list[str],
) -> str:
    try:
        return _require_complete_digest(field_name, _field(binding, field_name))
    except ValueError:
        mismatches.append(field_name)
        return ""


def _normalized_request_limit_policy(limits: Mapping[str, Any]) -> dict[str, Any]:
    if set(limits) != set(BASELINE_REQUEST_LIMIT_POLICY):
        raise ValueError("request limit policy contains missing or unknown fields")
    normalized = dict(limits)
    for field_name in ("max_tokens", "timeout_seconds", "max_provider_attempts"):
        value = limits.get(field_name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{field_name} must be a positive integer")
    if (
        limits.get("stream") is not False
        or limits.get("thinking") != {"type": "enabled"}
        or limits.get("reasoning_effort") != "high"
    ):
        raise ValueError("request controls have invalid types")
    return {
        field_name: normalized[field_name]
        for field_name in BASELINE_REQUEST_LIMIT_POLICY
    }


def _field(value: Any, field_name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(field_name)
    return getattr(value, field_name, None)


def _required_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    return value


def _require_complete_digest(field_name: str, value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("sha256:")
        or len(value) != len("sha256:") + 64
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{field_name} must be a complete sha256 digest")
    return value


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("expected mapping")
    return value


def _require_non_empty_string(field_name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _require_non_negative_int_value(field_name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _normalize_ordered_case_ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("ordered_case_ids must be a list or tuple")
    normalized: list[str] = []
    for case_id in value:
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError("case_id values must be non-empty strings")
        normalized.append(case_id)
    if len(set(normalized)) != len(normalized):
        raise ValueError("duplicate case_id in ordered_case_ids")
    return tuple(normalized)


def _case_list(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("case slice must be a list or tuple")
    cases: list[Mapping[str, Any]] = []
    for item in value:
        cases.append(_mapping(item))
    return tuple(cases)


def _case_id(case: Mapping[str, Any]) -> str:
    case_id = case.get("case_id")
    if not isinstance(case_id, str) or not case_id:
        raise ValueError("case_id must be a non-empty string")
    return case_id


def _tasks(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("tasks must be a list or tuple")
    return tuple(_mapping(task) for task in value)


def _list_of_mappings(value: Any) -> tuple[Mapping[str, Any], ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("expected list or tuple")
    return tuple(_mapping(item) for item in value)


def _positive_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("expected positive integer")
    return value


def _non_negative_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("expected non-negative integer")
    return value


def _non_negative_number(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool) or not isinstance(value, (float, int)) or value < 0:
        raise ValueError("expected non-negative number")
    return float(value)


__all__ = [
    "BASELINE_MODEL_ENTRY_ID",
    "BASELINE_PROVIDER_FAMILY",
    "BASELINE_PROVIDER_MODEL_ID",
    "EXP2_EXPERIMENT_ID",
    "EXP2_EXPECTED_ROOT_RUNS",
    "Experiment2ScalabilityModule",
    "evaluate_exp2_optional_worker_levels",
    "count_exp2_root_runs",
    "expand_exp2_conditions",
    "freeze_exp2_case_selections",
    "summarize_exp2_scalability",
]
