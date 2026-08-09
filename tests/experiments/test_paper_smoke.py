import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

import tokenshare.experiments.paper_smoke_report as smoke_report_module
from tokenshare.experiments.paper_dispatcher import PaperExperimentDispatchPlan
from tokenshare.experiments.paper_exp2_scalability import (
    Exp2FactorizationCaseSelection,
)
from tokenshare.experiments.paper_experiment_contracts import (
    FrozenCaseSelection,
    FrozenConditionSelectionBinding,
)
from tokenshare.experiments.paper_models import (
    PaperExperimentCondition,
    digest_json,
)
from tokenshare.experiments.paper_formal_evidence import FormalEvidenceStore
from tokenshare.experiments.paper_smoke import (
    PAPER_SMOKE_PROFILE_SCHEMA_VERSION,
    load_paper_smoke_profile,
    resolve_paper_smoke_execution_plan,
)
from tokenshare.experiments.paper_smoke_report import (
    _actual_usage,
    generate_paper_smoke_report,
)


CATALOG_DIGEST = "sha256:" + "1" * 64
REPO_ROOT = Path(__file__).resolve().parents[2]
DUAL_DOMAIN_SMOKE_PROFILE = (
    REPO_ROOT / "benchmarks/paper/paper_smoke_dual_domain_profile.v1.json"
)


def test_smoke_suite_forwards_results_first_trace_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments import paper_formal_evidence, paper_formal_runner
    from tokenshare.experiments import paper_smoke as smoke
    from tokenshare.experiments import paper_smoke_report

    trace_context = object()
    captured: list[dict[str, object]] = []
    terminal = SimpleNamespace(status="completed", provider_attempt_count=0)
    monkeypatch.setattr(
        paper_formal_runner,
        "execute_paper_formal_suite",
        lambda **kwargs: captured.append(kwargs) or terminal,
    )
    monkeypatch.setattr(
        paper_smoke_report,
        "generate_paper_smoke_report",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        paper_formal_evidence.FormalEvidenceStore,
        "_refresh_evidence_manifest",
        lambda _self: None,
    )
    profile = SimpleNamespace(
        profile_digest="sha256:" + "a" * 64,
        baseline_policy="omitted_for_smoke_regression",
        suite_id="matrix8-trace",
        to_dict=lambda: {},
    )
    execution_plan = SimpleNamespace(
        profile_digest=profile.profile_digest,
        baseline_policy=profile.baseline_policy,
        dispatch_plans=(),
        output_root=tmp_path,
        root_case_filter={},
        to_dict=lambda: {},
    )

    assert smoke.execute_paper_smoke_suite(
        profile=profile,
        execution_plan=execution_plan,
        catalog_manifest=object(),
        budget=SimpleNamespace(
            quota_preflight={"budget_approval": {"approval_mode": "plan_only"}}
        ),
        ai_api_configs={},
        transport=object(),
        real_transport=False,
        hard_limits={},
        launch_manifest={},
        trace_context=trace_context,
    ) is terminal
    assert captured[0]["trace_context"] is trace_context
    assert captured[0]["real_transport"] is False
MATRIX8_SMOKE_PROFILES = tuple(
    REPO_ROOT / f"benchmarks/paper/paper_smoke_exp{experiment}_matrix8_profile.v1.json"
    for experiment in range(1, 5)
)
EXP5_V4_SMOKE_PROFILE = (
    REPO_ROOT / "benchmarks/paper/paper_smoke_exp5_profile.v4.json"
)


def _epd027_launcher_source(name: str) -> str:
    return (REPO_ROOT / "local" / name).read_text(encoding="utf-8-sig")


def _smoke_suite_identity(
    experiment_id: str,
    condition_id: str,
    case_id: str,
) -> dict[str, object]:
    return {
        "dispatch": {
            "body": {
                "plans": [
                    {
                        "experiment_id": experiment_id,
                        "conditions": [{"condition_id": condition_id}],
                        "selections": [{"ordered_case_ids": [case_id]}],
                    }
                ]
            }
        },
        "suite": {
            "body": {"root_case_filter": {condition_id: [case_id]}}
        },
    }


