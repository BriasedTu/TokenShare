# TokenShare Agent 导航

日期：2026-07-13

状态：工作流导航文档。本文只回答“新 AI 遇到问题应先看哪里、代码应放到哪个模块、外部参考资料应如何落库和使用”。本文不是协议设计规格，不替代 `AGENTS.md`、`feature_list.json`、`progress.md` 或 `session-handoff.md`。

## 1. 使用时机

新的 AI 在完成 `AGENTS.md` 的启动要求后，如果需要判断事实来源、模块归属、参考资料边界、联网资料落库方式或下一步阅读顺序，应阅读本文。

本文的定位：

- 不替代 `AGENTS.md` 的最高优先级工作规则。
- 不替代 `feature_list.json` 的 active feature 状态。
- 不替代 `progress.md` 和 `session-handoff.md` 的当前进展。
- 不把 `reference_repos/` 变成 TokenShare runtime 依赖。
- 不允许把已经影响设计或代码的联网资料只停留在浏览器结果或聊天回答里。

## 2. 事实源优先级

后续 AI 应按以下顺序判断当前项目事实：

1. `AGENTS.md`：最高优先级工作规则、范围边界、验证和完成标准。
2. `feature_list.json`：当前 active track / active feature 和 feature 状态。当前 active feature 是 `feat-011` Paper Real AI Experiments；`feat-010` Phase 9 replay / audit 已延后。
3. `progress.md`：最近进展、验证证据、风险和下一步。
4. `session-handoff.md`：跨会话恢复摘要，尤其是上一轮未解决问题。
5. `Doc/TechnicalDocument/tokenshare_v1_complete_spec.md`：Phase 1-6 收敛后的完整说明，接管旧 Phase 1-6 默认说明入口。
6. `Doc/TechnicalDocument/tokenshare_v1_code_map.md`：Phase 1-6 收敛后的代码映射，以当前 `src/` 和 `tests/` 为准。
7. `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`：唯一权威实验设计；用于全部新论文实验、真实 AI API 门槛、Experiment 1-5、输入 catalog、runner 改造、failure/ablation、metrics/report、预算和论文结果口径。
8. `Doc/TechnicalDocument/2026-07-13-feat-011-paper-real-ai-experiments-implementation-plan.md`：feat-011 实施顺序与边界清单；只说明落地顺序，不替代唯一权威实验设计。
9. `Doc/TechnicalDocument/2026-07-15-feat-011-lean-3x3-topic-template-design.md`：Lean 3×3 topic-family / paper-difficulty theorem template 设计记录；用于 case selection、oracle package 和能力缺口判断，不表示 runner 已支持完整 3×3 执行矩阵。
10. `Doc/TechnicalDocument/2026-06-28-phase-7-ai-api-executor-field-spec.md`、`Doc/TechnicalDocument/2026-06-28-phase-7-ai-api-executor-tdd-plan.md`、`Doc/TechnicalDocument/2026-06-28-phase-7-ai-api-executor-code-map.md`：Phase 7 实验级 AI API executor 已实现边界。
11. `Doc/TechnicalDocument/2026-06-29-phase-8-experiment-infrastructure-code-map.md`：Phase 8 第一版 regression infrastructure 的 source/tests/边界和验证证据映射；不是新论文实验设计权威。
12. `README.md`：人类入口、运行命令和仓库地图。
13. `Doc/TechnicalDocument/2026-06-04-tokenshare-paper-module-map.md`：论文、技术报告和本地 TeX/OCR 映射；用于追踪研究依据。
14. `Doc/TechnicalDocument/2026-06-22-p01-p12-tokenshare-candidate-mechanism-spec.md`：P01-P22 机制整合记录；只用于追溯取舍理由，不覆盖当前实现规格。
15. `Doc/TechnicalDocument/2026-06-02-tokenshare-protocol-kernel-revised-draft.md`：历史讨论稿；只用于理解早期设计原因。
16. `reference_repos/`：外部参考源码；只能用于借鉴模式，不属于 TokenShare runtime。

旧 Phase 1-6 文档已移动到 `Doc/TechnicalDocument/phase-1-6-archive/`。该目录只作普通历史归档，不写单独索引，也不作为默认阅读入口。如果新文档和旧归档冲突，应相信新文档和当前代码；必要时把冲突记录到 `progress.md` 或 `session-handoff.md`。

## 3. 遇到问题时看哪里

