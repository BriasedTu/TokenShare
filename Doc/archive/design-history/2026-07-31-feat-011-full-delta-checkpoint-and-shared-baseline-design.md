# FEAT-011 Full 增量 checkpoint 与共享 baseline 索引设计

## 目标与范围

本批解决 formal P0-full 在 `47,762 roots / 256,886 AI units / 650,438 attempts` 口径下仍存在的三项生产风险：

1. `FormalEvidenceStore._publish_checkpoint_generation()` 每提交一个 root 都重写该 condition 已有的全部 JSONL，形成 O(N²) 写放大；60 个 `500 roots / 2,330 AI units` 的 Exp3 condition 按历史 smoke 体量可产生约 7 TB 级临时写入。
2. `run_scheduled_cases()` 在 condition 全部 roots 完成后才处理 outcomes；最大 condition 同时保留 500 个完整 adapter outcomes，且 checkpoint 前崩溃可能重调这些 roots 的真实 provider。
3. 每个 Exp3 root 都重新扫描 Exp1 conditions 构造 shared baseline reference；36,126 个 Exp3 roots 会触发至少 `36,126 × 12 = 433,512` 次 source-condition 验证。

采用 generation v3 per-root delta chain、condition 终态单次 snapshot compaction、runner 在线 checkpoint/compact accumulator，以及 suite 内共享的 Exp1 validated reference index。新运行使用 v3；历史 generation v1/v2 与 evidence manifest v1 只读兼容，不迁移、不升级、不回写。实现不改变真实 provider/model/request controls，不新增攻击模型，也不重新调用历史 API。

本设计与 `2026-07-31-feat-011-full-resource-acd-design.md` 的 A/C/D 合并实施：A 的 evidence manifest v2 必须理解 v3 delta/snapshot；C 的 adapter cleanup 必须发生在 root delta 完整提交后；D 的磁盘 preflight 继续先行，且其 temp/COW component 应覆盖 compaction snapshot 峰值。

## 方案选择

### 采用：delta generation chain + terminal snapshot

每个 root 只写包含该 root 记录的 immutable delta generation；scheduler 只能在所有 root 已提交后再确定的 condition-scope 尾事件，写成至多一个受约束的 event-only delta，绝不回写已提交 root delta。现有 `PENDING.json → CURRENT.json → condition_manifest.json → evidence_manifest` 发布顺序继续作为 commit protocol；父 generation 不再在每个 root 提交后删除。condition 达到冻结分母且所有 roots 均为 terminal 后，用临时 SQLite 流式折叠 delta chain，按冻结 selection order 写一个自包含 snapshot generation，并以第二种 PENDING publication 原子切换 CURRENT。切换完成后 delta parents 变成不可达 orphan，才能删除。

该方案同时具备：

- 每个 root evidence 只写一次，终态 snapshot 再写一次，总写量 O(N)；
- root 提交后立即释放 outcome 和 adapter working tree，常驻对象不随 condition roots 线性增长；
- CURRENT 已提交的成功或负面 terminal root 在 resume 时都不会再次调用 provider；
- terminal metrics/report/replay 仍读取一个 self-contained snapshot，减少下游改动。

### 不采用：per-root immutable segment inventory

该方案同样可做到 O(N)，但会为 47,762 roots 产生约 30–35 万个小文件，并要求 metrics、report、replay、shared-root 与 manifest consumers 全部改成 segment inventory。Windows 元数据和目录枚举成本高于 v3 chain，临近 full 的改动面也更大。

### 不采用：共享 append journal

直接向五个 condition JSONL append，再用 commit journal 记录 offsets，可以减少文件数量，但需要处理多文件 append 的原子性、partial tail、fsync/truncate 恢复和未来并发写入。它比 immutable generation 的 crash state 更难审计，不适合作为本轮最小可靠方案。

单次 condition bulk checkpoint 也不采用：它只消除 O(N²)，仍保留整 condition 内存，并在 checkpoint 前崩溃时重调最多 500 roots。microbatch snapshot 同样只能缩小而不能消除该语义缺口。

## Generation v3 schema

### generation manifest

新 generation manifest schema 为 `tokenshare.paper_checkpoint_generation.v3`，严格 exact-key 校验：

