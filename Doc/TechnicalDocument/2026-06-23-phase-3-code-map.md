# Phase 3 代码与文档对应关系

| 项 | 值 |
|---|---|
| 日期 | 2026-06-23 |
| 对应 feature | `feat-004` - Phase 3 - Plugin and Executor Contracts |
| 上游规格 | `Doc/TechnicalDocument/2026-06-23-phase-3-plugin-executor-field-spec.md` |
| 上游设计 | `Doc/TechnicalDocument/2026-06-03-tokenshare-protocol-technical-design.md` 第 4.3、8、12、21 节 |
| 目的 | 说明 Phase 3 最小实现分别对应哪些插件 descriptor、执行器 descriptor、registry freeze、统一 request/submission、artifact、event 和 SQLite 投影边界。 |

## 1. 总体对应关系

Phase 3 实现遵循字段草案的三层持久化原则：完整 descriptor、registry snapshot、`ExecutionRequest`、`ExecutionSubmission`、AI raw/parsed 输出保存为 artifact；JSONL event payload 只保存 ref、digest 和最小查询摘要；SQLite 只作为可重建索引。

| 规格内容 | 代码位置 | 说明 | 主要测试 |
|---|---|---|---|
| `PluginDescriptor` / `OutputContract` | `src/tokenshare/plugins/contracts.py` | 描述插件版本、支持 task type、输入输出 contract、execution contract、validator/merge policy 声明；只声明能力，不执行验证或合并。 | `tests/plugins/test_plugin_registry.py` |
| `PluginRegistry` / `RegistrySnapshot` | `src/tokenshare/plugins/registry.py` | 内存 registry 可注册 descriptor；`freeze()` 把插件 descriptor 和 executor descriptor 保存为 artifact，并生成固定 registry snapshot。冻结后禁止继续注册插件版本。 | `tests/plugins/test_plugin_registry.py` |
| `ExecutorDescriptor` / `ExecutorStatus` | `src/tokenshare/executors/contracts.py` | 定义执行器 descriptor、`Available` / `Busy` / `Offline` / `Disabled` 状态枚举、`EnvironmentRef`、`PromptPackage`、`ExecutionRequest` 和 `ExecutionSubmission`。 | `tests/executors/test_executor_registry.py`、`tests/executors/test_mock_ai_executor.py`、`tests/executors/test_deterministic_executor.py` |
| `ExecutorRegistry` | `src/tokenshare/executors/registry.py` | 只把 `Available` executor 视为可分派；对 Busy/Offline/Disabled 和 capability/schema mismatch 给出 no-match reason；冻结时 artifact 化 descriptor。 | `tests/executors/test_executor_registry.py` |
| `MockAIExecutor` | `src/tokenshare/executors/mock_ai.py` | 本地 deterministic fixture executor，不调用生产 AI API；保存 `RawModelOutput`、`ParsedModelOutput` 或 `ParseFailureReport` artifact，并返回统一 `ExecutionSubmission`。 | `tests/executors/test_mock_ai_executor.py` |
| `DeterministicLocalExecutor` | `src/tokenshare/executors/deterministic.py` | 非 AI 本地 deterministic executor 边界；接收统一 `ExecutionRequest`，返回统一 `ExecutionSubmission`，只保存结构化 parsed/candidate artifact。 | `tests/executors/test_deterministic_executor.py` |
| Phase 3 `EventType` | `src/tokenshare/storage/events.py` | 新增 `REGISTRY_SNAPSHOT_RECORDED`、`EXECUTION_REQUEST_RECORDED`、`EXECUTION_SUBMISSION_RECORDED`。 | `tests/storage/test_phase3_event_projection.py`、`tests/test_phase3_execution_flow.py` |
| `Attempt.Running -> Submitted` | `src/tokenshare/core/state_machines.py` | 允许 Phase 3 submission 后进入 `Submitted`，写入 submitted time 和 output artifact refs；继续拒绝 `Submitted -> Verifying` 等 Phase 4 状态。 | `tests/core/test_state_machines.py` |
| Phase 3 编排入口 | `src/tokenshare/protocol_engine.py` | 新增 `record_registry_snapshot()`、`record_execution_request()`、`record_execution_submission()`；保存完整 artifact，写短 event payload；只有 submission 与当前 running attempt/lease/fencing token 匹配时才写 `ATTEMPT_STATE_CHANGED Running -> Submitted`，不匹配 submission 只保留 audit event。 | `tests/test_phase3_execution_flow.py` |
| SQLite Phase 3 projection | `src/tokenshare/storage/sqlite_index.py` | 新增 `registry_snapshots`、`execution_requests`、`execution_submissions`、`executor_statuses` 四张 index-only 表；不保存完整 body。 | `tests/storage/test_phase3_event_projection.py` |
| Phase 3 测试夹具 | `tests/phase3_fixtures.py` | 提供 descriptor、output contract、environment ref 和 schema ref helper。 | Phase 3 测试 |

