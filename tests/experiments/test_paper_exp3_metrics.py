from __future__ import annotations

from decimal import Decimal

from tokenshare.experiments.paper_exp3_metrics import (
    EXP3_TRACE_NUMERIC_FIELDS,
    ONLINE_CURRENT_PROVIDER_ROLES,
    STARTED_REPLACEMENT_ROLES,
    SUCCESSFUL_REPLACEMENT_ROLES,
    TRACE_SOURCE_BANK_ROLES,
    Exp3OnlineRecoveryInput,
    Exp3PersistedObservation,
    Exp3TraceConditionInput,
    project_exp3_online_recovery,
    project_exp3_trace_condition,
)
from tokenshare.experiments.paper_metric_contract import load_paper_metric_contract


def _observation(observation_id: str, **facts: object) -> Exp3PersistedObservation:
    return Exp3PersistedObservation(observation_id=observation_id, facts=facts)


def _trace(
    *observations: Exp3PersistedObservation,
    fault_type: str = "false_positive",
    sample_slot_id: str = "slot-1",
):
    return project_exp3_trace_condition(
        Exp3TraceConditionInput(
            condition_id="condition-1",
            fault_type=fault_type,
            repeat_id=0,
            sample_slot_id=sample_slot_id,
            observations=observations,
        ),
        load_paper_metric_contract(),
    )


def _online(
    *observations: Exp3PersistedObservation,
    recovery_source: str = "validation_replacement",
    current_replacement_attempt_id: str = "attempt-1",
    provider_object_links: tuple[tuple[str, str, str, str, str, str], ...] = (),
):
    return project_exp3_online_recovery(
        Exp3OnlineRecoveryInput(
            recovery_source=recovery_source,
            case_id="case-1",
            repeat_id=0,
            observations=(
                _observation(
                    "recovery-anchor",
                    member_kind="exp3_online_recovery_identity",
                    case_id="case-1",
                    recovery_source=recovery_source,
                    current_replacement_attempt_id=current_replacement_attempt_id,
                    provider_object_links=provider_object_links,
                ),
                *observations,
            ),
        ),
        load_paper_metric_contract(),
    )


def _root(observation_id: str, *, final: bool = True, success: bool = True):
    return _observation(
        observation_id,
        member_kind="preregistered_root",
        final_result_reference_complete=final,
        end_to_end_verified_success=success,
        source_bank_roles=TRACE_SOURCE_BANK_ROLES,
        current_provider_roles=ONLINE_CURRENT_PROVIDER_ROLES,
    )


def _candidate(
    observation_id: str,
    *,
    injection_completed: bool = True,
    reached_verification: bool = True,
    rejected: bool = False,
    escaped: bool = False,
):
    return _observation(
        observation_id,
        member_kind="exp3_controlled_wrong_candidate",
        fault_type="false_positive",
        injection_completed=injection_completed,
        reached_verification=reached_verification,
        independently_known_wrong=True,
        candidate_fault_id=f"fault-{observation_id}",
        injected_fault_id=f"fault-{observation_id}",
        candidate_attempt_id=f"attempt-{observation_id}",
        injection_attempt_id=f"attempt-{observation_id}",
        candidate_id=f"candidate-{observation_id}",
        verifier_rejected_candidate_id=(
            f"candidate-{observation_id}" if rejected else "candidate-not-rejected"
        ),
        canonical_candidate_id=(
            f"candidate-{observation_id}" if escaped else "candidate-not-canonical"
        ),
        root_candidate_id="candidate-not-root",
        source_bank_roles=TRACE_SOURCE_BANK_ROLES,
    )


