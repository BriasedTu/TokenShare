"""Pure scheduling decisions for Phase 2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from tokenshare.core.models import ClientRecord, JsonObject, ProtocolConfig
from tokenshare.core.task_graph import TaskGraph


@dataclass(frozen=True)
class SchedulingDecision:
    """Why a ready TaskUnit was matched to a client."""

    decision_id: str
    task_id: str
    unit_id: str
    client_id: str
    policy_id: str
    matched_capabilities: list[str]
    lease_kind: str
    reason: str
    created_at: str
    input_summary: JsonObject | None = None
    schema_version: str = "phase2.scheduling_decision.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "decision_id": self.decision_id,
            "task_id": self.task_id,
            "unit_id": self.unit_id,
            "client_id": self.client_id,
            "policy_id": self.policy_id,
            "matched_capabilities": list(self.matched_capabilities),
            "lease_kind": self.lease_kind,
            "reason": self.reason,
            "created_at": self.created_at,
            "input_summary": dict(self.input_summary or {}),
        }


class Scheduler:
    """FIFO ready-unit scheduler with capability filtering."""

    def __init__(self, *, policy_id: str = "fifo_ready_v1") -> None:
        self.policy_id = policy_id

    def select_next(
        self,
        *,
        graph: TaskGraph,
        clients: Iterable[ClientRecord],
        protocol_config: ProtocolConfig,
        active_leases_by_unit_id: dict[str, Any],
        now: str,
        decision_id: str,
        lease_kind: str = "primary",
    ) -> SchedulingDecision | None:
        ready_unit_ids = sorted(
            graph.ready_unit_ids(),
            key=lambda unit_id: (graph.units[unit_id].created_at, unit_id),
        )
        client_list = list(clients)
        for unit_id in ready_unit_ids:
            if (
                not protocol_config.allow_shadow_execution
                and _has_active_lease(active_leases_by_unit_id.get(unit_id))
            ):
                continue
            unit = graph.units[unit_id]
            for client in client_list:
                matched = _matched_capabilities(unit.required_capabilities, client)
                if matched is None:
                    continue
                return SchedulingDecision(
                    decision_id=decision_id,
                    task_id=graph.task_id,
                    unit_id=unit_id,
                    client_id=client.client_id,
                    policy_id=self.policy_id,
                    matched_capabilities=matched,
                    lease_kind=lease_kind,
                    reason="ready_and_available",
                    created_at=now,
                    input_summary={
                        "ready_queue_size": len(ready_unit_ids),
                        "client_count": len(client_list),
                    },
                )
        return None


def _has_active_lease(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, int):
        return value > 0
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return bool(value)


def _matched_capabilities(required: JsonObject, client: ClientRecord) -> list[str] | None:
    if not _client_is_available(client.status):
        return None
    matched: list[str] = []
    for key, required_value in required.items():
        if key not in client.capabilities:
            return None
        if not _capability_matches(required_value, client.capabilities[key]):
            return None
        matched.append(key)
    return matched


def _client_is_available(status: str) -> bool:
    # Phase 3 的执行器状态契约使用序列化值 Available；active 是 Phase 2 ClientRecord 兼容入口。
    return status == "Available" or status.lower() == "active"


def _capability_matches(required_value: Any, client_value: Any) -> bool:
    if isinstance(required_value, (list, tuple, set)):
        required_set = set(required_value)
        if isinstance(client_value, (list, tuple, set)):
            return required_set.issubset(set(client_value))
        return required_set == {client_value}
    if isinstance(client_value, (list, tuple, set)):
        return required_value in set(client_value)
    return required_value == client_value
