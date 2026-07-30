# Ledger 权威执行回调小补丁设计

日期：2026-07-23  
状态：已实现并验证
所属 feature：`feat-011` Paper Real AI Experiments  
适用范围：第四章代码—论文对齐指南中 submission、lease、replacement token 与单协调器权威边界的小补丁

## 1. 目的

本文冻结 system runtime 迁移后剩余的一个正常时序一致性缺口：

- `ProtocolEngine.record_execution_submission()` 当前依据调用方传入的 `Attempt`、`Lease` 快照执行 acceptance；如果 recovery 已经在 ledger 中把同一 attempt/lease 写成 terminal，而调用方随后交回恢复前的旧对象，旧 submission 仍可能被接受并写出与权威历史冲突的状态边。
- `ProtocolEngine.record_lease_heartbeat()` 存在同一根因；旧 lease 对象可能在 ledger 已记录 `Released` 或 `Expired` 后继续写入 `Active` heartbeat。

该补丁用于使代码真正满足 `2026-07-22-chapter-4-code-paper-alignment-revision-guide.md` 中“submission 与当前权威记录匹配”和“旧 attempt/lease 不得推进权威状态”的方法主张。

## 2. 范围

本设计只处理受信本地研究工作流中的延迟回调、旧不可变对象和 ledger 顺序一致性。它不是针对人为攻击的防护。

范围内：

- submission 在 acceptance 前使用 ledger 中最新 attempt/lease snapshot；
- heartbeat 在状态推进前使用 ledger 中最新 lease snapshot；
- stale submission 仍保存审计 artifact/event，但不推进 attempt；
- terminal lease 的 stale heartbeat 不写新 heartbeat event；
- replacement lease 的 fencing token 局部不重复回归；
- 相关 code map、对齐指南、feature/progress/handoff 状态同步。

范围外：

- 恶意 event/artifact/manifest 篡改或伪造；
- auth、signature、ACL、path/injection、security fuzzing；
- 分布式锁、CAS、共识、全局 fencing epoch 或密码学 token；
- 多 coordinator、网络 worker、exactly-once；
- 新 event family、SQLite 主体 schema 或新的协议对象；
- 任意断点 runtime resume。

## 3. 备选方案与决定

### 3.1 方案 A：在 `ProtocolEngine` 使用 ledger 最新 snapshot（采用）

在 engine 写入边界读取已有 `_latest_attempt_snapshot()` / `_latest_lease_snapshot()`，把最新 payload 恢复为领域对象，再交给现有纯规则判断或状态机。

优点：

- `ProtocolEngine` 继续是协议事实写入权威；
- submission 和 heartbeat 共用同一 authoritative-snapshot 原则；
- 不要求 coordinator 维护第二套 current-state cache；
- 不改变 core 纯规则、event schema 或 SQLite schema；
- 可以用局部定向测试证明。

### 3.2 方案 B：只要求 coordinator 传最新对象（不采用）

当前正常同步路径大多已经这样工作，但 engine public API 仍可被普通延迟回调传入旧对象，不能兑现论文中的权威边界。

### 3.3 方案 C：每次完整 replay 后再执行（不采用）

完整重建能够得到权威状态，但会把局部写入前检查扩大成 runtime resume/replay 机制，超出当前停止线。

## 4. 权威 snapshot 解析

### 4.1 数据来源

`ProtocolEngine` 在写 submission 或 heartbeat 前读取当前 `EventLedger`：

- attempt 使用 `_latest_attempt_snapshot(events, attempt_id)`；
- lease 使用 `_latest_lease_snapshot(events, lease_id)`。

最新 payload 中的稳定枚举字符串必须显式恢复为 `AttemptState` / `LeaseState`，不能把普通字符串直接留在 dataclass 字段中。`Attempt` snapshot 中的单个和命名 `ArtifactRef` 也必须通过 `ArtifactRef.from_dict()` 恢复，不能把普通 JSON object 塞回 `Attempt`。

