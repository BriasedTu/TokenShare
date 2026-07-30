import inspect
import json
from dataclasses import replace
from pathlib import Path
from threading import Lock
from time import sleep
from types import SimpleNamespace

import pytest

import tokenshare.experiments.paper_formal_runner as formal_runner
from tokenshare.core.models import ArtifactRef
from tokenshare.executors.ai_api_config import (
    AIAPIExecutorConfig,
    AIAPIProviderEntry,
)
from tokenshare.experiments.paper_experiment_contracts import (
    ExperimentSummaryRows,
    FrozenCaseSelectionBatch,
    FrozenCaseSelection,
    FrozenConditionSelectionBinding,
)
from tokenshare.experiments.paper_models import (
    PaperAttemptResult,
    PaperAttemptStatus,
    PaperBudgetResult,
    PaperConditionResult,
    PaperExperimentCondition,
    PaperModelExecutionRecord,
    PaperStatus,
    PaperTaskStatus,
)
from tokenshare.experiments.paper_smoke_report import generate_paper_smoke_report
from tokenshare.local_runtime import ParsedCandidateContext, WorkerTerminationPolicy
from tokenshare.storage.artifacts import ArtifactStore


CATALOG_DIGEST = "sha256:" + "1" * 64
BUDGET_DIGEST = "sha256:" + "2" * 64
EXPERIMENT_ID = "exp1_real_ai_feasibility"
PROVIDER_CONFIG_ID = "exp1_baseline_siliconflow"
MODEL_ENTRY_ID = "glm_5_2_exp1_baseline"
PROVIDER_MODEL_ID = "zai-org/GLM-5.2"
ENDPOINT_DIGEST = "sha256:" + "3" * 64
EXP5_EXPERIMENT_ID = "exp5_real_ai_model_endpoint_comparison"
MODEL_COHORT_ID = "paper-model-endpoint-cohort-v1"
MODEL_COHORT_DIGEST = "sha256:" + "4" * 64


def _shared_exp1_manifest(
    *,
    request_limits: dict[str, object],
    fault_target_manifest: dict[str, object] | None,
    worker_death_manifest: dict[str, object] | None,
) -> dict[str, object]:
    return {
        "baseline_policy": "shared_exp1_reference",
        "fault_target_manifest": fault_target_manifest,
        "worker_death_manifest": worker_death_manifest,
        "matched_baseline": {
            "source_kind": "shared_exp1_reference",
            "comparison_kind": "shared_reference",
            "additional_execution_required": False,
            "source_experiment_id": "exp1_real_ai_feasibility",
            "source_repeat_id": 0,
            "source_seed": 1,
            "source_worker_count": 10,
            "source_reference_ids_by_case": {
                "case-1": "planned-exp1-reference-case-1"
            },
            "reference_policy_id": "shared-exp1-policy-test",
            "request_limits": request_limits,
        },
    }


def _complete_shared_exp1_reference(*_args, **_kwargs) -> dict[str, object]:
    source_versions = {
        **dict(_kwargs.get("expected_source_versions", {})),
        "runtime_generation_identity_digest": "sha256:" + "5" * 64,
    }
    core = {
        "schema_version": "tokenshare.paper_exp1_shared_reference.v1",
        "reference_id": "shared-exp1-reference-case-1",
        "source_experiment_id": "exp1_real_ai_feasibility",
        "source_condition_id": "exp1_factorization_easy_w10_r0",
        "source_case_id": "case-1",
        "source_root_status": "completed",
        "evidence_integrity": "complete",
        "baseline_comparison_eligible": True,
        "baseline_unavailable_reason": None,
        "provider_calls_made": 0,
        "total_tokens": 0,
        "cost_estimate": 0.0,
        "source_versions": source_versions,
    }
    return {**core, "source_hash": formal_runner.digest_json(core)}


def test_exp3_shared_reference_is_resolved_before_provider_dispatch() -> None:
    request_limits = {
        "max_tokens": 300_000,
        "timeout_seconds": 600,
        "max_provider_attempts": 1,
        "stream": False,
        "thinking": {"type": "enabled"},
        "reasoning_effort": "high",
    }
    calls: list[dict[str, object]] = []

    class EvidenceStore:
        def build_shared_root_reference(self, **kwargs):
            calls.append(kwargs)
            core = {
                "schema_version": "tokenshare.paper_exp1_shared_reference.v1",
                "source_condition_id": "exp1_factorization_easy_w10_r0",
                "source_root_status": "completed",
                "evidence_integrity": "complete",
                "baseline_comparison_eligible": True,
                "baseline_unavailable_reason": None,
                "source_versions": {
                    **dict(kwargs["expected_source_versions"]),
                    "runtime_generation_identity_digest": "sha256:" + "5" * 64,
                },
            }
            return {**core, "source_hash": formal_runner.digest_json(core)}

    callback = object.__new__(formal_runner._FormalConditionExecutionCallback)
    object.__setattr__(callback, "evidence_store", EvidenceStore())
    object.__setattr__(callback, "request_limits", request_limits)
    object.__setattr__(callback, "execution_classification", None)
    case = {
        "case_id": "case-1",
        "split_params": {"strategy_id": "factorization.test.v1"},
    }
    condition = SimpleNamespace(
        experiment_id="exp3_real_ai_fault_recovery",
        condition_id="exp3-factor-easy-w10-r0",
        domain="factorization",
        difficulty="easy",
        paper_difficulty="easy",
        topic_family=None,
        catalog_digest=CATALOG_DIGEST,
        provider_config_id="exp1_baseline_deepseek",
        model_entry_id="deepseek_v4_pro_exp1_baseline",
        provider_family="deepseek",
        provider_model_id="deepseek-v4-pro",
        reasoning_profile_id="high",
        source_provider_config_digest="sha256:" + "8" * 64,
        model_endpoint_identity_digest="sha256:" + "9" * 64,
    )
    manifest = {
        "baseline_policy": "shared_exp1_reference",
        "matched_baseline": {
            "source_kind": "shared_exp1_reference",
            "comparison_kind": "shared_reference",
            "additional_execution_required": False,
            "source_experiment_id": "exp1_real_ai_feasibility",
            "source_repeat_id": 0,
            "source_seed": 1,
            "source_worker_count": 10,
            "source_reference_ids_by_case": {"case-1": "planned-ref-1"},
            "reference_policy_id": "policy-1",
            "request_limits": request_limits,
        },
    }

    reference = callback._ensure_exp3_shared_exp1_reference(
        condition=condition,
        case_id="case-1",
        case=case,
        callback_kwargs={"execution_manifest": manifest},
    )

    assert reference["planned_source_reference_id"] == "planned-ref-1"
    assert reference["reference_policy_id"] == "policy-1"
    assert calls == [
        {
            "source_experiment_id": "exp1_real_ai_feasibility",
            "case_id": "case-1",
            "source_repeat_id": 0,
            "expected_condition_identity": {
                "domain": "factorization",
                "difficulty": "easy",
                "paper_difficulty": "easy",
                "topic_family": None,
                "worker_count": 10,
                "repeat_id": 0,
                "seed": 1,
                "catalog_digest": CATALOG_DIGEST,
                "provider_config_id": "exp1_baseline_deepseek",
                "model_entry_id": "deepseek_v4_pro_exp1_baseline",
                "provider_family": "deepseek",
                "provider_model_id": "deepseek-v4-pro",
                "reasoning_profile_id": "high",
                "source_provider_config_digest": "sha256:" + "8" * 64,
                "model_endpoint_identity_digest": "sha256:" + "9" * 64,
                "request_limits": request_limits,
            },
            "expected_source_versions": formal_runner._execution_version_identity(
                domain="factorization",
                split_profile_digest=formal_runner.digest_json(case["split_params"]),
                runtime_generation_identity=None,
            ),
        }
    ]
    source = inspect.getsource(
        formal_runner._FormalConditionExecutionCallback._dispatch_root_case
    )
    assert source.index("_ensure_exp3_shared_exp1_reference") < source.index(
        "dispatch_paper_case"
    )


def test_formal_exp3_rejects_smoke_baseline_omission_before_dispatch() -> None:
    callback = object.__new__(formal_runner._FormalConditionExecutionCallback)
    object.__setattr__(callback, "execution_classification", None)
    condition = SimpleNamespace(experiment_id="exp3_real_ai_fault_recovery")

    with pytest.raises(ValueError, match="formal Exp3 scope"):
        callback._ensure_exp3_shared_exp1_reference(
            condition=condition,
            case_id="case-1",
            case={"case_id": "case-1", "split_params": {}},
            callback_kwargs={
                "execution_manifest": {
                    "baseline_policy": "omitted_for_smoke_regression",
                }
            },
        )


def test_formal_runner_rejects_exp3_before_exp1_before_output_creation(
    tmp_path: Path,
) -> None:
    suite_root = tmp_path / "reversed-suite"
    config = _ai_config()
    exp3 = "exp3_real_ai_fault_recovery"
    exp1 = "exp1_real_ai_feasibility"
    exp3_plan = _plan_for_experiment(
        tmp_path=suite_root,
        config=config,
        experiment_id=exp3,
        condition_id="condition-exp3-reversed",
        fault_type="false_positive",
        fault_rate=1.0,
    )
    exp1_plan = _plan_for_experiment(
        tmp_path=suite_root,
        config=config,
        experiment_id=exp1,
        condition_id="condition-exp1-reversed",
    )
    kwargs = _formal_execution_kwargs(
        tmp_path=suite_root,
        config=config,
        plan=exp3_plan,
    )
    kwargs.update(
        {
            "dispatch_plans": (exp3_plan, exp1_plan),
            "budget": _budget(
                planned_experiments=(exp3, exp1),
                planned_conditions=2,
                planned_root_runs=2,
                planned_ai_units=2,
            ),
        }
    )

    with pytest.raises(ValueError, match="Exp1 must precede Exp3"):
        formal_runner.execute_paper_formal_suite(**kwargs)

    assert not suite_root.exists()


def test_dependency_integrity_classification_is_typed_not_message_based() -> None:
    assert formal_runner._dependency_integrity_for_error(
        ValueError("missing hash corrupt absent")
    ) is formal_runner.PaperEvidenceIntegrity.INVALID
    assert formal_runner._dependency_integrity_for_error(
        FileNotFoundError("opaque")
    ) is formal_runner.PaperEvidenceIntegrity.MISSING
    assert formal_runner._dependency_integrity_for_error(
        json.JSONDecodeError("opaque", "", 0)
    ) is formal_runner.PaperEvidenceIntegrity.CORRUPT


def test_missing_shared_exp1_evidence_closes_suite_and_stops_new_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exp3 = "exp3_real_ai_fault_recovery"
    exp4 = "exp4_real_ai_protocol_ablation"
    config = _ai_config()
    exp3_plan = _plan_for_experiment(
        tmp_path=tmp_path,
        config=config,
        experiment_id=exp3,
        condition_id="condition-exp3-shared-source-missing",
        fault_type="false_positive",
        fault_rate=1.0,
    )
    exp4_plan = _plan_for_experiment(
        tmp_path=tmp_path,
        config=config,
        experiment_id=exp4,
        condition_id="condition-exp4-must-not-dispatch",
        ablation_mode="NO_VERIFICATION",
    )
    invoked_conditions: list[str] = []
    provider_dispatches: list[str] = []

    class Exp3Module:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            invoked_conditions.append(condition.condition_id)
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
                experiment_id=exp3,
                execution_manifest={
                    "baseline_policy": "shared_exp1_reference",
                    "fault_target_manifest": {
                        "fault_type": "false_positive",
                        "selected_target_ai_unit_ids": ["case-1:range_0"],
                        "reserve_target_ai_unit_ids": [],
                    },
                    "worker_death_manifest": None,
                    "matched_baseline": {
                        "source_kind": "shared_exp1_reference",
                        "comparison_kind": "shared_reference",
                        "additional_execution_required": False,
                        "source_experiment_id": "exp1_real_ai_feasibility",
                        "source_repeat_id": 0,
                        "source_seed": 1,
                        "source_worker_count": 10,
                        "source_reference_ids_by_case": {
                            "case-1": "planned-ref-case-1"
                        },
                        "reference_policy_id": "policy-case-1",
                        "request_limits": dict(context.request_limits),
                    },
                },
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=exp3, rows=())

    class Exp4Module:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            invoked_conditions.append(condition.condition_id)
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=exp4, rows=())

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((exp3, Exp3Module()), (exp4, Exp4Module())),
    )

    def dispatch(**kwargs):
        provider_dispatches.append(kwargs["condition"].condition_id)
        raise AssertionError("missing shared evidence must block before provider dispatch")

    monkeypatch.setattr(formal_runner, "dispatch_paper_case", dispatch)

    suite = formal_runner.execute_paper_formal_suite(
        **{
            **_formal_execution_kwargs(
                tmp_path=tmp_path,
                config=config,
                plan=exp3_plan,
            ),
            "dispatch_plans": (exp3_plan, exp4_plan),
            "budget": _budget(
                planned_experiments=(exp3, exp4),
                planned_conditions=2,
                planned_root_runs=2,
                planned_ai_units=2,
            ),
        }
    )

    assert suite.status == PaperStatus.BLOCKED
    assert invoked_conditions == ["condition-exp3-shared-source-missing"]
    assert provider_dispatches == []
    assert suite.error_summary == (
        {
            "outcome_status": "blocked_dependency",
            "evidence_integrity": "missing",
            "failure_stage": "shared_exp1_reference",
            "failure_kind": "source_evidence_unavailable",
            "condition_id": "condition-exp3-shared-source-missing",
            "task_id": "case-1",
        },
    )
    exp3_task = _generation_records(
        tmp_path,
        exp3,
        "condition-exp3-shared-source-missing",
        "per_task_results.jsonl",
    )[0]
    exp4_task = _generation_records(
        tmp_path,
        exp4,
        "condition-exp4-must-not-dispatch",
        "per_task_results.jsonl",
    )[0]
    assert exp3_task["root_status"] == "blocked"
    assert exp3_task["outcome_status"] == "blocked_dependency"
    assert exp3_task["evidence_integrity"] == "missing"
    assert exp4_task["root_status"] == "not_started"
    assert exp4_task["outcome_status"] == "blocked_dependency"
    assert exp4_task["evidence_integrity"] == "missing"
    for experiment_id in (exp3, exp4):
        manifest = json.loads(
            (
                tmp_path
                / "experiments"
                / experiment_id
                / "experiment_manifest.json"
            ).read_text(encoding="utf-8")
        )
        assert manifest["status"] == "blocked"
    assert json.loads(
        (tmp_path / "formal_runner_result.json").read_text(encoding="utf-8")
    )["status"] == "blocked"


