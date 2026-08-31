"""TokenShare Experiments 当前 catalog 的最小 JSONL 读取与显式 ID 选择。"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any


def load_cases(path: str | Path) -> Iterator[dict[str, Any]]:
    """逐行读取 UTF-8 JSONL，并拒绝重复或缺失的 ``case_id``。"""

    seen_case_ids: set[str] = set()
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at line {line_number}: {exc.msg}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"case at line {line_number} must be a JSON object")
            case_id = row.get("case_id")
            if not isinstance(case_id, str) or not case_id:
                raise ValueError(f"case at line {line_number} has no non-empty case_id")
            if case_id in seen_case_ids:
                raise ValueError(f"duplicate case_id at line {line_number}: {case_id}")
            seen_case_ids.add(case_id)
            yield row


def select_cases_by_ids(
    cases: Iterable[dict[str, Any]],
    ordered_ids: Sequence[str],
) -> tuple[dict[str, Any], ...]:
    """按冻结 ID 顺序返回题目，并一次报告全部缺失 ID。"""

    cases_by_id = {str(case["case_id"]): case for case in cases}
    missing_ids = tuple(case_id for case_id in ordered_ids if case_id not in cases_by_id)
    if missing_ids:
        raise ValueError(f"missing case_ids: {', '.join(missing_ids)}")
    return tuple(cases_by_id[case_id] for case_id in ordered_ids)
