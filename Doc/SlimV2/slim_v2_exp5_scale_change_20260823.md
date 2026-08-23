---
status: user_directed
document: slim_v2_exp5_scale_change_20260823
scope: Experiment 5 only; deterministic Full scale reduction and single-repeat compatibility
last_updated: 2026-08-23
---

# Experiment 5 缩容与单 repeat 实施记录

## 1. 直接用户决定与范围

用户决定将 Full Experiment 5 等比例缩为“28 加 3 乘 3”，只运行 `repeat0`，其余采用最小兼容方案。解释为：Factorization hard 28题，加Lean hard三条既有topic（`pure_logic`、`function_set`、`induction`）各3题，共37题；三个既有 SiliconFlow thinking endpoint 保留，DeepSeek V3 删除。

本记录只改变 Experiment 5 的Full inventory、Full计划上限和其repeat汇总规则。不得改变 Experiment 1–4 的case、provider、prompt、协议核心、replay来源或retry语义；Representative Experiment 5为单题×三个 endpoint设施探针。Exp1 Flash V4 只在后续的独立补充比较表中作为历史参考，不进入 Exp5 inventory。

## 2. 冻结选择

选择保持旧Full有序literal的前缀，不重新散列、重排或抽样：

- Factorization：`EXP5_FACTORIZATION_CASE_IDS[:28]`，从`factor_v2_hard_138`至`factor_v2_hard_088`；
- Lean pure logic：`lean_v2_hard_frontier_pure_logic_checker_{12,02,09}`；
- Lean function set：`lean_v2_hard_frontier_function_set_checker_{14,11,10}`；
- Lean induction：`lean_v2_hard_frontier_induction_checker_{06,05,10}`。

该规则的Full数量为：每model 28个Factorization case、每个case 8 planned units，即224 units；三个Lean topic各3 case，按固定 lemma-DAG 合计60 units；每model合计284 planned units。三个model的`repeat0`总计为111 roots、852 planned/actual-provider-attempt upper。

## 3. repeat 与汇总兼容

`repeat_id`唯一合法值为0；condition数由48变为12，模型顺序固定为`ABC`。既有summary/CSV字段保留，避免下游表结构断裂：

- `repeat0_wall_clock_ms`为事实值；`repeat1_wall_clock_ms`和`repeat2_wall_clock_ms`固定为`null + not_applicable_or_unavailable`；
- `repeat_wall_clock_ms`为单元素数组`[repeat0_wall_clock_ms]`；
- median/min/max均等于唯一repeat0值，`model_wall_clock_range_ms=0`；
- `model_wall_clock_sample_stddev_ms=null + insufficient_observations_for_sample_variance`，不把单批次表示为方差证据。

唯一repeat不妨碍按case cluster计算仍有定义的质量、调用覆盖、token/cost统计；但不能把37个root或单repeat wall-clock当成独立repeat样本。

## 4. 全局派生规模

相对旧Exp5的648 roots、4,992 online calls，本决定的Exp5减少到111 roots、852 online calls。其他实验不变，因此Full新总量为：

| 项目 | 新值 |
|---|---:|
| 论文 roots | 7,017 |
| root executions（含106 Exp3 references） | 7,123 |
| Exp5 planned / protocol upper / provider upper | 852 |
| 全部 protocol attempts（含references） | 91,380 |
| 固定 replay attempts | 84,618 |
| 真实 provider-call upper | 6,762 |

这些数值必须由`build_inventory("full")`/`build_plan("full")`重新计算并由profile测试验证；文档常量不能代替运行时求和。

## 5. 与 parsed-unsubmitted 发布修复的边界

同日的Exp5 reducer裁决只处理已终止协议里已有`2xx + raw + parsed`但未到verification边界的事实。它不改变题目缩容、模型、调用上限或provider调用次数；精确规则和四审法定人数记录见`slim_v2_final_summary_lightweight_repair_plan.md`第7.1.1节。三个模型的正式表发布后，`compare-exp5-v4-reference`只读同 case 的 Exp1 Flash V4 committed facts，生成独立 supplemental 表；V4 不发布 latency，旧 Pro cohort也绝不作为 Flash 结果。

## 6. 验证

- 静态profile：精确断言37个case、12 conditions、111 roots、852 Exp5 units/provider upper，以及Full总量`7,017/7,123/6,762`；
- reducer golden：single-repeat wall-clock兼容字段与parsed-unsubmitted null传播；
- CLI `plan --profile full`：实际输出须与第4节一致；
- `run-all`接线：Exp2–4 provider-call仍为0，Exp5不运行coverage tail；
- 真实运行：新的Flash Representative只在零调用preflight、focused verification和旧Pro reducer publication后启动。
