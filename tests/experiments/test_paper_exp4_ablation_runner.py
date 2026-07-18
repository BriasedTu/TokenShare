from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any

import pytest

from tokenshare.experiments.paper_ablation import (
    PaperAblationMode,
    ablation_profile_for_mode,
)
from tokenshare.experiments.paper_experiment_contracts import (
    PaperExecutionContext,
    PaperExperimentModule,
)
from tokenshare.experiments.paper_exp4_ablation_runner import (
    BASELINE_MODEL_ENTRY_ID,
    BASELINE_PROVIDER_FAMILY,
    BASELINE_PROVIDER_MODEL_ID,
    EXP4_EXPERIMENT_ID,
    EXP4_REPEATS,
    EXP4_ROOT_RUN_COUNT,
    Experiment4AblationModule,
    build_exp4_mode_execution_config,
    count_exp4_root_runs,
    expand_exp4_conditions,
    freeze_exp4_case_selections,
    run_exp4_condition,
    summarize_exp4_ablation,
    validate_exp4_condition,
)
from tokenshare.experiments.paper_models import PaperConditionResult, PaperStatus


CATALOG_DIGEST = "sha256:" + "1" * 64
SOURCE_CONFIG_DIGEST = "sha256:" + "2" * 64
ENDPOINT_DIGEST = "sha256:" + "3" * 64


def test_exp4_module_conforms_to_gate_b_protocol() -> None:
    assert isinstance(Experiment4AblationModule(), PaperExperimentModule)


def test_exp4_expands_frozen_six_mode_matrix_with_glm_baseline() -> None:
    conditions = expand_exp4_conditions(_context())

    assert EXP4_ROOT_RUN_COUNT == 540
    assert EXP4_REPEATS == 3
    assert len(conditions) == 108
    assert len({condition.condition_id for condition in conditions}) == 108
    assert {condition.ablation_mode for condition in conditions} == {
        mode.value for mode in PaperAblationMode
    }
    assert {condition.domain for condition in conditions} == {
        "factorization",
        "lean_proof",
    }
    assert {
        condition.paper_difficulty
        for condition in conditions
        if condition.domain == "factorization"
    } == {"easy", "medium", "hard"}
    assert {
        condition.paper_difficulty
        for condition in conditions
        if condition.domain == "lean_proof"
    } == {"simple", "medium_lemma_dag", "hard_frontier"}
    assert {condition.repeat_id for condition in conditions} == {0, 1, 2}
    assert {condition.worker_count for condition in conditions} == {10}
    assert {condition.fault_type for condition in conditions} == {"none"}
    assert {condition.model_policy for condition in conditions} == {"fixed_entry"}
    assert {condition.model_entry_id for condition in conditions} == {
        BASELINE_MODEL_ENTRY_ID
    }
    assert {condition.provider_family for condition in conditions} == {
        BASELINE_PROVIDER_FAMILY
    }
    assert {condition.provider_model_id for condition in conditions} == {
        BASELINE_PROVIDER_MODEL_ID
    }
    assert all(condition.real_transport_required for condition in conditions)
    assert all(condition.paper_eligible_required for condition in conditions)


def test_exp4_freezes_five_task_batches_and_exact_540_root_runs() -> None:
    context = _context()
    conditions = expand_exp4_conditions(context)
    selections = freeze_exp4_case_selections(context, conditions)

    assert len(selections) == len(conditions)
    assert count_exp4_root_runs(conditions, selections) == EXP4_ROOT_RUN_COUNT
    assert all(len(selection.ordered_case_ids) == 5 for selection in selections)
    assert all(selection.is_executable for selection in selections)

    selections_by_slice: dict[tuple[str, str], set[tuple[tuple[str, ...], str]]] = {}
    for condition, selection in zip(conditions, selections, strict=True):
        key = (condition.domain, str(condition.paper_difficulty))
        selections_by_slice.setdefault(key, set()).add(
            (selection.ordered_case_ids, selection.selection_digest)
        )
    assert all(len(values) == 1 for values in selections_by_slice.values())


