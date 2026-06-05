# Phase 1 最小对象字段规格

| 项 | 值 |
|---|---|
| 日期 | 2026-06-05 |
| 状态 | Draft |
| 对应 feature | `feat-002` - Phase 1 - Protocol Base Objects and Storage |
| 目标 | 固化 Phase 1 最小对象字段、JSONL event envelope 和 SQLite 可重建索引边界。 |
| 非目标 | 不实现代码；不定义完整 `Lease`、`Attempt`、验证、合并或结算字段；不引入外部 workflow runtime。 |
| 上游依据 | `Doc/TechnicalDocument/2026-06-03-tokenshare-protocol-technical-design.md`、`Doc/TechnicalDocument/2026-06-02-tokenshare-protocol-kernel-revised-draft.md` |

## 1. 如何阅读本规格

本文件是字段规格，不是代码实现。它用于让后续 Python dataclass、JSONL event payload 和 SQLite materialized index 使用同一套命名边界。

| 名称层级 | 示例 | 含义 | 未来代码落点 |
|---|---|---|---|
| 协议对象名 | `TaskSpec`、`TaskUnit`、`ArtifactRef` | 协议语义对象。后续通常会实现为 Python `dataclass` 或等价类。 | `tokenshare.core` 或 `tokenshare.storage` 中的对象定义。 |
| 对象字段名 | `task_id`、`schema_version`、`content_hash` | 协议对象里的稳定字段，也是持久化 JSON key。 | Python 属性、JSON key，部分字段会进入 SQLite 索引列。 |
| 字段值或枚举值 | `state = "Ready"`、`artifact_type = "root_input"` | 字段的取值。后续可实现为 `Enum`，但本规格先定义稳定字符串。 | Python enum 或常量。 |
| 事件类型 | `TASK_REGISTERED`、`ARTIFACT_STORED` | `LedgerEvent.event_type` 的取值。表示发生过的协议事实。 | 常量或 enum，写入 JSONL。 |
| SQLite 索引表名 | `task_specs`、`artifact_refs` | 从 JSONL event 和 artifact ref 重建出的查询表。不是权威状态源。 | `tokenshare.storage` 的 SQLite materialized index。 |
| Artifact schema 标识 | `artifact_schema_id`、`artifact_schema_version` | artifact 内容格式的版本标识。用于插件和审计检查。 | 插件 descriptor、artifact ref 和验证逻辑。 |

## 2. 设计原则

| 原则 | 约束 |
|---|---|
| JSONL event ledger 是权威事实源 | 删除 SQLite 后，必须能仅凭 JSONL event 和 artifact 文件重建协议状态。 |
| SQLite 只是可重建索引 | SQLite 表可以提高查询效率，但不能保存唯一事实或隐藏状态。 |
| 所有持久化对象显式版本化 | 每个协议对象和 event 都带 `schema_version`。 |
| 核心对象不硬编码插件领域 | factorization、Lean stub 的领域细节放入 `plugin_payload` 或 artifact schema，不进入协议核心字段。 |
| Phase 1 只保留最小闭环字段 | 先支撑 root task registration、artifact save/read/hash、event append/read。 |
| 大内容不内联进 event | event 保存 `ArtifactRef` 和摘要，不保存大型 prompt、日志或模型原文。 |

## 3. 参考项目借鉴记录

| 参考项目 | 借鉴点 | TokenShare 中的采用方式 |
|---|---|---|
| Luigi | `task_id` 由任务族和参数派生；调度器保存 `status`、`deps`、`priority`、`resources`。 | `TaskUnit` 固定 `unit_id`、`state`、`required_capabilities`、`weight`，依赖通过 `TaskRelation` 表达。 |
| CWL / cwltool | typed input/output、文件 `location`、`checksum`、`format`。 | `ArtifactRef` 统一保存 `uri`、`content_hash`、`artifact_schema_id`、`media_type`。 |
| Temporal | append-only history、事件序号、事件类型、重放不重新执行非确定性逻辑。 | `LedgerEvent` 使用 `event_seq`、`event_type`、hash chain 和 replay-friendly payload。 |
| Dagster | event log 区分 run、step、timestamp、event-specific data。 | `LedgerEvent` 区分 `task_id`、`object_type`、`object_id`、`payload`。 |
| Prefect | state 和 artifact 都有类型、时间、数据引用或 metadata。 | `TaskUnit.state`、`ArtifactRef.artifact_type`、`metadata` 保持轻量可扩展。 |