def _commit_smoke_generation(generation: Path) -> None:
    tasks = [
        json.loads(line)
        for line in (generation / "per_task_results.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    first_task = tasks[0]
    run_manifest = {
        "schema_version": "tokenshare.paper_run_evidence.v1",
        "generation_id": generation.name,
        "experiment_id": first_task["experiment_id"],
        "condition_id": first_task["condition_id"],
        "repeat_id": first_task["repeat_id"],
        "task_ids": [str(task["task_id"]) for task in tasks],
        "completed_task_ids": [
            str(task["task_id"])
            for task in tasks
            if task.get("root_status") == "completed"
        ],
        "status": "completed",
    }
    (generation / "run_manifest.json").write_text(
        json.dumps(run_manifest), encoding="utf-8"
    )
    files = []
    for relative_path in (
        "run_manifest.json",
        "per_task_results.jsonl",
        "per_attempt_results.jsonl",
        "fault_injections.jsonl",
        "events/event_log.jsonl",
        "artifacts/artifact_index.jsonl",
    ):
        path = generation / relative_path
        content = path.read_bytes()
        records = [
            json.loads(line)
            for line in content.decode("utf-8").splitlines()
            if line.strip()
        ]
        files.append(
            {
                "path": relative_path,
                "size": len(content),
                "content_sha256": "sha256:" + sha256(content).hexdigest(),
                "record_count": len(records),
                "records_digest": digest_json(records),
            }
        )
    manifest = {
        "schema_version": "tokenshare.paper_checkpoint_generation.v1",
        "generation_id": generation.name,
        "files": files,
    }
    (generation / "generation_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    (generation.parents[1] / "CURRENT.json").write_text(
        json.dumps(
            {
                "schema_version": "tokenshare.paper_checkpoint_current.v1",
                "generation_id": generation.name,
                "generation_manifest_digest": digest_json(manifest),
            }
        ),
        encoding="utf-8",
    )


def test_bank_exp1_exp5_scopes_are_not_interchangeable() -> None:
    expected = {
        "run_epd027_bank_acquisition.ps1": (
            "acquire-bank",
            "epd027_full_bank_acquisition",
        ),
        "run_epd027_exp1_online.ps1": ("run-exp1-online", "exp1_full_online"),
        "run_epd027_exp5_capability.ps1": (
            "run-exp5-capability-smoke",
            "exp5_capability_smoke",
        ),
        "run_epd027_exp5_online.ps1": ("run-exp5-online", "exp5_full_online"),
    }
    all_scopes = {scope for _, scope in expected.values()}
    for name, (command, scope) in expected.items():
        source = _epd027_launcher_source(name)
        assert f'"{command}"' in source
        assert f'$ExpectedProviderScope = "{scope}"' in source
        assert "$receipt.scope -ne $ExpectedProviderScope" in source
        for other_scope in all_scopes - {scope}:
            assert f'$ExpectedProviderScope = "{other_scope}"' not in source


def test_exp1_launcher_routes_run_exp1_online() -> None:
    source = _epd027_launcher_source("run_epd027_exp1_online.ps1")
    assert '"run-exp1-online"' in source
    assert '"run-online-checks"' not in source
    assert '"run-exp5-online"' not in source


def test_exp5_capability_and_online_launchers_require_distinct_scopes() -> None:
    capability = _epd027_launcher_source("run_epd027_exp5_capability.ps1")
    online = _epd027_launcher_source("run_epd027_exp5_online.ps1")
    assert '"run-exp5-capability-smoke"' in capability
    assert '$ExpectedProviderScope = "exp5_capability_smoke"' in capability
    assert '"run-exp5-online"' in online
    assert '$ExpectedProviderScope = "exp5_full_online"' in online
    assert "exp5_full_online" not in capability
    assert "exp5_capability_smoke" not in online


def test_new_launchers_never_pass_unlimited_budget() -> None:
    for name in (
        "run_epd027_l3_checks.ps1",
        "run_epd027_bank_acquisition.ps1",
        "run_epd027_trace_matrix.ps1",
        "run_epd027_exp1_online.ps1",
        "run_epd027_exp5_capability.ps1",
        "run_epd027_exp5_online.ps1",
    ):
        source = _epd027_launcher_source(name)
        assert "--unlimited-budget" not in source
        assert '"unlimited"' not in source
        if name != "run_epd027_trace_matrix.ps1":
            assert '"--budget-mode", "bounded"' in source


def test_formal_launchers_delegate_gates_to_integrated_pipeline_run() -> None:
    expected_dispatch = {
        "run_epd027_trace_matrix.ps1": "run-trace",
        "run_epd027_exp1_online.ps1": "run-exp1-online",
        "run_epd027_exp5_online.ps1": "run-exp5-online",
    }
    for name, dispatch in expected_dispatch.items():
        source = _epd027_launcher_source(name)
        assert source.count(f'"{dispatch}"') == 1
        assert '"validate-formal-execution-gate"' not in source
        assert '"validate-paper-publication-gate"' not in source
        assert source.rstrip().endswith("exit $runnerExitCode")


def test_default_smoke_profile_freezes_27_non_paper_root_runs() -> None:
    profile = load_paper_smoke_profile(
        Path("benchmarks/paper/paper_smoke_profile.v1.json")
    )

    assert profile.schema_version == PAPER_SMOKE_PROFILE_SCHEMA_VERSION
    assert profile.expected_root_runs == 27
    assert len(profile.items) == 27
    assert profile.experiment_ids == (
        "exp1_real_ai_feasibility",
        "exp2_real_ai_scalability",
        "exp3_real_ai_fault_recovery",
        "exp4_real_ai_protocol_ablation",
        "exp5_real_ai_model_endpoint_comparison",
    )
    assert profile.pilot_only is True
    assert profile.regression_only is True
    assert profile.paper_eligible is False


def test_dual_domain_smoke_profile_freezes_only_factorization_and_lean_roots() -> None:
    profile = load_paper_smoke_profile(DUAL_DOMAIN_SMOKE_PROFILE)

    assert profile.schema_version == PAPER_SMOKE_PROFILE_SCHEMA_VERSION
    assert profile.suite_id == "paper_smoke_dual_domain_v1"
    assert profile.profile_version == "v1"
    assert profile.experiment_ids == ("exp1_real_ai_feasibility",)
    assert profile.expected_root_runs == 2
    assert tuple(item.case_id for item in profile.items) == (
        "factor_v2_easy_109",
        "lean_easy_01",
    )
    assert {
        item.condition_selector["domain"] for item in profile.items
    } == {"factorization", "lean_proof"}
    assert profile.formal is False
    assert profile.pilot_only is True
    assert profile.regression_only is True
    assert profile.paper_eligible is False
    assert {"smoke_suite", "pilot_only"}.issubset(
        profile.ineligibility_reasons
    )


@pytest.mark.parametrize("profile_path", MATRIX8_SMOKE_PROFILES)
def test_matrix8_profiles_are_rejected_as_legacy_authority(profile_path: Path) -> None:
    with pytest.raises(ValueError, match="legacy matrix8"):
        load_paper_smoke_profile(profile_path)


def test_smoke_profile_v2_switches_current_baseline_and_exp5_cohort() -> None:
    exp1_to_exp4 = load_paper_smoke_profile(
        Path("benchmarks/paper/paper_smoke_exp1_exp4_profile.v2.json")
    )
    full = load_paper_smoke_profile(
        Path("benchmarks/paper/paper_smoke_profile.v2.json")
    )

    assert exp1_to_exp4.suite_id == "paper_smoke_exp1_exp4_v2"
    assert exp1_to_exp4.profile_version == "v2"
    assert all(
        item.condition_selector.get("model_entry_id")
        == "deepseek_v4_pro_exp1_baseline"
        for item in exp1_to_exp4.items
    )
    assert full.suite_id == "paper_smoke_v2"
    assert full.profile_version == "v2"
    assert {
        item.condition_selector.get("cohort_member_id")
        for item in full.items
        if item.experiment_id == "exp5_real_ai_model_endpoint_comparison"
    } == {
        "glm_5_2_siliconflow",
        "deepseek_v4_pro_deepseek",
        "gpt_5_6_sol_high_openai",
    }
    assert {"smoke_suite", "pilot_only"}.issubset(
        full.ineligibility_reasons
    )
    assert sum(
        item.experiment_id == "exp3_real_ai_fault_recovery"
        for item in full.items
    ) == 7
    assert sum(
        item.experiment_id == "exp4_real_ai_protocol_ablation"
        for item in full.items
    ) == 5
    assert sum(
        item.experiment_id == "exp5_real_ai_model_endpoint_comparison"
        for item in full.items
    ) == 6


def test_smoke_profile_v3_freezes_four_model_half_hard_contract() -> None:
    v1_path = Path("benchmarks/paper/paper_smoke_profile.v1.json")
    v2_path = Path("benchmarks/paper/paper_smoke_profile.v2.json")
    assert sha256(v1_path.read_bytes()).hexdigest() == (
        "78804cb40290b0e86ac649db776ad27a677c051855e477fc19c008c4bf4eb760"
    )
    assert sha256(v2_path.read_bytes()).hexdigest() == (
        "622733fe2d4a9971c6118168a8ac92498f72e3562a654c869430c8cf2e35a1ca"
    )

    previous = load_paper_smoke_profile(v2_path)
    profile = load_paper_smoke_profile(
        Path("benchmarks/paper/paper_smoke_profile.v3.json")
    )

    assert profile.schema_version == "tokenshare.paper_smoke_profile.v2"
    assert profile.suite_id == "paper_smoke_v3"
    assert profile.profile_version == "v3"
    assert profile.expected_root_runs == 29
    assert len(profile.items) == 29
    assert profile.formal is False
    assert profile.pilot_only is True
    assert profile.regression_only is True
    assert profile.paper_eligible is False
    assert [item.to_dict() for item in profile.items[:21]] == [
        item.to_dict() for item in previous.items[:21]
    ]

    exp5 = profile.items[21:]
    member_order = (
        "glm_5_2_siliconflow",
        "qwen3_14b_siliconflow",
        "minimax_m2_5_siliconflow",
        "deepseek_v3_pro_siliconflow",
    )
    assert tuple(
        dict.fromkeys(item.condition_selector["cohort_member_id"] for item in exp5)
    ) == member_order
    assert all(item.repeat_id == 0 for item in exp5)
    assert all(item.condition_selector["worker_count"] == 3 for item in exp5)
    for member_id in member_order:
        member_items = [
            item
            for item in exp5
            if item.condition_selector["cohort_member_id"] == member_id
        ]
        assert [item.case_id for item in member_items] == [
            "factor_v2_hard_001",
            "lean_v2_hard_frontier_pure_logic_checker_06",
        ]
        assert [item.condition_selector["domain"] for item in member_items] == [
            "factorization",
            "lean_proof",
        ]
        assert all(
            item.condition_selector["difficulty"] == "hard"
            for item in member_items
        )

    assert profile.exp5_v3_contract == {
        "schema_version": "tokenshare.paper_smoke_exp5_contract.v1",
        "cohort_id": "tokenshare.paper.model_endpoint_cohort.v3",
        "cohort_digest": (
            "sha256:1b317c9827d87c43b974b77c469b115906da463a79064d3ffb3b949dc5257942"
        ),
        "provider_config_id": "executor_ai_api_exp5_siliconflow_v3",
        "provider_config_digest": (
            "sha256:6b1d6ffe977340ebbf59d69368d8c977fef664007575de72593c08c038d5ffe1"
        ),
        "selection_id": "tokenshare.paper.exp5.hard_half.v3",
        "selection_digest": (
            "sha256:fbec153a02befa4071b4ad4d639e51e910a3bc4c433cc9eea4b19ac6489a3490"
        ),
        "sequence_plan_digest": (
            "sha256:de9271732513d7d2ab20624d2949af3c9230b9adc2cd8db4b1f1ddb0d57b58cb"
        ),
        "shared_provider_family": "siliconflow",
        "max_in_flight_global": 3,
        "model_arms_sequential": True,
        "repeat_member_order": {
            "0": list(member_order),
            "1": [
                "qwen3_14b_siliconflow",
                "deepseek_v3_pro_siliconflow",
                "glm_5_2_siliconflow",
                "minimax_m2_5_siliconflow",
            ],
            "2": [
                "minimax_m2_5_siliconflow",
                "glm_5_2_siliconflow",
                "deepseek_v3_pro_siliconflow",
                "qwen3_14b_siliconflow",
            ],
        },
    }
    assert profile.to_dict()["exp5_v3_contract"] == profile.exp5_v3_contract


def test_exp5_v3_standalone_smoke_profile_contains_only_eight_roots() -> None:
    profile = load_paper_smoke_profile(
        Path("benchmarks/paper/paper_smoke_exp5_profile.v3.json")
    )

    assert profile.suite_id == "paper_smoke_exp5_v3"
    assert profile.experiment_ids == (
        "exp5_real_ai_model_endpoint_comparison",
    )
    assert profile.expected_root_runs == 8
    assert len(profile.items) == 8
    assert all(
        item.experiment_id == "exp5_real_ai_model_endpoint_comparison"
        for item in profile.items
    )
    assert profile.exp5_v3_contract is not None
    assert profile.paper_eligible is False


def test_exp5_v4_standalone_smoke_profile_binds_active_selection() -> None:
    profile = load_paper_smoke_profile(
        Path("benchmarks/paper/paper_smoke_exp5_profile.v4.json")
    )

    assert profile.suite_id == "paper_smoke_exp5_v4"
    assert profile.profile_version == "v4"
    assert profile.expected_root_runs == 8
    assert profile.exp5_v3_contract is not None
    assert profile.exp5_v3_contract["selection_id"] == (
        "tokenshare.paper.exp5.parent_quarter.v4"
    )
    assert profile.exp5_v3_contract["selection_digest"] == (
        "sha256:452f25dcc53a1eb0387665c6f451f1320095efb0c4bf154af62bf5e388afb6b2"
    )


def test_smoke_profile_v3_contract_drift_fails_closed(tmp_path: Path) -> None:
    source = json.loads(
        Path("benchmarks/paper/paper_smoke_profile.v3.json").read_text(
            encoding="utf-8"
        )
    )

    invalid_concurrency = json.loads(json.dumps(source))
    invalid_concurrency["exp5_v3_contract"]["max_in_flight_global"] = 4
    path = tmp_path / "invalid-concurrency.json"
    path.write_text(json.dumps(invalid_concurrency), encoding="utf-8")
    with pytest.raises(ValueError, match="max_in_flight_global exact integer 3"):
        load_paper_smoke_profile(path)

    invalid_sequence = json.loads(json.dumps(source))
    invalid_sequence["exp5_v3_contract"]["repeat_member_order"]["1"].reverse()
    path = tmp_path / "invalid-sequence.json"
    path.write_text(json.dumps(invalid_sequence), encoding="utf-8")
    with pytest.raises(ValueError, match="repeat_member_order"):
        load_paper_smoke_profile(path)

    unknown_field = json.loads(json.dumps(source))
    unknown_field["exp5_v3_contract"]["unversioned_override"] = True
    path = tmp_path / "unknown-contract-field.json"
    path.write_text(json.dumps(unknown_field), encoding="utf-8")
    with pytest.raises(ValueError, match="contract fields"):
        load_paper_smoke_profile(path)

    invalid_binding = json.loads(json.dumps(source))
    invalid_binding["exp5_v3_contract"]["cohort_digest"] = "sha256:" + "0" * 64
    path = tmp_path / "invalid-binding.json"
    path.write_text(json.dumps(invalid_binding), encoding="utf-8")
    with pytest.raises(ValueError, match="canonical cohort_digest mismatch"):
        load_paper_smoke_profile(path)


@pytest.mark.parametrize(
    "field_name",
    (
        "cohort_digest",
        "provider_config_digest",
        "selection_digest",
        "sequence_plan_digest",
    ),
)
def test_smoke_profile_v3_rejects_rehashed_noncanonical_digest(
    tmp_path: Path,
    field_name: str,
) -> None:
    body = _v3_profile_body()
    body["exp5_v3_contract"][field_name] = "sha256:" + "0" * 64
    _refresh_profile_digest(body)
    path = tmp_path / f"noncanonical-{field_name}.json"
    path.write_text(json.dumps(body), encoding="utf-8")

    with pytest.raises(ValueError, match=f"canonical {field_name} mismatch"):
        load_paper_smoke_profile(path)


@pytest.mark.parametrize(
    "profile_digest",
    (
        None,
        True,
        "sha256:short",
        "sha256:" + "g" * 64,
    ),
)
def test_smoke_profile_v3_requires_complete_declared_profile_digest(
    tmp_path: Path,
    profile_digest: object,
) -> None:
    body = _v3_profile_body()
    if profile_digest is None:
        body.pop("profile_digest")
    else:
        body["profile_digest"] = profile_digest
    path = tmp_path / "invalid-profile-digest.json"
    path.write_text(json.dumps(body), encoding="utf-8")

    with pytest.raises(ValueError, match="profile_digest must be a sha256 digest"):
        load_paper_smoke_profile(path)


@pytest.mark.parametrize("target", ("catalog", "item"))
def test_smoke_profile_v3_rejects_unknown_nested_fields(
    tmp_path: Path,
    target: str,
) -> None:
    body = _v3_profile_body()
    if target == "catalog":
        body["catalog"]["unversioned_override"] = True
    else:
        body["items"][0]["unversioned_override"] = True
    path = tmp_path / f"unknown-{target}-field.json"
    path.write_text(json.dumps(body), encoding="utf-8")

    with pytest.raises(ValueError, match=f"v3 {target} fields"):
        load_paper_smoke_profile(path)


@pytest.mark.parametrize("invalid_value", (3.0, True))
def test_smoke_profile_v3_requires_exact_integer_global_concurrency(
    tmp_path: Path,
    invalid_value: object,
) -> None:
    body = _v3_profile_body()
    body["exp5_v3_contract"]["max_in_flight_global"] = invalid_value
    _refresh_profile_digest(body)
    path = tmp_path / "invalid-global-concurrency.json"
    path.write_text(json.dumps(body), encoding="utf-8")

    with pytest.raises(ValueError, match="max_in_flight_global exact integer 3"):
        load_paper_smoke_profile(path)


@pytest.mark.parametrize("invalid_value", (3.0, True))
def test_smoke_profile_v3_requires_exact_integer_selector_worker_count(
    tmp_path: Path,
    invalid_value: object,
) -> None:
    body = _v3_profile_body()
    body["items"][21]["condition_selector"]["worker_count"] = invalid_value
    _refresh_profile_digest(body)
    path = tmp_path / "invalid-selector-worker-count.json"
    path.write_text(json.dumps(body), encoding="utf-8")

    with pytest.raises(ValueError, match="worker_count exact integer 3"):
        load_paper_smoke_profile(path)


def test_exp1_exp4_smoke_profile_freezes_original_21_root_subset() -> None:
    full_profile = load_paper_smoke_profile(
        Path("benchmarks/paper/paper_smoke_profile.v1.json")
    )
    profile = load_paper_smoke_profile(
        Path("benchmarks/paper/paper_smoke_exp1_exp4_profile.v1.json")
    )

    assert profile.suite_id == "paper_smoke_exp1_exp4_v1"
    assert profile.expected_root_runs == 21
    assert len(profile.items) == 21
    assert profile.experiment_ids == (
        "exp1_real_ai_feasibility",
        "exp2_real_ai_scalability",
        "exp3_real_ai_fault_recovery",
        "exp4_real_ai_protocol_ablation",
    )
    assert {
        experiment_id: sum(
            item.experiment_id == experiment_id for item in profile.items
        )
        for experiment_id in profile.experiment_ids
    } == {
        "exp1_real_ai_feasibility": 6,
        "exp2_real_ai_scalability": 3,
        "exp3_real_ai_fault_recovery": 7,
        "exp4_real_ai_protocol_ablation": 5,
    }
    assert [item.to_dict() for item in profile.items] == [
        item.to_dict()
        for item in full_profile.items
        if item.experiment_id != "exp5_real_ai_model_endpoint_comparison"
    ]
    assert profile.pilot_only is True
    assert profile.regression_only is True
    assert profile.paper_eligible is False


def test_exp3_exp4_smoke_profile_freezes_11_roots_and_explicit_baseline_omission() -> None:
    profile = load_paper_smoke_profile(
        Path("benchmarks/paper/paper_smoke_exp3_exp4_profile.v1.json")
    )

    assert profile.suite_id == "paper_smoke_exp3_exp4_v1"
    assert profile.profile_version == "v1"
    assert profile.experiment_ids == (
        "exp3_real_ai_fault_recovery",
        "exp4_real_ai_protocol_ablation",
    )
    assert profile.expected_root_runs == 11
    assert len(profile.items) == 11
    assert profile.baseline_policy == "omitted_for_smoke_regression"
    assert {item.case_id for item in profile.items} == {"factor_v2_easy_001"}
    assert {item.repeat_id for item in profile.items} == {0}
    exp3 = [
        item
        for item in profile.items
        if item.experiment_id == "exp3_real_ai_fault_recovery"
    ]
    exp4 = [
        item
        for item in profile.items
        if item.experiment_id == "exp4_real_ai_protocol_ablation"
    ]
    assert len(exp3) == 6
    assert {
        item.condition_selector["fault_type"]
        for item in exp3
        if item.condition_selector["fault_type"] != "worker_death"
    } == {
        "false_positive",
        "false_negative",
        "no_return",
        "late_submission",
        "executor_error",
    }
    assert all(
        item.condition_selector.get("fault_rate") == 1.0
        for item in exp3
        if item.condition_selector["fault_type"] != "worker_death"
    )
    worker_death = next(
        item for item in exp3 if item.condition_selector["fault_type"] == "worker_death"
    )
    assert worker_death.condition_selector["dead_worker_count"] == 1
    assert worker_death.condition_selector["kill_progress_percent"] == 50
    assert worker_death.condition_selector["worker_count"] == 10
    assert {item.condition_selector["ablation_mode"] for item in exp4} == {
        "FULL",
        "NO_VERIFICATION",
        "NO_PARSER_POLICY",
        "NO_REQUEUE",
        "NO_MERGE_GATE",
    }


def test_smoke_profile_cannot_upgrade_eligibility(tmp_path: Path) -> None:
    body = _profile_body()
    body["paper_eligible"] = True
    path = tmp_path / "invalid-smoke.json"
    path.write_text(json.dumps(body), encoding="utf-8")

    with pytest.raises(ValueError, match="paper_eligible=false"):
        load_paper_smoke_profile(path)


def test_smoke_resolution_keeps_canonical_condition_and_selection_identity(
    tmp_path: Path,
) -> None:
    profile_path = tmp_path / "smoke.json"
    profile_path.write_text(json.dumps(_profile_body()), encoding="utf-8")
    profile = load_paper_smoke_profile(profile_path)
    condition = _condition()
    selection = _selection()
    canonical_plan = PaperExperimentDispatchPlan(
        experiment_id=condition.experiment_id,
        output_root=(tmp_path / "formal" / condition.experiment_id).as_posix(),
        conditions=(condition,),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(condition, selection),
        ),
    )

    execution = resolve_paper_smoke_execution_plan(
        profile=profile,
        dispatch_plans=(canonical_plan,),
        catalog_id="tokenshare.paper.catalog",
        catalog_version="v2",
        catalog_digest=CATALOG_DIGEST,
        output_root=tmp_path / "smoke",
    )

    assert execution.direct_root_run_count == 1
    assert execution.root_case_filter == {
        condition.condition_id: ("factor_v2_easy_001",)
    }
    resolved = execution.items[0]
    assert resolved.condition_id == condition.condition_id
    assert resolved.condition_digest == condition.condition_digest
    assert resolved.selection_id == selection.selection_id
    assert resolved.selection_digest == selection.selection_digest
    smoke_condition, smoke_selection = execution.dispatch_plans[0].bound_items()[0]
    assert smoke_condition.to_dict() == condition.to_dict()
    assert smoke_selection.to_dict() == selection.to_dict()
    assert execution.dispatch_plans[0].paper_eligible_possible is False


def test_smoke_resolution_groups_distinct_cases_under_one_canonical_condition(
    tmp_path: Path,
) -> None:
    body = _profile_body()
    second_item = {
        **body["items"][0],
        "item_id": "exp1_factor_easy_second",
        "case_id": "factor_v2_easy_002",
    }
    body["items"].append(second_item)
    body["expected_root_runs"] = 2
    profile_path = tmp_path / "smoke.json"
    profile_path.write_text(json.dumps(body), encoding="utf-8")
    profile = load_paper_smoke_profile(profile_path)
    condition = _condition()
    selection = _selection()
    canonical_plan = PaperExperimentDispatchPlan(
        experiment_id=condition.experiment_id,
        output_root=(tmp_path / "formal" / condition.experiment_id).as_posix(),
        conditions=(condition,),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(condition, selection),
        ),
    )

    execution = resolve_paper_smoke_execution_plan(
        profile=profile,
        dispatch_plans=(canonical_plan,),
        catalog_id="tokenshare.paper.catalog",
        catalog_version="v2",
        catalog_digest=CATALOG_DIGEST,
        output_root=tmp_path / "smoke",
    )

    assert execution.direct_root_run_count == 2
    assert execution.root_case_filter == {
        condition.condition_id: (
            "factor_v2_easy_001",
            "factor_v2_easy_002",
        )
    }
    assert [item.case_id for item in execution.items] == [
        "factor_v2_easy_001",
        "factor_v2_easy_002",
    ]
    assert len(execution.dispatch_plans[0].conditions) == 1
    assert len(execution.dispatch_plans[0].condition_selection_bindings) == 1
    assert execution.budget_selection_commitments(
        expected_ai_units_by_case={
            "factor_v2_easy_001": 1,
            "factor_v2_easy_002": 3,
        }
    ) == [
        {
            **selection.to_dict(),
            "condition_id": condition.condition_id,
            "condition_digest": condition.condition_digest,
            "canonical_ordered_case_ids": list(selection.ordered_case_ids),
            "ordered_case_ids": [
                "factor_v2_easy_001",
                "factor_v2_easy_002",
            ],
            "expected_ai_unit_count": 4,
            "smoke_root_filter": True,
        }
    ]


