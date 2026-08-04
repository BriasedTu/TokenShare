from __future__ import annotations

import ast
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from verification import run_verification


ROOT = Path(__file__).resolve().parents[1]
FAST_MANIFEST = ROOT / "verification" / "fast-tests.txt"
PROFILE_DIR = ROOT / "verification" / "profiles"
PROFILE_NAMES = (
    "paper-l1-components",
    "paper-l2-historical-real",
    "paper-l3-new-real-smoke-audit",
    "paper-l4-cell-lineage",
)
L1_TASK_NODEIDS = {
    0: "tests/experiments/test_paper_pipeline_profile.py::test_profile_freezes_four_plus_492_and_516_hard_calls",
    1: "tests/experiments/test_paper_metric_contract.py::test_tracked_contract_loads_against_current_pipeline_profile",
    2: "tests/local_runtime/test_runtime_boundaries.py::test_protocol_run_ledger_binding_is_strict_and_identity_bound",
    3: "tests/experiments/test_paper_direct_results.py::test_public_row_cannot_mint_success_or_publishable_projection",
    4: "tests/executors/test_ai_api_request_identity.py::test_artifact_digest_and_wire_share_same_prepared_bytes_object",
    5: "tests/executors/test_response_bank.py::test_external_locator_has_no_path_uri_or_artifact_ref",
    6: "tests/experiments/test_paper_response_bank.py::test_every_inventory_row_has_unique_inventory_entry_id_and_semantic_slot_key",
    7: "tests/experiments/test_paper_budget_ledger.py::test_unique_key_is_inventory_digest_and_inventory_entry_id",
    8: "tests/experiments/test_paper_response_bank_acquisition.py::test_acquisition_orders_prepare_admit_persist_reserve_dispatch_terminal_publish_settle",
    9: "tests/local_runtime/test_logical_scheduler.py::test_stable_tie_break_replays_identical_event_order",
    10: "tests/local_runtime/test_trace_delivery_parent_commit.py::test_parent_commit_accepts_core_neutral_typed_trace_delivery_stager",
    11: "tests/executors/test_trace_backed.py::test_trace_executor_calls_provider_zero_and_streams_external_source",
    12: "tests/experiments/test_paper_exp1_metrics.py::test_exp1_completion_requires_complete_final_reference_for_all_preregistered_roots",
    13: "tests/experiments/test_paper_exp2_metrics.py::test_speedup_requires_same_case_repeat_success_positive_times",
    14: "tests/experiments/test_paper_exp3_metrics.py::test_controlled_wrong_candidate_denominator_is_exact_boundary",
    15: "tests/experiments/test_paper_exp4_metrics.py::test_each_ablation_pairs_one_full_by_case_repeat_sample",
    16: "tests/experiments/test_paper_exp5_metrics.py::test_nonpass_denominator_includes_every_actual_first_attempt",
    17: "tests/experiments/test_paper_metric_registry.py::test_registry_has_exactly_one_projector_per_experiment_table",
    18: "tests/experiments/test_paper_formal_evidence.py::test_only_paid_full_acquisition_receipt_plus_complete_manifest_can_be_trace_paper_eligible",
    19: "tests/experiments/test_paper_formal_runner.py::test_trace_run_uses_normal_coordinator_fault_verifier_checker_merge_settlement",
    20: "tests/experiments/test_paper_metric_observations.py::test_every_numeric_draft_becomes_one_observation",
    21: "tests/experiments/test_paper_metric_renderer_contract.py::test_renderer_accepts_only_observations_allowed_by_contract",
    22: "tests/experiments/test_paper_full_resource_trace.py::test_current_path_has_no_shared_exp1_builder_or_schema",
    23: "tests/experiments/test_paper_historical_real_fixture.py::test_real_success_predicate_uses_per_number_passed_correct_oracle_not_missing_summary_passed",
    24: "tests/experiments/test_paper_traceability.py::test_pure_component_recomputation_uses_synthetic_fixture_for_l1",
    25: "tests/experiments/test_paper_online_checks.py::test_task25_exports_no_metric_formula_or_post_bank_gate",
    26: "tests/experiments/test_paper_paid_authorization.py::test_receipt_binds_authorized_plan_inventory_and_prompt_admission_digests",
    27: "tests/experiments/test_run_paper_pipeline.py::test_pipeline_owned_formal_orchestration_orders_typed_gates_and_propagates_blocked",
    28: "tests/experiments/test_paper_formal_gate.py::test_execution_gate_checks_only_prerequisites_available_before_selected_run",
    29: "tests/experiments/test_smoke_launcher_supervision.py::test_old_launchers_exit_two_before_secret_read",
}
L1_COMPONENT_FILES = (
    "tests/experiments/test_paper_pipeline_profile.py",
    "tests/experiments/test_paper_metric_contract.py",
    "tests/local_runtime/test_runtime_boundaries.py",
    "tests/experiments/test_paper_direct_results.py",
    "tests/executors/test_ai_api_request_identity.py",
    "tests/executors/test_response_bank.py",
    "tests/experiments/test_paper_response_bank.py",
    "tests/experiments/test_paper_budget_ledger.py",
    "tests/experiments/test_paper_response_bank_acquisition.py",
    "tests/local_runtime/test_logical_scheduler.py",
    "tests/local_runtime/test_trace_delivery_parent_commit.py",
    "tests/executors/test_trace_backed.py",
    "tests/experiments/test_paper_exp1_metrics.py",
    "tests/experiments/test_paper_exp2_metrics.py",
    "tests/experiments/test_paper_exp3_metrics.py",
    "tests/experiments/test_paper_exp4_metrics.py",
    "tests/experiments/test_paper_exp5_metrics.py",
    "tests/experiments/test_paper_metric_registry.py",
    "tests/experiments/test_paper_formal_evidence.py",
)
L1_TASK25_TO_29_FILES = {
    "tests/experiments/test_paper_online_checks.py": 6,
    "tests/experiments/test_paper_paid_authorization.py": 14,
    "tests/experiments/test_run_paper_pipeline.py": 39,
    "tests/experiments/test_paper_formal_gate.py": 18,
    "tests/experiments/test_smoke_launcher_supervision.py": 9,
}
L1_TASK19_NODEIDS = {
    "tests/experiments/test_paper_formal_runner.py::test_trace_run_uses_normal_coordinator_fault_verifier_checker_merge_settlement",
    "tests/experiments/test_paper_formal_runner.py::test_missing_bank_preflight_records_block_without_engine_task_lease_request_provider_events",
    "tests/experiments/test_paper_formal_runner.py::test_runner_object_graph_never_materializes_source",
    "tests/experiments/test_paper_formal_runner.py::test_trace_current_provider_calls_are_zero",
    "tests/experiments/test_paper_formal_callbacks.py::test_runner_uses_logical_scheduler_for_trace_and_real_clock_for_online",
    "tests/experiments/test_paper_formal_callbacks.py::test_worker_death_parent_commit_and_ordinal_replacement_flow",
}
L1_TASK20_TO_22_FILES = {
    "tests/experiments/test_paper_metric_observations.py": 11,
    "tests/experiments/test_paper_metric_renderer_contract.py": 4,
    "tests/experiments/test_paper_full_resource_trace.py": 4,
}
L1_TASK24_NODEIDS = {
    "tests/experiments/test_paper_traceability.py::test_pure_component_recomputation_uses_synthetic_fixture_for_l1",
    "tests/experiments/test_paper_traceability.py::test_replay_never_calls_provider_or_writes_source_bank",
}
L2_TASK23_HISTORICAL_SINGLE_LEAF_NODEIDS = {
    "tests/experiments/test_paper_historical_real_fixture.py::test_real_success_predicate_uses_per_number_passed_correct_oracle_not_missing_summary_passed",
    "tests/experiments/test_paper_historical_real_fixture.py::test_case_row_batch_raw_provenance_and_full_tree_digests_are_exact",
    "tests/experiments/test_paper_historical_real_fixture.py::test_tracked_fixture_contains_minimal_sanitized_unmodified_payload_and_source_hashes",
    "tests/experiments/test_paper_historical_real_fixture.py::test_dedicated_single_leaf_adapter_runs_normal_coordinator_parser_verifier_canonical_merge_ledger_to_table",
    "tests/experiments/test_paper_historical_real_fixture.py::test_adapter_rejects_formal_range_catalog_and_never_claims_range_semantics",
    "tests/experiments/test_paper_historical_real_fixture.py::test_fixture_provider_calls_zero_and_classification_regression_only",
    "tests/experiments/test_paper_historical_real_fixture.py::test_fixture_can_never_be_trace_paper_eligible",
    "tests/experiments/test_paper_historical_real_fixture.py::test_20260731_exp34_is_negative_only_and_source_hash_unchanged",
}
L1_SUPERSEDED_NODEIDS = {
    "tests/experiments/test_paper_pipeline_profile.py::test_loader_exposes_complete_frozen_typed_authority",
}


