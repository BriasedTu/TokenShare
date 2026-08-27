# Experiments 干净抽取冻结前基线

日期：2026-08-27
状态：`recorded_pre_tag_source_baseline`

本基线是后续抽取的机器可核验来源证据。机器可读事实位于同目录的 `2026-08-27-experiments-clean-extraction-baseline.json`；本文件只解释这些事实如何用于抽取对等检查。它记录的是 baseline 提交之前的来源提交，不包含 baseline 自身、annotated tag 对象或 tag peeled commit 的自引用身份。

## 1. 来源边界

- `pre_baseline_source_sha`：`019972b6ef45b198202f9ea650d7a2fd144bbe5e`
- focused verification 实际运行于 `tested_source_sha`：`0f8e7ff49c4a52d869f4ab3fa79a9fa90bc3629f`
- 风险驱动 diff-proof：`git diff --quiet 0f8e7ff49c4a52d869f4ab3fa79a9fa90bc3629f 019972b6ef45b198202f9ea650d7a2fd144bbe5e -- src tests benchmarks fixtures configs verification` 退出码为 0。
- 两提交间非顶层 `paper/**` 的变化仅为本次清洗 plan/manifest 的批准重命名与修订。运行时代码、测试、题库、fixture、配置和 verification blob 均未变化，因此没有为了提交文档重复运行约 14 分钟的 focused 套件。
- 顶层 `paper/**` 完全不在本基线范围内：没有读取、验证、复制、编辑或暂存，也不作为来源工作树 clean gate。

## 2. Authoritative raw-byte gate

Raw freeze commit `342971a7247ffb88dc599dd436e34e3ed10403cd` 是 `pre_baseline_source_sha` 的祖先。Manifest 第 3.4 节 20 个 authoritative 文件逐项通过：

- working raw SHA-256：20/20；
- index blob raw SHA-256：20/20；
- `HEAD:path` blob raw SHA-256：20/20；
- working 与 cached attributes：20/20 均为 `text: unset`、`eol: unspecified`。

JSON 保存了每个路径的 expected/working/index/HEAD SHA-256 和两组 attribute 结果。目标抽取必须逐元素比较，不能通过文本管道、换行转换、解析后重写或抽样代替 raw-byte gate。

## 3. 正式题库与 selection identity

正式题库完整保留：

- Factorization catalog：500 个有序 case ID，摘要 `b05d785aa1f4ec2202ffa4f6f43f36fc11d6aaf2f61004591f3c2f1392c0d513`；
- Lean direct catalog：30 个有序 case ID；
- Lean lemma-graph catalog：165 个有序 case ID，摘要 `9be5537cddf2a280e74ce85a202c8d8667b36a023e2b1796ca4c0d26fe907adc`。

JSON 完整保存上述 ordered case IDs，以及 full/representative profile 的逐实验 case IDs、condition objects、challenge objects、provider-bound objects、profile/plan 摘要。Full 的关键门为：

- Exp1–5 unique cases：`435 / 50 / 53 / 65 / 37`；
- conditions：`12 / 48 / 342 / 33 / 12`；
- roots：`435 / 600 / 3726 / 2145 / 111`，合计 `7017`；
- Exp3 references：`106`；executions：`7123`；
- downstream trace-consuming union：99 个排序 case ID，canonical bytes `2,461`，SHA-256 `7123655c6c439afe653efd87094924dc7ab5e99c47e43c2cff11d587ab9fbefc`；
- Exp4 challenge plans：`195`，SHA-256 `396bde3849cc3c0f37e67a9b880a34e114a8f1215b5e438b62c62c14b34bc057`；
- provider-call upper bound：总计 `6762`，Exp1–5 为 `5910 / 0 / 0 / 0 / 852`。

`full_root_identities` 完整保存 7,017 个五字段对象，canonical items 为 1,448,798 bytes，SHA-256 为 `ec5ca012faffc148be5eff65d43394b2d7861c666468e59841c716c88dea7c07`。`full_reference_identities` 完整保存 106 个对象，22,055 bytes，SHA-256 为 `b537bd614ac9c86f2f864afb0e6a7f3c1fe22e38d772784c1fc8c0c914f43cdb`。

