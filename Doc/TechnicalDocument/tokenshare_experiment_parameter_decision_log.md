# TokenShare 实验参数决策台账

日期：2026-07-24

状态：持续维护。用于记录用户在实验运行前确认的参数修改、修改理由、影响规模和同步状态。

## 1. 文档定位

本台账解决“实验参数会继续讨论和修改，已确认决定不能只留在聊天记录里”的问题。

- `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md` 仍是实验执行和论文口径的唯一权威。
- 本台账记录每一项用户决定的来源、状态和影响；状态为 `user_confirmed` 的决定必须同步到权威实验设计。
- runner、预算、测试和报告只有在对应状态变为 `implemented` / `verified` 后，才可声称已经采用新参数。
- 如果台账与权威实验设计或代码暂时不一致，必须按状态列说明差异，不能静默选择其中一套参数开跑。
- 后续用户要求修改实验参数时，继续在本文追加新编号，不覆盖旧记录；被替代的决定标记为 `superseded` 并指向新编号。

## 2. 状态定义

| 状态 | 含义 |
|:---|:---|
| `discussing` | 尚在讨论，不得进入正式计划或运行。 |
| `user_confirmed` | 用户已明确确认，应同步权威设计，但不表示代码已经修改。 |
| `design_synced` | 唯一权威实验设计已更新。 |
| `implemented` | runner、预算、测试或报告代码已采用该参数。 |
| `verified` | 定向验证实际通过，并已记录证据。 |
| `superseded` | 已被后续决定替代，不得继续使用。 |

## 3. 当前有效决定

### EPD-001：Experiment 1 两个领域均只运行一次

| 字段 | 决定 |
|:---|:---|
| 决定日期 | 2026-07-24 |
| 用户决定 | “只把 Exp1 两个领域都改成单次。” |
| 适用实验 | Experiment 1：真实 AI 跨领域可行性与难度 |
| Factorization | 冻结 500 道唯一 root，每道运行 1 次。 |
| Lean | 冻结 135 道唯一 root，每道运行 1 次。 |
| worker count | 保持 10，不修改。 |
| 模型和请求参数 | 本决定当时不修改 GLM/nonthinking；该描述已由 EPD-008/009 覆盖。当前 Experiment 1–4 使用官方 DeepSeek v4-pro、thinking high、`600/300000`。 |
| fault / ablation | 保持 `none / FULL`，不修改。 |
| pilot | 仍只作单次、非主表运行；本决定不把 pilot 结果升级为正式结果。 |
| 不受影响实验 | Experiment 2 的性能重复、Experiment 3 的故障/死亡重复、Experiment 4 的消融重复、Experiment 5 的端点比较重复均暂不修改。 |
| 状态 | `verified`：Task A 已同步单次 repeat/seed、runner、预算、CLI 和测试；formal plan-only 为 12 conditions / 635 roots / 2,900 首轮 AI units。 |

#### 修改理由

Factorization 的最终因子由 deterministic verifier 判断，Lean proof 由固定 Lean checker 判断；重复运行不会提高正确性判定本身。Experiment 1 的主要问题是“两个领域和不同难度能否完成”，500 道 Factorization 与 135 道 Lean 已提供大量不同输入。取消同题重复可以显著减少时间、token 和费用。

本决定接受以下统计边界：

- Experiment 1 不再声称同一道题的重复运行稳定性。
- 不再估计同一道题的 within-case 成功率或 token/latency 方差。
- 仍可按不同 case 汇总 completion rate、accepted validity、token、cost 和 failure breakdown。
- Exp2–5 中以延迟波动、故障随机性、消融或模型端点比较为研究对象的重复暂时保留。

#### 规模影响

| 指标 | 旧设计 | EPD-001 |
|:---|---:|---:|
| Factorization 唯一 roots | 500 | 500 |
| Lean 唯一 roots | 135 | 135 |
| Experiment 1 root-runs | 1,905 | 635 |
| Factorization 首轮 AI units / provider calls 基数 | 6,990 | 2,330 |
| Lean 首轮 AI units / provider calls 基数 | 1,710 | 570 |
| Experiment 1 首轮 AI units / provider calls 基数 | 8,700 | 2,900 |

“首轮 provider calls 基数”不包含协议 replacement attempt；实际请求数以 provider usage / attempt evidence 为准。

#### 待同步清单

- [x] 唯一权威实验设计中的 Experiment 1 repeat 和规模。
- [x] `src/tokenshare/experiments/paper_exp1.py` 的 condition expansion、seed family 和 root-run 常量。
- [x] Experiment 1、dispatcher、budget 和正式 CLI 相关测试期望。
- [x] 重新执行 `--plan-only`，生成新的 catalog/condition/budget identity 和 digest。
- [x] 定向测试与 Fast；本次自主执行 Prompt 明确禁止 Full，故没有把 Full 写成验收证据。
- [x] `progress.md`、`feature_list.json` 和 code map 的实现证据。

### EPD-002：预算估计与实际 token 报告必须分离

| 字段 | 决定 |
|:---|:---|
| 记录日期 | 2026-07-24 |
| 性质 | 对现有口径的确认，不新增实验机制。 |
| 运行前预算 | `run_budget.json` 根据题目、拆分、condition、repeat、provider attempt 和单次 token/cost 上界生成，用于资源准备、审批或硬限额。 |
| 实际报告 | `prompt_tokens`、`completion_tokens`、`total_tokens` 必须来自真实 provider response usage evidence，再汇总到 task/condition/suite 报告。 |
| 禁止事项 | 不得把预算 token 或预算成本写成实验实际消耗。 |
| 关系 | 两者不是同一个数，但共享同一冻结计划；若启用硬限额，实际累计 usage 会决定是否停止启动新任务。 |
| 状态 | `verified`：预算 identity 已覆盖 headline/supporting roots、首轮 AI units 和逐 condition replacement policy/reserve；实际 usage 仍只读 provider evidence。 |

#### 已知待处理项

2026-07-25 Task 11 当时关闭原待处理项：Exp1/2/5 的协议 replacement reserve 为 0；Exp3 按 rate-fault `max_retries=2`、worker-death `dead_count+1` 计算；Exp4 五模式按 `max_retries=1` 且 `NO_REQUEUE` reserve 为 0。当时 P0-core/P0-full provider-attempt 上界为 `715,558/730,210`；该 dedicated-baseline 算术已由 EPD-012 取代。当前 P0-core 上界为 `640,550`；只叠加历史三端点 Exp5 v2 时 P0-full 为 `655,202`。这些仍是运行前上界，不得写成实际 token、成本或 provider calls。

### EPD-003：Experiment 2 改为 hard Factorization 单域扩展性实验

| 字段 | 决定 |
|:---|:---|
| 决定日期 | 2026-07-24 |
| 用户原意 | Lean 固定 DAG 基本串行，不再用 Experiment 2 测其 worker 扩展性；Experiment 2 只关注 hard Factorization。 |
| 适用实验 | Experiment 2：真实 AI worker 扩展性 |
| domain | 仅 `factorization`；`lean_proof` 退出 Experiment 2。 |
| Factorization 来源 | 使用正式 Factorization catalog 的全部 166 道 `hard` 题，不做抽样。冻结分布为 `early=53`、`middle=53`、`late=53`、`no_factor=7`。 |
| 每题拆分 | 由 Factorization 插件确定性切成 20 个连续候选因子区间。该参数只用于 Experiment 2，不修改 Experiment 1 的 `2/4/8` 拆分。 |
| worker levels | `1、3、7、10、30、50`。 |
| repeats | 每个 worker level 对完全相同的冻结题库运行 2 遍。 |
| 上限对照 | 每题只有 20 个可同时运行的 range child；因此 worker 30 和 50 的实际单题并发上限都为 20，用于观察扩展性平台期。 |
| factor witness 早停 | verifier 接受一个 factor witness 后，系统停止调度尚未发出的 sibling；已经发给 provider 的同批在途请求不追溯取消。Task 4 已按此语义实现并验证。 |
| 题库选择原则 | 全量使用 166 道 hard 题，保留自然存在的 `early`、`middle`、`late` 和 `no_factor` 类型，不得只挑需要跑满全部区间、对扩展曲线有利的题，也不得按运行结果事后删题。 |
| 题库数量 | 166 道。此前讨论的“只使用 7 道 no-factor 题”未被采纳。 |
| 正式汇总 | 两遍原始值全部保留；condition-level 报告 min/max 和相对差，不把 `n=2` 的 IQR 当稳定性证据；逐 root 在相同 `case_id × repeat_id` 上做 worker=1 paired speedup，并按 factor position 分层。 |
| 状态 | `verified`：hard-only 12-condition matrix、20-way plugin profile、自然早停、真实 worker facts、paired metrics、预算/plan-only/CLI 和测试均已实现并验证。 |

#### 当前规模公式

正式题数 `H=166`：

- Experiment 2 root-runs：`166 × 6 worker levels × 2 repeats = 1,992`。
- 计划 AI units：`166 × 20 units × 6 worker levels × 2 repeats = 39,840`。
- 首轮 provider calls 不能直接断言等于 `39,840`：factor witness 可能让系统停止调度尚未发出的 range child；已经组成并发批次并发出的请求不会被追溯取消。实际请求数必须来自 attempt/provider usage evidence。
- replacement/requeue 会在首轮请求之外增加 provider calls。
- 早停会形成真实的 latency/cost trade-off：更大的 worker batch 可能更快得到 witness，也可能在 witness 返回前发出更多并行请求。因此报告必须同时给出 wall-clock、实际 provider calls、已执行 AI units、因早停未调度的 units 和 worker 利用率，不能只报告 speedup。

#### 范围边界

- 一般合数的递归完整分解不是 EPD-003 的实现前置条件。Exp2 的 159 道 factor-bearing hard roots 是预注册 semiprime；现有插件验证 factor 与 prime cofactor 后即可完成。不得把这条实验边界误写成插件已经支持任意 composite cofactor 的递归解析。
- 7 道 no-factor roots 必须等待全部 20 个 range children；159 道 semiprime roots 保留自然早停。二者共同进入同一正式题库。

#### 待同步清单

- [x] 用户确认全部 166 道 hard 题；一般合数的递归完整分解不阻塞 Exp2 半素数早停口径。
- [x] 唯一权威实验设计。
- [x] Exp2 condition expansion、selection、20-way split projection、协议 child 上限和报告。
- [x] budget / plan-only identity / digest。
- [x] tests。
- [x] progress / feature / code map 的实现证据。

#### 2026-07-24 实际行为与指标审计

结论：真实 thread worker、系统 scheduler/lease/attempt/provider/verifier/canonical 执行底座已经存在，但 EPD-003 的正式矩阵、20-way split、自然早停和论文指标尚未接通，因此 Exp2 当前不可作为正式扩展性实验运行。

- 当前代码仍是 Factorization+Lean、三档难度、worker=`1/3/10/30`、5 repeats、10,300 roots；没有实现 166 hard-only / `1/3/7/10/30/50` / 2 repeats。
- selection 复用 catalog 原有 2/4/8 split，没有 Factorization 插件拥有的 `factorization.exp2_contiguous_20way.v1`。
- Factorization 插件已有 verified witness OR readiness，但 coordinator 会在进入 readiness 检查前继续发完 Ready siblings；“未调度 sibling 早停”尚未实现。
- worker backend 的真实 execution facts 没进入 normal Exp2 formal evidence；adapter 又给协议 event 注入固定 `NOW`，所以当前 formal wall-clock、critical path、peak concurrency 和 utilization 不能用于论文。
- 正式 CLI 使用简化 `paper_formal_metrics._exp2_rows()`；更完整的 `summarize_exp2_scalability()` 没接入生产 evidence/CSV。planned/executed/unscheduled/in-flight、paired per-root speedup、两遍 min/max/relative difference 均未形成正式输出。

当前 Exp2 要求见唯一权威实验设计的“Experiment 2”节；2026-07-24 的文件级修复过程已归档到 `Doc/archive/design-history/2026-07-24-feat-011-exp2-exp5-experiment-facility-completion-implementation-plan.md`，只供 provenance。

