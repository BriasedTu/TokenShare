# Metrics contract

公开实验指标遵循冻结口径：固定预注册分母、失败不删除、基础设施无效与科学结果分离、所有成本使用静态价格表离线计算。

## 固定分母

每个报告 cell 必须保留：

- `preregistered_root_count`
- `scientifically_valid_root_count`
- `infrastructure_invalid_root_count`

且满足：

```text
scientifically_valid_root_count + infrastructure_invalid_root_count
  = preregistered_root_count
```

只要一个 cell 中存在 infrastructure-invalid root，依赖科学有效性的 rate/effect 写为 `null`，并记录 `missing_reason=infrastructure_invalid_root_present`。已经完整持久化的精确事实，如 inventory、attempt/fault/replacement 计数、tokens、cost、wall-clock，仍应继续报告。

## 顶层 root failure taxonomy

顶层 `failure_kind` 只允许三类：

- `no_final`
- `incorrect_final`
- `infrastructure_invalid`

细分原因写入 nullable `failure_origin`。不得用 `final_result_present`、`verified_correct` 或 mode 配置反推出新的顶层分类。

## Experiment 2–4 trace 复用

Experiment 2、3、4 已退出在线 provider 检查，只复用 Experiment 1 普通 per-unit traces。

- 来源键：`case_id × source_repeat_id=0 × planned_ai_unit_id`
- 下游实验自己的 `repeat_id` 不进入来源键。
- 当前 ordinal 存在时精确读取；不存在时回退到同一 trace 的最后一个自然 attempt。
- 运行期间 provider calls 必须为 0。
- Factorization 对比 `candidate_start/candidate_end`；Lean 对比 `lemma_node_id/dependency_path`。

## Timing

- roots 在 runner 层串行。
- `root_start_at_ms` 是该 root 协议生命周期开始、任何 AI 子任务调度之前的时间。
- `root_terminal_at_ms` 是 root 完成或失败终止之后的时间。
- `runtime_wall_clock_ms = root_terminal_at_ms - root_start_at_ms`。
- Experiment 1 coverage tail 发生在当前 root terminal 之后、下一个 root start 之前，tail 时间单独报告。

## Token 与 cost

所有成本由 reducer/projector 使用普通静态价格表计算，不构成预算、审批或运行 gate。

| Experiment | pricing version | provider/model |
|---|---|---|
| Experiment 1 | `slim_v2.pricing.2026-08-23` | DeepSeek `deepseek-v4-flash` |
| Experiment 5 | `slim_v2.pricing.2026-08-20` | SiliconFlow GLM/Qwen/MiniMax endpoints |

`reasoning_tokens` 是 `completion_tokens` 的子集，不得在 completion cost 外重复相加。任一 attempt 缺失必要 usage 字段时，该 attempt 的 cost 为 `null`，并记录 `usage_status/missing_reason`；不得猜测。

## Identity expectations

Task E corpus verifier 从公开 catalog/config 重新计算完整 identity 集合：

| 集合 | count | canonical byte length | SHA-256 |
|---|---:|---:|---|
| full root identities | 7,017 | 1,448,798 | `ec5ca012faffc148be5eff65d43394b2d7861c666468e59841c716c88dea7c07` |
| full Experiment 3 references | 106 | 22,055 | `b537bd614ac9c86f2f864afb0e6a7f3c1fe22e38d772784c1fc8c0c914f43cdb` |

这些 identity 集合是公开 corpus/config 的静态核验结果，不需要 provider 调用。