@pytest.mark.parametrize(
    ("paper_difficulty", "expected_topic_counts"),
    [
        ("simple", {"pure_logic": 2, "function_set": 2, "induction": 1}),
        (
            "medium_lemma_dag",
            {"pure_logic": 1, "function_set": 2, "induction": 2},
        ),
        (
            "hard_frontier",
            {"pure_logic": 2, "function_set": 1, "induction": 2},
        ),
    ],
)
def test_exp4_lean_selection_reuses_exp2_exact_221_slice(
    paper_difficulty: str,
    expected_topic_counts: dict[str, int],
) -> None:
    context = _context()
    conditions = expand_exp4_conditions(context)
    selections = freeze_exp4_case_selections(context, conditions)
    selected = next(
        selection
        for condition, selection in zip(conditions, selections, strict=True)
        if condition.domain == "lean_proof"
        and condition.paper_difficulty == paper_difficulty
    )
    expected_ids = tuple(
        case["case_id"]
        for topic_family in ("pure_logic", "function_set", "induction")
        for case in context.catalog["exp2"]["lean_proof"][paper_difficulty][
            topic_family
        ]
    )

    assert selected.ordered_case_ids == expected_ids
    assert selected.topic_family is None
    assert selected.to_dict()["topic_family_marker"] == "mixed_topic_family"
    assert {
        topic_family: sum(
            f"_{topic_family}_" in case_id
            for case_id in selected.ordered_case_ids
        )
        for topic_family in expected_topic_counts
    } == expected_topic_counts


def test_exp4_rejects_lean_slice_drift_from_exp2_reference() -> None:
    catalog = deepcopy(_catalog())
    catalog["exp4"]["lean_proof"] = deepcopy(catalog["exp4"]["lean_proof"])
    catalog["exp4"]["lean_proof"]["simple"]["pure_logic"][0]["case_id"] = (
        "lean_simple_pure_logic_drift"
    )
    context = _context(catalog=catalog)
    conditions = expand_exp4_conditions(context)

    with pytest.raises(ValueError, match="Lean slice drift"):
        freeze_exp4_case_selections(context, conditions)


def test_exp4_rejects_lean_ai_unit_drift_from_exp2_reference() -> None:
    catalog = deepcopy(_catalog())
    catalog["exp4"]["lean_proof"] = deepcopy(catalog["exp4"]["lean_proof"])
    catalog["exp4"]["lean_proof"]["simple"]["pure_logic"][0][
        "expected_ai_unit_count"
    ] += 1
    context = _context(catalog=catalog)
    conditions = expand_exp4_conditions(context)

    with pytest.raises(ValueError, match="Lean slice drift"):
        freeze_exp4_case_selections(context, conditions)


def test_exp4_missing_formal_lean_slice_is_structured_blocked_with_zero_calls() -> None:
    callback_count = 0

    def callback(**kwargs: Any) -> PaperConditionResult:
        nonlocal callback_count
        callback_count += 1
        raise AssertionError("blocked selection must not reach execution callback")

    catalog = deepcopy(_catalog())
    catalog["exp4"]["lean_proof"] = deepcopy(catalog["exp4"]["lean_proof"])
    del catalog["exp4"]["lean_proof"]["hard_frontier"]
    context = _context(catalog=catalog, callback=callback)
    conditions = expand_exp4_conditions(context)
    selections = freeze_exp4_case_selections(context, conditions)
    blocked = [
        (condition, selection)
        for condition, selection in zip(conditions, selections, strict=True)
        if condition.domain == "lean_proof"
        and condition.paper_difficulty == "hard_frontier"
    ]

    assert len(blocked) == 18
    assert all(selection.is_blocked for _, selection in blocked)
    assert all(selection.ordered_case_ids == () for _, selection in blocked)
    assert all(selection.expected_ai_unit_count == 0 for _, selection in blocked)
    assert all(
        selection.to_dict()["provider_calls_made"] == 0
        for _, selection in blocked
    )
    assert all(
        "synthetic" not in str(selection.to_dict()).lower()
        for _, selection in blocked
    )

    result = run_exp4_condition(context, blocked[0][0], blocked[0][1]).to_dict()

    assert result["status"] == "blocked"
    assert result["task_count"] == 5
    assert result["blocked_root_count"] == 5
    assert result["provider_attempt_count"] == 0
    assert callback_count == 0


