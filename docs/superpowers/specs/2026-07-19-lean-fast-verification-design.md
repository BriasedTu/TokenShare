# TokenShare Lean 快速验证设计

日期：2026-07-19
状态：已批准，待实施

## 背景与根因

TokenShare 已把默认启动验证拆成快速档 `.\init.ps1` / `./init.sh` 与完整档 `.\init.ps1 -Full` / `./init.sh --full`，但 Lean 相关定向测试仍可能耗时数分钟。实测结果如下：

- 冷启动 `lake build TokenShare.LemmaGraphOracle` 约 33.7 秒；
- 同一模块后续增量 build 约 0.49 秒；
- 对整个 `LemmaGraphOracle.lean` 运行一次 Lean 检查约 0.6–0.8 秒。

根因不是单次 Lean 编译，而是 `check_lean_proof()` 为每个 case、lemma node 和 root 分别启动 `lake env lean`。catalog preflight、Task 14 golden evidence、adapter 和 merge 测试会把该行为放大为几十至数百个 Lean 子进程。现有 Python 内存缓存只能在同一进程内复用相同输入，不能避免首次遍历所有不同 node，也不能跨 pytest 进程复用。

## 目标

1. 在所有 agent 必读入口中明确标出 Lean 慢路径，默认禁止无必要的完整 Lean 测试。
2. 提供跨 PowerShell/Bash 的专用快速 Lean 验证入口。
3. 快速入口仍真实调用本地 Lean，并覆盖 exact Task 14 catalog proof candidates 与一条端到端 split/check/merge/root-recheck 路径。
4. 保留完整逐 case/node checker 验证，供 Lean 核心修改、集成 checkpoint 和论文结果发布前使用。
5. 不改变生产 checker、正式实验 evidence、paper eligibility 或 provider 调用语义。

## 非目标

- 不减少 `init.ps1 -Full` / `init.sh --full` 的完整测试范围。
- 不把快速验证输出冒充 Task 14 golden evidence 或论文证据。
- 不新增跨进程 accepted-checker evidence 缓存。
- 不修改真实 AI provider、预算、实验条件或协议核心。
- 不通过并发启动大量 Lean 进程换取速度；Windows 本地冷启动下这会增加资源竞争。

## 方案选择

采用“批量 Lean 验证 + 一个端到端代表样本”的方案。

未采用的方案：

- 只缩小测试集合：实现简单，但无法验证 exact catalog 中全部 proof candidates。
- 持久化 checker 结果缓存：重复运行最快，但可能复用与当前 catalog、toolchain 或环境不一致的陈旧 evidence，不符合正式实验的审计边界。

## Agent 决策规则

`AGENTS.md` 在项目简介之后、启动流程之前新增醒目章节，并使用以下规则：

| 改动范围 | 默认验证 | 何时升级 |
|---|---|---|
| 非 Lean 模块、文档、Exp1–5 独立模块 | 默认 `init` 快速档和 owned tests | 不主动运行完整 Lean |
| Lean metadata、catalog selection、readiness、纯结构规则 | 专用 Lean 快速入口 + 精确 Python node IDs | 发现真实 checker/assembly 风险时升级 |
| checker、proof rendering、oracle module、split/merge/root recheck | 专用快速入口后运行受影响的精确真实 Lean tests | feature checkpoint 运行完整 Lean |
| Prompt H、合并、发布实验结果 | 完整 `init` 档 | 必须运行一次，不在每个并发 agent 重复运行 |

禁止把整个 `tests/plugins/lean_proof`、`test_lean_task14_readiness.py`、`test_lean_lemma_graph_catalog.py` 或 `pytest tests` 当作普通启动检查。必须先运行最小 node ID 或 `-k` 选择；只有风险边界确实需要时才扩大范围。

并发 Prompt Pack 的共同规则同步为：C–G 独立模块 agent 运行默认快速档、owned tests 和必要 impact tests；完整 Lean/full suite 由修改 Lean 边界的 agent 或 Prompt H integration owner 集中运行一次。

## 快速验证入口

新增两个薄 wrapper：

- PowerShell：`./verify-lean-fast.ps1`
- Bash/Git Bash/WSL：`./verify-lean-fast.sh`

二者调用同一个 Python 实现和同一个测试清单，避免入口漂移。入口不接受真实 provider 配置，不访问网络，并在任一步失败时返回非零退出码。

