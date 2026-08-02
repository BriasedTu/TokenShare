"""Task 15：Experiment 4 standalone pair/projector。"""

from __future__ import annotations

from decimal import Decimal

from tokenshare.experiments.paper_exp4_metrics import (
    EXP4_MODES,
    EXP4_NUMERIC_FIELDS,
    TRACE_SOURCE_BANK_ROLES,
    Exp4DirectRootFacts,
    Exp4ModeInput,
    Exp4PersistedObservation,
    build_exp4_observations,
)
from tokenshare.experiments.paper_metric_contract import load_paper_metric_contract


def _observation(observation_id: str, **facts: object) -> Exp4PersistedObservation:
    return Exp4PersistedObservation(
        observation_id=observation_id,
        facts={"source_bank_roles": TRACE_SOURCE_BANK_ROLES, **facts},
    )


def _root(
    root_id: str,
    *,
    case_id: str = "case-1",
    case_digest: str = "a" * 64,
    sample: int = 0,
    replacements: tuple[str, ...] = ("sample-0", "replacement-1"),
    final: bool = True,
    success: bool = True,
    wall_clock: int | None = 100,
    tokens: int | None = 1000,
    cost: Decimal | None = Decimal("1.0"),
    identity_consistent: bool = True,
    evidence_complete: bool = True,
    infrastructure_valid: bool = True,
) -> Exp4DirectRootFacts:
    return Exp4DirectRootFacts(
        preregistered_root_run_id=root_id,
        case_id=case_id,
        case_record_digest=case_digest,
        sample_slot_index=sample,
        replacement_slot_ids=replacements,
        final_result_reference_complete=final,
        end_to_end_verified_success=success,
        trace_replay_wall_clock_ms=wall_clock,
        trace_attributed_tokens=tokens,
        trace_attributed_cost=cost,
        identity_consistent=identity_consistent,
        paper_evidence_complete=evidence_complete,
        infrastructure_valid=infrastructure_valid,
        source_bank_roles=TRACE_SOURCE_BANK_ROLES,
    )


def _mode(
    mode: str,
    *roots: Exp4DirectRootFacts,
    domain: str = "factorization",
    repeat: int = 0,
    observations: tuple[Exp4PersistedObservation, ...] = (),
) -> Exp4ModeInput:
    return Exp4ModeInput(
        condition_id=f"condition:{mode}:{domain}:{repeat}:{len(roots)}",
        domain=domain,
        repeat_id=repeat,
        ablation_mode=mode,
        roots=roots,
        observations=observations,
    )


def _project(*inputs: Exp4ModeInput):
    return build_exp4_observations(inputs, load_paper_metric_contract())


def _specific(projection, mode: str):
    rows = tuple(row for row in projection.mode_specific_rows if row.ablation_mode == mode)
    assert len(rows) == 1
    return rows[0]


def test_each_ablation_pairs_one_full_by_case_repeat_sample() -> None:
    full = _root("full")
    decoy = _root("full-decoy", replacements=("sample-0", "replacement-decoy"))
    inputs = [_mode("FULL", full, decoy)]
    inputs.extend(_mode(mode, _root(f"root:{mode}")) for mode in EXP4_MODES if mode != "FULL")

    projection = _project(*inputs)

    assert len(projection.pair_rows) == 4
    assert {row.ablation_mode for row in projection.pair_rows} == set(EXP4_MODES[1:])
    assert {row.full_root_run_id for row in projection.pair_rows} == {"full"}
    assert all(row.pair_identity[:4] == ("case-1", 0, 0, ("sample-0", "replacement-1")) for row in projection.pair_rows)

    duplicate = _project(
        _mode("FULL", _root("duplicate-full")),
        _mode(
            "NO_VERIFICATION",
            _root("duplicate-ablation-1"),
            _root("duplicate-ablation-2"),
        ),
    )
    assert len(duplicate.pair_rows) == 2
    for row in duplicate.pair_rows:
        assert row.ineligibility_reasons == ("duplicate_exp4_ablation_identity",)
        assert all(
            cell.value is None
            and cell.publish_blocked
            and cell.reason == "duplicate_exp4_ablation_identity"
            for cell in row.cells
        )
    duplicate_summary = next(
        row
        for row in duplicate.mode_summary_rows
        if row.ablation_mode == "NO_VERIFICATION"
    )
    assert duplicate_summary.preregistered_root_run_ids == (
        "duplicate-ablation-1",
        "duplicate-ablation-2",
    )
    for row in (duplicate_summary, _specific(duplicate, "NO_VERIFICATION")):
        assert row.ineligibility_reasons == ("duplicate_exp4_ablation_identity",)
        assert all(
            cell.value is None
            and cell.publish_blocked
            and cell.reason == "duplicate_exp4_ablation_identity"
            for cell in row.cells
        )


