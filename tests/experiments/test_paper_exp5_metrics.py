from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from tokenshare.experiments.paper_exp5_metrics import (
    CURRENT_PROVIDER_ROLES,
    ENDPOINT_SERVING_CONFOUNDING_CAPTION,
    EXP5_QUALITY_FIELDS,
    EXP5_RESOURCE_FIELDS,
    Exp5FirstProviderAttemptFacts,
    Exp5ModelRepeatFacts,
    Exp5PlannedAIUnitFacts,
    Exp5PreregisteredRootFacts,
    build_exp5_observations,
)
from tokenshare.experiments.paper_metric_contract import load_paper_metric_contract
from tokenshare.experiments.paper_model_identity import PaperModelEndpointIdentity


def _digest(character: str) -> str:
    return "sha256:" + character * 64


IDENTITY = PaperModelEndpointIdentity(
    model_cohort_id="exp5-cohort-v3",
    model_cohort_digest=_digest("1"),
    cohort_member_id="glm-5.2",
    provider_config_id="exp5-sf-v3",
    selected_entry_id="glm-5.2-entry",
    provider_family="siliconflow",
    provider_model_id="zai-org/GLM-5.2",
    reasoning_profile_id="thinking-32768",
    effective_reasoning_controls={
        "enable_thinking": True,
        "thinking_budget": 32768,
    },
    source_provider_config_digest=_digest("2"),
)
_DEFAULT_IDENTITY_DIGEST = object()


def _root(
    root_id: str,
    *,
    terminal: int | None = 200,
    dispatched: int | None = 100,
    final: bool = True,
    success: bool = True,
) -> Exp5PreregisteredRootFacts:
    return Exp5PreregisteredRootFacts(
        preregistered_root_run_id=root_id,
        root_dispatched_at_ms=dispatched,
        root_terminal_at_ms=terminal,
        final_result_reference_complete=final,
        end_to_end_verified_success=success,
    )


def _attempt(
    attempt_id: str,
    unit_id: str,
    *,
    provider_failure: bool = False,
    parse_failure: bool = False,
    rejected: bool = False,
    accepted: bool = True,
    tokens: int | None = 10,
    cost: str | None = "0.25",
) -> Exp5FirstProviderAttemptFacts:
    return Exp5FirstProviderAttemptFacts(
        attempt_id=attempt_id,
        planned_ai_unit_id=unit_id,
        actual_call=True,
        provider_transport_failure=provider_failure,
        parse_schema_unusable=parse_failure,
        verification_checker_rejected=rejected,
        verifier_accepted_candidate=accepted,
        actual_total_tokens=tokens,
        actual_cost_estimate_cny=None if cost is None else Decimal(cost),
        current_provider_roles=CURRENT_PROVIDER_ROLES,
    )


def _repeat(
    repeat_id: int,
    *,
    roots: tuple[Exp5PreregisteredRootFacts, ...] | None = None,
    attempts: tuple[Exp5FirstProviderAttemptFacts, ...] = (),
    planned_unit_ids: tuple[str, ...] | None = None,
    dispatch: int | None = 100,
    identity: PaperModelEndpointIdentity | None = IDENTITY,
    observed_identity: PaperModelEndpointIdentity | None = IDENTITY,
    persisted_identity_digest: str | None | object = _DEFAULT_IDENTITY_DIGEST,
) -> Exp5ModelRepeatFacts:
    if roots is None:
        roots = (_root(f"root-{repeat_id}"),)
    if planned_unit_ids is None:
        planned_unit_ids = tuple(value.planned_ai_unit_id for value in attempts)
    if persisted_identity_digest is _DEFAULT_IDENTITY_DIGEST:
        persisted_identity_digest = (
            None
            if observed_identity is None
            else observed_identity.model_endpoint_identity_digest
        )
    return Exp5ModelRepeatFacts(
        model_arm_id="glm-5.2-arm",
        repeat_id=repeat_id,
        frozen_identity=identity,
        observed_identity=observed_identity,
        persisted_model_endpoint_identity_digest=persisted_identity_digest,
        protocol_first_dispatch_at_ms=dispatch,
        preregistered_roots=roots,
        planned_ai_units=tuple(
            Exp5PlannedAIUnitFacts(unit_id=value, planned_call=True)
            for value in planned_unit_ids
        ),
        first_provider_attempts=attempts,
        max_retries=0,
        replacement_attempts_allowed=False,
    )


