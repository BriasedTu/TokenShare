"""被 gitignore 的本地 AI API smoke config loader。"""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from pathlib import Path

from tokenshare.core.models import JsonObject
from tokenshare.executors.ai_api_config import AIAPIExecutorConfig, load_ai_api_config


DEFAULT_LOCAL_AI_API_CONFIG_PATH = Path("local/ai_api_smoke.local.json")


def load_local_ai_api_config(path: str | Path = DEFAULT_LOCAL_AI_API_CONFIG_PATH) -> AIAPIExecutorConfig:
    """读取本地 JSON，并把其中的 API key 仅注入当前进程环境变量。"""

    config_path = Path(path)
    body = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        raise ValueError("local ai api config must be a JSON object")
    sanitized = _inject_local_secrets(body)
    return load_ai_api_config(sanitized)


def _inject_local_secrets(body: JsonObject) -> JsonObject:
    sanitized = deepcopy(body)
    entries = sanitized.get("entries", [])
    if not isinstance(entries, list):
        raise ValueError("entries must be a list")
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("each ai api entry must be an object")
        if "api_key" not in entry:
            continue
        secret = entry.pop("api_key")
        if not isinstance(secret, str) or not secret:
            raise ValueError("api_key must be a non-empty string in local config")
        env_name = str(entry.get("api_key_env") or _local_env_name(str(entry["entry_id"])))
        os.environ[env_name] = secret
        entry["api_key_env"] = env_name
    return sanitized


def _local_env_name(entry_id: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", entry_id).strip("_").upper()
    if not normalized:
        normalized = "DEFAULT"
    return f"TOKENSHARE_LOCAL_AI_API_KEY_{normalized}"