def test_exp4_rejects_condition_model_drift_before_execution() -> None:
    context = _context()
    condition = expand_exp4_conditions(context)[0]
    drifted = replace(condition, model_entry_id="qwen_unapproved")

    with pytest.raises(ValueError, match="GLM-5.2 baseline model identity"):
        validate_exp4_condition(context, drifted)


def test_exp4_mode_configs_isolate_output_roots_and_preserve_full_default() -> None:
    context = _context()
    conditions = expand_exp4_conditions(context)
    comparable = [
        condition
        for condition in conditions
        if condition.domain == "factorization"
        and condition.paper_difficulty == "easy"
        and condition.repeat_id == 0
    ]
    configs = [
        build_exp4_mode_execution_config(context, condition)
        for condition in comparable
    ]

    assert len(configs) == 6
    assert len({config.output_root for config in configs}) == 6
    assert all(
        config.output_root.startswith(context.output_root)
        for config in configs
    )
    assert all(config.scope == "experiment_boundary" for config in configs)
    assert all(
        config.requires_provider_attempt_before_ablation is True
        for config in configs
    )
    assert all(
        config.system_default_behavior_changed is False
        for config in configs
    )
    full_config = next(
        config
        for config in configs
        if config.ablation_mode == PaperAblationMode.FULL.value
    )
    assert full_config.disabled_mechanisms == ()
    assert full_config.to_dict()["config_digest"].startswith("sha256:")
    assert (
        ablation_profile_for_mode(PaperAblationMode.FULL)
        .to_dict()["system_default_behavior_changed"]
        is False
    )


def test_exp4_run_condition_delegates_with_mode_wrapper_and_existing_profile() -> None:
    captured: list[dict[str, Any]] = []

    def callback(**kwargs: Any) -> PaperConditionResult:
        captured.append(kwargs)
        condition = kwargs["condition"]
        selection = kwargs["selection"]
        return PaperConditionResult(
            condition_id=condition.condition_id,
            status=PaperStatus.COMPLETED,
            repeat_count=1,
            task_count=len(selection.ordered_case_ids),
            completed_root_count=len(selection.ordered_case_ids),
            failed_root_count=0,
            blocked_root_count=0,
            provider_attempt_count=5,
            metrics_ref=None,
        )

    context = _context(callback=callback)
    conditions = expand_exp4_conditions(context)
    selections = freeze_exp4_case_selections(context, conditions)
    condition = next(
        item
        for item in conditions
        if item.ablation_mode == PaperAblationMode.NO_VERIFICATION.value
    )
    index = conditions.index(condition)

    result = run_exp4_condition(context, condition, selections[index])

    assert result.condition_id == condition.condition_id
    assert len(captured) == 1
    assert captured[0]["experiment_id"] == EXP4_EXPERIMENT_ID
    assert captured[0]["mode_config"].ablation_mode == condition.ablation_mode
    assert captured[0]["ablation_profile"].to_dict() == (
        ablation_profile_for_mode(condition.ablation_mode).to_dict()
    )
    assert captured[0]["output_root"] == captured[0]["mode_config"].output_root
    assert context.output_root == "outputs/experiments/exp4_test"


def test_exp4_run_condition_rejects_selection_drift_before_callback() -> None:
    callback_count = 0

    def callback(**kwargs: Any) -> PaperConditionResult:
        nonlocal callback_count
        callback_count += 1
        raise AssertionError("callback must not run for drifted selection")

    context = _context(callback=callback)
    conditions = expand_exp4_conditions(context)
    selections = freeze_exp4_case_selections(context, conditions)
    condition = conditions[0]
    selection = selections[0]
    drifted = replace(
        selection,
        ordered_case_ids=tuple(reversed(selection.ordered_case_ids)),
    )

    with pytest.raises(ValueError, match="selection digest"):
        run_exp4_condition(context, condition, drifted)
    assert callback_count == 0


