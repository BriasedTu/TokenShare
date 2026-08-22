# Slim V2 全量实验性能与资源风险专项审查

日期：2026-08-22 +08:00
状态：审查完成；Full 暂不具备资源安全放行条件
范围：只读检查 Slim V2 正式实现、冻结 Full inventory、plan/preflight、provider、fixed-trace 回放、runtime 与 reducer；未调用 provider，未运行 representative/full，未修改实现代码。

## 1. 结论

Slim V2 已有真实的内存约束设计：roots 串行、每个 root 的 worker 数有界、provider response 最大 16 MiB、reducer 按 experiment/slice 分段读取，inventory 与 JSONL 扫描没有把整次 Full 的 raw/system 数据常驻内存。专项测量没有发现随 7,554 个论文 roots 线性增长的常驻内存泄漏，**当前首要风险不是全局 OOM，而是 fixed-trace 数据重复写入造成的磁盘与 I/O 爆炸**。

因此，本次结论为：

- representative 的既有运行状态不因本审查改变；
- **Full 不应按当前实现启动**；
- 必须先消除 fixed-trace artifact 的原始响应重复，并让 `plan`/逐 root preflight 按真实写入模型计数；
- reducer、source closure 与 provider deadline 仍有会造成“看似卡死”或极慢的次级风险，但没有发现明确死锁。

## 2. 冻结规模与当前 plan

`plan --profile full` 的实际离线输出：

| 项目 | 数值 |
|---|---:|
| 论文 roots | 7,554 |
| execution roots（含 Exp3 references） | 7,660 |
| 在线 provider call 上限 | 10,902 |
| 当前估算 | 31,008,542,720 bytes（28.879 GiB） |
| 当前 hard upper | 459,692,625,920 bytes（428.122 GiB） |
| 逐 root 预留 | 0.5 GiB |
| Exp2–4 与 Exp3 reference 的 fixed attempts 上限 | 84,618 |

当前 hard upper 主要覆盖在线 response/artifact，但没有按真实大小覆盖 84,618 个 fixed attempts 的 response artifact。

## 3. Findings

### P1：fixed-trace 每个 attempt 都复制完整来源响应，且同一 artifact 内再次复制 completion

`FixedTraceSubmissionAdapter.execute()` 为每次下游 attempt 完整读取来源 response；随后 `_submission_from_content()` 把 `outcome.raw_response_json` 与 `outcome.content_text` 一起写入 response artifact。通常 `raw_response_json` 本身已经包含相同 completion，因此保存结果约为输入 completion 的 2 倍。

直接调用生产 `ArtifactStore.save_json()` 的测量：

| completion 大小 | response artifact | 数据放大 | 序列化额外峰值内存 |
|---:|---:|---:|---:|
| 1 MiB | 2.000 MiB | 2.000× | 4.001 MiB |
| 4 MiB | 8.000 MiB | 2.000× | 16.001 MiB |
| 8 MiB | — | — | 32.001 MiB |

按 84,618 个 fixed attempts 推算，仅这一路径的正文复制量为：

| 平均来源 response | fixed artifacts 约占用 |
|---:|---:|
| 0.25 MiB | 41.32 GiB |
| 1 MiB | 165.27 GiB |
| 4 MiB | 661.08 GiB |
| 16 MiB 上限 | 2,644.31 GiB |

这会造成磁盘耗尽、长时间写盘和 JSON 序列化抖动；并发 fixed worker 还会同时持有若干份字符串与序列化缓冲，在低内存机器上形成数百 MiB 级瞬时峰值。

### P1：Full 资源估算和逐 root 磁盘 preflight 漏算 fixed response artifact

`profiles.py` 把每个 fixed attempt 固定估为 12 KiB；CLI 的逐 root 磁盘校验也只给 protocol attempt 计 64 KiB。二者均没有覆盖上述完整来源响应及重复 completion。结果是 `plan` 可显示 28.879 GiB estimate、磁盘 preflight 也可通过，但运行仍可能在中途因磁盘不足失败。

现有“每个 root 前重新检查磁盘”机制本身有效，但输入的 required bytes 失真，所以不能形成 Full 的资源安全保证。

### P2：source closure 对同一来源 response 做大量重复全量 JSON 读取

Full preflight 会按每个下游 root 的每个 attempt 重新读取来源 trace 及 response：

