# Experiment design contract

本文件概述实验问题、冻结条件、执行语义和输出边界。实现入口见 [代码导航](code-map.md)，核验步骤见 [复现指南](../../REPRODUCIBILITY.md)。

## 设计原则

TokenShare experiments 是“冻结实验计划 + 现有系统本体 + 两种回答来源 + 普通增量输出 + 离线 reducer”。

- 协议状态、租约、attempt、verification、canonical、merge、settlement 等行为由现有 TokenShare runtime 负责。
- Factorization 与 Lean 领域语义由各自插件负责。
- 实验层只负责枚举冻结 condition/root、选择回答来源、记录最小原始字段并离线汇总指标。
- 公开分支不建立第二套协议真值，也不引入发布资格、预算审批或证据闭包。

## 两种回答来源

| 来源 | 使用范围 | 约束 |
|---|---|---|
| real-provider caller | Experiment 1 与 Experiment 5 | 只命中冻结 provider entry；保存 raw response、usage、latency、model identity；secret 不落盘 |
| fixed-response trace executor | Experiment 2、3、4 | 只读 Experiment 1 per-unit trace；按 `case_id × source_repeat_id=0 × planned_ai_unit_id` 查找；provider calls 固定为 0 |

fixed-response trace 缺少当前 ordinal 时，只能确定性回退到同一 trace 的最后一个自然 attempt；不得补调用、换模型或读取其他来源。

## Experiment 1

- 覆盖 Factorization 与 Lean 两个 domain。
- 使用 DeepSeek Flash entry `deepseek_v4_flash_exp1_baseline`。
- 每个 root 先运行完整协议生命周期并提交 root 结果。
- 同 profile 下会被 Experiment 2–4 消费的 source roots 才执行 coverage tail；非 source roots 明确记录 `trace_tail_status=not_required_by_downstream`。
- coverage tail 的时间、tokens、cost 单列，不进入 Experiment 1 正文 protocol runtime。

## Experiment 2

- 完整继承 Experiment 1 的 task split、planned AI units、prompt 与依赖。
- 唯一实验变量是 worker count。
- 运行使用 fixed-response trace executor，不调用 provider。
- speedup 和效率指标使用逻辑调度器观测值，不从真实 provider latency 重跑。

## Experiment 3

- Full profile 使用 49 个 Factorization case 和 3 个 Lean case，共 52 个 case；展开为 3,654 个 paper fault roots，并单列 104 个 auxiliary references。
- 使用冻结五类 rate fault：`false_positive`、`false_negative`、`no_return`、`late_submission`、`executor_error`。
- worker death 是单独预注册实验条件，不是新增 fault type。
- token/latency 采用冻结扰动公式，正文资源为 simulated trace-attributed。
- 全部运行只复用 Experiment 1 trace，provider-call upper 为 0。

## Experiment 4

- Full profile 使用 49 个 Factorization case 和 15 个 Lean case，共 64 个 case、3 个 repeats；FULL、四个单机制关闭、六个双机制关闭共 11 个 modes，展开为 2,112 个 paper roots。
- 全部运行只复用 Experiment 1 trace，provider-call upper 为 0。
- challenge plan 在 mode 展开前按 `case_id × repeat_id` 固定；注入器不能读取 mode。
- 四个 challenge families 各包含 48 个固定 plans，共 192 个 plans。
- outcome 必须来自实际 route/event/artifact/checker 证据，不能由 mode 配置反推。
- `{R,M}` 使用 `RECOVERY_MERGE_FIRST`：premature merge 抢先时 recovery 观察记为 preempted，不能双计 stuck。

challenge 的实际暴露按每个目标及适用 attempt 的 request、执行记录和 submission 判定，独立于 mode 与失败原因。目标尚无 request 时保留空观察并注明 `challenge_target_not_dispatched`；request 已准备但执行边界未被观察到时注明 `challenge_boundary_not_observed`，不能据此捏造注入机会。多目标及 `every_attempt` 逐项核对，已观察到一项不能豁免其它已完成项。

已执行到 parser challenge 但源响应缺失、内容不是字符串或不是 JSON 时，注入器显式记录 `opportunity=false, injected=false`，保留自然失败。真实注入后即使 parser 异常、attempt 尚未完成，也保留已有注入记录和原基础设施失败分类。

已落盘的 submission 必须自身携带适用挑战记录；内存副本不能代替缺失的持久记录。身份、边界、ordinal、类型、冲突副本或 artifact 校验失败仍报证据错误。零暴露的合法失败保留在计划分母中，机会数和注入数为零；基础设施失败仍使对应统计单元无效。

## Experiment 5

- 使用 SiliconFlow 三个冻结 endpoint：`zai-org/GLM-5.2`、`Qwen/Qwen3-14B`、`MiniMaxAI/MiniMax-M2.5`。
- 当前 v4 配置每次协议 attempt 最多发起一次 provider transport attempt；每个 AI unit 允许两次协议重试，因此最多三次 provider calls。
- 记录 provider usage、latency、model identity、pricing version 与 cost estimate。

## 输出模型

每个预注册 root 至少写一行普通 JSONL。单 root 的 provider failure、parse failure、verifier/checker rejection、retry exhaustion 或 no-final 都必须保留固定身份与失败分类；不得通过删除失败样本改变分母。

基础设施错误与实验失败分开记录。Lean checker 的 `environment_error`、`timeout`、`helper_error` 属于 infrastructure-invalid；环境正常时的 proof rejection 属于实验结果。
