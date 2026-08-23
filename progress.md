# TokenShare 当前进度

更新时间：2026-08-23 +08:00

本文件保留当前权威状态、最近验收锚点、资源边界和下一步；逐轮命令、评审及旧测试细节由 `session-handoff.md`、`Doc/archive/`、git history 与仓库外 `TokenShareData` 保留。

## Slim V2 当前 focus：Exp5 三模型实测与 Exp1 Flash V4 补充比较（已实施，等待新 Representative）

- **用户决定（2026-08-23）**：Exp5 从 GLM、Qwen、MiniMax、DeepSeek V3 四模型改为前三个 SiliconFlow thinking 模型；三者 `thinking_budget=100000`、`max_tokens=100000`、`repeat0`、真实 Exp5 延迟保持并列。DeepSeek V3 已从 Exp5 inventory/config 删除。
- **最小隔离实现**：不改 `run-all`、`representative`、`reduce_run()` 主循环、正式五张 `metrics/tables/exp1–5.*` 或 `summary.json`。新增显式零调用 `compare-exp5-v4-reference --run-dir <three-model-exp5-run> --source-run-dir <flash-exp1-run>`，只读普通 inventory/committed `RootResultV2`，在 target run 原子发布 `metrics/supplemental/exp5_with_exp1_v4_reference.{jsonl,csv}`。
- **V4 reference 口径**：仅在 source/target 选择 case 精确相同、source 为 Exp1 `deepseek-v4-flash` repeat0 committed result且无 ordinal>0 provider call 时加入。补充表保留 V4 的质量、ordinal-0 actual token 和原始成本/价格事实；所有 V4 wall-clock 字段固定 `null + not_applicable_or_unavailable`。三个 Exp5 live 行保留各自真实 `repeat0_wall_clock_ms`。该表不构成正式四模型 Exp5 endpoint comparison，不进入153 formal metrics或正式摘要。
- **已观察的 fail-first 与验证**：profile 新断言在旧四模型 inventory 下为 `4 failed, 13 passed`；最小变更后 `tests/experiments/slim_v2/test_profiles.py -q` 为 `17 passed in 1.02s`。补充 reducer import 缺失红灯、CLI subcommand 缺失红灯均已观察；新比较行/CLI focused tests各为 `1 passed`。随后 `test_reducer_golden.py=35 passed`、`test_profiles.py=17 passed`、`test_answer_paths.py=21 passed`、`test_cli_e2e.py -k 'not representative_fake_transport'=26 passed, 1 deselected`；Exp5 config JSON parse、`python -m compileall -q src/tokenshare/experiments/slim_v2`及`git diff --check`均通过。交付前新增 source/target case-set mismatch 与 source ordinal>0 provider-call 的 fail-closed reducer tests，`2 passed, 35 deselected`；两种实际越界输入分别抛出精确 `ValueError`，且未创建 supplemental 输出。最终 `test_reducer_golden.py -q`=`37 passed in 11.91s`；复核后 `test_profiles.py -q`=`17 passed in 0.90s`、Exp5 Answer-paths=`3 passed, 17 deselected`、supplemental CLI=`1 passed, 26 deselected`，Slim `compileall`与`git diff --check`仍通过。最后一个 production fake Representative E2E 因153指标×10,000 bootstrap计算在60秒内未返回结果而主动终止，留给独立复核按资源安排决定；本轮未调用真实 provider、未启动 Representative/Full、未运行 Lean suite/Audit。

## Slim V2 当前 focus：最终汇总轻量修复 Tasks 1–6 全部完成并验收，交棒 Owner 3

