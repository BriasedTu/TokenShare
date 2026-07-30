import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from tokenshare.experiments.paper_dispatcher import PaperExperimentDispatchPlan
from tokenshare.experiments.paper_experiment_contracts import (
    FrozenCaseSelection,
    FrozenConditionSelectionBinding,
)
from tokenshare.experiments.paper_models import (
    PaperExperimentCondition,
    digest_json,
)
from tokenshare.experiments.paper_formal_evidence import FormalEvidenceStore
from tokenshare.experiments.paper_formal_metrics import recompute_paper_formal_metrics
from tokenshare.experiments.paper_smoke import (
    PAPER_SMOKE_PROFILE_SCHEMA_VERSION,
    load_paper_smoke_profile,
    resolve_paper_smoke_execution_plan,
)
from tokenshare.experiments.paper_smoke_report import generate_paper_smoke_report


CATALOG_DIGEST = "sha256:" + "1" * 64


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
        "6efeed8fcb8fc28f86ccb0e6e12e39c07233814519cf2c2a64948224490232da"
    )
    assert sha256(v2_path.read_bytes()).hexdigest() == (
        "25faccb5ef6e9bc0dd171b1a2fce398b6cf68a52cc22fc12e4dd4eee38566a64"
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
    with pytest.raises(ValueError, match="formal non-pilot"):
        recompute_paper_formal_metrics(tmp_path)


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
            }
        ),
        encoding="utf-8",
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
    (run_root / "CURRENT.json").write_text(
        json.dumps({"generation_id": "generation-1"}), encoding="utf-8"
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
                "provider_attempt_index": 0,
                "provider_attempt_count": 0,
                "model_execution_record_ref": {"artifact_id": "model-record-1"},
                "prompt_tokens": 7,
                "completion_tokens": 5,
                "total_tokens": 12,
                "cost_estimate": 0.125,
            },
            {
                "attempt_id": "attempt-2",
                "task_id": "factor_v2_easy_001",
                "provider_attempt_index": 0,
                "provider_attempt_count": 2,
                "model_execution_record_ref": {"artifact_id": "model-record-2"},
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
        [{"event_id": "event-1", "task_id": "factor_v2_easy_001"}],
    )
    _write_jsonl(
        generation / "artifacts" / "artifact_index.jsonl",
        [
            {
                "path": "artifacts/raw.json",
                "content_hash": "sha256:" + "6" * 64,
                "task_id": "factor_v2_easy_001",
            }
        ],
    )

    report = generate_paper_smoke_report(output_root=tmp_path, secret_values=())

    row = report["rows"][0]
    assert row["provider_attempt_count"] == 3
    assert row["total_tokens"] == 15
    assert row["cost_estimate"] == pytest.approx(0.15)
    assert row["provider_actual_billing"] is None
    assert row["provider_actual_billing_available"] is False
    assert row["outcome_status"] == "succeeded"
    assert row["evidence_integrity"] == "complete"
    assert row["event_refs"] == ["event-1"]
    assert row["artifact_refs"] == ["artifacts/raw.json"]
    assert row["paper_eligible"] is False
    assert {"smoke_suite", "pilot_only"}.issubset(
        row["ineligibility_reasons"]
    )
    assert (tmp_path / "metrics" / "smoke_summary.csv").is_file()
    assert (tmp_path / "metrics" / "smoke_failures.json").is_file()
    assert (tmp_path / "audit" / "smoke_eligibility_report.json").is_file()
    assert (tmp_path / "audit" / "smoke_evidence_manifest.json").is_file()
    assert (tmp_path / "audit" / "secret_scan_report.json").is_file()


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
