# Feat-011 DeepSeek Exp1–4 600 秒 / 300K 重新预注册设计

## 1. 状态与授权

- 状态：已实施并验证；hard×10 真实诊断已终态完成，结果含 2 个真实 checker rejection。
- 批准日期：2026-07-28。
- 授权范围：只调整 Experiment 1–4 的官方 DeepSeek `deepseek-v4-pro` 正式基线，以及使用同一题目集合的 hard×10 / concurrency=10 真实 API 诊断。
- 明确不调整：Experiment 5 cohort v2、SiliconFlow GLM-5.2、OpenAI GPT-5.6 Sol、题库、prompt、parser、checker、repeat、worker levels、provider retry 数量和实验分母。
- 预算：沿用用户对本次诊断的无上限预算授权；这不取消每个 AI unit 仅一次 provider attempt 的限制。

## 2. 重新预注册的精确控制

Experiment 1–4 新的固定请求控制为：

| 字段 | 值 |
|---|---:|
| provider | DeepSeek 官方 endpoint |
| model | `deepseek-v4-pro` |
| endpoint | `https://api.deepseek.com/chat/completions` |
| thinking | `{"type":"enabled"}` |
| reasoning effort | `high` |
| stream | `false` |
| `timeout_seconds` | `600` |
| `max_tokens` | `300000` |
| `max_provider_attempts` | `1` |
| provider concurrency ceiling | `50` |

`max_tokens=300000` 是单次请求的生成上限，不是必须消耗的 token 数，也不是 suite 总预算。`timeout_seconds=600` 是本地 transport 的请求超时参数；它不能保证 provider 一定在 600 秒内完成，也不能把未返回 usage 的已计费请求推算成零。

## 3. 版本与兼容边界

不得原地改写已经用于旧 smoke/diagnostic 的 v2 配置。实施时：

1. 保留 `exp1_baseline_provider_config.v2.json` 和 `exp1_minimal_pilot_profile.v2.json` 的原始 `100/8192` 内容及 digest，供历史 replay 和 Experiment 5 cohort v2 使用。
2. 新建 `exp1_baseline_provider_config.v3.json` 和 `exp1_minimal_pilot_profile.v3.json`，冻结 `600/300000`。
3. Experiment 1–4 CLI 默认配置与固定 request-control 常量切换到 v3。
4. Experiment 5 cohort v2 的三个 member 继续保持 `max_tokens=8192`，公共 `timeout_seconds=100`；其 cohort digest 不得变化。
5. 为避免把 Exp1–4 的新 timeout 泄漏到 Exp5，代码中建立明确的 Exp1–4 DeepSeek 请求上限常量；Exp5 继续使用原来的 100 秒正式公共 timeout。

任何实际配置若在 model、endpoint、thinking、reasoning effort、stream、timeout、max tokens、attempts 或并发上与本节不同，Exp1–4 formal preflight 必须在 provider 调用前拒绝。

## 4. hard×10 / concurrency=10 诊断协议

重跑必须沿用上一轮 direct-network 诊断的十个 hard factorization case、prompt 构建器、JSON response format、parser/checker 和并发数 10，只改变：

- provider 配置从 v2 切换为 v3；
- `timeout_seconds` 从 100 改为 600；
- `max_tokens` 从 8192 改为 300000；
- 输出到新的、不可覆盖的 timestamped output directory；
- 生成新的 config digest、request-body digest、selection digest 和 run identity。

真实调用前必须持久化 command、启动时间、model endpoint、request limits、配置 digest、selection digest、预期调用数 10 和输出目录。仍然禁止 provider retry；每题恰好一次调用。失败、超时、空 content、parser rejection、checker rejection 和缺失 usage 均原样保留。

诊断仍属于 smoke/regression evidence：`paper_eligible=false`，不得写入正式论文结果，也不得生成 `formal_paper_report.md`。

## 5. 验证与停止条件

真实 API 调用前依次要求：

1. RED/GREEN 定向测试证明 Exp1–4 接受且只接受 `600/300000`。
2. 定向测试证明 Exp5 cohort v2 仍为 `100/8192`，其 digest 未变化。
3. `init.ps1` 最终 pytest summary 不含 `FAILED`。
4. diagnostic prepare 阶段完成，所有预调用 artifact 已落盘且不含 secret。
5. 环境中只验证 `DEEPSEEK_API_KEY` 是否存在，不读取或打印其值。

若 provider key 缺失、网络代理状态改变、配置/digest/selection 漂移、测试失败或 prepare artifact 不完整，不得调用真实 API；记录 structured blocked。

## 6. 官方在线文档摘要

