# Phase 6 Factorization 插件代码映射

日期：2026-06-27

状态：Task 1 / Task 2 已实现。本文只映射 factorization 插件第一切片的代码、测试和规格章节，不表示 Lean stub、structured report stub、parser、verifier、merge policy 或端到端 flow 已实现。

## 1. 事实源

- 实现规格：`Doc/TechnicalDocument/2026-06-27-phase-6-factorization-plugin-field-spec.md`
- 本轮范围：第 6 节 Schema version 策略、第 7 节插件 descriptor 契约、第 8 节对象字段、第 15 节 Task 1 / Task 2。
- 协议边界：factorization 数学规则只在 `tokenshare.plugins.factorization` 内；没有新增 `tokenshare.core` 规则、协议 event type 或 SQLite authority table。

## 2. Source Map

| 文件 | 规格章节 | 当前内容 |
|---|---|---|
| `src/tokenshare/plugins/factorization/schemas.py` | 第 6 节、第 7 节 | 插件 id/version、unit type、schema version、contract id、strategy id、validator policy、merge policy 和 schema ref helper。 |
| `src/tokenshare/plugins/factorization/models.py` | 第 6 节、第 8 节、第 15 节 Task 1 | 纯 dataclass models、canonical JSON digest helper、decimal string 校验、`FactorIntegerSubject`、`RangeResult`、`PrimeFactorizationResult` 等 Task 1 对象约束。 |
| `src/tokenshare/plugins/factorization/descriptor.py` | 第 7 节、第 15 节 Task 1 | 基于现有 `PluginDescriptor`、`OutputContract`、`SplitStrategyContract` 构造 factorization descriptor，声明 unit types、output contracts、split strategy、validator policy、merge policy 和 first-slice limitation metadata。 |
| `src/tokenshare/plugins/factorization/split_strategy.py` | 第 8.3 节、第 8.4 节、第 8.5 节、第 12 节、第 15 节 Task 2 | `candidate_range_partition.v1` 纯分区 helper：生成稳定 `CandidateRangePartitionParams`、`CandidateRangeCoverageProof` 和非空 `FactorSearchRangeInput` ranges；覆盖 `[2, floor_sqrt(N)]` 且无 gap / overlap；不生成 `DecompositionProposal` 或 `MergePlan`。 |
| `src/tokenshare/plugins/factorization/__init__.py` | 第 15 节 Task 1 / Task 2 | 导出 Task 1 descriptor builder / pure models，以及 Task 2 `partition_candidate_ranges` 和 `CandidateRangePartitionResult`。 |

## 3. Test Map

| 测试文件 | 覆盖内容 |
|---|---|
| `tests/plugins/factorization/test_factorization_schemas.py` | descriptor 稳定 digest / unit types / contracts / policies / first-slice metadata；`FactorIntegerSubject` decimal string 约束；`RangeResult` 条件字段约束；`PrimeFactorizationResult` prime factors 升序、指数、乘积和 primality 约束。 |
| `tests/plugins/factorization/test_candidate_range_partition.py` | `candidate_range_partition.v1` 覆盖 domain 无 gap / overlap、同输入 deterministic、尊重 `max_children_per_unit`、小 domain 自动收缩 `actual_child_count` 且不生成空 range。 |

## 4. 验证证据

- 红灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\plugins\factorization\test_factorization_schemas.py -q` 失败，原因是 `ModuleNotFoundError: No module named 'tokenshare.plugins.factorization.descriptor'`。
- 绿灯：同一 targeted pytest 通过，结果 `4 passed in 0.07s`。
- Task 2 红灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\plugins\factorization\test_candidate_range_partition.py -q; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }` 失败，原因是 `ModuleNotFoundError: No module named 'tokenshare.plugins.factorization.split_strategy'`。
- Task 2 绿灯：同一 targeted pytest 通过，结果 `4 passed in 0.07s`。
- Factorization 插件定向验证：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\plugins\factorization -q; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }` 通过，结果 `8 passed in 0.07s`。
- 完整启动验证证据记录在 `progress.md`、`feature_list.json` 和 `session-handoff.md` 的本轮状态中。

## 5. 未实现范围

- 未实现 execution instruction helper、parser、verifier、merge policy 或端到端 flow。
- 未实现 Lean stub、structured report stub、Phase 7 AI API executor、Phase 8 实验基础设施、fault simulation 或 metrics。
- 未声明 early success、sibling pruning 或 composite cofactor 完整递归 resolution 已完成；这些仍是 first-slice limitation。
