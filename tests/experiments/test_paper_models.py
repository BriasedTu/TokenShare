from dataclasses import replace

import pytest

import tokenshare.experiments.paper_models as paper_models
from tokenshare.executors.response_bank import (
    COMMON_ROLES,
    OBJECT_ROLES,
    CurrentTraceWrapper,
    ExternalBankObjectLocator as CanonicalBankObjectLocator,
    ResponseBankEntry,
    ResponseBankInventoryRow,
    ResponseBankManifest,
    canonical_digest,
    inventory_entry_id,
    semantic_slot_key,
)
from tokenshare.executors.trace_backed import (
    APPROVED_REAL_SOURCE_EVIDENCE_CLASS,
    TraceReplacementBinding,
    TraceSourceBinding,
)
from tokenshare.experiments.paper_models import (
    ExternalBankObjectLocator,
    PaperAttemptResult,
    PaperAttemptStatus,
    PaperBudgetResult,
    PaperConditionResult,
    PaperExperimentCondition,
    PaperExperimentResult,
    PaperModelExecutionRecord,
    PaperRunResult,
    PaperStatus,
    PaperSuiteResult,
    PaperTaskResult,
    PaperTaskStatus,
    digest_json,
)
from tokenshare.experiments.paper_unit_commitments import build_ai_unit_binding


def _unit_binding(index: int, *, domain: str = "factorization") -> dict:
    unit_id = f"unit-{index}"
    commitment_kind = (
        "factorization_range.v1"
        if domain == "factorization"
        else "lean_simple_child.v1"
    )
    return build_ai_unit_binding(
        planned_ai_unit_id=f"planned-{index}",
        unit_id=unit_id,
        task_unit_snapshot={"unit_id": unit_id, "payload_digest": digest_json(index)},
        domain_unit_commitment={
            "schema_version": "tokenshare.paper_domain_unit_commitment.v1",
            "domain": domain,
            "commitment_kind": commitment_kind,
            "planned_ai_unit_id": f"planned-{index}",
            "unit_id": unit_id,
        },
    )


def _online_evidence_facts(*, executed_ai_unit_count: int = 2) -> dict:
    bindings = [_unit_binding(index) for index in range(executed_ai_unit_count)]
    attempts = [
        {
            "schema_version": "tokenshare.paper_current_online_attempt_ref.v1",
            "planned_ai_unit_id": binding["planned_ai_unit_id"],
            "unit_id": binding["unit_id"],
            "unit_binding_digest": binding["binding_digest"],
            "attempt_id": f"attempt-{index}",
            "request_identity_digest": digest_json({"request": index}),
            "real_transport": True,
            "identity_consistent": True,
        }
        for index, binding in enumerate(bindings)
    ]
    lifecycle = [
        {
            "schema_version": "tokenshare.paper_current_online_lifecycle_ref.v1",
            "planned_ai_unit_id": binding["planned_ai_unit_id"],
            "unit_id": binding["unit_id"],
            "unit_binding_digest": binding["binding_digest"],
            "attempt_ref": f"attempt-{index}",
            "event_ref": f"event-{index}",
            "artifact_ref": f"artifact-{index}",
            "terminal_ref": f"terminal-{index}",
        }
        for index, binding in enumerate(bindings)
    ]
    return {
        "schema_version": "tokenshare.paper_evidence_eligibility_facts.v2",
        "evidence_class": "online_real_provider",
        "source_classification": "current_real_provider",
        "executed_ai_unit_count": executed_ai_unit_count,
        "executed_unit_bindings": bindings,
        "current_provider_call_count": executed_ai_unit_count,
        "source_provider_call_count": 0,
        "current_real_provider_attempt_refs": attempts,
        "current_lifecycle_refs": lifecycle,
        "trace_source_bindings": [],
        "source_manifest": None,
        "source_inventory_rows": [],
        "source_entries": [],
        "paid_receipt_claim": None,
        "direct_evidence_complete": True,
        "identity_consistent": True,
        "regression_only": False,
    }


