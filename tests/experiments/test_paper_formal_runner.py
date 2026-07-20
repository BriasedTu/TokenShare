import json
from dataclasses import replace
from pathlib import Path
from threading import Lock
from time import sleep
from types import SimpleNamespace

import pytest

import tokenshare.experiments.paper_formal_runner as formal_runner
from tokenshare.executors.ai_api_config import (
    AIAPIExecutorConfig,
    AIAPIProviderEntry,
)
from tokenshare.experiments.paper_experiment_contracts import (
    ExperimentSummaryRows,
    FrozenCaseSelectionBatch,
    FrozenCaseSelection,
    FrozenConditionSelectionBinding,
)
from tokenshare.experiments.paper_models import (
    PaperAttemptResult,
    PaperAttemptStatus,
    PaperBudgetResult,
    PaperConditionResult,
    PaperExperimentCondition,
    PaperStatus,
    PaperTaskStatus,
)
from tokenshare.storage.artifacts import ArtifactStore


CATALOG_DIGEST = "sha256:" + "1" * 64
BUDGET_DIGEST = "sha256:" + "2" * 64
EXPERIMENT_ID = "exp1_real_ai_feasibility"
PROVIDER_CONFIG_ID = "exp1_baseline_siliconflow"
MODEL_ENTRY_ID = "glm_5_2_exp1_baseline"
PROVIDER_MODEL_ID = "zai-org/GLM-5.2"
ENDPOINT_DIGEST = "sha256:" + "3" * 64
EXP5_EXPERIMENT_ID = "exp5_real_ai_model_endpoint_comparison"
MODEL_COHORT_ID = "paper-model-endpoint-cohort-v1"
MODEL_COHORT_DIGEST = "sha256:" + "4" * 64
COHORT_MEMBER_ID = "glm_5_2_siliconflow"


def test_formal_runner_checkpoints_required_evidence_and_reuses_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)
    adapter_calls: list[str] = []

    class CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=EXPERIMENT_ID, rows=())

    def fake_case_dispatch(**kwargs):
        adapter_calls.append(kwargs["case"]["case_id"])
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", fake_case_dispatch)
    kwargs = _formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan)

    executed = formal_runner.execute_paper_formal_suite(**kwargs)
    assert executed.status == PaperStatus.COMPLETED
    assert executed.provider_attempt_count == 1
    assert executed.total_tokens == 11
    assert executed.total_cost_estimate == pytest.approx(0.125)
    assert adapter_calls == ["case-1"]

    suite_files = {
        "suite_manifest.json",
        "run_budget.json",
        "input_catalog_manifest.json",
        "paper_dispatch_plans.json",
        "conditions.jsonl",
        "evidence_manifest.json",
    }
    assert suite_files <= {item.name for item in tmp_path.iterdir()}
    suite_manifest = json.loads(
        (tmp_path / "suite_manifest.json").read_text(encoding="utf-8")
    )
    assert suite_manifest["formal"] is True
    assert suite_manifest["pilot_only"] is False
    assert suite_manifest["execution_scope"] == "formal_matrix"
    assert suite_manifest["regression_only"] is True
    assert suite_manifest["paper_eligible"] is False
    assert (tmp_path / EXPERIMENT_ID / "experiment_manifest.json").is_file()

    run_root = tmp_path / EXPERIMENT_ID / "runs" / "condition-1" / "0"
    for relative_path in (
        "CURRENT.json",
        "artifacts/case-1/raw-output.txt",
        "artifacts/case-1/request.json",
    ):
        assert (run_root / relative_path).is_file(), relative_path

    resumed = formal_runner.execute_paper_formal_suite(**kwargs, resume=True)
    assert resumed.to_dict() == executed.to_dict()
    assert adapter_calls == ["case-1"]

    replayed = formal_runner.execute_paper_formal_suite(
        **{**kwargs, "ai_api_configs": {}, "transport": None},
        replay_only=True,
    )
    assert replayed.to_dict() == executed.to_dict()
    assert adapter_calls == ["case-1"]


def test_formal_runner_dispatches_planned_conditions_with_isolated_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)
    calls: list[tuple[object, object, str]] = []

    def capture_dispatch(*, context, plan, condition_id):
        calls.append((context, plan, condition_id))
        return PaperConditionResult(
            condition_id=condition_id,
            status=PaperStatus.COMPLETED,
            repeat_count=1,
            task_count=1,
            completed_root_count=1,
            failed_root_count=0,
            blocked_root_count=0,
            provider_attempt_count=1,
            metrics_ref=None,
        )

    monkeypatch.setattr(formal_runner, "dispatch_paper_condition", capture_dispatch)

    suite = formal_runner.execute_paper_formal_suite(
        dispatch_plans=(plan,),
        catalog_manifest=SimpleNamespace(catalog_digest=CATALOG_DIGEST),
        budget=_budget(planned_conditions=1, planned_root_runs=1, planned_ai_units=1),
        budget_approval={
            "approval_mode": "user_bypassed",
            "budget_digest": BUDGET_DIGEST,
        },
        output_root=tmp_path,
        ai_api_configs={PROVIDER_CONFIG_ID: config},
        transport=object(),
        real_transport=False,
        hard_limits={"stop_after_current_task": True},
    )

    assert [(item[1], item[2]) for item in calls] == [(plan, "condition-1")]
    assert Path(calls[0][0].output_root) == tmp_path / EXPERIMENT_ID
    assert (tmp_path / EXPERIMENT_ID).is_dir()
    assert suite.status == PaperStatus.COMPLETED
    assert suite.condition_count == len(plan.bound_items())
    assert suite.paper_eligible is False
    assert suite.provider_attempt_count == 1


