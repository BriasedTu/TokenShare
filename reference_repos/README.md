# 外部参考源码（Reference Repositories）

本目录保存 package layout 研究用的外部开源项目浅克隆 / sparse checkout。它们不是 TokenShare 运行时代码，也不参与 `init.ps1` / `init.sh` 的 `compileall` 验证。

除本说明文件外，本目录下的外部源码已通过根目录 `.gitignore` 忽略，避免把第三方源码误提交进 TokenShare。

## 已拉取仓库

| 本地目录 | 上游仓库 | commit | 选择原因 | 已拉取范围 |
|---|---|---|---|---|
| `temporalio-sdk-python` | <https://github.com/temporalio/sdk-python> | `05aef6b` | Python SDK，关注 workflow/client/worker/replay 边界。 | `temporalio/`、`tests/`、`pyproject.toml`、`README.md` |
| `spotify-luigi` | <https://github.com/spotify/luigi> | `c76e18c` | Python 工作流系统，关注 task/target/worker/scheduler 的扁平核心布局。 | `luigi/`、`test/`、`examples/`、`pyproject.toml`、`README.rst` |
| `cwltool` | <https://github.com/common-workflow-language/cwltool> | `8949fc2` | CWL Python 参考实现，关注 typed workflow、executor、job、path mapping。 | `cwltool/`、`tests/`、`pyproject.toml`、`README.rst` |
| `prefect` | <https://github.com/PrefectHQ/prefect> | `649f253` | 现代 Python 编排系统，关注 `src/` layout、events/server/workers/results/states 分层。 | `src/prefect/`、`tests/`、`pyproject.toml`、`README.md` |
| `dagster` | <https://github.com/dagster-io/dagster> | `8bb7394` | 大型数据编排系统，关注 `_core/storage/events/execution/executor` 等内部边界。 | `python_modules/dagster/dagster/`、`python_modules/dagster/pyproject.toml`、`README.md` |

## 观察重点

- `src/` layout：Prefect 使用 `src/prefect`，适合避免测试从当前目录误导入未安装包。
- 单包核心 layout：Temporal Python SDK 使用顶层 `temporalio/`，内部按 `client`、`worker`、`workflow`、`api` 分层。
- 扁平核心 layout：Luigi 将 `task.py`、`worker.py`、`scheduler.py`、`target.py` 等核心模块直接放在 `luigi/` 下，简单但大型化后边界较弱。
- 领域执行 layout：cwltool 将 `workflow.py`、`executors.py`、`job.py`、`pathmapper.py`、`process.py` 等按执行语义拆分。
- 大型内部 core layout：Dagster 使用 `dagster/_core/storage`、`dagster/_core/events`、`dagster/_core/execution`、`dagster/_core/executor` 等内部命名空间隔离复杂协议内核。

## 对 TokenShare 的初步启发

- TokenShare V1 更适合采用 `src/tokenshare/`，而不是直接在根目录放 `tokenshare/`，这样测试和实验运行更接近真实安装后的导入路径。
- 协议核心应有明确内部边界，例如 `core`、`storage`、`plugins`、`executors`、`experiments`、`replay`。
- SQLite 相关实现不应散落在 `core`，应放入 `storage` 或 `storage/sqlite`，保持 JSONL event ledger 的权威地位清晰。
- 插件和执行器需要分开目录，避免 factorization / Lean stub 逻辑进入协议核心。
- 测试目录应按模块边界镜像，例如 `tests/core`、`tests/storage`、`tests/replay`、`tests/plugins`。
