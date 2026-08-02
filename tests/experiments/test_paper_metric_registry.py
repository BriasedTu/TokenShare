from __future__ import annotations

from dataclasses import replace

import pytest

from tokenshare.experiments import paper_metric_registry
from tokenshare.experiments.paper_exp1_metrics import build_exp1_observations
from tokenshare.experiments.paper_exp2_metrics import (
    build_exp2_online_observations,
    build_exp2_trace_observations,
)
from tokenshare.experiments.paper_exp3_metrics import (
    TRACE_SOURCE_BANK_ROLES,
    Exp3PersistedObservation,
    Exp3TraceConditionInput,
    project_exp3_online_recovery,
    project_exp3_trace_condition,
)
from tokenshare.experiments.paper_exp4_metrics import build_exp4_observations
from tokenshare.experiments.paper_exp5_metrics import build_exp5_observations
from tokenshare.experiments.paper_metric_contract import (
    load_paper_metric_contract,
)
from tokenshare.experiments.paper_metric_registry import (
    load_paper_metric_registry,
)


def test_registry_has_exactly_one_projector_per_experiment_table(monkeypatch) -> None:
    contract = load_paper_metric_contract()
    registry = load_paper_metric_registry(contract)

    assert tuple(item.table_id for item in registry.registrations) == tuple(
        table.table_id for table in contract.tables
    )
    assert tuple(item.output_path for item in registry.registrations) == tuple(
        table.output_path for table in contract.tables
    )
    assert {
        item.table_id: item.projector for item in registry.registrations
    } == {
        "exp1_feasibility": build_exp1_observations,
        "exp2_trace_scalability": build_exp2_trace_observations,
        "exp2_online_concurrency": build_exp2_online_observations,
        "exp3_trace_robustness": project_exp3_trace_condition,
        "exp3_online_recovery": project_exp3_online_recovery,
        "exp4_ablation": build_exp4_observations,
        "exp5_quality": build_exp5_observations,
        "exp5_resources": build_exp5_observations,
    }

    calls = []

    def counting_exp5(payload, current_contract):
        calls.append(payload)
        return build_exp5_observations(payload, current_contract)

    monkeypatch.setattr(
        paper_metric_registry,
        "build_exp5_observations",
        counting_exp5,
    )
    counted_registry = load_paper_metric_registry(contract)
    counted_registry.project_all(
        {key: () for key in counted_registry.required_input_keys}
    )
    assert len(calls) == 1


def test_registry_maps_exp3_trace_and_online_recovery_tables_to_task14_projectors() -> None:
    registry = load_paper_metric_registry(load_paper_metric_contract())

    assert (
        registry.require_registration("exp3_trace_robustness").projector
        is project_exp3_trace_condition
    )
    assert (
        registry.require_registration("exp3_online_recovery").projector
        is project_exp3_online_recovery
    )


def test_registry_rejects_uncontracted_output_field() -> None:
    registry = load_paper_metric_registry(load_paper_metric_contract())
    draft = registry.project_table("exp3_trace_robustness", _exp3_trace_payload())
    condition_row = draft.rows[0]

    bad_cell = replace(
        condition_row.cells[0],
        metric_id="renderer_only_success_score",
    )
    bad_row = replace(
        condition_row,
        source_row=replace(
            condition_row.source_row,
            cells=(bad_cell, *condition_row.cells[1:]),
        ),
    )

    with pytest.raises(ValueError, match="uncontracted metric key"):
        registry.validate_draft(
            replace(
                draft,
                rows=(bad_row, *draft.rows[1:]),
            )
        )

    missing_row = replace(
        condition_row,
        source_row=replace(
            condition_row.source_row,
            cells=condition_row.cells[:-1],
        ),
    )
    with pytest.raises(ValueError, match="cell field set contract drift"):
        registry.validate_draft(replace(draft, rows=(missing_row, *draft.rows[1:])))

    wrong_kind_row = replace(condition_row, row_kind="worker_death_summary")
    with pytest.raises(ValueError, match="cell field set contract drift"):
        registry.validate_draft(
            replace(draft, rows=(wrong_kind_row, *draft.rows[1:]))
        )

    with pytest.raises(ValueError, match="output path contract drift"):
        registry.validate_draft(
            replace(draft, output_path="metrics/uncontracted.csv")
        )


def test_global_infra_invalid_blocks_publish_cells() -> None:
    registry = load_paper_metric_registry(load_paper_metric_contract())
    payload = _exp3_trace_payload()

    ordinary = registry.project_table("exp3_trace_robustness", payload)
    ordinary_cells = ordinary.rows[0].cells
    explicit_null = next(
        cell
        for cell in ordinary_cells
        if cell.metric_id == "replacement_attempt_success_rate"
    )
    assert explicit_null.value is None
    assert explicit_null.reason == "not_applicable_no_started_replacement"
    assert not explicit_null.publish_blocked

    blocked = registry.project_table(
        "exp3_trace_robustness",
        payload,
        global_infra_invalid=True,
    )
    assert all(row.cells for row in blocked.rows)
    blocked_cells = tuple(cell for row in blocked.rows for cell in row.cells)
    assert all(cell.value is None for cell in blocked_cells)
    assert all(cell.publish_blocked for cell in blocked_cells)
    assert {cell.reason for cell in blocked_cells} == {
        "global_infrastructure_invalid"
    }


def _exp3_trace_payload() -> tuple[Exp3TraceConditionInput, ...]:
    return (
        Exp3TraceConditionInput(
            condition_id="condition-1",
            fault_type="false_positive",
            repeat_id=0,
            sample_slot_id="slot-1",
            observations=(
                Exp3PersistedObservation(
                    observation_id="root-1",
                    facts={
                        "member_kind": "preregistered_root",
                        "final_result_reference_complete": True,
                        "end_to_end_verified_success": True,
                        "source_bank_roles": TRACE_SOURCE_BANK_ROLES,
                    },
                ),
            ),
            infra_invalid=False,
        ),
    )
