# TokenShare 快速启动验证设计

日期：2026-07-18  
状态：已批准

## 背景

当前 `init.ps1` / `init.sh` 每次启动都会执行完整 `pytest tests`。随着 Lean catalog、paper budget 和实验 runner 测试增加，完整基线已经达到分钟级，阻碍频繁的小步开发。

## 决策

启动验证分成两个明确档位：

- 默认快速档：`\.\init.ps1` 或 `./init.sh`。
- 完整档：`\.\init.ps1 -Full` 或 `./init.sh --full`。

两个档位都必须执行：

1. Python `json` / `sqlite3` 导入检查；
2. harness 必需文件和 active feature 检查；
3. 排除 `reference_repos` 的全仓 `compileall`。

默认快速档随后执行共享清单 `verification/fast-tests.txt`。清单只包含秒级、无网络、无真实 API、无完整 Lean catalog 构建的 smoke/regression tests。PowerShell 与 Bash 必须读取同一个清单，避免两个入口逐渐分叉。

完整档随后执行 `pytest tests`，其含义与修改前一致。feature 完成、提交/合并前以及实验发布前仍需完整档通过；默认快速档只用于启动和开发循环，不能替代完成证据。

## 快速档覆盖面

快速档覆盖：

- 协议核心与存储；
- executor 契约与无真实调用的测试；
- factorization 插件；
- package layout；
- Lean descriptor/schema 与本地环境轻量检查；
- paper experiment contracts 和 paper models。

快速档有意排除完整 Lean catalog/readiness、paper budget、真实 AI benchmark 和其他长时间实验测试。这些测试仍由完整档及改动相关的定向测试负责。

## 非目标

- 不缓存 Lean checker、API 或实验结果；缓存容易让验证误用陈旧 artifact。
- 不改变 pytest marker、测试语义或生产代码。
- 不减少完整档的测试范围。

## 失败策略

- 快速清单缺失、为空、含不存在路径或重复路径时，验证必须失败。
- 未知命令行参数必须失败并显示用法。
- 任一公共检查或 pytest 失败时，脚本必须返回非零退出码。

