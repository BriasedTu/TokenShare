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


FACTOR_SEARCH_PROMPT_PROFILE = "factorization.bounded_range_prompt.v2"

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
            "requires_json_mode": True,
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
    range_start = int(range_input.range_start)
    range_end = int(range_input.range_end)
    divisor_count = range_end - range_start + 1
    response_skeleton = {
        "schema_version": RANGE_RESULT_SCHEMA_VERSION,
        "range_result_id": f"range_result:{range_input.coverage_id}:{range_input.child_index}",
        "result_kind": f"<{RANGE_RESULT_FOUND_FACTOR} or {RANGE_RESULT_NO_FACTOR}>",
        "target_n": range_input.target_n,
        "range_start": range_input.range_start,
        "range_end": range_input.range_end,
        "coverage_id": range_input.coverage_id,
        "child_index": range_input.child_index,
        "partition_params_digest": range_input.partition_params_digest,
        "found_factor": "<decimal string divisor or null>",
        "cofactor": "<decimal string cofactor or null>",
        "checked_divisor_count": "<unquoted JSON integer>",
        "executor_summary": {"checked_range": f"{range_input.range_start}-{range_input.range_end}"},
        "created_at": "<ISO-8601 timestamp>",
    }
    return "\n".join(
        [
            "Execute one TokenShare bounded factor search.",
            "IMMUTABLE TASK AND RESPONSE SKELETON:",
            json.dumps(response_skeleton, ensure_ascii=False, sort_keys=True),
            (
                "The skeleton is canonical. N=target_n, L=range_start, U=range_end. "
                "Never replace, round, reconstruct, or change these decimal strings."
            ),
            "SEARCH SILENTLY; never enumerate candidates in the response.",
            (
                "1. For odd N, bounded Fermat: "
                "a_start = ceil((U^2 + N) / (2U)) and "
                "a_end = floor((L^2 + N) / (2L))."
            ),
            (
                "Use it when a_start <= a_end and 3*(a_end-a_start+1) < U-L+1. "
                "Test every a in that interval. If a^2-N=b^2, test d=a-b, q=a+b, "
                "accepting only L<=d<=U. A complete scan covers all odd factor pairs "
                "with smaller factor in [L,U]."
            ),
            (
                "2. Otherwise use sound wheel-sieved trial division. A multiple of p "
                "may be skipped only when N % p != 0. If N % p == 0 and p is outside "
                "[L,U], do not sieve its multiples. Test every remaining candidate."
            ),
            (
                f"{RANGE_RESULT_NO_FACTOR} is allowed only after exhaustive coverage of "
                "[L,U], never after a partial search, timeout, or quick guess."
            ),
            "FINAL GATE:",
            (
                "Reload N, L, and U from the immutable skeleton, not scratch work. For d: "
                "require L<=d<=U; set q=N//d; require N%d==0 and d*q==N; compare the "
                "decimal product digit-for-digit with the original target_n. On failure, "
                "discard d and continue."
            ),
            (
                f"{RANGE_RESULT_FOUND_FACTOR}: found_factor=str(d), cofactor=str(q). "
                f"{RANGE_RESULT_NO_FACTOR}: both are null."
            ),
            (
                "checked_divisor_count is an unquoted protocol coverage integer, not an "
                f"operation count: use {divisor_count} for {RANGE_RESULT_NO_FACTOR}, "
                f"or d-L+1 for {RANGE_RESULT_FOUND_FACTOR}."
            ),
            (
                "Replace created_at with a non-empty ISO-8601 timestamp. Keep "
                "executor_summary as an object. Copy every other bound value exactly."
            ),
            f"Return only one JSON object matching {RANGE_RESULT_SCHEMA_VERSION}.",
            "Do not search outside the assigned range.",
            "Do not create child tasks or modify the task graph.",
            "Do not change the schema or return prose, markdown, or reasoning.",
        ]
    )
