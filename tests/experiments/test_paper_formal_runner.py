import hashlib
import inspect
import json
from dataclasses import replace
from pathlib import Path
from threading import Lock
from time import sleep
from types import SimpleNamespace

import pytest

import tokenshare.experiments.paper_budget as paper_budget
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
from tokenshare.local_runtime import (
    ParsedCandidateContext,
    WorkerTerminationPolicy,
    build_experiment_ablation_gate_applied_observation,
)
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger


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


def _smoke_execution_classification() -> dict[str, object]:
    return {
        "formal": False,
        "pilot_only": True,
        "regression_only": True,
        "paper_eligible": False,
        "execution_scope": "smoke_suite",
        "ineligibility_reasons": ["smoke_suite", "pilot_only"],
    }


def _condition_result(
    status: PaperStatus,
    *,
    evidence_complete_experimental_failure: bool = False,
) -> PaperConditionResult:
    return PaperConditionResult(
        condition_id="condition-status-probe",
        status=status,
        repeat_count=1,
        task_count=1,
        completed_root_count=0,
        failed_root_count=1,
        blocked_root_count=0,
        provider_attempt_count=1,
        metrics_ref=(
            {
                "evidence_integrity": "complete",
                "outcome_counts": {"failed_experimental": 1},
            }
            if evidence_complete_experimental_failure
            else None
        ),
    )


@pytest.mark.parametrize(
    "status",
    (
        PaperStatus.FAILED,
        PaperStatus.BLOCKED,
        PaperStatus.BUDGET_EXHAUSTED,
        PaperStatus.INCOMPLETE,
    ),
)
def test_suite_status_preserves_infrastructure_terminal_status(
    status: PaperStatus,
) -> None:
    assert formal_runner._suite_status(
        plans=(SimpleNamespace(status="planned"),),
        results=[_condition_result(status)],
    ) is status


@pytest.mark.parametrize(
    "status",
    (
        PaperStatus.FAILED,
        PaperStatus.BLOCKED,
        PaperStatus.BUDGET_EXHAUSTED,
    ),
)
def test_smoke_classification_does_not_wash_infrastructure_status(
    status: PaperStatus,
) -> None:
    assert formal_runner._classified_suite_status(
        status,
        classification=_smoke_execution_classification(),
    ) is status


def test_smoke_keeps_evidence_complete_experimental_failure_nonfatal() -> None:
    status = formal_runner._suite_status(
        plans=(SimpleNamespace(status="planned"),),
        results=[
            _condition_result(
                PaperStatus.COMPLETED_WITH_FAILURES,
                evidence_complete_experimental_failure=True,
            )
        ],
    )

    assert status is PaperStatus.COMPLETED_WITH_FAILURES
    assert formal_runner._classified_suite_status(
        status,
        classification=_smoke_execution_classification(),
    ) is PaperStatus.COMPLETED_WITH_FAILURES


def test_smoke_classification_does_not_make_technical_attempt_evidence_incomplete() -> None:
    classification = _smoke_execution_classification()
    classified_attempt = {
        "paper_eligible": False,
        "paper_ineligibility_reasons": [],
        "ineligibility_reasons": classification["ineligibility_reasons"],
    }

    reasons = formal_runner._task_checkpoint_ineligibility_reasons(
        task={
            "record_scope": "protocol",
            "root_status": "completed",
            "evidence_artifact_refs": [{"artifact_id": "artifact-complete"}],
        },
        attempts=(classified_attempt,),
        events=({"record_scope": "protocol", "event_id": "event-complete"},),
        protocol_runtime={
            "execution_scope": "whole_root",
            "selected_ai_unit_ids": [],
        },
        real_transport=True,
        transport=object(),
        source_task_eligible=True,
    )
    classified_flags = formal_runner._evidence_flags(
        paper_eligible=True,
        execution_classification=classification,
    )

    assert "attempt_evidence_incomplete" not in reasons
    assert classified_attempt["paper_eligible"] is False
    assert classified_attempt["paper_ineligibility_reasons"] == []
    assert classified_flags["paper_eligible"] is False
    assert classified_flags["ineligibility_reasons"] == [
        "smoke_suite",
        "pilot_only",
    ]


@pytest.mark.parametrize(
    ("condition_status", "expected_suite_status"),
    (
        ("completed", "completed"),
        ("completed_with_failures", "completed_with_failures"),
        ("blocked", "blocked"),
        ("failed", "failed"),
        ("budget_exhausted", "budget_exhausted"),
        ("incomplete", "incomplete"),
    ),
)
def test_recomputed_suite_status_matches_live_terminal_semantics(
    condition_status: str,
    expected_suite_status: str,
) -> None:
    assert formal_runner._recomputed_suite_status(
        condition_statuses=[condition_status],
        has_plans=True,
        any_blocked_plan=False,
    ) == expected_suite_status


def test_protocol_event_projection_does_not_mutate_hashed_ledger_body(
    tmp_path: Path,
) -> None:
    ledger = EventLedger(tmp_path / "source-events.jsonl")
    event = ledger.append(
        event_type="TASK_REGISTERED",
        object_type="Task",
        object_id="paper_factorization_case-1",
        payload={"root_task_id": "paper_factorization_case-1"},
        idempotency_key="register-paper-factorization-case-1",
        task_id="paper_factorization_case-1",
        occurred_at="2026-07-31T00:00:00Z",
    ).to_dict()
    condition = SimpleNamespace(
        experiment_id=EXPERIMENT_ID,
        condition_id="condition-1",
        repeat_id=0,
    )

    projected = formal_runner._event_record_with_context(
        event,
        condition=condition,
        task_id="case-1",
    )

    assert projected == event


def test_provider_latency_observation_is_complete_or_null() -> None:
    incomplete = formal_runner._provider_latency_observation(
        attempts=(
            SimpleNamespace(provider_attempt_count=1, latency_ms=25),
            SimpleNamespace(provider_attempt_count=1, latency_ms=None),
        ),
        expected_provider_attempt_count=2,
    )
    zero_call = formal_runner._provider_latency_observation(
        attempts=(
            SimpleNamespace(provider_attempt_count=0, latency_ms=0),
        ),
        expected_provider_attempt_count=0,
    )

    assert incomplete == (
        None,
        "incomplete",
        "missing_provider_latency_evidence",
    )
    assert zero_call == (0.0, "not_applicable", "no_provider_attempts")


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
    monkeypatch.setattr(
        formal_runner.shutil,
        "copytree",
        lambda *_args, **_kwargs: pytest.fail(
            "formal execution must not publish a compatibility copy"
        ),
    )
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
    assert not (tmp_path / EXPERIMENT_ID / "experiment_manifest.json").exists()

    adapter_root = (
        tmp_path / EXPERIMENT_ID / "runs" / "condition-1" / "case-1"
    )
    assert not adapter_root.exists()
    run_root = (
        tmp_path
        / "experiments"
        / EXPERIMENT_ID
        / "runs"
        / "condition-1"
        / "0"
    )
    assert (run_root / "CURRENT.json").is_file()
    assert len(list((run_root / "artifacts" / "case-1").glob("*-raw-output.txt"))) == 1
    assert len(list((run_root / "artifacts" / "case-1").glob("*-request.json"))) == 1
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

    evidence_store = formal_runner.FormalEvidenceStore(tmp_path)
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

    replay_report_ref = formal_runner.write_paper_formal_replay_report(
        output_root=tmp_path
    )
    replay_report_path = tmp_path / "audit" / "replay_report.json"
    replay_report = json.loads(replay_report_path.read_text(encoding="utf-8"))
    assert replay_report_ref["path"] == "audit/replay_report.json"
    assert replay_report_ref["content_hash"] == (
        "sha256:" + hashlib.sha256(replay_report_path.read_bytes()).hexdigest()
    )
    assert replay_report["schema_version"] == "tokenshare.paper_replay_report.v1"
    assert replay_report["status"] == "replay_verified"
    assert replay_report["provider_calls_made"] == 0
    assert replay_report["replayed_result"] == executed.to_dict()
    assert replay_report["replayed_result_digest"].startswith("sha256:")
    assert replay_report["persisted_result_ref"]["path"] == (
        "formal_runner_result.json"
    )
    assert replay_report["persisted_result_ref"]["content_hash"].startswith("sha256:")
    assert replay_report["comparison"]["status"] == "matched"
    assert replay_report["comparison"]["mismatched_fields"] == []
    recomputed = replay_report["independently_recomputed_summary"]
    assert recomputed["status"] == "completed"
    assert recomputed["condition_count"] == 1
    assert recomputed["run_count"] == 1
    assert recomputed["task_count"] == 1
    assert recomputed["provider_attempt_count"] == 1
    assert recomputed["total_tokens"] == 11
    assert recomputed["total_cost_estimate"] == pytest.approx(0.125)
    assert recomputed["paper_eligible"] is False
    assert recomputed["error_summary"] == []
    assert replay_report["independently_recomputed_summary_digest"].startswith(
        "sha256:"
    )
    assert replay_report["source_refs"]["formal_runner_result"]["path"] == (
        "formal_runner_result.json"
    )
    assert replay_report["source_refs"]["evidence_manifest"]["path"] == (
        "evidence_manifest.json"
    )
    formal_runner.FormalEvidenceStore(tmp_path)._validate_evidence_manifest()
    first_report_bytes = replay_report_path.read_bytes()
    formal_runner.FormalEvidenceStore(tmp_path)._refresh_evidence_manifest()
    repeated_ref = formal_runner.write_paper_formal_replay_report(
        output_root=tmp_path
    )
    assert replay_report_path.read_bytes() == first_report_bytes
    assert repeated_ref == replay_report_ref


def test_formal_runner_preserves_adapter_tree_when_checkpoint_fails(
    tmp_path: Path,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)
    condition, _selection = plan.bound_items()[0]
    adapter_root = (
        Path(plan.output_root)
        / "runs"
        / condition.condition_id
        / "case-1"
    )
    adapter_result = _complete_adapter_result(
        output_root=adapter_root,
        condition=condition,
        case_id="case-1",
    )

    class FailingEvidenceStore:
        output_root = tmp_path

        def checkpoint_root(self, **_kwargs):
            raise RuntimeError("checkpoint publication failed")

    budget = _budget(
        planned_conditions=1,
        planned_root_runs=1,
        planned_ai_units=1,
    )
    callback = formal_runner._FormalConditionExecutionCallback(
        catalog_manifest={},
        config=config,
        transport=object(),
        real_transport=False,
        output_root=Path(plan.output_root),
        request_limits=config.defaults,
        evidence_store=FailingEvidenceStore(),
        completed_task_keys=set(),
        usage=SimpleNamespace(),
        budget=budget,
        rolling_disk_forecast=_rolling_tracker(budget),
        hard_limits={},
        root_case_ids=None,
        execution_classification=None,
    )

    with pytest.raises(RuntimeError, match="checkpoint publication failed"):
        callback._checkpoint_adapter_result(
            condition=condition,
            task_id="case-1",
            task=adapter_result.task_result,
            adapter_result=adapter_result,
            adapter_root=adapter_root,
        )

    assert adapter_root.is_dir()
    assert any(path.is_file() for path in adapter_root.rglob("*"))
    assert not (
        tmp_path
        / "experiments"
        / EXPERIMENT_ID
        / "runs"
        / "condition-1"
        / "0"
        / "CURRENT.json"
    ).exists()


@pytest.mark.parametrize("terminal_kind", ("exception", "budget"))
def test_terminal_checkpoint_removes_existing_exact_adapter_case(
    tmp_path: Path,
    terminal_kind: str,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)
    condition, _selection = plan.bound_items()[0]
    task_id = "case-1"
    adapter_root = (
        Path(plan.output_root)
        / "runs"
        / condition.condition_id
        / task_id
    )
    adapter_root.mkdir(parents=True)
    (adapter_root / "working.txt").write_text("working", encoding="utf-8")
    canonical_run_root = (
        tmp_path
        / "experiments"
        / condition.experiment_id
        / "runs"
        / condition.condition_id
        / str(condition.repeat_id)
    )
    canonical_run_root.mkdir(parents=True)
    (canonical_run_root / "CURRENT.json").write_text(
        json.dumps(
            {
                "schema_version": "tokenshare.paper_checkpoint_current.v1",
                "generation_id": "generation-1",
                "generation_manifest_digest": "sha256:" + "1" * 64,
            }
        ),
        encoding="utf-8",
    )

    class CommitEvidenceStore:
        output_root = tmp_path

        def checkpoint_root(self, **_kwargs):
            return {
                "schema_version": "tokenshare.paper_checkpoint_commit.v1",
                "experiment_id": condition.experiment_id,
                "condition_id": condition.condition_id,
                "repeat_id": str(condition.repeat_id),
                "task_id": task_id,
                "run_root": (
                    Path("experiments")
                    / condition.experiment_id
                    / "runs"
                    / condition.condition_id
                    / str(condition.repeat_id)
                ).as_posix(),
                "generation_id": "generation-1",
                "generation_manifest_digest": "sha256:" + "1" * 64,
            }

    budget = _budget(
        planned_conditions=1,
        planned_root_runs=1,
        planned_ai_units=1,
    )
    callback = formal_runner._FormalConditionExecutionCallback(
        catalog_manifest={},
        config=config,
        transport=object(),
        real_transport=False,
        output_root=Path(plan.output_root),
        request_limits=config.defaults,
        evidence_store=CommitEvidenceStore(),
        completed_task_keys=set(),
        usage=formal_runner._UsageTotals(),
        budget=budget,
        rolling_disk_forecast=_rolling_tracker(budget),
        hard_limits={},
        root_case_ids=None,
        execution_classification=None,
    )

    if terminal_kind == "exception":
        callback._checkpoint_exception(
            condition=condition,
            task_id=task_id,
            error=RuntimeError("experiment failure"),
        )
    else:
        callback._checkpoint_budget_exhausted(
            condition=condition,
            task_id=task_id,
        )

    assert not adapter_root.exists()


