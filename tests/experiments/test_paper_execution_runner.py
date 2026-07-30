import json
import math
from functools import lru_cache
from pathlib import Path

import pytest

import tokenshare.experiments.paper_report as paper_report
import tokenshare.experiments.paper_runner as paper_runner
from tokenshare.experiments.paper_budget import (
    Exp1PilotProfile,
    load_exp1_pilot_profile,
    plan_exp1_pilot,
)
from tokenshare.experiments.paper_catalog import (
    PaperInputCatalogManifest,
    estimated_ai_units_for_case,
    load_paper_catalogs,
)
from tokenshare.experiments.factorization_paper_adapter import (
    ScriptedFactorizationRangeTransport,
    run_factorization_paper_case,
)
from tokenshare.experiments.lean_paper_adapter import (
    ScriptedLeanPaperProofTransport,
    run_lean_paper_case,
)
from tokenshare.experiments.paper_metrics import recompute_exp1_pilot_metrics
from tokenshare.experiments.paper_models import PaperStatus, digest_json
from tokenshare.experiments.paper_runner import (
    build_exp1_pilot_execution_plan,
    execute_exp1_pilot,
)


FACTOR_CATALOG = Path("benchmarks/paper/factorization_catalog.v1.jsonl")
LEAN_CATALOG = Path("benchmarks/paper/lean_catalog.v1.jsonl")
LEAN_LEMMA_GRAPH_CATALOG = Path(
    "benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl"
)
PILOT_PROFILE = Path("benchmarks/paper/exp1_minimal_pilot_profile.v1.json")


def test_exp1_pilot_execution_plan_has_frozen_conditions_and_ai_unit_order() -> None:
    catalog, profile, budget = _approved_inputs()

    plan = build_exp1_pilot_execution_plan(
        catalog_manifest=catalog,
        pilot_profile=profile,
        budget=budget,
    )

    assert len(plan.conditions) == 8
    assert [task["case_id"] for task in plan.tasks] == [
        "factor_easy_01",
        "factor_medium_01",
        "factor_hard_01",
        "lean_easy_01",
        "lean_v2_medium_lemma_dag_01",
        "lean_v2_medium_function_set_dx_subset_chain_01",
        "lean_v2_medium_induction_nat_predicate_chain_01",
        "lean_v2_hard_frontier_01",
    ]
    assert sum(len(task["ai_units"]) for task in plan.tasks) == 22
    assert [task["task_id"] for task in plan.tasks[:4]] == [
        "paper_factorization_factor_easy_01",
        "paper_factorization_factor_medium_01",
        "paper_factorization_factor_hard_01",
        "paper_lean_lean_easy_01",
    ]
    assert plan.tasks[-1]["execution_status"] == "structured_blocked"
    assert plan.tasks[-1]["ai_units"] == []
    assert plan.tasks[0]["ai_units"] == ["range_0", "range_1"]
    assert plan.tasks[4]["ai_units"] == [
        "pure_medium_leaf_a_01",
        "pure_medium_leaf_ab_01",
        "pure_medium_mid_b_01",
        "pure_medium_leaf_bc_01",
        "pure_medium_root_01",
    ]
    assert [condition.domain for condition in plan.conditions] == [
        "factorization",
        "factorization",
        "factorization",
        "lean_proof",
        "lean_proof",
        "lean_proof",
        "lean_proof",
        "lean_proof",
    ]
    for condition in plan.conditions:
        assert condition.model_policy == "fixed_entry"
        assert condition.model_entry_id == "glm_5_2_exp1_baseline"
        assert condition.provider_config_id == "exp1_baseline_siliconflow"
        assert condition.provider_family == "siliconflow"
        assert condition.provider_model_id == "zai-org/GLM-5.2"
        assert condition.source_provider_config_digest == (
            profile.source_provider_config.config_digest
        )
        assert condition.model_endpoint_identity_digest == (
            profile.model_endpoint_identity.model_endpoint_identity_digest
        )


def test_exp1_pilot_execution_plan_rejects_zero_conditions() -> None:
    catalog, profile, budget = _approved_inputs()
    empty_body = json.loads(json.dumps(profile.body, ensure_ascii=False))
    empty_body["catalog_slice"] = []
    empty_profile = Exp1PilotProfile(
        body=empty_body,
        source_provider_config=profile.source_provider_config,
        model_endpoint_identity=profile.model_endpoint_identity,
    )

    with pytest.raises(ValueError, match="zero conditions"):
        build_exp1_pilot_execution_plan(
            catalog_manifest=catalog,
            pilot_profile=empty_profile,
            budget=budget,
        )


