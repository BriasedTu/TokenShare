from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import copy, deepcopy
from dataclasses import replace
import os
from pathlib import Path
import pickle
import signal
from threading import Event, Lock, Thread
from time import monotonic, perf_counter, sleep
from types import SimpleNamespace

import pytest

from tokenshare.executors.ai_api_request_identity import (
    PreparedOutboundRequestFactory,
)
from tokenshare.experiments.paper_budget import (
    PaperExecutionBudgetProjection,
    build_exp5_v3_token_ceiling_mapping,
    load_exp1_pilot_profile,
    plan_paper_suite,
    project_paper_execution_budget,
)
from tokenshare.experiments.paper_catalog import load_paper_catalogs
from tokenshare.experiments.paper_exp1 import EXP1_FORMAL_REQUEST_CONTROLS
from tokenshare.experiments.paper_experiment_contracts import (
    FrozenConditionSelectionBinding,
)
from tokenshare.experiments.paper_formal_plan import (
    FormalConditionSnapshot,
    FormalExecutionCoverage,
    FormalPlanSnapshot,
    FormalPreparedRequestInventory,
    FormalRepresentativeCoverage,
    FormalRootSnapshot,
    derive_paper_formal_exp4_excluded_coverage,
    derive_paper_formal_full_coverage,
    derive_paper_formal_representative_coverage,
    freeze_formal_root_prepared_replacement_requests,
    freeze_formal_root_prepared_requests,
    freeze_paper_formal_prepared_request_inventory,
    freeze_paper_formal_plan_snapshot,
    validate_paper_formal_budget_commitments,
    validate_paper_formal_plan_bindings,
)
from tokenshare.experiments.paper_response_bank import (
    RepresentativeAcquisitionAuthority,
    build_representative_unified_acquisition_plan,
    prepare_representative_acquisition_authority,
)
from tokenshare.experiments.paper_resource_accounting import FrozenPricing
from decimal import Decimal
import tokenshare.experiments.paper_formal_plan as formal_plan_module
import tokenshare.experiments.paper_experiment_contracts as contracts_module
import tokenshare.experiments.paper_response_bank as response_bank_module
from tokenshare.experiments.paper_formal_runner import (
    APPROVED_ENDPOINT_BINDINGS_KEY,
    validate_paper_formal_root_case_filter,
    validate_paper_formal_suite_plan,
)
from tokenshare.experiments.paper_exp3_fault_recovery import (
    RATE_FAULT_TARGET_SEED,
    formal_exp3_scheduled_target_unit_ids_by_case,
)
from tokenshare.experiments.paper_faults import select_fault_targets
from tokenshare.experiments.paper_models import digest_json
from tokenshare.experiments.paper_model_policy import (
    EXP5_PRICING_FRESHNESS_AS_OF,
    build_model_endpoint_cohort_preflight,
    load_model_endpoint_cohort,
    load_model_entry_map,
    load_provider_config_map,
)
from tokenshare.experiments.paper_runner import (
    build_gate_c_dispatch_plans,
    build_lean_3x3_matrix_plan,
)
from tokenshare.experiments.paper_suite_scale import load_paper_suite_scale_profile
from tokenshare.plugins.factorization.models import RangeResult
from tokenshare.plugins.factorization.schemas import RANGE_RESULT_FOUND_FACTOR
from tokenshare.plugins.factorization.split_strategy import (
    partition_candidate_ranges,
    resolve_requested_child_count,
)
from tokenshare.plugins.factorization.validator import verify_range_result
from tokenshare.plugins.factorization.runtime_adapter import (
    FactorizationRuntimeAdapter,
)
from tokenshare.plugins.lean_proof.runtime_adapter import LeanRuntimeAdapter
from tokenshare.storage.artifacts import ArtifactStore


EXPERIMENT_IDS = (
    "exp1_real_ai_feasibility",
    "exp2_real_ai_scalability",
    "exp3_real_ai_fault_recovery",
    "exp4_real_ai_protocol_ablation",
    "exp5_real_ai_model_endpoint_comparison",
)


@pytest.mark.parametrize(
    ("domain", "case_id", "expected"),
    (
        ("factorization", "factor-case", "paper_factorization_factor-case"),
        ("lean_proof", "lean-case", "paper_lean_lean-case"),
    ),
)
def test_formal_runtime_task_id_is_the_closed_shared_contract(
    domain: str,
    case_id: str,
    expected: str,
) -> None:
    helper = getattr(contracts_module, "formal_runtime_task_id", None)
    assert helper is not None
    assert helper(domain, case_id) == expected


@pytest.mark.parametrize(
    ("domain", "case_id"),
    (
        ("unknown", "case"),
        ("", "case"),
        ("factorization", ""),
        ("lean_proof", None),
    ),
)
def test_formal_runtime_task_id_rejects_unknown_or_empty_identity(
    domain: object,
    case_id: object,
) -> None:
    helper = getattr(contracts_module, "formal_runtime_task_id", None)
    assert helper is not None
    with pytest.raises(ValueError):
        helper(domain, case_id)


