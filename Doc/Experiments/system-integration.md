# System integration contract

本文件描述公开实验设施如何接入 TokenShare 系统本体。公开 experiments 代码应调用现有公共接口，不复制协议核心，也不读取旧实验设施补数据。

## Root lifecycle

每个 root 使用独占的运行对象：

1. 创建该 root 的 `ArtifactStore` 与 `EventLedger`。
2. 创建 `ProtocolEngine` 与 `ProtocolRunCoordinator`。
3. 创建该 root 独占的 Factorization 或 Lean runtime adapter。
4. 选择 worker backend。
5. 构造 `ProtocolRunRequest`，只调用一次 `run_root()`。
6. 从 result、ledger、store、adapter 与 hooks 中投影普通原始字段。
7. Experiment 1 source root 在协议终态后执行 coverage tail；非 source root 写零 tail 资源并结束。
8. append 一条 normalized root JSONL 后，才开始下一个 root。

## Factorization

Factorization root input 使用公开 catalog 中的 case object。插件负责确定性 split、range request、parser、verifier 与 merge；实验层只选择 provider/fixed trace 来源和 worker/fault/mode 条件。

正确性不来自 provider 文本，而来自最终 `prime_factorization` artifact 与独立 product check。

## Lean

Lean root input 使用公开 lemma-DAG catalog 与公开 fixture project：

- `benchmarks/experiments/lean_lemma_graph_catalog.v1.jsonl`
- `benchmarks/experiments/fixtures/lean_proof_project/`

Lean plugin 负责 fixed lemma-DAG units、dependency path、proof normalization、child checker、merge assembly 与 root recheck。正式 proof candidate 必须由真实 local Lean checker 接受后才能成为 canonical proof。

semantic authority sidecar 保留冻结逻辑路径与 digest；运行时读取公开 physical path。这样既保持冻结环境 identity，又避免公开运行依赖旧目录。

## AI provider boundary

Experiment 1/5 的 provider caller 只接收冻结 single entry：

- configured/requested/resolved model 必须记录。
- raw response、usage、latency、provider request start、HTTP/error 摘要必须落盘。
- API key 只能存在于当前进程环境和 transport 调用栈，不能进入 tracked config、raw output、artifact metadata、event、日志或错误文本。

Experiment 2–4 不创建 provider caller，不允许 transport fallback。

## Fixed-response trace boundary

fixed-response executor 只读同 profile 的 Experiment 1 per-unit trace。命中后必须普通字段核对：

- Factorization：`candidate_start/candidate_end`
- Lean：`lemma_node_id/dependency_path`

source attempt 的 raw/result/usage/latency/cost 原样复用；Experiment 3 的扰动字段另行记录，不改写 source。

## 不允许的运行时依赖

公开 experiments 不得 import、调用或读取旧 runner、旧 response-bank authority、budget authority、receipt、publication gate、lineage/evidence closure、旧 request identity 或旧输出目录。历史 source path 只允许出现在 provenance/manifest/logical-key 字段中，不能作为运行时输入路径。