def test_exp1_pilot_dispatches_both_domains_and_persists_evidence(
    tmp_path: Path,
) -> None:
    catalog, profile, budget = _approved_inputs()
    calls: list[dict] = []
    adapter = _recording_adapter(calls)

    result = execute_exp1_pilot(
        catalog_manifest=catalog,
        pilot_profile=profile,
        budget=budget,
        approved_budget_digest=budget.budget_digest,
        baseline_entry_id="glm_5_2_exp1_baseline",
        output_base=tmp_path,
        real_transport=True,
        factorization_adapter=adapter,
        lean_adapter=adapter,
    )

    suite_root = tmp_path / "paper_exp1_minimal_pilot_v1"
    assert result.output_root == suite_root.as_posix()
    assert [call["case_id"] for call in calls] == [
        "factor_easy_01",
        "factor_medium_01",
        "factor_hard_01",
        "lean_easy_01",
        "lean_v2_medium_lemma_dag_01",
        "lean_v2_medium_function_set_dx_subset_chain_01",
        "lean_v2_medium_induction_nat_predicate_chain_01",
    ]
    assert [call["domain"] for call in calls] == [
        "factorization",
        "factorization",
        "factorization",
        "lean_proof",
        "lean_proof",
        "lean_proof",
        "lean_proof",
    ]
    assert all(call["real_transport"] is True for call in calls)
    assert all(call["entry_id"] == "glm_5_2_exp1_baseline" for call in calls)
    assert {call["timeout_seconds"] for call in calls} == {100}
    assert [call["max_tokens"] for call in calls] == [
        512,
        512,
        512,
        1024,
        1024,
        1024,
        1024,
    ]
    assert result.provider_attempt_count == 22
    assert result.provider_calls_made == 22
    assert result.blocked_run_count == 1
    assert result.status == PaperStatus.COMPLETED_WITH_FAILURES.value
    assert _jsonl_count(suite_root / "conditions.jsonl") == 8
    assert _jsonl_count(suite_root / "run_results.jsonl") == 8
    assert _jsonl_count(suite_root / "per_task_results.jsonl") == 8
    assert _jsonl_count(suite_root / "per_attempt_results.jsonl") == 22
    assert _jsonl_count(suite_root / "events" / "event_log.jsonl") >= 16
    assert _jsonl_count(suite_root / "artifacts" / "artifact_index.jsonl") >= 22
    assert (suite_root / "evidence_manifest.json").exists()
    execution_plan = json.loads(
        (suite_root / "execution_plan.json").read_text(encoding="utf-8")
    )
    assert execution_plan["budget_digest"] == budget.budget_digest
    assert execution_plan["provider_calls_made_before_execution"] == 0
    assert execution_plan["planned_ai_units"] == 22
    assert json.loads(
        (suite_root / "suite_manifest.json").read_text(encoding="utf-8")
    )["provider_attempt_count"] == 22


def test_exp1_pilot_selector_uses_partial_runtime_scope_and_independent_output_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    catalog, profile, budget = _approved_inputs_without_rechecking_lean()
    monkeypatch.setenv("TOKENSHARE_EXP1_BASELINE_API_KEY", "offline-capturing-key")
    calls: list[dict] = []

    def fake_dispatch(**kwargs):
        calls.append(kwargs)
        adapter_root = Path(kwargs["output_root"]) / str(kwargs["case"]["case_id"])
        adapter_root.mkdir(parents=True, exist_ok=True)
        result = _FakeAdapterResult(
            case=kwargs["case"],
            condition=kwargs["condition"],
            output_root=adapter_root,
            ai_unit_count=0,
        )
        (adapter_root / "run_manifest.json").write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return result

    monkeypatch.setattr(paper_runner, "dispatch_paper_case", fake_dispatch, raising=False)

    class IneligibleReportResult:
        paper_eligible = False
        output_paths = ()

    monkeypatch.setattr(
        paper_report,
        "write_exp1_pilot_report",
        lambda _suite_root, *, secret_values=(): IneligibleReportResult(),
    )
    independent_root = tmp_path / "pilot-unit-factor-easy-01-range-0"

    result = execute_exp1_pilot(
        catalog_manifest=catalog,
        pilot_profile=profile,
        budget=budget,
        approved_budget_digest=budget.budget_digest,
        baseline_entry_id="glm_5_2_exp1_baseline",
        output_base=tmp_path / "unused-base",
        execution_output_root=independent_root,
        real_transport=True,
        case_id="factor_easy_01",
        ai_unit_id="range_0",
        transport=object(),
    )

    assert result.output_root == independent_root.as_posix()
    assert result.condition_count == 1
    assert result.run_count == 1
    assert result.provider_calls_made == 0
    assert len(calls) == 1
    assert calls[0]["case"]["case_id"] == "factor_easy_01"
    assert calls[0]["selected_ai_unit_id"] == "range_0"
    assert calls[0]["real_transport"] is True
    assert calls[0]["transport"] is not None
    planned_ai_units = paper_runner._planned_ai_unit_ids(calls[0]["case"])
    plan = json.loads(
        (independent_root / "execution_plan.json").read_text(encoding="utf-8")
    )
    assert plan["planned_root_runs"] == 1
    assert plan["planned_ai_units"] == len(planned_ai_units)
    assert plan["tasks"][0]["ai_units"] == planned_ai_units
    assert plan["tasks"][0]["requested_ai_unit_id"] == "range_0"
    assert plan["tasks"][0]["selector_execution_scope"] == (
        "selected_ai_unit_protocol"
    )
    assert plan["execution_scope"] == "selected_ai_unit_protocol"
    assert plan["pilot_only"] is True


def test_exp1_pilot_unit_selector_rejects_partial_or_unknown_selection_before_dispatch(
    tmp_path: Path,
) -> None:
    catalog, profile, budget = _approved_inputs_without_rechecking_lean()
    common = {
        "catalog_manifest": catalog,
        "pilot_profile": profile,
        "budget": budget,
        "approved_budget_digest": budget.budget_digest,
        "baseline_entry_id": "glm_5_2_exp1_baseline",
        "output_base": tmp_path,
        "real_transport": True,
        "factorization_adapter": _forbidden_adapter,
        "lean_adapter": _forbidden_adapter,
    }

    with pytest.raises(ValueError, match="case_id and ai_unit_id must be provided together"):
        execute_exp1_pilot(**common, case_id="factor_easy_01")
    with pytest.raises(ValueError, match="independent execution_output_root"):
        execute_exp1_pilot(
            **common,
            case_id="factor_easy_01",
            ai_unit_id="range_0",
        )
    with pytest.raises(ValueError, match="AI unit is not present in approved pilot plan"):
        execute_exp1_pilot(
            **common,
            case_id="factor_easy_01",
            ai_unit_id="range_not_approved",
            execution_output_root=tmp_path / "unknown-unit",
        )


