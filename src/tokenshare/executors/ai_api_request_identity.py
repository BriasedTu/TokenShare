"""AI API 出站请求的稳定身份与精确 wire bytes。"""

from __future__ import annotations

import json
import base64
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal, Mapping
from urllib.parse import urljoin, urlsplit, urlunsplit

from tokenshare.core.models import JsonObject


PREPARED_OUTBOUND_REQUEST_SCHEMA_VERSION = "tokenshare.prepared_outbound_request.v1"
CANONICAL_JSON_UTF8_PROFILE = "canonical_json_utf8_v1"
PROMPT_ADMISSION_PROFILE_ID = "tokenshare.prompt_admission.utf8_byte_upper.v1"
PROMPT_ADMISSION_PROFILE_VERSION = 1
PROMPT_ADMISSION_PROFILE_DIGEST = (
    "sha256:e693ef1c9dbf50aff36aaae1b14f7029c5954c6708a6570182e69a7051685d49"
)
PROMPT_ADMISSION_TOKEN_LIMIT = 32_768


@dataclass(frozen=True, kw_only=True)
class PreparedOutboundRequest:
    """一次构造、供 artifact 与 transport 共同消费的请求值。"""

    schema_version: Literal["tokenshare.prepared_outbound_request.v1"]
    body_obj: JsonObject
    body_bytes: bytes
    body_digest: str
    serialization_profile: Literal["canonical_json_utf8_v1"]
    normalized_absolute_endpoint: str
    provider_config_digest: str
    entry_id: str
    configured_model: str
    effective_controls_digest: str
    plugin_id: str
    plugin_version: str
    prompt_profile_id: str
    prompt_serialization_schema: str
    body_serialization_schema: str
    prompt_admission_profile_id: str
    prompt_admission_profile_version: int
    prompt_admission_profile_digest: str
    estimated_prompt_tokens: int
    case_id: str
    planned_ai_unit_id: str
    sample_slot_index: int
    replacement_slot: int
    inference_request_digest: str

    def to_dict(self) -> JsonObject:
        """返回可精确恢复 wire bytes 的 canonical JSON persistence body。"""

        prepared = validate_prepared_request(self)
        return {
            "schema_version": prepared.schema_version,
            "body_obj": json.loads(prepared.body_bytes.decode("utf-8")),
            "body_bytes_base64": base64.b64encode(prepared.body_bytes).decode("ascii"),
            "body_digest": prepared.body_digest,
            "serialization_profile": prepared.serialization_profile,
            "normalized_absolute_endpoint": prepared.normalized_absolute_endpoint,
            "provider_config_digest": prepared.provider_config_digest,
            "entry_id": prepared.entry_id,
            "configured_model": prepared.configured_model,
            "effective_controls_digest": prepared.effective_controls_digest,
            "plugin_id": prepared.plugin_id,
            "plugin_version": prepared.plugin_version,
            "prompt_profile_id": prepared.prompt_profile_id,
            "prompt_serialization_schema": prepared.prompt_serialization_schema,
            "body_serialization_schema": prepared.body_serialization_schema,
            "prompt_admission_profile_id": prepared.prompt_admission_profile_id,
            "prompt_admission_profile_version": prepared.prompt_admission_profile_version,
            "prompt_admission_profile_digest": prepared.prompt_admission_profile_digest,
            "estimated_prompt_tokens": prepared.estimated_prompt_tokens,
            "case_id": prepared.case_id,
            "planned_ai_unit_id": prepared.planned_ai_unit_id,
            "sample_slot_index": prepared.sample_slot_index,
            "replacement_slot": prepared.replacement_slot,
            "inference_request_digest": prepared.inference_request_digest,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "PreparedOutboundRequest":
        """严格恢复 canonical persistence body；不接受隐式类型转换。"""

        if not isinstance(value, Mapping):
            raise ValueError("prepared request must be a JSON object")
        expected = {
            "schema_version",
            "body_obj",
            "body_bytes_base64",
            "body_digest",
            "serialization_profile",
            "normalized_absolute_endpoint",
            "provider_config_digest",
            "entry_id",
            "configured_model",
            "effective_controls_digest",
            "plugin_id",
            "plugin_version",
            "prompt_profile_id",
            "prompt_serialization_schema",
            "body_serialization_schema",
            "prompt_admission_profile_id",
            "prompt_admission_profile_version",
            "prompt_admission_profile_digest",
            "estimated_prompt_tokens",
            "case_id",
            "planned_ai_unit_id",
            "sample_slot_index",
            "replacement_slot",
            "inference_request_digest",
        }
        if set(value) != expected:
            raise ValueError("prepared request fields do not match v1 schema")
        string_fields = expected - {
            "body_obj",
            "prompt_admission_profile_version",
            "estimated_prompt_tokens",
            "sample_slot_index",
            "replacement_slot",
        }
        if any(
            not isinstance(value[field], str) or not value[field]
            for field in string_fields
        ):
            raise ValueError("prepared request string field is invalid")
        integer_fields = (
            "prompt_admission_profile_version",
            "estimated_prompt_tokens",
            "sample_slot_index",
            "replacement_slot",
        )
        for field in integer_fields:
            item = value[field]
            if isinstance(item, bool) or not isinstance(item, int) or item < 0:
                raise ValueError(f"prepared request {field} is invalid")
        body_obj = value["body_obj"]
        if type(body_obj) is not dict:
            raise ValueError("prepared request body_obj must be a JSON object")
        encoded = value["body_bytes_base64"]
        try:
            body_bytes = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("prepared request body bytes are invalid") from exc
        if base64.b64encode(body_bytes).decode("ascii") != encoded:
            raise ValueError("prepared request body bytes are not canonical base64")
        arguments = dict(value)
        arguments.pop("body_bytes_base64")
        arguments["body_bytes"] = body_bytes
        return validate_prepared_request(cls(**arguments))

    def provenance_dict(self) -> JsonObject:
        """返回不复制请求正文 bytes 的稳定 provenance 字段。"""

        return {
            "schema_version": self.schema_version,
            "body_digest": self.body_digest,
            "serialization_profile": self.serialization_profile,
            "normalized_absolute_endpoint": self.normalized_absolute_endpoint,
            "provider_config_digest": self.provider_config_digest,
            "entry_id": self.entry_id,
            "configured_model": self.configured_model,
            "effective_controls_digest": self.effective_controls_digest,
            "plugin_id": self.plugin_id,
            "plugin_version": self.plugin_version,
            "prompt_profile_id": self.prompt_profile_id,
            "prompt_serialization_schema": self.prompt_serialization_schema,
            "body_serialization_schema": self.body_serialization_schema,
            "prompt_admission_profile_id": self.prompt_admission_profile_id,
            "prompt_admission_profile_version": self.prompt_admission_profile_version,
            "prompt_admission_profile_digest": self.prompt_admission_profile_digest,
            "estimated_prompt_tokens": self.estimated_prompt_tokens,
            "case_id": self.case_id,
            "planned_ai_unit_id": self.planned_ai_unit_id,
            "sample_slot_index": self.sample_slot_index,
            "replacement_slot": self.replacement_slot,
            "inference_request_digest": self.inference_request_digest,
        }


class PreparedOutboundRequestFactory:
    """全仓唯一允许构造 :class:`PreparedOutboundRequest` 的入口。"""

    @staticmethod
    def prepare(
        *,
        body_obj: JsonObject,
        base_url: str,
        endpoint: str,
        provider_config_digest: str,
        entry_id: str,
        configured_model: str,
        effective_controls_digest: str,
        plugin_id: str,
        plugin_version: str,
        prompt_profile_id: str,
        prompt_serialization_schema: str,
        body_serialization_schema: str,
        case_id: str,
        planned_ai_unit_id: str,
        sample_slot_index: int,
        replacement_slot: int,
    ) -> PreparedOutboundRequest:
        body_bytes = _canonical_json_bytes(body_obj)
        body_digest = _digest_bytes(body_bytes)
        normalized_endpoint = _normalized_absolute_endpoint(base_url, endpoint)
        estimated_prompt_tokens = _estimated_prompt_tokens(body_obj, body_bytes)
        if estimated_prompt_tokens > PROMPT_ADMISSION_TOKEN_LIMIT:
            raise ValueError(
                "prompt admission token upper bound exceeds 32768: "
                f"{estimated_prompt_tokens}"
            )
        identity = {
            "body_digest": body_digest,
            "normalized_absolute_endpoint": normalized_endpoint,
            "provider_config_digest": provider_config_digest,
            "entry_id": entry_id,
            "configured_model": configured_model,
            "effective_controls_digest": effective_controls_digest,
            "plugin_id": plugin_id,
            "plugin_version": plugin_version,
            "prompt_profile_id": prompt_profile_id,
            "prompt_serialization_schema": prompt_serialization_schema,
            "body_serialization_schema": body_serialization_schema,
            "prompt_admission_profile_digest": PROMPT_ADMISSION_PROFILE_DIGEST,
            "case_id": case_id,
            "planned_ai_unit_id": planned_ai_unit_id,
            "sample_slot_index": sample_slot_index,
            "replacement_slot": replacement_slot,
        }
        prepared = PreparedOutboundRequest(
            schema_version=PREPARED_OUTBOUND_REQUEST_SCHEMA_VERSION,
            body_obj=body_obj,
            body_bytes=body_bytes,
            body_digest=body_digest,
            serialization_profile=CANONICAL_JSON_UTF8_PROFILE,
            normalized_absolute_endpoint=normalized_endpoint,
            provider_config_digest=provider_config_digest,
            entry_id=entry_id,
            configured_model=configured_model,
            effective_controls_digest=effective_controls_digest,
            plugin_id=plugin_id,
            plugin_version=plugin_version,
            prompt_profile_id=prompt_profile_id,
            prompt_serialization_schema=prompt_serialization_schema,
            body_serialization_schema=body_serialization_schema,
            prompt_admission_profile_id=PROMPT_ADMISSION_PROFILE_ID,
            prompt_admission_profile_version=PROMPT_ADMISSION_PROFILE_VERSION,
            prompt_admission_profile_digest=PROMPT_ADMISSION_PROFILE_DIGEST,
            estimated_prompt_tokens=estimated_prompt_tokens,
            case_id=case_id,
            planned_ai_unit_id=planned_ai_unit_id,
            sample_slot_index=sample_slot_index,
            replacement_slot=replacement_slot,
            inference_request_digest=_digest_json(identity),
        )
        return validate_prepared_request(prepared)


def validate_prepared_request(
    prepared: PreparedOutboundRequest,
) -> PreparedOutboundRequest:
    """从对象重算 bytes/digest/identity；漂移时在任何副作用前拒绝。"""

    if type(prepared) is not PreparedOutboundRequest:
        raise TypeError("transport requires PreparedOutboundRequest")
    if prepared.schema_version != PREPARED_OUTBOUND_REQUEST_SCHEMA_VERSION:
        raise ValueError("prepared request schema version drift")
    if prepared.serialization_profile != CANONICAL_JSON_UTF8_PROFILE:
        raise ValueError("prepared request serialization profile drift")
    recomputed_bytes = _canonical_json_bytes(prepared.body_obj)
    if recomputed_bytes != prepared.body_bytes:
        raise ValueError("prepared request body bytes mismatch")
    recomputed_digest = _digest_bytes(prepared.body_bytes)
    if recomputed_digest != prepared.body_digest:
        raise ValueError("prepared request body digest mismatch")
    if (
        prepared.prompt_admission_profile_id != PROMPT_ADMISSION_PROFILE_ID
        or prepared.prompt_admission_profile_version != PROMPT_ADMISSION_PROFILE_VERSION
        or prepared.prompt_admission_profile_digest != PROMPT_ADMISSION_PROFILE_DIGEST
    ):
        raise ValueError("prepared request prompt admission profile drift")
    estimated = _estimated_prompt_tokens(prepared.body_obj, prepared.body_bytes)
    if estimated != prepared.estimated_prompt_tokens:
        raise ValueError("prepared request prompt admission estimate drift")
    if estimated > PROMPT_ADMISSION_TOKEN_LIMIT:
        raise ValueError("prepared request prompt admission exceeds 32768")
    if _normalized_absolute_endpoint(prepared.normalized_absolute_endpoint, "") != (
        prepared.normalized_absolute_endpoint
    ):
        raise ValueError("prepared request normalized absolute endpoint drift")
    if _digest_json(_identity_preimage(prepared)) != prepared.inference_request_digest:
        raise ValueError("prepared request inference identity drift")
    return prepared


def _identity_preimage(prepared: PreparedOutboundRequest) -> JsonObject:
    return {
        "body_digest": prepared.body_digest,
        "normalized_absolute_endpoint": prepared.normalized_absolute_endpoint,
        "provider_config_digest": prepared.provider_config_digest,
        "entry_id": prepared.entry_id,
        "configured_model": prepared.configured_model,
        "effective_controls_digest": prepared.effective_controls_digest,
        "plugin_id": prepared.plugin_id,
        "plugin_version": prepared.plugin_version,
        "prompt_profile_id": prepared.prompt_profile_id,
        "prompt_serialization_schema": prepared.prompt_serialization_schema,
        "body_serialization_schema": prepared.body_serialization_schema,
        "prompt_admission_profile_digest": prepared.prompt_admission_profile_digest,
        "case_id": prepared.case_id,
        "planned_ai_unit_id": prepared.planned_ai_unit_id,
        "sample_slot_index": prepared.sample_slot_index,
        "replacement_slot": prepared.replacement_slot,
    }


def _estimated_prompt_tokens(body_obj: JsonObject, body_bytes: bytes) -> int:
    messages = body_obj.get("messages", [])
    if not isinstance(messages, list):
        raise ValueError("prompt admission requires messages list")
    return len(body_bytes) + 8 * len(messages) + 16


def _canonical_json_bytes(value: JsonObject) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest_json(value: JsonObject) -> str:
    return _digest_bytes(_canonical_json_bytes(value))


def _digest_bytes(value: bytes) -> str:
    return f"sha256:{sha256(value).hexdigest()}"


def _normalized_absolute_endpoint(base_url: str, endpoint: str) -> str:
    raw = (
        base_url
        if not endpoint and urlsplit(base_url).scheme
        else endpoint
        if urlsplit(endpoint).scheme
        else urljoin(base_url.rstrip("/") + "/", endpoint.lstrip("/"))
    )
    parts = urlsplit(raw)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise ValueError("prepared request endpoint must be absolute http(s) URL")
    if parts.username is not None or parts.password is not None or parts.fragment:
        raise ValueError("prepared request endpoint is not normalized")
    host = parts.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    default_port = (parts.scheme.lower() == "https" and parts.port == 443) or (
        parts.scheme.lower() == "http" and parts.port == 80
    )
    netloc = host if parts.port is None or default_port else f"{host}:{parts.port}"
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), netloc, path, parts.query, ""))
