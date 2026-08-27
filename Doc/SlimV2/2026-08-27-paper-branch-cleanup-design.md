---
status: review_requested
document: paper_branch_cleanup_design
scope: 论文对应仓库分支的实验设施清洗、公开命名、正式结果保留与多智能体实施治理
date: 2026-08-27
---

# 论文分支清洗设计

## 1. 目标

本设计把当前分支整理为论文对外引用的唯一仓库分支。外部读者进入 HEAD 后，只应看到：TokenShare 系统本体、Factorization 与 Lean 插件/checker、唯一一套当前实验设施、正式题库、论文工作区、正式指标结果和清楚的复现入口。

清洗不是修改实验结论，也不是重做实验。旧实验设施从 HEAD 物理删除，但由已发布的 annotated tag `slim-v2-reference-20260820` 保留历史访问能力。正式 Full 原始结果不得被删除、改写、重算或重新调用 provider。

## 2. 已核验的当前基线

- 当前分支为 `codex/slim-v2-baseline`。
- 旧实验源码约 77 个 tracked 文件、104,613 行文本；旧实验测试约 78 个 tracked 文件、76,041 行文本。
- `local/` 当前包含 29,037 个被 Git 跟踪的历史 pytest/实验过程产物，它们不属于系统本体或正式结果。
- 当前实验实现位于 `src/tokenshare/experiments/slim_v2/`，没有运行时导入旧 `tokenshare.experiments.paper_*` 模块。
- 系统本体没有导入旧 experiments；但 Factorization/Lean runtime adapter 与 `executors/__init__.py` 仍从巨型 `executors/ai_api.py` 导入 descriptor/旧 executor 符号，删除前必须先完成最小依赖抽离。
- 正式 Full run 位于本地 `TokenShareData/outputs/slim_v2/slim-v2-full-flash-20260823-233000-b4c8e951/`，包含 `run.json`、正式 `metrics/summary.json` 及 Experiment 1–5 的 JSONL/CSV 表。
- `TokenShareData/` 下没有 tracked 文件；仓库根 `.gitignore` 已显式加入 `/TokenShareData/`。
- 本地正式 Full 原始目录继续保留，外部归档位置等待用户咨询导师后另行决定。
- annotated tag `slim-v2-reference-20260820` 已发布到 `origin`；远端 tag object 为 `ab940428575707b3f8cf34716a2494d7d3c4901b`，peeled commit 为 `3489533e79cde05d6ae2a0c9f139785249f60f09`。

## 3. 冻结决策

### 3.1 对外名称

`Slim V2` 只是工程推进阶段的内部实现代号，不是实验设施的正式名称。清洗后的当前代码、测试、文档、题库、命令和结果入口统一使用 `experiments`：

- Python package：`tokenshare.experiments`；
- 测试：`tests/experiments/`；
- 题库：`benchmarks/experiments/`；
- 文档：`Doc/Experiments/`；
- Provider 配置：`configs/experiments/`；
- Git 内结果：`results/experiments/`；
- Windows launcher：`run_experiments.cmd`。

清洗时应删除只用于开发代号的 `Slim*` 用户可见类型、命令和文档称呼，改用职责名称。HEAD 不保留 `tokenshare.experiments.slim_v2` compatibility wrapper；旧 import 由已发布 tag/history 提供。正式结果中已持久化的历史事实不得改名，包括 run ID、schema version、pricing version、原始 metrics 内容和校验值。`RESULTS.md` 只需说明其中的 `slim_v2` 是产生该结果时使用的内部实现版本标识。

### 3.2 题库位置

题库不拆分为独立仓库。当前题库规模小，并与 profile、planned-unit 身份、Lean 环境和正式分母紧密绑定。HEAD 中只保留当前实验实际依赖的冻结题库及 manifest，删除旧 pilot、smoke、旧模型 cohort 和旧 paper profile/config。

题库 manifest 至少记录：schema/version、文件 SHA-256、case 数、来源与许可证、正式 Full run 使用的代码 commit 与选择规则。Provider config 不作为题库内容，应放在当前 experiments 的配置位置。

### 3.3 正式结果位置

Git 中保存：

- `run.json` 的发布副本；
- 正式 `metrics/summary.json`；
- Experiment 1–5 各 JSONL/CSV 表；
- 结果 manifest、文件大小与 SHA-256；
- 原始数据状态和获取说明；
- 已知合法缺失值及其解释。

约 7.14 GB、1,088,134 个非 metrics 文件的原始 Full 目录暂时保留在本地仓库的 ignored `TokenShareData/` 中。当前明确状态为 `pending_advisor_archive_decision`，不得填入虚假或临时下载 URL。导师决定归档平台后，以独立用户授权任务发布不可变归档，再更新 Git 内 manifest 和 `RESULTS.md`。这项后续发布不允许改写原始目录内容。

## 4. 最终 HEAD 布局

