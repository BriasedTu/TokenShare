# Feat-011 Lean 分层题型题库复核 Prompt

> 状态：给后续 reviewer / agent 的复核 prompt。本文不是实现结果，不表示当前 Lean plugin 已支持这些题型，也不替代 `tokenshare_latest_real_plugin_experiment_design.md`。
> 日期：2026-07-15

## 使用方式

把下面整段 prompt 交给下一位 reviewer / agent。任务目标是先复核怎么做，并给出正式实验扩大实施顺序；不是直接批量添加题库、修改 Lean plugin 或改 runner 执行代码。

```text
你在 Windows 仓库 E:\TokenEcnomic\TokenShare 工作。严格遵守 AGENTS.md、Doc/agent-navigation.md 和 UTF-8 PowerShell 工作流；读取中文/JSON/Markdown/代码时使用 Get-Content -Encoding UTF8 / Select-String -Encoding UTF8，不要默认使用 rg。若联网查资料，必须按 Doc/agent-navigation.md 落库并更新索引；没有联网就明确写“本轮未联网”。

背景：
- 当前 active feature 是 feat-011 Paper Real AI Experiments。
- 唯一权威实验设计是 Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md。
- 当前 benchmarks/paper/lean_catalog.v1.jsonl 全部只算 simple / shallow；历史 easy/medium/hard 标签不能支撑正式 Lean 难度主张。
- 当前 benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl 只有一个 checker-backed medium_lemma_dag golden fixture 和一个 no-oracle hard_frontier structured blocked fixture。
- 当前 Lean plugin 有 lemma-DAG certificate/proposal 数据结构，但还没有完整 recursive DAG adapter execution、proof-file assembly、dependency-aware merge/root recheck，也不能自动从任意新 theorem 发现最佳拆分。

需要复核的新题库方向：
用户已决定把当前正式 Lean 实验目标扩大为 3 个 paper difficulty × 3 个 topic_family：
- paper_difficulty: simple, medium_lemma_dag, hard_frontier
- topic_family: pure_logic, function_set, induction
- 每个 (paper_difficulty, topic_family) 单元目标 10-20 道可审计 case。

重要边界：
- 这是正式实验扩大目标，但当前只是文档层决策，不表示 runner、paper adapter 或 Lean plugin 已支持执行该矩阵。
- 题库、deterministic split/oracle 方案、adapter DAG execution 和预算复核未完成前，不要修改 runner 来假装这些 condition 已经可以运行。
- AI 输出的拆分方案不能作为协议事实。Lean 拆分必须来自 Lean plugin / deterministic catalog rule / fixed oracle package。
- 不要把 Lean 规则写进 tokenshare.core。
- 不要让实验层绕过 Lean checker。
- hard/frontier 无 oracle proof 时只能 structured_blocked / frontier_stress，不能 checker success。
- 不要破坏 Task 7/8/9 fault / worker / ablation 基础设施。

请先只做复核，不要直接实现。必须阅读：
- AGENTS.md
- Doc/agent-navigation.md
- feature_list.json
- progress.md
- session-handoff.md
- Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md
- Doc/TechnicalDocument/2026-07-13-feat-011-paper-real-ai-experiments-implementation-plan.md
- Doc/TechnicalDocument/2026-06-29-phase-8-experiment-infrastructure-code-map.md
- fixtures/lean_proof_project/TokenShare/SplitRules.lean
- fixtures/lean_proof_project/TokenShare/LemmaGraphCases.lean
- fixtures/lean_proof_project/TokenShare/LemmaGraphOracle.lean
- src/tokenshare/plugins/lean_proof/checker.py
- src/tokenshare/plugins/lean_proof/models.py
- src/tokenshare/plugins/lean_proof/split_strategy.py
- src/tokenshare/plugins/lean_proof/merge_policy.py
- src/tokenshare/experiments/paper_catalog.py
- src/tokenshare/experiments/lean_paper_adapter.py
- tests/plugins/lean_proof
- tests/experiments/test_lean_lemma_graph_catalog.py
- tests/experiments/test_paper_catalog.py
- benchmarks/paper/lean_catalog.v1.jsonl
- benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl

复核问题：
1. 当前 schema 是否足以表达 topic_family？如果不足，建议新增哪些字段，例如 topic_family、topic_family_version、construction_rule_id、oracle_package_group、proof_assembly_shape。
2. 3 × 3 × 10-20 的候选池规模是否合理？它对 P0 运行预算、plan-only provider attempt 上界、Lean checker preflight 时间和人工 oracle proof 编写成本有什么影响？
3. pure_logic / function_set / induction 三类题型分别适合哪些 Lean theorem 模板？请每类每档给出 2-3 个候选模板，标注需要的 imports、是否依赖 Mathlib、是否适合 fixed oracle package。
4. 哪些题型可以先用 deterministic catalog rule + fixed oracle package 表达，哪些必须先补 Lean plugin split/merge 规则？
5. function_set 题型中，类似“函数与集合、奇函数、D(x) subset”的题应归入 medium_lemma_dag 还是 hard_frontier？请给出判断标准。
6. induction 题型需要哪些 deterministic split/merge 能力，例如 induction skeleton、rewrite skeleton、lemma reuse、proof-file assembly。
7. hard_frontier 的可采信边界如何写入 catalog：哪些 case 可以有 oracle passed，哪些只能 structured_blocked / frontier_stress？
8. 最小实现切片应该是什么？建议先做每个 topic_family 1 个 golden case，还是先补 adapter DAG execution，再扩题库？请特别说明正式实验扩大前的 blocker 顺序。
9. 需要新增/修改哪些测试？请覆盖 loader、schema、preflight、environment_digest mismatch、oracle hash mismatch、bad oracle proof、DAG cycle、topic_family distribution、budget estimation。
10. 如果要进入实现，TDD 的 RED/GREEN 验证顺序应该是什么？

输出要求：
- 给出结论：可行 / 需降级 / 需分阶段。
- 给出推荐阶段顺序，明确哪些是实验层，哪些是 Lean plugin 内部。
- 给出建议 schema diff，但不要直接修改代码。
- 给出 3 × 3 题型矩阵的候选 case 模板表。
- 给出风险清单：Lean toolchain/mathlib 风险、oracle proof 人工成本、AI proof success 不稳定、预算扩大、checker preflight 时间、hard/frontier overclaim。
- 明确说明是否使用联网资料；如使用，先完成本地落库和索引同步。
```

## 本 prompt 的复核标准

复核者应把“3 × 3 × 10-20”理解为用户已经确认的正式实验扩大目标，但不应把它理解为可以立即写 runner 或一次性塞入 90-180 道 Lean proof case。正确判断是：先验证 schema、题型模板、oracle package 组织、adapter DAG execution 和 Lean plugin 能力，再按预算扩展正式题库与实验矩阵。

可接受的推荐通常应分阶段：

1. 先补 recursive DAG adapter execution / proof-file assembly / dependency-aware checker evidence。
2. 为 `pure_logic`、`function_set`、`induction` 各做一个 simple 或 medium golden case。
3. 用 preflight 和预算结果决定每格扩到 10 还是 20。
4. hard/frontier 只把 oracle-backed theorem 作为 passed proof case；无 oracle 的只作为 structured blocked / frontier stress。