def _profile_entries(name: str) -> list[str]:
    path = PROFILE_DIR / f"{name}.txt"
    assert path.is_file()
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _test_function_nodeids(relative_path: str) -> set[str]:
    module = ast.parse((ROOT / relative_path).read_text(encoding="utf-8-sig"))
    return {
        f"{relative_path}::{node.name}"
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    }


def _fast_test_entries() -> list[str]:
    assert FAST_MANIFEST.is_file(), "默认快速验证必须使用共享测试清单"
    return [
        line.strip()
        for line in FAST_MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_fast_manifest_is_nonempty_unique_and_resolvable() -> None:
    entries = _fast_test_entries()

    assert entries, "快速测试清单不能为空"
    assert len(entries) == len(set(entries)), "快速测试清单不能包含重复路径"
    assert all((ROOT / entry).exists() for entry in entries)


def test_fast_manifest_keeps_required_smoke_coverage() -> None:
    entries = set(_fast_test_entries())

    assert {
        "tests/core",
        "tests/storage",
        "tests/executors",
        "tests/plugins/factorization",
        "tests/test_package_layout.py",
        "tests/plugins/lean_proof/test_lean_descriptor_and_schemas.py",
        "tests/plugins/lean_proof/test_lean_environment.py",
        "tests/experiments/test_paper_experiment_contracts.py",
        "tests/experiments/test_paper_models.py",
    }.issubset(entries)


def test_fast_manifest_excludes_known_long_running_suites() -> None:
    entries = set(_fast_test_entries())

    assert "tests" not in entries
    assert "tests/experiments/test_paper_budget.py" not in entries
    assert "tests/experiments/test_paper_catalog.py" not in entries
    assert "tests/experiments/test_lean_paper_adapter.py" not in entries
    assert "tests/experiments/test_lean_task14_readiness.py" not in entries


def test_both_startup_scripts_expose_full_mode_and_share_manifest() -> None:
    powershell = (ROOT / "init.ps1").read_text(encoding="utf-8")
    bash = (ROOT / "init.sh").read_text(encoding="utf-8")
    runner = (ROOT / "verification" / "run_verification.py").read_text(
        encoding="utf-8"
    )

    assert "[switch] $Full" in powershell
    assert "--full" in bash
    assert "verification/fast-tests.txt" in runner


def test_startup_harness_routes_to_current_governance_not_historical_draft() -> None:
    runner = (ROOT / "verification" / "run_verification.py").read_text(
        encoding="utf-8"
    )

    assert '"Doc/repository-governance.md"' in runner
    assert (
        '"Doc/TechnicalDocument/2026-06-02-tokenshare-protocol-kernel-revised-draft.md"'
        not in runner
    )


def test_startup_scripts_use_one_conda_python_verification_entry() -> None:
    powershell = (ROOT / "init.ps1").read_text(encoding="utf-8")
    bash = (ROOT / "init.sh").read_text(encoding="utf-8")

    assert powershell.count("conda run") == 1
    assert bash.count('run -n "$CONDA_ENV" python') == 1
    assert "verification/run_verification.py" in powershell
    assert "verification/run_verification.py" in bash


def test_profiles_expose_lean_audit_and_real_canary_manifest() -> None:
    powershell = (ROOT / "init.ps1").read_text(encoding="utf-8")
    bash = (ROOT / "init.sh").read_text(encoding="utf-8")
    runner = (ROOT / "verification" / "run_verification.py").read_text(
        encoding="utf-8"
    )
    canaries = ROOT / "verification" / "lean-canary-tests.txt"

    assert "[switch] $LeanAudit" in powershell
    assert "[switch] $ForceAllLeanAudit" in powershell
    assert "--lean-audit" in bash
    assert "--force-all-lean-audit" in bash
    assert "verification/lean-canary-tests.txt" in runner
    assert '"--only-lean-canary"' in runner
    assert canaries.is_file()
    assert any(
        line.strip() and not line.lstrip().startswith("#")
        for line in canaries.read_text(encoding="utf-8").splitlines()
    )


def test_context_budget_rejects_oversized_tier1_file(tmp_path: Path) -> None:
    for relative_path in run_verification.TIER1_CONTEXT_BUDGET_BYTES:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ok\n", encoding="utf-8")
    oversized_path = tmp_path / "feature_list.json"
    oversized_path.write_bytes(
        b"x" * (run_verification.TIER1_CONTEXT_BUDGET_BYTES["feature_list.json"] + 1)
    )

    with pytest.raises(SystemExit, match="context budget"):
        run_verification._verify_context_budget(tmp_path)


def test_context_budget_rejects_oversized_tier1_total(tmp_path: Path) -> None:
    sizes = {
        "AGENTS.md": 15 * 1024,
        "feature_list.json": 25 * 1024,
        "progress.md": 25 * 1024,
        "session-handoff.md": 20 * 1024,
        "Doc/agent-navigation.md": 15 * 1024,
    }
    assert all(
        size <= run_verification.TIER1_CONTEXT_BUDGET_BYTES[relative_path]
        for relative_path, size in sizes.items()
    )
    assert sum(sizes.values()) > run_verification.TIER1_TOTAL_CONTEXT_BUDGET_BYTES
    for relative_path, size in sizes.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)

    with pytest.raises(SystemExit, match="total context budget"):
        run_verification._verify_context_budget(tmp_path)