def test_smoke_launch_manifest_is_persisted_fail_closed_before_execution(
    tmp_path: Path,
) -> None:
    launch = {
        "schema_version": "tokenshare.paper_smoke_launch_manifest.v1",
        "launch_manifest_digest": "sha256:" + "5" * 64,
        "paper_eligible": False,
    }

    normalized = formal_runner._normalize_pre_execution_documents(
        {"smoke_launch_manifest.json": launch}
    )
    formal_runner._persist_pre_execution_documents(
        suite_root=tmp_path,
        documents=normalized,
    )

    persisted = json.loads(
        (tmp_path / "smoke_launch_manifest.json").read_text(encoding="utf-8")
    )
    assert persisted == launch
    with pytest.raises(
        ValueError,
        match="pre-execution document identity mismatch",
    ):
        formal_runner._persist_pre_execution_documents(
            suite_root=tmp_path,
            documents={
                "smoke_launch_manifest.json": {
                    **launch,
                    "launch_manifest_digest": "sha256:" + "6" * 64,
                }
            },
        )


def test_smoke_recovery_manifest_is_append_only_and_path_safe(
    tmp_path: Path,
) -> None:
    recovery = {
        "schema_version": "tokenshare.paper_smoke_recovery_manifest.v1",
        "recovery_id": "smoke_recovery_1234abcd",
        "recovery_manifest_digest": "sha256:" + "7" * 64,
        "paper_eligible": False,
    }
    relative_path = "smoke_recoveries/smoke_recovery_1234abcd.json"

    normalized = formal_runner._normalize_recovery_documents(
        {relative_path: recovery}
    )
    formal_runner._persist_pre_execution_documents(
        suite_root=tmp_path,
        documents=normalized,
    )

    assert json.loads(
        (tmp_path / relative_path).read_text(encoding="utf-8")
    ) == recovery
    with pytest.raises(ValueError, match="recovery document path"):
        formal_runner._normalize_recovery_documents(
            {"../smoke_recovery_escape.json": recovery}
        )


def test_formal_runner_binds_frozen_lean_case_metadata_for_adapter() -> None:
    condition = PaperExperimentCondition(
        experiment_id=EXPERIMENT_ID,
        condition_id="exp1_lean_simple_pure_logic_w10_r0",
        domain="lean_proof",
        difficulty="easy",
        paper_difficulty="simple",
        topic_family="pure_logic",
        worker_count=10,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=1,
        catalog_digest=CATALOG_DIGEST,
    )
    case = {
        "paper_difficulty": "simple",
        "topic_family": "pure_logic",
        "topic_family_version": "shallow_v1",
        "construction_rule_id": None,
        "oracle_package_group": None,
        "proof_assembly_shape": None,
    }

    bound = formal_runner._condition_with_frozen_case_metadata(
        condition=condition,
        case=case,
    )

    assert condition.topic_family_version is None
    assert bound.condition_id == condition.condition_id
    assert bound.topic_family_version == "shallow_v1"
    assert bound.construction_rule_id is None

    with pytest.raises(ValueError, match="condition topic_family_version"):
        formal_runner._condition_with_frozen_case_metadata(
            condition=replace(condition, topic_family_version="wrong_v1"),
            case=case,
        )


def test_formal_runner_binds_frozen_factorization_difficulty_for_adapter() -> None:
    condition = PaperExperimentCondition(
        experiment_id="exp3_real_ai_fault_recovery",
        condition_id="exp3_rate_fault_factorization__false_positive__r0__rep0",
        domain="factorization",
        difficulty="medium",
        paper_difficulty="medium",
        worker_count=10,
        fault_type="false_positive",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=1,
        catalog_digest=CATALOG_DIGEST,
    )

    bound = formal_runner._condition_with_frozen_case_metadata(
        condition=condition,
        case={"difficulty": "easy", "paper_difficulty": "easy"},
    )

    assert condition.difficulty == "medium"
    assert condition.paper_difficulty == "medium"
    assert bound.condition_id == condition.condition_id
    assert bound.difficulty == "easy"
    assert bound.paper_difficulty == "easy"


def test_exp3_hook_ignores_non_target_parsed_candidate_before_raw_output() -> None:
    condition = PaperExperimentCondition(
        experiment_id="exp3_real_ai_fault_recovery",
        condition_id="exp3_rate_fault_factorization__false_positive__r0__rep0",
        domain="factorization",
        difficulty="medium",
        paper_difficulty="medium",
        worker_count=10,
        fault_type="false_positive",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=1,
        catalog_digest=CATALOG_DIGEST,
    )
    ref = ArtifactRef(
        artifact_id="subject",
        artifact_type="Subject",
        uri="artifacts/subject",
        content_hash="sha256:" + "a" * 64,
        size_bytes=1,
        media_type="application/json",
        artifact_schema_id="test.subject",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={},
        created_at="2026-07-27T00:00:00Z",
    )
    bridge = formal_runner._Exp3RuntimeHookBridge(
        condition=condition,
        case_id="factor_v2_easy_001",
        fault_type="false_positive",
        selected_unit_ids=(),
        reserve_unit_ids=(),
        runtime_records=[],
    )

    directive = bridge.after_parsed_candidate_persisted(
        ParsedCandidateContext(
            run_id="run-root",
            task_id="task-root",
            unit_id="factor-root",
            attempt_id="attempt-root",
            lease_id="lease-root",
            worker_id="worker-root",
            raw_output_ref=None,
            original_parsed_output_ref=ref,
            candidate_output_refs={"subject": ref},
            submitted_at="2026-07-27T00:00:01Z",
            experiment_unit_id=None,
        )
    )

    assert directive is None


COHORT_MEMBER_ID = "glm_5_2_siliconflow"


def test_formal_runner_checkpoints_required_evidence_and_reuses_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)
    adapter_calls: list[str] = []

    class CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

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

    def fake_case_dispatch(**kwargs):
        adapter_calls.append(kwargs["case"]["case_id"])
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", fake_case_dispatch)
    kwargs = _formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan)

    executed = formal_runner.execute_paper_formal_suite(**kwargs)
    assert executed.status == PaperStatus.COMPLETED
    assert executed.provider_attempt_count == 1
    assert executed.total_tokens == 11
    assert executed.total_cost_estimate == pytest.approx(0.125)
    assert adapter_calls == ["case-1"]

    suite_files = {
        "suite_manifest.json",
        "run_budget.json",
        "input_catalog_manifest.json",
        "paper_dispatch_plans.json",
        "conditions.jsonl",
        "evidence_manifest.json",
    }
    assert suite_files <= {item.name for item in tmp_path.iterdir()}
    suite_manifest = json.loads(
        (tmp_path / "suite_manifest.json").read_text(encoding="utf-8")
    )
    assert suite_manifest["formal"] is True
    assert suite_manifest["pilot_only"] is False
    assert suite_manifest["execution_scope"] == "formal_matrix"
    assert suite_manifest["regression_only"] is True
    assert suite_manifest["paper_eligible"] is False
    assert (tmp_path / EXPERIMENT_ID / "experiment_manifest.json").is_file()

    run_root = tmp_path / EXPERIMENT_ID / "runs" / "condition-1" / "0"
    for relative_path in (
        "CURRENT.json",
        "artifacts/case-1/raw-output.txt",
        "artifacts/case-1/request.json",
    ):
        assert (run_root / relative_path).is_file(), relative_path
    current = json.loads((run_root / "CURRENT.json").read_text(encoding="utf-8"))
    checkpoint_task = json.loads(
        (
            run_root
            / ".generations"
            / current["generation_id"]
            / "per_task_results.jsonl"
        ).read_text(encoding="utf-8")
    )
    assert checkpoint_task["runtime_generation_identity"] == {
        "schema_version": "tokenshare.paper_runtime_generation_identity.v1",
        "run_id": "run-case-1",
        "task_id": "case-1",
        "root_unit_id": "root-case-1",
        "event_count": 1,
        "first_event_id": "event-case-1",
        "last_event_id": "event-case-1",
        "last_event_hash": "sha256:event-case-1",
        "ledger_digest": "sha256:ledger-case-1",
    }
    assert checkpoint_task["case_id"] == "case-1"
    assert checkpoint_task["factor_position_quantile"] == "early"
    assert checkpoint_task["runtime_observation"][
        "planned_ai_unit_ids"
    ] == ["range_0"]

    canonical_late_file = (
        tmp_path
        / "experiments"
        / EXPERIMENT_ID
        / "runs"
        / "condition-1"
        / "0"
        / "compatibility-late.json"
    )
    canonical_late_file.write_text('{"kind":"canonical"}\n', encoding="utf-8")
    evidence_store = formal_runner.FormalEvidenceStore(tmp_path)
    evidence_store._refresh_evidence_manifest()
    compatibility_late_file = (
        tmp_path
        / EXPERIMENT_ID
        / "runs"
        / "condition-1"
        / "0"
        / "compatibility-late.json"
    )
    compatibility_late_file.write_bytes(canonical_late_file.read_bytes())
    with pytest.raises(
        ValueError,
        match="evidence manifest does not exactly index stored files",
    ):
        evidence_store._validate_evidence_manifest()

    repair = evidence_store.repair_stale_compatibility_manifest()

    assert repair is not None
    assert repair["unindexed_compatibility_file_count"] == 1
    assert repair["original_manifest_ref"]["path"].startswith("repairs/")
    evidence_store._validate_evidence_manifest()

    recovery_path = "smoke_recoveries/smoke_recovery_test.json"
    resumed = formal_runner.execute_paper_formal_suite(
        **kwargs,
        resume=True,
        recovery_documents={
            recovery_path: {
                "schema_version": "tokenshare.paper_smoke_recovery_manifest.v1",
                "recovery_id": "smoke_recovery_test",
                "recovery_manifest_digest": "sha256:" + "8" * 64,
                "paper_eligible": False,
            }
        },
    )
    assert resumed.to_dict() == executed.to_dict()
    assert adapter_calls == ["case-1"]
    assert (tmp_path / recovery_path).is_file()

    replayed = formal_runner.execute_paper_formal_suite(
        **{**kwargs, "ai_api_configs": {}, "transport": None},
        replay_only=True,
    )
    assert replayed.to_dict() == executed.to_dict()
    assert adapter_calls == ["case-1"]


def test_formal_runner_rejects_completed_protocol_result_without_real_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)

    class CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

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

    def missing_evidence_dispatch(**kwargs):
        result = _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )
        result.attempt_results = []
        result.event_records = []
        result.task_result.event_refs = []
        return result

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, CallbackModule()),),
    )
    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_case",
        missing_evidence_dispatch,
    )

    suite = formal_runner.execute_paper_formal_suite(
        **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan)
    )

    assert suite.status == PaperStatus.BLOCKED
    task = _generation_records(
        tmp_path,
        EXPERIMENT_ID,
        "condition-1",
        "per_task_results.jsonl",
    )[0]
    assert task["outcome_status"] == "blocked_dependency"
    assert task["evidence_integrity"] == "invalid"
    assert json.loads(
        (tmp_path / "formal_runner_result.json").read_text(encoding="utf-8")
    )["status"] == "blocked"

def test_formal_runner_dispatches_planned_conditions_with_isolated_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)
    calls: list[tuple[object, object, str]] = []

    def capture_dispatch(*, context, plan, condition_id):
        calls.append((context, plan, condition_id))
        return PaperConditionResult(
            condition_id=condition_id,
            status=PaperStatus.COMPLETED,
            repeat_count=1,
            task_count=1,
            completed_root_count=1,
            failed_root_count=0,
            blocked_root_count=0,
            provider_attempt_count=1,
            metrics_ref=None,
        )

    monkeypatch.setattr(formal_runner, "dispatch_paper_condition", capture_dispatch)

    suite = formal_runner.execute_paper_formal_suite(
        dispatch_plans=(plan,),
        catalog_manifest=SimpleNamespace(catalog_digest=CATALOG_DIGEST),
        budget=_budget(planned_conditions=1, planned_root_runs=1, planned_ai_units=1),
        budget_approval={
            "approval_mode": "user_bypassed",
            "budget_digest": BUDGET_DIGEST,
        },
        output_root=tmp_path,
        ai_api_configs={PROVIDER_CONFIG_ID: config},
        transport=object(),
        real_transport=False,
        hard_limits={"stop_after_current_task": True},
    )

    assert [(item[1], item[2]) for item in calls] == [(plan, "condition-1")]
    assert Path(calls[0][0].output_root) == tmp_path / EXPERIMENT_ID
    assert (tmp_path / EXPERIMENT_ID).is_dir()
    assert suite.status == PaperStatus.COMPLETED
    assert suite.condition_count == len(plan.bound_items())
    assert suite.paper_eligible is False
    assert suite.provider_attempt_count == 1


