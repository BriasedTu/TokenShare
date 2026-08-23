from __future__ import annotations

from dataclasses import asdict, replace
from collections import Counter
import csv
import json
import os
from pathlib import Path
import threading
from typing import Any

import pytest

from tokenshare.experiments.slim_v2.schema import (
    AblationObservationV1,
    AttemptResultV1,
    ProviderEntryViewV1,
    RootInventoryV1,
    RootResultV2,
    UnitTraceV1,
)


def _nullable_reasons(record_type: type[Any]) -> dict[str, str]:
    return {
        path: "offline_fixture_not_applicable"
        for path in record_type.nullable_leaf_paths()
    }


def _success(inventory: RootInventoryV1, challenge: object | None) -> RootResultV2:
    challenge_family = getattr(challenge, "challenge_family", None)
    target_rule = getattr(challenge, "attempt_rule", None)
    is_exp1 = inventory.experiment_id == "exp1"
    is_exp2 = inventory.experiment_id == "exp2"
    is_exp34 = inventory.experiment_id in {"exp3", "exp4"}
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
        challenge_family=challenge_family,
        challenge_target_planned_ai_unit_ids=(
            [inventory.planned_ai_unit_ids[0]]
            if inventory.experiment_id == "exp4"
            else None
        ),
        challenge_attempt_ordinal_rule=(
            target_rule if inventory.experiment_id == "exp4" else None
        ),
        provider_family=(
            "deepseek"
            if inventory.experiment_id != "exp5"
            else "siliconflow"
        ),
        provider_entry_id=inventory.provider_entry_id,
        configured_model=inventory.configured_model,
        requested_model=inventory.configured_model,
        resolved_model=inventory.configured_model,
        reasoning_mode="offline_fixture",
        root_start_at_ms=1,
        root_terminal_at_ms=2,
        runtime_wall_clock_ms=1,
        trace_tail_started_at_ms=None,
        trace_tail_terminal_at_ms=None,
        trace_tail_wall_clock_ms=0 if is_exp1 else None,
        trace_tail_status="not_needed" if is_exp1 else None,
        trace_tail_target_ai_unit_ids=[] if is_exp1 else None,
        trace_tail_recorded_ai_unit_ids=[] if is_exp1 else None,
        trace_tail_success_unit_count=0 if is_exp1 else None,
        trace_tail_failure_unit_count=0 if is_exp1 else None,
        trace_tail_provider_attempt_count=0 if is_exp1 else None,
        trace_tail_total_tokens=0 if is_exp1 else None,
        trace_tail_cost_estimate_cny=0.0 if is_exp1 else None,
        preflight_status="passed",
        protocol_started=True,
        root_status="completed",
        final_result_present=True,
        verified_correct=True,
        failure_stage=None,
        failure_kind=None,
        planned_ai_unit_ids=list(inventory.planned_ai_unit_ids),
        dispatched_ai_unit_ids=list(inventory.planned_ai_unit_ids),
        completed_ai_unit_ids=list(inventory.planned_ai_unit_ids),
        unscheduled_ai_unit_ids=[],
        in_flight_ai_unit_ids_at_witness=[] if is_exp2 else None,
        observed_peak_concurrency=1 if is_exp2 else None,
        worker_execution_facts=[],
        required_slot_count=len(inventory.planned_ai_unit_ids) if is_exp34 else None,
        recovered_valid_canonical_slot_count=(
            len(inventory.planned_ai_unit_ids) if is_exp34 else None
        ),
        attempts=[],
        fault_target_planned_ai_unit_ids=(
            [] if inventory.experiment_id == "exp3" else None
        ),
        fault_target_count=0 if inventory.experiment_id == "exp3" else None,
        fault_observations=[],
        recovery_observations=[],
        worker_death_observations=[],
        challenge_observations=[],
        ablation_observations=[
            AblationObservationV1(disabled_mechanism=name)
            for name in inventory.disabled_mechanisms
        ],
        missing_reason={},
        not_applicable_reason=_nullable_reasons(RootResultV2),
    )
    result.validate()
    return result


def _source_attempt(case_id: str, planned: str) -> AttemptResultV1:
    reasons = _nullable_reasons(AttemptResultV1)
    reasons.update(
        {f"attempts[].{path}": value for path, value in reasons.items()}
    )
    attempt = AttemptResultV1(
        attempt_id=f"source:{planned}:0",
        unit_id=f"unit:{planned}",
        planned_ai_unit_id=planned,
        attempt_ordinal=0,
        trace_origin="protocol",
        started_at_ms=1,
        ended_at_ms=2,
        result_kind="success",
        provider_call_made=True,
        http_status=200,
        provider_latency_ms=1,
        raw_response_present=True,
        raw_response_relative_path=f"responses/{case_id}-{planned}.json",
        parse_result="accepted",
        verifier_result="passed",
        canonical_accepted=True,
        prompt_tokens=1,
        prompt_cache_hit_tokens=0,
        prompt_cache_miss_tokens=1,
        completion_tokens=1,
        reasoning_tokens=0,
        total_tokens=2,
        provider_request_started_at_utc="2026-08-22T00:00:00Z",
        pricing_version="slim_v2.pricing.2026-08-23",
        pricing_tier="flat",
        cost_estimate_cny=0.0,
        usage_status="complete",
        call_state="terminal",
        missing_reason=reasons,
    )
    attempt.validate(experiment_id="exp1")
    return attempt


def _write_source_traces(context: object) -> None:
    inventory = context.inventory
    if inventory.experiment_id != "exp1":
        return
    for planned in inventory.planned_ai_unit_ids:
        candidate_start, candidate_end, lemma_node_id, dependency_path = (
            _source_trace_semantics(inventory, context.root_input, planned)
        )
        trace = UnitTraceV1(
            case_id=inventory.case_id,
            planned_ai_unit_id=planned,
            domain=inventory.domain,
            trace_origin="protocol",
            candidate_start=candidate_start,
            candidate_end=candidate_end,
            lemma_node_id=lemma_node_id,
            dependency_path=dependency_path,
            provider_family="deepseek",
            provider_entry_id="deepseek_v4_flash_exp1_baseline",
            configured_model="deepseek-v4-flash",
            requested_model="deepseek-v4-flash",
            resolved_model="deepseek-v4-flash",
            attempts=[_source_attempt(inventory.case_id, planned)],
        )
        context.run_store.write_trace(trace)
        context.run_store.write_response(
            f"{inventory.case_id}-{planned}",
            {
                "body": {
                    "choices": [{"message": {"content": "offline source fixture"}}]
                }
            },
        )


def _source_trace_semantics(
    inventory: RootInventoryV1,
    case: dict[str, Any],
    planned: str,
) -> tuple[int | None, int | None, str | None, list[str] | None]:
    if inventory.domain == "factorization":
        from tokenshare.plugins.factorization import partition_candidate_ranges

        split = case["split_params"]
        partition = partition_candidate_ranges(
            target_n=case["target_n"],
            requested_child_count=split["requested_child_count"],
            max_children_per_unit=case["candidate_divisor_count"],
            min_divisor=case["candidate_start"],
            max_divisor=case["candidate_end"],
        )
        item = next(
            item for item in partition.ranges if planned == f"range_{item.child_index}"
        )
        return int(item.range_start), int(item.range_end), None, None
    from tokenshare.plugins.lean_proof.fixed_plan import LeanFixedDecompositionPlan

    plan = LeanFixedDecompositionPlan.from_catalog_case(case)
    assert planned in plan.topological_order()
    return None, None, planned, list(plan.dependency_path(planned))