def _verified_factor_success_indexes(case: dict) -> tuple[int, ...]:
    requested = resolve_requested_child_count(case["split_params"])
    partition = partition_candidate_ranges(
        target_n=case["target_n"],
        requested_child_count=requested,
        max_children_per_unit=requested,
        min_divisor=case["candidate_start"],
        max_divisor=case["candidate_end"],
    )
    target = int(case["target_n"])
    accepted_indexes: list[int] = []
    for oracle_factor in case["oracle_prime_factors"]:
        factor = int(oracle_factor["prime"])
        for range_input in partition.ranges:
            if not (
                int(range_input.range_start)
                <= factor
                <= int(range_input.range_end)
            ):
                continue
            candidate = RangeResult(
                range_result_id=(
                    f"coverage-probe:{case['case_id']}:{range_input.child_index}"
                ),
                result_kind=RANGE_RESULT_FOUND_FACTOR,
                target_n=range_input.target_n,
                range_start=range_input.range_start,
                range_end=range_input.range_end,
                coverage_id=range_input.coverage_id,
                child_index=range_input.child_index,
                partition_params_digest=range_input.partition_params_digest,
                found_factor=str(factor),
                cofactor=str(target // factor),
                checked_divisor_count=(
                    factor - int(range_input.range_start) + 1
                ),
                executor_summary={
                    "executor": "deterministic_formal_coverage_probe",
                    "bounded_range_only": True,
                    "checked_start": int(range_input.range_start),
                    "checked_end": factor,
                },
                created_at="1970-01-01T00:00:00Z",
            )
            if verify_range_result(
                candidate.to_dict(),
                child_input=range_input,
            ).status == "passed":
                accepted_indexes.append(range_input.child_index)
    if not accepted_indexes:
        raise AssertionError("formal Factor case has no verifier-accepted oracle factor")
    return tuple(sorted(set(accepted_indexes)))


def _verified_factor_success_index(case: dict) -> int:
    return min(_verified_factor_success_indexes(case))


@pytest.fixture(scope="module")
def formal_inputs(tmp_path_factory: pytest.TempPathFactory):
    tmp_path = tmp_path_factory.mktemp("formal-plan")
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
        assert os.environ.get("DEEPSEEK_API_KEY") is None
        assert os.environ.get("SILICONFLOW_API_KEY") is None
        inputs = _build_formal_inputs(tmp_path)
    return inputs


@pytest.fixture(scope="module")
def formal_snapshot(formal_inputs):
    plans, catalog, budget, ai_api_configs, output_root = formal_inputs
    return freeze_paper_formal_plan_snapshot(
        dispatch_plans=plans,
        catalog_manifest=catalog,
        budget=budget,
        ai_api_configs=ai_api_configs,
        output_root=output_root,
    )


@pytest.fixture(scope="module")
def small_formal_preparation_inputs(tmp_path_factory: pytest.TempPathFactory):
    """Build two production roots without expanding the 40,520-record suite."""

    tmp_path = tmp_path_factory.mktemp("small-formal-preparation")
    catalog = load_paper_catalogs(
        factorization_path="benchmarks/paper/factorization_catalog.v2.jsonl",
        lean_path="benchmarks/paper/lean_catalog.v1.jsonl",
        lean_lemma_graph_path="benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl",
    )
    baseline_profile = load_exp1_pilot_profile(
        "benchmarks/paper/exp1_minimal_pilot_profile.v3.json"
    )
    baseline_identity = baseline_profile.model_endpoint_identity.to_dict()
    baseline_binding = {
        **baseline_identity,
        "model_entry_id": baseline_identity["selected_entry_id"],
        "request_controls": dict(EXP1_FORMAL_REQUEST_CONTROLS),
    }
    plan = build_gate_c_dispatch_plans(
        catalog_manifest=catalog,
        lean_3x3_matrix=build_lean_3x3_matrix_plan(catalog_manifest=catalog),
        experiment_ids=("exp1_real_ai_feasibility",),
        baseline_endpoint_binding=baseline_binding,
        model_endpoint_cohort_preflight=None,
        paper_suite_scale_profile=load_paper_suite_scale_profile(
            "benchmarks/paper/paper_suite_scale_profile.v1.json"
        ),
        output_root=tmp_path,
    )[0]
    bindings_by_id = {
        binding.condition_id: binding
        for binding in plan.condition_selection_bindings
    }
    cases_by_id = {
        str(case["case_id"]): case
        for case in (
            catalog.factorization_cases
            + catalog.lean_cases
            + catalog.lean_lemma_graph_cases
        )
    }
    configs = {
        baseline_identity["provider_config_id"]: (
            baseline_profile.source_provider_config
        )
    }
    condition_rows = []
    roots = []
    for domain in ("factorization", "lean_proof"):
        condition = next(
            item
            for item in plan.conditions
            if item.domain == domain and item.repeat_id == 0
        )
        binding = bindings_by_id[condition.condition_id]
        case_id = binding.selection.ordered_case_ids[0]
        case = cases_by_id[case_id]
        endpoint_controls = formal_plan_module._freeze_endpoint_controls(
            condition=condition,
            ai_api_configs=configs,
        )
        split_profile = formal_plan_module.build_paper_budget_split_profile(
            case=dict(case),
            condition=condition,
            frozen_selection=binding.selection.to_dict(),
        )
        plugin_id, plugin_version = formal_plan_module._plugin_identity(domain)
        condition_rows.append(
            FormalConditionSnapshot(
                condition=condition,
                binding=binding,
                endpoint_controls=endpoint_controls,
            )
        )
        roots.append(
            FormalRootSnapshot(
                condition=condition,
                binding=binding,
                case_id=case_id,
                case_record_digest=digest_json(case),
                condition_digest=condition.condition_digest,
                selection_digest=binding.selection.selection_digest,
                seed=condition.seed,
                repeat_id=condition.repeat_id,
                split_profile_id=getattr(
                    binding.selection,
                    "split_profile_id",
                    None,
                ),
                split_profile_digest=digest_json(split_profile),
                planned_ai_unit_ids=tuple(
                    str(unit_id) for unit_id in split_profile["ai_unit_order"]
                ),
                plugin_id=plugin_id,
                plugin_version=plugin_version,
                endpoint_controls=endpoint_controls,
            )
        )
    snapshot = FormalPlanSnapshot(
        conditions=tuple(condition_rows),
        roots=tuple(roots),
        condition_count=len(condition_rows),
        root_run_count=len(roots),
        first_attempt_ai_unit_count=sum(
            len(root.planned_ai_unit_ids) for root in roots
        ),
        provider_calls_made=0,
        budget_digest=digest_json({"kind": "focused_transient_planning"}),
    )
    return catalog, configs, snapshot


def _minimal_formal_snapshot(*, budget_marker: str) -> FormalPlanSnapshot:
    return FormalPlanSnapshot(
        conditions=(),
        roots=(),
        condition_count=0,
        root_run_count=0,
        first_attempt_ai_unit_count=0,
        provider_calls_made=0,
        budget_digest=digest_json({"budget_marker": budget_marker}),
    )


def test_formal_snapshot_digest_is_transiently_memoized_for_reads_and_to_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _minimal_formal_snapshot(budget_marker="memo-read")
    expected = digest_json(snapshot._body())
    instance_state_before = dict(vars(snapshot))
    real_digest_json = formal_plan_module.digest_json
    compute_count = 0

    def _counting_digest(value):
        nonlocal compute_count
        compute_count += 1
        return real_digest_json(value)

    monkeypatch.setattr(formal_plan_module, "digest_json", _counting_digest)

    direct = tuple(snapshot.snapshot_digest for _ in range(100))
    serialized = tuple(snapshot.to_dict()["snapshot_digest"] for _ in range(100))

    assert set(direct) == {expected}
    assert set(serialized) == {expected}
    assert compute_count == 1
    assert vars(snapshot) == instance_state_before
    assert set(snapshot.to_dict()) == {
        "schema_version",
        "condition_count",
        "root_run_count",
        "first_attempt_ai_unit_count",
        "provider_calls_made",
        "budget_digest",
        "conditions",
        "roots",
        "snapshot_digest",
    }


def test_formal_snapshot_digest_cache_is_pickle_and_copy_transient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _minimal_formal_snapshot(budget_marker="memo-pickle")
    old_pickle = pickle.dumps(snapshot, protocol=pickle.HIGHEST_PROTOCOL)
    canonical = snapshot.snapshot_digest
    assert pickle.dumps(snapshot, protocol=pickle.HIGHEST_PROTOCOL) == old_pickle

    loaded = pickle.loads(old_pickle)
    shallow = copy(snapshot)
    deep = deepcopy(snapshot)
    replaced = replace(
        snapshot,
        budget_digest=digest_json({"budget_marker": "memo-replaced"}),
    )
    assert all(
        candidate is not snapshot for candidate in (loaded, shallow, deep, replaced)
    )

    real_digest_json = formal_plan_module.digest_json
    compute_count = 0

    def _counting_digest(value):
        nonlocal compute_count
        compute_count += 1
        return real_digest_json(value)

    monkeypatch.setattr(formal_plan_module, "digest_json", _counting_digest)

    assert loaded.snapshot_digest == canonical
    assert shallow.snapshot_digest == canonical
    assert deep.snapshot_digest == canonical
    assert replaced.snapshot_digest == digest_json(replaced._body())
    assert replaced.snapshot_digest != canonical
    assert compute_count == 4
    assert loaded == shallow == deep == snapshot


def test_formal_snapshot_digest_concurrent_first_read_computes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _minimal_formal_snapshot(budget_marker="memo-concurrent")
    real_digest_json = formal_plan_module.digest_json
    counter_lock = Lock()
    compute_count = 0

    def _counting_digest(value):
        nonlocal compute_count
        with counter_lock:
            compute_count += 1
        sleep(0.02)
        return real_digest_json(value)

    monkeypatch.setattr(formal_plan_module, "digest_json", _counting_digest)

    with ThreadPoolExecutor(max_workers=16) as pool:
        values = tuple(pool.map(lambda _index: snapshot.snapshot_digest, range(64)))

    assert len(set(values)) == 1
    assert compute_count == 1


def test_formal_snapshot_digest_child_reset_reinitializes_transient_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _minimal_formal_snapshot(budget_marker="memo-child-reset")
    canonical = snapshot.snapshot_digest
    snapshot_id = id(snapshot)
    original_lock = formal_plan_module._FORMAL_PLAN_SNAPSHOT_DIGEST_LOCK
    assert snapshot_id in formal_plan_module._FORMAL_PLAN_SNAPSHOT_DIGEST_CACHE
    real_digest_json = formal_plan_module.digest_json
    compute_count = 0

    def _counting_digest(value):
        nonlocal compute_count
        compute_count += 1
        return real_digest_json(value)

    monkeypatch.setattr(formal_plan_module, "digest_json", _counting_digest)

    formal_plan_module._reset_formal_plan_snapshot_digest_cache_after_fork()

    assert formal_plan_module._FORMAL_PLAN_SNAPSHOT_DIGEST_LOCK is not original_lock
    assert formal_plan_module._FORMAL_PLAN_SNAPSHOT_DIGEST_CACHE == {}
    assert snapshot.snapshot_digest == canonical
    assert compute_count == 1


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX fork is unavailable")
def test_formal_snapshot_digest_fork_child_does_not_inherit_locked_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _minimal_formal_snapshot(budget_marker="memo-fork")
    expected = digest_json(snapshot._body())
    parent_pid = os.getpid()
    entered = Event()
    release = Event()
    real_digest_json = formal_plan_module.digest_json

    def _blocking_parent_digest(value):
        if os.getpid() == parent_pid:
            entered.set()
            if not release.wait(timeout=5.0):
                raise AssertionError("parent digest worker was not released")
        return real_digest_json(value)

    monkeypatch.setattr(
        formal_plan_module,
        "digest_json",
        _blocking_parent_digest,
    )
    worker = Thread(target=lambda: snapshot.snapshot_digest, daemon=True)
    worker.start()
    assert entered.wait(timeout=2.0)
    child_pid = os.fork()
    if child_pid == 0:
        try:
            os._exit(0 if snapshot.snapshot_digest == expected else 2)
        except BaseException:
            os._exit(3)

    child_status = None
    try:
        deadline = monotonic() + 2.0
        while monotonic() < deadline:
            waited_pid, status = os.waitpid(child_pid, os.WNOHANG)
            if waited_pid == child_pid:
                child_status = status
                break
            sleep(0.01)
        if child_status is None:
            os.kill(child_pid, signal.SIGKILL)
            os.waitpid(child_pid, 0)
            pytest.fail("fork child inherited the locked snapshot digest cache")
        assert os.waitstatus_to_exitcode(child_status) == 0
    finally:
        release.set()
        worker.join(timeout=2.0)
    assert not worker.is_alive()


def test_full_formal_snapshot_digest_first_read_and_repeats_are_bounded(
    formal_snapshot: FormalPlanSnapshot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = replace(formal_snapshot)
    real_digest_json = formal_plan_module.digest_json
    compute_count = 0

    def _counting_digest(value):
        nonlocal compute_count
        compute_count += 1
        return real_digest_json(value)

    monkeypatch.setattr(formal_plan_module, "digest_json", _counting_digest)

    first_started = perf_counter()
    expected = snapshot.snapshot_digest
    first_elapsed = perf_counter() - first_started
    repeat_started = perf_counter()
    repeats = tuple(snapshot.snapshot_digest for _ in range(100))
    repeat_elapsed = perf_counter() - repeat_started

    assert set(repeats) == {expected}
    assert compute_count == 1
    assert first_elapsed + repeat_elapsed <= 120.0
    assert repeat_elapsed <= 1.0


def test_representative_coverage_reuses_formal_objects_and_covers_all_axes(
    formal_inputs,
    formal_snapshot,
) -> None:
    plans, catalog, _budget, _ai_api_configs, _output_root = formal_inputs

    coverage = derive_paper_formal_representative_coverage(
        snapshot=formal_snapshot,
        dispatch_plans=plans,
        catalog_manifest=catalog,
        repeat_ids=(0,),
    )

    expected_condition_counts = {
        "exp1_real_ai_feasibility": 12,
        "exp2_real_ai_scalability": 6,
        "exp3_real_ai_fault_recovery": 81,
        "exp4_real_ai_protocol_ablation": 30,
        "exp5_real_ai_model_endpoint_comparison": 16,
    }
    assert coverage.condition_count == sum(expected_condition_counts.values())
    assert coverage.root_run_count == coverage.condition_count == 145
    assert coverage.provider_calls_made == 0
    assert coverage.source_snapshot_digest == formal_snapshot.snapshot_digest
    assert coverage.coverage_digest.startswith("sha256:")
    assert coverage.to_dict()["coverage_digest"] == coverage.coverage_digest

    formal_conditions_by_id = {
        item.condition.condition_id: item for item in formal_snapshot.conditions
    }
    for condition, binding in zip(
        coverage.conditions,
        coverage.bindings,
        strict=True,
    ):
        formal = formal_conditions_by_id[condition.condition_id]
        assert condition is formal.condition
        assert binding is formal.binding
        assert condition.condition_digest == binding.condition_digest
        assert binding.selection.selection_digest == formal.binding.selection.selection_digest
        assert condition.seed == formal.condition.seed
        assert condition.repeat_id == formal.condition.repeat_id == 0
        selected_case_ids = coverage.root_case_filter[condition.condition_id]
        assert selected_case_ids
        assert tuple(
            case_id
            for case_id in binding.selection.ordered_case_ids
            if case_id in selected_case_ids
        ) == selected_case_ids

    by_experiment = {
        experiment_id: tuple(
            condition
            for condition in coverage.conditions
            if condition.experiment_id == experiment_id
        )
        for experiment_id in expected_condition_counts
    }
    assert {
        experiment_id: len(conditions)
        for experiment_id, conditions in by_experiment.items()
    } == expected_condition_counts
    assert {condition.domain for condition in by_experiment["exp1_real_ai_feasibility"]} == {
        "factorization",
        "lean_proof",
    }


    exp1 = by_experiment["exp1_real_ai_feasibility"]
    assert {
        (condition.domain, condition.paper_difficulty, condition.topic_family)
        for condition in exp1
    } == {
        *(('factorization', difficulty, None) for difficulty in ('easy', 'medium', 'hard')),
        *(
            ('lean_proof', difficulty, topic)
            for difficulty in ('simple', 'medium_lemma_dag', 'hard_frontier')
            for topic in ('pure_logic', 'function_set', 'induction')
        ),
    }
    exp2 = by_experiment["exp2_real_ai_scalability"]
    assert {condition.domain for condition in exp2} == {"factorization"}
    assert {condition.worker_count for condition in exp2} == {1, 3, 7, 10, 30, 50}
    exp3 = by_experiment["exp3_real_ai_fault_recovery"]
    assert {condition.fault_type for condition in exp3} == {
        "false_positive",
        "false_negative",
        "no_return",
        "late_submission",
        "executor_error",
        "worker_death",
    }
    rate_faults = tuple(
        condition for condition in exp3 if condition.fault_type != "worker_death"
    )
    assert {
        (condition.domain, int(round(float(condition.fault_rate) * 100)))
        for condition in rate_faults
    } == {
        *(('factorization', rate) for rate in (1, 5, 10, 25, 50, 100)),
        *(('lean_proof', rate) for rate in (10, 50, 100)),
    }
    assert {
        (condition.domain, condition.fault_type)
        for condition in rate_faults
    } == {
        (domain, fault_type)
        for domain in ("factorization", "lean_proof")
        for fault_type in (
            "false_positive",
            "false_negative",
            "no_return",
            "late_submission",
            "executor_error",
        )
    }
    worker_deaths = tuple(
        condition for condition in exp3 if condition.fault_type == "worker_death"
    )
    death_axes = {
        (
            condition.domain,
            condition.paper_difficulty,
            condition.topic_family,
            int(condition.condition_id.split("__dead", 1)[1].split("__", 1)[0]),
            int(
                condition.condition_id.split("__dead", 1)[1]
                .split("__", 2)[1]
                .removeprefix("p")
            ),
        )
        for condition in worker_deaths
    }
    assert death_axes == {
        *(
            ("factorization", difficulty, None, dead, progress)
            for difficulty in ("easy", "medium", "hard")
            for dead in (1, 3)
            for progress in (25, 50, 75)
        ),
        *(
            ("lean_proof", "medium_lemma_dag", topic, dead, progress)
            for topic in ("pure_logic", "function_set", "induction")
            for dead in (1, 3)
            for progress in (25, 50, 75)
        ),
    }
    exp4 = by_experiment["exp4_real_ai_protocol_ablation"]
    assert {condition.ablation_mode for condition in exp4} == {
        "FULL",
        "NO_VERIFICATION",
        "NO_PARSER_POLICY",
        "NO_REQUEUE",
        "NO_MERGE_GATE",
    }
    assert {
        (condition.domain, condition.paper_difficulty, condition.ablation_mode)
        for condition in exp4
    } == {
        *(
            ("factorization", difficulty, mode)
            for difficulty in ("easy", "medium", "hard")
            for mode in (
                "FULL",
                "NO_VERIFICATION",
                "NO_PARSER_POLICY",
                "NO_REQUEUE",
                "NO_MERGE_GATE",
            )
        ),
        *(
            ("lean_proof", difficulty, mode)
            for difficulty in ("simple", "medium_lemma_dag", "hard_frontier")
            for mode in (
                "FULL",
                "NO_VERIFICATION",
                "NO_PARSER_POLICY",
                "NO_REQUEUE",
                "NO_MERGE_GATE",
            )
        ),
    }
    exp5 = by_experiment["exp5_real_ai_model_endpoint_comparison"]
    assert len({condition.model_endpoint_identity_digest for condition in exp5}) == 4
    assert {condition.domain for condition in exp5} == {"factorization", "lean_proof"}
    for endpoint_digest in {
        condition.model_endpoint_identity_digest for condition in exp5
    }:
        assert {
            (condition.domain, condition.paper_difficulty, condition.topic_family)
            for condition in exp5
            if condition.model_endpoint_identity_digest == endpoint_digest
        } == {
            ("factorization", "hard", None),
            ("lean_proof", "hard_frontier", "pure_logic"),
            ("lean_proof", "hard_frontier", "function_set"),
            ("lean_proof", "hard_frontier", "induction"),
        }


def test_full_and_representative_coverage_are_typed_views_of_one_snapshot(
    formal_inputs,
    formal_snapshot,
) -> None:
    plans, catalog, _budget, _ai_api_configs, _output_root = formal_inputs

    full = derive_paper_formal_full_coverage(
        snapshot=formal_snapshot,
        dispatch_plans=plans,
        catalog_manifest=catalog,
    )
    representative = derive_paper_formal_representative_coverage(
        snapshot=formal_snapshot,
        dispatch_plans=plans,
        catalog_manifest=catalog,
        repeat_ids=(0,),
    )

    assert isinstance(full, FormalExecutionCoverage)
    assert isinstance(representative, FormalRepresentativeCoverage)
    assert FormalRepresentativeCoverage is FormalExecutionCoverage
    assert full.selection_kind == "full"
    assert representative.selection_kind == "filtered"
    assert (
        full.condition_count,
        full.root_run_count,
        full.selected_first_attempt_ai_unit_count,
    ) == (324, 6_384, 40_520)
    assert representative.root_run_count == 145
    assert 0 < representative.selected_first_attempt_ai_unit_count < 40_520
    assert full.provider_calls_made == representative.provider_calls_made == 0
    assert full.source_snapshot is representative.source_snapshot is formal_snapshot
    assert full.source_snapshot_digest == representative.source_snapshot_digest
    assert full.conditions == tuple(item.condition for item in formal_snapshot.conditions)
    assert full.bindings == tuple(item.binding for item in formal_snapshot.conditions)
    assert full.roots == formal_snapshot.roots
    assert all(
        condition is source.condition and binding is source.binding
        for condition, binding, source in zip(
            full.conditions,
            full.bindings,
            formal_snapshot.conditions,
            strict=True,
        )
    )
    assert all(
        root is source_root
        for root, source_root in zip(full.roots, formal_snapshot.roots, strict=True)
    )
    assert {id(root) for root in representative.roots} < {
        id(root) for root in full.roots
    }
    assert full.coverage_digest != representative.coverage_digest


@pytest.mark.parametrize("selection", ("full", "representative"))
def test_exp4_excluded_coverage_delegates_only_exp1_to_3_and_exp5(
    monkeypatch: pytest.MonkeyPatch,
    selection: str,
) -> None:
    """此处只检验 typed coverage 输入，不构造 Full catalog fixture。"""

    conditions = tuple(
        SimpleNamespace(experiment_id=experiment_id, condition_id=condition_id)
        for experiment_id, condition_id in (
            ("exp1_real_ai_feasibility", "exp1-c0"),
            ("exp2_real_ai_scalability", "exp2-c0"),
            ("exp3_real_ai_fault_recovery", "exp3-c0"),
            ("exp4_real_ai_protocol_ablation", "exp4-c0"),
            ("exp5_real_ai_model_endpoint_comparison", "exp5-c0"),
        )
    )
    base = SimpleNamespace(
        conditions=conditions,
        root_case_filter={condition.condition_id: ("case-0",) for condition in conditions},
    )
    monkeypatch.setattr(
        formal_plan_module,
        "derive_paper_formal_full_coverage",
        lambda **_kwargs: base,
    )
    monkeypatch.setattr(
        formal_plan_module,
        "derive_paper_formal_representative_coverage",
        lambda **_kwargs: base,
    )
    captured: dict[str, object] = {}

    def fake_derive(**kwargs):
        captured.update(kwargs)
        return "exp4-excluded-coverage"

    monkeypatch.setattr(
        formal_plan_module,
        "derive_paper_formal_execution_coverage",
        fake_derive,
    )

    assert derive_paper_formal_exp4_excluded_coverage(
        snapshot=object(),
        dispatch_plans=(),
        catalog_manifest=object(),
        selection=selection,
    ) == "exp4-excluded-coverage"
    assert captured["selection_kind"] == (
        "full_exp1_exp3_exp5"
        if selection == "full"
        else "representative_exp1_exp3_exp5"
    )
    assert captured["selected_condition_ids"] == (
        "exp1-c0",
        "exp2-c0",
        "exp3-c0",
        "exp5-c0",
    )
    assert captured["root_case_filter"] == {
        "exp1-c0": ("case-0",),
        "exp2-c0": ("case-0",),
        "exp3-c0": ("case-0",),
        "exp5-c0": ("case-0",),
    }


def test_representative_exp4_excluded_coverage_keeps_exact_formal_exp5_sixteen_roots(
    formal_inputs,
    formal_snapshot,
) -> None:
    """正式 Exp4-excluded scope 只能带入 16 个正式 Exp5 root。"""

    plans, catalog, _budget, _ai_api_configs, _output_root = formal_inputs

    coverage = derive_paper_formal_exp4_excluded_coverage(
        snapshot=formal_snapshot,
        dispatch_plans=plans,
        catalog_manifest=catalog,
        selection="representative",
    )

    exp5_conditions = tuple(
        condition
        for condition in coverage.conditions
        if condition.experiment_id == "exp5_real_ai_model_endpoint_comparison"
    )
    exp5_roots = tuple(
        root
        for root in coverage.roots
        if root.condition.experiment_id == "exp5_real_ai_model_endpoint_comparison"
    )

    assert coverage.selection_kind == "representative_exp1_exp3_exp5"
    assert "exp4_real_ai_protocol_ablation" not in {
        condition.experiment_id for condition in coverage.conditions
    }
    assert len(exp5_conditions) == 16
    assert len(exp5_roots) == 16
    assert sum(
        len(coverage.root_case_filter[condition.condition_id])
        for condition in exp5_conditions
    ) == 16
    assert all(
        len(coverage.root_case_filter[condition.condition_id]) == 1
        for condition in exp5_conditions
    )
    assert {
        condition.model_entry_id for condition in exp5_conditions
    } == {
        "glm_5_2_exp5_v3",
        "qwen3_14b_exp5_v3",
        "minimax_m2_5_exp5_v3",
        "deepseek_v3_pro_exp5_v3",
    }
    assert {
        condition.model_endpoint_identity_digest for condition in exp5_conditions
    } == {
        root.endpoint_controls.model_endpoint_identity_digest
        for root in exp5_roots
    }


def test_full_coverage_rejects_snapshot_total_and_root_order_drift(
    formal_inputs,
    formal_snapshot,
) -> None:
    plans, catalog, _budget, _ai_api_configs, _output_root = formal_inputs

    with pytest.raises(ValueError, match="snapshot totals drift"):
        derive_paper_formal_full_coverage(
            snapshot=replace(
                formal_snapshot,
                first_attempt_ai_unit_count=(
                    formal_snapshot.first_attempt_ai_unit_count + 1
                ),
            ),
            dispatch_plans=plans,
            catalog_manifest=catalog,
        )
    with pytest.raises(ValueError, match="root order/selection drift"):
        derive_paper_formal_full_coverage(
            snapshot=replace(
                formal_snapshot,
                roots=tuple(reversed(formal_snapshot.roots)),
            ),
            dispatch_plans=plans,
            catalog_manifest=catalog,
        )


def test_full_and_representative_budget_projections_are_selection_exact(
    formal_inputs,
    formal_snapshot,
) -> None:
    plans, catalog, budget, _ai_api_configs, _output_root = formal_inputs
    full_coverage = derive_paper_formal_full_coverage(
        snapshot=formal_snapshot,
        dispatch_plans=plans,
        catalog_manifest=catalog,
    )
    representative_coverage = derive_paper_formal_representative_coverage(
        snapshot=formal_snapshot,
        dispatch_plans=plans,
        catalog_manifest=catalog,
        repeat_ids=(0,),
    )

    full = project_paper_execution_budget(
        snapshot=formal_snapshot,
        budget=budget,
        coverage=full_coverage,
    )
    representative = project_paper_execution_budget(
        snapshot=formal_snapshot,
        budget=budget,
        coverage=representative_coverage,
    )

    assert isinstance(full, PaperExecutionBudgetProjection)
    assert full.source_budget is representative.source_budget is budget
    assert full.source_snapshot is representative.source_snapshot is formal_snapshot
    assert full.coverage is full_coverage
    assert representative.coverage is representative_coverage
    assert full.source_budget_digest == budget.budget_digest
    assert full.source_snapshot_digest == formal_snapshot.snapshot_digest
    assert full.coverage_digest == full_coverage.coverage_digest
    assert (
        full.condition_count,
        full.root_run_count,
        full.first_attempt_ai_unit_count,
    ) == (324, 6_384, 40_520)
    assert full.provider_attempt_upper_bound == budget.max_provider_attempts
    assert full.protocol_replacement_reserve == (
        budget.max_provider_attempts - budget.planned_ai_units
    )
    assert full.token_upper_bound == budget.token_upper_bound
    assert full.cost_upper_bound == budget.cost_upper_bound
    assert full.disk_estimate == budget.disk_estimate
    assert full.disk_upper_bound_bytes == budget.disk_estimate["forecast_bytes"]
    assert full.provider_calls_made == representative.provider_calls_made == 0
    assert full.projection_digest.startswith("sha256:")
    assert full.to_dict()["projection_digest"] == full.projection_digest

    root_ratio = representative.root_run_count / full.root_run_count
    assert representative.root_run_count == 145
    assert representative.provider_attempt_upper_bound != round(
        full.provider_attempt_upper_bound * root_ratio
    )
    assert representative.token_upper_bound != round(full.token_upper_bound * root_ratio)
    assert representative.disk_upper_bound_bytes != round(
        full.disk_upper_bound_bytes * root_ratio
    )
    assert representative.hard_limits == {
        "max_total_provider_attempts": representative.provider_attempt_upper_bound,
        "max_total_tokens": representative.token_upper_bound,
        "max_cost_estimate": representative.cost_upper_bound,
        "max_disk_bytes": representative.disk_upper_bound_bytes,
    }


@pytest.mark.parametrize("drift_kind", ("replacement", "endpoint", "disk"))
def test_execution_budget_projection_rejects_frozen_authority_drift(
    formal_inputs,
    formal_snapshot,
    drift_kind: str,
) -> None:
    plans, catalog, budget, _ai_api_configs, _output_root = formal_inputs
    coverage = derive_paper_formal_full_coverage(
        snapshot=formal_snapshot,
        dispatch_plans=plans,
        catalog_manifest=catalog,
    )
    quota = deepcopy(budget.quota_preflight)
    disk_estimate = deepcopy(budget.disk_estimate)
    if drift_kind == "replacement":
        quota["budget_commitments"]["experiment_budget_identity"][
            "replacement_policy_by_condition"
        ][0]["planned_ai_unit_count"] += 1
        match = "replacement"
    elif drift_kind == "endpoint":
        endpoint = quota["budget_commitments"]["endpoint_budget_identity"]
        member_id = next(iter(endpoint["member_token_cost_subtotals"]))
        endpoint["member_token_cost_subtotals"][member_id][
            "cost_upper_bound_per_provider_attempt"
        ] += 0.001
        match = "endpoint"
    else:
        disk_estimate["inputs"]["planned_root_runs"] += 1
        match = "disk"
    drifted_budget = replace(
        budget,
        quota_preflight=quota,
        disk_estimate=disk_estimate,
    )

    with pytest.raises(ValueError, match=match):
        project_paper_execution_budget(
            snapshot=formal_snapshot,
            budget=drifted_budget,
            coverage=coverage,
        )


def test_representative_exp3_rate_fault_roots_contain_a_formal_target(
    formal_inputs,
    formal_snapshot,
) -> None:
    plans, catalog, _budget, _ai_api_configs, _output_root = formal_inputs
    coverage = derive_paper_formal_representative_coverage(
        snapshot=formal_snapshot,
        dispatch_plans=plans,
        catalog_manifest=catalog,
        repeat_ids=(0,),
    )
    roots_by_condition: dict[str, list] = {}
    for root in formal_snapshot.roots:
        roots_by_condition.setdefault(root.condition.condition_id, []).append(root)

    for condition in coverage.conditions:
        if (
            condition.experiment_id != "exp3_real_ai_fault_recovery"
            or condition.fault_type == "worker_death"
        ):
            continue
        roots = roots_by_condition[condition.condition_id]
        all_unit_ids = tuple(
            f"{root.case_id}:{unit_id}"
            for root in roots
            for unit_id in root.planned_ai_unit_ids
        )
        targets = set(
            select_fault_targets(
                all_unit_ids,
                fault_rate=float(condition.fault_rate),
                seed=RATE_FAULT_TARGET_SEED,
            )
        )
        selected_unit_ids = {
            f"{root.case_id}:{unit_id}"
            for root in roots
            if root.case_id in coverage.root_case_filter[condition.condition_id]
            for unit_id in root.planned_ai_unit_ids
        }
        assert selected_unit_ids & targets, condition.condition_id


def test_representative_worker_death_root_maximizes_formal_scheduled_targets(
    formal_inputs,
    formal_snapshot,
) -> None:
    plans, catalog, _budget, _ai_api_configs, _output_root = formal_inputs
    coverage = derive_paper_formal_representative_coverage(
        snapshot=formal_snapshot,
        dispatch_plans=plans,
        catalog_manifest=catalog,
        repeat_ids=(0,),
    )
    roots_by_condition: dict[str, list] = {}
    for root in formal_snapshot.roots:
        roots_by_condition.setdefault(root.condition.condition_id, []).append(root)

    selected_factor_axes: set[tuple[str, str, int]] = set()
    fallback_factor_axes: set[tuple[str, str, int]] = set()
    selected_factor_condition_count = 0
    for condition in coverage.conditions:
        if (
            condition.experiment_id != "exp3_real_ai_fault_recovery"
            or condition.fault_type != "worker_death"
        ):
            continue
        roots = roots_by_condition[condition.condition_id]
        targets_by_case = formal_exp3_scheduled_target_unit_ids_by_case(
            condition=condition,
            ordered_case_ids=tuple(root.case_id for root in roots),
            planned_ai_unit_ids_by_case={
                root.case_id: root.planned_ai_unit_ids for root in roots
            },
        )
        selected_case_id = coverage.root_case_filter[condition.condition_id][0]
        selected_target_count = len(targets_by_case[selected_case_id])
        assert selected_target_count > 0
        assert selected_target_count == max(
            len(targets) for targets in targets_by_case.values()
        )
        if condition.domain != "factorization":
            continue
        selected_factor_condition_count += 1
        selected_root = next(
            root for root in roots if root.case_id == selected_case_id
        )
        target_indexes = {
            selected_root.planned_ai_unit_ids.index(
                target.removeprefix(f"{selected_case_id}:")
            )
            for target in targets_by_case[selected_case_id]
        }
        cases_by_id = {
            str(case["case_id"]): case for case in catalog.factorization_cases
        }
        selected_success_indexes = set(
            _verified_factor_success_indexes(cases_by_id[selected_case_id])
        )
        progress_axis = next(
            part for part in condition.condition_id.split("__") if part.startswith("p")
        )
        death_axis = int(
            next(
                part.removeprefix("dead")
                for part in condition.condition_id.split("__")
                if part.startswith("dead")
            )
        )
        axis = (str(condition.difficulty), progress_axis, death_axis)
        selected_factor_axes.add(axis)
        if not selected_success_indexes <= target_indexes:
            max_target_roots = tuple(
                root
                for root in roots
                if len(targets_by_case[root.case_id]) == selected_target_count
            )
            assert not any(
                set(_verified_factor_success_indexes(cases_by_id[root.case_id]))
                <= {
                    root.planned_ai_unit_ids.index(
                        target.removeprefix(f"{root.case_id}:")
                    )
                    for target in targets_by_case[root.case_id]
                }
                for root in max_target_roots
            )
            assert min(selected_success_indexes) >= min(target_indexes)
            fallback_factor_axes.add(axis)

    assert selected_factor_condition_count == 18
    assert selected_factor_axes == {
        (difficulty, progress, dead)
        for difficulty in ("easy", "medium", "hard")
        for progress in ("p25", "p50", "p75")
        for dead in (1, 3)
    }
    assert fallback_factor_axes == {("hard", "p50", 1)}


def test_representative_factor_worker_death_selection_rejects_no_viable_candidate(
    formal_inputs,
    formal_snapshot,
) -> None:
    _plans, catalog, _budget, _ai_api_configs, _output_root = formal_inputs
    condition = next(
        item.condition
        for item in formal_snapshot.conditions
        if item.condition.experiment_id == "exp3_real_ai_fault_recovery"
        and item.condition.domain == "factorization"
        and item.condition.fault_type == "worker_death"
        and "__p75__" in item.condition.condition_id
    )
    roots = tuple(
        root for root in formal_snapshot.roots if root.condition is condition
    )
    targets_by_case = formal_exp3_scheduled_target_unit_ids_by_case(
        condition=condition,
        ordered_case_ids=tuple(root.case_id for root in roots),
        planned_ai_unit_ids_by_case={
            root.case_id: root.planned_ai_unit_ids for root in roots
        },
    )
    cases_by_id = {
        str(case["case_id"]): case for case in catalog.factorization_cases
    }
    max_target_count = max(len(targets) for targets in targets_by_case.values())
    nonviable = tuple(
        root
        for root in roots
        if len(targets_by_case[root.case_id]) == max_target_count
        and not formal_plan_module._factorization_worker_death_root_is_structurally_viable(
            root=root,
            target_unit_ids=targets_by_case[root.case_id],
            case=cases_by_id[root.case_id],
        )
    )
    assert nonviable

    with pytest.raises(ValueError, match="no structurally viable"):
        formal_plan_module._select_representative_worker_death_root(
            condition=condition,
            roots=nonviable,
            targets_by_case={
                root.case_id: targets_by_case[root.case_id] for root in nonviable
            },
            cases_by_id=cases_by_id,
        )


def test_representative_factor_worker_death_selection_rejects_catalog_tamper(
    formal_inputs,
    formal_snapshot,
) -> None:
    _plans, catalog, _budget, _ai_api_configs, _output_root = formal_inputs
    root = next(
        root
        for root in formal_snapshot.roots
        if root.condition.experiment_id == "exp3_real_ai_fault_recovery"
        and root.condition.domain == "factorization"
        and root.condition.fault_type == "worker_death"
        and "__p75__" in root.condition.condition_id
    )
    case = next(
        case
        for case in catalog.factorization_cases
        if case["case_id"] == root.case_id
    )
    tampered = deepcopy(case)
    tampered["target_n"] = str(int(tampered["target_n"]) + 1)
    targets = formal_exp3_scheduled_target_unit_ids_by_case(
        condition=root.condition,
        ordered_case_ids=(root.case_id,),
        planned_ai_unit_ids_by_case={root.case_id: root.planned_ai_unit_ids},
    )[root.case_id]

    with pytest.raises(ValueError, match="catalog/root digest"):
        formal_plan_module._factorization_worker_death_root_is_structurally_viable(
            root=root,
            target_unit_ids=targets,
            case=tampered,
        )


def test_representative_coverage_rejects_coherent_binding_and_root_tamper(
    formal_inputs,
    formal_snapshot,
) -> None:
    plans, catalog, _budget, _ai_api_configs, _output_root = formal_inputs
    coverage = derive_paper_formal_representative_coverage(
        snapshot=formal_snapshot,
        dispatch_plans=plans,
        catalog_manifest=catalog,
        repeat_ids=(0,),
    )

    with pytest.raises(ValueError, match="condition/binding"):
        replace(
            coverage,
            bindings=(coverage.bindings[1], coverage.bindings[0], *coverage.bindings[2:]),
        )

    first_condition = coverage.conditions[0]
    selected_case_ids = set(coverage.root_case_filter[first_condition.condition_id])
    rogue_root = next(
        root
        for root in formal_snapshot.roots
        if root.condition is first_condition and root.case_id not in selected_case_ids
    )
    with pytest.raises(ValueError, match="root"):
        replace(coverage, roots=(rogue_root, *coverage.roots[1:]))

    with pytest.raises(ValueError, match="root order"):
        replace(coverage, roots=tuple(reversed(coverage.roots)))


def test_formal_root_case_filter_validator_accepts_only_exact_formal_subset(
    formal_inputs,
) -> None:
    plans, _catalog, _budget, _ai_api_configs, _output_root = formal_inputs
    selected = tuple(
        condition.condition_id
        for plan in plans
        for condition in plan.conditions
        if condition.repeat_id == 0
    )
    selections = {
        binding.condition_id: binding.selection
        for plan in plans
        for binding in plan.condition_selection_bindings
    }
    root_filter = {
        condition_id: (selections[condition_id].ordered_case_ids[0],)
        for condition_id in selected
    }

    assert validate_paper_formal_root_case_filter(
        dispatch_plans=plans,
        selected_condition_ids=selected,
        root_case_filter=root_filter,
    ) == root_filter
    with pytest.raises(ValueError, match="selected condition"):
        validate_paper_formal_root_case_filter(
            dispatch_plans=plans,
            selected_condition_ids=(*selected, "not-formal"),
            root_case_filter=root_filter,
        )
    with pytest.raises(ValueError, match="selected condition"):
        validate_paper_formal_root_case_filter(
            dispatch_plans=plans,
            selected_condition_ids=(*selected, selected[0]),
            root_case_filter=root_filter,
        )
    with pytest.raises(ValueError, match="condition coverage"):
        validate_paper_formal_root_case_filter(
            dispatch_plans=plans,
            selected_condition_ids=selected,
            root_case_filter={key: value for key, value in root_filter.items() if key != selected[0]},
        )
    first_id = selected[0]
    with pytest.raises(ValueError, match="non-canonical case"):
        validate_paper_formal_root_case_filter(
            dispatch_plans=plans,
            selected_condition_ids=selected,
            root_case_filter={**root_filter, first_id: ("not-a-formal-case",)},
        )
    with pytest.raises(ValueError, match="canonical case order"):
        validate_paper_formal_root_case_filter(
            dispatch_plans=plans,
            selected_condition_ids=selected,
            root_case_filter={
                **root_filter,
                first_id: tuple(reversed(selections[first_id].ordered_case_ids[:2])),
            },
        )


@pytest.fixture(scope="module")
def single_root_prepared_inventory(
    formal_inputs,
    formal_snapshot,
    tmp_path_factory: pytest.TempPathFactory,
):
    _plans, catalog, _budget, ai_api_configs, _output_root = formal_inputs
    root = formal_snapshot.roots[0]
    records = freeze_formal_root_prepared_requests(
        root=root,
        catalog_manifest=catalog,
        ai_api_configs=ai_api_configs,
        planning_artifact_root=tmp_path_factory.mktemp("single-root-validated"),
    )
    condition = next(
        item
        for item in formal_snapshot.conditions
        if item.condition is root.condition and item.binding is root.binding
    )
    snapshot = replace(
        formal_snapshot,
        conditions=(condition,),
        roots=(root,),
        condition_count=1,
        root_run_count=1,
        first_attempt_ai_unit_count=len(root.planned_ai_unit_ids),
    )
    inventory = FormalPreparedRequestInventory(
        records=records,
        record_count=len(records),
        unique_inference_request_count=len(
            {record.inference_request_digest for record in records}
        ),
        provider_calls_made=0,
        source_snapshot_digest=snapshot.snapshot_digest,
    )
    return root, records, snapshot, inventory, ai_api_configs


def test_formal_snapshot_freezes_every_current_full_plan_root_without_provider_calls(
    formal_inputs,
) -> None:
    plans, catalog, budget, ai_api_configs, output_root = formal_inputs
    snapshot = freeze_paper_formal_plan_snapshot(
        dispatch_plans=plans,
        catalog_manifest=catalog,
        budget=budget,
        ai_api_configs=ai_api_configs,
        output_root=output_root,
    )

    assert snapshot.condition_count == 324
    assert snapshot.root_run_count == 6_384
    assert snapshot.first_attempt_ai_unit_count == 40_520
    assert snapshot.provider_calls_made == 0
    assert snapshot.snapshot_digest.startswith("sha256:")
    snapshot_body = snapshot.to_dict()
    persisted_digest = snapshot_body.pop("snapshot_digest")
    assert persisted_digest == digest_json(snapshot_body)
    assert len(snapshot.conditions) == snapshot.condition_count
    assert len(snapshot.roots) == snapshot.root_run_count
    assert snapshot.conditions[0].condition is plans[0].conditions[0]
    assert (
        snapshot.conditions[0].binding
        is plans[0].condition_selection_bindings[0]
    )
    assert all(root.planned_ai_unit_ids for root in snapshot.roots)
    assert all(root.split_profile_digest.startswith("sha256:") for root in snapshot.roots)
    assert all(root.plugin_id in {"factorization", "lean_proof"} for root in snapshot.roots)
    assert all(root.seed == root.condition.seed for root in snapshot.roots)
    assert all(root.repeat_id == root.condition.repeat_id for root in snapshot.roots)
    assert all(root.condition_digest == root.condition.condition_digest for root in snapshot.roots)
    assert all(root.selection_digest == root.binding.selection.selection_digest for root in snapshot.roots)
    exp5_root = next(
        root
        for root in snapshot.roots
        if root.condition.experiment_id == "exp5_real_ai_model_endpoint_comparison"
    )
    assert exp5_root.endpoint_controls.provider_config_id == "siliconflow"
    assert exp5_root.endpoint_controls.model_entry_id == exp5_root.condition.model_entry_id
    assert exp5_root.endpoint_controls.provider_model_id == exp5_root.condition.provider_model_id
    assert exp5_root.endpoint_controls.max_provider_attempts == 1
    assert exp5_root.endpoint_controls.max_tokens == 32_768
    assert exp5_root.endpoint_controls.timeout_seconds == 600


def test_formal_root_preparation_uses_runtime_adapter_and_freezes_exact_request(
    formal_inputs,
    formal_snapshot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _plans, catalog, _budget, ai_api_configs, _output_root = formal_inputs
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    root = formal_snapshot.roots[0]

    records = freeze_formal_root_prepared_requests(
        root=root,
        catalog_manifest=catalog,
        ai_api_configs=ai_api_configs,
        planning_artifact_root=tmp_path / "single-root-plan",
    )

    assert tuple(record.planned_ai_unit_id for record in records) == (
        root.planned_ai_unit_ids
    )
    assert all(record.condition is root.condition for record in records)
    assert all(record.binding is root.binding for record in records)
    assert all(record.sample_slot_index == root.repeat_id for record in records)
    assert all(record.base_replacement_slot == 0 for record in records)
    assert all(record.replacement_slot_ids == (0,) for record in records)
    assert all(
        record.replacement_policy_id == "formal_attempt_budget.v1"
        for record in records
    )
    assert all(record.prepared_request.body_bytes for record in records)
    assert all(record.body_digest == record.prepared_request.body_digest for record in records)
    assert all(
        record.inference_request_digest
        == record.prepared_request.inference_request_digest
        for record in records
    )
    assert all(
        record.source_provider_config_digest
        == root.endpoint_controls.source_provider_config_digest
        for record in records
    )
    assert all(
        record.prepared_request.provider_config_digest
        == record.prepared_execution_config_digest
        for record in records
    )
    assert all(
        record.prepared_request.entry_id == root.endpoint_controls.model_entry_id
        for record in records
    )
    assert all(
        record.request_max_tokens == root.endpoint_controls.max_tokens
        for record in records
    )
    assert all(record.request_timeout_seconds == 600 for record in records)
    assert all(record.provider_calls_made == 0 for record in records)
    assert os.environ.get("DEEPSEEK_API_KEY") is None
    assert os.environ.get("SILICONFLOW_API_KEY") is None


@pytest.mark.parametrize("domain", ("factorization", "lean_proof"))
def test_formal_runtime_task_identity_persists_and_loads_for_each_domain(
    domain: str,
    formal_inputs,
    formal_snapshot,
    tmp_path: Path,
) -> None:
    _plans, catalog, _budget, ai_api_configs, _output_root = formal_inputs
    root = next(
        item
        for item in formal_snapshot.roots
        if item.condition.domain == domain and item.repeat_id == 0
    )
    records = freeze_formal_root_prepared_requests(
        root=root,
        catalog_manifest=catalog,
        ai_api_configs=ai_api_configs,
        planning_artifact_root=tmp_path / f"{domain}-runtime-task-plan",
    )
    expected_runtime_task_id = contracts_module.formal_runtime_task_id(
        domain,
        root.case_id,
    )
    for record in records:
        prepared = record.prepared_request
        assert prepared.case_id == expected_runtime_task_id
        assert prepared.planned_ai_unit_id == record.planned_ai_unit_id
        assert prepared.sample_slot_index == record.sample_slot_index
        assert prepared.replacement_slot == record.base_replacement_slot
        assert (
            prepared.provider_config_digest
            == record.prepared_execution_config_digest
        )
        assert prepared.entry_id == record.model_entry_id
        assert prepared.configured_model == record.provider_model_id
        assert prepared.plugin_id == root.plugin_id
        assert prepared.plugin_version == root.plugin_version
    compact_snapshot, compact_inventory, _coverage = _compact_formal_authority(
        formal_snapshot,
        roots=(root,),
        records=records,
    )
    inventory_root = tmp_path / f"{domain}-runtime-task-inventory"

    manifest = formal_plan_module.persist_formal_prepared_request_inventory(
        inventory=compact_inventory,
        snapshot=compact_snapshot,
        output_root=inventory_root,
    )
    loaded = formal_plan_module.load_formal_prepared_request_inventory(
        output_root=inventory_root,
        snapshot=compact_snapshot,
        expected_manifest=manifest,
    )

    assert loaded == compact_inventory
    assert loaded.inventory_digest == compact_inventory.inventory_digest


@pytest.mark.parametrize("case_identity_kind", ("catalog", "wrong_domain"))
def test_formal_runtime_task_identity_rejects_resigned_wrong_case_identity(
    case_identity_kind: str,
    formal_inputs,
    formal_snapshot,
    tmp_path: Path,
) -> None:
    _plans, catalog, _budget, ai_api_configs, _output_root = formal_inputs
    root = next(
        item
        for item in formal_snapshot.roots
        if item.condition.domain == "factorization" and item.repeat_id == 0
    )
    records = freeze_formal_root_prepared_requests(
        root=root,
        catalog_manifest=catalog,
        ai_api_configs=ai_api_configs,
        planning_artifact_root=tmp_path / f"resigned-{case_identity_kind}-plan",
    )
    original = records[0].prepared_request
    drifted_case_id = (
        root.case_id
        if case_identity_kind == "catalog"
        else contracts_module.formal_runtime_task_id("lean_proof", root.case_id)
    )
    resigned = PreparedOutboundRequestFactory.prepare(
        body_obj=dict(original.body_obj),
        base_url=original.normalized_absolute_endpoint,
        endpoint="",
        provider_config_digest=original.provider_config_digest,
        entry_id=original.entry_id,
        configured_model=original.configured_model,
        effective_controls_digest=original.effective_controls_digest,
        plugin_id=original.plugin_id,
        plugin_version=original.plugin_version,
        prompt_profile_id=original.prompt_profile_id,
        prompt_serialization_schema=original.prompt_serialization_schema,
        body_serialization_schema=original.body_serialization_schema,
        case_id=drifted_case_id,
        planned_ai_unit_id=original.planned_ai_unit_id,
        sample_slot_index=original.sample_slot_index,
        replacement_slot=original.replacement_slot,
    )
    drifted_records = (replace(records[0], prepared_request=resigned), *records[1:])
    compact_snapshot, compact_inventory, _coverage = _compact_formal_authority(
        formal_snapshot,
        roots=(root,),
        records=drifted_records,
    )

    with pytest.raises(ValueError, match="nested identity drift"):
        formal_plan_module.persist_formal_prepared_request_inventory(
            inventory=compact_inventory,
            snapshot=compact_snapshot,
            output_root=tmp_path / f"resigned-{case_identity_kind}-inventory",
        )


def test_formal_root_preparation_is_deterministic_across_planning_roots(
    formal_inputs,
    formal_snapshot,
    tmp_path: Path,
) -> None:
    _plans, catalog, _budget, ai_api_configs, _output_root = formal_inputs
    root = formal_snapshot.roots[0]

    first = freeze_formal_root_prepared_requests(
        root=root,
        catalog_manifest=catalog,
        ai_api_configs=ai_api_configs,
        planning_artifact_root=tmp_path / "deterministic-a",
    )
    second = freeze_formal_root_prepared_requests(
        root=root,
        catalog_manifest=catalog,
        ai_api_configs=ai_api_configs,
        planning_artifact_root=tmp_path / "deterministic-b",
    )

    assert tuple(item.request_identity_digest for item in first) == tuple(
        item.request_identity_digest for item in second
    )
    assert tuple(item.prepared_request for item in first) == tuple(
        item.prepared_request for item in second
    )


@pytest.mark.parametrize("domain", ("factorization", "lean_proof"))
def test_transient_planning_artifact_store_has_exact_durable_store_parity(
    domain: str,
    tmp_path: Path,
) -> None:
    """Planning-only storage must preserve the adapters' exact artifact contract."""

    transient = formal_plan_module._TransientPlanningArtifactStore()
    durable = ArtifactStore(tmp_path / f"durable-{domain}")
    payload = {
        "domain": domain,
        "fixture": "small_planning_artifact",
        "ordered_values": [1, 2, 3],
    }
    save_kwargs = {
        "artifact_id": f"planning:{domain}:payload",
        "artifact_type": "PlanningFixture",
        "artifact_schema_id": f"tokenshare.{domain}.planning_fixture",
        "artifact_schema_version": "v1",
        "source": {"kind": "focused_parity", "domain": domain},
        "metadata": {"planning_only": True},
        "created_at": "2026-08-16T00:00:00Z",
    }

    durable_ref = durable.save_json(payload, **save_kwargs)
    transient_ref = transient.save_json(payload, **save_kwargs)
    durable_bytes = durable.read_bytes(durable_ref)
    transient_bytes = transient.read_bytes(transient_ref)

    assert transient_ref == durable_ref
    assert transient_ref.to_dict() == durable_ref.to_dict()
    assert transient_bytes == durable_bytes
    assert transient_ref.content_hash == durable_ref.content_hash
    assert transient_ref.size_bytes == len(transient_bytes)
    assert transient.verify(transient_ref)

    transient.release()
    assert transient.retained_artifact_count == 0
    with pytest.raises(RuntimeError, match="released"):
        transient.read_bytes(transient_ref)


def test_small_formal_inventory_uses_real_adapters_and_releases_each_transient_store(
    small_formal_preparation_inputs,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production planning stays real while durable I/O and provider seams stay closed."""

    catalog, ai_api_configs, small_snapshot = small_formal_preparation_inputs
    roots = small_snapshot.roots
    adapter_calls = {"factorization": 0, "lean_proof": 0, "outbound": 0}
    real_factor_plan_units = FactorizationRuntimeAdapter.plan_units
    real_lean_plan_units = LeanRuntimeAdapter.plan_units
    real_prepare_outbound = formal_plan_module.prepare_ai_api_outbound_request
    real_transient_store = formal_plan_module._TransientPlanningArtifactStore
    stores = []

    def _factor_plan_units(self, *args, **kwargs):
        adapter_calls["factorization"] += 1
        return real_factor_plan_units(self, *args, **kwargs)

    def _lean_plan_units(self, *args, **kwargs):
        adapter_calls["lean_proof"] += 1
        return real_lean_plan_units(self, *args, **kwargs)

    def _prepare_outbound(**kwargs):
        adapter_calls["outbound"] += 1
        return real_prepare_outbound(**kwargs)

    class _TrackingTransientStore(real_transient_store):
        def __init__(self) -> None:
            super().__init__()
            stores.append(self)

    def _durable_commit_bomb(*_args, **_kwargs):
        raise AssertionError("planning inventory must not durably commit artifacts")

    def _fsync_bomb(*_args, **_kwargs):
        raise AssertionError("planning inventory must not fsync artifacts")

    monkeypatch.setattr(
        FactorizationRuntimeAdapter,
        "plan_units",
        _factor_plan_units,
    )
    monkeypatch.setattr(LeanRuntimeAdapter, "plan_units", _lean_plan_units)
    monkeypatch.setattr(
        formal_plan_module,
        "prepare_ai_api_outbound_request",
        _prepare_outbound,
    )
    monkeypatch.setattr(
        formal_plan_module,
        "_TransientPlanningArtifactStore",
        _TrackingTransientStore,
    )
    monkeypatch.setattr(ArtifactStore, "_commit_bytes", _durable_commit_bomb)
    monkeypatch.setattr(formal_plan_module.os, "fsync", _fsync_bomb)

    inventory = freeze_paper_formal_prepared_request_inventory(
        snapshot=small_snapshot,
        catalog_manifest=catalog,
        ai_api_configs=ai_api_configs,
        planning_artifact_root=tmp_path / "must-remain-absent",
    )

    assert inventory.record_count == small_snapshot.first_attempt_ai_unit_count
    assert adapter_calls["factorization"] == 1
    assert adapter_calls["lean_proof"] == 1
    assert adapter_calls["outbound"] == inventory.record_count
    assert len(stores) == len(
        {
            formal_plan_module._formal_prepared_template_cache_key(root)
            for root in roots
        }
    )
    assert all(store.released for store in stores)
    assert all(store.retained_artifact_count == 0 for store in stores)
    assert not (tmp_path / "must-remain-absent").exists()
    assert inventory.provider_calls_made == 0


@pytest.mark.parametrize("domain", ("factorization", "lean_proof"))
def test_formal_replacement_requests_match_direct_runtime_for_factor_and_lean(
    domain: str,
    formal_inputs,
    formal_snapshot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _plans, catalog, _budget, ai_api_configs, _output_root = formal_inputs
    root = next(
        item
        for item in formal_snapshot.roots
        if item.condition.experiment_id == "exp3_real_ai_fault_recovery"
        and item.condition.domain == domain
        and item.condition.repeat_id == 0
    )
    records = freeze_formal_root_prepared_requests(
        root=root,
        catalog_manifest=catalog,
        ai_api_configs=ai_api_configs,
        planning_artifact_root=tmp_path / f"{domain}-base",
    )
    bound_plugins: list[str] = []
    real_bind = formal_plan_module.bind_trace_execution_request

    def _capture_bind(request, binding):
        bound_plugins.append(str(request.plugin["plugin_id"]))
        return real_bind(request, binding)

    monkeypatch.setattr(
        formal_plan_module,
        "bind_trace_execution_request",
        _capture_bind,
    )
    prepared = freeze_formal_root_prepared_replacement_requests(
        root=root,
        base_records=records,
        catalog_manifest=catalog,
        ai_api_configs=ai_api_configs,
        planning_artifact_root=tmp_path / f"{domain}-replacement",
    )

    by_unit = {
        record.planned_ai_unit_id: tuple(
            item
            for item in prepared
            if item.planned_ai_unit_id == record.planned_ai_unit_id
        )
        for record in records
    }
    assert set(bound_plugins) == {root.plugin_id}
    for record in records:
        values = by_unit[record.planned_ai_unit_id]
        assert tuple(item.replacement_slot for item in values) == (
            record.replacement_slot_ids
        )
        assert values[0] == record.prepared_request
        assert all(item.body_bytes == values[0].body_bytes for item in values)
        assert all(item.body_digest == values[0].body_digest for item in values)
        assert all(
            item.provider_config_digest == values[0].provider_config_digest
            for item in values
        )
        assert len({item.inference_request_digest for item in values}) == len(values)


def test_public_representative_acquisition_rejects_compact_noncanonical_snapshot(
    formal_inputs,
    formal_snapshot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plans, catalog, budget, ai_api_configs, _output_root = formal_inputs
    root = next(
        item
        for item in formal_snapshot.roots
        if item.condition.experiment_id == "exp2_real_ai_scalability"
        and item.repeat_id == 0
    )
    records = freeze_formal_root_prepared_requests(
        root=root,
        catalog_manifest=catalog,
        ai_api_configs=ai_api_configs,
        planning_artifact_root=tmp_path / "compact-public-base",
    )
    compact_snapshot, compact_inventory, compact_coverage = _compact_formal_authority(
        formal_snapshot,
        roots=(root,),
        records=records,
    )
    monkeypatch.setattr(
        formal_plan_module,
        "freeze_paper_formal_plan_snapshot",
        lambda **_kwargs: formal_snapshot,
    )

    with pytest.raises(ValueError, match="canonical full snapshot"):
        build_representative_unified_acquisition_plan(
            snapshot=compact_snapshot,
            prepared_inventory=compact_inventory,
            coverage=compact_coverage,
            dispatch_plans=plans,
            catalog_manifest=catalog,
            budget=budget,
            ai_api_configs=ai_api_configs,
            planning_artifact_root=tmp_path / "compact-public-acquisition",
            api_key_env_by_provider_family={"deepseek": "DEEPSEEK_API_KEY"},
            frozen_pricing_by_provider_family={
                "deepseek": FrozenPricing(
                    currency="CNY",
                    input_per_million_tokens=Decimal("3.0"),
                    output_per_million_tokens=Decimal("6.0"),
                )
            },
            requested_at="2026-08-09T00:00:00Z",
        )


def test_public_representative_acquisition_rebuilds_and_audits_full_authority(
    formal_inputs,
    formal_snapshot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plans, catalog, budget, ai_api_configs, _output_root = formal_inputs
    supplied_coverage = derive_paper_formal_representative_coverage(
        snapshot=formal_snapshot,
        dispatch_plans=plans,
        catalog_manifest=catalog,
        repeat_ids=(0,),
    )
    canonical_snapshot = _clone_formal_snapshot(formal_snapshot)
    inventory = FormalPreparedRequestInventory(
        records=(),
        record_count=formal_snapshot.first_attempt_ai_unit_count,
        unique_inference_request_count=0,
        provider_calls_made=0,
        source_snapshot_digest=formal_snapshot.snapshot_digest,
    )
    calls: dict[str, object] = {}
    sentinel = object()
    validated_token = object()

    def _freeze(**kwargs):
        calls["freeze"] = kwargs
        return canonical_snapshot

    def _validate_inventory(**kwargs):
        calls["inventory"] = kwargs

    def _build(**kwargs):
        calls["build"] = kwargs
        return sentinel

    def _mint(**kwargs):
        calls["mint"] = kwargs
        return validated_token

    monkeypatch.setattr(formal_plan_module, "freeze_paper_formal_plan_snapshot", _freeze)
    monkeypatch.setattr(
        formal_plan_module,
        "validate_formal_prepared_request_inventory",
        _validate_inventory,
    )
    monkeypatch.setattr(
        response_bank_module,
        "_build_representative_unified_acquisition_plan_from_validated_authority",
        _build,
    )
    monkeypatch.setattr(
        response_bank_module,
        "_mint_validated_representative_authority",
        _mint,
    )

    result = build_representative_unified_acquisition_plan(
        snapshot=formal_snapshot,
        prepared_inventory=inventory,
        coverage=supplied_coverage,
        dispatch_plans=plans,
        catalog_manifest=catalog,
        budget=budget,
        ai_api_configs=ai_api_configs,
        planning_artifact_root=tmp_path / "public-full-authority",
        api_key_env_by_provider_family={},
        frozen_pricing_by_provider_family={},
        requested_at="2026-08-09T00:00:00Z",
    )

    assert result is sentinel
    assert calls["freeze"]["dispatch_plans"] == plans
    assert calls["freeze"]["budget"] is budget
    assert calls["inventory"]["inventory"] is inventory
    assert calls["inventory"]["snapshot"] is canonical_snapshot
    assert calls["mint"]["snapshot"] is canonical_snapshot
    assert calls["mint"]["prepared_inventory"] is inventory
    assert calls["mint"]["coverage"].source_snapshot is canonical_snapshot
    assert calls["build"]["validated_authority"] is validated_token


def test_atomic_representative_authority_freezes_full_inventory_once(
    formal_inputs,
    formal_snapshot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plans, catalog, budget, ai_api_configs, _output_root = formal_inputs
    root = next(
        item
        for item in formal_snapshot.roots
        if item.condition.experiment_id == "exp3_real_ai_fault_recovery"
        and item.condition.domain == "factorization"
        and item.repeat_id == 0
    )
    records = freeze_formal_root_prepared_requests(
        root=root,
        catalog_manifest=catalog,
        ai_api_configs=ai_api_configs,
        planning_artifact_root=tmp_path / "atomic-base",
    )
    snapshot, inventory, coverage = _compact_formal_authority(
        formal_snapshot,
        roots=(root,),
        records=records,
    )
    calls = {"freeze_inventory": 0, "independent_validate": 0}
    monkeypatch.setattr(
        formal_plan_module,
        "freeze_paper_formal_plan_snapshot",
        lambda **_kwargs: snapshot,
    )

    def _freeze_inventory(**_kwargs):
        calls["freeze_inventory"] += 1
        return inventory

    def _reject_independent_validate(**_kwargs):
        calls["independent_validate"] += 1
        raise AssertionError("atomic factory must not rebuild full inventory")

    monkeypatch.setattr(
        formal_plan_module,
        "freeze_paper_formal_prepared_request_inventory",
        _freeze_inventory,
    )
    monkeypatch.setattr(
        formal_plan_module,
        "derive_paper_formal_representative_coverage",
        lambda **_kwargs: coverage,
    )
    monkeypatch.setattr(
        formal_plan_module,
        "validate_formal_prepared_request_inventory",
        _reject_independent_validate,
    )

    authority = prepare_representative_acquisition_authority(
        dispatch_plans=plans,
        catalog_manifest=catalog,
        budget=budget,
        ai_api_configs=ai_api_configs,
        planning_artifact_root=tmp_path / "atomic-authority",
        api_key_env_by_provider_family={"deepseek": "DEEPSEEK_API_KEY"},
        frozen_pricing_by_provider_family={
            "deepseek": FrozenPricing(
                currency="CNY",
                input_per_million_tokens=Decimal("3.0"),
                output_per_million_tokens=Decimal("6.0"),
            )
        },
        requested_at="2026-08-09T00:00:00Z",
    )

    assert type(authority) is RepresentativeAcquisitionAuthority
    assert authority.snapshot is snapshot
    assert authority.prepared_inventory is inventory
    assert authority.coverage is coverage
    assert authority.coverage.source_snapshot is authority.snapshot
    assert authority.plan.source_snapshot_digest == snapshot.snapshot_digest
    assert authority.plan.source_prepared_inventory_digest == inventory.inventory_digest
    assert authority.plan.coverage_digest == coverage.coverage_digest
    assert authority.provider_calls_made == 0
    assert authority.validation_digest.startswith("sha256:")
    assert calls == {"freeze_inventory": 1, "independent_validate": 0}
    with pytest.raises(ValueError, match="authority digest mismatch"):
        replace(authority, validation_digest="")
    with pytest.raises(TypeError, match="validated authority token is forged"):
        response_bank_module._ValidatedRepresentativeAuthority(
            snapshot=snapshot,
            prepared_inventory=inventory,
            coverage=coverage,
            seal=object(),
        )


def test_semantic_builder_rejects_forged_validated_authority_token(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="validated authority token"):
        response_bank_module._build_representative_unified_acquisition_plan_from_validated_authority(
            validated_authority=object(),
            catalog_manifest=object(),
            ai_api_configs={},
            planning_artifact_root=tmp_path,
            api_key_env_by_provider_family={},
            frozen_pricing_by_provider_family={},
            requested_at="2026-08-09T00:00:00Z",
        )


def test_representative_paid_acquisition_call_graph_uses_exp1_only_selector() -> None:
    """Static boundary: the paid builder must route through the Exp1-only seam."""

    import inspect

    source = inspect.getsource(
        response_bank_module._build_representative_unified_acquisition_plan_from_validated_authority
    )

    assert "select_exp1_source_roots(" in source
    assert "full_roots=snapshot.roots" in source
    assert "selected_roots=coverage.roots" in source
    assert '"exp2_real_ai_scalability"' not in source
    assert '"exp3_real_ai_fault_recovery"' not in source
    assert '"exp4_real_ai_protocol_ablation"' not in source


@pytest.mark.parametrize("root_scope", ("selected", "unselected"))
def test_public_representative_acquisition_rejects_full_snapshot_control_tamper(
    root_scope: str,
    formal_inputs,
    formal_snapshot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plans, catalog, budget, ai_api_configs, _output_root = formal_inputs
    canonical_coverage = derive_paper_formal_representative_coverage(
        snapshot=formal_snapshot,
        dispatch_plans=plans,
        catalog_manifest=catalog,
        repeat_ids=(0,),
    )
    selected_ids = {id(root) for root in canonical_coverage.roots}
    root_index = next(
        index
        for index, root in enumerate(formal_snapshot.roots)
        if (id(root) in selected_ids) == (root_scope == "selected")
    )
    root = formal_snapshot.roots[root_index]
    drifted_root = replace(
        root,
        endpoint_controls=replace(
            root.endpoint_controls,
            request_controls_digest="sha256:" + "f" * 64,
        ),
    )
    drifted_roots = list(formal_snapshot.roots)
    drifted_roots[root_index] = drifted_root
    drifted_snapshot = replace(formal_snapshot, roots=tuple(drifted_roots))
    drifted_coverage = derive_paper_formal_representative_coverage(
        snapshot=drifted_snapshot,
        dispatch_plans=plans,
        catalog_manifest=catalog,
        repeat_ids=(0,),
    )
    inventory = FormalPreparedRequestInventory(
        records=(),
        record_count=drifted_snapshot.first_attempt_ai_unit_count,
        unique_inference_request_count=0,
        provider_calls_made=0,
        source_snapshot_digest=drifted_snapshot.snapshot_digest,
    )
    monkeypatch.setattr(
        formal_plan_module,
        "freeze_paper_formal_plan_snapshot",
        lambda **_kwargs: formal_snapshot,
    )

    with pytest.raises(ValueError, match="canonical full snapshot"):
        build_representative_unified_acquisition_plan(
            snapshot=drifted_snapshot,
            prepared_inventory=inventory,
            coverage=drifted_coverage,
            dispatch_plans=plans,
            catalog_manifest=catalog,
            budget=budget,
            ai_api_configs=ai_api_configs,
            planning_artifact_root=tmp_path / f"public-{root_scope}-tamper",
            api_key_env_by_provider_family={},
            frozen_pricing_by_provider_family={},
            requested_at="2026-08-09T00:00:00Z",
        )


def _compact_formal_authority(full_snapshot, *, roots, records):
    condition_ids = tuple(dict.fromkeys(root.condition.condition_id for root in roots))
    condition_by_id = {
        item.condition.condition_id: item for item in full_snapshot.conditions
    }
    conditions = tuple(condition_by_id[condition_id] for condition_id in condition_ids)
    ordered_roots = tuple(
        root
        for condition_id in condition_ids
        for root in roots
        if root.condition.condition_id == condition_id
    )
    snapshot = FormalPlanSnapshot(
        conditions=conditions,
        roots=ordered_roots,
        condition_count=len(conditions),
        root_run_count=len(ordered_roots),
        first_attempt_ai_unit_count=sum(
            len(root.planned_ai_unit_ids) for root in ordered_roots
        ),
        provider_calls_made=0,
        budget_digest=full_snapshot.budget_digest,
    )
    by_key = {
        (
            record.condition.condition_id,
            record.case_id,
            record.planned_ai_unit_id,
        ): record
        for record in records
    }
    ordered_records = tuple(
        by_key[(root.condition.condition_id, root.case_id, planned_ai_unit_id)]
        for root in ordered_roots
        for planned_ai_unit_id in root.planned_ai_unit_ids
    )
    inventory = FormalPreparedRequestInventory(
        records=ordered_records,
        record_count=len(ordered_records),
        unique_inference_request_count=len(
            {record.inference_request_digest for record in ordered_records}
        ),
        provider_calls_made=0,
        source_snapshot_digest=snapshot.snapshot_digest,
    )
    root_filter = {
        condition_id: tuple(
            root.case_id
            for root in ordered_roots
            if root.condition.condition_id == condition_id
        )
        for condition_id in condition_ids
    }
    coverage = FormalRepresentativeCoverage(
        conditions=tuple(item.condition for item in conditions),
        bindings=tuple(item.binding for item in conditions),
        roots=ordered_roots,
        root_case_filter=root_filter,
        source_snapshot=snapshot,
        source_snapshot_digest=snapshot.snapshot_digest,
        condition_count=len(conditions),
        root_run_count=len(ordered_roots),
    )
    return snapshot, inventory, coverage


def _clone_formal_snapshot(snapshot):
    return replace(
        snapshot,
        conditions=tuple(replace(item) for item in snapshot.conditions),
        roots=tuple(replace(root) for root in snapshot.roots),
    )


def test_formal_root_preparation_rejects_snapshot_endpoint_control_tamper(
    formal_inputs,
    formal_snapshot,
    tmp_path: Path,
) -> None:
    _plans, catalog, _budget, ai_api_configs, _output_root = formal_inputs
    root = formal_snapshot.roots[0]
    drifted = replace(
        root,
        endpoint_controls=replace(
            root.endpoint_controls,
            source_provider_config_digest="sha256:" + "f" * 64,
        ),
    )

    with pytest.raises(ValueError, match="formal root endpoint controls drift"):
        freeze_formal_root_prepared_requests(
            root=drifted,
            catalog_manifest=catalog,
            ai_api_configs=ai_api_configs,
            planning_artifact_root=tmp_path / "tampered-endpoint",
        )


def test_formal_prepared_record_digest_revalidates_nested_request_body(
    formal_inputs,
    formal_snapshot,
    tmp_path: Path,
) -> None:
    _plans, catalog, _budget, ai_api_configs, _output_root = formal_inputs
    record = freeze_formal_root_prepared_requests(
        root=formal_snapshot.roots[0],
        catalog_manifest=catalog,
        ai_api_configs=ai_api_configs,
        planning_artifact_root=tmp_path / "nested-request-tamper",
    )[0]
    drifted_body = dict(record.prepared_request.body_obj)
    drifted_body["model"] = "drifted-model"
    drifted = replace(
        record,
        prepared_request=replace(
            record.prepared_request,
            body_obj=drifted_body,
        ),
    )

    with pytest.raises(ValueError, match="prepared request body bytes mismatch"):
        _ = drifted.request_identity_digest


def test_formal_prepared_record_validator_rejects_top_level_provider_model_tamper(
    single_root_prepared_inventory,
    formal_inputs,
    tmp_path: Path,
) -> None:
    root, records, _snapshot, _inventory, ai_api_configs = (
        single_root_prepared_inventory
    )

    with pytest.raises(ValueError, match="provider_model_id"):
        formal_plan_module.validate_formal_prepared_request_record(
            record=replace(records[0], provider_model_id="drifted-model"),
            root=root,
            catalog_manifest=formal_inputs[1],
            ai_api_configs=ai_api_configs,
            planning_artifact_root=tmp_path / "audit-provider-model",
        )


def test_formal_prepared_record_validator_rejects_in_place_identity_mapping_tamper(
    single_root_prepared_inventory,
    formal_inputs,
    tmp_path: Path,
) -> None:
    root, records, _snapshot, _inventory, ai_api_configs = (
        single_root_prepared_inventory
    )
    drifted_identity = dict(records[0].provider_request_identity)
    drifted = replace(records[0], provider_request_identity=drifted_identity)
    drifted_identity["configured_model"] = "drifted-model"

    with pytest.raises(ValueError, match="provider request identity"):
        formal_plan_module.validate_formal_prepared_request_record(
            record=drifted,
            root=root,
            catalog_manifest=formal_inputs[1],
            ai_api_configs=ai_api_configs,
            planning_artifact_root=tmp_path / "audit-provider-identity",
        )


def test_formal_prepared_record_validator_rejects_coherently_resigned_body_tamper(
    single_root_prepared_inventory,
    formal_inputs,
    tmp_path: Path,
) -> None:
    root, records, _snapshot, _inventory, ai_api_configs = (
        single_root_prepared_inventory
    )
    record = records[0]
    original = record.prepared_request
    drifted_body = deepcopy(original.body_obj)
    drifted_body["messages"][0]["content"] += "\ncoherent-drift"
    resigned = PreparedOutboundRequestFactory.prepare(
        body_obj=drifted_body,
        base_url=original.normalized_absolute_endpoint,
        endpoint="",
        provider_config_digest=original.provider_config_digest,
        entry_id=original.entry_id,
        configured_model=original.configured_model,
        effective_controls_digest=original.effective_controls_digest,
        plugin_id=original.plugin_id,
        plugin_version=original.plugin_version,
        prompt_profile_id=original.prompt_profile_id,
        prompt_serialization_schema=original.prompt_serialization_schema,
        body_serialization_schema=original.body_serialization_schema,
        case_id=original.case_id,
        planned_ai_unit_id=original.planned_ai_unit_id,
        sample_slot_index=original.sample_slot_index,
        replacement_slot=original.replacement_slot,
    )
    drifted = replace(
        record,
        prepared_request=resigned,
        prompt_profile_digest=digest_json(
            {
                "body_digest": resigned.body_digest,
                "prompt_profile_id": resigned.prompt_profile_id,
                "prompt_serialization_schema": resigned.prompt_serialization_schema,
            }
        ),
    )

    with pytest.raises(ValueError, match="independent runtime template"):
        formal_plan_module.validate_formal_prepared_request_record(
            record=drifted,
            root=root,
            catalog_manifest=formal_inputs[1],
            ai_api_configs=ai_api_configs,
            planning_artifact_root=tmp_path / "audit-coherent-body",
        )


@pytest.mark.parametrize("mutation", ("count", "record", "source_digest"))
def test_formal_prepared_inventory_validator_rejects_internal_tamper(
    single_root_prepared_inventory,
    formal_inputs,
    tmp_path: Path,
    mutation: str,
) -> None:
    _root, records, snapshot, inventory, ai_api_configs = (
        single_root_prepared_inventory
    )
    if mutation == "count":
        drifted = replace(inventory, record_count=inventory.record_count + 1)
    elif mutation == "record":
        drifted = replace(
            inventory,
            records=(
                replace(records[0], provider_family="drifted-provider"),
                *records[1:],
            ),
        )
    else:
        drifted = replace(
            inventory,
            source_snapshot_digest="sha256:" + "f" * 64,
        )

    with pytest.raises(ValueError, match="formal prepared inventory"):
        formal_plan_module.validate_formal_prepared_request_inventory(
            inventory=drifted,
            snapshot=snapshot,
            catalog_manifest=formal_inputs[1],
            ai_api_configs=ai_api_configs,
            planning_artifact_root=tmp_path / f"audit-inventory-{mutation}",
        )


def test_full_prepared_inventory_covers_every_first_attempt_without_provider_calls(
    formal_inputs,
    formal_snapshot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _plans, catalog, _budget, ai_api_configs, _output_root = formal_inputs
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    inventory = freeze_paper_formal_prepared_request_inventory(
        snapshot=formal_snapshot,
        catalog_manifest=catalog,
        ai_api_configs=ai_api_configs,
        planning_artifact_root=tmp_path / "full-inventory-plan",
    )

    assert inventory.record_count == formal_snapshot.first_attempt_ai_unit_count == 40_520
    assert len(inventory.records) == inventory.record_count
    assert inventory.provider_calls_made == 0
    assert inventory.inventory_digest.startswith("sha256:")
    assert inventory.unique_inference_request_count <= inventory.record_count
    exp5_records = tuple(
        record
        for record in inventory.records
        if record.condition.experiment_id
        == "exp5_real_ai_model_endpoint_comparison"
    )
    assert len(exp5_records) == 4_992
    assert {
        record.prepared_request.configured_model for record in exp5_records
    } == {
        "zai-org/GLM-5.2",
        "Qwen/Qwen3-14B",
        "MiniMaxAI/MiniMax-M2.5",
        "Pro/deepseek-ai/DeepSeek-V3",
    }
    exp3_worker_death = next(
        record
        for record in inventory.records
        if record.condition.experiment_id == "exp3_real_ai_fault_recovery"
        and record.condition.fault_type == "worker_death"
    )
    assert exp3_worker_death.replacement_slot_ids == (0, 1, 2, 3, 4)
    assert exp3_worker_death.replacement_policy_id == "formal_attempt_budget.v1"
    assert "smoke" not in exp3_worker_death.replacement_policy_id
    assert os.environ.get("DEEPSEEK_API_KEY") is None
    assert os.environ.get("SILICONFLOW_API_KEY") is None


def test_formal_suite_plan_public_validator_returns_no_internal_bound_plan(
    formal_inputs,
) -> None:
    plans, catalog, budget, ai_api_configs, output_root = formal_inputs

    assert validate_paper_formal_suite_plan(
        dispatch_plans=plans,
        catalog_manifest=catalog,
        budget=budget,
        output_root=output_root,
        ai_api_configs=ai_api_configs,
        hard_limits={
            "max_total_provider_attempts": budget.max_provider_attempts,
            "max_total_tokens": budget.token_upper_bound,
            "max_cost_estimate": budget.cost_upper_bound,
        },
    ) is None


@pytest.mark.parametrize(
    ("experiment_index", "omitted_axis"),
    (
        (0, "first_condition"),
        (1, "worker_slice"),
        (4, "endpoint_condition_slice"),
    ),
)
def test_formal_snapshot_rejects_self_consistent_partial_formal_plan(
    formal_inputs,
    experiment_index: int,
    omitted_axis: str,
) -> None:
    plans, catalog, budget, ai_api_configs, output_root = formal_inputs
    target = plans[experiment_index]
    if omitted_axis == "first_condition":
        omitted_ids = {target.conditions[0].condition_id}
    elif omitted_axis == "worker_slice":
        omitted_worker_count = target.conditions[0].worker_count
        omitted_ids = {
            condition.condition_id
            for condition in target.conditions
            if condition.worker_count == omitted_worker_count
        }
    else:
        omitted_ids = {target.conditions[0].condition_id}
    partial_target = replace(
        target,
        conditions=tuple(
            condition
            for condition in target.conditions
            if condition.condition_id not in omitted_ids
        ),
        condition_selection_bindings=tuple(
            binding
            for binding in target.condition_selection_bindings
            if binding.condition_id not in omitted_ids
        ),
    )
    partial_plans = (
        *plans[:experiment_index],
        partial_target,
        *plans[experiment_index + 1 :],
    )
    partial_budget = _replan_budget(
        plans=partial_plans,
        catalog=catalog,
        source_budget=budget,
    )

    with pytest.raises(ValueError, match="canonical formal plan mismatch"):
        freeze_paper_formal_plan_snapshot(
            dispatch_plans=partial_plans,
            catalog_manifest=catalog,
            budget=partial_budget,
            ai_api_configs=ai_api_configs,
            output_root=output_root,
        )


def test_formal_budget_planner_rejects_missing_exp5_endpoint_member(
    formal_inputs,
) -> None:
    plans, catalog, budget, _configs, _root = formal_inputs
    exp5 = plans[4]
    omitted_member_id = exp5.conditions[0].cohort_member_id
    omitted_ids = {
        condition.condition_id
        for condition in exp5.conditions
        if condition.cohort_member_id == omitted_member_id
    }
    partial_exp5 = replace(
        exp5,
        conditions=tuple(
            condition
            for condition in exp5.conditions
            if condition.condition_id not in omitted_ids
        ),
        condition_selection_bindings=tuple(
            binding
            for binding in exp5.condition_selection_bindings
            if binding.condition_id not in omitted_ids
        ),
    )

    with pytest.raises(ValueError, match="exact four-member cohort"):
        _replan_budget(
            plans=(*plans[:4], partial_exp5),
            catalog=catalog,
            source_budget=budget,
        )


@pytest.mark.parametrize("mutation", ("missing", "reordered", "duplicate"))
def test_formal_snapshot_rejects_noncanonical_experiment_collection(
    formal_inputs,
    mutation: str,
) -> None:
    plans, catalog, budget, ai_api_configs, output_root = formal_inputs
    if mutation == "missing":
        drifted = plans[:-1]
    elif mutation == "reordered":
        drifted = (plans[1], plans[0], *plans[2:])
    else:
        drifted = (*plans, plans[-1])

    with pytest.raises(ValueError, match="canonical formal experiment order mismatch"):
        freeze_paper_formal_plan_snapshot(
            dispatch_plans=drifted,
            catalog_manifest=catalog,
            budget=budget,
            ai_api_configs=ai_api_configs,
            output_root=output_root,
        )


def test_formal_snapshot_rejects_exp4_identity_derived_from_drifted_conditions(
    formal_inputs,
) -> None:
    plans, catalog, budget, ai_api_configs, output_root = formal_inputs
    exp4 = plans[3]
    drifted_conditions = tuple(
        replace(
            condition,
            reasoning_profile_id="drifted_profile",
            model_cohort_id="tokenshare.paper.drifted_baseline.v1",
            model_cohort_digest="sha256:" + "e" * 64,
            cohort_member_id="drifted_baseline_member",
            model_endpoint_identity_digest="sha256:" + "d" * 64,
        )
        for condition in exp4.conditions
    )
    selections_by_id = {
        binding.condition_id: binding.selection
        for binding in exp4.condition_selection_bindings
    }
    drifted_exp4 = replace(
        exp4,
        conditions=drifted_conditions,
        condition_selection_bindings=tuple(
            FrozenConditionSelectionBinding.from_condition(
                condition,
                selections_by_id[condition.condition_id],
            )
            for condition in drifted_conditions
        ),
    )
    drifted_plans = (*plans[:3], drifted_exp4, plans[4])
    drifted_budget = _replan_budget(
        plans=drifted_plans,
        catalog=catalog,
        source_budget=budget,
    )

    with pytest.raises(ValueError, match="canonical formal plan mismatch"):
        freeze_paper_formal_plan_snapshot(
            dispatch_plans=drifted_plans,
            catalog_manifest=catalog,
            budget=drifted_budget,
            ai_api_configs=ai_api_configs,
            output_root=output_root,
        )


def test_formal_binding_validator_rejects_condition_digest_mismatch(
    formal_inputs,
) -> None:
    plan = formal_inputs[0][0]
    condition = plan.conditions[0]
    binding = plan.condition_selection_bindings[0]
    drifted = FrozenConditionSelectionBinding(
        condition_id=binding.condition_id,
        condition_digest="sha256:" + "f" * 64,
        selection=binding.selection,
    )

    with pytest.raises(ValueError, match="condition digest mismatch"):
        validate_paper_formal_plan_bindings(
            conditions=(condition,),
            bindings=(drifted,),
            catalog_manifest=formal_inputs[1],
        )


def test_formal_binding_validator_rejects_selection_case_absent_from_catalog(
    formal_inputs,
) -> None:
    plan = formal_inputs[0][0]
    condition = plan.conditions[0]
    binding = plan.condition_selection_bindings[0]
    drifted_selection = replace(
        binding.selection,
        ordered_case_ids=("missing_formal_case",),
        expected_ai_unit_count=1,
    )
    drifted = FrozenConditionSelectionBinding.from_condition(
        condition,
        drifted_selection,
    )

    with pytest.raises(ValueError, match="absent from formal catalog"):
        validate_paper_formal_plan_bindings(
            conditions=(condition,),
            bindings=(drifted,),
            catalog_manifest=formal_inputs[1],
        )


def test_formal_binding_validator_rejects_duplicate_condition_binding(
    formal_inputs,
) -> None:
    plan = formal_inputs[0][0]

    with pytest.raises(ValueError, match="duplicate condition binding"):
        validate_paper_formal_plan_bindings(
            conditions=(plan.conditions[0],),
            bindings=(
                plan.condition_selection_bindings[0],
                plan.condition_selection_bindings[0],
            ),
            catalog_manifest=formal_inputs[1],
        )


def test_formal_binding_validator_rejects_unknown_lean_catalog_schema(
    formal_inputs,
) -> None:
    plan = formal_inputs[0][0]
    condition = next(item for item in plan.conditions if item.domain == "lean_proof")
    binding = next(
        item
        for item in plan.condition_selection_bindings
        if item.condition_id == condition.condition_id
    )
    catalog = _catalog_with_case_changes(
        formal_inputs[1],
        binding.selection.ordered_case_ids[0],
        {"schema_version": "tokenshare.paper_unknown_case.v1"},
    )

    with pytest.raises(ValueError, match="unsupported formal catalog case schema"):
        validate_paper_formal_plan_bindings(
            conditions=(condition,),
            bindings=(binding,),
            catalog_manifest=catalog,
        )


def test_formal_binding_validator_rejects_wrong_case_difficulty(
    formal_inputs,
) -> None:
    plan = formal_inputs[0][0]
    condition = next(
        item
        for item in plan.conditions
        if item.domain == "factorization" and item.paper_difficulty == "easy"
    )
    binding = next(
        item
        for item in plan.condition_selection_bindings
        if item.condition_id == condition.condition_id
    )
    catalog = _catalog_with_case_changes(
        formal_inputs[1],
        binding.selection.ordered_case_ids[0],
        {"difficulty": "hard", "paper_difficulty": "hard"},
    )

    with pytest.raises(ValueError, match="difficulty mismatch"):
        validate_paper_formal_plan_bindings(
            conditions=(condition,),
            bindings=(binding,),
            catalog_manifest=catalog,
        )


def test_formal_budget_commitments_reject_frozen_selection_order_tamper(
    formal_inputs,
) -> None:
    plans, catalog, budget, _configs, _root = formal_inputs
    quota = deepcopy(budget.quota_preflight)
    frozen = quota["budget_commitments"]["frozen_selections"][0]
    frozen["ordered_case_ids"] = list(reversed(frozen["ordered_case_ids"]))

    with pytest.raises(ValueError, match="frozen selection mismatch"):
        validate_paper_formal_budget_commitments(
            dispatch_plans=plans,
            catalog_manifest=catalog,
            budget=replace(budget, quota_preflight=quota),
        )


@pytest.mark.parametrize(
    "tamper",
    ("planned_ai_unit_ids", "split_profile_digest", "commitment_digest"),
)
def test_formal_budget_commitments_reject_ai_unit_commitment_tamper(
    formal_inputs,
    tamper: str,
) -> None:
    plans, catalog, budget, _configs, _root = formal_inputs
    quota = deepcopy(budget.quota_preflight)
    commitments = quota["budget_commitments"]["ai_unit_commitments"]
    target = next(item for item in commitments if len(item["planned_ai_unit_ids"]) > 1)
    if tamper == "planned_ai_unit_ids":
        target[tamper] = list(reversed(target[tamper]))
    else:
        target[tamper] = "sha256:" + "f" * 64

    with pytest.raises(ValueError, match="AI-unit commitment"):
        validate_paper_formal_budget_commitments(
            dispatch_plans=plans,
            catalog_manifest=catalog,
            budget=replace(budget, quota_preflight=quota),
        )


def _build_formal_inputs(tmp_path: Path):
    catalog = load_paper_catalogs(
        factorization_path="benchmarks/paper/factorization_catalog.v2.jsonl",
        lean_path="benchmarks/paper/lean_catalog.v1.jsonl",
        lean_lemma_graph_path="benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl",
    )
    baseline_profile = load_exp1_pilot_profile(
        "benchmarks/paper/exp1_minimal_pilot_profile.v3.json"
    )
    baseline_identity = baseline_profile.model_endpoint_identity.to_dict()
    baseline_binding = {
        **baseline_identity,
        "model_entry_id": baseline_identity["selected_entry_id"],
        "request_controls": dict(EXP1_FORMAL_REQUEST_CONTROLS),
    }
    exp5_configs = load_provider_config_map(
        {"siliconflow": "benchmarks/paper/exp5_siliconflow_provider_config.v3.json"}
    )
    exp5_preflight = build_model_endpoint_cohort_preflight(
        cohort=load_model_endpoint_cohort(
            "benchmarks/paper/model_comparison_cohort.v3.json"
        ),
        entry_map=load_model_entry_map(
            "benchmarks/paper/model_comparison_entry_map.v3.json"
        ),
        provider_configs=exp5_configs,
        require_smoke_evidence=False,
        smoke_evidence_bundle=None,
        pricing_freshness_as_of=EXP5_PRICING_FRESHNESS_AS_OF,
    )
    member_plans = exp5_preflight["member_plans"]
    expected_secret_reasons = {"missing_api_key_env"}
    assert exp5_preflight["ineligible_members"]
    assert all(
        set(item["blocked_reasons"]) == expected_secret_reasons
        for item in exp5_preflight["ineligible_members"]
    )
    for member_plan in member_plans.values():
        assert set(member_plan["blocked_reasons"]) == expected_secret_reasons
        member_plan["api_key_env"] = "SILICONFLOW_API_KEY"
        member_plan["status"] = "planned"
        member_plan["blocked_reasons"] = []
    exp5_preflight.update(
        {
            "status": "planned",
            "paper_eligible_possible": True,
            "blocked_reason": None,
            "ineligibility_reasons": [],
            "ineligible_members": [],
        }
    )
    readiness = build_lean_3x3_matrix_plan(catalog_manifest=catalog)
    plans = build_gate_c_dispatch_plans(
        catalog_manifest=catalog,
        lean_3x3_matrix=readiness,
        experiment_ids=EXPERIMENT_IDS,
        baseline_endpoint_binding=baseline_binding,
        model_endpoint_cohort_preflight=exp5_preflight,
        paper_suite_scale_profile=load_paper_suite_scale_profile(
            "benchmarks/paper/paper_suite_scale_profile.v1.json"
        ),
        output_root=tmp_path,
    )
    conditions = tuple(
        condition for plan in plans for condition in plan.conditions
    )
    frozen_selections = tuple(
        {
            **binding.selection.to_dict(),
            "condition_id": binding.condition_id,
            "condition_digest": binding.condition_digest,
        }
        for plan in plans
        for binding in plan.condition_selection_bindings
    )
    budget = plan_paper_suite(
        catalog_manifest=catalog,
        conditions=conditions,
        max_provider_attempts_per_ai_unit=1,
        token_upper_bound_per_provider_attempt=304_096,
        token_upper_bound_by_endpoint_identity_digest=(
            build_exp5_v3_token_ceiling_mapping(exp5_preflight)
        ),
        cost_upper_bound_per_provider_attempt=0.05,
        plan_only=True,
        lean_3x3_matrix=readiness,
        model_endpoint_cohort_preflight=exp5_preflight,
        frozen_selections=frozen_selections,
        endpoint_identity={
            "baseline": baseline_binding,
            "model_endpoint_cohort_preflight": exp5_preflight,
        },
        request_limits=dict(EXP1_FORMAL_REQUEST_CONTROLS),
    )
    ai_api_configs = {
        baseline_identity["provider_config_id"]: baseline_profile.source_provider_config,
        **exp5_configs,
        APPROVED_ENDPOINT_BINDINGS_KEY: {
            "exp5_real_ai_model_endpoint_comparison": exp5_preflight
        },
    }

    return plans, catalog, budget, ai_api_configs, tmp_path


def _catalog_with_case_changes(catalog, case_id: str, changes: dict):
    fields = (
        "factorization_cases",
        "lean_cases",
        "lean_lemma_graph_cases",
    )
    updates = {}
    found = False
    for field_name in fields:
        values = getattr(catalog, field_name)
        changed = []
        for case in values:
            if case["case_id"] == case_id:
                changed.append({**case, **changes})
                found = True
            else:
                changed.append(case)
        updates[field_name] = tuple(changed)
    assert found
    return replace(catalog, **updates)


def _replan_budget(*, plans, catalog, source_budget):
    commitments = source_budget.quota_preflight["budget_commitments"]
    endpoint_identity = commitments["endpoint_identity"]
    exp5_preflight = endpoint_identity["model_endpoint_cohort_preflight"]
    conditions = tuple(
        condition
        for plan in plans
        for condition in plan.conditions
    )
    frozen_selections = tuple(
        {
            **binding.selection.to_dict(),
            "condition_id": binding.condition_id,
            "condition_digest": binding.condition_digest,
        }
        for plan in plans
        for binding in plan.condition_selection_bindings
    )
    return plan_paper_suite(
        catalog_manifest=catalog,
        conditions=conditions,
        max_provider_attempts_per_ai_unit=1,
        token_upper_bound_per_provider_attempt=304_096,
        token_upper_bound_by_endpoint_identity_digest=(
            build_exp5_v3_token_ceiling_mapping(exp5_preflight)
        ),
        cost_upper_bound_per_provider_attempt=0.05,
        plan_only=True,
        lean_3x3_matrix=build_lean_3x3_matrix_plan(catalog_manifest=catalog),
        model_endpoint_cohort_preflight=exp5_preflight,
        frozen_selections=frozen_selections,
        endpoint_identity=endpoint_identity,
        request_limits=dict(commitments["request_limits"]),
    )