def test_formal_runner_accepts_frozen_supporting_baseline_budget_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)
    budget = replace(
        _budget(
            planned_conditions=1,
            planned_root_runs=2,
            planned_ai_units=3,
        ),
        quota_preflight={
            "provider_calls_made": 0,
            "budget_commitments": {
                "experiment_budget_identity": {
                    "schema_version": (
                        "tokenshare.paper_experiment_budget_identity.v1"
                    ),
                    "headline_root_runs_by_experiment": {EXPERIMENT_ID: 1},
                    "supporting_baseline_root_runs_by_experiment": {
                        EXPERIMENT_ID: 1
                    },
                    "actual_scheduled_root_runs_by_experiment": {
                        EXPERIMENT_ID: 2
                    },
                    "headline_ai_units_by_experiment": {EXPERIMENT_ID: 1},
                    "supporting_baseline_ai_units_by_experiment": {
                        EXPERIMENT_ID: 2
                    },
                    "planned_first_attempt_ai_units_by_experiment": {
                        EXPERIMENT_ID: 3
                    },
                }
            },
        },
    )
    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_condition",
        lambda *, context, plan, condition_id: PaperConditionResult(
            condition_id=condition_id,
            status=PaperStatus.COMPLETED,
            repeat_count=1,
            task_count=1,
            completed_root_count=1,
            failed_root_count=0,
            blocked_root_count=0,
            provider_attempt_count=0,
            metrics_ref=None,
        ),
    )

    suite = formal_runner.execute_paper_formal_suite(
        dispatch_plans=(plan,),
        catalog_manifest=SimpleNamespace(catalog_digest=CATALOG_DIGEST),
        budget=budget,
        budget_approval={
            "approval_mode": "user_bypassed",
            "budget_digest": BUDGET_DIGEST,
        },
        output_root=tmp_path,
        ai_api_configs={PROVIDER_CONFIG_ID: config},
        transport=object(),
        real_transport=False,
        hard_limits={"stop_after_current_task": True},
    )

    assert suite.status == PaperStatus.COMPLETED
    persisted_budget = json.loads(
        (tmp_path / "run_budget.json").read_text(encoding="utf-8")
    )
    assert persisted_budget["planned_root_runs"] == 2
    assert persisted_budget["planned_ai_units"] == 3


def test_formal_runner_materializes_nested_windows_ads_artifact_name(
    tmp_path: Path,
) -> None:
    adapter_root = tmp_path / "adapter"
    nested_artifacts = adapter_root / "case-1" / "artifacts"
    nested_artifacts.mkdir(parents=True)
    artifact_name = "merge_input_bundle:merge_plan_1"
    source = nested_artifacts / artifact_name
    source.write_bytes(b"nested-merge-input")

    refs = formal_runner._materialize_artifacts(
        suite_root=tmp_path / "suite",
        condition=SimpleNamespace(
            experiment_id=EXPERIMENT_ID,
            condition_id="condition-1",
            repeat_id=0,
        ),
        task_id="case-1",
        adapter_root=adapter_root,
        source_refs=(
            {
                "uri": f"artifacts/{artifact_name}",
                "content_hash": formal_runner._sha256_bytes(
                    b"nested-merge-input"
                ),
            },
        ),
    )

    assert len(refs) == 1
    copied = tmp_path / "suite" / refs[0]["path"]
    assert copied.read_bytes() == b"nested-merge-input"


def test_formal_runner_builds_endpoint_context_through_registered_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)
    observed: dict[str, object] = {}
    adapter_calls: list[dict[str, object]] = []

    class ContextCheckingModule:
        def expand_conditions(self, context):
            raise AssertionError("execution must consume the frozen dispatch plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            observed["approved_endpoint_binding"] = dict(
                context.approved_endpoint_binding
            )
            observed["request_limits"] = dict(context.request_limits)
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=EXPERIMENT_ID, rows=())

    def capture_case_dispatch(**kwargs):
        adapter_calls.append(kwargs)
        return SimpleNamespace(
            task_result=SimpleNamespace(
                root_status=PaperTaskStatus.COMPLETED,
                provider_attempt_count=1,
                paper_eligible=False,
            ),
            eligibility_report=SimpleNamespace(paper_eligible=False),
        )

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, ContextCheckingModule()),),
    )
    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_case",
        capture_case_dispatch,
        raising=False,
    )

    suite = formal_runner.execute_paper_formal_suite(
        dispatch_plans=(plan,),
        catalog_manifest=SimpleNamespace(
            catalog_digest=CATALOG_DIGEST,
            factorization_cases=({"case_id": "case-1"},),
            lean_cases=(),
            lean_lemma_graph_cases=(),
        ),
        budget=_budget(planned_conditions=1, planned_root_runs=1, planned_ai_units=1),
        budget_approval={
            "approval_mode": "user_bypassed",
            "budget_digest": BUDGET_DIGEST,
        },
        output_root=tmp_path,
        ai_api_configs={PROVIDER_CONFIG_ID: config},
        transport=object(),
        real_transport=False,
        hard_limits={"stop_after_current_task": True},
    )

    request_controls = {
        **config.defaults,
        **config.entries[0].request_overrides,
    }
    assert observed["approved_endpoint_binding"] == {
        "provider_config_id": PROVIDER_CONFIG_ID,
        "selected_entry_id": MODEL_ENTRY_ID,
        "model_entry_id": MODEL_ENTRY_ID,
        "provider_family": "siliconflow",
        "provider_model_id": PROVIDER_MODEL_ID,
        "reasoning_profile_id": "default",
        "source_provider_config_digest": config.config_digest,
        "model_endpoint_identity_digest": ENDPOINT_DIGEST,
        "request_controls": request_controls,
    }
    assert observed["request_limits"] == request_controls
    assert len(adapter_calls) == 1
    assert adapter_calls[0]["ai_api_config"] is config
    assert adapter_calls[0]["entry_id"] == MODEL_ENTRY_ID
    assert adapter_calls[0]["max_tokens"] == 1024
    assert adapter_calls[0]["timeout_seconds"] == 30
    assert suite.status == PaperStatus.COMPLETED


def test_formal_runner_accepts_mapping_catalog_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)

    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_condition",
        lambda *, context, plan, condition_id: PaperConditionResult(
            condition_id=condition_id,
            status=PaperStatus.COMPLETED,
            repeat_count=1,
            task_count=1,
            completed_root_count=1,
            failed_root_count=0,
            blocked_root_count=0,
            provider_attempt_count=0,
            metrics_ref=None,
        ),
    )

    result = formal_runner.execute_paper_formal_suite(
        dispatch_plans=(plan,),
        catalog_manifest={"catalog_digest": CATALOG_DIGEST},
        budget=_budget(planned_conditions=1, planned_root_runs=1, planned_ai_units=1),
        budget_approval={
            "approval_mode": "user_bypassed",
            "budget_digest": BUDGET_DIGEST,
        },
        output_root=tmp_path,
        ai_api_configs={PROVIDER_CONFIG_ID: config},
        transport=object(),
        real_transport=False,
        hard_limits={},
    )

    assert result.status == PaperStatus.COMPLETED


@pytest.mark.parametrize(
    "drift",
    (
        "preflight_cohort_digest",
        "member_cohort_digest",
        "member_request_controls",
    ),
)
def test_exp5_endpoint_contract_rejects_cohort_or_control_drift(
    tmp_path: Path,
    drift: str,
) -> None:
    config = _ai_config()
    base_condition = _planned_dispatch_plan(
        tmp_path,
        config=config,
    ).conditions[0]
    condition = replace(
        base_condition,
        experiment_id=EXP5_EXPERIMENT_ID,
        model_cohort_id=MODEL_COHORT_ID,
        model_cohort_digest=MODEL_COHORT_DIGEST,
        cohort_member_id=COHORT_MEMBER_ID,
    )
    request_controls = _normalized_exp5_request_controls(config)
    member_plan = {
        "cohort_id": MODEL_COHORT_ID,
        "model_cohort_digest": MODEL_COHORT_DIGEST,
        "cohort_member_id": COHORT_MEMBER_ID,
        "provider_config_id": PROVIDER_CONFIG_ID,
        "selected_entry_id": MODEL_ENTRY_ID,
        "provider_family": "siliconflow",
        "provider_model_id": PROVIDER_MODEL_ID,
        "reasoning_profile_id": "default",
        "source_provider_config_digest": config.config_digest,
        "model_endpoint_identity_digest": ENDPOINT_DIGEST,
        "request_controls": request_controls,
    }
    approved_binding = {
        "status": "planned",
        "cohort_id": MODEL_COHORT_ID,
        "model_cohort_digest": MODEL_COHORT_DIGEST,
        "member_plans": {COHORT_MEMBER_ID: member_plan},
        "request_controls_snapshot": request_controls["comparable"],
        "request_controls_snapshot_digest": request_controls[
            "comparable_digest"
        ],
    }
    if drift == "preflight_cohort_digest":
        approved_binding["model_cohort_digest"] = "sha256:" + "9" * 64
    elif drift == "member_cohort_digest":
        member_plan["model_cohort_digest"] = "sha256:" + "9" * 64
    else:
        member_plan["request_controls"]["comparable"]["max_tokens"] = 2048

    with pytest.raises(ValueError, match="Exp5"):
        formal_runner._condition_endpoint_contract(
            experiment_id=EXP5_EXPERIMENT_ID,
            condition=condition,
            ai_api_configs={
                PROVIDER_CONFIG_ID: config,
                formal_runner.APPROVED_ENDPOINT_BINDINGS_KEY: {
                    EXP5_EXPERIMENT_ID: approved_binding,
                },
            },
        )


def test_exp5_endpoint_contract_accepts_preregistered_thinking_budget(
    tmp_path: Path,
) -> None:
    base_config = _ai_config()
    thinking_entry = replace(
        base_config.entries[0],
        request_overrides={
            **dict(base_config.entries[0].request_overrides),
            "enable_thinking": True,
            "thinking_budget": 32_768,
        },
    )
    config = replace(base_config, entries=[thinking_entry])
    base_condition = _planned_dispatch_plan(
        tmp_path,
        config=config,
    ).conditions[0]
    condition = replace(
        base_condition,
        experiment_id=EXP5_EXPERIMENT_ID,
        model_cohort_id=MODEL_COHORT_ID,
        model_cohort_digest=MODEL_COHORT_DIGEST,
        cohort_member_id=COHORT_MEMBER_ID,
        reasoning_profile_id="thinking",
    )
    request_controls = _normalized_exp5_request_controls(config)
    member_plan = {
        "cohort_id": MODEL_COHORT_ID,
        "model_cohort_digest": MODEL_COHORT_DIGEST,
        "cohort_member_id": COHORT_MEMBER_ID,
        "provider_config_id": PROVIDER_CONFIG_ID,
        "selected_entry_id": MODEL_ENTRY_ID,
        "provider_family": "siliconflow",
        "provider_model_id": PROVIDER_MODEL_ID,
        "reasoning_profile_id": "thinking",
        "source_provider_config_digest": config.config_digest,
        "model_endpoint_identity_digest": ENDPOINT_DIGEST,
        "request_controls": request_controls,
    }
    approved_binding = {
        "status": "planned",
        "cohort_id": MODEL_COHORT_ID,
        "model_cohort_digest": MODEL_COHORT_DIGEST,
        "member_plans": {COHORT_MEMBER_ID: member_plan},
        "request_controls_snapshot": request_controls["comparable"],
        "request_controls_snapshot_digest": request_controls[
            "comparable_digest"
        ],
    }

    _binding, request_limits, _config = (
        formal_runner._condition_endpoint_contract(
            experiment_id=EXP5_EXPERIMENT_ID,
            condition=condition,
            ai_api_configs={
                PROVIDER_CONFIG_ID: config,
                formal_runner.APPROVED_ENDPOINT_BINDINGS_KEY: {
                    EXP5_EXPERIMENT_ID: approved_binding,
                },
            },
        )
    )

    assert request_limits["enable_thinking"] is True
    assert request_limits["thinking_budget"] == 32_768


def test_formal_runner_blocked_plan_never_dispatches_or_calls_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport_calls: list[object] = []

    def forbidden_dispatch(**_kwargs):
        raise AssertionError("blocked formal plan must not dispatch a condition")

    def transport(*_args, **_kwargs):
        transport_calls.append(object())
        raise AssertionError("blocked formal plan must not call transport")

    monkeypatch.setattr(formal_runner, "dispatch_paper_condition", forbidden_dispatch)
    blocked_plan = _dispatch_plan_type()(
        experiment_id="exp5_real_ai_model_endpoint_comparison",
        output_root=(tmp_path / "exp5_real_ai_model_endpoint_comparison").as_posix(),
        conditions=(),
        condition_selection_bindings=(),
        status="blocked",
        blocked_reason="incomplete endpoint cohort",
        paper_eligible_possible=False,
    )

    suite = formal_runner.execute_paper_formal_suite(
        dispatch_plans=(blocked_plan,),
        catalog_manifest=SimpleNamespace(catalog_digest=CATALOG_DIGEST),
        budget=_budget(
            planned_experiments=(),
            planned_conditions=0,
            planned_root_runs=0,
            planned_ai_units=0,
        ),
        budget_approval={
            "approval_mode": "user_bypassed",
            "budget_digest": BUDGET_DIGEST,
        },
        output_root=tmp_path,
        ai_api_configs={},
        transport=transport,
        real_transport=False,
        hard_limits={},
    )

    assert transport_calls == []
    assert suite.status == PaperStatus.BLOCKED
    assert suite.provider_attempt_count == 0
    assert suite.paper_eligible is False


