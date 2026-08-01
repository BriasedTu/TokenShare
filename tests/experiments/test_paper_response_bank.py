from dataclasses import replace

import pytest

from tokenshare.executors.ai_api_request_identity import (
    PreparedOutboundRequestFactory,
)
from tokenshare.executors.response_bank import (
    ResponseBankInventoryRow,
    inventory_entry_id,
    semantic_slot_key,
)
from tokenshare.experiments.paper_budget import build_response_bank_budget_report
from tokenshare.experiments.paper_models import PaperBudgetResult, PaperStatus, digest_json
from tokenshare.experiments.paper_response_bank import (
    SemanticSlotCandidate,
    build_semantic_inventory,
    preflight_inventory_before_coordinator,
    replacement_slots_for,
)


def _prepared(
    *,
    case_id: str = "case-a",
    unit_id: str = "unit-0",
    sample: int = 0,
    replacement_slot: int = 0,
    body_marker: str = "same",
    provider_digest: str = "sha256:" + "1" * 64,
    plugin_version: str = "factorization.v1",
):
    return PreparedOutboundRequestFactory.prepare(
        body_obj={
            "model": "deepseek-v4-pro",
            "messages": [{"role": "user", "content": body_marker}],
            "max_tokens": 300000,
        },
        base_url="https://api.deepseek.com",
        endpoint="/chat/completions",
        provider_config_digest=provider_digest,
        entry_id="deepseek_v4_pro_exp1_baseline",
        configured_model="deepseek-v4-pro",
        effective_controls_digest="sha256:" + "2" * 64,
        plugin_id="factorization",
        plugin_version=plugin_version,
        prompt_profile_id="factorization.range_search.v2",
        prompt_serialization_schema="tokenshare.prompt.v2",
        body_serialization_schema="openai_chat_completions.v1",
        case_id=case_id,
        planned_ai_unit_id=unit_id,
        sample_slot_index=sample,
        replacement_slot=replacement_slot,
    )


def _candidate(
    *,
    condition_id: str = "condition-a",
    worker_count: int = 1,
    experiment_id: str = "exp2_real_ai_scalability",
    fault_type: str = "none",
    ablation_mode: str = "FULL",
    case_digest: str = "sha256:" + "3" * 64,
    unit_id: str = "unit-0",
    sample: int = 0,
    repeat_id: int | None = None,
    replacement_slot: int = 0,
    body_marker: str = "same",
    prompt_digest: str = "sha256:" + "4" * 64,
    provider_digest: str = "sha256:" + "1" * 64,
    plugin_version: str = "factorization.v1",
    terminal_kind: str | None = None,
) -> SemanticSlotCandidate:
    return SemanticSlotCandidate(
        experiment_id=experiment_id,
        condition_id=condition_id,
        condition_digest=digest_json({"condition_id": condition_id}),
        worker_count=worker_count,
        repeat_id=sample if repeat_id is None else repeat_id,
        fault_type=fault_type,
        ablation_mode=ablation_mode,
        case_id="case-a",
        case_record_digest=case_digest,
        planned_ai_unit_id=unit_id,
        sample_slot_index=sample,
        replacement_slot=replacement_slot,
        prompt_profile_digest=prompt_digest,
        prepared_request=_prepared(
            unit_id=unit_id,
            sample=sample,
            replacement_slot=replacement_slot,
            body_marker=body_marker,
            provider_digest=provider_digest,
            plugin_version=plugin_version,
        ),
        terminal_kind=terminal_kind,
    )


def _exp2_online_conditions() -> tuple[dict, ...]:
    cases = (
        ("early", "factor_v2_hard_034", 0),
        ("middle", "factor_v2_hard_122", 9),
        ("late", "factor_v2_hard_063", 1),
        ("no_factor", "factor_v2_hard_161", 44),
    )
    result = []
    for worker in (1, 3, 7, 10, 30, 50):
        for position, case_id, selection_index in cases:
            body = {
                "condition_id": f"epd027_exp2_online_w{worker}_{position}_r0",
                "experiment_id": "exp2_online_concurrency_check",
                "evidence_class": "online_real_provider",
                "worker_count": worker,
                "case_position": position,
                "case_id": case_id,
                "paper_difficulty": "hard",
                "active_selection_index": selection_index,
                "repeat_id": 0,
                "split_profile_id": "factorization.exp2_contiguous_20way.v1",
                "planned_first_ai_units": 20,
                "provider_calls_upper": 20,
                "max_concurrent_roots": 1,
            }
            result.append({**body, "condition_digest": digest_json(body)})
    return tuple(result)


