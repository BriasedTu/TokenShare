# EPD-027 accepted-Task 测试压缩实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`。每个 Task accepted 后执行本计划；测试压缩通过独立复审后，才进入下一 Task。

**Goal:** 删除一次性、重复和实现细节型测试，只保留证明唯一协议不变量、跨层接口或真实缺陷回归所需的最小测试集合。

**Architecture:** 先从当前 Task 的 commits 精确识别新增/扩展测试，再建立“测试 → 唯一不变量”映射。只删除或合并测试，不改变生产行为；保留每个唯一 fail-closed 边界的一条最小回归，以及至多一条正式跨层路径。压缩后运行最小受影响套件并由独立 reviewer 审核覆盖映射与反影子边界。

**Tech Stack:** Python、pytest、PowerShell、Git；所有离线 pytest 都加载 `verification.pytest_network_tripwire`。

---

### Task A: 只读盘点当前 accepted Task 的测试增量

**Files:** 当前 Task 与其 prerequisite commits 修改过的测试文件；不读历史输出或大日志。

- [ ] 用 `git show --stat`、`git diff <base>..<head> -- <tests>` 只定位本 Task 新增/修改的测试，不把既有未触及测试纳入删除范围。
- [ ] 为每个新增测试记录唯一不变量、与其他测试的覆盖重合、是否只验证实现细节、是否曾捕获真实缺陷。
- [ ] 输出精确保留/合并/删除清单和预计 pytest case 数下降；不修改文件、不运行测试。

### Task B: 最小化测试实现

**Files:** 只修改 Task A 清单中的测试文件；生产源码只读。

- [ ] 保留：每个唯一协议不变量一条；每个真实 review 缺陷一条；每个正式跨层接口至多一条代表路径。
- [ ] 合并：等价 invalid-field、枚举或 permutation 用单个表驱动循环测试表达，避免为同一判断产生多个 pytest case。
- [ ] 删除：一次性 RED、重复 happy path、只锁定私有 helper/行序/内部实现的断言、已由更强跨层测试完全覆盖的弱测试。
- [ ] 禁止：删除 network tripwire、paper eligibility/固定分母/hash-chain/typed producer/反影子边界；为了减少计数而弱化生产校验；把组件测试冒充全链路。

### Task C: 压缩后验证与独立复审

- [ ] 运行压缩后最小受影响 pytest 命令，必须使用 `-p verification.pytest_network_tripwire -q`；不运行 Fast、Full、LeanAudit或真实 API。
- [ ] 记录压缩前/后文件行数、pytest collected/pass 数、删除/合并数量、provider calls=0。
- [ ] 独立 reviewer 逐项核对不变量映射、生产代码零修改、正式 coordinator/engine边界仍有代表性回归；Critical/Important为0才PASS。
- [ ] scoped `git diff --check`、secret/output检查通过后创建独立测试压缩提交。
- [ ] 更新最小交接与状态文件：记录压缩证据，不把“更少测试”写成“更少验证要求”。

### Task 3 首次执行范围

- [ ] Base=`d2b16f89`；accepted commits=`860b7c48`,`4ea293b1`,`f9773944`。
- [ ] 只审上述提交实际新增/修改的测试 hunks；保护 Task 0 network tripwire 与 Task 2 metric-contract测试。
- [ ] 优先压缩 direct-result、ledger-binding、typed-hook三组重复 strict-schema/permutation测试；至少保留 ledger swap、bool-as-int、forged hook payload、pre-provider provenance、固定分母与禁用 engine 不得paper假成功的回归。