执行顺序：

1. 检查固定 Lean/lake/toolchain/project/environment digest。
2. 对 `TokenShare.LemmaGraphOracle` 执行一次 `lake build`，利用 Lake 自身的标准增量构建产物；不生成 TokenShare checker evidence cache。
3. 从 readiness manifest 读取 exact 135 selected case IDs，并与 catalog、selection digest、checker-backed/preflight 状态交叉核对。
4. 按 case 的拓扑顺序把 selected cases 的 node theorem payload 与 `node_proof_sources` 合成为一个临时 Lean source，在一次 `lake env lean` 中验证。临时文件结束后删除。
5. 选择 readiness manifest 中固定的 hard-frontier/induction golden case，执行一次真实 deterministic split、node checker、dependency-aware merge 和 root recheck；必须保持 `provider_calls_made=0`。
6. 运行秒级 Python 契约/负向测试，验证 semantic fingerprint、readiness fail-closed、manifest/CLI 契约及快速入口自身。

批量 source 仅是快速回归工具：它必须使用当前 catalog 的原始 theorem payload、proof source、imports、namespace、parameters、statement 和 dependency order，不得把 oracle module build 成功等同于每个 JSON proof candidate 成功。Lean 诊断需要映射回 case ID/node ID；任一失败使整个快速入口失败。

## 组件与文件边界

计划新增：

- `verification/lean_fast_verify.py`：批量 source 构造、manifest/catalog 对账、Lean 子进程执行和结构化摘要。
- `verification/lean-fast-tests.txt`：快速入口的精确 pytest node IDs/路径。
- `verify-lean-fast.ps1`、`verify-lean-fast.sh`：环境与统一入口。
- `tests/test_lean_fast_verification_profile.py`：静态入口/清单契约和批量 source 单元测试。

计划修改：

- `AGENTS.md`：最醒目的 Lean 慢路径规则和决策表。
- `Doc/TechnicalDocument/2026-07-18-feat-011-parallel-experiment-prompt-pack.md`：并发 agent 验证责任。
- `README.md`、`Doc/agent-navigation.md`：命令索引。
- `progress.md`、`session-handoff.md`：记录新验证策略与证据。

除非 TDD 证明不可避免，不修改 `check_lean_proof()`、paper catalog loader、Lean adapter、merge policy 或正式 evidence schema。

## 失败与审计语义

- toolchain、environment digest、catalog digest 或 selection digest 漂移：快速入口 fail closed。
- selected case 缺失、重复、非 checker-backed 或不是 preflight-passed：fail closed。
- batch source 任一 theorem 失败：输出稳定的 case/node 映射并返回非零。
- 端到端样本任一 split/check/merge/root-recheck 阶段失败：返回非零。
- 快速入口输出必须明确标记 `verification_profile=lean_fast`、`paper_eligible=false`、`provider_calls_made=0`，不得写入正式实验 output root。
- 标准 `.lake/build` 增量产物可以复用；TokenShare artifact/checker/evidence 不跨运行缓存。

## TDD 与验收

先写 RED，证明：

1. 两个 wrapper 必须引用同一个 Python 实现和测试清单；
2. 快速清单不能包含完整 Lean 目录或已知全量慢测试；
3. batch source 覆盖 exact selected IDs，缺失、重复或 digest drift 会失败；
4. batch source 保留 theorem 语义和 dependency order；
5. 输出明确是非论文 evidence，provider calls 为零；
6. `AGENTS.md` 和 Prompt Pack 含醒目慢路径规则。

GREEN 后验证：

- 静态契约和 batch builder 单元测试；
- `./verify-lean-fast.ps1` 实际成功及耗时记录；
- Bash 语法/参数验证，环境允许时实际执行；
- 默认 `.\init.ps1` / `./init.sh` 仍为快速档；
- 与 Lean 边界相关的精确真实测试；
- 最后由 integration checkpoint 运行一次 `.\init.ps1 -Full` 或 `./init.sh --full`，不在每个并发模块重复运行。

性能验收目标：热环境下快速 Lean 入口明显低于逐 node 全量测试；冷环境只承担一次约 30–40 秒的 Lake build，不再按 135 cases 重复 build。若 batch source 本身超过 60 秒，应记录分阶段耗时并重新评估 source 分片，但不得退回每 node 一个进程。