```json
{
  "schema_version": "tokenshare.paper_checkpoint_generation.v3",
  "generation_id": "<uuid>",
  "generation_kind": "delta | snapshot",
  "delta_role": "root_outcome | condition_tail_events | null",
  "selection_ordinal": "<non-negative-int-or-null>",
  "anchor_task_id": "<task-id-or-null>",
  "parent_generation_id": "<uuid-or-null>",
  "parent_generation_manifest_digest": "sha256:<...>-or-null",
  "compacted_from_head_generation_id": "<uuid-or-null>",
  "compacted_from_head_generation_manifest_digest": "sha256:<...>-or-null",
  "compacted_chain_digest": "sha256:<...>-or-null",
  "compacted_generation_count": 0,
  "files": ["<six exact FileEvidence records>"]
}
```

六个文件固定为 `run_manifest.json`、`per_task_results.jsonl`、`per_attempt_results.jsonl`、`fault_injections.jsonl`、`events/event_log.jsonl`、`artifacts/artifact_index.jsonl`。

`delta` 的共同规则：

- 第一条 delta 的 parent 两字段均为 null；后续 delta 的 parent id/digest 必须精确指向 prior CURRENT generation manifest。
- compacted 四字段使用 null/null/null/0。
- `root_outcome` 的 `selection_ordinal` 与 `anchor_task_id` 非空且绑定冻结 selection；`per_task_results.jsonl` 精确一条 root task，attempt 与 event 非空，fault 可空，artifact index 与该 task identity 一致。run manifest 的 `task_ids`/`completed_task_ids` 只描述该 delta，不伪装成 condition 全量 snapshot。
- `condition_tail_events` 的 `selection_ordinal` 为 null，`anchor_task_id` 精确绑定冻结 selection 中最后一个terminal root；task、attempt、fault、artifact 四个 JSONL 必须为空，event JSONL可以为空或只包含scheduler已验证的condition-scope `MERGE_GATE_*` 事件，run manifest声明零新增task。它同时是condition closure marker：每个condition在terminal snapshot前必须精确提交一条；重复exact body幂等，第二条或任何drift拒绝。
- root delta 不得因 scheduler 尾事件补写而修改；per-root事件仍随root delta，scheduler末尾事件和“无尾事件但调度已闭合”的事实只通过上述closure delta提交。不存在任意event append API。

`snapshot` 的规则：

- `delta_role`、`selection_ordinal`、`anchor_task_id` 均为 null；parent 两字段均为 null，因 snapshot 在 commit 后必须独立可验证，不能依赖待删除 delta。
- `compacted_from_head_*` 固定提交 compaction 开始时的 prior CURRENT；`compacted_chain_digest` 是按 oldest→newest 的 `(generation_id, manifest_digest)` canonical sequence digest；count 必须等于被折叠 delta 数。
- 六个文件包含 condition 全量 records；不论 root future 完成或 delta commit 顺序如何，task/attempt/fault/artifact 按冻结 selection ordinal与各自稳定 identity 重建，condition tail events 固定排在最后 root 的既有 events 之后。run manifest 是 terminal condition 全量 manifest。
- snapshot 发布后禁止再追加 root。完全相同的重复提交可幂等返回；任何 task/record/artifact drift 或额外 task 都 fail closed。

generation v1/v2 validator 保留原有只读语义。新 formal checkpoint 不写 v1/v2，不把历史 v2 父 id 当作可遍历 v3 chain，也不对历史目录 compaction。

### condition manifest v2

新运行写 `tokenshare.paper_condition_evidence.v2`，保留现有 identity/denominator/paper eligibility 字段并增加：

- `head_generation_kind`：`delta` 或 `snapshot`；
- `chain_generation_count`：运行中为 root delta 数加0或1个event-only delta，terminal snapshot 为 1；
- `root_delta_count` 与 `condition_event_delta_count`：running后者为0或1，snapshot前精确为1且不增加logical task count；
- `logical_task_count`：链折叠后的唯一 task 数；
- `reachable_size_bytes`：当前逻辑链六文件与其 artifact payload 的去重可达字节，用于 resume disk deduction；
- `commit_chain_digest`：running 时按实际 immutable delta commit order 增量计算，允许 futures 乱序完成而无需重写旧链；
- `logical_records_digest`：running 时为 null；terminal snapshot 才按冻结 selection order 对 task/attempt/fault/event/artifact logical inventories计算；
- CURRENT、head generation manifest、logical run manifest refs。