def test_formal_adapter_cleanup_rejects_non_exact_target(
    tmp_path: Path,
) -> None:
    plan_root = tmp_path / EXPERIMENT_ID
    outside = tmp_path / "outside" / "case-1"
    outside.mkdir(parents=True)
    marker = outside / "preserve.txt"
    marker.write_text("preserve", encoding="utf-8")

    with pytest.raises(ValueError, match="exact adapter case root"):
        formal_runner._verified_adapter_case_root(
            plan_root=plan_root,
            condition_id="condition-1",
            task_id="case-1",
            adapter_root=outside,
        )

    assert marker.read_text(encoding="utf-8") == "preserve"


def test_resume_cleanup_deletes_only_checkpointed_adapter_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _two_case_dispatch_plan(tmp_path, config=config)
    condition, selection = plan.bound_items()[0]
    checkpointed_case, uncheckpointed_case = selection.ordered_case_ids

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
    kwargs = {
        **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
        "budget": _budget(
            planned_conditions=1,
            planned_root_runs=1,
            planned_ai_units=1,
        ),
        "root_case_filter": {
            condition.condition_id: (checkpointed_case,),
        },
    }
    formal_runner.execute_paper_formal_suite(**kwargs)
    plan_root = Path(plan.output_root)
    checkpointed_root = (
        plan_root / "runs" / condition.condition_id / checkpointed_case
    )
    uncheckpointed_root = (
        plan_root / "runs" / condition.condition_id / uncheckpointed_case
    )
    for working_root in (checkpointed_root, uncheckpointed_root):
        working_root.mkdir(parents=True, exist_ok=True)
        (working_root / "working.txt").write_text("working", encoding="utf-8")
    formal_runner.FormalEvidenceStore(tmp_path)._refresh_evidence_manifest()
    order: list[str] = []
    original_load = formal_runner.FormalEvidenceStore.load.__func__
    original_cleanup = formal_runner._cleanup_checkpointed_adapter_trees

    def traced_load(cls, **load_kwargs):
        order.append("load")
        return original_load(cls, **load_kwargs)

    def traced_cleanup(**cleanup_kwargs):
        assert order == ["load"]
        order.append("cleanup")
        return original_cleanup(**cleanup_kwargs)

    monkeypatch.setattr(
        formal_runner.FormalEvidenceStore,
        "load",
        classmethod(traced_load),
    )
    monkeypatch.setattr(
        formal_runner,
        "_cleanup_checkpointed_adapter_trees",
        traced_cleanup,
    )
    monkeypatch.setattr(
        formal_runner.FormalEvidenceStore,
        "archive_uncheckpointed_adapter_runs",
        lambda self: pytest.fail("resume must not archive before full load"),
    )

    formal_runner.execute_paper_formal_suite(**kwargs, resume=True)

    assert order == ["load", "cleanup"]
    assert not checkpointed_root.exists()
    assert (uncheckpointed_root / "working.txt").is_file()


def test_resume_cleanup_uses_full_load_terminal_keys_without_per_root_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)
    condition, selection = plan.bound_items()[0]
    task_ids = tuple(f"case-{index:03d}" for index in range(500))
    selection = replace(
        selection,
        ordered_case_ids=task_ids,
        expected_ai_unit_count=len(task_ids),
    )
    plan = replace(
        plan,
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(
                condition,
                selection,
            ),
        ),
    )
    plan_root = Path(plan.output_root)
    for task_id in task_ids:
        working_root = plan_root / "runs" / condition.condition_id / task_id
        working_root.mkdir(parents=True)
        (working_root / "working.txt").write_text("working", encoding="utf-8")
    terminal_ids = set(task_ids[:250])
    terminal_keys = {
        formal_runner._formal_task_key(condition, task_id)
        for task_id in terminal_ids
    }
    monkeypatch.setattr(
        formal_runner.FormalEvidenceStore,
        "_validate_run",
        lambda *_args, **_kwargs: pytest.fail(
            "batch cleanup must consume the full-load terminal key set"
        ),
    )

    removed = formal_runner._cleanup_checkpointed_adapter_trees(
        suite_root=tmp_path,
        bound_plans=((plan, plan.bound_items()),),
        normalized_root_filter={},
        terminal_task_keys=terminal_keys,
    )

    assert removed == 250
    assert all(
        not (
            plan_root / "runs" / condition.condition_id / task_id
        ).exists()
        for task_id in terminal_ids
    )
    assert all(
        (
            plan_root / "runs" / condition.condition_id / task_id
        ).is_dir()
        for task_id in task_ids[250:]
    )


@pytest.mark.parametrize(
    ("field_name", "mutated_value"),
    (
        ("status", "failed"),
        ("condition_count", 7),
        ("run_count", 7),
        ("task_count", 7),
        ("provider_attempt_count", 7),
        ("total_tokens", 777),
        ("total_cost_estimate", 777.0),
        ("paper_eligible", True),
        (
            "error_summary",
            [
                {
                    "failure_stage": "tampered_summary",
                    "failure_kind": "not_canonical_evidence",
                }
            ],
        ),
    ),
)
def test_formal_replay_report_rejects_runner_summary_drift_after_manifest_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field_name: str,
    mutated_value: object,
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

    def fake_case_dispatch(**kwargs):
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
    formal_runner.execute_paper_formal_suite(
        **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan)
    )

    result_path = tmp_path / "formal_runner_result.json"
    persisted = json.loads(result_path.read_text(encoding="utf-8"))
    persisted[field_name] = mutated_value
    result_path.write_text(
        json.dumps(persisted, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    formal_runner.FormalEvidenceStore(tmp_path)._refresh_evidence_manifest()
    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_case",
        lambda **_kwargs: pytest.fail("replay must not call provider dispatch"),
    )

    with pytest.raises(
        ValueError,
        match="formal runner result does not match independently recomputed evidence",
    ):
        formal_runner.write_paper_formal_replay_report(output_root=tmp_path)


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


def test_completed_experiment_is_committed_before_later_experiment_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    exp1_plan = _plan_for_experiment(
        tmp_path=tmp_path,
        config=config,
        experiment_id=EXPERIMENT_ID,
        condition_id="condition-exp1-commit",
    )
    exp2_plan = _plan_for_experiment(
        tmp_path=tmp_path,
        config=config,
        experiment_id=formal_runner.EXP2_EXPERIMENT_ID,
        condition_id="condition-exp2-crash",
    )
    committed_result = PaperConditionResult(
        condition_id="condition-exp1-commit",
        status=PaperStatus.COMPLETED,
        repeat_count=1,
        task_count=1,
        completed_root_count=1,
        failed_root_count=0,
        blocked_root_count=0,
        provider_attempt_count=1,
        metrics_ref={
            "paper_eligible": True,
            "evidence_refs": [{"path": "committed-exp1"}],
        },
    )

    def interrupted_dispatch(*, context, plan, condition_id):
        del context
        if plan.experiment_id == formal_runner.EXP2_EXPERIMENT_ID:
            raise RuntimeError("later experiment crashed")
        assert condition_id == committed_result.condition_id
        return committed_result

    def hard_stop_before_suite_finalizer(**_kwargs):
        raise RuntimeError("process stopped before suite finalizer")

    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_condition",
        interrupted_dispatch,
    )
    monkeypatch.setattr(
        formal_runner,
        "_close_blocked_formal_suite",
        hard_stop_before_suite_finalizer,
    )
    with pytest.raises(RuntimeError, match="process stopped"):
        formal_runner.execute_paper_formal_suite(
            dispatch_plans=(exp1_plan, exp2_plan),
            catalog_manifest={
                "catalog_digest": CATALOG_DIGEST,
                "factorization_cases": (
                    {"case_id": "case-1", "expected_ai_unit_count": 1},
                ),
                "lean_cases": (),
                "lean_lemma_graph_cases": (),
            },
            budget=_budget(
                planned_experiments=(
                    EXPERIMENT_ID,
                    formal_runner.EXP2_EXPERIMENT_ID,
                ),
                planned_conditions=2,
                planned_root_runs=2,
                planned_ai_units=2,
            ),
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

    exp1_manifest = json.loads(
        (
            tmp_path
            / "experiments"
            / EXPERIMENT_ID
            / "experiment_manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert exp1_manifest["status"] == "completed"
    rows = [
        json.loads(line)
        for line in (tmp_path / "condition_results.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert {
        (row["experiment_id"], row["condition_id"], row["repeat_id"])
        for row in rows
    } == {(EXPERIMENT_ID, "condition-exp1-commit", 0)}
    suite_manifest = json.loads(
        (tmp_path / "suite_manifest.json").read_text(encoding="utf-8")
    )
    assert suite_manifest["status"] == "running"
    assert suite_manifest["paper_eligible"] is False
    rows_path = tmp_path / "condition_results.jsonl"
    manifest_path = (
        tmp_path
        / "experiments"
        / EXPERIMENT_ID
        / "experiment_manifest.json"
    )
    before = (rows_path.read_bytes(), manifest_path.read_bytes())

    formal_runner._finalize_formal_experiment(
        suite_root=tmp_path,
        plan=exp1_plan,
        condition_results=(committed_result,),
        execution_classification=None,
    )

    assert (rows_path.read_bytes(), manifest_path.read_bytes()) == before


def test_resume_rebuilds_missing_experiment_and_suite_closure_from_checkpoints(
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
        case_id = kwargs["case"]["case_id"]
        adapter_calls.append(case_id)
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=case_id,
        )

    original_finalizer = formal_runner._finalize_formal_experiment

    def hard_stop_after_checkpoint(**_kwargs):
        raise RuntimeError("process stopped after canonical checkpoint")

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", fake_case_dispatch)
    monkeypatch.setattr(
        formal_runner,
        "_finalize_formal_experiment",
        hard_stop_after_checkpoint,
    )
    monkeypatch.setattr(
        formal_runner,
        "_close_blocked_formal_suite",
        hard_stop_after_checkpoint,
    )
    kwargs = _formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan)

    with pytest.raises(RuntimeError, match="canonical checkpoint"):
        formal_runner.execute_paper_formal_suite(**kwargs)

    assert adapter_calls == ["case-1"]
    assert not (tmp_path / "formal_runner_result.json").exists()
    monkeypatch.setattr(
        formal_runner,
        "_finalize_formal_experiment",
        original_finalizer,
    )

    resumed = formal_runner.execute_paper_formal_suite(**kwargs, resume=True)

    assert resumed.status == PaperStatus.COMPLETED
    assert resumed.provider_attempt_count == 1
    assert resumed.total_tokens == 11
    assert resumed.total_cost_estimate == pytest.approx(0.125)
    assert adapter_calls == ["case-1"]
    experiment_manifest = json.loads(
        (
            tmp_path
            / "experiments"
            / EXPERIMENT_ID
            / "experiment_manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert experiment_manifest["status"] == "completed"
    assert (tmp_path / "formal_runner_result.json").is_file()


@pytest.mark.parametrize(
    "crash_stage",
    (
        "condition_results_written",
        "experiment_manifest_written",
        "inventory_refreshed",
    ),
)
def test_resume_repairs_experiment_finalization_intent_at_each_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_stage: str,
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
        case_id = kwargs["case"]["case_id"]
        adapter_calls.append(case_id)
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=case_id,
        )

    original_hook = formal_runner._formal_finalization_hook

    def crash_hook(*, stage, suite_root):
        del suite_root
        if stage == crash_stage:
            raise RuntimeError(f"crash at {stage}")

    def preserve_crash_seam(**_kwargs):
        raise RuntimeError("process stopped before blocked-suite closure")

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", fake_case_dispatch)
    monkeypatch.setattr(formal_runner, "_formal_finalization_hook", crash_hook)
    monkeypatch.setattr(
        formal_runner,
        "_close_blocked_formal_suite",
        preserve_crash_seam,
    )
    kwargs = _formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan)

    with pytest.raises(RuntimeError, match="blocked-suite closure"):
        formal_runner.execute_paper_formal_suite(**kwargs)

    assert adapter_calls == ["case-1"]
    assert (tmp_path / "PENDING.json").is_file()
    monkeypatch.setattr(formal_runner, "_formal_finalization_hook", original_hook)

    resumed = formal_runner.execute_paper_formal_suite(**kwargs, resume=True)

    assert resumed.status == PaperStatus.COMPLETED
    assert resumed.provider_attempt_count == 1
    assert resumed.total_tokens == 11
    assert resumed.total_cost_estimate == pytest.approx(0.125)
    assert adapter_calls == ["case-1"]
    assert not (tmp_path / "PENDING.json").exists()
    formal_runner.FormalEvidenceStore(tmp_path)._validate_evidence_manifest()


@pytest.mark.parametrize(
    "crash_stage",
    (
        "formal_runner_result_written",
        "suite_manifest_written",
        "suite_inventory_refreshed",
    ),
)
def test_resume_repairs_suite_finalization_intent_at_each_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_stage: str,
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
        case_id = kwargs["case"]["case_id"]
        adapter_calls.append(case_id)
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=case_id,
        )

    original_hook = formal_runner._formal_finalization_hook

    def crash_hook(*, stage, suite_root):
        del suite_root
        if stage == crash_stage:
            raise RuntimeError(f"crash at {stage}")

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", fake_case_dispatch)
    monkeypatch.setattr(formal_runner, "_formal_finalization_hook", crash_hook)
    kwargs = _formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan)

    with pytest.raises(RuntimeError, match=crash_stage):
        formal_runner.execute_paper_formal_suite(**kwargs)

    assert adapter_calls == ["case-1"]
    assert (tmp_path / "PENDING.json").is_file()
    monkeypatch.setattr(formal_runner, "_formal_finalization_hook", original_hook)

    resumed = formal_runner.execute_paper_formal_suite(**kwargs, resume=True)

    assert resumed.status == PaperStatus.COMPLETED
    assert resumed.provider_attempt_count == 1
    assert resumed.total_tokens == 11
    assert resumed.total_cost_estimate == pytest.approx(0.125)
    assert adapter_calls == ["case-1"]
    assert not (tmp_path / "PENDING.json").exists()
    formal_runner.FormalEvidenceStore(tmp_path)._validate_evidence_manifest()


def test_resume_rebuilds_failed_terminal_root_without_provider_retry(
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

    def rejected_case_dispatch(**kwargs):
        case_id = kwargs["case"]["case_id"]
        adapter_calls.append(case_id)
        result = _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=case_id,
        )
        return SimpleNamespace(
            **{
                **vars(result),
                "task_result": SimpleNamespace(
                    **{
                        **vars(result.task_result),
                        "root_status": PaperTaskStatus.FAILED,
                    }
                ),
                "attempt_results": [
                    replace(
                        result.attempt_results[0],
                        attempt_status=PaperAttemptStatus.VERIFICATION_REJECTED,
                        error_kind="verifier_rejected",
                    )
                ],
            }
        )

    original_hook = formal_runner._formal_finalization_hook

    def crash_after_failed_condition_row(*, stage, suite_root):
        del suite_root
        if stage == "condition_results_written":
            raise RuntimeError("crash after failed terminal checkpoint")

    def preserve_crash_seam(**_kwargs):
        raise RuntimeError("process stopped before failure closure")

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, CallbackModule()),),
    )
    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_case",
        rejected_case_dispatch,
    )
    monkeypatch.setattr(
        formal_runner,
        "_formal_finalization_hook",
        crash_after_failed_condition_row,
    )
    monkeypatch.setattr(
        formal_runner,
        "_close_blocked_formal_suite",
        preserve_crash_seam,
    )
    kwargs = _formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan)

    with pytest.raises(RuntimeError, match="failure closure"):
        formal_runner.execute_paper_formal_suite(**kwargs)

    assert adapter_calls == ["case-1"]
    monkeypatch.setattr(formal_runner, "_formal_finalization_hook", original_hook)

    resumed = formal_runner.execute_paper_formal_suite(**kwargs, resume=True)

    assert resumed.status == PaperStatus.COMPLETED_WITH_FAILURES
    assert resumed.provider_attempt_count == 1
    assert resumed.total_tokens == 11
    assert resumed.total_cost_estimate == pytest.approx(0.125)
    assert adapter_calls == ["case-1"]


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


