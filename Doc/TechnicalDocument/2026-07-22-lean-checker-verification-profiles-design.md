# Lean Checker 验证分层与持久化 Preflight 设计

日期：2026-07-22

状态：已实施并完成定向验证；作为仓库通用 Lean 验证方案使用

适用 feature：`feat-011` Paper Real AI Experiments 的 Lean catalog、Lean checker 回归和仓库验证流程优化。本文不改变论文实验样本、正式 checker 证据或协议生命周期边界。

## 1. 问题与目标

以下是 2026-07-22 设计实施前的问题快照，不描述当前 loader 行为：当时 `load_paper_catalogs()` 在加载 catalog 时同步运行真实 Lean preflight；一次 cache miss 会启动 30 个 shallow direct-case checker 和 570 个 lemma-graph node checker，共 600 个独立 `lake env lean` 子进程。进程内字典缓存只在当时的当前 Python 进程有效，新的 pytest 或 CLI 进程会重新执行全部检查。后文第 3–7 节定义的分层验证与持久化 preflight 已实施。

本机诊断证据如下：

- `tests/plugins/lean_proof/test_lean_checker_direct.py` 首次真实检查约 8.8 秒，后续检查约 0.76–0.84 秒。
- 单独测量的热启动 `lake env lean` 约 0.58 秒；预先准备 Lake 环境后直接运行 `lean.exe` 约 0.23 秒。
- 当时快速 `init.ps1` 的 pytest 为 288 passed、1 skipped、14.33 秒；脚本总墙钟约 40 秒，额外时间主要来自四次 `conda run` 和 `compileall`，不是实际 Lean checker。
- 历史 catalog、adapter 和完整验证的 7–13 分钟耗时与 600 次串行 checker 启动一致。

本设计目标：

1. 日常 catalog 加载和普通单元测试不隐式启动 Lean。
2. 保留少量真实 Lean smoke，持续验证 subprocess、import、accept/reject 和 artifact 契约。
3. 用 tracked、内容寻址的 preflight manifest 保存完整 catalog 审计结果；普通加载只验证 manifest 完整性和失效条件。
4. 完整 catalog 审计成为显式、可复现、无网络、零 provider-call 的验证档位。
5. 正式 AI proof candidate 仍逐 attempt 调用真实 Lean checker；replay 仍禁止重新调用 Lean。
6. 把 Windows/Bash 启动检查统一到一个 conda Python 入口，减少重复环境启动。

## 2. 不变边界

以下行为不得因测试提速而改变：

- 正式 Experiment 1–5 中所有可采信 Lean proof candidate 必须由固定本地 Lean/lake/toolchain/project 环境真实检查。
- 正式 per-node proof、proof-file assembly、dependency-aware merge 和 root recheck 必须保留真实 checker report、log、proof artifact 和 `EnvironmentRef`。
- catalog oracle/preflight manifest 不能替代正式模型输出的 checker evidence。
- fake checker、recorded test report 和 scripted transport 只能用于回归，不能产生 `paper_eligible=true`。
- replay、metrics 和 report 只能读取持久化历史 evidence，不得调用 Lean 补写成功事实。
- 正式 3×3 Lean selection 保持每格恰好 15 道、共 135 道；不得因提速改变 catalog、selection、固定 lemma-DAG 或 oracle package。

## 3. 验证档位

仓库验证分为三个正交档位。

### 3.1 Fast

命令保持 `.\init.ps1` / `./init.sh`。它运行 JSON/SQLite、harness、仅对 `src/`、`tests/`、`verification/` 执行 compileall，以及 `verification/fast-tests.txt`。Fast 不运行真实 Lean subprocess，只验证 Lean schema、environment manifest 纯逻辑和 tracked preflight manifest 的静态完整性。

### 3.2 Full

