# feat-011 Experiment 3/4/5 smoke 与论文证据闭环实施计划

日期：2026-07-31

设计：`2026-07-31-feat-011-exp345-smoke-and-paper-evidence-repair-design.md`

## 工作包 A：Exp5 smoke evidence 与 formal binding

1. 在现有 model policy / smoke output contract 上先写 RED：生产路径没有 evidence bundle、虚构 hash 能通过、formal 未绑定 source evidence digest。
2. 实现从 8-root smoke persisted artifacts 生成四 member evidence 的纯派生函数和 CLI 接线。
3. 实现 artifact index + bytes/hash + suite/config/cohort/member identity 校验。
4. formal plan/execute/replay 冻结并验证 evidence bundle digest；不改写 tracked entry map 的 runtime path。
5. 增加固定参数的 Exp5 v3 PowerShell launcher 与定向测试。

## 工作包 B：Exp5 fail-stop、denominator 与 taxonomy

1. 先写多-root RED：第一个 root resolved model mismatch 时当前代码仍调用第二个 root。
2. 将 condition root 调度改为逐 root checkpoint + identity decision；identity mismatch 后禁止该 condition 新调用，并继续下一 condition。
3. 为未执行 root 写 `not_started` evidence，保持 selection inventory。
4. formal metrics 用 frozen inventory 校验 1,284 planned denominator 与 missingness；失败/未开始不得消失。
5. 补 transport error taxonomy 与 canonical v3 member id 的定向回归。

## 工作包 C：Exp5 renderer production execute/replay

1. 先写 RED：formal execute/replay 当前缺少 6 audit + 8 paper 文件。
2. 从 persisted formal metrics/evidence 组装 renderer rows，不接收 caller 手工成功结论。
3. execute 与 replay 走同一 renderer 接线，输出纳入 manifest/audit refs。
4. 验证 replay 不调用 provider、输出 digest 稳定、失败/missingness 写 `null`/reason。

## 主 Agent 集成与监督

1. 审核各 Agent 的 RED 证据、文件边界、diff 与测试结果；拒绝 scripted fallback、常量填值或跳过协议生命周期的实现。
2. 处理共享 CLI/formal runner 冲突并补集成测试。
3. 运行 Exp3/4/5 定向测试、identity-only、plan-only、replay/output-contract 与 `.\init.ps1`；不运行全量测试。
4. 更新唯一权威实验设计中的 readiness、`progress.md`、`feature_list.json`、`session-handoff.md` 与 code map。
5. 在获得用户对付费真实 API 调用的明确授权后，才运行新的 11-root Exp3/4 与 8-root Exp5 smoke。

## 实施终态

- 工作包 A/B/C：完成。
- 主 Agent 集成：442 passed；Fast 456 passed/1 skipped；Exp3/4 独立复核通过。
- 真实 Exp3/4、Exp5 smoke：未执行，继续等待明确付费授权。