两组 identity 都固定五字段 `root_run_id, experiment_id, condition_id, case_id, repeat_id`，按 `experiment_id, condition_id, case_id, repeat_id, root_run_id` 的 Python/Unicode code-point 升序；哈希只覆盖无 BOM、无尾换行的 canonical `items` 数组。目标验证器必须同时检查 schema、字段集、顺序、count、canonical byte length、SHA-256，并逐位置比较完整数组。

## 4. 正式全量结果

正式运行 ID 为 `slim-v2-full-flash-20260823-233000-b4c8e951`。当前可复现 raw gates 为：

- 非 metrics：`1,088,134` files，`7,143,234,452` bytes；
- critical set：`21,260` files，`855,884,698` bytes；
- critical manifest SHA-256：`96dc9ea143d7532c0e54482907bb12017247df4286a6656fbdc45f84705fea47`；
- logical roots/references/calls/responses/traces：`7017 / 106 / 5889 / 2565 / 1964`；
- provider calls Exp1–5：`2124 / 0 / 0 / 0 / 808`；
- formal metric occurrences：`153`；unique metric IDs：`129`。

JSON 逐文件保存 `run.json`、`metrics/summary.json` 和 Exp1–5 CSV/JSONL 共 12 个发布文件的 SHA-256、bytes 与 rows。表行数依次为 Exp1 `12`、Exp2 `88`、Exp3 `684`、Exp4 `648`、Exp5 `3`。`run.json` SHA-256 为 `3fee9e896eeac6be2a4fa082dcdddef4db92b21742b6234333bc11a30e2e9478`；`metrics/summary.json` 为 `8a7f0d77d146e97d633f14cc4284501b2f4ecb479b70157e2559ece913b76932`。

约 7.14 GB raw archive 仅保存在本地 ignored `TokenShareData/`，状态为 `pending_advisor_archive_decision`；没有虚构外部 URL，也没有把 raw body、provider response body 或 credential 写入 Git。

## 5. Source focused verification

下列验证实际运行于 `tested_source_sha`，且 network/provider tripwire 均为 0：

| Gate | 结果 | pytest elapsed | total elapsed |
|---|---:|---:|---:|
| `tests/experiments/slim_v2` 全套 | 200 passed | 838.38s | 841.582s |
| retained system exact set | 475 passed | 221.74s | 226.128s |
| reducer golden exact node | 1 passed | 2.02s | n/a |
| 单个真实 Lean checker | 1 passed | n/a | 6.615s |
| compileall | exit 0 | n/a | 2.584s |
| full plan | exit 0 | n/a | 2.951s |

Retained system gate 包含 manifest 指定的 core/storage/local runtime/Factorization/plugin/executor 选择器、Factorization multi-worker vertical、submission/recovery、Lean pure contract 子集和六个纯 DeepSeek transport 节点。真实 Lean 只运行一个 direct-proof node，并由 hidden process 与 60 秒 process-tree cap 约束；没有运行 Full、Representative、LeanAudit 或真实 provider。

Full plan 命令为 `python -m tokenshare.experiments.slim_v2.cli plan --profile full --run-id extraction-baseline-plan`。它产生 roots/references/executions `7017 / 106 / 7123` 和 provider upper `6762 = 5910 / 0 / 0 / 0 / 852`；完整 plan object 与摘要在 JSON 中。

## 6. 仅历史证据

`9aa66fec8352982279de3e87fc1eae81` 与 `d59f593eeb7604374f05efecad4c142d` 仅作为 `historical_pre_post_audit_evidence`：算法未持久化且可能依赖 Git 不保持的 mtime，不能重算或用作 target gate。

旧 root/reference hashes `b6ae545102c57f73b1b6c7641b630944d024ba0e757b637a3253b26c6c68bdb7` 与 `9e67170af1c15400c50fc5c8d79ce29695838a4aef1d9d6b909410a10323c351` 的 tuple/schema/serialization 未持久化，仅记录为 `historical_unreproducible_hashes`，不参与抽取验收。

后续对等检查以 JSON 内可复现的 raw SHA、完整 arrays、canonical byte lengths、canonical SHA、12 个 result blobs 和 focused verification 合同为准。
