"""SQLite WAL 论文 acquisition 预算权威；不承担协议状态或 provider transport。"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Literal, Sequence, TypeVar

from tokenshare.executors.response_bank import (
    ResponseBankInventoryRow,
    canonical_digest,
)
from tokenshare.experiments.paper_budget import (
    L3_SMALL_PAID_BUDGET_LIMITS,
    PaperBudgetLimits,
)
from tokenshare.experiments.paper_resource_accounting import (
    FrozenPricing,
    ProviderUsage,
    account_terminal_usage,
)


BudgetState = Literal[
    "reserved",
    "dispatch_intent",
    "ambiguous",
    "terminal_published",
    "settled",
]
TerminalKind = Literal["success", "provider_failure"]
_T = TypeVar("_T")


class PaperBudgetLedgerError(RuntimeError):
    pass


class InventoryIdentityError(PaperBudgetLedgerError):
    pass


class BudgetExceededError(PaperBudgetLedgerError):
    pass


class InvalidBudgetStateTransition(PaperBudgetLedgerError):
    pass


class PaperBudgetLedgerBusyError(PaperBudgetLedgerError):
    pass


@dataclass(frozen=True, kw_only=True)
class ReservationRequest:
    inventory_digest: str
    inventory_entry_id: str
    semantic_slot_key: str
    inference_request_digest: str
    prompt_admission_profile_digest: str
    token_upper_bound: int
    cost_upper_bound: Decimal
    provider_family: str
    frozen_pricing: FrozenPricing

    def __post_init__(self) -> None:
        if self.token_upper_bound < 1 or self.cost_upper_bound <= 0:
            raise ValueError("reservation upper bounds must be positive")
        if not self.provider_family:
            raise ValueError("provider_family must be non-empty")


@dataclass(frozen=True, kw_only=True)
class ReservationRecord:
    inventory_digest: str
    inventory_entry_id: str
    semantic_slot_key: str
    state: BudgetState
    token_upper_bound: int
    cost_upper_bound: Decimal
    provider_family: str
    terminal_ref: str | None
    terminal_kind: str | None
    charged_tokens: int | None
    cost_estimate: Decimal | None
    usage_missing: bool | None
    revision: int


@dataclass(frozen=True, kw_only=True)
class ReservationDecision:
    granted: bool
    reservation: ReservationRecord


class PaperBudgetLedger:
    """只管理 provider acquisition accounting，不映射 ProtocolEngine 终态。"""

    def __init__(
        self,
        path: str | Path,
        *,
        limits: PaperBudgetLimits = L3_SMALL_PAID_BUDGET_LIMITS,
        busy_timeout_ms: int = 100,
        lock_retries: int = 3,
        lock_retry_backoff_seconds: float = 0.01,
    ) -> None:
        if busy_timeout_ms < 1 or lock_retries < 0 or lock_retry_backoff_seconds < 0:
            raise ValueError("lock retry configuration must be bounded and non-negative")
        self.path = Path(path)
        self.limits = limits
        self.busy_timeout_ms = busy_timeout_ms
        self.lock_retries = lock_retries
        self.lock_retry_backoff_seconds = lock_retry_backoff_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        def create(connection: sqlite3.Connection) -> None:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS inventories (
                    inventory_digest TEXT PRIMARY KEY,
                    row_count INTEGER NOT NULL CHECK (row_count >= 0)
                );
                CREATE TABLE IF NOT EXISTS inventory_rows (
                    inventory_digest TEXT NOT NULL,
                    inventory_entry_id TEXT NOT NULL,
                    semantic_slot_key TEXT NOT NULL,
                    inference_request_digest TEXT NOT NULL,
                    prompt_admission_profile_digest TEXT NOT NULL,
                    row_json TEXT NOT NULL,
                    PRIMARY KEY (inventory_digest, inventory_entry_id),
                    FOREIGN KEY (inventory_digest) REFERENCES inventories(inventory_digest)
                );
                CREATE TABLE IF NOT EXISTS reservations (
                    inventory_digest TEXT NOT NULL,
                    inventory_entry_id TEXT NOT NULL,
                    semantic_slot_key TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN (
                        'reserved', 'dispatch_intent', 'ambiguous',
                        'terminal_published', 'settled'
                    )),
                    call_count INTEGER NOT NULL CHECK (call_count = 1),
                    token_upper_bound INTEGER NOT NULL CHECK (token_upper_bound > 0),
                    cost_upper_bound TEXT NOT NULL,
                    provider_family TEXT NOT NULL,
                    frozen_pricing_json TEXT NOT NULL,
                    terminal_ref TEXT,
                    terminal_kind TEXT CHECK (
                        terminal_kind IS NULL OR terminal_kind IN ('success', 'provider_failure')
                    ),
                    charged_tokens INTEGER,
                    cost_estimate TEXT,
                    usage_missing INTEGER,
                    usage_json TEXT,
                    revision INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (inventory_digest, inventory_entry_id),
                    UNIQUE (inventory_digest, semantic_slot_key),
                    UNIQUE (terminal_ref),
                    FOREIGN KEY (inventory_digest, inventory_entry_id)
                        REFERENCES inventory_rows(inventory_digest, inventory_entry_id)
                );
                """
            )

        self._write(create)

    def _write(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        last_busy: sqlite3.OperationalError | None = None
        for attempt in range(self.lock_retries + 1):
            connection: sqlite3.Connection | None = None
            try:
                connection = self._connect()
                connection.execute("BEGIN IMMEDIATE")
                result = operation(connection)
                connection.commit()
                return result
            except sqlite3.OperationalError as exc:
                if connection is not None:
                    connection.rollback()
                if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                    raise
                last_busy = exc
                if attempt == self.lock_retries:
                    break
                time.sleep(self.lock_retry_backoff_seconds * (2**attempt))
            except BaseException:
                if connection is not None:
                    connection.rollback()
                raise
            finally:
                if connection is not None:
                    connection.close()
        raise PaperBudgetLedgerBusyError(
            "SQLite budget authority remained busy after bounded retries"
        ) from last_busy

    def preregister_inventory(
        self,
        *,
        inventory_digest: str,
        rows: Sequence[ResponseBankInventoryRow],
    ) -> None:
        canonical_rows = tuple(
            sorted(rows, key=lambda row: (row.semantic_slot_key, row.inventory_entry_id))
        )
        normalized: list[ResponseBankInventoryRow] = []
        for row in canonical_rows:
            try:
                normalized.append(ResponseBankInventoryRow.from_dict(row.to_dict()))
            except ValueError as exc:
                raise InventoryIdentityError(str(exc)) from exc
        expected_digest = canonical_digest([row.to_dict() for row in normalized])
        if inventory_digest != expected_digest:
            raise InventoryIdentityError("inventory_digest does not match canonical rows")

        def register(connection: sqlite3.Connection) -> None:
            existing = connection.execute(
                "SELECT row_count FROM inventories WHERE inventory_digest = ?",
                (inventory_digest,),
            ).fetchone()
            if existing is not None:
                self._validate_inventory(connection, inventory_digest)
                if int(existing["row_count"]) != len(normalized):
                    raise InventoryIdentityError("preregistered inventory row count drift")
                return
            connection.execute(
                "INSERT INTO inventories(inventory_digest, row_count) VALUES (?, ?)",
                (inventory_digest, len(normalized)),
            )
            for row in normalized:
                connection.execute(
                    """
                    INSERT INTO inventory_rows(
                        inventory_digest, inventory_entry_id, semantic_slot_key,
                        inference_request_digest, prompt_admission_profile_digest, row_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        inventory_digest,
                        row.inventory_entry_id,
                        row.semantic_slot_key,
                        row.inference_request_digest,
                        row.prompt_admission_profile_digest,
                        json.dumps(row.to_dict(), ensure_ascii=False, sort_keys=True),
                    ),
                )

        self._write(register)

    def _validate_inventory(
        self, connection: sqlite3.Connection, inventory_digest: str
    ) -> tuple[ResponseBankInventoryRow, ...]:
        manifest = connection.execute(
            "SELECT row_count FROM inventories WHERE inventory_digest = ?",
            (inventory_digest,),
        ).fetchone()
        if manifest is None:
            raise InventoryIdentityError("inventory_digest is not preregistered")
        stored = connection.execute(
            """
            SELECT inventory_entry_id, semantic_slot_key, inference_request_digest,
                   prompt_admission_profile_digest, row_json
            FROM inventory_rows
            WHERE inventory_digest = ?
            ORDER BY semantic_slot_key, inventory_entry_id
            """,
            (inventory_digest,),
        ).fetchall()
        if len(stored) != int(manifest["row_count"]):
            raise InventoryIdentityError("preregistered inventory row count drift")
        rows: list[ResponseBankInventoryRow] = []
        for stored_row in stored:
            try:
                body = json.loads(stored_row["row_json"])
                row = ResponseBankInventoryRow.from_dict(body)
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise InventoryIdentityError(
                    f"preregistered inventory row is not canonical: {exc}"
                ) from exc
            for field_name in (
                "inventory_entry_id",
                "semantic_slot_key",
                "inference_request_digest",
                "prompt_admission_profile_digest",
            ):
                if stored_row[field_name] != getattr(row, field_name):
                    raise InventoryIdentityError(
                        f"preregistered {field_name} column does not match canonical row"
                    )
            rows.append(row)
        if canonical_digest([row.to_dict() for row in rows]) != inventory_digest:
            raise InventoryIdentityError("inventory_digest does not match committed rows")
        return tuple(rows)

    def reserve(self, request: ReservationRequest) -> ReservationDecision:
        def reserve_once(connection: sqlite3.Connection) -> ReservationDecision:
            rows = self._validate_inventory(connection, request.inventory_digest)
            registered = next(
                (
                    row
                    for row in rows
                    if row.inventory_entry_id == request.inventory_entry_id
                ),
                None,
            )
            if registered is None:
                raise InventoryIdentityError("inventory_entry_id is not preregistered")
            for field_name in (
                "semantic_slot_key",
                "inference_request_digest",
                "prompt_admission_profile_digest",
            ):
                if getattr(request, field_name) != getattr(registered, field_name):
                    raise InventoryIdentityError(
                        f"request {field_name} does not match preregistered row"
                    )

            existing = connection.execute(
                """
                SELECT * FROM reservations
                WHERE inventory_digest = ? AND inventory_entry_id = ?
                """,
                (request.inventory_digest, request.inventory_entry_id),
            ).fetchone()
            if existing is not None:
                return ReservationDecision(
                    granted=False, reservation=_record_from_row(existing)
                )
            winner = connection.execute(
                """
                SELECT * FROM reservations
                WHERE inventory_digest = ? AND semantic_slot_key = ?
                """,
                (request.inventory_digest, request.semantic_slot_key),
            ).fetchone()
            if winner is not None:
                return ReservationDecision(
                    granted=False, reservation=_record_from_row(winner)
                )

            totals = connection.execute(
                """
                SELECT COALESCE(SUM(call_count), 0) AS calls,
                       COALESCE(SUM(CASE WHEN state = 'settled'
                           THEN charged_tokens ELSE token_upper_bound END), 0) AS tokens
                FROM reservations
                """
            ).fetchone()
            cost_total = sum(
                (
                    Decimal(row["cost_estimate"])
                    if row["state"] == "settled"
                    else Decimal(row["cost_upper_bound"])
                )
                for row in connection.execute(
                    "SELECT state, cost_upper_bound, cost_estimate FROM reservations"
                ).fetchall()
            )
            next_calls = int(totals["calls"]) + 1
            next_tokens = int(totals["tokens"]) + request.token_upper_bound
            next_cost = cost_total + request.cost_upper_bound
            if next_calls > self.limits.calls:
                raise BudgetExceededError("provider call hard limit exceeded")
            if next_tokens > self.limits.tokens:
                raise BudgetExceededError("token hard limit exceeded")
            if next_cost > self.limits.cny:
                raise BudgetExceededError("CNY reservation hard limit exceeded")
            if (
                request.provider_family == "deepseek"
                and next_cost > self.limits.deepseek_cumulative_cny
            ):
                raise BudgetExceededError("DeepSeek cumulative CNY hard stop exceeded")

            pricing_json = json.dumps(
                {
                    "currency": request.frozen_pricing.currency,
                    "input_per_million_tokens": str(
                        request.frozen_pricing.input_per_million_tokens
                    ),
                    "output_per_million_tokens": str(
                        request.frozen_pricing.output_per_million_tokens
                    ),
                },
                sort_keys=True,
            )
            connection.execute(
                """
                INSERT INTO reservations(
                    inventory_digest, inventory_entry_id, semantic_slot_key,
                    state, call_count, token_upper_bound, cost_upper_bound,
                    provider_family, frozen_pricing_json
                ) VALUES (?, ?, ?, 'reserved', 1, ?, ?, ?, ?)
                """,
                (
                    request.inventory_digest,
                    request.inventory_entry_id,
                    request.semantic_slot_key,
                    request.token_upper_bound,
                    str(request.cost_upper_bound),
                    request.provider_family,
                    pricing_json,
                ),
            )
            inserted = connection.execute(
                """
                SELECT * FROM reservations
                WHERE inventory_digest = ? AND inventory_entry_id = ?
                """,
                (request.inventory_digest, request.inventory_entry_id),
            ).fetchone()
            assert inserted is not None
            return ReservationDecision(
                granted=True, reservation=_record_from_row(inserted)
            )

        return self._write(reserve_once)

    def mark_dispatch_intent(
        self, inventory_digest: str, inventory_entry_id: str
    ) -> ReservationRecord:
        return self._transition(
            inventory_digest, inventory_entry_id, expected={"reserved"}, target="dispatch_intent"
        )

    def mark_ambiguous(
        self, inventory_digest: str, inventory_entry_id: str
    ) -> ReservationRecord:
        return self._transition(
            inventory_digest,
            inventory_entry_id,
            expected={"dispatch_intent"},
            target="ambiguous",
        )

    def _transition(
        self,
        inventory_digest: str,
        inventory_entry_id: str,
        *,
        expected: set[str],
        target: BudgetState,
    ) -> ReservationRecord:
        def transition(connection: sqlite3.Connection) -> ReservationRecord:
            current = self._select_reservation(
                connection, inventory_digest, inventory_entry_id
            )
            if current["state"] not in expected:
                raise InvalidBudgetStateTransition(
                    f"cannot transition {current['state']} to {target}"
                )
            connection.execute(
                """
                UPDATE reservations SET state = ?, revision = revision + 1
                WHERE inventory_digest = ? AND inventory_entry_id = ?
                """,
                (target, inventory_digest, inventory_entry_id),
            )
            return _record_from_row(
                self._select_reservation(connection, inventory_digest, inventory_entry_id)
            )

        return self._write(transition)

    def publish_terminal(
        self,
        inventory_digest: str,
        inventory_entry_id: str,
        *,
        terminal_ref: str,
        terminal_kind: TerminalKind,
    ) -> ReservationRecord:
        if not terminal_ref or terminal_kind not in {"success", "provider_failure"}:
            raise ValueError("terminal publication requires a ref and supported kind")

        def publish(connection: sqlite3.Connection) -> ReservationRecord:
            current = self._select_reservation(
                connection, inventory_digest, inventory_entry_id
            )
            if current["state"] in {"terminal_published", "settled"}:
                if (
                    current["terminal_ref"] == terminal_ref
                    and current["terminal_kind"] == terminal_kind
                ):
                    return _record_from_row(current)
                raise InvalidBudgetStateTransition("conflicting terminal publication")
            if current["state"] not in {"dispatch_intent", "ambiguous"}:
                raise InvalidBudgetStateTransition(
                    f"cannot publish terminal from {current['state']}"
                )
            try:
                connection.execute(
                    """
                    UPDATE reservations
                    SET state = 'terminal_published', terminal_ref = ?, terminal_kind = ?,
                        revision = revision + 1
                    WHERE inventory_digest = ? AND inventory_entry_id = ?
                    """,
                    (terminal_ref, terminal_kind, inventory_digest, inventory_entry_id),
                )
            except sqlite3.IntegrityError as exc:
                raise InventoryIdentityError(
                    "terminal_ref is already bound to another reservation"
                ) from exc
            return _record_from_row(
                self._select_reservation(connection, inventory_digest, inventory_entry_id)
            )

        return self._write(publish)

    def reconcile_terminal(
        self,
        inventory_digest: str,
        inventory_entry_id: str,
        *,
        usage: ProviderUsage | None,
    ) -> ReservationRecord:
        def reconcile(connection: sqlite3.Connection) -> ReservationRecord:
            current = self._select_reservation(
                connection, inventory_digest, inventory_entry_id
            )
            return self._reconcile_current(connection, current, usage=usage)

        return self._write(reconcile)

    def reconcile_terminal_ref(
        self, *, terminal_ref: str, usage: ProviderUsage | None
    ) -> ReservationRecord:
        """按已提交 terminal ref 幂等结算，恢复时无需重发 provider 请求。"""

        def reconcile(connection: sqlite3.Connection) -> ReservationRecord:
            current = connection.execute(
                "SELECT * FROM reservations WHERE terminal_ref = ?",
                (terminal_ref,),
            ).fetchone()
            if current is None:
                raise InventoryIdentityError("terminal_ref is not published")
            return self._reconcile_current(connection, current, usage=usage)

        return self._write(reconcile)

    def _reconcile_current(
        self,
        connection: sqlite3.Connection,
        current: sqlite3.Row,
        *,
        usage: ProviderUsage | None,
    ) -> ReservationRecord:
        if current["state"] == "settled":
            return _record_from_row(current)
        if current["state"] != "terminal_published":
            raise InvalidBudgetStateTransition(
                f"cannot settle from {current['state']}"
            )
        pricing_body = json.loads(current["frozen_pricing_json"])
        pricing = FrozenPricing(
            currency=pricing_body["currency"],
            input_per_million_tokens=Decimal(
                pricing_body["input_per_million_tokens"]
            ),
            output_per_million_tokens=Decimal(
                pricing_body["output_per_million_tokens"]
            ),
        )
        charge = account_terminal_usage(
            token_upper_bound=int(current["token_upper_bound"]),
            cost_upper_bound=Decimal(current["cost_upper_bound"]),
            pricing=pricing,
            usage=usage,
        )
        usage_json = (
            None if usage is None else json.dumps(asdict(usage), sort_keys=True)
        )
        connection.execute(
            """
            UPDATE reservations
            SET state = 'settled', charged_tokens = ?, cost_estimate = ?,
                usage_missing = ?, usage_json = ?, revision = revision + 1
            WHERE inventory_digest = ? AND inventory_entry_id = ?
            """,
            (
                charge.charged_tokens,
                str(charge.cost_estimate),
                int(charge.usage_missing),
                usage_json,
                current["inventory_digest"],
                current["inventory_entry_id"],
            ),
        )
        return _record_from_row(
            self._select_reservation(
                connection,
                current["inventory_digest"],
                current["inventory_entry_id"],
            )
        )

    def _select_reservation(
        self,
        connection: sqlite3.Connection,
        inventory_digest: str,
        inventory_entry_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT * FROM reservations
            WHERE inventory_digest = ? AND inventory_entry_id = ?
            """,
            (inventory_digest, inventory_entry_id),
        ).fetchone()
        if row is None:
            raise InventoryIdentityError("reservation is not registered")
        return row

    def get_reservation(
        self, inventory_digest: str, inventory_entry_id: str
    ) -> ReservationRecord:
        with self._connect() as connection:
            return _record_from_row(
                self._select_reservation(connection, inventory_digest, inventory_entry_id)
            )

    def list_reservations(self) -> tuple[ReservationRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM reservations ORDER BY inventory_digest, inventory_entry_id"
            ).fetchall()
        return tuple(_record_from_row(row) for row in rows)

    def export_audit_jsonl(self, path: str | Path) -> int:
        """只从已提交 SQLite row 导出；本模块没有 JSONL 导入入口。"""

        rows = self.list_reservations()
        payload = "".join(
            json.dumps(
                {
                    **asdict(row),
                    "cost_upper_bound": str(row.cost_upper_bound),
                    "cost_estimate": (
                        None if row.cost_estimate is None else str(row.cost_estimate)
                    ),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
            for row in rows
        )
        Path(path).write_text(payload, encoding="utf-8")
        return len(rows)


def _record_from_row(row: sqlite3.Row) -> ReservationRecord:
    return ReservationRecord(
        inventory_digest=row["inventory_digest"],
        inventory_entry_id=row["inventory_entry_id"],
        semantic_slot_key=row["semantic_slot_key"],
        state=row["state"],
        token_upper_bound=int(row["token_upper_bound"]),
        cost_upper_bound=Decimal(row["cost_upper_bound"]),
        provider_family=row["provider_family"],
        terminal_ref=row["terminal_ref"],
        terminal_kind=row["terminal_kind"],
        charged_tokens=(
            None if row["charged_tokens"] is None else int(row["charged_tokens"])
        ),
        cost_estimate=(
            None if row["cost_estimate"] is None else Decimal(row["cost_estimate"])
        ),
        usage_missing=(
            None if row["usage_missing"] is None else bool(row["usage_missing"])
        ),
        revision=int(row["revision"]),
    )