def test_formal_runner_hard_limit_blocks_second_root_and_resume_keeps_first_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _two_case_dispatch_plan(tmp_path, config=config)
    adapter_calls: list[str] = []

    class CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

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

    def fake_case_dispatch(**kwargs):
        case_id = kwargs["case"]["case_id"]
        adapter_calls.append(case_id)
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=case_id,
        )

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", fake_case_dispatch)
    kwargs = {
        **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
        "catalog_manifest": {
            "catalog_digest": CATALOG_DIGEST,
            "factorization_cases": (
                {"case_id": "case-1", "expected_ai_unit_count": 1},
                {"case_id": "case-2", "expected_ai_unit_count": 1},
            ),
            "lean_cases": (),
            "lean_lemma_graph_cases": (),
        },
        "budget": _budget(
            planned_conditions=1,
            planned_root_runs=2,
            planned_ai_units=2,
        ),
        "hard_limits": {"max_total_provider_attempts": 1},
    }

    exhausted = formal_runner.execute_paper_formal_suite(**kwargs)
    assert exhausted.status == PaperStatus.BUDGET_EXHAUSTED
    assert adapter_calls == ["case-1"]

    resumed = formal_runner.execute_paper_formal_suite(**kwargs, resume=True)
    assert resumed.status == PaperStatus.BUDGET_EXHAUSTED
    assert adapter_calls == ["case-1"]


def test_smoke_unexpected_runner_error_records_reportable_blocked_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _two_case_dispatch_plan(tmp_path, config=config)
    adapter_calls: list[str] = []

    class CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

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

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, CallbackModule()),),
    )

    def failed_dispatch(**kwargs):
        case_id = kwargs["case"]["case_id"]
        adapter_calls.append(case_id)
        if case_id == "case-1":
            raise RuntimeError("adapter failed")
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=case_id,
        )

    monkeypatch.setattr(formal_runner, "dispatch_paper_case", failed_dispatch)

    kwargs = {
        **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
        "catalog_manifest": {
            "catalog_digest": CATALOG_DIGEST,
            "factorization_cases": (
                {"case_id": "case-1", "expected_ai_unit_count": 1},
                {"case_id": "case-2", "expected_ai_unit_count": 1},
            ),
            "lean_cases": (),
            "lean_lemma_graph_cases": (),
        },
        "budget": _budget(
            planned_conditions=1,
            planned_root_runs=2,
            planned_ai_units=2,
        ),
        "suite_id": "test_smoke_blocked",
        "execution_classification": {
            "formal": False,
            "pilot_only": True,
            "regression_only": True,
            "paper_eligible": False,
            "execution_scope": "smoke_suite",
            "ineligibility_reasons": ["smoke_suite", "pilot_only"],
        },
        "pre_execution_documents": {
            "smoke_execution_plan.json": {
                "schema_version": "tokenshare.paper_smoke_execution_plan.v1",
                "suite_id": "test_smoke_blocked",
                "baseline_policy": "omitted_for_smoke_regression",
                "items": [
                    {
                        "item_id": f"smoke-{case_id}",
                        "experiment_id": EXPERIMENT_ID,
                        "condition_id": "condition-1",
                        "case_id": case_id,
                        "repeat_id": 0,
                        "condition_selector": {
                            "domain": "factorization",
                            "difficulty": "easy",
                        },
                    }
                    for case_id in ("case-1", "case-2")
                ],
            }
        },
    }
    suite = formal_runner.execute_paper_formal_suite(**kwargs)

    assert suite.status == PaperStatus.BLOCKED
    assert suite.provider_attempt_count == 0
    assert suite.error_summary[0]["failure_stage"] == "adapter_runtime"
    assert suite.error_summary[0]["failure_kind"] == "RuntimeError"
    run_root = tmp_path / "experiments" / EXPERIMENT_ID / "runs" / "condition-1" / "0"
    current = json.loads((run_root / "CURRENT.json").read_text(encoding="utf-8"))
    generation_root = run_root / ".generations" / current["generation_id"]
    tasks = _generation_records(
        tmp_path,
        EXPERIMENT_ID,
        "condition-1",
        "per_task_results.jsonl",
    )
    attempts = _generation_records(
        tmp_path,
        EXPERIMENT_ID,
        "condition-1",
        "per_attempt_results.jsonl",
    )
    assert generation_root.is_dir()
    assert adapter_calls == ["case-1"]
    assert [task["task_id"] for task in tasks] == ["case-1", "case-2"]
    assert tasks[0]["root_status"] == "blocked"
    assert tasks[0]["outcome_status"] == "blocked_dependency"
    assert tasks[0]["evidence_integrity"] == "invalid"
    assert tasks[0]["failure_stage"] == "adapter_runtime"
    assert tasks[0]["failure_kind"] == "RuntimeError"
    assert tasks[1]["root_status"] == "not_started"
    assert tasks[1]["outcome_status"] == "blocked_dependency"
    assert tasks[1]["evidence_integrity"] == "invalid"
    for task in tasks:
        assert task["formal"] is False
        assert task["pilot_only"] is True
        assert task["regression_only"] is True
        assert task["paper_eligible"] is False
        assert {"smoke_suite", "pilot_only"}.issubset(
            task["ineligibility_reasons"]
        )
    assert [attempt["attempt_status"] for attempt in attempts] == [
        "blocked_dependency",
        "not_started",
    ]
    for attempt in attempts:
        assert attempt["formal"] is False
        assert attempt["pilot_only"] is True
        assert attempt["regression_only"] is True
        assert attempt["paper_eligible"] is False
    assert (run_root / "artifacts" / "case-1" / "dependency-blocked.json").is_file()
    assert (run_root / "artifacts" / "case-2" / "not-started.json").is_file()
    report = generate_paper_smoke_report(output_root=tmp_path)
    assert [row["root_status"] for row in report["rows"]] == [
        "blocked",
        "not_started",
    ]


def test_formal_runner_exp2_delegates_worker_count_to_protocol_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment_id = "exp2_real_ai_scalability"
    config = _ai_config()
    base_plan = _two_case_dispatch_plan(tmp_path, config=config)
    base_condition, base_selection = base_plan.bound_items()[0]
    condition = replace(
        base_condition,
        experiment_id=experiment_id,
        condition_id="condition-exp2-workers-2",
        worker_count=2,
    )
    selection = replace(base_selection, experiment_id=experiment_id)
    plan = replace(
        base_plan,
        experiment_id=experiment_id,
        output_root=(tmp_path / experiment_id).as_posix(),
        conditions=(condition,),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(condition, selection),
        ),
    )
    lock = Lock()
    active = 0
    observed_max = 0

    class Exp2CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
                experiment_id=experiment_id,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=experiment_id, rows=())

    def fake_case_dispatch(**kwargs):
        nonlocal active, observed_max
        with lock:
            active += 1
            observed_max = max(observed_max, active)
        sleep(0.05)
        with lock:
            active -= 1
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((experiment_id, Exp2CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", fake_case_dispatch)
    kwargs = {
        **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
        "catalog_manifest": {
            "catalog_digest": CATALOG_DIGEST,
            "factorization_cases": (
                {"case_id": "case-1"},
                {"case_id": "case-2"},
            ),
            "lean_cases": (),
            "lean_lemma_graph_cases": (),
        },
        "budget": _budget(
            planned_experiments=(experiment_id,),
            planned_conditions=1,
            planned_root_runs=2,
            planned_ai_units=2,
        ),
    }

    suite = formal_runner.execute_paper_formal_suite(**kwargs)

    assert suite.status == PaperStatus.COMPLETED
    assert observed_max == 1
    run_root = (
        tmp_path
        / "experiments"
        / experiment_id
        / "runs"
        / condition.condition_id
        / "0"
    )
    current = json.loads((run_root / "CURRENT.json").read_text(encoding="utf-8"))
    event_log = (
        run_root
        / ".generations"
        / current["generation_id"]
        / "events"
        / "event_log.jsonl"
    ).read_text(encoding="utf-8")
    assert event_log.count('"event_type":"TASK_COMPLETED"') == 2
    assert "AI_UNIT_STARTED" not in event_log
    assert "MERGE_GATE_COMPLETED" not in event_log


def test_formal_runner_exp2_reserves_hard_limit_before_parallel_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment_id = "exp2_real_ai_scalability"
    config = _ai_config()
    base_plan = _two_case_dispatch_plan(tmp_path, config=config)
    base_condition, base_selection = base_plan.bound_items()[0]
    condition = replace(
        base_condition,
        experiment_id=experiment_id,
        condition_id="condition-exp2-hard-limit",
        worker_count=2,
    )
    selection = replace(base_selection, experiment_id=experiment_id)
    plan = replace(
        base_plan,
        experiment_id=experiment_id,
        output_root=(tmp_path / experiment_id).as_posix(),
        conditions=(condition,),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(condition, selection),
        ),
    )
    adapter_calls: list[str] = []
    call_lock = Lock()

    class Exp2CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
                experiment_id=experiment_id,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=experiment_id, rows=())

    def fake_case_dispatch(**kwargs):
        with call_lock:
            adapter_calls.append(kwargs["case"]["case_id"])
        sleep(0.05)
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((experiment_id, Exp2CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", fake_case_dispatch)
    suite = formal_runner.execute_paper_formal_suite(
        **{
            **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
            "catalog_manifest": {
                "catalog_digest": CATALOG_DIGEST,
                "factorization_cases": (
                    {"case_id": "case-1", "expected_ai_unit_count": 1},
                    {"case_id": "case-2", "expected_ai_unit_count": 1},
                ),
                "lean_cases": (),
                "lean_lemma_graph_cases": (),
            },
            "budget": _budget(
                planned_experiments=(experiment_id,),
                planned_conditions=1,
                planned_root_runs=2,
                planned_ai_units=2,
            ),
            "hard_limits": {"max_total_provider_attempts": 1},
        }
    )

    assert adapter_calls == ["case-1"]
    assert suite.provider_attempt_count == 1
    assert suite.status == PaperStatus.BUDGET_EXHAUSTED


def test_formal_runner_does_not_deduplicate_same_root_across_conditions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    base = _planned_dispatch_plan(tmp_path, config=config)
    first_condition, first_selection = base.bound_items()[0]
    second_condition = replace(
        first_condition,
        condition_id="condition-2",
        repeat_id=1,
        seed=2,
    )
    second_selection = replace(first_selection, selection_id="selection-2")
    plan = replace(
        base,
        conditions=(first_condition, second_condition),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(
                first_condition,
                first_selection,
            ),
            FrozenConditionSelectionBinding.from_condition(
                second_condition,
                second_selection,
            ),
        ),
    )
    calls: list[tuple[str, str]] = []

    class CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

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

    def fake_case_dispatch(**kwargs):
        calls.append((kwargs["condition"].condition_id, kwargs["case"]["case_id"]))
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", fake_case_dispatch)
    suite = formal_runner.execute_paper_formal_suite(
        **{
            **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
            "budget": _budget(
                planned_conditions=2,
                planned_root_runs=2,
                planned_ai_units=2,
            ),
        }
    )

    assert suite.status == PaperStatus.COMPLETED
    assert calls == [("condition-1", "case-1"), ("condition-2", "case-1")]


def test_formal_runner_exp3_maps_planned_target_to_protocol_unit_and_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment_id = "exp3_real_ai_fault_recovery"
    config = _ai_config()
    plan = _plan_for_experiment(
        tmp_path=tmp_path,
        config=config,
        experiment_id=experiment_id,
        condition_id="condition-exp3-rate-fault",
        fault_type="false_positive",
        fault_rate=1.0,
    )
    adapter_calls: list[tuple[str | None, bool]] = []

    class Exp3CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
                experiment_id=experiment_id,
                execution_manifest=_shared_exp1_manifest(
                    request_limits=dict(context.request_limits),
                    fault_target_manifest={
                        "fault_type": "false_positive",
                        "selected_target_ai_unit_ids": ["case-1:range_0"],
                    },
                    worker_death_manifest=None,
                ),
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=experiment_id, rows=())

    def fake_case_dispatch(**kwargs):
        selected_unit_id = kwargs.get("selected_ai_unit_id")
        adapter_calls.append(
            (selected_unit_id, callable(kwargs.get("post_raw_output_hook")))
        )
        original = _faultable_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
            attempt_suffix="original",
        )
        original_attempt = original.attempt_results[0]
        raw_directive = kwargs["post_raw_output_hook"](
            request=SimpleNamespace(
                task_id=original_attempt.task_id,
                unit_id=original_attempt.unit_id,
                attempt_id=original_attempt.attempt_id,
                allocation_decision={"worker_id": original_attempt.worker_id},
                soft_hints={"planned_ai_unit_id": "range_0"},
            ),
            artifact_store=ArtifactStore(Path(kwargs["output_root"])),
            submitted_at=original_attempt.ended_at,
            usage_summary={
                "prompt_tokens": original_attempt.prompt_tokens,
                "completion_tokens": original_attempt.completion_tokens,
                "total_tokens": original_attempt.total_tokens,
                "cost_estimate": original_attempt.cost_estimate,
            },
            raw_output_ref=ArtifactRef.from_dict(original_attempt.raw_output_ref),
            provenance_ref=ArtifactRef.from_dict(original_attempt.provenance_ref),
            usage_ref=ArtifactRef.from_dict(original_attempt.usage_ref),
            content_text='{"result_kind":"no_factor"}',
            provider_family=original_attempt.provider,
            model=original_attempt.model,
            entry_id=original_attempt.entry_id,
        )
        assert raw_directive is None
        directive = kwargs[
            "post_raw_output_hook"
        ].after_parsed_candidate_persisted(
            ParsedCandidateContext(
                run_id=original_attempt.run_id,
                task_id=original_attempt.task_id,
                unit_id=original_attempt.unit_id,
                attempt_id=original_attempt.attempt_id,
                lease_id="lease-original",
                worker_id=original_attempt.worker_id,
                raw_output_ref=ArtifactRef.from_dict(
                    original_attempt.raw_output_ref
                ),
                original_parsed_output_ref=ArtifactRef.from_dict(
                    original_attempt.parsed_output_ref
                ),
                candidate_output_refs={
                    "result": ArtifactRef.from_dict(
                        original_attempt.parsed_output_ref
                    )
                },
                submitted_at=original_attempt.ended_at,
                experiment_unit_id="range_0",
            )
        )
        assert directive is not None
        replacement = _faultable_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
            attempt_suffix="replacement",
        )
        task = SimpleNamespace(
            **{
                **vars(original.task_result),
                "provider_attempt_count": 2,
                "total_tokens": 22,
                "cost_estimate": 0.25,
            }
        )
        return SimpleNamespace(
            **{
                **vars(original),
                "task_result": task,
                "attempt_results": [
                    original_attempt,
                    replacement.attempt_results[0],
                ],
                "event_records": [
                    *original.event_records,
                    *replacement.event_records,
                ],
            }
        )

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((experiment_id, Exp3CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", fake_case_dispatch)
    monkeypatch.setattr(
        formal_runner.FormalEvidenceStore,
        "build_shared_root_reference",
        _complete_shared_exp1_reference,
    )
    suite = formal_runner.execute_paper_formal_suite(
        **{
            **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
            "budget": _budget(
                planned_experiments=(experiment_id,),
                planned_conditions=1,
                planned_root_runs=1,
                planned_ai_units=1,
            ),
        }
    )

    assert suite.status == PaperStatus.COMPLETED
    assert adapter_calls == [(None, True)]
    attempts = _generation_records(
        tmp_path, experiment_id, "condition-exp3-rate-fault", "per_attempt_results.jsonl"
    )
    faults = _generation_records(
        tmp_path, experiment_id, "condition-exp3-rate-fault", "fault_injections.jsonl"
    )
    events = _generation_records(
        tmp_path,
        experiment_id,
        "condition-exp3-rate-fault",
        "events/event_log.jsonl",
    )
    assert {attempt["attempt_id"] for attempt in attempts} == {
        "attempt-case-1-original",
        "attempt-case-1-replacement",
    }
    assert len(faults) == 1
    assert faults[0]["unit_id"] == "unit-case-1"
    assert faults[0]["selected_target_ai_unit_id"] == "case-1:range_0"
    task = _generation_records(
        tmp_path,
        experiment_id,
        "condition-exp3-rate-fault",
        "per_task_results.jsonl",
    )[0]
    assert task["fault_injected"] is True
    assert task["recovered_fault_target_count"] == 1
    assert any(
        event["event_type"] == "EXPERIMENT_FAULT_OBSERVED"
        for event in events
    )


def test_formal_runner_exp3_worker_death_does_not_fabricate_process_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment_id = "exp3_real_ai_fault_recovery"
    config = _ai_config()
    plan = _plan_for_experiment(
        tmp_path=tmp_path,
        config=config,
        experiment_id=experiment_id,
        condition_id="condition-exp3-worker-death",
        fault_type="worker_death",
        fault_rate=0.0,
    )
    termination_policies: list[WorkerTerminationPolicy | None] = []
    dispatched_condition_ids: list[str] = []

    class Exp3WorkerCallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
                experiment_id=experiment_id,
                execution_manifest=_shared_exp1_manifest(
                    request_limits=dict(context.request_limits),
                    fault_target_manifest=None,
                    worker_death_manifest={
                        "dead_worker_count_target": 3,
                        "kill_progress_target_percent": 25,
                        "selected_target_ai_unit_ids_by_case": {
                            "case-1": [
                                "case-1:range_0",
                                "case-1:range_1",
                            ]
                        },
                        "planned_ai_unit_count_by_case": {"case-1": 20},
                    },
                ),
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=experiment_id, rows=())

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((experiment_id, Exp3WorkerCallbackModule()),),
    )
    def complete_without_runtime_death(**kwargs):
        dispatched_condition_ids.append(kwargs["condition"].condition_id)
        termination_policies.append(kwargs.get("worker_termination_policy"))
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )

    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_case",
        complete_without_runtime_death,
    )
    monkeypatch.setattr(
        formal_runner.FormalEvidenceStore,
        "build_shared_root_reference",
        _complete_shared_exp1_reference,
    )
    suite = formal_runner.execute_paper_formal_suite(
        **{
            **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
            "budget": _budget(
                planned_experiments=(experiment_id,),
                planned_conditions=1,
                planned_root_runs=1,
                planned_ai_units=1,
            ),
        }
    )

    assert suite.status == PaperStatus.COMPLETED
    assert dispatched_condition_ids == ["condition-exp3-worker-death"]
    assert termination_policies[0] == WorkerTerminationPolicy(
        target_planned_ai_unit_ids=("range_0", "range_1"),
        termination_count_target=3,
        kill_point="progress_25",
        total_planned_ai_unit_count=20,
        process_timeout_seconds=60.0,
    )
    faults = _generation_records(
        tmp_path, experiment_id, "condition-exp3-worker-death", "fault_injections.jsonl"
    )
    task = _generation_records(
        tmp_path, experiment_id, "condition-exp3-worker-death", "per_task_results.jsonl"
    )[0]
    assert faults == []
    assert task["worker_death_count"] == 0
    assert task["worker_replacement_count"] == 0
    assert task["worker_death_evidence_complete"] is False
    assert task["matched_baseline_condition_id"] == (
        "exp1_factorization_easy_w10_r0"
    )
    assert task["matched_baseline_evidence_ref"]["reference_id"] == (
        "shared-exp1-reference-case-1"
    )
    assert task["baseline_comparison_eligible"] is True
    assert "matched_baseline_total_tokens" not in task