def test_every_inventory_row_has_unique_inventory_entry_id_and_semantic_slot_key() -> None:
    plan = build_semantic_inventory(
        (_candidate(), _candidate(unit_id="unit-1", body_marker="unit-1"))
    )
    assert len({row.inventory_entry_id for row in plan.rows}) == len(plan.rows)
    assert len({row.semantic_slot_key for row in plan.rows}) == len(plan.rows)


def test_planner_computes_inventory_entry_id_from_canonical_row_excluding_id() -> None:
    row = build_semantic_inventory((_candidate(),)).rows[0]
    assert row.inventory_entry_id == inventory_entry_id(row)
    body = row.to_dict()
    body["inventory_entry_id"] = "ignored-by-preimage"
    assert inventory_entry_id(body) == row.inventory_entry_id


def test_planner_is_stable_for_identical_rows_and_rejects_tampered_precomputed_id() -> None:
    candidate = _candidate()
    first = build_semantic_inventory((candidate,))
    second = build_semantic_inventory((candidate,))
    assert first.rows == second.rows
    with pytest.raises(ValueError, match="precomputed inventory_entry_id"):
        build_semantic_inventory(
            (replace(candidate, precomputed_inventory_entry_id="sha256:" + "f" * 64),)
        )


def test_semantic_slot_key_binds_all_frozen_slot_axes_and_admission_digest() -> None:
    base = _candidate()
    base_key = build_semantic_inventory((base,)).rows[0].semantic_slot_key
    variants = (
        _candidate(case_digest="sha256:" + "5" * 64),
        _candidate(unit_id="unit-1", body_marker="unit-1"),
        _candidate(sample=1),
        _candidate(provider_digest="sha256:" + "6" * 64),
        _candidate(prompt_digest="sha256:" + "7" * 64),
        _candidate(plugin_version="factorization.v2"),
    )
    assert all(
        build_semantic_inventory((item,)).rows[0].semantic_slot_key != base_key
        for item in variants
    )
    replacement_variant = semantic_slot_key(
        case_record_digest=base.case_record_digest,
        planned_ai_unit_id=base.planned_ai_unit_id,
        sample_slot_index=base.sample_slot_index,
        replacement_slot=1,
        provider_config_digest=base.prepared_request.provider_config_digest,
        prompt_profile_digest=base.prompt_profile_digest,
        prompt_admission_profile_digest=(
            base.prepared_request.prompt_admission_profile_digest
        ),
        plugin_version=base.prepared_request.plugin_version,
    )
    assert replacement_variant != base_key
    admission_variant = semantic_slot_key(
        case_record_digest=base.case_record_digest,
        planned_ai_unit_id=base.planned_ai_unit_id,
        sample_slot_index=base.sample_slot_index,
        replacement_slot=base.replacement_slot,
        provider_config_digest=base.prepared_request.provider_config_digest,
        prompt_profile_digest=base.prompt_profile_digest,
        prompt_admission_profile_digest="sha256:" + "8" * 64,
        plugin_version=base.prepared_request.plugin_version,
    )
    assert admission_variant != base_key
    condition_only = replace(base, condition_id="condition-b", worker_count=50)
    assert (
        build_semantic_inventory((condition_only,)).rows[0].semantic_slot_key
        == base_key
    )


def test_exp2_conditions_share_case_repeat_unit_slot_and_repeats_do_not() -> None:
    plan = build_semantic_inventory(
        (
            _candidate(condition_id="w1-r0", worker_count=1),
            _candidate(condition_id="w50-r0", worker_count=50),
            _candidate(condition_id="w1-r1", worker_count=1, sample=1),
        )
    )
    refs = {item["condition_id"]: item for item in plan.condition_refs}
    assert refs["w1-r0"]["semantic_slot_keys"] == refs["w50-r0"]["semantic_slot_keys"]
    assert refs["w1-r1"]["semantic_slot_keys"] != refs["w1-r0"]["semantic_slot_keys"]
    assert len(plan.rows) == 2


def test_same_repeat_cannot_map_to_multiple_sample_slots() -> None:
    with pytest.raises(ValueError, match="same repeat.*multiple sample"):
        build_semantic_inventory(
            (
                _candidate(condition_id="w1-r0", sample=0, repeat_id=0),
                _candidate(condition_id="w50-r0", sample=1, repeat_id=0),
            )
        )


def test_different_repeats_cannot_share_one_sample_slot() -> None:
    with pytest.raises(ValueError, match="different repeats.*same sample"):
        build_semantic_inventory(
            (
                _candidate(condition_id="w1-r0", sample=0, repeat_id=0),
                _candidate(condition_id="w1-r1", sample=0, repeat_id=1),
            )
        )


