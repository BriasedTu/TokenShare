import pytest

from tokenshare.experiments.paper_ablation import (
    PaperAblationEvidenceRecord,
    PaperAblationMode,
    PaperAblationProfile,
    ablation_profile_for_mode,
    summarize_ablation_evidence,
    validate_ablation_attempt_coverage,
)
from tokenshare.experiments.paper_budget import plan_paper_suite
from tokenshare.experiments.paper_catalog import load_paper_catalogs
from tokenshare.experiments.paper_runner import expand_plan_conditions
from tokenshare.experiments.paper_workers import (
    PaperAIUnit,
    validate_ai_unit_dependency_graph,
)


def test_exp4_condition_expansion_includes_all_protocol_ablation_modes() -> None:
    catalog = load_paper_catalogs(
        factorization_path="benchmarks/paper/factorization_catalog.v1.jsonl",
        lean_path="benchmarks/paper/lean_catalog.v1.jsonl",
    )

    conditions = expand_plan_conditions(
        catalog_manifest=catalog,
        experiment_ids=("exp4_real_ai_protocol_ablation",),
        worker_levels=(10,),
        repeats=1,
        seed_family=(9,),
    )

    assert len(conditions) == 36
    assert {condition.ablation_mode for condition in conditions} == {
        PaperAblationMode.FULL.value,
        PaperAblationMode.NO_PARSER_POLICY.value,
        PaperAblationMode.NO_VERIFICATION.value,
        PaperAblationMode.NO_REQUEUE.value,
        PaperAblationMode.NO_MERGE_GATE.value,
        PaperAblationMode.NO_SLOT_INTEGRITY.value,
    }
    assert {condition.domain for condition in conditions} == {
        "factorization",
        "lean_proof",
    }
    assert {condition.difficulty for condition in conditions} == {
        "easy",
        "medium",
        "hard",
    }
    assert all(condition.worker_count == 10 for condition in conditions)
    assert all(condition.fault_type == "none" for condition in conditions)
    assert all(condition.real_transport_required for condition in conditions)
    assert all(
        condition.paper_eligible_required
        for condition in conditions
        if condition.domain != "lean_proof" or condition.difficulty == "easy"
    )
    assert all(
        not condition.paper_eligible_required
        for condition in conditions
        if condition.domain == "lean_proof" and condition.difficulty in {"medium", "hard"}
    )

    budget = plan_paper_suite(
        catalog_manifest=catalog,
        conditions=conditions,
        max_provider_attempts_per_ai_unit=1,
        token_upper_bound_per_provider_attempt=100,
        cost_upper_bound_per_provider_attempt=0.01,
        plan_only=True,
    )
    assert budget.planned_root_runs == 180


@pytest.mark.parametrize("mode", list(PaperAblationMode))
def test_ablation_profiles_are_experiment_boundary_only_and_still_require_real_ai(
    mode: PaperAblationMode,
) -> None:
    profile = ablation_profile_for_mode(mode)
    body = profile.to_dict()

    assert body["mode"] == mode.value
    assert body["scope"] == "experiment_boundary"
    assert body["requires_provider_attempt_before_ablation"] is True
    assert body["system_default_behavior_changed"] is False
    if mode == PaperAblationMode.FULL:
        assert body["disabled_mechanisms"] == []
    else:
        assert body["disabled_mechanisms"]


def test_ablation_profile_rejects_safety_invariant_overrides() -> None:
    with pytest.raises(ValueError, match="requires_provider_attempt_before_ablation"):
        PaperAblationProfile(
            mode=PaperAblationMode.NO_VERIFICATION,
            disabled_mechanisms=("verification",),
            expected_risk_flags=("canonical_pollution",),
            requires_provider_attempt_before_ablation=False,
        )
    with pytest.raises(ValueError, match="system_default_behavior_changed"):
        PaperAblationProfile(
            mode=PaperAblationMode.NO_VERIFICATION,
            disabled_mechanisms=("verification",),
            expected_risk_flags=("canonical_pollution",),
            system_default_behavior_changed=True,
        )


