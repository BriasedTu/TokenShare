# feat-011：共享 Exp1 基线、双轴终态与 Exp3–4 smoke 设计

## 1. 目标与非目标

本设计落实 `feat-011` 的三个相互约束的目标：

1. 正式 Experiment 3 不再生成 0% rate-fault 条件，也不再为 `worker_death` 额外调用模型，而是按 `case_id` 引用已经持久化的正式 Experiment 1 证据。
2. runner 用互相独立的 `outcome_status` 与 `evidence_integrity` 描述实验结果和证据健康度；真实实验失败可以继续，基础设施或身份失败必须停止新的 API 调用并闭合证据清单。
3. 新增只覆盖 Experiment 3–4 的 11-root smoke profile。该 profile 明确声明 `baseline_policy="omitted_for_smoke_regression"`，不伪造共享基线，也不得进入正式论文执行范围。

本设计不修改 provider 参数，不新增故障类型，不运行真实 API，不重写历史 run01–run04，也不把跨实验引用放宽为任意实验之间的通用能力。

## 2. 两种基线策略的边界

### 2.1 正式 Exp3：共享引用

正式 Exp3 使用 `shared_exp1_reference`。引用只允许从 `exp1_real_ai_feasibility` 流向 `exp3_fault_recovery`，并且必须满足：

- 同一个 `case_id`；
- Exp1 的正式 condition、repeat、seed、worker、provider/model/config 与 catalog/split/plugin/parser/verifier/executor 身份均匹配；
- `CURRENT.json`、generation manifest 和被引用行的 hash/schema/identity 均可验证；
- Exp1 根任务可以是实验失败，只要证据完整且身份有效。

共享引用本身的 `provider_calls`、tokens 和 cost 恒为 0；报告比较使用被引用 Exp1 证据的实际 usage。若 Exp1 是完整的实验失败，Exp3 仍执行 fault condition，但 `baseline_comparison_eligible=false`，比较 delta 为 `null`，并记录稳定原因。

### 2.2 Exp3–4 smoke：显式省略

新 smoke profile 使用 `baseline_policy="omitted_for_smoke_regression"`。runner 必须输出：

- `baseline=null`；
- `baseline_comparison_eligible=false`；
- `baseline_unavailable_reason="smoke_baseline_not_requested"`；
- supporting baseline roots/AI units/provider commitments 全部为 0。

该策略只允许 `formal=false`、`pilot=true`、`regression=true`、`paper_eligible=false` 的 smoke 执行。正式 scope 在 dispatch 前拒绝该策略。这样可以用 11 个真实根任务回归 Exp3/4 路径，同时不会把 smoke 误表述为正式比较证据。

## 3. Exp3 计划与预算

正式 rate-fault 只保留正故障率：

- factorization：1%、5%、10%、25%、50%、100%；
- Lean：10%、50%、100%；
- `worker_death`：dead worker 1、kill at 50%、worker count 10。

两次 repeat 下：

- rate-fault roots = 30,090；
- worker-death roots = 6,036；
- Exp3 roots = 36,126；
- supporting baseline roots/AI units = 0；
- 新 actual P0-core roots = 46,478；
- 新 actual P0-full roots = 48,377（沿用历史 Exp5 v2 规模）。

预算模块仍保留 headline/supporting/actual 三层字段，但 supporting 层必须为空和为零。计划与预算共享同一组 canonical constants，避免在 profile 或 launcher 中手填第二套规模。

## 4. 共享引用证据结构

共享引用使用版本化结构 `tokenshare.paper_exp1_shared_reference.v1`，至少冻结以下字段：

- source suite/run/generation；
- source experiment/condition/case/task/repeat/seed/root status；
- provider/model/entry 与 request limits；
- catalog/split/plugin/parser/verifier/executor 身份；
- source task/attempt/event/artifact refs；
- source generation/row/artifact hash；
- 引用自身的零调用、零 tokens、零 cost usage；
- 被引用 source usage；
- `baseline_comparison_eligible` 与不可比较原因。

