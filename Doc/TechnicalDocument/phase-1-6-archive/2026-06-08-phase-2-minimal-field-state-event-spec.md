# Phase 2 最小字段、状态与事件规格

## 元数据

| 项目 | 内容 |
|---|---|
| 日期 | 2026-06-08 |
| 状态 | Draft |
| 对应 feature | `feat-003` - Phase 2 - Task Graph, State Machines, and Scheduling |
| 上游设计依据 | `Doc/TechnicalDocument/2026-06-03-tokenshare-protocol-technical-design.md`、`Doc/TechnicalDocument/2026-06-05-phase-1-minimal-object-field-spec.md`、`Doc/TechnicalDocument/2026-06-07-phase-2-coordination-debt-memo.md` |
| 本地参考项目 | `reference_repos/prefect`、`reference_repos/dagster`、`reference_repos/temporalio-sdk-python`、`reference_repos/spotify-luigi`、`reference_repos/cwltool` |
| 目标读者 | 后续实现 `TaskGraph`、状态机、调度、租约和事件投影的 agent |

## 1. 背景

Phase 1 已经完成协议基础对象、artifact store、JSONL event ledger、SQLite 可重建索引和 root task registration。Phase 2 要把“已注册的任务”推进到“可被调度、可恢复、可从事件重放”的最小协议内核。

上一轮边界审计已经确认：`TaskUnit.state` 只能表达任务图节点生命周期，不能混入 `Lease.Active/Expired` 或 `Attempt.Verifying` 这类细粒度执行状态。`RootTaskRegistrar` 也只能保留为 Phase 1 注册入口，不能继续长成 `TaskGraph`、`Scheduler`、`LeaseManager` 和 attempt orchestration 的总入口。

因此本文的任务不是写实现代码，而是固定 Phase 2 的最小对象、字段、状态机和事件语义，让后续实现可以按测试驱动方式逐步落地。

## 2. 问题陈述

Phase 2 需要解决四个问题：

1. 如何表示任务图，使 ready node 可以由依赖和 canonical outputs 推导，而不是由插件或 executor 私自推进。
2. 如何把 `TaskUnit`、`Lease`、`Attempt` 的状态拆开建模，避免一个字段承担三种生命周期。
3. 如何让调度、租约过期、执行失败和重试都写入 append-only event ledger，并能重放到同一状态。
4. 如何为后续自然语言任务的 AI 输出、结构化解析、验证和合并预留 artifact/event 边界，但不在 Phase 2 提前实现验证或合并。

## 3. 范围

Phase 2 范围内：

- `TaskGraph` 的最小内存视图和不变量。
- `TaskUnit` 状态转移规则。
- `Lease` 协议对象和状态机。
- `Attempt` 协议对象和状态机。
- `Scheduler` 的 ready node + client capability 匹配决策。
- `LeaseManager` 的 claim、heartbeat、release、expire、revoke 语义。
- 状态变化、租约变化、attempt 变化、恢复动作的 event payload。
- SQLite materialized index 对 `leases`、`attempts`、`task_units` 当前状态的可重建投影。
Phase 2 范围外：

- 插件注册表和 executor registry 的完整实现。
- 真实 executor 调用、AI API 调用或 Lean proving。
- submission 验证、canonical output binding、task expansion、merge、settlement。
- 真实分布式调度、HTTP worker pool、P2P runtime、生产权限系统。
- 把第三方 workflow 项目引入为 runtime dependency。

## 4. 本地参考启发

这些项目只作为本地可复查参考，不进入 TokenShare runtime：

| 本地路径 | 观察点 | 对 Phase 2 的启发 |
|---|---|---|
| `reference_repos/prefect/src/prefect/concurrency/_leases.py` | lease renewal 使用 TTL fraction、重试和失败回调；续期失败后可以取消执行范围 | `Lease` 必须有 `last_heartbeat_at`、`expires_at`、`heartbeat_count` 和明确的续期失败语义；失去租约不等于 executor failure |
| `reference_repos/dagster/python_modules/dagster/dagster/_core/storage/event_log/base.py` | event log storage 是内部核心边界，状态查询由 storage id 和 event record 推导 | TokenShare 继续保持 JSONL event ledger 权威，SQLite 只做可重建投影和查询视图 |
| `reference_repos/temporalio-sdk-python` | replay 围绕 workflow history；worker/replayer 复用拦截器和配置边界 | TokenShare replay 必须只消费历史事件和 artifact refs，不能重新调用 executor 或 AI |
| `reference_repos/spotify-luigi/luigi/scheduler.py` | worker 活跃度、scheduler state、task history 与 task 本身分离 | 调度不能塞进 `TaskUnit` 或 `RootTaskRegistrar`；worker/client 活跃度属于调度输入和 lease 管理 |
| `reference_repos/cwltool/cwltool` | workflow、builder、job、path mapping 分层，typed input/output 与执行准备分开 | `TaskRelation` 应继续按 named output/input 表达依赖，执行环境和 artifact path 只通过 refs 传递 |

