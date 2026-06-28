# Phase 6 Factorization 插件代码映射

日期：2026-06-28

状态：Task 1 / Task 2 / Task 3 / Task 4 / Task 5 / Task 6 / Task 7 / Task 8 / Task 9 / Task 10 已实现并完成验证；Task 11 已完成 code map 和状态同步；Task 12 已完成插件拥有的 prompt package builder 和 request prompt ref wiring；Task 13 已完成 plugin-owned AI output parse policy descriptor metadata 和插件侧 parser policy helper；2026-06-28 已完成 Factorization 已知问题修复和回归验证。本文只映射 factorization 插件第一切片的代码、测试、规格章节和协议边界，不表示真实 Lean proof 插件、structured report stub 或 Phase 7 AI API executor 已实现。

## 1. 事实源

- 实现规格：`Doc/TechnicalDocument/2026-06-27-phase-6-factorization-plugin-field-spec.md`
- 本轮范围：第 4 节第一版边界、第 6 节 Schema version 策略、第 7 节插件 descriptor 契约、第 8 节对象字段、第 8.7.1 节 AI output parse policy / raw-only 边界、第 9 节 `DecompositionProposal` 映射、第 10 节 `MergePlan` 映射、第 11.2 节 RangeResult verifier、第 11.3 节 Merge result verifier、第 12 节端到端 prime / semiprime flow、第 13 节 artifact / replay 边界、第 15 节 Task 1 / Task 2 / Task 3 / Task 4 / Task 5 / Task 6 / Task 7 / Task 8 / Task 9 / Task 10 / Task 11 / Task 12 / Task 13，以及 2026-06-28 已知问题修复。
- 协议边界：factorization 数学规则只在 `tokenshare.plugins.factorization` 内；没有新增 `tokenshare.core` 规则、协议 event type 或 SQLite authority table。

## 2. Source Map

