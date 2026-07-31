# FEAT-011 Exp1/2 证据闭环修复设计

## 背景与问题

2026-07-29 的 Exp1/2 历史 smoke 产物已经证明真实 provider 调用、原始 runtime event ledger、artifact 哈希和 usage 链条可审计，但 canonical evidence 仍有五类结构性缺口：

1. 协议事件使用固定 `NOW`，真实 attempt 使用墙钟 UTC，导致 Exp2 dependency timing contradiction，无法报告数值 critical path。
2. canonical artifact index 只物化顶层引用，CandidateOutput、checker proof、Lean proof/merge 等 JSON payload 内嵌 `ArtifactRef.v1` 没有形成递归闭包。
3. canonical run 根缺少能绑定 condition identity、分母、terminal 状态与 CURRENT generation 的 `condition_manifest.json`。
4. experiment manifest 只在 suite 全部完成后统一终结；后续 experiment 抛错会让已完成 experiment 仍显示 `running`。
5. smoke/pilot 的分类性不适合写论文，被错误复用为 `attempt_evidence_incomplete` 技术证据缺失。

历史输出目录严格只读；本修复只修改生产代码、测试和文档，不重新调用 API，也不回写历史结果。

只读审计的精确历史口径为 Exp1 28 条、Exp2 60 条，共 88 条 canonical attempt：84 `succeeded`、2 `parse_failed`、1 `verification_rejected`、1 `provider_error`。其中 87 条有 `raw_output_ref`；唯一 provider error 没有 raw output，但仍有 model execution record、provenance、usage 与 request evidence。九个 condition 的 CURRENT generation 都是历史 v1，缺 condition manifest；四个 experiment manifest 与 suite manifest 仍为 `running`，且缺 suite/condition result closure。该历史结果可保留真实性和 provenance，但不得升级为新论文证据。

## 方案选择

采用 runner/adapter 集中闭环方案：

- adapter 依据 transport 类型选择统一 lifecycle clock；真实 transport 使用 UTC wall clock，offline/capturing transport 使用固定 `NOW`。
- runner 统一递归扫描和物化 artifact 引用，不要求每个插件各自枚举传递闭包。
- evidence store 在发布 checkpoint generation 时同步写 condition manifest。
- runner 在每个 experiment 完成后立即终结该 experiment 的 manifest 和 condition rows；suite 仅在全体完成后具备论文资格。
- 技术证据完整性读取分类覆盖前的 source evidence 字段；smoke/pilot 只贡献分类原因。

未采用的方案：

- 由每个 adapter 返回完整传递闭包：会在 factorization/Lean 重复实现，且新 artifact payload 很容易漏报。
- validator 通过兼容目录惰性解析缺失引用：不能保证 canonical evidence 自包含，也无法满足 replay/归档边界。
- 全局改写 `EventLedger` 时钟：影响所有确定性测试和协议行为，范围过大。

## 详细设计

### 1. 同一 UTC 域的协议与 attempt 时钟

新增 transport-aware lifecycle clock helper：

- `real_transport=True` 且 transport 不是 offline/capturing：返回 UTC wall clock。
- 其他情况：返回固定 `NOW`。

Factorization adapter 将同一 clock 同时传给：

- `ProtocolRunCoordinator.now`
- `ProtocolRunCoordinator.observation_clock`
- worker backend `submitted_at`

Lean adapter使用同一 helper 做最小一致性接线。这样真实执行的 protocol event、runtime observation、attempt interval 均处于同一 UTC 时间域；offline/capturing fixture 仍保持可重复字节输出。

Exp2 critical path 继续使用现有 canonical dependency graph：task relation、bind/submit/complete event、attempt interval、merge link 和 root completion。修复时钟后，完整真实 graph 必须得到非负数值；缺失依赖或时间矛盾仍 fail closed，不允许用 wall-clock 总时长替代。

这里不承诺 `occurred_at` 按 `event_seq` 全局单调：`occurred_at` 是并发 worker 的业务观察时间，worker 可以先取时、后提交；`event_seq` 只表示 ledger commit order。dependency DAG 必须逐边验证时间不反转，数值 critical path 仅在完整图通过该校验时成立。

### 2. 递归 canonical artifact closure

`_materialize_artifacts` 改为 BFS：

1. 从 task、attempt、fault 等已知记录中的 `ArtifactRef.v1` 入队。
2. 对每个引用解析源文件、校验 bytes/hash/size，并复制到 canonical artifact root。
3. 如果 payload 是 JSON，递归遍历 mapping/list，发现新的 `ArtifactRef.v1` 后继续入队。
4. 以稳定 artifact identity 与 content hash 去重，最终稳定排序输出 canonical index。
5. 任一可达引用缺失、哈希不符或同一 identity 冲突时 fail closed。