def test_formal_runner_builds_endpoint_context_through_registered_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)
    observed: dict[str, object] = {}
    adapter_calls: list[dict[str, object]] = []

    class ContextCheckingModule:
        def expand_conditions(self, context):
            raise AssertionError("execution must consume the frozen dispatch plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            observed["approved_endpoint_binding"] = dict(
                context.approved_endpoint_binding
            )
            observed["request_limits"] = dict(context.request_limits)
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=EXPERIMENT_ID, rows=())

    def capture_case_dispatch(**kwargs):
        adapter_calls.append(kwargs)
        return SimpleNamespace(
            task_result=SimpleNamespace(
                root_status=PaperTaskStatus.COMPLETED,
                provider_attempt_count=1,
                paper_eligible=False,
            ),
            eligibility_report=SimpleNamespace(paper_eligible=False),
        )

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, ContextCheckingModule()),),
    )
    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_case",
        capture_case_dispatch,
        raising=False,
    )

    suite = formal_runner.execute_paper_formal_suite(
        dispatch_plans=(plan,),
        catalog_manifest=SimpleNamespace(
            catalog_digest=CATALOG_DIGEST,
            factorization_cases=({"case_id": "case-1"},),
            lean_cases=(),
            lean_lemma_graph_cases=(),
        ),
        budget=_budget(planned_conditions=1, planned_root_runs=1, planned_ai_units=1),
        budget_approval={
            "approval_mode": "user_bypassed",
            "budget_digest": BUDGET_DIGEST,
        },
        output_root=tmp_path,
        ai_api_configs={PROVIDER_CONFIG_ID: config},
        transport=object(),
        real_transport=False,
        hard_limits={"stop_after_current_task": True},
    )

    request_controls = {
        **config.defaults,
        **config.entries[0].request_overrides,
    }
    assert observed["approved_endpoint_binding"] == {
        "provider_config_id": PROVIDER_CONFIG_ID,
        "selected_entry_id": MODEL_ENTRY_ID,
        "model_entry_id": MODEL_ENTRY_ID,
        "provider_family": "siliconflow",
        "provider_model_id": PROVIDER_MODEL_ID,
        "reasoning_profile_id": "default",
        "source_provider_config_digest": config.config_digest,
        "model_endpoint_identity_digest": ENDPOINT_DIGEST,
        "request_controls": request_controls,
    }
    assert observed["request_limits"] == request_controls
    assert len(adapter_calls) == 1
    assert adapter_calls[0]["ai_api_config"] is config
    assert adapter_calls[0]["entry_id"] == MODEL_ENTRY_ID
    assert adapter_calls[0]["max_tokens"] == 1024
    assert adapter_calls[0]["timeout_seconds"] == 30
    assert suite.status == PaperStatus.COMPLETED


def test_formal_runner_accepts_mapping_catalog_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)

    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_condition",
        lambda *, context, plan, condition_id: PaperConditionResult(
            condition_id=condition_id,
            status=PaperStatus.COMPLETED,
            repeat_count=1,
            task_count=1,
            completed_root_count=1,
            failed_root_count=0,
            blocked_root_count=0,
            provider_attempt_count=0,
            metrics_ref=None,
        ),
    )

    result = formal_runner.execute_paper_formal_suite(
        dispatch_plans=(plan,),
        catalog_manifest={"catalog_digest": CATALOG_DIGEST},
        budget=_budget(planned_conditions=1, planned_root_runs=1, planned_ai_units=1),
        budget_approval={
            "approval_mode": "user_bypassed",
            "budget_digest": BUDGET_DIGEST,
        },
        output_root=tmp_path,
        ai_api_configs={PROVIDER_CONFIG_ID: config},
        transport=object(),
        real_transport=False,
        hard_limits={},
    )

    assert result.status == PaperStatus.COMPLETED


@pytest.mark.parametrize(
    "drift",
    (
        "preflight_cohort_digest",
        "member_cohort_digest",
        "member_request_controls",
    ),
)
def test_exp5_endpoint_contract_rejects_cohort_or_control_drift(
    tmp_path: Path,
    drift: str,
) -> None:
    config = _ai_config()
    base_condition = _planned_dispatch_plan(
        tmp_path,
        config=config,
    ).conditions[0]
    condition = replace(
        base_condition,
        experiment_id=EXP5_EXPERIMENT_ID,
        model_cohort_id=MODEL_COHORT_ID,
        model_cohort_digest=MODEL_COHORT_DIGEST,
        cohort_member_id=COHORT_MEMBER_ID,
    )
    request_controls = {
        **config.defaults,
        **config.entries[0].request_overrides,
    }
    member_plan = {
        "cohort_id": MODEL_COHORT_ID,
        "model_cohort_digest": MODEL_COHORT_DIGEST,
        "cohort_member_id": COHORT_MEMBER_ID,
        "provider_config_id": PROVIDER_CONFIG_ID,
        "selected_entry_id": MODEL_ENTRY_ID,
        "provider_family": "siliconflow",
        "provider_model_id": PROVIDER_MODEL_ID,
        "reasoning_profile_id": "default",
        "source_provider_config_digest": config.config_digest,
        "model_endpoint_identity_digest": ENDPOINT_DIGEST,
        "request_controls": request_controls,
    }
    approved_binding = {
        "status": "planned",
        "cohort_id": MODEL_COHORT_ID,
        "model_cohort_digest": MODEL_COHORT_DIGEST,
        "member_plans": {COHORT_MEMBER_ID: member_plan},
    }
    if drift == "preflight_cohort_digest":
        approved_binding["model_cohort_digest"] = "sha256:" + "9" * 64
    elif drift == "member_cohort_digest":
        member_plan["model_cohort_digest"] = "sha256:" + "9" * 64
    else:
        member_plan["request_controls"] = {
            **request_controls,
            "max_tokens": 2048,
        }

    with pytest.raises(ValueError, match="Exp5"):
        formal_runner._condition_endpoint_contract(
            experiment_id=EXP5_EXPERIMENT_ID,
            condition=condition,
            ai_api_configs={
                PROVIDER_CONFIG_ID: config,
                formal_runner.APPROVED_ENDPOINT_BINDINGS_KEY: {
                    EXP5_EXPERIMENT_ID: approved_binding,
                },
            },
        )


