from __future__ import annotations

import json
import multiprocessing
import sqlite3
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from tokenshare.executors.response_bank import (
    ResponseBankInventoryRow,
    canonical_digest,
    inventory_entry_id,
    semantic_slot_key,
)
from tokenshare.experiments.paper_budget import (
    L3_SMALL_PAID_BUDGET_LIMITS,
    PaperBudgetLimits,
    validate_provider_budget_mode,
)
from tokenshare.experiments.paper_budget_ledger import (
    BudgetExceededError,
    InventoryIdentityError,
    InvalidBudgetStateTransition,
    PaperBudgetLedger,
    PaperBudgetLedgerBusyError,
    ReservationRequest,
)
from tokenshare.experiments.paper_resource_accounting import (
    FrozenPricing,
    ProviderUsage,
)


def _inventory_row(
    *, request_digest: str = "request-a", planned_ai_unit_id: str = "unit-0"
) -> ResponseBankInventoryRow:
    slot = semantic_slot_key(
        case_record_digest="case-digest",
        planned_ai_unit_id=planned_ai_unit_id,
        sample_slot_index=0,
        replacement_slot=0,
        provider_config_digest="provider-config-digest",
        prompt_profile_digest="prompt-profile-digest",
        prompt_admission_profile_digest="admission-digest",
        plugin_version="plugin.v1",
    )
    provisional = ResponseBankInventoryRow(
        inventory_entry_id="",
        semantic_slot_key=slot,
        case_record_digest="case-digest",
        planned_ai_unit_id=planned_ai_unit_id,
        sample_slot_index=0,
        replacement_slot=0,
        provider_config_digest="provider-config-digest",
        prompt_profile_digest="prompt-profile-digest",
        prompt_admission_profile_digest="admission-digest",
        plugin_version="plugin.v1",
        entry_id="provider-entry",
        body_digest=f"body-{request_digest}",
        inference_request_digest=request_digest,
    )
    return replace(provisional, inventory_entry_id=inventory_entry_id(provisional))


def _inventory_digest(rows: list[ResponseBankInventoryRow]) -> str:
    return canonical_digest(
        [
            row.to_dict()
            for row in sorted(
                rows,
                key=lambda item: (item.semantic_slot_key, item.inventory_entry_id),
            )
        ]
    )


def _request(
    row: ResponseBankInventoryRow,
    inventory_digest: str,
    *,
    tokens: int = 10,
    cost: str = "1.0",
    provider_family: str = "deepseek",
) -> ReservationRequest:
    return ReservationRequest(
        inventory_digest=inventory_digest,
        inventory_entry_id=row.inventory_entry_id,
        semantic_slot_key=row.semantic_slot_key,
        inference_request_digest=row.inference_request_digest,
        prompt_admission_profile_digest=row.prompt_admission_profile_digest,
        token_upper_bound=tokens,
        cost_upper_bound=Decimal(cost),
        provider_family=provider_family,
        frozen_pricing=FrozenPricing(
            currency="CNY",
            input_per_million_tokens=Decimal("0.5"),
            output_per_million_tokens=Decimal("1.5"),
        ),
    )


def _ledger_with_rows(
    tmp_path: Path,
    rows: list[ResponseBankInventoryRow],
    *,
    limits: PaperBudgetLimits | None = None,
    name: str = "budget.sqlite3",
    busy_timeout_ms: int = 50,
    lock_retries: int = 2,
) -> tuple[PaperBudgetLedger, str]:
    digest = _inventory_digest(rows)
    ledger = PaperBudgetLedger(
        tmp_path / name,
        limits=limits or PaperBudgetLimits(
            calls=100,
            tokens=10_000,
            cny=Decimal("100"),
            deepseek_cumulative_cny=Decimal("1000"),
        ),
        busy_timeout_ms=busy_timeout_ms,
        lock_retries=lock_retries,
        lock_retry_backoff_seconds=0.005,
    )
    ledger.preregister_inventory(inventory_digest=digest, rows=rows)
    return ledger, digest


