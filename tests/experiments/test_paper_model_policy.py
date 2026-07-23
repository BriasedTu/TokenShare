import importlib
import importlib.util
import json
from pathlib import Path

import pytest

from tokenshare.experiments.factorization_paper_adapter import (
    ScriptedFactorizationRangeTransport,
    run_factorization_paper_case,
)
from tokenshare.experiments.lean_paper_adapter import (
    ScriptedLeanPaperProofTransport,
    run_lean_paper_case,
)
from tokenshare.experiments.paper_catalog import load_paper_catalogs
from tokenshare.experiments.paper_models import PaperExperimentCondition
from tokenshare.experiments.paper_runner import expand_plan_conditions
from tokenshare.experiments.run_paper_experiments import main


FACTOR_CATALOG = "benchmarks/paper/factorization_catalog.v1.jsonl"
LEAN_CATALOG = "benchmarks/paper/lean_catalog.v1.jsonl"


def test_paper_condition_accepts_fixed_entry_model_policy_and_records_cohort_fields() -> None:
    condition = _condition(
        domain="factorization",
        difficulty="easy",
        model_policy="fixed_entry",
        model_cohort_id="tokenshare.paper.model_endpoint_cohort.v1",
        cohort_member_id="glm_5_2_siliconflow",
        model_entry_id="glm-entry",
        provider_family="siliconflow",
        provider_model_id="zai-org/GLM-5.2",
        reasoning_profile_id="default",
        model_cohort_digest="sha256:" + "2" * 64,
    )

    body = condition.to_dict()

    assert body["model_policy"] == "fixed_entry"
    assert body["model_cohort_id"] == "tokenshare.paper.model_endpoint_cohort.v1"
    assert body["cohort_member_id"] == "glm_5_2_siliconflow"
    assert body["model_entry_id"] == "glm-entry"
    assert body["provider_family"] == "siliconflow"
    assert body["provider_model_id"] == "zai-org/GLM-5.2"
    assert body["reasoning_profile_id"] == "default"
    assert body["model_cohort_digest"] == "sha256:" + "2" * 64

    with pytest.raises(ValueError, match="model_policy"):
        _condition(
            domain="factorization",
            difficulty="easy",
            model_policy="strongest_available",
        )


def test_model_endpoint_cohort_preflight_selects_explicit_fixed_entries_and_blocks_incomplete_cohort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _policy_module()
    cohort_path = _write_cohort(tmp_path / "cohort.json")
    entry_map_path = _write_entry_map(tmp_path / "entry_map.json", include_gpt=True)
    sf_config_path = _write_provider_config(
        tmp_path / "siliconflow.json",
        provider_family="siliconflow",
        entries={
            "glm-entry": ("zai-org/GLM-5.2", "TOKENSHARE_GLM_KEY"),
            "qwen-entry": ("Qwen/Qwen3.6-27B", "TOKENSHARE_QWEN_KEY"),
        },
    )
    openai_config_path = _write_provider_config(
        tmp_path / "openai.json",
        provider_family="openai",
        entries={"gpt-entry": ("gpt-5.6-sol", "TOKENSHARE_GPT_KEY")},
    )
    for env_name in ("TOKENSHARE_GLM_KEY", "TOKENSHARE_QWEN_KEY", "TOKENSHARE_GPT_KEY"):
        monkeypatch.setenv(env_name, "test-key")

    cohort = policy.load_model_endpoint_cohort(cohort_path)
    entry_map = policy.load_model_entry_map(entry_map_path)
    provider_configs = policy.load_provider_config_map(
        {"siliconflow": sf_config_path, "openai": openai_config_path}
    )
    preflight = policy.build_model_endpoint_cohort_preflight(
        cohort=cohort,
        entry_map=entry_map,
        provider_configs=provider_configs,
    )

    assert preflight["status"] == "planned"
    assert preflight["model_policy"] == "fixed_entry"
    assert preflight["provider_calls_made"] == 0
    assert set(preflight["member_plans"]) == {
        "glm_5_2_siliconflow",
        "qwen3_6_27b_siliconflow",
        "gpt_5_6_sol_high_openai",
    }
    assert preflight["member_plans"]["gpt_5_6_sol_high_openai"]["selected_entry_id"] == (
        "gpt-entry"
    )
    assert preflight["member_plans"]["gpt_5_6_sol_high_openai"]["provider_family"] == "openai"
    assert preflight["member_plans"]["gpt_5_6_sol_high_openai"]["reasoning_profile_id"] == (
        "high"
    )
    gpt_plan = preflight["member_plans"]["gpt_5_6_sol_high_openai"]
    gpt_identity = gpt_plan["endpoint_identity"]
    assert gpt_plan["provider_config_id"] == "openai"
    assert (
        gpt_plan["source_provider_config_digest"]
        == provider_configs["openai"].config_digest
    )
    assert gpt_plan["model_endpoint_identity_digest"].startswith("sha256:")
    assert (
        gpt_plan["model_endpoint_identity_digest"]
        == gpt_identity["model_endpoint_identity_digest"]
    )
    assert gpt_identity["selected_entry_id"] == "gpt-entry"
    assert gpt_identity["effective_reasoning_controls"] == {
        "reasoning_effort": "high"
    }
    assert "api_key_env" not in gpt_identity

    incomplete = policy.build_model_endpoint_cohort_preflight(
        cohort=cohort,
        entry_map=policy.load_model_entry_map(
            _write_entry_map(tmp_path / "entry_map_missing_gpt.json", include_gpt=False)
        ),
        provider_configs=provider_configs,
    )

    assert incomplete["status"] == "blocked"
    assert incomplete["blocked_reason"] == "incomplete_model_cohort"
    assert incomplete["paper_eligible_possible"] is False
    assert incomplete["provider_calls_made"] == 0
    assert "gpt_5_6_sol_high_openai" in incomplete["missing_members"]


