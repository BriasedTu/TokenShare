# feat-011 并发实验 Prompt Pack

> 日期：2026-07-18
> 当前结论：Task 14 机械契约通过，语义审核失败；允许并发开发，禁止正式预算和 provider 调用
> 权威口径：Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md
> 主计划：Doc/TechnicalDocument/2026-07-13-feat-011-paper-real-ai-experiments-implementation-plan.md
> 并发设计：Doc/TechnicalDocument/2026-07-18-feat-011-parallel-experiment-development-design.md

## 0. 使用顺序

当前可以立刻并发启动两个工作包：

1. Prompt A：Task 14 semantic repair。
2. Prompt B：Gate B 公共开发契约冻结。它只能新增自己的两个文件，不能接 shared runner。

Gate B 通过并形成 checkpoint 后，可并发启动 Prompt C–G：

- Prompt C：Experiment 1 formal module。
- Prompt D：Experiment 2 scalability module。
- Prompt E：Experiment 3 fault/recovery module。
- Prompt F：Experiment 4 ablation module。
- Prompt G：Experiment 5 endpoint comparison module。

五个模块与 Task 14 semantic repair 都通过审核后，串行执行 Prompt H 集成。之后按 Prompt I–M 的顺序逐个完成 plan-only、预算批准、pilot 和正式实验。最后执行 Prompt N 联合审计。

如果并发槽不足，优先顺序为 A + B，然后 C/D/E，再 F/G。任何并发开发 prompt 都不授权真实 API 调用。

## 1. 所有开发 agent 的共同规则

- 工作目录必须是仓库根目录 E:/TokenEcnomic/TokenShare。
- 完整阅读 AGENTS.md、Doc/agent-navigation.md、权威实验设计、主计划、并发设计和本 prompt pack。
- 先运行 powershell -ExecutionPolicy Bypass -File ./init.ps1；若失败，先判断是否为本分支基线问题，不得盲目继续。
- 当前仓库可能存在其他 agent 的改动。不得 reset、clean、checkout 或覆盖其他工作包；先查看 git status 和 diff。
- 只修改 prompt 明确列出的 owned files。发现必须修改 shared file 时停止并报告 integration owner，不自行越界。
- 使用 TDD：先写能证明缺口的 RED，再做最小 GREEN。完成声明前运行目标测试、影响测试和完整 init.ps1。
- 不新增生产安全、恶意攻击防护、网络服务或协议 core 重构。保留现有 secret 不落盘和 replay 零调用约束即可。
- scripted/fake 只用于开发测试，必须保持 paper_eligible=false。
- 开发阶段不得审批预算、不得调用真实 provider、不得生成论文正式结果。
- 并行模块 agent 不更新 feature_list.json、progress.md、session-handoff.md、共享 code map 或主实施计划；这些由 integration owner 统一更新。
- 完成后报告：owned files、RED/GREEN 证据、完整验证、残余 blocker、commit hash。只提交本工作包文件，不 push、不 merge。

