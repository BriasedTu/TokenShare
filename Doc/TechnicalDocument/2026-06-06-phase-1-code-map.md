# Phase 1 代码与文档对应关系

| 项 | 值 |
|---|---|
| 日期 | 2026-06-06；2026-06-23 按当前代码重新校准 |
| 对应 feature | `feat-002` - Phase 1 - Protocol Base Objects and Storage |
| 上游规格 | `Doc/TechnicalDocument/2026-06-05-phase-1-minimal-object-field-spec.md` |
| 关联备忘录 | `Doc/TechnicalDocument/2026-06-07-phase-2-coordination-debt-memo.md` |
| 目的 | 说明 Phase 1 实现代码分别对应哪些协议对象、事件、存储边界和验收点。 |

## 1. 总体对应关系

Phase 1 本轮实现遵循“协议对象在 `tokenshare.core`，文件和索引存储在 `tokenshare.storage`”的边界。

| 规格内容 | 代码位置 | 说明 | 主要测试 |
|---|---|---|---|
| `ArtifactRef` | `src/tokenshare/core/models.py` | 作为协议引用对象保存 artifact 标识、URI、hash、schema 和来源，并提供 `to_dict()` / `from_dict()`。文件系统读写不放入该对象。 | `tests/core/test_phase1_models.py`、`tests/storage/test_artifact_store.py` |
| `ProtocolConfig` | `src/tokenshare/core/models.py` | 保存 Phase 1 默认运行策略快照，包括 lease、retry、调度策略、artifact store URI 和 event log URI。 | `tests/core/test_phase1_models.py`、`tests/test_phase1_root_registration.py` |
| `TaskSpec` | `src/tokenshare/core/models.py` | 保存根任务注册快照，固定插件、插件版本、拆分策略、根输入和协议配置。 | `tests/core/test_phase1_models.py`、`tests/test_phase1_root_registration.py` |
| `TaskUnit` | `src/tokenshare/core/models.py` | 提供 `create_root` 创建 root unit，初始 `state = "Ready"`，输入指向 root artifact；`TaskState` 只保留节点生命周期状态，不包含 `Lease` 或 `Attempt` 的细节状态。 | `tests/core/test_phase1_models.py`、`tests/test_phase1_root_registration.py` |
| `TaskRelation` | `src/tokenshare/core/models.py` | 实现 Phase 1 字段和 JSON snapshot；当前 root happy path 不创建关系。 | 后续 expand 测试会覆盖。 |
| `ClientRecord` | `src/tokenshare/core/models.py` | 实现 Phase 1 字段和 JSON snapshot；当前已被 Phase 2 scheduler 用作本地模拟 client 能力记录。 | `tests/core/test_scheduler.py` 间接覆盖 scheduler 使用；当前没有独立 client registration happy path。 |
| root task registration | `src/tokenshare/core/registration.py` | `RootTaskRegistrationRequest` / `RootTaskRegistrationResult` / `RootTaskRegistrar` 协调 `ArtifactStore` 和 `EventLedger`，按规格写入 `ARTIFACT_STORED`、`TASK_REGISTERED`、`TASK_UNIT_CREATED`；这是 Phase 1 临时协调器，Phase 2 不应继续在此扩展 `TaskGraph`、`Scheduler`、`LeaseManager` 或 attempt 状态机，详见协调边界备忘录。 | `tests/test_phase1_root_registration.py` |
| `ArtifactStore` | `src/tokenshare/storage/artifacts.py` | 写入 `artifacts/`、计算 `sha256:<hex>`、读取 bytes、校验 hash 和 size、写 manifest；当前还提供 `save_json()`，通过 canonical JSON bytes 复用 `save_bytes()`。 | `tests/storage/test_artifact_store.py` |
| `EventType` / `LedgerEvent` / JSONL `EventLedger` | `src/tokenshare/storage/events.py` | 每行一个 JSON event，维护 `event_seq`、幂等键、`prev_event_hash` 和 `event_hash`；重复 `idempotency_key` 只有在事件类型、对象、任务和 canonical payload 一致时返回旧事件，冲突时抛错。 | `tests/storage/test_event_ledger.py` |
| SQLite 可重建索引 | `src/tokenshare/storage/sqlite_index.py` | 从 JSONL events 重建 Phase 1 表：`ledger_events`、`task_specs`、`task_units`、`task_relations`、`artifact_refs`、`client_records`。当前同一文件还包含 Phase 2 `leases`、`attempts`、`recovery_actions` 投影，见 Phase 2 code map。SQLite 不是权威状态源。 | `tests/storage/test_sqlite_index.py`、`tests/storage/test_phase2_event_projection.py` |

## 2. 字段规格对应

