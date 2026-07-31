from __future__ import annotations

import inspect
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tokenshare.experiments.paper_ablation import (
    PaperAblationMode,
    ablation_profile_for_mode,
)
from tokenshare.experiments.paper_experiment_contracts import (
    PaperExecutionContext,
    PaperExperimentModule,
    canonical_contract_digest,
)
from tokenshare.experiments.paper_exp4_ablation_runner import (
    BASELINE_MODEL_ENTRY_ID,
    BASELINE_PROVIDER_FAMILY,
    BASELINE_PROVIDER_MODEL_ID,
    EXP4_EXPERIMENT_ID,
    EXP4_MODES,
    EXP4_REPEATS,
    EXP4_V1_ROOT_RUN_COUNT,
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
from tokenshare.experiments.paper_formal_callbacks import run_exp4_ablation_strategy
from tokenshare.experiments.paper_formal_runner import (
    _FormalConditionExecutionCallback,
)
from tokenshare.experiments.paper_suite_scale import (
    build_paper_suite_scale_policy,
    load_paper_suite_scale_profile,
)


CATALOG_DIGEST = "sha256:" + "1" * 64
SOURCE_CONFIG_DIGEST = "sha256:" + "2" * 64
ENDPOINT_DIGEST = "sha256:" + "3" * 64


def test_task7_formal_exp4_observes_runtime_without_manual_requeue() -> None:
    source = inspect.getsource(_FormalConditionExecutionCallback._apply_exp4_mode)

    assert "_apply_exp4_requeue_boundary" not in source
    assert "dispatch_paper_case" not in source


def test_task7_ablation_callback_uses_experiment_event_namespace() -> None:
    result = run_exp4_ablation_strategy(
        mode="NO_VERIFICATION",
        adapter_observation={
            "candidate_rejected": True,
            "deterministic_validity": False,
        },
    )

    assert result.events
    assert all(
        event["event_type"].startswith("EXPERIMENT_")
        for event in result.events
    )


def test_exp4_module_conforms_to_gate_b_protocol() -> None:
    assert isinstance(Experiment4AblationModule(), PaperExperimentModule)


def test_exp4_expands_frozen_five_mode_matrix_with_glm_baseline() -> None:
    conditions = expand_exp4_conditions(_context())

    assert EXP4_V1_ROOT_RUN_COUNT == 450
    assert EXP4_REPEATS == 3
    assert len(conditions) == 90
    assert len({condition.condition_id for condition in conditions}) == 90
    assert {condition.ablation_mode for condition in conditions} == {
        mode.value for mode in EXP4_MODES
    }
    assert PaperAblationMode.NO_SLOT_INTEGRITY not in EXP4_MODES
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


def test_exp4_v2_suite_scale_keeps_all_modes_repeats_and_lean_slices() -> None:
    context = _context(catalog=_prepared_catalog_v2())
    conditions = expand_exp4_conditions(context)
    selections = freeze_exp4_case_selections(context, conditions)

    assert count_exp4_root_runs(conditions, selections) == 975
    assert {
        len(selection.ordered_case_ids)
        for condition, selection in zip(conditions, selections, strict=True)
        if condition.domain == "factorization"
    } == {16, 17}
    assert {
        len(selection.ordered_case_ids)
        for condition, selection in zip(conditions, selections, strict=True)
        if condition.domain == "lean_proof"
    } == {5}
    assert {condition.ablation_mode for condition in conditions} == {
        mode.value for mode in EXP4_MODES
    }
    assert {condition.repeat_id for condition in conditions} == {0, 1, 2}


def test_exp4_accepts_complete_baseline_request_policy_and_normal_profile() -> None:
    binding = _baseline_binding()
    binding["reasoning_profile_id"] = "high"
    binding["request_controls"] = _complete_request_controls()

    conditions = expand_exp4_conditions(_context(binding=binding))

    assert {condition.reasoning_profile_id for condition in conditions} == {"high"}


def test_exp4_accepts_controls_resolved_from_actual_baseline_provider_config() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    config = json.loads(
        (repo_root / "benchmarks/paper/exp1_baseline_provider_config.v3.json")
        .read_text(encoding="utf-8")
    )
    entry = next(
        item
        for item in config["entries"]
        if item["entry_id"] == "deepseek_v4_pro_exp1_baseline"
    )
    resolved_controls = {
        **config["defaults"],
        **entry["request_overrides"],
    }
    assert resolved_controls == {
        "max_tokens": 300_000,
        "timeout_seconds": 600,
        "max_provider_attempts": 1,
        "stream": False,
        "thinking": {"type": "enabled"},
        "reasoning_effort": "high",
    }
    binding = _baseline_binding()
    binding["request_controls"] = resolved_controls
    context = _context(binding=binding, request_limits=resolved_controls)

    conditions = expand_exp4_conditions(context)
    selections = freeze_exp4_case_selections(context, conditions)

    assert len(conditions) == 90
    assert count_exp4_root_runs(conditions, selections) == EXP4_V1_ROOT_RUN_COUNT
    assert all(selection.is_executable for selection in selections)


@pytest.mark.parametrize(
    ("field_name", "drifted_value"),
    [
        ("max_tokens", 2048),
        ("timeout_seconds", 101),
        ("max_provider_attempts", 2),
        ("top_p", 0.9),
        ("stream", True),
    ],
)
def test_exp4_rejects_request_policy_drift_before_callback(
    field_name: str,
    drifted_value: Any,
) -> None:
    callback_count = 0

    def callback(**kwargs: Any) -> PaperConditionResult:
        nonlocal callback_count
        callback_count += 1
        raise AssertionError("request policy drift must not reach callback")

    binding = _baseline_binding()
    binding["reasoning_profile_id"] = "high"
    binding["request_controls"] = _complete_request_controls()
    request_limits = _complete_request_controls()
    request_limits[field_name] = drifted_value

    with pytest.raises(ValueError, match="context request controls drifted"):
        expand_exp4_conditions(
            _context(
                binding=binding,
                callback=callback,
                request_limits=request_limits,
            )
        )
    assert callback_count == 0


def test_exp4_consumes_versioned_integration_prepared_catalog_view() -> None:
    context = _context(catalog=_prepared_catalog())

    conditions = expand_exp4_conditions(context)
    selections = freeze_exp4_case_selections(context, conditions)

    assert len(conditions) == 90
    assert count_exp4_root_runs(conditions, selections) == EXP4_V1_ROOT_RUN_COUNT
    assert all(selection.is_executable for selection in selections)


def test_exp4_prepared_view_blocks_lean_when_semantic_readiness_is_not_ready() -> None:
    callback_count = 0

    def callback(**kwargs: Any) -> PaperConditionResult:
        nonlocal callback_count
        callback_count += 1
        raise AssertionError("unready Lean selection must not reach callback")

    catalog = _prepared_catalog()
    catalog["lean_semantic_readiness_status"] = "blocked"
    catalog["lean_semantic_readiness_reason"] = "task15_budget_input_incomplete"
    context = _context(catalog=catalog, callback=callback)
    conditions = expand_exp4_conditions(context)
    selections = freeze_exp4_case_selections(context, conditions)
    lean_pairs = [
        (condition, selection)
        for condition, selection in zip(conditions, selections, strict=True)
        if condition.domain == "lean_proof"
    ]

    assert len(lean_pairs) == 45
    assert all(selection.is_blocked for _, selection in lean_pairs)
    assert {
        selection.blocked_reason for _, selection in lean_pairs
    } == {"lean_semantic_readiness_not_passed:task15_budget_input_incomplete"}

    condition, selection = lean_pairs[0]
    result = run_exp4_condition(context, condition, selection)
    assert result.status == PaperStatus.BLOCKED
    assert result.provider_attempt_count == 0
    assert callback_count == 0


def test_exp4_prepared_view_rejects_shared_lean_slice_digest_drift() -> None:
    catalog = _prepared_catalog()
    catalog["shared_lean_slices"]["simple"]["pure_logic"][0]["case_id"] = (
        "lean_simple_pure_logic_drift"
    )
    context = _context(catalog=catalog)

    with pytest.raises(ValueError, match="shared Lean slice digest mismatch"):
        freeze_exp4_case_selections(context, expand_exp4_conditions(context))


def test_exp4_freezes_five_task_batches_and_exact_540_root_runs() -> None:
    context = _context()
    conditions = expand_exp4_conditions(context)
    selections = freeze_exp4_case_selections(context, conditions)

    assert len(selections) == len(conditions)
    assert count_exp4_root_runs(conditions, selections) == EXP4_V1_ROOT_RUN_COUNT
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
        for case in context.catalog["shared_lean_slices"][paper_difficulty][
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
    catalog = _prepared_catalog()
    catalog["shared_lean_slices"]["simple"]["pure_logic"][0]["case_id"] = (
        "lean_simple_pure_logic_drift"
    )
    context = _context(catalog=catalog)
    conditions = expand_exp4_conditions(context)

    with pytest.raises(ValueError, match="shared Lean slice digest mismatch"):
        freeze_exp4_case_selections(context, conditions)


def test_exp4_rejects_lean_ai_unit_drift_from_exp2_reference() -> None:
    catalog = _prepared_catalog()
    catalog["shared_lean_slices"]["simple"]["pure_logic"][0][
        "expected_ai_unit_count"
    ] += 1
    context = _context(catalog=catalog)
    conditions = expand_exp4_conditions(context)

    with pytest.raises(ValueError, match="shared Lean slice digest mismatch"):
        freeze_exp4_case_selections(context, conditions)


def test_exp4_missing_formal_lean_slice_is_structured_blocked_with_zero_calls() -> None:
    callback_count = 0

    def callback(**kwargs: Any) -> PaperConditionResult:
        nonlocal callback_count
        callback_count += 1
        raise AssertionError("blocked selection must not reach execution callback")

    catalog = _prepared_catalog()
    del catalog["shared_lean_slices"]["hard_frontier"]
    context = _context(catalog=catalog, callback=callback)
    conditions = expand_exp4_conditions(context)
    selections = freeze_exp4_case_selections(context, conditions)
    blocked = [
        (condition, selection)
        for condition, selection in zip(conditions, selections, strict=True)
        if condition.domain == "lean_proof"
        and condition.paper_difficulty == "hard_frontier"
    ]

    assert len(blocked) == 15
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

    with pytest.raises(ValueError, match="DeepSeek-V4-Pro baseline model identity"):
        validate_exp4_condition(context, drifted)


def test_exp4_requires_explicit_approved_selected_entry_id() -> None:
    binding = _baseline_binding()
    del binding["selected_entry_id"]

    with pytest.raises(ValueError, match="baseline model entry"):
        expand_exp4_conditions(_context(binding=binding))


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

    assert len(configs) == 5
    assert len({config.output_root for config in configs}) == 5
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


def test_exp4_callback_receives_fresh_canonical_selection() -> None:
    captured: list[Any] = []

    def callback(**kwargs: Any) -> PaperConditionResult:
        captured.append(kwargs["selection"])
        condition = kwargs["condition"]
        return PaperConditionResult(
            condition_id=condition.condition_id,
            status=PaperStatus.COMPLETED,
            repeat_count=1,
            task_count=5,
            completed_root_count=5,
            failed_root_count=0,
            blocked_root_count=0,
            provider_attempt_count=5,
            metrics_ref=None,
        )

    context = _context(callback=callback)
    condition = expand_exp4_conditions(context)[0]
    frozen = freeze_exp4_case_selections(context, (condition,))[0]
    deserialized_equivalent = replace(frozen)

    run_exp4_condition(context, condition, deserialized_equivalent)

    assert captured[0] == frozen
    assert captured[0] is not deserialized_equivalent


def test_exp4_summary_uses_authoritative_condition_bounds_and_repeat_statistics() -> None:
    records = []
    for repeat_id, condition_wall_clock_ms in enumerate((100, 200, 500)):
        records.append(
            _evidence_record(
                mode=PaperAblationMode.NO_VERIFICATION,
                repeat_id=repeat_id,
                condition_wall_clock_ms=condition_wall_clock_ms,
                tasks=tuple(
                    _task_result(
                        task_id=f"case_{index}",
                        completed=True,
                        accepted_validity=True,
                        error_exposed=False,
                        error_escaped=False,
                        wall_clock_ms=10_000,
                        total_tokens=repeat_id + 1,
                        cost_estimate=(repeat_id + 1) / 100,
                    )
                    for index in range(5)
                ),
                transport_kind="ai_api",
                paper_eligible=False,
            )
        )

    row = summarize_exp4_ablation(_evidence(records, scope="pilot")).rows[0]

    assert row["wall_clock_ms"] == 200
    assert row["wall_clock_median_ms"] == 200
    assert row["wall_clock_iqr_ms"] == 400
    assert row["total_tokens"] == 30
    assert row["total_tokens_median"] == 10
    assert row["total_tokens_iqr"] == 10
    assert row["cost_estimate"] == pytest.approx(0.3)
    assert row["cost_median"] == pytest.approx(0.1)
    assert row["cost_iqr"] == pytest.approx(0.1)


def test_exp4_formal_summary_accepts_only_complete_450_root_matrix() -> None:
    evidence = _formal_evidence()

    summary = summarize_exp4_ablation(evidence)

    assert len(summary.rows) == 30
    assert sum(row["root_run_count"] for row in summary.rows) == 450
    assert {
        row["ablation_mode"] for row in summary.rows
    } == {mode.value for mode in EXP4_MODES}
    assert all(row["repeat_count"] == 3 for row in summary.rows)
    assert all(row["repeat_set_status"] == "complete" for row in summary.rows)
    assert all(row["paper_eligible"] is True for row in summary.rows)

    incomplete = deepcopy(evidence)
    incomplete["condition_results"].pop()
    with pytest.raises(ValueError, match="formal evidence must contain 90 conditions"):
        summarize_exp4_ablation(incomplete)


def test_exp4_formal_summary_rejects_duplicate_repeat_with_complete_record_count() -> None:
    evidence = _formal_evidence()
    replacement = deepcopy(evidence["condition_results"][-1])
    replacement["condition_id"] += "_duplicate"
    replacement["condition_digest"] = canonical_contract_digest(
        {"condition_id": replacement["condition_id"]}
    )
    replacement["repeat_id"] = 1
    replacement["seed"] = 4001
    evidence["condition_results"][-1] = replacement

    with pytest.raises(ValueError, match="formal repeat set must be exactly 0, 1, 2"):
        summarize_exp4_ablation(evidence)


def test_exp4_pilot_scripted_evidence_cannot_enter_formal_summary() -> None:
    scripted = _evidence_record(
        mode=PaperAblationMode.FULL,
        tasks=tuple(
            _task_result(
                task_id=f"scripted_case_{index}",
                completed=True,
                accepted_validity=True,
                error_exposed=False,
                error_escaped=False,
            )
            for index in range(5)
        ),
        transport_kind="scripted",
        paper_eligible=False,
    )

    pilot_row = summarize_exp4_ablation(
        _evidence([scripted], scope="pilot")
    ).rows[0]
    assert pilot_row["execution_scope"] == "pilot"
    assert pilot_row["paper_eligible"] is False

    formal = _formal_evidence()
    formal["condition_results"][0]["transport_kind"] = "scripted"
    formal["condition_results"][0]["paper_eligible"] = False
    with pytest.raises(ValueError, match="formal evidence requires ai_api transport"):
        summarize_exp4_ablation(formal)


def test_exp4_summary_reports_protocol_outcomes_resources_and_escape_rates() -> None:
    evidence = [
        _evidence_record(
            mode=PaperAblationMode.NO_VERIFICATION,
            condition_wall_clock_ms=400,
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
        for row in summarize_exp4_ablation(_evidence(evidence)).rows
    }
    no_verification = rows[PaperAblationMode.NO_VERIFICATION.value]
    assert no_verification["root_run_count"] == 2
    assert no_verification["completion_rate"] == 0.5
    assert no_verification["accepted_validity_rate"] == 0.0
    assert no_verification["wrong_canonical_acceptance_rate"] == 0.5
    assert no_verification["wrong_canonical_count"] == 1
    assert no_verification["stuck_task_rate"] == 0.5
    assert no_verification["wall_clock_ms"] == 400
    assert no_verification["total_tokens"] == 30
    assert no_verification["cost"] == pytest.approx(0.3)
    assert no_verification["exposed_error_count"] == 2
    assert no_verification["escaped_error_count"] == 1
    assert no_verification["error_escape_rate"] == 0.5
    assert no_verification["error_escape_applicability"] == "applicable"

    no_parser = rows[PaperAblationMode.NO_PARSER_POLICY.value]
    assert no_parser["raw_only_exposure_count"] == 1
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
    assert rows[PaperAblationMode.NO_MERGE_GATE.value][
        "premature_merge_attempt_count"
    ] == 1
    assert rows[PaperAblationMode.NO_MERGE_GATE.value][
        "premature_merge_failure_rate"
    ] == 1.0
    assert PaperAblationMode.NO_SLOT_INTEGRITY.value not in rows
    assert rows[PaperAblationMode.FULL.value]["error_escape_rate"] is None
    assert rows[PaperAblationMode.FULL.value][
        "error_escape_applicability"
    ] == "not_applicable"


def test_exp4_specialty_counts_do_not_change_when_only_mode_label_changes() -> None:
    task = _task_result(
        task_id="task_same_evidence",
        completed=True,
        accepted_validity=False,
        wrong_canonical_acceptance=True,
        raw_only_acceptance=True,
        premature_merge=True,
        error_exposed=True,
        error_escaped=True,
    )
    summaries = {}
    for mode in (
        PaperAblationMode.NO_VERIFICATION,
        PaperAblationMode.NO_PARSER_POLICY,
    ):
        row = summarize_exp4_ablation(
            _evidence([_evidence_record(mode=mode, tasks=(task,))])
        ).rows[0]
        summaries[mode] = {
            field: row[field]
            for field in (
                "wrong_canonical_count",
                "raw_only_exposure_count",
                "stuck_task_count",
                "premature_merge_attempt_count",
                "premature_merge_failure_rate",
                "exposed_error_count",
                "escaped_error_count",
            )
        }

    assert summaries[PaperAblationMode.NO_VERIFICATION] == summaries[
        PaperAblationMode.NO_PARSER_POLICY
    ]


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
        summarize_exp4_ablation(_evidence(evidence))


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
        _evidence(
            [
                _evidence_record(
                    mode=PaperAblationMode.NO_VERIFICATION,
                    tasks=(task,),
                )
            ]
        )
    ).rows[0]

    assert summary["exposed_error_count"] == 3
    assert summary["escaped_error_count"] == 2
    assert summary["error_escape_rate"] == pytest.approx(2 / 3)


@pytest.mark.parametrize("invalid_cost", [float("nan"), float("inf")])
def test_exp4_summary_rejects_non_finite_cost(invalid_cost: float) -> None:
    task = _task_result(
        task_id="non_finite_cost",
        completed=True,
        accepted_validity=True,
        error_exposed=False,
        error_escaped=False,
    )
    task["cost_estimate"] = invalid_cost

    with pytest.raises(ValueError, match="cost_estimate must be a number >= 0"):
        summarize_exp4_ablation(
            _evidence(
                [
                    _evidence_record(
                        mode=PaperAblationMode.FULL,
                        tasks=(task,),
                    )
                ]
            )
        )


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

    rows = summarize_exp4_ablation(_evidence([scripted])).rows

    assert rows[0]["transport_kind"] == "scripted"
    assert rows[0]["paper_eligible"] is False


def _evidence_record(
    *,
    mode: PaperAblationMode,
    tasks: tuple[dict[str, Any], ...],
    transport_kind: str = "scripted",
    paper_eligible: bool = False,
    domain: str = "factorization",
    paper_difficulty: str = "easy",
    repeat_id: int = 0,
    condition_id: str | None = None,
    condition_digest: str | None = None,
    selection_digest: str = "sha256:" + "4" * 64,
    ordered_case_ids: tuple[str, ...] | None = None,
    condition_wall_clock_ms: int = 10,
) -> dict[str, Any]:
    resolved_condition_id = condition_id or (
        f"condition_{domain}_{paper_difficulty}_{mode.value.lower()}_r{repeat_id}"
    )
    resolved_case_ids = ordered_case_ids or tuple(
        str(task["case_id"]) for task in tasks
    )
    return {
        "schema_version": "tokenshare.paper_exp4_condition_evidence.v1",
        "condition_id": resolved_condition_id,
        "condition_digest": condition_digest
        or canonical_contract_digest({"condition_id": resolved_condition_id}),
        "domain": domain,
        "paper_difficulty": paper_difficulty,
        "ablation_mode": mode.value,
        "repeat_id": repeat_id,
        "seed": 4000 + repeat_id,
        "worker_count": 10,
        "fault_type": "none",
        "fault_rate": 0.0,
        "selection_digest": selection_digest,
        "ordered_case_ids": list(resolved_case_ids),
        "catalog_digest": CATALOG_DIGEST,
        "catalog_version": "v1",
        "provider_config_id": "exp1_baseline_deepseek",
        "selected_entry_id": "deepseek_v4_pro_exp1_baseline",
        "model_entry_id": "deepseek_v4_pro_exp1_baseline",
        "provider_family": "deepseek",
        "provider_model_id": "deepseek-v4-pro",
        "reasoning_profile_id": "high",
        "source_provider_config_digest": SOURCE_CONFIG_DIGEST,
        "model_endpoint_identity_digest": ENDPOINT_DIGEST,
        "request_controls": _complete_request_controls(),
        "transport_kind": transport_kind,
        "paper_eligible": paper_eligible,
        "condition_wall_clock_ms": condition_wall_clock_ms,
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
        "case_id": task_id,
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


def _evidence(
    records: list[dict[str, Any]],
    *,
    scope: str = "pilot",
) -> dict[str, Any]:
    return {
        "schema_version": "tokenshare.paper_exp4_ablation_evidence.v1",
        "execution_scope": scope,
        "suite_version": "paper_v1",
        "catalog_digest": CATALOG_DIGEST,
        "catalog_version": "v1",
        "condition_results": records,
    }


def _formal_evidence() -> dict[str, Any]:
    context = _context()
    conditions = expand_exp4_conditions(context)
    selections = freeze_exp4_case_selections(context, conditions)
    records: list[dict[str, Any]] = []
    for condition, selection in zip(conditions, selections, strict=True):
        records.append(
            _evidence_record(
                mode=PaperAblationMode(condition.ablation_mode),
                domain=condition.domain,
                paper_difficulty=str(condition.paper_difficulty),
                repeat_id=condition.repeat_id,
                condition_id=condition.condition_id,
                condition_digest=condition.condition_digest,
                selection_digest=selection.selection_digest,
                ordered_case_ids=selection.ordered_case_ids,
                condition_wall_clock_ms=100 + condition.repeat_id,
                tasks=tuple(
                    _task_result(
                        task_id=case_id,
                        completed=True,
                        accepted_validity=True,
                        error_exposed=False,
                        error_escaped=False,
                    )
                    for case_id in selection.ordered_case_ids
                ),
                transport_kind="ai_api",
                paper_eligible=True,
            )
        )
    return _evidence(records, scope="formal")


def _context(
    *,
    catalog: dict[str, Any] | None = None,
    binding: dict[str, Any] | None = None,
    callback=None,
    request_limits: dict[str, Any] | None = None,
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
        catalog=_prepared_catalog() if catalog is None else catalog,
        approved_endpoint_binding=(
            _baseline_binding() if binding is None else binding
        ),
        request_limits=(
            _complete_request_controls()
            if request_limits is None
            else request_limits
        ),
        hard_limits={"max_total_provider_attempts": 0},
        output_root=output_root,
        artifact_store=object(),
        event_store=object(),
        execution_callback=callback or default_callback,
    )


def _baseline_binding() -> dict[str, Any]:
    return {
        "provider_config_id": "exp1_baseline_deepseek",
        "selected_entry_id": "deepseek_v4_pro_exp1_baseline",
        "model_entry_id": "deepseek_v4_pro_exp1_baseline",
        "provider_family": "deepseek",
        "provider_model_id": "deepseek-v4-pro",
        "reasoning_profile_id": "high",
        "source_provider_config_digest": SOURCE_CONFIG_DIGEST,
        "model_endpoint_identity_digest": ENDPOINT_DIGEST,
        "request_controls": _complete_request_controls(),
    }


def _complete_request_controls() -> dict[str, Any]:
    return {
        "max_tokens": 300_000,
        "timeout_seconds": 600,
        "max_provider_attempts": 1,
        "stream": False,
        "thinking": {"type": "enabled"},
        "reasoning_effort": "high",
    }


def _prepared_catalog() -> dict[str, Any]:
    source = _catalog()
    shared_lean_slices = deepcopy(source["exp2"]["lean_proof"])
    return {
        "schema_version": "tokenshare.paper_exp4_catalog_view.v1",
        "catalog_source_kind": "paper_input_catalog_manifest",
        "suite_version": source["suite_version"],
        "catalog_version": source["catalog_version"],
        "catalog_digest": source["catalog_digest"],
        "lean_semantic_readiness_status": "ready",
        "lean_semantic_readiness_reason": None,
        "factorization_slices": deepcopy(source["exp4"]["factorization"]),
        "shared_lean_slice_experiment_ids": ["exp2", "exp4", "exp5"],
        "shared_lean_slices": shared_lean_slices,
        "shared_lean_slice_digests": {
            paper_difficulty: _shared_lean_slice_digest(
                paper_difficulty,
                shared_lean_slices[paper_difficulty],
            )
            for paper_difficulty in (
                "simple",
                "medium_lemma_dag",
                "hard_frontier",
            )
        },
    }


def _prepared_catalog_v2() -> dict[str, Any]:
    catalog = _prepared_catalog()
    profile = load_paper_suite_scale_profile(
        "benchmarks/paper/paper_suite_scale_profile.v1.json"
    )
    raw_cases = tuple(
        json.loads(line)
        for line in Path(profile.factorization_catalog_path)
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    )
    unit_counts = {"easy": 2, "medium": 4, "hard": 8}
    candidates_by_difficulty = {
        difficulty: tuple(
            {
                **case,
                "expected_ai_unit_count": unit_counts[difficulty],
            }
            for case in raw_cases
            if case["paper_difficulty"] == difficulty
        )
        for difficulty in ("easy", "medium", "hard")
    }
    suite_policy, selected_by_scope = build_paper_suite_scale_policy(
        profile=profile,
        catalog_id=profile.catalog_id,
        catalog_version=profile.catalog_version,
        catalog_digest=profile.catalog_digest,
        candidates_by_difficulty=candidates_by_difficulty,
    )
    selected = selected_by_scope[EXP4_EXPERIMENT_ID]
    catalog["catalog_id"] = profile.catalog_id
    catalog["catalog_version"] = profile.catalog_version
    catalog["catalog_digest"] = profile.catalog_digest
    catalog["factorization_slices"] = {
        difficulty: list(cases) for difficulty, cases in selected.items()
    }
    catalog["paper_suite_scale_policy"] = suite_policy
    return catalog


def _shared_lean_slice_digest(
    paper_difficulty: str,
    by_topic: dict[str, list[dict[str, Any]]],
) -> str:
    return canonical_contract_digest(
        {
            "paper_difficulty": paper_difficulty,
            "topic_allocations": {
                topic_family: len(by_topic[topic_family])
                for topic_family in ("pure_logic", "function_set", "induction")
            },
            "ordered_cases": [
                {
                    "case_id": case["case_id"],
                    "expected_ai_unit_count": case["expected_ai_unit_count"],
                }
                for topic_family in ("pure_logic", "function_set", "induction")
                for case in by_topic[topic_family]
            ],
        }
    )


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
