from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_load_cases_streams_utf8_jsonl_and_rejects_duplicate_case_id(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.case_source import load_cases

    streaming_path = tmp_path / "streaming.jsonl"
    streaming_path.write_text(
        '{"case_id":"case_1","label":"中文题目"}\nnot-json-yet\n',
        encoding="utf-8",
    )

    cases = load_cases(streaming_path)
    assert iter(cases) is cases
    assert next(cases) == {"case_id": "case_1", "label": "中文题目"}
    with pytest.raises(ValueError, match=r"line 2"):
        next(cases)

    duplicate_path = tmp_path / "duplicate.jsonl"
    duplicate_path.write_text(
        "\n".join(
            json.dumps({"case_id": "duplicate", "value": value})
            for value in (1, 2)
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"duplicate case_id.*duplicate"):
        list(load_cases(duplicate_path))


def test_select_cases_by_ids_preserves_frozen_order_and_reports_missing_ids() -> None:
    from tokenshare.experiments.case_source import select_cases_by_ids

    source = (
        {"case_id": "case_b", "value": 2},
        {"case_id": "case_a", "value": 1},
        {"case_id": "case_c", "value": 3},
    )

    selected = select_cases_by_ids(iter(source), ("case_c", "case_a"))
    assert [row["case_id"] for row in selected] == ["case_c", "case_a"]

    with pytest.raises(
        ValueError,
        match=r"missing case_ids: case_missing_a, case_missing_b",
    ):
        select_cases_by_ids(
            iter(source),
            ("case_missing_a", "case_b", "case_missing_b"),
        )
