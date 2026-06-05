# TokenShare Agent 导航

日期：2026-06-05

状态：工作流导航文档。本文只回答“新 AI 遇到问题应先看哪里、代码应放到哪个模块、参考源码应如何使用”。本文不是协议设计规格，不覆盖 TDD，不包含下一步任务提示词。

## 1. 使用时机

新的 AI 在完成 `AGENTS.md` 的启动要求后，如果需要判断事实来源、模块归属、参考源码边界或下一步阅读顺序，应阅读本文。

本文的定位是导航层：

- 不替代 `AGENTS.md` 的工作规则。
- 不替代 `feature_list.json` 的 feature 状态。
- 不替代 `progress.md` 和 `session-handoff.md` 的当前进展。
- 不替代 TDD 的协议设计。
- 不把 `reference_repos/` 变成 TokenShare runtime 依赖。

## 2. 事实源优先级

后续 AI 应按以下顺序判断当前项目事实：

1. `AGENTS.md`：最高优先级工作规则、范围边界、验证和完成标准。
2. `feature_list.json`：当前 active feature 和 feature 状态。
3. `progress.md`：最近进展、风险、验证证据、下一步。
4. `session-handoff.md`：跨会话恢复摘要，尤其是上一轮未解决问题。
5. `Doc/TechnicalDocument/2026-06-03-tokenshare-protocol-technical-design.md`：当前实现导向设计。
6. `Doc/TechnicalDocument/2026-06-05-phase-1-minimal-object-field-spec.md`：Phase 1 最小对象字段、事件 envelope 和 SQLite 可重建索引规格；用于细化 feat-002。
7. `README.md`：项目入口和稳定边界。
8. `Doc/TechnicalDocument/2026-06-02-tokenshare-protocol-kernel-revised-draft.md`：历史讨论稿；用于理解原因，不直接覆盖当前 TDD。
9. `reference_repos/`：外部参考源码；只能用于借鉴模式，不属于 TokenShare runtime。

如果两个文件冲突，应优先相信上面列表中更靠前的文件，并把冲突记录到 `progress.md` 或 `session-handoff.md`。

## 3. 遇到问题时看哪里

| 问题 | 首先阅读 | 辅助阅读 | 注意事项 |
|---|---|---|---|
| 当前要做哪个 feature | `feature_list.json` | `progress.md` | 一次只做一个 active feature。 |
| 启动和验证怎么跑 | `AGENTS.md` | `init.ps1`、`init.sh`、`README.md` | 当前基线会运行 `compileall` 和 `pytest tests`。 |
| V1 做什么、不做什么 | `AGENTS.md` | `README.md`、TDD 第 3、4 节 | 不要扩大到真实区块链、真实分布式 runtime 或真实 Lean proving。 |
| 技术栈是什么 | TDD 第 20 节 | `README.md`、`progress.md` | V1 是 Python 3.12+、SQLite、JSON、JSONL、本地文件系统。 |
| package layout 在哪里 | TDD 第 20.4 节 | `reference_repos/README.md`、`README.md` | 已创建 `src/tokenshare` 和镜像 `tests` 骨架。 |
| 具体代码应该放哪个模块 | 本文第 4 节 | TDD 第 5、6、10、11、20 节 | 先守住协议框架、插件、执行器三层边界。 |
| 需要借鉴已有项目结构 | `reference_repos/README.md` | 对应外部源码目录 | 只能借鉴思路，不引入为 runtime 依赖，不复制大段实现。 |
| 需要设计对象字段 | `Doc/TechnicalDocument/2026-06-05-phase-1-minimal-object-field-spec.md` | TDD 第 6、10、11、20、21、23 节；协议讨论稿第 6、7、10、11 节 | 先区分协议对象名、字段名、事件类型和 SQLite 表名，再写实现。 |
| 需要更新状态 | `progress.md` | `feature_list.json`、`session-handoff.md` | 没有验证证据，不要标记完成。 |

## 4. 模块路由

当前 package layout 已确定为 `src/` layout：

```text
src/
  tokenshare/
    core/
    storage/
    plugins/
      factorization/
      lean_stub/
    executors/
    replay/
    experiments/

tests/
  core/
  storage/
  plugins/
    factorization/
    lean_stub/
  executors/
  replay/
  experiments/
```

模块职责：

| 目录 | 放什么 | 不放什么 |
|---|---|---|
| `tokenshare.core` | 协议对象、状态枚举、任务图、状态机、协议配置和不变量。 | factorization/Lean 领域规则、文件系统细节、executor 调用细节。 |
| `tokenshare.storage` | `ArtifactStore`、JSONL `EventLedger`、SQLite materialized index、本地路径和内容哈希实现。 | 协议决策逻辑、插件验证规则。 |
| `tokenshare.plugins` | 插件契约、descriptor、factorization PoC、Lean stub PoC。 | 协议状态机推进、租约调度、正式输出绑定。 |
| `tokenshare.executors` | 执行器契约、mock executor、确定性程序执行器、AI stub executor。 | 任务图修改、验证结论绑定、结算逻辑。 |
| `tokenshare.replay` | 状态重放、审计重放、replay consistency、no-double-settlement 检查。 | 重新调用 AI 或 executor 来生成历史输出。 |
| `tokenshare.experiments` | experiment runner、fault simulation、metrics/report。 | 协议核心不可替代的状态事实。 |
| `tests/*` | 与 `src/tokenshare/*` 镜像的单元、集成、故障和 replay 测试。 | 外部参考项目测试。 |

## 5. 参考源码使用规则

`reference_repos/` 是研究材料，不是项目源码。使用规则：

- 需要 package layout 思路时，先看 `reference_repos/README.md`。
- 可以打开外部项目源码观察命名、目录边界和测试组织。
- 不要把外部项目加入 TokenShare runtime dependency。
- 不要让 `pytest` 或 `compileall` 扫描外部项目；启动脚本已排除它们。
- 不要复制大段外部代码；如果借鉴设计，应在 TDD 或 `progress.md` 中写清楚借鉴的是哪类边界思想。

## 6. 状态更新指引

当导航规则、模块归属、验证命令或参考源码边界变化时，应同步更新：

- `AGENTS.md`：只保留强制启动入口和最高优先级规则。
- 本文：维护事实源、模块路由和参考源码使用规则。
- `README.md`：维护人类入口和仓库地图。
- `progress.md` / `session-handoff.md`：记录本轮变化、验证证据和下一步。