| 文件 | 规格章节 | 当前内容 |
|---|---|---|
| `src/tokenshare/plugins/factorization/schemas.py` | 第 6 节、第 7 节、第 8.7.1 节 | 插件 id/version、unit type、schema version、contract id、strategy id、parser id、validator policy、merge policy、parse failure schema、root subject canonical output name `factor_integer_subject` 和 schema ref helper。 |
| `src/tokenshare/plugins/factorization/models.py` | 第 6 节、第 8 节、第 15 节 Task 1 | 纯 dataclass models、canonical JSON digest helper、decimal string 校验、`FactorIntegerSubject`、`RangeResult`、`PrimeFactorizationResult` 等 Task 1 对象约束；review hardening 后拒绝 bool 冒充整数、`RangeResult.range_end` 越过 `floor_sqrt(target_n)`、重复 prime factor 条目、以及不完整 required slot set 的 final merge result。已知问题修复后，`PrimeFactorizationResult` 不再做模型层无界 `_is_prime()` 扫描，改为要求 `primality_evidence` 由 merge policy 或 direct complete 路径提供。 |
| `src/tokenshare/plugins/factorization/descriptor.py` | 第 7 节、第 8.7.1 节、第 15 节 Task 1 / Task 7 / Task 10 / Task 12 / Task 13 | 基于现有 `PluginDescriptor`、`OutputContract`、`SplitStrategyContract` 构造 factorization descriptor，声明 unit types、output contracts、split strategy、validator policy、merge policy 和 first-slice limitation metadata；Task 7 补充 `plugin_identity`、`recursive_policy_details`、`first_slice_boundary` 和 `exclusive_task_types` metadata，明确当前就是主 TDD 14.1 整数分解插件，不注册第二个 continuation / recursive factorization plugin；Task 10 新增 `first_slice_limitations_detail`，把 all-required merge readiness、factor-found 不等于 early success、不得借 Phase 5 subtree pruning 模拟 sibling pruning、以及 composite cofactor limitation reason 写成机器可读口径；Task 12 在 `mock_ai_bounded_search.prompt_package` 中声明 prompt package 必须由 `factorization.build_factor_search_prompt_package.v1` 构造，prompt owner 是 factorization plugin，executor 不得定义 prompt 或 output schema；Task 13 新增 `ai_output_parse_policy` metadata，声明 `parser_id=factorization.range_result.parser.v1`、`parse_required=true`、`raw_only_allowed=false`、`raw_output_always_persisted=true`、`parsed_schema_version=factorization.range_result.v1`、`required_output_mapping.range_result`、`parse_failure_schema=phase3.parse_failure_report.v1` 和 `verification_authority=factorization.range_result.validator.v1`。 |
| `src/tokenshare/plugins/factorization/split_strategy.py` | 第 8.3 节、第 8.4 节、第 8.5 节、第 9 节、第 10 节、第 12 节、第 15 节 Task 2 / Task 5 | `candidate_range_partition.v1` 纯分区 helper：生成稳定 `CandidateRangePartitionParams`、`CandidateRangeCoverageProof` 和非空 `FactorSearchRangeInput` ranges；覆盖 `[2, floor_sqrt(N)]` 且无 gap / overlap；review hardening 后空 candidate domain 直接拒绝，避免生成 0-child coverage。Task 5 新增 `build_factorization_split_plan()`，从同一分区结果生成 Phase 4 `DecompositionProposal` 和 `MergePlan` 纯对象，child 仅为 `factor_search_range`，coverage proof 进入 promotion guard，required merge slots 与 children 一一对应，proposal expected output 明确 `merge_slot_policy=all_required_slots` 和完整 `merge_slot_keys`，`merge_slot_id` 仅作为 Phase 4 v1 真实 slot 兼容锚点；本轮追加要求 parent final output split plan 必须覆盖完整候选域 `[2, floor_sqrt(N)]`，拒绝 partial domain；plugin payload 只保存摘要和插件自定义 validation requirements。已知问题修复后，`build_factorization_split_strategy_result()` 对 `target_n=2` / `target_n=3` 直接返回 `complete` action 和 direct-small-prime `PrimeFactorizationResult`，不生成 proposal、merge plan、child ranges 或 `TASK_EXPANDED`。 |
| `src/tokenshare/plugins/factorization/validator.py` | 第 8.6 节、第 8.7.1 节、第 8.8 节、第 11.2 节、第 15 节 Task 3 / Task 4 / Task 13 | `build_factor_search_instruction()` 构造只含 bounded range / target / schema / allowed result kinds 的 executor instruction；`parse_range_result()` 只接受结构化 dict / JSON object；`verify_range_result()` 先检查 structured `RangeResult` envelope 必填字段、schema version、计数字段和条件字段，再对 `found_factor` 和 `no_factor_in_range` 做 deterministic child input consistency 与 domain recheck；本轮新增 `no_factor_recheck_max_divisors` 预算，超过预算直接 rejected，避免无界 brute-force。已知问题修复新增 `verify_factor_integer_subject()`，校验 root canonical `FactorIntegerSubject` 必须匹配 root input artifact ref、digest、`target_n` 和 requested output。Task 13 新增 `FactorizationAIParseResult` 和 `parse_factorization_ai_output()` 纯 helper，把现有 `parse_range_result()` 包装成插件声明的 parser policy callable：成功时返回 typed `factorization.range_result` artifact body 并映射 required output `range_result`，parse failure / raw-only 时返回 `phase3.parse_failure_report.v1` artifact body 且 `candidate_output_artifact_bodies={}`。 |
| `src/tokenshare/plugins/factorization/prompt_builder.py` | 第 7 节、第 8.7 节、第 15 节 Task 12 | 新增插件拥有的 `build_factor_search_prompt_package()`；从 `FactorSearchInstruction` 和 `FactorSearchRangeInput` 构造 Phase 3 `PromptPackage`，包含 bounded range prompt text、input summary、`factorization.range_result.v1` output schema、allowed result kinds、required fields、strict JSON、verification authority 和 executor 禁止项。该 builder 不调用模型、不解析输出、不写 ledger event，也不改变 verifier / canonical / merge 权威边界。 |
| `src/tokenshare/plugins/factorization/merge_policy.py` | 第 8.8 节、第 8.9 节、第 11.3 节、第 15 节 Task 6 | 新增 `RangeSlotMergeInput`、`FactorizationMergePolicyResult` 和 `merge_required_range_results()`；只消费 all-required canonical range slots，不写 ledger event、不创建 `ExpectedOutputResolution`；校验 provided `slot_key` 与 `RangeResult.coverage_id` / `child_index` 绑定一致；完整 no-factor coverage 输出 `prime_certificate` + `PrimeFactorizationResult`，semiprime 输出 `prime_factorization_result`，composite cofactor 或 primality check 超预算只输出 unresolved `nontrivial_factor_found` 和 limitation reason。 |
| `src/tokenshare/plugins/factorization/fixtures.py` | 第 4 节、第 8.7 节、第 9 节、第 10 节、第 12 节、第 13 节、第 15 节 Task 8 / Task 9 / Task 12 | 新增 prime / semiprime fixture 端到端 flow。`run_factorization_fixture_flow()` 使用现有 `RootTaskRegistrar`、`ProtocolEngine` Phase 3 request/submission、Phase 4 verification/canonical/expand、Phase 5 merge task creation / merge resolution / contribution / parent completion / root settlement flow；不新增 event type，不修改 `tokenshare.core`。fixture executor 只处理 bounded range，所有 subject、instruction、prompt package、raw output、parsed range output、merge result 和 final `PrimeFactorizationResult` 都 artifact 化；range child `ExecutionRequest` 同时携带 `execution_instruction_ref` 和 `prompt_package_ref`，root / merge deterministic request 保持 `prompt_package_ref=null`；partial range run 只验证 all-required merge gate，不提前 merge、不剪枝 sibling ranges。已知问题修复后，root canonical bundle key 使用 `factor_integer_subject`；最终请求输出 `prime_factorization_result` 由 expand/merge 或 direct complete 路径解析，不再把 root subject 伪装成最终分解。`target_n=2` / `target_n=3` 走 direct complete；`target_n=84` 这类 composite cofactor full flow 仍保持 parent unresolved，不记录 final resolution、parent completion 或 settlement。 |
| `src/tokenshare/protocol_engine.py` | Phase 4 complete / expand 通用协议 plumbing | 已知问题修复只做通用协议校验调整：complete evidence 必须包含 canonical refs，但允许插件 direct output refs 作为额外完成证据；expand 文档校验优先从 parent `TaskUnit.plugin_payload["required_outputs"]`、`["requested_outputs"]` 或 `"requested_output"` 推导 parent required outputs，缺省才回落到 canonical output refs。这里没有加入 factorization 数学规则。 |
| `src/tokenshare/plugins/registry.py` | 第 7 节、第 15 节 Task 7 | 通用 registry 新增 `exclusive_task_types` metadata 检查；descriptor 可声明独占 task type，注册第二个支持相同 factorization task type 的插件会失败。该机制是通用元数据约束，不在 registry 中硬编码 factorization 数学规则。 |
| `src/tokenshare/plugins/factorization/__init__.py` | 第 15 节 Task 1 / Task 2 / Task 5 / Task 6 / Task 12 / Task 13 | 导出 Task 1 descriptor builder / pure models、Task 2 `partition_candidate_ranges` / `CandidateRangePartitionResult`、Task 5 `build_factorization_split_plan` / `FactorizationSplitPlanResult`、Task 6 merge policy API、Task 12 `build_factor_search_prompt_package`，以及 Task 13 `parse_factorization_ai_output` / `FactorizationAIParseResult`。 |

