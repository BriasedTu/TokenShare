from __future__ import annotations

import json
import multiprocessing
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from tests.phase7_fixtures import FakeProviderResponse
from tokenshare.experiments import paper_response_bank as response_bank_module
from tokenshare.executors.ai_api_request_identity import PreparedOutboundRequestFactory
from tokenshare.executors.response_bank import (
    COMMON_ROLES,
    ResponseBankResolver,
    ResponseBankInventoryRow,
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


def _prepared(*, unit: str = "unit-0", marker: str = "same"):
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
        replacement_slot=0,
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


def _crash_at(target: str):
    def crash(stage: str) -> None:
        if stage == target:
            raise RuntimeError(f"crash:{stage}")

    return crash


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
        "dispatch_intent",
        "secret_resolved",
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
        tuple(_request(row, value) for row, value in zip(rows, prepared, strict=True))
    )
    assert report.status == "blocked"
    assert report.blocked_reason == "provider call hard limit exceeded"
    assert report.missing_inventory_entry_ids == (rows[1].inventory_entry_id,)
    assert len(transport.calls) == 1
    assert all(record.state == "settled" for record in ledger.list_reservations())
