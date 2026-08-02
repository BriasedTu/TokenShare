# EPD-027 最小交接摘要（2026-08-02）

> 本摘要是 EPD-027 的覆盖式可恢复状态快照。历史 accepted 过程由 git history 保留；本文件只保存恢复所需事实。

## 当前状态

- 工作树：`C:\Users\32133\.config\superpowers\worktrees\TokenShare\codex-feat-011-epd027-pipeline`。
- 分支：`codex/feat-011-epd027-pipeline`；Task 14 代码 HEAD=`77d79a65`，contract commit=`3dc92de5`；状态/交接提交在其后。
- Active feature：`feat-011`，仍为 `in-progress`；正式矩阵保持 **NO-GO**。
- Task 0–14 已 accepted（`15/35`）；不得重做。Task 15 为 `queued/next`，尚未开始；不得进入 Task 16。
- 用户已授权每个后续 Task 持续执行“实现 → 独立综合 review → 集中修正 → follow-up”循环直至 reviewer PASS，再直接推进下一 Task。
- 每个 Task 的 post-accept 测试最小化必须分派给子智能体；监督者不得亲自执行测试内容审计，只负责范围门禁、结构化证据验收与精确提交。
- 当前没有活动写入者。provider/network 调用均为 `0`，无 paid receipt。

## Task 14 accepted 结果

- `3dc92de5`：`fix(metrics): count started replacements without success roles`，严格 3 个 contract 文件。
- `77d79a65`：`feat(metrics): add standalone Exp3 projector`，严格 2 个 Task 14 文件。
- 实现 pure Experiment 3 trace/online recovery projector：fixed denominator、完整 replacement backlink identity、same-sample paired reference、absolute overhead、worker-death completeness、ordered provider chain 与统一 online missingness。
- 禁止影子 TokenShare：metrics 只能投影 persisted event/artifact/attempt/provider facts；不得根据 condition/fault 名称模拟 state、retry、death 或 terminal。

contract digest=`sha256:b72307e727e28f5233de934df28e1701cf46ffb711b99aa845ea6f8580694b0e`。started/reassignment 只要求完整实际启动链，successful 才额外要求 qualified/completed；retry-exhausted 为 started=`1`、reassignment=`1`、success=`0`。

## 已有验证证据（不得外推）

- 接管 Fast（本轮唯一一次）：`.\init.ps1` exit `0`，`497 passed, 1 skipped in 32.15s`；JSON/SQLite/compileall 通过。
- Task 14 初始 RED：12 tests 因 module absent collection error；outer wrapper exit `0`，pytest errors=`1`，`0.29s`，不得改写为 exit `1`。
- 初始 canonical GREEN：exit `0`，`12 passed in 0.97s`。
- review-fix targeted RED：Task 14 `6 failed in 2.52s`；contract nodeid `1 failed in 0.39s`。
- 第一轮集中修正后 Task 14 canonical：exit `0`，`12 passed in 1.71s`；最终 contract full file：exit `0`，`197 passed in 15.74s`。
- 第二轮 online missingness targeted RED：exit `1`，`2 failed in 0.70s`；最终 Task 14 canonical：exit `0`，`12 passed in 1.79s`；py_compile/diff-check 通过。
- 同一独立 reviewer 最终 `PASS`，Critical/Important=`0/0`；唯一 Minor 为 audit-only ratio coercion，不阻塞。post-accept test minimization=`NO_CHANGE`。
- provider/network calls=`0`；Fast 未重复，Full/LeanAudit 未运行，无 paid receipt。

## 精确下一动作

1. 只读取实施计划 Task 15 小节及权威 Experiment 4 必要段落，不读取完整大计划。
2. 启动一个 Task 15 实现 Agent，严格限制为 `paper_exp4_metrics.py` 与对应 test；按计划 RED→最小实现→canonical GREEN。
3. 当前 Task 进入 review 后才可做下一 Task 只读预检；同一时间只允许一个写入者。

恢复期间继续遵守：无经 Task 26 校验的 paid receipt 不得调用真实 API；不 push、不 merge、不创建 PR。
