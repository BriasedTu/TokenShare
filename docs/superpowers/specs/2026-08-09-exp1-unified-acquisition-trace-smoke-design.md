# 正式 Full Plan 派生 Representative Smoke 设计

**日期：** 2026-08-09  
**状态：** 用户已批准实施
**替代目标：** 本文完全替代旧“Exp1–5 各 4 Factor + 4 Lean / matrix8”设计。

## 1. 目标与非目标

本轮构建一次“正式 full plan 除规模外的缩小版”实验。系统必须先用当前 catalog、scale profile、selection、provider config 和正式模块构造 Exp1–5 全量计划，并对全量 `6,384` 个 root-runs、`40,520` 个 first-attempt AI units 及其正式 replacement 上界做无 API 验证；随后才从这些正式对象中选择一个 repeat 子集，并用独立 `root_case_filter` 抽取 representative roots。

smoke 不拥有 condition、selection、selection digest、seed、split profile、provider/model identity 或指标公式。扩大到 full roots/repeats 时，只删除 root filter 和 repeat filter；不切换 planner、runner、adapter、`ProtocolEngine`、verifier/checker、fault/recovery、settlement 或 metrics 路径。

本轮不修复 receipt、L1–L4、publication closure、论文 evidence chain 或其他非指标设施门禁；这些门禁按用户授权在 results-first 路径旁路。不得旁路 catalog 真实性、真实 provider acquisition、请求/模型身份、预算 hard ledger、`ProtocolEngine`、Factor verifier、Lean checker、fault-before-checker、终态与指标真实性。

## 2. 已审计的正式计划

当前正式计划由 `build_gate_c_dispatch_plans()` 和各实验模块展开：

| 实验 | 正式 conditions | 正式 roots | 正式 repeats | representative repeat |
|---|---:|---:|---|---|
| Exp1 | 12 | 435 | `0` | `0` |
| Exp2 | 12 | 600 | `0,1` | `0` |
| Exp3 | 162 | 3,726 | `0,1` | `0` |
| Exp4 | 90 | 975 | `0,1,2` | `0` |
| Exp5 | 48 | 648 | `0,1,2` | `0` |

当前计划在“每个被执行 condition 至少一个 root”的机械下界下会派生 `12 + 6 + 81 + 30 + 16 = 145` 个 root-runs。`145` 是当前正式 axes 的派生观测，不是 production 常量；condition 集合或 coverage 规则改变时，root 数必须随 full plan 重新计算。

覆盖含义如下：

- Exp1：3 个 Factor difficulty 和 3×3 Lean difficulty/topic cells 全部执行。
- Exp2：只执行 Factorization hard；repeat 0 的 `1,3,7,10,30,50` 六个 worker conditions 全部执行。不得存在 Lean seam。
- Exp3：repeat 0 的 81 个正式 conditions 全部执行，覆盖 Factor/Lean 的五类 rate fault、各自正式 rate，以及 dead worker `1/3` × kill progress `25/50/75%`。
- Exp4：repeat 0 的 30 个正式 conditions 全部执行，覆盖两域、三 difficulty、`FULL` 与四个正式 ablation mode。
- Exp5：repeat 0 的 16 个正式 conditions 全部执行，覆盖四个 SiliconFlow endpoint、Factor hard 与三个 Lean hard topic slices。

## 3. 方案选择

采用方案 A：从 full plan 派生 condition 子集和 root filter。

1. 先构造五个完整正式 dispatch plans、budgets 和 prepared-request inventories。
2. repeat filter 只保留上述正式 repeat IDs；保留原 `PaperExperimentCondition` 对象和原 `FrozenConditionSelectionBinding`，不 clone、不 `replace()` condition。
3. 对每个保留 condition，从原 `FrozenCaseSelection.ordered_case_ids` 中按正式可执行性规则选最少 roots，形成 `condition_id -> case_ids` 的 `root_case_filter`。
4. 交给现有 `paper_formal_runner._normalize_root_case_filter()` 再次验证 key 完整性和 case membership。

不采用方案 B（继续维护 matrix8 profiles/clones），因为它已被证实会引入 Exp2 Lean、非 hard selection、case-based seed 和 condition suffix。也不采用方案 C（保留所有 repeats、每 condition 一题），因为用户明确允许 repeat 子集，它会把当前 smoke 从约 145 roots 扩到 324 roots，却不增加新的 condition 类别或 runner 分支。

## 4. Full-plan snapshot 与无 API 验证

新增的 full-plan preflight 是正式计划验证器，不是 smoke validator。它必须在任何 provider dispatch 前完成以下工作：

1. 载入并验证 Factorization v2、Lean lemma graph、Lean 3×3 readiness、suite scale v1、Exp1–4 DeepSeek v3 config、Exp5 cohort/config/selection v3/v4 authorities。
2. 构造 Exp1–5 全部 324 个正式 conditions 与原 FrozenCaseSelections，验证 condition ID/digest、selection ID/digest、ordered case binding、repeat、seed、worker、fault/rate/death、ablation 和 endpoint axes。
3. 枚举所有 6,384 个正式 root-runs，确认 case 存在且全 catalog 唯一、每个 root 只通过其原 selection 进入 condition。
4. 使用原 runtime adapter 的 `plan_units()` 与 request preparation 路径冻结全计划 prepared requests；校验 plugin/prompt/provider/model/reasoning/request controls、body digest、inference digest、sample slot 和 replacement slot。Exp5 的 4,992 个 first-attempt requests 也必须在这里离线冻结，不能等到付费 dispatch 才发现漂移。
5. 使用 `plan_paper_suite(..., plan_only=True)` 计算正式 roots/units/attempt/token/cost/disk ceilings，并对 Exp4 增加与 Exp3 对等的 plan-time complete-matrix 检查。
6. 证明每个 condition 可由原 dispatcher/formal runner 调度，且 representative coverage map 覆盖所有要求的正式 axes。

