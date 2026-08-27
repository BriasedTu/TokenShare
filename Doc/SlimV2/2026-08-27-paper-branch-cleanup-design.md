---
status: review_requested
document: paper_branch_cleanup_design
revision: extraction_first
scope: 论文对应仓库分支的实验设施清洗、公开命名、正式结果保留与多智能体实施治理
date: 2026-08-27
---

# 论文分支抽取式清洗设计

## 1. Context

当前 `codex/slim-v2-baseline` 分支包含已经跑出论文正式结果的系统本体、Factorization/Lean 插件与 checker、当前实验设施、正式题库、论文工作区和正式结果，同时也保留了多代旧实验 runner、测试、配置、文档和大量 tracked 过程产物。它适合作为开发历史，却不适合作为论文对外引用的最终 HEAD。

当前实验设施在工程推进时被自动命名为 `Slim V2`。该名称不向外部读者解释设施职责，因此清洗后的公开名称统一为 `experiments`。已冻结结果中的 run ID、schema、pricing、指标字段和路径记录属于历史事实，不因代码重命名而改写。

已发布 annotated tag `slim-v2-reference-20260820` 保留较早参考点：远端 tag object 为 `ab940428575707b3f8cf34716a2494d7d3c4901b`，peeled commit 为 `3489533e79cde05d6ae2a0c9f139785249f60f09`。

正式 Full 原始目录当前保留在本地 ignored 路径 `TokenShareData/outputs/slim_v2/slim-v2-full-flash-20260823-233000-b4c8e951/`，约 7.14 GB、1,088,134 个非 metrics 文件。`TokenShareData/` 没有 tracked 文件，根 `.gitignore` 已显式忽略 `/TokenShareData/`。外部归档位置等待用户咨询导师，状态必须如实记录为 `pending_advisor_archive_decision`。

初步静态审计支持独立抽取：当前实验实现约 14 个源码文件，没有运行时导入旧 `tokenshare.experiments.paper_*`；系统本体没有导入 experiments。已知需要处理的共享边界是 Factorization/Lean runtime adapter 与 `executors/__init__.py` 仍从巨型 `executors/ai_api.py` 取得 descriptor/旧 executor 符号。旧实验本身约有 77 个源码文件、78 个测试文件，`local/` 另有约 29,037 个 tracked 历史过程文件；从空目标树抽取可以让这些内容默认缺席。

## 2. Problem

以“在原工作树里逐批删除”为主线存在三个结构性风险：

1. 文件数量巨大，删除清单稍有遗漏就会把旧设施残留在最终 HEAD；
2. 旧设施和系统本体共处多年，按目录删除容易误伤隐藏依赖、正式题库或正式结果所需输入；
3. 当前工作树含尚待归档的有效修改，直接进行大规模删除会让基线、清洗变更和其他合法工作混在一起。

因此采用用户批准的**抽取式清洗**：先冻结完整来源，再从一个具有正常 Git 祖先关系的空目标树开始，只把最终 HEAD 应当拥有的内容依次抽入。目标树缺少依赖时，以失败证据申请从冻结来源补取最小闭包；旧设施不会因为“暂时不知道能否删除”而自然进入目标树。

## 3. Goals and Success Criteria

最终分支是论文对外引用的唯一仓库分支。外部读者进入 HEAD 后只看到：

1. TokenShare 系统本体；
2. Factorization 与 Lean 插件、真实 checker 及其必要运行输入；
3. 唯一一套公开名为 `experiments` 的实验设施；
4. 正式题库及其不可变 manifest；
5. 论文工作区、权威文档、复现入口；
6. 正式 Full 的指标、run metadata、校验 manifest 和原始数据获取状态。

完成必须同时满足：

- 系统本体和实验设施在不访问源工作树的全新目标工作树中能够安装、导入、运行 focused tests 和离线端到端验证；
- 正式题库的 case 集、选择结果和 SHA-256 与清洗前完全一致；
- 正式原始目录未被写入，关键 fingerprint 不变，11 个正式 metrics 可由原始目录离线重新归约并逐文件比对；
- HEAD 不含旧实验源码、旧实验测试、旧 runner、旧配置、旧文档或 tracked 过程产物；
- 当前分支只通过 fast-forward 接收目标分支，不重写历史、不 force push；
- 原始 Full 数据未决定外部归档前继续留在本地 ignored `TokenShareData/`，不虚构下载地址。

