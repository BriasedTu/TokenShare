---
status: frozen_for_extraction
document: paper_branch_extraction_manifest
audit_head: 0b204ce3d6767dddf09a9e9b0af3531badb78544
target_branch: codex/experiments-clean-extraction
target_worktree: E:\TokenEcnomic\TokenShareWorktrees\experiments-clean-extraction
source_freeze_tag: experiments-pre-cleanup-20260827
date: 2026-08-27
---

# 论文分支精确抽取 Manifest

## 1. 执行合同

本文把 `audit_head` 上的三路只读审计结论冻结为机械抽取边界。`audit_head` 只用于证明清单来源；实际抽取必须等 Task B 完成论文修正、reducer 修正和来源整理后，解析本地 annotated tag `experiments-pre-cleanup-20260827^{}` 得到唯一的 `frozen_sha`。任何 writer 只能使用：

```powershell
$freezeTag = 'experiments-pre-cleanup-20260827'
$frozenSha = git rev-parse "$freezeTag^{}"
if ((git cat-file -t $freezeTag) -ne 'tag') { throw 'freeze source must be an annotated tag' }
git show "$frozenSha`:src/tokenshare/protocol_engine.py"
git restore --source=$frozenSha --staged --worktree -- 'src/tokenshare/protocol_engine.py'
```

禁止从 live HEAD、其他 branch、其他 worktree 的工作区内容、未跟踪文件或绝对路径复制实现。目标固定为 branch `codex/experiments-clean-extraction`、worktree `E:\TokenEcnomic\TokenShareWorktrees\experiments-clean-extraction`。本阶段不创建该 branch/worktree/tag，也不运行 provider、实验或测试。

清单术语是机械的：

- `keep_exact`：source 与 target 路径相同；除表中写明 `keep_then_synthesize` 的文件外，目标 bytes 必须等于 `frozen_sha` blob。
- `move_exact`：每行是唯一 source→target 映射。`byte_identical` 表示 bytes 不变；`move_then_synthesize` 表示先取得唯一 source blob，再只执行同一行或 `synthesize` 中列出的变换。
- `synthesize`：新建或重写文件，必须只使用本清单列出的输入事实。
- `exclude_exact`：目标树不得含该路径或 selector 的任何命中。
- 默认规则是 deny：`frozen_sha` 中不属于 `keep_exact ∪ move_exact` source 集，且不属于 `synthesize` target 集的 tracked path，一律不进入目标树。

目录 selector 以 `git ls-tree -r --name-only $frozenSha -- 'src/tokenshare'` 这类带本清单已枚举 exact path 的命令取得 tracked 全集；本文对每个 selector 给出完整保留或完整排除集合，不读取 ignored/untracked 内容。唯一例外是第 8 节点名的正式 raw 目录 12 个只读发布文件。

## 2. `keep_exact`

### 2.1 根与 package 入口

| Source = target | 职责 | 审计证据 / 内容规则 |
|---|---|---|
| `.gitignore` | 排除 cache、secret、本地 raw 与 LaTeX cache | 保留 `/TokenShareData/`、`local/*.local.json`、`local/cache/` 与论文 cache ignore；byte-identical |
| `LICENSE` | 对外许可证 | 根级公开必要文件；byte-identical |
| `requirements.txt` | 最小 Python/pytest 依赖 | 当前 runtime 为标准库、验证用 pytest；byte-identical |
| `src/tokenshare/__init__.py` | package 入口 | 系统闭包入口；byte-identical |
| `src/tokenshare/protocol_engine.py` | 协议 facade | retained tests 与系统纵向路径的公共入口；byte-identical |

`.gitattributes` 不在本表；它必须按第 5 节综合生成，不能保留旧 paper/archive 路径规则。

### 2.2 Core（13 个文件）

| Source = target | 职责 / 保留证据 |
|---|---|
| `src/tokenshare/core/__init__.py` | core 公共导出 |
| `src/tokenshare/core/contribution.py` | contribution 记录与结算规则 |
| `src/tokenshare/core/expansion.py` | 任务展开纯规则 |
| `src/tokenshare/core/leases.py` | lease 生命周期 |
| `src/tokenshare/core/merge.py` | merge 输入与结果规则 |
| `src/tokenshare/core/merge_coordinator.py` | merge 协调状态 |
| `src/tokenshare/core/models.py` | 协议对象 schema |
| `src/tokenshare/core/recovery.py` | replacement/recovery 纯规则 |
| `src/tokenshare/core/registration.py` | worker/executor 注册规则 |
| `src/tokenshare/core/scheduling.py` | 调度合同 |
| `src/tokenshare/core/state_machines.py` | 协议状态机 |
| `src/tokenshare/core/task_graph.py` | task DAG 与依赖 |
| `src/tokenshare/core/verification.py` | verification 决策规则 |

这 13 个文件共同构成实验实际调用的通用协议本体；全部 byte-identical，不把 Factorization、Lean 或实验策略写入 core。

### 2.3 Storage（4 个文件）

| Source = target | 职责 / 保留证据 |
|---|---|
| `src/tokenshare/storage/__init__.py` | storage 公共导出 |
| `src/tokenshare/storage/artifacts.py` | artifact bytes 与引用 |
| `src/tokenshare/storage/events.py` | append-only event ledger |
| `src/tokenshare/storage/sqlite_index.py` | SQLite 投影与查询 |

四项是 coordinator、checker、projector 共同运行闭包；全部 byte-identical。

### 2.4 Local runtime（7 个文件）

| Source = target | 职责 / 保留证据 |
|---|---|
| `src/tokenshare/local_runtime/__init__.py` | runtime 公共导出 |
| `src/tokenshare/local_runtime/contracts.py` | worker/coordinator/hook 合同 |
| `src/tokenshare/local_runtime/coordinator.py` | 单 root 协议生命周期、恢复与 merge |
| `src/tokenshare/local_runtime/logical_scheduler.py` | Exp2–4 逻辑调度能力 |
| `src/tokenshare/local_runtime/process_worker_child.py` | process backend 子进程入口 |
| `src/tokenshare/local_runtime/projection.py` | 协议事实投影 |
| `src/tokenshare/local_runtime/workers.py` | thread/process worker；以 `python -m tokenshare.local_runtime.process_worker_child` 动态启动 child |

七项全部 byte-identical；`workers.py` 与 `process_worker_child.py` 必须成对保留，不能因静态 import 未显示 child 而裁掉。

### 2.5 Plugin common 与 Factorization（13 个 common/Factorization 文件）

| Source = target | 职责 / 内容规则 |
|---|---|
| `src/tokenshare/plugins/__init__.py` | plugin 公共导出；byte-identical |
| `src/tokenshare/plugins/contracts.py` | plugin/split/parse/verify/merge 合同；byte-identical |
| `src/tokenshare/plugins/registry.py` | plugin descriptor registry；byte-identical |
| `src/tokenshare/plugins/factorization/__init__.py` | Factorization 导出；byte-identical |
| `src/tokenshare/plugins/factorization/descriptor.py` | plugin descriptor；byte-identical |
| `src/tokenshare/plugins/factorization/fixtures.py` | 确定性 fixture；byte-identical |
| `src/tokenshare/plugins/factorization/merge_policy.py` | domain merge；byte-identical |
| `src/tokenshare/plugins/factorization/models.py` | domain models；byte-identical |
| `src/tokenshare/plugins/factorization/prompt_builder.py` | AI prompt；byte-identical |
| `src/tokenshare/plugins/factorization/runtime_adapter.py` | runtime adapter；`keep_then_synthesize`，仅 descriptor import 改到 `tokenshare.executors.descriptors` |
| `src/tokenshare/plugins/factorization/schemas.py` | domain schema；byte-identical |
| `src/tokenshare/plugins/factorization/split_strategy.py` | 确定性 range split；byte-identical |
| `src/tokenshare/plugins/factorization/validator.py` | parser/verifier/root check；byte-identical |

### 2.6 Lean 运行闭包（排除 replay evidence 后 16 个文件）

| Source = target | 职责 / 内容规则 |
|---|---|
| `src/tokenshare/plugins/lean_proof/__init__.py` | Lean plugin 导出；byte-identical |
| `src/tokenshare/plugins/lean_proof/checker.py` | 真实本地 Lean checker；byte-identical |
| `src/tokenshare/plugins/lean_proof/child_proof.py` | child proof artifact 流；byte-identical |
| `src/tokenshare/plugins/lean_proof/descriptor.py` | plugin descriptor；byte-identical |
| `src/tokenshare/plugins/lean_proof/environment.py` | toolchain/project environment；`keep_then_synthesize`，只改 frozen logical key→public physical path 解析 |
| `src/tokenshare/plugins/lean_proof/fixed_plan.py` | 预注册 proof plan；byte-identical |
| `src/tokenshare/plugins/lean_proof/fixtures.py` | fixture project 定位；`keep_then_synthesize`，只改 physical path |
| `src/tokenshare/plugins/lean_proof/merge_policy.py` | lemma/root merge；byte-identical |
| `src/tokenshare/plugins/lean_proof/models.py` | Lean domain models；byte-identical |
| `src/tokenshare/plugins/lean_proof/preflight.py` | catalog/checker preflight；`keep_then_synthesize`，保留旧 logical identity 后映射新 bytes |
| `src/tokenshare/plugins/lean_proof/prompt_builder.py` | proof prompt；byte-identical |
| `src/tokenshare/plugins/lean_proof/runtime_adapter.py` | runtime adapter；`keep_then_synthesize`，仅 descriptor import 改到 `tokenshare.executors.descriptors` |
| `src/tokenshare/plugins/lean_proof/schemas.py` | Lean schema；byte-identical |
| `src/tokenshare/plugins/lean_proof/semantic_authority.py` | environment semantic authority；`keep_then_synthesize`，只增加 logical→physical path 映射 |
| `src/tokenshare/plugins/lean_proof/split_strategy.py` | fixed/lemma graph split；byte-identical |
| `src/tokenshare/plugins/lean_proof/validator.py` | parser/checker/root validator；byte-identical |

`src/tokenshare/plugins/lean_proof/replay_evidence.py` 不在运行闭包，见 `exclude_exact`。

### 2.7 Executor 最小闭包

| Source = target | 职责 / 保留证据 |
|---|---|
| `src/tokenshare/executors/contracts.py` | executor descriptor/request/result/status 合同 |
| `src/tokenshare/executors/registry.py` | executor registry |
| `src/tokenshare/executors/deterministic.py` | 通用 deterministic executor；获批计划明确属于系统本体 |
| `src/tokenshare/executors/mock_ai.py` | 通用 mock executor；获批计划明确属于系统本体与离线验证 |
| `src/tokenshare/executors/ai_api_config.py` | provider entry/config schema loader |
| `src/tokenshare/executors/ai_api_transport.py` | SiliconFlow/OpenAI/DeepSeek 纯 transport 与 envelope parser |
| `src/tokenshare/executors/ai_api_artifacts.py` | provider request/raw/usage artifact 持久化辅助 |

七项 byte-identical。`src/tokenshare/executors/__init__.py` 和新 `descriptors.py` 由第 5 节生成；`ai_api.py` 不保留。

### 2.8 System focused test selectors

以下 source=target 测试逐文件保留；`.gitkeep` 不属于测试行为。

| 组 | 精确 selector | 职责 / 证据 |
|---|---|---|
| core | `tests/core/test_lease_manager.py` | lease 分配/过期 |
| core | `tests/core/test_phase1_models.py` | 基础协议模型 |
| core | `tests/core/test_phase4_models.py` | merge/attempt 扩展模型 |
| core | `tests/core/test_phase5_models.py` | recovery/contribution 模型 |
| core | `tests/core/test_recovery.py` | recovery 状态转换 |
| core | `tests/core/test_scheduler.py` | scheduler 纯规则 |
| core | `tests/core/test_state_machines.py` | 状态机边界 |
| core | `tests/core/test_task_graph.py` | DAG 与依赖 |
| storage | `tests/storage/test_artifact_store.py` | artifact round trip |
| storage | `tests/storage/test_attempt_ordinal_migration.py` | attempt ordinal SQLite 兼容合同 |
| storage | `tests/storage/test_event_ledger.py` | event append/read |
| storage | `tests/storage/test_phase2_event_projection.py` | phase2 投影合同 |
| storage | `tests/storage/test_phase3_event_projection.py` | phase3 投影合同 |
| storage | `tests/storage/test_phase4_event_ledger_batch.py` | event batch |
| storage | `tests/storage/test_phase4_event_projection.py` | phase4 投影合同 |
| storage | `tests/storage/test_phase5_event_projection.py` | phase5 投影合同 |
| storage | `tests/storage/test_sqlite_index.py` | index/query |
| local runtime | `tests/local_runtime/test_coordinator_full_lifecycle.py` | coordinator 纵向生命周期 |
| local runtime | `tests/local_runtime/test_coordinator_logical_schedule.py` | coordinator/logical schedule 接线 |
| local runtime | `tests/local_runtime/test_factorization_runtime.py` | Factorization runtime 纵向路径 |
| local runtime | `tests/local_runtime/test_lean_fixed_plan_runtime.py` | fixed Lean plan runtime 接线 |
| local runtime | `tests/local_runtime/test_logical_scheduler.py` | 逻辑调度器 |
| local runtime | `tests/local_runtime/test_runtime_boundaries.py` | runtime 公共边界 |
| local runtime | `tests/local_runtime/test_runtime_hook_observations.py` | hook observation |
| local runtime | `tests/local_runtime/test_submission_and_recovery.py` | submission/replacement |
| local runtime | `tests/local_runtime/test_terminal_failure_projection.py` | terminal failure 投影 |
| local runtime | `tests/local_runtime/test_trace_delivery_parent_commit.py` | trace 与 parent commit |
| local runtime | `tests/local_runtime/test_worker_death_recovery.py` | process child death/recovery |
| Factorization | `tests/plugins/factorization/test_candidate_range_partition.py` | range partition |
| Factorization | `tests/plugins/factorization/test_factorization_ai_parse_policy.py` | parse policy |
| Factorization | `tests/plugins/factorization/test_factorization_merge_policy.py` | merge policy |
| Factorization | `tests/plugins/factorization/test_factorization_parser.py` | parser |
| Factorization | `tests/plugins/factorization/test_factorization_prompt_package.py` | prompt package |
| Factorization | `tests/plugins/factorization/test_factorization_registry.py` | descriptor registry |
| Factorization | `tests/plugins/factorization/test_factorization_runtime_adapter.py` | runtime adapter |
| Factorization | `tests/plugins/factorization/test_factorization_schemas.py` | schema |
| Factorization | `tests/plugins/factorization/test_factorization_split_strategy.py` | split strategy |
| Factorization | `tests/plugins/factorization/test_factorization_verifier.py` | verifier/root check |
| plugin common | `tests/plugins/test_plugin_registry.py` | common registry |

### 2.9 Lean test selectors

| 精确 selector | 职责 / 保留证据 |
|---|---|
| `tests/plugins/lean_proof/test_lean_checker_direct.py` | checker direct accept/reject；最终只运行第 11 节点名的单个真实 smoke |
| `tests/plugins/lean_proof/test_lean_checker_environment_cache.py` | environment cache 合同 |
| `tests/plugins/lean_proof/test_lean_child_proof_flow.py` | child proof artifacts |
| `tests/plugins/lean_proof/test_lean_descriptor_and_schemas.py` | descriptor/schema |
| `tests/plugins/lean_proof/test_lean_environment.py` | environment manifest/path |
| `tests/plugins/lean_proof/test_lean_fixed_plan.py` | fixed plan |
| `tests/plugins/lean_proof/test_lean_fixture_project_manifest.py` | 13-file fixture authority |
| `tests/plugins/lean_proof/test_lean_lemma_graph_certificate.py` | lemma graph certificate |
| `tests/plugins/lean_proof/test_lean_lemma_graph_merge_policy.py` | lemma graph merge |
| `tests/plugins/lean_proof/test_lean_merge_policy.py` | direct/fixed merge |
| `tests/plugins/lean_proof/test_lean_preflight.py` | preflight identity/path |
| `tests/plugins/lean_proof/test_lean_prompt_and_parse_policy.py` | prompt/parser |
| `tests/plugins/lean_proof/test_lean_runtime_adapter.py` | runtime adapter |
| `tests/plugins/lean_proof/test_lean_split_helper.py` | split helper |
| `tests/plugins/lean_proof/test_lean_split_strategy.py` | split strategy |
| `tests/plugins/lean_proof/test_lean_validator.py` | checker-backed validator/root check |

`test_lean_replay_evidence.py` 只服务被删 evidence pipeline；`test_lean_checker_injection.py` 以 checker injection 为威胁模型，按用户范围裁决为 `out_of_scope_by_user`，两者均排除。

### 2.10 Executor test selectors

| 精确 selector | 内容规则 / 职责 |
|---|---|
| `tests/executors/test_ai_api_artifacts.py` | keep exact；artifact helper |
| `tests/executors/test_ai_api_config.py` | keep exact；config loader |
| `tests/executors/test_ai_api_transport.py` | keep exact；SiliconFlow 纯 transport |
| `tests/executors/test_ai_api_openai_transport.py` | keep exact；OpenAI 纯 transport |
| `tests/executors/test_deterministic_executor.py` | keep exact；通用 deterministic executor |
| `tests/executors/test_executor_registry.py` | keep exact；registry |
| `tests/executors/test_mock_ai_executor.py` | keep exact；通用 mock executor |
| `tests/executors/test_ai_api_deepseek_transport.py` | `keep_then_synthesize`；只保留下列六个纯 transport test functions |
| `tests/executors/test_ai_api_descriptor.py` | `keep_then_synthesize`；改为新 `descriptors.py` 合同与旧 executor absence 合同 |

DeepSeek 文件只保留这些 exact functions：

```text
test_build_deepseek_chat_body_uses_fixed_thinking_controls_and_omits_sampling
test_parse_deepseek_response_preserves_reasoning_content_and_usage
test_parse_deepseek_response_maps_429_and_schema_failure
test_deepseek_transport_rejects_empty_or_invalid_json
test_deepseek_transport_accepts_blank_keepalive_before_json
test_deepseek_transport_classifies_timeout
```

同文件删除这些 exact functions，因为它们依赖旧 `_usage_summary` 或 `AIAPIExecutor`：

```text
test_deepseek_usage_cost_uses_cache_breakdown_and_reasoning_tokens
test_deepseek_usage_without_cache_breakdown_is_conservatively_uncached
test_deepseek_usage_missing_is_not_zero_cost
test_deepseek_executor_persists_reasoning_and_safe_resolved_metadata
test_deepseek_executor_timeout_marks_usage_missing
```

## 3. `move_exact`

### 3.1 当前实验源码：14 个一对一映射

每行 content rule 均为 `move_then_synthesize`：只更新公开 package/import/name/path 项，实验参数、公式、schema/pricing/protocol 序列化值不得改变。

| Source | Target | 职责 |
|---|---|---|
| `src/tokenshare/experiments/slim_v2/__init__.py` | `src/tokenshare/experiments/__init__.py` | 新轻量 initializer 的唯一输入；禁止合并旧顶层 initializer |
| `src/tokenshare/experiments/slim_v2/case_source.py` | `src/tokenshare/experiments/case_source.py` | authoritative case/selection load |
| `src/tokenshare/experiments/slim_v2/cli.py` | `src/tokenshare/experiments/cli.py` | 唯一 CLI |
| `src/tokenshare/experiments/slim_v2/execution.py` | `src/tokenshare/experiments/execution.py` | system/plugin/provider bridge |
| `src/tokenshare/experiments/slim_v2/gui.py` | `src/tokenshare/experiments/gui.py` | GUI→CLI single subprocess |
| `src/tokenshare/experiments/slim_v2/lean_environment.py` | `src/tokenshare/experiments/lean_environment.py` | bounded Lean environment pass |
| `src/tokenshare/experiments/slim_v2/profiles.py` | `src/tokenshare/experiments/profiles.py` | frozen full/representative inventory |
| `src/tokenshare/experiments/slim_v2/projector.py` | `src/tokenshare/experiments/projector.py` | protocol facts→root/trace records |
| `src/tokenshare/experiments/slim_v2/provider.py` | `src/tokenshare/experiments/provider.py` | thin provider caller/pricing projection |
| `src/tokenshare/experiments/slim_v2/reducer.py` | `src/tokenshare/experiments/reducer.py` | 11 official metrics payloads |
| `src/tokenshare/experiments/slim_v2/runtime.py` | `src/tokenshare/experiments/runtime.py` | Exp1–5 orchestration/resume |
| `src/tokenshare/experiments/slim_v2/scenarios.py` | `src/tokenshare/experiments/scenarios.py` | Exp2–4 scenario/fault/ablation |
| `src/tokenshare/experiments/slim_v2/schema.py` | `src/tokenshare/experiments/schema.py` | ordinary schema contracts |
| `src/tokenshare/experiments/slim_v2/storage.py` | `src/tokenshare/experiments/storage.py` | ordinary JSON/JSONL/resume store |

所有 source/test imports 从 `tokenshare.experiments.slim_v2` 改为 `tokenshare.experiments`。`gui.py:15` 的动态 module 字符串改为 `tokenshare.experiments.cli`。移动后 `cli.py:62`、`profiles.py:901`、`runtime.py:88` 的 `Path(__file__).parents[4]` 必须分别改为 `parents[3]`。

目标 `src/tokenshare/experiments/__init__.py` 必须由原嵌套 initializer 综合成轻量公开导出，只导出当前实验 schema/public symbols；不得读取、拼接或兼容现有 legacy `src/tokenshare/experiments/__init__.py`，不得创建 wrapper/alias/import hook。

### 3.2 当前实验 tests：12 个测试与 2 个 fixture

每行 content rule 为 `move_then_synthesize`，仅改 imports、公开符号名和公开路径 fixture；测试行为与 golden facts 不变。

| Source | Target | 职责 |
|---|---|---|
| `tests/experiments/slim_v2/test_answer_paths.py` | `tests/experiments/test_answer_paths.py` | real/fixed answer paths |
| `tests/experiments/slim_v2/test_case_source.py` | `tests/experiments/test_case_source.py` | corpus/selection source |
| `tests/experiments/slim_v2/test_cli_e2e.py` | `tests/experiments/test_cli_e2e.py` | fake CLI vertical |
| `tests/experiments/slim_v2/test_cli_plan.py` | `tests/experiments/test_cli_plan.py` | plan arrays/bounds |
| `tests/experiments/slim_v2/test_gui_launcher.py` | `tests/experiments/test_gui_launcher.py` | public GUI module/argv |
| `tests/experiments/slim_v2/test_lean_environment.py` | `tests/experiments/test_lean_environment.py` | environment pass/cache path |
| `tests/experiments/slim_v2/test_profiles.py` | `tests/experiments/test_profiles.py` | full profile identity |
| `tests/experiments/slim_v2/test_reducer_golden.py` | `tests/experiments/test_reducer_golden.py` | reducer 11-payload golden |
| `tests/experiments/slim_v2/test_runtime_resume.py` | `tests/experiments/test_runtime_resume.py` | crash/resume/no duplicate provider |
| `tests/experiments/slim_v2/test_scenarios.py` | `tests/experiments/test_scenarios.py` | Exp2–4 rules |
| `tests/experiments/slim_v2/test_schema.py` | `tests/experiments/test_schema.py` | schema validation |
| `tests/experiments/slim_v2/test_system_vertical.py` | `tests/experiments/test_system_vertical.py` | system/plugin fake vertical |
| `tests/experiments/slim_v2/fixtures/authority_contract.v1.json` | `tests/experiments/fixtures/authority_contract.v1.json` | metric/schema authority fixture |
| `tests/experiments/slim_v2/fixtures/reducer_golden_run.v1.json` | `tests/experiments/fixtures/reducer_golden_run.v1.json` | reducer golden fixture |

### 3.3 Launcher

| Source | Target | Content rule / 职责 |
|---|---|---|
| `run_slim_v2.cmd` | `run_experiments.cmd` | `move_then_synthesize`；只启动 `python -m tokenshare.experiments.gui`，变量前缀、错误文本与 UI 名称使用 `TOKENSHARE_EXPERIMENTS_` / `TokenShare Experiments` |

### 3.4 Authoritative corpus 与 provider configs

第 3.4 节所有 SHA-256 都是审计时 working-file raw bytes 的内容哈希，不是 Git object ID。Task B 必须在创建 freeze commit 与 annotated tag 之前完成以下 raw-byte 前置条件，否则抽取不得开始：

1. 用 `apply_patch` 在 source `.gitattributes` 中逐行加入以下 20 个 exact source path 的 `-text`；不得使用目录规则或 glob：

```gitattributes
benchmarks/paper/factorization_catalog.v2.jsonl -text
benchmarks/paper/lean_catalog.v1.jsonl -text
benchmarks/paper/lean_checker_preflight.v1.json -text
benchmarks/paper/lean_environment_semantic_authority.v1.json -text
benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl -text
benchmarks/paper/exp1_baseline_provider_config.v3.json -text
benchmarks/paper/exp5_siliconflow_provider_config.v3.json -text
fixtures/lean_proof_project/lake-manifest.json -text
fixtures/lean_proof_project/lakefile.lean -text
fixtures/lean_proof_project/lean-toolchain -text
fixtures/lean_proof_project/TokenShare.lean -text
fixtures/lean_proof_project/TokenShare/Helper.lean -text
fixtures/lean_proof_project/TokenShare/LemmaGraphCases.lean -text
fixtures/lean_proof_project/TokenShare/LemmaGraphOracle.lean -text
fixtures/lean_proof_project/TokenShare/Merge.lean -text
fixtures/lean_proof_project/TokenShare/SplitRules.lean -text
fixtures/lean_proof_project/TokenShare/Fixtures/Decomposition.lean -text
fixtures/lean_proof_project/TokenShare/Fixtures/Direct.lean -text
fixtures/lean_proof_project/TokenShare/Fixtures/Invalid.lean -text
fixtures/lean_proof_project/TokenShare/Fixtures/Unsupported.lean -text
```

2. 在 stage 前对这 20 个 working files 逐个执行 `Get-FileHash -Algorithm SHA256`，结果必须等于下表对应 SHA-256；保存这份 pre-stage map。然后只执行 `git add -- .gitattributes` 以及对上述 20 个 exact path 的逐文件 `git add --`。不得运行 formatter、checkout、`git add --renormalize` 或任何会改 working-file bytes 的命令。
3. Stage 后再次对 20 个 working files 执行 `Get-FileHash -Algorithm SHA256` 并逐值等于 pre-stage map；随后提交 raw bytes。创建 annotated tag 后解析 peeled SHA，以不经过 checkout 和 text conversion 的 `git cat-file blob` 读取每个 `$frozenSha:path` 的 stdout raw bytes，计算 SHA-256，并逐值等于下表。任何值不等立即停止。Git blob object ID 是 Git 对 blob header 与内容计算的对象标识；本表 SHA-256 只对 raw content bytes 计算，两者不得互换。
4. Freeze tag 产生后，所有抽取只读 peeled commit 的 blob；不得再读取 source live checkout。Sidecar 的 raw bytes 必须保持 byte-identical，尤其不得解析后重新序列化。

Target synthesized `.gitattributes` 必须逐行包含以下 20 个 exact public path 的 `-text`，保证启用 `core.autocrlf` 的 fresh checkout 仍保持 frozen raw bytes；不得以旧 source EOL 推断或转换：

```gitattributes
benchmarks/experiments/factorization_catalog.v2.jsonl -text
benchmarks/experiments/lean_catalog.v1.jsonl -text
benchmarks/experiments/lean_checker_preflight.v1.json -text
benchmarks/experiments/lean_environment_semantic_authority.v1.json -text
benchmarks/experiments/lean_lemma_graph_catalog.v1.jsonl -text
configs/experiments/exp1_baseline_provider_config.v3.json -text
configs/experiments/exp5_siliconflow_provider_config.v3.json -text
benchmarks/experiments/fixtures/lean_proof_project/lake-manifest.json -text
benchmarks/experiments/fixtures/lean_proof_project/lakefile.lean -text
benchmarks/experiments/fixtures/lean_proof_project/lean-toolchain -text
benchmarks/experiments/fixtures/lean_proof_project/TokenShare.lean -text
benchmarks/experiments/fixtures/lean_proof_project/TokenShare/Helper.lean -text
benchmarks/experiments/fixtures/lean_proof_project/TokenShare/LemmaGraphCases.lean -text
benchmarks/experiments/fixtures/lean_proof_project/TokenShare/LemmaGraphOracle.lean -text
benchmarks/experiments/fixtures/lean_proof_project/TokenShare/Merge.lean -text
benchmarks/experiments/fixtures/lean_proof_project/TokenShare/SplitRules.lean -text
benchmarks/experiments/fixtures/lean_proof_project/TokenShare/Fixtures/Decomposition.lean -text
benchmarks/experiments/fixtures/lean_proof_project/TokenShare/Fixtures/Direct.lean -text
benchmarks/experiments/fixtures/lean_proof_project/TokenShare/Fixtures/Invalid.lean -text
benchmarks/experiments/fixtures/lean_proof_project/TokenShare/Fixtures/Unsupported.lean -text
```

| Source | Target | SHA-256 | 记录数 / 内容规则 |
|---|---|---|---|
| `benchmarks/paper/factorization_catalog.v2.jsonl` | `benchmarks/experiments/factorization_catalog.v2.jsonl` | `9ce2b31a199455a37c0ca5afdee68e03540dc4912c4c4fe28e87ed3503467774` | 500；byte-identical |
| `benchmarks/paper/lean_catalog.v1.jsonl` | `benchmarks/experiments/lean_catalog.v1.jsonl` | `1b2b709ce459d1c72966c708bf6fabfd6a747842fdbd853f2b0d42b74a3052c3` | 30；byte-identical |
| `benchmarks/paper/lean_checker_preflight.v1.json` | `benchmarks/experiments/lean_checker_preflight.v1.json` | `3b8f0597466e4ed1836719f133a1698c75c9b063c6f60658720d9e6358c323c7` | 600 preflight entries；byte-identical |
| `benchmarks/paper/lean_environment_semantic_authority.v1.json` | `benchmarks/experiments/lean_environment_semantic_authority.v1.json` | `b5bfb38160086bbbd3266da7f82a9c1f7dea51f4ae34ab4cbc91b32c74b29f13` | 必须 byte-identical |
| `benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl` | `benchmarks/experiments/lean_lemma_graph_catalog.v1.jsonl` | `5a134f246d45ad302ead57ef6eb9559b0b040ed85fac50dc6af49974756f2cdc` | 165；byte-identical |
| `benchmarks/paper/exp1_baseline_provider_config.v3.json` | `configs/experiments/exp1_baseline_provider_config.v3.json` | `12a958510d1204738c162ad0e6754e550e41dce507dc796977c86da351bab30c` | byte-identical；新 physical default 由代码选择 |
| `benchmarks/paper/exp5_siliconflow_provider_config.v3.json` | `configs/experiments/exp5_siliconflow_provider_config.v3.json` | `4760850bc7929dd97d8c1fe6d811359aee4c6f3e663f49faf532b0e6a0481af3` | byte-identical；新 physical default 由代码选择 |

实际 Lean project source 是根 `fixtures/lean_proof_project/`，不是 `benchmarks/paper/fixtures/`。13 个 tracked files 全部 byte-identical 映射如下；`.lake/` 明确排除。

| Source | Target | SHA-256 |
|---|---|---|
| `fixtures/lean_proof_project/lake-manifest.json` | `benchmarks/experiments/fixtures/lean_proof_project/lake-manifest.json` | `1b07ce0a9898b379578390d24abf183a3c319bdb4b076c18ea31edf1daf2e869` |
| `fixtures/lean_proof_project/lakefile.lean` | `benchmarks/experiments/fixtures/lean_proof_project/lakefile.lean` | `288f5b67bd53c82742d276e5ed67714279bdce920c54a6986c1b4a7ef5032d39` |
| `fixtures/lean_proof_project/lean-toolchain` | `benchmarks/experiments/fixtures/lean_proof_project/lean-toolchain` | `61561b06f5587e027815fac8f59bf6ca160dfbf3f7372fc6297664acfd85b8c0` |
| `fixtures/lean_proof_project/TokenShare.lean` | `benchmarks/experiments/fixtures/lean_proof_project/TokenShare.lean` | `86a6496322cc4049f9f443b19399766e2dd450e0f0f3d9b21858487b4e40786d` |
| `fixtures/lean_proof_project/TokenShare/Helper.lean` | `benchmarks/experiments/fixtures/lean_proof_project/TokenShare/Helper.lean` | `9e98398cb779e5fc5b55cd0a8e521a56f58475285ef6fcd977d73fc62238b02c` |
| `fixtures/lean_proof_project/TokenShare/LemmaGraphCases.lean` | `benchmarks/experiments/fixtures/lean_proof_project/TokenShare/LemmaGraphCases.lean` | `f015a29cd0eb90854a5b247a52c420a4885287c20c397f11f2490f1a5c857784` |
| `fixtures/lean_proof_project/TokenShare/LemmaGraphOracle.lean` | `benchmarks/experiments/fixtures/lean_proof_project/TokenShare/LemmaGraphOracle.lean` | `4af791228bd24f4ea6d92fed511be8eee4f29d1ba8db5c6102968e743a8047ca` |
| `fixtures/lean_proof_project/TokenShare/Merge.lean` | `benchmarks/experiments/fixtures/lean_proof_project/TokenShare/Merge.lean` | `f149dc0171a55156891ecd415fc3889e9030e2b81ce53697b1e1c54a51252fc7` |
| `fixtures/lean_proof_project/TokenShare/SplitRules.lean` | `benchmarks/experiments/fixtures/lean_proof_project/TokenShare/SplitRules.lean` | `6716f703905abe0b230215ac20886eb96b8e929aec27fb9c78265a7450aee5c1` |
| `fixtures/lean_proof_project/TokenShare/Fixtures/Decomposition.lean` | `benchmarks/experiments/fixtures/lean_proof_project/TokenShare/Fixtures/Decomposition.lean` | `c974b8649b3eadf4738d39e3d852e8a958a5e75e2d751da01759e82cb2f919e0` |
| `fixtures/lean_proof_project/TokenShare/Fixtures/Direct.lean` | `benchmarks/experiments/fixtures/lean_proof_project/TokenShare/Fixtures/Direct.lean` | `06378524b81f22aa3ec652e96cc970be076aa390ab2ed6ada7f66f91013d8653` |
| `fixtures/lean_proof_project/TokenShare/Fixtures/Invalid.lean` | `benchmarks/experiments/fixtures/lean_proof_project/TokenShare/Fixtures/Invalid.lean` | `45393c97a1a61a4dd5607da8bbfb73690657b9f1ba61e07d39180a581cfcde85` |
| `fixtures/lean_proof_project/TokenShare/Fixtures/Unsupported.lean` | `benchmarks/experiments/fixtures/lean_proof_project/TokenShare/Fixtures/Unsupported.lean` | `a6e79e5563ef4dccf0d7985e941c247d70e6e418ba0ba0bbc65785b33ac0619b` |

Sidecar 中的旧路径是冻结 logical identity，不能重写 sidecar。实现必须先以旧 key 校验记录的 digest，再把 key 映射到上表 public physical path 读取 bytes。严格旧 key allowlist 见第 10 节。Sidecar exact structure counts 是：top-level fields=`4`，字段名为 `schema_version`、`authority`、`semantic_projection`、`sidecar_digest`；`authority.raw_files=3`；`semantic_projection.environment.files=12`；`semantic_projection.checker.files=1`。验证器必须同时比较这些计数、键名、raw SHA-256 与逐项 mapping。

### 3.5 Paper 的 pre-freeze 归一化与 target keep

Task B 必须先完成下列来源归一化；抽取 writer 随后只从 `frozen_sha` 的 `paper/` 同路径恢复 10 个文件。

| Audit source | Frozen/target path | 内容规则 / 证据 |
|---|---|---|
| `paper.bbl` | `paper/paper.bbl` | exact move，bytes 不变；构建复现输入 |
| `reference.bib` | `paper/reference.bib` | exact move，bytes 不变；唯一 bibliography database |
| `paper.tex` | `paper/paper.tex` | 不是纯移动；Task B 必须先按下表纠正权威冲突并冻结 |
| `paper/AGENTS.md` | `paper/AGENTS.md` | keep exact；论文 workspace 规则 |
| `paper/DETAIL_REQUESTS.md` | `paper/DETAIL_REQUESTS.md` | keep exact；代码细节提问通道 |
| `paper/IMPLEMENTATION_DETAILS.md` | `paper/IMPLEMENTATION_DETAILS.md` | keep exact；代码级参考 |
| `paper/LEAN_FOUR_NODE_DAG_EXAMPLES.md` | `paper/LEAN_FOUR_NODE_DAG_EXAMPLES.md` | keep exact；Lean DAG 例子 |
| `paper/PAPER_TERMINOLOGY_MAPPING.md` | `paper/PAPER_TERMINOLOGY_MAPPING.md` | keep exact；术语映射 |
| `paper/PAPER_WRITING_BRIEF.md` | `paper/PAPER_WRITING_BRIEF.md` | keep exact；论文共同基线 |
| `paper/PROFESSOR_REVISION_GUIDANCE.md` | `paper/PROFESSOR_REVISION_GUIDANCE.md` | keep exact；论文修订指导 |

Task B 对 `paper/paper.tex` 的冻结阻断项：

| 当前冲突位置 | 必须冻结的事实 |
|---|---|
| lines 1086–1096、1114–1115 | Exp1 是 300 Factorization + 135 Lean，provider/model 为 DeepSeek V4 Flash，不是 GLM-5.2，也不是 500 Factorization |
| lines 1029–1032、1138–1157 | Exp2 是 50 个 hard Factorization roots，完整继承 Exp1 plan；不得保留独立 20-way split/166 roots |
| lines 1168–1198 | Exp3 使用 50-root trace replay，provider calls=0；不得描述 fault/replacement 新真实调用或 500-root条件 |
| lines 1209–1219 | Exp4 是 FULL + 4 single + 6 pair 共 11 modes，50 Factorization + 15 Lean；不得保留五模式/500 Factorization |
| lines 1237–1246 | Exp5 models 为 `zai-org/GLM-5.2`、`Qwen/Qwen3-14B`、`MiniMaxAI/MiniMax-M2.5`；每 model 37 roots，`repeat_id=0` |
| lines 1616–1617 | bibliography 必须从 `paper/reference.bib` 对应的 `\bibliography{reference}` 读取，不得保留 `wine2026/reference` |

Task B 修正并提交后，抽取 writer 必须验证 `paper/` 仅含上述 10 个 tracked files；`paper/out/` 与 LaTeX cache 不进入 freeze-to-target 抽取集。

### 3.6 正式结果：12 个发布文件

只读 source root：`TokenShareData/outputs/slim_v2/slim-v2-full-flash-20260823-233000-b4c8e951/`。Target root：`results/experiments/slim-v2-full-flash-20260823-233000-b4c8e951/`。下列相对路径不变：

| Relative path | SHA-256 | Bytes | Data rows |
|---|---|---:|---:|
| `run.json` | `3fee9e896eeac6be2a4fa082dcdddef4db92b21742b6234333bc11a30e2e9478` | 622 | n/a |
| `metrics/summary.json` | `8a7f0d77d146e97d633f14cc4284501b2f4ecb479b70157e2559ece913b76932` | 98,364 | n/a |
| `metrics/tables/exp1.csv` | `7518c226980a314b7879695fa9867865c0193c4da242b8eb20a8c24fadfce426` | 55,478 | 12 |
| `metrics/tables/exp1.jsonl` | `78f9f20c3369ffdd6b2c030b0a8816f73a91363c95ebb86177ebfa9320527611` | 90,523 | 12 |
| `metrics/tables/exp2.csv` | `5d19eff9a21b834f12d4122702758610d6743bed94de72704efca0e333e761d2` | 450,853 | 88 |
| `metrics/tables/exp2.jsonl` | `f791025e30ca96cde1834d2974bf7ee60eabd7e5f64549fd661d6103c4931760` | 774,706 | 88 |
| `metrics/tables/exp3.csv` | `c5d2b0337e821ada6581a9fa79465c8ff8313b57c694aad576088fe507d4fe1a` | 3,608,819 | 684 |
| `metrics/tables/exp3.jsonl` | `989a236aa0ed833e065ed2ce5e0a82a0ae0a2e1ab3a18303066480956a7f554e` | 6,148,600 | 684 |
| `metrics/tables/exp4.csv` | `5f905557262801d3bf5840d5976b5d92b0b69cf704cec01c5d6d2e7d54de25c3` | 8,331,030 | 648 |
| `metrics/tables/exp4.jsonl` | `429fc896b83538b383e97e401ab91ef88fc9549e4541a77285ee1d0aa5c2c1d5` | 13,736,952 | 648 |
| `metrics/tables/exp5.csv` | `32bddb953f56e3e5c85d560f70711d245a56260360c6a61c055a2e5e711df4ed` | 18,976 | 3 |
| `metrics/tables/exp5.jsonl` | `c7770c2b5571c68ca3dbb65c8e579ec8044b5e00d56bf2de4c2e4434d1b2ded5` | 25,955 | 3 |

复制前后必须逐文件比较 SHA/bytes/row count；任一差异即停止，不调用 reducer 覆盖 source 或 target。

正式 summary 事实必须同时验证：formal metric occurrences=`153`、unique metric IDs=`129`；observed provider calls Exp1–5=`2124/0/0/0/808`；roots/references/calls/responses/traces=`7017/106/5889/2565/1964`。Raw 保留状态固定为 `pending_advisor_archive_decision`；不复制 7.14 GB raw，不虚构 URL。

Raw 不变量：非 metrics 文件=`1,088,134`，bytes=`7,143,234,452`，metadata fingerprints=`9aa66fec8352982279de3e87fc1eae81` 与 `d59f593eeb7604374f05efecad4c142d`，critical files=`21,260`，critical-file manifest SHA-256=`96dc9ea143d7532c0e54482907bb12017247df4286a6656fbdc45f84705fea47`。

## 4. 完整 selection / corpus identity

`benchmarks/experiments/manifest.v1.json` 与 `verification/verify_authoritative_corpus.py` 必须保存并比较完整排序数组，不得只保存下面摘要或抽样：

| Identity | 冻结值 |
|---|---|
| Factorization catalog sorted case-ID SHA-256 | `b05d785aa1f4ec2202ffa4f6f43f36fc11d6aaf2f61004591f3c2f1392c0d513` |
| Lean lemma-graph catalog sorted case-ID SHA-256 | `9be5537cddf2a280e74ce85a202c8d8667b36a023e2b1796ca4c0d26fe907adc` |
| Exp1–5 unique case counts | `435 / 50 / 53 / 65 / 37` |
| Exp1–5 paper roots | `435 / 600 / 3726 / 2145 / 111`；总计 `7017` |
| Exp3 references | `106` |
| execution roots | `7123` |
| Exp1–5 condition counts | `12 / 48 / 342 / 33 / 12` |
| downstream trace-consuming case union | `99` |
| ordered root identity SHA-256 | `b6ae545102c57f73b1b6c7641b630944d024ba0e757b637a3253b26c6c68bdb7` |
| ordered reference identity SHA-256 | `9e67170af1c15400c50fc5c8d79ce29695838a4aef1d9d6b909410a10323c351` |
| Exp4 challenge plans | `195`，SHA-256=`396bde3849cc3c0f37e67a9b880a34e114a8f1215b5e438b62c62c14b34bc057` |
| provider-call upper bound | total `6762`；Exp1–5=`5910/0/0/0/852` |

验证必须比较 source baseline JSON 中完整的 ordered case IDs、root identity tuples、reference identity tuples、condition objects、195 challenge objects 与 provider-bound objects，同位置逐元素相等；摘要 SHA 只作为第二重证据。

## 5. `synthesize`

| Target | 唯一输入 / 职责 | 必须验证 |
|---|---|---|
| `.gitattributes` | 逐行写入第 3.4 节列出的 20 个 exact public path `-text`；不保留旧 paper/archive/output 规则 | fresh checkout raw bytes 的 SHA-256 与第 3.4 节一致；无 glob、无 EOL 转换、无旧路径规则 |
| `src/tokenshare/executors/descriptors.py` | 只从 frozen `src/tokenshare/executors/ai_api.py:251-284` 拆出 `build_ai_api_executor_descriptor` | siliconflow/openai/deepseek 逐字段等价；非法 family `ValueError`；文件名不得为 `ai_api_descriptor.py` |
| `src/tokenshare/executors/__init__.py` | 重写为 contracts/registry/deterministic/mock/config/transport/artifacts/descriptors 的轻量导出 | 不导出/导入 `AIAPIExecutor`、local config、selector、replay、response bank 或 trace-backed |
| `src/tokenshare/plugins/factorization/runtime_adapter.py` | frozen blob + descriptor import 变更 | 不 import `executors.ai_api`；其余 diff 为空 |
| `src/tokenshare/plugins/lean_proof/runtime_adapter.py` | frozen blob + descriptor import 变更 | 不 import `executors.ai_api`；其余 diff 为空 |
| `src/tokenshare/plugins/lean_proof/environment.py` | frozen blob + logical→physical mapping | 旧 key 验 digest，新路径读 bytes；checker 语义不变 |
| `src/tokenshare/plugins/lean_proof/fixtures.py` | frozen blob + fixture physical root | 只解析 `benchmarks/experiments/fixtures/lean_proof_project` |
| `src/tokenshare/plugins/lean_proof/preflight.py` | frozen blob + logical→physical mapping | sidecar bytes 不改，600 entries 对等 |
| `src/tokenshare/plugins/lean_proof/semantic_authority.py` | frozen blob + logical→physical mapping | semantic-authority SHA 仍为第 3.4 节值 |
| `src/tokenshare/experiments/__init__.py` | 原 `slim_v2/__init__.py` 的当前 schema/public 面 | 新轻量 initializer；绝不合并 legacy initializer；无 wrapper |
| 第 3.1 节其余 13 个 experiment targets | 对应唯一 frozen source blob | imports/public names/runtime paths 按下文改名，科学与序列化合同不变 |
| `tests/executors/test_ai_api_descriptor.py` | frozen descriptor tests + 监督裁决 | 新 import、三 provider families、invalid family、registry 等价、旧 export absence |
| `tests/executors/test_ai_api_deepseek_transport.py` | 第 2.10 节六个 exact functions | 不 import `_usage_summary`/`AIAPIExecutor` |
| `benchmarks/experiments/manifest.v1.json` | 第 3.4、4 节完整 arrays/hashes/mappings | 完整 identity 对等，不抽样 |
| `tests/experiments/test_authoritative_corpus.py` | manifest 与 public corpus | 完整 arrays、SHA、13 fixture 与 sidecar mapping |
| `README.md` | 系统、唯一 experiments 入口、paper/results 路由 | 不把工程代号当产品名，不链接旧主线 |
| `AGENTS.md` | clean repository 工作/范围/验证规则 | 只描述当前系统与 experiments；不带 Rxx/relay |
| `REPRODUCIBILITY.md` | install、plan、fake smoke、official reducer 只读复核 | 无本机绝对 source dependency；raw pending 状态真实 |
| `RESULTS.md` | 12 发布文件与 raw archive 状态 | 历史 run ID 说明；不声称外部 archive 已存在 |
| `run_experiments.cmd` | 第 3.3 节唯一 launcher | module/env/UI 公共命名 |
| `Doc/Experiments/README.md` | 唯一实验文档入口 | 当前路径/命令 |
| `Doc/Experiments/design.md` | 设计宪章与获批实现综合 | 不复制过程状态 |
| `Doc/Experiments/metrics.md` | Exp1–5 条件、公式、失败口径 | Exp2–4=0 provider 且复用 Exp1 普通真实回答 |
| `Doc/Experiments/system-integration.md` | core/runtime/plugin/executor 接线 | 不带旧 gate/authority |
| `Doc/Experiments/corpus-manifest.md` | corpus/selection 人类说明 | 与机器 manifest 全量事实一致 |
| `Doc/Experiments/code-map.md` | retained source/tests/verification 路由 | 与最终 tree 一致 |
| `results/experiments/slim-v2-full-flash-20260823-233000-b4c8e951/manifest.json` | 第 3.6 节 12 files、`frozen_sha`、run ID、raw 状态 | 12 SHA/bytes/rows 全匹配；不含 raw body/secret |
| `verification/run_verification.py` | system/experiments 两个 focused gate 编排 | 只运行第 11 节 exact commands；network calls=0 |
| `verification/fast-tests.txt` | retained 非 Lean system selectors + experiment selectors | 无 legacy paper/formal/LeanAudit profile |
| `verification/verify_authoritative_corpus.py` | 第 3.4、4 节 corpus/完整 arrays | 完整数组与 identity 逐元素对等，非抽样 |
| `verification/verify_official_results.py` | 第 3.6 节 result/reducer 只读验证 | 第 8 节 tripwire 合同 |
| `verification/verify_extraction.py` | ancestry、tracked boundary、absence、allowlist | default-deny 与第 9、10 节全量扫描 |

公开 Python symbol 必须作以下 exact 映射；持久化 string values 不随 Python symbol 改写：

```text
SlimRunConfigV1 -> ExperimentRunConfigV1
SlimLeanExecutionBridge -> ExperimentLeanExecutionBridge
SLIM_V2_SCHEMA_VERSION -> EXPERIMENT_SCHEMA_VERSION
```

当前 runtime 名称必须改为：

| 旧 runtime 名称 | 新名称 |
|---|---|
| `TokenShareData/outputs/slim_v2` | `TokenShareData/outputs/experiments` |
| `local/cache/slim_v2/lean_environment_pass.v1.json` | `local/cache/experiments/lean_environment_pass.v1.json` |
| `tokenshare.experiments.slim_v2.cli` | `tokenshare.experiments.cli` |
| `tokenshare-slim-v2` | `tokenshare-experiments` |
| `.slim-v2.lock` | `.experiments.lock` |
| `slim-v2-%Y%m%d-%H%M%S` | `experiments-%Y%m%d-%H%M%S` |
| `TokenShare Slim V2` | `TokenShare Experiments` |
| `TOKENSHARE_SLIM_V2_REPO` | `TOKENSHARE_EXPERIMENTS_REPO` |
| `TOKENSHARE_SLIM_V2_EXIT` | `TOKENSHARE_EXPERIMENTS_EXIT` |
| `SLIM_V2_JOURNAL_RESUME_NOT_USED` | `TOKENSHARE_EXPERIMENTS_JOURNAL_RESUME_NOT_USED` |
| `SLIM_V2_RESUME_NOT_USED` | `TOKENSHARE_EXPERIMENTS_RESUME_NOT_USED` |
| `tokenshare_slim_v2_lean_environment_` | `tokenshare_experiments_lean_environment_` |
| `slim_v2_lean_environment_test` | `experiments_lean_environment_test` |
| `slim_v2_profile_id` | `experiments_profile_id` |
| `slim_v2_provider` | `experiments_provider` |
| `slim_v2_domain_parser` | `experiments_domain_parser` |
| `slim_v2_structural_verification_bypass` | `experiments_structural_verification_bypass` |
| `slim_v2_scenario_candidate` | `experiments_scenario_candidate` |
| `slim_v2_mode_blind_challenge` | `experiments_mode_blind_challenge` |

动态 request/artifact IDs 中的 `slim_` / `slim_v2_` 前缀也改为 `experiments_`；formal raw ignored 目录不重写。只有第 10 节逐值列出的冻结 schema/pricing/protocol 与发布事实可以继续含历史代号。

## 6. `exclude_exact`

### 6.1 Source/runtime/tests

| Exact path / selector | 原因 |
|---|---|
| `src/tokenshare/runtime_paths.py` | retained 闭包无 import；旧全局输出路由不进入唯一 experiments 路径 |
| `src/tokenshare/replay/**` | 旧 replay 系统，不在当前普通 resume/reducer 闭包 |
| `src/tokenshare/plugins/lean_stub/**` | 历史 stub，不是真实 Lean plugin |
| `src/tokenshare/plugins/lean_proof/replay_evidence.py` | 只服务旧 evidence pipeline |
| `src/tokenshare/executors/ai_api.py` | 巨型旧 `AIAPIExecutor`；只允许第 5 节 251–284 行 descriptor builder 拆分 |
| `src/tokenshare/executors/ai_api_local_config.py` | 明文 local-config loader 不属于公开最小 config 能力 |
| `src/tokenshare/executors/ai_api_replay.py` | 旧 replay |
| `src/tokenshare/executors/ai_api_request_identity.py` | 旧 request identity/gate 负担 |
| `src/tokenshare/executors/ai_api_selector.py` | 旧 selector/failover executor 层 |
| `src/tokenshare/executors/response_bank.py` | 已排除 response-bank authority |
| `src/tokenshare/executors/trace_backed.py` | 旧 trace-backed executor；当前 fixed response 在 experiments 内 |
| audit/freeze `src/tokenshare/experiments/**` minus 第 3.1 节 14 个 exact source paths | 旧实验约 77 文件；包括 legacy initializer、paper/formal runner、gate、budget、evidence、checkpoint、report，不进入目标 |
| audit/freeze `tests/experiments/**` minus 第 3.2 节 14 个 exact source paths | 旧实验 tests；不得为 retained test 引入 paper helper |
| `tests/integration/test_paper_protocol_runtime_integration.py` | 旧 paper runtime integration helper |
| `tests/plugins/lean_proof/test_lean_replay_evidence.py` | 对应被删 replay evidence |
| `tests/plugins/lean_proof/test_lean_checker_injection.py` | 安全/攻击型 checker injection；`out_of_scope_by_user` |
| `tests/plugins/lean_stub/**` | 对应被删 stub |
| `tests/executors/test_ai_api_executor_failover.py` | 旧 `AIAPIExecutor` |
| `tests/executors/test_ai_api_executor_parser.py` | 旧 `AIAPIExecutor` |
| `tests/executors/test_ai_api_executor_success.py` | 旧 `AIAPIExecutor` |
| `tests/executors/test_ai_api_local_config.py` | 被删 local-config loader |
| `tests/executors/test_ai_api_replay_guard.py` | 被删 replay |
| `tests/executors/test_ai_api_request_identity.py` | 被删 request identity |
| `tests/executors/test_ai_api_selector.py` | 被删 selector |
| `tests/executors/test_ai_api_siliconflow_smoke.py` | 真实网络 smoke；本轮禁止 provider/network |
| `tests/executors/test_response_bank.py` | 被删 response bank |
| `tests/executors/test_trace_backed.py` | 被删 trace-backed |

DeepSeek 混合文件的五个 exact function 排除项已在第 2.10 节列全，不用整文件 selector 替代。

### 6.2 其余 25 个旧 benchmark 文件

以下 25 个 tracked paths 全部排除；它们是旧 profile/cohort/smoke/metric contract，不是正式 corpus/config：

```text
benchmarks/paper/epd027_pipeline_profile.v1.json
benchmarks/paper/exp1_baseline_provider_config.v1.json
benchmarks/paper/exp1_baseline_provider_config.v2.json
benchmarks/paper/exp1_minimal_pilot_profile.v1.json
benchmarks/paper/exp1_minimal_pilot_profile.v2.json
benchmarks/paper/exp1_minimal_pilot_profile.v3.json
benchmarks/paper/exp5_hard_half_selection.v3.json
benchmarks/paper/exp5_parent_quarter_selection.v4.json
benchmarks/paper/factorization_catalog.v1.jsonl
benchmarks/paper/lean_task14_3x3_readiness.v1.json
benchmarks/paper/model_comparison_cohort.v1.json
benchmarks/paper/model_comparison_cohort.v2.json
benchmarks/paper/model_comparison_cohort.v3.json
benchmarks/paper/model_comparison_entry_map.v3.json
benchmarks/paper/paper_factorization_sampling_profile.v1.json
benchmarks/paper/paper_metric_contract.v1.json
benchmarks/paper/paper_smoke_exp1_exp4_profile.v1.json
benchmarks/paper/paper_smoke_exp1_exp4_profile.v2.json
benchmarks/paper/paper_smoke_exp3_exp4_profile.v1.json
benchmarks/paper/paper_smoke_exp5_profile.v3.json
benchmarks/paper/paper_smoke_exp5_profile.v4.json
benchmarks/paper/paper_smoke_profile.v1.json
benchmarks/paper/paper_smoke_profile.v2.json
benchmarks/paper/paper_smoke_profile.v3.json
benchmarks/paper/paper_suite_scale_profile.v1.json
```

另排除 `fixtures/lean_proof_project/.lake/**`；只允许第 3.4 节 13 个 tracked fixture files。

### 6.3 Paper/docs/root/harness/process artifacts

| Exact path / selector | 原因 |
|---|---|
| `paper/out/**` | LaTeX build cache |
| `paper/**/*.synctex.gz`, `paper/**/*.aux`, `paper/**/*.log`, `paper/**/*.fls`, `paper/**/*.fdb_latexmk`, `paper/**/*.toc`, `paper/**/*.blg` | 生成缓存；selector 对 `paper/` tracked tree 全量匹配 |
| audit/freeze `paper/**` minus 第 3.5 节 10 个 exact target paths | 论文 workspace 只保留获批 10 文件 |
| `Doc/**` | 不原样抽取任何旧 Doc；目标只由第 5 节六个 `Doc/Experiments/` 文件综合生成 |
| `Doc/SlimV2/**` | 工程代号、阶段/修复/relay 过程文档；不原样进入公开分支 |
| `README.md`、`AGENTS.md` | 旧入口/主线路由；目标同名文件综合生成 |
| `progress.md`、`feature_list.json`、`session-handoff.md` | 开发过程状态，不是公开事实 |
| `init.ps1`、`init.sh` | 旧全局/Full/LeanAudit launcher |
| `run_slim_v2.cmd` | 由 `run_experiments.cmd` 替代，目标不保留旧名 |
| audit/freeze 根目录其他 `run_*.cmd`, `run_*.ps1`, `run_*.sh`（明确排除第 3.3 节唯一 source） | 旧 launcher；目标唯一 launcher 是 `run_experiments.cmd` |
| `verification/lean-canary-tests.txt` | 旧 Lean canary profile |
| `verification/profiles/paper-l1-components.txt` | legacy paper profile |
| `verification/profiles/paper-l2-historical-real.txt` | legacy paper profile |
| `verification/profiles/paper-l3-new-real-smoke-audit.txt` | legacy network/paper profile |
| `verification/profiles/paper-l4-cell-lineage.txt` | legacy lineage profile |
| audit/freeze `verification/**` minus `verification/pytest_network_tripwire.py` 与 `verification/sitecustomize.py` | 旧 harness；目标脚本由第 5 节综合生成 |
| `local/**` | tracked pytest/Rxx/历史过程产物；不得进入目标 |
| `outputs/**` | tracked 历史输出；正式发布只取第 3.6 节 12 文件 |
| `TokenShareData/**` | ignored raw；不得跟踪或复制，12 个发布文件写入独立 target root |
| `.gitattributes` | 旧 paper/archive EOL 规则；目标重新综合 |

## 7. Authoritative corpus 映射合同

`lean_checker_preflight.v1.json`、`lean_environment_semantic_authority.v1.json` 和 `lean_lemma_graph_catalog.v1.jsonl` 的旧 path strings 是 digest identity 的一部分。Target 实现必须：

1. 读取 sidecar 原 bytes 并以旧 logical key 参与 digest/identity 检查；
2. 只在打开 bytes 前通过只读映射表换成 `benchmarks/experiments/...` physical path；
3. 禁止把 public path 写回 sidecar、重新序列化 sidecar或“更新” frozen digest；
4. `verification/verify_authoritative_corpus.py` 遍历 sidecar 中每一个 path-bearing entry，证明 key 属于第 10.4 节 exact allowlist、physical target 存在、bytes SHA 与记录一致；
5. 比较 500 Factorization records、30 Lean roots、600 preflight entries、165 lemma-graph entries与 13 fixture blobs 的完整数组/identity，不以首尾、计数或随机样本代替。

## 8. Official reducer 只读验证合同

`verification/verify_official_results.py` 必须在 raw root 上只读执行，且满足：

1. 先比较第 3.6 节 12 个发布文件的 SHA/bytes/rows；
2. monkeypatch `tokenshare.experiments.reducer._stage_payloads`，把 target path→serialized bytes 捕获到内存字典，不创建 stage/temp/metrics 文件；
3. monkeypatch `tokenshare.experiments.reducer._commit_staged_metrics` 为立即抛错 tripwire；任何调用都使验证失败；
4. 仅调用 `_reduce_run_staged(raw_root, {})`，捕获 5 CSV + 5 JSONL + `summary.json` 共 11 payload；逐 target relative path、逐 bytes 与 Git 发布 metrics 比较；
5. 禁止调用 `reduce_run()`；静态检查脚本 AST/源码也必须证明不存在该 call；
6. 运行前后复核 raw 非 metrics file/bytes/fingerprints/critical manifest；任一变化即失败；
7. raw 缺失时默认只检查 Git result manifest；显式 `--require-raw` 才要求 raw 存在。

## 9. Verification harness 保留与门

直接保留：

```text
verification/pytest_network_tripwire.py
verification/sitecustomize.py
```

综合生成：

```text
verification/run_verification.py
verification/fast-tests.txt
verification/verify_authoritative_corpus.py
verification/verify_official_results.py
verification/verify_extraction.py
```

`run_verification.py --focused system` 只能编排第 2.8、2.9、2.10 节保留 tests 中的无网络、非真实 Lean checker部分，并必须包含 Factorization multi-worker 纵向路径。`--focused experiments` 必须编排第 3.2 节 12 tests、2 fixtures、`test_authoritative_corpus.py`、fake Exp1–5 vertical、resume、reducer golden 与 network tripwire；Exp2–4 observed provider calls 必须为 0。两个 gate 都不得执行 provider、Representative/Full、LeanAudit、全量 Lean catalog、旧 paper/formal runner或跨 worktree import。

唯一真实 Lean checker 验证在两个 gate 之后单独执行第 11.4 节 exact pytest node；其 request 自带 `timeout_seconds=30`，外层进程再设 60 秒上限。不得扩张成 file/suite/catalog。

## 10. `historical_string_allowlist`

Allowlist 是“exact value + exact location set”，没有通配值。`verification/verify_extraction.py` 必须扫描 tracked text；出现含 `slim_v2`、`slim-v2`、`Slim V2`、`SLIM_V2` 或 `tokenshare_slim_v2` 的字符串时，只有下列逐值/位置组合可通过。Ignored raw 不属于 tracked 扫描范围。

### 10.1 正式 run identity

Exact value `slim-v2-full-flash-20260823-233000-b4c8e951` 只允许出现在以下 exact tracked paths：

```text
results/experiments/slim-v2-full-flash-20260823-233000-b4c8e951/run.json
results/experiments/slim-v2-full-flash-20260823-233000-b4c8e951/metrics/summary.json
results/experiments/slim-v2-full-flash-20260823-233000-b4c8e951/metrics/tables/exp1.csv
results/experiments/slim-v2-full-flash-20260823-233000-b4c8e951/metrics/tables/exp1.jsonl
results/experiments/slim-v2-full-flash-20260823-233000-b4c8e951/metrics/tables/exp2.csv
results/experiments/slim-v2-full-flash-20260823-233000-b4c8e951/metrics/tables/exp2.jsonl
results/experiments/slim-v2-full-flash-20260823-233000-b4c8e951/metrics/tables/exp3.csv
results/experiments/slim-v2-full-flash-20260823-233000-b4c8e951/metrics/tables/exp3.jsonl
results/experiments/slim-v2-full-flash-20260823-233000-b4c8e951/metrics/tables/exp4.csv
results/experiments/slim-v2-full-flash-20260823-233000-b4c8e951/metrics/tables/exp4.jsonl
results/experiments/slim-v2-full-flash-20260823-233000-b4c8e951/metrics/tables/exp5.csv
results/experiments/slim-v2-full-flash-20260823-233000-b4c8e951/metrics/tables/exp5.jsonl
results/experiments/slim-v2-full-flash-20260823-233000-b4c8e951/manifest.json
RESULTS.md
REPRODUCIBILITY.md
paper/paper.tex
Doc/Experiments/metrics.md
Doc/Experiments/corpus-manifest.md
```

### 10.2 冻结 schema / pricing / protocol exact values

以下每行都是唯一 exact value→exact tracked path set；未列路径不得出现该值：

- `tokenshare.slim_v2.schema.v1` → `src/tokenshare/experiments/__init__.py`。
- `tokenshare.slim_v2.run_config.v1` → `results/experiments/slim-v2-full-flash-20260823-233000-b4c8e951/run.json`；`src/tokenshare/experiments/cli.py`；`src/tokenshare/experiments/schema.py`；`tests/experiments/test_runtime_resume.py`。
- `tokenshare.slim_v2.root_inventory.v1` → `src/tokenshare/experiments/schema.py`。
- `tokenshare.slim_v2.unit_trace.v1` → `src/tokenshare/experiments/schema.py`。
- `tokenshare.slim_v2.root_result.v2` → `src/tokenshare/experiments/schema.py`；`tests/experiments/test_schema.py`。
- `tokenshare.slim_v2.premature_merge_observation.v2` → `src/tokenshare/experiments/schema.py`。
- `tokenshare.slim_v2.raw_model_output` → `src/tokenshare/experiments/execution.py`。
- `tokenshare.slim_v2.parse_failure` → `src/tokenshare/experiments/execution.py`。
- `tokenshare.slim_v2.unchecked_candidate.v1` → `src/tokenshare/experiments/execution.py`。
- `tokenshare.slim_v2.unchecked_candidate` → `src/tokenshare/experiments/execution.py`。
- `tokenshare.slim_v2.lean_environment_pass.v1` → `src/tokenshare/experiments/lean_environment.py`。
- `tokenshare.slim_v2.reducer_summary.v1` → `results/experiments/slim-v2-full-flash-20260823-233000-b4c8e951/metrics/summary.json`；`src/tokenshare/experiments/reducer.py`；`tests/experiments/test_cli_e2e.py`。
- `tokenshare.slim_v2.exp5_v4_reference_comparison.v1` → `src/tokenshare/experiments/reducer.py`。
- `slim_v2.protocol_material.v1` → `src/tokenshare/experiments/storage.py`。
- `slim_v2.provider_terminal.v1` → `src/tokenshare/experiments/provider.py`；`src/tokenshare/experiments/storage.py`。
- `slim_v2.exp3_perturbation.v1` → `src/tokenshare/experiments/scenarios.py`；`src/tokenshare/experiments/schema.py`；`tests/experiments/test_reducer_golden.py`；`tests/experiments/test_schema.py`。
- `slim_v2.pricing.2026-08-23` → `results/experiments/slim-v2-full-flash-20260823-233000-b4c8e951/run.json`；`src/tokenshare/experiments/schema.py`；`tests/experiments/test_cli_e2e.py`；`tests/experiments/test_runtime_resume.py`；`tests/experiments/test_schema.py`。
- `slim_v2.pricing.2026-08-20` → `results/experiments/slim-v2-full-flash-20260823-233000-b4c8e951/run.json`；`src/tokenshare/experiments/schema.py`；`tests/experiments/test_reducer_golden.py`；`tests/experiments/test_runtime_resume.py`；`tests/experiments/test_schema.py`。

这些值不得用于 current module、runtime output、cache、lock、CLI 或 UI 名称。

### 10.3 正式 `run.json` 旧路径 exact values

以下三个 exact values 都只允许出现在 exact tracked path `results/experiments/slim-v2-full-flash-20260823-233000-b4c8e951/run.json`：

- `benchmarks/paper/exp1_baseline_provider_config.v3.json`。
- `benchmarks/paper/exp5_siliconflow_provider_config.v3.json`。
- `local/ai_api_smoke.local.json`。

这些 path 不得成为当前 runtime default；新 defaults 是 `configs/experiments/...`。发布 `run.json` bytes 不得修改。

### 10.4 Sidecar frozen logical paths exact values

以下逐值位置均为 exact tracked paths。三个 catalog logical values 只允许在 exact byte-identical sidecar、mapping constant 与 mapping tests/verification 中：

- `benchmarks/paper/lean_catalog.v1.jsonl` → `benchmarks/experiments/lean_environment_semantic_authority.v1.json`；`src/tokenshare/plugins/lean_proof/semantic_authority.py`；`benchmarks/experiments/manifest.v1.json`；`tests/plugins/lean_proof/test_lean_fixture_project_manifest.py`；`tests/experiments/test_authoritative_corpus.py`；`verification/verify_authoritative_corpus.py`。
- `benchmarks/paper/lean_checker_preflight.v1.json` → `benchmarks/experiments/lean_environment_semantic_authority.v1.json`；`src/tokenshare/plugins/lean_proof/semantic_authority.py`；`benchmarks/experiments/manifest.v1.json`；`tests/plugins/lean_proof/test_lean_fixture_project_manifest.py`；`tests/experiments/test_authoritative_corpus.py`；`verification/verify_authoritative_corpus.py`。
- `benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl` → `benchmarks/experiments/lean_environment_semantic_authority.v1.json`；`src/tokenshare/plugins/lean_proof/semantic_authority.py`；`benchmarks/experiments/manifest.v1.json`；`tests/plugins/lean_proof/test_lean_fixture_project_manifest.py`；`tests/experiments/test_authoritative_corpus.py`；`verification/verify_authoritative_corpus.py`。

以下 fixture logical values 只允许在 exact byte-identical sidecar、mapping constant 与 mapping tests/verification 中：

- `fixtures/lean_proof_project/lakefile.lean` → `benchmarks/experiments/lean_environment_semantic_authority.v1.json`；`src/tokenshare/plugins/lean_proof/semantic_authority.py`；`benchmarks/experiments/manifest.v1.json`；`tests/plugins/lean_proof/test_lean_fixture_project_manifest.py`；`tests/experiments/test_authoritative_corpus.py`；`verification/verify_authoritative_corpus.py`。
- `fixtures/lean_proof_project/lean-toolchain` → `benchmarks/experiments/lean_environment_semantic_authority.v1.json`；`src/tokenshare/plugins/lean_proof/semantic_authority.py`；`benchmarks/experiments/manifest.v1.json`；`tests/plugins/lean_proof/test_lean_fixture_project_manifest.py`；`tests/experiments/test_authoritative_corpus.py`；`verification/verify_authoritative_corpus.py`。
- `fixtures/lean_proof_project/TokenShare.lean` → `benchmarks/experiments/lean_environment_semantic_authority.v1.json`；`src/tokenshare/plugins/lean_proof/semantic_authority.py`；`benchmarks/experiments/manifest.v1.json`；`tests/plugins/lean_proof/test_lean_fixture_project_manifest.py`；`tests/experiments/test_authoritative_corpus.py`；`verification/verify_authoritative_corpus.py`。
- `fixtures/lean_proof_project/TokenShare/Fixtures/Decomposition.lean` → `benchmarks/experiments/lean_environment_semantic_authority.v1.json`；`src/tokenshare/plugins/lean_proof/semantic_authority.py`；`benchmarks/experiments/manifest.v1.json`；`tests/plugins/lean_proof/test_lean_fixture_project_manifest.py`；`tests/experiments/test_authoritative_corpus.py`；`verification/verify_authoritative_corpus.py`。
- `fixtures/lean_proof_project/TokenShare/Fixtures/Direct.lean` → `benchmarks/experiments/lean_environment_semantic_authority.v1.json`；`src/tokenshare/plugins/lean_proof/semantic_authority.py`；`benchmarks/experiments/manifest.v1.json`；`tests/plugins/lean_proof/test_lean_fixture_project_manifest.py`；`tests/experiments/test_authoritative_corpus.py`；`verification/verify_authoritative_corpus.py`。
- `fixtures/lean_proof_project/TokenShare/Fixtures/Invalid.lean` → `benchmarks/experiments/lean_environment_semantic_authority.v1.json`；`src/tokenshare/plugins/lean_proof/semantic_authority.py`；`benchmarks/experiments/manifest.v1.json`；`tests/plugins/lean_proof/test_lean_fixture_project_manifest.py`；`tests/experiments/test_authoritative_corpus.py`；`verification/verify_authoritative_corpus.py`。
- `fixtures/lean_proof_project/TokenShare/Fixtures/Unsupported.lean` → `benchmarks/experiments/lean_environment_semantic_authority.v1.json`；`src/tokenshare/plugins/lean_proof/semantic_authority.py`；`benchmarks/experiments/manifest.v1.json`；`tests/plugins/lean_proof/test_lean_fixture_project_manifest.py`；`tests/experiments/test_authoritative_corpus.py`；`verification/verify_authoritative_corpus.py`。
- `fixtures/lean_proof_project/TokenShare/Helper.lean` → `benchmarks/experiments/lean_environment_semantic_authority.v1.json`；`src/tokenshare/plugins/lean_proof/semantic_authority.py`；`benchmarks/experiments/manifest.v1.json`；`tests/plugins/lean_proof/test_lean_fixture_project_manifest.py`；`tests/experiments/test_authoritative_corpus.py`；`verification/verify_authoritative_corpus.py`。
- `fixtures/lean_proof_project/TokenShare/LemmaGraphCases.lean` → `benchmarks/experiments/lean_environment_semantic_authority.v1.json`；`src/tokenshare/plugins/lean_proof/semantic_authority.py`；`benchmarks/experiments/manifest.v1.json`；`tests/plugins/lean_proof/test_lean_fixture_project_manifest.py`；`tests/experiments/test_authoritative_corpus.py`；`verification/verify_authoritative_corpus.py`。
- `fixtures/lean_proof_project/TokenShare/LemmaGraphOracle.lean` → `benchmarks/experiments/lean_environment_semantic_authority.v1.json`；`benchmarks/experiments/lean_lemma_graph_catalog.v1.jsonl`；`src/tokenshare/plugins/lean_proof/semantic_authority.py`；`benchmarks/experiments/manifest.v1.json`；`tests/plugins/lean_proof/test_lean_fixture_project_manifest.py`；`tests/experiments/test_authoritative_corpus.py`；`verification/verify_authoritative_corpus.py`。
- `fixtures/lean_proof_project/TokenShare/Merge.lean` → `benchmarks/experiments/lean_environment_semantic_authority.v1.json`；`src/tokenshare/plugins/lean_proof/semantic_authority.py`；`benchmarks/experiments/manifest.v1.json`；`tests/plugins/lean_proof/test_lean_fixture_project_manifest.py`；`tests/experiments/test_authoritative_corpus.py`；`verification/verify_authoritative_corpus.py`。
- `fixtures/lean_proof_project/TokenShare/SplitRules.lean` → `benchmarks/experiments/lean_environment_semantic_authority.v1.json`；`src/tokenshare/plugins/lean_proof/semantic_authority.py`；`benchmarks/experiments/manifest.v1.json`；`tests/plugins/lean_proof/test_lean_fixture_project_manifest.py`；`tests/experiments/test_authoritative_corpus.py`；`verification/verify_authoritative_corpus.py`。

`lake-manifest.json` 是实际 13-file physical corpus 的一员，但不在 sidecar frozen logical path 集；它按第 3.4 节 public physical path 直接验证。

以下当前名称无 allowlist，target 运行时出现即失败：`tokenshare.experiments.slim_v2`、`TokenShareData/outputs/slim_v2`、`local/cache/slim_v2`、`.slim-v2.lock`、`tokenshare-slim-v2`、`TokenShare Slim V2`、`TOKENSHARE_SLIM_V2_REPO`、`TOKENSHARE_SLIM_V2_EXIT`、`SLIM_V2_JOURNAL_RESUME_NOT_USED`、`SLIM_V2_RESUME_NOT_USED`、`tokenshare_slim_v2_lean_environment_`。动态 runtime output/artifact/request/source-kind 名称也不得以 `slim_` 或 `slim_v2_` 新建。

## 11. `verification_commands`

以下命令在 target/review worktree 执行；Task A 只冻结命令，不运行它们。

### 11.1 Corpus gate

```powershell
conda run -n tokenshare python verification/verify_authoritative_corpus.py
conda run -n tokenshare python -m pytest tests/experiments/test_authoritative_corpus.py -q
```

期望：第 3.4、4、7 节完整 arrays/identity/file SHA/sidecar mapping 全部对等；不是抽样。

### 11.2 System gate

```powershell
conda run -n tokenshare python verification/run_verification.py --focused system
conda run -n tokenshare python -m compileall -q src/tokenshare tests/core tests/storage tests/local_runtime tests/plugins tests/executors
```

期望：core/storage/local runtime/Factorization、Lean 纯合同、executor transport/descriptor/deterministic/mock 通过；Factorization k>1 纵向路径通过；network=0；不执行真实 Lean checker。

### 11.3 Experiments gate

```powershell
conda run -n tokenshare python verification/run_verification.py --focused experiments
conda run -n tokenshare python -m compileall -q src/tokenshare/experiments tests/experiments verification
conda run -n tokenshare python -m tokenshare.experiments.cli plan --profile full --format json
```

期望：12 moved tests + corpus test、fake Exp1–5、resume、reducer golden、GUI/CLI/import 通过；plan 完整数组与第 4 节相等；Exp2–4 provider calls=0；network=0。

### 11.4 单个有界 Lean checker smoke

```powershell
$leanNode = 'tests/plugins/lean_proof/test_lean_checker_direct.py::test_lean_checker_accepts_valid_direct_proof_with_real_environment'
$proc = Start-Process -FilePath 'conda' -ArgumentList @('run', '-n', 'tokenshare', 'python', '-m', 'pytest', $leanNode, '-q') -WindowStyle Hidden -PassThru
if (-not $proc.WaitForExit(60000)) {
    Stop-Process -Id $proc.Id -Force
    throw 'Lean checker smoke exceeded 60000 ms'
}
if ($proc.ExitCode -ne 0) { throw "Lean checker smoke failed with exit code $($proc.ExitCode)" }
```

该 exact node 是唯一真实本地 Lean checker smoke，request timeout=30 秒；supervisor 外层终止上限=60 秒。不得把 selector 扩成文件、目录、catalog、LeanAudit、`lake` 或 `lean` 回归。

### 11.5 Official results 与 extraction gate

```powershell
conda run -n tokenshare python verification/verify_official_results.py --raw-root 'E:\TokenEcnomic\TokenShareWorktrees\slim-v2-baseline\TokenShareData\outputs\slim_v2\slim-v2-full-flash-20260823-233000-b4c8e951' --require-raw
conda run -n tokenshare python verification/verify_extraction.py --frozen-sha $frozenSha
git ls-files -- 'paper/out/**' '*.synctex.gz' '*.aux' '*.log' '*.fls' '*.fdb_latexmk' '*.toc' '*.blg'
```

期望：12 result hashes、11 in-memory reducer payloads、raw invariants、ancestry、default-deny、旧设施 absence 与历史 allowlist 通过；最后命令无输出。`verify_official_results.py` 不调用 `reduce_run()` 且 `_commit_staged_metrics` tripwire 未触发。

### 11.6 Manifest 本身自检

```powershell
git diff --check -- 'Doc/SlimV2/2026-08-27-paper-branch-extraction-manifest.md'
$manifestPath = 'Doc/SlimV2/2026-08-27-paper-branch-extraction-manifest.md'
$anglePattern = ([char]60) + '[^' + ([char]62) + ']+' + ([char]62)
$wordPattern = @(('TO' + 'DO'), ('T' + 'BD'), ('FIX' + 'ME'), (([char]31867) + ([char]20284) + ([char]25991) + ([char]20214)), (([char]30456) + ([char]20851) + ([char]27979) + ([char]35797))) -join '|'
$shortHashPattern = '[0-9a-f]{8}' + ([char]8230)
$hits = Select-String -LiteralPath $manifestPath -Encoding UTF8 -Pattern $anglePattern,$wordPattern,$shortHashPattern
if ($hits) { $hits; throw 'manifest contains a forbidden placeholder or abbreviated hash' }
$manifestLines = Get-Content -LiteralPath $manifestPath -Encoding UTF8
$allowStart = ($manifestLines | Select-String '^## 10\. ').LineNumber
$allowEnd = ($manifestLines | Select-String '^## 11\. ').LineNumber
$allowlist = $manifestLines[($allowStart - 1)..($allowEnd - 2)] -join "`n"
if ($allowlist -match '`[^`]*[?*][^`]*`') { throw 'historical allowlist contains a wildcard path/value' }
```

期望：`git diff --check` 退出码 0；角括号占位、禁用词、allowlist wildcard 与 abbreviated SHA 扫描均无输出。

## 12. `minimum_dependency_request`

清单外依赖只能由 supervisor 批准以下完整记录；缺任一字段即拒绝。Request 必须保存到阶段审查记录，并以 exact path/symbol 扩展本 manifest 后才允许 writer 动手：

| Required field | Type 与内容合同 |
|---|---|
| `failure_command` | nonempty string；完整、可复跑、无 provider/network 的 exact command |
| `failure_exit_code` | integer；实际退出码 |
| `failure_error` | nonempty string；完整错误摘要与首个责任 stack frame |
| `responsibility` | nonempty string；缺失行为服务的唯一系统或实验职责 |
| `minimum_symbol_or_path` | nonempty array of strings；每项是一个 exact symbol 或 repository-relative tracked path |
| `current_interface_insufficient_because` | nonempty string；现有 retained public interface 不能完成职责的具体原因 |
| `forbidden_burden_check` | object；`old_runner`、`receipt`、`budget_authority`、`response_bank`、`publication_gate_or_eligibility`、`lineage_or_evidence_closure`、`replay_or_request_identity` 七个 exact keys 的值都必须是 string `absent` |
| `proposed_extraction` | nonempty string；仅描述从 freeze tag peeled SHA 定点抽取或重写的最小行为 |
| `focused_verification` | nonempty array of exact command strings；至少一项证明该职责，并包含 `conda run -n tokenshare python verification/verify_extraction.py --frozen-sha $frozenSha` |

批准前必须重新执行：

```powershell
$frozenSha = git rev-parse 'experiments-pre-cleanup-20260827^{}'
if ((git cat-file -t 'experiments-pre-cleanup-20260827') -ne 'tag') { throw 'not annotated' }
$request = Get-Content -LiteralPath 'Doc/Experiments/dependency-requests/current.json' -Encoding UTF8 | ConvertFrom-Json
foreach ($approvedExactPath in @($request.minimum_symbol_or_path)) {
    git show "$frozenSha`:$approvedExactPath"
    if ($LASTEXITCODE -ne 0) { throw "approved dependency path is absent from peeled SHA: $approvedExactPath" }
}
```

只准从该 peeled SHA 抽取。不得从 live source、后来 commit、其他 branch/worktree、未跟踪 path 或 archive tag 取材；不得因一个缺失 symbol 恢复整个目录。补取后必须运行 request 中 focused command、受影响的 system/experiments gate、`verify_extraction.py` 与禁止负担扫描。

## 13. 冻结结论

本 manifest 的分类是关闭集合：目标 writer 按 `keep_exact`、`move_exact`、`synthesize` 构造；其余由 default-deny 和 `exclude_exact` 排除。任何扩展只能走第 12 节，且不能改变正式 corpus、selection、实验参数、12 个结果文件、历史字符串 allowlist 或 raw `pending_advisor_archive_decision` 状态。
