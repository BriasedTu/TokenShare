from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest


def _scheduler_api():
    from tokenshare.local_runtime.logical_scheduler import (
        LogicalSourceLatencyScheduler,
        logical_source_latency_makespan_ms,
    )

    return LogicalSourceLatencyScheduler, logical_source_latency_makespan_ms


def test_logical_one_x_delivery_finishes_at_start_plus_source_latency() -> None:
    scheduler_type, _ = _scheduler_api()
    scheduler = scheduler_type(start_ms=40)

    event = scheduler.schedule_delivery(
        task_id="task-a",
        unit_id="unit-a",
        attempt_ordinal=0,
        source_latency_ms=125,
    )

    assert event.logical_start_ms == 40
    assert event.source_latency_ms == 125
    assert event.logical_time_ms == 165
    assert scheduler.pop_next() == event
    assert scheduler.clock_ms == 165
    with pytest.raises(FrozenInstanceError):
        event.logical_time_ms = 999


def test_w1_and_wk_makespan_match_discrete_event_schedule() -> None:
    _, makespan = _scheduler_api()

    assert makespan((100, 300, 200), worker_count=1) == 600
    assert makespan((100, 300, 200), worker_count=2) == 300


def test_early_stop_retry_deadline_death_and_resume_share_frozen_queue() -> None:
    scheduler_type, _ = _scheduler_api()
    from tokenshare.local_runtime.logical_scheduler import EVENT_PRIORITY_BY_KIND

    scheduler = scheduler_type(start_ms=0)
    specifications = (
        ("requeue", "unit-f", 1),
        ("worker_death", "unit-c", 0),
        ("early_stop", "unit-d", 0),
        ("retry", "unit-e", 1),
        ("lease_deadline", "unit-b", 0),
        ("worker_completion", "unit-a", 0),
    )
    for event_kind, unit_id, attempt_ordinal in specifications:
        scheduler.schedule_event(
            event_kind=event_kind,
            logical_time_ms=220,
            event_priority=EVENT_PRIORITY_BY_KIND[event_kind],
            task_id="task-a",
            unit_id=unit_id,
            attempt_ordinal=attempt_ordinal,
        )

    restored = scheduler_type.from_checkpoint(scheduler.checkpoint())
    popped = tuple(restored.pop_next() for _ in specifications)

    assert [event.event_kind for event in popped] == [
        "worker_completion",
        "lease_deadline",
        "worker_death",
        "early_stop",
        "retry",
        "requeue",
    ]
    assert [event.logical_time_ms for event in popped] == [220] * 6
    assert restored.clock_ms == 220


def test_stable_tie_break_replays_identical_event_order() -> None:
    scheduler_type, _ = _scheduler_api()
    rows = (
        (300, 20, "task-b", "unit-a", 0),
        (300, 10, "task-z", "unit-z", 0),
        (300, 20, "task-a", "unit-z", 0),
        (300, 20, "task-a", "unit-a", 1),
        (300, 20, "task-a", "unit-a", 0),
    )

    def replay_order(input_rows):
        scheduler = scheduler_type(start_ms=0)
        for logical_time_ms, priority, task_id, unit_id, attempt_ordinal in input_rows:
            scheduler.schedule_event(
                event_kind="worker_completion",
                logical_time_ms=logical_time_ms,
                event_priority=priority,
                task_id=task_id,
                unit_id=unit_id,
                attempt_ordinal=attempt_ordinal,
            )
        return tuple(event.ordering_key for event in scheduler.drain())

    expected = tuple(sorted(rows))
    assert replay_order(rows) == expected
    assert replay_order(reversed(rows)) == expected

    duplicate_scheduler = scheduler_type(start_ms=0)
    duplicate_scheduler.schedule_event(
        event_kind="worker_completion",
        logical_time_ms=300,
        event_priority=20,
        task_id="task-a",
        unit_id="unit-a",
        attempt_ordinal=0,
    )
    with pytest.raises(ValueError, match="duplicate logical event ordering key"):
        duplicate_scheduler.schedule_event(
            event_kind="retry",
            logical_time_ms=300,
            event_priority=20,
            task_id="task-a",
            unit_id="unit-a",
            attempt_ordinal=0,
        )


def test_checkpoint_restores_next_completion_sequence() -> None:
    scheduler_type, _ = _scheduler_api()
    scheduler = scheduler_type(start_ms=10)
    first = scheduler.schedule_delivery(
        task_id="task-a",
        unit_id="unit-a",
        attempt_ordinal=0,
        source_latency_ms=10,
    )
    second = scheduler.schedule_delivery(
        task_id="task-a",
        unit_id="unit-b",
        attempt_ordinal=0,
        source_latency_ms=20,
    )
    assert scheduler.pop_next() == first

    checkpoint = scheduler.checkpoint()
    restored = scheduler_type.from_checkpoint(checkpoint)

    assert restored.clock_ms == 20
    assert restored.peek_next() == second
    assert restored.pop_next().sequence == second.sequence
    next_event = restored.schedule_delivery(
        task_id="task-a",
        unit_id="unit-c",
        attempt_ordinal=1,
        source_latency_ms=1,
    )
    assert next_event.sequence == checkpoint.next_sequence