## Prompt A：下一步，Task 14 semantic repair

    继续 feat-011，但只执行 Task 14 semantic repair。不得进入 Task 15，不得批准预算，不得调用真实 AI API。

    当前机械契约已经是 9 个 cells × 每格 15 个 checker-backed selected case IDs、合计 135、0 blocked、expected_ai_unit_count=480、provider_calls_made=0。不要回退这些正确部分。但审核已经证明当前 catalog 不满足论文实验有效性：

    - 八个 v2 passed cells 每格 15 个 ID 实际只有一个 root theorem / lemma-DAG 语义形状。
    - construction_seed 只改变 ID、theorem name、library context 和 package ID，没有进入 theorem/DAG 构造。
    - 三个 hard_frontier checker pools 分别是对应 medium_lemma_dag 模板的重命名副本，depth/leaf/AI units、statements、edges 和 oracle theorem 相同。
    - hard/function_set 与 hard/induction 没有逐格端到端 golden split → proof assembly → merge → root recheck 证据。
    - 当前测试只验证 ID/count/checker status，无法捕获语义重复和难度标签伪升级。

    先写 RED，不要先改 catalog：

    1. 在 tests/experiments/test_lean_lemma_graph_catalog.py 和 tests/experiments/test_lean_task14_readiness.py 增加 canonical semantic fingerprint。fingerprint 必须忽略 case_id、construction_seed、theorem/node 名称、library_context.case_id、oracle package_id 等纯身份字段；必须纳入 root parameters_source/statement_source、node statements/kinds/depths、dependency edges、merge shape、expected depth/leaf/AI units，以及区分 oracle theorem 语义所需的字段。
    2. selected 的每个 cell 必须有 15 个不同 root fingerprints。允许共享一个经过预注册的 construction rule family，但不能只是 alpha-renaming、改 ID 或改 theorem name。
    3. hard_frontier 与同 topic 的 medium_lemma_dag fingerprint 集合必须不相交。hard 必须真实体现权威设计要求中的更深 DAG、nested induction、rewrite/theorem reuse、case split、quantifier/extensionality 或跨主题组合之一；不得仅把 difficulty 和 construction_rule_id 改成 hard。
    4. 九格各冻结 1–2 个明确 golden case IDs，并逐个执行 deterministic split、child/node proof construction、checker、dependency-aware merge、root recheck。能力状态不能因存在 passed row 自动写 ready。
    5. 对 semantic duplicate、hard-is-medium、缺 golden evidence 分别产生稳定失败信息。

    GREEN 实现要求：

    - 修改 benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl，并按需要修改 fixtures/lean_proof_project/TokenShare 下的独立 theorem/oracle modules、Lean plugin 的确定性规则或 experiment adapter。AI 仍不得决定协议级拆分。
    - 九格每格恰好 15 个语义不同、checker-backed、preflight-passed roots。simple 也必须是 15 道不同题，不能把三种 shallow shape 克隆成 15 个 ID。
    - medium 三格应覆盖各 topic 的多个预注册 theorem families；hard 三格必须有独立 oracle package/theorems，且复杂度与结构明显高于 medium。
    - 不得把 structured_blocked row、no-oracle row 或其他 cell 的 shallow row补入 selected 15。
    - 重新生成 benchmarks/paper/lean_task14_3x3_readiness.v1.json；旧 catalog/matrix/selection/oracle/AI-unit digests 全部视为失效并重算。
    - Task 15 budget input 只引用新的 exact 135 selected IDs；blocked/extra pool rows 不计入。
    - provider_calls_made 必须保持 0。

    Owned files：

    - benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl
    - benchmarks/paper/lean_task14_3x3_readiness.v1.json
    - fixtures/lean_proof_project/TokenShare 下为新 theorem shapes 必需的文件
    - src/tokenshare/experiments/paper_catalog.py
    - src/tokenshare/experiments/lean_paper_adapter.py
    - src/tokenshare/experiments/paper_runner.py 中仅 Task 14 readiness/fingerprint 部分
    - 必要时 src/tokenshare/plugins/lean_proof 中与新确定性 shape 直接相关的最小文件
    - 对应 Lean catalog/adapter/plugin/readiness tests

    不得修改 Exp1–5 新模块、shared contract 模块、CLI、metrics/report、状态文档。完成后先请求独立 code review；Critical/Important 全部关闭后再由 integration owner 更新状态。

    验证至少包括：

    - tests/experiments/test_lean_lemma_graph_catalog.py
    - tests/experiments/test_lean_task14_readiness.py
    - tests/experiments/test_paper_catalog.py
    - tests/experiments/test_lean_paper_adapter.py
    - tests/plugins/lean_proof
    - tests/experiments/test_paper_budget.py
    - tests/experiments/test_run_paper_experiments_cli.py
    - powershell -ExecutionPolicy Bypass -File ./init.ps1

    最终停在 Task 15 前。若任一 cell 无法达到 15 个语义不同 checker-backed roots，明确保持 semantic_blocked，不得标完成。

