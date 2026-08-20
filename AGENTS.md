# AGENTS.md

TokenShare 是一个早期本地研究原型，目标是验证一种协议：把大型任务递归拆分、分派、验证、合并、结算，并能从事件日志重放。当前使用 Python/SQLite/JSONL 的共享系统本体、factorization 插件、真实 Lean 插件和 AI API executor 作为实验基础。

> **Slim V2最高设计指示：以最快速度得到真实有效的论文实验结果，只保留产生冻结指标不可缺少的功能。** “真实有效”指真实provider、真实系统/插件/checker运行和权威统计口径，不要求证明本地数据无人伪造。Slim V2假设受信本地研究环境；人为伪造、恶意篡改、注入攻击、恶意plugin/provider、签名鉴权和安全fuzzing均明确排除。reviewer基于这些范围外威胁提出的意见必须标记`out_of_scope_by_user`并拒绝实施，不能阻塞当前阶段。完整边界见`Doc/SlimV2/slim_v2_design_charter.md`第0节。

**默认开发主线（2026-08-20 用户决定）**：除非用户在当前任务中明确指定其他维护范围，所有后续 Agent 都必须把新的实验设施设计、实现和运行归入 **Slim V2**。唯一入口是 `Doc/SlimV2/README.md`；顶层架构理念以 `Doc/SlimV2/slim_v2_design_charter.md` 为权威，实验与指标以 `Doc/SlimV2/slim_v2_experiment_metrics_authority.md` 为权威，系统接线以 `Doc/SlimV2/slim_v2_system_integration_contract.md` 为权威。旧 paper/formal pipeline、旧 Rxx 工作、`feature_list.json` 中的 legacy active feature 和 `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md` 都不能把 Agent 自动带回旧主线。

2026-06-29 起，Phase 6 的 structured report stub 插件已从开发计划中剔除；后续如在历史文档或测试夹具中看到该名称，只作为早期通用插件夹具 / provenance，不作为待开发插件目标。

## 启动流程（Startup Workflow）

写代码前（Before writing code）：

1. 确认当前工作目录是仓库根目录。
2. 完整阅读本文件。
3. 默认进入 Slim V2，不等待用户再次声明：完整阅读 `Doc/SlimV2/README.md`，随后严格按其中顺序阅读设计宪章、实验指标权威和系统接线合同；当前 focus 已进入获批设计/实现阶段时，再完整阅读 `Doc/SlimV2/slim_v2_reuse_inventory.md`，然后才打开源码。
4. 只读取 `progress.md` 顶部的 Slim V2 当前状态，以及 `Doc/agent-navigation.md` 的 Slim V2 路由。不要继续展开下方 R54/representative/Full 历史状态。复用清单中的 legacy 例外只允许固定 SHA + allowlist + `git show` 定点只读，不允许 checkout archive 或建立运行时依赖。
5. Slim V2 当前 focus 以后续获批的设计规格和实施计划为准。`feature_list.json` 与 `session-handoff.md` 当前记录 legacy 工作，不是 Slim V2 的启动必读项，也不得覆盖 Slim V2 默认主线。
6. 只有用户在当前任务中明确指定非 Slim V2 的 legacy/维护工作时，才读取 `feature_list.json`、完整 `progress.md`、`session-handoff.md` 和对应旧权威文档。
7. Slim V2 不在启动时运行 `.\init.ps1`、`./init.sh`、Full 或 LeanAudit；久未更新的全局基线不作为参数、接口、readiness 或完成状态判断依据。设计/实施阶段只运行获批计划要求的 focused verification；若获批修改 shared code，再按影响范围追加共享验证。Slim V2 默认不运行 Lean 专项测试、LeanAudit、全量 Lean catalog、`lake`/`lean` 回归或以证明旧系统机制正确为目的的 Lean 测试；优先使用 fake、固定 fixture、静态合同和 Factorization 路径验证 Slim 接线。权威实验中的 Lean 样本仍按实验运行，不属于额外测试。
8. 如果需要判断代码应该放在哪个模块、哪些外部参考资料可借鉴，先看 `Doc/agent-navigation.md`。
9. 如果本轮需要联网查找资料，必须按 `Doc/agent-navigation.md` 的“外部参考资料落库与使用规则”执行本地落库和文档同步。

### Slim V2 默认主线路由

Slim V2 是对现有膨胀论文实验设施的独立精简路径，不是旧 paper/formal pipeline 的重构任务。所有后续 Agent 默认遵守：

