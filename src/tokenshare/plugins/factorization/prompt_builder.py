"""Factorization 插件拥有的 prompt package 构造器。"""

from __future__ import annotations

import json

from tokenshare.executors.contracts import PromptPackage
from tokenshare.plugins.factorization.models import FactorSearchInstruction, FactorSearchRangeInput
from tokenshare.plugins.factorization.schemas import (
    RANGE_RESULT_FOUND_FACTOR,
    RANGE_RESULT_KINDS,
    RANGE_RESULT_NO_FACTOR,
    RANGE_RESULT_SCHEMA_VERSION,
    RANGE_RESULT_VALIDATOR_POLICY_ID,
)


FACTOR_SEARCH_PROMPT_PROFILE = "factorization.bounded_range_prompt.v1"

_RANGE_RESULT_REQUIRED_FIELDS = [
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


def build_factor_search_prompt_package(
    *,
    request_id: str,
    task_id: str,
    unit_id: str,
    range_input: FactorSearchRangeInput,
    instruction: FactorSearchInstruction,
    created_at: str,
    seed: int | None = None,
) -> PromptPackage:
    """把插件结构化 instruction 转成 AI/mock-AI 可消费的 PromptPackage。"""

    _check_instruction_alignment(
        request_id=request_id,
        unit_id=unit_id,
        range_input=range_input,
        instruction=instruction,
    )
    return PromptPackage(
        prompt_package_id=f"factor_search_prompt:{request_id}",
        request_id=request_id,
        task_id=task_id,
        unit_id=unit_id,
        prompt_text=_prompt_text(range_input),
        input_summary={
            "instruction_id": instruction.instruction_id,
            "target_n": range_input.target_n,
            "range_start": range_input.range_start,
            "range_end": range_input.range_end,
            "coverage_id": range_input.coverage_id,
            "child_index": range_input.child_index,
            "child_count": range_input.child_count,
            "partition_params_digest": range_input.partition_params_digest,
        },
        output_schema={
            "schema_version": RANGE_RESULT_SCHEMA_VERSION,
            "media_type": "application/json",
            "required_fields": list(_RANGE_RESULT_REQUIRED_FIELDS),
            "allowed_result_kinds": list(RANGE_RESULT_KINDS),
            "conditional_fields": {
                RANGE_RESULT_FOUND_FACTOR: {
                    "found_factor": "required_decimal_string",
                    "cofactor": "required_decimal_string",
                },
                RANGE_RESULT_NO_FACTOR: {
                    "found_factor": "null",
                    "cofactor": "null",
                },
            },
        },
        constraints={
            "prompt_owner": "factorization_plugin",
            "verification_authority": RANGE_RESULT_VALIDATOR_POLICY_ID,
            "strict_json_only": True,
            "bounded_range_only": True,
            "executor_must_not": [
                "search_outside_assigned_range",
                "create_or_modify_task_graph",
                "claim_final_prime_factorization",
                "invent_output_schema",
                "return_free_form_factor_claim",
            ],
        },
        seed=seed,
        fixture_profile=FACTOR_SEARCH_PROMPT_PROFILE,
        created_at=created_at,
    )


def _check_instruction_alignment(
    *,
    request_id: str,
    unit_id: str,
    range_input: FactorSearchRangeInput,
    instruction: FactorSearchInstruction,
) -> None:
    checks = {
        "request_id": (instruction.request_id, request_id),
        "unit_id": (instruction.unit_id, unit_id),
        "target_n": (instruction.target_n, range_input.target_n),
        "range_start": (instruction.range_start, range_input.range_start),
        "range_end": (instruction.range_end, range_input.range_end),
        "output_schema_version": (instruction.output_schema_version, RANGE_RESULT_SCHEMA_VERSION),
    }
    for field_name, (actual, expected) in checks.items():
        if actual != expected:
            raise ValueError(f"instruction {field_name} does not match prompt input")
    if instruction.allowed_result_kinds != RANGE_RESULT_KINDS:
        raise ValueError("instruction allowed_result_kinds do not match factorization range result")
    if instruction.determinism_requirement != "range_recheckable":
        raise ValueError("instruction determinism_requirement must be range_recheckable")


def _prompt_text(range_input: FactorSearchRangeInput) -> str:
    required_fields = ", ".join(_RANGE_RESULT_REQUIRED_FIELDS)
    range_start = int(range_input.range_start)
    range_end = int(range_input.range_end)
    divisor_count = range_end - range_start + 1
    bound_fields = {
        "schema_version": RANGE_RESULT_SCHEMA_VERSION,
        "target_n": range_input.target_n,
        "range_start": range_input.range_start,
        "range_end": range_input.range_end,
        "coverage_id": range_input.coverage_id,
        "child_index": range_input.child_index,
        "partition_params_digest": range_input.partition_params_digest,
    }
    found_factor_template = {
        **bound_fields,
        "range_result_id": f"range_result:{range_input.coverage_id}:{range_input.child_index}",
        "result_kind": RANGE_RESULT_FOUND_FACTOR,
        "found_factor": "<decimal string divisor in the assigned range>",
        "cofactor": "<decimal string target_n divided by found_factor>",
        "checked_divisor_count": 1,
        "executor_summary": {"checked_range": f"{range_input.range_start}-{range_input.range_end}"},
        "created_at": "<ISO-8601 timestamp>",
    }
    no_factor_template = {
        **bound_fields,
        "range_result_id": f"range_result:{range_input.coverage_id}:{range_input.child_index}",
        "result_kind": RANGE_RESULT_NO_FACTOR,
        "found_factor": None,
        "cofactor": None,
        "checked_divisor_count": divisor_count,
        "executor_summary": {"checked_range": f"{range_input.range_start}-{range_input.range_end}"},
        "created_at": "<ISO-8601 timestamp>",
    }
    return "\n".join(
        [
            "You are executing a TokenShare factorization bounded range task.",
            f"Target integer: {range_input.target_n}",
            (
                "Search divisor range: "
                f"{range_input.range_start} to {range_input.range_end} inclusive"
            ),
            f"Candidate divisors to test: {_candidate_divisors_text(range_start, range_end)}",
            f"Return only one JSON object matching {RANGE_RESULT_SCHEMA_VERSION}.",
            (
                "Allowed result_kind values: "
                f"{RANGE_RESULT_FOUND_FACTOR}, {RANGE_RESULT_NO_FACTOR}."
            ),
            "If a divisor d in the assigned range divides the target, return found_factor with cofactor target_n / d.",
            "If no divisor in the assigned range divides the target, return no_factor_in_range with found_factor and cofactor set to null.",
            "Do the divisibility checks silently before choosing result_kind.",
            "Do not copy the no_factor_in_range template unless every candidate divisor has non-zero remainder.",
            "Do not search outside the assigned range.",
            "Do not create child tasks or modify the task graph.",
            "Do not invent a different output schema.",
            "Do not return prose, markdown, or reasoning outside the JSON object.",
            "Use these exact protocol-bound JSON field values:",
            json.dumps(bound_fields, ensure_ascii=False, indent=2, sort_keys=True),
            "For found_factor, return exactly this JSON shape with computed factor fields:",
            json.dumps(found_factor_template, ensure_ascii=False, indent=2, sort_keys=True),
            "For no_factor_in_range, return exactly this JSON shape:",
            json.dumps(no_factor_template, ensure_ascii=False, indent=2, sort_keys=True),
            "All integer-valued protocol fields shown as strings must remain strings.",
            (
                "checked_divisor_count must be an unquoted JSON integer. "
                f"For no_factor_in_range in this range it must be {divisor_count}; "
                "for found_factor it must be found_factor - range_start + 1."
            ),
            "executor_summary must be a JSON object, not a string.",
            f"Required JSON fields: {required_fields}.",
        ]
    )


def _candidate_divisors_text(range_start: int, range_end: int) -> str:
    values = list(range(range_start, range_end + 1))
    if len(values) <= 50:
        return ", ".join(str(value) for value in values)
    prefix = ", ".join(str(value) for value in values[:25])
    suffix = ", ".join(str(value) for value in values[-5:])
    return f"{prefix}, ... , {suffix} ({len(values)} total integers)"
