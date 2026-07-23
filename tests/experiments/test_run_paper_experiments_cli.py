import json
import os
from pathlib import Path

import pytest

import tokenshare.experiments.run_paper_experiments as paper_cli
from tokenshare.experiments.factorization_paper_adapter import (
    ScriptedFactorizationRangeTransport,
)
from tokenshare.experiments.paper_budget import load_exp1_pilot_profile
from tokenshare.experiments.paper_catalog import PaperInputCatalogManifest
from tokenshare.experiments.paper_models import PaperStatus, PaperSuiteResult
from tokenshare.experiments.run_paper_experiments import main


APPROVED_EXP1_PILOT_DIGEST = (
    # Current schema digest for mock-approved CLI routing tests. The historical
    # real pilot approval digest remains immutable in existing evidence.
    "sha256:6815a90bad32bb71b53b5ee80d7b103"
    "6ee86c2223f0f3d626908298c8a3c481e"
)


def test_paper_cli_plan_only_writes_budget_and_suite_manifest(tmp_path: Path) -> None:
    exit_code = main(
        [
            "--output-root",
            str(tmp_path),
            "--experiments",
            "exp1",
            "--plan-only",
            "--worker-levels",
            "10",
            "--repeats",
            "1",
            "--seed-family",
            "1",
        ]
    )

    suite_manifest_path = tmp_path / "suite_manifest.json"
    budget_path = tmp_path / "run_budget.json"
    assert exit_code == 0
    assert suite_manifest_path.exists()
    assert budget_path.exists()
    suite = json.loads(suite_manifest_path.read_text(encoding="utf-8"))
    budget = json.loads(budget_path.read_text(encoding="utf-8"))
    assert suite["schema_version"] == "tokenshare.paper_suite_result.v1"
    assert suite["status"] == "planned"
    assert suite["paper_eligible"] is False
    assert budget["planned_experiments"] == ["exp1_real_ai_feasibility"]
    assert budget["quota_preflight"]["provider_calls_made"] == 0
    assert budget["planned_root_runs"] == 1_905
    assert budget["planned_ai_units"] == 8_700
    assert budget["token_upper_bound"] == 142_540_800
    assert budget["cost_upper_bound"] == pytest.approx(435.0)
    dispatch = json.loads(
        (tmp_path / "paper_dispatch_plans.json").read_text(encoding="utf-8")
    )
    assert dispatch["provider_calls_made"] == 0
    assert dispatch["plans"][0]["condition_count"] == 36


