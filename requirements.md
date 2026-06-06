# TokenShare Requirements

日期：2026-06-06

TokenShare V1 当前使用本地 `conda` 环境运行。环境名固定为 `tokenshare`，启动脚本会默认执行：

```powershell
conda run -n tokenshare python ...
```

如需临时覆盖环境名，可以设置 `TOKENSHARE_CONDA_ENV`。

## Conda 环境

创建环境：

```powershell
conda create -n tokenshare python=3.12 pytest -y
```

验证环境：

```powershell
conda run -n tokenshare python -c "import json, sqlite3; print('python-json-sqlite-ok')"
conda run -n tokenshare python -m pytest --version
```

当前本机已创建并验证的环境：

| 项 | 值 |
|---|---|
| conda env | `tokenshare` |
| Python | 3.12.13 |
| SQLite | 3.51.2 |
| pytest | 9.0.3 |

## Phase 1 运行依赖

Phase 1 代码只依赖 Python 标准库：

- `dataclasses`
- `enum`
- `hashlib`
- `json`
- `pathlib`
- `sqlite3`
- `typing`
- `urllib.parse`

测试依赖：

- `pytest`

## 启动验证

Windows PowerShell：

```powershell
.\init.ps1
```

Bash / Git Bash / WSL：

```bash
./init.sh
```

两个脚本都会使用 `conda run -n tokenshare python`，并运行：

```bash
python -c "import json, sqlite3; print('python-json-sqlite-ok')"
python -m compileall -q -x "reference_repos" .
PYTHONPATH=src python -m pytest tests
```

`reference_repos/` 是外部参考源码，不属于 TokenShare runtime，不参与 `compileall` 或 pytest discovery。
