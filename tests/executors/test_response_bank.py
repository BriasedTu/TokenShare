from __future__ import annotations

import dataclasses
import json
import shutil
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from tokenshare.executors.ai_api_replay import replay_response_bank_trace
from tokenshare.executors.ai_api_request_identity import PreparedOutboundRequestFactory
from tokenshare.executors.response_bank import (
    CurrentTraceWrapper,
    ExternalBankObjectLocator,
    OBJECT_ROLES,
    ResponseBankBlockedError,
    ResponseBankEntry,
    ResponseBankInventoryRow,
    ResponseBankManifest,
    ResultsFirstResponseBankManifest,
    ResponseBankResolver,
    ValidatedResponseBankIndex,
    canonical_inventory_rows,
    canonical_digest,
    initialize_response_bank,
    inventory_entry_id,
    response_bank_inventory_digest,
    semantic_slot_key,
    terminal_bank_entry_id,
)


def test_results_first_manifest_is_explicit_and_legacy_paid_bytes_do_not_change() -> None:
    legacy = ResponseBankManifest.create(
        bank_root_id="legacy",
        profile_digest="sha256:profile",
        budget_digest="sha256:budget",
        inventory_digest="sha256:inventory",
        provider_config_digest="sha256:provider",
        entry_ids=(),
        object_role_schema=OBJECT_ROLES,
        terminal_entry_count=0,
        created_by_paid_receipt_digest="sha256:receipt",
    )
    assert set(legacy.to_dict()) == {
        "bank_root_id",
        "manifest_digest",
        "profile_digest",
        "budget_digest",
        "inventory_digest",
        "provider_config_digest",
        "entry_ids",
        "object_role_schema",
        "terminal_entry_count",
        "created_by_paid_receipt_digest",
        "root_binding_marker_digest",
    }
    facility = ResultsFirstResponseBankManifest.create(
        bank_root_id="facility",
        profile_digest="sha256:profile",
        budget_digest="sha256:budget",
        inventory_digest="sha256:inventory",
        provider_config_digest="sha256:provider",
        entry_ids=(),
        object_role_schema=OBJECT_ROLES,
        terminal_entry_count=0,
        authorization_digest="sha256:authorization",
    )

    parsed = ResponseBankManifest.from_dict(facility.to_dict())

    assert isinstance(parsed, ResultsFirstResponseBankManifest)
    assert parsed.authorization_kind == "user_authorized_smoke_facility"
    assert "created_by_paid_receipt_digest" not in parsed.to_dict()
from tokenshare.storage.artifacts import ArtifactStore


SUCCESS_ROLES = (
    "request_body",
    "raw_output",
    "provenance",
    "usage",
    "latency",
    "pricing",
    "acquisition_attempt",
    "model_record",
)
FAILURE_ROLES = (
    "request_body",
    "provider_failure",
    "provenance",
    "usage",
    "latency",
    "pricing",
    "acquisition_attempt",
    "model_record",
)


def _prepared():
    return PreparedOutboundRequestFactory.prepare(
        body_obj={"messages": [{"role": "user", "content": "factor 21"}]},
        base_url="https://provider.invalid/v1/",
        endpoint="chat/completions",
        provider_config_digest="sha256:provider",
        entry_id="provider-entry",
        configured_model="model-v1",
        effective_controls_digest="sha256:controls",
        plugin_id="factorization",
        plugin_version="2.0.0",
        prompt_profile_id="prompt-v1",
        prompt_serialization_schema="prompt.schema.v1",
        body_serialization_schema="body.schema.v1",
        case_id="case-21",
        planned_ai_unit_id="unit-21",
        sample_slot_index=0,
        replacement_slot=0,
    )