def test_paper_cli_plan_only_outputs_blocked_aware_lean_3x3_matrix(
    tmp_path: Path,
) -> None:
    exit_code = main(
        [
            "--output-root",
            str(tmp_path),
            "--experiments",
            "exp1",
            "--plan-only",
            "--worker-levels",
            "10",
            "--repeats",
            "1",
            "--seed-family",
            "1",
        ]
    )

    matrix_path = tmp_path / "lean_3x3_matrix.json"
    budget_path = tmp_path / "run_budget.json"
    assert exit_code == 0
    assert matrix_path.exists()
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    budget = json.loads(budget_path.read_text(encoding="utf-8"))

    assert matrix["schema_version"] == "tokenshare.lean_3x3_matrix_plan.v1"
    assert matrix["cell_count"] == 9
    assert matrix["target_case_count"] == 15
    assert budget["quota_preflight"]["provider_calls_made"] == 0
    assert matrix["provider_calls_made"] == 0
    assert matrix["catalog_digest"].startswith("sha256:")
    assert matrix["environment_digest"].startswith("sha256:")
    assert matrix["matrix_digest"].startswith("sha256:")
    assert budget["quota_preflight"]["lean_3x3_matrix"]["cell_count"] == 9

    cells = {
        (cell["paper_difficulty"], cell["topic_family"]): cell
        for cell in matrix["cells"]
    }
    assert set(cells) == {
        (paper_difficulty, topic_family)
        for paper_difficulty in ("simple", "medium_lemma_dag", "hard_frontier")
        for topic_family in ("pure_logic", "function_set", "induction")
    }

    simple_pure_logic = cells[("simple", "pure_logic")]
    assert simple_pure_logic["catalog_pool_case_count"] == 45
    assert simple_pure_logic["available_case_count"] == 15
    assert simple_pure_logic["expected_ai_unit_count"] == 30
    assert simple_pure_logic["semantic_fingerprint_count"] == 15
    assert simple_pure_logic["preflight_status"]["status_counts"] == {"passed": 15}
    assert simple_pure_logic["paper_eligible_possible"] is True
    assert simple_pure_logic["blocked_reason"] is None

    simple_function_set = cells[("simple", "function_set")]
    assert simple_function_set["status"] == "planned"
    assert simple_function_set["available_case_count"] == 15
    assert simple_function_set["expected_ai_unit_count"] == 15
    assert simple_function_set["semantic_fingerprint_count"] == 15
    assert simple_function_set["paper_eligible_possible"] is True
    assert simple_function_set["blocked_reason"] is None

    simple_induction = cells[("simple", "induction")]
    assert simple_induction["status"] == "planned"
    assert simple_induction["available_case_count"] == 15
    assert simple_induction["expected_ai_unit_count"] == 15
    assert simple_induction["semantic_fingerprint_count"] == 15
    assert simple_induction["paper_eligible_possible"] is True
    assert simple_induction["blocked_reason"] is None

    medium_cells = {
        topic_family: cells[("medium_lemma_dag", topic_family)]
        for topic_family in ("pure_logic", "function_set", "induction")
    }
    assert {
        topic_family: cell["available_case_count"]
        for topic_family, cell in medium_cells.items()
    } == {"pure_logic": 15, "function_set": 15, "induction": 15}
    assert {
        topic_family: cell["expected_ai_unit_count"]
        for topic_family, cell in medium_cells.items()
    } == {"pure_logic": 75, "function_set": 60, "induction": 75}
    assert all(cell["status"] == "planned" for cell in medium_cells.values())
    assert all(cell["blocked_reason"] is None for cell in medium_cells.values())

    hard_expected_ai_units = {
        "pure_logic": 105,
        "function_set": 90,
        "induction": 105,
    }
    for topic_family in ("pure_logic", "function_set", "induction"):
        hard_cell = cells[("hard_frontier", topic_family)]
        assert hard_cell["catalog_case_count"] == 15
        assert hard_cell["available_case_count"] == 15
        assert hard_cell["expected_ai_unit_count"] == hard_expected_ai_units[topic_family]
        assert hard_cell["semantic_fingerprint_count"] == 15
        assert hard_cell["preflight_status"]["status_counts"] == {"passed": 15}
        assert hard_cell["preflight_status"]["summary"] == "passed"
        assert hard_cell["paper_eligible_possible"] is True
        assert hard_cell["blocked_reason"] is None

    assert matrix["topic_family_expected_ai_unit_counts"] == {
        "pure_logic": 210,
        "function_set": 165,
        "induction": 195,
    }
    assert matrix["paper_eligible_possible_cell_count"] == 9
    assert matrix["blocked_cell_count"] == 0
    assert matrix["task15_budget_input"]["selected_case_count"] == 135
    assert matrix["task15_budget_input"]["expected_ai_unit_count"] == 570


def test_paper_cli_plan_only_does_not_count_shallow_v1_as_medium_or_hard(
    tmp_path: Path,
) -> None:
    exit_code = main(
        [
            "--output-root",
            str(tmp_path),
            "--experiments",
            "exp1",
            "--plan-only",
        ]
    )

    assert exit_code == 0
    matrix = json.loads(
        (tmp_path / "lean_3x3_matrix.json").read_text(encoding="utf-8")
    )
    cells = {
        (cell["paper_difficulty"], cell["topic_family"]): cell
        for cell in matrix["cells"]
    }

    assert cells[("medium_lemma_dag", "pure_logic")]["available_case_count"] == 15
    assert cells[("medium_lemma_dag", "function_set")]["available_case_count"] == 15
    assert cells[("medium_lemma_dag", "induction")]["available_case_count"] == 15
    assert cells[("hard_frontier", "pure_logic")]["available_case_count"] == 15
    assert cells[("hard_frontier", "function_set")]["available_case_count"] == 15
    assert cells[("hard_frontier", "induction")]["available_case_count"] == 15


def test_paper_cli_rejects_formal_run_without_real_transport(tmp_path: Path) -> None:
    exit_code = main(["--output-root", str(tmp_path), "--experiments", "exp1"])

    suite = json.loads((tmp_path / "suite_manifest.json").read_text(encoding="utf-8"))
    assert exit_code == 1
    assert suite["status"] == "blocked"
    assert suite["error_summary"][0]["failure_kind"] == "missing_real_transport"


def test_paper_cli_requires_budget_digest_only_when_policy_flag_is_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_budget_policy_cli_boundaries(monkeypatch)
    exit_code = main(
        [
            "--output-root",
            str(tmp_path),
            "--experiments",
            "exp1",
            "--real-transport",
            "--require-budget-approval",
        ]
    )

    suite = json.loads((tmp_path / "suite_manifest.json").read_text(encoding="utf-8"))
    assert exit_code == 2
    assert suite["status"] == "blocked"
    assert suite["error_summary"][0]["failure_kind"] == "missing_budget_approval"