def _trace_evidence_facts(
    *,
    terminal_kind: str = "success",
    domain: str = "factorization",
    executed_ai_unit_count: int = 1,
    replacement_count: int = 1,
    redelivery_count: int = 1,
) -> dict:
    bindings = [
        _unit_binding(index, domain=domain)
        for index in range(executed_ai_unit_count)
    ]
    receipt_digest = digest_json({"paid_receipt": "receipt-1"})
    provider_digest = digest_json({"provider": 0})
    prompt_digest = digest_json({"prompt": 0})
    admission_digest = digest_json({"admission": 0})
    rows = []
    for unit_index, binding in enumerate(bindings):
        case_digest = digest_json({"case": unit_index})
        for replacement_slot in range(replacement_count):
            entry_id = f"entry-{unit_index}-{replacement_slot}"
            row = ResponseBankInventoryRow(
                inventory_entry_id="",
                semantic_slot_key=semantic_slot_key(
                    case_record_digest=case_digest,
                    planned_ai_unit_id=binding["planned_ai_unit_id"],
                    sample_slot_index=unit_index,
                    replacement_slot=replacement_slot,
                    provider_config_digest=provider_digest,
                    prompt_profile_digest=prompt_digest,
                    prompt_admission_profile_digest=admission_digest,
                    plugin_version="plugin-v1",
                ),
                case_record_digest=case_digest,
                planned_ai_unit_id=binding["planned_ai_unit_id"],
                sample_slot_index=unit_index,
                replacement_slot=replacement_slot,
                provider_config_digest=provider_digest,
                prompt_profile_digest=prompt_digest,
                prompt_admission_profile_digest=admission_digest,
                plugin_version="plugin-v1",
                entry_id=entry_id,
                body_digest=digest_json(
                    {"body": unit_index, "replacement": replacement_slot}
                ),
                inference_request_digest=digest_json(
                    {"inference": unit_index, "replacement": replacement_slot}
                ),
            )
            rows.append(replace(row, inventory_entry_id=inventory_entry_id(row)))
    inventory_digest = canonical_digest(
        [
            row.to_dict()
            for row in sorted(rows, key=lambda item: item.inventory_entry_id)
        ]
    )
    manifest = ResponseBankManifest.create(
        bank_root_id="bank-root-1",
        profile_digest=digest_json({"profile": 0}),
        budget_digest=digest_json({"budget": 0}),
        inventory_digest=inventory_digest,
        provider_config_digest=provider_digest,
        entry_ids=tuple(sorted(row.entry_id for row in rows)),
        object_role_schema=OBJECT_ROLES,
        terminal_entry_count=len(rows),
        created_by_paid_receipt_digest=receipt_digest,
    )
    manifest = replace(
        manifest,
        root_binding_marker_digest=digest_json({"root_marker": 0}),
    )
    entries = []
    locators_by_entry = {}
    terminal_role = (
        "raw_output" if terminal_kind == "success" else "provider_failure"
    )
    for row in rows:
        roles = COMMON_ROLES | {terminal_role}
        locators = tuple(
            CanonicalBankObjectLocator(
                bank_root_id=manifest.bank_root_id,
                manifest_digest=manifest.manifest_digest,
                entry_id=row.entry_id,
                object_role=role,
                object_digest=(
                    row.body_digest
                    if role == "request_body"
                    else digest_json({role: row.entry_id})
                ),
            )
            for role in sorted(roles)
        )
        locators_by_entry[row.entry_id] = locators
        entries.append(
            ResponseBankEntry(
                inventory_digest=inventory_digest,
                inventory_entry_id=row.inventory_entry_id,
                semantic_slot_key=row.semantic_slot_key,
                inference_request_digest=row.inference_request_digest,
                entry_id=row.entry_id,
                sample_slot_index=row.sample_slot_index,
                replacement_slot=row.replacement_slot,
                terminal_kind=terminal_kind,
                object_locators=locators,
                acquisition_state_ref=f"acquisition-{row.entry_id}",
            )
        )
    entries_by_slot = {
        (row.planned_ai_unit_id, row.replacement_slot): entry
        for row, entry in zip(rows, entries, strict=True)
    }
    source_bindings = []
    wrappers = []
    for unit_index, binding in enumerate(bindings):
        replacements = tuple(
            TraceReplacementBinding(
                replacement_slot=current_ordinal,
                entry_id=entries_by_slot[
                    (
                        binding["planned_ai_unit_id"],
                        0 if redelivery_count > 1 else current_ordinal,
                    )
                ].entry_id,
                inference_request_digest=entries_by_slot[
                    (
                        binding["planned_ai_unit_id"],
                        0 if redelivery_count > 1 else current_ordinal,
                    )
                ].inference_request_digest,
            )
            for current_ordinal in range(
                redelivery_count if redelivery_count > 1 else replacement_count
            )
        )
        source_bindings.append(
            TraceSourceBinding.create(
                planned_ai_unit_id=binding["planned_ai_unit_id"],
                sample_slot_index=unit_index,
                bank_root_id=manifest.bank_root_id,
                manifest_digest=manifest.manifest_digest,
                replacements=replacements,
                source_evidence_class=APPROVED_REAL_SOURCE_EVIDENCE_CLASS,
            )
        )
        selected_entry = entries_by_slot[(binding["planned_ai_unit_id"], 0)]
        selected_locators = locators_by_entry[selected_entry.entry_id]
        wrappers.extend(
            CurrentTraceWrapper(
                current_run_id="run-1",
                current_task_id="task-1",
                current_unit_id=binding["unit_id"],
                current_attempt_id=f"attempt-{unit_index}-{current_ordinal}",
                attempt_ordinal=current_ordinal,
                bank_root_id=manifest.bank_root_id,
                manifest_digest=manifest.manifest_digest,
                root_binding_marker_digest=manifest.root_binding_marker_digest,
                inference_request_digest=selected_entry.inference_request_digest,
                entry_id=selected_entry.entry_id,
                locator_digests={
                    item.object_role: item.object_digest
                    for item in selected_locators
                },
                logical_started_at="2026-08-02T00:00:00Z",
                logical_finished_at="2026-08-02T00:00:01Z",
                source_latency_ms=1,
                current_parse_ref=(
                    "parse-1" if terminal_kind == "success" else None
                ),
                current_verifier_ref=(
                    "verifier-1"
                    if terminal_kind == "success" and domain == "factorization"
                    else None
                ),
                current_checker_ref=(
                    "checker-1"
                    if terminal_kind == "success" and domain == "lean_proof"
                    else None
                ),
                current_canonical_ref=(
                    "canonical-1" if terminal_kind == "success" else None
                ),
                current_ledger_ref=f"ledger-{unit_index}-{current_ordinal}",
            )
            for current_ordinal in range(redelivery_count)
        )
    return {
        "schema_version": "tokenshare.paper_evidence_eligibility_facts.v2",
        "evidence_class": "real_model_trace_protocol_run",
        "source_classification": "approved_real_full_acquisition",
        "executed_ai_unit_count": executed_ai_unit_count,
        "executed_unit_bindings": bindings,
        "current_provider_call_count": 0,
        "source_provider_call_count": len(entries),
        "current_real_provider_attempt_refs": [],
        "current_lifecycle_refs": [wrapper.to_dict() for wrapper in wrappers],
        "trace_source_bindings": [binding.to_dict() for binding in source_bindings],
        "source_manifest": manifest.to_dict(),
        "source_inventory_rows": [row.to_dict() for row in rows],
        "source_entries": [entry.to_dict() for entry in entries],
        "paid_receipt_claim": {
            "schema_version": "tokenshare.paid_execution_receipt_claim.v1",
            "receipt_scope": "epd027_full_bank_acquisition",
            "receipt_digest": receipt_digest,
            "manifest_digest": manifest.manifest_digest,
        },
        "direct_evidence_complete": True,
        "identity_consistent": True,
        "regression_only": False,
    }


def test_online_class_requires_current_real_provider_attempt_per_executed_unit() -> None:
    facts = _online_evidence_facts()
    attempts = facts["current_real_provider_attempt_refs"]
    cross_ref = [dict(attempts[0]), {**attempts[1], "attempt_id": "attempt-cross"}]
    duplicate = [dict(attempts[0]), dict(attempts[0])]
    wrong_unit = [dict(attempts[0]), {**attempts[1], "unit_id": "unit-cross"}]

    for invalid_attempts in (cross_ref, duplicate, wrong_unit):
        report = paper_models.evaluate_versioned_paper_evidence(
            {**facts, "current_real_provider_attempt_refs": invalid_attempts}
        )
        assert "online_unit_attempt_identity_mismatch" in report.ineligibility_reasons
    assert paper_models.evaluate_versioned_paper_evidence(facts).paper_eligible is True