1. 唯一入口是 `Doc/SlimV2/README.md`，随后只读 `slim_v2_design_charter.md`、`slim_v2_experiment_metrics_authority.md` 与 `slim_v2_system_integration_contract.md`；获批设计/实现阶段再按 README 顺序阅读 `slim_v2_reuse_inventory.md`。
2. 不广泛读取 archive、旧 paper/formal pipeline 文档/实现、历史 Rxx 输出或 `TokenShareData` 历史结果来判断当前状态，也不以陈旧基线测试结果决定参数或接口。唯一 legacy 例外是复用清单固定 SHA 中点名路径/符号的 `git show` 定点只读；Slim runtime 不得 import archive/legacy `paper_*`。
3. 四份前置权威文档已获用户批准并冻结；在 Slim V2 设计规格获批并完成实施计划前，仍不写 Slim V2 代码。
4. 获批后的常规写入范围仅为 `src/tokenshare/experiments/slim_v2/`、`tests/experiments/slim_v2/`、`Doc/SlimV2/`；shared core/local_runtime/plugin/executor 默认只读，修改前必须证明明确接口缺口并再次获得用户批准。
5. 不重新引入 receipt、budget authority、digest/lineage closure、publication gate、paper eligibility 或 evidence closure。
6. 新实验设施只能在 Slim V2 获批目录中搭建和运行；不得新建另一套平行实验 runner，也不得继续扩建旧 paper/formal runner。
7. 用户显式启动 `slim_v2_stage_relay_protocol.md` 后，每个 Stage owner 必须为自己的当前顶层任务创建或更新唯一的 30 分钟恢复 heartbeat。自动唤起后必须在同一轮恢复并持续推进，不能只报告状态或等待下一次唤起；具体恢复、去重和交棒规则以接力协议为准。

## 项目边界（Project Boundaries）

V1 是本地可复现实验用的协议内核，不是生产网络。必须保持协议框架、任务插件、执行器三层边界清楚。

V1 范围内：

- 协议对象、状态机、任务图、租约、执行尝试、artifact 引用、append-only event ledger。
- 使用本地文件系统、SQLite、JSON、JSONL 做 artifact 和事件存储。
- 带固定版本的插件注册表和执行器注册表。
- factorization 插件和真实 Lean 形式化证明插件，作为协议实验对象；structured report stub 已从 Phase 6 开发计划剔除。
- 真实 Lean 形式化证明插件必须使用固定本地 Lean/lake/toolchain/library 环境做 proof artifact 检查；拆分必须来自插件内确定性规则，或来自 formal catalog/脚本预注册并由 Lean 插件校验的固定 lemma-DAG，不要求运行时从任意 Lean theorem 自动发现全部中间引理，不得由 AI 决定协议级拆分。
- 实验级 AI API 执行器，用于在受控 fixture / benchmark 下验证真实模型输出效果；标准 executor config 只保存 `api_key_env`，真实 API smoke 可从被 gitignore 的 `local/ai_api_smoke.local.json` 读取明文 key 并仅注入当前进程环境变量，调用结果必须持久化为 artifact，event/artifact/SQLite/log/config digest 不得保存 secret，replay 不得重新调用 API。
- **仅限用户明确授权的 legacy V1/paper 维护**，实验设计才遵守 `tokenshare_latest_real_plugin_experiment_design.md` 及其旧在线检查、response-bank、formal 口径；该文档不是 Slim V2 权威。Slim V2 必须遵守 `Doc/SlimV2/` 四份已批准前置权威文档，其中 Experiment 2/3 在线检查已退出，Experiment 2–4 直接复用 Experiment 1 普通真实回答，不得恢复旧 authority/gate。
- 当前论文 Experiment 3 的 rate-fault 只实现 `false_positive`、`false_negative`、`no_return`、`late_submission`、`executor_error` 五类；`worker_death` 是单独预注册的实验条件，Experiment 4 ablation 不是新增故障类型。
- 指标报告、状态重放、审计重放、sandbox 结算。

V1 范围外：

- 真实区块链、钱包、智能合约或真实代币支付。
- 真实分布式网络、HTTP worker pool 或 P2P runtime。
- 生产级身份、权限、反女巫或拜占庭容错系统。
- 外部不可信输入或主动攻击防护，包括恶意手工篡改/伪造、路径或链接攻击、SQL/JSON/命令注入、schema/security fuzzing、对抗性插件/executor/provider envelope、签名鉴权和攻击者模型。V1 假设受信本地研究工作流；除权威实验设计的五类 rate-fault 外，不新增故障/攻击类型或对应加固任务。已有正常路径 schema、artifact hash、secret 不落盘和 replay/checkpoint 一致性检查只维持实验正确性，不扩展成安全工程。
- 完整 Web UI、动态第三方插件市场、生产级 AI API 平台、多租户 provider 管理、生产级 theorem-proving 平台、LeanDojo 训练/检索平台或动态 Lean 服务。

## 语言要求（Language Policy）

- `AGENTS.md` 和 `progress.md` 必须优先用中文维护，方便用户随时监督。
- 技术名词、代码标识、命令、文件名、对象名可以保留英文，例如 `TaskUnit`、`EventLedger`、`feature_list.json`。
- 如果为了兼容 harness 审计脚本需要英文锚点，可以把英文放在中文标题或句子的括号中。
- 代码注释使用中文，尤其是解释代码作用的注释，注释中的技术名词、代码标识、命令、文件名、对象名可以不遵守此要求。