def test_model_endpoint_cohort_preflight_blocks_identity_entry_map_and_smoke_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _policy_module()
    for env_name in (
        "TOKENSHARE_GLM_KEY",
        "TOKENSHARE_QWEN_KEY",
        "TOKENSHARE_GPT_KEY",
        "TOKENSHARE_WRONG_PROVIDER_KEY",
    ):
        monkeypatch.setenv(env_name, "test-key")

    def run_preflight(
        *,
        cohort_overrides: dict[str, dict] | None = None,
        entry_map_overrides: dict[str, dict] | None = None,
        provider_configs: dict[str, Path] | None = None,
        entry_map_cohort_id: str = "tokenshare.paper.model_endpoint_cohort.v1",
        include_gpt: bool = True,
        extra_member: bool = False,
        omit_member_id: str | None = None,
    ) -> dict:
        cohort = policy.load_model_endpoint_cohort(
            _write_cohort(
                tmp_path / f"cohort_{len(list(tmp_path.glob('cohort_*.json')))}.json",
                member_overrides=cohort_overrides,
                extra_member=extra_member,
                omit_member_id=omit_member_id,
            )
        )
        entry_map = policy.load_model_entry_map(
            _write_entry_map(
                tmp_path
                / f"entry_map_{len(list(tmp_path.glob('entry_map_*.json')))}.json",
                include_gpt=include_gpt,
                cohort_id=entry_map_cohort_id,
                member_overrides=entry_map_overrides,
            )
        )
        configs = provider_configs or _valid_provider_config_paths(tmp_path)
        return policy.build_model_endpoint_cohort_preflight(
            cohort=cohort,
            entry_map=entry_map,
            provider_configs=policy.load_provider_config_map(configs),
        )

    wrong_model_configs = _valid_provider_config_paths(
        tmp_path,
        glm_model="wrong/Not-GLM-5.2",
    )
    assert _blocked(
        run_preflight(
            cohort_overrides={
                "glm_5_2_siliconflow": {"provider_model_id": "wrong/Not-GLM-5.2"}
            },
            provider_configs=wrong_model_configs,
        )
    )

    wrong_provider_configs = _valid_provider_config_paths(
        tmp_path,
        openai_entries={
            "gpt-entry": ("gpt-5.6-sol", "TOKENSHARE_GPT_KEY"),
            "glm-openai-entry": (
                "zai-org/GLM-5.2",
                "TOKENSHARE_WRONG_PROVIDER_KEY",
            ),
        },
    )
    assert _blocked(
        run_preflight(
            cohort_overrides={
                "glm_5_2_siliconflow": {"provider_family": "openai"}
            },
            entry_map_overrides={
                "glm_5_2_siliconflow": {
                    "provider_config_id": "openai",
                    "entry_id": "glm-openai-entry",
                    "smoke_evidence_ref": _smoke_evidence(
                        member_id="glm_5_2_siliconflow",
                        entry_id="glm-openai-entry",
                        provider_family="openai",
                        provider_model_id="zai-org/GLM-5.2",
                        reasoning_profile_id="default",
                    ),
                }
            },
            provider_configs=wrong_provider_configs,
        )
    )

    assert _blocked(
        run_preflight(
            cohort_overrides={
                "gpt_5_6_sol_high_openai": {"reasoning_profile_id": "low"}
            },
            entry_map_overrides={
                "gpt_5_6_sol_high_openai": {
                    "smoke_evidence_ref": _smoke_evidence(
                        member_id="gpt_5_6_sol_high_openai",
                        entry_id="gpt-entry",
                        provider_family="openai",
                        provider_model_id="gpt-5.6-sol",
                        reasoning_profile_id="low",
                    )
                }
            },
        )
    )

    assert _blocked(run_preflight(entry_map_cohort_id="wrong.cohort"))
    assert _blocked(
        run_preflight(
            entry_map_overrides={
                "glm_5_2_siliconflow": {"smoke_evidence_ref": None}
            }
        )
    )
    assert _blocked(
        run_preflight(
            entry_map_overrides={
                "glm_5_2_siliconflow": {
                    "smoke_evidence_ref": _smoke_evidence(
                        member_id="glm_5_2_siliconflow",
                        entry_id="glm-entry",
                        provider_family="openai",
                        provider_model_id="zai-org/GLM-5.2",
                        reasoning_profile_id="default",
                    )
                }
            }
        )
    )
    assert _blocked(run_preflight(extra_member=True))
    missing = run_preflight(omit_member_id="qwen3_6_27b_siliconflow")
    assert _blocked(missing)
    assert missing["provider_calls_made"] == 0


