from __future__ import annotations

import json
import multiprocessing
from threading import Event, Lock
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from tests.phase7_fixtures import FakeProviderResponse
from tokenshare.experiments import paper_response_bank as response_bank_module
from tokenshare.executors import ai_api_hard_deadline
from tokenshare.executors.ai_api import PreparedDispatchEvidence
from tokenshare.executors.ai_api_request_identity import PreparedOutboundRequestFactory
from tokenshare.executors.response_bank import (
    COMMON_ROLES,
    OBJECT_ROLES,
    ResponseBankManifest,
    ResponseBankResolver,
    ResponseBankInventoryRow,
    initialize_response_bank,
    inventory_entry_id,
    response_bank_inventory_digest,
    semantic_slot_key,
    terminal_bank_entry_id,
)
from tokenshare.experiments.paper_budget import PaperBudgetLimits
from tokenshare.experiments.paper_budget_ledger import PaperBudgetLedger
from tokenshare.experiments.paper_resource_accounting import FrozenPricing
from tokenshare.experiments.paper_paid_authorization import (
    PaidAuthorizationError,
    compute_receipt_digest,
    output_root_path_digest,
    validate_paid_execution_receipt,
)
from tokenshare.experiments.paper_response_bank import (
    PROVIDER_FAILURE_TAXONOMY,
    AcquisitionAuthorizationError,
    AcquisitionIdentityError,
    AcquisitionRequest,
    ResponseBankAcquisitionOrchestrator,
    SemanticInventoryPlan,
    build_paper_formal_trace_context,
    create_acquisition_plan_bundle,
    finalize_acquisition_child_bank,
    response_bank_manifest_for_bundle,
)
from tokenshare.storage.artifacts import ArtifactStore


class ScriptedExactTransport:
    def __init__(self, outcomes: list[Any], calls: Any | None = None) -> None:
        self.outcomes = list(outcomes)
        self.calls = [] if calls is None else calls

    def post_chat_completion(self, **kwargs: Any) -> FakeProviderResponse:
        self.calls.append(
            {
                "body_bytes": kwargs["body_bytes"],
                "normalized_absolute_endpoint": kwargs[
                    "normalized_absolute_endpoint"
                ],
            }
        )
        outcome = self.outcomes.pop(0)
        if outcome == "timeout":
            raise TimeoutError("fake timeout")
        if outcome == "connection_error":
            raise OSError("fake connection error")
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class HardDeadlineNetworkBomb:
    tokenshare_hard_total_deadline = True

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def post_chat_completion(self, **kwargs: Any) -> FakeProviderResponse:
        self.calls.append(kwargs)
        raise AssertionError("pre-dispatch child failure reached provider network")


class BoundedParallelProbeTransport:
    def __init__(self) -> None:
        self.calls: list[bytes] = []
        self.active = 0
        self.max_active = 0
        self._lock = Lock()
        self._release = Event()

    def post_chat_completion(self, **kwargs: Any) -> FakeProviderResponse:
        with self._lock:
            self.calls.append(kwargs["body_bytes"])
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if self.active >= 2:
                self._release.set()
        self._release.wait(timeout=0.5)
        try:
            return _success_response("parallel")
        finally:
            with self._lock:
                self.active -= 1


class FailureStopsNewSubmissionsTransport(BoundedParallelProbeTransport):
    def post_chat_completion(self, **kwargs: Any) -> FakeProviderResponse:
        with self._lock:
            self.calls.append(kwargs["body_bytes"])
            call_index = len(self.calls) - 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if self.active >= 2:
                self._release.set()
        self._release.wait(timeout=0.5)
        try:
            if call_index == 0:
                return FakeProviderResponse(
                    status_code=503, body={"message": "provider failure"}
                )
            return _success_response("in-flight")
        finally:
            with self._lock:
                self.active -= 1


