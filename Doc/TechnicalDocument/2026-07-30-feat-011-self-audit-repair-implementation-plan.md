# feat-011 自检牵连问题修复实施计划

## Task 1：锁定 RED

- 在 `test_paper_formal_metrics.py` 增加失败 source 仍校验冻结引用、聚合多来源 provenance 的测试。
- 在 `test_run_paper_experiments_cli.py` 与 `test_paper_formal_runner.py` 增加 Exp3→Exp1 逆序在输出创建/dispatch 前拒绝的测试。
- 单独运行新增节点，确认失败原因正是目标能力缺失。

## Task 2：最小实现

- 调整 shared source usage 重算，使完整与不完整 usage 都能与冻结 reference 对照。
- 将 shared reference 完整性校验移到 comparison availability 判断之前，并支持可信失败终态和空 artifact 列表。
- 聚合并输出全部 source condition ids；多来源时将兼容单值置空。
- CLI 与 formal runner 增加逆序 preflight，输出 identity 同步 fail closed。

## Task 3：验证与状态同步

- 运行新增节点、相关 metrics/runner/CLI/report/smoke 集合。
- 运行 `init.ps1` Fast。
- 同步 Phase 8 code map、`feature_list.json`、`progress.md`、`session-handoff.md`，并二次检索旧表述。
