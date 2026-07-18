from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.experiments.paper_experiment_contracts import (
    ExperimentSummaryRows,
    FrozenCaseSelection,
    PaperExecutionContext,
    PaperExperimentModule,
)
from tokenshare.experiments.paper_models import (
    PaperConditionResult,
    PaperStatus,
    digest_json,
)
from tokenshare.experiments.paper_model_identity import (
    PaperModelEndpointIdentity,
    build_model_endpoint_identity,
    validate_fixed_entry_config_identity,
)
from tokenshare.experiments.paper_exp1 import Exp1FormalModule


CATALOG_DIGEST = "sha256:" + "1" * 64
SOURCE_CONFIG_DIGEST = "sha256:" + "2" * 64
ENDPOINT_DIGEST = "sha256:" + "3" * 64
LEAN_ENVIRONMENT_DIGEST = "sha256:" + "6" * 64
LEAN_ORACLE_DIGEST = "sha256:" + "7" * 64


def test_exp1_formal_expands_exact_conditions_with_frozen_baseline_controls() -> None:
    module = Exp1FormalModule()
    context = _context()

    conditions = module.expand_conditions(context)

    assert isinstance(module, PaperExperimentModule)
    assert len(conditions) == 36
    assert [condition.repeat_id for condition in conditions[:12]] == [0] * 12
    assert [condition.repeat_id for condition in conditions[12:24]] == [1] * 12
    assert [condition.repeat_id for condition in conditions[24:]] == [2] * 12
    assert conditions[0].condition_id == "exp1_factorization_easy_w10_r0"
    assert conditions[3].condition_id == "exp1_lean_simple_pure_logic_w10_r0"
    assert conditions[-1].condition_id == "exp1_lean_hard_frontier_induction_w10_r2"
    assert Counter(condition.domain for condition in conditions) == {
        "factorization": 9,
        "lean_proof": 27,
    }
    assert {
        (condition.paper_difficulty, condition.topic_family)
        for condition in conditions
        if condition.domain == "lean_proof" and condition.repeat_id == 0
    } == {
        ("simple", "pure_logic"),
        ("simple", "function_set"),
        ("simple", "induction"),
        ("medium_lemma_dag", "pure_logic"),
        ("medium_lemma_dag", "function_set"),
        ("medium_lemma_dag", "induction"),
        ("hard_frontier", "pure_logic"),
        ("hard_frontier", "function_set"),
        ("hard_frontier", "induction"),
    }
    for condition in conditions:
        assert condition.experiment_id == "exp1_real_ai_feasibility"
        assert condition.worker_count == 10
        assert condition.fault_type == "none"
        assert condition.fault_rate == 0.0
        assert condition.ablation_mode == "FULL"
        assert condition.model_policy == "fixed_entry"
        assert condition.provider_config_id == "exp1_baseline_siliconflow"
        assert condition.model_entry_id == "glm_5_2_exp1_baseline"
        assert condition.provider_family == "siliconflow"
        assert condition.provider_model_id == "zai-org/GLM-5.2"
        assert condition.reasoning_profile_id == "default"
        assert condition.source_provider_config_digest == SOURCE_CONFIG_DIGEST
        assert condition.model_endpoint_identity_digest == ENDPOINT_DIGEST
        assert condition.catalog_digest == CATALOG_DIGEST


def test_exp1_formal_accepts_shared_endpoint_identity_and_effective_controls() -> None:
    module = Exp1FormalModule()
    identity = _shared_baseline_identity()
    context = _context(binding=identity)

    conditions = module.expand_conditions(context)

    assert len(conditions) == 36
    assert {condition.model_entry_id for condition in conditions} == {
        "glm_5_2_exp1_baseline"
    }
    assert {condition.model_endpoint_identity_digest for condition in conditions} == {
        identity.model_endpoint_identity_digest
    }


def test_exp1_formal_accepts_normal_validated_endpoint_binding() -> None:
    identity = _shared_baseline_identity()
    source_config = _baseline_source_config()
    binding = validate_fixed_entry_config_identity(
        expected_identity=identity,
        provider_config_id="exp1_baseline_siliconflow",
        source_config=source_config,
    )

    conditions = Exp1FormalModule().expand_conditions(_context(binding=binding))

    assert len(conditions) == 36
    assert {condition.reasoning_profile_id for condition in conditions} == {
        "default"
    }


def test_exp1_formal_freezes_165_unique_roots_and_495_root_runs_without_resampling() -> None:
    module = Exp1FormalModule()
    context = _context()
    conditions = module.expand_conditions(context)

    selections = module.freeze_case_selections(context, conditions)

    assert len(selections) == len(conditions)
    assert all(isinstance(selection, FrozenCaseSelection) for selection in selections)
    assert all(selection.is_executable for selection in selections)
    assert sum(len(selection.ordered_case_ids) for selection in selections) == 495
    assert len({case_id for selection in selections for case_id in selection.ordered_case_ids}) == 165
    assert sum(
        len(selection.ordered_case_ids)
        for selection in selections
        if selection.domain == "factorization"
    ) == 90
    assert sum(
        len(selection.ordered_case_ids)
        for selection in selections
        if selection.domain == "lean_proof"
    ) == 405

    grouped: dict[tuple[str, str, str | None], list[tuple[str, ...]]] = defaultdict(list)
    for selection in selections:
        grouped[
            (
                selection.domain,
                selection.paper_difficulty,
                selection.topic_family,
            )
        ].append(tuple(selection.ordered_case_ids))
    assert len(grouped) == 12
    for key, repeated_ids in grouped.items():
        expected_count = 10 if key[0] == "factorization" else 15
        assert repeated_ids == [repeated_ids[0], repeated_ids[0], repeated_ids[0]]
        assert len(repeated_ids[0]) == expected_count