preflight 输出必须包含 full-plan digest、每实验 condition/root/unit 计数、selection/condition digest inventory、prepared-request inventory digest、预算摘要和 coverage 摘要。它不能读取 secret、建立协议任务、调用 provider 或伪造 real-transport capability。

## 5. Representative coverage 派生

coverage 派生器只返回：

- 从 full dispatch plans 过滤出的正式 condition references；
- 原 binding references；
- `root_case_filter`；
- 解释 coverage 的只读 manifest/digest。

它不得返回或持久化新的 condition、selection、seed 或 split identity。选择规则按正式 selection 顺序稳定；如果某个 fault/death branch 要实际触达特定 unit，派生器可在同一原 selection 中增加或改选 root，直到该正式 hook 可调度。root 数由这一覆盖判定产生，不能由 `8`、`32`、`145` 等常量驱动。

指标分母是实际冻结在 representative `root_case_filter` 中的全部 root-runs。每个 root 无论正确、错误、provider failure、parse/checker/verifier rejection、重试用尽或未得到最终结果都保留在 correctness/completion 分母；`False` 与 `None` 分开，missing 记录 `None + missing_count + reason`。

## 6. 统一 acquisition 与 response bank

DeepSeek acquisition plan 从已验证的 full prepared-request snapshot 中抽取 Exp1–4 representative roots 所需的 exact semantic slots：

- base sample slots 来自正式 repeat/seed/split/request identity；
- Exp3/Exp4 replacement slots由正式 fault target、worker-death 和 retry policy计算；
- 能自然共享的 request 以完整 body/inference/provider identity 去重；
- 不能自然共享的正式请求作为 exact supplemental entry 加入，不修改 condition、seed、split 或 hash 规则。

acquisition 最多 10 路并发，继续使用原子 budget reservation、hard ledger、Windows artifact write lock、immutable child bank 和 ambiguous-dispatch reconcile。真实错误回答、parse failure、provider failure 和 usage missing 都必须 terminal 入库，禁止按 correctness 筛选或重采到答对为止。

Exp1–4 随后都从 bank 执行 `real_model_trace_protocol_run`；source actual spend 只在 acquisition ledger 计一次，四个 trace suites 的 current provider calls 必须为 0。Exp2–4 仍完整执行原 `ProtocolEngine`、Factor verifier、Lean checker、fault/recovery、merge、settlement 和 metrics。Exp4 `FULL` 是 Exp4 自己的正式执行，不复用 Exp1 终态。

Exp5 不消费 DeepSeek bank。它使用同一个 full Exp5 plan 的 repeat-0 condition references 和 root filter，对四个正式 SiliconFlow endpoint 当次真实调用；`max_retries=0` 与每 unit 单 attempt 保持不变。

## 7. 执行与终态

正式运行顺序固定为：

1. 在 fresh output root 重建 full-plan snapshot、budget 和 acquisition bundle，并复核所有 digests。
2. 创建 30 分钟恢复轮询；确认没有第二份 run。
3. 执行 DeepSeek acquisition；每个 slot 达到可审计 terminal 后才能被消费。
4. 执行 Exp1–4 trace suites；每 suite current provider calls=0。
5. 在 `SILICONFLOW_API_KEY` 或 gitignored local config 可用时执行 Exp5 四 endpoint online suite。
6. 汇总和审计所有 representative roots 的 correctness、completion、provider/source latency、prompt/completion/total tokens、cost、provider identity 与 missing counts。

suite 可以因模型回答错误而 `completed_with_failures`，但不得 `blocked` 或 `incomplete`。provider 中断后先检查 ledger、dispatch intent、bank terminal 和 persisted artifacts；ambiguous slot 不盲目 retry。

## 8. TDD 与验收

TDD 顺序：

1. RED：证明旧 matrix8 只覆盖部分正式 conditions、Exp2 含 Lean、seed/selection 被改写、固定 166/8/32。
2. GREEN：full-plan snapshot 可无 API 构造并验证全部 roots、budgets 和 prepared requests。
3. RED/GREEN：repeat/root coverage 只引用正式 objects，动态覆盖 Exp1–5 全部要求 axes。
4. RED/GREEN：统一 acquisition 从 full snapshot 抽取 exact requests，错误 terminal 保留，动态计数且 max concurrency `1..10`。
5. RED/GREEN：capturing bank representative E2E 实际进入两域 `ProtocolEngine`、Factor verifier、Lean checker、Exp3 全 fault/rate/death、Exp4 全 modes、Exp5 全 endpoint runner；Exp1–4 current provider calls=0。
6. 独立 spec review 与 code-quality review 必须 `0 Critical / 0 Important`。
7. 主 Agent 最终执行唯一一份真实 representative smoke，并以 terminal artifacts 和指标审计作为验收；Fast/Full 不是本轮完成标准。

## 9. 已知启动基线噪声

本轮开工 `init.ps1` 得到 `527 passed / 1 failed / 1 skipped`；唯一失败是历史 `paper-l1-components` 清单没有收录 `tests/executors/test_response_bank.py` 当前 nodeids。该问题由既有 `7df83214` 后的非指标 L1 profile 漂移造成，与 provider、协议或本轮 smoke 无关。按用户范围不为旧 evidence gate 扩大修复；定向功能验证和最终真实 representative smoke 才是本轮证据。
