from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import importlib
import importlib.util
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tokenshare.experiments.paper_catalog import load_paper_catalogs
from tokenshare.experiments.paper_experiment_contracts import (
    PaperExecutionContext,
    PaperExperimentModule,
)
from tokenshare.experiments.paper_model_identity import PaperModelEndpointIdentity
from tokenshare.experiments.paper_model_policy import (
    PAPER_MODEL_ENDPOINT_COHORT_ID,
    PAPER_MODEL_ENDPOINT_COHORT_MEMBER_IDS,
    PAPER_MODEL_ENDPOINT_COHORT_MEMBERS,
    PAPER_MODEL_ENDPOINT_COHORT_V3_ID,
    PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS,
    PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBERS,
)
from tokenshare.experiments.paper_models import (
    PaperAttemptResult,
    PaperConditionResult,
    PaperModelExecutionRecord,
    PaperStatus,
    PaperTaskResult,
    PaperTaskStatus,
    digest_json,
)
from tokenshare.experiments.paper_runner import _bind_lean_matrix_to_catalog


MODULE_NAME = "tokenshare.experiments.paper_exp5_model_comparison"
CATALOG_DIGEST = "sha256:" + "1" * 64
COHORT_DIGEST = "sha256:" + "2" * 64
_MISSING = object()


def test_exp5_v3_selection_artifact_freezes_107_hard_roots() -> None:
    selection = json.loads(
        Path("benchmarks/paper/exp5_hard_half_selection.v3.json").read_text(
            encoding="utf-8"
        )
    )

    assert selection["schema_version"] == (
        "tokenshare.paper_exp5_hard_half_selection.v3"
    )
    assert selection["selection_id"] == "tokenshare.paper.exp5.hard_half.v3"
    assert selection["selection_version"] == "v3"
    assert selection["counts_by_stratum"] == {
        "factorization:hard": 83,
        "lean_proof:hard:pure_logic": 8,
        "lean_proof:hard:function_set": 8,
        "lean_proof:hard:induction": 8,
    }
    assert len(selection["ordered_case_ids"]) == 107


def test_exp5_v3_expands_48_repeat_major_conditions() -> None:
    module = _load_module()
    conditions = module.expand_exp5_v3_conditions(
        _context(binding=_cohort_preflight_v3())
    )
    expected_order = {
        0: (
            "glm_5_2_siliconflow",
            "qwen3_14b_siliconflow",
            "minimax_m2_5_siliconflow",
            "deepseek_v3_pro_siliconflow",
        ),
        1: (
            "qwen3_14b_siliconflow",
            "deepseek_v3_pro_siliconflow",
            "glm_5_2_siliconflow",
            "minimax_m2_5_siliconflow",
        ),
        2: (
            "minimax_m2_5_siliconflow",
            "glm_5_2_siliconflow",
            "deepseek_v3_pro_siliconflow",
            "qwen3_14b_siliconflow",
        ),
    }

    assert len(conditions) == 48
    cursor = 0
    for repeat_id, member_ids in expected_order.items():
        predecessor = None
        for order_slot, member_id in enumerate(member_ids, start=1):
            block = conditions[cursor : cursor + 4]
            cursor += 4
            assert [condition.domain for condition in block] == [
                "factorization",
                "lean_proof",
                "lean_proof",
                "lean_proof",
            ]
            assert [condition.topic_family for condition in block] == [
                None,
                "pure_logic",
                "function_set",
                "induction",
            ]
            assert {condition.repeat_id for condition in block} == {repeat_id}
            assert {condition.cohort_member_id for condition in block} == {
                member_id
            }
            assert {condition.worker_count for condition in block} == {3}
            assert {condition.order_slot for condition in block} == {order_slot}
            assert {condition.predecessor_member_id for condition in block} == {
                predecessor
            }
            assert all(
                condition.schema_version == "tokenshare.paper_condition.v3"
                for condition in block
            )
            predecessor = member_id


def test_exp5_v3_freezes_1284_roots_and_9888_first_attempt_units() -> None:
    module = _load_module()
    context = _context(
        catalog=_tracked_v3_catalog(),
        binding=_cohort_preflight_v3(),
    )
    conditions = module.expand_exp5_v3_conditions(context)
    selections = module.freeze_exp5_v3_case_selections(context, conditions)

    assert module.count_exp5_v3_root_runs(conditions, selections) == 1_284
    assert sum(selection.expected_ai_unit_count for selection in selections) == (
        9_888
    )
    ids_by_scope: dict[tuple[str, str], set[tuple[str, ...]]] = {}
    for condition, selection in zip(conditions, selections, strict=True):
        scope = (
            condition.domain,
            str(condition.topic_family or condition.paper_difficulty),
        )
        ids_by_scope.setdefault(scope, set()).add(selection.ordered_case_ids)
        assert condition.exp5_selection_digest == (
            "sha256:fbec153a02befa4071b4ad4d639e51e910a3bc4c433cc9eea4b19ac6489a3490"
        )
    assert {scope: len(next(iter(ids))) for scope, ids in ids_by_scope.items()} == {
        ("factorization", "hard"): 83,
        ("lean_proof", "pure_logic"): 8,
        ("lean_proof", "function_set"): 8,
        ("lean_proof", "induction"): 8,
    }
    assert all(len(ids) == 1 for ids in ids_by_scope.values())


@pytest.mark.parametrize(
    "mutation",
    ("selection_digest", "case_order", "parent_catalog_digest"),
)
def test_exp5_v3_selection_loader_rejects_identity_drift(
    tmp_path: Path,
    mutation: str,
) -> None:
    module = _load_module()
    source = Path("benchmarks/paper/exp5_hard_half_selection.v3.json")
    body = json.loads(source.read_text(encoding="utf-8"))
    if mutation == "selection_digest":
        body["selection_digest"] = "sha256:" + "0" * 64
    elif mutation == "case_order":
        body["strata"][0]["ordered_case_ids"][:2] = reversed(
            body["strata"][0]["ordered_case_ids"][:2]
        )
        body["ordered_case_ids"] = [
            case_id
            for stratum in body["strata"]
            for case_id in stratum["ordered_case_ids"]
        ]
        digest_body = dict(body)
        digest_body.pop("selection_digest")
        body["selection_digest"] = digest_json(digest_body)
    else:
        body["parent_catalog"]["catalog_digest"] = "sha256:" + "9" * 64
        digest_body = dict(body)
        digest_body.pop("selection_digest")
        body["selection_digest"] = digest_json(digest_body)
    path = tmp_path / "selection.json"
    path.write_text(json.dumps(body), encoding="utf-8")

    with pytest.raises(ValueError, match="digest|order|parent"):
        module.load_exp5_v3_selection(path, catalog=_tracked_v3_catalog())


def test_exp5_v3_rejects_condition_order_drift() -> None:
    module = _load_module()
    context = _context(
        catalog=_tracked_v3_catalog(),
        binding=_cohort_preflight_v3(),
    )
    conditions = module.expand_exp5_v3_conditions(context)

    with pytest.raises(ValueError, match="condition order or identity drift"):
        module.freeze_exp5_v3_case_selections(
            context,
            tuple(reversed(conditions)),
        )