def _success_response(marker: str = "ok") -> FakeProviderResponse:
    return FakeProviderResponse(
        status_code=200,
        body={
            "id": f"response-{marker}",
            "model": "deepseek-v4-pro",
            "choices": [
                {
                    "message": {
                        "content": json.dumps({"answer": marker}),
                        "reasoning_content": "fake reasoning",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        },
    )


def _failure_outcome(kind: str) -> Any:
    if kind in {"timeout", "connection_error"}:
        return kind
    if kind == "invalid_output":
        return FakeProviderResponse(
            status_code=200,
            body={"id": "invalid", "model": "deepseek-v4-pro", "choices": []},
        )
    status = {
        "rate_limited": 429,
        "provider_error": 503,
        "auth_error": 401,
        "client_error": 400,
    }[kind]
    return FakeProviderResponse(status_code=status, body={"message": kind})


def _prepared(
    *,
    unit: str = "unit-0",
    marker: str = "same",
    replacement_slot: int = 0,
):
    return PreparedOutboundRequestFactory.prepare(
        body_obj={
            "model": "deepseek-v4-pro",
            "messages": [{"role": "user", "content": marker}],
            "max_tokens": 300000,
        },
        base_url="https://api.deepseek.com",
        endpoint="/chat/completions",
        provider_config_digest="sha256:" + "1" * 64,
        entry_id="deepseek_v4_pro_exp1_baseline",
        configured_model="deepseek-v4-pro",
        effective_controls_digest="sha256:" + "2" * 64,
        plugin_id="factorization",
        plugin_version="factorization.v1",
        prompt_profile_id="factorization.range_search.v2",
        prompt_serialization_schema="tokenshare.prompt.v2",
        body_serialization_schema="openai_chat_completions.v1",
        case_id="case-a",
        planned_ai_unit_id=unit,
        sample_slot_index=0,
        replacement_slot=replacement_slot,
    )


def _row(prepared: Any, *, slot_override: str | None = None):
    slot = slot_override or semantic_slot_key(
        case_record_digest="sha256:" + "3" * 64,
        planned_ai_unit_id=prepared.planned_ai_unit_id,
        sample_slot_index=prepared.sample_slot_index,
        replacement_slot=prepared.replacement_slot,
        provider_config_digest=prepared.provider_config_digest,
        prompt_profile_digest="sha256:" + "4" * 64,
        prompt_admission_profile_digest=prepared.prompt_admission_profile_digest,
        plugin_version=prepared.plugin_version,
    )
    provisional = ResponseBankInventoryRow(
        inventory_entry_id="",
        semantic_slot_key=slot,
        case_record_digest="sha256:" + "3" * 64,
        planned_ai_unit_id=prepared.planned_ai_unit_id,
        sample_slot_index=prepared.sample_slot_index,
        replacement_slot=prepared.replacement_slot,
        provider_config_digest=prepared.provider_config_digest,
        prompt_profile_digest="sha256:" + "4" * 64,
        prompt_admission_profile_digest=prepared.prompt_admission_profile_digest,
        plugin_version=prepared.plugin_version,
        entry_id=terminal_bank_entry_id(
            semantic_slot_key=slot,
            inference_request_digest=prepared.inference_request_digest,
        ),
        body_digest=prepared.body_digest,
        inference_request_digest=prepared.inference_request_digest,
    )
    return replace(
        provisional, inventory_entry_id=inventory_entry_id(provisional)
    )


def _inventory_digest(rows: tuple[ResponseBankInventoryRow, ...]) -> str:
    return response_bank_inventory_digest(rows)


def _semantic_plan(rows: tuple[ResponseBankInventoryRow, ...]) -> SemanticInventoryPlan:
    slot_keys = [row.semantic_slot_key for row in rows]
    case_refs = [
        {
            "case_id": "case-0",
            "case_record_digest": row.case_record_digest,
            "semantic_slot_keys": [row.semantic_slot_key],
        }
        for row in rows
    ]
    return SemanticInventoryPlan(
        schema_version="tokenshare.response_bank_semantic_inventory_plan.v1",
        inventory_digest=_inventory_digest(rows),
        rows=rows,
        condition_refs=(
            {
                "condition_id": "condition-0",
                "condition_digest": "sha256:" + "5" * 64,
                "experiment_id": "exp1_real_ai_feasibility",
                "worker_count": 1,
                "repeat_id": 0,
                "fault_type": "none",
                "ablation_mode": "FULL",
                "semantic_slot_keys": slot_keys,
                "case_refs": case_refs,
            },
        ),
        exp2_online_condition_refs=(),
        max_concurrent_roots=1,
        expected_slot_count=len(rows),
        terminal_provider_failure_count=0,
        terminal_success_count=0,
        terminal_unacquired_count=len(rows),
    )


def _limits(*, calls: int = 20) -> PaperBudgetLimits:
    return PaperBudgetLimits(
        calls=calls,
        tokens=1_000_000,
        cny=Decimal("100"),
        deepseek_cumulative_cny=Decimal("1000"),
    )


def _authorization(
    root: Path,
    inventory_digest: str,
    *,
    mode: str,
    expired: bool = False,
    authorized_plan_digest: str = "sha256:" + "6" * 64,
    profile_digest: str = "sha256:" + "7" * 64,
    budget_digest: str = "sha256:" + "8" * 64,
):
    receipt = {
        "schema_version": "tokenshare.paid_execution_receipt.v1",
        "receipt_digest": "",
        "scope": "epd027_full_bank_acquisition",
        "authorized_plan_digest": authorized_plan_digest,
        "profile_digest": profile_digest,
        "budget_digest": budget_digest,
        "inventory_digest": inventory_digest,
        "prompt_admission_profile_digest": (
            "sha256:e693ef1c9dbf50aff36aaae1b14f7029c5954c6708a6570182e69a7051685d49"
        ),
        "selected_experiments": ["epd027_full_bank_acquisition"],
        "output_root_path_digest": output_root_path_digest(root),
        "not_before": "2026-08-01T00:00:00Z",
        "expires_at": "2026-08-04T00:00:00Z",
        "user_approval_reference": "test-user-approval",
    }
    receipt["receipt_digest"] = compute_receipt_digest(receipt)
    return validate_paid_execution_receipt(
        receipt=receipt,
        requested_scope="epd027_full_bank_acquisition",
        authorized_plan_digest=str(receipt["authorized_plan_digest"]),
        profile_digest=str(receipt["profile_digest"]),
        budget_digest=str(receipt["budget_digest"]),
        inventory_digest=inventory_digest,
        prompt_admission_profile_digest=str(
            receipt["prompt_admission_profile_digest"]
        ),
        selected_experiments=("epd027_full_bank_acquisition",),
        output_root=root,
        output_mode=mode,
        action="reconcile_close" if expired else "dispatch",
        allow_provider_calls=not expired,
        now=datetime(
            2026, 8, 5 if expired else 2, tzinfo=timezone.utc
        ),
    )


def _request(row: ResponseBankInventoryRow, prepared: Any) -> AcquisitionRequest:
    return AcquisitionRequest(
        inventory_row=row,
        prepared_request=prepared,
        provider_family="deepseek",
        api_key_env="FAKE_DEEPSEEK_KEY",
        timeout_seconds=600,
        token_upper_bound=300000,
        cost_upper_bound=Decimal("1"),
        frozen_pricing=FrozenPricing(
            currency="CNY",
            input_per_million_tokens=Decimal("0.5"),
            output_per_million_tokens=Decimal("1.5"),
        ),
        requested_at="2026-08-02T00:00:00Z",
    )


def _secret(_name: str) -> str:
    return "fake-secret"


def _orchestrator(
    root: Path,
    rows: tuple[ResponseBankInventoryRow, ...],
    transport: ScriptedExactTransport,
    *,
    mode: str = "new_run",
    context: Any = None,
    ledger: PaperBudgetLedger | None = None,
    limits: PaperBudgetLimits | None = None,
    secret_resolver: Any = _secret,
    crash_hook: Any = None,
    durability_hook: Any = None,
) -> tuple[ResponseBankAcquisitionOrchestrator, PaperBudgetLedger]:
    digest = _inventory_digest(rows)
    budget = ledger or PaperBudgetLedger(
        root.parent / f"{root.name}.sqlite3", limits=limits or _limits()
    )
    budget.preregister_inventory(inventory_digest=digest, rows=rows)
    orchestrator = ResponseBankAcquisitionOrchestrator(
        output_root=root,
        bank_root_id="bank-root-task8",
        manifest_digest="sha256:" + "a" * 64,
        inventory_digest=digest,
        inventory_rows=rows,
        budget_ledger=budget,
        paid_authorization=context
        or _authorization(root, digest, mode=mode),
        invocation_mode=mode,
        transport=transport,
        secret_resolver=secret_resolver,
        now_epoch=100,
        crash_hook=crash_hook,
        durability_hook=durability_hook,
    )
    return orchestrator, budget


def _existing_attempt_evidence(
    tmp_path: Path,
    *,
    unit: str,
    outcomes: tuple[Any, ...],
) -> tuple[
    ResponseBankResolver,
    PaperBudgetLedger,
    ScriptedExactTransport,
    tuple[ResponseBankInventoryRow, ...],
]:
    prepared = tuple(
        _prepared(
            unit=unit,
            marker="same-logical-request",
            replacement_slot=attempt_index,
        )
        for attempt_index in range(len(outcomes))
    )
    prepared_by_slot = {value.replacement_slot: value for value in prepared}
    rows = tuple(
        sorted(
            (_row(value) for value in prepared),
            key=lambda row: row.inventory_entry_id,
        )
    )
    inventory_digest = _inventory_digest(rows)
    output_root = tmp_path / "acquisition"
    authorization = _authorization(
        output_root,
        inventory_digest,
        mode="new_run",
    )
    manifest = ResponseBankManifest.create(
        bank_root_id=f"bank-root-existing-{unit}",
        profile_digest=authorization.receipt.profile_digest,
        budget_digest=authorization.receipt.budget_digest,
        inventory_digest=inventory_digest,
        provider_config_digest=rows[0].provider_config_digest,
        entry_ids=tuple(row.entry_id for row in rows),
        object_role_schema=OBJECT_ROLES,
        terminal_entry_count=len(rows),
        created_by_paid_receipt_digest=authorization.receipt.receipt_digest,
    )
    ledger = PaperBudgetLedger(
        tmp_path / "budget.sqlite3",
        limits=_limits(calls=len(rows)),
    )
    ledger.preregister_inventory(
        inventory_digest=inventory_digest,
        rows=rows,
    )
    transport = ScriptedExactTransport(list(outcomes))
    orchestrator = ResponseBankAcquisitionOrchestrator(
        output_root=output_root,
        bank_root_id=manifest.bank_root_id,
        manifest_digest=manifest.manifest_digest,
        inventory_digest=inventory_digest,
        inventory_rows=rows,
        budget_ledger=ledger,
        paid_authorization=authorization,
        invocation_mode="new_run",
        transport=transport,
        secret_resolver=_secret,
        now_epoch=100,
    )
    requests = tuple(
        _request(row, prepared_by_slot[row.replacement_slot]) for row in rows
    )
    batch = orchestrator.acquire_all(requests)
    assert batch.status == "complete"
    entries = orchestrator.terminal_entries()
    objects: dict[str, bytes] = {}
    for entry in entries:
        for locator in entry.object_locators:
            objects[locator.object_digest] = orchestrator.read_role_bytes(
                entry,
                locator.object_role,
            )
    bank_root = tmp_path / "existing-exp1-attempt-bank"
    initialize_response_bank(
        bank_root,
        manifest=manifest,
        inventory_rows=rows,
        entries=entries,
        objects=objects,
    )
    return (
        ResponseBankResolver.open(bank_root),
        ledger,
        transport,
        tuple(sorted(rows, key=lambda row: row.replacement_slot)),
    )


def _crash_at(target: str):
    def crash(stage: str) -> None:
        if stage == target:
            raise RuntimeError(f"crash:{stage}")

    return crash


def _supervised_no_response_stop_evidence(
    inventory_entry_ids: tuple[str, ...],
) -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": (
            "tokenshare.supervised_provider_no_response_stop_evidence.v1"
        ),
        "supervisor_session_id": "paid-representative-test-session",
        "worker_pid": 4242,
        "target_inventory_entry_ids": list(inventory_entry_ids),
        "target_ledger_observations": [
            {
                "inventory_entry_id": inventory_entry_id,
                "state": "dispatch_intent",
                "revision": 1,
                "terminal_entry_observed": False,
                "reacquisition_count": 0,
            }
            for inventory_entry_id in inventory_entry_ids
        ],
        "process_dead": True,
        "provider_terminal_observed": False,
        "terminal_entry_observed": False,
        "transport_connection_observed": True,
        "observation_check_count": 2,
        "unchanged_observation_window_ms": 120_000,
        "observed_inflight_lower_bound_ms": 720_000,
        "stopped_at": "2026-08-16T18:03:41+08:00",
    }
    body["stop_evidence_digest"] = response_bank_module.canonical_digest(body)
    return body


def test_supervised_no_response_closure_exact_two_is_provider_zero_and_settles_upper_bound(
    tmp_path: Path,
) -> None:
    prepared = tuple(
        _prepared(unit=f"supervised-stop-{index}", marker=str(index))
        for index in range(2)
    )
    rows = tuple(_row(value) for value in prepared)
    transport = ScriptedExactTransport([])
    acquisition_root = tmp_path / "bank"
    crashed, ledger = _orchestrator(
        acquisition_root,
        rows,
        transport,
        crash_hook=_crash_at("dispatch_intent"),
    )
    requests = tuple(
        _request(row, request)
        for row, request in zip(rows, prepared, strict=True)
    )
    for request in requests:
        with pytest.raises(RuntimeError, match="crash:dispatch_intent"):
            crashed.acquire(request)
    assert transport.calls == []


    assert [record.state for record in ledger.list_reservations()] == [
        "dispatch_intent",
        "dispatch_intent",
    ]
    assert [record.revision for record in ledger.list_reservations()] == [1, 1]

    def secret_bomb(_name: str) -> str:
        raise AssertionError("supervised closure cannot resolve a provider secret")

    resumed, _ = _orchestrator(
        acquisition_root,
        rows,
        transport,
        mode="resume",
        ledger=ledger,
        secret_resolver=secret_bomb,
    )
    closure_root = tmp_path / "supervised-closure"
    stop_evidence = _supervised_no_response_stop_evidence(
        tuple(row.inventory_entry_id for row in rows)
    )
    authority = (
        response_bank_module.persist_supervised_no_response_closure_authority(
            closure_root=closure_root,
            orchestrator=resumed,
            requests=requests,
            target_inventory_entry_ids=tuple(
                row.inventory_entry_id for row in rows
            ),
            stop_evidence=stop_evidence,
        )
    )
    authority_path = (
        closure_root
        / "supervised_provider_no_response_closure_authority.v1.json"
    )
    authority_ref_path = (
        closure_root
        / "supervised_provider_no_response_closure_authority_ref.v1.json"
    )
    assert authority.schema_version == (
        "tokenshare.supervised_provider_no_response_closure_authority.v1"
    )
    assert authority.provider_calls_made == 0
    assert authority.target_inventory_entry_ids == tuple(
        row.inventory_entry_id for row in rows
    )
    assert authority_path.is_file()
    assert authority_ref_path.is_file()
    assert not (
        closure_root / "supervised_provider_no_response_closure_receipt.v1.json"
    ).exists()

    receipt = response_bank_module.apply_supervised_no_response_closure(
        closure_root=closure_root,
        orchestrator=resumed,
        requests=requests,
    )

    assert receipt.schema_version == (
        "tokenshare.supervised_provider_no_response_closure_receipt.v1"
    )
    assert receipt.authority_digest == authority.authority_digest
    assert receipt.provider_calls_made == 0
    assert receipt.closed_inventory_entry_ids == tuple(
        row.inventory_entry_id for row in rows
    )
    assert (
        closure_root / "supervised_provider_no_response_closure_receipt.v1.json"
    ).is_file()
    assert (
        closure_root
        / "supervised_provider_no_response_closure_receipt_ref.v1.json"
    ).is_file()
    assert transport.calls == []

    terminal_entries = resumed.terminal_entries()
    assert len(terminal_entries) == 2
    for entry, request in zip(terminal_entries, requests, strict=True):
        assert entry.terminal_kind == "provider_failure"
        failure = resumed.read_role_json(entry, "provider_failure")
        provenance = resumed.read_role_json(entry, "provenance")
        usage = resumed.read_role_json(entry, "usage")
        latency = resumed.read_role_json(entry, "latency")
        model = resumed.read_role_json(entry, "model_record")
        attempt = resumed.read_role_json(entry, "acquisition_attempt")
        assert failure == {
            "schema_version": "tokenshare.response_bank_provider_failure.v2",
            "evidence_kind": "supervised_stopped_attempt",
            "failure_kind": "no_response",
            "http_status": None,
            "message": "provider returned no terminal response before supervised stop",
            "raw_response_json": None,
            "transport_call_count": 1,
            "closure_provider_call_count": 0,
            "closure_authority_digest": authority.authority_digest,
            "stop_evidence_digest": stop_evidence["stop_evidence_digest"],
        }
        assert provenance["transport_call_count"] == 1
        assert provenance["closure_provider_call_count"] == 0
        assert provenance["closure_authority_digest"] == authority.authority_digest
        assert provenance["schema_version"] == "tokenshare.response_bank_provenance.v2"
        assert provenance["evidence_kind"] == "supervised_stopped_attempt"
        assert provenance["stop_evidence_digest"] == stop_evidence["stop_evidence_digest"]
        assert usage == {
            "schema_version": "tokenshare.response_bank_usage.v1",
            "usage_status": "usage_missing",
            "usage": None,
        }
        assert latency == {
            "schema_version": "tokenshare.response_bank_latency.v2",
            "evidence_kind": "supervised_stopped_attempt",
            "latency_ms": None,
            "latency_missing": True,
            "timing_source": "supervised_no_terminal_response",
            "observed_inflight_lower_bound_ms": 720_000,
            "unchanged_observation_window_ms": 120_000,
            "stop_evidence_digest": stop_evidence["stop_evidence_digest"],
        }
        assert model["configured_model"] == "deepseek-v4-pro"
        assert model["requested_model"] == "deepseek-v4-pro"
        assert model["resolved_model"] is None
        assert model["response_model_status"] == "unavailable_provider_failure"
        assert attempt["terminal_kind"] == "provider_failure"
        assert attempt["failure_kind"] == "no_response"
        record = ledger.get_reservation(
            _inventory_digest(rows), entry.inventory_entry_id
        )
        assert record.state == "settled"
        assert record.usage_missing is True
        assert record.charged_tokens == request.token_upper_bound
        assert record.cost_estimate == request.cost_upper_bound

    before = {
        path.relative_to(closure_root).as_posix(): path.read_bytes()
        for path in closure_root.rglob("*")
        if path.is_file()
    }
    repeated = response_bank_module.apply_supervised_no_response_closure(
        closure_root=closure_root,
        orchestrator=resumed,
        requests=requests,
    )
    after = {
        path.relative_to(closure_root).as_posix(): path.read_bytes()
        for path in closure_root.rglob("*")
        if path.is_file()
    }
    assert repeated == receipt
    assert after == before
    assert transport.calls == []


def test_supervised_no_response_closure_fails_closed_on_revision_drift_before_write(
    tmp_path: Path,
) -> None:
    prepared = _prepared(unit="supervised-stop-revision", marker="revision")
    row = _row(prepared)
    transport = ScriptedExactTransport([])
    acquisition_root = tmp_path / "bank"
    crashed, ledger = _orchestrator(
        acquisition_root,
        (row,),
        transport,
        crash_hook=_crash_at("dispatch_intent"),
    )
    request = _request(row, prepared)
    with pytest.raises(RuntimeError, match="crash:dispatch_intent"):
        crashed.acquire(request)

    stop_evidence = _supervised_no_response_stop_evidence(
        (row.inventory_entry_id,)
    )
    stop_evidence["target_ledger_observations"][0]["revision"] = 2
    stop_evidence["stop_evidence_digest"] = response_bank_module.canonical_digest(
        {
            key: value
            for key, value in stop_evidence.items()
            if key != "stop_evidence_digest"
        }
    )

    def secret_bomb(_name: str) -> str:
        raise AssertionError("closure validation cannot resolve a provider secret")

    resumed, _ = _orchestrator(
        acquisition_root,
        (row,),
        transport,
        mode="resume",
        ledger=ledger,
        secret_resolver=secret_bomb,
    )
    closure_root = tmp_path / "supervised-closure"
    with pytest.raises(
        response_bank_module.AcquisitionIdentityError,
        match="revision",
    ):
        response_bank_module.persist_supervised_no_response_closure_authority(
            closure_root=closure_root,
            orchestrator=resumed,
            requests=(request,),
            target_inventory_entry_ids=(row.inventory_entry_id,),
            stop_evidence=stop_evidence,
        )

    assert not closure_root.exists()
    assert ledger.get_reservation(
        _inventory_digest((row,)), row.inventory_entry_id
    ).state == "dispatch_intent"
    assert transport.calls == []


@pytest.mark.parametrize(
    ("field_name", "drift"),
    (
        ("observed_inflight_lower_bound_ms", 719_999),
        ("unchanged_observation_window_ms", 119_999),
        ("transport_connection_observed", False),
    ),
)
def test_supervised_no_response_stop_gate_rejects_weak_evidence_before_write(
    tmp_path: Path,
    field_name: str,
    drift: object,
) -> None:
    prepared = _prepared(unit=f"weak-stop-{field_name}", marker=field_name)
    row = _row(prepared)
    transport = ScriptedExactTransport([])
    acquisition_root = tmp_path / "bank"
    crashed, ledger = _orchestrator(
        acquisition_root,
        (row,),
        transport,
        crash_hook=_crash_at("dispatch_intent"),
    )
    request = _request(row, prepared)
    with pytest.raises(RuntimeError, match="crash:dispatch_intent"):
        crashed.acquire(request)
    resumed, _ = _orchestrator(
        acquisition_root,
        (row,),
        transport,
        mode="resume",
        ledger=ledger,
        secret_resolver=lambda _name: (_ for _ in ()).throw(
            AssertionError("weak stop evidence cannot resolve a provider secret")
        ),
    )
    stop_evidence = _supervised_no_response_stop_evidence(
        (row.inventory_entry_id,)
    )
    stop_evidence[field_name] = drift
    stop_evidence["stop_evidence_digest"] = response_bank_module.canonical_digest(
        {
            key: value
            for key, value in stop_evidence.items()
            if key != "stop_evidence_digest"
        }
    )
    closure_root = tmp_path / "supervised-closure"

    with pytest.raises(AcquisitionIdentityError, match="terminal-safe"):
        response_bank_module.persist_supervised_no_response_closure_authority(
            closure_root=closure_root,
            orchestrator=resumed,
            requests=(request,),
            target_inventory_entry_ids=(row.inventory_entry_id,),
            stop_evidence=stop_evidence,
        )

    assert not closure_root.exists()
    assert resumed._load_entry(row.inventory_entry_id) is None
    assert ledger.get_reservation(
        _inventory_digest((row,)), row.inventory_entry_id
    ).state == "dispatch_intent"
    assert transport.calls == []


def test_supervised_no_response_closure_crash_after_terminal_is_idempotently_resumable(
    tmp_path: Path,
) -> None:
    prepared = _prepared(unit="supervised-stop-crash", marker="crash")
    row = _row(prepared)
    transport = ScriptedExactTransport([])
    acquisition_root = tmp_path / "bank"
    crashed, ledger = _orchestrator(
        acquisition_root,
        (row,),
        transport,
        crash_hook=_crash_at("dispatch_intent"),
    )
    request = _request(row, prepared)
    with pytest.raises(RuntimeError, match="crash:dispatch_intent"):
        crashed.acquire(request)

    def secret_bomb(_name: str) -> str:
        raise AssertionError("closure recovery cannot resolve a provider secret")

    resumed, _ = _orchestrator(
        acquisition_root,
        (row,),
        transport,
        mode="resume",
        ledger=ledger,
        secret_resolver=secret_bomb,
    )
    closure_root = tmp_path / "supervised-closure"
    response_bank_module.persist_supervised_no_response_closure_authority(
        closure_root=closure_root,
        orchestrator=resumed,
        requests=(request,),
        target_inventory_entry_ids=(row.inventory_entry_id,),
        stop_evidence=_supervised_no_response_stop_evidence(
            (row.inventory_entry_id,)
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="crash:supervised_no_response_terminal_entry_committed",
    ):
        response_bank_module.apply_supervised_no_response_closure(
            closure_root=closure_root,
            orchestrator=resumed,
            requests=(request,),
            crash_hook=_crash_at(
                "supervised_no_response_terminal_entry_committed"
            ),
        )
    assert not (
        closure_root / "supervised_provider_no_response_closure_receipt_ref.v1.json"
    ).exists()

    receipt = response_bank_module.apply_supervised_no_response_closure(
        closure_root=closure_root,
        orchestrator=resumed,
        requests=(request,),
    )
    assert receipt.provider_calls_made == 0
    assert receipt.closed_inventory_entry_ids == (row.inventory_entry_id,)
    assert ledger.get_reservation(
        _inventory_digest((row,)), row.inventory_entry_id
    ).state == "settled"
    assert transport.calls == []


def test_supervised_no_response_closure_receipt_ref_last_crash_is_resumable(
    tmp_path: Path,
) -> None:
    prepared = _prepared(unit="supervised-stop-receipt", marker="receipt")
    row = _row(prepared)
    transport = ScriptedExactTransport([])
    acquisition_root = tmp_path / "bank"
    crashed, ledger = _orchestrator(
        acquisition_root,
        (row,),
        transport,
        crash_hook=_crash_at("dispatch_intent"),
    )
    request = _request(row, prepared)
    with pytest.raises(RuntimeError, match="crash:dispatch_intent"):
        crashed.acquire(request)

    resumed, _ = _orchestrator(
        acquisition_root,
        (row,),
        transport,
        mode="resume",
        ledger=ledger,
        secret_resolver=lambda _name: (_ for _ in ()).throw(
            AssertionError("receipt recovery cannot resolve provider secret")
        ),
    )
    closure_root = tmp_path / "supervised-closure"
    response_bank_module.persist_supervised_no_response_closure_authority(
        closure_root=closure_root,
        orchestrator=resumed,
        requests=(request,),
        target_inventory_entry_ids=(row.inventory_entry_id,),
        stop_evidence=_supervised_no_response_stop_evidence(
            (row.inventory_entry_id,)
        ),
    )
    with pytest.raises(
        RuntimeError,
        match="crash:supervised_no_response_closure_receipt_committed",
    ):
        response_bank_module.apply_supervised_no_response_closure(
            closure_root=closure_root,
            orchestrator=resumed,
            requests=(request,),
            crash_hook=_crash_at(
                "supervised_no_response_closure_receipt_committed"
            ),
        )
    assert (
        closure_root / "supervised_provider_no_response_closure_receipt.v1.json"
    ).is_file()
    assert not (
        closure_root / "supervised_provider_no_response_closure_receipt_ref.v1.json"
    ).exists()

    receipt = response_bank_module.apply_supervised_no_response_closure(
        closure_root=closure_root,
        orchestrator=resumed,
        requests=(request,),
    )
    assert receipt.provider_calls_made == 0
    assert receipt.closed_inventory_entry_ids == (row.inventory_entry_id,)
    assert (
        closure_root / "supervised_provider_no_response_closure_receipt_ref.v1.json"
    ).is_file()
    assert transport.calls == []


def test_supervised_no_response_closure_revalidates_budget_binding_and_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepared(unit="supervised-stop-binding", marker="binding")
    row = _row(prepared)
    request = _request(row, prepared)
    transport = ScriptedExactTransport([])
    crashed, ledger = _orchestrator(
        tmp_path / "bank",
        (row,),
        transport,
        crash_hook=_crash_at("dispatch_intent"),
    )
    with pytest.raises(RuntimeError, match="crash:dispatch_intent"):
        crashed.acquire(request)
    resumed, _ = _orchestrator(
        tmp_path / "bank",
        (row,),
        transport,
        mode="resume",
        ledger=ledger,
        secret_resolver=lambda _name: (_ for _ in ()).throw(
            AssertionError("closure cannot resolve a provider secret")
        ),
    )
    closure_root = tmp_path / "supervised-closure"
    response_bank_module.persist_supervised_no_response_closure_authority(
        closure_root=closure_root,
        orchestrator=resumed,
        requests=(request,),
        target_inventory_entry_ids=(row.inventory_entry_id,),
        stop_evidence=_supervised_no_response_stop_evidence(
            (row.inventory_entry_id,)
        ),
    )

    original_authorization = resumed.paid_authorization
    assert original_authorization is not None
    resumed.paid_authorization = replace(
        original_authorization,
        receipt=replace(
            original_authorization.receipt,
            budget_digest="sha256:" + "f" * 64,
        ),
    )
    with pytest.raises(AcquisitionIdentityError, match="context"):
        response_bank_module.apply_supervised_no_response_closure(
            closure_root=closure_root,
            orchestrator=resumed,
            requests=(request,),
        )
    resumed.paid_authorization = original_authorization
    with pytest.raises(AcquisitionIdentityError, match="binding identity drift"):
        response_bank_module.apply_supervised_no_response_closure(
            closure_root=closure_root,
            orchestrator=resumed,
            requests=(replace(request, token_upper_bound=request.token_upper_bound + 1),),
        )

    receipt = response_bank_module.apply_supervised_no_response_closure(
        closure_root=closure_root,
        orchestrator=resumed,
        requests=(request,),
    )
    real_get_reservation = ledger.get_reservation

    def drifted_record(inventory_digest: str, inventory_entry_id: str):
        return replace(
            real_get_reservation(inventory_digest, inventory_entry_id),
            usage_missing=False,
        )

    monkeypatch.setattr(ledger, "get_reservation", drifted_record)
    with pytest.raises(AcquisitionIdentityError, match="settled ledger accounting"):
        response_bank_module.apply_supervised_no_response_closure(
            closure_root=closure_root,
            orchestrator=resumed,
            requests=(request,),
        )
    monkeypatch.setattr(ledger, "get_reservation", real_get_reservation)
    real_read_role = resumed.read_role_json

    def drifted_role(entry, role: str):
        body = real_read_role(entry, role)
        return {**body, "unexpected": True} if role == "provider_failure" else body

    monkeypatch.setattr(resumed, "read_role_json", drifted_role)
    with pytest.raises(AcquisitionIdentityError, match="provider_failure identity"):
        response_bank_module.apply_supervised_no_response_closure(
            closure_root=closure_root,
            orchestrator=resumed,
            requests=(request,),
        )
    assert receipt.provider_calls_made == 0
    assert transport.calls == []


def test_supervised_no_response_closure_preflights_all_targets_before_first_write(
    tmp_path: Path,
) -> None:
    prepared = tuple(
        _prepared(unit=f"supervised-preflight-{index}", marker=str(index))
        for index in range(2)
    )
    rows = tuple(_row(value) for value in prepared)
    requests = tuple(
        _request(row, value)
        for row, value in zip(rows, prepared, strict=True)
    )
    transport = ScriptedExactTransport([])
    crashed, ledger = _orchestrator(
        tmp_path / "bank",
        rows,
        transport,
        crash_hook=_crash_at("dispatch_intent"),
    )
    for request in requests:
        with pytest.raises(RuntimeError, match="crash:dispatch_intent"):
            crashed.acquire(request)
    resumed, _ = _orchestrator(
        tmp_path / "bank",
        rows,
        transport,
        mode="resume",
        ledger=ledger,
        secret_resolver=lambda _name: (_ for _ in ()).throw(
            AssertionError("closure cannot resolve a provider secret")
        ),
    )
    closure_root = tmp_path / "supervised-closure"
    target_ids = tuple(row.inventory_entry_id for row in rows)
    response_bank_module.persist_supervised_no_response_closure_authority(
        closure_root=closure_root,
        orchestrator=resumed,
        requests=requests,
        target_inventory_entry_ids=target_ids,
        stop_evidence=_supervised_no_response_stop_evidence(target_ids),
    )
    before = ledger.list_reservations()
    drifted = (
        requests[0],
        replace(
            requests[1],
            token_upper_bound=requests[1].token_upper_bound + 1,
        ),
    )
    with pytest.raises(AcquisitionIdentityError, match="binding identity drift"):
        response_bank_module.apply_supervised_no_response_closure(
            closure_root=closure_root,
            orchestrator=resumed,
            requests=drifted,
        )
    assert ledger.list_reservations() == before
    assert all(resumed._load_entry(entry_id) is None for entry_id in target_ids)
    assert not (
        closure_root / "supervised_provider_no_response_closure_receipt.v1.json"
    ).exists()
    assert transport.calls == []


def test_live_hard_deadline_evidence_reaches_response_bank_roles_without_late_mislabel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = (
        _prepared(unit="live-no-response", marker="deadline", replacement_slot=0),
        _prepared(unit="live-success", marker="on-time", replacement_slot=0),
    )
    rows = tuple(_row(value) for value in prepared)
    transport = ScriptedExactTransport([])
    orchestrator, _ledger = _orchestrator(tmp_path / "bank", rows, transport)
    base_proof = {
        "schema_version": "tokenshare.ai_api_hard_deadline_quiescence.v1",
        "deadline_enforced": True,
        "hard_total_seconds": 600.0,
        "observed_wall_clock_ms": 600_123,
        "child_pid": 4242,
        "child_exit_code": -15,
        "terminate_attempted": True,
        "kill_attempted": False,
        "child_reaped": True,
        "network_start_acknowledged": True,
        "result_observed_before_deadline": False,
        "child_completed_before_parent_deadline": False,
        "result_commit_count": 0,
        "accepted_result_commit_count": 0,
        "late_result_rejected": False,
        "post_reap_late_result_absent": True,
        "parent_only_evidence_consumer": True,
        "ephemeral_files_secret_free": True,
        "request_file_sha256": "sha256:" + "a" * 64,
    }
    on_time_proof = {
        **base_proof,
        "observed_wall_clock_ms": 81,
        "child_exit_code": 0,
        "terminate_attempted": False,
        "result_observed_before_deadline": True,
        "child_completed_before_parent_deadline": True,
        "result_commit_count": 1,
        "accepted_result_commit_count": 1,
        "request_file_sha256": "sha256:" + "b" * 64,
    }
    evidence = [
        PreparedDispatchEvidence(
            terminal_kind="provider_failure",
            failure_kind="no_response",
            raw_response_json=None,
            content_text=None,
            reasoning_content=None,
            provider_response_id=None,
            finish_reason=None,
            usage=None,
            latency_ms=None,
            http_status=None,
            resolved_model=None,
            response_model_status="unavailable_provider_failure",
            error_message="provider returned no terminal response",
            transport_call_count=1,
            latency_timing_source="unknown_no_response",
            hard_deadline_evidence=base_proof,
        ),
        PreparedDispatchEvidence(
            terminal_kind="success",
            failure_kind=None,
            raw_response_json={"id": "on-time"},
            content_text='{"answer":"on-time"}',
            reasoning_content="reasoning",
            provider_response_id="on-time",
            finish_reason="stop",
            usage={
                "prompt_tokens": 2,
                "completion_tokens": 3,
                "total_tokens": 5,
            },
            latency_ms=81,
            http_status=200,
            resolved_model="deepseek-v4-pro",
            response_model_status="present",
            error_message=None,
            transport_call_count=1,
            latency_timing_source="provider_child_observed",
            hard_deadline_evidence=on_time_proof,
        ),
    ]
    def dispatch_after_ready(**kwargs: Any) -> PreparedDispatchEvidence:
        kwargs["on_transport_start"]()
        return evidence.pop(0)

    monkeypatch.setattr(
        response_bank_module,
        "dispatch_prepared_request_once",
        dispatch_after_ready,
    )

    entries = tuple(
        orchestrator.acquire(_request(row, value)).entry
        for row, value in zip(rows, prepared, strict=True)
    )
    assert all(entry is not None for entry in entries)
    failure_entry, success_entry = entries
    failure = orchestrator.read_role_json(failure_entry, "provider_failure")
    failure_provenance = orchestrator.read_role_json(
        failure_entry, "provenance"
    )
    failure_latency = orchestrator.read_role_json(failure_entry, "latency")
    deadline_digest = response_bank_module.canonical_digest(base_proof)
    assert failure["hard_deadline_evidence"] == base_proof
    assert failure["schema_version"] == "tokenshare.response_bank_provider_failure.v2"
    assert failure["evidence_kind"] == "hard_deadline_child"
    assert failure_provenance["transport_call_count"] == 1
    assert failure_provenance["hard_deadline_evidence_digest"] == deadline_digest
    assert failure_provenance["network_start_acknowledged"] is True
    assert failure_provenance["result_observed_before_deadline"] is False
    assert failure_provenance["post_reap_late_result_absent"] is True
    assert failure_provenance["child_reaped"] is True
    assert failure_provenance["parent_only_evidence_consumer"] is True
    assert failure_provenance["ephemeral_files_secret_free"] is True
    assert failure_latency == {
        "schema_version": "tokenshare.response_bank_latency.v2",
        "evidence_kind": "hard_deadline_child",
        "latency_ms": None,
        "latency_missing": True,
        "timing_source": "unknown_no_response",
        "hard_deadline_evidence_digest": deadline_digest,
        "observed_wall_clock_ms": 600_123,
    }

    success_provenance = orchestrator.read_role_json(success_entry, "provenance")
    success_latency = orchestrator.read_role_json(success_entry, "latency")
    assert success_provenance["result_observed_before_deadline"] is True
    assert success_provenance["late_result_rejected"] is False
    assert success_latency["latency_ms"] == 81
    assert success_latency["latency_missing"] is False
    assert success_latency["timing_source"] == "provider_child_observed"
    assert transport.calls == []


def test_pre_dispatch_hard_deadline_failure_releases_without_terminal_or_charge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepared(
        unit="pre-dispatch-failure",
        marker="child-bootstrap",
        replacement_slot=0,
    )
    row = _row(prepared)
    transport = ScriptedExactTransport([])
    orchestrator, ledger = _orchestrator(tmp_path / "bank", (row,), transport)
    dispatches: list[str] = []
    pre_dispatch_proof = {
        "schema_version": "tokenshare.ai_api_hard_deadline_quiescence.v1",
        "deadline_enforced": True,
        "hard_total_seconds": 600,
        "observed_wall_clock_ms": 17,
        "child_pid": 4242,
        "child_exit_code": 3,
        "terminate_attempted": False,
        "kill_attempted": False,
        "child_reaped": True,
        "network_start_acknowledged": False,
        "result_observed_before_deadline": False,
        "child_completed_before_parent_deadline": True,
        "result_commit_count": 0,
        "accepted_result_commit_count": 0,
        "late_result_rejected": False,
        "post_reap_late_result_absent": True,
        "parent_only_evidence_consumer": True,
        "ephemeral_files_secret_free": True,
        "request_file_sha256": "sha256:" + "c" * 64,
    }

    def pre_dispatch_failure(**_kwargs: Any) -> PreparedDispatchEvidence:
        dispatches.append(row.inventory_entry_id)
        return PreparedDispatchEvidence(
            terminal_kind="provider_failure",
            failure_kind="executor_error",
            raw_response_json=None,
            content_text=None,
            reasoning_content=None,
            provider_response_id=None,
            finish_reason=None,
            usage=None,
            latency_ms=None,
            http_status=None,
            resolved_model=None,
            response_model_status="unavailable_provider_failure",
            error_message="child failed before network boundary",
            transport_call_count=0,
            latency_timing_source="unavailable_pre_dispatch",
            hard_deadline_evidence=pre_dispatch_proof,
        )

    monkeypatch.setattr(
        response_bank_module,
        "dispatch_prepared_request_once",
        pre_dispatch_failure,
    )

    batch = orchestrator.acquire_all((_request(row, prepared),), max_in_flight=1)
    reservations = ledger.list_reservations()

    # 未越过 network-ready/GO 边界时没有 provider attempt：本轮只记录
    # 可恢复的 pre-dispatch 结果，不得生成回答库 terminal 或 upper charge。
    assert dispatches == [row.inventory_entry_id]
    assert transport.calls == []
    assert batch.status == "incomplete"
    assert len(batch.results) == 1
    assert batch.results[0].status == "pre_dispatch_failure"
    assert batch.results[0].entry is None
    assert batch.results[0].transport_invoked is False
    assert batch.results[0].failure_kind == "executor_error"
    assert batch.missing_inventory_entry_ids == (row.inventory_entry_id,)
    assert batch.ambiguous_inventory_entry_ids == ()
    assert reservations == ()
    assert orchestrator._load_entry(row.inventory_entry_id) is None
    audit_paths = tuple(
        (orchestrator.output_root / "pre_dispatch_release_audit").glob(
            "*.intent.v1.json"
        )
    )
    assert len(audit_paths) == 1
    audit = json.loads(audit_paths[0].read_text(encoding="utf-8"))
    assert audit["schema_version"] == "tokenshare.pre_dispatch_release_intent.v1"
    assert audit["inventory_entry_id"] == row.inventory_entry_id
    assert audit["provider_calls_made"] == 0
    assert audit["terminal_published"] is False
    assert audit["settled"] is False
    assert audit["ledger_action"] == "safe_release_reserved_intent"
    assert audit["intent_digest"] == response_bank_module.canonical_digest(
        {
            key: value
            for key, value in audit.items()
            if key != "intent_digest"
        }
    )
    assert tuple(
        (orchestrator.output_root / "pre_dispatch_release_audit").glob(
            "*.commit_ref.v1.json"
        )
    )


def test_real_hard_deadline_preboot_float_proof_releases_without_provider_accounting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepared(
        unit="real-pre-dispatch-failure",
        marker="real-child-bootstrap",
        replacement_slot=0,
    )
    row = _row(prepared)
    transport = HardDeadlineNetworkBomb()
    orchestrator, ledger = _orchestrator(
        tmp_path / "bank",
        (row,),
        transport,
    )
    real_child_environment = ai_api_hard_deadline._minimal_child_environment

    def environment_without_child_secret(api_key: str) -> dict[str, str]:
        environment = real_child_environment(api_key)
        environment.pop(ai_api_hard_deadline.HARD_DEADLINE_API_KEY_ENV)
        return environment

    monkeypatch.setattr(
        ai_api_hard_deadline,
        "_minimal_child_environment",
        environment_without_child_secret,
    )
    observed_evidence: list[PreparedDispatchEvidence] = []
    real_validator = response_bank_module._validated_pre_dispatch_failure_evidence

    def capture_real_evidence(
        evidence: PreparedDispatchEvidence,
        *,
        expected_hard_total_seconds: int,
    ) -> dict[str, object]:
        observed_evidence.append(evidence)
        return real_validator(
            evidence,
            expected_hard_total_seconds=expected_hard_total_seconds,
        )

    monkeypatch.setattr(
        response_bank_module,
        "_validated_pre_dispatch_failure_evidence",
        capture_real_evidence,
    )

    batch = orchestrator.acquire_all((_request(row, prepared),), max_in_flight=1)

    assert transport.calls == []
    assert batch.status == "incomplete"
    assert batch.results[0].status == "pre_dispatch_failure"
    assert batch.results[0].transport_invoked is False
    assert batch.results[0].entry is None
    assert ledger.list_reservations() == ()
    audit_path = next(
        (orchestrator.output_root / "pre_dispatch_release_audit").glob(
            "*.intent.v1.json"
        )
    )
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["provider_calls_made"] == 0
    assert audit["hard_deadline_evidence_digest"].startswith("sha256:")
    assert len(observed_evidence) >= 1
    real_proof = dict(observed_evidence[0].hard_deadline_evidence or {})
    assert type(real_proof["hard_total_seconds"]) is float
    assert real_proof["hard_total_seconds"] == 600.0
    for drift in (False, 599.0, float("inf")):
        with pytest.raises(AcquisitionIdentityError, match="not quiescent"):
            real_validator(
                replace(
                    observed_evidence[0],
                    hard_deadline_evidence={
                        **real_proof,
                        "hard_total_seconds": drift,
                    },
                ),
                expected_hard_total_seconds=600,
            )


@pytest.mark.parametrize(
    "crash_stage",
    (
        "pre_dispatch_release_intent_committed",
        "pre_dispatch_reservation_released",
    ),
)
def test_pre_dispatch_release_intent_recovers_ref_last_without_redispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_stage: str,
) -> None:
    prepared = _prepared(unit=f"pre-dispatch-crash-{crash_stage}", marker=crash_stage)
    row = _row(prepared)
    request = _request(row, prepared)
    transport = ScriptedExactTransport([])
    crashed, ledger = _orchestrator(
        tmp_path / "bank",
        (row,),
        transport,
        crash_hook=_crash_at(crash_stage),
    )
    proof = {
        "schema_version": "tokenshare.ai_api_hard_deadline_quiescence.v1",
        "deadline_enforced": True,
        "hard_total_seconds": 600.0,
        "observed_wall_clock_ms": 5,
        "child_pid": 4242,
        "child_exit_code": 3,
        "terminate_attempted": False,
        "kill_attempted": False,
        "child_reaped": True,
        "network_start_acknowledged": False,
        "result_observed_before_deadline": False,
        "child_completed_before_parent_deadline": True,
        "result_commit_count": 0,
        "accepted_result_commit_count": 0,
        "late_result_rejected": False,
        "post_reap_late_result_absent": True,
        "parent_only_evidence_consumer": True,
        "ephemeral_files_secret_free": True,
        "request_file_sha256": "sha256:" + "d" * 64,
    }

    def call_zero(**_kwargs: Any) -> PreparedDispatchEvidence:
        return PreparedDispatchEvidence(
            terminal_kind="provider_failure",
            failure_kind="executor_error",
            raw_response_json=None,
            content_text=None,
            reasoning_content=None,
            provider_response_id=None,
            finish_reason=None,
            usage=None,
            latency_ms=None,
            http_status=None,
            resolved_model=None,
            response_model_status="unavailable_provider_failure",
            error_message="child bootstrap failed",
            transport_call_count=0,
            latency_timing_source="unavailable_pre_dispatch",
            hard_deadline_evidence=proof,
        )

    monkeypatch.setattr(response_bank_module, "dispatch_prepared_request_once", call_zero)
    with pytest.raises(RuntimeError, match=f"crash:{crash_stage}"):
        crashed.acquire(request)
    audit_root = crashed.output_root / "pre_dispatch_release_audit"
    assert len(tuple(audit_root.glob("*.intent.v1.json"))) == 1
    assert tuple(audit_root.glob("*.commit_ref.v1.json")) == ()

    resumed, _ = _orchestrator(
        tmp_path / "bank",
        (row,),
        transport,
        mode="resume",
        ledger=ledger,
        secret_resolver=lambda _name: (_ for _ in ()).throw(
            AssertionError("reconcile cannot resolve provider secret")
        ),
    )
    batch = resumed.acquire_all((request,), max_in_flight=1)
    assert batch.status == "incomplete"
    assert batch.results == ()
    assert ledger.list_reservations() == ()
    assert len(tuple(audit_root.glob("*.commit_ref.v1.json"))) == 1
    assert transport.calls == []


def test_pre_dispatch_release_ref_only_fails_closed_before_dispatch(
    tmp_path: Path,
) -> None:
    prepared = _prepared(unit="pre-dispatch-ref-only", marker="ref-only")
    row = _row(prepared)
    transport = ScriptedExactTransport([])
    orchestrator, _ledger = _orchestrator(tmp_path / "bank", (row,), transport)
    audit_root = orchestrator.output_root / "pre_dispatch_release_audit"
    audit_root.mkdir()
    (audit_root / "orphan.commit_ref.v1.json").write_text(
        "{}\n", encoding="utf-8"
    )
    with pytest.raises(AcquisitionIdentityError, match="ref-only"):
        orchestrator.acquire_all((_request(row, prepared),), max_in_flight=1)
    assert transport.calls == []


def test_acquisition_orders_prepare_admit_persist_reserve_dispatch_terminal_publish_settle(
    tmp_path: Path,
) -> None:
    prepared = _prepared()
    row = _row(prepared)
    transport = ScriptedExactTransport([_success_response()])
    root = tmp_path / "bank"
    orchestrator, ledger = _orchestrator(
        root, (row,), transport
    )

    result = orchestrator.acquire(_request(row, prepared))

    assert result.lifecycle_order == (
        "paid_context_validated",
        "invocation_mode_validated",
        "output_marker_validated",
        "prepared",
        "consistency_validated",
        "admitted",
        "prepared_artifact_committed",
        "reserved",
        "secret_resolved",
        "dispatch_intent",
        "transport_sent",
        "terminal_objects_committed",
        "terminal_entry_committed",
        "terminal_published",
        "settled",
    )
    assert ledger.get_reservation(
        row.inventory_entry_id and _inventory_digest((row,)), row.inventory_entry_id
    ).state == "settled"
    assert len(transport.calls) == 1
    assert transport.calls[0]["body_bytes"] == prepared.body_bytes
    assert (
        transport.calls[0]["normalized_absolute_endpoint"]
        == prepared.normalized_absolute_endpoint
    )
    assert [path.name for path in root.glob("*paid_output_binding*")] == [
        "paid_output_binding.v1.json"
    ]
    for path in root.rglob("*"):
        if path.is_file():
            assert b"fake-secret" not in path.read_bytes()


def test_success_and_each_provider_failure_taxonomy_are_terminal_entries(
    tmp_path: Path,
) -> None:
    kinds = ("success", *PROVIDER_FAILURE_TAXONOMY)
    for index, kind in enumerate(kinds):
        prepared = _prepared(unit=f"unit-{index}", marker=kind)
        row = _row(prepared)
        outcome = _success_response(kind) if kind == "success" else _failure_outcome(kind)
        orchestrator, _ = _orchestrator(
            tmp_path / f"bank-{index}",
            (row,),
            ScriptedExactTransport([outcome]),
        )
        result = orchestrator.acquire(_request(row, prepared))
        roles = {locator.object_role for locator in result.entry.object_locators}
        assert COMMON_ROLES <= roles
        assert result.entry.terminal_kind == (
            "success" if kind == "success" else "provider_failure"
        )
        assert ("raw_output" in roles) == (kind == "success")
        assert ("provider_failure" in roles) == (kind != "success")
        usage = orchestrator.read_role_json(result.entry, "usage")
        latency = orchestrator.read_role_json(result.entry, "latency")
        assert usage["usage_status"] in {"reported", "usage_missing"}
        assert latency["latency_ms"] >= 0


def test_complete_batch_initializes_child_once_and_resume_only_reopens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepared()
    row = _row(prepared)
    request = _request(row, prepared)
    bundle = create_acquisition_plan_bundle(
        tmp_path / "plan-bundle",
        authorized_plan_digest="sha256:" + "6" * 64,
        profile_digest="sha256:" + "7" * 64,
        semantic_inventory_plan=_semantic_plan((row,)),
        acquisition_requests=(request,),
    )
    output_root = tmp_path / "acquisition"
    authorization = _authorization(
        output_root,
        bundle.inventory_digest,
        mode="new_run",
        budget_digest=bundle.full_budget.budget_digest,
    )
    manifest = response_bank_manifest_for_bundle(bundle, authorization)
    ledger = PaperBudgetLedger(
        tmp_path / "budget.sqlite3",
        limits=bundle.full_budget.to_limits(),
    )
    ledger.preregister_inventory(
        inventory_digest=bundle.inventory_digest,
        rows=bundle.inventory_rows,
    )
    transport = ScriptedExactTransport([_success_response()])
    first = ResponseBankAcquisitionOrchestrator(
        output_root=output_root,
        bank_root_id=manifest.bank_root_id,
        manifest_digest=manifest.manifest_digest,
        inventory_digest=bundle.inventory_digest,
        inventory_rows=bundle.inventory_rows,
        budget_ledger=ledger,
        paid_authorization=authorization,
        invocation_mode="new_run",
        transport=transport,
        secret_resolver=_secret,
        now_epoch=100,
    )
    initialize_calls: list[Path] = []
    real_initialize = response_bank_module.initialize_response_bank

    def count_initialize(root_path, **kwargs):
        initialize_calls.append(Path(root_path))
        return real_initialize(root_path, **kwargs)

    monkeypatch.setattr(
        response_bank_module,
        "initialize_response_bank",
        count_initialize,
    )
    first_batch = first.acquire_all(bundle.acquisition_requests)
    first_resolver = finalize_acquisition_child_bank(
        orchestrator=first,
        bundle=bundle,
        manifest=manifest,
        batch_result=first_batch,
    )

    resumed_authorization = _authorization(
        output_root,
        bundle.inventory_digest,
        mode="resume",
        budget_digest=bundle.full_budget.budget_digest,
    )
    resumed = ResponseBankAcquisitionOrchestrator(
        output_root=output_root,
        bank_root_id=manifest.bank_root_id,
        manifest_digest=manifest.manifest_digest,
        inventory_digest=bundle.inventory_digest,
        inventory_rows=bundle.inventory_rows,
        budget_ledger=ledger,
        paid_authorization=resumed_authorization,
        invocation_mode="resume",
        transport=ScriptedExactTransport([]),
        secret_resolver=_secret,
        now_epoch=100,
    )
    resumed_batch = resumed.acquire_all(bundle.acquisition_requests)
    resumed_resolver = finalize_acquisition_child_bank(
        orchestrator=resumed,
        bundle=bundle,
        manifest=manifest,
        batch_result=resumed_batch,
    )

    assert first_resolver is not None
    assert resumed_resolver is not None
    assert first_resolver.index.manifest.manifest_digest == manifest.manifest_digest
    assert resumed_resolver.index.manifest.manifest_digest == manifest.manifest_digest
    assert initialize_calls == [output_root / "immutable_response_bank"]
    assert len(transport.calls) == 1
    trace_context = build_paper_formal_trace_context(
        inventory_plan=bundle.semantic_inventory_plan,
        resolver=resumed_resolver,
    )
    assert trace_context.available_inventory_entry_ids == (row.inventory_entry_id,)
    assert trace_context.runtime_for(
        condition_id="condition-0",
        case_id="case-0",
    ).current_provider_call_count == 0


def _race_acquire_worker(
    root: str,
    db_path: str,
    rows: tuple[ResponseBankInventoryRow, ...],
    prepared: Any,
    shared_calls: Any,
    start: Any,
    results: Any,
) -> None:
    ledger = PaperBudgetLedger(db_path, limits=_limits(), lock_retries=5)
    transport = ScriptedExactTransport([_success_response(prepared.entry_id)], shared_calls)
    orchestrator, _ = _orchestrator(
        Path(root), rows, transport, mode="resume", ledger=ledger
    )
    start.wait(timeout=5)
    try:
        result = orchestrator.acquire(_request(_row(prepared, slot_override=rows[0].semantic_slot_key), prepared))
        results.put(result.status)
    except BaseException as exc:  # pragma: no cover - subprocess diagnostic
        results.put(type(exc).__name__)


def test_two_processes_same_semantic_slot_different_request_digest_only_registered_winner_invokes_transport(
    tmp_path: Path,
) -> None:
    first = _prepared(unit="race", marker="a")
    second = _prepared(unit="race", marker="b")
    first_row = _row(first)
    second_row = _row(second, slot_override=first_row.semantic_slot_key)
    rows = (first_row, second_row)
    root = tmp_path / "race-bank"
    setup, ledger = _orchestrator(root, rows, ScriptedExactTransport([]))
    del setup
    ctx = multiprocessing.get_context("spawn")
    manager = ctx.Manager()
    calls = manager.list()
    start = ctx.Event()
    results = ctx.Queue()
    processes = [
        ctx.Process(
            target=_race_acquire_worker,
            args=(str(root), str(ledger.path), rows, prepared, calls, start, results),
        )
        for prepared in (first, second)
    ]
    for process in processes:
        process.start()
    start.set()
    payloads = [results.get(timeout=15) for _ in processes]
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0
    assert len(calls) == 1
    assert sorted(payloads) == ["AcquisitionIdentityError", "settled"]


def test_loser_digest_mismatch_fails_closed_before_secret_and_transport(
    tmp_path: Path,
) -> None:
    first = _prepared(unit="same-slot", marker="winner")
    second = _prepared(unit="same-slot", marker="loser")
    first_row = _row(first)
    second_row = _row(second, slot_override=first_row.semantic_slot_key)
    secrets: list[str] = []
    transport = ScriptedExactTransport([_success_response("winner")])
    orchestrator, _ = _orchestrator(
        tmp_path / "bank",
        (first_row, second_row),
        transport,
        secret_resolver=lambda name: secrets.append(name) or "fake-secret",
    )
    orchestrator.acquire(_request(first_row, first))
    with pytest.raises(AcquisitionIdentityError):
        orchestrator.acquire(_request(second_row, second))
    assert len(transport.calls) == 1
    assert secrets == ["FAKE_DEEPSEEK_KEY"]


def test_resume_dispatches_only_missing_never_success_or_failure_terminal(
    tmp_path: Path,
) -> None:
    prepared = tuple(_prepared(unit=f"resume-{i}", marker=str(i)) for i in range(3))
    rows = tuple(_row(value) for value in prepared)
    initial, ledger = _orchestrator(
        tmp_path / "bank",
        rows,
        ScriptedExactTransport([_success_response(), _failure_outcome("rate_limited")]),
    )
    initial.acquire(_request(rows[0], prepared[0]))
    initial.acquire(_request(rows[1], prepared[1]))
    resumed_transport = ScriptedExactTransport([_success_response("missing")])
    resumed, _ = _orchestrator(
        tmp_path / "bank", rows, resumed_transport, mode="resume", ledger=ledger
    )
    result = resumed.acquire_all(tuple(_request(row, value) for row, value in zip(rows, prepared, strict=True)))
    assert len(resumed_transport.calls) == 1
    assert result.missing_inventory_entry_ids == ()


def test_crash_after_reserve_before_dispatch_intent_reconciles(tmp_path: Path) -> None:
    prepared = _prepared()
    row = _row(prepared)
    transport = ScriptedExactTransport([_success_response()])
    crashed, ledger = _orchestrator(
        tmp_path / "bank", (row,), transport, crash_hook=_crash_at("reserved")
    )
    with pytest.raises(RuntimeError, match="crash:reserved"):
        crashed.acquire(_request(row, prepared))
    resumed, _ = _orchestrator(
        tmp_path / "bank", (row,), transport, mode="resume", ledger=ledger
    )
    report = resumed.acquire_all((_request(row, prepared),))
    assert transport.calls == []
    assert report.status == "incomplete"
    assert report.missing_inventory_entry_ids == (row.inventory_entry_id,)
    assert ledger.list_reservations() == ()


def test_crash_after_dispatch_intent_before_or_after_send_becomes_ambiguous(
    tmp_path: Path,
) -> None:
    for stage, expected_calls in (("dispatch_intent", 0), ("transport_sent", 1)):
        prepared = _prepared(unit=stage, marker=stage)
        row = _row(prepared)
        transport = ScriptedExactTransport([_success_response(stage)])
        crashed, ledger = _orchestrator(
            tmp_path / stage, (row,), transport, crash_hook=_crash_at(stage)
        )
        with pytest.raises(RuntimeError, match=f"crash:{stage}"):
            crashed.acquire(_request(row, prepared))
        resumed, _ = _orchestrator(
            tmp_path / stage, (row,), transport, mode="resume", ledger=ledger
        )
        report = resumed.acquire_all((_request(row, prepared),))
        assert len(transport.calls) == expected_calls
        assert report.status == "incomplete"
        assert report.ambiguous_inventory_entry_ids == (row.inventory_entry_id,)


def test_crash_during_temp_write_or_after_fsync_before_rename_ignores_partial_object(
    tmp_path: Path,
) -> None:
    for stage in ("artifact_temp_written", "artifact_fsynced"):
        prepared = _prepared(unit=stage, marker=stage)
        row = _row(prepared)
        root = tmp_path / stage
        transport = ScriptedExactTransport([_success_response(stage)])
        crashed, ledger = _orchestrator(
            root, (row,), transport, durability_hook=_crash_at(stage)
        )
        with pytest.raises(RuntimeError, match=f"crash:{stage}"):
            crashed.acquire(_request(row, prepared))
        resumed, _ = _orchestrator(
            root, (row,), transport, mode="resume", ledger=ledger
        )
        assert resumed.acquire(_request(row, prepared)).status == "settled"
        assert len(transport.calls) == 1
        assert not tuple(root.rglob("*.tmp.*"))


def test_crash_after_rename_before_commit_marker_reconciles_object(
    tmp_path: Path,
) -> None:
    prepared = _prepared()
    row = _row(prepared)
    root = tmp_path / "bank"
    transport = ScriptedExactTransport([_success_response()])
    crashed, ledger = _orchestrator(
        root, (row,), transport, durability_hook=_crash_at("artifact_renamed")
    )
    with pytest.raises(RuntimeError, match="artifact_renamed"):
        crashed.acquire(_request(row, prepared))
    resumed, _ = _orchestrator(root, (row,), transport, mode="resume", ledger=ledger)
    assert resumed.acquire(_request(row, prepared)).status == "settled"
    assert len(transport.calls) == 1
    assert list((root / "objects").glob("*.commit.json"))


def test_crash_after_marker_before_terminal_published_reconciles_entry(
    tmp_path: Path,
) -> None:
    prepared = _prepared()
    row = _row(prepared)
    transport = ScriptedExactTransport([_success_response()])
    crashed, ledger = _orchestrator(
        tmp_path / "bank",
        (row,),
        transport,
        crash_hook=_crash_at("terminal_entry_committed"),
    )
    with pytest.raises(RuntimeError, match="terminal_entry_committed"):
        crashed.acquire(_request(row, prepared))
    resumed, _ = _orchestrator(
        tmp_path / "bank", (row,), transport, mode="resume", ledger=ledger
    )
    assert resumed.reconcile().settled_inventory_entry_ids == (row.inventory_entry_id,)
    assert len(transport.calls) == 1


def test_crash_after_terminal_published_before_settle_reconciles_once(
    tmp_path: Path,
) -> None:
    prepared = _prepared()
    row = _row(prepared)
    transport = ScriptedExactTransport([_success_response()])
    crashed, ledger = _orchestrator(
        tmp_path / "bank",
        (row,),
        transport,
        crash_hook=_crash_at("terminal_published"),
    )
    with pytest.raises(RuntimeError, match="terminal_published"):
        crashed.acquire(_request(row, prepared))
    resumed, _ = _orchestrator(
        tmp_path / "bank", (row,), transport, mode="resume", ledger=ledger
    )
    resumed.reconcile()
    first = ledger.get_reservation(_inventory_digest((row,)), row.inventory_entry_id)
    resumed.reconcile()
    second = ledger.get_reservation(_inventory_digest((row,)), row.inventory_entry_id)
    assert first == second
    assert len(transport.calls) == 1


def test_temp_flush_fsync_rename_and_commit_marker_are_durable_order(
    tmp_path: Path,
) -> None:
    stages: list[str] = []
    ArtifactStore(tmp_path).save_bytes(
        b"durable",
        artifact_id="durable-object",
        artifact_type="test",
        media_type="application/octet-stream",
        artifact_schema_id="tokenshare.test",
        artifact_schema_version="v1",
        source={},
        metadata={},
        created_at="2026-08-02T00:00:00Z",
        durability_hook=stages.append,
    )
    assert stages[:5] == [
        "artifact_temp_written",
        "artifact_flushed",
        "artifact_fsynced",
        "artifact_renamed",
        "artifact_commit_marker_written",
    ]


def test_ambiguous_dispatch_needs_paid_scope_and_one_reserved_reacquisition(
    tmp_path: Path,
) -> None:
    prepared = _prepared()
    row = _row(prepared)
    transport = ScriptedExactTransport([_success_response("lost"), _success_response("retry")])
    crashed, ledger = _orchestrator(
        tmp_path / "bank", (row,), transport, crash_hook=_crash_at("transport_sent")
    )
    with pytest.raises(RuntimeError):
        crashed.acquire(_request(row, prepared))
    resumed, _ = _orchestrator(
        tmp_path / "bank", (row,), transport, mode="resume", ledger=ledger
    )
    resumed.reconcile()
    with pytest.raises(AcquisitionAuthorizationError):
        resumed.reacquire(_request(row, prepared), paid_scope_digest="wrong")
    result = resumed.reacquire(
        _request(row, prepared),
        paid_scope_digest=resumed.paid_scope_digest,
    )
    assert result.linked_ambiguous_attempt_id is not None
    assert len(ledger.list_reacquisitions()) == 1
    assert len(transport.calls) == 2
    assert resumed.reacquire(
        _request(row, prepared),
        paid_scope_digest=resumed.paid_scope_digest,
    ).status == "already_terminal"
    assert len(ledger.list_reacquisitions()) == 1


def test_expired_receipt_can_reconcile_existing_terminal_but_never_reserve_or_dispatch(
    tmp_path: Path,
) -> None:
    first = _prepared(unit="first", marker="first")
    missing = _prepared(unit="missing", marker="missing")
    rows = (_row(first), _row(missing))
    transport = ScriptedExactTransport([_success_response("first")])
    crashed, ledger = _orchestrator(
        tmp_path / "bank",
        rows,
        transport,
        crash_hook=_crash_at("terminal_published"),
    )
    with pytest.raises(RuntimeError):
        crashed.acquire(_request(rows[0], first))
    expired = _authorization(
        tmp_path / "bank",
        _inventory_digest(rows),
        mode="resume",
        expired=True,
    )
    resumed, _ = _orchestrator(
        tmp_path / "bank",
        rows,
        transport,
        mode="resume",
        context=expired,
        ledger=ledger,
    )
    assert resumed.reconcile().settled_inventory_entry_ids == (rows[0].inventory_entry_id,)
    with pytest.raises(AcquisitionAuthorizationError, match="expired"):
        resumed.acquire(_request(rows[1], missing))
    assert len(transport.calls) == 1

    expired_new_root = tmp_path / "expired-new-run"
    with pytest.raises(PaidAuthorizationError, match="expired"):
        _authorization(
            expired_new_root,
            _inventory_digest(rows),
            mode="new_run",
            expired=True,
        )
    assert not expired_new_root.exists()


def test_budget_exhaustion_closes_inflight_and_leaves_exact_missing_inventory(
    tmp_path: Path,
) -> None:
    prepared = tuple(_prepared(unit=f"budget-{i}", marker=str(i)) for i in range(2))
    rows = tuple(_row(value) for value in prepared)
    transport = ScriptedExactTransport([_success_response("only")])
    orchestrator, ledger = _orchestrator(
        tmp_path / "bank", rows, transport, limits=_limits(calls=1)
    )
    report = orchestrator.acquire_all(
        tuple(_request(row, value) for row, value in zip(rows, prepared, strict=True)),
        max_in_flight=2,
    )
    assert report.status == "blocked"
    assert report.blocked_reason == "provider call hard limit exceeded"
    assert len(report.missing_inventory_entry_ids) == 1
    assert set(report.missing_inventory_entry_ids) < {
        row.inventory_entry_id for row in rows
    }
    assert len(transport.calls) == 1
    assert len(report.results) == 1
    assert report.results[0].entry.inventory_entry_id not in set(
        report.missing_inventory_entry_ids
    )
    assert all(record.state == "settled" for record in ledger.list_reservations())


def test_acquire_all_uses_bounded_parallelism_and_preserves_request_order(
    tmp_path: Path,
) -> None:
    prepared = tuple(
        _prepared(unit=f"parallel-{index}", marker=str(index))
        for index in range(4)
    )
    rows = tuple(_row(value) for value in prepared)
    transport = BoundedParallelProbeTransport()
    orchestrator, _ = _orchestrator(
        tmp_path / "bank", rows, transport, limits=_limits(calls=4)
    )
    requests = tuple(
        _request(row, value) for row, value in zip(rows, prepared, strict=True)
    )

    report = orchestrator.acquire_all(requests, max_in_flight=3)

    assert report.status == "complete"
    assert 1 < transport.max_active <= 3
    assert len(transport.calls) == 4
    assert tuple(result.entry.inventory_entry_id for result in report.results) == tuple(
        row.inventory_entry_id for row in rows
    )


def test_provider_failure_persists_terminal_and_continues_independent_slots(
    tmp_path: Path,
) -> None:
    prepared = tuple(
        _prepared(unit=f"failure-stop-{index}", marker=str(index))
        for index in range(5)
    )
    rows = tuple(_row(value) for value in prepared)
    transport = FailureStopsNewSubmissionsTransport()
    orchestrator, _ = _orchestrator(
        tmp_path / "bank", rows, transport, limits=_limits(calls=5)
    )
    requests = tuple(
        _request(row, value) for row, value in zip(rows, prepared, strict=True)
    )

    report = orchestrator.acquire_all(requests, max_in_flight=2)

    assert report.status == "complete"
    assert transport.max_active == 2
    assert len(transport.calls) == 5
    assert report.missing_inventory_entry_ids == ()
    assert tuple(result.entry.inventory_entry_id for result in report.results) == tuple(
        row.inventory_entry_id for row in rows
    )
    assert sum(
        result.entry.terminal_kind == "provider_failure"
        for result in report.results
    ) == 1


def test_serial_provider_failure_continues_independent_slots(tmp_path: Path) -> None:
    prepared = tuple(
        _prepared(unit=f"serial-failure-stop-{index}", marker=str(index))
        for index in range(5)
    )
    rows = tuple(_row(value) for value in prepared)
    transport = ScriptedExactTransport(
        [
            _failure_outcome("provider_error"),
            *(_success_response(f"unexpected-{index}") for index in range(4)),
        ]
    )
    orchestrator, _ = _orchestrator(
        tmp_path / "bank", rows, transport, limits=_limits(calls=5)
    )
    requests = tuple(
        _request(row, value) for row, value in zip(rows, prepared, strict=True)
    )

    report = orchestrator.acquire_all(requests, max_in_flight=1)

    assert report.status == "complete"
    assert len(transport.calls) == 5
    assert report.missing_inventory_entry_ids == ()
    assert len(report.results) == 5
    assert report.results[0].transport_invoked is True
    assert report.results[0].entry.terminal_kind == "provider_failure"


def test_resume_skips_all_persisted_slots_after_provider_failure_continues(
    tmp_path: Path,
) -> None:
    prepared = tuple(
        _prepared(unit=f"resume-failure-{index}", marker=str(index))
        for index in range(5)
    )
    rows = tuple(_row(value) for value in prepared)
    prepared_by_inventory_id = {
        row.inventory_entry_id: value
        for row, value in zip(rows, prepared, strict=True)
    }
    rows = tuple(sorted(rows, key=lambda row: row.inventory_entry_id))
    prepared = tuple(
        prepared_by_inventory_id[row.inventory_entry_id] for row in rows
    )
    requests = tuple(
        _request(row, value) for row, value in zip(rows, prepared, strict=True)
    )
    bundle = create_acquisition_plan_bundle(
        tmp_path / "plan-bundle",
        authorized_plan_digest="sha256:" + "6" * 64,
        profile_digest="sha256:" + "7" * 64,
        semantic_inventory_plan=_semantic_plan(rows),
        acquisition_requests=requests,
    )
    output_root = tmp_path / "acquisition"
    authorization = _authorization(
        output_root,
        bundle.inventory_digest,
        mode="new_run",
        budget_digest=bundle.full_budget.budget_digest,
    )
    manifest = response_bank_manifest_for_bundle(bundle, authorization)
    ledger = PaperBudgetLedger(
        tmp_path / "budget.sqlite3",
        limits=bundle.full_budget.to_limits(),
    )
    ledger.preregister_inventory(
        inventory_digest=bundle.inventory_digest,
        rows=bundle.inventory_rows,
    )
    first_transport = FailureStopsNewSubmissionsTransport()
    first = ResponseBankAcquisitionOrchestrator(
        output_root=output_root,
        bank_root_id=manifest.bank_root_id,
        manifest_digest=manifest.manifest_digest,
        inventory_digest=bundle.inventory_digest,
        inventory_rows=bundle.inventory_rows,
        budget_ledger=ledger,
        paid_authorization=authorization,
        invocation_mode="new_run",
        transport=first_transport,
        secret_resolver=_secret,
        now_epoch=100,
    )

    first_batch = first.acquire_all(bundle.acquisition_requests, max_in_flight=2)

    assert first_batch.status == "complete"
    assert len(first_transport.calls) == 5
    assert first_batch.missing_inventory_entry_ids == ()
    persisted = {
        result.entry.inventory_entry_id: (
            result.entry.to_dict(),
            first._entry_store.load_artifact_ref(
                first._entry_artifact_id(result.entry.inventory_entry_id)
            ).to_dict(),
        )
        for result in first_batch.results
    }
    failure_entry = next(
        result.entry
        for result in first_batch.results
        if result.entry.terminal_kind == "provider_failure"
    )

    resume_transport = ScriptedExactTransport([])
    resumed_authorization = _authorization(
        output_root,
        bundle.inventory_digest,
        mode="resume",
        budget_digest=bundle.full_budget.budget_digest,
    )
    resumed = ResponseBankAcquisitionOrchestrator(
        output_root=output_root,
        bank_root_id=manifest.bank_root_id,
        manifest_digest=manifest.manifest_digest,
        inventory_digest=bundle.inventory_digest,
        inventory_rows=bundle.inventory_rows,
        budget_ledger=ledger,
        paid_authorization=resumed_authorization,
        invocation_mode="resume",
        transport=resume_transport,
        secret_resolver=_secret,
        now_epoch=100,
    )

    resumed_batch = resumed.acquire_all(bundle.acquisition_requests, max_in_flight=2)
    resolver = finalize_acquisition_child_bank(
        orchestrator=resumed,
        bundle=bundle,
        manifest=manifest,
        batch_result=resumed_batch,
    )

    assert resumed_batch.status == "complete"
    assert resumed_batch.missing_inventory_entry_ids == ()
    assert resume_transport.calls == []
    assert {result.status for result in resumed_batch.results} == {
        "already_terminal"
    }
    for inventory_entry_id, before in persisted.items():
        entry = resumed._load_entry(inventory_entry_id)
        ref = resumed._entry_store.load_artifact_ref(
            resumed._entry_artifact_id(inventory_entry_id)
        )
        assert (entry.to_dict(), ref.to_dict()) == before
    assert ledger.list_reacquisitions() == ()
    assert resolver is not None
    assert resolver.entry(failure_entry.entry_id).terminal_kind == "provider_failure"


def test_existing_settled_attempts_are_loaded_in_order_without_provider_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver, ledger, transport, attempt_rows = _existing_attempt_evidence(
        tmp_path,
        unit="unit-history",
        outcomes=tuple(
            _success_response(f"wrong-{attempt_index}")
            for attempt_index in range(3)
        ),
    )
    assert len(transport.calls) == 3

    provider_bomb_calls: list[object] = []

    def provider_bomb(*args: Any, **kwargs: Any) -> None:
        provider_bomb_calls.append((args, kwargs))
        raise AssertionError("ordered existing-attempt loading dispatched provider")

    monkeypatch.setattr(
        response_bank_module,
        "dispatch_prepared_request_once",
        provider_bomb,
    )

    attempts = response_bank_module.load_ordered_existing_settled_attempts(
        resolver=resolver,
        budget_ledger=ledger,
        case_record_digest=attempt_rows[0].case_record_digest,
        planned_ai_unit_id="unit-history",
        sample_slot_index=0,
    )

    assert provider_bomb_calls == []
    assert len(transport.calls) == 3
    assert [attempt.attempt_index for attempt in attempts] == [0, 1, 2]
    assert [attempt.source_replacement_slot for attempt in attempts] == [0, 1, 2]
    assert [attempt.source_entry_id for attempt in attempts] == [
        next(
            entry.entry_id
            for entry in resolver.index.entries
            if entry.inventory_entry_id == row.inventory_entry_id
        )
        for row in attempt_rows
    ]
    assert [attempt.total_tokens for attempt in attempts] == [15, 15, 15]
    assert [attempt.cost_cny for attempt in attempts] == [
        Decimal("0.0000125"),
        Decimal("0.0000125"),
        Decimal("0.0000125"),
    ]
    assert all(attempt.api_latency_ms is not None for attempt in attempts)
    assert [attempt.terminal_selected for attempt in attempts] == [False, False, True]
    assert [attempt.retry_reason for attempt in attempts] == [None, None, None]
    assert [attempt.requeue_reason for attempt in attempts] == [None, None, None]
    assert all(attempt.terminal_kind == "success" for attempt in attempts)
    assert all(attempt.provider_family == "deepseek" for attempt in attempts)
    assert all(attempt.model_id == "deepseek-v4-pro" for attempt in attempts)
    assert all(
        attempt.provider_config_digest == attempt_rows[0].provider_config_digest
        for attempt in attempts
    )
    assert all(
        attempt.request_artifact_digest == row.body_digest
        for attempt, row in zip(attempts, attempt_rows, strict=True)
    )
    assert all(attempt.response_artifact_ref.endswith(":raw_output") for attempt in attempts)
    assert all(attempt.ledger_ref.startswith("paper_budget_ledger.reservation:") for attempt in attempts)
    assert all(attempt.ledger_record_digest.startswith("sha256:") for attempt in attempts)


def test_existing_attempts_preserve_provider_failure_missing_usage_and_continue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver, ledger, transport, rows = _existing_attempt_evidence(
        tmp_path,
        unit="unit-provider-failure",
        outcomes=(
            FakeProviderResponse(status_code=503, body={"message": "provider failure"}),
            _success_response("still-wrong"),
        ),
    )
    provider_bomb_calls: list[object] = []

    def provider_bomb(*args: Any, **kwargs: Any) -> None:
        provider_bomb_calls.append((args, kwargs))
        raise AssertionError("existing provider failure triggered a new provider call")

    monkeypatch.setattr(
        response_bank_module,
        "dispatch_prepared_request_once",
        provider_bomb,
    )

    attempts = response_bank_module.load_ordered_existing_settled_attempts(
        resolver=resolver,
        budget_ledger=ledger,
        case_record_digest=rows[0].case_record_digest,
        planned_ai_unit_id="unit-provider-failure",
        sample_slot_index=0,
    )

    assert provider_bomb_calls == []
    assert len(transport.calls) == 2
    assert [attempt.terminal_kind for attempt in attempts] == [
        "provider_failure",
        "success",
    ]
    assert attempts[0].failure_kind == "provider_error"
    assert (
        attempts[0].input_tokens,
        attempts[0].output_tokens,
        attempts[0].total_tokens,
        attempts[0].cost_cny,
    ) == (None, None, None, None)
    assert attempts[0].response_artifact_ref.endswith(":provider_failure")
    assert attempts[1].total_tokens == 15
    assert attempts[1].terminal_selected is True


@pytest.mark.parametrize(
    "drift",
    ("ambiguous", "dispatch_intent", "terminal_ref", "order_gap", "request_identity"),
)
def test_existing_attempts_fail_closed_on_ledger_order_or_identity_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    resolver, ledger, _, rows = _existing_attempt_evidence(
        tmp_path,
        unit="unit-drift",
        outcomes=tuple(_success_response(f"wrong-{index}") for index in range(3)),
    )
    if drift in {"ambiguous", "dispatch_intent", "terminal_ref"}:
        records = ledger.list_reservations()
        target_id = rows[1].inventory_entry_id
        altered = tuple(
            replace(
                record,
                **(
                    {"terminal_ref": "sha256:" + "f" * 64}
                    if drift == "terminal_ref"
                    else {"state": drift}
                ),
            )
            if record.inventory_entry_id == target_id
            else record
            for record in records
        )
        monkeypatch.setattr(ledger, "list_reservations", lambda: altered)
    elif drift == "order_gap":
        resolver.index.inventory_rows = tuple(
            row
            for row in resolver.index.inventory_rows
            if row.replacement_slot != 1
        )
    else:
        resolver.index.inventory_rows = tuple(
            replace(row, body_digest="sha256:" + "e" * 64)
            if row.replacement_slot == 1
            else row
            for row in resolver.index.inventory_rows
        )

    with pytest.raises(AcquisitionIdentityError):
        response_bank_module.load_ordered_existing_settled_attempts(
            resolver=resolver,
            budget_ledger=ledger,
            case_record_digest=rows[0].case_record_digest,
            planned_ai_unit_id="unit-drift",
            sample_slot_index=0,
        )


def test_existing_attempts_fail_closed_on_response_artifact_digest_drift(
    tmp_path: Path,
) -> None:
    resolver, ledger, _, rows = _existing_attempt_evidence(
        tmp_path,
        unit="unit-artifact-drift",
        outcomes=(_success_response("wrong"),),
    )
    entry = resolver.index.entries[0]
    response_locator = next(
        locator
        for locator in entry.object_locators
        if locator.object_role == "raw_output"
    )
    object_path = (
        resolver.root_path
        / "objects"
        / response_locator.object_digest.removeprefix("sha256:")
    )
    object_path.write_bytes(b"{}")

    with pytest.raises(ValueError, match="digest"):
        response_bank_module.load_ordered_existing_settled_attempts(
            resolver=resolver,
            budget_ledger=ledger,
            case_record_digest=rows[0].case_record_digest,
            planned_ai_unit_id="unit-artifact-drift",
            sample_slot_index=0,
        )