def test_trace_class_requires_current_calls_zero_and_dual_provenance() -> None:
    facts = _trace_evidence_facts()

    current_call = paper_models.evaluate_versioned_paper_evidence(
        {**facts, "current_provider_call_count": 1}
    )
    missing_current = paper_models.evaluate_versioned_paper_evidence(
        {**facts, "current_lifecycle_refs": []}
    )
    entries = facts["source_entries"]
    missing_source = paper_models.evaluate_versioned_paper_evidence(
        {**facts, "source_entries": []}
    )
    duplicate_source = paper_models.evaluate_versioned_paper_evidence(
        {**facts, "source_entries": [entries[0], entries[0]]}
    )
    cross_manifest_entry = dict(entries[0])
    cross_manifest_entry["object_locators"] = [
        {**locator, "manifest_digest": digest_json({"other": "manifest"})}
        for locator in cross_manifest_entry["object_locators"]
    ]
    cross_manifest = paper_models.evaluate_versioned_paper_evidence(
        {**facts, "source_entries": [cross_manifest_entry]}
    )
    wrappers = facts["current_lifecycle_refs"]
    wrong_current_entry = paper_models.evaluate_versioned_paper_evidence(
        {
            **facts,
            "current_lifecycle_refs": [{**wrappers[0], "entry_id": "entry-cross"}],
        }
    )
    wrong_current_slot = paper_models.evaluate_versioned_paper_evidence(
        {
            **facts,
            "current_lifecycle_refs": [{**wrappers[0], "attempt_ordinal": 1}],
        }
    )
    success_missing_parse = paper_models.evaluate_versioned_paper_evidence(
        {
            **facts,
            "current_lifecycle_refs": [
                {**wrappers[0], "current_parse_ref": None}
            ],
        }
    )
    success_missing_verifier = paper_models.evaluate_versioned_paper_evidence(
        {
            **facts,
            "current_lifecycle_refs": [
                {**wrappers[0], "current_verifier_ref": None}
            ],
        }
    )
    success_missing_canonical = paper_models.evaluate_versioned_paper_evidence(
        {
            **facts,
            "current_lifecycle_refs": [
                {**wrappers[0], "current_canonical_ref": None}
            ],
        }
    )
    lean_facts = _trace_evidence_facts(domain="lean_proof")
    lean_wrappers = lean_facts["current_lifecycle_refs"]
    success_missing_checker = paper_models.evaluate_versioned_paper_evidence(
        {
            **lean_facts,
            "current_lifecycle_refs": [
                {**lean_wrappers[0], "current_checker_ref": None}
            ],
        }
    )
    multi_facts = _trace_evidence_facts(
        executed_ai_unit_count=2,
        replacement_count=2,
    )
    multi_bindings = [
        TraceSourceBinding.from_dict(item)
        for item in multi_facts["trace_source_bindings"]
    ]
    drifted_bindings = [
        TraceSourceBinding.create(
            planned_ai_unit_id=binding.planned_ai_unit_id,
            sample_slot_index=binding.sample_slot_index,
            bank_root_id=binding.bank_root_id,
            manifest_digest=binding.manifest_digest,
            replacements=(
                binding.replacements[0],
                TraceReplacementBinding(
                    replacement_slot=1,
                    entry_id=binding.replacements[1].entry_id,
                    inference_request_digest=(
                        multi_bindings[1 - index]
                        .replacements[1]
                        .inference_request_digest
                    ),
                ),
            ),
            source_evidence_class=binding.source_evidence_class,
        ).to_dict()
        for index, binding in enumerate(multi_bindings)
    ]
    drifted_unexecuted_replacements = (
        paper_models.evaluate_versioned_paper_evidence(
            {**multi_facts, "trace_source_bindings": drifted_bindings}
        )
    )

    assert "trace_current_provider_calls_must_be_zero" in current_call.ineligibility_reasons
    assert "current_trace_identity_chain_invalid" in missing_current.ineligibility_reasons
    assert "canonical_source_manifest_invalid" in missing_source.ineligibility_reasons
    assert "canonical_source_manifest_invalid" in duplicate_source.ineligibility_reasons
    assert "canonical_source_manifest_invalid" in cross_manifest.ineligibility_reasons
    assert "current_trace_identity_chain_invalid" in (
        wrong_current_entry.ineligibility_reasons
    )
    assert "current_trace_identity_chain_invalid" in (
        wrong_current_slot.ineligibility_reasons
    )
    for missing_terminal_stage in (
        success_missing_parse,
        success_missing_verifier,
        success_missing_canonical,
        success_missing_checker,
    ):
        assert "current_trace_identity_chain_invalid" in (
            missing_terminal_stage.ineligibility_reasons
        )
    assert "current_trace_identity_chain_invalid" in (
        drifted_unexecuted_replacements.ineligibility_reasons
    )
    assert paper_models.evaluate_versioned_paper_evidence(lean_facts).paper_eligible is True
    assert paper_models.evaluate_versioned_paper_evidence(facts).paper_eligible is True


def test_trace_class_accepts_same_source_artifact_protocol_redelivery() -> None:
    facts = _trace_evidence_facts(redelivery_count=3)

    report = paper_models.evaluate_versioned_paper_evidence(facts)

    assert report.paper_eligible is True
    assert report.current_provider_call_count == 0
    wrappers = facts["current_lifecycle_refs"]
    assert [wrapper["attempt_ordinal"] for wrapper in wrappers] == [0, 1, 2]
    assert {wrapper["entry_id"] for wrapper in wrappers} == {"entry-0-0"}

    drifted = paper_models.evaluate_versioned_paper_evidence(
        {
            **facts,
            "current_lifecycle_refs": [
                wrappers[0],
                {
                    **wrappers[1],
                    "inference_request_digest": digest_json({"drifted": True}),
                },
                wrappers[2],
            ],
        }
    )
    assert "current_trace_identity_chain_invalid" in drifted.ineligibility_reasons