def test_ablation_attempt_coverage_uses_generic_ai_unit_dependency_graph() -> None:
    graph = validate_ai_unit_dependency_graph(_multi_level_ai_units())
    attempts = [
        _attempt(unit_id="unit_leaf_alpha", attempt_id="attempt_alpha"),
        _attempt(unit_id="unit_leaf_beta", attempt_id="attempt_beta"),
        _attempt(unit_id="unit_leaf_gamma", attempt_id="attempt_gamma"),
        _attempt(unit_id="unit_lemma_join", attempt_id="attempt_join"),
        _attempt(unit_id="unit_independent_leaf", attempt_id="attempt_leaf"),
        _attempt(unit_id="unit_root_assembly", attempt_id="attempt_root"),
    ]

    coverage = validate_ablation_attempt_coverage(
        dependency_graph=graph,
        attempts=attempts,
        artifact_inventory=_artifact_inventory_for_attempts(attempts),
    )

    body = coverage.to_dict()
    assert body["can_apply_ablation"] is True
    assert body["expected_ai_unit_count"] == 6
    assert body["covered_ai_unit_count"] == 6
    assert body["missing_unit_ids"] == []
    assert body["dependency_graph_digest"] == graph["graph_digest"]

    missing = validate_ablation_attempt_coverage(
        dependency_graph=graph,
        attempts=attempts[:-1],
        artifact_inventory=_artifact_inventory_for_attempts(attempts[:-1]),
    ).to_dict()
    assert missing["can_apply_ablation"] is False
    assert missing["missing_unit_ids"] == ["unit_root_assembly"]
    assert "missing_provider_attempt:unit_root_assembly" in missing["reasons"]


def test_ablation_attempt_coverage_rejects_fabricated_artifact_id_only_refs() -> None:
    graph = validate_ai_unit_dependency_graph(
        [
            PaperAIUnit(
                task_id="task_graph",
                unit_id="unit_target",
                unit_kind="generic_ai_unit",
            )
        ]
    )

    coverage = validate_ablation_attempt_coverage(
        dependency_graph=graph,
        attempts=[
            {
                "unit_id": "unit_target",
                "attempt_id": "attempt_target",
                "provider_attempt_index": 0,
                "raw_output_ref": {"artifact_id": "raw_attempt_target"},
                "provenance_ref": {"artifact_id": "prov_attempt_target"},
                "usage_ref": {"artifact_id": "usage_attempt_target"},
            }
        ],
        artifact_inventory=[],
    ).to_dict()

    assert coverage["can_apply_ablation"] is False
    assert "missing_provider_attempt:unit_target" in coverage["reasons"]
    assert any(
        "missing_request_ref" in reason or "unverified_raw_output_ref" in reason
        for reason in coverage["reasons"]
    )


def test_ablation_attempt_coverage_rejects_tampered_dependency_graph_digest() -> None:
    graph = validate_ai_unit_dependency_graph(
        [
            PaperAIUnit(
                task_id="task_graph",
                unit_id="unit_target",
                unit_kind="generic_ai_unit",
            )
        ]
    )
    graph["graph_digest"] = "sha256:" + "0" * 64

    with pytest.raises(ValueError, match="graph_digest"):
        validate_ablation_attempt_coverage(
            dependency_graph=graph,
            attempts=[],
            artifact_inventory=[],
        )