运行中 manifest 可指向 delta head，必须满足 `observed_root_count == logical_task_count <= expected_root_count`、`terminal=false`；它的 task set 是冻结 selection 的唯一子集，不要求 delta commit order 是 selection 前缀。全部 root terminal但condition closure尚未提交或snapshot尚未commit时仍是running。terminal condition必须指向snapshot，且expected/observed/terminal三个分母相等、compacted chain中condition closure精确一条。任何terminal manifest指向delta head、snapshot分母不全、closure缺失或digest不一致均fail closed。

## Adapter working-tree intake 与 salvage state machine

在线 checkpoint 仍有一个早于 generation publication 的崩溃窗口：真实 adapter 已把 `run_manifest.json`、task/attempt/fault/event/artifact index及其payload完整写入 case working tree，但 runner 尚未完成确定性实验投影和root delta提交。该现场是已发生的真实provider调用，resume不得忽略它后再次调provider。

每次 suite resume 必须严格按以下顺序执行，且发生在任何 provider/callback 前：

1. 只读完整加载并验证 canonical top-level evidence manifest、suite identity、所有 condition CURRENT/parent chain与artifact closure；没有完整 `FormalEvidenceStore.load()` GREEN，不得删除任何 working tree。
2. 从一次完整 load 得到冻结的 canonical terminal task-key set；对已经commit的task，仅在working tree identity与canonical commit token精确匹配后删除，不逐task重走整条delta chain。
3. 对冻结 selection 中 canonical 尚缺失的每个case，直接检查其固定adapter case path，而不是只在整个condition没有CURRENT时才找；即使canonical已经含其他tasks，也不能漏掉该case现场。
4. 把现场分类为以下互斥状态，再决定是否dispatch provider。

状态定义：

- `NO_CANONICAL_NO_WORKTREE`：唯一允许进入真实provider dispatch的状态。
- `CANONICAL_COMMITTED_EXACT`：canonical logical inventory已有同一terminal task；验证working tree若存在则只做exact cleanup，provider calls为0。
- `ADAPTER_COMPLETE_UNCOMMITTED`：canonical缺该task，但固定case tree包含完整六文件、严格run/task/attempt/event/artifact identities、provider provenance/request identity、payload hashes与冻结condition/case相符。用持久化事实重建adapter result，重做纯确定性的 `_apply_experiment_runtime()`，然后走正常root delta PENDING/CURRENT；provider calls为0。
- `ADAPTER_PARTIAL_OR_AMBIGUOUS`：文件缺失、JSONL截断、多个task、identity/hash/version漂移或无法证明完成。保留原目录与bytes，发出稳定设施分类并fail closed；不得静默归档、覆盖、删除、写成实验失败或重新调provider。
- `CANONICAL_WORKTREE_CONFLICT`：canonical与working tree声称同一task但内容/provenance不同，或tree绑定另一个condition/case。两边均保留并fail closed。

salvage只重做由冻结输入和持久化adapter facts决定的experiment projection；不得重新生成provider output、attempt timing、usage或artifact。`archive_uncheckpointed_adapter_runs()`不能再先移动这类现场：v3 resume由上述分类器接管，v1写操作继续拒绝；v2 adapter/compatibility tree是non-canonical，不依赖旧顶层`files` exact-set/rglob推断。旧archive helper若保留，只能在salvage分类之后处理已证明不属于任何冻结case的目录。

对应崩溃缝包括：adapter最后一个文件落盘后、projection中、delta target写完但PENDING前、PENDING各阶段、CURRENT commit后cleanup前。前五类完整现场resume均须`provider_calls=0`；残缺现场必须保持原bytes不变并blocked。root checkpoint成功返回一个绑定`condition/task/head generation/digest`的短生命周期validated commit token，cleanup只验证该token与fixed case path；不能每root调用会遍历全chain的`_validate_run()`。resume则复用第一次full load得到的冻结task-key set。cleanup不刷新evidence manifest，因为adapter/compatibility tree不属于canonical v2闭包；checkpoint自身的单次targeted refresh已经完成。

