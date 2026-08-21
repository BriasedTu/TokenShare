---
status: approved_under_user_delegation
document: slim_v2_implementation_plan
scope: Slim V2 implementation and offline focused verification
owner: Stage 2 implementation plan owner
created: 2026-08-21
last_updated: 2026-08-21
run_scope: representative_only
---

# TokenShare Slim V2 Implementation Plan

> **Stage 2一致性勘误（2026-08-21，用户已确认）：** 保持四个representative case IDs和当前catalog不变；`lean_v2_simple_induction_direct_nat_01`按公共fixed plan的1个planned AI unit执行。Representative Exp1为19个planned units、真实call hard cap 57；加Exp5的32后总hard cap为89。该修正只更正派生文档常量，不改变数据集、实验变量、runner或shared接口。

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:subagent-driven-development` to execute this plan task-by-task. Stage 3 必须为每个 task 使用新的实现子 Agent，严格串行；每个实现完成后再依次使用新的 spec reviewer 与 code-quality reviewer。所有步骤用 checkbox（`- [ ]`）追踪。

**Goal:** 在不修改 shared code、不调用真实 provider、不运行 representative/full 的前提下，按已批准设计实现同一条可供 representative 与 full 共用的 Slim V2 管线，并完成离线/focused verification。

**Architecture:** 冻结 profile 产生普通 inventory；唯一 root runner 串行调用现有 `ProtocolRunCoordinator.run_root()`；Experiment 1/5 使用 single-entry bounded caller，Experiment 2–4 使用严格三元键 fixed trace；Slim-local projector/sink 增量落盘；reducer 只读单个普通 run 目录。所有新增 runtime code 位于 `src/tokenshare/experiments/slim_v2/`，shared core/local_runtime/plugin/executor/storage 保持只读。

**Tech Stack:** Python 3、pytest、现有 TokenShare public runtime/Factorization/Lean adapters、urllib provider transport shape、JSON/JSONL/CSV、普通文件原子 replace。

---

## 0. 执行边界与统一工作法

### 0.1 权威与禁止项

实施优先级固定为：指标权威决定实验/字段/公式；接线合同决定公共接口；设计规格决定架构、schema、profiles、恢复和资源语义；复用清单只给定点参考。实现不得重新引入 budget authority、receipt、digest/lineage closure、response-bank authority、publication gate、paper eligibility、selection digest、prepared identity、hard-deadline child gate或旧 runner/pipeline。

受信本地研究边界继续适用。基于人为伪造、手工篡改、路径/链接/SQL/JSON/prompt/命令注入、恶意 plugin/executor/provider envelope、签名鉴权、权限或 security fuzzing 的审查意见必须标记 `out_of_scope_by_user` 并拒绝实施；Experiment 3/4 的冻结 fault/challenge 不扩张成安全平台。

冻结source key精确为`case_id × source_repeat_id=0 × planned_ai_unit_id`；下游`repeat_id`、condition、worker、fault、mode和当前ordinal均不进入key。恢复中真实attempt的未知终态枚举精确为`unknown_transport_outcome`，同一ordinal禁止盲目重调。

### 0.2 每个 task 的固定执行循环

每个 task 都按以下顺序执行，不跨 task 合并：

1. Stage 3 owner 在 `progress.md` 顶部写当前 task、允许写入文件和预期 failing test；
2. 新实现子 Agent只接收该 task 的自包含 prompt，并完整加载 `pua:pua`；
3. 只写本 task 点名测试，运行“先失败命令”并保存真实失败摘要；
4. 只写本 task 点名实现，运行“通过命令”；
5. 新 spec reviewer只读核对本 task 对应设计/权威条款；
6. 新 code-quality reviewer只读核对实现质量、资源生命周期和范围；
7. owner统一修复范围内发现并 fresh 复跑通过命令；
8. owner检查 `git diff --check`、本 task diff和工作树，只提交本 task允许文件；
9. 在本计划 task evidence 表和 `progress.md` 顶部写命令、exit code、pass数、review结论与上一步implementation commit SHA，再创建仅含这两个证据文件的独立evidence commit；不得amend实现commit后留下递归失效SHA。

任一task发现必须修改shared code，立即暂停相关改动并按relay第7.1节执行三名独立reviewer代理授权法定人数，不用“临时补丁”绕过，也不询问用户。测试默认不得运行Lean专项suite、LeanAudit、全量Lean catalog、`lake`/`lean`回归；Lean接线仅用fake checker、固定lemma-DAG fixture与静态合同。Stage 3不加载真实API secret，不启动真实provider、representative或full。

### 0.3 允许写入范围

- runtime：`src/tokenshare/experiments/slim_v2/`
- focused tests：`tests/experiments/slim_v2/`
- Slim 文档/证据：`Doc/SlimV2/slim_v2_implementation_plan.md`
- 接力状态：`progress.md` 顶部

其余文件只读。profile ID 一次性固化允许按设计规格第 12 节读取当前权威 catalog，并在确需 legacy 选择局部算法时，仅对 reuse inventory 固定 SHA `3489533e79cde05d6ae2a0c9f139785249f60f09` 的点名路径/符号执行最小 `git show`；不得 checkout、runtime import或复制旧 authority字段。

每个task的`Allowed writes`只列实现/测试allowlist；`Doc/SlimV2/slim_v2_implementation_plan.md`与`progress.md`顶部是所有task唯一的常设证据例外。实现commit只含本task实现/测试allowlist；紧随其后的evidence commit只含这两个证据文件。除此之外不得写入任何文件。

## 1. 最终文件职责与 task 映射

| 文件 | 单一职责 | 创建/主要修改 task |
|---|---|---|
| `src/tokenshare/experiments/slim_v2/__init__.py` | 包版本与公开入口，不运行实验 | 1 |
| `schema.py` | V1 config/inventory/root/attempt/trace/provider普通合同与验证 | 1 |
| `case_source.py` | UTF-8 JSONL逐行加载、显式ID选择 | 2 |
| `profiles.py` | full/representative有序ID、condition/root/challenge inventory和规模/调用/磁盘计划 | 2–3, 17 |
| `sink.py` | run layout、原子文件、attempt journal、resume、日志轮转 | 4 |
| `pricing.py` | `slim_v2.pricing.2026-08-20`纯成本投影 | 5 |
| `provider.py` | local secret投影、single-entry body/16MiB bounded read/envelope/terminal journal | 6 |
| `execution.py` | provider/fixed trace结果到公共submission的两领域薄bridge | 7, 11, 16 |
| `runtime_adapter.py` | 每root独占公共对象装配、一次`run_root`、backend验收分支 | 8–9, 16 |
| `projector.py` | 当前root event/store/plugin/hook事实到`RootResultV1` | 8, 13–16 |
| `trace_source.py` | Exp1唯一trace写入及Exp2–4 exact/fallback lookup | 10 |
| `coverage_tail.py` | Exp1 terminal后逐root补齐unscheduled units | 11 |
| `scenarios.py` | Exp2 scheduler、Exp3 fault/death/扰动、Exp4 challenge/mode | 12–15 |
| `reducer.py` | 单run流式分区、纯统计、Exp1–5表与CSV/JSON | 18–22 |
| `cli.py` | 原子命令、唯一roots串行循环、run-all依赖、representative别名 | 3, 23 |

## 2. Vertical slice 与硬要求追踪

| 设计规格 slice/硬要求 | 计划 task | 通过证据 |
|---|---|---|
| 17.1 profile+plan、full IDs一次固化、condition规模、磁盘双估算 | 2–3 | profile/plan静态测试 |
| 17.2 ordinary sink+resume、跨generation首次root start | 4, 23 | crash-window/resume测试 |
| 17.3 pricing、reasoning不双算 | 5 | 价格边界测试 |
| 17.4 provider one-call、16MiB bounded network read | 6 | fake transport + close/journal测试 |
| 17.5 Factorization vertical root、Thread→bounded-process分支 | 7–9 | k>1/资源/字段测试 |
| 17.6 Exp1 trace+tail | 10–11 | trace唯一性/tail隔离/resume测试 |
| 17.7 Exp2 fixed replay | 12 | exact/fallback/0-call/1-10-50调度测试 |
| 17.8 Exp3 | 13–14 | 五fault/扰动/reference/death测试 |
| 17.9 Exp4 | 15 | 11 modes/四challenge/pair/quadruple测试 |
| 17.10 Lean bridge | 16 | fake checker/固定fixture/并发合同测试 |
| 17.11 Exp5 fake transport | 17 | 四entry/in-flight3/零重试测试 |
| 17.12 reducer | 18–22 | 153 metric IDs、CI、固定分母、流式分区 |
| 17.13 atomic CLI、representative 89-call preflight | 23 | CLI integration/preflight/resume测试 |
| schema/metric追踪与资源/secret absence | 1, 6, 18, 23 | field-path集合、metric集合、secret扫描 |

## 3. 串行 implementation tasks

### Task 1: 冻结普通 schema 合同

**目标：** 建立所有后续组件共享的最小V1普通数据对象和字段级验证；不装配runtime、不读catalog。

**Files:**

- Create: `src/tokenshare/experiments/slim_v2/__init__.py`
- Create: `src/tokenshare/experiments/slim_v2/schema.py`
- Create: `tests/experiments/slim_v2/test_schema.py`
- Create: `tests/experiments/slim_v2/fixtures/authority_contract.v1.json`

**Tests / exact names:**

- `test_root_result_normalized_leaf_paths_equal_authority_contract`
- `test_full_schema_leaf_paths_equal_authority_plus_operational_contract`
- `test_attempt_and_trace_schema_keep_actual_source_and_simulated_fields_distinct`
- `test_nullable_fields_require_missing_or_not_applicable_reason`
- `test_exp2_to_exp4_actual_provider_fields_are_null_and_provider_call_is_false`
- `test_schema_contains_no_forbidden_authority_fields`

**Dependencies:** none。

**Allowed writes:** 仅上述四个文件。

- [x] **Step 1: 写字段集合失败测试。** fixture分别显式保存`metric_authority_leaf_paths`、`slim_operational_leaf_paths`、153个正式metric IDs和必填/可空规则。前者逐项来自指标权威第8节；后者只含设计规格额外冻结的运行字段，例如`trace_tail_success_unit_count/trace_tail_failure_unit_count`、`attempts[].raw_response_relative_path`与`attempts[].call_state`。测试要求root的metric projection与authority集合精确相等、完整schema与两集合冻结并集精确相等，不能用实现反向生成fixture或只比数量。
- [x] **Step 2: 运行先失败命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_schema.py -q`

  Expected: FAIL，首个失败为无法导入 `tokenshare.experiments.slim_v2.schema` 或缺少 `RootResultV1`；不得是fixture JSON/UTF-8错误。