def _row():
    prepared = _prepared()
    values = dict(
        inventory_entry_id="",
        semantic_slot_key=semantic_slot_key(
            case_record_digest="sha256:case",
            planned_ai_unit_id=prepared.planned_ai_unit_id,
            sample_slot_index=prepared.sample_slot_index,
            replacement_slot=prepared.replacement_slot,
            provider_config_digest=prepared.provider_config_digest,
            prompt_profile_digest="sha256:prompt",
            prompt_admission_profile_digest=prepared.prompt_admission_profile_digest,
            plugin_version=prepared.plugin_version,
        ),
        case_record_digest="sha256:case",
        planned_ai_unit_id=prepared.planned_ai_unit_id,
        sample_slot_index=prepared.sample_slot_index,
        replacement_slot=prepared.replacement_slot,
        provider_config_digest=prepared.provider_config_digest,
        prompt_profile_digest="sha256:prompt",
        prompt_admission_profile_digest=prepared.prompt_admission_profile_digest,
        plugin_version=prepared.plugin_version,
        entry_id="bank-entry-1",
        body_digest=prepared.body_digest,
        inference_request_digest=prepared.inference_request_digest,
    )
    values["inventory_entry_id"] = inventory_entry_id(values)
    return ResponseBankInventoryRow(**values)


def _objects(terminal_kind: str = "success") -> dict[str, bytes]:
    terminal_role = "raw_output" if terminal_kind == "success" else "provider_failure"
    return {
        "request_body": _prepared().body_bytes,
        terminal_role: json.dumps({terminal_role: True}, sort_keys=True).encode(),
        "provenance": b'{"source":"paid-acquisition"}',
        "usage": b'{"total_tokens":7}',
        "latency": b'{"milliseconds":12}',
        "pricing": b'{"cost_estimate":"0.01"}',
        "acquisition_attempt": b'{"attempt":1}',
        "model_record": b'{"model":"model-v1"}',
    }


def _locator(bank_root_id: str, manifest_digest: str, role: str, data: bytes):
    return ExternalBankObjectLocator(
        bank_root_id=bank_root_id,
        manifest_digest=manifest_digest,
        entry_id="bank-entry-1",
        object_role=role,
        object_digest=f"sha256:{sha256(data).hexdigest()}",
    )


def _bank(tmp_path: Path, terminal_kind: str = "success"):
    row = _row()
    objects = _objects(terminal_kind)
    manifest = ResponseBankManifest.create(
        bank_root_id="bank-root-1",
        profile_digest="sha256:profile",
        budget_digest="sha256:budget",
        inventory_digest=canonical_digest([row.to_dict()]),
        provider_config_digest=row.provider_config_digest,
        entry_ids=("bank-entry-1",),
        object_role_schema=(
            "request_body",
            "raw_output",
            "provider_failure",
            "provenance",
            "usage",
            "latency",
            "pricing",
            "acquisition_attempt",
            "model_record",
        ),
        terminal_entry_count=1,
        created_by_paid_receipt_digest="sha256:receipt",
    )
    locators = tuple(
        _locator(manifest.bank_root_id, manifest.manifest_digest, role, data)
        for role, data in objects.items()
    )
    entry = ResponseBankEntry(
        inventory_digest=manifest.inventory_digest,
        inventory_entry_id=row.inventory_entry_id,
        semantic_slot_key=row.semantic_slot_key,
        inference_request_digest=row.inference_request_digest,
        entry_id="bank-entry-1",
        sample_slot_index=row.sample_slot_index,
        replacement_slot=row.replacement_slot,
        terminal_kind=terminal_kind,
        object_locators=locators,
        acquisition_state_ref="acquisition-state-1",
    )
    manifest = initialize_response_bank(
        tmp_path / "source-bank",
        manifest=manifest,
        inventory_rows=(row,),
        entries=(entry,),
        objects={locator.object_digest: objects[locator.object_role] for locator in locators},
    )
    return tmp_path / "source-bank", manifest, row, entry, objects


def _wrapper(manifest, row, entry):
    return CurrentTraceWrapper(
        current_run_id="run-current",
        current_task_id="task-current",
        current_unit_id="unit-current",
        current_attempt_id="attempt-current",
        attempt_ordinal=3,
        bank_root_id=manifest.bank_root_id,
        manifest_digest=manifest.manifest_digest,
        root_binding_marker_digest=manifest.root_binding_marker_digest,
        inference_request_digest=row.inference_request_digest,
        entry_id=entry.entry_id,
        locator_digests={x.object_role: x.object_digest for x in entry.object_locators},
        logical_started_at="2026-08-01T00:00:00Z",
        logical_finished_at="2026-08-01T00:00:01Z",
        source_latency_ms=12,
        current_parse_ref="parse-current",
        current_verifier_ref="verify-current",
        current_checker_ref=None,
        current_canonical_ref="canonical-current",
        current_ledger_ref="ledger-current",
    )