2026-07-25 Task 3–4 已覆盖上述历史审计：正式矩阵为 1,992 roots，20-way 无早停上界为 39,840 首轮 AI units；coordinator 在每批后检查 plugin readiness，持久化 planned/dispatched/completed/unscheduled/in-flight 与 observed peak concurrency；正式 scalability CSV 使用真实 runtime timing 和逐 root worker-1 paired speedup。

### EPD-004：Experiment 3 所有 condition 只重复两遍

| 字段 | 决定 |
|:---|:---|
| 决定日期 | 2026-07-24 |
| 用户原意 | 认可 Experiment 3 的五类 rate-fault 和 worker-death 设计，不做其他大改；只把每档重复次数从 3 遍改为 2 遍。 |
| 适用实验 | Experiment 3：五类 rate-fault 条件和单独的 worker-death 条件。 |
| 原参数 | 每个 condition 重复 3 遍。 |
| 新参数 | 每个 condition 重复 2 遍。原 same-repeat no-fault baseline 配对要求已由 EPD-012 的 Exp1 shared reference 取代。 |
| 不变参数 | 五类 fault、Factorization/Lean 题库、`worker_count=10`、`dead_worker_count={1,3}`、kill progress `25%/50%/75%`、注入点、恢复与报告字段均不变；fault rates、模型和 baseline 口径以后续 EPD-009/EPD-012 为准。 |
| 正式汇总 | 保存两遍原始结果，报告 min/max 和相对差；不把 `n=2` 的 IQR 当作稳定性证据。 |
| 状态 | `verified`（仅“两遍”决定继续有效）；0% conditions、same-repeat/dedicated baseline 和下列旧规模已由 EPD-012 `superseded`。 |

#### 规模影响

> 下表记录 EPD-004 当时仅修改 repeats 的历史差异，不是当前运行分母；当前唯一有效总量见 EPD-012。

| 指标 | 原设计（3 遍） | EPD-004（2 遍） |
|:---|---:|---:|
| Experiment 3 rate-fault root-runs | 52,680 | 35,120 |
| Experiment 3 worker-death root-runs | 9,054 | 6,036 |
| Experiment 3 合计 root-runs | 61,734 | 41,156 |
| P0-core root-runs | 73,631 | 53,053 |
| P0-full root-runs | 78,266 | 57,688 |

root-run 数量不等于 provider calls。rate-fault replacement 和 worker-death recovery 会产生额外真实 provider attempts；必须由新的 plan-only 按两遍矩阵重新计算预算，实际 token/cost 继续只来自 provider usage evidence。

worker-death matched comparison 另需真实执行、并按 `domain × task_slice × repeat` 去重的 dedicated no-kill baselines：Factorization `(167+167+166)×2=1,000` root-runs，Lean `3×2=6`，共 1,006 supporting root-runs。它们进入 plan-only/成本，但不进入 41,156 个 fault-condition headline 分母；Exp3 实际调度总数为 42,162。

#### 待同步清单

- [x] 唯一权威实验设计和 P0 总规模。
- [x] `paper_exp3_fault_recovery.py` 的 repeat 常量、condition expansion 和 root-run 常量。
- [x] budget / plan-only identity / digest。
- [x] tests。
- [x] progress / feature / code map 的实现证据。

#### 2026-07-24 实际行为与指标审计

结论：五类 rate-fault 确实在真实 raw/provenance/usage 保存后注入，协议 replacement 确实由 coordinator/engine 产生；worker death 也确实终止真实 OS process，并产生 lease expiry/reassignment/replacement。以下缺口使 Exp3 当前仍不可作为正式论文实验运行：

- 代码仍是 3 repeats / 61,734 roots，没有同步 EPD-004 的 2 repeats / 41,156 roots。
- false-positive/negative 实际都在正式 domain parser 之前的 raw hook 注入，只是 hook 自行 `json.loads` 后把 record 标成“parsed 后、verification 前”；必须新增真实 parser→verifier 边界 hook，不能让记录的 stage 名称代替实际 stage。
- false-negative 从全部 planned units 预选 target；原输出没有 factor/proof candidate 时 mutation 会抛错，尚无 applicability/reserve-target 机制。
- fault record 在注入时硬写 `canonical_pollution=false`，runtime record 又给所有 fault 写 `requires_replacement=true`；正式 detection/false-accept/recovery 分母不是从真实运行结果派生。
- worker-death 的 25/50/75% 主要按 planned unit 序号挑 target；backend 不会等待真实完成比例达到阈值再 kill，`progress_before_kill` 还直接复制目标值。
- dedicated worker-death no-fault baseline 只有 manifest，没有实际执行；formal runner 把故障 run 自己的 time/token/cost 复制成 baseline fallback，overhead 会被错误压到 0。
- 正式 recovery 指标查找不存在的 `REPLACEMENT_ACCEPTED` event name；更完整的 `summarize_exp3()` 没接入 formal CSV。真实 replacement 即使存在也可能被漏计，recovery latency、wasted tokens、completeness 和两遍汇总也未完整输出。

当前 Exp3 要求见唯一权威实验设计的“Experiment 3”节；2026-07-24 的文件级修复过程已归档到 `Doc/archive/design-history/2026-07-24-feat-011-exp2-exp5-experiment-facility-completion-implementation-plan.md`，只供 provenance。

2026-07-25 Task 5–6 当时覆盖上述历史审计：false-positive/false-negative 位于真实 parsed-candidate→submission/verifier 边界；无候选 false-negative 为 not-applicable 并按冻结 reserve 晋位；worker death 由实际 completed/planned 进度触发并产生不同 PID/attempt/lease/recovery 事实。该版本曾把 1,006 个 dedicated no-kill baseline 纳入计划；2026-07-29 起已由 EPD-012 删除，历史 evidence 不重写。

### EPD-005：Experiment 4 删除 slot binding integrity 消融

| 字段 | 决定 |
|:---|:---|
| 决定日期 | 2026-07-24 |
| 用户原意 | “最后一种绑定检查可以删去。” |
| 适用实验 | Experiment 4：真实 AI 协议消融。 |
| 原矩阵 | `FULL + NO_VERIFICATION + NO_PARSER_POLICY + NO_REQUEUE + NO_MERGE_GATE + NO_SLOT_INTEGRITY`，共 6 个模式。 |
| 新矩阵 | `FULL + NO_VERIFICATION + NO_PARSER_POLICY + NO_REQUEUE + NO_MERGE_GATE`，共 5 个模式。 |
| 删除项 | `NO_SLOT_INTEGRITY` 及其 `slot_mismatch` 专项论文指标。 |
| repeats | 仍为每个 mode 3 遍，本决定不修改重复次数。 |
| 不变参数 | Factorization 全部 500 roots、Lean 固定 15 roots、`worker_count=10`、GLM-5.2 fixed entry、真实 AI API 和独立 output root 均不变。 |
| requeue 对照落地值 | 五个 mode 的 `ProtocolConfig.max_retries=1`；`NO_REQUEUE` 只关闭 `replacement_attempts_allowed`，使 FULL 与消融之间只有一个变量。 |
| 状态 | `verified`：五模式 90 conditions / 7,725 roots、统一 retry control、四类真实专项行为、evidence-derived rates、预算和测试均已实现并验证。 |

#### 规模影响

| 指标 | 原 6-mode 设计 | EPD-005 |
|:---|---:|---:|
| formal condition records | 108 | 90 |
| Factorization root-runs | 9,000 | 7,500 |
| Lean root-runs | 270 | 225 |
| Experiment 4 合计 root-runs | 9,270 | 7,725 |
| P0-core root-runs | 53,053 | 51,508 |
| P0-full root-runs | 57,688 | 56,143 |

root-run 数量仍不等于 provider calls；正式预算必须在 runner 采用 5-mode 矩阵后重新执行 plan-only。

#### 2026-07-24 实际输出审计

本次不是只核对设计文档，而是沿正式代码路径检查了 `paper_formal_runner.py`、`paper_formal_callbacks.py`、`paper_formal_metrics.py`、`paper_exp4_ablation_runner.py`、两类 paper adapter 和 `local_runtime/coordinator.py`。结论是：通用的 completion、accepted validity、attempt、wall-clock、provider latency/error、真实 token 和 cost 已有持久化与 CSV 输出；但其余四个 mode 的主要专项数据目前并非全部正确接线。

- `NO_VERIFICATION` 的 verifier bypass 和最终 deterministic audit 确实执行，但 wrong-canonical 字段没有从真实 canonical event 生产，现有 formal CSV 会漏计。
- `NO_PARSER_POLICY` 有逐 task 的真实 raw exposure evidence，但 formal CSV 目前按 mode 常量计数，不是按 observed exposure/acceptance 计数。
- `NO_REQUEUE` 有 policy/hook，但 Exp4 FULL 当前也是 `max_retries=0`，没有可比较的 replacement baseline；现有 stuck count 只是 mode 标记。
- `NO_MERGE_GATE` 有 bypass observation，但 readiness 不满足时 coordinator 在真正调用 plugin merge 前停止；现有 premature count 也是 mode 标记，不是实际 merge attempt。
- 正式 `paper_table_ablation.csv` 当前没有 `error_escape_rate/error_escape_applicability` 和四种专项 rate；声明这些字段的另一个 summarizer 没有接入 formal CSV。

因此，EPD-005 只代表参数设计已经同步，不代表 Exp4 已可正式运行。修复后的专项数字必须从 runtime/canonical/recovery/merge evidence 派生，不能用 ablation mode、预期值或 synthetic flag 填表。

2026-07-25 Task 7–8 已覆盖上述历史结论：`NO_SLOT_INTEGRITY` 已退出正式矩阵；FULL 与五模式使用同一 `max_retries=1` 控制；NO_VERIFICATION、NO_PARSER_POLICY、NO_REQUEUE、NO_MERGE_GATE 均保存实际 runtime observation；正式表的专项 count/rate、applicability 和三遍 paired aggregate 只从 evidence 派生。

#### 待同步清单

- [x] 唯一权威实验设计、正式模式列表和 P0 总规模。
- [x] 从 Exp4 formal matrix、枚举/controls、adapter、callback 和校验中删除 `NO_SLOT_INTEGRITY`；formal condition 数从 108 改为 90，root-run 常量从 9,270 改为 7,725。
- [x] `NO_VERIFICATION` 从真实 canonical event + deterministic audit 生成 wrong-canonical evidence，不能按 mode 推断。
- [x] `NO_PARSER_POLICY` 记录实际 raw exposure、candidate/canonical acceptance 和 validity，不能按 mode 推断。
- [x] `NO_REQUEUE` 给五个 mode 配置相同的 `max_retries=1`，仅在消融 mode 关闭 replacement；由真实 rejected/expired→无 replacement→root stuck 事实计数。
- [x] `NO_MERGE_GATE` 在 local runtime ablation hook 中真正执行并记录 plugin-level premature/incomplete merge attempt；不得以 bypass observation 后 `break` 代替。
- [x] 把四类专项 count/rate、error escape applicability、逐 task refs 和 3-repeat paired 汇总接入 `paper_table_ablation.csv`。
- [x] 收敛未接线的 `summarize_exp4_ablation()` 与已接线但简化的 `paper_formal_metrics._exp4_rows()`，只保留一条生产指标路径。
- [x] 更新 budget / plan-only identity / digest。
- [x] 更新 tests、progress、feature、code map 并执行定向验证。

### EPD-006：Experiment 5 独立使用 Experiment 1 的全部 hard 题

覆盖关系：本决定只剩“Exp5 使用 hard-only 难度层”这一约束仍有效；“全部 211 个 hard roots”已由 EPD-010 的分层确定性半量 107 roots 覆盖，“三个 endpoint / 36 conditions”等 cohort 形状已由 EPD-011 覆盖，均不是当前运行参数。