- [x] **Step 3: 最小实现。** 在`schema.py`定义并验证`SlimRunConfigV1,RootInventoryV1,AttemptResultV1,UnitTraceV1,RootResultV1,ProviderEntryViewV1,ProviderRequestControlV1,ProviderCallResultV1`；字段名、嵌套数组、时间单位、null reason与`call_state`严格按设计规格第7–9节。`__init__.py`只暴露schema版本。
- [x] **Step 4: 运行通过命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_schema.py -q`

  Expected: PASS，6 tests passed。

**完成证据：** failing/pass输出、normalized leaf set差集为空、禁止字段扫描为空、task commit SHA。

**Progress更新点：** 记录Task 1为完成、schema version和测试pass数；下一focus写Task 2。

### Task 2: 固化case读取与full/representative有序ID

**目标：** 用当前catalog与获准一次性选择规则把full/representative全部有序case IDs作为Slim-local只读常量固化；runtime不再读取旧selection/profile。

**Files:**

- Create: `src/tokenshare/experiments/slim_v2/case_source.py`
- Create: `src/tokenshare/experiments/slim_v2/profiles.py`
- Create: `tests/experiments/slim_v2/test_case_source.py`
- Create: `tests/experiments/slim_v2/test_profiles.py`

**Tests / exact names:**

- `test_load_cases_streams_utf8_jsonl_and_rejects_duplicate_case_id`
- `test_select_cases_by_ids_preserves_frozen_order_and_reports_missing_ids`
- `test_full_case_ids_are_literal_unique_and_exist_in_current_catalogs`
- `test_full_case_ids_match_all_authority_strata_and_counts`
- `test_full_literal_ids_produce_exact_one_thousand_nine_hundred_seventy_planned_units`
- `test_exp3_and_exp4_case_id_tuples_match_frozen_selection_rules_exactly`
- `test_representative_case_ids_and_strata_are_exact`
- `test_runtime_profiles_do_not_read_legacy_selection_files`

**Dependencies:** Task 1。

**Allowed writes:** 仅上述四个文件。

- [x] **Step 1: 写失败测试。** 测试从`benchmarks/paper/factorization_catalog.v2.jsonl`与`benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl`只读核对`profiles.py`字面tuple；独立重算本task冻结规则并要求tuple逐项相等，不调用production选择helper。明确断言Exp1 300+135且planned units=1,970、Exp2 hard 50、Exp3/4 shared 50+3或50+15、Exp5 42+12的ID唯一性/分层/顺序，设计规格第13节四个representative IDs，以及代表性Lean unit counts=`1/2`、Exp1 total units=19。
- [x] **Step 2: 运行先失败命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_case_source.py tests/experiments/slim_v2/test_profiles.py -q`

  Expected: FAIL，缺`load_cases/select_cases_by_ids`或frozen ID constants。

- [x] **Step 3: 一次性生成并人工审查字面ID。** 本task不读取任何archive JSON，完全使用两个当前权威catalog和以下冻结规则：Factorization先按`sha256("slim_v2.full.v1|seed=20260820|domain=factorization|difficulty="+difficulty+"|case_id="+case_id)`升序、再按`catalog_ordinal,case_id`破同分；easy/medium各取前100，hard先按给定顺序放`factor_v2_hard_138,factor_v2_hard_145`，再接排名中其余case并取100。Exp2为Exp1 hard前50；Exp3/4 Factorization分别取Exp1 easy/medium/hard前`17/17/16`。Lean每个`paper_difficulty×topic_family`同样用上述字符串把domain替换为`lean`排序；simple/pure_logic先放`lean_v2_simple_pure_logic_direct_prop_01`，simple/induction先放`lean_v2_simple_induction_direct_nat_01`，再接排名其余case；每格取15形成Exp1。Exp3 Lean逐topic取simple格第1题；Exp4每difficulty按`pure_logic/function_set/induction=2/2/1`取各格前N；Exp5为Exp1 Factorization hard前42及Lean hard三个topic各前4。生成后把所有tuple作为只读字面常量写进`profiles.py`；runtime不得包含选择算法，也不得打开`paper_suite_scale_300_50_54.v1`、`exp5_parent_quarter_selection.v4`或其他旧selection/profile。
- [x] **Step 4: 最小实现。** `case_source.py`提供逐行`load_cases(path)`与`select_cases_by_ids(iterable, ordered_ids)`；`profiles.py`只先提供字面ID/stratum常量和representative challenge rows，不展开conditions。
- [x] **Step 5: 运行通过命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_case_source.py tests/experiments/slim_v2/test_profiles.py -q`

  Expected: PASS，8 tests passed；全部tuple精确、planned unit count闭合、无legacy runtime read。

**完成证据：** 两个当前catalog核对通过、冻结规则在test侧独立重算且与literal tuples逐项相等、所有ID差集/重复集合为空、task implementation commit SHA。

**Progress更新点：** 记录full ID已一次性固化及各实验ID count；下一focus写Task 3。

### Task 3: 展开profile inventory、规模上限与无provider `plan`

**目标：** 生成canonical conditions/roots/references/challenges，并计算所有attempt/provider/disk上限；实现只读`plan`命令。

**Files:**

- Modify: `src/tokenshare/experiments/slim_v2/profiles.py`
- Create: `src/tokenshare/experiments/slim_v2/cli.py`
- Modify: `tests/experiments/slim_v2/test_profiles.py`
- Create: `tests/experiments/slim_v2/test_cli_plan.py`

**Tests / exact names:**

- `test_full_condition_and_root_counts_match_canonical_inventory`
- `test_full_attempt_and_provider_call_hard_caps_are_derived_from_inventory`
- `test_full_hard_upper_includes_four_hundred_twenty_six_point_seventeen_gib_online_term`
- `test_exp3_reference_inventory_is_separate_from_paper_denominator`
- `test_exp4_full_challenge_quotas_and_mode_expansion_are_exact`
- `test_representative_inventory_and_provider_call_hard_cap_are_exact`
- `test_plan_prints_estimate_and_hard_upper_bytes_without_loading_secret_or_provider`
- `test_plan_reports_exp2_and_exp3_reference_ceilings_from_frozen_units`

**Dependencies:** Task 2。

**Allowed writes:** 仅上述四个文件。

- [ ] **Step 1: 写失败测试。** Full精确断言论文roots 7,554、加references executions 7,660、Exp3 planned 17,148/attempt upper 54,372、Exp4 9,702/15,876、真实call hard cap 10,902；representative断言Exp1 4、Exp2 12、Exp3 8+2 refs、Exp4 44、Exp5 4，Exp1 planned units 19及真实call hard cap 89。89是从冻结inventory派生的preflight硬上限，实际早停可更低。
- [ ] **Step 2: 运行先失败命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_profiles.py tests/experiments/slim_v2/test_cli_plan.py -q`

  Expected: FAIL，缺`build_profile/build_inventory/build_plan`或`cli plan`。

- [ ] **Step 3: 最小实现profile。** condition ID按设计规格第7.4节固定字段顺序编码；显式保存所有`max_retries,continue_after_terminal_child_failure,worker_count,source_repeat_id`，其中全部实验`continue_after_terminal_child_failure=false`，不借public默认值。Exp1 entry/model=`deepseek_v4_pro_exp1_baseline/deepseek-v4-pro`、thinking high、timeout600、max_tokens300000、worker10、max_retries2；Exp2 workers=`1,3,7,10,30,50`、repeats0/1、max_retries2；Exp3 worker10、max_retries2；Exp4 worker10、max_retries1；Exp5 worker10、max_retries0。Exp3 references写独立inventory；Exp4 plan在mode前生成且满足full quota与representative四行。
- [ ] **Step 4: 最小实现plan。** 只从profile/catalog算root、planned/simulated/protocol/provider ceilings；按第14.3节同时输出p95 estimate和`B=16MiB`硬上界、integer bytes/GiB、每root余量；禁止解析secret、构造caller或检查价格/余额。
- [ ] **Step 5: 运行通过命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_case_source.py tests/experiments/slim_v2/test_profiles.py tests/experiments/slim_v2/test_cli_plan.py -q`

  Expected: PASS，本task 8 tests及Task 2 case-source/profile既有tests全部通过。

**完成证据：** full/representative规模表、89与10,902上限、磁盘estimate/hard-upper输出、secret/provider spy零调用、task commit SHA。

**Progress更新点：** 记录inventory恒等式与`plan`输出；下一focus写Task 4。

### Task 4: 普通原子sink、attempt journal与resume

**目标：** 实现普通文件布局、原子canonical写、完成主键扫描、generation与日志上限；不接runtime。

**Files:**

- Create: `src/tokenshare/experiments/slim_v2/sink.py`
- Create: `tests/experiments/slim_v2/test_sink_resume.py`

**Tests / exact names:**

- `test_atomic_json_replace_never_exposes_partial_canonical_file`
- `test_existing_same_key_same_payload_is_idempotent_but_conflict_stops`
- `test_result_file_wins_when_state_lags_after_crash`
- `test_terminal_attempt_is_reused_and_in_flight_becomes_unknown_without_same_ordinal_retry`
- `test_first_root_start_survives_new_generation_and_runtime_uses_final_terminal`
- `test_log_rotation_is_bounded_to_five_files_and_redacts_raw_prompt_and_secret`
- `test_all_handles_and_temp_files_close_after_each_write`
- `test_application_file_handle_limit_is_one_hundred_twenty_eight`

**Dependencies:** Task 1。

**Allowed writes:** 仅上述两个文件。

- [ ] **Step 1: 写故障注入测试。** 在intent、response、result、state commit四个窗口注入异常；用fake process liveness证明unknown transport不盲调；无递归删除断言。
- [ ] **Step 2: 运行先失败命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_sink_resume.py -q`

  Expected: FAIL，缺`RunLayout/AtomicJsonSink/AttemptJournal/ResumeIndex`。

- [ ] **Step 3: 最小实现。** 同目录unique temp→UTF-8 write→flush/fsync→close→replace；canonical冲突停止；result存在即terminal；首次`root_start_at_ms`跨generation只读；unknown temp仅移动到`state/abandoned_tmp`；Slim app级文件句柄上限128；日志32MiB×5，message≤4KiB。
- [ ] **Step 4: 运行通过命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_sink_resume.py -q`

  Expected: PASS，8 tests passed，测试结束无open handle/background process。

**完成证据：** 四crash窗口输出、conflict错误、generation timing断言、handle/temp/log上界、task commit SHA。

**Progress更新点：** 记录普通主键和crash语义；下一focus写Task 5。

### Task 5: 冻结pricing纯函数

**目标：** 只根据raw usage/model/request UTC投影固定CNY成本；价格不参与运行准入。

**Files:**

- Create: `src/tokenshare/experiments/slim_v2/pricing.py`
- Create: `tests/experiments/slim_v2/test_pricing.py`

**Tests / exact names:**

- `test_deepseek_peak_half_open_boundaries_use_attempt_request_start`
- `test_deepseek_off_peak_cache_split_cost_matches_authority`
- `test_siliconflow_four_endpoint_prices_match_authority`
- `test_qwen_prompt_tokens_all_use_input_price_without_free_cache_assumption`
- `test_reasoning_tokens_are_completion_subset_and_never_double_charged`
- `test_missing_required_usage_returns_null_reason_without_blocking_run`
- `test_exp3_simulated_cost_keeps_source_tier_and_unperturbed_prompt_cache`
- `test_inconsistent_prompt_cache_split_returns_null_usage_cost_reason`
- `test_inconsistent_total_prompt_completion_returns_null_usage_cost_reason`

**Dependencies:** Task 1。

**Allowed writes:** 仅上述两个文件。

- [ ] **Step 1: 写数值失败测试。** 覆盖09:00/12:00/14:00/18:00 Asia/Shanghai半开边界、四SiliconFlow endpoint、cache缺失、reasoning诊断与Exp3模拟completion替换；另断言`prompt_tokens=cache_hit+cache_miss`和`total_tokens=prompt+completion`，任一不一致时受影响usage/cost为null并写固定reason，且不阻止root。
- [ ] **Step 2: 运行先失败命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_pricing.py -q`

  Expected: FAIL，缺`project_actual_cost/project_simulated_cost`。