## PENDING/CURRENT crash state machine

### Immutable delta publication

`PENDING.json` 升级为 `tokenshare.paper_checkpoint_pending.v2`。root outcome使用`publication_kind="root_delta"`并带唯一task id/selection ordinal；condition tail使用`publication_kind="condition_tail_events"`、task id为null并带exact anchor task/event digest。两者共用target manifest digest、expected prior CURRENT body/digest和condition identity，走同一immutable delta publication状态机。

状态顺序：

1. `CLEAN_PRIOR`：CURRENT 指向 prior delta，或尚无 CURRENT。
2. `TARGET_WRITTEN`：target delta 六文件和 generation manifest 已完整落盘，但无 PENDING；它是 orphan，不具证据资格，resume 不得猜测晋升。
3. `INTENT_WRITTEN`：PENDING 冻结 prior→target CAS。
4. `CURRENT_TARGET`：CURRENT 原子切到 target。
5. `CONDITION_TARGET`：condition manifest v2 指向 target；root publication增加一个logical task，closure publication只把`condition_event_delta_count`从0置1。
6. `INVENTORY_TARGET`：evidence manifest v2 的唯一 condition entry 指向新 condition manifest。
7. `COMMITTED`：删除 PENDING；checkpoint 返回成功。

repair只接受CURRENT为exact prior或exact target。prior时验证target delta及父绑定后继续切换；target时补condition manifest/inventory。CURRENT为第三值、parent digest漂移、root PENDING task与delta task不同、tail PENDING anchor/events drift或第二条tail delta均fail closed。无PENDING orphan永不晋升，可在证明未被CURRENT/manifest引用后清理。

### Terminal snapshot compaction

同一个 PENDING v2 使用 `publication_kind="terminal_snapshot"`，task id 为 null，并增加 `compacted_prior_head_id/digest` 与 chain digest。状态顺序：

1. `TERMINAL_DELTA_HEAD`：delta chain 已覆盖冻结 expected tasks，所有 task terminal，尚未宣告 condition terminal。
2. `SNAPSHOT_WRITTEN`：临时 SQLite 折叠完成，target snapshot 六文件、manifest 和逐字段等价校验通过；无 PENDING 时仍只是 orphan。
3. `COMPACTION_INTENT`：PENDING 固定 prior delta head、chain digest 与 target snapshot。
4. `CURRENT_SNAPSHOT`：CURRENT 原子切到 snapshot。
5. `CONDITION_TERMINAL`：condition manifest v2 指向 snapshot，分母全等且 terminal=true。
6. `INVENTORY_TERMINAL`：evidence manifest condition entry 指向 terminal manifest。
7. `COMPACTION_COMMITTED`：删除 PENDING；snapshot 成为唯一论文证据入口。
8. `PARENTS_CLEANED`：仅删除 chain digest 已提交且不再被 CURRENT 引用的旧 delta dirs。

在3–6任一位置崩溃，repair依据intent幂等补完；在7–8崩溃，resume只做exact orphan parent cleanup，不改snapshot。若prior head已变化、snapshot logical digest与chain不同、chain不完整或出现非terminal task，compaction fail closed。旧parents不能在PENDING删除前被删。compaction触发器属于EvidenceStore的checkpoint commit：每次root/closure delta提交后，若`observed terminal root count == expected`且condition closure精确一条，就原子进入snapshot流程；不能只依赖scheduler正常return。因此成功、失败/budget/exception/worker death/not_started、blocked-suite补齐和Exp5 fail-stop都先提交closure，随后由EvidenceStore统一终态化。

## Chain validation 与流式 compaction

新增专责模块 `paper_formal_checkpoint.py`，避免继续扩大 evidence store 的 schema/IO 混合职责。它提供 v3 manifest parser、oldest→newest chain iterator、logical record iterator和 SQLite snapshot compactor。

链 validator 必须：