def test_formal_runner_materializer_rejects_truncated_source_bytes(
    tmp_path: Path,
) -> None:
    adapter_root = tmp_path / "adapter"
    source = adapter_root / "artifacts" / "raw-output.json"
    source.parent.mkdir(parents=True)
    source.write_bytes(b'{"truncated":')

    with pytest.raises(ValueError, match="hash mismatched"):
        formal_runner._materialize_artifacts(
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
                    "artifact_id": "raw-output.json",
                    "uri": "artifacts/raw-output.json",
                    "content_hash": formal_runner._sha256_bytes(
                        b'{"complete":true}'
                    ),
                },
            ),
        )


def test_adapter_artifact_refs_discovers_event_only_nested_ref() -> None:
    event_only_ref = {
        "schema_version": "ArtifactRef.v1",
        "artifact_id": "event-only",
        "artifact_type": "EventOnlyEvidence",
        "uri": "artifacts/event-only.json",
        "content_hash": "sha256:" + "a" * 64,
        "size_bytes": 2,
        "media_type": "application/json",
        "artifact_schema_id": "test.event_only",
        "artifact_schema_version": "v1",
        "source": {},
        "metadata": {},
        "created_at": "2026-07-20T00:00:00Z",
    }

    refs = formal_runner._adapter_artifact_refs(
        {},
        (),
        (),
        events=({"payload": {"deeply_nested": [event_only_ref]}},),
    )

    assert refs == (event_only_ref,)


def test_materialize_artifacts_recursively_closes_metadata_and_json_payload_refs(
    tmp_path: Path,
) -> None:
    adapter_root = tmp_path / "adapter"

    def write_ref(
        relative_path: str,
        *,
        artifact_id: str,
        body: bytes,
        source: dict[str, object] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        path = adapter_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return {
            "schema_version": "ArtifactRef.v1",
            "artifact_id": artifact_id,
            "artifact_type": "RecursiveEvidence",
            "uri": relative_path,
            "content_hash": formal_runner._sha256_bytes(body),
            "size_bytes": len(body),
            "media_type": "application/json",
            "artifact_schema_id": "test.recursive_evidence",
            "artifact_schema_version": "v1",
            "source": source or {},
            "metadata": metadata or {},
            "created_at": "2026-07-20T00:00:00Z",
        }

    metadata_ref = write_ref(
        "one/artifacts/result.json",
        artifact_id="metadata-child",
        body=b'{"source":"metadata"}',
    )
    payload_ref = write_ref(
        "two/artifacts/result.json",
        artifact_id="payload-child",
        body=b'{"source":"payload"}',
    )
    root_body = json.dumps(
        {"payload_ref": payload_ref},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    root_ref = write_ref(
        "root/artifacts/root.json",
        artifact_id="root",
        body=root_body,
        metadata={"metadata_ref": metadata_ref},
    )

    refs = formal_runner._materialize_artifacts(
        suite_root=tmp_path / "suite",
        condition=SimpleNamespace(
            experiment_id=EXPERIMENT_ID,
            condition_id="condition-1",
            repeat_id=0,
        ),
        task_id="case-1",
        adapter_root=adapter_root,
        source_refs=(root_ref,),
    )

    assert {ref["artifact_id"] for ref in refs} == {
        "root",
        "metadata-child",
        "payload-child",
    }
    assert len({ref["path"] for ref in refs}) == 3
    child_paths = {
        Path(ref["path"]).name
        for ref in refs
        if ref["artifact_id"] in {"metadata-child", "payload-child"}
    }
    assert len(child_paths) == 2
    for ref in refs:
        assert ref["source_artifact_ref"]["artifact_id"] == ref["artifact_id"]
        assert ref["source_uri"] == ref["source_artifact_ref"]["uri"]
        copied = tmp_path / "suite" / ref["path"]
        assert copied.read_bytes()


def test_materialize_artifacts_rejects_size_mismatch(tmp_path: Path) -> None:
    adapter_root = tmp_path / "adapter"
    source = adapter_root / "artifacts" / "size.json"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"{}")

    with pytest.raises(ValueError, match="size mismatched"):
        formal_runner._materialize_artifacts(
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
                    "schema_version": "ArtifactRef.v1",
                    "artifact_id": "size",
                    "artifact_type": "SizeEvidence",
                    "uri": "artifacts/size.json",
                    "content_hash": formal_runner._sha256_bytes(b"{}"),
                    "size_bytes": 3,
                    "media_type": "application/json",
                    "artifact_schema_id": "test.size_evidence",
                    "artifact_schema_version": "v1",
                    "source": {},
                    "metadata": {},
                    "created_at": "2026-07-20T00:00:00Z",
                },
            ),
        )


def test_materialize_artifacts_rejects_complete_ref_missing_size(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="size_bytes"):
        formal_runner._materialize_artifacts(
            suite_root=tmp_path / "suite",
            condition=SimpleNamespace(
                experiment_id=EXPERIMENT_ID,
                condition_id="condition-1",
                repeat_id=0,
            ),
            task_id="case-1",
            adapter_root=tmp_path / "adapter",
            source_refs=(
                {
                    "schema_version": "ArtifactRef.v1",
                    "artifact_id": "missing-size",
                    "artifact_type": "MalformedEvidence",
                    "uri": "artifacts/missing-size.json",
                    "content_hash": "sha256:" + "a" * 64,
                    "media_type": "application/json",
                    "artifact_schema_id": "test.malformed_evidence",
                    "artifact_schema_version": "v1",
                    "source": {},
                    "metadata": {},
                    "created_at": "2026-07-20T00:00:00Z",
                },
            ),
        )


def test_materialize_artifacts_rejects_invalid_declared_json(
    tmp_path: Path,
) -> None:
    adapter_root = tmp_path / "adapter"
    source = adapter_root / "artifacts" / "bad.json"
    source.parent.mkdir(parents=True)
    source.write_bytes(b'{"truncated":')
    source_ref = {
        "schema_version": "ArtifactRef.v1",
        "artifact_id": "bad-json",
        "artifact_type": "MalformedJsonEvidence",
        "uri": "artifacts/bad.json",
        "content_hash": formal_runner._sha256_bytes(source.read_bytes()),
        "size_bytes": len(source.read_bytes()),
        "media_type": "application/json",
        "artifact_schema_id": "test.malformed_json_evidence",
        "artifact_schema_version": "v1",
        "source": {},
        "metadata": {},
        "created_at": "2026-07-20T00:00:00Z",
    }

    with pytest.raises(ValueError, match="declared JSON"):
        formal_runner._materialize_artifacts(
            suite_root=tmp_path / "suite",
            condition=SimpleNamespace(
                experiment_id=EXPERIMENT_ID,
                condition_id="condition-1",
                repeat_id=0,
            ),
            task_id="case-1",
            adapter_root=adapter_root,
            source_refs=(source_ref,),
        )


def test_materialize_artifacts_deduplicates_same_identity_uri_alias(
    tmp_path: Path,
) -> None:
    adapter_root = tmp_path / "adapter"
    body = b'{"same":true}'
    refs = []
    for directory in ("one", "two"):
        relative_path = f"{directory}/artifacts/alias.json"
        source = adapter_root / relative_path
        source.parent.mkdir(parents=True)
        source.write_bytes(body)
        refs.append(
            {
                "schema_version": "ArtifactRef.v1",
                "artifact_id": "stable-identity",
                "artifact_type": "AliasEvidence",
                "uri": relative_path,
                "content_hash": formal_runner._sha256_bytes(body),
                "size_bytes": len(body),
                "media_type": "application/json",
                "artifact_schema_id": "test.alias_evidence",
                "artifact_schema_version": "v1",
                "source": {},
                "metadata": {},
                "created_at": "2026-07-20T00:00:00Z",
            }
        )

    materialized = formal_runner._materialize_artifacts(
        suite_root=tmp_path / "suite",
        condition=SimpleNamespace(
            experiment_id=EXPERIMENT_ID,
            condition_id="condition-1",
            repeat_id=0,
        ),
        task_id="case-1",
        adapter_root=adapter_root,
        source_refs=refs,
    )

    assert len(materialized) == 1
    assert materialized[0]["artifact_id"] == "stable-identity"