- [ ] **Step 3: 最小实现。** 版本常量固定为`slim_v2.pricing.2026-08-20`。CNY/1M：DeepSeek off-peak hit/miss/output=`0.15/4.50/13.50`、peak=`0.30/9.00/27.00`；GLM=`2.00/8.00/28.00`、Qwen input/output=`0.50/2.00`且无独立hit、MiniMax=`0.21/2.10/8.40`、SiliconFlow DeepSeek=`0.20/2.00/8.00`。峰段为Asia/Shanghai `[09:00,12:00)`与`[14:00,18:00)`；只计完整completion一次；缺输入返回`cost=None,usage_status,missing_reason`，不抛准入错误、不联网。
- [ ] **Step 4: 运行通过命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_pricing.py -q`

  Expected: PASS，9 tests passed。

**完成证据：** 边界数值表、reasoning不双算断言、network/budget spy零调用、task commit SHA。

**Progress更新点：** 记录pricing版本与数值测试；下一focus写Task 6。

### Task 6: single-entry bounded provider one-call

**目标：** 用fake transport闭合Exp1/5将实际使用的一次调用能力、16MiB网络读取上限、journal、model/usage和secret边界；不发真实请求。

**Files:**

- Create: `src/tokenshare/experiments/slim_v2/provider.py`
- Create: `tests/experiments/slim_v2/test_provider.py`
- Create: `tests/experiments/slim_v2/fixtures/provider_envelopes.json`

**Tests / exact names:**

- `test_load_single_entry_view_ignores_selector_pricing_digest_and_moves_plain_key_to_env`
- `test_call_once_deepseek_builds_frozen_body_and_persists_intent_response_terminal`
- `test_call_once_siliconflow_extracts_raw_usage_reasoning_latency_and_model`
- `test_call_once_never_retries_transport_http_envelope_or_parser_failure`
- `test_bounded_read_closes_response_at_sixteen_mib_plus_one_and_records_terminal_error`
- `test_http_response_closes_exactly_once_on_success_http_error_and_envelope_error`
- `test_null_or_non_string_content_is_provider_envelope_invalid`
- `test_resolved_model_missing_or_mismatch_is_explicit_condition_stop_result`
- `test_provider_outputs_and_errors_contain_no_api_key_or_authorization_header`

**Dependencies:** Tasks 1, 4。

**Allowed writes:** 仅上述三个文件。

- [ ] **Step 1: 写fake transport失败测试。** fake response逐块提供16MiB+1、记录`close()`、计数send次数；成功、HTTP error、envelope/parser error与超限四类terminal路径均断言response `close()`恰好一次；fake clock固定request UTC与`perf_counter`；secret sentinel扫描所有journal/raw/error。
- [ ] **Step 2: 运行先失败命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_provider.py -q`

  Expected: FAIL，缺`load_provider_entry_view/BoundedUrlLibTransport/call_once`。

- [ ] **Step 3: 最小实现。** 复用当前public body builders/envelope parsers和`AIAPIProviderEntry`形状；Slim-local urllib读取`response_max_bytes+1`，其中`response_max_bytes=16 MiB`；从取得response起用单一`try/finally`覆盖读取、持久化与解析，所有terminal路径恰好close一次；一次调用无内部retry；intent在send前原子写，response先于terminal；错误有界4KiB且不含header/secret。
- [ ] **Step 4: 运行通过命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_provider.py -q`

  Expected: PASS，9 tests passed，fake send count每case精确为0或1，response close count精确为1。

**完成证据：** 16MiB+1 close、一次调用计数、terminal journal、model identity、secret扫描、task commit SHA。

**Progress更新点：** 记录provider只经fake验证且真实call=0；下一focus写Task 7。

### Task 7: provider/fixed结果到公共submission的薄execution bridge

**目标：** 把caller结果或预构造fixed selection转换为Factorization/Lean公共submission，保留raw/parse/failure；不运行root。

**Files:**

- Create: `src/tokenshare/experiments/slim_v2/execution.py`
- Create: `tests/experiments/slim_v2/test_execution.py`

**Tests / exact names:**

- `test_factorization_provider_result_parses_and_builds_execution_submission`
- `test_lean_provider_result_normalizes_proof_submission_with_fake_checker_boundary`
- `test_parse_failure_keeps_raw_response_and_does_not_call_provider_again`
- `test_provider_failure_projects_explicit_result_kind_and_usage_status`
- `test_fixed_submission_never_exposes_transport_entrypoint`

**Dependencies:** Tasks 1, 6；fixed selection使用测试内对象，Task 10再接真实trace source。

**Allowed writes:** 仅上述两个文件。

- [ ] **Step 1: 写两领域失败测试。** 使用最小公共`ExecutionRequest` fixture，spy断言provider结果只解析一次，fixed path无法调用transport。
- [ ] **Step 2: 运行先失败命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_execution.py -q`

  Expected: FAIL，缺`ProviderExecutionBridge/FixedSelectionExecutionBridge`。

- [ ] **Step 3: 最小实现。** provider bridge调用领域parser并构造系统`ExecutionSubmission`；Lean再走公开normalize边界；raw refs/parse failures/usage/model/result kind完整；bridge不做重试、价格、领域正确性或scenario选择。fixed bridge只消费显式selection对象。
- [ ] **Step 4: 运行通过命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_execution.py -q`

  Expected: PASS，5 tests passed。

**完成证据：** 两领域submission字段快照、parser/provider失败路径、fixed transport spy零调用、task commit SHA。

**Progress更新点：** 记录execution bridge边界；下一focus写Task 8。

### Task 8: Factorization单root垂直接线与基础projector

**目标：** 用fake provider完成`case → 每root独占对象 → 一次run_root → RootResultV1`，验证真实Factorization plugin/runtime但不调用网络。

**Files:**

- Create: `src/tokenshare/experiments/slim_v2/runtime_adapter.py`
- Create: `src/tokenshare/experiments/slim_v2/projector.py`
- Create: `tests/experiments/slim_v2/test_runtime_adapter.py`
- Create: `tests/experiments/slim_v2/test_projector_schema.py`

**Tests / exact names:**

- `test_factorization_single_root_calls_public_coordinator_once_and_checks_final_product`
- `test_each_root_owns_distinct_store_ledger_engine_plugin_backend_and_coordinator`
- `test_root_lifecycle_start_precedes_dispatch_and_runtime_equals_terminal_minus_start`
- `test_projector_joins_current_task_events_store_submission_verification_and_canonical`
- `test_protocol_completed_does_not_replace_independent_verified_correct`
- `test_infrastructure_exception_still_projects_one_fixed_denominator_row`
- `test_projected_leaf_paths_match_schema_contract`
- `test_projector_propagates_inconsistent_usage_as_null_with_fixed_reason_without_blocking_root`
- `test_valid_deepseek_usage_projects_and_persists_pricing_version_tier_and_cost`

**Dependencies:** Tasks 1, 4, 5, 7。

**Allowed writes:** 仅上述四个文件。

- [ ] **Step 1: 写fake-provider root失败测试。** 使用一个小Factorization case、fake submission和真实public coordinator/plugin；spy要求`run_root`调用一次、两个roots对象identity不同；有效DeepSeek usage在一次真实projector路径中必须调用Task 5 pricing纯函数，并把`pricing_version,pricing_tier,cost_estimate_cny`同时写入attempt与root资源投影。
- [ ] **Step 2: 运行先失败命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_runtime_adapter.py tests/experiments/slim_v2/test_projector_schema.py -q`

  Expected: FAIL，缺`run_factorization_root/project_root_result`。

- [ ] **Step 3: 最小runtime装配。** 严格按设计规格4.1创建root独占ArtifactStore/EventLedger/ProtocolEngine/adapter/bridge/backend/coordinator/request；显式设置retries、timing policy、whole_root和continue flag；调用一次run_root。
- [ ] **Step 4: 最小projector。** 只读当前task events/store/plugin/journals；独立乘积检查；按最早失败边界分类；有效actual usage调用Task 5 pricing并持久化version/tier/cost到attempt/root，usage关系不一致时把受影响usage/cost投影为null+固定reason但不改变root实验结果；忽略ledger binding和防伪refs语义；异常也生成固定身份row。
- [ ] **Step 5: 运行通过命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_runtime_adapter.py tests/experiments/slim_v2/test_projector_schema.py tests/experiments/slim_v2/test_pricing.py -q`

  Expected: PASS，本task 9 tests与pricing纯函数合同全部通过。

**完成证据：** coordinator call count=1、对象不跨root、lifecycle恒等式、field set闭合、task commit SHA。

**Progress更新点：** 记录首条Factorization离线垂直路径；下一focus写Task 9A。

### Task 9A: Thread backend验收与领域决定冻结

**目标：** 只用Factorization真实k>1与Lean fake/固定fixture判断每个领域的Thread backend是否保持事实完整，并把representative/full共用的领域决定冻结；本task不实现process facade。

**Files:**

- Modify: `src/tokenshare/experiments/slim_v2/runtime_adapter.py`
- Modify: `tests/experiments/slim_v2/test_runtime_adapter.py`
- Create: `tests/experiments/slim_v2/fixtures/lean_fake_case.json`

**Tests / exact names:**

- `test_factorization_thread_backend_k_gt_one_preserves_unit_attempt_and_canonical_facts`
- `test_lean_thread_backend_fixed_dag_fake_checker_has_no_state_loss_or_cross_unit_mix`
- `test_backend_decision_is_frozen_per_domain_for_representative_and_full`
- `test_thread_acceptance_failure_records_domain_fallback_reason_without_shared_changes`

**Dependencies:** Task 8。

**Allowed writes:** 仅上述三个文件；shared runtime/plugin/executor只读。

- [ ] **Step 1: 写Thread验收失败测试。** 分别运行Factorization k=3/10与Lean fixed-DAG fake-checker k=3/10，断言unit/attempt/canonical/checker身份；确定性fixture产生事实丢失时只记录该领域fallback reason，不构造process backend。
- [ ] **Step 2: 运行先失败命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_runtime_adapter.py tests/experiments/slim_v2/test_projector_schema.py -q`

  Expected: FAIL，缺领域Thread acceptance与冻结decision；不得调用Lean二进制。