- **用户决定（2026-08-22）**：采用 `Doc/SlimV2/slim_v2_final_summary_lightweight_repair_plan.md` 的六项轻量修复；不迁移旧 `root_result.v1`，统一升级为严格 v2 并以全新 run ID/目录重跑。保留同一 v2 run 的 crash/resume。Exp4 route evidence 实现若不阻断结果输出则延期；不增加指标、规则、shared 改动、安全或门禁。
- **接力安排**：三个 Owner 是三个独立侧边栏对话，均为 `gpt-5.6-sol/high` 并顺序续接同一保存项目 dirty 工作树。Owner 1 thread=`01a028ea-9c35-7391-ad6a-3fdccc20467e` 已完成 Tasks 1–3；Owner 2 thread=`01a028ea-e22d-7a21-bbb6-62283ea8394f` 已完成 Tasks 4–6；Owner 3 thread=`01a028eb-386f-7fe2-b1ff-867c40209b36` 现在可从同一 dirty tree 启动全新 v2 真实 AI Representative。各 Owner 只统筹，具体实现和复核由其对话内子智能体完成。
- **原始阻断证据（已修复）**：生产 reducer 在 Representative 规模全部 72 paper roots + 2 references 提交后，曾先因 Exp2 repeat 数值残留模板 missing reason 停止；临时越过后又因 Exp3 合法零分母无 reason 停止，均未发布 summary/tables。Tasks 1–3 已按冻结计划修复这些 reducer 接线及相邻 failure/infra 语义，未改公式。
- **Owner 1 实现结果**：Task 1 已用统一内部写入路径闭合 metric value/reason 互斥、Exp2 repeat 旧 reason 清理和所有直接正式 point ratio 的 `zero_denominator`；Task 2 的三类 failure breakdown 只按 committed `failure_kind` 直接分组并互斥求和；Task 3 删除 Exp3/Exp5 过宽 infra-invalid early return，保留输入完整的 inventory/attempt/fault/replacement 与 token/cost/wall-clock 精确事实，只使相关科学 rate/effect/interval 为 `null + infrastructure_invalid_root_present`。生产改动仅为 `reducer.py`，focused test 改动仅为 `test_reducer_golden.py`；Exp4 route、shared code和Tasks 4–6未动。
- **独立复核**：每项均由独立实现子智能体完成，并依次通过 fresh spec 与 fresh implementation-quality reviewer。Task 3 review 发现的 reference-only infra、missing committed reference、infra/slot metadata 三项 Important 均经 fail-first、原实现者修复和原 reviewer 复核关闭；最终 Tasks 1–3 的 Critical/Important/Minor/out_of_scope 均为 `0/0/0/0`。
- **Owner 1 fresh 验证**：`test_reducer_golden.py -q`=`31 passed in 10.74s`；完整 `tests/experiments/slim_v2 -q`=`168 passed in 328.03s`；Slim 源码/测试 `compileall`与`git diff --check`均 exit 0。provider/network=`0/0`；未运行真实provider、representative/full、LeanAudit、Lean suite/catalog、`lake`或`lean`。
- **Owner 2 Task 4**：ordinary root result 的唯一生产 literal 为`tokenshare.slim_v2.root_result.v2`，`failure_origin`必现且可为null；writer/reader/embedded protocol projection/projector/runtime/CLI/reducer均只接受v2并明确拒绝v1。Exp2–4 source run必须有strict v2 committed Exp1 roots；无migration、compat、dual reader或old-run reuse。schema另加入`verified_correct`与`failure_kind`双向互斥。fresh spec=`SPEC_COMPLIANT`、quality=`APPROVED`、最终C/I/M/OOS=`0/0/0/0`；相关组合`94 passed`，Tasks 1–3回归`24 passed`。
- **Owner 2 Task 5**：fresh/resume共用从protocol snapshot与当前root全部已持久化coverage-tail `UnitTraceV1.attempts`重建最终attempts的路径，顺序固定为protocol原顺序，再按冻结tail target和自然ordinal；相同持久化事实的fresh/resume `asdict()`完全相等。完整tail调用保持`3→3`，部分tail为`2→3→3`。fresh spec=`SPEC_COMPLIANT`、quality=`APPROVED`、C/I/M/OOS=`0/0/0/0`；`test_runtime_resume.py/test_answer_paths.py/test_schema.py`分别为`20/19/14 passed`。
- **Owner 2 Task 6**：production CLI Representative fake E2E使用冻结真实profile `Exp1–5=4/12/8/44/4`，合计72 paper roots，加Factorization/Lean references各1为74 executions；原始用户硬约束是`72+2`，不采用冲突旧摘要`6/18/30/12/6`。默认走production `execute_root`、runtime/coordinator、storage/publication与真实`reduce_run()`；唯一fake边界为provider `_open_response`，真实provider/network=`0/0`，fake HTTP=`86`，未运行Lean/lake。恰好发布11文件：summary与Exp1–5各JSONL/CSV，五表行数=`3/20/16/108/4`。storage closure修复只允许Exp1 coverage tail，非Exp1禁tail规则不变。E2E=`1 passed in 548.10s`；fresh spec=`SPEC_COMPLIANT`、quality=`APPROVED`、C/I/M/OOS=`0/0/0/0`。
- **Owner 2 最终 fresh 验证**：`test_reducer_golden.py -q`=`31 passed in 10.91s`；`test_schema.py + test_runtime_resume.py -q`=`35 passed in 17.17s`；`test_cli_e2e.py -q`=`26 passed in 637.23s`；完整`tests/experiments/slim_v2 -q`=`177 passed in 917.87s`；Slim源码/测试`compileall` exit 0；`git diff --check` exit 0，仅有既有LF/CRLF warning。provider/network真实调用=`0/0`；未运行真实provider、真实Representative/Full、LeanAudit、Lean suite/catalog、`lake`或`lean`；未stage、commit或创建worktree。
- **当前下一步**：Owner 3 thread=`01a028eb-386f-7fe2-b1ff-867c40209b36` 从同一dirty tree开始新的真实Representative；必须完成其自身credentials、Lean environment与零调用preflight，使用全新v2 run ID/目录并从头运行，不迁移/读取v1或复用旧run。同一v2 run若中断只能按已持久化result/trace/call事实resume。Owner 2不启动真实调用。
- **2026-08-23 当前修复与模型迁移状态（本条更新上一条的操作状态，保留其历史交接事实）**：用户已直接将前向 Experiment 1 的模型身份改为 `deepseek-v4-flash`，并建立不继承 Pro 公式的 `slim_v2.pricing.2026-08-23` flat 价格版本（cache-hit/cache-miss/output=`0.05/1.5/4.5 CNY per 1M tokens`，不形成价格门禁）；同一证据包的三名新鲜只读 reviewer 已形成 `3/3` 法定人数。`slim-v2-representative-real-20260822-144900-ef9128fe` 已写入的 Pro model/pricing 与其四个已完成 Exp1 roots 是不可改写的精确历史 cohort 事实，不能重标、重算或混入 Flash 前向条件。已确认本次终止是 Exp2 的 `factor_v2_hard_138/range_0` 真实 `625,812 ms` 延迟越过 lease 后，三次 `result_kind=succeeded` 的提交都以 late/lease-expired 被 rejected，旧 projector 错误要求唯一 accepted failed submission 所致；它是 Exp2–4 共用逻辑时序的已证实可达投影缺口，不是 Exp1 模型回答失败。
- **本轮代码与验证已完成**：`projector._natural_rejection_stage()` 现在只为唯一 `lease_expired + rejected/succeeded + lease_deadline_exceeded` 终端事实投影 `child_execution`，其余自然拒绝仍按既有严格失败 submission 规则处理。实现先以 `625,812 ms` 边界写出 fail-first regression，并完成 fresh spec/quality review 与 Owner 级 13-test focused verification。前向 `profiles/provider/schema/execution/reducer`、Exp1 tracked provider config 和 focused tests 已切换为 Flash identity 及 experiment→price-version 映射；Flash flat 公式独立，旧 Pro 公式只保留 exact historical cohort。为同一 run 的唯一失败 Exp2 root，`runtime` 只从已终端 ledger 重建 typed fixed-trace attempts 并写入 projection，`cli` 只对精确 run/config/root 开放此冻结路径；重复 submission、非终态上下文均 fail closed，测试显式禁止该路径调用 transport/provider。
- **exact-run 终态与交棒（2026-08-23）**：同一 `--resume` 已修复 Exp2 投影并完成全部 root processing；现已确认 process=`0`、冻结 inventory roots=`72`、root result files=`72`、calls=`52 intent/52 terminal`、responses=`47`。每个带 response reference 的 terminal 都指向存在的 response 文件；5 条 `MiniMaxAI/MiniMax-M2.5` terminal 是 `provider_transport_error` 且没有 response；unknown terminal=`0`。此 run 是变更前 Pro cohort 的历史事实，绝不是 Flash evidence，也不得重标为 Flash。
- **未发布的准确阻断与下一棒**：`reduce --run-dir` 在 publication 前停止，精确异常为 `ValueError: missing first-attempt verification evidence: factor_v2_hard_145:range_3`；metrics、`summary.json` 与 temporary publication 文件仍均为零，故不得把本 Representative 标为成功或发布。现有 Exp5 证据仅表明：该 root 在 concurrent early-stop 的 root terminal 之后仍有一条 HTTP 200、已解析的 pre-submission/verification attempt，而 reducer 当前要求 verifier evidence。它是留给 Exp5 规模变动 Owner 的开放 semantic + Slim-local reducer repair 项；不得在本记录中新增 taxonomy/reason 或自行裁决分类。后续 Owner 必须先保留并复核该精确证据，按需要完成独立 review／法定人数，再与规模变动一并修复并启动新的 Flash Representative。
- **交棒已发送（2026-08-23）**：已向 thread=`01a02a2f-4ad3-7822-bd92-a64b1cece35c` 发送一次交棒，确认本 Representative 已停止、无需新建 worktree，并要求其先处理上述 Exp5 规模变动与语义/Reducer blocker，完成后启动新的 Flash Representative。
- **Exp5规模、语义修复与前向运行（2026-08-23）**：Full Exp5现冻结为28个Factorization hard加纯逻辑/函数集合/归纳各3个Lean case，四模型仅`repeat0`、`max_retries=0`；inventory为16 conditions、148 roots、1,136 online upper，Full为7,054 paper roots、7,160 executions、7,046 online calls、84,618 fixed和91,664 total protocol attempts。经三份独立语义意见与第四位绑定仲裁，Reducer对带既有`not_applicable_or_unavailable`标记的`2xx/raw/parsed`未提交事实保持actual/coverage/resources、质量nonpass及派生为null，不伪造成任一三类失败或verification rejection；并经fresh规格/质量复核修复其不进入checkable rejection bootstrap。owner focused=`51 passed in 12.72s`、Slim `compileall`通过、Full零调用plan确认strict-P95=`9.761 GiB`。已对旧Pro exact run仅一次本地reduce并发布11个metrics文件，七类输入目录逐文件SHA-256 manifest均未改变；旧Pro cohort仍不是Flash证据。新的`slim-v2-representative-flash-20260823-210500`已完成：72个paper roots、74个executions（含2个Exp3 references）、72个root results，以及summary和五实验表共11个metrics文件。真实provider为Exp1=`19`、Exp5=`32`，共51个attempt，全部terminal/HTTP 200且带raw response；Exp1的4个Flash roots均`verified_correct`。Exp2–4非完成root是各自预注册fixed replay、fault或ablation条件；Exp5的3个非完成root则是模型比较本身的真实`no_final`结果：MiniMax与DeepSeek-V3为`model_verification_exhausted`，Qwen为`model_parse_exhausted`，不应误作provider故障。