def test_four_transition_cells_keep_all_failures() -> None:
    outcomes = ((True, True), (True, False), (False, True), (False, False))
    inputs: list[Exp4ModeInput] = []
    for index, (full_success, ablation_success) in enumerate(outcomes):
        kwargs = {"case_id": f"case-{index}", "case_digest": f"{index + 1:064x}"}
        inputs.append(_mode("FULL", _root(f"full-{index}", success=full_success, **kwargs)))
        inputs.append(_mode("NO_VERIFICATION", _root(f"ablation-{index}", success=ablation_success, **kwargs)))

    pairs = _project(*inputs).pair_rows

    assert len(pairs) == 4
    transition_ids = (
        "full_success_ablation_success",
        "full_success_ablation_failure",
        "full_failure_ablation_success",
        "full_failure_ablation_failure",
    )
    assert [sum(row.require_cell(metric_id).value or 0 for row in pairs) for metric_id in transition_ids] == [1, 1, 1, 1]
    assert all(row.require_cell("paired_root_count").value == 1 for row in pairs)


def test_success_completion_loss_and_resource_delta_signs() -> None:
    projection = _project(
        _mode("FULL", _root("full", wall_clock=100, tokens=1000, cost=Decimal("1.0"))),
        _mode(
            "NO_VERIFICATION",
            _root(
                "ablation",
                final=False,
                success=False,
                wall_clock=130,
                tokens=900,
                cost=Decimal("1.25"),
            ),
        ),
    )
    pair = projection.pair_rows[0]

    assert pair.require_cell("end_to_end_success_loss_vs_full").value == 1
    assert pair.require_cell("completion_loss_vs_full").value == 1
    assert pair.require_cell("trace_replay_wall_clock_delta_vs_full").value == 30
    assert pair.require_cell("trace_attributed_token_delta_vs_full").value == -100
    assert pair.require_cell("trace_attributed_cost_delta_vs_full").value == Decimal("0.25")

    missing = _project(
        _mode("FULL", _root("full-missing", wall_clock=None, tokens=None, cost=None)),
        _mode("NO_VERIFICATION", _root("ablation-missing")),
    ).pair_rows[0]
    for metric_id in (
        "trace_replay_wall_clock_delta_vs_full",
        "trace_attributed_token_delta_vs_full",
        "trace_attributed_cost_delta_vs_full",
    ):
        assert missing.require_cell(metric_id).value is None
        assert missing.require_cell(metric_id).reason


def test_no_verification_denominator_and_zero_case() -> None:
    labeled = tuple(
        _observation(
            f"invalid-{index}",
            member_kind="independently_labeled_invalid_candidate",
            independent_label_verified=True,
        )
        for index in range(2)
    )
    accepted = _observation(
        "wrong-accepted",
        member_kind="wrong_canonical_acceptance_event",
        independent_label_verified=True,
        entered_canonical=True,
    )
    row = _specific(
        _project(_mode("NO_VERIFICATION", _root("root"), observations=(*labeled, accepted))),
        "NO_VERIFICATION",
    )

    assert row.require_cell("independently_labeled_invalid_candidate_count").value == 2
    assert row.require_cell("wrong_canonical_acceptance_count").value == 1
    assert row.require_cell("wrong_canonical_acceptance_rate").value == Decimal("0.5")

    zero = _specific(_project(_mode("NO_VERIFICATION", _root("zero"))), "NO_VERIFICATION")
    assert zero.require_cell("independently_labeled_invalid_candidate_count").value == 0
    assert zero.require_cell("wrong_canonical_acceptance_rate").value is None
    assert "zero_denominator" in (zero.require_cell("wrong_canonical_acceptance_rate").reason or "")


