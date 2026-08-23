---
status: user_frozen_source
document: slim_v2_official_pricing_sources_20260820
scope: historical DeepSeek Pro and Experiment 5 SiliconFlow API pricing evidence
accessed_at: 2026-08-20 Asia/Shanghai
---

# Slim V2 官方价格来源摘要（2026-08-20）

## 1. 范围与使用规则

本文只保存 2026-08-20 访问的 DeepSeek Pro 与 SiliconFlow 官方一手页面证据。未使用第三方价格聚合站。所有金额均保留官网原始币种和“每百万 tokens”单位，不在本文引入汇率换算。自 2026-08-23 起，前向 Experiment 1 的 Flash 价格与身份改由 `slim_v2_flash_pricing_source_20260823.md` 记录；本文的 Pro 部分只解释已经启动 run 的历史事实，不能覆盖前向默认值。

冻结时必须同时固定 `provider`、API `model`、币种、峰/谷时段（若有）、价格来源 URL 与访问日期。官网后续改价不得追溯改写已经启动的 run；新 run 如要采用新价，应形成新的 pricing snapshot/version。

## 2. DeepSeek Pro：Experiment 1 的历史快照

### 2.1 当前人民币价格

2026-08-20 快照中的本地 entry 为 `deepseek_v4_pro_exp1_baseline`，请求 model 为 `deepseek-v4-pro`。DeepSeek 官方价目页只定义 API model，不存在名为 `deepseek_v4_pro_exp1_baseline` 的官方产品名称；后者应视为当时的 TokenShare 本地 config entry ID，而非当前前向 Experiment 1 默认值。

官方中文价目页在访问时显示 API model `deepseek-v4-pro`，当前模型版本为 `DeepSeek-V4-Pro-0813`。单位为人民币元 / 1M tokens：

| 计费项 | 空闲时段 | 高峰时段 | 原始币种/单位 |
|---|---:|---:|---|
| input，cache hit | 0.15 | 0.30 | CNY / 1M tokens |
| input，cache miss | 4.50 | 9.00 | CNY / 1M tokens |
| output | 13.50 | 27.00 | CNY / 1M tokens |

高峰时段为北京时间 `09:00–12:00` 与 `14:00–18:00`，其余时段为空闲时段。官方 2026-08-13 GA 公告说明新峰/谷定价从 `2026-08-16 16:00 UTC` 起生效，因此在本次访问日已经生效。

来源：

