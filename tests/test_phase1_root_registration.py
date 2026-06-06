from tokenshare.core.models import ProtocolConfig, TaskState
from tokenshare.core.registration import RootTaskRegistrar, RootTaskRegistrationRequest
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType


def test_register_root_task_persists_root_input_and_three_ordered_events(tmp_path) -> None:
    artifact_store = ArtifactStore(tmp_path)
    event_ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    protocol_config = ProtocolConfig.default(
        config_id="config_phase1",
        artifact_store_uri=f"file://{tmp_path / 'artifacts'}",
        event_log_uri=f"file://{tmp_path / 'events' / 'task_demo.jsonl'}",
    )
    registrar = RootTaskRegistrar(
        artifact_store=artifact_store,
        event_ledger=event_ledger,
    )

    result = registrar.register_root_task(
        RootTaskRegistrationRequest(
            task_id="task_demo",
            root_unit_id="unit_root_demo",
            root_artifact_id="artifact_root_input",
            description="Factor a small fixture integer.",
            plugin_id="factorization",
            plugin_version="0.1.0",
            split_strategy_id="trial_division_ranges",
            split_strategy_params={"range_size": 10},
            root_input_bytes=b'{"n": 91}',
            root_input_media_type="application/json",
            root_input_schema_id="factorization.root_input",
            root_input_schema_version="1",
            protocol_config=protocol_config,
            required_capabilities={"executor": "local"},
            plugin_payload={"range": [2, 100]},
            metadata={"experiment": "phase1"},
            created_at="2026-06-06T00:00:00Z",
        )
    )

    events = event_ledger.read_all()

    assert result.task_spec.task_id == "task_demo"
    assert result.root_unit.state == TaskState.READY
    assert result.root_input_ref.artifact_id == "artifact_root_input"
    assert artifact_store.verify(result.root_input_ref)
    assert [event.event_type for event in events] == [
        EventType.ARTIFACT_STORED,
        EventType.TASK_REGISTERED,
        EventType.TASK_UNIT_CREATED,
    ]
    assert events[1].payload["task_spec"]["root_input_ref"]["artifact_id"] == "artifact_root_input"
    assert events[2].payload["task_unit"]["input_refs"]["root_input"]["artifact_id"] == (
        "artifact_root_input"
    )
