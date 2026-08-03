"""SQLite WAL 论文 acquisition 预算权威；不承担协议状态或 provider transport。"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict, dataclass
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Literal, Sequence, TypeVar

from tokenshare.executors.response_bank import (
    ResponseBankInventoryRow,
    canonical_inventory_rows,
    response_bank_inventory_digest,
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


class BudgetCategoryPolicyIdentityError(PaperBudgetLedgerError):
    pass


@dataclass(frozen=True, kw_only=True)
class PaperBudgetCategoryPolicy:
    """可选的 call 分类硬门；默认 full-bank ledger 不启用。"""

    policy_id: str
    planned_reservation_limit: int
    ambiguous_reacquisition_limit: int
    combined_call_limit: int
    schema_version: str = "tokenshare.paper_budget_category_policy.v1"

    def __post_init__(self) -> None:
        values = (
            self.planned_reservation_limit,
            self.ambiguous_reacquisition_limit,
            self.combined_call_limit,
        )
        if not self.policy_id or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in values
        ):
            raise ValueError("budget category policy identity and limits are invalid")
        if self.combined_call_limit != (
            self.planned_reservation_limit + self.ambiguous_reacquisition_limit
        ):
            raise ValueError("combined call limit must equal both category limits")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "policy_id": self.policy_id,
            "planned_reservation_limit": self.planned_reservation_limit,
            "ambiguous_reacquisition_limit": self.ambiguous_reacquisition_limit,
            "combined_call_limit": self.combined_call_limit,
        }

    @property
    def policy_digest(self) -> str:
        encoded = json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"sha256:{sha256(encoded).hexdigest()}"


L3_ONLINE_CHECK_CATEGORY_POLICY = PaperBudgetCategoryPolicy(
    policy_id="paper_online_checks_l3",
    planned_reservation_limit=496,
    ambiguous_reacquisition_limit=20,
    combined_call_limit=516,
)


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


@dataclass(frozen=True, kw_only=True)
class ReacquisitionRecord:
    reacquisition_id: str
    inventory_digest: str
    inventory_entry_id: str
    linked_ambiguous_attempt_id: str
    paid_scope_digest: str
    state: BudgetState
    terminal_ref: str | None
    terminal_kind: str | None
    charged_tokens: int | None
    cost_estimate: Decimal | None
    usage_missing: bool | None
    revision: int


class PaperBudgetLedger:
    """只管理 provider acquisition accounting，不映射 ProtocolEngine 终态。"""

    def __init__(
        self,
        path: str | Path,
        *,
        limits: PaperBudgetLimits = L3_SMALL_PAID_BUDGET_LIMITS,
        category_policy: PaperBudgetCategoryPolicy | None = None,
        busy_timeout_ms: int = 100,
        lock_retries: int = 3,
        lock_retry_backoff_seconds: float = 0.01,
    ) -> None:
        if busy_timeout_ms < 1 or lock_retries < 0 or lock_retry_backoff_seconds < 0:
            raise ValueError("lock retry configuration must be bounded and non-negative")
        self.path = Path(path)
        self.limits = limits
        self.category_policy = category_policy
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
                CREATE TABLE IF NOT EXISTS reacquisitions (
                    reacquisition_id TEXT PRIMARY KEY,
                    inventory_digest TEXT NOT NULL,
                    inventory_entry_id TEXT NOT NULL,
                    linked_ambiguous_attempt_id TEXT NOT NULL,
                    paid_scope_digest TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN (
                        'reserved', 'dispatch_intent', 'ambiguous',
                        'terminal_published', 'settled'
                    )),
                    call_count INTEGER NOT NULL CHECK (call_count = 1),
                    token_upper_bound INTEGER NOT NULL CHECK (token_upper_bound > 0),
                    cost_upper_bound TEXT NOT NULL,
                    provider_family TEXT NOT NULL,
                    frozen_pricing_json TEXT NOT NULL,
                    terminal_ref TEXT UNIQUE,
                    terminal_kind TEXT CHECK (
                        terminal_kind IS NULL OR terminal_kind IN ('success', 'provider_failure')
                    ),
                    charged_tokens INTEGER,
                    cost_estimate TEXT,
                    usage_missing INTEGER,
                    usage_json TEXT,
                    revision INTEGER NOT NULL DEFAULT 0,
                    UNIQUE (inventory_digest, inventory_entry_id),
                    FOREIGN KEY (inventory_digest, inventory_entry_id)
                        REFERENCES inventory_rows(inventory_digest, inventory_entry_id)
                );
                CREATE TABLE IF NOT EXISTS budget_category_policy (
                    singleton_key INTEGER PRIMARY KEY CHECK (singleton_key = 1),
                    schema_version TEXT NOT NULL,
                    policy_id TEXT NOT NULL,
                    policy_digest TEXT NOT NULL,
                    policy_json TEXT NOT NULL
                );
                """
            )
            self._bind_category_policy(connection)

        self._write(create)

    def _bind_category_policy(self, connection: sqlite3.Connection) -> None:
        stored = connection.execute(
            "SELECT * FROM budget_category_policy WHERE singleton_key = 1"
        ).fetchone()
        policy = self.category_policy
        if policy is None:
            if stored is not None:
                raise BudgetCategoryPolicyIdentityError(
                    "budget category policy presence mismatch"
                )
            return
        body = policy.to_dict()
        canonical_json = json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if stored is None:
            existing_calls = sum(
                int(
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                )
                for table in ("reservations", "reacquisitions")
            )
            if existing_calls:
                raise BudgetCategoryPolicyIdentityError(
                    "budget category policy presence mismatch"
                )
            connection.execute(
                """
                INSERT INTO budget_category_policy(
                    singleton_key, schema_version, policy_id, policy_digest, policy_json
                ) VALUES (1, ?, ?, ?, ?)
                """,
                (
                    policy.schema_version,
                    policy.policy_id,
                    policy.policy_digest,
                    canonical_json,
                ),
            )
            return
        if (
            stored["schema_version"] != policy.schema_version
            or stored["policy_id"] != policy.policy_id
            or stored["policy_digest"] != policy.policy_digest
            or stored["policy_json"] != canonical_json
        ):
            raise BudgetCategoryPolicyIdentityError(
                "budget category policy drift detected"
            )

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
        canonical_rows = canonical_inventory_rows(rows)
        normalized: list[ResponseBankInventoryRow] = []
        for row in canonical_rows:
            try:
                normalized.append(ResponseBankInventoryRow.from_dict(row.to_dict()))
            except ValueError as exc:
                raise InventoryIdentityError(str(exc)) from exc
        expected_digest = response_bank_inventory_digest(normalized)
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
        canonical_rows = canonical_inventory_rows(rows)
        if response_bank_inventory_digest(canonical_rows) != inventory_digest:
            raise InventoryIdentityError("inventory_digest does not match committed rows")
        return canonical_rows

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

            self._assert_category_capacity(connection, category="primary")
            calls, tokens, cost_total = self._budget_totals(connection)
            next_calls = calls + 1
            next_tokens = tokens + request.token_upper_bound
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

    def release_reserved(
        self, inventory_digest: str, inventory_entry_id: str
    ) -> bool:
        """恢复只可释放尚未产生 dispatch intent 的安全 reservation。"""

        def release(connection: sqlite3.Connection) -> bool:
            current = self._select_reservation(
                connection, inventory_digest, inventory_entry_id
            )
            if current["state"] != "reserved":
                return False
            connection.execute(
                """
                DELETE FROM reservations
                WHERE inventory_digest = ? AND inventory_entry_id = ? AND state = 'reserved'
                """,
                (inventory_digest, inventory_entry_id),
            )
            return True

        return self._write(release)

    def reserve_reacquisition(
        self,
        request: ReservationRequest,
        *,
        reacquisition_id: str,
        linked_ambiguous_attempt_id: str,
        paid_scope_digest: str,
    ) -> ReacquisitionRecord:
        """为 ambiguous attempt 仅建立一笔显式、付费 scope 绑定的重取预留。"""

        if not reacquisition_id or not linked_ambiguous_attempt_id or not paid_scope_digest:
            raise ValueError("reacquisition identity and paid scope must be non-empty")

        def reserve(connection: sqlite3.Connection) -> ReacquisitionRecord:
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
            primary = self._select_reservation(
                connection, request.inventory_digest, request.inventory_entry_id
            )
            if primary["state"] != "ambiguous":
                raise InvalidBudgetStateTransition(
                    "reacquisition requires an ambiguous primary reservation"
                )
            existing = connection.execute(
                """
                SELECT * FROM reacquisitions
                WHERE inventory_digest = ? AND inventory_entry_id = ?
                """,
                (request.inventory_digest, request.inventory_entry_id),
            ).fetchone()
            if existing is not None:
                if (
                    existing["reacquisition_id"] != reacquisition_id
                    or existing["linked_ambiguous_attempt_id"]
                    != linked_ambiguous_attempt_id
                    or existing["paid_scope_digest"] != paid_scope_digest
                ):
                    raise InventoryIdentityError("conflicting reacquisition identity")
                return _reacquisition_record_from_row(existing)
            self._assert_category_capacity(connection, category="reacquisition")
            calls, tokens, cost_total = self._budget_totals(connection)
            next_calls = calls + 1
            next_tokens = tokens + request.token_upper_bound
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
                INSERT INTO reacquisitions(
                    reacquisition_id, inventory_digest, inventory_entry_id,
                    linked_ambiguous_attempt_id, paid_scope_digest, state,
                    call_count, token_upper_bound, cost_upper_bound,
                    provider_family, frozen_pricing_json
                ) VALUES (?, ?, ?, ?, ?, 'reserved', 1, ?, ?, ?, ?)
                """,
                (
                    reacquisition_id,
                    request.inventory_digest,
                    request.inventory_entry_id,
                    linked_ambiguous_attempt_id,
                    paid_scope_digest,
                    request.token_upper_bound,
                    str(request.cost_upper_bound),
                    request.provider_family,
                    pricing_json,
                ),
            )
            row = connection.execute(
                "SELECT * FROM reacquisitions WHERE reacquisition_id = ?",
                (reacquisition_id,),
            ).fetchone()
            assert row is not None
            return _reacquisition_record_from_row(row)

        return self._write(reserve)

    def mark_reacquisition_dispatch_intent(
        self, reacquisition_id: str
    ) -> ReacquisitionRecord:
        return self._transition_reacquisition(
            reacquisition_id, expected={"reserved"}, target="dispatch_intent"
        )

    def mark_reacquisition_ambiguous(
        self, reacquisition_id: str
    ) -> ReacquisitionRecord:
        return self._transition_reacquisition(
            reacquisition_id,
            expected={"dispatch_intent"},
            target="ambiguous",
        )

    def publish_reacquisition_terminal(
        self,
        reacquisition_id: str,
        *,
        terminal_ref: str,
        terminal_kind: TerminalKind,
    ) -> ReacquisitionRecord:
        if not terminal_ref or terminal_kind not in {"success", "provider_failure"}:
            raise ValueError("terminal publication requires a ref and supported kind")

        def publish(connection: sqlite3.Connection) -> ReacquisitionRecord:
            current = self._select_reacquisition(connection, reacquisition_id)
            if current["state"] in {"terminal_published", "settled"}:
                if (
                    current["terminal_ref"] == terminal_ref
                    and current["terminal_kind"] == terminal_kind
                ):
                    return _reacquisition_record_from_row(current)
                raise InvalidBudgetStateTransition("conflicting terminal publication")
            if current["state"] not in {"dispatch_intent", "ambiguous"}:
                raise InvalidBudgetStateTransition(
                    f"cannot publish terminal from {current['state']}"
                )
            connection.execute(
                """
                UPDATE reacquisitions
                SET state = 'terminal_published', terminal_ref = ?, terminal_kind = ?,
                    revision = revision + 1
                WHERE reacquisition_id = ?
                """,
                (terminal_ref, terminal_kind, reacquisition_id),
            )
            return _reacquisition_record_from_row(
                self._select_reacquisition(connection, reacquisition_id)
            )

        return self._write(publish)

    def reconcile_reacquisition(
        self, reacquisition_id: str, *, usage: ProviderUsage | None
    ) -> ReacquisitionRecord:
        def reconcile(connection: sqlite3.Connection) -> ReacquisitionRecord:
            current = self._select_reacquisition(connection, reacquisition_id)
            if current["state"] == "settled":
                return _reacquisition_record_from_row(current)
            if current["state"] != "terminal_published":
                raise InvalidBudgetStateTransition(
                    f"cannot settle reacquisition from {current['state']}"
                )
            self._settle_row(
                connection, table="reacquisitions", key_field="reacquisition_id",
                key_value=reacquisition_id, current=current, usage=usage
            )
            return _reacquisition_record_from_row(
                self._select_reacquisition(connection, reacquisition_id)
            )

        return self._write(reconcile)

    def _transition_reacquisition(
        self, reacquisition_id: str, *, expected: set[str], target: BudgetState
    ) -> ReacquisitionRecord:
        def transition(connection: sqlite3.Connection) -> ReacquisitionRecord:
            current = self._select_reacquisition(connection, reacquisition_id)
            if current["state"] not in expected:
                raise InvalidBudgetStateTransition(
                    f"cannot transition {current['state']} to {target}"
                )
            connection.execute(
                """
                UPDATE reacquisitions SET state = ?, revision = revision + 1
                WHERE reacquisition_id = ?
                """,
                (target, reacquisition_id),
            )
            return _reacquisition_record_from_row(
                self._select_reacquisition(connection, reacquisition_id)
            )

        return self._write(transition)

    @staticmethod
    def _select_reacquisition(
        connection: sqlite3.Connection, reacquisition_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM reacquisitions WHERE reacquisition_id = ?",
            (reacquisition_id,),
        ).fetchone()
        if row is None:
            raise InventoryIdentityError("reacquisition is not registered")
        return row

    def list_reacquisitions(self) -> tuple[ReacquisitionRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM reacquisitions ORDER BY reacquisition_id"
            ).fetchall()
        return tuple(_reacquisition_record_from_row(row) for row in rows)

    def _budget_totals(
        self, connection: sqlite3.Connection
    ) -> tuple[int, int, Decimal]:
        rows = connection.execute(
            """
            SELECT state, call_count, token_upper_bound, cost_upper_bound,
                   charged_tokens, cost_estimate FROM reservations
            UNION ALL
            SELECT state, call_count, token_upper_bound, cost_upper_bound,
                   charged_tokens, cost_estimate FROM reacquisitions
            """
        ).fetchall()
        calls = sum(int(row["call_count"]) for row in rows)
        tokens = sum(
            int(row["charged_tokens"])
            if row["state"] == "settled"
            else int(row["token_upper_bound"])
            for row in rows
        )
        cost = sum(
            (
                Decimal(row["cost_estimate"])
                if row["state"] == "settled"
                else Decimal(row["cost_upper_bound"])
            )
            for row in rows
        )
        return calls, tokens, cost

    def _assert_category_capacity(
        self,
        connection: sqlite3.Connection,
        *,
        category: Literal["primary", "reacquisition"],
    ) -> None:
        policy = self.category_policy
        if policy is None:
            return
        primary_count = int(
            connection.execute("SELECT COUNT(*) FROM reservations").fetchone()[0]
        )
        reacquisition_count = int(
            connection.execute("SELECT COUNT(*) FROM reacquisitions").fetchone()[0]
        )
        next_primary = primary_count + int(category == "primary")
        next_reacquisition = reacquisition_count + int(category == "reacquisition")
        if next_primary > policy.planned_reservation_limit:
            raise BudgetExceededError(
                "planned reservation category hard limit exceeded"
            )
        if next_reacquisition > policy.ambiguous_reacquisition_limit:
            raise BudgetExceededError(
                "ambiguous reacquisition category hard limit exceeded"
            )
        if next_primary + next_reacquisition > policy.combined_call_limit:
            raise BudgetExceededError("combined category call hard limit exceeded")

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

    @staticmethod
    def _settle_row(
        connection: sqlite3.Connection,
        *,
        table: str,
        key_field: str,
        key_value: str,
        current: sqlite3.Row,
        usage: ProviderUsage | None,
    ) -> None:
        if (table, key_field) != ("reacquisitions", "reacquisition_id"):
            raise ValueError("unsupported settlement table")
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
            f"""
            UPDATE {table}
            SET state = 'settled', charged_tokens = ?, cost_estimate = ?,
                usage_missing = ?, usage_json = ?, revision = revision + 1
            WHERE {key_field} = ?
            """,
            (
                charge.charged_tokens,
                str(charge.cost_estimate),
                int(charge.usage_missing),
                usage_json,
                key_value,
            ),
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


def _reacquisition_record_from_row(row: sqlite3.Row) -> ReacquisitionRecord:
    return ReacquisitionRecord(
        reacquisition_id=row["reacquisition_id"],
        inventory_digest=row["inventory_digest"],
        inventory_entry_id=row["inventory_entry_id"],
        linked_ambiguous_attempt_id=row["linked_ambiguous_attempt_id"],
        paid_scope_digest=row["paid_scope_digest"],
        state=row["state"],
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