def _replacement(
    observation_id: str,
    *,
    roles=STARTED_REPLACEMENT_ROLES,
    ordinal: int = 1,
    replacement_attempt_id: str | None = None,
    original_task_unit_id: str = "unit-1",
    original_attempt_id: str = "attempt-0",
    fault_or_death_id: str | None = None,
    new_attempt_fault_or_death_id: str | None = None,
    replacement_worker_id: str = "worker-2",
    recovery_source_kind: str = "validation_replacement",
    qualified: bool = False,
    completed: bool = False,
):
    replacement_id = replacement_attempt_id or f"attempt-{observation_id}"
    trigger_id = fault_or_death_id or f"fault-{replacement_id}"
    return _observation(
        observation_id,
        member_kind="replacement_attempt",
        fault_or_death_id=trigger_id,
        fault_or_death_task_unit_id=original_task_unit_id,
        fault_or_death_attempt_id=original_attempt_id,
        original_task_unit_id=original_task_unit_id,
        replacement_task_unit_id=original_task_unit_id,
        original_attempt_id=original_attempt_id,
        replacement_attempt_id=replacement_id,
        original_attempt_ordinal=0,
        replacement_attempt_ordinal=ordinal,
        new_attempt_fault_or_death_id=(
            new_attempt_fault_or_death_id or trigger_id
        ),
        new_attempt_task_unit_id=original_task_unit_id,
        new_attempt_id=replacement_id,
        new_attempt_ordinal=ordinal,
        original_worker_id="worker-1",
        replacement_worker_id=replacement_worker_id,
        ordered_evidence_roles=roles,
        replacement_result_qualified=qualified,
        original_task_unit_completed=completed,
        replacement_result_task_unit_id="unit-1",
        completed_task_unit_id="unit-1",
        source_bank_roles=TRACE_SOURCE_BANK_ROLES,
        current_provider_roles=ONLINE_CURRENT_PROVIDER_ROLES,
        recovery_source_kind=recovery_source_kind,
    )


def _provider(
    observation_id: str,
    *,
    member_kind: str,
    provider_attempt_id: str,
    response_id: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost: Decimal,
):
    replacement = member_kind == "online_recovery_replacement_attempt"
    return _observation(
        observation_id,
        member_kind=member_kind,
        original_provider_dispatch_succeeded=not replacement,
        replacement_provider_dispatch_succeeded=replacement,
        actual_prompt_tokens=prompt_tokens,
        actual_completion_tokens=completion_tokens,
        actual_total_tokens=prompt_tokens + completion_tokens,
        actual_cost_estimate_cny=cost,
        current_provider_roles=ONLINE_CURRENT_PROVIDER_ROLES,
        case_id="case-1",
        recovery_source="validation_replacement",
        provider_attempt_id=provider_attempt_id,
        current_replacement_attempt_id="attempt-1",
        provider_response_id=response_id,
        raw_or_failure_id=f"raw-{provider_attempt_id}",
        provenance_id=f"provenance-{provider_attempt_id}",
        usage_id=f"usage-{provider_attempt_id}",
        model_record_id=f"model-{provider_attempt_id}",
    )


def _pair(
    *,
    fault_slot: str = "slot-1",
    reference_slot: str = "slot-1",
):
    return _observation(
        "pair-1",
        member_kind="exp3_trace_pair",
        pair_evidence_complete=True,
        fault_sample_slot_id=fault_slot,
        reference_sample_slot_id=reference_slot,
        fault_trace_replay_wall_clock_ms=150,
        reference_trace_replay_wall_clock_ms=100,
        fault_trace_attributed_tokens=40,
        reference_trace_attributed_tokens=25,
        fault_trace_attributed_cost=Decimal("3.5"),
        reference_trace_attributed_cost=Decimal("2.0"),
        source_bank_roles=TRACE_SOURCE_BANK_ROLES,
    )


def test_controlled_wrong_candidate_denominator_is_exact_boundary() -> None:
    row = _trace(
        _root("root-1"),
        _root("root-2", final=False, success=False),
        _candidate("intercepted", rejected=True),
        _candidate("escaped", escaped=True),
        _candidate("not-injected", injection_completed=False, rejected=True),
        _candidate("not-verified", reached_verification=False, escaped=True),
    )

    assert row.require_cell("preregistered_root_count").value == 2
    assert row.require_cell("controlled_wrong_candidate_count").value == 2
    assert row.require_cell("controlled_wrong_candidate_interception_count").value == 1
    assert row.require_cell("controlled_wrong_candidate_interception_rate").value == Decimal("0.5")
    assert row.require_cell("controlled_wrong_candidate_escape_count").value == 1
    assert row.require_cell("controlled_wrong_candidate_escape_rate").value == Decimal("0.5")


def test_reassignment_requires_started_linked_new_attempt() -> None:
    row = _trace(
        _replacement("started"),
        _observation(
            "recovery-plan-only",
            member_kind="recovery_plan",
            original_task_unit_id="unit-1",
            replacement_task_unit_id="unit-1",
        ),
        _replacement("same-ordinal", ordinal=0),
        _replacement(
            "wrong-trigger-backlink",
            new_attempt_fault_or_death_id="fault-unrelated",
        ),
    )

    assert row.require_cell("started_replacement_attempt_count").value == 1
    assert row.require_cell("reassignment_count").value == 1