## 5. 设计原则

- Event-first：JSONL `EventLedger` 是权威事实源；SQLite 和 `TaskGraph` 都是可重建视图。
- 三条状态线分离：`TaskUnit` 管节点生命周期，`Lease` 管调度占用权，`Attempt` 管一次执行尝试。
- 核心纯逻辑：`tokenshare.core` 可包含协议对象、状态机和纯 `TaskGraph` 判断，但不能写文件、append JSONL、更新 SQLite 或调用 executor。
- 编排窄入口：实际把 core 决策写入 storage 的逻辑应进入独立 orchestration/engine 层，不能继续扩展 `RootTaskRegistrar`。
- AI 文本不可直接改图：自然语言输出必须先作为 artifact 保存，再由插件解析为结构化对象，最终由协议事件推进状态。
- 字段版本显式：新增对象 snapshot 和 event payload 都包含 `schema_version`。

## 6. Phase 2 对象总览

| 对象或组件 | 类别 | 主要职责 | 权威持久化 | 推荐模块落点 |
|---|---|---|---|---|
| `TaskGraph` | 纯逻辑视图 | 管理 `TaskUnit` 和 `TaskRelation` 的图查询、ready 判断、环检测和容量限制 | 不作为权威对象持久化；由 events/SQLite 重建 | `tokenshare.core.task_graph` |
| `TaskUnitStateChange` | event payload | 记录节点生命周期从 old state 到 new state 的原因和触发源 | `TASK_UNIT_STATE_CHANGED` | `tokenshare.core.state_machines` 定义规则；storage 只保存事件 |
| `Lease` | 协议对象 | 表达某 client 对某 unit 的限时执行占用权和 fencing token | `LEASE_STATE_CHANGED` | `tokenshare.core.models` 或 `tokenshare.core.leases` |
| `Attempt` | 协议对象 | 表达一次执行尝试，从创建、运行、提交、验证到 canonical/rejected/failed/superseded | `ATTEMPT_STATE_CHANGED` | `tokenshare.core.models` 或 `tokenshare.core.attempts` |
| `Scheduler` | 纯决策组件 | 从 ready units 和 available clients 中选出可 claim 的 pair | 通过后续 lease/attempt/unit events 间接持久化 | `tokenshare.core.scheduling` |
| `LeaseManager` | 纯状态组件 | claim、heartbeat、release、expire、revoke lease，并产出状态变化决策 | `LEASE_STATE_CHANGED`、`RECOVERY_ACTION_RECORDED` | `tokenshare.core.leases` |
| `SchedulingDecision` | value object / event 子 payload | 解释某次调度选择的策略、输入摘要和原因 | 嵌入 lease/attempt 事件 payload | `tokenshare.core.scheduling` |
| `RecoveryAction` | event payload | 记录 lease expiry、executor error、late submission 等触发的 retry/fail/reschedule | `RECOVERY_ACTION_RECORDED` | `tokenshare.core.recovery` 或 `tokenshare.core.state_machines` |
| `SQLiteMaterializedIndex` extension | 投影视图 | 查询当前 unit/lease/attempt/client 状态 | 从 event ledger 重建 | `tokenshare.storage.sqlite_index` |

## 7. 最小字段规格

### 7.1 `TaskGraph`

`TaskGraph` 是运行时视图，不是新的权威持久化对象。它的输入来自已重放的 `TaskUnit`、`TaskRelation`、未来 canonical output binding 事件，以及 `ProtocolConfig`。