def test_exp4_summary_reports_protocol_outcomes_resources_and_escape_rates() -> None:
    evidence = [
        _evidence_record(
            mode=PaperAblationMode.NO_VERIFICATION,
            tasks=(
                _task_result(
                    task_id="task_nv_1",
                    completed=True,
                    accepted_validity=False,
                    wrong_canonical_acceptance=True,
                    error_exposed=True,
                    error_escaped=True,
                    wall_clock_ms=100,
                    total_tokens=10,
                    cost_estimate=0.1,
                ),
                _task_result(
                    task_id="task_nv_2",
                    completed=False,
                    accepted_validity=None,
                    stuck_task=True,
                    error_exposed=True,
                    error_escaped=False,
                    wall_clock_ms=300,
                    total_tokens=20,
                    cost_estimate=0.2,
                ),
            ),
        ),
        _evidence_record(
            mode=PaperAblationMode.NO_PARSER_POLICY,
            tasks=(
                _task_result(
                    task_id="task_np_1",
                    completed=True,
                    accepted_validity=True,
                    raw_only_acceptance=True,
                    error_exposed=False,
                    error_escaped=False,
                ),
            ),
        ),
        _evidence_record(
            mode=PaperAblationMode.NO_REQUEUE,
            tasks=(
                _task_result(
                    task_id="task_nr_1",
                    completed=False,
                    accepted_validity=None,
                    stuck_task=True,
                    error_exposed=True,
                    error_escaped=False,
                ),
            ),
        ),
        _evidence_record(
            mode=PaperAblationMode.NO_MERGE_GATE,
            tasks=(
                _task_result(
                    task_id="task_nm_1",
                    completed=True,
                    accepted_validity=False,
                    premature_merge=True,
                    error_exposed=True,
                    error_escaped=True,
                ),
            ),
        ),
        _evidence_record(
            mode=PaperAblationMode.NO_SLOT_INTEGRITY,
            tasks=(
                _task_result(
                    task_id="task_ns_1",
                    completed=True,
                    accepted_validity=False,
                    slot_mismatch=True,
                    error_exposed=True,
                    error_escaped=True,
                ),
            ),
        ),
        _evidence_record(
            mode=PaperAblationMode.FULL,
            tasks=(
                _task_result(
                    task_id="task_full_1",
                    completed=True,
                    accepted_validity=True,
                    error_exposed=False,
                    error_escaped=False,
                ),
            ),
        ),
    ]

    rows = {
        row["ablation_mode"]: row
        for row in summarize_exp4_ablation(evidence).rows
    }
    no_verification = rows[PaperAblationMode.NO_VERIFICATION.value]
    assert no_verification["root_run_count"] == 2
    assert no_verification["completion_rate"] == 0.5
    assert no_verification["accepted_validity_rate"] == 0.0
    assert no_verification["wrong_canonical_acceptance_rate"] == 0.5
    assert no_verification["stuck_task_rate"] == 0.5
    assert no_verification["wall_clock_ms"] == 400
    assert no_verification["total_tokens"] == 30
    assert no_verification["cost"] == pytest.approx(0.3)
    assert no_verification["exposed_error_count"] == 2
    assert no_verification["escaped_error_count"] == 1
    assert no_verification["error_escape_rate"] == 0.5
    assert no_verification["error_escape_applicability"] == "applicable"

    no_parser = rows[PaperAblationMode.NO_PARSER_POLICY.value]
    assert no_parser["raw_only_acceptance_rate"] == 1.0
    assert no_parser["error_escape_rate"] is None
    assert no_parser["error_escape_applicability"] == "zero_denominator"

    no_requeue = rows[PaperAblationMode.NO_REQUEUE.value]
    assert no_requeue["stuck_task_rate"] == 1.0
    assert no_requeue["exposed_error_count"] == 1
    assert no_requeue["escaped_error_count"] == 0
    assert no_requeue["error_escape_rate"] is None
    assert no_requeue["error_escape_applicability"] == "not_applicable"

    assert rows[PaperAblationMode.NO_MERGE_GATE.value][
        "premature_merge_rate"
    ] == 1.0
    assert rows[PaperAblationMode.NO_SLOT_INTEGRITY.value][
        "slot_mismatch_rate"
    ] == 1.0
    assert rows[PaperAblationMode.FULL.value]["error_escape_rate"] is None
    assert rows[PaperAblationMode.FULL.value][
        "error_escape_applicability"
    ] == "not_applicable"