- 从 CURRENT head 只沿 manifest parent refs 反向遍历，不 `rglob` 猜路径；
- 对每个 generation 校验目录名/id、manifest hash、六文件 exact set/hash/size/record count；
- 用 visited id/digest 集合拒绝 cycle；拒绝 missing parent、parent digest tamper、跨 experiment/condition/repeat；
- root delta task id 全链唯一；event-only delta不增加task且每condition最多一条。同 task 的 exact idempotent重放由checkpoint入口短路，不能生成第二个root delta；
- attempt/event/fault/artifact identities 不得跨 task 或重复冲突；protocol ledger/hash chain继续逐 task验证；
- chain 长度不得超过冻结 expected root count加一个condition-tail-event allowance；running logical task set必须是冻结selection的唯一子集，允许futures乱序完成；terminal/blocked closure必须是完整集合；
- `condition_tail_events` 只能位于全部root outcome deltas之后，anchor必须是冻结规则确定的最后已执行/terminal root，事件identity/body必须与scheduler accumulator的terminal事件摘要一致；
- snapshot 必须独立，parent 为 null，并验证 compacted provenance字段的 shape/digest；terminal snapshot task inventory必须与冻结 selection逐项相同。

compactor 不把全部 records 放入 Python list。它在 evidence root 同卷、evidence tree 之外创建 `TemporaryDirectory` 和 SQLite：

- tasks 表以 frozen order 唯一约束 task id；attempt/event/fault/artifact 表以现有 canonical merge identity 建 UNIQUE index；chain ingest order与输出order分离，所有root records按selection ordinal与record稳定identity输出，event-only condition events固定在最后root events之后；
- oldest→newest 逐 delta 解析，单条 canonical JSON text 与 stable order 写 SQLite；
- duplicate exact record只允许显式定义的幂等 identity，内容 drift立即拒绝；
- 六个 snapshot 文件逐 cursor/逐行写临时文件并增量 hash/record count，最后原子 replace；
- snapshot semantic audit 分别计算 logical inventories digest、usage totals、terminal status和artifact reachable closure，并与 delta chain 重算值逐字段相等；
- 成功/异常都先关闭 SQLite，再清理临时目录，保留原始异常。

性能压力验收使用 synthetic `500-root` condition；正式 Factor-150 planner 的 componentwise max-condition 是 `167 roots / 3,320 AI units / 3,320 provider attempts`。累计 generation bytes 不超过 `delta payload总量 + terminal snapshot × 固定系数 + 固定manifest开销`，禁止出现旧实现约 `N/2 × terminal snapshot` 的写放大。测试通过 writer byte counter，不真实生成大模型 payload。这里衡量累计 I/O write amplification；磁盘门禁衡量 space peak budget，两者不得混用。

## Runner 在线 checkpoint 与 compact accumulator

`run_scheduled_cases()` 不再返回完整 `outcomes` tuple供 condition 末尾二次处理，也不得先把raw outcomes塞进内部`outcome_values`、`records_by_case`、`all_records`或`ordered_events`再汇总。调度顺序改为：

1. dispatch 一个 root；
2. 对所有实验统一执行 `_apply_experiment_runtime()`，包括 Exp3 fault/worker death、Exp4 ablation与 Exp5 identity；
3. 从 outcome 提取 compact runtime facts交给 `ScheduledConditionAccumulator`；
4. 立即写 root delta checkpoint；若该root是salvage结果，沿用同一路径但provider calls为0；
5. checkpoint 完整提交后按 C 规则验证 canonical task，再删除 exact adapter case tree；
6. 释放 outcome/adapter_result；
7. Exp5 identity fail-stop 根据当前 outcome决定是否继续，未启动 roots仍逐个写 terminal `not_started` delta，不调用 provider；
8. scheduler结束时把仅此时才能冻结的`MERGE_GATE_*` condition-scope events（可为空）写成唯一condition-closure event-only delta；已经提交的last-root delta不可回写；
9. condition roots 全部 terminal 后按冻结selection order compaction并构造 `PaperConditionResult`。

accumulator只保存 condition 汇总必需信息：executed case ids、最小/最大 runtime window、每root observed parallel slots的最大值、critical path sum及availability三态、provider latency sum/availability三态、provider error count、成功unit count、最多一个condition大小的interval boundaries、dependency edge compact rows与稳定scheduler事件摘要。它不得保存task、attempt、artifact payload、adapter_result、raw provider response或大model execution record。即使future完成顺序乱序，terminal输出也按冻结selection order。最大live完整outcome测试必须始终为1；5 roots与500 roots的tracemalloc差值只允许accumulator的compact facts增长，不得接近完整bundle/大payload线性增长。