def test_formal_runner_blocked_plan_never_dispatches_or_calls_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport_calls: list[object] = []

    def forbidden_dispatch(**_kwargs):
        raise AssertionError("blocked formal plan must not dispatch a condition")

    def transport(*_args, **_kwargs):
        transport_calls.append(object())
        raise AssertionError("blocked formal plan must not call transport")

    monkeypatch.setattr(formal_runner, "dispatch_paper_condition", forbidden_dispatch)
    blocked_plan = _dispatch_plan_type()(
        experiment_id="exp5_real_ai_model_endpoint_comparison",
        output_root=(tmp_path / "exp5_real_ai_model_endpoint_comparison").as_posix(),
        conditions=(),
        condition_selection_bindings=(),
        status="blocked",
        blocked_reason="incomplete endpoint cohort",
        paper_eligible_possible=False,
    )

    suite = formal_runner.execute_paper_formal_suite(
        dispatch_plans=(blocked_plan,),
        catalog_manifest=SimpleNamespace(catalog_digest=CATALOG_DIGEST),
        budget=_budget(
            planned_experiments=(),
            planned_conditions=0,
            planned_root_runs=0,
            planned_ai_units=0,
        ),
        budget_approval={
            "approval_mode": "user_bypassed",
            "budget_digest": BUDGET_DIGEST,
        },
        output_root=tmp_path,
        ai_api_configs={},
        transport=transport,
        real_transport=False,
        hard_limits={},
    )

    assert transport_calls == []
    assert suite.status == PaperStatus.BLOCKED
    assert suite.provider_attempt_count == 0
    assert suite.paper_eligible is False


def test_formal_runner_hard_limit_blocks_second_root_and_resume_keeps_first_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _two_case_dispatch_plan(tmp_path, config=config)
    adapter_calls: list[str] = []

    class CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=EXPERIMENT_ID, rows=())

    def fake_case_dispatch(**kwargs):
        case_id = kwargs["case"]["case_id"]
        adapter_calls.append(case_id)
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=case_id,
        )

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", fake_case_dispatch)
    kwargs = {
        **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
        "catalog_manifest": {
            "catalog_digest": CATALOG_DIGEST,
            "factorization_cases": (
                {"case_id": "case-1", "expected_ai_unit_count": 1},
                {"case_id": "case-2", "expected_ai_unit_count": 1},
            ),
            "lean_cases": (),
            "lean_lemma_graph_cases": (),
        },
        "budget": _budget(
            planned_conditions=1,
            planned_root_runs=2,
            planned_ai_units=2,
        ),
        "hard_limits": {"max_total_provider_attempts": 1},
    }

    exhausted = formal_runner.execute_paper_formal_suite(**kwargs)
    assert exhausted.status == PaperStatus.BUDGET_EXHAUSTED
    assert adapter_calls == ["case-1"]

    resumed = formal_runner.execute_paper_formal_suite(**kwargs, resume=True)
    assert resumed.status == PaperStatus.BUDGET_EXHAUSTED
    assert adapter_calls == ["case-1"]


def test_formal_runner_checkpoints_adapter_runtime_error_as_failed_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)

    class CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=EXPERIMENT_ID, rows=())

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, CallbackModule()),),
    )
    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_case",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("adapter failed")),
    )

    suite = formal_runner.execute_paper_formal_suite(
        **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan)
    )

    assert suite.status == PaperStatus.COMPLETED_WITH_FAILURES
    run_root = tmp_path / "experiments" / EXPERIMENT_ID / "runs" / "condition-1" / "0"
    current = json.loads((run_root / "CURRENT.json").read_text(encoding="utf-8"))
    generation_root = run_root / ".generations" / current["generation_id"]
    task = json.loads(
        (generation_root / "per_task_results.jsonl").read_text(encoding="utf-8")
    )
    attempt = json.loads(
        (generation_root / "per_attempt_results.jsonl").read_text(encoding="utf-8")
    )
    assert task["root_status"] == "failed"
    assert task["error_kind"] == "RuntimeError"
    assert attempt["attempt_status"] == "failed"
    assert (run_root / "artifacts" / "case-1" / "runner-error.json").is_file()