## 3. Test Map

| 测试文件 | 覆盖内容 |
|---|---|
| `tests/plugins/factorization/test_factorization_schemas.py` | descriptor 稳定 digest / unit types / contracts / policies / first-slice metadata；`FactorIntegerSubject` decimal string 约束；`RangeResult` 条件字段和 sqrt 上界约束；`FactorSearchRangeInput`、`RangeResult`、`CandidateRangeCoverageProof`、`PrimeFactor` 拒绝 bool 冒充 integer；`PrimeFactorizationResult` prime factors 升序、唯一、指数、乘积和显式 `primality_evidence` 约束，避免模型层无界 primality scan；`FactorizationMergeResult` final output 必须拥有完整 required slot set。 |
| `tests/plugins/factorization/test_candidate_range_partition.py` | `candidate_range_partition.v1` 覆盖 domain 无 gap / overlap、同输入 deterministic、尊重 `max_children_per_unit`、小 domain 自动收缩 `actual_child_count` 且不生成空 range；review hardening 覆盖空 candidate domain 和 bool child count 拒绝。 |
| `tests/plugins/factorization/test_factorization_parser.py` | `parse_range_result()` 接受结构化 `found_factor` JSON/dict，拒绝 free-form factor claim；`build_factor_search_instruction()` 只暴露 bounded range、target、schema、allowed result kinds 和 deterministic recheck 要求。 |
| `tests/plugins/factorization/test_factorization_prompt_package.py` | Task 12 prompt package builder：断言 `build_factor_search_prompt_package()` 返回 Phase 3 `PromptPackage`，prompt/input_summary/output_schema/constraints 均由 factorization 插件决定；prompt 明确 bounded range、strict JSON、`factorization.range_result.v1`、allowed result kinds 和禁止 executor 搜索区间外、建图、声称最终分解、发明 schema 或返回 free-form claim。 |
| `tests/plugins/factorization/test_factorization_ai_parse_policy.py` | Task 13 parse policy 测试：descriptor 必须声明 parse required / raw-only forbidden；插件 parser policy 必须把合法模型 JSON 映射为 `range_result` required output；free-form raw output 必须生成 parse failure 而非 candidate output；raw-only submission 不得被视为 successful `range_result`。 |
| `tests/plugins/factorization/test_factorization_verifier.py` | `verify_range_result()` 拒绝 range 外因子、非 divisor、target / coverage / params mismatch，并对 `no_factor_in_range` 进行 brute-force recheck；review hardening 覆盖缺少 required schema fields 或错误 schema version 的 structured dict 不能绕过模型约束；本轮新增超预算 range 直接 rejected 的回归。已知问题修复新增 root subject verifier 测试，覆盖合法 root input artifact、伪造 source ref digest、root input `target_n` mismatch。 |
| `tests/plugins/factorization/test_factorization_split_strategy.py` | Task 5 proposal / merge plan generation：只生成 `factor_search_range` children；proposal 记录 no gap / no overlap / full domain / sqrt bound checked coverage proof；merge slots 与 children 一一对应且全部 required；expected output 明确 all-required slot coverage；partial candidate domain 不得生成 parent final-output split plan；plugin payload 不携带 canonical refs、resolution status、task state 或权威 output resolution。已知问题修复覆盖 `target_n=2` / `target_n=3` 返回 direct complete，不生成 expand path。 |
| `tests/plugins/factorization/test_factorization_merge_policy.py` | Task 6 all-required merge policy：prime coverage 输出 `prime_certificate` 和可解析 `PrimeFactorizationResult`；semiprime 输出最终 `prime_factorization_result`；missing / duplicate slot 被拒绝；slot/result 绑定错位被拒绝；composite cofactor 和 primality recheck 超预算只输出 `nontrivial_factor_found`，不得解析成 final result。 |
| `tests/plugins/factorization/test_factorization_registry.py` | Task 7 descriptor registry boundary：使用现有 `PluginRegistry.freeze()` 注册并冻结单个 `factorization@0.1.0` descriptor；断言 frozen registry snapshot 和 descriptor artifact 中没有 `factorization_continuation` / `recursive_factorization` 第二插件；断言 descriptor metadata 说明主 TDD 14.1 身份、same-plugin recursive policy、composite cofactor resolution 第一版限制，以及 early success / sibling pruning 不在第一切片；本轮新增注册第二个 factorization-like descriptor 必须被 `exclusive_task_types` 拒绝。 |
| `tests/test_phase6_factorization_flow.py` | Task 8 / Task 9 prime 与 semiprime fixture 端到端 flow：prime case 展开 range children、root canonical key 为 `factor_integer_subject`、全部 canonical `no_factor_in_range`、merge 输出 `prime_certificate` 和 `PrimeFactorizationResult([97])`、parent completion / contribution / root settlement 走现有 Phase 5 events；semiprime case executor 只搜索 bounded ranges，found factor / no-factor ranges 都经过 verifier，all-required merge 后输出 `PrimeFactorizationResult([7, 13])`；small prime case `target_n=2` 直接 complete，不创建 proposal、merge plan、child ranges、`TASK_EXPANDED` 或 merge task；partial semiprime case 只 canonical 一个 range 时不创建 merge task、不写 merge record、不 settlement；composite cofactor case `target_n=84` 在 merge canonical 后保持 parent unresolved，不记录 final expected output resolution、parent completion 或 settlement；Task 12 新增断言 range child request 同时持久化 `ExecutionInstruction` 和 `PromptPackage` artifact，并且 `prompt_package_ref` 指向插件生成的 bounded range prompt。 |
| `tests/test_phase4_complete_flow.py` | Phase 4 complete impact regression：complete evidence 可以携带插件 direct output refs 作为额外完成证据，但不能丢失 canonical output refs；用于支撑 factorization small-prime direct complete 的 root subject canonical ref + final direct output ref 组合。 |
| `tests/test_phase6_factorization_limitations.py` | Task 10 first-slice limitation tests：semiprime factor-found partial run 在所有 required ranges canonical 前不创建 merge task、不写 merge record / expected output resolution / settlement，descriptor metadata 明确 `all_required_ranges_canonical` 和 `not_early_success`；完整 semiprime flow 找到 factor 后仍执行并 canonical sibling ranges，ledger 不出现 `SUBTREE_PRUNED`；composite cofactor merge 只输出 `nontrivial_factor_found` 和 `composite_cofactor_requires_future_recursive_resolution`，不得生成 final `prime_factorization_result`。 |