def test_smoke_budget_prefers_frozen_per_case_ai_unit_commitments(
    tmp_path: Path,
) -> None:
    case_ids = tuple(f"factor_v2_hard_{index:03d}" for index in range(1, 5))
    body = _profile_body()
    body["experiment_ids"] = ["exp2_real_ai_scalability"]
    body["expected_root_runs"] = len(case_ids)
    body["items"] = [
        {
            **body["items"][0],
            "item_id": f"exp2_factor_hard_{index:03d}",
            "experiment_id": "exp2_real_ai_scalability",
            "case_id": case_id,
            "condition_selector": {
                **body["items"][0]["condition_selector"],
                "difficulty": "hard",
                "worker_count": 1,
            },
        }
        for index, case_id in enumerate(case_ids, start=1)
    ]
    profile_path = tmp_path / "smoke.json"
    profile_path.write_text(json.dumps(body), encoding="utf-8")
    profile = load_paper_smoke_profile(profile_path)
    condition = replace(
        _condition(),
        experiment_id="exp2_real_ai_scalability",
        condition_id="exp2_factorization_hard_w1_r0",
        difficulty="hard",
        paper_difficulty="hard",
        worker_count=1,
    )
    case_expected_ai_unit_counts = {case_id: 20 for case_id in case_ids}
    selection = Exp2FactorizationCaseSelection(
        selection_id="exp2-hard-20way",
        experiment_id=condition.experiment_id,
        suite_version="paper_v1",
        catalog_version="v2",
        domain="factorization",
        paper_difficulty="hard",
        topic_family=None,
        ordered_case_ids=case_ids,
        catalog_digest=CATALOG_DIGEST,
        expected_ai_unit_count=80,
        paper_eligible_required=True,
    )
    object.__setattr__(
        selection,
        "case_expected_ai_unit_counts",
        case_expected_ai_unit_counts,
    )
    canonical_plan = PaperExperimentDispatchPlan(
        experiment_id=condition.experiment_id,
        output_root=(tmp_path / "formal" / condition.experiment_id).as_posix(),
        conditions=(condition,),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(condition, selection),
        ),
    )
    execution = resolve_paper_smoke_execution_plan(
        profile=profile,
        dispatch_plans=(canonical_plan,),
        catalog_id="tokenshare.paper.catalog",
        catalog_version="v2",
        catalog_digest=CATALOG_DIGEST,
        output_root=tmp_path / "smoke",
    )

    commitments = execution.budget_selection_commitments(
        expected_ai_units_by_case={case_id: 8 for case_id in case_ids}
    )

    assert len(commitments) == 1
    commitment = commitments[0]
    assert commitment["ordered_case_ids"] == list(case_ids)
    assert commitment["case_expected_ai_unit_counts"] == (
        case_expected_ai_unit_counts
    )
    assert commitment["expected_ai_unit_count"] == 80
    assert commitment["expected_ai_unit_count"] == sum(
        commitment["case_expected_ai_unit_counts"][case_id]
        for case_id in commitment["ordered_case_ids"]
    )


