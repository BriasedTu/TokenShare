"""Slim V2 对单个协议 root 的最小公共装配。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from tokenshare.core.models import ProtocolConfig
from tokenshare.local_runtime import (
    NoOpRuntimeHooks,
    ProtocolExecutionScope,
    ProtocolMechanismPolicy,
    ProtocolRunCoordinator,
    ProtocolRunRequest,
    ProtocolRunResult,
)
from tokenshare.protocol_engine import ProtocolEngine
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger


@dataclass(frozen=True, slots=True)
class RootAssembly:
    """一个 root 独占的系统对象；不跨 root 共享可变 runtime。"""

    run_id: str
    root_input: object
    protocol_config: ProtocolConfig
    artifact_store: ArtifactStore
    event_ledger: EventLedger
    plugin_runtime: object
    worker_backend: object
    now: Callable[[], str]
    observation_clock: Callable[[], str]


def run_root_slice(assembly: RootAssembly) -> ProtocolRunResult:
    """经现有 engine/coordinator 恰好运行一次完整 root 生命周期。"""

    engine = ProtocolEngine(
        event_ledger=assembly.event_ledger,
        protocol_config=assembly.protocol_config,
        artifact_store=assembly.artifact_store,
    )
    coordinator = ProtocolRunCoordinator(
        engine=engine,
        artifact_store=assembly.artifact_store,
        event_ledger=assembly.event_ledger,
        now=assembly.now,
        observation_clock=assembly.observation_clock,
    )
    request = ProtocolRunRequest(
        run_id=assembly.run_id,
        root_input=assembly.root_input,
        plugin_runtime=assembly.plugin_runtime,
        worker_backend=assembly.worker_backend,
        mechanism_policy=ProtocolMechanismPolicy(),
        hooks=NoOpRuntimeHooks(),
        continue_after_terminal_child_failure=True,
        execution_scope=ProtocolExecutionScope(
            mode="whole_root",
            selected_ai_unit_ids=(),
        ),
        trace_delay_policy="online_real_time",
        logical_scheduler=None,
    )
    return coordinator.run_root(request)


__all__ = ["RootAssembly", "run_root_slice"]