def test_exp4_summary_rejects_escape_without_exposure() -> None:
    evidence = [
        _evidence_record(
            mode=PaperAblationMode.NO_VERIFICATION,
            tasks=(
                _task_result(
                    task_id="task_invalid_escape",
                    completed=True,
                    accepted_validity=False,
                    error_exposed=False,
                    error_escaped=True,
                ),
            ),
        )
    ]

    with pytest.raises(ValueError, match="escaped error must be exposed"):
        summarize_exp4_ablation(evidence)


def test_exp4_summary_counts_multiple_exposed_objects_per_task() -> None:
    task = _task_result(
        task_id="task_multiple_objects",
        completed=True,
        accepted_validity=False,
        error_exposed=True,
        error_escaped=True,
    )
    task["exposed_error_count"] = 3
    task["escaped_error_count"] = 2
    summary = summarize_exp4_ablation(
        [
            _evidence_record(
                mode=PaperAblationMode.NO_VERIFICATION,
                tasks=(task,),
            )
        ]
    ).rows[0]

    assert summary["exposed_error_count"] == 3
    assert summary["escaped_error_count"] == 2
    assert summary["error_escape_rate"] == pytest.approx(2 / 3)


def test_exp4_scripted_summary_remains_paper_ineligible() -> None:
    scripted = _evidence_record(
        mode=PaperAblationMode.FULL,
        tasks=(
            _task_result(
                task_id="task_scripted",
                completed=True,
                accepted_validity=True,
                error_exposed=False,
                error_escaped=False,
            ),
        ),
        transport_kind="scripted",
        paper_eligible=False,
    )

    rows = summarize_exp4_ablation([scripted]).rows

    assert rows[0]["transport_kind"] == "scripted"
    assert rows[0]["paper_eligible"] is False
    forged = dict(scripted)
    forged["paper_eligible"] = True
    with pytest.raises(ValueError, match="scripted transport cannot be paper eligible"):
        summarize_exp4_ablation([forged])
    real = _evidence_record(
        mode=PaperAblationMode.FULL,
        tasks=(
            _task_result(
                task_id="task_real",
                completed=True,
                accepted_validity=True,
                error_exposed=False,
                error_escaped=False,
            ),
        ),
        transport_kind="ai_api",
        paper_eligible=True,
    )
    with pytest.raises(ValueError, match="scripted transport cannot be paper eligible"):
        summarize_exp4_ablation([forged, real])


def _evidence_record(
    *,
    mode: PaperAblationMode,
    tasks: tuple[dict[str, Any], ...],
    transport_kind: str = "scripted",
    paper_eligible: bool = False,
) -> dict[str, Any]:
    return {
        "condition_id": f"condition_{mode.value.lower()}",
        "domain": "factorization",
        "paper_difficulty": "easy",
        "ablation_mode": mode.value,
        "repeat_id": 0,
        "selection_digest": "sha256:" + "4" * 64,
        "transport_kind": transport_kind,
        "paper_eligible": paper_eligible,
        "task_results": list(tasks),
    }


def _task_result(
    *,
    task_id: str,
    completed: bool,
    accepted_validity: bool | None,
    wrong_canonical_acceptance: bool = False,
    raw_only_acceptance: bool = False,
    stuck_task: bool = False,
    premature_merge: bool = False,
    slot_mismatch: bool = False,
    error_exposed: bool,
    error_escaped: bool,
    wall_clock_ms: int = 10,
    total_tokens: int = 5,
    cost_estimate: float = 0.01,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "root_status": "completed" if completed else "failed",
        "accepted_validity": accepted_validity,
        "wrong_canonical_acceptance": wrong_canonical_acceptance,
        "raw_only_acceptance": raw_only_acceptance,
        "stuck_task": stuck_task,
        "premature_merge": premature_merge,
        "slot_mismatch": slot_mismatch,
        "exposed_error_count": int(error_exposed),
        "escaped_error_count": int(error_escaped),
        "wall_clock_ms": wall_clock_ms,
        "total_tokens": total_tokens,
        "cost_estimate": cost_estimate,
    }


