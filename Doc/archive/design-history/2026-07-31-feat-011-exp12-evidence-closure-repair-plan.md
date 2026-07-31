# FEAT-011 Exp1/2 证据闭环修复实施计划

## 约束

- 共享 dirty worktree；保留其他代理和用户已有修改。
- 历史 output 只读，不调用 API。
- 不运行完整档、LeanAudit 或攻击面测试。
- 每项生产修改前必须先新增会失败的测试并保存 RED 输出。
- 本轮不更新 `feature_list.json`、`progress.md`、`session-handoff.md`。

## 任务 1：统一 lifecycle clock 与 Exp2 critical path

1. 在现有 adapter 定向测试中新增真实 transport 同域断言和 completed factorization numeric critical path 断言。
2. 运行测试，确认因固定 `NOW` 与 attempt wall clock 冲突而 RED。
3. 新增 transport-aware clock helper，Factorization 全量接线，Lean 最小一致性接线。
4. 运行同一测试转 GREEN，并运行相邻 adapter/critical-path regressions。

## 任务 2：递归 artifact closure

1. 构造顶层 JSON artifact 引用下再嵌套 candidate/checker/model/proof refs 的测试。
2. 运行测试，确认 canonical index 缺少传递引用而 RED。
3. 将 `_materialize_artifacts` 改为稳定 BFS，复制时校验 bytes/hash/size，JSON payload 递归入队。
4. 补 fail-closed 测试并运行相关 artifact/replay/validator regressions。

## 任务 3：condition manifest

1. 新增 checkpoint 测试，要求 canonical run 根存在 condition manifest，并验证 identity digest、expected/observed/terminal denominator、status、CURRENT/generation/run refs。
2. 运行测试确认 RED。
3. 在 generation 原子发布路径中写 manifest，并纳入 canonical evidence inventory/validator。
4. 运行 checkpoint/resume/validator 定向测试转 GREEN。

## 任务 4：experiment 增量终结与 suite fail closed

1. 新增 later interruption 测试：experiment A 完成，experiment B 抛错。
2. 运行测试确认 A 仍为 `running` 或 condition rows 未落盘而 RED。
3. 抽取幂等 per-experiment finalizer；每 plan 完成即时写 terminal experiment manifest 和 rows。
4. 用 top-level PENDING intent 保护 condition rows→experiment manifest 与 runner result→suite manifest 两组提交；resume 先按 prior/target digest 修复，再严格 load。
5. suite result 缺失时从 canonical CURRENT attempts 恢复 provider/tokens/cost usage，负面 terminal root 不重新调用 provider。
6. 保持执行中的 suite `paper_eligible=false`，suite 尾部复用 helper。
7. 运行 normal/blocked/interrupted/resume 及 experiment/suite 各三阶段 crash seam 定向测试转 GREEN。

## 任务 5：smoke 分类解耦与既有 Exp1/2 回归

1. 新增技术证据完整 smoke 测试，要求无 `attempt_evidence_incomplete` 且仍不可写论文。
2. 运行测试确认 RED。
3. 以独立 `paper_ineligibility_reasons` 判断技术完整性；分类覆盖只产生独立原因，不读取覆盖后的 `paper_eligible`。
4. 运行 protocol task id model inventory、ledger exact/hash/sidecar、timeout nullable metrics CSV、condition manifest、later interruption 的 targeted regression 集。

## 任务 6：文档与交付

1. 更新 Phase 8 experiment infrastructure code map，记录新增 helper、manifest、闭包和测试位置。
2. 二次审核设计文档、代码 map 和代码中的旧表述，确认不再声称 smoke 等于技术证据缺失，也不再声称 experiment 只能 suite 末尾终结。
3. 运行最终 targeted tests，记录命令、通过数和未运行范围。
4. 审核 `git diff`，确认历史 output、状态文件及范围外文件未修改。
