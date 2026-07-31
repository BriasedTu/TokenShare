# FEAT-011 Full 资源保护 A/C/D 设计

> **状态（2026-07-31）：部分 superseded。** 本文原 A 的 generation/manifest v2
> 方案已由 `2026-07-31-feat-011-full-delta-checkpoint-and-shared-baseline-design.md`
> 的 immutable generation v3 delta + terminal snapshot 方案替代；不得按本文 v2
> 章节继续实现。C 已按 commit-token exact cleanup 落地。D 已改为 calibrated
> forecast + rolling remaining counters；原 `attempts × max_tokens × 4` 全局启动门禁
> 与“旧 47,762-root 在 439.85 GiB 必须拒绝”的结论均已废止。

## 目标与边界

本批补齐正式 full suite 的三类资源保护：A 为分层 evidence manifest v2，C 为 canonical checkpoint 后不保留双份 adapter working tree，D 为可审计磁盘估算与执行前容量门禁。同时把 `_last_complete_event_ref()` 改为常量内存扫描。只修改 `paper_formal_evidence.py`、`paper_formal_runner.py`、`paper_budget.py`、对应定向测试与 code map；不调用真实 API，不运行 Full 或 LeanAudit。

历史 evidence 不迁移。v1 只允许完全只读的 load/replay；需要写入的 incomplete v1 resume 明确拒绝，已经闭合的 v1 suite 可直接返回。PENDING、临时文件、非 CURRENT generation 与未引用 orphan 均不算论文 evidence；v2 只验证受信工作流声明的引用闭包，不增加对不受信额外文件的全树 exact-set 或攻击防护。

## A：分层 evidence manifest v2

### 顶层 schema

新运行直接写 `tokenshare.paper_evidence_manifest.v2`。顶层只含：

- `static_files`：固定 suite 文件、冻结 smoke/recovery 文件、已知 experiment manifest 及正式 finalization 文件的 `FileEvidence`；路径来自固定 allowlist、冻结 experiment ids 或受控文档名，不遍历全树。
- `conditions`：按 `(experiment_id, condition_id, repeat_id)` 唯一的 condition inventory。每项含 `condition_manifest_ref` 与 `reachable_size_bytes`，后者只用于 resume 磁盘剩余量估算，不替代完整性验证。

checkpoint 热路径在写入 generation v2、`PENDING.json`、`CURRENT.json` 和 `condition_manifest.json` 后，只重算当前 run 的引用链并替换一个 condition inventory entry。它不得 `rglob` suite、重哈希其他 conditions 或把所有 root 路径保存在 Python 内存。suite/experiment finalization 只刷新固定 static refs；checkpoint PENDING repair 只补对应 condition entry。runner 的 experiment/suite finalization intent 在 target 写完、inventory 刷新后才删除。

### 递归验证链

v2 load 对每个 inventory entry 依次验证：

1. `condition_manifest_ref` 的路径、size、hash 与 condition identity；
2. condition manifest 中的 `CURRENT`、generation manifest、run manifest refs；
3. `CURRENT.generation_id` 与 generation manifest digest；
4. generation manifest 精确声明 run manifest 与五个 canonical run files；
5. run manifest 的 experiment/condition/repeat/task/status 与五个文件内容一致；
6. artifact index 中的 payload path、size/hash 与 task/attempt/event/fault 内可达 ArtifactRef 闭包一致。

任一步缺失、漂移、重复或交叉引用均 fail closed。generation v1 仍仅为历史 v1 manifest load 服务；新 manifest v2 condition 必须指向 generation v2。v2 不以全树 `rglob` 查找额外文件；未被链引用的 tmp/PENDING/noncurrent/orphan 不获得证据资格。

### v1 兼容

`_validate_evidence_manifest()` 版本分派：v1 使用原有完整 file index 校验，v2 使用上述层级链。v1 load/replay 不写文件、不刷新 manifest；runner 若发现 v1 suite 已闭合，直接返回 replayed result；若 v1 不完整且 resume 将产生写入，provider 前拒绝，不能调用 `_refresh_evidence_manifest()` 或升级 schema。

## C：full no-copy 与精确 working-tree 回收

adapter 仍写 `plan_root/runs/<condition>/<case>`，artifact materialization 与 canonical checkpoint 顺序不变。`checkpoint_root()` 成功返回后，runner 再验证 canonical `CURRENT` 指向的 generation 确实含当前 task；随后 resolve working tree，并证明目标严格位于 resolve 后的 `plan_root/runs/<condition>/<case>`、等于预期 exact case root、不是 `plan_root`/`runs`/condition 父目录，才用 `shutil.rmtree()` 删除。checkpoint 或验证失败时不删除。

`execution_classification=None` 的 formal full 在 condition 结束时跳过 compatibility `copytree`。smoke/regression 保持 compatibility view：adapter working tree 先按上述规则删除，再从 canonical experiment tree 复制受控兼容视图，因此历史 smoke readers 不变。

resume 在 provider/callback 前按冻结 dispatch/selection 枚举 exact case root；只有对应 canonical run 已有可验证 `CURRENT`/task checkpoint 时才删除 working dir。没有 canonical checkpoint 的 working tree保留给既有 orphan repair，不因名字相似、父目录存在或其他 case 已完成而误删。