def _projection(
    repeat0: Exp5ModelRepeatFacts,
    repeat1: Exp5ModelRepeatFacts | None = None,
    repeat2: Exp5ModelRepeatFacts | None = None,
):
    return build_exp5_observations(
        (
            repeat0,
            repeat1 or _repeat(1),
            repeat2 or _repeat(2),
        ),
        load_paper_metric_contract(),
    )


def _quality(projection):
    return projection.table_payloads[0].rows[0]


def _resources(projection):
    return projection.table_payloads[1].rows[0]


def test_nonpass_denominator_includes_every_actual_first_attempt() -> None:
    attempts = (
        _attempt("accepted", "unit-a"),
        _attempt(
            "provider-failed",
            "unit-b",
            provider_failure=True,
            accepted=False,
            tokens=None,
            cost=None,
        ),
        _attempt(
            "parse-failed", "unit-c", parse_failure=True, accepted=False
        ),
    )
    projection = _projection(_repeat(0, attempts=attempts))
    quality = _quality(projection)

    assert quality.require_cell("actual_first_provider_attempt_count").value == 3
    assert (
        quality.require_cell(
            "first_attempt_without_verifier_accepted_candidate_count"
        ).value
        == 2
    )
    assert quality.require_cell("first_attempt_nonpass_rate").value == Decimal(2) / 3
    assert _resources(projection).require_cell("first_attempt_call_coverage").value == 1
    assert _resources(projection).require_cell("actual_total_tokens").value is None
    assert _resources(projection).require_cell("actual_total_tokens").publish_blocked
    assert _resources(projection).require_cell("actual_cost_estimate_cny").value is None
    assert _resources(projection).require_cell("actual_cost_estimate_cny").publish_blocked

    unplanned = _attempt("unplanned", "unit-unplanned")
    invalid_repeat = replace(
        _repeat(0, attempts=(unplanned,)),
        planned_ai_units=(
            Exp5PlannedAIUnitFacts(unit_id="unit-unplanned", planned_call=False),
        ),
    )
    invalid = _projection(invalid_repeat)
    assert all(
        row.ineligibility_reasons == ("provider_attempt_without_planned_ai_unit",)
        and all(cell.value is None and cell.publish_blocked for cell in row.cells)
        for payload in invalid.table_payloads
        for row in payload.rows
    )


def test_reason_priority_is_mutually_exclusive() -> None:
    attempt = _attempt(
        "all-signals",
        "unit-all",
        provider_failure=True,
        parse_failure=True,
        rejected=True,
        accepted=False,
    )
    quality = _quality(_projection(_repeat(0, attempts=(attempt,))))

    assert quality.require_cell(
        "first_attempt_provider_transport_failure_count"
    ).value == 1
    assert quality.require_cell("first_attempt_parse_schema_unusable_count").value == 0
    assert quality.require_cell(
        "first_attempt_verification_checker_rejection_count"
    ).value == 0


def test_verification_rejection_denominator_is_checkable_only() -> None:
    attempts = (
        _attempt("transport", "unit-t", provider_failure=True, accepted=False),
        _attempt("parse", "unit-p", parse_failure=True, accepted=False),
        _attempt("rejected", "unit-r", rejected=True, accepted=False),
        _attempt("accepted", "unit-a"),
    )
    quality = _quality(_projection(_repeat(0, attempts=attempts)))

    assert quality.require_cell("first_attempt_checkable_candidate_count").value == 2
    assert quality.require_cell(
        "first_attempt_explicitly_rejected_by_verifier_count"
    ).value == 1
    assert quality.require_cell(
        "first_attempt_verification_rejection_rate"
    ).value == Decimal("0.5")


