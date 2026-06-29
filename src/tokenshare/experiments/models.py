"""Phase 8 实验基础设施的纯模型。"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from enum import Enum
from hashlib import sha256
from typing import Any


JsonObject = dict[str, Any]


class ExperimentStatus(str, Enum):
    """实验 run 的外部报告状态。"""

    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class SimulationProfile:
    """一次实验的故障、消融、executor 和随机性配置。"""

    profile_id: str
    seed: int
    executor_profile: str = "deterministic_local"
    fault_profile: str = "none"
    ablation_mode: str = "FULL"
    clock_semantics: str = "fixed_logical_clock"
    output_policy: JsonObject | None = None
    schema_version: str = "phase8.simulation_profile.v1"

    def __post_init__(self) -> None:
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise TypeError("seed must be an integer")
        for field_name in (
            "profile_id",
            "executor_profile",
            "fault_profile",
            "ablation_mode",
            "clock_semantics",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field_name} must be a non-empty string")

    @property
    def profile_digest(self) -> str:
        return digest_json(self._body(include_digest=False))

    def to_dict(self) -> JsonObject:
        return self._body(include_digest=True)

    def _body(self, *, include_digest: bool) -> JsonObject:
        body = {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "seed": self.seed,
            "executor_profile": self.executor_profile,
            "fault_profile": self.fault_profile,
            "ablation_mode": self.ablation_mode,
            "clock_semantics": self.clock_semantics,
            "output_policy": _json_value(self.output_policy or {}),
        }
        if include_digest:
            body["profile_digest"] = self.profile_digest
        return body


@dataclass(frozen=True)
class ExperimentCase:
    """Experiment 1-4 中的一个具体 fixture/case。"""

    experiment_id: str
    case_id: str
    plugin_id: str
    plugin_version: str
    fixture_name: str
    expected_event_types: list[str] | tuple[str, ...] | None = None
    expected_outputs: JsonObject | None = None
    metadata: JsonObject | None = None
    schema_version: str = "phase8.experiment_case.v1"

    def __post_init__(self) -> None:
        for field_name in (
            "experiment_id",
            "case_id",
            "plugin_id",
            "plugin_version",
            "fixture_name",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field_name} must be a non-empty string")

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "case_id": self.case_id,
            "plugin_id": self.plugin_id,
            "plugin_version": self.plugin_version,
            "fixture_name": self.fixture_name,
            "expected_event_types": list(self.expected_event_types or []),
            "expected_outputs": _json_value(self.expected_outputs or {}),
            "metadata": _json_value(self.metadata or {}),
        }


@dataclass(frozen=True)
class ExperimentRun:
    """一次可复现实验运行的 manifest 逻辑对象。"""

    run_id: str
    experiment_id: str
    case_id: str
    status: ExperimentStatus
    profile_digest: str
    seed: int
    clock_semantics: str
    plugin_descriptors: tuple[JsonObject, ...]
    executor_descriptors: tuple[JsonObject, ...]
    event_log_ref: JsonObject | None
    artifact_root: JsonObject | None
    blocked_reason: JsonObject | None
    created_at: str
    schema_version: str = "phase8.experiment_run_manifest.v1"

    @classmethod
    def create(
        cls,
        *,
        case: ExperimentCase,
        profile: SimulationProfile,
        created_at: str,
    ) -> "ExperimentRun":
        identity_digest = digest_json(
            {
                "experiment_id": case.experiment_id,
                "case_id": case.case_id,
                "profile_digest": profile.profile_digest,
                "seed": profile.seed,
                "created_at": created_at,
            }
        )
        run_id = (
            f"{case.experiment_id}__{case.case_id}__"
            f"seed{profile.seed}__{_digest_suffix(identity_digest)}"
        )
        return cls(
            run_id=run_id,
            experiment_id=case.experiment_id,
            case_id=case.case_id,
            status=ExperimentStatus.PENDING,
            profile_digest=profile.profile_digest,
            seed=profile.seed,
            clock_semantics=profile.clock_semantics,
            plugin_descriptors=(),
            executor_descriptors=(),
            event_log_ref=None,
            artifact_root=None,
            blocked_reason=None,
            created_at=created_at,
        )

    def with_status(
        self,
        status: ExperimentStatus | str,
        *,
        blocked_reason: JsonObject | None = None,
    ) -> "ExperimentRun":
        normalized = ExperimentStatus(status)
        if normalized == ExperimentStatus.BLOCKED and not blocked_reason:
            raise ValueError("blocked_reason is required for blocked runs")
        if normalized != ExperimentStatus.BLOCKED and blocked_reason is not None:
            raise ValueError("blocked_reason is only valid for blocked runs")
        return replace(self, status=normalized, blocked_reason=blocked_reason)

    def with_evidence(
        self,
        *,
        plugin_descriptors: tuple[JsonObject, ...] | list[JsonObject] | None = None,
        executor_descriptors: tuple[JsonObject, ...] | list[JsonObject] | None = None,
        event_log_ref: JsonObject | None = None,
        artifact_root: JsonObject | None = None,
    ) -> "ExperimentRun":
        return replace(
            self,
            plugin_descriptors=(
                tuple(plugin_descriptors)
                if plugin_descriptors is not None
                else self.plugin_descriptors
            ),
            executor_descriptors=(
                tuple(executor_descriptors)
                if executor_descriptors is not None
                else self.executor_descriptors
            ),
            event_log_ref=event_log_ref if event_log_ref is not None else self.event_log_ref,
            artifact_root=artifact_root if artifact_root is not None else self.artifact_root,
        )

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "experiment_id": self.experiment_id,
            "case_id": self.case_id,
            "status": self.status.value,
            "profile_digest": self.profile_digest,
            "seed": self.seed,
            "clock_semantics": self.clock_semantics,
            "plugin_descriptors": _json_value(self.plugin_descriptors),
            "executor_descriptors": _json_value(self.executor_descriptors),
            "event_log_ref": _json_value(self.event_log_ref or {}),
            "artifact_root": _json_value(self.artifact_root or {}),
            "blocked_reason": _json_value(self.blocked_reason),
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class AdapterPreflight:
    """插件 adapter 的运行前检查结果。"""

    ready: bool
    blocked_reason: JsonObject | None = None
    plugin_descriptors: tuple[JsonObject, ...] = ()
    executor_descriptors: tuple[JsonObject, ...] = ()


@dataclass(frozen=True)
class AdapterRunResult:
    """插件 adapter 运行后的通用证据包。"""

    status: ExperimentStatus
    case_report: JsonObject
    metrics: JsonObject
    event_log_path: str | None = None
    artifact_root_path: str | None = None
    plugin_descriptors: tuple[JsonObject, ...] = ()
    executor_descriptors: tuple[JsonObject, ...] = ()


@dataclass(frozen=True)
class ExperimentResult:
    """ExperimentRunner 返回给调用方的对象。"""

    run: ExperimentRun
    output_dir: Any
    manifest_path: Any
    case_report_path: Any
    metrics_path: Any
    summary_csv_path: Any


def digest_json(data: Any) -> str:
    """返回与协议其它 digest 一致的稳定 JSON sha256。"""

    encoded = json.dumps(
        _json_value(data),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


def status_value(status: ExperimentStatus | str) -> str:
    return ExperimentStatus(status).value


def _digest_suffix(digest: str, length: int = 12) -> str:
    return digest.replace("sha256:", "")[:length]


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value
