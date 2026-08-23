# Slim V2 全量实验性能与资源专项审查

初版日期：2026-08-22 +08:00
资源再评估：2026-08-23 +08:00
状态：**Full 已通过资源放行；尚未启动 Full。**

本记录只裁决 Full 的磁盘、内存和可恢复性资源边界，不替代严格 v2 representative 的端到端科学/功能验收。2026-08-22 的“暂不具备资源安全放行条件”基于尚无真实样本时的 `1 MiB` synthetic response 假设；该结论已由本次真实 representative 数据再评估取代。

## 1. 当前放行结论

允许启动 Full。当前 E 盘可用 `823.925 GiB`；按严格 v2 和既有真实 representative 的响应/ordinary artifact 实测分布建立的保守经验包络为 `102.326 GiB`，剩余空间为其 `8.05×`。

资源放行依赖下列已验证事实：

- 81 条真实 provider response 覆盖 Exp1 与 Exp5 的五个配置 endpoint/model；全样本 P95=`200,721 bytes`，严格 v2 子样本 P95=`312,983 bytes`，所有已观察 raw artifact 的最大值=`462,520 bytes`。
- `plan --profile full --representative-raw-response-p95-bytes 312983` 得到 response bound=`469,475 bytes`、estimate=`9.761 GiB`。全样本 P95 的同一重算为 `6.998 GiB`；默认 `1 MiB` 的旧 estimate=`19.261 GiB` 只保留为预数据保守默认值。
- Full 不再以“每个 root 最多 8 个 planned AI units”为资源前提：缩容后的 Exp5 Factorization root 为 8 个、Lean root 为 15 个 planned units。逐 root `512 MiB` margin、每 root 空间复检、安全停止与 resume 继续覆盖这些已知 inventory root。
- roots 串行、当前 root worker 有界、单 response 16 MiB 上限、每 root 空间复检和 ordinary-file resume 均保持启用；磁盘不足时安全停止并可 resume，而非损坏已提交结果。

本结论不把“所有 91,664 次 attempts 都恰好达到 16 MiB 协议上限”的数学极端情形当作必须一次性容纳的运行前储备；若按该非经验极端为所有 fixed artifacts 预留空间，任何实际本地 Full 都会被不现实的 TiB 级容量要求阻断。这不是 Slim V2 冻结的 p95 + per-root safe-stop/resume 资源策略。

## 2. 真实数据、计划器与经验包络

| 指标 | 实测或重算值 |
|---|---:|
| 真实 response 样本 | 81 |
| 严格 v2 response 样本 | 20 |
| 全样本 / 严格 v2 P95 | 200,721 / 312,983 bytes |
| 最大 raw artifact | 462,520 bytes |
| Full 默认 plan（1 MiB） | 19.261 GiB |
| Full strict-v2-P95 plan | 9.761 GiB |
| Full 全样本-P95 plan | 6.998 GiB |
| 经验资源包络 | 102.326 GiB |
| 当前 E 盘可用空间 | 823.925 GiB |

经验包络不是计划器的旧 `12 KiB/fixed attempt` 公式。它按2026-08-23缩容后的冻结 Full 数量 `84,618` fixed attempts、`7,046` online calls、`7,160` executions 计算：

- 每个 fixed attempt：`1.5 × 462,520 bytes` raw guard，加实测最大非 raw system overhead `135,975 bytes`；
- 每个 online call：上述 raw guard 的三份物化（response、terminal、system raw），另加 `256 KiB` 的系统/ordinary 开销；
- 每个 execution 另计 `160 KiB` ordinary root allowance，最后施加 `25%` 余量。

因此，现有 `profiles.py` 的 fixed-attempt `12 KiB` 仍不是精确的物理写入模型；但它不再构成 Full 的资源阻断。启动记录必须同时保存 strict-v2-P95 plan 和本节经验包络，不能只引用默认 `1 MiB` plan。

## 3. 对原先 165.27 GiB 表述的修正

“平均 response 1 MiB 时 fixed artifacts 约 165.27 GiB”是正确的合成压力算术，但不是对实际 Full 的预测：该计算假定 response 主体就是会再次完整复制的 `content_text`。

真实响应主要包含 reasoning 内容；`content_text` 在已测样本中很短。因此 fixed artifact 通常接近原始 response 大小，而不是稳定的两倍。严格 v2 已实际写出的 Exp1 raw artifacts 最大为 `462,520 bytes`，已开始的 Exp2 fixed artifacts 为约 `143,762 bytes`；早期完整 representative 的 Exp2/Exp3 fixed artifacts 也都低于 `138,862 bytes`。

原 `165.27 GiB`、`661.08 GiB` 和 `2,644.31 GiB` 表只保留为合成容量压力示例，不能再作为 Full “不得放行”的依据。

## 4. 未阻断的性能残余

- provider 墙钟时间存在长尾：历史真实 response 的最大 provider latency 为 `1,717.7 s`。这影响完成时间，但用户已明确不把时间作为本次 Full 资源放行条件。
- source closure 对 source response 有重复读取，reducer 有固定 10,000-replicate bootstrap；二者会造成 I/O/CPU 等待或界面上“无进度”的感受，但当前没有常驻全量 raw/system 数据的 OOM 迹象。
- 全量 inventory 构建/投影实测峰值为 `11.339 MiB`；reducer 单 bootstrap 峰值低于 `0.5 MiB`。

这些项目可在后续独立优化，但不要求在 Full 启动前修改 `shared` 或 Slim-local 实现。

## 5. 启动时的资源记录

启动 Full 前执行并记录，不额外引入 budget/gate：

```powershell
conda run --no-capture-output -n tokenshare python -m tokenshare.experiments.slim_v2.cli plan --profile full --run-id <new-full-run-id> --representative-raw-response-p95-bytes 312983
```

确认输出 estimate 约为 `9.761 GiB`，并在启动时再次检查目标磁盘可用空间。当前值 `823.925 GiB` 已满足本次经验包络；运行期间继续依赖已有的每 root 空间复检、安全停止和 resume。

## 6. 审查证据与历史范围

- 当前缩容后的 Full inventory：7,054 paper roots、7,160 executions、7,046 provider calls、84,618 fixed attempts；固定attempt部分不因只缩容Exp5的真实在线调用而变化。2026-08-23 前初版离线 inventory 的`7,554/7,660/10,902`仅保留为历史资源审查输入，不是当前计划。
- 真实 representative 普通文件：早期 run 覆盖 72 paper roots + 2 references、物理占用 `46.780 MiB`；严格 v2 run 已覆盖 4 个 Exp1 roots 并进入 Exp2 fixed path、物理占用 `13.029 MiB`。早期 run 的部分 root 为当时 Lean infrastructure-invalid，故不把其总大小直接线性外推；本记录使用每 attempt 的实际 raw/overhead 高水位建立包络。
- 初版 focused tests：`test_provider_oversize_closes_and_never_parses`、`test_disk_is_rechecked_before_every_root`、`test_reduce_run_golden_all_experiments_and_io_boundary`，`3 passed in 1.97s`。
- 本次再评估只读取已提交 real-run files 与 Full inventory/plan；未调用 provider，未启动 Full，未修改实现代码。
