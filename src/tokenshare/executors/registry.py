"""Phase 3 executor registry and status matching."""

from __future__ import annotations

from typing import Any

from tokenshare.core.models import JsonObject
from tokenshare.executors.contracts import ExecutorDescriptor, ExecutorStatus
from tokenshare.storage.artifacts import ArtifactStore


class ExecutorRegistry:
    """In-memory executor registry with explicit Phase 3 statuses."""

    def __init__(self) -> None:
        self._executors: dict[str, ExecutorDescriptor] = {}
        self._frozen = False

    def register(self, descriptor: ExecutorDescriptor) -> None:
        if self._frozen:
            raise ValueError("executor registry is frozen")
        if descriptor.executor_id in self._executors:
            raise ValueError(f"duplicate executor descriptor: {descriptor.executor_id}")
        self._executors[descriptor.executor_id] = descriptor

    def match_available(
        self,
        *,
        executor_type: str,
        hard_requirements: JsonObject,
        request_schema_version: str,
    ) -> list[ExecutorDescriptor]:
        return [
            descriptor
            for descriptor in self._sorted_descriptors()
            if self._match_failure(
                descriptor,
                executor_type=executor_type,
                hard_requirements=hard_requirements,
                request_schema_version=request_schema_version,
            )
            is None
        ]

    def no_match_reasons(
        self,
        *,
        executor_type: str,
        hard_requirements: JsonObject,
        request_schema_version: str,
    ) -> dict[str, str]:
        reasons: dict[str, str] = {}
        for descriptor in self._sorted_descriptors():
            reason = self._match_failure(
                descriptor,
                executor_type=executor_type,
                hard_requirements=hard_requirements,
                request_schema_version=request_schema_version,
            )
            if reason is not None:
                reasons[descriptor.executor_id] = reason
        return reasons

    def freeze_entries(self, *, artifact_store: ArtifactStore, frozen_at: str) -> list[JsonObject]:
        entries: list[JsonObject] = []
        for descriptor in self._sorted_descriptors():
            artifact_ref = artifact_store.save_json(
                descriptor.to_dict(),
                artifact_id=f"executor_descriptor_{descriptor.executor_id}_{descriptor.executor_version}",
                artifact_type="ExecutorDescriptor",
                artifact_schema_id="phase3.executor_descriptor",
                artifact_schema_version="v1",
                source={"kind": "executor_registry"},
                metadata={
                    "executor_id": descriptor.executor_id,
                    "executor_version": descriptor.executor_version,
                },
                created_at=frozen_at,
            )
            entries.append(
                {
                    "executor_id": descriptor.executor_id,
                    "executor_type": descriptor.executor_type,
                    "executor_version": descriptor.executor_version,
                    "descriptor_ref": artifact_ref.to_dict(),
                    "descriptor_digest": descriptor.descriptor_digest,
                    "status": descriptor.normalized_status.value,
                    "capabilities_summary": dict(descriptor.capabilities),
                }
            )
        return entries

    def mark_frozen(self) -> None:
        self._frozen = True

    def _sorted_descriptors(self) -> list[ExecutorDescriptor]:
        return sorted(
            self._executors.values(),
            key=lambda item: (item.executor_id, item.executor_version),
        )

    def _match_failure(
        self,
        descriptor: ExecutorDescriptor,
        *,
        executor_type: str,
        hard_requirements: JsonObject,
        request_schema_version: str,
    ) -> str | None:
        status = descriptor.normalized_status
        if status != ExecutorStatus.AVAILABLE:
            return f"status:{status.value}"
        if descriptor.executor_type != executor_type:
            return f"executor_type:{descriptor.executor_type}"
        if request_schema_version not in descriptor.supported_request_schema_versions:
            return f"request_schema:{request_schema_version}"
        for key, required_value in hard_requirements.items():
            if key not in descriptor.capabilities:
                return f"missing_capability:{key}"
            if not _capability_matches(required_value, descriptor.capabilities[key]):
                return f"capability_mismatch:{key}"
        return None


def _capability_matches(required_value: Any, actual_value: Any) -> bool:
    if isinstance(required_value, (list, tuple, set)):
        required = set(required_value)
        if isinstance(actual_value, (list, tuple, set)):
            return required.issubset(set(actual_value))
        return required == {actual_value}
    if isinstance(actual_value, (list, tuple, set)):
        return required_value in set(actual_value)
    return required_value == actual_value