| 字段 | 决定 |
|:---|:---|
| 决定日期 | 2026-07-24 |
| 用户原意 | Experiment 5 不再依赖 Experiment 2 的旧 selection，改为从 Experiment 1 正式题库选择 hard 题。 |
| 适用实验 | Experiment 5：三模型 model-provider endpoint comparison。 |
| Factorization | 使用 Experiment 1 正式 Factorization catalog 的全部 166 道 hard roots；保持 Exp1 冻结的 8-way split，不继承 Exp2 的 20-way profile。 |
| Lean | 使用 Experiment 1 正式 Lean catalog 的全部 45 道 hard roots，即 `hard_frontier × pure_logic/function_set/induction` 每格 15 道；继续使用各题预注册固定 lemma-DAG。 |
| endpoint / repeats | 三个预注册 endpoint，每个 condition 重复 3 次，不修改。 |
| condition 组织 | 每个 endpoint/repeat 包含 1 个 Factorization hard condition 和 3 个 Lean hard topic-family conditions，共 `3 × 3 × 4 = 36` conditions。 |
| selection 所有权 | 直接绑定 Experiment 1 formal catalog execution view、case IDs/order 和 digest；删除对 `_shared_exp2_slice()` 的依赖。Exp2 以后修改 domain/split/worker/selection 不得改变 Exp5。 |
| 状态 | `superseded`（仅 hard-only 难度约束继续有效）：题量由 EPD-010、模型与 condition 形状由 EPD-011 接管；本节的 v2 selection identity 与规模只供历史 replay。 |

#### 修改理由

Experiment 5 的目的是比较三个 endpoint 在同一批高难任务上的完成率、有效性、token、成本和延迟。使用全部 hard roots 可以：

- 避免从 hard 题中再做人为择优抽样；
- 比 easy/medium 更容易观察模型能力差异；
- 保留 Factorization 与 Lean 两个领域，以及 Lean 三个 topic family；
- 解除旧 Exp2 selection 与 Exp5 的不合理耦合；
- 将旧 4,635 root-runs 降到 1,899，同时仍有 211 个不同 hard roots。

#### 规模与预算影响

| 指标 | 旧 Exp5 | EPD-006 |
|:---|---:|---:|
| Factorization unique roots / endpoint-repeat | 500 | 166 |
| Lean unique roots / endpoint-repeat | 15 | 45 |
| unique roots / endpoint-repeat | 515 | 211 |
| formal conditions | 54 | 36 |
| Experiment 5 root-runs | 4,635 | 1,899 |
| Experiment 5 首轮 planned AI units | 旧 plan-only 复算 | 14,652 |
| P0-full headline root-runs | 56,143 | 53,407 |

首轮 planned AI units 的计算为：

```text
(166 × 8 + 15 × 7 + 15 × 6 + 15 × 7) × 3 endpoints × 3 repeats
= 14,652
```

该数不含 provider retry/replacement；实际 provider calls 和 token/cost 仍只从真实 attempt/usage evidence 报告。另计 EPD-004 的 1,006 个 worker-death supporting baselines 后，P0-full 实际调度 root-runs 为 54,413；论文正式矩阵 headline 仍为 53,407。

#### 2026-07-24 Exp5 行为与指标审计

三个 endpoint 的 fixed config/entry binding、v2 model execution artifact 和 resolved-model mismatch 隔离已经真实实现，但正式报告仍有五个 blocker：

1. formal identity match 可被 runner 硬写的 `fixed_entry_match` 掩盖；
2. 严格 `build_exp5_model_execution_rows()` 没接入正式 CLI，简化 JSONL 还读取错误的 model record ref 字段；
3. 没有独立的三端点正式 comparison CSV、三遍 aggregate 和 provider confounding；
4. wall-clock/quantile/eligibility 仍受固定协议时间和硬编码值影响；
5. preflight 没有强制三个 endpoint 的公共 request controls 相同。

第六个旧 selection 耦合缺口由本 EPD 直接解决，不再把 Exp5 继续绑到 Exp2。

2026-07-25 Task 9–10 实现已关闭上述五个 blocker：formal runner 不再写 `fixed_entry_match` 覆盖 observed identity；每个 attempt 从持久化 v2 record 与 request/raw/provenance/usage artifact 构造 strict join input，metrics 通过 `build_exp5_model_execution_rows()` 输出 `model_execution_records.jsonl`；正式表为 `paper_table_model_endpoint_comparison.csv`，保留逐 endpoint/domain/topic/repeat 行和三遍 aggregate，并显式标记 model-provider endpoint confounding；runtime timing 与最终 task eligibility 由共享 evidence 路径传播；formal runner 还会复核 Task 9 的 normalized cohort request-controls snapshot。

#### 待同步清单

- [x] 唯一权威实验设计和 P0-full 总规模。
- [x] Exp2–Exp5 实验设施补全实施计划。
- [x] `paper_exp5_model_comparison.py` 的 hard-only condition/selection/root-run 常量。
- [x] `paper_model_policy.py` 的跨 endpoint request-controls preflight。
- [x] strict v2 join、identity audit、真实 timing/eligibility 和 model endpoint comparison CSV。
- [x] budget / plan-only identity / digest。
- [x] Task 9–11 tests、progress、feature、code map、预算/CLI/capturing 和定向验证。

### EPD-007：正式 Experiment 1–5 AI 请求超时改为 100 秒

| 字段 | 决定 |
|:---|:---|
| 决定日期 | 2026-07-26 |
| 用户原意 | 将当前正式 Experiment 1–5 的单次 AI API 响应 timeout 从 30 秒改为 100 秒；确认采用只修改正式实验路径的方案。 |
| 适用实验 | Experiment 1–5 的正式 AI provider request controls、tracked Exp1 baseline/pilot 配置和 Exp5 cohort preflight。 |
| 原参数 | `timeout_seconds=30`。 |
| 新参数 | 当时统一为 `timeout_seconds=100`。2026-07-28 起 Exp1–4 已由 EPD-009 重新预注册为 600 秒，worker-death guard 相应为 630 秒；Exp5 cohort v2 曾继续使用本决定的 100 秒，但已在 2026-07-29 被 EPD-011 的 cohort v3 `timeout_seconds=600` 取代，只保留历史 replay。 |
| 不受影响范围 | 通用 `AIAPIExecutor`/paper adapter 30 秒缺省回退、协议 lease 300 秒、Lean payload/checker 30 秒资源限制、历史 direct Factorization 500 benchmark 60 秒。 |
| identity / budget | provider config、pilot profile、condition、budget 和 approval digest 均随参数变化；任何 30 秒配置下的旧 digest 不得复用。 |
| 状态 | `superseded`：当时实现与验证证据保留；当前 Exp1–4 由 EPD-009、Exp5 v3 由 EPD-011 覆盖，100 秒不再是现行正式参数。 |

#### 修改理由

30 秒不足以覆盖部分真实模型在复杂 Factorization/Lean 提示下的正常响应尾部。100 秒只扩大单次 provider 请求等待窗口，不改变模型、prompt、题库、worker、repeat、provider-attempt 或协议 replacement 设计。

#### 规模与论文口径影响

- root、AI unit、首次 provider attempt 和 replacement reserve 数量不变。
- Exp1 pilot 的顺序 wall-clock timeout ceiling 从请求上限重新派生；formal suite 的 request-limit commitment 同样改变 digest。实际 latency、token 和 cost 仍只从真实 provider evidence 报告。
- Exp5 三端点必须分别显式配置 100 秒；共同使用 30 秒也视为正式控制变量漂移并整体 blocked。

#### 待同步清单

- [x] 唯一权威实验设计。
- [x] Exp1–4 runner / CLI / Gate C 和 tracked baseline/pilot 配置。
- [x] Exp5 公共 controls 和 100 秒 fail-closed。
- [x] tests 与新 pilot mock approval digest。
- [x] timeout 直接影响集与 Fast 验证；feat-011 尚未完成，本次不运行 Full。
- [x] progress / feature / code map。

### EPD-008：Experiment 1–4 切换官方 DeepSeek-V4-Pro，Experiment 5 新建 cohort v2

| 字段 | 决定 |
|:---|:---|
| 决定日期 | 2026-07-27 |
| 用户原意 | Experiment 1–4 的当前正式 baseline 改为官方 DeepSeek `deepseek-v4-pro`；Experiment 5 新建 GLM/DeepSeek/GPT 三端点 cohort v2，不原地修改含 Qwen 的 v1。 |
| Experiment 1–4 | `provider_family=deepseek`，`base_url=https://api.deepseek.com`，`endpoint=/chat/completions`，thinking enabled，`reasoning_effort=high`，当时 `timeout_seconds=100`、`max_tokens=8192`、`max_in_flight_global=50`；请求上限已由 EPD-009 版本化替代。 |
| Experiment 5 v2 | SiliconFlow GLM-5.2 `enable_thinking=true`、官方 DeepSeek-V4-Pro high、OpenAI GPT-5.6 Sol high；三个端点公共 `timeout_seconds=100`、`max_tokens=8192`，不受 EPD-009 影响。 |
| 并发语义 | `worker_count` 与 provider inflight limit 是独立维度；Experiment 2 保留 1/3/7/10/30/50 六档。 |
| usage / cost | DeepSeek CNY 价格：cached input 0.025、uncached input 3、output 6（每 1M tokens）；cache 明细缺失按 uncached 保守估算，缺 usage 标记 `usage_missing`。本地数值只叫 `cost_estimate`；CNY/USD 分币种报告，不直接合计。 |
| 历史兼容 | `exp1_baseline_provider_config.v1.json`、`exp1_minimal_pilot_profile.v1.json`、`model_comparison_cohort.v1.json`、旧 digest 和旧 evidence 保持只读可 replay。 |
| 授权边界 | 本决定只授权实现与离线验证；不得调用付费 API 或启动 pilot/正式实验。 |
| 状态 | `superseded`：v2 实现与验证证据只供历史 replay；当前 Exp1–4 请求上限由 EPD-009、Exp5 cohort 由 EPD-010/011 覆盖。 |

#### identity 与版本影响

- 新 baseline config/profile 和 cohort v2 各自产生新 digest；任何 v1 digest 不得被重算或伪装成 v2。
- DeepSeek request identity 保存 resolved controls 和安全 metadata，不保存 Authorization/key；`reasoning_content` 只进入受控 raw provider evidence，checker 继续消费最终 `content`。
- timeout、429、空响应、无效 JSON/schema、usage missing 保持真实稳定分类；resume/replay 读取已持久化 attempt，不重发 provider 请求。

#### 待同步清单

- [x] 权威设计、AGENTS、README/CLI。
- [x] DeepSeek provider/config/transport/adapter 与 usage/cost。
- [x] Experiment 1–4 defaults、Experiment 5 cohort v2 与 v1 compatibility。
- [x] RED/GREEN 定向测试。
- [x] Fast/Full、secret scan、最终 digest/status evidence。

### EPD-009：Experiment 1–4 DeepSeek 重新预注册为 600 秒 / 300K

| 字段 | 决定 |
|:---|:---|
| 决定日期 | 2026-07-28 |
| 用户原意 | 将 DeepSeek 的 timeout 统一提升到 600 秒、max tokens 提升到 300K，调整正式配置并重新预注册；经范围确认只覆盖 Exp1–4 和同题 hard×10 诊断。 |
| 适用实验 | Experiment 1–4 的官方 DeepSeek pilot、正式 condition、故障恢复 attempt 和消融 mode；同配置的 hard×10 / concurrency=10 smoke diagnostic。 |
| 原参数 | tracked v2：`timeout_seconds=100`、`max_tokens=8192`。 |
| 新参数 | tracked v3：`timeout_seconds=600`、`max_tokens=300000`；thinking enabled、`reasoning_effort=high`、stream false、每 AI unit 一次 provider attempt、provider inflight=50 均不变。 |
| 不受影响范围 | Experiment 5 cohort v2 的 GLM/DeepSeek/GPT 三端点继续公共 `timeout_seconds=100`、`max_tokens=8192`；题库、prompt、parser、checker、worker、repeat、分母和 retry 不变。 |
| 版本/identity | 新建 `exp1_baseline_provider_config.v3.json`、`exp1_minimal_pilot_profile.v3.json` 和新 digest；v2 文件/digest/历史 evidence 原样保留，不能复用旧 approval/config identity。 |
| 官方能力依据 | DeepSeek 官方 2026-07-28 文档列出 V4-Pro context 1M、最大输出 384K，故 300K 在上限内；官方 rate-limit 文档列出 V4-Pro 并发 500 与 10 分钟未开始推理的服务端关闭边界。 |
| 状态 | `verified`：v3、Exp1–4 fail-closed 常量、预算/CLI、RED/GREEN、Fast/Full 与真实 hard×10 diagnostic 均已完成；Experiment 5 cohort v2 digest/controls 未变化。 |

#### 修改理由