def test_failed_exp1_shared_baseline_keeps_exp3_dispatch_and_nulls_comparison(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment_id = "exp3_real_ai_fault_recovery"
    config = _ai_config()
    plan = _plan_for_experiment(
        tmp_path=tmp_path,
        config=config,
        experiment_id=experiment_id,
        condition_id="condition-exp3-failed-shared-source",
        fault_type="worker_death",
    )

    class Exp3Module:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
                experiment_id=experiment_id,
                execution_manifest=_shared_exp1_manifest(
                    request_limits=dict(context.request_limits),
                    fault_target_manifest=None,
                    worker_death_manifest={
                        "dead_worker_count_target": 1,
                        "kill_progress_target_percent": 50,
                        "selected_target_ai_unit_ids_by_case": {
                            "case-1": ["case-1:range_0"]
                        },
                        "planned_ai_unit_count_by_case": {"case-1": 1},
                    },
                ),
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=experiment_id, rows=())

    provider_dispatches: list[str] = []

    def dispatch(**kwargs):
        provider_dispatches.append(kwargs["condition"].condition_id)
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )

    def failed_reference_builder(*args, **kwargs):
        reference = {
            **_complete_shared_exp1_reference(*args, **kwargs),
            "source_root_status": "failed",
            "baseline_comparison_eligible": False,
            "baseline_unavailable_reason": "source_exp1_failed_experimental",
        }
        core = {
            key: value
            for key, value in reference.items()
            if key
            not in {
                "source_hash",
                "source_reference_id",
                "planned_source_reference_id",
                "reference_policy_id",
            }
        }
        return {**reference, "source_hash": formal_runner.digest_json(core)}
    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((experiment_id, Exp3Module()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", dispatch)
    monkeypatch.setattr(
        formal_runner.FormalEvidenceStore,
        "build_shared_root_reference",
        failed_reference_builder,
    )

    suite = formal_runner.execute_paper_formal_suite(
        **{
            **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
            "budget": _budget(
                planned_experiments=(experiment_id,),
                planned_conditions=1,
                planned_root_runs=1,
                planned_ai_units=1,
            ),
        }
    )

    assert suite.status == PaperStatus.COMPLETED
    assert provider_dispatches == ["condition-exp3-failed-shared-source"]
    task = _generation_records(
        tmp_path,
        experiment_id,
        "condition-exp3-failed-shared-source",
        "per_task_results.jsonl",
    )[0]
    assert task["baseline"]["source_root_status"] == "failed"
    assert task["baseline_comparison_eligible"] is False
    assert task["baseline_unavailable_reason"] == (
        "source_exp1_failed_experimental"
    )


def test_formal_runner_worker_death_baseline_identity_mismatch_fails_closed_before_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exp3 = "exp3_real_ai_fault_recovery"
    exp4 = "exp4_real_ai_protocol_ablation"
    config = _ai_config()
    exp3_plan = _plan_for_experiment(
        tmp_path=tmp_path,
        config=config,
        experiment_id=exp3,
        condition_id="condition-exp3-invalid-baseline",
        fault_type="worker_death",
    )
    exp4_plan = _plan_for_experiment(
        tmp_path=tmp_path,
        config=config,
        experiment_id=exp4,
        condition_id="condition-exp4-must-not-run",
        ablation_mode="NO_VERIFICATION",
    )
    invoked_conditions: list[str] = []
    provider_dispatches: list[str] = []

    class Exp3CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            invoked_conditions.append(condition.condition_id)
            baseline = replace(
                condition,
                condition_id="condition-exp3-invalid-baseline-no-kill",
                fault_type="none",
                fault_rate=0.0,
            )
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
                experiment_id=exp3,
                execution_manifest={
                    "fault_target_manifest": None,
                    "worker_death_manifest": {
                        "dead_worker_count_target": 1,
                        "kill_progress_target_percent": 25,
                        "selected_target_ai_unit_ids_by_case": {
                            "case-1": ["case-1:range_0"]
                        },
                        "planned_ai_unit_count_by_case": {"case-1": 1},
                    },
                    "matched_baseline": {
                        "schema_version": (
                            "tokenshare.paper_exp3_matched_baseline.v1"
                        ),
                        "condition_id": baseline.condition_id,
                        "condition_digest": baseline.condition_digest,
                        "condition": baseline.to_dict(),
                        "source_kind": "dedicated_worker_death",
                        "additional_execution_required": True,
                        "repeat_id": baseline.repeat_id,
                        "seed": baseline.seed,
                        "worker_count": baseline.worker_count,
                        "request_limits": {
                            "max_tokens": 2048,
                            "timeout_seconds": 30,
                            "max_provider_attempts": 1,
                            "temperature": 0.0,
                            "top_p": 1.0,
                            "stream": False,
                            "enable_thinking": False,
                        },
                    },
                },
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=exp3, rows=())

    class Exp4CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            invoked_conditions.append(condition.condition_id)
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=exp4, rows=())

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((exp3, Exp3CallbackModule()), (exp4, Exp4CallbackModule())),
    )

    def dispatch(**kwargs):
        provider_dispatches.append(kwargs["condition"].condition_id)
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )

    monkeypatch.setattr(formal_runner, "dispatch_paper_case", dispatch)

    suite = formal_runner.execute_paper_formal_suite(
        **{
            **_formal_execution_kwargs(
                tmp_path=tmp_path,
                config=config,
                plan=exp3_plan,
            ),
            "dispatch_plans": (exp3_plan, exp4_plan),
            "budget": _budget(
                planned_experiments=(exp3, exp4),
                planned_conditions=2,
                planned_root_runs=2,
                planned_ai_units=2,
            ),
        }
    )

    assert suite.status == PaperStatus.BLOCKED
    assert invoked_conditions == ["condition-exp3-invalid-baseline"]
    assert provider_dispatches == []
    assert (tmp_path / "condition_results.jsonl").is_file()
    exp3_task = _generation_records(
        tmp_path,
        exp3,
        "condition-exp3-invalid-baseline",
        "per_task_results.jsonl",
    )[0]
    exp4_task = _generation_records(
        tmp_path,
        exp4,
        "condition-exp4-must-not-run",
        "per_task_results.jsonl",
    )[0]
    assert exp3_task["outcome_status"] == "blocked_dependency"
    assert exp3_task["evidence_integrity"] == "invalid"
    assert exp4_task["root_status"] == "not_started"


