# TokenShare Agent 导航

本文件只做路由，不复制设计正文。项目文档分层、大小预算和归档规则见 `Doc/repository-governance.md`。

## 默认最小启动集合（Tier 1）

按顺序读取：

1. `AGENTS.md`：项目边界、工作规则和验证命令。
2. `Doc/SlimV2/README.md`：默认实验设施主线和范围边界。
3. `Doc/SlimV2/slim_v2_experiment_metrics_authority.md`：Experiment 1–5 与必须指标。
4. `Doc/SlimV2/slim_v2_system_integration_contract.md`：共享系统接线和接口缺口。
5. 若当前 focus 已进入获批设计/实现阶段，完整阅读 `Doc/SlimV2/slim_v2_reuse_inventory.md`，再按其中 allowlist 定点打开源码；其他任务跳过本项。
6. `progress.md` 顶部 Slim V2 段落：当前阶段和下一步。

不要在启动时递归读取 `Doc/archive/`、`outputs/`、`reference_repos/`、pytest 临时目录或仓库外 `TokenShareData`。

### Slim V2 默认分流

除非用户在当前任务中明确指定其他维护范围，所有 Agent 都默认进入 Slim V2，不按旧 paper/formal 路线展开 Tier 2。三份 Slim V2 前置文档均为 `user_approved`，是该范围内的当前权威；下一阶段是编写和审批设计规格，设计规格与实施计划完成前不能写代码或启动实验。

Slim V2 Agent 不广泛读取 archive、旧 paper/formal pipeline、历史 Rxx 输出或历史实验数据来寻找“最新版本”，也不尝试修复旧设施。唯一 legacy 只读例外由 `slim_v2_reuse_inventory.md` 固定：只允许对完整 archive SHA、allowlist 路径和点名符号使用 `git show`；不得 checkout 或建立运行时依赖。需要共享系统接口时只读 Slim V2 接线合同点名的 core/local_runtime/plugin/executor/storage 公共区域；发现合同未闭合时向用户报告，不自行扩大范围。

`feature_list.json`、完整 `progress.md`、`session-handoff.md` 和旧实验权威只在用户明确指定 legacy/非 Slim 维护任务时读取；其中记录的 active feature 或 Rxx 下一步不能覆盖 Slim V2 默认主线。

## 权威文档（Tier 2）

| 任务 | 必读文件 |
|---|---|
| Slim V2 指标或范围讨论 | `Doc/SlimV2/README.md` → `Doc/SlimV2/slim_v2_experiment_metrics_authority.md` → `Doc/SlimV2/slim_v2_system_integration_contract.md`（已获用户批准并冻结） |
| Slim V2 获批设计或实现 | 上述三份权威 → `Doc/SlimV2/slim_v2_reuse_inventory.md` → 清单允许的当前公共源码或固定 SHA `git show` 位置 |
| Slim V2 官方价格来源复核（仅价格维护任务，不是普通启动必读） | `Doc/SlimV2/slim_v2_official_pricing_sources_20260820.md`；实际计算口径仍以指标权威第 1.4 节为准 |
| Slim V2 定位可复用代码（支持材料，不是权威） | `Doc/SlimV2/slim_v2_reuse_inventory.md`；必须先读完三份权威文档；legacy 仅允许固定 SHA + allowlist + `git show` 定点只读，禁止 runtime import |
| Slim V2 串行新任务接力（仅在用户显式启动时） | `Doc/SlimV2/slim_v2_stage_relay_protocol.md`；只定义阶段 owner、子 Agent、checkpoint、委托审批和新任务接力，不覆盖实验/指标/接线权威，也不授权修改 shared code |
| 仓库治理、上下文或数据位置 | `Doc/repository-governance.md` |
| 协议对象、schema、状态机、replay 边界 | `Doc/TechnicalDocument/tokenshare_v1_complete_spec.md` |
| 代码归属和模块路由 | `Doc/TechnicalDocument/tokenshare_v1_code_map.md` |
| legacy 实验设计、runner、论文表格、failure/ablation/generalization（仅用户明确指定时） | `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md` |
| 实验参数为何改变 | `Doc/TechnicalDocument/tokenshare_experiment_parameter_decision_log.md` |
| Lean catalog/checker/toolchain/helper 验证 | `Doc/TechnicalDocument/2026-07-22-lean-checker-verification-profiles-design.md` |
| 论文源码与论证映射 | `Doc/TechnicalDocument/2026-06-04-tokenshare-paper-module-map.md` |
| Exp5 v3 冻结 design identity | `Doc/TechnicalDocument/2026-07-29-feat-011-exp5-siliconflow-four-model-v3-design.md` |

`2026-07-29-feat-011-exp5-siliconflow-four-model-v3-design.md` 必须保留原路径，因为 tracked cohort snapshot 冻结了其路径和 digest。它不取代 Slim V2 权威；其中 14.1 等“当前差距”段落是实施前冻结快照，不描述当前状态。Slim V2 冲突以 `Doc/SlimV2/` 三份已批准文档为准；legacy 内部冲突才以 `tokenshare_latest_real_plugin_experiment_design.md` 为准。

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
| Slim V2 runner/hooks/projector/reducer（用户批准后） | `src/tokenshare/experiments/slim_v2/` |
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
| Slim V2 focused tests（用户批准后） | `tests/experiments/slim_v2/` |
| harness/context | `tests/test_init_verification_profiles.py` |
| runtime data paths | `tests/test_runtime_paths.py`、`tests/experiments/test_runtime_output_defaults.py` |

上述默认/Full/LeanAudit 命令只适用于用户明确指定的 legacy/非 Slim 工作。Slim V2 只运行获批设计/计划点名的 focused verification，默认排除 Lean 专项 suite、LeanAudit、全量 catalog 和 `lake`/`lean` 回归；权威实验要求的 Lean roots 仍作为实验样本运行。具体例外边界以 `Doc/SlimV2/README.md` 和串行接力协议为准。

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

- Slim V2 推荐输出：`TokenShareData/outputs/slim_v2/<run_id>/`；reducer 只枚举该普通文件夹，不读取历史 paper/formal 输出补数。
- 新实验默认：仓库同级 `TokenShareData/outputs/experiments/`。
- diagnostics：`TokenShareData/outputs/diagnostics/`。
- launcher supervision：`TokenShareData/local/supervision/`。
- 可用仓库外绝对路径环境变量 `TOKENSHARE_DATA_ROOT` 覆盖默认根。
- 普通实验 CLI 的显式 `--output-root` 优先；paper runner 必须显式指定全新 root。历史输出移动后只通过只读路径解析访问，不改历史 evidence/digest，不 resume。

仓库内不再保存运行生成的 `outputs/`。不要创建 junction/symlink 把外部数据伪装回仓库。