def _race_worker(
    db_path: str,
    inventory_digest: str,
    row_dict: dict[str, Any],
    start: Any,
    results: Any,
) -> None:
    row = ResponseBankInventoryRow.from_dict(row_dict)
    ledger = PaperBudgetLedger(
        db_path,
        limits=PaperBudgetLimits(
            calls=10,
            tokens=1_000,
            cny=Decimal("10"),
            deepseek_cumulative_cny=Decimal("1000"),
        ),
        busy_timeout_ms=100,
        lock_retries=5,
        lock_retry_backoff_seconds=0.01,
    )
    start.wait(timeout=5)
    try:
        decision = ledger.reserve(_request(row, inventory_digest))
        results.put(
            {
                "granted": decision.granted,
                "winner": decision.reservation.inventory_entry_id,
            }
        )
    except BaseException as exc:  # pragma: no cover - 仅传回子进程诊断
        results.put({"error": f"{type(exc).__name__}: {exc}"})


def test_unique_key_is_inventory_digest_and_inventory_entry_id(tmp_path: Path) -> None:
    row = _inventory_row()
    ledger, digest = _ledger_with_rows(tmp_path, [row])

    first = ledger.reserve(_request(row, digest))
    duplicate = ledger.reserve(_request(row, digest))

    assert first.granted is True
    assert duplicate.granted is False
    assert duplicate.reservation.inventory_entry_id == row.inventory_entry_id
    assert len(ledger.list_reservations()) == 1
    with sqlite3.connect(ledger.path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        indexes = connection.execute("PRAGMA index_list(reservations)").fetchall()
        unique_columns = {
            tuple(
                item[2]
                for item in connection.execute(
                    f'PRAGMA index_info("{index[1]}")'
                ).fetchall()
            )
            for index in indexes
            if index[2]
        }
    assert ("inventory_digest", "inventory_entry_id") in unique_columns


def test_transaction_recomputes_inventory_entry_id_excluding_self_before_reserve(
    tmp_path: Path,
) -> None:
    row = _inventory_row()
    ledger, digest = _ledger_with_rows(tmp_path, [row])
    forged = row.to_dict()
    forged["inventory_entry_id"] = canonical_digest(forged)
    with sqlite3.connect(ledger.path) as connection:
        connection.execute(
            "UPDATE inventory_rows SET row_json = ? WHERE inventory_digest = ?",
            (json.dumps(forged, sort_keys=True), digest),
        )
        connection.commit()

    with pytest.raises(InventoryIdentityError, match="inventory_entry_id"):
        ledger.reserve(_request(row, digest))
    assert ledger.list_reservations() == ()


def test_transaction_rejects_tampered_or_unstable_inventory_row_id(
    tmp_path: Path,
) -> None:
    row = _inventory_row()
    ledger, digest = _ledger_with_rows(tmp_path, [row])
    tampered = {**row.to_dict(), "body_digest": "tampered-body"}
    with sqlite3.connect(ledger.path) as connection:
        connection.execute(
            "UPDATE inventory_rows SET row_json = ? WHERE inventory_digest = ?",
            (json.dumps(tampered, sort_keys=True), digest),
        )
        connection.commit()

    with pytest.raises(InventoryIdentityError, match="canonical"):
        ledger.reserve(_request(row, digest))


def test_transaction_rejects_request_digest_not_equal_preregistered_row(
    tmp_path: Path,
) -> None:
    row = _inventory_row()
    ledger, digest = _ledger_with_rows(tmp_path, [row])
    request = replace(
        _request(row, digest), inference_request_digest="unregistered-request"
    )

    with pytest.raises(InventoryIdentityError, match="inference_request_digest"):
        ledger.reserve(request)
    with pytest.raises(InventoryIdentityError, match="prompt_admission_profile_digest"):
        ledger.reserve(
            replace(
                _request(row, digest),
                prompt_admission_profile_digest="unregistered-admission",
            )
        )
    assert ledger.list_reservations() == ()


def test_begin_immediate_atomically_checks_and_reserves_calls_tokens_cny(
    tmp_path: Path,
) -> None:
    assert L3_SMALL_PAID_BUDGET_LIMITS.calls == 516
    assert L3_SMALL_PAID_BUDGET_LIMITS.tokens == 171_708_288
    assert L3_SMALL_PAID_BUDGET_LIMITS.cny == Decimal("979.524864")
    assert L3_SMALL_PAID_BUDGET_LIMITS.deepseek_cumulative_cny == Decimal("1000")
    with pytest.raises(ValueError, match="unlimited"):
        validate_provider_budget_mode(provider_writing=True, budget_mode="unlimited")

    dimensions = (
        (PaperBudgetLimits(calls=1, tokens=100, cny=Decimal("100")), 10, "1"),
        (PaperBudgetLimits(calls=2, tokens=15, cny=Decimal("100")), 10, "1"),
        (PaperBudgetLimits(calls=2, tokens=100, cny=Decimal("1.5")), 10, "1"),
    )
    for index, (limits, tokens, cost) in enumerate(dimensions):
        first = _inventory_row(request_digest=f"first-{index}")
        second = _inventory_row(
            request_digest=f"second-{index}", planned_ai_unit_id="unit-1"
        )
        ledger, digest = _ledger_with_rows(
            tmp_path,
            [first, second],
            limits=limits,
            name=f"atomic-{index}.sqlite3",
        )
        assert ledger.reserve(_request(first, digest, tokens=tokens, cost=cost)).granted
        with pytest.raises(BudgetExceededError):
            ledger.reserve(_request(second, digest, tokens=tokens, cost=cost))
        assert len(ledger.list_reservations()) == 1

    deepseek_limits = PaperBudgetLimits(
        calls=3,
        tokens=100,
        cny=Decimal("2000"),
        deepseek_cumulative_cny=Decimal("1000"),
    )
    first = _inventory_row(request_digest="deepseek-first")
    second = _inventory_row(
        request_digest="deepseek-second", planned_ai_unit_id="unit-1"
    )
    ledger, digest = _ledger_with_rows(
        tmp_path,
        [first, second],
        limits=deepseek_limits,
        name="deepseek-stop.sqlite3",
    )
    ledger.reserve(_request(first, digest, cost="999"))
    with pytest.raises(BudgetExceededError, match="DeepSeek"):
        ledger.reserve(_request(second, digest, cost="2"))


def test_busy_timeout_and_bounded_lock_retry_fail_closed(tmp_path: Path) -> None:
    row = _inventory_row()
    ledger, digest = _ledger_with_rows(
        tmp_path,
        [row],
        busy_timeout_ms=10,
        lock_retries=1,
    )
    blocker = sqlite3.connect(ledger.path, timeout=0)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(PaperBudgetLedgerBusyError):
            ledger.reserve(_request(row, digest))
    finally:
        blocker.rollback()
        blocker.close()
    assert ledger.list_reservations() == ()


def test_two_processes_racing_same_semantic_slot_different_digest_only_registered_winner_reserves(
    tmp_path: Path,
) -> None:
    first = _inventory_row(request_digest="race-a")
    second = _inventory_row(request_digest="race-b")
    assert first.semantic_slot_key == second.semantic_slot_key
    assert first.inventory_entry_id != second.inventory_entry_id
    ledger, digest = _ledger_with_rows(tmp_path, [first, second])
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_race_worker,
            args=(str(ledger.path), digest, row.to_dict(), start, results),
        )
        for row in (first, second)
    ]
    for process in processes:
        process.start()
    start.set()
    payloads = [results.get(timeout=10) for _ in processes]
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    assert not [payload for payload in payloads if "error" in payload]
    assert sorted(payload["granted"] for payload in payloads) == [False, True]
    winners = {payload["winner"] for payload in payloads}
    assert len(winners) == 1
    assert len(ledger.list_reservations()) == 1


