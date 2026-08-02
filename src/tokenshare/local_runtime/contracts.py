"""本地协议运行时的最小稳定契约。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from math import isfinite
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Callable, Literal, Protocol

if TYPE_CHECKING:
    from tokenshare.local_runtime.logical_scheduler import (
        LogicalSourceLatencyScheduler,
    )
    from tokenshare.storage.sqlite_index import SQLiteMaterializedIndex

from tokenshare.core.expansion import (
    DecompositionProposal,
    ExpansionDecision,
    ExpectedOutputRef,
    MergePlan,
    SplitStrategyInvocation,
)
from tokenshare.core.merge import ExpectedOutputResolution, MergeRecord, MergeTaskLink
from tokenshare.core.models import (
    ArtifactRef,
    Attempt,
    ClientRecord,
    JsonObject,
    Lease,
    TaskUnit,
)
from tokenshare.core.registration import RootTaskRegistrationRequest
from tokenshare.core.verification import CanonicalSelection, VerificationReport
from tokenshare.executors.contracts import ExecutionRequest, ExecutionSubmission
from tokenshare.executors.registry import ExecutorRegistry
from tokenshare.plugins.registry import PluginRegistry
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import LedgerEvent, VerifiedLedgerSnapshot


LOGICAL_DISPATCH_START_MS_HINT = "logical_dispatch_start_ms"


@dataclass(frozen=True, kw_only=True)
class CanonicalUnitContext:
    """插件构造领域 action 所需的 canonical unit 事实。"""

    unit: TaskUnit
    canonical_selection: CanonicalSelection


@dataclass(frozen=True, kw_only=True)
class CompleteAction:
    """插件拥有的 complete 领域 action。"""

    invocation: SplitStrategyInvocation
    decision: ExpansionDecision


@dataclass(frozen=True, kw_only=True)
class ExpandAction:
    """插件拥有的 expand 领域 action。"""

    invocation: SplitStrategyInvocation
    decision: ExpansionDecision
    proposal: DecompositionProposal
    merge_plan: MergePlan


@dataclass(frozen=True, kw_only=True)
class MergeExecutionContext:
    """merge executor canonical 后交给插件生成 resolution 的事实。"""

    parent: TaskUnit
    canonical_children: tuple[TaskUnit, ...]
    merge_unit: TaskUnit
    merge_task_link: MergeTaskLink
    merge_plan: MergePlan
    expected_output_refs: tuple[ExpectedOutputRef, ...]
    canonical_selection: CanonicalSelection
    canonical_event: LedgerEvent


@dataclass(frozen=True, kw_only=True)
class MergeResolutionAction:
    """插件生成、由 engine 落账的 merge resolution action。"""

    merge_record: MergeRecord
    expected_output_resolutions: tuple[ExpectedOutputResolution, ...]


@dataclass(frozen=True, kw_only=True)
class MergeAction:
    """把 merge canonical 事实转换成插件领域 resolution。"""

    resolution_builder: Callable[[MergeExecutionContext], MergeResolutionAction]


@dataclass(frozen=True, kw_only=True)
class MergeReadinessContext:
    """插件判断 parent 是否可进入 merge 的领域无关事实视图。"""

    parent: TaskUnit
    children: tuple[TaskUnit, ...]
    merge_plan: MergePlan
    canonical_events: tuple[LedgerEvent, ...]
    verification_events: tuple[LedgerEvent, ...]


@dataclass(frozen=True, kw_only=True)
class MergeReadinessDecision:
    """版本化的插件 completion/readiness 决策；runtime 只解释状态与选择集。"""

    status: str
    reason: str
    policy_id: str
    policy_version: str
    required_child_unit_ids: tuple[str, ...]
    selected_child_unit_ids: tuple[str, ...] = ()
    decision_digest: str | None = None
    schema_version: str = "tokenshare.merge_readiness_decision.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "tokenshare.merge_readiness_decision.v1":
            raise ValueError(
                "schema_version must be tokenshare.merge_readiness_decision.v1"
            )
        if self.status not in {"ready", "wait", "failed"}:
            raise ValueError("merge readiness status must be ready, wait, or failed")
        for field_name, value in {
            "reason": self.reason,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
        }.items():
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field_name} must be a non-empty string")
        required = self.required_child_unit_ids
        selected = self.selected_child_unit_ids
        if (
            not required
            or len(set(required)) != len(required)
            or any(not item for item in required)
        ):
            raise ValueError("required_child_unit_ids must be non-empty and unique")
        if len(set(selected)) != len(selected) or any(not item for item in selected):
            raise ValueError("selected_child_unit_ids must be unique")
        if not set(selected).issubset(required):
            raise ValueError("selected child units must belong to the required set")
        if self.status == "ready" and not selected:
            raise ValueError("ready merge decision must select at least one child")
        if self.status != "ready" and selected:
            raise ValueError("non-ready merge decision cannot select children")
        expected = _merge_readiness_digest(self._digest_body())
        if self.decision_digest is None:
            object.__setattr__(self, "decision_digest", expected)
        elif self.decision_digest != expected:
            raise ValueError("merge readiness decision_digest mismatch")

    def _digest_body(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "reason": self.reason,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "required_child_unit_ids": list(self.required_child_unit_ids),
            "selected_child_unit_ids": list(self.selected_child_unit_ids),
        }

    def to_dict(self) -> JsonObject:
        return {
            **self._digest_body(),
            "decision_digest": self.decision_digest,
        }


@dataclass(frozen=True, kw_only=True)
class RootProtocolPlan:
    """一个 root 的确定性协议计划；不包含 runtime 状态决定。"""

    registration_request: RootTaskRegistrationRequest
    plugin_registry: PluginRegistry
    executor_registry: ExecutorRegistry
    registry_snapshot_id: str
    clients: tuple[ClientRecord, ...]
    canonical_action_builder: Callable[
        [CanonicalUnitContext], CompleteAction | ExpandAction
    ]
    settlement_policy_id: str = "sandbox_equal_weight_v1"


class ProtocolTaskPluginRuntime(Protocol):
    """把通用运行时调用翻译为插件领域行为。"""

    def plan_root(
        self,
        root_input: object,
        *,
        artifact_store: ArtifactStore,
    ) -> RootProtocolPlan:
        """构造插件拥有的确定性根计划。"""
        ...

    def build_execution_request(
        self,
        unit: TaskUnit,
        *,
        attempt: Attempt,
        lease: Lease,
    ) -> ExecutionRequest:
        """为已调度的 attempt 构造执行请求。"""
        ...

    def verify_submission(
        self,
        submission: ExecutionSubmission,
        *,
        unit: TaskUnit,
    ) -> VerificationReport:
        """用插件规则验证已持久化的 submission。"""
        ...

    def build_merge(
        self,
        *,
        parent: TaskUnit,
        canonical_children: tuple[TaskUnit, ...],
        slot_integrity_enabled: bool = True,
    ) -> MergeAction:
        """按系统运行策略从 canonical child 集合构造插件拥有的 merge action。"""
        ...

    def planned_ai_unit_id(self, unit: TaskUnit) -> str | None:
        """返回实验冻结的通用 AI-unit identity；协议结构单元返回 None。"""
        ...

    def evaluate_merge_readiness(
        self,
        context: MergeReadinessContext,
    ) -> MergeReadinessDecision:
        """根据 canonical/verification 事实决定 merge 是否就绪。"""
        ...


@dataclass(frozen=True, kw_only=True)
class RawOutputContext:
    """provider response 已持久化、尚未进入 parser 的稳定 hook 事实。"""

    run_id: str
    task_id: str
    unit_id: str
    attempt_id: str
    worker_id: str
    raw_output_ref: ArtifactRef
    provenance_ref: ArtifactRef
    usage_ref: ArtifactRef | None
    content_text: str
    provider: str
    model: str
    entry_id: str
    usage_summary: JsonObject
    submitted_at: str
    experiment_unit_id: str | None = None
    lease_deadline_at: str | None = None


@dataclass(frozen=True, kw_only=True)
class ParsedCandidateContext:
    """真实 parser 已持久化 candidate、协议尚未记录 submission 的事实。"""

    run_id: str
    task_id: str
    unit_id: str
    attempt_id: str
    lease_id: str
    worker_id: str
    raw_output_ref: ArtifactRef | None
    original_parsed_output_ref: ArtifactRef
    candidate_output_refs: dict[str, ArtifactRef]
    submitted_at: str
    experiment_unit_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class ParserContext:
    """submission 进入 parser-policy boundary 前的稳定事实。"""

    execution_request: ExecutionRequest
    submission: ExecutionSubmission
    unit: TaskUnit
    attempt: Attempt
    lease: Lease


@dataclass(frozen=True, kw_only=True)
class VerificationContext:
    """submission 进入领域 verifier/checker 前的稳定事实。"""

    execution_request: ExecutionRequest
    submission: ExecutionSubmission
    unit: TaskUnit
    attempt: Attempt
    lease: Lease


@dataclass(frozen=True, kw_only=True)
class RecoveryContext:
    """engine 已记录 recovery、尚未创建 replacement 的稳定事实。"""

    unit: TaskUnit
    attempt: Attempt
    lease: Lease
    trigger: str
    decision: JsonObject
    recovery_event_refs: tuple[JsonObject, ...]


@dataclass(frozen=True, kw_only=True)
class MergeContext:
    """merge gate 的实际 child coverage 和协议证据。"""

    parent: object
    canonical_children: tuple[object, ...]
    required_child_unit_ids: tuple[str, ...]
    gate_satisfied: bool
    protocol_event_refs: tuple[JsonObject, ...] = ()


@dataclass(frozen=True, kw_only=True)
class UnitProgressContext:
    """worker/liveness hook 可观察的 unit progress。"""

    stage: str
    unit: TaskUnit
    attempt: Attempt | None = None
    lease: Lease | None = None


RUNTIME_HOOK_OBSERVATION_SCHEMA_VERSION = (
    "tokenshare.runtime_hook_observation.v1"
)


class RuntimeHookObservationKind(str, Enum):
    """正式 runtime hook observation 的三个稳定判别值。"""

    EXPERIMENT_FAULT_INJECTED = "EXPERIMENT_FAULT_INJECTED"
    EXPERIMENT_ABLATION_GATE_APPLIED = "EXPERIMENT_ABLATION_GATE_APPLIED"
    EXPERIMENT_PREMATURE_MERGE_ATTEMPTED = (
        "EXPERIMENT_PREMATURE_MERGE_ATTEMPTED"
    )


@dataclass(frozen=True, kw_only=True)
class ExperimentFaultInjectedPayloadV1:
    """受控 fault hook 的领域无关事实。"""

    condition_id: str
    run_id: str
    task_id: str
    unit_id: str
    selected_target_ai_unit_id: str
    attempt_id: str
    fault_type: str
    protocol_event_refs: tuple[Mapping[str, object], ...]
    artifact_refs: tuple[ArtifactRef, ...]
    occurred_at: str

    def __post_init__(self) -> None:
        for field_name in (
            "condition_id",
            "run_id",
            "task_id",
            "unit_id",
            "selected_target_ai_unit_id",
            "attempt_id",
            "occurred_at",
        ):
            _require_non_empty_string(field_name, getattr(self, field_name))
        if self.fault_type not in _RUNTIME_FAULT_TYPES:
            raise ValueError("fault_type must be a registered paper fault type")
        object.__setattr__(
            self,
            "protocol_event_refs",
            _freeze_event_refs(self.protocol_event_refs),
        )
        object.__setattr__(
            self,
            "artifact_refs",
            _freeze_artifact_refs(self.artifact_refs, require_non_empty=True),
        )

    def to_dict(self) -> JsonObject:
        return {
            "condition_id": self.condition_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "unit_id": self.unit_id,
            "selected_target_ai_unit_id": self.selected_target_ai_unit_id,
            "attempt_id": self.attempt_id,
            "fault_type": self.fault_type,
            "protocol_event_refs": _event_refs_to_json(self.protocol_event_refs),
            "artifact_refs": _artifact_refs_to_json(self.artifact_refs),
            "occurred_at": self.occurred_at,
        }


@dataclass(frozen=True, kw_only=True)
class ExperimentAblationGateAppliedPayloadV1:
    """ablation gate 的输入、输出与引用事实。"""

    ablation_mode: str
    disabled_mechanism: str
    protocol_event_refs: tuple[Mapping[str, object], ...]
    artifact_refs: tuple[ArtifactRef, ...]
    hook_input: Mapping[str, object]
    hook_result: Mapping[str, bool]

    def __post_init__(self) -> None:
        if self.ablation_mode not in _RUNTIME_ABLATION_MODES:
            raise ValueError("ablation_mode must be a registered paper ablation mode")
        if self.disabled_mechanism not in _RUNTIME_DISABLED_MECHANISMS:
            raise ValueError("disabled_mechanism must be a registered mechanism")
        object.__setattr__(
            self,
            "protocol_event_refs",
            _freeze_event_refs(self.protocol_event_refs),
        )
        object.__setattr__(
            self,
            "artifact_refs",
            _freeze_artifact_refs(self.artifact_refs),
        )
        object.__setattr__(self, "hook_input", _freeze_hook_input(self.hook_input))
        object.__setattr__(
            self,
            "hook_result",
            _freeze_hook_result(self.hook_result),
        )

    def to_dict(self) -> JsonObject:
        return {
            "ablation_mode": self.ablation_mode,
            "disabled_mechanism": self.disabled_mechanism,
            "protocol_event_refs": _event_refs_to_json(self.protocol_event_refs),
            "artifact_refs": _artifact_refs_to_json(self.artifact_refs),
            "hook_input": _thaw_json(self.hook_input),
            "hook_result": _thaw_json(self.hook_result),
        }


@dataclass(frozen=True, kw_only=True)
class ExperimentPrematureMergeAttemptedPayloadV1:
    """merge gate 被绕过后实际尝试的结果事实。"""

    attempt_schema_version: str
    run_id: str
    task_id: str
    parent_unit_id: str
    required_child_unit_ids: tuple[str, ...]
    canonical_child_unit_ids: tuple[str, ...]
    missing_child_unit_ids: tuple[str, ...]
    attempt_status: str
    plugin_result_type: str | None
    plugin_error: str | None
    root_check_passed: bool
    failure_kind: str
    protocol_event_refs: tuple[Mapping[str, object], ...]
    result_artifact_ref: ArtifactRef

    def __post_init__(self) -> None:
        if self.attempt_schema_version != "tokenshare.premature_merge_attempt.v1":
            raise ValueError("attempt_schema_version is unsupported")
        for field_name in ("run_id", "task_id", "parent_unit_id"):
            _require_non_empty_string(field_name, getattr(self, field_name))
        for field_name in (
            "required_child_unit_ids",
            "canonical_child_unit_ids",
            "missing_child_unit_ids",
        ):
            object.__setattr__(
                self,
                field_name,
                _freeze_non_empty_strings(getattr(self, field_name), field_name),
            )
        if self.attempt_status != "executed":
            raise ValueError("attempt_status must be executed")
        for field_name in ("plugin_result_type", "plugin_error"):
            value = getattr(self, field_name)
            if value is not None:
                _require_non_empty_string(field_name, value)
        _require_exact_bool("root_check_passed", self.root_check_passed)
        if self.failure_kind != "merge_readiness_unsatisfied":
            raise ValueError("failure_kind must be merge_readiness_unsatisfied")
        object.__setattr__(
            self,
            "protocol_event_refs",
            _freeze_event_refs(self.protocol_event_refs),
        )
        object.__setattr__(
            self,
            "result_artifact_ref",
            _strict_artifact_ref(self.result_artifact_ref, "result_artifact_ref"),
        )

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.attempt_schema_version,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "parent_unit_id": self.parent_unit_id,
            "required_child_unit_ids": list(self.required_child_unit_ids),
            "canonical_child_unit_ids": list(self.canonical_child_unit_ids),
            "missing_child_unit_ids": list(self.missing_child_unit_ids),
            "attempt_status": self.attempt_status,
            "plugin_result_type": self.plugin_result_type,
            "plugin_error": self.plugin_error,
            "root_check_passed": self.root_check_passed,
            "failure_kind": self.failure_kind,
            "protocol_event_refs": _event_refs_to_json(self.protocol_event_refs),
            "result_artifact_ref": _artifact_ref_to_json(
                self.result_artifact_ref
            ),
        }


RuntimeHookObservationPayloadV1 = (
    ExperimentFaultInjectedPayloadV1
    | ExperimentAblationGateAppliedPayloadV1
    | ExperimentPrematureMergeAttemptedPayloadV1
)


@dataclass(frozen=True, kw_only=True)
class RuntimeHookObservationV1:
    """带 canonical digest 的 runtime hook discriminated union envelope。"""

    kind: RuntimeHookObservationKind
    payload: RuntimeHookObservationPayloadV1
    observation_digest: str | None = None
    schema_version: str = RUNTIME_HOOK_OBSERVATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != RUNTIME_HOOK_OBSERVATION_SCHEMA_VERSION:
            raise ValueError("runtime hook observation schema_version is unsupported")
        if not isinstance(self.kind, RuntimeHookObservationKind):
            raise TypeError("runtime hook observation kind must be typed")
        expected_type = _RUNTIME_PAYLOAD_BY_KIND[self.kind]
        if not isinstance(self.payload, expected_type):
            raise TypeError("runtime hook observation payload does not match kind")
        expected_digest = _json_digest(self._digest_body())
        if self.observation_digest is None:
            object.__setattr__(self, "observation_digest", expected_digest)
            return
        _require_sha256_digest("observation_digest", self.observation_digest)
        if self.observation_digest != expected_digest:
            raise ValueError("runtime hook observation_digest mismatch")

    def _digest_body(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind.value,
            "payload": self.payload.to_dict(),
        }

    def to_dict(self) -> JsonObject:
        return {
            **self._digest_body(),
            "observation_digest": self.observation_digest,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "RuntimeHookObservationV1":
        _require_exact_keys(
            data,
            {"schema_version", "kind", "payload", "observation_digest"},
            "runtime hook observation",
        )
        if data["schema_version"] != RUNTIME_HOOK_OBSERVATION_SCHEMA_VERSION:
            raise ValueError("runtime hook observation schema_version is unsupported")
        try:
            kind = RuntimeHookObservationKind(data["kind"])
        except (TypeError, ValueError) as exc:
            raise ValueError("runtime hook observation kind is unsupported") from exc
        payload = _runtime_payload_from_dict(kind, data["payload"])
        return cls(
            schema_version=RUNTIME_HOOK_OBSERVATION_SCHEMA_VERSION,
            kind=kind,
            payload=payload,
            observation_digest=data["observation_digest"],
        )


_RUNTIME_FAULT_TYPES = frozenset(
    {
        "false_positive",
        "false_negative",
        "no_return",
        "late_submission",
        "executor_error",
    }
)
_RUNTIME_ABLATION_MODES = frozenset(
    {
        "FULL",
        "NO_PARSER_POLICY",
        "NO_VERIFICATION",
        "NO_REQUEUE",
        "NO_MERGE_GATE",
        "NO_SLOT_INTEGRITY",
    }
)
_RUNTIME_DISABLED_MECHANISMS = frozenset(
    {"parser_policy", "verification", "requeue", "merge_gate", "slot_integrity"}
)
_RUNTIME_PAYLOAD_BY_KIND = {
    RuntimeHookObservationKind.EXPERIMENT_FAULT_INJECTED: (
        ExperimentFaultInjectedPayloadV1
    ),
    RuntimeHookObservationKind.EXPERIMENT_ABLATION_GATE_APPLIED: (
        ExperimentAblationGateAppliedPayloadV1
    ),
    RuntimeHookObservationKind.EXPERIMENT_PREMATURE_MERGE_ATTEMPTED: (
        ExperimentPrematureMergeAttemptedPayloadV1
    ),
}


def build_experiment_fault_injected_observation(
    *,
    condition_id: str,
    run_id: str,
    task_id: str,
    unit_id: str,
    selected_target_ai_unit_id: str,
    attempt_id: str,
    fault_type: str,
    protocol_event_refs: tuple[Mapping[str, object], ...],
    artifact_refs: tuple[ArtifactRef | Mapping[str, object], ...],
    occurred_at: str,
) -> RuntimeHookObservationV1:
    """构造受控 fault observation，不写入协议 event。"""

    return RuntimeHookObservationV1(
        kind=RuntimeHookObservationKind.EXPERIMENT_FAULT_INJECTED,
        payload=ExperimentFaultInjectedPayloadV1(
            condition_id=condition_id,
            run_id=run_id,
            task_id=task_id,
            unit_id=unit_id,
            selected_target_ai_unit_id=selected_target_ai_unit_id,
            attempt_id=attempt_id,
            fault_type=fault_type,
            protocol_event_refs=protocol_event_refs,
            artifact_refs=artifact_refs,
            occurred_at=occurred_at,
        ),
    )


def build_experiment_ablation_gate_applied_observation(
    *,
    ablation_mode: str,
    disabled_mechanism: str,
    protocol_event_refs: tuple[Mapping[str, object], ...],
    artifact_refs: tuple[ArtifactRef | Mapping[str, object], ...],
    hook_input: Mapping[str, object],
    hook_result: Mapping[str, object],
) -> RuntimeHookObservationV1:
    """构造 ablation gate observation，不解释 gate 的业务适用性。"""

    return RuntimeHookObservationV1(
        kind=RuntimeHookObservationKind.EXPERIMENT_ABLATION_GATE_APPLIED,
        payload=ExperimentAblationGateAppliedPayloadV1(
            ablation_mode=ablation_mode,
            disabled_mechanism=disabled_mechanism,
            protocol_event_refs=protocol_event_refs,
            artifact_refs=artifact_refs,
            hook_input=hook_input,
            hook_result=hook_result,
        ),
    )


def build_experiment_premature_merge_attempted_observation(
    *,
    attempt_schema_version: str,
    run_id: str,
    task_id: str,
    parent_unit_id: str,
    required_child_unit_ids: tuple[str, ...],
    canonical_child_unit_ids: tuple[str, ...],
    missing_child_unit_ids: tuple[str, ...],
    attempt_status: str,
    plugin_result_type: str | None,
    plugin_error: str | None,
    root_check_passed: bool,
    failure_kind: str,
    protocol_event_refs: tuple[Mapping[str, object], ...],
    result_artifact_ref: ArtifactRef | Mapping[str, object],
) -> RuntimeHookObservationV1:
    """构造 premature merge observation，不创建 merge/canonical 结果。"""

    return RuntimeHookObservationV1(
        kind=RuntimeHookObservationKind.EXPERIMENT_PREMATURE_MERGE_ATTEMPTED,
        payload=ExperimentPrematureMergeAttemptedPayloadV1(
            attempt_schema_version=attempt_schema_version,
            run_id=run_id,
            task_id=task_id,
            parent_unit_id=parent_unit_id,
            required_child_unit_ids=required_child_unit_ids,
            canonical_child_unit_ids=canonical_child_unit_ids,
            missing_child_unit_ids=missing_child_unit_ids,
            attempt_status=attempt_status,
            plugin_result_type=plugin_result_type,
            plugin_error=plugin_error,
            root_check_passed=root_check_passed,
            failure_kind=failure_kind,
            protocol_event_refs=protocol_event_refs,
            result_artifact_ref=result_artifact_ref,
        ),
    )


def _runtime_payload_from_dict(
    kind: RuntimeHookObservationKind,
    value: object,
) -> RuntimeHookObservationPayloadV1:
    if not isinstance(value, Mapping):
        raise TypeError("runtime hook observation payload must be an object")
    if kind is RuntimeHookObservationKind.EXPERIMENT_FAULT_INJECTED:
        _require_exact_keys(
            value,
            {
                "condition_id",
                "run_id",
                "task_id",
                "unit_id",
                "selected_target_ai_unit_id",
                "attempt_id",
                "fault_type",
                "protocol_event_refs",
                "artifact_refs",
                "occurred_at",
            },
            "fault observation payload",
        )
        return ExperimentFaultInjectedPayloadV1(**dict(value))
    if kind is RuntimeHookObservationKind.EXPERIMENT_ABLATION_GATE_APPLIED:
        _require_exact_keys(
            value,
            {
                "ablation_mode",
                "disabled_mechanism",
                "protocol_event_refs",
                "artifact_refs",
                "hook_input",
                "hook_result",
            },
            "ablation observation payload",
        )
        return ExperimentAblationGateAppliedPayloadV1(**dict(value))
    _require_exact_keys(
        value,
        {
            "schema_version",
            "run_id",
            "task_id",
            "parent_unit_id",
            "required_child_unit_ids",
            "canonical_child_unit_ids",
            "missing_child_unit_ids",
            "attempt_status",
            "plugin_result_type",
            "plugin_error",
            "root_check_passed",
            "failure_kind",
            "protocol_event_refs",
            "result_artifact_ref",
        },
        "premature merge observation payload",
    )
    return ExperimentPrematureMergeAttemptedPayloadV1(
        attempt_schema_version=value["schema_version"],
        run_id=value["run_id"],
        task_id=value["task_id"],
        parent_unit_id=value["parent_unit_id"],
        required_child_unit_ids=value["required_child_unit_ids"],
        canonical_child_unit_ids=value["canonical_child_unit_ids"],
        missing_child_unit_ids=value["missing_child_unit_ids"],
        attempt_status=value["attempt_status"],
        plugin_result_type=value["plugin_result_type"],
        plugin_error=value["plugin_error"],
        root_check_passed=value["root_check_passed"],
        failure_kind=value["failure_kind"],
        protocol_event_refs=value["protocol_event_refs"],
        result_artifact_ref=value["result_artifact_ref"],
    )


@dataclass(frozen=True, kw_only=True)
class RawOutputDirective:
    """只改变 parser 输入或声明受控 result kind，不写协议状态。"""

    content_text: str | None = None
    result_kind: str | None = None
    experiment_records: tuple[RuntimeHookObservationV1, ...] = ()

    def __post_init__(self) -> None:
        _require_runtime_observation_tuple(self.experiment_records)


@dataclass(frozen=True, kw_only=True)
class ParsedCandidateDirective:
    """只替换 verifier 将读取的 candidate refs，并返回实验观察。"""

    replacement_candidate_output_refs: dict[str, ArtifactRef]
    experiment_records: tuple[RuntimeHookObservationV1, ...] = ()

    def __post_init__(self) -> None:
        _require_runtime_observation_tuple(self.experiment_records)


@dataclass(frozen=True, kw_only=True)
class GateDirective:
    """稳定 gate 的窄指令；状态推进仍由 coordinator/engine 完成。"""

    bypass: bool = False
    stop: bool = False
    replacement: Any | None = None
    experiment_records: tuple[RuntimeHookObservationV1, ...] = ()

    def __post_init__(self) -> None:
        _require_runtime_observation_tuple(self.experiment_records)


@dataclass(frozen=True, kw_only=True)
class WorkerDirective:
    """worker progress 点的窄指令；不包含协议 state mutation。"""

    action: str
    worker_id: str | None = None
    experiment_records: tuple[RuntimeHookObservationV1, ...] = ()

    def __post_init__(self) -> None:
        _require_runtime_observation_tuple(self.experiment_records)


class RuntimeHooks(Protocol):
    """实验层只能在这些稳定观察点返回可选 directive。"""

    def after_raw_output_persisted(
        self, context: RawOutputContext
    ) -> RawOutputDirective | None: ...

    def after_parsed_candidate_persisted(
        self, context: ParsedCandidateContext
    ) -> ParsedCandidateDirective | None: ...

    def before_parser(self, context: ParserContext) -> GateDirective | None: ...

    def before_verification(
        self, context: VerificationContext
    ) -> GateDirective | None: ...

    def before_requeue(self, context: RecoveryContext) -> GateDirective | None: ...

    def before_merge(self, context: MergeContext) -> GateDirective | None: ...

    def on_unit_progress(
        self, context: UnitProgressContext
    ) -> WorkerDirective | None: ...


class NoOpRuntimeHooks:
    """FULL 模式默认 hooks：不变更输入、不绕过 gate、不请求 worker 操作。"""

    def after_raw_output_persisted(self, context: RawOutputContext) -> None:
        return None

    def after_parsed_candidate_persisted(
        self, context: ParsedCandidateContext
    ) -> None:
        return None

    def before_parser(self, context: ParserContext) -> None:
        return None

    def before_verification(self, context: VerificationContext) -> None:
        return None

    def before_requeue(self, context: RecoveryContext) -> None:
        return None

    def before_merge(self, context: MergeContext) -> None:
        return None

    def on_unit_progress(self, context: UnitProgressContext) -> None:
        return None


@dataclass(frozen=True, kw_only=True)
class ProtocolMechanismPolicy:
    """Experiment 4 正式五模式共享的机制 gate；默认值等价于 FULL。

    ``slot_integrity_enabled`` 继续服务历史回归，但不再对应正式论文条件。
    """

    parser_policy_enabled: bool = True
    verification_enabled: bool = True
    replacement_attempts_allowed: bool = True
    merge_gate_enabled: bool = True
    slot_integrity_enabled: bool = True


@dataclass(frozen=True, kw_only=True)
class ProtocolExecutionScope:
    """一个 root run 的通用执行范围；selected 模式只用于诊断观察。"""

    mode: str = "whole_root"
    selected_ai_unit_ids: tuple[str, ...] = ()
    schema_version: str = "tokenshare.protocol_execution_scope.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "tokenshare.protocol_execution_scope.v1":
            raise ValueError(
                "schema_version must be tokenshare.protocol_execution_scope.v1"
            )
        if self.mode not in {"whole_root", "selected_ai_units"}:
            raise ValueError("unsupported protocol execution scope")
        if self.mode == "whole_root":
            if self.selected_ai_unit_ids:
                raise ValueError("whole_root scope cannot select AI units")
            return
        if (
            not self.selected_ai_unit_ids
            or len(set(self.selected_ai_unit_ids))
            != len(self.selected_ai_unit_ids)
            or any(not item for item in self.selected_ai_unit_ids)
        ):
            raise ValueError(
                "selected_ai_units scope requires non-empty unique IDs"
            )


@dataclass(frozen=True, kw_only=True)
class WorkerTerminationPolicy:
    """把预注册 planned AI unit 解析为需终止的真实 execution request。"""

    target_planned_ai_unit_ids: tuple[str, ...]
    kill_point: str
    termination_count_target: int | None = None
    total_planned_ai_unit_count: int | None = None
    process_timeout_seconds: float = 120.0

    def __post_init__(self) -> None:
        if (
            not self.target_planned_ai_unit_ids
            or len(set(self.target_planned_ai_unit_ids))
            != len(self.target_planned_ai_unit_ids)
            or any(not item for item in self.target_planned_ai_unit_ids)
        ):
            raise ValueError(
                "target_planned_ai_unit_ids must be non-empty and unique"
            )
        if self.kill_point not in {
            "progress_25",
            "progress_50",
            "progress_75",
        }:
            raise ValueError("unsupported worker termination kill_point")
        if self.process_timeout_seconds <= 0:
            raise ValueError("process_timeout_seconds must be positive")
        if (
            self.total_planned_ai_unit_count is not None
            and (
                isinstance(self.total_planned_ai_unit_count, bool)
                or self.total_planned_ai_unit_count < 1
            )
        ):
            raise ValueError("total_planned_ai_unit_count must be a positive integer")
        if (
            self.termination_count_target is not None
            and (
                self.termination_count_target < len(
                    self.target_planned_ai_unit_ids
                )
                or self.termination_count_target < 1
            )
        ):
            raise ValueError(
                "termination_count_target must cover every selected planned unit"
            )

    def matches(self, request: ExecutionRequest) -> bool:
        planned = request.soft_hints.get("planned_ai_unit_id")
        return (
            isinstance(planned, str)
            and planned in self.target_planned_ai_unit_ids
        )

    @property
    def termination_limit(self) -> int:
        return (
            len(self.target_planned_ai_unit_ids)
            if self.termination_count_target is None
            else self.termination_count_target
        )

    @property
    def target_progress_ratio(self) -> float:
        return int(self.kill_point.removeprefix("progress_")) / 100.0


@dataclass(frozen=True, kw_only=True)
class PreparedTraceDelivery:
    """Child形成、只能由parent提交的trace delivery值。"""

    schema_version: str
    current_run_id: str
    task_id: str
    unit_id: str
    attempt_id: str
    attempt_ordinal: int
    binding_digest: str
    inference_request_digest: str
    bank_root_id: str
    manifest_digest: str
    entry_id: str
    source_terminal_kind: Literal["success", "provider_failure"]
    source_bank_object_locators: tuple[Mapping[str, object], ...]
    logical_start_ms: int
    source_latency_ms: int
    logical_finish_ms: int
    parser_input_media_type: str
    parser_input_digest: str
    child_worker_id: str
    child_completion_sequence: int
    delivery_digest: str

    def __post_init__(self) -> None:
        if self.schema_version != "PreparedTraceDelivery.v1":
            raise ValueError("unsupported prepared trace delivery schema")
        for field_name in (
            "current_run_id",
            "task_id",
            "unit_id",
            "attempt_id",
            "bank_root_id",
            "entry_id",
            "parser_input_media_type",
            "child_worker_id",
        ):
            _require_non_empty_string(field_name, getattr(self, field_name))
        for field_name in (
            "binding_digest",
            "inference_request_digest",
            "manifest_digest",
            "parser_input_digest",
        ):
            _require_sha256_digest(field_name, getattr(self, field_name))
        for field_name in (
            "attempt_ordinal",
            "logical_start_ms",
            "source_latency_ms",
            "logical_finish_ms",
            "child_completion_sequence",
        ):
            _require_exact_int(field_name, getattr(self, field_name), minimum=0)
        if self.source_terminal_kind not in {"success", "provider_failure"}:
            raise ValueError("source_terminal_kind is unsupported")
        if self.logical_finish_ms != self.logical_start_ms + self.source_latency_ms:
            raise ValueError("logical_finish_ms must equal start plus latency")
        locators = _freeze_trace_locators(
            self.source_bank_object_locators,
            bank_root_id=self.bank_root_id,
            manifest_digest=self.manifest_digest,
            entry_id=self.entry_id,
        )
        object.__setattr__(self, "source_bank_object_locators", locators)
        expected = _json_digest(self._digest_body())
        if self.delivery_digest != expected:
            raise ValueError("prepared trace delivery_digest mismatch")

    def __reduce__(self):
        """跨process时通过规范JSON形态重建，避免序列化只读mapping view。"""

        return (type(self).from_dict, (self.to_dict(),))

    @classmethod
    def create(
        cls,
        *,
        current_run_id: str,
        task_id: str,
        unit_id: str,
        attempt_id: str,
        attempt_ordinal: int,
        binding_digest: str,
        inference_request_digest: str,
        bank_root_id: str,
        manifest_digest: str,
        entry_id: str,
        source_terminal_kind: Literal["success", "provider_failure"],
        source_bank_object_locators: tuple[Mapping[str, object], ...],
        logical_start_ms: int,
        source_latency_ms: int,
        parser_input_media_type: str,
        parser_input_digest: str,
        child_worker_id: str,
        child_completion_sequence: int,
    ) -> "PreparedTraceDelivery":
        locators = _freeze_trace_locators(
            source_bank_object_locators,
            bank_root_id=bank_root_id,
            manifest_digest=manifest_digest,
            entry_id=entry_id,
        )
        body: JsonObject = {
            "schema_version": "PreparedTraceDelivery.v1",
            "current_run_id": current_run_id,
            "task_id": task_id,
            "unit_id": unit_id,
            "attempt_id": attempt_id,
            "attempt_ordinal": attempt_ordinal,
            "binding_digest": binding_digest,
            "inference_request_digest": inference_request_digest,
            "bank_root_id": bank_root_id,
            "manifest_digest": manifest_digest,
            "entry_id": entry_id,
            "source_terminal_kind": source_terminal_kind,
            "source_bank_object_locators": [dict(item) for item in locators],
            "logical_start_ms": logical_start_ms,
            "source_latency_ms": source_latency_ms,
            "logical_finish_ms": logical_start_ms + source_latency_ms,
            "parser_input_media_type": parser_input_media_type,
            "parser_input_digest": parser_input_digest,
            "child_worker_id": child_worker_id,
            "child_completion_sequence": child_completion_sequence,
        }
        constructor = dict(body)
        constructor["source_bank_object_locators"] = locators
        return cls(**constructor, delivery_digest=_json_digest(body))

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "PreparedTraceDelivery":
        expected = {
            "schema_version",
            "current_run_id",
            "task_id",
            "unit_id",
            "attempt_id",
            "attempt_ordinal",
            "binding_digest",
            "inference_request_digest",
            "bank_root_id",
            "manifest_digest",
            "entry_id",
            "source_terminal_kind",
            "source_bank_object_locators",
            "logical_start_ms",
            "source_latency_ms",
            "logical_finish_ms",
            "parser_input_media_type",
            "parser_input_digest",
            "child_worker_id",
            "child_completion_sequence",
            "delivery_digest",
        }
        _require_exact_keys(value, expected, "prepared trace delivery")
        body = dict(value)
        body["source_bank_object_locators"] = tuple(
            body["source_bank_object_locators"]
        )
        return cls(**body)

    def _digest_body(self) -> JsonObject:
        body = self.to_dict()
        body.pop("delivery_digest")
        return body

    def with_child_completion(
        self,
        *,
        child_worker_id: str,
        child_completion_sequence: int,
    ) -> "PreparedTraceDelivery":
        return self.create(
            current_run_id=self.current_run_id,
            task_id=self.task_id,
            unit_id=self.unit_id,
            attempt_id=self.attempt_id,
            attempt_ordinal=self.attempt_ordinal,
            binding_digest=self.binding_digest,
            inference_request_digest=self.inference_request_digest,
            bank_root_id=self.bank_root_id,
            manifest_digest=self.manifest_digest,
            entry_id=self.entry_id,
            source_terminal_kind=self.source_terminal_kind,
            source_bank_object_locators=self.source_bank_object_locators,
            logical_start_ms=self.logical_start_ms,
            source_latency_ms=self.source_latency_ms,
            parser_input_media_type=self.parser_input_media_type,
            parser_input_digest=self.parser_input_digest,
            child_worker_id=child_worker_id,
            child_completion_sequence=child_completion_sequence,
        )

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "current_run_id": self.current_run_id,
            "task_id": self.task_id,
            "unit_id": self.unit_id,
            "attempt_id": self.attempt_id,
            "attempt_ordinal": self.attempt_ordinal,
            "binding_digest": self.binding_digest,
            "inference_request_digest": self.inference_request_digest,
            "bank_root_id": self.bank_root_id,
            "manifest_digest": self.manifest_digest,
            "entry_id": self.entry_id,
            "source_terminal_kind": self.source_terminal_kind,
            "source_bank_object_locators": [
                dict(item) for item in self.source_bank_object_locators
            ],
            "logical_start_ms": self.logical_start_ms,
            "source_latency_ms": self.source_latency_ms,
            "logical_finish_ms": self.logical_finish_ms,
            "parser_input_media_type": self.parser_input_media_type,
            "parser_input_digest": self.parser_input_digest,
            "child_worker_id": self.child_worker_id,
            "child_completion_sequence": self.child_completion_sequence,
            "delivery_digest": self.delivery_digest,
        }


@dataclass(frozen=True, kw_only=True)
class TraceDeliveryAttempt:
    attempt_id: str
    binding_digest: str
    status: Literal["started", "killed", "fenced"]
    event_ref: JsonObject

    def __post_init__(self) -> None:
        _require_non_empty_string("attempt_id", self.attempt_id)
        _require_sha256_digest("binding_digest", self.binding_digest)
        if self.status not in {"started", "killed", "fenced"}:
            raise ValueError("trace delivery attempt status is unsupported")
        _require_exact_keys(
            self.event_ref,
            {"event_id", "event_seq", "event_type", "event_hash"},
            "trace delivery attempt event_ref",
        )
        _require_non_empty_string("event_ref.event_id", self.event_ref["event_id"])
        _require_exact_int("event_ref.event_seq", self.event_ref["event_seq"], minimum=1)
        _require_non_empty_string(
            "event_ref.event_type", self.event_ref["event_type"]
        )
        _require_sha256_digest("event_ref.event_hash", self.event_ref["event_hash"])

    def to_dict(self) -> JsonObject:
        return {
            "attempt_id": self.attempt_id,
            "binding_digest": self.binding_digest,
            "status": self.status,
            "event_ref": dict(self.event_ref),
        }


@dataclass(frozen=True, kw_only=True)
class TraceConsumptionCore:
    attempt_id: str
    binding_digest: str
    current_fencing_token: str
    status: Literal["delivered"]
    current_wrapper_ref: ArtifactRef
    parser_input_ref: ArtifactRef
    parser_result_ref: ArtifactRef
    current_provenance_ref: ArtifactRef
    verifier_checker_refs: tuple[ArtifactRef, ...]
    canonical_ref: ArtifactRef | None
    trace_attribution_refs: tuple[ArtifactRef, ...]

    def to_dict(self) -> JsonObject:
        return {
            "attempt_id": self.attempt_id,
            "binding_digest": self.binding_digest,
            "current_fencing_token": self.current_fencing_token,
            "status": self.status,
            "current_wrapper_ref": self.current_wrapper_ref.to_dict(),
            "parser_input_ref": self.parser_input_ref.to_dict(),
            "parser_result_ref": self.parser_result_ref.to_dict(),
            "current_provenance_ref": self.current_provenance_ref.to_dict(),
            "verifier_checker_refs": [
                ref.to_dict() for ref in self.verifier_checker_refs
            ],
            "canonical_ref": (
                None if self.canonical_ref is None else self.canonical_ref.to_dict()
            ),
            "trace_attribution_refs": [
                ref.to_dict() for ref in self.trace_attribution_refs
            ],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "TraceConsumptionCore":
        expected = {
            "attempt_id",
            "binding_digest",
            "current_fencing_token",
            "status",
            "current_wrapper_ref",
            "parser_input_ref",
            "parser_result_ref",
            "current_provenance_ref",
            "verifier_checker_refs",
            "canonical_ref",
            "trace_attribution_refs",
        }
        _require_exact_keys(value, expected, "trace consumption core")
        canonical = value["canonical_ref"]
        return cls(
            attempt_id=value["attempt_id"],
            binding_digest=value["binding_digest"],
            current_fencing_token=value["current_fencing_token"],
            status=value["status"],
            current_wrapper_ref=ArtifactRef.from_dict(dict(value["current_wrapper_ref"])),
            parser_input_ref=ArtifactRef.from_dict(dict(value["parser_input_ref"])),
            parser_result_ref=ArtifactRef.from_dict(dict(value["parser_result_ref"])),
            current_provenance_ref=ArtifactRef.from_dict(
                dict(value["current_provenance_ref"])
            ),
            verifier_checker_refs=tuple(
                ArtifactRef.from_dict(dict(item))
                for item in value["verifier_checker_refs"]
            ),
            canonical_ref=(
                None if canonical is None else ArtifactRef.from_dict(dict(canonical))
            ),
            trace_attribution_refs=tuple(
                ArtifactRef.from_dict(dict(item))
                for item in value["trace_attribution_refs"]
            ),
        )


@dataclass(frozen=True, kw_only=True)
class TraceConsumptionRecord:
    core: TraceConsumptionCore
    committed_event_ref: JsonObject


@dataclass(frozen=True, kw_only=True)
class ParentTraceDeliveryStageContext:
    """parent domain stager 可见的当前运行上下文；不携带外部 root path。"""

    delivery: PreparedTraceDelivery
    request: ExecutionRequest
    artifact_store: ArtifactStore
    current_wrapper_ref: ArtifactRef
    parser_input_ref: ArtifactRef
    created_at: str


@dataclass(frozen=True, kw_only=True)
class ParentStagedTraceDelivery:
    """插件/adapter stage 后交回协议 parent 的 core-neutral artifact refs。"""

    parser_result_ref: ArtifactRef
    current_provenance_ref: ArtifactRef
    verifier_checker_refs: tuple[ArtifactRef, ...]
    canonical_ref: ArtifactRef | None
    trace_attribution_refs: tuple[ArtifactRef, ...]

    def __post_init__(self) -> None:
        for field_name in ("parser_result_ref", "current_provenance_ref"):
            if not isinstance(getattr(self, field_name), ArtifactRef):
                raise TypeError(f"{field_name} must be an ArtifactRef")
        if self.canonical_ref is not None and not isinstance(
            self.canonical_ref, ArtifactRef
        ):
            raise TypeError("canonical_ref must be an ArtifactRef or None")
        for field_name in ("verifier_checker_refs", "trace_attribution_refs"):
            refs = getattr(self, field_name)
            if not isinstance(refs, tuple) or any(
                not isinstance(ref, ArtifactRef) for ref in refs
            ):
                raise TypeError(f"{field_name} must be a tuple of ArtifactRef")
        if not self.trace_attribution_refs:
            raise ValueError("trace_attribution_refs must be non-empty")


class ParentTraceDeliveryStager(Protocol):
    """由 adapter 注入的 parent-only parser/checker/canonical staging boundary。"""

    def stage(
        self,
        context: ParentTraceDeliveryStageContext,
    ) -> ParentStagedTraceDelivery: ...


@dataclass(frozen=True, kw_only=True)
class ParentCommitStores:
    artifact_store: ArtifactStore
    event_ledger: object
    sqlite_index: "SQLiteMaterializedIndex | None" = None
    commit_hook: Callable[[str], None] | None = None
    trace_delivery_stager: ParentTraceDeliveryStager | None = None


class WorkerBackend(Protocol):
    """只冻结 worker 容量和同步执行边界，不规定并发实现。"""

    @property
    def capacity(self) -> int: ...

    def execute(self, request: ExecutionRequest) -> ExecutionSubmission: ...


@dataclass(frozen=True, kw_only=True)
class WorkerCompletionSchedule:
    """worker 返回给 parent scheduler 的纯 timing 输入。"""

    source_latency_ms: int
    attempt_ordinal: int
    event_priority: int = 10
    event_kind: Literal["worker_completion"] = "worker_completion"

    def __post_init__(self) -> None:
        for field_name in (
            "source_latency_ms",
            "attempt_ordinal",
            "event_priority",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{field_name} must be an integer")
            if value < 0:
                raise ValueError(f"{field_name} must be non-negative")
        if self.event_kind != "worker_completion":
            raise ValueError("worker completion schedule kind must be worker_completion")


@dataclass(frozen=True, kw_only=True)
class ProtocolRunRequest:
    """启动一个本地协议 root run 所需的领域无关输入。"""

    run_id: str
    root_input: object
    plugin_runtime: ProtocolTaskPluginRuntime
    worker_backend: WorkerBackend
    mechanism_policy: ProtocolMechanismPolicy = field(
        default_factory=ProtocolMechanismPolicy
    )
    hooks: RuntimeHooks = field(default_factory=NoOpRuntimeHooks)
    continue_after_terminal_child_failure: bool = False
    execution_scope: ProtocolExecutionScope = field(
        default_factory=ProtocolExecutionScope
    )
    trace_delay_policy: str | None = None
    logical_scheduler: "LogicalSourceLatencyScheduler | None" = None

    def __post_init__(self) -> None:
        if self.trace_delay_policy is None:
            if self.logical_scheduler is not None:
                raise ValueError(
                    "logical_scheduler requires logical_source_latency_1x policy"
                )
            return
        if self.trace_delay_policy == "logical_source_latency_1x":
            if self.logical_scheduler is None:
                raise ValueError(
                    "logical_source_latency_1x requires a logical scheduler"
                )
            return
        if self.trace_delay_policy == "online_real_time":
            if self.logical_scheduler is not None:
                raise ValueError("online_real_time cannot use a logical scheduler")
            return
        raise ValueError("unsupported protocol run timing policy")


@dataclass(frozen=True, kw_only=True)
class ProtocolRunLedgerBinding:
    """把 run/task/root 身份绑定到一次已验证的 producer ledger 快照。"""

    run_id: str
    task_id: str
    root_unit_id: str
    ledger_bytes_digest: str
    ledger_events_digest: str
    event_count: int
    tip_event_seq: int | None
    tip_event_id: str | None
    tip_event_hash: str | None
    binding_digest: str
    schema_version: str = "tokenshare.protocol_run_ledger_binding.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "tokenshare.protocol_run_ledger_binding.v1":
            raise ValueError("unsupported protocol run ledger binding schema")
        for field_name in ("run_id", "task_id", "root_unit_id"):
            _require_non_empty_string(field_name, getattr(self, field_name))
        _require_sha256_digest("ledger_bytes_digest", self.ledger_bytes_digest)
        _require_sha256_digest("ledger_events_digest", self.ledger_events_digest)
        if isinstance(self.event_count, bool) or not isinstance(self.event_count, int):
            raise TypeError("event_count must be an integer")
        if self.event_count < 0:
            raise ValueError("event_count must be non-negative")
        if self.event_count == 0:
            if any(
                value is not None
                for value in (
                    self.tip_event_seq,
                    self.tip_event_id,
                    self.tip_event_hash,
                )
            ):
                raise ValueError("empty ledger binding cannot have a tip")
        else:
            if type(self.tip_event_seq) is not int:
                raise TypeError("tip_event_seq must be an integer")
            if self.tip_event_seq <= 0:
                raise ValueError("tip_event_seq must be positive")
            if self.tip_event_seq != self.event_count:
                raise ValueError("tip_event_seq must equal event_count")
            _require_non_empty_string("tip_event_id", self.tip_event_id)
            _require_sha256_digest("tip_event_hash", self.tip_event_hash)
        _require_sha256_digest("binding_digest", self.binding_digest)
        if self.binding_digest != self.recompute_binding_digest():
            raise ValueError("protocol run ledger binding_digest mismatch")

    @classmethod
    def from_verified_snapshot(
        cls,
        *,
        run_id: str,
        task_id: str,
        root_unit_id: str,
        verified_snapshot: VerifiedLedgerSnapshot,
    ) -> "ProtocolRunLedgerBinding":
        if not isinstance(verified_snapshot, VerifiedLedgerSnapshot):
            raise TypeError("verified_snapshot must be VerifiedLedgerSnapshot")
        body = {
            "schema_version": "tokenshare.protocol_run_ledger_binding.v1",
            "run_id": run_id,
            "task_id": task_id,
            "root_unit_id": root_unit_id,
            "ledger_bytes_digest": verified_snapshot.ledger_bytes_digest,
            "ledger_events_digest": verified_snapshot.ledger_events_digest,
            "event_count": verified_snapshot.event_count,
            "tip_event_seq": verified_snapshot.tip_event_seq,
            "tip_event_id": verified_snapshot.tip_event_id,
            "tip_event_hash": verified_snapshot.tip_event_hash,
        }
        return cls(**body, binding_digest=_json_digest(body))

    def _digest_body(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "root_unit_id": self.root_unit_id,
            "ledger_bytes_digest": self.ledger_bytes_digest,
            "ledger_events_digest": self.ledger_events_digest,
            "event_count": self.event_count,
            "tip_event_seq": self.tip_event_seq,
            "tip_event_id": self.tip_event_id,
            "tip_event_hash": self.tip_event_hash,
        }

    def recompute_binding_digest(self) -> str:
        return _json_digest(self._digest_body())

    def to_dict(self) -> JsonObject:
        return {**self._digest_body(), "binding_digest": self.binding_digest}


@dataclass(frozen=True, kw_only=True)
class ProtocolRunResult:
    """运行结果只携带权威引用和派生摘要，不充当状态机。"""

    run_id: str
    task_id: str | None = None
    root_unit_id: str | None = None
    status: str = "pending"
    event_refs: tuple[JsonObject, ...] = ()
    artifact_refs: tuple[ArtifactRef, ...] = ()
    summary: JsonObject = field(default_factory=dict)
    ledger_binding: ProtocolRunLedgerBinding | None = None

    def __post_init__(self) -> None:
        binding = self.ledger_binding
        if binding is None:
            return
        if not isinstance(binding, ProtocolRunLedgerBinding):
            raise TypeError("ledger_binding must be ProtocolRunLedgerBinding")
        if (
            binding.run_id != self.run_id
            or binding.task_id != self.task_id
            or binding.root_unit_id != self.root_unit_id
        ):
            raise ValueError("protocol run ledger binding identity mismatch")


def _merge_readiness_digest(body: JsonObject) -> str:
    return _json_digest(body)


def _freeze_trace_locators(
    value: object,
    *,
    bank_root_id: str,
    manifest_digest: str,
    entry_id: str,
) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("source_bank_object_locators must be non-empty")
    keys = {
        "bank_root_id",
        "manifest_digest",
        "entry_id",
        "object_role",
        "object_digest",
    }
    normalized: list[Mapping[str, object]] = []
    seen_roles: set[str] = set()
    for index, item in enumerate(value):
        _require_exact_keys(item, keys, f"source_bank_object_locators[{index}]")
        body = dict(item)
        role = body["object_role"]
        _require_non_empty_string(f"source_bank_object_locators[{index}].object_role", role)
        if role in seen_roles:
            raise ValueError("source bank object locator roles must be unique")
        seen_roles.add(role)
        if (
            body["bank_root_id"] != bank_root_id
            or body["manifest_digest"] != manifest_digest
            or body["entry_id"] != entry_id
        ):
            raise ValueError("source bank object locator identity mismatch")
        _require_sha256_digest(
            f"source_bank_object_locators[{index}].object_digest",
            body["object_digest"],
        )
        normalized.append(MappingProxyType(body))
    return tuple(sorted(normalized, key=lambda item: str(item["object_role"])))


def _json_digest(body: JsonObject) -> str:
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


def _require_non_empty_string(field_name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_sha256_digest(field_name: str, value: object) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{field_name} must be a sha256 digest")


def _require_exact_keys(
    value: object,
    expected: set[str],
    field_name: str,
) -> None:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError(f"{field_name} must have exact keys")


def _require_exact_bool(field_name: str, value: object) -> None:
    if type(value) is not bool:
        raise TypeError(f"{field_name} must be an exact bool")


def _require_runtime_observation_tuple(value: object) -> None:
    if not isinstance(value, tuple) or any(
        not isinstance(item, RuntimeHookObservationV1) for item in value
    ):
        raise TypeError(
            "experiment_records must be a tuple of RuntimeHookObservationV1"
        )


def _require_exact_int(field_name: str, value: object, *, minimum: int) -> None:
    if type(value) is not int or value < minimum:
        raise TypeError(f"{field_name} must be an exact int >= {minimum}")


def _freeze_non_empty_strings(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{field_name} must be a list or tuple")
    result = tuple(value)
    for index, item in enumerate(result):
        _require_non_empty_string(f"{field_name}[{index}]", item)
    return result


def _freeze_event_refs(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, (list, tuple)):
        raise TypeError("protocol_event_refs must be a list or tuple")
    result: list[Mapping[str, object]] = []
    basic_keys = {"event_id", "event_seq", "event_type"}
    hashed_keys = {"event_id", "event_seq", "event_type", "event_hash"}
    for index, ref in enumerate(value):
        keys = set(ref) if isinstance(ref, Mapping) else set()
        if not isinstance(ref, Mapping) or (
            keys != basic_keys and keys != hashed_keys
        ):
            raise ValueError(
                f"protocol_event_refs[{index}] must have exact keys"
            )
        _require_non_empty_string(
            f"protocol_event_refs[{index}].event_id", ref["event_id"]
        )
        _require_exact_int(
            f"protocol_event_refs[{index}].event_seq",
            ref["event_seq"],
            minimum=1,
        )
        body: dict[str, object] = {
            "event_id": ref["event_id"],
            "event_seq": ref["event_seq"],
            "event_type": ref["event_type"],
        }
        _require_non_empty_string(
            f"protocol_event_refs[{index}].event_type", ref["event_type"]
        )
        if keys == hashed_keys:
            _require_sha256_digest(
                f"protocol_event_refs[{index}].event_hash", ref["event_hash"]
            )
            body.update(
                event_hash=ref["event_hash"],
            )
        result.append(MappingProxyType(body))
    return tuple(result)


def _event_refs_to_json(
    refs: tuple[Mapping[str, object], ...],
) -> list[JsonObject]:
    return [{str(key): _thaw_json(value) for key, value in ref.items()} for ref in refs]


def _freeze_artifact_refs(
    value: object,
    *,
    require_non_empty: bool = False,
) -> tuple[ArtifactRef, ...]:
    if not isinstance(value, (list, tuple)):
        raise TypeError("artifact_refs must be a list or tuple")
    if require_non_empty and not value:
        raise ValueError("artifact_refs must be non-empty")
    return tuple(
        _strict_artifact_ref(ref, f"artifact_refs[{index}]")
        for index, ref in enumerate(value)
    )


def _strict_artifact_ref(value: object, field_name: str) -> ArtifactRef:
    if isinstance(value, ArtifactRef):
        body = _artifact_ref_to_json(value)
    elif isinstance(value, Mapping):
        body = dict(value)
    else:
        raise TypeError(f"{field_name} must be an ArtifactRef or object")
    keys = {
        "schema_version",
        "artifact_id",
        "artifact_type",
        "uri",
        "content_hash",
        "size_bytes",
        "media_type",
        "artifact_schema_id",
        "artifact_schema_version",
        "source",
        "metadata",
        "created_at",
    }
    _require_exact_keys(body, keys, field_name)
    if body["schema_version"] != "ArtifactRef.v1":
        raise ValueError(f"{field_name}.schema_version is unsupported")
    for key in (
        "artifact_id",
        "artifact_type",
        "uri",
        "media_type",
        "artifact_schema_id",
        "artifact_schema_version",
        "created_at",
    ):
        _require_non_empty_string(f"{field_name}.{key}", body[key])
    _require_sha256_digest(f"{field_name}.content_hash", body["content_hash"])
    _require_exact_int(f"{field_name}.size_bytes", body["size_bytes"], minimum=0)
    if not isinstance(body["source"], Mapping):
        raise TypeError(f"{field_name}.source must be an object")
    if not isinstance(body["metadata"], Mapping):
        raise TypeError(f"{field_name}.metadata must be an object")
    source = _freeze_json(body["source"], f"{field_name}.source")
    metadata = _freeze_json(body["metadata"], f"{field_name}.metadata")
    return ArtifactRef(
        artifact_id=body["artifact_id"],
        artifact_type=body["artifact_type"],
        uri=body["uri"],
        content_hash=body["content_hash"],
        size_bytes=body["size_bytes"],
        media_type=body["media_type"],
        artifact_schema_id=body["artifact_schema_id"],
        artifact_schema_version=body["artifact_schema_version"],
        source=source,
        metadata=metadata,
        created_at=body["created_at"],
        schema_version="ArtifactRef.v1",
    )


def _artifact_ref_to_json(ref: ArtifactRef) -> JsonObject:
    return {
        "schema_version": ref.schema_version,
        "artifact_id": ref.artifact_id,
        "artifact_type": ref.artifact_type,
        "uri": ref.uri,
        "content_hash": ref.content_hash,
        "size_bytes": ref.size_bytes,
        "media_type": ref.media_type,
        "artifact_schema_id": ref.artifact_schema_id,
        "artifact_schema_version": ref.artifact_schema_version,
        "source": _thaw_json(ref.source),
        "metadata": _thaw_json(ref.metadata),
        "created_at": ref.created_at,
    }


def _artifact_refs_to_json(refs: tuple[ArtifactRef, ...]) -> list[JsonObject]:
    return [_artifact_ref_to_json(ref) for ref in refs]


def _freeze_hook_input(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError("hook_input must be an object")
    keys = set(value)
    identity_keys = {"task_id", "unit_id", "attempt_id", "lease_id"}
    recovery_keys = {*identity_keys, "trigger"}
    merge_keys = {"gate_satisfied", "required_child_unit_ids"}
    if keys == identity_keys:
        body = dict(value)
        for key in identity_keys:
            _require_non_empty_string(f"hook_input.{key}", body[key])
    elif keys == recovery_keys:
        body = dict(value)
        for key in recovery_keys:
            _require_non_empty_string(f"hook_input.{key}", body[key])
    elif keys == merge_keys:
        body = dict(value)
        _require_exact_bool("hook_input.gate_satisfied", body["gate_satisfied"])
        body["required_child_unit_ids"] = _freeze_non_empty_strings(
            body["required_child_unit_ids"],
            "hook_input.required_child_unit_ids",
        )
    else:
        raise ValueError("hook_input must have exact keys")
    return MappingProxyType(body)


def _freeze_hook_result(value: object) -> Mapping[str, bool]:
    _require_exact_keys(value, {"bypass", "stop"}, "hook_result")
    body = dict(value)
    _require_exact_bool("hook_result.bypass", body["bypass"])
    _require_exact_bool("hook_result.stop", body["stop"])
    return MappingProxyType(body)


def _freeze_json(value: object, field_name: str) -> object:
    if value is None or type(value) is bool or type(value) is int:
        return value
    if type(value) is float:
        if not isfinite(value):
            raise ValueError(f"{field_name} float must be finite")
        return value
    if isinstance(value, str):
        _require_non_empty_string(field_name, value)
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            _require_non_empty_string(f"{field_name} key", key)
            frozen[key] = _freeze_json(item, f"{field_name}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_json(item, f"{field_name}[{index}]")
            for index, item in enumerate(value)
        )
    raise TypeError(f"{field_name} contains a non-JSON value")


def _thaw_json(value: object) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value
