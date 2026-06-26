import pytest
import json

from tokenshare.core.models import ArtifactRef
from tokenshare.executors.registry import ExecutorRegistry
from tokenshare.plugins.registry import PluginRegistry
from tokenshare.storage.artifacts import ArtifactStore

from tests.phase3_fixtures import make_executor_descriptor, make_plugin_descriptor


def test_plugin_registry_freeze_persists_descriptor_artifacts_and_locks_versions(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    plugin_registry = PluginRegistry()
    executor_registry = ExecutorRegistry()
    plugin_registry.register(make_plugin_descriptor())
    executor_registry.register(make_executor_descriptor())

    snapshot = plugin_registry.freeze(
        task_id="task_demo",
        registry_snapshot_id="registry_snapshot_1",
        executor_registry=executor_registry,
        artifact_store=store,
        frozen_at="2026-06-23T00:00:01Z",
    )

    plugin_entry = snapshot.plugin_entries[0]
    executor_entry = snapshot.executor_entries[0]

    assert snapshot.schema_version == "phase3.registry_snapshot.v1"
    assert plugin_entry["plugin_id"] == "structured_report_stub"
    assert plugin_entry["plugin_version"] == "0.1.0"
    assert plugin_entry["descriptor_digest"].startswith("sha256:")
    assert plugin_entry["split_strategy_ids"] == ["structured_report_sections_v1"]
    assert executor_entry["executor_id"] == "executor_mock_ai"
    assert executor_entry["status"] == "Available"
    plugin_descriptor_ref = ArtifactRef.from_dict(plugin_entry["descriptor_ref"])
    assert store.verify(plugin_descriptor_ref)
    plugin_descriptor = json.loads(store.read_bytes(plugin_descriptor_ref).decode("utf-8"))
    split_strategy = plugin_descriptor["split_strategies"]["structured_report_sections_v1"]
    assert split_strategy["split_strategy_id"] == "structured_report_sections_v1"
    assert split_strategy["params_schema_ref"] == {"schema_ref": "schema.section_split_params.v1"}
    assert split_strategy["allowed_unit_types"] == ["section"]
    assert split_strategy["candidate_artifact_policy"]["executor_may_submit_candidates"] is True
    assert split_strategy["candidate_artifact_policy"]["executor_may_define_task_graph"] is False
    assert store.verify(ArtifactRef.from_dict(executor_entry["descriptor_ref"]))

    with pytest.raises(ValueError, match="frozen"):
        plugin_registry.register(make_plugin_descriptor())