def test_paper_cli_bypasses_manual_budget_approval_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_budget_policy_cli_boundaries(monkeypatch)
    exit_code = main(
        [
            "--output-root",
            str(tmp_path),
            "--experiments",
            "exp1",
            "--real-transport",
        ]
    )

    budget = json.loads((tmp_path / "run_budget.json").read_text(encoding="utf-8"))
    approval = budget["quota_preflight"]["budget_approval"]
    assert exit_code == 0
    assert approval == {
        "approval_required": False,
        "approval_mode": "user_bypassed",
        "authorization_source": "project_policy",
        "budget_digest": budget["budget_digest"],
        "provided_approval_digest": None,
    }
    assert budget["quota_preflight"]["provider_calls_made"] == 0


def test_paper_cli_routes_formal_capturing_run_to_formal_suite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_budget_policy_cli_boundaries(monkeypatch)
    calls: list[dict] = []

    def capture_formal_suite(**kwargs):
        calls.append(kwargs)
        return PaperSuiteResult(
            suite_id="paper_v1_formal_capture",
            status=PaperStatus.COMPLETED,
            output_root=Path(kwargs["output_root"]).as_posix(),
            started_at="2026-07-20T00:00:00Z",
            ended_at="2026-07-20T00:00:01Z",
            experiment_ids=["exp1_real_ai_feasibility"],
            condition_count=1,
            run_count=1,
            task_count=1,
            provider_attempt_count=1,
            total_tokens=8,
            total_cost_estimate=0.0,
            paper_eligible=False,
            eligibility_report_ref=None,
            budget_ref=kwargs["budget"].to_dict(),
            metrics_refs=[],
            audit_refs=[],
            error_summary=[],
        )

    capturing_transport = object()
    capturing_configs = {"capture": object()}
    monkeypatch.setattr(
        paper_cli,
        "execute_paper_formal_suite",
        capture_formal_suite,
        raising=False,
    )

    run_root = tmp_path / "run"
    exit_code = main(
        [
            "--output-root",
            str(run_root),
            "--experiments",
            "exp1",
            "--real-transport",
            "--max-total-provider-attempts",
            "2",
            "--max-total-tokens",
            "64",
            "--max-cost-estimate",
            "1.5",
            "--stop-after-current-task",
        ],
        gate_c_transport=capturing_transport,
        gate_c_ai_api_configs=capturing_configs,
    )

    assert exit_code == 0
    assert len(calls) == 1
    assert calls[0]["transport"] is capturing_transport
    assert calls[0]["ai_api_configs"] == capturing_configs
    assert calls[0]["hard_limits"] == {
        "max_total_provider_attempts": 2,
        "max_total_tokens": 64,
        "max_cost_estimate": 1.5,
        "stop_after_current_task": True,
    }
    suite = json.loads((run_root / "suite_manifest.json").read_text(encoding="utf-8"))
    assert suite["status"] == "completed"
    assert suite["paper_eligible"] is False


def test_paper_cli_formal_replay_skips_catalog_and_provider_config_loading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    replay_result = PaperSuiteResult(
        suite_id="formal-replay-test",
        status=PaperStatus.COMPLETED,
        output_root=tmp_path.as_posix(),
        started_at="2026-07-20T00:00:00Z",
        ended_at="2026-07-20T00:00:01Z",
        experiment_ids=["exp5_real_ai_model_endpoint_comparison"],
        condition_count=1,
        run_count=1,
        task_count=1,
        provider_attempt_count=1,
        total_tokens=10,
        total_cost_estimate=0.1,
        paper_eligible=False,
        eligibility_report_ref=None,
        budget_ref=None,
        metrics_refs=[],
        audit_refs=[],
        error_summary=[],
    )

    monkeypatch.setattr(
        paper_cli,
        "replay_paper_formal_suite",
        lambda **_kwargs: calls.append("replay") or replay_result,
    )
    monkeypatch.setattr(
        paper_cli,
        "_load_default_paper_catalogs",
        lambda: (_ for _ in ()).throw(AssertionError("catalog must not load")),
    )
    monkeypatch.setattr(
        paper_cli,
        "_model_endpoint_cohort_preflight_for_suite",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("provider cohort config must not load")
        ),
    )
    monkeypatch.setattr(
        paper_cli,
        "recompute_paper_formal_metrics",
        lambda _root: calls.append("metrics") or object(),
    )
    monkeypatch.setattr(
        paper_cli,
        "generate_paper_formal_report",
        lambda **_kwargs: calls.append("report"),
    )

    class _EvidenceStore:
        def __init__(self, _root):
            pass

        def _refresh_evidence_manifest(self):
            calls.append("refresh")

    monkeypatch.setattr(paper_cli, "FormalEvidenceStore", _EvidenceStore)

    exit_code = main(
        [
            "--output-root",
            str(tmp_path),
            "--experiments",
            "exp5",
            "--replay-only",
        ]
    )

    assert exit_code == 0
    assert calls == ["replay", "metrics", "report", "refresh"]