def test_replacement_success_uses_started_replacements() -> None:
    row = _trace(
        _replacement("one"),
        _replacement("two"),
        _replacement("three"),
        _replacement(
            "one-success",
            roles=SUCCESSFUL_REPLACEMENT_ROLES,
            replacement_attempt_id="attempt-one",
            qualified=True,
            completed=True,
        ),
    )

    assert row.require_cell("started_replacement_attempt_count").value == 3
    assert row.require_cell("successful_replacement_attempt_count").value == 1
    assert row.require_cell("replacement_attempt_success_rate").value == Decimal(1) / Decimal(3)

    orphan = _trace(
        _replacement(
            "orphan-success",
            roles=SUCCESSFUL_REPLACEMENT_ROLES,
            qualified=True,
            completed=True,
        )
    )
    orphan_success = orphan.require_cell("successful_replacement_attempt_count")
    assert orphan_success.value is None and orphan_success.publish_blocked
    assert "orphan_successful_replacement" in (orphan_success.reason or "")
    assert orphan.require_cell("replacement_attempt_success_rate").value is None

    mismatch = _trace(
        _replacement("matching-id", replacement_attempt_id="attempt-shared"),
        _replacement(
            "mismatched-success",
            roles=SUCCESSFUL_REPLACEMENT_ROLES,
            replacement_attempt_id="attempt-shared",
            fault_or_death_id="fault-different",
            qualified=True,
            completed=True,
        ),
    )
    mismatch_success = mismatch.require_cell("successful_replacement_attempt_count")
    assert mismatch_success.value is None and mismatch_success.publish_blocked
    assert "mismatched_successful_replacement" in (mismatch_success.reason or "")

    duplicate = _trace(
        _replacement("started-duplicate", replacement_attempt_id="attempt-duplicate"),
        _replacement(
            "success-duplicate-a",
            roles=SUCCESSFUL_REPLACEMENT_ROLES,
            replacement_attempt_id="attempt-duplicate",
            qualified=True,
            completed=True,
        ),
        _replacement(
            "success-duplicate-b",
            roles=SUCCESSFUL_REPLACEMENT_ROLES,
            replacement_attempt_id="attempt-duplicate",
            qualified=True,
            completed=True,
        ),
    )
    duplicate_success = duplicate.require_cell("successful_replacement_attempt_count")
    assert duplicate_success.value is None and duplicate_success.publish_blocked
    assert "duplicate_successful_replacement" in (duplicate_success.reason or "")


def test_discarded_tokens_require_source_usage_and_canonical_exclusion() -> None:
    valid = _observation(
        "discarded-valid",
        member_kind="exp3_discarded_trace_consumption",
        discarded_after_fault=True,
        canonical_excluded=True,
        source_usage_total_tokens=12,
        source_bank_entry_id="bank-1",
        current_attempt_id="attempt-1",
        fault_or_death_id="fault-1",
        rejection_or_abandonment_id="rejection-1",
        canonical_exclusion_rejection_or_abandonment_id="rejection-1",
        source_bank_roles=TRACE_SOURCE_BANK_ROLES,
    )
    missing_usage = _observation(
        "discarded-missing-usage",
        member_kind="exp3_discarded_trace_consumption",
        discarded_after_fault=True,
        canonical_excluded=True,
        source_bank_entry_id="bank-2",
        current_attempt_id="attempt-2",
        fault_or_death_id="fault-2",
        rejection_or_abandonment_id="rejection-2",
        canonical_exclusion_rejection_or_abandonment_id="rejection-2",
        source_bank_roles=TRACE_SOURCE_BANK_ROLES,
    )
    still_canonical = _observation(
        "discarded-still-canonical",
        member_kind="exp3_discarded_trace_consumption",
        discarded_after_fault=True,
        canonical_excluded=False,
        source_usage_total_tokens=9,
        source_bank_entry_id="bank-3",
        current_attempt_id="attempt-3",
        fault_or_death_id="fault-3",
        rejection_or_abandonment_id="rejection-3",
        canonical_exclusion_rejection_or_abandonment_id="rejection-3",
        source_bank_roles=TRACE_SOURCE_BANK_ROLES,
    )

    assert _trace(valid).require_cell("discarded_trace_tokens").value == 12
    assert _trace(valid, missing_usage).require_cell("discarded_trace_tokens").value is None
    assert _trace(still_canonical).require_cell("discarded_trace_tokens").value is None
    required = (
        "source_bank_entry_id",
        "current_attempt_id",
        "fault_or_death_id",
        "rejection_or_abandonment_id",
    )
    for field in required:
        incomplete = dict(valid.facts)
        incomplete.pop(field)
        cell = _trace(
            Exp3PersistedObservation(
                observation_id=f"discarded-missing-{field}", facts=incomplete
            )
        ).require_cell("discarded_trace_tokens")
        assert cell.value is None and cell.publish_blocked
    bad_backlink = dict(valid.facts)
    bad_backlink["canonical_exclusion_rejection_or_abandonment_id"] = "other"
    backlink_cell = _trace(
        Exp3PersistedObservation(
            observation_id="discarded-bad-exclusion-backlink", facts=bad_backlink
        )
    ).require_cell("discarded_trace_tokens")
    assert backlink_cell.value is None and backlink_cell.publish_blocked


