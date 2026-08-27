# 论文分支抽取式清洗 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** `review_requested`

**Goal:** 从冻结的论文实验来源提交建立一条具有正常祖先关系的干净抽取分支，只保留系统本体、正式题库、当前实验设施、权威文档、论文工作区和正式全量结果；将当前实验设施的公开名称统一为 `experiments`，并用可复核证据证明系统、实验设施和正式结果在清洗后仍然有效。

**Architecture:** 当前分支先整理并冻结为只读来源；目标分支从“父提交为冻结来源、内容为空”的提交开始，按系统本体、题库与配置、实验设施、论文与正式结果的顺序构造。每一阶段只允许从冻结 SHA 抽取清单内的文件；若验证暴露缺失依赖，必须先提交最小依赖申请，再从同一冻结 SHA 补取。最终由当前分支 `--ff-only` 前移到目标分支，不重写历史、不强推远端。

**Tech Stack:** Git worktree、PowerShell、Python 3、pytest、SQLite/JSON/JSONL、本地 Lean checker、SHA-256 清单、Codex 监督智能体与阶段化子智能体。

---

## 0. 已批准的不变量

实现人员不得在执行中重新解释以下决定：

- 公开设施名称和包路径为 `experiments`；最终运行时代码不得存在 `tokenshare.experiments.slim_v2` 兼容层。
- 已冻结结果中的 run ID、schema 字符串值、pricing/version 字符串值、指标字段和历史路径记录保持逐字不变；它们是历史事实，不属于公开命名残留。
- 正式题库完整保留。迁移前后 case 集、选择结果和 SHA-256 必须完全一致。
- 正式全量结果的 `run.json` 和 11 个 metrics 文件进入 Git；约 7.14 GB 的原始运行目录继续保存在本地 ignored `TokenShareData/`，外部归档状态保持 `pending_advisor_archive_decision`。
- 清洗期间禁止调用真实 provider，禁止重跑 Representative/Full，禁止覆盖正式运行目录的任何文件。
- 不执行全量 LeanAudit 或全 Lean catalog 回归；最终只运行一个有界真实本地 Lean checker smoke，其余接线验证优先使用 fake、固定 fixture 和静态合同。
- 本计划不发布 tag、不 push 分支、不创建远端 release。任何远端写入均需用户另行明确批准。
- 目标分支暂定为 `codex/experiments-clean-extraction`；目标工作树暂定为 `E:\TokenEcnomic\TokenShareWorktrees\experiments-clean-extraction`。执行前必须再次证明二者均不存在，存在时停止而不是复用或覆盖。

## 1. 监督与交接协议

根智能体是唯一监督者，负责阶段授权、工作树状态核验、提交验收和最终 fast-forward。实施使用 `superpowers:subagent-driven-development`，但并行只用于彼此独立的只读审计或最终只读评审；所有写入阶段顺序执行。

每个写入阶段采用以下固定角色：

1. 一个 fresh writer 只处理该阶段列出的文件；
2. 一个 fresh spec reviewer 只读检查是否满足本计划和抽取清单；
3. 一个 fresh quality reviewer 只读检查实现、验证证据和范围污染；
4. 原 writer 根据两类评审意见修正；监督者复核并创建阶段提交。

监督者给每个子智能体的任务包必须包含：冻结来源 SHA、目标 HEAD、允许路径、禁止路径、不得联网/不得调用 provider/不得写正式原始运行目录、验证命令和期望结果。子智能体不得自行扩大抽取范围，不得直接合并、打 tag、push 或操作另一个工作树。

任何失败后的“回老地方找”都必须形成一条最小依赖申请，至少包含：

- 失败命令和完整错误摘要；
- 缺失职责，而不是先报一个想复制的目录；
- 所需的最小 symbol、数据文件或合同；
- 为什么现有公开接口不能满足；
- 对旧 runner、receipt、budget authority、response bank、publication gate、eligibility、lineage/evidence closure 等禁止负担的检查；
- 补取后的 focused verification。

只有监督者批准后，writer 才能从冻结 SHA 的明确路径补取；禁止从变化中的来源 HEAD、其他 worktree 工作区或绝对路径建立运行时 import、链接或复制关系。

## 2. 固定路径与预期提交

实施开始时记录以下变量，后续所有证据都引用实际解析值：

```powershell
$sourceRoot = 'E:\TokenEcnomic\TokenShareWorktrees\slim-v2-baseline'
$targetRoot = 'E:\TokenEcnomic\TokenShareWorktrees\experiments-clean-extraction'
$targetBranch = 'codex/experiments-clean-extraction'
$freezeTag = 'experiments-pre-cleanup-20260827'
```

除带有明确 `git -C` 或绝对脚本路径的命令外，Task D–G 的相对路径命令必须在 `Push-Location $targetRoot` 与 `Pop-Location` 之间执行；Task H 的验证命令必须在 `Push-Location $reviewRoot` 与 `Pop-Location` 之间执行。这样 Python 的 repository root、fixture 相对路径和 import 根都来自被验证的工作树，而不是来源工作树。

预期阶段提交（若评审修正需要追加提交，可以追加，但不得 squash 或 rebase）：

