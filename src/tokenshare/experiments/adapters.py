"""Phase 8 插件实验适配契约。"""

from __future__ import annotations

from typing import Protocol

from tokenshare.experiments.models import (
    AdapterPreflight,
    AdapterRunResult,
    ExperimentCase,
    SimulationProfile,
)


class PluginExperimentAdapter(Protocol):
    """插件向通用 ExperimentRunner 暴露的最小契约。"""

    plugin_id: str
    plugin_version: str

    def preflight(self, case: ExperimentCase, profile: SimulationProfile) -> AdapterPreflight:
        """检查 fixture、descriptor、环境和真实证据能力。"""

    def run_case(
        self,
        case: ExperimentCase,
        profile: SimulationProfile,
        output_root,
    ) -> AdapterRunResult:
        """运行一个实验 case 并返回结构化证据。"""


class AdapterRegistry:
    """按 plugin id/version 查找实验 adapter。"""

    def __init__(self) -> None:
        self._adapters: dict[tuple[str, str], PluginExperimentAdapter] = {}

    def register(self, adapter: PluginExperimentAdapter) -> None:
        key = (adapter.plugin_id, adapter.plugin_version)
        if key in self._adapters:
            raise ValueError(f"experiment adapter already registered: {key[0]}@{key[1]}")
        self._adapters[key] = adapter

    def get(self, plugin_id: str, plugin_version: str) -> PluginExperimentAdapter:
        try:
            return self._adapters[(plugin_id, plugin_version)]
        except KeyError as exc:
            raise ValueError(
                f"missing experiment adapter: {plugin_id}@{plugin_version}"
            ) from exc
