"""Experiment 1 formal module built on the Gate B paper contracts."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from math import ceil
from statistics import median
from typing import Any

from tokenshare.experiments.paper_catalog import estimated_ai_units_for_case
from tokenshare.experiments.paper_experiment_contracts import (
    ExperimentSummaryRows,
    FrozenCaseSelection,
    PaperExecutionContext,
)
from tokenshare.experiments.paper_models import (
    LEAN_PAPER_DIFFICULTIES,
    LEAN_TOPIC_FAMILIES,
    PAPER_DIFFICULTIES,
    UNSUPPORTED_PAPER_TRANSPORTS,
    JsonObject,
    PaperConditionResult,
    PaperExperimentCondition,
    PaperStatus,
    digest_json,
)


EXP1_FORMAL_EXPERIMENT_ID = "exp1_real_ai_feasibility"
EXP1_FORMAL_SUITE_VERSION = "paper_v1"
EXP1_FORMAL_REPEAT_COUNT = 3
EXP1_FORMAL_WORKER_COUNT = 10
EXP1_FORMAL_SEED_FAMILY = (1, 2, 3)
EXP1_FACTOR_CASES_PER_DIFFICULTY = 10
EXP1_LEAN_CASES_PER_CELL = 15
EXP1_EXPECTED_UNIQUE_ROOTS = 165
EXP1_EXPECTED_ROOT_RUNS = 495

EXP1_BASELINE_PROVIDER_CONFIG_ID = "exp1_baseline_siliconflow"
EXP1_BASELINE_ENTRY_ID = "glm_5_2_exp1_baseline"
EXP1_BASELINE_PROVIDER_FAMILY = "siliconflow"
EXP1_BASELINE_PROVIDER_MODEL_ID = "zai-org/GLM-5.2"
EXP1_BASELINE_REASONING_PROFILE_ID = "temperature0_thinking_false"

_LEAN_DIFFICULTY_TO_LEGACY_DIFFICULTY = {
    "simple": "easy",
    "medium_lemma_dag": "medium",
    "hard_frontier": "hard",
}
_LEAN_CELL_KEYS = tuple(
    f"{paper_difficulty}/{topic_family}"
    for paper_difficulty in LEAN_PAPER_DIFFICULTIES
    for topic_family in LEAN_TOPIC_FAMILIES
)
_TASK14_MATRIX_PLAN_SCHEMA_VERSIONS = frozenset(
    {
        "tokenshare.lean_3x3_matrix_plan.v1",
        "tokenshare.lean_task14_3x3_readiness.v1",
    }
)
_DIFFICULTY_ORDER = {
    "easy": 0,
    "medium": 1,
    "hard": 2,
    "simple": 0,
    "medium_lemma_dag": 1,
    "hard_frontier": 2,
}


class Exp1FormalModule:
    """Gate B module for formal Experiment 1 plan/evidence boundaries."""

    def expand_conditions(
        self,
        context: PaperExecutionContext,
    ) -> tuple[PaperExperimentCondition, ...]:
        baseline = _baseline_identity(context)
        catalog_digest = _catalog_digest(context.catalog)
        conditions: list[PaperExperimentCondition] = []
        for repeat_id, seed in enumerate(EXP1_FORMAL_SEED_FAMILY):
            for difficulty in PAPER_DIFFICULTIES:
                conditions.append(
                    PaperExperimentCondition(
                        experiment_id=EXP1_FORMAL_EXPERIMENT_ID,
                        condition_id=(
                            f"exp1_factorization_{difficulty}"
                            f"_w{EXP1_FORMAL_WORKER_COUNT}_r{repeat_id}"
                        ),
                        domain="factorization",
                        difficulty=difficulty,
                        paper_difficulty=difficulty,
                        topic_family=None,
                        worker_count=EXP1_FORMAL_WORKER_COUNT,
                        fault_type="none",
                        fault_rate=0.0,
                        ablation_mode="FULL",
                        model_policy="fixed_entry",
                        repeat_id=repeat_id,
                        seed=seed,
                        catalog_digest=catalog_digest,
                        **baseline,
                    )
                )
            for paper_difficulty in LEAN_PAPER_DIFFICULTIES:
                for topic_family in LEAN_TOPIC_FAMILIES:
                    conditions.append(
                        PaperExperimentCondition(
                            experiment_id=EXP1_FORMAL_EXPERIMENT_ID,
                            condition_id=(
                                f"exp1_lean_{paper_difficulty}_{topic_family}"
                                f"_w{EXP1_FORMAL_WORKER_COUNT}_r{repeat_id}"
                            ),
                            domain="lean_proof",
                            difficulty=_LEAN_DIFFICULTY_TO_LEGACY_DIFFICULTY[
                                paper_difficulty
                            ],
                            paper_difficulty=paper_difficulty,
                            topic_family=topic_family,
                            worker_count=EXP1_FORMAL_WORKER_COUNT,
                            fault_type="none",
                            fault_rate=0.0,
                            ablation_mode="FULL",
                            model_policy="fixed_entry",
                            repeat_id=repeat_id,
                            seed=seed,
                            catalog_digest=catalog_digest,
                            **baseline,
                        )
                    )
        return tuple(conditions)

    def freeze_case_selections(
        self,
        context: PaperExecutionContext,
        conditions: tuple[PaperExperimentCondition, ...],
    ) -> tuple[FrozenCaseSelection, ...]:
        expected_conditions = self.expand_conditions(context)
        if tuple(condition.condition_digest for condition in conditions) != tuple(
            condition.condition_digest for condition in expected_conditions
        ):
            raise ValueError("Experiment 1 formal condition order drift")

        selections = tuple(
            _selection_for_condition(context=context, condition=condition)
            for condition in conditions
        )
        _validate_selection_inventory(selections)
        return selections

    def run_condition(
        self,
        context: PaperExecutionContext,
        condition: PaperExperimentCondition,
        selection: FrozenCaseSelection,
    ) -> PaperConditionResult:
        canonical_condition = _canonical_condition_for(context, condition)
        _validate_condition_selection_match(
            condition=canonical_condition,
            selection=selection,
        )
        _validate_canonical_selection(
            context=context,
            condition=canonical_condition,
            selection=selection,
        )
        if selection.is_blocked:
            return PaperConditionResult(
                condition_id=canonical_condition.condition_id,
                status=PaperStatus.BLOCKED,
                repeat_count=1,
                task_count=0,
                completed_root_count=0,
                failed_root_count=0,
                blocked_root_count=0,
                provider_attempt_count=0,
                metrics_ref={
                    "blocked_reason": selection.blocked_reason,
                    "provider_calls_made": 0,
                },
            )
        result = context.execution_callback(
            context=context,
            condition=canonical_condition,
            selection=selection,
        )
        if not isinstance(result, PaperConditionResult):
            raise ValueError("execution callback must return PaperConditionResult")
        if result.condition_id != canonical_condition.condition_id:
            raise ValueError("execution callback returned a mismatched condition_id")
        return result

    def summarize(self, evidence: Any) -> ExperimentSummaryRows:
        records = _task_records(evidence)
        _reject_scripted_paper_eligible_records(records)
        highest_by_scope = _highest_valid_completion_by_scope(records)
        rows: list[JsonObject] = []
        for group_key, group_records in _grouped_task_records(records).items():
            domain, paper_difficulty, topic_family = group_key
            rows.append(
                _summary_row(
                    domain=domain,
                    paper_difficulty=paper_difficulty,
                    topic_family=topic_family,
                    records=group_records,
                    highest_observed_valid_completion_difficulty=highest_by_scope.get(
                        (domain, topic_family)
                    ),
                )
            )
        return ExperimentSummaryRows(
            experiment_id=EXP1_FORMAL_EXPERIMENT_ID,
            rows=tuple(sorted(rows, key=_summary_sort_key)),
        )


def expand_conditions(
    context: PaperExecutionContext,
) -> tuple[PaperExperimentCondition, ...]:
    return Exp1FormalModule().expand_conditions(context)


def freeze_case_selections(
    context: PaperExecutionContext,
    conditions: tuple[PaperExperimentCondition, ...],
) -> tuple[FrozenCaseSelection, ...]:
    return Exp1FormalModule().freeze_case_selections(context, conditions)


def run_condition(
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
    selection: FrozenCaseSelection,
) -> PaperConditionResult:
    return Exp1FormalModule().run_condition(context, condition, selection)


def summarize(evidence: Any) -> ExperimentSummaryRows:
    return Exp1FormalModule().summarize(evidence)


def _selection_for_condition(
    *,
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
) -> FrozenCaseSelection:
    if condition.domain == "factorization":
        cases = _catalog_cases(
            context.catalog,
            domain="factorization",
            difficulty=condition.difficulty,
            paper_difficulty=condition.paper_difficulty,
            topic_family=None,
        )
        if len(cases) != EXP1_FACTOR_CASES_PER_DIFFICULTY:
            raise ValueError(
                "Experiment 1 factorization catalog must contain exactly "
                f"{EXP1_FACTOR_CASES_PER_DIFFICULTY} cases per difficulty"
            )
        return _executable_selection(
            context=context,
            condition=condition,
            cases=cases,
        )

    if not _lean_semantic_readiness_passed(context.catalog):
        return _blocked_selection(
            context=context,
            condition=condition,
            blocked_reason="lean_semantic_readiness_not_passed",
        )

    cases = _selected_lean_cases_for_condition(
        context=context,
        condition=condition,
    )
    if len(cases) != EXP1_LEAN_CASES_PER_CELL:
        return _blocked_selection(
            context=context,
            condition=condition,
            blocked_reason="missing_lean_selected_case",
        )
    return _executable_selection(context=context, condition=condition, cases=cases)


def _executable_selection(
    *,
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
    cases: tuple[JsonObject, ...],
) -> FrozenCaseSelection:
    return FrozenCaseSelection(
        selection_id=f"{condition.condition_id}_selection_v1",
        experiment_id=condition.experiment_id,
        suite_version=EXP1_FORMAL_SUITE_VERSION,
        catalog_version=_catalog_version(context.catalog),
        domain=condition.domain,
        paper_difficulty=str(condition.paper_difficulty),
        topic_family=condition.topic_family,
        ordered_case_ids=tuple(str(case["case_id"]) for case in cases),
        catalog_digest=_catalog_digest(context.catalog),
        expected_ai_unit_count=sum(estimated_ai_units_for_case(case) for case in cases),
        paper_eligible_required=True,
    )


def _blocked_selection(
    *,
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
    blocked_reason: str,
) -> FrozenCaseSelection:
    return FrozenCaseSelection(
        selection_id=f"{condition.condition_id}_selection_v1",
        experiment_id=condition.experiment_id,
        suite_version=EXP1_FORMAL_SUITE_VERSION,
        catalog_version=_catalog_version(context.catalog),
        domain=condition.domain,
        paper_difficulty=str(condition.paper_difficulty),
        topic_family=condition.topic_family,
        ordered_case_ids=(),
        catalog_digest=_catalog_digest(context.catalog),
        expected_ai_unit_count=0,
        paper_eligible_required=True,
        blocked_reason=blocked_reason,
    )


def _validate_selection_inventory(selections: tuple[FrozenCaseSelection, ...]) -> None:
    root_case_ids_by_group: dict[tuple[str, str, str | None], tuple[str, ...]] = {}
    unique_root_owner: dict[str, tuple[str, str, str | None]] = {}
    root_run_count = 0
    blocked_count = 0
    for selection in selections:
        group = (
            selection.domain,
            selection.paper_difficulty,
            selection.topic_family,
        )
        if selection.is_blocked:
            blocked_count += 1
            continue
        ids = tuple(selection.ordered_case_ids)
        root_run_count += len(ids)
        if group in root_case_ids_by_group:
            if ids != root_case_ids_by_group[group]:
                raise ValueError("Experiment 1 formal selection repeat/order drift")
            continue
        root_case_ids_by_group[group] = ids
        for case_id in ids:
            owner = unique_root_owner.get(case_id)
            if owner is not None and owner != group:
                raise ValueError(f"duplicate root case_id across selections: {case_id}")
            unique_root_owner[case_id] = group

    if blocked_count:
        return
    if len(unique_root_owner) != EXP1_EXPECTED_UNIQUE_ROOTS:
        raise ValueError("Experiment 1 formal unique root count drift")
    if root_run_count != EXP1_EXPECTED_ROOT_RUNS:
        raise ValueError("Experiment 1 formal root-run count drift")


def _validate_condition_selection_match(
    *,
    condition: PaperExperimentCondition,
    selection: FrozenCaseSelection,
) -> None:
    if (
        selection.experiment_id != condition.experiment_id
        or selection.domain != condition.domain
        or selection.paper_difficulty != condition.paper_difficulty
        or selection.topic_family != condition.topic_family
        or not selection.selection_id.startswith(condition.condition_id)
    ):
        raise ValueError("selection does not match condition")


def _canonical_condition_for(
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
) -> PaperExperimentCondition:
    expected_conditions = {
        expected.condition_id: expected
        for expected in Exp1FormalModule().expand_conditions(context)
    }
    canonical = expected_conditions.get(condition.condition_id)
    if (
        canonical is None
        or condition.condition_digest != canonical.condition_digest
    ):
        raise ValueError("condition does not match canonical formal condition")
    return canonical


def _validate_canonical_selection(
    *,
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
    selection: FrozenCaseSelection,
) -> None:
    canonical = _selection_for_condition(context=context, condition=condition)
    if _selection_contract_body(selection) != _selection_contract_body(canonical):
        raise ValueError("selection does not match canonical frozen selection")


def _selection_contract_body(selection: FrozenCaseSelection) -> JsonObject:
    return {
        "schema_version": selection.schema_version,
        "selection_id": selection.selection_id,
        "experiment_id": selection.experiment_id,
        "suite_version": selection.suite_version,
        "catalog_version": selection.catalog_version,
        "domain": selection.domain,
        "paper_difficulty": selection.paper_difficulty,
        "topic_family": selection.topic_family,
        "ordered_case_ids": list(selection.ordered_case_ids),
        "catalog_digest": selection.catalog_digest,
        "expected_ai_unit_count": selection.expected_ai_unit_count,
        "paper_eligible_required": selection.paper_eligible_required,
        "blocked_reason": selection.blocked_reason,
        "selection_digest": selection.selection_digest,
    }


def _baseline_identity(context: PaperExecutionContext) -> JsonObject:
    binding = context.approved_endpoint_binding
    selected_entry_id = _field(binding, "selected_entry_id")
    model_entry_id = _field(binding, "model_entry_id")
    provider_config_id = _field(binding, "provider_config_id")
    provider_family = _field(binding, "provider_family")
    provider_model_id = _field(binding, "provider_model_id")
    reasoning_profile_id = _field(binding, "reasoning_profile_id")
    request_controls = _fixed_request_controls(context)
    if (
        selected_entry_id != EXP1_BASELINE_ENTRY_ID
        or model_entry_id != EXP1_BASELINE_ENTRY_ID
        or provider_config_id != EXP1_BASELINE_PROVIDER_CONFIG_ID
        or provider_family != EXP1_BASELINE_PROVIDER_FAMILY
        or provider_model_id != EXP1_BASELINE_PROVIDER_MODEL_ID
        or reasoning_profile_id != EXP1_BASELINE_REASONING_PROFILE_ID
        or request_controls["temperature"] != 0.0
        or request_controls["enable_thinking"] is not False
    ):
        raise ValueError("Experiment 1 requires the fixed GLM-5.2 baseline")
    source_provider_config_digest = _field(binding, "source_provider_config_digest")
    model_endpoint_identity_digest = _field(
        binding,
        "model_endpoint_identity_digest",
    )
    if not _is_complete_digest(source_provider_config_digest) or not _is_complete_digest(
        model_endpoint_identity_digest
    ):
        raise ValueError("Experiment 1 requires the fixed GLM-5.2 baseline")
    return {
        "provider_config_id": EXP1_BASELINE_PROVIDER_CONFIG_ID,
        "model_entry_id": EXP1_BASELINE_ENTRY_ID,
        "provider_family": EXP1_BASELINE_PROVIDER_FAMILY,
        "provider_model_id": EXP1_BASELINE_PROVIDER_MODEL_ID,
        "reasoning_profile_id": EXP1_BASELINE_REASONING_PROFILE_ID,
        "source_provider_config_digest": source_provider_config_digest,
        "model_endpoint_identity_digest": model_endpoint_identity_digest,
    }


def _fixed_request_controls(context: PaperExecutionContext) -> JsonObject:
    request_limits = _required_controls_mapping(
        context.request_limits,
        "request_limits",
    )
    binding_controls = _field(context.approved_endpoint_binding, "request_controls")
    binding_controls = _required_controls_mapping(
        binding_controls,
        "approved binding request_controls",
    )
    request_temperature = _required_temperature_control(request_limits)
    binding_temperature = _required_temperature_control(binding_controls)
    request_enable_thinking = _required_enable_thinking_control(request_limits)
    binding_enable_thinking = _required_enable_thinking_control(binding_controls)
    if (
        request_temperature != binding_temperature
        or request_enable_thinking is not binding_enable_thinking
    ):
        raise ValueError("Experiment 1 requires the fixed GLM-5.2 baseline")
    return {
        "temperature": request_temperature,
        "enable_thinking": request_enable_thinking,
    }


def _required_controls_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("Experiment 1 requires the fixed GLM-5.2 baseline")
    return value


def _required_temperature_control(controls: Mapping[str, Any]) -> float:
    value = controls.get("temperature")
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or float(value) != 0.0
    ):
        raise ValueError("Experiment 1 requires the fixed GLM-5.2 baseline")
    return 0.0


def _required_enable_thinking_control(controls: Mapping[str, Any]) -> bool:
    if controls.get("enable_thinking") is not False:
        raise ValueError("Experiment 1 requires the fixed GLM-5.2 baseline")
    return False


def _catalog_cases(
    catalog: Any,
    *,
    domain: str,
    difficulty: str | None,
    paper_difficulty: str | None,
    topic_family: str | None,
) -> tuple[JsonObject, ...]:
    if hasattr(catalog, "cases_for") and callable(catalog.cases_for):
        return tuple(
            dict(case)
            for case in catalog.cases_for(
                domain=domain,
                difficulty=difficulty,
                paper_difficulty=paper_difficulty,
                topic_family=topic_family,
            )
        )
    if isinstance(catalog, Mapping):
        raw_cases = catalog.get("cases", ())
        if not isinstance(raw_cases, Sequence):
            raise ValueError("catalog cases must be a sequence")
        return tuple(
            dict(case)
            for case in raw_cases
            if isinstance(case, Mapping)
            and case.get("domain") == domain
            and (difficulty is None or case.get("difficulty") == difficulty)
            and (
                paper_difficulty is None
                or case.get("paper_difficulty") == paper_difficulty
            )
            and (topic_family is None or case.get("topic_family") == topic_family)
        )
    raise ValueError("catalog must provide cases_for or cases")


def _lean_semantic_readiness_passed(catalog: Any) -> bool:
    value = _field(catalog, "lean_semantic_readiness_passed")
    if value is False:
        return False
    return _validated_task15_budget_input(catalog) is not None


def _selected_lean_cases_for_condition(
    *,
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
) -> tuple[JsonObject, ...]:
    task15_input = _validated_task15_budget_input(context.catalog)
    if task15_input is None:
        return ()
    cell_key = f"{condition.paper_difficulty}/{condition.topic_family}"
    selected_ids = tuple(
        str(case_id)
        for case_id in task15_input["selected_case_ids_by_cell"][cell_key]
    )
    cases_by_id = _cases_by_id(
        _catalog_cases(
            context.catalog,
            domain="lean_proof",
            difficulty=None,
            paper_difficulty=None,
            topic_family=None,
        )
    )
    selected_cases: list[JsonObject] = []
    for case_id in selected_ids:
        case = cases_by_id.get(case_id)
        if case is None:
            return ()
        if (
            case.get("paper_difficulty") != condition.paper_difficulty
            or case.get("topic_family") != condition.topic_family
        ):
            return ()
        selected_cases.append(case)
    return tuple(selected_cases)


def _validated_task15_budget_input(catalog: Any) -> JsonObject | None:
    task15_input = _field(catalog, "task15_budget_input")
    if not isinstance(task15_input, Mapping):
        return None
    if task15_input.get("schema_version") != "tokenshare.lean_task15_budget_input.v1":
        return None
    if task15_input.get("catalog_digest") != _catalog_digest(catalog):
        return None
    if (
        task15_input.get("target_case_count") != EXP1_LEAN_CASES_PER_CELL
        or task15_input.get("selected_case_count") != 135
        or task15_input.get("executable_cell_count") != 9
        or task15_input.get("blocked_cell_count") != 0
        or task15_input.get("provider_calls_made") != 0
    ):
        return None
    environment_digest = task15_input.get("environment_digest")
    if not _is_complete_digest(environment_digest):
        return None
    oracle_package_digests = _normalized_digest_mapping(
        task15_input.get("oracle_package_digests")
    )
    if oracle_package_digests is None:
        return None
    selected_by_cell = task15_input.get("selected_case_ids_by_cell")
    counts_by_cell = task15_input.get("case_counts_by_cell")
    if not isinstance(selected_by_cell, Mapping) or not isinstance(counts_by_cell, Mapping):
        return None
    if set(selected_by_cell) != set(_LEAN_CELL_KEYS) or set(counts_by_cell) != set(
        _LEAN_CELL_KEYS
    ):
        return None

    seen_ids: set[str] = set()
    normalized_by_cell: dict[str, list[str]] = {}
    for cell_key in _LEAN_CELL_KEYS:
        case_ids = selected_by_cell[cell_key]
        if not isinstance(case_ids, Sequence) or isinstance(case_ids, (str, bytes)):
            return None
        normalized_ids = [
            str(case_id)
            for case_id in case_ids
            if isinstance(case_id, str) and case_id
        ]
        if (
            len(normalized_ids) != EXP1_LEAN_CASES_PER_CELL
            or len(normalized_ids) != len(case_ids)
            or counts_by_cell[cell_key] != EXP1_LEAN_CASES_PER_CELL
        ):
            return None
        for case_id in normalized_ids:
            if case_id in seen_ids:
                raise ValueError(f"duplicate root case_id in Task14 selection: {case_id}")
            seen_ids.add(case_id)
        normalized_by_cell[cell_key] = normalized_ids
    if len(seen_ids) != 135:
        return None
    semantic_fingerprints_by_cell = _normalized_cell_digest_lists(
        task15_input.get("semantic_fingerprint_digests_by_cell")
    )
    if semantic_fingerprints_by_cell is None:
        return None
    golden_case_ids_by_cell = _normalized_golden_case_ids_by_cell(
        task15_input.get("golden_case_ids_by_cell"),
        selected_by_cell=normalized_by_cell,
    )
    if golden_case_ids_by_cell is None:
        return None
    selection_digest = digest_json(
        _task14_selection_digest_body(
            catalog_digest=_catalog_digest(catalog),
            environment_digest=environment_digest,
            oracle_package_digests=oracle_package_digests,
            selected_case_ids_by_cell=normalized_by_cell,
            semantic_fingerprint_digests_by_cell=semantic_fingerprints_by_cell,
            golden_case_ids_by_cell=golden_case_ids_by_cell,
        )
    )
    if (
        task15_input.get("selection_digest") != selection_digest
        or task15_input.get("catalog_slice_digest") != selection_digest
    ):
        return None
    if not _task14_matrix_digest_matches(
        catalog=catalog,
        task15_input=task15_input,
        selection_digest=selection_digest,
    ):
        return None
    normalized = dict(task15_input)
    normalized["selected_case_ids_by_cell"] = normalized_by_cell
    normalized["semantic_fingerprint_digests_by_cell"] = semantic_fingerprints_by_cell
    normalized["golden_case_ids_by_cell"] = golden_case_ids_by_cell
    normalized["oracle_package_digests"] = oracle_package_digests
    normalized["selection_digest"] = selection_digest
    normalized["catalog_slice_digest"] = selection_digest
    return normalized


def _normalized_digest_mapping(value: Any) -> dict[str, str] | None:
    if not isinstance(value, Mapping) or not value:
        return None
    normalized: dict[str, str] = {}
    for key, digest in value.items():
        if not isinstance(key, str) or not key or not _is_complete_digest(digest):
            return None
        normalized[key] = str(digest)
    return normalized


def _normalized_cell_digest_lists(value: Any) -> dict[str, list[str]] | None:
    if not isinstance(value, Mapping) or set(value) != set(_LEAN_CELL_KEYS):
        return None
    normalized: dict[str, list[str]] = {}
    for cell_key in _LEAN_CELL_KEYS:
        digests = value[cell_key]
        if not isinstance(digests, Sequence) or isinstance(digests, (str, bytes)):
            return None
        normalized_digests = [str(digest) for digest in digests]
        if len(normalized_digests) != EXP1_LEAN_CASES_PER_CELL:
            return None
        if any(not _is_complete_digest(digest) for digest in normalized_digests):
            return None
        normalized[cell_key] = normalized_digests
    return normalized


def _normalized_golden_case_ids_by_cell(
    value: Any,
    *,
    selected_by_cell: Mapping[str, list[str]],
) -> dict[str, list[str]] | None:
    if not isinstance(value, Mapping) or set(value) != set(_LEAN_CELL_KEYS):
        return None
    normalized: dict[str, list[str]] = {}
    for cell_key in _LEAN_CELL_KEYS:
        case_ids = value[cell_key]
        if not isinstance(case_ids, Sequence) or isinstance(case_ids, (str, bytes)):
            return None
        normalized_ids = [
            str(case_id)
            for case_id in case_ids
            if isinstance(case_id, str) and case_id
        ]
        if len(normalized_ids) != len(case_ids) or not normalized_ids:
            return None
        selected_ids = set(selected_by_cell[cell_key])
        if any(case_id not in selected_ids for case_id in normalized_ids):
            return None
        normalized[cell_key] = normalized_ids
    return normalized


def _task14_selection_digest_body(
    *,
    catalog_digest: str,
    environment_digest: Any,
    oracle_package_digests: Mapping[str, str],
    selected_case_ids_by_cell: Mapping[str, list[str]],
    semantic_fingerprint_digests_by_cell: Mapping[str, list[str]],
    golden_case_ids_by_cell: Mapping[str, list[str]],
) -> JsonObject:
    return {
        "schema_version": "tokenshare.lean_task14_selected_cases.v1",
        "catalog_digest": catalog_digest,
        "environment_digest": environment_digest,
        "oracle_package_digests": dict(oracle_package_digests),
        "target_case_count": EXP1_LEAN_CASES_PER_CELL,
        "selected_case_ids_by_cell": {
            key: list(value) for key, value in selected_case_ids_by_cell.items()
        },
        "semantic_fingerprint_digests_by_cell": {
            key: list(value)
            for key, value in semantic_fingerprint_digests_by_cell.items()
        },
        "golden_case_ids_by_cell": {
            key: list(value) for key, value in golden_case_ids_by_cell.items()
        },
    }


def _task14_matrix_digest_matches(
    *,
    catalog: Any,
    task15_input: Mapping[str, Any],
    selection_digest: str,
) -> bool:
    matrix_plan = _task14_matrix_plan(catalog)
    if matrix_plan is None:
        return False
    try:
        expected_matrix_digest = digest_json(_task14_matrix_digest_body(matrix_plan))
        expected_ai_unit_count = _task14_matrix_expected_ai_unit_count(matrix_plan)
    except (KeyError, TypeError, ValueError):
        return False
    matrix_task15_input = matrix_plan.get("task15_budget_input")
    if not isinstance(matrix_task15_input, Mapping):
        return False
    return (
        matrix_plan.get("matrix_digest") == expected_matrix_digest
        and task15_input.get("matrix_digest") == expected_matrix_digest
        and matrix_task15_input.get("matrix_digest") == expected_matrix_digest
        and matrix_task15_input.get("selection_digest") == selection_digest
        and matrix_task15_input.get("catalog_slice_digest") == selection_digest
        and task15_input.get("expected_ai_unit_count") == expected_ai_unit_count
        and matrix_task15_input.get("expected_ai_unit_count") == expected_ai_unit_count
    )


def _task14_matrix_plan(catalog: Any) -> Mapping[str, Any] | None:
    candidates = (
        catalog,
        _field(catalog, "task14_matrix_plan"),
        _field(catalog, "lean_task14_matrix_plan"),
        _field(catalog, "lean_3x3_matrix_plan"),
        _field(catalog, "task14_readiness"),
    )
    for candidate in candidates:
        if (
            isinstance(candidate, Mapping)
            and candidate.get("schema_version") in _TASK14_MATRIX_PLAN_SCHEMA_VERSIONS
            and isinstance(candidate.get("cells"), Sequence)
            and not isinstance(candidate.get("cells"), (str, bytes))
            and isinstance(candidate.get("task15_budget_input"), Mapping)
        ):
            return candidate
    return None


def _task14_matrix_digest_body(body: Mapping[str, Any]) -> JsonObject:
    cells = body["cells"]
    if not isinstance(cells, Sequence) or isinstance(cells, (str, bytes)):
        raise ValueError("Task14 matrix cells must be a sequence")
    ignored_wrapper_fields = {
        "blocked_cell_map",
        "catalog_path",
        "catalog_version",
        "executable_cell_map",
        "matrix_digest",
    }
    digest_body = {
        key: value for key, value in body.items() if key not in ignored_wrapper_fields
    }
    digest_body["schema_version"] = "tokenshare.lean_3x3_matrix_plan.v1"
    task15_budget_input = digest_body.get("task15_budget_input")
    if isinstance(task15_budget_input, Mapping):
        digest_body["task15_budget_input"] = {
            key: value
            for key, value in task15_budget_input.items()
            if key != "matrix_digest"
        }
    digest_body["cells"] = [
        _task14_cell_digest_projection(cell) for cell in cells
    ]
    return digest_body


def _task14_matrix_expected_ai_unit_count(body: Mapping[str, Any]) -> int:
    cells = body["cells"]
    if not isinstance(cells, Sequence) or isinstance(cells, (str, bytes)):
        raise ValueError("Task14 matrix cells must be a sequence")
    total = 0
    for cell in cells:
        if not isinstance(cell, Mapping):
            raise ValueError("Task14 matrix cell must be a JSON object")
        expected_ai_unit_count = cell.get("expected_ai_unit_count")
        if (
            not isinstance(expected_ai_unit_count, int)
            or isinstance(expected_ai_unit_count, bool)
            or expected_ai_unit_count < 0
        ):
            raise ValueError("Task14 matrix cell AI-unit count must be non-negative")
        total += expected_ai_unit_count
    if total < 1:
        raise ValueError("Task14 matrix must declare AI units")
    return total


def _task14_cell_digest_projection(cell: Any) -> JsonObject:
    if not isinstance(cell, Mapping):
        raise ValueError("Task14 matrix cell must be a JSON object")
    projected = {
        key: value
        for key, value in cell.items()
        if key != "golden_evidence_by_case_id"
    }
    projected["golden_evidence_digest_by_case_id"] = {
        case_id: digest_json(_task14_golden_evidence_digest_body(evidence))
        for case_id, evidence in sorted(
            dict(cell.get("golden_evidence_by_case_id", {})).items()
        )
    }
    return projected


def _task14_golden_evidence_digest_body(evidence: Any) -> JsonObject:
    if not isinstance(evidence, Mapping):
        raise ValueError("Task14 golden evidence must be a JSON object")
    return {
        "schema_version": evidence.get("schema_version"),
        "evidence_source": evidence.get("evidence_source"),
        "case_id": evidence.get("case_id"),
        "environment_digest": evidence.get("environment_digest"),
        "oracle_package_digest": evidence.get("oracle_package_digest"),
        "split_certificate_digest": evidence.get("split_certificate_digest"),
        "deterministic_split": evidence.get("deterministic_split"),
        "child_proof_file_construction": evidence.get(
            "child_proof_file_construction"
        ),
        "checker_preflight": evidence.get("checker_preflight"),
        "dependency_aware_merge": evidence.get("dependency_aware_merge"),
        "root_recheck": evidence.get("root_recheck"),
        "provider_calls_made": evidence.get("provider_calls_made"),
        "node_ids": sorted(dict(evidence.get("node_checker_report_refs", {}))),
    }


def _cases_by_id(cases: tuple[JsonObject, ...]) -> dict[str, JsonObject]:
    indexed: dict[str, JsonObject] = {}
    for case in cases:
        case_id = str(case.get("case_id"))
        if case_id in indexed:
            raise ValueError(f"duplicate root case_id in catalog: {case_id}")
        indexed[case_id] = case
    return indexed


def _catalog_digest(catalog: Any) -> str:
    value = _field(catalog, "catalog_digest")
    if isinstance(value, str) and value:
        return value
    raise ValueError("catalog_digest is required")


def _catalog_version(catalog: Any) -> str:
    value = _field(catalog, "catalog_version")
    if isinstance(value, str) and value:
        return value
    return "v1"


def _field(value: Any, field_name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(field_name)
    return getattr(value, field_name, None)


def _task_records(evidence: Any) -> tuple[JsonObject, ...]:
    if isinstance(evidence, Mapping):
        records = evidence.get("task_results", evidence.get("tasks"))
    else:
        records = evidence
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise ValueError("Experiment 1 summary requires task result records")
    normalized: list[JsonObject] = []
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError("Experiment 1 task result must be a JSON object")
        normalized.append(dict(record))
    return tuple(normalized)


def _reject_scripted_paper_eligible_records(records: tuple[JsonObject, ...]) -> None:
    for record in records:
        if record.get("pilot_only") is True:
            raise ValueError("pilot output cannot enter formal Experiment 1 summary")
        transport_kind = record.get("transport_kind")
        if (
            isinstance(transport_kind, str)
            and transport_kind in UNSUPPORTED_PAPER_TRANSPORTS
            and record.get("paper_eligible") is True
        ):
            raise ValueError(f"{transport_kind} transport cannot be paper eligible")


def _grouped_task_records(
    records: tuple[JsonObject, ...],
) -> dict[tuple[str, str, str | None], tuple[JsonObject, ...]]:
    grouped: dict[tuple[str, str, str | None], list[JsonObject]] = defaultdict(list)
    for record in records:
        grouped[
            (
                str(record.get("domain")),
                str(record.get("paper_difficulty")),
                _optional_string(record.get("topic_family")),
            )
        ].append(record)
    return {key: tuple(value) for key, value in grouped.items()}


def _summary_row(
    *,
    domain: str,
    paper_difficulty: str,
    topic_family: str | None,
    records: tuple[JsonObject, ...],
    highest_observed_valid_completion_difficulty: str | None,
) -> JsonObject:
    completed = [record for record in records if record.get("root_status") == "completed"]
    accepted = [record for record in records if record.get("accepted_validity") is True]
    completed_count = len(completed)
    wall_clock_values = [_number(record.get("wall_clock_ms")) for record in records]
    token_values = [_number(record.get("total_tokens")) for record in records]
    cost_values = [_number(record.get("cost_estimate")) for record in completed]
    failure_breakdown = _failure_breakdown(records)
    return {
        "experiment_id": EXP1_FORMAL_EXPERIMENT_ID,
        "domain": domain,
        "paper_difficulty": paper_difficulty,
        "topic_family": topic_family,
        "case_count": len(
            {
                str(record.get("case_id") or record.get("task_id"))
                for record in records
            }
        ),
        "root_run_count": len(records),
        "repeat_count": len({int(record.get("repeat_id", 0)) for record in records}),
        "completion_rate": _ratio(completed_count, len(records)),
        "accepted_validity_rate": _ratio(len(accepted), len(records)),
        "wall_clock_median_ms": _median(wall_clock_values),
        "wall_clock_p90_ms": _p90(wall_clock_values),
        "total_tokens_median": _median(token_values),
        "total_tokens_p90": _p90(token_values),
        "cost_per_completed_task": (
            None if completed_count == 0 else sum(cost_values) / completed_count
        ),
        "failure_breakdown": failure_breakdown,
        "highest_observed_valid_completion_difficulty": (
            highest_observed_valid_completion_difficulty
        ),
        "paper_eligible": all(record.get("paper_eligible") is True for record in records),
        "transport_kind": _common_transport_kind(records),
    }


def _highest_valid_completion_by_scope(
    records: tuple[JsonObject, ...],
) -> dict[tuple[str, str | None], str]:
    highest: dict[tuple[str, str | None], str] = {}
    for record in records:
        if (
            record.get("root_status") != "completed"
            or record.get("accepted_validity") is not True
        ):
            continue
        domain = str(record.get("domain"))
        topic_family = _optional_string(record.get("topic_family"))
        difficulty = str(record.get("paper_difficulty"))
        key = (domain, topic_family)
        current = highest.get(key)
        if current is None or _DIFFICULTY_ORDER[difficulty] > _DIFFICULTY_ORDER[current]:
            highest[key] = difficulty
    return highest


def _failure_breakdown(records: tuple[JsonObject, ...]) -> list[JsonObject]:
    counts: Counter[str] = Counter()
    for record in records:
        if (
            record.get("root_status") == "completed"
            and record.get("accepted_validity") is True
        ):
            continue
        failure_kind = record.get("failure_kind") or record.get("failure_stage")
        if not isinstance(failure_kind, str) or not failure_kind:
            failure_kind = "unknown"
        counts[failure_kind] += 1
    return [
        {"failure_kind": failure_kind, "count": count}
        for failure_kind, count in sorted(counts.items())
    ]


def _summary_sort_key(row: JsonObject) -> tuple[int, int, int, str]:
    domain = str(row["domain"])
    paper_difficulty = str(row["paper_difficulty"])
    topic_family = row.get("topic_family")
    domain_order = 0 if domain == "factorization" else 1
    topic_order = (
        -1
        if topic_family is None
        else LEAN_TOPIC_FAMILIES.index(str(topic_family))
    )
    return (
        domain_order,
        _DIFFICULTY_ORDER[paper_difficulty],
        topic_order,
        paper_difficulty,
    )


def _common_transport_kind(records: tuple[JsonObject, ...]) -> str | None:
    values = {
        record.get("transport_kind")
        for record in records
        if isinstance(record.get("transport_kind"), str)
    }
    if len(values) == 1:
        return str(next(iter(values)))
    if not values:
        return None
    return "mixed"


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return median(values)


def _p90(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[ceil(0.9 * len(ordered)) - 1]


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _is_complete_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 71
        and value.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


__all__ = [
    "EXP1_BASELINE_ENTRY_ID",
    "EXP1_FORMAL_EXPERIMENT_ID",
    "EXP1_EXPECTED_ROOT_RUNS",
    "EXP1_EXPECTED_UNIQUE_ROOTS",
    "Exp1FormalModule",
    "expand_conditions",
    "freeze_case_selections",
    "run_condition",
    "summarize",
]
