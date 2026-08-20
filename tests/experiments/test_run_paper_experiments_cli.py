import json
import os
import socket
import time
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from threading import Lock
from types import SimpleNamespace

import pytest

import tokenshare.experiments.factorization_paper_adapter as factorization_paper_adapter
import tokenshare.experiments.lean_paper_adapter as lean_paper_adapter
import tokenshare.experiments.paper_catalog as paper_catalog_module
import tokenshare.experiments.paper_exp1 as paper_exp1
import tokenshare.experiments.paper_formal_runner as formal_runner
import tokenshare.experiments.paper_runner as paper_runner
import tokenshare.experiments.paper_smoke as paper_smoke
import tokenshare.experiments.run_paper_experiments as paper_cli
from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.experiments.factorization_paper_adapter import (
    ScriptedFactorizationRangeTransport,
)
from tokenshare.experiments.paper_budget import load_exp1_pilot_profile
from tokenshare.experiments.paper_catalog import PaperInputCatalogManifest
from tokenshare.experiments.paper_model_identity import PaperModelEndpointIdentity
from tokenshare.experiments.paper_model_policy import (
    EXP5_PRICING_FRESHNESS_AS_OF,
    EXP5_PRICING_FRESHNESS_AUTHORITY_SCHEMA_VERSION,
    EXP5_PRICING_MAX_AGE_DAYS,
    PAPER_MODEL_ENDPOINT_COHORT_V3_ID,
    PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS,
    PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBERS,
    load_model_endpoint_cohort,
    load_model_entry_map,
    load_provider_config_map,
)
from tokenshare.experiments.paper_models import (
    PaperStatus,
    PaperSuiteResult,
    digest_json,
)
from tokenshare.experiments.run_paper_experiments import main
from tests.phase7_fixtures import prepared_wire_kwargs


def test_results_first_reuse_rebuilds_exp5_pricing_authority_from_current_config() -> None:
    cohort = load_model_endpoint_cohort(paper_cli.DEFAULT_EXP5_MODEL_COHORT)
    entry_map = load_model_entry_map(paper_cli.DEFAULT_EXP5_MODEL_ENTRY_MAP)
    current_provider_configs = load_provider_config_map(
        {"siliconflow": paper_cli.DEFAULT_EXP5_PROVIDER_CONFIG}
    )
    stale_provider_configs = dict(current_provider_configs)
    stale_provider_configs[formal_runner.APPROVED_ENDPOINT_BINDINGS_KEY] = {
        "exp5_real_ai_model_endpoint_comparison": {
            "pricing_freshness_authority": {
                "schema_version": EXP5_PRICING_FRESHNESS_AUTHORITY_SCHEMA_VERSION,
                "pricing_freshness_as_of": "2026-07-29",
                "max_age_days": EXP5_PRICING_MAX_AGE_DAYS,
            }
        }
    }

    refreshed = paper_cli.refresh_results_first_exp5_execution_authority(
        base_ai_api_configs=stale_provider_configs,
        cohort=cohort,
        entry_map=entry_map,
        provider_configs=current_provider_configs,
    )

    binding = refreshed[formal_runner.APPROVED_ENDPOINT_BINDINGS_KEY][
        "exp5_real_ai_model_endpoint_comparison"
    ]
    pricing_authority = binding["pricing_freshness_authority"]
    assert refreshed["siliconflow"] is current_provider_configs["siliconflow"]
    assert pricing_authority["pricing_freshness_as_of"] == EXP5_PRICING_FRESHNESS_AS_OF
    assert pricing_authority["authority_digest"] == digest_json(
        {
            name: value
            for name, value in pricing_authority.items()
            if name != "authority_digest"
        }
    )
    assert set(pricing_authority["source_provider_config_digest_by_member"]) == set(
        PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS
    )


def test_legacy_cli_routes_pipeline_subcommands_without_parsing_them(monkeypatch) -> None:
    observed: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        paper_cli,
        "run_paper_pipeline_main",
        lambda argv: observed.append(tuple(argv)) or 17,
    )

    assert main(["validate-profile", "--profile", "tracked.json"]) == 17
    assert observed == [("validate-profile", "--profile", "tracked.json")]


def test_legacy_cli_routes_representative_full_plan_smoke_without_legacy_gates(
    monkeypatch,
) -> None:
    observed: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        paper_cli,
        "run_paper_pipeline_main",
        lambda argv: observed.append(tuple(argv)) or 19,
    )
    argv = (
        "representative-full-plan-smoke",
        "--output-root",
        "fresh-output",
        "--planning-artifact-root",
        "planning",
        "--plan-bundle-root",
        "bundle",
        "--new-run",
        "--plan-only",
    )

    assert main(argv) == 19
    assert observed == [argv]


def test_legacy_cli_routes_full_results_first_without_legacy_gates(monkeypatch) -> None:
    observed: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        paper_cli,
        "run_paper_pipeline_main",
        lambda argv: observed.append(tuple(argv)) or 23,
    )
    argv = (
        "run-results-first",
        "--selection",
        "full",
        "--output-root",
        "fresh-output",
        "--planning-artifact-root",
        "planning",
        "--plan-bundle-root",
        "bundle",
        "--new-run",
        "--allow-provider-calls",
    )

    assert main(argv) == 23
    assert observed == [argv]


def test_legacy_direct_paid_formal_path_is_not_an_official_entrypoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    class FailIfTransportUsed:
        def post_chat_completion(self, **_kwargs):
            pytest.fail("legacy direct formal path must stop before transport")

    monkeypatch.setattr(
        paper_cli,
        "_paper_output_root",
        lambda _path: pytest.fail(
            "legacy direct paid guard must precede output/planning construction"
        ),
    )

    exit_code = main(
        [
            "--output-root",
            str(tmp_path / "legacy-output"),
            "--experiments",
            "exp1,exp2,exp3,exp4,exp5",
            "--real-transport",
        ],
        gate_c_transport=FailIfTransportUsed(),
    )

    body = json.loads(capsys.readouterr().out)
    assert exit_code == 3
    assert body["status"] == "blocked"
    assert body["failure_kind"] == "legacy_direct_paid_execution_deauthorized"
    assert body["provider_calls_made"] is None
    assert body["provider_calls_missing_reason"] == (
        "legacy_direct_path_has_no_atomic_ledger"
    )
    assert "run-results-first" in body["message"]
    assert not (tmp_path / "legacy-output").exists()