def test_manifest_inventory_entry_and_current_wrapper_v1_exact_fields(tmp_path) -> None:
    _, manifest, row, entry, _ = _bank(tmp_path)
    wrapper = _wrapper(manifest, row, entry)

    assert {field.name for field in dataclasses.fields(manifest)} == {
        "bank_root_id", "manifest_digest", "profile_digest", "budget_digest",
        "inventory_digest", "provider_config_digest", "entry_ids",
        "object_role_schema", "terminal_entry_count",
        "created_by_paid_receipt_digest", "root_binding_marker_digest",
    }
    assert {field.name for field in dataclasses.fields(row)} == {
        "inventory_entry_id", "semantic_slot_key", "case_record_digest",
        "planned_ai_unit_id", "sample_slot_index", "replacement_slot",
        "provider_config_digest", "prompt_profile_digest",
        "prompt_admission_profile_digest", "plugin_version", "entry_id",
        "body_digest", "inference_request_digest",
    }
    assert {field.name for field in dataclasses.fields(entry)} == {
        "inventory_digest", "inventory_entry_id", "semantic_slot_key",
        "inference_request_digest", "entry_id", "sample_slot_index",
        "replacement_slot", "terminal_kind", "object_locators",
        "acquisition_state_ref",
    }
    assert {field.name for field in dataclasses.fields(wrapper)} == {
        "current_run_id", "current_task_id", "current_unit_id",
        "current_attempt_id", "attempt_ordinal", "bank_root_id",
        "manifest_digest", "root_binding_marker_digest", "inference_request_digest", "entry_id",
        "locator_digests", "logical_started_at", "logical_finished_at",
        "source_latency_ms", "current_parse_ref", "current_verifier_ref",
        "current_checker_ref", "current_canonical_ref", "current_ledger_ref",
    }


def test_inventory_entry_id_recomputes_canonical_row_excluding_only_itself() -> None:
    row = _row()
    expected = canonical_digest(
        {key: value for key, value in row.to_dict().items() if key != "inventory_entry_id"}
    )
    assert row.inventory_entry_id == expected


def test_inventory_entry_id_is_stable_across_load_and_replay(tmp_path) -> None:
    root, _, row, _, _ = _bank(tmp_path)
    first = ResponseBankResolver.open(root).index.inventory_rows[0]
    second = ResponseBankResolver.open(root).index.inventory_rows[0]
    assert first.inventory_entry_id == row.inventory_entry_id == second.inventory_entry_id


def test_terminal_bank_entry_id_is_versioned_unique_and_separate_from_provider_entry() -> None:
    prepared = _prepared()
    first_slot = semantic_slot_key(
        case_record_digest="sha256:case-1",
        planned_ai_unit_id="unit-1",
        sample_slot_index=0,
        replacement_slot=0,
        provider_config_digest=prepared.provider_config_digest,
        prompt_profile_digest="sha256:prompt",
        prompt_admission_profile_digest=prepared.prompt_admission_profile_digest,
        plugin_version=prepared.plugin_version,
    )
    second_slot = semantic_slot_key(
        case_record_digest="sha256:case-2",
        planned_ai_unit_id="unit-2",
        sample_slot_index=0,
        replacement_slot=0,
        provider_config_digest=prepared.provider_config_digest,
        prompt_profile_digest="sha256:prompt",
        prompt_admission_profile_digest=prepared.prompt_admission_profile_digest,
        plugin_version=prepared.plugin_version,
    )

    first = terminal_bank_entry_id(
        semantic_slot_key=first_slot,
        inference_request_digest=prepared.inference_request_digest,
    )
    second = terminal_bank_entry_id(
        semantic_slot_key=second_slot,
        inference_request_digest=prepared.inference_request_digest,
    )

    assert first == canonical_digest(
        {
            "schema_version": "tokenshare.response_bank_terminal_entry_identity.v1",
            "semantic_slot_key": first_slot,
            "inference_request_digest": prepared.inference_request_digest,
        }
    )
    assert first != second
    assert prepared.entry_id == "provider-entry"
    assert prepared.entry_id not in {first, second}
    assert ResponseBankInventoryRow.from_dict(_row().to_dict()).entry_id == (
        "bank-entry-1"
    )