1. `docs(experiments): freeze extraction manifest`
2. 当前来源工作所需的主题提交，名称由实际 dirty diff 决定
3. `docs(experiments): record extraction baseline`
4. `chore: start clean extraction tree`
5. `chore: establish public experiment repository skeleton`
6. `refactor(system): extract protocol runtime and plugin core`
7. `data(experiments): preserve authoritative corpora and configs`
8. `refactor(experiments): publish the current experiment facility`
9. `docs(paper): preserve paper workspace and formal results`
10. 如有最终评审修正，按职责追加一个或多个小提交

## 3. Task A：并行只读审计并冻结抽取清单

**Files:**

- Create: `Doc/SlimV2/2026-08-27-paper-branch-extraction-manifest.md`
- Read only: `src/tokenshare/**`
- Read only: `tests/**`
- Read only: `benchmarks/**`
- Read only: `configs/**`
- Read only: `verification/**`
- Read only: `Doc/SlimV2/**`
- Read only: `paper/**` 以及当前 HEAD 中的 `paper.tex`、`paper.bbl`、`reference.bib`
- Read only: `TokenShareData/outputs/slim_v2/slim-v2-full-flash-20260823-233000-b4c8e951/run.json`
- Read only: 同一正式运行目录下 `metrics/**`

- [ ] 监督者记录当前 `git rev-parse HEAD`，并确认本阶段除 manifest writer 外的三个审计智能体全部只读。

- [ ] 并行派发依赖审计智能体：从当前 14 个 `src/tokenshare/experiments/slim_v2/*.py` 入口出发，输出源码、插件、executor、配置和 fixture 的传递依赖清单；每个保留项附 import/call-site 证据，每个排除项附“不在闭包内”的证据。

- [ ] 并行派发题库/结果审计智能体：输出正式题库全部文件、配置文件、case ID 集、profile 选择结果、正式结果文件、字节数和 SHA-256；严禁写入或重算落盘。

- [ ] 并行派发文档/harness 审计智能体：输出对外必需文档、论文文件、最小验证脚本和应排除的过程文档/旧验证 profile。

- [ ] 审计全部结束后，派发一个 fresh consolidation writer 创建 manifest。manifest 必须逐路径列出：`keep_exact`、`move_exact`、`synthesize`、`exclude_exact`、`historical_string_allowlist`、`verification_commands`，不得使用“类似文件”“相关测试”等模糊表述。

- [ ] manifest 至少落实以下候选边界，审计证据只能收紧或提交缺失依赖申请，不能无证据扩张：

  - 保留 `src/tokenshare/core/**`、`storage/**`、`local_runtime/**`、`protocol_engine.py`；
  - 保留 factorization 与 Lean plugin/checker 的运行闭包，但排除只服务旧 evidence pipeline 的 `src/tokenshare/plugins/lean_proof/replay_evidence.py`；
  - 保留 executor contracts、registry、deterministic、mock、AI transport/config/artifacts；排除旧 `AIAPIExecutor`、selector、request identity、response bank、trace-backed 和旧 replay；
  - 从 `src/tokenshare/executors/ai_api.py` 仅抽出 descriptor 构造职责到新文件，不复制巨型模块；
  - 当前实验设施 14 个源码文件和 `tests/experiments/slim_v2/**` 迁移到无 `slim_v2` 层级的新路径；
  - 正式题库与 Lean checker 语义权威文件完整迁移；
  - 不抽取 `feature_list.json`、`session-handoff.md`、Rxx 历史状态、旧 paper/formal runners、tracked `local/` 过程产物和旧结果目录。

- [ ] 监督者检查 manifest 中每一个 `move_exact` 都有唯一源路径和目标路径，每一个 synthesized 文件都有职责、输入事实和验证方法。

- [ ] 运行文档自检：

```powershell
git diff --check -- 'Doc/SlimV2/2026-08-27-paper-branch-extraction-manifest.md'
Select-String -Path 'Doc/SlimV2/2026-08-27-paper-branch-extraction-manifest.md' -Encoding UTF8 -Pattern 'TODO|TBD|FIXME|类似文件|相关测试'
```

预期：`git diff --check` 退出码 0；`Select-String` 无输出。

- [ ] 只提交 manifest：

```powershell
git add -- 'Doc/SlimV2/2026-08-27-paper-branch-extraction-manifest.md'
git diff --cached --name-only
git commit -m 'docs(experiments): freeze extraction manifest'
```

预期：cached name-only 只有 manifest；提交成功。

## 4. Task B：整理来源工作并建立冻结基线

**Files:**

- Modify as already intended: `src/tokenshare/experiments/slim_v2/reducer.py`
- Modify as already intended: `tests/experiments/slim_v2/test_reducer_golden.py`
- Review/commit: `Doc/SlimV2/2026-08-27-reducer-missing-reason-repair-plan.md`
- Review/commit: `paper/**`
- Review and commit without discarding existing intent: `AGENTS.md`, `Doc/SlimV2/README.md`, `Doc/TechnicalDocument/tokenshare_v1_code_map.md`, `progress.md`
- Resolve tracked relocation: `paper.tex`, `paper.bbl`, `reference.bib`
- Create: `Doc/SlimV2/2026-08-27-paper-branch-extraction-baseline.md`
- Create: `Doc/SlimV2/2026-08-27-paper-branch-extraction-baseline.json`

这一任务只把已经存在的合法工作整理成可审计主题提交；不得用 `reset --hard`、`checkout --`、跨工作树移动或临时 stash 掩盖 dirty 状态。

- [ ] 监督者逐项查看 `git status --short` 和每个 dirty path 的 diff，确认归属。相互独立的 reducer 修复、论文迁移、harness 文档必须分成主题提交。