## D：磁盘估算与执行前门禁

### versioned calibrated forecast

`PaperBudgetResult.disk_estimate` 使用 `tokenshare.paper_disk_estimate.v3`；calibration
policy 使用 `tokenshare.paper_disk_calibration.v2`，并在 budget digest 计算前冻结。
space forecast 为 root、AI unit、provider-attempt envelope、provider-attempt payload、
duplicated model-execution record 与固定 manifest/temp 之和。`max_tokens × 4` 只生成
`theoretical_max_payload_bytes` 诊断，不进入全局 startup forecast。

payload 校准来自只读外部 run：
`E:\TokenEcnomic\TokenShareData\outputs\experiments\paper_smoke_exp1_exp4_v3_20260729T060859Z_run04`。
只取 completed Exp1/Exp2 `CURRENT` canonical evidence，排除顶层/`experiments` compatibility
重复；按 artifact id 去重。共有 88 个 provider attempts，其中 87 个持久化
`raw_model_output` payload。对 87 个 `ArtifactRef.size_bytes` 采用 nearest-rank p95
（索引 `ceil(0.95*N)-1`），得到 `289246` bytes，max 为 `1232946` bytes；forecast
向上缓冲到 `327680` bytes/attempt。该目录只读，未修改历史 evidence。

正式 Factor-150 的同一次 Exp1–5 plan-only 口径为 `17,312 roots / 115,076
AI units / 234,788 provider attempts`；componentwise max-condition 为 `167 roots /
3,320 units / 3,320 attempts`。startup required 为：

```text
headroom = max(ceil(forecast * 25%), 2 GiB)
required = forecast + headroom + max_condition_compaction
```

该口径在 `439.85 GiB` free 下通过。理论 payload 诊断既不替换 forecast，也不单独
决定 startup block。

### startup、rolling 与 compaction guard

startup 在 EvidenceStore/output/provider 前执行 strict v3 shape/policy/input/component
校验；`replay_only` 跳过。resume 不把 actual canonical bytes 从 p95 forecast 做字节
相减；二者不是同一进度单位。rolling tracker 从冻结 plan 的 root/unit/attempt
counters 恢复，resume 只按 validated terminal task identities 扣除能证明的计数；
无法归属的 replacement reserve 保守保留。

每个 root callback/provider 前，required 为 remaining suite forecast、remaining
headroom、max-condition compaction，加当前 root `theoretical payload - 320 KiB
payload forecast` 的正补差。放行时以锁保护 O(1) counters，并把每个并发 in-flight
root 的 `root forecast + theoretical overage` 都纳入 reservation token；finally 精确
释放。前序实际 payload 超 forecast 会通过卷 free 的下降在后续 root 重新 fail
closed。Exp3 replacement/Exp4 requeue attempt upper 与 budget policy 使用同一映射。

磁盘资源 block 不逐 root 写 `not_started` evidence；只写一个 bounded
`tokenshare.paper_infrastructure_blocked.v1` marker，记录 selection/remaining identity
digest、remaining count 与 last committed cursor。50,000 个 remaining roots 仍只有
一次 marker write 和常量大小 body。CLI 捕获 startup
`PaperInfrastructureBlockedError`，输出结构化 JSON、exit 3、provider 0，且不创建 run
root。

condition compaction guard API 已具备 exact/one-byte 边界；其 production call site
必须由 generation v3 terminal snapshot compaction 在写入前接线，并在失败时保留
parent/CURRENT。这一项在 A v3 完成前不得宣称 full-safe。

space peak budget 与累计 I/O write amplification 是两个指标：上述门禁约束同一时刻
所需空间；generation v3 的 delta/terminal snapshot 另需证明累计写入 O(N)，不得用
space forecast 掩盖旧 generation v2 的 O(N²) 重写。

## 常量内存 final event ref

`_last_complete_event_ref()` 保持旧语义：按 WindowsPath 的 `CURRENT.json` 路径顺序，选择最后一个含匹配事件的路径，再取该文件最后一个 `TASK_COMPLETED` 或 `EXPERIMENT_FAULT_OBSERVED`。实现只保存当前最大 pointer 与该文件最后匹配 event，JSONL 逐行解析，不建立全 suite candidates 或单文件 records list；不改成按 timestamp/event id 取最大。

## 错误、恢复与验收

- manifest/link/hash drift、v1 写入尝试、unsafe cleanup target 与磁盘不足均在 provider 前 fail closed。
- checkpoint 失败保留 adapter 现场；PENDING repair 完成引用发布后才允许清理。
- `available == required` 与少 1 byte 两条边界都测试，后者同时验证 provider/callback/evidence-store 调用数为零。
- synthetic event JSONL 用 `tracemalloc` 证明峰值不随事件总数线性增长；manifest hot path spy 禁止 suite `rglob` 与其他 condition rehash。
- 运行 A/C/D focused 与三个定向测试文件、`py_compile` 和 scoped `git diff --check`；不运行 Fast、真实 API、Full 或 LeanAudit。
