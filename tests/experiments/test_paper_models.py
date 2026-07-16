import pytest

from tokenshare.experiments.paper_models import (
    PaperAttemptResult,
    PaperAttemptStatus,
    PaperBudgetResult,
    PaperConditionResult,
    PaperExperimentCondition,
    PaperExperimentResult,
    PaperRunResult,
    PaperStatus,
    PaperSuiteResult,
    PaperTaskResult,
    PaperTaskStatus,
)


def test_paper_condition_digest_is_stable_and_records_required_controls() -> None:
    condition = PaperExperimentCondition(
        experiment_id="exp2_real_ai_scalability",
        condition_id="exp2_factorization_medium_w10_repeat0",
        domain="factorization",
        difficulty="medium",
        worker_count=10,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=42,
        catalog_digest="sha256:" + "1" * 64,
    )
    same_condition = PaperExperimentCondition(
        experiment_id="exp2_real_ai_scalability",
        condition_id="exp2_factorization_medium_w10_repeat0",
        domain="factorization",
        difficulty="medium",
        worker_count=10,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=42,
        catalog_digest="sha256:" + "1" * 64,
    )

    body = condition.to_dict()

    assert body["schema_version"] == "tokenshare.paper_condition.v1"
    assert body["paper_difficulty"] == "medium"
    assert body["real_transport_required"] is True
    assert body["paper_eligible_required"] is True
    assert condition.condition_digest == same_condition.condition_digest


def test_paper_condition_supports_explicit_paper_difficulty_in_digest() -> None:
    simple_condition = PaperExperimentCondition(
        experiment_id="exp1_real_ai_feasibility",
        condition_id="exp1_lean_medium_legacy_simple_repeat0",
        domain="lean_proof",
        difficulty="medium",
        paper_difficulty="simple",
        worker_count=10,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=1,
        catalog_digest="sha256:" + "1" * 64,
    )
    lemma_dag_condition = PaperExperimentCondition(
        experiment_id="exp1_real_ai_feasibility",
        condition_id="exp1_lean_medium_legacy_simple_repeat0",
        domain="lean_proof",
        difficulty="medium",
        paper_difficulty="medium_lemma_dag",
        worker_count=10,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=1,
        catalog_digest="sha256:" + "1" * 64,
    )

    assert simple_condition.to_dict()["paper_difficulty"] == "simple"
    assert lemma_dag_condition.to_dict()["paper_difficulty"] == "medium_lemma_dag"
    assert simple_condition.condition_digest != lemma_dag_condition.condition_digest


def test_paper_condition_roundtrip_records_topic_and_provenance_fields() -> None:
    condition = PaperExperimentCondition(
        experiment_id="exp1_real_ai_feasibility",
        condition_id="exp1_lean_medium_pure_logic_repeat0",
        domain="lean_proof",
        difficulty="medium",
        paper_difficulty="medium_lemma_dag",
        topic_family="pure_logic",
        topic_family_version="v1",
        construction_rule_id="fixed_oracle_lemma_graph.pure_logic.v1",
        oracle_package_group="lean_lemma_graph_oracle.pure_logic.v1",
        proof_assembly_shape="recursive_lemma_dag_required_slots.v1",
        worker_count=10,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=1,
        catalog_digest="sha256:" + "1" * 64,
    )

    body = condition.to_dict()

    assert body["topic_family"] == "pure_logic"
    assert body["topic_family_version"] == "v1"
    assert body["construction_rule_id"] == "fixed_oracle_lemma_graph.pure_logic.v1"
    assert body["oracle_package_group"] == "lean_lemma_graph_oracle.pure_logic.v1"
    assert body["proof_assembly_shape"] == "recursive_lemma_dag_required_slots.v1"


def test_paper_task_result_roundtrip_records_topic_and_provenance_fields() -> None:
    task = PaperTaskResult(
        condition_id="cond1",
        repeat_id=0,
        task_id="lean_v2_medium_lemma_dag_01",
        domain="lean_proof",
        difficulty="medium",
        paper_difficulty="medium_lemma_dag",
        topic_family="pure_logic",
        topic_family_version="v1",
        construction_rule_id="fixed_oracle_lemma_graph.pure_logic.v1",
        oracle_package_group="lean_lemma_graph_oracle.pure_logic.v1",
        proof_assembly_shape="recursive_lemma_dag_required_slots.v1",
        root_status=PaperTaskStatus.BLOCKED,
        accepted_validity=None,
        failure_stage=None,
        failure_kind=None,
        attempt_count=0,
        provider_attempt_count=0,
        wall_clock_ms=0,
        total_tokens=0,
        cost_estimate=0.0,
        event_refs=[],
        artifact_refs=[],
        paper_eligible=False,
    )

    body = task.to_dict()

    assert body["paper_difficulty"] == "medium_lemma_dag"
    assert body["topic_family"] == "pure_logic"
    assert body["topic_family_version"] == "v1"
    assert body["construction_rule_id"] == "fixed_oracle_lemma_graph.pure_logic.v1"
    assert body["oracle_package_group"] == "lean_lemma_graph_oracle.pure_logic.v1"
    assert body["proof_assembly_shape"] == "recursive_lemma_dag_required_slots.v1"