| 规格章节 | 实现方式 |
|---|---|
| 5.1 `TaskSpec` | `TaskSpec.to_dict()` 输出稳定 JSON key：`schema_version`、`task_id`、`plugin_id`、`root_input_ref`、`protocol_config` 等。 |
| 5.2 `TaskUnit` | `TaskUnit.create_root()` 固定 root unit 的 `parent_unit_id = None`、`depth = 0`、`unit_type = "root"`、`state = "Ready"`。 |
| 5.3 `TaskRelation` | `TaskRelation` 已实现最小字段；Phase 1 不主动生成关系事件。 |
| 5.4 `ClientRecord` | `ClientRecord` 已实现最小字段；Phase 1 不主动注册 client。 |
| 5.5 `ArtifactRef` | `ArtifactStore.save_bytes()` 返回 `ArtifactRef`，并把 hash、size、media type、schema 和 source 写入 snapshot。 |
| 5.6 `LedgerEvent` | `EventLedger.append()` 自动分配 `event_seq`、`event_id`、`prev_event_hash`、`event_hash`，并保存 payload snapshot。 |
| 5.7 `ProtocolConfig` | `ProtocolConfig.default()` 提供 Phase 1 基础策略默认值，调用方必须显式传入 `config_id`、`artifact_store_uri`、`event_log_uri`。 |

## 3. 最小事件顺序

规格第 6 节要求 root task happy path 事件顺序如下，本轮由 `RootTaskRegistrar.register_root_task()` 实现：

| 顺序 | 事件类型 | 代码来源 | 验证 |
|---|---|---|---|
| 1 | `ARTIFACT_STORED` | root input 写入 `ArtifactStore` 后追加。 | `tests/test_phase1_root_registration.py` 检查事件列表第 1 项。 |
| 2 | `TASK_REGISTERED` | `TaskSpec` snapshot 创建后追加。 | 同一测试检查事件列表第 2 项和 `root_input_ref`。 |
| 3 | `TASK_UNIT_CREATED` | root `TaskUnit` 创建后追加。 | 同一测试检查事件列表第 3 项和 root input 映射。 |

## 4. 存储边界

| 存储 | 权威性 | 本轮实现 |
|---|---|---|
| JSONL event ledger | 权威事实源 | `EventLedger` 负责 append/read/hash chain/idempotency。 |
| `artifacts/` | artifact 内容权威存储 | `ArtifactStore` 写入 bytes，并通过 `ArtifactRef.content_hash` 校验。 |
| SQLite | 可重建索引 | `SQLiteMaterializedIndex.rebuild_from_events()` 每次从事件重建表，不保存隐藏状态。 |

## 5. 当前未进入实现的边界

- `Lease`、`Attempt`、scheduler、状态机推进已由 `feat-003` 实现；本 Phase 1 code map 不把它们计入 Phase 1 行为，详见 `Doc/TechnicalDocument/2026-06-08-phase-2-code-map.md`。
- `RootTaskRegistrar` 是 Phase 1 兼容入口和临时协调器；Phase 2 编排入口应另行收束，不能继续在该类中堆叠状态机或调度职责。
- `PluginRegistry`、`ExecutorRegistry`、`ExecutionRequest`、`ExecutionSubmission` 属于 `feat-004`。
- factorization、Lean stub 和 structured report stub 的领域规则不进入协议核心字段；本轮只允许通过 `plugin_payload` 和 artifact schema 标识保存。
- 真实链上结算、真实分布式 worker、真实 Lean proving 仍然不属于 V1。

## 6. 2026-06-23 代码实物校准

本次校准以 `src/tokenshare/` 和 `tests/` 中当前 Python 源码为准，确认 Phase 1 code map 中提到的路径全部存在；同时把当前代码中属于 Phase 1 兼容层、但此前 map 未点名的对象补入上表。

| 代码文件 | 当前真实符号 | 本 map 处理 |
|---|---|---|
| `src/tokenshare/core/models.py` | `TaskState`、`ArtifactRef`、`ProtocolConfig`、`TaskSpec`、`TaskUnit`、`TaskRelation`、`ClientRecord`，以及 Phase 2 的 `LeaseState`、`AttemptState`、`Lease`、`Attempt` | Phase 1 对象在本文件覆盖；Phase 2 对象转由 Phase 2 code map 覆盖，避免重复算入 Phase 1。 |
| `src/tokenshare/core/registration.py` | `RootTaskRegistrationRequest`、`RootTaskRegistrationResult`、`RootTaskRegistrar.register_root_task()` | 已补入 root task registration 行。 |
| `src/tokenshare/storage/artifacts.py` | `ArtifactStore.save_bytes()`、`save_json()`、`read_bytes()`、`verify()` | 已补入 `save_json()`，其余为 Phase 1 artifact store 边界。 |
| `src/tokenshare/storage/events.py` | `EventType`、`LedgerEvent`、`EventLedger`，以及 Phase 2 event type 常量 | Phase 1 event envelope 在本文件覆盖；Phase 2 event type 转由 Phase 2 code map 覆盖。 |
| `src/tokenshare/storage/sqlite_index.py` | `SQLiteMaterializedIndex.rebuild_from_events()` 和 Phase 1/Phase 2 投影表写入分支 | Phase 1 表在本文件覆盖；Phase 2 表转由 Phase 2 code map 覆盖。 |