def test_expand_plan_conditions_uses_exp5_preflight_fixed_entries_without_breaking_exp1_exp4(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _policy_module()
    catalog = _catalog()
    for env_name in ("TOKENSHARE_GLM_KEY", "TOKENSHARE_QWEN_KEY", "TOKENSHARE_GPT_KEY"):
        monkeypatch.setenv(env_name, "test-key")
    cohort = policy.load_model_endpoint_cohort(_write_cohort(tmp_path / "cohort.json"))
    preflight = policy.build_model_endpoint_cohort_preflight(
        cohort=cohort,
        entry_map=policy.load_model_entry_map(
            _write_entry_map(tmp_path / "entry_map.json", include_gpt=True)
        ),
        provider_configs=policy.load_provider_config_map(
            _valid_provider_config_paths(tmp_path)
        ),
    )

    exp5_conditions = expand_plan_conditions(
        catalog_manifest=catalog,
        experiment_ids=("exp5_real_ai_model_endpoint_comparison",),
        worker_levels=(10,),
        repeats=1,
        seed_family=(1,),
        model_endpoint_cohort_preflight=preflight,
    )

    assert len(exp5_conditions) == 18
    assert {condition.model_policy for condition in exp5_conditions} == {"fixed_entry"}
    assert {condition.experiment_id for condition in exp5_conditions} == {
        "exp5_real_ai_model_endpoint_comparison"
    }
    assert {condition.cohort_member_id for condition in exp5_conditions} == {
        "glm_5_2_siliconflow",
        "qwen3_6_27b_siliconflow",
        "gpt_5_6_sol_high_openai",
    }
    assert {condition.provider_family for condition in exp5_conditions} == {
        "siliconflow",
        "openai",
    }
    entry_by_member = {
        member_id: plan["selected_entry_id"]
        for member_id, plan in preflight["member_plans"].items()
    }
    assert all(
        condition.model_entry_id == entry_by_member[condition.cohort_member_id]
        for condition in exp5_conditions
    )
    plan_by_member = preflight["member_plans"]
    assert all(
        condition.provider_config_id
        == plan_by_member[condition.cohort_member_id]["provider_config_id"]
        and condition.source_provider_config_digest
        == plan_by_member[condition.cohort_member_id][
            "source_provider_config_digest"
        ]
        and condition.model_endpoint_identity_digest
        == plan_by_member[condition.cohort_member_id][
            "model_endpoint_identity_digest"
        ]
        for condition in exp5_conditions
    )

    core_conditions = expand_plan_conditions(
        catalog_manifest=catalog,
        experiment_ids=(
            "exp1_real_ai_feasibility",
            "exp4_real_ai_protocol_ablation",
        ),
        worker_levels=(10,),
        repeats=1,
        seed_family=(1,),
        model_endpoint_cohort_preflight=preflight,
    )

    assert core_conditions
    assert {condition.model_policy for condition in core_conditions} == {"fixed_entry"}
    assert all(
        condition.provider_config_id is None
        and condition.source_provider_config_digest is None
        and condition.model_endpoint_identity_digest is None
        for condition in core_conditions
    )
    assert "exp5_real_ai_model_endpoint_comparison" not in {
        condition.experiment_id for condition in core_conditions
    }


def test_source_config_drift_changes_condition_identity_without_changing_endpoint_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _policy_module()
    catalog = _catalog()
    for env_name in ("TOKENSHARE_GLM_KEY", "TOKENSHARE_QWEN_KEY", "TOKENSHARE_GPT_KEY"):
        monkeypatch.setenv(env_name, "test-key")
    cohort = policy.load_model_endpoint_cohort(_write_cohort(tmp_path / "cohort.json"))
    entry_map = policy.load_model_entry_map(
        _write_entry_map(tmp_path / "entry_map.json", include_gpt=True)
    )
    siliconflow_path = _write_provider_config(
        tmp_path / "siliconflow.json",
        provider_family="siliconflow",
        entries={
            "glm-entry": ("zai-org/GLM-5.2", "TOKENSHARE_GLM_KEY"),
            "qwen-entry": ("Qwen/Qwen3.6-27B", "TOKENSHARE_QWEN_KEY"),
        },
    )
    original_openai_path = _write_provider_config(
        tmp_path / "openai_original.json",
        provider_family="openai",
        entries={"gpt-entry": ("gpt-5.6-sol", "TOKENSHARE_GPT_KEY")},
        metadata={"approval": "original"},
    )
    changed_openai_path = _write_provider_config(
        tmp_path / "openai_changed.json",
        provider_family="openai",
        entries={"gpt-entry": ("gpt-5.6-sol", "TOKENSHARE_GPT_KEY")},
        metadata={"approval": "changed-after-planning"},
    )

    def build_preflight(openai_path: Path) -> dict:
        return policy.build_model_endpoint_cohort_preflight(
            cohort=cohort,
            entry_map=entry_map,
            provider_configs=policy.load_provider_config_map(
                {"siliconflow": siliconflow_path, "openai": openai_path}
            ),
        )

    original = build_preflight(original_openai_path)
    changed = build_preflight(changed_openai_path)
    original_gpt = original["member_plans"]["gpt_5_6_sol_high_openai"]
    changed_gpt = changed["member_plans"]["gpt_5_6_sol_high_openai"]

    assert original_gpt["model_endpoint_identity_digest"] == changed_gpt[
        "model_endpoint_identity_digest"
    ]
    assert original_gpt["source_provider_config_digest"] != changed_gpt[
        "source_provider_config_digest"
    ]

    original_conditions = expand_plan_conditions(
        catalog_manifest=catalog,
        experiment_ids=("exp5_real_ai_model_endpoint_comparison",),
        worker_levels=(10,),
        repeats=1,
        seed_family=(1,),
        model_endpoint_cohort_preflight=original,
    )
    changed_conditions = expand_plan_conditions(
        catalog_manifest=catalog,
        experiment_ids=("exp5_real_ai_model_endpoint_comparison",),
        worker_levels=(10,),
        repeats=1,
        seed_family=(1,),
        model_endpoint_cohort_preflight=changed,
    )
    original_gpt_condition = next(
        condition
        for condition in original_conditions
        if condition.cohort_member_id == "gpt_5_6_sol_high_openai"
    )
    changed_gpt_condition = next(
        condition
        for condition in changed_conditions
        if condition.cohort_member_id == "gpt_5_6_sol_high_openai"
    )

    assert original_gpt_condition.condition_digest != changed_gpt_condition.condition_digest


def test_exp5_blocked_cohort_does_not_expand_extra_conditions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _policy_module()
    catalog = _catalog()
    for env_name in ("TOKENSHARE_GLM_KEY", "TOKENSHARE_QWEN_KEY", "TOKENSHARE_GPT_KEY"):
        monkeypatch.setenv(env_name, "test-key")
    cohort = policy.load_model_endpoint_cohort(
        _write_cohort(tmp_path / "cohort_extra.json", extra_member=True)
    )
    preflight = policy.build_model_endpoint_cohort_preflight(
        cohort=cohort,
        entry_map=policy.load_model_entry_map(
            _write_entry_map(tmp_path / "entry_map.json", include_gpt=True)
        ),
        provider_configs=policy.load_provider_config_map(
            _valid_provider_config_paths(tmp_path)
        ),
    )

    conditions = expand_plan_conditions(
        catalog_manifest=catalog,
        experiment_ids=("exp5_real_ai_model_endpoint_comparison",),
        worker_levels=(10,),
        repeats=1,
        seed_family=(1,),
        model_endpoint_cohort_preflight=preflight,
    )

    assert preflight["status"] == "blocked"
    assert preflight["blocked_reason"] == "incomplete_model_cohort"
    assert preflight["provider_calls_made"] == 0
    assert len(conditions) == 0


def test_cli_plan_only_exp5_writes_fixed_endpoint_cohort_evidence_without_provider_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cohort_path = _write_cohort(tmp_path / "cohort.json")
    entry_map_path = _write_entry_map(tmp_path / "entry_map.json", include_gpt=True)
    sf_config_path = _write_provider_config(
        tmp_path / "siliconflow.json",
        provider_family="siliconflow",
        entries={
            "glm-entry": ("zai-org/GLM-5.2", "TOKENSHARE_GLM_KEY"),
            "qwen-entry": ("Qwen/Qwen3.6-27B", "TOKENSHARE_QWEN_KEY"),
        },
    )
    openai_config_path = _write_provider_config(
        tmp_path / "openai.json",
        provider_family="openai",
        entries={"gpt-entry": ("gpt-5.6-sol", "TOKENSHARE_GPT_KEY")},
    )
    for env_name in ("TOKENSHARE_GLM_KEY", "TOKENSHARE_QWEN_KEY", "TOKENSHARE_GPT_KEY"):
        monkeypatch.setenv(env_name, "test-key")

    exit_code = main(
        [
            "--output-root",
            str(tmp_path / "out"),
            "--experiments",
            "exp5",
            "--plan-only",
            "--model-cohort-file",
            str(cohort_path),
            "--model-entry-map",
            str(entry_map_path),
            "--provider-config",
            f"siliconflow={sf_config_path}",
            "--provider-config",
            f"openai={openai_config_path}",
        ]
    )

    output_root = tmp_path / "out"
    suite = json.loads((output_root / "suite_manifest.json").read_text(encoding="utf-8"))
    budget = json.loads((output_root / "run_budget.json").read_text(encoding="utf-8"))
    cohort_plan = json.loads(
        (output_root / "model_endpoint_cohort_plan.json").read_text(encoding="utf-8")
    )

    assert exit_code == 0
    assert suite["status"] == "planned"
    assert suite["model_endpoint_cohort_preflight"]["status"] == "planned"
    assert suite["model_endpoint_cohort_preflight"]["model_policy"] == "fixed_entry"
    assert suite["model_policy_preflight"] is None
    assert budget["quota_preflight"]["provider_calls_made"] == 0
    assert budget["quota_preflight"]["model_endpoint_cohort_preflight"]["provider_calls_made"] == 0
    assert cohort_plan["status"] == "planned"
    assert set(cohort_plan["member_plans"]) == {
        "glm_5_2_siliconflow",
        "qwen3_6_27b_siliconflow",
        "gpt_5_6_sol_high_openai",
    }
    assert suite["condition_count"] == 54
    assert suite["run_count"] == 4_635
    assert suite["task_count"] == 4_635
    assert budget["planned_conditions"] == 54
    assert budget["planned_root_runs"] == 4_635


def test_cli_exp5_missing_member_is_structured_blocked_but_exp1_plan_still_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cohort_path = _write_cohort(tmp_path / "cohort.json")
    entry_map_path = _write_entry_map(tmp_path / "entry_map_missing_gpt.json", include_gpt=False)
    sf_config_path = _write_provider_config(
        tmp_path / "siliconflow.json",
        provider_family="siliconflow",
        entries={
            "glm-entry": ("zai-org/GLM-5.2", "TOKENSHARE_GLM_KEY"),
            "qwen-entry": ("Qwen/Qwen3.6-27B", "TOKENSHARE_QWEN_KEY"),
        },
    )
    openai_config_path = _write_provider_config(
        tmp_path / "openai.json",
        provider_family="openai",
        entries={"gpt-entry": ("gpt-5.6-sol", "TOKENSHARE_GPT_KEY")},
    )
    for env_name in ("TOKENSHARE_GLM_KEY", "TOKENSHARE_QWEN_KEY", "TOKENSHARE_GPT_KEY"):
        monkeypatch.setenv(env_name, "test-key")

    exp5_exit = main(
        [
            "--output-root",
            str(tmp_path / "exp5"),
            "--experiments",
            "exp5",
            "--plan-only",
            "--model-cohort-file",
            str(cohort_path),
            "--model-entry-map",
            str(entry_map_path),
            "--provider-config",
            f"siliconflow={sf_config_path}",
            "--provider-config",
            f"openai={openai_config_path}",
        ]
    )
    exp5_plan = json.loads(
        (tmp_path / "exp5" / "model_endpoint_cohort_plan.json").read_text(encoding="utf-8")
    )
    exp5_suite = json.loads(
        (tmp_path / "exp5" / "suite_manifest.json").read_text(encoding="utf-8")
    )
    exp5_dispatch = json.loads(
        (tmp_path / "exp5" / "paper_dispatch_plans.json").read_text(encoding="utf-8")
    )

    assert exp5_exit == 0
    assert exp5_suite["status"] == "blocked"
    assert exp5_plan["status"] == "blocked"
    assert exp5_plan["blocked_reason"] == "incomplete_model_cohort"
    assert exp5_plan["paper_eligible_possible"] is False
    assert exp5_plan["provider_calls_made"] == 0
    assert exp5_dispatch["plans"][0]["status"] == "blocked"
    assert exp5_dispatch["plans"][0]["blocked_reason"] == "incomplete_model_cohort"
    assert exp5_dispatch["plans"][0]["paper_eligible_possible"] is False

    exp1_exit = main(
        [
            "--output-root",
            str(tmp_path / "exp1"),
            "--experiments",
            "exp1",
            "--plan-only",
            "--model-cohort-file",
            str(cohort_path),
            "--model-entry-map",
            str(entry_map_path),
            "--provider-config",
            f"siliconflow={sf_config_path}",
            "--provider-config",
            f"openai={openai_config_path}",
        ]
    )
    exp1_suite = json.loads(
        (tmp_path / "exp1" / "suite_manifest.json").read_text(encoding="utf-8")
    )

    assert exp1_exit == 0
    assert exp1_suite["status"] == "planned"
    assert exp1_suite["condition_count"] > 0
    assert exp1_suite["model_endpoint_cohort_preflight"] is None

    combined_exit = main(
        [
            "--output-root",
            str(tmp_path / "combined"),
            "--experiments",
            "exp1,exp5",
            "--plan-only",
            "--model-cohort-file",
            str(cohort_path),
            "--model-entry-map",
            str(entry_map_path),
            "--provider-config",
            f"siliconflow={sf_config_path}",
            "--provider-config",
            f"openai={openai_config_path}",
        ]
    )
    combined_suite = json.loads(
        (tmp_path / "combined" / "suite_manifest.json").read_text(encoding="utf-8")
    )
    combined_dispatch = json.loads(
        (tmp_path / "combined" / "paper_dispatch_plans.json").read_text(
            encoding="utf-8"
        )
    )

    assert combined_exit == 0
    assert combined_suite["status"] == "planned"
    assert combined_suite["condition_count"] == 36
    assert [plan["status"] for plan in combined_dispatch["plans"]] == [
        "planned",
        "blocked",
    ]
    assert combined_dispatch["plans"][1]["blocked_reason"] == "incomplete_model_cohort"
    assert combined_dispatch["provider_calls_made"] == 0


def test_formal_exp5_schema_and_cli_reject_legacy_strong_weak_mixed(
    tmp_path: Path,
) -> None:
    for legacy_policy in ("strong_only", "weak_only", "mixed"):
        with pytest.raises(ValueError, match="model_policy"):
            _condition(
                domain="factorization",
                difficulty="easy",
                model_policy=legacy_policy,
            )

    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--output-root",
                str(tmp_path / "out"),
                "--experiments",
                "exp5",
                "--plan-only",
                "--model-policies",
                "mixed",
            ]
        )
    assert exc.value.code == 2


