"""Experiment 1 formal module built on the Gate B paper contracts."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from math import ceil, isfinite
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
EXP1_BASELINE_REASONING_PROFILE_ID = "default"

EXP1_FORMAL_REQUEST_CONTROLS: JsonObject = {
    "max_tokens": 1024,
    "timeout_seconds": 30,
    "max_provider_attempts": 1,
    "temperature": 0.0,
    "top_p": 1.0,
    "stream": False,
    "enable_thinking": False,
}
_EXP1_FORMAL_SUMMARY_INPUT_SCHEMA_VERSION = (
    "tokenshare.paper_exp1_summary_input.v1"
)
_PAPER_TASK_METRICS_SCHEMA_VERSION = "tokenshare.paper_task_metrics.v1"
_EXP1_SUPPORTED_CATALOG_VERSIONS = frozenset({"v1"})

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
        formal_summary = isinstance(evidence, Mapping) and (
            evidence.get("schema_version")
            == _EXP1_FORMAL_SUMMARY_INPUT_SCHEMA_VERSION
            or "task_metrics" in evidence
            or "conditions" in evidence
            or "selections" in evidence
        )
        if formal_summary:
            records = _formal_task_records(evidence)
        else:
            records = _task_records(evidence)
            _reject_scripted_paper_eligible_records(records)
            if any(record.get("paper_eligible") is True for record in records):
                raise ValueError(
                    "paper-eligible Experiment 1 summary requires canonical formal input"
                )
            for record in records:
                _validate_task_metrics_and_status(record, formal=False)
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
                    formal_summary=formal_summary,
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
    identity = _field(binding, "identity") or binding
    selected_entry_id = _field(identity, "selected_entry_id")
    model_entry_id = _field(identity, "model_entry_id")
    provider_config_id = _field(identity, "provider_config_id")
    provider_family = _field(identity, "provider_family")
    provider_model_id = _field(identity, "provider_model_id")
    reasoning_profile_id = _field(identity, "reasoning_profile_id")
    request_controls = _fixed_request_controls(context, identity=identity)
    if (
        selected_entry_id != EXP1_BASELINE_ENTRY_ID
        or (
            _has_field(identity, "model_entry_id")
            and model_entry_id != EXP1_BASELINE_ENTRY_ID
        )
        or provider_config_id != EXP1_BASELINE_PROVIDER_CONFIG_ID
        or provider_family != EXP1_BASELINE_PROVIDER_FAMILY
        or provider_model_id != EXP1_BASELINE_PROVIDER_MODEL_ID
        or reasoning_profile_id != EXP1_BASELINE_REASONING_PROFILE_ID
        or request_controls["temperature"] != 0.0
        or request_controls["enable_thinking"] is not False
    ):
        raise ValueError("Experiment 1 requires the fixed GLM-5.2 baseline")
    source_provider_config_digest = _field(
        identity,
        "source_provider_config_digest",
    )
    model_endpoint_identity_digest = _field(
        identity,
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


def _fixed_request_controls(
    context: PaperExecutionContext,
    *,
    identity: Any,
) -> JsonObject:
    request_limits = _required_controls_mapping(
        context.request_limits,
        "request_limits",
    )
    normalized_request_limits = _normalized_request_controls(request_limits)

    binding_controls = _field(context.approved_endpoint_binding, "request_controls")
    if binding_controls is not None:
        normalized_binding_controls = _normalized_request_controls(
            _required_controls_mapping(
                binding_controls,
                "approved binding request_controls",
            )
        )
        if normalized_request_limits != normalized_binding_controls:
            raise ValueError(
                "Experiment 1 requires fixed GLM-5.2 baseline request controls"
            )

    source_config = _field(context.approved_endpoint_binding, "source_config")
    selected_entry = _field(context.approved_endpoint_binding, "selected_entry")
    if source_config is not None or selected_entry is not None:
        defaults = _required_controls_mapping(
            _field(source_config, "defaults"),
            "approved binding source defaults",
        )
        overrides = _required_controls_mapping(
            _field(selected_entry, "request_overrides"),
            "approved binding entry request_overrides",
        )
        effective_controls = {**dict(defaults), **dict(overrides)}
        if _normalized_request_controls(effective_controls) != normalized_request_limits:
            raise ValueError(
                "Experiment 1 requires fixed GLM-5.2 baseline request controls"
            )

    reasoning_controls = _field(identity, "effective_reasoning_controls")
    if reasoning_controls is not None:
        normalized_reasoning = _required_controls_mapping(
            reasoning_controls,
            "approved endpoint effective_reasoning_controls",
        )
        expected_reasoning = {
            "temperature": EXP1_FORMAL_REQUEST_CONTROLS["temperature"],
            "enable_thinking": EXP1_FORMAL_REQUEST_CONTROLS["enable_thinking"],
        }
        if (
            "enable_thinking" not in normalized_reasoning
            or any(
                field_name not in expected_reasoning
                or value != expected_reasoning[field_name]
                for field_name, value in normalized_reasoning.items()
            )
        ):
            raise ValueError(
                "Experiment 1 requires fixed GLM-5.2 baseline request controls"
            )
    return normalized_request_limits


def _required_controls_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("Experiment 1 requires the fixed GLM-5.2 baseline")
    return value


def _normalized_request_controls(controls: Mapping[str, Any]) -> JsonObject:
    if set(controls) != set(EXP1_FORMAL_REQUEST_CONTROLS):
        raise ValueError(
            "Experiment 1 requires fixed GLM-5.2 baseline request controls"
        )
    normalized = dict(controls)
    for field_name in ("max_tokens", "timeout_seconds", "max_provider_attempts"):
        value = normalized[field_name]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(
                "Experiment 1 requires fixed GLM-5.2 baseline request controls"
            )
    temperature = normalized["temperature"]
    if (
        isinstance(temperature, bool)
        or not isinstance(temperature, (int, float))
        or float(temperature) != 0.0
        or normalized["enable_thinking"] is not False
        or normalized != EXP1_FORMAL_REQUEST_CONTROLS
    ):
        raise ValueError(
            "Experiment 1 requires fixed GLM-5.2 baseline request controls"
        )
    return dict(EXP1_FORMAL_REQUEST_CONTROLS)


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
                return None
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
    return _required_catalog_version(_field(catalog, "catalog_version"))


def _field(value: Any, field_name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(field_name)
    return getattr(value, field_name, None)


def _has_field(value: Any, field_name: str) -> bool:
    if isinstance(value, Mapping):
        return field_name in value
    return hasattr(value, field_name)


def _formal_task_records(evidence: Mapping[str, Any]) -> tuple[JsonObject, ...]:
    _reject_nested_pilot_evidence(evidence)
    if (
        evidence.get("schema_version")
        != _EXP1_FORMAL_SUMMARY_INPUT_SCHEMA_VERSION
        or evidence.get("experiment_id") != EXP1_FORMAL_EXPERIMENT_ID
        or evidence.get("suite_version") != EXP1_FORMAL_SUITE_VERSION
    ):
        raise ValueError("Experiment 1 formal summary input is incomplete")
    if evidence.get("formal") is not True or evidence.get("pilot_only") is not False:
        raise ValueError("pilot output cannot enter formal Experiment 1 summary")
    suite_paper_eligible = evidence.get("paper_eligible")
    if not isinstance(suite_paper_eligible, bool):
        raise ValueError("Experiment 1 formal paper_eligible must be a bool")
    catalog_digest = evidence.get("catalog_digest")
    if not _is_complete_digest(catalog_digest):
        raise ValueError("Experiment 1 formal catalog_digest is required")
    catalog_version = _required_catalog_version(evidence.get("catalog_version"))
    raw_conditions = _mapping_sequence(
        evidence.get("conditions"),
        "conditions",
    )
    raw_selections = _mapping_sequence(
        evidence.get("selections"),
        "selections",
    )
    expected_condition_ids = _expected_formal_condition_ids()
    if (
        len(raw_conditions) != len(expected_condition_ids)
        or len(raw_selections) != len(expected_condition_ids)
    ):
        raise ValueError("Experiment 1 formal summary requires 36 canonical conditions")

    conditions: list[PaperExperimentCondition] = []
    selections: list[FrozenCaseSelection] = []
    source_config_digests: set[str] = set()
    endpoint_identity_digests: set[str] = set()
    for raw_condition, expected_condition_id in zip(
        raw_conditions,
        expected_condition_ids,
        strict=True,
    ):
        condition = _validated_summary_condition(
            raw_condition,
            catalog_digest=str(catalog_digest),
        )
        if condition.condition_id != expected_condition_id:
            raise ValueError("Experiment 1 formal condition order/inventory drift")
        conditions.append(condition)
        source_config_digests.add(str(condition.source_provider_config_digest))
        endpoint_identity_digests.add(str(condition.model_endpoint_identity_digest))

    for raw_selection, condition in zip(
        raw_selections,
        conditions,
        strict=True,
    ):
        selection = _validated_summary_selection(
            raw_selection,
            condition=condition,
            catalog_version=catalog_version,
        )
        selections.append(selection)

    if len(source_config_digests) != 1 or len(endpoint_identity_digests) != 1:
        raise ValueError("Experiment 1 formal model identity digest drift")
    _validate_selection_inventory(tuple(selections))

    raw_task_metrics = _mapping_sequence(
        evidence.get("task_metrics"),
        "task_metrics",
    )
    if len(raw_task_metrics) != EXP1_EXPECTED_ROOT_RUNS:
        raise ValueError("Experiment 1 formal summary requires 495 root-runs")
    conditions_by_id = {
        condition.condition_id: condition for condition in conditions
    }
    tasks_by_condition: dict[str, list[JsonObject]] = defaultdict(list)
    task_keys: set[tuple[str, str]] = set()
    run_ids: set[str] = set()
    task_ids: set[str] = set()
    for raw_task in raw_task_metrics:
        condition_id = _required_non_empty_string(
            "condition_id",
            raw_task.get("condition_id"),
        )
        condition = conditions_by_id.get(condition_id)
        if condition is None:
            raise ValueError("task metric condition_id is not in the formal plan")
        task = _validated_shared_task_metric(
            raw_task,
            condition=condition,
            suite_paper_eligible=suite_paper_eligible,
        )
        task_key = (condition_id, str(task["case_id"]))
        if task_key in task_keys:
            raise ValueError("duplicate Experiment 1 condition/case task metric")
        task_keys.add(task_key)
        run_id = str(task["run_id"])
        task_id = str(task["task_id"])
        if run_id in run_ids or task_id in task_ids:
            raise ValueError("duplicate Experiment 1 run_id/task_id metric")
        run_ids.add(run_id)
        task_ids.add(task_id)
        tasks_by_condition[condition_id].append(task)

    tasks: list[JsonObject] = []
    for condition, selection in zip(conditions, selections, strict=True):
        condition_tasks = tasks_by_condition.get(condition.condition_id, [])
        if tuple(str(task["case_id"]) for task in condition_tasks) != tuple(
            selection.ordered_case_ids
        ):
            raise ValueError("actual task IDs must match frozen ordered_case_ids")
        tasks.extend(condition_tasks)

    root_ids_by_repeat: dict[int, set[str]] = defaultdict(set)
    for task in tasks:
        case_id = str(task["case_id"])
        root_ids_by_repeat[int(task["repeat_id"])].add(case_id)
    if set(root_ids_by_repeat) != set(range(EXP1_FORMAL_REPEAT_COUNT)) or any(
        len(case_ids) != EXP1_EXPECTED_UNIQUE_ROOTS
        for case_ids in root_ids_by_repeat.values()
    ):
        raise ValueError("Experiment 1 formal summary requires 165 roots per repeat")
    if len(set.union(*root_ids_by_repeat.values())) != EXP1_EXPECTED_UNIQUE_ROOTS:
        raise ValueError("Experiment 1 formal summary unique root inventory drift")
    matrix_paper_eligible = bool(tasks) and all(
        task.get("_exp1_derived_paper_eligible") is True for task in tasks
    )
    if not matrix_paper_eligible:
        for task in tasks:
            task["_exp1_derived_paper_eligible"] = False
    return tuple(tasks)


def _validated_shared_task_metric(
    value: Mapping[str, Any],
    *,
    condition: PaperExperimentCondition,
    suite_paper_eligible: bool,
) -> JsonObject:
    if value.get("schema_version") != _PAPER_TASK_METRICS_SCHEMA_VERSION:
        raise ValueError("Experiment 1 requires paper_task_metrics.v1 rows")
    case_id = _required_non_empty_string("case_id", value.get("case_id"))
    run_id = _required_non_empty_string("run_id", value.get("run_id"))
    task_id = _required_non_empty_string("task_id", value.get("task_id"))
    if (
        value.get("condition_id") != condition.condition_id
        or value.get("domain") != condition.domain
        or value.get("difficulty") != condition.difficulty
        or value.get("paper_difficulty") != condition.paper_difficulty
        or value.get("topic_family") != condition.topic_family
    ):
        raise ValueError("task metric identity does not match formal condition")
    if value.get("pilot_only") is not False:
        raise ValueError("pilot output cannot enter formal Experiment 1 summary")
    if not isinstance(value.get("paper_eligible"), bool):
        raise ValueError("task metric paper_eligible must be a bool")
    for field_name in ("attempted", "completed", "accepted_validity"):
        if not isinstance(value.get(field_name), bool):
            raise ValueError(f"task metric {field_name} must be a bool")
    for field_name in (
        "attempt_count",
        "provider_attempt_count",
        "parser_failure_count",
        "verifier_rejection_count",
        "checker_rejection_count",
        "provider_error_count",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "provider_latency_ms",
        "artifact_ref_count",
    ):
        _required_non_negative_int(field_name, value.get(field_name))
    if value.get("total_tokens") != (
        int(value["prompt_tokens"]) + int(value["completion_tokens"])
    ):
        raise ValueError("task metric total_tokens is inconsistent")
    if value.get("completed") is not (value.get("root_status") == "completed"):
        raise ValueError("task metric completed flag conflicts with root_status")
    _validate_task_metrics_and_status(value, formal=True)

    transport_kind = value.get("transport_kind")
    if transport_kind is not None and (
        not isinstance(transport_kind, str) or not transport_kind
    ):
        raise ValueError("task metric transport_kind must be a non-empty string")
    derived_paper_eligible = bool(
        suite_paper_eligible
        and value.get("paper_eligible") is True
        and (
            transport_kind is None
            or transport_kind == "ai_api"
        )
    )
    normalized = dict(value)
    normalized.update(
        {
            "case_id": case_id,
            "run_id": run_id,
            "task_id": task_id,
            "repeat_id": condition.repeat_id,
            "transport_kind": (
                "ai_api"
                if transport_kind is None and derived_paper_eligible
                else transport_kind
            ),
            "_exp1_derived_paper_eligible": derived_paper_eligible,
        }
    )
    return normalized


def _expected_formal_condition_ids() -> tuple[str, ...]:
    condition_ids: list[str] = []
    for repeat_id in range(EXP1_FORMAL_REPEAT_COUNT):
        condition_ids.extend(
            f"exp1_factorization_{difficulty}_w{EXP1_FORMAL_WORKER_COUNT}_r{repeat_id}"
            for difficulty in PAPER_DIFFICULTIES
        )
        condition_ids.extend(
            (
                f"exp1_lean_{paper_difficulty}_{topic_family}"
                f"_w{EXP1_FORMAL_WORKER_COUNT}_r{repeat_id}"
            )
            for paper_difficulty in LEAN_PAPER_DIFFICULTIES
            for topic_family in LEAN_TOPIC_FAMILIES
        )
    return tuple(condition_ids)


def _validated_summary_condition(
    value: Any,
    *,
    catalog_digest: str,
) -> PaperExperimentCondition:
    if not isinstance(value, Mapping):
        raise ValueError("Experiment 1 summary condition evidence is required")
    expected_keys = set(PaperExperimentCondition.__dataclass_fields__) | {
        "condition_digest"
    }
    if set(value) != expected_keys:
        raise ValueError("Experiment 1 summary condition body is incomplete")
    parsed = PaperExperimentCondition(
        **{
            field_name: value[field_name]
            for field_name in PaperExperimentCondition.__dataclass_fields__
        }
    )
    if dict(value) != parsed.to_dict():
        raise ValueError("Experiment 1 summary condition digest mismatch")
    expected_difficulty = (
        str(parsed.paper_difficulty)
        if parsed.domain == "factorization"
        else _LEAN_DIFFICULTY_TO_LEGACY_DIFFICULTY.get(
            str(parsed.paper_difficulty)
        )
    )
    expected_topic_family = (
        None if parsed.domain == "factorization" else parsed.topic_family
    )
    expected_condition_id = (
        f"exp1_factorization_{parsed.paper_difficulty}"
        f"_w{EXP1_FORMAL_WORKER_COUNT}_r{parsed.repeat_id}"
        if parsed.domain == "factorization"
        else (
            f"exp1_lean_{parsed.paper_difficulty}_{parsed.topic_family}"
            f"_w{EXP1_FORMAL_WORKER_COUNT}_r{parsed.repeat_id}"
        )
    )
    if (
        parsed.experiment_id != EXP1_FORMAL_EXPERIMENT_ID
        or parsed.condition_id != expected_condition_id
        or parsed.difficulty != expected_difficulty
        or parsed.topic_family != expected_topic_family
        or parsed.worker_count != EXP1_FORMAL_WORKER_COUNT
        or parsed.fault_type != "none"
        or float(parsed.fault_rate) != 0.0
        or parsed.ablation_mode != "FULL"
        or parsed.model_policy != "fixed_entry"
        or parsed.provider_config_id != EXP1_BASELINE_PROVIDER_CONFIG_ID
        or parsed.model_entry_id != EXP1_BASELINE_ENTRY_ID
        or parsed.provider_family != EXP1_BASELINE_PROVIDER_FAMILY
        or parsed.provider_model_id != EXP1_BASELINE_PROVIDER_MODEL_ID
        or parsed.reasoning_profile_id != EXP1_BASELINE_REASONING_PROFILE_ID
        or parsed.repeat_id not in range(EXP1_FORMAL_REPEAT_COUNT)
        or parsed.seed != EXP1_FORMAL_SEED_FAMILY[parsed.repeat_id]
        or parsed.catalog_digest != catalog_digest
        or parsed.real_transport_required is not True
        or parsed.paper_eligible_required is not True
        or not _is_complete_digest(parsed.source_provider_config_digest)
        or not _is_complete_digest(parsed.model_endpoint_identity_digest)
    ):
        raise ValueError("Experiment 1 summary condition fixed identity drift")
    return parsed


def _validated_summary_selection(
    value: Any,
    *,
    condition: PaperExperimentCondition,
    catalog_version: str,
) -> FrozenCaseSelection:
    if not isinstance(value, Mapping):
        raise ValueError("Experiment 1 summary selection evidence is required")
    selection = FrozenCaseSelection.from_dict(value)
    if dict(value) != selection.to_dict():
        raise ValueError("Experiment 1 summary selection body is incomplete")
    expected_case_count = (
        EXP1_FACTOR_CASES_PER_DIFFICULTY
        if condition.domain == "factorization"
        else EXP1_LEAN_CASES_PER_CELL
    )
    if (
        selection.selection_id != f"{condition.condition_id}_selection_v1"
        or selection.experiment_id != EXP1_FORMAL_EXPERIMENT_ID
        or selection.suite_version != EXP1_FORMAL_SUITE_VERSION
        or selection.catalog_version != catalog_version
        or selection.domain != condition.domain
        or selection.paper_difficulty != condition.paper_difficulty
        or selection.topic_family != condition.topic_family
        or selection.catalog_digest != condition.catalog_digest
        or selection.paper_eligible_required is not True
        or selection.is_blocked
        or len(selection.ordered_case_ids) != expected_case_count
        or selection.expected_ai_unit_count < expected_case_count
    ):
        raise ValueError("Experiment 1 summary selection does not match condition")
    return selection


def _reject_nested_pilot_evidence(value: Any) -> None:
    if isinstance(value, Mapping):
        if (
            value.get("pilot_only") is True
            or value.get("run_kind") == "pilot"
            or value.get("formal_run") is False
            or value.get("formal") is False
        ):
            raise ValueError("pilot output cannot enter formal Experiment 1 summary")
        for nested in value.values():
            _reject_nested_pilot_evidence(nested)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for nested in value:
            _reject_nested_pilot_evidence(nested)


def _mapping_sequence(value: Any, field_name: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{field_name} must be a sequence")
    normalized: list[Mapping[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError(f"{field_name} entries must be mappings")
        normalized.append(item)
    return tuple(normalized)


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
    _reject_nested_pilot_evidence(records)
    for record in records:
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


def _validate_task_metrics_and_status(
    record: Mapping[str, Any],
    *,
    formal: bool,
) -> None:
    _number(record.get("wall_clock_ms"), "wall_clock_ms")
    _required_non_negative_int("total_tokens", record.get("total_tokens"))
    _number(record.get("cost_estimate"), "cost_estimate")
    root_status = record.get("root_status")
    accepted_validity = record.get("accepted_validity")
    failure_stage = record.get("failure_stage")
    failure_kind = record.get("failure_kind")
    if accepted_validity not in (True, False, None):
        raise ValueError("accepted_validity must be bool or null")
    for field_name, value in (
        ("failure_stage", failure_stage),
        ("failure_kind", failure_kind),
    ):
        if value is not None and (not isinstance(value, str) or not value):
            raise ValueError(f"{field_name} must be a non-empty string or null")
    if root_status == "completed":
        if accepted_validity is not True:
            raise ValueError("completed task must have accepted validity")
        if failure_stage is not None or failure_kind is not None:
            raise ValueError("completed task cannot declare failure provenance")
        return
    if root_status == "failed":
        if accepted_validity is True:
            raise ValueError("failed task cannot have accepted validity")
        if failure_stage is None and failure_kind is None:
            raise ValueError("failed task requires failure stage or kind")
        if formal and (failure_stage is None or failure_kind is None):
            raise ValueError("formal failed task requires failure stage and kind")
        return
    if root_status not in {
        "blocked",
        "timeout",
        "budget_exhausted",
        "ineligible",
    }:
        raise ValueError("root_status is not a supported paper task status")
    if accepted_validity is True:
        raise ValueError("non-completed task cannot have accepted validity")
    if root_status == "budget_exhausted":
        if failure_stage is not None or failure_kind != "budget_limit":
            raise ValueError(
                "budget-exhausted task requires budget_limit failure kind"
            )
        return
    if formal and (failure_stage is None or failure_kind is None):
        raise ValueError("formal non-completed task requires failure stage and kind")


def _summary_row(
    *,
    domain: str,
    paper_difficulty: str,
    topic_family: str | None,
    records: tuple[JsonObject, ...],
    highest_observed_valid_completion_difficulty: str | None,
    formal_summary: bool,
) -> JsonObject:
    completed = [record for record in records if record.get("root_status") == "completed"]
    accepted = [record for record in records if record.get("accepted_validity") is True]
    completed_count = len(completed)
    wall_clock_values = [
        _number(record.get("wall_clock_ms"), "wall_clock_ms")
        for record in records
    ]
    token_values = [
        _number(record.get("total_tokens"), "total_tokens") for record in records
    ]
    cost_values = [
        _number(record.get("cost_estimate"), "cost_estimate")
        for record in records
    ]
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
        "paper_eligible": formal_summary
        and bool(records)
        and all(
            record.get("_exp1_derived_paper_eligible") is True
            for record in records
        ),
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


def _number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be numeric")
    normalized = float(value)
    if not isfinite(normalized) or normalized < 0:
        raise ValueError(f"{field_name} must be finite and non-negative")
    return normalized


def _required_non_negative_int(field_name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _required_non_empty_string(field_name: str, value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _required_catalog_version(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("catalog_version is required")
    if value not in _EXP1_SUPPORTED_CATALOG_VERSIONS:
        raise ValueError("catalog_version is not supported")
    return value


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