| 问题 | 首先阅读 | 辅助阅读 | 注意事项 |
|---|---|---|---|
| 当前要做哪个 feature / track | `feature_list.json` | `progress.md`、`session-handoff.md` | 当前 active feature 是 `feat-011` Paper Real AI Experiments。`feat-007` 真实 Lean proof plugin、`feat-008` Phase 7 AI API executor、`feat-009` Phase 8 已完成；`feat-010` replay/audit 已延后。 |
| 启动和验证怎么跑 | `AGENTS.md` | `init.ps1`、`init.sh`、`README.md` | 当前基线会运行 Python JSON/SQLite、`compileall` 和 `pytest tests`。 |
| Phase 1-6 V1 协议在做什么 | `Doc/TechnicalDocument/tokenshare_v1_complete_spec.md` | `README.md` | 只覆盖 Phase 1-6 协议内核、存储、插件和执行器契约。 |
| Phase 1-6 代码在哪、测试在哪 | `Doc/TechnicalDocument/tokenshare_v1_code_map.md` | `src/tokenshare/`、`tests/` | code map 必须以当前实现为准；不要从旧归档文档倒推。 |
| 最新真实 AI 论文实验 | `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md` | `Doc/TechnicalDocument/2026-07-13-feat-011-paper-real-ai-experiments-implementation-plan.md`、`Doc/TechnicalDocument/2026-07-15-feat-011-lean-3x3-topic-template-design.md`、`src/tokenshare/experiments/` | 所有可写入论文的新实验必须真实调用 AI API。旧 deterministic/scripted suite、direct 500 和 Lean 50 只能作为 regression/calibration；Lean 3×3 模板设计不等于完整 runner 已可执行。 |
| Phase 7 AI API executor | Phase 7 field spec / TDD plan / code map | `src/tokenshare/executors/ai_api*.py`、`tests/executors/test_ai_api_*.py` | 保持 artifact/provenance/secret/replay 边界；标准 config 只保存 `api_key_env`。 |
| Phase 8 regression infrastructure | `Doc/TechnicalDocument/2026-06-29-phase-8-experiment-infrastructure-code-map.md` | `src/tokenshare/experiments/`、`tests/experiments/` | Phase 8 code map 不是最新论文实验设计。 |
| V1 做什么、不做什么 | `AGENTS.md` | `tokenshare_v1_complete_spec.md`、`README.md` | 不做真实区块链、真实分布式 network、生产级 AI 平台或生产级 theorem-proving 平台。 |
| 具体代码应该放哪个模块 | 本文第 5 节 | `tokenshare_v1_code_map.md` | 守住协议框架、任务插件、执行器三层边界。 |
| 需要联网查找资料 | 本文第 6 节 | paper module map、`reference_repos/README.md` | 被用于项目决策的外部资料必须本地落库并同步索引。 |
| 需要更新状态 | `progress.md` | `feature_list.json`、`session-handoff.md` | 没有验证证据，不要标记完成。 |

## 4. 工具与编码规则

本仓库在 Windows / PowerShell 环境中维护，中文文档很多。为了避免编码乱码、重复误判和无效上下文消耗，后续 agent 必须遵守以下工具约定：

- 读取 Markdown、JSON、脚本和代码时显式使用 UTF-8：`Get-Content -Encoding UTF8 <path>`。
- 搜索文本时使用 PowerShell：`Select-String -Path <files> -Pattern "<pattern>"`；多文件递归先用 `Get-ChildItem -Recurse` 选定范围，再传给 `Select-String`。
- 枚举文件时使用 PowerShell：`Get-ChildItem`，需要递归时加 `-Recurse`，需要隐藏文件时加 `-Force`。
- 常规仓库检索不要使用 `rg`。除非用户明确要求或 PowerShell 命令无法完成，否则不要把 `rg` 作为默认搜索工具。
- 写入 JSON 后必须验证 JSON 可解析；例如用 `conda run -n tokenshare python -c "import json; ..."` 检查。
- Bash / WSL 仅用于运行 `./init.sh` 或用户明确要求的跨 shell 验证；不要用它替代 PowerShell 做常规仓库阅读和搜索。

推荐命令模板：

```powershell
Get-Content -Encoding UTF8 AGENTS.md
Get-ChildItem -Recurse -File Doc | Select-String -Pattern "外部参考资料"
conda run -n tokenshare python -c "import json; from pathlib import Path; json.loads(Path('feature_list.json').read_text(encoding='utf-8')); print('feature-list-json-ok')"
```

## 5. 模块路由

当前 package layout 已确定为 `src/` layout：

```text
src/
  tokenshare/
    core/
    storage/
    plugins/
      factorization/
      lean_proof/
      lean_stub/
    executors/
    replay/
    experiments/

tests/
  core/
  storage/
  plugins/
    factorization/
    lean_proof/
    lean_stub/
  executors/
  replay/
  experiments/
```

