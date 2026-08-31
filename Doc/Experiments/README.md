# TokenShare experiments public package

本目录说明清洗后公开分支中保留的唯一实验设施与数据边界。目标是让读者能从公开路径理解、核验并复现实验输入、配置、结果与最小验证 harness，而不需要进入内部开发文档或顶层论文工作区。

## 保留内容

- 系统本体与运行时：`src/tokenshare/core/`、`src/tokenshare/storage/`、`src/tokenshare/local_runtime/`、`src/tokenshare/protocol_engine.py`。
- 领域插件：Factorization 插件与 Lean proof 插件。
- 最小 executor 能力：真实 provider transport/body descriptor 所需闭包，以及普通离线测试 helper。
- 公开实验代码入口：`src/tokenshare/experiments/`，命令行为 `python -m tokenshare.experiments.cli`，Windows GUI launcher 为 `run_experiments.cmd`。
- 权威 corpus 与 provider config：`benchmarks/experiments/`、`configs/experiments/`。
- 正式发布结果：`results/experiments/`（Task G 迁入）。
- 最小验证入口：当前为 `verification/verify_authoritative_corpus.py`；result/extraction gates 后续迁入。

## 冻结来源身份

本公开包从本地 annotated tag `experiments-pre-cleanup-20260827` 的 peeled commit 抽取：

- tag object SHA：`018ac5c4a960ea10838a79287221e62fad7b1ac7`
- frozen commit SHA：`bb5e637785afb6bd5743e4d89af4c02ab0736204`

Task E 迁入的 20 个 corpus/config/Lean fixture blobs 均由 `verification/verify_authoritative_corpus.py` 与 `benchmarks/experiments/manifest.v1.json` 验证为 byte-identical。历史 source path 只作为 manifest provenance；运行时只使用公开路径。

## 当前公开路径

| 类别 | 路径 |
|---|---|
| Factorization catalog | `benchmarks/experiments/factorization_catalog.v2.jsonl` |
| Lean direct catalog | `benchmarks/experiments/lean_catalog.v1.jsonl` |
| Lean lemma-DAG catalog | `benchmarks/experiments/lean_lemma_graph_catalog.v1.jsonl` |
| Lean checker preflight record | `benchmarks/experiments/lean_checker_preflight.v1.json` |
| Lean semantic authority sidecar | `benchmarks/experiments/lean_environment_semantic_authority.v1.json` |
| Lean fixture project | `benchmarks/experiments/fixtures/lean_proof_project/` |
| Experiment 1 provider config | `configs/experiments/exp1_baseline_provider_config.v3.json` |
| Experiment 5 provider config | `configs/experiments/exp5_siliconflow_provider_config.v3.json` |
| Corpus manifest | `benchmarks/experiments/manifest.v1.json` |

## 实验语义概要

TokenShare experiments 使用同一套协议本体和领域插件运行五组冻结实验：

1. Experiment 1：Factorization 与 Lean 的真实 provider baseline。
2. Experiment 2：复用 Experiment 1 per-unit traces，改变 worker count；provider calls 固定为 0。
3. Experiment 3：复用 Experiment 1 per-unit traces，注入冻结 fault/recovery 场景；provider calls 固定为 0。
4. Experiment 4：复用 Experiment 1 per-unit traces，运行 11 个 mechanism mode 的 mode-blind challenge；provider calls 固定为 0。
5. Experiment 5：使用 SiliconFlow 三个 endpoint 的真实 provider 对照。

roots 在 runner 层串行执行；`worker_count` 只控制单个 root 内部 AI units 的并发。Experiment 1 的 coverage tail 只服务同 profile 下 Experiment 2–4 实际消费的 source roots，不回写 root runtime。

## 不属于公开实验运行依赖的内容

清洗后公开分支不保留旧实验 runner、旧 response bank、旧 publication gate、budget authority、receipt、lineage/evidence closure、旧 selector/request identity、trace-backed delivery 或任何顶层论文工作区文件。顶层 `paper/**` 不属于本包。

## 最小核验命令

在仓库根目录设置显式 `PYTHONPATH` 后运行：

```powershell
$repo = "E:\TokenEcnomic\TokenShareWorktrees\experiments-clean-extraction"
$env:PYTHONPATH = "$repo\src;$repo"
conda run -n tokenshare python verification/verify_authoritative_corpus.py
conda run -n tokenshare python -m pytest tests/experiments/test_authoritative_corpus.py -q
```

Task G 会迁入正式结果与 result verifier；Task H 会在 fresh clean review worktree 中运行完整 extraction gates、compileall 与一个有界本地 Lean checker smoke；不会调用真实 provider，不会重跑 Full 或 LeanAudit。