## Prompt B：Gate B 公共开发契约冻结

    继续 feat-011，只执行 Gate B 公共开发契约冻结。该工作包可与 Task 14 semantic repair 并行，但必须完全避免文件冲突。不得接入 shared runner/CLI，不得调用 provider。

    目标是新增一个薄层 contracts module，让 Exp1–5 各自模块可以独立开发，同时不复制 provider、artifact、checker、budget 或 report 系统。

    只允许创建：

    - src/tokenshare/experiments/paper_experiment_contracts.py
    - tests/experiments/test_paper_experiment_contracts.py

    不得修改 __init__.py、paper_models.py、paper_runner.py、CLI、metrics/report、状态文档或 catalog。

    使用 TDD 冻结以下最小接口：

    1. FrozenCaseSelection：schema_version、selection_id、experiment_id、domain、paper_difficulty、topic_family、ordered_case_ids、catalog_digest、suite/catalog version、selection_digest、expected_ai_unit_count、paper_eligible_required、blocked_reason。构造时拒绝空 ID、重复 ID、顺序不稳定、非法 digest、executable 与 blocked 状态矛盾。
    2. PaperExecutionContext：只保存对现有 catalog、approved endpoint binding、request limits、hard limits、output root、artifact/event stores 和 injected execution callback 的引用或稳定 ID；不得创建第二套 transport/store/checker。
    3. PaperConditionResult 与 ExperimentSummaryRows 的最小跨模块返回协议；正式 dataclass 若已存在则引用，不复制。
    4. PaperExperimentModule Protocol，固定四个入口：
       expand_conditions(context)
       freeze_case_selections(context, conditions)
       run_condition(context, condition, selection)
       summarize(evidence)
    5. canonical digest helper 必须对 ordered IDs 和冻结 metadata 敏感，并对 dict 插入顺序不敏感。
    6. structured-blocked selection 必须能在零 provider call 下表达；不存在“缺正式 catalog 就自动用 synthetic case”的生产 fallback。

    测试需覆盖 digest drift、重复/乱序 IDs、blocked/executable 矛盾、module Protocol conformance、serialization round-trip 和 scripted fixture 仍 paper-ineligible。

    运行目标测试、tests/experiments/test_paper_models.py、tests/experiments/test_paper_budget.py 和完整 init.ps1。只提交上述两个文件，给 integration owner 返回 commit hash。

## Prompt C：Experiment 1 formal module

    基于已冻结的 Gate B contracts，只开发 Experiment 1 独立模块。不得修改 shared runner/CLI/metrics/report/status，不得调用 provider。

    Owned files：

    - src/tokenshare/experiments/paper_exp1.py
    - tests/experiments/test_paper_exp1_formal.py

    实现 Gate B 的四个入口。冻结正式矩阵：

    - Factorization：easy/medium/hard 各 10 roots，共 30。
    - Lean：simple/medium_lemma_dag/hard_frontier × pure_logic/function_set/induction，每格 15，共 135。
    - 合计 165 unique roots，3 repeats，495 root-runs。
    - worker_count=10；model 固定 SiliconFlow zai-org/GLM-5.2、entry glm_5_2_exp1_baseline、temperature=0.0、enable_thinking=false。
    - fault=none，ablation=FULL。

    模块必须消费 FrozenCaseSelection，不得自己重抽样。Lean semantic readiness 未通过时，formal expansion 必须在 provider 前 structured blocked；synthetic fixture 只用于 tests。

    summary 输入/输出至少覆盖 per domain/paper_difficulty/topic_family 的 case_count、completion、accepted_validity、wall-clock median/P90、tokens median/P90、cost per completed task、failure breakdown，以及 highest_observed_valid_completion_difficulty。不要在模块内写 CSV；返回规范 rows 供 integration owner 汇总。

    RED 覆盖 165/495 算术、缺任一 Lean cell、重复 root、model drift、repeat/order drift、pilot/formal 混入、scripted evidence paper eligibility。GREEN 后运行 owned tests、contracts tests、paper budget/models impact tests和完整 init.ps1。只提交 owned files。