def test_ablation_summary_counts_evidence_derived_failure_modes_for_generic_units() -> None:
    records = [
        PaperAblationEvidenceRecord(
            condition_id="cond_no_verification",
            repeat_id=0,
            run_id="run1",
            task_id="task_graph",
            unit_id="unit_lemma_join",
            attempt_id="attempt_join",
            ablation_mode=PaperAblationMode.NO_VERIFICATION,
            dependency_path=("unit_leaf_alpha", "unit_lemma_join"),
            disabled_mechanisms=("verification",),
            canonical_pollution=True,
            premature_merge=False,
            blocked_root=False,
            settlement_blocked=False,
            slot_integrity_violation=False,
            event_refs=[_event_ref("evt_bad_canonical")],
            artifact_refs=[_artifact_ref("artifact_bad_canonical", "AblationAudit")],
        ),
        PaperAblationEvidenceRecord(
            condition_id="cond_no_merge_gate",
            repeat_id=0,
            run_id="run1",
            task_id="task_graph",
            unit_id="unit_root_assembly",
            attempt_id="attempt_root",
            ablation_mode=PaperAblationMode.NO_MERGE_GATE,
            dependency_path=("unit_lemma_join", "unit_root_assembly"),
            disabled_mechanisms=("merge_gate",),
            canonical_pollution=False,
            premature_merge=True,
            blocked_root=False,
            settlement_blocked=False,
            slot_integrity_violation=False,
            event_refs=[_event_ref("evt_premature_merge")],
            artifact_refs=[_artifact_ref("artifact_merge_summary", "MergeAudit")],
        ),
        PaperAblationEvidenceRecord(
            condition_id="cond_no_requeue",
            repeat_id=0,
            run_id="run1",
            task_id="task_graph",
            unit_id="unit_leaf_beta",
            attempt_id="attempt_beta",
            ablation_mode=PaperAblationMode.NO_REQUEUE,
            dependency_path=("unit_leaf_beta",),
            disabled_mechanisms=("requeue",),
            canonical_pollution=False,
            premature_merge=False,
            blocked_root=True,
            settlement_blocked=False,
            slot_integrity_violation=False,
            event_refs=[_event_ref("evt_no_requeue")],
            artifact_refs=[_artifact_ref("artifact_worker_death", "WorkerDeathRecord")],
        ),
        PaperAblationEvidenceRecord(
            condition_id="cond_no_slot_integrity",
            repeat_id=0,
            run_id="run1",
            task_id="task_graph",
            unit_id="unit_root_assembly",
            attempt_id="attempt_root_slot",
            ablation_mode=PaperAblationMode.NO_SLOT_INTEGRITY,
            dependency_path=("unit_lemma_join", "unit_root_assembly"),
            disabled_mechanisms=("slot_integrity",),
            canonical_pollution=False,
            premature_merge=False,
            blocked_root=False,
            settlement_blocked=True,
            slot_integrity_violation=True,
            event_refs=[_event_ref("evt_slot_mismatch")],
            artifact_refs=[_artifact_ref("artifact_slot_audit", "SlotIntegrityAudit")],
        ),
    ]

    summary = summarize_ablation_evidence(records).to_dict()

    assert summary["record_count"] == 4
    assert summary["canonical_pollution_count"] == 1
    assert summary["premature_merge_count"] == 1
    assert summary["blocked_root_count"] == 1
    assert summary["settlement_blocked_count"] == 1
    assert summary["slot_integrity_violation_count"] == 1
    assert summary["affected_unit_ids"] == [
        "unit_leaf_beta",
        "unit_lemma_join",
        "unit_root_assembly",
    ]
    assert summary["by_ablation_mode"][PaperAblationMode.NO_VERIFICATION.value][
        "canonical_pollution_count"
    ] == 1


def test_ablation_evidence_rejects_metric_flags_without_refs() -> None:
    with pytest.raises(ValueError, match="event_refs"):
        PaperAblationEvidenceRecord(
            condition_id="cond_bad",
            repeat_id=0,
            run_id="run1",
            task_id="task_graph",
            unit_id="unit_root_assembly",
            attempt_id="attempt_root",
            ablation_mode=PaperAblationMode.NO_MERGE_GATE,
            dependency_path=("unit_lemma_join", "unit_root_assembly"),
            disabled_mechanisms=("merge_gate",),
            canonical_pollution=False,
            premature_merge=True,
            blocked_root=False,
            settlement_blocked=False,
            slot_integrity_violation=False,
            event_refs=[],
            artifact_refs=[_artifact_ref("artifact_merge_summary", "MergeAudit")],
        )


def test_ablation_evidence_rejects_clean_record_without_refs() -> None:
    with pytest.raises(ValueError, match="event_refs"):
        PaperAblationEvidenceRecord(
            condition_id="cond_bad",
            repeat_id=0,
            run_id="run1",
            task_id="task_graph",
            unit_id="unit_root_assembly",
            attempt_id="attempt_root",
            ablation_mode=PaperAblationMode.FULL,
            dependency_path=("unit_root_assembly",),
            disabled_mechanisms=(),
            canonical_pollution=False,
            premature_merge=False,
            blocked_root=False,
            settlement_blocked=False,
            slot_integrity_violation=False,
            event_refs=[],
            artifact_refs=[],
        )