命令保持 `.\init.ps1 -Full` / `./init.sh --full`。它运行全部 Python regression 和一组小型真实 Lean smoke，但不重新执行完整 600-entry catalog audit。真实 smoke 至少覆盖：

- valid proof accepted；
- invalid proof rejected并保存日志；
- `sorry`/`admit` 即使 Lean 返回 0 也被拒绝；
- project helper import 可解析；
- child proof 和 merge/root proof 各一条真实路径。

除 Lean catalog/plugin/toolchain 相关改动和发布论文实验外，feature 完成使用 Full 即可。

Full 不能只信任 tracked manifest。即使所有摘要均未变化，也必须运行固定、确定性的 Lean canary bundle。bundle 由两部分组成：少量真实 subprocess 用例覆盖 direct/child/merge 的 accepted/rejected 和 placeholder 拒绝路径；快速契约测试覆盖 timeout、artifact binding、checker mode、rejected canonical pollution；3×3 readiness 测试冻结九个 cell 的选择和摘要。它不是从 catalog 随机抽样，数量必须是与 catalog 总 case/node 数无关的小常数。

迁移 Task 之间需要独立运行该 bundle 时，使用：

```powershell
conda run -n tokenshare python verification/run_verification.py --mode full --only-lean-canary
```

### 3.3 LeanAudit

新增 `.\init.ps1 -Full -LeanAudit` / `./init.sh --full --lean-audit`。它在 Full 后显式执行内容寻址的增量 catalog preflight：先验证 tracked manifest 和完整 expected coverage，再只重检 cache key 变化的 entry；验证模式不得修改 tracked 文件。若共享 checker/toolchain/fixture helper/entry source rendering 输入变化、无法分类的 entry 依赖变化，或显式指定 `-ForceAllLeanAudit`，则 600 个 entry 全量失效并全部重检；只影响 child/merge/root assembly 的变化进入定向测试和 canary，不伪造 catalog entry 失效。

以下情况强制运行 LeanAudit；其中发布论文结果、共享 checker/toolchain/helper/entry source rendering 改动必须同时使用 `-ForceAllLeanAudit`：

- 修改 Lean catalog、Task 14 readiness selection 或 oracle package；
- 修改 Lean checker、Lean plugin proof assembly/merge、fixture project、toolchain 或 environment digest 规则；
- 发布、引用或重新生成正式论文实验结果前。

另提供显式 refresh 命令更新 tracked manifest。refresh 先写临时文件，只有全部条目 accepted 且 manifest 自校验通过时才原子替换；失败不得覆盖上一次 good manifest：

```powershell
conda run -n tokenshare python -m tokenshare.experiments.lean_catalog_audit --refresh --force-all
```

## 4. Catalog Loader 与 Audit 分离

`load_paper_catalogs()` 改为纯加载/校验入口。它继续验证 schema、case identity、oracle shape、3×3 matrix、environment digest 和 catalog digest，但不直接调用 `check_lean_proof()`。

加载器读取 tracked `benchmarks/paper/lean_checker_preflight.v1.json`，验证：

- manifest schema 和自摘要；
- shallow catalog、lemma-graph catalog、readiness manifest、fixture project 和 checker implementation 的输入摘要；
- `environment_digest` 与当前固定环境相等；
- coverage 中没有缺失、重复或多余的 case/node；
- 所有正式可采信条目状态均为 `accepted`；
- coverage 计数和 proof-digest bundle 与 manifest body 一致。

任一检查失败时 fail closed，抛出稳定的 stale/mismatch 错误，并提示运行 LeanAudit 或 refresh；加载器不得自动执行昂贵 preflight，也不得静默跳过条目。

当前实现的 `tokenshare.lean_catalog_preflight_manifest.v1` 包含：

