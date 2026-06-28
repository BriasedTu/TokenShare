import json

import pytest

from tokenshare.plugins.factorization.models import FactorSearchRangeInput, RangeResult
from tokenshare.plugins.factorization.validator import (
    build_factor_search_instruction,
    parse_range_result,
)


CREATED_AT = "2026-06-27T00:00:00Z"


def test_parse_range_result_accepts_structured_found_factor() -> None:
    payload = {
        "schema_version": "factorization.range_result.v1",
        "range_result_id": "range_result:unit_2:attempt_1:coverage_1:0",
        "result_kind": "found_factor",
        "target_n": "21",
        "range_start": "2",
        "range_end": "4",
        "coverage_id": "coverage_1",
        "child_index": 0,
        "partition_params_digest": "sha256:params",
        "found_factor": "3",
        "cofactor": "7",
        "checked_divisor_count": 4,
        "executor_summary": {"checked": "bounded range"},
        "created_at": CREATED_AT,
    }

    parsed = parse_range_result(json.dumps(payload))

    assert isinstance(parsed, RangeResult)
    assert parsed.to_dict() == payload


def test_parse_range_result_rejects_freeform_factor_claim() -> None:
    with pytest.raises(ValueError, match="structured JSON object"):
        parse_range_result("I found factor 3, so 21 = 3 * 7")


def test_build_factor_search_instruction_contains_bounded_range_only() -> None:
    range_input = FactorSearchRangeInput(
        target_n="221",
        range_start="5",
        range_end="10",
        coverage_id="coverage_1",
        child_index=1,
        child_count=4,
        partition_params_digest="sha256:params",
    )

    instruction = build_factor_search_instruction(
        request_id="request_1",
        unit_id="unit_2",
        range_input=range_input,
    )
    body = instruction.to_dict()

    assert body == {
        "schema_version": "factorization.factor_search_instruction.v1",
        "instruction_id": "factor_search_instruction:request_1",
        "request_id": "request_1",
        "unit_id": "unit_2",
        "target_n": "221",
        "range_start": "5",
        "range_end": "10",
        "output_schema_version": "factorization.range_result.v1",
        "allowed_result_kinds": ["found_factor", "no_factor_in_range"],
        "determinism_requirement": "range_recheckable",
    }
    serialized = json.dumps(body, sort_keys=True)
    assert "global" not in serialized.lower()
    assert "split" not in serialized.lower()
    assert "unbounded" not in serialized.lower()
    assert "coverage_id" not in body
    assert "partition_params_digest" not in body