def test_smoke_resolution_rejects_duplicate_condition_case_items(
    tmp_path: Path,
) -> None:
    body = _profile_body()
    body["items"].append(
        {
            **body["items"][0],
            "item_id": "exp1_factor_easy_duplicate",
        }
    )
    body["expected_root_runs"] = 2
    profile_path = tmp_path / "smoke.json"
    profile_path.write_text(json.dumps(body), encoding="utf-8")
    profile = load_paper_smoke_profile(profile_path)
    condition = _condition()
    selection = _selection()
    canonical_plan = PaperExperimentDispatchPlan(
        experiment_id=condition.experiment_id,
        output_root=(tmp_path / "formal" / condition.experiment_id).as_posix(),
        conditions=(condition,),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(condition, selection),
        ),
    )

    with pytest.raises(
        ValueError,
        match="paper smoke condition/case selections must be unique",
    ):
        resolve_paper_smoke_execution_plan(
            profile=profile,
            dispatch_plans=(canonical_plan,),
            catalog_id="tokenshare.paper.catalog",
            catalog_version="v2",
            catalog_digest=CATALOG_DIGEST,
            output_root=tmp_path / "smoke",
        )


def test_smoke_resolution_rejects_ambiguous_selector_before_execution(
    tmp_path: Path,
) -> None:
    profile_path = tmp_path / "smoke.json"
    profile_path.write_text(json.dumps(_profile_body()), encoding="utf-8")
    profile = load_paper_smoke_profile(profile_path)
    first = _condition()
    second = replace(
        first,
        condition_id="exp1_factor_easy_duplicate",
        seed=2,
    )
    selection = _selection()
    duplicate_selection = FrozenCaseSelection(
        **{
            key: value
            for key, value in selection.to_dict().items()
            if key
            not in {
                "selection_id",
                "selection_digest",
                "case_selection_digest",
                "execution_status",
                "paper_eligible_possible",
                "provider_calls_made",
            }
        },
        selection_id="selection-duplicate",
    )
    plan = PaperExperimentDispatchPlan(
        experiment_id=first.experiment_id,
        output_root=(tmp_path / "formal" / first.experiment_id).as_posix(),
        conditions=(first, second),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(first, selection),
            FrozenConditionSelectionBinding.from_condition(
                second, duplicate_selection
            ),
        ),
    )

    with pytest.raises(ValueError, match="exactly one canonical condition"):
        resolve_paper_smoke_execution_plan(
            profile=profile,
            dispatch_plans=(plan,),
            catalog_id="tokenshare.paper.catalog",
            catalog_version="v2",
            catalog_digest=CATALOG_DIGEST,
            output_root=tmp_path / "smoke",
        )


def test_formal_evidence_store_freezes_smoke_classification(tmp_path: Path) -> None:
    condition = _condition()
    selection = _selection()
    plan = PaperExperimentDispatchPlan(
        experiment_id=condition.experiment_id,
        output_root=(tmp_path / condition.experiment_id).as_posix(),
        conditions=(condition,),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(condition, selection),
        ),
        paper_eligible_possible=False,
    )
    bodies = {
        "suite": {
            "schema_version": "tokenshare.paper_formal_runner.v1",
            "suite_id": "test_smoke",
            "experiment_ids": [condition.experiment_id],
            "execution_classification": {
                "formal": False,
                "pilot_only": True,
                "regression_only": True,
                "paper_eligible": False,
                "execution_scope": "smoke_suite",
                "ineligibility_reasons": ["smoke_suite", "pilot_only"],
            },
            "root_case_filter": {
                condition.condition_id: ["factor_v2_easy_001"]
            },
        },
        "dispatch": {"plans": [plan.to_dict()]},
        "budget": {"budget_digest": "sha256:" + "4" * 64},
        "catalog": {"catalog_digest": CATALOG_DIGEST},
        "identity": {},
        "request_limits": {},
        "hard_limits": {},
    }

    FormalEvidenceStore.initialize(
        output_root=tmp_path,
        **bodies,
        capturing=False,
    )
    FormalEvidenceStore.load(
        output_root=tmp_path,
        **{f"expected_{name}": body for name, body in bodies.items()},
    )

    suite = json.loads((tmp_path / "suite_manifest.json").read_text(encoding="utf-8"))
    experiment = json.loads(
        (
            tmp_path
            / "experiments"
            / condition.experiment_id
            / "experiment_manifest.json"
        ).read_text(encoding="utf-8")
    )
    for manifest in (suite, experiment):
        assert manifest["formal"] is False
        assert manifest["pilot_only"] is True
        assert manifest["regression_only"] is True
        assert manifest["paper_eligible"] is False
        assert manifest["execution_scope"] == "smoke_suite"
        assert {"smoke_suite", "pilot_only"}.issubset(
            manifest["ineligibility_reasons"]
        )