def test_formal_runner_exp2_uses_condition_worker_count_for_actual_scheduling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment_id = "exp2_real_ai_scalability"
    config = _ai_config()
    base_plan = _two_case_dispatch_plan(tmp_path, config=config)
    base_condition, base_selection = base_plan.bound_items()[0]
    condition = replace(
        base_condition,
        experiment_id=experiment_id,
        condition_id="condition-exp2-workers-2",
        worker_count=2,
    )
    selection = replace(base_selection, experiment_id=experiment_id)
    plan = replace(
        base_plan,
        experiment_id=experiment_id,
        output_root=(tmp_path / experiment_id).as_posix(),
        conditions=(condition,),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(condition, selection),
        ),
    )
    lock = Lock()
    active = 0
    observed_max = 0

    class Exp2CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
                experiment_id=experiment_id,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=experiment_id, rows=())

    def fake_case_dispatch(**kwargs):
        nonlocal active, observed_max
        with lock:
            active += 1
            observed_max = max(observed_max, active)
        sleep(0.05)
        with lock:
            active -= 1
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((experiment_id, Exp2CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", fake_case_dispatch)
    kwargs = {
        **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
        "catalog_manifest": {
            "catalog_digest": CATALOG_DIGEST,
            "factorization_cases": (
                {"case_id": "case-1"},
                {"case_id": "case-2"},
            ),
            "lean_cases": (),
            "lean_lemma_graph_cases": (),
        },
        "budget": _budget(
            planned_experiments=(experiment_id,),
            planned_conditions=1,
            planned_root_runs=2,
            planned_ai_units=2,
        ),
    }

    suite = formal_runner.execute_paper_formal_suite(**kwargs)

    assert suite.status == PaperStatus.COMPLETED
    assert observed_max == 2
    run_root = (
        tmp_path
        / "experiments"
        / experiment_id
        / "runs"
        / condition.condition_id
        / "0"
    )
    current = json.loads((run_root / "CURRENT.json").read_text(encoding="utf-8"))
    event_log = (
        run_root
        / ".generations"
        / current["generation_id"]
        / "events"
        / "event_log.jsonl"
    ).read_text(encoding="utf-8")
    assert "AI_UNIT_STARTED" in event_log
    assert "MERGE_GATE_COMPLETED" in event_log


def test_formal_runner_exp2_reserves_hard_limit_before_parallel_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment_id = "exp2_real_ai_scalability"
    config = _ai_config()
    base_plan = _two_case_dispatch_plan(tmp_path, config=config)
    base_condition, base_selection = base_plan.bound_items()[0]
    condition = replace(
        base_condition,
        experiment_id=experiment_id,
        condition_id="condition-exp2-hard-limit",
        worker_count=2,
    )
    selection = replace(base_selection, experiment_id=experiment_id)
    plan = replace(
        base_plan,
        experiment_id=experiment_id,
        output_root=(tmp_path / experiment_id).as_posix(),
        conditions=(condition,),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(condition, selection),
        ),
    )
    adapter_calls: list[str] = []
    call_lock = Lock()

    class Exp2CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
                experiment_id=experiment_id,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=experiment_id, rows=())

    def fake_case_dispatch(**kwargs):
        with call_lock:
            adapter_calls.append(kwargs["case"]["case_id"])
        sleep(0.05)
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((experiment_id, Exp2CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", fake_case_dispatch)
    suite = formal_runner.execute_paper_formal_suite(
        **{
            **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
            "catalog_manifest": {
                "catalog_digest": CATALOG_DIGEST,
                "factorization_cases": (
                    {"case_id": "case-1", "expected_ai_unit_count": 1},
                    {"case_id": "case-2", "expected_ai_unit_count": 1},
                ),
                "lean_cases": (),
                "lean_lemma_graph_cases": (),
            },
            "budget": _budget(
                planned_experiments=(experiment_id,),
                planned_conditions=1,
                planned_root_runs=2,
                planned_ai_units=2,
            ),
            "hard_limits": {"max_total_provider_attempts": 1},
        }
    )

    assert adapter_calls == ["case-1"]
    assert suite.provider_attempt_count == 1
    assert suite.status == PaperStatus.BUDGET_EXHAUSTED


def test_formal_runner_does_not_deduplicate_same_root_across_conditions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    base = _planned_dispatch_plan(tmp_path, config=config)
    first_condition, first_selection = base.bound_items()[0]
    second_condition = replace(
        first_condition,
        condition_id="condition-2",
        repeat_id=1,
        seed=2,
    )
    second_selection = replace(first_selection, selection_id="selection-2")
    plan = replace(
        base,
        conditions=(first_condition, second_condition),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(
                first_condition,
                first_selection,
            ),
            FrozenConditionSelectionBinding.from_condition(
                second_condition,
                second_selection,
            ),
        ),
    )
    calls: list[tuple[str, str]] = []

    class CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=EXPERIMENT_ID, rows=())

    def fake_case_dispatch(**kwargs):
        calls.append((kwargs["condition"].condition_id, kwargs["case"]["case_id"]))
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", fake_case_dispatch)
    suite = formal_runner.execute_paper_formal_suite(
        **{
            **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
            "budget": _budget(
                planned_conditions=2,
                planned_root_runs=2,
                planned_ai_units=2,
            ),
        }
    )

    assert suite.status == PaperStatus.COMPLETED
    assert calls == [("condition-1", "case-1"), ("condition-2", "case-1")]


def test_formal_runner_exp3_consumes_post_ai_fault_manifest_and_replaces_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment_id = "exp3_real_ai_fault_recovery"
    config = _ai_config()
    plan = _plan_for_experiment(
        tmp_path=tmp_path,
        config=config,
        experiment_id=experiment_id,
        condition_id="condition-exp3-rate-fault",
        fault_type="false_positive",
        fault_rate=1.0,
    )
    adapter_calls: list[str | None] = []

    class Exp3CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
                experiment_id=experiment_id,
                execution_manifest={
                    "fault_target_manifest": {
                        "fault_type": "false_positive",
                        "selected_target_ai_unit_ids": ["unit-case-1"],
                    },
                    "worker_death_manifest": None,
                },
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=experiment_id, rows=())

    def fake_case_dispatch(**kwargs):
        selected_unit_id = kwargs.get("selected_ai_unit_id")
        adapter_calls.append(selected_unit_id)
        return _faultable_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
            attempt_suffix="replacement" if selected_unit_id else "original",
        )

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((experiment_id, Exp3CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", fake_case_dispatch)
    suite = formal_runner.execute_paper_formal_suite(
        **{
            **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
            "budget": _budget(
                planned_experiments=(experiment_id,),
                planned_conditions=1,
                planned_root_runs=1,
                planned_ai_units=1,
            ),
        }
    )

    assert suite.status == PaperStatus.COMPLETED
    assert adapter_calls == [None, "unit-case-1"]
    attempts = _generation_records(
        tmp_path, experiment_id, "condition-exp3-rate-fault", "per_attempt_results.jsonl"
    )
    faults = _generation_records(
        tmp_path, experiment_id, "condition-exp3-rate-fault", "fault_injections.jsonl"
    )
    events = _generation_records(
        tmp_path,
        experiment_id,
        "condition-exp3-rate-fault",
        "events/event_log.jsonl",
    )
    assert {attempt["attempt_id"] for attempt in attempts} == {
        "attempt-case-1-original",
        "attempt-case-1-replacement",
    }
    assert faults[0]["original_output_ref"]["artifact_id"] == "parsed-case-1-original"
    assert faults[0]["mutated_output_ref"]["artifact_id"].endswith("mutated_output")
    event_types = [event["event_type"] for event in events]
    assert event_types.index("PROVIDER_RAW_PERSISTED") < event_types.index("FAULT_INJECTED")
    assert event_types.index("FAULT_INJECTED") < event_types.index("REPLACEMENT_ACCEPTED")


def test_formal_runner_exp3_worker_death_persists_real_process_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment_id = "exp3_real_ai_fault_recovery"
    config = _ai_config()
    plan = _plan_for_experiment(
        tmp_path=tmp_path,
        config=config,
        experiment_id=experiment_id,
        condition_id="condition-exp3-worker-death",
        fault_type="worker_death",
        fault_rate=0.0,
    )

    class Exp3WorkerCallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
                experiment_id=experiment_id,
                execution_manifest={
                    "fault_target_manifest": None,
                    "worker_death_manifest": {
                        "dead_worker_count_target": 1,
                        "kill_progress_target_percent": 25,
                    },
                },
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=experiment_id, rows=())

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((experiment_id, Exp3WorkerCallbackModule()),),
    )
    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_case",
        lambda **kwargs: _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        ),
    )
    suite = formal_runner.execute_paper_formal_suite(
        **{
            **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
            "budget": _budget(
                planned_experiments=(experiment_id,),
                planned_conditions=1,
                planned_root_runs=1,
                planned_ai_units=1,
            ),
        }
    )

    assert suite.status == PaperStatus.COMPLETED
    faults = _generation_records(
        tmp_path, experiment_id, "condition-exp3-worker-death", "fault_injections.jsonl"
    )
    record = faults[0]
    assert record["worker_process_exitcode"] not in (0, None)
    assert record["replacement_process_exitcode"] == 0
    assert record["coordinator"]["survived"] is True


