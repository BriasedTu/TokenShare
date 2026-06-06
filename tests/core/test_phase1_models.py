from tokenshare.core.models import (
    ArtifactRef,
    ProtocolConfig,
    TaskSpec,
    TaskState,
    TaskUnit,
)


def test_task_spec_and_root_unit_snapshots_use_phase1_json_keys() -> None:
    root_input_ref = ArtifactRef(
        artifact_id="artifact_root_input",
        artifact_type="root_input",
        uri="artifacts/artifact_root_input",
        content_hash="sha256:abc123",
        size_bytes=17,
        media_type="application/json",
        artifact_schema_id="factorization.root_input",
        artifact_schema_version="1",
        source={"kind": "client_input"},
        metadata={"case": "phase1"},
        created_at="2026-06-06T00:00:00Z",
    )
    protocol_config = ProtocolConfig.default(
        config_id="config_phase1",
        artifact_store_uri="file://artifacts",
        event_log_uri="file://events/task_demo.jsonl",
        metadata={"purpose": "test"},
    )
    task_spec = TaskSpec(
        task_id="task_demo",
        description="Factor a small fixture integer.",
        plugin_id="factorization",
        plugin_version="0.1.0",
        split_strategy_id="trial_division_ranges",
        split_strategy_params={"range_size": 10},
        root_input_ref=root_input_ref,
        protocol_config=protocol_config,
        metadata={"experiment": "phase1"},
        created_at="2026-06-06T00:00:01Z",
    )

    root_unit = TaskUnit.create_root(
        task_spec=task_spec,
        unit_id="unit_root_demo",
        required_capabilities={"executor": "local"},
        plugin_payload={"range": [2, 100]},
        now="2026-06-06T00:00:02Z",
    )

    task_snapshot = task_spec.to_dict()
    unit_snapshot = root_unit.to_dict()

    assert task_snapshot["schema_version"] == "TaskSpec.v1"
    assert task_snapshot["root_input_ref"]["artifact_id"] == "artifact_root_input"
    assert task_snapshot["protocol_config"]["canonical_output_policy"] == "first_verified_bundle"
    assert unit_snapshot["schema_version"] == "TaskUnit.v1"
    assert unit_snapshot["state"] == TaskState.READY
    assert unit_snapshot["input_refs"]["root_input"]["content_hash"] == "sha256:abc123"
    assert unit_snapshot["canonical_output_refs"] == {}
    assert unit_snapshot["plugin_payload"] == {"range": [2, 100]}
