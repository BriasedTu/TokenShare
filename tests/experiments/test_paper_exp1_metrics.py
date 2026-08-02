"""Task 12：Experiment 1 独立 projector 的固定分母与 missingness。"""

from __future__ import annotations

from decimal import Decimal

from tests.experiments.test_paper_direct_results import (
    _canonical_fixture,
    _inventory_manifest,
    _inventory_row,
    _project,
)
from tokenshare.experiments.paper_direct_results import (
    build_canonical_direct_evidence,
)
from tokenshare.experiments.paper_exp1_metrics import (
    EXP1_NUMERIC_FIELDS,
    Exp1ActualProviderAttemptFacts,
    Exp1HydratedDirectRow,
    build_exp1_observations,
)
from tokenshare.experiments.paper_metric_contract import (
    MembershipStatus,
    load_paper_metric_contract,
)


ONLINE_ROLES = (
    "request_body",
    "raw_output_or_provider_failure",
    "provenance",
    "usage_status",
    "latency",
    "pricing",
    "provider_attempt",
    "model_record",
)


def _direct_row(tmp_path, *, root_id: str, correct: bool = True, verdict: bool = True):
    inventory_row, condition, catalog = _inventory_row(root_id=root_id)
    kwargs, *_ = _canonical_fixture(
        tmp_path,
        inventory_row,
        correct=correct,
        include_verdict=verdict,
    )
    evidence = build_canonical_direct_evidence(**kwargs)
    projection = _project(
        _inventory_manifest(inventory_row),
        (condition,),
        (catalog,),
        {root_id: evidence},
    )
    return projection.rows[0]


def _not_started_row():
    inventory_row, condition, catalog = _inventory_row(root_id="inventory:not-started")
    return _project(
        _inventory_manifest(inventory_row),
        (condition,),
        (catalog,),
        {},
    ).rows[0]


def _attempt(
    attempt_id: str,
    *,
    latency: int | None = 10,
    tokens: int | None = 20,
    cost: Decimal | None = Decimal("0.2"),
    roles: tuple[str, ...] | None = ONLINE_ROLES,
) -> Exp1ActualProviderAttemptFacts:
    return Exp1ActualProviderAttemptFacts(
        attempt_id=attempt_id,
        provider_latency_ms=latency,
        total_tokens=tokens,
        cost_estimate_cny=cost,
        current_provider_roles=roles,
    )


def _cell(result, metric_id: str):
    assert len(result) == 1
    return result[0].require_cell(metric_id)


def test_exp1_completion_requires_complete_final_reference_for_all_preregistered_roots(
    tmp_path,
) -> None:
    complete = _direct_row(tmp_path, root_id="inventory:complete")
    not_started = _not_started_row()
    rows = (
        Exp1HydratedDirectRow(
            direct_result=complete,
            root_start_at_ms=100,
            root_terminal_at_ms=500,
            actual_provider_attempts=(_attempt("attempt:complete"),),
        ),
        Exp1HydratedDirectRow(
            direct_result=not_started,
            actual_provider_attempts=(),
        ),
    )

    result = build_exp1_observations(rows, load_paper_metric_contract())

    assert _cell(result, "preregistered_root_count").value == 2
    assert _cell(result, "final_result_root_count").value == 1
    assert _cell(result, "completion_rate").value == Decimal("0.5")
    assert _cell(result, "no_final_failure_count").value == 1
    assert _cell(result, "failure_root_count").value == 1


def test_wrong_final_is_completion_not_success(tmp_path) -> None:
    wrong = _direct_row(tmp_path, root_id="inventory:wrong", correct=False)
    result = build_exp1_observations(
        (
            Exp1HydratedDirectRow(
                direct_result=wrong,
                root_start_at_ms=100,
                root_terminal_at_ms=500,
                actual_provider_attempts=(_attempt("attempt:wrong"),),
            ),
        ),
        load_paper_metric_contract(),
    )

    assert _cell(result, "final_result_root_count").value == 1
    assert _cell(result, "completion_rate").value == Decimal(1)
    assert _cell(result, "verified_correct_root_count").value == 0
    assert _cell(result, "end_to_end_verified_success_rate").value == Decimal(0)
    assert _cell(result, "incorrect_final_failure_count").value == 1
    assert _cell(result, "failure_root_count").value == 1