## 2. 事件顺序对应

### 2.1 Registry snapshot

`ProtocolEngine.record_registry_snapshot()` 的最小顺序：

| 顺序 | 动作 | 结果 |
|---|---|---|
| 1 | `PluginRegistry.freeze()` 保存 `PluginDescriptor` artifact | snapshot 中 plugin entry 只保留 descriptor ref、digest 和查询摘要。 |
| 2 | `ExecutorRegistry.freeze_entries()` 保存 `ExecutorDescriptor` artifact | snapshot 中 executor entry 只保留 descriptor ref、digest、status 和查询摘要。 |
| 3 | 保存 `RegistrySnapshot` artifact | snapshot 本体可由 artifact hash 复查。 |
| 4 | 写 `REGISTRY_SNAPSHOT_RECORDED` | event payload 只保留 snapshot ref/digest、plugin/executor entry 摘要和 `frozen_at`。 |

### 2.2 Execution request

`ProtocolEngine.record_execution_request()` 的最小顺序：

| 顺序 | 动作 | 结果 |
|---|---|---|
| 1 | 保存完整 `ExecutionRequest` artifact | 包含 lease、attempt、plugin/executor descriptor ref、inline `allocation_decision`、capability snapshot、task unit snapshot、input refs、output contract、requirements/hints 和 `EnvironmentRef`。 |
| 2 | 写 `EXECUTION_REQUEST_RECORDED` | event payload 只保留 request ref/digest、task/unit/attempt/lease、plugin/executor ID 和 created time。 |

`EXECUTION_REQUEST_RECORDED` 不推进 `Attempt` 状态。

### 2.3 Execution submission

`ProtocolEngine.record_execution_submission()` 的最小顺序：

| 顺序 | 动作 | 结果 |
|---|---|---|
| 1 | 保存完整 `ExecutionSubmission` artifact | 包含 result kind、raw/parsed/candidate/log/provenance refs、环境回显和 usage/error 摘要。 |
| 2 | 写 `EXECUTION_SUBMISSION_RECORDED` | event payload 只保留 submission ref/digest、task/unit/attempt/lease、result kind 和 submitted time。 |
| 3 | 如果 attempt 当前是 `Running`，且 submission 的 task/unit/attempt/lease/fencing token 与当前 attempt/lease 匹配，写 `ATTEMPT_STATE_CHANGED Running -> Submitted` | attempt snapshot 可引用 output artifact refs；不进入 verification、canonical、merge 或 settlement。 |

迟到、非 Running attempt、attempt/lease 不匹配或 fencing token 不匹配的 submission 可以被记录为 audit artifact/event，但不会推进 attempt 状态。

## 3. 边界说明

- Phase 3 没有实现 submission verification、canonical output binding、expansion、merge 或 settlement。
- `RootTaskRegistrar` 没有被扩展；Phase 3 编排入口继续放在顶层 `tokenshare.protocol_engine.ProtocolEngine`。
- `ExecutorRegistry` 明确了 `Available` / `Busy` / `Offline` / `Disabled` 状态契约；Phase 2 `Scheduler` 只把序列化状态 `Available` 和 legacy `active` 视为可分派，避免继续接受 `ready`、`online`、`idle` 等旧字符串。
- `MockAIExecutor` 不调用真实 AI；它只使用 deterministic fixture，证明 prompt/raw/parsed/parse failure artifact 边界。
- `DeterministicLocalExecutor` 不做 factorization、Lean 或 structured report 领域逻辑；它只证明非 AI executor 也使用统一 request/submission envelope。
- SQLite projection 只保存索引摘要和 event payload JSON；完整 body 必须从 artifact store 读取。

## 4. 当前源码文件清单

