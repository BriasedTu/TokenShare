---
status: user_frozen_source
document: slim_v2_flash_pricing_source_20260823
scope: forward Experiment 1 DeepSeek Flash identity and static pricing
source_kind: user_direct_price_decision
recorded_at: 2026-08-23 Asia/Shanghai
---

# Slim V2 Experiment 1 Flash 价格来源记录（2026-08-23）

## 1. 来源与范围

本记录的价格和模型身份由用户于 2026-08-23 直接提供并冻结；本次没有用联网资料确认、替代或推断价格。它只适用于此日期之后启动的前向/new-run Experiment 1，不追溯改写任何已经持久化的 provider attempt。

前向本地 provider entry 为 `deepseek_v4_flash_exp1_baseline`，API model 为 `deepseek-v4-flash`，不可继续把 `deepseek_v4_pro_exp1_baseline` / `deepseek-v4-pro` 作为前向 Experiment 1 默认身份。

## 2. 不可变价格版本与公式

价格版本固定为 `slim_v2.pricing.2026-08-23`，币种与单位均为 CNY / 1M tokens：

| 计费项 | 单价 |
|---|---:|
| input，cache hit | 0.05 |
| input，cache miss | 1.50 |
| output | 4.50 |

Flash 使用单一 `pricing_tier=flat`。它没有 peak/off_peak 选择，也不能从此前 Pro 的峰谷价格表、比例或公式推导。

```text
cost_estimate_cny = (
    prompt_cache_hit_tokens  × 0.05
  + prompt_cache_miss_tokens × 1.50
  + completion_tokens        × 4.50
) / 1_000_000
```

`prompt_tokens` 必须等于 cache hit 与 cache miss 两项之和。`reasoning_tokens` 仍是 `completion_tokens` 的子集，只作诊断，不可额外计价。缺失 cache split、`completion_tokens` 或价格版本时，成本为 `null + reason`，不得猜测。

## 3. 非门禁与历史事实

该版本只是普通 pricing projector/reducer 常量，不查询余额、不设预算、不阻止运行，也不形成 publication gate。

`slim-v2-representative-real-20260822-144900-ef9128fe` 的已持久化 Experiment 1 facts 发生在本记录之前，保留其实际 `deepseek-v4-pro` 与 `slim_v2.pricing.2026-08-20` 值。不得将这些事实重标、重价、混入 Flash 或当作 post-change Flash 比较结果；该 exact run 的窄恢复边界以最终汇总轻量修复计划和指标权威第 1.4.4 节为准。
