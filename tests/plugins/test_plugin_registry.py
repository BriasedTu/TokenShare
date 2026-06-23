import pytest

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
    assert executor_entry["executor_id"] == "executor_mock_ai"
    assert executor_entry["status"] == "Available"
    assert store.verify(ArtifactRef.from_dict(plugin_entry["descriptor_ref"]))
    assert store.verify(ArtifactRef.from_dict(executor_entry["descriptor_ref"]))

    with pytest.raises(ValueError, match="frozen"):
        plugin_registry.register(make_plugin_descriptor())
