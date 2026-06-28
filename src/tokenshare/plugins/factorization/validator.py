"""Factorization 插件 parser 和 deterministic verifier。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from tokenshare.core.models import JsonObject
from tokenshare.core.models import ArtifactRef
from tokenshare.plugins.factorization.models import (
    FactorSearchInstruction,
    FactorIntegerSubject,
    FactorSearchRangeInput,
    RangeResult,
    RootInput,
)
from tokenshare.plugins.factorization.schemas import (
    FACTOR_SEARCH_INSTRUCTION_SCHEMA_VERSION,
    PARSE_FAILURE_REPORT_SCHEMA_VERSION,
    RANGE_RESULT_PARSER_ID,
    RANGE_RESULT_FOUND_FACTOR,
    RANGE_RESULT_KINDS,
    RANGE_RESULT_NO_FACTOR,
    RANGE_RESULT_SCHEMA_VERSION,
    REQUESTED_OUTPUT_PRIME_FACTORIZATION,
)


DEFAULT_NO_FACTOR_RECHECK_MAX_DIVISORS = 100_000


@dataclass(frozen=True, kw_only=True)
class RangeVerificationResult:
    accepted: bool
    status: str
    layer_summary: JsonObject
    failure_summary: JsonObject | None

    def to_phase4_layer_summary(self) -> JsonObject:
        return dict(self.layer_summary)


@dataclass(frozen=True, kw_only=True)
class FactorizationAIParseResult:
    """插件 parser policy 对 AI raw output 的纯解释结果。"""

    succeeded: bool
    result_kind: str
    parser_id: str
    required_output_name: str | None
    parsed_artifact_schema_id: str | None
    parsed_artifact_schema_version: str | None
    parsed_artifact_body: JsonObject | None
    candidate_output_artifact_bodies: dict[str, JsonObject]
    parse_failure_artifact_body: JsonObject | None


def parse_range_result(payload: JsonObject | str) -> RangeResult:
    """只接受结构化 JSON object / dict，不从自然语言中抽取候选因子。"""

    body = _structured_json_object(payload)
    try:
        return RangeResult(**body)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid structured range_result: {exc}") from exc


def parse_factorization_ai_output(
    raw_output: JsonObject | str | None,
    *,
    raw_output_ref_summary: JsonObject,
    created_at: str,
    raw_only: bool = False,
) -> FactorizationAIParseResult:
    """执行 Factorization 插件拥有的 AI output parse policy。

    helper 只解释 raw output 并返回待持久化 artifact body；真实保存、submission
    组装和 provider transport 仍属于 executor / artifact store。
    """

    if raw_only or raw_output is None:
        return _parse_failure_result(
            failure_kind="raw_only_not_allowed",
            message="Factorization requires parsed range_result output; raw-only is not allowed",
            raw_output=raw_output,
            raw_output_ref_summary=raw_output_ref_summary,
            created_at=created_at,
        )
    try:
        parsed = parse_range_result(raw_output)
    except (TypeError, ValueError) as exc:
        return _parse_failure_result(
            failure_kind=_parse_failure_kind(exc),
            message=str(exc),
            raw_output=raw_output,
            raw_output_ref_summary=raw_output_ref_summary,
            created_at=created_at,
        )

    artifact_body = parsed.to_dict()
    return FactorizationAIParseResult(
        succeeded=True,
        result_kind="parsed",
        parser_id=RANGE_RESULT_PARSER_ID,
        required_output_name="range_result",
        parsed_artifact_schema_id="factorization.range_result",
        parsed_artifact_schema_version="v1",
        parsed_artifact_body=artifact_body,
        candidate_output_artifact_bodies={"range_result": artifact_body},
        parse_failure_artifact_body=None,
    )


def build_factor_search_instruction(
    *,
    request_id: str,
    unit_id: str,
    range_input: FactorSearchRangeInput,
) -> FactorSearchInstruction:
    """构造只包含 bounded range 和输出 schema 的 executor instruction。"""

    return FactorSearchInstruction(
        instruction_id=f"factor_search_instruction:{request_id}",
        request_id=request_id,
        unit_id=unit_id,
        target_n=range_input.target_n,
        range_start=range_input.range_start,
        range_end=range_input.range_end,
        output_schema_version=RANGE_RESULT_SCHEMA_VERSION,
        allowed_result_kinds=list(RANGE_RESULT_KINDS),
        determinism_requirement="range_recheckable",
        schema_version=FACTOR_SEARCH_INSTRUCTION_SCHEMA_VERSION,
    )


def verify_range_result(
    result: RangeResult | JsonObject,
    *,
    child_input: FactorSearchRangeInput,
    no_factor_recheck_max_divisors: int = DEFAULT_NO_FACTOR_RECHECK_MAX_DIVISORS,
) -> RangeVerificationResult:
    """对 range_result 做可重放的 factorization domain check。"""

    if isinstance(result, RangeResult):
        body = result.to_dict()
    elif isinstance(result, dict):
        body = dict(result)
    else:
        return _rejected("invalid_output", "range_result must be a structured object")

    envelope_error = _check_range_result_envelope(body)
    if envelope_error is not None:
        return envelope_error

    mismatch = _check_child_input_alignment(body, child_input)
    if mismatch is not None:
        return mismatch

    result_kind = body.get("result_kind")
    if result_kind == RANGE_RESULT_FOUND_FACTOR:
        return _verify_found_factor(body)
    if result_kind == RANGE_RESULT_NO_FACTOR:
        return _verify_no_factor(
            body,
            no_factor_recheck_max_divisors=no_factor_recheck_max_divisors,
        )
    return _rejected("invalid_result_kind", "result_kind must be found_factor or no_factor_in_range")


def verify_factor_integer_subject(
    subject: FactorIntegerSubject | JsonObject,
    *,
    root_input_ref: ArtifactRef,
    root_input_body: JsonObject,
) -> RangeVerificationResult:
    """Verify a factor_integer subject against its root input artifact."""

    try:
        typed_subject = (
            subject
            if isinstance(subject, FactorIntegerSubject)
            else FactorIntegerSubject(**dict(subject))
        )
        root_input = RootInput(**dict(root_input_body))
    except (TypeError, ValueError) as exc:
        return _rejected("invalid_factor_integer_subject", str(exc))

    if typed_subject.source_kind != "root_input":
        return _rejected("invalid_source_kind", "factor_integer subject must come from root_input")
    if typed_subject.requested_output != REQUESTED_OUTPUT_PRIME_FACTORIZATION:
        return _rejected(
            "invalid_requested_output",
            "factor_integer subject requested_output must be prime_factorization_result",
        )
    if typed_subject.source_ref != root_input_ref.to_dict():
        return _rejected("source_ref_mismatch", "factor_integer subject source_ref mismatch")
    if root_input.target_n != typed_subject.target_n:
        return _rejected("target_mismatch", "factor_integer subject target_n mismatch")
    if root_input.requested_output != typed_subject.requested_output:
        return _rejected(
            "requested_output_mismatch",
            "factor_integer subject requested_output does not match root input",
        )
    return _accepted(
        "factor_integer_subject_checked",
        "factor_integer subject matches root input artifact",
        details={
            "target_n": typed_subject.target_n,
            "root_input_artifact_id": root_input_ref.artifact_id,
            "root_input_digest": root_input.input_digest,
        },
    )


def _structured_json_object(payload: JsonObject | str) -> JsonObject:
    if isinstance(payload, str):
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError("range_result parser requires a structured JSON object") from exc
        if not isinstance(decoded, dict):
            raise ValueError("range_result parser requires a structured JSON object")
        return decoded
    if isinstance(payload, dict):
        return dict(payload)
    raise TypeError("range_result parser requires a structured JSON object")


def _parse_failure_result(
    *,
    failure_kind: str,
    message: str,
    raw_output: JsonObject | str | None,
    raw_output_ref_summary: JsonObject,
    created_at: str,
) -> FactorizationAIParseResult:
    return FactorizationAIParseResult(
        succeeded=False,
        result_kind="parse_failed",
        parser_id=RANGE_RESULT_PARSER_ID,
        required_output_name=None,
        parsed_artifact_schema_id=None,
        parsed_artifact_schema_version=None,
        parsed_artifact_body=None,
        candidate_output_artifact_bodies={},
        parse_failure_artifact_body={
            "schema_version": PARSE_FAILURE_REPORT_SCHEMA_VERSION,
            "parser_id": RANGE_RESULT_PARSER_ID,
            "failure_kind": failure_kind,
            "message": message,
            "raw_excerpt": _raw_excerpt(raw_output),
            "raw_output_ref_summary": dict(raw_output_ref_summary),
            "candidate_outputs": {},
            "created_at": created_at,
        },
    )


def _parse_failure_kind(exc: Exception) -> str:
    message = str(exc)
    if "structured JSON object" in message:
        return "invalid_json_object"
    return "invalid_structured_range_result"


def _raw_excerpt(raw_output: JsonObject | str | None, *, max_chars: int = 200) -> str | None:
    if raw_output is None:
        return None
    if isinstance(raw_output, str):
        text = raw_output
    else:
        text = json.dumps(raw_output, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}..."


def _check_range_result_envelope(body: JsonObject) -> RangeVerificationResult | None:
    if body.get("schema_version") != RANGE_RESULT_SCHEMA_VERSION:
        return _rejected(
            "invalid_output",
            "range_result schema_version must be factorization.range_result.v1",
            details={"field": "schema_version"},
        )
    required_string_fields = (
        "range_result_id",
        "result_kind",
        "target_n",
        "range_start",
        "range_end",
        "coverage_id",
        "partition_params_digest",
        "created_at",
    )
    for field_name in required_string_fields:
        if not isinstance(body.get(field_name), str) or not body.get(field_name):
            return _rejected(
                "invalid_output",
                f"{field_name} must be a non-empty string",
                details={"field": field_name},
            )
    if not str(body["partition_params_digest"]).startswith("sha256:"):
        return _rejected(
            "invalid_output",
            "partition_params_digest must be a sha256 digest",
            details={"field": "partition_params_digest"},
        )
    result_kind = body.get("result_kind")
    if result_kind not in RANGE_RESULT_KINDS:
        return _rejected(
            "invalid_result_kind",
            "result_kind must be found_factor or no_factor_in_range",
            details={"field": "result_kind"},
        )
    child_index = body.get("child_index")
    if isinstance(child_index, bool) or not isinstance(child_index, int) or child_index < 0:
        return _rejected(
            "invalid_output",
            "child_index must be a non-negative integer",
            details={"field": "child_index"},
        )
    checked_count = body.get("checked_divisor_count")
    if isinstance(checked_count, bool) or not isinstance(checked_count, int) or checked_count < 0:
        return _rejected(
            "invalid_output",
            "checked_divisor_count must be a non-negative integer",
            details={"field": "checked_divisor_count"},
        )
    if not isinstance(body.get("executor_summary"), dict):
        return _rejected(
            "invalid_output",
            "executor_summary must be an object",
            details={"field": "executor_summary"},
        )
    has_factor = body.get("found_factor") is not None
    has_cofactor = body.get("cofactor") is not None
    if result_kind == RANGE_RESULT_FOUND_FACTOR and not (has_factor and has_cofactor):
        return _rejected(
            "invalid_output",
            "found_factor result requires found_factor and cofactor",
        )
    if result_kind == RANGE_RESULT_NO_FACTOR and (has_factor or has_cofactor):
        return _rejected(
            "invalid_output",
            "no_factor_in_range must not include found_factor or cofactor",
        )
    return None


def _check_child_input_alignment(
    body: JsonObject,
    child_input: FactorSearchRangeInput,
) -> RangeVerificationResult | None:
    expected = child_input.to_dict()
    checks = (
        ("target_n", "target_mismatch", "target_n does not match child input"),
        ("range_start", "range_mismatch", "range_start does not match child input"),
        ("range_end", "range_mismatch", "range_end does not match child input"),
        ("coverage_id", "coverage_mismatch", "coverage_id does not match child input"),
        (
            "partition_params_digest",
            "partition_params_mismatch",
            "partition_params_digest does not match child input",
        ),
    )
    for field_name, reason_code, summary in checks:
        if body.get(field_name) != expected[field_name]:
            return _rejected(reason_code, summary, details={"field": field_name})
    if body.get("child_index") != expected["child_index"]:
        return _rejected(
            "child_index_mismatch",
            "child_index does not match child input",
            details={"field": "child_index"},
        )
    return None


def _verify_found_factor(body: JsonObject) -> RangeVerificationResult:
    target_n = _parse_positive_int(body.get("target_n"))
    range_start = _parse_positive_int(body.get("range_start"))
    range_end = _parse_positive_int(body.get("range_end"))
    found_factor = _parse_positive_int(body.get("found_factor"))
    cofactor = _parse_positive_int(body.get("cofactor"))

    if target_n is None:
        return _rejected("invalid_target", "target_n must be a decimal string")
    if range_start is None or range_end is None:
        return _rejected("invalid_range", "range bounds must be decimal strings")
    if found_factor is None:
        return _rejected("invalid_output", "found_factor must be a decimal string")
    if cofactor is None:
        return _rejected("invalid_output", "cofactor must be a decimal string")
    if found_factor <= 1 or found_factor >= target_n:
        return _rejected(
            "invalid_factor",
            "found_factor must be greater than 1 and smaller than target_n",
        )
    if found_factor < range_start or found_factor > range_end:
        return _rejected(
            "factor_outside_range",
            "found_factor is outside the bounded child range",
            details={"factor": str(found_factor)},
        )
    if target_n % found_factor != 0:
        return _rejected("non_divisor", "found_factor does not divide target_n")
    if target_n // found_factor != cofactor:
        return _rejected("cofactor_mismatch", "cofactor must equal target_n / found_factor")
    return _accepted(
        "found_factor_rechecked",
        "found_factor is in range and divides target_n",
        details={"factor": str(found_factor), "cofactor": str(cofactor)},
    )


def _verify_no_factor(
    body: JsonObject,
    *,
    no_factor_recheck_max_divisors: int,
) -> RangeVerificationResult:
    if (
        isinstance(no_factor_recheck_max_divisors, bool)
        or not isinstance(no_factor_recheck_max_divisors, int)
        or no_factor_recheck_max_divisors < 1
    ):
        return _rejected(
            "invalid_verifier_budget",
            "no_factor_recheck_max_divisors must be a positive integer",
        )
    if body.get("found_factor") is not None or body.get("cofactor") is not None:
        return _rejected(
            "invalid_output",
            "no_factor_in_range must not include found_factor or cofactor",
        )
    target_n = _parse_positive_int(body.get("target_n"))
    range_start = _parse_positive_int(body.get("range_start"))
    range_end = _parse_positive_int(body.get("range_end"))
    if target_n is None:
        return _rejected("invalid_target", "target_n must be a decimal string")
    if range_start is None or range_end is None or range_start > range_end:
        return _rejected("invalid_range", "range bounds must be valid decimal strings")
    divisor_count = range_end - range_start + 1
    if divisor_count > no_factor_recheck_max_divisors:
        return _rejected(
            "range_recheck_budget_exceeded",
            "no_factor_in_range claim exceeds deterministic recheck budget",
            details={
                "requested_divisor_count": divisor_count,
                "max_divisor_count": no_factor_recheck_max_divisors,
            },
        )
    for divisor in range(range_start, range_end + 1):
        if target_n % divisor == 0:
            return _rejected(
                "divisor_exists_in_range",
                "no_factor_in_range claim is false",
                details={"divisor": str(divisor)},
            )
    return _accepted(
        "no_factor_range_rechecked",
        "range was brute-force checked and contains no divisor",
        details={"checked_divisor_count": divisor_count},
    )


def _accepted(reason_code: str, summary: str, *, details: JsonObject | None = None) -> RangeVerificationResult:
    layer = _layer("passed", reason_code, summary, details=details)
    return RangeVerificationResult(
        accepted=True,
        status="passed",
        layer_summary=layer,
        failure_summary=None,
    )


def _rejected(reason_code: str, summary: str, *, details: JsonObject | None = None) -> RangeVerificationResult:
    layer = _layer("rejected", reason_code, summary, details=details)
    return RangeVerificationResult(
        accepted=False,
        status="rejected",
        layer_summary=layer,
        failure_summary={
            "failure_kind": "invalid_output",
            "failed_layer": "plugin_domain_check",
            "message": summary,
            "evidence_refs": [],
        },
    )


def _layer(
    status: str,
    reason_code: str,
    summary: str,
    *,
    details: JsonObject | None = None,
) -> JsonObject:
    return {
        "status": status,
        "reason_code": reason_code,
        "summary": summary,
        "details": details or {},
        "evidence_refs": [],
        "checked_at": None,
    }


def _parse_positive_int(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    if not value or not value.isdecimal():
        return None
    if len(value) > 1 and value.startswith("0"):
        return None
    parsed = int(value)
    if parsed < 1:
        return None
    return parsed