def test_exp1_formal_consumes_task14_selected_lean_ids_instead_of_catalog_pools() -> None:
    module = Exp1FormalModule()
    context = _context(catalog=_FakeCatalog(extra_lean_pool=True))
    conditions = module.expand_conditions(context)

    selections = module.freeze_case_selections(context, conditions)

    assert all(selection.is_executable for selection in selections)
    assert sum(len(selection.ordered_case_ids) for selection in selections) == 495
    assert len({case_id for selection in selections for case_id in selection.ordered_case_ids}) == 165
    simple_pure = next(
        selection
        for selection in selections
        if selection.domain == "lean_proof"
        and selection.paper_difficulty == "simple"
        and selection.topic_family == "pure_logic"
    )
    hard_function_set = next(
        selection
        for selection in selections
        if selection.domain == "lean_proof"
        and selection.paper_difficulty == "hard_frontier"
        and selection.topic_family == "function_set"
    )
    assert tuple(simple_pure.ordered_case_ids) == tuple(
        context.catalog.task15_budget_input["selected_case_ids_by_cell"][
            "simple/pure_logic"
        ]
    )
    assert tuple(hard_function_set.ordered_case_ids) == tuple(
        context.catalog.task15_budget_input["selected_case_ids_by_cell"][
            "hard_frontier/function_set"
        ]
    )
    assert len(context.catalog.cases_for(
        domain="lean_proof",
        paper_difficulty="simple",
        topic_family="pure_logic",
    )) == 45
    assert len(context.catalog.cases_for(
        domain="lean_proof",
        paper_difficulty="hard_frontier",
        topic_family="function_set",
    )) == 25


def test_exp1_formal_real_catalog_probe_generates_495_root_runs() -> None:
    module = Exp1FormalModule()
    context = _context(catalog=_RealCatalogProbe())
    conditions = module.expand_conditions(context)

    selections = module.freeze_case_selections(context, conditions)

    assert all(selection.is_executable for selection in selections)
    assert sum(len(selection.ordered_case_ids) for selection in selections) == 495
    assert len({case_id for selection in selections for case_id in selection.ordered_case_ids}) == 165
    assert len(context.catalog.cases_for(
        domain="lean_proof",
        paper_difficulty="simple",
        topic_family="pure_logic",
    )) == 45
    for topic_family in ("pure_logic", "function_set", "induction"):
        assert len(context.catalog.cases_for(
            domain="lean_proof",
            paper_difficulty="hard_frontier",
            topic_family=topic_family,
        )) == 25


def test_exp1_formal_blocks_lean_before_provider_when_readiness_is_missing() -> None:
    calls: list[str] = []
    module = Exp1FormalModule()
    context = _context(
        catalog=_FakeCatalog(lean_semantic_ready=False, missing_lean_cell=True),
        callback=_forbidden_callback(calls),
    )
    conditions = module.expand_conditions(context)

    selections = module.freeze_case_selections(context, conditions)
    lean_selections = [selection for selection in selections if selection.domain == "lean_proof"]
    blocked = [selection for selection in lean_selections if selection.is_blocked]

    assert len(blocked) == 27
    assert {selection.blocked_reason for selection in blocked} == {
        "lean_semantic_readiness_not_passed"
    }
    assert all(selection.ordered_case_ids == () for selection in blocked)
    assert all(selection.expected_ai_unit_count == 0 for selection in blocked)
    assert all(selection.to_dict()["provider_calls_made"] == 0 for selection in blocked)

    condition = next(condition for condition in conditions if condition.domain == "lean_proof")
    selection = next(
        selection
        for selection in selections
        if selection.selection_id.startswith(condition.condition_id)
    )
    result = module.run_condition(context, condition, selection)

    assert result.status == PaperStatus.BLOCKED
    assert result.provider_attempt_count == 0
    assert calls == []


def test_exp1_formal_blocks_incomplete_task14_readiness_before_provider() -> None:
    module = Exp1FormalModule()
    context = _context(
        catalog=_FakeCatalog(incomplete_task15_budget_input=True),
    )
    conditions = module.expand_conditions(context)

    selections = module.freeze_case_selections(context, conditions)
    lean_selections = [selection for selection in selections if selection.domain == "lean_proof"]

    assert len(lean_selections) == 27
    assert all(selection.is_blocked for selection in lean_selections)
    assert {selection.blocked_reason for selection in lean_selections} == {
        "lean_semantic_readiness_not_passed"
    }
    assert sum(len(selection.ordered_case_ids) for selection in selections) == 90


def test_exp1_formal_rejects_task14_selection_order_tamper_with_stale_digest() -> None:
    module = Exp1FormalModule()
    context = _context(catalog=_FakeCatalog(tamper_task14_selection_order=True))

    conditions = module.expand_conditions(context)
    selections = module.freeze_case_selections(context, conditions)

    lean_selections = [selection for selection in selections if selection.domain == "lean_proof"]
    assert len(lean_selections) == 27
    assert all(selection.is_blocked for selection in lean_selections)
    assert {selection.blocked_reason for selection in lean_selections} == {
        "lean_semantic_readiness_not_passed"
    }


def test_exp1_formal_blocks_duplicate_task14_roots_and_rejects_condition_drift() -> None:
    module = Exp1FormalModule()
    duplicate_context = _context(catalog=_FakeCatalog(duplicate_selected_case=True))
    duplicate_selections = module.freeze_case_selections(
        duplicate_context,
        module.expand_conditions(duplicate_context),
    )

    lean_selections = [
        selection
        for selection in duplicate_selections
        if selection.domain == "lean_proof"
    ]
    assert len(lean_selections) == 27
    assert all(selection.is_blocked for selection in lean_selections)
    assert all(selection.expected_ai_unit_count == 0 for selection in lean_selections)
    assert {selection.blocked_reason for selection in lean_selections} == {
        "lean_semantic_readiness_not_passed"
    }

    context = _context()
    conditions = module.expand_conditions(context)
    with pytest.raises(ValueError, match="condition order drift"):
        module.freeze_case_selections(context, tuple(reversed(conditions)))
    drifted_repeat = (replace(conditions[0], repeat_id=99),) + conditions[1:]
    with pytest.raises(ValueError, match="condition order drift"):
        module.freeze_case_selections(context, drifted_repeat)