def test_formal_checkpoint_materializes_nested_no_return_artifact_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)
    condition = plan.conditions[0]
    expected_ids = {
        "no-return-request",
        "no-return-raw",
        "no-return-provenance",
        "no-return-usage",
        "no-return-model-record",
        "no-return-primitive-fault",
        "no-return-runtime-fault",
    }

    class CheckpointModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen dispatch plan")

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

    def nested_no_return_dispatch(**kwargs):
        adapter_root = Path(kwargs["output_root"])
        case_id = kwargs["case"]["case_id"]
        base = _complete_adapter_result(
            output_root=adapter_root,
            condition=kwargs["condition"],
            case_id=case_id,
        )
        store = ArtifactStore(adapter_root / "nested" / "runtime")
        created_at = "2026-07-20T00:00:00Z"

        def save_json(artifact_id: str, artifact_type: str, body: dict[str, object]):
            assert artifact_id in expected_ids
            return store.save_json(
                body,
                artifact_id=artifact_id,
                artifact_type=artifact_type,
                artifact_schema_id=f"test.{artifact_type.lower()}",
                artifact_schema_version="v1",
                source={"test": "formal-no-return-materialization"},
                metadata={},
                created_at=created_at,
            )

        request_ref = save_json(
            "no-return-request",
            "ExecutionRequest",
            {"request_id": "request-case-1", "attempt_id": "attempt-case-1"},
        )
        raw_ref = save_json(
            "no-return-raw",
            "RawModelOutput",
            {"content_text": "{}"},
        )
        provenance_ref = save_json(
            "no-return-provenance",
            "AIProviderResponseProvenance",
            {
                "request_id": "request-case-1",
                "raw_output_ref": raw_ref.to_dict(),
            },
        )
        usage_ref = save_json(
            "no-return-usage",
            "AIUsageSummary",
            {"request_id": "request-case-1", "total_tokens": 11},
        )
        model_ref = save_json(
            "no-return-model-record",
            "PaperModelExecutionRecord",
            {
                "attempt_id": "attempt-case-1",
                "request_ref": request_ref.to_dict(),
                "raw_output_ref": raw_ref.to_dict(),
                "provenance_ref": provenance_ref.to_dict(),
                "usage_ref": usage_ref.to_dict(),
            },
        )
        primitive_fault_ref = save_json(
            "no-return-primitive-fault",
            "FaultInjectionRecord",
            {
                "attempt_id": "attempt-case-1",
                "fault_type": "no_return",
            },
        )
        runtime_fault_ref = save_json(
            "no-return-runtime-fault",
            "RuntimeFaultInjectionRecord",
            {
                "attempt_id": "attempt-case-1",
                "fault_type": "no_return",
                "primitive_fault_record_ref": primitive_fault_ref.to_dict(),
                "original_raw_output_ref": raw_ref.to_dict(),
                "original_provenance_ref": provenance_ref.to_dict(),
                "pre_fault_usage_ref": usage_ref.to_dict(),
            },
        )
        attempt = SimpleNamespace(
            **{
                **vars(base.attempt_results[0]),
                "attempt_status": PaperAttemptStatus.LEASE_EXPIRED,
                "request_ref": request_ref.to_dict(),
                "raw_output_ref": raw_ref.to_dict(),
                "provenance_ref": provenance_ref.to_dict(),
                "usage_ref": usage_ref.to_dict(),
                "model_execution_record_ref": model_ref.to_dict(),
                "fault_injection_ref": primitive_fault_ref.to_dict(),
                "error_kind": "lease_expired",
                "paper_eligible": True,
            }
        )
        task = SimpleNamespace(
            **{
                **vars(base.task_result),
                "artifact_refs": [
                    request_ref.to_dict(),
                    runtime_fault_ref.to_dict(),
                ],
                "paper_eligible": True,
            }
        )
        runtime_fault = {
            "fault_injection_id": "no-return-runtime-fault",
            "record_ref": runtime_fault_ref.to_dict(),
            "primitive_fault_record_ref": primitive_fault_ref.to_dict(),
            "original_raw_output_ref": raw_ref.to_dict(),
            "original_provenance_ref": provenance_ref.to_dict(),
            "pre_fault_usage_ref": usage_ref.to_dict(),
        }
        return SimpleNamespace(
            **{
                **vars(base),
                "task_result": task,
                "attempt_results": [attempt],
                "fault_records": [runtime_fault],
            }
        )

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, CheckpointModule()),),
    )
    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_case",
        nested_no_return_dispatch,
    )

    formal_runner.execute_paper_formal_suite(
        **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan)
    )

    run_root = (
        tmp_path
        / "experiments"
        / EXPERIMENT_ID
        / "runs"
        / condition.condition_id
        / str(condition.repeat_id)
    )
    current = json.loads((run_root / "CURRENT.json").read_text(encoding="utf-8"))
    generation_root = run_root / ".generations" / current["generation_id"]
    artifact_index = _generation_records(
        tmp_path,
        EXPERIMENT_ID,
        condition.condition_id,
        "artifacts/artifact_index.jsonl",
    )
    assert {
        str(ref["artifact_id"]) for ref in artifact_index
    } == expected_ids
    for ref in artifact_index:
        payload = tmp_path / str(ref["path"])
        assert payload.is_file()
        assert formal_runner._sha256_bytes(payload.read_bytes()) == ref["content_hash"]

    attempts = _generation_records(
        tmp_path,
        EXPERIMENT_ID,
        condition.condition_id,
        "per_attempt_results.jsonl",
    )
    assert (
        generation_root / "artifacts" / "artifact_index.jsonl"
    ).is_file()
    for field_name in (
        "request_ref",
        "raw_output_ref",
        "provenance_ref",
        "usage_ref",
        "model_execution_record_ref",
        "fault_injection_ref",
    ):
        assert formal_runner._persisted_artifact_ref_resolves(
            attempts[0][field_name],
            artifact_index,
        )


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