## Slim V2 旧状态：Stage 4 representative曾启动但命中credential强制停止

- **接力身份与checkpoint**：`stage=4`、`run_scope=representative_only`、branch=`codex/slim-v2-baseline`，Stage 4 Task=`01a026aa-cece-7391-9815-65a3487d87cb`，`previous_task_id=01a02577-5180-7c53-8472-eb7e517b0c4f`；启动HEAD=`5e9eab24a23ee7fc11075fc741c581923bd58678`，implementation parent=`d6e5610c10782add73f70397aca88ec79fc79145`。本条所在提交是Stage 4强制停止证据checkpoint，不是representative完成checkpoint。
- **仓库事实**：启动时branch/HEAD/parent精确匹配接力输入；工作树只有受保护`M AGENTS.md`，SHA256=`382A2DC37F01C25DE5156569FBC7027C3FE725DD5D5F99A5D5F7FAF0A752FAED`，未reset/revert/stash/delete/edit或提交；固定reference tag解析为`3489533e79cde05d6ae2a0c9f139785249f60f09`。
- **heartbeat**：当前Task唯一`TokenShare Slim V2 Stage 4 recovery` heartbeat已创建并验证为`ACTIVE`、`FREQ=MINUTELY;INTERVAL=30`，绑定当前Task；Stage 4尚未完成，故按relay保持启用。没有创建Stage 5。
- **零调用plan**：同一CLI `plan --profile representative --run-id slim-v2-representative-20260822-stage4`成功，精确输出论文roots=`72`、executions=`74`、provider-call upper=`89`；Exp1/2/3/4/5 roots=`4/12/8(+2 references)/44/4`，Exp2–4 provider upper=`0`；估算=`0.239 GiB`、hard upper=`3.498 GiB`、逐root margin=`0.5 GiB`，E盘可用空间约`824 GiB`。
- **credential强制停止**：`DEEPSEEK_API_KEY`在当前进程存在；`SILICONFLOW_API_KEY`在Process/User/Machine与`conda run -n tokenshare`子进程中均不存在，`local/ai_api_smoke.local.json`也不存在。按relay §7.7，缺credential属于客观强制停止条件，法定人数无法生成外部secret，因此未分派quorum、未运行partial representative、未修改代码。
- **正式CLI证据**：仅执行一次`conda run --no-capture-output -n tokenshare python -m tokenshare.experiments.slim_v2.cli representative --run-id slim-v2-representative-20260822-stage4`，exit code=`1`，失败点为provider preflight的`missing provider secret/API key env var: SILICONFLOW_API_KEY`；失败发生在写run inventory和调用`execute_root`之前。
- **进程/results/trace/journal核对**：启动前无Slim V2 CLI/GUI遗留进程，目标run目录不存在；失败后目标run目录仍不存在，因此results/traces/calls/responses均为0，provider/network=`0/0`，没有未知终态或可盲目重试的attempt。credential外部可用后应再次先核对进程与该run目录；若仍不存在，使用同一run ID不带`--resume`重新执行同一`representative`命令。
- **禁止项**：未运行full、Lean专项suite、LeanAudit、全量catalog、`lake`/`lean`回归，未push/merge/PR。Stage 4没有满足设施通过标准，不能标记representative完成，也不能关闭heartbeat或创建Stage 5。

### Slim V2 跨域失败语义与 Lean 有效性修复（2026-08-22）