def _context(
    *,
    catalog: dict[str, Any] | None = None,
    binding: dict[str, Any] | None = None,
    callback=None,
    output_root: str = "outputs/experiments/exp4_test",
) -> PaperExecutionContext:
    def default_callback(**kwargs: Any) -> PaperConditionResult:
        condition = kwargs["condition"]
        selection = kwargs["selection"]
        return PaperConditionResult(
            condition_id=condition.condition_id,
            status=PaperStatus.BLOCKED,
            repeat_count=1,
            task_count=len(selection.ordered_case_ids),
            completed_root_count=0,
            failed_root_count=0,
            blocked_root_count=len(selection.ordered_case_ids),
            provider_attempt_count=0,
            metrics_ref=None,
        )

    return PaperExecutionContext(
        context_id="exp4_test_context",
        catalog=catalog or _catalog(),
        approved_endpoint_binding=binding or _baseline_binding(),
        request_limits={
            "max_tokens": 1024,
            "timeout_seconds": 30,
            "max_provider_attempts": 1,
            "temperature": 0.0,
            "enable_thinking": False,
        },
        hard_limits={"max_total_provider_attempts": 0},
        output_root=output_root,
        artifact_store=object(),
        event_store=object(),
        execution_callback=callback or default_callback,
    )


def _baseline_binding() -> dict[str, Any]:
    return {
        "provider_config_id": "exp1_baseline_siliconflow",
        "selected_entry_id": "glm_5_2_exp1_baseline",
        "model_entry_id": "glm_5_2_exp1_baseline",
        "provider_family": "siliconflow",
        "provider_model_id": "zai-org/GLM-5.2",
        "reasoning_profile_id": "temperature_0_enable_thinking_false",
        "source_provider_config_digest": SOURCE_CONFIG_DIGEST,
        "model_endpoint_identity_digest": ENDPOINT_DIGEST,
        "request_controls": {"temperature": 0.0, "enable_thinking": False},
    }


def _catalog() -> dict[str, Any]:
    lean_slices = {
        "simple": {
            "pure_logic": _lean_cases("simple", "pure_logic", 2, 2),
            "function_set": _lean_cases("simple", "function_set", 2, 2),
            "induction": _lean_cases("simple", "induction", 1, 2),
        },
        "medium_lemma_dag": {
            "pure_logic": _lean_cases("medium_lemma_dag", "pure_logic", 1, 4),
            "function_set": _lean_cases("medium_lemma_dag", "function_set", 2, 4),
            "induction": _lean_cases("medium_lemma_dag", "induction", 2, 4),
        },
        "hard_frontier": {
            "pure_logic": _lean_cases("hard_frontier", "pure_logic", 2, 6),
            "function_set": _lean_cases("hard_frontier", "function_set", 1, 6),
            "induction": _lean_cases("hard_frontier", "induction", 2, 6),
        },
    }
    factorization_slices = {
        difficulty: [
            {
                "case_id": f"factorization_{difficulty}_{index:02d}",
                "expected_ai_unit_count": index + 1,
            }
            for index in range(1, 6)
        ]
        for difficulty in ("easy", "medium", "hard")
    }
    return {
        "suite_version": "paper_v1",
        "catalog_version": "v1",
        "catalog_digest": CATALOG_DIGEST,
        "exp2": {
            "factorization": factorization_slices,
            "lean_proof": lean_slices,
        },
        "exp4": {
            "factorization": factorization_slices,
            "lean_proof": lean_slices,
        },
    }


def _lean_cases(
    paper_difficulty: str,
    topic_family: str,
    count: int,
    expected_ai_unit_count: int,
) -> list[dict[str, Any]]:
    return [
        {
            "case_id": f"lean_{paper_difficulty}_{topic_family}_{index:02d}",
            "expected_ai_unit_count": expected_ai_unit_count,
        }
        for index in range(1, count + 1)
    ]
