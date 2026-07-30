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
| 模型和请求参数 | 保持 SiliconFlow `zai-org/GLM-5.2` / `glm_5_2_exp1_baseline`、`temperature=0.0`、`enable_thinking=false`，不修改。 |
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

详细文件级修复清单见唯一权威实验设计的“2026-07-24 Exp2 实现与指标接线审计”。

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

详细文件级修复清单见唯一权威实验设计的“2026-07-24 Exp3 实现与指标接线审计”。

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
| 状态 | `verified`：hard-only selection、公平 controls、persisted v2 observed identity、strict formal join、endpoint comparison CSV、预算/CLI/capturing 均已实现并验证。 |

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
| 状态 | `verified`：共享正式常量、Exp1–4/CLI/Gate C、tracked JSON 和 Exp5 fail-closed 已接入；timeout 直接影响集 `218 passed`，最终 Fast `331 passed, 1 skipped in 14.83s`。 |

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
| 状态 | `verified_offline`：代码、v2 tracked configs、TDD 与文档已接入；定向 `369 passed`、Fast `346 passed/1 skipped`、Full `1420 passed/1 skipped`。未调用真实 provider。 |

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
| 状态 | `verified_offline`：selection artifact、runner、预算、profile、drift gate 和定向测试均已实现；semantic selection digest=`sha256:fbec153a02befa4071b4ad4d639e51e910a3bc4c433cc9eea4b19ac6489a3490`。真实 smoke 仍 hold，不得启动正式 Exp5。 |

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
- [ ] Exp5 专属 selection artifact、runner 常量和 condition expansion。
- [ ] budget / P0-full 总量 / plan-only identity / digest。
- [ ] smoke profile 与 catalog/selection drift gates。
- [ ] metrics/report 的样本量与 paired comparison metadata。
- [ ] tests、progress、feature、handoff 和 code map；实现后运行定向验证。

### EPD-011：Experiment 5 cohort v3 请求参数、论文输出与实施边界

| 字段 | 决定 |
|:---|:---|
| 决定日期 | 2026-07-29 |
| 用户原意 | 用户在审阅四模型、半量题库、参数和论文输出设计后明确要求“直接实施”，由主线程监督 `gpt-5.6-sol` 超高强度子线程分步执行；目标是让 Experiment 5 能跑通，不跑全量测试，不处理人为注入攻击。 |
| 适用实验 | 下一版 Experiment 5 SiliconFlow 四模型 cohort v3；不覆盖 Exp1–4 或 Exp5 v1/v2 replay。 |
| 公共请求参数 | `timeout_seconds=600`、`max_tokens=32768`、`temperature=0.0`、`top_p=1.0`、`stream=false`、`max_provider_attempts=1`、SiliconFlow 全局 in-flight=`3`。 |
| reasoning profile | GLM-5.2、Qwen3-14B 与 MiniMax-M2.5：`enable_thinking=true, thinking_budget=32768`；Pro/DeepSeek-V3：`enable_thinking=false` 且不发送 `thinking_budget`。MiniMax 修订依据是实际请求即使显式发送 `enable_thinking=false` 仍返回 reasoning usage。 |
| 调度 | 模型 arm 顺序执行，3 repeats 使用预注册 3×4 部分平衡顺序；condition worker capacity 与共享 SiliconFlow provider gate 均为 3。 |
| 论文强制输出 | 审计 CSV/JSONL、overall/by-domain-topic 表、六模型对 paired comparisons、order/concurrency 与 failure taxonomy、LaTeX 表、PDF/SVG 图、数据派生结果摘要和失败附录。 |
| 预算与 pricing | token plan 按 entry 计算，当前保守 ceiling=`607,518,720`；正式 config 必须冻结可审计的 SiliconFlow official pricing snapshot，缺失则正式货币预算/preflight blocked，不猜价格。 |
| 验证边界 | 只运行 Exp5/provider/metrics/report/CLI 定向 pytest 与必要 compile/Fast；不运行全量 pytest、`init.ps1 -Full`、LeanAudit 或正式矩阵。 |
| 排除范围 | 不新增、修复或扩展人为注入攻击、对抗性输入或安全工程；只保持既有 secret 不落盘与正常实验正确性门禁。 |
| 真实调用边界 | 本决定批准代码与离线定向测试，不自动批准真实付费 API。四 entry capability smoke、8-root smoke 与 1,284-root 正式矩阵仍分别需要运行前明确授权。 |
| 状态 | `verified_offline`：v3 离线设施已实现，最终 Exp5 定向集合=`464 passed in 231.56s`，本次 bootstrap/key/MiniMax 修订影响集=`227 passed`；Task 7/8/9 独立 review 均 PASS。五次最小 endpoint/key 诊断不是正式 evidence；8-root smoke 未运行，正式 preflight blocked。 |

#### 待同步清单

- [x] v3 设计与实施计划。
- [x] 参数决策台账、权威设计和导航。
- [x] transport / identity / usage。
- [x] cohort / config / selection / policy / runner / budget。
- [x] metrics / statistics / paper artifacts / smoke profile。
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
| Exp3–4-only smoke direct/actual roots | 11 / 11 |

condition、selection、profile、execution-plan 和 budget identity 均随本决定重新生成；execution-plan/budget digest 继续绑定具体 `output_root`。旧 digest 和 run01–run04 evidence 只读保留，不重算、不升级。

#### 待同步清单

- [x] 权威实验设计和本参数台账。
- [x] Exp3 canonical plan、FormalEvidenceStore、runner、budget、metrics/report。
- [x] 11-root profile、CLI identity、v3 launcher 与离线 identity-only。
- [x] RED/GREEN 定向测试与 Fast。
- [x] code maps、导航、feature/progress/handoff。

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