def test_formal_runner_exp4_persists_mode_specific_wrapper_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment_id = "exp4_real_ai_protocol_ablation"
    config = _ai_config()
    plan = _plan_for_experiment(
        tmp_path=tmp_path,
        config=config,
        experiment_id=experiment_id,
        condition_id="condition-exp4-no-verification",
        ablation_mode="NO_VERIFICATION",
    )
    dispatched_modes: list[str | None] = []

    class Exp4CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
                experiment_id=experiment_id,
                mode_config={"ablation_mode": "NO_VERIFICATION"},
                ablation_profile={"disabled_mechanisms": ["verification"]},
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=experiment_id, rows=())

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((experiment_id, Exp4CallbackModule()),),
    )
    def rejected_dispatch(**kwargs):
        dispatched_modes.append(kwargs.get("ablation_mode"))
        return _rejected_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )

    monkeypatch.setattr(formal_runner, "dispatch_paper_case", rejected_dispatch)
    suite = formal_runner.execute_paper_formal_suite(
        **{
            **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
            "budget": _budget(
                planned_experiments=(experiment_id,),
                planned_conditions=1,
                planned_root_runs=1,
                planned_ai_units=1,
            ),
        }
    )

    assert suite.status == PaperStatus.COMPLETED_WITH_FAILURES
    assert dispatched_modes == ["NO_VERIFICATION", "NO_VERIFICATION"]
    task = _generation_records(
        tmp_path, experiment_id, "condition-exp4-no-verification", "per_task_results.jsonl"
    )[0]
    events = _generation_records(
        tmp_path,
        experiment_id,
        "condition-exp4-no-verification",
        "events/event_log.jsonl",
    )
    assert task["root_status"] == "failed"
    assert task["ablation_runtime_flags"]["wrong_canonical_exposed"] is True
    assert task["final_deterministic_validity"] is False
    assert any(event["event_type"] == "ABLATION_BOUNDARY_APPLIED" for event in events)


@pytest.mark.parametrize(
    ("mode", "expected_calls", "expected_status", "expected_stuck"),
    (
        ("FULL", 2, PaperStatus.COMPLETED, False),
        ("NO_REQUEUE", 1, PaperStatus.COMPLETED_WITH_FAILURES, True),
    ),
)
def test_formal_runner_exp4_no_requeue_changes_replacement_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected_calls: int,
    expected_status: PaperStatus,
    expected_stuck: bool,
) -> None:
    experiment_id = "exp4_real_ai_protocol_ablation"
    config = _ai_config()
    plan = _plan_for_experiment(
        tmp_path=tmp_path,
        config=config,
        experiment_id=experiment_id,
        condition_id=f"condition-exp4-{mode.lower()}",
        ablation_mode=mode,
    )
    dispatch_calls: list[Path] = []

    class Exp4CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
                experiment_id=experiment_id,
                mode_config={"ablation_mode": mode},
                ablation_profile={"disabled_mechanisms": ["requeue"]},
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=experiment_id, rows=())

    def rejecting_then_complete_dispatch(**kwargs):
        output_root = Path(kwargs["output_root"])
        dispatch_calls.append(output_root)
        builder = (
            _rejected_adapter_result
            if len(dispatch_calls) == 1
            else _complete_adapter_result
        )
        return builder(
            output_root=output_root,
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((experiment_id, Exp4CallbackModule()),),
    )
    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_case",
        rejecting_then_complete_dispatch,
    )
    suite = formal_runner.execute_paper_formal_suite(
        **{
            **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
            "budget": _budget(
                planned_experiments=(experiment_id,),
                planned_conditions=1,
                planned_root_runs=1,
                planned_ai_units=1,
                max_provider_attempts=expected_calls,
            ),
        }
    )

    assert len(dispatch_calls) == expected_calls
    assert suite.status == expected_status
    task = _generation_records(
        tmp_path,
        experiment_id,
        f"condition-exp4-{mode.lower()}",
        "per_task_results.jsonl",
    )[0]
    assert task["stuck_after_rejection"] is expected_stuck
    assert task["replacement_attempt_count"] == expected_calls - 1