def test_smoke_report_recomputes_usage_and_refs_from_persisted_evidence(
    tmp_path: Path,
) -> None:
    condition = _condition()
    selection = _selection()
    execution_plan = {
        "schema_version": "tokenshare.paper_smoke_execution_plan.v1",
        "suite_id": "test_smoke",
        "profile_digest": "sha256:" + "5" * 64,
        "catalog": {
            "catalog_id": "tokenshare.paper.catalog",
            "catalog_version": "v2",
            "catalog_digest": CATALOG_DIGEST,
        },
        "direct_root_run_count": 1,
        "items": [
            {
                "item_id": "exp1_factor_easy",
                "experiment_id": condition.experiment_id,
                "case_id": "factor_v2_easy_001",
                "repeat_id": 0,
                "condition_selector": {
                    "domain": "factorization",
                    "difficulty": "easy",
                    "worker_count": 10,
                },
                "condition_id": condition.condition_id,
                "condition_digest": condition.condition_digest,
                "selection_id": selection.selection_id,
                "selection_digest": selection.selection_digest,
            }
        ],
        "formal": False,
        "pilot_only": True,
        "regression_only": True,
        "paper_eligible": False,
        "ineligibility_reasons": ["smoke_suite", "pilot_only"],
    }
    (tmp_path / "smoke_execution_plan.json").write_text(
        json.dumps(execution_plan), encoding="utf-8"
    )
    (tmp_path / "suite_manifest.json").write_text(
        json.dumps(
            {
                "suite_id": "test_smoke",
                "status": "completed",
                "formal": False,
                "pilot_only": True,
                "regression_only": True,
                "paper_eligible": False,
                "execution_scope": "smoke_suite",
                "ineligibility_reasons": ["smoke_suite", "pilot_only"],
                "suite_identity": _smoke_suite_identity(
                    condition.experiment_id,
                    condition.condition_id,
                    "factor_v2_easy_001",
                ),
            }
        ),
        encoding="utf-8",
    )
    _write_jsonl(
        tmp_path / "conditions.jsonl",
        [
            {
                "experiment_id": condition.experiment_id,
                "condition_id": condition.condition_id,
                "repeat_id": 0,
            }
        ],
    )
    run_root = (
        tmp_path
        / "experiments"
        / condition.experiment_id
        / "runs"
        / condition.condition_id
        / "0"
    )
    generation = run_root / ".generations" / "generation-1"
    (generation / "events").mkdir(parents=True)
    (generation / "artifacts").mkdir(parents=True)
    record_ref_1, record_index_1 = _persist_model_execution_record(
        suite_root=tmp_path,
        generation=generation,
        task_id="factor_v2_easy_001",
        attempt_id="attempt-1",
        provider_attempt_count=1,
        experiment_id=condition.experiment_id,
        condition_id=condition.condition_id,
        repeat_id=0,
    )
    record_ref_2, record_index_2 = _persist_model_execution_record(
        suite_root=tmp_path,
        generation=generation,
        task_id="factor_v2_easy_001",
        attempt_id="attempt-2",
        provider_attempt_count=2,
        experiment_id=condition.experiment_id,
        condition_id=condition.condition_id,
        repeat_id=0,
    )
    _write_jsonl(
        generation / "per_task_results.jsonl",
        [
            {
                "task_id": "factor_v2_easy_001",
                "experiment_id": condition.experiment_id,
                "condition_id": condition.condition_id,
                "condition_digest": condition.condition_digest,
                "repeat_id": 0,
                "root_status": "completed",
                "accepted_validity": True,
                "wall_clock_ms": 125,
                "formal": False,
                "pilot_only": True,
                "regression_only": True,
                "paper_eligible": False,
            }
        ],
    )
    _write_jsonl(
        generation / "per_attempt_results.jsonl",
        [
            {
                "attempt_id": "attempt-1",
                "task_id": "factor_v2_easy_001",
                "experiment_id": condition.experiment_id,
                "condition_id": condition.condition_id,
                "repeat_id": 0,
                "provider_attempt_index": 0,
                "provider_attempt_count": 0,
                "model_execution_record_ref": record_ref_1,
                "latency_ms": 120,
                "prompt_tokens": 7,
                "completion_tokens": 5,
                "total_tokens": 12,
                "cost_estimate": 0.125,
            },
            {
                "attempt_id": "attempt-2",
                "task_id": "factor_v2_easy_001",
                "experiment_id": condition.experiment_id,
                "condition_id": condition.condition_id,
                "repeat_id": 0,
                "provider_attempt_index": 0,
                "provider_attempt_count": 2,
                "model_execution_record_ref": record_ref_2,
                "latency_ms": 280,
                "prompt_tokens": 2,
                "completion_tokens": 1,
                "total_tokens": 3,
                "cost_estimate": 0.025,
            },
        ],
    )
    _write_jsonl(generation / "fault_injections.jsonl", [])
    _write_jsonl(
        generation / "events" / "event_log.jsonl",
        [
            {
                "event_id": "event-1",
                "task_id": "factor_v2_easy_001",
                "experiment_id": condition.experiment_id,
                "condition_id": condition.condition_id,
                "repeat_id": 0,
            }
        ],
    )
    raw_path = run_root / "artifacts" / "raw.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text("{}", encoding="utf-8")
    _write_jsonl(
        generation / "artifacts" / "artifact_index.jsonl",
        [
            {
                "path": raw_path.relative_to(tmp_path).as_posix(),
                "content_hash": "sha256:" + sha256(raw_path.read_bytes()).hexdigest(),
                "task_id": "factor_v2_easy_001",
                "experiment_id": condition.experiment_id,
                "condition_id": condition.condition_id,
                "repeat_id": 0,
            },
            record_index_1,
            record_index_2,
        ],
    )
    _commit_smoke_generation(generation)

    report = generate_paper_smoke_report(output_root=tmp_path, secret_values=())

    row = report["rows"][0]
    assert row["provider_attempt_count"] == 3
    assert row["provider_latency_ms"] == pytest.approx(400.0)
    assert row["provider_latency_sample_size"] == 2
    assert row["provider_latency_missing_count"] == 0
    assert row["provider_latency_unavailable_reason"] is None
    assert row["prompt_tokens"] == 9
    assert row["prompt_tokens_sample_size"] == 2
    assert row["prompt_tokens_missing_count"] == 0
    assert row["prompt_tokens_unavailable_reason"] is None
    assert row["completion_tokens"] == 6
    assert row["completion_tokens_sample_size"] == 2
    assert row["completion_tokens_missing_count"] == 0
    assert row["completion_tokens_unavailable_reason"] is None
    assert row["total_tokens"] == 15
    assert row["total_tokens"] == row["prompt_tokens"] + row["completion_tokens"]
    assert row["total_tokens_sample_size"] == 2
    assert row["total_tokens_missing_count"] == 0
    assert row["cost_estimate"] == pytest.approx(0.15)
    assert row["cost_estimate_sample_size"] == 2
    assert row["cost_estimate_missing_count"] == 0
    assert row["provider_actual_billing"] is None
    assert row["provider_actual_billing_available"] is False
    assert row["outcome_status"] == "succeeded"
    assert row["evidence_integrity"] == "complete"
    assert row["event_refs"] == ["event-1"]
    assert row["artifact_refs"] == sorted(
        [
            record_index_1["path"],
            record_index_2["path"],
            raw_path.relative_to(tmp_path).as_posix(),
        ]
    )
    assert row["paper_eligible"] is False
    assert {"smoke_suite", "pilot_only"}.issubset(
        row["ineligibility_reasons"]
    )
    assert (tmp_path / "metrics" / "smoke_summary.csv").is_file()
    csv_header = (
        tmp_path / "metrics" / "smoke_summary.csv"
    ).read_text(encoding="utf-8").splitlines()[0].split(",")
    assert {
        "correctness_numerator",
        "correctness_denominator",
        "correctness_rate",
        "correctness_missing_count",
        "correctness_unavailable_reason",
        "completion_numerator",
        "completion_denominator",
        "completion_rate",
        "provider_latency_ms",
        "provider_latency_sample_size",
        "provider_latency_missing_count",
        "provider_latency_unavailable_reason",
        "prompt_tokens",
        "prompt_tokens_sample_size",
        "prompt_tokens_missing_count",
        "prompt_tokens_unavailable_reason",
        "completion_tokens",
        "completion_tokens_sample_size",
        "completion_tokens_missing_count",
        "completion_tokens_unavailable_reason",
    }.issubset(csv_header)
    assert (tmp_path / "metrics" / "smoke_failures.json").is_file()
    assert (tmp_path / "audit" / "smoke_eligibility_report.json").is_file()
    assert (tmp_path / "audit" / "smoke_evidence_manifest.json").is_file()
    assert (tmp_path / "audit" / "secret_scan_report.json").is_file()
    assert row["correctness_numerator"] == 1
    assert row["correctness_denominator"] == 1
    assert row["correctness_rate"] == pytest.approx(1.0)
    assert row["correctness_missing_count"] == 0
    assert row["completion_numerator"] == 1
    assert row["completion_denominator"] == 1
    assert row["completion_rate"] == pytest.approx(1.0)
    assert report["correctness_numerator"] == 1
    assert report["correctness_denominator"] == 1
    assert report["correctness_rate"] == pytest.approx(1.0)
    assert report["correctness_missing_count"] == 0
    assert report["completion_numerator"] == 1
    assert report["completion_denominator"] == 1
    assert report["completion_rate"] == pytest.approx(1.0)
    assert report["provider_latency_ms"] == pytest.approx(400.0)
    assert report["provider_latency_sample_size"] == 2
    assert report["provider_latency_missing_count"] == 0
    assert report["provider_latency_unavailable_reason"] is None
    assert report["prompt_tokens"] == 9
    assert report["prompt_tokens_sample_size"] == 2
    assert report["prompt_tokens_missing_count"] == 0
    assert report["prompt_tokens_unavailable_reason"] is None
    assert report["completion_tokens"] == 6
    assert report["completion_tokens_sample_size"] == 2
    assert report["completion_tokens_missing_count"] == 0
    assert report["completion_tokens_unavailable_reason"] is None
    assert report["total_tokens"] == (
        report["prompt_tokens"] + report["completion_tokens"]
    )