## 4. Scope

### 4.1 必须抽取

1. `core/`、`storage/`、`local_runtime/` 中构成协议本体的实现；
2. Factorization 与 Lean 插件的固定拆分、parser、verifier/checker、merge、root check 和必要环境输入；
3. 当前实验的 runner、provider、scenario、projector、reducer、storage、schema 和 CLI；
4. 当前实验真实依赖的 provider transport、config loader 和 envelope parser；
5. 上述行为的风险相关 focused tests；
6. 正式 Factorization/Lean 题库、选择配置、manifest 和许可证/来源说明；
7. 论文工作区 `paper/`；
8. 当前系统规格、实验权威、系统接线、code map、复现说明和结果说明；
9. 正式 `run.json`、`metrics/summary.json`、Experiment 1–5 JSONL/CSV、结果 manifest；
10. 最小构建/依赖文件、verification 入口、launcher、`.gitignore`、许可证和仓库治理文件。

### 4.2 禁止回迁

1. 非当前 experiments 的旧实验源码、tests、launchers 和 runner；
2. 旧 formal/paper pipeline 的 adapters、budget、receipt、evidence、checkpoint、response-bank、publication/eligibility/gate、lineage/digest closure；
3. 非正式题库的 pilot、smoke、旧模型 cohort 和旧 paper profile/config；
4. 旧 Rxx 状态链、legacy feature/handoff、历史实施计划、接力协议和阶段修复过程文档；
5. legacy verification profiles、旧 LeanAudit/paper runner 入口及其 manifest；
6. Git 已跟踪的 `local/pytest-*`、旧实验日志、缓存、fixture 输出和其他过程产物；
7. 仅为旧设施服务、且不在目标依赖闭包中的巨型 executor/replay 实现。

无法证明必要的内容不进入目标树。反过来，任何失败证据证明系统或正式实验确实需要的内容，均按第 8 节的受控补取流程评估，而不是为了满足预设删除数字拒绝保留。

### 4.3 非目标

- 不修改实验参数、公式、统计口径、provider、pricing、fault 或 ablation；
- 不重跑 Representative/Full，不调用真实 provider；
- 不把历史开发代号从冻结结果中抹除；
- 不重写 Git 历史、不 force push；
- 不建立独立题库仓库；
- 不增加生产安全、攻击者模型、预算门禁或证据链；
- 不在导师决定前擅自选择或发布外部原始数据平台。

## 5. Target Repository Shape

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

`README.md` 是唯一对外人类入口。它不再把读者引向 `feat-011`、Rxx、旧 paper/formal runner、budget gate 或被取代的实验参数。`Doc/Experiments/` 只保留理解和复现当前实验所必需的权威内容；过程性文档留在 Git history/tag 中。

公开路径统一为：Python package `tokenshare.experiments`、tests `tests/experiments/`、题库 `benchmarks/experiments/`、配置 `configs/experiments/`、文档 `Doc/Experiments/`、Git 内结果 `results/experiments/`、launcher `run_experiments.cmd`。最终 HEAD 不保留 `tokenshare.experiments.slim_v2` compatibility wrapper。

## 6. Git and Worktree Architecture

### 6.1 冻结来源

Stage 0 先审核并提交当前工作树中所有应进入完整来源的合法修改，使源工作树回到 clean 状态。记录冻结 commit SHA，并创建本地 annotated tag `experiments-pre-cleanup-20260827`。发布该新 tag 属于独立远端写入，只有用户再次明确授权后执行；已有远端 tag 不受影响。

冻结后，当前 `slim-v2-baseline` 工作树只读：不再接收实现、格式化、测试快照更新或文档修补。监督智能体在每个阶段核对其 HEAD、status 和正式原始目录 fingerprint 未变化。

### 6.2 空目标树与正常祖先关系

计划目标工作树为现有 worktrees 根目录下的 sibling，例如 `E:\TokenEcnomic\TokenShareWorktrees\experiments-clean-extraction`；计划目标分支为 `codex/experiments-clean-extraction`。实施前必须验证目标路径和分支不存在，避免覆盖现有工作。

目标分支的第一个 commit 是**空树 commit**，其 parent 明确设为冻结源 commit。这样目标从空内容开始，但仍是当前分支的直接后代：