def test_inventory_digest_uses_one_inventory_entry_id_canonical_sort() -> None:
    first = _row()
    second_values = first.to_dict()
    second_values["case_record_digest"] = "sha256:case-2"
    second_values["planned_ai_unit_id"] = "unit-22"
    second_values["semantic_slot_key"] = semantic_slot_key(
        case_record_digest=second_values["case_record_digest"],
        planned_ai_unit_id=second_values["planned_ai_unit_id"],
        sample_slot_index=second_values["sample_slot_index"],
        replacement_slot=second_values["replacement_slot"],
        provider_config_digest=second_values["provider_config_digest"],
        prompt_profile_digest=second_values["prompt_profile_digest"],
        prompt_admission_profile_digest=second_values[
            "prompt_admission_profile_digest"
        ],
        plugin_version=second_values["plugin_version"],
    )
    second_values["entry_id"] = terminal_bank_entry_id(
        semantic_slot_key=second_values["semantic_slot_key"],
        inference_request_digest=second_values["inference_request_digest"],
    )
    second_values["inventory_entry_id"] = inventory_entry_id(second_values)
    second = ResponseBankInventoryRow.from_dict(second_values)
    canonical = tuple(
        sorted((first, second), key=lambda row: row.inventory_entry_id)
    )

    assert canonical_inventory_rows((second, first)) == canonical
    assert response_bank_inventory_digest((second, first)) == canonical_digest(
        [row.to_dict() for row in canonical]
    )
    assert response_bank_inventory_digest((first, second)) == (
        response_bank_inventory_digest((second, first))
    )


def test_inventory_row_tamper_changes_recomputed_id_and_fails_closed(tmp_path) -> None:
    root, _, row, _, _ = _bank(tmp_path)
    inventory_path = root / "inventory.v1.json"
    body = json.loads(inventory_path.read_text(encoding="utf-8"))
    body[0]["body_digest"] = "sha256:tampered"
    inventory_path.write_text(json.dumps(body), encoding="utf-8")
    assert inventory_entry_id(body[0]) != row.inventory_entry_id
    with pytest.raises(ValueError, match="inventory_entry_id"):
        ResponseBankResolver.open(root)


def test_external_locator_has_no_path_uri_or_artifact_ref() -> None:
    names = {field.name for field in dataclasses.fields(ExternalBankObjectLocator)}
    assert names == {
        "bank_root_id", "manifest_digest", "entry_id", "object_role", "object_digest"
    }
    assert not names.intersection({"path", "root_path", "uri", "artifact_ref", "content_hash"})


def test_external_root_is_process_local_and_marker_manifest_bound(tmp_path) -> None:
    root, manifest, _, _, _ = _bank(tmp_path)
    resolver = ResponseBankResolver.open(root)
    assert "root_path" not in json.dumps(manifest.to_dict())
    marker = json.loads((root / "response_bank_root_marker.v1.json").read_text("utf-8"))
    assert marker["bank_root_id"] == manifest.bank_root_id
    assert marker["manifest_digest"] == manifest.manifest_digest
    assert marker["marker_digest"] == manifest.root_binding_marker_digest
    moved = tmp_path / "moved-bank"
    root.rename(moved)
    with pytest.raises(ValueError, match="root binding"):
        ResponseBankResolver.open(moved)
    assert resolver.root_path == root.resolve()


def test_locator_stream_reads_and_hashes_bank_internal_object(tmp_path, monkeypatch) -> None:
    root, _, _, entry, objects = _bank(tmp_path)
    resolver = ResponseBankResolver.open(root)
    raw_locator = next(x for x in entry.object_locators if x.object_role == "raw_output")
    source = root / "objects" / raw_locator.object_digest.removeprefix("sha256:")
    original_open = Path.open
    reads: list[int] = []

    class RecordingReader:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.wrapped.close()

        def read(self, size=-1):
            reads.append(size)
            return self.wrapped.read(size)

    def recording_open(path, *args, **kwargs):
        opened = original_open(path, *args, **kwargs)
        mode = args[0] if args else kwargs.get("mode", "r")
        return RecordingReader(opened) if path == source and mode == "rb" else opened

    monkeypatch.setattr(Path, "open", recording_open)
    assert resolver.read_verified(raw_locator, chunk_size=3) == objects["raw_output"]
    assert reads and all(size == 3 for size in reads)

    source.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="object digest"):
        resolver.read_verified(raw_locator, chunk_size=3)