建议新增 engine-private 恢复 helper，例如：

```text
_authoritative_attempt(events, supplied_attempt) -> Attempt
_authoritative_lease(events, supplied_lease) -> Lease
```

具体命名可以调整，但不得把 storage/event 类型移入 `tokenshare.core.recovery`。`core.recovery` 继续只接收领域对象并保持纯决策。

实现优先复用 `protocol_engine.py` 已有 `_artifact_refs_from_dict()`；只为 nullable 单个 ref 增加 engine-private 小 helper。不得为了本补丁给所有协议模型增加通用反序列化框架。

### 4.2 兼容规则

历史和局部单元测试可能直接调用 engine，ledger 中没有对应的 state snapshot。为保持当前 V1 兼容边界：

- 找到最新 snapshot 时，必须使用最新对象；
- 完全找不到该对象的 ledger snapshot 时，回退到调用方传入对象；
- 找到 snapshot 但 payload 缺少必需字段、枚举非法或无法恢复时 fail closed，不得继续使用旧对象；
- attempt 与 lease 分别按上述规则解析，不能因为其中一个缺失就忽略另一个已经存在的权威 snapshot。

这不是历史 replay 的新保证，只是保持现有 direct engine tests 与新 system runtime 路径兼容。

## 5. Submission 行为

`record_execution_submission()` 的顺序固定为：

1. 读取 ledger 并恢复 authoritative attempt/lease；
2. 使用 authoritative 对象调用 `evaluate_submission_acceptance()`；
3. 按现有逻辑保存 submission artifact；
4. 始终写 `EXECUTION_SUBMISSION_RECORDED` v2 审计事件；
5. rejected 时返回 `attempt=None`、`attempt_event=None`；
6. accepted 时只从 authoritative `Running` attempt 推进到 `Submitted`。

拒绝原因继续使用现有稳定枚举，不新增 failure/fault type：

- authoritative attempt 已非 `Running`：`attempt_not_running`；
- authoritative lease 已非 `Active`：`lease_not_active`；
- identity/token/deadline 不匹配：继续使用当前对应原因。

当 recovery 已同时终止 attempt 和 lease 时，沿用 `evaluate_submission_acceptance()` 的现有判断顺序，稳定返回 `attempt_not_running`。

不得因为旧 submission 被拒绝而跳过审计 event，也不得让它进入 verification、canonical 或 merge。

## 6. Heartbeat 行为

`record_lease_heartbeat()` 在调用 `LeaseManager.heartbeat()` 前恢复 authoritative lease：

- 最新 lease 仍为 `Active` 时，在最新 heartbeat count / expiry 基础上生成下一次 heartbeat；
- 最新 lease 为 `Released`、`Expired` 或 `Revoked` 时，沿用 `LeaseManager.heartbeat()` 的 terminal-lease rejection，不写 heartbeat event；
- ledger 没有该 lease snapshot 时，按第 4.2 节回退到 supplied lease。

这里不能只比较整份 supplied/latest payload 是否相等。合法的前一次 heartbeat 会更新 `heartbeat_count` 和 `expires_at`；后续 heartbeat 应在最新 Active lease 上继续，而不是因调用方对象较旧而无条件拒绝。

## 7. Replacement fencing token

coordinator 当前以：

```text
safe(run_id) + schedule_ordinal
```

生成 `lease_id`、`attempt_id` 和 `fencing_token`。该逻辑满足本地单次 run 内每次重新调度使用不同 attempt-bound token 的论文边界。

本补丁不改生成算法，只增加回归：

- 同一 unit 的 primary/replacement lease ID 不同；
- 对应 fencing token 也不同；
- 不声明全局单调、跨 restart 唯一或密码学随机。

## 8. 投影和错误处理

本补丁不修改 SQLite schema。正确性来自禁止冲突状态事件进入 ledger：

- stale submission 不再产生第二条从旧 `Running` 出发的 `Submitted` transition；
- stale heartbeat 不再在 terminal lease 后写 `Active` snapshot；
- `SQLiteMaterializedIndex.rebuild_from_events()` 因而继续按最后一个合法 event 重建终态。

