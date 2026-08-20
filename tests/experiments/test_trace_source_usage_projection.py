from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from tokenshare.executors.response_bank import (
    OBJECT_ROLES,
    ExternalBankObjectLocator,
    ResponseBankEntry,
    ResponseBankInventoryRow,
    ResponseBankManifest,
    ResponseBankResolver,
    initialize_response_bank,
    inventory_entry_id,
    response_bank_inventory_digest,
    semantic_slot_key,
)
from tokenshare.executors.trace_backed import (
    TraceBackedExecutor,
    freeze_projected_trace_source_binding,
    freeze_trace_source_binding,
)
from tokenshare.experiments import paper_formal_runner as formal_runner
from tokenshare.experiments.paper_direct_results import (
    build_canonical_direct_evidence,
)
from tokenshare.experiments.paper_models import (
    ExternalBankObjectLocator as PaperExternalBankObjectLocator,
)
from tokenshare.storage.artifacts import ArtifactStore
from tests.executors.test_trace_backed import _bound_request
from tests.experiments.test_paper_direct_results import (
    _canonical_fixture,
    _inventory_manifest,
    _inventory_row,
    _project,
    _replace_inventory_row,
)


def _digest(data: bytes) -> str:
    return f"sha256:{sha256(data).hexdigest()}"