- **直因与修复**：representative Lean 的5/6 parser失败由模型把冻结`schema_version="lean_proof.proof_candidate.v1"`缩写为`v1/1.0`引起；Lean prompt现给出完整JSON skeleton与禁止缩写规则。唯一parse成功候选进入checker后命中缺失`LemmaGraphOracle.olean`；checker现区分proof rejection与environment/timeout/helper error，不再把全部非零退出压为rejected。
- **环境闭环**：新增独立`lean-environment-test`，补齐Cases/Oracle构建并以oracle proof实测全部checker-backed节点；165 cases/690 nodes中135/570 checker-backed全部accepted，30/120为预注册`structured_blocked`且不冒充通过。仅全过后原子写持久pass；实验启动只轻量校验pass、关键输入与两份`.olean`哈希，不运行Lean/lake。pass失效只阻断pending Lean ordinary/reference roots，Factorization继续，fixed-source closure排除Lean-invalid keys。
- **跨域终态与指标**：Factorization/Lean的parse、正常verification、provider-only与mixed耗尽结构化返回有效`no_final`并保留`failure_origin`；checker环境错误立即停止并投影`infrastructure_invalid`；未知runtime/store/ledger故障fail closed。Reducer保留固定库存，缺committed result计`missing_committed_root_result`设施无效；三项mandatory inventory diagnostics满足valid+infra=preregistered且不扩展153 formal occurrences；infra cell的Exp1–5相关科学率/interval为null，pair/quadruple不接纳。
- **coverage tail全路径**：有效`no_final`仍按冻结`unscheduled_ai_unit_ids`原顺序执行/恢复tail，只有condition/runtime/infra terminal阻断；execute/resume/projector/CLI共用同一predicate。Lean缺canonical依赖生成带真实`lemma_node_id/dependency_path`且不调用provider的typed pre-dispatch trace；snapshot同时冻结protocol projection/traces、tail requests和已知pre-dispatch traces并校验集合闭包；Exp2–4 fixed replay可直接消费，tail资源只统计真实provider calls。
- **范围保持**：Lean题库、pure leaf/root DAG、induction单节点、retry次数、provider policy与实验矩阵均未修改；未覆盖工作区已有Factorization prompt v2改动。未调用provider，未运行representative/full/LeanAudit。
- **最终focused证据**：跨层Lean/Factorization runtime、projector、schema、reducer、CLI、prompt/checker/validator、环境pass测试=`140 passed in 94.68s`；完整Exp2–4场景与真实Process worker-death=`18 passed in 249.76s`；权威/初始化合同=`25 passed in 1.73s`；轻量`validate_lean_environment_pass()`成功并返回pass digest=`sha256:12003b4021d0fc493c358cfee1cd750e19ab0c8c20eb5bae2a616e46355f97b3`；相关源码`compileall`与`git diff --check`通过。

### Factorization B prompt v2统一修订（2026-08-22）

- **用户决定**：后续Slim V2 `representative`与`full`统一使用B方案新提示词`factorization.bounded_range_prompt.v2`；旧v1 representative结果只作诊断，不与v2 Full或Exp2–4来源混用。
- **实现边界**：仅修改`src/tokenshare/plugins/factorization/prompt_builder.py`及focused test；保留`factorization.range_result.v1`、14字段`RangeResult`、result kinds、parser、verifier、runtime adapter、execution bridge和trace schema。四份冻结前置权威无需修改；设计规格、实施蓝图和code map已同步实验身份与兼容边界。
- **提示词行为**：单一canonical JSON skeleton；目标数只出现一次且输出前重新加载；删除候选枚举和双结果模板；按精确`a_start/a_end`路由bounded Fermat；wheel只能用满足`N % p != 0`的prime筛倍数；`no_factor_in_range`必须完备覆盖；factor必须通过range、modulo、quotient、product和逐位目标比较。
- **静态缩减证据**：代表性`N=718034459,[16748,20096]` prompt由3154降至2336字符（减少818，约25.9%），目标数出现次数由4降至1。prompt/profile测试分别完成可解释RED→GREEN；最终合并的Factorization plugin、Phase 6 flow与Slim answer-path focused regression=`80 passed in 34.58s`，compileall、五份修改文档严格UTF-8读取和`git diff --check`均PASS。provider/network=`0/0`，未运行representative/full或Lean。

### Slim V2 Full 性能与资源专项审查（2026-08-22）

- **2026-08-23 资源再评估与用户决定**：用户已批准撤销“Full不可资源安全放行”的预数据结论；**Full通过资源放行，尚未实际启动**。该决定仅裁决磁盘/内存/恢复资源，不替代严格v2 representative的端到端科学/功能验收。
- **真实数据与重算**：81条真实provider response覆盖Exp1及Exp5五个配置endpoint/model；全样本P95=`200,721 bytes`、严格v2子样本P95=`312,983 bytes`、最大raw artifact=`462,520 bytes`。因此默认`1 MiB` plan=`19.261 GiB`是当前保守值；以严格v2 P95重算的Full plan为`9.761 GiB`，全样本P95重算为`6.998 GiB`。
- **经验资源包络与空间**：不用`12 KiB/fixed attempt`旧公式，而让84,618个fixed attempts均按`1.5×`最大raw artifact加实测最大非raw overhead=`135,975 bytes`，7,046个online calls按三份raw物化加`256 KiB`系统开销，7,160 executions各加`160 KiB`并施加25%余量，得到`102.326 GiB`。E盘当前可用`823.925 GiB`，为包络`8.05×`；逐root仍以既有`512 MiB` margin、空间复检、安全停止与resume覆盖冻结inventory。
- **历史修正与残余风险**：`165.27 GiB`是“每个response均为1 MiB且主体会被完整重复”为前提的合成压力算术，不是实际预测；真实response主体主要为reasoning，fixed artifact通常接近raw大小。fixed字节模型、source closure重复I/O、bootstrap进度和provider总墙钟deadline仍可后续优化，但不再阻断Full资源放行；provider长尾仅影响完成时间，用户已明确不作为本次放行条件。
- **启动记录**：启动前以`--representative-raw-response-p95-bytes 312983`运行同一Full `plan`并保存输出，再检查目标盘可用空间；运行中保留每root空间复检、安全停止和resume。当前无活跃Full进程。本项完整依据见`Doc/SlimV2/slim_v2_full_performance_audit_20260822.md`；本轮未修改实现代码、未调用provider或启动Full。

## Slim V2 旧Stage 3 provenance：已暂停并由六Task重规划取代

> 本节只保留旧编号、提交和测试provenance；其中“当前focus/允许写入/下一focus”等命令性表述均已失效，不得恢复执行或覆盖上方当前状态。

- **Task 1完成证据**：implementation commit=`aaabca417f76c00db6011f8bf0bb04617fffa4f2`；owner fresh focused=`6 passed in 0.17s`；authority leaves=`168/168`、metric records=`153/153`、operational leaves=`5/5`、required/nullable规则=`173/173`；UTF-8、禁止import、尾随空白、cache与`git diff --check`均通过。
- **Task 1评审**：spec最终`0 Critical / 0 Important / 0 Minor / 0 out_of_scope_by_user`；quality最终`0 Critical / 0 Important / 0 Minor`并`APPROVED`。生命周期未启动时四个边界字段的诚实null表示经relay §7.1三票一致选择方案A，固定reason细节按两票多数，状态=`approved_under_user_delegation_by_quorum`。
- **Task 2完成证据**：implementation commit=`13a25193465736c0fecda5d304d9de5be3c2ecbc`；owner fresh=`8 passed in 0.20s`；Factorization/Lean catalog分别为`500/165`行与SHA256 `9ce2b31a…7774/5a134f24…2cdc`；Exp1=`300+135`且planned units=`1970`，Exp2=`50`，Exp3=`50+3`，Exp4=`50+15`，Exp5=`42+12`，representative四题units=`19`，全部tuple差集与重复为0。
- **Task 2评审**：spec=`0 Critical / 0 Important / 0 Minor / 0 out_of_scope_by_user`并独立重算全部tuple；quality=`0 Critical / 0 Important / 0 Minor`、`APPROVED`。正式Lean只选`preflight_status=passed`与`task14_checker_backed_pool`，准确排除30个`structured_blocked` stress cases；archive/legacy读取为0。
- **当前唯一 focus**：严格执行Task 3“展开profile inventory、规模上限与无provider `plan`”；不提前开始Task 4。
- **Task 3允许写入**：修改`src/tokenshare/experiments/slim_v2/profiles.py`与`tests/experiments/slim_v2/test_profiles.py`；创建`src/tokenshare/experiments/slim_v2/cli.py`与`tests/experiments/slim_v2/test_cli_plan.py`；计划证据表和本顶部状态仍是常设证据例外。
- **Task 3 fail-first命令**：`conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_profiles.py tests/experiments/slim_v2/test_cli_plan.py -q`；允许失败为缺`build_profile/build_inventory/build_plan`或`cli plan`。
- **运行边界**：Stage 3 provider/network spy累计保持0；不运行representative/full、Lean专项suite、LeanAudit、全量catalog或`lake`/`lean`；shared code保持只读。

