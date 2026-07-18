"""Experiment 4 protocol-ablation module built on Gate B contracts."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from tokenshare.experiments.paper_ablation import (
    ABLATION_SCOPE,
    PaperAblationMode,
    ablation_profile_for_mode,
)
from tokenshare.experiments.paper_experiment_contracts import (
    ExperimentSummaryRows,
    FrozenCaseSelection,
    PaperExecutionContext,
    canonical_contract_digest,
)
from tokenshare.experiments.paper_models import (
    PaperConditionResult,
    PaperExperimentCondition,
    PaperStatus,
    UNSUPPORTED_PAPER_TRANSPORTS,
)


EXP4_EXPERIMENT_ID = "exp4_real_ai_protocol_ablation"
EXP4_SUITE_VERSION = "paper_v1"
EXP4_CATALOG_VERSION = "v1"
EXP4_REPEATS = 3
EXP4_WORKER_COUNT = 10
EXP4_TASKS_PER_DIFFICULTY = 5
EXP4_ROOT_RUN_COUNT = 540
EXP4_SEED_BASE = 4000

BASELINE_PROVIDER_CONFIG_ID = "exp1_baseline_siliconflow"
BASELINE_MODEL_ENTRY_ID = "glm_5_2_exp1_baseline"
BASELINE_PROVIDER_FAMILY = "siliconflow"
BASELINE_PROVIDER_MODEL_ID = "zai-org/GLM-5.2"
BASELINE_REASONING_PROFILE_ID = "temperature_0_enable_thinking_false"
BASELINE_REQUEST_CONTROLS = {
    "temperature": 0.0,
    "enable_thinking": False,
}

EXP4_MODES = (
    PaperAblationMode.FULL,
    PaperAblationMode.NO_VERIFICATION,
    PaperAblationMode.NO_PARSER_POLICY,
    PaperAblationMode.NO_REQUEUE,
    PaperAblationMode.NO_MERGE_GATE,
    PaperAblationMode.NO_SLOT_INTEGRITY,
)
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


class Exp4MixedLeanCaseSelection(FrozenCaseSelection):
    """Gate B selection carrying one mixed-topic Lean five-task slice."""

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
        if self.experiment_id != EXP4_EXPERIMENT_ID:
            raise ValueError("selection must belong to Experiment 4")
        if self.paper_difficulty not in LEAN_PAPER_DIFFICULTIES:
            raise ValueError("Lean paper_difficulty is not valid for Experiment 4")
        _require_complete_digest("catalog_digest", self.catalog_digest)
        if not isinstance(self.paper_eligible_required, bool):
            raise ValueError("paper_eligible_required must be a bool")
        normalized_case_ids = _normalize_ordered_case_ids(self.ordered_case_ids)
        object.__setattr__(self, "ordered_case_ids", normalized_case_ids)
        normalized_blocked_reason = _normalize_blocked_reason(self.blocked_reason)
        object.__setattr__(self, "blocked_reason", normalized_blocked_reason)
        _require_non_negative_int(
            "expected_ai_unit_count",
            self.expected_ai_unit_count,
        )
        if normalized_blocked_reason is None:
            if not normalized_case_ids or self.expected_ai_unit_count < 1:
                raise ValueError(
                    "executable selection must declare ordered case ids and AI units"
                )
        elif normalized_case_ids or self.expected_ai_unit_count != 0:
            raise ValueError("blocked selection must not declare cases or AI units")
        object.__setattr__(
            self,
            "topic_family_marker",
            LEAN_BATCH_SELECTION_TOPIC_FAMILY_MARKER,
        )

    def _body(self, *, include_digest: bool) -> dict[str, Any]:
        body = super()._body(include_digest=include_digest)
        if self.domain == "lean_proof" and self.topic_family is None:
            body["topic_family_marker"] = LEAN_BATCH_SELECTION_TOPIC_FAMILY_MARKER
        return body


@dataclass(frozen=True, kw_only=True)
class Exp4ModeExecutionConfig:
    condition_id: str
    ablation_mode: str
    output_root: str
    disabled_mechanisms: tuple[str, ...]
    expected_risk_flags: tuple[str, ...]
    scope: str = ABLATION_SCOPE
    requires_provider_attempt_before_ablation: bool = True
    system_default_behavior_changed: bool = False
    schema_version: str = "tokenshare.paper_exp4_mode_execution_config.v1"

    def __post_init__(self) -> None:
        _require_non_empty_string("condition_id", self.condition_id)
        _require_non_empty_string("output_root", self.output_root)
        mode = PaperAblationMode(self.ablation_mode)
        profile = ablation_profile_for_mode(mode)
        if tuple(self.disabled_mechanisms) != profile.disabled_mechanisms:
            raise ValueError("disabled_mechanisms do not match ablation profile")
        if tuple(self.expected_risk_flags) != profile.expected_risk_flags:
            raise ValueError("expected_risk_flags do not match ablation profile")
        if self.scope != ABLATION_SCOPE:
            raise ValueError("ablation scope must be experiment_boundary")
        if self.requires_provider_attempt_before_ablation is not True:
            raise ValueError("ablation still requires a provider attempt")
        if self.system_default_behavior_changed is not False:
            raise ValueError("Experiment 4 must not change default FULL behavior")
        object.__setattr__(
            self,
            "disabled_mechanisms",
            tuple(self.disabled_mechanisms),
        )
        object.__setattr__(
            self,
            "expected_risk_flags",
            tuple(self.expected_risk_flags),
        )

    @property
    def config_digest(self) -> str:
        return canonical_contract_digest(self._body(include_digest=False))

    def to_dict(self) -> dict[str, Any]:
        return self._body(include_digest=True)

    def _body(self, *, include_digest: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "schema_version": self.schema_version,
            "condition_id": self.condition_id,
            "ablation_mode": self.ablation_mode,
            "output_root": self.output_root,
            "disabled_mechanisms": list(self.disabled_mechanisms),
            "expected_risk_flags": list(self.expected_risk_flags),
            "scope": self.scope,
            "requires_provider_attempt_before_ablation": (
                self.requires_provider_attempt_before_ablation
            ),
            "system_default_behavior_changed": self.system_default_behavior_changed,
        }
        if include_digest:
            body["config_digest"] = self.config_digest
        return body


class Experiment4AblationModule:
    def expand_conditions(
        self,
        context: PaperExecutionContext,
    ) -> tuple[PaperExperimentCondition, ...]:
        return expand_exp4_conditions(context)

    def freeze_case_selections(
        self,
        context: PaperExecutionContext,
        conditions: tuple[PaperExperimentCondition, ...],
    ) -> tuple[FrozenCaseSelection, ...]:
        return freeze_exp4_case_selections(context, conditions)

    def run_condition(
        self,
        context: PaperExecutionContext,
        condition: PaperExperimentCondition,
        selection: FrozenCaseSelection,
    ) -> PaperConditionResult:
        return run_exp4_condition(context, condition, selection)

    def summarize(self, evidence: Any) -> ExperimentSummaryRows:
        return summarize_exp4_ablation(evidence)


def expand_exp4_conditions(
    context: PaperExecutionContext,
) -> tuple[PaperExperimentCondition, ...]:
    catalog_digest = _catalog_digest(context)
    endpoint_binding = _validate_approved_endpoint_binding(context)
    conditions: list[PaperExperimentCondition] = []
    for domain in ("factorization", "lean_proof"):
        paper_difficulties = (
            FACTOR_PAPER_DIFFICULTIES
            if domain == "factorization"
            else LEAN_PAPER_DIFFICULTIES
        )
        for paper_difficulty in paper_difficulties:
            difficulty = (
                paper_difficulty
                if domain == "factorization"
                else LEAN_CONDITION_DIFFICULTY[paper_difficulty]
            )
            for mode in EXP4_MODES:
                for repeat_id in range(EXP4_REPEATS):
                    conditions.append(
                        _condition(
                            domain=domain,
                            difficulty=difficulty,
                            paper_difficulty=paper_difficulty,
                            mode=mode,
                            repeat_id=repeat_id,
                            catalog_digest=catalog_digest,
                            endpoint_binding=endpoint_binding,
                        )
                    )
    return tuple(conditions)


def freeze_exp4_case_selections(
    context: PaperExecutionContext,
    conditions: Sequence[PaperExperimentCondition],
) -> tuple[FrozenCaseSelection, ...]:
    selections: list[FrozenCaseSelection] = []
    for condition in conditions:
        canonical_condition = validate_exp4_condition(context, condition)
        if canonical_condition.domain == "factorization":
            selections.append(_factorization_selection(context, canonical_condition))
        else:
            selections.append(_lean_selection(context, canonical_condition))
    return tuple(selections)


def count_exp4_root_runs(
    conditions: Sequence[PaperExperimentCondition],
    selections: Sequence[FrozenCaseSelection],
) -> int:
    if len(conditions) != len(selections):
        raise ValueError("conditions and selections must have the same length")
    total = 0
    for condition, selection in zip(conditions, selections, strict=True):
        _validate_selection_condition_shape(selection, condition)
        if selection.is_executable:
            if len(selection.ordered_case_ids) != EXP4_TASKS_PER_DIFFICULTY:
                raise ValueError("Experiment 4 selections must contain 5 roots")
            total += len(selection.ordered_case_ids)
    return total


def validate_exp4_condition(
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
) -> PaperExperimentCondition:
    endpoint_binding = _validate_approved_endpoint_binding(context)
    if condition.experiment_id != EXP4_EXPERIMENT_ID:
        raise ValueError("condition must belong to Experiment 4 ablation")
    if condition.ablation_mode not in {mode.value for mode in EXP4_MODES}:
        raise ValueError("Experiment 4 condition has an unsupported ablation mode")
    if condition.worker_count != EXP4_WORKER_COUNT:
        raise ValueError("Experiment 4 worker_count must remain fixed at 10")
    if condition.fault_type != "none" or float(condition.fault_rate) != 0.0:
        raise ValueError("Experiment 4 conditions must not inject faults")
    if condition.model_policy != "fixed_entry":
        raise ValueError("Experiment 4 requires fixed_entry model policy")
    if (
        condition.provider_config_id != BASELINE_PROVIDER_CONFIG_ID
        or condition.model_entry_id != BASELINE_MODEL_ENTRY_ID
        or condition.provider_family != BASELINE_PROVIDER_FAMILY
        or condition.provider_model_id != BASELINE_PROVIDER_MODEL_ID
        or condition.reasoning_profile_id != BASELINE_REASONING_PROFILE_ID
    ):
        raise ValueError("Experiment 4 requires GLM-5.2 baseline model identity")
    if (
        condition.source_provider_config_digest
        != endpoint_binding["source_provider_config_digest"]
        or condition.model_endpoint_identity_digest
        != endpoint_binding["model_endpoint_identity_digest"]
    ):
        raise ValueError("Experiment 4 condition endpoint binding digest mismatch")
    if condition.repeat_id not in range(EXP4_REPEATS):
        raise ValueError("Experiment 4 repeat_id must be 0, 1, or 2")
    if condition.topic_family is not None:
        raise ValueError("Experiment 4 condition must use one mixed five-task batch")
    if condition.domain == "factorization":
        if condition.paper_difficulty not in FACTOR_PAPER_DIFFICULTIES:
            raise ValueError("factorization difficulty must be easy, medium, or hard")
        expected_difficulty = str(condition.paper_difficulty)
    else:
        if condition.domain != "lean_proof":
            raise ValueError("domain must be factorization or lean_proof")
        if condition.paper_difficulty not in LEAN_PAPER_DIFFICULTIES:
            raise ValueError("Lean paper_difficulty is not valid for Experiment 4")
        expected_difficulty = LEAN_CONDITION_DIFFICULTY[
            str(condition.paper_difficulty)
        ]
    canonical = _condition(
        domain=condition.domain,
        difficulty=expected_difficulty,
        paper_difficulty=str(condition.paper_difficulty),
        mode=PaperAblationMode(condition.ablation_mode),
        repeat_id=condition.repeat_id,
        catalog_digest=_catalog_digest(context),
        endpoint_binding=endpoint_binding,
    )
    if condition.condition_digest != canonical.condition_digest:
        raise ValueError("condition does not match canonical Experiment 4 condition")
    return canonical


def build_exp4_mode_execution_config(
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
) -> Exp4ModeExecutionConfig:
    canonical_condition = validate_exp4_condition(context, condition)
    profile = ablation_profile_for_mode(canonical_condition.ablation_mode)
    normalized_base = context.output_root.replace("\\", "/").rstrip("/")
    output_root = "/".join(
        (
            normalized_base,
            EXP4_EXPERIMENT_ID,
            canonical_condition.ablation_mode.lower(),
            canonical_condition.condition_id,
        )
    )
    return Exp4ModeExecutionConfig(
        condition_id=canonical_condition.condition_id,
        ablation_mode=canonical_condition.ablation_mode,
        output_root=output_root,
        disabled_mechanisms=profile.disabled_mechanisms,
        expected_risk_flags=profile.expected_risk_flags,
        scope=profile.scope,
        requires_provider_attempt_before_ablation=(
            profile.requires_provider_attempt_before_ablation
        ),
        system_default_behavior_changed=profile.system_default_behavior_changed,
    )


def run_exp4_condition(
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
    selection: FrozenCaseSelection,
) -> PaperConditionResult:
    canonical_condition = validate_exp4_condition(context, condition)
    canonical_selection = (
        _factorization_selection(context, canonical_condition)
        if canonical_condition.domain == "factorization"
        else _lean_selection(context, canonical_condition)
    )
    _validate_selection_condition_shape(selection, canonical_condition)
    if selection.selection_digest != canonical_selection.selection_digest:
        raise ValueError("selection digest does not match canonical Experiment 4 slice")
    if canonical_selection.is_blocked:
        return PaperConditionResult(
            condition_id=canonical_condition.condition_id,
            status=PaperStatus.BLOCKED,
            repeat_count=1,
            task_count=EXP4_TASKS_PER_DIFFICULTY,
            completed_root_count=0,
            failed_root_count=0,
            blocked_root_count=EXP4_TASKS_PER_DIFFICULTY,
            provider_attempt_count=0,
            metrics_ref=None,
        )
    mode_config = build_exp4_mode_execution_config(context, canonical_condition)
    ablation_profile = ablation_profile_for_mode(
        canonical_condition.ablation_mode
    )
    result = context.execution_callback(
        context=context,
        condition=canonical_condition,
        selection=selection,
        experiment_id=EXP4_EXPERIMENT_ID,
        mode_config=mode_config,
        ablation_profile=ablation_profile,
        output_root=mode_config.output_root,
    )
    if not isinstance(result, PaperConditionResult):
        raise ValueError("execution_callback must return PaperConditionResult")
    if result.condition_id != canonical_condition.condition_id:
        raise ValueError("execution_callback returned a different condition_id")
    return result


def summarize_exp4_ablation(evidence: Any) -> ExperimentSummaryRows:
    records = _evidence_records(evidence)
    grouped: dict[
        tuple[str, str, str],
        list[tuple[Mapping[str, Any], tuple[dict[str, Any], ...]]],
    ] = defaultdict(list)
    for record in records:
        domain = _required_string(record, "domain")
        if domain not in {"factorization", "lean_proof"}:
            raise ValueError("evidence domain must be factorization or lean_proof")
        paper_difficulty = _required_string(record, "paper_difficulty")
        _validate_summary_difficulty(domain, paper_difficulty)
        mode = PaperAblationMode(_required_string(record, "ablation_mode")).value
        _require_non_empty_string("condition_id", record.get("condition_id"))
        _require_non_negative_int("repeat_id", record.get("repeat_id"))
        _require_complete_digest("selection_digest", record.get("selection_digest"))
        transport_kind = _required_string(record, "transport_kind")
        if not isinstance(record.get("paper_eligible"), bool):
            raise ValueError("paper_eligible must be a bool")
        if (
            transport_kind in UNSUPPORTED_PAPER_TRANSPORTS
            and record["paper_eligible"] is True
        ):
            raise ValueError(
                f"{transport_kind} transport cannot be paper eligible"
            )
        tasks = _summary_task_results(record.get("task_results"))
        grouped[(domain, paper_difficulty, mode)].append((record, tasks))

    rows = [
        _summarize_exp4_group(key, group_records)
        for key, group_records in sorted(grouped.items())
    ]
    return ExperimentSummaryRows(
        experiment_id=EXP4_EXPERIMENT_ID,
        rows=tuple(rows),
    )


def _summarize_exp4_group(
    key: tuple[str, str, str],
    records: Sequence[
        tuple[Mapping[str, Any], tuple[dict[str, Any], ...]]
    ],
) -> dict[str, Any]:
    domain, paper_difficulty, mode = key
    tasks = [task for _, record_tasks in records for task in record_tasks]
    root_run_count = len(tasks)
    completed_count = sum(task["root_status"] == "completed" for task in tasks)
    accepted_valid_count = sum(task["accepted_validity"] is True for task in tasks)
    exposed_error_count = sum(task["exposed_error_count"] for task in tasks)
    escaped_error_count = sum(task["escaped_error_count"] for task in tasks)
    applicability, escape_rate = _error_escape_rate(
        mode=PaperAblationMode(mode),
        exposed_error_count=exposed_error_count,
        escaped_error_count=escaped_error_count,
    )
    transport_kinds = {
        _required_string(record, "transport_kind")
        for record, _ in records
    }
    return {
        "domain": domain,
        "paper_difficulty": paper_difficulty,
        "topic_family": (
            "not_applicable"
            if domain == "factorization"
            else LEAN_BATCH_SELECTION_TOPIC_FAMILY_MARKER
        ),
        "ablation_mode": mode,
        "repeat_count": len({record["repeat_id"] for record, _ in records}),
        "unique_case_count": len({task["task_id"] for task in tasks}),
        "root_run_count": root_run_count,
        "completed_root_count": completed_count,
        "completion_rate": completed_count / root_run_count,
        "accepted_valid_count": accepted_valid_count,
        "accepted_validity_rate": (
            accepted_valid_count / completed_count
            if completed_count > 0
            else None
        ),
        "wrong_canonical_acceptance_count": _flag_count(
            tasks,
            "wrong_canonical_acceptance",
        ),
        "wrong_canonical_acceptance_rate": _flag_rate(
            tasks,
            "wrong_canonical_acceptance",
        ),
        "raw_only_acceptance_count": _flag_count(
            tasks,
            "raw_only_acceptance",
        ),
        "raw_only_acceptance_rate": _flag_rate(
            tasks,
            "raw_only_acceptance",
        ),
        "stuck_task_count": _flag_count(tasks, "stuck_task"),
        "stuck_task_rate": _flag_rate(tasks, "stuck_task"),
        "premature_merge_count": _flag_count(tasks, "premature_merge"),
        "premature_merge_rate": _flag_rate(tasks, "premature_merge"),
        "slot_mismatch_count": _flag_count(tasks, "slot_mismatch"),
        "slot_mismatch_rate": _flag_rate(tasks, "slot_mismatch"),
        "wall_clock_ms": sum(task["wall_clock_ms"] for task in tasks),
        "total_tokens": sum(task["total_tokens"] for task in tasks),
        "cost": sum(task["cost_estimate"] for task in tasks),
        "exposed_error_count": exposed_error_count,
        "escaped_error_count": escaped_error_count,
        "error_escape_rate": escape_rate,
        "error_escape_applicability": applicability,
        "transport_kind": (
            next(iter(transport_kinds))
            if len(transport_kinds) == 1
            else "mixed"
        ),
        "paper_eligible": all(
            record["paper_eligible"] is True
            for record, _ in records
        ),
    }


def _error_escape_rate(
    *,
    mode: PaperAblationMode,
    exposed_error_count: int,
    escaped_error_count: int,
) -> tuple[str, float | None]:
    if escaped_error_count > exposed_error_count:
        raise ValueError("escaped_error_count cannot exceed exposed_error_count")
    if mode in {PaperAblationMode.FULL, PaperAblationMode.NO_REQUEUE}:
        return "not_applicable", None
    if exposed_error_count == 0:
        return "zero_denominator", None
    return "applicable", escaped_error_count / exposed_error_count


def _summary_task_results(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("task_results must be a non-empty list or tuple")
    normalized: list[dict[str, Any]] = []
    task_ids: set[str] = set()
    allowed_root_statuses = {
        "completed",
        "failed",
        "blocked",
        "timeout",
        "budget_exhausted",
        "ineligible",
    }
    for item in value:
        task = dict(_mapping(item, "task_result"))
        task_id = _required_string(task, "task_id")
        if task_id in task_ids:
            raise ValueError("duplicate task_id in one condition result")
        task_ids.add(task_id)
        root_status = _required_string(task, "root_status")
        if root_status not in allowed_root_statuses:
            raise ValueError("task_result root_status is invalid")
        accepted_validity = task.get("accepted_validity")
        if accepted_validity is not None and not isinstance(
            accepted_validity,
            bool,
        ):
            raise ValueError("accepted_validity must be a bool or null")
        for field_name in (
            "wrong_canonical_acceptance",
            "raw_only_acceptance",
            "stuck_task",
            "premature_merge",
            "slot_mismatch",
        ):
            if not isinstance(task.get(field_name), bool):
                raise ValueError(f"{field_name} must be a bool")
        _require_non_negative_int(
            "exposed_error_count",
            task.get("exposed_error_count"),
        )
        _require_non_negative_int(
            "escaped_error_count",
            task.get("escaped_error_count"),
        )
        if task["escaped_error_count"] > task["exposed_error_count"]:
            raise ValueError("escaped error must be exposed")
        _require_non_negative_int("wall_clock_ms", task.get("wall_clock_ms"))
        _require_non_negative_int("total_tokens", task.get("total_tokens"))
        _require_non_negative_number("cost_estimate", task.get("cost_estimate"))
        normalized.append(task)
    return tuple(normalized)


def _evidence_records(value: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, Mapping):
        value = value.get("condition_results")
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("evidence must contain condition results")
    return tuple(_mapping(record, "condition_result") for record in value)


def _validate_summary_difficulty(domain: str, paper_difficulty: str) -> None:
    allowed = (
        FACTOR_PAPER_DIFFICULTIES
        if domain == "factorization"
        else LEAN_PAPER_DIFFICULTIES
    )
    if paper_difficulty not in allowed:
        raise ValueError("paper_difficulty does not match evidence domain")


def _flag_count(tasks: Sequence[Mapping[str, Any]], field_name: str) -> int:
    return sum(task[field_name] is True for task in tasks)


def _flag_rate(tasks: Sequence[Mapping[str, Any]], field_name: str) -> float:
    return _flag_count(tasks, field_name) / len(tasks)


def _factorization_selection(
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
) -> FrozenCaseSelection:
    exp4_catalog = _exp4_catalog(context)
    by_difficulty = _mapping(exp4_catalog.get("factorization"), "exp4.factorization")
    cases = _case_list(
        by_difficulty.get(condition.paper_difficulty),
        field_name=f"exp4.factorization.{condition.paper_difficulty}",
    )
    if len(cases) != EXP4_TASKS_PER_DIFFICULTY:
        raise ValueError("Experiment 4 factorization selection must contain 5 roots")
    return _selection_from_cases(context, condition, cases)


def _lean_selection(
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
) -> FrozenCaseSelection:
    paper_difficulty = str(condition.paper_difficulty)
    exp4_lean_value = _exp4_catalog(context).get("lean_proof")
    if exp4_lean_value is None:
        return _blocked_lean_selection(
            context,
            condition,
            reason=f"missing_exp4_formal_lean_slice:{paper_difficulty}",
        )
    exp4_lean = _mapping(exp4_lean_value, "exp4.lean_proof")
    exp2_lean_value = _exp2_catalog(context).get("lean_proof")
    if exp2_lean_value is None:
        return _blocked_lean_selection(
            context,
            condition,
            reason=f"missing_exp2_shared_lean_slice:{paper_difficulty}",
        )
    exp2_lean = _mapping(exp2_lean_value, "exp2.lean_proof")
    missing_exp4_reason = _missing_lean_slice_reason(
        exp4_lean,
        paper_difficulty,
        source="exp4_formal",
    )
    if missing_exp4_reason is not None:
        return _blocked_lean_selection(
            context,
            condition,
            reason=missing_exp4_reason,
        )
    missing_exp2_reason = _missing_lean_slice_reason(
        exp2_lean,
        paper_difficulty,
        source="exp2_shared",
    )
    if missing_exp2_reason is not None:
        return _blocked_lean_selection(
            context,
            condition,
            reason=missing_exp2_reason,
        )
    exp4_cases = _lean_slice_cases(exp4_lean, paper_difficulty, source="Exp4")
    exp2_cases = _lean_slice_cases(exp2_lean, paper_difficulty, source="Exp2")
    exp4_signature = tuple(_lean_case_signature(case) for case in exp4_cases)
    exp2_signature = tuple(_lean_case_signature(case) for case in exp2_cases)
    if exp4_signature != exp2_signature:
        raise ValueError(
            "Experiment 4 Lean slice drift from Experiment 2 exact reference"
        )
    return _selection_from_cases(context, condition, exp4_cases)


def _missing_lean_slice_reason(
    lean_catalog: Mapping[str, Any],
    paper_difficulty: str,
    *,
    source: str,
) -> str | None:
    if paper_difficulty not in lean_catalog:
        return f"missing_{source}_lean_slice:{paper_difficulty}"
    by_topic_value = lean_catalog[paper_difficulty]
    if not isinstance(by_topic_value, Mapping):
        raise ValueError(f"{source}.{paper_difficulty} must be a mapping")
    for topic_family in LEAN_TOPIC_ALLOCATIONS[paper_difficulty]:
        if topic_family not in by_topic_value:
            return (
                f"missing_{source}_lean_slice:{paper_difficulty}:"
                f"{topic_family}"
            )
    return None


def _blocked_lean_selection(
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
    *,
    reason: str,
) -> Exp4MixedLeanCaseSelection:
    catalog = _mapping(context.catalog, "catalog")
    return Exp4MixedLeanCaseSelection(
        selection_id=(
            f"{EXP4_EXPERIMENT_ID}:lean_proof:{condition.paper_difficulty}"
        ),
        experiment_id=EXP4_EXPERIMENT_ID,
        suite_version=_non_empty_catalog_value(catalog, "suite_version"),
        catalog_version=_non_empty_catalog_value(catalog, "catalog_version"),
        domain="lean_proof",
        paper_difficulty=str(condition.paper_difficulty),
        topic_family=None,
        ordered_case_ids=(),
        catalog_digest=_catalog_digest(context),
        expected_ai_unit_count=0,
        paper_eligible_required=True,
        blocked_reason=reason,
    )


def _lean_slice_cases(
    lean_catalog: Mapping[str, Any],
    paper_difficulty: str,
    *,
    source: str,
) -> tuple[Mapping[str, Any], ...]:
    by_topic = _mapping(
        lean_catalog.get(paper_difficulty),
        f"{source}.{paper_difficulty}",
    )
    cases: list[Mapping[str, Any]] = []
    for topic_family, expected_count in LEAN_TOPIC_ALLOCATIONS[
        paper_difficulty
    ].items():
        topic_cases = _case_list(
            by_topic.get(topic_family),
            field_name=f"{source}.{paper_difficulty}.{topic_family}",
        )
        if len(topic_cases) != expected_count:
            raise ValueError(
                f"{source} Lean {paper_difficulty}/{topic_family} slice must "
                f"contain {expected_count} roots"
            )
        cases.extend(topic_cases)
    return tuple(cases)


def _selection_from_cases(
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
    cases: Sequence[Mapping[str, Any]],
) -> FrozenCaseSelection:
    case_ids = tuple(_case_id(case) for case in cases)
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("Experiment 4 selection contains duplicate case IDs")
    expected_ai_unit_count = sum(
        _positive_int(case.get("expected_ai_unit_count"))
        for case in cases
    )
    catalog = _mapping(context.catalog, "catalog")
    selection_class = (
        Exp4MixedLeanCaseSelection
        if condition.domain == "lean_proof"
        else FrozenCaseSelection
    )
    return selection_class(
        selection_id=(
            f"{EXP4_EXPERIMENT_ID}:{condition.domain}:"
            f"{condition.paper_difficulty}"
        ),
        experiment_id=EXP4_EXPERIMENT_ID,
        suite_version=_non_empty_catalog_value(catalog, "suite_version"),
        catalog_version=_non_empty_catalog_value(catalog, "catalog_version"),
        domain=condition.domain,
        paper_difficulty=str(condition.paper_difficulty),
        topic_family=None,
        ordered_case_ids=case_ids,
        catalog_digest=_catalog_digest(context),
        expected_ai_unit_count=expected_ai_unit_count,
        paper_eligible_required=True,
    )


def _validate_selection_condition_shape(
    selection: FrozenCaseSelection,
    condition: PaperExperimentCondition,
) -> None:
    if selection.experiment_id != EXP4_EXPERIMENT_ID:
        raise ValueError("selection must belong to Experiment 4")
    if selection.domain != condition.domain:
        raise ValueError("selection domain does not match condition")
    if selection.paper_difficulty != condition.paper_difficulty:
        raise ValueError("selection paper_difficulty does not match condition")
    if selection.catalog_digest != condition.catalog_digest:
        raise ValueError("selection catalog digest does not match condition")


def _exp4_catalog(context: PaperExecutionContext) -> Mapping[str, Any]:
    return _mapping(
        _mapping(context.catalog, "catalog").get("exp4"),
        "catalog.exp4",
    )


def _exp2_catalog(context: PaperExecutionContext) -> Mapping[str, Any]:
    return _mapping(
        _mapping(context.catalog, "catalog").get("exp2"),
        "catalog.exp2",
    )


def _case_list(value: Any, *, field_name: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a list or tuple")
    cases: list[Mapping[str, Any]] = []
    for case in value:
        cases.append(_mapping(case, f"{field_name} case"))
    return tuple(cases)


def _case_id(case: Mapping[str, Any]) -> str:
    value = case.get("case_id")
    _require_non_empty_string("case_id", value)
    return str(value)


def _lean_case_signature(case: Mapping[str, Any]) -> tuple[str, int]:
    return (
        _case_id(case),
        _positive_int(case.get("expected_ai_unit_count")),
    )


def _positive_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("expected_ai_unit_count must be an integer >= 1")
    return value


def _non_empty_catalog_value(catalog: Mapping[str, Any], field_name: str) -> str:
    value = catalog.get(field_name)
    _require_non_empty_string(field_name, value)
    return str(value)


def _condition(
    *,
    domain: str,
    difficulty: str,
    paper_difficulty: str,
    mode: PaperAblationMode,
    repeat_id: int,
    catalog_digest: str,
    endpoint_binding: Mapping[str, Any],
) -> PaperExperimentCondition:
    return PaperExperimentCondition(
        experiment_id=EXP4_EXPERIMENT_ID,
        condition_id=(
            f"exp4_{domain}_{paper_difficulty}_{mode.value.lower()}_r{repeat_id}"
        ),
        domain=domain,
        difficulty=difficulty,
        paper_difficulty=paper_difficulty,
        topic_family=None,
        worker_count=EXP4_WORKER_COUNT,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode=mode.value,
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
        seed=EXP4_SEED_BASE + repeat_id,
        catalog_digest=catalog_digest,
    )


def _catalog_digest(context: PaperExecutionContext) -> str:
    catalog = _mapping(context.catalog, "catalog")
    value = catalog.get("catalog_digest")
    _require_complete_digest("catalog_digest", value)
    return str(value)


def _validate_approved_endpoint_binding(
    context: PaperExecutionContext,
) -> Mapping[str, Any]:
    binding = _mapping(
        context.approved_endpoint_binding,
        "approved_endpoint_binding",
    )
    expected = {
        "provider_config_id": BASELINE_PROVIDER_CONFIG_ID,
        "provider_family": BASELINE_PROVIDER_FAMILY,
        "provider_model_id": BASELINE_PROVIDER_MODEL_ID,
        "reasoning_profile_id": BASELINE_REASONING_PROFILE_ID,
    }
    for field_name, expected_value in expected.items():
        if binding.get(field_name) != expected_value:
            raise ValueError(
                f"Experiment 4 requires GLM-5.2 baseline {field_name}"
            )
    selected_entry_id = binding.get(
        "selected_entry_id",
        binding.get("model_entry_id"),
    )
    if selected_entry_id != BASELINE_MODEL_ENTRY_ID:
        raise ValueError("Experiment 4 requires GLM-5.2 baseline model entry")
    if binding.get("model_entry_id", selected_entry_id) != BASELINE_MODEL_ENTRY_ID:
        raise ValueError("Experiment 4 requires GLM-5.2 baseline model entry")
    for field_name in (
        "source_provider_config_digest",
        "model_endpoint_identity_digest",
    ):
        _require_complete_digest(field_name, binding.get(field_name))
    request_controls = _mapping(binding.get("request_controls"), "request_controls")
    if dict(request_controls) != BASELINE_REQUEST_CONTROLS:
        raise ValueError("Experiment 4 baseline request controls drifted")
    for field_name, expected_value in BASELINE_REQUEST_CONTROLS.items():
        if context.request_limits.get(field_name) != expected_value:
            raise ValueError("Experiment 4 context request controls drifted")
    return binding


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    return value


def _require_complete_digest(field_name: str, value: Any) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{field_name} must be a complete sha256 digest")


def _require_non_empty_string(field_name: str, value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_non_negative_int(field_name: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be an integer >= 0")


def _require_non_negative_number(field_name: str, value: Any) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (float, int))
        or value < 0
    ):
        raise ValueError(f"{field_name} must be a number >= 0")


def _required_string(value: Mapping[str, Any], field_name: str) -> str:
    item = value.get(field_name)
    _require_non_empty_string(field_name, item)
    return str(item)


def _normalize_ordered_case_ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("ordered_case_ids must be a list or tuple")
    normalized: list[str] = []
    for case_id in value:
        _require_non_empty_string("case_id", case_id)
        normalized.append(case_id)
    if len(set(normalized)) != len(normalized):
        raise ValueError("duplicate case_id in ordered_case_ids")
    return tuple(normalized)


def _normalize_blocked_reason(value: Any) -> str | None:
    if value is None:
        return None
    _require_non_empty_string("blocked_reason", value)
    return str(value)


__all__ = [
    "BASELINE_MODEL_ENTRY_ID",
    "BASELINE_PROVIDER_FAMILY",
    "BASELINE_PROVIDER_MODEL_ID",
    "EXP4_EXPERIMENT_ID",
    "EXP4_REPEATS",
    "EXP4_ROOT_RUN_COUNT",
    "Exp4ModeExecutionConfig",
    "Exp4MixedLeanCaseSelection",
    "Experiment4AblationModule",
    "build_exp4_mode_execution_config",
    "count_exp4_root_runs",
    "expand_exp4_conditions",
    "freeze_exp4_case_selections",
    "run_exp4_condition",
    "summarize_exp4_ablation",
    "validate_exp4_condition",
]