## 4. 协议对象总览

| 协议对象名 | 含义 | 是否可能是未来类名 | Phase 1 角色 | 主要持久化位置 |
|---|---|---|---|---|
| `TaskSpec` | 根任务注册对象，固定插件、拆分策略、根输入、预算和协议配置。 | 是 | `register_task` 输入和 `TASK_REGISTERED` payload。 | JSONL event；SQLite `task_specs` 索引。 |
| `TaskUnit` | 任务图中的可执行、可拆分或可合并节点。 | 是 | 创建 root unit，初始状态为 `Ready`。 | JSONL event；SQLite `task_units` 索引。 |
| `TaskRelation` | 任务节点之间的拆分边或依赖边。 | 是 | Phase 1 只定义字段，root task happy path 可暂不生成关系。 | JSONL event；SQLite `task_relations` 索引。 |
| `ClientRecord` | 本地模拟客户端或执行器能力记录。 | 是 | Phase 1 只定义字段，后续调度前实现。 | JSONL event；SQLite `client_records` 索引。 |
| `ArtifactRef` | 输入、输出、日志或原始执行结果的数据引用。 | 是 | root input 保存和内容哈希校验。 | artifact store manifest / event payload；SQLite `artifact_refs` 索引。 |
| `LedgerEvent` | append-only JSONL 事件。 | 是 | `EventLedger.append/read` 的最小记录单位。 | JSONL event ledger。 |
| `ProtocolConfig` | 第一版运行策略快照。 | 是 | `TaskSpec` 内嵌或引用的配置快照。 | `TaskSpec` payload；后续可进入 run metadata。 |

## 5. 字段规格

### 5.1 `TaskSpec`

| 字段名 | 必填 | 建议类型 | 含义 | Phase 1 用途 |
|---|---|---|---|---|
| `schema_version` | 是 | string | `TaskSpec` 持久化 schema 版本。 | replay 时选择解析器。 |
| `task_id` | 是 | string | 根任务全局标识。 | 关联 root unit、event 和 artifact。 |
| `description` | 是 | string | 人类可读的任务描述。 | 审计和报告。 |
| `plugin_id` | 是 | string | 使用的任务插件标识。 | 固定插件边界，不把领域逻辑放进核心。 |
| `plugin_version` | 是 | string | 使用的插件版本。 | replay 和审计时固定版本。 |
| `split_strategy_id` | 是 | string | 拆分策略标识。 | 固定本次运行的拆分逻辑。 |
| `split_strategy_params` | 是 | object | 拆分策略参数。 | 作为稳定 JSON 保存。 |
| `root_input_ref` | 是 | `ArtifactRef` snapshot | 根输入 artifact 引用。 | root input hash 校验和 replay。 |
| `root_budget` | 否 | number | 根任务 sandbox 预算。 | 后续结算使用；Phase 1 可保存但不消费。 |
| `root_deadline` | 否 | string, ISO-8601 UTC | 根任务截止时间。 | 后续调度使用。 |
| `protocol_config` | 是 | `ProtocolConfig` snapshot | 本次运行的协议配置快照。 | 保证 replay 使用同一策略。 |
| `metadata` | 是 | object | 非核心扩展字段。 | 允许实验标签，不影响核心语义。 |
| `created_at` | 是 | string, ISO-8601 UTC | 创建时间。 | 审计和排序。 |

