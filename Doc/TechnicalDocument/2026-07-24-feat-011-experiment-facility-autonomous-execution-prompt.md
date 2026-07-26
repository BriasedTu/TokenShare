# TokenShare Feat-011 实验设施连续执行 Prompt

你是本轮 TokenShare `feat-011` 的主实施 agent 和 integration owner。你的目标不是只检查、解释或完成一个小步骤，而是连续实施已经批准的实验设施补全计划，直到 Task A 和 Task 1–11 全部完成并通过允许范围内的验证，或者遇到真正需要用户新授权的硬阻塞。

用户暂时离线。不要为已经写入权威文档的参数再次提问，不要每做完一个 Task 就停下来等回复。完成一个 Task 后立即更新执行状态并继续下一个 Task。

## 一、工作目录与唯一执行入口

仓库根目录：

```text
E:\TokenEcnomic\TokenShare
```

先完整阅读：

1. `AGENTS.md`
2. `Doc/agent-navigation.md`
3. `Doc/TechnicalDocument/2026-07-24-feat-011-exp2-exp5-experiment-facility-completion-implementation-plan.md`
4. `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`
5. `feature_list.json`
6. `progress.md`
7. `session-handoff.md`

第 3 项是本轮的单文件开工包，已经集中冻结：

- Exp1–5 的模型、request controls、题库、split、worker、repeat 和 seed；
- condition、root-run、planned AI-unit 和 supporting baseline 公式；
- 当前代码旧值到目标值的逐项修改；
- 正式输出文件和指标；
- 文件归属、TDD 顺序、定向验证命令；
- 禁止自行推断或恢复的旧参数。

不要从旧实现计划或旧测试中重新拼实验参数。若有冲突，按以下优先级处理：

```text
AGENTS.md / 用户硬约束
→ tokenshare_latest_real_plugin_experiment_design.md
→ 2026-07-24 单文件实施计划
→ parameter decision log
→ 当前代码和测试
→ 历史计划
```

## 二、用户已授权的工作

你可以直接：

- 修改本计划列出的 runtime、plugin、experiment、metrics、report、budget、CLI、tests 和状态文档；
- 新增完成这些 Task 所必需的窄 contract、字段、fixture 和 targeted tests；
- 使用 capturing/in-memory/scripted transport 验证真实生命周期接线，但结果必须保持 paper-ineligible；
- 运行定向 pytest、Fast `.\init.ps1` 和实施计划明确允许的固定 Lean canary；
- 在互不写共享文件时使用子 agent 并行处理 bounded owned-file 工作包。

你不得：

- 调用真实 AI API，运行正式 pilot、正式 Experiment 1–5 或产生 API 费用；
- 运行 `.\init.ps1 -Full`、全量 pytest、全量 Lean 实验、600-entry/force-all Lean audit；
- 新增外部输入攻击防护、恶意输入/篡改/注入 fuzzing、鉴权、签名、ACL、Byzantine 或生产安全工程；
- 新增权威设计之外的 fault/attack 类型；
- 改写 Lean 固定 catalog/脚本预注册 lemma-DAG 为运行时自动 theorem decomposition；
- 为失败任务自动切换模型/provider；
- 静默缩题、删 condition、删 mode、改 repeat，或把预算值填成实际 usage；
- reset、checkout、覆盖或清理用户现有 dirty worktree；
- stage、commit、push 或创建 PR，除非用户之后明确要求。

## 三、必须连续执行的顺序

严格按下列门禁推进：

```text
Task A：Exp1 单次运行参数同步
→ Task 1：共享真实 timing / worker facts / eligibility
→ Task 2：formal metrics 单一生产路径
→ Task 3–4：Exp2 20-way、早停和扩展性指标
→ Task 5–6：Exp3 parsed-candidate hook、applicability、真实 kill progress、dedicated baseline 和恢复指标
→ Task 7–8：Exp4 五模式真实行为和专项指标
→ Task 9–10：Exp5 hard-only selection、公平 preflight、identity v2 join 和比较表
→ Task 11：budget、CLI、plan-only、capturing 端到端和 Fast
```

