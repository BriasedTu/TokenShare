from tests.phase2_fixtures import make_client, make_config, make_unit
from tokenshare.core.scheduling import Scheduler
from tokenshare.core.task_graph import TaskGraph


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