@pytest.mark.parametrize("entrypoint", ("expand", "freeze"))
@pytest.mark.parametrize(
    "mutation",
    (
        "source_count",
        "retained_count",
        "counts_by_stratum",
        "stratum_source_count",
        "stratum_retained_count",
    ),
)
def test_exp5_public_v3_rejects_float_selection_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entrypoint: str,
    mutation: str,
) -> None:
    module = _load_module()
    calls: list[str] = []
    context = _context(
        catalog=_tracked_v3_catalog(),
        binding=_cohort_preflight_v3(),
        callback=lambda **kwargs: calls.append("called"),
    )
    canonical_conditions = module.expand_exp5_v3_conditions(context)
    body = json.loads(
        Path("benchmarks/paper/exp5_hard_half_selection.v3.json").read_text(
            encoding="utf-8"
        )
    )
    if mutation == "source_count":
        body["source_count"] = float(body["source_count"])
    elif mutation == "retained_count":
        body["retained_count"] = float(body["retained_count"])
    elif mutation == "counts_by_stratum":
        body["counts_by_stratum"]["factorization:hard"] = 83.0
    elif mutation == "stratum_source_count":
        body["strata"][0]["source_count"] = 166.0
    else:
        body["strata"][0]["retained_count"] = 83.0
    digest_body = dict(body)
    digest_body.pop("selection_digest")
    body["selection_digest"] = digest_json(digest_body)
    selection_path = tmp_path / "selection.json"
    selection_path.write_text(json.dumps(body), encoding="utf-8")
    monkeypatch.setitem(
        module.expand_exp5_v3_conditions.__kwdefaults__,
        "selection_path",
        selection_path,
    )
    monkeypatch.setitem(
        module.freeze_exp5_v3_case_selections.__kwdefaults__,
        "selection_path",
        selection_path,
    )
    drifted_conditions = tuple(
        replace(
            condition,
            exp5_selection_digest=body["selection_digest"],
        )
        for condition in canonical_conditions
    )
    experiment = module.Experiment5ModelComparisonModule()

    with pytest.raises(ValueError, match="integer|count"):
        if entrypoint == "expand":
            experiment.expand_conditions(context)
        else:
            experiment.freeze_case_selections(context, drifted_conditions)
    assert calls == []


@pytest.mark.parametrize("entrypoint", ("expand", "freeze"))
@pytest.mark.parametrize(
    ("field_name", "drifted_value"),
    (
        ("provider_calls_made", False),
        ("provider_calls_made", 0.0),
        ("timeout_seconds", 600.0),
        ("max_tokens", 32768.0),
        ("max_provider_attempts", 1.0),
        ("max_provider_attempts", True),
    ),
)
def test_exp5_public_v3_rejects_non_integer_preflight_counts_and_controls(
    entrypoint: str,
    field_name: str,
    drifted_value: object,
) -> None:
    module = _load_module()
    calls: list[str] = []
    catalog = _tracked_v3_catalog()
    valid_context = _context(
        catalog=catalog,
        binding=_cohort_preflight_v3(),
    )
    canonical_conditions = module.expand_exp5_v3_conditions(valid_context)
    binding = deepcopy(_cohort_preflight_v3())
    if field_name == "provider_calls_made":
        binding[field_name] = drifted_value
    else:
        for plan in binding["member_plans"].values():
            request_controls = plan["request_controls"]
            comparable = dict(request_controls["comparable"])
            comparable[field_name] = drifted_value
            request_controls["comparable"] = comparable
            request_controls["comparable_digest"] = digest_json(comparable)
        snapshot = dict(binding["request_controls_snapshot"])
        snapshot[field_name] = drifted_value
        binding["request_controls_snapshot"] = snapshot
        binding["request_controls_snapshot_digest"] = digest_json(snapshot)
    context = _context(
        catalog=catalog,
        binding=binding,
        callback=lambda **kwargs: calls.append("called"),
    )
    experiment = module.Experiment5ModelComparisonModule()

    with pytest.raises(ValueError, match="integer|preflight|controls"):
        if entrypoint == "expand":
            experiment.expand_conditions(context)
        else:
            experiment.freeze_case_selections(context, canonical_conditions)
    assert calls == []


def test_exp5_public_module_dispatches_v3_expansion_and_freeze() -> None:
    module = _load_module()
    context = _context(
        catalog=_tracked_v3_catalog(),
        binding=_cohort_preflight_v3(),
    )
    experiment = module.Experiment5ModelComparisonModule()

    conditions = experiment.expand_conditions(context)
    selections = experiment.freeze_case_selections(context, conditions)

    assert len(conditions) == 48
    assert len(selections) == 48
    assert all(
        condition.schema_version == "tokenshare.paper_condition.v3"
        for condition in conditions
    )
    assert {len(selection.ordered_case_ids) for selection in selections} == {
        8,
        83,
    }
    with pytest.raises(ValueError, match="condition order or identity drift"):
        experiment.freeze_case_selections(context, tuple(reversed(conditions)))


def test_exp5_public_module_runs_v3_selection_and_rejects_digest_drift() -> None:
    module = _load_module()
    calls: list[tuple[str, int]] = []

    def callback(**kwargs: Any) -> PaperConditionResult:
        condition = kwargs["condition"]
        selection = kwargs["selection"]
        calls.append((condition.domain, len(selection.ordered_case_ids)))
        return PaperConditionResult(
            condition_id=condition.condition_id,
            status=PaperStatus.BLOCKED,
            repeat_count=1,
            task_count=len(selection.ordered_case_ids),
            completed_root_count=0,
            failed_root_count=0,
            blocked_root_count=len(selection.ordered_case_ids),
            provider_attempt_count=0,
            metrics_ref={"transport_kind": "scripted", "paper_eligible": False},
        )

    context = _context(
        catalog=_tracked_v3_catalog(),
        binding=_cohort_preflight_v3(),
        callback=callback,
    )
    experiment = module.Experiment5ModelComparisonModule()
    conditions = module.expand_exp5_v3_conditions(context)
    selections = module.freeze_exp5_v3_case_selections(context, conditions)
    factor_index = next(
        index
        for index, condition in enumerate(conditions)
        if condition.domain == "factorization"
    )
    lean_index = next(
        index
        for index, condition in enumerate(conditions)
        if condition.domain == "lean_proof"
    )

    experiment.run_condition(
        context,
        conditions[factor_index],
        selections[factor_index],
    )
    experiment.run_condition(
        context,
        conditions[lean_index],
        selections[lean_index],
    )

    assert calls == [("factorization", 83), ("lean_proof", 8)]
    with pytest.raises(ValueError, match="canonical Experiment 5 v3 selection"):
        experiment.run_condition(
            context,
            conditions[factor_index],
            replace(
                selections[factor_index],
                ordered_case_ids=tuple(
                    reversed(selections[factor_index].ordered_case_ids)
                ),
            ),
        )
    with pytest.raises(ValueError, match="canonical Experiment 5 v3 identity"):
        experiment.run_condition(
            context,
            replace(
                conditions[factor_index],
                exp5_selection_digest="sha256:" + "0" * 64,
            ),
            selections[factor_index],
        )
    assert calls == [("factorization", 83), ("lean_proof", 8)]


