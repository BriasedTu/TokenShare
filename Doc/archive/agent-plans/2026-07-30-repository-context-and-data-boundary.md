# Repository Context and Data Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 TokenShare 的默认 agent 接管上下文保持精简、当前事实唯一，并把实验运行数据迁出仓库且由代码统一解析默认路径。

**Architecture:** 新增一个无 I/O 副作用的 `tokenshare.runtime_paths` 模块，统一解析仓库根、外部数据根和各类默认输出目录；CLI 只在 argparse 构造时读取默认值，显式参数继续优先。Harness 使用 Tier 1/2/3 分层，根级状态文件只保存当前摘要，历史内容移动到 `Doc/archive/`，验证器用大小、schema 和路径边界门禁防止再次膨胀。

**Tech Stack:** Python 3、pytest、PowerShell、JSON、Markdown、现有 `verification/run_verification.py`。

---

### Task 1: 冻结迁移边界和当前事实

**Files:**
- Create outside repo: `E:/TokenEcnomic/TokenShareData/migration-backups/2026-07-30-context-consolidation-before/`
- Create: `Doc/repository-governance.md`
- Create: `docs/superpowers/plans/2026-07-30-repository-context-and-data-boundary.md`

- [x] **Step 1: 验证仓库根、外部根和磁盘空间**

Run: PowerShell 解析两个绝对路径，断言 data root 不在 repo root 下，并读取 E 盘可用空间。

Expected: `E:/TokenEcnomic/TokenShareData` 位于仓库外，可用空间大于当前约 0.9 GB outputs。

- [x] **Step 2: 复制修改前状态和文档快照**

Run: 将根级 harness 文件、`Doc/` 和 `docs/` 复制到外部 migration backup。

Expected: 备份至少包含 100 个文件，且不修改源文件。

### Task 2: 用 TDD 建立外部数据根

**Files:**
- Create: `src/tokenshare/runtime_paths.py`
- Create: `tests/test_runtime_paths.py`

- [ ] **Step 1: 写默认根、override 和仓库内拒绝测试**

```python
def test_default_data_root_is_repository_sibling() -> None:
    assert default_data_root() == repository_root().parent / "TokenShareData"

def test_absolute_environment_override_is_used(monkeypatch, tmp_path) -> None:
    target = tmp_path / "tokenshare-data"
    monkeypatch.setenv("TOKENSHARE_DATA_ROOT", str(target.resolve()))
    assert data_root() == target.resolve()

def test_repository_internal_override_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("TOKENSHARE_DATA_ROOT", str(repository_root() / "outputs"))
    with pytest.raises(ValueError, match="outside the repository"):
        data_root()
```

- [ ] **Step 2: 运行 RED**

Run: `conda run -n tokenshare python -m pytest -q tests/test_runtime_paths.py`

Expected: FAIL，因为 `tokenshare.runtime_paths` 尚不存在。

- [ ] **Step 3: 实现最小路径 API**

实现 `repository_root()`、`default_data_root()`、`data_root()`、`experiment_outputs_root()`、`diagnostic_outputs_root()` 和 `supervision_root()`；环境 override 必须为绝对仓库外路径。

- [ ] **Step 4: 运行 GREEN**

Run: `conda run -n tokenshare python -m pytest -q tests/test_runtime_paths.py`

Expected: PASS。

### Task 3: 让所有实验入口使用统一默认根

**Files:**
- Modify: `src/tokenshare/experiments/run_all.py`
- Modify: `src/tokenshare/experiments/run_ai_profile.py`
- Modify: `src/tokenshare/experiments/run_factorization_500_ai.py`
- Modify: `src/tokenshare/experiments/run_lean_ai_benchmark.py`
- Modify: `src/tokenshare/experiments/run_paper_experiments.py`
- Modify: related CLI tests under `tests/experiments/`

- [ ] **Step 1: 为五个 CLI 写捕获默认 output root 的测试**

测试 monkeypatch 实际 suite/runner 函数，不调用 provider；设置临时绝对 `TOKENSHARE_DATA_ROOT`，省略 `--output-root`，断言传入路径分别落在外部根的 `outputs/experiments/...` 下。

- [ ] **Step 2: 运行 RED**

Run: 只运行新增的五个默认路径 nodeids。

Expected: 当前实现仍返回仓库相对 `outputs/...`，断言失败。

- [ ] **Step 3: 接入 `runtime_paths`**

将 argparse 默认值替换为统一函数结果；不得改变显式 `--output-root`、identity digest 或测试传入的 `tmp_path`。

- [ ] **Step 4: 运行 GREEN 和 CLI 相邻回归**

Run: `conda run -n tokenshare python -m pytest -q tests/experiments/test_run_all_cli.py tests/experiments/test_ai_profile_suite.py tests/experiments/test_factorization_500_ai.py tests/experiments/test_lean_ai_benchmark.py tests/experiments/test_run_paper_experiments_cli.py`

Expected: PASS，provider calls 为 0。

### Task 4: 给 harness 增加真实上下文预算门禁

**Files:**
- Modify: `verification/run_verification.py`
- Modify: `tests/test_init_verification_profiles.py`
- Add to manifest: `verification/fast-tests.txt`

