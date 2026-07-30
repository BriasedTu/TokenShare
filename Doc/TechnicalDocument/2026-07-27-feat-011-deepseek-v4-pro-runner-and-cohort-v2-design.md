# feat-011：DeepSeek-V4-Pro runner 与 Experiment 5 cohort v2 设计

> 历史版本说明（2026-07-28）：本文冻结的 Exp1–4 `100/8192` 控制已由 `2026-07-28-feat-011-deepseek-exp1-exp4-300k-reregistration-design.md` 版本化替代为 v3 `600/300000`。本文的 Experiment 5 cohort v2 `100/8192` 以及 v1/v2 replay 边界继续有效；不得把本文旧 Exp1–4 数值当作当前正式配置。

日期：2026-07-27  
状态：已批准，进入实施  
适用 feature：`feat-011`

## 1. 目标与非目标

本设计为 TokenShare 增加独立的官方 DeepSeek provider runner，并把 Experiment 1–4 的当前正式默认端点从 SiliconFlow GLM-5.2 迁移到官方 `deepseek-v4-pro`。同时新增 Experiment 5 cohort v2，以同一单次生成上限比较 SiliconFlow GLM-5.2、官方 DeepSeek-V4-Pro 与 OpenAI GPT-5.6 Sol high。

本轮只实现离线 runner、配置、版本迁移、测试和文档，不调用任何付费模型 API，不运行 pilot、smoke、正式实验或全量 Lean 实验，不修改既有 ledger、artifact、attempt、JSONL、CSV 或报告。

## 2. 固定配置

### 2.1 Experiment 1–4 当前默认端点

| 字段 | 固定值 |
| --- | --- |
| `provider_family` | `deepseek` |
| `model` | `deepseek-v4-pro` |
| `base_url` | `https://api.deepseek.com` |
| `endpoint` | `/chat/completions` |
| `thinking` | `{"type":"enabled"}` |
| `reasoning_effort` | `high` |
| `max_tokens` | `8192` |
| `max_in_flight_global` | `50` |
| `api_key_env` | `DEEPSEEK_API_KEY` |

`max_tokens=8192` 只表示单次请求的生成上限，不是实际 token 消耗，也不是整套实验的 token budget。实际消耗只能来自 provider response 的 `usage`。

`worker_count` 与 `max_in_flight_global` 是独立概念：前者是 Experiment 2 的实验自变量，保持 `1/3/7/10/30/50`；后者是本地 provider runner 的全局并发上限。调度计划不得以 `min(worker_count, 3)` 或其他本地 semaphore 静默改写 Experiment 2 档位。

### 2.2 Experiment 5 cohort v2

cohort v2 恰好包含：

1. SiliconFlow `zai-org/GLM-5.2`，`enable_thinking=true`；
2. 官方 DeepSeek `deepseek-v4-pro`，`thinking={"type":"enabled"}`、`reasoning_effort=high`；
3. OpenAI GPT-5.6 Sol，`reasoning_effort=high`。

三个端点的 `max_tokens` 均为 `8192`。v2 不包含 Qwen；v1 的 GLM/Qwen/GPT 定义、旧 GLM baseline、旧 digest 和旧 evidence 原样保留，用于历史重放。

## 3. Provider 分层

新增独立 `provider_family="deepseek"`，不把 DeepSeek 伪装为 `openai` 或 `siliconflow`。实现分为：

- DeepSeek request body builder：只生成官方 DeepSeek 请求字段；thinking 模式明确省略 `temperature` 与 `top_p`。
- DeepSeek response parser：提取最终 `content`、可选的完整 `reasoning_content` 和原始 `usage`。
- DeepSeek transport：使用官方 base URL 与 `/chat/completions`，保留 `deepseek` provider identity，并将 HTTP 429、timeout、连接错误、空响应、无效 JSON 和 schema 错误分类为真实 provider 错误。
- 共享 executor：通过 provider adapter 路由 SiliconFlow、OpenAI 和 DeepSeek；factorization 与 Lean AI adapter 共同使用同一 DeepSeek transport。

非流式 HTTP 响应在 JSON 前可能包含空白 keepalive。transport 在解析前允许前导/尾随空白，但空白-only 响应仍分类为 `invalid_response`，不能伪造成功。