def test_exp5_epd006_uses_all_exp1_hard_roots_without_exp2_slice() -> None:
    module = _load_module()
    catalog = _epd006_catalog()
    context = _context(catalog=catalog)

    conditions = module.expand_exp5_conditions(context)
    selections = module.freeze_exp5_case_selections(context, conditions)

    assert len(conditions) == 36
    assert len(selections) == 36
    assert module.count_exp5_root_runs(conditions, selections) == 1_899
    assert sum(selection.expected_ai_unit_count for selection in selections) == (
        14_652
    )
    assert "_shared_exp2_slice" not in inspect.getsource(
        module._selection_for_condition
    )
    expected_factor_ids = tuple(
        case["case_id"]
        for case in catalog.factorization_cases
        if case["paper_difficulty"] == "hard"
    )
    readiness_ids = catalog.task15_budget_input["selected_case_ids_by_cell"]
    for condition, selection in zip(conditions, selections, strict=True):
        assert condition.paper_difficulty in {"hard", "hard_frontier"}
        if condition.domain == "factorization":
            assert condition.topic_family is None
            assert selection.ordered_case_ids == expected_factor_ids
            assert len(selection.ordered_case_ids) == 166
        else:
            assert condition.topic_family in module.LEAN_TOPIC_FAMILIES
            assert selection.topic_family == condition.topic_family
            assert selection.ordered_case_ids == tuple(
                readiness_ids[
                    f"hard_frontier/{condition.topic_family}"
                ]
            )
            assert len(selection.ordered_case_ids) == 15


def test_exp5_module_implements_contract_and_freezes_1899_root_runs() -> None:
    module = _load_module()
    context = _context()
    experiment = module.Experiment5ModelComparisonModule()

    conditions = experiment.expand_conditions(context)
    selections = experiment.freeze_case_selections(context, conditions)

    assert isinstance(experiment, PaperExperimentModule)
    assert len(conditions) == 36
    assert len(selections) == 36
    assert module.count_exp5_root_runs(conditions, selections) == 1_899
    assert {condition.cohort_member_id for condition in conditions} == set(
        PAPER_MODEL_ENDPOINT_COHORT_MEMBER_IDS
    )
    assert {condition.repeat_id for condition in conditions} == {0, 1, 2}
    assert {condition.model_policy for condition in conditions} == {"fixed_entry"}
    assert {len(selection.ordered_case_ids) for selection in selections} == {
        15,
        166,
    }
    assert all(condition.paper_eligible_required for condition in conditions)


def test_exp5_reuses_exact_exp1_hard_slices_for_every_endpoint_and_repeat() -> None:
    module = _load_module()
    context = _context()
    conditions = module.expand_exp5_conditions(context)
    selections = module.freeze_exp5_case_selections(context, conditions)
    ids_by_scope: dict[tuple[str, str], set[tuple[str, ...]]] = {}

    for condition, selection in zip(conditions, selections, strict=True):
        scope = (
            condition.domain,
            str(condition.topic_family or condition.paper_difficulty),
        )
        ids_by_scope.setdefault(scope, set()).add(tuple(selection.ordered_case_ids))
        if condition.domain == "lean_proof":
            assert selection.topic_family == condition.topic_family

    assert len(ids_by_scope) == 4
    assert all(len(observed) == 1 for observed in ids_by_scope.values())
    assert {
        len(next(iter(observed))) for observed in ids_by_scope.values()
    } == {15, 166}


def test_exp5_accepts_flat_formal_catalog_and_freezes_readiness_ids() -> None:
    module = _load_module()
    catalog = _epd006_catalog()
    context = _context(catalog=catalog)

    conditions = module.expand_exp5_conditions(context)
    selections = module.freeze_exp5_case_selections(context, conditions)

    assert module.count_exp5_root_runs(conditions, selections) == 1_899
    readiness_ids = catalog.task15_budget_input["selected_case_ids_by_cell"]
    for condition, selection in zip(conditions, selections, strict=True):
        if condition.domain == "factorization":
            expected_ids = tuple(
                case["case_id"]
                for case in catalog.factorization_cases
                if case["paper_difficulty"] == "hard"
            )
        else:
            expected_ids = tuple(
                readiness_ids[
                    f"hard_frontier/{condition.topic_family}"
                ]
            )
        assert selection.ordered_case_ids == expected_ids


def test_exp5_incomplete_cohort_structures_zero_call_formal_block() -> None:
    module = _load_module()
    preflight = _cohort_preflight()
    preflight["member_plans"].pop("deepseek_v4_pro_deepseek")
    calls: list[str] = []

    context = _context(
        binding=preflight,
        callback=lambda **kwargs: calls.append("called"),
    )
    gate = module.assess_exp5_cohort(context)

    assert gate.is_blocked is True
    assert gate.to_dict()["blocked_reason"] == "incomplete_model_cohort"
    assert gate.to_dict()["provider_attempt_count"] == 0
    assert gate.to_dict()["provider_calls_made"] == 0
    assert module.expand_exp5_conditions(context) == ()
    assert module.freeze_exp5_case_selections(context, ()) == ()
    assert calls == []


def test_exp5_planned_cohort_is_only_eligible_possible_before_calls() -> None:
    module = _load_module()

    body = module.assess_exp5_cohort(_context()).to_dict()

    assert body["status"] == "planned"
    assert body["paper_eligible_possible"] is True
    assert body["paper_eligible"] is False
    assert body["provider_calls_made"] == 0


def test_exp5_malformed_member_inventory_is_structured_blocked() -> None:
    module = _load_module()
    preflight = _cohort_preflight()
    preflight["expected_member_ids"] = None

    gate = module.assess_exp5_cohort(_context(binding=preflight))

    assert gate.is_blocked is True
    assert "cohort_member_order_mismatch" in gate.blocked_reasons
    assert gate.to_dict()["provider_calls_made"] == 0


def test_exp5_single_member_plan_is_pilot_only_and_never_formal_eligible() -> None:
    module = _load_module()
    context = _context()

    plan = module.build_exp5_single_member_pilot_plan(
        context,
        cohort_member_id="deepseek_v4_pro_deepseek",
    )

    assert plan["status"] == "planned"
    assert plan["pilot_only"] is True
    assert plan["paper_eligible"] is False
    assert plan["cohort_member_id"] == "deepseek_v4_pro_deepseek"
    assert plan["condition_count"] == 12
    assert plan["root_run_count"] == 633
    assert plan["provider_calls_made"] == 0


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (
            lambda body: body["member_plans"]["deepseek_v4_pro_deepseek"].update(
                {
                    "provider_config_id": "siliconflow",
                    "selected_entry_id": "glm-entry",
                }
            ),
            "entry_namespace_conflict",
        ),
        (
            lambda body: body["member_plans"][
                "gpt_5_6_sol_high_openai"
            ]["endpoint_identity"]["effective_reasoning_controls"].update(
                {"reasoning_effort": "medium"}
            ),
            "reasoning_controls_mismatch",
        ),
        (
            lambda body: body["member_plans"][
                "gpt_5_6_sol_high_openai"
            ].update({"source_provider_config_digest": "sha256:" + "9" * 64}),
            "source_provider_config_digest_mismatch",
        ),
    ],
)
def test_exp5_formal_cohort_fails_closed_on_identity_drift(
    mutate,
    reason: str,
) -> None:
    module = _load_module()
    preflight = deepcopy(_cohort_preflight())
    mutate(preflight)

    gate = module.assess_exp5_cohort(_context(binding=preflight))

    assert gate.is_blocked is True
    assert any(reason in item for item in gate.blocked_reasons)
    assert gate.to_dict()["provider_calls_made"] == 0


def test_exp5_rejects_readiness_drift_before_execution() -> None:
    module = _load_module()
    catalog = _epd006_catalog()
    catalog.task15_budget_input["selected_case_ids_by_cell"][
        "hard_frontier/pure_logic"
    ][0] = "drifted_case"
    context = _context(catalog=catalog)
    conditions = module.expand_exp5_conditions(context)

    with pytest.raises(ValueError, match="drift"):
        module.freeze_exp5_case_selections(context, conditions)