def _write_factor_source(store: object, inventory: RootInventoryV1) -> None:
    from tokenshare.experiments.slim_v2 import cli

    case = cli._case_inputs()[inventory.case_id]
    for planned in inventory.planned_ai_unit_ids:
        candidate_start, candidate_end, lemma_node_id, dependency_path = (
            _source_trace_semantics(inventory, case, planned)
        )
        trace = UnitTraceV1(
            case_id=inventory.case_id,
            planned_ai_unit_id=planned,
            domain="factorization",
            trace_origin="protocol",
            candidate_start=candidate_start,
            candidate_end=candidate_end,
            lemma_node_id=lemma_node_id,
            dependency_path=dependency_path,
            provider_family="deepseek",
            provider_entry_id=inventory.provider_entry_id,
            configured_model=inventory.configured_model,
            requested_model=inventory.configured_model,
            resolved_model=inventory.configured_model,
            attempts=[_source_attempt(inventory.case_id, planned)],
        )
        store.write_trace(trace)
        store.write_response(
            f"{inventory.case_id}-{planned}",
            {"body": {"choices": [{"message": {"content": "source"}}]}},
        )


def _fake_services(cli, calls: list[tuple[str, str, Path | None]]):
    def execute(context):
        calls.append(
            (
                context.inventory.experiment_id,
                context.inventory.case_id,
                context.source_run_dir,
            )
        )
        _write_source_traces(context)
        return _success(context.inventory, context.challenge_plan)

    return cli._CliServices(
        execute_root=execute,
        resume_root=lambda context, protocol: pytest.fail(
            "fresh execution cannot enter the protocol resume seam"
        ),
        reduce_run=lambda run_dir: {"run_dir": str(run_dir), "reduced": True},
        provider_preflight=lambda experiment_ids, roots: {},
        disk_free_bytes=lambda path: 1 << 50,
        lean_environment_preflight=lambda _root: {"status": "passed"},
    )


