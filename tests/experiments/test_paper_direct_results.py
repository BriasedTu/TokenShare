"""Task 3：canonical typed evidence 与固定 inventory 分母。"""

from __future__ import annotations

import json
from dataclasses import fields, replace
from hashlib import sha256
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
    persist_native_online_direct_artifacts,
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
from tokenshare.plugins.lean_proof.checker import render_lean_source
from tokenshare.plugins.lean_proof.models import LeanTheoremPayload
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType


def _digest(label: str) -> str:
    return digest_json({"label": label})


def _test_lean_root_theorem_body(case_id: str) -> dict[str, Any]:
    body = {
        "schema_version": "lean_proof.theorem_payload.v1",
        "theorem_id": f"lean_theorem:{case_id}",
        "theorem_name": case_id.replace("-", "_"),
        "imports": ["Init"],
        "namespace": "TokenSharePaperCatalog",
        "open_namespaces": [],
        "options": {},
        "parameters_source": "",
        "statement_source": "True",
        "theorem_source": None,
        "proof_candidate_ref": None,
        "library_context": {"case_id": case_id},
        "decomposition_policy": {
            "policy_id": "lean_proof.deterministic_tactic_split.v1",
            "allowed_rules": ["conjunction"],
            "max_depth": 1,
            "max_children": 2,
            "unsupported_policy": "return_unsupported",
        },
        "resource_limits": {"timeout_seconds": 30, "max_output_bytes": 65536},
    }
    body["payload_digest"] = digest_json(body)
    return body


def _test_lean_official_case_digest(case_id: str) -> str:
    return digest_json(
        {
            "schema_version": "tokenshare.test_lean_official_case.v1",
            "case_id": case_id,
            "root_theorem_payload": _test_lean_root_theorem_body(case_id),
        }
    )


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
    if values["condition_axes"].get("domain") == "lean_proof":
        theorem_body = _test_lean_root_theorem_body(values["case_id"])
        case_ref = dict(values["preregistered_case_ref"])
        case_ref["schema_version"] = "tokenshare.preregistered_lean_case_ref.v1"
        defaults = {
            "official_case_digest": _test_lean_official_case_digest(
                values["case_id"]
            ),
            "official_root_theorem_id": theorem_body["theorem_id"],
            "official_root_theorem_payload_digest": theorem_body[
                "payload_digest"
            ],
        }
        for field_name, field_value in defaults.items():
            case_ref.setdefault(field_name, field_value)
        values["preregistered_case_ref"] = case_ref
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
    source_extra: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
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
            **(source_extra or {}),
        },
        metadata=metadata or {},
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


