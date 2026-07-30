from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tokenshare.core.models import ProtocolConfig, TaskState, TaskUnit
from tokenshare.core.task_graph import TaskGraph
from tokenshare.experiments.factorization_paper_adapter import (
    ScriptedFactorizationRangeTransport,
    run_factorization_paper_case,
)
from tokenshare.experiments.paper_factorization_catalog import (
    generate_factorization_paper_cases,
)
from tokenshare.experiments.paper_models import PaperExperimentCondition
from tokenshare.experiments.paper_models import PaperTaskStatus
from tokenshare.local_runtime import ProtocolRunCoordinator
from tokenshare.protocol_engine import ProtocolEngine
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType


def test_factorization_full_paper_path_projects_engine_lifecycle(
    tmp_path: Path,
) -> None:
    case = generate_factorization_paper_cases()[0]
    condition = PaperExperimentCondition(
        experiment_id="exp1_real_ai_feasibility",
        condition_id="factorization_runtime_full",
        domain="factorization",
        difficulty=str(case["difficulty"]),
        worker_count=1,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=1,
        catalog_digest=f"sha256:{'1' * 64}",
    )

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
        entry_id="factorization_paper_scripted",
    )

    ledger = EventLedger(
        Path(result.output_root)
        / "events"
        / f"paper_factorization_{case['case_id']}.jsonl"
    )
    events = ledger.read_all()
    child_ids = {
        event.payload["task_unit"]["unit_id"]
        for event in events
        if event.event_type == EventType.TASK_UNIT_CREATED
        and event.payload["task_unit"]["unit_type"] == "factor_search_range"
    }
    assert len(child_ids) == result.split_summary["range_child_count"]
    assert len(result.task_result.event_refs) == len(events)
    scheduled_child_ids = {
        event.payload["lease"]["unit_id"]
        for event in events
        if event.event_type == EventType.LEASE_STATE_CHANGED
        and event.payload.get("scheduling_decision")
        and event.payload.get("lease", {}).get("unit_id") in child_ids
    }
    assert scheduled_child_ids <= child_ids
    assert len(scheduled_child_ids) == result.task_result.provider_attempt_count
    for child_id in scheduled_child_ids:
        assert any(
            event.event_type == EventType.LEASE_STATE_CHANGED
            and event.payload.get("lease", {}).get("unit_id") == child_id
            and event.payload.get("scheduling_decision", {}).get("unit_id") == child_id
            for event in events
        )
        assert {
            event.event_type
            for event in events
            if event.payload.get("unit_id") == child_id
        }.issuperset(
            {
                EventType.EXECUTION_REQUEST_RECORDED,
                EventType.EXECUTION_SUBMISSION_RECORDED,
                EventType.VERIFICATION_RECORDED,
                EventType.CANONICAL_OUTPUTS_BOUND,
            }
        )
    assert any(event.event_type == EventType.MERGE_RECORDED for event in events)
    assert any(event.event_type == EventType.SETTLEMENT_RECORDED for event in events)
    assert result.eligibility_report.paper_eligible is False


def test_factorization_full_failure_projects_engine_terminal_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    case = generate_factorization_paper_cases()[0]
    condition = PaperExperimentCondition(
        experiment_id="exp1_real_ai_feasibility",
        condition_id="factorization_runtime_failure",
        domain="factorization",
        difficulty=str(case["difficulty"]),
        worker_count=1,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=1,
        catalog_digest=f"sha256:{'1' * 64}",
    )
    runtime_results = []
    original_run_root = ProtocolRunCoordinator.run_root

    def capture_runtime_result(self, request):
        runtime_result = original_run_root(self, request)
        runtime_results.append(runtime_result)
        return runtime_result

    monkeypatch.setattr(
        ProtocolRunCoordinator,
        "run_root",
        capture_runtime_result,
    )

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=ScriptedFactorizationRangeTransport(
            force_false_negative_child_indices={0, 1, 2},
        ),
        real_transport=False,
        entry_id="factorization_paper_scripted",
    )

    assert len(runtime_results) == 1
    runtime_result = runtime_results[0]
    assert runtime_result.status == "failed"
    ledger = EventLedger(
        Path(result.output_root)
        / "events"
        / f"paper_factorization_{case['case_id']}.jsonl"
    )
    root_failure = next(
        event
        for event in ledger.read_all()
        if event.event_type == EventType.TASK_UNIT_STATE_CHANGED
        and event.object_id == runtime_result.root_unit_id
        and event.payload["task_unit"]["state"] == "Failed"
    )
    assert root_failure.payload["task_unit_state_change"]["trigger"] == (
        "terminal_child_failure"
    )
    assert result.run_evidence["protocol_runtime"]["status"] == "failed"
    assert result.task_result.root_status == PaperTaskStatus.FAILED


def test_parent_failure_rejects_stale_child_failure_event(tmp_path: Path) -> None:
    ledger = EventLedger(tmp_path / "events.jsonl")
    config = ProtocolConfig.default(
        config_id="parent_failure_event_test",
        artifact_store_uri="file://artifacts",
        event_log_uri="file://events.jsonl",
    )
    engine = ProtocolEngine(
        event_ledger=ledger,
        protocol_config=config,
        artifact_store=ArtifactStore(tmp_path),
    )
    common = {
        "task_id": "task_parent_failure",
        "input_refs": {},
        "canonical_output_refs": {},
        "required_capabilities": {},
        "weight": 1.0,
        "budget_limit": None,
        "deadline": None,
        "plugin_payload": {},
        "metadata": {},
        "created_at": "2026-07-22T00:00:00Z",
        "updated_at": "2026-07-22T00:00:00Z",
    }
    parent = TaskUnit(
        unit_id="parent",
        parent_unit_id=None,
        depth=0,
        unit_type="root",
        state=TaskState.PROCESSING,
        **common,
    )
    child = TaskUnit(
        unit_id="child",
        parent_unit_id=parent.unit_id,
        depth=1,
        unit_type="factor_search_range",
        state=TaskState.FAILED,
        **common,
    )
    graph = TaskGraph(
        task_id=parent.task_id,
        units={parent.unit_id: parent, child.unit_id: child},
    )
    recorded = ledger.append(
        event_type=EventType.TASK_UNIT_STATE_CHANGED,
        object_type="TaskUnit",
        object_id=child.unit_id,
        task_id=child.task_id,
        actor={"kind": "protocol_engine"},
        idempotency_key="child_failed",
        payload={"task_unit": child.to_dict()},
        occurred_at="2026-07-22T00:00:00Z",
    )
    stale = replace(recorded, event_hash=f"sha256:{'0' * 64}")

    with pytest.raises(ValueError, match="recorded child failure event"):
        engine.record_parent_failure(
            parent_unit=parent,
            failed_child=child,
            graph=graph,
            child_failure_event=stale,
            now="2026-07-22T00:00:01Z",
            correlation_id="parent_failure_test",
        )