## Slim V2 旧横向Stage 2 provenance：原批准结论已暂停

> 本节记录旧横向计划的历史审查，不再证明当前六Task蓝图已批准；当前审批状态和下一步以上方current focus为准。

- **实施计划已收口**：`Doc/SlimV2/slim_v2_implementation_plan.md`状态为`approved_under_user_delegation`，按设计规格第17节拆为25个严格串行task；每个task均冻结目标文件、测试文件/测试名、先失败命令与预期、最小实现、通过命令与预期、依赖、允许写入、完成证据和progress更新点。Stage 3只按该计划实现与运行离线/focused verification，不启动真实representative provider。
- **代表性inventory一致性勘误已获用户确认并闭合**：保持四个case IDs和当前catalog不变；`lean_v2_simple_induction_direct_nat_01`按公共fixed plan的1个planned AI unit执行，派生常量同步为Exp1 units `19`、Exp1 hard cap `57`、总hard cap `89`。设计规格的精确CLI `--output-root`及Slice 6派生常量也已同步；未改变数据集、实验变量、runner或shared接口。
- **两路只读审查全部通过**：spec coverage与task/test decomposition reviewer初审发现经统一裁决、逐项修复并短复核后，最终均为`0 Critical / 0 Important / 0 Minor / 0 out_of_scope_by_user`，verdict=`PASS`，shared interface gap=`none`。计划不存在TODO/TBD/开放实施选择。
- **用户睡眠期间代理授权规则已写入接力协议**：`slim_v2_stage_relay_protocol.md`第7.1节要求三名互相独立、只读且遵守`$pua:pua`的子Agent审议；至少两票一致即直接形成`approved_under_user_delegation_by_quorum`，三案全异则由第四名auditor绑定裁决，不询问用户。该规则不扩大系统权限、任务写入、实验范围、run_scope、provider上限、安全范围或远端/破坏性操作权限；超界只能选择`safe_no_action`。
- **Stage 2 fresh非Lean验证通过**：11份启动/权威/本阶段文档严格UTF-8；正式metric追踪`153/153`且missing references `0`；implementation tasks与七类固定字段均`25/25`，失败/最小/通过测试分解逐task闭合；Markdown fences成对、table issues `0`、placeholders `0`、active stale representative constants `0`、`git diff --check` PASS；`conda run -n tokenshare python -m pytest tests/test_init_verification_profiles.py -q`为`25 passed in 0.78s`。本阶段没有写实现/shared代码，没有运行Lean专项suite、LeanAudit、catalog、`lake`/`lean`、representative/full，没有调用provider，没有push/merge/PR。Stage 2 checkpoint为本条所在本地commit；下一棒是Stage 3实施与离线focused验证。

## Slim V2 前置权威文档：已获用户批准并冻结