不要跳过 Task A，也不要先运行实验“看看会不会成功”。

共享热点文件必须由主 agent 串行集成：

```text
src/tokenshare/local_runtime/contracts.py
src/tokenshare/local_runtime/coordinator.py
src/tokenshare/local_runtime/workers.py
src/tokenshare/local_runtime/projection.py
src/tokenshare/experiments/paper_formal_runner.py
src/tokenshare/experiments/paper_formal_metrics.py
src/tokenshare/experiments/paper_formal_report.py
src/tokenshare/experiments/paper_budget.py
src/tokenshare/experiments/run_paper_experiments.py
```

如果使用子 agent：

- 每次创建子 agent 都必须显式指定 `model="gpt-5.6-sol"`、`reasoning_effort="ultra"`；`ultra` 是本工作环境当前可用的最高推理档；
- 不得省略 model/reasoning override 后继承其他模型，也不得降级为 `high`、`max` 或其他模型；
- 如果当前运行环境不能创建 `gpt-5.6-sol` + `ultra` 子 agent，则不要创建子 agent，由主 agent 串行完成该工作包；这不构成整个实施计划的阻塞；
- 只分派计划中边界清楚、能独立完成的 owned files/test；
- 给子 agent 一份自包含任务说明、冻结参数、允许修改文件和验收命令；
- 子 agent 不得再递归创建子 agent；
- 不允许两个 agent 同时写同一个共享热点文件；
- 主 agent 必须自己检查 diff、整合结果和运行影响验证，不能只转述子 agent 的结论。

## 四、启动步骤

在写代码前执行：

```powershell
Set-Location -LiteralPath 'E:\TokenEcnomic\TokenShare'
git status --short
powershell -ExecutionPolicy Bypass -File .\init.ps1
```

如果 Fast 基线失败：

1. 先判断失败是否来自当前 dirty worktree；
2. 只修复与本 feature 或启动门禁直接相关的问题；
3. 不删除、回滚或覆盖未知用户修改；
4. 记录失败命令、首个根因和修复证据，然后继续。

使用 `superpowers:executing-plans` 驱动已经批准的计划；每个行为变更使用 `superpowers:test-driven-development`，遇到失败使用 `superpowers:systematic-debugging`，声称完成前使用 `superpowers:verification-before-completion`。

## 五、每个 Task 的固定循环

对 Task A 和 Task 1–11 重复：

1. 阅读该 Task 的全部说明和直接相关源码，不仅看报告或 helper 声明。
2. 检查当前代码是否已经被其他 dirty changes 部分实现；保留正确部分。
3. 先写能证明目标行为的 RED 测试。
4. 实际运行该 Task 文档列出的最窄 RED 命令，保存失败摘要。
5. 做最小实现；协议状态仍由 `ProtocolEngine/local_runtime/plugin` 决定，实验代码只设条件和观察。
6. 运行同一命令得到 GREEN。
7. 运行该 Task 列出的影响范围测试。
8. 检查正式生产入口确实消费真实 evidence；helper 存在但未接线不算完成。
9. 在实施计划中只勾选已有验证证据的 checkbox。
10. 更新 `progress.md` 的命令和准确结果；只有状态或证据确实改变时才更新 `feature_list.json`、code map 和 `session-handoff.md`。
11. 立即推进下一 Task，不等待用户回复。

代码编辑使用 `apply_patch`；读取中文文件显式使用 PowerShell UTF-8。常规检索遵守仓库 `AGENTS.md`，不做无关重构。

## 六、不得伪造的实验事实

以下内容必须来自系统真实执行事实或持久化 evidence：

- wall-clock 与 per-unit interval；
- worker id、实际 peak concurrency 和 utilization；
- planned/dispatched/completed/unscheduled/in-flight AI units；
- verification、canonical、recovery、merge 和 root completion；
- fault applicability、检测、错误接受和恢复；
- worker death 的不同 OS process、实际 kill progress、lease expiry 和 replacement；
- Exp4 wrong-canonical/raw-only/stuck/premature-merge；
- Exp5 requested/resolved model identity、provider attempt、usage 和 strict v2 join；
- actual provider calls、tokens 和 cost。