关闭代理后的 hard×10 诊断中，6/10 响应在 8192 个 reasoning tokens 处 `finish_reason=length` 且最终 content 为空。该证据支持提高 DeepSeek 单次生成上限，但不证明 GLM/GPT 支持同一上限，因此只版本化调整 Exp1–4 DeepSeek。

#### 规模与论文口径影响

- root、AI unit、worker、repeat、首次 provider attempt 数量和统计分母均不变。
- plan budget 的单 attempt token upper bound 更新为 `300000 + 4096 = 304096`；它是保守上限，不是实际 usage。
- worker-death process guard 继续按请求 timeout + 30 秒派生，Exp3 新值为 630 秒。
- Experiment 5 的 cohort/request-controls/digest 不随本决定改变。

#### 待同步清单

- [x] 唯一权威实验设计与 AGENTS/README。
- [x] v3 provider config/profile、Exp1–4 runner/CLI 与 fail-closed tests。
- [x] Exp5 v2 digest/100/8192 不变性测试。
- [x] Fast/Full 与真实 diagnostic evidence。
- [x] progress / feature / handoff / code map 最终回填。

#### 验证与真实诊断证据

- 最终 Full=`1421 passed, 1 skipped in 762.24s`；此前一次 Full 的 13 个 Gate C failures 均来自测试夹具仍加载 v2 request controls，修复后该文件 `33 passed`，生产 fail-closed 未放宽。
- 实际 run=`deepseek_hard10_concurrency10_direct_300k_20260727T182217Z`，config/selection digest=`sha256:4bdf0d330c8d01af9760f17b333d75a68f0b8bfad214e807072562e057ef3923` / `sha256:af0cf36cbea5787d4c1267f08d4451d6a949ffe125c9b6ba5d4a3029676ddcca`。
- 10/10 请求均 HTTP 200 且带 usage，8 checker-valid、2 checker-rejected，0 retry/429/usage-missing；provider-reported total=`239,585` tokens，其中 nested reasoning=`221,563`。该证据仅属 smoke/regression，`paper_eligible=false`，没有运行正式矩阵。

### EPD-010：Experiment 5 hard-only 题库按分层确定性规则减半

| 字段 | 决定 |
|:---|:---|
| 决定日期 | 2026-07-29 |
| 用户原意 | “把 Exp5 中运行的 hard-only 题库删减一半，直接对半删，不要影响其它实验的题库。” |
| 适用实验 | 仅下一版 Experiment 5 model comparison；不回写或裁剪共享正式 catalog。 |
| 原题库 | 每个 model-repeat 使用 Factorization hard 166 道，加 Lean `hard_frontier` 三个 topic family 各 15 道，共 211 道。 |
| 新题库 | Factorization hard 保留 83 道；Lean `pure_logic/function_set/induction` 各保留 8 道，共 24 道；每个 model-repeat 合计 107 道。 |
| 固定选择规则 | 在 Factorization hard 与三个 Lean topic stratum 内分别按 `sha256(case_id)` 升序排列，保留前 `ceil(n/2)`；把所选 case IDs、顺序、父 catalog digest 和 selection digest 冻结到 Exp5 专属版本化 selection artifact。不得依据模型输出、checker 结果、延迟或成本事后换题。 |
| 配对规则 | 所有模型、repeat 和相同 domain/topic condition 使用完全相同的冻结 case 集及顺序，保证逐 root 配对比较。 |
| 不受影响范围 | Experiment 1–4 的 catalog、selection、root 数、split/lemma-DAG、repeat 和论文分母全部不变；Factorization/Lean 共享正式 catalog 文件也不删除任何题。Exp5 小型 smoke 仍为每模型 2 个直接 roots，不因正式题库减半而再缩小。 |
| 状态 | `superseded`（由 EPD-026 取代新运行题量）：本节 v3 selection artifact 与 digest 保持不可变，只供历史 replay，并作为 v4 parent selection provenance；hard-only、分层确定性和跨模型同题顺序原则继续有效。 |

#### 奇数题格的取整口径

“对半”必须在不破坏 Lean 三个 topic family 覆盖的前提下变成唯一可执行规则。Factorization 的 166 道可精确减半为 83 道；每个 Lean topic 有 15 道，无法精确二分，因此每格向上取整保留 8 道。最终保留 `107/211 = 50.71%`，删除 104 道。分层后再做确定性哈希选择，避免按 catalog 原顺序截取造成系统性偏斜，也避免运行后挑题。

#### 规模与论文口径影响

当前讨论中的四模型、3-repeat Exp5 若保持不变，则：

| 指标 | 减半前四模型方案 | EPD-010 |
|:---|---:|---:|
| unique roots / model-repeat | 211 | 107 |
| formal conditions | 48 | 48 |
| Experiment 5 root-runs | 2,532 | 1,284 |
| 首轮 planned AI units | 19,536 | 9,888 |
| 小型 smoke direct root-runs | 8 | 8 |

首轮 planned AI units 的计算为：

```text
(83 × 8 + 8 × 7 + 8 × 6 + 8 × 7) × 4 models × 3 repeats
= 9,888
```

该数不含 provider retry/replacement；实际 provider calls、tokens、cost 和 latency 仍只从真实 attempt/usage evidence 报告。若后续为完整四模型顺序平衡而把 repeats 从 3 改为 4，必须另立决定并重新计算条件、root-run、AI-unit 和预算 identity；EPD-010 本身不修改 repeat 数。

#### 待同步清单

- [x] 实验参数决策台账。
- [x] 唯一权威实验设计中的下一版 Exp5 题库口径。
- [x] Exp5 专属 selection artifact、runner 常量和 condition expansion。
- [x] budget / P0-full 总量 / plan-only identity / digest。
- [x] smoke profile 与 catalog/selection drift gates。
- [x] metrics/report 的样本量与 paired comparison metadata 的离线模块；production CLI/replay 完整接线仍受 EPD-011 当前 P1 门禁约束。
- [x] tests、progress、feature、handoff 和 code map 的离线同步；真实 smoke/formal 仍未授权。

### EPD-011：Experiment 5 cohort v3 请求参数、论文输出与实施边界

| 字段 | 决定 |
|:---|:---|
| 决定日期 | 2026-07-29 |
| 用户原意 | 用户在审阅四模型、半量题库、参数和论文输出设计后明确要求“直接实施”，由主线程监督 `gpt-5.6-sol` 超高强度子线程分步执行；目标是让 Experiment 5 能跑通，不跑全量测试，不处理人为注入攻击。 |
| 适用实验 | 下一版 Experiment 5 SiliconFlow 四模型 cohort v3；不覆盖 Exp1–4 或 Exp5 v1/v2 replay。 |
| 公共请求参数 | `timeout_seconds=600`、`max_tokens=32768`、`temperature=0.0`、`top_p=1.0`、`stream=false`、`max_provider_attempts=1`、SiliconFlow 全局 in-flight=`3`。 |
| reasoning profile | GLM-5.2、Qwen3-14B 与 MiniMax-M2.5：`enable_thinking=true, thinking_budget=32768`；Pro/DeepSeek-V3：`enable_thinking=false` 且不发送 `thinking_budget`。MiniMax 修订依据是实际请求即使显式发送 `enable_thinking=false` 仍返回 reasoning usage。 |
| 调度 | 模型 arm 顺序执行，3 repeats 使用预注册 3×4 部分平衡顺序；condition worker capacity 与共享 SiliconFlow provider gate 均为 3。 |
| 论文强制输出 | 审计 CSV/JSONL、overall/by-domain-topic 表、六个无序模型对的 paired comparisons、order/concurrency 与 failure taxonomy、LaTeX 表、PDF/SVG 图、数据派生结果摘要和失败附录。 |
| 预算与 pricing | token plan 按 entry 计算，当前保守 ceiling=`607,518,720`；正式 config 必须冻结可审计的 SiliconFlow official pricing snapshot，缺失则正式货币预算/preflight blocked，不猜价格。 |
| 验证边界 | 只运行 Exp5/provider/metrics/report/CLI 定向 pytest 与必要 compile/Fast；不运行全量 pytest、`init.ps1 -Full`、LeanAudit 或正式矩阵。 |
| 排除范围 | 不新增、修复或扩展人为注入攻击、对抗性输入或安全工程；只保持既有 secret 不落盘与正常实验正确性门禁。 |
| 真实调用边界 | 本决定批准代码与离线定向测试，不自动批准真实付费 API。四 entry capability smoke、8-root smoke 与 1,284-root 正式矩阵仍分别需要运行前明确授权。 |
| 状态 | `implemented_offline_with_p1_blockers`：v3 selection/identity/budget/renderer 模块与 bootstrap 入口已实现并有历史定向证据，但 2026-07-30 全链 review 发现 canonical v3 provider-config binding、四 member artifact-backed smoke evidence、部分/失败运行分母与 missingness、六类 renderer 的 production CLI/replay 接线尚未闭合。五次最小 endpoint/key 诊断不是正式 evidence；8-root smoke 未运行，P0-full NO-GO。 |

#### 待同步清单

- [x] v3 设计与实施计划。
- [x] 参数决策台账、权威设计和导航。
- [x] transport / identity / usage。
- [x] cohort / config / selection / policy / runner / budget。
- [ ] metrics / statistics / paper artifacts 的 production CLI/replay 全链闭合；离线模块已存在，但不能据此解除正式门禁。
- [x] 定向测试、plan-only、progress、feature、handoff 和 code map。

#### 2026-07-30 离线实施证据与运行边界

当前冻结 cohort/provider/semantic-selection/sequence digest 分别为 `sha256:1b317c9827d87c43b974b77c469b115906da463a79064d3ffb3b949dc5257942`、`sha256:6b1d6ffe977340ebbf59d69368d8c977fef664007575de72593c08c038d5ffe1`、`sha256:fbec153a02befa4071b4ad4d639e51e910a3bc4c433cc9eea4b19ac6489a3490`、`sha256:de9271732513d7d2ab20624d2949af3c9230b9adc2cd8db4b1f1ddb0d57b58cb`。正式矩阵为 48 conditions / 1,284 roots / 9,888 planned first-attempt AI units / 607,518,720 token ceiling；29-root 综合 profile 外新增 `paper_smoke_exp5_profile.v3.json`，固定 8 direct roots、digest=`sha256:b44646d8b298200165abb73155304085c6800fab9ab363aa52483bd03556e61b`。输出契约覆盖 CSV/JSONL/TeX/PDF/SVG/Markdown。

#### 2026-07-30 用户批准修订：MiniMax thinking 与首次 smoke bootstrap

- 最小真实请求证明 MiniMax-M2.5 在显式 `enable_thinking=false` 时仍返回非空 reasoning，复核 usage 为 87 reasoning tokens；用户据此批准把 MiniMax 预注册为 thinking，使用 `thinking_budget=32768`。该诊断不冒充正式 capability/smoke evidence。
- 旧实现把“正式矩阵必须已有 passed smoke evidence”的 eligibility preflight 直接复用于“首次 smoke 用来生成 evidence”的启动路径，形成循环门禁；这是设施复用与测试覆盖缺陷，不是实验设计要求。
- 修订后正式 preflight 默认仍要求 passed evidence；Exp5 smoke bootstrap 显式不要求先验 smoke evidence，但仍验证四模型 identity、config/key、catalog/selection、request limits 和预算。新增物理独立 8-root profile，并允许 `--local-ai-api-config` 只向当前进程注入 `SILICONFLOW_API_KEY`。

#### 2026-07-30 用户批准修复与少量真实 API 验证

- 用户明确要求修复 Exp5 smoke 启动问题，并批准修复期间少量调用 AI API 验证设施。该授权不等于批准完整 8-root smoke 或正式矩阵。
- 实际根因是 runner 漏投影预注册的 `thinking_budget`，而不是修改 EPD-011 参数；实现已改为 preflight/runner 共用唯一 reasoning-controls projector。启动前异常也会在尚无 manifest 时留下 blocked/0-attempt evidence。
- 共执行 2 个顺序 Qwen provider attempts：首个远端断开且无 HTTP status，第二个成功，usage=`397 total / 303 reasoning tokens`；两个 probe 都 artifact-backed、secret scan=0，但仍是 paper-ineligible diagnostic。
- 空 run01 不可恢复；完整 8-root smoke 仍未运行，正式 preflight/P0-full 保持 blocked。定向验证 155 passed，Fast 412 passed/1 skipped；未运行 Full、全量 pytest、LeanAudit 或正式矩阵。