def test_all_preregistered_roots_drive_completion_success() -> None:
    repeat0 = _repeat(
        0,
        roots=(
            _root("successful"),
            _root("incorrect-final", success=False),
        ),
    )
    repeat1 = _repeat(1, roots=(_root("no-final", final=False, success=False),))
    repeat2 = _repeat(2, roots=(_root("early-stop", terminal=150, final=False, success=False),))
    quality = _quality(_projection(repeat0, repeat1, repeat2))

    assert quality.require_cell("preregistered_root_count").value == 4
    assert quality.require_cell("final_result_root_count").value == 2
    assert quality.require_cell("verified_correct_root_count").value == 1
    assert quality.require_cell("completion_rate").value == Decimal("0.5")
    assert quality.require_cell("end_to_end_verified_success_rate").value == Decimal("0.25")


def test_model_repeat_wallclock_is_first_dispatch_to_all_roots_terminal() -> None:
    repeat0 = _repeat(
        0,
        dispatch=100,
        roots=(
            _root("early-terminal", dispatched=110, terminal=150),
            _root("last-terminal", dispatched=120, terminal=225),
        ),
    )
    resources = _resources(_projection(repeat0))

    assert resources.require_cell("repeat0_wall_clock_ms").value == 125

    incomplete = replace(
        repeat0,
        preregistered_roots=(
            repeat0.preregistered_roots[0],
            replace(repeat0.preregistered_roots[1], root_terminal_at_ms=None),
        ),
    )
    incomplete_cell = _resources(_projection(incomplete)).require_cell(
        "repeat0_wall_clock_ms"
    )
    assert incomplete_cell.value is None
    assert incomplete_cell.publish_blocked

    terminal_before_dispatch = replace(
        repeat0,
        preregistered_roots=(
            repeat0.preregistered_roots[0],
            _root("backwards-root", dispatched=180, terminal=170),
        ),
    )
    backwards_resources = _resources(_projection(terminal_before_dispatch))
    assert backwards_resources.require_cell("repeat0_wall_clock_ms").value is None
    assert backwards_resources.require_cell("repeat0_wall_clock_ms").publish_blocked
    assert backwards_resources.require_cell("model_wall_clock_median_ms").value is None
    assert backwards_resources.require_cell("model_wall_clock_median_ms").publish_blocked


def test_wallclock_never_sums_root_clocks() -> None:
    repeat0 = _repeat(
        0,
        dispatch=100,
        roots=(
            _root("overlap-a", dispatched=100, terminal=150),
            _root("overlap-b", dispatched=110, terminal=160),
        ),
    )
    wallclock = _resources(_projection(repeat0)).require_cell(
        "repeat0_wall_clock_ms"
    ).value

    assert wallclock == 60
    assert wallclock != (150 - 100) + (160 - 110)

    for invalid_root in (
        _root("dispatch-before-protocol", dispatched=90, terminal=160),
        _root("terminal-before-protocol", dispatched=80, terminal=90),
    ):
        invalid_repeat = replace(
            repeat0,
            preregistered_roots=(repeat0.preregistered_roots[0], invalid_root),
        )
        invalid_resources = _resources(_projection(invalid_repeat))
        assert invalid_resources.require_cell("repeat0_wall_clock_ms").value is None
        assert invalid_resources.require_cell("repeat0_wall_clock_ms").publish_blocked
        assert invalid_resources.require_cell("model_wall_clock_range_ms").value is None
        assert invalid_resources.require_cell("model_wall_clock_range_ms").publish_blocked


def test_three_raw_repeats_and_median_min_max_range() -> None:
    repeats = (
        _repeat(0, dispatch=100, roots=(_root("r0", terminal=130),)),
        _repeat(1, dispatch=100, roots=(_root("r1", terminal=110),)),
        _repeat(2, dispatch=100, roots=(_root("r2", terminal=120),)),
    )
    resources = _resources(_projection(*repeats))

    assert tuple(
        resources.require_cell(f"repeat{repeat_id}_wall_clock_ms").value
        for repeat_id in range(3)
    ) == (30, 10, 20)
    assert resources.require_cell("model_wall_clock_median_ms").value == 20
    assert resources.require_cell("model_wall_clock_min_ms").value == 10
    assert resources.require_cell("model_wall_clock_max_ms").value == 30
    assert resources.require_cell("model_wall_clock_range_ms").value == 20


