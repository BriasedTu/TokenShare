"""Task 3：canonical typed evidence 与固定 inventory 分母。"""

from __future__ import annotations

import json
from dataclasses import fields, replace
from pathlib import Path
from typing import Any

import pytest

from tokenshare.core.models import ArtifactRef
from tokenshare.experiments.paper_direct_results import (
    CONDITION_AXIS_KEYS,
    PaperDirectProjection,
    PaperDirectRootResult,
    build_canonical_direct_evidence,
    build_direct_boolean_aggregate,
    project_paper_direct_results,
)
from tokenshare.experiments.paper_models import (
    CanonicalDirectRootEvidence,
    ExternalBankObjectLocator,
    PaperDirectRootInventoryRow,
    PreregisteredRootInventoryManifest,
    digest_json,
)
from tokenshare.local_runtime import (
    build_experiment_ablation_gate_applied_observation,
    build_experiment_fault_injected_observation,
    build_experiment_premature_merge_attempted_observation,
)
from tokenshare.local_runtime.projection import project_protocol_run
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType


def _digest(label: str) -> str:
    return digest_json({"label": label})


def _axes(worker_count: int = 3) -> dict[str, Any]:
    axes = {
        "domain": "factorization",
        "difficulty": "hard",
        "topic_family": None,
        "worker_count": worker_count,
        "sample_slot_index": 0,
        "fault_condition": None,
        "death_condition": None,
        "ablation_mode": None,
        "model_endpoint_id": "deepseek_v4_pro_exp1_baseline",
    }
    assert tuple(axes) == CONDITION_AXIS_KEYS
    return axes


