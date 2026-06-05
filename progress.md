# 会话进度日志（Session Progress Log）

## 当前状态（Current State）

**最后更新：** 2026-06-05
**当前 Feature：** feat-002 - Phase 1 - Protocol Base Objects and Storage
**仓库阶段：** startup / local research prototype

TokenShare 当前已有设计文档、仓库元数据和初始 Python package layout 骨架。启动期 harness 已经建立并通过验证；V1 技术栈已收束为 Python 3.12+、SQLite、JSON、JSONL 和本地文件系统。Phase 1 最小对象字段规格已经以独立文档固化，下一步可以基于该规格进入协议基础对象与存储的窄切片实现。

## 项目理解（Project Understanding）

TokenShare V1 不是整数分解应用、不是 Lean theorem prover，也不是区块链产品。它要先做一个本地协议内核，证明以下协议闭环可以工作：

1. 注册根任务。
2. 创建并维护递归任务图。
3. 通过 lease 和 attempt 调度 ready task unit。
4. 持久化 artifact 和 append-only event。
5. 通过插件规则验证 submission。
6. 绑定唯一 canonical output bundle。
7. 对节点执行 expand 或 complete。
8. 将子节点结果向父节点合并。
9. 记录贡献并执行 sandbox settlement。
10. 从 JSONL event replay 和 audit，不重新运行非确定性执行。

V1 的两个实验是 factorization 和 Lean stub proof。它们是用于验证协议可扩展性的插件，不是协议核心逻辑。

## 状态（Status）

### 已完成（What's Done）

- [x] 阅读 `README.md`。
- [x] 阅读 TDD：`Doc/TechnicalDocument/2026-06-03-tokenshare-protocol-technical-design.md`。
- [x] 阅读协议讨论稿：`Doc/TechnicalDocument/2026-06-02-tokenshare-protocol-kernel-revised-draft.md`。
- [x] 确认 V1 是 Python/SQLite/JSONL 本地可复现实验原型。
- [x] 创建启动期 harness 文件。
- [x] 成功运行启动验证。
- [x] 成功运行 harness 结构验证，得分 100/100。
- [x] 记录语言要求：`AGENTS.md` 和 `progress.md` 以后优先用中文维护，技术标识和命令可保留英文。
- [x] 按当前稳定边界重写中文 `README.md`，作为项目入口文档。
- [x] 对照 Airflow、Argo Workflows、Temporal、Ray、BOINC、SQLite 和 Python 官方资料，完成 V1 技术栈取舍。
- [x] 更新 TDD：新增“V1 技术栈决策”，并将“第一版实现语言”从开放问题收束为 Python 3.12+ / SQLite / JSONL 本地轻栈。
- [x] 创建 `reference_repos/`，浅克隆 / sparse checkout Temporal Python SDK、Luigi、cwltool、Prefect、Dagster，用于 package layout 参考。
- [x] 更新启动验证：`compileall` 排除 `reference_repos/`，避免外部参考源码影响 TokenShare 基线。
- [x] 确定并创建初始 package layout：`src/tokenshare/{core,storage,plugins,executors,replay,experiments}` 和镜像 `tests/` 骨架。
- [x] 更新启动验证：存在 `tests/` 时在 `PYTHONPATH=src` 下运行 `pytest tests`，并加入 package layout smoke test。
- [x] 新增 agent 导航文档：`Doc/agent-navigation.md`，记录事实源优先级、模块路由和参考源码使用规则。
- [x] 将 agent 导航从 `Doc/TechnicalDocument/` 移到 `Doc/` 根目录，保持技术设计目录只放设计资料。
- [x] 新增 Phase 1 最小对象字段规格：`Doc/TechnicalDocument/2026-06-05-phase-1-minimal-object-field-spec.md`，用表格区分协议对象名、未来 Python 类名、对象字段名、JSON key、事件类型和 SQLite 索引表名。
- [x] 更新 `Doc/agent-navigation.md`，把对象字段规格文档加入事实源和“需要设计对象字段”的导航索引。

### 进行中（What's In Progress）

- [ ] 开始 Phase 1 - Protocol Base Objects and Storage。
  - 细节：定义第一条实现切片，覆盖 `TaskSpec`、`TaskUnit`、`TaskRelation`、`ClientRecord`、`ArtifactRef`、`ArtifactStore`、`LedgerEvent`、JSONL `EventLedger`、`ProtocolConfig`。
  - 当前状态：最小字段规格、Phase 1 事件集合和 SQLite 可重建索引边界已形成 Draft；实现时如发现字段冲突，需要回写规格文档。

### 下一步（What's Next）

1. 基于最小字段规格创建 Phase 1 具体模块文件。
2. 为 `TaskSpec`、`ArtifactRef`、`LedgerEvent` 和基础 `ProtocolConfig` 补最小测试。
3. 实现一条窄闭环：root task registration、artifact save/read/hash、event append/read。

## 阻塞与风险（Blockers / Risks）

- [ ] 字段规格目前是 Draft，编码时若发现 `ArtifactRef` 模块归属、ID 生成或 event payload 形态需要调整，必须回写 `Doc/TechnicalDocument/2026-06-05-phase-1-minimal-object-field-spec.md`。
- [ ] SQLite 只能作为可重建索引和查询视图，不能变成隐藏权威状态源；实现时需要用测试验证“删除 SQLite 后仍可从 JSONL + artifacts 重放”。
- [ ] Lean V1 是 stub，不要不小心扩大到真实 theorem proving。

## 已做决策（Decisions Made）

