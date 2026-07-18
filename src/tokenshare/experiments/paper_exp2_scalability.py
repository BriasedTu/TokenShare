"""Experiment 2 worker scalability module built on Gate B contracts."""

from __future__ import annotations

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
BASELINE_REASONING_PROFILE_ID = "temperature_0_enable_thinking_false"

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
        _validate_selection_matches_condition(selection, condition)
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
                            )
                        )
            continue
        for paper_difficulty in LEAN_PAPER_DIFFICULTIES:
            for topic_family in LEAN_TOPIC_FAMILIES:
                for worker_count in MANDATORY_WORKER_LEVELS:
                    for repeat_id in range(EXP2_REPEATS):
                        conditions.append(
                            _condition(
                                domain=domain,
                                difficulty=LEAN_CONDITION_DIFFICULTY[
                                    paper_difficulty
                                ],
                                paper_difficulty=paper_difficulty,
                                topic_family=topic_family,
                                worker_count=worker_count,
                                repeat_id=repeat_id,
                                catalog_digest=catalog_digest,
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
        _validate_selection_matches_condition(selection, condition)
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
        or condition.provider_family != BASELINE_PROVIDER_FAMILY
        or condition.provider_model_id != BASELINE_PROVIDER_MODEL_ID
    ):
        raise ValueError("Experiment 2 requires GLM-5.2 baseline model identity")
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
    if condition.topic_family not in LEAN_TOPIC_FAMILIES:
        raise ValueError("Lean condition must declare a valid topic_family")


def summarize_exp2_scalability(evidence: Any) -> ExperimentSummaryRows:
    records = _condition_records(evidence)
    rows = [_summarize_condition(record) for record in records]
    baseline_wall_clock = {
        _baseline_key(row): row["wall_clock_ms"]
        for row in rows
        if row["worker_count"] == 1
    }
    completed_rows: list[dict[str, Any]] = []
    for row in rows:
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
        completed_rows.append(row)
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
        model_entry_id=BASELINE_MODEL_ENTRY_ID,
        provider_family=BASELINE_PROVIDER_FAMILY,
        provider_model_id=BASELINE_PROVIDER_MODEL_ID,
        reasoning_profile_id=BASELINE_REASONING_PROFILE_ID,
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
    cases = _case_list(by_topic.get(condition.topic_family))
    expected_count = LEAN_TOPIC_ALLOCATIONS[str(condition.paper_difficulty)][
        str(condition.topic_family)
    ]
    if len(cases) != expected_count:
        raise ValueError("Experiment 2 Lean topic slice count drift")
    expected_ai_units = sum(
        _positive_int(case.get("expected_ai_unit_count"))
        for case in cases
    )
    return _selection(
        context,
        condition,
        selection_id=(
            f"{EXP2_EXPERIMENT_ID}:lean_proof:"
            f"{condition.paper_difficulty}:{condition.topic_family}"
        ),
        ordered_case_ids=[_case_id(case) for case in cases],
        expected_ai_unit_count=expected_ai_units,
    )


def _selection(
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
    *,
    selection_id: str,
    ordered_case_ids: Sequence[str],
    expected_ai_unit_count: int,
) -> FrozenCaseSelection:
    catalog = _catalog(context)
    return FrozenCaseSelection(
        selection_id=selection_id,
        experiment_id=EXP2_EXPERIMENT_ID,
        suite_version=str(catalog.get("suite_version") or EXP2_SUITE_VERSION),
        catalog_version=str(catalog.get("catalog_version") or EXP2_CATALOG_VERSION),
        domain=condition.domain,
        paper_difficulty=str(condition.paper_difficulty),
        topic_family=condition.topic_family,
        ordered_case_ids=tuple(ordered_case_ids),
        catalog_digest=_catalog_digest(context),
        expected_ai_unit_count=expected_ai_unit_count,
        paper_eligible_required=True,
    )


def _validate_selection_matches_condition(
    selection: FrozenCaseSelection,
    condition: PaperExperimentCondition,
) -> None:
    if selection.experiment_id != EXP2_EXPERIMENT_ID:
        raise ValueError("selection must belong to Experiment 2")
    if selection.domain != condition.domain:
        raise ValueError("selection domain does not match condition")
    if selection.paper_difficulty != condition.paper_difficulty:
        raise ValueError("selection difficulty does not match condition")
    if selection.topic_family != condition.topic_family:
        raise ValueError("selection topic_family does not match condition")
    if selection.is_blocked:
        raise ValueError("Experiment 2 executable condition cannot use blocked selection")


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
    task_paper_eligible = any(task.get("paper_eligible") is True for task in tasks)
    paper_eligible = record.get("paper_eligible") is True or task_paper_eligible
    transport_kind = str(
        record.get("transport_kind")
        or next((task.get("transport_kind") for task in tasks if task.get("transport_kind")), "")
    )
    condition_id = str(condition.get("condition_id") or "")
    domain = str(condition.get("domain") or "")
    paper_difficulty = str(condition.get("paper_difficulty") or condition.get("difficulty") or "")
    topic_family = condition.get("topic_family")
    worker_count = _positive_int(condition.get("worker_count"))
    repeat_id = _non_negative_int(condition.get("repeat_id"))
    return {
        "experiment_id": EXP2_EXPERIMENT_ID,
        "condition_id": condition_id,
        "domain": domain,
        "paper_difficulty": paper_difficulty,
        "topic_family": topic_family,
        "worker_count": worker_count,
        "repeat_id": repeat_id,
        "selection_digest": str(selection.get("selection_digest") or ""),
        "case_count": task_count,
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
    }


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
    nodes: dict[str, tuple[int, tuple[str, ...]]] = {}
    for unit in _list_of_mappings(task.get("ai_units")):
        unit_id = str(unit.get("unit_id") or "")
        if not unit_id:
            raise ValueError("ai unit must declare unit_id")
        duration = _time_duration_ms(unit)
        dependencies = tuple(str(item) for item in unit.get("dependencies", ()))
        nodes[unit_id] = (duration, dependencies)
    for gate in _list_of_mappings(task.get("merge_gates")):
        gate_id = str(gate.get("gate_id") or "")
        if not gate_id:
            raise ValueError("merge gate must declare gate_id")
        duration = _time_duration_ms(gate)
        dependencies = tuple(str(item) for item in gate.get("dependencies", ()))
        nodes[gate_id] = (duration, dependencies)
    if not nodes:
        return _non_negative_int(task.get("ended_at_ms")) - _non_negative_int(
            task.get("started_at_ms")
        )
    cache: dict[str, int] = {}
    visiting: set[str] = set()

    def visit(node_id: str) -> int:
        if node_id in cache:
            return cache[node_id]
        if node_id in visiting:
            raise ValueError("dependency graph contains cycle")
        if node_id not in nodes:
            raise ValueError(f"missing dependency node: {node_id}")
        visiting.add(node_id)
        duration, dependencies = nodes[node_id]
        prefix = max((visit(dependency) for dependency in dependencies), default=0)
        visiting.remove(node_id)
        cache[node_id] = prefix + duration
        return cache[node_id]

    return max(visit(node_id) for node_id in nodes)


def _task_provider_latency_sum(task: Mapping[str, Any]) -> int:
    units = _list_of_mappings(task.get("ai_units"))
    if not units:
        return _non_negative_int(task.get("provider_latency_ms"))
    return sum(_non_negative_int(unit.get("provider_latency_ms")) for unit in units)


def _time_duration_ms(value: Mapping[str, Any]) -> int:
    started = _non_negative_int(value.get("started_at_ms"))
    ended = _non_negative_int(value.get("ended_at_ms"))
    if ended < started:
        raise ValueError("ended_at_ms must be >= started_at_ms")
    return ended - started


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


def _catalog(context: PaperExecutionContext) -> Mapping[str, Any]:
    return _mapping(context.catalog)


def _exp2_catalog(context: PaperExecutionContext) -> Mapping[str, Any]:
    return _mapping(_catalog(context).get("exp2"))


def _catalog_digest(context: PaperExecutionContext) -> str:
    digest = _catalog(context).get("catalog_digest")
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise ValueError("catalog_digest must be a sha256 digest")
    return digest


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("expected mapping")
    return value


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