def test_formal_runner_exp5_persists_fixed_entry_model_execution_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    base = _planned_dispatch_plan(tmp_path, config=config)
    base_condition, base_selection = base.bound_items()[0]
    condition = replace(
        base_condition,
        experiment_id=EXP5_EXPERIMENT_ID,
        condition_id="condition-exp5-fixed-entry",
        model_cohort_id=MODEL_COHORT_ID,
        model_cohort_digest=MODEL_COHORT_DIGEST,
        cohort_member_id=COHORT_MEMBER_ID,
    )
    selection = replace(base_selection, experiment_id=EXP5_EXPERIMENT_ID)
    plan = replace(
        base,
        experiment_id=EXP5_EXPERIMENT_ID,
        output_root=(tmp_path / EXP5_EXPERIMENT_ID).as_posix(),
        conditions=(condition,),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(condition, selection),
        ),
    )
    request_controls = {**config.defaults, **config.entries[0].request_overrides}
    member_plan = {
        "cohort_id": MODEL_COHORT_ID,
        "model_cohort_digest": MODEL_COHORT_DIGEST,
        "cohort_member_id": COHORT_MEMBER_ID,
        "provider_config_id": PROVIDER_CONFIG_ID,
        "selected_entry_id": MODEL_ENTRY_ID,
        "provider_family": "siliconflow",
        "provider_model_id": PROVIDER_MODEL_ID,
        "reasoning_profile_id": "default",
        "source_provider_config_digest": config.config_digest,
        "model_endpoint_identity_digest": ENDPOINT_DIGEST,
        "request_controls": request_controls,
    }

    class Exp5CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
                experiment_id=EXP5_EXPERIMENT_ID,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=EXP5_EXPERIMENT_ID, rows=())

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXP5_EXPERIMENT_ID, Exp5CallbackModule()),),
    )
    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_case",
        lambda **kwargs: _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        ),
    )
    suite = formal_runner.execute_paper_formal_suite(
        **{
            **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
            "ai_api_configs": {
                PROVIDER_CONFIG_ID: config,
                formal_runner.APPROVED_ENDPOINT_BINDINGS_KEY: {
                    EXP5_EXPERIMENT_ID: {
                        "status": "planned",
                        "cohort_id": MODEL_COHORT_ID,
                        "model_cohort_digest": MODEL_COHORT_DIGEST,
                        "member_plans": {COHORT_MEMBER_ID: member_plan},
                    }
                },
            },
            "budget": _budget(
                planned_experiments=(EXP5_EXPERIMENT_ID,),
                planned_conditions=1,
                planned_root_runs=1,
                planned_ai_units=1,
            ),
        }
    )

    assert suite.status == PaperStatus.COMPLETED
    task = _generation_records(
        tmp_path, EXP5_EXPERIMENT_ID, condition.condition_id, "per_task_results.jsonl"
    )[0]
    attempt = _generation_records(
        tmp_path,
        EXP5_EXPERIMENT_ID,
        condition.condition_id,
        "per_attempt_results.jsonl",
    )[0]
    assert task["model_execution_records"][0]["entry_id"] == MODEL_ENTRY_ID
    assert attempt["model_identity_audit"] == "fixed_entry_match"
    assert attempt["cohort_member_id"] == COHORT_MEMBER_ID


def _planned_dispatch_plan(
    output_root: Path,
    *,
    config: AIAPIExecutorConfig,
) -> formal_runner.PaperExperimentDispatchPlan:
    condition = PaperExperimentCondition(
        experiment_id=EXPERIMENT_ID,
        condition_id="condition-1",
        domain="factorization",
        difficulty="easy",
        paper_difficulty="easy",
        worker_count=1,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="full_protocol",
        model_policy="fixed_entry",
        provider_config_id=PROVIDER_CONFIG_ID,
        model_entry_id=MODEL_ENTRY_ID,
        provider_family="siliconflow",
        provider_model_id=PROVIDER_MODEL_ID,
        reasoning_profile_id="default",
        source_provider_config_digest=config.config_digest,
        model_endpoint_identity_digest=ENDPOINT_DIGEST,
        repeat_id=0,
        seed=1,
        catalog_digest=CATALOG_DIGEST,
    )
    selection = FrozenCaseSelection(
        selection_id="selection-1",
        experiment_id=EXPERIMENT_ID,
        suite_version="paper_v1",
        catalog_version="catalog-v1",
        domain="factorization",
        paper_difficulty="easy",
        topic_family=None,
        ordered_case_ids=("case-1",),
        catalog_digest=CATALOG_DIGEST,
        expected_ai_unit_count=1,
        paper_eligible_required=True,
    )
    return _dispatch_plan_type()(
        experiment_id=EXPERIMENT_ID,
        output_root=(output_root / EXPERIMENT_ID).as_posix(),
        conditions=(condition,),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(condition, selection),
        ),
    )


def _two_case_dispatch_plan(
    output_root: Path,
    *,
    config: AIAPIExecutorConfig,
) -> formal_runner.PaperExperimentDispatchPlan:
    plan = _planned_dispatch_plan(output_root, config=config)
    condition, selection = plan.bound_items()[0]
    selection = replace(
        selection,
        ordered_case_ids=("case-1", "case-2"),
        expected_ai_unit_count=2,
    )
    return replace(
        plan,
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(condition, selection),
        ),
    )