@pytest.mark.parametrize("execution_path", ("smoke", "formal"))
def test_failed_worker_death_closes_manifests_and_continues_exp4_in_both_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    execution_path: str,
) -> None:
    exp3 = "exp3_real_ai_fault_recovery"
    exp4 = "exp4_real_ai_protocol_ablation"
    config = _ai_config()
    exp3_plan = _plan_for_experiment(
        tmp_path=tmp_path,
        config=config,
        experiment_id=exp3,
        condition_id="condition-exp3-worker-death-failed",
        fault_type="worker_death",
    )
    exp4_plan = _plan_for_experiment(
        tmp_path=tmp_path,
        config=config,
        experiment_id=exp4,
        condition_id="condition-exp4-after-worker-death",
        ablation_mode="NO_VERIFICATION",
    )

    class Exp3CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
                experiment_id=exp3,
                execution_manifest=_shared_exp1_manifest(
                    request_limits=dict(context.request_limits),
                    fault_target_manifest=None,
                    worker_death_manifest={
                        "dead_worker_count_target": 1,
                        "kill_progress_target_percent": 50,
                        "selected_target_ai_unit_ids_by_case": {
                            "case-1": ["case-1:range_0"]
                        },
                        "planned_ai_unit_count_by_case": {"case-1": 1},
                    },
                ),
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=exp3, rows=())

    class Exp4CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
                experiment_id=exp4,
                mode_config={"ablation_mode": "NO_VERIFICATION"},
                ablation_profile={"disabled_mechanisms": ["verification"]},
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=exp4, rows=())

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((exp3, Exp3CallbackModule()), (exp4, Exp4CallbackModule())),
    )
    dispatched: list[str] = []

    def dispatch(**kwargs):
        condition = kwargs["condition"]
        dispatched.append(condition.condition_id)
        base = _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=condition,
            case_id=kwargs["case"]["case_id"],
        )
        if condition.condition_id == "condition-exp3-worker-death-failed":
            attempt = replace(
                base.attempt_results[0],
                attempt_status=PaperAttemptStatus.WORKER_DIED,
                error_kind="worker_died",
            )
            task = SimpleNamespace(
                **{
                    **vars(base.task_result),
                    "root_status": PaperTaskStatus.FAILED,
                }
            )
            return SimpleNamespace(
                **{
                    **vars(base),
                    "task_result": task,
                    "attempt_results": [attempt],
                    "fault_records": [
                        {
                            "schema_version": (
                                "tokenshare.paper_worker_death_incomplete.v1"
                            ),
                            "fault_type": "worker_death",
                            "dead_attempt": {
                                "attempt_id": attempt.attempt_id,
                                "unit_id": attempt.unit_id,
                            },
                            "replacement_fact": None,
                            "recovery_completed": False,
                            "evidence_complete": False,
                            "coordinator": {"survived": True},
                        }
                    ],
                }
            )
        if condition.experiment_id == exp4:
            return _rejected_adapter_result(
                output_root=Path(kwargs["output_root"]),
                condition=condition,
                case_id=kwargs["case"]["case_id"],
            )
        return base

    monkeypatch.setattr(formal_runner, "dispatch_paper_case", dispatch)
    monkeypatch.setattr(
        formal_runner.FormalEvidenceStore,
        "build_shared_root_reference",
        _complete_shared_exp1_reference,
    )
    smoke_items = []
    for plan in (exp3_plan, exp4_plan):
        condition, selection = plan.bound_items()[0]
        smoke_items.append(
            {
                "item_id": f"item-{condition.condition_id}",
                "experiment_id": condition.experiment_id,
                "condition_id": condition.condition_id,
                "condition_digest": condition.condition_digest,
                "selection_id": selection.selection_id,
                "selection_digest": selection.selection_digest,
                "case_id": "case-1",
                "repeat_id": condition.repeat_id,
                "condition_selector": {
                    "domain": condition.domain,
                    "difficulty": condition.difficulty,
                    "fault_type": condition.fault_type,
                    "ablation_mode": condition.ablation_mode,
                },
            }
        )
    budget = _budget(
        planned_experiments=(exp3, exp4),
        planned_conditions=2,
        planned_root_runs=2,
        planned_ai_units=2,
    )
    if execution_path == "smoke":
        from tokenshare.experiments.paper_smoke import (
            PaperSmokeExecutionPlan,
            PaperSmokeItem,
            PaperSmokeProfile,
            ResolvedPaperSmokeItem,
            execute_paper_smoke_suite,
        )

        profile_items = tuple(
            PaperSmokeItem(
                item_id=item["item_id"],
                experiment_id=item["experiment_id"],
                case_id=item["case_id"],
                repeat_id=item["repeat_id"],
                condition_selector=item["condition_selector"],
            )
            for item in smoke_items
        )
        profile = PaperSmokeProfile(
            suite_id="worker-death-continuation-smoke",
            profile_version="test-v1",
            catalog_id="test-catalog",
            catalog_version="test-v1",
            catalog_digest=CATALOG_DIGEST,
            experiment_ids=(exp3, exp4),
            expected_root_runs=2,
            output_mode="separate_root",
            items=profile_items,
            ineligibility_reasons=(
                "offline_regression",
                "smoke_baseline_not_requested",
            ),
            baseline_policy="omitted_for_smoke_regression",
        )
        execution_plan = PaperSmokeExecutionPlan(
            suite_id=profile.suite_id,
            profile_digest=profile.profile_digest,
            catalog_id=profile.catalog_id,
            catalog_version=profile.catalog_version,
            catalog_digest=profile.catalog_digest,
            output_root=str(tmp_path),
            experiment_ids=(exp3, exp4),
            items=tuple(ResolvedPaperSmokeItem(**item) for item in smoke_items),
            dispatch_plans=(exp3_plan, exp4_plan),
            root_case_filter={
                item["condition_id"]: (item["case_id"],)
                for item in smoke_items
            },
            baseline_policy="omitted_for_smoke_regression",
        )
        budget = replace(
            budget,
            quota_preflight={
                "provider_calls_made": 0,
                "budget_approval": {
                    "approval_mode": "user_bypassed",
                    "budget_digest": BUDGET_DIGEST,
                },
            },
        )
        suite = execute_paper_smoke_suite(
            profile=profile,
            execution_plan=execution_plan,
            catalog_manifest=_formal_execution_kwargs(
                tmp_path=tmp_path,
                config=config,
                plan=exp3_plan,
            )["catalog_manifest"],
            budget=budget,
            ai_api_configs={PROVIDER_CONFIG_ID: config},
            transport=object(),
            real_transport=False,
            hard_limits={},
            launch_manifest={
                "schema_version": "tokenshare.paper_smoke_launch_manifest.v1",
                "launch_id": "offline-worker-death-continuation",
                "paper_eligible": False,
            },
        )
    else:
        from tokenshare.experiments.paper_formal_metrics import (
            recompute_paper_formal_metrics,
        )
        from tokenshare.experiments.paper_formal_report import (
            generate_paper_formal_report,
        )

        suite = formal_runner.execute_paper_formal_suite(
            **{
                **_formal_execution_kwargs(
                    tmp_path=tmp_path,
                    config=config,
                    plan=exp3_plan,
                ),
                "dispatch_plans": (exp3_plan, exp4_plan),
                "budget": budget,
            }
        )
        metrics = recompute_paper_formal_metrics(tmp_path)
        formal_report = generate_paper_formal_report(
            output_root=tmp_path,
            metrics=metrics,
            secret_values=(),
        )
        assert metrics.paper_eligible is False
        assert formal_report.paper_eligible is False
        assert formal_report.formal_paper_table_generated is False

    assert suite.status == PaperStatus.COMPLETED_WITH_FAILURES
    assert dispatched == [
        "condition-exp3-worker-death-failed",
        "condition-exp4-after-worker-death",
    ]
    exp3_task = _generation_records(
        tmp_path,
        exp3,
        "condition-exp3-worker-death-failed",
        "per_task_results.jsonl",
    )[0]
    assert exp3_task["root_status"] == "failed"
    assert exp3_task["worker_death_evidence_complete"] is False
    assert exp3_task["outcome_status"] == "failed_experimental"
    assert exp3_task["evidence_integrity"] == "complete"
    assert (tmp_path / "condition_results.jsonl").is_file()
    for experiment_id in (exp3, exp4):
        manifest = json.loads(
            (
                tmp_path
                / "experiments"
                / experiment_id
                / "experiment_manifest.json"
            ).read_text(encoding="utf-8")
        )
        assert manifest["status"] == "completed_with_failures"
    if execution_path == "smoke":
        report = json.loads(
            (tmp_path / "metrics" / "smoke_summary.json").read_text(
                encoding="utf-8"
            )
        )
        assert report["suite_status"] == "completed_with_failures"
        assert report["row_count"] == 2
        exp3_row = next(
            row for row in report["rows"] if row["experiment_id"] == exp3
        )
        assert exp3_row["baseline_policy"] == (
            "omitted_for_smoke_regression"
        )
        assert exp3_row["baseline"] is None
        assert exp3_row["baseline_comparison_eligible"] is False
        assert exp3_row["baseline_unavailable_reason"] == (
            "smoke_baseline_not_requested"
        )
        assert exp3_row["outcome_status"] == "failed_experimental"
        assert exp3_row["evidence_integrity"] == "complete"
        expected_outputs = (
            "metrics/smoke_summary.json",
            "metrics/smoke_failures.json",
            "audit/smoke_eligibility_report.json",
            "audit/secret_scan_report.json",
            "audit/smoke_evidence_manifest.json",
        )
    else:
        suite_manifest = json.loads(
            (tmp_path / "suite_manifest.json").read_text(encoding="utf-8")
        )
        assert suite_manifest["formal"] is True
        assert suite_manifest["pilot_only"] is False
        assert suite_manifest["execution_scope"] == "formal_matrix"
        expected_outputs = (
            "metrics/formal_metrics.json",
            "audit/paper_eligibility_report.json",
            "audit/secret_scan_report.json",
            "formal_regression_report.md",
            "formal_report_result.json",
        )
    for relative in expected_outputs:
        assert (tmp_path / relative).is_file()


def test_formal_runner_exp4_persists_mode_specific_wrapper_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment_id = "exp4_real_ai_protocol_ablation"
    config = _ai_config()
    plan = _plan_for_experiment(
        tmp_path=tmp_path,
        config=config,
        experiment_id=experiment_id,
        condition_id="condition-exp4-no-verification",
        ablation_mode="NO_VERIFICATION",
    )
    dispatched_modes: list[str | None] = []

    class Exp4CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
                experiment_id=experiment_id,
                mode_config={"ablation_mode": "NO_VERIFICATION"},
                ablation_profile={"disabled_mechanisms": ["verification"]},
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=experiment_id, rows=())

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((experiment_id, Exp4CallbackModule()),),
    )
    def rejected_dispatch(**kwargs):
        dispatched_modes.append(kwargs.get("ablation_mode"))
        return _rejected_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )

    monkeypatch.setattr(formal_runner, "dispatch_paper_case", rejected_dispatch)
    suite = formal_runner.execute_paper_formal_suite(
        **{
            **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
            "budget": _budget(
                planned_experiments=(experiment_id,),
                planned_conditions=1,
                planned_root_runs=1,
                planned_ai_units=1,
            ),
        }
    )

    assert suite.status == PaperStatus.COMPLETED_WITH_FAILURES
    assert dispatched_modes == ["NO_VERIFICATION"]
    task = _generation_records(
        tmp_path, experiment_id, "condition-exp4-no-verification", "per_task_results.jsonl"
    )[0]
    events = _generation_records(
        tmp_path,
        experiment_id,
        "condition-exp4-no-verification",
        "events/event_log.jsonl",
    )
    assert task["root_status"] == "failed"
    assert task["ablation_runtime_flags"]["wrong_canonical_exposed"] is True
    assert task["final_deterministic_validity"] is False
    assert any(
        event["event_type"] == "EXPERIMENT_ABLATION_OBSERVED"
        for event in events
    )


@pytest.mark.parametrize(
    ("mode", "expected_status"),
    (
        ("FULL", PaperStatus.COMPLETED_WITH_FAILURES),
        ("NO_REQUEUE", PaperStatus.COMPLETED_WITH_FAILURES),
    ),
)
def test_formal_runner_exp4_observes_without_synthetic_replacement_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected_status: PaperStatus,
) -> None:
    experiment_id = "exp4_real_ai_protocol_ablation"
    config = _ai_config()
    plan = _plan_for_experiment(
        tmp_path=tmp_path,
        config=config,
        experiment_id=experiment_id,
        condition_id=f"condition-exp4-{mode.lower()}",
        ablation_mode=mode,
    )
    dispatch_calls: list[Path] = []

    class Exp4CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
                experiment_id=experiment_id,
                mode_config={"ablation_mode": mode},
                ablation_profile={"disabled_mechanisms": ["requeue"]},
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=experiment_id, rows=())

    def rejecting_then_complete_dispatch(**kwargs):
        output_root = Path(kwargs["output_root"])
        dispatch_calls.append(output_root)
        builder = (
            _rejected_adapter_result
            if len(dispatch_calls) == 1
            else _complete_adapter_result
        )
        return builder(
            output_root=output_root,
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((experiment_id, Exp4CallbackModule()),),
    )
    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_case",
        rejecting_then_complete_dispatch,
    )
    suite = formal_runner.execute_paper_formal_suite(
        **{
            **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
            "budget": _budget(
                planned_experiments=(experiment_id,),
                planned_conditions=1,
                planned_root_runs=1,
                planned_ai_units=1,
                max_provider_attempts=1,
            ),
        }
    )

    assert len(dispatch_calls) == 1
    assert suite.status == expected_status
    task = _generation_records(
        tmp_path,
        experiment_id,
        f"condition-exp4-{mode.lower()}",
        "per_task_results.jsonl",
    )[0]
    assert task["root_status"] == "failed"
    assert task["ablation_mode"] == mode
    assert "stuck_after_rejection" not in task
    assert "replacement_attempt_count" not in task


def test_formal_runner_exp5_persists_v2_observed_identity_without_fixed_entry_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    base = _planned_dispatch_plan(tmp_path, config=config)
    base_condition, base_selection = base.bound_items()[0]
    condition = replace(
        base_condition,
        experiment_id=EXP5_EXPERIMENT_ID,
        condition_id="condition-exp5-fixed-entry",
        model_cohort_id=MODEL_COHORT_ID,
        model_cohort_digest=MODEL_COHORT_DIGEST,
        cohort_member_id=COHORT_MEMBER_ID,
    )
    selection = replace(base_selection, experiment_id=EXP5_EXPERIMENT_ID)
    plan = replace(
        base,
        experiment_id=EXP5_EXPERIMENT_ID,
        output_root=(tmp_path / EXP5_EXPERIMENT_ID).as_posix(),
        conditions=(condition,),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(condition, selection),
        ),
    )
    request_controls = _normalized_exp5_request_controls(config)
    member_plan = {
        "cohort_id": MODEL_COHORT_ID,
        "model_cohort_digest": MODEL_COHORT_DIGEST,
        "cohort_member_id": COHORT_MEMBER_ID,
        "provider_config_id": PROVIDER_CONFIG_ID,
        "selected_entry_id": MODEL_ENTRY_ID,
        "provider_family": "siliconflow",
        "provider_model_id": PROVIDER_MODEL_ID,
        "reasoning_profile_id": "default",
        "source_provider_config_digest": config.config_digest,
        "model_endpoint_identity_digest": ENDPOINT_DIGEST,
        "request_controls": request_controls,
    }

    class Exp5CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
                experiment_id=EXP5_EXPERIMENT_ID,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=EXP5_EXPERIMENT_ID, rows=())

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXP5_EXPERIMENT_ID, Exp5CallbackModule()),),
    )
    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_case",
        lambda **kwargs: _exp5_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
            resolved_model="unexpected/resolved-model",
        ),
    )
    suite = formal_runner.execute_paper_formal_suite(
        **{
            **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
            "ai_api_configs": {
                PROVIDER_CONFIG_ID: config,
                formal_runner.APPROVED_ENDPOINT_BINDINGS_KEY: {
                    EXP5_EXPERIMENT_ID: {
                        "status": "planned",
                        "cohort_id": MODEL_COHORT_ID,
                        "model_cohort_digest": MODEL_COHORT_DIGEST,
                        "member_plans": {COHORT_MEMBER_ID: member_plan},
                        "request_controls_snapshot": request_controls[
                            "comparable"
                        ],
                        "request_controls_snapshot_digest": request_controls[
                            "comparable_digest"
                        ],
                    }
                },
            },
            "budget": _budget(
                planned_experiments=(EXP5_EXPERIMENT_ID,),
                planned_conditions=1,
                planned_root_runs=1,
                planned_ai_units=1,
            ),
        }
    )

    assert suite.status == PaperStatus.COMPLETED
    task = _generation_records(
        tmp_path, EXP5_EXPERIMENT_ID, condition.condition_id, "per_task_results.jsonl"
    )[0]
    attempt = _generation_records(
        tmp_path,
        EXP5_EXPERIMENT_ID,
        condition.condition_id,
        "per_attempt_results.jsonl",
    )[0]
    model_input = task["model_execution_records"][0]
    assert (
        model_input["record"]["schema_version"]
        == "tokenshare.paper_model_execution_record.v2"
    )
    assert model_input["record"]["identity_status"] == "model_identity_mismatch"
    assert "resolved_model_mismatch" in model_input["record"]["mismatch_reasons"]
    assert attempt["model_identity_audit"] == "model_identity_mismatch"
    assert attempt["model_identity_audit"] != "fixed_entry_match"
    assert attempt["cohort_member_id"] == COHORT_MEMBER_ID