## 4. 验证证据

- 红灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\plugins\factorization\test_factorization_schemas.py -q` 失败，原因是 `ModuleNotFoundError: No module named 'tokenshare.plugins.factorization.descriptor'`。
- 绿灯：同一 targeted pytest 通过，结果 `4 passed in 0.07s`。
- Task 2 红灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\plugins\factorization\test_candidate_range_partition.py -q; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }` 失败，原因是 `ModuleNotFoundError: No module named 'tokenshare.plugins.factorization.split_strategy'`。
- Task 2 绿灯：同一 targeted pytest 通过，结果 `4 passed in 0.07s`。
- Task 3 红灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\plugins\factorization\test_factorization_parser.py -q; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }` 失败，原因是 `ModuleNotFoundError: No module named 'tokenshare.plugins.factorization.validator'`。
- Task 3 绿灯：同一 targeted pytest 通过，结果 `3 passed in 0.06s`。
- Task 4 红灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\plugins\factorization\test_factorization_verifier.py -q; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }` 失败，原因是 `ImportError: cannot import name 'verify_range_result' from 'tokenshare.plugins.factorization.validator'`。
- Task 4 绿灯：同一 targeted pytest 通过，结果 `4 passed in 0.07s`。
- Task 3 / Task 4 定向验证：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\plugins\factorization\test_factorization_parser.py tests\plugins\factorization\test_factorization_verifier.py -q; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }` 通过，结果 `7 passed in 0.06s`。
- Factorization 插件定向验证：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\plugins\factorization -q; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }` 通过，结果 `15 passed in 0.07s`。
- 完整启动验证：`powershell -ExecutionPolicy Bypass -File .\init.ps1` 通过，pytest collected 226 items，结果 `226 passed in 20.26s`。
- Task 5 红灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\plugins\factorization\test_factorization_split_strategy.py -q; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }` 失败，原因是 `AttributeError: module 'tokenshare.plugins.factorization.split_strategy' has no attribute 'build_factorization_split_plan'`。
- Task 5 绿灯：同一 targeted pytest 通过，结果 `4 passed in 0.08s`。
- Task 5 后 factorization 插件定向验证：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\plugins\factorization -q; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }` 通过，结果 `19 passed in 0.11s`。
- Task 5 后 compileall：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m compileall -x "reference_repos" .` 通过。
- Review hardening 红灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\plugins\factorization -q; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }` 失败，结果 `5 failed, 18 passed`；失败用例覆盖 0-child coverage、bool child count、重复 prime factor、partial final merge result、structured dict 绕过 `RangeResult` schema。
- Review hardening 绿灯：同一 factorization 插件定向验证通过，结果 `23 passed in 0.13s`。
- Review hardening 完整启动验证：`.\init.ps1` 通过，pytest collected 236 items，结果 `236 passed in 23.01s`。
- Task 7 红灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\plugins\factorization\test_factorization_registry.py -q; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }` 失败，结果 `1 failed, 1 passed`；失败原因是 descriptor metadata 缺少 `plugin_identity`，即尚未显式说明主 TDD 14.1 整数分解插件身份和递归 / first-slice 边界。
- Task 7 绿灯：同一 targeted pytest 通过，结果 `2 passed in 0.14s`。
- Task 7 后 factorization 插件定向验证：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\plugins\factorization -q; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }` 通过，结果 `25 passed in 0.12s`。
- 2026-06-28 审查修复红灯：Task 6 merge policy 文件缺失导致 factorization 定向套件收集失败，错误为 `ModuleNotFoundError: No module named 'tokenshare.plugins.factorization.merge_policy'`；单独运行 verifier / split strategy / registry 回归分别失败于 `verify_range_result()` 缺少 `no_factor_recheck_max_divisors` 参数、proposal expected output 缺少 `merge_slot_policy`、registry 未拒绝第二个 factorization-like descriptor。
- 2026-06-28 审查修复绿灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\plugins\factorization\test_factorization_merge_policy.py tests\plugins\factorization\test_factorization_verifier.py tests\plugins\factorization\test_factorization_split_strategy.py tests\plugins\factorization\test_factorization_registry.py -q` 通过，结果 `17 passed in 0.36s`。
- 2026-06-28 factorization 插件定向验证：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\plugins\factorization -q; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }` 通过，结果 `31 passed in 0.14s`。
- 2026-06-28 registry 影响面验证：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\plugins\test_plugin_registry.py tests\plugins\factorization -q; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }` 通过，结果 `32 passed in 0.15s`。
- 2026-06-28 compileall：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m compileall -x "reference_repos" .; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }` 通过。
- 2026-06-28 最终启动验证：`.\init.ps1` 通过，pytest collected 242 items，结果 `242 passed in 22.26s`。
- 2026-06-28 追加风险修复红灯：`tests\plugins\factorization\test_factorization_merge_policy.py -q` 失败，结果 `2 failed, 4 passed`，覆盖 slot/result 错位未拒绝和 `merge_required_range_results()` 缺少 `primality_recheck_max_divisors`；`test_factorization_split_strategy.py test_factorization_schemas.py -q` 失败，结果 `2 failed, 9 passed`，覆盖 partial candidate domain split plan 未拒绝和 `RangeResult.range_end` 越过 sqrt 未拒绝；schemas bool integer 定向红灯失败，结果 `1 failed, 6 passed`，覆盖 bool 冒充 `child_index`。
- 2026-06-28 追加风险修复绿灯：merge policy 定向 `6 passed in 0.13s`；schemas 定向 `7 passed in 0.12s`；split strategy + merge policy 定向 `11 passed in 0.12s`；factorization 插件全套 `36 passed in 0.18s`。
- 2026-06-28 追加风险修复最终验证：registry + factorization 影响面 `37 passed in 0.19s`；`.\init.ps1` 通过，pytest collected 247 items，结果 `247 passed in 22.29s`。
- 2026-06-28 Task 8 / Task 9 红灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\test_phase6_factorization_flow.py -q` 失败，原因是 `ModuleNotFoundError: No module named 'tokenshare.plugins.factorization.fixtures'`。
- 2026-06-28 Task 8 / Task 9 绿灯：`$env:PYTHONPATH='src'; $env:PYTHONIOENCODING='utf-8'; conda run -n tokenshare python -m pytest tests\test_phase6_factorization_flow.py -q` 通过，结果 `3 passed in 1.51s`。
- 2026-06-28 factorization + Phase 6 flow 定向验证：`$env:PYTHONPATH='src'; $env:PYTHONIOENCODING='utf-8'; conda run -n tokenshare python -m pytest tests\plugins\factorization tests\test_phase6_factorization_flow.py -q` 通过，结果 `39 passed in 1.45s`。
- 2026-06-28 Phase 4 targeted suite 回归验证：`$env:PYTHONPATH='src'; $env:PYTHONIOENCODING='utf-8'; conda run -n tokenshare python -m pytest tests\test_phase4_verification_flow.py tests\test_phase4_canonical_flow.py tests\test_phase4_split_invocation_flow.py tests\test_phase4_expand_flow.py tests\test_phase4_complete_flow.py -q` 通过，结果 `52 passed in 2.30s`。
- 2026-06-28 Phase 5 targeted suite 回归验证：`$env:PYTHONPATH='src'; $env:PYTHONIOENCODING='utf-8'; conda run -n tokenshare python -m pytest tests\test_phase5_merge_task_creation_flow.py tests\test_phase5_merge_resolution_flow.py tests\test_phase5_contribution_settlement_flow.py tests\test_phase5_subtree_pruning_flow.py tests\storage\test_phase5_event_projection.py -q` 通过，结果 `70 passed in 15.98s`。
- 2026-06-28 Task 8 / Task 9 最终启动验证：`.\init.ps1` 通过，pytest collected 250 items，结果 `250 passed in 24.93s`。
- 2026-06-28 Task 10 红灯：`$env:PYTHONPATH='src'; $env:PYTHONIOENCODING='utf-8'; conda run -n tokenshare python -m pytest tests\test_phase6_factorization_limitations.py -q` 失败，结果 `3 failed`；三个用例均失败于 descriptor metadata 缺少 `first_slice_limitations_detail`，证明限制项口径尚未机器可读。
- 2026-06-28 Task 10 绿灯：同一 targeted pytest 通过，结果 `3 passed in 0.94s`。
- 2026-06-28 Task 10 后 Phase 6 factorization 定向验证：`$env:PYTHONPATH='src'; $env:PYTHONIOENCODING='utf-8'; conda run -n tokenshare python -m pytest tests\plugins\factorization tests\test_phase6_factorization_flow.py tests\test_phase6_factorization_limitations.py -q` 通过，结果 `42 passed in 2.44s`。
- 2026-06-28 Task 11 JSON 验证：`conda run -n tokenshare python -c "import json; from pathlib import Path; data=json.loads(Path('feature_list.json').read_text(encoding='utf-8')); assert 'Doc/TechnicalDocument/2026-06-27-phase-6-factorization-plugin-code-map.md' in data['source_documents']; feat=next(f for f in data['features'] if f['id']=='feat-007'); assert feat['status']=='in-progress'; assert 'factorization_task11_status_sync_evidence' in feat; print('feature-list-json-ok')"` 通过，输出 `feature-list-json-ok`。
- 2026-06-28 Task 11 路径审计：确认本文件、`src/tokenshare/plugins/factorization/` 下 schemas / models / descriptor / split_strategy / validator / merge_policy / fixtures，以及 `tests/plugins/factorization/` 与 `tests/test_phase6_factorization_flow.py`、`tests/test_phase6_factorization_limitations.py` 均存在，输出 `factorization-code-map-paths-ok`。
- 2026-06-28 Task 11 `git diff --check`：退出码 0，无 whitespace error；仅输出工作树 LF/CRLF 转换 warning。
- 2026-06-28 Task 11 最终启动验证：`.\init.ps1` 通过，输出 `python-json-sqlite-ok`、`harness-files-ok`，pytest collected 253 items，结果 `253 passed in 23.75s`。
- 2026-06-28 Task 12 prompt package builder 红灯：`$env:PYTHONPATH='src'; $env:PYTHONIOENCODING='utf-8'; conda run -n tokenshare python -m pytest tests\plugins\factorization\test_factorization_prompt_package.py tests\test_phase6_factorization_flow.py -q` 失败，原因是 `ModuleNotFoundError: No module named 'tokenshare.plugins.factorization.prompt_builder'`。
- 2026-06-28 Task 12 初始绿灯：同一 targeted pytest 通过，结果 `5 passed in 2.15s`。
- 2026-06-28 Task 12 descriptor / public API 红灯：`$env:PYTHONPATH='src'; $env:PYTHONIOENCODING='utf-8'; conda run -n tokenshare python -m pytest tests\plugins\factorization\test_factorization_prompt_package.py tests\plugins\factorization\test_factorization_schemas.py -q` 失败，原因是 `ImportError: cannot import name 'build_factor_search_prompt_package' from 'tokenshare.plugins.factorization'`；随后同组测试还要求 descriptor 暴露 `mock_ai_bounded_search.prompt_package` 机器可读边界。
- 2026-06-28 Task 12 descriptor / public API 绿灯：同一 targeted pytest 通过，结果 `8 passed in 0.09s`。
- 2026-06-28 Task 12 后 Phase 6 factorization 定向验证：`$env:PYTHONPATH='src'; $env:PYTHONIOENCODING='utf-8'; conda run -n tokenshare python -m pytest tests\plugins\factorization tests\test_phase6_factorization_flow.py tests\test_phase6_factorization_limitations.py -q` 通过，结果 `44 passed in 2.78s`。
- 2026-06-28 Task 13 红灯：`$env:PYTHONPATH='src'; $env:PYTHONIOENCODING='utf-8'; conda run -n tokenshare python -m pytest tests\plugins\factorization\test_factorization_ai_parse_policy.py -q` 失败，原因是 `ImportError: cannot import name 'parse_factorization_ai_output' from 'tokenshare.plugins.factorization'`。
- 2026-06-28 Task 13 绿灯：同一 targeted pytest 通过，结果 `4 passed in 0.09s`。
- 2026-06-28 Task 13 后 Phase 6 factorization 定向验证：`$env:PYTHONPATH='src'; $env:PYTHONIOENCODING='utf-8'; conda run -n tokenshare python -m pytest tests\plugins\factorization tests\test_phase6_factorization_flow.py tests\test_phase6_factorization_limitations.py -q` 通过，结果 `48 passed in 2.87s`。
- 2026-06-28 已知问题修复 targeted 绿灯：已修复 root canonical output key、generic parent required output derivation、small prime direct complete、root subject verifier、模型层无界 primality scan、composite cofactor unresolved full flow 和 complete evidence 额外 direct output ref 兼容问题。`$env:PYTHONPATH='src'; $env:PYTHONIOENCODING='utf-8'; conda run -n tokenshare python -m pytest tests\plugins\factorization\test_factorization_schemas.py tests\plugins\factorization\test_factorization_split_strategy.py tests\plugins\factorization\test_factorization_verifier.py tests\plugins\factorization\test_factorization_merge_policy.py tests\test_phase6_factorization_flow.py tests\test_phase4_complete_flow.py -q` 通过，结果 `36 passed in 3.19s`。
- 2026-06-28 已知问题修复 factorization suite：`$env:PYTHONPATH='src'; $env:PYTHONIOENCODING='utf-8'; conda run -n tokenshare python -m pytest tests\plugins\factorization tests\test_phase6_factorization_flow.py tests\test_phase6_factorization_limitations.py -q` 通过，结果 `56 passed in 4.15s`。
- 2026-06-28 已知问题修复 Phase 4 impact suite：`$env:PYTHONPATH='src'; $env:PYTHONIOENCODING='utf-8'; conda run -n tokenshare python -m pytest tests\test_phase4_verification_flow.py tests\test_phase4_canonical_flow.py tests\test_phase4_split_invocation_flow.py tests\test_phase4_expand_flow.py tests\test_phase4_complete_flow.py tests\storage\test_phase4_event_projection.py -q` 通过，结果 `78 passed in 3.89s`。
- 2026-06-28 已知问题修复 Phase 5 impact suite：`$env:PYTHONPATH='src'; $env:PYTHONIOENCODING='utf-8'; conda run -n tokenshare python -m pytest tests\test_phase5_merge_task_creation_flow.py tests\test_phase5_merge_resolution_flow.py tests\test_phase5_contribution_settlement_flow.py tests\test_phase5_subtree_pruning_flow.py tests\storage\test_phase5_event_projection.py -q` 通过，结果 `70 passed in 15.86s`。
- 2026-06-28 已知问题修复 compileall：`conda run -n tokenshare python -m compileall -x "reference_repos" .` 退出码 0。
- 2026-06-28 已知问题修复后启动验证：`.\init.ps1` 通过，输出 `python-json-sqlite-ok`、`harness-files-ok`，pytest collected 268 items，结果 `268 passed in 24.09s`；状态同步前复跑基线也通过，pytest collected 268 items，结果 `268 passed in 25.57s`。

## 5. 未实现范围

- 未实现真实 Lean proof 插件、structured report stub、Phase 7 AI API executor、Phase 8 实验基础设施、fault simulation 或 metrics。
- 未声明 early success、sibling pruning 或 composite cofactor 完整递归 resolution 已完成；Task 10 已用 limitation tests 和 descriptor metadata 固化这些仍是 first-slice limitation。
- Task 12 只实现插件拥有的 prompt package builder 和 fixture request 引用；Task 13 只实现 Factorization descriptor parse policy metadata 和插件侧 parser policy helper。仍未实现真实 AI API 调用、provider 配置、API key 管理、usage / cost 记录、Phase 7 通用 AI executor parser bridge 或 replay-time AI 禁止检查，这些仍属于 Phase 7 / Phase 9 范围。

## 6. Task 11 状态同步

Task 11 只同步文档和状态，不新增或修改 Factorization source / tests。同步范围如下：

- 本文件记录 source map、test map、字段规格章节、验证证据和未实现范围。
- `feature_list.json` 只追加 Factorization 子范围完成证据，并保持 `feat-007` 为 `in-progress`；真实 Lean proof 插件和 structured report stub 仍是 `feat-007` 未完成项。
- `progress.md` 记录 Factorization Task 1-10 已实现内容、红灯 / 绿灯 targeted tests、最终 `.\init.ps1` 结果和后续未实现项。
- `session-handoff.md` 记录下一步应进入真实 Lean proof 插件或 structured report stub，不应回退到 Factorization，除非发现回归。
- `Doc/agent-navigation.md` 增加本 code map 入口，供后续确认 Factorization 第一切片的 source / tests / 协议边界。

## 7. Task 12 prompt package 同步

Task 12 是用户在 Task 11 后要求的小范围 Factorization 适配修正，用于把“prompt 应由插件确定”的设计决定落实到代码和 TDD 文档中。同步范围如下：

- `src/tokenshare/plugins/factorization/prompt_builder.py` 新增 `build_factor_search_prompt_package()`。
- `src/tokenshare/plugins/factorization/fixtures.py` 在 range child request 中保存 `PromptPackage` artifact，并把 `ExecutionRequest.prompt_package_ref` 指向该 artifact。
- `src/tokenshare/plugins/factorization/descriptor.py` 在 `mock_ai_bounded_search.prompt_package` 声明插件拥有 prompt builder、executor 不定义 prompt / output schema。
- `tests/plugins/factorization/test_factorization_prompt_package.py` 和 `tests/test_phase6_factorization_flow.py` 证明 prompt package artifact 和 request ref 存在。
- `feature_list.json`、`progress.md` 和 `session-handoff.md` 只记录 Factorization prompt package 子范围证据，不把 `feat-007` 标记为 done。

本轮路径审计必须确认以下路径存在：

- `Doc/TechnicalDocument/2026-06-27-phase-6-factorization-plugin-code-map.md`
- `src/tokenshare/plugins/factorization/schemas.py`
- `src/tokenshare/plugins/factorization/models.py`
- `src/tokenshare/plugins/factorization/descriptor.py`
- `src/tokenshare/plugins/factorization/split_strategy.py`
- `src/tokenshare/plugins/factorization/validator.py`
- `src/tokenshare/plugins/factorization/prompt_builder.py`
- `src/tokenshare/plugins/factorization/merge_policy.py`
- `src/tokenshare/plugins/factorization/fixtures.py`
- `tests/plugins/factorization/test_factorization_schemas.py`
- `tests/plugins/factorization/test_candidate_range_partition.py`
- `tests/plugins/factorization/test_factorization_parser.py`
- `tests/plugins/factorization/test_factorization_prompt_package.py`
- `tests/plugins/factorization/test_factorization_ai_parse_policy.py`
- `tests/plugins/factorization/test_factorization_verifier.py`
- `tests/plugins/factorization/test_factorization_split_strategy.py`
- `tests/plugins/factorization/test_factorization_merge_policy.py`
- `tests/plugins/factorization/test_factorization_registry.py`
- `tests/test_phase6_factorization_flow.py`
- `tests/test_phase6_factorization_limitations.py`

## 8. Task 13 parse policy 同步

Task 13 是用户在讨论真实 AI API executor 输出解析边界后确认的小范围实现，用于把“executor owns transport, plugin owns interpretation, verifier owns acceptance, ledger owns history”的规则落到 Factorization descriptor 和插件侧 parser policy helper 中。同步范围如下：

- `src/tokenshare/plugins/factorization/descriptor.py` 新增 `metadata.ai_output_parse_policy`，明确 `parse_required=true`、`raw_only_allowed=false`、`raw_output_always_persisted=true`、`parser_id=factorization.range_result.parser.v1`、parsed schema、required output mapping、parse failure schema 和 verification authority。
- `src/tokenshare/plugins/factorization/schemas.py` 新增 `RANGE_RESULT_PARSER_ID` 和 `PARSE_FAILURE_REPORT_SCHEMA_VERSION` 常量。
- `src/tokenshare/plugins/factorization/validator.py::parse_factorization_ai_output()` 复用现有 `parse_range_result()` 语义，只接受结构化 JSON object / dict 并拒绝 free-form factor claim；成功返回 typed `factorization.range_result` artifact body 和 required output name `range_result`；parse failure / raw-only 返回 `phase3.parse_failure_report.v1` artifact body，`candidate_output_artifact_bodies={}`，不得进入 verification / canonical。
- `src/tokenshare/plugins/factorization/__init__.py` 导出 `parse_factorization_ai_output` 和 `FactorizationAIParseResult`，作为后续 Phase 7 plugin parser registry / callable bridge 的最小入口。
- 通用 AI API executor 不得内置 factorization-specific JSON schema、required output mapping 或 raw-only 成功语义；它只能调用插件声明的 parser policy 并保存 artifact。
- 本轮不实现真实 AI API 调用、SiliconFlow client、provider config、API key、usage/cost、Phase 8 experiment infrastructure、replay/audit，也不把 Factorization 解析规则写入通用 executor。