def test_entry_roles_are_unique_complete_and_success_failure_exclusive(tmp_path) -> None:
    _, manifest, row, entry, _ = _bank(tmp_path)
    ValidatedResponseBankIndex.build(manifest, (row,), (entry,))
    duplicate = replace(entry, object_locators=entry.object_locators + (entry.object_locators[0],))
    with pytest.raises(ValueError, match="duplicate object role"):
        ValidatedResponseBankIndex.build(manifest, (row,), (duplicate,))
    missing = replace(entry, object_locators=entry.object_locators[:-1])
    with pytest.raises(ValueError, match="missing object roles"):
        ValidatedResponseBankIndex.build(manifest, (row,), (missing,))


def test_current_store_rejects_materializing_source_object(tmp_path, monkeypatch) -> None:
    source_root, manifest, row, entry, objects = _bank(tmp_path)
    consumer = ArtifactStore(tmp_path / "consumer")
    wrapper = _wrapper(manifest, row, entry)

    ref = consumer.save_external_trace_wrapper(
        wrapper.to_dict(), artifact_id="current-wrapper", created_at="2026-08-01T00:00:02Z"
    )
    assert consumer.verify(ref)
    assert not any(
        path.read_bytes() in objects.values()
        for path in consumer.artifact_dir.iterdir()
        if path.is_file()
    )
    with pytest.raises(ValueError, match="source bank object"):
        consumer.save_external_trace_wrapper(
            {**wrapper.to_dict(), "source_bytes": objects["raw_output"]},
            artifact_id="forbidden", created_at="2026-08-01T00:00:03Z",
        )
    forbidden = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("source object materialization is forbidden")
    )
    monkeypatch.setattr(consumer, "save_bytes", forbidden)
    monkeypatch.setattr(consumer, "save_json", forbidden)
    monkeypatch.setattr(shutil, "copytree", forbidden)
    monkeypatch.setattr(ResponseBankResolver, "_materialize", forbidden, raising=False)
    restored = replay_response_bank_trace(
        external_bank_root=source_root,
        wrapper=wrapper,
    )
    assert restored.entry == entry
    assert source_root.exists()


def test_terminal_provider_failure_is_complete_without_raw(tmp_path) -> None:
    root, _, _, entry, _ = _bank(tmp_path, terminal_kind="provider_failure")
    restored = ResponseBankResolver.open(root).entry(entry.entry_id)
    roles = {x.object_role for x in restored.object_locators}
    assert roles == set(FAILURE_ROLES)
    assert "raw_output" not in roles


def test_replay_requires_same_explicit_root_binding_and_restores_entry_ordinal_roles_timing(
    tmp_path,
) -> None:
    root, manifest, row, entry, _ = _bank(tmp_path)
    wrapper = _wrapper(manifest, row, entry)
    restored = replay_response_bank_trace(external_bank_root=root, wrapper=wrapper)
    assert restored.entry == entry
    assert restored.attempt_ordinal == 3
    assert set(restored.locators_by_role) == set(SUCCESS_ROLES)
    assert restored.logical_started_at == wrapper.logical_started_at
    assert restored.logical_finished_at == wrapper.logical_finished_at
    assert restored.source_latency_ms == 12
    with pytest.raises(ResponseBankBlockedError, match="explicit external bank root"):
        replay_response_bank_trace(external_bank_root=None, wrapper=wrapper)
    other_root, _, _, _, _ = _bank(tmp_path / "other")
    with pytest.raises(ResponseBankBlockedError, match="root binding"):
        replay_response_bank_trace(external_bank_root=other_root, wrapper=wrapper)


def test_conflicting_terminal_entry_is_rejected(tmp_path) -> None:
    _, manifest, row, entry, _ = _bank(tmp_path)
    failure = _locator(
        manifest.bank_root_id,
        manifest.manifest_digest,
        "provider_failure",
        b'{"provider_failure":true}',
    )
    conflicting = replace(entry, object_locators=entry.object_locators + (failure,))
    with pytest.raises(ValueError, match="conflicting terminal"):
        ValidatedResponseBankIndex.build(manifest, (row,), (conflicting,))
