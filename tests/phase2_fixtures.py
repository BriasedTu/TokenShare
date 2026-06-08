from tokenshare.core.models import ArtifactRef, ClientRecord, ProtocolConfig, TaskRelation, TaskState, TaskUnit


def make_artifact_ref(artifact_id: str = "artifact_output") -> ArtifactRef:
    return ArtifactRef(
        artifact_id=artifact_id,
        artifact_type="candidate_output",
        uri=f"artifacts/{artifact_id}",
        content_hash=f"sha256:{artifact_id}",
        size_bytes=17,
        media_type="application/json",
        artifact_schema_id="tokenshare.test_output",
        artifact_schema_version="1",
        source={"kind": "test"},
        metadata={},
        created_at="2026-06-08T00:00:00Z",
    )


def make_config() -> ProtocolConfig:
    return ProtocolConfig.default(
        config_id="config_phase2",
        artifact_store_uri="file://artifacts",
        event_log_uri="file://events/task_demo.jsonl",
    )


def make_unit(
    unit_id: str = "unit_ready",
    *,
    state: TaskState = TaskState.READY,
    required_capabilities: dict | None = None,
    canonical_output_refs: dict | None = None,
    depth: int = 0,
) -> TaskUnit:
    return TaskUnit(
        unit_id=unit_id,
        task_id="task_demo",
        parent_unit_id=None,
        depth=depth,
        unit_type="work",
        state=state,
        input_refs={},
        canonical_output_refs=canonical_output_refs or {},
        required_capabilities=required_capabilities or {"executor": "local"},
        weight=1.0,
        budget_limit=None,
        deadline=None,
        plugin_payload={},
        metadata={},
        created_at="2026-06-08T00:00:00Z",
        updated_at="2026-06-08T00:00:00Z",
    )


def make_relation(
    relation_id: str = "rel_root_child",
    *,
    source_unit_id: str = "unit_root",
    target_unit_id: str = "unit_child",
    source_output_name: str = "result",
    target_input_name: str = "root_result",
) -> TaskRelation:
    return TaskRelation(
        relation_id=relation_id,
        task_id="task_demo",
        relation_type="depends_on_output",
        source_unit_id=source_unit_id,
        target_unit_id=target_unit_id,
        source_output_name=source_output_name,
        target_input_name=target_input_name,
        created_reason="test",
        metadata={},
        created_at="2026-06-08T00:00:00Z",
    )


def make_client(
    client_id: str = "client_local",
    *,
    capabilities: dict | None = None,
    status: str = "active",
) -> ClientRecord:
    return ClientRecord(
        client_id=client_id,
        executor_type="local",
        executor_id="executor_local",
        executor_version="0.1.0",
        capabilities=capabilities or {"executor": "local", "proof": ["stub"]},
        status=status,
        stats={},
        metadata={},
        registered_at="2026-06-08T00:00:00Z",
    )