@pytest.mark.parametrize(
    "evidence_class",
    ("capability_only", "historical", "synthetic", "regression_only"),
)
def test_capability_historical_and_synthetic_sources_are_always_ineligible(
    evidence_class: str,
) -> None:
    facts = {
        **_trace_evidence_facts(),
        "evidence_class": evidence_class,
        "source_classification": evidence_class,
    }

    report = paper_models.evaluate_versioned_paper_evidence(facts)

    assert report.paper_eligible is False
    expected_reason = (
        "regression_only"
        if evidence_class == "regression_only"
        else f"evidence_class_always_ineligible:{evidence_class}"
    )
    assert expected_reason in report.ineligibility_reasons


def test_provider_failure_entry_can_be_complete_source_evidence() -> None:
    facts = _trace_evidence_facts(terminal_kind="provider_failure")
    entries = facts["source_entries"]
    roles = {item["object_role"] for item in entries[0]["object_locators"]}

    report = paper_models.evaluate_versioned_paper_evidence(facts)
    missing_provenance_entry = {
        **entries[0],
        "object_locators": [
            item
            for item in entries[0]["object_locators"]
            if item["object_role"] != "provenance"
        ],
    }
    missing_terminal_entry = {
        **entries[0],
        "object_locators": [
            item
            for item in entries[0]["object_locators"]
            if item["object_role"] != "provider_failure"
        ],
    }
    missing_provenance = paper_models.evaluate_versioned_paper_evidence(
        {**facts, "source_entries": [missing_provenance_entry]}
    )
    missing_terminal = paper_models.evaluate_versioned_paper_evidence(
        {**facts, "source_entries": [missing_terminal_entry]}
    )
    wrappers = facts["current_lifecycle_refs"]
    forged_success_ref = paper_models.evaluate_versioned_paper_evidence(
        {
            **facts,
            "current_lifecycle_refs": [
                {**wrappers[0], "current_canonical_ref": "forged-canonical"}
            ],
        }
    )
    missing_ledger = paper_models.evaluate_versioned_paper_evidence(
        {
            **facts,
            "current_lifecycle_refs": [
                {**wrappers[0], "current_ledger_ref": None}
            ],
        }
    )

    assert report.paper_eligible is True
    assert "provider_failure" in roles
    assert "raw_output" not in roles
    assert "canonical_source_manifest_invalid" in missing_provenance.ineligibility_reasons
    assert "canonical_source_manifest_invalid" in missing_terminal.ineligibility_reasons
    assert "current_trace_identity_chain_invalid" in forged_success_ref.ineligibility_reasons
    assert "current_trace_identity_chain_invalid" in missing_ledger.ineligibility_reasons


def _executor_error_attempt() -> PaperAttemptResult:
    return PaperAttemptResult(
        condition_id="exp3-condition",
        repeat_id=0,
        run_id="exp3-run",
        task_id="task-root-1",
        unit_id="unit-root-1",
        attempt_id="attempt-root-1",
        worker_id="worker-root-1",
        provider_attempt_index=0,
        attempt_status=PaperAttemptStatus.EXECUTOR_ERROR,
        provider=None,
        model=None,
        entry_id=None,
        request_ref={"artifact_id": "request-root-1"},
        raw_output_ref=None,
        parsed_output_ref=None,
        parse_failure_ref=None,
        provenance_ref=None,
        usage_ref=None,
        started_at="2026-07-28T00:00:00Z",
        ended_at="2026-07-28T00:00:01Z",
        latency_ms=0,
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        cost_estimate=0.0,
        error_kind="retry_limit_reached",
        fault_injection_ref=None,
        paper_eligible=False,
        model_execution_record_ref=None,
        provider_attempt_count=0,
        executor_id="executor_factorization_runtime",
        executor_type="deterministic_local",
        schema_version="tokenshare.paper_attempt_result.v2",
    )


def _ai_pre_provider_executor_error_attempt() -> PaperAttemptResult:
    return replace(
        _executor_error_attempt(),
        executor_id="executor_ai_api",
        executor_type="ai_api_pre_provider",
        provenance_ref={"artifact_id": "provenance-root-1"},
        error_kind="config_error",
    )


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("provider_attempt_index", False),
        ("provider_attempt_index", 0.0),
        ("provider_attempt_index", -1),
        ("provider_attempt_count", False),
        ("provider_attempt_count", 0.0),
        ("provider_attempt_count", -1),
        ("prompt_tokens", False),
        ("prompt_tokens", 0.0),
        ("prompt_tokens", -1),
        ("completion_tokens", False),
        ("completion_tokens", 0.0),
        ("completion_tokens", -1),
        ("total_tokens", False),
        ("total_tokens", 0.0),
        ("total_tokens", -1),
        ("cost_estimate", False),
        ("cost_estimate", -0.1),
        ("latency_ms", False),
        ("latency_ms", 0.0),
        ("latency_ms", -1),
        ("error_kind", None),
        ("error_kind", ""),
        ("executor_id", "executor_ai_api"),
        ("executor_type", "ai_api"),
        ("provider", "deepseek"),
        ("model", "deepseek-v4-pro"),
        ("entry_id", "deepseek_v4_pro_exp1_baseline"),
        ("raw_output_ref", {"artifact_id": "raw-1"}),
        ("parsed_output_ref", {"artifact_id": "parsed-1"}),
        ("parse_failure_ref", {"artifact_id": "parse-failure-1"}),
        ("provenance_ref", {"artifact_id": "provenance-1"}),
        ("usage_ref", {"artifact_id": "usage-1"}),
        ("fault_injection_ref", {"artifact_id": "fault-1"}),
        ("model_execution_record_ref", {"artifact_id": "model-record-1"}),
        ("request_ref", None),
        ("request_ref", {}),
        ("paper_eligible", True),
    ),
)
def test_executor_error_v2_rejects_values_outside_closed_schema(
    field_name: str,
    invalid_value,
) -> None:
    attempt = _executor_error_attempt()

    with pytest.raises(ValueError):
        replace(attempt, **{field_name: invalid_value})