```text
冻结源 commit
    └─ 空树 commit
         └─ 骨架
              └─ 系统本体
                   └─ 权威文档与题库
                        └─ experiments
                             └─ 论文与正式结果
                                  └─ 最终公开 HEAD
```

这种结构同时满足：目标不继承源 tree 内容、完整历史仍可达、最终当前分支可执行 `git merge --ff-only codex/experiments-clean-extraction`。不使用 orphan 分支、squash 合并、rebase、历史改写或 force push。

### 6.3 自包含约束

目标树运行时不得通过相对路径、绝对路径、环境变量、junction、symlink、editable install 或 Python import 访问源工作树。所有验证在明确清除源仓库路径和旧 editable install 影响的环境中执行。

## 7. Extraction Sequence

### Stage A：清洗前并行只读审计

由三个只读审计智能体并行产出：

1. 系统/experiments import 与运行时依赖闭包；
2. 正式 Full、正式题库、选择结果和校验事实；
3. 文档、配置、verification、public entry 的抽取/禁止回迁分类。

监督智能体合并为带来源 commit、目标路径和理由的 extraction manifest。该阶段不得写仓库。

### Stage 0：冻结完整来源

审核当前 reducer 修复、论文目录迁移和文档变化，逐项验证并形成单主题 commits；不把抽取变更混入冻结提交。随后运行清洗前基线，记录源 commit、工作树状态、题库 manifest、正式 metrics SHA、原始目录文件数/字节数/metadata fingerprint 和关键内容 SHA，创建本地 pre-cleanup annotated tag，并转入只读。

### Stage 1：建立空目标与最小骨架

从冻结 commit 创建带 parent 的空树 commit，在 sibling worktree checkout 目标分支。只抽取许可证、依赖声明、package scaffold、最小 README/AGENTS、`.gitignore` 和 verification 骨架；不先复制整个目录再删减。

### Stage 2：抽取系统本体

抽取 core、storage、local runtime、Factorization/Lean 插件/checker 和实际依赖的 executor transport。对插件仍依赖巨型 `executors/ai_api.py` 的 descriptor 符号，直接在目标树建立职责单一的最小公共模块，并从冻结源提取所需行为，不搬入旧 response-bank/replay 体系。

本阶段必须先让系统 package、协议状态机、存储、插件 adapter/checker 和多 worker Factorization 纵向路径通过，再允许抽取实验设施。

### Stage 3：抽取权威文档与正式题库

把仍有效的设计宪章、指标权威、系统接线和实现说明整理为公开 `Doc/Experiments/` 内容。完整抽取正式 Factorization/Lean 题库、选择配置、manifest、来源和许可证；路径可改名，内容与选择身份不得变化。

### Stage 4：抽取唯一实验设施

从冻结来源中当前 `slim_v2` 实现抽取行为，直接落到 `src/tokenshare/experiments/`，tests 落到 `tests/experiments/`。同步公开职责命名、CLI、config 和 launcher；不先复制旧 package 再保留 compatibility 层。冻结 run 中历史字符串保持原样。

本阶段验证 Experiment 1–5 fake-transport 管线、Experiment 2–4 零新增 provider 调用、resume、projector/reducer 和 golden metrics。

### Stage 5：抽取论文、正式结果与公开入口

抽取 `paper/`、正式 run metadata、11 个 metrics 发布副本及其 manifest。重写 README、REPRODUCIBILITY、RESULTS、AGENTS、导航和 code map，使外部读者从一个入口理解系统、运行 plan、执行实验和离线归约。ignored 原始 Full 目录不复制到目标工作树，也不加入 Git。

### Stage 6：clean-room 最终验证与审查

在目标工作树的干净环境执行第 10 节全部最终 gate。之后并行启动源码残留、数据完整性和外部读者体验三个只读 reviewer；监督智能体合并结论并关闭全部问题。

### Stage 7：fast-forward 当前分支

只有 Stage 0–6 的证据完整且源工作树仍处于冻结 SHA，才允许当前分支执行 `--ff-only` 前移到目标分支。若 Git 判断不能 fast-forward，立即停止并报告，不采用替代合并或历史重写。远端 push 仍需用户对该外部写入另行明确授权。

## 8. Missing-Dependency Protocol

