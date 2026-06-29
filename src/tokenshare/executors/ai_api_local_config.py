"""被 gitignore 的本地 AI API smoke config loader。"""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from tokenshare.core.models import JsonObject
from tokenshare.executors.ai_api_config import AIAPIExecutorConfig, load_ai_api_config


DEFAULT_LOCAL_AI_API_CONFIG_PATH = Path("local/ai_api_smoke.local.json")


@dataclass(frozen=True)
class _LocalAPIKeySlot:
    key_id: str
    api_key_env: str
    enabled: bool


def load_local_ai_api_config(path: str | Path = DEFAULT_LOCAL_AI_API_CONFIG_PATH) -> AIAPIExecutorConfig:
    """读取本地 JSON，并把其中的 API key 仅注入当前进程环境变量。"""

    config_path = Path(path)
    body = json.loads(config_path.read_text(encoding="utf-8-sig"))
    if not isinstance(body, dict):
        raise ValueError("local ai api config must be a JSON object")
    sanitized = _inject_local_secrets(body)
    return load_ai_api_config(sanitized)


def _inject_local_secrets(body: JsonObject) -> JsonObject:
    sanitized = deepcopy(body)
    if "models" in sanitized:
        sanitized = _expand_model_matrix(sanitized)
    entries = sanitized.get("entries", [])
    if not isinstance(entries, list):
        raise ValueError("entries must be a list")
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("each ai api entry must be an object")
        if "api_key" not in entry:
            continue
        secret = entry.pop("api_key")
        env_name = str(entry.get("api_key_env") or _local_env_name(str(entry["entry_id"])))
        if not isinstance(secret, str):
            raise ValueError("api_key must be a string in local config")
        if _is_unfilled_placeholder(secret):
            entry["enabled"] = False
            entry["api_key_env"] = env_name
            continue
        os.environ[env_name] = secret
        entry["api_key_env"] = env_name
    return sanitized


def _expand_model_matrix(body: JsonObject) -> JsonObject:
    expanded = deepcopy(body)
    key_slots = _load_key_slots(expanded.pop("api_keys", []))
    models = expanded.pop("models")
    if not isinstance(models, list) or not models:
        raise ValueError("models must be a non-empty list")
    if not key_slots:
        raise ValueError("api_keys must contain at least one key slot")
    entries: list[JsonObject] = []
    for model in models:
        if not isinstance(model, dict):
            raise ValueError("each model entry must be an object")
        model_enabled = bool(model.get("enabled", True))
        model_id = str(model["model_id"])
        for key_slot in key_slots:
            entries.append(
                {
                    "entry_id": f"{model_id}__{key_slot.key_id}",
                    "enabled": model_enabled and key_slot.enabled,
                    "base_url": str(model.get("base_url", "https://api.siliconflow.cn/v1")),
                    "api_key_env": key_slot.api_key_env,
                    "model": str(model["model"]),
                    "endpoint": str(model.get("endpoint", "/chat/completions")),
                    "supports_json_mode": bool(model.get("supports_json_mode", False)),
                    "supports_streaming": bool(model.get("supports_streaming", False)),
                    "request_overrides": dict(model.get("request_overrides", {})),
                    "pricing": dict(model["pricing"]),
                    "tags": [str(tag) for tag in model.get("tags", [])]
                    + [f"api_key:{key_slot.key_id}"],
                }
            )
    expanded["entries"] = entries
    return expanded


def _load_key_slots(key_bodies: object) -> list[_LocalAPIKeySlot]:
    if not isinstance(key_bodies, list):
        raise ValueError("api_keys must be a list")
    slots: list[_LocalAPIKeySlot] = []
    seen: set[str] = set()
    for key_body in key_bodies:
        if not isinstance(key_body, dict):
            raise ValueError("each api key slot must be an object")
        key_id = str(key_body["key_id"])
        if key_id in seen:
            raise ValueError(f"duplicate api key slot: {key_id}")
        seen.add(key_id)
        secret = key_body.get("api_key", "")
        if not isinstance(secret, str):
            raise ValueError("api_key must be a string in local config")
        env_name = str(key_body.get("api_key_env") or _local_env_name(key_id))
        enabled = bool(key_body.get("enabled", True)) and not _is_unfilled_placeholder(secret)
        if enabled:
            os.environ[env_name] = secret
        slots.append(_LocalAPIKeySlot(key_id=key_id, api_key_env=env_name, enabled=enabled))
    return slots


def _is_unfilled_placeholder(secret: str) -> bool:
    normalized = secret.strip()
    if not normalized:
        return True
    upper = normalized.upper()
    return upper.startswith("PASTE_") or upper.startswith("REPLACE_")


def _local_env_name(entry_id: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", entry_id).strip("_").upper()
    if not normalized:
        normalized = "DEFAULT"
    return f"TOKENSHARE_LOCAL_AI_API_KEY_{normalized}"