def test_feature_state_rejects_task_named_top_level_history(tmp_path: Path) -> None:
    body = {
        "schema_version": "tokenshare.feature_list.v2",
        "project": "TokenShare",
        "stage": "paper-experiments",
        "active_feature": "feat-011",
        "active_focus": {"summary": "context consolidation"},
        "authorities": {},
        "features": [
            {
                "id": "feat-011",
                "name": "Paper Real AI Experiments",
                "status": "in-progress",
                "dependencies": ["feat-009"],
                "summary": "Current feature.",
                "acceptance": [],
                "evidence": [],
                "next_action": "Continue.",
            }
        ],
        "feat011_task_history_2026_07_30": {"status": "done"},
    }
    path = tmp_path / "feature_list.json"
    path.write_text(json.dumps(body), encoding="utf-8")

    with pytest.raises(SystemExit, match="unsupported top-level fields"):
        run_verification._verify_feature_state(path)


def test_feature_state_requires_exactly_one_matching_active_feature(
    tmp_path: Path,
) -> None:
    body = {
        "schema_version": "tokenshare.feature_list.v2",
        "project": "TokenShare",
        "stage": "paper-experiments",
        "active_feature": "feat-011",
        "active_focus": {"summary": "context consolidation"},
        "authorities": {},
        "features": [
            {
                "id": "feat-010",
                "name": "Replay and Audit",
                "status": "in-progress",
                "dependencies": ["feat-009"],
                "summary": "Deferred work.",
                "acceptance": [],
                "evidence": [],
                "next_action": "None.",
            },
            {
                "id": "feat-011",
                "name": "Paper Real AI Experiments",
                "status": "in-progress",
                "dependencies": ["feat-009"],
                "summary": "Current feature.",
                "acceptance": [],
                "evidence": [],
                "next_action": "Continue.",
            },
        ],
    }
    path = tmp_path / "feature_list.json"
    path.write_text(json.dumps(body), encoding="utf-8")

    with pytest.raises(SystemExit, match="exactly one in-progress"):
        run_verification._verify_feature_state(path)