def test_absolute_overheads_are_fault_minus_paired_trace_reference() -> None:
    row = _trace(_pair())

    assert row.require_cell("trace_replay_wall_clock_overhead_ms").value == 50
    assert row.require_cell("trace_attributed_token_overhead").value == 15
    assert row.require_cell("trace_attributed_cost_overhead").value == Decimal("1.5")
    assert _trace(_pair(reference_slot="slot-2")).require_cell(
        "trace_replay_wall_clock_overhead_ms"
    ).value is None


def test_ratio_is_separately_named_audit_only() -> None:
    row = _trace(_pair())

    assert set(row.audit_ratios) == {
        "trace_replay_wall_clock_ratio_audit",
        "trace_attributed_token_ratio_audit",
        "trace_attributed_cost_ratio_audit",
    }
    assert all(name.endswith("_ratio_audit") for name in row.audit_ratios)
    assert not any("overhead_ratio" in name for name in row.metric_ids)


def test_signed_kill_error_per_death_mean_max() -> None:
    row = _trace(
        _observation(
            "death-1",
            member_kind="exp3_worker_death_progress",
            progress_evidence_complete=True,
            actual_kill_progress_ratio=Decimal("0.30"),
            target_kill_progress_ratio=Decimal("0.25"),
            source_bank_roles=TRACE_SOURCE_BANK_ROLES,
        ),
        _observation(
            "death-2",
            member_kind="exp3_worker_death_progress",
            progress_evidence_complete=True,
            actual_kill_progress_ratio=Decimal("0.65"),
            target_kill_progress_ratio=Decimal("0.75"),
            source_bank_roles=TRACE_SOURCE_BANK_ROLES,
        ),
        fault_type="worker_death",
    )

    assert row.require_cell("kill_progress_error_pp").value is None
    assert row.require_cell("kill_progress_error_signed_mean_pp").value == Decimal("-2.5")
    assert row.require_cell("kill_progress_error_signed_max_pp").value == Decimal("5")


def test_result_completeness_worker_death_only() -> None:
    slots = (
        _observation(
            "slot-1",
            member_kind="exp3_required_slot",
            recovered_valid_canonical=True,
            source_bank_roles=TRACE_SOURCE_BANK_ROLES,
        ),
        _observation(
            "slot-2",
            member_kind="exp3_required_slot",
            recovered_valid_canonical=False,
            source_bank_roles=TRACE_SOURCE_BANK_ROLES,
        ),
    )

    assert _trace(*slots).require_cell("result_completeness_rate").value is None
    assert _trace(*slots, fault_type="worker_death").require_cell(
        "result_completeness_rate"
    ).value == Decimal("0.5")


def test_trace_has_no_wasted_actual_tokens() -> None:
    row = _trace(_root("root-1"))

    assert "wasted_actual_tokens" not in EXP3_TRACE_NUMERIC_FIELDS
    assert "wasted_actual_tokens" not in row.metric_ids