def test_executor_error_v2_allows_explicit_ai_pre_provider_source() -> None:
    attempt = _ai_pre_provider_executor_error_attempt()

    assert attempt.attempt_status == PaperAttemptStatus.EXECUTOR_ERROR
    assert attempt.provider_attempt_count == 0
    assert attempt.provider is None
    assert attempt.raw_output_ref is None
    assert attempt.usage_ref is None
    assert attempt.model_execution_record_ref is None
    assert attempt.provenance_ref == {"artifact_id": "provenance-root-1"}
    assert attempt.executor_id == "executor_ai_api"
    assert attempt.executor_type == "ai_api_pre_provider"


def test_ai_pre_provider_executor_error_without_provenance_stays_ineligible() -> None:
    attempt = replace(
        _ai_pre_provider_executor_error_attempt(),
        provenance_ref=None,
    )

    assert attempt.provenance_ref is None
    assert attempt.provider_attempt_count == 0
    assert attempt.paper_eligible is False
    with pytest.raises(ValueError, match="paper-ineligible"):
        replace(attempt, paper_eligible=True)


def test_provider_failure_v3_serializes_missing_usage_as_null() -> None:
    attempt = PaperAttemptResult(
        condition_id="condition1",
        repeat_id=0,
        run_id="run1",
        task_id="task1",
        unit_id="unit1",
        attempt_id="attempt1",
        worker_id="worker1",
        provider_attempt_index=0,
        provider_attempt_count=1,
        attempt_status=PaperAttemptStatus.PROVIDER_ERROR,
        provider="siliconflow",
        model="zai-org/GLM-5.2",
        entry_id="glm_5_2_exp5_v3",
        request_ref={"artifact_id": "request1"},
        raw_output_ref=None,
        parsed_output_ref=None,
        parse_failure_ref=None,
        provenance_ref={"artifact_id": "provenance1"},
        usage_ref={"artifact_id": "usage1"},
        model_execution_record_ref={"artifact_id": "model-record1"},
        started_at="2026-07-31T00:00:00Z",
        ended_at="2026-07-31T00:00:01Z",
        latency_ms=1000,
        prompt_tokens=None,
        completion_tokens=None,
        total_tokens=None,
        cost_estimate=None,
        cost_estimate_status="usage_missing",
        error_kind="timeout",
        fault_injection_ref=None,
        paper_eligible=True,
        schema_version="tokenshare.paper_attempt_result.v3",
    )

    body = attempt.to_dict()

    assert body["prompt_tokens"] is None
    assert body["completion_tokens"] is None
    assert body["total_tokens"] is None
    assert body["cost_estimate"] is None
    assert body["cost_estimate_status"] == "usage_missing"

    with pytest.raises(ValueError):
        replace(
            attempt,
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            cost_estimate=0.1,
        )


def test_provider_attempt_v3_accepts_only_latency_missing_with_complete_usage() -> None:
    attempt = PaperAttemptResult(
        condition_id="condition1",
        repeat_id=0,
        run_id="run1",
        task_id="task1",
        unit_id="unit1",
        attempt_id="attempt1",
        worker_id="worker1",
        provider_attempt_index=0,
        provider_attempt_count=1,
        attempt_status=PaperAttemptStatus.SUCCEEDED,
        provider="siliconflow",
        model="zai-org/GLM-5.2",
        entry_id="glm_5_2_exp5_v3",
        request_ref={"artifact_id": "request1"},
        raw_output_ref={"artifact_id": "raw1"},
        parsed_output_ref={"artifact_id": "parsed1"},
        parse_failure_ref=None,
        provenance_ref={"artifact_id": "provenance1"},
        usage_ref={"artifact_id": "usage1"},
        model_execution_record_ref={"artifact_id": "model-record1"},
        started_at="2026-07-31T00:00:00Z",
        ended_at="2026-07-31T00:00:01Z",
        latency_ms=None,
        prompt_tokens=10,
        completion_tokens=20,
        total_tokens=30,
        cost_estimate=0.1,
        cost_estimate_status="estimated",
        error_kind=None,
        fault_injection_ref=None,
        paper_eligible=True,
        schema_version="tokenshare.paper_attempt_result.v3",
    )

    assert attempt.to_dict()["latency_ms"] is None
    with pytest.raises(ValueError):
        replace(attempt, cost_estimate_status="usage_missing")


def test_model_execution_record_v2_represents_provider_failure_as_not_observed() -> None:
    record = PaperModelExecutionRecord(
        condition_id="condition1",
        repeat_id=0,
        run_id="run1",
        task_id="task1",
        unit_id="unit1",
        attempt_id="attempt1",
        expected_identity={"provider_model_id": "gpt-5.6-sol"},
        source_provider_config_digest="sha256:" + "1" * 64,
        prepared_execution_config_digest="sha256:" + "2" * 64,
        request_ref={"artifact_id": "request1"},
        provenance_ref={"artifact_id": "provenance1"},
        raw_output_ref=None,
        usage_ref={"artifact_id": "usage1"},
        actual_request_identities=[],
        actual_provider_attempts=[],
        requested_model="gpt-5.6-sol",
        resolved_model=None,
        response_model_status="unavailable",
        identity_status="not_observed",
        mismatch_reasons=[],
        paper_eligible=False,
        created_at="2026-07-17T00:00:00Z",
    )

    body = record.to_dict()
    assert body["schema_version"] == "tokenshare.paper_model_execution_record.v2"
    assert body["identity_status"] == "not_observed"
    assert body["requested_model"] == "gpt-5.6-sol"
    assert body["resolved_model"] is None
    assert body["response_model_status"] == "unavailable"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"response_model_status": "present", "resolved_model": None}, "present"),
        (
            {"response_model_status": "missing", "resolved_model": "gpt-5.6-sol"},
            "resolved_model",
        ),
        (
            {"response_model_status": "missing", "identity_status": "not_observed"},
            "not_observed",
        ),
        (
            {"schema_version": "tokenshare.paper_model_execution_record.v1"},
            "schema_version",
        ),
        (
            {
                "identity_status": "matched",
                "response_model_status": "present",
                "resolved_model": "different-model",
                "raw_output_ref": {"artifact_id": "raw1"},
                "paper_eligible": True,
            },
            "expected_identity",
        ),
        (
            {
                "identity_status": "matched",
                "requested_model": "different-request-model",
                "response_model_status": "present",
                "resolved_model": "gpt-5.6-sol",
                "raw_output_ref": {"artifact_id": "raw1"},
                "paper_eligible": True,
            },
            "requested_model",
        ),
    ],
)
def test_model_execution_record_v2_rejects_internally_inconsistent_identity(
    changes: dict,
    message: str,
) -> None:
    record = PaperModelExecutionRecord(
        condition_id="condition1",
        repeat_id=0,
        run_id="run1",
        task_id="task1",
        unit_id="unit1",
        attempt_id="attempt1",
        expected_identity={"provider_model_id": "gpt-5.6-sol"},
        source_provider_config_digest="sha256:" + "1" * 64,
        prepared_execution_config_digest="sha256:" + "2" * 64,
        request_ref={"artifact_id": "request1"},
        provenance_ref={"artifact_id": "provenance1"},
        raw_output_ref=None,
        usage_ref={"artifact_id": "usage1"},
        actual_request_identities=[],
        actual_provider_attempts=[],
        requested_model="gpt-5.6-sol",
        resolved_model=None,
        response_model_status="unavailable",
        identity_status="not_observed",
        mismatch_reasons=[],
        paper_eligible=False,
        created_at="2026-07-17T00:00:00Z",
    )

    with pytest.raises(ValueError, match=message):
        replace(record, **changes)