- [ ] **Step 3: 最小实现。** 每领域执行同一focused acceptance合同；通过则冻结`thread`，失败则冻结`bounded_process_required`及确定性原因。同领域representative/full读取同一decision；worker-death固定process，不进入本分支。
- [ ] **Step 4: 运行通过命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_runtime_adapter.py tests/experiments/slim_v2/test_projector_schema.py -q`

  Expected: PASS，本task 4 tests与Task 8 runtime/projector合同全部通过，Lean subprocess spy=0。

**完成证据：** 两领域Thread验收事实、冻结decision/fallback reason、rep/full identity、task implementation commit SHA。

**Progress更新点：** 写明每领域Thread验收结论；下一focus写Task 9B。

### Task 9B: Slim-local bounded-process facade与统一资源生命周期

**目标：** 只为Task 9A判定需要fallback的领域实现bounded-process facade，并验收physical pool、queue、permit、buffer、句柄和结束清理；shared保持只读。

**Files:**

- Modify: `src/tokenshare/experiments/slim_v2/runtime_adapter.py`
- Modify: `src/tokenshare/experiments/slim_v2/execution.py`
- Create: `tests/experiments/slim_v2/test_resource_bounds.py`

**Tests / exact names:**

- `test_thread_failure_selects_bounded_process_facade_without_shared_changes`
- `test_exp2_logical_capacity_fifty_uses_at_most_ten_physical_processes_and_queue_fifty`
- `test_parent_shared_provider_permits_bound_deepseek_ten_and_siliconflow_three`
- `test_backend_finally_closes_manager_queue_semaphore_threads_processes_and_permits`
- `test_response_and_fixed_trace_buffers_respect_one_hundred_sixty_forty_eight_and_one_hundred_twenty_eight_mib_bounds`
- `test_application_file_handles_never_exceed_one_hundred_twenty_eight`
- `test_fixed_trace_content_read_semaphore_is_capped_at_eight`

**Dependencies:** Task 9A。

**Allowed writes:** 仅上述三个文件；shared runtime/plugin/executor只读。

- [ ] **Step 1: 写资源失败测试。** 注入Task 9A的fallback decision；spy记录physical processes、queue depth、parent共享permit、response/fixed buffers、open handles以及finally后的live children/borrowed permits。
- [ ] **Step 2: 运行先失败命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_resource_bounds.py tests/experiments/slim_v2/test_runtime_adapter.py tests/experiments/slim_v2/test_execution.py tests/experiments/slim_v2/test_projector_schema.py -q`

  Expected: FAIL，缺bounded-process facade与资源生命周期实现；既有runtime/execution/projector合同仍必须被收集。

- [ ] **Step 3: 最小实现fallback。** 仅在decision为`bounded_process_required`时包装现有process prepare/export接缝；physical pool≤10、queue≤50；Exp2 logical capacity仍取condition。真实provider permit由parent创建并跨进程共享，DeepSeek≤10、SiliconFlow≤3且`finally`释放；fixed trace read semaphore=8；16MiB单响应与并发上限共同约束Exp1约160MiB、Exp5约48MiB、fixed trace约128MiB；app句柄≤128。
- [ ] **Step 4: 运行通过命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_resource_bounds.py tests/experiments/slim_v2/test_runtime_adapter.py tests/experiments/slim_v2/test_execution.py tests/experiments/slim_v2/test_projector_schema.py -q`

  Expected: PASS，本task 7 tests及全部被修改模块既有合同通过；结束时`active_children=0`且borrowed permits=0。

**完成证据：** k=50/physical≤10/queue≤50、permit≤10/3、buffer/handle上界、生命周期清理、task implementation commit SHA。两种backend均不能闭合时按relay第7.1节执行代理授权法定人数，不预改shared。

**Progress更新点：** 记录最终领域backend和资源证据；下一focus写Task 10。

### Task 10: Exp1唯一UnitTrace与Exp2–4严格fixed lookup

**目标：** 实现三元键trace原子commit、普通语义核对、exact ordinal/last fallback和零transport source selection。

**Files:**

- Create: `src/tokenshare/experiments/slim_v2/trace_source.py`
- Create: `tests/experiments/slim_v2/test_trace_source.py`

**Tests / exact names:**

- `test_unit_trace_key_is_case_source_repeat_zero_and_planned_unit`
- `test_protocol_and_coverage_tail_cannot_commit_same_planned_unit_twice`
- `test_trace_requires_at_least_one_monotonic_natural_attempt`
- `test_exact_ordinal_is_selected_when_present`
- `test_missing_ordinal_uses_last_natural_attempt_and_preserves_current_ordinal`
- `test_factorization_range_and_lean_node_dependency_fields_must_match`
- `test_duplicate_missing_empty_or_wrong_model_trace_stops_without_transport_fallback`
- `test_reconstructed_prompt_and_dependencies_equal_exp1_plan_output`

**Dependencies:** Tasks 1, 4, 7。

**Allowed writes:** 仅上述两个文件。

- [ ] **Step 1: 写失败测试。** 构造protocol/tail、ordinals 0/1/2、source失败result、Factorization/Lean语义错和transport spy；逐字比较当前plan重建prompt/dependency输入。
- [ ] **Step 2: 运行先失败命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_trace_source.py -q`

  Expected: FAIL，缺`UnitTraceStore/FixedTraceSource.select_attempt`。

- [ ] **Step 3: 最小实现。** 每planned unit一条原子JSON；source key不含condition/worker/fault/mode/current ordinal；exact优先，缺失取max ordinal；返回source ordinal/origin/result/raw/usage/latency/cost/model并保持当前ordinal供Exp3扰动；任何非法source都不创建caller。
- [ ] **Step 4: 运行通过命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_trace_source.py -q`

  Expected: PASS，8 tests passed，transport spy=0。

**完成证据：** key/ordinary semantics/exact/fallback snapshots、duplicate错误、prompt/dependency equality、task commit SHA。

**Progress更新点：** 记录source lookup合同；下一focus写Task 11。

### Task 11: Exp1逐root coverage tail与崩溃恢复

**目标：** 在正常root terminal后、下一root start前只补unscheduled且无protocol trace的planned units，资源与正文隔离。

**Files:**

- Create: `src/tokenshare/experiments/slim_v2/coverage_tail.py`
- Modify: `src/tokenshare/experiments/slim_v2/execution.py`
- Modify: `src/tokenshare/experiments/slim_v2/projector.py`
- Create: `tests/experiments/slim_v2/test_coverage_tail.py`

**Tests / exact names:**

- `test_tail_targets_are_sorted_unscheduled_minus_protocol_trace_ids`
- `test_tail_never_submits_to_terminal_protocol_or_changes_root_result_and_runtime`
- `test_tail_uses_same_request_prompt_dependencies_provider_and_controls`
- `test_tail_stops_on_first_accepted_or_after_three_natural_attempts`
- `test_tail_failure_record_still_closes_trace_key_and_counts_failure_unit`
- `test_tail_summary_counts_targets_recorded_success_failure_attempt_tokens_and_cost`
- `test_protocol_and_tail_trace_union_equals_planned_units_exactly_once`
- `test_resume_skips_protocol_and_committed_tail_units_and_only_fills_missing_key`
- `test_next_root_start_occurs_after_tail_terminal`
- `test_tail_commit_failure_duplicate_or_illegal_target_stops_before_next_root_start`
- `test_no_tail_target_uses_exact_not_needed_null_zero_contract_without_caller`

**Dependencies:** Tasks 8, 10；provider使用Task 6 fake transport。

**Allowed writes:** 仅上述四个文件。

- [ ] **Step 1: 写fake tail失败测试。** fixture包含accepted ordinal0、accepted ordinal2、三次失败、pre-dispatch失败与中断后已commit trace；clock断言root terminal/tail/next root顺序；分别注入sink commit failure、duplicate key与非法target并断言下一root callback从未执行。无target时逐字段断言started/terminal为null、wall=0、status=`not_needed`、success/failure/attempt/token/cost均为0且caller count=0。
- [ ] **Step 2: 运行先失败命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_coverage_tail.py -q`

  Expected: FAIL，缺`run_coverage_tail`和tail projection。

- [ ] **Step 3: 最小实现。** 输入冻结plan、runtime unscheduled IDs、protocol trace keys、同caller/parser/verifier/checker；每target最多3自然attempts；checker仅决定继续，不创建protocol attempt/canonical/merge；先写trace再更新tail state；summary守恒式固定。
- [ ] **Step 4: 运行通过命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_coverage_tail.py tests/experiments/slim_v2/test_execution.py tests/experiments/slim_v2/test_projector_schema.py -q`

  Expected: PASS，本task 11 tests及被修改execution/projector既有合同全部通过；所有provider calls来自fake transport，失败注入后next-root count=0。

**完成证据：** target集合、summary守恒、protocol runtime不变、跨root时序、resume调用计数、task commit SHA。

**Progress更新点：** 记录Exp1 protocol/tail闭合；下一focus写Task 12。

### Task 12: Experiment 2 fixed replay logical worker扩展

**目标：** 让同一Exp1 plan/trace/source latency在1/3/7/10/30/50容量下真实推进逻辑调度，Exp2当次provider calls恒0。

**Files:**

- Create: `src/tokenshare/experiments/slim_v2/scenarios.py`
- Modify: `src/tokenshare/experiments/slim_v2/runtime_adapter.py`
- Create: `tests/experiments/slim_v2/test_scenarios_exp2.py`

**Tests / exact names:**

- `test_exp2_inherits_exp1_split_units_prompt_and_dependencies_exactly`
- `test_exp2_worker_one_three_seven_ten_thirty_fifty_use_logical_scheduler_capacity`
- `test_exp2_one_ten_fifty_makespan_peak_utilization_and_early_stop_match_discrete_events`
- `test_exp2_repeat_id_never_changes_source_repeat_zero_lookup`
- `test_exp2_max_retries_two_uses_exact_then_last_attempt_fallback`
- `test_exp2_actual_provider_fields_are_null_and_transport_count_is_zero`
- `test_exp2_paired_worker_one_to_ten_and_fifty_identity_is_complete`

**Dependencies:** Tasks 9B, 10。

**Allowed writes:** 仅上述三个文件。

- [ ] **Step 1: 写逻辑事件失败测试。** 用固定latencies手算w1/w10/w50 completion order/makespan/worker intervals/utilization；早停fixture保留unscheduled；repeat0/1命中同source。
- [ ] **Step 2: 运行先失败命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_scenarios_exp2.py -q`

  Expected: FAIL，缺`build_exp2_scenario/run_exp2_root`。

- [ ] **Step 3: 最小实现。** whole_root、`logical_source_latency_1x`、公共LogicalSourceLatencyScheduler；worker只改capacity；从profile显式取max_retries=2；记录runtime observation与source consumption；任何transport attempt停止condition。
- [ ] **Step 4: 运行通过命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_scenarios_exp2.py tests/experiments/slim_v2/test_runtime_adapter.py tests/experiments/slim_v2/test_resource_bounds.py -q`

  Expected: PASS，本task 7 tests及被修改runtime/resource既有合同全部通过，provider/transport count=0。

**完成证据：** 六worker容量、手算makespan、pair identity、source repeat、零调用、task commit SHA。

**Progress更新点：** 记录Exp2路径闭合；下一focus写Task 13。

### Task 13: Experiment 3五fault、确定性扰动与辅助reference

**目标：** 实现rate-fault target/ordinal0动作、source与simulated资源、同case/repeat辅助reference；worker death留给Task 14。

**Files:**

- Modify: `src/tokenshare/experiments/slim_v2/scenarios.py`
- Modify: `src/tokenshare/experiments/slim_v2/projector.py`
- Create: `tests/experiments/slim_v2/test_scenarios_exp3.py`

**Tests / exact names:**

- `test_fault_target_count_is_ceil_positive_at_least_one_and_uniform_over_sorted_planned_ids`
- `test_each_of_five_faults_injects_only_ordinal_zero_and_replacement_is_clean`
- `test_false_positive_reaches_real_domain_verification_and_false_negative_forces_rejected_path`
- `test_no_return_late_submission_and_executor_error_preserve_simulated_consumption`
- `test_perturbation_identity_uses_only_case_unit_repeat_and_current_ordinal`
- `test_perturbation_matches_frozen_token_network_latency_formula_and_seed`
- `test_reasoning_is_completion_subset_and_simulated_cost_keeps_source_tier`
- `test_one_auxiliary_reference_per_case_repeat_is_reused_across_fault_type_and_rate`
- `test_exp3_provider_call_count_is_zero_and_resource_semantics_are_simulated_trace_attributed`

**Dependencies:** Tasks 5, 10, 12。

**Allowed writes:** 仅上述三个文件。

- [ ] **Step 1: 写五fault/扰动失败测试。** rates覆盖1%与100%，ordinal0/1，exact/fallback后仍用current ordinal扰动；固定随机输出作golden；真实领域verification用Factorization与Lean fake checker边界。
- [ ] **Step 2: 运行先失败命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_scenarios_exp3.py -q`

  Expected: FAIL，缺`select_fault_targets/build_rate_fault_hooks/perturb_attempt/build_exp3_reference`。