def test_exp1_pilot_cli_plan_only_writes_independent_zero_call_budget(
    tmp_path: Path,
) -> None:
    exit_code = main(
        [
            "--output-root",
            str(tmp_path),
            "--experiments",
            "exp1",
            "--exp1-pilot-profile",
            "benchmarks/paper/exp1_minimal_pilot_profile.v1.json",
            "--plan-only",
        ]
    )

    suite = json.loads(
        (tmp_path / "suite_manifest.json").read_text(encoding="utf-8")
    )
    budget = json.loads((tmp_path / "run_budget.json").read_text(encoding="utf-8"))
    frozen_profile = json.loads(
        (tmp_path / "exp1_pilot_profile.json").read_text(encoding="utf-8")
    )
    assert exit_code == 0
    assert suite["suite_id"] == "paper_exp1_minimal_pilot_v1"
    assert suite["run_count"] == 8
    assert suite["task_count"] == 8
    assert suite["provider_attempt_count"] == 0
    assert suite["paper_eligible"] is False
    assert budget["planned_root_runs"] == 8
    assert budget["planned_ai_units"] == 22
    assert budget["max_provider_attempts"] == 22
    assert budget["quota_preflight"]["provider_calls_made"] == 0
    assert budget["quota_preflight"]["exp1_pilot"]["blocked_root_runs"] == 1
    assert frozen_profile["profile_digest"].startswith("sha256:")
    assert frozen_profile["baseline_provider"][
        "source_provider_config_digest"
    ].startswith("sha256:")
    assert '"api_key":' not in json.dumps(frozen_profile)


def test_exp1_pilot_profile_execution_requires_pilot_mode_before_provider_calls(
    tmp_path: Path,
) -> None:
    exit_code = main(
        [
            "--output-root",
            str(tmp_path),
            "--experiments",
            "exp1",
            "--exp1-pilot-profile",
            "benchmarks/paper/exp1_minimal_pilot_profile.v1.json",
            "--real-transport",
        ]
    )

    suite = json.loads(
        (tmp_path / "suite_manifest.json").read_text(encoding="utf-8")
    )
    assert exit_code == 3
    assert suite["suite_id"] == "paper_exp1_minimal_pilot_v1_blocked"
    assert suite["status"] == "blocked"
    assert suite["provider_attempt_count"] == 0
    assert suite["total_tokens"] == 0
    assert suite["error_summary"][0]["failure_kind"] == "invalid_pilot_mode"


