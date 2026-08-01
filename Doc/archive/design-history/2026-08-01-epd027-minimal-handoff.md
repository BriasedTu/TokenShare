# EPD-027 最小交接摘要（2026-08-01）

> 本摘要是 2026-08-02 Task 6 comprehensive final review PASS 后的 fresh 状态快照。Task 6 已 accepted，以本摘要中的 accepted 状态为当前权威。

## 1. Active feature / focus

- Active feature：`FEAT-011`。
- Active focus：`EPD-027` 全面实验设施改造；目标链路为“真实 API / 真实模型 trace → canonical TokenShare runtime → 持久化证据 → direct results → 论文指标”。
- 工作树：`C:\Users\32133\.config\superpowers\worktrees\TokenShare\codex-feat-011-epd027-pipeline`。
- 分支：`codex/feat-011-epd027-pipeline`。
- 权威实施计划：`Doc/archive/design-history/2026-08-01-feat-011-response-bank-paper-pipeline-implementation-plan.md`。
- 当前停止点：Task 0–6 已接受（`7/35`）。Task 6 已提交；Task 7 是 queued/next，尚未开始。

## 2. Task 0–34 状态

| Task | 状态 | 摘要 |
|---:|---|---|
| 0 | accepted | 离线 network tripwire；已提交。 |
| 1 | accepted | 冻结 EPD-027 profile、离线批准与 paid authority 分离；已提交。 |
| 2 | accepted | 机器可执行 paper metric contract；已通过多轮双审并提交。 |
| 3 | accepted | canonical direct-results projector；两项通用前置与 Task 3 均已复审 PASS，Task 3 已提交为 `f9773944`。 |
| 4 | accepted | exact outbound bytes / request identity / admission / Lean prompt v2；commit `0cbda1df`，最终 review PASS。 |
| 5 | accepted | immutable response-bank objects/index/opaque locator；commit `58cda71f`，最终 review PASS。 |
| 6 | accepted | semantic slot inventory / zero-engine preflight；commit `5f97034c`，final review PASS。 |
| 7 | queued | SQLite WAL atomic budget authority。 |
| 8 | queued | acquire/publish/resume/reconcile bank entries。 |
| 9 | queued | deterministic logical source-latency scheduler。 |
| 10 | queued | attempt ordinal / parent-side worker commit ABI。 |
| 11 | queued | trace-backed executor / dual provenance。 |
| 12 | queued | standalone Exp1 projector。 |
| 13 | queued | standalone Exp2 projector。 |
| 14 | queued | standalone Exp3 projector。 |
| 15 | queued | standalone Exp4 projector。 |
| 16 | queued | standalone Exp5 projector。 |
| 17 | queued | projector registry / formal metrics integration。 |
| 18 | queued | evidence classes / acquisition eligibility。 |
| 19 | queued | formal runner / preflight / scheduler / normal lifecycle。 |
| 20 | queued | numeric-cell lineage observations。 |
| 21 | queued | contract-only render/report / claim audit。 |
| 22 | queued | external bank pressure path。 |
| 23 | queued | historical real success fixture / L2 path。 |
| 24 | queued | deterministic replay / cell-lineage recompute。 |
| 25 | queued | L3 online/capability run planning。 |
| 26 | queued | validate user-supplied paid receipts。 |
| 27 | queued | bounded pipeline CLI。 |
| 28 | queued | execution gate / publication gate split。 |
| 29 | queued | supersede legacy launchers / scoped new launchers。 |
| 30 | queued | four verification profiles。 |
| 31 | queued | execute/review L1 component acceptance。 |
| 32 | queued | execute/review L2 historical-real full path。 |
| 33 | queued | conditional L3 new-real smoke under paid receipt。 |
| 34 | queued | conditional L4 cell audit / docs / handoff。 |

## 3. 已完成任务：文件、命令、退出码、证据

### Bootstrap / EOL authority（Task 前置）

- Commit：`33024c67`、`3166a860`。
- 文件：`.gitattributes` 精确 EOL 规则；未采用宽泛规则。

### Task 0 — accepted

- Commit：`ed0b056a`。
- 文件：
  - `verification/pytest_network_tripwire.py`
  - `verification/sitecustomize.py`
  - `tests/test_paper_network_tripwire.py`
- 已知验证：Task0 focused 8 passed；transport 相关定向套件 42 passed；Fast 458 passed / 1 skipped。
- 命令族：`python -m pytest -p verification.pytest_network_tripwire ... -q`；`\.\init.ps1`。
- 退出码：0。
- 证据路径：上述测试文件、commit `ed0b056a`。

### Task 1 — accepted

- Commit：`78fbddaa`。
- 文件：
  - `benchmarks/paper/epd027_pipeline_profile.v1.json`
  - `src/tokenshare/experiments/paper_pipeline_profile.py`
  - `tests/experiments/test_paper_pipeline_profile.py`
  - `src/tokenshare/experiments/paper_budget.py`
  - `tests/experiments/test_paper_budget.py`