- [ ] **Step 1: 写 RED 测试**

断言验证器包含 Tier 1 单文件/总字节预算、稳定 feature-list 顶层字段、唯一 in-progress feature、外部默认数据根检查；断言 compileall 只接收 `src`、`tests`、`verification`，不再递归仓库根。

- [ ] **Step 2: 运行 RED**

Run: `conda run -n tokenshare python -m pytest -q tests/test_init_verification_profiles.py`

Expected: FAIL，因为当前验证器只检查文件存在和关键词。

- [ ] **Step 3: 实现门禁**

预算初始值：`AGENTS.md <= 16 KB`，`feature_list.json <= 32 KB`，`progress.md <= 32 KB`，`session-handoff.md <= 32 KB`，Tier 1 合计 `<= 96 KB`。compileall 分目录执行并继续排除缓存。

- [ ] **Step 4: 运行 GREEN**

Run: 同上。

Expected: 在状态文件收敛前只剩预算相关失败；完成 Task 6 后全绿。

### Task 5: 安全迁移运行数据

**Files:**
- Move outside repo: `outputs/` -> `E:/TokenEcnomic/TokenShareData/outputs/`
- Move outside repo: `local/supervision/` -> `E:/TokenEcnomic/TokenShareData/local/supervision/`
- Create outside repo: `E:/TokenEcnomic/TokenShareData/migration-manifest.json`

- [ ] **Step 1: 停止条件检查**

确认没有 TokenShare experiment/worker/launcher 进程；解析源和目标绝对路径；目标不得已存在，源必须位于仓库内，目标必须位于外部 data root。

- [ ] **Step 2: 记录迁移前 inventory**

记录每个树的文件数、总字节、最新 mtime，并对每个一级目录选择首尾文件计算 SHA-256。

- [ ] **Step 3: 同卷移动**

使用 PowerShell `Move-Item -LiteralPath` 串行移动两个明确目录，不使用通配符，不创建 junction/symlink。

- [ ] **Step 4: 记录并验证迁移后 inventory**

断言源不存在、目标存在、文件数/字节数/抽样哈希一致；将 old/new mapping 和 inventory 写入外部 manifest。

### Task 6: 收敛当前状态和文档层级

**Files:**
- Rewrite: `AGENTS.md`
- Rewrite: `feature_list.json`
- Rewrite: `progress.md`
- Rewrite: `session-handoff.md`
- Rewrite: `Doc/agent-navigation.md`
- Rewrite: `README.md`
- Rewrite: `Doc/TechnicalDocument/tokenshare_v1_code_map.md`
- Create: `Doc/archive/README.md`
- Move: dated/superseded design and plan documents into topic directories under `Doc/archive/`
- Move: old root harness snapshots into `Doc/archive/state-history/`
- Move: `docs/superpowers/` into `Doc/archive/agent-plans/` after execution

- [ ] **Step 1: 归档而不拼接**

保留每份历史文件原文，按 `design-history`、`code-maps`、`state-history`、`agent-plans` 分类；不创建单一巨型合并文档。

- [ ] **Step 2: 重写 Tier 1**

每个文件只保留本治理文档第 3 节定义的职责；`feature_list.json` 只使用固定 schema 字段并保留 feat-001..011 状态摘要。

- [ ] **Step 3: 重写导航和人类入口**

默认接管只列 Tier 1；按协议、Lean、AI executor、实验、论文、历史资料分别给出唯一 Tier 2 入口。

- [ ] **Step 4: 生成短 code map**

以当前 `src/tokenshare` 和 `tests` 实际目录为准，记录模块职责、主要入口和禁止依赖；不维护逐函数编年史。

- [ ] **Step 5: 扫描冲突表述**

在非归档当前文档中搜索 `当前覆盖`、`最新覆盖`、旧 Exp5 v1/v2 参数、仓库内 `outputs/` 默认命令和 `local/supervision` 默认命令；逐项修正或明确标为 historical。

### Task 7: 最终验证与证据回填

**Files:**
- Update: `feature_list.json`
- Update: `progress.md`
- Update: `session-handoff.md`

- [ ] **Step 1: 定向测试**

运行 runtime paths、五个 CLI、verification profile 和文档门禁测试。

- [ ] **Step 2: Fast**

Run: `.\init.ps1`

Expected: 无仓库内 pytest/output listing warning，全部 Fast tests 通过。

- [ ] **Step 3: Full**

Run: `.\init.ps1 -Full`

Expected: 全部 tests 通过；本轮不改变 Lean catalog/checker/toolchain，所以不运行 LeanAudit。

- [ ] **Step 4: 文档—实现二次审核**

逐项比对默认路径函数、CLI help、README 命令、AGENTS 启动顺序、feature 当前状态和唯一实验设计；任何差异先修正再重跑相关门禁。

- [ ] **Step 5: 写回精简证据**

只记录命令、结果、迁移 manifest 路径和下一步；完整历史保留在 archive/Git，不把测试逐项日志复制回 Tier 1。

**Execution note:** 用户已明确要求当前会话直接执行；采用 inline execution。现有工作树包含用户/其他任务改动，禁止 reset/clean/checkout/stage/commit/push。