对root delta已CURRENT committed但adapter cleanup前崩溃的情况，resume从一次完整validated logical inventory加入`completed_task_keys`（成功和负面terminal均加入），provider调用数保持0，并只删除有exact canonical task的working tree。adapter完整但尚未checkpoint则走前述salvage；checkpoint/materialization/PENDING repair失败时保留adapter tree。`_checkpoint_adapter_result`、`_checkpoint_exception`和`_checkpoint_budget_exhausted`都遵循相同commit-token cleanup规则。

## Evidence manifest v2 递归语义

A 的 `tokenshare.paper_evidence_manifest.v2` 顶层仍只有 static refs 与 condition inventory。对 v3 condition：

- running delta：`condition_manifest_ref → CURRENT → head delta manifest → parent chain → 每个delta六文件 → artifact index → payload`；event-only delta的空task/attempt/fault/artifact files也必须按manifest校验，不能跳过；
- terminal snapshot：`condition_manifest_ref → CURRENT → snapshot manifest → snapshot六文件 → artifact index → payload`，compacted provenance只作为摘要，不要求已清理 parent仍存在；
- inventory entry 的 `reachable_size_bytes` 必须等于上述当前可达链的去重字节总和；
- load 逐 condition流式遍历，拒绝 duplicate path/ref、missing/extra manifest file、path越界、hash/size/record count不符、cycle、parent tamper、task duplicate和artifact closure漂移；
- checkpoint hot path只校验prior head+新delta，并从prior condition manifest增量更新count/size/commit-chain digest；running `logical_records_digest`保持null，不扫描其他conditions或全suite；terminal compaction只替换该condition entry。

evidence manifest v1继续原校验且完全只读；generation v1/v2只允许在历史 v1/v2 evidence路径中验证/replay。resume在任何preflight、finalization repair、recovery doc、archive、cleanup或refresh前识别v1：已闭合v1只做完整只读load/replay并直接返回，未闭合v1在provider和任何写前拒绝。两种路径调用前后历史目录逐文件bytes与mtime完全相等，且不调用disk-usage preflight。

## Consumers 与兼容

### usage、load、resume 与 replay

建立统一 logical run reader：v1/v2 与 v3 snapshot直接读六文件；v3 delta从oldest→newest流式yield。下列入口不得再直接假定 CURRENT generation是全量文件：

- `FormalEvidenceStore.load()` 与 completed terminal task keys；
- `_usage_from_current_checkpoints()`；
- `_audit_persisted_condition_evidence()`；
- `_current_replay_run_evidence()`、`_last_complete_event_ref()`及 terminal failure audit；
- shared Exp1 root reference builder。

`_last_complete_event_ref()` 对每个CURRENT必须把整条logical chain视为一个旧full-generation候选：oldest→newest流式扫描events，event-only head不能遮蔽parent中的最后一个`TASK_COMPLETED`；再在不同run候选间维护最大Windows path/稳定run order。外层直接迭代`glob`并维护当前max，不用`sorted(glob)`物化所有pointer paths。为保持旧full snapshot失败语义，chain任一组成generation的events文件malformed，整个CURRENT logical run候选作废，不能只跳过坏delta后从其余parents拼出看似完整的事件引用。

正式 suite/experiment finalization要求每个 terminal condition CURRENT均为 v3 snapshot；因此 metrics/report正常只读路径不需要聚合 delta。replay仍支持历史 v1/v2 terminal generation；若看到声称terminal但CURRENT为v3 delta必须拒绝。

### metrics 与 report

`LazyFormalRunBundleMapping._current_generation()` 接受历史 v1/v2或v3 snapshot，拒绝running delta。`_load_run_bundle()`读取snapshot并保持已冻结排序/CSV/JSON/digest语义。用同一 synthetic condition在旧v2全量generation与新v3 snapshot下复算，condition rows、experiment rows、model execution records、failure examples、replay summary和report eligibility必须逐字段相同；允许的差异仅限 source path/schema provenance refs，并必须由明确normalizer排除，不能放宽论文指标。

