# TokenShare 仓库信息与运行数据治理

日期：2026-07-30

状态：当前权威治理规则。本文只定义仓库信息层级、当前事实来源、运行数据边界和防复发门禁；协议与论文实验参数分别由系统规格和唯一权威实验设计定义。

## 1. 目标

TokenShare 的默认接管路径必须满足以下结果：

- 新 agent 不读取历史设计流水账即可判断当前 feature、阻塞和下一步。
- 同一事实只有一个当前权威来源；历史文档只能解释当时为什么这样做，不能覆盖当前实现。
- 运行结果、supervision、diagnostic 和测试临时文件位于仓库外，不干扰递归搜索、编译和索引。
- 文档按职责拆分，不通过把多个旧文件拼成一个超大文件实现“合并”。
- 当前文档描述必须能由代码、配置、测试或持久化证据复核。

## 2. 信息层级

### Tier 1：默认接管

每次会话默认只读取：

1. `AGENTS.md`：强制规则、边界和启动步骤。
2. `feature_list.json`：精简 feature 状态与当前 focus。
3. `progress.md`：当前实现事实与最近验证；不保留跨轮编年史。
4. `session-handoff.md`：当前阻塞、工作树边界和下一步。
5. `Doc/agent-navigation.md`：按任务路由到 Tier 2/3。

### Tier 2：按任务加载

- 系统边界与模块：`Doc/TechnicalDocument/tokenshare_v1_complete_spec.md`、`tokenshare_v1_code_map.md`。
- 论文 Experiment 1–5：`tokenshare_latest_real_plugin_experiment_design.md`。
- Lean checker：`2026-07-22-lean-checker-verification-profiles-design.md`。
- 参数变更来源：`tokenshare_experiment_parameter_decision_log.md`。

Tier 2 文件不得互相宣称同一事实都为“最新”。导航必须明确每个文件唯一负责的问题。

### Tier 3：历史与外部材料

- `Doc/archive/`：已完成、被取代或只供 provenance 的设计、计划、Prompt、旧 code map 和旧状态快照。
- `Doc/TechnicalDocument/tokenshare-paper-tex/`：论文/报告本地材料。
- `reference_repos/`：外部源码参考。

Tier 3 不进入默认接管链。常规搜索必须先限定目录；仓库根 `.ignore` 默认排除 archive/reference/cache/pytest 临时目录，需要 provenance 时再显式指定路径读取。

## 3. 当前事实职责

| 文件 | 只负责 | 不负责 |
|---|---|---|
| `AGENTS.md` | 强制规则、范围、验证、完成标准 | 历史过程、逐次测试输出、具体 bug 复盘 |
| `feature_list.json` | feature 状态、当前 focus、验收摘要、证据指针 | 逐任务完整日志、大段自然语言证据 |
| `progress.md` | 当前 maintenance/feature 的短期进度与最新验证 | 跨月编年史 |
| `session-handoff.md` | 下一会话恢复所需的当前事实 | 已被覆盖的旧交接 |
| `Doc/agent-navigation.md` | 事实源和模块/任务路由 | 复制系统规格或实验参数 |
| `README.md` | 人类快速入口和常用命令 | 完整 code map、实验决策日志 |

## 4. 运行数据边界

运行数据根由 `TOKENSHARE_DATA_ROOT` 控制：

- 默认值：仓库同级 `TokenShareData/`。
- 环境变量必须解析为仓库外绝对目录；指向仓库内部时 fail closed。
- 默认实验结果：`<data-root>/outputs/experiments/`。
- 默认 diagnostic：`<data-root>/outputs/diagnostics/`。
- 默认 supervision：`<data-root>/local/supervision/`。
- 迁移备份：`<data-root>/migration-backups/`。

普通实验 CLI 的显式 `--output-root` 优先；paper runner 必须显式指定全新 `--output-root`，不得复用固定容器。历史 evidence 移动后保持文件内容不变；仓库绝对、`outputs/...` 仓库相对和 `runs/...` suite 相对路径只在读取时解析，不得 resume、补写或重分类历史 paid/smoke run。路径迁移清单记录旧位置、新位置、文件数、字节数和抽样哈希。

2026-07-30 已完成首次迁移：`outputs/`、`local/supervision/` 与 `local/` 根生成的 `.log/.exit` 合计 101,048 个文件、931,156,489 字节，移动到 `E:\TokenEcnomic\TokenShareData`。迁移前后 inventory 与 24 个确定性样本逐项一致；完整 manifest 为 `E:\TokenEcnomic\TokenShareData\migrations\2026-07-30-runtime-data-relocation.json`。三个 ACL 拒绝的仓库根 `pytest*` 临时目录明确排除，未触碰。

仓库内不创建指向数据根的 junction/symlink，避免不同搜索工具递归进入外部数据。仓库只保留路径规则和迁移清单摘要。

## 5. 文档归档规则

满足任一条件的文档进入 `Doc/archive/`：

- feature 已完成，文件只记录实施过程；
- 参数或设计已由唯一权威文档吸收；
- 文件标题或正文明确为 historical/superseded；
- agent Prompt 已执行完毕；
- code map 描述的实现已被新的短 code map 接管。

归档不等于删除。`Doc/archive/README.md` 只维护按主题索引和“不得作为当前事实”的规则，不复制全文摘要。归档文件内部的旧路径和旧参数作为历史原貌保留；当前文件不得引用归档文档来定义现行行为。

## 6. 代码治理顺序

本轮只修改与仓库接管和运行数据边界直接相关的代码。业务代码大文件拆分采用独立 feature，顺序为：

1. 用 characterization tests 锁定外部行为。
2. 优先拆 `run_paper_experiments.py` 的 CLI/preflight/launch 边界。
3. 再拆 formal runner 的 dispatch/evidence/budget/finalization。
4. 再按 Exp1–5 拆 metrics。
5. 最后评估 `ProtocolEngine` 的应用流拆分，保持单一 ledger 写入权威。

不得仅为降低行数移动函数，也不得把领域逻辑重新塞入协议核心。

## 7. 防复发门禁与人工审核

Fast verification 自动检查：

- Tier 1 文件存在且 `feature_list.json` 可解析。
- 恰好一个 `in-progress` feature。
- Tier 1 文件总大小和单文件大小不超过约定预算。
- `feature_list.json` 顶层只含 schema/current/features 等稳定字段，不再追加任务名字段。
- compileall 只扫描代码和测试目录，不遍历运行数据、归档资料或外部参考仓库。
- 默认数据根位于仓库外。

以下语义一致性由文档修改后的人工二次审核负责，不能声称由 Fast 自动保证：

- 当前文档没有互相竞争的“唯一权威实验设计”入口；
- “当前”“最新”“已完成”等状态表述与代码、配置和验证证据一致；
- 冻结历史文档中的旧状态不会覆盖导航指定的当前权威。

任何自动门禁变化都必须有测试；没有验证输出和语义二次审核，不得更新 maintenance focus 的完成状态。