def _plan_for_experiment(
    *,
    tmp_path: Path,
    config: AIAPIExecutorConfig,
    experiment_id: str,
    condition_id: str,
    fault_type: str = "none",
    fault_rate: float = 0.0,
    ablation_mode: str = "full_protocol",
) -> formal_runner.PaperExperimentDispatchPlan:
    base = _planned_dispatch_plan(tmp_path, config=config)
    base_condition, base_selection = base.bound_items()[0]
    condition = replace(
        base_condition,
        experiment_id=experiment_id,
        condition_id=condition_id,
        fault_type=fault_type,
        fault_rate=fault_rate,
        ablation_mode=ablation_mode,
    )
    selection = replace(base_selection, experiment_id=experiment_id)
    return replace(
        base,
        experiment_id=experiment_id,
        output_root=(tmp_path / experiment_id).as_posix(),
        conditions=(condition,),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(condition, selection),
        ),
    )


def _ai_config() -> AIAPIExecutorConfig:
    return AIAPIExecutorConfig(
        schema_version="phase7.ai_api_executor_config.v1",
        executor_id="formal-runner-test",
        provider_family="siliconflow",
        selection_policy={"kind": "fixed"},
        defaults={
            "timeout_seconds": 30,
            "max_tokens": 512,
            "temperature": 0.2,
            "top_p": 1.0,
            "stream": False,
            "max_provider_attempts": 1,
        },
        entries=[
            AIAPIProviderEntry(
                entry_id=MODEL_ENTRY_ID,
                enabled=True,
                base_url="https://example.invalid/v1",
                api_key_env="TOKENSHARE_FORMAL_RUNNER_TEST_KEY",
                model=PROVIDER_MODEL_ID,
                endpoint="/chat/completions",
                supports_json_mode=True,
                supports_streaming=False,
                request_overrides={
                    "max_tokens": 1024,
                    "temperature": 0.0,
                    "enable_thinking": False,
                },
                pricing={
                    "currency": "USD",
                    "input_per_million_tokens": 1.0,
                    "output_per_million_tokens": 2.0,
                },
                tags=["test"],
            )
        ],
        local_concurrency={"max_in_flight_global": 1},
        metadata={"test_only": True},
    )


def _dispatch_plan_type():
    return formal_runner.execute_paper_formal_suite.__globals__[
        "PaperExperimentDispatchPlan"
    ]


def _budget(
    *,
    planned_experiments: tuple[str, ...] = (EXPERIMENT_ID,),
    planned_conditions: int,
    planned_root_runs: int,
    planned_ai_units: int,
    max_provider_attempts: int | None = None,
) -> PaperBudgetResult:
    attempt_upper_bound = (
        planned_ai_units
        if max_provider_attempts is None
        else max_provider_attempts
    )
    return PaperBudgetResult(
        budget_digest=BUDGET_DIGEST,
        planned_experiments=planned_experiments,
        planned_conditions=planned_conditions,
        planned_root_runs=planned_root_runs,
        planned_ai_units=planned_ai_units,
        max_provider_attempts=attempt_upper_bound,
        token_upper_bound=attempt_upper_bound * 1024,
        cost_upper_bound=attempt_upper_bound * 0.01,
        wall_clock_estimate=float(max(1, planned_ai_units)),
        quota_preflight={"provider_calls_made": 0},
        rate_limit_preflight={"status": "not_checked"},
        disk_estimate={"bytes": 0},
        status=PaperStatus.PLANNED,
    )


def _formal_execution_kwargs(
    *,
    tmp_path: Path,
    config: AIAPIExecutorConfig,
    plan: formal_runner.PaperExperimentDispatchPlan,
) -> dict[str, object]:
    return {
        "dispatch_plans": (plan,),
        "catalog_manifest": {
            "catalog_digest": CATALOG_DIGEST,
            "factorization_cases": (
                {"case_id": "case-1", "expected_ai_unit_count": 1},
            ),
            "lean_cases": (),
            "lean_lemma_graph_cases": (),
        },
        "budget": _budget(
            planned_conditions=1,
            planned_root_runs=1,
            planned_ai_units=1,
        ),
        "budget_approval": {
            "approval_mode": "user_bypassed",
            "budget_digest": BUDGET_DIGEST,
        },
        "output_root": tmp_path,
        "ai_api_configs": {PROVIDER_CONFIG_ID: config},
        "transport": object(),
        "real_transport": False,
        "hard_limits": {},
    }


def _complete_adapter_result(
    *,
    output_root: Path,
    condition: PaperExperimentCondition,
    case_id: str,
) -> SimpleNamespace:
    adapter_store = ArtifactStore(output_root)
    raw_ref = adapter_store.save_bytes(
        b"raw model output",
        artifact_id="raw-output.txt",
        artifact_type="raw_model_output",
        media_type="text/plain",
        artifact_schema_id="test.raw_output",
        artifact_schema_version="v1",
        source={"test": "formal-runner"},
        metadata={},
        created_at="2026-07-20T00:00:00Z",
    )
    request_ref = adapter_store.save_bytes(
        b"request body",
        artifact_id="request.json",
        artifact_type="provider_request",
        media_type="application/json",
        artifact_schema_id="test.request",
        artifact_schema_version="v1",
        source={"test": "formal-runner"},
        metadata={},
        created_at="2026-07-20T00:00:00Z",
    )
    attempt = PaperAttemptResult(
        condition_id=condition.condition_id,
        repeat_id=condition.repeat_id,
        run_id=f"run-{case_id}",
        task_id=case_id,
        unit_id=f"unit-{case_id}",
        attempt_id=f"attempt-{case_id}",
        worker_id="worker-1",
        provider_attempt_index=1,
        attempt_status=PaperAttemptStatus.SUCCEEDED,
        provider="siliconflow",
        model=PROVIDER_MODEL_ID,
        entry_id=MODEL_ENTRY_ID,
        request_ref=request_ref.to_dict(),
        raw_output_ref=raw_ref.to_dict(),
        parsed_output_ref=None,
        parse_failure_ref=None,
        provenance_ref=None,
        usage_ref=None,
        started_at="2026-07-20T00:00:00Z",
        ended_at="2026-07-20T00:00:01Z",
        latency_ms=1000,
        prompt_tokens=5,
        completion_tokens=6,
        total_tokens=11,
        cost_estimate=0.125,
        error_kind=None,
        fault_injection_ref=None,
        paper_eligible=False,
    )
    task = SimpleNamespace(
        task_id=case_id,
        repeat_id=condition.repeat_id,
        root_status=PaperTaskStatus.COMPLETED,
        provider_attempt_count=1,
        total_tokens=11,
        cost_estimate=0.125,
        artifact_refs=[raw_ref.to_dict(), request_ref.to_dict()],
        event_refs=[{"event_id": f"event-{case_id}", "event_type": "TASK_COMPLETED"}],
    )
    return SimpleNamespace(
        task_result=task,
        attempt_results=[attempt],
        fault_records=[],
        event_records=[{"event_id": f"event-{case_id}", "event_type": "TASK_COMPLETED"}],
        eligibility_report=SimpleNamespace(paper_eligible=False),
    )