def test_paper_condition_digest_is_stable_and_records_required_controls() -> None:
    condition = PaperExperimentCondition(
        experiment_id="exp2_real_ai_scalability",
        condition_id="exp2_factorization_medium_w10_repeat0",
        domain="factorization",
        difficulty="medium",
        worker_count=10,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=42,
        catalog_digest="sha256:" + "1" * 64,
    )
    same_condition = PaperExperimentCondition(
        experiment_id="exp2_real_ai_scalability",
        condition_id="exp2_factorization_medium_w10_repeat0",
        domain="factorization",
        difficulty="medium",
        worker_count=10,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=42,
        catalog_digest="sha256:" + "1" * 64,
    )

    body = condition.to_dict()

    assert body["schema_version"] == "tokenshare.paper_condition.v1"
    assert body["paper_difficulty"] == "medium"
    assert body["real_transport_required"] is True
    assert body["paper_eligible_required"] is True
    assert condition.condition_digest == same_condition.condition_digest


def test_formal_exp5_condition_records_complete_fixed_entry_identity() -> None:
    condition = PaperExperimentCondition(**_formal_exp5_condition_body())

    body = condition.to_dict()

    assert body["model_cohort_id"] == "tokenshare.paper.model_endpoint_cohort.v1"
    assert body["model_cohort_digest"] == "sha256:" + "2" * 64
    assert body["cohort_member_id"] == "gpt_5_6_sol_high_openai"
    assert body["provider_config_id"] == "openai"
    assert body["model_entry_id"] == "gpt-entry"
    assert body["provider_family"] == "openai"
    assert body["provider_model_id"] == "gpt-5.6-sol"
    assert body["reasoning_profile_id"] == "high"
    assert body["source_provider_config_digest"] == "sha256:" + "3" * 64
    assert body["model_endpoint_identity_digest"] == "sha256:" + "4" * 64


@pytest.mark.parametrize(
    "missing_field",
    [
        "model_cohort_id",
        "model_cohort_digest",
        "cohort_member_id",
        "provider_config_id",
        "model_entry_id",
        "provider_family",
        "provider_model_id",
        "reasoning_profile_id",
        "source_provider_config_digest",
        "model_endpoint_identity_digest",
    ],
)
def test_formal_exp5_condition_rejects_incomplete_fixed_entry_identity(
    missing_field: str,
) -> None:
    body = _formal_exp5_condition_body()
    body[missing_field] = None

    with pytest.raises(ValueError, match="complete fixed-entry identity"):
        PaperExperimentCondition(**body)


@pytest.mark.parametrize(
    "digest_field",
    [
        "model_cohort_digest",
        "source_provider_config_digest",
        "model_endpoint_identity_digest",
    ],
)
def test_formal_exp5_condition_rejects_malformed_identity_digest(
    digest_field: str,
) -> None:
    body = _formal_exp5_condition_body()
    body[digest_field] = "sha256:not-a-complete-digest"

    with pytest.raises(ValueError, match=digest_field):
        PaperExperimentCondition(**body)


def test_paper_condition_supports_explicit_paper_difficulty_in_digest() -> None:
    simple_condition = PaperExperimentCondition(
        experiment_id="exp1_real_ai_feasibility",
        condition_id="exp1_lean_medium_legacy_simple_repeat0",
        domain="lean_proof",
        difficulty="medium",
        paper_difficulty="simple",
        worker_count=10,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=1,
        catalog_digest="sha256:" + "1" * 64,
    )
    lemma_dag_condition = PaperExperimentCondition(
        experiment_id="exp1_real_ai_feasibility",
        condition_id="exp1_lean_medium_legacy_simple_repeat0",
        domain="lean_proof",
        difficulty="medium",
        paper_difficulty="medium_lemma_dag",
        worker_count=10,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=1,
        catalog_digest="sha256:" + "1" * 64,
    )

    assert simple_condition.to_dict()["paper_difficulty"] == "simple"
    assert lemma_dag_condition.to_dict()["paper_difficulty"] == "medium_lemma_dag"
    assert simple_condition.condition_digest != lemma_dag_condition.condition_digest


def test_paper_condition_roundtrip_records_topic_and_provenance_fields() -> None:
    condition = PaperExperimentCondition(
        experiment_id="exp1_real_ai_feasibility",
        condition_id="exp1_lean_medium_pure_logic_repeat0",
        domain="lean_proof",
        difficulty="medium",
        paper_difficulty="medium_lemma_dag",
        topic_family="pure_logic",
        topic_family_version="v1",
        construction_rule_id="fixed_oracle_lemma_graph.pure_logic.v1",
        oracle_package_group="lean_lemma_graph_oracle.pure_logic.v1",
        proof_assembly_shape="recursive_lemma_dag_required_slots.v1",
        worker_count=10,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=1,
        catalog_digest="sha256:" + "1" * 64,
    )

    body = condition.to_dict()

    assert body["topic_family"] == "pure_logic"
    assert body["topic_family_version"] == "v1"
    assert body["construction_rule_id"] == "fixed_oracle_lemma_graph.pure_logic.v1"
    assert body["oracle_package_group"] == "lean_lemma_graph_oracle.pure_logic.v1"
    assert body["proof_assembly_shape"] == "recursive_lemma_dag_required_slots.v1"