def test_exp1_pilot_cli_requires_frozen_baseline_entry_before_execution(
    tmp_path: Path,
) -> None:
    exit_code = main(
        [
            "--output-root",
            str(tmp_path),
            "--experiments",
            "exp1",
            "--exp1-pilot-profile",
            "benchmarks/paper/exp1_minimal_pilot_profile.v1.json",
            "--pilot",
            "--real-transport",
            "--approve-budget-digest",
            APPROVED_EXP1_PILOT_DIGEST,
        ]
    )

    suite = json.loads(
        (
            tmp_path
            / "paper_exp1_minimal_pilot_v1_blocked"
            / "suite_manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert exit_code == 3
    assert suite["status"] == "blocked"
    assert suite["provider_attempt_count"] == 0
    assert suite["error_summary"][0]["failure_kind"] == (
        "missing_baseline_entry_id"
    )


def test_exp1_pilot_cli_blocks_wrong_baseline_entry_without_provider_calls(
    tmp_path: Path,
) -> None:
    formal_suite_root = tmp_path / "paper_exp1_minimal_pilot_v1"
    formal_suite_root.mkdir(parents=True)
    formal_manifest = formal_suite_root / "suite_manifest.json"
    formal_manifest.write_text(
        '{"schema_version":"sentinel.formal_suite.v1","status":"completed"}',
        encoding="utf-8",
    )
    formal_manifest_before = formal_manifest.read_bytes()

    exit_code = main(
        [
            "--output-root",
            str(tmp_path),
            "--experiments",
            "exp1",
            "--exp1-pilot-profile",
            "benchmarks/paper/exp1_minimal_pilot_profile.v1.json",
            "--pilot",
            "--real-transport",
            "--approve-budget-digest",
            APPROVED_EXP1_PILOT_DIGEST,
            "--baseline-entry-id",
            "wrong-entry",
        ]
    )

    suite = json.loads(
        (
            tmp_path
            / "paper_exp1_minimal_pilot_v1_blocked"
            / "suite_manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert exit_code == 3
    assert suite["status"] == "blocked"
    assert suite["provider_attempt_count"] == 0
    assert suite["total_tokens"] == 0
    assert suite["error_summary"][0]["failure_kind"] == (
        "baseline_entry_mismatch"
    )
    assert formal_manifest.read_bytes() == formal_manifest_before


def test_exp1_pilot_cli_routes_approved_limits_and_resume_to_orchestrator(
    tmp_path: Path,
    monkeypatch,
) -> None:
    received: dict = {}

    def fake_execute_exp1_pilot(**kwargs):
        received.update(kwargs)
        suite_root = kwargs["output_base"] / "paper_exp1_minimal_pilot_v1"
        suite_root.mkdir(parents=True, exist_ok=True)
        body = {
            "schema_version": "tokenshare.paper_exp1_pilot_execution_result.v1",
            "suite_id": "paper_exp1_minimal_pilot_v1",
            "status": "completed_with_failures",
            "output_root": suite_root.as_posix(),
            "provider_attempt_count": 0,
            "provider_calls_made": 0,
        }
        (suite_root / "suite_manifest.json").write_text(
            json.dumps(body, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return _FakeExecutionResult(body)

    monkeypatch.setattr(
        paper_cli,
        "execute_exp1_pilot",
        fake_execute_exp1_pilot,
    )

    exit_code = main(
        [
            "--output-root",
            str(tmp_path),
            "--experiments",
            "exp1",
            "--exp1-pilot-profile",
            "benchmarks/paper/exp1_minimal_pilot_profile.v1.json",
            "--pilot",
            "--real-transport",
            "--approve-budget-digest",
            APPROVED_EXP1_PILOT_DIGEST,
            "--baseline-entry-id",
            "glm_5_2_exp1_baseline",
            "--provider-attempt-limit",
            "11",
            "--token-limit",
            "50000",
            "--cost-limit",
            "0.06",
            "--resume",
            "--case-id",
            "factor_easy_01",
            "--ai-unit-id",
            "range_0",
            "--pilot-output-root",
            str(tmp_path / "isolated-unit"),
        ]
    )

    assert exit_code == 0
    assert received["approved_budget_digest"] == APPROVED_EXP1_PILOT_DIGEST
    assert received["baseline_entry_id"] == "glm_5_2_exp1_baseline"
    assert received["output_base"] == tmp_path
    assert received["real_transport"] is True
    assert received["provider_attempt_limit"] == 11
    assert received["token_limit"] == 50000
    assert received["cost_limit"] == 0.06
    assert received["resume"] is True
    assert received["replay_only"] is False
    assert received["case_id"] == "factor_easy_01"
    assert received["ai_unit_id"] == "range_0"
    assert received["execution_output_root"] == tmp_path / "isolated-unit"


def test_general_gate_c_cli_routes_single_exp2_case_unit_through_shared_runner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plan_root = tmp_path / "plan"
    assert main(
        [
            "--output-root",
            str(plan_root),
            "--experiments",
            "exp2",
            "--plan-only",
        ]
    ) == 0
    budget = json.loads((plan_root / "run_budget.json").read_text(encoding="utf-8"))
    dispatch_bundle = json.loads(
        (plan_root / "paper_dispatch_plans.json").read_text(encoding="utf-8")
    )
    plan = dispatch_bundle["plans"][0]
    pair = next(
        (condition, selection)
        for condition, selection in zip(
            plan["conditions"],
            plan["selections"],
            strict=True,
        )
        if condition["domain"] == "factorization"
        and condition["paper_difficulty"] == "easy"
        and condition["worker_count"] == 1
        and condition["repeat_id"] == 0
    )
    condition, selection = pair
    observed: dict = {}

    def fake_execute_gate_c_pilot_case(**kwargs):
        observed.update(kwargs)
        body = {
            "schema_version": "tokenshare.paper_gate_c_pilot_execution_result.v1",
            "suite_id": "gate_c_cli_exp2_test",
            "experiment_id": "exp2_real_ai_scalability",
            "condition_id": condition["condition_id"],
            "case_id": selection["ordered_case_ids"][0],
            "ai_unit_id": "range_0",
            "status": "completed_with_failures",
            "output_root": (tmp_path / "isolated-exp2").as_posix(),
            "provider_calls_made": 0,
            "transport_calls_observed": 0,
            "paper_eligible": False,
            "pilot_only": True,
        }
        return _FakeExecutionResult(body)

    monkeypatch.setattr(
        paper_cli,
        "execute_gate_c_pilot_case",
        fake_execute_gate_c_pilot_case,
    )
    pilot_root = tmp_path / "isolated-exp2"
    exit_code = main(
        [
            "--output-root",
            str(plan_root),
            "--experiments",
            "exp2",
            "--pilot",
            "--real-transport",
            "--replay-only",
            "--approve-budget-digest",
            budget["budget_digest"],
            "--condition-id",
            condition["condition_id"],
            "--case-id",
            selection["ordered_case_ids"][0],
            "--ai-unit-id",
            "range_0",
            "--pilot-output-root",
            str(pilot_root),
        ]
    )

    assert exit_code == 0
    assert observed["experiment_id"] == "exp2_real_ai_scalability"
    assert observed["condition_id"] == condition["condition_id"]
    assert observed["case_id"] == selection["ordered_case_ids"][0]
    assert observed["ai_unit_id"] == "range_0"
    assert observed["approved_budget_digest"] == budget["budget_digest"]
    assert observed["execution_output_root"] == pilot_root
    assert observed["real_transport"] is True
    assert observed["replay_only"] is True
    assert observed["ai_api_configs"] == {}


def test_general_gate_c_cli_reaches_registered_module_adapter_and_capture(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plan_root = tmp_path / "plan-e2e"
    assert main(
        [
            "--output-root",
            str(plan_root),
            "--experiments",
            "exp2",
            "--plan-only",
        ]
    ) == 0
    budget = json.loads((plan_root / "run_budget.json").read_text(encoding="utf-8"))
    plan = json.loads(
        (plan_root / "paper_dispatch_plans.json").read_text(encoding="utf-8")
    )["plans"][0]
    condition, selection = next(
        (condition, selection)
        for condition, selection in zip(
            plan["conditions"],
            plan["selections"],
            strict=True,
        )
        if condition["domain"] == "factorization"
        and condition["paper_difficulty"] == "easy"
        and condition["worker_count"] == 1
        and condition["repeat_id"] == 0
    )
    profile = load_exp1_pilot_profile(
        "benchmarks/paper/exp1_minimal_pilot_profile.v1.json"
    )
    entry = profile.source_provider_config.entries[0]
    monkeypatch.setenv(entry.api_key_env, "gate-c-cli-e2e-capture-key")
    transport = _CLICapturingTransport()
    pilot_root = tmp_path / "pilot-e2e"

    exit_code = main(
        [
            "--output-root",
            str(plan_root),
            "--experiments",
            "exp2",
            "--pilot",
            "--real-transport",
            "--approve-budget-digest",
            budget["budget_digest"],
            "--condition-id",
            condition["condition_id"],
            "--case-id",
            selection["ordered_case_ids"][0],
            "--pilot-output-root",
            str(pilot_root),
        ],
        gate_c_transport=transport,
        gate_c_ai_api_configs={
            profile.model_endpoint_identity.provider_config_id: (
                profile.source_provider_config
            )
        },
    )

    assert exit_code == 0
    assert transport.calls
    assert all(
        call["response_format"] == {"type": "json_object"}
        and call["temperature"] == 0
        and call["enable_thinking"] is False
        for call in transport.calls
    )
    suite = json.loads(
        (pilot_root / "suite_manifest.json").read_text(encoding="utf-8")
    )
    assert suite["experiment_id"] == "exp2_real_ai_scalability"
    assert suite["provider_calls_made"] == 0
    assert suite["transport_calls_observed"] == len(transport.calls)
    assert suite["paper_eligible"] is False


def test_exp1_pilot_cli_rejects_unpaired_or_non_pilot_unit_selector(
    tmp_path: Path,
) -> None:
    unpaired = main(
        [
            "--output-root",
            str(tmp_path / "unpaired"),
            "--experiments",
            "exp1",
            "--case-id",
            "factor_easy_01",
        ]
    )
    non_pilot = main(
        [
            "--output-root",
            str(tmp_path / "non-pilot"),
            "--experiments",
            "exp1",
            "--case-id",
            "factor_easy_01",
            "--ai-unit-id",
            "range_0",
            "--plan-only",
        ]
    )
    missing_isolated_root = main(
        [
            "--output-root",
            str(tmp_path / "missing-isolated-root"),
            "--experiments",
            "exp1",
            "--pilot",
            "--case-id",
            "factor_easy_01",
            "--ai-unit-id",
            "range_0",
        ]
    )

    assert unpaired == 3
    assert non_pilot == 3
    assert missing_isolated_root == 3
    unpaired_suite = json.loads(
        (tmp_path / "unpaired" / "suite_manifest.json").read_text(encoding="utf-8")
    )
    non_pilot_suite = json.loads(
        (tmp_path / "non-pilot" / "suite_manifest.json").read_text(encoding="utf-8")
    )
    missing_root_suite = json.loads(
        (tmp_path / "missing-isolated-root" / "suite_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert unpaired_suite["error_summary"][0]["failure_kind"] == "invalid_pilot_selector"
    assert non_pilot_suite["error_summary"][0]["failure_kind"] == "invalid_pilot_selector"
    assert missing_root_suite["error_summary"][0]["failure_kind"] == "invalid_pilot_selector"


def test_paper_cli_defaults_to_factorization_catalog_v2() -> None:
    assert paper_cli.DEFAULT_FACTOR_CATALOG == Path(
        "benchmarks/paper/factorization_catalog.v2.jsonl"
    )
    assert paper_cli.DEFAULT_EXP1_PILOT_FACTOR_CATALOG == Path(
        "benchmarks/paper/factorization_catalog.v1.jsonl"
    )


def _patch_budget_policy_cli_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = PaperInputCatalogManifest(
        catalog_id="tokenshare.paper.catalog",
        catalog_version="v2",
        catalog_digest="sha256:" + "1" * 64,
        generator_version="budget_policy_cli_fixture",
        case_count=0,
        domain_counts={},
        difficulty_counts={},
        paper_difficulty_counts={},
        topic_family_counts={},
        paper_difficulty_topic_family_counts={},
        oracle_validation_status="passed",
        lean_preflight_status="passed",
        lean_preflight_summary={},
        lean_lemma_graph_preflight_summary={},
        created_at="2026-07-20T00:00:00Z",
        source_files=[],
        factorization_cases=(),
        lean_cases=(),
        lean_lemma_graph_cases=(),
    )
    monkeypatch.setattr(
        paper_cli,
        "_load_default_paper_catalogs",
        lambda: catalog,
    )
    monkeypatch.setattr(
        paper_cli,
        "build_lean_3x3_matrix_plan",
        lambda **_kwargs: {
            "schema_version": "tokenshare.lean_3x3_matrix_plan.v1",
            "catalog_digest": catalog.catalog_digest,
            "matrix_digest": "sha256:" + "2" * 64,
            "provider_calls_made": 0,
        },
    )
    monkeypatch.setattr(
        paper_cli,
        "build_gate_c_dispatch_plans",
        lambda **_kwargs: (),
    )

    def fake_formal_suite(**kwargs):
        return PaperSuiteResult(
            suite_id="paper_v2_budget_policy_fixture",
            status=PaperStatus.COMPLETED,
            output_root=Path(kwargs["output_root"]).as_posix(),
            started_at="2026-07-20T00:00:00Z",
            ended_at="2026-07-20T00:00:01Z",
            experiment_ids=["exp1_real_ai_feasibility"],
            condition_count=0,
            run_count=0,
            task_count=0,
            provider_attempt_count=0,
            total_tokens=0,
            total_cost_estimate=0.0,
            paper_eligible=False,
            eligibility_report_ref=None,
            budget_ref=kwargs["budget"].to_dict(),
            metrics_refs=[],
            audit_refs=[],
            error_summary=[],
        )

    monkeypatch.setattr(
        paper_cli,
        "execute_paper_formal_suite",
        fake_formal_suite,
        raising=False,
    )


@pytest.mark.parametrize(
    "root_relation",
    ("equal", "parent", "child"),
)
def test_exp1_pilot_cli_rejects_canonical_selector_output_root_overlap(
    tmp_path: Path,
    root_relation: str,
) -> None:
    output_base = tmp_path / root_relation
    canonical_root = output_base / "paper_exp1_minimal_pilot_v1"
    pilot_output_root = {
        "equal": canonical_root,
        "parent": output_base,
        "child": canonical_root / "selected-unit",
    }[root_relation]

    exit_code = main(
        [
            "--output-root",
            str(output_base),
            "--experiments",
            "exp1",
            "--exp1-pilot-profile",
            "benchmarks/paper/exp1_minimal_pilot_profile.v1.json",
            "--pilot",
            "--real-transport",
            "--approve-budget-digest",
            APPROVED_EXP1_PILOT_DIGEST,
            "--baseline-entry-id",
            "glm_5_2_exp1_baseline",
            "--resume",
            "--case-id",
            "factor_easy_01",
            "--ai-unit-id",
            "range_0",
            "--pilot-output-root",
            str(pilot_output_root),
        ]
    )

    assert exit_code == 3
    blocked_root = output_base / "paper_exp1_minimal_pilot_v1_blocked"
    suite = json.loads(
        (blocked_root / "suite_manifest.json").read_text(encoding="utf-8")
    )
    assert suite["error_summary"][0]["failure_kind"] == "invalid_pilot_selector"
    assert "must not overlap canonical Exp1 pilot root" in suite["error_summary"][0][
        "message"
    ]
    assert not (canonical_root / "execution_plan.json").exists()
    assert not (canonical_root / "suite_manifest.json").exists()


def test_exp1_pilot_cli_injects_matching_local_key_into_approved_env(
    tmp_path: Path,
    monkeypatch,
) -> None:
    secret = "test-local-key-never-persisted"
    local_config = tmp_path / "local_ai_api.json"
    local_config.write_text(
        json.dumps(
            {
                "schema_version": "phase7.ai_api_executor_config.v1",
                "executor_id": "test_local_loader",
                "provider_family": "siliconflow",
                "selection_policy": {
                    "kind": "uniform_random_without_weights",
                    "seed_source": "request_or_environment_seed",
                },
                "defaults": {
                    "timeout_seconds": 30,
                    "max_tokens": 1024,
                    "temperature": 0.0,
                    "top_p": 1.0,
                    "stream": False,
                    "max_provider_attempts": 1,
                },
                "entries": [
                    {
                        "entry_id": "local_glm",
                        "enabled": True,
                        "base_url": "https://api.siliconflow.cn/v1",
                        "api_key_env": "TOKENSHARE_TEST_LOCAL_KEY",
                        "api_key": secret,
                        "model": "zai-org/GLM-5.2",
                        "endpoint": "/chat/completions",
                        "supports_json_mode": True,
                        "supports_streaming": False,
                        "request_overrides": {
                            "temperature": 0.0,
                            "enable_thinking": False,
                        },
                        "pricing": {
                            "currency": "USD",
                            "input_per_million_tokens": 1.0,
                            "output_per_million_tokens": 2.0,
                        },
                        "tags": ["test"],
                    }
                ],
                "local_concurrency": {"max_in_flight_global": 1},
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("TOKENSHARE_EXP1_BASELINE_API_KEY", raising=False)
    observed: dict = {}

    def fake_execute_exp1_pilot(**kwargs):
        observed["approved_env_value"] = os.environ.get(
            "TOKENSHARE_EXP1_BASELINE_API_KEY"
        )
        observed["source_config_digest"] = (
            kwargs["pilot_profile"].source_provider_config.config_digest
        )
        suite_root = kwargs["output_base"] / "paper_exp1_minimal_pilot_v1"
        suite_root.mkdir(parents=True, exist_ok=True)
        body = {
            "schema_version": "tokenshare.paper_exp1_pilot_execution_result.v1",
            "suite_id": "paper_exp1_minimal_pilot_v1",
            "status": "completed_with_failures",
            "output_root": suite_root.as_posix(),
            "provider_attempt_count": 0,
            "provider_calls_made": 0,
        }
        (suite_root / "suite_manifest.json").write_text(
            json.dumps(body), encoding="utf-8"
        )
        return _FakeExecutionResult(body)

    monkeypatch.setattr(paper_cli, "execute_exp1_pilot", fake_execute_exp1_pilot)

    exit_code = main(
        [
            "--output-root",
            str(tmp_path / "outputs"),
            "--experiments",
            "exp1",
            "--exp1-pilot-profile",
            "benchmarks/paper/exp1_minimal_pilot_profile.v1.json",
            "--pilot",
            "--real-transport",
            "--approve-budget-digest",
            APPROVED_EXP1_PILOT_DIGEST,
            "--baseline-entry-id",
            "glm_5_2_exp1_baseline",
            "--ai-api-config",
            str(local_config),
        ]
    )

    assert exit_code == 0
    assert observed["approved_env_value"] == secret
    assert observed["source_config_digest"] == (
        load_exp1_pilot_profile(
            "benchmarks/paper/exp1_minimal_pilot_profile.v1.json"
        ).source_provider_config.config_digest
    )


class _FakeExecutionResult:
    def __init__(self, body: dict) -> None:
        self.body = body

    def to_dict(self) -> dict:
        return dict(self.body)


class _CLICapturingTransport:
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
