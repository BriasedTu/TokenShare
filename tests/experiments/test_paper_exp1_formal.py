from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace
from typing import Any

import pytest

from tokenshare.experiments.paper_experiment_contracts import (
    ExperimentSummaryRows,
    FrozenCaseSelection,
    PaperExecutionContext,
    PaperExperimentModule,
)
from tokenshare.experiments.paper_models import PaperConditionResult, PaperStatus
from tokenshare.experiments.paper_exp1 import Exp1FormalModule


CATALOG_DIGEST = "sha256:" + "1" * 64
SOURCE_CONFIG_DIGEST = "sha256:" + "2" * 64
ENDPOINT_DIGEST = "sha256:" + "3" * 64


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
        assert condition.reasoning_profile_id == "temperature0_thinking_false"
        assert condition.source_provider_config_digest == SOURCE_CONFIG_DIGEST
        assert condition.model_endpoint_identity_digest == ENDPOINT_DIGEST
        assert condition.catalog_digest == CATALOG_DIGEST


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


def test_exp1_formal_rejects_duplicate_roots_model_drift_and_condition_order_drift() -> None:
    module = Exp1FormalModule()
    with pytest.raises(ValueError, match="duplicate root case_id"):
        context = _context(catalog=_FakeCatalog(duplicate_global_case=True))
        module.freeze_case_selections(context, module.expand_conditions(context))

    with pytest.raises(ValueError, match="GLM-5.2 baseline"):
        module.expand_conditions(
            _context(binding={**_baseline_binding(), "provider_model_id": "wrong-model"})
        )

    with pytest.raises(ValueError, match="GLM-5.2 baseline"):
        module.expand_conditions(_context(request_limits={"temperature": 0.7}))

    context = _context()
    conditions = module.expand_conditions(context)
    with pytest.raises(ValueError, match="condition order drift"):
        module.freeze_case_selections(context, tuple(reversed(conditions)))
    drifted_repeat = (replace(conditions[0], repeat_id=99),) + conditions[1:]
    with pytest.raises(ValueError, match="condition order drift"):
        module.freeze_case_selections(context, drifted_repeat)


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
            0.00,
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
    assert simple["cost_per_completed_task"] == pytest.approx(0.15)
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


def _context(
    *,
    catalog: Any | None = None,
    binding: dict[str, Any] | None = None,
    request_limits: dict[str, Any] | None = None,
    callback: Any | None = None,
) -> PaperExecutionContext:
    return PaperExecutionContext(
        context_id="exp1_formal_test",
        catalog=catalog or _FakeCatalog(),
        approved_endpoint_binding=binding or _baseline_binding(),
        request_limits=request_limits or {"temperature": 0.0, "enable_thinking": False},
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
        "reasoning_profile_id": "temperature0_thinking_false",
        "source_provider_config_digest": SOURCE_CONFIG_DIGEST,
        "model_endpoint_identity_digest": ENDPOINT_DIGEST,
        "request_controls": {"temperature": 0.0, "enable_thinking": False},
    }


class _FakeCatalog:
    catalog_digest = CATALOG_DIGEST
    catalog_version = "v1"
    lean_semantic_readiness_passed: bool

    def __init__(
        self,
        *,
        lean_semantic_ready: bool = True,
        missing_lean_cell: bool = False,
        duplicate_global_case: bool = False,
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
                for index in range(1, 16):
                    case_id = f"lean_{paper_difficulty}_{topic_family}_{index:02d}"
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
        if duplicate_global_case:
            self._cases[-1]["case_id"] = self._cases[0]["case_id"]

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