def test_repeat_sample_mapping_is_one_to_one_across_replacement_slots() -> None:
    def cross_replacement_candidates(
        *, first_repeat: int, first_sample: int, second_repeat: int, second_sample: int
    ) -> tuple[SemanticSlotCandidate, ...]:
        shared = {
            "experiment_id": "exp4_real_ai_protocol_ablation",
            "ablation_mode": "FULL",
        }
        return (
            _candidate(
                **shared,
                condition_id="first",
                repeat_id=first_repeat,
                sample=first_sample,
                replacement_slot=0,
            ),
            _candidate(
                **shared,
                condition_id="first",
                repeat_id=first_repeat,
                sample=first_sample,
                replacement_slot=1,
                provider_digest="sha256:" + "8" * 64,
            ),
            _candidate(
                **shared,
                condition_id="second",
                repeat_id=second_repeat,
                sample=second_sample,
                replacement_slot=1,
            ),
            _candidate(
                **shared,
                condition_id="second",
                repeat_id=second_repeat,
                sample=second_sample,
                replacement_slot=0,
                provider_digest="sha256:" + "9" * 64,
            ),
        )

    negative_cases = (
        (
            "same repeat cannot map to multiple sample slots",
            cross_replacement_candidates(
                first_repeat=0,
                first_sample=0,
                second_repeat=0,
                second_sample=1,
            ),
        ),
        (
            "different repeats cannot share the same sample slot",
            cross_replacement_candidates(
                first_repeat=0,
                first_sample=0,
                second_repeat=1,
                second_sample=0,
            ),
        ),
    )
    for error, candidates in negative_cases:
        with pytest.raises(ValueError, match=error):
            build_semantic_inventory(candidates)

    allowed = build_semantic_inventory(
        tuple(
            _candidate(
                experiment_id="exp4_real_ai_protocol_ablation",
                condition_id="full-r0",
                ablation_mode="FULL",
                repeat_id=0,
                sample=0,
                replacement_slot=slot,
            )
            for slot in (0, 1)
        )
    )
    assert {row.replacement_slot for row in allowed.rows} == {0, 1}


def test_exp2_full_24_condition_sequence_and_max_concurrent_roots_one() -> None:
    conditions = _exp2_online_conditions()
    plan = build_semantic_inventory(
        (_candidate(),),
        exp2_online_conditions=conditions,
        max_concurrent_roots=1,
    )
    assert [item["condition_id"] for item in plan.exp2_online_condition_refs] == [
        item["condition_id"] for item in conditions
    ]
    assert len(plan.exp2_online_condition_refs) == 24
    assert plan.max_concurrent_roots == 1
    drifted = list(conditions)
    drifted[0] = {**drifted[0], "condition_id": "drifted-condition-id"}
    with pytest.raises(ValueError, match="condition digest"):
        build_semantic_inventory(
            (_candidate(),),
            exp2_online_conditions=drifted,
            max_concurrent_roots=1,
        )
    with pytest.raises(ValueError, match="max_concurrent_roots"):
        build_semantic_inventory(
            (_candidate(),),
            exp2_online_conditions=conditions,
            max_concurrent_roots=2,
        )


def test_exp3_rate_slots_zero_to_two_and_death_slots_zero_to_four() -> None:
    assert replacement_slots_for(
        experiment_id="exp3_real_ai_fault_recovery",
        fault_type="false_positive",
        ablation_mode="FULL",
    ) == (0, 1, 2)
    assert replacement_slots_for(
        experiment_id="exp3_real_ai_fault_recovery",
        fault_type="worker_death",
        ablation_mode="FULL",
    ) == (0, 1, 2, 3, 4)
    rate = build_semantic_inventory(
        tuple(
            _candidate(
                experiment_id="exp3_real_ai_fault_recovery",
                condition_id="exp3-rate",
                fault_type="false_positive",
                replacement_slot=slot,
                body_marker=f"rate-{slot}",
            )
            for slot in range(3)
        )
    )
    death = build_semantic_inventory(
        tuple(
            _candidate(
                experiment_id="exp3_real_ai_fault_recovery",
                condition_id="exp3-death",
                fault_type="worker_death",
                replacement_slot=slot,
                body_marker=f"death-{slot}",
            )
            for slot in range(5)
        )
    )
    assert {row.replacement_slot for row in rate.rows} == set(range(3))
    assert {row.replacement_slot for row in death.rows} == set(range(5))
    with pytest.raises(ValueError, match="incomplete replacement slots"):
        build_semantic_inventory(
            tuple(
                _candidate(
                    experiment_id="exp3_real_ai_fault_recovery",
                    condition_id="exp3-death",
                    fault_type="worker_death",
                    replacement_slot=slot,
                    body_marker=f"death-{slot}",
                )
                for slot in range(4)
            )
        )