def test_exact_two_summary_table_payloads() -> None:
    projection = _projection(_repeat(0))

    assert tuple(value.table_id for value in projection.table_payloads) == (
        "exp5_quality",
        "exp5_resources",
    )
    assert projection.table_payloads[0].numeric_output_fields == EXP5_QUALITY_FIELDS
    assert projection.table_payloads[1].numeric_output_fields == EXP5_RESOURCE_FIELDS
    emitted = {
        cell.metric_id
        for payload in projection.table_payloads
        for row in payload.rows
        for cell in row.cells
    }
    assert not emitted & {
        "accepted_validity_rate",
        "retry_count",
        "retry_success_rate",
        "recovery_rate",
        "model_pairwise_significance",
        "model_pairwise_rank",
        "composite_model_score",
    }

    same_display_different_identity = replace(
        IDENTITY,
        selected_entry_id="glm-5.2-other-entry",
        source_provider_config_digest=_digest("3"),
    )
    distinct = build_exp5_observations(
        tuple(_repeat(repeat_id) for repeat_id in range(3))
        + tuple(
            replace(
                _repeat(
                    repeat_id,
                    identity=same_display_different_identity,
                    observed_identity=same_display_different_identity,
                ),
                model_arm_id="glm-5.2-second-endpoint-arm",
            )
            for repeat_id in range(3)
        ),
        load_paper_metric_contract(),
    )
    assert all(len(payload.rows) == 2 for payload in distinct.table_payloads)
    assert len(distinct.table_payloads[0].caption_metadata["model_endpoint_identities"]) == 2


def test_endpoint_serving_confounding_caption_required() -> None:
    projection = _projection(_repeat(0))
    expected_metadata = {
        "endpoint_serving_confounding_caption": ENDPOINT_SERVING_CONFOUNDING_CAPTION,
        "model_endpoint_identities": (IDENTITY.to_dict(),),
    }

    assert ENDPOINT_SERVING_CONFOUNDING_CAPTION == (
        "endpoint_and_serving_profile_are_confounding_factors"
    )
    assert all(
        payload.caption_metadata == expected_metadata
        for payload in projection.table_payloads
    )

    mismatch = replace(IDENTITY, reasoning_profile_id="different-serving-profile")
    mismatch_projection = _projection(
        _repeat(0, observed_identity=mismatch),
        _repeat(1, observed_identity=mismatch),
        _repeat(2, observed_identity=mismatch),
    )
    for payload in mismatch_projection.table_payloads:
        row = payload.rows[0]
        assert row.ineligibility_reasons == ("model_endpoint_identity_mismatch",)
        assert all(cell.value is None and cell.publish_blocked for cell in row.cells)

    missing_projection = _projection(
        _repeat(0, observed_identity=None),
        _repeat(1, observed_identity=None),
        _repeat(2, observed_identity=None),
    )
    for payload in missing_projection.table_payloads:
        row = payload.rows[0]
        assert row.ineligibility_reasons == ("model_endpoint_identity_missing",)
        assert all(cell.value is None and cell.publish_blocked for cell in row.cells)

    digest_mismatch_projection = _projection(
        _repeat(0, persisted_identity_digest=_digest("f")),
        _repeat(1, persisted_identity_digest=_digest("f")),
        _repeat(2, persisted_identity_digest=_digest("f")),
    )
    for payload in digest_mismatch_projection.table_payloads:
        row = payload.rows[0]
        assert row.ineligibility_reasons == (
            "model_endpoint_identity_digest_mismatch",
        )
        assert all(cell.value is None and cell.publish_blocked for cell in row.cells)