### 5.2 `TaskUnit`

| 字段名 | 必填 | 建议类型 | 含义 | Phase 1 用途 |
|---|---|---|---|---|
| `schema_version` | 是 | string | `TaskUnit` 持久化 schema 版本。 | replay 解析。 |
| `unit_id` | 是 | string | 任务节点标识。 | root unit 返回值和 event 关联。 |
| `task_id` | 是 | string | 所属根任务标识。 | 跨对象关联。 |
| `parent_unit_id` | 否 | string or null | 父节点标识。root unit 为 `null`。 | 表达递归树位置。 |
| `depth` | 是 | integer | 节点深度。root unit 为 `0`。 | 检查 `max_depth`。 |
| `unit_type` | 是 | string | 节点类型。由协议保存，插件解释。 | root unit 可用 `root`。 |
| `state` | 是 | string | 生命周期状态。Phase 1 初始用 `Ready`。 | SQLite 查询 ready/root 状态。 |
| `input_refs` | 是 | object | 命名输入到 `ArtifactRef` 的映射。 | root unit 指向 root input。 |
| `canonical_output_refs` | 是 | object | 命名正式输出到 `ArtifactRef` 的映射。初始为空。 | Phase 1 先保存空对象。 |
| `required_capabilities` | 是 | object | 执行该节点需要的能力声明。 | 后续匹配 `ClientRecord`。 |
| `weight` | 是 | number | 贡献和结算权重。 | 后续 settlement；创建时固定。 |
| `budget_limit` | 否 | number or null | 节点局部预算限制。 | 后续使用。 |
| `deadline` | 否 | string or null | 节点局部截止时间。 | 后续调度使用。 |
| `plugin_payload` | 是 | object | 插件领域负载。 | factorization / Lean 字段放这里。 |
| `metadata` | 是 | object | 非核心扩展字段。 | 实验标签。 |
| `created_at` | 是 | string, ISO-8601 UTC | 创建时间。 | 审计。 |
| `updated_at` | 是 | string, ISO-8601 UTC | 最近更新时间。 | SQLite 索引查询。 |

### 5.3 `TaskRelation`

| 字段名 | 必填 | 建议类型 | 含义 | Phase 1 用途 |
|---|---|---|---|---|
| `schema_version` | 是 | string | `TaskRelation` 持久化 schema 版本。 | replay 解析。 |
| `relation_id` | 是 | string | 关系标识。 | event 和 SQLite 主键。 |
| `task_id` | 是 | string | 所属根任务标识。 | 查询任务图。 |
| `relation_type` | 是 | string | `decomposition` 或 `dependency`。 | 区分递归拆分边和执行依赖边。 |
| `source_unit_id` | 是 | string | 来源节点。 | 图边起点。 |
| `target_unit_id` | 是 | string | 目标节点。 | 图边终点。 |
| `source_output_name` | 否 | string or null | 依赖的来源命名输出。 | dependency 边使用。 |
| `target_input_name` | 否 | string or null | 目标节点输入名。 | dependency 边使用。 |
| `created_reason` | 是 | string | 创建原因。 | 审计插件展开或协议创建。 |
| `metadata` | 是 | object | 非核心扩展字段。 | 保留扩展空间。 |
| `created_at` | 是 | string, ISO-8601 UTC | 创建时间。 | 审计和排序。 |

### 5.4 `ClientRecord`