- [ ] **Step 3: 最小实现。** `false_positive`把ordinal0变为schema-valid领域错误并送真实verification；`false_negative`把原正确ordinal0强制送rejected；`no_return`消费模拟资源后不提交并等lease expiry；`late_submission`在deadline+1ms提交；`executor_error`消费模拟资源后返回明确错误；replacement不重复注入。target为`ceil(rate×planned-first-attempt N)`且正rate至少1，按排序列表均匀选。扰动身份只含`case_id/planned_ai_unit_id/experiment_repeat_id/attempt_ordinal`，seed=`20260820`、version=`slim_v2.exp3_perturbation.v1`；`δ_token,δ_network∈[-0.1,0.1]`，`simulated_generated=max(0,round(completion×(1+δ_token)))`，`simulated_total=max(1,prompt+simulated_generated)`，`δ_latency=0.8δ_token+0.2δ_network`，`simulated_latency=max(1,round(source_latency×(1+δ_latency)))`。reference key=`case_id×repeat_id`且不进paper inventory；logical scheduler消费simulated latency；source与simulated并存。
- [ ] **Step 4: 运行通过命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_scenarios_exp3.py tests/experiments/slim_v2/test_scenarios_exp2.py tests/experiments/slim_v2/test_projector_schema.py -q`

  Expected: PASS，本task 9 tests及被修改scenarios/projector既有合同全部通过，provider/transport count=0。

**完成证据：** target golden、五fault observations、扰动golden、reference复用、resource semantics、task commit SHA。

**Progress更新点：** 记录Exp3 rate-fault/reference闭合；下一focus写Task 14。

### Task 14: Experiment 3真实Process worker death与恢复关联

**目标：** 用公共Process backend/termination policy执行dead=1/3、progress=25/50/75，投影PID/exit/progress和original→replacement链。

**Files:**

- Modify: `src/tokenshare/experiments/slim_v2/scenarios.py`
- Modify: `src/tokenshare/experiments/slim_v2/runtime_adapter.py`
- Modify: `src/tokenshare/experiments/slim_v2/projector.py`
- Create: `tests/experiments/slim_v2/test_scenarios_exp3_worker_death.py`

**Tests / exact names:**

- `test_worker_death_always_uses_process_backend_and_frozen_termination_policy`
- `test_dead_one_three_and_progress_twenty_five_fifty_seventy_five_are_observed_from_real_process_facts`
- `test_recovery_event_and_new_attempt_form_original_to_replacement_chain`
- `test_recovery_decision_without_new_attempt_is_not_counted_started_or_reassigned`
- `test_death_original_and_replacement_attempts_use_simulated_latency_and_tokens`
- `test_discarded_death_tokens_join_original_attempt_to_noncanonical_simulated_usage`
- `test_processes_and_sidecars_are_closed_after_root_failure_or_success`

**Dependencies:** Tasks 9B, 13。

**Allowed writes:** 仅上述四个文件。

- [ ] **Step 1: 写Process失败测试。** 使用短小picklable fake execution与真实子进程，不调用Lean/provider；覆盖成功replacement、未启动replacement、root unrecovered与exception cleanup。
- [ ] **Step 2: 运行先失败命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_scenarios_exp3_worker_death.py -q`

  Expected: FAIL，缺worker-death scenario/projector joins；不得因multiprocessing残留挂起。

- [ ] **Step 3: 最小实现。** 精确构造WorkerTerminationPolicy；只从WorkerExecutionFact与recovery/attempt events投影；max_retries=2；worker death仍用Exp3扰动和logical scheduler；`finally`终止/join全部process/manager/queue。
- [ ] **Step 4: 运行通过命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_scenarios_exp3_worker_death.py tests/experiments/slim_v2/test_scenarios_exp3.py tests/experiments/slim_v2/test_scenarios_exp2.py tests/experiments/slim_v2/test_runtime_adapter.py tests/experiments/slim_v2/test_resource_bounds.py tests/experiments/slim_v2/test_projector_schema.py -q`

  Expected: PASS，本task 7 tests及被修改scenarios/runtime/projector全部既有合同通过，结束后无live child/sidecar。

**完成证据：** PID/exit/progress事实、replacement chain、discarded token join、资源清理、task commit SHA。

**Progress更新点：** 记录Exp3 worker-death闭合；下一focus写Task 15。

### Task 15: Experiment 4 mode-blind challenge与11-mode真实消融

**目标：** 冻结full/representative challenge分配、mode-blind注入、11个独立policy展开及实际observation；不计算最终metrics。

**Files:**

- Modify: `src/tokenshare/experiments/slim_v2/scenarios.py`
- Modify: `src/tokenshare/experiments/slim_v2/projector.py`
- Create: `tests/experiments/slim_v2/test_scenarios_exp4.py`

**Tests / exact names:**

- `test_full_challenge_assignment_rotation_and_quotas_are_exact`
- `test_representative_four_challenge_plans_are_exact`
- `test_challenge_plan_is_created_before_mode_and_identical_across_eleven_modes`
- `test_injector_signature_and_behavior_cannot_observe_mode_policy_or_disabled_set`
- `test_invalid_parser_no_return_and_child_delay_inject_at_frozen_boundaries`
- `test_eleven_modes_map_to_exact_mechanism_booleans_with_slot_integrity_true_and_retry_one`
- `test_six_double_modes_run_both_disabled_mechanisms_in_one_root_not_offline_join`
- `test_observations_come_from_hook_event_checker_facts_not_mode_name`
- `test_preflight_block_plan_mismatch_and_missed_opportunity_mark_cell_invalid`
- `test_exp4_provider_calls_are_zero_and_fault_fields_are_null`

**Dependencies:** Tasks 10, 12–13。

**Allowed writes:** 仅上述三个文件。

- [ ] **Step 1: 写challenge/policy失败测试。** full quota、代表四行、11 mode matrix与六个四端集合逐项硬编码；用同一plan对象跨mode；构造target未到边界与到达未注入两种不同结果。
- [ ] **Step 2: 运行先失败命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_scenarios_exp4.py -q`

  Expected: FAIL，缺`build_exp4_challenge_plans/build_mechanism_policy/ModeBlindChallengeInjector`。

- [ ] **Step 3: 最小实现。** 分domain稳定排序与quota跳过算法；family固定为`INVALID_PARSED_CANDIDATE,PARSER_REQUIRED_CANONICAL_JSON,RECOVERABLE_NO_RETURN,REQUIRED_CHILD_DELAY`，Factorization循环从INVALID开始、Lean从CHILD_DELAY开始，full quotas Factorization=`38/38/37/37`、Lean=`11/11/11/12`。前三类目标为稳定首个planned unit；CHILD_DELAY的Factorization目标为所有含真实divisor ranges，prime取稳定最后required range，Lean取稳定最后required terminal slot。invalid在parser后verification前把Factorization target改成`target_n+1`或Lean proof换成引用不存在标识符；canonical JSON在parser前保持候选字段语义不变；两个no-return在source usage后。representative四plan固定为`factor_v2_hard_138×0/INVALID`、`factor_v2_hard_145×0/PARSER`、`lean_v2_simple_induction_direct_nat_01×0/CHILD_DELAY`、`lean_v2_simple_pure_logic_direct_prop_01×0/NO_RETURN`。injector构造/调用参数不含mode。mode固定为`FULL,NO_VERIFICATION,NO_PARSER_POLICY,NO_REQUEUE,NO_MERGE_GATE,NO_VERIFICATION__NO_PARSER_POLICY,NO_VERIFICATION__NO_REQUEUE,NO_VERIFICATION__NO_MERGE_GATE,NO_PARSER_POLICY__NO_REQUEUE,NO_PARSER_POLICY__NO_MERGE_GATE,NO_REQUEUE__NO_MERGE_GATE`；每mode只映射对应policy布尔，slot integrity恒true。observations由collector join公共事实，不预填预期结果。
- [ ] **Step 4: 运行通过命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_scenarios_exp4.py tests/experiments/slim_v2/test_scenarios_exp3_worker_death.py tests/experiments/slim_v2/test_scenarios_exp3.py tests/experiments/slim_v2/test_scenarios_exp2.py tests/experiments/slim_v2/test_projector_schema.py -q`

  Expected: PASS，本task 10 tests及被修改scenarios/projector全部既有合同通过，provider/transport count=0。

**完成证据：** quota/rotation、injector signature、11 modes、四端identity、validity分类、task commit SHA。

**Progress更新点：** 记录Exp4 scenario闭合；下一focus写Task 16。

### Task 16: Lean fixed-DAG bridge与root recheck投影

**目标：** 用固定lemma-DAG和fake checker闭合Lean provider/fixed execution、依赖readiness、child checker与root checker；不执行Lean二进制。

**Files:**

- Modify: `src/tokenshare/experiments/slim_v2/execution.py`
- Modify: `src/tokenshare/experiments/slim_v2/runtime_adapter.py`
- Modify: `src/tokenshare/experiments/slim_v2/projector.py`
- Create: `tests/experiments/slim_v2/test_lean_bridge.py`

**Tests / exact names:**

- `test_lean_fixed_dag_preserves_planned_unit_node_dependency_prompt_and_readiness`
- `test_lean_provider_and_fixed_bridges_normalize_candidate_before_checker`
- `test_child_checker_acceptance_is_required_for_canonical_proof`
- `test_root_verified_correct_requires_merge_accepted_and_root_checker_accepted`
- `test_protocol_completed_with_root_checker_rejection_is_not_verified_correct`
- `test_lean_k_gt_one_fake_checker_keeps_request_checker_and_canonical_identity_aligned`
- `test_lean_fixture_uses_no_lean_lake_or_catalog_subprocess`

**Dependencies:** Tasks 8, 9B, 10, 15。

**Allowed writes:** 仅上述四个文件；复用Task 9A固定fixture。

- [ ] **Step 1: 写fake checker失败测试。** checker按request ID返回accepted/rejected/environment-error；root merge report独立变化；subprocess spy禁止`lean/lake`。
- [ ] **Step 2: 运行先失败命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_lean_bridge.py -q`

  Expected: FAIL，缺Lean root装配/projector规则；不得出现Lean toolchain调用。

