"""保持请求身份与响应身份分离的 AI API artifact schema helper。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from tokenshare.core.models import JsonObject


RAW_MODEL_OUTPUT_SCHEMA_V1 = "phase7.raw_model_output.v1"
RAW_MODEL_OUTPUT_SCHEMA_V2 = "phase7.raw_model_output.v2"
RESPONSE_MODEL_STATUSES = frozenset(
    {"present", "missing", "null", "empty", "invalid_type"}
)


@dataclass(frozen=True, kw_only=True)
class RawModelIdentityEvidence:
    """从一个持久化 RawModelOutput 解码出的类型化身份事实。"""

    schema_version: str
    configured_model: str | None
    requested_model: str | None
    resolved_model: str | None
    response_model_status: str


def classify_response_model(raw_response_json: Mapping[str, Any]) -> tuple[str | None, str]:
    """只分类 provider response 的 ``model`` 字段，不做 fallback。"""

    if "model" not in raw_response_json:
        return None, "missing"
    value = raw_response_json["model"]
    if value is None:
        return None, "null"
    if not isinstance(value, str):
        return None, "invalid_type"
    if not value.strip():
        return None, "empty"
    return value, "present"


def build_raw_model_identity_fields(
    *,
    configured_model: str,
    requested_model: str,
    raw_response_json: Mapping[str, Any],
) -> JsonObject:
    """构造 v2 RawModelOutput body 中只与身份有关的字段。"""

    configured = _required_model("configured_model", configured_model)
    requested = _required_model("requested_model", requested_model)
    resolved, status = classify_response_model(raw_response_json)
    return {
        "configured_model": configured,
        "requested_model": requested,
        "resolved_model": resolved,
        "response_model_status": status,
    }


def read_raw_model_identity_evidence(
    raw_output: Mapping[str, Any],
) -> RawModelIdentityEvidence:
    """读取 v1/v2 raw identity，不把本地事实升级成响应事实。"""

    schema_version = raw_output.get("schema_version")
    raw_response = raw_output.get("raw_response_json")
    response_body: Mapping[str, Any] = (
        raw_response if isinstance(raw_response, Mapping) else {}
    )
    resolved_from_response, status_from_response = classify_response_model(response_body)

    if schema_version == RAW_MODEL_OUTPUT_SCHEMA_V1:
        return RawModelIdentityEvidence(
            schema_version=RAW_MODEL_OUTPUT_SCHEMA_V1,
            configured_model=None,
            requested_model=None,
            resolved_model=resolved_from_response,
            response_model_status=status_from_response,
        )
    if schema_version != RAW_MODEL_OUTPUT_SCHEMA_V2:
        raise ValueError(f"unsupported RawModelOutput schema: {schema_version}")
    if not isinstance(raw_response, Mapping):
        raise ValueError("RawModelOutput v2 raw_response_json must be an object")

    configured_model = _required_model(
        "configured_model",
        raw_output.get("configured_model"),
    )
    requested_model = _required_model(
        "requested_model",
        raw_output.get("requested_model"),
    )
    persisted_resolved = _optional_model(raw_output.get("resolved_model"))
    persisted_status = raw_output.get("response_model_status")
    if persisted_status not in RESPONSE_MODEL_STATUSES:
        raise ValueError("invalid RawModelOutput response_model_status")
    if (
        persisted_resolved != resolved_from_response
        or persisted_status != status_from_response
    ):
        raise ValueError("RawModelOutput response model evidence is inconsistent")
    return RawModelIdentityEvidence(
        schema_version=RAW_MODEL_OUTPUT_SCHEMA_V2,
        configured_model=configured_model,
        requested_model=requested_model,
        resolved_model=persisted_resolved,
        response_model_status=str(persisted_status),
    )


def _required_model(field_name: str, value: object) -> str:
    model = _optional_model(value)
    if model is None:
        raise ValueError(f"{field_name} must be a non-empty string")
    return model


def _optional_model(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value