def _condition_manifest(
    condition_id: str,
    axes: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    record_body = {
        "schema_version": "tokenshare.preregistered_condition_record.v1",
        "condition_id": condition_id,
        "condition_axes": axes,
        "condition_axes_digest": digest_json(axes),
    }
    record = {**record_body, "condition_record_digest": digest_json(record_body)}
    body = {
        "schema_version": "tokenshare.preregistered_condition_manifest.v1",
        "records": [record],
    }
    manifest = {**body, "condition_manifest_digest": digest_json(body)}
    ref = {
        "schema_version": "tokenshare.preregistered_condition_ref.v1",
        "condition_manifest_digest": manifest["condition_manifest_digest"],
        "condition_record_digest": record["condition_record_digest"],
        "condition_axes_digest": record["condition_axes_digest"],
    }
    return manifest, ref


def _catalog(
    records: list[tuple[str, str, str]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    built: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for case_id, quantile, stratum in records:
        case_axes = {
            "factor_position_quantile": quantile,
            "position_stratum": stratum,
        }
        body = {
            "schema_version": "tokenshare.preregistered_case_record.v1",
            "case_id": case_id,
            "domain": "factorization",
            "difficulty": "hard",
            "case_axes_digest": digest_json(case_axes),
            **case_axes,
        }
        record = {**body, "case_record_digest": digest_json(body)}
        built.append(record)
        by_id[case_id] = record
    body = {
        "schema_version": "tokenshare.preregistered_case_catalog_manifest.v1",
        "records": built,
    }
    return {**body, "catalog_digest": digest_json(body)}, by_id


def _case_ref(catalog: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "tokenshare.preregistered_case_ref.v1",
        "catalog_digest": catalog["catalog_digest"],
        "case_record_digest": record["case_record_digest"],
        "case_axes_digest": record["case_axes_digest"],
        "factor_position_quantile": record["factor_position_quantile"],
        "position_stratum": record["position_stratum"],
    }


def _inventory_row(
    *,
    root_id: str = "inventory:root:0",
    condition_id: str = "opaque-condition",
    case_id: str = "opaque-case",
    worker_count: int = 3,
    evidence_class: str = "online_real_provider",
    inventory_id: str = "inventory:paper:test",
) -> tuple[PaperDirectRootInventoryRow, dict[str, Any], dict[str, Any]]:
    axes = _axes(worker_count)
    condition_manifest, condition_ref = _condition_manifest(condition_id, axes)
    catalog, records = _catalog([(case_id, "early", "front")])
    values = {
        "inventory_id": inventory_id,
        "preregistered_root_run_id": root_id,
        "experiment_id": "experiment_1",
        "condition_id": condition_id,
        "preregistered_condition_ref": condition_ref,
        "condition_axes": axes,
        "case_id": case_id,
        "preregistered_case_ref": _case_ref(catalog, records[case_id]),
        "repeat_id": 0,
        "evidence_class": evidence_class,
    }
    row = PaperDirectRootInventoryRow(
        **values,
        inventory_row_digest=digest_json(
            {
                "schema_version": "tokenshare.paper_direct_root_inventory_row.v2",
                **values,
            }
        ),
    )
    return row, condition_manifest, catalog


def _inventory_manifest(
    *rows: PaperDirectRootInventoryRow,
    inventory_id: str = "inventory:paper:test",
) -> PreregisteredRootInventoryManifest:
    body = {
        "schema_version": "tokenshare.preregistered_root_inventory_manifest.v1",
        "inventory_id": inventory_id,
        "root_count": len(rows),
        "rows": [row.to_dict() for row in rows],
    }
    return PreregisteredRootInventoryManifest(
        inventory_id=inventory_id,
        rows=rows,
        root_count=len(rows),
        inventory_digest=digest_json(body),
    )


def _replace_inventory_row(
    row: PaperDirectRootInventoryRow,
    **changes: Any,
) -> PaperDirectRootInventoryRow:
    values = row.to_dict()
    values.pop("schema_version")
    values.pop("inventory_row_digest")
    values.update(changes)
    body = {
        "schema_version": "tokenshare.paper_direct_root_inventory_row.v2",
        **values,
    }
    return PaperDirectRootInventoryRow(
        **values,
        inventory_row_digest=digest_json(body),
    )


def _save_role_artifact(
    store: ArtifactStore,
    *,
    label: str,
    role: str,
    execution_id: str,
    task_id: str,
    body: dict[str, Any] | None = None,
) -> ArtifactRef:
    return store.save_json(
        body or {"label": label},
        artifact_id=f"artifact_{label}",
        artifact_type="PaperDirectEvidence",
        artifact_schema_id=f"tokenshare.paper_direct.{role}",
        artifact_schema_version="v1",
        source={
            "role": role,
            "execution_id": execution_id,
            "task_id": task_id,
        },
        metadata={},
        created_at="2026-08-01T00:00:00Z",
    )


def _append_event(
    ledger: EventLedger,
    *,
    task_id: str,
    event_type: EventType,
    suffix: str,
    payload: dict[str, Any],
) -> None:
    ledger.append(
        event_type=event_type,
        object_type="PaperDirectFixture",
        object_id=f"object:{suffix}",
        task_id=task_id,
        actor={"kind": "pytest"},
        correlation_id="correlation:paper-direct",
        idempotency_key=f"paper-direct:{suffix}",
        payload=payload,
        occurred_at="2026-08-01T00:00:00Z",
    )


def _replay_ledger_with_payload_change(
    ledger: EventLedger,
    *,
    path: Path,
    changed_event_seq: int,
) -> EventLedger:
    swapped = EventLedger(path)
    for event in ledger.read_all():
        payload = dict(event.payload)
        if event.event_seq == changed_event_seq:
            payload["producer_note"] = "different-valid-payload"
        swapped.append(
            event_type=event.event_type,
            object_type=event.object_type,
            object_id=event.object_id,
            task_id=event.task_id,
            actor=dict(event.actor),
            correlation_id=event.correlation_id,
            causation_event_id=event.causation_event_id,
            idempotency_key=event.idempotency_key,
            payload=payload,
            occurred_at=event.occurred_at,
        )
    return swapped


def _replay_ledger_with_attempt_unit_changes(
    ledger: EventLedger,
    *,
    path: Path,
    units_by_event_seq: dict[int, str],
) -> EventLedger:
    swapped = EventLedger(path)
    for event in ledger.read_all():
        payload = dict(event.payload)
        if event.event_seq in units_by_event_seq:
            payload["unit_id"] = units_by_event_seq[event.event_seq]
        swapped.append(
            event_type=event.event_type,
            object_type=event.object_type,
            object_id=event.object_id,
            task_id=event.task_id,
            actor=dict(event.actor),
            correlation_id=event.correlation_id,
            causation_event_id=event.causation_event_id,
            idempotency_key=event.idempotency_key,
            payload=payload,
            occurred_at=event.occurred_at,
        )
    return swapped


def _canonical_fixture(
    tmp_path: Path,
    row: PaperDirectRootInventoryRow,
    *,
    correct: bool = True,
    completed: bool = True,
    evidence_class: str | None = None,
    include_verdict: bool = True,
    include_merge: bool = True,
    terminal_event_type: EventType = EventType.TASK_UNIT_STATE_CHANGED,
    terminal_state: str | None = None,
):
    root_id = row.preregistered_root_run_id
    execution_id = f"execution:{root_id}"
    selected_class = evidence_class or row.evidence_class
    task_id = f"task:{root_id}"
    unit_id = f"unit:{root_id}"
    store = ArtifactStore(tmp_path / root_id.replace(":", "_"))
    ledger = EventLedger(store.root_path / "events" / "ledger.jsonl")
    final_ref = _save_role_artifact(
        store,
        label=f"final_{root_id.replace(':', '_')}",
        role="final_result",
        execution_id=execution_id,
        task_id=task_id,
    )
    parser_ref = _save_role_artifact(
        store,
        label=f"parser_{root_id.replace(':', '_')}",
        role="parser_result",
        execution_id=execution_id,
        task_id=task_id,
    )
    verdict_body = {
        "schema_version": "tokenshare.paper_direct_correctness_verdict.v1",
        "execution_id": execution_id,
        "task_id": task_id,
        "root_unit_id": unit_id,
        "final_artifact_id": final_ref.artifact_id,
        "final_content_hash": final_ref.content_hash,
        "final_size_bytes": final_ref.size_bytes,
        "verdict_kind": "independent_verifier",
        "correct": correct,
    }
    verdict_ref = _save_role_artifact(
        store,
        label=f"verdict_{root_id.replace(':', '_')}",
        role="independent_verdict",
        execution_id=execution_id,
        task_id=task_id,
        body=verdict_body,
    )
    current_roles = (
        "request_body",
        "raw_output_or_provider_failure",
        "provenance",
        "usage_status",
        "latency",
        "pricing",
        "provider_attempt",
        "model_record",
    )
    current_refs = tuple(
        _save_role_artifact(
            store,
            label=f"{role}_{root_id.replace(':', '_')}",
            role=role,
            execution_id=execution_id,
            task_id=task_id,
        )
        for role in current_roles
    )
    resource_ref = _save_role_artifact(
        store,
        label=f"resource_{root_id.replace(':', '_')}",
        role=(
            "actual_resource_book"
            if selected_class == "online_real_provider"
            else "trace_resource_book"
        ),
        execution_id=execution_id,
        task_id=task_id,
    )
    base_unit = {
        "unit_id": unit_id,
        "task_id": task_id,
        "state": "Ready",
    }
    attempt_id = f"attempt:{root_id}"
    _append_event(
        ledger,
        task_id=task_id,
        event_type=EventType.TASK_UNIT_CREATED,
        suffix="created",
        payload={"task_unit": base_unit},
    )
    for event_type, state, suffix in (
        (EventType.EXECUTION_REQUEST_RECORDED, "Running", "request"),
        (EventType.EXECUTION_SUBMISSION_RECORDED, "Submitted", "submission"),
        (EventType.VERIFICATION_RECORDED, "Verified", "verification"),
    ):
        _append_event(
            ledger,
            task_id=task_id,
            event_type=event_type,
            suffix=suffix,
            payload={
                "schema_version": f"paper_direct_fixture.{suffix}.v1",
                "task_id": task_id,
                "unit_id": unit_id,
                "attempt_id": attempt_id,
                "state": state,
            },
        )
    _append_event(
        ledger,
        task_id=task_id,
        event_type=EventType.CANONICAL_OUTPUTS_BOUND,
        suffix="canonical",
        payload={
            "canonical_selection": {
                "unit_id": unit_id,
                "canonical_output_refs": {"answer": final_ref.to_dict()},
            }
        },
    )
    if include_merge:
        _append_event(
            ledger,
            task_id=task_id,
            event_type=EventType.MERGE_RECORDED,
            suffix="merge",
            payload={
                "schema_version": "phase5.merge_recorded.v1",
                "task_id": task_id,
                "parent_unit_id": unit_id,
                "merge_output_refs": {"answer": final_ref.to_dict()},
                "merge_record": {
                    "task_id": task_id,
                    "parent_unit_id": unit_id,
                    "merge_output_refs": {"answer": final_ref.to_dict()},
                },
            },
        )
    _append_event(
        ledger,
        task_id=task_id,
        event_type=terminal_event_type,
        suffix="terminal",
        payload={
            "task_unit": {
                **base_unit,
                "state": terminal_state or ("Completed" if completed else "Failed"),
            }
        },
    )
    runtime_result = project_protocol_run(
        run_id=execution_id,
        task_id=task_id,
        root_unit_id=unit_id,
        event_ledger=ledger,
        artifact_store=store,
    )
    kwargs: dict[str, Any] = {
        "inventory_row": row,
        "execution_id": execution_id,
        "event_ledger": ledger,
        "artifact_store": store,
        "runtime_result": runtime_result,
        "final_result_ref": final_ref,
        "parser_refs": (parser_ref,),
        "verifier_checker_refs": (verdict_ref,) if include_verdict else (),
    }
    if selected_class == "online_real_provider":
        kwargs.update(
            current_provider_object_refs=current_refs,
            actual_resource_book_ref=resource_ref,
        )
    else:
        roles = (
            (
                "request_body",
                "raw_output_or_provider_failure",
                "provenance",
                "usage_status",
                "latency",
                "pricing",
                "acquisition_attempt",
                "model_record",
            )
            if selected_class == "real_model_trace_protocol_run"
            else ("raw_output", "provenance", "model_record")
        )
        kwargs.update(
            source_bank_object_locators=tuple(
                ExternalBankObjectLocator(
                    bank_root_id="approved-bank",
                    manifest_digest=_digest("bank-manifest"),
                    entry_id="entry-1",
                    object_role=role,
                    object_digest=_digest(f"source:{role}"),
                )
                for role in roles
            ),
            trace_resource_book_ref=resource_ref,
        )
    return kwargs, ledger, store, runtime_result, final_ref


def _official_runtime_hook_observations(
    runtime_result: Any,
    final_ref: ArtifactRef,
) -> list[dict[str, Any]]:
    event_refs = tuple(runtime_result.event_refs[:1])
    return [
        build_experiment_fault_injected_observation(
            condition_id="condition:hook",
            run_id=runtime_result.run_id,
            task_id=runtime_result.task_id,
            unit_id=runtime_result.root_unit_id,
            selected_target_ai_unit_id=runtime_result.root_unit_id,
            attempt_id="attempt:hook",
            fault_type="executor_error",
            protocol_event_refs=event_refs,
            artifact_refs=(final_ref,),
            occurred_at="2026-08-01T00:00:00Z",
        ).to_dict(),
        build_experiment_ablation_gate_applied_observation(
            ablation_mode="NO_VERIFICATION",
            disabled_mechanism="verification",
            protocol_event_refs=event_refs,
            artifact_refs=(final_ref,),
            hook_input={
                "task_id": runtime_result.task_id,
                "unit_id": runtime_result.root_unit_id,
                "attempt_id": "attempt:hook",
                "lease_id": "lease:hook",
            },
            hook_result={"bypass": True, "stop": False},
        ).to_dict(),
        build_experiment_premature_merge_attempted_observation(
            attempt_schema_version="tokenshare.premature_merge_attempt.v1",
            run_id=runtime_result.run_id,
            task_id=runtime_result.task_id,
            parent_unit_id=runtime_result.root_unit_id,
            required_child_unit_ids=("child:canonical", "child:missing"),
            canonical_child_unit_ids=("child:canonical",),
            missing_child_unit_ids=("child:missing",),
            attempt_status="executed",
            plugin_result_type=None,
            plugin_error="merge gate was bypassed",
            root_check_passed=False,
            failure_kind="merge_readiness_unsatisfied",
            protocol_event_refs=event_refs,
            result_artifact_ref=final_ref,
        ).to_dict(),
    ]


def _project(
    inventory: PreregisteredRootInventoryManifest,
    condition_manifests: tuple[dict[str, Any], ...],
    catalogs: tuple[dict[str, Any], ...],
    evidence: dict[str, CanonicalDirectRootEvidence],
):
    return project_paper_direct_results(
        root_inventory_manifest=inventory,
        condition_manifests=condition_manifests,
        catalog_manifests=catalogs,
        canonical_runtime_evidence=tuple(evidence.values()),
    )


def _public_init_values(value: Any) -> dict[str, Any]:
    return {
        field.name: getattr(value, field.name)
        for field in fields(value)
        if field.init
    }


def test_public_row_cannot_mint_success_or_publishable_projection(
    tmp_path: Path,
) -> None:
    row, condition_manifest, catalog = _inventory_row()
    kwargs, *_ = _canonical_fixture(tmp_path, row)
    formal = build_canonical_direct_evidence(**kwargs)
    projection = _project(
        _inventory_manifest(row),
        (condition_manifest,),
        (catalog,),
        {row.preregistered_root_run_id: formal},
    )
    successful = projection.rows[0]
    assert successful.end_to_end_verified_success is True

    manual = CanonicalDirectRootEvidence._from_validated(
        **_public_init_values(formal)
    )
    forged_entries = (
        (
            lambda: CanonicalDirectRootEvidence(
                independently_verified_correct=True,
                paper_evidence_complete=True,
                identity_consistent=True,
            ),
            TypeError,
            None,
        ),
        (
            lambda: PaperDirectRootResult(**_public_init_values(successful)),
            ValueError,
            "canonical factory",
        ),
        (
            lambda: _project(
                _inventory_manifest(row),
                (condition_manifest,),
                (catalog,),
                {row.preregistered_root_run_id: manual},
            ),
            ValueError,
            "canonical evidence factory",
        ),
    )
    for forge, error_type, error_match in forged_entries:
        if error_match is None:
            with pytest.raises(error_type):
                forge()
        else:
            with pytest.raises(error_type, match=error_match):
                forge()

    public_projection = PaperDirectProjection(
        inventory_id=projection.inventory_id,
        inventory_digest=projection.inventory_digest,
        rows=projection.rows,
        denominator_inventory_ids=projection.denominator_inventory_ids,
    )
    with pytest.raises(ValueError, match="canonical factory projection"):
        build_direct_boolean_aggregate(
            public_projection,
            outcome_field="end_to_end_verified_success",
        )


def test_not_started_rejects_all_dedicated_event_refs(tmp_path: Path) -> None:
    observed, condition_manifest, catalog = _inventory_row()
    missing, _, _ = _inventory_row(root_id="inventory:not-started-special-ref")
    kwargs, *_ = _canonical_fixture(tmp_path, observed)
    projection = _project(
        _inventory_manifest(observed, missing),
        (condition_manifest,),
        (catalog,),
        {observed.preregistered_root_run_id: build_canonical_direct_evidence(**kwargs)},
    )
    terminal_ref = projection.rows[0].terminal_root_event_ref
    assert terminal_ref is not None
    values = _public_init_values(projection.rows[1])
    values["terminal_root_event_ref"] = terminal_ref

    with pytest.raises(ValueError, match="not_started row cannot contain execution evidence"):
        PaperDirectRootResult(**values)


def test_component_row_and_projection_deep_freeze_mutable_inputs(
    tmp_path: Path,
) -> None:
    row, condition_manifest, catalog = _inventory_row()
    kwargs, *_ = _canonical_fixture(tmp_path, row)
    formal = _project(
        _inventory_manifest(row),
        (condition_manifest,),
        (catalog,),
        {row.preregistered_root_run_id: build_canonical_direct_evidence(**kwargs)},
    ).rows[0]
    values = _public_init_values(formal)
    mutable_axes = dict(formal.condition_axes)
    mutable_reasons = ["component_fixture"]
    values.update(
        condition_axes=mutable_axes,
        root_status="ineligible",
        paper_evidence_complete=False,
        end_to_end_verified_success=False,
        infrastructure_valid=False,
        ineligibility_reasons=mutable_reasons,
    )
    component = PaperDirectRootResult(**values)
    mutable_axes["worker_count"] = 50
    mutable_reasons.append("mutated")
    assert component.condition_axes["worker_count"] == 3
    assert component.ineligibility_reasons == ("component_fixture",)
    with pytest.raises(TypeError):
        component.condition_axes["worker_count"] = 7

    mutable_rows = [component]
    mutable_denominator = [component.preregistered_root_run_id]
    public_projection = PaperDirectProjection(
        inventory_id="component-inventory",
        inventory_digest=_digest("component-inventory"),
        rows=mutable_rows,
        denominator_inventory_ids=mutable_denominator,
    )
    mutable_rows.clear()
    mutable_denominator.clear()
    assert public_projection.rows == (component,)
    assert public_projection.denominator_inventory_ids == (
        component.preregistered_root_run_id,
    )


def test_manifest_missing_root_wrong_digest_and_foreign_observed_are_rejected(
    tmp_path: Path,
) -> None:
    row, condition_manifest, catalog = _inventory_row()
    other, _, _ = _inventory_row(root_id="inventory:other")
    inventory = _inventory_manifest(row, other)
    with pytest.raises(ValueError, match="inventory digest"):
        replace(inventory, rows=(row,), root_count=1)
    with pytest.raises(ValueError, match="inventory digest"):
        replace(inventory, inventory_digest=_digest("wrong"))
    kwargs, *_ = _canonical_fixture(tmp_path, other)
    foreign = build_canonical_direct_evidence(**kwargs)
    with pytest.raises(ValueError, match="foreign observed"):
        _project(
            _inventory_manifest(row),
            (condition_manifest,),
            (catalog,),
            {other.preregistered_root_run_id: foreign},
        )
    with pytest.raises(ValueError, match="duplicate observed"):
        project_paper_direct_results(
            root_inventory_manifest=_inventory_manifest(other),
            condition_manifests=(condition_manifest,),
            catalog_manifests=(catalog,),
            canonical_runtime_evidence=(foreign, foreign),
        )


def test_condition_and_case_refs_are_digest_bound_and_ids_are_opaque() -> None:
    row, condition_manifest, catalog = _inventory_row(
        condition_id="opaque-w50-fault-model-other",
        case_id="case-claims-late-tail",
        worker_count=3,
    )
    direct = _project(
        _inventory_manifest(row),
        (condition_manifest,),
        (catalog,),
        {},
    ).rows[0]
    assert direct.condition_axes["worker_count"] == 3
    assert direct.preregistered_case_ref["factor_position_quantile"] == "early"
    drifted_values = {
        **row.to_dict(),
        "condition_axes": {**dict(row.condition_axes), "worker_count": 50},
    }
    drifted_values.pop("schema_version")
    drifted_values.pop("inventory_row_digest")
    with pytest.raises(ValueError, match="inventory row digest"):
        PaperDirectRootInventoryRow(
            **drifted_values,
            inventory_row_digest=row.inventory_row_digest,
        )


def test_case_id_and_catalog_order_never_reconstruct_position_stratum() -> None:
    row, condition_manifest, _ = _inventory_row(
        case_id="case-claims-late-tail"
    )
    catalogs = (
        _catalog(
            [
                ("case-claims-late-tail", "early", "front"),
                ("case-other", "late", "tail"),
            ]
        ),
        _catalog(
            [
                ("case-other", "late", "tail"),
                ("case-claims-late-tail", "early", "front"),
            ]
        ),
    )
    projected = []
    for catalog, records in catalogs:
        candidate = _replace_inventory_row(
            row,
            preregistered_case_ref=_case_ref(
                catalog,
                records[row.case_id],
            ),
        )
        projected.append(
            _project(
                _inventory_manifest(candidate),
                (condition_manifest,),
                (catalog,),
                {},
            ).rows[0]
        )
    assert [
        item.preregistered_case_ref["position_stratum"] for item in projected
    ] == ["front", "front"]


def test_cross_task_artifact_and_changed_hash_or_size_are_rejected(
    tmp_path: Path,
) -> None:
    row, _, _ = _inventory_row()
    kwargs, _, _, _, final_ref = _canonical_fixture(tmp_path, row)
    foreign = replace(final_ref, source={**final_ref.source, "task_id": "task:other"})
    with pytest.raises(ValueError, match="task binding"):
        build_canonical_direct_evidence(**{**kwargs, "final_result_ref": foreign})
    for changed in (
        replace(final_ref, content_hash=_digest("changed")),
        replace(final_ref, size_bytes=final_ref.size_bytes + 1),
    ):
        with pytest.raises(ValueError, match="artifact reference verification"):
            build_canonical_direct_evidence(
                **{**kwargs, "final_result_ref": changed}
            )


def test_completion_requires_canonical_runtime_final_and_merge_provenance(
    tmp_path: Path,
) -> None:
    row, condition_manifest, catalog = _inventory_row()
    kwargs, _, store, runtime_result, _ = _canonical_fixture(tmp_path, row)
    foreign_final = _save_role_artifact(
        store,
        label="foreign_final_same_task",
        role="final_result",
        execution_id=kwargs["execution_id"],
        task_id=runtime_result.task_id,
    )
    with pytest.raises(ValueError, match="canonical runtime result"):
        build_canonical_direct_evidence(
            **{**kwargs, "final_result_ref": foreign_final}
        )

    no_merge_kwargs, *_ = _canonical_fixture(
        tmp_path,
        _replace_inventory_row(row, preregistered_root_run_id="inventory:no-merge"),
        include_merge=False,
    )
    no_merge_row = no_merge_kwargs["inventory_row"]
    evidence = build_canonical_direct_evidence(**no_merge_kwargs)
    direct = _project(
        _inventory_manifest(no_merge_row),
        (condition_manifest,),
        (catalog,),
        {no_merge_row.preregistered_root_run_id: evidence},
    ).rows[0]
    assert direct.final_result_reference_complete is False
    assert "missing_merge_provenance" in direct.ineligibility_reasons


def test_terminal_root_ref_rejects_wrong_event_type_or_nonterminal_state(
    tmp_path: Path,
) -> None:
    cases = (
        (EventType.TASK_UNIT_CREATED, "Completed"),
        (EventType.TASK_UNIT_STATE_CHANGED, "Processing"),
    )
    for index, (event_type, state) in enumerate(cases):
        row, _, _ = _inventory_row(root_id=f"inventory:terminal:{index}")
        kwargs, *_ = _canonical_fixture(
            tmp_path,
            row,
            terminal_event_type=event_type,
            terminal_state=state,
        )

        evidence = build_canonical_direct_evidence(**kwargs)

        assert evidence.terminal_root_event_ref is None
        assert "missing_terminal_root_provenance" in evidence.ineligibility_reasons
        assert evidence.paper_evidence_complete is False


def test_duplicate_seq_hash_bad_prev_chain_and_ledger_swap_are_rejected(
    tmp_path: Path,
) -> None:
    row, _, _ = _inventory_row()
    kwargs, ledger, _, _, _ = _canonical_fixture(tmp_path, row)
    lines = ledger.path.read_text(encoding="utf-8").splitlines()
    original = ledger.path.read_text(encoding="utf-8")
    for mutation in ("duplicate_seq", "duplicate_hash", "bad_prev"):
        bodies = [json.loads(line) for line in lines]
        if mutation == "duplicate_seq":
            bodies[1]["event_seq"] = bodies[0]["event_seq"]
        elif mutation == "duplicate_hash":
            bodies[1]["event_hash"] = bodies[0]["event_hash"]
        else:
            bodies[1]["prev_event_hash"] = _digest("wrong-prev")
        ledger.path.write_text(
            "".join(json.dumps(body) + "\n" for body in bodies),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="ledger hash chain|duplicate"):
            build_canonical_direct_evidence(**kwargs)
        ledger.path.write_text(original, encoding="utf-8")

    other, _, _ = _inventory_row(root_id="inventory:other-ledger")
    other_kwargs, other_ledger, *_ = _canonical_fixture(tmp_path, other)
    with pytest.raises(ValueError, match="runtime result.*ledger|task binding"):
        build_canonical_direct_evidence(
            **{**kwargs, "event_ledger": other_ledger}
        )


def test_runtime_result_requires_exact_producer_ledger_binding_and_summary(
    tmp_path: Path,
) -> None:
    row, _, _ = _inventory_row()
    kwargs, ledger, store, runtime_result, _ = _canonical_fixture(tmp_path, row)
    swapped = _replay_ledger_with_payload_change(
        ledger,
        path=ledger.path.parent / "swapped-ledger.jsonl",
        changed_event_seq=2,
    )
    swapped_result = project_protocol_run(
        run_id=runtime_result.run_id,
        task_id=runtime_result.task_id,
        root_unit_id=runtime_result.root_unit_id,
        event_ledger=swapped,
        artifact_store=store,
    )
    assert swapped_result.status == runtime_result.status
    assert swapped_result.event_refs == runtime_result.event_refs
    assert swapped_result.artifact_refs == runtime_result.artifact_refs
    assert swapped_result.summary == runtime_result.summary
    assert swapped_result.ledger_binding != runtime_result.ledger_binding

    with pytest.raises(ValueError, match="producer ledger binding"):
        build_canonical_direct_evidence(**{**kwargs, "event_ledger": swapped})
    with pytest.raises(ValueError, match="producer ledger binding"):
        build_canonical_direct_evidence(
            **{
                **kwargs,
                "runtime_result": replace(runtime_result, ledger_binding=None),
            }
        )
    with pytest.raises(ValueError, match="runtime result summary"):
        build_canonical_direct_evidence(
            **{
                **kwargs,
                "runtime_result": replace(
                    runtime_result,
                    summary={
                        **runtime_result.summary,
                        "event_count": runtime_result.summary["event_count"] + 1,
                    },
                ),
            }
        )
    with pytest.raises((TypeError, ValueError), match="hook observations"):
        build_canonical_direct_evidence(
            **{
                **kwargs,
                "runtime_result": replace(
                    runtime_result,
                    summary={
                        **runtime_result.summary,
                        "runtime_hook_observations": [
                            {
                                "unvalidated_kind": "forged",
                                "arbitrary_payload": {"value": 1},
                            }
                        ],
                    },
                ),
            }
        )
    with pytest.raises(ValueError, match="runtime result summary"):
        build_canonical_direct_evidence(
            **{
                **kwargs,
                "runtime_result": replace(
                    runtime_result,
                    summary={
                        **runtime_result.summary,
                        "unexpected_summary_field": True,
                    },
                ),
            }
        )


def test_runtime_result_accepts_all_official_typed_hook_observations(
    tmp_path: Path,
) -> None:
    row, _, _ = _inventory_row()
    kwargs, _, _, runtime_result, final_ref = _canonical_fixture(tmp_path, row)
    observations = _official_runtime_hook_observations(runtime_result, final_ref)

    evidence = build_canonical_direct_evidence(
        **{
            **kwargs,
            "runtime_result": replace(
                runtime_result,
                summary={
                    **runtime_result.summary,
                    "runtime_hook_observations": observations,
                },
            ),
        }
    )

    assert [item["kind"] for item in observations] == [
        "EXPERIMENT_FAULT_INJECTED",
        "EXPERIMENT_ABLATION_GATE_APPLIED",
        "EXPERIMENT_PREMATURE_MERGE_ATTEMPTED",
    ]
    assert evidence.paper_evidence_complete is True


def test_runtime_result_rejects_noncanonical_typed_hook_observations(
    tmp_path: Path,
) -> None:
    row, _, _ = _inventory_row()
    kwargs, _, _, runtime_result, final_ref = _canonical_fixture(tmp_path, row)
    canonical = _official_runtime_hook_observations(runtime_result, final_ref)[-1]
    invalid_observations: list[dict[str, Any]] = []

    unknown_schema = json.loads(json.dumps(canonical))
    unknown_schema["schema_version"] = "tokenshare.runtime_hook_observation.v999"
    invalid_observations.append(unknown_schema)
    unknown_kind = json.loads(json.dumps(canonical))
    unknown_kind["kind"] = "EXPERIMENT_FORGED"
    invalid_observations.append(unknown_kind)
    unknown_top_key = json.loads(json.dumps(canonical))
    unknown_top_key["unexpected"] = True
    invalid_observations.append(unknown_top_key)
    unknown_payload_key = json.loads(json.dumps(canonical))
    unknown_payload_key["payload"]["unexpected"] = True
    invalid_observations.append(unknown_payload_key)
    non_strict_scalar = json.loads(json.dumps(canonical))
    non_strict_scalar["payload"]["root_check_passed"] = 1
    invalid_observations.append(non_strict_scalar)
    invalid_ref = json.loads(json.dumps(canonical))
    invalid_ref["payload"]["result_artifact_ref"]["size_bytes"] = False
    invalid_observations.append(invalid_ref)
    digest_mismatch = json.loads(json.dumps(canonical))
    digest_mismatch["observation_digest"] = f"sha256:{'0' * 64}"
    invalid_observations.append(digest_mismatch)
    invalid_observations.append(
        {
            "schema_version": canonical["schema_version"],
            "kind": canonical["kind"],
            "payload": {"unvalidated_kind": "forged", "arbitrary_payload": 1},
            "observation_digest": canonical["observation_digest"],
        }
    )

    for invalid in invalid_observations:
        with pytest.raises((TypeError, ValueError), match="hook observations"):
            build_canonical_direct_evidence(
                **{
                    **kwargs,
                    "runtime_result": replace(
                        runtime_result,
                        summary={
                            **runtime_result.summary,
                            "runtime_hook_observations": [invalid],
                        },
                    ),
                }
            )


def test_attempt_chain_rejects_cross_unit_or_noncanonical_unit_identity(
    tmp_path: Path,
) -> None:
    cases = (
        {3: "unit:foreign"},
        {2: "unit:foreign", 3: "unit:foreign", 4: "unit:foreign"},
    )
    for index, units_by_event_seq in enumerate(cases):
        row, _, _ = _inventory_row(root_id=f"inventory:cross-unit:{index}")
        kwargs, ledger, store, runtime_result, _ = _canonical_fixture(tmp_path, row)
        swapped = _replay_ledger_with_attempt_unit_changes(
            ledger,
            path=ledger.path.parent / f"cross-unit-ledger-{index}.jsonl",
            units_by_event_seq=units_by_event_seq,
        )
        swapped_result = project_protocol_run(
            run_id=runtime_result.run_id,
            task_id=runtime_result.task_id,
            root_unit_id=runtime_result.root_unit_id,
            event_ledger=swapped,
            artifact_store=store,
        )

        evidence = build_canonical_direct_evidence(
            **{
                **kwargs,
                "event_ledger": swapped,
                "runtime_result": swapped_result,
            }
        )

        assert evidence.identity_consistent is False
        assert "missing_bound_attempt_chain" in evidence.ineligibility_reasons
        assert evidence.paper_evidence_complete is False


def test_zero_verifier_refs_cannot_succeed_and_blocks_publish(tmp_path: Path) -> None:
    row, condition_manifest, catalog = _inventory_row()
    kwargs, *_ = _canonical_fixture(tmp_path, row, include_verdict=False)
    evidence = build_canonical_direct_evidence(**kwargs)
    assert evidence.paper_evidence_complete is False
    assert evidence.independently_verified_correct is False
    projection = _project(
        _inventory_manifest(row),
        (condition_manifest,),
        (catalog,),
        {row.preregistered_root_run_id: evidence},
    )
    direct = projection.rows[0]
    assert direct.root_status == "ineligible"
    assert "missing_independent_verdict" in direct.ineligibility_reasons
    assert direct.end_to_end_verified_success is False
    assert build_direct_boolean_aggregate(
        projection,
        outcome_field="end_to_end_verified_success",
    ).status == "blocked"


def test_completion_and_e2e_four_gates_are_derived_from_canonical_evidence(
    tmp_path: Path,
) -> None:
    row, condition_manifest, catalog = _inventory_row()
    kwargs, *_ = _canonical_fixture(tmp_path, row)
    correct = build_canonical_direct_evidence(**kwargs)
    direct = _project(
        _inventory_manifest(row),
        (condition_manifest,),
        (catalog,),
        {row.preregistered_root_run_id: correct},
    ).rows[0]
    assert direct.final_result_reference_complete is True
    assert direct.independently_verified_correct is True
    assert direct.paper_evidence_complete is True
    assert direct.identity_consistent is True
    assert direct.end_to_end_verified_success is True

    wrong_row, _, _ = _inventory_row(root_id="inventory:wrong")
    wrong_kwargs, *_ = _canonical_fixture(tmp_path, wrong_row, correct=False)
    # 独立 verdict=false 是证据完整的模型错误，不是 infra-invalid。
    wrong = build_canonical_direct_evidence(**wrong_kwargs)
    assert wrong.paper_evidence_complete is True
    assert wrong.independently_verified_correct is False
    assert wrong.ineligibility_reasons == ()


def test_nested_input_mutation_does_not_change_inventory_or_evidence_snapshot(
    tmp_path: Path,
) -> None:
    row, _, _ = _inventory_row()
    kwargs, *_ = _canonical_fixture(tmp_path, row)
    source = kwargs["final_result_ref"].source
    evidence = build_canonical_direct_evidence(**kwargs)
    before = evidence.final_result_ref.to_dict()
    source["task_id"] = "task:mutated"
    assert evidence.final_result_ref.to_dict() == before
    with pytest.raises(TypeError):
        row.condition_axes["worker_count"] = 50


def test_strict_unknown_bool_int_and_duplicate_locator_inputs_are_rejected(
    tmp_path: Path,
) -> None:
    row, _, catalog = _inventory_row()
    values = row.to_dict()
    values.pop("schema_version")
    values["unknown"] = "no"
    with pytest.raises(TypeError):
        PaperDirectRootInventoryRow(**values)
    values.pop("unknown")
    values["repeat_id"] = True
    with pytest.raises(ValueError, match="repeat_id"):
        PaperDirectRootInventoryRow(**values)
    with pytest.raises(ValueError, match="finite"):
        _replace_inventory_row(
            row,
            condition_axes={
                **dict(row.condition_axes),
                "sample_slot_index": float("nan"),
            },
        )
    with pytest.raises(ValueError, match="role"):
        ExternalBankObjectLocator(
            bank_root_id="approved-bank",
            manifest_digest=_digest("manifest"),
            entry_id="entry-1",
            object_role="unknown_role",
            object_digest=_digest("unknown"),
        )

    bad_axes = {**dict(row.condition_axes), "worker_count": True}
    condition_manifest, condition_ref = _condition_manifest(
        row.condition_id,
        bad_axes,
    )
    candidate = _replace_inventory_row(
        row,
        condition_axes=bad_axes,
        preregistered_condition_ref=condition_ref,
    )
    with pytest.raises(ValueError, match="worker_count"):
        _project(
            _inventory_manifest(candidate),
            (condition_manifest,),
            (catalog,),
            {},
        )

    trace, _, _ = _inventory_row(evidence_class="real_model_trace_protocol_run")
    kwargs, _, store, runtime_result, _ = _canonical_fixture(tmp_path, trace)
    reversed_locators = tuple(reversed(kwargs["source_bank_object_locators"]))
    evidence = build_canonical_direct_evidence(
        **{**kwargs, "source_bank_object_locators": reversed_locators}
    )
    assert evidence.source_bank_object_locators == tuple(
        sorted(
            reversed_locators,
            key=lambda item: (item.entry_id, item.object_role, item.object_digest),
        )
    )
    first = next(
        locator
        for locator in kwargs["source_bank_object_locators"]
        if locator.object_role == "raw_output_or_provider_failure"
    )
    with pytest.raises(ValueError, match="duplicate.*role"):
        build_canonical_direct_evidence(
            **{
                **kwargs,
                "source_bank_object_locators": (
                    *kwargs["source_bank_object_locators"],
                    first,
                ),
            }
        )
    current_ref = _save_role_artifact(
        store,
        label="current_for_exclusivity",
        role="request_body",
        execution_id=kwargs["execution_id"],
        task_id=runtime_result.task_id,
    )
    with pytest.raises(ValueError, match="mutually exclusive"):
        build_canonical_direct_evidence(
            **{
                **kwargs,
                "current_provider_object_refs": (current_ref,),
            }
        )


def test_regression_only_cannot_be_upgraded_to_paper_evidence(tmp_path: Path) -> None:
    row, condition_manifest, catalog = _inventory_row(
        evidence_class="regression_only"
    )
    kwargs, *_ = _canonical_fixture(tmp_path, row)
    evidence = build_canonical_direct_evidence(**kwargs)
    projection = _project(
        _inventory_manifest(row),
        (condition_manifest,),
        (catalog,),
        {row.preregistered_root_run_id: evidence},
    )
    assert projection.rows[0].paper_eligible is False
    assert "regression_only" in projection.rows[0].ineligibility_reasons
    with pytest.raises(TypeError, match="evidence_class"):
        build_canonical_direct_evidence(
            **{**kwargs, "evidence_class": "real_model_trace_protocol_run"}
        )


def test_four_root_hand_calculation_keeps_denominator_four_and_blocks(
    tmp_path: Path,
) -> None:
    _, condition_manifest, catalog = _inventory_row()
    rows = tuple(
        _inventory_row(root_id=f"inventory:{kind}")[0]
        for kind in ("correct", "wrong", "invalid", "not-started")
    )
    correct_kwargs, *_ = _canonical_fixture(tmp_path, rows[0])
    wrong_kwargs, *_ = _canonical_fixture(tmp_path, rows[1], correct=False)
    invalid_kwargs, *_ = _canonical_fixture(
        tmp_path,
        rows[2],
        completed=False,
        include_verdict=False,
    )
    projection = _project(
        _inventory_manifest(*rows),
        (condition_manifest,),
        (catalog,),
        {
            rows[0].preregistered_root_run_id: build_canonical_direct_evidence(
                **correct_kwargs
            ),
            rows[1].preregistered_root_run_id: build_canonical_direct_evidence(
                **wrong_kwargs
            ),
            rows[2].preregistered_root_run_id: build_canonical_direct_evidence(
                **invalid_kwargs
            ),
        },
    )
    aggregate = build_direct_boolean_aggregate(
        projection,
        outcome_field="end_to_end_verified_success",
    )
    assert len(projection.rows) == 4
    assert projection.denominator_inventory_ids == tuple(
        row.preregistered_root_run_id for row in rows
    )
    not_started = projection.rows[3]
    assert not_started.root_status == "not_started"
    assert not_started.execution_binding is None
    assert not_started.event_refs == ()
    assert not_started.artifact_refs == ()
    assert not_started.current_provider_object_refs == ()
    assert not_started.source_bank_object_locators == ()
    assert not_started.actual_resource_book_ref is None
    assert not_started.trace_resource_book_ref is None
    assert projection.provider_calls == 0
    assert projection.recompute_only is True
    assert projection.paper_eligible is False
    assert sum(row.final_result_reference_complete for row in projection.rows) == 2
    assert sum(row.end_to_end_verified_success for row in projection.rows) == 1
    assert aggregate.audit_denominator_count == 4
    assert aggregate.status == "blocked"
    assert aggregate.value is None