@pytest.mark.parametrize(
    "binding_drift",
    [
        {"provider_config_id": "wrong_provider_config"},
        {"model_entry_id": None},
        {"model_entry_id": "wrong-entry"},
        {"provider_family": "openai"},
        {"provider_model_id": "wrong-model"},
        {"reasoning_profile_id": "temperature1_thinking_true"},
    ],
)
def test_exp1_formal_rejects_complete_baseline_identity_drift(
    binding_drift: dict[str, Any],
) -> None:
    module = Exp1FormalModule()

    with pytest.raises(ValueError, match="GLM-5.2 baseline"):
        module.expand_conditions(_context(binding={**_baseline_binding(), **binding_drift}))


@pytest.mark.parametrize(
    "request_limits",
    [
        {"temperature": 0.7, "enable_thinking": False},
        {"temperature": 0.0, "enable_thinking": True},
    ],
)
def test_exp1_formal_rejects_request_control_drift(
    request_limits: dict[str, Any],
) -> None:
    module = Exp1FormalModule()

    with pytest.raises(ValueError, match="GLM-5.2 baseline"):
        module.expand_conditions(_context(request_limits=request_limits))


def test_exp1_formal_rejects_binding_request_control_drift_even_when_context_limits_match() -> None:
    module = Exp1FormalModule()
    binding = {
        **_baseline_binding(),
        "request_controls": {"temperature": 0.9, "enable_thinking": True},
    }

    with pytest.raises(ValueError, match="GLM-5.2 baseline"):
        module.expand_conditions(
            _context(
                binding=binding,
                request_limits={"temperature": 0.0, "enable_thinking": False},
            )
        )


def test_exp1_formal_rejects_non_reasoning_request_control_drift_before_callback() -> None:
    calls: list[str] = []
    module = Exp1FormalModule()
    request_limits = _baseline_request_controls()
    request_limits.update({"max_tokens": 1, "timeout_seconds": 1})

    with pytest.raises(ValueError, match="request controls"):
        module.expand_conditions(
            _context(
                request_limits=request_limits,
                callback=_forbidden_callback(calls),
            )
        )

    assert calls == []


def test_exp1_formal_run_condition_consumes_the_matching_frozen_selection() -> None:
    calls: list[tuple[str, str]] = []

    def callback(**kwargs: Any) -> PaperConditionResult:
        condition = kwargs["condition"]
        selection = kwargs["selection"]
        calls.append((condition.condition_id, selection.selection_id))
        return PaperConditionResult(
            condition_id=condition.condition_id,
            status=PaperStatus.COMPLETED,
            repeat_count=1,
            task_count=len(selection.ordered_case_ids),
            completed_root_count=len(selection.ordered_case_ids),
            failed_root_count=0,
            blocked_root_count=0,
            provider_attempt_count=0,
            metrics_ref={"transport_kind": "scripted", "paper_eligible": False},
        )

    module = Exp1FormalModule()
    context = _context(callback=callback)
    conditions = module.expand_conditions(context)
    selections = module.freeze_case_selections(context, conditions)
    condition = conditions[0]
    selection = selections[0]

    result = module.run_condition(context, condition, selection)

    assert result.condition_id == condition.condition_id
    assert result.task_count == 10
    assert calls == [(condition.condition_id, selection.selection_id)]
    with pytest.raises(ValueError, match="selection does not match condition"):
        module.run_condition(context, conditions[1], selection)


def test_exp1_formal_run_condition_rejects_noncanonical_condition_and_selection() -> None:
    calls: list[str] = []

    def callback(**kwargs: Any) -> PaperConditionResult:
        calls.append(kwargs["condition"].condition_id)
        return PaperConditionResult(
            condition_id=kwargs["condition"].condition_id,
            status=PaperStatus.COMPLETED,
            repeat_count=1,
            task_count=len(kwargs["selection"].ordered_case_ids),
            completed_root_count=len(kwargs["selection"].ordered_case_ids),
            failed_root_count=0,
            blocked_root_count=0,
            provider_attempt_count=0,
            metrics_ref={"transport_kind": "scripted", "paper_eligible": False},
        )

    module = Exp1FormalModule()
    context = _context(callback=callback)
    conditions = module.expand_conditions(context)
    selections = module.freeze_case_selections(context, conditions)
    condition = conditions[3]
    selection = selections[3]

    reversed_selection = replace(
        selection,
        ordered_case_ids=tuple(reversed(selection.ordered_case_ids)),
    )
    with pytest.raises(ValueError, match="canonical frozen selection"):
        module.run_condition(context, condition, reversed_selection)

    drifted_condition = replace(condition, model_entry_id="wrong-entry")
    with pytest.raises(ValueError, match="canonical formal condition"):
        module.run_condition(context, drifted_condition, selection)

    assert calls == []