`source_reference_id` 由 Exp1 source identity 和 `case_id` 决定，不包含 Exp3 fault condition 或 repeat。因此，同一 case 在多个 fault condition/repeat 中指向同一份 source evidence；每次 Exp3 checkpoint 仍持久化自己的 immutable reference artifact，便于审计与 replay。

缺失 CURRENT/generation、hash/schema 损坏、身份不匹配或 case 不存在均归类为基础设施阻断，不降级为“重新调用 Exp1”。replay 只读已持久化引用与 source evidence，绝不重调 provider。

## 5. 双轴终态

新增稳定枚举：

- `outcome_status`: `succeeded`、`failed_experimental`、`blocked_dependency`；
- `evidence_integrity`: `complete`、`missing`、`invalid`、`corrupt`；
- suite terminal status: `completed_with_failures`、`blocked`、`incomplete`。

语义如下：

| outcome_status | evidence_integrity | runner 行为 |
| --- | --- | --- |
| succeeded | complete | 继续执行 |
| failed_experimental | complete | 记录真实实验失败并继续执行 |
| blocked_dependency | missing/invalid/corrupt | 停止新的 API dispatch，闭合当前与未开始任务 |

`incomplete` 只用于进程被强制中断或持久化本身失败，不能作为普通失败的兜底。未知内部异常在 suite 边界被归为 infrastructure block；边界内不做 blanket catch-and-continue。

阻断闭合必须记录失败 stage/kind、最后完整 task/event、已完成/实验失败/阻断/未开始计数，并将后续根任务显式标记为 `not_started`。若清单持久化仍可用，则 experiment/suite manifest 为 `blocked`；只有闭合写入失败才为 `incomplete`。

## 6. smoke profile 与 launcher

新增 `paper_smoke_exp3_exp4_v1`，直接根任务恰好为 11 个：

- `factor_v2_easy_001` 的五类 rate fault，各取 100%；
- 同一 case 的 `worker_death`：dead worker 1、kill at 50%、worker count 10；
- 同一 case 的五种 Exp4 ablation mode；
- 全部 repeat 0、worker 10。

profile 继承 tracked DeepSeek v3 配置：official `deepseek-v4-pro`、thinking enabled、`reasoning_effort=high`、`timeout_seconds=600`、`max_tokens=300000`、`stream=false`、attempts 1、retry 0、inflight 50。

`local/run_exp3_exp4_v3_smoke.ps1` 只编排一次 identity-only 和一次同 identity 的真实 transport。它在启动前验证所有输出路径不存在、profile 的 omission policy 和预期 identity，不支持 resume/restart，并复用 `invoke_native_process_with_logs.ps1` 监督进程。仓库验证只检查 launcher，不执行它。

## 7. 兼容性与迁移

- 旧 smoke profile 若未声明 `baseline_policy`，解析为原有正式计划策略；只有新 profile 显式省略。
- 历史 v1/v2 计划和 run01–run04 保持只读，其 digest 不被改写。
- 旧 `matched_baseline_*` 报告字段如仍被消费者使用，只作为共享引用的兼容别名，不再表示额外执行。
- 新跨实验能力默认关闭，仅 formal Exp1→Exp3 purpose allowlist 可以开启。

## 8. 验证策略

每个行为先写 RED 测试并记录失败，再实现 GREEN：

1. Exp3 正故障率、共享 source identity 和预算恒等式；
2. source evidence 缺失/损坏/实验失败的引用行为；
3. 双轴枚举和 suite 阻断闭合；
4. 11-root smoke omission policy、formal scope 拒绝和 CLI identity；
5. launcher 静态监督契约；
6. metrics/report/replay 不把引用计成新调用。

最后运行定向测试、`init.ps1` Fast、identity-only、secret scan、历史目录 mtime/hash 审计；不运行真实 API、Full、全量 pytest 或 LeanAudit。