def test_no_parser_policy_denominator_and_zero_case() -> None:
    observations = (
        _observation(
            "raw-accepted",
            member_kind="raw_only_exposure_event",
            parser_policy_disabled=True,
            raw_candidate_exposed=True,
            raw_candidate_accepted=True,
        ),
        _observation(
            "raw-rejected",
            member_kind="raw_only_exposure_event",
            parser_policy_disabled=True,
            raw_candidate_exposed=True,
            raw_candidate_accepted=False,
        ),
    )
    row = _specific(_project(_mode("NO_PARSER_POLICY", _root("root"), observations=observations)), "NO_PARSER_POLICY")

    assert row.require_cell("raw_only_exposure_count").value == 2
    assert row.require_cell("raw_only_acceptance_count").value == 1
    assert row.require_cell("raw_only_acceptance_rate").value == Decimal("0.5")

    zero = _specific(_project(_mode("NO_PARSER_POLICY", _root("zero"))), "NO_PARSER_POLICY")
    assert zero.require_cell("raw_only_exposure_count").value == 0
    assert zero.require_cell("raw_only_acceptance_rate").value is None
    assert "zero_denominator" in (zero.require_cell("raw_only_acceptance_rate").reason or "")


def test_no_requeue_denominator_and_zero_case() -> None:
    observations = (
        _observation(
            "stuck",
            member_kind="stuck_task_event",
            requeue_disabled=True,
            task_stuck=True,
        ),
    )
    row = _specific(
        _project(
            _mode(
                "NO_REQUEUE",
                _root("root-1", case_id="case-1"),
                _root("root-2", case_id="case-2", case_digest="b" * 64),
                observations=observations,
            )
        ),
        "NO_REQUEUE",
    )

    assert row.require_cell("stuck_task_count").value == 1
    assert row.require_cell("stuck_task_rate").value == Decimal("0.5")
    assert len(row.require_cell("stuck_task_rate").included_member_ids) == 2

    zero = _specific(_project(_mode("NO_REQUEUE")), "NO_REQUEUE")
    assert zero.require_cell("stuck_task_count").value == 0
    assert zero.require_cell("stuck_task_rate").value is None
    assert "zero_denominator" in (zero.require_cell("stuck_task_rate").reason or "")


def test_no_merge_gate_denominator_and_zero_case() -> None:
    observations = (
        _observation(
            "merge-failed",
            member_kind="premature_merge_event",
            merge_gate_disabled=True,
            merge_attempted_before_ready=True,
            premature_merge_failed=True,
        ),
        _observation(
            "merge-succeeded",
            member_kind="premature_merge_event",
            merge_gate_disabled=True,
            merge_attempted_before_ready=True,
            premature_merge_failed=False,
        ),
    )
    row = _specific(_project(_mode("NO_MERGE_GATE", _root("root"), observations=observations)), "NO_MERGE_GATE")

    assert row.require_cell("premature_merge_attempt_count").value == 2
    assert row.require_cell("premature_merge_failure_count").value == 1
    assert row.require_cell("premature_merge_failure_rate").value == Decimal("0.5")

    zero = _specific(_project(_mode("NO_MERGE_GATE", _root("zero"))), "NO_MERGE_GATE")
    assert zero.require_cell("premature_merge_attempt_count").value == 0
    assert zero.require_cell("premature_merge_failure_rate").value is None
    assert "zero_denominator" in (zero.require_cell("premature_merge_failure_rate").reason or "")


def test_retired_generic_aliases_absent() -> None:
    aliases = {
        "exposed_error_count",
        "escaped_error_count",
        "error_escape_rate",
        "wrong_canonical_count",
        "raw_only_count",
        "stuck_count",
        "premature_merge_count",
    }
    projection = _project(
        *(_mode(mode, _root(f"root:{mode}")) for mode in EXP4_MODES)
    )
    metric_ids = set(EXP4_NUMERIC_FIELDS)
    for row in (*projection.mode_summary_rows, *projection.pair_rows, *projection.mode_specific_rows):
        metric_ids.update(row.metric_ids)

    assert aliases.isdisjoint(metric_ids)
    no_inference = _specific(projection, "NO_VERIFICATION")
    assert no_inference.require_cell("wrong_canonical_acceptance_count").value == 0
    wrong_mode = _specific(projection, "FULL")
    assert wrong_mode.require_cell("wrong_canonical_acceptance_count").value is None
    assert "not_applicable" in (wrong_mode.require_cell("wrong_canonical_acceptance_count").reason or "")