- [ ] reducer writer 完成现有修复，并运行：

```powershell
conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_reducer_golden.py -q
conda run -n tokenshare python -m compileall -q src/tokenshare/experiments/slim_v2/reducer.py
```

预期：pytest 退出码 0（当前已知基线为 40 passed，若数量不同必须解释收集差异）；compileall 退出码 0。

- [ ] 论文迁移 writer 核对当前 HEAD 的根 `paper.tex`、`paper.bbl`、`reference.bib` 与 `paper/` 中目标文件的关系；保留论文源和可复现构建所需文件，排除 `paper/out/paper.synctex.gz` 等生成缓存。任何内容变化必须在论文主题提交中可见，不能伪装成纯移动。

- [ ] harness/docs writer 只在实际状态需要时更新 dirty 文档。`progress.md` 中已有的其他工作必须保留，不允许为了“干净”而抹除用户修改。

- [ ] 每个主题提交前运行 `git diff --cached --name-only`，确认没有夹带其他 dirty path；每个提交后重新执行 `git status --short`。

- [ ] 来源工作树达到 clean 后，记录冻结提交：

```powershell
$frozenSha = git rev-parse HEAD
git status --porcelain=v1
git show --no-patch --format='%H %s' $frozenSha
```

预期：status 无输出；show 返回一个完整 SHA 和最后一个主题提交。

- [ ] 以只读方式生成 baseline JSON。至少记录：`frozen_sha`、正式题库逐文件 SHA-256、case ID 排序集摘要、profile 选择结果摘要、正式结果 12 个文件逐文件 SHA-256、运行目录文件/字节统计、reducer golden 结果、实验 plan JSON 及 provider-call bounds。不得把 API key、响应正文或 7.14 GB raw 文件纳入 Git。

- [ ] baseline 必须确认以下已知事实；若实际值不同，停止并调查，不得更新计划来迎合差异：

  - 正式运行 ID：`slim-v2-full-flash-20260823-233000-b4c8e951`；
  - 非 metrics 文件：`1,088,134`；字节数：`7,143,234,452`；
  - critical-file manifest：`96dc9ea143d7532c0e54482907bb12017247df4286a6656fbdc45f84705fea47`；
  - critical files 数量：`21,260`；metadata fingerprints：`9aa66fec8352982279de3e87fc1eae81`、`d59f593eeb7604374f05efecad4c142d`；
  - `run.json` SHA-256：`3FEE9E896EEAC6BE2A4FA082DCDDDEF4DB92B21742B6234333BC11A30E2E9478`；
  - `metrics/summary.json` SHA-256：`8A7F0D77D146E97D633F14CC4284501B2F4ECB479B70157E2559ECE913B76932`；
  - metrics 表行数：Exp1 `12`、Exp2 `88`、Exp3 `684`、Exp4 `648`、Exp5 `3`；
  - formal metric occurrences：`153`；
  - roots `7017`、references `106`、calls `5889`、responses `2565`、traces `1964`；
  - provider calls：Exp1 `2124`、Exp2 `0`、Exp3 `0`、Exp4 `0`、Exp5 `808`。

- [ ] 运行冻结前 focused baseline：当前实验测试、保留系统测试清单、fake end-to-end、Factorization 多 worker smoke 和一个有界 Lean checker smoke。具体命令与实际 pass 数写入 baseline 文档；任何失败必须先处理，不能靠目标树抽取消失。

- [ ] 提交两份 baseline 文件：

```powershell
git add -- 'Doc/SlimV2/2026-08-27-paper-branch-extraction-baseline.md' 'Doc/SlimV2/2026-08-27-paper-branch-extraction-baseline.json'
git diff --cached --name-only
git commit -m 'docs(experiments): record extraction baseline'
```

- [ ] 创建本地 annotated tag 并验证 peeled commit：

```powershell
$frozenSha = git rev-parse HEAD
git tag -a $freezeTag $frozenSha -m 'Freeze source for paper-branch extraction on 2026-08-27'
$peeledSha = git rev-parse "$freezeTag^{}"
if ($peeledSha -ne $frozenSha) { throw "freeze tag peeled to $peeledSha, expected $frozenSha" }
git cat-file -t $freezeTag
```

预期：peeled SHA 与 frozen SHA 完全一致；对象类型为 `tag`。不得执行 `git push`。

- [ ] 从此刻起来源工作树只读。监督者在每个后续阶段开始前确认其 `HEAD` 仍等于 `$frozenSha` 且 status clean。

## 5. Task C：建立空内容目标分支和公开骨架

**Files:**

- Create target commit with empty tree
- Restore: `.gitattributes`, `.gitignore`, `LICENSE`, `requirements.txt`, `src/tokenshare/__init__.py`
- Create: `README.md`
- Create: `AGENTS.md`
- Create: `REPRODUCIBILITY.md`
- Create: `RESULTS.md`
- Create: `run_experiments.cmd`
- Create: `src/tokenshare/experiments/__init__.py`（先建最小包，完整导出在 Task F 完成）
- Create: `tests/__init__.py`, `tests/experiments/__init__.py`

- [ ] 在来源工作树验证目标不存在；任一检查命中即停止：