@pytest.mark.parametrize(
    "root_relation",
    ("equal", "parent", "child"),
)
def test_exp1_pilot_unit_selector_rejects_canonical_output_root_overlap(
    tmp_path: Path,
    root_relation: str,
) -> None:
    catalog, profile, budget = _approved_inputs_without_rechecking_lean()
    output_base = tmp_path / root_relation
    canonical_root = output_base / str(profile.body["suite_id"])
    execution_root = {
        "equal": canonical_root,
        "parent": output_base,
        "child": canonical_root / "selected-unit",
    }[root_relation]

    with pytest.raises(
        ValueError,
        match="must not overlap canonical Exp1 pilot root",
    ):
        execute_exp1_pilot(
            catalog_manifest=catalog,
            pilot_profile=profile,
            budget=budget,
            approved_budget_digest=budget.budget_digest,
            baseline_entry_id="glm_5_2_exp1_baseline",
            output_base=output_base,
            execution_output_root=execution_root,
            real_transport=True,
            case_id="factor_easy_01",
            ai_unit_id="range_0",
            factorization_adapter=_forbidden_adapter,
            lean_adapter=_forbidden_adapter,
        )


def test_exp1_pilot_persists_transport_and_pilot_only_report_contract(
    tmp_path: Path,
) -> None:
    catalog, profile, budget = _approved_inputs()
    adapter = _recording_adapter([])

    execute_exp1_pilot(
        catalog_manifest=catalog,
        pilot_profile=profile,
        budget=budget,
        approved_budget_digest=budget.budget_digest,
        baseline_entry_id="glm_5_2_exp1_baseline",
        output_base=tmp_path,
        real_transport=True,
        factorization_adapter=adapter,
        lean_adapter=adapter,
    )

    suite_root = tmp_path / "paper_exp1_minimal_pilot_v1"
    plan = json.loads((suite_root / "execution_plan.json").read_text(encoding="utf-8"))
    suite = json.loads((suite_root / "suite_manifest.json").read_text(encoding="utf-8"))
    runs = _read_jsonl(suite_root / "run_results.jsonl")
    assert plan["pilot_only"] is True
    assert suite["pilot_only"] is True
    assert all("transport_evidence" in run for run in runs)
    assert runs[-1]["transport_evidence"] == {
        "real_transport": False,
        "transport_kind": "not_called_structured_blocked",
    }
    assert runs[0]["transport_evidence"] == {
        "real_transport": False,
        "transport_kind": "test_fake",
    }


def test_exp1_eligible_execution_invokes_shared_report_writer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    catalog, profile, budget = _approved_inputs()
    base_adapter = _recording_adapter([])
    report_calls: list[dict] = []

    def eligible_adapter(**kwargs):
        result = base_adapter(**kwargs)
        result.body["task_result"]["paper_eligible"] = True
        result.body["eligibility_report"] = {
            "paper_eligible": True,
            "ineligibility_reasons": [],
        }
        result.body["run_evidence"]["transport_evidence"] = {
            "real_transport": True,
            "transport_kind": "ai_api",
        }
        return result

    class ReportResult:
        paper_eligible = True
        output_paths = (
            (tmp_path / "paper_exp1_minimal_pilot_v1" / "metrics" / "metrics.json").as_posix(),
        )

    def recording_report_writer(suite_root, *, secret_values=()):
        report_calls.append(
            {"suite_root": Path(suite_root), "secret_values": tuple(secret_values)}
        )
        return ReportResult()

    monkeypatch.setattr(
        paper_report,
        "write_exp1_pilot_report",
        recording_report_writer,
    )

    result = execute_exp1_pilot(
        catalog_manifest=catalog,
        pilot_profile=profile,
        budget=budget,
        approved_budget_digest=budget.budget_digest,
        baseline_entry_id="glm_5_2_exp1_baseline",
        output_base=tmp_path,
        real_transport=True,
        factorization_adapter=eligible_adapter,
        lean_adapter=eligible_adapter,
    )

    assert result.paper_eligible is True
    assert report_calls == [
        {
            "suite_root": tmp_path / "paper_exp1_minimal_pilot_v1",
            "secret_values": (),
        }
    ]


def test_exp1_default_real_adapters_require_key_env_before_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    catalog, profile, budget = _approved_inputs()
    monkeypatch.delenv("TOKENSHARE_EXP1_BASELINE_API_KEY", raising=False)

    with pytest.raises(ValueError, match="API key environment"):
        execute_exp1_pilot(
            catalog_manifest=catalog,
            pilot_profile=profile,
            budget=budget,
            approved_budget_digest=budget.budget_digest,
            baseline_entry_id="glm_5_2_exp1_baseline",
            output_base=tmp_path,
            real_transport=True,
            factorization_adapter=None,
            lean_adapter=_forbidden_adapter,
        )

    assert not (tmp_path / "paper_exp1_minimal_pilot_v1").exists()


def test_exp1_default_execution_writes_audit_report_when_ineligible(
    tmp_path: Path,
    monkeypatch,
) -> None:
    catalog, profile, budget = _approved_inputs()
    adapter = _recording_adapter([])
    report_calls: list[Path] = []
    monkeypatch.setenv("TOKENSHARE_EXP1_BASELINE_API_KEY", "test-key-for-gated-adapter")
    monkeypatch.setattr(
        "tokenshare.experiments.factorization_paper_adapter.run_factorization_paper_case",
        adapter,
    )
    monkeypatch.setattr(
        "tokenshare.experiments.lean_paper_adapter.run_lean_paper_case",
        adapter,
    )

    class IneligibleReportResult:
        paper_eligible = False
        output_paths = ()

    def ineligible_report_writer(suite_root, *, secret_values=()):
        report_calls.append(Path(suite_root))
        assert tuple(secret_values) == ("test-key-for-gated-adapter",)
        return IneligibleReportResult()

    monkeypatch.setattr(
        paper_report,
        "write_exp1_pilot_report",
        ineligible_report_writer,
    )

    result = execute_exp1_pilot(
        catalog_manifest=catalog,
        pilot_profile=profile,
        budget=budget,
        approved_budget_digest=budget.budget_digest,
        baseline_entry_id="glm_5_2_exp1_baseline",
        output_base=tmp_path,
        real_transport=True,
    )

    assert result.paper_eligible is False
    assert report_calls == [tmp_path / "paper_exp1_minimal_pilot_v1"]


