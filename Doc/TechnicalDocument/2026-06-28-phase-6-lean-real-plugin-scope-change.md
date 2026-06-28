# Phase 6 Lean 真实证明插件范围变更记录

日期：2026-06-28

状态：范围变更记录。本文覆盖旧文档中“Lean stub / synthetic fixture only / 完整 Lean theorem proving 属于 V1 外”的旧口径；后续 Lean 插件字段规格和实现计划必须以本文为准。

## 1. 变更原因

用户确认：单纯 stub 插件不足以支撑论文实验，Phase 6 第二个插件必须具备真实 Lean 形式化证明能力。虽然这扩大了早期 V1 范围，但论文交付时间优先，当前实现路线必须调整。

## 2. 新范围

Phase 6 Lean 插件现在是 **真实 Lean 形式化证明插件**，不是 `Lean stub proof`。

V1 内必须实现：

- 输入为 Lean 代码形式的 theorem / proof-state artifact，而不是自然语言命题。
- 插件内 deterministic parser / normalizer / split strategy 自动识别 Lean 命题结构，并生成 `DecompositionProposal` / `MergePlan`。
- 拆分算法无 AI 介入；AI 或其他 executor 只能尝试生成 proof candidate，不能决定协议级子任务。
- 验证使用固定本地 Lean toolchain / lake project / library context 真实检查 proof artifact。
- `EnvironmentRef` 必须记录 Lean 可执行文件、toolchain、lake project、library/import set、namespace/context、配置 digest 和展示摘要。
- checker stdout/stderr、exit code、timeout、diagnostics、normalized theorem/proof digest、proof artifact ref 必须持久化，replay 不得重新运行 Lean 来补历史事实。
- 至少跑通一条 direct proof 路径和一条 decomposition / 子证明合并路径。
- 若使用 miniF2F 或其他 benchmark 子集，必须固定版本、problem IDs、split 和本地材料索引；不允许只写在线链接。

## 3. 仍然不进入 V1 的内容

以下内容仍不属于 V1：

- 生产级 theorem-proving 平台。
- LeanDojo 训练、DPR/premise retrieval 训练管线或 best-first search 平台。
- 动态第三方 proof plugin 市场。
- 生产级多租户 Lean 服务。
- 把 Lean 规则硬编码进 `tokenshare.core`。

## 4. 实现边界

Lean 领域逻辑属于 `tokenshare.plugins` 下的 Lean 插件。协议核心只看到版本化 descriptor、typed artifacts、validator policy、split strategy result、verification report、canonical output、merge plan 和 event。

当前源码中已有 `src/tokenshare/plugins/lean_stub/` 占位目录。后续实现可以选择：

- 保留包路径作为兼容壳，但 descriptor / docs / tests 使用 `lean_proof` 或 `lean_real_proof` 身份；
- 或新增 `src/tokenshare/plugins/lean_proof/`，并把旧 `lean_stub` 标记为废弃兼容入口。

该选择需要在 Lean 插件字段规格中一次性固定，并同步 code map。

## 5. 当前环境风险

2026-06-28 启动检查后，当前 Windows PATH 未发现 `lean` 或 `lake` 命令。后续实现真实 Lean checker 前必须先建立可复现 toolchain：

- 固定 Lean / lake 版本。
- 固定 lake project 或本地 Lean fixture project。
- 将环境摘要写入 `EnvironmentRef`。
- 让 baseline 或 targeted test 能在没有全局 Lean 时明确 skip / fail fast，而不是误报 checker 成功。

## 6. 文档同步要求

旧表述中以下口径均已废弃：

- “Lean stub proof”。
- “Lean V1 只使用 synthetic fixture checker”。
- “V1 不接入真实 Lean checker / mathlib / miniF2F 子集”。
- “完整 Lean theorem proving 属于 V1 范围外”。

新的准确口径是：

> V1 不做生产级 theorem-proving 平台，但 Phase 6 必须实现本地真实 Lean checker 驱动的 Lean 形式化证明插件；插件拆分算法无 AI 介入，验证环境固定、可审计、可重放。