- **启动验证保持轻量：** 使用 Python stdlib 检查和 `compileall`；存在 `tests/` 目录时在 `PYTHONPATH=src` 下运行 `pytest tests`。
- **feature list 映射 TDD 阶段：** feat-002 到 feat-008 对应 Phase 1 到 Phase 7。
- **协议边界写入 harness：** protocol core 不能硬编码 factorization 或 Lean 行为。
- **监督语言要求：** `AGENTS.md` 和 `progress.md` 优先用中文维护，方便用户随时监督；技术名词、对象名、命令和文件名可保留英文。
- **V1 技术栈决策：** 采用 Python 3.12+、SQLite、JSON、JSONL、本地文件系统和 pytest；Airflow、Argo Workflows、Temporal、Ray、BOINC 等只作为设计参照和后续迁移候选，不进入 V1 runtime。
- **Package layout 决策：** 采用 `src/` layout；协议核心、存储、插件、执行器、重放、实验分别位于 `tokenshare.core`、`tokenshare.storage`、`tokenshare.plugins`、`tokenshare.executors`、`tokenshare.replay`、`tokenshare.experiments`。
- **字段规格决策：** Phase 1 采用“稳定对象字段 + 版本化 JSON payload + SQLite 可重建索引”的最小规格；协议对象名、字段名、事件类型和 SQLite 表名在 `Doc/TechnicalDocument/2026-06-05-phase-1-minimal-object-field-spec.md` 中分层记录。

## 本轮修改文件（Files Modified This Session）

- `AGENTS.md` - agent 启动流程、范围、规则、完成标准；已改为中文并加入语言要求。
- `feature_list.json` - TokenShare 阶段路线图和 feature 状态。
- `progress.md` - 当前理解、状态、风险、下一步；已改为中文并记录语言要求。
- `session-handoff.md` - 下一轮 restart 摘要。
- `init.sh` - Bash 基线验证。
- `init.ps1` - Windows PowerShell 基线验证。
- `README.md` - 中文项目入口，记录项目定义、V1 范围、非目标、启动命令、仓库地图和当前状态。
- `Doc/TechnicalDocument/2026-06-03-tokenshare-protocol-technical-design.md` - 新增 V1 技术栈决策、依赖更新和开放问题更新。
- `.gitignore` - 忽略 `reference_repos/` 下的第三方源码克隆，仅保留本地索引说明文件。
- `reference_repos/README.md` - 记录参考仓库来源、commit、拉取范围和 package layout 观察重点。
- `src/tokenshare/` - 初始 Python package 骨架。
- `tests/` - 镜像测试目录骨架和 package layout smoke test。
- `Doc/agent-navigation.md` - agent 导航、模块路由和参考源码使用规则。
- `Doc/TechnicalDocument/2026-06-05-phase-1-minimal-object-field-spec.md` - Phase 1 最小对象字段规格、事件 envelope 和 SQLite 可重建索引边界。

## 完成证据（Evidence of Completion）

- [x] 启动验证：`powershell -ExecutionPolicy Bypass -File E:\TokenEcnomic\TokenShare\init.ps1` passed。
- [x] Harness 验证：`node C:\Users\32133\.codex\skills\harness-creator\scripts\validate-harness.mjs --target E:\TokenEcnomic\TokenShare` returned Overall 100/100。
- [x] README 更新后验证：`powershell -ExecutionPolicy Bypass -File E:\TokenEcnomic\TokenShare\init.ps1` passed；输出包含 `python-json-sqlite-ok`、`harness-files-ok`，并因暂无 `tests/` 目录跳过 `pytest`。
- [x] 技术栈决策更新后验证：`.\init.ps1` passed；输出包含 `python-json-sqlite-ok`、`harness-files-ok`，并因暂无 `tests/` 目录跳过 `pytest`。
- [x] 参考源码加入后验证：`powershell -ExecutionPolicy Bypass -File E:\TokenEcnomic\TokenShare\init.ps1` passed；`reference_repos/` 已从 `compileall` 排除，输出包含 `python-json-sqlite-ok`、`harness-files-ok`。
- [x] Package layout 骨架验证：`powershell -ExecutionPolicy Bypass -File E:\TokenEcnomic\TokenShare\init.ps1` passed；输出包含 `python-json-sqlite-ok`、`harness-files-ok`，并运行 `tests\test_package_layout.py`，结果 `1 passed`。
- [x] Agent 导航文档验证：`powershell -ExecutionPolicy Bypass -File E:\TokenEcnomic\TokenShare\init.ps1` passed；`tests\test_package_layout.py` 结果 `1 passed`。
- [x] Harness 结构验证：`node C:\Users\32133\.codex\skills\harness-creator\scripts\validate-harness.mjs --target E:\TokenEcnomic\TokenShare` returned Overall 100/100。
- [x] Agent 导航迁移验证：`powershell -ExecutionPolicy Bypass -File E:\TokenEcnomic\TokenShare\init.ps1` passed；`tests\test_package_layout.py` 结果 `1 passed`。
- [x] Agent 导航迁移后 Harness 结构验证：`node C:\Users\32133\.codex\skills\harness-creator\scripts\validate-harness.mjs --target E:\TokenEcnomic\TokenShare` returned Overall 100/100。
- [x] Phase 1 字段规格文档验证：`powershell -ExecutionPolicy Bypass -File E:\TokenEcnomic\TokenShare\init.ps1` passed；`tests\test_package_layout.py` 结果 `1 passed`。
- [x] 字段规格索引更新后 Harness 结构验证：`node C:\Users\32133\.codex\skills\harness-creator\scripts\validate-harness.mjs --target E:\TokenEcnomic\TokenShare` returned Overall 100/100。

## 下次会话提示（Notes for Next Session）

Feat-001 已完成。Phase 1 最小字段规格已写入 `Doc/TechnicalDocument/2026-06-05-phase-1-minimal-object-field-spec.md`。下一步应按该规格实现窄切片：root task registration、artifact save/read/hash、JSONL event append/read。