- **Stage 1设计规格已完成并获委托审批**：唯一交付物`Doc/SlimV2/slim_v2_design_spec.md`状态为`approved_under_user_delegation`，`run_scope=representative_only`。本棒固定分支`codex/slim-v2-baseline`，启动checkpoint=`9f711cc2c58ae77c090ac30ac32141948cc833dd`；Stage 1最终checkpoint为本条所在的本地commit，实际SHA会写入Stage 2接力prompt。规格冻结最小架构、五实验控制/数据/失败语义、全部schema和指标追踪、普通恢复主键、full资源上界与实现顺序；其representative调用上限已在Stage 2按用户确认的catalog一致性勘误由92修正为89。shared interface gap=`none`，未批准也未修改shared code。
- **Stage 1三路只读审查已闭合**：metrics初始`0 Critical/7 Important/2 Minor`，integration为`0/3/0`并有1条范围外攻击意见，slimness为`1/3/1`；全部范围内问题已修复，范围外意见标记`out_of_scope_by_user`并拒绝实施，最终未解决范围内Critical=`0`、Important=`0`。跨文档二审确认正式metric IDs `153/153`、权威raw/closure标识规范化`191/191`，禁止gate/authority未进入runtime依赖。
- **Stage 1 focused验证通过**：8份权威/规格Markdown严格UTF-8；围栏22且成对、表格列问题0、实现语义占位符0、`git diff --check`通过；`conda run -n tokenshare python -m pytest tests/test_init_verification_profiles.py -q`为`25 passed in 0.86s`。本棒没有运行Lean专项suite、LeanAudit、catalog、`lake`/`lean`，没有启动representative/full、没有调用provider、没有push/merge/PR。下一棒是Stage 2，仅编写`Doc/SlimV2/slim_v2_implementation_plan.md`并继续`representative_only`接力。
- 用户将强约束 prompt 中的顶层设计思想提升为独立权威文档 `Doc/SlimV2/slim_v2_design_charter.md`：冻结指标优先于旧代码、最小充分、薄适配层、普通数据、实验原子化、只为必要回答付费、representative/full 同管线、资源有界、失败保留，以及明确放弃 budget/receipt/digest/lineage/publication/response-bank authority/旧 runner 等设施。后续 prompt 仍保留硬约束，设计宪章作为第二道权威锁；设计规格必须提供 charter requirement追踪矩阵。
- 用户进一步冻结最高设计指示：Slim V2以最快速度得到真实有效实验结果为唯一核心，只保留产生冻结指标不可缺少的功能；真实有效不表示需要证明本地数据无人伪造。本项目假设受信本地研究环境，明确排除人为伪造、恶意篡改、路径/SQL/JSON/prompt/命令注入、恶意plugin/executor/provider、安全fuzzing、签名鉴权和攻击者模型。reviewer基于这些范围外威胁提出的意见必须标记`out_of_scope_by_user`并拒绝实施，不得计入Critical/Important或阻塞阶段；Experiment 3/4冻结fault/challenge不得扩张成安全工程。该指示已置于AGENTS、README、设计宪章和Stage 1强约束prompt最显眼位置。
- `2026-08-21`设计宪章与强约束prompt focused verification：7份Slim V2 Markdown严格UTF-8读取；最高指示前置、受信环境、伪造/注入排除、`out_of_scope_by_user`处置、Experiment 3/4与攻击模型分离、四份权威阅读顺序、Stage 1 prompt十三组约束、无残留“三份权威”表述、Markdown表格/代码围栏、尾随空白和tracked `git diff --check`全部PASS；纯harness测试`tests/test_init_verification_profiles.py`为`25 passed`。本轮没有运行Lean、LeanAudit、catalog、`lake`或`lean`命令，没有调用provider。
- 用户新增无人监督运行规则：接力链每个 Stage owner 都必须为自己的当前顶层任务创建/更新唯一的 30 分钟 recovery heartbeat；每次唤起先检查已有进程和持久化结果，再从原阶段恢复并在同一轮持续推进，禁止只报状态或等待下一次唤起。交棒时确认下一阶段 heartbeat active 后才禁用当前项；未来任务尚未创建，不能在本任务预绑定其 heartbeat。
- 用户新增测试裁剪规则：Slim V2 默认不运行 Lean 专项 suite、LeanAudit、全量 Lean catalog 或 `lake`/`lean` 回归，不为证明已完善的系统本体重复跑 Lean；优先用 Factorization、fake、固定 fixture 和静态合同验证 Slim 接线。指标权威要求的 representative/full Lean roots 仍作为实验样本运行，不属于额外测试。README、接力协议、AGENTS、Agent 导航和接线合同中三处旧 k>1 Lean focused-test 要求均已同步，避免权威冲突。
- `2026-08-21` 新规则 focused verification：6 份 Slim V2 Markdown 严格 UTF-8 读取；heartbeat 30 分钟/同轮持续推进/单实例/交棒/真实调用去重断言、Lean 测试裁剪正反断言、Markdown 表格列数、尾随空白和 `git diff --check` 全部 PASS；纯 harness 测试 `tests/test_init_verification_profiles.py` 为 `25 passed`。本轮没有运行 Lean、LeanAudit、catalog、`lake` 或 `lean` 命令，也没有调用 provider。
- 已新增 `Doc/SlimV2/slim_v2_stage_relay_protocol.md`，用于在设计、实施计划、代码实施监督和 representative 运行之间创建上下文隔离的全新 Codex 顶层任务；阶段内部只使用边界明确的子 Agent。该协议是用户授权的可选工作流，不覆盖四份冻结前置权威，未显式启动时不自动创建任务。接力文档初次 focused verification时的 6 份 Slim V2 Markdown 均可严格 UTF-8 读取，四阶段/双 `run_scope`/委托审批/单下一任务断言、Markdown表格列数、尾随空白和 tracked diff check全部PASS；`tests/test_init_verification_profiles.py`为`25 passed`。设计宪章加入后的新总数与验证见本轮后续记录。
- `Doc/SlimV2/README.md`、`slim_v2_design_charter.md`、`slim_v2_experiment_metrics_authority.md`、`slim_v2_system_integration_contract.md` 均为 `status: user_approved`，已成为 Slim V2 范围内的前置权威；设计规格现已获委托审批，下一步是Stage 2实施计划，仍未授权Stage 2编写实现代码。
- Slim V2 现为默认开发主线：除非用户在当前任务中明确指定其他维护范围，所有后续 Agent 都从 `Doc/SlimV2/README.md` 进入，不再自行梳理旧 paper/formal pipeline、archive、历史 Rxx 输出或 `TokenShareData` 历史结果；legacy `feature_list/session-handoff` 和陈旧基线不能覆盖该默认路由。
- 旧实验设施已固定为本地只读参考：archive branch=`archive/slim-v2-reference-20260820`，commit=`3489533e79cde05d6ae2a0c9f139785249f60f09`，annotated tag=`slim-v2-reference-20260820`。后续 legacy 参考只允许按 `slim_v2_reuse_inventory.md` 的 allowlist 使用完整 SHA + `git show` 定点读取；禁止 checkout/广泛阅读，Slim runtime 不得 import archive/legacy `paper_*`。本轮未 push。
- 干净 Slim baseline 从 `main` commit `c963d7f8b9cc2346267279170740910a42de54fd` 建立为本地 branch=`codex/slim-v2-baseline`。清单点名的 39 个公开符号全部存在，baseline 共享接口 focused suite `81 passed`，因此没有选择性移植或修改 shared core/local_runtime/plugin/executor；当前 baseline 只收口 Slim 文档/harness。
- 用户已冻结五项 Slim V2 补充规则：Exp2–4 复用 Exp1 正式运行自然 traces；`relative_range=(max-min)/mean`；除 Exp2 外 `worker_count=10`；Exp2/3 在线检查退出；Thread/Process 由实施 Agent 用 focused tests 裁决，不要求用户预选。trace 的精确来源键与 split 修订见下一条。
- 用户随后冻结了回答复用修订：Exp2 取消独立 20-way split并完整继承 Exp1 任务计划；Exp1 每个实际执行 AI unit 保存 `source_repeat_id=0`、最多三个自然 attempts 的普通 per-unit trace；Exp2–4 的实验 `repeat_id` 与来源 repeat 分离，按 `case_id × source_repeat_id=0 × planned_ai_unit_id` 命中并核对普通 Factorization range 或 Lean node/dependency 字段，不新增下游 provider call。
- Experiment 1 trace coverage 已由用户冻结：每个 root 正常 `run_root()` terminal 后立即执行 Slim-local coverage tail，只补该 root 的 `unscheduled_ai_unit_ids` 且不重复 protocol units；trace 标记 `trace_origin=coverage_tail`，tail wall/token/cost 单列，完成后才开始下一 root。Exp1 正文批次 wall-clock 使用正常协议 `Σ runtime_wall_clock_ms`，避免 root 间 tail 泄漏进 `max-min`。Exp2–4 exact ordinal 缺失时确定性读取同 trace 最后自然 attempt；Exp3 仍按下游当前 ordinal 生成不同扰动，不增加 AI 调用。
- 用户已批准 Experiment 4 的 challenge-driven 两两完备消融设计，并已写入指标权威：固定 FULL、4 个单机制和 6 个双机制共 11 modes；65 roots、3 repeats、Exp1 DeepSeek 回答复用和 provider calls=0 不变；四类 challenge 在 mode 前按 `case_id × repeat_id` 确定，injector 不可见 mode；配置/preflight BLOCK 使 cell 无效，协议启动后的 no-final/stuck/incorrect/root-check rejection 才计为实验结果；新增数值任务下降、pair 与 interaction 公式。
- 用户已冻结 Experiment 3 正式故障/扰动规则：fault-rate 分母为每 root 的 planned first-attempt AI units，目标稳定均匀选取且只注入 ordinal 0；五类 fault 的动作、replacement 行为、seed=20260820 的 token/latency 扰动、配对公平和核心恢复/资源指标均已写入指标权威与接线合同。官方 usage schema 进一步确认 reasoning 是 completion 子集，因此扰动与成本不得把 `completion_tokens+reasoning_tokens` 直接重复相加。
- 用户接受真实 API 边界按能力设计，不强制复用旧 `AIAPIExecutor` 整类；roots 固定串行，root start/terminal/runtime 使用完整协议生命周期边界；Experiment 4 五个 delta 指标使用不带附加后缀的正式名称。上述决定已同步进相应 Slim V2 权威文档。
- DeepSeek 与 SiliconFlow 官方价格已联网核对并冻结为 `slim_v2.pricing.2026-08-20`：DeepSeek `deepseek-v4-pro` 使用人民币峰/谷 cache-hit/cache-miss/output 表；SiliconFlow 四个 endpoint 使用专用价格页，并由用户登录后的模型详情逐项交叉核对。价格只是普通成本换算常量，不是预算或门禁。来源、访问日期、reasoning 归属和页面差异已落库到 `Doc/SlimV2/slim_v2_official_pricing_sources_20260820.md`，索引已写入 `Doc/agent-navigation.md`。
- 前置权威文档轮次曾按指标权威修正接线合同中的旧五模式表述，补齐 11 modes、challenge plan/observation、`disabled_mechanisms`、protocol-start、trace coverage tail、last-attempt fallback 与 ablation 细粒度字段来源；本次Stage 1已据此完成设计规格，但仍未开始Slim V2实现代码。
- 实际代码核对发现：现有 coordinator/plugin/executor 能覆盖主要生命周期，但需要 Slim-local coverage-tail acquisition、fixed-response executor、薄 provider caller、普通 pricing projector 与 metrics projector；细粒度字段从同一次 run 的 ledger/store 提取。两个 runtime adapter 的 k>1 接法仍需实施期测试证明，shared 系统代码保持只读。可直接复用/轻量适配/只借逻辑/禁止复用的精确源码位置已同步到 `Doc/SlimV2/slim_v2_reuse_inventory.md`，并按当前权威修正旧 split、fault、11-mode ablation、timing、pricing、reasoning 与 trace coverage 口径。
- 下方 R54/representative/Full 段落描述旧 paper/formal 设施的既有状态，不是 Slim V2 的设计来源；Slim V2 Agent 不应继续阅读这些历史链路来决定实现。
- 本轮仓库收口没有启动实验、调用 provider 或修改运行数据；创建了本地 archive commit/tag 和 Slim baseline branch，但没有 push/merge/PR。archive 纳入范围在 tag 前通过公开接口 `88 passed` 与稳定专项 `29 passed`；4 份未闭合新增 WIP 测试未进入 archive allowlist，原共享脏工作树副本保持不变。
- 文档 focused verification 已通过：原有五份 `Doc/SlimV2/*.md` 均可按 UTF-8 读取；指标权威第 8 节与接线合同第 7 节原始叶字段集合 `168/168` 完全一致、missing/extra 均为 0；价格、reasoning、roots 串行、Exp1 两阶段 trace coverage/last-attempt fallback、Exp2 split、Exp3 seed、Exp4 11 modes、固定 archive SHA/tag 与 `git show` allowlist 断言全部 PASS；清单 85 个代码跨度无越界/歧义、36 个关键公开符号定位匹配；Markdown 表头列数一致，Slim harness 定向测试 `25 passed`，相关文件 `git diff --check` 无 whitespace error（仅既有 Windows LF/CRLF 提示）。接力协议是随后新增的第六份文档，其独立验证记录见本段前文。

