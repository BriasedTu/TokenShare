# Phase 7 AI API Executor 代码映射

日期：2026-06-28

状态：Phase 7 Experimental AI API Executor 已实现、完成审查 hardening 并完成定向验证。本文映射 SiliconFlow-only 第一版 executor 的 source、tests、字段规格章节、验证证据和协议边界。

## 1. Source Map

| 文件 | 规格章节 | 当前内容 |
|---|---|---|
| `src/tokenshare/executors/ai_api_config.py` | 第 6 节 | 本地 config dataclasses、schema validation、safe digest、secret env lookup。 |
| `src/tokenshare/executors/ai_api_local_config.py` | 第 13、16.2 节 / 2026-06-29 smoke 体验补丁 | 读取被 gitignore 的 `local/ai_api_smoke.local.json`，允许 smoke 文件包含本地 `api_keys` 和 `models` 矩阵并展开成标准 `entries`；已填写的明文 `api_key` 只注入当前进程环境变量，再调用标准 `load_ai_api_config()`；safe dict、config digest、artifact/event/log 均不包含 secret。 |
| `src/tokenshare/executors/ai_api_transport.py` | 第 8 节、第 12 节 | SiliconFlow chat completions request/response boundary、HTTP status / invalid envelope error mapping、opt-in stdlib transport。 |
| `src/tokenshare/executors/ai_api_selector.py` | 第 7 节 | Eligible filtering、seeded uniform random selection、bounded failover order。 |
| `src/tokenshare/executors/ai_api.py` | 第 4 节、第 9-13 节 | Descriptor builder、AIAPIExecutor orchestration、raw/parsed/parse failure/provenance/usage artifact persistence。 |
| `src/tokenshare/executors/ai_api_replay.py` | 第 14 节 | Replay guard helper that verifies historical artifacts without calling transport。 |
| `src/tokenshare/executors/__init__.py` | TDD plan Task 10 | Package-level public exports for Phase 7 executor APIs。 |

## 2. Test Map

| 测试文件 | 覆盖内容 |
|---|---|
| `tests/phase7_fixtures.py` | Shared PromptPackage、ExecutionRequest、config、fake transport fixture。 |
| `tests/executors/test_ai_api_config.py` | Config validation、secret boundary、digest、duplicate entry rejection、strict boolean entry fields。 |
| `tests/executors/test_ai_api_local_config.py` | Gitignored local smoke JSON loader、process-local secret injection、safe config redaction、默认路径被 `.gitignore` 覆盖、API key pool × model matrix 展开。 |
| `tests/executors/test_ai_api_descriptor.py` | ExecutorDescriptor builder、registry matching、package exports。 |
| `tests/executors/test_ai_api_transport.py` | SiliconFlow body construction、response/error mapping、stdlib transport bad-body mapping。 |
| `tests/executors/test_ai_api_selector.py` | Eligible filtering、seeded selection、JSON mode filtering。 |
| `tests/executors/test_ai_api_executor_success.py` | Success path、artifact persistence、usage/cost、missing usage status、redaction scan。 |
| `tests/executors/test_ai_api_executor_failover.py` | 429、client timeout、network error、503/504、missing secret/no eligible provider、invalid prompt constraint、invalid envelope request-scoped provider failover / no-failover boundaries。 |
| `tests/executors/test_ai_api_executor_parser.py` | Plain parser success、plugin-owned parse result bridge、plugin-owned parse failure、raw-only mode。 |
| `tests/executors/test_ai_api_replay_guard.py` | Replay no-call artifact checks and missing artifact failure。 |
| `tests/executors/test_ai_api_siliconflow_smoke.py` | Opt-in real SiliconFlow smoke gate; skipped by default。 |
| `tests/test_phase7_ai_api_execution_flow.py` | AIAPIExecutor submission can be recorded by existing Phase 3 `ProtocolEngine.record_execution_submission()` and advances attempt to `Submitted`。 |

## 3. Boundary Notes

