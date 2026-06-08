# Phase 2 协调边界备忘录

| 项 | 值 |
|---|---|
| 日期 | 2026-06-07 |
| 状态 | Active Memo |
| 关联 feature | `feat-003` - Phase 2 - Task Graph, State Machines, and Scheduling |
| 目的 | 记录 Phase 1 临时协调器在进入 Phase 2 前后的边界债，防止协议核心、存储和编排职责继续混在一起。 |
| 非目标 | 不定义 Phase 2 字段规格；不替代主 TDD；不提前实现 `ProtocolEngine`。 |

## 1. 背景

Phase 1 为了跑通 root task registration，引入了 `RootTaskRegistrar` 作为临时协调器。它会调用
`ArtifactStore` 和 `EventLedger`，并按顺序写入 root input、`TaskSpec` 和 root `TaskUnit`
相关事件。

这个临时做法在 Phase 1 可接受，因为目标是证明基础对象和存储闭环。但 Phase 2 将开始实现
`TaskGraph`、`Scheduler`、`LeaseManager`、`Lease` 和 `Attempt` 状态机。如果继续让
`tokenshare.core.registration` 承担更多编排职责，协议核心会逐步依赖存储实现，后续 replay、
调度和验证边界会变得难以维护。

## 2. 已立即修复的问题

| 问题 | 处理 |
|---|---|
| `EventLedger.append()` 遇到重复 `idempotency_key` 时无条件返回旧事件，可能吞掉冲突 payload。 | 已改为比较 `event_type`、`object_type`、`object_id`、`task_id` 和 canonical payload；一致时返回旧事件，冲突时抛出 `ValueError`。 |
| `tokenshare.core.__init__` 重新导出 `RootTaskRegistrar`，导致导入 `tokenshare.storage.events` 时出现 `core -> registration -> storage` 循环导入。 | 已从 `tokenshare.core.__init__` 移除 `RootTaskRegistrar` 相关导出；需要时直接从 `tokenshare.core.registration` 导入。 |
| `TaskUnit` JSON snapshot 测试用 `TaskState.READY` 比较 wire format。 | 已改为直接断言 `"Ready"`，锁定持久化 JSON 字符串。 |

## 3. 保留到 Phase 2 的协调债

Phase 2 开始实现状态机和调度时，需要把长期编排入口从 `RootTaskRegistrar` 迁出，建议落点是
后续的 `ProtocolEngine` 或等价 application service。

边界建议：

| 模块 | 应保留职责 | 不应继续承担 |
|---|---|---|
| `tokenshare.core` | 协议对象、状态枚举、状态机、不变量、`TaskGraph` 纯逻辑。 | 文件系统写入、JSONL append、SQLite projection、执行器调用。 |
| `tokenshare.storage` | `ArtifactStore`、`EventLedger`、SQLite materialized index。 | 状态机决策、调度决策、插件领域验证。 |
| 编排层 | 把 core 的决策结果写入 storage；协调 `TaskGraph`、`Scheduler`、`LeaseManager`、artifact 和 ledger。 | 插件领域规则、执行器内部实现。 |

`RootTaskRegistrar` 可以继续作为 Phase 1 兼容入口存在，但不应成为 Phase 2 的增长点。新代码需要
创建 lease、attempt、状态转移或 recovery action 时，应优先设计编排层，而不是继续往
`tokenshare.core.registration` 里追加方法。

## 4. 触发条件

如果后续 agent 发现以下任一情况，应先回到本备忘录和主 TDD 再动手：

- 准备在 `RootTaskRegistrar` 中加入 `TaskGraph`、`Scheduler`、`LeaseManager` 或 attempt 逻辑。
- 准备从 `tokenshare.core.__init__` 重新导出依赖 storage 的协调器。
- 准备让 `tokenshare.core` 直接 append JSONL event 或写 SQLite。
- 准备让 `tokenshare.storage` 根据事件内容做协议状态决策。

## 5. 验证记录

本备忘录相关代码变更已使用 TDD 验证：

- 红灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\storage\test_event_ledger.py -q` 先暴露 `tokenshare.core.__init__` 的循环导入，修复后同一命令暴露重复幂等键不抛错。
- 绿灯：同一 event ledger 定向测试通过。
- 绿灯：core model 和 package layout 定向测试通过。