### Exp3 shared Exp1 validated index

在 `_dispatch_formal_conditions()` 首次进入 Exp3 前，以同一个 `FormalEvidenceStore` 从已terminal且已snapshot的 Exp1 experiment一次构建 `ValidatedSharedRootReferenceIndex`：

- 遍历每个 Exp1 condition一次，验证 snapshot、condition identity、source version、usage与artifact/event refs；
- 以 case id为唯一键保存完整shared reference；重复case、缺失case、source experiment未terminal或任一reference不完整均fail closed；
- index是当前进程内只读mapping，最多约635 entries，不写新论文evidence；resume每进程重建一次，绝不调用provider；
- 所有Exp3 callbacks共享该实例，lookup仍逐字段核对planned reference id、domain/difficulty/topic、worker/repeat/seed、request limits、endpoint/model identity、split profile/version和source hash；
- 0% shared-reference条件继续只引用Exp1，不新增provider usage。

这把原先约433,512次 source-condition验证降为每进程一次Exp1 inventory扫描加O(1) lookup，同时不信任未经验证的缓存。

## 错误与不可伪造边界

- CURRENT/PENDING/parent三者不构成exact prior或target状态时fail closed，不进行“选最新目录”式恢复。
- duplicate task、cycle、missing parent、parent digest tamper、snapshot/delta logical digest差异、terminal分母漂移均阻止condition/suite paper eligibility。
- root outcome必须先经过真实adapter和实验runtime投影，再进入delta；不能用accumulator或snapshot生成器合成attempt/provider evidence。
- snapshot只压缩已提交canonical delta，不调用executor/provider，不修改attempt结果，不重新计算非确定性输出。
- artifact materialization失败或delta未commit时保留adapter现场；cleanup只发生在COMMITTED后。canonical artifact payload若在失败前已content-addressed复制但未被index引用，不获得论文证据资格，后续exact retry可复用。
- historical v1/v2不迁移为v3；旧结果不能因新compaction设施获得论文资格。

## 验收标准

1. 500-root synthetic condition的累计generation写字节随N线性；500/50 roots写字节比接近10且不超过设计阈值，不得接近100。
2. 调度500 roots并给每root放置大model payload时，完整outcome/adapter_result同时存活数不超过1；`outcome_values`等raw集合不存在，内存只随compact facts和最大单root变化。
3. 从adapter完整落盘到root delta CURRENT提交、再到cleanup的每个崩溃缝resume provider call均为0；成功、失败、timeout、worker_died、not_started等terminal root不重试。残缺/冲突working tree逐文件bytes不变并显式blocked。
4. root delta publication与snapshot compaction的每个crash seam均可按PENDING幂等恢复；未知CURRENT或tamper fail closed。
5. parent缺失、digest漂移、cycle、duplicate task/attempt/event/artifact、跨condition引用、第二条/漂移event-only delta和terminal delta均有独立RED→GREEN。
6. v1/v2 load/replay结果保持；v1 closed/incomplete resume在任何preflight/repair前分派，历史目录bytes/mtime完全相等且provider/disk-usage调用为0。
7. v3 snapshot与等价v2全量generation的usage、replay、shared-root、metrics/report逐字段相等。
8. Exp3 full synthetic inventory证明Exp1 condition validator每进程调用次数等于source conditions数，而不是Exp3 roots×conditions；所有lookup provider calls为0。
9. checkpoint成功后exact adapter tree删除；checkpoint/compaction失败保留；resume先full load一次再清理。500-root spy证明cleanup不重复遍历chain、不额外refresh evidence manifest，formal不copy compatibility tree。
10. futures乱序完成、normal tail、负面terminal/blocked补齐与Exp5 early-stop场景在snapshot前均精确产生1个受约束condition-closure event-only delta；terminal snapshot按冻结selection order重建，`MERGE_GATE_*` events不丢、不重、不回写旧delta，metrics/replay与v2 reference等价。
11. 只运行定向tests、Fast最终门和scoped验证；不运行真实API、full实验、`init -Full`或LeanAudit。