```powershell
if (Test-Path -LiteralPath $targetRoot) { throw "target path already exists: $targetRoot" }
git show-ref --verify --quiet "refs/heads/$targetBranch"
if ($LASTEXITCODE -eq 0) { throw "target branch already exists: $targetBranch" }
$frozenSha = git rev-parse "$freezeTag^{}"
```

- [ ] 创建父提交为 frozen SHA 的空树提交，并建立 worktree：

```powershell
$emptyTree = @() | git mktree
$emptyCommit = 'chore: start clean extraction tree' | git commit-tree $emptyTree -p $frozenSha
git branch $targetBranch $emptyCommit
git worktree add -- $targetRoot $targetBranch
```

- [ ] 验证 ancestry 和空内容：

```powershell
git -C $targetRoot rev-parse 'HEAD^'
git -C $targetRoot ls-tree -r --name-only HEAD
git merge-base --is-ancestor $frozenSha $emptyCommit
git -C $targetRoot status --porcelain=v1
```

预期：`HEAD^` 等于 frozen SHA；`ls-tree` 和 status 无输出；ancestor 命令退出码 0。

- [ ] 按 manifest 从 frozen SHA 恢复五个根/包文件：

```powershell
git -C $targetRoot restore --source=$frozenSha --staged --worktree -- '.gitattributes' '.gitignore' 'LICENSE' 'requirements.txt' 'src/tokenshare/__init__.py'
```

- [ ] writer 用 `apply_patch` 创建公开骨架。README 首屏说明仓库包含 protocol system、`experiments`、正式题库、论文和正式 metrics；不得把 `Slim V2` 当产品名。`RESULTS.md` 暂时只记录历史 run ID 和原始归档 pending 状态，不声称已有外部归档。

- [ ] `run_experiments.cmd` 只启动 `python -m tokenshare.experiments.gui`，环境变量前缀使用 `TOKENSHARE_EXPERIMENTS_`；不得保留 `run_slim_v2.cmd`。

- [ ] 运行：

```powershell
git -C $targetRoot diff --check
git -C $targetRoot status --short
git -C $targetRoot add -- '.gitattributes' '.gitignore' 'LICENSE' 'requirements.txt' 'README.md' 'AGENTS.md' 'REPRODUCIBILITY.md' 'RESULTS.md' 'run_experiments.cmd' 'src/tokenshare/__init__.py' 'src/tokenshare/experiments/__init__.py' 'tests/__init__.py' 'tests/experiments/__init__.py'
git -C $targetRoot commit -m 'chore: establish public experiment repository skeleton'
```

预期：diff check 退出码 0；提交后目标 status clean。

## 6. Task D：抽取系统本体并拆分 executor descriptor

**Files:**

- Restore exact paths listed in manifest under `src/tokenshare/core/**`
- Restore exact paths listed in manifest under `src/tokenshare/storage/**`
- Restore exact paths listed in manifest under `src/tokenshare/local_runtime/**`
- Restore: `src/tokenshare/protocol_engine.py`
- Restore selected: `src/tokenshare/plugins/**`
- Restore selected: `src/tokenshare/executors/**`
- Create: `src/tokenshare/executors/descriptors.py`
- Modify: `src/tokenshare/executors/__init__.py`
- Modify: Factorization/Lean adapter files that currently import descriptor from `ai_api.py`
- Restore selected system tests from manifest
- Create/modify: `tests/executors/test_ai_api_descriptor.py`

- [ ] writer 只能使用 manifest 的 exact path 列表执行 `git restore --source=$frozenSha --staged --worktree -- <paths>`；禁止恢复整个 `src/tokenshare/experiments`、整个 `executors/ai_api.py` 或整个旧 tests 树作为捷径。

- [ ] 先写 descriptor 合同测试。测试必须证明：

  - `build_ai_api_executor_descriptor` 可从 `tokenshare.executors.descriptors` 导入；
  - Factorization/Lean adapter 不再 import `tokenshare.executors.ai_api`；
  - `tokenshare.executors` 不公开旧 `AIAPIExecutor`；
  - siliconflow/openai/deepseek 三个 provider family 构造结果与 frozen baseline 相同；非法 family 抛出 `ValueError`。

- [ ] 运行 RED：

```powershell
conda run -n tokenshare python -m pytest tests/executors/test_ai_api_descriptor.py -q
```

预期：因 `descriptors.py` 尚不存在或 adapter 尚未改接而失败；失败原因必须与预期一致。

- [ ] 在 `descriptors.py` 实现唯一所需 descriptor builder。其 descriptor ID/type、两种 request schema、capabilities、environment policy、status 和 metadata 必须与 frozen `ai_api.py` 的函数结果逐字段一致。

- [ ] 清理 `executors/__init__.py` 公开面，只导出目标系统和实验闭包实际使用的合同/registry/transport/config/artifact/descriptor；不得为了兼容旧测试导出旧 executor。

- [ ] 运行 GREEN 和 manifest 指定的系统 focused tests：

```powershell
conda run -n tokenshare python -m pytest tests/executors/test_ai_api_descriptor.py -q
conda run -n tokenshare python -m pytest tests/core tests/storage tests/local_runtime tests/plugins/factorization -q
conda run -n tokenshare python -m compileall -q src/tokenshare
```

预期：全部退出码 0。Lean 相关纯合同测试按 manifest 单独运行；本阶段不运行全量 catalog。

- [ ] 静态检查运行时闭包：