def test_factorization_and_lean_adapters_keep_attempt_model_identity_records(
    tmp_path: Path,
) -> None:
    catalog = _catalog()
    factor_case = catalog.cases_for(domain="factorization", difficulty="easy")[0]
    lean_case = catalog.cases_for(domain="lean_proof", difficulty="easy")[0]

    factor_result = run_factorization_paper_case(
        case=factor_case,
        condition=_condition(
            domain="factorization",
            difficulty="easy",
            model_policy="fixed_entry",
            catalog_digest=catalog.catalog_digest,
            model_entry_id="glm-entry",
        ),
        output_root=tmp_path / "factorization",
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
    )
    lean_result = run_lean_paper_case(
        case=lean_case,
        condition=_condition(
            domain="lean_proof",
            difficulty="easy",
            model_policy="fixed_entry",
            catalog_digest=catalog.catalog_digest,
            model_entry_id="gpt-entry",
            paper_difficulty=lean_case["paper_difficulty"],
            topic_family=lean_case["topic_family"],
            topic_family_version=lean_case["topic_family_version"],
            construction_rule_id=lean_case.get("construction_rule_id"),
            oracle_package_group=lean_case.get("oracle_package_group"),
            proof_assembly_shape=lean_case.get("proof_assembly_shape"),
        ),
        output_root=tmp_path / "lean",
        transport=ScriptedLeanPaperProofTransport(),
        real_transport=False,
    )

    factor_attempt = factor_result.attempt_results[0]
    lean_attempt = lean_result.attempt_results[0]
    assert factor_attempt.entry_id == "glm-entry"
    assert factor_attempt.provider == "siliconflow"
    assert factor_attempt.model == "TokenShare/Scripted-Factorization-Range"
    assert lean_attempt.entry_id == "gpt-entry"
    assert lean_attempt.provider == "siliconflow"
    assert lean_attempt.model == "TokenShare/Scripted-Lean-Paper-Prover"

    with pytest.raises(ValueError, match="model_entry_id"):
        run_factorization_paper_case(
            case=factor_case,
            condition=_condition(
                domain="factorization",
                difficulty="easy",
                model_policy="fixed_entry",
                catalog_digest=catalog.catalog_digest,
                model_entry_id="glm-entry",
            ),
            output_root=tmp_path / "factorization_mismatch",
            transport=ScriptedFactorizationRangeTransport(),
            real_transport=False,
            entry_id="other-entry",
        )