- 已知验证：focused 177 passed；canonical-root Task1 suite 203 passed；provider calls 0。
- 命令族：`python -m pytest -p verification.pytest_network_tripwire` 加上述 Task1 测试。
- 退出码：0。
- 证据路径：上述测试文件、commit `78fbddaa`。
- 冻结 digest：profile `sha256:1e704021...0aae`；budget `sha256:1898ca13...8c69`。

### Task 2 — accepted

- Commit：`d2b16f89714b1df70295cb0a1b32bced0146ce53`。
- 文件：
  - `benchmarks/paper/paper_metric_contract.v1.json`
  - `src/tokenshare/experiments/paper_metric_contract.py`
  - `tests/experiments/test_paper_metric_contract.py`
- 验证命令：`python -m pytest -p verification.pytest_network_tripwire tests/experiments/test_paper_metric_contract.py -q`。
- 退出码：0；最终已知 195 passed；provider calls 0。
- 其他验证：target compileall、`git diff --check`，退出码 0。
- 证据路径：上述测试文件、commit `d2b16f89`。

### Task 3 — accepted

- Owner：`task3_review_fixes`；commit author=`Braised Tu`。
- Reviewer：`task3_independent_review`；最终 verdict=`PASS`，无未解决 Critical/Important。
- Prerequisite commits：
  - `860b7c48`（通用 ledger-binding，7 files changed）：最终 focused `40 passed`，独立 review PASS，provider calls `0`。
  - `4ea293b1`（typed runtime-hook observation，18 files changed）：最终 `69 passed` + `286 passed`，独立 review PASS，provider calls `0`。
- Task commit：`f9773944`（`refactor(experiments): add evidence-gated direct results`）。
- Changed files（Task 3 commit，严格四文件）：
  - `src/tokenshare/experiments/paper_direct_results.py`（新）
  - `src/tokenshare/experiments/paper_models.py`（修改）
  - `tests/experiments/test_paper_direct_results.py`（新）
  - `tests/experiments/test_paper_models.py`（修改）
- Validation：canonical focused 命令族为 `conda run -n tokenshare python -m pytest -p verification.pytest_network_tripwire tests/experiments/test_paper_direct_results.py tests/experiments/test_paper_models.py -q`；最终退出码 `0`，`101 passed`，provider calls `0`。
- Evidence：`ProtocolRunLedgerBinding` 绑定 verified ledger bytes/events/tip；`RuntimeHookObservationV1.from_dict()` 关闭三类 hook schema；direct projector 从 canonical runtime facts 与预注册 inventory 生成固定分母结果。独立审查确认正式路径没有影子 TokenShare/影子状态机，禁用 `ProtocolEngine` 时不能铸造 paper-eligible success；手工 typed fixture 仅用于组件测试。
- Post-task 测试压缩：commit `66643084`（`test(experiments): compact Task 3 regressions`），仅修改 5 个已复审测试文件，production 零修改；test defs / 估算 cases 从 `77/153` 降至 `66/118`。canonical scoped 结果为 exit `0`、`118 passed in 5.55s`，provider calls=`0`；独立 reviewer=`PASS`，Critical/Important/Minor=`0/0/0`。
- 本次压缩未重复运行 Fast、Full 或 LeanAudit；上述 canonical 结果为压缩执行阶段已接受证据。
### Task 4 — accepted

- Commit：`0cbda1df`（`feat(executors): migrate all dispatch paths to prepared ABI`），26 个 production/tests 文件。
- 验证：focused `125 passed`；canonical 初次 `323 passed / 39 failed` 后 fixture 逐步收敛；最终正式 E2E nodeid exit `0`，`1 passed in 6.68s`（total `7.635s`），日志=`%TEMP%\tokenshare_task4_canonical_single_selection_nodeid.log`；provider calls=`0`，network tripwire loaded。
- Review：最终 `PASS`，Critical/Important=`0/0`。post-accept test minimization 运行 3 分钟，结论 `NO_CHANGE`，保留 5 个 distinct tests。`py_compile`、diff-check、name-only 均 exit `0`。
- 用户批准的唯一计划外 production：`src/tokenshare/experiments/paper_formal_evidence.py` 将 immutable `protocol_task_id` 映射到 case task-id closure，映射冲突 fail closed；修复 canonical selection evidence，review 确认无 shadow protocol path。
- 本轮未运行 Fast、Full、LeanAudit，未联网，未调用 provider。

### Task 5 — accepted

