"""Experiment 2 worker scalability module built on Gate B contracts."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from tokenshare.experiments.paper_experiment_contracts import (
    ExperimentSummaryRows,
    FrozenCaseSelection,
    PaperExecutionContext,
)
from tokenshare.experiments.paper_models import (
    LEAN_TOPIC_FAMILIES,
    PaperConditionResult,
    PaperExperimentCondition,
)


EXP2_EXPERIMENT_ID = "exp2_real_ai_worker_scalability"
EXP2_SUITE_VERSION = "paper_v1"
EXP2_CATALOG_VERSION = "v1"
MANDATORY_WORKER_LEVELS = (1, 3, 10, 30)
OPTIONAL_WORKER_LEVELS = (100, 300)
EXP2_REPEATS = 5
EXP2_SEED_BASE = 2000

BASELINE_PROVIDER_FAMILY = "siliconflow"
BASELINE_PROVIDER_MODEL_ID = "zai-org/GLM-5.2"
BASELINE_MODEL_ENTRY_ID = "glm_5_2_exp1_baseline"
BASELINE_PROVIDER_CONFIG_ID = "exp1_baseline_siliconflow"
BASELINE_REASONING_PROFILE_ID = "temperature_0_enable_thinking_false"
BASELINE_REQUEST_CONTROLS = {"temperature": 0.0, "enable_thinking": False}

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
LEAN_BATCH_SELECTION_TOPIC_FAMILY_MARKER = "mixed_2_2_1"


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
    ) -> tuple[FrozenCaseSelection, ...]:
        return freeze_exp2_case_selections(context, conditions)

    def run_condition(
        self,
        context: PaperExecutionContext,
        condition: PaperExperimentCondition,
        selection: FrozenCaseSelection,
    ) -> PaperConditionResult:
        validate_exp2_condition(context, condition)
        _validate_canonical_selection(context, condition, selection)
        return context.execution_callback(
            context=context,
            condition=condition,
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
    for domain in ("factorization", "lean_proof"):
        if domain == "factorization":
            for paper_difficulty in FACTOR_PAPER_DIFFICULTIES:
                for worker_count in MANDATORY_WORKER_LEVELS:
                    for repeat_id in range(EXP2_REPEATS):
                        conditions.append(
                            _condition(
                                domain=domain,
                                difficulty=paper_difficulty,
                                paper_difficulty=paper_difficulty,
                                topic_family=None,
                                worker_count=worker_count,
                                repeat_id=repeat_id,
                                catalog_digest=catalog_digest,
                                endpoint_binding=endpoint_binding,
                            )
                        )
            continue
        for paper_difficulty in LEAN_PAPER_DIFFICULTIES:
            for worker_count in MANDATORY_WORKER_LEVELS:
                for repeat_id in range(EXP2_REPEATS):
                    conditions.append(
                        _condition(
                            domain=domain,
                            difficulty=LEAN_CONDITION_DIFFICULTY[paper_difficulty],
                            paper_difficulty=paper_difficulty,
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
) -> tuple[FrozenCaseSelection, ...]:
    selections: list[FrozenCaseSelection] = []
    for condition in conditions:
        validate_exp2_condition(context, condition)
        if condition.domain == "factorization":
            selections.append(_factorization_selection(context, condition))
        else:
            selections.append(_lean_selection(context, condition))
    return tuple(selections)


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
    preflight = _exp2_catalog(context).get("optional_worker_preflight", {})
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
) -> None:
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
        raise ValueError("Experiment 2 requires GLM-5.2 baseline model identity")
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
        return
    if condition.domain != "lean_proof":
        raise ValueError("domain must be factorization or lean_proof")
    if condition.paper_difficulty not in LEAN_PAPER_DIFFICULTIES:
        raise ValueError("Lean paper_difficulty is not valid for Experiment 2")
    if condition.topic_family is not None:
        raise ValueError("Lean Experiment 2 condition must use one mixed 5-task batch")


def summarize_exp2_scalability(evidence: Any) -> ExperimentSummaryRows:
    records = _condition_records(evidence)
    repeat_rows = [_summarize_condition(record) for record in records]
    baseline_wall_clock = {
        _baseline_key(row): row["wall_clock_ms"]
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
        baseline = baseline_wall_clock.get(_baseline_key(row))
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
        row["included_in_rate_limit_excluded_view"] = (
            row["rate_limited_429_count"] == 0
        )
        scored_rows.append(row)
    completed_rows = _aggregate_repeat_rows(scored_rows)
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
    cases_by_difficulty = _mapping(_exp2_catalog(context).get("factorization"))
    cases = _case_list(cases_by_difficulty.get(condition.paper_difficulty))
    if len(cases) != 5:
        raise ValueError("Experiment 2 factorization selection must contain 5 roots")
    expected_ai_units = 0
    case_ids: list[str] = []
    for case in cases:
        if case.get("parallelism_scope") != "within_root_range_children":
            raise ValueError(
                "factorization scaling must use within-root range children"
            )
        case_ids.append(_case_id(case))
        expected_ai_units += _positive_int(case.get("expected_ai_unit_count"))
    return _selection(
        context,
        condition,
        selection_id=(
            f"{EXP2_EXPERIMENT_ID}:factorization:{condition.paper_difficulty}"
        ),
        ordered_case_ids=case_ids,
        expected_ai_unit_count=expected_ai_units,
    )


def _lean_selection(
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
) -> FrozenCaseSelection:
    lean_catalog = _mapping(_exp2_catalog(context).get("lean_proof"))
    by_topic = _mapping(lean_catalog.get(condition.paper_difficulty))
    cases: list[Mapping[str, Any]] = []
    for topic_family, expected_count in LEAN_TOPIC_ALLOCATIONS[
        str(condition.paper_difficulty)
    ].items():
        topic_cases = _case_list(by_topic.get(topic_family))
        if len(topic_cases) != expected_count:
            raise ValueError("Experiment 2 Lean topic slice count drift")
        cases.extend(topic_cases)
    expected_ai_units = sum(
        _positive_int(case.get("expected_ai_unit_count")) for case in cases
    )
    if len(cases) != 5:
        raise ValueError("Experiment 2 Lean selection must contain 5 roots")
    return _selection(
        context,
        condition,
        selection_id=(
            f"{EXP2_EXPERIMENT_ID}:lean_proof:"
            f"{condition.paper_difficulty}:topic_mixed_2_2_1"
        ),
        ordered_case_ids=[_case_id(case) for case in cases],
        expected_ai_unit_count=expected_ai_units,
        selection_topic_family=None,
        mixed_topic_family_counts=LEAN_TOPIC_ALLOCATIONS[
            str(condition.paper_difficulty)
        ],
    )


def _selection(
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
    *,
    selection_id: str,
    ordered_case_ids: Sequence[str],
    expected_ai_unit_count: int,
    selection_topic_family: str | None = None,
    mixed_topic_family_counts: Mapping[str, int] | None = None,
) -> FrozenCaseSelection:
    catalog = _catalog(context)
    selection_type = (
        Exp2MixedLeanCaseSelection
        if condition.domain == "lean_proof" and mixed_topic_family_counts is not None
        else FrozenCaseSelection
    )
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
    )
    if isinstance(selection, Exp2MixedLeanCaseSelection):
        object.__setattr__(
            selection,
            "topic_family_counts",
            dict(mixed_topic_family_counts or {}),
        )
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
    return {
        "selection_id": selection.selection_id,
        "experiment_id": selection.experiment_id,
        "suite_version": selection.suite_version,
        "catalog_version": selection.catalog_version,
        "domain": selection.domain,
        "paper_difficulty": selection.paper_difficulty,
        "topic_family": selection.topic_family,
        "ordered_case_ids": tuple(selection.ordered_case_ids),
        "catalog_digest": selection.catalog_digest,
        "expected_ai_unit_count": selection.expected_ai_unit_count,
        "paper_eligible_required": selection.paper_eligible_required,
        "blocked_reason": selection.blocked_reason,
        "selection_digest": selection.selection_digest,
        "schema_version": selection.schema_version,
        "topic_family_marker": getattr(selection, "topic_family_marker", None),
        "topic_family_counts": dict(getattr(selection, "topic_family_counts", {})),
    }


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


def _summarize_condition(record: Mapping[str, Any]) -> dict[str, Any]:
    condition = _mapping(record.get("condition"))
    selection = _mapping(record.get("selection"))
    tasks = _tasks(record.get("tasks"))
    transport_kind = _validated_transport_kind(record, tasks)
    task_count = len(tasks)
    completed_root_count = sum(1 for task in tasks if task.get("root_status") == "completed")
    accepted_validity_count = sum(
        1 for task in tasks if task.get("accepted_validity") is True
    )
    wall_clock_ms = _condition_wall_clock_ms(tasks)
    critical_path_ms = max((_task_critical_path_ms(task) for task in tasks), default=0)
    provider_latency_sum_ms = sum(_task_provider_latency_sum(task) for task in tasks)
    total_tokens = sum(_non_negative_int(task.get("total_tokens")) for task in tasks)
    total_cost = sum(_non_negative_number(task.get("cost_estimate")) for task in tasks)
    rate_limited_429_count = sum(
        _non_negative_int(task.get("rate_limited_429_count")) for task in tasks
    )
    retry_count = sum(_non_negative_int(task.get("retry_count")) for task in tasks)
    paper_eligible_task_count = sum(
        1 for task in tasks if task.get("paper_eligible") is True
    )
    all_tasks_eligible = task_count > 0 and paper_eligible_task_count == task_count
    paper_eligible = all_tasks_eligible and record.get("paper_eligible") is not False
    condition_id = str(condition.get("condition_id") or "")
    domain = str(condition.get("domain") or "")
    paper_difficulty = str(condition.get("paper_difficulty") or condition.get("difficulty") or "")
    topic_family = condition.get("topic_family")
    worker_count = _positive_int(condition.get("worker_count"))
    repeat_id = _non_negative_int(condition.get("repeat_id"))
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
        "task_batch_id": str(selection.get("selection_id") or ""),
        "ordered_case_ids": tuple(selection.get("ordered_case_ids", ())),
        "topic_family_counts": _topic_family_counts(selection.get("ordered_case_ids", ())),
        "case_count": task_count,
        "root_run_count": task_count,
        "completed_root_count": completed_root_count,
        "completion_rate": _ratio(completed_root_count, task_count),
        "accepted_validity_count": accepted_validity_count,
        "accepted_validity_rate": _ratio(accepted_validity_count, task_count),
        "wall_clock_ms": wall_clock_ms,
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
        accepted = sum(row["accepted_validity_count"] for row in group)
        paper_eligible_tasks = sum(row["paper_eligible_task_count"] for row in group)
        rate_limit_excluded_rows = [
            row for row in group if row["rate_limited_429_count"] == 0
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
        base["rate_limit_sensitivity"] = (
            "rate_limited"
            if base["rate_limited_429_count"] > 0
            else "not_rate_limited"
        )
        base["included_in_rate_limit_excluded_view"] = (
            base["rate_limited_429_count"] == 0
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


def _condition_wall_clock_ms(tasks: Sequence[Mapping[str, Any]]) -> int:
    starts: list[int] = []
    ends: list[int] = []
    for task in tasks:
        starts.append(_non_negative_int(task.get("started_at_ms")))
        ends.append(_non_negative_int(task.get("ended_at_ms")))
    if not starts or not ends:
        return 0
    return max(ends) - min(starts)


def _task_critical_path_ms(task: Mapping[str, Any]) -> int:
    nodes: dict[str, tuple[int, int, tuple[str, ...]]] = {}
    for unit in _list_of_mappings(task.get("ai_units")):
        unit_id = str(unit.get("unit_id") or "")
        if not unit_id:
            raise ValueError("ai unit must declare unit_id")
        started, ended = _time_bounds_ms(unit)
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
            path_start = started
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
    return _mapping(context.catalog)


def _exp2_catalog(context: PaperExecutionContext) -> Mapping[str, Any]:
    return _mapping(_catalog(context).get("exp2"))


def _catalog_digest(context: PaperExecutionContext) -> str:
    digest = _catalog(context).get("catalog_digest")
    return _require_complete_digest("catalog_digest", digest)


def _validate_approved_endpoint_binding(
    context: PaperExecutionContext,
) -> Mapping[str, Any]:
    binding = _mapping(context.approved_endpoint_binding)
    expected_fields = {
        "provider_config_id": BASELINE_PROVIDER_CONFIG_ID,
        "selected_entry_id": BASELINE_MODEL_ENTRY_ID,
        "model_entry_id": BASELINE_MODEL_ENTRY_ID,
        "provider_family": BASELINE_PROVIDER_FAMILY,
        "provider_model_id": BASELINE_PROVIDER_MODEL_ID,
        "reasoning_profile_id": BASELINE_REASONING_PROFILE_ID,
    }
    mismatches: list[str] = []
    for field_name, expected in expected_fields.items():
        actual = binding.get(field_name)
        if actual != expected:
            mismatches.append(field_name)
    source_digest = _binding_digest_field(
        binding,
        "source_provider_config_digest",
        mismatches,
    )
    endpoint_digest = _binding_digest_field(
        binding,
        "model_endpoint_identity_digest",
        mismatches,
    )
    try:
        binding_controls = _normalized_request_controls(
            _mapping(binding.get("request_controls"))
        )
    except ValueError:
        mismatches.append("request_controls")
        binding_controls = {}
    if binding_controls != BASELINE_REQUEST_CONTROLS:
        mismatches.append("request_controls")
    if mismatches:
        fields = ", ".join(dict.fromkeys(mismatches))
        raise ValueError(f"approved endpoint binding mismatch: {fields}")

    context_controls = _context_request_controls(context.request_limits)
    if context_controls != BASELINE_REQUEST_CONTROLS:
        raise ValueError("request controls drift from approved endpoint binding")

    return {
        **binding,
        "source_provider_config_digest": source_digest,
        "model_endpoint_identity_digest": endpoint_digest,
        "request_controls": binding_controls,
    }


def _binding_digest_field(
    binding: Mapping[str, Any],
    field_name: str,
    mismatches: list[str],
) -> str:
    try:
        return _require_complete_digest(field_name, binding.get(field_name))
    except ValueError:
        mismatches.append(field_name)
        return ""


def _context_request_controls(request_limits: Mapping[str, Any]) -> dict[str, Any]:
    if "request_controls" in request_limits:
        controls = _mapping(request_limits.get("request_controls"))
    else:
        controls = {
            field_name: request_limits.get(field_name)
            for field_name in BASELINE_REQUEST_CONTROLS
        }
    return _normalized_request_controls(controls)


def _normalized_request_controls(controls: Mapping[str, Any]) -> dict[str, Any]:
    temperature = controls.get("temperature")
    enable_thinking = controls.get("enable_thinking")
    if isinstance(temperature, bool) or not isinstance(temperature, (float, int)):
        raise ValueError("temperature request control must be numeric")
    if not isinstance(enable_thinking, bool):
        raise ValueError("enable_thinking request control must be boolean")
    return {"temperature": float(temperature), "enable_thinking": enable_thinking}


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
    "Experiment2ScalabilityModule",
    "evaluate_exp2_optional_worker_levels",
    "count_exp2_root_runs",
    "expand_exp2_conditions",
    "freeze_exp2_case_selections",
    "summarize_exp2_scalability",
]
