# Exp1 统一 Acquisition 与 Exp2–4 Trace Smoke 设计

**日期：** 2026-08-09  
**状态：** 用户已批准实施

## 目标

把 Experiment 1 扩展为 Experiment 2–4 的统一真实回答 acquisition 阶段。Exp1 阶段对冻结的 4 Factorization + 4 Lean smoke 题及 Exp3 明确需要的 replacement slots 进行一次真实 API acquisition；Exp2–4 随后只消费该不可变 response bank，当前 provider call 必须为 0，但仍完整执行各自的协议、故障、验证、checker、merge 与指标投影。

Experiment 5 保持独立 endpoint comparison，不消费 DeepSeek Exp1 bank。

## 不可突破的边界

- 允许旁路 receipt、L1–L4、publication closure、历史 digest 与其他非指标实验设施门禁。
- 不允许旁路 `ProtocolEngine`、Factorization parser/verifier、Lean 固定 checker、故障 hook 的真实生命周期、模型身份校验或指标计算。
- trace-backed 运行必须明确标记为 `real_model_trace_protocol_run`，不得冒充当次 `real_transport`。
- acquisition 的实际支出与 Exp2–4 的 trace attribution 分账：前者只计一次 actual spend，后者报告所消费真实回答的 source latency/tokens/cost，并报告 current provider calls = 0。

## 数据流

1. 从 Exp1 统一 acquisition profile 生成稳定 outbound request identity 和 semantic-slot inventory。
2. 对基础 sample slots 与 Exp3 replacement slots 各采集一次；已存在对象只有在 request body、endpoint、model、reasoning controls、case、AI unit、sample/replacement slot 与 object digests 全部精确匹配时才能导入。
3. 将 raw response、provider provenance、usage、latency、pricing 和 terminal status 写入 immutable response bank。
4. Exp2–4 通过 `PaperTraceRuntimeContext` / `TraceSourceBinding` 消费 bank，重新进入原 adapter、coordinator、`ProtocolEngine` 与领域验证链。
5. Exp3 的 replacement slot 只有在故障条件真实触发 replacement 时消费；它不能用于替换普通错误回答。
6. Exp4 的 FULL 仍是 Exp4 自己执行的协议基线，四个 ablation 与 FULL 使用相同 Exp1-acquired sample/replacement slots；不能把 Exp1 的 root 终态直接当作 Exp4 FULL 终态。

## 错误回答与分母

Exp1 acquisition 返回的真实错误、parse failure、checker/verifier rejection 或 provider failure，只要有完整真实 terminal evidence，都原样冻结并由 Exp2–4 消费。禁止：

- 只保留正确回答；
- 因回答错误而自动重采；
- 用 replacement slot “洗白”基础错误；
- 从 Exp2–4 的正确率或完成率分母删除错误 root。

统计语义固定为：实验性错误为 `False` 并保留在固定 root 分母；真正缺失/不可判定为 `None` 并带 reason。`False` 与 `None` 不得合并。

## Lean Exp3 fault hook

parsed-candidate fault 必须发生在 AI parser 成功之后、固定 Lean checker 之前。变异后的 candidate refs 作为 checker 输入，重新产生 checker report；禁止把变异 artifact 与旧 accepted checker report 组合。coordinator 后续对同一 typed fault observation 只能幂等 no-op。

## Smoke 范围与验收

- Exp1–5 各 8 roots：4 Factorization + 4 Lean。
- Exp1：统一 acquisition/source run；真实 API、真实指标。
- Exp2–4：trace-backed，current provider calls = 0；每个条件真实执行；8-root 固定分母；source latency/token/cost 与 correctness/completion 均可追溯。
- Exp5：独立四 endpoint 真实 API；若 `SILICONFLOW_API_KEY` 缺失则明确报告外部 blocker，不伪造结果。
- 只运行定向 TDD、离线集成 smoke 和最终真实 smoke；不以 Full/Fast 作为本轮验收门槛。

## 恢复策略

- provider dispatch 后但 bank terminal 未闭合的 ambiguous slot 不盲目重试；先按持久化 request/provenance/usage/terminal evidence 对账。
- Exp2–4 trace run 可从已闭合 checkpoint 恢复，因为不会重新调用 provider。
- 任何需要新增 provider 回答的缺槽只能回到 Exp1 acquisition 阶段，在新的 acquisition attempt root 中补采一次。