```powershell
Get-ChildItem -LiteralPath "$targetRoot\src" -Recurse -File | Select-String -Encoding UTF8 -Pattern 'executors\.ai_api import|response_bank|paper_formal|trace_backed'
```

预期：无运行时命中；历史说明文件不在此扫描范围内。

- [ ] spec/quality review 通过后提交：

```powershell
git -C $targetRoot add -- 'src/tokenshare' 'tests/core' 'tests/storage' 'tests/local_runtime' 'tests/plugins' 'tests/executors'
git -C $targetRoot diff --cached --name-only
git -C $targetRoot commit -m 'refactor(system): extract protocol runtime and plugin core'
```

## 7. Task E：迁移正式题库、语义权威和 provider 配置

**Files:**

- Move: `benchmarks/paper/factorization_catalog.v2.jsonl` -> `benchmarks/experiments/factorization_catalog.v2.jsonl`
- Move: `benchmarks/paper/lean_catalog.v1.jsonl` -> `benchmarks/experiments/lean_catalog.v1.jsonl`
- Move: `benchmarks/paper/lean_checker_preflight.v1.json` -> `benchmarks/experiments/lean_checker_preflight.v1.json`
- Move: `benchmarks/paper/lean_environment_semantic_authority.v1.json` -> `benchmarks/experiments/lean_environment_semantic_authority.v1.json`
- Move: `benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl` -> `benchmarks/experiments/lean_lemma_graph_catalog.v1.jsonl`
- Move exact tree: `benchmarks/paper/fixtures/lean_proof_project/**` -> `benchmarks/experiments/fixtures/lean_proof_project/**`
- Move: `benchmarks/paper/exp1_baseline_provider_config.v3.json` -> `configs/experiments/exp1_baseline_provider_config.v3.json`
- Move: `benchmarks/paper/exp5_siliconflow_provider_config.v3.json` -> `configs/experiments/exp5_siliconflow_provider_config.v3.json`
- Create: `benchmarks/experiments/manifest.v1.json`
- Create: `tests/experiments/test_authoritative_corpus.py`
- Modify: retained Lean environment/semantic-authority path constants
- Create: `Doc/Experiments/README.md`
- Create: `Doc/Experiments/design.md`
- Create: `Doc/Experiments/metrics.md`
- Create: `Doc/Experiments/system-integration.md`
- Create: `Doc/Experiments/corpus-manifest.md`

- [ ] 从 frozen SHA 恢复 manifest 列出的 corpus/config 原文件，再用 `git mv` 放入公开路径。除了路径字段确实需要更新的配置，不得格式化或重写 JSON/JSONL；原始内容 SHA 必须先记录再比较。

- [ ] 创建 corpus manifest，逐文件记录 frozen path、public path、SHA-256、记录数和职责。对发生必要路径字段变化的配置，同时记录 source SHA、target SHA 和逐字段差异；不得将这种文件声称为 byte-identical。

- [ ] 更新 Lean plugin 的 `environment.py`、`semantic_authority.py` 及 manifest 明确列出的 path consumers，使默认路径指向 `benchmarks/experiments`。只允许路径变化，不改变 checker 语义。

- [ ] 编写 corpus 测试，比较 baseline JSON 与目标：完整 case ID 排序集、Factorization/Lean 记录数、profile 选择 ID/摘要、所有内容未变文件的 SHA-256、Lean project 相对路径与文件 SHA。正式 case 集不能用抽样检查代替。

- [ ] 从已批准的 Slim V2 权威文档综合生成 `Doc/Experiments/` 五份公开文档。去掉工程代号和过程状态，但不得改动实验条件、指标口径、provider reuse 事实或系统边界。`metrics.md` 明确 Exp2–4 复用 Exp1 普通真实回答。

- [ ] 运行：

```powershell
conda run -n tokenshare python -m pytest tests/experiments/test_authoritative_corpus.py -q
conda run -n tokenshare python -m pytest tests/plugins/lean_proof/test_lean_environment.py tests/plugins/lean_proof/test_lean_fixture_project_manifest.py tests/plugins/lean_proof/test_lean_preflight.py -q
git -C $targetRoot diff --check
```

预期：全部退出码 0；题库测试证明完整 case/selection/SHA 对等，不只是文件存在。

- [ ] spec reviewer 逐条对照原权威文档核验科学口径；quality reviewer 检查是否把过程计划或旧 runner 说明带入公开文档。

- [ ] 提交：

```powershell
git -C $targetRoot add -- 'benchmarks/experiments' 'configs/experiments' 'src/tokenshare/plugins' 'tests/experiments/test_authoritative_corpus.py' 'tests/plugins/lean_proof' 'Doc/Experiments'
git -C $targetRoot commit -m 'data(experiments): preserve authoritative corpora and configs'
```

## 8. Task F：抽取并公开当前实验设施

**Files:**

- Move all exact files: `src/tokenshare/experiments/slim_v2/*.py` -> `src/tokenshare/experiments/*.py`
- Move all exact files: `tests/experiments/slim_v2/**` -> `tests/experiments/**`
- Modify: `src/tokenshare/experiments/__init__.py`
- Modify all moved experiment modules and tests
- Modify: `run_experiments.cmd`
- Modify: `README.md`, `REPRODUCIBILITY.md`, `Doc/Experiments/**`

- [ ] 从 frozen SHA 只恢复 manifest 中列出的 14 个当前实验源码和对应 tests/fixtures，然后在同一个未提交工作区中直接移动到公开路径；历史上不需要产生一个含旧嵌套路径的目标提交。