## 当前结论：representative 与 Full 均不可宣称完成

- `R54` 的 plan-only、receipt、paid reload/attestation 均绑定同一个预算 digest=`sha256:551c5699...`，且 `Exp1 new_paid=false`、`global_new_paid=Exp5 only` 正确；失败并非这两个字段在进程间丢失。paid service 在消耗 readiness 并物化 Exp1 acquisition bundle 后再次推导预算，改用当前 Exp5 pricing binding，得到不同 digest=`sha256:8a004584...`，随后以 `results-first execution requires the exact prepared provider budget` 阻断，控制流尚未到 external-bank resolver 或 Exp5 transport。
- 直接根因在 fresh Exp4-excluded loader：它先刷新 current Exp5 execution binding，却只在 Full 有 `approval_authority` 时把该 binding 传给预算推导；representative 因此回退到 frozen endpoint/pricing digests。paid service 的二次推导则无条件传 current Exp5 binding。金额、calls、tokens 与 `new_paid` 均一致，只有 Exp5 pricing provenance digests 不同。
- 这是系统性预算注册缺陷而非单字段事故：同一预算 authority 在 fresh loader、paid service 与 legacy warm/plan/cold restore 多处重新推导，且 optional authority context 不一致；现有 `persist_results_first_provider_budget()` 没有 production callsite。最快建议是 representative/Full 统一消费已刷新 Exp5 binding、预算只签发/持久化一次、receipt 后只校验不重算，并在 external-bank reuse 时跳过 90-call Exp1 acquisition bundle 物化；实施仍待用户确认。
- `R52`（`representative_exp1_exp3_exp5`）已按用户要求启动后不监督；本轮不读取或推测其终态。
- `local/Newfullrun.ps1` + `local/newfullrun_audit.py` 只生成固定 selection=`full_exp1_exp3_exp5`（Exp4 excluded）的 current-pricing、zero-call Full 预算审批 authority；状态为 `awaiting_user_approval`，receipt=`not_issued`，不创建 bank、不 dispatch。
- `local/Newfullrun_execute.ps1` 已接通单 A → internal receipts → Full bank → scope → plan-only → paid canonical loader；本轮按用户要求不启动 Full，Full paid 结果仍未宣称完成。
- `2026-08-20` authority review 发现并修复 execute.ps1 的空 planning-root P0；representative 新鲜 scope/plan-only 验证 `selection=representative_exp1_exp3_exp5`、115 conditions/roots、provider_calls=0、budget authority=`sha256:84a33a6d...`。本轮未启动 Full。