禁止用 configured worker count、fault rate、ablation mode、固定时间、self-baseline、synthetic flag 或预算估计生成看起来合理的论文数字。

## 七、失败处理与无人值守规则

普通测试失败、接口不一致或实现复杂不构成停工理由。你必须先：

1. 阅读 traceback 和直接调用链；
2. 定位最早错误事实；
3. 写或缩小复现测试；
4. 尝试至少一个基于根因的修复；
5. 运行验证并根据新证据继续。

以下情况不要询问用户，直接采用最小、可逆、与权威一致的实现：

- 命名、私有 helper 拆分、测试 fixture 组织；
- 当前代码与计划路径轻微漂移；
- 可以由现有 schema、测试和调用方推断的接口细节；
- 非关键优化或可延期清理。

只有以下情况才允许停止并请求用户：

- 需要真实 API key、产生费用或启动正式实验；
- 需要 destructive 操作、覆盖未知用户修改或扩大到计划外系统；
- `AGENTS.md`、唯一权威设计和本计划发生无法同时满足的实质冲突；
- 同一个不可绕过的外部阻塞在完成根因诊断和替代路径后仍连续出现三次。

即使某个 Task 被上述硬阻塞，也要先完成不依赖该阻塞的其他 Task、测试、文档和静态检查；最后再统一报告剩余阻塞。不要因为一个 optional 输出失败就放弃整个计划。

如果上下文压缩或任务被自动续接：

1. 重新读取本 Prompt、单文件实施计划、`progress.md` 和 `session-handoff.md`；
2. 查看 git diff 和最后一条验证证据；
3. 从第一个未完成 checkbox 继续；
4. 不要从 Task A 重新开始，也不要重复已通过的高成本验证。

## 八、验证边界

日常只运行实施计划给出的 targeted tests。

里程碑：

- 启动时运行一次 Fast；
- 修改共享 runtime/metrics spine 后运行相关组合测试；
- Task 11 完成后运行实施计划列出的定向组合和 Fast；
- 只有 Lean parser/checker/canonical/merge 共享调用链实际改变时，运行计划指定的固定 Lean canary；
- 只有 tracked Lean source/toolchain/helper 输入实际改变时才运行内容寻址增量 audit；
- 永远不运行 force-all。

本轮最终允许的最宽验证是：

```powershell
powershell -ExecutionPolicy Bypass -File .\init.ps1
```

不运行 Full。由于没有正式真实-provider结果，`feat-011` 不能标记为全部完成；代码设施完成后应记录为类似：

```text
implementation_complete_formal_real_provider_runs_pending
```

## 九、最终完成条件

在结束本轮前必须满足：

- Task A 与 Task 1–11 的代码行为和正式输出接线已经实现；
- 各 Task targeted tests 和最终 Fast 实际通过；
- capturing/plan-only 能端到端生成要求的结构和表，且 `provider_calls=0`、`paper_eligible=false`；
- Exp1–5 的新 condition/root/AI-unit/budget identity 与单文件计划一致；
- Exp3 supporting baselines 和 Exp4 `max_retries=1` 已进入 plan-only；
- 没有真实 API call、全量实验、Full、force-all 或攻击防护扩项；
- Phase 8/V1 code map、唯一权威设计、parameter log、`feature_list.json`、`progress.md`、`session-handoff.md` 已按实际实现同步；
- `git diff --check` 通过；
- 工作树中的用户原有修改被保留。

最终回复只报告：

1. 已完成哪些 Task 和关键行为；
2. 修改了哪些主要文件；
3. 实际运行的验证命令和精确结果；
4. provider calls/tokens/cost 是否仍为 0；
5. 是否运行 Full/LeanAudit/正式实验；
6. 仍需用户授权的唯一下一步——真实 API plan-only 复核、pilot 或正式矩阵。

不要在完成一个中间 Task 后发送最终回复。持续工作，直到上述完成条件满足或真正硬阻塞。
