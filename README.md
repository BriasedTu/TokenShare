# TokenShare

TokenShare 是一个 Python/SQLite/JSONL 的本地研究原型，用来验证大型任务的递归拆分、分派、验证、合并、结算与事件重放。它不是区块链、生产网络或多租户 AI 平台。

当前 active feature 是 `feat-011`：使用真实 AI API 完成 Factorization 和 Lean 两类论文实验。准确状态看 `feature_list.json` 与 `progress.md`；实验参数只以 `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md` 为准。

## 快速开始

PowerShell：

```powershell
.\init.ps1
```

Bash/Git Bash/WSL：

```bash
./init.sh
```

Fast 档运行 JSON/SQLite、harness、代码 compileall 和 `verification/fast-tests.txt`。feature 完成、提交/合并或发布实验结果前运行：

```powershell
.\init.ps1 -Full
```

涉及 Lean catalog/checker/toolchain/helper 时，按 `AGENTS.md` 和 Lean verification profile 增加 `-LeanAudit`；不要默认 force-all。

## 最小阅读路径

1. `AGENTS.md`
2. `feature_list.json`
3. `progress.md`
4. `session-handoff.md`
5. `Doc/agent-navigation.md`

不要在接管项目时递归加载 `Doc/archive/` 或运行数据。文档分层和大小预算见 `Doc/repository-governance.md`。

## 架构边界

- `src/tokenshare/core/`：协议对象与纯规则。
- `src/tokenshare/storage/`：artifact、append-only event ledger、SQLite index。
- `src/tokenshare/local_runtime/`：scheduler/lease/attempt/worker 的本地生命周期协调。
- `src/tokenshare/plugins/`：Factorization 和真实 Lean 固定计划插件。
- `src/tokenshare/executors/`：deterministic/mock/AI API executor、transport 与 replay。
- `src/tokenshare/experiments/`：实验条件、fault/death/ablation、projection、metrics、report 和 CLI。
- `src/tokenshare/runtime_paths.py`：仓库外运行数据根和历史只读路径映射。

协议 core 不理解 Factorization、Lean、provider 或论文实验策略；插件决定领域 split/parser/verifier/merge；executor 不决定实验 cohort 或 paper eligibility；论文指标必须从持久化 event/artifact 派生。

详细归属见 `Doc/TechnicalDocument/tokenshare_v1_code_map.md`。

## 运行数据不在仓库内

默认数据根是仓库同级的 `TokenShareData/`。以下为本机实例：

```text
E:\TokenEcnomic\TokenShareData\
├── outputs\
│   ├── experiments\
│   └── diagnostics\
├── local\
│   └── supervision\
├── migrations\
└── migration-backups\
```

可用 `TOKENSHARE_DATA_ROOT` 指定另一个仓库外绝对路径。普通实验 CLI 未指定输出目录时写入该数据根；paper runner 必须显式给出全新的 `--output-root`，不会复用固定 `paper_v1` 容器。历史 output 已保持原内容迁出；旧 evidence 中的仓库绝对、`outputs/...` 仓库相对和 `runs/...` suite 相对 artifact 路径只在读取时解析，不得改写或 resume。

## 当前论文实验口径摘要

- Experiment 1：Factorization 500 + Lean 135（3 difficulty × 3 topic family × 每格 15）。
- Experiment 2：Factorization hard 166，20-way split，worker `1/3/7/10/30/50`，每档 2 repeats。
- Experiment 3：五类 rate-fault，加独立 worker-death 条件；正式矩阵 36,126 roots。
- Experiment 4：`FULL + 4` 个 ablation mode，7,725 roots。
- Experiment 5：SiliconFlow 四模型 cohort v3，48 conditions / 1,284 roots / 9,888 first-attempt units。

Experiment 1–4 固定官方 DeepSeek `deepseek-v4-pro`，thinking enabled、high、`timeout_seconds=600`、`max_tokens=300000`。Experiment 5 四模型公共 `600/32768`、单次 provider attempt；GLM/Qwen/MiniMax thinking budget 32768，DeepSeek nonthinking。

这些只是导航摘要；任何冲突都以唯一权威实验设计为准，参数决定台账只解释变更 provenance。当前 Exp5 P0-full 仍为 NO-GO，不应直接启动正式运行。

## 常用入口

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m tokenshare.experiments.run_paper_experiments --help
```

论文实验必须先做 `--plan-only`、配置/identity/budget preflight，并使用全新的输出根。真实 API 会产生费用；没有用户明确授权时不要启动 smoke、pilot 或 formal matrix。

其他回归入口：

```powershell
conda run -n tokenshare python -m tokenshare.experiments.run_all
conda run -n tokenshare python -m tokenshare.experiments.run_ai_profile
conda run -n tokenshare python -m tokenshare.experiments.run_factorization_500_ai
conda run -n tokenshare python -m tokenshare.experiments.run_lean_ai_benchmark
```

未传输出目录时，它们都写入仓库外数据根。deterministic/scripted/diagnostic 结果只能用于回归或校准，不能作为论文正式证据。

## 文档入口

- 仓库治理：`Doc/repository-governance.md`
- Agent 导航：`Doc/agent-navigation.md`
- 协议规格：`Doc/TechnicalDocument/tokenshare_v1_complete_spec.md`
- 当前 code map：`Doc/TechnicalDocument/tokenshare_v1_code_map.md`
- 唯一实验设计：`Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`
- 参数决定：`Doc/TechnicalDocument/tokenshare_experiment_parameter_decision_log.md`
- 历史资料：`Doc/archive/README.md`

如需联网资料，必须按 `Doc/agent-navigation.md` 的规则把论文、源码或文档摘要落库，不能只保留在线链接。