def test_canonical_results_first_builder_freezes_one_typed_full_authority_for_both_scales(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.paper_budget import PaperExecutionBudgetProjection
    from tokenshare.experiments.paper_formal_plan import (
        FormalExecutionCoverage,
        FormalPlanSnapshot,
        FormalPreparedRequestInventory,
    )
    from tokenshare.experiments.run_paper_pipeline import (
        ResultsFirstExecutionAuthority,
        ResultsFirstExecutionPolicy,
    )

    authorities = paper_cli.build_results_first_execution_authorities(
        output_root=tmp_path / "execution",
        planning_artifact_root=tmp_path / "planning",
        plan_bundle_root=tmp_path / "bundle",
        resume=False,
    )

    assert set(authorities) == {"full", "filtered"}
    full = authorities["full"]
    filtered = authorities["filtered"]
    assert type(full) is type(filtered) is ResultsFirstExecutionAuthority
    assert type(full.policy) is ResultsFirstExecutionPolicy
    assert full.policy is filtered.policy
    assert type(full.full_snapshot) is FormalPlanSnapshot
    assert type(full.full_prepared_inventory) is FormalPreparedRequestInventory
    assert full.full_snapshot is filtered.full_snapshot
    assert full.full_prepared_inventory is filtered.full_prepared_inventory
    assert full.full_budget is filtered.full_budget
    assert type(full.coverage) is type(filtered.coverage) is FormalExecutionCoverage
    assert type(full.execution_budget_projection) is PaperExecutionBudgetProjection
    assert type(filtered.execution_budget_projection) is PaperExecutionBudgetProjection
    assert full.coverage.selection_kind == "full"
    assert filtered.coverage.selection_kind == "filtered"
    assert (
        full.coverage.condition_count,
        full.coverage.root_run_count,
        full.coverage.selected_first_attempt_ai_unit_count,
    ) == (324, 6_384, 40_520)
    assert filtered.coverage.root_run_count == 145
    for authority in authorities.values():
        assert authority.coverage.source_snapshot is full.full_snapshot
        assert authority.execution_budget_projection.source_budget is full.full_budget
        assert authority.execution_budget_projection.coverage is authority.coverage
        assert authority.bundle.coverage_digest == authority.coverage.coverage_digest
        assert authority.bundle.plan_digest.startswith("sha256:")
        assert authority.provider_calls_made == 0
    assert full.bundle.coverage_digest != filtered.bundle.coverage_digest
    assert full.bundle.plan_digest != filtered.bundle.plan_digest
    assert not (tmp_path / "planning" / "full-slots").exists()
    assert not (tmp_path / "planning" / "filtered-slots").exists()
    assert not (tmp_path / "bundle").exists()
    approved = full.ai_api_configs[
        paper_cli.APPROVED_ENDPOINT_BINDINGS_KEY
    ]["exp5_real_ai_model_endpoint_comparison"]
    assert set(approved["member_plans"]) == {
        "glm_5_2_siliconflow",
        "qwen3_14b_siliconflow",
        "minimax_m2_5_siliconflow",
        "deepseek_v3_pro_siliconflow",
    }
    assert full.execution_budget_projection.hard_limits != (
        filtered.execution_budget_projection.hard_limits
    )


def test_legacy_representative_authority_wrapper_delegates_singular_exact_root_builder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = object()
    observed: list[dict[str, object]] = []

    def build(**kwargs: object):
        observed.append(dict(kwargs))
        return sentinel

    monkeypatch.setattr(
        paper_cli,
        "build_results_first_execution_authority_for_selection",
        build,
        raising=False,
    )
    output_root = tmp_path / "exact-output"
    planning_root = tmp_path / "exact-planning"
    bundle_root = tmp_path / "exact-bundle"

    result = paper_cli.build_representative_full_plan_smoke_authority(
        output_root=output_root,
        planning_artifact_root=planning_root,
        plan_bundle_root=bundle_root,
        resume=True,
    )

    assert result is sentinel
    assert observed == [
        {
            "selection": "representative",
            "output_root": output_root,
            "planning_artifact_root": planning_root,
            "plan_bundle_root": bundle_root,
            "resume": True,
        }
    ]


@pytest.mark.parametrize(
    ("selection", "selection_kind"),
    (("full", "full"), ("representative", "filtered")),
)
def test_singular_results_first_builder_preserves_exact_user_roots(
    selection: str,
    selection_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = object()
    observed: list[dict[str, object]] = []

    def build(**kwargs: object):
        observed.append(dict(kwargs))
        return {selection_kind: sentinel}

    monkeypatch.setattr(
        paper_cli,
        "_build_results_first_execution_authorities",
        build,
    )
    output_root = tmp_path / "output"
    planning_root = tmp_path / "planning"
    bundle_root = tmp_path / "bundle"

    result = paper_cli.build_results_first_execution_authority_for_selection(
        selection=selection,
        output_root=output_root,
        planning_artifact_root=planning_root,
        plan_bundle_root=bundle_root,
        resume=True,
    )

    assert result is sentinel
    assert observed == [
        {
            "output_root": output_root,
            "planning_artifact_root": planning_root,
            "plan_bundle_root": bundle_root,
            "resume": True,
            "_selection_kinds": (selection_kind,),
            "_exact_roots": True,
        }
    ]


@pytest.mark.parametrize(
    ("selection", "selection_kind"),
    (
        ("representative_exp1_exp3_exp5", "filtered"),
        ("full_exp1_exp3_exp5", "full"),
    ),
)
def test_exp4_excluded_results_first_selections_are_explicit_and_typed(
    selection: str,
    selection_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """排除 Exp4 只能由两个预注册 selection 进入 authority builder。"""

    sentinel = object()
    observed: list[dict[str, object]] = []

    def build(**kwargs: object):
        observed.append(dict(kwargs))
        return {selection_kind: sentinel}

    monkeypatch.setattr(
        paper_cli,
        "_build_results_first_execution_authorities",
        build,
    )

    result = paper_cli.build_results_first_execution_authority_for_selection(
        selection=selection,
        output_root=tmp_path / "output",
        planning_artifact_root=tmp_path / "planning",
        plan_bundle_root=tmp_path / "bundle",
        resume=False,
    )

    assert result is sentinel
    assert observed == [
        {
            "output_root": tmp_path / "output",
            "planning_artifact_root": tmp_path / "planning",
            "plan_bundle_root": tmp_path / "bundle",
            "resume": False,
            "_selection_kinds": (selection_kind,),
            "_exact_roots": True,
            "_exclude_exp4": True,
        }
    ]


APPROVED_EXP1_PILOT_DIGEST = (
    # Current schema digest for mock-approved CLI routing tests. The historical
    # real pilot approval digest remains immutable in existing evidence.
    "sha256:24a1279b178ff9ba7c6a8f39288310f"
    "3c83b271e7d0a562475746ae05613280d"
)
from tokenshare.experiments.paper_suite_scale import load_paper_suite_scale_profile


@pytest.fixture(autouse=True)
def _use_tracked_lean_environment(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """CLI fixtures bind the tracked checker identity without running Lean."""

    production_dual_domain_tests = {
        "test_dual_domain_smoke_identity_and_plan_freeze_two_canonical_roots",
        (
            "test_dual_domain_smoke_capturing_transport_runs_protocol_"
            "and_lean_checker"
        ),
    }
    node_name = getattr(request.node, "originalname", None) or request.node.name
    if node_name in production_dual_domain_tests:
        # 这些回归必须使用 production catalog/Lean authority/selection；任何
        # autouse 替换都会把 frozen selection 漂移伪装成 smoke 通过。
        return

    repo_root = Path(__file__).resolve().parents[2]
    tracked = json.loads(
        (repo_root / "benchmarks/paper/lean_checker_preflight.v1.json").read_text(
            encoding="utf-8"
        )
    )
    environment = paper_catalog_module._current_lean_environment_manifest_without_preflight()
    object.__setattr__(environment, "environment_digest", tracked["environment_digest"])
    oracle_source = (
        repo_root / "fixtures/lean_proof_project/TokenShare/LemmaGraphOracle.lean"
    ).resolve()
    original_file_digest = paper_catalog_module._file_digest

    def worktree_file_digest(path: Path) -> str:
        resolved = Path(path).resolve()
        if resolved != oracle_source:
            return original_file_digest(resolved)
        source = resolved.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
        return f"sha256:{sha256(source.encode('utf-8')).hexdigest()}"

    monkeypatch.setattr(
        paper_catalog_module,
        "_current_lean_environment_manifest_without_preflight",
        lambda: environment,
    )
    monkeypatch.setattr(
        paper_catalog_module,
        "lean_checker_implementation_digest",
        lambda: tracked["checker_implementation_digest"],
    )
    monkeypatch.setattr(paper_catalog_module, "_file_digest", worktree_file_digest)
    monkeypatch.setattr(
        paper_catalog_module,
        "default_lean_paper_environment_manifest",
        lambda: environment,
    )
    monkeypatch.setattr(
        lean_paper_adapter,
        "default_lean_paper_environment_manifest",
        lambda: environment,
    )
    tracked_readiness = json.loads(
        (repo_root / "benchmarks/paper/lean_task14_3x3_readiness.v1.json").read_text(
            encoding="utf-8"
        )
    )
    def tracked_matrix_plan(*, catalog_manifest, **_kwargs):
        matrix = paper_runner._bind_lean_matrix_to_catalog(
            deepcopy(tracked_readiness),
            catalog_manifest=catalog_manifest,
        )
        matrix["schema_version"] = "tokenshare.lean_3x3_matrix_plan.v1"
        matrix_digest = digest_json(paper_runner._lean_matrix_digest_body(matrix))
        matrix["matrix_digest"] = matrix_digest
        matrix["task15_budget_input"]["matrix_digest"] = matrix_digest
        return matrix

    monkeypatch.setattr(
        paper_cli,
        "build_lean_3x3_matrix_plan",
        tracked_matrix_plan,
    )
    scale_profile = load_paper_suite_scale_profile(
        "benchmarks/paper/paper_suite_scale_profile.v1.json"
    )
    build_dispatch_plans = paper_cli.build_gate_c_dispatch_plans
    execute_pilot_case = paper_cli.execute_gate_c_pilot_case

    def build_test_dispatch_plans(**kwargs):
        kwargs.setdefault("paper_suite_scale_profile", scale_profile)
        return build_dispatch_plans(**kwargs)

    def execute_test_pilot_case(**kwargs):
        kwargs.setdefault("paper_suite_scale_profile", scale_profile)
        return execute_pilot_case(**kwargs)

    # 双域 smoke 回归必须验证 profile 指定的真实 canonical case_id；通用
    # 测试缩容夹具会在 selection 漂移时替换 profile case，因此这两个定向测试
    # 保留 production full-plan resolver，直接证明冻结 root identity。
    use_full_canonical_smoke_plan = request.node.name in {
        "test_dual_domain_smoke_identity_and_plan_freeze_two_canonical_roots",
        (
            "test_dual_domain_smoke_capturing_transport_runs_protocol_"
            "and_lean_checker"
        ),
    }
    if not use_full_canonical_smoke_plan:
        monkeypatch.setattr(
            paper_cli,
            "build_gate_c_dispatch_plans",
            build_test_dispatch_plans,
        )
        monkeypatch.setattr(
            paper_cli,
            "execute_gate_c_pilot_case",
            execute_test_pilot_case,
        )
    resolve_smoke_plan = paper_cli.resolve_paper_smoke_execution_plan

    def resolve_test_smoke_plan(**kwargs):
        profile = kwargs["profile"]
        plans_by_experiment = {
            plan.experiment_id: plan for plan in kwargs["dispatch_plans"]
        }
        adapted_items = []
        for item in profile.items:
            plan = plans_by_experiment[item.experiment_id]
            matches = [
                (condition, selection)
                for condition, selection in plan.bound_items()
                if condition.repeat_id == item.repeat_id
                and paper_smoke._condition_matches(
                    condition, item.condition_selector
                )
            ]
            if len(matches) != 1:
                adapted_items.append(item)
                continue
            _condition, selection = matches[0]
            case_id = (
                item.case_id
                if item.case_id in selection.ordered_case_ids
                else selection.ordered_case_ids[0]
            )
            adapted_items.append(replace(item, case_id=case_id))
        adapted_profile = replace(profile, items=tuple(adapted_items))
        result = resolve_smoke_plan(**{**kwargs, "profile": adapted_profile})
        return replace(result, profile_digest=profile.profile_digest)

    if not use_full_canonical_smoke_plan:
        monkeypatch.setattr(
            paper_cli,
            "resolve_paper_smoke_execution_plan",
            resolve_test_smoke_plan,
        )
    validate_disk_estimate = formal_runner._validated_formal_disk_estimate

    def validate_test_disk_estimate(budget):
        quota = deepcopy(budget.quota_preflight)
        quota["budget_commitments"]["request_limits"][
            "token_upper_bound_per_provider_attempt"
        ] = budget.disk_estimate["inputs"]["max_tokens"]
        return validate_disk_estimate(replace(budget, quota_preflight=quota))

    monkeypatch.setattr(
        formal_runner,
        "_validated_formal_disk_estimate",
        validate_test_disk_estimate,
    )
    if "disk_" not in request.node.name:
        monkeypatch.setattr(
            formal_runner,
            "_preflight_formal_disk_capacity",
            lambda **_kwargs: {"condition_compaction_bytes": 0},
        )


@pytest.mark.parametrize(
    ("status", "expected_exit_code"),
    (
        (PaperStatus.COMPLETED, 0),
        (PaperStatus.COMPLETED_WITH_FAILURES, 0),
        (PaperStatus.BLOCKED, 3),
        (PaperStatus.FAILED, 3),
        (PaperStatus.BUDGET_EXHAUSTED, 3),
        (PaperStatus.INCOMPLETE, 3),
    ),
)
def test_execution_result_exit_code_preserves_terminal_semantics(
    status: PaperStatus,
    expected_exit_code: int,
) -> None:
    result = SimpleNamespace(to_dict=lambda: {"status": status.value})

    assert paper_cli._execution_result_exit_code(result) == expected_exit_code


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
    assert budget["planned_root_runs"] == 435
    assert budget["planned_ai_units"] == 1_970
    assert budget["token_upper_bound"] == 599_069_120
    assert budget["cost_upper_bound"] == pytest.approx(98.5)
    assert budget["quota_preflight"]["budget_commitments"]["request_limits"][
        "timeout_seconds"
    ] == 600
    dispatch = json.loads(
        (tmp_path / "paper_dispatch_plans.json").read_text(encoding="utf-8")
    )
    assert dispatch["provider_calls_made"] == 0
    assert dispatch["plans"][0]["condition_count"] == 12


def test_paper_cli_rejects_exp3_before_exp1_without_consuming_output_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_root = tmp_path / "reversed-exp3-exp1"
    monkeypatch.setattr(
        paper_cli,
        "expand_plan_conditions",
        lambda **_kwargs: pytest.fail("dependency order must fail before planning"),
    )

    exit_code = main(
        [
            "--output-root",
            str(output_root),
            "--experiments",
            "exp3,exp1",
            "--plan-only",
        ]
    )

    assert exit_code == 3
    assert not output_root.exists()
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "blocked"
    assert result["failure_kind"] == "invalid_experiment_dependency_order"
    assert result["provider_calls_made"] == 0


def test_shared_exp1_output_identity_requires_dependency_order() -> None:
    identity = paper_cli._shared_exp1_reference_output_identity(
        (
            "exp3_real_ai_fault_recovery",
            "exp1_real_ai_feasibility",
        )
    )

    assert identity["cross_experiment_evidence_allowed"] is False
    assert identity["cross_experiment_evidence_policy"][
        "dependency_order_valid"
    ] is False


def test_exp5_v3_cli_plan_only_uses_endpoint_budget_identity_without_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preflight = _exp5_v3_cli_preflight()
    monkeypatch.setattr(
        paper_cli,
        "_model_endpoint_cohort_preflight_for_suite",
        lambda **_kwargs: (
            preflight,
            {"cohort_id": PAPER_MODEL_ENDPOINT_COHORT_V3_ID},
        ),
    )
    transport_calls: list[dict] = []

    def transport(**kwargs):
        transport_calls.append(kwargs)
        raise AssertionError("plan-only must not call the provider")

    exit_code = main(
        [
            "--output-root",
            str(tmp_path),
            "--experiments",
            "exp5",
            "--plan-only",
        ],
        gate_c_transport=transport,
    )

    suite = json.loads(
        (tmp_path / "suite_manifest.json").read_text(encoding="utf-8")
    )
    budget = json.loads(
        (tmp_path / "run_budget.json").read_text(encoding="utf-8")
    )
    dispatch = json.loads(
        (tmp_path / "paper_dispatch_plans.json").read_text(encoding="utf-8")
    )
    assert exit_code == 0
    assert transport_calls == []
    assert suite["status"] == "planned"
    assert suite["condition_count"] == 48
    assert suite["run_count"] == budget["planned_root_runs"] == 648
    assert suite["provider_attempt_count"] == 0
    assert budget["planned_conditions"] == 48
    assert budget["planned_root_runs"] == 648
    assert budget["planned_ai_units"] == 4_992
    assert budget["max_provider_attempts"] == 4_992
    assert budget["token_upper_bound"] == 306_708_480
    assert budget["quota_preflight"]["provider_calls_made"] == 0
    token_bounds = budget["quota_preflight"]["token_upper_bound_by_member"]
    assert set(token_bounds) == set(PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS)
    assert sum(token_bounds.values()) == budget["token_upper_bound"]
    assert dispatch["provider_calls_made"] == 0
    assert dispatch["plans"][0]["condition_count"] == 48


def test_authoritative_exp1_to_exp5_plan_only_disk_forecast_fits_439_85_gib(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preflight = _exp5_v3_cli_preflight()
    monkeypatch.setattr(
        paper_cli,
        "_model_endpoint_cohort_preflight_for_suite",
        lambda **_kwargs: (
            preflight,
            {"cohort_id": PAPER_MODEL_ENDPOINT_COHORT_V3_ID},
        ),
    )
    provider_calls: list[dict] = []

    def forbidden_transport(**kwargs):
        provider_calls.append(kwargs)
        raise AssertionError("authoritative plan-only must not call provider")

    exit_code = main(
        [
            "--output-root",
            str(tmp_path),
            "--experiments",
            "exp1,exp2,exp3,exp4,exp5",
            "--plan-only",
        ],
        gate_c_transport=forbidden_transport,
    )

    budget = json.loads(
        (tmp_path / "run_budget.json").read_text(encoding="utf-8")
    )
    assert exit_code == 0
    assert provider_calls == []
    assert budget["planned_root_runs"] == 6_384
    assert budget["planned_ai_units"] == 40_520
    assert budget["max_provider_attempts"] == 81_272
    assert budget["token_upper_bound"] == 23_503_151_360
    assert budget["cost_upper_bound"] == pytest.approx(7_346.259328)
    experiment_budget = budget["quota_preflight"]["budget_commitments"][
        "experiment_budget_identity"
    ]
    assert experiment_budget["actual_scheduled_root_runs_by_experiment"] == {
        "exp1_real_ai_feasibility": 435,
        "exp2_real_ai_scalability": 600,
        "exp3_real_ai_fault_recovery": 3_726,
        "exp4_real_ai_protocol_ablation": 975,
        "exp5_real_ai_model_endpoint_comparison": 648,
    }
    assert experiment_budget["planned_first_attempt_ai_units_by_experiment"] == {
        "exp1_real_ai_feasibility": 1_970,
        "exp2_real_ai_scalability": 12_000,
        "exp3_real_ai_fault_recovery": 17_148,
        "exp4_real_ai_protocol_ablation": 4_410,
        "exp5_real_ai_model_endpoint_comparison": 4_992,
    }
    assert experiment_budget["replacement_reserve_by_experiment"] == {
        "exp3_real_ai_fault_recovery": 37_224,
        "exp4_real_ai_protocol_ablation": 3_528,
    }
    inputs = budget["disk_estimate"]["inputs"]
    assert inputs["max_condition_root_runs"] == 100
    assert inputs["max_condition_ai_units"] == 1_000
    assert inputs["max_condition_provider_attempts"] == 1_000
    estimate = budget["disk_estimate"]
    forecast = int(estimate["forecast_bytes"])
    required = (
        forecast
        + max((forecast + 3) // 4, 2 * 1024**3)
        + int(estimate["max_condition_compaction_bytes"])
    )
    assert forecast == 50_206_081_024
    assert required == 63_406_407_680
    assert required < int(439.85 * 1024**3)
    assert int(estimate["theoretical_max_payload_bytes"]) not in {
        forecast,
        required,
    }


def test_exp5_v3_formal_preflight_loads_explicit_smoke_evidence_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle_path = tmp_path / "exp5_endpoint_smoke_evidence.json"
    bundle = {
        "schema_version": "tokenshare.paper_exp5_endpoint_smoke_evidence_bundle.v1",
        "bundle_digest": "sha256:" + "5" * 64,
    }
    cohort = {
        "cohort_id": PAPER_MODEL_ENDPOINT_COHORT_V3_ID,
        "members": [
            {"cohort_member_id": member_id}
            for member_id in PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS
        ],
    }
    captured: dict[str, object] = {}
    monkeypatch.setattr(paper_cli, "load_model_endpoint_cohort", lambda _path: cohort)
    monkeypatch.setattr(paper_cli, "load_model_entry_map", lambda _path: {"members": {}})
    monkeypatch.setattr(paper_cli, "load_provider_config_map", lambda _paths: {})
    monkeypatch.setattr(
        paper_cli,
        "load_exp5_smoke_evidence_bundle",
        lambda path: bundle if Path(path) == bundle_path else pytest.fail("wrong bundle"),
        raising=False,
    )

    def build_preflight(**kwargs):
        captured.update(kwargs)
        return {"status": "planned", "provider_calls_made": 0}

    monkeypatch.setattr(paper_cli, "build_model_endpoint_cohort_preflight", build_preflight)

    preflight, loaded_cohort = paper_cli._model_endpoint_cohort_preflight_for_suite(
        experiment_ids=("exp5_real_ai_model_endpoint_comparison",),
        model_cohort_file="cohort.json",
        model_entry_map="entry-map.json",
        provider_config_args=(),
        smoke_evidence_bundle=bundle_path.as_posix(),
    )

    assert preflight["status"] == "planned"
    assert loaded_cohort == cohort
    assert captured["smoke_evidence_bundle"] == bundle


def test_exp5_v3_blocked_preflight_reports_canonical_member_ids() -> None:
    cohort = {
        "cohort_id": PAPER_MODEL_ENDPOINT_COHORT_V3_ID,
        "members": [
            {"cohort_member_id": member_id}
            for member_id in PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS
        ],
    }

    preflight = paper_cli._blocked_model_endpoint_cohort_preflight(
        message="blocked fixture",
        cohort=cohort,
    )

    assert preflight["expected_member_ids"] == list(
        PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS
    )


def test_blocked_suite_persists_exp5_cohort_preflight_diagnostics(
    tmp_path: Path,
) -> None:
    preflight = paper_cli._blocked_model_endpoint_cohort_preflight(
        message="missing artifact-backed smoke evidence bundle",
        cohort=None,
    )

    paper_cli._write_blocked_suite(
        output_root=tmp_path,
        experiment_ids=("exp5_real_ai_model_endpoint_comparison",),
        failure_kind="gate_c_plan_blocked",
        message="invalid Experiment 5 v3 cohort preflight",
        model_endpoint_cohort_preflight=preflight,
    )

    suite = json.loads(
        (tmp_path / "suite_manifest.json").read_text(encoding="utf-8")
    )
    assert suite["model_endpoint_cohort_preflight"] == preflight
    assert suite["model_endpoint_cohort_preflight"]["expected_member_ids"] == list(
        PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS
    )


def test_completed_exp5_smoke_writes_endpoint_bundle_and_refreshes_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []
    entry_map_path = tmp_path / "entry-map.json"
    entry_map = {"members": {}}
    monkeypatch.setattr(paper_cli, "load_model_entry_map", lambda path: entry_map)

    def write_bundle(**kwargs):
        calls.append(("write", kwargs))
        return {"bundle_digest": "sha256:" + "6" * 64}

    monkeypatch.setattr(
        paper_cli,
        "write_exp5_smoke_evidence_bundle",
        write_bundle,
        raising=False,
    )

    class EvidenceStore:
        def __init__(self, root):
            calls.append(("store", Path(root)))

        def _refresh_evidence_manifest(self):
            calls.append(("refresh", None))

    monkeypatch.setattr(paper_cli, "FormalEvidenceStore", EvidenceStore)
    result = SimpleNamespace(to_dict=lambda: {"status": "completed"})
    profile = SimpleNamespace(suite_id="paper_smoke_exp5_v4")

    bundle = paper_cli._write_completed_exp5_smoke_evidence(
        profile=profile,
        output_root=tmp_path,
        result=result,
        entry_map_path=entry_map_path,
    )

    assert bundle["bundle_digest"] == "sha256:" + "6" * 64
    assert calls[0][0] == "write"
    assert calls[0][1]["source_suite_root"] == tmp_path
    assert calls[0][1]["entry_map"] == entry_map
    assert calls[0][1]["output_path"] == (
        tmp_path / "audit" / "exp5_endpoint_smoke_evidence.json"
    )
    assert calls[-1] == ("refresh", None)


def test_completed_with_failures_exp5_smoke_still_writes_endpoint_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_map_path = tmp_path / "entry-map.json"
    monkeypatch.setattr(
        paper_cli,
        "load_model_entry_map",
        lambda _path: {"members": {}},
    )
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        paper_cli,
        "write_exp5_smoke_evidence_bundle",
        lambda **kwargs: calls.append(kwargs) or {"bundle_digest": "sha256:" + "6" * 64},
    )

    class EvidenceStore:
        def __init__(self, _root):
            pass

        def _refresh_evidence_manifest(self):
            pass

    monkeypatch.setattr(paper_cli, "FormalEvidenceStore", EvidenceStore)

    bundle = paper_cli._write_completed_exp5_smoke_evidence(
        profile=SimpleNamespace(suite_id="paper_smoke_exp5_v4"),
        output_root=tmp_path,
        result=SimpleNamespace(to_dict=lambda: {"status": "completed_with_failures"}),
        entry_map_path=entry_map_path,
    )

    assert bundle is not None
    assert len(calls) == 1


def test_exp5_standalone_smoke_identity_injects_local_key_without_prior_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "local-exp5-smoke-test-secret"
    local_config_body = json.loads(
        Path(
            "benchmarks/paper/exp5_siliconflow_provider_config.v3.json"
        ).read_text(encoding="utf-8")
    )
    for index, entry in enumerate(local_config_body["entries"]):
        entry["enabled"] = index == 0
        if index == 0:
            entry["api_key_env"] = "TOKENSHARE_TEST_LOCAL_EXP5_KEY"
            entry["api_key"] = secret
    local_config = tmp_path / "ai_api_smoke.local.json"
    local_config.write_text(
        json.dumps(local_config_body, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    output_root = tmp_path / "identity-only-must-not-exist"
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)

    exit_code = main(
        [
            "--smoke-profile",
            "benchmarks/paper/paper_smoke_exp5_profile.v4.json",
            "--output-root",
            str(output_root),
            "--model-cohort-file",
            "benchmarks/paper/model_comparison_cohort.v3.json",
            "--model-entry-map",
            "benchmarks/paper/model_comparison_entry_map.v3.json",
            "--provider-config",
            "siliconflow=benchmarks/paper/exp5_siliconflow_provider_config.v3.json",
            "--local-ai-api-config",
            str(local_config),
            "--unlimited-budget",
            "--smoke-identity-only",
        ]
    )

    identity = json.loads(capsys.readouterr().out)
    semantics = identity["preregistered_semantics"]
    assert exit_code == 0
    assert not output_root.exists()
    assert os.environ["SILICONFLOW_API_KEY"] == secret
    assert semantics["suite_id"] == "paper_smoke_exp5_v4"
    assert semantics["experiment_ids"] == [
        "exp5_real_ai_model_endpoint_comparison"
    ]
    assert semantics["direct_root_runs"] == 8
    assert "model_endpoint" not in semantics
    assert "request_limits" not in semantics
    cohort_identity = semantics["model_endpoint_cohort"]
    assert cohort_identity["cohort_id"] == PAPER_MODEL_ENDPOINT_COHORT_V3_ID
    assert cohort_identity["model_cohort_digest"] == (
        "sha256:1b317c9827d87c43b974b77c469b115906da463a79064d3ffb3b949dc5257942"
    )
    assert [
        member["cohort_member_id"]
        for member in cohort_identity["member_endpoints"]
    ] == list(PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS)
    assert {
        member["provider_model_id"]
        for member in cohort_identity["member_endpoints"]
    } == {
        "zai-org/GLM-5.2",
        "Qwen/Qwen3-14B",
        "MiniMaxAI/MiniMax-M2.5",
        "Pro/deepseek-ai/DeepSeek-V3",
    }
    assert all(
        member["source_provider_config_digest"]
        == "sha256:5135c1c2da0f7512b9891f4ea35c65891044dab544d7ad01230ef1db43ff4b5d"
        for member in cohort_identity["member_endpoints"]
    )


def test_exp5_standalone_execution_does_not_require_unused_exp1_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "local-exp5-execution-test-secret"
    local_config_body = json.loads(
        Path(
            "benchmarks/paper/exp5_siliconflow_provider_config.v3.json"
        ).read_text(encoding="utf-8")
    )
    for index, entry in enumerate(local_config_body["entries"]):
        entry["enabled"] = index == 0
        if index == 0:
            entry["api_key_env"] = "TOKENSHARE_TEST_LOCAL_EXP5_EXEC_KEY"
            entry["api_key"] = secret
    local_config = tmp_path / "ai_api_smoke.local.json"
    local_config.write_text(
        json.dumps(local_config_body, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    captured: dict[str, object] = {}

    def capture_smoke_suite(**kwargs):
        captured.update(kwargs)
        return _smoke_result(
            kwargs["execution_plan"],
            kwargs["budget"],
            status=PaperStatus.COMPLETED_WITH_FAILURES,
        )

    monkeypatch.setattr(
        paper_cli,
        "execute_paper_smoke_suite",
        capture_smoke_suite,
    )
    smoke_evidence_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        paper_cli,
        "_write_completed_exp5_smoke_evidence",
        lambda **kwargs: smoke_evidence_calls.append(kwargs),
    )
    output_root = tmp_path / "exp5-only-execution"
    exit_code = main(
        [
            "--smoke-profile",
            "benchmarks/paper/paper_smoke_exp5_profile.v4.json",
            "--output-root",
            str(output_root),
            "--model-cohort-file",
            "benchmarks/paper/model_comparison_cohort.v3.json",
            "--model-entry-map",
            "benchmarks/paper/model_comparison_entry_map.v3.json",
            "--provider-config",
            "siliconflow=benchmarks/paper/exp5_siliconflow_provider_config.v3.json",
            "--local-ai-api-config",
            str(local_config),
            "--unlimited-budget",
            "--real-transport",
        ],
        gate_c_transport=SimpleNamespace(),
    )

    assert exit_code == 0
    assert "DEEPSEEK_API_KEY" not in os.environ
    assert os.environ["SILICONFLOW_API_KEY"] == secret
    assert captured["execution_plan"].direct_root_run_count == 8
    assert len(smoke_evidence_calls) == 1
    assert smoke_evidence_calls[0]["output_root"] == output_root


def test_exp5_standalone_prelaunch_failure_persists_blocked_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "local-exp5-blocked-evidence-test-secret"
    local_config_body = json.loads(
        Path(
            "benchmarks/paper/exp5_siliconflow_provider_config.v3.json"
        ).read_text(encoding="utf-8")
    )
    for index, entry in enumerate(local_config_body["entries"]):
        entry["enabled"] = index == 0
        if index == 0:
            entry["api_key_env"] = "TOKENSHARE_TEST_LOCAL_EXP5_BLOCKED_KEY"
            entry["api_key"] = secret
    local_config = tmp_path / "ai_api_smoke.local.json"
    local_config.write_text(
        json.dumps(local_config_body, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    monkeypatch.setattr(
        paper_cli,
        "execute_paper_smoke_suite",
        lambda **_kwargs: (_ for _ in ()).throw(
            ValueError("prelaunch diagnostic failure")
        ),
    )
    output_root = tmp_path / "exp5-prelaunch-failure"

    exit_code = main(
        [
            "--smoke-profile",
            "benchmarks/paper/paper_smoke_exp5_profile.v4.json",
            "--output-root",
            str(output_root),
            "--model-cohort-file",
            "benchmarks/paper/model_comparison_cohort.v3.json",
            "--model-entry-map",
            "benchmarks/paper/model_comparison_entry_map.v3.json",
            "--provider-config",
            "siliconflow=benchmarks/paper/exp5_siliconflow_provider_config.v3.json",
            "--local-ai-api-config",
            str(local_config),
            "--unlimited-budget",
            "--real-transport",
        ],
        gate_c_transport=SimpleNamespace(),
    )

    result = json.loads(capsys.readouterr().out)
    suite = json.loads(
        (output_root / "suite_manifest.json").read_text(encoding="utf-8")
    )
    assert exit_code == 3
    assert result == {
        "failure_kind": "smoke_execution_failed",
        "message": "prelaunch diagnostic failure",
        "status": "failed",
    }
    assert suite["status"] == "blocked"
    assert suite["provider_attempt_count"] == 0
    assert suite["error_summary"] == [
        {
            "failure_kind": "smoke_execution_failed",
            "message": "prelaunch diagnostic failure",
        }
    ]


def test_exp5_v3_smoke_cli_serializes_arms_and_shares_three_slot_namespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preflight = _exp5_v3_cli_preflight()
    monkeypatch.setattr(
        paper_cli,
        "_model_endpoint_cohort_preflight_for_suite",
        lambda **_kwargs: (
            preflight,
            {"cohort_id": PAPER_MODEL_ENDPOINT_COHORT_V3_ID},
        ),
    )
    full_profile = paper_cli.load_paper_smoke_profile(
        "benchmarks/paper/paper_smoke_profile.v3.json"
    )
    exp5_experiment_id = "exp5_real_ai_model_endpoint_comparison"
    profile = replace(
        full_profile,
        suite_id="paper_smoke_v3_exp5_concurrency_test",
        experiment_ids=(exp5_experiment_id,),
        expected_root_runs=8,
        items=tuple(
            item
            for item in full_profile.items
            if item.experiment_id == exp5_experiment_id
        ),
    )
    monkeypatch.setattr(
        paper_cli,
        "load_paper_smoke_profile",
        lambda _path: profile,
    )
    baseline = load_exp1_pilot_profile(
        "benchmarks/paper/exp1_minimal_pilot_profile.v3.json"
    )
    transport = _ConcurrentSmokeTransport()
    expected_member_order = list(PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS)
    observed: dict[str, object] = {}

    def capture_smoke_suite(**kwargs):
        exp5_plan = next(
            plan
            for plan in kwargs["execution_plan"].dispatch_plans
            if plan.experiment_id
            == "exp5_real_ai_model_endpoint_comparison"
        )
        conditions = tuple(exp5_plan.conditions)
        observed["execution_plan_digest"] = (
            kwargs["execution_plan"].execution_plan_digest
        )
        observed["condition_ids"] = [
            condition.condition_id for condition in conditions
        ]
        assert [condition.cohort_member_id for condition in conditions] == [
            member_id
            for member_id in expected_member_order
            for _ in range(2)
        ]
        assert all(condition.worker_count == 3 for condition in conditions)
        assert {condition.provider_config_id for condition in conditions} == {
            "executor_ai_api_exp5_siliconflow_v3"
        }
        wrapped_transport = kwargs["transport"]
        for member_id in expected_member_order:
            member_plan = preflight["member_plans"][member_id]
            entry = SimpleNamespace(
                entry_id=member_plan["selected_entry_id"],
                provider="siliconflow",
                model=member_plan["provider_model_id"],
                base_url="https://api.example.test/v1",
                endpoint="/chat/completions",
            )
            with ThreadPoolExecutor(max_workers=6) as executor:
                futures = [
                    executor.submit(
                        wrapped_transport.post_chat_completion,
                        api_key="test-double-only",
                        **prepared_wire_kwargs(
                            entry,
                            {
                                "member_id": member_id,
                                "call_index": index,
                                "model": entry.model,
                                "messages": [],
                            },
                            provider_family="siliconflow",
                        ),
                        timeout_seconds=1,
                    )
                    for index in range(6)
                ]
                for future in futures:
                    future.result()
        execution_plan = kwargs["execution_plan"]
        return PaperSuiteResult(
            suite_id=execution_plan.suite_id,
            status=PaperStatus.COMPLETED,
            output_root=execution_plan.output_root,
            started_at="2026-07-30T00:00:00Z",
            ended_at="2026-07-30T00:00:01Z",
            experiment_ids=execution_plan.experiment_ids,
            condition_count=len(execution_plan.items),
            run_count=execution_plan.direct_root_run_count,
            task_count=execution_plan.direct_root_run_count,
            provider_attempt_count=len(transport.calls),
            total_tokens=0,
            total_cost_estimate=0.0,
            paper_eligible=False,
            eligibility_report_ref=None,
            budget_ref={"budget_digest": kwargs["budget"].budget_digest},
            metrics_refs=(),
            audit_refs=(),
            error_summary=(),
        )

    monkeypatch.setattr(
        paper_cli,
        "execute_paper_smoke_suite",
        capture_smoke_suite,
    )
    exit_code = main(
        [
            "--output-root",
            str(tmp_path),
            "--smoke-profile",
            "benchmarks/paper/paper_smoke_profile.v3.json",
            "--real-transport",
            "--unlimited-budget",
        ],
        gate_c_transport=transport,
        gate_c_ai_api_configs={
            baseline.model_endpoint_identity.provider_config_id: (
                baseline.source_provider_config
            )
        },
    )

    evidence = json.loads(
        (tmp_path / "audit" / "exp5_v3_execution_schedule.json").read_text(
            encoding="utf-8"
        )
    )
    assert exit_code == 0
    assert transport.provider_calls_made == 24
    assert transport.observed_peak == 3
    assert evidence["provider_namespace"] == (
        "executor_ai_api_exp5_siliconflow_v3"
    )
    assert evidence["schema_version"] == (
        "tokenshare.paper_exp5_v3_execution_schedule.v2"
    )
    assert evidence["profile_digest"] == profile.profile_digest
    assert evidence["execution_plan_digest"] == observed[
        "execution_plan_digest"
    ]
    assert evidence["expected_condition_ids"] == observed["condition_ids"]
    assert evidence["max_in_flight_global"] == 3
    assert evidence["observed_peak"] == 3
    assert evidence["expected_member_order"] == expected_member_order
    assert evidence["observed_member_order"] == expected_member_order
    assert evidence["sequence_plan_digest"] == (
        "sha256:de9271732513d7d2ab20624d2949af3c9230b9adc2cd8db4b1f1ddb0d57b58cb"
    )
    assert evidence["overlap_violation"] is False
    assert all(
        window["started_at"] <= window["ended_at"]
        and type(window["started_tick_ns"]) is int
        and type(window["ended_tick_ns"]) is int
        and window["started_tick_ns"] < window["ended_tick_ns"]
        and type(window["observed_peak"]) is int
        for window in evidence["arm_windows"]
    )
    assert evidence["sequence_complete"] is True
    assert evidence["prefix_valid"] is True
    assert [
        segment["provider_calls_made"]
        for segment in evidence["execution_segments"]
    ] == [24]
    manifest = json.loads(
        (tmp_path / "evidence_manifest.json").read_text(encoding="utf-8")
    )
    assert "audit/exp5_v3_execution_schedule.json" in {
        item["path"] for item in manifest["files"]
    }


def test_exp5_v3_provider_router_uses_explicit_entry_family_binding() -> None:
    config_body = json.loads(
        Path("benchmarks/paper/exp5_siliconflow_provider_config.v3.json").read_text(
            encoding="utf-8"
        )
    )
    entries = load_ai_api_config(config_body).entries
    expected_response = object()
    observed: list[dict[str, object]] = []

    class _FakeSiliconFlowTransport:
        def post_chat_completion(self, **kwargs):
            observed.append(
                {
                    "api_key": kwargs["api_key"],
                    "body_bytes": kwargs["body_bytes"],
                    "normalized_absolute_endpoint": kwargs[
                        "normalized_absolute_endpoint"
                    ],
                    "timeout_seconds": kwargs["timeout_seconds"],
                }
            )
            return expected_response

    router = paper_cli._ProviderFamilyTransportRouter(
        provider_family_by_entry_id={
            entry.entry_id: "siliconflow" for entry in entries
        }
    )
    router._transports["siliconflow"] = _FakeSiliconFlowTransport()
    assert len(entries) == 4

    for index, entry in enumerate(entries):
        body = {"messages": [{"role": "user", "content": entry.model}]}
        response = router.tokenshare_transport_for_provider(
            "siliconflow"
        ).post_chat_completion(
            api_key=f"process-only-test-key-{index}",
            **prepared_wire_kwargs(entry, {**body, "model": entry.model}, provider_family="siliconflow"),
            timeout_seconds=17 + index,
        )

        assert response is expected_response
        assert observed[index] == {
            "api_key": f"process-only-test-key-{index}",
            "body_bytes": prepared_wire_kwargs(
                entry, {**body, "model": entry.model}, provider_family="siliconflow"
            )["body_bytes"],
            "normalized_absolute_endpoint": prepared_wire_kwargs(
                entry, {**body, "model": entry.model}, provider_family="siliconflow"
            )["normalized_absolute_endpoint"],
            "timeout_seconds": 17 + index,
        }


def test_exp5_v3_default_router_routes_mixed_approved_execution_configs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exp1_config = load_ai_api_config(
        json.loads(
            Path("benchmarks/paper/exp1_baseline_provider_config.v3.json").read_text(
                encoding="utf-8"
            )
        )
    )
    exp5_config = load_ai_api_config(
        json.loads(
            Path("benchmarks/paper/exp5_siliconflow_provider_config.v3.json").read_text(
                encoding="utf-8"
            )
        )
    )
    preflight = _exp5_v3_cli_preflight()
    for member_plan in preflight["member_plans"].values():
        member_plan["selected_entry_id"] = next(
            entry.entry_id
            for entry in exp5_config.entries
            if entry.model == member_plan["provider_model_id"]
        )
    member_ids = list(PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS)
    conditions = [
        SimpleNamespace(
            cohort_member_id=member_id,
            condition_id=f"condition-{member_id}-{repeat_index}",
        )
        for member_id in member_ids
        for repeat_index in range(2)
    ]
    profile = SimpleNamespace(
        profile_digest="sha256:" + "1" * 64,
        exp5_v3_contract={
            "repeat_member_order": {"0": member_ids},
            "sequence_plan_digest": "sha256:" + "2" * 64,
            "provider_config_id": "executor_ai_api_exp5_siliconflow_v3",
            "max_in_flight_global": 3,
        },
    )
    execution_plan = SimpleNamespace(
        execution_plan_digest="sha256:" + "3" * 64,
        dispatch_plans=[
            SimpleNamespace(
                experiment_id="exp5_real_ai_model_endpoint_comparison",
                conditions=conditions,
            )
        ],
    )
    deepseek_calls: list[object] = []
    siliconflow_calls: list[object] = []

    class _FakeTransport:
        def __init__(self, calls: list[object]) -> None:
            self._calls = calls

        def post_chat_completion(self, **kwargs):
            self._calls.append(kwargs)
            return object()

    monkeypatch.setattr(
        paper_cli,
        "UrlLibDeepSeekTransport",
        lambda: _FakeTransport(deepseek_calls),
    )
    monkeypatch.setattr(
        paper_cli,
        "UrlLibSiliconFlowTransport",
        lambda: _FakeTransport(siliconflow_calls),
    )

    transport, limiter = paper_cli._exp5_v3_smoke_execution_transport(
        profile=profile,
        execution_plan=execution_plan,
        model_endpoint_cohort_preflight=preflight,
        transport=None,
        ai_api_configs={
            "exp1-baseline": exp1_config,
            "exp5-cohort": exp5_config,
            paper_cli.APPROVED_ENDPOINT_BINDINGS_KEY: {"ignored": "sentinel"},
        },
    )
    baseline_entry = exp1_config.entries[0]
    exp5_entry = exp5_config.entries[0]

    transport.tokenshare_transport_for_provider("deepseek").post_chat_completion(
        api_key="deepseek-key",
        **prepared_wire_kwargs(
            baseline_entry,
            {"model": baseline_entry.model, "messages": []},
            provider_family="deepseek",
        ),
        timeout_seconds=600,
    )
    transport.tokenshare_transport_for_provider("siliconflow").post_chat_completion(
        api_key="siliconflow-key",
        **prepared_wire_kwargs(
            exp5_entry,
            {"model": exp5_entry.model, "messages": []},
            provider_family="siliconflow",
        ),
        timeout_seconds=600,
    )

    assert len(deepseek_calls) == 1
    assert len(siliconflow_calls) == 1
    assert limiter is transport
    assert limiter.new_provider_calls_made == 1


def test_exp5_v3_complete_resume_reuses_canonical_schedule_without_rewrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile, preflight, baseline = _install_exp5_v3_cli_test_profile(monkeypatch)
    transport = _ConcurrentSmokeTransport()
    invocation_count = 0

    def capture_smoke_suite(**kwargs):
        nonlocal invocation_count
        invocation_count += 1
        if invocation_count == 1:
            _call_exp5_members(
                transport=kwargs["transport"],
                preflight=preflight,
                member_ids=PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS,
            )
        return _completed_smoke_result(kwargs["execution_plan"], kwargs["budget"])

    monkeypatch.setattr(
        paper_cli,
        "execute_paper_smoke_suite",
        capture_smoke_suite,
    )
    monkeypatch.setattr(
        paper_cli,
        "_restore_smoke_resume_dispatch_views",
        lambda *, execution_plan, **_kwargs: execution_plan,
    )
    monkeypatch.setattr(
        paper_cli,
        "_restore_smoke_resume_budget",
        lambda *, budget, **_kwargs: budget,
    )
    monkeypatch.setattr(
        paper_cli,
        "_resolve_smoke_invocation_manifests",
        lambda **_kwargs: ({"schema_version": "launch.test.v1"}, None),
    )

    first_exit = _invoke_exp5_v3_cli(
        tmp_path=tmp_path,
        transport=transport,
        baseline=baseline,
        resume=False,
    )
    schedule_path = tmp_path / "audit" / "exp5_v3_execution_schedule.json"
    before = schedule_path.read_bytes()
    calls_before = transport.provider_calls_made

    resume_exit = _invoke_exp5_v3_cli(
        tmp_path=tmp_path,
        transport=transport,
        baseline=baseline,
        resume=True,
    )

    assert first_exit == 0
    assert resume_exit == 0
    assert invocation_count == 2
    assert transport.provider_calls_made == calls_before
    assert schedule_path.read_bytes() == before


def test_exp5_v3_hard_limit_resume_without_calls_preserves_valid_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _profile, preflight, baseline = _install_exp5_v3_cli_test_profile(monkeypatch)
    transport = _ConcurrentSmokeTransport()
    invocation_count = 0

    def capture_smoke_suite(**kwargs):
        nonlocal invocation_count
        invocation_count += 1
        if invocation_count == 1:
            _call_exp5_members(
                transport=kwargs["transport"],
                preflight=preflight,
                member_ids=PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS[:2],
            )
        return _smoke_result(
            kwargs["execution_plan"],
            kwargs["budget"],
            status=PaperStatus.BUDGET_EXHAUSTED,
        )

    monkeypatch.setattr(
        paper_cli,
        "execute_paper_smoke_suite",
        capture_smoke_suite,
    )
    monkeypatch.setattr(
        paper_cli,
        "_restore_smoke_resume_dispatch_views",
        lambda *, execution_plan, **_kwargs: execution_plan,
    )
    monkeypatch.setattr(
        paper_cli,
        "_restore_smoke_resume_budget",
        lambda *, budget, **_kwargs: budget,
    )
    monkeypatch.setattr(
        paper_cli,
        "_resolve_smoke_invocation_manifests",
        lambda **_kwargs: ({"schema_version": "launch.test.v1"}, None),
    )

    first_exit = _invoke_exp5_v3_cli(
        tmp_path=tmp_path,
        transport=transport,
        baseline=baseline,
        resume=False,
    )
    schedule_path = tmp_path / "audit" / "exp5_v3_execution_schedule.json"
    before = schedule_path.read_bytes()
    calls_before = transport.provider_calls_made
    resume_exit = _invoke_exp5_v3_cli(
        tmp_path=tmp_path,
        transport=transport,
        baseline=baseline,
        resume=True,
    )

    evidence = json.loads(before.decode("utf-8"))
    assert first_exit == 3
    assert resume_exit == 3
    assert evidence["observed_member_order"] == list(
        PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS[:2]
    )
    assert evidence["sequence_complete"] is False
    assert transport.provider_calls_made == calls_before == 2
    assert schedule_path.read_bytes() == before


def test_exp5_v3_partial_resume_merges_same_arm_suffix_without_fake_calls(
    tmp_path: Path,
) -> None:
    binding, member_id_by_entry_id = _exp5_v3_schedule_test_binding()
    delegate = _ConcurrentSmokeTransport()
    first = paper_cli._Exp5V3SharedProviderTransport(
        delegate=delegate,
        member_id_by_entry_id=member_id_by_entry_id,
        schedule_binding=binding,
    )
    _call_bound_members(first, member_id_by_entry_id, ("member-a", "member-b"))
    paper_cli._write_exp5_v3_execution_schedule(
        output_root=tmp_path,
        limiter=first,
    )
    prior = paper_cli._load_exp5_v3_execution_schedule(
        output_root=tmp_path,
        schedule_binding=binding,
    )

    resumed = paper_cli._Exp5V3SharedProviderTransport(
        delegate=delegate,
        member_id_by_entry_id=member_id_by_entry_id,
        schedule_binding=binding,
        prior_evidence=prior,
    )
    _call_bound_members(
        resumed,
        member_id_by_entry_id,
        ("member-b", "member-c", "member-d"),
    )
    evidence = paper_cli._write_exp5_v3_execution_schedule(
        output_root=tmp_path,
        limiter=resumed,
    )

    assert evidence["observed_member_order"] == [
        "member-a",
        "member-b",
        "member-c",
        "member-d",
    ]
    assert evidence["sequence_complete"] is True
    assert evidence["provider_calls_made"] == 5
    assert [
        segment["provider_calls_made"]
        for segment in evidence["execution_segments"]
    ] == [2, 3]
    assert sum(
        segment["provider_calls_made"]
        for segment in evidence["execution_segments"]
    ) == evidence["provider_calls_made"]
    member_b = next(
        window
        for window in evidence["arm_windows"]
        if window["cohort_member_id"] == "member-b"
    )
    assert member_b["provider_calls_made"] == 2


@pytest.mark.parametrize(
    "tamper_kind",
    ("overlap", "peak", "order", "condition_binding"),
)
def test_exp5_v3_resume_recomputes_schedule_and_rejects_tampered_prefix(
    tmp_path: Path,
    tamper_kind: str,
) -> None:
    binding, member_id_by_entry_id = _exp5_v3_schedule_test_binding()
    limiter = paper_cli._Exp5V3SharedProviderTransport(
        delegate=_ConcurrentSmokeTransport(),
        member_id_by_entry_id=member_id_by_entry_id,
        schedule_binding=binding,
    )
    _call_bound_members(limiter, member_id_by_entry_id, ("member-a", "member-b"))
    paper_cli._write_exp5_v3_execution_schedule(
        output_root=tmp_path,
        limiter=limiter,
    )
    schedule_path = tmp_path / "audit" / "exp5_v3_execution_schedule.json"
    tampered = json.loads(schedule_path.read_text(encoding="utf-8"))
    if tamper_kind == "overlap":
        tampered["execution_segments"][0]["arm_windows"][1][
            "started_tick_ns"
        ] = tampered["execution_segments"][0]["arm_windows"][0][
            "ended_tick_ns"
        ] - 1
        tampered["overlap_violation"] = False
    elif tamper_kind == "peak":
        tampered["execution_segments"][0]["arm_windows"][0][
            "observed_peak"
        ] = 4
        tampered["limit_violation"] = False
    elif tamper_kind == "order":
        tampered["execution_segments"][0]["arm_windows"].reverse()
        tampered["prefix_valid"] = True
    else:
        tampered["expected_condition_ids"][0] = "condition-tampered"
    digest_body = {
        key: value for key, value in tampered.items() if key != "evidence_digest"
    }
    tampered["evidence_digest"] = digest_json(digest_body)
    schedule_path.write_text(
        json.dumps(tampered, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    before = schedule_path.read_bytes()

    with pytest.raises(ValueError, match="Exp5 v3 execution schedule"):
        paper_cli._load_exp5_v3_execution_schedule(
            output_root=tmp_path,
            schedule_binding=binding,
        )

    assert schedule_path.read_bytes() == before


def test_exp5_v3_provider_exception_releases_slot_and_invalid_suffix_preserves_prefix(
    tmp_path: Path,
) -> None:
    binding, member_id_by_entry_id = _exp5_v3_schedule_test_binding()
    delegate = _FailOnceSmokeTransport()
    first = paper_cli._Exp5V3SharedProviderTransport(
        delegate=delegate,
        member_id_by_entry_id=member_id_by_entry_id,
        schedule_binding=binding,
    )
    entry_a = _bound_entry(member_id_by_entry_id, "member-a")
    with pytest.raises(RuntimeError, match="provider exploded"):
        first.post_chat_completion(
            api_key="test-double-only",
            **prepared_wire_kwargs(
                entry_a,
                {"member_id": "member-a", "model": entry_a.model, "messages": []},
                provider_family="siliconflow",
            ),
            timeout_seconds=1,
        )
    first.post_chat_completion(
        api_key="test-double-only",
        **prepared_wire_kwargs(
            entry_a,
            {"member_id": "member-a", "model": entry_a.model, "messages": []},
            provider_family="siliconflow",
        ),
        timeout_seconds=1,
    )
    evidence = paper_cli._write_exp5_v3_execution_schedule(
        output_root=tmp_path,
        limiter=first,
    )
    assert evidence["provider_calls_made"] == 2
    assert evidence["observed_peak"] == 1

    schedule_path = tmp_path / "audit" / "exp5_v3_execution_schedule.json"
    before = schedule_path.read_bytes()
    prior = paper_cli._load_exp5_v3_execution_schedule(
        output_root=tmp_path,
        schedule_binding=binding,
    )
    invalid = paper_cli._Exp5V3SharedProviderTransport(
        delegate=delegate,
        member_id_by_entry_id=member_id_by_entry_id,
        schedule_binding=binding,
        prior_evidence=prior,
    )
    _call_bound_members(invalid, member_id_by_entry_id, ("member-c",))

    with pytest.raises(ValueError, match="Exp5 v3 execution schedule"):
        paper_cli._write_exp5_v3_execution_schedule(
            output_root=tmp_path,
            limiter=invalid,
        )

    assert schedule_path.read_bytes() == before


@pytest.mark.parametrize(
    "conflicting_args",
    (
        ("--require-budget-approval",),
        ("--approve-budget-digest", "sha256:" + "0" * 64),
        ("--max-total-provider-attempts", "1"),
        ("--max-total-tokens", "1"),
        ("--max-cost-estimate", "0.01"),
    ),
)
def test_paper_cli_rejects_unlimited_budget_conflicts_before_transport(
    tmp_path: Path,
    conflicting_args: tuple[str, ...],
) -> None:
    transport_calls: list[dict] = []

    def transport(**kwargs):
        transport_calls.append(kwargs)
        raise AssertionError("budget conflict must fail before transport")

    exit_code = main(
        [
            "--output-root",
            str(tmp_path),
            "--experiments",
            "exp1",
            "--real-transport",
            "--unlimited-budget",
            *conflicting_args,
        ],
        gate_c_transport=transport,
    )

    assert exit_code == 3
    assert transport_calls == []
    suite = json.loads((tmp_path / "suite_manifest.json").read_text(encoding="utf-8"))
    assert suite["status"] == "blocked"
    assert suite["error_summary"][0]["failure_kind"] == "invalid_budget_mode"


def test_smoke_budget_conflict_persists_non_paper_classification(
    tmp_path: Path,
) -> None:
    exit_code = main(
        [
            "--output-root",
            str(tmp_path),
            "--smoke-profile",
            "benchmarks/paper/paper_smoke_profile.v1.json",
            "--unlimited-budget",
            "--max-total-tokens",
            "1",
        ]
    )

    assert exit_code == 3
    suite = json.loads((tmp_path / "suite_manifest.json").read_text(encoding="utf-8"))
    assert suite["formal"] is False
    assert suite["pilot_only"] is True
    assert suite["regression_only"] is True
    assert suite["paper_eligible"] is False
    assert {"smoke_suite", "pilot_only"}.issubset(
        suite["ineligibility_reasons"]
    )
    assert suite["provider_attempt_count"] == 0


def test_smoke_output_identity_mismatch_fails_before_writing_missing_documents(
    tmp_path: Path,
) -> None:
    (tmp_path / "smoke_profile.json").write_text(
        json.dumps({"profile_digest": "sha256:" + "0" * 64}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="smoke output identity mismatch"):
        paper_cli._write_smoke_documents_fail_closed(
            output_root=tmp_path,
            documents={
                "smoke_profile.json": {"profile_digest": "sha256:" + "1" * 64},
                "run_budget.json": {"budget_digest": "sha256:" + "2" * 64},
            },
        )

    assert not (tmp_path / "run_budget.json").exists()


def test_incomplete_smoke_cohort_records_each_endpoint_member_blocked(
    tmp_path: Path,
) -> None:
    profile = paper_cli.load_paper_smoke_profile(
        Path("benchmarks/paper/paper_smoke_profile.v1.json")
    )
    members = paper_cli._smoke_exp5_blocked_members(
        profile=profile,
        preflight={"status": "blocked", "member_plans": {}},
    )

    paper_cli._write_smoke_blocked_suite(
        output_root=tmp_path,
        profile=profile,
        failure_kind="incomplete_model_cohort",
        message="missing endpoint",
        blocked_members=members,
    )

    audit = json.loads(
        (tmp_path / "audit" / "smoke_endpoint_preflight.json").read_text(
            encoding="utf-8"
        )
    )
    assert [item["cohort_member_id"] for item in audit["members"]] == [
        "glm_5_2_siliconflow",
        "gpt_5_6_sol_high_openai",
        "qwen3_6_27b_siliconflow",
    ]
    assert all(item["status"] == "blocked" for item in audit["members"])
    assert all(item["provider_attempt_count"] == 0 for item in audit["members"])
    assert audit["paper_eligible"] is False


def test_exp1_exp4_smoke_does_not_require_or_register_exp5_cohort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = load_exp1_pilot_profile(
        "benchmarks/paper/exp1_minimal_pilot_profile.v3.json"
    )
    entry = profile.source_provider_config.entries[0]
    monkeypatch.setenv(entry.api_key_env, "launch-manifest-secret")
    captured: dict = {}

    def capture_smoke_suite(**kwargs):
        captured.update(kwargs)
        execution_plan = kwargs["execution_plan"]
        return PaperSuiteResult(
            suite_id=execution_plan.suite_id,
            status=PaperStatus.COMPLETED,
            output_root=execution_plan.output_root,
            started_at="2026-07-27T00:00:00Z",
            ended_at="2026-07-27T00:00:01Z",
            experiment_ids=execution_plan.experiment_ids,
                condition_count=20,
                run_count=20,
                task_count=20,
                provider_attempt_count=20,
                total_tokens=20,
            total_cost_estimate=0.0,
            paper_eligible=False,
            eligibility_report_ref=None,
            budget_ref={"budget_digest": kwargs["budget"].budget_digest},
            metrics_refs=(),
            audit_refs=(),
            error_summary=(),
        )

    monkeypatch.setattr(
        paper_cli,
        "execute_paper_smoke_suite",
        capture_smoke_suite,
    )

    command_argv = [
        "--output-root",
        str(tmp_path),
        "--smoke-profile",
        "benchmarks/paper/paper_smoke_exp1_exp4_profile.v2.json",
        "--ai-api-config",
        "benchmarks/paper/exp1_baseline_provider_config.v3.json",
        "--real-transport",
        "--unlimited-budget",
    ]
    exit_code = main(
        command_argv,
        gate_c_transport=object(),
        gate_c_ai_api_configs={
            profile.model_endpoint_identity.provider_config_id: (
                profile.source_provider_config
            )
        },
    )

    assert exit_code == 0
    assert captured["execution_plan"].direct_root_run_count == 20
    assert captured["budget"].planned_root_runs == 20
    approval = captured["budget"].quota_preflight["budget_approval"]
    assert approval["approval_required"] is False
    assert approval["approval_mode"] == "explicit_unlimited"
    assert approval["provided_approval_digest"] is None
    assert approval["authorization_source"] == "cli"
    assert approval["budget_digest"] == captured["budget"].budget_digest
    assert captured["budget"].quota_preflight["budget_commitments"][
        "hard_limits"
    ] == {}
    assert paper_cli.APPROVED_ENDPOINT_BINDINGS_KEY not in captured["ai_api_configs"]
    launch = captured["launch_manifest"]
    assert launch["schema_version"] == "tokenshare.paper_smoke_launch_manifest.v1"
    assert launch["authorization_scope"] == "exp1_exp4_only_smoke"
    assert launch["recorded_at"].endswith("Z")
    assert launch["recorded_at_local"]
    assert launch["execution_identity"]["suite_id"] == (
        "paper_smoke_exp1_exp4_v2"
    )
    assert launch["execution_identity"]["run_id"].startswith("smoke_run_")
    assert launch["execution_identity"]["generation_id"].startswith(
        "smoke_generation_"
    )
    assert launch["execution_identity"]["checkpoint_identities_at_launch"] == []
    assert launch["command"]["module"] == (
        "tokenshare.experiments.run_paper_experiments"
    )
    assert launch["command"]["argv"] == command_argv
    assert launch["suite_id"] == "paper_smoke_exp1_exp4_v2"
    assert launch["profile_digest"] == captured["profile"].profile_digest
    assert launch["execution_plan_digest"] == (
        captured["execution_plan"].execution_plan_digest
    )
    assert launch["catalog"]["catalog_digest"] == (
        captured["catalog_manifest"].catalog_digest
    )
    assert launch["budget"] == {
        "budget_mode": "unlimited",
        "budget_digest": captured["budget"].budget_digest,
        "direct_root_runs": 20,
        "actual_scheduled_root_runs": 20,
        "planned_ai_units": captured["budget"].planned_ai_units,
        "max_provider_attempts": captured["budget"].max_provider_attempts,
    }
    assert launch["model_endpoint"]["provider_family"] == "deepseek"
    assert launch["model_endpoint"]["provider_model_id"] == "deepseek-v4-pro"
    assert launch["model_endpoint"]["selected_entry_id"] == (
        "deepseek_v4_pro_exp1_baseline"
    )
    assert launch["model_endpoint"]["provider_config_path"] == (
        "benchmarks/paper/exp1_baseline_provider_config.v3.json"
    )
    assert launch["model_endpoint"]["provider_inflight_limit"] == 50
    assert launch["model_endpoint"]["source_provider_config_digest"] == (
        profile.source_provider_config.config_digest
    )
    assert launch["request_limits"] == {
        "max_provider_attempts": 1,
        "max_tokens": 300_000,
        "timeout_seconds": 600,
        "stream": False,
        "thinking": {"type": "enabled"},
        "reasoning_effort": "high",
    }
    assert launch["output_root"] == tmp_path.resolve(strict=False).as_posix()
    assert launch["worker_counts"] == [1, 10, 50]
    assert launch["repeat_ids"] == [0]
    assert len(launch["selection_inventory"]) == 20
    assert all(item["selection_digest"] for item in launch["selection_inventory"])
    assert launch["selection_bundle_digest"] == paper_cli.digest_json(
        launch["selection_inventory"]
    )
    assert launch["source_control"]["git_commit"]
    assert "commit_less_diff" in launch["source_control"]
    assert launch["explicit_exclusions"] == [
        "exp5",
        "pilot",
        "formal_experiments",
        "full_experiment_matrix",
        "full_lean",
        "lean_audit",
        "force_all_lean_audit",
    ]
    assert launch["formal"] is False
    assert launch["pilot_only"] is True
    assert launch["regression_only"] is True
    assert launch["paper_eligible"] is False
    assert "launch-manifest-secret" not in json.dumps(launch, sort_keys=True)

    formal_runner._validate_suite_inputs(
        dispatch_plans=captured["execution_plan"].dispatch_plans,
        catalog_manifest=captured["catalog_manifest"],
        budget=captured["budget"],
        budget_approval=approval,
        output_root=tmp_path,
        ai_api_configs=captured["ai_api_configs"],
        hard_limits=captured["hard_limits"],
        resume=False,
        replay_only=False,
        root_case_filter=captured["execution_plan"].root_case_filter,
    )

    stored_plans = [
        json.loads(json.dumps(plan.to_dict()))
        for plan in captured["execution_plan"].dispatch_plans
    ]
    stored_budget = captured["budget"].to_dict()
    first_golden_case = next(
        iter(
            stored_budget["quota_preflight"]["lean_3x3_matrix"]["cells"][0][
                "golden_evidence_by_case_id"
            ].values()
        )
    )
    first_golden_case["root_checker_report_ref"]["content_hash"] = (
        "sha256:" + "f" * 64
    )
    stored_view = stored_plans[0]["catalog_execution_view"]
    stored_view["view_body"]["resume_test_marker"] = "frozen"
    stored_view["view_digest"] = paper_cli.digest_json(
        {
            "schema_version": stored_view["schema_version"],
            "view_kind": stored_view["view_kind"],
            "catalog_manifest_digest": stored_view[
                "catalog_manifest_digest"
            ],
            "view_body": stored_view["view_body"],
        }
    )
    (tmp_path / "suite_manifest.json").write_text(
        json.dumps(
            {
                "suite_identity": {
                    "dispatch": {
                        "body": {
                            "schema_version": "tokenshare.paper_dispatch.v1",
                            "plans": stored_plans,
                        }
                    },
                    "budget": {"body": stored_budget},
                }
            }
        ),
        encoding="utf-8",
    )

    restored_execution_plan = paper_cli._restore_smoke_resume_dispatch_views(
        execution_plan=captured["execution_plan"],
        catalog_manifest=captured["catalog_manifest"],
        output_root=tmp_path,
    )

    assert restored_execution_plan.dispatch_plans[0].catalog_execution_view == (
        stored_view
    )
    assert (
        restored_execution_plan.dispatch_plans[0].condition_selection_bindings
        == captured["execution_plan"].dispatch_plans[
            0
        ].condition_selection_bindings
    )

    restored_budget = paper_cli._restore_smoke_resume_budget(
        budget=captured["budget"],
        output_root=tmp_path,
    )

    assert restored_budget.to_dict() == stored_budget
    with pytest.raises(ValueError, match="outside Lean golden evidence hashes"):
        paper_cli._restore_smoke_resume_budget(
            budget=replace(
                captured["budget"],
                planned_ai_units=captured["budget"].planned_ai_units + 1,
            ),
            output_root=tmp_path,
        )


def test_smoke_cli_returns_nonzero_when_execution_result_is_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = load_exp1_pilot_profile(
        "benchmarks/paper/exp1_minimal_pilot_profile.v3.json"
    )
    entry = profile.source_provider_config.entries[0]
    monkeypatch.setenv(entry.api_key_env, "blocked-smoke-exit-code-secret")

    def blocked_smoke_suite(**kwargs):
        return _smoke_result(
            kwargs["execution_plan"],
            kwargs["budget"],
            status=PaperStatus.BLOCKED,
        )

    monkeypatch.setattr(
        paper_cli,
        "execute_paper_smoke_suite",
        blocked_smoke_suite,
    )

    exit_code = main(
        [
            "--output-root",
            str(tmp_path),
            "--smoke-profile",
            "benchmarks/paper/paper_smoke_exp3_exp4_profile.v1.json",
            "--ai-api-config",
            "benchmarks/paper/exp1_baseline_provider_config.v3.json",
            "--real-transport",
            "--unlimited-budget",
        ],
        gate_c_transport=object(),
        gate_c_ai_api_configs={
            profile.model_endpoint_identity.provider_config_id: (
                profile.source_provider_config
            )
        },
    )

    assert exit_code == 3


def test_exp1_exp4_smoke_identity_preflight_changes_only_run_instance_identity_for_new_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    identities = []
    for name in ("run-a", "run-b"):
        output_root = tmp_path / name
        exit_code = main(
            [
                "--output-root",
                str(output_root),
                "--smoke-profile",
                "benchmarks/paper/paper_smoke_exp1_exp4_profile.v2.json",
                "--ai-api-config",
                "benchmarks/paper/exp1_baseline_provider_config.v3.json",
                "--unlimited-budget",
                "--smoke-identity-only",
            ]
        )

        assert exit_code == 0
        assert not output_root.exists()
        identities.append(json.loads(capsys.readouterr().out))

    first, second = identities
    assert first["schema_version"] == (
        "tokenshare.paper_smoke_prelaunch_identity.v1"
    )
    assert first["preregistered_semantics"] == second["preregistered_semantics"]
    semantics = first["preregistered_semantics"]
    current_selection_bundle_digest = semantics["selection_bundle_digest"]
    assert semantics == {
        "suite_id": "paper_smoke_exp1_exp4_v2",
        "profile_digest": (
            "sha256:ddc13477c6f75ead4846b557ff38c6dac936c7b8fcc4e726e5d33fa4f7f7751b"
        ),
        "catalog_digest": (
            "sha256:9293070c526d2912aebf38c85712a5572cc912c9e65f5a02754a3375957704fb"
        ),
            "selection_bundle_digest": current_selection_bundle_digest,
        "provider_config_source_digest": (
            "sha256:4bdf0d330c8d01af9760f17b333d75a68f0b8bfad214e807072562e057ef3923"
        ),
        "experiment_ids": [
            "exp1_real_ai_feasibility",
            "exp2_real_ai_scalability",
            "exp3_real_ai_fault_recovery",
            "exp4_real_ai_protocol_ablation",
        ],
        "direct_root_runs": 20,
        "supporting_worker_death_baselines": 0,
        "supporting_baseline_root_runs": 0,
        "supporting_baseline_ai_units": 0,
        "supporting_provider_attempt_upper_bound": 0,
        "actual_scheduled_root_runs": 20,
        "planned_first_attempt_ai_units": 110,
        "budget_provider_attempt_upper_bound": 142,
        "provider_retry_limit": 0,
        "worker_counts": [1, 10, 50],
        "repeat_ids": [0],
        "baseline_policy": "required_by_formal_plan",
        "baseline": "required_by_formal_plan",
        "baseline_comparison_eligible": True,
        "baseline_unavailable_reason": None,
        "cross_experiment_evidence_allowed": True,
        "cross_experiment_evidence_policy": {
            "policy_id": "exp1_to_exp3_shared_reference_v1",
            "allowed": True,
            "dependency_order_valid": True,
            "source_experiment_id": "exp1_real_ai_feasibility",
            "target_experiment_id": "exp3_real_ai_fault_recovery",
            "purpose": "shared_exp1_reference",
        },
        "model_endpoint": {
            "provider_config_id": "exp1_baseline_deepseek",
            "selected_entry_id": "deepseek_v4_pro_exp1_baseline",
            "provider_family": "deepseek",
            "provider_model_id": "deepseek-v4-pro",
            "reasoning_profile_id": "high",
            "base_url": "https://api.deepseek.com",
            "endpoint": "/chat/completions",
            "provider_inflight_limit": 50,
        },
        "request_limits": {
            "max_provider_attempts": 1,
            "max_tokens": 300_000,
            "timeout_seconds": 600,
            "stream": False,
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
        },
        "paper_eligible": False,
    }
    assert first["identity_policy"] == {
        "cross_run_stable": "preregistered_semantics",
        "per_output_root": [
            "run_instance_identity.output_root",
            "run_instance_identity.execution_plan_digest",
            "run_instance_identity.budget_digest",
        ],
        "same_output_root_identity_drift": "fail_closed",
    }
    assert first["run_instance_identity"]["output_root"] != second[
        "run_instance_identity"
    ]["output_root"]
    assert first["run_instance_identity"]["execution_plan_digest"] != second[
        "run_instance_identity"
    ]["execution_plan_digest"]
    assert first["run_instance_identity"]["budget_digest"] != second[
        "run_instance_identity"
    ]["budget_digest"]


def test_smoke_explicit_output_root_ignores_invalid_default_data_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_root = tmp_path / "explicit-identity"
    monkeypatch.setenv(
        "TOKENSHARE_DATA_ROOT",
        str(Path(__file__).resolve().parents[2] / "invalid-data-root"),
    )

    exit_code = main(
        [
            "--output-root",
            str(output_root),
            "--smoke-profile",
            "benchmarks/paper/paper_smoke_exp3_exp4_profile.v1.json",
            "--ai-api-config",
            "benchmarks/paper/exp1_baseline_provider_config.v3.json",
            "--unlimited-budget",
            "--smoke-identity-only",
        ]
    )

    assert exit_code == 0
    assert not output_root.exists()
    assert json.loads(capsys.readouterr().out)["run_instance_identity"][
        "output_root"
    ] == output_root.resolve().as_posix()


def test_smoke_rejects_configured_formal_root_without_creating_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data_root = (tmp_path / "TokenShareData").resolve()
    formal_root = data_root / "outputs" / "experiments" / "paper_v1"
    monkeypatch.setenv("TOKENSHARE_DATA_ROOT", str(data_root))

    exit_code = main(
        [
            "--output-root",
            str(formal_root),
            "--smoke-profile",
            "benchmarks/paper/paper_smoke_exp3_exp4_profile.v1.json",
            "--ai-api-config",
            "benchmarks/paper/exp1_baseline_provider_config.v3.json",
            "--unlimited-budget",
            "--plan-only",
        ]
    )

    assert exit_code == 3
    assert not formal_root.exists()
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "blocked"
    assert result["failure_kind"] == "invalid_smoke_output_root"
    assert result["provider_calls_made"] == 0


def test_paper_cli_requires_explicit_output_root_without_consuming_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data_root = (tmp_path / "TokenShareData").resolve()
    monkeypatch.setenv("TOKENSHARE_DATA_ROOT", str(data_root))

    exit_code = main(["--experiments", "exp1", "--plan-only"])

    assert exit_code == 3
    assert not data_root.exists()
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "blocked"
    assert result["failure_kind"] == "missing_output_root"
    assert result["provider_calls_made"] == 0


def test_exp3_exp4_smoke_identity_has_11_direct_roots_and_no_baseline_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    output_root = tmp_path / "identity-only"

    exit_code = main(
        [
            "--output-root",
            str(output_root),
            "--smoke-profile",
            "benchmarks/paper/paper_smoke_exp3_exp4_profile.v1.json",
            "--ai-api-config",
            "benchmarks/paper/exp1_baseline_provider_config.v3.json",
            "--unlimited-budget",
            "--smoke-identity-only",
        ]
    )

    assert exit_code == 0
    assert not output_root.exists()
    identity = json.loads(capsys.readouterr().out)
    semantics = identity["preregistered_semantics"]
    assert semantics["suite_id"] == "paper_smoke_exp3_exp4_v1"
    assert semantics["experiment_ids"] == [
        "exp3_real_ai_fault_recovery",
        "exp4_real_ai_protocol_ablation",
    ]
    assert semantics["direct_root_runs"] == 11
    assert semantics["supporting_baseline_root_runs"] == 0
    assert semantics["supporting_baseline_ai_units"] == 0
    assert semantics["supporting_provider_attempt_upper_bound"] == 0
    assert semantics["actual_scheduled_root_runs"] == 11
    assert semantics["baseline_policy"] == "omitted_for_smoke_regression"
    assert semantics["baseline"] is None
    assert semantics["baseline_comparison_eligible"] is False
    assert semantics["baseline_unavailable_reason"] == (
        "smoke_baseline_not_requested"
    )
    assert semantics["cross_experiment_evidence_allowed"] is False
    assert semantics["worker_counts"] == [10]
    assert semantics["repeat_ids"] == [0]
    assert semantics["model_endpoint"]["provider_model_id"] == (
        "deepseek-v4-pro"
    )
    assert semantics["model_endpoint"]["reasoning_profile_id"] == "high"
    assert semantics["model_endpoint"]["provider_inflight_limit"] == 50
    assert semantics["request_limits"] == {
        "max_provider_attempts": 1,
        "max_tokens": 300_000,
        "timeout_seconds": 600,
        "stream": False,
        "thinking": {"type": "enabled"},
        "reasoning_effort": "high",
    }


def test_exp1_exp4_smoke_rejects_frozen_run_instance_digest_drift_before_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = load_exp1_pilot_profile(
        "benchmarks/paper/exp1_minimal_pilot_profile.v3.json"
    )
    dispatch_calls = 0

    def capture_smoke_suite(**kwargs):
        nonlocal dispatch_calls
        dispatch_calls += 1
        raise AssertionError("provider dispatch must remain unreachable")

    monkeypatch.setattr(
        paper_cli,
        "execute_paper_smoke_suite",
        capture_smoke_suite,
    )
    exit_code = main(
        [
            "--output-root",
            str(tmp_path),
            "--smoke-profile",
            "benchmarks/paper/paper_smoke_exp1_exp4_profile.v2.json",
            "--ai-api-config",
            "benchmarks/paper/exp1_baseline_provider_config.v3.json",
            "--real-transport",
            "--unlimited-budget",
            "--expect-smoke-execution-plan-digest",
            "sha256:" + "8" * 64,
            "--expect-smoke-budget-digest",
            "sha256:" + "9" * 64,
        ],
        gate_c_transport=object(),
        gate_c_ai_api_configs={
            profile.model_endpoint_identity.provider_config_id: (
                profile.source_provider_config
            )
        },
    )

    suite = json.loads(
        (tmp_path / "suite_manifest.json").read_text(encoding="utf-8")
    )
    assert exit_code == 3
    assert dispatch_calls == 0
    assert suite["status"] == "blocked"
    assert suite["provider_attempt_count"] == 0
    assert suite["error_summary"][0]["failure_kind"] == (
        "smoke_run_instance_identity_blocked"
    )


@pytest.mark.parametrize("drift_field", ("execution_plan_digest", "budget_digest"))
def test_smoke_resume_rejects_same_root_run_instance_identity_drift(
    tmp_path: Path,
    drift_field: str,
) -> None:
    stable = {
        "schema_version": "tokenshare.paper_smoke_launch_manifest.v1",
        "authorization_scope": "exp1_exp4_only_smoke",
        "suite_id": "paper_smoke_exp1_exp4_v2",
        "profile_digest": "sha256:" + "1" * 64,
        "execution_plan_digest": "sha256:" + "2" * 64,
        "catalog": {"catalog_digest": "sha256:" + "3" * 64},
        "budget": {"budget_digest": "sha256:" + "4" * 64},
        "output_root": tmp_path.resolve(strict=False).as_posix(),
        "paper_eligible": False,
    }
    initial_body = {
        **stable,
        "recorded_at": "2026-07-28T00:00:00Z",
        "command": {"executable": "python", "module": "paper", "argv": []},
    }
    initial = {
        **initial_body,
        "launch_manifest_digest": paper_cli.digest_json(initial_body),
    }
    (tmp_path / "smoke_launch_manifest.json").write_text(
        json.dumps(initial),
        encoding="utf-8",
    )
    checkpoint_root = tmp_path / "experiments" / "exp1" / "runs" / "c1" / "0"
    checkpoint_root.mkdir(parents=True)
    (checkpoint_root / "CURRENT.json").write_text(
        json.dumps(
            {
                "generation_id": "generation-1",
                "generation_manifest_digest": "sha256:" + "5" * 64,
            }
        ),
        encoding="utf-8",
    )
    drifted = json.loads(json.dumps(stable))
    if drift_field == "execution_plan_digest":
        drifted[drift_field] = "sha256:" + "9" * 64
    else:
        drifted["budget"][drift_field] = "sha256:" + "9" * 64
    current_body = {
        **drifted,
        "recorded_at": "2026-07-28T00:01:00Z",
        "command": {
            "executable": "python",
            "module": "paper",
            "argv": ["--resume"],
        },
    }
    current = {
        **current_body,
        "launch_manifest_digest": paper_cli.digest_json(current_body),
    }

    with pytest.raises(ValueError, match="smoke resume authorization identity drift"):
        paper_cli._resolve_smoke_invocation_manifests(
            resume=True,
            output_root=tmp_path,
            current_manifest=current,
        )


def test_exp1_exp4_smoke_rejects_explicit_v2_before_provider_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = load_exp1_pilot_profile(
        "benchmarks/paper/exp1_minimal_pilot_profile.v3.json"
    )
    dispatch_calls = 0

    def capture_smoke_suite(**kwargs):
        nonlocal dispatch_calls
        dispatch_calls += 1
        execution_plan = kwargs["execution_plan"]
        return PaperSuiteResult(
            suite_id=execution_plan.suite_id,
            status=PaperStatus.COMPLETED,
            output_root=execution_plan.output_root,
            started_at="2026-07-28T00:00:00Z",
            ended_at="2026-07-28T00:00:01Z",
            experiment_ids=execution_plan.experiment_ids,
            condition_count=21,
            run_count=21,
            task_count=21,
            provider_attempt_count=21,
            total_tokens=21,
            total_cost_estimate=0.0,
            paper_eligible=False,
            eligibility_report_ref=None,
            budget_ref={"budget_digest": kwargs["budget"].budget_digest},
            metrics_refs=(),
            audit_refs=(),
            error_summary=(),
        )

    monkeypatch.setattr(
        paper_cli,
        "execute_paper_smoke_suite",
        capture_smoke_suite,
    )
    exit_code = main(
        [
            "--output-root",
            str(tmp_path),
            "--smoke-profile",
            "benchmarks/paper/paper_smoke_exp1_exp4_profile.v2.json",
            "--ai-api-config",
            "benchmarks/paper/exp1_baseline_provider_config.v2.json",
            "--real-transport",
            "--unlimited-budget",
        ],
        gate_c_transport=object(),
        gate_c_ai_api_configs={
            profile.model_endpoint_identity.provider_config_id: (
                profile.source_provider_config
            )
        },
    )

    suite = json.loads(
        (tmp_path / "suite_manifest.json").read_text(encoding="utf-8")
    )
    assert exit_code == 3
    assert dispatch_calls == 0
    assert suite["status"] == "blocked"
    assert suite["provider_attempt_count"] == 0
    assert suite["error_summary"][0]["failure_kind"] == (
        "exp1_exp4_v3_config_blocked"
    )


def test_exp1_exp4_smoke_rejects_implicit_v3_default_before_provider_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = load_exp1_pilot_profile(
        "benchmarks/paper/exp1_minimal_pilot_profile.v3.json"
    )
    dispatch_calls = 0

    def capture_smoke_suite(**kwargs):
        nonlocal dispatch_calls
        dispatch_calls += 1
        raise AssertionError("provider dispatch must remain unreachable")

    monkeypatch.setattr(
        paper_cli,
        "execute_paper_smoke_suite",
        capture_smoke_suite,
    )
    exit_code = main(
        [
            "--output-root",
            str(tmp_path),
            "--smoke-profile",
            "benchmarks/paper/paper_smoke_exp1_exp4_profile.v2.json",
            "--real-transport",
            "--unlimited-budget",
        ],
        gate_c_transport=object(),
        gate_c_ai_api_configs={
            profile.model_endpoint_identity.provider_config_id: (
                profile.source_provider_config
            )
        },
    )

    suite = json.loads(
        (tmp_path / "suite_manifest.json").read_text(encoding="utf-8")
    )
    assert exit_code == 3
    assert dispatch_calls == 0
    assert suite["status"] == "blocked"
    assert suite["provider_attempt_count"] == 0
    assert suite["error_summary"][0]["failure_kind"] == (
        "exp1_exp4_v3_config_blocked"
    )


@pytest.mark.parametrize(
    "drift_kind",
    (
        "provider_family",
        "timeout_seconds",
        "max_tokens",
        "model",
        "endpoint",
        "thinking",
        "reasoning_effort",
    ),
)
def test_exp1_exp4_smoke_rejects_v3_semantic_drift_before_provider_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift_kind: str,
) -> None:
    profile = load_exp1_pilot_profile(
        "benchmarks/paper/exp1_minimal_pilot_profile.v3.json"
    )
    config_body = json.loads(
        Path("benchmarks/paper/exp1_baseline_provider_config.v3.json").read_text(
            encoding="utf-8"
        )
    )
    if drift_kind == "provider_family":
        config_body["provider_family"] = "siliconflow"
    elif drift_kind == "timeout_seconds":
        config_body["defaults"]["timeout_seconds"] = 599
    elif drift_kind == "max_tokens":
        config_body["defaults"]["max_tokens"] = 299_999
    elif drift_kind == "model":
        config_body["entries"][0]["model"] = "deepseek-v4-pro-drift"
    elif drift_kind == "endpoint":
        config_body["entries"][0]["endpoint"] = "/v1/chat/completions"
    elif drift_kind == "thinking":
        config_body["entries"][0]["request_overrides"]["thinking"] = {
            "type": "disabled"
        }
    elif drift_kind == "reasoning_effort":
        config_body["entries"][0]["request_overrides"][
            "reasoning_effort"
        ] = "medium"
    else:  # pragma: no cover - 参数表是封闭集合。
        raise AssertionError(f"unsupported drift kind: {drift_kind}")
    drifted_config = load_ai_api_config(config_body)
    dispatch_calls = 0

    def capture_smoke_suite(**kwargs):
        nonlocal dispatch_calls
        dispatch_calls += 1
        raise AssertionError("provider dispatch must remain unreachable")

    monkeypatch.setattr(
        paper_cli,
        "execute_paper_smoke_suite",
        capture_smoke_suite,
    )
    exit_code = main(
        [
            "--output-root",
            str(tmp_path),
            "--smoke-profile",
            "benchmarks/paper/paper_smoke_exp1_exp4_profile.v2.json",
            "--ai-api-config",
            "benchmarks/paper/exp1_baseline_provider_config.v3.json",
            "--real-transport",
            "--unlimited-budget",
        ],
        gate_c_transport=object(),
        gate_c_ai_api_configs={
            profile.model_endpoint_identity.provider_config_id: drifted_config
        },
    )

    suite = json.loads(
        (tmp_path / "suite_manifest.json").read_text(encoding="utf-8")
    )
    assert exit_code == 3
    assert dispatch_calls == 0
    assert suite["status"] == "blocked"
    assert suite["provider_attempt_count"] == 0
    assert suite["error_summary"][0]["failure_kind"] == (
        "exp1_exp4_v3_config_blocked"
    )


def test_smoke_resume_reuses_launch_identity_and_records_checkpoint_lineage(
    tmp_path: Path,
) -> None:
    stable = {
        "schema_version": "tokenshare.paper_smoke_launch_manifest.v1",
        "authorization_scope": "exp1_exp4_only_smoke",
        "suite_id": "paper_smoke_exp1_exp4_v2",
        "profile_digest": "sha256:" + "1" * 64,
        "execution_plan_digest": "sha256:" + "2" * 64,
        "catalog": {"catalog_digest": "sha256:" + "3" * 64},
        "budget": {"budget_digest": "sha256:" + "4" * 64},
        "output_root": tmp_path.resolve(strict=False).as_posix(),
        "paper_eligible": False,
    }
    initial_body = {
        **stable,
        "recorded_at": "2026-07-27T07:37:37Z",
        "command": {"executable": "python", "module": "paper", "argv": []},
    }
    initial = {
        **initial_body,
        "launch_manifest_digest": paper_cli.digest_json(initial_body),
    }
    (tmp_path / "smoke_launch_manifest.json").write_text(
        json.dumps(initial),
        encoding="utf-8",
    )
    checkpoint_root = (
        tmp_path
        / "experiments"
        / "exp1_real_ai_feasibility"
        / "runs"
        / "condition-1"
        / "0"
    )
    checkpoint_root.mkdir(parents=True)
    (checkpoint_root / "CURRENT.json").write_text(
        json.dumps(
            {
                "schema_version": "tokenshare.paper_checkpoint_current.v1",
                "generation_id": "generation-1",
                "generation_manifest_digest": "sha256:" + "5" * 64,
            }
        ),
        encoding="utf-8",
    )
    current_body = {
        **stable,
        "recorded_at": "2026-07-27T08:00:00Z",
        "command": {
            "executable": "python",
            "module": "paper",
            "argv": ["--resume"],
        },
    }
    current = {
        **current_body,
        "launch_manifest_digest": paper_cli.digest_json(current_body),
    }

    launch, recovery = paper_cli._resolve_smoke_invocation_manifests(
        resume=True,
        output_root=tmp_path,
        current_manifest=current,
    )

    assert launch == initial
    assert recovery["parent_launch_manifest_digest"] == initial[
        "launch_manifest_digest"
    ]
    assert recovery["command"]["argv"] == ["--resume"]
    assert recovery["source_checkpoints"] == [
        {
            "checkpoint_path": (
                "experiments/exp1_real_ai_feasibility/runs/condition-1/0/"
                "CURRENT.json"
            ),
            "generation_id": "generation-1",
            "generation_manifest_digest": "sha256:" + "5" * 64,
        }
    ]
    assert recovery["recovery_id"].startswith("smoke_recovery_")
    assert recovery["paper_eligible"] is False


def test_paper_cli_plan_only_p0_core_uses_shared_exp1_without_supporting_roots(
    tmp_path: Path,
) -> None:
    exit_code = main(
        [
            "--output-root",
            str(tmp_path),
            "--experiments",
            "exp1,exp2,exp3,exp4",
            "--plan-only",
        ]
    )

    assert exit_code == 0
    budget = json.loads(
        (tmp_path / "run_budget.json").read_text(encoding="utf-8")
    )
    dispatch = json.loads(
        (tmp_path / "paper_dispatch_plans.json").read_text(encoding="utf-8")
    )
    identity = budget["quota_preflight"]["budget_commitments"][
        "experiment_budget_identity"
    ]
    assert [plan["condition_count"] for plan in dispatch["plans"]] == [
        12,
        12,
        162,
        90,
    ]
    assert identity["headline_p0_core_root_runs"] == budget["planned_root_runs"]
    assert identity["actual_p0_core_root_runs"] == budget["planned_root_runs"]
    assert identity["supporting_baseline_root_runs_by_experiment"] == {}
    planned_units = identity["planned_first_attempt_ai_units_by_experiment"]
    assert set(planned_units) == set(identity["headline_root_runs_by_experiment"])
    assert budget["planned_ai_units"] == sum(planned_units.values())
    assert budget["max_provider_attempts"] >= budget["planned_ai_units"]
    disk_inputs = budget["disk_estimate"]["inputs"]
    assert disk_inputs["planned_conditions"] == budget["planned_conditions"]
    assert disk_inputs["planned_root_runs"] == budget["planned_root_runs"]
    assert disk_inputs["planned_ai_units"] == budget["planned_ai_units"]
    assert disk_inputs["provider_attempt_upper_bound"] == budget[
        "max_provider_attempts"
    ]
    assert disk_inputs["token_upper_bound"] == budget["token_upper_bound"]
    estimate = budget["disk_estimate"]
    forecast = int(estimate["forecast_bytes"])
    required = (
        forecast
        + max((forecast + 3) // 4, 2 * 1024**3)
        + int(estimate["max_condition_compaction_bytes"])
    )
    assert required < int(439.85 * 1024**3)
    assert budget["quota_preflight"]["provider_calls_made"] == 0
    assert dispatch["provider_calls_made"] == 0


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


def test_paper_cli_rejects_stale_budget_digest_before_formal_dispatch(
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
            "--approve-budget-digest",
            "sha256:" + "0" * 64,
        ]
    )

    suite = json.loads(
        (tmp_path / "suite_manifest.json").read_text(encoding="utf-8")
    )
    assert exit_code == 2
    assert suite["status"] == "blocked"
    assert suite["error_summary"][0]["failure_kind"] == (
        "budget_digest_mismatch"
    )


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
        Path(kwargs["output_root"]).mkdir(parents=True, exist_ok=True)
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


def test_paper_cli_reports_disk_block_as_structured_json_without_creating_run_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_budget_policy_cli_boundaries(monkeypatch)
    run_root = tmp_path / "disk-blocked"

    def blocked_formal_suite(**kwargs: object) -> object:
        assert Path(kwargs["output_root"]) == run_root
        assert not run_root.exists()
        raise formal_runner.PaperInfrastructureBlockedError(
            "formal disk preflight failed: insufficient disk capacity",
            evidence_integrity=formal_runner.PaperEvidenceIntegrity.INVALID,
            failure_stage="disk_preflight",
            failure_kind="insufficient_disk_capacity",
            diagnostics={"required_bytes": 101, "available_bytes": 100},
        )

    monkeypatch.setattr(
        paper_cli,
        "execute_paper_formal_suite",
        blocked_formal_suite,
    )

    exit_code = main(
        [
            "--output-root",
            str(run_root),
            "--experiments",
            "exp1",
            "--real-transport",
        ],
        gate_c_transport=object(),
        gate_c_ai_api_configs={"capture": object()},
    )

    summary = json.loads(capsys.readouterr().out)
    assert exit_code == 3
    assert summary["status"] == "blocked"
    assert summary["failure_stage"] == "disk_preflight"
    assert summary["failure_kind"] == "insufficient_disk_capacity"
    assert summary["resource_diagnostics"] == {
        "required_bytes": 101,
        "available_bytes": 100,
    }
    assert summary["provider_calls_made"] == 0
    assert not run_root.exists()


def test_paper_cli_offline_capturing_e2e_is_regression_only_without_tables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = load_exp1_pilot_profile(
        "benchmarks/paper/exp1_minimal_pilot_profile.v3.json"
    )
    entry = profile.source_provider_config.entries[0]
    monkeypatch.setenv(entry.api_key_env, "task11-offline-capturing-key")
    monkeypatch.setattr(
        formal_runner.FormalEvidenceStore,
        "_validate_suite_and_experiment_manifests",
        lambda self, **_kwargs: None,
    )
    build_dispatch_plans = paper_cli.build_gate_c_dispatch_plans
    canonical_selection_for_condition = paper_exp1._selection_for_condition

    def single_root_selection(selection, catalog_manifest):
        case_id = selection.ordered_case_ids[0]
        case = next(
            case
            for case in catalog_manifest.factorization_cases
            if case["case_id"] == case_id
        )
        return replace(
            selection,
            ordered_case_ids=(case_id,),
            expected_ai_unit_count=(
                paper_catalog_module.estimated_ai_units_for_case(case)
            ),
        )

    def single_root_canonical_selection(*, context, condition):
        return single_root_selection(
            canonical_selection_for_condition(
                context=context,
                condition=condition,
            ),
            context.catalog,
        )

    def build_factorization_capture_plan(**kwargs):
        plans = build_dispatch_plans(**kwargs)
        plan = plans[0]
        condition_index = next(
            index
            for index, condition in enumerate(plan.conditions)
            if condition.domain == "factorization"
        )
        binding = plan.condition_selection_bindings[condition_index]
        selection = single_root_selection(
            binding.selection,
            kwargs["catalog_manifest"],
        )
        monkeypatch.setattr(
            paper_exp1,
            "_selection_for_condition",
            single_root_canonical_selection,
        )
        return (
            replace(
                plan,
                conditions=(plan.conditions[condition_index],),
                condition_selection_bindings=(
                    replace(binding, selection=selection),
                ),
            ),
        )

    monkeypatch.setattr(
        paper_cli,
        "build_gate_c_dispatch_plans",
        build_factorization_capture_plan,
    )

    class ZeroUsageCapturingTransport(_CLICapturingTransport):
        def post_chat_completion(self, **kwargs):
            response = super().post_chat_completion(**kwargs)
            response.body["usage"] = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            }
            response.text = json.dumps(response.body, ensure_ascii=False)
            return response

    transport = ZeroUsageCapturingTransport()
    exit_code = main(
        [
            "--output-root",
            str(tmp_path),
            "--experiments",
            "exp1",
            "--real-transport",
            "--max-total-provider-attempts",
            "20",
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
    suite = json.loads(
        (tmp_path / "suite_manifest.json").read_text(encoding="utf-8")
    )
    budget = json.loads(
        (tmp_path / "run_budget.json").read_text(encoding="utf-8")
    )
    assert suite["formal"] is True
    assert suite["paper_eligible"] is False
    assert "traceability_replay_input_root_ref" not in suite
    assert budget["quota_preflight"]["provider_calls_made"] == 0

    run_root = (
        tmp_path
        / "experiments"
        / "exp1_real_ai_feasibility"
        / "runs"
        / "exp1_factorization_easy_w10_r0"
        / "0"
    )
    current = json.loads(
        (run_root / "CURRENT.json").read_text(encoding="utf-8")
    )
    generation_root = run_root / ".generations" / current["generation_id"]
    task_rows = [
        json.loads(line)
        for line in (generation_root / "per_task_results.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    case_task_id = next(
        row["task_id"]
        for row in task_rows
        if row.get("root_status") == "completed"
    )
    event_rows = [
        json.loads(line)
        for line in (generation_root / "events" / "event_log.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    protocol_events = [
        row for row in event_rows if row.get("schema_version") == "LedgerEvent.v2"
    ]
    assert protocol_events
    assert {
        row["task_id"] for row in protocol_events
    } == {f"paper_factorization_{case_task_id}"}

    def nested_artifact_refs(value):
        refs = []
        if isinstance(value, dict):
            if value.get("schema_version") == "ArtifactRef.v1":
                refs.append(value)
            for child in value.values():
                refs.extend(nested_artifact_refs(child))
        elif isinstance(value, list):
            for child in value:
                refs.extend(nested_artifact_refs(child))
        return refs

    raw_ref = next(
        ref
        for event in protocol_events
        for ref in nested_artifact_refs(event)
        if ref.get("artifact_type") == "RawModelOutput"
    )
    artifact_rows = [
        json.loads(line)
        for line in (generation_root / "artifacts" / "artifact_index.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    raw_row = next(
        row
        for row in artifact_rows
        if row.get("artifact_id") == raw_ref["artifact_id"]
        and row.get("content_hash") == raw_ref["content_hash"]
    )
    assert {
        "task_id": raw_row["task_id"],
        "artifact_id": raw_row["artifact_id"],
        "content_hash": raw_row["content_hash"],
        "size_bytes": raw_row["size_bytes"],
        "source_uri": raw_row["source_uri"],
        "path": raw_row["path"],
    } == {
        "task_id": case_task_id,
        "artifact_id": raw_ref["artifact_id"],
        "content_hash": raw_ref["content_hash"],
        "size_bytes": raw_ref["size_bytes"],
        "source_uri": raw_ref["uri"],
        "path": raw_row["path"],
    }
    raw_payload = (tmp_path / raw_row["path"]).read_bytes()
    assert len(raw_payload) == raw_ref["size_bytes"]
    assert f"sha256:{sha256(raw_payload).hexdigest()}" == raw_ref["content_hash"]
    for relative_path in (
        "metrics/paper_metric_drafts.v1.json",
        "metrics/paper_lineage_source_index.v1.jsonl",
        "metrics/paper_metric_observations.v1.jsonl",
        "metrics/paper_metric_observations_manifest.v1.json",
        "metrics/paper_table_feasibility.csv.draft.json",
        "metrics/paper_table_scalability_online.csv.draft.json",
        "metrics/paper_plot_scalability.csv.draft.json",
        "metrics/paper_table_recovery_online.csv.draft.json",
        "metrics/paper_plot_robustness.csv.draft.json",
        "metrics/paper_table_ablation.csv.draft.json",
        "metrics/paper_table_model_endpoint_quality.csv.draft.json",
        "metrics/paper_table_model_endpoint_resources.csv.draft.json",
        "audit/replay_report.json",
        "audit/paper_formal_report_manifest.v2.json",
        "audit/paper_eligibility_report.json",
        "formal_report_result.json",
    ):
        assert not (tmp_path / relative_path).exists(), relative_path


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
        "write_paper_formal_replay_report",
        lambda **_kwargs: calls.append("replay-report"),
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
    assert calls == ["replay", "replay-report", "metrics", "report", "refresh"]


def test_formal_cli_exit_gate_rejects_failed_report_for_paper_eligible_suite() -> None:
    execution_result = PaperSuiteResult(
        suite_id="paper-report-gate-test",
        status=PaperStatus.COMPLETED_WITH_FAILURES,
        output_root="unused",
        started_at="2026-07-31T00:00:00Z",
        ended_at="2026-07-31T00:00:01Z",
        experiment_ids=["exp5_real_ai_model_endpoint_comparison"],
        condition_count=48,
        run_count=1284,
        task_count=1284,
        provider_attempt_count=9888,
        total_tokens=1,
        total_cost_estimate=0.1,
        paper_eligible=True,
        eligibility_report_ref=None,
        budget_ref=None,
        metrics_refs=[],
        audit_refs=[],
        error_summary=[],
    )
    failed_report = SimpleNamespace(
        paper_eligible=False,
        formal_paper_table_generated=False,
    )

    assert paper_cli._formal_execution_exit_code(
        execution_result,
        failed_report,
    ) == 3
    successful_report = SimpleNamespace(
        paper_eligible=True,
        formal_paper_table_generated=True,
    )
    assert paper_cli._formal_execution_exit_code(
        execution_result,
        successful_report,
    ) == 0


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
            "benchmarks/paper/exp1_minimal_pilot_profile.v2.json",
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
    assert suite["suite_id"] == "paper_exp1_minimal_pilot_v2"
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
            "benchmarks/paper/exp1_minimal_pilot_profile.v2.json",
            "--real-transport",
        ]
    )

    suite = json.loads(
        (tmp_path / "suite_manifest.json").read_text(encoding="utf-8")
    )
    assert exit_code == 3
    assert suite["suite_id"] == "paper_exp1_minimal_pilot_v2_blocked"
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
            "benchmarks/paper/exp1_minimal_pilot_profile.v2.json",
            "--pilot",
            "--real-transport",
            "--approve-budget-digest",
            APPROVED_EXP1_PILOT_DIGEST,
        ]
    )

    suite = json.loads(
        (
            tmp_path
            / "paper_exp1_minimal_pilot_v2_blocked"
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
    formal_suite_root = tmp_path / "paper_exp1_minimal_pilot_v2"
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
            "benchmarks/paper/exp1_minimal_pilot_profile.v2.json",
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
            / "paper_exp1_minimal_pilot_v2_blocked"
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
        suite_root = kwargs["output_base"] / "paper_exp1_minimal_pilot_v2"
        suite_root.mkdir(parents=True, exist_ok=True)
        body = {
            "schema_version": "tokenshare.paper_exp1_pilot_execution_result.v1",
            "suite_id": "paper_exp1_minimal_pilot_v2",
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
            "benchmarks/paper/exp1_minimal_pilot_profile.v2.json",
            "--pilot",
            "--real-transport",
            "--approve-budget-digest",
            APPROVED_EXP1_PILOT_DIGEST,
            "--baseline-entry-id",
            "deepseek_v4_pro_exp1_baseline",
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
    assert received["baseline_entry_id"] == "deepseek_v4_pro_exp1_baseline"
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
        and condition["paper_difficulty"] == "hard"
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
        and condition["paper_difficulty"] == "hard"
        and condition["worker_count"] == 1
        and condition["repeat_id"] == 0
    )
    profile = load_exp1_pilot_profile(
        "benchmarks/paper/exp1_minimal_pilot_profile.v3.json"
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
    request_bodies = [json.loads(call.decode("utf-8")) for call in transport.calls]
    assert all(
        body["response_format"] == {"type": "json_object"}
        and body["thinking"] == {"type": "enabled"}
        and body["reasoning_effort"] == "high"
        and "temperature" not in body
        and "top_p" not in body
        for body in request_bodies
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


@pytest.mark.parametrize(
    "root_relation",
    ("equal", "parent", "child"),
)
def test_exp1_pilot_cli_rejects_canonical_selector_output_root_overlap(
    tmp_path: Path,
    root_relation: str,
) -> None:
    output_base = tmp_path / root_relation
    canonical_root = output_base / "paper_exp1_minimal_pilot_v2"
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
            "benchmarks/paper/exp1_minimal_pilot_profile.v2.json",
            "--pilot",
            "--real-transport",
            "--approve-budget-digest",
            APPROVED_EXP1_PILOT_DIGEST,
            "--baseline-entry-id",
            "deepseek_v4_pro_exp1_baseline",
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
    blocked_root = output_base / "paper_exp1_minimal_pilot_v2_blocked"
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
                    {
                        "entry_id": "local_deepseek",
                        "enabled": True,
                        "base_url": "https://api.deepseek.com",
                        "api_key_env": "TOKENSHARE_TEST_LOCAL_KEY",
                        "api_key": secret,
                        "model": "deepseek-v4-pro",
                        "endpoint": "/chat/completions",
                        "supports_json_mode": True,
                        "supports_streaming": False,
                        "request_overrides": {
                            "thinking": {"type": "enabled"},
                            "reasoning_effort": "high",
                        },
                        "pricing": {
                            "currency": "CNY",
                            "cached_input_per_million_tokens": 0.025,
                            "uncached_input_per_million_tokens": 3.0,
                            "output_per_million_tokens": 6.0,
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
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    observed: dict = {}

    def fake_execute_exp1_pilot(**kwargs):
        observed["approved_env_value"] = os.environ.get(
            "DEEPSEEK_API_KEY"
        )
        observed["source_config_digest"] = (
            kwargs["pilot_profile"].source_provider_config.config_digest
        )
        suite_root = kwargs["output_base"] / "paper_exp1_minimal_pilot_v2"
        suite_root.mkdir(parents=True, exist_ok=True)
        body = {
            "schema_version": "tokenshare.paper_exp1_pilot_execution_result.v1",
            "suite_id": "paper_exp1_minimal_pilot_v2",
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
            "benchmarks/paper/exp1_minimal_pilot_profile.v2.json",
            "--pilot",
            "--real-transport",
            "--approve-budget-digest",
            APPROVED_EXP1_PILOT_DIGEST,
            "--baseline-entry-id",
            "deepseek_v4_pro_exp1_baseline",
            "--ai-api-config",
            str(local_config),
        ]
    )

    assert exit_code == 0
    assert observed["approved_env_value"] == secret
    assert observed["source_config_digest"] == (
        load_exp1_pilot_profile(
            "benchmarks/paper/exp1_minimal_pilot_profile.v2.json"
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

    def post_chat_completion(self, **kwargs):
        response = self.delegate.post_chat_completion(**kwargs)
        response.body["model"] = json.loads(
            kwargs["body_bytes"].decode("utf-8")
        )["model"]
        self.calls.append(kwargs["body_bytes"])
        return response


class _DualDomainCLICapturingTransport:
    """按 canonical prompt 路由两个既有 scripted provider adapter。"""

    tokenshare_offline_capturing_transport = True

    def __init__(self) -> None:
        self.factorization_delegate = ScriptedFactorizationRangeTransport()
        self.lean_delegate = lean_paper_adapter.ScriptedLeanPaperProofTransport()
        self.calls: list[dict] = []

    def post_chat_completion(self, **kwargs):
        body = json.loads(kwargs["body_bytes"].decode("utf-8"))
        prompt = "\n".join(
            str(message.get("content", ""))
            for message in body.get("messages", ())
            if isinstance(message, dict)
        )
        domain = (
            "lean_proof"
            if "Theorem payload digest:" in prompt
            else "factorization"
        )
        delegate = (
            self.lean_delegate
            if domain == "lean_proof"
            else self.factorization_delegate
        )
        response = delegate.post_chat_completion(**kwargs)
        response.body["model"] = body["model"]
        self.calls.append({"domain": domain, "body": body})
        return response


def test_factorization_direct_sources_ignore_synthetic_fault_attempts_only() -> None:
    def artifact_ref(name: str) -> dict[str, object]:
        return {
            "schema_version": "ArtifactRef.v1",
            "artifact_id": name,
            "artifact_type": "test",
            "uri": f"artifact://{name}",
            "content_hash": "sha256:" + "1" * 64,
            "size_bytes": 1,
            "media_type": "application/json",
            "artifact_schema_id": "test.v1",
            "artifact_schema_version": "v1",
            "source": {},
            "metadata": {},
            "created_at": "2026-08-08T00:00:00Z",
        }

    actual = SimpleNamespace(
        provider_attempt_count=1,
        request_ref=artifact_ref("request"),
        raw_output_ref=artifact_ref("raw"),
        parse_failure_ref=None,
        provenance_ref=artifact_ref("provenance"),
        usage_ref=artifact_ref("usage"),
        model_execution_record_ref=artifact_ref("model"),
        fault_injection_ref=None,
    )
    synthetic_fault = SimpleNamespace(
        provider_attempt_count=1,
        request_ref=artifact_ref("fault-request"),
        raw_output_ref=None,
        parse_failure_ref=None,
        provenance_ref=None,
        usage_ref=artifact_ref("fault-usage"),
        model_execution_record_ref=artifact_ref("fault-model"),
        fault_injection_ref=artifact_ref("fault-record"),
    )
    zero_provider_terminal = SimpleNamespace(
        provider_attempt_count=0,
        request_ref=None,
        raw_output_ref=None,
        parse_failure_ref=None,
        provenance_ref=None,
        usage_ref=None,
        model_execution_record_ref=None,
        fault_injection_ref=None,
    )

    sources = factorization_paper_adapter._native_online_provider_sources(
        (actual, synthetic_fault, zero_provider_terminal)
    )

    assert all(len(refs) == 1 for refs in sources.values())
    assert sources["request_body"][0].artifact_id == "request"
    assert sources["provenance"][0].artifact_id == "provenance"
    assert sources["model_record"][0].artifact_id == "model"

    incomplete_actual = SimpleNamespace(
        **{
            **vars(actual),
            "provenance_ref": None,
        }
    )
    with pytest.raises(ValueError, match="actual provider evidence is incomplete"):
        factorization_paper_adapter._native_online_provider_sources(
            (incomplete_actual, synthetic_fault)
        )
    missing_model_actual = SimpleNamespace(
        **{
            **vars(actual),
            "model_execution_record_ref": None,
        }
    )
    with pytest.raises(ValueError, match="actual provider evidence is incomplete"):
        factorization_paper_adapter._native_online_provider_sources(
            (actual, missing_model_actual)
        )


def test_dual_domain_smoke_identity_and_plan_freeze_two_canonical_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    profile_path = (
        "benchmarks/paper/paper_smoke_dual_domain_profile.v1.json"
    )
    provider_path = "benchmarks/paper/exp1_baseline_provider_config.v3.json"
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    identity_root = tmp_path / "identity"
    assert main(
        [
            "--output-root",
            str(identity_root),
            "--smoke-profile",
            profile_path,
            "--ai-api-config",
            provider_path,
            "--unlimited-budget",
            "--smoke-identity-only",
        ]
    ) == 0
    assert not identity_root.exists()
    identity = json.loads(capsys.readouterr().out)
    semantics = identity["preregistered_semantics"]
    assert semantics["suite_id"] == "paper_smoke_dual_domain_v1"
    assert semantics["experiment_ids"] == ["exp1_real_ai_feasibility"]
    assert semantics["direct_root_runs"] == 2
    assert semantics["actual_scheduled_root_runs"] == 2
    assert semantics["worker_counts"] == [10]
    assert semantics["paper_eligible"] is False

    plan_root = tmp_path / "plan"
    assert main(
        [
            "--output-root",
            str(plan_root),
            "--smoke-profile",
            profile_path,
            "--ai-api-config",
            provider_path,
            "--unlimited-budget",
            "--plan-only",
        ]
    ) == 0
    capsys.readouterr()
    plan = json.loads(
        (plan_root / "smoke_execution_plan.json").read_text(encoding="utf-8")
    )
    assert plan["direct_root_run_count"] == 2
    assert len(plan["items"]) == 2
    assert {item["case_id"] for item in plan["items"]} == {
        "factor_v2_easy_109",
        "lean_easy_01",
    }
    assert {
        item["condition_selector"]["domain"] for item in plan["items"]
    } == {"factorization", "lean_proof"}
    assert {
        case_id
        for case_ids in plan["root_case_filter"].values()
        for case_id in case_ids
    } == {"factor_v2_easy_109", "lean_easy_01"}
    planned_suite = json.loads(
        (plan_root / "suite_manifest.json").read_text(encoding="utf-8")
    )
    assert planned_suite["run_count"] == 2
    assert planned_suite["formal"] is False
    assert planned_suite["pilot_only"] is True
    assert planned_suite["regression_only"] is True
    assert planned_suite["paper_eligible"] is False


@pytest.mark.parametrize("experiment_number", (1, 2, 3, 4))
def test_matrix8_profile_path_is_blocked_before_provider_dispatch(
    experiment_number: int,
    tmp_path: Path,
) -> None:
    provider_calls: list[dict[str, object]] = []

    def forbidden_transport(**kwargs):
        provider_calls.append(kwargs)
        raise AssertionError("legacy matrix8 must be rejected before provider dispatch")

    exit_code = main(
        [
            "--output-root",
            str(tmp_path / f"matrix8-exp{experiment_number}"),
            "--smoke-profile",
            (
                "benchmarks/paper/"
                f"paper_smoke_exp{experiment_number}_matrix8_profile.v1.json"
            ),
            "--smoke-identity-only",
        ],
        gate_c_transport=forbidden_transport,
    )

    assert exit_code == 3
    assert provider_calls == []


def test_smoke_plan_only_counts_dispatch_conditions_not_root_items(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    written: dict[str, object] = {}
    monkeypatch.setattr(
        paper_cli,
        "_write_smoke_documents_fail_closed",
        lambda *, output_root, documents: written.update(documents),
    )
    profile = SimpleNamespace(
        suite_id="two-roots-one-condition",
        experiment_ids=("exp1_real_ai_feasibility",),
        to_dict=lambda: {"suite_id": "two-roots-one-condition"},
    )
    execution_plan = SimpleNamespace(
        items=(object(), object()),
        dispatch_plans=(
            SimpleNamespace(conditions=(object(),)),
        ),
        direct_root_run_count=2,
        to_dict=lambda: {"direct_root_run_count": 2},
    )

    paper_cli._write_smoke_plan_only(
        output_root=tmp_path,
        profile=profile,
        execution_plan=execution_plan,
        budget={},
        lean_3x3_matrix={},
        model_endpoint_cohort_preflight=None,
    )

    suite = written["suite_manifest.json"]
    assert isinstance(suite, dict)
    assert suite["condition_count"] == 1
    assert suite["run_count"] == 2


def test_dual_domain_smoke_capturing_transport_runs_protocol_and_lean_checker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = load_exp1_pilot_profile(
        "benchmarks/paper/exp1_minimal_pilot_profile.v3.json"
    )
    entry = profile.source_provider_config.entries[0]
    monkeypatch.setenv(entry.api_key_env, "dual-domain-smoke-capture-key")
    transport = _DualDomainCLICapturingTransport()

    coordinator_case_ids: list[str] = []
    coordinator_lock = Lock()
    original_run_root = lean_paper_adapter.ProtocolRunCoordinator.run_root

    def recording_run_root(self, request):
        with coordinator_lock:
            coordinator_case_ids.append(str(request.root_input["case_id"]))
        return original_run_root(self, request)

    monkeypatch.setattr(
        lean_paper_adapter.ProtocolRunCoordinator,
        "run_root",
        recording_run_root,
    )
    checker_calls: list[tuple[object, object, dict[str, object]]] = []
    checker_lock = Lock()
    original_checker = lean_paper_adapter.check_lean_proof

    def recording_checker(request, *, artifact_store, environment_manifest):
        result = original_checker(
            request,
            artifact_store=artifact_store,
            environment_manifest=environment_manifest,
        )
        theorem_payload = json.loads(
            artifact_store.read_bytes(request.theorem_payload_ref).decode("utf-8")
        )
        with checker_lock:
            checker_calls.append((request, result, theorem_payload))
        return result

    monkeypatch.setattr(
        lean_paper_adapter,
        "check_lean_proof",
        recording_checker,
    )

    output_root = tmp_path / "dual-domain-smoke"
    exit_code = main(
        [
            "--output-root",
            str(output_root),
            "--smoke-profile",
            "benchmarks/paper/paper_smoke_dual_domain_profile.v1.json",
            "--ai-api-config",
            "benchmarks/paper/exp1_baseline_provider_config.v3.json",
            "--real-transport",
            "--unlimited-budget",
        ],
        gate_c_transport=transport,
        gate_c_ai_api_configs={
            profile.model_endpoint_identity.provider_config_id: (
                profile.source_provider_config
            )
        },
    )

    assert exit_code == 0
    assert {call["domain"] for call in transport.calls} == {
        "factorization",
        "lean_proof",
    }
    assert set(coordinator_case_ids) == {
        "factor_v2_easy_109",
        "lean_easy_01",
    }
    smoke_checker_calls = [
        call
        for call in checker_calls
        if (
            call[2]["theorem_id"] == "lean_theorem:lean_easy_01"
            or str(call[2]["theorem_id"]).startswith(
                "lean_theorem:lean_easy_01:"
            )
        )
        and not call[0].request_id.startswith("task14_golden_checker_")
    ]
    assert smoke_checker_calls
    assert len(smoke_checker_calls) == 3
    assert [
        call[0].checker_mode.value for call in smoke_checker_calls
    ].count("child_proof") == 2
    assert [
        call[0].checker_mode.value for call in smoke_checker_calls
    ].count("merge_proof") == 1
    for request, report, theorem_payload in smoke_checker_calls:
        assert report.request_id == request.request_id
        assert report.status.value == "accepted"
        assert theorem_payload["schema_version"] == "lean_proof.theorem_payload.v1"
        assert theorem_payload["theorem_id"] == "lean_theorem:lean_easy_01" or str(
            theorem_payload["theorem_id"]
        ).startswith("lean_theorem:lean_easy_01:")
        assert theorem_payload["payload_digest"].startswith("sha256:")

    required_outputs = (
        "metrics/smoke_summary.json",
    )
    assert all(
        (output_root / relative_path).is_file()
        for relative_path in required_outputs
    )

    current_paths = sorted(
        (
            output_root
            / "experiments"
            / "exp1_real_ai_feasibility"
            / "runs"
        ).glob("*/0/CURRENT.json")
    )
    assert len(current_paths) == 2
    generation_roots = []
    for current_path in current_paths:
        current = json.loads(current_path.read_text(encoding="utf-8"))
        generation_root = (
            current_path.parent
            / ".generations"
            / current["generation_id"]
        )
        generation_roots.append(generation_root)
        assert all(
            (generation_root / relative_path).is_file()
            for relative_path in (
                "per_attempt_results.jsonl",
                "artifacts/artifact_index.jsonl",
            )
        )

    attempts = [
        json.loads(line)
        for generation_root in generation_roots
        for line in (generation_root / "per_attempt_results.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    provider_attempts = [
        attempt
        for attempt in attempts
        if attempt.get("provider_attempt_count", 0) > 0
    ]
    assert provider_attempts
    assert {attempt["condition_id"] for attempt in provider_attempts} == {
        "exp1_factorization_easy_w10_r0",
        "exp1_lean_simple_pure_logic_w10_r0",
    }
    assert all(
        type(attempt.get("latency_ms")) is int
        and attempt["latency_ms"] >= 0
        and type(attempt.get("prompt_tokens")) is int
        and attempt["prompt_tokens"] >= 0
        and type(attempt.get("completion_tokens")) is int
        and attempt["completion_tokens"] >= 0
        and type(attempt.get("total_tokens")) is int
        and attempt["total_tokens"] > 0
        and attempt["total_tokens"]
        == attempt["prompt_tokens"] + attempt["completion_tokens"]
        and isinstance(attempt.get("cost_estimate"), (int, float))
        and not isinstance(attempt["cost_estimate"], bool)
        and attempt["cost_estimate"] >= 0
        and attempt["cost_estimate_currency"] == "CNY"
        and attempt["cost_estimate_status"] == "estimated"
        for attempt in provider_attempts
    )

    artifact_records: dict[tuple[str, str], dict[str, object]] = {}
    for generation_root in generation_roots:
        for line in (
            generation_root / "artifacts" / "artifact_index.jsonl"
        ).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            artifact_records[
                (record["artifact_id"], record["content_hash"])
            ] = record

    def load_json_artifact(ref: dict[str, object]) -> dict[str, object]:
        assert ref["schema_version"] == "ArtifactRef.v1"
        key = (str(ref["artifact_id"]), str(ref["content_hash"]))
        record = artifact_records[key]
        artifact_path = output_root / str(record["path"])
        return json.loads(artifact_path.read_text(encoding="utf-8"))

    expected_ref_schemas = {
        "raw_output_ref": ("phase7.raw_model_output", "v2"),
        "usage_ref": ("tokenshare.paper_ai_usage", "v1"),
    }
    for attempt in provider_attempts:
        bodies: dict[str, dict[str, object]] = {}
        for field_name, (schema_id, schema_version) in expected_ref_schemas.items():
            ref = attempt[field_name]
            assert ref["artifact_schema_id"] == schema_id
            assert ref["artifact_schema_version"] == schema_version
            bodies[field_name] = load_json_artifact(ref)

        usage = bodies["usage_ref"]
        assert usage["schema_version"] == "tokenshare.paper_ai_usage.v1"
        assert usage["prompt_tokens"] == attempt["prompt_tokens"]
        assert usage["completion_tokens"] == attempt["completion_tokens"]
        assert usage["total_tokens"] == attempt["total_tokens"]
        assert usage["cost_estimate"] == attempt["cost_estimate"]
        assert usage["currency"] == attempt["cost_estimate_currency"]
        assert usage["cost_estimate_status"] == attempt["cost_estimate_status"]
        assert usage["provider_attempt_count"] == attempt["provider_attempt_count"]

        raw_output = bodies["raw_output_ref"]
        assert raw_output["schema_version"] == "phase7.raw_model_output.v2"
        assert raw_output["request_id"] == usage["request_id"]
        assert raw_output["usage"] == {
            "prompt_tokens": attempt["prompt_tokens"],
            "completion_tokens": attempt["completion_tokens"],
            "total_tokens": attempt["total_tokens"],
        }

    metrics = json.loads(
        (output_root / "metrics" / "smoke_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert metrics["expected_root_count"] == 2
    assert metrics["row_count"] == 2
    assert metrics["started_root_count"] == 2
    assert metrics["completed_root_count"] == 2
    assert metrics["failed_or_blocked_root_count"] == 0
    assert metrics["correctness_numerator"] == 2
    assert metrics["correctness_denominator"] == 2
    assert metrics["correctness_rate"] == pytest.approx(1.0)
    assert metrics["correctness_missing_count"] == 0
    assert metrics["completion_numerator"] == 2
    assert metrics["completion_denominator"] == 2
    assert metrics["completion_rate"] == pytest.approx(1.0)
    assert {row["domain"] for row in metrics["rows"]} == {
        "factorization",
        "lean_proof",
    }
    assert all(row["accepted_validity"] is True for row in metrics["rows"])
    assert all(
        type(row["wall_clock_ms"]) is int and row["wall_clock_ms"] >= 0
        for row in metrics["rows"]
    )
    # capturing transport 的 provider attempts 仍保留原始 usage；smoke metrics
    # 只投影 paper-ineligible source rows，因此这里明确冻结为零而不做错误聚合。
    assert metrics["provider_attempt_count"] == 0
    assert metrics["provider_latency_ms"] == 0.0
    assert metrics["provider_latency_sample_size"] == 0
    assert metrics["provider_latency_missing_count"] == 0
    assert metrics["provider_latency_unavailable_reason"] == (
        "no_provider_attempts"
    )
    assert metrics["total_tokens"] == 0
    assert metrics["cost_estimate"] == 0.0
    assert metrics["formal"] is False
    assert metrics["pilot_only"] is True
    assert metrics["regression_only"] is True
    assert metrics["paper_eligible"] is False


class _ConcurrentSmokeTransport:
    tokenshare_offline_capturing_transport = True

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.observed_peak = 0
        self._active = 0
        self._lock = Lock()

    @property
    def provider_calls_made(self) -> int:
        return len(self.calls)

    def post_chat_completion(self, **kwargs):
        with self._lock:
            self._active += 1
            self.observed_peak = max(self.observed_peak, self._active)
        try:
            time.sleep(0.02)
            with self._lock:
                self.calls.append(
                    {
                        "body_bytes": kwargs["body_bytes"],
                        "normalized_absolute_endpoint": kwargs[
                            "normalized_absolute_endpoint"
                        ],
                    }
                )
            return SimpleNamespace(status_code=200, body={}, text="{}")
        finally:
            with self._lock:
                self._active -= 1


class _FailOnceSmokeTransport:
    tokenshare_offline_capturing_transport = True

    def __init__(self) -> None:
        self._failed = False

    def post_chat_completion(self, **_kwargs):
        if not self._failed:
            self._failed = True
            raise RuntimeError("provider exploded")
        return SimpleNamespace(status_code=200, body={}, text="{}")


def _install_exp5_v3_cli_test_profile(
    monkeypatch: pytest.MonkeyPatch,
):
    preflight = _exp5_v3_cli_preflight()
    monkeypatch.setattr(
        paper_cli,
        "_model_endpoint_cohort_preflight_for_suite",
        lambda **_kwargs: (
            preflight,
            {"cohort_id": PAPER_MODEL_ENDPOINT_COHORT_V3_ID},
        ),
    )
    full_profile = paper_cli.load_paper_smoke_profile(
        "benchmarks/paper/paper_smoke_profile.v3.json"
    )
    exp5_experiment_id = "exp5_real_ai_model_endpoint_comparison"
    profile = replace(
        full_profile,
        suite_id="paper_smoke_v3_exp5_resume_test",
        experiment_ids=(exp5_experiment_id,),
        expected_root_runs=8,
        items=tuple(
            item
            for item in full_profile.items
            if item.experiment_id == exp5_experiment_id
        ),
    )
    monkeypatch.setattr(
        paper_cli,
        "load_paper_smoke_profile",
        lambda _path: profile,
    )
    baseline = load_exp1_pilot_profile(
        "benchmarks/paper/exp1_minimal_pilot_profile.v3.json"
    )
    return profile, preflight, baseline


def _invoke_exp5_v3_cli(
    *,
    tmp_path: Path,
    transport: object,
    baseline,
    resume: bool,
) -> int:
    args = [
        "--output-root",
        str(tmp_path),
        "--smoke-profile",
        "benchmarks/paper/paper_smoke_profile.v3.json",
        "--real-transport",
        "--unlimited-budget",
    ]
    if resume:
        args.append("--resume")
    return main(
        args,
        gate_c_transport=transport,
        gate_c_ai_api_configs={
            baseline.model_endpoint_identity.provider_config_id: (
                baseline.source_provider_config
            )
        },
    )


def _completed_smoke_result(execution_plan, budget) -> PaperSuiteResult:
    return _smoke_result(execution_plan, budget, status=PaperStatus.COMPLETED)


def _smoke_result(execution_plan, budget, *, status: PaperStatus) -> PaperSuiteResult:
    return PaperSuiteResult(
        suite_id=execution_plan.suite_id,
        status=status,
        output_root=execution_plan.output_root,
        started_at="2026-07-30T00:00:00Z",
        ended_at="2026-07-30T00:00:01Z",
        experiment_ids=execution_plan.experiment_ids,
        condition_count=len(execution_plan.items),
        run_count=execution_plan.direct_root_run_count,
        task_count=execution_plan.direct_root_run_count,
        provider_attempt_count=0,
        total_tokens=0,
        total_cost_estimate=0.0,
        paper_eligible=False,
        eligibility_report_ref=None,
        budget_ref={"budget_digest": budget.budget_digest},
        metrics_refs=(),
        audit_refs=(),
        error_summary=(),
    )


def _call_exp5_members(*, transport, preflight: dict, member_ids) -> None:
    for member_id in member_ids:
        member_plan = preflight["member_plans"][member_id]
        entry = SimpleNamespace(
                entry_id=member_plan["selected_entry_id"],
                provider="siliconflow",
                model=member_plan["provider_model_id"],
                base_url="https://api.example.test/v1",
                endpoint="/chat/completions",
            )
        transport.post_chat_completion(
            api_key="test-double-only",
            **prepared_wire_kwargs(
                entry,
                {"member_id": member_id, "model": entry.model, "messages": []},
                provider_family="siliconflow",
            ),
            timeout_seconds=1,
        )


def _exp5_v3_schedule_test_binding() -> tuple[dict, dict[str, str]]:
    member_ids = ("member-a", "member-b", "member-c", "member-d")
    return (
        {
            "profile_digest": "sha256:" + "1" * 64,
            "execution_plan_digest": "sha256:" + "2" * 64,
            "sequence_plan_digest": "sha256:" + "3" * 64,
            "provider_namespace": "executor_ai_api_exp5_siliconflow_v3",
            "max_in_flight_global": 3,
            "expected_member_order": list(member_ids),
            "expected_condition_ids": [
                f"condition-{member_id}-{repeat_index}"
                for member_id in member_ids
                for repeat_index in range(2)
            ],
        },
        {f"entry-{member_id}": member_id for member_id in member_ids},
    )


def _bound_entry(member_id_by_entry_id: dict[str, str], member_id: str):
    entry_id = next(
        entry_id
        for entry_id, mapped_member_id in member_id_by_entry_id.items()
        if mapped_member_id == member_id
    )
    return SimpleNamespace(
        entry_id=entry_id,
        provider="siliconflow",
        model=member_id,
        base_url="https://api.example.test/v1",
        endpoint="/chat/completions",
    )


def _call_bound_members(
    transport,
    member_id_by_entry_id: dict[str, str],
    member_ids,
) -> None:
    for member_id in member_ids:
        transport.post_chat_completion(
            api_key="test-double-only",
            **prepared_wire_kwargs(
                _bound_entry(member_id_by_entry_id, member_id),
                {"member_id": member_id, "model": member_id, "messages": []},
                provider_family="siliconflow",
            ),
            timeout_seconds=1,
        )


def _exp5_v3_cli_preflight() -> dict:
    cohort_digest = "sha256:" + "c" * 64
    comparable_controls = {
        "temperature": 0.0,
        "top_p": 1.0,
        "stream": False,
        "timeout_seconds": 600,
        "max_tokens": 32_768,
        "max_provider_attempts": 1,
        "domain_contracts": {
            "factorization": {
                "prompt_profile": "factorization.bounded_range_prompt.v1",
                "parser_id": "factorization.range_result.parser.v1",
                "plugin_version": "0.1.0",
            },
            "lean_proof": {
                "prompt_profile": "lean_proof.proof_candidate_prompt.v1",
                "parser_id": "lean_proof.proof_candidate.parser.v1",
                "plugin_version": "0.1.0",
            },
        },
    }
    member_plans: dict[str, dict] = {}
    for index, member_id in enumerate(
        PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS,
        start=1,
    ):
        expected = PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBERS[member_id]
        source_digest = "sha256:" + f"{index}" * 64
        entry_id = f"{member_id}_entry"
        reasoning_controls = dict(expected["request_overrides"])
        identity = PaperModelEndpointIdentity(
            model_cohort_id=PAPER_MODEL_ENDPOINT_COHORT_V3_ID,
            model_cohort_digest=cohort_digest,
            cohort_member_id=member_id,
            provider_config_id="executor_ai_api_exp5_siliconflow_v3",
            selected_entry_id=entry_id,
            provider_family="siliconflow",
            provider_model_id=str(expected["provider_model_id"]),
            reasoning_profile_id=str(expected["reasoning_profile_id"]),
            effective_reasoning_controls=reasoning_controls,
            source_provider_config_digest=source_digest,
        )
        pricing_snapshot = dict(expected["pricing"])
        member_plans[member_id] = {
            "schema_version": "tokenshare.paper_model_endpoint_member_plan.v1",
            "status": "planned",
            "blocked_reasons": [],
            "cohort_id": PAPER_MODEL_ENDPOINT_COHORT_V3_ID,
            "model_cohort_digest": cohort_digest,
            "cohort_member_id": member_id,
            "provider_config_id": "executor_ai_api_exp5_siliconflow_v3",
            "selected_entry_id": entry_id,
            "provider_family": "siliconflow",
            "provider_model_id": expected["provider_model_id"],
            "reasoning_profile_id": expected["reasoning_profile_id"],
            "source_provider_config_digest": source_digest,
            "model_endpoint_identity_digest": (
                identity.model_endpoint_identity_digest
            ),
            "endpoint_identity": identity.to_dict(),
            "request_controls": {
                "schema_version": "tokenshare.paper_exp5_request_controls.v1",
                "comparable": comparable_controls,
                "comparable_digest": digest_json(comparable_controls),
                "provider_specific_reasoning": reasoning_controls,
                "provider_specific_reasoning_digest": digest_json(
                    reasoning_controls
                ),
            },
            "pricing_snapshot": pricing_snapshot,
            "pricing_snapshot_digest": digest_json(pricing_snapshot),
            "pricing_freshness": {
                "accessed_at": EXP5_PRICING_FRESHNESS_AS_OF,
                "pricing_freshness_as_of": EXP5_PRICING_FRESHNESS_AS_OF,
                "age_days": 0,
                "max_age_days": EXP5_PRICING_MAX_AGE_DAYS,
                "status": "fresh",
            },
        }
    return {
        "schema_version": "tokenshare.paper_model_endpoint_cohort_preflight.v1",
        "status": "planned",
        "paper_eligible_possible": True,
        "blocked_reason": None,
        "ineligibility_reasons": [],
        "provider_calls_made": 0,
        "model_policy": "fixed_entry",
        "cohort_id": PAPER_MODEL_ENDPOINT_COHORT_V3_ID,
        "model_cohort_digest": cohort_digest,
        "expected_member_ids": list(PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS),
        "member_plans": member_plans,
        "request_controls_snapshot": comparable_controls,
        "request_controls_snapshot_digest": digest_json(comparable_controls),
        "pricing_freshness_authority": {
            "schema_version": EXP5_PRICING_FRESHNESS_AUTHORITY_SCHEMA_VERSION,
            "pricing_freshness_as_of": EXP5_PRICING_FRESHNESS_AS_OF,
            "max_age_days": EXP5_PRICING_MAX_AGE_DAYS,
        },
    }


def test_smoke_authority_rejects_full_scope_and_preserves_ineligibility(
    tmp_path: Path,
) -> None:
    profile = paper_smoke.load_paper_smoke_profile(
        Path("benchmarks/paper/paper_smoke_exp5_profile.v3.json")
    )
    execution_plan = paper_smoke.PaperSmokeExecutionPlan(
        suite_id=profile.suite_id,
        profile_digest=profile.profile_digest,
        catalog_id=profile.catalog_id,
        catalog_version=profile.catalog_version,
        catalog_digest=profile.catalog_digest,
        output_root=tmp_path.resolve(strict=False).as_posix(),
        experiment_ids=profile.experiment_ids,
        items=(),
        dispatch_plans=(),
        root_case_filter={},
        baseline_policy=profile.baseline_policy,
    )
    kwargs = {
        "authorized_plan_digest": "sha256:" + "a" * 64,
        "profile": profile,
        "execution_plan": execution_plan,
        "catalog_manifest": object(),
        "budget": SimpleNamespace(budget_digest="sha256:" + "b" * 64),
        "ai_api_configs": {"siliconflow": object()},
        "transport": object(),
        "hard_limits": {},
        "resume": False,
        "launch_manifest": {"scope": "exp5_capability_smoke"},
    }

    authority = paper_smoke.build_paper_smoke_service_authority(
        scope="exp5_capability_smoke",
        **kwargs,
    )

    assert authority.inventory_digest == execution_plan.execution_plan_digest
    assert authority.keyword_arguments["profile"].paper_eligible is False
    assert paper_smoke.smoke_execution_classification()["paper_eligible"] is False
    with pytest.raises(ValueError, match="full Exp5 scope"):
        paper_smoke.build_paper_smoke_service_authority(
            scope="exp5_full_online",
            **kwargs,
        )
    with pytest.raises(ValueError, match="not-paper-eligible"):
        paper_smoke.build_paper_smoke_service_authority(
            scope="exp5_capability_smoke",
            **{**kwargs, "profile": replace(profile, paper_eligible=True)},
        )


@pytest.mark.parametrize(
    ("command", "experiment_id", "provider_config_id"),
    (
        (
            "run-exp1-online",
            "exp1_real_ai_feasibility",
            "exp1_baseline_deepseek",
        ),
        (
            "run-exp5-online",
            "exp5_real_ai_model_endpoint_comparison",
            "siliconflow",
        ),
    ),
)
def test_pipeline_formal_authority_recomputes_actual_plan_budget_and_configs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    experiment_id: str,
    provider_config_id: str,
) -> None:
    from tokenshare.experiments.paper_pipeline_profile import (
        load_paper_pipeline_profile,
    )

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    provider_calls: list[str] = []
    network_calls: list[str] = []

    def provider_bomb(*_args, **_kwargs):
        provider_calls.append("provider")
        raise AssertionError("formal authority construction must not call provider")

    def network_bomb(*_args, **_kwargs):
        network_calls.append("network")
        raise AssertionError("formal authority construction must not open network")

    monkeypatch.setattr(socket, "create_connection", network_bomb)
    monkeypatch.setattr(socket, "getaddrinfo", network_bomb)
    if command == "run-exp5-online":
        monkeypatch.setattr(
            paper_cli,
            "_ProviderFamilyTransportRouter",
            lambda **_kwargs: provider_bomb,
        )
    profile = load_paper_pipeline_profile()

    authority = paper_cli.build_epd027_formal_service_authority(
        command=command,
        profile=profile,
        output_root=tmp_path / command,
    )
    kwargs = authority.keyword_arguments
    assert authority.plan_digest == profile.offline_approval.approved_plan_digest
    assert authority.budget_digest == kwargs["budget"].budget_digest
    assert authority.inventory_digest.startswith("sha256:")
    assert [plan.experiment_id for plan in kwargs["dispatch_plans"]] == [
        experiment_id
    ]
    assert provider_config_id in kwargs["ai_api_configs"]
    assert kwargs["real_transport"] is True
    assert kwargs["budget_approval"] == {
        "approval_mode": "paid_receipt",
        "budget_digest": authority.budget_digest,
    }
    assert provider_calls == []
    assert network_calls == []
    if command == "run-exp5-online":
        config = kwargs["ai_api_configs"]["siliconflow"]
        preflight = kwargs["ai_api_configs"][
            paper_cli.APPROVED_ENDPOINT_BINDINGS_KEY
        ][experiment_id]
        assert kwargs["transport"] is provider_bomb
        assert all(
            member["source_provider_config_digest"] == config.config_digest
            and member["api_key_env"] == "SILICONFLOW_API_KEY"
            for member in preflight["member_plans"].values()
        )


def test_pipeline_online_checks_authority_binds_496_plan_and_l3_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments.paper_online_checks import (
        freeze_paper_online_checks_plan,
    )
    from tokenshare.experiments.paper_pipeline_profile import (
        load_paper_pipeline_profile,
    )

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    profile = load_paper_pipeline_profile()
    plan = freeze_paper_online_checks_plan()

    authority = paper_cli.build_epd027_formal_service_authority(
        command="run-online-checks",
        profile=profile,
        output_root=tmp_path / "online-checks",
    )
    assert authority.inventory_digest == plan.plan_digest
    assert authority.budget_digest == profile.budget_digest
    assert plan.capability_calls_exact + plan.exp2_calls_upper + plan.exp3_calls_upper == 496
    assert [item.experiment_id for item in authority.keyword_arguments["dispatch_plans"]] == [
        "exp1_real_ai_feasibility",
        "exp2_real_ai_scalability",
        "exp3_real_ai_fault_recovery",
    ]
    assert [
        len(item.conditions)
        for item in authority.keyword_arguments["dispatch_plans"]
    ] == [2, 6, 2]
    root_filter = authority.keyword_arguments["root_case_filter"]
    assert sum(len(case_ids) for case_ids in root_filter.values()) == 28
    assert authority.keyword_arguments["hard_limits"][
        "max_total_provider_attempts"
    ] == 516
    assert "online_root_callback_factory" not in authority.keyword_arguments
    assert authority.keyword_arguments["real_transport"] is True


def test_pipeline_capability_smoke_authority_uses_canonical_exp5_subset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments.paper_pipeline_profile import (
        load_paper_pipeline_profile,
    )

    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    profile = load_paper_pipeline_profile()

    authority = paper_cli.build_epd027_capability_smoke_service_authority(
        profile=profile,
        output_root=tmp_path / "capability-smoke",
    )

    execution_plan = authority.keyword_arguments["execution_plan"]
    assert authority.scope == "exp5_capability_smoke"
    assert authority.plan_digest == profile.offline_approval.approved_plan_digest
    assert authority.inventory_digest == execution_plan.execution_plan_digest
    assert authority.budget_digest == profile.budget_digest
    assert authority.keyword_arguments["budget"].budget_digest.startswith("sha256:")
    assert authority.keyword_arguments["profile"].paper_eligible is False
    assert authority.keyword_arguments["real_transport"] is True
    root_callback_factory = authority.keyword_arguments[
        "online_root_callback_factory"
    ]
    assert root_callback_factory.serialize_roots is False
    assert callable(root_callback_factory)


@pytest.mark.parametrize(
    ("command", "selected_experiments"),
    (
        ("run-exp1-online", ("exp1",)),
        ("run-exp5-online", ("exp5",)),
    ),
)
def test_default_formal_authority_producer_exposes_typed_gate_components(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    selected_experiments: tuple[str, ...],
) -> None:
    from tokenshare.experiments.paper_formal_gate import (
        PaperGatePrerequisiteEnvelope,
        PaperGateSelectionEnvelope,
    )
    from tokenshare.experiments.paper_pipeline_profile import (
        load_paper_pipeline_profile,
    )

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    authority = paper_cli.build_epd027_formal_service_authority(
        command=command,
        profile=load_paper_pipeline_profile(),
        output_root=tmp_path / command,
    )

    assert isinstance(authority.gate_selection, PaperGateSelectionEnvelope)
    assert authority.gate_selection.selected_experiments == selected_experiments
    assert isinstance(authority.gate_prerequisites, PaperGatePrerequisiteEnvelope)
    assert authority.gate_prerequisites.l1_attestation.level == "L1"
    assert authority.gate_prerequisites.l2_attestation.level == "L2"
    assert callable(authority.publication_gate_factory)


def test_default_trace_authority_binds_validated_full_bank_prerequisite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.executors.response_bank import OBJECT_ROLES, ResponseBankManifest
    from tokenshare.experiments import paper_response_bank
    from tokenshare.experiments.paper_formal_gate import (
        CompleteFullBankPrerequisite,
        PaidAuthorizationScopeAuthority,
        SelectedPaidAuthorizationBinding,
        formal_execution_gate,
    )
    from tokenshare.experiments.paper_paid_authorization import (
        PaidAuthorizationValidation,
        PaidExecutionReceipt,
        PaidOutputBindingMarker,
    )
    from tokenshare.experiments.paper_pipeline_profile import (
        load_paper_pipeline_profile,
    )

    profile = load_paper_pipeline_profile()
    inventory_digest = "sha256:" + "b" * 64
    entry_ids = ("inventory-entry-a", "inventory-entry-b")
    manifest = ResponseBankManifest.create(
        bank_root_id="test-bank-root",
        profile_digest=profile.profile_digest,
        budget_digest="sha256:" + "c" * 64,
        inventory_digest=inventory_digest,
        provider_config_digest="sha256:" + "d" * 64,
        entry_ids=entry_ids,
        object_role_schema=OBJECT_ROLES,
        terminal_entry_count=len(entry_ids),
        created_by_paid_receipt_digest="sha256:" + "e" * 64,
    )
    semantic_inventory = SimpleNamespace(
        rows=tuple(
            SimpleNamespace(inventory_entry_id=entry_id) for entry_id in entry_ids
        )
    )
    bundle = SimpleNamespace(
        authorized_plan_digest=profile.offline_approval.approved_plan_digest,
        profile_digest=profile.profile_digest,
        inventory_digest=inventory_digest,
        semantic_inventory_plan=semantic_inventory,
    )
    resolver = SimpleNamespace(
        index=SimpleNamespace(
            manifest=manifest,
            entries=tuple(
                SimpleNamespace(inventory_entry_id=entry_id)
                for entry_id in entry_ids
            ),
        )
    )
    monkeypatch.setattr(
        paper_response_bank,
        "load_acquisition_plan_bundle",
        lambda _root: bundle,
    )
    monkeypatch.setattr(
        paper_response_bank,
        "build_paper_formal_trace_context",
        lambda **_kwargs: SimpleNamespace(inventory_plan=semantic_inventory),
    )

    authority = paper_cli.build_epd027_formal_service_authority(
        command="run-trace",
        profile=profile,
        output_root=tmp_path / "trace",
        plan_bundle_root=tmp_path / "plan-bundle",
        external_bank_resolver=SimpleNamespace(open=lambda: resolver),
    )

    full_bank = authority.gate_prerequisites.full_bank
    assert isinstance(full_bank, CompleteFullBankPrerequisite)
    assert full_bank.manifest is manifest
    assert full_bank.preflight.status == "ready"
    assert authority.gate_selection.selected_experiments == ("exp2", "exp3", "exp4")
    assert formal_execution_gate(
        authority.gate_selection,
        authority.gate_prerequisites,
    ).blocked_reasons == (
        "missing_paid_authority_commitment",
        "missing_paid_authorization:epd027_full_bank_acquisition",
    )

    receipt = PaidExecutionReceipt(
        schema_version="tokenshare.paid_execution_receipt.v1",
        receipt_digest=manifest.created_by_paid_receipt_digest,
        scope="epd027_full_bank_acquisition",
        authorized_plan_digest=profile.offline_approval.approved_plan_digest,
        profile_digest=profile.profile_digest,
        budget_digest=manifest.budget_digest,
        inventory_digest=inventory_digest,
        prompt_admission_profile_digest=profile.prompt_admission_profile_digest,
        selected_experiments=("exp2", "exp3", "exp4"),
        output_root_path_digest="sha256:" + "f" * 64,
        not_before="2026-08-01T00:00:00Z",
        expires_at="2026-08-04T00:00:00Z",
        user_approval_reference="validated-bank-receipt",
    )
    marker = PaidOutputBindingMarker(
        schema_version="tokenshare.paid_output_binding.v1",
        marker_digest="sha256:" + "a" * 64,
        receipt_digest=receipt.receipt_digest,
        authorized_plan_digest=receipt.authorized_plan_digest,
        profile_digest=receipt.profile_digest,
        budget_digest=receipt.budget_digest,
        inventory_digest=receipt.inventory_digest,
        prompt_admission_profile_digest=receipt.prompt_admission_profile_digest,
        output_root_path_digest=receipt.output_root_path_digest,
    )
    binding = SelectedPaidAuthorizationBinding(
        authority=PaidAuthorizationScopeAuthority(
            scope=receipt.scope,
            authorized_plan_digest=receipt.authorized_plan_digest,
            profile_digest=receipt.profile_digest,
            budget_digest=receipt.budget_digest,
            inventory_digest=receipt.inventory_digest,
            prompt_admission_profile_digest=receipt.prompt_admission_profile_digest,
            selected_experiments=receipt.selected_experiments,
            output_root_path_digest=receipt.output_root_path_digest,
        ),
        validation=PaidAuthorizationValidation(
            receipt=receipt,
            marker=marker,
            output_mode="resume",
            authorization_state="reconcile_close_only",
            provider_dispatch_allowed=False,
        ),
    )
    ready_authority = paper_cli.build_epd027_formal_service_authority(
        command="run-trace",
        profile=profile,
        output_root=tmp_path / "trace-ready",
        plan_bundle_root=tmp_path / "plan-bundle",
        external_bank_resolver=SimpleNamespace(open=lambda: resolver),
        paid_authorization_bindings=(binding,),
    )
    assert formal_execution_gate(
        ready_authority.gate_selection,
        ready_authority.gate_prerequisites,
    ).status == "ready"


@pytest.mark.parametrize(
    ("pricing_currency", "drop_usage", "expected_l4_status"),
    (
        pytest.param("CNY", False, "passed", id="complete-cny"),
        pytest.param("USD", False, "blocked", id="wrong-currency"),
        pytest.param("CNY", True, "blocked", id="missing-usage"),
    ),
)
def test_publication_factory_uses_typed_terminal_and_official_evidence_closure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pricing_currency: str,
    drop_usage: bool,
    expected_l4_status: str,
) -> None:
    from tests.experiments.test_factorization_paper_adapter import _v2_condition
    from tests.experiments.test_paper_formal_runner import (
        BUDGET_DIGEST,
        CATALOG_DIGEST,
        ENDPOINT_DIGEST,
        EXPERIMENT_ID,
        MODEL_ENTRY_ID,
        PROVIDER_CONFIG_ID,
        PROVIDER_MODEL_ID,
        _ai_config,
        _budget,
        _dispatch_plan_type,
        _formal_execution_kwargs,
    )
    from tokenshare.experiments import factorization_paper_adapter
    from tokenshare.experiments.factorization_paper_adapter import (
        ScriptedFactorizationRangeTransport,
    )
    from tokenshare.experiments.paper_experiment_contracts import (
        ExperimentSummaryRows,
        FrozenCaseSelection,
        FrozenCaseSelectionBatch,
        FrozenConditionSelectionBinding,
    )
    from tokenshare.experiments.paper_factorization_catalog import (
        generate_factorization_paper_cases,
    )
    from tokenshare.experiments.paper_formal_gate import SelectedFormalMetricsAudit
    from tokenshare.experiments.paper_pipeline_profile import (
        load_paper_pipeline_profile,
    )

    output_root = tmp_path / "official-online-suite"
    config = _ai_config()
    config = replace(
        config,
        entries=[
            replace(
                config.entries[0],
                pricing={
                    **config.entries[0].pricing,
                    "currency": pricing_currency,
                },
            )
        ],
    )
    case = generate_factorization_paper_cases()[0]
    expected_ai_unit_count = int(case["split_params"]["requested_child_count"])
    condition = replace(
        _v2_condition(case),
        condition_id="condition-epd027-online-projector",
        worker_count=1,
        catalog_digest=CATALOG_DIGEST,
        provider_config_id=PROVIDER_CONFIG_ID,
        model_entry_id=MODEL_ENTRY_ID,
        provider_family="siliconflow",
        provider_model_id=PROVIDER_MODEL_ID,
        reasoning_profile_id="default",
        source_provider_config_digest=config.config_digest,
        model_endpoint_identity_digest=ENDPOINT_DIGEST,
    )
    selection = FrozenCaseSelection(
        selection_id="selection-epd027-online-projector",
        experiment_id=EXPERIMENT_ID,
        suite_version="paper_v1",
        catalog_version="catalog-v1",
        domain="factorization",
        paper_difficulty=str(case["difficulty"]),
        topic_family=None,
        ordered_case_ids=(str(case["case_id"]),),
        catalog_digest=CATALOG_DIGEST,
        expected_ai_unit_count=expected_ai_unit_count,
        paper_eligible_required=True,
    )
    plan = _dispatch_plan_type()(
        experiment_id=EXPERIMENT_ID,
        output_root=(output_root / EXPERIMENT_ID).as_posix(),
        conditions=(condition,),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(condition, selection),
        ),
    )

    class Exp1OnlineModule:
        def expand_conditions(self, context):
            raise AssertionError("online closure consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=EXPERIMENT_ID, rows=())

    class ScriptedRealTransport:
        def __init__(self) -> None:
            self.delegate = ScriptedFactorizationRangeTransport()

        def post_chat_completion(self, **call_kwargs):
            response = self.delegate.post_chat_completion(**call_kwargs)
            response.body["model"] = json.loads(
                call_kwargs["body_bytes"].decode("utf-8")
            )["model"]
            if drop_usage:
                response.body.pop("usage", None)
            response.text = json.dumps(response.body, ensure_ascii=False)
            return response

    module_globals = formal_runner.dispatch_paper_condition.__globals__
    original_modules = module_globals["_MODULES"]
    monkeypatch.setitem(
        module_globals,
        "_MODULES",
        ((EXPERIMENT_ID, Exp1OnlineModule()),),
    )
    monkeypatch.setattr(
        factorization_paper_adapter,
        "_validate_real_transport_mode",
        lambda **_kwargs: None,
    )
    monkeypatch.setenv("TOKENSHARE_FORMAL_RUNNER_TEST_KEY", "test-only")
    kwargs = _formal_execution_kwargs(
        tmp_path=output_root,
        config=config,
        plan=plan,
    )
    kwargs.update(
        {
            "catalog_manifest": {
                "catalog_digest": CATALOG_DIGEST,
                "factorization_cases": (case,),
                "lean_cases": (),
                "lean_lemma_graph_cases": (),
            },
            "budget": _budget(
                planned_conditions=1,
                planned_root_runs=1,
                planned_ai_units=expected_ai_unit_count,
            ),
            "budget_approval": {
                "approval_mode": "user_bypassed",
                "budget_digest": BUDGET_DIGEST,
            },
            "transport": ScriptedRealTransport(),
            "real_transport": True,
            "enforce_publication_closure": True,
        }
    )
    terminal = formal_runner.execute_paper_formal_suite(**kwargs)
    terminal = replace(terminal, status=PaperStatus.COMPLETED_WITH_FAILURES)
    monkeypatch.setitem(module_globals, "_MODULES", original_modules)
    loaded = formal_runner.load_paper_traceability_replay_inputs(output_root)
    assert len(loaded.current["canonical_runtime_evidence"]) == 1
    assert len(
        loaded.current["canonical_runtime_evidence"][0].current_provider_object_refs
    ) == 8
    profile = load_paper_pipeline_profile()
    authority = paper_cli.build_epd027_formal_service_authority(
        command="run-exp1-online",
        profile=profile,
        output_root=output_root,
    )

    publication = authority.publication_gate_factory(terminal)

    assert publication.experiment_evidence[0].experiment_id == "exp1"
    route = publication.experiment_evidence[0].routes[0]
    assert route.route_id == "online"
    assert route.evidence_class == "online_real_provider"
    assert route.eligibility_reports[0].paper_eligible is True
    assert route.eligibility_reports[0].source_manifest_complete is False
    assert len(route.selected_audits) == 2
    assert all(
        isinstance(value, SelectedFormalMetricsAudit)
        for value in route.selected_audits
    )
    assert route.selected_audits[0] == route.selected_audits[1]
    if expected_l4_status == "passed":
        assert route.selected_audits[0].publish_blocked_observation_ids == ()
        assert route.selected_audits[0].non_regression is True
        observations_path = (
            output_root
            / "publication_gate"
            / "primary"
            / "selected_metrics_1"
            / "metrics"
            / "paper_metric_observations.v1.jsonl"
        )
        selected_observations = tuple(
            payload
            for line in observations_path.read_text(encoding="utf-8").splitlines()
            if (payload := json.loads(line))["table_id"] == "exp1_feasibility"
        )
        assert len(selected_observations) == 13
        assert all(
            observation["numeric_value"] is not None
            and observation["publish_blocked"] is False
            and observation["direct_result_refs"]
            and observation["current_provider_object_refs"]
            and observation["observation_id"].startswith("sha256:")
            and observation["observation_digest"].startswith("sha256:")
            for observation in selected_observations
        )
        assert publication.l3_attestation.status == "passed"
    else:
        assert route.selected_audits[0].publish_blocked_observation_ids
        assert route.selected_audits[0].non_regression is False
    assert terminal.status == PaperStatus.COMPLETED_WITH_FAILURES
    assert publication.l4_attestation.status == expected_l4_status
    assert publication.replay_results == ()
    with pytest.raises(TypeError, match="PaperSuiteResult"):
        authority.publication_gate_factory({"status": "completed"})
