import json
from copy import deepcopy
from dataclasses import replace
from importlib.util import find_spec
from pathlib import Path

import pytest

from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.experiments import paper_dispatcher
from tokenshare.experiments.factorization_paper_adapter import (
    ScriptedFactorizationRangeTransport,
)
from tokenshare.experiments.lean_paper_adapter import ScriptedLeanPaperProofTransport
from tokenshare.experiments.paper_budget import (
    load_exp1_pilot_profile,
    plan_paper_suite,
)
from tokenshare.experiments.paper_catalog import PaperInputCatalogManifest
from tokenshare.experiments.paper_factorization_catalog import (
    CATALOG_GENERATOR_VERSION as FACTORIZATION_V2_GENERATOR_VERSION,
)
from tokenshare.experiments.paper_experiment_contracts import (
    ExperimentSummaryRows,
    FrozenCaseSelection,
    PaperExecutionContext,
    PaperExperimentModule,
)
from tokenshare.experiments.paper_models import (
    PaperConditionResult,
    PaperExperimentCondition,
    PaperStatus,
    digest_json,
)
from tokenshare.experiments.paper_model_identity import PaperModelEndpointIdentity
from tokenshare.experiments.paper_model_policy import (
    EXP5_COMPARABLE_REQUEST_CONTROL_FIELDS,
    EXP5_DOMAIN_EXECUTION_CONTRACTS,
    PAPER_MODEL_ENDPOINT_COHORT_ID,
    PAPER_MODEL_ENDPOINT_COHORT_MEMBER_IDS,
    PAPER_MODEL_ENDPOINT_COHORT_MEMBERS,
)
from tokenshare.experiments.paper_metrics import (
    recompute_gate_c_pilot_metrics,
    write_gate_c_pilot_metrics,
)
from tokenshare.experiments import paper_runner
from tokenshare.experiments import paper_formal_runner
from tokenshare.experiments.paper_runner import build_gate_c_dispatch_plans
from tokenshare.local_runtime import ProtocolRunRequest, ProtocolRunResult


EXPERIMENT_IDS = (
    "exp1_real_ai_feasibility",
    "exp2_real_ai_scalability",
    "exp3_real_ai_fault_recovery",
    "exp4_real_ai_protocol_ablation",
    "exp5_real_ai_model_endpoint_comparison",
)


def test_gate_c_shared_dispatcher_module_exists() -> None:
    assert find_spec("tokenshare.experiments.paper_dispatcher") is not None


def test_gate_c_dispatcher_registers_all_modules_through_protocol() -> None:
    registered_ids = getattr(
        paper_dispatcher,
        "registered_paper_experiment_ids",
        None,
    )
    load_module = getattr(paper_dispatcher, "load_paper_experiment_module", None)

    assert callable(registered_ids)
    assert callable(load_module)
    assert registered_ids() == EXPERIMENT_IDS
    assert all(
        isinstance(load_module(experiment_id), PaperExperimentModule)
        for experiment_id in EXPERIMENT_IDS
    )

    with pytest.raises(ValueError, match="unsupported paper experiment"):
        load_module("exp6_not_registered")


def test_gate_c_plans_all_real_modules_from_frozen_formal_catalog(tmp_path) -> None:
    catalog = _frozen_formal_catalog_v2()
    readiness = json.loads(
        Path("benchmarks/paper/lean_task14_3x3_readiness.v1.json").read_text(
            encoding="utf-8"
        )
    )
    profile = load_exp1_pilot_profile(
        "benchmarks/paper/exp1_minimal_pilot_profile.v3.json"
    )
    identity = profile.model_endpoint_identity.to_dict()
    baseline_binding = {
        **identity,
        "model_entry_id": identity["selected_entry_id"],
        "request_controls": {
            "max_tokens": 300000,
            "timeout_seconds": 600,
            "max_provider_attempts": 1,
            "stream": False,
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
        },
    }

    plans = build_gate_c_dispatch_plans(
        catalog_manifest=catalog,
        lean_3x3_matrix=readiness,
        experiment_ids=EXPERIMENT_IDS,
        baseline_endpoint_binding=baseline_binding,
        model_endpoint_cohort_preflight={"status": "blocked"},
        output_root=tmp_path,
    )

    assert [plan.experiment_id for plan in plans] == list(EXPERIMENT_IDS)
    assert [len(plan.conditions) for plan in plans] == [12, 12, 162, 90, 0]
    assert [
        sum(len(selection.ordered_case_ids) for selection in plan.selections)
        for plan in plans
    ] == [635, 1_992, 36_126, 7_725, 0]
    assert all(plan.provider_calls_made == 0 for plan in plans)
    assert [plan.status for plan in plans] == [
        "planned",
        "planned",
        "planned",
        "planned",
        "blocked",
    ]
    assert plans[-1].blocked_reason == "incomplete_model_cohort"
    assert plans[-1].paper_eligible_possible is False
    for plan in plans[:4]:
        assert all(
            condition.model_cohort_id
            == profile.model_endpoint_identity.model_cohort_id
            and condition.model_cohort_digest
            == profile.model_endpoint_identity.model_cohort_digest
            and condition.cohort_member_id
            == profile.model_endpoint_identity.cohort_member_id
            for condition in plan.conditions
        )


def test_gate_c_v2_plans_freeze_current_p0_matrix_and_budget_identity(
    tmp_path: Path,
) -> None:
    catalog = _frozen_formal_catalog_v2()
    assert catalog.generator_version == FACTORIZATION_V2_GENERATOR_VERSION
    readiness = json.loads(
        Path("benchmarks/paper/lean_task14_3x3_readiness.v1.json").read_text(
            encoding="utf-8"
        )
    )
    profile = load_exp1_pilot_profile(
        "benchmarks/paper/exp1_minimal_pilot_profile.v3.json"
    )
    cohort_preflight, _configs = _complete_cohort_preflight()
    plans = build_gate_c_dispatch_plans(
        catalog_manifest=catalog,
        lean_3x3_matrix=readiness,
        experiment_ids=EXPERIMENT_IDS,
        baseline_endpoint_binding=_baseline_binding(profile),
        model_endpoint_cohort_preflight=cohort_preflight,
        output_root=tmp_path,
    )

    root_runs = [
        sum(len(selection.ordered_case_ids) for selection in plan.selections)
        for plan in plans
    ]
    assert root_runs == [635, 1_992, 36_126, 7_725, 1_899]
    assert sum(root_runs[:4]) == 46_478
    assert sum(root_runs) == 48_377

    all_factor_ids = {
        str(case["case_id"]) for case in catalog.factorization_cases
    }
    expected_sizes = {"easy": 167, "medium": 167, "hard": 166}
    for plan in (plans[0], plans[3]):
        grouped: dict[str, set[tuple[str, ...]]] = {}
        for condition, selection in plan.bound_items():
            if condition.domain != "factorization":
                continue
            grouped.setdefault(str(condition.paper_difficulty), set()).add(
                tuple(selection.ordered_case_ids)
            )
        assert set(grouped) == set(expected_sizes)
        assert all(len(selections) == 1 for selections in grouped.values())
        selected_union: set[str] = set()
        for difficulty, selections in grouped.items():
            ordered_ids = next(iter(selections))
            assert len(ordered_ids) == expected_sizes[difficulty]
            selected_union.update(ordered_ids)
        assert selected_union == all_factor_ids

    hard_factor_ids = {
        str(case["case_id"])
        for case in catalog.factorization_cases
        if case["difficulty"] == "hard"
    }
    for plan in (plans[1], plans[4]):
        factor_selections = {
            tuple(selection.ordered_case_ids)
            for condition, selection in plan.bound_items()
            if condition.domain == "factorization"
        }
        assert len(factor_selections) == 1
        assert set(next(iter(factor_selections))) == hard_factor_ids

    exp3_rate_selections = {
        tuple(selection.ordered_case_ids)
        for condition, selection in plans[2].bound_items()
        if condition.domain == "factorization"
        and condition.fault_type != "worker_death"
    }
    assert len(exp3_rate_selections) == 1
    assert set(next(iter(exp3_rate_selections))) == all_factor_ids

    exp3_death_by_difficulty: dict[str, set[tuple[str, ...]]] = {}
    for condition, selection in plans[2].bound_items():
        if (
            condition.domain != "factorization"
            or condition.fault_type != "worker_death"
        ):
            continue
        exp3_death_by_difficulty.setdefault(
            str(condition.paper_difficulty), set()
        ).add(tuple(selection.ordered_case_ids))
    assert set(exp3_death_by_difficulty) == set(expected_sizes)
    assert all(
        len(selections) == 1
        for selections in exp3_death_by_difficulty.values()
    )
    exp3_death_union: set[str] = set()
    for difficulty, selections in exp3_death_by_difficulty.items():
        ordered_ids = next(iter(selections))
        assert len(ordered_ids) == expected_sizes[difficulty]
        exp3_death_union.update(ordered_ids)
    assert exp3_death_union == all_factor_ids

    conditions = tuple(
        condition for plan in plans for condition in plan.conditions
    )
    frozen_selections = [
        {
            **selection.to_dict(),
            "condition_id": condition.condition_id,
            "condition_digest": condition.condition_digest,
        }
        for plan in plans
        for condition, selection in plan.bound_items()
    ]
    budget = plan_paper_suite(
        catalog_manifest=catalog,
        conditions=conditions,
        max_provider_attempts_per_ai_unit=1,
        token_upper_bound_per_provider_attempt=1024,
        cost_upper_bound_per_provider_attempt=0.01,
        plan_only=True,
        frozen_selections=frozen_selections,
        model_endpoint_cohort_preflight=cohort_preflight,
    )
    identity = budget.quota_preflight["budget_commitments"][
        "experiment_budget_identity"
    ]
    assert identity["headline_root_runs_by_experiment"] == {
        "exp1_real_ai_feasibility": 635,
        "exp2_real_ai_scalability": 1_992,
        "exp3_real_ai_fault_recovery": 36_126,
        "exp4_real_ai_protocol_ablation": 7_725,
        "exp5_real_ai_model_endpoint_comparison": 1_899,
    }
    assert identity["supporting_baseline_root_runs_by_experiment"] == {}
    assert identity["actual_scheduled_root_runs_by_experiment"] == {
        "exp1_real_ai_feasibility": 635,
        "exp2_real_ai_scalability": 1_992,
        "exp3_real_ai_fault_recovery": 36_126,
        "exp4_real_ai_protocol_ablation": 7_725,
        "exp5_real_ai_model_endpoint_comparison": 1_899,
    }
    assert identity["planned_first_attempt_ai_units_by_experiment"] == {
        "exp1_real_ai_feasibility": 2_900,
        "exp2_real_ai_scalability": 39_840,
        "exp3_real_ai_fault_recovery": 168_348,
        "exp4_real_ai_protocol_ablation": 35_910,
        "exp5_real_ai_model_endpoint_comparison": 14_652,
    }
    assert identity["headline_p0_core_root_runs"] == 46_478
    assert identity["headline_p0_full_root_runs"] == 48_377
    assert identity["actual_p0_core_root_runs"] == 46_478
    assert identity["actual_p0_full_root_runs"] == 48_377
    assert identity["exp2_no_early_stop_ai_unit_upper_bound"] == 39_840
    assert identity["exp4_replacement_reserve"] > 0
    assert identity["exp3_replacement_reserve"] > 0
    assert budget.planned_root_runs == 48_377
    assert budget.planned_ai_units == 261_650
    assert budget.max_provider_attempts == (
        budget.planned_ai_units
        + identity["exp3_replacement_reserve"]
        + identity["exp4_replacement_reserve"]
    )
    assert budget.quota_preflight["provider_calls_made"] == 0