@pytest.mark.parametrize(
    ("limit_overrides", "expected_reason"),
    [
        ({"provider_attempt_limit": 2}, "provider_attempt_limit"),
        ({"token_limit": 5120}, "token_limit"),
        ({"cost_limit": 0.006144}, "cost_limit"),
    ],
)
def test_exp1_pilot_hard_limits_stop_after_current_task(
    tmp_path: Path,
    limit_overrides: dict,
    expected_reason: str,
) -> None:
    catalog, profile, budget = _approved_inputs()
    calls: list[dict] = []
    adapter = _recording_adapter(calls)

    result = execute_exp1_pilot(
        catalog_manifest=catalog,
        pilot_profile=profile,
        budget=budget,
        approved_budget_digest=budget.budget_digest,
        baseline_entry_id="glm_5_2_exp1_baseline",
        output_base=tmp_path,
        real_transport=True,
        factorization_adapter=adapter,
        lean_adapter=adapter,
        **limit_overrides,
    )

    assert [call["case_id"] for call in calls] == ["factor_easy_01"]
    assert result.status == PaperStatus.BUDGET_EXHAUSTED.value
    assert result.provider_calls_made == 2
    assert result.stop_reason == expected_reason
    suite = json.loads(
        (
            tmp_path
            / "paper_exp1_minimal_pilot_v1"
            / "suite_manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert suite["status"] == PaperStatus.BUDGET_EXHAUSTED.value
    assert suite["provider_attempt_count"] == 2


def test_exp1_pilot_replay_consumes_existing_evidence_without_adapter_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog, profile, budget = _approved_inputs()
    first_calls: list[dict] = []
    adapter = _recording_adapter(first_calls)
    first = execute_exp1_pilot(
        catalog_manifest=catalog,
        pilot_profile=profile,
        budget=budget,
        approved_budget_digest=budget.budget_digest,
        baseline_entry_id="glm_5_2_exp1_baseline",
        output_base=tmp_path,
        real_transport=True,
        factorization_adapter=adapter,
        lean_adapter=adapter,
    )
    evidence_before = (
        tmp_path
        / "paper_exp1_minimal_pilot_v1"
        / "per_attempt_results.jsonl"
    ).read_bytes()
    suite_root = tmp_path / "paper_exp1_minimal_pilot_v1"
    tree_before = {
        path.relative_to(suite_root).as_posix(): path.read_bytes()
        for path in suite_root.rglob("*")
        if path.is_file()
    }
    monkeypatch.setattr(
        paper_runner,
        "_finalize_exp1_pilot_result",
        lambda **_kwargs: pytest.fail("replay must not rewrite derived evidence"),
    )

    replay = execute_exp1_pilot(
        catalog_manifest=catalog,
        pilot_profile=profile,
        budget=budget,
        approved_budget_digest=budget.budget_digest,
        baseline_entry_id="glm_5_2_exp1_baseline",
        output_base=tmp_path,
        real_transport=True,
        replay_only=True,
        factorization_adapter=_forbidden_adapter,
        lean_adapter=_forbidden_adapter,
    )

    assert len(first_calls) == 7
    assert replay.provider_calls_made == 0
    assert replay.provider_attempt_count == first.provider_attempt_count == 22
    assert replay.replayed_run_count == 8
    assert (
        tmp_path
        / "paper_exp1_minimal_pilot_v1"
        / "per_attempt_results.jsonl"
    ).read_bytes() == evidence_before
    assert {
        path.relative_to(suite_root).as_posix(): path.read_bytes()
        for path in suite_root.rglob("*")
        if path.is_file()
    } == tree_before


def test_exp1_pilot_replay_derives_stop_reason_from_hashed_event_evidence(
    tmp_path: Path,
) -> None:
    catalog, profile, budget = _approved_inputs()
    base_adapter = _recording_adapter([])

    def profile_violating_adapter(**kwargs):
        result = base_adapter(**kwargs)
        if kwargs["case"]["case_id"] == (
            "lean_v2_medium_induction_nat_predicate_chain_01"
        ):
            excess_tokens = 1_000_000
            result.body["task_result"]["total_tokens"] = excess_tokens
            result.body["attempt_results"][0]["total_tokens"] += (
                excess_tokens
                - sum(
                    item["total_tokens"]
                    for item in result.body["attempt_results"]
                )
            )
        return result

    first = execute_exp1_pilot(
        catalog_manifest=catalog,
        pilot_profile=profile,
        budget=budget,
        approved_budget_digest=budget.budget_digest,
        baseline_entry_id="glm_5_2_exp1_baseline",
        output_base=tmp_path,
        real_transport=True,
        factorization_adapter=profile_violating_adapter,
        lean_adapter=profile_violating_adapter,
    )
    suite_root = tmp_path / "paper_exp1_minimal_pilot_v1"
    (suite_root / "suite_manifest.json").unlink()

    replay = execute_exp1_pilot(
        catalog_manifest=catalog,
        pilot_profile=profile,
        budget=budget,
        approved_budget_digest=budget.budget_digest,
        baseline_entry_id="glm_5_2_exp1_baseline",
        output_base=tmp_path,
        real_transport=True,
        replay_only=True,
        factorization_adapter=_forbidden_adapter,
        lean_adapter=_forbidden_adapter,
    )

    assert first.stop_reason == "observed_tokens_exceeded_approved_profile"
    assert replay.stop_reason == first.stop_reason
    assert replay.status == PaperStatus.BUDGET_EXHAUSTED.value
    assert replay.paper_eligible is False
    assert replay.provider_calls_made == 0


def test_exp1_pilot_replay_rejects_tampered_attempt_and_artifact_evidence(
    tmp_path: Path,
) -> None:
    catalog, profile, budget = _approved_inputs()
    adapter = _recording_adapter([])
    execute_exp1_pilot(
        catalog_manifest=catalog,
        pilot_profile=profile,
        budget=budget,
        approved_budget_digest=budget.budget_digest,
        baseline_entry_id="glm_5_2_exp1_baseline",
        output_base=tmp_path,
        real_transport=True,
        factorization_adapter=adapter,
        lean_adapter=adapter,
    )
    suite_root = tmp_path / "paper_exp1_minimal_pilot_v1"
    (suite_root / "per_attempt_results.jsonl").write_text("", encoding="utf-8")
    (suite_root / "artifacts" / "artifact_index.jsonl").write_text(
        "", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="evidence manifest mismatch"):
        execute_exp1_pilot(
            catalog_manifest=catalog,
            pilot_profile=profile,
            budget=budget,
            approved_budget_digest=budget.budget_digest,
            baseline_entry_id="glm_5_2_exp1_baseline",
            output_base=tmp_path,
            real_transport=True,
            replay_only=True,
            factorization_adapter=_forbidden_adapter,
            lean_adapter=_forbidden_adapter,
        )


def test_exp1_pilot_replay_rehashes_existing_execution_plan(
    tmp_path: Path,
) -> None:
    catalog, profile, budget = _approved_inputs()
    adapter = _recording_adapter([])
    execute_exp1_pilot(
        catalog_manifest=catalog,
        pilot_profile=profile,
        budget=budget,
        approved_budget_digest=budget.budget_digest,
        baseline_entry_id="glm_5_2_exp1_baseline",
        output_base=tmp_path,
        real_transport=True,
        factorization_adapter=adapter,
        lean_adapter=adapter,
    )
    suite_root = tmp_path / "paper_exp1_minimal_pilot_v1"
    plan_path = suite_root / "execution_plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    retained_digest = plan["execution_plan_digest"]
    plan["tasks"][0]["case_id"] = "tampered_case"
    plan["execution_plan_digest"] = retained_digest
    plan_path.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="execution plan digest is invalid"):
        execute_exp1_pilot(
            catalog_manifest=catalog,
            pilot_profile=profile,
            budget=budget,
            approved_budget_digest=budget.budget_digest,
            baseline_entry_id="glm_5_2_exp1_baseline",
            output_base=tmp_path,
            real_transport=True,
            replay_only=True,
            factorization_adapter=_forbidden_adapter,
            lean_adapter=_forbidden_adapter,
        )


def test_exp1_pilot_partial_resume_rejects_missing_evidence_without_calls(
    tmp_path: Path,
) -> None:
    catalog, profile, budget = _approved_inputs()
    adapter = _recording_adapter([])
    execute_exp1_pilot(
        catalog_manifest=catalog,
        pilot_profile=profile,
        budget=budget,
        approved_budget_digest=budget.budget_digest,
        baseline_entry_id="glm_5_2_exp1_baseline",
        output_base=tmp_path,
        real_transport=True,
        factorization_adapter=adapter,
        lean_adapter=adapter,
    )
    suite_root = tmp_path / "paper_exp1_minimal_pilot_v1"
    run_records = (suite_root / "run_results.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    incomplete_run_records = run_records[:6] + run_records[7:]
    (suite_root / "run_results.jsonl").write_text(
        "\n".join(incomplete_run_records) + "\n",
        encoding="utf-8",
    )
    resume_calls: list[dict] = []
    forbidden = _recording_adapter(resume_calls)

    with pytest.raises(
        ValueError,
        match="evidence manifest mismatch|complete existing.*evidence",
    ):
        execute_exp1_pilot(
            catalog_manifest=catalog,
            pilot_profile=profile,
            budget=budget,
            approved_budget_digest=budget.budget_digest,
            baseline_entry_id="glm_5_2_exp1_baseline",
            output_base=tmp_path,
            real_transport=True,
            resume=True,
            factorization_adapter=forbidden,
            lean_adapter=forbidden,
        )

    assert resume_calls == []


def test_exp1_pilot_checkpoints_completed_root_before_later_adapter_failure(
    tmp_path: Path,
) -> None:
    catalog, profile, budget = _approved_inputs()
    completed_adapter = _recording_adapter([])
    call_count = 0

    def fail_on_second_root(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("synthetic second-root failure")
        return completed_adapter(**kwargs)

    with pytest.raises(RuntimeError, match="synthetic second-root failure"):
        execute_exp1_pilot(
            catalog_manifest=catalog,
            pilot_profile=profile,
            budget=budget,
            approved_budget_digest=budget.budget_digest,
            baseline_entry_id="glm_5_2_exp1_baseline",
            output_base=tmp_path,
            real_transport=True,
            factorization_adapter=fail_on_second_root,
            lean_adapter=fail_on_second_root,
        )

    suite_root = tmp_path / "paper_exp1_minimal_pilot_v1"
    assert _jsonl_count(suite_root / "run_results.jsonl") == 1
    assert _jsonl_count(suite_root / "per_task_results.jsonl") == 1
    assert _jsonl_count(suite_root / "per_attempt_results.jsonl") == 2
    checkpointed_run = json.loads(
        (suite_root / "run_results.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert checkpointed_run["case_id"] == "factor_easy_01"
    checkpointed_events = _read_jsonl(suite_root / "events" / "event_log.jsonl")
    assert any(
        event["event_type"] == "task_started"
        and event["case_id"] == "factor_medium_01"
        for event in checkpointed_events
    )


def test_exp1_pilot_rejects_approval_or_entry_drift_before_adapter_calls(
    tmp_path: Path,
) -> None:
    catalog, profile, budget = _approved_inputs()
    calls: list[dict] = []
    adapter = _recording_adapter(calls)

    with pytest.raises(ValueError, match="approved budget digest"):
        execute_exp1_pilot(
            catalog_manifest=catalog,
            pilot_profile=profile,
            budget=budget,
            approved_budget_digest="sha256:" + "0" * 64,
            baseline_entry_id="glm_5_2_exp1_baseline",
            output_base=tmp_path / "budget-drift",
            real_transport=True,
            factorization_adapter=adapter,
            lean_adapter=adapter,
        )
    with pytest.raises(ValueError, match="baseline entry"):
        execute_exp1_pilot(
            catalog_manifest=catalog,
            pilot_profile=profile,
            budget=budget,
            approved_budget_digest=budget.budget_digest,
            baseline_entry_id="wrong-entry",
            output_base=tmp_path / "entry-drift",
            real_transport=True,
            factorization_adapter=adapter,
            lean_adapter=adapter,
        )

    assert calls == []
    assert not (tmp_path / "budget-drift").exists()
    assert not (tmp_path / "entry-drift").exists()


@pytest.mark.parametrize("invalid_cost_limit", [math.nan, math.inf, -math.inf])
def test_exp1_pilot_rejects_non_finite_cost_limit_before_calls(
    tmp_path: Path,
    invalid_cost_limit: float,
) -> None:
    catalog, profile, budget = _approved_inputs()
    calls: list[dict] = []
    adapter = _recording_adapter(calls)

    with pytest.raises(ValueError, match="cost_limit must be finite"):
        execute_exp1_pilot(
            catalog_manifest=catalog,
            pilot_profile=profile,
            budget=budget,
            approved_budget_digest=budget.budget_digest,
            baseline_entry_id="glm_5_2_exp1_baseline",
            output_base=tmp_path,
            real_transport=True,
            cost_limit=invalid_cost_limit,
            factorization_adapter=adapter,
            lean_adapter=adapter,
        )

    assert calls == []
    assert not (tmp_path / "paper_exp1_minimal_pilot_v1").exists()


def test_exp1_pilot_never_marks_fake_adapter_evidence_paper_eligible(
    tmp_path: Path,
) -> None:
    catalog, profile, budget = _approved_inputs()
    fake = _recording_adapter([])

    def falsely_claiming_adapter(**kwargs):
        result = fake(**kwargs)
        result.body["task_result"]["paper_eligible"] = True
        result.body["eligibility_report"] = {
            "paper_eligible": True,
            "ineligibility_reasons": [],
        }
        assert result.body["run_evidence"]["transport_evidence"] == {
            "real_transport": False,
            "transport_kind": "test_fake",
        }
        return result

    result = execute_exp1_pilot(
        catalog_manifest=catalog,
        pilot_profile=profile,
        budget=budget,
        approved_budget_digest=budget.budget_digest,
        baseline_entry_id="glm_5_2_exp1_baseline",
        output_base=tmp_path,
        real_transport=True,
        factorization_adapter=falsely_claiming_adapter,
        lean_adapter=falsely_claiming_adapter,
    )

    assert result.paper_eligible is False
    tasks = _read_jsonl(
        tmp_path
        / "paper_exp1_minimal_pilot_v1"
        / "per_task_results.jsonl"
    )
    assert all(task["paper_eligible"] is False for task in tasks)


def test_exp1_pilot_budget_exhaustion_is_never_suite_paper_eligible(
    tmp_path: Path,
) -> None:
    catalog, profile, budget = _approved_inputs()
    fake = _recording_adapter([])

    def eligible_root_adapter(**kwargs):
        result = fake(**kwargs)
        result.body["task_result"]["paper_eligible"] = True
        result.body["eligibility_report"] = {
            "paper_eligible": True,
            "ineligibility_reasons": [],
        }
        result.body["run_evidence"]["transport_evidence"] = {
            "real_transport": True,
            "transport_kind": "ai_api",
        }
        return result

    result = execute_exp1_pilot(
        catalog_manifest=catalog,
        pilot_profile=profile,
        budget=budget,
        approved_budget_digest=budget.budget_digest,
        baseline_entry_id="glm_5_2_exp1_baseline",
        output_base=tmp_path,
        real_transport=True,
        provider_attempt_limit=2,
        factorization_adapter=eligible_root_adapter,
        lean_adapter=eligible_root_adapter,
    )

    assert result.status == PaperStatus.BUDGET_EXHAUSTED.value
    assert result.paper_eligible is False


def test_exp1_runner_consumes_existing_factorization_adapter_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    catalog, profile, budget = _approved_inputs()
    monkeypatch.setenv("TOKENSHARE_EXP1_BASELINE_API_KEY", "fake-test-key")
    transport = ScriptedFactorizationRangeTransport()

    def scripted_factorization_adapter(**kwargs):
        return run_factorization_paper_case(
            case=kwargs["case"],
            condition=kwargs["condition"],
            output_root=kwargs["output_root"],
            transport=transport,
            real_transport=False,
            ai_api_config=kwargs["ai_api_config"],
            entry_id=kwargs["entry_id"],
            max_tokens=kwargs["max_tokens"],
            timeout_seconds=kwargs["timeout_seconds"],
        )

    result = execute_exp1_pilot(
        catalog_manifest=catalog,
        pilot_profile=profile,
        budget=budget,
        approved_budget_digest=budget.budget_digest,
        baseline_entry_id="glm_5_2_exp1_baseline",
        output_base=tmp_path,
        real_transport=True,
        provider_attempt_limit=2,
        factorization_adapter=scripted_factorization_adapter,
        lean_adapter=_forbidden_adapter,
    )

    suite_root = tmp_path / "paper_exp1_minimal_pilot_v1"
    assert len(transport.calls) == 2
    assert result.provider_attempt_count == 2
    assert result.provider_calls_made == 2
    assert result.status == PaperStatus.BUDGET_EXHAUSTED.value
    assert result.paper_eligible is False
    assert _jsonl_count(suite_root / "per_attempt_results.jsonl") == 2
    assert _jsonl_count(suite_root / "artifacts" / "artifact_index.jsonl") >= 1
    assert json.loads(
        (suite_root / "evidence_manifest.json").read_text(encoding="utf-8")
    )["schema_version"] == "tokenshare.paper_execution_evidence_manifest.v1"
    report = paper_report.write_exp1_pilot_report(suite_root)
    assert report.paper_eligible is False
    assert (suite_root / "metrics" / "metrics.json").is_file()
    assert (suite_root / "audit" / "audit_summary.json").is_file()

    run = json.loads(
        (suite_root / "run_results.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    indexed_ref = next(
        json.loads(line)["artifact_ref"]
        for line in (
            suite_root / "artifacts" / "artifact_index.jsonl"
        ).read_text(encoding="utf-8").splitlines()
        if "\"uri\"" in line
    )
    (Path(run["artifact_root"]) / indexed_ref["uri"]).write_bytes(b"tampered")

    with pytest.raises(ValueError, match="artifact integrity"):
        execute_exp1_pilot(
            catalog_manifest=catalog,
            pilot_profile=profile,
            budget=budget,
            approved_budget_digest=budget.budget_digest,
            baseline_entry_id="glm_5_2_exp1_baseline",
            output_base=tmp_path,
            real_transport=True,
            provider_attempt_limit=2,
            replay_only=True,
            factorization_adapter=_forbidden_adapter,
            lean_adapter=_forbidden_adapter,
        )


def test_exp1_runner_scripted_simple_lean_evidence_is_strict_but_ineligible(
    tmp_path: Path,
    monkeypatch,
) -> None:
    catalog, profile, budget = _approved_inputs()
    monkeypatch.setenv("TOKENSHARE_EXP1_BASELINE_API_KEY", "fake-test-key")
    factor_transport = ScriptedFactorizationRangeTransport()
    lean_transport = ScriptedLeanPaperProofTransport(model="zai-org/GLM-5.2")

    def scripted_factorization_adapter(**kwargs):
        return run_factorization_paper_case(
            case=kwargs["case"],
            condition=kwargs["condition"],
            output_root=kwargs["output_root"],
            transport=factor_transport,
            real_transport=False,
            ai_api_config=kwargs["ai_api_config"],
            entry_id=kwargs["entry_id"],
            max_tokens=kwargs["max_tokens"],
            timeout_seconds=kwargs["timeout_seconds"],
        )

    def scripted_lean_adapter(**kwargs):
        return run_lean_paper_case(
            case=kwargs["case"],
            condition=kwargs["condition"],
            output_root=kwargs["output_root"],
            transport=lean_transport,
            real_transport=False,
            ai_api_config=kwargs["ai_api_config"],
            entry_id=kwargs["entry_id"],
            max_tokens=kwargs["max_tokens"],
            timeout_seconds=kwargs["timeout_seconds"],
        )

    result = execute_exp1_pilot(
        catalog_manifest=catalog,
        pilot_profile=profile,
        budget=budget,
        approved_budget_digest=budget.budget_digest,
        baseline_entry_id="glm_5_2_exp1_baseline",
        output_base=tmp_path,
        real_transport=True,
        provider_attempt_limit=8,
        factorization_adapter=scripted_factorization_adapter,
        lean_adapter=scripted_lean_adapter,
    )

    suite_root = tmp_path / "paper_exp1_minimal_pilot_v1"
    metrics = recompute_exp1_pilot_metrics(suite_root)
    tasks = _read_jsonl(suite_root / "per_task_results.jsonl")
    attempts = _read_jsonl(suite_root / "per_attempt_results.jsonl")
    simple_condition = next(
        condition.condition_id
        for condition in build_exp1_pilot_execution_plan(
            catalog_manifest=catalog,
            pilot_profile=profile,
            budget=budget,
        ).conditions
        if condition.paper_difficulty == "simple"
    )
    simple_task = next(
        task for task in tasks if task["condition_id"] == simple_condition
    )
    simple_attempts = [
        attempt
        for attempt in attempts
        if attempt["condition_id"] == simple_condition
    ]

    assert len(factor_transport.calls) == 6
    assert len(lean_transport.calls) == 2
    assert result.provider_calls_made == 8
    assert simple_task["paper_difficulty"] == "simple"
    assert simple_task["topic_family"] == "pure_logic"
    assert simple_task["topic_family_version"] == "shallow_v1"
    assert {item["planned_ai_unit_id"] for item in simple_attempts} == {
        "child_0",
        "child_1",
    }
    assert all(item["paper_difficulty"] == "simple" for item in simple_attempts)
    assert all(item["topic_family"] == "pure_logic" for item in simple_attempts)
    assert metrics.paper_eligible is False
    assert any(
        reason.startswith("non_real_transport:")
        for reason in metrics.ineligibility_reasons
    )


@lru_cache(maxsize=1)
def _approved_inputs():
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
        lean_lemma_graph_path=LEAN_LEMMA_GRAPH_CATALOG,
    )
    profile = load_exp1_pilot_profile(PILOT_PROFILE)
    plan_only_budget = plan_exp1_pilot(
        catalog_manifest=catalog,
        pilot_profile=profile,
        plan_only=True,
    )
    budget = plan_exp1_pilot(
        catalog_manifest=catalog,
        pilot_profile=profile,
        plan_only=False,
        approve_budget_digest=plan_only_budget.budget_digest,
    )
    return catalog, profile, budget


@lru_cache(maxsize=1)
def _approved_inputs_without_rechecking_lean():
    factorization_cases = tuple(
        {**case, "paper_difficulty": case["difficulty"]}
        for case in _read_jsonl(FACTOR_CATALOG)
    )
    lean_cases = tuple(
        {
            **case,
            "paper_difficulty": "simple",
            "topic_family": "pure_logic",
            "topic_family_version": "shallow_v1",
        }
        for case in _read_jsonl(LEAN_CATALOG)
    )
    lean_lemma_graph_cases = tuple(_read_jsonl(LEAN_LEMMA_GRAPH_CATALOG))
    digest_body = {
        "catalog_id": "tokenshare.paper.catalog",
        "catalog_version": "v1",
        "factorization_cases": factorization_cases,
        "lean_cases": lean_cases,
        "lean_lemma_graph_cases": lean_lemma_graph_cases,
    }
    catalog = PaperInputCatalogManifest(
        catalog_id="tokenshare.paper.catalog",
        catalog_version="v1",
        catalog_digest=digest_json(digest_body),
        generator_version="pilot_selector_test",
        case_count=(
            len(factorization_cases) + len(lean_cases) + len(lean_lemma_graph_cases)
        ),
        domain_counts={},
        difficulty_counts={},
        paper_difficulty_counts={},
        topic_family_counts={},
        paper_difficulty_topic_family_counts={},
        oracle_validation_status="passed",
        lean_preflight_status="passed",
        lean_preflight_summary={},
        lean_lemma_graph_preflight_summary={},
        created_at="2026-07-19T00:00:00Z",
        source_files=[],
        factorization_cases=factorization_cases,
        lean_cases=lean_cases,
        lean_lemma_graph_cases=lean_lemma_graph_cases,
    )
    profile = load_exp1_pilot_profile(PILOT_PROFILE)
    plan_only_budget = plan_exp1_pilot(
        catalog_manifest=catalog,
        pilot_profile=profile,
        plan_only=True,
    )
    budget = plan_exp1_pilot(
        catalog_manifest=catalog,
        pilot_profile=profile,
        plan_only=False,
        approve_budget_digest=plan_only_budget.budget_digest,
    )
    return catalog, profile, budget


def _recording_adapter(calls: list[dict]):
    def adapter(**kwargs):
        case = kwargs["case"]
        condition = kwargs["condition"]
        ai_unit_count = estimated_ai_units_for_case(case)
        calls.append(
            {
                "case_id": case["case_id"],
                "domain": condition.domain,
                "real_transport": kwargs["real_transport"],
                "entry_id": kwargs["entry_id"],
                "max_tokens": kwargs["max_tokens"],
                "timeout_seconds": kwargs["timeout_seconds"],
            }
        )
        adapter_root = Path(kwargs["output_root"]) / str(case["case_id"])
        adapter_root.mkdir(parents=True, exist_ok=True)
        result = _FakeAdapterResult(
            case=case,
            condition=condition,
            output_root=adapter_root,
            ai_unit_count=ai_unit_count,
        )
        (adapter_root / "run_manifest.json").write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return result

    return adapter


class _FakeAdapterResult:
    def __init__(
        self,
        *,
        case: dict,
        condition,
        output_root: Path,
        ai_unit_count: int,
    ) -> None:
        case_id = str(case["case_id"])
        task_id = (
            f"paper_factorization_{case_id}"
            if condition.domain == "factorization"
            else f"paper_lean_{case_id}"
        )
        self.body = {
            "schema_version": "tokenshare.test_fake_adapter_result.v1",
            "case_id": case_id,
            "condition": condition.to_dict(),
            "task_result": {
                "schema_version": "tokenshare.paper_task_result.v1",
                "condition_id": condition.condition_id,
                "repeat_id": condition.repeat_id,
                "task_id": task_id,
                "domain": condition.domain,
                "difficulty": condition.difficulty,
                "paper_difficulty": condition.paper_difficulty,
                "root_status": "completed",
                "accepted_validity": True,
                "failure_stage": None,
                "failure_kind": None,
                "attempt_count": ai_unit_count,
                "provider_attempt_count": ai_unit_count,
                "wall_clock_ms": ai_unit_count,
                "total_tokens": ai_unit_count * 10,
                "cost_estimate": ai_unit_count * 0.001,
                "event_refs": [],
                "artifact_refs": [
                    {"artifact_id": f"artifact_{case_id}_{index}"}
                    for index in range(ai_unit_count)
                ],
                "paper_eligible": False,
            },
            "attempt_results": [
                {
                    "schema_version": "tokenshare.paper_attempt_result.v1",
                    "condition_id": condition.condition_id,
                    "repeat_id": condition.repeat_id,
                    "run_id": f"run_{condition.condition_id}",
                    "task_id": task_id,
                    "unit_id": f"{case_id}_unit_{index}",
                    "attempt_id": f"{case_id}_attempt_{index}",
                    "provider_attempt_index": 0,
                    "total_tokens": 10,
                    "cost_estimate": 0.001,
                    "paper_eligible": False,
                    "raw_output_ref": {
                        "artifact_id": f"raw_{case_id}_{index}"
                    },
                    "model_execution_record_ref": {
                        "artifact_id": f"identity_{case_id}_{index}"
                    },
                }
                for index in range(ai_unit_count)
            ],
            "eligibility_report": {
                "paper_eligible": False,
                "ineligibility_reasons": ["scripted_or_fake_transport"],
            },
            "run_evidence": {
                "schema_version": "tokenshare.paper_run_evidence.v1",
                "transport_evidence": {
                    "real_transport": False,
                    "transport_kind": "test_fake",
                },
            },
            "output_root": output_root.as_posix(),
        }

    def to_dict(self) -> dict:
        return json.loads(json.dumps(self.body, ensure_ascii=False))


def _forbidden_adapter(**kwargs):
    raise AssertionError(f"replay attempted adapter call for {kwargs['case']['case_id']}")


def _jsonl_count(path: Path) -> int:
    return len(
        [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    )


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