def test_smoke_report_fault_refs_use_traceable_fallbacks_without_none(
    tmp_path: Path,
) -> None:
    experiment_id = "exp3_real_ai_fault_recovery"
    condition_id = "exp3_fault_refs"
    case_id = "factor_v2_easy_001"
    repeat_id = 0
    (tmp_path / "suite_manifest.json").write_text(
        json.dumps(
            {
                "suite_id": "test_smoke",
                "status": "completed",
                "formal": False,
                "pilot_only": True,
                "regression_only": True,
                "paper_eligible": False,
                "execution_scope": "smoke_suite",
                "ineligibility_reasons": ["smoke_suite", "pilot_only"],
                "suite_identity": _smoke_suite_identity(
                    experiment_id,
                    condition_id,
                    case_id,
                ),
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "smoke_execution_plan.json").write_text(
        json.dumps(
            {
                "schema_version": "tokenshare.paper_smoke_execution_plan.v1",
                "suite_id": "test_smoke",
                "items": [
                    {
                        "item_id": "exp3_fault_refs",
                        "experiment_id": experiment_id,
                        "condition_id": condition_id,
                        "case_id": case_id,
                        "repeat_id": repeat_id,
                        "condition_selector": {"domain": "factorization"},
                    }
                ],
                "formal": False,
                "pilot_only": True,
                "regression_only": True,
                "paper_eligible": False,
            }
        ),
        encoding="utf-8",
    )
    _write_jsonl(
        tmp_path / "conditions.jsonl",
        [
            {
                "experiment_id": experiment_id,
                "condition_id": condition_id,
                "repeat_id": repeat_id,
            }
        ],
    )
    run_root = (
        tmp_path
        / "experiments"
        / experiment_id
        / "runs"
        / condition_id
        / str(repeat_id)
    )
    generation = run_root / ".generations" / "generation-1"
    (generation / "events").mkdir(parents=True)
    (generation / "artifacts").mkdir(parents=True)
    _write_jsonl(
        generation / "per_task_results.jsonl",
        [
            {
                "task_id": case_id,
                "experiment_id": experiment_id,
                "condition_id": condition_id,
                "repeat_id": repeat_id,
                "root_status": "completed",
                "formal": False,
                "pilot_only": True,
                "regression_only": True,
                "paper_eligible": False,
            }
        ],
    )
    _write_jsonl(
        generation / "per_attempt_results.jsonl",
        [
            {
                "attempt_id": "attempt-not-dispatched",
                "task_id": case_id,
                "experiment_id": experiment_id,
                "condition_id": condition_id,
                "repeat_id": repeat_id,
                "provider_attempt_count": 0,
            }
        ],
    )
    _write_jsonl(
        generation / "events" / "event_log.jsonl",
        [
            {
                "event_id": "event-not-dispatched",
                "task_id": case_id,
                "experiment_id": experiment_id,
                "condition_id": condition_id,
                "repeat_id": repeat_id,
            }
        ],
    )
    evidence_path = run_root / "artifacts" / "terminal.json"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text("{}", encoding="utf-8")
    _write_jsonl(
        generation / "artifacts" / "artifact_index.jsonl",
        [
            {
                "path": evidence_path.relative_to(tmp_path).as_posix(),
                "content_hash": "sha256:"
                + sha256(evidence_path.read_bytes()).hexdigest(),
                "task_id": case_id,
                "experiment_id": experiment_id,
                "condition_id": condition_id,
                "repeat_id": repeat_id,
            }
        ],
    )
    _write_jsonl(
        generation / "fault_injections.jsonl",
        [
            {
                "task_id": case_id,
                "experiment_id": experiment_id,
                "condition_id": condition_id,
                "repeat_id": repeat_id,
                "record_ref": {
                    "path": "faults/z.json",
                    "artifact_id": "shadow-artifact",
                    "uri": "artifact://shadow",
                },
                "fault_injection_id": "shadow-fault-id",
            },
            {
                "task_id": case_id,
                "experiment_id": experiment_id,
                "condition_id": condition_id,
                "repeat_id": repeat_id,
                "record_ref": {
                    "artifact_id": "fault-artifact-a",
                    "uri": "artifact://shadow-a",
                },
                "fault_injection_id": "shadow-fault-id-a",
            },
            {
                "task_id": case_id,
                "experiment_id": experiment_id,
                "condition_id": condition_id,
                "repeat_id": repeat_id,
                "record_ref": {"uri": "artifact://fault-b"},
                "fault_injection_id": "shadow-fault-id-b",
            },
            {
                "task_id": case_id,
                "experiment_id": experiment_id,
                "condition_id": condition_id,
                "repeat_id": repeat_id,
                "record_ref": {},
                "fault_injection_id": "fault-id-c",
            },
            {
                "task_id": case_id,
                "experiment_id": experiment_id,
                "condition_id": condition_id,
                "repeat_id": repeat_id,
                "fault_injection_id": "fault-id-d",
            },
            {
                "task_id": case_id,
                "experiment_id": experiment_id,
                "condition_id": condition_id,
                "repeat_id": repeat_id,
                "record_ref": {"path": "faults/z.json"},
            },
            {
                "task_id": case_id,
                "experiment_id": experiment_id,
                "condition_id": condition_id,
                "repeat_id": repeat_id,
                "record_ref": {"path": None, "artifact_id": "", "uri": "   "},
                "fault_injection_id": "",
            },
        ],
    )
    _commit_smoke_generation(generation)

    report = generate_paper_smoke_report(output_root=tmp_path)

    assert report["rows"][0]["fault_refs"] == [
        "artifact://fault-b",
        "fault-artifact-a",
        "fault-id-c",
        "fault-id-d",
        "faults/z.json",
    ]
    row = report["rows"][0]
    assert row["provider_attempt_count"] is None
    assert row["provider_attempt_count_unavailable_reason"] == (
        "invalid_model_execution_record_ref"
    )
    assert row["total_tokens"] is None
    assert row["total_tokens_sample_size"] == 0
    assert row["total_tokens_missing_count"] == 1
    assert row["total_tokens_unavailable_reason"] == (
        "invalid_model_execution_record_ref"
    )
    assert row["cost_estimate"] is None
    assert row["cost_estimate_sample_size"] == 0
    assert row["cost_estimate_missing_count"] == 1
    assert row["cost_estimate_unavailable_reason"] == (
        "invalid_model_execution_record_ref"
    )
    assert row["accepted_validity"] is None
    assert row["accepted_validity_unavailable_reason"] == (
        "missing_accepted_validity_evidence"
    )
    assert row["wall_clock_ms"] is None
    assert row["wall_clock_ms_unavailable_reason"] == (
        "missing_wall_clock_evidence"
    )
    assert row["evidence_integrity"] == "invalid"
    assert row["smoke_execution_status"] == "incomplete"
    assert report["completed_root_count"] == 0
    assert report["failed_or_blocked_root_count"] == 1
    assert report["provider_attempt_count"] is None
    assert report["provider_attempt_count_missing_count"] == 1
    assert report["total_tokens"] is None
    assert report["total_tokens_missing_count"] == 1
    assert report["cost_estimate"] is None
    assert report["cost_estimate_missing_count"] == 1
    assert row["provider_latency_ms"] is None
    assert row["provider_latency_sample_size"] == 0
    assert row["provider_latency_missing_count"] == 1
    assert row["provider_latency_unavailable_reason"] == (
        "invalid_model_execution_record_ref"
    )
    assert report["provider_latency_ms"] is None
    assert report["provider_latency_sample_size"] == 0
    assert report["provider_latency_missing_count"] == 1
    assert report["correctness_numerator"] == 0
    assert report["correctness_denominator"] == 1
    assert report["correctness_rate"] is None
    assert report["correctness_missing_count"] == 1


def test_smoke_summary_keeps_false_distinct_from_missing_with_fixed_denominator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "suite_manifest.json").write_text(
        json.dumps(
            {
                "suite_id": "test_smoke",
                "status": "completed_with_failures",
                "formal": False,
                "pilot_only": True,
                "regression_only": True,
                "paper_eligible": False,
                "execution_scope": "smoke_suite",
                "ineligibility_reasons": ["smoke_suite", "pilot_only"],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "smoke_execution_plan.json").write_text(
        json.dumps(
            {
                "items": [
                    {"case_id": "observed-false"},
                    {"case_id": "missing-correctness"},
                ]
            }
        ),
        encoding="utf-8",
    )
    rows = {
        "observed-false": {
            "case_id": "observed-false",
            "accepted_validity": False,
            "accepted_validity_unavailable_reason": None,
            "smoke_execution_status": "completed",
            "root_status": "completed",
            "outcome_status": "succeeded",
            "evidence_integrity": "complete",
            "provider_attempt_count": 0,
            "prompt_tokens": 0,
            "prompt_tokens_sample_size": 0,
            "prompt_tokens_missing_count": 0,
            "completion_tokens": 0,
            "completion_tokens_sample_size": 0,
            "completion_tokens_missing_count": 0,
            "total_tokens": 0,
            "total_tokens_sample_size": 0,
            "total_tokens_missing_count": 0,
            "cost_estimate": 0.0,
            "cost_estimate_sample_size": 0,
            "cost_estimate_missing_count": 0,
            "provider_latency_ms": 0.0,
            "provider_latency_sample_size": 0,
            "provider_latency_missing_count": 0,
            "paper_eligible": False,
        },
        "missing-correctness": {
            "case_id": "missing-correctness",
            "accepted_validity": None,
            "accepted_validity_unavailable_reason": (
                "missing_accepted_validity_evidence"
            ),
            "smoke_execution_status": "blocked",
            "root_status": "blocked",
            "outcome_status": "blocked_dependency",
            "evidence_integrity": "missing",
            "provider_attempt_count": 0,
            "prompt_tokens": 0,
            "prompt_tokens_sample_size": 0,
            "prompt_tokens_missing_count": 0,
            "completion_tokens": 0,
            "completion_tokens_sample_size": 0,
            "completion_tokens_missing_count": 0,
            "total_tokens": 0,
            "total_tokens_sample_size": 0,
            "total_tokens_missing_count": 0,
            "cost_estimate": 0.0,
            "cost_estimate_sample_size": 0,
            "cost_estimate_missing_count": 0,
            "provider_latency_ms": 0.0,
            "provider_latency_sample_size": 0,
            "provider_latency_missing_count": 0,
            "paper_eligible": False,
        },
    }
    monkeypatch.setattr(
        smoke_report_module,
        "_row_from_persisted_evidence",
        lambda **kwargs: dict(rows[kwargs["item"]["case_id"]]),
    )

    report = generate_paper_smoke_report(output_root=tmp_path)

    assert report["correctness_numerator"] == 0
    assert report["correctness_denominator"] == 2
    assert report["correctness_rate"] is None
    assert report["correctness_missing_count"] == 1
    assert report["correctness_unavailable_reason"] == (
        "incomplete_correctness_evidence"
    )
    assert report["completion_numerator"] == 1
    assert report["completion_denominator"] == 2
    assert report["completion_rate"] == pytest.approx(0.5)
    false_row, missing_row = report["rows"]
    assert false_row["correctness_numerator"] == 0
    assert false_row["correctness_rate"] == pytest.approx(0.0)
    assert false_row["correctness_missing_count"] == 0
    assert missing_row["correctness_numerator"] == 0
    assert missing_row["correctness_rate"] is None
    assert missing_row["correctness_missing_count"] == 1
    assert false_row["completion_rate"] == pytest.approx(1.0)
    assert missing_row["completion_rate"] == pytest.approx(0.0)


def test_smoke_usage_does_not_infer_provider_call_from_ref_shape(
    tmp_path: Path,
) -> None:
    observation = _actual_usage(
        [
            {
                "attempt_id": "attempt-1",
                "task_id": "case-1",
                "provider_attempt_count": 0,
                "model_execution_record_ref": {"artifact_id": "shape-only"},
                "total_tokens": 12,
                "cost_estimate": 0.125,
            }
        ],
        root=tmp_path,
        artifacts=(),
        capturing=False,
    )

    assert observation["provider_attempt_count"] is None
    assert observation["provider_attempt_count_unavailable_reason"] == (
        "invalid_model_execution_record_ref"
    )
    assert observation["total_tokens"] is None
    assert observation["total_tokens_sample_size"] == 0
    assert observation["total_tokens_missing_count"] == 1
    assert observation["cost_estimate"] is None
    assert observation["cost_estimate_sample_size"] == 0
    assert observation["cost_estimate_missing_count"] == 1


def test_smoke_usage_is_complete_or_null_for_verified_provider_calls(
    tmp_path: Path,
) -> None:
    generation = tmp_path / "generation"
    (generation / "artifacts").mkdir(parents=True)
    record_ref, record_index = _persist_model_execution_record(
        suite_root=tmp_path,
        generation=generation,
        task_id="case-1",
        attempt_id="attempt-1",
        provider_attempt_count=1,
    )

    observation = _actual_usage(
        [
            {
                "attempt_id": "attempt-1",
                "task_id": "case-1",
                "provider_attempt_count": 1,
                "model_execution_record_ref": record_ref,
                "total_tokens": None,
                "cost_estimate": None,
            }
        ],
        root=tmp_path,
        artifacts=(record_index,),
        capturing=False,
    )

    assert observation["provider_attempt_count"] == 1
    assert observation["provider_latency_ms"] is None
    assert observation["provider_latency_sample_size"] == 0
    assert observation["provider_latency_missing_count"] == 1
    assert observation["provider_latency_unavailable_reason"] == (
        "missing_provider_latency_evidence"
    )
    assert observation["prompt_tokens"] is None
    assert observation["prompt_tokens_sample_size"] == 0
    assert observation["prompt_tokens_missing_count"] == 1
    assert observation["prompt_tokens_unavailable_reason"] == (
        "missing_prompt_tokens_evidence"
    )
    assert observation["completion_tokens"] is None
    assert observation["completion_tokens_sample_size"] == 0
    assert observation["completion_tokens_missing_count"] == 1
    assert observation["completion_tokens_unavailable_reason"] == (
        "missing_completion_tokens_evidence"
    )
    assert observation["total_tokens"] is None
    assert observation["total_tokens_sample_size"] == 0
    assert observation["total_tokens_missing_count"] == 1
    assert observation["total_tokens_unavailable_reason"] == (
        "missing_total_tokens_evidence"
    )
    assert observation["cost_estimate"] is None
    assert observation["cost_estimate_sample_size"] == 0
    assert observation["cost_estimate_missing_count"] == 1
    assert observation["cost_estimate_unavailable_reason"] == (
        "missing_cost_estimate_evidence"
    )

    capturing = _actual_usage(
        (),
        root=tmp_path,
        artifacts=(),
        capturing=True,
    )
    assert capturing["provider_attempt_count"] == 0
    assert capturing["provider_latency_ms"] == 0.0
    assert capturing["provider_latency_sample_size"] == 0
    assert capturing["provider_latency_missing_count"] == 0
    assert capturing["provider_latency_unavailable_reason"] == (
        "no_provider_attempts"
    )
    assert capturing["prompt_tokens"] == 0
    assert capturing["prompt_tokens_sample_size"] == 0
    assert capturing["prompt_tokens_missing_count"] == 0
    assert capturing["completion_tokens"] == 0
    assert capturing["completion_tokens_sample_size"] == 0
    assert capturing["completion_tokens_missing_count"] == 0
    assert capturing["total_tokens"] == 0
    assert capturing["total_tokens_missing_count"] == 0
    assert capturing["cost_estimate"] == 0.0
    assert capturing["cost_estimate_missing_count"] == 0


def test_smoke_usage_recovers_fault_hidden_latency_from_verified_provenance(
    tmp_path: Path,
) -> None:
    generation = tmp_path / "generation"
    (generation / "artifacts").mkdir(parents=True)
    record_ref, artifact_indices = _persist_model_execution_record_with_latency(
        suite_root=tmp_path,
        generation=generation,
        task_id="case-1",
        attempt_id="attempt-1",
        latency_values=(11, 13),
    )

    observation = _actual_usage(
        (
            {
                "attempt_id": "attempt-1",
                "task_id": "case-1",
                "provider_attempt_count": 2,
                "model_execution_record_ref": record_ref,
                "latency_ms": None,
                "prompt_tokens": 7,
                "completion_tokens": 5,
                "total_tokens": 12,
                "cost_estimate": 0.125,
            },
        ),
        root=tmp_path,
        artifacts=artifact_indices,
        capturing=False,
    )

    assert observation["provider_attempt_count"] == 2
    assert observation["provider_latency_ms"] == pytest.approx(24.0)
    assert observation["provider_latency_sample_size"] == 2
    assert observation["provider_latency_missing_count"] == 0
    assert observation["provider_latency_unavailable_reason"] is None


def test_smoke_usage_projects_prompt_completion_and_total_from_validated_attempts(
    tmp_path: Path,
) -> None:
    generation = tmp_path / "generation"
    (generation / "artifacts").mkdir(parents=True)
    first_ref, first_index = _persist_model_execution_record(
        suite_root=tmp_path,
        generation=generation,
        task_id="case-1",
        attempt_id="attempt-1",
        provider_attempt_count=1,
    )
    second_ref, second_index = _persist_model_execution_record(
        suite_root=tmp_path,
        generation=generation,
        task_id="case-1",
        attempt_id="attempt-2",
        provider_attempt_count=1,
    )

    observation = _actual_usage(
        (
            {
                "attempt_id": "attempt-1",
                "task_id": "case-1",
                "provider_attempt_count": 1,
                "model_execution_record_ref": first_ref,
                "prompt_tokens": 7,
                "completion_tokens": 5,
                "total_tokens": 12,
                "cost_estimate": 0.125,
            },
            {
                "attempt_id": "attempt-2",
                "task_id": "case-1",
                "provider_attempt_count": 1,
                "model_execution_record_ref": second_ref,
                "prompt_tokens": 2,
                "completion_tokens": 1,
                "total_tokens": 3,
                "cost_estimate": 0.025,
            },
        ),
        root=tmp_path,
        artifacts=(first_index, second_index),
        capturing=False,
    )

    assert observation["prompt_tokens"] == 9
    assert observation["prompt_tokens_sample_size"] == 2
    assert observation["prompt_tokens_missing_count"] == 0
    assert observation["prompt_tokens_unavailable_reason"] is None
    assert observation["completion_tokens"] == 6
    assert observation["completion_tokens_sample_size"] == 2
    assert observation["completion_tokens_missing_count"] == 0
    assert observation["completion_tokens_unavailable_reason"] is None
    assert observation["total_tokens"] == 15
    assert observation["total_tokens"] == (
        observation["prompt_tokens"] + observation["completion_tokens"]
    )


def test_smoke_usage_marks_inconsistent_token_components_unavailable(
    tmp_path: Path,
) -> None:
    generation = tmp_path / "generation"
    (generation / "artifacts").mkdir(parents=True)
    record_ref, record_index = _persist_model_execution_record(
        suite_root=tmp_path,
        generation=generation,
        task_id="case-1",
        attempt_id="attempt-1",
        provider_attempt_count=1,
    )

    observation = _actual_usage(
        (
            {
                "attempt_id": "attempt-1",
                "task_id": "case-1",
                "provider_attempt_count": 1,
                "model_execution_record_ref": record_ref,
                "prompt_tokens": 7,
                "completion_tokens": 5,
                "total_tokens": 13,
                "cost_estimate": 0.125,
            },
        ),
        root=tmp_path,
        artifacts=(record_index,),
        capturing=False,
    )

    for field_name in ("prompt_tokens", "completion_tokens", "total_tokens"):
        assert observation[field_name] is None
        assert observation[f"{field_name}_sample_size"] == 0
        assert observation[f"{field_name}_missing_count"] == 1
        assert observation[f"{field_name}_unavailable_reason"] == (
            "inconsistent_token_usage_evidence"
        )
    assert observation["evidence_issue"] is None


def test_smoke_usage_missing_component_is_nonblocking_metric_unavailability(
    tmp_path: Path,
) -> None:
    generation = tmp_path / "generation"
    (generation / "artifacts").mkdir(parents=True)
    record_ref, record_index = _persist_model_execution_record(
        suite_root=tmp_path,
        generation=generation,
        task_id="case-1",
        attempt_id="attempt-1",
        provider_attempt_count=1,
    )

    observation = _actual_usage(
        (
            {
                "attempt_id": "attempt-1",
                "task_id": "case-1",
                "provider_attempt_count": 1,
                "model_execution_record_ref": record_ref,
                "prompt_tokens": 7,
                "total_tokens": 12,
                "cost_estimate": 0.125,
            },
        ),
        root=tmp_path,
        artifacts=(record_index,),
        capturing=False,
    )

    assert observation["prompt_tokens"] == 7
    assert observation["completion_tokens"] is None
    assert observation["completion_tokens_missing_count"] == 1
    assert observation["completion_tokens_unavailable_reason"] == (
        "missing_completion_tokens_evidence"
    )
    assert observation["total_tokens"] == 12
    assert observation["evidence_issue"] is None


def test_smoke_usage_joins_model_record_on_protocol_task_id(
    tmp_path: Path,
) -> None:
    generation = tmp_path / "generation"
    (generation / "artifacts").mkdir(parents=True)
    record_ref, record_index = _persist_model_execution_record(
        suite_root=tmp_path,
        generation=generation,
        task_id="protocol-child-1",
        attempt_id="attempt-1",
        provider_attempt_count=1,
    )

    observation = _actual_usage(
        [
            {
                "attempt_id": "attempt-1",
                "task_id": "case-1",
                "protocol_task_id": "protocol-child-1",
                "provider_attempt_count": 1,
                "model_execution_record_ref": record_ref,
                "total_tokens": 12,
                "cost_estimate": 0.125,
            }
        ],
        root=tmp_path,
        artifacts=(record_index,),
        capturing=False,
    )

    assert observation["provider_attempt_count"] == 1
    assert observation["provider_attempt_count_unavailable_reason"] is None
    assert observation["total_tokens"] == 12
    assert observation["cost_estimate"] == pytest.approx(0.125)


def _persist_model_execution_record(
    *,
    suite_root: Path,
    generation: Path,
    task_id: str,
    attempt_id: str,
    provider_attempt_count: int,
    experiment_id: str | None = None,
    condition_id: str | None = None,
    repeat_id: int | None = None,
) -> tuple[dict, dict]:
    record = {
        "schema_version": "tokenshare.paper_model_execution_record.v2",
        "task_id": task_id,
        "attempt_id": attempt_id,
        "actual_provider_attempts": [
            {"provider_attempt_index": index}
            for index in range(provider_attempt_count)
        ],
        "actual_request_identities": [
            {"provider_attempt_index": index}
            for index in range(provider_attempt_count)
        ],
    }
    if experiment_id is not None:
        record.update(
            {
                "experiment_id": experiment_id,
                "condition_id": condition_id,
                "repeat_id": repeat_id,
            }
        )
    record["record_digest"] = digest_json(record)
    artifact_root = (
        generation.parents[1]
        if generation.parent.name == ".generations"
        else generation
    )
    path = artifact_root / "artifacts" / f"{attempt_id}-model-record.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    content_hash = "sha256:" + sha256(path.read_bytes()).hexdigest()
    artifact_id = f"model-record-{attempt_id}"
    ref = {
        "artifact_id": artifact_id,
        "content_hash": content_hash,
    }
    index = {
        **ref,
        "path": path.relative_to(suite_root).as_posix(),
        "task_id": task_id,
    }
    if experiment_id is not None:
        index.update(
            {
                "experiment_id": experiment_id,
                "condition_id": condition_id,
                "repeat_id": repeat_id,
            }
        )
    return ref, index


def _persist_model_execution_record_with_latency(
    *,
    suite_root: Path,
    generation: Path,
    task_id: str,
    attempt_id: str,
    latency_values: tuple[int, ...],
) -> tuple[dict, tuple[dict, dict]]:
    artifact_root = (
        generation.parents[1]
        if generation.parent.name == ".generations"
        else generation
    )
    provenance_path = artifact_root / "artifacts" / f"{attempt_id}-provenance.json"
    provenance = {
        "schema_version": "phase7.ai_provider_call_provenance.v2",
        "attempts": [
            {
                "provider_family": "test",
                "configured_model": "test-model",
                "entry_id": "test-entry",
                "result_kind": "succeeded",
                "latency_ms": latency,
            }
            for latency in latency_values
        ],
    }
    provenance_path.write_text(
        json.dumps(provenance, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    provenance_hash = "sha256:" + sha256(provenance_path.read_bytes()).hexdigest()
    provenance_ref = {
        "artifact_id": f"provenance-{attempt_id}",
        "content_hash": provenance_hash,
    }
    provenance_index = {
        **provenance_ref,
        "path": provenance_path.relative_to(suite_root).as_posix(),
        "task_id": task_id,
    }
    record = {
        "schema_version": "tokenshare.paper_model_execution_record.v2",
        "task_id": task_id,
        "attempt_id": attempt_id,
        "provenance_ref": provenance_ref,
        "actual_provider_attempts": [
            {
                "provider_family": "test",
                "configured_model": "test-model",
                "entry_id": "test-entry",
                "result_kind": "succeeded",
            }
            for _ in latency_values
        ],
        "actual_request_identities": [
            {"provider_attempt_index": index}
            for index, _ in enumerate(latency_values)
        ],
    }
    record["record_digest"] = digest_json(record)
    record_path = artifact_root / "artifacts" / f"{attempt_id}-model-record.json"
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    record_hash = "sha256:" + sha256(record_path.read_bytes()).hexdigest()
    record_ref = {
        "artifact_id": f"model-record-{attempt_id}",
        "content_hash": record_hash,
    }
    record_index = {
        **record_ref,
        "path": record_path.relative_to(suite_root).as_posix(),
        "task_id": task_id,
    }
    return record_ref, (record_index, provenance_index)


def _profile_body() -> dict:
    return {
        "schema_version": "tokenshare.paper_smoke_profile.v1",
        "suite_id": "test_smoke",
        "profile_version": "v1",
        "catalog": {
            "catalog_id": "tokenshare.paper.catalog",
            "catalog_version": "v2",
            "catalog_digest": CATALOG_DIGEST,
        },
        "experiment_ids": ["exp1_real_ai_feasibility"],
        "expected_root_runs": 1,
        "output_mode": "isolated_smoke_root",
        "formal": False,
        "pilot_only": True,
        "regression_only": True,
        "paper_eligible": False,
        "ineligibility_reasons": ["smoke_suite", "pilot_only"],
        "items": [
            {
                "item_id": "exp1_factor_easy",
                "experiment_id": "exp1_real_ai_feasibility",
                "case_id": "factor_v2_easy_001",
                "repeat_id": 0,
                "condition_selector": {
                    "domain": "factorization",
                    "difficulty": "easy",
                    "worker_count": 10,
                    "fault_type": "none",
                    "fault_rate": 0.0,
                    "ablation_mode": "FULL",
                    "model_entry_id": "glm_5_2_exp1_baseline",
                },
            }
        ],
    }


def _v3_profile_body() -> dict:
    return json.loads(
        Path("benchmarks/paper/paper_smoke_profile.v3.json").read_text(
            encoding="utf-8"
        )
    )


def _refresh_profile_digest(body: dict) -> None:
    digest_body = dict(body)
    digest_body.pop("profile_digest", None)
    body["profile_digest"] = digest_json(digest_body)


def _condition() -> PaperExperimentCondition:
    return PaperExperimentCondition(
        schema_version="tokenshare.paper_condition.v2",
        experiment_id="exp1_real_ai_feasibility",
        condition_id="exp1_factor_easy",
        domain="factorization",
        difficulty="easy",
        paper_difficulty="easy",
        worker_count=10,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        provider_config_id="exp1_baseline_siliconflow",
        model_entry_id="glm_5_2_exp1_baseline",
        provider_family="siliconflow",
        provider_model_id="zai-org/GLM-5.2",
        reasoning_profile_id="default",
        source_provider_config_digest="sha256:" + "2" * 64,
        model_endpoint_identity_digest="sha256:" + "3" * 64,
        repeat_id=0,
        seed=1,
        catalog_digest=CATALOG_DIGEST,
        real_transport_required=True,
        paper_eligible_required=True,
    )


def _selection() -> FrozenCaseSelection:
    return FrozenCaseSelection(
        selection_id="canonical-selection",
        experiment_id="exp1_real_ai_feasibility",
        suite_version="paper_v1",
        catalog_version="v2",
        domain="factorization",
        paper_difficulty="easy",
        topic_family=None,
        ordered_case_ids=("factor_v2_easy_001", "factor_v2_easy_002"),
        catalog_digest=CATALOG_DIGEST,
        expected_ai_unit_count=4,
        paper_eligible_required=True,
    )


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