def test_exp5_run_condition_rejects_condition_and_selection_drift_before_callback() -> None:
    module = _load_module()
    calls: list[tuple[str, str]] = []

    def callback(**kwargs: Any) -> PaperConditionResult:
        condition = kwargs["condition"]
        selection = kwargs["selection"]
        calls.append((condition.model_entry_id, selection.selection_digest))
        return PaperConditionResult(
            condition_id=condition.condition_id,
            status=PaperStatus.BLOCKED,
            repeat_count=1,
            task_count=5,
            completed_root_count=0,
            failed_root_count=0,
            blocked_root_count=5,
            provider_attempt_count=0,
            metrics_ref={"transport_kind": "scripted", "paper_eligible": False},
        )

    context = _context(callback=callback)
    experiment = module.Experiment5ModelComparisonModule()
    conditions = experiment.expand_conditions(context)
    selections = experiment.freeze_case_selections(context, conditions)
    condition = conditions[0]
    selection = selections[0]

    result = experiment.run_condition(context, condition, selection)

    assert result.condition_id == condition.condition_id
    assert calls == [(condition.model_entry_id, selection.selection_digest)]

    with pytest.raises(ValueError, match="canonical Experiment 5 identity"):
        experiment.run_condition(
            context,
            replace(condition, seed=condition.seed + 1),
            selection,
        )
    drifted_selection = replace(
        selection,
        ordered_case_ids=tuple(reversed(selection.ordered_case_ids)),
    )
    with pytest.raises(ValueError, match="canonical Experiment 5 slice"):
        experiment.run_condition(context, condition, drifted_selection)
    assert len(calls) == 1