- [ ] **Step 3: 最小实现。** root独占LeanRuntimeAdapter；正式构造点保留`check_lean_proof`注入，但测试传fake；provider/fixed bridge走公开parser/normalize；projector分别保存child/root checker并只按双accepted判正确。
- [ ] **Step 4: 运行通过命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_lean_bridge.py tests/experiments/slim_v2/test_execution.py tests/experiments/slim_v2/test_runtime_adapter.py tests/experiments/slim_v2/test_resource_bounds.py tests/experiments/slim_v2/test_projector_schema.py -q`

  Expected: PASS，本task 7 tests及被修改execution/runtime/projector全部既有合同通过，subprocess spy=0。

**完成证据：** DAG/依赖/prompt identity、checker分类、root correctness、k>1对齐、无Lean命令、task commit SHA。

**Progress更新点：** 记录Lean轻量接线闭合；下一focus写Task 17。

### Task 17: Experiment 5四endpoint fake transport与零重试

**目标：** 用同一runner/provider/bridge表达四个SiliconFlow entry、ABCD/BDAC/CADB顺序、in-flight=3、零replacement和first-attempt字段。

**Files:**

- Modify: `src/tokenshare/experiments/slim_v2/profiles.py`
- Modify: `src/tokenshare/experiments/slim_v2/provider.py`
- Modify: `src/tokenshare/experiments/slim_v2/runtime_adapter.py`
- Create: `tests/experiments/slim_v2/test_exp5.py`

**Tests / exact names:**

- `test_exp5_four_entry_model_reasoning_and_request_controls_are_exact`
- `test_exp5_repeat_model_order_is_abcd_bdac_cadb`
- `test_exp5_worker_ten_uses_global_provider_in_flight_three`
- `test_exp5_retry_zero_and_replacement_disabled_allow_one_attempt_per_unit`
- `test_exp5_first_attempt_transport_parse_checker_taxonomy_is_mutually_exclusive`
- `test_exp5_model_mismatch_stops_condition_and_writes_remaining_inventory_rows_invalid`
- `test_exp5_fake_path_uses_same_bounded_caller_bridge_sink_and_schema_as_full`
- `test_exp5_four_entry_valid_usage_persists_flat_pricing_version_tier_and_cost`

**Dependencies:** Tasks 5, 6, 9B, 16。

**Allowed writes:** 仅上述四个文件。

- [ ] **Step 1: 写四entry失败测试。** fake envelopes覆盖thinking/nonthinking、usage/cache/model mismatch和失败taxonomy；四个valid usage逐entry断言Task 5价格数值、`pricing_version=slim_v2.pricing.2026-08-20`、`pricing_tier=flat`及attempt/root持久化；并发barrier证明最多3；未调度unit保留planned。
- [ ] **Step 2: 运行先失败命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_exp5.py -q`

  Expected: FAIL，缺Exp5 condition装配/first-attempt projection。

- [ ] **Step 3: 最小实现。** 四个entry/model固定为`glm_5_2_exp5_v3/zai-org/GLM-5.2`、`qwen3_14b_exp5_v3/Qwen/Qwen3-14B`、`minimax_m2_5_exp5_v3/MiniMaxAI/MiniMax-M2.5`、`deepseek_v3_pro_exp5_v3/Pro/deepseek-ai/DeepSeek-V3`；前三者thinking budget32768，第四个nonthinking。timeout600/max_tokens32768；`max_retries=0`且replacement false；共享semaphore=3；所有valid actual usage复用Task 8已接好的pricing projector并写flat version/tier/cost；model mismatch剩余root写infrastructure-invalid，不换entry。
- [ ] **Step 4: 运行通过命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_exp5.py tests/experiments/slim_v2/test_profiles.py tests/experiments/slim_v2/test_provider.py tests/experiments/slim_v2/test_pricing.py tests/experiments/slim_v2/test_runtime_adapter.py tests/experiments/slim_v2/test_projector_schema.py tests/experiments/slim_v2/test_resource_bounds.py -q`

  Expected: PASS，本task 8 tests及profiles/provider/pricing/runtime/projector/resource全部既有合同通过，全部来自fake transport。

**完成证据：** 四entry snapshot、逐entry pricing version/tier/cost、repeat order、peak in-flight=3、attempt≤1、taxonomy/model fail-stop、task implementation commit SHA。

**Progress更新点：** 记录Exp5 fake闭合且真实call=0；下一focus写Task 18。

### Task 18: reducer流式分区与统一统计内核

**目标：** 建立只读单run目录的inventory/result join、分区、null传播、case-cluster bootstrap与统计纯函数；不实现实验专属表。

**Files:**

- Create: `src/tokenshare/experiments/slim_v2/reducer.py`
- Create: `tests/experiments/slim_v2/test_reducer_statistics.py`
- Create: `tests/experiments/slim_v2/test_reducer_streaming.py`

**Tests / exact names:**

- `test_inventory_is_fixed_denominator_and_missing_result_is_null_infrastructure_invalid`
- `test_ratio_zero_denominator_and_missing_sum_inputs_return_null_with_reason`
- `test_hyndman_fan_type_seven_quantiles_and_sample_variance_n_minus_one`
- `test_case_cluster_bootstrap_preserves_strata_repeats_pairs_and_quadruples`
- `test_bootstrap_uses_ten_thousand_seed_20260820_and_percentile_bounds`
- `test_interval_is_null_below_five_clusters_or_below_9500_valid_replicates`
- `test_streaming_partitions_never_load_raw_responses_system_events_or_whole_run`
- `test_partition_memory_target_is_256_mib_and_work_files_are_rebuildable`
- `test_reducer_workers_default_one_and_are_capped_at_four`
- `test_reducer_has_no_runtime_plugin_checker_provider_or_legacy_imports`

**Dependencies:** Tasks 1, 4。

**Allowed writes:** 仅上述三个文件。

- [ ] **Step 1: 写统计golden失败测试。** 小fixture手算rate、median/q25/q75/IQR、sample variance/stddev、paired/quadruple cluster抽样；文件spy拒绝读取responses/system；large synthetic iterator检查bounded state。
- [ ] **Step 2: 运行先失败命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_reducer_statistics.py tests/experiments/slim_v2/test_reducer_streaming.py -q`

  Expected: FAIL，缺`stream_partitions/reduce_rate/distribution_summary/bootstrap_case_clusters`。

- [ ] **Step 3: 最小实现。** 先流式join inventory/root/reference/challenge为小observation并写`metrics/.work`；一次只载入一个table/slice partition，目标≤256 MiB；reducer workers默认1且只允许1–4；bootstrap抽case并携带strata/全部repeats/arms；成功正式输出原子写，work可重建；不得调用runtime/provider。
- [ ] **Step 4: 运行通过命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_reducer_statistics.py tests/experiments/slim_v2/test_reducer_streaming.py -q`

  Expected: PASS，10 tests passed，I/O spy与memory bound通过。

**完成证据：** 统计golden、bootstrap metadata、null reasons、stream/memory/I/O边界、task commit SHA。

**Progress更新点：** 记录统计内核与分区合同；下一focus写Task 19A。

### Task 19A: Reducer Experiment 1正文与coverage-tail隔离

**目标：** 只计算Experiment 1全部必须metric IDs、分布/CI和tail诊断，证明tail不进入正文runtime/token/cost。

**Files:**

- Modify: `src/tokenshare/experiments/slim_v2/reducer.py`
- Create: `tests/experiments/slim_v2/test_reducer_exp1.py`

**Tests / exact names:**

- `test_exp1_protocol_only_totals_exclude_coverage_tail_and_sum_root_runtime`
- `test_exp1_tail_diagnostics_are_separate_and_failure_does_not_change_root_correctness`
- `test_exp1_root_distributions_and_rate_bootstrap_follow_authority`
- `test_exp1_exact_totals_do_not_receive_confidence_intervals`

**Dependencies:** Tasks 11, 18。

**Allowed writes:** 仅上述两个文件。

- [ ] **Step 1: 写Exp1 golden失败测试。** run目录同时含protocol/tail、missing usage和自然root失败；逐metric断言value/numerator/denominator/null reason/CI字段与tail隔离。
- [ ] **Step 2: 运行先失败命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_reducer_exp1.py -q`

  Expected: FAIL，缺`reduce_exp1`或正式Exp1 metric rows。

- [ ] **Step 3: 最小实现。** 正文只选`trace_origin=protocol`，batch wall求root runtime之和；tail仅产生独立诊断；固定分母与null/CI规则逐ID执行。
- [ ] **Step 4: 运行通过命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_reducer_exp1.py tests/experiments/slim_v2/test_reducer_statistics.py tests/experiments/slim_v2/test_reducer_streaming.py -q`

  Expected: PASS，本task 4 tests及reducer统计/流式既有合同全部通过。

**完成证据：** Exp1 metric snapshot、tail隔离、CI/无CI字段、task implementation commit SHA。

**Progress更新点：** 记录Exp1 reducer闭合；下一focus写Task 19B。

### Task 19B: Reducer Experiment 5首次输出与三repeat资源

**目标：** 只计算Experiment 5全部必须metric IDs、first-attempt互斥taxonomy、实际调用覆盖和三repeat wall-clock统计。

**Files:**

- Modify: `src/tokenshare/experiments/slim_v2/reducer.py`
- Create: `tests/experiments/slim_v2/test_reducer_exp5.py`

**Tests / exact names:**

- `test_exp5_first_attempt_nonpass_taxonomy_and_checkable_denominators_are_exact`
- `test_exp5_actual_call_coverage_tokens_cost_and_three_repeat_wall_clock_are_exact`
- `test_exp5_root_token_cost_distributions_and_model_wall_sample_stddev_are_exact`
- `test_exp5_exact_totals_do_not_receive_confidence_intervals`

**Dependencies:** Tasks 17, 18, 19A。

**Allowed writes:** 仅上述两个文件。

- [ ] **Step 1: 写Exp5 golden失败测试。** run目录覆盖transport/parse/checker互斥失败、planned-but-unscheduled早停、usage缺失和三repeat；逐metric断言value/numerator/denominator/null reason/CI字段。
- [ ] **Step 2: 运行先失败命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_reducer_exp5.py -q`

  Expected: FAIL，缺`reduce_exp5`或正式Exp5 metric rows。