def test_state_machine_allows_only_reserved_dispatch_intent_ambiguous_or_terminal_published_settled(
    tmp_path: Path,
) -> None:
    ambiguous_row = _inventory_row(request_digest="ambiguous")
    terminal_row = _inventory_row(
        request_digest="terminal", planned_ai_unit_id="unit-1"
    )
    ledger, digest = _ledger_with_rows(tmp_path, [ambiguous_row, terminal_row])

    ledger.reserve(_request(ambiguous_row, digest))
    assert ledger.mark_dispatch_intent(digest, ambiguous_row.inventory_entry_id).state == "dispatch_intent"
    assert ledger.mark_ambiguous(digest, ambiguous_row.inventory_entry_id).state == "ambiguous"
    assert ledger.publish_terminal(
        digest,
        ambiguous_row.inventory_entry_id,
        terminal_ref="terminal-ambiguous",
        terminal_kind="provider_failure",
    ).state == "terminal_published"
    assert ledger.reconcile_terminal(
        digest, ambiguous_row.inventory_entry_id, usage=None
    ).state == "settled"

    ledger.reserve(_request(terminal_row, digest))
    with pytest.raises(InvalidBudgetStateTransition):
        ledger.mark_ambiguous(digest, terminal_row.inventory_entry_id)
    ledger.mark_dispatch_intent(digest, terminal_row.inventory_entry_id)
    ledger.publish_terminal(
        digest,
        terminal_row.inventory_entry_id,
        terminal_ref="terminal-success",
        terminal_kind="success",
    )
    with pytest.raises(InvalidBudgetStateTransition):
        ledger.mark_dispatch_intent(digest, terminal_row.inventory_entry_id)


