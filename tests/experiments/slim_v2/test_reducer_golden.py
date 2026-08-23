from __future__ import annotations

import ast
from dataclasses import asdict, fields
import json
from pathlib import Path
from typing import Any

import pytest

from tokenshare.experiments.slim_v2 import reducer as reducer_module
from tokenshare.experiments.slim_v2.reducer import (
    formal_metric_occurrences,
    metric_ids,
    reduce_run,
    sample_variance,
    stratified_case_cluster_bootstrap,
    type7_quantile,
)
from tokenshare.experiments.slim_v2.schema import (
    ROOT_RESULT_SCHEMA_VERSION,
    AblationObservationV1,
    AttemptResultV1,
    ChallengeObservationV1,
    FaultObservationV1,
    RecoveryObservationV1,
    RootInventoryV1,
    RootResultV2,
)
from tokenshare.experiments.slim_v2.storage import RunStore


FIXTURES = Path(__file__).with_name("fixtures")
AUTHORITY = json.loads(
    (FIXTURES / "authority_contract.v1.json").read_text(encoding="utf-8")
)
GOLDEN = json.loads(
    (FIXTURES / "reducer_golden_run.v1.json").read_text(encoding="utf-8")
)["expected"]


def _nullable_reasons(record_type: type[Any]) -> dict[str, str]:
    reasons = {
        path: "synthetic_not_applicable"
        for path in record_type.nullable_leaf_paths()
    }
    if record_type is AttemptResultV1:
        reasons.update(
            {f"attempts[].{path}": reason for path, reason in tuple(reasons.items())}
        )
    return reasons


def _inventory(
    experiment_id: str,
    condition_id: str,
    case_id: str,
    *,
    repeat_id: int = 0,
    difficulty: str = "hard",
    worker_count: int = 10,
    mode: str | None = None,
    fault_type: str | None = None,
    dead_worker_count: int | None = None,
    kill_progress_target_ratio: float | None = None,
    challenge_plan_id: str | None = None,
    configured_model: str = "deepseek-v4-pro",
) -> RootInventoryV1:
    disabled = {
        "NO_VERIFICATION": ["verification"],
        "NO_PARSER_POLICY": ["parser_policy"],
        "NO_REQUEUE": ["requeue"],
        "NO_MERGE_GATE": ["merge_gate"],
        "NO_VERIFICATION__NO_PARSER_POLICY": ["verification", "parser_policy"],
        "NO_VERIFICATION__NO_REQUEUE": ["verification", "requeue"],
        "NO_VERIFICATION__NO_MERGE_GATE": ["verification", "merge_gate"],
        "NO_PARSER_POLICY__NO_REQUEUE": ["parser_policy", "requeue"],
        "NO_PARSER_POLICY__NO_MERGE_GATE": ["parser_policy", "merge_gate"],
        "NO_REQUEUE__NO_MERGE_GATE": ["requeue", "merge_gate"],
    }.get(mode, [])
    row = RootInventoryV1(
        experiment_id=experiment_id,
        condition_id=condition_id,
        case_id=case_id,
        repeat_id=repeat_id,
        domain="factorization",
        difficulty=difficulty,
        topic_family=None,
        position_stratum="early" if experiment_id == "exp2" else None,
        worker_count=worker_count,
        mode=mode,
        disabled_mechanisms=disabled,
        fault_type=fault_type,
        fault_rate=1.0 if fault_type else None,
        dead_worker_count=dead_worker_count,
        kill_progress_target_ratio=kill_progress_target_ratio,
        provider_entry_id=(
            f"endpoint-{configured_model}" if experiment_id == "exp5"
            else "deepseek_v4_pro_exp1_baseline"
        ),
        configured_model=configured_model,
        planned_ai_unit_ids=["range_0"],
        challenge_plan_id=challenge_plan_id,
    )
    row.validate()
    return row


def _attempt(
    experiment_id: str,
    *,
    total_tokens: int | None = 100,
    cost: float | None = 1.0,
    simulated_total_tokens: int | None = None,
) -> AttemptResultV1:
    missing = _nullable_reasons(AttemptResultV1)
    common: dict[str, Any] = {
        "attempt_id": f"{experiment_id}-attempt-0",
        "unit_id": "range_0",
        "planned_ai_unit_id": "range_0",
        "attempt_ordinal": 0,
        "result_kind": "succeeded",
        "raw_response_present": True,
        "parse_result": "accepted",
        "verifier_result": "accepted",
        "checker_result": None,
        "canonical_accepted": True,
        "usage_status": "complete",
        "missing_reason": missing,
    }
    if experiment_id in {"exp1", "exp5"}:
        common.update(
            trace_origin="protocol" if experiment_id == "exp1" else None,
            started_at_ms=0,
            ended_at_ms=10,
            provider_call_made=True,
            http_status=200,
            provider_latency_ms=10,
            prompt_tokens=40,
            prompt_cache_hit_tokens=10,
            prompt_cache_miss_tokens=30,
            completion_tokens=60 if total_tokens is not None else None,
            reasoning_tokens=20 if total_tokens is not None else None,
            total_tokens=total_tokens,
            provider_request_started_at_utc="2026-08-20T00:00:00Z",
            pricing_version="slim_v2.pricing.2026-08-20",
            pricing_tier="off_peak" if experiment_id == "exp1" else "flat",
            cost_estimate_cny=cost,
            call_state="terminal",
        )
    else:
        common.update(
            provider_call_made=False,
            source_response_slot_id=f"slot-{experiment_id}",
            source_response_consumed=True,
            source_attempt_ordinal=0,
            source_attempt_fallback_used=False,
            source_trace_origin="protocol",
            source_result_kind="succeeded",
            source_case_id="pair-case",
            source_repeat_id=0,
            source_planned_ai_unit_id="range_0",
            source_unit_candidate_start=2,
            source_unit_candidate_end=10,
            source_latency_ms=10,
            source_total_tokens=total_tokens,
            source_cost_estimate_cny=cost,
            call_state="not_started",
        )
        if experiment_id == "exp3":
            common.update(
                source_prompt_tokens=40,
                source_prompt_cache_hit_tokens=10,
                source_prompt_cache_miss_tokens=30,
                source_completion_tokens=60,
                source_reasoning_tokens=20,
                source_pricing_version="slim_v2.pricing.2026-08-20",
                source_pricing_tier="off_peak",
                simulated_total_tokens=simulated_total_tokens,
                simulated_latency_ms=10,
                token_perturbation_factor=0.0,
                network_perturbation_factor=0.0,
                perturbation_seed=20260820,
                perturbation_version="slim_v2.exp3_perturbation.v1",
            )
    attempt = AttemptResultV1(**common)
    attempt.validate(experiment_id=experiment_id)
    return attempt


def _result(
    inventory: RootInventoryV1,
    *,
    runtime_ms: int = 10,
    final: bool = True,
    verified: bool = True,
    attempt: AttemptResultV1 | None = None,
    challenge_family: str = "INVALID_PARSED_CANDIDATE",
    tail_tokens: int = 0,
) -> RootResultV2:
    failure_kind = None if verified else ("incorrect_final" if final else "no_final")
    failure_stage = None if verified else "protocol_runtime"
    challenge = []
    ablations = []
    if inventory.experiment_id == "exp4":
        challenge = [
            ChallengeObservationV1(
                challenge_plan_id=str(inventory.challenge_plan_id),
                challenge_family=challenge_family,
                target_planned_ai_unit_id="range_0",
                attempt_ordinal=0,
                injection_boundary="pre_verification",
                opportunity=True,
                injected=True,
                source_semantics_preserved=True,
                candidate_independent_label="invalid",
                reached_verification=True,
                verifier_rejected=verified,
                escaped_to_canonical_or_root=not verified,
                replacement_started=False,
                replacement_succeeded=False,
                valid_final_after_challenge=verified,
            )
        ]
        ablations = [
            AblationObservationV1(
                disabled_mechanism=name,
                route_status="not_reached",
                root_checker_call_count=0,
                root_checker_reached=False,
                root_check_passed=None,
            )
            for name in inventory.disabled_mechanisms
        ]
    fault_observations = []
    recoveries = []
    if inventory.experiment_id == "exp3" and inventory.fault_type:
        fault_observations = [
            FaultObservationV1(
                attempt_id="exp3-attempt-0",
                fault_type=inventory.fault_type,
                target_planned_ai_unit_id="range_0",
                injected=True,
                reached_verification=True,
                independently_wrong=True,
                verifier_intercepted=True,
                escaped_to_canonical_or_root=False,
                discarded_total_tokens=(
                    attempt.simulated_total_tokens if attempt is not None else None
                ),
            )
        ]
        recoveries = [
            RecoveryObservationV1(
                original_attempt_id="exp3-attempt-0",
                replacement_attempt_id="exp3-attempt-1",
                replacement_started=True,
                replacement_succeeded=True,
                reassigned=True,
            )
        ]
    if attempt is not None and inventory.experiment_id in {"exp2", "exp3", "exp4"}:
        attempt.source_case_id = inventory.case_id
    result = RootResultV2(
        experiment_id=inventory.experiment_id,
        condition_id=inventory.condition_id,
        case_id=inventory.case_id,
        repeat_id=inventory.repeat_id,
        domain=inventory.domain,
        difficulty=inventory.difficulty,
        topic_family=inventory.topic_family,
        position_stratum=inventory.position_stratum,
        mode=inventory.mode,
        disabled_mechanisms=list(inventory.disabled_mechanisms),
        worker_count=inventory.worker_count,
        fault_type=inventory.fault_type,
        fault_rate=inventory.fault_rate,
        dead_worker_count=inventory.dead_worker_count,
        kill_progress_target_ratio=inventory.kill_progress_target_ratio,
        challenge_plan_id=inventory.challenge_plan_id,
        challenge_family=challenge_family if inventory.experiment_id == "exp4" else None,
        challenge_target_planned_ai_unit_ids=(
            ["range_0"] if inventory.experiment_id == "exp4" else None
        ),
        challenge_attempt_ordinal_rule=(
            "ordinal_0" if inventory.experiment_id == "exp4" else None
        ),
        provider_family="synthetic",
        provider_entry_id=inventory.provider_entry_id,
        configured_model=inventory.configured_model,
        requested_model=inventory.configured_model,
        resolved_model=inventory.configured_model,
        reasoning_mode="synthetic",
        root_start_at_ms=0,
        root_terminal_at_ms=runtime_ms,
        runtime_wall_clock_ms=runtime_ms,
        trace_tail_started_at_ms=(runtime_ms if inventory.experiment_id == "exp1" and tail_tokens else None),
        trace_tail_terminal_at_ms=(runtime_ms + 100 if inventory.experiment_id == "exp1" and tail_tokens else None),
        trace_tail_wall_clock_ms=(100 if tail_tokens else 0) if inventory.experiment_id == "exp1" else None,
        trace_tail_status=("completed" if tail_tokens else "not_needed") if inventory.experiment_id == "exp1" else None,
        trace_tail_target_ai_unit_ids=(["range_tail"] if tail_tokens else []) if inventory.experiment_id == "exp1" else None,
        trace_tail_recorded_ai_unit_ids=(["range_tail"] if tail_tokens else []) if inventory.experiment_id == "exp1" else None,
        trace_tail_success_unit_count=(1 if tail_tokens else 0) if inventory.experiment_id == "exp1" else None,
        trace_tail_failure_unit_count=0 if inventory.experiment_id == "exp1" else None,
        trace_tail_provider_attempt_count=(1 if tail_tokens else 0) if inventory.experiment_id == "exp1" else None,
        trace_tail_total_tokens=tail_tokens if inventory.experiment_id == "exp1" else None,
        trace_tail_cost_estimate_cny=(9.0 if tail_tokens else 0) if inventory.experiment_id == "exp1" else None,
        preflight_status="passed",
        protocol_started=True,
        root_status="completed" if final else "failed",
        final_result_present=final,
        verified_correct=verified,
        failure_stage=failure_stage,
        failure_kind=failure_kind,
        failure_origin=("synthetic_no_final" if failure_kind == "no_final" else None),
        planned_ai_unit_ids=["range_0"],
        dispatched_ai_unit_ids=["range_0"],
        completed_ai_unit_ids=["range_0"] if final else [],
        unscheduled_ai_unit_ids=[],
        in_flight_ai_unit_ids_at_witness=([] if inventory.experiment_id == "exp2" else None),
        observed_peak_concurrency=(1 if inventory.experiment_id == "exp2" else None),
        worker_execution_facts=[],
        required_slot_count=(1 if inventory.experiment_id in {"exp3", "exp4"} else None),
        recovered_valid_canonical_slot_count=(
            int(verified) if inventory.experiment_id in {"exp3", "exp4"} else None
        ),
        attempts=[] if attempt is None else [attempt],
        fault_target_planned_ai_unit_ids=(
            ["range_0"] if inventory.fault_type else None
        ),
        fault_target_count=1 if inventory.fault_type else None,
        fault_observations=fault_observations,
        recovery_observations=recoveries,
        worker_death_observations=[],
        challenge_observations=challenge,
        ablation_observations=ablations,
        missing_reason={},
        not_applicable_reason=_nullable_reasons(RootResultV2),
    )
    result.validate()
    return result