目标树遇到失败时不得笼统回迁旧目录。实施智能体提交 missing-dependency request，至少包含：

1. 失败的 import、test、CLI 或 runtime 证据；
2. 缺失行为服务的系统/实验职责；
3. 所需最小 symbols、数据文件或配置字段；
4. 现有目标公共接口为何不足；
5. 直接搬入原路径是否会带回旧 gate、budget、evidence、response-bank 或其他禁止设施；
6. 建议的最小抽取方式与 focused verification。

监督智能体核对冻结来源并批准 extraction manifest 的最小扩展。只能从记录的冻结 commit 定点读取，不能从后来变化的 live source、其他 branch 或未跟踪文件取材。补取完成后必须增加能证明该职责的风险相关验证，并再次执行禁止回迁扫描。

## 9. Multi-Agent Governance

根监督智能体维护唯一 extraction manifest、阶段状态、任务包、写权限、reviewer 调度、commit 核验和最终完成判断。执行模式为“顺序写入、并行只读审计”：

1. Stage A 的三个审计智能体可并行，只读报告；
2. Stage 0–5 每阶段只有一个新鲜实施智能体拥有目标工作树写权限；
3. 实施者完成 focused verification、自审和单主题 commit；
4. 新鲜规格 reviewer 检查漏项、越界和错误回迁；原实施者修正，直至规格通过；
5. 新鲜质量 reviewer 检查依赖边界、可维护性、测试和公开清晰度；原实施者修正，直至质量通过；
6. 两级 review 后，监督智能体独立复跑阶段 gate、核对 diff/commit/目标 status，再启动下一阶段；
7. Stage 6 的三个终审可并行但保持只读，最终结论由监督智能体作出。

每个任务包必须包含：冻结来源 SHA、目标起始 SHA、允许写入路径、禁止路径、抽取条目、保留不变量、验证命令、禁止 provider/network、禁止访问/修改正式 Full 原始目录和预期单主题 commit。Reviewer 不得直接修代码，多个 writer 不得并行修改目标树。

## 10. Risk-Driven Verification

### 10.1 清洗前基线

冻结来源至少记录并验证：

- retained Python packages 可导入/compile；
- 当前 core/storage/local runtime/Factorization/Lean adapter/provider transport focused tests；
- 当前 experiments focused suite、CLI `plan`、fake-transport Experiment 1–5、resume 和 reducer golden；
- 正式 plan 的 7,017 个论文 roots、106 个 Experiment 3 references 和 provider-call upper bounds；
- 正式题库文件清单、case IDs、选择结果和逐文件 SHA-256；
- 正式 `run.json` SHA-256 `3FEE9E896EEAC6BE2A4FA082DCDDDEF4DB92B21742B6234333BC11A30E2E9478`；
- 正式 `metrics/summary.json` SHA-256 `8A7F0D77D146E97D633F14CC4284501B2F4ECB479B70157E2559ECE913B76932`；
- 正式原始目录 resolved path、文件数、字节数、metadata fingerprint 和关键内容 SHA。

若当前合法未提交修改尚未通过相应验证，Stage 0 不得冻结。

### 10.2 阶段阻断门

| 抽取阶段 | 必须证明的行为 |
|---|---|
| 系统本体抽取 | core、storage、local runtime、Factorization/Lean adapter/checker 和 executor transport 的 retained tests 通过 |
| 权威文档抽取 | 公开规格与实际代码/命令一致，没有链接回源工作树或旧主线 |
| **旧题库与无引用配置清理** | 正式题库完整保留，迁移后 case 集、选择结果和 SHA-256 与清洗前完全一致 |
| experiments 抽取 | `tokenshare.experiments` import、CLI `plan`、Experiment 1–5 fake pipeline、resume 和 reducer golden 通过 |
| 正式结果抽取 | Git 内 11 个 metrics 与正式原始 Full 离线重新归约结果逐文件一致 |
| 公开收口 | 从全新 checkout 可按 README 完成安装、plan 和离线 smoke，且不需要源工作树 |

### 10.3 最终 clean-room gate

