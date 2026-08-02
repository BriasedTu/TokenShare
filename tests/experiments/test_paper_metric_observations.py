from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

import pytest

from tokenshare.experiments.paper_formal_evidence import (
    LineageSourceIndex,
    LineageSourceRecord,
)
from tokenshare.experiments.paper_metric_contract import (
    MetricObservationBundle,
    capture_metric_computation_traces,
    load_paper_metric_contract,
    recompute_metric,
)
from tokenshare.experiments.paper_metric_observations import (
    materialize_metric_observations,
    recompute_observation_value,
)
from tokenshare.experiments.paper_metric_registry import (
    DraftMetricRow,
    MetricTableDraft,
)
from tokenshare.experiments.paper_models import (
    ArtifactIdentitySnapshot,
    ExternalBankObjectLocator,
    digest_json,
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
TRACE_ROLES = (*ONLINE_ROLES[:-2], "acquisition_attempt", "model_record")
INPUT_DIGEST = digest_json({"fixture": "task20-lineage"})


@dataclass(frozen=True, kw_only=True)
class _Cell:
    metric_id: str
    value: int | Decimal | None
    reason: str | None
    publish_blocked: bool
    membership: tuple[object, ...] = ()


@dataclass(frozen=True, kw_only=True)
class _Row:
    row_label: str
    cells: tuple[_Cell, ...]
    paper_eligible: bool = False


def _artifact(role: str, *, attempt_id: str = "attempt-1") -> ArtifactIdentitySnapshot:
    return ArtifactIdentitySnapshot(
        artifact_id=f"artifact-{attempt_id}-{role}",
        artifact_type="PaperLineageFixture",
        uri=f"artifact://{attempt_id}/{role}",
        content_hash=digest_json({"attempt_id": attempt_id, "role": role}),
        size_bytes=1,
        media_type="application/json",
        artifact_schema_id="tokenshare.paper_lineage_fixture",
        artifact_schema_version="v1",
        source_role=role,
        source_task_id="task-1",
        source_execution_id=attempt_id,
        created_at="2026-08-02T00:00:00Z",
    )


def _locator(role: str, *, entry_id: str = "entry-1") -> ExternalBankObjectLocator:
    return ExternalBankObjectLocator(
        bank_root_id="bank-1",
        manifest_digest=digest_json({"manifest": 1}),
        entry_id=entry_id,
        object_role=role,
        object_digest=digest_json({"entry_id": entry_id, "role": role}),
    )


def _online_bundle(
    *,
    row_gate: bool = True,
    row_identity: str = "online",
    attempt_ids: tuple[str, ...] = ("attempt-1",),
) -> MetricObservationBundle:
    return MetricObservationBundle(
        row_facts={"infra_invalid": False} if row_gate else {},
        member_ids=("root-1", *attempt_ids),
        member_facts_by_id={
            "root-1": {
                "member_kind": "preregistered_root",
                "final_result_reference_complete": True,
                "end_to_end_verified_success": True,
                "failure_class": "none",
                "failure_stage": "none",
                "failure_kind": "none",
                "infra_invalid": False,
            },
            **{
                attempt_id: {
                    "member_kind": "actual_provider_attempt",
                    "current_provider_roles": ONLINE_ROLES,
                    "provider_attempt_id": attempt_id,
                }
                for attempt_id in attempt_ids
            },
        },
        row_identity_digest=digest_json({"row": row_identity}),
    )


def _trace_bundle() -> MetricObservationBundle:
    return MetricObservationBundle(
        row_facts={"infra_invalid": False},
        member_ids=("root-1", "consumption-1"),
        member_facts_by_id={
            "root-1": {
                "member_kind": "preregistered_root",
                "final_result_reference_complete": True,
                "end_to_end_verified_success": True,
                "infra_invalid": False,
            },
            "consumption-1": {
                "member_kind": "committed_trace_consumption",
                "committed": True,
                "source_bank_entry_id": "entry-1",
                "source_bank_roles": TRACE_ROLES,
            },
        },
        row_identity_digest=digest_json({"row": "trace"}),
    )


def _online_sources(*, roles: tuple[str, ...] = ONLINE_ROLES) -> LineageSourceIndex:
    return LineageSourceIndex.create(
        records=(
            LineageSourceRecord.create(
                member_id="root-1",
                evidence_class="online_real_provider",
                direct_result_refs=({"preregistered_root_run_id": "root-1"},),
            ),
            LineageSourceRecord.create(
                member_id="attempt-1",
                evidence_class="online_real_provider",
                current_provider_object_refs=tuple(_artifact(role) for role in roles),
            ),
        ),
        input_identity_digest=INPUT_DIGEST,
    )


def _trace_sources() -> LineageSourceIndex:
    return LineageSourceIndex.create(
        records=(
            LineageSourceRecord.create(
                member_id="root-1",
                evidence_class="real_model_trace_protocol_run",
                direct_result_refs=({"preregistered_root_run_id": "root-1"},),
            ),
            LineageSourceRecord.create(
                member_id="entry-1",
                evidence_class="real_model_trace_protocol_run",
                source_bank_object_locators=tuple(_locator(role) for role in TRACE_ROLES),
            ),
        ),
        input_identity_digest=INPUT_DIGEST,
    )


def _draft(table_id: str, row_kind: str, cells: tuple[_Cell, ...]) -> MetricTableDraft:
    return MetricTableDraft(
        table_id=table_id,
        experiment_id="fixture",
        output_path=f"metrics/{table_id}.csv",
        numeric_output_fields=tuple(cell.metric_id for cell in cells),
        rows=(DraftMetricRow(row_kind=row_kind, source_row=_Row(row_label="row-1", cells=cells)),),
        projector_name="task20.fixture",
    )


def _capture_cells(
    table_id: str,
    row_kind: str,
    metric_ids: tuple[str, ...],
    bundle: MetricObservationBundle,
):
    contract = load_paper_metric_contract()
    with capture_metric_computation_traces() as traces:
        evaluations = tuple(
            recompute_metric(
                contract,
                table_id,
                metric_id,
                row_kind=row_kind,
                bundle=bundle,
            )
            for metric_id in metric_ids
        )
    cells = tuple(
        _Cell(
            metric_id=metric_id,
            value=evaluation.value,
            reason=evaluation.reason,
            publish_blocked=evaluation.publish_blocked,
            membership=evaluation.member_decisions,
        )
        for metric_id, evaluation in zip(metric_ids, evaluations, strict=True)
    )
    return contract, tuple(traces), cells


def _materialize(table_id, row_kind, metric_ids, bundle, sources):
    contract, traces, cells = _capture_cells(table_id, row_kind, metric_ids, bundle)
    publication = materialize_metric_observations(
        contract=contract,
        table_drafts=(_draft(table_id, row_kind, cells),),
        computation_traces=traces,
        source_index=sources,
        expected_source_input_identity_digest=INPUT_DIGEST,
    )
    return contract, publication


def test_every_numeric_draft_becomes_one_observation() -> None:
    _, publication = _materialize(
        "exp1_feasibility",
        "condition_summary",
        ("preregistered_root_count", "final_result_root_count"),
        _online_bundle(),
        _online_sources(),
    )

    assert publication.numeric_draft_cell_count == 2
    assert publication.numeric_cell_coverage == Decimal("1")
    assert len({item.observation_id for item in publication.observations}) == 2

    first_bundle = _online_bundle(row_identity="first")
    second_bundle = _online_bundle(row_identity="second")
    _, first_traces, first_cells = _capture_cells(
        "exp1_feasibility", "condition_summary", ("preregistered_root_count",), first_bundle
    )
    contract, second_traces, second_cells = _capture_cells(
        "exp1_feasibility", "condition_summary", ("preregistered_root_count",), second_bundle
    )
    reordered = MetricTableDraft(
        table_id="exp1_feasibility",
        experiment_id="fixture",
        output_path="metrics/exp1_feasibility.csv",
        numeric_output_fields=("preregistered_root_count",),
        rows=(
            DraftMetricRow(row_kind="condition_summary", source_row=_Row(row_label="second", cells=second_cells)),
            DraftMetricRow(row_kind="condition_summary", source_row=_Row(row_label="first", cells=first_cells)),
        ),
        projector_name="task20.fixture",
    )
    exact = materialize_metric_observations(
        contract=contract,
        table_drafts=(reordered,),
        computation_traces=(*first_traces, *second_traces),
        source_index=_online_sources(),
        expected_source_input_identity_digest=INPUT_DIGEST,
    )
    assert [(item.row_key["row_label"], item.row_identity_digest) for item in exact.observations] == [
        ("second", second_bundle.row_identity_digest),
        ("first", first_bundle.row_identity_digest),
    ]
    with pytest.raises(ValueError, match="cell identity|row identity|duplicate"):
        materialize_metric_observations(
            contract=contract,
            table_drafts=(reordered,),
            computation_traces=(*first_traces, *first_traces),
            source_index=_online_sources(),
            expected_source_input_identity_digest=INPUT_DIGEST,
        )
    with pytest.raises(ValueError, match="cell identity"):
        replace(
            first_traces[0],
            cell_identity_digest=second_traces[0].cell_identity_digest,
        )


def test_observation_has_formula_value_numerator_and_complete_denominator() -> None:
    contract, publication = _materialize(
        "exp1_feasibility",
        "condition_summary",
        ("preregistered_root_count",),
        _online_bundle(),
        _online_sources(),
    )
    observation = publication.observations[0]

    assert observation.formula_id == contract.require_metric(
        "exp1_feasibility", "preregistered_root_count"
    ).formula_id
    assert observation.numeric_value == 1
    assert observation.numerator_membership_ids == ("root-1",)
    assert observation.denominator_inventory_ids == ("root-1", "attempt-1")
    assert set(observation.member_facts_by_id) == set(observation.denominator_inventory_ids)


def test_observation_has_excluded_and_null_reasons() -> None:
    _, valid = _materialize(
        "exp1_feasibility",
        "condition_summary",
        ("preregistered_root_count",),
        _online_bundle(),
        _online_sources(),
    )
    _, null = _materialize(
        "exp1_feasibility",
        "condition_summary",
        ("preregistered_root_count",),
        _online_bundle(row_gate=False),
        _online_sources(),
    )

    assert valid.observations[0].excluded_member_ids == ("attempt-1",)
    assert valid.observations[0].exclusion_reasons["attempt-1"]
    assert null.observations[0].numeric_value is None
    assert null.observations[0].null_reason == "missing_required_row_evidence"


def test_online_observation_requires_current_provider_roles_and_marks_source_locator_na() -> None:
    _, publication = _materialize(
        "exp1_feasibility",
        "condition_summary",
        ("preregistered_root_count",),
        _online_bundle(),
        _online_sources(),
    )
    observation = publication.observations[0]

    assert observation.required_current_provider_roles == ONLINE_ROLES
    assert {item.source_role for item in observation.current_provider_object_refs} == set(ONLINE_ROLES)
    assert observation.source_bank_object_locators == ()
    assert observation.not_applicable_evidence_roles == ("source_bank_object_locators",)

    split_bundle = _online_bundle(attempt_ids=("attempt-1", "attempt-2"))
    split_sources = LineageSourceIndex.create(
        records=(
            LineageSourceRecord.create(member_id="root-1", evidence_class="online_real_provider"),
            LineageSourceRecord.create(
                member_id="attempt-1",
                evidence_class="online_real_provider",
                current_provider_object_refs=tuple(_artifact(role, attempt_id="attempt-1") for role in ONLINE_ROLES[:4]),
            ),
            LineageSourceRecord.create(
                member_id="attempt-2",
                evidence_class="online_real_provider",
                current_provider_object_refs=tuple(_artifact(role, attempt_id="attempt-2") for role in ONLINE_ROLES[4:]),
            ),
        ),
        input_identity_digest=INPUT_DIGEST,
    )
    _, split = _materialize(
        "exp1_feasibility", "condition_summary", ("preregistered_root_count",), split_bundle, split_sources
    )
    assert split.observations[0].numeric_value is None


def test_trace_observation_requires_source_locator_roles_and_marks_current_provider_na() -> None:
    _, publication = _materialize(
        "exp2_trace_scalability",
        "worker_repeat_observation",
        ("preregistered_root_count",),
        _trace_bundle(),
        _trace_sources(),
    )
    observation = publication.observations[0]

    assert observation.required_source_bank_roles == TRACE_ROLES
    assert observation.covered_source_bank_roles == TRACE_ROLES
    assert observation.current_provider_object_refs == ()
    assert observation.not_applicable_evidence_roles == ("current_provider_object_refs",)

    split_sources = LineageSourceIndex.create(
        records=(
            LineageSourceRecord.create(member_id="root-1", evidence_class="real_model_trace_protocol_run"),
            LineageSourceRecord.create(
                member_id="entry-1",
                evidence_class="real_model_trace_protocol_run",
                source_bank_object_locators=tuple(_locator(role, entry_id="entry-1") for role in TRACE_ROLES[:4]),
            ),
            LineageSourceRecord.create(
                member_id="entry-2",
                evidence_class="real_model_trace_protocol_run",
                source_bank_object_locators=tuple(_locator(role, entry_id="entry-2") for role in TRACE_ROLES[4:]),
            ),
        ),
        input_identity_digest=INPUT_DIGEST,
    )
    split_bundle = replace(
        _trace_bundle(),
        member_ids=("root-1", "consumption-1", "consumption-2"),
        member_facts_by_id={
            **_trace_bundle().member_facts_by_id,
            "consumption-2": {
                "member_kind": "committed_trace_consumption",
                "committed": True,
                "source_bank_entry_id": "entry-2",
                "source_bank_roles": TRACE_ROLES,
            },
        },
    )
    _, split = _materialize(
        "exp2_trace_scalability", "worker_repeat_observation", ("preregistered_root_count",), split_bundle, split_sources
    )
    assert split.observations[0].numeric_value is None


def test_exp3_online_recovery_drafts_become_observations() -> None:
    bundle = MetricObservationBundle(
        row_facts={"infra_invalid": False},
        member_ids=("attempt-1",),
        member_facts_by_id={
            "attempt-1": {
                "member_kind": "online_recovery_original_attempt",
                "original_provider_dispatch_succeeded": True,
                "current_provider_roles": ONLINE_ROLES,
            }
        },
        row_identity_digest=digest_json({"row": "exp3-online"}),
    )
    _, publication = _materialize(
        "exp3_online_recovery",
        "online_recovery_summary",
        ("actual_provider_calls",),
        bundle,
        _online_sources(),
    )

    assert publication.observations[0].table_id == "exp3_online_recovery"
    assert publication.observations[0].numeric_value == 1


def test_non_null_value_recomputes_from_membership() -> None:
    contract, publication = _materialize(
        "exp1_feasibility",
        "condition_summary",
        ("preregistered_root_count",),
        _online_bundle(),
        _online_sources(),
    )
    observation = publication.observations[0]

    assert recompute_observation_value(contract, observation) == observation.numeric_value
    with pytest.raises(ValueError, match="independent recomputation mismatch"):
        recompute_observation_value(contract, replace(observation, numeric_value=2))


def test_missing_required_role_blocks_cell_and_table() -> None:
    contract, traces, cells = _capture_cells(
        "exp1_feasibility",
        "condition_summary",
        ("preregistered_root_count",),
        _online_bundle(),
    )
    sources = _online_sources(roles=ONLINE_ROLES[:-1])
    publication = materialize_metric_observations(
        contract=contract,
        table_drafts=(
            _draft("exp1_feasibility", "condition_summary", cells),
        ),
        computation_traces=traces,
        source_index=sources,
        expected_source_input_identity_digest=INPUT_DIGEST,
    )
    observation = publication.observations[0]

    assert observation.numeric_value is None
    assert observation.publish_blocked is True
    assert observation.null_reason == "missing_required_lineage:model_record"
    assert publication.blocked_table_ids == ("exp1_feasibility",)
    with pytest.raises(ValueError, match="source input identity mismatch"):
        materialize_metric_observations(
            contract=contract,
            table_drafts=(
                _draft("exp1_feasibility", "condition_summary", cells),
            ),
            computation_traces=traces,
            source_index=sources,
            expected_source_input_identity_digest=digest_json({"wrong": True}),
        )
