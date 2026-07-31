# FEAT-011 Full 资源有界复算设计（B 批次）

## 目标

本批只收敛 formal metrics 与 formal report 的进程内峰值内存，不修改 evidence、runner、budget、CLI 或 callbacks。历史小 fixture 的 metrics JSON/CSV/JSONL 字节、排序与 digest 语义必须保持不变。

## 已批准边界

- `paper_formal_metrics.py` 不再同时持有全部 run bundle；run 索引放入临时 SQLite，`Mapping` 每次只加载一个 run，消费后释放。
- `model_execution_records.jsonl` 从 canonical artifact 逐条校验、逐行写入；兼容别名从权威文件分块复制，不构造全量 records/list/text。
- metrics 输出逐文件写临时文件并原子替换。JSON 使用 `JSONEncoder.iterencode`，CSV 使用逐行 writer，写入时增量计算 SHA-256。
- JSONL 按文件对象逐行解析；禁止 `read_text().splitlines()` 造成文件文本与行列表双份驻留。
- 文件摘要使用固定大小 chunk；禁止为摘要调用 `read_bytes()`。
- `paper_formal_report.py::_scan_formal_output` 逐目录枚举文件、逐 chunk 扫描 secret，并保留与旧全路径排序相同的 findings 顺序。
- 不把 plan commitments、worker pool、RSS backpressure、manifest v2 或 compatibility view 带入本批。

## 组件设计

### `LazyFormalRunBundleMapping`

该类型实现 `collections.abc.Mapping[str, Mapping[str, Any]]` 和 context manager。构造阶段将 `conditions.jsonl` 与 run generation 路径写入输出根同卷、evidence 根之外的临时 SQLite；索引把每个 run path 保存为逐组件 `os.path.normcase()` 后的 tuple，并通过 SQLite 自定义 collation 按 tuple 比较，再以稳定 discovery order 破同键。`__iter__` 依二者返回 condition id，从而在 Windows 上保持旧 `sorted(WindowsPath)` 的大小写折叠及组件前缀顺序（例如 `a` 目录排在同层 `a.txt` 目录之前），而不误用整串 path 或 SQLite `BINARY` 的顺序。`__getitem__` 才读取该 run 的 task/attempt/fault/event/artifact JSONL，且不缓存 value。关闭时先关闭 SQLite，再删除临时目录。

已有聚合函数继续接收 `Mapping`，因此普通 dict 单元测试和历史语义不变。`recompute_paper_formal_metrics` 在 `with` 块内完成复算与输出；condition row 计算后立即丢弃 bundle。Exp5 过滤使用只保存 key 的 lazy subset view，禁止 `{key: bundle}` 物化。

### 流式 model inventory 与输出

新增 model record chunk iterator。每个 attempt ref 在当前 run 的 artifact index 中解析，artifact 文件用 chunk hash 校验，JSON object 加载后校验 identity，随即 canonical `json.dumps(..., sort_keys=True) + "\n"` 产出。为保持 duplicate 检查且避免 Python set 随 suite 增长，artifact id 与 attempt identity 写入临时 SQLite 唯一索引；artifact id 全局唯一，同一 id 指向不同 content hash 必须显式拒绝，不能被视作两个合法 identity。

inventory 临时 SQLite 从 schema 初始化开始就由 `closing()` 覆盖，并在 `TemporaryDirectory` 退出清理前关闭连接。即使 schema 初始化本身失败，也必须保留原始异常并先释放 Windows 文件句柄，不能让临时目录删除失败覆盖根因。

`_write_metrics_outputs` 不再构造 `paths_and_content`。每个输出通过 `_write_chunks_atomic` 独立写入 `<name>.tmp`，写入时更新 digest，成功后 `replace`；失败清理该临时文件且不影响已存在正式文件。权威 model inventory 写完后，兼容别名从权威文件按 chunk 原子复制。

JSON digest 改用与旧 `json.dumps(..., separators=(",", ":"))` 等价的 `JSONEncoder.iterencode`，避免额外大字符串/bytes。小 fixture 以旧冻结字节和 digest 为兼容门。

### report 流式扫描

新增递归文件 iterator。每层只保存当前目录的 `os.scandir` entries，并只用 `os.path.normcase(entry.name)` 排序后深度遍历；目录名不得追加虚拟 `/`，否则会破坏 `a` 与 `a.txt` 的组件前缀顺序。该遍历在 Windows 上复现 `sorted(root.rglob("*"))` 的大小写折叠、逐组件文件顺序而不保存全树 Path。

每个文件按 1 MiB 读取。扫描窗口保留 `max(配置 secret 长度 - 1, token pattern 最小匹配长度 - 1)` 字节 overlap，保证跨 chunk 的 exact secret 与 `sk-/sf-` pattern 都能检出。内存上界与最大单 chunk、配置 secret 和当前目录 fan-out 有关，不随全树文件总量或文件大小线性增长。

## 错误与兼容性

- 缺失/非 object JSON、未解析 model ref、artifact hash 或 identity 不一致继续使用现有 `ValueError` 文案/类别。
- 历史 v1 evidence schema 和 metrics schema 不变；本批不迁移落盘数据。
- 输出相对路径、CSV 字段顺序、JSON indent/key order、JSONL 行序、LF 与 SHA-256 前缀均保持不变。
- 临时 SQLite/临时输出必须在成功和异常路径清理，不纳入正式 evidence/ref；SQLite schema 初始化失败时也要先关闭连接，并保留原始错误。

## 验收

- RED→GREEN：lazy Mapping 同时存活的完整 bundle 不超过一个；重复访问重新读取且不缓存。
- synthetic 大 suite 下，metrics 峰值由“所有 run 总和”降为“最大单 run + 固定开销”；测试阈值为 20 runs 峰值不超过 `5 runs 峰值 × 1.20 + 1 MiB`，且两者绝对差不超过 64 MiB。
- model inventory writer 接受 one-shot iterator；权威与兼容 JSONL 字节完全相同，小 fixture 与旧冻结字节一致。
- `_digest` 与旧 canonical `json.dumps` SHA-256 完全一致；hash/read/scan 均不调用 `Path.read_bytes()` 或 JSONL `Path.read_text()`。
- report 能检出跨 chunk secret，文件路径枚举不调用 `Path.rglob`，大文件扫描读取块不超过 1 MiB 加 overlap。
