# Experiments 抽取式清洗 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** `approved_for_execution`

**Goal:** 从冻结的实验来源提交建立一条具有正常祖先关系的干净抽取分支，只保留系统本体、正式题库、当前实验设施、权威文档和正式全量结果；将当前实验设施的公开名称统一为 `experiments`，并用可复核证据证明系统、实验设施和正式结果在清洗后仍然有效。

**Architecture:** 来源分支只冻结本次清洗需要的非 `paper/**` 内容，目标分支从“父提交为冻结来源、内容为空”的提交开始，按系统本体、题库与配置、实验设施、正式结果与验证 harness 的顺序构造。冻结后每一阶段只允许读取 annotated tag 的 peeled commit；若验证暴露缺失依赖，必须先提交最小依赖申请，再从同一 peeled commit 补取。来源分支 `codex/slim-v2-baseline` 保留给用户继续工作，不移动到目标 SHA；最终只发布干净目标分支和冻结 tag。

**Tech Stack:** Git worktree、PowerShell、Python 3、pytest、SQLite/JSON/JSONL、本地 Lean checker、SHA-256 清单、Codex 监督智能体与阶段化子智能体。

---

## 0. 已批准的不变量

实现人员不得在执行中重新解释以下决定：

- 公开设施名称和包路径为 `experiments`；最终运行时代码不得存在 `tokenshare.experiments.slim_v2` 兼容层。
- 已冻结结果中的 run ID、schema 字符串值、pricing/version 字符串值、指标字段和历史路径记录保持逐字不变；它们是历史事实，不属于公开命名残留。
- 正式题库完整保留。迁移前后 case 集、选择结果和 SHA-256 必须完全一致。
- 顶层 `paper/**` 完全排除于本次清洗：不读取、不复制、不验证、不审查，也不因其持续变化阻塞。`benchmarks/paper/**` 是历史路径下的正式题库和配置来源，不属于该排除项，必须按 manifest 完整抽取到公开路径。
- 正式全量结果的 `run.json` 和 11 个 metrics 文件进入 Git；约 7.14 GB 的原始运行目录继续保存在本地 ignored `TokenShareData/`，外部归档状态保持 `pending_advisor_archive_decision`。
- 清洗期间禁止调用真实 provider，禁止重跑 Representative/Full，禁止覆盖正式运行目录的任何文件。
- 不执行全量 LeanAudit 或全 Lean catalog 回归；最终只运行一个有界真实本地 Lean checker smoke，其余接线验证优先使用 fake、固定 fixture 和静态合同。
- 用户已批准最终发布 annotated tag 与干净目标分支；只允许显式发布 `experiments-pre-cleanup-20260827` 和 `codex/experiments-clean-extraction`，不得 push 来源分支、强推、创建远端 release 或改写历史。
- 目标分支暂定为 `codex/experiments-clean-extraction`；目标工作树暂定为 `E:\TokenEcnomic\TokenShareWorktrees\experiments-clean-extraction`。执行前必须再次证明二者均不存在，存在时停止而不是复用或覆盖。

## 1. 监督与交接协议

根智能体是唯一监督者，负责阶段授权、工作树状态核验、提交验收和最终发布。实施使用 `superpowers:subagent-driven-development`，但并行只用于彼此独立的只读审计或最终只读评审；所有写入阶段顺序执行。

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