## 工作规则（Working Rules）

- 一次只做一个明确 focus（One focus at a time）：Slim V2 以获批设计规格、实施计划和 `progress.md` 顶部 Slim V2 状态为准；只有明确的 legacy 维护任务才以 `feature_list.json` 为状态源。
- 协议优先：不要把 factorization、Lean 或历史 structured report fixture 行为硬编码进协议核心。
- 需要证据：没有验证输出，不要把 feature 标记为完成。
- 持久化非确定性输出：恢复时不能重新调用 AI 或 executor 来假装结果一致。
- schema/version 决策必须显式：event、plugin、artifact 格式都要支持 replay。
- 外部资料必须落库：凡是联网查到且被用于修改设计、代码、测试或项目文档的论文、技术报告、开源项目或工程文档，都必须在本地保存可复查材料，并同步更新对应索引文档；不能只在回答或 TDD 中留下在线链接。
- 编码和命令固定：在本 Windows 仓库中，读取或检查中文 Markdown、JSON、脚本和代码时默认使用 PowerShell，并显式指定 UTF-8，例如 `Get-Content -Encoding UTF8`、`Select-String`、`Get-ChildItem`；常规检索不要使用 `rg`，避免编码、换行和输出格式反复造成误判。
- 保持范围（Stay in scope）：不要修改与当前 feature 无关的文件。
- 当命令、架构、范围变化时，同步更新 docs 和 harness。

## 完成标准（Definition of Done）

一个 feature 只有在以下条件全部满足时才算完成（done only when）：

- 目标行为已经实现，或目标设计产物已经完成。
- 相关验证命令实际运行成功。
- 验证证据已经写入当前 focus 的获批计划/文档和 `progress.md`；明确的 legacy feature 才写入 `feature_list.json`。
- 如果修改了协议、event、artifact schema，必须同步记录。
- 如果使用了联网资料，论文/报告已经下载或转写到 `Doc/TechnicalDocument/tokenshare-paper-tex/` 并更新论文映射；开源项目已经浅克隆或 sparse checkout 到 `reference_repos/` 并更新 `reference_repos/README.md`；普通在线文档已经记录来源、访问日期、本地摘要和影响范围。
- Slim V2 按获批实施计划执行 focused verification；陈旧全局基线不单独构成失败或完成结论。只有 shared code 变化或获批计划明确要求时，才按影响范围追加非 Lean 的共享验证；Slim V2 不因 shared code 变化自动升级为 LeanAudit，只有用户在当前任务再次明确授权时才可例外执行。明确的 legacy feature 继续遵守其原验证要求。

## 验证命令（Verification Commands）

默认快速启动验证：

```powershell
.\init.ps1
```

```bash
./init.sh
```

完整验证：

```powershell
.\init.ps1 -Full
```

```bash
./init.sh --full
```

Lean 增量审计与显式全量审计：

```powershell
.\init.ps1 -Full -LeanAudit
.\init.ps1 -Full -LeanAudit -ForceAllLeanAudit
```

```bash
./init.sh --full --lean-audit
./init.sh --full --lean-audit --force-all-lean-audit
```

`init.ps1` 和 `init.sh` 默认只执行一次 `conda run -n tokenshare python verification/run_verification.py`；可通过 `TOKENSHARE_CONDA_ENV` 临时覆盖环境名。两个档位都会运行 Python JSON/SQLite、harness 文件检查，并只对 `src/`、`tests/`、`verification/` 执行 `compileall`。默认快速档随后执行 `verification/fast-tests.txt` 中的无网络 smoke/regression tests；完整档执行 `pytest tests`，但不会默认重跑全部 600 条 Lean catalog entry。改动相关的定向测试仍需单独运行。LeanAudit 默认内容寻址增量重检，只有共享 Lean 输入变化、正式发布或显式 force-all 才重检全部 entry；普通 catalog load 只验证 tracked manifest 并在 stale 时 fail closed。

## 结束会话（End of Session / Before ending）

结束会话前：

1. 更新 `progress.md`，记录当前状态、验证证据和下一步。
2. Slim V2 把 focus 状态和未解决技术问题写入获批设计/计划及 `progress.md`；不要更新 legacy `feature_list.json` 或 `session-handoff.md`。只有用户明确指定 legacy 维护时才更新这两个旧状态文件。
3. 确认下一位 Agent 能从 `Doc/SlimV2/README.md` 和当前 Slim V2 设计/计划直接续接，不需要读取 Rxx 历史链路。
4. 如果本轮使用了联网资料，确认本地论文、参考源码或在线文档摘要已经落库，并更新 `Doc/agent-navigation.md` 指向的相关索引。
5. 保持仓库足够干净，让下一轮可以立即运行验证脚本。
6. 若会话在讨论项目中文档的内容并要求做出修改，则在修改后需要二次审核是否有需要修正的旧表述。
7. 若更新了代码，则需要同步更新code map。