- `schema_version,manifest_digest,generated_at`；
- `source_digests`，覆盖 catalog/readiness/fixture/checker implementation 的内容摘要；
- `environment_digest,checker_implementation_digest`；
- `checked_direct_case_count,checked_graph_case_count,checked_graph_node_count,total_entry_count`；
- `entries[]`，每项包含 `entry_id,case_id,node_id|null,checker_mode,generated_source_digest,oracle_proof_digest,entry_key,status` 和确定性 checker 结果摘要；
- `proof_digest_bundle,status`。

manifest 只保存确定性摘要和覆盖证据，不保存临时 artifact 路径。完整 audit 的 stdout/stderr/checker reports 写入被 gitignore 的 audit output root；tracked manifest 用摘要引用它们的语义结果。

### 4.1 每条 entry 的增量真实检查

tracked manifest 的每条 direct case 或 lemma-graph node 都必须有独立 cache key：

```text
sha256(
  generated_source_digest,
  oracle_proof_digest,
  environment_digest,
  checker_implementation_digest,
  checker_mode,
  resource_limits
)
```

每次 Full 的 catalog loader 测试或 LeanAudit 都先枚举完整 expected coverage 并重算全部 cache key。匹配条目复用以前真实 Lean 检查的确定性证据；缺失或不匹配条目在 audit 中立即重新执行真实 Lean，而普通 loader 只 fail closed 并提示 audit/refresh。复用不是跳过验证：当前运行必须重新验证 manifest 自摘要、entry key、coverage 和确定性结果 bundle。

`checker_implementation_digest` 由实际参与 catalog entry source rendering、placeholder policy、subprocess、preflight request 和 environment resolution 的 source files 内容计算，不依赖人工记得升级版本号。child/merge/root assembly 不改变这 600 条 oracle entry 的 source 时，不伪造 entry 失效，而由 checker-call spy、assembly 定向测试和真实 canary 覆盖。`environment_digest` 必须覆盖 `lean-toolchain`、lakefile、`lake-manifest.json`、Lean/Lake executable/version、fixture helper source tree、import set 和运行平台。若实现无法证明某个输入是否影响 entry 结果，必须 fail closed，把相关 scope 视为失效。

失效范围固定如下：

- 单个 catalog/oracle/generated source 改变：只重检对应 direct case 或 node，以及依赖该 node 的 assembly/root canary。
- checker、entry source rendering、environment resolution、Lean toolchain、lakefile/manifest 或 fixture helper 改变：全量 entry 失效。
- child/merge/root assembly、runtime/core/experiment orchestration 改变但 catalog entry source/checker 输入未变：复用 catalog proof evidence，同时运行穷举 checker-call 契约测试、assembly 定向测试和真实 end-to-end canary。
- 未识别或无法归类的依赖改变：全量 entry 失效。

LeanAudit 默认使用增量模式并报告 `reused_entry_count`、`rechecked_entry_count`、`canary_entry_count` 和 `invalidated_by[]`。发布论文实验、修改共享 checker/toolchain/helper 或显式传入 `--force-all` 时，必须忽略 entry cache 重检完整 coverage。

## 5. Checker 注入边界

新增最小 `LeanChecker` callable protocol，签名与当前 `check_lean_proof(request, *, artifact_store, environment_manifest)` 相同。生产默认值始终是真实 subprocess checker。

需要 checker 的 Lean child、merge、fixture 和 paper adapter 路径显式接收并向下传递该 callable。正式 CLI 不暴露 fake backend 选项，也不从环境变量切换 backend。

测试目录提供 deterministic fake checker。它生成与 `LeanCheckerReport` 契约一致的 report、log 和 proof artifact，以便测试 orchestration、artifact linkage、failure propagation 和 paper-result projection；测试用例显式选择 accepted/rejected/timeout 结果。fake report 的 command summary 标记 `backend=test_fake`，且 paper eligibility 审计必须把该 backend 判为 regression-only。

绝大多数 adapter/runner 测试使用 injected fake checker。真实 subprocess 只保留在 Full smoke 和 LeanAudit，从而避免单元测试按 child/node 数重复启动 Lean。