| 字段名 | 必填 | 建议类型 | 含义 | Phase 1 用途 |
|---|---|---|---|---|
| `schema_version` | 是 | string | `ClientRecord` 持久化 schema 版本。 | replay 解析。 |
| `client_id` | 是 | string | 客户端标识。 | 后续 lease/attempt 关联。 |
| `executor_type` | 是 | string | 执行器类别。 | 匹配本地、确定性程序、AI stub 等。 |
| `executor_id` | 是 | string | 执行器实现标识。 | 审计和调度。 |
| `executor_version` | 是 | string | 执行器版本。 | 固定执行环境。 |
| `capabilities` | 是 | object | 能力声明。 | 后续匹配 `TaskUnit.required_capabilities`。 |
| `status` | 是 | string | `available`、`offline` 或 `disabled`。 | 后续调度过滤。 |
| `stats` | 是 | object | 协议统计，例如成功次数、失败次数。 | 后续指标和调度。 |
| `metadata` | 是 | object | 非核心扩展字段。 | 实验标签。 |
| `registered_at` | 是 | string, ISO-8601 UTC | 注册时间。 | 审计。 |
| `last_seen_at` | 否 | string or null | 最近可见时间。 | 后续故障模拟。 |

### 5.5 `ArtifactRef`

| 字段名 | 必填 | 建议类型 | 含义 | Phase 1 用途 |
|---|---|---|---|---|
| `schema_version` | 是 | string | `ArtifactRef` 持久化 schema 版本。 | replay 解析。 |
| `artifact_id` | 是 | string | artifact 标识。 | 读取和索引。 |
| `artifact_type` | 是 | string | `root_input`、`candidate_output`、`canonical_output`、`log`、`raw_executor_output`、`prompt_package` 等。 | Phase 1 使用 `root_input`。 |
| `uri` | 是 | string | 本地 artifact URI 或相对路径 URI。 | `ArtifactStore` 读取数据。 |
| `content_hash` | 是 | string | 内容哈希，建议格式 `sha256:<hex>`。 | 保存后校验。 |
| `size_bytes` | 是 | integer | 内容大小。 | 基础完整性检查。 |
| `media_type` | 是 | string | 内容媒体类型，例如 `application/json`。 | 读取和插件解析提示。 |
| `artifact_schema_id` | 是 | string | 内容 schema 标识。 | 插件和审计检查。 |
| `artifact_schema_version` | 是 | string | 内容 schema 版本。 | replay 兼容。 |
| `source` | 是 | object | 来源追踪，例如 `{"kind": "client_input"}` 或 attempt 信息。 | root input 来源审计。 |
| `metadata` | 是 | object | 非核心扩展字段。 | 实验标签。 |
| `created_at` | 是 | string, ISO-8601 UTC | 创建时间。 | 审计。 |

### 5.6 `LedgerEvent`

| 字段名 | 必填 | 建议类型 | 含义 | Phase 1 用途 |
|---|---|---|---|---|
| `schema_version` | 是 | string | event envelope schema 版本。 | replay 解析。 |
| `event_seq` | 是 | integer | ledger 内单调递增序号。 | 顺序重放。 |
| `event_id` | 是 | string | 事件标识。 | 幂等和审计。 |
| `event_type` | 是 | string | 事件类型常量。 | 分派 replay handler。 |
| `occurred_at` | 是 | string, ISO-8601 UTC | 事件发生时间。 | 审计和指标。 |
| `task_id` | 否 | string or null | 关联根任务。 | 跨对象查询。 |
| `object_type` | 是 | string | 事件主对象类型，例如 `TaskSpec`、`TaskUnit`、`ArtifactRef`。 | 通用索引。 |
| `object_id` | 是 | string | 事件主对象标识。 | 通用索引。 |
| `actor` | 是 | object | 触发者，例如 protocol、client、plugin。 | 审计。 |
| `correlation_id` | 否 | string or null | 同一操作链路标识。 | 将多条事件串成一次操作。 |
| `causation_event_id` | 否 | string or null | 直接导致本事件的上游事件。 | 因果链。 |
| `idempotency_key` | 是 | string | 重复写入保护键。 | 防止重复注册或重复绑定。 |
| `payload` | 是 | object | 事件特定载荷。 | 保存对象 snapshot 或变更摘要。 |
| `prev_event_hash` | 否 | string or null | 前一条事件 hash。第一条可为 `null`。 | hash chain。 |
| `event_hash` | 是 | string | 当前事件 hash。 | append-only 完整性检查。 |