## Prompt D：Experiment 2 scalability module

    基于 Gate B contracts，只开发 Experiment 2 worker scalability 独立模块。不得修改 shared runner/CLI/metrics/report/status，不得调用 provider。

    Owned files：

    - src/tokenshare/experiments/paper_exp2_scalability.py
    - tests/experiments/test_paper_exp2_scalability.py

    冻结：

    - 强制 worker levels 1、3、10、30；100、300 只有 AI-unit 数、quota 和真实 worker preflight 都满足时才支持，否则输出 unsupported_worker_level。
    - 2 domains × 3 difficulties × 5-task batch × 4 worker levels × 5 repeats = 600 root-runs。
    - Lean 5-task slice：simple 2/2/1、medium 1/2/2、hard 2/1/2，具体 task IDs 和 selection digest 在所有 worker/repeat 间完全相同。
    - Factorization 必须测同一 root 内 range children 并行，不得把多个独立整数冒充同一 root scaling。
    - Exp1–4 baseline model identity固定为 GLM-5.2 entry，不随 worker level 改变。

    实现 critical path 从 task/attempt timestamps 和 dependency/merge gates 计算；speedup=T1/Tw，efficiency=speedup/w，throughput=completed roots/wall-clock seconds。provider_latency_sum 不能代替 wall-clock 或 critical path。汇总还需 total tokens、cost、completion、429、retry，以及含限流和排除限流 run 的敏感性标记。

    RED 覆盖 batch ID漂移、worker level 重新抽样、错误 critical path、100/300 伪支持、模型漂移、root-run 算术和 scripted paper eligibility。GREEN 后运行 owned tests、contracts tests、worker harness/runner impact tests 和完整 init.ps1。只提交 owned files。

## Prompt E：Experiment 3 fault/recovery module

    基于 Gate B contracts，只开发 Experiment 3 rate-fault 与 worker-death 独立模块。复用现有 paper_faults.py 和 paper_workers.py 公共 primitive，不得修改它们；需要扩展时停止并交 integration owner。不得调用 provider。

    Owned files：

    - src/tokenshare/experiments/paper_exp3_fault_recovery.py
    - tests/experiments/test_paper_exp3_fault_recovery.py

    Rate-fault 冻结：

    - fault types：false_positive、false_negative、no_return、late_submission、executor_error。
    - Factorization rates：0、1、5、10、25、50、100 percent；固定 5 tasks。
    - Lean rates：0、10、50、100 percent；固定 3 tasks，pure_logic/function_set/induction 各 1。
    - 每 condition 3 repeats。
    - root-runs：Factorization 525，Lean 180，合计 705。
    - target IDs 由固定 seed 从 AI units 选择并写 manifest；不同 fault/rate/repeat 不得重抽 task slice。

    Worker-death 冻结：

    - worker_count=10，dead_worker_count target 为 1 和 3，kill progress target 为 25/50/75 percent。
    - 2 domains × 3 tasks × 2 death counts × 3 positions × 3 repeats = 108 root-runs。
    - coordinator 必须继续；实际终止的是独立 executor worker process。
    - actual dead count 不等于 target 时 run failed，不能并入该 condition。

    所有首次/恢复 attempts 都固定 GLM-5.2 entry，不得模型升级/failover。故障只能在真实 raw/provenance 持久化之后的权威 injection point 生效；模块测试用 scripted evidence 模拟这一时序。

    输出结构至少包含 original/mutated refs、matched_baseline_condition_id、detection/false-accept/recovery/completion、recovery latency、retry/reassignment、wasted actual tokens、wall-clock/token/cost overhead；worker death 另含 actual dead count、target/actual kill progress、coordinator_continued、required/recovered slots、completeness、root_output_complete、accepted_validity。零 baseline 分母返回 null + zero_baseline_denominator，不得产生 NaN/Infinity。synthetic mutation 不得伪造 tokens。

    RED 覆盖 705/108 算术、fault injection 早于 raw persistence、baseline 不匹配、actual dead count 不符、错误分母、model failover、slice drift 和 scripted eligibility。GREEN 后运行 owned tests、contracts、现有 fault/worker tests和完整 init.ps1。只提交 owned files。