def _preflight_blocked_result(inventory: RootInventoryV1) -> RootResultV2:
    result = _result(inventory)
    result.root_start_at_ms = None
    result.root_terminal_at_ms = None
    result.runtime_wall_clock_ms = None
    result.preflight_status = "blocked"
    result.protocol_started = False
    result.root_status = "failed"
    result.final_result_present = False
    result.verified_correct = False
    result.failure_stage = "preflight"
    result.failure_kind = "infrastructure_invalid"
    result.failure_origin = "preflight_unavailable"
    result.dispatched_ai_unit_ids = []
    result.completed_ai_unit_ids = []
    result.recovered_valid_canonical_slot_count = 0
    result.challenge_observations = []
    result.missing_reason.update(
        {
            "root_start_at_ms": "protocol_lifecycle_not_started",
            "root_terminal_at_ms": "protocol_lifecycle_not_started",
            "runtime_wall_clock_ms": "protocol_lifecycle_not_started",
        }
    )
    result.validate()
    return result


def _infrastructure_invalid_result_with_facts(
    inventory: RootInventoryV1,
    *,
    attempt: AttemptResultV1,
    runtime_ms: int,
) -> RootResultV2:
    result = _result(inventory, attempt=attempt, runtime_ms=runtime_ms)
    result.root_status = "failed"
    result.final_result_present = False
    result.verified_correct = False
    result.failure_stage = "protocol_runtime"
    result.failure_kind = "infrastructure_invalid"
    result.failure_origin = "synthetic_runtime_infrastructure_failure"
    result.completed_ai_unit_ids = []
    if result.recovered_valid_canonical_slot_count is not None:
        result.recovered_valid_canonical_slot_count = 0
    result.validate()
    return result


