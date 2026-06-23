from dataclasses import replace

from tests.phase2_fixtures import make_client, make_config, make_unit
from tokenshare.core.scheduling import Scheduler
from tokenshare.core.task_graph import TaskGraph
from tokenshare.executors.contracts import ExecutorStatus


def test_scheduler_selects_ready_unit_with_matching_client_capabilities() -> None:
    unit = make_unit(required_capabilities={"executor": "local", "proof": "stub"})
    graph = TaskGraph(task_id="task_demo", units={unit.unit_id: unit}, relations=[])
    scheduler = Scheduler(policy_id="fifo_ready_v1")

    decision = scheduler.select_next(
        graph=graph,
        clients=[
            make_client("client_wrong", capabilities={"executor": "remote"}),
            make_client("client_local", capabilities={"executor": "local", "proof": ["stub"]}),
        ],
        protocol_config=make_config(),
        active_leases_by_unit_id={},
        now="2026-06-08T00:00:00Z",
        decision_id="decision_1",
    )

    assert decision is not None
    assert decision.unit_id == unit.unit_id
    assert decision.client_id == "client_local"
    assert decision.matched_capabilities == ["executor", "proof"]


def test_scheduler_skips_units_with_active_lease_when_shadow_execution_disabled() -> None:
    unit = make_unit()
    graph = TaskGraph(task_id="task_demo", units={unit.unit_id: unit}, relations=[])
    scheduler = Scheduler(policy_id="fifo_ready_v1")

    decision = scheduler.select_next(
        graph=graph,
        clients=[make_client()],
        protocol_config=make_config(),
        active_leases_by_unit_id={unit.unit_id: ["lease_existing"]},
        now="2026-06-08T00:00:00Z",
        decision_id="decision_1",
    )

    assert decision is None


def test_scheduler_fifo_orders_ready_units_by_created_at() -> None:
    newer = replace(
        make_unit(unit_id="unit_newer"),
        created_at="2026-06-08T00:02:00Z",
        updated_at="2026-06-08T00:02:00Z",
    )
    older = replace(
        make_unit(unit_id="unit_older"),
        created_at="2026-06-08T00:01:00Z",
        updated_at="2026-06-08T00:01:00Z",
    )
    graph = TaskGraph(
        task_id="task_demo",
        units={newer.unit_id: newer, older.unit_id: older},
        relations=[],
    )
    scheduler = Scheduler(policy_id="fifo_ready_v1")

    decision = scheduler.select_next(
        graph=graph,
        clients=[make_client()],
        protocol_config=make_config(),
        active_leases_by_unit_id={},
        now="2026-06-08T00:03:00Z",
        decision_id="decision_1",
    )

    assert decision is not None
    assert decision.unit_id == "unit_older"


def test_scheduler_uses_phase3_executor_status_contract() -> None:
    unit = make_unit(required_capabilities={"executor": "local"})
    graph = TaskGraph(task_id="task_demo", units={unit.unit_id: unit}, relations=[])
    scheduler = Scheduler(policy_id="fifo_ready_v1")

    decision = scheduler.select_next(
        graph=graph,
        clients=[
            make_client("client_legacy_ready", status="ready"),
            make_client("client_available", status=ExecutorStatus.AVAILABLE.value),
        ],
        protocol_config=make_config(),
        active_leases_by_unit_id={},
        now="2026-06-08T00:00:00Z",
        decision_id="decision_1",
    )

    assert decision is not None
    assert decision.client_id == "client_available"