def _policy_module():
    module_name = "tokenshare.experiments.paper_model_policy"
    assert importlib.util.find_spec(module_name) is not None
    return importlib.import_module(module_name)


def _condition(
    *,
    domain: str,
    difficulty: str,
    model_policy: str,
    catalog_digest: str = "sha256:" + "1" * 64,
    model_cohort_id: str | None = None,
    cohort_member_id: str | None = None,
    model_entry_id: str | None = None,
    provider_family: str | None = None,
    provider_model_id: str | None = None,
    reasoning_profile_id: str | None = None,
    model_cohort_digest: str | None = None,
    paper_difficulty: str | None = None,
    topic_family: str | None = None,
    topic_family_version: str | None = None,
    construction_rule_id: str | None = None,
    oracle_package_group: str | None = None,
    proof_assembly_shape: str | None = None,
) -> PaperExperimentCondition:
    return PaperExperimentCondition(
        experiment_id="exp1_real_ai_feasibility",
        condition_id=f"cond_{domain}_{difficulty}_{model_policy}",
        domain=domain,
        difficulty=difficulty,
        worker_count=10,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy=model_policy,
        model_cohort_id=model_cohort_id,
        cohort_member_id=cohort_member_id,
        model_entry_id=model_entry_id,
        provider_family=provider_family,
        provider_model_id=provider_model_id,
        reasoning_profile_id=reasoning_profile_id,
        model_cohort_digest=model_cohort_digest,
        repeat_id=0,
        seed=1,
        catalog_digest=catalog_digest,
        paper_difficulty=paper_difficulty,
        topic_family=topic_family,
        topic_family_version=topic_family_version,
        construction_rule_id=construction_rule_id,
        oracle_package_group=oracle_package_group,
        proof_assembly_shape=proof_assembly_shape,
    )