测试必须显式重建 SQLite，并确认：

- recovery 后 stale submission 保持 attempt terminal；
- lease 保持 `Released` 或 `Expired`；
- audit submission event 可查询，且 `acceptance_status=rejected`。

## 9. 测试边界

必须先写 RED，再实施 GREEN：

1. recovery 后使用恢复前的 `Running`/`Active` 对象提交，RED 应证明旧实现错误接受并写冲突状态边；
2. recovery 后使用恢复前的 lease heartbeat，RED 应证明旧实现写出 terminal 后的 `Active`；
3. SQLite rebuild RED 应证明旧 submission 会把终态投影回 `Submitted`；
4. replacement 回归在现有 local runtime/integration 测试上增加 token 不等断言；
5. 现有 direct engine late/deadline/fencing/rejection-reason tests 必须继续通过；
6. Factorization 与 Lean system runtime canary 必须继续通过，证明 shared engine 改动没有绕开正式路径。

## 10. 文档状态

实现完成后：

- `2026-07-22-chapter-4-code-paper-alignment-revision-guide.md` 将对应机制更新为已补齐，并把 `lease_inactive` 修正为实际稳定值 `lease_not_active`；
- `tokenshare_v1_code_map.md` 记录 submission/heartbeat authoritative-snapshot guard；
- `2026-06-29-phase-8-experiment-infrastructure-code-map.md` 只在其仍描述 runtime 权威路径时同步；
- `feature_list.json`、`progress.md`、`session-handoff.md` 记录 RED/GREEN 与验证证据；
- `feat-011` 仍保持 `in-progress`，因为正式真实-provider Experiment 1–5 尚未完成；
- 不把本补丁验证写成 Full+LeanAudit、600-entry force-all 或正式论文实验结果。

## 11. 完成标准

只有同时满足以下条件，本小补丁才完成：

- stale submission 审计保留但不能推进 terminal attempt；
- stale heartbeat 不能重新激活 terminal lease；
- latest Active heartbeat 仍能连续更新；
- replacement token 显式不重复回归通过；
- SQLite rebuild 保持 recovery 终态；
- 定向 core/storage/local-runtime/integration tests 通过；
- fixed Lean canary、Fast 和 feature 完成所需 Full 通过；
- docs/code map/feature/progress/handoff 已同步；
- provider/API calls 为 0；
- 没有增加人为攻击防护或新的故障类型。

## 12. 实施与验证记录

- stale submission RED：旧实现错误返回 `acceptance_status=accepted`；修复后精确节点、late/expired acceptance 与 core recovery 合计 `33 passed in 0.59s`。
- stale heartbeat RED：旧实现不会拒绝 `Released` lease，且第二次使用原始 lease heartbeat 会产生 count=1 幂等键冲突；修复后 Phase 2 scheduling 文件 `4 passed in 0.37s`。
- replacement token：真实 OS worker-death 精确集成节点 `1 passed in 16.96s`，并显式断言 replacement lease、attempt、fencing token 均不同。
- 定向影响面：core recovery/lease、Phase 2/3、SQLite projection、local runtime 共 `68 passed in 8.13s`；Factorization FULL、Lean fixed-plan FULL、late/rejection recovery、worker death 与写入权威边界共 `5 passed in 43.38s`。
- 固定 Lean canary：`11 passed in 118.55s`，并通过 JSON/SQLite、harness、compileall。
- Fast：最终文档回填后 `330 passed, 1 skipped in 15.92s`；回填前同为 330/1，耗时 17.28s。
- Full：收集 1303 项，`1302 passed, 1 skipped in 1082.59s`。
- provider/API calls：0。
- 未运行：600-entry Lean `--refresh --force-all`、真实 provider、正式 Experiment 1–5。
- 范围确认：未增加人为攻击防护、新 fault、分布式授权或全局 token 语义。