def _planned_dispatch_plan(
    output_root: Path,
    *,
    config: AIAPIExecutorConfig,
) -> formal_runner.PaperExperimentDispatchPlan:
    condition = PaperExperimentCondition(
        experiment_id=EXPERIMENT_ID,
        condition_id="condition-1",
        domain="factorization",
        difficulty="easy",
        paper_difficulty="easy",
        worker_count=1,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="full_protocol",
        model_policy="fixed_entry",
        provider_config_id=PROVIDER_CONFIG_ID,
        model_entry_id=MODEL_ENTRY_ID,
        provider_family="siliconflow",
        provider_model_id=PROVIDER_MODEL_ID,
        reasoning_profile_id="default",
        source_provider_config_digest=config.config_digest,
        model_endpoint_identity_digest=ENDPOINT_DIGEST,
        repeat_id=0,
        seed=1,
        catalog_digest=CATALOG_DIGEST,
    )
    selection = FrozenCaseSelection(
        selection_id="selection-1",
        experiment_id=EXPERIMENT_ID,
        suite_version="paper_v1",
        catalog_version="catalog-v1",
        domain="factorization",
        paper_difficulty="easy",
        topic_family=None,
        ordered_case_ids=("case-1",),
        catalog_digest=CATALOG_DIGEST,
        expected_ai_unit_count=1,
        paper_eligible_required=True,
    )
    return _dispatch_plan_type()(
        experiment_id=EXPERIMENT_ID,
        output_root=(output_root / EXPERIMENT_ID).as_posix(),
        conditions=(condition,),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(condition, selection),
        ),
    )


def _two_case_dispatch_plan(
    output_root: Path,
    *,
    config: AIAPIExecutorConfig,
) -> formal_runner.PaperExperimentDispatchPlan:
    plan = _planned_dispatch_plan(output_root, config=config)
    condition, selection = plan.bound_items()[0]
    selection = replace(
        selection,
        ordered_case_ids=("case-1", "case-2"),
        expected_ai_unit_count=2,
    )
    return replace(
        plan,
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(condition, selection),
        ),
    )


def _plan_for_experiment(
    *,
    tmp_path: Path,
    config: AIAPIExecutorConfig,
    experiment_id: str,
    condition_id: str,
    fault_type: str = "none",
    fault_rate: float = 0.0,
    ablation_mode: str = "full_protocol",
) -> formal_runner.PaperExperimentDispatchPlan:
    base = _planned_dispatch_plan(tmp_path, config=config)
    base_condition, base_selection = base.bound_items()[0]
    condition = replace(
        base_condition,
        experiment_id=experiment_id,
        condition_id=condition_id,
        fault_type=fault_type,
        fault_rate=fault_rate,
        ablation_mode=ablation_mode,
    )
    selection = replace(base_selection, experiment_id=experiment_id)
    return replace(
        base,
        experiment_id=experiment_id,
        output_root=(tmp_path / experiment_id).as_posix(),
        conditions=(condition,),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(condition, selection),
        ),
    )


def _ai_config() -> AIAPIExecutorConfig:
    return AIAPIExecutorConfig(
        schema_version="phase7.ai_api_executor_config.v1",
        executor_id="formal-runner-test",
        provider_family="siliconflow",
        selection_policy={"kind": "fixed"},
        defaults={
            "timeout_seconds": 30,
            "max_tokens": 512,
            "temperature": 0.2,
            "top_p": 1.0,
            "stream": False,
            "max_provider_attempts": 1,
        },
        entries=[
            AIAPIProviderEntry(
                entry_id=MODEL_ENTRY_ID,
                enabled=True,
                base_url="https://example.invalid/v1",
                api_key_env="TOKENSHARE_FORMAL_RUNNER_TEST_KEY",
                model=PROVIDER_MODEL_ID,
                endpoint="/chat/completions",
                supports_json_mode=True,
                supports_streaming=False,
                request_overrides={
                    "max_tokens": 1024,
                    "temperature": 0.0,
                    "enable_thinking": False,
                },
                pricing={
                    "currency": "USD",
                    "input_per_million_tokens": 1.0,
                    "output_per_million_tokens": 2.0,
                },
                tags=["test"],
            )
        ],
        local_concurrency={"max_in_flight_global": 1},
        metadata={"test_only": True},
    )


def _dispatch_plan_type():
    return formal_runner.execute_paper_formal_suite.__globals__[
        "PaperExperimentDispatchPlan"
    ]


def _budget(
    *,
    planned_experiments: tuple[str, ...] = (EXPERIMENT_ID,),
    planned_conditions: int,
    planned_root_runs: int,
    planned_ai_units: int,
    max_provider_attempts: int | None = None,
) -> PaperBudgetResult:
    attempt_upper_bound = (
        planned_ai_units
        if max_provider_attempts is None
        else max_provider_attempts
    )
    return PaperBudgetResult(
        budget_digest=BUDGET_DIGEST,
        planned_experiments=planned_experiments,
        planned_conditions=planned_conditions,
        planned_root_runs=planned_root_runs,
        planned_ai_units=planned_ai_units,
        max_provider_attempts=attempt_upper_bound,
        token_upper_bound=attempt_upper_bound * 1024,
        cost_upper_bound=attempt_upper_bound * 0.01,
        wall_clock_estimate=float(max(1, planned_ai_units)),
        quota_preflight={"provider_calls_made": 0},
        rate_limit_preflight={"status": "not_checked"},
        disk_estimate={"bytes": 0},
        status=PaperStatus.PLANNED,
    )


def _formal_execution_kwargs(
    *,
    tmp_path: Path,
    config: AIAPIExecutorConfig,
    plan: formal_runner.PaperExperimentDispatchPlan,
) -> dict[str, object]:
    return {
        "dispatch_plans": (plan,),
        "catalog_manifest": {
            "catalog_digest": CATALOG_DIGEST,
            "factorization_cases": (
                {"case_id": "case-1", "expected_ai_unit_count": 1},
            ),
            "lean_cases": (),
            "lean_lemma_graph_cases": (),
        },
        "budget": _budget(
            planned_conditions=1,
            planned_root_runs=1,
            planned_ai_units=1,
        ),
        "budget_approval": {
            "approval_mode": "user_bypassed",
            "budget_digest": BUDGET_DIGEST,
        },
        "output_root": tmp_path,
        "ai_api_configs": {PROVIDER_CONFIG_ID: config},
        "transport": object(),
        "real_transport": False,
        "hard_limits": {},
    }


def _normalized_exp5_request_controls(
    config: AIAPIExecutorConfig,
) -> dict[str, object]:
    effective = {
        **config.defaults,
        **config.entries[0].request_overrides,
    }
    comparable = {
        field_name: effective[field_name]
        for field_name in formal_runner.EXP5_COMPARABLE_REQUEST_CONTROL_FIELDS
    }
    comparable["domain_contracts"] = {
        domain: dict(contract)
        for domain, contract in (
            formal_runner.EXP5_DOMAIN_EXECUTION_CONTRACTS.items()
        )
    }
    reasoning = {
        field_name: effective[field_name]
        for field_name in (
            "enable_thinking",
            "thinking_budget",
            "thinking",
            "reasoning_effort",
        )
        if field_name in effective
    }
    return {
        "schema_version": "tokenshare.paper_exp5_request_controls.v1",
        "comparable": comparable,
        "comparable_digest": formal_runner.digest_json(comparable),
        "provider_specific_reasoning": reasoning,
        "provider_specific_reasoning_digest": formal_runner.digest_json(
            reasoning
        ),
    }


def _exp5_adapter_result(
    *,
    output_root: Path,
    condition: PaperExperimentCondition,
    case_id: str,
    resolved_model: str,
) -> SimpleNamespace:
    base = _complete_adapter_result(
        output_root=output_root,
        condition=condition,
        case_id=case_id,
    )
    store = ArtifactStore(output_root)
    created_at = "2026-07-20T00:00:00Z"
    request_ref = store.save_json(
        {"model": PROVIDER_MODEL_ID},
        artifact_id="exp5-request.json",
        artifact_type="AIRequest",
        artifact_schema_id="test.exp5.request",
        artifact_schema_version="v1",
        source={"test": "formal-runner-exp5"},
        metadata={},
        created_at=created_at,
    )
    raw_body = {
        "schema_version": "phase7.raw_model_output.v2",
        "provider_family": "siliconflow",
        "entry_id": MODEL_ENTRY_ID,
        "configured_model": PROVIDER_MODEL_ID,
        "requested_model": PROVIDER_MODEL_ID,
        "resolved_model": resolved_model,
        "response_model_status": "present",
        "raw_response_json": {"model": resolved_model},
    }
    raw_ref = store.save_json(
        raw_body,
        artifact_id="exp5-raw.json",
        artifact_type="RawModelOutput",
        artifact_schema_id="phase7.raw_model_output",
        artifact_schema_version="v2",
        source={"test": "formal-runner-exp5"},
        metadata={},
        created_at=created_at,
    )
    provenance_ref = store.save_json(
        {
            "provider_family": "siliconflow",
            "entry_id": MODEL_ENTRY_ID,
            "configured_model": PROVIDER_MODEL_ID,
        },
        artifact_id="exp5-provenance.json",
        artifact_type="AIProviderCallProvenance",
        artifact_schema_id="test.exp5.provenance",
        artifact_schema_version="v1",
        source={"test": "formal-runner-exp5"},
        metadata={},
        created_at=created_at,
    )
    usage_ref = store.save_json(
        {"total_tokens": 11, "cost_estimate": 0.125},
        artifact_id="exp5-usage.json",
        artifact_type="AIUsage",
        artifact_schema_id="test.exp5.usage",
        artifact_schema_version="v1",
        source={"test": "formal-runner-exp5"},
        metadata={},
        created_at=created_at,
    )
    expected_identity = {
        "model_cohort_id": MODEL_COHORT_ID,
        "model_cohort_digest": MODEL_COHORT_DIGEST,
        "cohort_member_id": COHORT_MEMBER_ID,
        "provider_config_id": PROVIDER_CONFIG_ID,
        "selected_entry_id": MODEL_ENTRY_ID,
        "provider_family": "siliconflow",
        "provider_model_id": PROVIDER_MODEL_ID,
        "reasoning_profile_id": "default",
        "effective_reasoning_controls": {"enable_thinking": False},
    }
    record = PaperModelExecutionRecord(
        condition_id=condition.condition_id,
        repeat_id=condition.repeat_id,
        run_id=f"run-{case_id}",
        task_id=case_id,
        unit_id=f"unit-{case_id}",
        attempt_id=f"attempt-{case_id}",
        expected_identity=expected_identity,
        source_provider_config_digest="sha256:" + "5" * 64,
        prepared_execution_config_digest="sha256:" + "6" * 64,
        request_ref=request_ref.to_dict(),
        provenance_ref=provenance_ref.to_dict(),
        raw_output_ref=raw_ref.to_dict(),
        usage_ref=usage_ref.to_dict(),
        actual_request_identities=[
            {
                "schema_version": "phase7.provider_request_identity.v2",
                "provider_family": "siliconflow",
                "entry_id": MODEL_ENTRY_ID,
                "configured_model": PROVIDER_MODEL_ID,
                "requested_model": PROVIDER_MODEL_ID,
                "reasoning_controls": {"enable_thinking": False},
                "effective_request_controls_digest": "sha256:" + "7" * 64,
            }
        ],
        actual_provider_attempts=[
            {
                "provider_family": "siliconflow",
                "entry_id": MODEL_ENTRY_ID,
                "configured_model": PROVIDER_MODEL_ID,
                "result_kind": "succeeded",
            }
        ],
        requested_model=PROVIDER_MODEL_ID,
        resolved_model=resolved_model,
        response_model_status="present",
        identity_status="model_identity_mismatch",
        mismatch_reasons=["resolved_model_mismatch"],
        paper_eligible=False,
        created_at=created_at,
    )
    model_ref = store.save_json(
        record.to_dict(),
        artifact_id="exp5-model-execution.json",
        artifact_type="PaperModelExecutionRecord",
        artifact_schema_id="tokenshare.paper_model_execution_record",
        artifact_schema_version="v2",
        source={"test": "formal-runner-exp5"},
        metadata={"identity_status": record.identity_status},
        created_at=created_at,
    )
    original_attempt = base.attempt_results[0]
    attempt = replace(
        original_attempt,
        request_ref=request_ref.to_dict(),
        raw_output_ref=raw_ref.to_dict(),
        provenance_ref=provenance_ref.to_dict(),
        usage_ref=usage_ref.to_dict(),
        model_execution_record_ref=model_ref.to_dict(),
    )
    task = SimpleNamespace(
        **{
            **vars(base.task_result),
            "paper_eligible": False,
            "artifact_refs": [
                request_ref.to_dict(),
                raw_ref.to_dict(),
                provenance_ref.to_dict(),
                usage_ref.to_dict(),
                model_ref.to_dict(),
            ],
        }
    )
    return SimpleNamespace(
        **{
            **vars(base),
            "task_result": task,
            "attempt_results": [attempt],
        }
    )


