# AGENTS.md

TokenShare 是一个早期本地研究原型，目标是验证一种协议：把大型任务递归拆分、分派、验证、合并、结算，并能从事件日志重放。当前目标是用 Python/SQLite/JSONL 做本地实现，跑通 factorization、Lean stub proof 和 structured report stub 三类 proof-of-concept 实验。

## 启动流程（Startup Workflow）

写代码前（Before writing code）：

1. 确认当前工作目录是仓库根目录。
2. 完整阅读本文件。
3. 阅读导引：
   - `Doc/agent-navigation.md`（agent 导航、模块路由和外部参考资料落库规则）
4. 运行基线验证：
   - PowerShell：`.\init.ps1`
   - Bash/Git Bash/WSL：`./init.sh`
5. 阅读 `feature_list.json`，选择且只选择一个未完成 feature。
6. 阅读 `progress.md` 和 `session-handoff.md`，确认当前状态和未解决决策。
7. 如果需要判断代码应该放在哪个模块、哪些外部参考资料可借鉴，先看 `Doc/agent-navigation.md`。
8. 如果本轮需要联网查找资料，必须按 `Doc/agent-navigation.md` 的“外部参考资料落库与使用规则”执行本地落库和文档同步。

如果基线验证失败，先修复验证问题，再新增范围。

## 项目边界（Project Boundaries）

V1 是本地可复现实验用的协议内核，不是生产网络。必须保持协议框架、任务插件、执行器三层边界清楚。

V1 范围内：

- 协议对象、状态机、任务图、租约、执行尝试、artifact 引用、append-only event ledger。
- 使用本地文件系统、SQLite、JSON、JSONL 做 artifact 和事件存储。
- 带固定版本的插件注册表和执行器注册表。
- factorization 插件、Lean stub 插件和 structured report stub 插件，作为协议实验对象。
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
- 代码注释使用中文，尤其是解释代码作用的注释，注释中的技术名词、代码标识、命令、文件名、对象名可以不遵守此要求。

## 工作规则（Working Rules）

- 一次只做一个 feature（One feature at a time）：以 `feature_list.json` 作为状态源。
- 协议优先：不要把 factorization、Lean 或 structured report 行为硬编码进协议核心。
- 需要证据：没有验证输出，不要把 feature 标记为完成。
- 持久化非确定性输出：恢复时不能重新调用 AI 或 executor 来假装结果一致。
- schema/version 决策必须显式：event、plugin、artifact 格式都要支持 replay。
- 外部资料必须落库：凡是联网查到且被用于修改设计、代码、测试或项目文档的论文、技术报告、开源项目或工程文档，都必须在本地保存可复查材料，并同步更新对应索引文档；不能只在回答或 TDD 中留下在线链接。
- 编码和命令固定：在本 Windows 仓库中，读取或检查中文 Markdown、JSON、脚本和代码时默认使用 PowerShell，并显式指定 UTF-8，例如 `Get-Content -Encoding UTF8`、`Select-String`、`Get-ChildItem`；常规检索不要使用 `rg`，避免编码、换行和输出格式反复造成误判。
- 保持范围（Stay in scope）：不要修改与当前 feature 无关的文件。
- 当命令、架构、范围变化时，同步更新 docs 和 harness。

## 完成标准（Definition of Done）

一个 feature 只有在以下条件全部满足时才算完成（done only when）：

- 目标行为已经实现，或目标设计产物已经完成。
- 相关验证命令实际运行成功。
- 验证证据已经写入 `feature_list.json` 或 `progress.md`。
- 如果修改了协议、event、artifact schema，必须同步记录。
- 如果使用了联网资料，论文/报告已经下载或转写到 `Doc/TechnicalDocument/tokenshare-paper-tex/` 并更新论文映射；开源项目已经浅克隆或 sparse checkout 到 `reference_repos/` 并更新 `reference_repos/README.md`；普通在线文档已经记录来源、访问日期、本地摘要和影响范围。
- 仓库仍然可以通过 `.\init.ps1` 或 `./init.sh` 重新启动验证，保持 restartable 和 clean。

## 验证命令（Verification Commands）

当前启动验证：

```bash
conda run -n tokenshare python -c "import json, sqlite3; print('python-json-sqlite-ok')"
conda run -n tokenshare python -m compileall -x "reference_repos" .
PYTHONPATH=src conda run -n tokenshare python -m pytest tests
```

`init.ps1` 和 `init.sh` 默认使用 `conda run -n tokenshare python`；可通过 `TOKENSHARE_CONDA_ENV` 临时覆盖环境名。脚本会无条件运行前两个检查；`reference_repos/` 是外部参考源码目录，不参与 `compileall`；只有存在 `tests/` 目录时才在 `PYTHONPATH=src` 下运行 `pytest tests`。

## 结束会话（End of Session / Before ending）

结束会话前：

1. 更新 `progress.md`，记录当前状态、验证证据和下一步。
2. 更新 `feature_list.json`，记录 feature 状态和证据。
3. 在 `session-handoff.md` 中记录未解决风险或决策。   
4. 如果本轮使用了联网资料，确认本地论文、参考源码或在线文档摘要已经落库，并更新 `Doc/agent-navigation.md` 指向的相关索引。
5. 保持仓库足够干净，让下一轮可以立即运行验证脚本。
6. 若会话在讨论项目中文档的内容并要求做出修改，则在修改后需要二次审核是否有需要修正的旧表述。
7. 若更新了代码，则需要同步更新code map。