def test_resume_hard_limit_rebuilds_missing_closure_without_provider_retry(
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

    original_finalizer = formal_runner._finalize_formal_experiment

    def hard_stop_after_condition_checkpoint(**_kwargs):
        raise RuntimeError("process stopped before hard-limit closure")

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", fake_case_dispatch)
    monkeypatch.setattr(
        formal_runner,
        "_finalize_formal_experiment",
        hard_stop_after_condition_checkpoint,
    )
    monkeypatch.setattr(
        formal_runner,
        "_close_blocked_formal_suite",
        hard_stop_after_condition_checkpoint,
    )
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

    with pytest.raises(RuntimeError, match="hard-limit closure"):
        formal_runner.execute_paper_formal_suite(**kwargs)

    assert adapter_calls == ["case-1"]
    monkeypatch.setattr(
        formal_runner,
        "_finalize_formal_experiment",
        original_finalizer,
    )

    resumed = formal_runner.execute_paper_formal_suite(**kwargs, resume=True)

    assert resumed.status == PaperStatus.BUDGET_EXHAUSTED
    assert resumed.provider_attempt_count == 1
    assert resumed.total_tokens == 11
    assert resumed.total_cost_estimate == pytest.approx(0.125)
    assert adapter_calls == ["case-1"]
    experiment_manifest = json.loads(
        (
            tmp_path
            / "experiments"
            / EXPERIMENT_ID
            / "experiment_manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert experiment_manifest["status"] == "budget_exhausted"


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
    condition_result = json.loads(
        (tmp_path / "condition_results.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert condition_result["metrics_ref"]["worker_count"] == 2
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
        condition_id=(
            "exp3_worker_death_factorization__test__dead3__p25__rep0"
        ),
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
    assert dispatched_condition_ids == [
        "exp3_worker_death_factorization__test__dead3__p25__rep0"
    ]
    assert termination_policies[0] == WorkerTerminationPolicy(
        target_planned_ai_unit_ids=("range_0", "range_1"),
        termination_count_target=3,
        kill_point="progress_25",
        total_planned_ai_unit_count=20,
        process_timeout_seconds=60.0,
    )
    faults = _generation_records(
        tmp_path,
        experiment_id,
        "exp3_worker_death_factorization__test__dead3__p25__rep0",
        "fault_injections.jsonl",
    )
    task = _generation_records(
        tmp_path,
        experiment_id,
        "exp3_worker_death_factorization__test__dead3__p25__rep0",
        "per_task_results.jsonl",
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
        condition_id=(
            "exp3_worker_death_factorization__failed_shared__dead1__p50__rep0"
        ),
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
    assert provider_dispatches == [
        "exp3_worker_death_factorization__failed_shared__dead1__p50__rep0"
    ]
    task = _generation_records(
        tmp_path,
        experiment_id,
        "exp3_worker_death_factorization__failed_shared__dead1__p50__rep0",
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
        condition_id=(
            "exp3_worker_death_factorization__failed__dead1__p50__rep0"
        ),
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
        if condition.condition_id == (
            "exp3_worker_death_factorization__failed__dead1__p50__rep0"
        ):
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
        from tokenshare.experiments.paper_metric_contract import (
            load_paper_metric_contract,
        )
        from tokenshare.experiments.paper_metric_registry import (
            load_paper_metric_registry,
        )

        metric_contract = load_paper_metric_contract()
        metric_registry = load_paper_metric_registry(metric_contract)
        metrics = recompute_paper_formal_metrics(
            tmp_path,
            {key: () for key in metric_registry.required_input_keys},
            global_infrastructure_valid=False,
            registry=metric_registry,
            contract=metric_contract,
        )
        # 旧 report 壳尚未迁移到 Task 18 draft shape；本回归只验证失败封口。
        object.__setattr__(metrics, "condition_rows", ())
        object.__setattr__(metrics, "experiment_rows", {})
        object.__setattr__(metrics, "capturing", False)
        object.__setattr__(metrics, "exp5_artifact_rows", None)
        object.__setattr__(metrics, "exp5_artifact_rows_digest", None)
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
        "exp3_worker_death_factorization__failed__dead1__p50__rep0",
        "condition-exp4-after-worker-death",
    ]
    exp3_task = _generation_records(
        tmp_path,
        exp3,
        "exp3_worker_death_factorization__failed__dead1__p50__rep0",
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
        assert exp3_row["accepted_validity"] is None
        assert exp3_row["accepted_validity_unavailable_reason"] == (
            "not_applicable_failed_experimental"
        )
        assert exp3_row["wall_clock_ms"] == 1000
        assert exp3_row["wall_clock_ms_unavailable_reason"] is None
        assert exp3_row["smoke_execution_status"] == "failed"
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
            "metrics/paper_metric_drafts.v1.json",
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


def _apply_exp4_runtime_probe(
    *,
    tmp_path: Path,
    hook_observations: list[dict[str, object]],
) -> formal_runner._RootExecutionOutcome:
    condition = SimpleNamespace(
        condition_id="condition-exp4-probe",
        repeat_id=0,
        ablation_mode="NO_VERIFICATION",
    )
    task = {
        "task_id": "case-exp4-probe",
        "root_status": "completed",
        "event_refs": [],
        "artifact_refs": [],
    }
    outcome = formal_runner._RootExecutionOutcome(
        case_id="case-exp4-probe",
        root_status="completed",
        adapter_root=tmp_path,
        worker_id="worker-1",
        task=task,
        adapter_result={
            "task_result": task,
            "attempt_results": [{"attempt_status": "succeeded"}],
            "fault_records": [],
            "event_records": [],
            "run_evidence": {
                "ablation_runtime": {
                    "schema_version": "tokenshare.paper_ablation_runtime.v1",
                    "hook_observations": hook_observations,
                }
            },
        },
    )
    return formal_runner._FormalConditionExecutionCallback._apply_exp4_mode(
        None,
        condition=condition,
        outcome=outcome,
        callback_kwargs={
            "mode_config": {"ablation_mode": "NO_VERIFICATION"}
        },
    )


def test_formal_runner_exp4_preserves_typed_hook_envelope_without_flat_fields(
    tmp_path: Path,
) -> None:
    artifact_ref = ArtifactRef(
        artifact_id="runtime-hook-artifact",
        artifact_type="experiment_evidence",
        uri="artifacts/runtime-hook-artifact.json",
        content_hash="sha256:" + "8" * 64,
        size_bytes=17,
        media_type="application/json",
        artifact_schema_id="tokenshare.test.runtime_hook",
        artifact_schema_version="v1",
        source={"kind": "pytest"},
        metadata={},
        created_at="2026-08-01T00:00:00Z",
    )
    observation = build_experiment_ablation_gate_applied_observation(
        ablation_mode="NO_VERIFICATION",
        disabled_mechanism="verification",
        protocol_event_refs=(),
        artifact_refs=(artifact_ref,),
        hook_input={
            "task_id": "case-exp4-probe",
            "unit_id": "unit-1",
            "attempt_id": "attempt-1",
            "lease_id": "lease-1",
        },
        hook_result={"bypass": True, "stop": False},
    ).to_dict()

    result = _apply_exp4_runtime_probe(
        tmp_path=tmp_path,
        hook_observations=[observation],
    )

    assert result.task["ablation_runtime"]["hook_observations"] == [
        observation
    ]
    assert set(
        result.task["ablation_runtime"]["hook_observations"][0]
    ) == {"schema_version", "kind", "payload", "observation_digest"}


def test_formal_runner_exp4_keeps_not_applicable_summary_outside_hooks(
    tmp_path: Path,
) -> None:
    result = _apply_exp4_runtime_probe(
        tmp_path=tmp_path,
        hook_observations=[],
    )
    runtime = result.task["ablation_runtime"]

    assert runtime["hook_observations"] == []
    assert runtime["target_hook_not_applicable"] == {
        "ablation_mode": "NO_VERIFICATION",
        "disabled_mechanism": "verification",
        "applicability": "not_applicable",
        "not_applicable_reason": "target_lifecycle_boundary_not_reached",
        "protocol_event_refs": [],
        "artifact_refs": [],
    }


def test_formal_runner_exp4_rejects_legacy_flat_hook_in_formal_path(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="runtime hook observation"):
        _apply_exp4_runtime_probe(
            tmp_path=tmp_path,
            hook_observations=[
                {
                    "event_type": "EXPERIMENT_ABLATION_GATE_APPLIED",
                    "ablation_mode": "NO_VERIFICATION",
                    "disabled_mechanism": "verification",
                }
            ],
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

    assert suite.status == PaperStatus.COMPLETED_WITH_FAILURES
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
    assert task["root_status"] == "ineligible"
    assert attempt["attempt_status"] == "model_identity_mismatch"
    assert attempt["cohort_member_id"] == COHORT_MEMBER_ID


def test_formal_runner_exp5_identity_mismatch_stops_condition_and_materializes_remaining_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    base = _two_case_dispatch_plan(tmp_path, config=config)
    base_condition, base_selection = base.bound_items()[0]
    first_condition = replace(
        base_condition,
        experiment_id=EXP5_EXPERIMENT_ID,
        condition_id="condition-exp5-first",
        model_cohort_id=MODEL_COHORT_ID,
        model_cohort_digest=MODEL_COHORT_DIGEST,
        cohort_member_id=COHORT_MEMBER_ID,
    )
    second_condition = replace(
        first_condition,
        condition_id="condition-exp5-second",
    )
    first_selection = replace(
        base_selection,
        experiment_id=EXP5_EXPERIMENT_ID,
    )
    second_selection = replace(
        first_selection,
        selection_id="selection-exp5-second",
        ordered_case_ids=("case-3",),
        expected_ai_unit_count=1,
    )
    plan = replace(
        base,
        experiment_id=EXP5_EXPERIMENT_ID,
        output_root=(tmp_path / EXP5_EXPERIMENT_ID).as_posix(),
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
    dispatch_calls: list[tuple[str, str]] = []

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

    def mismatching_dispatch(**kwargs):
        condition = kwargs["condition"]
        case_id = kwargs["case"]["case_id"]
        dispatch_calls.append((condition.condition_id, case_id))
        return _exp5_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=condition,
            case_id=case_id,
            resolved_model="unexpected/resolved-model",
        )

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXP5_EXPERIMENT_ID, Exp5CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", mismatching_dispatch)
    execution_kwargs = {
        **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
        "catalog_manifest": {
            "catalog_digest": CATALOG_DIGEST,
            "factorization_cases": tuple(
                {"case_id": case_id, "expected_ai_unit_count": 1}
                for case_id in ("case-1", "case-2", "case-3")
            ),
            "lean_cases": (),
            "lean_lemma_graph_cases": (),
        },
        "ai_api_configs": {
            PROVIDER_CONFIG_ID: config,
            formal_runner.APPROVED_ENDPOINT_BINDINGS_KEY: {
                EXP5_EXPERIMENT_ID: {
                    "status": "planned",
                    "cohort_id": MODEL_COHORT_ID,
                    "model_cohort_digest": MODEL_COHORT_DIGEST,
                    "member_plans": {COHORT_MEMBER_ID: member_plan},
                    "request_controls_snapshot": request_controls["comparable"],
                    "request_controls_snapshot_digest": request_controls[
                        "comparable_digest"
                    ],
                }
            },
        },
        "budget": _budget(
            planned_experiments=(EXP5_EXPERIMENT_ID,),
            planned_conditions=2,
            planned_root_runs=3,
            planned_ai_units=3,
        ),
    }
    suite = formal_runner.execute_paper_formal_suite(**execution_kwargs)

    assert suite.status == PaperStatus.COMPLETED_WITH_FAILURES
    assert suite.task_count == 3
    assert suite.provider_attempt_count == 2
    assert dispatch_calls == [
        (first_condition.condition_id, "case-1"),
        (second_condition.condition_id, "case-3"),
    ]
    first_tasks = _generation_records(
        tmp_path,
        EXP5_EXPERIMENT_ID,
        first_condition.condition_id,
        "per_task_results.jsonl",
    )
    first_attempts = _generation_records(
        tmp_path,
        EXP5_EXPERIMENT_ID,
        first_condition.condition_id,
        "per_attempt_results.jsonl",
    )
    assert [task["task_id"] for task in first_tasks] == ["case-1", "case-2"]
    assert [task["root_status"] for task in first_tasks] == [
        "ineligible",
        "not_started",
    ]
    assert first_tasks[1]["error_kind"] == "model_identity_fail_stop"
    assert [attempt["attempt_status"] for attempt in first_attempts] == [
        "model_identity_mismatch",
        "not_started",
    ]
    assert first_attempts[0]["provider_attempt_count"] == 1
    assert first_attempts[1]["provider_attempt_index"] == 0
    assert all(
        task["cohort_member_id"] == COHORT_MEMBER_ID for task in first_tasks
    )

    resumed = formal_runner.execute_paper_formal_suite(
        **execution_kwargs,
        resume=True,
    )
    assert resumed.status == PaperStatus.COMPLETED_WITH_FAILURES
    assert dispatch_calls == [
        (first_condition.condition_id, "case-1"),
        (second_condition.condition_id, "case-3"),
    ]


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
    max_tokens = 1024
    disk_estimate = paper_budget._paper_disk_estimate(
        planned_conditions=planned_conditions,
        planned_root_runs=planned_root_runs,
        planned_ai_units=planned_ai_units,
        provider_attempt_upper_bound=attempt_upper_bound,
        max_tokens=max_tokens,
        max_condition_root_runs=planned_root_runs,
        max_condition_ai_units=planned_ai_units,
        max_condition_provider_attempts=attempt_upper_bound,
    )
    return PaperBudgetResult(
        budget_digest=BUDGET_DIGEST,
        planned_experiments=planned_experiments,
        planned_conditions=planned_conditions,
        planned_root_runs=planned_root_runs,
        planned_ai_units=planned_ai_units,
        max_provider_attempts=attempt_upper_bound,
        token_upper_bound=attempt_upper_bound * max_tokens,
        cost_upper_bound=attempt_upper_bound * 0.01,
        wall_clock_estimate=float(max(1, planned_ai_units)),
        quota_preflight={
            "provider_calls_made": 0,
            "budget_commitments": {
                "request_limits": {
                    "token_upper_bound_per_provider_attempt": max_tokens,
                }
            },
        },
        rate_limit_preflight={"status": "not_checked"},
        disk_estimate=disk_estimate,
        status=PaperStatus.PLANNED,
    )


def _rolling_tracker(budget: PaperBudgetResult) -> formal_runner._RollingDiskForecast:
    estimate = budget.disk_estimate
    inputs = estimate["inputs"]
    policy = estimate["policy"]
    return formal_runner._RollingDiskForecast(
        remaining_root_runs=int(inputs["planned_root_runs"]),
        remaining_ai_units=int(inputs["planned_ai_units"]),
        remaining_provider_attempts=int(inputs["provider_attempt_upper_bound"]),
        fixed_forecast_bytes=(
            int(policy["fixed_manifest_bytes"])
            + int(policy["fixed_temp_bytes"])
        ),
        max_condition_compaction_bytes=int(
            estimate["max_condition_compaction_bytes"]
        ),
        policy=policy,
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


def _disk_test_budget() -> PaperBudgetResult:
    return _budget(
        planned_conditions=1,
        planned_root_runs=1,
        planned_ai_units=1,
    )


def test_formal_disk_preflight_allows_exact_available_capacity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget = _disk_test_budget()
    estimate_bytes = int(budget.disk_estimate["forecast_bytes"])
    condition_compaction_bytes = int(
        budget.disk_estimate["max_condition_compaction_bytes"]
    )
    headroom_bytes = max((estimate_bytes + 3) // 4, 2 * 1024**3)
    required = estimate_bytes + headroom_bytes + condition_compaction_bytes
    monkeypatch.setattr(
        formal_runner.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=required, used=0, free=required),
    )

    details = formal_runner._preflight_formal_disk_capacity(
        output_root=tmp_path / "new-suite",
        budget=budget,
        resume=False,
    )

    assert details["required_bytes"] == required
    assert details["available_bytes"] == required
    assert details["headroom_bytes"] == headroom_bytes
    assert details["condition_compaction_bytes"] == condition_compaction_bytes


def test_formal_disk_preflight_blocks_one_byte_short_before_evidence_or_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite_root = tmp_path / "disk-blocked-suite"
    config = _ai_config()
    plan = _planned_dispatch_plan(suite_root, config=config)
    kwargs = _formal_execution_kwargs(
        tmp_path=suite_root,
        config=config,
        plan=plan,
    )
    budget = _disk_test_budget()
    estimate_bytes = int(budget.disk_estimate["forecast_bytes"])
    condition_compaction_bytes = int(
        budget.disk_estimate["max_condition_compaction_bytes"]
    )
    headroom_bytes = max((estimate_bytes + 3) // 4, 2 * 1024**3)
    required = estimate_bytes + headroom_bytes + condition_compaction_bytes
    kwargs["budget"] = budget
    calls = {"evidence": 0, "provider": 0}

    def forbidden_initialize(**_kwargs: object) -> object:
        calls["evidence"] += 1
        raise AssertionError("evidence initialize must follow disk preflight")

    class ForbiddenTransport:
        def complete(self, **_kwargs: object) -> object:
            calls["provider"] += 1
            raise AssertionError("provider must follow disk preflight")

    kwargs["transport"] = ForbiddenTransport()
    monkeypatch.setattr(
        formal_runner.FormalEvidenceStore,
        "initialize",
        forbidden_initialize,
    )
    monkeypatch.setattr(
        formal_runner.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(
            total=required,
            used=1,
            free=required - 1,
        ),
    )

    with pytest.raises(
        formal_runner.PaperInfrastructureBlockedError,
        match="formal disk preflight failed",
    ) as captured:
        formal_runner.execute_paper_formal_suite(**kwargs)

    summary = captured.value.to_summary()
    assert summary["failure_stage"] == "disk_preflight"
    assert summary["failure_kind"] == "insufficient_disk_capacity"
    assert summary["resource_diagnostics"]["required_bytes"] == required
    assert summary["resource_diagnostics"]["available_bytes"] == required - 1
    assert summary["resource_diagnostics"]["components"] == {
        **budget.disk_estimate["components"],
        "condition_compaction_bytes": condition_compaction_bytes,
        "headroom_bytes": headroom_bytes,
    }
    assert calls == {"evidence": 0, "provider": 0}
    assert not suite_root.exists()


def test_formal_rolling_root_disk_guard_allows_exact_capacity_and_blocks_one_short(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget = _budget(
        planned_conditions=1,
        planned_root_runs=1,
        planned_ai_units=2,
        max_provider_attempts=6,
    )
    case = {"case_id": "case-1", "expected_ai_unit_count": 2}
    request_limits = {"max_provider_attempts": 3, "max_tokens": 100_000}
    policy = budget.disk_estimate["policy"]
    tracker = formal_runner._RollingDiskForecast(
        remaining_root_runs=1,
        remaining_ai_units=2,
        remaining_provider_attempts=6,
        fixed_forecast_bytes=(
            int(policy["fixed_manifest_bytes"])
            + int(policy["fixed_temp_bytes"])
        ),
        max_condition_compaction_bytes=int(
            budget.disk_estimate["max_condition_compaction_bytes"]
        ),
        policy=policy,
    )
    forecast = int(budget.disk_estimate["forecast_bytes"])
    headroom = max((forecast + 3) // 4, 2 * 1024**3)
    theoretical = 2 * 3 * 100_000 * int(policy["utf8_bytes_per_token"])
    forecast_payload = 6 * int(policy["p95_provider_attempt_payload_bytes"])
    theoretical_over_forecast = max(0, theoretical - forecast_payload)
    expected = (
        forecast
        + headroom
        + int(budget.disk_estimate["max_condition_compaction_bytes"])
        + theoretical_over_forecast
    )
    available = iter((expected, expected - 1))
    monkeypatch.setattr(
        formal_runner.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=expected, used=0, free=next(available)),
    )

    details = formal_runner._preflight_formal_root_capacity(
        output_root=tmp_path / "rolling-root",
        condition=SimpleNamespace(
            condition_id="condition-1",
            experiment_id="exp1_real_ai_feasibility",
        ),
        task_id="case-1",
        case=case,
        request_limits=request_limits,
        rolling_forecast=tracker,
    )

    assert details["required_bytes"] == expected
    assert details["available_bytes"] == expected
    assert details["theoretical_response_bytes"] == theoretical
    assert details["theoretical_over_forecast_bytes"] == theoretical_over_forecast
    assert details["remaining_forecast_bytes"] == forecast
    assert details["headroom_bytes"] == headroom
    assert tracker.remaining_root_runs == 0
    assert tracker.remaining_ai_units == 0
    assert tracker.remaining_provider_attempts == 0
    assert tracker.in_flight_forecast_bytes == details["root_reservation_bytes"]
    tracker.consume_root_forecast(
        root_reservation_bytes=int(details["root_reservation_bytes"])
    )
    assert tracker.in_flight_forecast_bytes == 0
    tracker_short = formal_runner._RollingDiskForecast(
        remaining_root_runs=1,
        remaining_ai_units=2,
        remaining_provider_attempts=6,
        fixed_forecast_bytes=(
            int(policy["fixed_manifest_bytes"])
            + int(policy["fixed_temp_bytes"])
        ),
        max_condition_compaction_bytes=int(
            budget.disk_estimate["max_condition_compaction_bytes"]
        ),
        policy=policy,
    )
    with pytest.raises(
        formal_runner.PaperInfrastructureBlockedError,
        match="rolling root disk guard failed",
    ) as captured:
        formal_runner._preflight_formal_root_capacity(
            output_root=tmp_path / "rolling-root",
            condition=SimpleNamespace(
                condition_id="condition-1",
                experiment_id="exp1_real_ai_feasibility",
            ),
            task_id="case-1",
            case=case,
            request_limits=request_limits,
            rolling_forecast=tracker_short,
        )
    summary = captured.value.to_summary()
    assert summary["failure_kind"] == "insufficient_rolling_root_capacity"
    assert summary["condition_id"] == "condition-1"
    assert summary["task_id"] == "case-1"
    assert summary["resource_diagnostics"]["available_bytes"] == expected - 1
    assert tracker_short.remaining_root_runs == 1
    assert tracker_short.remaining_ai_units == 2
    assert tracker_short.remaining_provider_attempts == 6
    assert tracker_short.in_flight_forecast_bytes == 0


def test_formal_condition_compaction_guard_has_exact_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reachable = 3 * 1024**3
    required = reachable + 2 * 1024**3
    available = iter((required, required - 1))
    monkeypatch.setattr(
        formal_runner.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=required, used=0, free=next(available)),
    )

    details = formal_runner._preflight_formal_condition_compaction_capacity(
        output_root=tmp_path / "compaction-root",
        condition_id="condition-1",
        current_condition_reachable_bytes=reachable,
    )

    assert details["required_bytes"] == required
    assert details["available_bytes"] == required
    with pytest.raises(
        formal_runner.PaperInfrastructureBlockedError,
        match="condition compaction disk guard failed",
    ) as captured:
        formal_runner._preflight_formal_condition_compaction_capacity(
            output_root=tmp_path / "compaction-root",
            condition_id="condition-1",
            current_condition_reachable_bytes=reachable,
        )
    assert captured.value.to_summary()["failure_kind"] == (
        "insufficient_condition_compaction_capacity"
    )


def test_compaction_guard_resume_reuses_nonempty_tail_without_provider_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)
    condition, _selection = plan.bound_items()[0]
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

    original_schedule = formal_runner.run_scheduled_cases

    def scheduled_with_terminal_event(**kwargs):
        result = original_schedule(**kwargs)
        if result.ordered_case_ids:
            return replace(
                result,
                events=(
                    {
                        "experiment_id": condition.experiment_id,
                        "condition_id": condition.condition_id,
                        "repeat_id": condition.repeat_id,
                        "task_id": "case-1",
                        "event_id": "merge-gate-condition-1",
                        "event_type": "MERGE_GATE_COMPLETED",
                    },
                ),
            )
        return result

    guard_calls = 0

    def fail_then_allow_guard(**_kwargs):
        nonlocal guard_calls
        guard_calls += 1
        if guard_calls == 1:
            raise formal_runner.PaperInfrastructureBlockedError(
                "synthetic compaction guard failure",
                evidence_integrity=formal_runner.PaperEvidenceIntegrity.INVALID,
                failure_stage="condition_compaction",
                failure_kind="insufficient_condition_compaction_capacity",
                condition_id=condition.condition_id,
            )
        return {"required_bytes": 1, "available_bytes": 1}

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", fake_case_dispatch)
    monkeypatch.setattr(formal_runner, "run_scheduled_cases", scheduled_with_terminal_event)
    monkeypatch.setattr(
        formal_runner,
        "_preflight_formal_condition_compaction_capacity",
        fail_then_allow_guard,
    )
    kwargs = _formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan)

    first = formal_runner.execute_paper_formal_suite(**kwargs)

    assert first.status == PaperStatus.BLOCKED
    assert adapter_calls == ["case-1"]
    run_root = (
        tmp_path
        / "experiments"
        / EXPERIMENT_ID
        / "runs"
        / condition.condition_id
        / "0"
    )
    current = json.loads((run_root / "CURRENT.json").read_text(encoding="utf-8"))
    tail_root = run_root / ".generations" / current["generation_id"]
    tail_manifest = json.loads(
        (tail_root / "generation_manifest.json").read_text(encoding="utf-8")
    )
    assert tail_manifest["delta_role"] == "condition_tail_events"
    assert "MERGE_GATE_COMPLETED" in (
        tail_root / "events" / "event_log.jsonl"
    ).read_text(encoding="utf-8")
    parent_ids = {path.name for path in (run_root / ".generations").iterdir()}
    assert len(parent_ids) == 2

    resumed = formal_runner.execute_paper_formal_suite(**kwargs, resume=True)

    assert resumed.status == PaperStatus.COMPLETED
    assert adapter_calls == ["case-1"]
    assert guard_calls == 2
    current = json.loads((run_root / "CURRENT.json").read_text(encoding="utf-8"))
    snapshot_root = run_root / ".generations" / current["generation_id"]
    snapshot_manifest = json.loads(
        (snapshot_root / "generation_manifest.json").read_text(encoding="utf-8")
    )
    assert snapshot_manifest["generation_kind"] == "snapshot"
    assert {path.name for path in (run_root / ".generations").iterdir()} == {
        current["generation_id"]
    }


def test_formal_rolling_guard_counts_prior_in_flight_theoretical_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget = _budget(
        planned_conditions=1,
        planned_root_runs=2,
        planned_ai_units=2,
        max_provider_attempts=2,
    )
    policy = budget.disk_estimate["policy"]
    tracker = formal_runner._RollingDiskForecast(
        remaining_root_runs=2,
        remaining_ai_units=2,
        remaining_provider_attempts=2,
        fixed_forecast_bytes=(
            int(policy["fixed_manifest_bytes"])
            + int(policy["fixed_temp_bytes"])
        ),
        max_condition_compaction_bytes=int(
            budget.disk_estimate["max_condition_compaction_bytes"]
        ),
        policy=policy,
    )
    free = {"bytes": 10**15}
    monkeypatch.setattr(
        formal_runner.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(
            total=10**15,
            used=0,
            free=free["bytes"],
        ),
    )
    condition = SimpleNamespace(
        condition_id="condition-1",
        experiment_id="exp1_real_ai_feasibility",
    )
    first = formal_runner._preflight_formal_root_capacity(
        output_root=tmp_path,
        condition=condition,
        task_id="case-1",
        case={"case_id": "case-1", "expected_ai_unit_count": 1},
        request_limits={"max_provider_attempts": 1, "max_tokens": 100_000},
        rolling_forecast=tracker,
    )
    assert first["theoretical_over_forecast_bytes"] > 0
    assert tracker.in_flight_forecast_bytes == first["root_reservation_bytes"]
    free["bytes"] = int(first["required_bytes"])

    with pytest.raises(
        formal_runner.PaperInfrastructureBlockedError,
        match="rolling root disk guard failed",
    ) as captured:
        formal_runner._preflight_formal_root_capacity(
            output_root=tmp_path,
            condition=condition,
            task_id="case-2",
            case={"case_id": "case-2", "expected_ai_unit_count": 1},
            request_limits={
                "max_provider_attempts": 1,
                "max_tokens": 100_000,
            },
            rolling_forecast=tracker,
        )

    diagnostics = captured.value.to_summary()["resource_diagnostics"]
    assert diagnostics["required_bytes"] > first["required_bytes"]
    assert diagnostics["remaining_forecast_bytes"] > (
        first["remaining_forecast_bytes"] - first["root_forecast_bytes"]
    )


def test_formal_rolling_root_guard_blocks_before_provider_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite_root = tmp_path / "rolling-provider-blocked"
    config = _ai_config()
    plan = _planned_dispatch_plan(suite_root, config=config)
    kwargs = _formal_execution_kwargs(
        tmp_path=suite_root,
        config=config,
        plan=plan,
    )
    disk_checks = {"count": 0}
    provider_calls = {"count": 0}

    class ForbiddenTransport:
        def complete(self, **_kwargs: object) -> object:
            provider_calls["count"] += 1
            raise AssertionError("provider dispatch must follow rolling disk guard")

    def disk_usage(_path: object) -> SimpleNamespace:
        disk_checks["count"] += 1
        free = 10**15 if disk_checks["count"] == 1 else 0
        return SimpleNamespace(total=10**15, used=0, free=free)

    kwargs["transport"] = ForbiddenTransport()
    monkeypatch.setattr(formal_runner.shutil, "disk_usage", disk_usage)
    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_condition",
        lambda *, context, plan, condition_id: context.execution_callback(
            condition=plan.conditions[0],
            selection=plan.condition_selection_bindings[0].selection,
        ),
    )
    monkeypatch.setattr(
        formal_runner,
        "_checkpoint_dependency_outcome",
        lambda **_kwargs: pytest.fail(
            "disk-resource closure must not checkpoint remaining roots"
        ),
    )

    result = formal_runner.execute_paper_formal_suite(**kwargs)

    assert result.status == PaperStatus.BLOCKED
    assert result.error_summary[0]["failure_kind"] == (
        "insufficient_rolling_root_capacity"
    )
    assert provider_calls["count"] == 0
    assert disk_checks["count"] >= 2
    marker = json.loads(
        (suite_root / "infrastructure_blocked.json").read_text(encoding="utf-8")
    )
    assert marker["remaining_task_count"] == 1
    assert marker["failure"]["failure_kind"] == (
        "insufficient_rolling_root_capacity"
    )


def test_disk_resource_block_closure_writes_one_bounded_marker_for_50k_remaining(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    condition = SimpleNamespace(
        experiment_id=EXPERIMENT_ID,
        condition_id="condition-50k",
        repeat_id=0,
    )
    selection = SimpleNamespace(
        ordered_case_ids=tuple(f"case-{index:05d}" for index in range(50_000))
    )
    plan = SimpleNamespace(experiment_id=EXPERIMENT_ID, status="planned")
    writes: list[dict[str, object]] = []
    monkeypatch.setattr(
        formal_runner,
        "_atomic_write_json",
        lambda _path, body: writes.append(dict(body)),
    )
    monkeypatch.setattr(
        formal_runner,
        "_checkpoint_dependency_outcome",
        lambda **_kwargs: pytest.fail("disk closure must not write per-root evidence"),
    )
    blocked_error = formal_runner.PaperInfrastructureBlockedError(
        "rolling capacity exhausted",
        evidence_integrity=formal_runner.PaperEvidenceIntegrity.INVALID,
        failure_stage="disk_preflight",
        failure_kind="insufficient_rolling_root_capacity",
        condition_id=condition.condition_id,
        task_id="case-00000",
        diagnostics={"required_bytes": 2, "available_bytes": 1},
    )

    result = formal_runner._close_disk_resource_blocked_suite(
        suite_root=tmp_path,
        suite_id="suite-50k",
        started_at="2026-07-31T00:00:00Z",
        plans=(plan,),
        bound_plans=((plan, ((condition, selection),)),),
        condition_results=(),
        blocked_error=blocked_error,
        root_case_filter={},
        usage=formal_runner._UsageTotals(),
        budget=_budget(
            planned_conditions=1,
            planned_root_runs=50_000,
            planned_ai_units=50_000,
        ),
        budget_approval={"approval_mode": "user_bypassed"},
        completed_task_keys=set(),
    )

    assert result.status == PaperStatus.BLOCKED
    assert result.task_count == 50_000
    assert len(writes) == 1
    assert writes[0]["remaining_task_count"] == 50_000
    assert len(json.dumps(writes[0])) < 5_000


def test_formal_disk_preflight_resume_conservatively_keeps_full_forecast(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite_root = tmp_path / "resume-suite"
    suite_root.mkdir()
    budget = _disk_test_budget()
    estimate_bytes = int(budget.disk_estimate["forecast_bytes"])
    reachable_bytes = estimate_bytes + 500
    (suite_root / "evidence_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "tokenshare.paper_evidence_manifest.v2",
                "static_files": [{"path": "suite_manifest.json", "size": 700}],
                "conditions": [
                    {
                        "experiment_id": EXPERIMENT_ID,
                        "condition_id": "condition-1",
                        "repeat_id": 0,
                        "condition_manifest_ref": {
                            "path": "condition_manifest.json",
                            "size": 100,
                        },
                        "reachable_size_bytes": reachable_bytes,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    two_gib = 2 * 1024**3
    headroom = max((estimate_bytes + 3) // 4, two_gib)
    required = estimate_bytes + headroom + reachable_bytes
    monkeypatch.setattr(
        formal_runner.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=required, used=0, free=required),
    )

    details = formal_runner._preflight_formal_disk_capacity(
        output_root=suite_root,
        budget=budget,
        resume=True,
    )

    assert details["existing_canonical_bytes"] == 700 + reachable_bytes
    assert details["remaining_forecast_bytes"] == estimate_bytes
    assert details["headroom_bytes"] == headroom
    assert details["condition_compaction_bytes"] == reachable_bytes
    assert details["required_bytes"] == required


def test_formal_disk_preflight_resume_includes_largest_condition_cow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite_root = tmp_path / "resume-cow-suite"
    suite_root.mkdir()
    three_gib = 3 * 1024**3
    (suite_root / "evidence_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "tokenshare.paper_evidence_manifest.v2",
                "static_files": [],
                "conditions": [
                    {
                        "experiment_id": EXPERIMENT_ID,
                        "condition_id": "condition-large",
                        "repeat_id": 0,
                        "condition_manifest_ref": {
                            "path": "condition_manifest.json",
                            "size": 100,
                        },
                        "reachable_size_bytes": three_gib,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    forecast = int(_disk_test_budget().disk_estimate["forecast_bytes"])
    headroom = max((forecast + 3) // 4, 2 * 1024**3)
    required = forecast + headroom + three_gib
    monkeypatch.setattr(
        formal_runner.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=required, used=0, free=required),
    )

    details = formal_runner._preflight_formal_disk_capacity(
        output_root=suite_root,
        budget=_disk_test_budget(),
        resume=True,
    )

    assert details["remaining_forecast_bytes"] == int(
        _disk_test_budget().disk_estimate["forecast_bytes"]
    )
    assert details["condition_compaction_bytes"] == three_gib
    assert details["headroom_bytes"] == 2 * 1024**3
    assert details["required_bytes"] == required


def test_formal_replay_only_skips_disk_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = SimpleNamespace(status="replayed")
    monkeypatch.setattr(
        formal_runner,
        "replay_paper_formal_suite",
        lambda *, output_root: expected,
    )
    monkeypatch.setattr(
        formal_runner.shutil,
        "disk_usage",
        lambda _path: pytest.fail("replay must skip disk preflight"),
    )

    result = formal_runner.execute_paper_formal_suite(
        dispatch_plans=(),
        catalog_manifest={},
        budget=_disk_test_budget(),
        budget_approval={},
        output_root=tmp_path,
        ai_api_configs={},
        transport=object(),
        real_transport=False,
        hard_limits={},
        replay_only=True,
    )

    assert result is expected


@pytest.mark.parametrize(
    "drift_kind",
    (
        "schema",
        "forecast_sum",
        "policy",
        "input_count",
        "negative_component",
        "theoretical_payload",
        "token_identity",
        "extra_top_level",
    ),
)
def test_formal_disk_preflight_rejects_estimate_drift_before_evidence_or_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift_kind: str,
) -> None:
    suite_root = tmp_path / f"disk-drift-{drift_kind}"
    config = _ai_config()
    plan = _planned_dispatch_plan(suite_root, config=config)
    kwargs = _formal_execution_kwargs(
        tmp_path=suite_root,
        config=config,
        plan=plan,
    )
    budget = _disk_test_budget()
    estimate = json.loads(json.dumps(budget.disk_estimate))
    if drift_kind == "schema":
        estimate["schema_version"] = "tokenshare.paper_disk_estimate.v1"
    elif drift_kind == "forecast_sum":
        estimate["forecast_bytes"] = 0
    elif drift_kind == "policy":
        estimate["policy"]["utf8_bytes_per_token"] = 3
    elif drift_kind == "input_count":
        estimate["inputs"]["planned_root_runs"] = 2
    elif drift_kind == "negative_component":
        estimate["components"]["forecast_provider_attempt_payload_bytes"] = -1
    elif drift_kind == "theoretical_payload":
        estimate["theoretical_max_payload_bytes"] = 0
    elif drift_kind == "token_identity":
        quota = json.loads(json.dumps(budget.quota_preflight))
        quota["budget_commitments"]["request_limits"][
            "token_upper_bound_per_provider_attempt"
        ] = 2048
        budget = replace(budget, quota_preflight=quota)
    elif drift_kind == "extra_top_level":
        estimate["unexpected"] = True
    kwargs["budget"] = replace(budget, disk_estimate=estimate)
    calls = {"evidence": 0, "provider": 0}

    def forbidden_initialize(**_kwargs: object) -> object:
        calls["evidence"] += 1
        raise AssertionError("invalid estimate reached EvidenceStore")

    class ForbiddenTransport:
        def complete(self, **_kwargs: object) -> object:
            calls["provider"] += 1
            raise AssertionError("invalid estimate reached provider")

    kwargs["transport"] = ForbiddenTransport()
    monkeypatch.setattr(
        formal_runner.FormalEvidenceStore,
        "initialize",
        forbidden_initialize,
    )
    monkeypatch.setattr(
        formal_runner.shutil,
        "disk_usage",
        lambda _path: pytest.fail("invalid estimate reached disk usage"),
    )

    with pytest.raises(
        formal_runner.PaperInfrastructureBlockedError,
        match="formal disk estimate is invalid",
    ) as captured:
        formal_runner.execute_paper_formal_suite(**kwargs)

    assert captured.value.to_summary()["failure_stage"] == "disk_preflight"
    assert calls == {"evidence": 0, "provider": 0}
    assert not suite_root.exists()


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
        provider_attempt_count=1,
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
        b'{"request":"body"}',
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
    logical = formal_runner.FormalEvidenceStore(
        suite_root
    ).load_logical_run_records(
        experiment_id=experiment_id,
        condition_id=condition_id,
        repeat_id=0,
    )
    key_by_path = {
        "per_task_results.jsonl": "tasks",
        "per_attempt_results.jsonl": "attempts",
        "fault_injections.jsonl": "faults",
        "events/event_log.jsonl": "events",
        "artifacts/artifact_index.jsonl": "artifacts",
    }
    return list(logical[key_by_path[relative_path]])


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
    compatibility_root = tmp_path / EXPERIMENT_ID
    assert (compatibility_root / "experiment_manifest.json").is_file()
    assert (
        compatibility_root
        / "runs"
        / condition.condition_id
        / "0"
        / "CURRENT.json"
    ).is_file()
    assert not (
        compatibility_root
        / "runs"
        / condition.condition_id
        / selected_case_id
    ).exists()
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
    from tokenshare.experiments.paper_formal_evidence import FormalEvidenceStore

    generate_paper_smoke_report(output_root=tmp_path, secret_values=())
    FormalEvidenceStore(tmp_path)._refresh_evidence_manifest()
    tree_before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    import tokenshare.experiments.paper_smoke_report as smoke_report_module

    monkeypatch.setattr(
        smoke_report_module,
        "generate_paper_smoke_report",
        lambda **_kwargs: pytest.fail("smoke replay must be read-only"),
    )

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
    assert {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    } == tree_before


def test_missing_bank_preflight_records_block_without_engine_task_lease_request_provider_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.experiments.test_factorization_paper_adapter import _v2_condition
    from tokenshare.experiments.factorization_paper_adapter import (
        ScriptedFactorizationRangeTransport,
        run_factorization_paper_case,
    )
    from tokenshare.experiments.paper_factorization_catalog import (
        generate_factorization_paper_cases,
    )
    from tokenshare.experiments.paper_response_bank import (
        PaperFormalTraceContext,
        PaperTraceCaseBinding,
        SemanticInventoryPlan,
    )
    from tokenshare.executors.response_bank import canonical_digest

    case = generate_factorization_paper_cases()[0]
    source = run_factorization_paper_case(
        case=case,
        condition=_v2_condition(case),
        output_root=tmp_path / "source",
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
    )
    complete_runtime = _trace_context_from_adapter_result(
        bank_root=tmp_path / "complete-bank",
        adapter_result=source,
    )
    omitted_id = complete_runtime.resolver.index.entries[-1].entry_id
    runtime = _trace_context_from_adapter_result(
        bank_root=tmp_path / "incomplete-bank",
        adapter_result=source,
        omitted_terminal_entry_ids=(omitted_id,),
    )
    rows = runtime.resolver.index.inventory_rows
    config = _ai_config()
    dispatch_plan = _planned_dispatch_plan(tmp_path / "suite", config=config)
    condition = dispatch_plan.conditions[0]
    inventory_plan = SemanticInventoryPlan(
        schema_version="tokenshare.response_bank_semantic_inventory_plan.v1",
        inventory_digest=runtime.resolver.index.manifest.inventory_digest,
        rows=rows,
        condition_refs=(
            {
                "condition_id": condition.condition_id,
                "condition_digest": condition.condition_digest,
                "experiment_id": condition.experiment_id,
                "worker_count": condition.worker_count,
                "repeat_id": condition.repeat_id,
                "fault_type": condition.fault_type,
                "ablation_mode": condition.ablation_mode,
                "semantic_slot_keys": [row.semantic_slot_key for row in rows],
                "case_refs": [
                    {
                        "case_id": "case-1",
                        "case_record_digest": "sha256:" + "a" * 64,
                        "semantic_slot_keys": [
                            row.semantic_slot_key for row in rows
                        ],
                    }
                ],
            },
        ),
        exp2_online_condition_refs=(),
        max_concurrent_roots=1,
        expected_slot_count=len(rows),
        terminal_provider_failure_count=0,
        terminal_success_count=len(runtime.resolver.index.entries),
        terminal_unacquired_count=1,
    )
    case_binding = PaperTraceCaseBinding(
        condition_id=condition.condition_id,
        case_id="case-1",
        case_record_digest="sha256:" + "a" * 64,
        runtime=runtime,
        inventory_entry_ids=tuple(row.inventory_entry_id for row in rows),
    )
    with pytest.raises(ValueError, match="identity"):
        PaperFormalTraceContext(
            inventory_plan=inventory_plan,
            cases=(replace(case_binding, inventory_entry_ids=("wrong",)),),
        )

    alternate_digest = "sha256:" + "b" * 64
    alternate_runtime = _trace_context_from_adapter_result(
        bank_root=tmp_path / "alternate-bank",
        adapter_result=source,
        case_record_digest=alternate_digest,
    )
    alternate_rows = alternate_runtime.resolver.index.inventory_rows
    combined_rows = tuple(rows) + tuple(alternate_rows)
    cross_case_plan = replace(
        inventory_plan,
        inventory_digest=canonical_digest(
            [row.to_dict() for row in sorted(
                combined_rows, key=lambda item: item.inventory_entry_id
            )]
        ),
        rows=combined_rows,
        condition_refs=(
            {
                **inventory_plan.condition_refs[0],
                "semantic_slot_keys": [
                    row.semantic_slot_key for row in combined_rows
                ],
                "case_refs": [
                    inventory_plan.condition_refs[0]["case_refs"][0],
                    {
                        "case_id": "case-2",
                        "case_record_digest": alternate_digest,
                        "semantic_slot_keys": [
                            row.semantic_slot_key for row in alternate_rows
                        ],
                    },
                ],
            },
        ),
        expected_slot_count=len(combined_rows),
        terminal_success_count=(
            len(runtime.resolver.index.entries)
            + len(alternate_runtime.resolver.index.entries)
        ),
        terminal_unacquired_count=1,
    )
    alternate_binding = PaperTraceCaseBinding(
        condition_id=condition.condition_id,
        case_id="case-2",
        case_record_digest=alternate_digest,
        runtime=alternate_runtime,
        inventory_entry_ids=tuple(
            row.inventory_entry_id for row in alternate_rows
        ),
    )
    PaperFormalTraceContext(
        inventory_plan=cross_case_plan,
        cases=(case_binding, alternate_binding),
    )
    with pytest.raises(ValueError, match="case record digest"):
        PaperFormalTraceContext(
            inventory_plan=cross_case_plan,
            cases=(
                replace(
                    case_binding,
                    runtime=alternate_runtime,
                    inventory_entry_ids=alternate_binding.inventory_entry_ids,
                ),
                replace(
                    alternate_binding,
                    runtime=runtime,
                    inventory_entry_ids=case_binding.inventory_entry_ids,
                ),
            ),
        )
    context = PaperFormalTraceContext(
        inventory_plan=inventory_plan,
        cases=(case_binding,),
    )
    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_case",
        lambda **_kwargs: pytest.fail("preflight block must precede protocol dispatch"),
    )
    suite_root = tmp_path / "suite"
    suite = formal_runner.execute_paper_formal_suite(
        **_formal_execution_kwargs(
            tmp_path=suite_root,
            config=config,
            plan=dispatch_plan,
        ),
        trace_context=context,
    )

    assert suite.status is PaperStatus.BLOCKED
    marker = suite_root / "paper_preflight_blocked.v1.json"
    assert marker.is_file()
    assert not tuple(suite_root.rglob("*.jsonl"))
    assert not tuple(suite_root.rglob("events"))


def test_formal_runner_passes_trace_context_to_condition_callback() -> None:
    import ast
    import inspect
    import textwrap

    tree = ast.parse(
        textwrap.dedent(inspect.getsource(formal_runner._dispatch_formal_conditions))
    )
    callback_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_FormalConditionExecutionCallback"
    ]

    assert len(callback_calls) == 1
    trace_keywords = [
        keyword
        for keyword in callback_calls[0].keywords
        if keyword.arg == "trace_context"
    ]
    assert len(trace_keywords) == 1
    assert isinstance(trace_keywords[0].value, ast.Name)
    assert trace_keywords[0].value.id == "trace_context"


def test_formal_runner_finalizer_calls_match_public_signature() -> None:
    import ast
    import inspect
    import textwrap

    signature = inspect.signature(formal_runner._finalize_formal_experiment)
    accepted_keywords = set(signature.parameters)
    tree = ast.parse(
        textwrap.dedent(inspect.getsource(formal_runner._dispatch_formal_conditions))
    )
    finalizer_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_finalize_formal_experiment"
    ]

    assert len(finalizer_calls) == 2
    assert all(
        keyword.arg in accepted_keywords
        for call in finalizer_calls
        for keyword in call.keywords
    )


def _trace_context_from_adapter_result(
    *,
    bank_root: Path,
    adapter_result,
    replacement_count: int = 1,
    omitted_terminal_entry_ids: tuple[str, ...] = (),
    case_record_digest: str = "sha256:" + "a" * 64,
):
    from hashlib import sha256

    from tokenshare.executors.response_bank import (
        OBJECT_ROLES,
        ExternalBankObjectLocator,
        ResponseBankEntry,
        ResponseBankInventoryRow,
        ResponseBankManifest,
        ResponseBankResolver,
        canonical_digest,
        initialize_response_bank,
        inventory_entry_id,
        semantic_slot_key,
    )
    from tokenshare.executors.trace_backed import freeze_trace_source_binding
    from tokenshare.experiments.paper_dispatcher import PaperTraceRuntimeContext

    def digest(data: bytes) -> str:
        return f"sha256:{sha256(data).hexdigest()}"

    source_store = ArtifactStore(Path(adapter_result.output_root))
    specs = []
    rows = []
    for attempt in adapter_result.attempt_results:
        if attempt.parsed_output_ref is None:
            continue
        candidate = source_store.read_bytes(
            ArtifactRef.from_dict(attempt.parsed_output_ref)
        ).decode("utf-8")
        for replacement_slot in range(replacement_count):
            entry_id = f"entry-{attempt.planned_ai_unit_id}-{replacement_slot}"
            request_body = json.dumps(
                {
                    "planned_ai_unit_id": attempt.planned_ai_unit_id,
                    "replacement_slot": replacement_slot,
                },
                sort_keys=True,
            ).encode("utf-8")
            inference_digest = digest(request_body + b":inference")
            values = {
                "inventory_entry_id": "",
                "semantic_slot_key": semantic_slot_key(
                    case_record_digest=case_record_digest,
                    planned_ai_unit_id=attempt.planned_ai_unit_id,
                    sample_slot_index=0,
                    replacement_slot=replacement_slot,
                    provider_config_digest="sha256:provider",
                    prompt_profile_digest="sha256:prompt",
                    prompt_admission_profile_digest="sha256:admission",
                    plugin_version="2.0.0",
                ),
                "case_record_digest": case_record_digest,
                "planned_ai_unit_id": attempt.planned_ai_unit_id,
                "sample_slot_index": 0,
                "replacement_slot": replacement_slot,
                "provider_config_digest": "sha256:provider",
                "prompt_profile_digest": "sha256:prompt",
                "prompt_admission_profile_digest": "sha256:admission",
                "plugin_version": "2.0.0",
                "entry_id": entry_id,
                "body_digest": digest(request_body),
                "inference_request_digest": inference_digest,
            }
            values["inventory_entry_id"] = inventory_entry_id(values)
            row = ResponseBankInventoryRow(**values)
            rows.append(row)
            specs.append((row, request_body, inference_digest, candidate))
    inventory_digest = canonical_digest(
        [row.to_dict() for row in sorted(rows, key=lambda item: item.inventory_entry_id)]
    )
    retained_specs = tuple(
        spec for spec in specs if spec[0].entry_id not in omitted_terminal_entry_ids
    )
    receipt_digest = "sha256:" + "9" * 64
    manifest = ResponseBankManifest.create(
        bank_root_id=f"bank-{bank_root.name}",
        profile_digest="sha256:profile",
        budget_digest="sha256:budget",
        inventory_digest=inventory_digest,
        provider_config_digest="sha256:provider",
        entry_ids=tuple(row.entry_id for row, *_rest in retained_specs),
        object_role_schema=OBJECT_ROLES,
        terminal_entry_count=len(retained_specs),
        created_by_paid_receipt_digest=receipt_digest,
    )
    entries = []
    objects = {}
    for row, request_body, inference_digest, candidate in retained_specs:
        raw_objects = {
            "request_body": request_body,
            "raw_output": json.dumps(
                {
                    "schema_version": "tokenshare.response_bank_raw_output.v1",
                    "raw_response_json": {"id": row.entry_id},
                    "content_text": candidate,
                    "reasoning_content": None,
                    "provider_response_id": row.entry_id,
                    "finish_reason": "stop",
                },
                sort_keys=True,
            ).encode("utf-8"),
            "provenance": b'{"source_transport":"real_api"}',
            "usage": b'{"usage_status":"reported","usage":{"total_tokens":7}}',
            "latency": json.dumps(
                {"latency_ms": 10 + row.replacement_slot}
            ).encode("utf-8"),
            "pricing": b'{"cost_usd":"0.01"}',
            "acquisition_attempt": b'{"attempt":"approved"}',
            "model_record": b'{"model":"frozen-model"}',
        }
        locators = tuple(
            ExternalBankObjectLocator(
                bank_root_id=manifest.bank_root_id,
                manifest_digest=manifest.manifest_digest,
                entry_id=row.entry_id,
                object_role=role,
                object_digest=digest(data),
            )
            for role, data in raw_objects.items()
        )
        objects.update(
            {locator.object_digest: raw_objects[locator.object_role] for locator in locators}
        )
        entries.append(
            ResponseBankEntry(
                inventory_digest=inventory_digest,
                inventory_entry_id=row.inventory_entry_id,
                semantic_slot_key=row.semantic_slot_key,
                inference_request_digest=inference_digest,
                entry_id=row.entry_id,
                sample_slot_index=0,
                replacement_slot=row.replacement_slot,
                terminal_kind="success",
                object_locators=locators,
                acquisition_state_ref=f"state-{row.entry_id}",
            )
        )
    initialize_response_bank(
        bank_root,
        manifest=manifest,
        inventory_rows=rows,
        entries=entries,
        objects=objects,
    )
    resolver = ResponseBankResolver.open(bank_root)
    bindings = tuple(
        freeze_trace_source_binding(
            resolver,
            planned_ai_unit_id=planned_ai_unit_id,
            sample_slot_index=0,
            entry_ids=tuple(
                row.entry_id
                for row in rows
                if row.planned_ai_unit_id == planned_ai_unit_id
                and row.entry_id not in omitted_terminal_entry_ids
            ),
        )
        for planned_ai_unit_id in dict.fromkeys(
            row.planned_ai_unit_id for row in rows
        )
        if any(
            row.planned_ai_unit_id == planned_ai_unit_id
            and row.entry_id not in omitted_terminal_entry_ids
            for row in rows
        )
    )
    return PaperTraceRuntimeContext(
        resolver=resolver,
        bindings=bindings,
    )


def test_trace_run_uses_normal_coordinator_fault_verifier_checker_merge_settlement(
    tmp_path: Path,
) -> None:
    from tests.experiments.test_factorization_paper_adapter import (
        _v2_condition,
    )
    from tokenshare.experiments.factorization_paper_adapter import (
        ScriptedFactorizationRangeTransport,
        run_factorization_paper_case,
    )
    from tokenshare.experiments.paper_factorization_catalog import (
        generate_factorization_paper_cases,
    )

    case = generate_factorization_paper_cases()[0]
    condition = _v2_condition(case)
    source = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "source",
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
    )
    trace_context = _trace_context_from_adapter_result(
        bank_root=tmp_path / "bank",
        adapter_result=source,
    )
    result = formal_runner.dispatch_paper_case(
        case=case,
        condition=condition,
        output_root=(tmp_path / "trace").as_posix(),
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
        ai_api_config=None,
        entry_id=None,
        max_tokens=512,
        timeout_seconds=30,
        trace_context=trace_context,
    )
    event_types = {event["event_type"] for event in result.event_records}
    assert {
        "TASK_REGISTERED",
        "LEASE_STATE_CHANGED",
        "EXECUTION_REQUEST_RECORDED",
    } <= event_types
    assert {"VERIFICATION_RECORDED", "CANONICAL_OUTPUTS_BOUND"} <= event_types
    assert any("MERGE" in event_type for event_type in event_types)
    assert any("SETTLEMENT" in event_type for event_type in event_types)
    assert result.task_result.root_status is PaperTaskStatus.COMPLETED


def test_runner_object_graph_never_materializes_source(tmp_path: Path) -> None:
    from tests.experiments.test_factorization_paper_adapter import _v2_condition
    from tokenshare.experiments.factorization_paper_adapter import (
        ScriptedFactorizationRangeTransport,
        run_factorization_paper_case,
    )
    from tokenshare.experiments.paper_factorization_catalog import (
        generate_factorization_paper_cases,
    )

    case = generate_factorization_paper_cases()[0]
    condition = _v2_condition(case)
    source = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "source",
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
    )
    bank_root = tmp_path / "bank"
    runtime = _trace_context_from_adapter_result(
        bank_root=bank_root,
        adapter_result=source,
    )
    source_bytes = {
        path.relative_to(bank_root).as_posix(): path.read_bytes()
        for path in bank_root.rglob("*")
        if path.is_file()
    }
    assert not any(
        isinstance(value, (bytes, bytearray))
        for value in runtime.__dict__.values()
    )
    result = formal_runner.dispatch_paper_case(
        case=case,
        condition=condition,
        output_root=(tmp_path / "trace").as_posix(),
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
        ai_api_config=None,
        entry_id=None,
        max_tokens=512,
        timeout_seconds=30,
        trace_context=runtime,
    )
    assert result.task_result.root_status is PaperTaskStatus.COMPLETED
    assert {
        path.relative_to(bank_root).as_posix(): path.read_bytes()
        for path in bank_root.rglob("*")
        if path.is_file()
    } == source_bytes
    assert not (Path(result.output_root) / "objects").exists()


def test_trace_current_provider_calls_are_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.experiments.test_lean_paper_adapter import _condition_for_case
    from tests.support.lean_checker import RecordingLeanChecker
    from tokenshare.plugins.lean_proof.checker import LeanCheckerStatus
    from tokenshare.experiments.lean_paper_adapter import (
        ScriptedLeanPaperProofTransport,
        run_lean_paper_case,
    )
    import tokenshare.experiments.lean_paper_adapter as lean_adapter_module
    import tokenshare.experiments.paper_catalog as paper_catalog_module
    from tokenshare.experiments.paper_dispatcher import PaperTraceRuntimeContext

    case = paper_catalog_module._with_lean_v1_paper_difficulty(
        json.loads(
            Path("benchmarks/paper/lean_catalog.v1.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()[0]
        )
    )
    environment = lean_adapter_module.default_lean_paper_environment_manifest()
    object.__setattr__(environment, "environment_digest", case["environment_digest"])
    monkeypatch.setattr(
        lean_adapter_module,
        "default_lean_paper_environment_manifest",
        lambda: environment,
    )
    condition = _condition_for_case(CATALOG_DIGEST, case)
    source = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "lean-source",
        transport=ScriptedLeanPaperProofTransport(),
        real_transport=False,
        checker=RecordingLeanChecker(),
    )
    context = _trace_context_from_adapter_result(
        bank_root=tmp_path / "lean-bank",
        adapter_result=source,
        replacement_count=2,
    )
    transport = ScriptedLeanPaperProofTransport()

    class RejectFirstChecker(RecordingLeanChecker):
        def __call__(self, request, *, artifact_store, environment_manifest):
            self.status = (
                LeanCheckerStatus.REJECTED
                if not self.requests
                else LeanCheckerStatus.ACCEPTED
            )
            return super().__call__(
                request,
                artifact_store=artifact_store,
                environment_manifest=environment_manifest,
            )

    checker = RejectFirstChecker()
    result = formal_runner.dispatch_paper_case(
        case=case,
        condition=condition,
        output_root=(tmp_path / "lean-trace").as_posix(),
        transport=transport,
        real_transport=False,
        ai_api_config=None,
        entry_id=None,
        max_tokens=1024,
        timeout_seconds=30,
        checker=checker,
        trace_context=context,
    )

    assert PaperTraceRuntimeContext.current_provider_call_count == 0
    assert transport.calls == []
    assert result.task_result.provider_attempt_count == 0
    assert checker.requests
    event_types = {event["event_type"] for event in result.event_records}
    assert {
        "TASK_REGISTERED",
        "LEASE_STATE_CHANGED",
        "EXECUTION_REQUEST_RECORDED",
        "VERIFICATION_RECORDED",
        "CANONICAL_OUTPUTS_BOUND",
    } <= event_types
    assert any(
        event["event_type"] == "TRACE_DELIVERY_COMMITTED.v1"
        for event in result.event_records
    )
    assert result.merge_summary["status"] == "completed"
    assert any(
        attempt.attempt_status is PaperAttemptStatus.CHECKER_REJECTED
        for attempt in result.attempt_results
    )
    captured_facts = []
    evaluate_evidence = formal_runner.evaluate_versioned_paper_evidence

    def capture_evidence(facts):
        captured_facts.append(facts)
        return evaluate_evidence(facts)

    monkeypatch.setattr(
        formal_runner,
        "evaluate_versioned_paper_evidence",
        capture_evidence,
    )
    missing_receipt = formal_runner._evaluate_trace_root_evidence(
        adapter_result=result,
        adapter_root=Path(result.output_root),
        trace_runtime=context,
    )
    manifest = context.resolver.index.manifest
    paid_context = replace(
        context,
        paid_receipt_claim={
            "schema_version": "tokenshare.paid_execution_receipt_claim.v1",
            "receipt_scope": "epd027_full_bank_acquisition",
            "receipt_digest": manifest.created_by_paid_receipt_digest,
            "manifest_digest": manifest.manifest_digest,
        },
    )
    paid = formal_runner._evaluate_trace_root_evidence(
        adapter_result=result,
        adapter_root=Path(result.output_root),
        trace_runtime=paid_context,
    )
    assert missing_receipt.paper_eligible is False
    assert "paid_full_acquisition_receipt_required" in (
        missing_receipt.ineligibility_reasons
    )
    assert paid.paper_eligible is True
    facts = captured_facts[-1]
    planned_ids = tuple(
        binding["planned_ai_unit_id"] for binding in facts.executed_unit_bindings
    )
    unit_ids = tuple(
        binding["unit_id"] for binding in facts.executed_unit_bindings
    )
    assert facts.executed_ai_unit_count == len(set(planned_ids)) == len(planned_ids)
    assert len(set(unit_ids)) == len(unit_ids)
    assert len(facts.current_lifecycle_refs) == len(unit_ids)
    rejected_planned_id = next(
        attempt.planned_ai_unit_id
        for attempt in result.attempt_results
        if attempt.attempt_status is PaperAttemptStatus.CHECKER_REJECTED
    )
    rejected_source_binding = next(
        binding
        for binding in facts.trace_source_bindings
        if binding["planned_ai_unit_id"] == rejected_planned_id
    )
    assert [
        replacement["replacement_slot"]
        for replacement in rejected_source_binding["replacements"]
    ] == [0, 1]
    assert len({
        replacement["entry_id"]
        for replacement in rejected_source_binding["replacements"]
    }) == 2