所有使用 fake checker 的 runtime/adapter 测试必须同时使用 spy 断言预期 unit、checker mode、调用次数和 artifact binding。这样可以发现“代码忘记调用 checker”或“rejected result 仍进入 canonical/merge”的 bug，而不是只验证 fake 返回值。真实 canary 再验证 fake 契约没有与 subprocess backend 漂移。

## 6. Lean Subprocess 启动优化

真实 checker 第一次使用某个 `environment_digest` 时，执行一次 `lake env <python> ...`，捕获 Lake 实际设置的 Lean 相关变量。缓存仅保存 allowlist：`ELAN_HOME`、`LAKE_HOME`、`LEAN_AR`、`LEAN_CC`、`LEAN_CXX`、`LEAN_PATH`、`LEAN_SRC_PATH`、`LEAN_SYSROOT` 和 `PATH`；不得缓存 API key 或其他任意进程环境变量。

每次 proof check 把当前进程环境与该 allowlist 合并后直接调用 manifest 指定的 `lean_executable`。command summary 记录 direct executable、Lake bootstrap method 和变量名列表，不记录环境值。该优化必须满足：

- 相同 generated source 在 `lake env lean` 与 direct configured `lean` 下 accept/reject 一致；
- project helper imports 可解析；
- timeout、stdout/stderr 截断和 placeholder policy 不变；
- bootstrap/cache 按 `environment_digest + executable paths + project_root` 隔离；
- bootstrap 失败返回 `environment_error`，不得回退到未记录的系统 Lean。

catalog batching 暂不进入本 feature。分批编译会改变 per-entry failure localization 和 checker artifact 形状；完成测试分层和 direct startup 后，只有 LeanAudit 仍不能接受时再单独设计。

## 7. 单一验证入口

新增 `verification/run_verification.py`，由 `init.ps1` 和 `init.sh` 通过一次 `conda run -n <env> python` 调用。它负责：

1. JSON/SQLite 可用性和 harness 文件检查；
2. 调用当前解释器仅对 `src/`、`tests/`、`verification/` 完成 compileall；
3. 根据 fast/full 选择 pytest 参数；
4. Full 固定运行真实 canary；根据 `--lean-audit` 调用增量或 `--force-all` catalog audit；
5. 保留现有清晰的退出码和摘要输出。

PowerShell/Bash 只负责参数解析、环境变量和调用，不重复实现验证规则。若已经处于目标 conda environment，仍可直接使用当前 Python，但该优化不能改变默认环境名或 `TOKENSHARE_CONDA_ENV` 覆盖规则。

## 8. 测试与验收

实现必须使用 TDD，至少证明：

1. RED：普通 catalog load 会调用 checker；GREEN：匹配 manifest 时 checker 调用数为 0。
2. RED：catalog、oracle、readiness、fixture、checker version 任一摘要变化仍被接受；GREEN：稳定 stale error 且 checker 调用数为 0。
3. RED：缺失/重复/额外 entry 或非 accepted entry 被接受；GREEN：fail closed。
4. RED：audit failure 会覆盖 good manifest；GREEN：原子写入只发生在完整成功后。
5. RED：adapter 单元测试必须启动真实 Lean；GREEN：injected fake 生成完整 artifact/report evidence 且不具论文资格。
6. RED：direct configured Lean 无法导入 fixture helper；GREEN：与 `lake env lean` 行为等价。
7. RED：验证脚本产生多次 conda 环境启动；GREEN：PowerShell/Bash 各只有一个默认 conda Python 入口。
8. Fast、Full、Full+LeanAudit 的选择逻辑和帮助文本有回归测试。
9. entry key 的每个依赖分别发生变化时，受影响范围按第 4.1 节失效；未知依赖触发全量失效。
10. fake checker spy 能发现缺失 checker 调用、错误 checker mode、rejected canonical pollution 和缺失 artifact binding。

