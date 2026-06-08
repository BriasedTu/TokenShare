# Phase 2 代码与文档对应关系

| 项 | 值 |
|---|---|
| 日期 | 2026-06-08 |
| 对应 feature | `feat-003` - Phase 2 - Task Graph, State Machines, and Scheduling |
| 上游规格 | `Doc/TechnicalDocument/2026-06-08-phase-2-minimal-field-state-event-spec.md` |
| 关联备忘录 | `Doc/TechnicalDocument/2026-06-07-phase-2-coordination-debt-memo.md` |
| 目的 | 说明 Phase 2 最小实现分别对应哪些协议对象、状态机、调度、租约、事件和 SQLite 投影边界。 |

## 1. 总体对应关系

本轮实现遵循 Phase 2 规格中的三层边界：`tokenshare.core` 只保存协议对象、状态机和纯决策；`tokenshare.storage` 只保存 event ledger 和可重建 SQLite 投影；顶层 `tokenshare.protocol_engine` 是最小 application service，负责把 core 决策按规定顺序写入 `EventLedger`。

| 规格内容 | 代码位置 | 说明 | 主要测试 |
|---|---|---|---|
| `Lease` / `Attempt` 对象与状态枚举 | `src/tokenshare/core/models.py` | 新增 `LeaseState`、`AttemptState`、`Lease`、`Attempt`，提供稳定 `to_dict()` wire format。 | `tests/core/test_state_machines.py`、`tests/core/test_lease_manager.py` |
| `TaskGraph` | `src/tokenshare/core/task_graph.py` | 实现 graph 视图、relation endpoint 校验、named dependency ready 判断、重复 target input 防护和环检测。 | `tests/core/test_task_graph.py` |
| 状态机 | `src/tokenshare/core/state_machines.py` | 实现 Phase 2 最小 `TaskUnit`、`Lease`、`Attempt` 合法转移；Phase 4 的 canonical/complete 路径仍被拒绝。 | `tests/core/test_state_machines.py` |
| `Scheduler` / `SchedulingDecision` | `src/tokenshare/core/scheduling.py` | FIFO ready unit + client capability 匹配；`allow_shadow_execution=false` 时跳过已有 active lease 的 unit。 | `tests/core/test_scheduler.py` |
| `LeaseManager` | `src/tokenshare/core/leases.py` | 纯规则生成 claim、heartbeat、lease expiry recovery；过期 lease 会把关联 attempt 标记为 `Superseded`，并根据 retry policy 产出下一步 TaskUnit 状态。 | `tests/core/test_lease_manager.py` |
| Phase 2 event type | `src/tokenshare/storage/events.py` | 启用 `TASK_UNIT_STATE_CHANGED`、`LEASE_STATE_CHANGED`、`ATTEMPT_STATE_CHANGED`、`RECOVERY_ACTION_RECORDED` 和 `CLIENT_STATE_CHANGED`。 | `tests/storage/test_phase2_event_projection.py`、`tests/test_phase2_scheduling_flow.py` |
| SQLite Phase 2 projection | `src/tokenshare/storage/sqlite_index.py` | 扩展 `task_units` 当前状态字段，并新增 `leases`、`attempts`、`recovery_actions` 投影视图；仍只从 JSONL events 重建。 | `tests/storage/test_phase2_event_projection.py` |
| 最小编排入口 | `src/tokenshare/protocol_engine.py` | 不进入 `tokenshare.core.__init__`；负责 schedule、heartbeat 和 lease expiry 三条 flow 的事件顺序、correlation、causation 和 idempotency key。 | `tests/test_phase2_scheduling_flow.py` |
| Phase 2 测试夹具 | `tests/phase2_fixtures.py`、`tests/__init__.py` | 提供测试用 `TaskUnit`、`ClientRecord`、`ProtocolConfig` 和 artifact 引用；`tests/__init__.py` 让 helper import 稳定。 | Phase 2 全部测试 |

## 2. 事件顺序对应

### 2.1 调度 ready unit

`ProtocolEngine.schedule_ready_unit()` 按规格写入四个事件：

| 顺序 | 事件类型 | 主要 payload | 验证 |
|---|---|---|---|
| 1 | `LEASE_STATE_CHANGED` | `old_state=null`、`new_state=Active`、`lease`、`scheduling_decision` | `tests/test_phase2_scheduling_flow.py` |
| 2 | `ATTEMPT_STATE_CHANGED` | `old_state=null`、`new_state=Created`、`attempt` | 同上 |
| 3 | `ATTEMPT_STATE_CHANGED` | `old_state=Created`、`new_state=Running`、`attempt` | 同上 |
| 4 | `TASK_UNIT_STATE_CHANGED` | `old_state=Ready`、`new_state=Processing`、`reason=scheduled` | 同上 |

### 2.2 heartbeat

`ProtocolEngine.record_lease_heartbeat()` 按规格写入一个事件：

| 顺序 | 事件类型 | 主要 payload | 验证 |
|---|---|---|---|
| 1 | `LEASE_STATE_CHANGED` | `old_state=Active`、`new_state=Active`、更新后的 `lease` | `tests/test_phase2_scheduling_flow.py` |

Heartbeat 不改变 `TaskUnit` 或 `Attempt` 状态。

### 2.3 lease expiry recovery

`ProtocolEngine.record_lease_expiry()` 按规格写入四个事件：

| 顺序 | 事件类型 | 主要 payload | 验证 |
|---|---|---|---|
| 1 | `LEASE_STATE_CHANGED` | `Active -> Expired` | `tests/test_phase2_scheduling_flow.py` |
| 2 | `ATTEMPT_STATE_CHANGED` | `Running -> Superseded` | 同上 |
| 3 | `RECOVERY_ACTION_RECORDED` | `retry_allowed`、`retry_count`、`reason` | 同上 |
| 4 | `TASK_UNIT_STATE_CHANGED` | `Processing -> Ready` 或 `Processing -> Failed` | 同上 |

## 3. 边界说明

- `RootTaskRegistrar` 未被扩展；Phase 2 编排入口使用顶层 `tokenshare.protocol_engine.ProtocolEngine`，避免 `tokenshare.core` 依赖 storage。
- SQLite projection 不参与状态机决策；删除 DB 后仍可由 JSONL events 重建 `task_units`、`leases`、`attempts` 和 `recovery_actions`。
- Phase 2 没有实现插件注册、executor 调用、submission 验证、canonical output binding、expansion、merge 或 settlement。
- `Attempt.Canonical` 和 `TaskUnit.Processing -> Completed` 在 Phase 2 状态机中仍被拒绝，等待 Phase 4 验证/canonical binding 设计落地。

## 4. 验证记录

- 红灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\core\test_task_graph.py tests\core\test_state_machines.py tests\core\test_scheduler.py tests\core\test_lease_manager.py tests\storage\test_phase2_event_projection.py tests\test_phase2_scheduling_flow.py -q` 失败，原因是 `tokenshare.core.task_graph`、`tokenshare.core.scheduling`、`tokenshare.core.leases`、`Lease` / `Attempt` 对象和 `tokenshare.protocol_engine` 尚不存在。
- Heartbeat 红灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\test_phase2_scheduling_flow.py -q` 失败，原因是 `ProtocolEngine.record_lease_heartbeat` 尚不存在。
- 绿灯：Phase 2 定向命令通过，结果 `9 passed in 0.22s`。
- 完整验证：`powershell -ExecutionPolicy Bypass -File .\init.ps1` 通过，pytest collected 18 items，结果 `18 passed`。