def _catalog():
    return load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )


def _write_cohort(
    path: Path,
    *,
    member_overrides: dict[str, dict] | None = None,
    extra_member: bool = False,
    omit_member_id: str | None = None,
) -> Path:
    members = [
        member
        for member in _cohort_members(member_overrides=member_overrides)
        if member["cohort_member_id"] != omit_member_id
    ]
    if extra_member:
        members.append(
            {
                "cohort_member_id": "extra_endpoint_not_in_design",
                "provider_family": "openai",
                "provider_model_id": "not-authorized",
                "reasoning_profile_id": "default",
            }
        )
    body = {
        "schema_version": "tokenshare.paper_model_endpoint_cohort.v1",
        "cohort_id": "tokenshare.paper.model_endpoint_cohort.v1",
        "members": members,
    }
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _cohort_members(*, member_overrides: dict[str, dict] | None = None) -> list[dict]:
    overrides = member_overrides or {}
    members = [
        {
            "cohort_member_id": "glm_5_2_siliconflow",
            "provider_family": "siliconflow",
            "provider_model_id": "zai-org/GLM-5.2",
            "reasoning_profile_id": "default",
            "external_benchmark_source": "Artificial Analysis Intelligence Index",
            "index_version": "v4.1",
            "index_score": 51,
            "benchmark_variant": "max",
            "benchmark_match_status": "exact_or_provider_listing",
            "source_url": "https://artificialanalysis.ai/",
            "observed_at": "2026-07-16",
        },
        {
            "cohort_member_id": "qwen3_6_27b_siliconflow",
            "provider_family": "siliconflow",
            "provider_model_id": "Qwen/Qwen3.6-27B",
            "reasoning_profile_id": "default",
            "external_benchmark_source": "Artificial Analysis Intelligence Index",
            "index_version": "v4.1",
            "index_score": 37,
            "benchmark_variant": "reasoning",
            "benchmark_match_status": "exact_or_provider_listing",
            "source_url": "https://artificialanalysis.ai/",
            "observed_at": "2026-07-16",
        },
        {
            "cohort_member_id": "gpt_5_6_sol_high_openai",
            "provider_family": "openai",
            "provider_model_id": "gpt-5.6-sol",
            "reasoning_profile_id": "high",
            "external_benchmark_source": "Artificial Analysis Intelligence Index",
            "index_version": "v4.1",
            "index_score": 56,
            "benchmark_variant": "high",
            "benchmark_match_status": "exact_or_provider_listing",
            "source_url": "https://artificialanalysis.ai/",
            "observed_at": "2026-07-16",
        },
    ]
    for member in members:
        member.update(overrides.get(member["cohort_member_id"], {}))
    return members


