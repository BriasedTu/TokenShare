# Phase 2 代码与文档对应关系

| 项 | 值 |
|---|---|
| 日期 | 2026-06-08；2026-06-23 补充 Phase 2 stabilization；2026-06-23 按当前代码重新校准 |
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
| `Scheduler` / `SchedulingDecision` | `src/tokenshare/core/scheduling.py` | 按 `TaskUnit.created_at` / `unit_id` 执行 FIFO ready unit + client capability 匹配；`allow_shadow_execution=false` 时跳过已有 active lease 的 unit。内部 helper `_has_active_lease()`、`_matched_capabilities()`、`_capability_matches()` 只服务该纯决策逻辑，不是独立协议对象。 | `tests/core/test_scheduler.py` |
| `LeaseManager` / `LeaseClaim` / `LeaseExpiryDecision` | `src/tokenshare/core/leases.py` | 纯规则生成 claim、heartbeat、lease expiry recovery；`LeaseClaim` 包含新 lease、Created attempt 和 Running attempt；`LeaseExpiryDecision` 包含 Expired lease、Superseded attempt、recovery action 和下一步 TaskUnit 状态。heartbeat 只允许在 `now < expires_at` 时续租，expiry 只允许在 `now >= expires_at` 后发生。 | `tests/core/test_lease_manager.py` |
| Phase 2 event type | `src/tokenshare/storage/events.py` | 启用 `TASK_UNIT_STATE_CHANGED`、`LEASE_STATE_CHANGED`、`ATTEMPT_STATE_CHANGED`、`RECOVERY_ACTION_RECORDED` 和 `CLIENT_STATE_CHANGED`。 | `tests/storage/test_phase2_event_projection.py`、`tests/test_phase2_scheduling_flow.py` |
| SQLite Phase 2 projection | `src/tokenshare/storage/sqlite_index.py` | 扩展 `task_units` 当前状态字段，并新增 `leases`、`attempts`、`recovery_actions` 投影视图；仍只从 JSONL events 重建。 | `tests/storage/test_phase2_event_projection.py` |
| 最小编排入口 | `src/tokenshare/protocol_engine.py` | 不进入 `tokenshare.core.__init__`；`ProtocolEngine` 负责 schedule、heartbeat 和 lease expiry 三条 flow 的事件顺序、correlation、causation 和 idempotency key；返回 `SchedulingFlowResult`、`LeaseHeartbeatFlowResult`、`LeaseExpiryFlowResult`。调度前通过 `_active_leases_by_unit_id_from_events()` 从 `EventLedger` 投影 active leases，并用 `_merge_active_lease_maps()` 合并调用方额外视图，避免调用者未传 `active_leases_by_unit_id` 时重复 claim。 | `tests/test_phase2_scheduling_flow.py` |
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
- `ProtocolEngine.schedule_ready_unit()` 的调度判断必须读取 event ledger 中最新 `LEASE_STATE_CHANGED` 事件形成 active lease 投影；上层传入的 `active_leases_by_unit_id` 只能补充额外视图，不能成为唯一防重复来源。
- Phase 2 时间不变量：未到 `expires_at` 的 lease 不能提前 `Expired`；`now >= expires_at` 的 Active lease 不能再 heartbeat 续命。
- Phase 2 没有实现插件注册、executor 调用、submission 验证、canonical output binding、expansion、merge 或 settlement。
- `Attempt.Canonical` 和 `TaskUnit.Processing -> Completed` 在 Phase 2 状态机中仍被拒绝，等待 Phase 4 验证/canonical binding 设计落地。

## 4. 2026-06-23 代码实物校准

本次校准以当前代码为准，确认本 code map 中所有路径真实存在，并补齐此前未点名但已存在的 Phase 2 对象、flow result 和测试 helper。`__pycache__/`、`.gitkeep` 和空 `__init__.py` 只作为包/目录占位，不代表协议行为。

### 4.1 当前源码文件清单