### EPD-012：Exp3 复用 Exp1 shared reference、双轴终态与 Exp3–4-only smoke

| 字段 | 决定 |
|:---|:---|
| 决定日期 | 2026-07-29 |
| 用户原意 | 执行 FEAT-011 prompt：正式 Exp3 不再重复跑无故障 baseline；区分实验失败与设施失败；新增不要求 baseline 的 Exp3–4-only smoke。 |
| 正式 baseline | Exp3 按同 `case_id` 引用正式 Exp1 `repeat_id=0,seed=1,worker_count=10` 的持久化 root evidence；所有 fault/repeat/death 条件可共享同一 source root。reference 自身 provider calls/tokens/cost 为 0，source usage 仍保留为真实比较输入。 |
| 正式 fault rates | Factorization=`1/5/10/25/50/100%`；Lean=`10/50/100%`。0% 只投影 Exp1 reference row，不是 Exp3 condition/root/provider call。 |
| comparison | `comparison_kind=shared_reference`，不得宣称 same-repeat/paired baseline。Exp1 root 有完整失败 evidence 时 Exp3 继续，`baseline_comparison_eligible=false`，所有 delta 为 `null`；source usage 不伪装为 0。 |
| supporting roots | 删除 rate-fault 0% 重复 roots 和 worker-death dedicated no-kill roots；Exp3 supporting baseline roots/AI units/provider attempts 全部为 0。 |
| 双轴终态 | `outcome_status={succeeded,failed_experimental,blocked_dependency}`；`evidence_integrity={complete,missing,invalid,corrupt}`。可信实验失败继续，设施/identity/evidence 失败停止新 API 但闭合 manifests；`incomplete` 只用于中断或终态持久化失败。 |
| smoke 例外 | `paper_smoke_exp3_exp4_v1` 精确 11 roots，仅 `factor_v2_easy_001,repeat0,worker10`：五类 r100、dead1/p50、Exp4 五 modes。冻结 `baseline_policy=omitted_for_smoke_regression`，reference=null，comparison=false，reason=`smoke_baseline_not_requested`，始终 paper-ineligible。 |
| provider/request | Exp1–4 官方 DeepSeek `deepseek-v4-pro` / `deepseek_v4_pro_exp1_baseline`，thinking enabled、high、`600/300000`、stream false、单 AI unit 一次 provider attempt、provider retry 0、inflight 50；本决定不改这些控制。 |
| 授权边界 | 只实施代码、文档、离线 TDD/Fast/identity；不得启动真实 API、smoke、resume/restart 或修改 run01–run04。 |
| 状态 | `verified_offline`：canonical plan/budget/reference/终态/smoke/profile/CLI/launcher/metrics/report 已实现；定向与 Fast 证据记录在 `progress.md`。未调用真实 provider。 |

#### 新规模与 identity

| 指标 | EPD-012 |
|:---|---:|
| Exp3 rate-fault roots | 30,090 |
| Exp3 worker-death roots | 6,036 |
| Exp3 合计实际 roots | 36,126 |
| supporting baseline roots | 0 |
| P0-core 唯一实际 roots | 46,478 |
| P0-full（历史三端点 Exp5 v2）唯一实际 roots | 48,377 |
| P0-full（当前四模型 Exp5 v3）唯一实际 roots | 47,762 |
| Exp3–4-only smoke direct/actual roots | 11 / 11 |

condition、selection、profile、execution-plan 和 budget identity 均随本决定重新生成；execution-plan/budget digest 继续绑定具体 `output_root`。旧 digest 和 run01–run04 evidence 只读保留，不重算、不升级。

#### 待同步清单

- [x] 权威实验设计和本参数台账。
- [x] Exp3 canonical plan、FormalEvidenceStore、runner、budget、metrics/report。
- [x] 11-root profile、CLI identity、v3 launcher 与离线 identity-only。
- [x] RED/GREEN 定向测试与 Fast。
- [x] code maps、导航、feature/progress/handoff。

### EPD-013：论文正确性主指标统一使用全部预注册 root-run 作为分母

| 字段 | 决定 |
|:---|:---|
| 决定日期 | 2026-07-31 |
| 用户原意 | 保留原始口径：`最终结果正确的题数 / 全部预注册题数`；不采用只统计实际产生最终结果题目的条件正确率。 |
| 适用实验 | Experiment 1–5 的正确性主指标，以及按 domain、difficulty、topic、condition、repeat、fault、ablation、model 分层后的对应汇总。 |
| 指标名称 | `end_to_end_verified_success_rate`（端到端验证成功率）。 |
| 分子 | 存在最终结果，且由冻结的 Factorization 正确性判断或固定 Lean checker 确认正确，并具备完整、一致的 task/attempt/event/artifact 证据的 root-run 数。 |
| 分母 | 对应汇总单元的全部预注册 root-run；实验性失败、规定重试用尽、无返回、超时、未恢复 worker death 和未得到可接受结果均不得从分母删除。 |
| 禁止口径 | 不以“实际产生最终结果的题数”为论文主指标分母；不把未解决 root-run 从主正确率中排除。 |
| 解释性输出 | 单独保留错误最终结果数、未得到可接受结果数与 failure stage/kind，解释主指标下降原因；这些列不替换主指标分母。 |
| 无效运行 | suite/condition 因配置、identity 或基础设施阻断而 paper-ineligible 时，审计仍保留固定分母，但该运行不得作为论文低成功率结果发布。 |
| 状态 | `design_synced`：用户已确认，唯一权威实验设计已同步；现有 runner/metrics/report 字段、测试和论文表格是否完全采用该名称与分子/分母规则，留待本轮完整指标追溯实施计划审计后进入 `implemented` / `verified`。 |

#### 修改理由

条件正确率会把重试用尽、无返回或最终没有可接受答案的题排除在分母之外，使系统表现虚高。全部预注册 root-run 分母能同时保留成功与失败，不需要把“未得到答案”误称为“错误最终答案”；失败原因仍通过互斥结果分类和逐 root evidence 单独解释。

#### 论文与实现影响

- Experiment 1、4、5 当前 `accepted validity` / `validity` 表述必须统一映射到本主指标，不能出现第二套条件分母。
- Experiment 2、3 中凡报告最终结果正确性，也使用相同 root-run 分母；吞吐、恢复等专项指标继续使用各自明确分母。
- `per_task_results.jsonl` 必须能区分 verified correct、错误最终结果和未得到可接受结果；汇总表必须保存预注册分母与三类计数。
- 现有代码若仍只输出 `accepted_validity_rate`，实施计划必须先确认其实际分子/分母，再决定兼容别名、schema 版本化或字段替换，不能只改列名。

#### 待同步清单

- [x] 唯一权威实验设计。
- [ ] runner / metrics / report 与 machine-readable metric contract。
- [ ] Exp1–5 逐 root、condition、repeat、aggregate 测试。
- [ ] 离线真实产物 replay 与论文表格端到端门禁。
- [ ] progress / feature / code map（在完整实施计划确认并实施时同步）。

### EPD-014：完成率表示产生最终结果的预注册 root-run 比例

| 字段 | 决定 |
|:---|:---|
| 决定日期 | 2026-07-31 |
| 用户决定 | 同意将完成率定义为：`实际产生最终结果的 root-run 数 / 全部预注册 root-run 数`。 |
| 适用实验 | Experiment 1–5 的 completion 主指标及其所有正式分层汇总。 |
| 指标名称 | `completion_rate`。 |
| 分子 | 实际产生最终 canonical/root result，并具备完整结果引用的 root-run 数；不要求最终结果正确。 |
| 分母 | 与 EPD-013 完全相同的全部预注册 root-run。 |
| 不计入分子 | 规定重试用尽、无返回、超时、未恢复 worker death，以及 `failed`、`blocked`、`budget_exhausted`、`ineligible`、`not_started` 等没有最终结果的 root-run。 |
| 与正确性关系 | 完成且正确同时进入 completion 与 EPD-013 分子；完成但错误只进入 completion 分子；未得到最终结果不进入任一分子。 |
| 状态 | `design_synced`：用户已确认，唯一权威实验设计已同步；runner/metrics/report/schema 与真实产物端到端验证尚待完整实施计划覆盖。 |

#### 修改理由

完成率只回答“系统是否最终给出结果”，端到端验证成功率回答“系统是否最终给出正确结果”。二者共享全部预注册 root-run 分母，差距能够揭示已经产出但内容错误的最终结果，同时不会隐藏重试用尽或未得到答案的题。

#### 待同步清单

- [x] 唯一权威实验设计。
- [ ] runner / metrics / report 与 machine-readable metric contract。
- [ ] 逐 root 三分类和 `completion_rate - end_to_end_verified_success_rate` 一致性测试。
- [ ] 离线真实产物 replay、smoke 与论文表格门禁。
- [ ] progress / feature / code map（在完整实施计划确认并实施时同步）。

### EPD-015：验证检出能力只使用 Experiment 3 的受控错误分母

| 字段 | 决定 |
|:---|:---|
| 决定日期 | 2026-07-31 |
| 用户决定 | 验证错误检出属于 Experiment 3 已有的受控故障实验，不再另造自然错误检出率；验证机制对完整系统的作用由 Experiment 4 的 `FULL` 与 `NO_VERIFICATION` 对照体现。 |
| 受控错误拦截率分母 | `false_positive` 中预注册、实际完成注入且确实到达 verification 边界的已知错误候选数。 |
| 受控错误拦截率分子 | 同一分母中有直接 verifier evidence 证明被明确拒绝的注入候选数。 |
| 受控错误逃逸率 | 使用同一受控分母；分子是有 canonical/root-result evidence 证明已穿过验证边界的注入候选数。 |
| 严格排除 | 自然产生但没有独立错误标签的候选、无返回、超时、parser/executor error、worker death 或其他 fault 不得进入上述分母。 |
| 自然运行统计 | 可以报告 verifier 拒绝次数或进入验证候选中的拒绝占比，但只能解释验证机制介入频率，不得称为自然错误检出率。 |
| Experiment 4 职责 | 在相同 `case_id × repeat_id` 上比较 `FULL` 与 `NO_VERIFICATION` 的 completion 和 `end_to_end_verified_success_rate`，回答开启验证是否改善最终系统结果，不重复计算检出率。 |
| 状态 | `design_synced`：用户已确认，唯一权威实验设计已同步；现有 detection/false-accept 字段的兼容、schema 和论文表格迁移留待完整指标追溯实施计划。 |

#### 修改理由

自然错误总数无法由待评价的 verifier 自身给出；用 verifier 已经发现的错误推测未发现错误会形成循环论证。Experiment 3 的预注册受控错误提供了运行前可知且可追踪的分母，Experiment 4 的消融则提供验证机制对端到端结果影响的独立系统级证据，二者不能混成一个指标。

#### 待同步清单

- [x] 唯一权威实验设计。
- [ ] machine-readable metric contract、runner、metrics 与 report 字段。
- [ ] fault/attempt identity、互斥 block/escape outcome 与不完整 evidence fail-closed 测试。
- [ ] Experiment 4 配对差值、离线 replay、smoke 和论文表格门禁。
- [ ] progress / feature / code map（在完整实施计划确认并实施时同步）。

### EPD-016：删除含义模糊的 recovery rate，仅保留替代尝试成功率

| 字段 | 决定 |
|:---|:---|
| 决定日期 | 2026-07-31 |
| 用户决定 | 检出需要恢复的错误后，协议必须自动启动恢复；“检出后是否启动恢复”的比例与检出重复，不作为论文指标。含义模糊的 `recovery_rate` 删除。 |
| 完整性门禁 | 对协议规定应恢复且已经检出的 fault，必须存在匹配 fault/attempt identity 的 replacement/requeue evidence；缺失不是低恢复率，而是恢复链证据不完整并 fail closed。 |
| 保留辅助指标 | `replacement_attempt_success_rate = 产生合格替代结果并完成原 task unit 的 replacement attempts / 实际启动的 replacement attempts`。 |
| 汇总边界 | 只在 Experiment 3 按 fault type 分别报告，不跨 fault 类型汇总；零分母写 `null` 和 applicability reason。 |
| 与最终结果关系 | 该辅助指标解释重试次数限制或替代回答再次失败；整道题最终是否正确继续使用 `end_to_end_verified_success_rate`，不另造 root 级恢复率。 |
| 状态 | `design_synced`：用户已确认，唯一权威实验设计已同步；现有 `recovery_rate` 字段迁移、兼容和测试留待完整指标追溯实施计划。 |