## Prompt F：Experiment 4 ablation module

    基于 Gate B contracts，只开发 Experiment 4 ablation 独立模块。复用现有 paper_ablation.py，不修改 protocol core 或默认 FULL 语义。不得修改 shared runner/CLI/metrics/report/status，不得调用 provider。

    Owned files：

    - src/tokenshare/experiments/paper_exp4_ablation_runner.py
    - tests/experiments/test_paper_exp4_ablation_runner.py

    六个模式固定为 FULL、NO_VERIFICATION、NO_PARSER_POLICY、NO_REQUEUE、NO_MERGE_GATE、NO_SLOT_INTEGRITY。每个 domain、每个 difficulty 固定 5 tasks；Lean 使用与 Exp2/5 完全相同的 2/2/1 exact slices；6 modes × 3 repeats，共 540 root-runs。所有模式固定 GLM-5.2 entry。

    每个 mode 使用独立 output root；模块返回 mode-specific wrapper/config，不修改协议默认配置。summary 至少覆盖 completion、accepted validity、wrong canonical acceptance、raw-only acceptance、stuck task、premature merge、slot mismatch、wall-clock、tokens、cost。

    明确实现：

    - exposed_error_count：到达被关闭机制且 FULL 本应拒绝/隔离的对象数。
    - escaped_error_count：其中继续进入 canonical/merge 或错误 terminal success 的对象数。
    - error_escape_rate=escaped/exposed。
    - 不适用或 exposed=0 时 rate=null，applicability 分别为 not_applicable 或 zero_denominator；不得用 0 伪装。

    RED 覆盖 mode 之间 output root 污染、FULL 默认语义被改、slice/model drift、540 算术、错误 denominator、NO_REQUEUE 被伪报 0 escape、scripted eligibility。GREEN 后运行 owned tests、contracts、现有 ablation tests和完整 init.ps1。只提交 owned files。

## Prompt G：Experiment 5 endpoint comparison module

    基于 Gate B contracts，只开发 Experiment 5 model-provider endpoint comparison 独立模块。复用现有 fixed-entry identity/cohort/preflight helpers，不修改通用 AIAPIExecutor、Exp1–4 baseline policy 或 shared runner。不得调用 provider。

    Owned files：

    - src/tokenshare/experiments/paper_exp5_model_comparison.py
    - tests/experiments/test_paper_exp5_model_comparison.py

    cohort 固定：

    - glm_5_2_siliconflow：SiliconFlow，zai-org/GLM-5.2。
    - qwen3_6_27b_siliconflow：SiliconFlow，Qwen/Qwen3.6-27B。
    - gpt_5_6_sol_high_openai：OpenAI，gpt-5.6-sol，reasoning_effort=high。

    不使用 strong/weak/mixed 标签。每个 domain/difficulty 固定 5 tasks；Lean 与 Exp2/4 复用完全相同 2/2/1 task IDs；3 endpoints × 3 repeats，共 270 root-runs。首次/恢复链保持同一 fixed entry，不得失败后换 endpoint。

    formal expansion 必须要求完整三成员 cohort、provider_config_id + selected_entry_id、cohort digest、source config digest、endpoint identity digest 和 reasoning controls。缺任一 member 时整个 formal Exp5 structured blocked，reason=incomplete_model_cohort，attempt count=0；单成员只能 pilot_only。

    返回 model_execution_records join 所需字段：condition/task/unit/attempt identity、configured/requested/resolved model、response model status、source/prepared config digests、reasoning controls、latency/tokens/cost、provider errors、paper eligibility。resolved model 只能来自持久化 raw response，缺失或 mismatch 必须保留稳定 failure reason，不得从 config/request 回填。

    RED 覆盖 270 算术、cohort 不完整、entry namespace 冲突、reasoning drift、source config drift、resolved model 缺失/mismatch、endpoint failover、slice drift、v1 artifact 保守读取和 scripted eligibility。GREEN 后运行 owned tests、contracts、model identity/policy/executor transport impact tests和完整 init.ps1。只提交 owned files。