访问日期：2026-07-28。以下均为 DeepSeek 官方文档；本节是按 `Doc/agent-navigation.md` 要求保存的本地摘要。

| 来源 | 本地摘要 | 影响范围 |
|---|---|---|
| `https://api-docs.deepseek.com/quick_start/pricing/` | DeepSeek-V4-Pro context length 为 1M，最大输出为 384K；300,000 小于官方最大输出。页面同时说明 token 按实际输入/输出量计费。 | 允许把 Exp1–4 单次生成上限预注册为 300,000；不把该上限解释为实际消耗。 |
| `https://api-docs.deepseek.com/api/create-chat-completion` | `max_tokens` 是单次 chat completion 最大生成量，总输入和生成受 context length 限制；`finish_reason=length` 表示达到 token/context 上限。 | request schema、length 截断解释、response evidence。 |
| `https://api-docs.deepseek.com/quick_start/rate_limit` | DeepSeek-V4-Pro 当前账户并发上限文档值为 500；请求等待时 provider 可发送 keep-alive，若 10 分钟仍未开始推理，服务端会关闭连接。 | concurrency=10 能力依据；600 秒本地 timeout 的边界说明。 |

这些网页内容可能随 provider 更新。本次只据其确认 `deepseek-v4-pro` 的 300K 请求参数在官方 384K 上限内；不据此修改既有 pricing 数值或 Experiment 5 配置。

## 7. 预期产物

- v3 baseline provider config 与 pilot profile；
- Exp1–4 固定请求控制、CLI 默认路径和 fail-closed preflight 更新；
- v2/Exp5 不变性测试；
- 定向测试、Fast、Full 验证记录；
- 新 hard×10 诊断目录，包含 launch manifest、prompt/request digests、attempt/result、usage、summary 和最终 regression report；
- `progress.md`、`feature_list.json`、`session-handoff.md`、code map 与权威实验设计/参数决策日志同步。

## 8. 实施与诊断终态（2026-07-28）

- tracked v3 baseline/profile、Exp1–4 固定常量、CLI 默认路径、预算上界与 fail-closed tests 已接入；v2 baseline digest 与 Experiment 5 cohort v2 digest 均由测试冻结且未变化。
- TDD/门禁：migration=`7 passed`，Exp1/2/3/4=`36/28/25/38 passed`，完整影响集=`218 passed`，Fast=`346 passed, 1 skipped`。首次 Full 因 Gate C 测试夹具仍加载 v2 controls 得到 `13 failed, 1408 passed, 1 skipped`；仅更新夹具后该文件 `33 passed`，最终 Full=`1421 passed, 1 skipped in 762.24s`，无 FAILED。没有放宽生产 fail-closed。
- 第一个 prepared identity `deepseek_hard10_concurrency10_direct_300k_20260727T182030Z` 在 transport 前发现 WinINet/Python 仍解析 `127.0.0.1:7890`，未执行。为兑现 direct-network 语义，新 identity 把只针对 `api.deepseek.com` 的 process `NO_PROXY` 策略写入 launch manifest，系统代理设置保持不变。
- 实际 run=`deepseek_hard10_concurrency10_direct_300k_20260727T182217Z`；config/selection digest=`sha256:4bdf0d330c8d01af9760f17b333d75a68f0b8bfad214e807072562e057ef3923` / `sha256:af0cf36cbea5787d4c1267f08d4451d6a949ffe125c9b6ba5d4a3029676ddcca`。
- expected/executed=`10/10`，max active=`10`，HTTP 200/usage=`10/10`，retry/429/usage-missing=`0/0/0`，wall-clock=`459.267125s`。结果为 8 `normal_valid`、2 `checker_rejected`、0 parser failure；两项负面样本是 `hard_003` 漏因子 19013、`hard_009` 漏因子 58189，均在 `plugin_domain_check` 被拒绝。
- provider usage prompt/completion/total=`14,346/225,239/239,585`；raw `completion_tokens_details.reasoning_tokens=221,563`，非 reasoning completion=`3,676`。冻结价格的 client estimate=`CNY 1.3529648`，不是账单实扣。原 diagnostic summary 只读顶层 reasoning 字段而写 0，原文件不改写；独立 `usage_reconciliation.json` 从 hash-verified raw results 派生正确值，正式 runner 的归一化代码与既有测试本来就读取嵌套字段。
- 最终报告为该 output root 下的 `diagnostic_final_report.md`。本次仍是 smoke/regression evidence，`paper_eligible=false`；未运行 pilot、正式 Experiment 1–5 matrix、LeanAudit、force-all 或全量 Lean。仓库 `init.ps1 -Full` 只是测试门禁，不是 Full experiment。