`event_hash` 应基于稳定 JSON canonical form 计算，计算输入不包含 `event_hash` 自身。

### 5.7 `ProtocolConfig`

| 字段名 | 必填 | 建议类型 | 含义 | Phase 1 用途 |
|---|---|---|---|---|
| `schema_version` | 是 | string | `ProtocolConfig` schema 版本。 | replay 解析。 |
| `config_id` | 是 | string | 配置快照标识。 | 关联 `TaskSpec`。 |
| `lease_ttl_seconds` | 是 | integer | lease 默认有效期。 | 后续调度。 |
| `heartbeat_interval_seconds` | 是 | integer | 心跳间隔。 | 后续故障恢复。 |
| `max_retries` | 是 | integer | 最大重试次数。 | 后续 attempt 恢复。 |
| `retry_backoff_seconds` | 是 | integer | 重试退避基础值。 | 后续恢复。 |
| `allow_shadow_execution` | 是 | boolean | 是否允许影子执行。 | 后续冗余验证。 |
| `scheduling_policy` | 是 | string | 调度策略标识。 | 后续 scheduler。 |
| `canonical_output_policy` | 是 | string | 正式输出选择策略，V1 默认 `first_verified_bundle`。 | 后续验证绑定。 |
| `max_depth` | 是 | integer | 最大递归深度。 | 创建 child unit 时检查。 |
| `max_children_per_unit` | 是 | integer | 单节点最大子节点数。 | 展开时检查。 |
| `max_total_units` | 是 | integer | 单根任务最大节点总数。 | 图规模限制。 |
| `max_expansions_per_unit` | 是 | integer | 单节点最大展开次数。 | 防止重复展开。 |
| `artifact_store_uri` | 是 | string | artifact store 根 URI。 | Phase 1 写入 root input。 |
| `event_log_uri` | 是 | string | JSONL event ledger URI。 | Phase 1 append/read。 |
| `base_reward_rates` | 是 | object | sandbox reward 基础倍率。 | 后续结算；Phase 1 只保存。 |
| `metadata` | 是 | object | 非核心扩展字段。 | 实验标签。 |

## 6. Phase 1 最小事件集合

| 事件类型 | 主对象 | 最小 payload | 幂等键建议 | 说明 |
|---|---|---|---|---|
| `ARTIFACT_STORED` | `ArtifactRef` | `{"artifact_ref": ArtifactRef}` | `artifact:<content_hash>` | artifact 已写入并通过 hash 计算。 |
| `TASK_REGISTERED` | `TaskSpec` | `{"task_spec": TaskSpec}` | `register_task:<task_id>` | 根任务注册事实。 |
| `TASK_UNIT_CREATED` | `TaskUnit` | `{"task_unit": TaskUnit}` | `task_unit_created:<unit_id>` | 创建 root unit 或后续 child unit。 |
| `TASK_RELATION_CREATED` | `TaskRelation` | `{"task_relation": TaskRelation}` | `task_relation_created:<relation_id>` | Phase 1 定义，后续 expand 时使用。 |
| `CLIENT_REGISTERED` | `ClientRecord` | `{"client_record": ClientRecord}` | `client_registered:<client_id>` | Phase 1 定义，后续 scheduler 使用。 |

Phase 1 root task happy path 的最小事件顺序：

| 顺序 | 事件类型 | 结果 |
|---|---|---|
| 1 | `ARTIFACT_STORED` | root input artifact 可读、可 hash 校验。 |
| 2 | `TASK_REGISTERED` | `TaskSpec` 固定 root input、插件版本和协议配置。 |
| 3 | `TASK_UNIT_CREATED` | root `TaskUnit` 创建，`state = "Ready"`。 |

## 7. SQLite 可重建索引

SQLite 表名不是协议对象名，也不是权威状态源。它们只是从 JSONL event payload 和 artifact refs 重建出的查询视图。