模块职责：

| 目录 | 放什么 | 不放什么 |
|---|---|---|
| `tokenshare.core` | 协议对象、状态枚举、任务图、状态机、调度/租约纯规则、verification/expansion/merge/contribution 纯对象和不变量。 | factorization/Lean 领域规则、文件系统细节、executor provider 调用。 |
| `tokenshare.storage` | `ArtifactStore`、JSONL `EventLedger`、SQLite materialized index、本地路径和内容哈希实现。 | 协议决策逻辑、插件验证规则、AI provider 调用。 |
| `tokenshare.plugins` | 插件契约、descriptor、factorization PoC、真实 Lean proof 插件；`lean_stub` 只作历史 compatibility。 | 协议状态机推进、租约调度、正式输出绑定、结算。 |
| `tokenshare.executors` | 执行器契约、mock executor、确定性程序执行器、Phase 7 实验级 AI API executor。 | 任务图修改、验证结论绑定、结算逻辑、生产级 AI 平台或 secret 持久化。 |
| `tokenshare.protocol_engine` | storage-writing application flow：调度、request/submission、verification、canonical binding、split/expand、merge、completion、settlement、pruning。 | 领域算法、provider transport、真实网络 runtime。 |
| `tokenshare.replay` | 后续 replay / audit hardening 的 package 边界。 | 重新调用 AI 或 executor 来生成历史输出。 |
| `tokenshare.experiments` | experiment runner、fault simulation、metrics/report、AI profile、benchmarks 和 paper experiments。 | 协议核心不可替代的状态事实；不得重新定义插件验证权威或 replay 历史事实。 |
| `tests/*` | 与 `src/tokenshare/*` 镜像的单元、集成、故障和 regression 测试。 | 外部参考项目测试。 |

## 6. 外部参考资料落库与使用规则

联网查找资料后，如果资料被用于修改 TokenShare 的设计、代码、测试、README、feature 路线或其他项目文档，必须先完成本地落库和索引同步。只在最终回答或 TDD 里留下 URL 不算完成。

论文、技术报告和正式 PDF：

- 下载 PDF 或可复查的正式文本；如果已经转换/OCR，应把 TeX 或纯文本放入 `Doc/TechnicalDocument/tokenshare-paper-tex/`。
- 在 `Doc/TechnicalDocument/2026-06-04-tokenshare-paper-module-map.md` 中记录论文标题、来源 URL、本地文件路径、下载/转换日期、借鉴到的模块或设计点。
- 如果因版权、登录或访问限制不能保存全文，必须记录原因、可访问的元数据、本地摘要、访问日期和该资料影响了哪些项目文档；不要把无法复查的资料作为唯一依据。

开源项目和工程实现：

- 将被借鉴的项目浅克隆或 sparse checkout 到 `reference_repos/`，固定 commit 或 tag。
- 更新 `reference_repos/README.md`，记录上游仓库、commit、拉取范围、选择原因和观察重点。
- 不要把外部项目加入 TokenShare runtime dependency。
- 不要让 `pytest` 或 `compileall` 扫描外部项目；启动脚本已排除 `reference_repos/`。
- 不要复制大段外部代码；如果借鉴设计，应在设计文档、`progress.md` 或 `session-handoff.md` 中写清楚借鉴的是哪类边界思想。

普通在线文档、规范页面或博客：

- 如果只是回答用户临时问题，可以在回答中引用来源；如果进入项目设计或实现，必须在最相关的设计文档、`progress.md` 或 `session-handoff.md` 中记录来源 URL、访问日期、本地摘要和影响范围。
- 如果该在线文档对应可拉取仓库、版本化文档或 release tag，应优先保存对应仓库或 tag 到 `reference_repos/`，而不是只记录网页。

`reference_repos/` 是研究材料，不是项目源码。可以打开外部项目源码观察命名、目录边界和测试组织，但不能引入为 runtime dependency。

## 7. 状态更新指引

当导航规则、模块归属、验证命令或外部参考资料边界变化时，应同步更新：

- `AGENTS.md`：只保留强制启动入口和最高优先级规则。
- 本文：维护事实源、模块路由和外部参考资料落库规则。
- `README.md`：维护人类入口和仓库地图。
- `feature_list.json`：维护 active feature、source documents 和验证证据。
- `progress.md` / `session-handoff.md`：记录本轮变化、验证证据和下一步。
- `Doc/TechnicalDocument/2026-06-04-tokenshare-paper-module-map.md`：维护论文、技术报告、本地 TeX/OCR 和借鉴模块映射。
- `reference_repos/README.md`：维护外部项目本地拉取索引。