- [ ] 先修改测试 import 和公开符号期望，覆盖：schema/config、profiles、case source、execution、runtime/resume、projector、scenarios、provider、storage、CLI plan/e2e、GUI launcher、reducer golden、system vertical 和 answer paths。

- [ ] 公共 Python 名称按职责重命名：

  - `SlimRunConfigV1` -> `ExperimentRunConfigV1`；
  - `SlimLeanExecutionBridge` -> `ExperimentLeanExecutionBridge`；
  - `SLIM_V2_SCHEMA_VERSION` -> `EXPERIMENT_SCHEMA_VERSION`；
  - 其他公开 `Slim*`/`SLIM_*` 符号由 writer 在 manifest 中逐项映射。

  被序列化的历史字符串值（例如 `tokenshare.slim_v2.*`、`slim_v2.pricing.*`、正式 run ID）不得随 Python 名称一起改写。

- [ ] 将默认新输出根改为 `TokenShareData/outputs/experiments`；配置和题库默认路径改为 Task E 的公开路径。不得移动、重命名或写入现有 `TokenShareData/outputs/slim_v2/slim-v2-full-flash-20260823-233000-b4c8e951/`。

- [ ] 不创建 `src/tokenshare/experiments/slim_v2/`、module alias、import hook 或兼容 wrapper。若测试仍依赖旧 import，应修改测试/调用者，而不是恢复旧包。

- [ ] 运行 experiment focused suite：

```powershell
conda run -n tokenshare python -m pytest tests/experiments -q
conda run -n tokenshare python -m compileall -q src/tokenshare/experiments
conda run -n tokenshare python -m tokenshare.experiments.cli plan --profile full --format json
```

预期：pytest 与 compileall 退出码 0；plan JSON 的 case 集、选择摘要和 provider-call bounds 与 baseline JSON 逐字段一致，公开路径只出现在路径字段中。

- [ ] 运行无真实 provider 的 fake vertical 和 resume 测试；网络 tripwire 必须观察到 0 个外联调用。若命令尚未由现有测试提供，writer 在 `verification/run_verification.py` 中增加明确的 `--focused experiments` 入口，而不是新建平行 runner。

- [ ] 扫描运行时公开面：

```powershell
Get-ChildItem -LiteralPath "$targetRoot\src" -Recurse -File | Select-String -Encoding UTF8 -Pattern 'tokenshare\.experiments\.slim_v2|from \.slim_v2|import \.slim_v2'
Get-ChildItem -LiteralPath "$targetRoot\tests" -Recurse -File | Select-String -Encoding UTF8 -Pattern 'tokenshare\.experiments\.slim_v2'
```

预期：均无输出。对 `tokenshare.slim_v2.*` 等持久化字符串另做 allowlist 检查，不得用全局替换消除。

- [ ] spec reviewer 比较 frozen 与 target 的 plan、fake pipeline artifacts、reducer golden 和 resume 行为；quality reviewer 检查包结构、公共命名和旧设施残留。

- [ ] 提交：

```powershell
git -C $targetRoot add -- 'src/tokenshare/experiments' 'tests/experiments' 'run_experiments.cmd' 'README.md' 'REPRODUCIBILITY.md' 'Doc/Experiments' 'verification'
git -C $targetRoot commit -m 'refactor(experiments): publish the current experiment facility'
```

## 9. Task G：保留论文、正式结果和最小验证 harness

**Files:**

- Restore exact approved set: `paper/AGENTS.md`
- Restore exact approved set: `paper/DETAIL_REQUESTS.md`
- Restore exact approved set: `paper/IMPLEMENTATION_DETAILS.md`
- Restore exact approved set: `paper/LEAN_FOUR_NODE_DAG_EXAMPLES.md`
- Restore exact approved set: `paper/PAPER_TERMINOLOGY_MAPPING.md`
- Restore exact approved set: `paper/PAPER_WRITING_BRIEF.md`
- Restore exact approved set: `paper/paper.tex`
- Restore exact approved set: `paper/PROFESSOR_REVISION_GUIDANCE.md`
- Restore exact approved set: `paper/reference.bib`
- Restore `paper/paper.bbl` only if Task B proves it is an intentional reproducibility input
- Create: `results/experiments/slim-v2-full-flash-20260823-233000-b4c8e951/run.json`
- Create: `results/experiments/slim-v2-full-flash-20260823-233000-b4c8e951/metrics/summary.json`
- Create exact 10 table files under: `results/experiments/slim-v2-full-flash-20260823-233000-b4c8e951/metrics/tables/`
- Create: `results/experiments/slim-v2-full-flash-20260823-233000-b4c8e951/manifest.json`
- Create: `verification/verify_official_results.py`
- Create: `verification/verify_extraction.py`
- Create/modify: `verification/run_verification.py`
- Create/modify: `verification/fast-tests.txt`
- Restore/create: `verification/pytest_network_tripwire.py`, `verification/sitecustomize.py`
- Create: `Doc/Experiments/code-map.md`
- Modify: `README.md`, `RESULTS.md`, `REPRODUCIBILITY.md`, `AGENTS.md`

- [ ] 从 frozen SHA 抽取 manifest 列出的论文文件。排除 `paper/out/**`、synctex、日志和其他构建缓存。运行论文引用/路径静态检查；若公开包路径在论文正文中被当作当前接口，应改为 `tokenshare.experiments`，但历史实验名、run ID 和 schema 字符串保持不变。