function Assert-RepoPythonImport {
    param([Parameter(Mandatory = $true)][string]$RepoRoot)
    $resolvedRepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
    $expectedPackageRoot = (Resolve-Path -LiteralPath (Join-Path $resolvedRepoRoot 'src\tokenshare')).Path
    $env:PYTHONPATH = (Join-Path $resolvedRepoRoot 'src')
    conda run -n tokenshare python -c "from pathlib import Path; import tokenshare; actual=Path(tokenshare.__file__).resolve(); expected=Path(r'$expectedPackageRoot').resolve(); assert actual.parent == expected, f'wrong tokenshare import: {actual} != {expected}'"
    if ($LASTEXITCODE -ne 0) { throw "tokenshare import escaped repository root: $resolvedRepoRoot" }
}
```

除带有明确 `git -C` 或绝对脚本路径的命令外，Task D–G 的相对路径命令必须在 `Push-Location $targetRoot` 与 `Pop-Location` 之间执行；Task H 的验证命令必须在 `Push-Location $reviewRoot` 与 `Pop-Location` 之间执行。这样 Python 的 repository root、fixture 相对路径和 import 根都来自被验证的工作树，而不是来源工作树。

已完成的计划/manifest/raw-freeze 历史与待执行阶段如下；不 squash、不 rebase，也不伪造 manifest-only 当前动作：

1. Manifest 初始冻结提交：`bbbfd6587e9a234e7371a62d60c0d83c2f524c3c`，后续 manifest-only 加固提交如实保留在 history；
2. 两文档联合范围修订：`04b574ef58f07fd12479703a1ff8b041da88ff62`；
3. 两文档联合 freeze-contract 修订：`ccaa7f3a6a0a99b5f064db4448a39400e5002190`；本次 approved execution 修订同样作为两文档联合提交，commit SHA 只能在提交后由验收记录，不能在文档内自引用；
4. Source raw-byte freeze：`342971a7247ffb88dc599dd436e34e3ed10403cd`（已完成）；
5. 下一执行起点：Task B 只读复核 20 个 source authoritative blobs、生成 baseline、提交 baseline、创建 annotated tag；
6. 待执行：`docs(experiments): record extraction baseline`；
7. 待执行：`chore: start clean extraction tree`；
8. 待执行：`chore: establish public experiment repository skeleton`；
9. 待执行：`refactor(system): extract protocol runtime and plugin core`；
10. 待执行：`data(experiments): preserve authoritative corpora and configs`；
11. 待执行：`refactor(experiments): publish the current experiment facility`；
12. 待执行：`data(experiments): preserve formal results and verification harness`；
13. 如有最终评审修正，按职责追加一个或多个小提交。

## 3. Task A：并行只读审计并冻结抽取清单（completed）

**Completion state:** 当前 HEAD 已包含已冻结 manifest 及两文档联合修订；本 Task 不再创建或提交任何文件。下一执行起点是 Task B 的 source raw-byte 复核、baseline 与 annotated tag。

**Files:**

- Frozen/current: `Doc/SlimV2/2026-08-27-experiments-clean-extraction-manifest.md`
- Read only: `src/tokenshare/**`
- Read only: `tests/**`
- Read only: `benchmarks/**`
- Read only: `configs/**`
- Read only: `verification/**`
- Read only: `Doc/SlimV2/**`
- Read only: `TokenShareData/outputs/slim_v2/slim-v2-full-flash-20260823-233000-b4c8e951/run.json`
- Read only: 同一正式运行目录下 `metrics/**`

- [x] 监督者记录 audit HEAD，并确认除 manifest writer 外的三个审计智能体全部只读。

- [x] 并行依赖审计已从当前 14 个 `src/tokenshare/experiments/slim_v2/*.py` 入口完成，保留/排除路径已有 import/call-site 证据。

- [x] 题库/结果审计已输出正式题库、配置、case ID、profile selection、正式结果 bytes/SHA；审计过程未写正式 raw。

- [x] 文档/harness 审计已输出公开实验文档与最小验证边界；顶层 `paper/**` 未进入目标范围。

- [x] Consolidation writer 已创建 manifest，并逐路径冻结 `keep_exact`、`move_exact`、`synthesize`、`exclude_exact`、`historical_string_allowlist`、`verification_commands`。

- [x] Manifest 已落实以下边界；后续只能收紧或提交最小依赖申请，不能无证据扩张：

  - 保留 `src/tokenshare/core/**`、`storage/**`、`local_runtime/**`、`protocol_engine.py`；
  - 保留 factorization 与 Lean plugin/checker 的运行闭包，但排除只服务旧 evidence pipeline 的 `src/tokenshare/plugins/lean_proof/replay_evidence.py`；
  - 保留 executor contracts、registry、deterministic、mock、AI transport/config/artifacts；排除旧 `AIAPIExecutor`、selector、request identity、response bank、trace-backed 和旧 replay；
  - 从 `src/tokenshare/executors/ai_api.py` 仅抽出 descriptor 构造职责到新文件，不复制巨型模块；
  - 当前实验设施 14 个源码文件和 `tests/experiments/slim_v2/**` 迁移到无 `slim_v2` 层级的新路径；
  - 正式题库与 Lean checker 语义权威文件完整迁移；
  - 不抽取 `feature_list.json`、`session-handoff.md`、Rxx 历史状态、旧 paper/formal runners、tracked `local/` 过程产物和旧结果目录。

- [x] 监督者已检查每个 `move_exact` 的唯一 source/target，以及每个 synthesized 文件的职责、输入与验证方法。

- [x] 已运行文档自检并通过：

```powershell
git diff --check -- 'Doc/SlimV2/2026-08-27-experiments-clean-extraction-manifest.md'
$forbiddenTerms = @(('TO' + 'DO'), ('T' + 'BD'), ('FIX' + 'ME'), (([char]31867) + ([char]20284) + ([char]25991) + ([char]20214)), (([char]30456) + ([char]20851) + ([char]27979) + ([char]35797))) -join '|'
Select-String -Path 'Doc/SlimV2/2026-08-27-experiments-clean-extraction-manifest.md' -Encoding UTF8 -Pattern $forbiddenTerms
```

预期：`git diff --check` 退出码 0；`Select-String` 无输出。

Manifest 初始提交为 `bbbfd6587e9a234e7371a62d60c0d83c2f524c3c`；随后 manifest-only 加固、`04b574ef58f07fd12479703a1ff8b041da88ff62` 与 `ccaa7f3a6a0a99b5f064db4448a39400e5002190` 两次联合修订均保留在真实 history。不得为“完成 Task A”再制造无内容提交。

## 4. Task B：整理来源工作并建立冻结基线

**Files:**

- Completed source-owner commit: `.gitattributes` + manifest 第 3.4 节 20 个 exact authoritative paths，commit `342971a7247ffb88dc599dd436e34e3ed10403cd`
- Verify only: 7 个 `benchmarks/paper/` catalog/config/semantic-sidecar exact paths
- Verify only: 13 个 `fixtures/lean_proof_project/` exact tracked paths（排除 `.lake/**`）
- Completed source topic: `src/tokenshare/experiments/slim_v2/reducer.py`, `tests/experiments/slim_v2/test_reducer_golden.py` 与修复计划，commit `c5df94f2f6f9315beb4af923172b619dfc98b7e7`
- Completed source docs/harness topics: `AGENTS.md`, `Doc/SlimV2/README.md`, `Doc/TechnicalDocument/tokenshare_v1_code_map.md`, `progress.md`
- Create: `Doc/SlimV2/2026-08-27-experiments-clean-extraction-baseline.md`
- Create: `Doc/SlimV2/2026-08-27-experiments-clean-extraction-baseline.json`

当前 Task B 起点已经越过 source raw-byte 写入：`342971a7247ffb88dc599dd436e34e3ed10403cd` 已完成 `.gitattributes` 与 20 个权威文件的 raw-byte freeze。后续不得重复修改、renormalize 或重新提交这些文件；唯一 source writer 是根 supervisor 指定的 baseline writer，其他 agent 对 source 只读。当前动作是复核既成 raw tree、生成 baseline、提交 baseline、创建 annotated tag；不得用 `reset --hard`、`checkout --`、跨工作树移动或临时 stash 掩盖 dirty 状态。

- [ ] Supervisor 先验证 raw-freeze commit 是当前 HEAD ancestor，再按 manifest 第 3.4 节固定 20-path SHA map 重新运行完整 working/index/HEAD blob gate：20 个 working-file SHA、20 个 index `git cat-file blob` bytes、20 个 `HEAD:path` blob bytes必须逐项相等；`git check-attr text eol` 与 `git check-attr --cached text eol` 对全部路径都必须返回 `text: unset`、`eol: unspecified`。该步骤只读，不得再次编辑 `.gitattributes` 或 20 个文件。

```powershell
$rawFreezeCommit = '342971a7247ffb88dc599dd436e34e3ed10403cd'
git merge-base --is-ancestor $rawFreezeCommit HEAD
if ($LASTEXITCODE -ne 0) { throw 'raw-byte freeze commit is not an ancestor of current HEAD' }
```

随后原样运行 manifest 第 3.4 节已经冻结的 `$rawPaths`、SHA map、binary `git cat-file blob` Python body，以及同节 working/`--cached` attribute gate；该 manifest block 是唯一 exact command body，不在本计划复制第二份可能漂移的 20-path map。预期：20/20 working/index/HEAD SHA 相同，20/20 working/cached attributes 精确为 `-text`；没有 source 写入或新提交。

- [ ] 监督者只查看本次范围内路径的状态和 diff，确认 reducer 修复与 harness 文档分别形成主题提交。顶层 `paper/**` 不读取、不暂存、不恢复；其工作区变化保持原样。

- [x] Reducer source topic 已完成并运行：

```powershell
conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_reducer_golden.py -q
conda run -n tokenshare python -m compileall -q src/tokenshare/experiments/slim_v2/reducer.py
```

预期：pytest 退出码 0（当前已知基线为 40 passed，若数量不同必须解释收集差异）；compileall 退出码 0。

- [x] Harness/docs source topics 已按真实意图提交，`progress.md` 的其他工作未被抹除。

- [x] 各已完成 source topic 在提交前核验 cached paths；当前非顶层 `paper/**` 的 tracked/untracked 工作树为空。后续提交继续只用 `git diff --quiet -- . ':(exclude)paper/**'` 与 `git ls-files --others --exclude-standard -- . ':(exclude)paper/**'` 复核，不运行会枚举顶层排除项的裸 status 命令。

- [ ] 冻结前只要求来源 index 为空且顶层 `paper/**` 之外的工作树干净。用 pathspec 排除顶层 `paper/**`，不得为了通过 guard 读取、提交、暂存、恢复或移动其中内容：

```powershell
$sourceCached = @(git diff --cached --name-only)
if ($LASTEXITCODE -ne 0 -or $sourceCached.Count -ne 0) { throw 'source index must be empty before freeze' }
git diff --quiet -- . ':(exclude)paper/**'
if ($LASTEXITCODE -eq 1) { throw 'tracked non-paper worktree changes remain' }
if ($LASTEXITCODE -ne 0) { throw "cannot inspect tracked non-paper status: exit $LASTEXITCODE" }
$sourceUntracked = @(git ls-files --others --exclude-standard -- . ':(exclude)paper/**')
if ($LASTEXITCODE -ne 0 -or $sourceUntracked.Count -ne 0) { throw 'untracked non-paper worktree changes remain' }
$preBaselineSourceSha = git rev-parse HEAD
git show --no-patch --format='%H %s' $preBaselineSourceSha
```

预期：index 为空、非 `paper/**` tracked/untracked status 为空；show 返回 `pre_baseline_source_sha` 及最后一个来源主题提交。顶层 `paper/**` 可以继续变化。

- [ ] 以只读方式生成 baseline JSON。至少记录：`pre_baseline_source_sha=$preBaselineSourceSha`、正式题库逐文件 SHA-256、case ID 排序集摘要、profile 选择结果摘要、正式结果 12 个文件逐文件 SHA-256、运行目录文件/字节统计、reducer golden 结果、实验 plan 输出及 provider-call bounds，以及 manifest 第 4 节定义的完整 root/reference identity 自描述对象。Baseline 文档不能记录包含自身的 `frozen_sha`，也不能预言随后 tag 的 object/peeled SHA；不得把 API key、响应正文或 7.14 GB raw 文件纳入 Git。

- [ ] baseline 必须确认以下已知事实；若实际值不同，停止并调查，不得更新计划来迎合差异：

  - 正式运行 ID：`slim-v2-full-flash-20260823-233000-b4c8e951`；
  - 非 metrics 文件：`1,088,134`；字节数：`7,143,234,452`；
  - critical-file manifest：`96dc9ea143d7532c0e54482907bb12017247df4286a6656fbdc45f84705fea47`；
  - critical files 数量：`21,260`；critical-file manifest SHA-256：`96dc9ea143d7532c0e54482907bb12017247df4286a6656fbdc45f84705fea47`；
  - `run.json` SHA-256：`3FEE9E896EEAC6BE2A4FA082DCDDDEF4DB92B21742B6234333BC11A30E2E9478`；
  - `metrics/summary.json` SHA-256：`8A7F0D77D146E97D633F14CC4284501B2F4ECB479B70157E2559ECE913B76932`；
  - metrics 表行数：Exp1 `12`、Exp2 `88`、Exp3 `684`、Exp4 `648`、Exp5 `3`；
  - formal metric occurrences：`153`；
  - roots `7017`、references `106`、calls `5889`、responses `2565`、traces `1964`；
  - provider calls：Exp1 `2124`、Exp2 `0`、Exp3 `0`、Exp4 `0`、Exp5 `808`。
  - `full_root_identities`：count `7017`、canonical items bytes `1,448,798`、items SHA-256 `ec5ca012faffc148be5eff65d43394b2d7861c666468e59841c716c88dea7c07`；
  - `full_reference_identities`：count `106`、canonical items bytes `22,055`、items SHA-256 `b537bd614ac9c86f2f864afb0e6a7f3c1fe22e38d772784c1fc8c0c914f43cdb`。

  `9aa66fec8352982279de3e87fc1eae81` 与 `d59f593eeb7604374f05efecad4c142d` 只能以 `historical_pre_post_audit_evidence` 记录：生成算法没有持久化，且可能依赖 Git 不保证的 mtime，因此不得重算或作为 target gate。旧 root/reference hashes `b6ae545102c57f73b1b6c7641b630944d024ba0e757b637a3253b26c6c68bdb7` 与 `9e67170af1c15400c50fc5c8d79ce29695838a4aef1d9d6b909410a10323c351` 只能进入 `historical_unreproducible_hashes`，不得作为 gate。

- [ ] Source CLI 计划命令固定为：

```powershell
conda run -n tokenshare python -m tokenshare.experiments.slim_v2.cli plan --profile full --run-id extraction-baseline-plan
```

预期：退出码 0，输出/落盘 plan 可被 baseline reader 解析；不得增加当前 source CLI 未实现的格式化参数。

- [ ] 运行冻结前 focused baseline：当前实验测试、保留系统测试清单、fake end-to-end、Factorization 多 worker smoke 和一个有界 Lean checker smoke。具体命令与实际 pass 数写入 baseline 文档；任何失败必须先处理，不能靠目标树抽取消失。

- [ ] 提交两份 baseline 文件：

```powershell
git add -- 'Doc/SlimV2/2026-08-27-experiments-clean-extraction-baseline.md' 'Doc/SlimV2/2026-08-27-experiments-clean-extraction-baseline.json'
git diff --cached --name-only
git commit -m 'docs(experiments): record extraction baseline'
$baselineCommit = git rev-parse HEAD
$baselineParent = git rev-parse "$baselineCommit^"
if ($baselineParent -ne $preBaselineSourceSha) { throw "baseline parent $baselineParent does not match pre-baseline evidence $preBaselineSourceSha" }
```

预期：baseline commit 只包含两份 baseline 文档，且其唯一父提交精确等于 JSON 中的 `pre_baseline_source_sha`。

- [ ] Baseline 提交完成后创建本地 annotated tag，并以 fail-closed gate 验证 tag object 与 peeled commit：

```powershell
$tagRef = "refs/tags/$freezeTag"
git show-ref --verify --quiet $tagRef
$tagExistsExit = $LASTEXITCODE
if ($tagExistsExit -eq 0) { throw "freeze tag already exists: $tagRef" }
if ($tagExistsExit -ne 1) { throw "cannot inspect freeze tag: exit $tagExistsExit" }
$baselineCommit = git rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($baselineCommit)) { throw 'cannot resolve baseline commit' }
git tag -a $freezeTag $baselineCommit -m 'Freeze source for experiments clean extraction on 2026-08-27'
if ($LASTEXITCODE -ne 0) { throw 'annotated freeze tag creation failed' }
$freezeType = git cat-file -t $tagRef
if ($LASTEXITCODE -ne 0 -or $freezeType -ne 'tag') { throw 'freeze ref is not an annotated tag object' }
$tagObjectSha = git rev-parse $tagRef
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($tagObjectSha)) { throw 'cannot resolve tag object SHA' }
$peeledSha = git rev-parse "$tagRef^{}"
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($peeledSha)) { throw 'cannot resolve peeled commit SHA' }
if ($peeledSha -ne $baselineCommit) { throw "freeze tag peeled to $peeledSha, expected baseline commit $baselineCommit" }
$frozenSha = $peeledSha
```

预期：预存 tag 检查只接受“确实不存在”；创建命令成功；`refs/tags/$freezeTag` 对象类型精确为 `tag`；记录 `$tagObjectSha`；peeled SHA 精确等于当前 baseline commit。不得执行 `git push`。

- [ ] 从此刻起抽取来源只认 `$freezeTag^{}`。来源分支及顶层 `paper/**` 可以继续变化，且不得成为后续门禁；所有 writer/验证器都必须用 `git show`、`git cat-file` 或 `git restore --source=$frozenSha` 读取 peeled commit，禁止读取 live source 工作树文件。

## 5. Task C：建立空内容目标分支和公开骨架

**Files:**

- Create target commit with empty tree
- Restore: `LICENSE`, `requirements.txt`, `src/tokenshare/__init__.py`
- Create: `.gitattributes`, `.gitignore`
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
$branchRef = "refs/heads/$targetBranch"
git -C $sourceRoot show-ref --verify --quiet $branchRef
$branchProbeExit = $LASTEXITCODE
if ($branchProbeExit -eq 0) { throw "target branch already exists: $targetBranch" }
if ($branchProbeExit -ne 1) { throw "cannot inspect target branch: exit $branchProbeExit" }
$objectFormatLines = @(git -C $sourceRoot rev-parse --show-object-format)
if ($LASTEXITCODE -ne 0 -or $objectFormatLines.Count -ne 1) { throw 'cannot resolve repository object format' }
$oidPattern = switch ($objectFormatLines[0].Trim()) {
    'sha1' { '^[0-9a-f]{40}$' }
    'sha256' { '^[0-9a-f]{64}$' }
    default { throw "unsupported object format: $($objectFormatLines[0])" }
}
$tagRef = "refs/tags/$freezeTag"
$freezeTypeLines = @(git -C $sourceRoot cat-file -t $tagRef)
if ($LASTEXITCODE -ne 0 -or $freezeTypeLines.Count -ne 1 -or $freezeTypeLines[0].Trim() -ne 'tag') { throw 'freeze ref is not an annotated tag' }
$frozenLines = @(git -C $sourceRoot rev-parse "$tagRef^{}")
if ($LASTEXITCODE -ne 0 -or $frozenLines.Count -ne 1 -or $frozenLines[0].Trim() -notmatch $oidPattern) { throw 'cannot resolve one valid peeled commit OID' }
$frozenSha = $frozenLines[0].Trim()
$frozenTypeLines = @(git -C $sourceRoot cat-file -t $frozenSha)
if ($LASTEXITCODE -ne 0 -or $frozenTypeLines.Count -ne 1 -or $frozenTypeLines[0].Trim() -ne 'commit') { throw 'peeled freeze object is not a commit' }
```

- [ ] 创建父提交为 frozen SHA 的空树提交，并建立 worktree：

```powershell
$emptyTreeLines = @(@() | git -C $sourceRoot mktree)
if ($LASTEXITCODE -ne 0 -or $emptyTreeLines.Count -ne 1 -or $emptyTreeLines[0].Trim() -notmatch $oidPattern) { throw 'cannot create one valid empty-tree OID' }
$emptyTree = $emptyTreeLines[0].Trim()
$emptyTreeType = @(git -C $sourceRoot cat-file -t $emptyTree)
if ($LASTEXITCODE -ne 0 -or $emptyTreeType.Count -ne 1 -or $emptyTreeType[0].Trim() -ne 'tree') { throw 'empty-tree OID is not a tree' }
$emptyTreePaths = @(git -C $sourceRoot ls-tree -r --name-only $emptyTree)
if ($LASTEXITCODE -ne 0 -or $emptyTreePaths.Count -ne 0) { throw 'constructed tree is not empty' }
$emptyCommitLines = @('chore: start clean extraction tree' | git -C $sourceRoot commit-tree $emptyTree -p $frozenSha)
if ($LASTEXITCODE -ne 0 -or $emptyCommitLines.Count -ne 1 -or $emptyCommitLines[0].Trim() -notmatch $oidPattern) { throw 'cannot create one valid empty commit OID' }
$emptyCommit = $emptyCommitLines[0].Trim()
$emptyCommitType = @(git -C $sourceRoot cat-file -t $emptyCommit)
if ($LASTEXITCODE -ne 0 -or $emptyCommitType.Count -ne 1 -or $emptyCommitType[0].Trim() -ne 'commit') { throw 'empty commit OID is not a commit' }
$emptyCommitTree = @(git -C $sourceRoot rev-parse "$emptyCommit^{tree}")
if ($LASTEXITCODE -ne 0 -or $emptyCommitTree.Count -ne 1 -or $emptyCommitTree[0].Trim() -ne $emptyTree) { throw 'empty commit tree mismatch' }
$parentLine = @(git -C $sourceRoot rev-list --parents -n 1 $emptyCommit)
if ($LASTEXITCODE -ne 0 -or $parentLine.Count -ne 1) { throw 'cannot inspect empty commit parents' }
$parentParts = @($parentLine[0].Trim() -split ' ')
if ($parentParts.Count -ne 2 -or $parentParts[0] -ne $emptyCommit -or $parentParts[1] -ne $frozenSha) { throw 'empty commit must have exactly the frozen commit as parent' }
git -C $sourceRoot worktree add --detach -- $targetRoot $emptyCommit
if ($LASTEXITCODE -ne 0) { throw 'detached target worktree creation failed; do not create target branch' }
$detachedStatus = @(git -C $targetRoot status --porcelain=v1)
if ($LASTEXITCODE -ne 0 -or $detachedStatus.Count -ne 0) { throw 'new detached target worktree is not clean' }
$detachedHead = @(git -C $targetRoot rev-parse HEAD)
if ($LASTEXITCODE -ne 0 -or $detachedHead.Count -ne 1 -or $detachedHead[0].Trim() -ne $emptyCommit) { throw 'detached target HEAD mismatch' }
git -C $targetRoot switch -c $targetBranch
$switchExit = $LASTEXITCODE
if ($switchExit -ne 0) {
    $rollbackStatus = @(git -C $targetRoot status --porcelain=v1)
    if ($LASTEXITCODE -ne 0 -or $rollbackStatus.Count -ne 0) { throw 'branch creation failed and detached worktree is not safely removable' }
    git -C $sourceRoot worktree remove -- $targetRoot
    if ($LASTEXITCODE -ne 0) { throw 'branch creation failed and detached worktree rollback failed' }
    $rollbackBranch = @(git -C $sourceRoot show-ref --verify $branchRef)
    $rollbackBranchExit = $LASTEXITCODE
    if ($rollbackBranchExit -eq 0) {
        if ($rollbackBranch.Count -ne 1 -or ($rollbackBranch[0] -split ' ')[0] -ne $emptyCommit) { throw 'target branch appeared at an unexpected OID during rollback' }
        git -C $sourceRoot update-ref -d $branchRef $emptyCommit
        if ($LASTEXITCODE -ne 0) { throw 'cannot roll back half-created target branch with CAS' }
    } elseif ($rollbackBranchExit -ne 1) {
        throw "cannot inspect target branch during rollback: exit $rollbackBranchExit"
    }
    throw "target branch creation failed: exit $switchExit"
}
$createdBranch = @(git -C $sourceRoot show-ref --verify $branchRef)
if ($LASTEXITCODE -ne 0 -or $createdBranch.Count -ne 1 -or $createdBranch[0].Trim() -ne "$emptyCommit $branchRef") { throw 'target branch ref mismatch after switch' }
```

- [ ] 验证 ancestry 和空内容：

```powershell
$targetHead = @(git -C $targetRoot rev-parse HEAD)
if ($LASTEXITCODE -ne 0 -or $targetHead.Count -ne 1 -or $targetHead[0].Trim() -ne $emptyCommit) { throw 'target HEAD is not the empty commit' }
$targetParent = @(git -C $targetRoot rev-parse 'HEAD^')
if ($LASTEXITCODE -ne 0 -or $targetParent.Count -ne 1 -or $targetParent[0].Trim() -ne $frozenSha) { throw 'target HEAD parent is not frozen SHA' }
$targetTreePaths = @(git -C $targetRoot ls-tree -r --name-only HEAD)
if ($LASTEXITCODE -ne 0 -or $targetTreePaths.Count -ne 0) { throw 'target HEAD tree is not empty' }
git -C $targetRoot merge-base --is-ancestor $frozenSha $emptyCommit
if ($LASTEXITCODE -ne 0) { throw 'frozen commit is not target ancestor' }
$targetStatus = @(git -C $targetRoot status --porcelain=v1)
if ($LASTEXITCODE -ne 0 -or $targetStatus.Count -ne 0) { throw 'target worktree is not clean after creation' }
```

预期：`HEAD^` 等于 frozen SHA；`ls-tree` 和 status 无输出；ancestor 命令退出码 0。

- [ ] 按 manifest 从 frozen SHA 恢复三个 byte-identical 根/包文件；`.gitattributes` 与 `.gitignore` 必须综合生成，不能恢复来源版本：

```powershell
git -C $targetRoot restore --source=$frozenSha --staged --worktree -- 'LICENSE' 'requirements.txt' 'src/tokenshare/__init__.py'
if ($LASTEXITCODE -ne 0) { throw 'root/package restore from frozen commit failed' }
```

- [ ] writer 用 `apply_patch` 创建公开骨架。README 首屏只说明仓库包含 protocol system、唯一 `experiments` 设施、正式题库和正式 metrics；不得介绍顶层 `paper/**`，也不得把 `Slim V2` 当产品名。`RESULTS.md` 暂时只记录历史 run ID 和原始归档 pending 状态，不声称已有外部归档。

- [ ] `.gitignore` 只综合保留 `/TokenShareData/`、`local/*.local.json`、`local/cache/`、Python bytecode、pytest/coverage cache、常见虚拟环境与编辑器临时文件规则；不携带只服务 LaTeX 或顶层 `paper/**` 的规则。`.gitattributes` 在本 Task 初始提交中一次写入 manifest 的 20 个 authoritative target paths + 12 个 official-result target paths，共 32 条 exact `-text`；结果文件尚未出现不影响规则先行生效。

- [ ] `tests/__init__.py` 与 `tests/experiments/__init__.py` 作为两个 exact empty package markers 创建并保留，避免环境中的第三方 `tests` namespace 抢占；它们必须出现在 manifest `synthesize` allowlist、Task C staged set 和最终 extraction verifier 的 exact-required targets 中。

- [ ] `run_experiments.cmd` 只启动 `python -m tokenshare.experiments.gui`，环境变量前缀使用 `TOKENSHARE_EXPERIMENTS_`；不得保留 `run_slim_v2.cmd`。

- [ ] 运行：

```powershell
git -C $targetRoot diff --check
if ($LASTEXITCODE -ne 0) { throw 'target skeleton diff check failed' }
$skeletonStatus = @(git -C $targetRoot status --porcelain=v1)
if ($LASTEXITCODE -ne 0 -or $skeletonStatus.Count -eq 0) { throw 'target skeleton has no auditable changes or status failed' }
git -C $targetRoot add -- '.gitattributes' '.gitignore' 'LICENSE' 'requirements.txt' 'README.md' 'AGENTS.md' 'REPRODUCIBILITY.md' 'RESULTS.md' 'run_experiments.cmd' 'src/tokenshare/__init__.py' 'src/tokenshare/experiments/__init__.py' 'tests/__init__.py' 'tests/experiments/__init__.py'
if ($LASTEXITCODE -ne 0) { throw 'target skeleton staging failed' }
$skeletonCached = @(git -C $targetRoot diff --cached --name-only)
if ($LASTEXITCODE -ne 0 -or $skeletonCached.Count -ne 13) { throw 'target skeleton staged set is not the exact 13 paths' }
git -C $targetRoot commit -m 'chore: establish public experiment repository skeleton'
if ($LASTEXITCODE -ne 0) { throw 'target skeleton commit failed' }
$skeletonHead = @(git -C $targetRoot rev-parse HEAD)
if ($LASTEXITCODE -ne 0 -or $skeletonHead.Count -ne 1 -or $skeletonHead[0].Trim() -notmatch $oidPattern) { throw 'cannot resolve skeleton commit OID' }
$skeletonType = @(git -C $targetRoot cat-file -t $skeletonHead[0].Trim())
if ($LASTEXITCODE -ne 0 -or $skeletonType.Count -ne 1 -or $skeletonType[0].Trim() -ne 'commit') { throw 'skeleton HEAD is not a commit' }
$postSkeletonStatus = @(git -C $targetRoot status --porcelain=v1)
if ($LASTEXITCODE -ne 0 -or $postSkeletonStatus.Count -ne 0) { throw 'target worktree is not clean after skeleton commit' }
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
Assert-RepoPythonImport -RepoRoot $targetRoot
conda run -n tokenshare python -m pytest tests/executors/test_ai_api_descriptor.py -q
```

预期：因 `descriptors.py` 尚不存在或 adapter 尚未改接而失败；失败原因必须与预期一致。

- [ ] 在 `descriptors.py` 实现唯一所需 descriptor builder。其 descriptor ID/type、两种 request schema、capabilities、environment policy、status 和 metadata 必须与 frozen `ai_api.py` 的函数结果逐字段一致。

- [ ] 清理 `executors/__init__.py` 公开面，只导出目标系统和实验闭包实际使用的合同/registry/transport/config/artifact/descriptor；不得为了兼容旧测试导出旧 executor。

- [ ] 运行 GREEN 和 manifest 指定的系统 focused tests：

```powershell
Assert-RepoPythonImport -RepoRoot $targetRoot
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
- Move exact tree: `fixtures/lean_proof_project/**` -> `benchmarks/experiments/fixtures/lean_proof_project/**`
- Move: `benchmarks/paper/exp1_baseline_provider_config.v3.json` -> `configs/experiments/exp1_baseline_provider_config.v3.json`
- Move: `benchmarks/paper/exp5_siliconflow_provider_config.v3.json` -> `configs/experiments/exp5_siliconflow_provider_config.v3.json`
- Create: `benchmarks/experiments/manifest.v1.json`
- Create: `tests/experiments/test_authoritative_corpus.py`
- Create: `verification/verify_authoritative_corpus.py`
- Modify: retained Lean environment/semantic-authority path constants
- Create: `Doc/Experiments/README.md`
- Create: `Doc/Experiments/design.md`
- Create: `Doc/Experiments/metrics.md`
- Create: `Doc/Experiments/system-integration.md`
- Create: `Doc/Experiments/corpus-manifest.md`

- [ ] 从 frozen SHA 恢复 manifest 列出的 7 个正式 catalog/config/semantic-sidecar blobs 和根级 Lean fixture tree，再用 `git mv` 放入公开路径。7 个正式 JSON/JSONL 文件必须逐 bytes 等于 frozen blobs，绝不格式化、解析后重写或修改内部路径；目标物理路径适配只能发生在 consumer code 的只读 resolution 层。

- [ ] 创建 corpus manifest，逐文件记录 frozen path、public path、SHA-256、记录数和职责。7 个正式文件的 source/target SHA 必须相同；provider config 的新默认物理位置由代码选择，不允许改写 config bytes。Lean fixtures 同样按 manifest 的 13 个 exact blobs 做 byte-identical 检查。

- [ ] 更新 Lean plugin 的 `environment.py`、`semantic_authority.py` 及 manifest 明确列出的 path consumers，使默认路径指向 `benchmarks/experiments`。只允许路径变化，不改变 checker 语义。

- [ ] 编写 corpus 测试，比较 baseline JSON 与目标：完整 case ID 排序集、Factorization/Lean 记录数、profile 选择 ID/摘要、所有内容未变文件的 SHA-256、Lean project 相对路径与文件 SHA。正式 case 集不能用抽样检查代替。

- [ ] 创建 `verification/verify_authoritative_corpus.py`：从 baseline JSON 读取完整 `full_root_identities.items` 与 `full_reference_identities.items`，从 target corpus/plan 重算同 schema arrays，按 manifest 固定 sort/canonical JSON 算法执行逐元素比较，并分别校验 count、canonical byte length 与 `items_sha256`。同时验证 7 个正式 blobs、13 个 Lean fixture blobs 和 sidecar logical→physical mapping；不得用摘要或抽样替代完整 arrays。

- [ ] 从已批准的 Slim V2 权威文档综合生成 `Doc/Experiments/` 五份公开文档。去掉工程代号和过程状态，但不得改动实验条件、指标口径、provider reuse 事实或系统边界。`metrics.md` 明确 Exp2–4 复用 Exp1 普通真实回答。

- [ ] 运行：

```powershell
Assert-RepoPythonImport -RepoRoot $targetRoot
conda run -n tokenshare python -m pytest tests/experiments/test_authoritative_corpus.py -q
conda run -n tokenshare python verification/verify_authoritative_corpus.py
conda run -n tokenshare python -m pytest tests/plugins/lean_proof/test_lean_environment.py tests/plugins/lean_proof/test_lean_fixture_project_manifest.py tests/plugins/lean_proof/test_lean_preflight.py -q
git -C $targetRoot diff --check
```

预期：全部退出码 0；题库测试与独立 verifier 都证明完整 case/selection/identity arrays/count/canonical bytes/SHA 对等，不只是文件存在。

- [ ] spec reviewer 逐条对照原权威文档核验科学口径；quality reviewer 检查是否把过程计划或旧 runner 说明带入公开文档。

- [ ] 提交：

```powershell
git -C $targetRoot add -- 'benchmarks/experiments' 'configs/experiments' 'src/tokenshare/plugins' 'tests/experiments/test_authoritative_corpus.py' 'tests/plugins/lean_proof' 'verification/verify_authoritative_corpus.py' 'Doc/Experiments'
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
Assert-RepoPythonImport -RepoRoot $targetRoot
conda run -n tokenshare python -m pytest tests/experiments -q
conda run -n tokenshare python -m compileall -q src/tokenshare/experiments
conda run -n tokenshare python -m tokenshare.experiments.cli plan --profile full --run-id extraction-target-plan
```

预期：pytest 与 compileall 退出码 0；plan 输出中的 case 集、选择摘要和 provider-call bounds 与 baseline JSON 逐字段一致，公开路径只出现在路径字段中。CLI 不使用未实现的格式化参数。

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

## 9. Task G：保留正式结果和最小验证 harness

**Files:**

- Create: `results/experiments/slim-v2-full-flash-20260823-233000-b4c8e951/run.json`
- Create: `results/experiments/slim-v2-full-flash-20260823-233000-b4c8e951/metrics/summary.json`
- Create exact 10 table files under: `results/experiments/slim-v2-full-flash-20260823-233000-b4c8e951/metrics/tables/`
- Create: `results/experiments/slim-v2-full-flash-20260823-233000-b4c8e951/manifest.json`
- Retain: `verification/verify_authoritative_corpus.py`
- Create: `verification/verify_official_results.py`
- Create: `verification/verify_extraction.py`
- Create: `verification/extraction_manifest.json`
- Create/modify: `verification/run_verification.py`
- Create/modify: `verification/fast-tests.txt`
- Restore/create: `verification/pytest_network_tripwire.py`, `verification/sitecustomize.py`
- Create: `Doc/Experiments/code-map.md`
- Modify: `README.md`, `RESULTS.md`, `REPRODUCIBILITY.md`, `AGENTS.md`

- [ ] 复制前先证明 Task C 已提交的 `.gitattributes` 对 manifest 第 3.5 节 12 个目标 result paths 全部返回 `text: unset`、`eol: unspecified`。然后从 ignored 正式 raw 目录只复制 `run.json`、`metrics/summary.json`、Exp1–5 的 CSV/JSONL 共 10 个表。复制前后逐文件 SHA-256 与 byte size 都必须等于 baseline JSON；任一不同立即删除目标副本并停止调查，不得运行 reducer 覆盖来源或目标，也不得修改结果内容。

- [ ] 结果 `manifest.json` 记录 12 个文件的相对路径、SHA-256、字节数、表行数、历史 run ID 和 raw archive 状态，不复制 baseline 中不存在的 frozen identity。`RESULTS.md` 解释目录名中的 `slim-v2` 是冻结运行的历史内部标识，不是公开设施名称。

- [ ] 创建 target-side `verification/extraction_manifest.json`，记录 `pre_baseline_source_sha`、`source_tag`、`tag_object_sha`、`peeled_commit_sha` 和生成时 target ancestor check。值只能在 annotated tag 创建并通过 Task B fail-closed gate 后取得；`peeled_commit_sha` 必须等于 `$frozenSha`，`tag_object_sha` 必须解析为 `tag` 对象。`verification/verify_extraction.py` 必须重新解析本地 tag 并逐字段核对该文件。

- [ ] 实现 `verification/verify_official_results.py`：

  1. 读取 Git 结果目录和 ignored raw 目录；
  2. 比较 12 个已发布文件的 SHA-256；
  3. monkeypatch reducer 私有 `_stage_payloads`，只在内存中捕获 `_reduce_run_staged(run_dir, {})` 生成的 11 个 metrics payload；
  4. 将捕获字节与 Git 中 11 个 metrics 文件逐字节比较；
  5. 禁止调用会写回正式目录的 `reduce_run()`；
  6. `--verify-worktree-index` 模式对 12 个 target files 分别读取 working bytes 与 index blob bytes，比较 baseline SHA-256/size，并检查 working/cached attributes 均为 `-text`；index bytes 必须通过 binary `git cat-file blob` 读取，不经过 text pipeline；
  7. 若 raw 目录在将来的外部 checkout 中不存在，默认验证 Git manifest、working/index bytes 与 `.gitattributes`；只有显式 `--require-raw` 才因缺失 raw 失败。

- [ ] 12 个 working files 首轮 SHA/size 通过后，只 stage 这 12 个 exact paths，再运行 index blob gate；此步骤发生在结果 manifest 和其他 Task G 文件最终 staging 之前：

```powershell
$officialResultRoot = 'results/experiments/slim-v2-full-flash-20260823-233000-b4c8e951'
$officialResultPaths = @(
    "$officialResultRoot/run.json",
    "$officialResultRoot/metrics/summary.json",
    "$officialResultRoot/metrics/tables/exp1.csv", "$officialResultRoot/metrics/tables/exp1.jsonl",
    "$officialResultRoot/metrics/tables/exp2.csv", "$officialResultRoot/metrics/tables/exp2.jsonl",
    "$officialResultRoot/metrics/tables/exp3.csv", "$officialResultRoot/metrics/tables/exp3.jsonl",
    "$officialResultRoot/metrics/tables/exp4.csv", "$officialResultRoot/metrics/tables/exp4.jsonl",
    "$officialResultRoot/metrics/tables/exp5.csv", "$officialResultRoot/metrics/tables/exp5.jsonl"
)
git -C $targetRoot add -- $officialResultPaths
if ($LASTEXITCODE -ne 0) { throw 'official result staging failed' }
Assert-RepoPythonImport -RepoRoot $targetRoot
conda run -n tokenshare python verification/verify_official_results.py --raw-root 'E:\TokenEcnomic\TokenShareWorktrees\slim-v2-baseline\TokenShareData\outputs\slim_v2\slim-v2-full-flash-20260823-233000-b4c8e951' --require-raw --verify-worktree-index
if ($LASTEXITCODE -ne 0) { throw 'official result working/index byte gate failed' }
```

预期：12/12 working bytes 与 12/12 index blob bytes 的 SHA-256/size 均等于 baseline；12/12 working/cached attributes 均为 `-text`。此后不得再写这些 12 个 paths。

- [ ] 实现 `verification/verify_extraction.py`：验证冻结 tag peeled commit/目标 ancestry、题库 manifest、禁止路径、运行时 import、结果 manifest、历史字符串 allowlist 和 Git tracked 文件边界。它必须断言 `git ls-files -- 'paper/**'` 无输出，并且不能把绝对 source worktree 写入目标配置或运行时。

- [ ] 保留并再次运行 Task E 创建的 `verification/verify_authoritative_corpus.py`；它必须从 baseline JSON 读取完整 identity arrays，重算 target arrays，并逐元素校验 count、canonical byte length 与 identity SHA，不得在 Task G 被其他 harness 替换或降级为抽样。

- [ ] 最小 harness 只包含本计划实际需要的 focused/fast verification。不得抽取旧 Rxx、LeanAudit、paper/formal pipeline profile 作为“保险”。

- [ ] 运行：

```powershell
Assert-RepoPythonImport -RepoRoot $targetRoot
conda run -n tokenshare python verification/verify_official_results.py --raw-root 'E:\TokenEcnomic\TokenShareWorktrees\slim-v2-baseline\TokenShareData\outputs\slim_v2\slim-v2-full-flash-20260823-233000-b4c8e951' --require-raw --verify-worktree-index
conda run -n tokenshare python verification/verify_authoritative_corpus.py
conda run -n tokenshare python verification/verify_extraction.py --frozen-sha $frozenSha
$paperTracked = @(git ls-files -- 'paper/**')
if ($LASTEXITCODE -ne 0 -or $paperTracked.Count -ne 0) { throw 'top-level paper workspace must be absent from target' }
```

预期：三个 Python gate 全部退出码 0，并报告 12 个 working/index result hashes+sizes、11 个 reducer payloads、完整 identity arrays/count/canonical bytes/SHA 对等和 tag evidence 对等；最后的 exact selector 无输出，证明顶层 `paper/**` 没有任何 tracked 文件。

- [ ] spec reviewer 核对结果/公开实验文档；quality reviewer 特别检查是否误跟踪顶层 `paper/**`、raw artifacts、secret 或 source worktree 绝对路径。两者都不得读取来源工作树的顶层 `paper/**`。

- [ ] 提交：

```powershell
git -C $targetRoot add -- 'results/experiments' 'verification' 'Doc/Experiments/code-map.md' 'README.md' 'RESULTS.md' 'REPRODUCIBILITY.md' 'AGENTS.md'
git -C $targetRoot diff --cached --name-only
git -C $targetRoot commit -m 'data(experiments): preserve formal results and verification harness'
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

- [ ] 在 review worktree 创建独立环境或使用现有 `tokenshare` conda 环境；先调用同一 `Assert-RepoPythonImport` 将 `PYTHONPATH` 固定为 `$reviewRoot\src` 并断言 `tokenshare.__file__` 位于 review tree，再断言 `sys.path` 不包含 `$sourceRoot` 或 `$targetRoot`。不得从另一个 worktree 借用源码。

- [ ] 运行 risk-driven final gate：

```powershell
Assert-RepoPythonImport -RepoRoot $reviewRoot
conda run -n tokenshare python -c "import sys; from pathlib import Path; blocked={Path(r'$sourceRoot').resolve(),Path(r'$targetRoot').resolve()}; actual={Path(p).resolve() for p in sys.path if p}; assert blocked.isdisjoint(actual), f'cross-worktree sys.path contamination: {blocked & actual}'"
if ($LASTEXITCODE -ne 0) { throw 'review Python path isolation failed' }
conda run -n tokenshare python -m compileall -q "$reviewRoot\src" "$reviewRoot\tests" "$reviewRoot\verification"
conda run -n tokenshare python "$reviewRoot\verification\run_verification.py" --focused system
conda run -n tokenshare python "$reviewRoot\verification\run_verification.py" --focused experiments
conda run -n tokenshare python "$reviewRoot\verification\verify_official_results.py" --verify-worktree-index
conda run -n tokenshare python "$reviewRoot\verification\verify_extraction.py" --frozen-sha $frozenSha
```

预期：全部退出码 0；system gate 包含 protocol/store/replay、Factorization 多 worker；experiments gate 包含 corpus、plan、fake vertical、resume、reducer golden、网络 tripwire 0 calls；official result gate 在 fresh checkout、没有 raw 时仍逐项验证 12 个 working/index blobs 的 SHA-256/size 与 32 条 `.gitattributes` 规则。

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

## 11. Task I：发布干净目标分支与 annotated tag

**Files:** 无内容编辑；只发布两个已获批 ref，不移动来源分支。

- [ ] 在目标工作树执行最终本地验证。来源分支 HEAD 及其顶层 `paper/**` 状态不参与 gate：

```powershell
$targetStatus = @(git -C $targetRoot status --porcelain=v1)
if ($LASTEXITCODE -ne 0 -or $targetStatus.Count -ne 0) { throw 'target worktree must be clean before publication' }
$publishObjectFormat = @(git -C $sourceRoot rev-parse --show-object-format)
if ($LASTEXITCODE -ne 0 -or $publishObjectFormat.Count -ne 1) { throw 'cannot resolve object format before publication' }
$oidPattern = switch ($publishObjectFormat[0].Trim()) {
    'sha1' { '^[0-9a-f]{40}$' }
    'sha256' { '^[0-9a-f]{64}$' }
    default { throw "unsupported object format: $($publishObjectFormat[0])" }
}
$tagRef = "refs/tags/$freezeTag"
$freezeType = @(git -C $sourceRoot cat-file -t $tagRef)
if ($LASTEXITCODE -ne 0 -or $freezeType.Count -ne 1 -or $freezeType[0].Trim() -ne 'tag') { throw 'freeze ref is not an annotated tag' }
$frozenLines = @(git -C $sourceRoot rev-parse "$tagRef^{}")
if ($LASTEXITCODE -ne 0 -or $frozenLines.Count -ne 1 -or $frozenLines[0].Trim() -notmatch $oidPattern) { throw 'cannot resolve valid peeled freeze commit' }
$frozenSha = $frozenLines[0].Trim()
$targetLines = @(git -C $targetRoot rev-parse HEAD)
if ($LASTEXITCODE -ne 0 -or $targetLines.Count -ne 1 -or $targetLines[0].Trim() -notmatch $oidPattern) { throw 'cannot resolve valid target commit' }
$targetSha = $targetLines[0].Trim()
git -C $targetRoot merge-base --is-ancestor $frozenSha $targetSha
if ($LASTEXITCODE -ne 0) { throw 'frozen commit is not an ancestor of target' }
$paperTracked = @(git -C $targetRoot ls-files -- 'paper/**')
if ($LASTEXITCODE -ne 0 -or $paperTracked.Count -ne 0) { throw 'target tracks top-level paper workspace' }
```

预期：目标 status 为空；tag 对象类型是 `tag`；peeled commit 是 target ancestor；目标不跟踪顶层 `paper/**`。不得要求来源 HEAD 等于 frozen SHA，也不得对来源工作树做 clean 检查。

- [ ] 保存最终 target evidence：target SHA、提交列表、验证命令结果、题库/result manifests、final reviewer findings 与处置。证据写入公开 `REPRODUCIBILITY.md` 或 `Doc/Experiments/code-map.md` 时，不得包含本机绝对路径；本机路径只留在监督会话记录。

- [ ] 发布前解析 remote，为 branch/tag 分别构造 CAS lease；随后用一次 `git push --atomic` 同时发布两个 ref：

```powershell
$remote = 'origin'
$targetRef = "refs/heads/$targetBranch"
$tagRef = "refs/tags/$freezeTag"
$localTagObjectLines = @(git -C $sourceRoot rev-parse $tagRef)
if ($LASTEXITCODE -ne 0 -or $localTagObjectLines.Count -ne 1 -or $localTagObjectLines[0].Trim() -notmatch $oidPattern) { throw 'cannot resolve local annotated tag object' }
$localTagObject = $localTagObjectLines[0].Trim()
$remoteBranch = @(git -C $targetRoot ls-remote --refs $remote $targetRef)
if ($LASTEXITCODE -ne 0) { throw 'cannot inspect remote target branch' }
if ($remoteBranch.Count -eq 0) {
    $branchLease = "--force-with-lease=$targetRef`:"
} elseif ($remoteBranch.Count -eq 1 -and ($remoteBranch[0] -split "`t")[0] -eq $targetSha) {
    $branchLease = "--force-with-lease=$targetRef`:$targetSha"
} else {
    throw 'remote target branch exists at a different or ambiguous SHA'
}
$remoteTag = @(git -C $sourceRoot ls-remote $remote $tagRef "$tagRef^{}")
if ($LASTEXITCODE -ne 0) { throw 'cannot inspect remote freeze tag' }
if ($remoteTag.Count -eq 0) {
    $tagLease = "--force-with-lease=$tagRef`:"
} else {
    $remoteTagMap = @{}
    foreach ($line in $remoteTag) {
        $parts = $line -split "`t"
        if ($parts.Count -ne 2 -or $remoteTagMap.ContainsKey($parts[1])) { throw 'ambiguous remote tag response' }
        $remoteTagMap[$parts[1]] = $parts[0]
    }
    if ($remoteTagMap.Count -ne 2 -or $remoteTagMap[$tagRef] -ne $localTagObject -or $remoteTagMap["$tagRef^{}"] -ne $frozenSha) { throw 'remote freeze tag exists with different object or peeled SHA' }
    $tagLease = "--force-with-lease=$tagRef`:$localTagObject"
}
git -C $targetRoot push --atomic $branchLease $tagLease $remote "$targetRef`:$targetRef" "$tagRef`:$tagRef"
if ($LASTEXITCODE -ne 0) { throw 'atomic branch+tag publication failed; do not retry without --atomic or CAS leases' }
```

Preflight 后的远端竞态必须由两个 `--force-with-lease=<ref>:<expected>` CAS 条件阻止。Remote 不支持 atomic 时整次发布失败；禁止拆成两次 push 或降级。禁止 push `codex/slim-v2-baseline`，禁止裸 `--force`/`--force-with-lease`（仅允许上面两个 exact CAS lease），禁止 merge/fast-forward 来源分支。

- [ ] 重新查询远端并验证 ref 与 peeled SHA：

```powershell
$remoteBranch = @(git -C $targetRoot ls-remote --refs $remote $targetRef)
if ($LASTEXITCODE -ne 0) { throw 'cannot post-check remote target branch' }
$remoteTag = @(git -C $sourceRoot ls-remote $remote $tagRef "$tagRef^{}")
if ($LASTEXITCODE -ne 0) { throw 'cannot post-check remote freeze tag' }
if ($remoteBranch.Count -ne 1 -or ($remoteBranch[0] -split "`t")[0] -ne $targetSha) { throw 'remote target branch SHA mismatch' }
$remoteTagMap = @{}
foreach ($line in $remoteTag) {
    $parts = $line -split "`t"
    if ($parts.Count -ne 2 -or $remoteTagMap.ContainsKey($parts[1])) { throw 'ambiguous remote tag post-check' }
    $remoteTagMap[$parts[1]] = $parts[0]
}
if ($remoteTagMap.Count -ne 2) { throw 'remote annotated tag post-check is incomplete' }
if ($remoteTagMap[$tagRef] -ne $localTagObject) { throw 'remote annotated tag object SHA mismatch' }
if ($remoteTagMap["$tagRef^{}"] -ne $frozenSha) { throw 'remote annotated tag peeled SHA mismatch' }
```

预期：远端目标 branch 解析到 `$targetSha`，远端 annotated tag object 解析到 `$localTagObject`，其 peeled ref 解析到 `$frozenSha`。向用户报告三者、保留结果 manifest SHA、验证摘要和仍为 pending 的 raw archive 决策。

## 12. 完成判据

只有同时满足以下条件，监督者才可声称清洗完成：

- 目标分支 `codex/experiments-clean-extraction` 已发布到验收后的 target SHA；来源分支 `codex/slim-v2-baseline` 未移动、未 push，用户可以继续其独立工作；
- `src/tokenshare/experiments/` 是唯一实验设施包，源码/测试/harness 不含旧 runner 或兼容 wrapper；
- 系统本体、Factorization、Lean plugin/checker、AI transport/config/artifacts 的最小闭包通过 focused verification；
- 正式题库完整 case 集、选择结果和 SHA-256 与 baseline 完全一致；
- 正式 `run.json` 与 11 个 metrics 文件逐字节匹配 baseline，reducer 内存重算的 11 个 payload 也逐字节匹配；
- provider/network 调用计数为 0，正式 raw 目录没有写入；
- clean-room checkout 完成 compile、system、experiments、result、Factorization 多 worker 和单个 Lean checker smoke；
- 三类 final reviewer 无未处置 blocker/high finding；
- README 只给外部读者一个 `experiments` 入口，历史 `slim-v2` 字符串只存在于 allowlist 规定的持久化事实和说明中；
- 顶层 `paper/**` 在目标 `git ls-files` 中为空，README/公开实验文档不介绍该 workspace；`benchmarks/paper/**` 来源中的正式题库/配置已按清单迁移并通过全量 identity/SHA gate；
- 本地和远端 annotated freeze tag 的 peeled ref 都解析到冻结来源 SHA；未发生来源分支 push、强推、远端 release 或历史重写；
- 原始全量归档仍明确标记为 `pending_advisor_archive_decision`，没有虚构外部 URL。
