from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

import pytest

from tokenshare.executors.response_bank import CurrentTraceWrapper
from tokenshare.executors.trace_backed import (
    TraceReplacementBinding,
    TraceSourceBinding,
)
from tokenshare.experiments import paper_formal_evidence, paper_metric_observations
from tokenshare.experiments.paper_formal_evidence import (
    CommittedTraceConsumption,
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
    LedgerEventIdentitySnapshot,
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
        row_facts=(
            {
                "infra_invalid": False,
                "evidence_class": "online_real_provider",
            }
            if row_gate
            else {}
        ),
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
        row_facts={
            "infra_invalid": False,
            "evidence_class": "real_model_trace_protocol_run",
        },
        member_ids=("root-1", "consumption-1"),
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
    return _run_scoped_online_sources(roles=roles)


def _attempt_event(attempt_id: str = "attempt-1") -> LedgerEventIdentitySnapshot:
    return LedgerEventIdentitySnapshot(
        event_seq=1,
        event_id=f"event-{attempt_id}",
        event_type="ATTEMPT_STATE_CHANGED",
        event_hash=digest_json({"event": attempt_id}),
        prev_event_hash=None,
        task_id="task-1",
        object_type="Attempt",
        object_id=attempt_id,
    )


def _run_scoped_online_sources(
    *,
    run_id: str = "run-1",
    roles: tuple[str, ...] = ONLINE_ROLES,
) -> LineageSourceIndex:
    direct_ref = {
        "preregistered_root_run_id": "root-1",
        "execution_binding": {
            "execution_id": "run-1",
            "task_id": "task-1",
            "root_unit_id": "root-unit-1",
        },
        "attempt_refs": [_attempt_event().to_dict()],
    }
    attempt_event = _attempt_event()
    return LineageSourceIndex.create(
        records=(
            LineageSourceRecord.create(
                member_id="root-1",
                evidence_class="online_real_provider",
                direct_result_refs=(direct_ref,),
                current_task_attempt_event_refs=(attempt_event,),
                current_provider_object_refs=tuple(
                    _artifact(role, attempt_id=run_id) for role in roles
                ),
            ),
            LineageSourceRecord.create(
                member_id="attempt-1",
                evidence_class="online_real_provider",
                direct_result_refs=(direct_ref,),
                current_task_attempt_event_refs=(attempt_event,),
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
    assert paper_metric_observations._digest_metric_observations(
        publication.observations
    ) == digest_json([value.to_dict() for value in publication.observations])

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
    assert observation.covered_current_provider_roles == ONLINE_ROLES
    assert observation.lineage_source_record_refs
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


def test_exp5_planned_only_observation_does_not_require_attempt_lineage() -> None:
    member_id = "unit:0:planned-1"
    bundle = MetricObservationBundle(
        row_facts={
            "infra_invalid": False,
            "max_retries": 0,
            "replacement_attempts_allowed": False,
        },
        member_ids=(member_id,),
        member_facts_by_id={
            member_id: {
                "member_kind": "exp5_planned_ai_unit",
                "planned_call": True,
            },
        },
        row_identity_digest=digest_json({"row": "exp5-planned-only"}),
    )
    sources = LineageSourceIndex.create(
        records=(
            LineageSourceRecord.create(
                member_id=member_id,
                evidence_class="online_real_provider",
            ),
        ),
        input_identity_digest=INPUT_DIGEST,
    )

    _, publication = _materialize(
        "exp5_resources",
        "model_summary",
        ("planned_first_attempt_ai_unit_count",),
        bundle,
        sources,
    )

    observation = publication.observations[0]
    assert observation.null_reason is None
    assert observation.numeric_value == 1
    assert observation.publish_blocked is False
    assert observation.required_current_provider_roles == ()
    assert observation.covered_current_provider_roles == ()
    assert observation.current_provider_object_refs == ()
    assert observation.current_task_attempt_event_refs == ()


def test_online_observation_binds_attempt_lineage_to_run_scoped_provider_roles() -> None:
    _, publication = _materialize(
        "exp1_feasibility",
        "condition_summary",
        ("preregistered_root_count",),
        _online_bundle(),
        _run_scoped_online_sources(),
    )

    observation = publication.observations[0]
    assert observation.publish_blocked is False
    assert observation.numeric_value == 1
    assert observation.covered_current_provider_roles == ONLINE_ROLES
    assert observation.lineage_source_record_refs


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    (
        ("missing_run", "invalid_lineage_ref:execution_binding"),
        ("wrong_run", "invalid_lineage_identity:wrong-run"),
        (
            "wrong_attempt",
            "invalid_lineage_ref:attempt-2",
        ),
        ("wrong_task", "invalid_lineage_task:run-1"),
        ("wrong_role", "missing_required_lineage:model_record"),
        ("wrong_digest", "invalid_lineage_ref:attempt-1"),
        ("wrong_ref", "invalid_lineage_ref:attempt-1"),
    ),
)
def test_online_observation_blocks_mutated_attempt_to_run_lineage(
    mutation: str,
    expected_reason: str,
) -> None:
    attempt_event = _attempt_event()
    direct_ref = {
        "preregistered_root_run_id": "root-1",
        "execution_binding": {
            "execution_id": "run-1",
            "task_id": "task-1",
            "root_unit_id": "root-unit-1",
        },
        "attempt_refs": [attempt_event.to_dict()],
    }
    provider_refs = tuple(_artifact(role, attempt_id="run-1") for role in ONLINE_ROLES)
    if mutation == "missing_run":
        direct_ref["execution_binding"].pop("execution_id")
    elif mutation == "wrong_run":
        provider_refs = tuple(
            _artifact(role, attempt_id="wrong-run") for role in ONLINE_ROLES
        )
    elif mutation == "wrong_attempt":
        direct_ref["attempt_refs"][0]["object_id"] = "attempt-2"
    elif mutation == "wrong_task":
        provider_refs = tuple(
            replace(value, source_task_id="wrong-task") for value in provider_refs
        )
    elif mutation == "wrong_role":
        provider_refs = tuple(
            replace(value, source_role="unexpected_role")
            if value.source_role == "model_record"
            else value
            for value in provider_refs
        )
    elif mutation == "wrong_digest":
        direct_ref["attempt_refs"][0]["event_hash"] = "sha256:not-a-digest"
    elif mutation == "wrong_ref":
        direct_ref["attempt_refs"][0]["event_id"] = "event-other"
    sources = LineageSourceIndex.create(
        records=(
            LineageSourceRecord.create(
                member_id="root-1",
                evidence_class="online_real_provider",
                direct_result_refs=(direct_ref,),
                current_task_attempt_event_refs=(attempt_event,),
                current_provider_object_refs=provider_refs,
            ),
            LineageSourceRecord.create(
                member_id="attempt-1",
                evidence_class="online_real_provider",
                direct_result_refs=(direct_ref,),
                current_task_attempt_event_refs=(attempt_event,),
            ),
        ),
        input_identity_digest=INPUT_DIGEST,
    )

    _, publication = _materialize(
        "exp1_feasibility",
        "condition_summary",
        ("preregistered_root_count",),
        _online_bundle(),
        sources,
    )

    observation = publication.observations[0]
    assert observation.publish_blocked is True
    assert observation.numeric_value is None
    assert observation.null_reason == expected_reason


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


def test_trace_observation_uses_only_exact_consumed_entry_alias_not_root_inventory() -> None:
    root_ref = {"preregistered_root_run_id": "root-1"}
    sources = LineageSourceIndex.create(
        records=(
            LineageSourceRecord.create(
                member_id="root-1",
                evidence_class="real_model_trace_protocol_run",
                direct_result_refs=(root_ref,),
                source_bank_object_locators=tuple(
                    _locator(role, entry_id=entry_id)
                    for entry_id in ("entry-1", "entry-unconsumed")
                    for role in TRACE_ROLES
                ),
            ),
            LineageSourceRecord.create(
                member_id="entry-1",
                evidence_class="real_model_trace_protocol_run",
                source_bank_object_locators=tuple(
                    _locator(role, entry_id="entry-1") for role in TRACE_ROLES
                ),
            ),
        ),
        input_identity_digest=INPUT_DIGEST,
    )

    _, publication = _materialize(
        "exp2_trace_scalability",
        "worker_repeat_observation",
        ("preregistered_root_count",),
        _trace_bundle(),
        sources,
    )

    observation = publication.observations[0]
    assert observation.publish_blocked is False
    assert {value["member_id"] for value in observation.lineage_source_record_refs} == {
        "root-1",
        "entry-1",
    }


@pytest.mark.parametrize(
    "conflict_kind",
    (
        "missing_role",
        "extra_role",
        "different_digest",
        "different_bank",
        "different_manifest",
        "different_entry_id",
        "source_and_empty",
    ),
)
def test_lineage_merge_rejects_cross_root_source_entry_locator_conflicts(
    conflict_kind: str,
) -> None:
    complete = tuple(_locator(role) for role in TRACE_ROLES)
    if conflict_kind == "missing_role":
        conflicting = complete[:-1]
    elif conflict_kind == "extra_role":
        conflicting = (*complete, _locator("raw_output"))
    elif conflict_kind == "different_bank":
        conflicting = tuple(
            replace(locator, bank_root_id="bank-other") for locator in complete
        )
    elif conflict_kind == "different_manifest":
        conflicting = tuple(
            replace(
                locator,
                manifest_digest=digest_json({"manifest": "other"}),
            )
            for locator in complete
        )
    elif conflict_kind == "different_entry_id":
        conflicting = tuple(
            replace(locator, entry_id="entry-other") for locator in complete
        )
    elif conflict_kind == "source_and_empty":
        conflicting = ()
    else:
        conflicting = (
            replace(
                complete[0],
                object_digest=digest_json({"entry": "entry-1", "conflict": True}),
            ),
            *complete[1:],
        )
    records = tuple(
        LineageSourceRecord.create(
            member_id="entry-1",
            evidence_class="real_model_trace_protocol_run",
            source_bank_object_locators=locators,
        )
        for locators in (complete, conflicting)
    )

    with pytest.raises(ValueError, match="source entry locator identity conflict"):
        paper_formal_evidence._merge_lineage_source_records(records)


def test_lineage_merge_deduplicates_identical_cross_root_source_entry_locators() -> None:
    complete = tuple(_locator(role) for role in TRACE_ROLES)
    records = tuple(
        LineageSourceRecord.create(
            member_id="entry-1",
            evidence_class="real_model_trace_protocol_run",
            source_bank_object_locators=locators,
        )
        for locators in (complete, tuple(reversed(complete)))
    )

    assert paper_formal_evidence._merge_lineage_source_records(records) == (
        LineageSourceRecord.create(
            member_id="entry-1",
            evidence_class="real_model_trace_protocol_run",
            source_bank_object_locators=paper_formal_evidence._unique_typed(complete),
        ),
    )


def test_lineage_merge_allows_same_generic_alias_when_all_locator_sets_are_empty() -> None:
    records = tuple(
        LineageSourceRecord.create(
            member_id="shared-run-local-id",
            evidence_class="real_model_trace_protocol_run",
        )
        for _ in range(2)
    )

    assert paper_formal_evidence._merge_lineage_source_records(records) == (
        records[0],
    )


@pytest.mark.parametrize("conflict_kind", ("different_entry_id", "duplicate_role"))
def test_lineage_merge_rejects_invalid_singleton_source_entry_alias(
    conflict_kind: str,
) -> None:
    complete = tuple(_locator(role) for role in TRACE_ROLES)
    locators = (
        tuple(replace(locator, entry_id="entry-other") for locator in complete)
        if conflict_kind == "different_entry_id"
        else (
            *complete,
            replace(
                complete[0],
                object_digest=digest_json({"duplicate": "different"}),
            ),
        )
    )
    record = LineageSourceRecord.create(
        member_id="entry-1",
        evidence_class="real_model_trace_protocol_run",
        source_bank_object_locators=paper_formal_evidence._unique_typed(locators),
    )

    with pytest.raises(ValueError, match="source entry locator identity conflict"):
        paper_formal_evidence._merge_lineage_source_records((record,))


def test_lineage_merge_accepts_valid_singleton_source_entry_alias() -> None:
    locators = tuple(_locator(role) for role in (*TRACE_ROLES, "raw_output"))
    record = LineageSourceRecord.create(
        member_id="entry-1",
        evidence_class="real_model_trace_protocol_run",
        source_bank_object_locators=paper_formal_evidence._unique_typed(locators),
    )

    merged = paper_formal_evidence._merge_lineage_source_records((record,))

    assert len(merged) == 1
    assert merged[0].source_bank_object_locators == paper_formal_evidence._unique_typed(
        locators
    )


def test_shared_source_entry_alias_keeps_only_immutable_locators() -> None:
    entry_id = "entry-shared"
    manifest_digest = digest_json({"manifest": "shared"})
    locators = tuple(_locator(role, entry_id=entry_id) for role in TRACE_ROLES)
    root_records = []
    alias_records = []
    for index in (1, 2):
        root_id = f"root-{index}"
        binding = TraceSourceBinding.create(
            planned_ai_unit_id=f"unit-{index}",
            sample_slot_index=0,
            bank_root_id="bank-1",
            manifest_digest=manifest_digest,
            replacements=(
                TraceReplacementBinding(
                    replacement_slot=0,
                    entry_id=entry_id,
                    inference_request_digest=digest_json({"request": index}),
                ),
            ),
            source_evidence_class="approved_real_api_acquisition",
        )
        wrapper = CurrentTraceWrapper(
            current_run_id=f"run-{index}",
            current_task_id=f"task-{index}",
            current_unit_id=f"unit-{index}",
            current_attempt_id=f"attempt-{index}",
            attempt_ordinal=0,
            bank_root_id="bank-1",
            manifest_digest=manifest_digest,
            root_binding_marker_digest=digest_json({"root": index}),
            inference_request_digest=digest_json({"request": index}),
            entry_id=entry_id,
            locator_digests={"request_body": digest_json({"locator": index})},
            logical_started_at="logical:1",
            logical_finished_at="logical:2",
            source_latency_ms=1,
            current_parse_ref=None,
            current_verifier_ref=None,
            current_checker_ref=None,
            current_canonical_ref=None,
            current_ledger_ref=f"commit-{index}",
        )
        consumption = CommittedTraceConsumption(
            event_id=f"commit-{index}",
            attempt_id=f"attempt-{index}",
            unit_id=f"unit-{index}",
            planned_ai_unit_id=f"unit-{index}",
            attempt_ordinal=0,
            entry_id=entry_id,
            binding_digest=binding.binding_digest,
            delivery_digest=digest_json({"delivery": index}),
            wrapper_artifact_id=f"artifact-{index}",
            wrapper_content_hash=digest_json({"wrapper": index}),
        )
        root_records.append(
            LineageSourceRecord.create(
                member_id=root_id,
                evidence_class="real_model_trace_protocol_run",
                direct_result_refs=({"preregistered_root_run_id": root_id},),
                source_bank_object_locators=locators,
                current_trace_wrappers=(wrapper,),
                committed_trace_consumptions=(consumption,),
                trace_source_bindings=(binding,),
            )
        )
        alias_wrappers, alias_consumptions, alias_bindings = (
            paper_formal_evidence._trace_alias_local_closure(
                alias=entry_id,
                source_locators=locators,
                wrappers=(wrapper,),
                committed_trace_consumptions=(consumption,),
                trace_source_bindings=(binding,),
            )
        )
        alias_records.append(
            LineageSourceRecord.create(
                member_id=entry_id,
                evidence_class="real_model_trace_protocol_run",
                source_bank_object_locators=locators,
                current_trace_wrappers=alias_wrappers,
                committed_trace_consumptions=alias_consumptions,
                trace_source_bindings=alias_bindings,
            )
        )

    merged = paper_formal_evidence._merge_lineage_source_records(
        (*root_records, *alias_records)
    )
    shared = next(record for record in merged if record.member_id == entry_id)

    assert shared.source_bank_object_locators
    assert shared.current_trace_wrappers == ()
    assert shared.committed_trace_consumptions == ()
    assert shared.trace_source_bindings == ()
    for root_record in root_records:
        persisted = next(
            record for record in merged if record.member_id == root_record.member_id
        )
        assert persisted.current_trace_wrappers == root_record.current_trace_wrappers
        assert (
            persisted.committed_trace_consumptions
            == root_record.committed_trace_consumptions
        )
        assert persisted.trace_source_bindings == root_record.trace_source_bindings


def test_trace_observation_does_not_use_root_inventory_when_consumed_alias_is_missing() -> None:
    sources = LineageSourceIndex.create(
        records=(
            LineageSourceRecord.create(
                member_id="root-1",
                evidence_class="real_model_trace_protocol_run",
                direct_result_refs=({"preregistered_root_run_id": "root-1"},),
                source_bank_object_locators=tuple(
                    _locator(role, entry_id="entry-1") for role in TRACE_ROLES
                ),
            ),
        ),
        input_identity_digest=INPUT_DIGEST,
    )

    _, publication = _materialize(
        "exp2_trace_scalability",
        "worker_repeat_observation",
        ("preregistered_root_count",),
        _trace_bundle(),
        sources,
    )

    observation = publication.observations[0]
    assert observation.publish_blocked is True
    assert observation.null_reason == "missing_required_lineage:request_body"


def test_typed_pair_aliases_declare_exact_root_scope_and_source_identities() -> None:
    bundle = MetricObservationBundle(
        row_facts={
            "infra_invalid": False,
            "summary_kind": "repeat",
            "evidence_class": "real_model_trace_protocol_run",
        },
        member_ids=("root-1", "root-2", "pair-1"),
        member_facts_by_id={
            "root-1": {
                "member_kind": "preregistered_root",
                "preregistered_root_run_id": "root-1",
                "final_result_reference_complete": True,
                "end_to_end_verified_success": True,
                "source_bank_entry_ids": ("entry-1",),
            },
            "root-2": {
                "member_kind": "preregistered_root",
                "preregistered_root_run_id": "root-2",
                "final_result_reference_complete": True,
                "end_to_end_verified_success": True,
                "source_bank_entry_ids": ("entry-2",),
            },
            "pair-1": {
                "member_kind": "exp2_preregistered_pair",
                "baseline_worker_count": 1,
                "compared_worker_count": 3,
                "baseline_case_record_digest": "case",
                "compared_case_record_digest": "case",
                "baseline_repeat_id": 0,
                "compared_repeat_id": 0,
                "baseline_sample_slot_index": 0,
                "compared_sample_slot_index": 0,
                "baseline_end_to_end_verified_success": True,
                "compared_end_to_end_verified_success": True,
                "baseline_time_evidence_complete": True,
                "compared_time_evidence_complete": True,
                "baseline_trace_replay_wall_clock_ms": 10,
                "compared_trace_replay_wall_clock_ms": 5,
                "closed_exclusion_reason": "pair_is_eligible",
                "lineage_root_run_ids": ("root-1", "root-2"),
                "source_bank_entry_ids": ("entry-1", "entry-2"),
            }
        },
        row_identity_digest=digest_json({"row": "typed-pair"}),
    )
    records = []
    for root_id in ("root-1", "root-2"):
        entry_id = "entry-1" if root_id == "root-1" else "entry-2"
        source_binding = TraceSourceBinding.create(
            planned_ai_unit_id=f"unit-{root_id}",
            sample_slot_index=0,
            bank_root_id="bank-1",
            manifest_digest=digest_json({"manifest": 1}),
            replacements=(
                TraceReplacementBinding(
                    replacement_slot=0,
                    entry_id=entry_id,
                    inference_request_digest=digest_json({"request": root_id}),
                ),
            ),
            source_evidence_class="approved_real_api_acquisition",
        )
        records.append(
            LineageSourceRecord.create(
                member_id=root_id,
                evidence_class="real_model_trace_protocol_run",
                direct_result_refs=({"preregistered_root_run_id": root_id},),
                committed_trace_consumptions=(
                    CommittedTraceConsumption(
                        event_id=f"commit-{root_id}",
                        attempt_id=f"attempt-{root_id}",
                        unit_id=f"unit-{root_id}",
                        planned_ai_unit_id=f"unit-{root_id}",
                        attempt_ordinal=0,
                        entry_id=entry_id,
                        binding_digest=source_binding.binding_digest,
                        delivery_digest=digest_json({"delivery": root_id}),
                        wrapper_artifact_id=f"artifact-{root_id}",
                        wrapper_content_hash=digest_json({"wrapper": root_id}),
                    ),
                ),
                current_trace_wrappers=(
                    CurrentTraceWrapper(
                        current_run_id=f"run-{root_id}",
                        current_task_id=f"task-{root_id}",
                        current_unit_id=f"unit-{root_id}",
                        current_attempt_id=f"attempt-{root_id}",
                        attempt_ordinal=0,
                        bank_root_id="bank-1",
                        manifest_digest=digest_json({"manifest": 1}),
                        root_binding_marker_digest=digest_json({"root": root_id}),
                        inference_request_digest=digest_json({"request": root_id}),
                        entry_id=entry_id,
                        locator_digests={
                            "request_body": digest_json({"locator": root_id})
                        },
                        logical_started_at="logical:1",
                        logical_finished_at="logical:2",
                        source_latency_ms=1,
                        current_parse_ref=None,
                        current_verifier_ref=None,
                        current_checker_ref=None,
                        current_canonical_ref=None,
                        current_ledger_ref="event-1",
                    ),
                ),
                trace_source_bindings=(source_binding,),
            )
        )
    for entry_id, root_id in (("entry-1", "root-1"), ("entry-2", "root-2")):
        records.append(
            LineageSourceRecord.create(
                member_id=entry_id,
                evidence_class="real_model_trace_protocol_run",
                direct_result_refs=({"preregistered_root_run_id": root_id},),
                source_bank_object_locators=tuple(
                    _locator(role, entry_id=entry_id) for role in TRACE_ROLES
                ),
            )
        )
    sources = LineageSourceIndex.create(
        records=tuple(records),
        input_identity_digest=INPUT_DIGEST,
    )

    _, publication = _materialize(
        "exp2_trace_scalability",
        "repeat_summary",
        ("paired_speedup_planned_pair_count",),
        bundle,
        sources,
    )

    observation = publication.observations[0]
    assert observation.publish_blocked is False, {
        "null_reason": observation.null_reason,
        "blocked_reasons": observation.blocked_reasons,
        "source_roles": observation.covered_source_bank_roles,
    }
    assert observation.numeric_value == 1
    assert {value["member_id"] for value in observation.lineage_source_record_refs} == {
        "root-1",
        "root-2",
        "entry-1",
        "entry-2",
    }


def test_typed_pair_accepts_bound_committed_history_before_current_wrapper() -> None:
    manifest_digest = digest_json({"manifest": "history"})
    bundle = MetricObservationBundle(
        row_facts={"evidence_class": "real_model_trace_protocol_run"},
        member_ids=("root-1", "root-2", "pair-1"),
        member_facts_by_id={
            "root-1": {
                "member_kind": "preregistered_root",
                "preregistered_root_run_id": "root-1",
                "source_bank_entry_ids": ("entry-1-old", "entry-1-current"),
            },
            "root-2": {
                "member_kind": "preregistered_root",
                "preregistered_root_run_id": "root-2",
                "source_bank_entry_ids": ("entry-2",),
            },
            "pair-1": {
                "member_kind": "exp4_paired_root",
                "lineage_root_run_ids": ("root-1", "root-2"),
                "source_bank_entry_ids": (
                    "entry-1-old",
                    "entry-1-current",
                    "entry-2",
                ),
            },
        },
        row_identity_digest=digest_json({"row": "replacement-history"}),
    )

    def wrapper(root_id: str, entry_id: str, ordinal: int) -> CurrentTraceWrapper:
        return CurrentTraceWrapper(
            current_run_id=f"run-{root_id}",
            current_task_id=f"task-{root_id}",
            current_unit_id=f"unit-{root_id}",
            current_attempt_id=f"attempt-{root_id}-{ordinal}",
            attempt_ordinal=ordinal,
            bank_root_id="bank-1",
            manifest_digest=manifest_digest,
            root_binding_marker_digest=digest_json({"root": root_id}),
            inference_request_digest=digest_json(
                {"request": root_id, "ordinal": ordinal}
            ),
            entry_id=entry_id,
            locator_digests={"request_body": digest_json({"entry": entry_id})},
            logical_started_at="logical:1",
            logical_finished_at="logical:2",
            source_latency_ms=1,
            current_parse_ref=None,
            current_verifier_ref=None,
            current_checker_ref=None,
            current_canonical_ref=None,
            current_ledger_ref=f"event-{root_id}",
        )

    def binding(root_id: str, entries: tuple[str, ...]) -> TraceSourceBinding:
        return TraceSourceBinding.create(
            planned_ai_unit_id=f"unit-{root_id}",
            sample_slot_index=0,
            bank_root_id="bank-1",
            manifest_digest=manifest_digest,
            replacements=tuple(
                TraceReplacementBinding(
                    replacement_slot=ordinal,
                    entry_id=entry_id,
                    inference_request_digest=digest_json(
                        {"request": root_id, "ordinal": ordinal}
                    ),
                )
                for ordinal, entry_id in enumerate(entries)
            ),
            source_evidence_class="approved_real_api_acquisition",
        )

    def committed(
        root_id: str,
        entries: tuple[str, ...],
        source_binding: TraceSourceBinding,
    ) -> tuple[CommittedTraceConsumption, ...]:
        return tuple(
            CommittedTraceConsumption(
                event_id=f"commit-{root_id}-{ordinal}",
                attempt_id=f"attempt-{root_id}-{ordinal}",
                unit_id=f"unit-{root_id}",
                planned_ai_unit_id=f"unit-{root_id}",
                attempt_ordinal=ordinal,
                entry_id=entry_id,
                binding_digest=source_binding.binding_digest,
                delivery_digest=digest_json(
                    {"delivery": root_id, "ordinal": ordinal}
                ),
                wrapper_artifact_id=f"artifact-{root_id}-{ordinal}",
                wrapper_content_hash=digest_json(
                    {"wrapper": root_id, "ordinal": ordinal}
                ),
            )
            for ordinal, entry_id in enumerate(entries)
        )

    root_1_binding = binding(
        "root-1",
        ("entry-1-old", "entry-1-current", "entry-1-unused"),
    )
    root_2_binding = binding("root-2", ("entry-2",))

    sources = LineageSourceIndex.create(
        records=(
            LineageSourceRecord.create(
                member_id="root-1",
                evidence_class="real_model_trace_protocol_run",
                direct_result_refs=({"preregistered_root_run_id": "root-1"},),
                current_trace_wrappers=(
                    wrapper("root-1", "entry-1-current", 1),
                ),
                committed_trace_consumptions=committed(
                    "root-1",
                    ("entry-1-old", "entry-1-current"),
                    root_1_binding,
                ),
                trace_source_bindings=(root_1_binding,),
            ),
            LineageSourceRecord.create(
                member_id="root-2",
                evidence_class="real_model_trace_protocol_run",
                direct_result_refs=({"preregistered_root_run_id": "root-2"},),
                current_trace_wrappers=(wrapper("root-2", "entry-2", 0),),
                committed_trace_consumptions=committed(
                    "root-2", ("entry-2",), root_2_binding
                ),
                trace_source_bindings=(root_2_binding,),
            ),
        ),
        input_identity_digest=INPUT_DIGEST,
    )

    assert paper_metric_observations._typed_root_lineage_issue(
        sources,
        bundle,
    ) is None

    underclaimed_root = dict(bundle.member_facts_by_id["root-1"])
    underclaimed_root["source_bank_entry_ids"] = ("entry-1-current",)
    underclaimed_pair = dict(bundle.member_facts_by_id["pair-1"])
    underclaimed_pair["source_bank_entry_ids"] = (
        "entry-1-current",
        "entry-2",
    )
    underclaimed_bundle = MetricObservationBundle(
        row_facts=bundle.row_facts,
        member_ids=bundle.member_ids,
        member_facts_by_id={
            **bundle.member_facts_by_id,
            "root-1": underclaimed_root,
            "pair-1": underclaimed_pair,
        },
        row_identity_digest=digest_json({"row": "underclaimed-history"}),
    )
    assert paper_metric_observations._typed_root_lineage_issue(
        sources,
        underclaimed_bundle,
    ) == "root_source_lineage_mismatch:root-1"

    tampered_root = dict(bundle.member_facts_by_id["root-1"])
    tampered_root["source_bank_entry_ids"] = (
        "entry-1-old",
        "entry-1-current",
        "entry-1-unused",
    )
    tampered_pair = dict(bundle.member_facts_by_id["pair-1"])
    tampered_pair["source_bank_entry_ids"] = (
        "entry-1-old",
        "entry-1-current",
        "entry-1-unused",
        "entry-2",
    )
    tampered_bundle = MetricObservationBundle(
        row_facts=bundle.row_facts,
        member_ids=bundle.member_ids,
        member_facts_by_id={
            **bundle.member_facts_by_id,
            "root-1": tampered_root,
            "pair-1": tampered_pair,
        },
        row_identity_digest=digest_json({"row": "unbound-history"}),
    )
    assert paper_metric_observations._typed_root_lineage_issue(
        sources,
        tampered_bundle,
    ) == "root_source_lineage_mismatch:root-1"


def test_metric_observation_rejects_alias_lineage_from_foreign_root() -> None:
    foreign_ref = {"preregistered_root_run_id": "root-foreign"}
    sources = LineageSourceIndex.create(
        records=(
            LineageSourceRecord.create(
                member_id="root-1",
                evidence_class="real_model_trace_protocol_run",
                direct_result_refs=({"preregistered_root_run_id": "root-1"},),
            ),
            LineageSourceRecord.create(
                member_id="entry-1",
                evidence_class="real_model_trace_protocol_run",
                direct_result_refs=(foreign_ref,),
                source_bank_object_locators=tuple(
                    _locator(role) for role in TRACE_ROLES
                ),
            ),
        ),
        input_identity_digest=INPUT_DIGEST,
    )

    with pytest.raises(ValueError, match="crosses metric bundle root scope"):
        _materialize(
            "exp2_trace_scalability",
            "worker_repeat_observation",
            ("preregistered_root_count",),
            _trace_bundle(),
            sources,
        )

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


def test_exp1_trace_observation_uses_source_route_without_current_provider_roles() -> None:
    _, publication = _materialize(
        "exp1_feasibility",
        "condition_summary",
        ("preregistered_root_count",),
        _trace_bundle(),
        _trace_sources(),
    )
    observation = publication.observations[0]

    assert observation.numeric_value == 1
    assert observation.evidence_class == "real_model_trace_protocol_run"
    assert observation.required_current_provider_roles == ()
    assert observation.required_source_bank_roles == TRACE_ROLES
    assert observation.covered_source_bank_roles == TRACE_ROLES
    assert observation.current_provider_object_refs == ()


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