def _write_entry_map(
    path: Path,
    *,
    include_gpt: bool,
    cohort_id: str = "tokenshare.paper.model_endpoint_cohort.v1",
    member_overrides: dict[str, dict] | None = None,
) -> Path:
    members = {
        "glm_5_2_siliconflow": {
            "provider_config_id": "siliconflow",
            "entry_id": "glm-entry",
            "smoke_evidence_ref": _smoke_evidence(
                member_id="glm_5_2_siliconflow",
                entry_id="glm-entry",
                provider_family="siliconflow",
                provider_model_id="zai-org/GLM-5.2",
                reasoning_profile_id="default",
            ),
        },
        "qwen3_6_27b_siliconflow": {
            "provider_config_id": "siliconflow",
            "entry_id": "qwen-entry",
            "smoke_evidence_ref": _smoke_evidence(
                member_id="qwen3_6_27b_siliconflow",
                entry_id="qwen-entry",
                provider_family="siliconflow",
                provider_model_id="Qwen/Qwen3.6-27B",
                reasoning_profile_id="default",
            ),
        },
    }
    if include_gpt:
        members["gpt_5_6_sol_high_openai"] = {
            "provider_config_id": "openai",
            "entry_id": "gpt-entry",
            "smoke_evidence_ref": _smoke_evidence(
                member_id="gpt_5_6_sol_high_openai",
                entry_id="gpt-entry",
                provider_family="openai",
                provider_model_id="gpt-5.6-sol",
                reasoning_profile_id="high",
            ),
        }
    for member_id, override in (member_overrides or {}).items():
        if member_id in members:
            members[member_id].update(override)
    body = {
        "schema_version": "tokenshare.paper_model_entry_map.v1",
        "cohort_id": cohort_id,
        "members": members,
    }
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _smoke_evidence(
    *,
    member_id: str,
    entry_id: str,
    provider_family: str,
    provider_model_id: str,
    reasoning_profile_id: str,
) -> dict:
    return {
        "schema_version": "tokenshare.paper_model_endpoint_smoke_evidence.v1",
        "status": "passed",
        "cohort_member_id": member_id,
        "entry_id": entry_id,
        "provider_family": provider_family,
        "provider_model_id": provider_model_id,
        "reasoning_profile_id": reasoning_profile_id,
        "provider_attempt_count": 1,
        "raw_output_ref": {"artifact_id": f"raw_{entry_id}", "content_hash": "sha256:" + "3" * 64},
        "provenance_ref": {
            "artifact_id": f"provenance_{entry_id}",
            "content_hash": "sha256:" + "4" * 64,
        },
        "usage_ref": {"artifact_id": f"usage_{entry_id}", "content_hash": "sha256:" + "5" * 64},
    }


