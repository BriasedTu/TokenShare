# AGENTS.md

本仓库是 TokenShare 的干净公开研究分支。系统本体与实验设施共享同一套协议对象、运行时、存储、插件和 executor 合同；唯一实验包是 `src/tokenshare/experiments/`。

## 本地数据边界

- 从当前仓库根目录执行验证；本地设施和工具链使用实际路径，不依赖历史工作树。
- `TokenShareData/` 保存完整本地数据；其中 `sources/official-full-run` 是指向历史只读来源的既有目录链接。处理目录链接时必须区分链接本身与真实数据，禁止递归清理其目标。
- 不自动创建或恢复 L:、M: 等虚拟盘符。复用主目录下的实际路径和已准备的 Lean 缓存。
- 论文源文件、写作交接和实施计划属于本地归档，不作为公开运行依赖；清理时保留作者源文件。

## 权威路由

- 系统代码：`src/tokenshare/core/`、`storage/`、`local_runtime/`、`plugins/`、`executors/`。
- 实验代码：`src/tokenshare/experiments/`；不得建立第二套 runner、平行实验包或兼容 wrapper。
- 正式题库：`benchmarks/experiments/`；正式配置：`configs/experiments/`。
- 正式结果：`results/experiments/`；验证入口：`verification/`。
- 公开实验文档：`Doc/Experiments/`。

## 工作规则

1. 采用风险驱动验证：优先验证核心行为、复杂边界和已知缺陷，不为覆盖率堆叠测试。
2. 未获用户明确授权时，禁止调用真实 provider、使用真实 API key 或启动有成本的实验运行。
3. `TokenShareData/` 保存本地 raw 数据并保持 ignored；本地 secret 与 cache 只能放在被 `.gitignore` 覆盖的位置。
4. 配置中只记录 secret 的环境变量名，不提交 secret；生成的非确定性输出必须持久化后再参与 replay。
5. 不重新引入已排除的旧 runner、平行实验设施、历史验证 profile 或过程状态文档。
6. 修改系统、实验、题库或结果后，运行与实际风险匹配的 focused verification，并保留可复核证据。

## 默认验证边界

- 默认 pytest 与 focused 基线验证禁止启动真实 Lean/Lake；真实编译测试必须显式传入 `--run-lean-integration`。完整 Lean check 仅用于首次验收或证明、环境、检查器相关变化，不作为日常基线。
- Lean 环境配置时准备并锁定依赖和编译缓存；后续优先核对并复用有效验证证据，避免无变化的重复完整检查。
- 系统改动：运行 `verification/run_verification.py --focused system`。
- 实验改动：运行 `verification/run_verification.py --focused experiments`。
- 题库改动：运行权威 corpus verifier，必须比较完整 identity arrays 与文件 SHA。
- 正式结果只允许只读验证；不得用 reducer 覆盖已发布文件。

## 本地正式 Full run 入口

- `TokenShareData/sources/official-full-run/` 是完整正式 Full run（Experiment 1–5）的只读本地逻辑入口。
- 后续 Experiment 3/4 以该目录作为 `--source-run-dir`，只读取其中的 Experiment 1 traces；禁止 provider fallback。
- 新输出必须写入 `TokenShareData/outputs/experiments/<new-run-id>/`；禁止将 source 作为输出目录或改写 source。
