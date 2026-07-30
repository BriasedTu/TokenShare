# TokenShare 当前进度

更新时间：2026-07-30

本文件只保存当前事实与最近验证。历史过程由 git history、`Doc/archive/` 和仓库外迁移备份保留，不再把逐轮日志追加到启动上下文。

## 当前状态

- Active feature：`feat-011`（Paper Real AI Experiments），状态仍为 `in-progress`。
- 仓库上下文与数据边界维护焦点已完成；`feat-011` 本身仍为 `in-progress`。
- 正式实验唯一权威：`Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`。
- Experiment 1–4 固定使用官方 DeepSeek `deepseek-v4-pro` / `deepseek_v4_pro_exp1_baseline`，thinking enabled、`reasoning_effort=high`、`timeout_seconds=600`、`max_tokens=300000`。
- Experiment 5 当前为 SiliconFlow 四模型 cohort v3；正式 P0-full 仍是 **NO-GO**，原因见 `session-handoff.md`。
- 历史 smoke 输出均为只读 provenance，不能 resume、补写、重分类或升级为论文证据。

## 本轮仓库清理与数据边界

已完成：

- 新增 `Doc/repository-governance.md`，定义 Tier 1/2/3 文档层级、唯一权威和归档规则。
- 新增 `src/tokenshare/runtime_paths.py`：新运行默认写入仓库同级 `TokenShareData`；`TOKENSHARE_DATA_ROOT` 只接受仓库外绝对路径；显式 CLI 输出目录不受默认环境变量阻断。
- 四个通用实验 CLI 默认写入仓库外数据根；paper runner 强制显式指定全新 `--output-root`，不再复用固定历史容器。
- PowerShell smoke launcher 现可正确处理绝对路径，不再把 `E:\...` 拼进仓库路径。
- 历史 evidence 不改写 digest；旧仓库绝对路径、`outputs/...` 仓库相对路径和 `runs/...` suite 相对路径只在读取时解析。Exp1/smoke replay 不再刷新 report、manifest 或其他派生文件。
- 启动验证新增 Tier 1 单文件/总量预算和 `feature_list.v2` 稳定 schema 检查；compileall 只扫描 `src/`、`tests/`、`verification/`，不再递归实验数据与文档。
- 已建立修改前外部备份：`E:\TokenEcnomic\TokenShareData\migration-backups\2026-07-30-context-consolidation-before`。
- 已把 `outputs/`、`local/supervision/` 与 `local/` 根生成日志迁到 `E:\TokenEcnomic\TokenShareData`：101,048 files / 931,156,489 bytes；迁移前后 inventory 与 24 个样本逐文件 hash 一致，仓库内源路径均已消失。manifest：`E:\TokenEcnomic\TokenShareData\migrations\2026-07-30-runtime-data-relocation.json`。
- 已把 40 份旧设计、2 份旧 code map 和 6 份旧 Agent plan/spec 分区移入 `Doc/archive/`；顶层 `Doc/TechnicalDocument` 只保留 7 个按职责路由的文件。
- 已精简 README、导航、code map、唯一实验权威和三个状态文件；Tier 1 总量低于 32 KiB 目标。
- Fast manifest 已覆盖 context/runtime path/launcher 边界；最终代码修改后的 Fast 为 `448 passed, 1 skipped in 20.17s`。

独立 doc review 与 code review 均已 APPROVED。大规模业务模块拆分没有混入本轮；顺序记录在 `Doc/repository-governance.md`，应另立 feature 用 characterization tests 实施。

## 本轮验证证据

- replay/path/smoke isolation 新增边界测试先得到 `6 failed`，修复后 `6 passed in 7.99s`。
- 最终相关回归：`242 passed in 207.34s`；harness 结构/总预算：`15 passed in 0.12s`。
- 真实迁移 suite `paper_exp1_minimal_pilot_v1`：只读加载 8 runs / 206 artifacts；708 个文件逐文件 SHA-256 前后完全一致。
- 独立 code review：APPROVED，复跑 `34 passed in 33.73s`；独立 doc review：APPROVED（无 Critical/Important）。
- 最终 Fast：`448 passed, 1 skipped in 20.17s`。
- 按用户最终指示重跑迁移接线聚焦回归：`47 passed in 36.14s`，且未重建仓库 `outputs/`。
- 用户明确要求只验证迁移接线、不继续全量测试；已终止未完成的 Full，其输出不作为验证证据。本轮未改 Lean 输入，因此未运行 LeanAudit。

## 业务实验的当前阻塞

- Exp5 primary router 修复已经完成并通过规格/质量复核，但正式链路仍为 NO-GO。
- P1：canonical v3 provider-config binding 未完整闭合；8-root smoke 尚未自动产出四 member 的 artifact-backed endpoint smoke evidence；部分/失败运行的 1,284 分母与 missingness 保留仍需验证；六类论文 renderer 尚未全部接入 production CLI/replay。
- P2：原始 transport 异常可能被 `missing_submission_event` 掩盖；部分 blocked 诊断仍可能使用 v2 member IDs。
- 在这些门禁关闭前，不启动 Exp5 8-root smoke，不运行正式 P0-full。

## 范围与安全记录

- 本轮未联网、未调用真实 provider、未修改 catalog 样本量、实验矩阵、模型或请求参数。
- 未运行 LeanAudit/force-all；未 stage、commit、push；未 reset/clean/checkout。
- 三个仓库根 `pytest*` 目录存在 ACL 拒绝，当前作为独立环境遗留项保留，不纳入数据迁移或“仓库完全干净”的声明。