def test_paper_task_result_roundtrip_records_topic_and_provenance_fields() -> None:
    task = PaperTaskResult(
        condition_id="cond1",
        repeat_id=0,
        task_id="lean_v2_medium_lemma_dag_01",
        domain="lean_proof",
        difficulty="medium",
        paper_difficulty="medium_lemma_dag",
        topic_family="pure_logic",
        topic_family_version="v1",
        construction_rule_id="fixed_oracle_lemma_graph.pure_logic.v1",
        oracle_package_group="lean_lemma_graph_oracle.pure_logic.v1",
        proof_assembly_shape="recursive_lemma_dag_required_slots.v1",
        root_status=PaperTaskStatus.BLOCKED,
        accepted_validity=None,
        failure_stage=None,
        failure_kind=None,
        attempt_count=0,
        provider_attempt_count=0,
        wall_clock_ms=0,
        total_tokens=0,
        cost_estimate=0.0,
        event_refs=[],
        artifact_refs=[],
        paper_eligible=False,
    )

    body = task.to_dict()

    assert body["paper_difficulty"] == "medium_lemma_dag"
    assert body["topic_family"] == "pure_logic"
    assert body["topic_family_version"] == "v1"
    assert body["construction_rule_id"] == "fixed_oracle_lemma_graph.pure_logic.v1"
    assert body["oracle_package_group"] == "lean_lemma_graph_oracle.pure_logic.v1"
    assert body["proof_assembly_shape"] == "recursive_lemma_dag_required_slots.v1"

    with pytest.raises(ValueError):
        replace(task, total_tokens=None)
    nullable = replace(
        task,
        total_tokens=None,
        cost_estimate=None,
        cost_estimate_status="usage_missing",
        schema_version="tokenshare.paper_task_result.v2",
    )
    assert nullable.to_dict()["total_tokens"] is None
    with pytest.raises(ValueError):
        replace(
            task,
            cost_estimate_status="usage_missing",
            schema_version="tokenshare.paper_task_result.v2",
        )


def test_paper_condition_digest_changes_for_topic_family_or_construction_rule() -> None:
    base = {
        "experiment_id": "exp1_real_ai_feasibility",
        "condition_id": "exp1_lean_medium_repeat0",
        "domain": "lean_proof",
        "difficulty": "medium",
        "paper_difficulty": "medium_lemma_dag",
        "topic_family": "pure_logic",
        "topic_family_version": "v1",
        "construction_rule_id": "fixed_oracle_lemma_graph.pure_logic.v1",
        "oracle_package_group": "lean_lemma_graph_oracle.pure_logic.v1",
        "proof_assembly_shape": "recursive_lemma_dag_required_slots.v1",
        "worker_count": 10,
        "fault_type": "none",
        "fault_rate": 0.0,
        "ablation_mode": "FULL",
        "model_policy": "fixed_entry",
        "repeat_id": 0,
        "seed": 1,
        "catalog_digest": "sha256:" + "1" * 64,
    }

    first = PaperExperimentCondition(**base)
    changed_topic = PaperExperimentCondition(**{**base, "topic_family": "function_set"})
    changed_rule = PaperExperimentCondition(
        **{
            **base,
            "construction_rule_id": "fixed_oracle_lemma_graph.function_set.v1",
        }
    )

    assert first.condition_digest != changed_topic.condition_digest
    assert first.condition_digest != changed_rule.condition_digest


def test_paper_condition_rejects_invalid_lean_topic_family() -> None:
    with pytest.raises(ValueError, match="topic_family"):
        PaperExperimentCondition(
            experiment_id="exp1_real_ai_feasibility",
            condition_id="exp1_lean_algebra_repeat0",
            domain="lean_proof",
            difficulty="medium",
            paper_difficulty="medium_lemma_dag",
            topic_family="algebra",
            worker_count=10,
            fault_type="none",
            fault_rate=0.0,
            ablation_mode="FULL",
            model_policy="fixed_entry",
            repeat_id=0,
            seed=1,
            catalog_digest="sha256:" + "1" * 64,
        )