1. 在不含源路径和旧 editable install 的环境中 import/compile 全部 retained packages；
2. 运行全部 retained tests，不运行已排除的旧 tests；
3. 运行多 worker Factorization 纵向路径；
4. 运行一次有时间上限的真实本地 Lean checker smoke，证明 parser/checker/toolchain 接线仍有效；它不是全量 LeanAudit；
5. 运行 fake-transport Experiment 1–5，核对 Experiment 2–4 provider calls 为 0；
6. 验证 resume、projector、reducer 和 golden outputs；
7. 从本地 ignored 正式原始目录只读归约到独立临时目录，与 Git 内 11 个 metrics 逐文件比较；
8. 比较清洗前后题库 manifest、正式原始目录 fingerprint、`run.json` 和 `summary.json` hashes；
9. 扫描 imports、paths、docs、commands 和 tracked files，确认无旧 runner、旧 namespace、legacy verification 或 tracked pytest 产物；
10. 记录真实 provider/network calls 为 0。

真实在线 provider smoke、Representative/Full 重跑、额外 LeanAudit、全量 Lean catalog 和 `lake`/`lean` 回归不属于清洗验证；若以后需要，必须由用户单独授权。

## 11. Risks and Mitigations

| 风险 | 影响 | 缓解与证据 |
|---|---|---|
| 动态 import、配置路径或运行时数据未被静态审计发现 | 目标树导入成功但真实路径失败 | 每阶段 clean-room focused tests、fake pipeline、正式离线归约；按 missing-dependency protocol 最小补取 |
| 当前合法未提交工作未进入冻结来源 | 清洗后丢失真实修复或论文事实 | Stage 0 逐项审核、验证、单主题提交；冻结前必须 clean |
| 正式题库、选择身份或原始结果被误改 | 论文口径失真 | 源只读、逐文件 SHA/case/selection manifest、原始 fingerprint 和离线 metrics 对比 |
| 补依赖时重新带回旧实验设施 | 最终 HEAD 再次混乱 | 每次扩展 extraction manifest 都要说明职责与最小性，并执行禁止回迁扫描 |
| 空目标树造成当前分支无法正常接收 | 需要危险合并或历史改写 | 空树 commit 以冻结 commit 为 parent；最终仅允许 `--ff-only`，失败即停止 |
| 验证环境悄然引用源工作树 | 产生虚假的“目标可运行”结论 | 清除源路径/editable install，禁止 link/junction，使用全新 checkout 复核 |
| 不调用真实 provider 无法证明在线服务仍可用 | 外部服务兼容性不在本轮得到新证据 | 明确排除付费在线验证；保留 transport contract tests，后续可由用户独立授权 bounded smoke |

## 12. Rollback and Recovery

1. Stage 7 前，当前源分支和源工作树始终保留在冻结 commit；抽取失败只需废弃目标 worktree/branch，不影响来源和正式原始数据。
2. 每个阶段使用单主题 commit；问题优先在目标分支追加修复或在尚未共享时撤销该阶段 commit，不改源历史。
3. 已发布 `slim-v2-reference-20260820` tag、冻结源 commit 和本地 pre-cleanup annotated tag 提供三层定位；新 tag 在未获授权前不发布。
4. 最终整合只执行 fast-forward。若源 HEAD/status/fingerprint 变化或 fast-forward 条件不成立，停止整合并重新审计差异。
5. 正式原始目录从不作为 worktree 删除/移动目标；所有归约输出进入新临时目录，验证失败时只清理该明确临时目录。

## 13. Alternatives Considered

### 13.1 原工作树删除式清洗

优点是 Git diff 直观；缺点是旧文件规模大、误删和残留风险都高，并容易混入当前未提交工作。已拒绝作为实施主线。

### 13.2 Orphan 分支或重写历史

可以得到视觉上全新的仓库，但会破坏正常祖先关系，使当前论文分支无法 fast-forward，并增加强推和引用失效风险。已拒绝。

### 13.3 新建独立仓库或独立题库仓库

会切断系统、题库、选择规则、Lean 环境、论文和正式结果之间的版本绑定。当前规模与复现目标不支持该拆分。已拒绝。

## 14. Implementation Handoff

本设计获书面复核后，下一步才进入实施计划编写。实施计划必须把 Stage A–7 拆成可执行任务，列出每阶段精确路径、命令、预期证据、review checkpoint 和 commit 边界。设计批准本身不授权立即创建 worktree、移动分支、发布新 tag、推送远端或开始删除。