def test_crash_reopen_preserves_inflight_reservation(tmp_path: Path) -> None:
    row = _inventory_row()
    ledger, digest = _ledger_with_rows(tmp_path, [row])
    ledger.reserve(_request(row, digest))
    ledger.mark_dispatch_intent(digest, row.inventory_entry_id)

    reopened = PaperBudgetLedger(ledger.path, limits=ledger.limits)

    record = reopened.get_reservation(digest, row.inventory_entry_id)
    assert record.state == "dispatch_intent"
    assert reopened.reserve(_request(row, digest)).granted is False


def test_terminal_entry_without_settle_reconciles_exactly_once(tmp_path: Path) -> None:
    row = _inventory_row()
    ledger, digest = _ledger_with_rows(tmp_path, [row])
    ledger.reserve(_request(row, digest, tokens=100, cost="10"))
    ledger.mark_dispatch_intent(digest, row.inventory_entry_id)
    ledger.publish_terminal(
        digest,
        row.inventory_entry_id,
        terminal_ref="immutable-terminal-ref",
        terminal_kind="success",
    )

    reopened = PaperBudgetLedger(ledger.path, limits=ledger.limits)
    usage = ProviderUsage(input_tokens=2, output_tokens=3)
    first = reopened.reconcile_terminal_ref(
        terminal_ref="immutable-terminal-ref", usage=usage
    )
    second = reopened.reconcile_terminal_ref(
        terminal_ref="immutable-terminal-ref", usage=usage
    )

    assert first == second
    assert first.state == "settled"
    assert first.charged_tokens == 5
    assert first.cost_estimate == Decimal("0.0000055")
    assert len(reopened.list_reservations()) == 1


def test_missing_usage_settles_full_upper(tmp_path: Path) -> None:
    row = _inventory_row()
    ledger, digest = _ledger_with_rows(
        tmp_path, [row], limits=L3_SMALL_PAID_BUDGET_LIMITS
    )
    ledger.reserve(_request(row, digest, tokens=332_768, cost="1.898304"))
    ledger.mark_dispatch_intent(digest, row.inventory_entry_id)
    ledger.publish_terminal(
        digest,
        row.inventory_entry_id,
        terminal_ref="provider-failure-ref",
        terminal_kind="provider_failure",
    )

    settled = ledger.reconcile_terminal(digest, row.inventory_entry_id, usage=None)

    assert settled.usage_missing is True
    assert settled.charged_tokens == 332_768
    assert settled.cost_estimate == Decimal("1.898304")


def test_jsonl_is_export_not_authority(tmp_path: Path) -> None:
    row = _inventory_row()
    ledger, digest = _ledger_with_rows(tmp_path, [row])
    ledger.reserve(_request(row, digest))
    audit_path = tmp_path / "budget-audit.jsonl"

    exported = ledger.export_audit_jsonl(audit_path)
    assert exported == 1
    line = json.loads(audit_path.read_text(encoding="utf-8").strip())
    assert line["inventory_entry_id"] == row.inventory_entry_id
    audit_path.write_text(
        json.dumps({**line, "state": "settled", "cost_estimate": "0"}) + "\n",
        encoding="utf-8",
    )

    reopened = PaperBudgetLedger(ledger.path, limits=ledger.limits)
    record = reopened.get_reservation(digest, row.inventory_entry_id)
    assert record.state == "reserved"
    assert record.cost_estimate is None