| 路径 | response 读取次数 | 唯一来源键 | 放大 |
|---|---:|---:|---:|
| Exp2 | 4,800 | 400 | 12.00× |
| Exp3（含 references） | 17,196 | 234 | 73.49× |
| Exp4 | 9,669 | 293 | 33.00× |
| 合计 | 31,665 | 565 | 56.04× |

这不是内存泄漏，因为单次读取后对象可释放；但在大型 response 上会产生几十到数百 GiB 的重复预检 I/O，并且 preflight 没有细粒度进度，容易被误判为卡死。

### P2：reducer 的 10,000 次 bootstrap 有界但耗时，且缺少进度反馈

reducer 按 slice 处理，没有把 Full raw/system artifacts 全量载入内存。`stratified_case_cluster_bootstrap()` 每次只保留一个样本和 10,000 个标量 estimate，实测峰值小于 0.5 MiB，不构成 OOM 风险。

但基于完整 inventory 的生产 slice 仪表化显示，共约 4,897 次有效 bootstrap 调用（cluster 数不少于 5）；本机单次测量由约 0.06 秒到 0.94 秒不等。粗略预计 Full reducer 约需 6–10 分钟 CPU，再加约 50 秒非 bootstrap 构造/读取开销；较慢机器可能更久。该阶段缺少 slice/table 级进度输出，因此会呈现“长时间无响应”，但没有观察到死锁。

### P2：provider timeout 不是严格总墙钟 deadline

provider 使用 `urlopen(..., timeout=600)` 并把 response 限制为 16 MiB且在 `finally` 中关闭，这能约束单次读入内存并避免正常连接泄漏。不过该 timeout 是 socket I/O timeout，不保证整个请求在 600 秒总墙钟内结束；持续缓慢返回数据的 provider 仍可能让单个 root 长时间停滞。

## 4. 已确认的性能保护

- 7,554 个 roots 的完整 inventory 构建与投影：0.407 秒，`tracemalloc` 峰值 11.339 MiB；没有全量 inventory OOM 迹象。
- roots 严格串行；Thread/Process worker 只覆盖当前 root，batch 数由 capacity 限制。
- provider 单响应读取硬上限为 16 MiB，并在所有路径关闭 response。
- resume 扫描主要保留文件名/键集合，不加载全量 artifact 正文。
- reducer 按 experiment/slice 读取，并明确不读取 raw/system tables。
- bootstrap 内存有界；当前问题是重复计算时间与无进度反馈，不是数据集复制导致 OOM。

## 5. Full 放行前的最小改进

1. **阻断项**：fixed path 不再把完整 provider envelope 写入每个下游 attempt，也不在同一 artifact 重复保存 completion；只保存 checker/plugin 必需的最小输出与来源引用。
2. **阻断项**：`plan` 和逐 root preflight 使用与实际 fixed artifact 一致的字节模型；增加一个 synthetic response 的 focused test，证明估算不少于实际写入。
3. source closure 按唯一 `(case_id, source_repeat_id, planned_ai_unit_id)` 验证一次，只缓存轻量元数据；执行期缓存范围最多限制在当前 root。
4. 保持 10,000 bootstrap 与冻结 seed/统计口径不变，但复用相同 slice 的重采样权重/索引，并输出 table/slice 级进度。
5. 为 provider 增加严格总墙钟截止或等价 watchdog，并在超时后保留普通失败状态；不引入旧 authority/gate。

上述改进都可在 `src/tokenshare/experiments/slim_v2/` 内完成，当前没有证据要求修改 shared core/runtime/plugin/executor。

## 6. 本次验证证据

- 离线 Full `plan`：成功；没有 provider/network 调用。
- 完整 Full inventory 内存测量：7,554 roots，峰值 11.339 MiB。
- reducer 完整 inventory 的 slice/bootstrap 调用计数：Exp1–5 均覆盖。
- 生产 artifact serializer 的 1/4/8 MiB 合成正文测量：确认约 2× 磁盘放大及约 4× 临时序列化峰值。
- focused tests：`test_provider_oversize_closes_and_never_parses`、`test_disk_is_rechecked_before_every_root`、`test_reduce_run_golden_all_experiments_and_io_boundary`，结果 `3 passed in 1.97s`。
- 未运行 Lean 专项测试、LeanAudit、全量 Lean catalog、representative 或 full；未调用真实 provider。