- [DeepSeek 中文“模型 & 价格”](https://api-docs.deepseek.com/zh-cn/quick_start/pricing)，访问日期 2026-08-20；页面未显示独立的最后更新时间，但显示模型版本 `DeepSeek-V4-Pro-0813`。
- [DeepSeek-V4-Pro GA Release](https://api-docs.deepseek.com/news/news260813)，页面可见日期 2026-08-13；页面可见的新价格生效时间为 2026-08-16 16:00 UTC。

### 2.2 官方美元页面的平行展示

官方英文价目页对同一 API model 同时给出美元价格。该表不是人民币表的本文自行换算结果：

| 计费项 | off-peak | peak | 原始币种/单位 |
|---|---:|---:|---|
| input，cache hit | 0.022 | 0.044 | USD / 1M tokens |
| input，cache miss | 0.66 | 1.32 | USD / 1M tokens |
| output | 1.98 | 3.96 | USD / 1M tokens |

英文页面的 peak 为 `01:00–04:00 UTC` 与 `06:00–10:00 UTC`，与中文页面的北京时间区间一致。Slim V2 的必须指标名为 `actual_cost_estimate_cny`，因此若实际 DeepSeek 账户按人民币扣费，可直接冻结第 2.1 节人民币表；若账户实际按美元扣费，则必须保存 USD 成本并由另一个明确、版本化的汇率规则换算，不能把美元数字直接标成 CNY。

来源：[DeepSeek 英文 “Models & Pricing”](https://api-docs.deepseek.com/quick_start/pricing/)，访问日期 2026-08-20；页面未显示独立的最后更新时间。

### 2.3 reasoning tokens 的计费归属

DeepSeek 官方 Chat Completions 响应 schema 将 `reasoning_tokens` 放在 `completion_tokens_details` 下，并说明该对象是 completion token 的细分；`total_tokens` 等于 prompt 与 completion 的总和。价目页只设置 input 与 output 单价。因此 reasoning tokens 已包含在 `completion_tokens` / output tokens 中，按 output 单价计费，不得再把 `reasoning_tokens` 额外加一次。

推荐计算口径：

```text
cost = prompt_cache_hit_tokens  × cache_hit_rate
     + prompt_cache_miss_tokens × cache_miss_rate
     + completion_tokens        × output_rate
```

其中各 token 数先除以 1,000,000。不得使用 `content` 的可见文本 token 数替代 provider 返回的 `completion_tokens`。

来源：[DeepSeek Chat Completions API schema](https://api-docs.deepseek.com/api/create-chat-completion/)，访问日期 2026-08-20；页面未显示独立的最后更新时间。

## 3. SiliconFlow：Experiment 5

### 3.1 专用价格页的当前值

SiliconFlow 官方“模型价格总览”页在访问时标注“实时价格同步”，列头明确为 input、output、cache，单位均为人民币元 / 1M tokens。四个 Slim V2 API model 的当前可见行如下：

| Slim V2/API model | input | output | cache（补充） | 原始币种/单位 |
|---|---:|---:|---:|---|
| `zai-org/GLM-5.2` | 8.00 | 28.00 | 2.00 | CNY / 1M tokens |
| `Qwen/Qwen3-14B` | 0.50 | 2.00 | 未列价（`-`） | CNY / 1M tokens |
| `MiniMaxAI/MiniMax-M2.5` | 2.10 | 8.40 | 0.21 | CNY / 1M tokens |
| `Pro/deepseek-ai/DeepSeek-V3` | 2.00 | 8.00 | 0.20 | CNY / 1M tokens |

来源：

- [SiliconFlow 大模型 API 价格方案](https://siliconflow.cn/pricing)，访问日期 2026-08-20。该页无可见的最后更新时间，只显示“实时价格同步”；每一行的模型详情链接以 `target=` 精确编码 API model ID。
- 用户登录后的 [SiliconFlow 模型广场](https://cloud.siliconflow.cn/me/models)，访问日期 2026-08-20。逐一打开四个精确 API model 的“价格信息/在线推理”详情，显示的每千 tokens 数值分别换算后与上表完全一致：GLM-5.2 为 cache/input/output `2/8/28`，Qwen3-14B 为 input/output `0.5/2`，MiniMax-M2.5 为 `0.21/2.1/8.4`，Pro DeepSeek-V3 为 `0.2/2/8`，单位均为 CNY / 1M tokens。核对过程中未读取或保存 API key、余额、账单或账号资料。

### 3.2 模型标识与权威文档名称核对

四个 Slim V2 字符串都与 SiliconFlow 官方价格页详情链接的 target model ID 精确一致，没有 API identifier 改名：

| Slim V2 字符串 | 官网 UI display name | 结论 |
|---|---|---|
| `zai-org/GLM-5.2` | `GLM-5.2` | API ID 一致；UI 省略厂商前缀 |
| `Qwen/Qwen3-14B` | `Qwen3-14B` | API ID 一致；UI 省略厂商前缀 |
| `MiniMaxAI/MiniMax-M2.5` | `MiniMax-M2.5` | API ID 一致；UI 省略厂商前缀 |
| `Pro/deepseek-ai/DeepSeek-V3` | `DeepSeek-V3 (Pro)` | API ID 一致；UI 把 `Pro/` 改为显示后缀 |

官方模型中心可见的模型发布时间分别为：

- `zai-org/GLM-5.2`：2026-06-17；[直接筛选页](https://siliconflow.cn/models?q=zai-org%2FGLM-5.2&page=1)。
- `Qwen/Qwen3-14B`：2025-04-29；[直接筛选页](https://siliconflow.cn/models?q=Qwen%2FQwen3-14B&page=1)。
- `MiniMaxAI/MiniMax-M2.5`：2026-02-13；[直接筛选页](https://siliconflow.cn/models?q=MiniMaxAI%2FMiniMax-M2.5&page=1)。
- `Pro/deepseek-ai/DeepSeek-V3`：2025-03-24；[直接筛选页](https://siliconflow.cn/models?q=Pro%2Fdeepseek-ai%2FDeepSeek-V3&page=1)。

这些日期是模型卡的“发布时间”，不是价目页的最后更新时间。

### 3.3 reasoning tokens 的计费归属

SiliconFlow 官方 Chat Completions 示例把 `reasoning_tokens` 放在 `usage.completion_tokens_details` 中；示例同时给出 `completion_tokens=1540`、`reasoning_tokens=1190`、`total_tokens=prompt_tokens+completion_tokens`。结合价目页只区分 input 与 output，可以明确按 provider 返回的 `completion_tokens` 使用 output 单价，reasoning tokens 是 completion/output 的子集，不另收第二次。

因此对配置为 thinking 的 GLM、Qwen、MiniMax 条件，成本计算使用完整 `completion_tokens`；不得只数最终 `content`，也不得再加 `reasoning_tokens`。`Pro/deepseek-ai/DeepSeek-V3` 在 Slim V2 中配置为 nonthinking；若实际响应仍给出 usage details，仍按同一 completion 归属规则记录，不能自行猜测或构造 reasoning token 数。

来源：[SiliconFlow 创建对话请求（OpenAI）](https://docs.siliconflow.cn/cn/api-reference/chat-completions/chat-completions)，访问日期 2026-08-20；页面未显示独立的最后更新时间。

## 4. 页面差异与最终来源优先级

### 4.1 `zai-org/GLM-5.2` input 的交叉核对

同一次 2026-08-20 访问中，早先的公开模型镜像查询曾返回 input `6.00`、output `28.00`；但随后两个直接计价界面都返回 input `8.00`：

- 专用价格页显示 input `8.00`、output `28.00`、cache `2.00` CNY / 1M tokens；
- 用户登录后的官方模型广场为同一 API ID 打开的“价格信息”详情显示每千 tokens 为 input `0.008`、output `0.028`、cache hit `0.002`，即每百万 tokens 为 `8/28/2`。

用户已要求把官网结果直接作为实验计算标准。最终采用“专用 `/pricing` 为价格权威、登录后 `/me/models` 价格详情为交叉核对”的优先级，冻结 GLM-5.2 input=`8.00`。公开模型镜像的早先 `6.00` 只保留为同日页面差异记录，不进入 Slim V2 运行或成本计算。其余三个 endpoint 在两个计价界面也一致。

### 4.2 cache 与 provider usage

SiliconFlow 专用价格页虽然列出部分模型 cache 价，但本任务要求的四模型冻结项是 input/output。若实际 provider usage 不能稳定区分 cache-hit 与 cache-miss tokens，则不得凭总 prompt tokens 猜测 cache 命中量。Qwen3-14B 的 cache 列为 `-`，不能解释成免费；应解释为官网未列独立 cache 单价。

## 5. 是否足以冻结

| 项目 | 可冻结性 | 说明 |
|---|---|---|
| DeepSeek `deepseek-v4-pro` 人民币价格 | 是 | 必须冻结峰/谷两档及北京时间边界，不能只保存一个 input/output 数字 |
| DeepSeek 美元价格 | 是 | 仅用于实际按 USD 计费的账户；不得无汇率规则直接写入 CNY 指标 |
| SiliconFlow Qwen/MiniMax/DeepSeek-V3 | 是 | 专用价格页与模型中心 input/output 一致 |
| SiliconFlow GLM-5.2 | 是 | 专用 `/pricing` 与登录后的模型价格详情均为 input 8.00；公开镜像早先的 6.00 不进入冻结表 |
| 两家 reasoning token 归属 | 是 | 两家官方 schema 都把 reasoning 放在 completion token 细分中；成本使用 completion/output，禁止重复加算 |

2026-08-20 快照的冻结策略：DeepSeek Pro 采用人民币峰/谷表；SiliconFlow 指定 `https://siliconflow.cn/pricing` 为价格权威，并以登录后的 `https://cloud.siliconflow.cn/me/models` 价格详情交叉核对。`slim_v2.pricing.2026-08-20` 对 Experiment 5 与已持久化 Pro 事实仍只用于成本换算，不创建预算、余额、审批或运行门禁；它不是前向 Flash Experiment 1 的价格版本。