def test_ablation_evidence_rejects_non_boolean_metric_flags() -> None:
    with pytest.raises(ValueError, match="canonical_pollution"):
        summarize_ablation_evidence(
            [
                {
                    "condition_id": "cond_bad",
                    "repeat_id": 0,
                    "run_id": "run1",
                    "task_id": "task_graph",
                    "unit_id": "unit_root_assembly",
                    "attempt_id": "attempt_root",
                    "ablation_mode": PaperAblationMode.FULL.value,
                    "dependency_path": ["unit_root_assembly"],
                    "disabled_mechanisms": [],
                    "canonical_pollution": "false",
                    "premature_merge": False,
                    "blocked_root": False,
                    "settlement_blocked": False,
                    "slot_integrity_violation": False,
                    "event_refs": [_event_ref("evt_clean")],
                    "artifact_refs": [_artifact_ref("artifact_clean", "AblationAudit")],
                }
            ]
        )


def _multi_level_ai_units() -> list[PaperAIUnit]:
    return [
        PaperAIUnit(
            task_id="task_graph",
            unit_id="unit_leaf_alpha",
            unit_kind="generic_ai_unit",
        ),
        PaperAIUnit(
            task_id="task_graph",
            unit_id="unit_leaf_beta",
            unit_kind="generic_ai_unit",
        ),
        PaperAIUnit(
            task_id="task_graph",
            unit_id="unit_leaf_gamma",
            unit_kind="generic_ai_unit",
        ),
        PaperAIUnit(
            task_id="task_graph",
            unit_id="unit_lemma_join",
            unit_kind="generic_ai_unit",
            dependencies=(
                "unit_leaf_alpha",
                "unit_leaf_beta",
                "unit_leaf_gamma",
            ),
            depth=1,
        ),
        PaperAIUnit(
            task_id="task_graph",
            unit_id="unit_independent_leaf",
            unit_kind="generic_ai_unit",
        ),
        PaperAIUnit(
            task_id="task_graph",
            unit_id="unit_root_assembly",
            unit_kind="generic_ai_unit",
            dependencies=("unit_lemma_join", "unit_independent_leaf"),
            depth=2,
        ),
    ]


def _attempt(*, unit_id: str, attempt_id: str) -> dict:
    request_ref = _artifact_ref(f"request_{attempt_id}", "ExecutionRequest", source_kind="paper_runner")
    raw_ref = _artifact_ref(f"raw_{attempt_id}", "RawModelOutput")
    parsed_ref = _artifact_ref(f"parsed_{attempt_id}", "ParsedModelOutput")
    provenance_ref = _artifact_ref(f"provenance_{attempt_id}", "AIProviderCallProvenance")
    usage_ref = _artifact_ref(f"usage_{attempt_id}", "AIUsageSummary", source_kind="paper_runner")
    return {
        "unit_id": unit_id,
        "attempt_id": attempt_id,
        "provider": "siliconflow",
        "model": "glm",
        "entry_id": "glm_5_2__sf_key_1",
        "provider_attempt_index": 0,
        "request_ref": request_ref,
        "raw_output_ref": raw_ref,
        "parsed_output_ref": parsed_ref,
        "parse_failure_ref": None,
        "provenance_ref": provenance_ref,
        "usage_ref": usage_ref,
        "started_at": "2026-07-15T00:00:00Z",
        "ended_at": "2026-07-15T00:00:01Z",
        "latency_ms": 1000,
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "cost_estimate": 0.001,
        "paper_eligible": True,
    }


def _artifact_inventory_for_attempts(attempts: list[dict]) -> list[dict]:
    inventory: list[dict] = []
    for attempt in attempts:
        for field_name in (
            "request_ref",
            "raw_output_ref",
            "parsed_output_ref",
            "provenance_ref",
            "usage_ref",
        ):
            ref = attempt.get(field_name)
            if ref is not None:
                inventory.append(dict(ref))
    return inventory


def _event_ref(event_id: str) -> dict:
    return {
        "event_id": event_id,
        "event_type": "ABLATION_EVIDENCE_RECORDED",
    }


def _artifact_ref(
    artifact_id: str,
    artifact_type: str,
    *,
    source_kind: str = "ai_api_executor",
) -> dict:
    return {
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "content_hash": "sha256:" + (artifact_id.encode("utf-8").hex() + "0" * 64)[:64],
        "artifact_schema_id": f"tokenshare.{artifact_type}",
        "source": {"kind": source_kind},
    }