- [ ] 从 ignored 正式 raw 目录只复制以下 12 个文件到 Git 结果路径：`run.json`、`metrics/summary.json`、Exp1–5 的 CSV/JSONL 共 10 个表。复制前后逐文件 SHA-256 与 baseline JSON 比较；任一不同立即删除目标副本并停止调查，不得运行 reducer 覆盖来源或目标。

- [ ] `manifest.json` 记录 12 个文件的相对路径、SHA-256、字节数、表行数、frozen SHA、历史 run ID、raw archive 状态。`RESULTS.md` 解释目录名中的 `slim-v2` 是冻结运行的历史内部标识，不是公开设施名称。

- [ ] 实现 `verification/verify_official_results.py`：

  1. 读取 Git 结果目录和 ignored raw 目录；
  2. 比较 12 个已发布文件的 SHA-256；
  3. monkeypatch reducer 私有 `_stage_payloads`，只在内存中捕获 `_reduce_run_staged(run_dir, {})` 生成的 11 个 metrics payload；
  4. 将捕获字节与 Git 中 11 个 metrics 文件逐字节比较；
  5. 禁止调用会写回正式目录的 `reduce_run()`；
  6. 若 raw 目录在将来的外部 checkout 中不存在，默认只验证 Git manifest；只有显式 `--require-raw` 才因缺失 raw 失败。

- [ ] 实现 `verification/verify_extraction.py`：验证来源/目标 ancestry、题库 manifest、禁止路径、运行时 import、结果 manifest、历史字符串 allowlist 和 Git tracked 文件边界。它不能把绝对 source worktree 写入目标配置或运行时。

- [ ] 最小 harness 只包含本计划实际需要的 focused/fast verification。不得抽取旧 Rxx、LeanAudit、paper/formal pipeline profile 作为“保险”。

- [ ] 运行：

```powershell
conda run -n tokenshare python verification/verify_official_results.py --raw-root 'E:\TokenEcnomic\TokenShareWorktrees\slim-v2-baseline\TokenShareData\outputs\slim_v2\slim-v2-full-flash-20260823-233000-b4c8e951' --require-raw
conda run -n tokenshare python verification/verify_extraction.py --frozen-sha $frozenSha
git ls-files -- 'paper/out/**' '*.synctex.gz' '*.aux' '*.log'
```

预期：前两条退出码 0，并报告 12 个 result hashes、11 个 reducer payloads、完整 corpus 对等；最后一条无输出，证明没有跟踪论文构建缓存。

- [ ] spec reviewer 核对论文/结果/公开文档；quality reviewer 特别检查是否误跟踪 raw artifacts、secret、LaTeX cache 或 source worktree 绝对路径。

- [ ] 提交：

```powershell
git -C $targetRoot add -- 'paper' 'results/experiments' 'verification' 'Doc/Experiments/code-map.md' 'README.md' 'RESULTS.md' 'REPRODUCIBILITY.md' 'AGENTS.md'
git -C $targetRoot diff --cached --name-only
git -C $targetRoot commit -m 'docs(paper): preserve paper workspace and formal results'
```

## 10. Task H：干净检出验证与三路最终评审

**Files:**

- Verify all tracked target files
- Modify only files required by accepted reviewer findings

- [ ] 确认目标 status clean，并创建一个显式、可验证、只用于验收的 detached worktree；不得在来源工作树或目标 writer 工作树中伪装 clean-room：

```powershell
$reviewRoot = 'E:\TokenEcnomic\TokenShareWorktrees\experiments-clean-review'
if (Test-Path -LiteralPath $reviewRoot) { throw "review path already exists: $reviewRoot" }
$targetSha = git -C $targetRoot rev-parse HEAD
git worktree add --detach -- $reviewRoot $targetSha
git -C $reviewRoot status --porcelain=v1
```

预期：review status 无输出。

- [ ] 在 review worktree 创建独立环境或使用现有 `tokenshare` conda 环境，但显式将 `PYTHONPATH` 设为 `$reviewRoot\src`，并运行 Python 断言 `sys.path` 不包含 `$sourceRoot` 或 `$targetRoot`。不得从另一个 worktree 借用源码。

- [ ] 运行 risk-driven final gate：

```powershell
conda run -n tokenshare python -m compileall -q "$reviewRoot\src" "$reviewRoot\tests" "$reviewRoot\verification"
conda run -n tokenshare python "$reviewRoot\verification\run_verification.py" --focused system
conda run -n tokenshare python "$reviewRoot\verification\run_verification.py" --focused experiments
conda run -n tokenshare python "$reviewRoot\verification\verify_official_results.py"
conda run -n tokenshare python "$reviewRoot\verification\verify_extraction.py" --frozen-sha $frozenSha
```

预期：全部退出码 0；system gate 包含 protocol/store/replay、Factorization 多 worker；experiments gate 包含 corpus、plan、fake vertical、resume、reducer golden、网络 tripwire 0 calls；official result gate 在没有 raw 时完成 Git manifest 验证。

- [ ] 单独运行一次有界真实本地 Lean checker smoke，固定使用正式题库中的一个已知小样本、固定本地 toolchain、超时和临时 artifact 目录；记录命令、case ID、checker 退出码和 artifact hash。不得扩展为全量 Lean catalog。

