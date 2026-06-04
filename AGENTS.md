# AGENTS.md

TokenShare 是一个早期本地研究原型，目标是验证一种协议：把大型任务递归拆分、分派、验证、合并、结算，并能从事件日志重放。当前目标是用 Python/SQLite/JSONL 做本地实现，跑通 factorization 和 Lean stub proof 两个 proof-of-concept 实验。

## 启动流程（Startup Workflow）

写代码前（Before writing code）：

1. 确认当前工作目录是仓库根目录。
2. 完整阅读本文件。
3. 阅读当前设计资料：
   - `Doc/TechnicalDocument/2026-06-03-tokenshare-protocol-technical-design.md`
   - `Doc/TechnicalDocument/2026-06-02-tokenshare-protocol-kernel-revised-draft.md`
4. 运行基线验证：
   - PowerShell：`.\init.ps1`
   - Bash/Git Bash/WSL：`./init.sh`
5. 阅读 `feature_list.json`，选择且只选择一个未完成 feature。
6. 阅读 `progress.md` 和 `session-handoff.md`，确认当前状态和未解决决策。

如果基线验证失败，先修复验证问题，再新增范围。

## 项目边界（Project Boundaries）

V1 是本地可复现实验用的协议内核，不是生产网络。必须保持协议框架、任务插件、执行器三层边界清楚。

V1 范围内：

- 协议对象、状态机、任务图、租约、执行尝试、artifact 引用、append-only event ledger。
- 使用本地文件系统、SQLite、JSON、JSONL 做 artifact 和事件存储。
- 带固定版本的插件注册表和执行器注册表。
- factorization 插件和 Lean stub 插件，作为协议实验对象。
- offline、slow、executor_error、invalid_output、late_submission 五类故障模拟。
- 指标报告、状态重放、审计重放、sandbox 结算。

V1 范围外：

- 真实区块链、钱包、智能合约或真实代币支付。
- 真实分布式网络、HTTP worker pool 或 P2P runtime。
- 生产级身份、权限、反女巫或拜占庭容错系统。
- 完整 Web UI、动态第三方插件市场、生产 AI API 集成、完整 Lean theorem proving。

## 语言要求（Language Policy）

- `AGENTS.md` 和 `progress.md` 必须优先用中文维护，方便用户随时监督。
- 技术名词、代码标识、命令、文件名、对象名可以保留英文，例如 `TaskUnit`、`EventLedger`、`feature_list.json`。
- 如果为了兼容 harness 审计脚本需要英文锚点，可以把英文放在中文标题或句子的括号中。

## 工作规则（Working Rules）

- 一次只做一个 feature（One feature at a time）：以 `feature_list.json` 作为状态源。
- 协议优先：不要把 factorization 或 Lean 行为硬编码进协议核心。
- 需要证据：没有验证输出，不要把 feature 标记为完成。
- 持久化非确定性输出：恢复时不能重新调用 AI 或 executor 来假装结果一致。
- schema/version 决策必须显式：event、plugin、artifact 格式都要支持 replay。
- 保持范围（Stay in scope）：不要修改与当前 feature 无关的文件。
- 当命令、架构、范围变化时，同步更新 docs 和 harness。

## 完成标准（Definition of Done）

一个 feature 只有在以下条件全部满足时才算完成（done only when）：

- 目标行为已经实现，或目标设计产物已经完成。
- 相关验证命令实际运行成功。
- 验证证据已经写入 `feature_list.json` 或 `progress.md`。
- 如果修改了协议、event、artifact schema，必须同步记录。
- 仓库仍然可以通过 `.\init.ps1` 或 `./init.sh` 重新启动验证，保持 restartable 和 clean。

## 验证命令（Verification Commands）

当前启动验证：

```bash
python -c "import json, sqlite3; print('python-json-sqlite-ok')"
python -m compileall .
python -m pytest
```

`init.ps1` 和 `init.sh` 会无条件运行前两个检查；只有存在 `tests/` 目录时才运行 `pytest`。

## 结束会话（End of Session / Before ending）

结束会话前：

1. 更新 `progress.md`，记录当前状态、验证证据和下一步。
2. 更新 `feature_list.json`，记录 feature 状态和证据。
3. 在 `session-handoff.md` 中记录未解决风险或决策。
4. 保持仓库足够干净，让下一轮可以立即运行验证脚本。