DeepSeek 的 `reasoning_content` 作为受控 provider response evidence 完整持久化；插件 checker 继续只消费最终 `content`。持久化的 resolved request metadata 不包含 `Authorization` header、API key 或其派生信息。

## 4. Usage 与 cost estimate

pricing schema 以向后兼容方式扩展。旧配置继续支持：

- `currency`
- `input_per_million_tokens`
- `output_per_million_tokens`

新配置支持：

- `currency`
- `cached_input_per_million_tokens`
- `uncached_input_per_million_tokens`
- `output_per_million_tokens`

DeepSeek-V4-Pro 固定价格为：cached input `¥0.025 / 1M tokens`、uncached input `¥3 / 1M tokens`、output `¥6 / 1M tokens`，币种记录为 `CNY`。

usage parser 读取可用字段：`prompt_tokens`、`completion_tokens`、`total_tokens`、`completion_tokens_details.reasoning_tokens`、`prompt_cache_hit_tokens`、`prompt_cache_miss_tokens`。有 hit/miss 明细时按明细估算；没有明细时将全部 prompt tokens 按未缓存输入保守估算，并记录估算口径。未收到 usage 的 timeout、断连、空响应或错误 attempt 标为 `usage_missing`，不得以零 token 代替。

所有本地金额只命名为 `cost_estimate` 或 provider-reported usage estimate，不能称为实际账单费用。报告保留 `currency`，只允许同币种聚合；CNY 与 USD 不直接相加，跨币种换算留待未来另行预注册汇率口径。

## 5. 版本迁移与重放

新增并跟踪：

- `benchmarks/paper/exp1_baseline_provider_config.v2.json`：Experiment 1–4 当前 DeepSeek baseline；
- `benchmarks/paper/exp1_minimal_pilot_profile.v2.json`：指向 baseline v2 的当前 runner profile；
- `benchmarks/paper/model_comparison_cohort.v2.json`：Experiment 5 新三模型 cohort；
- `benchmarks/paper/paper_smoke_exp1_exp4_profile.v2.json` 与 `paper_smoke_profile.v2.json`：当前 DeepSeek baseline / cohort v2 的非正式 smoke selector。

保留且不原地修改：

- `benchmarks/paper/exp1_baseline_provider_config.v1.json`；
- `benchmarks/paper/exp1_minimal_pilot_profile.v1.json`；
- `benchmarks/paper/model_comparison_cohort.v1.json`；
- `benchmarks/paper/paper_smoke_exp1_exp4_profile.v1.json` 与 `paper_smoke_profile.v1.json`；
- 已生成的历史 config/selection digest 和全部 evidence。

当前 CLI、Experiment 1–4 runner 常量、model policy 和文档默认值切换到 v2。loader 与 preflight 同时识别 v1/v2，digest 继续由完整 safe config / cohort JSON 内容计算；不能把 v2 冒充为旧 digest。resume/replay 以已持久化 attempt 为准，已有完成结果不得重新发起 provider 请求。

## 6. Secret 边界

tracked 配置只保存 `api_key_env: "DEEPSEEK_API_KEY"`。真实 key 只能由受 `.gitignore` 保护的 `local/*.local.json` 在当前进程注入，或由用户在 PowerShell 当前会话安全设置；不得写入补丁、命令、日志、event、artifact、digest、测试输出、文档或最终回复。

实现完成后执行 tracked/worktree secret 扫描。扫描只报告命中与否，不输出环境变量值、长度、前后缀或其他可识别片段。

## 7. 错误分类与验证边界

必须离线覆盖：配置 schema、精确 request body、thinking 字段过滤、response/usage 解析、cache 计费、usage missing、429、timeout、空响应、无效 JSON、空白 keepalive、factorization/Lean 路由、resume 不重调、默认 baseline 迁移、cohort v2、v1 完整性、digest 敏感性和 secret absence。

本轮不通过降低题库、实验分母、repeat、timeout、failure condition 或 expected result 来获得测试通过，也不运行真实 API 验证。

## 8. 外部资料落库记录

以下资料均为 DeepSeek 官方文档，访问日期统一为 2026-07-27。本节是本地可复查摘要；代码与测试只实现本节列出的受影响行为。