def test_paper_result_objects_serialize_stable_status_schema_and_evidence_refs() -> None:
    attempt = PaperAttemptResult(
        condition_id="cond1",
        repeat_id=0,
        run_id="run1",
        task_id="task1",
        unit_id="unit1",
        attempt_id="attempt1",
        worker_id="worker1",
        provider_attempt_index=0,
        attempt_status=PaperAttemptStatus.SUCCEEDED,
        provider="siliconflow",
        model="glm",
        entry_id="glm_5_2__sf_key_1",
        request_ref={"artifact_id": "request1"},
        raw_output_ref={"artifact_id": "raw1"},
        parsed_output_ref={"artifact_id": "parsed1"},
        parse_failure_ref=None,
        provenance_ref={"artifact_id": "prov1"},
        usage_ref={"artifact_id": "usage1"},
        started_at="2026-07-14T00:00:00Z",
        ended_at="2026-07-14T00:00:01Z",
        latency_ms=1234,
        prompt_tokens=100,
        completion_tokens=156,
        total_tokens=256,
        cost_estimate=0.001,
        error_kind=None,
        fault_injection_ref=None,
        paper_eligible=True,
    )
    task = PaperTaskResult(
        condition_id="cond1",
        repeat_id=0,
        task_id="task1",
        domain="lean_proof",
        difficulty="easy",
        paper_difficulty="simple",
        root_status=PaperTaskStatus.COMPLETED,
        accepted_validity=True,
        failure_stage=None,
        failure_kind=None,
        attempt_count=1,
        provider_attempt_count=1,
        wall_clock_ms=2000,
        total_tokens=256,
        cost_estimate=0.001,
        event_refs=[{"event_id": "evt1"}],
        artifact_refs=[{"artifact_id": "raw1"}],
        paper_eligible=True,
    )
    run = PaperRunResult(
        condition_id="cond1",
        repeat_id=0,
        run_id="run1",
        status=PaperStatus.COMPLETED,
        run_manifest_ref={"path": "run_manifest.json"},
        per_task_results_ref={"path": "per_task_results.jsonl"},
        per_attempt_results_ref={"path": "per_attempt_results.jsonl"},
        fault_injections_ref={"path": "fault_injections.jsonl"},
        event_log_ref={"path": "events/event_log.jsonl"},
        artifact_root="artifacts",
        paper_eligible=True,
        ineligibility_reasons=[],
    )
    condition = PaperConditionResult(
        condition_id="cond1",
        status=PaperStatus.COMPLETED,
        repeat_count=1,
        task_count=1,
        completed_root_count=1,
        failed_root_count=0,
        blocked_root_count=0,
        provider_attempt_count=1,
        metrics_ref={"path": "metrics/cond1.json"},
    )
    experiment = PaperExperimentResult(
        experiment_id="exp1_real_ai_feasibility",
        status=PaperStatus.COMPLETED,
        condition_ids=["cond1"],
        run_count=1,
        task_count=1,
        completion_rate=1.0,
        accepted_validity_rate=1.0,
        total_tokens=256,
        total_cost_estimate=0.001,
        summary_ref={"path": "summary.json"},
    )
    budget = PaperBudgetResult(
        budget_digest="sha256:" + "2" * 64,
        planned_experiments=["exp1_real_ai_feasibility"],
        planned_conditions=1,
        planned_root_runs=1,
        planned_ai_units=2,
        max_provider_attempts=2,
        token_upper_bound=2048,
        cost_upper_bound=0.1,
        wall_clock_estimate=60.0,
        quota_preflight={"status": "not_checked"},
        rate_limit_preflight={"status": "not_checked"},
        disk_estimate={"bytes": 4096},
        status=PaperStatus.PLANNED,
    )
    suite = PaperSuiteResult(
        suite_id="paper_v1_plan",
        status=PaperStatus.PLANNED,
        output_root="outputs/experiments/paper_v1/paper_v1_plan",
        started_at="2026-07-14T00:00:00Z",
        ended_at="2026-07-14T00:00:01Z",
        experiment_ids=["exp1_real_ai_feasibility"],
        condition_count=1,
        run_count=1,
        task_count=1,
        provider_attempt_count=0,
        total_tokens=0,
        total_cost_estimate=0.0,
        paper_eligible=False,
        eligibility_report_ref={"path": "audit/paper_eligibility_report.json"},
        budget_ref=budget.to_dict(),
        metrics_refs=[],
        audit_refs=[],
        error_summary=[],
    )

    attempt_body = attempt.to_dict()
    assert attempt_body["schema_version"] == "tokenshare.paper_attempt_result.v1"
    assert attempt_body["provider_attempt_index"] == 0
    assert attempt_body["started_at"] == "2026-07-14T00:00:00Z"
    assert attempt_body["ended_at"] == "2026-07-14T00:00:01Z"
    assert attempt_body["prompt_tokens"] == 100
    assert attempt_body["completion_tokens"] == 156
    assert task.to_dict()["paper_difficulty"] == "simple"
    assert task.to_dict()["root_status"] == "completed"
    assert run.to_dict()["status"] == "completed"
    assert condition.to_dict()["provider_attempt_count"] == 1
    assert experiment.to_dict()["accepted_validity_rate"] == 1.0
    assert budget.to_dict()["status"] == "planned"
    assert suite.to_dict()["paper_eligible"] is False

def test_invalid_paper_status_values_are_rejected() -> None:
    with pytest.raises(ValueError, match="status"):
        PaperExperimentResult(
            experiment_id="exp1",
            status="almost_done",
            condition_ids=[],
            run_count=0,
            task_count=0,
            completion_rate=0.0,
            accepted_validity_rate=0.0,
            total_tokens=0,
            total_cost_estimate=0.0,
            summary_ref={},
        )


def test_paper_condition_rejects_unknown_domain_or_difficulty() -> None:
    base = {
        "experiment_id": "exp1_real_ai_feasibility",
        "condition_id": "cond1",
        "domain": "factorization",
        "difficulty": "easy",
        "worker_count": 10,
        "fault_type": "none",
        "fault_rate": 0.0,
        "ablation_mode": "FULL",
        "model_policy": "fixed_entry",
        "repeat_id": 0,
        "seed": 1,
        "catalog_digest": "sha256:" + "1" * 64,
    }

    with pytest.raises(ValueError, match="domain"):
        PaperExperimentCondition(**{**base, "domain": "lean-proof"})
    with pytest.raises(ValueError, match="difficulty"):
        PaperExperimentCondition(**{**base, "difficulty": "all"})


def _formal_exp5_condition_body() -> dict:
    return {
        "experiment_id": "exp5_real_ai_model_endpoint_comparison",
        "condition_id": "exp5_factorization_easy_gpt_repeat0",
        "domain": "factorization",
        "difficulty": "easy",
        "worker_count": 10,
        "fault_type": "none",
        "fault_rate": 0.0,
        "ablation_mode": "FULL",
        "model_policy": "fixed_entry",
        "model_cohort_id": "tokenshare.paper.model_endpoint_cohort.v1",
        "model_cohort_digest": "sha256:" + "2" * 64,
        "cohort_member_id": "gpt_5_6_sol_high_openai",
        "provider_config_id": "openai",
        "model_entry_id": "gpt-entry",
        "provider_family": "openai",
        "provider_model_id": "gpt-5.6-sol",
        "reasoning_profile_id": "high",
        "source_provider_config_digest": "sha256:" + "3" * 64,
        "model_endpoint_identity_digest": "sha256:" + "4" * 64,
        "repeat_id": 0,
        "seed": 1,
        "catalog_digest": "sha256:" + "1" * 64,
    }


def test_external_bank_object_locator_is_opaque_and_path_free() -> None:
    locator = ExternalBankObjectLocator(
        bank_root_id="approved-bank-root",
        manifest_digest=digest_json({"manifest": 1}),
        entry_id="entry-1",
        object_role="raw_output",
        object_digest=digest_json({"raw": 1}),
    )

    assert locator.to_dict() == {
        "schema_version": "tokenshare.external_bank_object_locator.v1",
        "bank_root_id": "approved-bank-root",
        "manifest_digest": digest_json({"manifest": 1}),
        "entry_id": "entry-1",
        "object_role": "raw_output",
        "object_digest": digest_json({"raw": 1}),
    }
    assert not ({"path", "uri", "artifact_ref"} & set(locator.to_dict()))