| 文件 | 当前真实内容 | 本 map 处理 |
|---|---|---|
| `src/tokenshare/core/models.py` | Phase 1 对象；Phase 2 `LeaseState`、`AttemptState`、`Lease`、`Attempt` | Phase 2 对象已在总体对应关系中覆盖。 |
| `src/tokenshare/core/task_graph.py` | `TaskGraph.ready_unit_ids()` 和图不变量校验 helper | 已覆盖为 Phase 2 graph 视图。 |
| `src/tokenshare/core/state_machines.py` | `transition_task_unit()`、`transition_lease()`、`transition_attempt()` | 已覆盖为 Phase 2 状态机。 |
| `src/tokenshare/core/scheduling.py` | `SchedulingDecision`、`Scheduler.select_next()` 和 capability/active-lease helper | 已覆盖为 Phase 2 调度纯决策。 |
| `src/tokenshare/core/leases.py` | `LeaseClaim`、`LeaseExpiryDecision`、`LeaseManager.claim()`、`heartbeat()`、`expire()` | 已补齐结果对象映射。 |
| `src/tokenshare/protocol_engine.py` | `SchedulingFlowResult`、`LeaseHeartbeatFlowResult`、`LeaseExpiryFlowResult`、`ProtocolEngine` 三条 event-backed flow 和 active lease 投影 helper | 已补齐 flow result 和 ledger 投影映射。 |
| `src/tokenshare/storage/events.py` | Phase 1 event envelope；Phase 2 `TASK_UNIT_STATE_CHANGED`、`CLIENT_STATE_CHANGED`、`LEASE_STATE_CHANGED`、`ATTEMPT_STATE_CHANGED`、`RECOVERY_ACTION_RECORDED` | Phase 2 event type 已覆盖；event envelope 本身见 Phase 1 code map。 |
| `src/tokenshare/storage/sqlite_index.py` | Phase 1 表；Phase 2 `leases`、`attempts`、`recovery_actions`，以及 `CLIENT_STATE_CHANGED` 更新分支 | Phase 2 projection 已覆盖；client 状态分支存在但当前没有独立测试。 |
| `src/tokenshare/core/registration.py`、`src/tokenshare/storage/artifacts.py` | Phase 1 root registration 和 artifact store | 不属于 Phase 2 行为，见 Phase 1 code map。 |
| `src/tokenshare/executors/__init__.py`、`src/tokenshare/plugins/__init__.py`、`src/tokenshare/plugins/factorization/__init__.py`、`src/tokenshare/plugins/lean_stub/__init__.py`、`src/tokenshare/replay/__init__.py`、`src/tokenshare/experiments/__init__.py` | 当前为空包占位 | 不计入 Phase 2 已实现功能；对应功能属于 feat-004 或后续 feature。 |

### 4.2 当前测试文件清单

| 文件 | 当前覆盖内容 | 本 map 处理 |
|---|---|---|
| `tests/core/test_task_graph.py` | ready 判断、named dependency、missing endpoint、cycle | 已覆盖。 |
| `tests/core/test_state_machines.py` | TaskUnit/Lease/Attempt 状态机边界和 Phase 4 状态拒绝 | 已覆盖。 |
| `tests/core/test_scheduler.py` | capability 匹配、active lease skip、created_at / unit_id FIFO | 已覆盖。 |
| `tests/core/test_lease_manager.py` | claim/heartbeat/expiry recovery、早过期拒绝、过期 heartbeat 拒绝 | 已覆盖。 |
| `tests/storage/test_phase2_event_projection.py` | Phase 2 events 到 SQLite `leases` / `attempts` / `recovery_actions` 投影 | 已覆盖。 |
| `tests/test_phase2_scheduling_flow.py` | schedule、heartbeat、expiry 的 event 顺序，以及从 ledger 防重复 active lease | 已覆盖。 |
| `tests/phase2_fixtures.py`、`tests/__init__.py` | Phase 2 测试 helper 和稳定 import | 已覆盖为测试夹具。 |
| `tests/storage/test_sqlite_index.py`、`tests/storage/test_event_ledger.py`、`tests/storage/test_artifact_store.py`、`tests/core/test_phase1_models.py`、`tests/test_phase1_root_registration.py`、`tests/test_package_layout.py` | Phase 1/storage/package layout 基线 | 不计入 Phase 2 专属测试，但它们参与完整启动验证。 |

## 5. 验证记录

- 红灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\core\test_task_graph.py tests\core\test_state_machines.py tests\core\test_scheduler.py tests\core\test_lease_manager.py tests\storage\test_phase2_event_projection.py tests\test_phase2_scheduling_flow.py -q` 失败，原因是 `tokenshare.core.task_graph`、`tokenshare.core.scheduling`、`tokenshare.core.leases`、`Lease` / `Attempt` 对象和 `tokenshare.protocol_engine` 尚不存在。
- Heartbeat 红灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\test_phase2_scheduling_flow.py -q` 失败，原因是 `ProtocolEngine.record_lease_heartbeat` 尚不存在。
- 绿灯：Phase 2 定向命令通过，结果 `9 passed in 0.22s`。
- 完整验证：`powershell -ExecutionPolicy Bypass -File .\init.ps1` 通过，pytest collected 18 items，结果 `18 passed`。
- 2026-06-23 stabilization 红灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\core\test_scheduler.py tests\core\test_lease_manager.py tests\test_phase2_scheduling_flow.py -q` 失败，新增回归测试暴露 FIFO 未按 `created_at`、未到期 lease 可提前 expire、超时 lease 可 heartbeat、同一 ready graph 可重复生成 active lease。
- 2026-06-23 stabilization 绿灯：同一定向命令通过，结果 `7 passed in 0.18s`。
- 2026-06-23 code map 校准前基线验证：`powershell -ExecutionPolicy Bypass -File .\init.ps1` 通过，pytest collected 21 items，结果 `21 passed in 0.49s`。
- 2026-06-23 code map 路径审计：使用 PowerShell + Python AST/Markdown 扫描确认两个 code map 中引用的 `src/`、`tests/` 和 `Doc/` 路径全部存在；并按 AST 扫描结果补齐本文件的 Phase 2 当前真实符号清单。