def test_exp5_model_execution_join_returns_auditable_v2_fields() -> None:
    module = _load_module()
    item = _model_execution_item()

    rows = module.build_exp5_model_execution_rows(
        {"model_execution_records": [item]}
    )
    summary = module.summarize_exp5_model_comparison(
        {"model_execution_records": [item]}
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["condition_id"] == "condition1"
    assert row["task_id"] == "task1"
    assert row["unit_id"] == "unit1"
    assert row["attempt_id"] == "attempt1"
    assert row["configured_model"] == "gpt-5.6-sol"
    assert row["requested_model"] == "gpt-5.6-sol"
    assert row["resolved_model"] == "gpt-5.6-sol"
    assert row["response_model_status"] == "present"
    assert row["source_provider_config_digest"] == "sha256:" + "5" * 64
    assert row["prepared_execution_config_digest"] == "sha256:" + "8" * 64
    assert row["reasoning_controls"] == {"reasoning_effort": "high"}
    assert row["latency_ms"] == 123
    assert row["total_tokens"] == 45
    assert row["cost_estimate"] == 0.012
    assert row["provider_errors"] == []
    assert row["error_kind"] is None
    assert row["attempt_paper_eligible"] is False
    assert row["task_paper_eligible"] is True
    assert row["effective_request_controls_digests"] == [
        "sha256:" + "7" * 64
    ]
    assert row["paper_eligible"] is True
    assert summary.rows == rows


def test_exp5_model_execution_join_uses_final_task_eligibility_for_standard_attempt() -> None:
    module = _load_module()
    item = _model_execution_item()

    assert item["attempt"]["paper_eligible"] is False
    assert item["task"]["paper_eligible"] is True
    eligible_row = module.build_exp5_model_execution_rows(
        {"model_execution_records": [item]}
    )[0]

    item["task"]["paper_eligible"] = False
    ineligible_row = module.build_exp5_model_execution_rows(
        {"model_execution_records": [item]}
    )[0]

    assert eligible_row["paper_eligible"] is True
    assert "task_not_paper_eligible" in ineligible_row["failure_reasons"]
    assert ineligible_row["paper_eligible"] is False


def test_exp5_model_execution_join_requires_final_task_eligibility_and_identity() -> None:
    module = _load_module()
    missing_task = _model_execution_item()
    missing_task.pop("task")
    mismatched_task = _model_execution_item()
    mismatched_task["task"]["task_id"] = "different-task"

    row = module.build_exp5_model_execution_rows(
        {"model_execution_records": [missing_task]}
    )[0]

    assert "task_eligibility_missing" in row["failure_reasons"]
    assert row["paper_eligible"] is False
    with pytest.raises(ValueError, match="task task_id"):
        module.build_exp5_model_execution_rows(
            {"model_execution_records": [mismatched_task]}
        )


@pytest.mark.parametrize(
    ("response_model", "expected_status", "expected_reason"),
    [
        (_MISSING, "missing", "missing_resolved_model"),
        ("gpt-5.6-sol-version-drift", "present", "resolved_model_mismatch"),
    ],
)
def test_exp5_model_execution_join_preserves_missing_and_mismatched_response_model(
    response_model: object,
    expected_status: str,
    expected_reason: str,
) -> None:
    module = _load_module()
    item = _model_execution_item(response_model=response_model)

    row = module.build_exp5_model_execution_rows(
        {"model_execution_records": [item]}
    )[0]

    assert row["response_model_status"] == expected_status
    assert row["resolved_model"] == (
        None if response_model is _MISSING else response_model
    )
    assert expected_reason in row["failure_reasons"]
    assert row["paper_eligible"] is False


def test_exp5_model_execution_join_rejects_endpoint_failover_and_scripted_eligibility() -> None:
    module = _load_module()
    failover = _model_execution_item(actual_entry_id="other-entry")
    scripted = _model_execution_item(transport_kind="scripted")

    failover_row = module.build_exp5_model_execution_rows(
        {"model_execution_records": [failover]}
    )[0]
    scripted_row = module.build_exp5_model_execution_rows(
        {"model_execution_records": [scripted]}
    )[0]

    assert "endpoint_failover" in failover_row["failure_reasons"]
    assert failover_row["paper_eligible"] is False
    assert "unsupported_transport" in scripted_row["failure_reasons"]
    assert scripted_row["paper_eligible"] is False


@pytest.mark.parametrize("field_name", ["pilot_only", "provider_errors"])
def test_exp5_model_execution_join_requires_top_level_formal_metadata(
    field_name: str,
) -> None:
    module = _load_module()
    item = _model_execution_item()
    item.pop(field_name)

    with pytest.raises(ValueError, match=field_name):
        module.build_exp5_model_execution_rows(
            {"model_execution_records": [item]}
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [("pilot_only", None), ("provider_errors", None)],
)
def test_exp5_model_execution_join_rejects_invalid_top_level_formal_metadata(
    field_name: str,
    value: object,
) -> None:
    module = _load_module()
    item = _model_execution_item()
    item[field_name] = value

    with pytest.raises(ValueError, match=field_name):
        module.build_exp5_model_execution_rows(
            {"model_execution_records": [item]}
        )


def test_exp5_model_execution_join_audits_persisted_raw_provider_and_entry() -> None:
    module = _load_module()
    item = _model_execution_item()
    item["raw_output"]["provider_family"] = "siliconflow"
    item["raw_output"]["entry_id"] = "wrong-entry"

    row = module.build_exp5_model_execution_rows(
        {"model_execution_records": [item]}
    )[0]

    assert "raw_provider_identity_mismatch" in row["failure_reasons"]
    assert "raw_entry_identity_mismatch" in row["failure_reasons"]
    assert row["paper_eligible"] is False


def test_exp5_model_execution_join_rejects_missing_v2_raw_provider_identity() -> None:
    module = _load_module()
    item = _model_execution_item()
    item["raw_output"].pop("provider_family")
    item["raw_output"].pop("entry_id")

    row = module.build_exp5_model_execution_rows(
        {"model_execution_records": [item]}
    )[0]

    assert "raw_provider_identity_mismatch" in row["failure_reasons"]
    assert "raw_entry_identity_mismatch" in row["failure_reasons"]
    assert row["paper_eligible"] is False


def test_exp5_model_execution_join_binds_formal_attempt_identity_refs_and_fields() -> None:
    module = _load_module()
    item = _model_execution_item()
    attempt = item["attempt"]
    item.update(
        {
            "model_policy": "mixed",
            "transport_kind": "custom_transport",
        }
    )
    attempt.update(
        {
            "provider": "siliconflow",
            "model": "wrong-model",
            "entry_id": "wrong-entry",
            "request_ref": {"artifact_id": "wrong-request"},
        }
    )

    row = module.build_exp5_model_execution_rows(
        {"model_execution_records": [item]}
    )[0]

    assert "invalid_model_policy" in row["failure_reasons"]
    assert "unsupported_transport" in row["failure_reasons"]
    assert "attempt_provider_identity_mismatch" in row["failure_reasons"]
    assert "attempt_model_identity_mismatch" in row["failure_reasons"]
    assert "attempt_entry_identity_mismatch" in row["failure_reasons"]
    assert "attempt_artifact_ref_mismatch" in row["failure_reasons"]
    assert row["worker_id"] == "worker1"
    assert row["provider_attempt_index"] == 0
    assert row["attempt_status"] == "succeeded"
    assert row["started_at"] == "2026-07-19T00:00:00Z"
    assert row["ended_at"] == "2026-07-19T00:00:01Z"
    assert row["model_execution_record_ref"] == {
        "artifact_id": "model-record1"
    }
    assert row["paper_eligible"] is False


@pytest.mark.parametrize(
    "field_name",
    [
        "condition_id",
        "repeat_id",
        "run_id",
        "task_id",
        "unit_id",
        "attempt_id",
        "worker_id",
        "provider_attempt_index",
        "attempt_status",
        "provider",
        "model",
        "entry_id",
        "request_ref",
        "raw_output_ref",
        "provenance_ref",
        "usage_ref",
        "model_execution_record_ref",
        "started_at",
        "ended_at",
        "latency_ms",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cost_estimate",
        "paper_eligible",
    ],
)
def test_exp5_model_execution_join_fails_closed_on_missing_attempt_field(
    field_name: str,
) -> None:
    module = _load_module()
    item = _model_execution_item()
    item["attempt"].pop(field_name)

    with pytest.raises(ValueError, match=field_name):
        module.build_exp5_model_execution_rows(
            {"model_execution_records": [item]}
        )


@pytest.mark.parametrize("cost_estimate", [float("nan"), float("inf")])
def test_exp5_model_execution_join_rejects_non_finite_cost(
    cost_estimate: float,
) -> None:
    module = _load_module()
    item = _model_execution_item()
    item["attempt"]["cost_estimate"] = cost_estimate

    with pytest.raises(ValueError, match="cost_estimate"):
        module.build_exp5_model_execution_rows(
            {"model_execution_records": [item]}
        )


def test_exp5_model_execution_join_requires_actual_request_and_provider_attempt_identity() -> None:
    module = _load_module()
    item = _model_execution_item()
    original = item["record"]
    record = PaperModelExecutionRecord(
        condition_id=original["condition_id"],
        repeat_id=original["repeat_id"],
        run_id=original["run_id"],
        task_id=original["task_id"],
        unit_id=original["unit_id"],
        attempt_id=original["attempt_id"],
        expected_identity=original["expected_identity"],
        source_provider_config_digest=original["source_provider_config_digest"],
        prepared_execution_config_digest=original[
            "prepared_execution_config_digest"
        ],
        request_ref=original["request_ref"],
        provenance_ref=original["provenance_ref"],
        raw_output_ref=original["raw_output_ref"],
        usage_ref=original["usage_ref"],
        actual_request_identities=[],
        actual_provider_attempts=[],
        requested_model=original["requested_model"],
        resolved_model=original["resolved_model"],
        response_model_status=original["response_model_status"],
        identity_status="matched",
        mismatch_reasons=[],
        paper_eligible=True,
        created_at=original["created_at"],
    )
    item["record"] = record.to_dict()

    row = module.build_exp5_model_execution_rows(
        {"model_execution_records": [item]}
    )[0]

    assert "missing_request_identity" in row["failure_reasons"]
    assert "missing_provider_attempt_identity" in row["failure_reasons"]
    assert row["paper_eligible"] is False


def test_exp5_model_execution_join_reads_v1_response_conservatively() -> None:
    module = _load_module()
    item = _model_execution_item()
    v1_record = {
        **item["record"],
        "schema_version": "tokenshare.paper_model_execution_record.v1",
        "configured_model": "gpt-5.6-sol",
        "requested_model": "gpt-5.6-sol",
        "resolved_model": "gpt-5.6-sol",
        "response_model_status": "present",
        "paper_eligible": True,
    }
    v1_record.pop("record_digest")
    v1_record["record_digest"] = digest_json(v1_record)
    item["record"] = v1_record
    item["raw_output"] = {
        "schema_version": "phase7.raw_model_output.v1",
        "model": "gpt-5.6-sol",
        "raw_response_json": {"id": "response-without-model"},
    }

    row = module.build_exp5_model_execution_rows(
        {"model_execution_records": [item]}
    )[0]

    assert row["requested_model"] is None
    assert row["resolved_model"] is None
    assert row["response_model_status"] == "missing"
    assert "historical_v1_identity" in row["failure_reasons"]
    assert "missing_resolved_model" in row["failure_reasons"]
    assert row["paper_eligible"] is False


def test_exp5_model_execution_join_fails_closed_on_unknown_or_inconsistent_schema() -> None:
    module = _load_module()
    unknown = _model_execution_item()
    unknown["record"]["schema_version"] = "unknown.model.record"
    inconsistent = _model_execution_item()
    inconsistent["raw_output"]["resolved_model"] = "other-model"

    with pytest.raises(ValueError, match="unsupported model execution record"):
        module.build_exp5_model_execution_rows(
            {"model_execution_records": [unknown]}
        )
    with pytest.raises(ValueError, match="inconsistent"):
        module.build_exp5_model_execution_rows(
            {"model_execution_records": [inconsistent]}
        )


def _load_module():
    spec = importlib.util.find_spec(MODULE_NAME)
    assert spec is not None, "Experiment 5 model comparison module must exist"
    return importlib.import_module(MODULE_NAME)


def _context(
    *,
    catalog: Any = None,
    binding: dict[str, Any] | None = None,
    callback=None,
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
            metrics_ref={"transport_kind": "scripted", "paper_eligible": False},
        )

    return PaperExecutionContext(
        context_id="exp5_test_context",
        catalog=catalog or _epd006_catalog(),
        approved_endpoint_binding=binding or _cohort_preflight(),
        request_limits={"max_provider_attempts": 1, "max_tokens": 8192},
        hard_limits={"max_total_provider_attempts": 0},
        output_root="outputs/experiments/exp5_test",
        artifact_store=object(),
        event_store=object(),
        execution_callback=callback or default_callback,
    )


def _model_execution_item(
    *,
    response_model: object = "gpt-5.6-sol",
    actual_entry_id: str = "gpt-entry",
    transport_kind: str = "ai_api",
) -> dict[str, Any]:
    expected_identity = deepcopy(
        _cohort_preflight()["member_plans"]["gpt_5_6_sol_high_openai"][
            "endpoint_identity"
        ]
    )
    raw_response_json: dict[str, Any] = {"id": "response1"}
    if response_model is not _MISSING:
        raw_response_json["model"] = response_model
    resolved_model = None if response_model is _MISSING else str(response_model)
    response_model_status = "missing" if response_model is _MISSING else "present"
    matched = response_model == "gpt-5.6-sol"
    mismatch_reasons = (
        []
        if matched
        else [
            "missing_resolved_model"
            if response_model is _MISSING
            else "resolved_model_mismatch"
        ]
    )
    request_identity = {
        "schema_version": "phase7.provider_request_identity.v2",
        "provider_family": "openai",
        "entry_id": actual_entry_id,
        "configured_model": "gpt-5.6-sol",
        "requested_model": "gpt-5.6-sol",
        "reasoning_controls": {"reasoning_effort": "high"},
        "effective_request_controls_digest": "sha256:" + "7" * 64,
    }
    record = PaperModelExecutionRecord(
        condition_id="condition1",
        repeat_id=0,
        run_id="run1",
        task_id="task1",
        unit_id="unit1",
        attempt_id="attempt1",
        expected_identity=expected_identity,
        source_provider_config_digest="sha256:" + "5" * 64,
        prepared_execution_config_digest="sha256:" + "8" * 64,
        request_ref={"artifact_id": "request1"},
        provenance_ref={"artifact_id": "provenance1"},
        raw_output_ref={"artifact_id": "raw1"},
        usage_ref={"artifact_id": "usage1"},
        actual_request_identities=[request_identity],
        actual_provider_attempts=[
            {
                "provider_family": "openai",
                "entry_id": actual_entry_id,
                "configured_model": "gpt-5.6-sol",
                "result_kind": "succeeded",
            }
        ],
        requested_model="gpt-5.6-sol",
        resolved_model=resolved_model,
        response_model_status=response_model_status,
        identity_status="matched" if matched else "model_identity_mismatch",
        mismatch_reasons=mismatch_reasons,
        paper_eligible=matched,
        created_at="2026-07-19T00:00:00Z",
    )
    raw_output = {
        "schema_version": "phase7.raw_model_output.v2",
        "provider_family": "openai",
        "entry_id": actual_entry_id,
        "configured_model": "gpt-5.6-sol",
        "requested_model": "gpt-5.6-sol",
        "resolved_model": resolved_model,
        "response_model_status": response_model_status,
        "raw_response_json": raw_response_json,
    }
    attempt = PaperAttemptResult(
        condition_id="condition1",
        repeat_id=0,
        run_id="run1",
        task_id="task1",
        unit_id="unit1",
        attempt_id="attempt1",
        worker_id="worker1",
        provider_attempt_index=0,
        attempt_status="succeeded",
        provider="openai",
        model="gpt-5.6-sol",
        entry_id="gpt-entry",
        request_ref={"artifact_id": "request1"},
        raw_output_ref={"artifact_id": "raw1"},
        parsed_output_ref=None,
        parse_failure_ref=None,
        provenance_ref={"artifact_id": "provenance1"},
        usage_ref={"artifact_id": "usage1"},
        model_execution_record_ref={"artifact_id": "model-record1"},
        started_at="2026-07-19T00:00:00Z",
        ended_at="2026-07-19T00:00:01Z",
        latency_ms=123,
        prompt_tokens=12,
        completion_tokens=33,
        total_tokens=45,
        cost_estimate=0.012,
        error_kind=None,
        fault_injection_ref=None,
        paper_eligible=False,
    ).to_dict()
    task = PaperTaskResult(
        condition_id="condition1",
        repeat_id=0,
        task_id="task1",
        domain="lean_proof",
        difficulty="hard",
        paper_difficulty="hard_frontier",
        topic_family="pure_logic",
        root_status=PaperTaskStatus.COMPLETED,
        accepted_validity=True,
        failure_stage=None,
        failure_kind=None,
        attempt_count=1,
        provider_attempt_count=1,
        wall_clock_ms=150,
        total_tokens=45,
        cost_estimate=0.012,
        event_refs=[],
        artifact_refs=[],
        paper_eligible=True,
    ).to_dict()
    return {
        "record": record.to_dict(),
        "record_ref": {"artifact_id": "model-record1"},
        "raw_output": raw_output,
        "attempt": attempt,
        "task": task,
        "transport_kind": transport_kind,
        "model_policy": "fixed_entry",
        "provider_errors": [],
        "pilot_only": False,
    }


def _cohort_preflight() -> dict[str, Any]:
    member_plans: dict[str, Any] = {}
    entry_ids = {
        "glm_5_2_siliconflow": "glm-entry",
        "deepseek_v4_pro_deepseek": "deepseek-entry",
        "gpt_5_6_sol_high_openai": "gpt-entry",
    }
    comparable_controls = {
        "stream": False,
        "timeout_seconds": 100,
        "max_tokens": 8192,
        "max_provider_attempts": 1,
        "domain_contracts": {
            "factorization": {
                "prompt_profile": "factorization.bounded_range_prompt.v1",
                "parser_id": "factorization.range_result.parser.v1",
                "plugin_version": "0.1.0",
            },
            "lean_proof": {
                "prompt_profile": "lean_proof.proof_candidate_prompt.v1",
                "parser_id": "lean_proof.proof_candidate.parser.v1",
                "plugin_version": "0.1.0",
            },
        },
    }
    for index, member_id in enumerate(PAPER_MODEL_ENDPOINT_COHORT_MEMBER_IDS, start=3):
        expected = PAPER_MODEL_ENDPOINT_COHORT_MEMBERS[member_id]
        source_digest = "sha256:" + str(index) * 64
        effective_controls = {
            "siliconflow": {"enable_thinking": True},
            "deepseek": {
                "thinking": {"type": "enabled"},
                "reasoning_effort": "high",
            },
            "openai": {"reasoning_effort": "high"},
        }[str(expected["provider_family"])]
        identity = PaperModelEndpointIdentity(
            model_cohort_id=PAPER_MODEL_ENDPOINT_COHORT_ID,
            model_cohort_digest=COHORT_DIGEST,
            cohort_member_id=member_id,
            provider_config_id=str(expected["provider_family"]),
            selected_entry_id=entry_ids[member_id],
            provider_family=str(expected["provider_family"]),
            provider_model_id=str(expected["provider_model_id"]),
            reasoning_profile_id=str(expected["reasoning_profile_id"]),
            effective_reasoning_controls=effective_controls,
            source_provider_config_digest=source_digest,
        )
        member_plans[member_id] = {
            "schema_version": "tokenshare.paper_model_endpoint_member_plan.v1",
            "status": "planned",
            "blocked_reasons": [],
            "cohort_id": PAPER_MODEL_ENDPOINT_COHORT_ID,
            "model_cohort_digest": COHORT_DIGEST,
            "cohort_member_id": member_id,
            "provider_config_id": str(expected["provider_family"]),
            "selected_entry_id": entry_ids[member_id],
            "provider_family": str(expected["provider_family"]),
            "provider_model_id": str(expected["provider_model_id"]),
            "reasoning_profile_id": str(expected["reasoning_profile_id"]),
            "source_provider_config_digest": source_digest,
            "model_endpoint_identity_digest": (
                identity.model_endpoint_identity_digest
            ),
            "endpoint_identity": identity.to_dict(),
            "request_controls": {
                "schema_version": "tokenshare.paper_exp5_request_controls.v1",
                "comparable": comparable_controls,
                "comparable_digest": digest_json(comparable_controls),
                "provider_specific_reasoning": effective_controls,
                "provider_specific_reasoning_digest": digest_json(
                    effective_controls
                ),
            },
        }
    return {
        "schema_version": "tokenshare.paper_model_endpoint_cohort_preflight.v1",
        "status": "planned",
        "paper_eligible_possible": True,
        "blocked_reason": None,
        "ineligibility_reasons": [],
        "provider_calls_made": 0,
        "model_policy": "fixed_entry",
        "cohort_id": PAPER_MODEL_ENDPOINT_COHORT_ID,
        "model_cohort_digest": COHORT_DIGEST,
        "expected_member_ids": list(PAPER_MODEL_ENDPOINT_COHORT_MEMBER_IDS),
        "member_plans": member_plans,
        "request_controls_snapshot": comparable_controls,
        "request_controls_snapshot_digest": digest_json(comparable_controls),
    }


def _cohort_preflight_v3() -> dict[str, Any]:
    cohort_digest = "sha256:" + "c" * 64
    comparable_controls = {
        "temperature": 0.0,
        "top_p": 1.0,
        "stream": False,
        "timeout_seconds": 600,
        "max_tokens": 32768,
        "max_provider_attempts": 1,
        "domain_contracts": {
            "factorization": {
                "prompt_profile": "factorization.bounded_range_prompt.v1",
                "parser_id": "factorization.range_result.parser.v1",
                "plugin_version": "0.1.0",
            },
            "lean_proof": {
                "prompt_profile": "lean_proof.proof_candidate_prompt.v1",
                "parser_id": "lean_proof.proof_candidate.parser.v1",
                "plugin_version": "0.1.0",
            },
        },
    }
    member_plans: dict[str, Any] = {}
    for index, member_id in enumerate(
        PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS,
        start=3,
    ):
        expected = PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBERS[member_id]
        source_digest = "sha256:" + str(index) * 64
        selected_entry_id = f"{member_id}_entry"
        reasoning_controls = dict(expected["request_overrides"])
        identity = PaperModelEndpointIdentity(
            model_cohort_id=PAPER_MODEL_ENDPOINT_COHORT_V3_ID,
            model_cohort_digest=cohort_digest,
            cohort_member_id=member_id,
            provider_config_id="siliconflow",
            selected_entry_id=selected_entry_id,
            provider_family="siliconflow",
            provider_model_id=str(expected["provider_model_id"]),
            reasoning_profile_id=str(expected["reasoning_profile_id"]),
            effective_reasoning_controls=reasoning_controls,
            source_provider_config_digest=source_digest,
        )
        member_plans[member_id] = {
            "schema_version": "tokenshare.paper_model_endpoint_member_plan.v1",
            "status": "planned",
            "blocked_reasons": [],
            "cohort_id": PAPER_MODEL_ENDPOINT_COHORT_V3_ID,
            "model_cohort_digest": cohort_digest,
            "cohort_member_id": member_id,
            "provider_config_id": "siliconflow",
            "selected_entry_id": selected_entry_id,
            "provider_family": "siliconflow",
            "provider_model_id": expected["provider_model_id"],
            "reasoning_profile_id": expected["reasoning_profile_id"],
            "source_provider_config_digest": source_digest,
            "model_endpoint_identity_digest": (
                identity.model_endpoint_identity_digest
            ),
            "endpoint_identity": identity.to_dict(),
            "request_controls": {
                "schema_version": "tokenshare.paper_exp5_request_controls.v1",
                "comparable": comparable_controls,
                "comparable_digest": digest_json(comparable_controls),
                "provider_specific_reasoning": reasoning_controls,
                "provider_specific_reasoning_digest": digest_json(
                    reasoning_controls
                ),
            },
        }
    return {
        "schema_version": "tokenshare.paper_model_endpoint_cohort_preflight.v1",
        "status": "planned",
        "paper_eligible_possible": True,
        "blocked_reason": None,
        "ineligibility_reasons": [],
        "provider_calls_made": 0,
        "model_policy": "fixed_entry",
        "cohort_id": PAPER_MODEL_ENDPOINT_COHORT_V3_ID,
        "model_cohort_digest": cohort_digest,
        "expected_member_ids": list(PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS),
        "member_plans": member_plans,
        "request_controls_snapshot": comparable_controls,
        "request_controls_snapshot_digest": digest_json(comparable_controls),
    }


def _tracked_v3_catalog() -> SimpleNamespace:
    manifest = load_paper_catalogs(
        factorization_path=Path("benchmarks/paper/factorization_catalog.v2.jsonl"),
        lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
        lean_lemma_graph_path=Path(
            "benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl"
        ),
    )
    readiness = json.loads(
        Path("benchmarks/paper/lean_task14_3x3_readiness.v1.json").read_text(
            encoding="utf-8"
        )
    )
    bound_readiness = _bind_lean_matrix_to_catalog(
        readiness,
        catalog_manifest=manifest,
    )
    return SimpleNamespace(
        catalog_id=manifest.catalog_id,
        catalog_version=manifest.catalog_version,
        catalog_digest=manifest.catalog_digest,
        suite_version="paper_v1",
        factorization_cases=manifest.factorization_cases,
        lean_cases=manifest.lean_cases,
        lean_lemma_graph_cases=manifest.lean_lemma_graph_cases,
        task15_budget_input=bound_readiness["task15_budget_input"],
        lean_task14_readiness=bound_readiness,
    )


def _formal_catalog() -> SimpleNamespace:
    factorization_cases = tuple(
        {
            "schema_version": "tokenshare.paper_factorization_case.v1",
            "case_id": f"factor_{difficulty}_{index:02d}",
            "difficulty": difficulty,
            "paper_difficulty": difficulty,
            "candidate_start": "2",
            "candidate_end": str(20 + index),
            "split_params": {
                "strategy_id": "factorization.candidate_range_partition.v1",
                "range_policy": "contiguous",
                "requested_child_count": 2 + index,
            },
        }
        for difficulty in ("easy", "medium", "hard")
        for index in range(1, 6)
    )
    selected_by_cell: dict[str, list[str]] = {}
    semantic_by_cell: dict[str, list[str]] = {}
    golden_by_cell: dict[str, list[str]] = {}
    lean_cases: list[dict[str, Any]] = []
    for difficulty in ("simple", "medium_lemma_dag", "hard_frontier"):
        for topic_family in ("pure_logic", "function_set", "induction"):
            cell_key = f"{difficulty}/{topic_family}"
            case_ids = [
                f"lean_{difficulty}_{topic_family}_{index:02d}"
                for index in range(1, 16)
            ]
            selected_by_cell[cell_key] = case_ids
            semantic_by_cell[cell_key] = [
                digest_json({"cell": cell_key, "index": index})
                for index in range(1, 16)
            ]
            golden_by_cell[cell_key] = [case_ids[0]]
            lean_cases.extend(
                {
                    "schema_version": "tokenshare.paper_lean_case.v2",
                    "case_id": case_id,
                    "difficulty": (
                        "easy"
                        if difficulty == "simple"
                        else "medium"
                        if difficulty == "medium_lemma_dag"
                        else "hard"
                    ),
                    "paper_difficulty": difficulty,
                    "topic_family": topic_family,
                    "expected_child_count": 2 + index,
                }
                for index, case_id in enumerate(case_ids, start=1)
            )
    simple_cases = tuple(
        case for case in lean_cases if case["paper_difficulty"] == "simple"
    )
    graph_cases = tuple(
        case for case in lean_cases if case["paper_difficulty"] != "simple"
    )
    catalog_id = "tokenshare.paper.catalog.test"
    catalog_version = "v1"
    catalog_digest = digest_json(
        {
            "catalog_id": catalog_id,
            "catalog_version": catalog_version,
            "factorization_cases": list(factorization_cases),
            "lean_cases": list(simple_cases),
            "lean_lemma_graph_cases": list(graph_cases),
        }
    )
    environment_digest = "sha256:" + "a" * 64
    oracle_package_digests = {
        cell_key: digest_json({"oracle": cell_key})
        for cell_key in selected_by_cell
    }
    selection_digest = digest_json(
        {
            "schema_version": "tokenshare.lean_task14_selected_cases.v1",
            "catalog_digest": catalog_digest,
            "environment_digest": environment_digest,
            "oracle_package_digests": oracle_package_digests,
            "target_case_count": 15,
            "selected_case_ids_by_cell": selected_by_cell,
            "semantic_fingerprint_digests_by_cell": semantic_by_cell,
            "golden_case_ids_by_cell": golden_by_cell,
        }
    )
    task15_budget_input = {
        "schema_version": "tokenshare.lean_task15_budget_input.v1",
        "target_case_count": 15,
        "selected_case_count": 135,
        "executable_cell_count": 9,
        "blocked_cell_count": 0,
        "provider_calls_made": 0,
        "catalog_digest": catalog_digest,
        "environment_digest": environment_digest,
        "oracle_package_digests": oracle_package_digests,
        "selected_case_ids_by_cell": selected_by_cell,
        "semantic_fingerprint_digests_by_cell": semantic_by_cell,
        "golden_case_ids_by_cell": golden_by_cell,
        "selection_digest": selection_digest,
        "catalog_slice_digest": selection_digest,
    }
    return SimpleNamespace(
        catalog_id=catalog_id,
        catalog_version=catalog_version,
        catalog_digest=catalog_digest,
        factorization_cases=factorization_cases,
        lean_cases=simple_cases,
        lean_lemma_graph_cases=graph_cases,
        task15_budget_input=task15_budget_input,
    )


def _epd006_catalog() -> SimpleNamespace:
    base = _formal_catalog()
    factorization_cases = tuple(
        {
            "schema_version": "tokenshare.paper_factorization_case.v1",
            "case_id": f"factor_v2_{difficulty}_{index:03d}",
            "difficulty": difficulty,
            "paper_difficulty": difficulty,
            "candidate_start": "2",
            "candidate_end": str(1_000 + index),
            "expected_ai_unit_count": requested_children,
            "split_params": {
                "strategy_id": "factorization.candidate_range_partition.v1",
                "range_policy": "contiguous",
                "requested_child_count": requested_children,
            },
        }
        for difficulty, count, requested_children in (
            ("easy", 167, 2),
            ("medium", 167, 4),
            ("hard", 166, 8),
        )
        for index in range(1, count + 1)
    )
    lean_cases = tuple(deepcopy(case) for case in base.lean_cases)
    hard_units = {"pure_logic": 7, "function_set": 6, "induction": 7}
    graph_cases = tuple(
        {
            **deepcopy(case),
            **(
                {
                    "expected_ai_unit_count": hard_units[
                        str(case["topic_family"])
                    ]
                }
                if case["paper_difficulty"] == "hard_frontier"
                else {}
            ),
        }
        for case in base.lean_lemma_graph_cases
    )
    catalog_id = "tokenshare.paper.catalog.epd006.test"
    catalog_version = "v2"
    catalog_digest = digest_json(
        {
            "catalog_id": catalog_id,
            "catalog_version": catalog_version,
            "factorization_cases": list(factorization_cases),
            "lean_cases": list(lean_cases),
            "lean_lemma_graph_cases": list(graph_cases),
        }
    )
    readiness = deepcopy(base.task15_budget_input)
    readiness["catalog_digest"] = catalog_digest
    selection_digest = digest_json(
        {
            "schema_version": "tokenshare.lean_task14_selected_cases.v1",
            "catalog_digest": catalog_digest,
            "environment_digest": readiness["environment_digest"],
            "oracle_package_digests": readiness["oracle_package_digests"],
            "target_case_count": 15,
            "selected_case_ids_by_cell": readiness[
                "selected_case_ids_by_cell"
            ],
            "semantic_fingerprint_digests_by_cell": readiness[
                "semantic_fingerprint_digests_by_cell"
            ],
            "golden_case_ids_by_cell": readiness["golden_case_ids_by_cell"],
        }
    )
    readiness["selection_digest"] = selection_digest
    readiness["catalog_slice_digest"] = selection_digest
    return SimpleNamespace(
        catalog_id=catalog_id,
        catalog_version=catalog_version,
        catalog_digest=catalog_digest,
        suite_version="paper_v1",
        factorization_cases=factorization_cases,
        lean_cases=lean_cases,
        lean_lemma_graph_cases=graph_cases,
        task15_budget_input=readiness,
    )


def _catalog() -> dict[str, Any]:
    exp2 = {
        "factorization": {
            difficulty: [
                {
                    "case_id": f"factorization_{difficulty}_{index:02d}",
                    "expected_ai_unit_count": 2 + index,
                    "parallelism_scope": "within_root_range_children",
                }
                for index in range(1, 6)
            ]
            for difficulty in ("easy", "medium", "hard")
        },
        "lean_proof": {
            "simple": {
                "pure_logic": _lean_cases("simple", "pure_logic", 2, 2),
                "function_set": _lean_cases("simple", "function_set", 2, 2),
                "induction": _lean_cases("simple", "induction", 1, 2),
            },
            "medium_lemma_dag": {
                "pure_logic": _lean_cases("medium_lemma_dag", "pure_logic", 1, 4),
                "function_set": _lean_cases(
                    "medium_lemma_dag", "function_set", 2, 4
                ),
                "induction": _lean_cases("medium_lemma_dag", "induction", 2, 4),
            },
            "hard_frontier": {
                "pure_logic": _lean_cases("hard_frontier", "pure_logic", 2, 6),
                "function_set": _lean_cases("hard_frontier", "function_set", 1, 6),
                "induction": _lean_cases("hard_frontier", "induction", 2, 6),
            },
        },
    }
    return {
        "suite_version": "paper_v1",
        "catalog_version": "v1",
        "catalog_digest": CATALOG_DIGEST,
        "exp2": exp2,
        "exp5": {
            "slice_digest": digest_json(_slice_digest_body(exp2)),
            **exp2,
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


def _slice_digest_body(exp2: dict[str, Any]) -> dict[str, Any]:
    return {
        "factorization": {
            difficulty: [case["case_id"] for case in cases]
            for difficulty, cases in exp2["factorization"].items()
        },
        "lean_proof": {
            difficulty: {
                topic: [case["case_id"] for case in cases]
                for topic, cases in topics.items()
            }
            for difficulty, topics in exp2["lean_proof"].items()
        },
    }