- [ ] **Step 3: 最小实现。** 只按actual ordinal0 provider calls和冻结互斥优先级分类；model wall只对三个完整批次算sample stddev，不为median加CI；usage缺失按root/null规则传播。
- [ ] **Step 4: 运行通过命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_reducer_exp5.py tests/experiments/slim_v2/test_reducer_exp1.py tests/experiments/slim_v2/test_reducer_statistics.py tests/experiments/slim_v2/test_reducer_streaming.py -q`

  Expected: PASS，本task 4 tests及全部既有reducer合同通过。

**完成证据：** Exp5 metric snapshot、taxonomy、三repeat/CI规则、task implementation commit SHA。

**Progress更新点：** 记录Exp5 reducer闭合；下一focus写Task 20。

### Task 20: Reducer Experiment 2配对扩展性

**目标：** 计算Exp2固定分母、logical timing、source资源、worker并发利用率和每项独立pair eligibility。

**Files:**

- Modify: `src/tokenshare/experiments/slim_v2/reducer.py`
- Create: `tests/experiments/slim_v2/test_reducer_exp2.py`

**Tests / exact names:**

- `test_exp2_counts_trace_resources_and_worker_observations_by_worker_repeat_position`
- `test_exp2_speedup_and_efficiency_require_correct_complete_positive_time_pair`
- `test_exp2_token_and_cost_multiplier_use_their_own_resource_pair_denominators`
- `test_exp2_repeat_min_max_and_relative_difference_use_max_minus_min_over_mean`
- `test_exp2_pair_distributions_ci_and_exact_inventory_counts_are_separated`
- `test_exp2_never_reports_actual_provider_consumption`

**Dependencies:** Tasks 12, 18, 19B。

**Allowed writes:** 仅上述两个文件。

- [ ] **Step 1: 写配对golden失败测试。** 同case/repeat包含w1/w10/w50、错误root、零时间、缺token、缺cost，确保speed/token/cost各自planned/eligible/ineligible与reason不同。
- [ ] **Step 2: 运行先失败命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_reducer_exp2.py -q`

  Expected: FAIL，缺`reduce_exp2`或pair metric rows。

- [ ] **Step 3: 最小实现。** grouping/公式严格按authority 3.2–3.3；repeat汇总只描述，不给两个值CI；counts保持精确；actual usage不出现为当次消费。
- [ ] **Step 4: 运行通过命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_reducer_exp2.py tests/experiments/slim_v2/test_reducer_exp5.py tests/experiments/slim_v2/test_reducer_exp1.py tests/experiments/slim_v2/test_reducer_statistics.py tests/experiments/slim_v2/test_reducer_streaming.py -q`

  Expected: PASS，本task 6 tests及全部既有reducer合同通过。

**完成证据：** pair eligibility snapshots、relative difference golden、零actual usage、task commit SHA。

**Progress更新点：** 记录Exp2 reducer闭合；下一focus写Task 21。

### Task 21: Reducer Experiment 3恢复与模拟资源

**目标：** 计算五fault/death核心计数、candidate/slot rates、fault/reference overhead pairs、discarded资源与kill-progress统计。

**Files:**

- Modify: `src/tokenshare/experiments/slim_v2/reducer.py`
- Create: `tests/experiments/slim_v2/test_reducer_exp3.py`

**Tests / exact names:**

- `test_exp3_five_core_counts_and_fixed_root_denominator_are_exact`
- `test_exp3_wrong_candidate_interception_and_escape_share_candidate_denominator`
- `test_exp3_replacement_success_reassignment_and_slot_completeness_follow_observations`
- `test_exp3_fault_and_death_overheads_use_same_case_repeat_reference_and_independent_pair_counts`
- `test_exp3_discarded_tokens_include_fault_and_worker_death_original_attempts`
- `test_exp3_kill_progress_signed_mean_variance_ci_and_exact_signed_max_are_exact`
- `test_exp3_outputs_resource_semantics_simulated_trace_attributed_and_no_actual_calls`

**Dependencies:** Tasks 13–14, 18, 20。

**Allowed writes:** 仅上述两个文件。

- [ ] **Step 1: 写恢复golden失败测试。** 包含injected未dispatch、recovery decision无attempt、successful replacement、unrecovered root、fault/death不同缺失资源与多个death observations。
- [ ] **Step 2: 运行先失败命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_reducer_exp3.py -q`

  Expected: FAIL，缺`reduce_exp3`或核心metric rows。

- [ ] **Step 3: 最小实现。** fault type/death condition保持分开；candidate按case cluster；三overhead各自pair eligibility；discarded death tokens按original attempt join；signed mean重采样case，signed max精确无CI；metadata明确simulated。
- [ ] **Step 4: 运行通过命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_reducer_exp3.py tests/experiments/slim_v2/test_reducer_exp2.py tests/experiments/slim_v2/test_reducer_exp5.py tests/experiments/slim_v2/test_reducer_exp1.py tests/experiments/slim_v2/test_reducer_statistics.py tests/experiments/slim_v2/test_reducer_streaming.py -q`

  Expected: PASS，本task 7 tests及全部既有reducer合同通过。

**完成证据：** 五核心count、pair/reason、discarded资源、kill统计、resource metadata、task commit SHA。

**Progress更新点：** 记录Exp3 reducer闭合；下一focus写Task 22。

### Task 22: Reducer Experiment 4有效性、pair与双机制interaction

**目标：** 计算Exp4全部运行/挑战/机制/资源/pair/quadruple指标，严格执行科学cell null规则与三个repeat输出。

**Files:**

- Modify: `src/tokenshare/experiments/slim_v2/reducer.py`
- Create: `tests/experiments/slim_v2/test_reducer_exp4.py`

**Tests / exact names:**

- `test_exp4_protocol_start_challenge_opportunity_injection_and_plan_mismatch_are_exact`
- `test_exp4_invalid_cell_nulls_performance_pair_and_interaction_metrics_with_reason`
- `test_exp4_transition_counts_losses_and_each_delta_use_independent_pair_eligibility`
- `test_exp4_challenge_specific_candidate_parser_requeue_and_merge_metrics_use_actual_observations`
- `test_exp4_six_matched_quadruples_compute_success_completion_and_slot_interaction_penalties`
- `test_exp4_pair_and_interaction_repeat_zero_one_two_median_min_max_names_are_exact`
- `test_exp4_five_delta_metric_ids_have_no_median_or_ms_suffix`
- `test_exp4_counts_and_coverage_checks_remain_exact_without_confidence_intervals`

**Dependencies:** Tasks 15, 18, 21。

**Allowed writes:** 仅上述两个文件。

- [ ] **Step 1: 写四端golden失败测试。** 每个mechanism pair构造FULL/NO_i/NO_j/NO_ij三repeat；另构造preflight block、plan mismatch、missed opportunity、target未到边界和不适用mode。
- [ ] **Step 2: 运行先失败命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_reducer_exp4.py -q`

  Expected: FAIL，缺`reduce_exp4`或pair/quadruple rows。

- [ ] **Step 3: 最小实现。** cell valid gate按四条件；专项指标只读actual observations；每delta独立pair counts；四端不可拆case cluster；interaction符号按authority；不适用写null+reason；正式delta ID无额外后缀。
- [ ] **Step 4: 运行通过命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_reducer_exp4.py tests/experiments/slim_v2/test_reducer_exp3.py tests/experiments/slim_v2/test_reducer_exp2.py tests/experiments/slim_v2/test_reducer_exp5.py tests/experiments/slim_v2/test_reducer_exp1.py tests/experiments/slim_v2/test_reducer_statistics.py tests/experiments/slim_v2/test_reducer_streaming.py -q`

  Expected: PASS，本task 8 tests及全部既有reducer合同通过。

**完成证据：** validity/null snapshots、四格transition、六quadruple interaction、repeat字段与exact IDs、task commit SHA。

**Progress更新点：** 记录Exp4 reducer闭合；下一focus写Task 23。

### Task 23: 原子CLI、唯一串行runner、representative 89-call preflight与全离线验收

**目标：** 把已验证组件接成设计规格第7.1节全部原子命令和同管线representative；仍只用fake provider/fixtures，不启动真实representative。

**Files:**

- Modify: `src/tokenshare/experiments/slim_v2/cli.py`
- Modify: `src/tokenshare/experiments/slim_v2/runtime_adapter.py`
- Modify: `src/tokenshare/experiments/slim_v2/projector.py`
- Modify: `src/tokenshare/experiments/slim_v2/sink.py`
- Create: `tests/experiments/slim_v2/test_cli_integration.py`
- Create: `tests/experiments/slim_v2/test_representative_preflight.py`

**Tests / exact names:**

- `test_atomic_exp1_exp5_and_exp2_exp3_exp4_with_explicit_source_commands_parse`
- `test_atomic_exp2_to_exp4_missing_source_fails_before_runtime_or_provider_creation`
- `test_atomic_exp2_to_exp4_incomplete_wrong_profile_or_trace_closure_source_fails_before_runtime`
- `test_run_all_orders_exp1_exp2_exp3_exp4_exp5_reduce_and_passes_explicit_source`
- `test_representative_is_exact_alias_for_run_all_representative_profile`
- `test_output_root_default_and_explicit_parent_keep_run_id_as_leaf`
- `test_representative_and_run_all_preflight_finish_before_secret_runtime_or_root_start`
- `test_root_loop_is_serial_and_exp1_tail_finishes_before_next_root_start`
- `test_resume_skips_committed_root_trace_and_terminal_attempt_without_duplicate_fake_call`
- `test_existing_run_without_resume_refuses_overwrite_and_failed_root_is_not_auto_retried`
- `test_run_checks_estimate_before_start_and_minimum_free_space_before_each_root_then_resumes`
- `test_condition_fail_stop_writes_remaining_preregistered_rows_infrastructure_invalid`
- `test_reduce_uses_only_run_dir_and_writes_all_required_jsonl_csv_summary_outputs`
- `test_representative_preflight_inventory_is_four_twelve_eight_plus_two_forty_four_four`
- `test_representative_preflight_real_provider_call_hard_cap_is_eighty_nine_and_exp2_to_exp4_zero`
- `test_full_and_representative_share_cli_runner_adapter_provider_schema_resume_and_reducer_types`
- `test_run_dir_contains_no_secret_forbidden_authority_or_legacy_runtime_dependency`

**Dependencies:** 全部前序tasks 1–22，包括9A/9B与19A/19B。

**Allowed writes:** 仅上述六个文件。

- [ ] **Step 1: 写CLI integration失败测试。** dependency spies记录构造/调用顺序；fake provider计算call ceilings但不联网；一次人为中断发生在terminal attempt后、result前；atomic Exp2–4分别提供未完成source、错误profile和缺三元trace closure的source，断言在backend/runtime/provider构造前失败且不写论文结果行；静态import scan检查shared只读和legacy禁止依赖。
- [ ] **Step 2: 运行先失败命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_cli_integration.py tests/experiments/slim_v2/test_representative_preflight.py -q`

  Expected: FAIL，缺完整subcommands/serial loop/representative preflight；不得尝试读取local secret或联网。

- [ ] **Step 3: 最小CLI。** 实现`plan`、五个`run --experiment`、`run-all`、`reduce`、`representative`与`--resume`；默认父目录固定为`TokenShareData/outputs/slim_v2`，显式`--output-root`只替换父目录且`run_id`始终为末级，CLI显式值优先。Exp2–4 source必显式且在任何backend/runtime/provider构造前验证source run terminal、profile相同、所需case和三元trace closure完整；run-all只按依赖传当前Exp1。`representative`严格调用与`run-all --profile representative`相同的内部`preflight_profile()`后继续同一runner，不新增preflight命令或公开flag。开始前要求free space大于运行estimate，每个新root前要求至少`max(512 MiB,4×B)`，不足时安全停并保留state供同run resume；每root`try/finally`关闭资源；单root自然失败继续，接线condition错误写剩余rows后停condition。

  Exact command surface:

  ```text
  python -m tokenshare.experiments.slim_v2.cli plan --profile <representative|full> --run-id <id> [--output-root <dir>]
  python -m tokenshare.experiments.slim_v2.cli run --experiment exp1 --profile <p> --run-id <id> [--output-root <dir>] [--resume]
  python -m tokenshare.experiments.slim_v2.cli run --experiment exp2|exp3|exp4 --profile <p> --run-id <id> --source-run-dir <dir> [--output-root <dir>] [--resume]
  python -m tokenshare.experiments.slim_v2.cli run --experiment exp5 --profile <p> --run-id <id> [--output-root <dir>] [--resume]
  python -m tokenshare.experiments.slim_v2.cli run-all --profile <p> --run-id <id> [--output-root <dir>] [--resume]
  python -m tokenshare.experiments.slim_v2.cli reduce --run-dir <dir>
  python -m tokenshare.experiments.slim_v2.cli representative --run-id <id> [--output-root <dir>] [--resume]
  ```