def test_paper_condition_digest_changes_for_topic_family_or_construction_rule() -> None:
    base = {
        "experiment_id": "exp1_real_ai_feasibility",
        "condition_id": "exp1_lean_medium_repeat0",
        "domain": "lean_proof",
        "difficulty": "medium",
        "paper_difficulty": "medium_lemma_dag",
        "topic_family": "pure_logic",
        "topic_family_version": "v1",
        "construction_rule_id": "fixed_oracle_lemma_graph.pure_logic.v1",
        "oracle_package_group": "lean_lemma_graph_oracle.pure_logic.v1",
        "proof_assembly_shape": "recursive_lemma_dag_required_slots.v1",
        "worker_count": 10,
        "fault_type": "none",
        "fault_rate": 0.0,
        "ablation_mode": "FULL",
        "model_policy": "fixed_entry",
        "repeat_id": 0,
        "seed": 1,
        "catalog_digest": "sha256:" + "1" * 64,
    }

    first = PaperExperimentCondition(**base)
    changed_topic = PaperExperimentCondition(**{**base, "topic_family": "function_set"})
    changed_rule = PaperExperimentCondition(
        **{
            **base,
            "construction_rule_id": "fixed_oracle_lemma_graph.function_set.v1",
        }
    )

    assert first.condition_digest != changed_topic.condition_digest
    assert first.condition_digest != changed_rule.condition_digest


def test_paper_condition_rejects_invalid_lean_topic_family() -> None:
    with pytest.raises(ValueError, match="topic_family"):
        PaperExperimentCondition(
            experiment_id="exp1_real_ai_feasibility",
            condition_id="exp1_lean_algebra_repeat0",
            domain="lean_proof",
            difficulty="medium",
            paper_difficulty="medium_lemma_dag",
            topic_family="algebra",
            worker_count=10,
            fault_type="none",
            fault_rate=0.0,
            ablation_mode="FULL",
            model_policy="fixed_entry",
            repeat_id=0,
            seed=1,
            catalog_digest="sha256:" + "1" * 64,
        )