def _encoded(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _source_bank(
    root: Path,
    *,
    usages: tuple[dict[str, int] | None, ...] = (
        {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        {"prompt_tokens": 13, "completion_tokens": 5, "total_tokens": 18},
    ),
    latencies: tuple[int | None, ...] | None = None,
    request_model: str = "deepseek-v4-pro",
    configured_model: str = "deepseek-v4-pro",
    requested_model: str = "deepseek-v4-pro",
    resolved_model: str = "deepseek-v4-pro",
    response_model_status: str = "present",
    provenance_entry_id: str = "deepseek_v4_pro_exp1_baseline",
    provenance_provider_config_digest: str = "sha256:provider",
    provenance_inference_request_digest: str | None = None,
) -> ResponseBankResolver:
    resolved_latencies = (
        tuple(100 + slot for slot in range(len(usages)))
        if latencies is None
        else latencies
    )
    if len(resolved_latencies) != len(usages):
        raise ValueError("latencies must align with usages")
    rows: list[ResponseBankInventoryRow] = []
    objects_by_entry: list[dict[str, bytes]] = []
    for replacement_slot, (usage, latency_ms) in enumerate(
        zip(usages, resolved_latencies, strict=True)
    ):
        entry_id = f"entry-{replacement_slot}"
        request_body = _encoded(
            {"model": request_model, "slot": replacement_slot}
        )
        values = {
            "inventory_entry_id": "",
            "semantic_slot_key": semantic_slot_key(
                case_record_digest="sha256:case",
                planned_ai_unit_id="planned-unit",
                sample_slot_index=0,
                replacement_slot=replacement_slot,
                provider_config_digest="sha256:provider",
                prompt_profile_digest="sha256:prompt",
                prompt_admission_profile_digest="sha256:admission",
                plugin_version="2.0.0",
            ),
            "case_record_digest": "sha256:case",
            "planned_ai_unit_id": "planned-unit",
            "sample_slot_index": 0,
            "replacement_slot": replacement_slot,
            "provider_config_digest": "sha256:provider",
            "prompt_profile_digest": "sha256:prompt",
            "prompt_admission_profile_digest": "sha256:admission",
            "plugin_version": "2.0.0",
            "entry_id": entry_id,
            "body_digest": _digest(request_body),
            "inference_request_digest": _digest(request_body + b":inference"),
        }
        values["inventory_entry_id"] = inventory_entry_id(values)
        rows.append(ResponseBankInventoryRow(**values))
        objects_by_entry.append(
            {
                "request_body": request_body,
                "raw_output": _encoded(
                    {
                        "schema_version": "tokenshare.response_bank_raw_output.v1",
                        "raw_response_json": {"id": entry_id},
                        "content_text": '{"factors":[2,3]}',
                        "reasoning_content": None,
                        "provider_response_id": entry_id,
                        "finish_reason": "stop",
                    }
                ),
                "provenance": _encoded(
                    {
                        "schema_version": "tokenshare.response_bank_provenance.v1",
                        "provider_family": "deepseek",
                        "entry_id": provenance_entry_id,
                        "provider_config_digest": (
                            provenance_provider_config_digest
                        ),
                        "inference_request_digest": (
                            provenance_inference_request_digest
                            or values["inference_request_digest"]
                        ),
                        "normalized_absolute_endpoint": "https://api.deepseek.com/chat/completions",
                        "transport_call_count": 1,
                        "secret_persisted": False,
                        "receipt_digest": "sha256:receipt",
                    }
                ),
                "usage": _encoded(
                    {
                        "schema_version": "tokenshare.response_bank_usage.v1",
                        "usage_status": (
                            "reported" if usage is not None else "usage_missing"
                        ),
                        "usage": usage,
                    }
                ),
                "latency": _encoded(
                    {
                        "schema_version": "tokenshare.response_bank_latency.v1",
                        "latency_ms": latency_ms,
                        "timing_source": "provider_transport_observed",
                    }
                ),
                "pricing": _encoded(
                    {
                        "schema_version": "tokenshare.response_bank_pricing.v1",
                        "currency": "CNY",
                        "input_per_million_tokens": "1",
                        "output_per_million_tokens": "3",
                        "token_upper_bound": 1000,
                        "cost_upper_bound": "1",
                    }
                ),
                "acquisition_attempt": _encoded(
                    {
                        "schema_version": (
                            "tokenshare.response_bank_acquisition_attempt.v1"
                        ),
                        "attempt_id": f"acquisition-{replacement_slot}",
                        "linked_ambiguous_attempt_id": None,
                        "requested_at": "2026-08-09T00:00:00Z",
                        "terminal_kind": "success",
                        "failure_kind": None,
                    }
                ),
                "model_record": _encoded(
                    {
                        "schema_version": "tokenshare.response_bank_model_record.v1",
                        "configured_model": configured_model,
                        "requested_model": requested_model,
                        "resolved_model": resolved_model,
                        "response_model_status": response_model_status,
                    }
                ),
            }
        )

    inventory_digest = response_bank_inventory_digest(rows)
    manifest = ResponseBankManifest.create(
        bank_root_id="bank-root",
        profile_digest="sha256:profile",
        budget_digest="sha256:budget",
        inventory_digest=inventory_digest,
        provider_config_digest="sha256:provider",
        entry_ids=tuple(row.entry_id for row in rows),
        object_role_schema=OBJECT_ROLES,
        terminal_entry_count=len(rows),
        created_by_paid_receipt_digest="sha256:receipt",
    )
    entries: list[ResponseBankEntry] = []
    object_bytes: dict[str, bytes] = {}
    for row, role_objects in zip(rows, objects_by_entry, strict=True):
        locators = tuple(
            ExternalBankObjectLocator(
                bank_root_id=manifest.bank_root_id,
                manifest_digest=manifest.manifest_digest,
                entry_id=row.entry_id,
                object_role=role,
                object_digest=_digest(data),
            )
            for role, data in role_objects.items()
        )
        object_bytes.update(
            {
                locator.object_digest: role_objects[locator.object_role]
                for locator in locators
            }
        )
        entries.append(
            ResponseBankEntry(
                inventory_digest=inventory_digest,
                inventory_entry_id=row.inventory_entry_id,
                semantic_slot_key=row.semantic_slot_key,
                inference_request_digest=row.inference_request_digest,
                entry_id=row.entry_id,
                sample_slot_index=row.sample_slot_index,
                replacement_slot=row.replacement_slot,
                terminal_kind="success",
                object_locators=locators,
                acquisition_state_ref=f"acquisition-{row.replacement_slot}",
            )
        )
    initialize_response_bank(
        root,
        manifest=manifest,
        inventory_rows=rows,
        entries=entries,
        objects=object_bytes,
    )
    return ResponseBankResolver.open(root)


def _trace_runtime_and_result(
    tmp_path: Path,
    resolver: ResponseBankResolver,
    *,
    ordinals: tuple[int, ...],
    source_entry_ids_by_current_attempt: tuple[str, ...] | None = None,
    delivery_kinds_by_current_attempt: tuple[str, ...] | None = None,
    redelivery_reasons_by_current_attempt: tuple[str | None, ...] | None = None,
    current_planned_ai_unit_id: str = "planned-unit",
) -> tuple[SimpleNamespace, SimpleNamespace]:
    binding = (
        freeze_projected_trace_source_binding(
            resolver,
            current_planned_ai_unit_id=current_planned_ai_unit_id,
            current_sample_slot_index=0,
            source_entry_ids_by_current_attempt=(
                source_entry_ids_by_current_attempt
            ),
            delivery_kinds_by_current_attempt=delivery_kinds_by_current_attempt,
            redelivery_reasons_by_current_attempt=(
                redelivery_reasons_by_current_attempt
            ),
            source_evidence_class="approved_real_api_acquisition",
        )
        if source_entry_ids_by_current_attempt is not None
        else freeze_trace_source_binding(
            resolver,
            planned_ai_unit_id="planned-unit",
            sample_slot_index=0,
            entry_ids=tuple(entry.entry_id for entry in resolver.index.entries),
            source_evidence_class="approved_real_api_acquisition",
        )
    )
    executor = TraceBackedExecutor(
        resolver=resolver,
        bindings=(binding,),
        current_run_id="trace-run",
    )
    store = ArtifactStore(tmp_path / "current-run")
    events = []
    for ordinal in ordinals:
        delivery = executor.execute(
            _bound_request(binding, ordinal=ordinal),
            submission_id="ignored",
            submitted_at="ignored",
        )
        wrapper_ref = store.save_external_trace_wrapper(
            delivery.to_dict(),
            artifact_id=f"wrapper-{ordinal}",
            created_at="1970-01-01T00:00:00Z",
        )
        events.append(
            {
                "event_id": f"commit-{ordinal}",
                "event_type": "TRACE_DELIVERY_COMMITTED.v1",
                "payload": {
                    "attempt_id": delivery.attempt_id,
                    "current_wrapper_ref": wrapper_ref.to_dict(),
                },
            }
        )
    adapter_result = SimpleNamespace(
        output_root=store.root_path.as_posix(),
        event_records=tuple(events),
        attempt_results=tuple(
            {"attempt_id": f"attempt-{ordinal}", "provider_attempt_count": 0}
            for ordinal in ordinals
        ),
    )
    trace_runtime = SimpleNamespace(
        resolver=resolver,
        inventory_rows=resolver.index.inventory_rows,
        bindings=(binding,),
    )
    return trace_runtime, adapter_result


def _projected_summary(
    tmp_path: Path,
    resolver: ResponseBankResolver,
    *,
    ordinals: tuple[int, ...],
    source_provider_config_digest: str = "sha256:provider",
    source_entry_ids_by_current_attempt: tuple[str, ...] | None = None,
    delivery_kinds_by_current_attempt: tuple[str, ...] | None = None,
    redelivery_reasons_by_current_attempt: tuple[str | None, ...] | None = None,
    current_planned_ai_unit_id: str = "planned-unit",
) -> dict[str, object]:
    trace_runtime, adapter_result = _trace_runtime_and_result(
        tmp_path,
        resolver,
        ordinals=ordinals,
        source_entry_ids_by_current_attempt=source_entry_ids_by_current_attempt,
        delivery_kinds_by_current_attempt=delivery_kinds_by_current_attempt,
        redelivery_reasons_by_current_attempt=(
            redelivery_reasons_by_current_attempt
        ),
        current_planned_ai_unit_id=current_planned_ai_unit_id,
    )
    return formal_runner._project_committed_trace_source_usage(
        adapter_result=adapter_result,
        adapter_root=Path(adapter_result.output_root),
        trace_runtime=trace_runtime,
        condition=SimpleNamespace(
            provider_model_id="deepseek-v4-pro",
            model_entry_id="deepseek_v4_pro_exp1_baseline",
            source_provider_config_digest=source_provider_config_digest,
        ),
    )


def _exp2_direct_row(tmp_path: Path):
    row, condition_manifest, catalog = _inventory_row(
        evidence_class="real_model_trace_protocol_run",
        worker_count=3,
    )
    row = _replace_inventory_row(
        row,
        experiment_id="exp2_real_ai_scalability",
    )
    kwargs, *_ = _canonical_fixture(
        tmp_path,
        row,
        evidence_class="real_model_trace_protocol_run",
    )
    evidence = build_canonical_direct_evidence(**kwargs)
    return _project(
        _inventory_manifest(row),
        (condition_manifest,),
        (catalog,),
        {row.preregistered_root_run_id: evidence},
    ).rows[0]


def _paper_locators(resolver: ResponseBankResolver):
    return tuple(
        PaperExternalBankObjectLocator(
            bank_root_id=locator.bank_root_id,
            manifest_digest=locator.manifest_digest,
            entry_id=locator.entry_id,
            object_role=formal_runner._paper_trace_object_role(locator.object_role),
            object_digest=locator.object_digest,
        )
        for entry in resolver.index.entries
        for locator in entry.object_locators
    )


def test_projects_only_committed_source_usage_and_ignores_unused_replacement(
    tmp_path: Path,
) -> None:
    resolver = _source_bank(tmp_path / "bank")

    summary = _projected_summary(tmp_path, resolver, ordinals=(0,))

    assert summary["current_provider_call_count"] == 0
    assert summary["current_provider_spend_cny"] == "0"
    assert summary["committed_consumption_count"] == 1
    consumption = summary["consumptions"][0]
    assert consumption["consumption_id"] == "commit-0"
    assert consumption["entry_id"] == "entry-0"
    assert consumption["replacement_slot"] == 0
    assert consumption["prompt_tokens"] == 11
    assert consumption["completion_tokens"] == 7
    assert consumption["total_tokens"] == 18
    assert consumption["latency_ms"] == 100
    assert Decimal(consumption["cost_estimate_cny"]) == Decimal("0.000032")


def test_redelivery_keeps_current_ordinal_separate_from_source_attempt_accounting(
    tmp_path: Path,
) -> None:
    resolver = _source_bank(
        tmp_path / "bank",
        usages=({"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},),
    )

    summary = _projected_summary(
        tmp_path,
        resolver,
        ordinals=(0, 1, 2),
        source_entry_ids_by_current_attempt=("entry-0", "entry-0", "entry-0"),
        current_planned_ai_unit_id="exp3-current-unit",
    )

    consumptions = tuple(summary["consumptions"])
    assert summary["current_provider_call_count"] == 0
    assert summary["source_provider_attempt_count"] == 1
    assert [value["current_attempt_ordinal"] for value in consumptions] == [0, 1, 2]
    assert {value["entry_id"] for value in consumptions} == {"entry-0"}
    assert {value["planned_ai_unit_id"] for value in consumptions} == {
        "exp3-current-unit"
    }
    assert {value["source_planned_ai_unit_id"] for value in consumptions} == {
        "planned-unit"
    }
    assert {value["source_sample_slot_index"] for value in consumptions} == {0}
    assert {value["source_replacement_slot"] for value in consumptions} == {0}
    assert {value["replacement_slot"] for value in consumptions} == {0}
    assert formal_runner._complete_trace_source_sum(
        consumptions, "total_tokens"
    ) == 18
    assert formal_runner._complete_trace_source_sum(
        consumptions, "cost_estimate_cny", decimal=True
    ) == Decimal("0.000032")
    persisted = formal_runner._persisted_trace_source_consumptions(
        {"trace_source_usage": summary}
    )
    assert persisted == consumptions

    row = SimpleNamespace(
        preregistered_root_run_id="exp4-redelivery-root",
        case_id="case-1",
        preregistered_case_ref={"case_record_digest": "sha256:case"},
        repeat_id=0,
        final_result_reference_complete=True,
        end_to_end_verified_success=False,
        identity_consistent=True,
        paper_evidence_complete=True,
        infrastructure_valid=True,
        source_bank_object_locators=_paper_locators(resolver),
    )
    hydrated = formal_runner._hydrate_exp4_root(
        row,
        {
            "task": {"trace_source_usage": summary},
            "attempts": [],
            "run_evidence": {
                "protocol_runtime": {
                    "runtime_observation": {"runtime_wall_clock_ms": 9}
                }
            },
        },
    )
    assert hydrated.trace_attributed_tokens == 18
    assert hydrated.trace_attributed_cost == Decimal("0.000032")
    assert hydrated.replacement_slot_ids == ()


def test_exp3_paired_reference_preserves_current_and_source_identities(
    tmp_path: Path,
) -> None:
    resolver = _source_bank(
        tmp_path / "bank",
        usages=({"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},),
    )
    trace_runtime, _adapter_result = _trace_runtime_and_result(
        tmp_path,
        resolver,
        ordinals=(0, 1, 2),
        source_entry_ids_by_current_attempt=("entry-0", "entry-0", "entry-0"),
    )

    reference = formal_runner._paired_trace_reference_from_runtime(
        trace_runtime=trace_runtime,
        case_id="case-1",
    )

    assert reference["source_entry_ids"] == ["entry-0"]
    mappings = reference["current_delivery_source_mappings"]
    assert [value["current_attempt_ordinal"] for value in mappings] == [0, 1, 2]
    assert {value["source_replacement_slot"] for value in mappings} == {0}
    formal_runner._validate_persisted_exp3_source_reference(
        reference,
        resolver=resolver,
    )

    drifted = json.loads(json.dumps(reference))
    drifted["current_delivery_source_mappings"][1]["source_replacement_slot"] = 1
    with pytest.raises(ValueError, match="delivery/source identity drifted"):
        formal_runner._validate_persisted_exp3_source_reference(
            drifted,
            resolver=resolver,
        )


def test_projects_prepared_wire_config_when_tracked_source_config_differs(
    tmp_path: Path,
) -> None:
    resolver = _source_bank(tmp_path / "bank")

    summary = _projected_summary(
        tmp_path,
        resolver,
        ordinals=(0,),
        source_provider_config_digest="sha256:tracked-source-provider",
    )

    assert summary["committed_consumption_count"] == 1


def test_rejects_trace_runtime_inventory_row_binding_drift(tmp_path: Path) -> None:
    resolver = _source_bank(tmp_path / "bank")
    trace_runtime, adapter_result = _trace_runtime_and_result(
        tmp_path,
        resolver,
        ordinals=(0,),
    )
    trace_runtime.inventory_rows = (
        replace(
            resolver.index.inventory_rows[0],
            semantic_slot_key="sha256:drifted-slot",
        ),
        *resolver.index.inventory_rows[1:],
    )

    with pytest.raises(ValueError, match="formal inventory identity"):
        formal_runner._project_committed_trace_source_usage(
            adapter_result=adapter_result,
            adapter_root=Path(adapter_result.output_root),
            trace_runtime=trace_runtime,
            condition=SimpleNamespace(
                provider_model_id="deepseek-v4-pro",
                model_entry_id="deepseek_v4_pro_exp1_baseline",
                source_provider_config_digest="sha256:tracked-source-provider",
            ),
        )


def test_missing_source_usage_remains_null_without_zero_fill(tmp_path: Path) -> None:
    resolver = _source_bank(tmp_path / "bank", usages=(None,))

    summary = _projected_summary(tmp_path, resolver, ordinals=(0,))

    consumption = summary["consumptions"][0]
    assert consumption["prompt_tokens"] is None
    assert consumption["completion_tokens"] is None
    assert consumption["total_tokens"] is None
    assert consumption["cost_estimate_cny"] is None
    assert consumption["latency_ms"] == 100
    assert formal_runner._sum_required_numbers(
        tuple(summary["consumptions"]),
        "cost_estimate_cny",
        decimal=True,
    ) is None


def test_nullable_source_summary_reports_known_and_missing_by_unique_attempt(
    tmp_path: Path,
) -> None:
    resolver = _source_bank(
        tmp_path / "bank",
        usages=(
            {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
            None,
        ),
    )

    summary = _projected_summary(
        tmp_path,
        resolver,
        ordinals=(0, 1, 2),
        source_entry_ids_by_current_attempt=("entry-0", "entry-1", "entry-1"),
    )

    assert summary["committed_consumption_count"] == 3
    assert summary["source_provider_attempt_count"] == 2
    assert summary["source_tokens_total"] is None
    assert summary["source_tokens_known_total"] == 18
    assert summary["source_tokens_missing_attempt_count"] == 1
    assert summary["source_api_latency_total_ms"] == 201
    assert summary["source_api_latency_known_total_ms"] == 201
    assert summary["source_api_latency_missing_attempt_count"] == 0
    assert summary["source_cost_total_cny"] is None
    assert summary["source_cost_known_total_cny"] == "0.000032"
    assert summary["source_cost_missing_attempt_count"] == 1
    assert formal_runner._persisted_trace_source_consumptions(
        {"trace_source_usage": summary}
    ) == tuple(summary["consumptions"])

    drifted = json.loads(json.dumps(summary))
    drifted["source_tokens_known_total"] = 19
    with pytest.raises(ValueError, match="known totals are inconsistent"):
        formal_runner._persisted_trace_source_consumptions(
            {"trace_source_usage": drifted}
        )


def test_missing_source_latency_stays_nullable_through_exp2_hydration(
    tmp_path: Path,
) -> None:
    resolver = _source_bank(
        tmp_path / "bank",
        usages=(
            {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        ),
        latencies=(None,),
    )
    summary = _projected_summary(tmp_path, resolver, ordinals=(0,))
    consumption = summary["consumptions"][0]
    assert consumption["latency_ms"] is None
    assert consumption["source_api_latency_missing"] is True
    assert consumption["source_api_latency_missing_count"] == 1
    assert summary["source_api_latency_total_ms"] is None
    assert summary["source_api_latency_known_total_ms"] == 0
    assert summary["source_api_latency_missing_attempt_count"] == 1

    direct = _exp2_direct_row(tmp_path / "direct")
    unit_id = consumption["unit_id"]
    hydrated = formal_runner._hydrate_exp2_trace_metric_row(
        direct,
        {
            "task": {"trace_source_usage": summary},
            "attempts": [],
            "run_evidence": {
                "protocol_runtime": {
                    "runtime_observation": {
                        "runtime_wall_clock_ms": 1,
                        "planned_ai_unit_ids": [unit_id],
                        "dispatched_ai_unit_ids": [unit_id],
                        "completed_ai_unit_ids": [unit_id],
                        "in_flight_ai_unit_ids_at_witness": [],
                        "observed_peak_concurrency": 1,
                    }
                }
            },
        },
    )

    assert hydrated.source_api_latency_total_ms is None
    assert hydrated.source_api_latency_known_total_ms == 0
    assert hydrated.source_api_latency_missing_attempt_count == 1
    assert hydrated.ai_units is None


def test_exp3_nullable_trace_pair_keeps_known_coverage_and_fixed_member(
    tmp_path: Path,
) -> None:
    resolver = _source_bank(
        tmp_path / "bank",
        usages=(
            {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
            None,
        ),
    )
    summary = _projected_summary(tmp_path, resolver, ordinals=(0, 1))
    row = SimpleNamespace(
        preregistered_root_run_id="exp3-nullable-root",
        final_result_reference_complete=True,
        end_to_end_verified_success=False,
        condition_axes={"sample_slot_index": 0},
    )

    observations = formal_runner._persisted_exp3_metric_observations(
        row,
        {
            "faults": [],
            "events": [],
            "attempts": [],
            "task": {
                "trace_source_usage": summary,
                "paired_trace_reference": {
                    "source_entry_ids": ["entry-0", "entry-1"],
                },
            },
        },
    )

    pair = next(
        value.facts
        for value in observations
        if value.facts["member_kind"] == "exp3_trace_pair"
    )
    assert pair["fault_trace_attributed_tokens"] is None
    assert pair["fault_trace_attributed_tokens_known_total"] == 18
    assert pair["fault_trace_attributed_tokens_missing_attempt_count"] == 1
    assert pair["fault_trace_attributed_cost"] is None
    assert pair["fault_trace_attributed_cost_known_total"] == Decimal("0.000032")
    assert pair["fault_trace_attributed_cost_missing_attempt_count"] == 1
    assert pair["pair_evidence_complete"] is True


def test_exp3_reference_and_fault_account_all_ordinary_attempts_once(
    tmp_path: Path,
) -> None:
    resolver = _source_bank(
        tmp_path / "bank",
        usages=(
            {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
            None,
            {"prompt_tokens": 13, "completion_tokens": 5, "total_tokens": 18},
        ),
        latencies=(100, None, 102),
    )
    summary = _projected_summary(
        tmp_path,
        resolver,
        ordinals=(0, 1, 2, 3),
        source_entry_ids_by_current_attempt=(
            "entry-0",
            "entry-1",
            "entry-2",
            "entry-2",
        ),
        delivery_kinds_by_current_attempt=(
            "ordinary_attempt",
            "ordinary_attempt",
            "ordinary_attempt",
            "fault_redelivery",
        ),
        redelivery_reasons_by_current_attempt=(
            None,
            None,
            None,
            "worker_death_requeue",
        ),
    )
    row = SimpleNamespace(
        preregistered_root_run_id="exp3-complete-source-trace",
        final_result_reference_complete=True,
        end_to_end_verified_success=False,
        condition_axes={"sample_slot_index": 0},
    )
    observations = formal_runner._persisted_exp3_metric_observations(
        row,
        {
            "faults": [],
            "events": [],
            "attempts": [],
            "task": {
                "trace_source_usage": summary,
                "paired_trace_reference": {
                    "source_entry_ids": ["entry-0", "entry-1", "entry-2"],
                },
            },
        },
    )
    pair = next(
        value.facts
        for value in observations
        if value.facts["member_kind"] == "exp3_trace_pair"
    )

    for prefix in ("fault", "reference"):
        assert pair[f"{prefix}_trace_attributed_tokens"] is None
        assert pair[f"{prefix}_trace_attributed_tokens_known_total"] == 36
        assert pair[f"{prefix}_trace_attributed_tokens_missing_attempt_count"] == 1
        assert pair[f"{prefix}_trace_attributed_cost"] is None
        assert pair[f"{prefix}_trace_attributed_cost_known_total"] == Decimal(
            "0.000060"
        )
        assert pair[f"{prefix}_trace_attributed_cost_missing_attempt_count"] == 1
        assert pair[f"{prefix}_source_api_latency_total_ms"] is None
        assert pair[f"{prefix}_source_api_latency_known_total_ms"] == 202
        assert pair[f"{prefix}_source_api_latency_missing_attempt_count"] == 1
    assert pair["reference_protocol_fault_or_recovery_delay_ms"] == 0
    assert pair["fault_protocol_fault_or_recovery_delay_ms"] == 102


def test_persisted_latency_missing_count_is_strict_by_delivery_kind(
    tmp_path: Path,
) -> None:
    resolver = _source_bank(
        tmp_path / "bank",
        usages=(
            {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        ),
        latencies=(None,),
    )
    summary = _projected_summary(
        tmp_path,
        resolver,
        ordinals=(0, 1),
        source_entry_ids_by_current_attempt=("entry-0", "entry-0"),
        delivery_kinds_by_current_attempt=(
            "ordinary_attempt",
            "fault_redelivery",
        ),
        redelivery_reasons_by_current_attempt=(None, "worker_death_requeue"),
    )
    ordinary, redelivery = summary["consumptions"]
    assert ordinary["delivery_kind"] == "ordinary_attempt"
    assert ordinary["source_api_latency_missing_count"] == 1
    assert redelivery["delivery_kind"] == "fault_redelivery"
    assert redelivery["source_api_latency_missing_count"] == 0
    assert redelivery["source_api_latency_ref"] == ordinary["source_api_latency_ref"]

    for index, invalid_count in ((0, 0), (1, 1)):
        drifted = json.loads(json.dumps(summary))
        drifted["consumptions"][index][
            "source_api_latency_missing_count"
        ] = invalid_count
        with pytest.raises(ValueError, match="latency missing count"):
            formal_runner._persisted_trace_source_consumptions(
                {"trace_source_usage": drifted}
            )


def test_exp3_discarded_source_attempt_keeps_nullable_resource_coverage() -> None:
    facts = formal_runner._exp3_discarded_source_resource_facts(
        {
            "consumption_id": "discarded-commit",
            "unit_id": "discarded-unit",
            "source_acquisition_attempt_id": "discarded-source-attempt",
            "total_tokens": None,
            "latency_ms": None,
            "cost_estimate_cny": None,
        }
    )

    assert facts["source_usage_total_tokens"] is None
    assert facts["source_usage_total_tokens_known_total"] == 0
    assert facts["source_usage_total_tokens_missing_attempt_count"] == 1
    assert facts["source_api_latency_total_ms"] is None
    assert facts["source_api_latency_known_total_ms"] == 0
    assert facts["source_api_latency_missing_attempt_count"] == 1
    assert facts["source_cost_total_cny"] is None
    assert facts["source_cost_known_total_cny"] == Decimal(0)
    assert facts["source_cost_missing_attempt_count"] == 1


@pytest.mark.parametrize(
    "bank_kwargs",
    (
        {"response_model_status": "missing"},
        {"configured_model": "wrong-model"},
        {"requested_model": "wrong-model"},
        {"resolved_model": "wrong-model"},
        {"request_model": "wrong-model"},
        {"provenance_entry_id": "wrong-entry"},
        {"provenance_provider_config_digest": "sha256:wrong-provider"},
        {"provenance_inference_request_digest": "sha256:wrong-request"},
    ),
)
def test_rejects_source_metrics_when_committed_model_identity_is_not_matched(
    tmp_path: Path,
    bank_kwargs: dict[str, str],
) -> None:
    resolver = _source_bank(tmp_path / "bank", **bank_kwargs)

    with pytest.raises(ValueError, match="trace source .*identity"):
        _projected_summary(tmp_path, resolver, ordinals=(0,))


def test_exp2_trace_hydration_uses_source_tokens_cost_and_latency(
    tmp_path: Path,
) -> None:
    resolver = _source_bank(tmp_path / "bank")
    summary = _projected_summary(tmp_path, resolver, ordinals=(0,))
    direct = _exp2_direct_row(tmp_path / "direct")
    unit_id = summary["consumptions"][0]["unit_id"]
    facts = {
        "task": {"trace_source_usage": summary},
        "attempts": [],
        "run_evidence": {
            "protocol_runtime": {
                "runtime_observation": {
                    "runtime_wall_clock_ms": 100,
                    "planned_ai_unit_ids": [unit_id],
                    "dispatched_ai_unit_ids": [unit_id],
                    "completed_ai_unit_ids": [unit_id],
                    "in_flight_ai_unit_ids_at_witness": [],
                    "observed_peak_concurrency": 1,
                }
            }
        },
    }

    hydrated = formal_runner._hydrate_exp2_trace_metric_row(direct, facts)

    assert len(hydrated.trace_consumptions) == 1
    assert hydrated.trace_consumptions[0].source_total_tokens == 18
    assert hydrated.trace_consumptions[0].source_cost_estimate_cny == Decimal(
        "0.000032"
    )
    assert hydrated.ai_units[0].busy_worker_time_ms == 100


def test_exp4_trace_hydration_uses_committed_source_usage_and_replacement_slots(
    tmp_path: Path,
) -> None:
    resolver = _source_bank(tmp_path / "bank")
    summary = _projected_summary(tmp_path, resolver, ordinals=(0, 1))
    assert summary["source_provider_attempt_count"] == 2
    assert summary["source_tokens_total"] == 36
    assert summary["source_api_latency_total_ms"] == 201
    assert summary["source_cost_total_cny"] == "0.000060"
    assert [
        value["current_attempt_ordinal"] for value in summary["consumptions"]
    ] == [0, 1]
    assert [
        value["source_replacement_slot"] for value in summary["consumptions"]
    ] == [0, 1]
    row = SimpleNamespace(
        preregistered_root_run_id="root-1",
        case_id="case-1",
        preregistered_case_ref={"case_record_digest": "sha256:case"},
        repeat_id=0,
        final_result_reference_complete=True,
        end_to_end_verified_success=False,
        identity_consistent=True,
        paper_evidence_complete=True,
        infrastructure_valid=True,
        source_bank_object_locators=_paper_locators(resolver),
    )
    facts = {
        "task": {"trace_source_usage": summary},
        "attempts": [],
        "run_evidence": {
            "protocol_runtime": {
                "runtime_observation": {"runtime_wall_clock_ms": 201}
            }
        },
    }

    hydrated = formal_runner._hydrate_exp4_root(row, facts)

    assert hydrated.replacement_slot_ids == ("entry-1",)
    assert hydrated.trace_attributed_tokens == 36
    assert hydrated.trace_attributed_cost == Decimal("0.000060")
    assert hydrated.end_to_end_verified_success is False


def test_exp4_missing_source_usage_is_null_not_zero(tmp_path: Path) -> None:
    resolver = _source_bank(tmp_path / "bank", usages=(None,))
    summary = _projected_summary(tmp_path, resolver, ordinals=(0,))
    assert summary["source_tokens_total"] is None
    assert summary["source_tokens_known_total"] == 0
    assert summary["source_tokens_missing_attempt_count"] == 1
    assert summary["source_cost_total_cny"] is None
    assert summary["source_cost_known_total_cny"] == "0"
    assert summary["source_cost_missing_attempt_count"] == 1
    row = SimpleNamespace(
        preregistered_root_run_id="root-1",
        case_id="case-1",
        preregistered_case_ref={"case_record_digest": "sha256:case"},
        repeat_id=0,
        final_result_reference_complete=True,
        end_to_end_verified_success=False,
        identity_consistent=True,
        paper_evidence_complete=True,
        infrastructure_valid=True,
        source_bank_object_locators=_paper_locators(resolver),
    )
    facts = {
        "task": {"trace_source_usage": summary},
        "attempts": [],
        "run_evidence": {
            "protocol_runtime": {"runtime_observation": {"runtime_wall_clock_ms": 100}}
        },
    }

    hydrated = formal_runner._hydrate_exp4_root(row, facts)

    assert hydrated.trace_attributed_tokens is None
    assert hydrated.trace_attributed_cost is None