#### 修改理由

“错误被发现后启动重试”是协议必须满足的行为，不应包装成接近 100% 的论文指标。替代尝试能否真正产生合格结果则受重试次数和模型再次失败影响，具有独立解释价值；root 最终正确性已由全局端到端主指标覆盖。

#### 待同步清单

- [x] 唯一权威实验设计。
- [ ] machine-readable metric contract、runner、metrics、report 与旧字段迁移。
- [ ] 检出到 replacement 的 identity 完整性门禁及 fail-closed 测试。
- [ ] 按 fault type 的替代尝试分子/分母、零分母、离线 replay 与论文辅助表测试。
- [ ] progress / feature / code map（在完整实施计划确认并实施时同步）。

### EPD-017：删除论文 recovery latency，时间代价使用总 wall-clock overhead

| 字段 | 决定 |
|:---|:---|
| 决定日期 | 2026-07-31 |
| 用户决定 | 指标在精不在多；`recovery_latency` 相对总运行时间及故障 overhead 没有足够独立的论文解释价值，从论文指标集合删除。 |
| 论文时间指标 | Experiment 3 使用真实 wall-clock，以及相对 shared Exp1 无故障 evidence 的 wall-clock overhead，回答故障使整道题总共增加多少时间。 |
| 仍保留的时间 evidence | fault 发生/确认、replacement/requeue 启动/结束及 provider latency 原始时间必须继续持久化，用于审计、诊断和重放，不进入论文主表。 |
| 禁止替代 | 不把“故障确认到替代任务开始”的局部派发时间重新包装成另一项论文指标。 |
| 状态 | `design_synced`：用户已确认，唯一权威实验设计已同步；现有 `recovery_latency_ms` 报表字段的移除/审计降级和测试留待完整指标追溯实施计划。 |

#### 修改理由

局部恢复调度时间只能帮助排查慢在调度还是 provider，不能比真实总运行时间和相对无故障 overhead 更直接地支撑论文主张。保留原始时间 evidence 足以满足诊断与可追溯要求，不需要在论文中增加一列。

#### 待同步清单

- [x] 唯一权威实验设计。
- [ ] machine-readable metric contract、metrics/report 论文字段与审计字段分层。
- [ ] wall-clock/shared-reference overhead 完整性和缺失 baseline fail-closed 测试。
- [ ] 原始恢复时间 evidence 的 replay/审计保留测试。
- [ ] progress / feature / code map（在完整实施计划确认并实施时同步）。

### EPD-018：保留 Experiment 3 实际重新分派次数

| 字段 | 决定 |
|:---|:---|
| 决定日期 | 2026-07-31 |
| 用户决定 | 保留 `reassignment_count`，继续作为 Experiment 3 指标。 |
| 计数对象 | 因 fault 或 worker death 实际启动、具有新 `attempt_id`，并能反向关联原 fault 与原 task unit 的 replacement/requeue attempt。 |
| 不计入 | 只有恢复计划、recovery event 或 `retry_allowed=true`，但没有真实启动新 attempt 的记录不得计数。 |
| 汇总边界 | 按 fault type 或 worker-death condition 分别报告；不把不同故障类型的原始计数混成一个性能比例。 |
| 证据要求 | 新旧 attempt、fault、task unit 与 worker/PID（适用时）的 identity 链必须完整；缺失时该指标为 `null` 并使对应专项 evidence fail closed。 |
| 状态 | `design_synced`：用户已确认，唯一权威实验设计已同步；当前实现从 recovery event 计数的行为需在完整指标追溯实施计划中审计和迁移。 |

#### 修改理由

重新分派次数能够直观展示故障条件实际触发了多少次任务迁移，但只有真实新 attempt 才构成重新分派；计划或事件记录不能代替执行事实。

#### 待同步清单

- [x] 唯一权威实验设计。
- [ ] machine-readable metric contract、runner、metrics 与 report 字段。
- [ ] fault→old attempt→new attempt→task unit/worker identity 完整性测试。
- [ ] 按 fault/worker-death condition 的离线 replay、smoke 与论文表格测试。
- [ ] progress / feature / code map（在完整实施计划确认并实施时同步）。

### EPD-019：wasted actual tokens 是 Experiment 3 关键真实性指标

| 字段 | 决定 |
|:---|:---|
| 决定日期 | 2026-07-31 |
| 用户决定 | `wasted_actual_tokens` 必须保留并视为关键指标；它证明故障注入不是只改状态或事后填表，而是已经消耗真实 AI tokens，故障后又实际运行了 AI replacement。 |
| 分子/汇总对象 | 具有真实 provider usage、且因实际 fault/worker death 导致其输出不能进入最终有效 canonical 路径的 attempt tokens 总和。该指标是数量，不计算比例。 |
| 原 attempt 证据 | 每笔 tokens 必须关联真实 provider attempt、raw/provenance/usage、fault identity 以及 rejection/abandonment/canonical exclusion evidence。 |
| 真实重跑证据 | 对协议规定应 replacement/requeue 的记录，必须存在不同 `attempt_id` 的后续真实 provider attempt 及独立 usage evidence；只有 recovery event、计划值或预算不能证明 AI 重新运行。 |
| replacement tokens | replacement attempt tokens 进入总 tokens 与 token/cost overhead；仅当该 replacement 自身也实际失败并被废弃时，才按其自身 evidence 进入 wasted tokens。 |
| 缺失处理 | 必要 actual usage 缺失时汇总写 `null` 并 fail closed；不得补 0，不得用 token budget、max_tokens 或估算值冒充真实消耗。 |
| 指标地位 | Experiment 3 关键论文指标，同时承担真实执行证据职责；不是仅供诊断的辅助列。 |
| 状态 | `design_synced`：用户已确认，唯一权威实验设计已同步；当前实现是否逐 attempt 正确归因、replacement usage 是否闭环，留待完整指标追溯实施计划审计。 |

#### 修改理由

总 token overhead 只能说明故障运行相对基线的净变化，不能直接证明哪些已经付费生成的工作因故障作废，也不能单独证明 replacement 真正调用了 AI。逐 attempt 的 wasted tokens 与后续 replacement usage identity 链共同提供这一真实性证据。

#### 待同步清单

- [x] 唯一权威实验设计。
- [ ] machine-readable metric contract、runner、metrics 与 report 字段。
- [ ] faulted attempt actual usage、canonical exclusion 与 replacement provider attempt identity 闭环测试。
- [ ] usage missing/null、二次 replacement 失败、worker death partial usage 边界测试。
- [ ] 离线 replay、smoke 与论文表格的真实重跑门禁。
- [ ] progress / feature / code map（在完整实施计划确认并实施时同步）。

### EPD-020：Experiment 2 使用正确结果配对加速并精简扩展性指标

| 字段 | 决定 |
|:---|:---|
| 决定日期 | 2026-07-31 |
| 用户决定 | 同意现有 Experiment 2 指标审计建议：逐项判断为保留的全部保留，判断为不保留或重复的直接从论文指标删除。 |
| 正确性门禁 | 每个 worker level 报告 `end_to_end_verified_success_rate` 与 completion；快速失败不得被解释成加速。 |
| 总时间 | `wall_clock_ms` 保存每个预注册 root-run 的真实协议端到端时间，包括正确、错误和未完成终态。 |
| 配对加速 | `paired_speedup = worker=1 wall_clock_ms / worker=k wall_clock_ms`；只在同 `case_id × repeat_id` 两端均端到端正确、时间 evidence 完整且大于 0 时计算。否则为 `null`，同时报告 eligible/ineligible pair count 与原因。 |
| 配对汇总 | 每个 repeat 内按 worker level 报告逐 root 值和 eligible pairs 的中位数，并按 factor position 分层；两个 repeats 原始汇总均保留，只报告 min/max 与相对差。 |
| 资源代价 | 保留实际 provider attempts、total tokens 与 cost；同 `case_id × repeat_id` 相对 worker=1 计算 paired token/cost multiplier。资源倍率要求实际 usage/cost evidence 完整且分母大于 0，不因运行失败删除样本。 |
| 并行真实性 | 保留 planned/executed/unscheduled units、in-flight-at-witness、observed peak concurrency 与 worker utilization；峰值并发不得超过 configured workers、dispatched units 或图宽 20。 |
| 效率 | 只保留解释性 `parallel_efficiency = paired_speedup / configured_worker_count`，沿用 speedup eligibility；删除完全重复的 `efficiency` 别名。 |
| 外部干扰 | 保留 429/rate-limit 与 retry 作为干扰记录和敏感性分析条件，不包装成扩展性收益。 |
| 删除论文指标 | 删除 `throughput = executed_ai_unit_count / wall_clock` 与逐 root `throughput_roots_per_second`。前者会奖励无用执行，后者只是总时间的倒数。 |
| 降级为审计 | critical path 与 provider latency 继续持久化供诊断/审计，但退出 Experiment 2 论文指标集合，不能替代 wall-clock。 |
| 状态 | `design_synced`：用户已确认，唯一权威实验设计已同步；当前 metrics/report/schema、图表与测试仍待完整指标追溯实施计划迁移。 |

#### 修改理由

Experiment 2 必须证明的是“相同任务正确完成时是否更快，以及为此增加多少真实资源”，不能把快速失败或更多无用 AI unit 执行包装成扩展性。现有两个 throughput 不能提供独立且可靠的论文信息，重复 efficiency 也没有保留两列的价值。

#### 待同步清单

- [x] 唯一权威实验设计。
- [ ] machine-readable metric contract、root/condition/repeat aggregation 与旧字段迁移。
- [ ] 双端正确 speedup eligibility、失败 pair null、eligible/ineligible count 与 factor-position 分层测试。
- [ ] actual token/cost paired multiplier、usage missing/null 与全量失败资源保留测试。
- [ ] concurrency inventory/utilization、图宽 20、429/retry sensitivity 与审计字段分层测试。
- [ ] 离线 replay、smoke、论文 CSV/JSON/figure table 与旧 throughput/efficiency 字段删除测试。
- [ ] progress / feature / code map（在完整实施计划确认并实施时同步）。

### EPD-021：Experiment 3 保留 overhead 名称并披露 shared-reference 语义

| 字段 | 决定 |
|:---|:---|
| 决定日期 | 2026-07-31 |
| 用户决定 | 保留 `wall_clock_overhead`、`token_overhead`、`cost_overhead` 名称，不改成 shared-reference delta。 |
| 比较来源 | 继续使用 EPD-012 的正式 Exp1 持久化 shared reference，不新增 Experiment 3 0% 或 dedicated baseline provider calls。 |
| 强制披露 | 论文表头、caption 或方法说明必须标明 `comparison_kind=shared_reference`，说明它不是同一次、同 repeat 的严格 paired baseline。 |
| 禁止表述 | 不得声称 overhead 的全部差异都由 fault 单独造成，不得写成严格 paired causal effect。 |
| 状态 | `design_synced`：用户已确认，唯一权威实验设计已同步；renderer/caption/schema 与测试留待完整指标追溯实施计划。 |

#### 待同步清单

- [x] 唯一权威实验设计。
- [ ] machine-readable comparison kind、paper table/caption 与方法说明。
- [ ] shared reference 缺失或 paper-ineligible 时 overhead=`null` 的 fail-closed 测试。
- [ ] 离线 replay、smoke 与正式论文渲染测试。

### EPD-022：Experiment 3 正式规模正在由其他工作调整

| 字段 | 决定 |
|:---|:---|
| 决定日期 | 2026-07-31 |
| 用户通知 | Experiment 3 全量运行资源消耗过大，用户正在通过其他工作修改正式规模；本轮任务只讨论指标，不负责该规模修改。 |
| 当前边界 | 本轮不得修改 fault rates、root 数、repeat、worker-death matrix、预算或总 root-run 数，也不得覆盖其他工作产生的规模变更。 |
| 协同规则 | 在指标设计转入实施计划或任何真实运行前，必须重新读取唯一权威实验设计、参数台账和工作树中的最新规模；当前文档中的 36,126 root-runs 不得被本轮当作最终定案重新固化。 |
| 对指标的影响 | 指标定义可以继续讨论；任何依赖样本数、分层、统计汇总或预算的实现步骤必须等待最终规模同步后再冻结。 |
| 状态 | `superseded`（由 EPD-026 关闭未决规模）：本节保留为并行协同 provenance；最终规模已经机器可读冻结并完成离线验证。 |

