import json
from pathlib import Path

import pytest

import tokenshare.experiments.run_paper_experiments as paper_cli
from tokenshare.experiments.paper_catalog import PaperInputCatalogManifest
from tokenshare.experiments.paper_models import PaperStatus, PaperSuiteResult
from tokenshare.experiments.run_paper_experiments import main


def test_paper_cli_defaults_to_factorization_catalog_v2() -> None:
    assert paper_cli.DEFAULT_FACTOR_CATALOG == Path(
        "benchmarks/paper/factorization_catalog.v2.jsonl"
    )
    assert paper_cli.DEFAULT_EXP1_PILOT_FACTOR_CATALOG == Path(
        "benchmarks/paper/factorization_catalog.v1.jsonl"
    )


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


def _patch_budget_policy_cli_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-budget-policy-key")
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