## 最近修复

- Exp5 pricing authority 已拆出 R13 source-bank 复用边界：R13 response bank 只复用 Exp1–3 trace，Exp5 binding 从当前 provider config 重建。定向测试 `2 passed`，未调用 provider；R51 的旧 `provider_calls=0` blocked 记录不被改写。
- R50 Exp1 finalizer 已修复 partial committed-source coverage 与 raw response artifact identity 对齐；定向回归分别 `28 passed`、`32 passed, 35 deselected`，未调用 provider。
- current-price authority 已接入 Full materializer/loader；新增 external-bank manifest binding 与 structured freshness blocked 校验。representative 仍使用 R13 frozen Exp1 pricing/bank，Full-only current pricing 仍需真实 acquisition 才能产生 paid 结果。

## Representative 固定边界与已付 acquisition

- representative 固定 `145` roots，Exp1/2/3/4/5=`12/6/81/30/16`；`representative_exp1_exp3_exp5` 本轮实际保留 Exp1/2/3/5=`12/6/81/16`，Exp4 排除，不能用 Full 或历史 145-root 结果替代。
- Exp2–4 主矩阵只消费可追溯的 immutable response bank，完整重跑 TokenShare 状态机、fault/recovery、worker death、ablation、verifier/checker、merge 与 settlement，标为 `real_model_trace_protocol_run`；当前 provider calls 必须为 `0`。只有 Exp1、Exp5及另行批准的 Exp2 在线检查、Exp3 在线恢复允许真实 API。
- Exp1 acquisition inventory=`90`，`90/90 settled`，`76 success + 14 provider_failure/usage_missing`，provider calls=`90`，每 slot 至多一次且无 reacquisition。known actual=`1,733,487 tokens / CNY 10.021065`；usage-missing 上界 charged=`6,027,352 tokens / CNY 35.502660`。
- typed representative cap=`202 calls / 34,385,749 tokens / CNY 242.762815`，budget digest=`sha256:9f3e0ffbb7f27a5c970a27a1b9c5d62ceb5dd0634dd2dc1a308c114471540d8e`；Exp1 已占 90 calls，后续 Exp5 上限 112，外层 CNY1500 不得抬高 typed cap。
- R13 paid ledger `local/paid-representative-20260816-r13-paid-output/acquisition/acquisition_budget.v1.sqlite3` SHA256=`ea778c0f5cadabc8cfa02f94d8b335eebb4d3a6c08d4130f45ed3b494fad53fe`，WAL/SHM absent。R15 plan-only source pin 已因 bootstrap adapter 变化而 stale，不得用于 paid resume。

## Bootstrap / authority 门禁

- 最新唯一 official provider-zero bootstrap session=`18419` fail-closed，`ValueError / staged_publication_preflight`；进程已回收，四个正式 publication target absent，ledger/source/provider unchanged (`0/0`)。禁止 blind retry。
- 历史 official bootstrap 曾因 multi-entry condition 的 `entry_digests` 排序/映射契约 fail-closed；该记录只保留为旧边界证据，不再作为 R54 的当前根因或修复判断。
- L2=`8 passed`；L3/L4 为 exit=`3` blocked。Task34 follow-up L1=`882 passed`，Fast=`525 passed / 1 skipped`；review=`0 Critical / 0 Important`。这些是边界证据，不是 formal publication PASS。
- execution/publication gates 保持 BLOCKED 且无环；execution digest=`sha256:b1dfa6edf77b3a0982dc72ce09fde74f1fe1300d7f055520c95cfe7cc721de3c`，publication digest=`sha256:7b49f79a7a22f63eb7bf9e3ba7056fd61c918b12deeed5070433a03113273d0c`。formal matrix **NO-GO**。

## Full 固定规模与旧 FullAudit 证据

- Full authority 固定 `324 conditions / 6,384 roots / 40,520 first attempts`；正式 Full 不得重建 catalog/snapshot/inventory 或复用 R13 representative bank。
- 正式规模 profile=`paper_suite_scale_300_50_54.v1`，Exp1–5 roots/first-attempt/provider-attempt upper=`435/1970/1970`、`600/12000/12000`、`3726/17148/54372`、`975/4410/7938`、`648/4992/4992`，合计 `6384/40520/81272`；冻结 ceiling=`23,503,151,360 tokens / CNY 7,346.259328`，启动前仍须按 current authority 重算。
- `2026-08-18` FullAudit 是 zero-call preparation，不是 Full run：`234 conditions / 5409 roots / 36110 selected units`，source bank=`pending_new_full_exp1_acquisition`、`r13_bank_accepted=false`，projection=`6962 calls / 909477542 tokens / CNY 7113.566514`，状态=`ready_for_user_paid_receipt`，无 paid receipt、无 acquisition、provider=`0`。其专项 focused test `1 + 3 passed`，未运行 Full E2E/LeanAudit。

## 历史验收摘要

- Task0–6：协议基础、ledger binding、typed hooks、direct-results 与正式 evidence 基础已 accepted；离线回归 provider/network=`0/0`。
- Task7–18：request identity、immutable response bank、inventory/preflight、budget/WAL、scheduler、trace-backed executor、lineage、Factor/Lean、metrics、replacement、receipt/replay 门禁已 accepted。
- Task19–28：在严格 plan-out 必要性批准后接入 trace、lineage producers、direct merge、traceability、online checks、paid authorization、pipeline 与 execution/publication gate；专项回归通过，provider/network=`0/0`，无真实 paid receipt。
- Task29–34：readiness/profile、Full-resource/L1/L2/L3/L4 边界已验收；L3/L4 仍 blocked，formal matrix 不得升级为 PASS。完整逐轮证据见 `session-handoff.md` 与 git history。

## 下一步与禁止事项

- 当前只推进 R54 canonical budget authority 修复设计：先让 representative/Full 使用相同 current Exp5 pricing binding，再移除 receipt/readiness 后的重复预算推导，并把 external-bank lineage 校验前置以跳过无意义的 Exp1 acquisition bundle 物化。实现前不得启动新的 paid run、Full、LeanAudit、force-all、正式矩阵或额外真实 API。
- 陈旧默认基线的失败只留作环境记录，不作为本轮根因、修复正确性或运行 readiness 的判断依据；后续仅运行针对 R54 authority/data-flow 的 focused tests、纯离线 artifact diff 与 provider-zero dry-run。
- 不得伪造 receipt、accepted、正确率、usage、latency、terminal 或 publication；TTFT 无持久化来源时保持 missing。
- 不联网、不 stage/commit/push/merge/PR，不做 destructive worktree 操作；V1 仍限于本地可复现实验协议内核。