#### 待同步清单

- [ ] 等待负责规模修改的工作给出用户确认结果。
- [ ] 最终结果同步唯一权威实验设计、runner plan、budget、测试及论文样本数。
- [ ] 指标实施计划开始前重新审计规模依赖，避免覆盖并行修改。

### EPD-023：区分论文额外 token 表述与作废 attempt tokens

| 字段 | 决定 |
|:---|:---|
| 决定日期 | 2026-07-31 |
| 用户决定 | 论文正文为了易于理解，使用“相对正常运行多出来的 tokens”解释额外 token 消耗，并在方法中进一步说明。 |
| 对应机器指标 | 该说法对应 `token_overhead = fault-condition total actual tokens - shared Exp1 reference total actual tokens`，同时遵守 EPD-021 的 shared-reference 非配对披露。 |
| `wasted_actual_tokens` | 仍表示已经生成、后来因 rejection/abandonment 等未进入有效 canonical 的 attempt actual tokens；它不是相对 reference 的净增量。 |
| 真实重跑证明 | `wasted_actual_tokens` 单独只能证明已有 AI 工作被作废；AI 确实重新运行必须由新 `attempt_id`、独立 provider response/provenance 与 actual usage 证明。 |
| replacement tokens | 成功 replacement 的 tokens 进入 total tokens/overhead，但不算 wasted；replacement 自身也失败并被作废时，才按其独立 evidence 进入 wasted。 |
| 状态 | `design_synced`：用户已确认，唯一权威实验设计已同步；论文文案、machine-readable contract 与逐 attempt 归因测试留待完整实施计划。 |

#### 修改理由

人为 fault 不会让原 provider 调用凭空使用更多 tokens；它使已经生成的结果失去用途，重试才产生后续额外消耗。分别保留净增量和作废 tokens，才能同时回答总体资源代价与已有 AI 工作浪费。

#### 待同步清单

- [x] 唯一权威实验设计。
- [ ] 论文表头/caption/method 的通俗表述与 shared-reference 限定。
- [ ] total overhead、discarded attempt 与 replacement usage 三本账的一致性测试。
- [ ] 离线 replay、smoke 与论文表格逐 attempt 追溯门禁。

### EPD-024：Experiment 4 使用独立 FULL 配对与四个 mode 专项指标

| 字段 | 决定 |
|:---|:---|
| 决定日期 | 2026-07-31 |
| 用户决定 | 不再考虑使用 Experiment 1 替代 baseline；同意采用 FULL 配对主结果、资源差值和四个 mode 专项指标的最小集合，并删除通用 escape 与重复别名。 |
| 正式 baseline | Experiment 4 每个 `case_id × repeat_id` 单独运行一个 FULL，并由四个消融 mode 共同复用；Experiment 1 不进入正式配对。 |
| 不能复用 Exp1 的原因 | Exp1 只有 `repeat_id=0,seed=1` 且正式 replacement policy 为 `max_retries=0`；Exp4 三 repeats、`max_retries=1`，复用后 NO_REQUEUE 不再是只改变一个机制。 |
| 配对正确性主结果 | 保留 FULL/消融正确与未正确的四类转移计数；`end_to_end_success_loss_vs_full = FULL success - ablation success`，正值表示删除机制导致退化。 |
| 配对完成主结果 | `completion_loss_vs_full = FULL completion - ablation completion`，使用同一预注册 pair；实验性失败保留，identity/evidence/infrastructure 不完整时为 `null` 并 fail closed。 |
| 配对资源差值 | `wall_clock_delta_vs_full`、`token_delta_vs_full`、`cost_delta_vs_full` 均为“消融值减 FULL 值”；actual evidence 缺失时对应字段为 `null`，失败运行的实际消耗不得删除。 |
| `NO_VERIFICATION` | 保留 `wrong_canonical_acceptance_count/rate`，分母为有独立正确性标签的 invalid candidates。 |
| `NO_PARSER_POLICY` | 保留 `raw_only_exposure_count` 与 `raw_only_acceptance_count/rate`。 |
| `NO_REQUEUE` | 保留 `stuck_task_count/rate`，分母为该 mode 全部预注册 root-runs。 |
| `NO_MERGE_GATE` | 保留 `premature_merge_attempt_count` 与 `premature_merge_failure_count/rate`。 |
| 删除通用指标 | 删除跨 mode 混合不同分母的 `exposed_error_count`、`escaped_error_count`、`error_escape_rate`。 |
| 删除重复别名 | 删除 `wrong_canonical_count`、`raw_only_count`、`stuck_count`、`premature_merge_count`，只保留含义明确的正式字段。 |
| 论文资格门禁 | FULL 不得出现被关闭机制或对应违规 hook；每个消融只关闭一个机制，题目、模型、request、retry、identity 与事件链必须匹配，不另计为指标。 |
| 状态 | `design_synced`：用户已确认，唯一权威实验设计已同步；当前代码只绑定 paired FULL identity、尚未计算配对差值，旧字段与 renderer/tests 留待完整实施计划迁移。 |

#### 修改理由

消融实验的核心是同一题、同一 repeat 下只删除一个机制后的结果变化。Exp1 与 Exp4 的 repeat 和重试控制不同，不能成为 NO_REQUEUE 等模式的严格对照；通用 error escape 又混合了错误候选、卡住任务和提前合并等不同对象，没有统一分母。

#### 待同步清单

- [x] 唯一权威实验设计。
- [ ] machine-readable pair transition/delta contract、metrics/report 与 renderer。
- [ ] FULL 独立运行且四 mode 共享、Exp1 不得绑定为 paired FULL 的 identity 测试。
- [ ] 正确性/完成率四类转移、全预注册分母、实验失败保留与 infra/evidence fail-closed 测试。
- [ ] wall-clock/token/cost paired delta、usage missing/null 与失败资源保留测试。
- [ ] 四个 mode 专项分母、零分母/applicability、旧通用字段和重复别名删除测试。
- [ ] 离线 replay、smoke、论文 CSV/JSON/table 与 progress/feature/code map。

### EPD-025：Experiment 5 固定不重试并改为两张模型汇总表

| 字段 | 决定 |
|:---|:---|
| 决定日期 | 2026-07-31 |
| 用户决定 | 保持原定 Experiment 5 不重试方针；不做繁琐的模型两两比较，改为按模型汇总首次输出质量、最终 root 结果和资源消耗，正文控制在两张表内。论文后续可以把冻结表格制作成更直观的比较图，但本轮不冻结具体图形。 |
| 不重试边界 | `ProtocolConfig.max_retries=0`、`replacement_attempts_allowed=false`，每 AI unit 最多一次 provider attempt；删除 recovery、retry count、retry success 与“重试用尽后的正确率”等不适用表述。 |
| 质量主表 | 保留 `first_attempt_nonpass_rate`、互斥的首次未通过原因、`first_attempt_verification_rejection_rate`、全局 `completion_rate` 与 `end_to_end_verified_success_rate`。错误或无结果 root-run 均保留在全预注册分母中。 |
| 首次未通过率 | `first_attempt_without_verifier_accepted_candidate_count / actual_first_provider_attempt_count`；原因分为 provider/transport failure、parse/schema unusable、verification/checker rejection，不能混称模型自然错误。 |
| 首次验证拒绝率 | `first_attempt_explicitly_rejected_by_verifier_count / first_attempt_checkable_candidate_count`；只表示 verifier/checker 对实际到达验证环节的首次可判断候选的明确拒绝比例，不得命名为自然错误检出率。 |
| 资源主表 | planned first-attempt AI units、actual first provider attempts、actual/planned 调用覆盖率、actual total tokens、冻结价格快照下的 cost estimate、end-to-end wall-clock。实际调用可能因自然早停或 condition-local fail-stop 少于计划调用，必须同时呈现。 |
| 重复实验 | 三个 repeat 原始值全部保存；正文按 model 显示汇总结果及 repeat 间范围，不做 pairwise significance。 |
| 删除/降级 | 删除六个无序 model-pair 论文比较、显著性检验、胜负排名、综合评分和 `accepted_validity_rate` 等重复旧名；provider latency、domain/topic/repeat 明细、failure taxonomy 保留为解释性附录或审计数据。 |
| 论文资格门禁 | model identity、usage 完整性、固定价格、零 retry、顺序/并发与完整 evidence 链只决定结果能否入论文，不作为模型表现指标。 |
| 对 EPD-011 的影响 | EPD-011 的 cohort、请求参数、模型顺序、预算、身份和真实调用边界继续有效；其中“六个无序模型对的 paired comparisons”及相应旧论文输出要求由本决定 supersede。 |
| 状态 | `design_synced`：用户已确认，唯一权威实验设计已同步；现有 machine-readable metric contract、metrics/report/renderer、replay 和测试尚未迁移，不能把设计冻结当作实现完成。 |

#### 修改理由

Experiment 5 的目的不是证明任意两个模型之间的严格因果差异，而是在相同题目、相同计划节点数和相同协议下，比较四个 endpoint 的首次输出质量、最终 root 正确性与真实资源消耗。不重试可以避免把恢复策略混入模型 endpoint 比较；按模型汇总比六组 pairwise 更直接，也更适合论文正文。

#### 待同步清单

- [x] 唯一权威实验设计与参数决策台账。
- [ ] machine-readable metric contract、固定分母、互斥 failure taxonomy 与 missingness。
- [ ] metrics/report/renderer 删除 pairwise/recovery/旧正确率字段，并生成两张正式表及可追溯明细。
- [ ] execute/replay、CSV/JSON/TeX/PDF/SVG/Markdown 输出契约与旧 artifact 兼容策略。
- [ ] 首次调用 planned/actual、自然早停、condition fail-stop、零 retry 和三个 repeat 汇总测试。
- [ ] 完整指标追溯实施计划确认后，同步实现证据到 progress、feature 与 code map。

### EPD-026：冻结 300/50/54 正式规模并启用全量资源上界

| 字段 | 决定 |
|:---|:---|
| 决定日期 | 2026-07-31 |
| 用户原意 | 正式全量马上启动前，把实验设施补全并控制全量资源；必须保证正式题集不在运行时漂移，Exp5 继续有跨模型可比较性，同时检查磁盘与内存不会按整个 suite 线性膨胀。 |
| 机器可读权威 | `benchmarks/paper/paper_suite_scale_profile.v1.json`，profile=`paper_suite_scale_300_50_54.v1`，解析 digest=`sha256:9cab1fb5077265807e1f64c1b63265d0dd3e2f1e87a3371f1c811132e5da7dad`。 |
| Factorization corpus | 从不可变 v2 500 题 catalog 按 difficulty 内稳定 hash 固定选择 easy/medium/hard=`100/100/100`；Exp1 使用全部 300，Exp2 使用 hard 50，Exp3/4 共享 `17/17/16`。不得用 catalog prefix、运行时随机或结果后换题。 |
| Exp5 selection | `exp5_parent_quarter_selection.v4.json` 按 stratum 取不可变 v3 parent selection 的有序前缀：Factorization hard 42，Lean `pure_logic/function_set/induction` 各 4，共 54 roots/model-repeat；canonical `selection_digest=sha256:452f25dcc53a1eb0387665c6f451f1320095efb0c4bf154af62bf5e388afb6b2`，tracked 文件原始字节 `content_digest=sha256:e6c5c05e4310b385495ca3dccfcc2d7f38b71841d451726c89a1fe45200beb67`。 |
| 历史兼容 | `paper_factorization_sampling_profile.v1.json`、`exp5_hard_half_selection.v3.json`、`paper_smoke_exp5_profile.v3.json` 保持原字节只供 replay/provenance；新 run 不得加载它们冒充 active selection/profile。 |
| Exp5 smoke | active profile=`paper_smoke_exp5_profile.v4.json`，suite=`paper_smoke_exp5_v4`，canonical profile digest=`sha256:ef3b46948dee69ff640d4e21a25c3d84979045e2a8be93d0429fd9474e6b0dd6`，profile 文件原始字节 digest=`sha256:8378ce5c5a3c76339344088a36f995eda5c862059b9c9eecf5e34347985de5df`；8-item `selection_inventory` bundle digest=`sha256:eb1a7c33103fe4ef514627c8bf905de5d179e5547d98328fd17b00b62591e9b1`。最后一项不是 v4 selection digest；smoke 仍为四模型各 1 Factorization + 1 Lean 的 8-root regression-only capability smoke。 |
| 不受影响方法 | EPD-001 的 Exp1 单 repeat、EPD-003 的 Exp2 hard-only/worker/repeat、EPD-004 的 Exp3 双 repeat、EPD-005/024 的五种 Exp4 mode 与独立 FULL、EPD-011 的 cohort/request、EPD-025 的零 retry/两张模型表均继续有效；本决定只替换旧题量、selection、总量和相关预算 identity。 |
| 状态 | `verified_offline`：规模 profile/selection、runner/Exp1–5/budget/CLI/smoke loader 与 fail-closed identity 已实现；Exp3 26 + Exp4 39、Exp5 model 90、Exp5 smoke/evidence/supervision 48、authoritative CLI exact plan-only 1 项通过。未调用真实 provider、未运行全量。 |