| 来源 | 本地摘要 | 影响范围 |
| --- | --- | --- |
| `https://api-docs.deepseek.com/zh-cn/` | 官方 API 使用 `https://api.deepseek.com`；Chat Completions endpoint 为 `/chat/completions`；V4-Pro 支持 `thinking` 与 `reasoning_effort`。 | provider identity、base URL、endpoint、请求字段 |
| `https://api-docs.deepseek.com/zh-cn/quick_start/pricing/` | DeepSeek-V4-Pro cached input、uncached input、output 的人民币单价分别为 0.025、3、6 元/百万 token。 | pricing schema、CNY cost estimate |
| `https://api-docs.deepseek.com/zh-cn/guides/thinking_mode/` | Thinking mode 使用 `thinking={"type":"enabled"}`；返回可含 `reasoning_content`；`temperature`、`top_p` 等采样字段在该模式下不应依赖。 | body builder、response evidence、字段过滤测试 |
| `https://api-docs.deepseek.com/zh-cn/quick_start/rate_limit/` | 超过并发限制返回 HTTP 429；非流式等待期间可能发送空白 keepalive。 | 429 分类、空白 keepalive 解析、并发语义 |
| `https://api-docs.deepseek.com/zh-cn/quick_start/token_usage/` | 实际 token 使用量通过 response `usage` 返回。 | usage 来源、usage_missing 语义 |
| `https://api-docs.deepseek.com/zh-cn/guides/json_mode/` | JSON mode 使用 `response_format={"type":"json_object"}`，提示词需明确 JSON，生成上限不足可能截断，最终 content 仍需校验。 | JSON request passthrough、空 content/schema failure 边界 |
| `https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/` | usage 可含 cache hit/miss token 与 completion token details；assistant message 可含 `reasoning_content`。 | usage parser、reasoning token 与 evidence 字段 |

未引入第三方论文、报告或开源仓库，因此不新增 `tokenshare-paper-tex` 或 `reference_repos` 材料。

## 9. 实施与离线验证终态

EPD-008 的 runner/config/cohort 迁移已实现并完成离线验证。TDD 首轮精确套件为 `17 failed, 8 passed`；最终跨 provider、两类 adapter、Experiment 1–5、CLI、smoke、replay/runtime 的定向套件为 `369 passed in 240.14s`。首次 Full 在 `1419 passed, 1 skipped` 后暴露一个复制的 budget-policy fixture 未提供 DeepSeek 测试占位 key；生产 fail-closed 未放宽，修复 fixture 后最终 Full 为 `1420 passed, 1 skipped in 750.41s`，证据回填后最终 Fast 为 `346 passed, 1 skipped in 17.28s`。

版本 digest：

- baseline config v1=`sha256:a602b7597691089a40e1d036b60f426d272e906e8f59d067938e9364e411fbcf`，v2=`sha256:b000e54782c8dbe8a7df122ec8fc4d6c9b98d1b1f2d6f9dbc94f7029aebaa99f`；
- pilot profile v1=`sha256:7f3637de8a775b40d4f5d65acae19940228ff9760d1ad49ff72d93614dcc69aa`，v2=`sha256:f805151d18180cda6a737f59796b9bc46e2c43dc3a36c10c8d4d010468d0544f`；
- Experiment 5 cohort v1=`sha256:6a8745c4fdd419f616d540a92bb899fedf68f8c8bf110061680f054876aa2e02`，v2=`sha256:4be1c6e981e636ab524009a2f521a522fc63409f88255a33c2afb82412862fd8`；
- Exp1–4 smoke profile v2=`sha256:8ca13d1a7730ecc4166b3232d9ba089d4334addd842b1f8fe812792f7facec76`，全 cohort smoke profile v2=`sha256:eba4f3a7dddefc1469c0f4b50ad06c843ac19854ba439fcdfaae12dd646b044b`。

secret 扫描覆盖 26,235 个 tracked、未忽略 untracked 与历史 outputs 文本文件，高置信 credential 命中路径为 0；tracked paper JSON 明文 `api_key` 字段为 0，`.gitignore` 的 `local/*.local.json` 规则存在。

本轮 provider calls/tokens/cost=`0/0/0`；没有运行真实付费 API、pilot、smoke、正式 Experiment 1–5、LeanAudit 或全量 Lean 实验。`feat-011` 整体仍为 `in-progress`，只因为未来真实 provider 正式论文实验与结果闭环不在本次授权内；EPD-008 实现子项本身已离线验证完成。
