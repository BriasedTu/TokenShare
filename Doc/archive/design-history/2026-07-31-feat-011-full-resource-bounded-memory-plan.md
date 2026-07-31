# FEAT-011 Full Resource Bounded Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 `superpowers:test-driven-development` 逐任务执行；本批由当前 agent inline 实施，不再分派子 agent。

**Goal:** 在不改变历史小 fixture 字节/digest 的前提下，让 formal metrics 按单 run 局部加载，并让 metrics/report 的大文件与全树扫描保持资源有界。

**Architecture:** 临时 SQLite 保存 run 与 inventory 唯一索引；`Mapping` value 按需从 generation JSONL 读取且无缓存。所有大输出、hash、JSON digest 和 secret scan 都改为 iterator/chunk 驱动，并以逐文件临时文件 + 原子替换发布。

**Tech Stack:** Python 3、stdlib `sqlite3`/`tempfile`/`json.JSONEncoder.iterencode`/`hashlib`/`csv`/`os.scandir`、pytest。

---

### Task 1：Lazy run bundle 与 JSONL 单份读取

**Files:**
- Modify: `src/tokenshare/experiments/paper_formal_metrics.py`
- Test: `tests/experiments/test_paper_formal_metrics.py`

- [x] 写 RED：构造多个 run，验证 Mapping 无 value cache、每次只局部 load、context 退出删除 SQLite；spy 禁止 JSONL `read_text`。
- [x] 使用外部 `--basetemp` 运行新增测试，确认因类型/行为缺失而失败。
- [x] 实现 `_iter_jsonl`、无额外文本副本的 `_read_jsonl`、`LazyFormalRunBundleMapping` 与 lazy subset。
- [x] 将 `recompute_paper_formal_metrics` 改为 Mapping 生命周期内逐 run 聚合；移除 `run_bundles` 实体 dict。
- [x] 运行新增测试与现有 metrics 定向回归，确认 GREEN。

### Task 2：streamed model inventory 与逐文件 atomic outputs

**Files:**
- Modify: `src/tokenshare/experiments/paper_formal_metrics.py`
- Test: `tests/experiments/test_paper_formal_metrics.py`

- [x] 写 RED：one-shot model iterator、canonical/alias 字节相同、输出异常保留旧文件、chunk hash 禁止 `read_bytes`、iterencode digest 与旧 digest 相同。
- [x] 运行新增测试确认预期失败。
- [x] 实现 `_iter_model_record_chunks`、临时 SQLite duplicate index、`_write_chunks_atomic`、`_iter_json_chunks`、`_iter_csv_chunks`、`_hash_file`。
- [x] 重写 `_write_metrics_outputs` 为逐文件发布；保留 `_model_record_text` 作为小 fixture 兼容 wrapper，但生产路径不得调用它。
- [x] 替换 metrics 中大文件 `read_bytes` hash；运行新增与全部 `test_paper_formal_metrics.py` 定向测试确认 GREEN。

### Task 3：formal report 流式路径与分块扫描

**Files:**
- Modify: `src/tokenshare/experiments/paper_formal_report.py`
- Test: `tests/experiments/test_paper_formal_report.py`

- [x] 写 RED：禁止 `Path.rglob/read_bytes`，验证路径排序；创建跨 1 MiB 边界 exact secret 与 API-key pattern 并要求检出。
- [x] 运行新增测试确认预期失败。
- [x] 实现 `_iter_files_streaming` 与 `_scan_file_for_secrets`，将 `_scan_formal_output` 改为逐文件逐 chunk。
- [x] 将 report ref/hash 与 JSONL 读取改为 chunk/逐行，避免旁路重新引入大副本。
- [x] 运行新增与全部 `test_paper_formal_report.py` 定向测试确认 GREEN。

### Task 4：兼容与资源验收

**Files:**
- Test: `tests/experiments/test_paper_formal_metrics.py`
- Test: `tests/experiments/test_paper_formal_report.py`

- [x] 运行两份 targeted 测试，使用仓库外 `--basetemp`。
- [x] 运行 bounded-memory synthetic 测试并记录峰值阈值证据。
- [x] 检查 `git diff --check` 与四个获准文件/两份 design-plan 文档的 diff；不修改 progress/feature/handoff。
- [x] 向根 agent 回报 RED、GREEN、兼容性与未运行 Full/API/LeanAudit；Fast 由根 agent 在并行工作完成后统一执行。