#### 当前精确规模

| 实验 | Root-runs | First-attempt AI units | Provider-attempt upper bound |
|:---|---:|---:|---:|
| Experiment 1 | 435 | 1,970 | 1,970 |
| Experiment 2 | 600 | 12,000 | 12,000 |
| Experiment 3 | 3,726 | 17,148 | 54,372 |
| Experiment 4 | 975 | 4,410 | 7,938 |
| Experiment 5 | 648 | 4,992 | 4,992 |
| Exp1–4 | 5,736 | 35,528 | 76,280 |
| Exp1–5 | 6,384 | 40,520 | 81,272 |

Exp1–5 的精确 token ceiling=`23,503,151,360`，冻结价格快照下 cost ceiling=`7,346.259328`。这是 provider-attempt 上界预算，不是实际 usage 或实付费用；正式报告仍只使用持久化 actual usage/cost evidence。

#### 磁盘与内存口径

- 正式 disk forecast=`50,206,081,024` bytes（46.76 GiB）；最大 condition compaction reserve=`648,806,400` bytes（0.60 GiB）；再加 `max(25% forecast, 2 GiB)` 后 required=`63,406,407,680` bytes（59.05 GiB）。2026-07-31 本机 E: 只读快照 free=`471,755,141,120` bytes（439.36 GiB），足够容纳该上界；正式启动必须对实际 output volume 重新检查，不能复用该瞬时读数。
- 最大单 condition=`100 roots / 1,000 AI units / 1,000 provider attempts`。generation v3 每 root 写 delta checkpoint 后释放；terminal compaction 经临时 SQLite 流式完成；metrics 使用 lazy bundle mapping，JSONL/报告按流式/分块读取。因此峰值内存是 `O(max_single_outcome + current_bundle + fixed SQLite/chunk overhead)`，不是 `O(81,272 attempts)`。
- 合成 metrics 探针 5-run/20-run 峰值分别为 `1,675,937 / 1,744,169` bytes，且任一时刻完整 bundle live count=1；它证明当前 metrics 路径不随 suite 大小线性常驻，但不构成真实 provider 任意超大单响应的绝对 RSS 保证。正式运行仍须保留 OS 余量并监控单响应与当前 bundle 峰值。

#### 取代范围

- EPD-001 中 Factorization 500、635 roots、2,900 units 的旧规模行被本决定替换；“Exp1 两域均单次”继续有效。
- EPD-003 中 hard 166/1,992 roots/39,840 units 被 hard 50/600/12,000 替换；hard-only、20-way、worker levels 与双 repeat 继续有效。
- EPD-005/012 中 Exp3/4 旧 root/unit/attempt 总量被本决定替换；fault、death、shared-reference、ablation 方法不变。
- EPD-010 的 107-root v3 selection 被 54-root v4 selection 取代；v3 selection 只作不可变 parent/replay provenance。
- EPD-022 的“规模未决”状态被本决定关闭。EPD-025 不被取代，其 zero-retry 与两表指标决定保持有效。

#### 待同步清单

- [x] 唯一权威设计、机器可读 scale profile 与不可变 selection provenance。
- [x] Exp1–5 runner、预算、CLI、Exp5 formal/smoke loader 与 launcher identity。
- [x] 逐实验和全 suite exact plan-only、token/cost/disk forecast、最大 condition 计数。
- [x] 定向测试、progress、handoff、feature evidence 与 code map。
- [ ] 真实 API smoke 与正式全量；仍按付费授权和新 output identity 单独执行。

### EPD-027：Experiment 2–4 改为两阶段真实回答库，并冻结两类在线检查

| 字段 | 决定 |
|:---|:---|
| 决定日期 | 2026-08-01 |
| 用户原意 | 接受 response-bank 修正版的其他建议；Experiment 2 在缩小题集上真实运行全部六个 worker 档位，Experiment 3 保留小型在线恢复检查；同时明确这不是一次普通修复，而是实验设施的全面修改。 |
| 总体方法 | Experiment 1/5 保持逐 unit 真实 API。Experiment 2–4 先建立不可变真实回答库，再由 trace-backed executor 通过正常 TokenShare 状态机消费；fault hook、verifier/checker、lease、worker death、requeue、merge、settlement 与 event ledger 均真实运行。 |
| 稳定 bank identity | 不使用带 attempt/lease/fencing/time/condition 的完整 `ExecutionRequest` digest。新建 `inference_request_digest`，绑定 provider 实际请求正文、endpoint/model、有效 controls、prompt/plugin version、独立 sample/repeat slot 与 replacement slot。 |
| 配对与重复 | 同一 `case_id × repeat_id` 内的比较条件共享真实回答；不同 repeat 使用独立 sample slot。replacement 按当前冻结最大恢复深度准备，不能只备一个。缺 entry 立即 blocked，不临时调用、scripted 回退或缩短 retry。 |
| Experiment 2 在线检查 | 在缩小且预注册的题集上，用真实 API 完整覆盖 worker=`1,3,7,10,30,50`。用途是检查在线排队、限流、超时和扩展趋势是否与 trace 主矩阵严重背离；不是把 600-root-run 主矩阵改写成全量在线结果。题集大小、重复数、阈值和预算在实施计划中冻结。 |
| Experiment 3 在线检查 | 保留小型真实 API 恢复检查，至少覆盖 verifier/checker 拒绝后 replacement 与 worker death 后重新分派。必须证明 fault/death 之后才建立新 attempt、发起新 provider call，并保存独立 raw/provenance/usage。 |
| 指标边界 | Experiment 2–4 的正确率、完成率与机制指标继续从完整状态机产生；主矩阵时间/资源改为 trace-replay/trace-attributed 口径。Experiment 3 主矩阵用 `discarded_trace_tokens`；`wasted_actual_tokens` 只用于在线恢复检查的 actual 重跑样本。Experiment 1/5 指标原义不变。 |
| 论文主张 | Experiment 2–4 必须称为“基于不可变真实模型 trace 的协议扩展性/恢复/消融”，不得声称每个条件都重新在线调用 provider。Experiment 2 在线检查和 Experiment 3 在线检查单独标为 `online_real_provider`。 |
| 调用量与预算 | EPD-026 的 76,280 保留为旧全在线上界/最大 trace-slot capacity，不再代表预计在线调用量。当前审计量级约为 6,904 次 DeepSeek acquisition 加在线检查，正式数字必须由完整 outbound-body bank inventory 给出；Experiment 5 SiliconFlow 预算单列。人民币 1,000 为 DeepSeek acquisition/在线检查目标硬停止线，须同时约束 calls/tokens/CNY 并为 in-flight 悲观预留。 |
| 设施影响 | 必须跨 request identity、executor/transport、artifact schema、paper eligibility、budget、runner、metrics/report/renderer、smoke/canary、replay/audit 全面改造；现有 stored-evidence replay 不能替代 trace-backed state-machine run。 |
| 状态 | `design_synced`：用户已确认方法和两类在线检查，唯一权威设计及 harness 状态已同步；未实现、未写完整实施计划、未运行真实 API。 |

#### 修改理由

回答库能把大量重复付费调用收敛为有限的真实模型样本，并让相同 repeat 内的条件使用相同回答，从而减少“不同条件恰好得到不同模型答案”的干扰。但预取回答不能证明故障后现场重调 API，本地回放也不能复现真实 provider 的并发排队与限流。因此 Exp2 用缩小题集的六档在线检查约束外部并发偏差，Exp3 用小型在线恢复检查保留“故障后确实重新调用 AI”的关键真实性证据。

#### 对既有决定的取代范围

- EPD-019 中“正式 Exp3 全矩阵每个 replacement 都是故障后的真实在线 provider attempt”由本决定取代。其证据目的保留在小型在线恢复检查；主矩阵改用 `discarded_trace_tokens`，不能继续叫 `wasted_actual_tokens`。
- EPD-020 中 Experiment 2 的 `wall_clock_ms`、`paired_speedup`、actual provider attempts/tokens/cost 与 `parallel_efficiency` 的全矩阵在线含义由 trace-replay/trace-attributed 版本取代；正确率、完成率、调度、并发和利用率指标继续有效。六档真实 API 在线检查单独报告实际资源和 provider 干扰。
- EPD-021/023 中 Experiment 3 相对 shared Exp1 的 actual wall-clock/token/cost overhead 改为同 sample slot 的 paired trace reference；在线检查的 actual spend 单列。
- EPD-024 中 Experiment 4 的 FULL 独立协议运行及四 mode 配对结构不变，但同一 pair 共享 source trace；资源差值改为 trace-replay/trace-attributed，不能称为各 mode 当次实际在线支出。
- EPD-026 的题量、root-run、first-attempt unit、fault/mode/repeat 与 selection 不变；`provider-attempt upper bound` 只保留为旧全在线上界和 trace-slot capacity。新的 acquisition/canary 数量与 budget identity 必须重新生成。

#### 待同步清单

- [x] 唯一权威实验设计、参数决定台账、progress/feature/handoff 与 code map 的待实施边界。
- [ ] 完整实施计划：从冻结指标反推 direct result、bank/source/consumer evidence、组件、迁移顺序、RED→GREEN 测试和一次性验收。
- [ ] stable provider-body digest、bank schema/inventory、sample/replacement slot 与 fail-closed completeness。
- [ ] trace-backed executor、新 submission/source provenance、完整状态机与双 evidence-class paper eligibility。
- [ ] acquisition actual spend、trace attribution、人民币 1,000 calls/tokens/CNY/in-flight 硬门。
- [ ] Experiment 2 六档在线检查与 Experiment 3 在线恢复检查的题集、重复、阈值、预算和 profile identity。
- [ ] metrics/report/renderer/replay/audit、smoke/canary、plan-only、Fast/Full 与真实 API 验证。

## 4. 后续新增决定模板

复制以下模板并递增编号：

```markdown
### EPD-XXX：决定标题

| 字段 | 决定 |
|:---|:---|
| 决定日期 | YYYY-MM-DD |
| 用户原意 | 简要引用或准确转述 |
| 适用实验 | Experiment N / domain / condition |
| 原参数 | ... |
| 新参数 | ... |
| 不受影响范围 | ... |
| 状态 | discussing / user_confirmed / design_synced / implemented / verified |

#### 修改理由

...

#### 规模与论文口径影响

...

#### 待同步清单

- [ ] 权威设计
- [ ] runner / budget / report
- [ ] tests
- [ ] plan-only identity / digest
- [ ] progress / feature / code map
```

## 5. 正式运行前检查

每次生成正式 plan-only 前，agent 必须：

1. 阅读本文所有未被 `superseded` 的决定。
2. 确认所有 `user_confirmed` 决定已经进入唯一权威实验设计。
3. 确认计划涉及的决定已经达到 `implemented` 和 `verified`。
4. 重新生成 plan-only；旧 condition、selection、budget digest 不得跨参数修改复用。
5. 在回答用户“会跑多少题、多少请求、多少 token”时，明确区分 root、AI unit、首轮 provider call、replacement call、预算上限和实际 usage。