```text
TokenShare/
├─ README.md
├─ AGENTS.md
├─ REPRODUCIBILITY.md
├─ RESULTS.md
├─ benchmarks/experiments/
├─ configs/experiments/
├─ results/experiments/<official-run>/
├─ src/tokenshare/
│  ├─ core/
│  ├─ storage/
│  ├─ local_runtime/
│  ├─ plugins/
│  ├─ executors/
│  └─ experiments/
├─ tests/
│  ├─ core/
│  ├─ storage/
│  ├─ local_runtime/
│  ├─ plugins/
│  ├─ executors/
│  └─ experiments/
├─ Doc/
│  ├─ Experiments/
│  └─ TechnicalDocument/
├─ paper/
├─ verification/
└─ run_experiments.cmd
```

`README.md` 是唯一人类入口，不再引导读者进入 `feat-011`、Rxx、旧 paper/formal runner、budget gate 或被取代的实验参数。`feature_list.json` 与 `session-handoff.md` 删除。`progress.md` 的最终有效事实吸收到 `RESULTS.md` 后删除；最终 HEAD 不保留开发进度流水账。

`Doc/Experiments/` 只保留解释当前实验设计、指标、系统接线、实现和复现所必需的最终文档。实施计划、接力协议、阶段修复计划和历史设计过程不进入最终 HEAD，由 Git history/tag 保存。

## 5. 保留边界

必须保留：

1. `core/`、`storage/`、`local_runtime/` 的系统本体；
2. Factorization 与 Lean 插件、固定拆分、parser、verifier/checker、merge 与 root check；
3. 当前 experiments runner/provider/scenario/projector/reducer/storage/schema/CLI；
4. 当前 experiments 实际依赖的 provider transport、config 与 envelope parser；
5. 当前系统与 experiments 的风险相关 focused tests；
6. 当前正式题库与 Lean checker 所需 fixture/environment 输入；
7. 论文工作区 `paper/`；
8. 最小系统规格、当前 code map、实验权威、复现说明和结果说明；
9. 正式 metrics、run metadata 和校验 manifest；
10. `.gitignore` 中对本地 secret、cache、output 和 `/TokenShareData/` 的保护。

任何无法证明属于旧实验设施的文件默认保留。删除举证责任由实施者承担。

## 6. 删除边界

在依赖抽离和静态证明完成后，从 HEAD 删除：

1. 非当前 experiments 的旧实验源码与旧实验 tests；
2. formal/paper runner、旧 adapters、budget、receipt、evidence、checkpoint、response-bank、publication/eligibility/gate、lineage/digest closure；
3. 旧 pilot、smoke、旧 model cohort、旧 paper profile/config 和不被当前题库闭包引用的 benchmark 文件；
4. 旧 Rxx 状态链、feature/handoff、历史计划、旧实验权威、archive 文档和阶段过程文件；
5. legacy verification profiles、旧 LeanAudit/paper runner 启动入口及其测试 manifest；
6. Git 已跟踪的 `local/pytest-*`、旧实验 launcher、日志、fixture 输出和其他过程产物；
7. 依赖闭包证明不再需要的旧 executor/replay/response-bank 实现。

不得递归删除未跟踪的 `local/`、本地 API secret、`TokenShareData/`、正式 Full 原始目录、论文文件、许可证、系统代码或与旧实验无关的普通项目文件。

## 7. 实施顺序

### Stage A：清洗前只读审计

并行完成三个互不写入的审计：

- 系统/experiments import 与运行时依赖闭包；
- 正式 Full 结果、题库和校验事实；
- 文档、配置、verification 与 public entry 分类。

监督智能体汇总三份审计，形成精确 keep/delete/move manifest。没有 manifest 不得开始删除。

### Stage 1：冻结当前有效工作

审核当前未提交的 reducer 修复、论文目录迁移和文档变化。只提交已核验的正式结果与当前论文事实，不把清洗变更混入该 commit。记录正式 Full 原始目录的文件数、字节数、metadata fingerprint 和关键内容 SHA。

### Stage 2：正式命名迁移

使用 `git mv` 把当前实现从内部 `slim_v2` 命名提升为正式 `experiments` 命名，同步 tests、benchmarks、docs、launcher、imports、code map 和验证路径。冻结数据中的历史标识不改。

### Stage 3：共享依赖解耦

把插件需要的 descriptor builder 移到小型公共模块，更新 Factorization、Lean 和 executor exports。证明系统本体和当前 experiments 不再依赖待删除巨型旧 executor/response-bank/replay 模块。

### Stage 4：旧设施物理删除

严格按已批准 manifest 删除旧源码、测试、配置、文档、verification profiles 和 tracked `local/pytest-*`。不使用未解析 glob 递归删除；每批删除前后核对显式 tracked path 集。

### Stage 5：公开仓库收口

重写 README、AGENTS、复现说明、结果说明、题库 manifest、结果 manifest 和当前 verification。把正式 metrics 发布副本加入 Git，但不复制 ignored 原始 Full 大目录。

