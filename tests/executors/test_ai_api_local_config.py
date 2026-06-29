import json
import os
from pathlib import Path

from tests.phase7_fixtures import make_config_dict
from tokenshare.executors.ai_api_local_config import (
    DEFAULT_LOCAL_AI_API_CONFIG_PATH,
    load_local_ai_api_config,
)


def test_local_ai_api_config_file_can_hold_gitignored_secret_without_persisting_it(
    tmp_path: Path,
):
    body = make_config_dict()
    body["entries"] = [body["entries"][0]]
    body["entries"][0].pop("api_key_env")
    body["entries"][0]["api_key"] = "sf-test-secret"
    config_path = tmp_path / "ai_api_smoke.local.json"
    config_path.write_text(json.dumps(body), encoding="utf-8")

    config = load_local_ai_api_config(config_path)

    assert config.entries[0].api_key_env == "TOKENSHARE_LOCAL_AI_API_KEY_SF_QWEN"
    assert os.environ[config.entries[0].api_key_env] == "sf-test-secret"
    assert config.entries[0].resolve_api_key() == "sf-test-secret"
    assert "sf-test-secret" not in json.dumps(config.to_safe_dict(), sort_keys=True)
    assert "sf-test-secret" not in config.config_digest


def test_default_local_ai_api_config_path_points_to_local_ignored_json():
    assert DEFAULT_LOCAL_AI_API_CONFIG_PATH.as_posix() == "local/ai_api_smoke.local.json"


def test_default_local_ai_api_config_path_is_gitignored():
    gitignore = Path(".gitignore").read_text(encoding="utf-8")

    assert "local/*.local.json" in gitignore


def test_local_ai_api_config_disables_unfilled_placeholder_keys(tmp_path: Path):
    body = make_config_dict()
    body["entries"] = [body["entries"][0], body["entries"][1]]
    body["entries"][0].pop("api_key_env")
    body["entries"][0]["api_key"] = "sf-real-ish-key"
    body["entries"][1].pop("api_key_env")
    body["entries"][1]["api_key"] = "PASTE_SILICONFLOW_API_KEY_2_HERE"
    config_path = tmp_path / "ai_api_smoke.local.json"
    config_path.write_text(json.dumps(body), encoding="utf-8")

    config = load_local_ai_api_config(config_path)

    assert [entry.enabled for entry in config.entries] == [True, False]
    assert os.environ[config.entries[0].api_key_env] == "sf-real-ish-key"
    assert config.entries[1].api_key_env == "TOKENSHARE_LOCAL_AI_API_KEY_SF_DEEPSEEK"


def test_local_ai_api_config_accepts_utf8_bom_from_windows_editors(tmp_path: Path):
    body = make_config_dict()
    body["entries"] = [body["entries"][0]]
    body["entries"][0].pop("api_key_env")
    body["entries"][0]["api_key"] = "sf-bom-key"
    config_path = tmp_path / "ai_api_smoke.local.json"
    config_path.write_bytes(b"\xef\xbb\xbf" + json.dumps(body).encode("utf-8"))

    config = load_local_ai_api_config(config_path)

    assert config.entries[0].resolve_api_key() == "sf-bom-key"


def test_local_ai_api_config_expands_api_key_pool_across_model_matrix(tmp_path: Path):
    body = make_config_dict()
    body.pop("entries")
    body["api_keys"] = [
        {"key_id": "key_1", "api_key": "sf-key-1"},
        {"key_id": "key_2", "api_key": "PASTE_SILICONFLOW_API_KEY_2_HERE"},
    ]
    body["models"] = [
        {
            "model_id": "qwen",
            "model": "Qwen/Qwen3.6-27B",
            "supports_json_mode": True,
            "pricing": {
                "currency": "CNY",
                "input_per_million_tokens": 0.3,
                "output_per_million_tokens": 3.2,
            },
        },
        {
            "model_id": "minimax",
            "model": "MiniMaxAI/MiniMax-M2.5",
            "supports_json_mode": True,
            "pricing": {
                "currency": "CNY",
                "input_per_million_tokens": 0.3,
                "output_per_million_tokens": 1.2,
            },
        },
    ]
    config_path = tmp_path / "ai_api_smoke.local.json"
    config_path.write_text(json.dumps(body), encoding="utf-8")

    config = load_local_ai_api_config(config_path)

    assert [entry.entry_id for entry in config.entries] == [
        "qwen__key_1",
        "qwen__key_2",
        "minimax__key_1",
        "minimax__key_2",
    ]
    assert [entry.enabled for entry in config.entries] == [True, False, True, False]
    assert {entry.model for entry in config.entries if entry.enabled} == {
        "Qwen/Qwen3.6-27B",
        "MiniMaxAI/MiniMax-M2.5",
    }
    assert {entry.base_url for entry in config.entries} == {"https://api.siliconflow.cn/v1"}