def test_exp1_formal_summary_rows_cover_feasibility_metrics_and_scripted_eligibility() -> None:
    module = Exp1FormalModule()
    tasks = [
        _task("lean_case_1", "lean_proof", "simple", "pure_logic", True, True, 100, 10, 0.10),
        _task("lean_case_2", "lean_proof", "simple", "pure_logic", True, True, 200, 20, 0.20),
        _task(
            "lean_case_3",
            "lean_proof",
            "simple",
            "pure_logic",
            False,
            False,
            300,
            30,
            0.30,
            failure_kind="checker_rejected",
        ),
        _task(
            "lean_case_4",
            "lean_proof",
            "medium_lemma_dag",
            "pure_logic",
            True,
            True,
            400,
            40,
            0.40,
        ),
        _task("factor_easy_1", "factorization", "easy", None, True, True, 50, 5, 0.05),
    ]

    summary = module.summarize({"task_results": tasks})
    rows = {(row["domain"], row["paper_difficulty"], row["topic_family"]): row for row in summary.rows}
    simple = rows[("lean_proof", "simple", "pure_logic")]

    assert isinstance(summary, ExperimentSummaryRows)
    assert simple["case_count"] == 3
    assert simple["root_run_count"] == 3
    assert simple["completion_rate"] == pytest.approx(2 / 3)
    assert simple["accepted_validity_rate"] == pytest.approx(2 / 3)
    assert simple["wall_clock_median_ms"] == 200
    assert simple["wall_clock_p90_ms"] == 300
    assert simple["total_tokens_median"] == 20
    assert simple["total_tokens_p90"] == 30
    assert simple["cost_per_completed_task"] == pytest.approx(0.30)
    assert simple["failure_breakdown"] == [{"failure_kind": "checker_rejected", "count": 1}]
    assert simple["highest_observed_valid_completion_difficulty"] == "medium_lemma_dag"
    assert rows[("factorization", "easy", None)][
        "highest_observed_valid_completion_difficulty"
    ] == "easy"

    with pytest.raises(ValueError, match="scripted transport cannot be paper eligible"):
        module.summarize(
            {
                "task_results": [
                    {
                        **tasks[0],
                        "transport_kind": "scripted",
                        "paper_eligible": True,
                    }
                ]
            }
        )


def test_exp1_formal_summary_rejects_pilot_records_even_when_real_transport_eligible() -> None:
    module = Exp1FormalModule()

    with pytest.raises(ValueError, match="pilot output cannot enter formal"):
        module.summarize(
            {
                "task_results": [
                    {
                        **_task(
                            "lean_pilot_case",
                            "lean_proof",
                            "simple",
                            "pure_logic",
                            True,
                            True,
                            100,
                            10,
                            0.1,
                        ),
                        "transport_kind": "ai_api",
                        "paper_eligible": True,
                        "pilot_only": True,
                    }
                ]
            }
        )


def test_exp1_formal_summary_requires_complete_canonical_formal_inventory() -> None:
    evidence = _integration_summary_evidence(
        _context(binding=_shared_baseline_identity())
    )
    evidence["task_metrics"] = evidence["task_metrics"][:-1]

    with pytest.raises(ValueError, match="495 root-runs"):
        Exp1FormalModule().summarize(evidence)


def test_exp1_formal_summary_accepts_exact_165_by_3_real_evidence_matrix() -> None:
    summary = Exp1FormalModule().summarize(
        _integration_summary_evidence(_context(binding=_shared_baseline_identity()))
    )

    assert len(summary.rows) == 12
    assert sum(int(row["case_count"]) for row in summary.rows) == 165
    assert sum(int(row["root_run_count"]) for row in summary.rows) == 495
    assert {int(row["repeat_count"]) for row in summary.rows} == {3}
    assert all(row["paper_eligible"] is True for row in summary.rows)


def test_exp1_formal_summary_accepts_integration_prepared_task_metrics() -> None:
    summary = Exp1FormalModule().summarize(
        _integration_summary_evidence(_context(binding=_shared_baseline_identity()))
    )

    assert len(summary.rows) == 12
    assert sum(int(row["case_count"]) for row in summary.rows) == 165
    assert sum(int(row["root_run_count"]) for row in summary.rows) == 495
    assert {int(row["repeat_count"]) for row in summary.rows} == {3}
    assert all(row["transport_kind"] == "ai_api" for row in summary.rows)
    assert all(row["paper_eligible"] is True for row in summary.rows)


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        (
            lambda evidence: evidence["conditions"][0].update(
                {"condition_digest": "sha256:" + "0" * 64}
            ),
            "condition digest mismatch",
        ),
        (
            lambda evidence: evidence["selections"][0].update(
                {
                    "ordered_case_ids": list(
                        reversed(evidence["selections"][0]["ordered_case_ids"])
                    )
                }
            ),
            "selection_digest mismatch",
        ),
    ],
)
def test_exp1_formal_summary_rejects_accidental_plan_inventory_drift(
    tamper: Any,
    message: str,
) -> None:
    evidence = _integration_summary_evidence(
        _context(binding=_shared_baseline_identity())
    )
    tamper(evidence)

    with pytest.raises(ValueError, match=message):
        Exp1FormalModule().summarize(evidence)


def test_exp1_formal_summary_rejects_integration_prepared_pilot_metrics() -> None:
    evidence = _integration_summary_evidence(
        _context(binding=_shared_baseline_identity())
    )
    evidence.update({"formal": False, "pilot_only": True})
    for task in evidence["task_metrics"]:
        task["pilot_only"] = True

    with pytest.raises(ValueError, match="pilot output"):
        Exp1FormalModule().summarize(evidence)


def test_exp1_formal_summary_keeps_scripted_shared_metrics_ineligible() -> None:
    evidence = _integration_summary_evidence(
        _context(binding=_shared_baseline_identity())
    )
    evidence["paper_eligible"] = False
    for task in evidence["task_metrics"]:
        task.update({"paper_eligible": False, "transport_kind": "scripted"})

    summary = Exp1FormalModule().summarize(evidence)

    assert summary.rows
    assert all(row["paper_eligible"] is False for row in summary.rows)