def test_exp4_full_and_ablations_share_zero_to_one() -> None:
    modes = (
        "FULL",
        "NO_VERIFICATION",
        "NO_PARSER_POLICY",
        "NO_REQUEUE",
        "NO_MERGE_GATE",
    )
    for mode in modes:
        assert replacement_slots_for(
            experiment_id="exp4_real_ai_protocol_ablation",
            fault_type="none",
            ablation_mode=mode,
        ) == (0, 1)
    plan = build_semantic_inventory(
        tuple(
            _candidate(
                experiment_id="exp4_real_ai_protocol_ablation",
                condition_id=f"exp4-{mode}",
                ablation_mode=mode,
                replacement_slot=slot,
                body_marker=f"shared-{slot}",
            )
            for mode in modes
            for slot in (0, 1)
        )
    )
    assert len(plan.rows) == 2
    refs = {item["condition_id"]: item for item in plan.condition_refs}
    assert len({tuple(item["semantic_slot_keys"]) for item in refs.values()}) == 1


def test_same_semantic_slot_cannot_preregister_two_inference_digests() -> None:
    with pytest.raises(ValueError, match="one semantic slot"):
        build_semantic_inventory(
            (_candidate(body_marker="first"), _candidate(body_marker="second"))
        )


def test_early_stop_does_not_prune_inventory() -> None:
    candidates = tuple(
        _candidate(unit_id=f"unit-{index}", body_marker=f"unit-{index}")
        for index in range(20)
    )
    full = build_semantic_inventory(candidates)
    stopped = build_semantic_inventory(
        candidates,
        observed_early_stop_slot_keys=(full.rows[0].semantic_slot_key,),
    )
    assert stopped.rows == full.rows
    assert stopped.expected_slot_count == 20


def test_missing_slot_writes_preflight_blocked_record_and_zero_protocol_engine_events() -> None:
    plan = build_semantic_inventory(
        (_candidate(), _candidate(unit_id="unit-1", body_marker="unit-1"))
    )
    calls = []
    result = preflight_inventory_before_coordinator(
        plan=plan,
        available_inventory_entry_ids=(plan.rows[0].inventory_entry_id,),
        coordinator_factory=lambda: calls.append("constructed"),
    )
    assert result.status == "blocked"
    assert len(result.blocked_records) == 1
    assert result.blocked_records[0].schema_version == "tokenshare.response_bank_preflight_blocked.v1"
    assert result.protocol_engine_event_count == 0
    assert result.provider_call_count == 0
    assert result.coordinator_constructed is False
    assert calls == []


def test_terminal_provider_failure_counts_present() -> None:
    plan = build_semantic_inventory(
        (
            _candidate(),
            _candidate(
                unit_id="unit-1",
                body_marker="unit-1",
                terminal_kind="provider_failure",
            ),
        )
    )
    budget = PaperBudgetResult(
        budget_digest="sha256:" + "a" * 64,
        planned_experiments=["exp2_real_ai_scalability"],
        planned_conditions=1,
        planned_root_runs=1,
        planned_ai_units=2,
        max_provider_attempts=4,
        token_upper_bound=1200,
        cost_upper_bound=2.5,
        wall_clock_estimate=1.0,
        quota_preflight={"provider_calls_made": 0},
        rate_limit_preflight={"status": "not_checked"},
        disk_estimate={"bytes": 4096},
        status=PaperStatus.PLANNED,
    )
    report = build_response_bank_budget_report(
        budget=budget,
        inventory_plan=plan,
        model_ids=("deepseek-v4-pro",),
    )
    assert report == {
        "schema_version": "tokenshare.response_bank_budget_report.v1",
        "planned_root_runs": 1,
        "planned_first_attempt_ai_units": 2,
        "semantic_slot_count": 2,
        "model_ids": ["deepseek-v4-pro"],
        "max_concurrent_roots": 1,
        "provider_calls_upper": 2,
        "token_upper_bound": 600,
        "cost_upper_bound_cny": 1.25,
        "disk_estimate": {"bytes": 4096},
        "terminal_provider_failure_count": 1,
        "terminal_success_count": 0,
        "terminal_unacquired_count": 1,
    }


def test_conflicting_terminal_kinds_fail_closed_independent_of_input_order() -> None:
    terminal_pairs = (
        (None, "success"),
        (None, "provider_failure"),
        ("success", "provider_failure"),
    )
    for first_kind, second_kind in terminal_pairs:
        first = _candidate(terminal_kind=first_kind)
        second = _candidate(terminal_kind=second_kind)
        for ordered in ((first, second), (second, first)):
            with pytest.raises(ValueError, match="conflicting terminal_kind"):
                build_semantic_inventory(ordered)