def test_paper_result_objects_serialize_stable_status_schema_and_evidence_refs() -> None:
    attempt = PaperAttemptResult(
        condition_id="cond1",
        repeat_id=0,
        run_id="run1",
        task_id="task1",
        unit_id="unit1",
        attempt_id="attempt1",
        worker_id="worker1",
        provider_attempt_index=0,
        attempt_status=PaperAttemptStatus.SUCCEEDED,
        provider="siliconflow",
        model="glm",
        entry_id="glm_5_2__sf_key_1",
        request_ref={"artifact_id": "request1"},
        raw_output_ref={"artifact_id": "raw1"},
        parsed_output_ref={"artifact_id": "parsed1"},
        parse_failure_ref=None,
        provenance_ref={"artifact_id": "prov1"},
        usage_ref={"artifact_id": "usage1"},
        started_at="2026-07-14T00:00:00Z",
        ended_at="2026-07-14T00:00:01Z",
        latency_ms=1234,
        prompt_tokens=100,
        completion_tokens=156,
        total_tokens=256,
        cost_estimate=0.001,
        error_kind=None,
        fault_injection_ref=None,
        paper_eligible=True,
    )
    task = PaperTaskResult(
        condition_id="cond1",
        repeat_id=0,
        task_id="task1",
        domain="lean_proof",
        difficulty="easy",
        paper_difficulty="simple",
        root_status=PaperTaskStatus.COMPLETED,
        accepted_validity=True,
        failure_stage=None,
        failure_kind=None,
        attempt_count=1,
        provider_attempt_count=1,
        wall_clock_ms=2000,
        total_tokens=256,
        cost_estimate=0.001,
        event_refs=[{"event_id": "evt1"}],
        artifact_refs=[{"artifact_id": "raw1"}],
        paper_eligible=True,
    )
    run = PaperRunResult(
        condition_id="cond1",
        repeat_id=0,
        run_id="run1",
        status=PaperStatus.COMPLETED,
        run_manifest_ref={"path": "run_manifest.json"},
        per_task_results_ref={"path": "per_task_results.jsonl"},
        per_attempt_results_ref={"path": "per_attempt_results.jsonl"},
        fault_injections_ref={"path": "fault_injections.jsonl"},
        event_log_ref={"path": "events/event_log.jsonl"},
        artifact_root="artifacts",
        paper_eligible=True,
        ineligibility_reasons=[],
    )
    condition = PaperConditionResult(
        condition_id="cond1",
        status=PaperStatus.COMPLETED,
        repeat_count=1,
        task_count=1,
        completed_root_count=1,
        failed_root_count=0,
        blocked_root_count=0,
        provider_attempt_count=1,
        metrics_ref={"path": "metrics/cond1.json"},
    )
    experiment = PaperExperimentResult(
        experiment_id="exp1_real_ai_feasibility",
        status=PaperStatus.COMPLETED,
        condition_ids=["cond1"],
        run_count=1,
        task_count=1,
        completion_rate=1.0,
        accepted_validity_rate=1.0,
        total_tokens=256,
        total_cost_estimate=0.001,
        summary_ref={"path": "summary.json"},
    )
    budget = PaperBudgetResult(
        budget_digest="sha256:" + "2" * 64,
        planned_experiments=["exp1_real_ai_feasibility"],
        planned_conditions=1,
        planned_root_runs=1,
        planned_ai_units=2,
        max_provider_attempts=2,
        token_upper_bound=2048,
        cost_upper_bound=0.1,
        wall_clock_estimate=60.0,
        quota_preflight={"status": "not_checked"},
        rate_limit_preflight={"status": "not_checked"},
        disk_estimate={"bytes": 4096},
        status=PaperStatus.PLANNED,
    )
    suite = PaperSuiteResult(
        suite_id="paper_v1_plan",
        status=PaperStatus.PLANNED,
        output_root="outputs/experiments/paper_v1/paper_v1_plan",
        started_at="2026-07-14T00:00:00Z",
        ended_at="2026-07-14T00:00:01Z",
        experiment_ids=["exp1_real_ai_feasibility"],
        condition_count=1,
        run_count=1,
        task_count=1,
        provider_attempt_count=0,
        total_tokens=0,
        total_cost_estimate=0.0,
        paper_eligible=False,
        eligibility_report_ref={"path": "audit/paper_eligibility_report.json"},
        budget_ref=budget.to_dict(),
        metrics_refs=[],
        audit_refs=[],
        error_summary=[],
    )

    attempt_body = attempt.to_dict()
    assert attempt_body["schema_version"] == "tokenshare.paper_attempt_result.v1"
    assert attempt_body["provider_attempt_index"] == 0
    assert attempt_body["started_at"] == "2026-07-14T00:00:00Z"
    assert attempt_body["ended_at"] == "2026-07-14T00:00:01Z"
    assert attempt_body["prompt_tokens"] == 100
    assert attempt_body["completion_tokens"] == 156
    assert task.to_dict()["paper_difficulty"] == "simple"
    assert task.to_dict()["root_status"] == "completed"
    assert run.to_dict()["status"] == "completed"
    assert condition.to_dict()["provider_attempt_count"] == 1
    assert experiment.to_dict()["accepted_validity_rate"] == 1.0
    assert budget.to_dict()["status"] == "planned"
    assert suite.to_dict()["paper_eligible"] is False


def test_invalid_paper_status_values_are_rejected() -> None:
    with pytest.raises(ValueError, match="status"):
        PaperExperimentResult(
            experiment_id="exp1",
            status="almost_done",
            condition_ids=[],
            run_count=0,
            task_count=0,
            completion_rate=0.0,
            accepted_validity_rate=0.0,
            total_tokens=0,
            total_cost_estimate=0.0,
            summary_ref={},
        )


def test_paper_condition_rejects_unknown_domain_or_difficulty() -> None:
    base = {
        "experiment_id": "exp1_real_ai_feasibility",
        "condition_id": "cond1",
        "domain": "factorization",
        "difficulty": "easy",
        "worker_count": 10,
        "fault_type": "none",
        "fault_rate": 0.0,
        "ablation_mode": "FULL",
        "model_policy": "fixed_entry",
        "repeat_id": 0,
        "seed": 1,
        "catalog_digest": "sha256:" + "1" * 64,
    }

    with pytest.raises(ValueError, match="domain"):
        PaperExperimentCondition(**{**base, "domain": "lean-proof"})
    with pytest.raises(ValueError, match="difficulty"):
        PaperExperimentCondition(**{**base, "difficulty": "all"})