def test_exp1_formal_summary_applies_task_ineligibility_to_the_whole_matrix() -> None:
    evidence = _integration_summary_evidence(
        _context(binding=_shared_baseline_identity())
    )
    evidence["task_metrics"][0]["paper_eligible"] = False

    summary = Exp1FormalModule().summarize(evidence)

    assert summary.rows
    assert all(row["paper_eligible"] is False for row in summary.rows)


def test_exp1_formal_summary_preserves_normal_budget_exhausted_metrics() -> None:
    evidence = _integration_summary_evidence(
        _context(binding=_shared_baseline_identity())
    )
    evidence["paper_eligible"] = False
    task = evidence["task_metrics"][0]
    task.update(
        {
            "root_status": "budget_exhausted",
            "completed": False,
            "accepted_validity": False,
            "failure_stage": None,
            "failure_kind": "budget_limit",
            "attempt_count": 0,
            "provider_attempt_count": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "provider_latency_ms": 0,
            "cost_estimate": 0.0,
            "artifact_ref_count": 0,
            "paper_eligible": False,
        }
    )

    summary = Exp1FormalModule().summarize(evidence)

    easy = next(
        row
        for row in summary.rows
        if row["domain"] == "factorization"
        and row["paper_difficulty"] == "easy"
    )
    assert easy["failure_breakdown"] == [
        {"failure_kind": "budget_limit", "count": 1}
    ]
    assert all(row["paper_eligible"] is False for row in summary.rows)


def test_exp1_formal_summary_rejects_non_finite_integration_metric() -> None:
    evidence = _integration_summary_evidence(
        _context(binding=_shared_baseline_identity())
    )
    evidence["task_metrics"][0]["wall_clock_ms"] = float("nan")

    with pytest.raises(ValueError, match="wall_clock_ms must be finite"):
        Exp1FormalModule().summarize(evidence)


@pytest.mark.parametrize("catalog_version", [None, "unsupported-v2"])
def test_exp1_formal_requires_explicit_supported_catalog_version(
    catalog_version: str | None,
) -> None:
    module = Exp1FormalModule()
    catalog = _FakeCatalog()
    catalog.catalog_version = catalog_version
    context = _context(catalog=catalog)

    with pytest.raises(ValueError, match="catalog_version .*required|supported"):
        module.freeze_case_selections(context, module.expand_conditions(context))


def _context(
    *,
    catalog: Any | None = None,
    binding: Any | None = None,
    request_limits: dict[str, Any] | None = None,
    callback: Any | None = None,
) -> PaperExecutionContext:
    return PaperExecutionContext(
        context_id="exp1_formal_test",
        catalog=catalog or _FakeCatalog(),
        approved_endpoint_binding=binding or _baseline_binding(),
        request_limits=request_limits or _baseline_request_controls(),
        hard_limits={"max_total_provider_attempts": 0},
        output_root="outputs/experiments/exp1_formal_test",
        artifact_store=object(),
        event_store=object(),
        execution_callback=callback or _forbidden_callback([]),
    )


def _baseline_binding() -> dict[str, Any]:
    return {
        "provider_config_id": "exp1_baseline_siliconflow",
        "selected_entry_id": "glm_5_2_exp1_baseline",
        "model_entry_id": "glm_5_2_exp1_baseline",
        "provider_family": "siliconflow",
        "provider_model_id": "zai-org/GLM-5.2",
        "reasoning_profile_id": "default",
        "source_provider_config_digest": SOURCE_CONFIG_DIGEST,
        "model_endpoint_identity_digest": ENDPOINT_DIGEST,
        "request_controls": _baseline_request_controls(),
    }


def _baseline_request_controls() -> dict[str, Any]:
    return {
        "max_tokens": 1024,
        "timeout_seconds": 30,
        "max_provider_attempts": 1,
        "temperature": 0.0,
        "top_p": 1.0,
        "stream": False,
        "enable_thinking": False,
    }


def _shared_baseline_identity() -> PaperModelEndpointIdentity:
    source_config = _baseline_source_config()
    return build_model_endpoint_identity(
        model_cohort_id="exp1_to_exp4_glm_baseline",
        model_cohort_digest="sha256:" + "8" * 64,
        cohort_member_id="exp1_glm_5_2_siliconflow_default_v1",
        provider_config_id="exp1_baseline_siliconflow",
        selected_entry_id="glm_5_2_exp1_baseline",
        expected_provider_family="siliconflow",
        expected_provider_model_id="zai-org/GLM-5.2",
        expected_reasoning_profile_id="default",
        source_config=source_config,
    )


def _baseline_source_config():
    config_path = (
        Path(__file__).resolve().parents[2]
        / "benchmarks"
        / "paper"
        / "exp1_baseline_provider_config.v1.json"
    )
    return load_ai_api_config(json.loads(config_path.read_text(encoding="utf-8")))


