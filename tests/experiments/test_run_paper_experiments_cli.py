import json
import os
from pathlib import Path

import tokenshare.experiments.run_paper_experiments as paper_cli
from tokenshare.experiments.paper_budget import load_exp1_pilot_profile
from tokenshare.experiments.run_paper_experiments import main


APPROVED_EXP1_PILOT_DIGEST = (
    # Current schema digest for mock-approved CLI routing tests. The historical
    # real pilot approval digest remains immutable in existing evidence.
    "sha256:fae72f83a8f6c9c4987377282b9f5e770"
    "f361526d957b556c50b32c2b3f4ca83"
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


def test_paper_cli_requires_budget_digest_for_formal_run(tmp_path: Path) -> None:
    exit_code = main(
        [
            "--output-root",
            str(tmp_path),
            "--experiments",
            "exp1",
            "--real-transport",
        ]
    )

    suite = json.loads((tmp_path / "suite_manifest.json").read_text(encoding="utf-8"))
    assert exit_code == 2
    assert suite["status"] == "blocked"
    assert suite["error_summary"][0]["failure_kind"] == "missing_budget_approval"


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


def test_exp1_pilot_cli_unapproved_execution_stops_before_provider_calls(
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
    assert exit_code == 2
    assert suite["suite_id"] == "paper_exp1_minimal_pilot_v1_blocked"
    assert suite["status"] == "blocked"
    assert suite["provider_attempt_count"] == 0
    assert suite["total_tokens"] == 0
    assert suite["error_summary"][0]["failure_kind"] == "missing_budget_approval"


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