def test_online_projector_requires_ordered_fault_or_death_new_attempt_dispatch_provider_chain() -> None:
    complete = _online(
        _replacement(
            "complete",
            roles=SUCCESSFUL_REPLACEMENT_ROLES,
            qualified=True,
            completed=True,
        ),
        current_replacement_attempt_id="attempt-complete",
    )
    out_of_order_roles = list(SUCCESSFUL_REPLACEMENT_ROLES)
    out_of_order_roles[1], out_of_order_roles[2] = (
        out_of_order_roles[2],
        out_of_order_roles[1],
    )
    incomplete = _online(
        _replacement(
            "out-of-order",
            roles=tuple(out_of_order_roles),
            qualified=True,
            completed=True,
        ),
        current_replacement_attempt_id="attempt-out-of-order",
    )
    worker_death = _online(
        _replacement(
            "worker-death",
            roles=SUCCESSFUL_REPLACEMENT_ROLES,
            qualified=True,
            completed=True,
            recovery_source_kind="worker_death_requeue",
        ),
        recovery_source="worker_death_requeue",
        current_replacement_attempt_id="attempt-worker-death",
    )
    orphan_provider = _provider(
        "orphan-provider",
        member_kind="online_recovery_replacement_attempt",
        provider_attempt_id="attempt-orphan",
        response_id="response-orphan",
        prompt_tokens=1,
        completion_tokens=1,
        cost=Decimal("0.1"),
    )
    orphan = _online(orphan_provider)
    empty_resources = _online()

    assert complete.require_cell("replacement_chain_count").value == 1
    assert incomplete.require_cell("replacement_chain_count").value == 0
    assert worker_death.require_cell("worker_death_reassignment_chain_count").value == 1
    assert orphan.require_cell("actual_provider_calls").value is None
    for metric_id in (
        "actual_provider_calls",
        "actual_prompt_tokens",
        "actual_completion_tokens",
        "actual_total_tokens",
        "actual_cost_estimate_cny",
        "wasted_actual_tokens",
    ):
        cell = empty_resources.require_cell(metric_id)
        assert cell.value is None and cell.publish_blocked


def test_online_actual_calls_usage_cost_and_wasted_tokens_use_current_provider_objects() -> None:
    original_base = _provider(
        "provider-original",
        member_kind="online_recovery_original_attempt",
        provider_attempt_id="attempt-0",
        response_id="response-0",
        prompt_tokens=10,
        completion_tokens=20,
        cost=Decimal("1.25"),
    )
    original = Exp3PersistedObservation(
        observation_id=original_base.observation_id,
        facts={
            **original_base.facts,
            "original_task_unit_id": "unit-1",
            "replacement_task_unit_id": "unit-1",
            "original_attempt_id": "attempt-0",
            "replacement_attempt_id": "attempt-1",
            "original_attempt_ordinal": 0,
            "replacement_attempt_ordinal": 1,
            "recovery_source_kind": "validation_replacement",
            "original_attempt_discarded_after_fault": True,
            "original_attempt_total_tokens": 30,
            "fault_or_death_at_ms": 10,
            "replacement_attempt_created_at_ms": 11,
            "replacement_provider_dispatch_at_ms": 12,
            "replacement_has_independent_raw_or_failure": True,
            "replacement_has_independent_provenance": True,
            "replacement_has_independent_usage": True,
            "original_provider_response_id": "response-0",
            "replacement_provider_response_id": "response-1",
            "ordered_evidence_roles": STARTED_REPLACEMENT_ROLES,
        },
    )
    replacement = _provider(
        "provider-replacement",
        member_kind="online_recovery_replacement_attempt",
        provider_attempt_id="attempt-1",
        response_id="response-1",
        prompt_tokens=8,
        completion_tokens=16,
        cost=Decimal("1.75"),
    )
    links = (
        (
            "attempt-0",
            "response-0",
            "raw-attempt-0",
            "provenance-attempt-0",
            "usage-attempt-0",
            "model-attempt-0",
        ),
        (
            "attempt-1",
            "response-1",
            "raw-attempt-1",
            "provenance-attempt-1",
            "usage-attempt-1",
            "model-attempt-1",
        ),
    )
    row = _online(original, replacement, provider_object_links=links)

    assert row.require_cell("actual_provider_calls").value == 2
    assert row.require_cell("actual_prompt_tokens").value == 18
    assert row.require_cell("actual_completion_tokens").value == 36
    assert row.require_cell("actual_total_tokens").value == 54
    assert row.require_cell("actual_cost_estimate_cny").value == Decimal("3.00")
    assert row.require_cell("wasted_actual_tokens").value == 30

    mismatched = Exp3PersistedObservation(
        observation_id=replacement.observation_id,
        facts={**replacement.facts, "provider_response_id": "response-mismatch"},
    )
    mismatch_row = _online(original, mismatched, provider_object_links=links)
    for metric_id in (
        "actual_provider_calls",
        "actual_prompt_tokens",
        "actual_completion_tokens",
        "actual_total_tokens",
        "actual_cost_estimate_cny",
        "wasted_actual_tokens",
    ):
        cell = mismatch_row.require_cell(metric_id)
        assert cell.value is None and cell.publish_blocked