@pytest.mark.parametrize(
    ("features", "message"),
    [
        (["not-a-record"], "feature records must be objects"),
        (
            [
                {
                    "id": "feat-011",
                    "name": "Paper Real AI Experiments",
                    "status": "in-progress",
                    "dependencies": [],
                    "summary": "Current feature.",
                    "acceptance": [],
                    "evidence": [],
                    "next_action": "Continue.",
                },
                {
                    "id": "feat-011",
                    "name": "Duplicate",
                    "status": "done",
                    "dependencies": [],
                    "summary": "Duplicate feature.",
                    "acceptance": [],
                    "evidence": [],
                    "next_action": "None.",
                },
            ],
            "duplicate feature ids",
        ),
        (
            [
                {
                    "id": "feat-011",
                    "status": "in-progress",
                }
            ],
            "missing required fields",
        ),
    ],
)
def test_feature_state_rejects_malformed_or_duplicate_records(
    tmp_path: Path,
    features: list[object],
    message: str,
) -> None:
    body = {
        "schema_version": "tokenshare.feature_list.v2",
        "project": "TokenShare",
        "stage": "paper-experiments",
        "active_feature": "feat-011",
        "active_focus": {"summary": "context consolidation"},
        "authorities": {},
        "features": features,
    }
    path = tmp_path / "feature_list.json"
    path.write_text(json.dumps(body), encoding="utf-8")

    with pytest.raises(SystemExit, match=message):
        run_verification._verify_feature_state(path)


