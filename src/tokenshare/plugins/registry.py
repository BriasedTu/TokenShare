"""Phase 3 plugin registry and frozen run snapshots."""

from __future__ import annotations

from dataclasses import dataclass

from tokenshare.core.models import JsonObject
from tokenshare.executors.registry import ExecutorRegistry
from tokenshare.plugins.contracts import PluginDescriptor
from tokenshare.storage.artifacts import ArtifactStore


@dataclass(frozen=True)
class RegistrySnapshot:
    """Frozen plugin and executor descriptor refs for one root task."""

    registry_snapshot_id: str
    task_id: str
    plugin_entries: list[JsonObject]
    executor_entries: list[JsonObject]
    frozen_at: str
    metadata: JsonObject | None = None
    schema_version: str = "phase3.registry_snapshot.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "registry_snapshot_id": self.registry_snapshot_id,
            "task_id": self.task_id,
            "plugin_entries": [dict(entry) for entry in self.plugin_entries],
            "executor_entries": [dict(entry) for entry in self.executor_entries],
            "frozen_at": self.frozen_at,
            "metadata": dict(self.metadata or {}),
        }


class PluginRegistry:
    """In-memory registry that can be frozen into artifact-backed descriptors."""

    def __init__(self) -> None:
        self._plugins: dict[tuple[str, str], PluginDescriptor] = {}
        self._frozen = False

    def register(self, descriptor: PluginDescriptor) -> None:
        if self._frozen:
            raise ValueError("plugin registry is frozen")
        key = (descriptor.plugin_id, descriptor.plugin_version)
        if key in self._plugins:
            raise ValueError(f"duplicate plugin descriptor: {descriptor.plugin_id}@{descriptor.plugin_version}")
        self._plugins[key] = descriptor

    def freeze(
        self,
        *,
        task_id: str,
        registry_snapshot_id: str,
        executor_registry: ExecutorRegistry,
        artifact_store: ArtifactStore,
        frozen_at: str,
        metadata: JsonObject | None = None,
    ) -> RegistrySnapshot:
        plugin_entries: list[JsonObject] = []
        for descriptor in sorted(
            self._plugins.values(), key=lambda item: (item.plugin_id, item.plugin_version)
        ):
            artifact_ref = artifact_store.save_json(
                descriptor.to_dict(),
                artifact_id=f"plugin_descriptor_{descriptor.plugin_id}_{descriptor.plugin_version}",
                artifact_type="PluginDescriptor",
                artifact_schema_id="phase3.plugin_descriptor",
                artifact_schema_version="v1",
                source={"kind": "plugin_registry"},
                metadata={"plugin_id": descriptor.plugin_id, "plugin_version": descriptor.plugin_version},
                created_at=frozen_at,
            )
            plugin_entries.append(
                {
                    "plugin_id": descriptor.plugin_id,
                    "plugin_version": descriptor.plugin_version,
                    "descriptor_ref": artifact_ref.to_dict(),
                    "descriptor_digest": descriptor.descriptor_digest,
                    "supported_task_types": list(descriptor.supported_task_types),
                }
            )

        snapshot = RegistrySnapshot(
            registry_snapshot_id=registry_snapshot_id,
            task_id=task_id,
            plugin_entries=plugin_entries,
            executor_entries=executor_registry.freeze_entries(
                artifact_store=artifact_store,
                frozen_at=frozen_at,
            ),
            frozen_at=frozen_at,
            metadata=metadata or {},
        )
        self._frozen = True
        executor_registry.mark_frozen()
        return snapshot

    @property
    def is_frozen(self) -> bool:
        return self._frozen
