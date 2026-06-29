# Phase 6 Lean 工具链配置记录

日期：2026-06-29

状态：实现证据记录。本文只记录本轮真实 Lean checker 最小闭环使用的本地工具链来源、版本、安装位置和验证命令，不替代 `Doc/TechnicalDocument/2026-06-29-phase-6-lean-real-plugin-tdd.md`。

## 1. 使用来源

- Lean 官方安装说明：`https://lean-lang.org/install/manual/`，访问日期 2026-06-29。本轮只用于确认 Lean 推荐通过 `elan` 管理工具链，项目通过 `lean-toolchain` 固定 Lean 版本。
- `elan` GitHub release：`https://github.com/leanprover/elan/releases/tag/v4.2.3`，访问日期 2026-06-29。
- `elan-x86_64-pc-windows-msvc.zip` 下载 URL：`https://github.com/leanprover/elan/releases/download/v4.2.3/elan-x86_64-pc-windows-msvc.zip`。
- GitHub release API 给出的 Windows zip digest：`sha256:be5e92a2dfdd8176099b2db0b810c27237c9054f1e5db1126f4f2a1134773b25`。
- Lean 4 release：`https://github.com/leanprover/lean4/releases/tag/v4.8.0`，发布时间 2024-06-05。

这些来源影响了本轮代码、测试和 fixture project，因此按 `Doc/agent-navigation.md` 第 6 节在本文落库。

## 2. 本地安装位置

- 工具根目录：`%LOCALAPPDATA%\TokenShare\LeanToolchain`
- 下载包：`%LOCALAPPDATA%\TokenShare\LeanToolchain\downloads\elan-v4.2.3-x86_64-pc-windows-msvc.zip`
- 解压目录：`%LOCALAPPDATA%\TokenShare\LeanToolchain\elan-v4.2.3`
- `ELAN_HOME`：`%LOCALAPPDATA%\TokenShare\LeanToolchain\elan-home`
- `elan.exe`：`%LOCALAPPDATA%\TokenShare\LeanToolchain\elan-home\bin\elan.exe`
- `lean.exe` shim：`%LOCALAPPDATA%\TokenShare\LeanToolchain\elan-home\bin\lean.exe`
- `lake.exe` shim：`%LOCALAPPDATA%\TokenShare\LeanToolchain\elan-home\bin\lake.exe`
- pinned toolchain：`%LOCALAPPDATA%\TokenShare\LeanToolchain\elan-home\toolchains\leanprover--lean4---v4.8.0`

安装命令使用 `elan-init.exe -y --no-modify-path --default-toolchain none`，没有修改系统 PATH。本轮所有真实 checker 测试通过显式 `ELAN_HOME` 和 executable 路径运行。

## 3. 版本与校验

本轮下载校验：

```text
downloaded_sha256 = be5e92a2dfdd8176099b2db0b810c27237c9054f1e5db1126f4f2a1134773b25
expected_sha256   = be5e92a2dfdd8176099b2db0b810c27237c9054f1e5db1126f4f2a1134773b25
match             = true
```

版本输出：

```text
elan 4.2.3 (b6cec7e10 2026-06-08)
Lean (version 4.8.0, x86_64-w64-windows-gnu, commit df668f00e6c0, Release)
Lake version 5.0.0-df668f0 (Lean version 4.8.0)
```

fixture project 的 `lean-toolchain` 固定为：

```text
leanprover/lean4:v4.8.0
```

## 4. Fixture project 验证

固定 Lean project 路径：

```text
fixtures/lean_proof_project/
```

构建命令：

```powershell
$env:ELAN_HOME = Join-Path $env:LOCALAPPDATA 'TokenShare\LeanToolchain\elan-home'
& "$env:ELAN_HOME\bin\lake.exe" build
```

验证输出摘要：

```text
Built TokenShare.SplitRules
Built TokenShare.Merge
Built TokenShare.Helper
Built TokenShare.Fixtures.Direct
Built TokenShare.Fixtures.Decomposition
Built TokenShare.Fixtures.Unsupported
Built TokenShare.Fixtures.Invalid
Built TokenShare
Build completed successfully.
```

## 5. 当前实现影响范围

本地工具链已用于 `src/tokenshare/plugins/lean_proof/checker.py` 的真实 direct proof checker 测试、`src/tokenshare/plugins/lean_proof/split_strategy.py` 的 split helper certificate 测试、child proof flow、merge proof root recheck、Phase 8 Lean adapter ready path 和 replay evidence guard：

- 有效 proof candidate 使用 Lean 4.8.0 返回 `accepted`。
- 无效 proof candidate 使用 Lean 4.8.0 返回 `rejected`。
- `by sorry` 在 Lean 4.8.0 可返回 exit code 0，但插件现在会根据 proof source / Lean warning 将其标记为 `rejected`，且不生成 proof artifact。
- proof candidate 必须带 schema、`proof_candidate_id`、`theorem_payload_digest`、`proof_source` 和 `created_at`，且 digest 必须绑定到 theorem payload。
- generated Lean source、stdout、stderr、checker report 和 proof artifact 均由 `ArtifactStore` 持久化。
- split helper 使用固定 `lake build` 后执行 `lake env lean <generated_split_helper_source>`，输出 `lean_proof.split_certificate.v1` JSON，并由 `ArtifactStore` 持久化 generated source、stdout、stderr、certificate 和 helper report；Python bridge 会复核 parent goal elaboration、`decomposition_policy` 和 merge-rule 支持边界。
- 当前 deterministic split helper 仅对 conjunction / iff 产出可执行 merge plan；implication intro / forall intro 在未实现对应 verified merge 前输出 `unsupported_merge_rule`，其他不可覆盖形状输出 `unsupported_goal_shape`。它是 TDD Task 6/7 的受控 fixture-policy slice，不是完整生产级 Lean metaprogram/tactic search。
- merge proof policy 读取 accepted child proof artifact source，嵌入 root merge proof 的局部 `have`，再由同一固定 Lean checker 复验。
- Phase 8 `LeanProofExperimentAdapter` 默认先运行真实 Lean preflight；缺 toolchain / fixture project 时输出 structured blocked reason，而不是默认 ready。
- `LeanEnvironmentManifest.environment_digest` 不再包含 `created_at`，避免同一固定环境因 manifest 生成时间变化而改变身份；`created_at` 仍保存在 manifest / `EnvironmentRef` 元数据中。

`feat-007` 已在后续实现中完成并标记 done；当前 active feature 是 `feat-010` Phase 9 replay / audit。若未来扩展更多 Lean decomposition 规则，必须同时提供对应 verified merge policy 和 checker-backed 回归测试，不能只输出 supported split certificate。