def test_compile_repository_only_scans_code_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for relative_path in ("src", "tests", "verification"):
        (tmp_path / relative_path).mkdir()
    observed: list[Path] = []

    def capture(path: Path, **_kwargs: object) -> bool:
        observed.append(Path(path))
        return True

    monkeypatch.setattr(run_verification, "ROOT", tmp_path)
    monkeypatch.setattr(run_verification.compileall, "compile_dir", capture)

    run_verification._compile_repository()

    assert observed == [
        tmp_path / "src",
        tmp_path / "tests",
        tmp_path / "verification",
    ]


def test_four_named_profiles_have_exact_nonempty_nodeids() -> None:
    exact_nodeid = re.compile(r"^tests/.+\.py::test_[^\s:]+(?:\[[^\]\r\n]+\])?$")

    assert tuple(run_verification.PROFILE_NAMES) == PROFILE_NAMES
    for name in PROFILE_NAMES:
        entries = _profile_entries(name)
        assert entries
        assert len(entries) == len(set(entries))
        assert all(exact_nodeid.fullmatch(entry) for entry in entries)
        assert all((ROOT / entry.split("::", 1)[0]).is_file() for entry in entries)


def test_profiles_load_network_tripwire(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: list[list[str]] = []

    monkeypatch.setattr(
        run_verification.pytest,
        "main",
        lambda args: observed.append(list(args)) or pytest.ExitCode.OK,
    )
    run_verification._run_profile_tests(["tests/test_package_layout.py::test_package_imports"])

    assert observed == [[
        "-q",
        "-p",
        "verification.pytest_network_tripwire",
        "tests/test_package_layout.py::test_package_imports",
    ]]


def test_l1_includes_all_offline_nodeids_from_tasks_0_through_29_including_task25_to_29() -> None:
    entries = set(_profile_entries("paper-l1-components"))

    assert set(L1_TASK_NODEIDS) == set(range(30))
    expected_l1_task_nodeids = {
        nodeid for task, nodeid in L1_TASK_NODEIDS.items() if task != 23
    }
    assert expected_l1_task_nodeids.issubset(entries)
    assert L1_TASK_NODEIDS[23] not in entries
    for relative_path in L1_COMPONENT_FILES:
        planned = _test_function_nodeids(relative_path) - L1_SUPERSEDED_NODEIDS
        assert planned.issubset(entries)
    assert L1_SUPERSEDED_NODEIDS.isdisjoint(entries)
    assert L1_TASK19_NODEIDS.issubset(entries)
    for relative_path, expected_count in L1_TASK20_TO_22_FILES.items():
        nodeids = _test_function_nodeids(relative_path)
        assert len(nodeids) == expected_count
        assert nodeids.issubset(entries)
    for relative_path, expected_count in L1_TASK25_TO_29_FILES.items():
        nodeids = _test_function_nodeids(relative_path)
        assert len(nodeids) == expected_count
        assert nodeids.issubset(entries)


def test_l1_includes_task24_pure_component_but_not_artifact_audit() -> None:
    entries = set(_profile_entries("paper-l1-components"))

    assert L1_TASK_NODEIDS[24] in L1_TASK24_NODEIDS
    assert L1_TASK24_NODEIDS.issubset(entries)
    assert (
        "tests/experiments/test_paper_traceability.py::test_artifact_root_audit_is_separate_l4_nodeid"
        not in entries
    )


def test_l1_includes_one_lean_root_at_most_two_checker_calls() -> None:
    entries = _profile_entries("paper-l1-components")
    lean = [entry for entry in entries if "test_lean_checker_direct.py" in entry]

    assert lean == [
        "tests/plugins/lean_proof/test_lean_checker_direct.py::test_lean_checker_accepts_valid_direct_proof_with_real_environment"
    ]
    source = (ROOT / lean[0].split("::", 1)[0]).read_text(encoding="utf-8")
    function = source.split("def test_lean_checker_accepts_valid_direct_proof_with_real_environment", 1)[1].split("\ndef ", 1)[0]
    assert 1 <= function.count("check_lean_proof(") <= 2


def test_l2_runs_positive_historical_single_leaf_full_path() -> None:
    entries = set(_profile_entries("paper-l2-historical-real"))

    authoritative_nodeids = _test_function_nodeids(
        "tests/experiments/test_paper_historical_real_fixture.py"
    )
    assert len(L2_TASK23_HISTORICAL_SINGLE_LEAF_NODEIDS) == 8
    assert L2_TASK23_HISTORICAL_SINGLE_LEAF_NODEIDS == authoritative_nodeids
    assert entries == L2_TASK23_HISTORICAL_SINGLE_LEAF_NODEIDS


def test_l3_audit_requires_terminal_real_output_or_reports_blocked_not_passed(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    exit_code = run_verification.main(
        ["--profile", "paper-l3-new-real-smoke-audit"]
    )
    output = capsys.readouterr().out

    assert exit_code != 0
    assert '"status": "blocked"' in output
    assert '"status": "passed"' not in output

    (tmp_path / "suite_manifest.json").write_text(
        json.dumps({"capturing": True}), encoding="utf-8"
    )
    import tokenshare.experiments.paper_formal_runner as formal_runner

    monkeypatch.setattr(
        formal_runner,
        "replay_paper_formal_suite",
        lambda **_kwargs: SimpleNamespace(
            status="completed",
            ended_at="2026-08-04T00:00:00Z",
            provider_attempt_count=1,
        ),
    )
    audit = run_verification._audit_terminal_real_output(tmp_path)
    assert audit["status"] == "blocked"
    assert audit["reason"] == "terminal_real_output_required"


def test_l4_contains_task24_artifact_audit_and_requires_l3_pass_two_equal_recomputations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    entries = set(_profile_entries("paper-l4-cell-lineage"))
    assert {
        "tests/experiments/test_paper_traceability.py::test_l4_factory_rejects_manifest_listed_derived_evidence_output",
        "tests/experiments/test_paper_traceability.py::test_l4_protected_replay_input_root_rejects_mutated_or_missing_closure[mutated-direct]",
    }.issubset(entries)
    calls: list[str] = []
    monkeypatch.setattr(
        run_verification,
        "_audit_terminal_real_output",
        lambda _root: calls.append("l3")
        or {"status": "passed", "terminal_status": "completed"},
    )
    monkeypatch.setattr(
        run_verification,
        "_recompute_l4_digests",
        lambda _root: calls.append("l4") or ("sha256:same", "sha256:same"),
    )

    result = run_verification._audit_profile_artifacts(
        "paper-l4-cell-lineage", tmp_path.resolve()
    )

    assert result["status"] == "passed"
    assert result["recomputation_digests"] == ["sha256:same", "sha256:same"]
    assert calls == ["l3", "l4"]


def test_fast_never_claims_l1_to_l4(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(run_verification, "_verify_python_runtime", lambda: None)
    monkeypatch.setattr(run_verification, "_verify_harness_files", lambda: None)
    monkeypatch.setattr(run_verification, "_verify_test_manifest", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(run_verification, "_compile_repository", lambda: None)
    monkeypatch.setattr(run_verification, "_run_pytest", lambda *_args, **_kwargs: None)

    assert run_verification.main([]) == 0
    output = capsys.readouterr().out
    assert all(name not in output for name in run_verification.PROFILE_NAMES)


def test_unknown_profile_or_outside_path_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="Unknown verification profile"):
        run_verification._profile_manifest_path("../paper-l1-components")
    repository = tmp_path / "repository"
    relative_root = repository / "local" / "verification" / "l1-root"
    outputs_root = repository / "outputs" / "l2-root"
    outside_root = tmp_path / "outside-root"
    relative_root.mkdir(parents=True)
    outputs_root.mkdir(parents=True)
    outside_root.mkdir()
    original_root = run_verification.ROOT
    run_verification.ROOT = repository
    try:
        assert run_verification._validate_artifact_root(
            Path("local/verification/l1-root")
        ) == relative_root
        assert run_verification._validate_artifact_root(outputs_root) == outputs_root
        with pytest.raises(SystemExit, match="outside the repository"):
            run_verification._validate_artifact_root(outside_root)
        assert run_verification._audit_profile_artifacts(
            "paper-l1-components", relative_root
        )["status"] == "passed"
        assert run_verification._audit_profile_artifacts(
            "paper-l2-historical-real", outputs_root
        )["status"] == "passed"
    finally:
        run_verification.ROOT = original_root

