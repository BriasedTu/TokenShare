# TokenShare Agent 导航

本文件只做路由，不复制设计正文。项目文档分层、大小预算和归档规则见 `Doc/repository-governance.md`。

## 最小启动集合（Tier 1）

按顺序读取：

1. `AGENTS.md`：项目边界、工作规则和验证命令。
2. `feature_list.json`：唯一 active feature 与当前维护焦点。
3. `progress.md`：当前实现事实和最近验证。
4. `session-handoff.md`：阻塞、风险和下一步。
5. 本文件：按任务进入 Tier 2 权威文档。

不要在启动时递归读取 `Doc/archive/`、`outputs/`、`reference_repos/`、pytest 临时目录或仓库外 `TokenShareData`。

## 权威文档（Tier 2）

| 任务 | 必读文件 |
|---|---|
| 仓库治理、上下文或数据位置 | `Doc/repository-governance.md` |
| 协议对象、schema、状态机、replay 边界 | `Doc/TechnicalDocument/tokenshare_v1_complete_spec.md` |
| 代码归属和模块路由 | `Doc/TechnicalDocument/tokenshare_v1_code_map.md` |
| 实验设计、runner、论文表格、failure/ablation/generalization | `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md` |
| 实验参数为何改变 | `Doc/TechnicalDocument/tokenshare_experiment_parameter_decision_log.md` |
| Lean catalog/checker/toolchain/helper 验证 | `Doc/TechnicalDocument/2026-07-22-lean-checker-verification-profiles-design.md` |
| 论文源码与论证映射 | `Doc/TechnicalDocument/2026-06-04-tokenshare-paper-module-map.md` |
| Exp5 v3 冻结 design identity | `Doc/TechnicalDocument/2026-07-29-feat-011-exp5-siliconflow-four-model-v3-design.md` |

`2026-07-29-feat-011-exp5-siliconflow-four-model-v3-design.md` 必须保留原路径，因为 tracked cohort snapshot 冻结了其路径和 digest。它不取代总实验权威；其中 14.1 等“当前差距”段落是实施前冻结快照，不描述 2026-07-30 现状。冲突时以 `tokenshare_latest_real_plugin_experiment_design.md` 为准，参数决定台账只提供 provenance。

## 代码路由

| 修改内容 | 首选目录/文件 |
|---|---|
| 协议纯规则与对象 | `src/tokenshare/core/` |
| ledger、artifact、SQLite | `src/tokenshare/storage/` |
| 协议生命周期协调与 worker | `src/tokenshare/local_runtime/` |
| Factorization 领域规则 | `src/tokenshare/plugins/factorization/` |
| Lean 固定计划、checker、merge | `src/tokenshare/plugins/lean_proof/` |
| executor/config/transport/replay | `src/tokenshare/executors/` |
| 论文实验、projection、metrics/report/CLI | `src/tokenshare/experiments/` |
| 仓库外数据根和历史读取映射 | `src/tokenshare/runtime_paths.py` |
| 启动验证 | `verification/run_verification.py`、`verification/*.txt` |
| 当前总体 code map | `Doc/TechnicalDocument/tokenshare_v1_code_map.md` |

关键边界：

- `core` 不得硬编码 Factorization、Lean、provider、fault 或论文实验策略。
- 插件决定领域 split/parser/verifier/merge；AI 不决定协议级拆分。
- executor 负责请求与持久化，不决定 cohort、fault、worker 或 paper eligibility。
- paper runner 从 events/artifacts 投影指标，不得回写协议事实或伪造成功。
- 非确定性 provider 输出必须持久化；replay 不得重新调用 provider。

## 测试路由

| 范围 | 位置 |
|---|---|
| core/storage | `tests/core/`、`tests/storage/` |
| executors | `tests/executors/` |
| plugins | `tests/plugins/` |
| local runtime/system integration | `tests/local_runtime/`、`tests/integration/`（如存在） |
| experiments | `tests/experiments/` |
| harness/context | `tests/test_init_verification_profiles.py` |
| runtime data paths | `tests/test_runtime_paths.py`、`tests/experiments/test_runtime_output_defaults.py` |

默认运行 `.\init.ps1`；feature 完成、提交/合并或发布实验结果前运行 `.\init.ps1 -Full`。Lean 共享输入变化或正式发布时按 `AGENTS.md` 加 `-LeanAudit`，不要用普通 catalog load 代替 checker evidence。

## 外部资料落库规则

只有确实需要联网时才搜索。凡用于修改设计、代码、测试或论文的外部材料，必须同时保留本地可复查版本：

- 论文/报告：保存或转写到 `Doc/TechnicalDocument/tokenshare-paper-tex/`，并更新论文映射。
- 开源项目：浅克隆或 sparse checkout 到 `reference_repos/`，并更新 `reference_repos/README.md`。
- 普通在线文档：在对应权威文档或本地摘要中记录来源、访问日期、关键结论和影响范围。

只有在线链接、没有本地材料和索引的资料不能作为实现依据。本轮若未联网，应在 `progress.md` 明确记录。

## 历史资料（Tier 3）

- 旧设计与实施计划：`Doc/archive/design-history/`
- 旧 code map：`Doc/archive/code-maps/`
- 旧 agent 执行计划：`Doc/archive/agent-plans/`
- Phase 1–6 provenance：`Doc/TechnicalDocument/phase-1-6-archive/`
- 论文源码与已冻结研究材料：`Doc/TechnicalDocument/tokenshare-paper-tex/`

Tier 3 只在追查 provenance、旧 digest、历史回归语义或 `git blame` 不足时读取。归档文件中出现“最新”“当前”“唯一权威”均按其历史日期解释，不得覆盖 Tier 1/2。

## 数据位置

- 新实验默认：仓库同级 `TokenShareData/outputs/experiments/`。
- diagnostics：`TokenShareData/outputs/diagnostics/`。
- launcher supervision：`TokenShareData/local/supervision/`。
- 可用仓库外绝对路径环境变量 `TOKENSHARE_DATA_ROOT` 覆盖默认根。
- 普通实验 CLI 的显式 `--output-root` 优先；paper runner 必须显式指定全新 root。历史输出移动后只通过只读路径解析访问，不改历史 evidence/digest，不 resume。

仓库内不再保存运行生成的 `outputs/`。不要创建 junction/symlink 把外部数据伪装回仓库。