def test_provider_condition_failure_blocks_later_roots_by_failure_origin(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2 import cli
    from tokenshare.experiments.slim_v2.storage import RunStore

    calls: list[tuple[str, str]] = []
    blocked_condition: str | None = None
    services = _fake_services(cli, [])

    def execute(context):
        nonlocal blocked_condition
        identity = (
            str(context.inventory.condition_id),
            str(context.inventory.case_id),
        )
        calls.append(identity)
        if blocked_condition is None:
            blocked_condition = identity[0]
            return cli._invalid_root_result(
                context.inventory,
                context.challenge_plan,
                failure_origin="provider_configuration_invalid",
            )
        assert identity[0] != blocked_condition, (
            "provider condition fail-stop reached a later root"
        )
        return _success(context.inventory, context.challenge_plan)

    services = replace(services, execute_root=execute)
    assert cli.main(
        [
            "run",
            "--experiment",
            "exp1",
            "--profile",
            "representative",
            "--run-id",
            "condition-fail-stop",
            "--output-root",
            str(tmp_path),
        ],
        _services=services,
    ) == 0
    assert blocked_condition is not None
    assert sum(condition == blocked_condition for condition, _case in calls) == 1
    blocked_results = [
        result
        for inventory, result in RunStore(
            tmp_path / "condition-fail-stop"
        ).iter_inventory_results("roots")
        if str(inventory.condition_id) == blocked_condition
    ]
    assert len(blocked_results) > 1
    assert all(result is not None for result in blocked_results)
    assert {
        result.failure_origin for result in blocked_results if result is not None
    } == {"provider_configuration_invalid"}


def test_invalid_lean_environment_is_recorded_before_provider_without_blocking_factorization(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2 import cli
    from tokenshare.experiments.slim_v2.lean_environment import LeanEnvironmentInvalid
    from tokenshare.experiments.slim_v2.storage import RunStore

    executed: list[str] = []
    provider_domains: list[set[str]] = []
    services = _fake_services(cli, [])
    services = replace(
        services,
        execute_root=lambda context: (
            executed.append(str(context.inventory.domain))
            or _success(context.inventory, context.challenge_plan)
        ),
        provider_preflight=lambda _experiments, roots: (
            provider_domains.append({str(root.domain) for root in roots}) or {}
        ),
        lean_environment_preflight=lambda _root: (_ for _ in ()).throw(
            LeanEnvironmentInvalid("missing pass")
        ),
    )

    assert cli.main(
        [
            "run",
            "--experiment",
            "exp1",
            "--profile",
            "representative",
            "--run-id",
            "invalid-lean-environment",
            "--output-root",
            str(tmp_path),
        ],
        _services=services,
    ) == 0

    assert executed and set(executed) == {"factorization"}
    assert provider_domains == [{"factorization"}]
    results = [
        result
        for inventory, result in RunStore(
            tmp_path / "invalid-lean-environment"
        ).iter_inventory_results("roots")
        if inventory.domain == "lean"
    ]
    assert results
    assert all(result is not None for result in results)
    assert {result.failure_stage for result in results if result is not None} == {
        "preflight"
    }
    assert {result.failure_kind for result in results if result is not None} == {
        "infrastructure_invalid"
    }
    assert {result.failure_origin for result in results if result is not None} == {
        "lean_environment_invalid"
    }


def test_invalid_lean_environment_excludes_lean_roots_from_shared_source_closure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments.slim_v2 import cli
    from tokenshare.experiments.slim_v2.lean_environment import LeanEnvironmentInvalid
    from tokenshare.experiments.slim_v2.storage import RunStore

    closure_groups: list[tuple[str, ...]] = []
    executed: list[str] = []

    def record_source_closure(roots, _source_run_dir, _cases) -> None:
        closure_groups.append(tuple(str(root.domain) for root in roots))

    monkeypatch.setattr(cli, "_source_closure", record_source_closure)
    services = replace(
        _fake_services(cli, []),
        execute_root=lambda context: (
            executed.append(str(context.inventory.domain))
            or _success(context.inventory, context.challenge_plan)
        ),
        lean_environment_preflight=lambda _root: (_ for _ in ()).throw(
            LeanEnvironmentInvalid("missing pass")
        ),
    )

    assert cli.main(
        [
            "run",
            "--experiment",
            "exp4",
            "--profile",
            "representative",
            "--run-id",
            "invalid-lean-fixed-source",
            "--source-run-dir",
            str(tmp_path / "source"),
            "--output-root",
            str(tmp_path),
        ],
        _services=services,
    ) == 0

    assert len(closure_groups) == 11
    assert sum(len(group) for group in closure_groups) == 22
    assert all(set(group) == {"factorization"} for group in closure_groups)
    assert executed and set(executed) == {"factorization"}
    results = list(
        RunStore(tmp_path / "invalid-lean-fixed-source").iter_inventory_results(
            "roots"
        )
    )
    assert sum(
        result is not None
        and result.failure_origin == "lean_environment_invalid"
        for inventory, result in results
        if inventory.domain == "lean"
    ) == 22
    assert all(
        result is not None and result.verified_correct is True
        for inventory, result in results
        if inventory.domain == "factorization"
    )


def test_atomic_source_is_explicit_and_run_all_orders_dependencies_and_reducer(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2 import cli

    with pytest.raises(SystemExit):
        cli.main(
            [
                "run",
                "--experiment",
                "exp2",
                "--profile",
                "representative",
                "--run-id",
                "missing-source",
                "--output-root",
                str(tmp_path),
            ]
        )

    calls: list[tuple[str, str, Path | None]] = []
    reduced: list[Path] = []
    services = replace(
        _fake_services(cli, calls),
        reduce_run=lambda run_dir: reduced.append(Path(run_dir)) or {"ok": True},
    )
    assert cli.main(
        [
            "run-all",
            "--profile",
            "representative",
            "--run-id",
            "ordered",
            "--output-root",
            str(tmp_path),
        ],
        _services=services,
    ) == 0

    experiments = [item[0] for item in calls]
    assert experiments == sorted(experiments, key=("exp1", "exp2", "exp3", "exp4", "exp5").index)
    assert {item[2] for item in calls if item[0] in {"exp2", "exp3", "exp4"}} == {
        tmp_path / "ordered"
    }
    assert len(calls) == 73
    assert reduced == [tmp_path / "ordered"]

    atomic_calls: list[tuple[str, str, Path | None]] = []
    assert cli.main(
        [
            "run",
            "--experiment",
            "exp2",
            "--profile",
            "representative",
            "--run-id",
            "atomic-exp2",
            "--source-run-dir",
            str(tmp_path / "ordered"),
            "--output-root",
            str(tmp_path),
        ],
        _services=_fake_services(cli, atomic_calls),
    ) == 0
    assert len(atomic_calls) == 12
    assert {item[0] for item in atomic_calls} == {"exp2"}
    assert {item[2] for item in atomic_calls} == {tmp_path / "ordered"}


def test_fresh_exp1_run_persists_only_selected_inventory_and_reduces(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2 import cli
    from tokenshare.experiments.slim_v2.reducer import reduce_run
    from tokenshare.experiments.slim_v2.storage import RunStore

    run_id = "exp1-only"
    assert cli.main(
        [
            "run", "--experiment", "exp1", "--profile", "representative",
            "--run-id", run_id, "--output-root", str(tmp_path),
        ],
        _services=_fake_services(cli, []),
    ) == 0

    store = RunStore(tmp_path / run_id)
    for name in ("conditions", "roots"):
        rows = [
            json.loads(line)
            for line in store.inventory_path(name).read_text(encoding="utf-8").splitlines()
        ]
        assert rows
        assert {row["experiment_id"] for row in rows} == {"exp1"}
    assert store.inventory_path("exp3_references").read_text(encoding="utf-8") == ""
    assert store.inventory_path("exp4_challenges").read_text(encoding="utf-8") == ""

    summary = reduce_run(store.run_dir)
    assert summary["paper_root_inventory_counts"] == {
        "exp1": 4, "exp2": 0, "exp3": 0, "exp4": 0, "exp5": 0,
    }
    assert summary["tables"]["exp1"]["row_count"] > 0
    assert all(
        summary["tables"][experiment_id]["row_count"] == 0
        for experiment_id in ("exp2", "exp3", "exp4", "exp5")
    )


def test_representative_alias_and_reduce_have_no_second_runner(tmp_path: Path) -> None:
    from tokenshare.experiments.slim_v2 import cli

    calls: list[tuple[str, str, Path | None]] = []
    reduced: list[Path] = []
    services = replace(
        _fake_services(cli, calls),
        reduce_run=lambda run_dir: reduced.append(Path(run_dir)) or {"ok": True},
    )
    assert cli.main(
        [
            "representative",
            "--run-id",
            "representative-alias",
            "--output-root",
            str(tmp_path),
        ],
        _services=services,
    ) == 0
    experiments = [item[0] for item in calls]
    assert len(calls) == 73
    assert experiments == sorted(
        experiments, key=("exp1", "exp2", "exp3", "exp4", "exp5").index
    )
    assert reduced == [tmp_path / "representative-alias"]

    calls_before_reduce = list(calls)
    assert cli.main(
        ["reduce", "--run-dir", str(tmp_path / "representative-alias")],
        _services=services,
    ) == 0
    assert calls == calls_before_reduce
    assert reduced == [
        tmp_path / "representative-alias",
        tmp_path / "representative-alias",
    ]


def test_compare_exp5_v4_reference_is_explicit_and_does_not_start_a_runner(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2 import cli
    from tokenshare.experiments.slim_v2.profiles import (
        build_inventory,
        project_root_inventory_rows,
    )
    from tokenshare.experiments.slim_v2.storage import RunStore

    inventory = build_inventory("representative")
    projected = project_root_inventory_rows(inventory)
    target_roots = [
        root for root in projected.roots if root.experiment_id == "exp5"
    ]
    source_root = next(
        root
        for root in projected.roots
        if root.experiment_id == "exp1" and root.case_id == "factor_v2_hard_145"
    )
    target = RunStore(tmp_path / "target")
    source = RunStore(tmp_path / "source")
    target.write_frozen_inventories(
        conditions=inventory.conditions,
        roots=target_roots,
        exp3_references=[],
        exp4_challenges=[],
    )
    for root in target_roots:
        target.write_root_result(_success(root, None))
    source.write_frozen_inventories(
        conditions=inventory.conditions,
        roots=[source_root],
        exp3_references=[],
        exp4_challenges=[],
    )
    source.write_root_result(_success(source_root, None))

    assert cli.main(
        [
            "compare-exp5-v4-reference",
            "--run-dir",
            str(target.run_dir),
            "--source-run-dir",
            str(source.run_dir),
        ]
    ) == 0
    assert (
        target.run_dir
        / "metrics"
        / "supplemental"
        / "exp5_with_exp1_v4_reference.jsonl"
    ).is_file()
    assert not (target.run_dir / "metrics" / "summary.json").exists()


def test_main_reuses_public_run_experiment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2 import cli

    observed: list[dict[str, object]] = []

    def run_experiment(**kwargs: object) -> int:
        observed.append(dict(kwargs))
        return 23

    monkeypatch.setattr(cli, "run_experiment", run_experiment)
    assert cli.main(
        [
            "run", "--experiment", "exp5", "--profile", "representative",
            "--run-id", "public-runner", "--output-root", str(tmp_path),
        ],
        _services=_fake_services(cli, []),
    ) == 23
    assert observed[0]["experiment_ids"] == ("exp5",)


def test_public_run_experiment_supplies_its_own_default_services(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2 import cli

    calls: list[tuple[str, str, Path | None]] = []
    monkeypatch.setattr(cli, "_default_services", lambda: _fake_services(cli, calls))

    assert cli.run_experiment(
        experiment_ids=("exp5",),
        profile_id="representative",
        run_id="public-default-services",
        output_root=str(tmp_path),
        source_run_dir=None,
        resume=False,
        reduce_after=False,
    ) == 0
    assert len(calls) == 3


def test_roots_are_serial_and_resume_uses_only_committed_file_facts(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2 import cli

    active = False
    calls: list[str] = []

    def execute(context):
        nonlocal active
        assert active is False
        active = True
        calls.append(context.inventory.case_id)
        result = _success(context.inventory, context.challenge_plan)
        active = False
        return result

    services = replace(_fake_services(cli, []), execute_root=execute)
    argv = [
        "run",
        "--experiment",
        "exp5",
        "--profile",
        "representative",
        "--run-id",
        "resume",
        "--output-root",
        str(tmp_path),
    ]
    assert cli.main(argv, _services=services) == 0
    assert len(calls) == 3
    assert cli.main([*argv, "--resume"], _services=services) == 0
    assert len(calls) == 3

    from tokenshare.experiments.slim_v2.storage import RunStore

    store = RunStore(tmp_path / "resume")
    original_config = store.run_config_path().read_text(encoding="utf-8")
    changed = store.read_run_config()
    changed["run_id"] = "different-run"
    store.run_config_path().write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(RuntimeError, match="run config"):
        cli.main([*argv, "--resume"], _services=services)
    store.run_config_path().write_text(original_config, encoding="utf-8")

    first = next(store.iter_root_inventory_rows("roots"))
    result_path = store.root_result_path(
        first.experiment_id, first.condition_id, first.case_id, first.repeat_id
    )
    if first.experiment_id != "exp5":
        first = next(
            row
            for row in store.iter_root_inventory_rows("roots")
            if row.experiment_id == "exp5"
        )
        result_path = store.root_result_path(
            first.experiment_id, first.condition_id, first.case_id, first.repeat_id
        )
    result_path.write_text("{broken", encoding="utf-8")
    with pytest.raises((json.JSONDecodeError, ValueError)):
        cli.main([*argv, "--resume"], _services=services)
    assert len(calls) == 3


def test_resume_protocol_tail_and_started_without_protocol_never_rerun_root(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2 import cli
    from tokenshare.experiments.slim_v2.profiles import (
        build_inventory,
        project_root_inventory_rows,
    )
    from tokenshare.experiments.slim_v2.storage import RunStore

    rows = project_root_inventory_rows(build_inventory("representative")).roots
    exp1 = [row for row in rows if row.experiment_id == "exp1"]
    store = RunStore(tmp_path / "protocol-resume")
    store.write_frozen_inventories(
        conditions=[], roots=rows, exp3_references=[], exp4_challenges=[]
    )
    protocol_key = (
        exp1[0].experiment_id,
        exp1[0].condition_id,
        exp1[0].case_id,
        exp1[0].repeat_id,
    )
    from tokenshare.experiments.slim_v2.schema import SlimRunConfigV1

    store.write_run_config(
        asdict(
            SlimRunConfigV1(
                run_id="protocol-resume",
                profile_id="representative",
                experiment_ids=["exp1"],
                source_run_dir=None,
            )
        )
    )
    store.write_root_protocol_snapshot(
        *protocol_key,
        protocol_result={
            "summary": {"runtime_observation": {"unscheduled_ai_unit_ids": []}}
        },
        traces=[],
        tail_requests={},
        protocol_projection=_success(exp1[0], None),
    )
    started = exp1[1]
    started_dir = store.system_root_directory(
        started.experiment_id,
        started.condition_id,
        started.case_id,
        started.repeat_id,
    )
    started_dir.mkdir(parents=True)
    (started_dir / "events.jsonl").write_text("{\"started\":true}\n", encoding="utf-8")

    executed: list[str] = []
    resumed: list[str] = []

    def execute(context):
        executed.append(context.inventory.case_id)
        return _success(context.inventory, context.challenge_plan)

    def resume(context, protocol):
        resumed.append(context.inventory.case_id)
        return _success(context.inventory, context.challenge_plan)

    services = replace(
        _fake_services(cli, []), execute_root=execute, resume_root=resume
    )
    assert cli.main(
        [
            "run",
            "--experiment",
            "exp1",
            "--profile",
            "representative",
            "--run-id",
            "protocol-resume",
            "--output-root",
            str(tmp_path),
            "--resume",
        ],
        _services=services,
    ) == 0
    assert resumed == [exp1[0].case_id]
    assert started.case_id not in executed
    assert store.root_result_path(
        started.experiment_id,
        started.condition_id,
        started.case_id,
        started.repeat_id,
    ).is_file()


def test_protocol_resume_requires_secret_only_for_missing_tail_target(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2 import cli
    from tokenshare.experiments.slim_v2.profiles import (
        build_inventory,
        project_root_inventory_rows,
    )
    from tokenshare.experiments.slim_v2.schema import SlimRunConfigV1
    from tokenshare.experiments.slim_v2.storage import RunStore

    inventory = build_inventory("representative")
    rows = project_root_inventory_rows(inventory)
    root = next(row for row in rows.roots if row.experiment_id == "exp1")
    target = root.planned_ai_unit_ids[-1]

    def seed(run_id: str, *, completed_tail: bool) -> RunStore:
        store = RunStore(tmp_path / run_id)
        store.write_frozen_inventories(
            conditions=inventory.conditions,
            roots=rows.roots,
            exp3_references=rows.exp3_references,
            exp4_challenges=inventory.challenges,
        )
        store.write_run_config(
            asdict(
                SlimRunConfigV1(
                    run_id=run_id,
                    profile_id="representative",
                    experiment_ids=["exp1"],
                    source_run_dir=None,
                )
            )
        )
        store.write_root_protocol_snapshot(
            root.experiment_id,
            root.condition_id,
            root.case_id,
            root.repeat_id,
            protocol_result={
                "summary": {
                    "runtime_observation": {
                        "unscheduled_ai_unit_ids": [target]
                    }
                }
            },
            traces=[],
            tail_requests={target: {}},
            protocol_projection=_success(root, None),
        )
        for other in rows.roots:
            if other.experiment_id == "exp1" and other.case_id != root.case_id:
                store.write_root_result(_success(other, None))
        if completed_tail:
            attempt = replace(
                _source_attempt(root.case_id, target),
                trace_origin="coverage_tail",
            )
            store.write_trace(
                UnitTraceV1(
                    case_id=root.case_id,
                    planned_ai_unit_id=target,
                    domain="factorization",
                    trace_origin="coverage_tail",
                    candidate_start=2,
                    candidate_end=3,
                    lemma_node_id=None,
                    dependency_path=None,
                    provider_family="deepseek",
                    provider_entry_id=root.provider_entry_id,
                    configured_model=root.configured_model,
                    requested_model=root.configured_model,
                    resolved_model=root.configured_model,
                    attempts=[attempt],
                )
            )
        return store

    missing = seed("missing-tail", completed_tail=False)
    missing_preflights: list[list[str]] = []
    missing_services = replace(
        _fake_services(cli, []),
        provider_preflight=lambda experiment_ids, roots: (
            missing_preflights.append([root.case_id for root in roots]) or {}
        ),
        resume_root=lambda context, protocol: _success(
            context.inventory, context.challenge_plan
        ),
    )
    assert cli.main(
        [
            "run", "--experiment", "exp1", "--profile", "representative",
            "--run-id", "missing-tail", "--output-root", str(tmp_path), "--resume",
        ],
        _services=missing_services,
    ) == 0
    assert missing_preflights == [[root.case_id]]
    assert missing.root_result_path(
        root.experiment_id, root.condition_id, root.case_id, root.repeat_id
    ).is_file()

    complete = seed("complete-tail", completed_tail=True)
    complete_preflights: list[object] = []
    complete_services = replace(
        _fake_services(cli, []),
        provider_preflight=lambda experiment_ids, roots: (
            complete_preflights.append(tuple(roots)) or {}
        ),
        resume_root=lambda context, protocol: _success(
            context.inventory, context.challenge_plan
        ),
    )
    assert cli.main(
        [
            "run", "--experiment", "exp1", "--profile", "representative",
            "--run-id", "complete-tail", "--output-root", str(tmp_path), "--resume",
        ],
        _services=complete_services,
    ) == 0
    assert complete_preflights == []
    assert complete.root_result_path(
        root.experiment_id, root.condition_id, root.case_id, root.repeat_id
    ).is_file()


def test_generic_protocol_resume_uses_runtime_seam_without_provider_preflight(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2 import cli
    from tokenshare.experiments.slim_v2.profiles import (
        build_inventory,
        project_root_inventory_rows,
    )
    from tokenshare.experiments.slim_v2.schema import SlimRunConfigV1
    from tokenshare.experiments.slim_v2.storage import RunStore

    inventory = build_inventory("representative")
    rows = project_root_inventory_rows(inventory)
    roots = [root for root in rows.roots if root.experiment_id == "exp5"]
    root = roots[0]
    store = RunStore(tmp_path / "generic-resume")
    store.write_frozen_inventories(
        conditions=inventory.conditions,
        roots=rows.roots,
        exp3_references=rows.exp3_references,
        exp4_challenges=inventory.challenges,
    )
    store.write_run_config(
        asdict(
            SlimRunConfigV1(
                run_id="generic-resume",
                profile_id="representative",
                experiment_ids=["exp5"],
                source_run_dir=None,
            )
        )
    )
    store.write_root_protocol_snapshot(
        root.experiment_id, root.condition_id, root.case_id, root.repeat_id,
        protocol_result={"summary": {"runtime_observation": {}}},
        traces=[],
        tail_requests={},
        protocol_projection=_success(root, None),
    )
    for other in roots[1:]:
        store.write_root_result(_success(other, None))

    resumed: list[str] = []
    preflights: list[object] = []
    services = replace(
        _fake_services(cli, []),
        execute_root=lambda context: pytest.fail("generic resume reran root"),
        resume_root=lambda context, protocol: (
            resumed.append(context.inventory.case_id)
            or _success(context.inventory, context.challenge_plan)
        ),
        provider_preflight=lambda *args: preflights.append(args) or {},
    )
    assert cli.main(
        [
            "run", "--experiment", "exp5", "--profile", "representative",
            "--run-id", "generic-resume", "--output-root", str(tmp_path),
            "--resume",
        ],
        _services=services,
    ) == 0
    assert resumed == [root.case_id]
    assert preflights == []


@pytest.mark.parametrize(
    ("run_id", "blocked_summary"),
    [
        ("condition-failure-resume", {"slim_condition_failure": {"failure_kind": "x"}}),
        (
            "runtime-failure-resume",
            {"slim_runtime_failure": {"failure_origin": "unexpected_runtime_error"}},
        ),
        (
            "checker-infra-resume",
            {
                "terminal_failure": {
                    "failure_stage": "candidate_verification",
                    "failure_origin": "checker_environment_error",
                    "infrastructure_invalid": True,
                }
            },
        ),
    ],
)
def test_blocked_protocol_resume_never_requires_exp1_secret_or_provider(
    run_id: str,
    blocked_summary: dict[str, object],
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2 import cli
    from tokenshare.experiments.slim_v2.profiles import (
        build_inventory,
        project_root_inventory_rows,
    )
    from tokenshare.experiments.slim_v2.schema import SlimRunConfigV1
    from tokenshare.experiments.slim_v2.storage import RunStore

    inventory = build_inventory("representative")
    rows = project_root_inventory_rows(inventory)
    roots = [root for root in rows.roots if root.experiment_id == "exp1"]
    root = roots[0]
    store = RunStore(tmp_path / run_id)
    store.write_frozen_inventories(
        conditions=inventory.conditions,
        roots=rows.roots,
        exp3_references=rows.exp3_references,
        exp4_challenges=inventory.challenges,
    )
    store.write_run_config(
        asdict(
            SlimRunConfigV1(
                run_id=run_id,
                profile_id="representative",
                experiment_ids=["exp1"],
                source_run_dir=None,
            )
        )
    )
    store.write_root_protocol_snapshot(
        root.experiment_id, root.condition_id, root.case_id, root.repeat_id,
        protocol_result={"summary": blocked_summary},
        traces=[],
        tail_requests={},
        protocol_projection=_success(root, None),
    )
    for other in roots[1:]:
        store.write_root_result(_success(other, None))

    preflights: list[object] = []
    services = replace(
        _fake_services(cli, []),
        execute_root=lambda context: pytest.fail("blocked resume reran root/provider"),
        resume_root=lambda context, protocol: _success(
            context.inventory, context.challenge_plan
        ),
        provider_preflight=lambda *args: preflights.append(args) or {},
    )
    assert cli.main(
        [
            "run", "--experiment", "exp1", "--profile", "representative",
            "--run-id", run_id, "--output-root", str(tmp_path),
            "--resume",
        ],
        _services=services,
    ) == 0
    assert preflights == []


def test_exp1_tail_journal_intent_avoids_secret_preflight_but_keeps_trace_pending(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2 import cli
    from tokenshare.experiments.slim_v2.profiles import (
        build_inventory,
        project_root_inventory_rows,
    )
    from tokenshare.experiments.slim_v2.storage import RunStore

    root = next(
        row
        for row in project_root_inventory_rows(build_inventory("representative")).roots
        if row.experiment_id == "exp1"
    )
    target = root.planned_ai_unit_ids[-1]
    store = RunStore(tmp_path / "journal-only-tail")
    store.write_root_protocol_snapshot(
        root.experiment_id,
        root.condition_id,
        root.case_id,
        root.repeat_id,
        protocol_result={
            "summary": {"runtime_observation": {"unscheduled_ai_unit_ids": [target]}}
        },
        traces=[],
        tail_requests={
            target: {"attempt_ordinal": 0, "soft_hints": {"planned_ai_unit_id": target}}
        },
        protocol_projection=_success(root, None),
    )
    call_key = ":".join(
        (root.experiment_id, root.condition_id, root.case_id, str(root.repeat_id), target, "0")
    )
    store.write_call_intent(call_key, {"call_key": call_key})

    assert cli._protocol_tail_pending(store, root) is True
    assert cli._protocol_tail_requires_provider(store, root) is False


def test_invalid_run_lock_is_fail_closed(tmp_path: Path) -> None:
    from tokenshare.experiments.slim_v2 import cli

    path = tmp_path / ".incomplete.slim-v2.lock"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(RuntimeError, match="run lock"):
        with cli._RunLock(path):
            pytest.fail("invalid lock was reclaimed")


def test_disk_is_rechecked_before_every_root(tmp_path: Path) -> None:
    from tokenshare.experiments.slim_v2 import cli

    calls: list[tuple[str, str, Path | None]] = []
    services = replace(
        _fake_services(cli, calls),
        disk_free_bytes=lambda path: (1 << 50) if not calls else 0,
    )
    with pytest.raises(RuntimeError, match="disk"):
        cli.main(
            [
                "run", "--experiment", "exp5", "--profile", "representative",
                "--run-id", "disk-per-root", "--output-root", str(tmp_path),
            ],
            _services=services,
        )
    assert len(calls) == 1


def test_same_run_lock_blocks_before_store_or_provider_and_is_released(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2 import cli

    entered = threading.Event()
    release = threading.Event()
    first_errors: list[BaseException] = []

    def execute(context):
        entered.set()
        assert release.wait(timeout=5)
        return _success(context.inventory, context.challenge_plan)

    first_services = replace(_fake_services(cli, []), execute_root=execute)
    argv = [
        "run", "--experiment", "exp5", "--profile", "representative",
        "--run-id", "locked", "--output-root", str(tmp_path),
    ]

    def run_first() -> None:
        try:
            cli.main(argv, _services=first_services)
        except BaseException as exc:  # pragma: no cover - asserted below
            first_errors.append(exc)

    thread = threading.Thread(target=run_first)
    thread.start()
    assert entered.wait(timeout=5)

    monkeypatch.setattr(
        cli,
        "RunStore",
        lambda _run_dir: pytest.fail("second invocation reached RunStore"),
    )
    blocked_provider_calls: list[object] = []
    blocked_services = replace(
        _fake_services(cli, []),
        provider_preflight=lambda *args: blocked_provider_calls.append(args) or {},
    )
    with pytest.raises(RuntimeError, match="run lock"):
        cli.main(argv, _services=blocked_services)
    assert blocked_provider_calls == []

    monkeypatch.undo()
    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert first_errors == []
    assert not (tmp_path / ".locked.slim-v2.lock").exists()


def test_next_root_disk_bound_is_dynamic_and_preflight_writes_no_run_dir(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2 import cli
    from tokenshare.experiments.slim_v2.profiles import (
        build_inventory,
        project_root_inventory_rows,
    )

    roots = project_root_inventory_rows(build_inventory("representative")).roots
    exp1_root = next(
        root
        for root in roots
        if root.experiment_id == "exp1" and len(root.planned_ai_unit_ids) == 8
    )
    fixed_root = next(root for root in roots if root.experiment_id == "exp2")
    exp1_required = cli._required_root_write_bytes(exp1_root, "representative")
    fixed_required = cli._required_root_write_bytes(fixed_root, "representative")
    assert cli._root_caps(exp1_root, "representative")[3] == 24
    assert exp1_required > 512 * 1024**2
    assert fixed_required < 16 * 1024**2

    services = replace(
        _fake_services(cli, []),
        disk_free_bytes=lambda _path: exp1_required - 1,
    )
    with pytest.raises(RuntimeError, match=f"need {exp1_required}"):
        cli.main(
            [
                "run", "--experiment", "exp1", "--profile", "representative",
                "--run-id", "no-empty-run", "--output-root", str(tmp_path),
            ],
            _services=services,
        )
    assert not (tmp_path / "no-empty-run").exists()
    assert not (tmp_path / ".no-empty-run.slim-v2.lock").exists()


@pytest.mark.parametrize("source_root_state", ["missing", "v1"])
def test_source_closure_rejects_non_v2_source_root(
    source_root_state: str,
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2 import cli
    from tokenshare.experiments.slim_v2.profiles import (
        build_inventory,
        project_root_inventory_rows,
    )
    from tokenshare.experiments.slim_v2.storage import RunStore

    inventory = build_inventory("representative")
    rows = project_root_inventory_rows(inventory)
    target = next(root for root in rows.roots if root.experiment_id == "exp2")
    source_root = next(
        root
        for root in rows.roots
        if root.experiment_id == "exp1" and root.case_id == target.case_id
    )
    source = RunStore(tmp_path / source_root_state)
    source.write_frozen_inventories(
        conditions=inventory.conditions,
        roots=rows.roots,
        exp3_references=rows.exp3_references,
        exp4_challenges=inventory.challenges,
    )
    _write_factor_source(source, target)
    if source_root_state == "v1":
        document = asdict(_success(source_root, None))
        document["schema_version"] = "tokenshare.slim_v2.root_result.v1"
        path = source.root_result_path(
            source_root.experiment_id,
            source_root.condition_id,
            source_root.case_id,
            source_root.repeat_id,
        )
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(RuntimeError, match="source Exp1 root result"):
        cli._source_closure([target], source.run_dir, cli._case_inputs())


def test_source_closure_failure_is_scoped_to_condition(tmp_path: Path) -> None:
    from tokenshare.experiments.slim_v2 import cli
    from tokenshare.experiments.slim_v2.profiles import (
        build_inventory,
        project_root_inventory_rows,
    )
    from tokenshare.experiments.slim_v2.storage import RunStore

    inventory = build_inventory("representative")
    projected = project_root_inventory_rows(inventory)
    rows = projected.roots
    exp2 = [row for row in rows if row.experiment_id == "exp2"]
    omitted_case = exp2[0].case_id
    source = RunStore(tmp_path / "source")
    source.write_frozen_inventories(
        conditions=inventory.conditions,
        roots=rows,
        exp3_references=projected.exp3_references,
        exp4_challenges=inventory.challenges,
    )
    reusable_case_ids = {root.case_id for root in exp2 if root.case_id != omitted_case}
    for source_root in rows:
        if source_root.experiment_id == "exp1" and source_root.case_id in reusable_case_ids:
            source.write_root_result(_success(source_root, None))
    for root in exp2:
        if root.case_id != omitted_case:
            _write_factor_source(source, root)

    calls: list[tuple[str, str, Path | None]] = []
    assert cli.main(
        [
            "run", "--experiment", "exp2", "--profile", "representative",
            "--run-id", "source-scoped", "--source-run-dir", str(source.run_dir),
            "--output-root", str(tmp_path),
        ],
        _services=_fake_services(cli, calls),
    ) == 0
    executed_cases = {case_id for _experiment, case_id, _source in calls}
    assert executed_cases == {root.case_id for root in exp2 if root.case_id != omitted_case}
    output = RunStore(tmp_path / "source-scoped")
    omitted_results = [
        output.read_root_result(*(
            root.experiment_id, root.condition_id, root.case_id, root.repeat_id
        ))
        for root in exp2
        if root.case_id == omitted_case
    ]
    assert omitted_results
    assert {result.failure_kind for result in omitted_results} == {
        "infrastructure_invalid"
    }


def test_full_source_closure_checks_only_exp2_to_exp4_consumers_and_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full 的99个来源闭包由 inventory 定义；closure 不扫描336个非来源 Exp1 root。"""

    from tokenshare.experiments.slim_v2 import cli
    from tokenshare.experiments.slim_v2.profiles import (
        build_inventory,
        downstream_trace_consumer_case_ids,
        project_root_inventory_rows,
    )
    from tokenshare.experiments.slim_v2.storage import RunStore

    inventory = build_inventory("full")
    projected = project_root_inventory_rows(inventory)
    consumer_case_ids = downstream_trace_consumer_case_ids("full")
    assert len(consumer_case_ids) == 99

    selected_targets: list[RootInventoryV1] = []
    used_case_ids: set[str] = set()
    for experiment_id in ("exp2", "exp3", "exp4"):
        target = next(
            root
            for root in projected.roots
            if (
                root.experiment_id == experiment_id
                and root.domain == "factorization"
                and str(root.case_id) not in used_case_ids
            )
        )
        selected_targets.append(target)
        used_case_ids.add(str(target.case_id))
    assert {root.experiment_id for root in selected_targets} == {"exp2", "exp3", "exp4"}
    assert {str(root.case_id) for root in selected_targets} <= consumer_case_ids

    selected_sources = [
        next(
            root
            for root in projected.roots
            if root.experiment_id == "exp1" and root.case_id == target.case_id
        )
        for target in selected_targets
    ]
    non_source = next(
        root
        for root in projected.roots
        if (
            root.experiment_id == "exp1"
            and str(root.case_id) not in consumer_case_ids
        )
    )
    source = RunStore(tmp_path / "source")
    source.write_frozen_inventories(
        conditions=inventory.conditions,
        roots=[*selected_sources, non_source],
        exp3_references=[],
        exp4_challenges=[],
    )
    for root in selected_sources:
        source.write_root_result(_success(root, None))
        _write_factor_source(source, root)
    # 这份最终结果合法，但无 trace；它不能成为跨实验的隐式来源需求。
    non_source_result = replace(
        _success(non_source, None),
        trace_tail_status="not_required_by_downstream",
    )
    non_source_result.validate()
    source.write_root_result(non_source_result)

    observed_trace_case_ids: list[str] = []
    original_read_trace = RunStore.read_trace

    def spy_read_trace(
        store: RunStore,
        case_id: str,
        repeat_id: int,
        planned_ai_unit_id: str,
    ) -> UnitTraceV1:
        observed_trace_case_ids.append(case_id)
        return original_read_trace(store, case_id, repeat_id, planned_ai_unit_id)

    monkeypatch.setattr(RunStore, "read_trace", spy_read_trace)
    cli._source_closure(selected_targets, source.run_dir, cli._case_inputs())
    assert set(observed_trace_case_ids) == {str(root.case_id) for root in selected_targets}
    assert str(non_source.case_id) not in observed_trace_case_ids

    missing_target = selected_targets[1]
    source.trace_path(
        str(missing_target.case_id), 0, str(missing_target.planned_ai_unit_ids[0])
    ).unlink()
    with pytest.raises(RuntimeError, match="source closure is incomplete"):
        cli._source_closure(selected_targets, source.run_dir, cli._case_inputs())


def test_representative_cap_secret_disk_and_price_are_run_safety_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2 import cli
    from tokenshare.experiments.slim_v2.profiles import build_plan
    from tokenshare.experiments.slim_v2 import provider

    plan = build_plan("representative")
    assert (plan.paper_root_count, plan.execution_root_count, plan.online_provider_call_upper) == (
        71,
        73,
        81,
    )

    calls: list[tuple[str, str, Path | None]] = []
    no_disk = replace(
        _fake_services(cli, calls), disk_free_bytes=lambda path: 0
    )
    with pytest.raises(RuntimeError, match="disk"):
        cli.main(
            [
                "run",
                "--experiment",
                "exp5",
                "--profile",
                "representative",
                "--run-id",
                "no-disk",
                "--output-root",
                str(tmp_path),
            ],
            _services=no_disk,
        )
    assert calls == []

    monkeypatch.setattr(
        provider,
        "project_cost",
        lambda **kwargs: pytest.fail("price/cost must not participate in preflight"),
    )
    assert cli.main(
        [
            "run",
            "--experiment",
            "exp5",
            "--profile",
            "representative",
            "--run-id",
            "price-irrelevant",
            "--output-root",
            str(tmp_path),
        ],
        _services=_fake_services(cli, calls),
    ) == 0
    assert calls
    help_text = cli._parser().format_help().lower()
    for forbidden in ("price", "balance", "budget", "approval", "publication", "evidence"):
        assert forbidden not in help_text

    config_root = tmp_path / "secret-fixture"
    tracked = config_root / "benchmarks" / "paper"
    tracked.mkdir(parents=True)
    (tracked / "exp1_baseline_provider_config.v3.json").write_text(
        json.dumps(
            {
                "provider_family": "deepseek",
                "entries": [
                    {
                        "entry_id": "deepseek_v4_flash_exp1_baseline",
                        "enabled": True,
                        "base_url": "https://example.invalid",
                        "endpoint": "/chat/completions",
                        "api_key_env": "TASK6_MISSING_KEY",
                        "model": "deepseek-v4-flash",
                        "request_overrides": {},
                        "supports_json_mode": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("TASK6_MISSING_KEY", raising=False)
    with pytest.raises(RuntimeError, match="secret|API key"):
        cli._preflight_provider_entries(
            ("exp1",),
            tuple(),
            repo_root=config_root,
        )

    local = config_root / "local"
    local.mkdir()
    (local / "ai_api_smoke.local.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "entry_id": "deepseek_v4_flash_exp1_baseline",
                        "api_key": "task6-local-secret",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    entries = cli._preflight_provider_entries(
        ("exp1",), tuple(), repo_root=config_root
    )
    assert entries["deepseek_v4_flash_exp1_baseline"].configured_model == (
        "deepseek-v4-flash"
    )
    assert os.environ["TASK6_MISSING_KEY"] == "task6-local-secret"


def test_offline_factorization_and_lean_use_the_real_protocol_vertical(
    tmp_path: Path,
) -> None:
    from tokenshare.core.models import ProtocolConfig
    from tokenshare.experiments.slim_v2 import cli
    from tokenshare.experiments.slim_v2.projector import project_root_result
    from tokenshare.experiments.slim_v2.runtime import (
        RootAssembly,
        TailSummaryV1,
        run_root_slice,
    )
    from tokenshare.local_runtime import SequentialWorkerBackend
    from tokenshare.plugins.factorization.runtime_adapter import (
        FactorizationExecutionBridge,
        FactorizationRuntimeAdapter,
    )
    from tokenshare.plugins.lean_proof.runtime_adapter import (
        LeanExecutionBridge,
        LeanRuntimeAdapter,
    )
    from tokenshare.storage.artifacts import ArtifactStore
    from tokenshare.storage.events import EventLedger
    from tests.experiments.slim_v2.test_system_vertical import (
        NOW,
        RecordingLeanChecker,
        _Clock,
        _LeanSubmissionFake,
        _ObservationClock,
        _RangeSubmissionFake,
        _test_lean_environment,
    )

    domains: list[str] = []

    def execute(context):
        inventory = context.inventory
        domains.append(inventory.domain)
        system = context.run_store.system_root_directory(
            inventory.experiment_id,
            inventory.condition_id,
            inventory.case_id,
            inventory.repeat_id,
        ) / "system"
        artifacts = ArtifactStore(system)
        ledger = EventLedger(system / "events.jsonl")
        config = ProtocolConfig.default(
            config_id=f"task6-{inventory.case_id}",
            artifact_store_uri=f"file://{system}/artifacts",
            event_log_uri=f"file://{system}/events.jsonl",
        )
        clock = _Clock()
        if inventory.domain == "factorization":
            runtime = FactorizationRuntimeAdapter(
                provider_family="deepseek",
                seed=1,
                protocol_config=config,
                created_at=NOW,
            )
            executor = FactorizationExecutionBridge(
                plugin_runtime=runtime,
                range_executor=_RangeSubmissionFake(artifacts),
            )
        else:
            runtime = LeanRuntimeAdapter(
                provider_family="deepseek",
                environment_manifest=_test_lean_environment(),
                checker=RecordingLeanChecker(),
                protocol_config=config,
                created_at=NOW,
            )
            executor = LeanExecutionBridge(
                plugin_runtime=runtime,
                proof_candidate_executor=_LeanSubmissionFake(artifacts),
            )
        assembly = RootAssembly(
            run_id=f"task6:{inventory.case_id}",
            root_input=context.root_input,
            protocol_config=config,
            artifact_store=artifacts,
            event_ledger=ledger,
            plugin_runtime=runtime,
            worker_backend=SequentialWorkerBackend(
                executor=executor,
                submitted_at=clock,
            ),
            now=clock,
            observation_clock=_ObservationClock(),
        )
        protocol = run_root_slice(assembly)
        observation = protocol.summary["runtime_observation"]
        unscheduled = sorted(observation["unscheduled_ai_unit_ids"])
        tail = TailSummaryV1(
            trace_tail_started_at_ms=(3 if unscheduled else None),
            trace_tail_terminal_at_ms=(3 if unscheduled else None),
            trace_tail_wall_clock_ms=0,
            trace_tail_status=(
                "completed_with_failures" if unscheduled else "not_needed"
            ),
            trace_tail_target_ai_unit_ids=unscheduled,
            trace_tail_recorded_ai_unit_ids=unscheduled,
            trace_tail_success_unit_count=0,
            trace_tail_failure_unit_count=len(unscheduled),
            trace_tail_provider_attempt_count=len(unscheduled),
            trace_tail_total_tokens=0,
            trace_tail_cost_estimate_cny=0.0,
        )
        return project_root_result(
            inventory=inventory,
            assembly=assembly,
            protocol_result=protocol,
            provider_family="deepseek",
            requested_model=inventory.configured_model,
            resolved_model=inventory.configured_model,
            reasoning_mode="offline_fixture",
            tail_summary=tail,
        )

    services = replace(_fake_services(cli, []), execute_root=execute)
    assert cli.main(
        [
            "run",
            "--experiment",
            "exp1",
            "--profile",
            "representative",
            "--run-id",
            "offline-vertical",
            "--output-root",
            str(tmp_path),
        ],
        _services=services,
    ) == 0
    assert domains == ["factorization", "factorization", "lean", "lean"]
    assert not os.environ.get("TASK6_SHOULD_NOT_EXIST")


def test_representative_fake_transport_uses_production_runtime_and_reducer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """73 次离线执行只能替换外部 HTTP 边界，其余均走生产接线。"""

    from tokenshare.experiments.slim_v2 import cli, provider
    from tokenshare.experiments.slim_v2.reducer import reduce_run
    from tokenshare.experiments.slim_v2.storage import RunStore
    from tests.experiments.slim_v2.test_answer_paths import _FakeResponse

    services = cli._default_services()
    assert services.execute_root is cli._production_execute_root
    assert services.reduce_run is reduce_run

    monkeypatch.setenv("DEEPSEEK_API_KEY", "task6-offline-deepseek")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "task6-offline-siliconflow")
    fake_responses: list[_FakeResponse] = []
    requested_models: list[str] = []

    def fake_open(request: object, _timeout_seconds: float) -> _FakeResponse:
        request_data = getattr(request, "data", None)
        assert isinstance(request_data, bytes)
        request_body = json.loads(request_data.decode("utf-8"))
        model = request_body["model"]
        assert isinstance(model, str)
        requested_models.append(model)
        response = _FakeResponse(
            json.dumps(
                {
                    "id": f"task6-fake-{len(fake_responses)}",
                    "model": model,
                    # 两领域都形成真实 parser failure；不会启动 Lean/lake。
                    "choices": [
                        {
                            "message": {
                                "content": "{}",
                                "reasoning_content": "",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 2,
                        "prompt_cache_hit_tokens": 0,
                        "prompt_cache_miss_tokens": 2,
                        "completion_tokens": 1,
                        "total_tokens": 3,
                        "completion_tokens_details": {"reasoning_tokens": 0},
                    },
                },
                separators=(",", ":"),
            ).encode("utf-8")
        )
        fake_responses.append(response)
        return response

    monkeypatch.setattr(provider, "_open_response", fake_open)
    run_id = "representative-production-reducer"
    assert cli.main(
        [
            "representative",
            "--run-id",
            run_id,
            "--output-root",
            str(tmp_path),
        ]
    ) == 0

    run_dir = tmp_path / run_id
    store = RunStore(run_dir)
    paper_results = list(store.iter_inventory_results("roots"))
    reference_results = list(store.iter_inventory_results("exp3_references"))
    assert len(paper_results) == 71
    assert len(reference_results) == 2
    assert all(result is not None for _inventory, result in paper_results)
    assert all(result is not None for _inventory, result in reference_results)
    assert Counter(
        inventory.experiment_id for inventory, _result in paper_results
    ) == Counter({"exp1": 4, "exp2": 12, "exp3": 8, "exp4": 44, "exp5": 3})
    assert {inventory.experiment_id for inventory, _result in reference_results} == {
        "exp3"
    }

    # 每次 execution 都留下生产 coordinator 的 protocol snapshot 与 event ledger。
    assert len(list((run_dir / "roots").rglob("protocol.json"))) == 73
    assert len(list((run_dir / "system").rglob("events.jsonl"))) == 73

    summary_path = run_dir / "metrics" / "summary.json"
    table_dir = run_dir / "metrics" / "tables"
    expected_publication = {summary_path} | {
        table_dir / f"exp{number}.{suffix}"
        for number in range(1, 6)
        for suffix in ("jsonl", "csv")
    }
    assert {path for path in (run_dir / "metrics").rglob("*") if path.is_file()} == (
        expected_publication
    )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["schema_version"] == "tokenshare.slim_v2.reducer_summary.v1"
    assert summary["formal_metric_id_count"] == 153
    assert summary["paper_root_inventory_counts"] == {
        "exp1": 4,
        "exp2": 12,
        "exp3": 8,
        "exp4": 44,
        "exp5": 3,
    }
    assert summary["exp3_reference_inventory_count"] == 2
    assert summary["provider_calls_observed"] == {
        "exp1": 54,
        "exp2": 0,
        "exp3": 0,
        "exp4": 0,
        "exp5": 24,
    }

    for experiment_id in ("exp1", "exp2", "exp3", "exp4", "exp5"):
        jsonl_path = table_dir / f"{experiment_id}.jsonl"
        csv_path = table_dir / f"{experiment_id}.csv"
        jsonl_rows = [
            json.loads(line)
            for line in jsonl_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            csv_rows = list(csv.DictReader(handle))
        assert len(jsonl_rows) == summary["tables"][experiment_id]["row_count"]
        assert len(csv_rows) == len(jsonl_rows)
        assert jsonl_rows
        assert all(row["table_id"] == experiment_id for row in jsonl_rows)
        assert all(
            {"row_kind", "slice", "missing_reasons", "table_metadata"}
            <= row.keys()
            for row in jsonl_rows
        )

    # 81 是冻结上界；当前确定性 parser-failure 路径实际产生 78 次 fake HTTP。
    assert len(requested_models) == len(fake_responses) == 78
    assert set(requested_models) == {
        "deepseek-v4-flash",
        "zai-org/GLM-5.2",
        "Qwen/Qwen3-14B",
        "MiniMaxAI/MiniMax-M2.5",
    }
    assert all(response.closed for response in fake_responses)
    persisted_text = "".join(
        path.read_text(encoding="utf-8")
        for path in run_dir.rglob("*.json")
    )
    assert "task6-offline-deepseek" not in persisted_text
    assert "task6-offline-siliconflow" not in persisted_text