最小字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task_id` | string | 是 | 所属 root task |
| `units` | map[string, TaskUnit] | 是 | 当前任务图节点，以 `unit_id` 索引 |
| `relations` | list[TaskRelation] | 是 | named output/input 依赖边 |
| `out_edges_by_unit_id` | map[string, list[TaskRelation]] | 是 | 查询优化视图，可重建 |
| `in_edges_by_unit_id` | map[string, list[TaskRelation]] | 是 | 查询优化视图，可重建 |
| `canonical_outputs_by_unit_id` | map[string, map[string, ArtifactRef]] | 否 | Phase 4 后由 canonical binding 事件重建；Phase 2 可为空 |
| `ready_unit_ids` | list[string] | 否 | 可由状态和依赖现场计算，不能作为权威字段 |
| `max_depth_observed` | integer | 否 | 用于校验 `ProtocolConfig.max_depth` |

最小不变量：

- `relations[*].source_unit_id` 和 `relations[*].target_unit_id` 必须存在于 `units`。
- `relation_type == "depends_on_output"` 的边必须指定 `source_output_name` 和 `target_input_name`。
- 同一 target input 在同一 unit 内不能被多个 source output 重复绑定，除非未来 schema 显式支持 fan-in。
- Phase 2 不要求 canonical outputs 已存在，但 ready 判断必须预留“依赖的 named output 已 canonical”的条件。
- 动态 expansion 进入 Phase 4 后，图更新必须通过 structured proposal/decision 事件原子表达；Phase 2 不允许 executor 或 raw text 直接添加节点。

### 7.2 `TaskUnitStateChange`

`TaskUnitStateChange` 不是单独表里的权威对象，而是 `TASK_UNIT_STATE_CHANGED` 的 payload。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | 初始值 `phase2.task_unit_state_change.v1` |
| `task_id` | string | 是 | 所属 root task |
| `unit_id` | string | 是 | 被推进的 unit |
| `old_state` | string/null | 是 | 创建或重放修复时可为 null |
| `new_state` | string | 是 | `TaskState` 枚举值 |
| `reason` | string | 是 | 机器可读原因，例如 `scheduled`、`lease_expired_retry`、`retry_limit_reached` |
| `trigger` | string | 是 | `scheduler`、`lease_manager`、`recovery`、`manual_cancel`、`replay_repair` |
| `correlation_id` | string | 是 | 把同一次调度或恢复的多个事件串起来 |
| `causation_event_id` | string/null | 否 | 触发本次变化的上游事件 |
| `changed_at` | string | 是 | UTC ISO 8601 |
| `state_context` | object | 否 | 当前 active lease/attempt 摘要、retry count、依赖摘要，不放大文本 |

### 7.3 `Lease`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | 初始值 `phase2.lease.v1` |
| `lease_id` | string | 是 | 全局唯一 |
| `task_id` | string | 是 | 所属 root task |
| `unit_id` | string | 是 | 被租约占用的 unit |
| `attempt_id` | string | 是 | 该 lease 授权的 attempt |
| `client_id` | string | 是 | 被授权的 client/executor |
| `state` | string | 是 | `Active`、`Released`、`Expired`、`Revoked` |
| `fencing_token` | string | 是 | 用于拒绝迟到或旧 lease submission |
| `issued_at` | string | 是 | UTC ISO 8601 |
| `expires_at` | string | 是 | UTC ISO 8601；由 `lease_ttl_seconds` 推导 |
| `last_heartbeat_at` | string/null | 否 | 最近 heartbeat 成功时间 |
| `heartbeat_count` | integer | 是 | 初始 0；每次成功 heartbeat 增加 |
| `lease_kind` | string | 是 | `primary`、`retry`、`shadow` |
| `terminated_at` | string/null | 否 | release/expire/revoke 时间 |
| `terminated_reason` | string/null | 否 | `completed`、`lease_expired`、`client_revoked`、`manual_cancel` 等 |
| `metadata` | object | 否 | 小型结构化扩展，不放 executor 大日志 |

### 7.4 `Attempt`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | 初始值 `phase2.attempt.v1` |
| `attempt_id` | string | 是 | 全局唯一 |
| `task_id` | string | 是 | 所属 root task |
| `unit_id` | string | 是 | 被执行的 unit |
| `lease_id` | string | 是 | 授权本次 attempt 的 lease |
| `client_id` | string | 是 | 执行方 |
| `state` | string | 是 | `Created`、`Running`、`Submitted`、`Verifying`、`Verified`、`Canonical`、`Rejected`、`Failed`、`Superseded` |
| `attempt_kind` | string | 是 | `primary`、`retry`、`shadow` |
| `created_at` | string | 是 | UTC ISO 8601 |
| `started_at` | string/null | 否 | 真正开始执行的时间 |
| `submitted_at` | string/null | 否 | submission 到达时间 |
| `finished_at` | string/null | 否 | terminal attempt state 时间 |
| `environment_summary` | object | 否 | executor name/version、plugin id/version、runtime summary；不放 secrets |
| `input_artifact_refs` | map[string, ArtifactRef] | 否 | 执行输入快照，Phase 3 后填充 |
| `raw_output_ref` | ArtifactRef/null | 否 | AI 或 executor 原始输出 artifact；自然语言必须先落 artifact |
| `parsed_output_ref` | ArtifactRef/null | 否 | 结构化解析结果 artifact |
| `candidate_output_refs` | map[string, ArtifactRef] | 否 | 候选输出 bundle；Phase 4 验证后才能 canonical |
| `log_ref` | ArtifactRef/null | 否 | 执行日志引用 |
| `failure_kind` | string/null | 否 | `offline`、`slow`、`executor_error`、`invalid_output`、`late_submission` 等 |
| `failure_reason` | string/null | 否 | 简短机器可读原因或安全截断摘要 |
| `superseded_by_attempt_id` | string/null | 否 | 被更新 attempt 取代时填充 |
| `metadata` | object | 否 | 小型结构化扩展 |

设计选择：lease 过期导致仍在运行的 attempt 进入 `Superseded`，不是 `Failed`。因为 executor 可能之后仍提交 late output；该 output 只能进入审计路径，不能绑定 formal output。真正 executor 崩溃或返回错误时才进入 `Failed`。

### 7.5 `SchedulingDecision`

`SchedulingDecision` 初期不需要单独 event type。它作为结构化子 payload 嵌入 `LEASE_STATE_CHANGED` 和 `ATTEMPT_STATE_CHANGED`，解释为什么一个 ready unit 被分配给某 client。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | 初始值 `phase2.scheduling_decision.v1` |
| `decision_id` | string | 是 | 全局唯一，用于 correlation |
| `task_id` | string | 是 | 所属 root task |
| `unit_id` | string | 是 | 被选择的 unit |
| `client_id` | string | 是 | 被选择的 client |
| `policy_id` | string | 是 | 例如 `fifo_ready_v1` |
| `matched_capabilities` | list[string] | 是 | client 满足的能力标签 |
| `lease_kind` | string | 是 | `primary`、`retry`、`shadow` |
| `reason` | string | 是 | `ready_and_available`、`retry_allowed`、`shadow_after_elapsed` |
| `created_at` | string | 是 | UTC ISO 8601 |
| `input_summary` | object | 否 | ready queue size、client active lease count 等小型摘要 |

### 7.6 `RecoveryAction`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | 初始值 `phase2.recovery_action.v1` |
| `recovery_action_id` | string | 是 | 全局唯一 |
| `task_id` | string | 是 | 所属 root task |
| `unit_id` | string | 是 | 受影响 unit |
| `trigger` | string | 是 | `lease_expired`、`executor_error`、`invalid_output`、`late_submission`、`manual_cancel` |
| `lease_id` | string/null | 否 | 相关 lease |
| `attempt_id` | string/null | 否 | 相关 attempt |
| `old_task_state` | string/null | 否 | 恢复前 unit state |
| `new_task_state` | string/null | 否 | 恢复后 unit state |
| `retry_count` | integer | 是 | 当前 unit 已消耗 attempt/retry 数 |
| `retry_allowed` | boolean | 是 | 是否允许重新进入 Ready |
| `reason` | string | 是 | 机器可读原因 |
| `created_at` | string | 是 | UTC ISO 8601 |
| `metadata` | object | 否 | 小型扩展 |

### 7.7 Phase 2 复用和扩展的既有对象

- `TaskRelation`：继续使用 Phase 1 的 named output/input 字段，不新增“整节点依赖”语义。
- `ClientRecord`：Phase 2 可增加 `status` 相关状态事件，但不要把 active lease 列表作为权威字段写入 client 对象；active lease 由 lease events 投影。
- `ProtocolConfig`：复用 `lease_ttl_seconds`、`heartbeat_interval_seconds`、`max_retries`、`allow_shadow_execution`、`scheduling_policy`、`max_depth`、`max_children_per_unit`、`max_total_units` 等字段。Phase 2 不新增 `max_parallel_attempts_per_unit`；若未来需要显式并行 attempt 配额，应作为后续 schema 扩展，而不是硬编码常量。
- `LedgerEvent` envelope：继续沿用 Phase 1 的 event id、event type、object type/id、task id、payload hash、previous hash、idempotency key。

## 8. 状态机

### 8.1 `TaskUnit` 状态

完整枚举沿用当前 TDD 和代码中的 `TaskState`：

- `Created`
- `Blocked`
- `Ready`
- `Processing`
- `WaitingForChildren`
- `MergeReady`
- `Merging`
- `Completed`
- `MergeFailed`
- `Failed`
- `Cancelled`

Phase 2 必须实现的最小转移：

| 转移 | 触发 | 事件 | 说明 |
|---|---|---|---|
| `Created -> Ready` | root unit 或所有依赖已满足 | `TASK_UNIT_STATE_CHANGED` | Phase 1 root task 已可直接 Ready；后续 child unit 可由 dependency check 推导 |
| `Created -> Blocked` | unit 创建时存在未满足依赖 | `TASK_UNIT_STATE_CHANGED` | Phase 2 可先为图扩展预留 |
| `Blocked -> Ready` | 所有输入依赖的 named canonical output 已存在 | `TASK_UNIT_STATE_CHANGED` | Phase 4 后完整启用 |
| `Ready -> Processing` | scheduler 创建有效 lease + running attempt | `TASK_UNIT_STATE_CHANGED` | 不表示 lease 本身状态，只表示至少有有效 attempt 正在处理 |
| `Processing -> Ready` | lease expired/revoked 且 retry policy 允许，且无其他有效 attempt | `TASK_UNIT_STATE_CHANGED` | 同时记录 `RECOVERY_ACTION_RECORDED` |
| `Processing -> Failed` | retry/budget/deadline 耗尽或不可恢复错误 | `TASK_UNIT_STATE_CHANGED` | 终态 |
| `Ready/Processing/Blocked -> Cancelled` | root cancel 或人工取消 | `TASK_UNIT_STATE_CHANGED` | 终态 |

Phase 2 只保留但不完整实现的未来转移：

- `Processing -> WaitingForChildren`：Phase 4 expansion 后父节点等待 children。
- `WaitingForChildren -> MergeReady`：全部 required child canonical outputs 已就绪。
- `MergeReady -> Merging -> Completed/MergeFailed`：Phase 5 merge。
- `Processing -> Completed`：Phase 4 直接完成路径，必须等 verification/canonical binding 设计落地后再启用。

### 8.2 `Lease` 状态

| 状态 | 含义 |
|---|---|
| `Active` | client 仍持有有效 fencing token，可以提交该 lease 授权的 attempt |
| `Released` | 正常释放，例如 attempt 已提交并进入后续验证，或执行结束后不再需要占用 |
| `Expired` | 超过 `expires_at` 或 heartbeat 失败；旧 fencing token 失效 |
| `Revoked` | 协议或人工撤销，例如 client 被禁用、root cancel、资源回收 |

允许转移：

- `null -> Active`
- `Active -> Active`，仅 heartbeat，必须增加 `heartbeat_count` 并更新 `last_heartbeat_at/expires_at`
- `Active -> Released`
- `Active -> Expired`
- `Active -> Revoked`

禁止转移：

- `Expired/Released/Revoked -> Active`。重试必须创建新 lease 和新 fencing token。
- 任意 terminal lease 再 heartbeat。

### 8.3 `Attempt` 状态

| 状态 | 含义 |
|---|---|
| `Created` | attempt 已分配 id，与 lease/client/unit 绑定，但 executor 尚未开始 |
| `Running` | executor 已开始处理 |
| `Submitted` | executor 提交了 candidate refs，尚未验证 |
| `Verifying` | Phase 4 验证中 |
| `Verified` | candidate bundle 通过验证，但还未成为 canonical |
| `Canonical` | 本 attempt 的 output bundle 被绑定为正式输出 |
| `Rejected` | submission 可读但不符合 schema/plugin validation |
| `Failed` | executor 或环境失败 |
| `Superseded` | lease 失效、shadow/重试胜出或已有 canonical 导致本 attempt 不能再影响正式状态 |

Phase 2 必须实现的最小转移：

- `null -> Created`
- `Created -> Running`
- `Running -> Failed`
- `Running -> Superseded`
- `Created -> Superseded`

Phase 3/4 后启用：

- `Running -> Submitted`
- `Submitted -> Verifying`
- `Verifying -> Verified`
- `Verifying -> Rejected`
- `Verified -> Canonical`
- `Verified -> Superseded`
- `Submitted -> Rejected`

### 8.4 跨对象不变量

- 一个 `Lease` 必须绑定一个 `Attempt`，一个 `Attempt` 必须引用一个 `Lease`。
- `TaskUnit.state == Processing` 表示至少存在一个未 terminal 且 lease 仍有效的 attempt；它不记录具体 lease 状态。
- Phase 2 最小版本使用 `allow_shadow_execution=false` 表达同一 `unit_id` 最多一个 `Active` lease；未来若支持 shadow/并行 attempt，再新增显式并行配额字段。
- `Lease.Expired/Revoked/Released` 后，关联 attempt 不能再进入 `Canonical`。
- `Attempt.Canonical` 只能在 Phase 4 canonical binding 事件后出现；Phase 2 不应提前设置。
- late submission 只能记录为审计 artifact/event，不得回写 canonical output。
- SQLite 当前状态必须能从 event ledger 完整重建；不能把补丁状态只写 SQLite。

## 9. Event 规格

### 9.1 新增或启用的 event type

| Event Type | object_type | object_id | payload |
|---|---|---|---|
| `TASK_UNIT_STATE_CHANGED` | `TaskUnit` | `unit_id` | `TaskUnitStateChange` |
| `CLIENT_STATE_CHANGED` | `ClientRecord` | `client_id` | client status change payload |
| `LEASE_STATE_CHANGED` | `Lease` | `lease_id` | `{ old_state, new_state, lease, scheduling_decision?, reason, correlation_id }` |
| `ATTEMPT_STATE_CHANGED` | `Attempt` | `attempt_id` | `{ old_state, new_state, attempt, reason, correlation_id }` |
| `RECOVERY_ACTION_RECORDED` | `RecoveryAction` | `recovery_action_id` | `RecoveryAction` |

Phase 2 不新增 `SCHEDULING_DECISION_RECORDED`。原因是最小实现中调度决策必须与实际 lease/attempt 创建强绑定；如果未来需要审计未被采纳的候选队列，再引入独立 event。

### 9.2 调度 ready unit 的事件顺序

当 scheduler 将 ready unit 分配给 client 时，建议使用同一个 `correlation_id` 写入：

1. `LEASE_STATE_CHANGED`：`old_state=null`，`new_state=Active`，payload 包含 `Lease` 和 `SchedulingDecision`。
2. `ATTEMPT_STATE_CHANGED`：`old_state=null`，`new_state=Created`，payload 包含 `Attempt`。
3. `ATTEMPT_STATE_CHANGED`：`old_state=Created`，`new_state=Running`。
4. `TASK_UNIT_STATE_CHANGED`：`old_state=Ready`，`new_state=Processing`，`reason=scheduled`。

如果实现把 `Created -> Running` 合并为一次内部动作，事件层仍应至少能表达 attempt 从 null 到 Running 的完整 snapshot，并在测试中保证 replay 后 attempt 状态确定。

### 9.3 lease expiry 的事件顺序

当 `LeaseManager` 发现 active lease 过期：

1. `LEASE_STATE_CHANGED`：`Active -> Expired`。
2. `ATTEMPT_STATE_CHANGED`：关联 attempt `Running/Created -> Superseded`，`reason=lease_expired`。
3. `RECOVERY_ACTION_RECORDED`：记录 `retry_allowed`、`retry_count` 和恢复原因。
4. 若允许 retry 且无其他有效 attempt：`TASK_UNIT_STATE_CHANGED`：`Processing -> Ready`，`reason=lease_expired_retry`。
5. 若不允许 retry：`TASK_UNIT_STATE_CHANGED`：`Processing -> Failed`，`reason=retry_limit_reached` 或 `deadline_exceeded`。

### 9.4 executor error 的事件顺序

当 executor 明确返回失败：

1. `ATTEMPT_STATE_CHANGED`：`Running -> Failed`，`failure_kind=executor_error`。
2. `LEASE_STATE_CHANGED`：`Active -> Released` 或 `Active -> Revoked`，取决于失败是否仍能正常释放资源。
3. `RECOVERY_ACTION_RECORDED`：记录 retry/fail 决策。
4. `TASK_UNIT_STATE_CHANGED`：`Processing -> Ready/Failed`。

### 9.5 heartbeat 的事件语义

heartbeat 使用 `LEASE_STATE_CHANGED` 表达 `Active -> Active`：

- 必须更新 `last_heartbeat_at`、`expires_at`、`heartbeat_count`。
- idempotency key 需要包含 `lease_id` 和 heartbeat 序号或请求 id。
- heartbeat 不能改变 `TaskUnit` 或 `Attempt` 状态。

### 9.6 idempotency key 建议

| 场景 | key 形态 |
|---|---|
| 创建 lease | `lease:create:<lease_id>` |
| heartbeat | `lease:heartbeat:<lease_id>:<heartbeat_count>` |
| lease terminal | `lease:terminal:<lease_id>:<new_state>` |
| attempt 状态变化 | `attempt:state:<attempt_id>:<old_state>:<new_state>:<correlation_id>` |
| task unit 状态变化 | `task_unit:state:<unit_id>:<old_state>:<new_state>:<correlation_id>` |
| recovery action | `recovery:<unit_id>:<trigger>:<attempt_id>:<retry_count>` |

同一 idempotency key 的重复写入必须满足 Phase 1 已修复的规则：event type、object、task 和 canonical payload 完全一致才可返回旧事件；否则必须失败。

## 10. SQLite 投影视图

SQLite 只能做 query/index，不是权威状态源。Phase 2 建议新增或扩展以下表：

| 表名 | 来源事件 | 说明 |
|---|---|---|
| `task_units` | `TASK_UNIT_CREATED`、`TASK_UNIT_STATE_CHANGED` | 更新当前 `state`、`updated_at`、`last_state_reason` |
| `leases` | `LEASE_STATE_CHANGED` | 以 `lease_id` 为主键，保存最新 lease snapshot |
| `attempts` | `ATTEMPT_STATE_CHANGED` | 以 `attempt_id` 为主键，保存最新 attempt snapshot |
| `recovery_actions` | `RECOVERY_ACTION_RECORDED` | append-style 投影，便于审计 recovery |
| `client_records` | `CLIENT_REGISTERED`、`CLIENT_STATE_CHANGED` | 保存 client 当前状态和 capability snapshot |

重建规则：

- 删除 SQLite 后，只用 JSONL events 和 artifact refs 可以重建所有上述表。
- `TaskGraph` 可从 `task_units`、`task_relations`、future canonical binding projection 重建。
- SQLite projection 遇到未知 future event type 时应跳过或记录 unsupported，而不是改变权威事件。

## 11. 模块落点

推荐后续实现按以下边界拆分：

| 模块 | 放什么 | 不放什么 |
|---|---|---|
| `tokenshare.core.task_graph` | `TaskGraph`、ready 判断、环检测、容量限制 | 文件系统、SQLite、event append、plugin 领域逻辑 |
| `tokenshare.core.state_machines` | `TaskUnit`、`Lease`、`Attempt` 的合法转移判断 | 调用 executor、写 artifact、写 JSONL |
| `tokenshare.core.scheduling` | `Scheduler` 纯决策、`SchedulingDecision` | 修改 `TaskUnit` 状态或持久化 |
| `tokenshare.core.leases` | `LeaseManager` 纯规则、过期/heartbeat/revoke 决策 | 直接 sleep、后台线程、网络心跳 |
| `tokenshare.storage.events` | event type、payload serialization、append/read/idempotency | 状态机规则和调度策略 |
| `tokenshare.storage.sqlite_index` | Phase 2 event projection | 将 SQLite 作为权威状态 |
| 独立 orchestration/engine 层 | 调用 core 决策并按顺序写 event/storage | 扩展 `RootTaskRegistrar` 成总协调器 |

若当前代码尚未创建 orchestration package，可以先用一个窄的 application service 承接 Phase 2 集成测试，但该 service 不能从 `tokenshare.core.__init__` 急切导出，避免再次形成 core/storage 循环依赖。

## 12. 自然语言与 AI 输出边界

Phase 2 虽然不调用 AI，但字段设计必须照顾自然语言任务的特殊性：

- `Attempt.raw_output_ref` 必须为未来 AI 原始文本预留位置，且 raw text 只能作为 artifact 持久化。
- `Attempt.parsed_output_ref` 用于保存插件解析后的结构化 JSON；状态机只看 artifact refs 和验证事件，不解析自然语言正文。
- `candidate_output_refs` 必须是 named output bundle，方便 structured report stub 对 section、evidence、claim 等输出做覆盖检查。
- 任务图 expansion 只能由未来 `DecompositionProposal`、`ExpansionDecision` 和 `TASK_EXPANDED` 事件触发，不能由 raw model text 直接创建 child unit。
- late submission 或 invalid output 的原文可以保存为 audit artifact，但不能修改 `TaskGraph`、不能绑定 canonical output、不能解锁下游依赖。
- event payload 只放摘要和 artifact refs，不嵌入长自然语言正文，避免 replay、diff 和 idempotency hash 被非结构化文本污染。

## 13. 测试策略

Phase 2 建议先写测试，再实现：

| 测试组 | 最小断言 |
|---|---|
| `tests/core/test_task_graph.py` | 图能加载 units/relations；非法 relation 被拒绝；ready 判断不依赖 SQLite；环检测和容量限制生效 |
| `tests/core/test_state_machines.py` | `TaskUnit`、`Lease`、`Attempt` 合法转移通过；跨对象非法状态混入被拒绝 |
| `tests/core/test_scheduler.py` | ready unit 只分配给 capability 匹配且 active lease 未超限的 client；policy 输出 `SchedulingDecision` |
| `tests/core/test_lease_manager.py` | claim 生成 Active lease；heartbeat 更新 expiry；expiry 生成 Expired lease + Superseded attempt + recovery decision |
| `tests/storage/test_phase2_event_projection.py` | Phase 2 events 可 append/read；SQLite 删除后可从 JSONL 重建 `leases`、`attempts`、unit current state |
| `tests/test_phase2_scheduling_flow.py` | ready node 被 schedule 后 events 顺序稳定；lease expiry 后 unit 回到 Ready；所有 state transition 都有 event |

验收命令继续使用：

```powershell
.\init.ps1
```

## 14. 观测与审计

本地 V1 不需要生产监控系统，但 event payload 应保留审计所需信息：

- 每次调度/恢复使用 `correlation_id`。
- 下游状态变化记录 `causation_event_id`。
- 状态变化必须带 `reason` 和 `trigger`。
- `Lease` 与 `Attempt` 都记录 `client_id`，便于统计 executor error、late submission、lease expiry。
- `RecoveryAction` 保存 retry 是否允许和 retry count，便于复现实验故障模型。
- SQLite projection 可以派生指标：ready count、processing count、active lease count、expired lease count、attempt failure count。

## 15. 回滚与重放策略

TokenShare V1 不做破坏性 rollback。错误状态通过 append corrective/recovery events 处理：

- 写错 SQLite projection：删除 SQLite，重新从 JSONL event ledger rebuild。
- lease 过期或 executor failure：写 `RECOVERY_ACTION_RECORDED` 和后续状态事件。
- 非法 late submission：保存 audit artifact/event，但不修改 canonical output。
- 代码版本升级后 replay 不一致：保留旧 event schema parser，必要时新增 migration event，不修改历史 JSONL。

## 16. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| `TaskUnit`、`Lease`、`Attempt` 状态再次混合 | 状态机不可维护，replay 语义不清 | 用独立枚举和状态机测试锁住边界 |
| 调度事件序列局部写入后崩溃 | replay 看到 Active lease 但 unit 仍 Ready 等中间态 | 使用 `correlation_id`，恢复逻辑能识别并补偿；调度查询同时看 active lease |
| SQLite 成为隐藏权威状态 | 删除 SQLite 后无法恢复 | 所有状态推进先写 event，SQLite 仅 projection |
| AI 自然语言正文进入 event payload | idempotency hash 脆弱、审计困难 | 长文本只进 artifact；event 只保存 refs 和摘要 |
| Phase 2 过早实现 verification/merge | feature 边界膨胀，插件逻辑侵入协议核心 | 只保留 future states/fields，不创建 canonical binding 或 merge 逻辑 |
| Phase 2 被误解为插件或处理端开发 | executor/plugin/AI 逻辑提前进入内核，后续边界难以恢复 | 本阶段只实现内核：任务图、状态机、调度/租约规则、event 和 projection；不调用 executor、插件或 AI |
| `RootTaskRegistrar` 再次膨胀 | core/storage/orchestration 循环依赖复发 | Phase 2 集成入口放独立 service，且不从 `tokenshare.core.__init__` 导出 |
| orchestration 命名未完全确定 | 过早绑定入口名称导致实现结构反复调整 | 第一批实现先落纯 core 和 storage projection；最小 application service 在集成流测试前再命名，不阻塞内核对象开发 |

## 17. 实施计划

1. 先补充 Phase 2 红灯测试：`TaskGraph`、状态机、scheduler、lease manager、event projection。
2. 实现 core 层协议对象和状态机，不写 storage。
3. 实现 `TaskGraph` 纯视图，支持 ready 判断和基本图不变量。
4. 实现 `Scheduler` 和 `LeaseManager` 的纯决策返回值。
5. 扩展 `EventLedger` event type 与 payload snapshot helper，但不让 storage 决定状态。
6. 扩展 SQLite projection，确保删除 DB 后可重建。
7. 增加最小 orchestration/application service，把 core 决策按本文事件顺序写入 ledger。
8. 运行 `.\init.ps1`，把验证证据写入 `feature_list.json` 和 `progress.md`。

## 18. 当前设计决策摘要

- `Lease` 过期时，关联 `Attempt` 进入 `Superseded`，不是 `Failed`。
- `SchedulingDecision` 在 Phase 2 嵌入 lease/attempt 事件，不单独建 event type。
- `TaskGraph` 是可重建视图，不作为新的权威 artifact snapshot。
- Phase 2 不允许 `Attempt.Canonical`，该状态只在 Phase 4 canonical binding 后出现。
- 自然语言 raw output 必须通过 artifact refs 进入系统，不能直接进入 task graph 或 event payload 主体。
- 后续实现不得把 `TaskGraph`、`Scheduler`、`LeaseManager` 或 attempt orchestration 塞进 `RootTaskRegistrar`。
- Phase 2 仍然是协议内核开发，不做插件实现、executor/处理端开发、AI 调用、submission 验证、canonical binding、expansion、merge 或 settlement。
- Phase 2 不新增 `max_parallel_attempts_per_unit`；先使用 `allow_shadow_execution=false` 的等价约束：同一 `unit_id` 在最小版本中最多只有一个 active lease。
- Phase 2 heartbeat 每次成功都写 `LEASE_STATE_CHANGED Active -> Active` 事件，不做心跳压缩；V1 优先保证 replay 简单和审计清晰。

## 19. 待后续确认

- orchestration/application service 的模块名是 `tokenshare.core.engine`、`tokenshare.protocol_engine`，还是放在 `tokenshare.experiments` 的本地 runner 旁边。该命名不阻塞第一批 Phase 2 代码：先实现纯 core 对象/状态机和 storage projection，再在集成流测试前确定最小入口。
