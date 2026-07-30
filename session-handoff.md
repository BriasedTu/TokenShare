# Session Handoff

更新时间：2026-07-30

本文件只保留下一位 Agent 开工所需事实。历史交接由 git history、`Doc/archive/` 和仓库外迁移备份保存。

## 开工顺序

1. 完整阅读 `AGENTS.md`。
2. 阅读 `feature_list.json`、`progress.md` 和本文件。
3. 阅读 `Doc/agent-navigation.md`，再按任务进入 Tier 2。
4. 运行 `.\init.ps1`；开始新的 feature、提交/合并或发布时按 `AGENTS.md` 运行 Full。

## 当前工作

- Active feature：`feat-011`；`repository-context-and-data-boundary` 维护焦点已完成，实施计划已归档到 `Doc/archive/agent-plans/`。
- 已实现仓库外 runtime path、通用 CLI 默认路径、paper 显式新 root、PowerShell 绝对路径、三种历史 artifact 路径的只读解析和启动上下文预算。
- 数据迁移已完成：`outputs/` 100,937 files / 930,569,400 bytes，`local/supervision/` 87 / 504,924 bytes，`local/` 根生成日志 24 / 82,165 bytes，合计 101,048 files / 931,156,489 bytes。
- 外部目标：`E:\TokenEcnomic\TokenShareData`。修改前备份位于 `migration-backups/2026-07-30-context-consolidation-before`；迁移 manifest 位于 `migrations/2026-07-30-runtime-data-relocation.json`。
- 迁移前后 inventory 和 24 个确定性样本逐文件 hash 一致；仓库内 `outputs/`、`local/supervision/` 和 `local/*.(log|exit)` 已消失。没有 junction/symlink。
- 三个仓库根 `pytest*` 目录 ACL 拒绝：不要移动、删除或声称已清理；它们不是正式迁移范围。

## 不可破坏的边界

- 不改写历史 output 内的绝对路径、execution-plan digest、budget digest 或 evidence；旧输出迁移后只读，不 resume。
- `resolve_persisted_data_path()` 支持旧仓库绝对路径、`outputs/...` 仓库相对路径和 `runs/...` suite 相对路径；只解析读取位置，不改 evidence。
- 通用实验 CLI 默认写入仓库同级 `TokenShareData`；paper runner 必须显式指定全新 `--output-root`。无效默认环境不能阻断合法显式路径，smoke 不能写入正式 paper boundary。
- Exp1/smoke replay 必须只读，不生成 report、不刷新 evidence manifest。
- 不修改 protocol/plugin/executor 三层边界，不新增攻击/安全工程，不改变权威实验矩阵。
- 工作树中的既有或本轮修改不得用 reset/clean/checkout 清除；未经用户授权不 stage/commit/push。

## Feat-011 业务状态

- 唯一权威：`Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`。
- Exp1–4：官方 DeepSeek `deepseek-v4-pro` / `deepseek_v4_pro_exp1_baseline`，thinking enabled、high、`600/300000`。
- Exp5：SiliconFlow 四模型 cohort v3，48 conditions / 1,284 roots / 9,888 first-attempt units，公共 `600/32768`、单次 attempt；GLM/Qwen/MiniMax thinking budget 32768，DeepSeek nonthinking。
- Exp5 正式链路仍是 NO-GO：canonical v3 provider-config binding、artifact-backed 8-root smoke evidence、部分/失败分母与 missingness、production renderer/replay 仍有 P1 缺口。
- 历史 Exp1–4、Exp3–4、Exp5 smoke/diagnostic 全部只读，不能升级为 paper evidence。

## 当前验证

- 最终相关回归：`242 passed in 207.34s`；真实迁移 suite 只读加载 8 runs / 206 artifacts，708 文件 hash 前后不变。
- 独立 code/doc review：APPROVED；code reviewer 独立复跑 `34 passed in 33.73s`。
- 用户指定的迁移接线最终聚焦回归：`47 passed in 36.14s`，且未重建仓库 `outputs/`。

用户明确要求不继续本轮 Full，只保留迁移接线定向验证；未完成的 Full 已终止且不计为证据。下一步回到 feat-011 P1 门禁，获得用户授权前不启动真实 API smoke/formal。