- [ ] **Step 4: Representative preflight。** `representative`与`run-all`内部第一阶段调用同一个纯`preflight_profile()`，只展开并验证精确inventory/source keys/schema/entry/model/磁盘/调用上限：Exp1 4 roots/19 units、Exp2 12、Exp3 8+2 refs、Exp4 44、Exp5 4；Exp1 hard cap57、Exp5 hard cap32、总89；Exp2–4 caller/transport构造数为0。focused test直接调用纯函数并通过CLI dependency spies证明preflight在secret/runtime/root前完成；不增加公开preflight命令/flag，不读取secret、不调用provider、不运行root。
- [ ] **Step 5: 运行通过命令。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_cli_integration.py tests/experiments/slim_v2/test_representative_preflight.py -q`

  Expected: PASS，17 tests passed，network/secret/runtime-before-preflight spy=0。

- [ ] **Step 6: 运行全Slim focused suite。**

  Run: `conda run -n tokenshare python -m pytest tests/experiments/slim_v2 -q`

  Expected: PASS；不得收集任何Lean专项suite、LeanAudit/catalog/lake/lean测试；不得调用真实provider。

**完成证据：** 原子命令matrix、output-root优先级、source preflight、run-all order、serial timeline、resume fake-call count、89-call preflight、全suite pass数与时长、secret/legacy/forbidden scan、task implementation commit SHA。

**Progress更新点：** 记录Stage 3全部tasks完成、全focused suite证据、未解决问题和Stage 4下一步；不得在Stage 3启动真实representative。

## 4. Stage 3 fresh verification与完成门槛

Task 23通过后，Stage 3 owner必须在新shell中依次运行并读取完整退出码：

```powershell
conda run -n tokenshare python -m pytest tests/experiments/slim_v2 -q
conda run -n tokenshare python -m pytest tests/test_init_verification_profiles.py -q
git diff --check
git status --short --branch
```

随后执行严格UTF-8、Markdown和静态范围检查：

1. 严格UTF-8读取`Doc/SlimV2/`当前权威、设计和计划；
2. 代码围栏成对、Markdown表格列数一致、无未决占位标记；
3. source/tests import scan不存在旧paper/formal runner、budget/receipt/evidence/response-bank/publication依赖；
4. source/tests命令字符串不存在Lean专项suite、LeanAudit、全量catalog、`lake`或`lean`回归；
5. `schema.py` leaf paths与fixture集合相等，`reducer.py`正式metric ID集合与153-ID fixture相等；
6. full/representative规模、provider caps、Exp2–4零调用、16MiB read、process/queue/permit/handle/log/disk边界均有通过测试；
7. 仓库只包含本计划允许写入文件，没有secret、`local/`输出、TokenShareData结果或测试临时文件。

完成状态只能在以下全部成立时写入：全部25 tasks（含9A/9B与19A/19B）有failing/pass证据、implementation commit与紧随的evidence commit；每task spec/quality review已闭合；final cross-component reviewer范围内Critical=0、Important=0；shared interface gap仍为none；没有真实provider/representative/full/Lean专项命令；`progress.md`顶部与本计划evidence同步。若确需最小Lean single-case smoke，必须先在progress写轻量方法无法定位的具体证据与≤10分钟上限；没有该前置记录就不得运行。

## 5. 实施证据记录格式

Stage 3对每个task追加一行，不覆盖计划语义：

| Task | failing command/result | passing command/result | spec review | quality review | commit |
|---|---|---|---|---|---|
| Task 1 | 原命令先被既有src-layout阻断：`No module named tokenshare`；仅设置进程内`PYTHONPATH=src`后得到计划允许RED：`No module named tokenshare.experiments.slim_v2`。修正轮另真实得到`6 failed`、`2 failed, 4 passed`、`1 failed, 5 passed`，分别覆盖nullable/递归/条件规则、资源族与verified/final、自然ordinal上限。 | owner fresh：`PYTHONPATH=src`、禁bytecode/cache运行Task 1命令，`6 passed in 0.17s`；authority leaves=`168/168`、metric records=`153/153`、operational leaves=`5/5`、规则=`173/173`；UTF-8、禁止import、尾随空白、cache、`git diff --check`均通过。 | 最终`0 Critical / 0 Important / 0 Minor / 0 out_of_scope_by_user`，`Spec compliant`。生命周期未启动字段冲突按relay §7.1三票一致选择A，固定reason细节按两票多数，状态=`approved_under_user_delegation_by_quorum`。 | 最终`0 Critical / 0 Important / 0 Minor`，`APPROVED`；首次唯一Important（Exp1自然attempt必须连续且最多3次）经第四轮RED/GREEN关闭。 | implementation `aaabca417f76c00db6011f8bf0bb04617fffa4f2` |
| Task 2 | 设置进程内`PYTHONPATH=src`运行计划命令，`8 failed in 0.26s`，首败=`No module named tokenshare.experiments.slim_v2.case_source`。首次GREEN为`7 passed, 1 failed`并暴露Lean正式case过滤缺口，修正为只选`preflight_status=passed`与`task14_checker_backed_pool`。 | owner fresh：Task 2命令`8 passed in 0.20s`；catalog为Factorization `500`行/SHA256 `9ce2b31a…7774`、Lean `165`行/SHA256 `5a134f24…2cdc`；tuple逐项、唯一/存在/分层、Exp1 units=`1400+570=1970`、representative units=`19`、UTF-8/literal-only/cache/diff均通过。 | `0 Critical / 0 Important / 0 Minor / 0 out_of_scope_by_user`，`Spec compliant`；独立重算所有hash/pin/slice后首个mismatch与差集均为空。 | `0 Critical / 0 Important / 0 Minor`，`APPROVED`。 | implementation `13a25193465736c0fecda5d304d9de5be3c2ecbc` |

Stage 3只在某task真实完成后追加该task的具体一行；不得预填空值、预计pass数或虚构SHA。

该表只记录开发证据，不是runtime gate、receipt、digest或publication state。Stage 3不得以预计结果、子Agent口头摘要或陈旧基线代替真实命令输出。

## 6. Stage 2只读审查与裁决记录

审查基于SHA256 `1DF24BA5E365A17DA1BFB8802CA9D76B3689BF4CCEA8B1CF5BC782757B20F0CC` 的1144行草稿快照，两名reviewer均只读且未修改文件：

- task/test decomposition：`1 Critical / 6 Important / 2 Minor / 0 out_of_scope_by_user`；
- spec coverage：`1 Critical / 3 Important / 1 Minor / 0 out_of_scope_by_user`；
- 去重后原草稿为`1 Critical / 9 Important / 3 Minor`；共同Critical是Task 2读取reuse allowlist未点名的两个archive JSON，确定性阻断full ID固化；
- owner随后静态核对发现representative Lean planned-unit冲突；两名reviewer短复核均判定新增Critical且命中relay第7节，无保持`20/60/92`不变的Slim-local解法。用户随后明确授权保持四个case IDs和当前catalog不变的最小一致性勘误。

统一裁决与修订如下；所有项均为范围内问题，`out_of_scope_by_user=0`：

| severity | finding | owner裁决与计划落点 |
|---|---|---|
| Critical | Task 2读取两个未列入reuse allowlist的archive JSON | 接受；Task 2完全移除archive JSON读取，只用当前两个catalog和本计划冻结的精确hash/pin/slice规则，一次性写literal tuples |
| Critical | representative induction实际1 unit，与设计的2/20/60/92冲突 | 用户已确认保持case/catalog，修正文档派生值为`1/19/57/89`；设计规格13.1/13.5/13.6、Task 2/3/23已同步 |
| Important | task实现allowlist与plan/progress证据写入冲突，SHA无法自包含 | 接受；第0.2/0.3节冻结implementation commit后独立evidence commit，plan/progress为唯一常设证据例外 |
| Important | authority leaf set与额外operational fields边界不唯一 | 接受；Task 1 fixture拆成authority与operational集合，分别断言metric projection与完整schema冻结并集 |
| Important | Exp3/4 Lean及各实验full ID子集未精确冻结 | 接受；Task 2给出完整hash、pin和逐实验slice规则，并要求test独立重算后逐tuple相等 |
| Important | 修改既有模块的task未运行既有合同回归 | 接受；9B、11–17、19B–22的passing command显式加入所改模块全部既有Slim测试，Task 23再跑全suite |
| Important | tail持久化/重复/非法target失败未证明阻止下一root | 接受；Task 11新增三类失败注入和next-root count=0断言 |
| Important | CLI遗漏`--output-root`且preflight入口不明确 | 接受；Task 23冻结所有run/plan/representative的父目录override，并把preflight定义为representative/run-all同一内部第一阶段，不新增命令 |
| Important | provider usage cache/total内部关系未验收 | 接受；Task 5验证两条等式，Task 8验证null+固定reason传播且不阻止root |
| Important | HTTP response成功/HTTP/envelope路径未证明close | 接受；Task 6要求四类terminal路径`close()`恰好一次并用单一`try/finally`覆盖 |
| Important | atomic Exp2–4未验incomplete/wrong-profile/trace closure source在runtime前拒绝 | 接受；Task 23新增三类source preflight与backend/runtime/provider构造数0断言 |
| Minor | Task 9 focus过大 | 接受；拆为9A Thread decision与9B bounded-process/resource lifecycle |
| Minor | Task 19合并两个独立实验reducer | 接受；拆为19A Exp1与19B Exp5，各自独立测试/命令/evidence |
| Minor | Exp1无tail target的精确字段无测试 | 接受；Task 11新增null/zero/not_needed逐字段合同和caller=0 |

第一次修订后的双路复核结果为：spec coverage=`0 Critical / 1 Important / 1 Minor`，task/test decomposition=`0 Critical / 2 Important / 3 Minor`，`out_of_scope_by_user=0`。新增范围内项也已统一闭合：设计规格exact CLI补齐`--output-root`；Task 8/17增加Exp1/5 valid usage到pricing version/tier/cost的正向持久化测试；设计规格残留的旧unit数量常量改为19；Task 2 evidence改为当前catalog规则独立重算与literal tuple相等；Task 3 passing command纳入`test_case_source.py`。

两名原reviewer对最终修订前快照完成限定短复核：spec coverage与task/test decomposition均为`0 Critical / 0 Important / 0 Minor / 0 out_of_scope_by_user`，结论均为PASS。所有初审与复审范围内发现已统一修复，shared interface gap仍为`none`；本计划据此设为`approved_under_user_delegation`。本表只是Stage 2文档审查裁决，不是runtime approval gate。
