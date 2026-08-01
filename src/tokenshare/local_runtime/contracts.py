"""本地协议运行时的最小稳定契约。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Callable, Protocol

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


@dataclass(frozen=True, kw_only=True)
class RawOutputDirective:
    """只改变 parser 输入或声明受控 result kind，不写协议状态。"""

    content_text: str | None = None
    result_kind: str | None = None
    experiment_records: tuple[JsonObject, ...] = ()


@dataclass(frozen=True, kw_only=True)
class ParsedCandidateDirective:
    """只替换 verifier 将读取的 candidate refs，并返回实验观察。"""

    replacement_candidate_output_refs: dict[str, ArtifactRef]
    experiment_records: tuple[JsonObject, ...] = ()


@dataclass(frozen=True, kw_only=True)
class GateDirective:
    """稳定 gate 的窄指令；状态推进仍由 coordinator/engine 完成。"""

    bypass: bool = False
    stop: bool = False
    replacement: Any | None = None
    experiment_records: tuple[JsonObject, ...] = ()


@dataclass(frozen=True, kw_only=True)
class WorkerDirective:
    """worker progress 点的窄指令；不包含协议 state mutation。"""

    action: str
    worker_id: str | None = None
    experiment_records: tuple[JsonObject, ...] = ()


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


class WorkerBackend(Protocol):
    """只冻结 worker 容量和同步执行边界，不规定并发实现。"""

    @property
    def capacity(self) -> int: ...

    def execute(self, request: ExecutionRequest) -> ExecutionSubmission: ...


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