def test_gate_c_dispatcher_delegates_plan_and_run_without_copying_module_logic(
    monkeypatch,
) -> None:
    module = _RecordingModule()
    monkeypatch.setattr(
        paper_dispatcher,
        "_MODULES",
        (("exp1_real_ai_feasibility", module),),
    )
    context = _context()

    plan_experiment = getattr(
        paper_dispatcher,
        "plan_paper_experiment",
        None,
    )
    dispatch_condition = getattr(
        paper_dispatcher,
        "dispatch_paper_condition",
        None,
    )

    assert callable(plan_experiment)
    assert callable(dispatch_condition)
    plan = plan_experiment(
        context=context,
        experiment_id="exp1_real_ai_feasibility",
    )
    assert module.calls == ["expand", "freeze"]
    assert plan.output_root == "outputs/gate-c/exp1"
    assert plan.provider_calls_made == 0
    assert plan.conditions == (module.condition,)
    assert plan.selections == (module.selection,)

    result = dispatch_condition(
        context=context,
        plan=plan,
        condition_id=module.condition.condition_id,
    )
    assert module.calls == ["expand", "freeze", "run"]
    assert result.condition_id == module.condition.condition_id


def test_gate_c_dispatcher_binds_each_selection_to_exact_condition_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _ReorderingFreezeModule()
    monkeypatch.setattr(
        paper_dispatcher,
        "_MODULES",
        (("exp1_real_ai_feasibility", module),),
    )

    plan = paper_dispatcher.plan_paper_experiment(
        context=_context(),
        experiment_id="exp1_real_ai_feasibility",
    )

    assert module.freeze_inputs == [
        (
            module.easy_condition.condition_id,
            module.hard_condition.condition_id,
        )
    ]
    assert plan.bound_condition(module.easy_condition.condition_id) == (
        module.easy_condition,
        module.easy_selection,
    )
    assert plan.bound_condition(module.hard_condition.condition_id) == (
        module.hard_condition,
        module.hard_selection,
    )
    serialized = plan.to_dict()
    assert serialized["condition_selection_binding_count"] == 2
    assert serialized["condition_selection_bindings"] == [
        {
            "schema_version": "tokenshare.paper_condition_selection_binding.v1",
            "condition_id": module.easy_condition.condition_id,
            "condition_digest": module.easy_condition.condition_digest,
            "selection": module.easy_selection.to_dict(),
        },
        {
            "schema_version": "tokenshare.paper_condition_selection_binding.v1",
            "condition_id": module.hard_condition.condition_id,
            "condition_digest": module.hard_condition.condition_digest,
            "selection": module.hard_selection.to_dict(),
        },
    ]


def test_gate_c_dispatch_plan_rejects_inexact_condition_selection_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding_type = getattr(
        paper_dispatcher,
        "FrozenConditionSelectionBinding",
        None,
    )
    assert binding_type is not None
    module = _ReorderingFreezeModule()
    monkeypatch.setattr(
        paper_dispatcher,
        "_MODULES",
        (("exp1_real_ai_feasibility", module),),
    )
    canonical = paper_dispatcher.plan_paper_experiment(
        context=_context(),
        experiment_id="exp1_real_ai_feasibility",
    )
    bindings_by_condition_id = {
        binding.condition_id: binding
        for binding in canonical.condition_selection_bindings
    }
    easy_binding = bindings_by_condition_id[module.easy_condition.condition_id]
    hard_binding = bindings_by_condition_id[module.hard_condition.condition_id]
    plan_type = type(canonical)
    common = {
        "experiment_id": canonical.experiment_id,
        "output_root": canonical.output_root,
    }

    with pytest.raises(ValueError, match="duplicate condition selection binding"):
        plan_type(
            **common,
            conditions=(module.easy_condition,),
            condition_selection_bindings=(easy_binding, easy_binding),
        )
    with pytest.raises(ValueError, match="missing condition selection binding"):
        plan_type(
            **common,
            conditions=(module.easy_condition, module.hard_condition),
            condition_selection_bindings=(easy_binding,),
        )
    with pytest.raises(ValueError, match="extra condition selection binding"):
        plan_type(
            **common,
            conditions=(module.easy_condition,),
            condition_selection_bindings=(easy_binding, hard_binding),
        )
    with pytest.raises(ValueError, match="condition digest mismatch"):
        plan_type(
            **common,
            conditions=(module.easy_condition, module.hard_condition),
            condition_selection_bindings=(
                replace(
                    easy_binding,
                    condition_digest=module.hard_condition.condition_digest,
                ),
                hard_binding,
            ),
        )
    with pytest.raises(ValueError, match="selection does not match bound condition"):
        plan_type(
            **common,
            conditions=(module.easy_condition, module.hard_condition),
            condition_selection_bindings=(
                replace(easy_binding, selection=module.hard_selection),
                hard_binding,
            ),
        )


