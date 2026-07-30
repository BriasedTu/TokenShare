# feat-011：DeepSeek-V4-Pro runner 与 cohort v2 实施计划

日期：2026-07-27  
状态：执行中  
对应设计：`2026-07-27-feat-011-deepseek-v4-pro-runner-and-cohort-v2-design.md`

> 本计划执行已经批准的模型、thinking、max_tokens、并发和 cohort 参数；除非发现不可实现的真实矛盾，不再请求参数批准。

## 1. 基线与保护现场

1. 确认仓库根目录、完整读取启动文档和 active feature。
2. 检查 `git status --short` 与重叠文件 diff，保护用户已有 feat-011/smoke 修改和失败 evidence。
3. 运行修改前 Fast 基线并记录结果；若失败，先只读诊断。

完成证据：修改前 `init.ps1` 的退出码与 pytest summary。

## 2. RED：先固定 provider 与 schema 行为

新增或扩展 executor 测试，先运行并保存预期失败：

- `tests/executors/test_ai_api_config.py`：接受 `deepseek`、拒绝未知 provider、兼容新旧 pricing schema。
- `tests/executors/test_ai_api_deepseek_transport.py`：精确 body、thinking 字段过滤、content/reasoning_content/usage、keepalive、429、timeout、空响应、无效 JSON。
- `tests/executors/test_ai_api_executor.py`：cache hit/miss 计费、缺明细保守估算、usage_missing、reasoning evidence 与安全 request metadata。

RED 标准：失败必须由缺少 DeepSeek/provider migration 行为造成，不能由测试夹具或语法错误造成。

## 3. GREEN：实现 DeepSeek provider 与 usage 估算

修改：

- `src/tokenshare/executors/ai_api_config.py`
- `src/tokenshare/executors/ai_api_local_config.py`
- `src/tokenshare/executors/ai_api_transport.py` 或独立 DeepSeek transport 模块
- `src/tokenshare/executors/ai_api.py`

实现内容：

1. 注册独立 `deepseek` family 与默认 base URL。
2. 添加 DeepSeek request/response/transport/error 类型，保留 DeepSeek provenance。
3. thinking 模式只发送有效字段，完整保留 `reasoning_content`。
4. 扩展 pricing 和 usage summary，区分 cached/uncached input；没有 usage 时显式 `usage_missing`。
5. 保证 request/evidence/digest 中没有 secret。

运行 executor 定向测试直到 GREEN。

## 4. RED/GREEN：插件路由、版本化配置与默认迁移

先扩展实验测试并保存 RED：

- factorization 与 Lean AI adapter 都能选择 DeepSeek transport；
- Experiment 2 worker 档仍为 `1/3/7/10/30/50`，不被本地三路限制截断；
- Experiment 1–4 当前 baseline 为官方 DeepSeek 固定配置；
- cohort v2 恰好为 GLM/DeepSeek/GPT，三者 `max_tokens=8192`；
- GLM thinking 开启，DeepSeek/GPT 为 high；
- cohort v1、旧 GLM config 和旧 digest 保持可读；
- config/cohort digest 对 v2 差异敏感；
- replay/resume 对已持久化成功 attempt 不重新调用 provider。

随后最小修改：

- `src/tokenshare/plugins/factorization_ai_adapter.py`
- `src/tokenshare/plugins/lean_ai_adapter.py`
- `src/tokenshare/experiments/paper_model_identity.py`
- `src/tokenshare/experiments/paper_model_policy.py`
- `src/tokenshare/experiments/paper_exp1.py`
- `src/tokenshare/experiments/paper_exp2_scalability.py`
- `src/tokenshare/experiments/paper_exp3_fault_recovery.py`
- `src/tokenshare/experiments/paper_exp4_ablation_runner.py`
- `src/tokenshare/experiments/paper_exp5_model_comparison.py`
- `src/tokenshare/experiments/paper_formal_runner.py`
- `src/tokenshare/experiments/run_paper_experiments.py`

新增：

- `benchmarks/paper/exp1_baseline_provider_config.v2.json`
- `benchmarks/paper/exp1_minimal_pilot_profile.v2.json`
- `benchmarks/paper/model_comparison_cohort.v2.json`
- `benchmarks/paper/paper_smoke_exp1_exp4_profile.v2.json`
- `benchmarks/paper/paper_smoke_profile.v2.json`

v1 文件不原地改写。实施时对用户已有 smoke/recovery diff 只做局部兼容修改。

## 5. 报告币种与历史兼容

扩展 Experiment 5/formal evidence 的 cost estimate 字段，使 currency 与估算口径随 endpoint/attempt 保存。同币种可以聚合，混合 CNY/USD 时输出分币种结果或明确不可直接合计状态，不引入汇率。

验证旧单一 input pricing、旧 cohort selection 和旧 persisted evidence 仍可加载；不得重写历史 artifact。

## 6. 文档与 harness 同步

更新并二次检索旧表述：

- `AGENTS.md`
- `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`
- decision log
- Phase 8 code map
- `README.md` 与 CLI 示例（受影响部分）
- `Doc/agent-navigation.md` 的官方在线文档摘要索引
- `feature_list.json`
- `progress.md`
- `session-handoff.md`

重点消除“Experiment 1–4 当前固定 GLM”和“Experiment 5 当前包含 Qwen”的过期表述，同时明确 v1 仍用于历史重放。

## 7. 最终验证

按顺序执行并保存真实输出：

1. 所有新增/修改模块的定向 pytest；
2. `powershell -ExecutionPolicy Bypass -File .\init.ps1`；
3. `powershell -ExecutionPolicy Bypass -File .\init.ps1 -Full`；
4. `git diff --check`；
5. secret 扫描，只报告是否命中；
6. `git status --short`；
7. 对旧 GLM config、cohort v1、旧 evidence 执行存在性、内容或 digest 检查。

本轮不修改 Lean catalog/checker/toolchain/shared fixture helper，因此不计划运行 LeanAudit。若实际改动范围发生变化，再按仓库规则决定是否追加。

## 8. 会话收尾

把 RED/GREEN、Fast/Full、digest、secret scan、遗留边界和下一步写入 `feature_list.json`、`progress.md`、`session-handoff.md`。最终报告明确声明：没有调用真实付费 API，没有运行 pilot、smoke、正式 Experiment 1–5 或全量 Lean 实验，也没有暂存、提交或推送 Git 变更。

## 9. 执行结果

- RED：`17 failed, 8 passed`，缺口集中在独立 DeepSeek provider、精确 request/response、usage/cost、默认 baseline 与 cohort v2。
- GREEN：定向影响集 `369 passed in 240.14s`；证据回填后最终 Fast `346 passed, 1 skipped in 17.28s`；最终 Full `1420 passed, 1 skipped in 750.41s`。
- 首次 Full 的唯一失败是旧 budget-policy fixture 未提供 DeepSeek 测试占位 key；生产 key 缺失仍 fail-closed，fixture 精确复验 `3 passed`。
- v1 tracked baseline/pilot/cohort 无 Git diff，v1/v2 digest 分离；历史 evidence 未删除或重写。
- secret 扫描覆盖 26,235 个 tracked/untracked/history-output 文本文件，高置信 credential 命中路径与 tracked paper JSON 明文 `api_key` 字段均为 0。
- 本轮不触及 Lean catalog/checker/toolchain/shared fixture helper，因此未运行 LeanAudit；provider calls/tokens/cost=`0/0/0`。