def _complete_adapter_result(
    *,
    output_root: Path,
    condition: PaperExperimentCondition,
    case_id: str,
) -> SimpleNamespace:
    adapter_store = ArtifactStore(output_root)
    raw_ref = adapter_store.save_bytes(
        b"raw model output",
        artifact_id="raw-output.txt",
        artifact_type="raw_model_output",
        media_type="text/plain",
        artifact_schema_id="test.raw_output",
        artifact_schema_version="v1",
        source={"test": "formal-runner"},
        metadata={},
        created_at="2026-07-20T00:00:00Z",
    )
    request_ref = adapter_store.save_bytes(
        b"request body",
        artifact_id="request.json",
        artifact_type="provider_request",
        media_type="application/json",
        artifact_schema_id="test.request",
        artifact_schema_version="v1",
        source={"test": "formal-runner"},
        metadata={},
        created_at="2026-07-20T00:00:00Z",
    )
    attempt = PaperAttemptResult(
        condition_id=condition.condition_id,
        repeat_id=condition.repeat_id,
        run_id=f"run-{case_id}",
        task_id=case_id,
        unit_id=f"unit-{case_id}",
        attempt_id=f"attempt-{case_id}",
        worker_id="worker-1",
        provider_attempt_index=1,
        attempt_status=PaperAttemptStatus.SUCCEEDED,
        provider="siliconflow",
        model=PROVIDER_MODEL_ID,
        entry_id=MODEL_ENTRY_ID,
        request_ref=request_ref.to_dict(),
        raw_output_ref=raw_ref.to_dict(),
        parsed_output_ref=None,
        parse_failure_ref=None,
        provenance_ref=None,
        usage_ref=None,
        started_at="2026-07-20T00:00:00Z",
        ended_at="2026-07-20T00:00:01Z",
        latency_ms=1000,
        prompt_tokens=5,
        completion_tokens=6,
        total_tokens=11,
        cost_estimate=0.125,
        error_kind=None,
        fault_injection_ref=None,
        paper_eligible=False,
    )
    task = SimpleNamespace(
        task_id=case_id,
        repeat_id=condition.repeat_id,
        root_status=PaperTaskStatus.COMPLETED,
        provider_attempt_count=1,
        total_tokens=11,
        cost_estimate=0.125,
        artifact_refs=[raw_ref.to_dict(), request_ref.to_dict()],
        event_refs=[{"event_id": f"event-{case_id}", "event_type": "TASK_COMPLETED"}],
    )
    return SimpleNamespace(
        task_result=task,
        attempt_results=[attempt],
        fault_records=[],
        event_records=[{"event_id": f"event-{case_id}", "event_type": "TASK_COMPLETED"}],
        eligibility_report=SimpleNamespace(paper_eligible=False),
        run_evidence={
            "protocol_runtime": {
                "case_id": case_id,
                "factor_position_quantile": "early",
                "runtime_observation": {
                    "schema_version": "tokenshare.protocol_runtime_observation.v1",
                    "run_id": f"run-{case_id}",
                    "runtime_started_at": "2026-07-20T00:00:00Z",
                    "runtime_ended_at": "2026-07-20T00:00:01Z",
                    "runtime_wall_clock_ms": 1000,
                    "planned_ai_unit_ids": ["range_0"],
                    "dispatched_ai_unit_ids": ["range_0"],
                    "completed_ai_unit_ids": ["range_0"],
                    "unscheduled_ai_unit_ids": [],
                    "in_flight_ai_unit_ids_at_witness": [],
                    "witness_observed_at": "2026-07-20T00:00:01Z",
                    "worker_execution_facts": [],
                    "observed_peak_concurrency": 1,
                },
                "generation_identity": {
                    "schema_version": "tokenshare.paper_runtime_generation_identity.v1",
                    "run_id": f"run-{case_id}",
                    "task_id": case_id,
                    "root_unit_id": f"root-{case_id}",
                    "event_count": 1,
                    "first_event_id": f"event-{case_id}",
                    "last_event_id": f"event-{case_id}",
                    "last_event_hash": f"sha256:event-{case_id}",
                    "ledger_digest": f"sha256:ledger-{case_id}",
                }
            }
        },
    )


def _faultable_adapter_result(
    *,
    output_root: Path,
    condition: PaperExperimentCondition,
    case_id: str,
    attempt_suffix: str,
) -> SimpleNamespace:
    store = ArtifactStore(output_root)
    created_at = "2026-07-19T00:00:00Z"

    def save_json(artifact_id: str, artifact_type: str, body: dict[str, object]):
        return store.save_json(
            body,
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            artifact_schema_id=f"test.{artifact_type.lower()}",
            artifact_schema_version="v1",
            source={"test": "formal-exp3-runner"},
            metadata={},
            created_at=created_at,
        )

    raw_ref = save_json(
        f"raw-{case_id}-{attempt_suffix}",
        "RawModelOutput",
        {"content_text": '{"result_kind":"no_factor"}'},
    )
    parsed_ref = save_json(
        f"parsed-{case_id}-{attempt_suffix}",
        "ParsedModelOutput",
        {
            "result_kind": "no_factor",
            "target_n": "91",
            "range_start": "2",
            "range_end": "10",
            "found_factor": None,
            "cofactor": None,
        },
    )
    provenance_ref = save_json(
        f"provenance-{case_id}-{attempt_suffix}",
        "AIProviderCallProvenance",
        {"provider_family": "siliconflow", "model": PROVIDER_MODEL_ID},
    )
    usage_ref = save_json(
        f"usage-{case_id}-{attempt_suffix}",
        "AIUsage",
        {"total_tokens": 11, "cost_estimate": 0.125},
    )
    request_ref = save_json(
        f"request-{case_id}-{attempt_suffix}",
        "AIRequest",
        {"model": PROVIDER_MODEL_ID},
    )
    attempt = PaperAttemptResult(
        condition_id=condition.condition_id,
        repeat_id=condition.repeat_id,
        run_id=f"run-{case_id}",
        task_id=case_id,
        unit_id=f"unit-{case_id}",
        attempt_id=f"attempt-{case_id}-{attempt_suffix}",
        worker_id="worker-1",
        provider_attempt_index=1 if attempt_suffix == "original" else 2,
        attempt_status=PaperAttemptStatus.SUCCEEDED,
        provider="siliconflow",
        model=PROVIDER_MODEL_ID,
        entry_id=MODEL_ENTRY_ID,
        request_ref=request_ref.to_dict(),
        raw_output_ref=raw_ref.to_dict(),
        parsed_output_ref=parsed_ref.to_dict(),
        parse_failure_ref=None,
        provenance_ref=provenance_ref.to_dict(),
        usage_ref=usage_ref.to_dict(),
        started_at=created_at,
        ended_at="2026-07-19T00:00:01Z",
        latency_ms=1000,
        prompt_tokens=5,
        completion_tokens=6,
        total_tokens=11,
        cost_estimate=0.125,
        error_kind=None,
        fault_injection_ref=None,
        paper_eligible=False,
    )
    task = SimpleNamespace(
        task_id=case_id,
        repeat_id=condition.repeat_id,
        root_status=PaperTaskStatus.COMPLETED,
        provider_attempt_count=1,
        total_tokens=11,
        cost_estimate=0.125,
        artifact_refs=[
            raw_ref.to_dict(),
            parsed_ref.to_dict(),
            provenance_ref.to_dict(),
            usage_ref.to_dict(),
            request_ref.to_dict(),
        ],
        event_refs=[],
    )
    return SimpleNamespace(
        task_result=task,
        attempt_results=[attempt],
        fault_records=[],
        event_records=[
            {
                "event_id": f"event-{case_id}-{attempt_suffix}",
                "event_type": "TASK_COMPLETED",
            }
        ],
        eligibility_report=SimpleNamespace(paper_eligible=False),
    )


def _rejected_adapter_result(
    *,
    output_root: Path,
    condition: PaperExperimentCondition,
    case_id: str,
) -> SimpleNamespace:
    result = _faultable_adapter_result(
        output_root=output_root,
        condition=condition,
        case_id=case_id,
        attempt_suffix="rejected",
    )
    rejected_attempt = replace(
        result.attempt_results[0],
        attempt_status=PaperAttemptStatus.VERIFICATION_REJECTED,
        error_kind="verifier_rejected",
    )
    task = SimpleNamespace(
        **{
            **vars(result.task_result),
            "root_status": PaperTaskStatus.FAILED,
        }
    )
    return SimpleNamespace(
        **{
            **vars(result),
            "task_result": task,
            "attempt_results": [rejected_attempt],
        }
    )


def _generation_records(
    suite_root: Path,
    experiment_id: str,
    condition_id: str,
    relative_path: str,
) -> list[dict[str, object]]:
    run_root = (
        suite_root
        / "experiments"
        / experiment_id
        / "runs"
        / condition_id
        / "0"
    )
    current = json.loads((run_root / "CURRENT.json").read_text(encoding="utf-8"))
    path = run_root / ".generations" / current["generation_id"] / relative_path
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_formal_runner_smoke_filter_executes_one_canonical_root_and_freezes_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _two_case_dispatch_plan(tmp_path, config=config)
    condition, selection = plan.bound_items()[0]
    selected_case_id = selection.ordered_case_ids[0]
    dispatched: list[str] = []

    class SmokeCallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("smoke execution consumes the canonical plan")

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

    def fake_case_dispatch(**kwargs):
        case_id = str(kwargs["case"]["case_id"])
        dispatched.append(case_id)
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=case_id,
        )

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, SmokeCallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", fake_case_dispatch)
    kwargs = {
        **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
        "catalog_manifest": {
            "catalog_digest": CATALOG_DIGEST,
            "factorization_cases": (
                {"case_id": "case-1", "expected_ai_unit_count": 1},
                {"case_id": "case-2", "expected_ai_unit_count": 1},
            ),
            "lean_cases": (),
            "lean_lemma_graph_cases": (),
        },
        "budget": _budget(
            planned_conditions=1,
            planned_root_runs=1,
            planned_ai_units=1,
        ),
        "root_case_filter": {condition.condition_id: (selected_case_id,)},
        "execution_classification": {
            "formal": False,
            "pilot_only": True,
            "regression_only": True,
            "paper_eligible": False,
            "execution_scope": "smoke_suite",
            "ineligibility_reasons": ["smoke_suite", "pilot_only"],
        },
        "suite_id": "test_smoke",
        "pre_execution_documents": {
            "smoke_profile.json": {
                "schema_version": "tokenshare.paper_smoke_profile.v1",
                "suite_id": "test_smoke",
                "paper_eligible": False,
            },
            "smoke_execution_plan.json": {
                "schema_version": "tokenshare.paper_smoke_execution_plan.v1",
                "suite_id": "test_smoke",
                "direct_root_run_count": 1,
                "formal": False,
                "pilot_only": True,
                "regression_only": True,
                "paper_eligible": False,
                "ineligibility_reasons": ["smoke_suite", "pilot_only"],
                "items": [
                    {
                        "item_id": "smoke-root",
                        "experiment_id": EXPERIMENT_ID,
                        "condition_id": condition.condition_id,
                        "condition_digest": condition.condition_digest,
                        "selection_id": selection.selection_id,
                        "selection_digest": selection.selection_digest,
                        "case_id": selected_case_id,
                        "repeat_id": condition.repeat_id,
                        "condition_selector": {
                            "domain": condition.domain,
                            "difficulty": condition.difficulty,
                            "worker_count": condition.worker_count,
                        },
                    }
                ],
            },
        },
    }

    suite = formal_runner.execute_paper_formal_suite(**kwargs)

    assert dispatched == [selected_case_id]
    assert suite.suite_id == "test_smoke"
    assert suite.paper_eligible is False
    suite_manifest = json.loads(
        (tmp_path / "suite_manifest.json").read_text(encoding="utf-8")
    )
    assert suite_manifest["formal"] is False
    assert suite_manifest["pilot_only"] is True
    assert suite_manifest["regression_only"] is True
    tasks = _generation_records(
        tmp_path,
        EXPERIMENT_ID,
        condition.condition_id,
        "per_task_results.jsonl",
    )
    assert [task["task_id"] for task in tasks] == [selected_case_id]
    assert tasks[0]["formal"] is False
    assert tasks[0]["pilot_only"] is True
    assert tasks[0]["regression_only"] is True
    assert tasks[0]["paper_eligible"] is False
    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_case",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("smoke replay must not call provider dispatch")
        ),
    )
    from tokenshare.experiments.paper_smoke import replay_paper_smoke_suite

    replayed = replay_paper_smoke_suite(output_root=tmp_path)
    summary = json.loads(
        (tmp_path / "metrics" / "smoke_summary.json").read_text(encoding="utf-8")
    )
    assert replayed.suite_id == "test_smoke"
    assert summary["provider_attempt_count"] == 0
    assert summary["total_tokens"] == 0
    assert summary["cost_estimate"] == 0.0
    assert summary["provider_actual_billing"] is None
    assert summary["provider_actual_billing_available"] is False