闭包必须覆盖 candidate、checker proof、Lean proof/merge、model request/response/usage 等嵌套引用；validator 只认可 canonical index 中可解析且哈希一致的对象。

### 3. condition manifest

每个 canonical run 根新增 `condition_manifest.json`，至少包含：

- schema version；
- `experiment_id`、`condition_id`、`repeat_id`；
- canonical condition identity body 与 digest；
- expected、observed、terminal root denominator；
- run status 与 terminal 布尔值；
- CURRENT、generation manifest、run manifest 的相对引用和 digest；
- `paper_eligible` 与稳定排序的 ineligibility reasons。

condition manifest 在 checkpoint generation 与 CURRENT 原子发布完成后写入，并纳入 evidence inventory。任何 digest/identity/denominator 不一致都使验证失败。

checkpoint publication 使用排除在 inventory 之外的 `PENDING.json`：先冻结目标 generation、父 generation/CURRENT identity 与 digest，再按 generation→CURRENT→condition manifest→inventory 顺序提交。resume 只允许按 intent 指定的目标补完；没有 PENDING 的孤立 generation 不会自动晋升。历史 generation v1 保持只读兼容，新的 CURRENT publication 使用 v2 父链。

### 4. experiment 增量终结

从 suite 末尾统一 finalizer 中抽出幂等的 per-experiment finalizer：

- 每个 plan 的所有选定 condition 得到 terminal result 后，立即原子写入该 experiment 的 terminal manifest。
- 同步 upsert 该 experiment 的 completed condition result rows，避免后续异常丢失已完成结果。
- suite manifest 在执行中保持 `running` 且 `paper_eligible=false`；只有完整 suite terminal 且所有论文条件成立时才可变为 true。
- suite 尾部 finalizer 复用相同 helper，支持 blocked/interrupted 结果并保持幂等。

per-experiment 与 suite terminal commit 都有 top-level `PENDING.json` publication intent。前者以 `condition_results.jsonl`→`experiment_manifest.json` 为两阶段目标，后者以 `formal_runner_result.json`→`suite_manifest.json` 为两阶段目标；intent 固定 prior/target digest，resume 只接受 prior 或 target 两种状态，随后重建 evidence inventory 并清理 intent。这样 rows 后、manifest 后或 inventory 后进程中断都可验证恢复，不用宽泛自动刷新掩盖未知文件。若 suite result 尚未存在，resume 从 canonical CURRENT generation 的 protocol attempts 重算 provider count、tokens、nullable/currency-aware cost；负面 terminal root 同样不会再次调用 provider。

### 5. 分类与技术证据完整性解耦

attempt checkpoint 用独立的 `paper_ineligibility_reasons` 保存技术证据缺口。`attempt_evidence_incomplete` 只在该字段缺失、格式错误或非空时出现；不得读取已被 smoke/pilot 分类强制覆盖为 false 的 `paper_eligible`。`smoke`、`pilot` 等原因独立决定论文资格。因而技术完整的 smoke 可以同时满足：

- `attempt_evidence_incomplete` 不出现；
- `smoke` 或 `pilot` 分类原因仍存在；
- `paper_eligible=false`。

## 验证策略

严格执行 RED→GREEN：

1. real transport fixture 证明 protocol/attempt 同域且 Exp2 critical path 为数值；offline capturing 时间戳保持固定。
2. 构造多层 JSON `ArtifactRef.v1`，先证明当前 index 漏引用，再证明闭包完整、bytes/hash/size 正确。
3. checkpoint 测试 condition manifest identity digest、denominator、terminal、CURRENT/generation refs。
4. 两个 experiment 中第二个故意中断，证明第一个已终结且 suite 不具论文资格。
5. smoke 技术证据完整时不再产生 `attempt_evidence_incomplete`。
6. 对 experiment 与 suite publication 分别在两个 target 写入后和 inventory 刷新后注入中断，证明 resume 按 PENDING 幂等补完。
7. 在 failed terminal root 后、finalizer 前中断，证明 resume 不重新调用 provider且 usage 不丢失。
8. 保持 protocol task id model inventory、canonical ledger exact/hash/sidecar、timeout nullable metrics CSV 的 Exp1/2 回归。

只运行相关 targeted tests；不运行真实 API、`-Full`、`-LeanAudit` 或攻击面测试。