def _write_reference(store: RunStore, result: RootResultV2) -> None:
    path = store.exp3_reference_result_path(
        str(result.condition_id), str(result.case_id), int(result.repeat_id)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    document = asdict(result)
    document["schema_version"] = ROOT_RESULT_SCHEMA_VERSION
    path.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _challenge_plan(
    case_id: str,
    *,
    repeat_id: int = 0,
    plan_id: str = "plan-1",
    family: str = "INVALID_PARSED_CANDIDATE",
) -> dict[str, Any]:
    return {
        "challenge_plan_id": plan_id,
        "case_id": case_id,
        "repeat_id": repeat_id,
        "challenge_family": family,
        "target_rule": "stable_first_planned_unit",
        "attempt_rule": "ordinal_0",
    }


def _materialize_golden(run_dir: Path) -> None:
    store = RunStore(run_dir)
    roots: list[RootInventoryV1] = []
    results: list[RootResultV2] = []

    exp1_easy = _inventory("exp1", "exp1-easy", "exp1-easy", difficulty="easy")
    exp1_no_final = _inventory(
        "exp1", "exp1-medium", "exp1-medium", difficulty="medium"
    )
    exp1_missing = _inventory("exp1", "exp1-hard-b", "exp1-hard-b")
    roots.extend((exp1_easy, exp1_no_final, exp1_missing))
    results.extend(
        (
            _result(exp1_easy, attempt=_attempt("exp1"), tail_tokens=1000),
            _result(exp1_no_final, runtime_ms=20, final=False, verified=False),
        )
    )

    exp2_base = _inventory("exp2", "exp2-w1", "pair-case", worker_count=1)
    exp2_ten = _inventory("exp2", "exp2-w10", "pair-case", worker_count=10)
    roots.extend((exp2_base, exp2_ten))
    results.extend(
        (
            _result(exp2_base, runtime_ms=100, attempt=_attempt("exp2", cost=10.0)),
            _result(
                exp2_ten,
                runtime_ms=20,
                attempt=_attempt("exp2", total_tokens=None, cost=20.0),
            ),
        )
    )

    exp3_fault = _inventory(
        "exp3", "exp3-fault", "pair-case", fault_type="false_positive"
    )
    exp3_reference = _inventory("exp3", "exp3-reference", "pair-case")
    exp3_death = _inventory(
        "exp3", "exp3-death", "death-case", dead_worker_count=1,
        kill_progress_target_ratio=0.25,
    )
    exp3_death_reference = _inventory(
        "exp3", "exp3-death-reference", "death-case"
    )
    roots.extend((exp3_fault, exp3_death))
    results.extend(
        (
            _result(
                exp3_fault,
                runtime_ms=15,
                attempt=_attempt("exp3", simulated_total_tokens=120),
            ),
            _result(
                exp3_death,
                runtime_ms=13,
                attempt=_attempt("exp3", simulated_total_tokens=110),
            ),
        )
    )

    modes = (
        "FULL",
        "NO_VERIFICATION",
        "NO_PARSER_POLICY",
        "NO_REQUEUE",
        "NO_MERGE_GATE",
        "NO_VERIFICATION__NO_PARSER_POLICY",
        "NO_VERIFICATION__NO_REQUEUE",
        "NO_VERIFICATION__NO_MERGE_GATE",
        "NO_PARSER_POLICY__NO_REQUEUE",
        "NO_PARSER_POLICY__NO_MERGE_GATE",
        "NO_REQUEUE__NO_MERGE_GATE",
    )
    for mode in modes:
        inventory = _inventory(
            "exp4",
            f"exp4-{mode}",
            "exp4-case",
            mode=mode,
            challenge_plan_id="plan-1",
        )
        is_vp = mode == "NO_VERIFICATION__NO_PARSER_POLICY"
        roots.append(inventory)
        results.append(
            _result(
                inventory,
                runtime_ms=12 if is_vp else 10,
                final=not is_vp,
                verified=not is_vp,
                attempt=_attempt("exp4"),
            )
        )

    endpoints = (
        "zai-org/GLM-5.2",
        "Qwen/Qwen3-14B",
        "MiniMaxAI/MiniMax-M2.5",
    )
    for index, endpoint in enumerate(endpoints):
        inventory = _inventory(
            "exp5",
            f"exp5-{index}",
            f"exp5-case-{index}",
            configured_model=endpoint,
        )
        roots.append(inventory)
        results.append(_result(inventory, attempt=_attempt("exp5")))

    store.write_frozen_inventories(
        conditions=[],
        roots=roots,
        exp3_references=[exp3_reference, exp3_death_reference],
        exp4_challenges=[_challenge_plan("exp4-case")],
    )
    for result in results:
        store.write_root_result(result)
    _write_reference(
        store,
        _result(
            exp3_reference,
            runtime_ms=10,
            attempt=_attempt("exp3", simulated_total_tokens=100),
        ),
    )
    _write_reference(
        store,
        _result(
            exp3_death_reference,
            runtime_ms=10,
            attempt=_attempt("exp3", simulated_total_tokens=100),
        ),
    )


def test_metric_ids_match_the_153_occurrence_authority_contract() -> None:
    expected = tuple(AUTHORITY["formal_metric_ids"])
    assert len(expected) == GOLDEN["formal_metric_id_count"] == 153
    assert metric_ids() == expected
    occurrences = formal_metric_occurrences()
    assert tuple(item["metric_id"] for item in occurrences) == expected
    assert tuple(item["ordinal"] for item in occurrences) == tuple(range(153))
    assert all(
        item["table_scope"] in {"exp1", "exp2", "exp3", "exp4", "exp5"}
        and item["row_kind"] in {"cell", "pair", "fault_reference_pair", "death_reference_pair", "interaction", "model"}
        and item["formula_producer"] not in {"_apply_template", "template_placeholder"}
        for item in occurrences
    )


def test_type7_sample_variance_and_fixed_cluster_bootstrap_boundaries() -> None:
    assert type7_quantile([1.0, 2.0, 3.0], 0.25) == pytest.approx(1.5)
    assert type7_quantile([1.0, 2.0, 3.0], 0.75) == pytest.approx(2.5)
    assert sample_variance([1.0, 2.0, 3.0]) == pytest.approx(1.0)
    assert sample_variance([1.0]) is None

    rows = [
        {"case_id": f"case-{index}", "domain": "factorization", "value": index}
        for index in range(5)
    ]
    statistic = lambda sample: sum(item["value"] for item in sample) / len(sample)
    first = stratified_case_cluster_bootstrap(rows, statistic)
    second = stratified_case_cluster_bootstrap(rows, statistic)
    assert first == second
    assert first["valid_bootstrap_replicate_count"] == 10_000
    assert first["bootstrap_seed"] == 20260820
    assert first["ci95_low"] is not None

    too_few = stratified_case_cluster_bootstrap(rows[:4], statistic)
    assert too_few["ci95_low"] is None
    assert too_few["missing_reason"] == "insufficient_case_clusters_for_interval"

    mostly_invalid = stratified_case_cluster_bootstrap(
        rows,
        lambda sample: (
            1.0 if len({item["case_id"] for item in sample}) == 1 else None
        ),
    )
    assert mostly_invalid["valid_bootstrap_replicate_count"] < 9_500
    assert mostly_invalid["ci95_low"] is None
    assert mostly_invalid["missing_reason"] == "insufficient_valid_bootstrap_replicates"


def test_reduce_run_golden_all_experiments_and_io_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "run"
    _materialize_golden(run_dir)
    observed_reads: list[Path] = []
    original_open = Path.open

    def spy_open(path: Path, *args: Any, **kwargs: Any):
        mode = args[0] if args else kwargs.get("mode", "r")
        if "r" in mode:
            observed_reads.append(path)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", spy_open)
    summary = reduce_run(run_dir)

    assert summary["formal_metric_id_count"] == 153
    assert summary["provider_calls_observed"]["exp2"] == 0
    assert summary["provider_calls_observed"]["exp3"] == 0
    assert summary["provider_calls_observed"]["exp4"] == 0
    assert all(
        not ({"responses", "raw", "system", "events", "artifacts"} & set(path.parts))
        for path in observed_reads
    )

    tables = run_dir / "metrics" / "tables"
    exp1 = _read_rows(tables / "exp1.jsonl")
    easy = next(row for row in exp1 if row["slice"]["difficulty"] == "easy")
    no_final = next(row for row in exp1 if row["slice"]["difficulty"] == "medium")
    hard = next(row for row in exp1 if row["slice"]["difficulty"] == "hard")
    for key, value in GOLDEN["exp1_easy"].items():
        assert easy[key] == pytest.approx(value)
    for key, value in GOLDEN["exp1_no_final"].items():
        assert no_final[key] == pytest.approx(value)
    assert hard["preregistered_root_count"] == 1
    assert hard["scientifically_valid_root_count"] == 0
    assert hard["infrastructure_invalid_root_count"] == 1
    assert hard["infra_invalid_failure_count"] == 0
    assert hard["failure_root_count"] == 0
    assert hard["failure_origin_counts"] == {"missing_committed_root_result": 1}
    assert (
        hard["scientifically_valid_root_count"]
        + hard["infrastructure_invalid_root_count"]
        == hard["preregistered_root_count"]
    )
    assert hard["completion_rate"] is None
    assert hard["missing_reasons"]["completion_rate"] == (
        "infrastructure_invalid_root_present"
    )

    exp2 = _read_rows(tables / "exp2.jsonl")
    pair = next(
        row
        for row in exp2
        if row["row_kind"] == "pair" and row["slice"]["worker_count"] == 10
    )
    for key, value in GOLDEN["exp2_worker10_pair"].items():
        assert pair[key] == pytest.approx(value)
    for metric in (
        "trace_replay_paired_speedup",
        "trace_replay_parallel_efficiency",
        "paired_trace_token_multiplier",
        "paired_trace_cost_multiplier",
    ):
        assert f"{metric}_median_ci95_low" in pair
        assert f"{metric}_median_bootstrap_variance" in pair

    exp3 = _read_rows(tables / "exp3.jsonl")
    fault_pair = next(row for row in exp3 if row["row_kind"] == "fault_reference_pair")
    for key, value in GOLDEN["exp3_fault_pair"].items():
        assert fault_pair[key] == pytest.approx(value)
    death_pair = next(row for row in exp3 if row["row_kind"] == "death_reference_pair")
    for key, value in GOLDEN["exp3_death_pair"].items():
        assert death_pair[key] == pytest.approx(value)
    death_cell = next(
        row
        for row in exp3
        if row["row_kind"] == "cell" and row["slice"]["dead_worker_count"] == 1
    )
    assert death_cell["injected_fault_target_count"] is None
    assert (
        death_cell["not_applicable_reasons"]["injected_fault_target_count"]
        == "not_a_rate_fault_condition"
    )
    assert death_cell["controlled_wrong_candidate_count"] is None
    assert death_cell["kill_progress_error_signed_mean_pp"] is None
    assert (
        death_cell["missing_reasons"]["kill_progress_error_signed_mean_pp"]
        == "missing_worker_death_observation"
    )

    exp4 = _read_rows(tables / "exp4.jsonl")
    interactions = [row for row in exp4 if row["row_kind"] == "interaction"]
    assert len(interactions) == GOLDEN["exp4_interaction_row_count"]
    vp = next(
        row
        for row in interactions
        if row["slice"]["mechanism_i"] == "verification"
        and row["slice"]["mechanism_j"] == "parser_policy"
    )
    for key, value in GOLDEN["exp4_vp_interaction"].items():
        assert vp[key] == pytest.approx(value)

    exp5 = _read_rows(tables / "exp5.jsonl")
    assert len([row for row in exp5 if row["row_kind"] == "model"]) == GOLDEN["exp5_endpoint_count"]
    actual_tables = {
        "exp1": exp1,
        "exp2": exp2,
        "exp3": exp3,
        "exp4": exp4,
        "exp5": exp5,
    }
    assert len(summary["formal_metric_occurrences"]) == 153
    for occurrence in summary["formal_metric_occurrences"]:
        assert occurrence["evidence_fields"]
        candidates = [
            row
            for row in actual_tables[occurrence["table_scope"]]
            if row["row_kind"] == occurrence["row_kind"]
        ]
        assert candidates, occurrence
        metric = occurrence["metric_id"]
        assert any(
            metric in row
            and (
                row[metric] is not None
                or metric in row["missing_reasons"]
                or metric in row["not_applicable_reasons"]
            )
            and row["missing_reasons"].get(metric)
            != "metric_inputs_not_present_in_this_run"
            for row in candidates
        ), occurrence
    assert (run_dir / "metrics" / "summary.json").exists()
    assert all((tables / f"exp{number}.csv").exists() for number in range(1, 6))
    assert not list((run_dir / "metrics").rglob("*.tmp"))
    stable_before = {
        path.relative_to(run_dir): path.read_bytes()
        for path in (run_dir / "metrics").rglob("*") if path.is_file()
    }
    assert reduce_run(run_dir) == summary
    stable_after = {
        path.relative_to(run_dir): path.read_bytes()
        for path in (run_dir / "metrics").rglob("*") if path.is_file()
    }
    assert stable_after == stable_before

    reducer_path = Path(__file__).parents[3] / "src/tokenshare/experiments/slim_v2/reducer.py"
    tree = ast.parse(reducer_path.read_text(encoding="utf-8"))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not any(
        banned in module
        for module in imported
        for banned in ("runtime", "provider", "checker", "scenarios", "execution")
    )


def test_exp5_v4_reference_table_keeps_live_latency_and_omits_reused_latency(
    tmp_path: Path,
) -> None:
    """补充表只带入 V4 的质量/资源事实，绝不将 Exp1 时间伪装为 Exp5。"""

    from tokenshare.experiments.slim_v2.reducer import (
        reduce_exp5_with_exp1_v4_reference,
    )

    target = RunStore(tmp_path / "exp5-target")
    source = RunStore(tmp_path / "exp1-flash-source")
    case_id = "shared-factor-case"
    models = (
        "zai-org/GLM-5.2",
        "Qwen/Qwen3-14B",
        "MiniMaxAI/MiniMax-M2.5",
    )
    target_roots = [
        _inventory(
            "exp5",
            f"target-{ordinal}",
            case_id,
            configured_model=model,
        )
        for ordinal, model in enumerate(models)
    ]
    target.write_frozen_inventories(
        conditions=[], roots=target_roots, exp3_references=[], exp4_challenges=[]
    )
    for ordinal, root in enumerate(target_roots, start=1):
        target.write_root_result(
            _result(
                root,
                runtime_ms=ordinal * 10,
                attempt=_attempt("exp5", total_tokens=100 * ordinal, cost=float(ordinal)),
            )
        )

    source_root = _inventory(
        "exp1",
        "flash-source",
        case_id,
        configured_model="deepseek-v4-flash",
    )
    source.write_frozen_inventories(
        conditions=[], roots=[source_root], exp3_references=[], exp4_challenges=[]
    )
    source.write_root_result(
        _result(
            source_root,
            runtime_ms=999,
            attempt=_attempt("exp1", total_tokens=777, cost=7.77),
        )
    )

    summary = reduce_exp5_with_exp1_v4_reference(target.run_dir, source.run_dir)

    assert summary["row_count"] == 4
    output = target.run_dir / "metrics" / "supplemental"
    rows = _read_rows(output / "exp5_with_exp1_v4_reference.jsonl")
    assert len(rows) == 4
    assert (output / "exp5_with_exp1_v4_reference.csv").is_file()
    live = {row["configured_model"]: row for row in rows if row["observation_origin"] == "exp5_live"}
    assert set(live) == set(models)
    assert [live[model]["repeat0_wall_clock_ms"] for model in models] == [10, 20, 30]
    assert [live[model]["actual_first_attempt_total_tokens"] for model in models] == [100, 200, 300]

    reused = next(row for row in rows if row["observation_origin"] == "exp1_reused_actual")
    assert reused["configured_model"] == "deepseek-v4-flash"
    assert reused["source_experiment_id"] == "exp1"
    assert reused["actual_first_attempt_total_tokens"] == 777
    assert reused["actual_first_attempt_cost_estimate_cny"] == pytest.approx(7.77)
    for field in reused["wall_clock_field_names"]:
        assert reused[field] is None
        assert reused["not_applicable_reasons"][field] == "not_applicable_or_unavailable"
    assert not (target.run_dir / "metrics" / "summary.json").exists()
    assert not (target.run_dir / "metrics" / "tables" / "exp5.jsonl").exists()


def _write_exp5_v4_reference_boundary_inputs(
    tmp_path: Path,
    *,
    source_case_id: str = "shared-factor-case",
    source_attempt_ordinal: int = 0,
) -> tuple[RunStore, RunStore]:
    """写入最小 ordinary 输入，以验证补充表在越界来源事实下 fail closed。"""

    target = RunStore(tmp_path / "exp5-target")
    source = RunStore(tmp_path / "exp1-flash-source")
    models = (
        "zai-org/GLM-5.2",
        "Qwen/Qwen3-14B",
        "MiniMaxAI/MiniMax-M2.5",
    )
    target_roots = [
        _inventory(
            "exp5", f"target-{ordinal}", "shared-factor-case",
            configured_model=model,
        )
        for ordinal, model in enumerate(models)
    ]
    target.write_frozen_inventories(
        conditions=[], roots=target_roots, exp3_references=[], exp4_challenges=[]
    )
    for root in target_roots:
        target.write_root_result(_result(root, attempt=_attempt("exp5")))

    source_root = _inventory(
        "exp1", "flash-source", source_case_id,
        configured_model="deepseek-v4-flash",
    )
    source.write_frozen_inventories(
        conditions=[], roots=[source_root], exp3_references=[], exp4_challenges=[]
    )
    source_attempt = _attempt("exp1")
    source_attempt.attempt_ordinal = source_attempt_ordinal
    source.write_root_result(_result(source_root, attempt=source_attempt))
    return target, source


def test_exp5_v4_reference_rejects_source_target_case_set_mismatch(
    tmp_path: Path,
) -> None:
    """Flash source 不能遗漏或替换任一 Exp5 case。"""

    from tokenshare.experiments.slim_v2.reducer import (
        reduce_exp5_with_exp1_v4_reference,
    )

    target, source = _write_exp5_v4_reference_boundary_inputs(
        tmp_path, source_case_id="different-factor-case"
    )

    with pytest.raises(ValueError, match="source and target case sets must match exactly"):
        reduce_exp5_with_exp1_v4_reference(target.run_dir, source.run_dir)

    assert not (target.run_dir / "metrics" / "supplemental").exists()


def test_exp5_v4_reference_rejects_source_provider_retry(
    tmp_path: Path,
) -> None:
    """Flash source 中任何 ordinal>0 的真实调用都不具备补充比较资格。"""

    from tokenshare.experiments.slim_v2.reducer import (
        reduce_exp5_with_exp1_v4_reference,
    )

    target, source = _write_exp5_v4_reference_boundary_inputs(
        tmp_path, source_attempt_ordinal=1
    )

    with pytest.raises(
        ValueError, match="source Exp1 Flash results must not contain ordinal>0 provider calls"
    ):
        reduce_exp5_with_exp1_v4_reference(target.run_dir, source.run_dir)

    assert not (target.run_dir / "metrics" / "supplemental").exists()


def test_duplicate_inventory_blocks_reduction_before_atomic_replacement(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    store = RunStore(run_dir)
    duplicate = _inventory("exp1", "duplicate", "duplicate", difficulty="easy")
    store.write_frozen_inventories(
        conditions=[],
        roots=[duplicate, duplicate],
        exp3_references=[],
        exp4_challenges=[],
    )
    metrics_dir = run_dir / "metrics"
    metrics_dir.mkdir(parents=True)
    summary_path = metrics_dir / "summary.json"
    summary_path.write_text('{"sentinel":true}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate root inventory identity"):
        reduce_run(run_dir)

    assert json.loads(summary_path.read_text(encoding="utf-8")) == {"sentinel": True}


def test_exp4_quadruples_reject_blocked_mismatched_and_missing_arms(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    store = RunStore(run_dir)
    modes = (
        "FULL",
        "NO_VERIFICATION",
        "NO_PARSER_POLICY",
        "NO_VERIFICATION__NO_PARSER_POLICY",
    )
    roots: list[RootInventoryV1] = []
    results: list[RootResultV2] = []
    for case_id, invalid_kind in (
        ("blocked-case", "blocked"),
        ("mismatched-case", "mismatched"),
        ("missing-case", "missing"),
    ):
        for mode in modes:
            inventory = _inventory(
                "exp4",
                f"{case_id}-{mode}",
                case_id,
                mode=mode,
                challenge_plan_id="plan-1",
            )
            roots.append(inventory)
            if invalid_kind == "missing" and mode == modes[-1]:
                continue
            result = _result(inventory, attempt=_attempt("exp4"))
            if invalid_kind == "blocked" and mode == "NO_VERIFICATION":
                result = _preflight_blocked_result(inventory)
            if invalid_kind == "mismatched" and mode == modes[-1]:
                result.challenge_plan_id = "different-plan"
                result.validate()
            results.append(result)

    store.write_frozen_inventories(
        conditions=[],
        roots=roots,
        exp3_references=[],
        exp4_challenges=[
            _challenge_plan("blocked-case"),
            _challenge_plan("mismatched-case"),
            _challenge_plan("missing-case"),
        ],
    )
    for result in results:
        store.write_root_result(result)

    reduce_run(run_dir)
    interactions = [
        row
        for row in _read_rows(run_dir / "metrics" / "tables" / "exp4.jsonl")
        if row["row_kind"] == "interaction"
        and row["slice"]["mechanism_i"] == "verification"
        and row["slice"]["mechanism_j"] == "parser_policy"
    ]
    assert sum(row["planned_quadruple_count"] for row in interactions) == 3
    assert sum(row["eligible_quadruple_count"] for row in interactions) == 0
    assert sum(row["ineligible_quadruple_count"] for row in interactions) == 3
    reasons = {
        reason
        for row in interactions
        for reason in row["ineligible_quadruple_reason_counts"]
    }
    assert reasons == {
        "challenge_plan_mismatch",
        "infrastructure_invalid_root_present",
        "missing_committed_root_result",
    }
    missing_rows = [
        row
        for row in interactions
        if row["ineligible_quadruple_reason_counts"].get(
            "missing_committed_root_result"
        )
    ]
    assert missing_rows
    assert all(
        row["slice"]["challenge_family"] == "INVALID_PARSED_CANDIDATE"
        for row in missing_rows
    )


def test_exp4_actual_observations_and_metric_independent_quad_eligibility(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    store = RunStore(run_dir)
    pair_mode = "NO_VERIFICATION__NO_PARSER_POLICY"
    modes = ("FULL", "NO_VERIFICATION", "NO_PARSER_POLICY", pair_mode)
    roots = [
        _inventory(
            "exp4",
            f"actual-{mode}",
            "actual-case",
            mode=mode,
            challenge_plan_id="actual-plan",
        )
        for mode in modes
    ]
    results = [
        _result(root, attempt=_attempt("exp4"))
        for root in roots
    ]
    pair_result = results[-1]
    pair_result.challenge_observations[0].escaped_to_canonical_or_root = False
    pair_result.ablation_observations[0].wrong_canonical_accepted = True
    pair_result.ablation_observations[0].root_checker_rejected_after_wrong_canonical = True
    pair_result.ablation_observations[1].root_checker_rejected_after_wrong_canonical = True
    pair_result.recovered_valid_canonical_slot_count = None
    pair_result.required_slot_count = None
    pair_result.validate()
    store.write_frozen_inventories(
        conditions=[], roots=roots, exp3_references=[],
        exp4_challenges=[
            _challenge_plan("actual-case", plan_id="actual-plan")
        ],
    )
    for result in results:
        store.write_root_result(result)

    reduce_run(run_dir)
    rows = _read_rows(run_dir / "metrics" / "tables" / "exp4.jsonl")
    cell = next(row for row in rows if row["row_kind"] == "cell" and row["slice"]["mode"] == pair_mode)
    assert cell["wrong_canonical_acceptance_count"] == 1
    assert cell["root_checker_rejection_after_wrong_canonical_count"] == 1
    interaction = next(row for row in rows if row["row_kind"] == "interaction")
    for metric in (
        "single_removal_success_loss_i",
        "pair_removal_success_loss_ij",
        "pair_interaction_success_penalty_ij",
        "pair_interaction_completion_penalty_ij",
    ):
        assert interaction[f"{metric}_eligible_quadruple_count"] == 1
        assert interaction[metric] is not None
    required_metric = "pair_interaction_required_slot_penalty_ij"
    assert interaction[f"{required_metric}_eligible_quadruple_count"] == 0
    assert interaction[required_metric] is None


def test_exp5_first_attempt_taxonomy_uses_actual_boundaries(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    store = RunStore(run_dir)
    roots: list[RootInventoryV1] = []
    results: list[RootResultV2] = []
    for index, kind in enumerate(("pass", "transport", "parse", "verification")):
        root = _inventory(
            "exp5", f"taxonomy-{index}", f"taxonomy-{index}",
            configured_model="zai-org/GLM-5.2",
        )
        attempt = _attempt("exp5")
        if kind == "transport":
            attempt.raw_response_present = False
        elif kind == "parse":
            attempt.result_kind = "parse_failed"
            attempt.parse_result = "parse_failed"
            attempt.verifier_result = None
        elif kind == "verification":
            attempt.verifier_result = "rejected"
        attempt.validate(experiment_id="exp5")
        roots.append(root)
        results.append(_result(root, attempt=attempt))
    store.write_frozen_inventories(
        conditions=[], roots=roots, exp3_references=[], exp4_challenges=[]
    )
    for result in results:
        store.write_root_result(result)
    reduce_run(run_dir)
    row = _read_rows(run_dir / "metrics" / "tables" / "exp5.jsonl")[0]
    assert row["first_attempt_provider_transport_failure_count"] == 1
    assert row["first_attempt_parse_schema_unusable_count"] == 1
    assert row["first_attempt_verification_checker_rejection_count"] == 1
    assert row["first_attempt_without_verifier_accepted_candidate_count"] == 3


def test_exp2_repeat_values_clear_placeholder_reasons(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "exp2-repeat-reasons")
    roots: list[RootInventoryV1] = []
    results: list[RootResultV2] = []
    for repeat_id, baseline_ms, treatment_ms in ((0, 10, 5), (1, 12, 3)):
        for worker_count, runtime_ms in ((1, baseline_ms), (10, treatment_ms)):
            inventory = _inventory(
                "exp2",
                f"repeat-{repeat_id}-worker-{worker_count}",
                "repeat-case",
                repeat_id=repeat_id,
                worker_count=worker_count,
            )
            roots.append(inventory)
            results.append(
                _result(
                    inventory,
                    runtime_ms=runtime_ms,
                    attempt=_attempt("exp2"),
                )
            )
    store.write_frozen_inventories(
        conditions=[],
        roots=roots,
        exp3_references=[],
        exp4_challenges=[],
    )
    for result in results:
        store.write_root_result(result)

    reduce_run(store.run_dir)

    pair_rows = [
        row
        for row in _read_rows(store.run_dir / "metrics" / "tables" / "exp2.jsonl")
        if row["row_kind"] == "pair"
    ]
    expected = {
        "trace_replay_paired_speedup_repeat_min": 2.0,
        "trace_replay_paired_speedup_repeat_max": 4.0,
        "trace_replay_paired_speedup_relative_difference": 2.0 / 3.0,
    }
    for row in pair_rows:
        for metric, value in expected.items():
            assert row[metric] == pytest.approx(value)
            assert metric not in row["missing_reasons"]
            assert metric not in row["not_applicable_reasons"]


@pytest.mark.parametrize(
    ("experiment_id", "metric"),
    (
        ("exp3", "controlled_wrong_candidate_interception_rate"),
        ("exp4", "raw_only_acceptance_rate"),
        ("exp5", "first_attempt_nonpass_rate"),
    ),
)
def test_formal_point_ratio_zero_denominator_has_reason(
    tmp_path: Path,
    experiment_id: str,
    metric: str,
) -> None:
    store = RunStore(tmp_path / f"zero-denominator-{experiment_id}")
    inventory_kwargs: dict[str, Any] = {}
    challenge_rows: list[dict[str, Any]] = []
    if experiment_id == "exp3":
        inventory_kwargs["fault_type"] = "false_positive"
    elif experiment_id == "exp4":
        inventory_kwargs.update(
            mode="NO_PARSER_POLICY",
            challenge_plan_id="zero-denominator-plan",
        )
        challenge_rows.append(
            _challenge_plan(
                "zero-denominator-case",
                plan_id="zero-denominator-plan",
                family="PARSER_REQUIRED_CANONICAL_JSON",
            )
        )
    elif experiment_id == "exp5":
        inventory_kwargs["configured_model"] = "zai-org/GLM-5.2"
    inventory = _inventory(
        experiment_id,
        "zero-denominator-condition",
        "zero-denominator-case",
        **inventory_kwargs,
    )
    attempt = None if experiment_id == "exp5" else _attempt(
        experiment_id,
        simulated_total_tokens=(100 if experiment_id == "exp3" else None),
    )
    result = _result(
        inventory,
        attempt=attempt,
        challenge_family=(
            "PARSER_REQUIRED_CANONICAL_JSON"
            if experiment_id == "exp4"
            else "INVALID_PARSED_CANDIDATE"
        ),
    )
    if experiment_id == "exp3":
        result.fault_observations = []
        result.recovery_observations = []
        result.validate()
    store.write_frozen_inventories(
        conditions=[],
        roots=[inventory],
        exp3_references=[],
        exp4_challenges=challenge_rows,
    )
    store.write_root_result(result)

    reduce_run(store.run_dir)

    rows = _read_rows(
        store.run_dir / "metrics" / "tables" / f"{experiment_id}.jsonl"
    )
    row = next(item for item in rows if item["row_kind"] in {"cell", "model"})
    assert row[metric] is None
    assert row["missing_reasons"][metric] == "zero_denominator"
    assert metric not in row["not_applicable_reasons"]


def test_infrastructure_invalid_root_nulls_cell_scientific_rates(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "infra-invalid-run")
    valid_root = _inventory("exp1", "valid", "valid-case", difficulty="easy")
    invalid_root = _inventory("exp1", "invalid", "invalid-case", difficulty="easy")
    store.write_frozen_inventories(
        conditions=[],
        roots=[valid_root, invalid_root],
        exp3_references=[],
        exp4_challenges=[],
    )
    store.write_root_result(_result(valid_root, attempt=_attempt("exp1")))
    store.write_root_result(_preflight_blocked_result(invalid_root))

    reduce_run(store.run_dir)

    row = _read_rows(store.run_dir / "metrics" / "tables" / "exp1.jsonl")[0]
    assert row["preregistered_root_count"] == 2
    assert row["scientifically_valid_root_count"] == 1
    assert row["infrastructure_invalid_root_count"] == 1
    assert row["completion_rate"] is None
    assert row["end_to_end_verified_success_rate"] is None
    assert row["missing_reasons"]["completion_rate"] == (
        "infrastructure_invalid_root_present"
    )
    assert row["missing_reasons"]["end_to_end_verified_success_rate"] == (
        "infrastructure_invalid_root_present"
    )


def test_failure_counts_use_committed_failure_kind_only(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "committed-failure-taxonomy")
    inventories = [
        _inventory("exp1", f"failure-{index}", f"failure-case-{index}", difficulty="easy")
        for index in range(3)
    ]
    no_final = _result(inventories[0], final=True, verified=False)
    no_final.failure_kind = "no_final"
    incorrect_final = _result(inventories[1], final=False, verified=False)
    incorrect_final.failure_kind = "incorrect_final"
    infrastructure_invalid = _result(inventories[2], final=True, verified=False)
    infrastructure_invalid.failure_kind = "infrastructure_invalid"
    results = (no_final, incorrect_final, infrastructure_invalid)
    for result in results:
        result.failure_origin = "shared_diagnostic_origin"
        result.validate()
    store.write_frozen_inventories(
        conditions=[],
        roots=inventories,
        exp3_references=[],
        exp4_challenges=[],
    )
    for result in results:
        store.write_root_result(result)

    reduce_run(store.run_dir)

    row = _read_rows(store.run_dir / "metrics" / "tables" / "exp1.jsonl")[0]
    assert row["no_final_failure_count"] == 1
    assert row["incorrect_final_failure_count"] == 1
    assert row["infra_invalid_failure_count"] == 1
    assert row["failure_root_count"] == 3
    assert (
        row["no_final_failure_count"]
        + row["incorrect_final_failure_count"]
        + row["infra_invalid_failure_count"]
        == row["failure_root_count"]
    )
    assert row["failure_origin_counts"] == {"shared_diagnostic_origin": 3}


def test_infrastructure_invalid_pair_endpoint_is_reported_ineligible(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "infra-invalid-pair-run")
    baseline = _inventory("exp2", "baseline", "paired-case", worker_count=1)
    treatment = _inventory("exp2", "treatment", "paired-case", worker_count=10)
    store.write_frozen_inventories(
        conditions=[],
        roots=[baseline, treatment],
        exp3_references=[],
        exp4_challenges=[],
    )
    store.write_root_result(_result(baseline, attempt=_attempt("exp2")))
    store.write_root_result(_preflight_blocked_result(treatment))

    reduce_run(store.run_dir)

    rows = _read_rows(store.run_dir / "metrics" / "tables" / "exp2.jsonl")
    pair = next(row for row in rows if row["row_kind"] == "pair")
    assert pair["paired_speedup_planned_pair_count"] == 1
    assert pair["paired_speedup_eligible_pair_count"] == 0
    assert pair["paired_speedup_ineligible_pair_count"] == 1
    assert pair["paired_speedup_ineligible_reason_counts"] == {
        "infrastructure_invalid_root_present": 1
    }


@pytest.mark.parametrize(
    ("experiment_id", "rate_metrics"),
    (
        (
            "exp3",
            (
                "controlled_wrong_candidate_interception_rate",
                "controlled_wrong_candidate_escape_rate",
                "replacement_attempt_success_rate",
                "result_completeness_rate",
            ),
        ),
        (
            "exp5",
            (
                "first_attempt_nonpass_rate",
                "first_attempt_verification_rejection_rate",
                "first_attempt_call_coverage",
            ),
        ),
    ),
)
def test_infrastructure_invalid_root_nulls_exp3_and_exp5_scientific_rate_intervals(
    tmp_path: Path,
    experiment_id: str,
    rate_metrics: tuple[str, ...],
) -> None:
    store = RunStore(tmp_path / f"infra-invalid-{experiment_id}")
    inventory_kwargs = (
        {"fault_type": "false_positive"}
        if experiment_id == "exp3"
        else {"configured_model": "zai-org/GLM-5.2"}
    )
    valid_root = _inventory(
        experiment_id, "valid", "valid-case", **inventory_kwargs
    )
    invalid_root = _inventory(
        experiment_id, "invalid", "invalid-case", **inventory_kwargs
    )
    store.write_frozen_inventories(
        conditions=[],
        roots=[valid_root, invalid_root],
        exp3_references=[],
        exp4_challenges=[],
    )
    store.write_root_result(
        _result(
            valid_root,
            attempt=_attempt(
                experiment_id,
                simulated_total_tokens=(100 if experiment_id == "exp3" else None),
            ),
        )
    )
    store.write_root_result(_preflight_blocked_result(invalid_root))

    reduce_run(store.run_dir)

    row = _read_rows(
        store.run_dir / "metrics" / "tables" / f"{experiment_id}.jsonl"
    )[0]
    assert row["scientifically_valid_root_count"] == 1
    assert row["infrastructure_invalid_root_count"] == 1
    for metric in rate_metrics:
        assert row[metric] is None
        assert row["missing_reasons"][metric] == (
            "infrastructure_invalid_root_present"
        )
        for suffix in (
            "ci95_low",
            "ci95_high",
            "bootstrap_variance",
            "bootstrap_standard_error",
        ):
            assert row[f"{metric}_{suffix}"] is None
        assert row["metric_metadata"][metric]["missing_reason"] == (
            "infrastructure_invalid_root_present"
        )


def test_exp3_infrastructure_invalid_cell_preserves_exact_recovery_facts(
    tmp_path: Path,
) -> None:
    store = RunStore(tmp_path / "exp3-infra-exact-facts")
    treatment = _inventory(
        "exp3",
        "infra-fault",
        "infra-fault-case",
        fault_type="false_positive",
    )
    reference = _inventory(
        "exp3",
        "infra-fault-reference",
        "infra-fault-case",
    )
    treatment_result = _infrastructure_invalid_result_with_facts(
        treatment,
        attempt=_attempt("exp3", simulated_total_tokens=120),
        runtime_ms=15,
    )
    reference_result = _result(
        reference,
        attempt=_attempt("exp3", simulated_total_tokens=100),
        runtime_ms=10,
    )
    store.write_frozen_inventories(
        conditions=[],
        roots=[treatment],
        exp3_references=[reference],
        exp4_challenges=[],
    )
    store.write_root_result(treatment_result)
    _write_reference(store, reference_result)

    reduce_run(store.run_dir)

    rows = _read_rows(store.run_dir / "metrics" / "tables" / "exp3.jsonl")
    cell = next(row for row in rows if row["row_kind"] == "cell")
    assert cell["injected_fault_target_count"] == 1
    assert cell["controlled_wrong_candidate_count"] == 1
    assert cell["controlled_wrong_candidate_interception_count"] == 1
    assert cell["controlled_wrong_candidate_escape_count"] == 0
    assert cell["started_replacement_attempt_count"] == 1
    assert cell["successful_replacement_attempt_count"] == 1
    assert cell["reassignment_count"] == 1
    assert cell["discarded_simulated_trace_tokens"] == 120
    assert cell["recovered_valid_canonical_required_slots"] == 0
    assert cell["preregistered_required_slots"] == 1
    assert cell["unrecovered_root_count"] == 1
    for metric in (
        "completion_rate",
        "end_to_end_verified_success_rate",
        "controlled_wrong_candidate_interception_rate",
        "controlled_wrong_candidate_escape_rate",
        "replacement_attempt_success_rate",
        "result_completeness_rate",
    ):
        assert cell[metric] is None
        assert cell["missing_reasons"][metric] == (
            "infrastructure_invalid_root_present"
        )
    pair = next(row for row in rows if row["row_kind"] == "fault_reference_pair")
    for metric in (
        "simulated_wall_clock_overhead_ms",
        "simulated_token_overhead",
        "simulated_trace_attributed_cost_overhead",
    ):
        assert pair[metric] is None
        assert pair["missing_reasons"][metric] == (
            "infrastructure_invalid_root_present"
        )
        assert pair[f"{metric}_planned_pair_count"] == 1
        assert pair[f"{metric}_eligible_pair_count"] == 0
        assert pair[f"{metric}_ineligible_pair_count"] == 1


def test_exp3_infrastructure_invalid_reference_nulls_pair_effects(
    tmp_path: Path,
) -> None:
    store = RunStore(tmp_path / "exp3-infra-reference")
    treatment = _inventory(
        "exp3",
        "valid-fault",
        "infra-reference-case",
        fault_type="false_positive",
    )
    reference = _inventory(
        "exp3",
        "infra-reference",
        "infra-reference-case",
    )
    store.write_frozen_inventories(
        conditions=[],
        roots=[treatment],
        exp3_references=[reference],
        exp4_challenges=[],
    )
    store.write_root_result(
        _result(
            treatment,
            attempt=_attempt("exp3", simulated_total_tokens=120),
            runtime_ms=15,
        )
    )
    _write_reference(
        store,
        _infrastructure_invalid_result_with_facts(
            reference,
            attempt=_attempt("exp3", simulated_total_tokens=100),
            runtime_ms=10,
        ),
    )

    reduce_run(store.run_dir)

    rows = _read_rows(store.run_dir / "metrics" / "tables" / "exp3.jsonl")
    pair = next(row for row in rows if row["row_kind"] == "fault_reference_pair")
    for metric in (
        "simulated_wall_clock_overhead_ms",
        "simulated_token_overhead",
        "simulated_trace_attributed_cost_overhead",
    ):
        assert pair[metric] is None
        assert pair["missing_reasons"][metric] == (
            "infrastructure_invalid_root_present"
        )
        assert pair[f"{metric}_planned_pair_count"] == 1
        assert pair[f"{metric}_eligible_pair_count"] == 0
        assert pair[f"{metric}_ineligible_pair_count"] == 1
        assert pair[f"{metric}_ineligible_reason_counts"] == {
            "infrastructure_invalid_root_present": 1
        }
        assert pair["metric_metadata"][metric]["missing_reason"] == (
            "infrastructure_invalid_root_present"
        )


def test_exp3_missing_committed_reference_nulls_pair_effects_as_infra(
    tmp_path: Path,
) -> None:
    store = RunStore(tmp_path / "exp3-missing-reference-result")
    treatment = _inventory(
        "exp3",
        "valid-fault",
        "missing-reference-case",
        fault_type="false_positive",
    )
    reference = _inventory(
        "exp3",
        "missing-reference",
        "missing-reference-case",
    )
    store.write_frozen_inventories(
        conditions=[],
        roots=[treatment],
        exp3_references=[reference],
        exp4_challenges=[],
    )
    store.write_root_result(
        _result(
            treatment,
            attempt=_attempt("exp3", simulated_total_tokens=120),
            runtime_ms=15,
        )
    )

    reduce_run(store.run_dir)

    rows = _read_rows(store.run_dir / "metrics" / "tables" / "exp3.jsonl")
    pair = next(row for row in rows if row["row_kind"] == "fault_reference_pair")
    for metric in (
        "simulated_wall_clock_overhead_ms",
        "simulated_token_overhead",
        "simulated_trace_attributed_cost_overhead",
    ):
        assert pair[metric] is None
        assert pair["missing_reasons"][metric] == (
            "infrastructure_invalid_root_present"
        )
        assert pair[f"{metric}_planned_pair_count"] == 1
        assert pair[f"{metric}_eligible_pair_count"] == 0
        assert pair[f"{metric}_ineligible_pair_count"] == 1
        assert pair[f"{metric}_ineligible_reason_counts"] == {
            "missing_committed_root_result": 1
        }
        assert pair["metric_metadata"][metric]["missing_reason"] == (
            "infrastructure_invalid_root_present"
        )


def test_exp3_infra_rate_reason_overrides_missing_slot_metadata_only(
    tmp_path: Path,
) -> None:
    store = RunStore(tmp_path / "exp3-infra-missing-slots")
    inventory = _inventory(
        "exp3",
        "infra-missing-slots",
        "infra-missing-slots-case",
        fault_type="false_positive",
    )
    result = _infrastructure_invalid_result_with_facts(
        inventory,
        attempt=_attempt("exp3", simulated_total_tokens=120),
        runtime_ms=15,
    )
    result.required_slot_count = None
    result.missing_reason["required_slot_count"] = "missing_required_slot_input"
    result.validate()
    store.write_frozen_inventories(
        conditions=[],
        roots=[inventory],
        exp3_references=[],
        exp4_challenges=[],
    )
    store.write_root_result(result)

    reduce_run(store.run_dir)

    rows = _read_rows(store.run_dir / "metrics" / "tables" / "exp3.jsonl")
    cell = next(row for row in rows if row["row_kind"] == "cell")
    for metric in (
        "recovered_valid_canonical_required_slots",
        "preregistered_required_slots",
    ):
        assert cell[metric] is None
        assert cell["missing_reasons"][metric] == "missing_required_slot_input"
    assert cell["result_completeness_rate"] is None
    assert cell["missing_reasons"]["result_completeness_rate"] == (
        "infrastructure_invalid_root_present"
    )
    assert cell["metric_metadata"]["result_completeness_rate"][
        "missing_reason"
    ] == "infrastructure_invalid_root_present"


def test_exp5_infrastructure_invalid_cell_preserves_actual_resource_facts(
    tmp_path: Path,
) -> None:
    store = RunStore(tmp_path / "exp5-infra-exact-facts")
    roots = [
        _inventory(
            "exp5",
            f"infra-model-repeat-{repeat_id}",
            f"infra-model-case-{repeat_id}",
            repeat_id=repeat_id,
            configured_model="zai-org/GLM-5.2",
        )
        for repeat_id in range(3)
    ]
    results = [
        _infrastructure_invalid_result_with_facts(
            inventory,
            attempt=_attempt("exp5", total_tokens=100, cost=1.0),
            runtime_ms=10 * (index + 1),
        )
        for index, inventory in enumerate(roots)
    ]
    store.write_frozen_inventories(
        conditions=[],
        roots=roots,
        exp3_references=[],
        exp4_challenges=[],
    )
    for result in results:
        store.write_root_result(result)

    reduce_run(store.run_dir)

    row = _read_rows(store.run_dir / "metrics" / "tables" / "exp5.jsonl")[0]
    assert row["actual_first_provider_attempt_count"] == 3
    assert row["first_attempt_without_verifier_accepted_candidate_count"] == 0
    assert row["first_attempt_provider_transport_failure_count"] == 0
    assert row["first_attempt_parse_schema_unusable_count"] == 0
    assert row["first_attempt_verification_checker_rejection_count"] == 0
    assert row["first_attempt_checkable_candidate_count"] == 3
    assert row["first_attempt_explicitly_rejected_by_verifier_count"] == 0
    assert row["planned_first_attempt_ai_unit_count"] == 3
    assert row["actual_total_tokens"] == 300
    assert row["actual_cost_estimate_cny"] == pytest.approx(3.0)
    assert row["repeat0_wall_clock_ms"] == 10
    assert row["repeat1_wall_clock_ms"] == 20
    assert row["repeat2_wall_clock_ms"] == 30
    assert row["model_wall_clock_median_ms"] == 20
    assert row["model_wall_clock_min_ms"] == 10
    assert row["model_wall_clock_max_ms"] == 30
    assert row["model_wall_clock_range_ms"] == 20
    for metric in (
        "completion_rate",
        "end_to_end_verified_success_rate",
        "first_attempt_nonpass_rate",
        "first_attempt_verification_rejection_rate",
        "first_attempt_call_coverage",
    ):
        assert row[metric] is None
        assert row["missing_reasons"][metric] == (
            "infrastructure_invalid_root_present"
        )
        for suffix in (
            "ci95_low",
            "ci95_high",
            "bootstrap_variance",
            "bootstrap_standard_error",
        ):
            assert row[f"{metric}_{suffix}"] is None


def test_exp5_infrastructure_invalid_cell_keeps_missing_usage_null(
    tmp_path: Path,
) -> None:
    store = RunStore(tmp_path / "exp5-infra-missing-usage")
    roots = [
        _inventory(
            "exp5",
            f"infra-missing-repeat-{repeat_id}",
            f"infra-missing-case-{repeat_id}",
            repeat_id=repeat_id,
            configured_model="zai-org/GLM-5.2",
        )
        for repeat_id in range(3)
    ]
    for index, inventory in enumerate(roots):
        attempt = _attempt(
            "exp5",
            total_tokens=(None if index == 0 else 100),
            cost=(None if index == 0 else 1.0),
        )
        store.write_root_result(
            _infrastructure_invalid_result_with_facts(
                inventory,
                attempt=attempt,
                runtime_ms=10 * (index + 1),
            )
        )
    store.write_frozen_inventories(
        conditions=[],
        roots=roots,
        exp3_references=[],
        exp4_challenges=[],
    )

    reduce_run(store.run_dir)

    row = _read_rows(store.run_dir / "metrics" / "tables" / "exp5.jsonl")[0]
    for metric in ("actual_total_tokens", "actual_cost_estimate_cny"):
        assert row[metric] is None
        assert row["missing_reasons"][metric] == "usage_missing"


def test_exp5_parsed_unsubmitted_attempt_nulls_only_nonpass_quality_metrics(
    tmp_path: Path,
) -> None:
    store = RunStore(tmp_path / "exp5-parsed-unsubmitted")
    root = _inventory(
        "exp5",
        "parsed-unsubmitted-model",
        "parsed-unsubmitted-case",
        configured_model="zai-org/GLM-5.2",
    )
    attempt = _attempt("exp5")
    attempt.result_kind = "parsed"
    attempt.parse_result = "parsed"
    attempt.verifier_result = None
    attempt.checker_result = None
    attempt.canonical_accepted = False
    attempt.missing_reason["attempts[].verifier_result"] = (
        "not_applicable_or_unavailable"
    )
    attempt.validate(experiment_id="exp5")
    store.write_frozen_inventories(
        conditions=[], roots=[root], exp3_references=[], exp4_challenges=[]
    )
    store.write_root_result(
        _result(root, final=False, verified=False, attempt=attempt)
    )

    reduce_run(store.run_dir)

    row = _read_rows(store.run_dir / "metrics" / "tables" / "exp5.jsonl")[0]
    assert row["actual_first_provider_attempt_count"] == 1
    assert row["first_attempt_call_coverage"] == 1.0
    assert row["actual_total_tokens"] == 100
    assert row["actual_cost_estimate_cny"] == pytest.approx(1.0)
    assert row["first_attempt_provider_transport_failure_count"] == 0
    assert row["first_attempt_parse_schema_unusable_count"] == 0
    assert row["first_attempt_verification_checker_rejection_count"] == 0
    assert row["first_attempt_checkable_candidate_count"] == 0
    assert row["first_attempt_explicitly_rejected_by_verifier_count"] == 0
    for metric in (
        "first_attempt_without_verifier_accepted_candidate_count",
        "first_attempt_nonpass_rate",
    ):
        assert row[metric] is None
        assert row["not_applicable_reasons"][metric] == (
            "not_applicable_or_unavailable"
        )
    for suffix in (
        "ci95_low",
        "ci95_high",
        "bootstrap_variance",
        "bootstrap_standard_error",
    ):
        assert row[f"first_attempt_nonpass_rate_{suffix}"] is None
    assert row["metric_metadata"]["first_attempt_nonpass_rate"][
        "missing_reason"
    ] == "not_applicable_or_unavailable"
    assert row["first_attempt_verification_rejection_rate"] is None
    assert row["missing_reasons"]["first_attempt_verification_rejection_rate"] == (
        "zero_denominator"
    )


def test_exp5_parsed_unsubmitted_does_not_pollute_verification_rate_bootstrap(
    tmp_path: Path,
) -> None:
    store = RunStore(tmp_path / "exp5-parsed-unsubmitted-bootstrap")
    roots = [
        _inventory(
            "exp5",
            "parsed-unsubmitted-bootstrap-model",
            f"parsed-unsubmitted-bootstrap-case-{index}",
            configured_model="zai-org/GLM-5.2",
        )
        for index in range(6)
    ]
    parsed = _attempt("exp5")
    parsed.result_kind = "parsed"
    parsed.parse_result = "parsed"
    parsed.verifier_result = None
    parsed.checker_result = None
    parsed.canonical_accepted = False
    parsed.missing_reason["attempts[].verifier_result"] = (
        "not_applicable_or_unavailable"
    )
    parsed.validate(experiment_id="exp5")
    results = [_result(roots[0], final=False, verified=False, attempt=parsed)]
    for index, root in enumerate(roots[1:], start=1):
        attempt = _attempt("exp5")
        if index in {2, 4}:
            attempt.verifier_result = "rejected"
            attempt.canonical_accepted = False
            attempt.validate(experiment_id="exp5")
            results.append(_result(root, final=False, verified=False, attempt=attempt))
        else:
            results.append(_result(root, attempt=attempt))
    store.write_frozen_inventories(
        conditions=[], roots=roots, exp3_references=[], exp4_challenges=[]
    )
    for result in results:
        store.write_root_result(result)

    reduce_run(store.run_dir)

    row = _read_rows(store.run_dir / "metrics" / "tables" / "exp5.jsonl")[0]
    metric = "first_attempt_verification_rejection_rate"
    assert row[metric] == pytest.approx(2 / 5)
    for suffix in (
        "ci95_low",
        "ci95_high",
        "bootstrap_variance",
        "bootstrap_standard_error",
    ):
        assert row[f"{metric}_{suffix}"] is not None
    assert row["metric_metadata"][metric]["case_cluster_count"] == 5
    assert row["metric_metadata"][metric]["valid_bootstrap_replicate_count"] == (
        10_000
    )


def test_exp5_single_repeat_keeps_wall_clock_summary_schema(
    tmp_path: Path,
) -> None:
    store = RunStore(tmp_path / "exp5-single-repeat")
    root = _inventory(
        "exp5",
        "single-repeat-model",
        "single-repeat-case",
        configured_model="zai-org/GLM-5.2",
    )
    store.write_frozen_inventories(
        conditions=[], roots=[root], exp3_references=[], exp4_challenges=[]
    )
    store.write_root_result(_result(root, runtime_ms=25, attempt=_attempt("exp5")))

    reduce_run(store.run_dir)

    row = _read_rows(store.run_dir / "metrics" / "tables" / "exp5.jsonl")[0]
    assert row["repeat0_wall_clock_ms"] == 25
    assert row["repeat1_wall_clock_ms"] is None
    assert row["repeat2_wall_clock_ms"] is None
    for metric in ("repeat1_wall_clock_ms", "repeat2_wall_clock_ms"):
        assert row["not_applicable_reasons"][metric] == (
            "not_applicable_or_unavailable"
        )
    assert row["repeat_wall_clock_ms"] == [25]
    assert row["model_wall_clock_median_ms"] == 25
    assert row["model_wall_clock_min_ms"] == 25
    assert row["model_wall_clock_max_ms"] == 25
    assert row["model_wall_clock_range_ms"] == 0
    assert row["model_wall_clock_sample_stddev_ms"] is None
    assert row["missing_reasons"]["model_wall_clock_sample_stddev_ms"] == (
        "insufficient_observations_for_sample_variance"
    )


def test_exp4_normal_retry_exhaustion_remains_scientifically_valid(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "normal-exhaustion-exp4")
    modes = ("FULL", "NO_VERIFICATION")
    roots = [
        _inventory(
            "exp4",
            f"normal-{mode}",
            "normal-exhaustion-case",
            mode=mode,
            challenge_plan_id="normal-plan",
        )
        for mode in modes
    ]
    full = _result(roots[0], attempt=_attempt("exp4"))
    exhausted = _result(
        roots[1],
        attempt=_attempt("exp4"),
        final=False,
        verified=False,
    )
    exhausted.failure_origin = "model_parse_exhausted"
    exhausted.validate()
    store.write_frozen_inventories(
        conditions=[],
        roots=roots,
        exp3_references=[],
        exp4_challenges=[
            _challenge_plan("normal-exhaustion-case", plan_id="normal-plan")
        ],
    )
    store.write_root_result(full)
    store.write_root_result(exhausted)

    reduce_run(store.run_dir)

    rows = _read_rows(store.run_dir / "metrics" / "tables" / "exp4.jsonl")
    cell = next(
        row
        for row in rows
        if row["row_kind"] == "cell" and row["slice"]["mode"] == "NO_VERIFICATION"
    )
    pair = next(row for row in rows if row["row_kind"] == "pair")
    assert cell["scientifically_valid_ablation_cell"] is True
    assert cell["failure_origin_counts"] == {"model_parse_exhausted": 1}
    assert pair["planned_pair_count"] == 1
    assert pair["runtime_valid_pair_count"] == 1
    assert pair["runtime_invalid_pair_count"] == 0

    invalid_run = tmp_path / "missing-verification-run"
    invalid_store = RunStore(invalid_run)
    invalid_root = _inventory(
        "exp5", "missing-verification", "missing-verification",
        configured_model="zai-org/GLM-5.2",
    )
    invalid_attempt = _attempt("exp5")
    invalid_attempt.verifier_result = None
    invalid_attempt.validate(experiment_id="exp5")
    invalid_store.write_frozen_inventories(
        conditions=[], roots=[invalid_root], exp3_references=[],
        exp4_challenges=[],
    )
    invalid_store.write_root_result(
        _result(invalid_root, attempt=invalid_attempt)
    )
    with pytest.raises(ValueError, match="missing first-attempt verification evidence"):
        reduce_run(invalid_run)


def test_exp4_conditional_failure_bootstrap_recomputes_matched_denominator(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    store = RunStore(run_dir)
    roots: list[RootInventoryV1] = []
    results: list[RootResultV2] = []
    challenges = []
    for index in range(5):
        case_id = f"bootstrap-case-{index}"
        challenges.append(_challenge_plan(case_id, plan_id=f"plan-{index}"))
        for mode in ("FULL", "NO_VERIFICATION"):
            root = _inventory(
                "exp4", f"bootstrap-{index}-{mode}", case_id,
                mode=mode, challenge_plan_id=f"plan-{index}",
            )
            roots.append(root)
            failed = mode == "NO_VERIFICATION" and index in {0, 1}
            results.append(
                _result(
                    root, final=not failed, verified=not failed,
                    attempt=_attempt("exp4"),
                )
            )
    store.write_frozen_inventories(
        conditions=[], roots=roots, exp3_references=[], exp4_challenges=challenges
    )
    for result in results:
        store.write_root_result(result)
    reduce_run(run_dir)
    pair = next(
        row for row in _read_rows(run_dir / "metrics" / "tables" / "exp4.jsonl")
        if row["row_kind"] == "pair"
    )
    metric = "ablation_failure_given_full_success_rate"
    assert pair[metric] == pytest.approx(0.4)
    assert pair[f"{metric}_ci95_low"] is not None
    assert pair[f"{metric}_bootstrap_variance"] is not None
    assert pair["metric_metadata"][metric]["case_cluster_count"] == 5
    assert pair["metric_metadata"][metric]["valid_bootstrap_replicate_count"] == 10_000


def test_exp1_missing_protocol_runtime_carries_direct_cell_reason(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    store = RunStore(run_dir)
    root = _inventory("exp1", "exp1-hard", "exp1-invalid")
    store.write_frozen_inventories(
        conditions=[],
        roots=[root],
        exp3_references=[],
        exp4_challenges=[],
    )
    store.write_root_result(_preflight_blocked_result(root))

    reduce_run(run_dir)

    row = _read_rows(run_dir / "metrics" / "tables" / "exp1.jsonl")[0]
    assert row["actual_end_to_end_wall_clock_ms"] is None
    assert row["missing_reasons"]["actual_end_to_end_wall_clock_ms"] == (
        "missing_root_runtime"
    )
    assert row["root_end_to_end_elapsed_ms"] is None
    assert row["missing_reasons"]["root_end_to_end_elapsed_ms"] == (
        "insufficient_observations_for_sample_variance"
    )


def test_required_slot_missing_nulls_the_whole_exp4_cell_interval(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    store = RunStore(run_dir)
    roots: list[RootInventoryV1] = []
    results: list[RootResultV2] = []
    challenges = []
    for index in range(5):
        case_id = f"slot-cell-{index}"
        plan_id = f"slot-plan-{index}"
        root = _inventory(
            "exp4", f"slot-condition-{index}", case_id,
            mode="FULL", challenge_plan_id=plan_id,
        )
        result = _result(root, attempt=_attempt("exp4"))
        if index == 0:
            result.required_slot_count = None
            result.recovered_valid_canonical_slot_count = None
            result.validate()
        roots.append(root)
        results.append(result)
        challenges.append(_challenge_plan(case_id, plan_id=plan_id))
    store.write_frozen_inventories(
        conditions=[], roots=roots, exp3_references=[],
        exp4_challenges=challenges,
    )
    for result in results:
        store.write_root_result(result)

    reduce_run(run_dir)
    cell = next(
        row for row in _read_rows(run_dir / "metrics" / "tables" / "exp4.jsonl")
        if row["row_kind"] == "cell"
    )
    metric = "required_slot_completion_rate"
    assert cell[metric] is None
    assert cell["missing_reasons"][metric] == "missing_required_slot_input"
    for suffix in (
        "ci95_low", "ci95_high", "bootstrap_variance",
        "bootstrap_standard_error",
    ):
        assert cell[f"{metric}_{suffix}"] is None
    assert cell["metric_metadata"][metric]["missing_reason"] == (
        "missing_required_slot_input"
    )


def test_null_inputs_and_exp3_resource_semantics_propagate(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    store = RunStore(run_dir)
    roots: list[RootInventoryV1] = []
    results: list[RootResultV2] = []
    references: list[RootInventoryV1] = []
    for index in range(5):
        case_id = f"slot-case-{index}"
        fault = _inventory("exp3", f"fault-{index}", case_id, fault_type="false_positive")
        reference = _inventory("exp3", f"reference-{index}", case_id)
        attempt = _attempt("exp3", simulated_total_tokens=120)
        if index == 0:
            attempt.source_pricing_tier = None
        result = _result(fault, attempt=attempt)
        if index == 0:
            result.required_slot_count = None
            result.recovered_valid_canonical_slot_count = None
        result.validate()
        roots.append(fault)
        results.append(result)
        references.append(reference)
    exp2 = _inventory("exp2", "empty-worker-facts", "empty-worker-facts", worker_count=10)
    roots.append(exp2)
    results.append(_result(exp2, attempt=_attempt("exp2")))
    store.write_frozen_inventories(
        conditions=[], roots=roots, exp3_references=references, exp4_challenges=[]
    )
    for result in results:
        store.write_root_result(result)
    for reference in references:
        _write_reference(
            store,
            _result(reference, attempt=_attempt("exp3", simulated_total_tokens=100)),
        )
    summary = reduce_run(run_dir)
    exp2_row = next(
        row for row in _read_rows(run_dir / "metrics" / "tables" / "exp2.jsonl")
        if row["row_kind"] == "cell"
    )
    assert exp2_row["worker_utilization"] is None
    assert exp2_row["missing_reasons"]["worker_utilization"] == "missing_worker_execution_intervals"
    exp3_rows = _read_rows(run_dir / "metrics" / "tables" / "exp3.jsonl")
    cell = next(row for row in exp3_rows if row["row_kind"] == "cell")
    assert cell["result_completeness_rate"] is None
    assert cell["result_completeness_rate_ci95_low"] is None
    assert cell["missing_reasons"]["result_completeness_rate"] == "missing_required_slot_input"
    pair = next(row for row in exp3_rows if row["row_kind"] == "fault_reference_pair")
    cost_metric = "simulated_trace_attributed_cost_overhead"
    assert pair[cost_metric] is not None
    assert pair[f"{cost_metric}_planned_pair_count"] == 5
    assert pair[f"{cost_metric}_eligible_pair_count"] == 4
    assert pair[f"{cost_metric}_ineligible_pair_count"] == 1
    assert pair[f"{cost_metric}_ci95_low"] is None
    assert all(row["resource_semantics"] == "simulated_trace_attributed" for row in exp3_rows)
    assert summary["resource_semantics"]["exp3"] == "simulated_trace_attributed"
    csv_text = (run_dir / "metrics" / "tables" / "exp3.csv").read_text(encoding="utf-8")
    assert "simulated_trace_attributed" in csv_text


def test_missing_required_evidence_suffix_blocks_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "run"
    _materialize_golden(run_dir)
    original = reducer_module._add_distribution

    def omit_required_suffix(*args: Any, **kwargs: Any) -> None:
        original(*args, **kwargs)
        row, metric = args[:2]
        if metric == "trace_replay_paired_speedup":
            row.pop("trace_replay_paired_speedup_median_ci95_low", None)

    monkeypatch.setattr(reducer_module, "_add_distribution", omit_required_suffix)
    with pytest.raises(ValueError, match="required evidence field"):
        reduce_run(run_dir)
    assert not (run_dir / "metrics" / "summary.json").exists()


def test_late_formula_error_keeps_prior_complete_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "run"
    _materialize_golden(run_dir)
    reduce_run(run_dir)
    before = {
        path.relative_to(run_dir): path.read_bytes()
        for path in (run_dir / "metrics").rglob("*") if path.is_file()
    }
    original_exp1 = reducer_module._reduce_exp1

    def changed_exp1(observations: Any) -> list[dict[str, Any]]:
        rows = original_exp1(observations)
        if rows:
            rows[0]["actual_total_tokens"] = 999999
        return rows

    def fail_exp5(_observations: Any) -> list[dict[str, Any]]:
        raise ValueError("late exp5 formula failure")

    monkeypatch.setattr(reducer_module, "_reduce_exp1", changed_exp1)
    monkeypatch.setattr(reducer_module, "_reduce_exp5", fail_exp5)
    with pytest.raises(ValueError, match="late exp5 formula failure"):
        reduce_run(run_dir)
    after = {
        path.relative_to(run_dir): path.read_bytes()
        for path in (run_dir / "metrics").rglob("*") if path.is_file()
    }
    assert after == before


def test_commit_failure_removes_summary_and_retry_republishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "run"
    _materialize_golden(run_dir)
    expected = reduce_run(run_dir)
    original_replace = Path.replace
    failed = False

    def fail_one_commit(path: Path, target: Path):
        nonlocal failed
        if target.name == "exp3.jsonl" and not failed:
            failed = True
            raise OSError("injected table commit failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_one_commit)
    with pytest.raises(OSError, match="injected table commit failure"):
        reduce_run(run_dir)
    assert not (run_dir / "metrics" / "summary.json").exists()
    monkeypatch.setattr(Path, "replace", original_replace)
    assert reduce_run(run_dir) == expected
    assert (run_dir / "metrics" / "summary.json").exists()


def test_reduce_writes_an_experiment_before_scanning_the_next(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "run"
    store = RunStore(run_dir)
    exp1 = _inventory("exp1", "stream-exp1", "stream-exp1", difficulty="easy")
    exp2 = _inventory("exp2", "stream-exp2", "stream-exp2", worker_count=1)
    store.write_frozen_inventories(
        conditions=[], roots=[exp1, exp2], exp3_references=[], exp4_challenges=[]
    )
    store.write_root_result(_result(exp1, attempt=_attempt("exp1")))
    store.write_root_result(_result(exp2, attempt=_attempt("exp2")))
    events: list[str] = []
    original_read = RunStore.read_root_result
    original_open = Path.open

    def spy_read(
        self: RunStore,
        experiment_id: str,
        condition_id: str,
        case_id: str,
        repeat_id: int,
    ):
        events.append(f"scan:{experiment_id}")
        return original_read(
            self, experiment_id, condition_id, case_id, repeat_id
        )

    def spy_open(path: Path, *args: Any, **kwargs: Any):
        mode = args[0] if args else kwargs.get("mode", "r")
        if "x" in mode and "exp1.jsonl" in path.name:
            events.append("stage:exp1")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(RunStore, "read_root_result", spy_read)
    monkeypatch.setattr(Path, "open", spy_open)
    summary = reduce_run(run_dir)
    assert events.index("stage:exp1") < events.index("scan:exp2")
    contract = summary["formal_metric_occurrences"]
    assert len(contract) == 153
    tables = {
        name: _read_rows(run_dir / "metrics" / "tables" / f"{name}.jsonl")
        for name in ("exp1", "exp2", "exp3", "exp4", "exp5")
    }
    for occurrence in contract:
        row_kinds = occurrence["row_kind"]
        if isinstance(row_kinds, str):
            row_kinds = [row_kinds]
        candidates = [
            row for row in tables[occurrence["table_scope"]]
            if row["row_kind"] in row_kinds
        ]
        if not candidates:
            assert occurrence["production_status"] == "authority_legal_not_observed"
            assert occurrence["production_reason"] == (
                "no_preregistered_rows_for_occurrence_scope"
            )
            continue
        assert occurrence["production_status"] == "computed_or_legal_null"
        metric = occurrence["metric_id"]
        assert any(
            metric in row
            and (
                row[metric] is not None
                or metric in row["missing_reasons"]
                or metric in row["not_applicable_reasons"]
            )
            and row["missing_reasons"].get(metric) != "metric_inputs_not_present_in_this_run"
            for row in candidates
        )