def _blocked(preflight: dict) -> bool:
    assert preflight["provider_calls_made"] == 0
    assert preflight["paper_eligible_possible"] is False
    assert preflight["blocked_reason"] == "incomplete_model_cohort"
    return preflight["status"] == "blocked"


def _valid_provider_config_paths(
    tmp_path: Path,
    *,
    glm_model: str = "zai-org/GLM-5.2",
    openai_entries: dict[str, tuple[str, str]] | None = None,
) -> dict[str, Path]:
    return {
        "siliconflow": _write_provider_config(
            tmp_path / f"siliconflow_{len(list(tmp_path.glob('siliconflow_*.json')))}.json",
            provider_family="siliconflow",
            entries={
                "glm-entry": (glm_model, "TOKENSHARE_GLM_KEY"),
                "qwen-entry": ("Qwen/Qwen3.6-27B", "TOKENSHARE_QWEN_KEY"),
            },
        ),
        "openai": _write_provider_config(
            tmp_path / f"openai_{len(list(tmp_path.glob('openai_*.json')))}.json",
            provider_family="openai",
            entries=openai_entries
            or {"gpt-entry": ("gpt-5.6-sol", "TOKENSHARE_GPT_KEY")},
        ),
    }


def _write_provider_config(
    path: Path,
    *,
    provider_family: str,
    entries: dict[str, tuple[str, str]],
    metadata: dict | None = None,
) -> Path:
    body = {
        "schema_version": "phase7.ai_api_executor_config.v1",
        "executor_id": "executor_ai_api",
        "provider_family": provider_family,
        "selection_policy": {
            "kind": "uniform_random_without_weights",
            "seed_source": "request_or_environment_seed",
        },
        "defaults": {
            "timeout_seconds": 30,
            "max_tokens": 512,
            "temperature": 0.0,
            "top_p": 0.9,
            "stream": False,
            "max_provider_attempts": 1,
        },
        "entries": [
            {
                "entry_id": entry_id,
                "enabled": True,
                "base_url": (
                    "https://api.openai.com/v1"
                    if provider_family == "openai"
                    else "https://api.siliconflow.cn/v1"
                ),
                "api_key_env": api_key_env,
                "model": model,
                "endpoint": "/chat/completions",
                "supports_json_mode": True,
                "supports_streaming": False,
                "request_overrides": (
                    {"temperature": 0.0, "reasoning_effort": "high"}
                    if provider_family == "openai"
                    else {"temperature": 0.0}
                ),
                "pricing": {
                    "currency": "USD",
                    "input_per_million_tokens": 1.0,
                    "output_per_million_tokens": 2.0,
                },
                "tags": ["paper", provider_family],
            }
            for entry_id, (model, api_key_env) in entries.items()
        ],
        "local_concurrency": {"max_in_flight_global": 1},
        "metadata": metadata or {"purpose": "paper-model-endpoint-test"},
    }
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path
