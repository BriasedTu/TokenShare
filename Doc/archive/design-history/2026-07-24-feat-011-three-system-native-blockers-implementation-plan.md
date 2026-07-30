# Feat-011 Three System-Native Blockers Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修复 formal catalog view identity、selected-unit paper lifecycle bypass 和 Factorization witness 被 sibling failure 推翻三个阻塞问题。

**Architecture:** planning 将版本化 catalog execution view 冻结进 dispatch plan；selected-unit 通过通用 `ProtocolExecutionScope` 限制 coordinator/scheduler；插件通过领域无关 `MergeReadinessDecision` 告诉 coordinator 哪些 canonical child 足以 merge，所有协议事实仍由 engine/ledger 产生。

**Tech Stack:** Python 3.12、pytest、SQLite/JSONL、TokenShare `ProtocolEngine`/`ProtocolRunCoordinator`。

**实施状态（2026-07-24）：** Task 1–4 已完成。最终影响集 `336 passed`，补充 execution-runner/CLI 影响文件 `50 passed`，Fast `330 passed, 1 skipped`，固定 Lean canary `11 passed`，Full `1324 passed, 1 skipped`。全部 provider calls/tokens/cost 为 0；未运行 force-all、真实 API 或正式 Experiment 1。

---

## Task 1：冻结 catalog execution view

**Files:**

- Create: `src/tokenshare/experiments/paper_catalog_execution_view.py`
- Modify: `src/tokenshare/experiments/paper_dispatcher.py`
- Modify: `src/tokenshare/experiments/paper_runner.py`
- Modify: `src/tokenshare/experiments/paper_formal_runner.py`
- Test: `tests/experiments/test_paper_formal_runner.py`
- Test: `tests/experiments/test_paper_gate_c_dispatcher.py`

1. 新增 RED：构造同时含 Factorization/Lean 的 Exp1 plan，正式 context dispatch 第一条 Lean condition，断言旧实现抛出 selection mismatch 且 callback=0。
2. 新增 RED：篡改 resume 输入 execution view identity，必须在 callback/provider 前拒绝；正常 replay provider=0。
3. 运行精确节点并保存失败输出。
4. 实现 `PaperCatalogExecutionView.v1` 的 body/digest、manifest-backed 重建和 Exp3/Exp4 mapping view。
5. `PaperExperimentDispatchPlan.v3` 冻结 view body/digest；formal runner 从 plan 恢复而不是使用 raw manifest。
6. 运行精确 GREEN，并断言 planning/execution selection body/digest 完全相同。

## Task 2：selected-unit 统一进入 coordinator

**Files:**

- Modify: `src/tokenshare/local_runtime/contracts.py`
- Modify: `src/tokenshare/local_runtime/coordinator.py`
- Modify: `src/tokenshare/local_runtime/projection.py`
- Modify: `src/tokenshare/core/scheduling.py`
- Modify: `src/tokenshare/protocol_engine.py`
- Modify: `src/tokenshare/plugins/factorization/runtime_adapter.py`
- Modify: `src/tokenshare/plugins/lean_proof/runtime_adapter.py`
- Modify: `src/tokenshare/experiments/factorization_paper_adapter.py`
- Modify: `src/tokenshare/experiments/lean_paper_adapter.py`
- Modify: `src/tokenshare/experiments/paper_dispatcher.py`
- Modify: `src/tokenshare/experiments/paper_projection.py`
- Modify: `src/tokenshare/experiments/paper_models.py`
- Modify: `src/tokenshare/experiments/paper_runner.py`
- Modify: `src/tokenshare/experiments/run_paper_experiments.py`
- Test: `tests/experiments/test_factorization_paper_adapter.py`
- Test: `tests/experiments/test_lean_paper_adapter.py`
- Test: `tests/experiments/test_paper_gate_c_dispatcher.py`
- Test: `tests/integration/test_paper_protocol_runtime_integration.py`
- Test: `tests/local_runtime/test_coordinator_full_lifecycle.py`

1. 新增双领域 RED：selected unit 必须经过 monkeypatched coordinator，并具有 schedule/lease/request/submission/verification/canonical evidence。
2. 新增 RED：未选 sibling 无 attempt/canonical，root 无 merge/completion/settlement，结果为 partial 且 paper eligible false。
3. 新增/收紧 AST RED：experiments 不直接调用 `transition_task_unit` 或 `EventLedger.append/append_batch`。
4. 运行精确节点并保存失败输出。
5. 实现 `ProtocolExecutionScope.v1`、scheduler allowlist 和 coordinator partial terminal。
6. 把两个 adapter/CLI/dispatcher selected 路径接入同一个 coordinator helper，隔离旧生命周期。
7. 运行精确 GREEN；复跑 whole-root、fault、ablation 相邻节点。

## Task 3：Factorization 非对称 completion

**Files:**

- Modify: `src/tokenshare/local_runtime/contracts.py`
- Modify: `src/tokenshare/local_runtime/coordinator.py`
- Modify: `src/tokenshare/core/merge.py`
- Modify: `src/tokenshare/core/merge_coordinator.py`
- Modify: `src/tokenshare/plugins/factorization/schemas.py`
- Modify: `src/tokenshare/plugins/factorization/descriptor.py`
- Modify: `src/tokenshare/plugins/factorization/split_strategy.py`
- Modify: `src/tokenshare/plugins/factorization/merge_policy.py`
- Modify: `src/tokenshare/plugins/factorization/runtime_adapter.py`
- Modify: `src/tokenshare/plugins/lean_proof/runtime_adapter.py`
- Test: `tests/plugins/factorization/`
- Test: `tests/integration/test_paper_protocol_runtime_integration.py`
- Test: `tests/local_runtime/test_coordinator_full_lifecycle.py`

1. 新增 RED：accepted witness + provider error，root 仍 completed。
2. 新增 RED：accepted witness + verifier rejection，root 仍 completed。
3. 新增 RED：无 witness + terminal failure 不 merge；全 no-factor 完整 coverage 正常完成；伪 witness不完成。
4. 新增并发 RED：已经启动的 sibling attempt/evidence 保留。
5. 运行精确节点并保存失败输出。
6. 实现 `MergeReadinessDecision.v1`；Factorization plugin 决定 OR/AND，Lean 保持 all-required。
7. 将 merge task link/slot binding升级为可审计的 decision-selected canonical subset；Factorization policy/version显式升级。
8. 运行精确 GREEN，并搜索 `tokenshare.core` 不含 `found_factor` 领域判断。

## Task 4：影响验证与文档收口

**Files:**

- Modify: `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`
- Modify: `Doc/TechnicalDocument/tokenshare_v1_code_map.md`
- Modify: `Doc/TechnicalDocument/2026-06-29-phase-8-experiment-infrastructure-code-map.md`
- Modify: `feature_list.json`
- Modify: `progress.md`
- Modify: `session-handoff.md`

1. 运行用户指定的精确定向与影响 suites。
2. 运行固定 Lean canary；不运行 force-all。
3. 运行 Fast。
4. 运行 Full。
5. 以中文更新权威实验设计、code map、feature/progress/handoff，记录命令、退出码、测试数和 provider=0。
6. 二次搜索 raw catalog execution、paper-owned selected lifecycle、Factorization unconditional all-required 的当前式旧表述。
7. 运行 JSON/compile/diff 检查；不 commit、不 push，等待用户审核。