| 表名 | 主键 | 建议索引列 | payload 列 | 来源事件 |
|---|---|---|---|---|
| `ledger_events` | `event_seq` | `event_id`、`event_type`、`task_id`、`object_type`、`object_id`、`occurred_at`、`event_hash` | `payload_json` | 所有 `LedgerEvent`。 |
| `task_specs` | `task_id` | `plugin_id`、`plugin_version`、`created_at` | `payload_json` | `TASK_REGISTERED`。 |
| `task_units` | `unit_id` | `task_id`、`parent_unit_id`、`state`、`depth`、`created_at`、`updated_at` | `payload_json` | `TASK_UNIT_CREATED`，后续状态事件。 |
| `task_relations` | `relation_id` | `task_id`、`relation_type`、`source_unit_id`、`target_unit_id` | `payload_json` | `TASK_RELATION_CREATED`。 |
| `artifact_refs` | `artifact_id` | `artifact_type`、`uri`、`content_hash`、`artifact_schema_id`、`created_at` | `payload_json` | `ARTIFACT_STORED` 和后续 artifact 引用事件。 |
| `client_records` | `client_id` | `executor_type`、`status`、`last_seen_at` | `payload_json` | `CLIENT_REGISTERED` 和后续 client 状态事件。 |

## 8. 模块归属

| 内容 | 建议模块 | 说明 |
|---|---|---|
| `TaskSpec`、`TaskUnit`、`TaskRelation`、`ClientRecord`、`ProtocolConfig` | `tokenshare.core` | 协议对象和状态边界，不依赖文件系统。 |
| `ArtifactRef` | `tokenshare.core` 或 `tokenshare.storage` | 如果只表达引用，可放 core；如果包含本地路径细节，应放 storage。实现前需要最终决定。 |
| `ArtifactStore` | `tokenshare.storage` | 负责写入、读取、hash 校验和 artifact manifest。 |
| `LedgerEvent`、JSONL `EventLedger` | `tokenshare.storage` | 负责 append-only、幂等键、hash chain、read。 |
| SQLite materialized index | `tokenshare.storage` | 只从 JSONL 和 artifact refs 重建，不提供隐藏权威状态。 |

## 9. 后续实现验收点

| 验收点 | 期望 |
|---|---|
| root task registration | 输入 `TaskSpec` 后产生 root `TaskUnit` 引用，并写入 `TASK_REGISTERED` 和 `TASK_UNIT_CREATED`。 |
| artifact save/read/hash | root input 写入 `ArtifactStore` 后，`ArtifactRef.content_hash` 可复算一致。 |
| event append/read | `EventLedger` 能 append JSONL event，并按顺序 read 出相同 envelope。 |
| SQLite 可重建 | 删除 SQLite 后，可以从 JSONL event 和 artifact refs 重建索引表。 |
| 插件边界 | `plugin_payload` 可保存领域字段，但协议核心字段不出现 factorization 或 Lean 专属字段。 |

## 10. 暂存决策和开放点

| 项 | 当前建议 | 后续决策点 |
|---|---|---|
| ID 生成 | 使用带前缀的稳定字符串，例如 `task_...`、`unit_...`、`artifact_...`。 | 实现时决定使用 UUID、内容派生 ID，或 deterministic test ID。 |
| `ArtifactRef` 模块位置 | 倾向先放 `tokenshare.core`，因为它是协议引用对象；本地读写逻辑放 `tokenshare.storage`。 | 如果实现发现路径语义过重，可拆为 core ref 和 storage manifest。 |
| 时间格式 | 使用 ISO-8601 UTC 字符串。 | 实现时统一 helper，避免本地时区污染 replay。 |
| enum 实现 | 本规格先固定字符串值。 | 实现时可用 Python `Enum`，但 JSON 仍保存字符串。 |
| event payload 形态 | 保存对象 snapshot。 | 后续状态变更事件可改为 snapshot + delta，但必须保持 replay 简单。 |