| 文件 | 当前真实内容 | 本 map 处理 |
|---|---|---|
| `src/tokenshare/plugins/contracts.py` | `OutputContract`、`PluginDescriptor`、descriptor digest helper | Phase 3 插件 contract。 |
| `src/tokenshare/plugins/registry.py` | `PluginRegistry`、`RegistrySnapshot` | Phase 3 plugin registry freeze。 |
| `src/tokenshare/executors/contracts.py` | `ExecutorStatus`、`EnvironmentRef`、`ExecutorDescriptor`、`PromptPackage`、`ExecutionRequest`、`ExecutionSubmission` | Phase 3 executor/request/submission contract。 |
| `src/tokenshare/executors/registry.py` | `ExecutorRegistry`、available matching、no-match reason、descriptor artifact freeze | Phase 3 executor registry。 |
| `src/tokenshare/executors/mock_ai.py` | `MockAIExecutorProfile`、`MockAIExecutor` | Phase 3 mock AI artifact path。 |
| `src/tokenshare/executors/deterministic.py` | `DeterministicLocalExecutor` | Phase 3 deterministic executor boundary。 |
| `src/tokenshare/protocol_engine.py` | Phase 2 scheduling/heartbeat/expiry flow；Phase 3 registry/request/submission flow | Phase 3 新增 flow 已覆盖。 |
| `src/tokenshare/core/state_machines.py` | TaskUnit/Lease/Attempt transitions；Phase 3 `Running -> Submitted` | Phase 3 attempt submission 已覆盖。 |
| `src/tokenshare/storage/events.py` | Phase 1/2 event envelope；Phase 3 event type | Phase 3 event type 已覆盖。 |
| `src/tokenshare/storage/sqlite_index.py` | Phase 1/2 projection；Phase 3 四张 index-only 表 | Phase 3 projection 已覆盖。 |

## 5. 当前测试文件清单

| 文件 | 当前覆盖内容 | 本 map 处理 |
|---|---|---|
| `tests/plugins/test_plugin_registry.py` | descriptor artifact 化、registry freeze 后版本锁定 | 已覆盖。 |
| `tests/executors/test_executor_registry.py` | `Available` 才可分派，Busy/Offline/Disabled no-match reason | 已覆盖。 |
| `tests/executors/test_mock_ai_executor.py` | 统一 request 到 mock AI submission，`PromptPackage`、raw、parsed artifact 可验证 | 已覆盖。 |
| `tests/executors/test_deterministic_executor.py` | 非 AI deterministic executor 使用统一 request/submission，且不产生 raw model output | 已覆盖。 |
| `tests/test_phase3_execution_flow.py` | registry snapshot、schedule、request artifact event、mock AI submission、submission artifact event、attempt `Running -> Submitted`、不匹配 submission audit-only | 已覆盖。 |
| `tests/storage/test_phase3_event_projection.py` | 从 Phase 3 events 重建 SQLite 四张 index-only 表 | 已覆盖。 |
| `tests/phase3_fixtures.py` | Phase 3 test helper | 已覆盖为测试夹具。 |
| `tests/core/test_state_machines.py` | Phase 3 允许 `Running -> Submitted`，继续拒绝 Phase 4 verification states | 已补充覆盖。 |
| `tests/core/test_scheduler.py` | Scheduler 使用 Phase 3 `Available` 状态和 legacy `active` 兼容入口，不再接受旧 `ready` 状态 | 已补充覆盖。 |

## 6. 验证记录

- 启动基线：`.\init.ps1` 通过，pytest collected 21 items，结果 `21 passed in 0.36s`。
- Phase 3 红灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\plugins\test_plugin_registry.py tests\executors\test_executor_registry.py tests\executors\test_mock_ai_executor.py tests\test_phase3_execution_flow.py tests\storage\test_phase3_event_projection.py -q` 失败，原因是 `tokenshare.executors.contracts`、`tokenshare.executors.registry` 等 Phase 3 模块尚不存在。
- Phase 3 deterministic executor 红灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\executors\test_deterministic_executor.py -q` 失败，原因是 `tokenshare.executors.deterministic` 尚不存在。
- Phase 3 定向绿灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\core\test_state_machines.py tests\plugins\test_plugin_registry.py tests\executors\test_executor_registry.py tests\executors\test_mock_ai_executor.py tests\executors\test_deterministic_executor.py tests\test_phase3_execution_flow.py tests\storage\test_phase3_event_projection.py -q` 通过，结果 `9 passed in 0.53s`。
- 完整启动验证：`.\init.ps1` 通过，pytest collected 28 items，结果 `28 passed`。后续文档同步后应再次运行完整验证并以最新输出为准。
- Phase 3 边界修复红灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\core\test_scheduler.py tests\test_phase3_execution_flow.py -q` 失败，暴露 `ready` 旧状态仍可调度，以及 `record_execution_submission()` 尚未接收 lease/fencing token 绑定校验。
- Phase 3 边界修复定向绿灯：同一定向命令通过，结果 `6 passed in 0.18s`。
- Phase 3 边界修复完整启动验证：`powershell -ExecutionPolicy Bypass -File E:\TokenEcnomic\TokenShare\init.ps1` 通过，pytest collected 30 items，结果 `30 passed in 0.67s`。
