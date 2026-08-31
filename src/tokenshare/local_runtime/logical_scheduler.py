"""确定性的 source-latency 离散事件调度器。

本模块只管理逻辑时间与事件全序，不创建或修改任何协议事实。协议状态变更仍由
``ProtocolRunCoordinator`` 在弹出事件后委托给 ``ProtocolEngine``。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import heapq
from typing import Iterable, Literal, Mapping


LOGICAL_SOURCE_LATENCY_1X = "logical_source_latency_1x"
ONLINE_REAL_TIME = "online_real_time"
WORKER_COMPLETION_PRIORITY = 10
LEASE_DEADLINE_PRIORITY = 20
WORKER_DEATH_PRIORITY = 30
EARLY_STOP_PRIORITY = 40
RETRY_PRIORITY = 50
REQUEUE_PRIORITY = 60

EVENT_PRIORITY_BY_KIND = {
    "worker_completion": WORKER_COMPLETION_PRIORITY,
    "lease_deadline": LEASE_DEADLINE_PRIORITY,
    "worker_death": WORKER_DEATH_PRIORITY,
    "early_stop": EARLY_STOP_PRIORITY,
    "retry": RETRY_PRIORITY,
    "requeue": REQUEUE_PRIORITY,
}

LogicalEventKind = Literal[
    "worker_completion",
    "early_stop",
    "lease_deadline",
    "worker_death",
    "retry",
    "requeue",
]

_EVENT_KINDS = frozenset(
    {
        "worker_completion",
        "early_stop",
        "lease_deadline",
        "worker_death",
        "retry",
        "requeue",
    }
)
_EVENT_FIELDS = frozenset(
    {
        "schema_version",
        "event_kind",
        "logical_time_ms",
        "event_priority",
        "task_id",
        "unit_id",
        "attempt_ordinal",
        "sequence",
        "logical_start_ms",
        "source_latency_ms",
    }
)
_CHECKPOINT_FIELDS = frozenset(
    {
        "schema_version",
        "clock_ms",
        "next_sequence",
        "pending_events",
        "used_ordering_keys",
        "wall_clock_origin",
        "wall_clock_origin_logical_ms",
    }
)


@dataclass(frozen=True, kw_only=True)
class LogicalScheduledEvent:
    """一个不可变事件；其排序键严格等于 EPD-027 冻结五元组。"""

    event_kind: LogicalEventKind
    logical_time_ms: int
    event_priority: int
    task_id: str
    unit_id: str
    attempt_ordinal: int
    sequence: int
    logical_start_ms: int | None = None
    source_latency_ms: int | None = None
    schema_version: str = "tokenshare.logical_scheduled_event.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "tokenshare.logical_scheduled_event.v1":
            raise ValueError("unsupported logical scheduled event schema")
        if self.event_kind not in _EVENT_KINDS:
            raise ValueError(f"unsupported logical event kind: {self.event_kind}")
        _require_non_negative_int("logical_time_ms", self.logical_time_ms)
        _require_non_negative_int("event_priority", self.event_priority)
        _require_non_empty_string("task_id", self.task_id)
        _require_non_empty_string("unit_id", self.unit_id)
        _require_non_negative_int("attempt_ordinal", self.attempt_ordinal)
        _require_non_negative_int("sequence", self.sequence)
        delivery_values = (self.logical_start_ms, self.source_latency_ms)
        if any(value is not None for value in delivery_values):
            if any(value is None for value in delivery_values):
                raise ValueError(
                    "logical delivery requires both start and source latency"
                )
            _require_non_negative_int("logical_start_ms", self.logical_start_ms)
            _require_non_negative_int("source_latency_ms", self.source_latency_ms)
            if self.logical_time_ms != self.logical_start_ms + self.source_latency_ms:
                raise ValueError(
                    "logical finish must equal logical start plus source latency"
                )

    @property
    def ordering_key(self) -> tuple[int, int, str, str, int]:
        return (
            self.logical_time_ms,
            self.event_priority,
            self.task_id,
            self.unit_id,
            self.attempt_ordinal,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "event_kind": self.event_kind,
            "logical_time_ms": self.logical_time_ms,
            "event_priority": self.event_priority,
            "task_id": self.task_id,
            "unit_id": self.unit_id,
            "attempt_ordinal": self.attempt_ordinal,
            "sequence": self.sequence,
            "logical_start_ms": self.logical_start_ms,
            "source_latency_ms": self.source_latency_ms,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "LogicalScheduledEvent":
        _require_exact_fields(value, _EVENT_FIELDS, "logical scheduled event")
        return cls(**dict(value))  # type: ignore[arg-type]


@dataclass(frozen=True, kw_only=True)
class LogicalSchedulerCheckpoint:
    """恢复 queue、clock 与 sequence 所需的闭合快照。"""

    clock_ms: int
    next_sequence: int
    pending_events: tuple[LogicalScheduledEvent, ...]
    used_ordering_keys: tuple[tuple[int, int, str, str, int], ...]
    wall_clock_origin: str | None = None
    wall_clock_origin_logical_ms: int | None = None
    schema_version: str = "tokenshare.logical_scheduler_checkpoint.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "tokenshare.logical_scheduler_checkpoint.v1":
            raise ValueError("unsupported logical scheduler checkpoint schema")
        _require_non_negative_int("clock_ms", self.clock_ms)
        _require_non_negative_int("next_sequence", self.next_sequence)
        if tuple(sorted(self.pending_events, key=lambda event: event.ordering_key)) != (
            self.pending_events
        ):
            raise ValueError("checkpoint pending_events must use canonical order")
        pending_keys = tuple(event.ordering_key for event in self.pending_events)
        if len(set(pending_keys)) != len(pending_keys):
            raise ValueError("checkpoint contains duplicate pending ordering key")
        if len(set(self.used_ordering_keys)) != len(self.used_ordering_keys):
            raise ValueError("checkpoint contains duplicate used ordering key")
        if not set(pending_keys).issubset(self.used_ordering_keys):
            raise ValueError("checkpoint pending keys must be registered as used")
        if any(event.logical_time_ms < self.clock_ms for event in self.pending_events):
            raise ValueError("checkpoint contains an event before its clock")
        if any(event.sequence >= self.next_sequence for event in self.pending_events):
            raise ValueError("checkpoint next_sequence does not follow pending events")
        if (self.wall_clock_origin is None) != (
            self.wall_clock_origin_logical_ms is None
        ):
            raise ValueError("checkpoint wall-clock origin fields must be paired")
        if self.wall_clock_origin is not None:
            _parse_utc(self.wall_clock_origin)
            _require_non_negative_int(
                "wall_clock_origin_logical_ms",
                self.wall_clock_origin_logical_ms,
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "clock_ms": self.clock_ms,
            "next_sequence": self.next_sequence,
            "pending_events": [event.to_dict() for event in self.pending_events],
            "used_ordering_keys": [list(key) for key in self.used_ordering_keys],
            "wall_clock_origin": self.wall_clock_origin,
            "wall_clock_origin_logical_ms": self.wall_clock_origin_logical_ms,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "LogicalSchedulerCheckpoint":
        _require_exact_fields(value, _CHECKPOINT_FIELDS, "logical scheduler checkpoint")
        pending = value["pending_events"]
        used = value["used_ordering_keys"]
        if not isinstance(pending, list) or not isinstance(used, list):
            raise TypeError("checkpoint queue and used keys must be arrays")
        if any(not isinstance(item, Mapping) for item in pending):
            raise TypeError("checkpoint pending event must be an object")
        return cls(
            schema_version=value["schema_version"],  # type: ignore[arg-type]
            clock_ms=value["clock_ms"],  # type: ignore[arg-type]
            next_sequence=value["next_sequence"],  # type: ignore[arg-type]
            pending_events=tuple(
                LogicalScheduledEvent.from_dict(item)
                for item in pending
            ),
            used_ordering_keys=tuple(_ordering_key_from_json(item) for item in used),
            wall_clock_origin=value["wall_clock_origin"],  # type: ignore[arg-type]
            wall_clock_origin_logical_ms=value[
                "wall_clock_origin_logical_ms"
            ],  # type: ignore[arg-type]
        )


class LogicalSourceLatencyScheduler:
    """按冻结全序推进的单进程确定性离散事件 queue。"""

    def __init__(self, *, start_ms: int = 0) -> None:
        _require_non_negative_int("start_ms", start_ms)
        self._clock_ms = start_ms
        self._next_sequence = 0
        self._queue: list[
            tuple[tuple[int, int, str, str, int], LogicalScheduledEvent]
        ] = []
        self._used_ordering_keys: set[tuple[int, int, str, str, int]] = set()
        self._pop_history: list[LogicalScheduledEvent] = []
        self._wall_clock_origin: str | None = None
        self._wall_clock_origin_logical_ms: int | None = None

    @property
    def clock_ms(self) -> int:
        return self._clock_ms

    @property
    def next_sequence(self) -> int:
        return self._next_sequence

    @property
    def pending_count(self) -> int:
        return len(self._queue)

    @property
    def pop_history(self) -> tuple[LogicalScheduledEvent, ...]:
        return tuple(self._pop_history)

    @property
    def has_wall_clock_origin(self) -> bool:
        return self._wall_clock_origin is not None

    def bind_wall_clock_origin(self, timestamp: str) -> None:
        parsed = _parse_utc(timestamp)
        canonical = parsed.isoformat().replace("+00:00", "Z")
        if self._wall_clock_origin is None:
            self._wall_clock_origin = canonical
            self._wall_clock_origin_logical_ms = self._clock_ms
            return
        if self._wall_clock_origin != canonical:
            raise ValueError("logical scheduler wall-clock origin is already bound")

    def now_timestamp(self) -> str:
        if (
            self._wall_clock_origin is None
            or self._wall_clock_origin_logical_ms is None
        ):
            raise RuntimeError("logical scheduler wall-clock origin is not bound")
        value = _parse_utc(self._wall_clock_origin) + timedelta(
            milliseconds=self._clock_ms - self._wall_clock_origin_logical_ms
        )
        return value.isoformat().replace("+00:00", "Z")

    def schedule_delivery(
        self,
        *,
        task_id: str,
        unit_id: str,
        attempt_ordinal: int,
        source_latency_ms: int,
        event_priority: int = WORKER_COMPLETION_PRIORITY,
        logical_start_ms: int | None = None,
        event_kind: LogicalEventKind = "worker_completion",
    ) -> LogicalScheduledEvent:
        start_ms = self._clock_ms if logical_start_ms is None else logical_start_ms
        _require_non_negative_int("logical_start_ms", start_ms)
        _require_non_negative_int("source_latency_ms", source_latency_ms)
        if start_ms < self._clock_ms:
            raise ValueError("logical delivery cannot start before current clock")
        return self._schedule(
            event_kind=event_kind,
            logical_time_ms=start_ms + source_latency_ms,
            event_priority=event_priority,
            task_id=task_id,
            unit_id=unit_id,
            attempt_ordinal=attempt_ordinal,
            logical_start_ms=start_ms,
            source_latency_ms=source_latency_ms,
        )

    def schedule_event(
        self,
        *,
        event_kind: LogicalEventKind,
        logical_time_ms: int,
        event_priority: int,
        task_id: str,
        unit_id: str,
        attempt_ordinal: int,
    ) -> LogicalScheduledEvent:
        return self._schedule(
            event_kind=event_kind,
            logical_time_ms=logical_time_ms,
            event_priority=event_priority,
            task_id=task_id,
            unit_id=unit_id,
            attempt_ordinal=attempt_ordinal,
            logical_start_ms=None,
            source_latency_ms=None,
        )

    def _schedule(
        self,
        *,
        event_kind: LogicalEventKind,
        logical_time_ms: int,
        event_priority: int,
        task_id: str,
        unit_id: str,
        attempt_ordinal: int,
        logical_start_ms: int | None,
        source_latency_ms: int | None,
    ) -> LogicalScheduledEvent:
        if logical_time_ms < self._clock_ms:
            raise ValueError("logical event cannot be scheduled before current clock")
        event = LogicalScheduledEvent(
            event_kind=event_kind,
            logical_time_ms=logical_time_ms,
            event_priority=event_priority,
            task_id=task_id,
            unit_id=unit_id,
            attempt_ordinal=attempt_ordinal,
            sequence=self._next_sequence,
            logical_start_ms=logical_start_ms,
            source_latency_ms=source_latency_ms,
        )
        if event.ordering_key in self._used_ordering_keys:
            raise ValueError("duplicate logical event ordering key")
        self._next_sequence += 1
        self._used_ordering_keys.add(event.ordering_key)
        heapq.heappush(self._queue, (event.ordering_key, event))
        return event

    def peek_next(self) -> LogicalScheduledEvent:
        if not self._queue:
            raise IndexError("logical scheduler queue is empty")
        return self._queue[0][1]

    def pop_next(self) -> LogicalScheduledEvent:
        if not self._queue:
            raise IndexError("logical scheduler queue is empty")
        _, event = heapq.heappop(self._queue)
        if event.logical_time_ms < self._clock_ms:
            raise RuntimeError("logical scheduler clock would move backwards")
        self._clock_ms = event.logical_time_ms
        self._pop_history.append(event)
        return event

    def drain(self) -> tuple[LogicalScheduledEvent, ...]:
        return tuple(self.pop_next() for _ in range(len(self._queue)))

    def checkpoint(self) -> LogicalSchedulerCheckpoint:
        return LogicalSchedulerCheckpoint(
            clock_ms=self._clock_ms,
            next_sequence=self._next_sequence,
            pending_events=tuple(
                event for _, event in sorted(self._queue, key=lambda item: item[0])
            ),
            used_ordering_keys=tuple(sorted(self._used_ordering_keys)),
            wall_clock_origin=self._wall_clock_origin,
            wall_clock_origin_logical_ms=self._wall_clock_origin_logical_ms,
        )

    @classmethod
    def from_checkpoint(
        cls,
        checkpoint: LogicalSchedulerCheckpoint | Mapping[str, object],
    ) -> "LogicalSourceLatencyScheduler":
        restored = (
            checkpoint
            if isinstance(checkpoint, LogicalSchedulerCheckpoint)
            else LogicalSchedulerCheckpoint.from_dict(checkpoint)
        )
        scheduler = cls(start_ms=restored.clock_ms)
        scheduler._next_sequence = restored.next_sequence
        scheduler._used_ordering_keys = set(restored.used_ordering_keys)
        scheduler._wall_clock_origin = restored.wall_clock_origin
        scheduler._wall_clock_origin_logical_ms = (
            restored.wall_clock_origin_logical_ms
        )
        for event in restored.pending_events:
            heapq.heappush(scheduler._queue, (event.ordering_key, event))
        return scheduler


def logical_source_latency_makespan_ms(
    source_latencies_ms: Iterable[int],
    *,
    worker_count: int,
) -> int:
    """按冻结输入顺序进行 list scheduling，不读取 CPU 执行时长。"""

    if isinstance(worker_count, bool) or not isinstance(worker_count, int):
        raise TypeError("worker_count must be an integer")
    if worker_count < 1:
        raise ValueError("worker_count must be positive")
    latencies = tuple(source_latencies_ms)
    for latency in latencies:
        _require_non_negative_int("source_latency_ms", latency)
    if not latencies:
        return 0
    workers = [(0, worker_id) for worker_id in range(worker_count)]
    heapq.heapify(workers)
    makespan = 0
    for latency in latencies:
        available_at, worker_id = heapq.heappop(workers)
        finished_at = available_at + latency
        makespan = max(makespan, finished_at)
        heapq.heappush(workers, (finished_at, worker_id))
    return makespan


def _ordering_key_from_json(value: object) -> tuple[int, int, str, str, int]:
    if not isinstance(value, list) or len(value) != 5:
        raise TypeError("logical ordering key must be a five-item array")
    logical_time_ms, event_priority, task_id, unit_id, attempt_ordinal = value
    _require_non_negative_int("logical_time_ms", logical_time_ms)
    _require_non_negative_int("event_priority", event_priority)
    _require_non_empty_string("task_id", task_id)
    _require_non_empty_string("unit_id", unit_id)
    _require_non_negative_int("attempt_ordinal", attempt_ordinal)
    return logical_time_ms, event_priority, task_id, unit_id, attempt_ordinal


def _require_exact_fields(
    value: Mapping[str, object],
    expected: frozenset[str],
    label: str,
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"{label} fields mismatch: missing={missing}, extra={extra}")


def _require_non_negative_int(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")


def _require_non_empty_string(field_name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")


def _parse_utc(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("wall-clock origin must be a non-empty timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("wall-clock origin must be UTC")
    return parsed.astimezone(UTC)
