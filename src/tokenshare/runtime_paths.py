"""TokenShare 仓库外运行数据路径。"""

from __future__ import annotations

import os
from pathlib import Path


def repository_root() -> Path:
    """返回 TokenShare 仓库根目录。"""

    return Path(__file__).resolve().parents[2]


def default_data_root() -> Path:
    """返回默认的仓库同级运行数据根目录。"""

    return repository_root().parent / "TokenShareData"


def data_root() -> Path:
    """返回经校验的仓库外运行数据根目录。"""

    override = os.environ.get("TOKENSHARE_DATA_ROOT")
    if override is None:
        return default_data_root()

    candidate = Path(override)
    if not candidate.is_absolute():
        raise ValueError("TOKENSHARE_DATA_ROOT override must be an absolute path")

    candidate = candidate.resolve()
    repo_root = repository_root()
    if candidate == repo_root or repo_root in candidate.parents:
        raise ValueError("TOKENSHARE_DATA_ROOT must be outside the repository")
    return candidate


def experiment_outputs_root() -> Path:
    """返回实验输出根目录。"""

    return data_root() / "outputs" / "experiments"


def resolve_experiment_output_root(
    explicit_output_root: str | Path | None,
    *default_parts: str,
) -> Path:
    """解析 CLI 输出根；显式参数不读取默认数据根。"""

    if explicit_output_root is not None:
        return Path(explicit_output_root)
    return experiment_outputs_root().joinpath(*default_parts)


def resolve_persisted_data_path(
    persisted_path: str | Path,
    *,
    relative_to: str | Path | None = None,
) -> Path:
    """只读解析历史绝对、仓库相对和 suite 相对数据路径。"""

    candidate = Path(persisted_path)
    if not candidate.is_absolute():
        if candidate.parts and candidate.parts[0].casefold() == "outputs":
            return data_root().joinpath(*candidate.parts)
        if relative_to is not None:
            return Path(relative_to).joinpath(candidate)
        return candidate
    if candidate.exists():
        return candidate

    repo_root = repository_root()
    try:
        relative = candidate.resolve(strict=False).relative_to(repo_root)
    except ValueError:
        return candidate
    if not relative.parts or relative.parts[0].casefold() != "outputs":
        return candidate
    return data_root().joinpath(relative)


def diagnostic_outputs_root() -> Path:
    """返回诊断输出根目录。"""

    return data_root() / "outputs" / "diagnostics"


def supervision_root() -> Path:
    """返回本地监督数据根目录。"""

    return data_root() / "local" / "supervision"