def _faultable_adapter_result(
    *,
    output_root: Path,
    condition: PaperExperimentCondition,
    case_id: str,
    attempt_suffix: str,
) -> SimpleNamespace:
    store = ArtifactStore(output_root)
    created_at = "2026-07-19T00:00:00Z"

    def save_json(artifact_id: str, artifact_type: str, body: dict[str, object]):
        return store.save_json(
            body,
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            artifact_schema_id=f"test.{artifact_type.lower()}",
            artifact_schema_version="v1",
            source={"test": "formal-exp3-runner"},
            metadata={},
            created_at=created_at,
        )

    raw_ref = save_json(
        f"raw-{case_id}-{attempt_suffix}",
        "RawModelOutput",
        {"content_text": '{"result_kind":"no_factor"}'},
    )
    parsed_ref = save_json(
        f"parsed-{case_id}-{attempt_suffix}",
        "ParsedModelOutput",
        {
            "result_kind": "no_factor",
            "target_n": "91",
            "range_start": "2",
            "range_end": "10",
            "found_factor": None,
            "cofactor": None,
        },
    )
    provenance_ref = save_json(
        f"provenance-{case_id}-{attempt_suffix}",
        "AIProviderCallProvenance",
        {"provider_family": "siliconflow", "model": PROVIDER_MODEL_ID},
    )
    usage_ref = save_json(
        f"usage-{case_id}-{attempt_suffix}",
        "AIUsage",
        {"total_tokens": 11, "cost_estimate": 0.125},
    )
    request_ref = save_json(
        f"request-{case_id}-{attempt_suffix}",
        "AIRequest",
        {"model": PROVIDER_MODEL_ID},
    )
    attempt = PaperAttemptResult(
        condition_id=condition.condition_id,
        repeat_id=condition.repeat_id,
        run_id=f"run-{case_id}",
        task_id=case_id,
        unit_id=f"unit-{case_id}",
        attempt_id=f"attempt-{case_id}-{attempt_suffix}",
        worker_id="worker-1",
        provider_attempt_index=1 if attempt_suffix == "original" else 2,
        attempt_status=PaperAttemptStatus.SUCCEEDED,
        provider="siliconflow",
        model=PROVIDER_MODEL_ID,
        entry_id=MODEL_ENTRY_ID,
        request_ref=request_ref.to_dict(),
        raw_output_ref=raw_ref.to_dict(),
        parsed_output_ref=parsed_ref.to_dict(),
        parse_failure_ref=None,
        provenance_ref=provenance_ref.to_dict(),
        usage_ref=usage_ref.to_dict(),
        started_at=created_at,
        ended_at="2026-07-19T00:00:01Z",
        latency_ms=1000,
        prompt_tokens=5,
        completion_tokens=6,
        total_tokens=11,
        cost_estimate=0.125,
        error_kind=None,
        fault_injection_ref=None,
        paper_eligible=False,
    )
    task = SimpleNamespace(
        task_id=case_id,
        repeat_id=condition.repeat_id,
        root_status=PaperTaskStatus.COMPLETED,
        provider_attempt_count=1,
        total_tokens=11,
        cost_estimate=0.125,
        artifact_refs=[
            raw_ref.to_dict(),
            parsed_ref.to_dict(),
            provenance_ref.to_dict(),
            usage_ref.to_dict(),
            request_ref.to_dict(),
        ],
        event_refs=[],
    )
    return SimpleNamespace(
        task_result=task,
        attempt_results=[attempt],
        fault_records=[],
        event_records=[
            {
                "event_id": f"event-{case_id}-{attempt_suffix}",
                "event_type": "TASK_COMPLETED",
            }
        ],
        eligibility_report=SimpleNamespace(paper_eligible=False),
    )


def _rejected_adapter_result(
    *,
    output_root: Path,
    condition: PaperExperimentCondition,
    case_id: str,
) -> SimpleNamespace:
    result = _faultable_adapter_result(
        output_root=output_root,
        condition=condition,
        case_id=case_id,
        attempt_suffix="rejected",
    )
    rejected_attempt = replace(
        result.attempt_results[0],
        attempt_status=PaperAttemptStatus.VERIFICATION_REJECTED,
        error_kind="verifier_rejected",
    )
    task = SimpleNamespace(
        **{
            **vars(result.task_result),
            "root_status": PaperTaskStatus.FAILED,
        }
    )
    return SimpleNamespace(
        **{
            **vars(result),
            "task_result": task,
            "attempt_results": [rejected_attempt],
        }
    )


def _generation_records(
    suite_root: Path,
    experiment_id: str,
    condition_id: str,
    relative_path: str,
) -> list[dict[str, object]]:
    run_root = (
        suite_root
        / "experiments"
        / experiment_id
        / "runs"
        / condition_id
        / "0"
    )
    current = json.loads((run_root / "CURRENT.json").read_text(encoding="utf-8"))
    path = run_root / ".generations" / current["generation_id"] / relative_path
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