def _integration_summary_evidence(
    context: PaperExecutionContext,
) -> dict[str, Any]:
    module = Exp1FormalModule()
    conditions = module.expand_conditions(context)
    selections = module.freeze_case_selections(context, conditions)
    task_metrics: list[dict[str, Any]] = []
    for condition, selection in zip(conditions, selections, strict=True):
        for case_id in selection.ordered_case_ids:
            task_metrics.append(
                {
                    "schema_version": "tokenshare.paper_task_metrics.v1",
                    "condition_id": condition.condition_id,
                    "run_id": f"{condition.condition_id}_{case_id}",
                    "case_id": case_id,
                    "task_id": f"task_{case_id}_r{condition.repeat_id}",
                    "domain": condition.domain,
                    "difficulty": condition.difficulty,
                    "paper_difficulty": condition.paper_difficulty,
                    "topic_family": condition.topic_family,
                    "execution_status": "executable",
                    "root_status": "completed",
                    "attempted": True,
                    "completed": True,
                    "accepted_validity": True,
                    "failure_stage": None,
                    "failure_kind": None,
                    "attempt_count": 1,
                    "provider_attempt_count": 1,
                    "parser_failure_count": 0,
                    "verifier_rejection_count": 0,
                    "checker_rejection_count": 0,
                    "provider_error_count": 0,
                    "prompt_tokens": 4,
                    "completion_tokens": 6,
                    "total_tokens": 10,
                    "provider_latency_ms": 50,
                    "wall_clock_ms": 100,
                    "cost_estimate": 0.01,
                    "artifact_ref_count": 6,
                    "paper_eligible": True,
                    "pilot_only": False,
                }
            )
    return {
        "schema_version": "tokenshare.paper_exp1_summary_input.v1",
        "experiment_id": "exp1_real_ai_feasibility",
        "formal": True,
        "pilot_only": False,
        "paper_eligible": True,
        "catalog_digest": CATALOG_DIGEST,
        "catalog_version": "v1",
        "suite_version": "paper_v1",
        "conditions": [condition.to_dict() for condition in conditions],
        "selections": [selection.to_dict() for selection in selections],
        "task_metrics": task_metrics,
    }


