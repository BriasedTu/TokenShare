"""Phase 7 AI API executor local config models."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from hashlib import sha256

from tokenshare.core.models import JsonObject


CONFIG_SCHEMA_VERSION = "phase7.ai_api_executor_config.v1"


@dataclass(frozen=True)
class AIAPIProviderEntry:
    entry_id: str
    enabled: bool
    base_url: str
    api_key_env: str
    model: str
    endpoint: str
    supports_json_mode: bool
    supports_streaming: bool
    request_overrides: JsonObject
    pricing: JsonObject
    tags: list[str]

    def resolve_api_key(self) -> str:
        value = os.environ.get(self.api_key_env, "")
        if not value:
            raise ValueError(f"missing API key env var: {self.api_key_env}")
        return value

    def to_safe_dict(self) -> JsonObject:
        return {
            "entry_id": self.entry_id,
            "enabled": self.enabled,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "model": self.model,
            "endpoint": self.endpoint,
            "supports_json_mode": self.supports_json_mode,
            "supports_streaming": self.supports_streaming,
            "request_overrides": dict(self.request_overrides),
            "pricing": dict(self.pricing),
            "tags": list(self.tags),
        }


@dataclass(frozen=True)
class AIAPIExecutorConfig:
    schema_version: str
    executor_id: str
    provider_family: str
    selection_policy: JsonObject
    defaults: JsonObject
    entries: list[AIAPIProviderEntry]
    local_concurrency: JsonObject
    metadata: JsonObject

    @property
    def config_digest(self) -> str:
        return _sha256_json(self.to_safe_dict())

    def to_safe_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "executor_id": self.executor_id,
            "provider_family": self.provider_family,
            "selection_policy": dict(self.selection_policy),
            "defaults": dict(self.defaults),
            "entries": [entry.to_safe_dict() for entry in self.entries],
            "local_concurrency": dict(self.local_concurrency),
            "metadata": dict(self.metadata),
        }


def load_ai_api_config(body: JsonObject) -> AIAPIExecutorConfig:
    if body.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise ValueError("unsupported ai api config schema")
    if body.get("provider_family") != "siliconflow":
        raise ValueError("phase7 first slice supports siliconflow only")
    entries = [_load_entry(entry) for entry in body.get("entries", [])]
    if not entries:
        raise ValueError("at least one ai api entry is required")
    seen: set[str] = set()
    for entry in entries:
        if entry.entry_id in seen:
            raise ValueError(f"duplicate ai api entry: {entry.entry_id}")
        seen.add(entry.entry_id)
    return AIAPIExecutorConfig(
        schema_version=str(body["schema_version"]),
        executor_id=str(body["executor_id"]),
        provider_family=str(body["provider_family"]),
        selection_policy=dict(body["selection_policy"]),
        defaults=dict(body["defaults"]),
        entries=entries,
        local_concurrency=dict(body.get("local_concurrency", {})),
        metadata=dict(body.get("metadata", {})),
    )


def _load_entry(body: JsonObject) -> AIAPIProviderEntry:
    if "api_key" in body:
        raise ValueError("api key value must not be stored in ai api config")
    pricing = dict(body.get("pricing", {}))
    for field in ("currency", "input_per_million_tokens", "output_per_million_tokens"):
        if field not in pricing:
            raise ValueError(f"missing pricing field: {field}")
    enabled = _required_bool(body, "enabled")
    supports_json_mode = _required_bool(body, "supports_json_mode")
    supports_streaming = _optional_bool(body, "supports_streaming", default=False)
    return AIAPIProviderEntry(
        entry_id=str(body["entry_id"]),
        enabled=enabled,
        base_url=str(body["base_url"]).rstrip("/"),
        api_key_env=str(body["api_key_env"]),
        model=str(body["model"]),
        endpoint=str(body.get("endpoint", "/chat/completions")),
        supports_json_mode=supports_json_mode,
        supports_streaming=supports_streaming,
        request_overrides=dict(body.get("request_overrides", {})),
        pricing=pricing,
        tags=[str(tag) for tag in body.get("tags", [])],
    )


def _required_bool(body: JsonObject, field: str) -> bool:
    value = body.get(field)
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _optional_bool(body: JsonObject, field: str, *, default: bool) -> bool:
    value = body.get(field, default)
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _sha256_json(data: JsonObject) -> str:
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return f"sha256:{sha256(encoded).hexdigest()}"
