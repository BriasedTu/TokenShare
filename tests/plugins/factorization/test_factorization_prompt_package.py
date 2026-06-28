from tokenshare.executors.contracts import PromptPackage
from tokenshare.plugins.factorization import build_factor_search_prompt_package
from tokenshare.plugins.factorization.models import FactorSearchRangeInput
from tokenshare.plugins.factorization.schemas import (
    RANGE_RESULT_FOUND_FACTOR,
    RANGE_RESULT_NO_FACTOR,
    RANGE_RESULT_SCHEMA_VERSION,
    RANGE_RESULT_VALIDATOR_POLICY_ID,
)
from tokenshare.plugins.factorization.validator import build_factor_search_instruction


CREATED_AT = "2026-06-28T00:00:00Z"


def test_factorization_builds_plugin_owned_prompt_package_for_bounded_range() -> None:
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

    prompt = build_factor_search_prompt_package(
        request_id="request_1",
        task_id="task_factorization",
        unit_id="unit_2",
        range_input=range_input,
        instruction=instruction,
        created_at=CREATED_AT,
    )

    assert isinstance(prompt, PromptPackage)
    body = prompt.to_dict()
    assert body["schema_version"] == "phase3.prompt_package.v1"
    assert body["prompt_package_id"] == "factor_search_prompt:request_1"
    assert body["request_id"] == "request_1"
    assert body["task_id"] == "task_factorization"
    assert body["unit_id"] == "unit_2"
    assert body["fixture_profile"] == "factorization.bounded_range_prompt.v1"
    assert body["seed"] is None
    assert body["created_at"] == CREATED_AT

    assert body["input_summary"] == {
        "instruction_id": "factor_search_instruction:request_1",
        "target_n": "221",
        "range_start": "5",
        "range_end": "10",
        "coverage_id": "coverage_1",
        "child_index": 1,
        "child_count": 4,
        "partition_params_digest": "sha256:params",
    }
    assert body["output_schema"]["schema_version"] == RANGE_RESULT_SCHEMA_VERSION
    assert body["output_schema"]["allowed_result_kinds"] == [
        RANGE_RESULT_FOUND_FACTOR,
        RANGE_RESULT_NO_FACTOR,
    ]
    assert body["output_schema"]["required_fields"] == [
        "schema_version",
        "range_result_id",
        "result_kind",
        "target_n",
        "range_start",
        "range_end",
        "coverage_id",
        "child_index",
        "partition_params_digest",
        "found_factor",
        "cofactor",
        "checked_divisor_count",
        "executor_summary",
        "created_at",
    ]
    assert body["constraints"]["prompt_owner"] == "factorization_plugin"
    assert body["constraints"]["verification_authority"] == RANGE_RESULT_VALIDATOR_POLICY_ID
    assert body["constraints"]["strict_json_only"] is True
    assert body["constraints"]["executor_must_not"] == [
        "search_outside_assigned_range",
        "create_or_modify_task_graph",
        "claim_final_prime_factorization",
        "invent_output_schema",
        "return_free_form_factor_claim",
    ]

    prompt_text = body["prompt_text"]
    assert "Target integer: 221" in prompt_text
    assert "Search divisor range: 5 to 10 inclusive" in prompt_text
    assert "Return only one JSON object" in prompt_text
    assert "Do not search outside the assigned range" in prompt_text
    assert "Do not create child tasks" in prompt_text
    assert "factorization.range_result.v1" in prompt_text
    assert "found_factor" in prompt_text
    assert "no_factor_in_range" in prompt_text
    assert "full prime factorization" not in prompt_text.lower()