- 标准 executor config 只保存 `api_key_env`；真实 smoke 可从被 gitignore 的 `local/ai_api_smoke.local.json` 读取本地 `api_keys`，并按 `models` 展开为标准 provider entries。API key values are not persisted.
- Provider failover is request-scoped and does not create new protocol attempts, leases, graph mutations, canonical binding, reward, or settlement decisions.
- Plugin parsing remains plugin-owned; executor only calls an injected parser hook and persists parsed or parse-failure artifacts.
- Replay guard reads historical artifacts and never calls SiliconFlow or reads API key env vars.
- The default test suite uses fake transport; real SiliconFlow smoke test is opt-in through `TOKENSHARE_RUN_SILICONFLOW_SMOKE=1`.
- `outputs/` 和 `local/*.local.json` 被 `.gitignore` 覆盖，实验输出和本地 smoke secret 文件不进入版本库。
- 2026-06-29 local 模板已按 SiliconFlow Chat Completions 的 `model` 字段配置 6 个不同厂商模型；真实 smoke 使用 raw text prompt，所以不会因 JSON mode 支持差异排除非 JSON 模型。

## 4. Verification Evidence

- Pre-start baseline: `.\init.ps1` passed with `python-json-sqlite-ok`, `harness-files-ok`, pytest collected 268 items, result `268 passed in 29.55s`.
- RED evidence: config test failed with missing `tokenshare.executors.ai_api_config`; descriptor test failed with missing `tokenshare.executors.ai_api`; transport test failed with missing `tokenshare.executors.ai_api_transport`; selector test failed with missing `tokenshare.executors.ai_api_selector`; executor success test failed with missing `AIAPIExecutor`; invalid envelope regression failed because result was `executor_error` instead of `invalid_output`; parser bridge test failed because plugin parser exception escaped; replay guard test failed with missing `tokenshare.executors.ai_api_replay`; package export test failed because `tokenshare.executors` did not export `AIAPIExecutor`; smoke gate test failed with missing `UrlLibSiliconFlowTransport`.
- GREEN targeted evidence: `tests/executors/test_ai_api_config.py` passed `3 passed`; descriptor + registry passed `2 passed`; transport passed `3 passed`; selector passed `3 passed`; success passed `1 passed`; failover + success passed `4 passed`; parser + success + failover passed `6 passed`; redaction targeted passed `1 passed`; executor success/failover/parser suite passed `7 passed`; replay guard passed `2 passed`; package/export executor suite passed `23 passed`; smoke gate default path returned `1 skipped`; final Phase 7 targeted suite `tests\executors tests\test_phase7_ai_api_execution_flow.py -q` passed with `25 passed, 1 skipped in 0.33s`.
- Metadata checks: `feature-list-json-ok`、`phase7-paths-ok`；`git diff --check` 退出码 0，仅有 LF/CRLF warning。
- Final startup verification: `.\init.ps1` passed with `python-json-sqlite-ok`、`harness-files-ok`，pytest collected 291 items，result `290 passed, 1 skipped in 28.61s`。
- Review hardening evidence: RED targeted suite first failed with 9 expected failures covering string boolean config, urllib bad body, missing usage, no-secret, network error, and plugin parse-result bridge gaps; supplemental prompt constraint red test proved string `requires_json_mode` reached transport. GREEN targeted suite passed with `24 passed in 0.48s`; related executor + Factorization parser + Phase 7 flow suite passed with `39 passed, 1 skipped in 0.77s`; `compileall -x "reference_repos" .` exited 0; `git diff --check` exited 0 with LF/CRLF warnings only。

- Final startup verification after hardening: `.\init.ps1` passed with `python-json-sqlite-ok`, `harness-files-ok`, pytest collected 301 items, result `300 passed, 1 skipped in 33.44s`.
- 2026-06-29 local smoke config patch：RED targeted tests first failed with missing `tokenshare.executors.ai_api_local_config`; GREEN local config suite passed with `3 passed in 0.14s`; local config + smoke skip suite passed with `3 passed, 1 skipped in 0.13s`; wider `tests\executors -q` passed with `37 passed, 1 skipped in 0.56s`; final `.\init.ps1` passed with pytest collected 368 items, result `367 passed, 1 skipped in 115.85s`.