class _FakeCatalog:
    catalog_digest = CATALOG_DIGEST
    catalog_version = "v1"
    lean_semantic_readiness_passed: bool
    task15_budget_input: dict[str, Any]

    def __init__(
        self,
        *,
        lean_semantic_ready: bool = True,
        missing_lean_cell: bool = False,
        duplicate_selected_case: bool = False,
        extra_lean_pool: bool = False,
        incomplete_task15_budget_input: bool = False,
        tamper_task14_selection_order: bool = False,
    ) -> None:
        self.lean_semantic_readiness_passed = lean_semantic_ready
        self._cases: list[dict[str, Any]] = []
        for difficulty, ai_units in (("easy", 1), ("medium", 2), ("hard", 3)):
            for index in range(1, 11):
                case_id = f"factor_{difficulty}_{index:02d}"
                self._cases.append(
                    {
                        "case_id": case_id,
                        "domain": "factorization",
                        "difficulty": difficulty,
                        "paper_difficulty": difficulty,
                        "topic_family": None,
                        "expected_ai_unit_count": ai_units,
                    }
                )
        lean_difficulty = {
            "simple": "easy",
            "medium_lemma_dag": "medium",
            "hard_frontier": "hard",
        }
        for paper_difficulty, ai_units in (
            ("simple", 2),
            ("medium_lemma_dag", 5),
            ("hard_frontier", 6),
        ):
            for topic_family in ("pure_logic", "function_set", "induction"):
                if missing_lean_cell and (
                    paper_difficulty,
                    topic_family,
                ) == ("hard_frontier", "function_set"):
                    continue
                selected_ids: list[str] = []
                for index in range(1, 16):
                    case_id = f"lean_{paper_difficulty}_{topic_family}_{index:02d}"
                    selected_ids.append(case_id)
                    self._cases.append(
                        {
                            "case_id": case_id,
                            "domain": "lean_proof",
                            "difficulty": lean_difficulty[paper_difficulty],
                            "paper_difficulty": paper_difficulty,
                            "topic_family": topic_family,
                            "topic_family_version": "synthetic_fixture",
                            "construction_rule_id": (
                                f"synthetic.{paper_difficulty}.{topic_family}.v1"
                            ),
                            "oracle_package_group": f"synthetic.{topic_family}.v1",
                            "proof_assembly_shape": "synthetic_fixture.v1",
                            "expected_ai_unit_count": ai_units,
                        }
                    )
                if extra_lean_pool:
                    extra_count = (
                        30
                        if (paper_difficulty, topic_family) == ("simple", "pure_logic")
                        else 10
                        if paper_difficulty == "hard_frontier"
                        else 0
                    )
                    for index in range(16, 16 + extra_count):
                        self._cases.append(
                            {
                                **self._cases[-1],
                                "case_id": (
                                    f"lean_{paper_difficulty}_{topic_family}"
                                    f"_extra_{index:02d}"
                                ),
                            }
                        )
        selected_case_ids_by_cell = {
            f"{paper_difficulty}/{topic_family}": [
                f"lean_{paper_difficulty}_{topic_family}_{index:02d}"
                for index in range(1, 16)
            ]
            for paper_difficulty in ("simple", "medium_lemma_dag", "hard_frontier")
            for topic_family in ("pure_logic", "function_set", "induction")
        }
        if duplicate_selected_case:
            selected_case_ids_by_cell["hard_frontier/induction"][-1] = (
                "lean_simple_pure_logic_01"
            )
        matrix_plan = _fake_task14_matrix_plan(selected_case_ids_by_cell)
        self.task14_matrix_plan = matrix_plan
        self.task15_budget_input = dict(matrix_plan["task15_budget_input"])
        if tamper_task14_selection_order:
            tampered_by_cell = {
                key: list(value)
                for key, value in self.task15_budget_input[
                    "selected_case_ids_by_cell"
                ].items()
            }
            tampered_by_cell["simple/pure_logic"] = list(
                reversed(tampered_by_cell["simple/pure_logic"])
            )
            self.task15_budget_input["selected_case_ids_by_cell"] = tampered_by_cell
        if incomplete_task15_budget_input:
            self.task15_budget_input["case_counts_by_cell"] = {}

    def cases_for(
        self,
        *,
        domain: str,
        difficulty: str | None = None,
        paper_difficulty: str | None = None,
        topic_family: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        return tuple(
            case
            for case in self._cases
            if case["domain"] == domain
            and (difficulty is None or case["difficulty"] == difficulty)
            and (
                paper_difficulty is None
                or case["paper_difficulty"] == paper_difficulty
            )
            and (topic_family is None or case["topic_family"] == topic_family)
        )


_LEAN_TEST_CELL_KEYS = tuple(
    f"{paper_difficulty}/{topic_family}"
    for paper_difficulty in ("simple", "medium_lemma_dag", "hard_frontier")
    for topic_family in ("pure_logic", "function_set", "induction")
)


def _fake_task14_matrix_plan(
    selected_case_ids_by_cell: dict[str, list[str]],
) -> dict[str, Any]:
    semantic_fingerprints_by_cell = {
        cell_key: [
            digest_json(
                {
                    "schema_version": "tokenshare.fake_lean_semantic.v1",
                    "case_id": case_id,
                }
            )
            for case_id in case_ids
        ]
        for cell_key, case_ids in selected_case_ids_by_cell.items()
    }
    golden_case_ids_by_cell = {
        cell_key: list(case_ids[:2])
        for cell_key, case_ids in selected_case_ids_by_cell.items()
    }
    oracle_package_digests = {
        f"synthetic.{topic_family}.v1": LEAN_ORACLE_DIGEST
        for topic_family in ("pure_logic", "function_set", "induction")
    }
    topic_family_expected_ai_unit_counts = {
        topic_family: 0
        for topic_family in ("pure_logic", "function_set", "induction")
    }
    topic_family_available_case_counts = {
        topic_family: 0
        for topic_family in ("pure_logic", "function_set", "induction")
    }
    ai_units_by_difficulty = {
        "simple": 2,
        "medium_lemma_dag": 5,
        "hard_frontier": 6,
    }
    cells: list[dict[str, Any]] = []
    for cell_key in _LEAN_TEST_CELL_KEYS:
        paper_difficulty, topic_family = cell_key.split("/", 1)
        case_ids = list(selected_case_ids_by_cell[cell_key])
        semantic_fingerprints = semantic_fingerprints_by_cell[cell_key]
        expected_ai_unit_count = (
            ai_units_by_difficulty[paper_difficulty] * len(case_ids)
        )
        topic_family_expected_ai_unit_counts[topic_family] += expected_ai_unit_count
        topic_family_available_case_counts[topic_family] += len(case_ids)
        cells.append(
            {
                "schema_version": "tokenshare.lean_3x3_matrix_cell_plan.v1",
                "domain": "lean_proof",
                "status": "planned",
                "paper_difficulty": paper_difficulty,
                "topic_family": topic_family,
                "catalog_case_count": len(case_ids),
                "catalog_pool_case_count": len(case_ids),
                "checker_backed_pool_case_count": len(case_ids),
                "available_case_count": len(case_ids),
                "target_case_count": 15,
                "expected_ai_unit_count": expected_ai_unit_count,
                "preflight_status": {
                    "summary": "passed",
                    "status_counts": {"passed": len(case_ids)},
                    "checker_backed_case_count": len(case_ids),
                },
                "paper_eligible_possible": True,
                "blocked_reason": None,
                "semantic_fingerprint_count": len(set(semantic_fingerprints)),
                "semantic_fingerprint_digest": digest_json(semantic_fingerprints),
                "semantic_fingerprint_digests": semantic_fingerprints,
                "golden_case_ids": golden_case_ids_by_cell[cell_key],
                "golden_evidence_by_case_id": {
                    golden_case_id: _fake_task14_golden_evidence(golden_case_id)
                    for golden_case_id in golden_case_ids_by_cell[cell_key]
                },
                "case_ids": case_ids,
                "oracle_package_digest": LEAN_ORACLE_DIGEST,
                "readiness": {
                    "blocker": None,
                    "dependency_aware_merge": "ready",
                    "deterministic_split_rule": "ready",
                    "golden_evidence": "passed",
                    "local_checker_preflight": "passed",
                    "oracle_package": "present",
                    "proof_file_assembly": "ready",
                    "provider_calls_made": 0,
                    "root_recheck": "ready",
                },
            }
        )
    selection_digest = digest_json(
        {
            "schema_version": "tokenshare.lean_task14_selected_cases.v1",
            "catalog_digest": CATALOG_DIGEST,
            "environment_digest": LEAN_ENVIRONMENT_DIGEST,
            "oracle_package_digests": oracle_package_digests,
            "target_case_count": 15,
            "selected_case_ids_by_cell": selected_case_ids_by_cell,
            "semantic_fingerprint_digests_by_cell": semantic_fingerprints_by_cell,
            "golden_case_ids_by_cell": golden_case_ids_by_cell,
        }
    )
    body: dict[str, Any] = {
        "schema_version": "tokenshare.lean_3x3_matrix_plan.v1",
        "domain": "lean_proof",
        "catalog_digest": CATALOG_DIGEST,
        "environment_digest": LEAN_ENVIRONMENT_DIGEST,
        "oracle_package_digests": oracle_package_digests,
        "provider_calls_made": 0,
        "cell_count": len(cells),
        "target_case_count": 15,
        "cells": cells,
        "topic_family_expected_ai_unit_counts": topic_family_expected_ai_unit_counts,
        "topic_family_available_case_counts": topic_family_available_case_counts,
        "paper_eligible_possible_cell_count": len(cells),
        "blocked_cell_count": 0,
        "task15_boundary": {
            "formal_exp1_started": False,
            "exp2_to_exp5_started": False,
            "real_ai_api_calls_allowed": False,
            "provider_calls_made": 0,
        },
        "task15_budget_input": {
            "schema_version": "tokenshare.lean_task15_budget_input.v1",
            "catalog_digest": CATALOG_DIGEST,
            "environment_digest": LEAN_ENVIRONMENT_DIGEST,
            "oracle_package_digests": oracle_package_digests,
            "target_case_count": 15,
            "executable_cell_count": len(cells),
            "blocked_cell_count": 0,
            "expected_ai_unit_count": sum(
                topic_family_expected_ai_unit_counts.values()
            ),
            "selected_case_count": sum(
                len(case_ids) for case_ids in selected_case_ids_by_cell.values()
            ),
            "selected_case_ids_by_cell": selected_case_ids_by_cell,
            "semantic_fingerprint_digests_by_cell": semantic_fingerprints_by_cell,
            "golden_case_ids_by_cell": golden_case_ids_by_cell,
            "case_counts_by_cell": {
                cell_key: len(case_ids)
                for cell_key, case_ids in selected_case_ids_by_cell.items()
            },
            "selection_digest": selection_digest,
            "catalog_slice_digest": selection_digest,
            "construction_sampling_rules": {
                "selection_rule": (
                    "stable catalog order; first target_case_count checker-backed "
                    "preflight-passed cases per paper_difficulty/topic_family cell"
                ),
                "simple_pure_logic_rule": (
                    "legacy shallow Lean v1 rows remain simple/pure_logic and are "
                    "sliced to exactly target_case_count"
                ),
                "blocked_rows_counted": False,
                "provider_calls_made": 0,
            },
            "provider_calls_made": 0,
        },
    }
    body["matrix_digest"] = digest_json(_fake_task14_matrix_digest_body(body))
    body["task15_budget_input"]["matrix_digest"] = body["matrix_digest"]
    return body


def _fake_task14_golden_evidence(case_id: str) -> dict[str, Any]:
    return {
        "schema_version": "tokenshare.lean_task14_golden_evidence.v1",
        "evidence_source": "synthetic_fixture",
        "case_id": case_id,
        "environment_digest": LEAN_ENVIRONMENT_DIGEST,
        "oracle_package_digest": LEAN_ORACLE_DIGEST,
        "split_certificate_digest": digest_json({"split": case_id}),
        "deterministic_split": "passed",
        "child_proof_file_construction": "passed",
        "checker_preflight": "passed",
        "dependency_aware_merge": "passed",
        "root_recheck": "passed",
        "provider_calls_made": 0,
        "node_checker_report_refs": {
            f"{case_id}_node": {
                "artifact_id": f"{case_id}_node_checker",
            }
        },
    }


def _fake_task14_matrix_digest_body(body: dict[str, Any]) -> dict[str, Any]:
    digest_body = {
        key: value for key, value in body.items() if key != "matrix_digest"
    }
    digest_body["cells"] = [
        _fake_task14_cell_digest_projection(cell) for cell in body["cells"]
    ]
    return digest_body


def _fake_task14_cell_digest_projection(cell: dict[str, Any]) -> dict[str, Any]:
    projected = {
        key: value
        for key, value in cell.items()
        if key != "golden_evidence_by_case_id"
    }
    projected["golden_evidence_digest_by_case_id"] = {
        case_id: digest_json(_fake_task14_golden_evidence_digest_body(evidence))
        for case_id, evidence in sorted(
            dict(cell.get("golden_evidence_by_case_id", {})).items()
        )
    }
    return projected


def _fake_task14_golden_evidence_digest_body(evidence: dict[str, Any]) -> dict[str, Any]:
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


class _RealCatalogProbe:
    catalog_version = "v1"

    def __init__(self) -> None:
        readiness = json.loads(
            Path("benchmarks/paper/lean_task14_3x3_readiness.v1.json").read_text(
                encoding="utf-8"
            )
        )
        self.task14_matrix_plan = readiness
        self.catalog_digest = readiness["task15_budget_input"]["catalog_digest"]
        self.task15_budget_input = readiness["task15_budget_input"]
        self._cases: list[dict[str, Any]] = []
        for path in (
            Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
            Path("benchmarks/paper/lean_catalog.v1.jsonl"),
            Path("benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl"),
        ):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                case = json.loads(line)
                if path.name == "factorization_catalog.v1.jsonl":
                    case["domain"] = "factorization"
                    case.setdefault("paper_difficulty", case["difficulty"])
                else:
                    case["domain"] = "lean_proof"
                    if path.name == "lean_catalog.v1.jsonl":
                        case["paper_difficulty"] = "simple"
                        case["topic_family"] = "pure_logic"
                self._cases.append(case)

    def cases_for(
        self,
        *,
        domain: str,
        difficulty: str | None = None,
        paper_difficulty: str | None = None,
        topic_family: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        return tuple(
            case
            for case in self._cases
            if case["domain"] == domain
            and (difficulty is None or case["difficulty"] == difficulty)
            and (
                paper_difficulty is None
                or case.get("paper_difficulty") == paper_difficulty
            )
            and (topic_family is None or case.get("topic_family") == topic_family)
        )


def _forbidden_callback(calls: list[str]):
    def callback(**kwargs: Any) -> PaperConditionResult:
        calls.append(kwargs["condition"].condition_id)
        raise AssertionError("provider-backed execution callback must not be called")

    return callback


def _task(
    case_id: str,
    domain: str,
    paper_difficulty: str,
    topic_family: str | None,
    completed: bool,
    accepted_validity: bool,
    wall_clock_ms: int,
    total_tokens: int,
    cost_estimate: float,
    *,
    failure_kind: str | None = None,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "task_id": f"task_{case_id}",
        "domain": domain,
        "paper_difficulty": paper_difficulty,
        "topic_family": topic_family,
        "root_status": "completed" if completed else "failed",
        "accepted_validity": accepted_validity,
        "failure_kind": failure_kind,
        "wall_clock_ms": wall_clock_ms,
        "total_tokens": total_tokens,
        "cost_estimate": cost_estimate,
        "transport_kind": "scripted",
        "paper_eligible": False,
    }