## Prompt H：Gate C 串行集成

    作为唯一 integration owner，串行集成 Task 14 semantic repair、Gate B contracts 和 Exp1–5 五个模块。不得调用真实 provider；本任务只完成代码集成、plan-only 和离线验证。

    开始前读取每个模块的 review 结论和 commit，确认无未关闭 Critical/Important。按 C、D、E、F、G 顺序逐个合并；每合并一个模块就运行该模块 tests + contracts tests。不要一次性解决所有冲突。

    integration owner 独占 shared files：

    - src/tokenshare/experiments/__init__.py
    - src/tokenshare/experiments/paper_models.py
    - src/tokenshare/experiments/paper_runner.py
    - src/tokenshare/experiments/run_paper_experiments.py
    - src/tokenshare/experiments/paper_budget.py
    - src/tokenshare/experiments/paper_metrics.py
    - src/tokenshare/experiments/paper_report.py
    - shared integration tests、code maps、feature_list.json、progress.md、session-handoff.md

    集成目标：

    1. shared dispatcher 通过统一 Protocol 加载五个模块，不复制 condition expansion。
    2. CLI 支持逐实验 plan-only 和隔离 output root；不完整 catalog、cohort、budget 或 identity 全在 provider 前 blocked。
    3. exact case selections 在 worker/mode/model/repeat 间不漂移；Exp2/4/5 共用同一 Lean 2/2/1 IDs，Exp3 使用每 topic 1 道。
    4. Exp1–4 始终绑定 GLM-5.2 baseline；Exp5 始终是完整三端点 fixed-entry cohort。
    5. budget digest 覆盖完整 conditions、exact selections、AI-unit commitments、endpoint identity、request/hard limits。
    6. resume/replay 不重新调用 provider；各实验 output 不互相污染。
    7. metrics/report 从 per-task/per-attempt/event/artifact evidence 派生专项 rows，不硬编码结果。
    8. scripted/fake、blocked、pilot、formal、budget-exhausted 数据严格隔离。

    若 Task 14 semantic repair 尚未通过，可以完成代码集成，但正式 Lean selections、formal budget 和 provider path 必须保持 semantic_blocked。不得沿用旧 480-AI-unit digest。

    增加跨实验 contract/integration tests，运行 tests/experiments、相关 executor/factorization/Lean plugin suites、compileall 和完整 init.ps1。然后更新中文状态文档和 code map，记录仍未调用 provider。请求独立 review，通过后形成 reviewed integration checkpoint。

## Prompt I：Experiment 1 plan、批准、pilot 与 formal run

    只执行 Experiment 1。开始前必须确认 Task 14 semantic repair、Gate C、完整 init.ps1 和独立 review 均通过；当前 catalog/selection digests 必须是修复后的新值。

    第一阶段只运行 plan-only：

    - 冻结 30 Factorization + 135 Lean = 165 unique roots，3 repeats = 495 root-runs。
    - 验证 Lean 九格每格 15 个语义不同、checker-backed roots。
    - 固定 worker_count、GLM-5.2 entry、request profile、seed/order、timeout 和 hard limits。
    - 展开 provider-attempt/token/cost/time/disk 上界，生成 exact budget digest。
    - provider_calls_made 必须为 0。

    输出预算摘要和 digest 后停止，向用户请求对该 exact digest 的明确批准。未批准不得 smoke/pilot/formal。

    获得同一任务中的明确批准后：

    1. 运行最小真实 pilot，pilot_only=true，不进入主表。
    2. 审计 raw/provenance/usage/artifact/event、checker/verifier、resume/replay 零调用和报告行。
    3. pilot 暴露问题时只修直接问题；任何 catalog/config/condition/limit drift 都使旧批准失效，重新 plan并再次请求批准。
    4. 在 hard limits 内运行正式 Exp1。
    5. 生成 feasibility rows、difficulty boundary、失败示例和完整 audit。
    6. 运行验证并更新状态，停在 Experiment 2 run 前。

## Prompt J：Experiment 2 plan、批准、pilot 与 formal run

    只执行 Experiment 2，并要求 Experiment 1 已完成审计。先 plan-only 冻结 600 root-runs、worker levels 1/3/10/30、exact 5-task batches、5 repeats、GLM-5.2 identity、request/hard limits和预算 digest。100/300 只做 preflight，条件不满足就输出 unsupported_worker_level。provider_calls_made=0 后停止请求用户批准。

    批准后先跑小 pilot，验证真实 worker 并发、同一 root child parallelism、timestamps/dependency critical path、429 记录、output isolation和resume/replay。drift 时重新批准。正式运行后输出 wall-clock、critical path、throughput、speedup、efficiency、tokens/cost、completion、429/retry和限流敏感性分析。验证并更新状态，停在 Experiment 3 run 前。