def _rewrite_durable_manifest(path: Path, body: dict[str, Any]) -> None:
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    path.write_bytes(encoded)
    marker = {
        "schema_version": "tokenshare.durable_file_commit.v1",
        "target_name": path.name,
        "content_hash": f"sha256:{sha256(encoded).hexdigest()}",
        "size_bytes": len(encoded),
    }
    path.with_name(f"{path.name}.commit.json").write_text(
        json.dumps(marker, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
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
    merge_unit_canonical_final: bool = False,
    merge_selection_id: str = "canonical:merge-unit",
    duplicate_merge_canonical: bool = False,
    merge_commitment_tamper: str | None = None,
    lean_proof_bytes: bytes | None = None,
    lean_proof_artifact_type: str = "LeanProofArtifact",
    include_verification_event: bool = True,
    duplicate_verification_event: bool = False,
    verification_event_tamper: str | None = None,
):
    root_id = row.preregistered_root_run_id
    execution_id = f"execution:{root_id}"
    selected_class = evidence_class or row.evidence_class
    task_id = f"task:{root_id}"
    unit_id = f"unit:{root_id}"
    store = ArtifactStore(tmp_path / root_id.replace(":", "_"))
    ledger = EventLedger(store.root_path / "events" / "ledger.jsonl")
    lean_checker_request_id = f"request:{row.case_id}"
    expected_proof_source = "by\n  trivial\n"
    lean_theorem_body = _test_lean_root_theorem_body(row.case_id)
    lean_proof_digest = digest_json(
        {
            "theorem_payload_digest": lean_theorem_body["payload_digest"],
            "proof_source": expected_proof_source,
        }
    )
    lean_checker_proof_ref = (
        store.save_bytes(
            lean_proof_bytes or expected_proof_source.encode("utf-8"),
            artifact_id=f"checker_proof_{root_id.replace(':', '_')}",
            artifact_type=lean_proof_artifact_type,
            media_type="text/x-lean",
            artifact_schema_id="lean_proof.proof_artifact",
            artifact_schema_version="v1",
            source={"kind": "lean_checker", "request_id": lean_checker_request_id},
            metadata={"proof_digest": lean_proof_digest},
            created_at="2026-08-01T00:00:00Z",
        )
        if row.condition_axes.get("domain") == "lean_proof"
        else None
    )
    final_ref = _save_role_artifact(
        store,
        label=f"final_{root_id.replace(':', '_')}",
        role="final_result",
        execution_id=execution_id,
        task_id=task_id,
        body={
            "schema_version": "factorization.prime_factorization_result.v1",
            "target_n": "91",
            "prime_factors": [
                {"prime": "7", "exponent": 1},
                {"prime": "13" if correct else "11", "exponent": 1},
            ],
        },
        source_extra=(
            {"source_ref": lean_checker_proof_ref.to_dict()}
            if lean_checker_proof_ref is not None
            else None
        ),
    )
    parser_ref = _save_role_artifact(
        store,
        label=f"parser_{root_id.replace(':', '_')}",
        role="parser_result",
        execution_id=execution_id,
        task_id=task_id,
    )
    verdict_ref = _factor_domain_verdict_ref(
        store=store,
        row=row,
        execution_id=execution_id,
        task_id=task_id,
        root_unit_id=unit_id,
        final_ref=final_ref,
        correct=correct,
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
    merge_unit_id = f"merge:{unit_id}"
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
    if include_verification_event:
        domain = str(row.condition_axes.get("domain"))
        validator_policy_id = {
            "factorization": "factorization.merge_result.validator.v1",
            "lean_proof": "lean_proof.checker.validator.v1",
        }[domain]
        verification_report = {
            "task_id": task_id,
            "unit_id": unit_id,
            "plugin_id": domain,
            "validator_policy_id": validator_policy_id,
            "status": "passed",
            "eligible_for_canonical": True,
            "candidate_output_refs": {"answer": final_ref.to_dict()},
        }
        verification_payload = {
            "schema_version": "phase4.verification_record.v1",
            "task_id": task_id,
            "unit_id": unit_id,
            "attempt_id": attempt_id,
            "state": "Verified",
            "plugin_id": domain,
            "validator_policy_id": validator_policy_id,
            "status": "passed",
            "eligible_for_canonical": True,
            "verification_report": verification_report,
        }
        if verification_event_tamper == "plugin":
            verification_payload["plugin_id"] = "factorization"
            verification_report["plugin_id"] = "factorization"
        verification_payload["verification_report_digest"] = digest_json(
            verification_report
        )
        for index in range(2 if duplicate_verification_event else 1):
            _append_event(
                ledger,
                task_id=task_id,
                event_type=EventType.VERIFICATION_RECORDED,
                suffix=f"verification-{index}",
                payload=verification_payload,
            )
    root_canonical_ref = final_ref
    if merge_unit_canonical_final:
        root_canonical_ref = _save_role_artifact(
            store,
            label=f"root_marker_{root_id.replace(':', '_')}",
            role="root_marker",
            execution_id=execution_id,
            task_id=task_id,
        )
        _append_event(
            ledger,
            task_id=task_id,
            event_type=EventType.TASK_UNIT_CREATED,
            suffix="merge-unit-created",
            payload={"task_unit": {"unit_id": merge_unit_id, "task_id": task_id, "state": "Ready"}},
        )
    _append_event(
        ledger,
        task_id=task_id,
        event_type=EventType.CANONICAL_OUTPUTS_BOUND,
        suffix="canonical",
        payload={
            "canonical_selection": {
                "unit_id": unit_id,
                "canonical_output_refs": {"answer": root_canonical_ref.to_dict()},
            }
        },
    )
    merge_canonical_event_seq: int | None = None
    if merge_unit_canonical_final:
        merge_canonical_refs = {"answer": final_ref.to_dict()}
        if merge_commitment_tamper == "canonical_refs_extra":
            merge_canonical_refs["extra"] = final_ref.to_dict()
        for index in range(2 if duplicate_merge_canonical else 1):
            selection_id = merge_selection_id if index == 0 else f"{merge_selection_id}:duplicate"
            _append_event(
                ledger,
                task_id=task_id,
                event_type=EventType.CANONICAL_OUTPUTS_BOUND,
                suffix=f"merge-unit-canonical-{index}",
                payload={
                    "canonical_selection": {
                        "canonical_selection_id": selection_id,
                        "unit_id": merge_unit_id,
                        "canonical_output_refs": merge_canonical_refs,
                    }
                },
            )
            if index == 0:
                merge_canonical_event_seq = ledger.read_all()[-1].event_seq
    if include_merge:
        top_refs = {"answer": final_ref.to_dict()}
        record_refs = {"answer": final_ref.to_dict()}
        canonical_seq = merge_canonical_event_seq
        top_merge_unit = merge_unit_id
        record_merge_unit = merge_unit_id
        top_selection = merge_selection_id
        record_selection = merge_selection_id
        if merge_commitment_tamper == "top_merge_unit":
            top_merge_unit = f"{merge_unit_id}:tampered"
        elif merge_commitment_tamper == "record_merge_unit_missing":
            record_merge_unit = None
        elif merge_commitment_tamper == "top_selection":
            top_selection = "canonical:tampered-top"
        elif merge_commitment_tamper == "record_selection_missing":
            record_selection = None
        elif merge_commitment_tamper == "canonical_event_seq":
            canonical_seq = (canonical_seq or 0) + 1
        elif merge_commitment_tamper == "top_refs_extra":
            top_refs["extra"] = final_ref.to_dict()
        elif merge_commitment_tamper == "record_refs_renamed":
            record_refs = {"renamed": final_ref.to_dict()}
        merge_record = {
            "task_id": task_id,
            "parent_unit_id": unit_id,
            "merge_unit_id": record_merge_unit,
            "canonical_selection_id": record_selection,
            "canonical_event_seq": merge_canonical_event_seq,
            "merge_output_refs": record_refs,
        }
        if merge_commitment_tamper == "record_event_seq_missing":
            merge_record.pop("canonical_event_seq")
        _append_event(
            ledger,
            task_id=task_id,
            event_type=EventType.MERGE_RECORDED,
            suffix="merge",
            payload={
                "schema_version": "phase5.merge_recorded.v1",
                "task_id": task_id,
                "parent_unit_id": unit_id,
                "merge_unit_id": top_merge_unit,
                "canonical_selection_id": top_selection,
                "canonical_event_seq": canonical_seq,
                "merge_output_refs": top_refs,
                "merge_record": merge_record,
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


def _factor_domain_verdict_ref(
    *,
    store: ArtifactStore,
    row: PaperDirectRootInventoryRow,
    execution_id: str,
    task_id: str,
    root_unit_id: str,
    final_ref: ArtifactRef,
    correct: bool,
    final_binding_ref: ArtifactRef | None = None,
) -> ArtifactRef:
    environment_ref = {
        "schema_version": "phase3.environment_ref.v1",
        "environment_id": "env_factorization_runtime",
        "environment_digest": "sha256:env_factorization_runtime",
        "runtime": "python",
        "tool_versions": {"factorization_plugin": "1.0.0"},
        "resource_limits": {"timeout_seconds": 600},
        "fixture_profile_digest": "sha256:factorization_runtime",
        "seed": 1,
        "clock_policy": "fixed",
        "created_at": "2026-08-01T00:00:00Z",
    }
    checks = {
        "target_matches": True,
        "canonical_factor_encoding": True,
        "factor_product_matches": correct,
        "all_factors_prime": True,
    }
    body = {
        "schema_version": "tokenshare.paper_factorization_domain_verifier_report.v1",
        "case_id": row.case_id,
        "execution_id": execution_id,
        "task_id": task_id,
        "root_unit_id": root_unit_id,
        "final_result_ref": (final_binding_ref or final_ref).to_dict(),
        "environment_ref": environment_ref,
        "verifier": {
            "verifier_id": "factorization.prime_factorization_result.verifier",
            "verifier_version": "v1",
        },
        "target_n": "91",
        "checks": checks,
        "status": "accepted" if correct else "rejected",
        "correct": correct,
    }
    body["report_digest"] = digest_json(body)
    return store.save_json(
        body,
        artifact_id=f"factor_domain_verdict_{row.case_id}_{correct}",
        artifact_type="FactorizationDomainVerifierReport",
        artifact_schema_id="tokenshare.paper_factorization_domain_verifier_report",
        artifact_schema_version="v1",
        source={
            "kind": "factorization_domain_verifier",
            "role": "independent_verdict",
            "case_id": row.case_id,
            "execution_id": execution_id,
            "task_id": task_id,
            "root_unit_id": root_unit_id,
            "final_result_ref": (final_binding_ref or final_ref).to_dict(),
            "environment_digest": environment_ref["environment_digest"],
        },
        metadata={"status": body["status"], "correct": correct},
        created_at="2026-08-01T00:00:00Z",
    )


def _lean_domain_verdict_refs(
    *,
    store: ArtifactStore,
    row: PaperDirectRootInventoryRow,
    execution_id: str,
    task_id: str,
    root_unit_id: str,
    final_ref: ArtifactRef,
    status: str,
    binding_final_ref: ArtifactRef | None = None,
    binding_report_ref: ArtifactRef | None = None,
    binding_status: str | None = None,
    binding_environment_digest: str | None = None,
    missing_checker_ref: str | None = None,
    report_request_id: str | None = None,
    checker_source_request_id: str | None = None,
    report_normalized_theorem_digest: str | None = None,
    report_proof_digest: str | None = None,
    theorem_body_override: dict[str, Any] | None = None,
    generated_source_bytes: bytes | None = None,
    generated_source_artifact_type: str = "LeanGeneratedSource",
    ledger: EventLedger | None = None,
    canonical_selection_tamper: str | None = None,
    verification_metadata_tamper: str | None = None,
) -> tuple[ArtifactRef, ArtifactRef]:
    environment_ref = {
        "schema_version": "phase3.environment_ref.v1",
        "environment_id": "env_lean_runtime",
        "environment_digest": "sha256:env_lean_runtime",
        "runtime": "lean",
        "tool_versions": {"lean": "4.19.0"},
        "resource_limits": {"timeout_seconds": 600},
        "fixture_profile_digest": "sha256:lean_runtime",
        "seed": 1,
        "clock_policy": "fixed",
        "created_at": "2026-08-01T00:00:00Z",
    }
    request_id = f"request:{row.case_id}"
    source_request_id = checker_source_request_id or request_id
    theorem_body = theorem_body_override or _test_lean_root_theorem_body(row.case_id)
    root_theorem_ref = _save_role_artifact(
        store,
        label=f"root_theorem_{row.case_id}_{status}",
        role="protocol_runtime_artifact",
        execution_id=execution_id,
        task_id=task_id,
        body=theorem_body,
        source_extra={"case_id": row.case_id},
        metadata={
            "case_id": row.case_id,
            "output_name": "lean_theorem_payload",
        },
    )
    normalized_theorem_digest = digest_json(
        {
            "theorem_name": theorem_body["theorem_name"],
            "imports": theorem_body["imports"],
            "namespace": theorem_body["namespace"],
            "parameters_source": theorem_body["parameters_source"],
            "statement_source": theorem_body["statement_source"],
        }
    )
    proof_ref = ArtifactRef.from_dict(final_ref.source["source_ref"])
    checker_refs: dict[str, ArtifactRef] = {
        "stdout_ref": store.save_bytes(
            b"checker ok\n",
            artifact_id=f"checker_stdout_{row.case_id}_{status}",
            artifact_type="LeanCheckerStdout",
            media_type="text/plain",
            artifact_schema_id="lean_proof.checker_log",
            artifact_schema_version="v1",
            source={"kind": "lean_checker", "request_id": source_request_id},
            metadata={},
            created_at="2026-08-01T00:00:00Z",
        ),
        "stderr_ref": store.save_bytes(
            b"",
            artifact_id=f"checker_stderr_{row.case_id}_{status}",
            artifact_type="LeanCheckerStderr",
            media_type="text/plain",
            artifact_schema_id="lean_proof.checker_log",
            artifact_schema_version="v1",
            source={"kind": "lean_checker", "request_id": source_request_id},
            metadata={},
            created_at="2026-08-01T00:00:00Z",
        ),
        "generated_source_ref": store.save_bytes(
            generated_source_bytes
            or render_lean_source(
                LeanTheoremPayload.from_dict(theorem_body),
                store.read_bytes(proof_ref).decode("utf-8"),
            ).encode("utf-8"),
            artifact_id=f"checker_generated_{row.case_id}_{status}",
            artifact_type=generated_source_artifact_type,
            media_type="text/x-lean",
            artifact_schema_id="lean_proof.generated_source",
            artifact_schema_version="v1",
            source={"kind": "lean_checker", "request_id": source_request_id},
            metadata={},
            created_at="2026-08-01T00:00:00Z",
        ),
    }
    proof_digest = str(proof_ref.metadata["proof_digest"])
    report_body = {
        "schema_version": "lean_proof.checker_report.v1",
        "report_id": f"report:{row.case_id}:{status}",
        "request_id": report_request_id or request_id,
        "status": status,
        "exit_code": 0 if status == "accepted" else 1,
        "stdout_ref": (
            None if missing_checker_ref == "stdout_ref" else checker_refs["stdout_ref"].to_dict()
        ),
        "stderr_ref": (
            None if missing_checker_ref == "stderr_ref" else checker_refs["stderr_ref"].to_dict()
        ),
        "generated_source_ref": (
            None
            if missing_checker_ref == "generated_source_ref"
            else checker_refs["generated_source_ref"].to_dict()
        ),
        "proof_artifact_ref": (
            None
            if status != "accepted" or missing_checker_ref == "proof_artifact_ref"
            else proof_ref.to_dict()
        ),
        "diagnostics": {},
        "normalized_theorem_digest": (
            report_normalized_theorem_digest or normalized_theorem_digest
        ),
        "proof_digest": (
            (report_proof_digest or proof_digest) if status == "accepted" else None
        ),
        "environment_ref": environment_ref,
        "command_summary": {"backend": "test"},
        "duration_ms": 1,
    }
    report_ref = _save_role_artifact(
        store,
        label=f"root_checker_{row.case_id}_{status}",
        role="root_checker_report",
        execution_id=execution_id,
        task_id=task_id,
        body=report_body,
        source_extra={"request_id": source_request_id},
    )
    bound_report_ref = binding_report_ref or report_ref
    binding_body = {
        "schema_version": "tokenshare.paper_lean_domain_verdict_binding.v2",
        "case_id": row.case_id,
        "execution_id": execution_id,
        "task_id": task_id,
        "root_unit_id": root_unit_id,
        "final_result_ref": (binding_final_ref or final_ref).to_dict(),
        "root_checker_report_ref": bound_report_ref.to_dict(),
        "root_theorem_payload_ref": root_theorem_ref.to_dict(),
        "official_case_digest": row.preregistered_case_ref[
            "official_case_digest"
        ],
        "official_root_theorem_id": row.preregistered_case_ref[
            "official_root_theorem_id"
        ],
        "official_root_theorem_payload_digest": row.preregistered_case_ref[
            "official_root_theorem_payload_digest"
        ],
        "normalized_theorem_digest": normalized_theorem_digest,
        "environment_digest": (
            binding_environment_digest or environment_ref["environment_digest"]
        ),
        "report_status": binding_status or status,
        "verifier": {
            "verifier_id": "lean_proof.checker.validator.v1",
            "verifier_version": "0.1.0",
        },
    }
    binding_body["binding_digest"] = digest_json(binding_body)
    binding_ref = _save_role_artifact(
        store,
        label=f"lean_domain_binding_{row.case_id}_{status}",
        role="lean_checker_verdict",
        execution_id=execution_id,
        task_id=task_id,
        body=binding_body,
        source_extra={
            "case_id": row.case_id,
            "root_unit_id": root_unit_id,
            "final_result_ref": (binding_final_ref or final_ref).to_dict(),
            "root_checker_report_ref": bound_report_ref.to_dict(),
            "root_theorem_payload_ref": root_theorem_ref.to_dict(),
            "official_case_digest": row.preregistered_case_ref[
                "official_case_digest"
            ],
            "official_root_theorem_id": row.preregistered_case_ref[
                "official_root_theorem_id"
            ],
            "official_root_theorem_payload_digest": row.preregistered_case_ref[
                "official_root_theorem_payload_digest"
            ],
            "normalized_theorem_digest": normalized_theorem_digest,
            "environment_digest": (
                binding_environment_digest or environment_ref["environment_digest"]
            ),
        },
    )
    if ledger is not None:
        attempt_id = f"attempt:{row.case_id}:{status}"
        submission_id = f"submission:{row.case_id}:{status}"
        verification_report_id = f"verification:{row.case_id}:{status}"
        checker_report_ref = report_ref.to_dict()
        if verification_metadata_tamper == "checker_report_ref":
            checker_report_ref = final_ref.to_dict()
        verification_report = {
            "verification_report_id": verification_report_id,
            "attempt_id": attempt_id,
            "submission_id": submission_id,
            "task_id": task_id,
            "unit_id": root_unit_id,
            "plugin_id": "lean_proof",
            "validator_policy_id": "lean_proof.checker.validator.v1",
            "status": "passed",
            "eligible_for_canonical": True,
            "candidate_output_refs": {"answer": final_ref.to_dict()},
            "metadata": {
                "checker_report_ref": checker_report_ref,
                "plugin_domain_layer": {
                    "details": {
                        "environment_digest": environment_ref["environment_digest"],
                        "proof_digest": report_body["proof_digest"],
                        "checker_status": report_body["status"],
                    }
                },
            },
        }
        verification_payload = {
            "task_id": task_id,
            "unit_id": root_unit_id,
            "attempt_id": attempt_id,
            "submission_id": submission_id,
            "plugin_id": "lean_proof",
            "validator_policy_id": "lean_proof.checker.validator.v1",
            "status": "passed",
            "eligible_for_canonical": True,
            "verification_report": verification_report,
            "verification_report_digest": digest_json(verification_report),
        }
        _append_event(
            ledger,
            task_id=task_id,
            event_type=EventType.VERIFICATION_RECORDED,
            suffix=f"lean-verification-binding-{status}",
            payload=verification_payload,
        )
        verification_event_seq = ledger.read_all()[-1].event_seq
        selected_verification_event_seq = verification_event_seq
        if canonical_selection_tamper == "verification_event_seq":
            selected_verification_event_seq += 1
        _append_event(
            ledger,
            task_id=task_id,
            event_type=EventType.CANONICAL_OUTPUTS_BOUND,
            suffix=f"lean-canonical-binding-{status}",
            payload={
                "canonical_selection": {
                    "canonical_output_refs": {"answer": final_ref.to_dict()},
                    "selected_verification_event_seq": selected_verification_event_seq,
                    "selected_verification_report_id": verification_report_id,
                    "selected_attempt_id": attempt_id,
                    "selected_submission_id": submission_id,
                    "task_id": task_id,
                    "unit_id": root_unit_id,
                }
            },
        )
    return binding_ref, report_ref


@pytest.mark.parametrize(
    ("status", "expected_correct"),
    (("accepted", True), ("rejected", False)),
)
def test_canonical_lean_domain_verdict_uses_actual_root_checker_status(
    tmp_path: Path,
    status: str,
    expected_correct: bool,
) -> None:
    base, _, _ = _inventory_row(root_id=f"inventory:lean:{status}")
    axes = {**base.condition_axes, "domain": "lean_proof", "topic_family": "pure_logic"}
    row = _replace_inventory_row(base, condition_axes=axes)
    kwargs, ledger, store, runtime, final_ref = _canonical_fixture(
        tmp_path,
        row,
        include_verification_event=False,
    )
    binding_ref, report_ref = _lean_domain_verdict_refs(
        store=store,
        row=row,
        execution_id=runtime.run_id,
        task_id=runtime.task_id,
        root_unit_id=runtime.root_unit_id,
        final_ref=final_ref,
        status=status,
        ledger=ledger,
    )
    kwargs["verifier_checker_refs"] = (binding_ref, report_ref)
    kwargs["runtime_result"] = project_protocol_run(
        run_id=runtime.run_id,
        task_id=runtime.task_id,
        root_unit_id=runtime.root_unit_id,
        event_ledger=ledger,
        artifact_store=store,
    )

    evidence = build_canonical_direct_evidence(**kwargs)

    assert evidence.paper_evidence_complete is True
    assert evidence.independently_verified_correct is expected_correct


def test_canonical_lean_domain_verdict_binds_selected_verification_event(
    tmp_path: Path,
) -> None:
    base, _, _ = _inventory_row(root_id="inventory:lean:selected-verification")
    row = _replace_inventory_row(
        base,
        condition_axes={**base.condition_axes, "domain": "lean_proof", "topic_family": "pure_logic"},
    )
    kwargs, ledger, store, runtime, final_ref = _canonical_fixture(
        tmp_path,
        row,
        include_verification_event=False,
    )
    kwargs["verifier_checker_refs"] = _lean_domain_verdict_refs(
        store=store,
        row=row,
        execution_id=runtime.run_id,
        task_id=runtime.task_id,
        root_unit_id=runtime.root_unit_id,
        final_ref=final_ref,
        status="accepted",
        ledger=ledger,
        canonical_selection_tamper="verification_event_seq",
    )
    kwargs["runtime_result"] = project_protocol_run(
        run_id=runtime.run_id,
        task_id=runtime.task_id,
        root_unit_id=runtime.root_unit_id,
        event_ledger=ledger,
        artifact_store=store,
    )

    with pytest.raises(ValueError, match="canonical selection"):
        build_canonical_direct_evidence(**kwargs)


def test_canonical_lean_domain_verdict_binds_checker_event_metadata(
    tmp_path: Path,
) -> None:
    base, _, _ = _inventory_row(root_id="inventory:lean:checker-event-metadata")
    row = _replace_inventory_row(
        base,
        condition_axes={**base.condition_axes, "domain": "lean_proof", "topic_family": "pure_logic"},
    )
    kwargs, ledger, store, runtime, final_ref = _canonical_fixture(
        tmp_path,
        row,
        include_verification_event=False,
    )
    kwargs["verifier_checker_refs"] = _lean_domain_verdict_refs(
        store=store,
        row=row,
        execution_id=runtime.run_id,
        task_id=runtime.task_id,
        root_unit_id=runtime.root_unit_id,
        final_ref=final_ref,
        status="accepted",
        ledger=ledger,
        verification_metadata_tamper="checker_report_ref",
    )
    kwargs["runtime_result"] = project_protocol_run(
        run_id=runtime.run_id,
        task_id=runtime.task_id,
        root_unit_id=runtime.root_unit_id,
        event_ledger=ledger,
        artifact_store=store,
    )

    with pytest.raises(ValueError, match="checker event metadata"):
        build_canonical_direct_evidence(**kwargs)


@pytest.mark.parametrize(
    ("fixture_options", "message"),
    (
        pytest.param(
            {"include_verification_event": False},
            "authoritative verification event",
            id="missing",
        ),
        pytest.param(
            {"duplicate_verification_event": True},
            "authoritative verification event",
            id="duplicate",
        ),
        pytest.param(
            {"verification_event_tamper": "plugin"},
            "validator identity",
            id="plugin-drift",
        ),
    ),
)
def test_canonical_lean_domain_verdict_requires_authoritative_verification_event(
    tmp_path: Path,
    fixture_options: dict[str, Any],
    message: str,
) -> None:
    base, _, _ = _inventory_row(root_id=f"inventory:lean:event:{next(iter(fixture_options))}")
    row = _replace_inventory_row(
        base,
        condition_axes={**base.condition_axes, "domain": "lean_proof", "topic_family": "pure_logic"},
    )
    kwargs, _, store, runtime, final_ref = _canonical_fixture(
        tmp_path,
        row,
        **fixture_options,
    )
    kwargs["verifier_checker_refs"] = _lean_domain_verdict_refs(
        store=store,
        row=row,
        execution_id=runtime.run_id,
        task_id=runtime.task_id,
        root_unit_id=runtime.root_unit_id,
        final_ref=final_ref,
        status="accepted",
    )

    with pytest.raises(ValueError, match=message):
        build_canonical_direct_evidence(**kwargs)


def test_canonical_lean_domain_verdict_rejects_missing_root_checker_report(
    tmp_path: Path,
) -> None:
    base, _, _ = _inventory_row(root_id="inventory:lean:missing-checker")
    row = _replace_inventory_row(
        base,
        condition_axes={**base.condition_axes, "domain": "lean_proof", "topic_family": "pure_logic"},
    )
    kwargs, _, store, runtime, final_ref = _canonical_fixture(tmp_path, row)
    binding_ref, _ = _lean_domain_verdict_refs(
        store=store,
        row=row,
        execution_id=runtime.run_id,
        task_id=runtime.task_id,
        root_unit_id=runtime.root_unit_id,
        final_ref=final_ref,
        status="rejected",
    )
    kwargs["verifier_checker_refs"] = (binding_ref,)

    with pytest.raises(ValueError, match="root checker report"):
        build_canonical_direct_evidence(**kwargs)


def test_canonical_lean_domain_verdict_rejects_wrong_checker_ref(
    tmp_path: Path,
) -> None:
    base, _, _ = _inventory_row(root_id="inventory:lean:wrong-checker")
    row = _replace_inventory_row(
        base,
        condition_axes={**base.condition_axes, "domain": "lean_proof", "topic_family": "pure_logic"},
    )
    kwargs, _, store, runtime, final_ref = _canonical_fixture(tmp_path, row)
    wrong_ref = _save_role_artifact(
        store,
        label="wrong-root-checker",
        role="protocol_runtime_artifact",
        execution_id=runtime.run_id,
        task_id=runtime.task_id,
    )
    binding_ref, report_ref = _lean_domain_verdict_refs(
        store=store,
        row=row,
        execution_id=runtime.run_id,
        task_id=runtime.task_id,
        root_unit_id=runtime.root_unit_id,
        final_ref=final_ref,
        status="accepted",
        binding_report_ref=wrong_ref,
    )
    kwargs["verifier_checker_refs"] = (binding_ref, report_ref)

    with pytest.raises(ValueError, match="checker report identity"):
        build_canonical_direct_evidence(**kwargs)


def test_canonical_lean_domain_verdict_rejects_wrong_final_identity(
    tmp_path: Path,
) -> None:
    base, _, _ = _inventory_row(root_id="inventory:lean:wrong-final")
    row = _replace_inventory_row(
        base,
        condition_axes={**base.condition_axes, "domain": "lean_proof", "topic_family": "pure_logic"},
    )
    kwargs, _, store, runtime, final_ref = _canonical_fixture(tmp_path, row)
    wrong_ref = _save_role_artifact(
        store,
        label="wrong-lean-final",
        role="protocol_runtime_artifact",
        execution_id=runtime.run_id,
        task_id=runtime.task_id,
    )
    binding_ref, report_ref = _lean_domain_verdict_refs(
        store=store,
        row=row,
        execution_id=runtime.run_id,
        task_id=runtime.task_id,
        root_unit_id=runtime.root_unit_id,
        final_ref=final_ref,
        status="rejected",
        binding_final_ref=wrong_ref,
    )
    kwargs["verifier_checker_refs"] = (binding_ref, report_ref)

    with pytest.raises(ValueError, match="final identity"):
        build_canonical_direct_evidence(**kwargs)


@pytest.mark.parametrize(
    ("binding_overrides", "message"),
    (
        ({"binding_status": "timeout"}, "status mismatch"),
        (
            {"binding_environment_digest": "sha256:different-lean-environment"},
            "environment mismatch",
        ),
    ),
)
def test_canonical_lean_domain_verdict_rejects_checker_binding_tamper(
    tmp_path: Path,
    binding_overrides: dict[str, str],
    message: str,
) -> None:
    base, _, _ = _inventory_row(root_id=f"inventory:lean:tamper:{message}")
    row = _replace_inventory_row(
        base,
        condition_axes={**base.condition_axes, "domain": "lean_proof", "topic_family": "pure_logic"},
    )
    kwargs, _, store, runtime, final_ref = _canonical_fixture(tmp_path, row)
    binding_ref, report_ref = _lean_domain_verdict_refs(
        store=store,
        row=row,
        execution_id=runtime.run_id,
        task_id=runtime.task_id,
        root_unit_id=runtime.root_unit_id,
        final_ref=final_ref,
        status="rejected",
        **binding_overrides,
    )
    kwargs["verifier_checker_refs"] = (binding_ref, report_ref)

    with pytest.raises(ValueError, match=message):
        build_canonical_direct_evidence(**kwargs)


@pytest.mark.parametrize(
    "missing_ref",
    ("stdout_ref", "stderr_ref", "generated_source_ref", "proof_artifact_ref"),
)
def test_canonical_lean_accepted_verdict_requires_checker_artifacts(
    tmp_path: Path,
    missing_ref: str,
) -> None:
    base, _, _ = _inventory_row(root_id=f"inventory:lean:missing:{missing_ref}")
    row = _replace_inventory_row(
        base,
        condition_axes={**base.condition_axes, "domain": "lean_proof", "topic_family": "pure_logic"},
    )
    kwargs, _, store, runtime, final_ref = _canonical_fixture(tmp_path, row)
    binding_ref, report_ref = _lean_domain_verdict_refs(
        store=store,
        row=row,
        execution_id=runtime.run_id,
        task_id=runtime.task_id,
        root_unit_id=runtime.root_unit_id,
        final_ref=final_ref,
        status="accepted",
        missing_checker_ref=missing_ref,
    )
    kwargs["verifier_checker_refs"] = (binding_ref, report_ref)

    with pytest.raises(ValueError, match=missing_ref):
        build_canonical_direct_evidence(**kwargs)


@pytest.mark.parametrize(
    ("report_overrides", "message"),
    (
        ({"report_request_id": "request:wrong"}, "request identity"),
        ({"checker_source_request_id": "request:wrong-source"}, "request identity"),
        ({"report_proof_digest": _digest("wrong-proof")}, "proof digest"),
        (
            {"report_normalized_theorem_digest": _digest("wrong-theorem")},
            "normalized theorem",
        ),
    ),
)
def test_canonical_lean_accepted_verdict_rejects_checker_artifact_tamper(
    tmp_path: Path,
    report_overrides: dict[str, str],
    message: str,
) -> None:
    base, _, _ = _inventory_row(root_id=f"inventory:lean:checker-tamper:{message}")
    row = _replace_inventory_row(
        base,
        condition_axes={**base.condition_axes, "domain": "lean_proof", "topic_family": "pure_logic"},
    )
    kwargs, _, store, runtime, final_ref = _canonical_fixture(tmp_path, row)
    binding_ref, report_ref = _lean_domain_verdict_refs(
        store=store,
        row=row,
        execution_id=runtime.run_id,
        task_id=runtime.task_id,
        root_unit_id=runtime.root_unit_id,
        final_ref=final_ref,
        status="accepted",
        **report_overrides,
    )
    kwargs["verifier_checker_refs"] = (binding_ref, report_ref)

    with pytest.raises(ValueError, match=message):
        build_canonical_direct_evidence(**kwargs)


def test_canonical_lean_rejects_same_case_nonofficial_root_theorem(
    tmp_path: Path,
) -> None:
    base, _, _ = _inventory_row(root_id="inventory:lean:nonofficial-theorem")
    row = _replace_inventory_row(
        base,
        condition_axes={**base.condition_axes, "domain": "lean_proof", "topic_family": "pure_logic"},
    )
    kwargs, _, store, runtime, final_ref = _canonical_fixture(tmp_path, row)
    alternate = _test_lean_root_theorem_body(row.case_id)
    alternate["statement_source"] = "False"
    alternate.pop("payload_digest")
    alternate["payload_digest"] = digest_json(alternate)
    binding_ref, report_ref = _lean_domain_verdict_refs(
        store=store,
        row=row,
        execution_id=runtime.run_id,
        task_id=runtime.task_id,
        root_unit_id=runtime.root_unit_id,
        final_ref=final_ref,
        status="accepted",
        theorem_body_override=alternate,
    )
    kwargs["verifier_checker_refs"] = (binding_ref, report_ref)

    with pytest.raises(ValueError, match="official root theorem"):
        build_canonical_direct_evidence(**kwargs)


@pytest.mark.parametrize(
    ("fixture_overrides", "report_overrides", "message"),
    (
        ({"lean_proof_bytes": b"by\n  exact False.elim (by contradiction)\n"}, {}, "proof digest"),
        ({}, {"generated_source_bytes": b"import Init\n-- tampered\n"}, "generated source"),
        ({"lean_proof_artifact_type": "AIUsageSummary"}, {}, "proof_artifact_ref type"),
        ({}, {"generated_source_artifact_type": "AIUsageSummary"}, "generated_source_ref type"),
    ),
)
def test_canonical_lean_rejects_checker_content_or_type_substitution(
    tmp_path: Path,
    fixture_overrides: dict[str, Any],
    report_overrides: dict[str, Any],
    message: str,
) -> None:
    base, _, _ = _inventory_row(root_id=f"inventory:lean:content:{message}")
    row = _replace_inventory_row(
        base,
        condition_axes={**base.condition_axes, "domain": "lean_proof", "topic_family": "pure_logic"},
    )
    kwargs, _, store, runtime, final_ref = _canonical_fixture(
        tmp_path,
        row,
        **fixture_overrides,
    )
    binding_ref, report_ref = _lean_domain_verdict_refs(
        store=store,
        row=row,
        execution_id=runtime.run_id,
        task_id=runtime.task_id,
        root_unit_id=runtime.root_unit_id,
        final_ref=final_ref,
        status="accepted",
        **report_overrides,
    )
    kwargs["verifier_checker_refs"] = (binding_ref, report_ref)

    with pytest.raises(ValueError, match=message):
        build_canonical_direct_evidence(**kwargs)


def test_canonical_factor_domain_verdict_keeps_false_answer_in_denominator(
    tmp_path: Path,
) -> None:
    row, _, _ = _inventory_row(root_id="inventory:factor:false")
    kwargs, _, store, runtime, final_ref = _canonical_fixture(
        tmp_path, row, correct=False
    )
    verdict_ref = _factor_domain_verdict_ref(
        store=store,
        row=row,
        execution_id=runtime.run_id,
        task_id=runtime.task_id,
        root_unit_id=runtime.root_unit_id,
        final_ref=final_ref,
        correct=False,
    )
    kwargs["verifier_checker_refs"] = (verdict_ref,)

    evidence = build_canonical_direct_evidence(**kwargs)

    assert evidence.paper_evidence_complete is True
    assert evidence.independently_verified_correct is False


def test_canonical_factor_domain_verdict_rejects_tampered_self_report(
    tmp_path: Path,
) -> None:
    row, _, _ = _inventory_row(root_id="inventory:factor:self-report")
    kwargs, _, store, runtime, final_ref = _canonical_fixture(
        tmp_path, row, correct=False
    )
    verdict_ref = _factor_domain_verdict_ref(
        store=store,
        row=row,
        execution_id=runtime.run_id,
        task_id=runtime.task_id,
        root_unit_id=runtime.root_unit_id,
        final_ref=final_ref,
        correct=True,
    )
    kwargs["verifier_checker_refs"] = (verdict_ref,)

    with pytest.raises(ValueError, match="Factor domain verdict"):
        build_canonical_direct_evidence(**kwargs)


def test_canonical_factor_domain_verdict_rejects_wrong_final_identity(
    tmp_path: Path,
) -> None:
    row, _, _ = _inventory_row(root_id="inventory:factor:wrong-final")
    kwargs, _, store, runtime, final_ref = _canonical_fixture(tmp_path, row)
    wrong_ref = _save_role_artifact(
        store,
        label="wrong-factor-final",
        role="protocol_runtime_artifact",
        execution_id=runtime.run_id,
        task_id=runtime.task_id,
    )
    verdict_ref = _factor_domain_verdict_ref(
        store=store,
        row=row,
        execution_id=runtime.run_id,
        task_id=runtime.task_id,
        root_unit_id=runtime.root_unit_id,
        final_ref=final_ref,
        final_binding_ref=wrong_ref,
        correct=False,
    )
    kwargs["verifier_checker_refs"] = (verdict_ref,)

    with pytest.raises(ValueError, match="final identity"):
        build_canonical_direct_evidence(**kwargs)


def test_merge_unit_canonical_final_projects_through_unique_bound_merge(
    tmp_path: Path,
) -> None:
    row, _, _ = _inventory_row(evidence_class="regression_only")
    kwargs, *_ = _canonical_fixture(
        tmp_path,
        row,
        merge_unit_canonical_final=True,
    )

    evidence = build_canonical_direct_evidence(**kwargs)

    assert evidence.canonical_acceptance_ref.event_seq < evidence.merge_ref.event_seq
    assert evidence.merge_ref.event_seq < evidence.terminal_root_event_ref.event_seq


def test_merge_unit_canonical_final_rejects_ambiguous_canonical_event(
    tmp_path: Path,
) -> None:
    row, _, _ = _inventory_row(
        root_id="inventory:merge-reject:ambiguous",
        evidence_class="regression_only",
    )
    kwargs, *_ = _canonical_fixture(
        tmp_path,
        row,
        merge_unit_canonical_final=True,
        duplicate_merge_canonical=True,
    )

    with pytest.raises(ValueError, match="ambiguous|multiple|unique"):
        build_canonical_direct_evidence(**kwargs)


@pytest.mark.parametrize(
    "field_name",
    (
        "artifact_type",
        "artifact_schema",
        "source",
        "source_role",
        "metadata",
        "uri",
        "created_at",
    ),
)
def test_merge_unit_runtime_artifact_rejects_same_bytes_manifest_tamper(
    tmp_path: Path,
    field_name: str,
) -> None:
    row, _, _ = _inventory_row(
        root_id=f"inventory:manifest:{field_name}",
        evidence_class="regression_only",
    )
    kwargs, _, store, runtime_result, _ = _canonical_fixture(
        tmp_path,
        row,
        merge_unit_canonical_final=True,
    )
    runtime_ref = next(
        ref for ref in runtime_result.artifact_refs if ref.source.get("role") == "root_marker"
    )
    path = store.artifact_dir / f"{runtime_ref.artifact_id}.manifest.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    if field_name == "artifact_type":
        body["artifact_type"] = "TamperedType"
    elif field_name == "artifact_schema":
        body["artifact_schema_id"] = "tampered.schema"
    elif field_name == "source":
        body["source"]["kind"] = "tampered"
    elif field_name == "source_role":
        body["source"]["role"] = "tampered_role"
    elif field_name == "metadata":
        body["metadata"] = {"tampered": True}
    elif field_name == "uri":
        body["uri"] = f"./{body['uri']}"
    else:
        body["created_at"] = "2026-08-03T00:00:00Z"
    _rewrite_durable_manifest(path, body)

    with pytest.raises(ValueError, match="artifact reference verification"):
        build_canonical_direct_evidence(**kwargs)


@pytest.mark.parametrize(
    "tamper",
    (
        "top_merge_unit",
        "record_merge_unit_missing",
        "top_selection",
        "record_selection_missing",
        "canonical_event_seq",
        "record_event_seq_missing",
        "top_refs_extra",
        "record_refs_renamed",
        "canonical_refs_extra",
    ),
)
def test_merge_unit_final_rejects_incomplete_official_merge_commitment(
    tmp_path: Path,
    tamper: str,
) -> None:
    row, _, _ = _inventory_row(
        root_id=f"inventory:commitment:{tamper}",
        evidence_class="regression_only",
    )
    kwargs, *_ = _canonical_fixture(
        tmp_path,
        row,
        merge_unit_canonical_final=True,
        merge_commitment_tamper=tamper,
    )

    with pytest.raises(ValueError, match="merge-unit final"):
        build_canonical_direct_evidence(**kwargs)


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


def test_catalog_case_difficulty_is_independent_of_condition_difficulty() -> None:
    row, _original_condition_manifest, catalog = _inventory_row()
    condition_axes = {**dict(row.condition_axes), "difficulty": "medium"}
    condition_manifest, condition_ref = _condition_manifest(
        row.condition_id,
        condition_axes,
    )
    candidate = _replace_inventory_row(
        row,
        preregistered_condition_ref=condition_ref,
        condition_axes=condition_axes,
    )

    projection = _project(
        _inventory_manifest(candidate),
        (condition_manifest,),
        (catalog,),
        {},
    )

    assert projection.rows[0].condition_axes["difficulty"] == "medium"
    assert catalog["records"][0]["difficulty"] == "hard"
    assert projection.rows[0].preregistered_case_ref["case_record_digest"] == (
        catalog["records"][0]["case_record_digest"]
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


def test_four_root_hand_calculation_keeps_denominator_and_counts_failed_false(
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
    assert aggregate.status == "computed"
    assert aggregate.value == pytest.approx(0.25)


def test_native_online_direct_builder_persists_exact_identity_bound_artifacts(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path / "native-online")
    execution_id = "execution:native-online"
    task_id = "task:native-online"
    root_unit_id = "unit:native-online"
    final_ref = _save_role_artifact(
        store,
        label="native-final",
        role="final_result",
        execution_id=execution_id,
        task_id=task_id,
    )
    oracle_ref = _save_role_artifact(
        store,
        label="native-oracle",
        role="verification_report",
        execution_id=execution_id,
        task_id=task_id,
        body={"accepted": True},
    )
    provider_refs = tuple(
        _save_role_artifact(
            store,
            label=f"native-provider-{role}",
            role=role,
            execution_id=execution_id,
            task_id=task_id,
        )
        for role in (
            "request_body",
            "raw_output_or_provider_failure",
            "provenance",
            "usage_status",
            "latency",
            "pricing",
            "provider_attempt",
            "model_record",
        )
    )

    resource_ref, verdict_ref = persist_native_online_direct_artifacts(
        artifact_store=store,
        execution_id=execution_id,
        task_id=task_id,
        root_unit_id=root_unit_id,
        final_result_ref=final_ref,
        oracle_verdict_ref=oracle_ref,
        oracle_kind="independent_verifier",
        oracle_correct=True,
        current_provider_object_refs=provider_refs,
    )

    resource = json.loads(store.read_bytes(resource_ref))
    verdict = json.loads(store.read_bytes(verdict_ref))
    assert resource["schema_version"] == "tokenshare.paper_actual_resource_book.v1"
    assert resource["current_provider_object_refs"] == [
        ref.to_dict() for ref in provider_refs
    ]
    assert resource_ref.source["role"] == "actual_resource_book"
    assert verdict == {"accepted": True}
    assert verdict_ref == oracle_ref


def test_native_online_direct_builder_projects_role_books_from_native_sources(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path / "native-role-books")
    execution_id = "execution:native-role-books"
    task_id = "task:native-role-books"
    final_ref = _save_role_artifact(
        store,
        label="role-book-final",
        role="final_result",
        execution_id=execution_id,
        task_id=task_id,
    )
    oracle_ref = _save_role_artifact(
        store,
        label="role-book-oracle",
        role="verification_report",
        execution_id=execution_id,
        task_id=task_id,
    )
    native_ref = _save_role_artifact(
        store,
        label="native-provider-envelope",
        role="native_executor_envelope",
        execution_id=execution_id,
        task_id=task_id,
    )
    roles = (
        "request_body",
        "raw_output_or_provider_failure",
        "provenance",
        "usage_status",
        "latency",
        "pricing",
        "provider_attempt",
        "model_record",
    )

    resource_ref, _ = persist_native_online_direct_artifacts(
        artifact_store=store,
        execution_id=execution_id,
        task_id=task_id,
        root_unit_id="unit:native-role-books",
        final_result_ref=final_ref,
        oracle_verdict_ref=oracle_ref,
        oracle_kind="independent_verifier",
        oracle_correct=True,
        current_provider_object_refs={role: (native_ref,) for role in roles},
    )

    resource = json.loads(store.read_bytes(resource_ref))
    projected = tuple(
        ArtifactRef.from_dict(value)
        for value in resource["current_provider_object_refs"]
    )
    assert {value.source["role"] for value in projected} == set(roles)
    assert all(value.source["source_artifact_refs"] == [native_ref.to_dict()] for value in projected)


def test_trace_direct_evidence_accepts_complete_roles_for_multiple_entries(
    tmp_path: Path,
) -> None:
    row, _, _ = _inventory_row(
        root_id="inventory:trace:multi-entry",
        evidence_class="real_model_trace_protocol_run",
    )
    kwargs, *_ = _canonical_fixture(tmp_path, row)
    first_entry = tuple(kwargs["source_bank_object_locators"])
    second_entry = tuple(
        ExternalBankObjectLocator(
            bank_root_id=value.bank_root_id,
            manifest_digest=value.manifest_digest,
            entry_id="entry-2",
            object_role=value.object_role,
            object_digest=_digest(f"entry-2:{value.object_role}"),
        )
        for value in first_entry
    )
    kwargs["source_bank_object_locators"] = (*first_entry, *second_entry)

    evidence = build_canonical_direct_evidence(**kwargs)

    assert {value.entry_id for value in evidence.source_bank_object_locators} == {
        "entry-1",
        "entry-2",
    }
    assert len(evidence.source_bank_object_locators) == 2 * len(first_entry)


def test_failed_direct_evidence_preserves_missing_final_result(
    tmp_path: Path,
) -> None:
    row, _, _ = _inventory_row(
        root_id="inventory:failed:no-final",
        evidence_class="online_real_provider",
    )
    kwargs, *_ = _canonical_fixture(
        tmp_path,
        row,
        completed=False,
        include_merge=False,
        include_verdict=False,
    )
    kwargs["final_result_ref"] = None

    evidence = build_canonical_direct_evidence(**kwargs)

    assert evidence.canonical_runtime_status == "failed"
    assert evidence.final_result_ref is None
    projection = project_paper_direct_results(
        root_inventory_manifest=_inventory_manifest(row),
        condition_manifests=(_inventory_row(root_id=row.preregistered_root_run_id)[1],),
        catalog_manifests=(_inventory_row(root_id=row.preregistered_root_run_id)[2],),
        canonical_runtime_evidence=(evidence,),
    )
    assert projection.rows[0].root_status == "failed"
    assert projection.rows[0].final_result_ref is None
    aggregate = build_direct_boolean_aggregate(
        projection,
        outcome_field="end_to_end_verified_success",
    )
    assert aggregate.status == "computed"
    assert aggregate.value == 0.0
    assert aggregate.audit_denominator_count == 1