def test_online_missing_provider_role_blocks_recovery_cell() -> None:
    incomplete_roles = tuple(
        role for role in ONLINE_CURRENT_PROVIDER_ROLES if role != "model_record"
    )
    chain = _replacement(
        "missing-model",
        roles=SUCCESSFUL_REPLACEMENT_ROLES,
        qualified=True,
        completed=True,
    )
    chain = Exp3PersistedObservation(
        observation_id=chain.observation_id,
        facts={**chain.facts, "current_provider_roles": incomplete_roles},
    )

    duplicate_original = _provider(
        "duplicate-original",
        member_kind="online_recovery_original_attempt",
        provider_attempt_id="attempt-0",
        response_id="response-duplicate",
        prompt_tokens=1,
        completion_tokens=1,
        cost=Decimal("0.1"),
    )
    duplicate_replacement = _provider(
        "duplicate-replacement",
        member_kind="online_recovery_replacement_attempt",
        provider_attempt_id="attempt-1",
        response_id="response-duplicate",
        prompt_tokens=1,
        completion_tokens=1,
        cost=Decimal("0.1"),
    )
    duplicate_links = (
        (
            "attempt-0",
            "response-duplicate",
            "raw-attempt-0",
            "provenance-attempt-0",
            "usage-attempt-0",
            "model-attempt-0",
        ),
        (
            "attempt-1",
            "response-duplicate",
            "raw-attempt-1",
            "provenance-attempt-1",
            "usage-attempt-1",
            "model-attempt-1",
        ),
    )

    cell = _online(
        chain, current_replacement_attempt_id="attempt-missing-model"
    ).require_cell("replacement_chain_count")
    assert cell.value is None
    assert cell.publish_blocked
    duplicate_cell = _online(
        duplicate_original,
        duplicate_replacement,
        provider_object_links=duplicate_links,
    ).require_cell("actual_provider_calls")
    assert duplicate_cell.value is None and duplicate_cell.publish_blocked

    global_original_base = _provider(
        "global-role-original",
        member_kind="online_recovery_original_attempt",
        provider_attempt_id="attempt-0",
        response_id="response-0",
        prompt_tokens=2,
        completion_tokens=3,
        cost=Decimal("0.2"),
    )
    global_original = Exp3PersistedObservation(
        observation_id=global_original_base.observation_id,
        facts={
            **global_original_base.facts,
            "original_task_unit_id": "unit-1",
            "replacement_task_unit_id": "unit-1",
            "original_attempt_id": "attempt-0",
            "replacement_attempt_id": "attempt-1",
            "original_attempt_ordinal": 0,
            "replacement_attempt_ordinal": 1,
            "recovery_source_kind": "validation_replacement",
            "original_attempt_discarded_after_fault": True,
            "original_attempt_total_tokens": 5,
            "fault_or_death_at_ms": 10,
            "replacement_attempt_created_at_ms": 11,
            "replacement_provider_dispatch_at_ms": 12,
            "replacement_has_independent_raw_or_failure": True,
            "replacement_has_independent_provenance": True,
            "replacement_has_independent_usage": True,
            "original_provider_response_id": "response-0",
            "replacement_provider_response_id": "response-1",
            "ordered_evidence_roles": STARTED_REPLACEMENT_ROLES,
        },
    )
    global_replacement_base = _provider(
        "global-role-replacement",
        member_kind="online_recovery_replacement_attempt",
        provider_attempt_id="attempt-1",
        response_id="response-1",
        prompt_tokens=2,
        completion_tokens=3,
        cost=Decimal("0.2"),
    )
    global_replacement = Exp3PersistedObservation(
        observation_id=global_replacement_base.observation_id,
        facts={
            **global_replacement_base.facts,
            "current_provider_roles": incomplete_roles,
        },
    )
    global_links = (
        (
            "attempt-0",
            "response-0",
            "raw-attempt-0",
            "provenance-attempt-0",
            "usage-attempt-0",
            "model-attempt-0",
        ),
        (
            "attempt-1",
            "response-1",
            "raw-attempt-1",
            "provenance-attempt-1",
            "usage-attempt-1",
            "model-attempt-1",
        ),
    )
    global_row = _online(
        global_original,
        global_replacement,
        provider_object_links=global_links,
    )
    for metric_id in (
        "actual_provider_calls",
        "actual_prompt_tokens",
        "actual_completion_tokens",
        "actual_total_tokens",
        "actual_cost_estimate_cny",
        "wasted_actual_tokens",
    ):
        cell = global_row.require_cell(metric_id)
        assert cell.value is None and cell.publish_blocked