## Prompt K：Experiment 3 plan、批准、pilot 与 formal run

    只执行 Experiment 3，并要求 Experiment 2 已完成审计。先 plan-only 冻结 rate-fault 705 root-runs、worker-death 108 root-runs、exact task/AI-unit target IDs、fault rates、death counts、kill positions、matched baselines、GLM-5.2 identity、hard limits和预算 digest。provider_calls_made=0 后停止请求用户批准。

    批准后 pilot 必须证明 raw output 先落盘再注入、原始/变异 refs 可追踪、replacement attempt 真实调用、独立 worker process 可实际终止且 coordinator 继续。drift 时重新批准。正式运行后输出 detection/false accept/recovery/completion、matched wall-clock/token/cost overhead、actual dead count、target/actual kill progress、reassignment、slot/root completeness、accepted validity，并保留至少一个可恢复和一个不可恢复/代价过高案例。验证并更新状态，停在 Experiment 4 run 前。

## Prompt L：Experiment 4 plan、批准、pilot 与 formal run

    只执行 Experiment 4，并要求 Experiment 3 已完成审计。先 plan-only 冻结 540 root-runs、六 modes、exact 5-task slices、3 repeats、独立 output roots、GLM-5.2 identity、hard limits和预算 digest。provider_calls_made=0 后停止请求用户批准。

    批准后 pilot 验证每个 mode 只关闭一个实验边界、FULL 默认语义未改变、错误 evidence 不跨 output root 污染。drift 时重新批准。正式运行输出 completion/validity、wrong canonical/raw-only/stuck/premature merge/slot mismatch，以及 exposed_error_count、escaped_error_count、error_escape_rate/applicability、time/tokens/cost。验证并更新状态，停在 Experiment 5 run 前。

## Prompt M：Experiment 5 plan、批准、pilot 与 formal run

    只执行 Experiment 5，并要求 Experiment 4 已完成审计。先 plan-only 验证完整三成员 cohort、各 provider config/key env/real smoke、reasoning controls、source/prepared/endpoint/cohort digests、exact shared slices、270 root-runs和hard limits。缺任一 member 时整体 incomplete_model_cohort blocked、零调用；不得用两模型或单模型生成主表。完整时生成预算 digest，provider_calls_made=0 后停止请求用户批准。

    批准后先分别做最小 endpoint pilot，核对 configured/requested/resolved model、reasoning、usage、latency/cost和identity mismatch stop。任何 config/model/reasoning drift 重新 plan/批准。正式运行后输出 model_execution_records.jsonl 和按 endpoint/provider 分层的 completion、accepted validity、tokens、cost、latency、provider error、recovery，并明确 provider confounding。验证并更新状态，停在联合审计前。

## Prompt N：五实验联合审计与论文输入

    只执行 Task 20 联合审计，不新增实验条件、不补跑未批准 provider call、不手工改结果。

    验证 Exp1–5 每个正式 suite 都有：

    - frozen catalog/selection/condition/budget/endpoint digests 与批准记录。
    - real transport、per-task/per-attempt、raw/parsed/provenance/usage、event/artifact evidence。
    - checker/verifier/canonical/merge 状态和 paper eligibility。
    - resume/replay 零调用证据。
    - 预算实际值不超过批准 hard limits；超限或不完整 run 明确排除。
    - scripted/fake/pilot/blocked/formal 数据完全分离。

    从 evidence 重生成并交叉核对：

    - paper_table_feasibility.csv
    - paper_plot_scalability.csv
    - paper_plot_robustness.csv
    - paper_table_ablation.csv
    - Experiment 5 endpoint comparison table
    - model_execution_records.jsonl
    - failure_examples.json
    - input_catalog_manifest.json
    - audit_summary.json 和 report.md

    所有 summary 数字必须能回到 task/attempt/event/artifact。负面结果保留，不预写正向结论。运行全量 tests、compileall、完整 init.ps1，更新 feature_list.json、progress.md、session-handoff.md 和 code maps。请求独立最终 review；只有无 Critical/Important 且正式证据齐全时，才能把 feat-011 标 done 并提交/推送。