### Stage 6：最终验证与审计

完成离线全链验证、结果完整性复核、残留依赖扫描和外部读者体验审查。全部 gate 通过后才把分支声明为论文正式仓库分支。

## 8. 多智能体监督协议

根监督智能体负责唯一计划、阶段状态、任务包、写权限、reviewer 调度、commit 核验和最终完成判断。实施阶段采用“顺序写入、并行只读审计”：

1. Stage A 的三个审计智能体可以并行，但只能读取和报告；
2. Stage 1–5 每阶段只有一个新鲜实施智能体拥有写权限；
3. 实施者完成修改、focused verification、自审和单主题 commit；
4. 新鲜规格 reviewer 检查漏项、越界和误删；未通过则由原实施者修正并复审；
5. 规格通过后，新鲜质量 reviewer 检查依赖、可维护性、测试与清晰度；未通过则由原实施者修正并复审；
6. 两级 review 通过后，监督智能体独立复跑阶段验证并核对 commit；
7. 当前阶段所有意见关闭后，才允许下一个实施智能体启动；
8. Stage 6 可并行派出源码残留、数据完整性和外部读者体验三个只读 reviewer，最终结论由监督智能体合并。

每个任务包必须包含：起始 commit SHA、允许路径、禁止路径、保留不变量、精确删除/move 清单、验证命令、禁止 provider/network、禁止修改正式 Full 原始目录和预期单主题 commit。

多个实施者不得并行修改共享工作树。Reviewer 不得在审查阶段直接修代码。

## 9. 数据与破坏性操作安全

1. 清洗前后都核对正式 Full 原始目录的 resolved absolute path；它必须位于当前仓库的 `TokenShareData/` 下。
2. 所有删除目标先由 `git ls-files` 形成显式列表；不得对仓库根、`TokenShareData/`、`local/` 整体或未解析变量执行递归删除。
3. tracked `local/pytest-*` 只删除 manifest 中的精确路径；未跟踪文件不进入删除列表。
4. 正式结果只读验证输出到临时目录，不覆盖正式 metrics 或原始 run。
5. 不调用真实 provider，不重跑 Representative/Full，不补造缺失 usage，不把合法 `null` 改为 0。
6. 任何需要改变系统本体语义、实验公式、题库 case、正式结果内容或冻结 schema 的发现必须停止并报告用户，不能借清洗自行修改。

## 10. 风险驱动验证

每阶段只运行与其风险对应的 focused verification；最终至少包括：

1. 当前 `tokenshare.experiments` package import 与 compile 成功；
2. 当前 experiments focused tests 全部通过；
3. core、storage、local runtime、Factorization、Lean adapter 和 provider transport 的相关 focused tests 通过；
4. `plan` 仍产生 7,017 个论文 roots、106 个 Experiment 3 references 和相同 provider-call upper bounds；
5. 使用正式原始目录离线归约到独立临时位置，与 Git 中冻结的 11 个正式 metrics 文件逐项比较；
6. 清洗前后正式 Full 原始目录文件数、字节数、metadata fingerprint 和关键内容 SHA 完全一致；
7. 默认验证和全部清洗验证的真实 provider/network calls 为 0；
8. HEAD 中不存在旧 runner import、旧 namespace 入口、legacy verification 路由或 tracked pytest 运行产物；
9. 题库 manifest 的 case 数、文件 SHA 与 profile inventory 一致；
10. README、REPRODUCIBILITY、RESULTS、AGENTS、导航和 code map 不再把读者带回旧设施；
11. 不运行额外 LeanAudit、全量 Lean catalog、`lake`/`lean` 回归或付费实验。

## 11. 完成标准

只有以下条件全部满足，清洗才完成：

- HEAD 中只剩唯一 `tokenshare.experiments` 实验设施；
- 系统本体、Factorization、Lean、checker、正式题库、论文和正式 metrics 全部保留；
- 正式 Full 原始数据仍在本地 ignored `TokenShareData/`，其核验事实未变化；
- 旧设施只可通过已发布 annotated tag/history 获取；
- 根 README 和命令能够让新读者直接理解、运行计划、执行实验和离线归约；
- 默认验证不导入旧设施、不调用 provider，并通过当前 focused suite；
- 所有实施阶段都通过规格 review、质量 review 和监督智能体独立验证；
- 外部原始数据归档仍未决定时，结果 manifest 诚实记录 `pending_advisor_archive_decision`，不虚构公开可用性。

## 12. 非目标

- 不修改实验参数、公式、统计口径、provider、pricing、fault 或 ablation；
- 不重跑正式实验；
- 不把历史开发代号从已冻结结果中抹除；
- 不重写 Git 历史；
- 不建立新题库仓库；
- 不增加生产安全、攻击者模型、预算门禁或证据链；
- 不在导师决定前擅自选择或发布外部原始数据平台。