- Commit：`58cda71f`（`feat(executors): add opaque external response-bank locator`），严格五文件。
- 文件：`src/tokenshare/executors/response_bank.py`、`tests/executors/test_response_bank.py`、`src/tokenshare/storage/artifacts.py`、`src/tokenshare/executors/ai_api_replay.py`、`tests/executors/test_ai_api_replay_guard.py`。
- 验证：canonical combined exit `0`，`17 passed / 0 failed / 0 skipped in 0.81s`；provider/network calls=`0`；3 个 production 文件 `py_compile` exit `0`；diff-check exit `0`。
- Review：综合 review=`PASS`，Critical/Important/Minor=`0/0/0`；post-task test minimization `<5min`=`NO_CHANGE`，保留 17 个 distinct cases。
- 本轮未运行 Fast、Full、LeanAudit，未联网，未调用 provider。

### Task 6 — accepted

- Commit：`5f97034c`（`feat(experiments): plan complete response-bank inventory`），严格五文件。
- 文件：`src/tokenshare/experiments/paper_response_bank.py`、`tests/experiments/test_paper_response_bank.py`、`src/tokenshare/experiments/paper_budget.py`、`tests/experiments/test_paper_budget.py`，以及用户授权的唯一计划外 `benchmarks/paper/epd027_pipeline_profile.v1.json`。
- 验证：canonical combined exit `0`，`44 passed in 25.14s`；network tripwire/provider calls=`0`；2 个 production 文件 `py_compile` 与 diff-check exit `0`。
- Review：comprehensive final review=`PASS`，C1 terminal conflict 与 C2 repeat-sample 跨 replacement closure 已闭合，Critical/Important=`0/0`；post-task test minimization `<5min`=`NO_CHANGE`。
- Profile：仅同步 plan authority/profile 派生 digest，budget digest 保持不变；模型、题量、repeat、fault、worker、evidence 参数均无漂移。
- 本轮未运行 Fast、Full、LeanAudit，未联网，未调用 provider。

### 最新仓库级 Fast 证据

- 2026-08-01 `.\init.ps1`：exit `0`，`468 passed, 1 skipped in 20.19s`；JSON/SQLite、harness、compileall 均通过。
- 状态同步时误把 `verification/run_verification.py` 当成无 pytest harness，实际再次进入 Fast：exit `0`，`468 passed, 1 skipped in 18.78s`。这是一次重复验证，不作为新增覆盖证据；后续不得再用该命令规避 Fast 门禁。
- 本次 Fast provider calls=`0`。Full、LeanAudit、真实 API 均未运行；Full 是按用户指令 intentionally not run。

## 4. 当前协作状态

- Task 6 已 accepted；Task 7 是下一项，保持 queued 且尚未开始。
- Task 4 之前的 `READY_AFTER_TASK3` 只作历史 provenance，已被 final review PASS 覆盖。

## 5. 当前风险 / 注意事项

1. Task 6 已闭合；complete semantic slot inventory/zero-engine preflight 已实现并通过 review。
2. Task 6 production/tests/profile 已独立提交；本次状态提交只能包含 minimal handoff、`progress.md`、`feature_list.json`、`session-handoff.md` 与 code map。
3. Task 0–6 只覆盖 `7/35`；Task 7 及以后保持 queued，`feat-011` 保持 `in-progress`，正式全量维持 NO-GO。
4. 未获用户提供且经 Task 26 校验的 paid receipt，不得调用真实 API；离线 implementation approval 不构成付费授权。

## 6. 下一批可直接派发任务

1. 保持 Task 6=`accepted` 和 accepted=`7/35`。
2. Task 7 是 queued/next，尚未开始；按严格串行门禁另行实施与复审。

## 7. 禁止命令与付费 API 门禁

- 禁止：`.\init.ps1 -Full`、`./init.sh --full`。
- 禁止：`.\init.ps1 -Full -LeanAudit`、`--lean-audit`、`--force-all-lean-audit`。
- 禁止：全量 `pytest tests`。
- 禁止：600-entry/大批 Lean 调度、正式 LeanAudit、force-all。
- 禁止：人工注入攻击、安全 fuzzing、恶意篡改防护扩展；仅做受信本地研究工作流的正确性与防旁路。
- 禁止：旧 smoke/launcher 直接启动正式实验；后续须按计划先 supersede 并建立 bounded launcher。
- 禁止：任何真实 provider dispatch，除非存在与所选 scope 精确匹配、通过 Task26 校验的用户提供 paid receipt。
- 离线 implementation approval 不能授权付费执行。
- 缺 receipt、缺 bank entry、超预算或证据不完整必须 fail closed，不得临时 scripted 生成。
- 预算硬停止线：人民币 1000 元；达到即停止新的 provider dispatch，已有证据正常收口。
- Experiment 1/5 保持真实 API；Experiment 2/3/4 按批准的两阶段 real-trace paired 设计；Exp2/Exp3 保留小型在线检查。
- 当前 state/docs 已同步到 Task 6 accepted；Task 7 保持 queued 且尚未开始，不得自动调用 API。
