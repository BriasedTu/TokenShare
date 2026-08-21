# TokenShare 当前进度

更新时间：2026-08-21 +08:00

本文件保留当前权威状态、最近验收锚点、资源边界和下一步；逐轮命令、评审及旧测试细节由 `session-handoff.md`、`Doc/archive/`、git history 与仓库外 `TokenShareData` 保留。

## Slim V2 当前 focus：Task 3/6已完成；Task 4/6等待启动脚本图形包装蓝图checkpoint后恢复

- **接力身份与checkpoint**：`stage=3`、`task_index=3/6`、branch=`codex/slim-v2-baseline`；Task 3参数覆盖checkpoint=`93ebf0dad2715d944a6cbe9476b71cd47096a29e`。原Task 4任务`01a0238e-b2db-7da1-9d6c-60bb12d79ecd`已创建但保持`PAUSED_FOR_UPSTREAM_CHECKPOINT`，没有基于旧蓝图进入实现写入；本轮文档checkpoint完成后由Task 3 owner向同一Task 4发送新SHA并恢复接力，不另建Task 4。
- **Task 3回答纵链已闭合**：新增`provider.py/execution.py/test_answer_paths.py`，校准`runtime.py/storage.py/projector.py`并最小调整`test_system_vertical.py`。Exp1与Exp5经同一single-attempt caller、公共`ExecutionSubmission`、两领域parser/bridge/checker跑完整root；caller显式接收call context与同一`RunStore`，send前intent、16 MiB+1、有界关闭、response/terminal、无内部retry或第二store。Exp1=`600s/300000`，Exp5按用户最新覆盖=`600s/100000`；三个thinking entry仍为`thinking_budget=32768`，价格只作普通纯投影。
- **trace/tail/resume闭合**：Exp1先写不可变`protocol.json`完整protocol-origin trace重建素材，再物化唯一protocol traces；tail只从terminal protocol的typed unscheduled集合调用缺key unit。tail timing与资源事实进入typed trace，resume不重复provider且重建完整target/recorded/success/failure/attempt/token/cost/原时间边界；正文status/runtime/正确性/protocol bytes不变。fixed adapter exact/last fallback零transport；Exp5四entry零replacement、无tail。
- **已启动失败必须计入正确率**：配置、模型身份或journal错误sticky后不重复provider/journal；若root已经启动，现有coordinator返回terminal failed result，projector落`protocol_started=true, root_status=failed, final_result_present=false, verified_correct=false`和显式provider failure，不能因无最终答案从固定分母消失。只允许协议尚未启动的preflight/config阻断不成为已启动样本；Task 4–6必须继续传递本规则，Task 5 reducer不得丢弃任何已启动失败row，Task 6停止后续condition roots前必须先commit当前失败root。
- **Task 3验证与review**：参数覆盖前主体纵链owner/spec/quality分别为`22 passed in 26.63s / 26.61s / 28.88s`，只保留为基线。用户把Exp5 `max_tokens`提高到`100000`后，四endpoint先取得预期RED，targeted=`4 passed in 8.60s`、实现者fresh=`22 passed in 26.82s`；覆盖后spec/quality首轮独立复跑分别为`22 passed in 28.44s / 27.92s`，均确认代码、测试、design spec与metrics authority参数一致，唯一`Important=1`均为覆盖后证据尚未写入本计划/progress。补录后短复核最终为`SPEC_COMPLIANT / APPROVED`且`Critical/Important/Minor/out_of_scope_by_user=0/0/0/0`；owner post-override fresh=`22 passed in 26.79s`。scoped compile、禁止依赖扫描、`git diff --check`通过，当前未解决问题=`none`，shared interface gap=`none`。现有conda环境未editable-install本仓库，验证仅为当前进程设置`PYTHONPATH=src`，没有修改共享环境/conftest。
- **运行边界**：`run_scope=representative_only`，provider/network=`0/0`；未运行真实provider、representative/full、Lean专项suite、LeanAudit、catalog全量、`lake`或`lean`，未push/merge/PR。
- **遗留dirty保护**：范围外继承`AGENTS.md`保持启动SHA256=`382A2DC37F01C25DE5156569FBC7027C3FE725DD5D5F99A5D5F7FAF0A752FAED`，禁止reset/revert/stash/delete或纳入checkpoint；提交后仍应是唯一范围外dirty。
- **启动脚本图形包装裁决**：用户明确要求的只是用薄图形窗口包装启动脚本。Task 6新增仓库根目录`run_slim_v2.cmd`和Slim-local `gui.py`：只选择`representative/full`、Exp1–5/all、run ID/output、Exp2–4必要source与resume，然后执行同一CLI。禁止计划看板、日志系统、偏好保存、任务队列、Web service、自动重试或第二runner。当前`tokenshare` conda环境已只读确认Tkinter=`8.6`。
- **影响审计与六Task状态**：Tasks 1–3=`completed`；Task 4=`paused_for_upstream_checkpoint`；Tasks 5–6=`pending`。本次变化只扩展Task 6的人机入口，不改schema、provider、scenario、reducer、指标、run目录或状态真值，因此Tasks 1–5无需返工，Task 4仍按原范围执行。文档checkpoint后唯一下一步是由Task 3 owner通知同一Task 4恢复；禁止另建Task 4、提前Task 5/6实现或运行真实provider。
- **Task 6边界只跨棒传递**：未来Task 6除原子CLI、resume、preflight和离线E2E外，只增加上述启动脚本图形包装；GUI必须映射同一CLI且同一时刻至多一个CLI子进程。preflight只允许防程序失控、磁盘耗尽、重复调用/重复付费；禁止价格/余额审批、budget authority、人工授权、publication readiness或evidence completeness，价格表缺失或变化不得阻止实验。该边界不得前移为Task 4范围。

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
