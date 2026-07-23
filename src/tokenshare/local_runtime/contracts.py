"""本地协议运行时的最小稳定契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
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
from tokenshare.storage.events import LedgerEvent


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
    ) -> MergeAction:
        """从 canonical child 集合构造插件拥有的 merge action。"""
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
    lease_deadline_at: str | None = None


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
    """Experiment 4 六种模式共享的机制 gate；默认值等价于 FULL。"""

    parser_policy_enabled: bool = True
    verification_enabled: bool = True
    replacement_attempts_allowed: bool = True
    merge_gate_enabled: bool = True
    slot_integrity_enabled: bool = True


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