最终验证顺序：定向 RED/GREEN、Lean plugin impact、paper catalog/adapter impact、`tests/experiments`、Fast、Full、Full+LeanAudit。最后同步 `AGENTS.md`、`README.md`、`Doc/agent-navigation.md`、`tokenshare_v1_code_map.md`、`feature_list.json`、`progress.md` 和 `session-handoff.md`，明确新的完成与发布门槛。

## 9. 成功标准

- Fast 和普通 catalog/model/runner 单元测试不启动 Lean。
- Full 的真实 checker 调用数量为固定小常数，而不是随 catalog case/node 数增长。
- LeanAudit 对当前 600-entry coverage 产生与 tracked manifest 一致的结果，provider calls 为 0。
- 增量 audit 对未变化 entry 复用真实历史证据，对变化 entry 执行真实 Lean；`--force-all` 不复用任何 entry。
- Full 每次真实运行固定 canary，并通过 spy/fake 契约覆盖所有 checker 调用分支。
- 正式 AI Lean execution 仍逐 attempt 使用真实 checker，并保留现有 artifact/event/evidence 契约。
- 日常验证墙钟显著下降；最终状态文档记录优化前后实测值，不预设必须达到的绝对秒数。
- 未修改 Lean 3×3 selection、正式实验条件、论文样本量或 replay 规则。

## 10. 已实施结果与实测

截至 2026-07-22，本设计已经落地：

- `load_paper_catalogs()` 只做静态 catalog/manifest 校验，匹配证据时真实 checker 调用数为 0；stale、tampered、coverage 缺失或非 accepted entry 均 fail closed，不回退到隐式全量检查。
- `lean_catalog_audit` 提供 `--verify`、`--refresh` 和 `--force-all`；未变化 entry 可复用，所有摘要、coverage、entry key 和结果 bundle 每次重新校验；所有路径 `provider_calls_made=0`。
- adapter/runtime 测试通过显式 `LeanChecker` 注入使用 schema-compatible fake，并用 spy 验证调用次数、mode、artifact binding 和 rejection propagation；非生产 backend 自动使结果失去 paper eligibility。
- checker 按环境摘要只进行一次 Lake bootstrap，后续直接调用固定 `lean.exe`；本机 cold 首次检查约 6.29 秒，warm 检查约 0.25–0.35 秒。
- 600-entry `--force-all` 审计由旧实现约 441.7 秒降至 188.8 秒，600/600 accepted；tracked manifest 摘要为 `sha256:3d7d6888dacd15e4467f5532697c94158282c4d1986827e513fb9d1664eadbf2`。
- Fast 首次改造后实测为 290 passed、1 skipped，pytest 11.93 秒、脚本总墙钟约 28.5 秒；最终冷态复验同为 290 passed、1 skipped，pytest 25.30 秒、runner 总墙钟 46.1 秒，说明公共 compile/test 墙钟仍受机器冷暖状态影响。两次都不启动 600-entry audit。
- 最终固定 Lean canary bundle 为 11 passed，pytest 47.15 秒，runner 总墙钟 61.6 秒；它额外覆盖 timeout、`admit` 和 checker injection/rejection 契约，耗时仍与 catalog entry 数无关。
- 2026-07-22 当时 Full 曾被 3 个 paper 断言漂移阻塞；这些问题随后已由对应实验迁移修复，不能继续视为当前 blocker。本文只规定 Lean 分层验证方法；当前 Full 结果以 `progress.md` 的最新实际运行证据为准。

因此，日常开发使用 Fast；普通 feature 完成使用 Full；Lean 相关迁移 Task 额外使用固定 canary 和增量 audit；正式发布/共享 Lean 输入变化使用一次 force-all audit。这个分层不减少 bug 检查面，而是用静态证据、spy 契约、小型真实 canary 和按内容失效的全量证据分别覆盖不同风险。