def test_gate_c_pilot_executes_registered_module_and_replays_without_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = _frozen_formal_catalog_v2()
    readiness = json.loads(
        Path("benchmarks/paper/lean_task14_3x3_readiness.v1.json").read_text(
            encoding="utf-8"
        )
    )
    profile = load_exp1_pilot_profile(
        "benchmarks/paper/exp1_minimal_pilot_profile.v3.json"
    )
    binding = _baseline_binding(profile)
    plan = build_gate_c_dispatch_plans(
        catalog_manifest=catalog,
        lean_3x3_matrix=readiness,
        experiment_ids=("exp2_real_ai_scalability",),
        baseline_endpoint_binding=binding,
        model_endpoint_cohort_preflight=None,
        output_root=tmp_path / "plan",
    )[0]
    frozen = [
        {
            **selection.to_dict(),
            "condition_id": condition.condition_id,
            "condition_digest": condition.condition_digest,
        }
        for condition, selection in zip(
            plan.conditions,
            plan.selections,
            strict=True,
        )
    ]
    budget = plan_paper_suite(
        catalog_manifest=catalog,
        conditions=plan.conditions,
        max_provider_attempts_per_ai_unit=1,
        token_upper_bound_per_provider_attempt=2048,
        cost_upper_bound_per_provider_attempt=0.01,
        plan_only=True,
        lean_3x3_matrix=readiness,
        frozen_selections=frozen,
        endpoint_identity={"baseline": binding},
        request_limits=binding["request_controls"],
    )
    condition, selection = next(
        (condition, selection)
        for condition, selection in zip(
            plan.conditions,
            plan.selections,
            strict=True,
        )
        if condition.domain == "factorization"
        and condition.paper_difficulty == "hard"
        and condition.worker_count == 1
        and condition.repeat_id == 0
    )
    case_id = selection.ordered_case_ids[0]
    entry = profile.source_provider_config.entries[0]
    monkeypatch.setenv(entry.api_key_env, "gate-c-module-capture-key")
    transport = _CapturingFactorizationTransport()
    output_root = tmp_path / "exp2-pilot"

    execute = getattr(paper_runner, "execute_gate_c_pilot_case", None)
    assert callable(execute)
    result = execute(
        catalog_manifest=catalog,
        lean_3x3_matrix=readiness,
        experiment_id=plan.experiment_id,
        baseline_endpoint_binding=binding,
        model_endpoint_cohort_preflight=None,
        budget=budget,
        approved_budget_digest=budget.budget_digest,
        condition_id=condition.condition_id,
        case_id=case_id,
        ai_unit_id="range_0",
        execution_output_root=output_root,
        ai_api_configs={
            profile.model_endpoint_identity.provider_config_id: (
                profile.source_provider_config
            )
        },
        transport=transport,
        real_transport=True,
    )

    assert result.experiment_id == "exp2_real_ai_scalability"
    assert result.condition_id == condition.condition_id
    assert result.provider_calls_made == 0
    assert result.transport_calls_observed == len(transport.calls) > 0
    assert result.paper_eligible is False
    for relative_path in (
        "suite_manifest.json",
        "run_budget.json",
        "input_catalog_manifest.json",
        "conditions.jsonl",
        "run_results.jsonl",
        "per_task_results.jsonl",
        "per_attempt_results.jsonl",
        "fault_injections.jsonl",
        "model_execution_records.jsonl",
        "events/event_log.jsonl",
        "metrics/per_condition_summary.csv",
        "audit/paper_eligibility_report.json",
        "audit/secret_scan_report.json",
        "evidence_manifest.json",
    ):
        assert (output_root / relative_path).is_file(), relative_path

    replay_transport = _RejectingTransport()
    replay = execute(
        catalog_manifest=catalog,
        lean_3x3_matrix=readiness,
        experiment_id=plan.experiment_id,
        baseline_endpoint_binding=binding,
        model_endpoint_cohort_preflight=None,
        budget=budget,
        approved_budget_digest=budget.budget_digest,
        condition_id=condition.condition_id,
        case_id=case_id,
        ai_unit_id="range_0",
        execution_output_root=output_root,
        ai_api_configs={
            profile.model_endpoint_identity.provider_config_id: (
                profile.source_provider_config
            )
        },
        transport=replay_transport,
        real_transport=True,
        replay_only=True,
    )
    assert replay.provider_calls_made == 0
    assert replay.transport_calls_observed == 0
    assert replay.replayed is True
    assert replay_transport.calls == 0

    metrics_before = recompute_gate_c_pilot_metrics(output_root)
    task_path = output_root / "per_task_results.jsonl"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task["root_status"] = (
        "completed" if task["root_status"] != "completed" else "failed"
    )
    task["accepted_validity"] = task["root_status"] == "completed"
    task_path.write_text(
        json.dumps(task, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    metrics_after = write_gate_c_pilot_metrics(output_root)
    assert metrics_before["row"]["root_status"] != metrics_after["row"][
        "root_status"
    ]
    assert metrics_after["row"]["accepted_validity"] == (
        task["root_status"] == "completed"
    )
    csv_text = (output_root / "metrics" / "per_condition_summary.csv").read_text(
        encoding="utf-8"
    )
    assert task["root_status"] in csv_text


def test_gate_c_metrics_accept_provider_error_without_response_artifacts(
    tmp_path: Path,
) -> None:
    output_root = _gate_c_metrics_fixture(
        tmp_path,
        attempt_status="provider_error",
    )

    metrics = recompute_gate_c_pilot_metrics(output_root)

    assert metrics["row"]["attempt_count"] == 1
    assert metrics["row"]["provider_attempt_count"] == 1
    assert metrics["row"]["total_tokens"] == 0


@pytest.mark.parametrize(
    "missing_ref",
    (
        "request_ref",
        "provenance_ref",
        "usage_ref",
        "model_execution_record_ref",
    ),
)
def test_gate_c_metrics_provider_error_still_requires_audit_artifacts(
    tmp_path: Path,
    missing_ref: str,
) -> None:
    output_root = _gate_c_metrics_fixture(
        tmp_path,
        attempt_status="provider_error",
        missing_refs=(missing_ref,),
    )

    with pytest.raises(ValueError, match=f"unindexed {missing_ref}"):
        recompute_gate_c_pilot_metrics(output_root)


@pytest.mark.parametrize("attempt_status", ("succeeded", "verification_rejected"))
def test_gate_c_metrics_response_attempts_still_require_raw_output(
    tmp_path: Path,
    attempt_status: str,
) -> None:
    output_root = _gate_c_metrics_fixture(
        tmp_path,
        attempt_status=attempt_status,
        missing_refs=("raw_output_ref",),
    )

    with pytest.raises(ValueError, match="unindexed raw_output_ref"):
        recompute_gate_c_pilot_metrics(output_root)


@pytest.mark.parametrize(
    ("missing_ref", "expected_error"),
    (
        ("raw_output_ref", "unindexed raw_output_ref"),
        ("parse_failure_ref", "unindexed parse_failure_ref"),
    ),
)
def test_gate_c_metrics_parse_failure_requires_raw_and_failure_artifacts(
    tmp_path: Path,
    missing_ref: str,
    expected_error: str,
) -> None:
    output_root = _gate_c_metrics_fixture(
        tmp_path,
        attempt_status="parse_failed",
        missing_refs=(missing_ref,),
    )

    with pytest.raises(ValueError, match=expected_error):
        recompute_gate_c_pilot_metrics(output_root)


@pytest.mark.parametrize("response_ref_kind", ("parsed", "parse_failure"))
def test_gate_c_metrics_identity_mismatch_preserves_parser_evidence_kind(
    tmp_path: Path,
    response_ref_kind: str,
) -> None:
    output_root = _gate_c_metrics_fixture(
        tmp_path,
        attempt_status="model_identity_mismatch",
        response_ref_kind=response_ref_kind,
    )

    metrics = recompute_gate_c_pilot_metrics(output_root)

    assert metrics["row"]["attempt_count"] == 1
    assert metrics["row"]["provider_attempt_count"] == 1


def test_gate_c_metrics_reject_unindexed_optional_provider_error_artifact(
    tmp_path: Path,
) -> None:
    output_root = _gate_c_metrics_fixture(
        tmp_path,
        attempt_status="provider_error",
        provider_error_raw_output=True,
        unindexed_refs=("raw_output_ref",),
    )

    with pytest.raises(ValueError, match="unindexed raw_output_ref"):
        recompute_gate_c_pilot_metrics(output_root)


@pytest.mark.parametrize(
    "experiment_id",
    tuple(
        experiment_id
        for experiment_id in EXPERIMENT_IDS
        if experiment_id != "exp3_real_ai_fault_recovery"
    ),
)
def test_gate_c_supported_registered_experiments_reach_real_mode_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    experiment_id: str,
) -> None:
    catalog = _frozen_formal_catalog_v2()
    readiness = json.loads(
        Path("benchmarks/paper/lean_task14_3x3_readiness.v1.json").read_text(
            encoding="utf-8"
        )
    )
    profile = load_exp1_pilot_profile(
        "benchmarks/paper/exp1_minimal_pilot_profile.v3.json"
    )
    baseline_binding = _baseline_binding(profile)
    cohort_preflight, cohort_configs = _complete_cohort_preflight()
    endpoint_preflight = (
        cohort_preflight
        if experiment_id == "exp5_real_ai_model_endpoint_comparison"
        else None
    )
    plan = build_gate_c_dispatch_plans(
        catalog_manifest=catalog,
        lean_3x3_matrix=readiness,
        experiment_ids=(experiment_id,),
        baseline_endpoint_binding=baseline_binding,
        model_endpoint_cohort_preflight=endpoint_preflight,
        output_root=tmp_path / "plans",
    )[0]
    condition, selection = _capturing_condition_and_selection(plan)
    factor_cases = {
        str(case["case_id"]): case for case in catalog.factorization_cases
    }
    case_id = next(
        selected_case_id
        for selected_case_id in selection.ordered_case_ids
        if factor_cases[selected_case_id]["difficulty"] == condition.difficulty
    )
    frozen = [
        {
            **selected.to_dict(),
            "condition_id": planned_condition.condition_id,
            "condition_digest": planned_condition.condition_digest,
        }
        for planned_condition, selected in zip(
            plan.conditions,
            plan.selections,
            strict=True,
        )
    ]
    endpoint_identity = (
        {"model_endpoint_cohort_preflight": cohort_preflight}
        if endpoint_preflight is not None
        else {"baseline": baseline_binding}
    )
    budget = plan_paper_suite(
        catalog_manifest=catalog,
        conditions=plan.conditions,
        max_provider_attempts_per_ai_unit=1,
        token_upper_bound_per_provider_attempt=2048,
        cost_upper_bound_per_provider_attempt=0.01,
        plan_only=True,
        lean_3x3_matrix=readiness,
        model_endpoint_cohort_preflight=endpoint_preflight,
        frozen_selections=frozen,
        endpoint_identity=endpoint_identity,
        request_limits=baseline_binding["request_controls"],
    )
    configs = (
        cohort_configs
        if endpoint_preflight is not None
        else {
            profile.model_endpoint_identity.provider_config_id: (
                profile.source_provider_config
            )
        }
    )
    for config in configs.values():
        for entry in config.entries:
            monkeypatch.setenv(entry.api_key_env, f"capture-{entry.entry_id}")
    transport = _CapturingFactorizationTransport()

    result = paper_runner.execute_gate_c_pilot_case(
        catalog_manifest=catalog,
        lean_3x3_matrix=readiness,
        experiment_id=experiment_id,
        baseline_endpoint_binding=baseline_binding,
        model_endpoint_cohort_preflight=endpoint_preflight,
        budget=budget,
        approved_budget_digest=budget.budget_digest,
        condition_id=condition.condition_id,
        case_id=case_id,
        ai_unit_id=None,
        execution_output_root=tmp_path / experiment_id,
        ai_api_configs=configs,
        transport=transport,
        real_transport=True,
    )

    assert result.experiment_id == experiment_id
    assert result.condition_digest == condition.condition_digest
    assert result.selection_digest == selection.selection_digest
    assert result.provider_calls_made == 0
    assert result.transport_calls_observed == len(transport.calls) > 0
    assert result.paper_eligible is False
    request_bodies = transport.calls
    assert all(body["model"] == condition.provider_model_id for body in request_bodies)
    assert all(body["response_format"] == {"type": "json_object"} for body in request_bodies)
    if condition.provider_family == "deepseek":
        assert all(
            body["thinking"] == {"type": "enabled"}
            and body["reasoning_effort"] == "high"
            and "temperature" not in body
            and "top_p" not in body
            for body in request_bodies
        )
    elif condition.provider_family == "siliconflow":
        assert all(body["enable_thinking"] is True for body in request_bodies)
    else:
        assert all(body["reasoning_effort"] == "high" for body in request_bodies)


def test_gate_c_pilot_does_not_fabricate_removed_exp3_zero_rate_baseline(
    tmp_path: Path,
) -> None:
    catalog = _frozen_formal_catalog_v2()
    readiness = json.loads(
        Path("benchmarks/paper/lean_task14_3x3_readiness.v1.json").read_text(
            encoding="utf-8"
        )
    )
    profile = load_exp1_pilot_profile(
        "benchmarks/paper/exp1_minimal_pilot_profile.v3.json"
    )
    plan = build_gate_c_dispatch_plans(
        catalog_manifest=catalog,
        lean_3x3_matrix=readiness,
        experiment_ids=("exp3_real_ai_fault_recovery",),
        baseline_endpoint_binding=_baseline_binding(profile),
        model_endpoint_cohort_preflight=None,
        output_root=tmp_path,
    )[0]

    with pytest.raises(
        ValueError,
        match="formal or smoke runner production callback",
    ):
        paper_runner._validate_gate_c_pilot_condition_scope(plan.conditions[0])


def test_gate_c_exp2_excludes_lean_and_exp5_reuses_exp1_hard_lean_selection(
    tmp_path: Path,
) -> None:
    catalog = _frozen_formal_catalog_v2()
    readiness = json.loads(
        Path("benchmarks/paper/lean_task14_3x3_readiness.v1.json").read_text(
            encoding="utf-8"
        )
    )
    profile = load_exp1_pilot_profile(
        "benchmarks/paper/exp1_minimal_pilot_profile.v3.json"
    )
    cohort_preflight, _configs = _complete_cohort_preflight()
    plans = build_gate_c_dispatch_plans(
        catalog_manifest=catalog,
        lean_3x3_matrix=readiness,
        experiment_ids=(
            "exp1_real_ai_feasibility",
            "exp2_real_ai_scalability",
            "exp5_real_ai_model_endpoint_comparison",
        ),
        baseline_endpoint_binding=_baseline_binding(profile),
        model_endpoint_cohort_preflight=cohort_preflight,
        output_root=tmp_path,
    )

    assert all(
        condition.domain == "factorization"
        for condition in plans[1].conditions
    )
    exp1_hard = {
        condition.topic_family: selection
        for condition, selection in plans[0].bound_items()
        if condition.domain == "lean_proof"
        and condition.paper_difficulty == "hard_frontier"
        and condition.repeat_id == 0
    }
    exp5_hard = {
        condition.topic_family: selection
        for condition, selection in plans[2].bound_items()
        if condition.domain == "lean_proof"
        and condition.paper_difficulty == "hard_frontier"
        and condition.cohort_member_id == "glm_5_2_siliconflow"
        and condition.repeat_id == 0
    }
    assert set(exp1_hard) == set(exp5_hard)
    assert len(exp1_hard) == 3
    for topic_family, exp1_selection in exp1_hard.items():
        exp5_selection = exp5_hard[topic_family]
        assert len(exp1_selection.ordered_case_ids) == 15
        assert exp5_selection.ordered_case_ids == exp1_selection.ordered_case_ids
        assert (
            exp5_selection.case_selection_digest
            == exp1_selection.case_selection_digest
        )


def test_gate_c_registered_exp1_reaches_lean_checker_and_merge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = _frozen_formal_catalog()
    readiness = json.loads(
        Path("benchmarks/paper/lean_task14_3x3_readiness.v1.json").read_text(
            encoding="utf-8"
        )
    )
    profile = load_exp1_pilot_profile(
        "benchmarks/paper/exp1_minimal_pilot_profile.v3.json"
    )
    binding = _baseline_binding(profile)
    plan = build_gate_c_dispatch_plans(
        catalog_manifest=catalog,
        lean_3x3_matrix=readiness,
        experiment_ids=("exp1_real_ai_feasibility",),
        baseline_endpoint_binding=binding,
        model_endpoint_cohort_preflight=None,
        output_root=tmp_path / "plan",
    )[0]
    condition, selection = next(
        (condition, selection)
        for condition, selection in zip(
            plan.conditions,
            plan.selections,
            strict=True,
        )
        if condition.domain == "lean_proof"
        and condition.paper_difficulty == "simple"
        and condition.topic_family == "pure_logic"
        and condition.repeat_id == 0
    )
    budget = _budget_for_plan(
        catalog=catalog,
        readiness=readiness,
        plan=plan,
        endpoint_identity={"baseline": binding},
        request_limits=binding["request_controls"],
    )
    entry = profile.source_provider_config.entries[0]
    monkeypatch.setenv(entry.api_key_env, "gate-c-lean-capture-key")
    transport = _CapturingLeanTransport()
    output_root = tmp_path / "lean-exp1-pilot"

    result = paper_runner.execute_gate_c_pilot_case(
        catalog_manifest=catalog,
        lean_3x3_matrix=readiness,
        experiment_id=plan.experiment_id,
        baseline_endpoint_binding=binding,
        model_endpoint_cohort_preflight=None,
        budget=budget,
        approved_budget_digest=budget.budget_digest,
        condition_id=condition.condition_id,
        case_id=selection.ordered_case_ids[0],
        ai_unit_id=None,
        execution_output_root=output_root,
        ai_api_configs={
            profile.model_endpoint_identity.provider_config_id: (
                profile.source_provider_config
            )
        },
        transport=transport,
        real_transport=True,
    )

    assert result.status == "completed"
    assert result.provider_calls_made == 0
    task = json.loads(
        (output_root / "per_task_results.jsonl").read_text(encoding="utf-8")
    )
    attempts = _read_jsonl(output_root / "per_attempt_results.jsonl")
    assert task["root_status"] == "completed"
    assert task["accepted_validity"] is True
    assert attempts
    assert all(attempt["attempt_status"] == "succeeded" for attempt in attempts)
    assert all(attempt["raw_output_ref"] for attempt in attempts)
    assert all(attempt["parsed_output_ref"] for attempt in attempts)
    assert all(attempt["model_execution_record_ref"] for attempt in attempts)


def test_formal_exp1_reuses_planning_catalog_view_when_crossing_to_lean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Factorization 后的第一条 Lean condition 必须复用冻结 planning view。"""

    catalog = _frozen_formal_catalog()
    readiness = json.loads(
        Path("benchmarks/paper/lean_task14_3x3_readiness.v1.json").read_text(
            encoding="utf-8"
        )
    )
    profile = load_exp1_pilot_profile(
        "benchmarks/paper/exp1_minimal_pilot_profile.v3.json"
    )
    binding = _baseline_binding(profile)
    full_plan = build_gate_c_dispatch_plans(
        catalog_manifest=catalog,
        lean_3x3_matrix=readiness,
        experiment_ids=("exp1_real_ai_feasibility",),
        baseline_endpoint_binding=binding,
        model_endpoint_cohort_preflight=None,
        output_root=tmp_path,
    )[0]
    factor_item = next(
        item for item in full_plan.bound_items() if item[0].domain == "factorization"
    )
    lean_item = next(
        item for item in full_plan.bound_items() if item[0].domain == "lean_proof"
    )
    selected_ids = {factor_item[0].condition_id, lean_item[0].condition_id}
    plan = replace(
        full_plan,
        conditions=(factor_item[0], lean_item[0]),
        condition_selection_bindings=tuple(
            binding_item
            for binding_item in full_plan.condition_selection_bindings
            if binding_item.condition_id in selected_ids
        ),
    )
    budget = _budget_for_plan(
        catalog=catalog,
        readiness=readiness,
        plan=plan,
        endpoint_identity={"baseline": binding},
        request_limits=binding["request_controls"],
    )
    callback_domains: list[str] = []
    execution_selections: dict[str, dict] = {}

    class CapturingFormalCallback:
        def __init__(self, **_kwargs) -> None:
            pass

        def __call__(self, *, context, condition, selection):
            callback_domains.append(condition.domain)
            execution_selections[condition.condition_id] = selection.to_dict()
            return PaperConditionResult(
                condition_id=condition.condition_id,
                status=PaperStatus.COMPLETED,
                repeat_count=1,
                task_count=len(selection.ordered_case_ids),
                completed_root_count=len(selection.ordered_case_ids),
                failed_root_count=0,
                blocked_root_count=0,
                provider_attempt_count=0,
                metrics_ref=None,
            )

    monkeypatch.setattr(
        paper_formal_runner,
        "_FormalConditionExecutionCallback",
        CapturingFormalCallback,
    )
    transport = _RejectingTransport()

    execution_kwargs = {
        "dispatch_plans": (plan,),
        "catalog_manifest": catalog,
        "budget": budget,
        "budget_approval": {
            "approval_mode": "user_bypassed",
            "budget_digest": budget.budget_digest,
        },
        "output_root": tmp_path,
        "ai_api_configs": {
            profile.model_endpoint_identity.provider_config_id: (
                profile.source_provider_config
            )
        },
        "transport": transport,
        "real_transport": False,
        "hard_limits": {},
    }
    executed = paper_formal_runner.execute_paper_formal_suite(
        **execution_kwargs,
    )

    assert callback_domains == ["factorization", "lean_proof"]
    assert callback_domains.count("lean_proof") == 1
    assert execution_selections[lean_item[0].condition_id] == lean_item[1].to_dict()
    assert transport.calls == 0

    replayed = paper_formal_runner.execute_paper_formal_suite(
        **{
            **execution_kwargs,
            "dispatch_plans": (plan,),
            "ai_api_configs": {},
            "transport": transport,
            "replay_only": True,
        }
    )
    assert replayed.to_dict() == executed.to_dict()
    assert transport.calls == 0

    drifted_view = deepcopy(plan.catalog_execution_view)
    assert drifted_view is not None
    drifted_view["view_body"]["optional_worker_preflight"] = {"drift": True}
    drifted_view["view_digest"] = digest_json(
        {
            "schema_version": drifted_view["schema_version"],
            "view_kind": drifted_view["view_kind"],
            "catalog_manifest_digest": drifted_view[
                "catalog_manifest_digest"
            ],
            "view_body": drifted_view["view_body"],
        }
    )
    drifted_plan = replace(plan, catalog_execution_view=drifted_view)
    with pytest.raises(ValueError, match="identity"):
        paper_formal_runner.execute_paper_formal_suite(
            **{
                **execution_kwargs,
                "dispatch_plans": (drifted_plan,),
                "resume": True,
            }
        )
    assert transport.calls == 0


def test_gate_c_exp5_openai_member_preserves_high_reasoning_controls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = _frozen_formal_catalog_v2()
    readiness = json.loads(
        Path("benchmarks/paper/lean_task14_3x3_readiness.v1.json").read_text(
            encoding="utf-8"
        )
    )
    profile = load_exp1_pilot_profile(
        "benchmarks/paper/exp1_minimal_pilot_profile.v3.json"
    )
    binding = _baseline_binding(profile)
    cohort_preflight, configs = _complete_cohort_preflight()
    plan = build_gate_c_dispatch_plans(
        catalog_manifest=catalog,
        lean_3x3_matrix=readiness,
        experiment_ids=("exp5_real_ai_model_endpoint_comparison",),
        baseline_endpoint_binding=binding,
        model_endpoint_cohort_preflight=cohort_preflight,
        output_root=tmp_path / "plan",
    )[0]
    condition, selection = next(
        (condition, selection)
        for condition, selection in zip(
            plan.conditions,
            plan.selections,
            strict=True,
        )
        if condition.domain == "factorization"
        and condition.paper_difficulty == "hard"
        and condition.repeat_id == 0
        and condition.cohort_member_id == "gpt_5_6_sol_high_openai"
    )
    budget = _budget_for_plan(
        catalog=catalog,
        readiness=readiness,
        plan=plan,
        endpoint_identity={
            "model_endpoint_cohort_preflight": cohort_preflight
        },
        request_limits=binding["request_controls"],
    )
    for config in configs.values():
        for entry in config.entries:
            monkeypatch.setenv(entry.api_key_env, f"capture-{entry.entry_id}")
    transport = _CapturingFactorizationTransport()

    result = paper_runner.execute_gate_c_pilot_case(
        catalog_manifest=catalog,
        lean_3x3_matrix=readiness,
        experiment_id=plan.experiment_id,
        baseline_endpoint_binding=binding,
        model_endpoint_cohort_preflight=cohort_preflight,
        budget=budget,
        approved_budget_digest=budget.budget_digest,
        condition_id=condition.condition_id,
        case_id=selection.ordered_case_ids[0],
        ai_unit_id=None,
        execution_output_root=tmp_path / "exp5-gpt-pilot",
        ai_api_configs=configs,
        transport=transport,
        real_transport=True,
    )

    assert result.provider_calls_made == 0
    assert transport.calls
    assert all(body["model"] == "gpt-5.6-sol" for body in transport.calls)
    assert all(body["reasoning_effort"] == "high" for body in transport.calls)
    assert all(body["response_format"] == {"type": "json_object"} for body in transport.calls)
    assert all(body["temperature"] == 0 for body in transport.calls)
    attempts = _read_jsonl(tmp_path / "exp5-gpt-pilot" / "per_attempt_results.jsonl")
    assert all(attempt["model_execution_record_ref"] for attempt in attempts)


def test_gate_c_budget_selection_and_identity_drift_stop_before_transport(
    tmp_path: Path,
) -> None:
    catalog = _frozen_formal_catalog_v2()
    readiness = json.loads(
        Path("benchmarks/paper/lean_task14_3x3_readiness.v1.json").read_text(
            encoding="utf-8"
        )
    )
    profile = load_exp1_pilot_profile(
        "benchmarks/paper/exp1_minimal_pilot_profile.v3.json"
    )
    binding = _baseline_binding(profile)
    plan = build_gate_c_dispatch_plans(
        catalog_manifest=catalog,
        lean_3x3_matrix=readiness,
        experiment_ids=("exp2_real_ai_scalability",),
        baseline_endpoint_binding=binding,
        model_endpoint_cohort_preflight=None,
        output_root=tmp_path / "plan",
    )[0]
    condition, selection = _capturing_condition_and_selection(plan)
    case_id = selection.ordered_case_ids[0]
    budget = _budget_for_plan(
        catalog=catalog,
        readiness=readiness,
        plan=plan,
        endpoint_identity={"baseline": binding},
        request_limits=binding["request_controls"],
    )
    configs = {
        profile.model_endpoint_identity.provider_config_id: (
            profile.source_provider_config
        )
    }

    wrong_digest_transport = _RejectingTransport()
    with pytest.raises(ValueError, match="budget digest mismatch"):
        paper_runner.execute_gate_c_pilot_case(
            catalog_manifest=catalog,
            lean_3x3_matrix=readiness,
            experiment_id=plan.experiment_id,
            baseline_endpoint_binding=binding,
            model_endpoint_cohort_preflight=None,
            budget=budget,
            approved_budget_digest="sha256:" + "0" * 64,
            condition_id=condition.condition_id,
            case_id=case_id,
            ai_unit_id="range_0",
            execution_output_root=tmp_path / "wrong-digest",
            ai_api_configs=configs,
            transport=wrong_digest_transport,
            real_transport=True,
        )
    assert wrong_digest_transport.calls == 0

    drifted_quota = deepcopy(budget.quota_preflight)
    matching = next(
        item
        for item in drifted_quota["budget_commitments"]["frozen_selections"]
        if item["condition_id"] == condition.condition_id
    )
    matching["ordered_case_ids"] = list(reversed(matching["ordered_case_ids"]))
    drifted_budget = replace(budget, quota_preflight=drifted_quota)
    selection_transport = _RejectingTransport()
    with pytest.raises(ValueError, match="frozen selection drift"):
        paper_runner.execute_gate_c_pilot_case(
            catalog_manifest=catalog,
            lean_3x3_matrix=readiness,
            experiment_id=plan.experiment_id,
            baseline_endpoint_binding=binding,
            model_endpoint_cohort_preflight=None,
            budget=drifted_budget,
            approved_budget_digest=drifted_budget.budget_digest,
            condition_id=condition.condition_id,
            case_id=case_id,
            ai_unit_id="range_0",
            execution_output_root=tmp_path / "selection-drift",
            ai_api_configs=configs,
            transport=selection_transport,
            real_transport=True,
        )
    assert selection_transport.calls == 0

    drifted_config_body = profile.source_provider_config.to_safe_dict()
    drifted_config_body["entries"][0]["model"] = "wrong/model"
    identity_transport = _RejectingTransport()
    with pytest.raises(ValueError, match="source provider config digest drift"):
        paper_runner.execute_gate_c_pilot_case(
            catalog_manifest=catalog,
            lean_3x3_matrix=readiness,
            experiment_id=plan.experiment_id,
            baseline_endpoint_binding=binding,
            model_endpoint_cohort_preflight=None,
            budget=budget,
            approved_budget_digest=budget.budget_digest,
            condition_id=condition.condition_id,
            case_id=case_id,
            ai_unit_id="range_0",
            execution_output_root=tmp_path / "identity-drift",
            ai_api_configs={
                profile.model_endpoint_identity.provider_config_id: (
                    load_ai_api_config(drifted_config_body)
                )
            },
            transport=identity_transport,
            real_transport=True,
        )
    assert identity_transport.calls == 0


@pytest.mark.parametrize(
    ("domain", "adapter_name"),
    (
        ("factorization", "run_factorization_paper_case"),
        ("lean_proof", "run_lean_paper_case"),
    ),
)
def test_gate_c_case_dispatcher_routes_protocol_request_through_coordinator(
    monkeypatch,
    domain: str,
    adapter_name: str,
) -> None:
    dispatch_case = getattr(paper_dispatcher, "dispatch_paper_case", None)
    calls: list[dict] = []
    request = ProtocolRunRequest(
        run_id="run_1",
        root_input={"case_id": "case_1"},
        plugin_runtime=object(),
        worker_backend=object(),
    )
    runtime_result = ProtocolRunResult(run_id="run_1", status="completed")

    class CoordinatorSpy:
        def __init__(self) -> None:
            self.requests = []

        def run_root(self, received):
            self.requests.append(received)
            return runtime_result

    coordinator = CoordinatorSpy()

    def adapter(**kwargs):
        calls.append(kwargs)
        return kwargs["protocol_run_dispatcher"](
            coordinator=coordinator,
            request=request,
        )

    monkeypatch.setattr(
        paper_dispatcher,
        adapter_name,
        adapter,
        raising=False,
    )
    condition = _condition_for_domain(domain)

    assert callable(dispatch_case)
    result = dispatch_case(
        case={"case_id": "case_1"},
        condition=condition,
        output_root="outputs/gate-c/case-1",
        transport="capturing-transport",
        real_transport=False,
        ai_api_config="config",
        entry_id="entry_1",
        max_tokens=123,
        timeout_seconds=17,
    )

    assert result is runtime_result
    assert coordinator.requests == [request]
    assert calls == [
        {
            "case": {"case_id": "case_1"},
            "condition": condition,
            "output_root": "outputs/gate-c/case-1",
            "transport": "capturing-transport",
            "real_transport": False,
            "ai_api_config": "config",
            "entry_id": "entry_1",
            "max_tokens": 123,
            "timeout_seconds": 17,
            "selected_ai_unit_id": None,
            "post_raw_output_hook": None,
            "ablation_mode": None,
            "worker_termination_policy": None,
            "protocol_run_dispatcher": paper_dispatcher.execute_protocol_request,
        }
    ]


def test_dispatch_paper_case_routes_selected_unit_to_system_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    condition = _condition_for_domain("factorization")
    calls: list[dict] = []

    def capture_adapter(**kwargs):
        calls.append(kwargs)
        return "selected-runtime-result"

    monkeypatch.setattr(
        paper_dispatcher,
        "run_factorization_paper_case",
        capture_adapter,
    )
    result = paper_dispatcher.dispatch_paper_case(
        case={"case_id": "case_1"},
        condition=condition,
        output_root="outputs/gate-c/case-1",
        transport="capturing-transport",
        real_transport=False,
        ai_api_config="config",
        entry_id="entry_1",
        max_tokens=123,
        timeout_seconds=17,
        selected_ai_unit_id="unit_1",
    )

    assert result == "selected-runtime-result"
    assert calls[0]["selected_ai_unit_id"] == "unit_1"
    assert (
        calls[0]["protocol_run_dispatcher"]
        is paper_dispatcher.execute_protocol_request
    )


class _RecordingModule:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.condition = PaperExperimentCondition(
            experiment_id="exp1_real_ai_feasibility",
            condition_id="gate_c_condition",
            domain="factorization",
            difficulty="easy",
            paper_difficulty="easy",
            topic_family=None,
            worker_count=1,
            fault_type="none",
            fault_rate=0.0,
            ablation_mode="FULL",
            model_policy="fixed_entry",
            repeat_id=0,
            seed=1,
            catalog_digest="sha256:" + "1" * 64,
        )
        self.selection = FrozenCaseSelection(
            selection_id="gate_c_selection",
            experiment_id="exp1_real_ai_feasibility",
            suite_version="paper_v1",
            catalog_version="v1",
            domain="factorization",
            paper_difficulty="easy",
            topic_family=None,
            ordered_case_ids=("factor_easy_01",),
            catalog_digest="sha256:" + "1" * 64,
            expected_ai_unit_count=1,
            paper_eligible_required=True,
        )

    def expand_conditions(self, context):
        self.calls.append("expand")
        return (self.condition,)

    def freeze_case_selections(self, context, conditions):
        self.calls.append("freeze")
        assert conditions == (self.condition,)
        return paper_dispatcher.FrozenCaseSelectionBatch(
            (
                paper_dispatcher.FrozenConditionSelectionBinding(
                    condition_id=self.condition.condition_id,
                    condition_digest=self.condition.condition_digest,
                    selection=self.selection,
                ),
            )
        )

    def run_condition(self, context, condition, selection):
        self.calls.append("run")
        assert condition == self.condition
        assert selection == self.selection
        return PaperConditionResult(
            condition_id=condition.condition_id,
            status=PaperStatus.COMPLETED,
            repeat_count=1,
            task_count=1,
            completed_root_count=1,
            failed_root_count=0,
            blocked_root_count=0,
            provider_attempt_count=1,
            metrics_ref={"transport_kind": "capturing"},
        )

    def summarize(self, evidence):
        return ExperimentSummaryRows(
            experiment_id="exp1_real_ai_feasibility",
            rows=(),
        )


class _ReorderingFreezeModule:
    def __init__(self) -> None:
        baseline = _RecordingModule()
        self.easy_condition = baseline.condition
        self.hard_condition = replace(
            baseline.condition,
            condition_id="gate_c_hard_condition",
            difficulty="hard",
            paper_difficulty="hard",
            seed=2,
        )
        self.easy_selection = baseline.selection
        self.hard_selection = replace(
            baseline.selection,
            selection_id="gate_c_hard_selection",
            paper_difficulty="hard",
            ordered_case_ids=("factor_hard_01",),
        )
        self.freeze_inputs: list[tuple[str, ...]] = []
        self._selection_by_condition_id = {
            self.easy_condition.condition_id: self.easy_selection,
            self.hard_condition.condition_id: self.hard_selection,
        }

    def expand_conditions(self, context):
        return (self.easy_condition, self.hard_condition)

    def freeze_case_selections(self, context, conditions):
        self.freeze_inputs.append(
            tuple(condition.condition_id for condition in conditions)
        )
        binding_type = getattr(
            paper_dispatcher,
            "FrozenConditionSelectionBinding",
        )
        batch_type = getattr(paper_dispatcher, "FrozenCaseSelectionBatch")
        bindings = tuple(
            binding_type(
                condition_id=condition.condition_id,
                condition_digest=condition.condition_digest,
                selection=self._selection_by_condition_id[condition.condition_id],
            )
            for condition in conditions
        )
        return batch_type(tuple(reversed(bindings)))

    def run_condition(self, context, condition, selection):
        expected_selection = self._selection_by_condition_id[condition.condition_id]
        assert selection == expected_selection
        return PaperConditionResult(
            condition_id=condition.condition_id,
            status=PaperStatus.COMPLETED,
            repeat_count=1,
            task_count=1,
            completed_root_count=1,
            failed_root_count=0,
            blocked_root_count=0,
            provider_attempt_count=1,
            metrics_ref={"transport_kind": "capturing"},
        )

    def summarize(self, evidence):
        return ExperimentSummaryRows(
            experiment_id="exp1_real_ai_feasibility",
            rows=(),
        )


class _CapturingFactorizationTransport:
    tokenshare_offline_capturing_transport = True

    def __init__(self) -> None:
        self.delegate = ScriptedFactorizationRangeTransport()
        self.calls: list[dict] = []

    def post_chat_completion(self, *, entry, api_key, body, timeout_seconds):
        response = self.delegate.post_chat_completion(
            entry=entry,
            api_key=api_key,
            body=body,
            timeout_seconds=timeout_seconds,
        )
        response.body["model"] = entry.model
        self.calls.append(json.loads(json.dumps(body)))
        return response


class _CapturingLeanTransport:
    tokenshare_offline_capturing_transport = True

    def __init__(self) -> None:
        self.delegate = ScriptedLeanPaperProofTransport()
        self.calls: list[dict] = []

    def post_chat_completion(self, *, entry, api_key, body, timeout_seconds):
        response = self.delegate.post_chat_completion(
            entry=entry,
            api_key=api_key,
            body=body,
            timeout_seconds=timeout_seconds,
        )
        response.body["model"] = entry.model
        self.calls.append(json.loads(json.dumps(body)))
        return response


class _RejectingTransport:
    tokenshare_offline_capturing_transport = True

    def __init__(self) -> None:
        self.calls = 0

    def post_chat_completion(self, **_kwargs):
        self.calls += 1
        raise AssertionError("replay must not call transport")


def _context() -> PaperExecutionContext:
    return PaperExecutionContext(
        context_id="gate_c_dispatcher_test",
        catalog={"catalog_digest": "sha256:" + "1" * 64},
        approved_endpoint_binding={"status": "approved"},
        request_limits={"max_provider_attempts": 1},
        hard_limits={"max_total_provider_attempts": 1},
        output_root="outputs/gate-c/exp1",
        artifact_store=object(),
        event_store=object(),
        execution_callback=lambda **_kwargs: None,
    )


def _baseline_binding(profile) -> dict:
    identity = profile.model_endpoint_identity.to_dict()
    return {
        **identity,
        "model_entry_id": identity["selected_entry_id"],
        "request_controls": {
            "max_tokens": 300000,
            "timeout_seconds": 600,
            "max_provider_attempts": 1,
            "stream": False,
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
        },
    }


def _budget_for_plan(
    *,
    catalog,
    readiness: dict,
    plan,
    endpoint_identity: dict,
    request_limits: dict,
):
    frozen = [
        {
            **selection.to_dict(),
            "condition_id": condition.condition_id,
            "condition_digest": condition.condition_digest,
        }
        for condition, selection in zip(
            plan.conditions,
            plan.selections,
            strict=True,
        )
    ]
    return plan_paper_suite(
        catalog_manifest=catalog,
        conditions=plan.conditions,
        max_provider_attempts_per_ai_unit=1,
        token_upper_bound_per_provider_attempt=2048,
        cost_upper_bound_per_provider_attempt=0.01,
        plan_only=True,
        lean_3x3_matrix=readiness,
        frozen_selections=frozen,
        endpoint_identity=endpoint_identity,
        request_limits=request_limits,
    )


def _capturing_condition_and_selection(plan):
    target_difficulty = {
        "exp2_real_ai_scalability": "hard",
        "exp5_real_ai_model_endpoint_comparison": "hard",
    }.get(plan.experiment_id, "easy")
    candidates = [
        (condition, selection)
        for condition, selection in zip(
            plan.conditions,
            plan.selections,
            strict=True,
        )
        if condition.domain == "factorization"
        and condition.paper_difficulty == target_difficulty
        and condition.repeat_id == 0
        and selection.is_executable
        and (
            condition.experiment_id != "exp2_real_ai_scalability"
            or condition.worker_count == 1
        )
        and (
            condition.experiment_id != "exp4_real_ai_protocol_ablation"
            or condition.ablation_mode == "FULL"
        )
        and (
            condition.experiment_id
            != "exp5_real_ai_model_endpoint_comparison"
            or condition.cohort_member_id == "glm_5_2_siliconflow"
        )
    ]
    assert candidates
    return candidates[0]


def _shared_lean_selection(plan, *, paper_difficulty: str, condition_filter):
    matches = [
        selection
        for condition, selection in zip(
            plan.conditions,
            plan.selections,
            strict=True,
        )
        if condition.domain == "lean_proof"
        and condition.paper_difficulty == paper_difficulty
        and condition_filter(condition)
    ]
    assert len(matches) == 1
    return matches[0]


def _complete_cohort_preflight():
    cohort_digest = "sha256:" + "9" * 64
    siliconflow = load_ai_api_config(
        {
            "schema_version": "phase7.ai_api_executor_config.v1",
            "executor_id": "gate_c_exp5_siliconflow",
            "provider_family": "siliconflow",
            "selection_policy": {
                "kind": "uniform_random_without_weights",
                "seed_source": "request_or_environment_seed",
            },
            "defaults": {
                "timeout_seconds": 100,
                "max_tokens": 8192,
                "stream": False,
                "max_provider_attempts": 1,
            },
            "entries": [
                _config_entry(
                    entry_id="glm-exp5-capture",
                    key_env="TOKENSHARE_GATE_C_GLM_KEY",
                    model="zai-org/GLM-5.2",
                    request_overrides={"enable_thinking": True},
                ),
            ],
            "local_concurrency": {"max_in_flight_global": 1},
            "metadata": {"test_only": True},
        }
    )
    deepseek = load_ai_api_config(
        {
            "schema_version": "phase7.ai_api_executor_config.v1",
            "executor_id": "gate_c_exp5_deepseek",
            "provider_family": "deepseek",
            "selection_policy": {
                "kind": "uniform_random_without_weights",
                "seed_source": "request_or_environment_seed",
            },
            "defaults": {
                "timeout_seconds": 100,
                "max_tokens": 8192,
                "stream": False,
                "max_provider_attempts": 1,
            },
            "entries": [
                _config_entry(
                    entry_id="deepseek-exp5-capture",
                    key_env="TOKENSHARE_GATE_C_DEEPSEEK_KEY",
                    model="deepseek-v4-pro",
                    request_overrides={
                        "thinking": {"type": "enabled"},
                        "reasoning_effort": "high",
                    },
                )
            ],
            "local_concurrency": {"max_in_flight_global": 1},
            "metadata": {"test_only": True},
        }
    )
    openai = load_ai_api_config(
        {
            "schema_version": "phase7.ai_api_executor_config.v1",
            "executor_id": "gate_c_exp5_openai",
            "provider_family": "openai",
            "selection_policy": {
                "kind": "uniform_random_without_weights",
                "seed_source": "request_or_environment_seed",
            },
            "defaults": {
                "timeout_seconds": 100,
                "max_tokens": 8192,
                "stream": False,
                "max_provider_attempts": 1,
            },
            "entries": [
                _config_entry(
                    entry_id="gpt-exp5-capture",
                    key_env="TOKENSHARE_GATE_C_GPT_KEY",
                    model="gpt-5.6-sol",
                    request_overrides={"reasoning_effort": "high"},
                )
            ],
            "local_concurrency": {"max_in_flight_global": 1},
            "metadata": {"test_only": True},
        }
    )
    configs = {
        "siliconflow": siliconflow,
        "deepseek": deepseek,
        "openai": openai,
    }
    entry_ids = {
        "glm_5_2_siliconflow": "glm-exp5-capture",
        "deepseek_v4_pro_deepseek": "deepseek-exp5-capture",
        "gpt_5_6_sol_high_openai": "gpt-exp5-capture",
    }
    member_plans = {}
    request_controls_snapshot = {
        field_name: {
            **siliconflow.defaults,
            **siliconflow.entries[0].request_overrides,
        }[field_name]
        for field_name in EXP5_COMPARABLE_REQUEST_CONTROL_FIELDS
    }
    request_controls_snapshot["domain_contracts"] = {
        domain: dict(contract)
        for domain, contract in EXP5_DOMAIN_EXECUTION_CONTRACTS.items()
    }
    for member_id in PAPER_MODEL_ENDPOINT_COHORT_MEMBER_IDS:
        expected = PAPER_MODEL_ENDPOINT_COHORT_MEMBERS[member_id]
        provider = str(expected["provider_family"])
        effective_controls = {
            "siliconflow": {"enable_thinking": True},
            "deepseek": {
                "thinking": {"type": "enabled"},
                "reasoning_effort": "high",
            },
            "openai": {"reasoning_effort": "high"},
        }[provider]
        identity = PaperModelEndpointIdentity(
            model_cohort_id=PAPER_MODEL_ENDPOINT_COHORT_ID,
            model_cohort_digest=cohort_digest,
            cohort_member_id=member_id,
            provider_config_id=provider,
            selected_entry_id=entry_ids[member_id],
            provider_family=provider,
            provider_model_id=str(expected["provider_model_id"]),
            reasoning_profile_id=str(expected["reasoning_profile_id"]),
            effective_reasoning_controls=effective_controls,
            source_provider_config_digest=configs[provider].config_digest,
        )
        member_plans[member_id] = {
            "schema_version": "tokenshare.paper_model_endpoint_member_plan.v1",
            "status": "planned",
            "blocked_reasons": [],
            "cohort_id": PAPER_MODEL_ENDPOINT_COHORT_ID,
            "model_cohort_digest": cohort_digest,
            "cohort_member_id": member_id,
            "provider_config_id": provider,
            "selected_entry_id": entry_ids[member_id],
            "provider_family": provider,
            "provider_model_id": expected["provider_model_id"],
            "reasoning_profile_id": expected["reasoning_profile_id"],
            "source_provider_config_digest": configs[provider].config_digest,
            "model_endpoint_identity_digest": (
                identity.model_endpoint_identity_digest
            ),
            "endpoint_identity": identity.to_dict(),
            "request_controls": {
                "schema_version": "tokenshare.paper_exp5_request_controls.v1",
                "comparable": dict(request_controls_snapshot),
                "comparable_digest": digest_json(
                    request_controls_snapshot
                ),
                "provider_specific_reasoning": effective_controls,
                "provider_specific_reasoning_digest": digest_json(
                    effective_controls
                ),
            },
        }
    return (
        {
            "schema_version": (
                "tokenshare.paper_model_endpoint_cohort_preflight.v1"
            ),
            "status": "planned",
            "paper_eligible_possible": True,
            "blocked_reason": None,
            "ineligibility_reasons": [],
            "provider_calls_made": 0,
            "model_policy": "fixed_entry",
            "cohort_id": PAPER_MODEL_ENDPOINT_COHORT_ID,
            "model_cohort_digest": cohort_digest,
            "expected_member_ids": list(PAPER_MODEL_ENDPOINT_COHORT_MEMBER_IDS),
            "member_plans": member_plans,
            "request_controls_snapshot": request_controls_snapshot,
            "request_controls_snapshot_digest": digest_json(
                request_controls_snapshot
            ),
        },
        configs,
    )


def _config_entry(
    *,
    entry_id: str,
    key_env: str,
    model: str,
    request_overrides: dict,
) -> dict:
    return {
        "entry_id": entry_id,
        "enabled": True,
        "base_url": "https://example.invalid/v1",
        "api_key_env": key_env,
        "model": model,
        "endpoint": "/chat/completions",
        "supports_json_mode": True,
        "supports_streaming": False,
        "request_overrides": request_overrides,
        "pricing": {
            "currency": "USD",
            "input_per_million_tokens": 1.0,
            "output_per_million_tokens": 2.0,
        },
        "tags": ["gate_c_test"],
    }


def _condition_for_domain(domain: str) -> PaperExperimentCondition:
    return PaperExperimentCondition(
        experiment_id="exp1_real_ai_feasibility",
        condition_id=f"gate_c_{domain}",
        domain=domain,
        difficulty="easy",
        paper_difficulty="easy" if domain == "factorization" else "simple",
        topic_family=None if domain == "factorization" else "pure_logic",
        worker_count=1,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=1,
        catalog_digest="sha256:" + "1" * 64,
    )


def _frozen_formal_catalog() -> PaperInputCatalogManifest:
    factorization_cases = tuple(
        {**case, "paper_difficulty": case["difficulty"]}
        for case in _read_jsonl("benchmarks/paper/factorization_catalog.v1.jsonl")
    )
    lean_cases = tuple(
        {
            **case,
            "paper_difficulty": "simple",
            "topic_family": "pure_logic",
            "topic_family_version": "shallow_v1",
        }
        for case in _read_jsonl("benchmarks/paper/lean_catalog.v1.jsonl")
    )
    lean_lemma_graph_cases = tuple(
        _read_jsonl("benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl")
    )
    digest_body = {
        "catalog_id": "tokenshare.paper.catalog",
        "catalog_version": "v1",
        "factorization_cases": factorization_cases,
        "lean_cases": lean_cases,
        "lean_lemma_graph_cases": lean_lemma_graph_cases,
    }
    return PaperInputCatalogManifest(
        catalog_id="tokenshare.paper.catalog",
        catalog_version="v1",
        catalog_digest=digest_json(digest_body),
        generator_version="gate_c_frozen_test",
        case_count=(
            len(factorization_cases) + len(lean_cases) + len(lean_lemma_graph_cases)
        ),
        domain_counts={},
        difficulty_counts={},
        paper_difficulty_counts={},
        topic_family_counts={},
        paper_difficulty_topic_family_counts={},
        oracle_validation_status="passed",
        lean_preflight_status="passed",
        lean_preflight_summary={},
        lean_lemma_graph_preflight_summary={},
        created_at="2026-07-19T00:00:00Z",
        source_files=[],
        factorization_cases=factorization_cases,
        lean_cases=lean_cases,
        lean_lemma_graph_cases=lean_lemma_graph_cases,
    )


def _frozen_formal_catalog_v2() -> PaperInputCatalogManifest:
    factorization_cases = tuple(
        _read_jsonl("benchmarks/paper/factorization_catalog.v2.jsonl")
    )
    lean_cases = tuple(
        {
            **case,
            "paper_difficulty": "simple",
            "topic_family": "pure_logic",
            "topic_family_version": "shallow_v1",
        }
        for case in _read_jsonl("benchmarks/paper/lean_catalog.v1.jsonl")
    )
    lean_lemma_graph_cases = tuple(
        _read_jsonl("benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl")
    )
    digest_body = {
        "catalog_id": "tokenshare.paper.catalog",
        "catalog_version": "v2",
        "factorization_cases": factorization_cases,
        "lean_cases": lean_cases,
        "lean_lemma_graph_cases": lean_lemma_graph_cases,
    }
    return PaperInputCatalogManifest(
        catalog_id="tokenshare.paper.catalog",
        catalog_version="v2",
        catalog_digest=digest_json(digest_body),
        generator_version=FACTORIZATION_V2_GENERATOR_VERSION,
        case_count=(
            len(factorization_cases) + len(lean_cases) + len(lean_lemma_graph_cases)
        ),
        domain_counts={"factorization": 500, "lean_proof": 195},
        difficulty_counts={
            "factorization": {"easy": 167, "medium": 167, "hard": 166}
        },
        paper_difficulty_counts={},
        topic_family_counts={},
        paper_difficulty_topic_family_counts={},
        oracle_validation_status="passed",
        lean_preflight_status="passed",
        lean_preflight_summary={},
        lean_lemma_graph_preflight_summary={},
        created_at="2026-07-20T00:00:00Z",
        source_files=["benchmarks/paper/factorization_catalog.v2.jsonl"],
        factorization_cases=factorization_cases,
        lean_cases=lean_cases,
        lean_lemma_graph_cases=lean_lemma_graph_cases,
    )


def _read_jsonl(path: str) -> list[dict]:
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _gate_c_metrics_fixture(
    tmp_path: Path,
    *,
    attempt_status: str,
    missing_refs: tuple[str, ...] = (),
    unindexed_refs: tuple[str, ...] = (),
    provider_error_raw_output: bool = False,
    response_ref_kind: str | None = None,
) -> Path:
    output_root = tmp_path / f"gate-c-metrics-{attempt_status}"
    plan = {
        "schema_version": "tokenshare.paper_gate_c_execution_plan.v1",
        "suite_id": "gate_c_metrics_fixture",
        "condition_id": "gate_c_condition",
        "selected_case_id": "factor_v2_easy_001",
    }
    plan["execution_plan_digest"] = digest_json(plan)
    refs = {
        field_name: {"artifact_id": f"fixture_{field_name}"}
        for field_name in (
            "request_ref",
            "raw_output_ref",
            "parsed_output_ref",
            "parse_failure_ref",
            "provenance_ref",
            "usage_ref",
            "model_execution_record_ref",
        )
    }
    has_raw_output = attempt_status != "provider_error" or provider_error_raw_output
    if response_ref_kind is None:
        response_ref_kind = (
            "parse_failure" if attempt_status == "parse_failed" else "parsed"
        )
    attempt = {
        "schema_version": "tokenshare.paper_attempt_result.v1",
        "condition_id": "gate_c_condition",
        "repeat_id": 0,
        "run_id": "gate_c_run",
        "task_id": "gate_c_task",
        "unit_id": "range_0",
        "attempt_id": "gate_c_attempt_0",
        "attempt_status": attempt_status,
        "request_ref": refs["request_ref"],
        "raw_output_ref": refs["raw_output_ref"] if has_raw_output else None,
        "parsed_output_ref": (
            refs["parsed_output_ref"]
            if attempt_status != "provider_error" and response_ref_kind == "parsed"
            else None
        ),
        "parse_failure_ref": (
            refs["parse_failure_ref"]
            if attempt_status != "provider_error"
            and response_ref_kind == "parse_failure"
            else None
        ),
        "provenance_ref": refs["provenance_ref"],
        "usage_ref": refs["usage_ref"],
        "model_execution_record_ref": refs["model_execution_record_ref"],
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost_estimate": 0.0,
    }
    for field_name in missing_refs:
        attempt[field_name] = None
    artifact_index = [
        {
            "schema_version": "tokenshare.paper_artifact_index_record.v1",
            "artifact_ref": ref,
        }
        for field_name, ref in attempt.items()
        if field_name.endswith("_ref")
        and isinstance(ref, dict)
        and field_name not in unindexed_refs
    ]
    task = {
        "condition_id": "gate_c_condition",
        "run_id": "gate_c_run",
        "task_id": "gate_c_task",
        "case_id": "factor_v2_easy_001",
        "domain": "factorization",
        "paper_difficulty": "easy",
        "root_status": "failed",
        "accepted_validity": False,
    }
    events = [
        {"event_type": event_type}
        for event_type in (
            "suite_started",
            "task_started",
            "task_completed",
            "suite_finished",
        )
    ]

    def write_json(path: Path, body: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(body, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def write_jsonl(path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                for row in rows
            ),
            encoding="utf-8",
        )

    write_json(output_root / "execution_plan.json", plan)
    write_jsonl(output_root / "per_task_results.jsonl", [task])
    write_jsonl(output_root / "per_attempt_results.jsonl", [attempt])
    write_jsonl(output_root / "events" / "event_log.jsonl", events)
    write_jsonl(
        output_root / "artifacts" / "artifact_index.jsonl",
        artifact_index,
    )
    return output_root