def test_infra_invalid_returns_blocked_null_publish_observation(tmp_path) -> None:
    invalid = _direct_row(
        tmp_path,
        root_id="inventory:invalid",
        verdict=False,
    )
    result = build_exp1_observations(
        (Exp1HydratedDirectRow(direct_result=invalid),),
        load_paper_metric_contract(),
    )

    for cell in result[0].cells:
        assert cell.value is None
        assert cell.reason == "infra_invalid"
        assert cell.publish_blocked is True
        assert cell.audit_denominator_member_ids == ("inventory:invalid",)
        assert cell.paper_eligible is False
        assert all(
            decision.status
            in {
                MembershipStatus.INCLUDED,
                MembershipStatus.EXCLUDED,
                MembershipStatus.BLOCKED_MISSING_EVIDENCE,
            }
            for decision in cell.membership
        )


def test_actual_wall_latency_tokens_cost_estimate_missingness_is_explicit(
    tmp_path,
) -> None:
    left = _direct_row(tmp_path, root_id="inventory:left")
    right = _direct_row(tmp_path, root_id="inventory:right", correct=False)
    hydrated = (
        Exp1HydratedDirectRow(
            direct_result=left,
            root_start_at_ms=100,
            root_terminal_at_ms=500,
            actual_provider_attempts=(
                _attempt("attempt:left", latency=10, tokens=20, cost=Decimal("0.2")),
            ),
        ),
        Exp1HydratedDirectRow(
            direct_result=right,
            root_start_at_ms=200,
            root_terminal_at_ms=550,
            actual_provider_attempts=(
                _attempt("attempt:right", latency=15, tokens=30, cost=Decimal("0.3")),
            ),
        ),
    )
    contract = load_paper_metric_contract()

    result = build_exp1_observations(hydrated, contract)
    assert _cell(result, "actual_end_to_end_wall_clock_ms").value == Decimal(450)
    assert _cell(result, "actual_provider_latency_ms").value == Decimal(25)
    assert _cell(result, "actual_total_tokens").value == Decimal(50)
    assert _cell(result, "actual_cost_estimate_cny").value == Decimal("0.5")

    missing_time = build_exp1_observations(
        (
            hydrated[0],
            Exp1HydratedDirectRow(
                direct_result=right,
                root_start_at_ms=200,
                root_terminal_at_ms=None,
                actual_provider_attempts=hydrated[1].actual_provider_attempts,
            ),
        ),
        contract,
    )
    assert _cell(missing_time, "actual_end_to_end_wall_clock_ms").value is None

    missing_usage = build_exp1_observations(
        (
            hydrated[0],
            Exp1HydratedDirectRow(
                direct_result=right,
                root_start_at_ms=200,
                root_terminal_at_ms=550,
                actual_provider_attempts=(_attempt("attempt:right", tokens=None),),
            ),
        ),
        contract,
    )
    token_cell = _cell(missing_usage, "actual_total_tokens")
    assert token_cell.value is None and token_cell.publish_blocked
    assert token_cell.reason == "missing_member_field:attempt:right:total_tokens"
    assert _cell(missing_usage, "actual_provider_latency_ms").value == Decimal(20)

    missing_pricing = build_exp1_observations(
        (
            Exp1HydratedDirectRow(
                direct_result=left,
                root_start_at_ms=100,
                root_terminal_at_ms=500,
                actual_provider_attempts=(_attempt("attempt:left", cost=None),),
            ),
        ),
        contract,
    )
    assert _cell(missing_pricing, "actual_cost_estimate_cny").value is None

    missing_roles = build_exp1_observations(
        (
            Exp1HydratedDirectRow(
                direct_result=left,
                root_start_at_ms=100,
                root_terminal_at_ms=500,
                actual_provider_attempts=(_attempt("attempt:left", roles=None),),
            ),
        ),
        contract,
    )
    role_cell = _cell(missing_roles, "actual_provider_latency_ms")
    assert role_cell.value is None and role_cell.publish_blocked
    assert role_cell.reason == "missing_current_provider_roles:attempt:left"


def test_exp1_has_no_accepted_validity_alias(tmp_path) -> None:
    row = _direct_row(tmp_path, root_id="inventory:no-alias")
    result = build_exp1_observations(
        (
            Exp1HydratedDirectRow(
                direct_result=row,
                root_start_at_ms=10,
                root_terminal_at_ms=20,
                actual_provider_attempts=(_attempt("attempt:no-alias"),),
            ),
        ),
        load_paper_metric_contract(),
    )

    assert len(EXP1_NUMERIC_FIELDS) == 13
    assert result[0].metric_ids == EXP1_NUMERIC_FIELDS
    assert "accepted_validity_rate" not in result[0].metric_ids
    assert not any("validity" in metric_id for metric_id in result[0].metric_ids)