- [ ] 运行 runtime absence scans，范围只包括 `src/`、`tests/`、`verification/` 和 launcher：

```powershell
$runtimePaths = @("$reviewRoot\src", "$reviewRoot\tests", "$reviewRoot\verification", "$reviewRoot\run_experiments.cmd")
Get-ChildItem -LiteralPath $runtimePaths -Recurse -File | Select-String -Encoding UTF8 -Pattern 'paper_formal|response_bank|trace_backed|publication_gate|paper_eligibility|tokenshare\.experiments\.slim_v2'
```

预期：无输出。对 `receipt`、`budget`、`lineage` 等可能属于系统通用协议词的检查，必须按 manifest 的 symbol/path 禁止清单而不是粗暴词频删除。

- [ ] 并行派发三个 final read-only reviewer：

  - 系统/依赖 reviewer：检查 retained closure、旧 runner absence、无跨 worktree 依赖；
  - 科学资产 reviewer：检查完整题库、case selection、正式结果和 metrics authority；
  - 外部读者 reviewer：从 README 开始走一遍，检查公开命名、唯一入口、复现实用性和文档是否仍让人误以为有多套设施。

- [ ] 每个 reviewer 只报告有文件/行/命令证据的 finding，并按 blocker/high/medium/low 分类。根监督者逐条决定；accepted finding 交回原阶段 writer 修复并运行相应 focused verification。

- [ ] 若有修正，创建职责明确的小提交，不得 amend 已评审阶段提交。修正后重新创建 review worktree 或将其安全更新到新 target SHA，再重跑受影响 gate 和全部 manifest/hash gate。

- [ ] 验收完成后核对 reviewRoot 的解析绝对路径确实等于预期路径，再移除 detached review worktree：

```powershell
$resolvedReviewRoot = (Resolve-Path -LiteralPath $reviewRoot).Path
if ($resolvedReviewRoot -ne $reviewRoot) { throw "unexpected review path: $resolvedReviewRoot" }
git worktree remove -- $reviewRoot
```

预期：worktree remove 成功；目标 writer worktree保留，来源 worktree未改变。

## 11. Task I：将论文分支 fast-forward 到清洗结果

**Files:** 无内容编辑；仅 Git ref 前移。

- [ ] 在来源工作树最后一次验证：

```powershell
git -C $sourceRoot status --porcelain=v1
$sourceHead = git -C $sourceRoot rev-parse HEAD
$frozenSha = git -C $sourceRoot rev-parse "$freezeTag^{}"
$targetSha = git -C $targetRoot rev-parse HEAD
if ($sourceHead -ne $frozenSha) { throw "source moved after freeze" }
git merge-base --is-ancestor $sourceHead $targetSha
```

预期：status 无输出；source HEAD 等于 frozen SHA；ancestor 命令退出码 0。

- [ ] 保存最终 target evidence：target SHA、提交列表、验证命令结果、题库/result manifests、final reviewer findings 与处置。证据写入公开 `REPRODUCIBILITY.md` 或 `Doc/Experiments/code-map.md` 时，不得包含本机绝对路径；本机路径只留在监督会话记录。

- [ ] 在来源工作树执行唯一允许的集成动作：

```powershell
git -C $sourceRoot merge --ff-only $targetBranch
```

预期：fast-forward 成功，没有 merge commit。

- [ ] 验证两个工作树解析到同一 commit，来源分支内容与 target tree 完全相同：

```powershell
$finalSourceSha = git -C $sourceRoot rev-parse HEAD
$finalTargetSha = git -C $targetRoot rev-parse HEAD
if ($finalSourceSha -ne $finalTargetSha) { throw 'source and target SHA differ' }
git -C $sourceRoot diff --exit-code $finalTargetSha
git -C $sourceRoot status --porcelain=v1
```

预期：SHA 相同；diff 退出码 0；status 无输出。

- [ ] 不 push、不发布 freeze tag。向用户报告本地最终 SHA、冻结 tag peeled SHA、保留结果 manifest SHA、验证摘要和仍为 pending 的 raw archive 决策，请用户单独批准远端发布。

## 12. 完成判据

只有同时满足以下条件，监督者才可声称清洗完成：

- 当前论文分支已通过 `--ff-only` 到目标 SHA，且来源/目标 worktree status clean；
- `src/tokenshare/experiments/` 是唯一实验设施包，源码/测试/harness 不含旧 runner 或兼容 wrapper；
- 系统本体、Factorization、Lean plugin/checker、AI transport/config/artifacts 的最小闭包通过 focused verification；
- 正式题库完整 case 集、选择结果和 SHA-256 与 baseline 完全一致；
- 正式 `run.json` 与 11 个 metrics 文件逐字节匹配 baseline，reducer 内存重算的 11 个 payload 也逐字节匹配；
- provider/network 调用计数为 0，正式 raw 目录没有写入；
- clean-room checkout 完成 compile、system、experiments、result、Factorization 多 worker 和单个 Lean checker smoke；
- 三类 final reviewer 无未处置 blocker/high finding；
- README 只给外部读者一个 `experiments` 入口，历史 `slim-v2` 字符串只存在于 allowlist 规定的持久化事实和说明中；
- 本地 freeze tag 解析到冻结来源 SHA；未发生未授权 push、tag 发布、远端 release 或历史重写；
- 原始全量归档仍明确标记为 `pending_advisor_archive_decision`，没有虚构外部 URL。
