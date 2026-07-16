import pytest

from tests.phase7_fixtures import make_config_dict
from tokenshare.executors.ai_api_config import load_ai_api_config


def test_load_ai_api_config_rejects_plaintext_api_key() -> None:
    body = make_config_dict()
    body["entries"][0]["api_key"] = "sk-not-allowed"

    with pytest.raises(ValueError, match="api key value must not be stored"):
        load_ai_api_config(body)


def test_load_ai_api_config_validates_entries_and_digest(monkeypatch) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "secret-b")

    config = load_ai_api_config(make_config_dict())

    assert config.schema_version == "phase7.ai_api_executor_config.v1"
    assert config.provider_family == "siliconflow"
    assert [entry.entry_id for entry in config.entries] == ["sf_qwen", "sf_deepseek"]
    assert config.entries[0].api_key_env == "SILICONFLOW_API_KEY_A"
    assert config.entries[0].resolve_api_key() == "secret-a"
    assert "secret-a" not in config.config_digest
    assert config.config_digest.startswith("sha256:")


def test_load_ai_api_config_accepts_openai_provider_family_and_reasoning_digest(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    body = make_config_dict()
    body["provider_family"] = "openai"
    body["entries"] = [
        {
            "entry_id": "openai_gpt_5_6_sol_high",
            "enabled": True,
            "base_url": "https://api.openai.com/v1",
            "api_key_env": "OPENAI_API_KEY",
            "model": "gpt-5.6-sol",
            "endpoint": "/chat/completions",
            "supports_json_mode": True,
            "supports_streaming": False,
            "request_overrides": {"reasoning_effort": "high", "temperature": 0.0},
            "pricing": {
                "currency": "USD",
                "input_per_million_tokens": 0.0,
                "output_per_million_tokens": 0.0,
            },
            "tags": ["paper", "openai", "reasoning:high"],
        }
    ]

    config = load_ai_api_config(body)
    safe = config.to_safe_dict()
    changed_body = make_config_dict()
    changed_body.update({key: body[key] for key in ("provider_family", "entries")})
    changed_body["entries"][0]["request_overrides"] = {"reasoning_effort": "medium"}
    changed_config = load_ai_api_config(changed_body)

    assert config.provider_family == "openai"
    assert config.entries[0].resolve_api_key() == "openai-secret"
    assert safe["provider_family"] == "openai"
    assert safe["entries"][0]["model"] == "gpt-5.6-sol"
    assert safe["entries"][0]["api_key_env"] == "OPENAI_API_KEY"
    assert safe["entries"][0]["request_overrides"]["reasoning_effort"] == "high"
    assert "openai-secret" not in config.config_digest
    assert config.config_digest != changed_config.config_digest


def test_load_ai_api_config_rejects_duplicate_entry_ids() -> None:
    body = make_config_dict()
    body["entries"][1]["entry_id"] = body["entries"][0]["entry_id"]

    with pytest.raises(ValueError, match="duplicate ai api entry"):
        load_ai_api_config(body)


@pytest.mark.parametrize("field", ["enabled", "supports_json_mode", "supports_streaming"])
def test_load_ai_api_config_rejects_string_boolean_fields(field: str) -> None:
    body = make_config_dict()
    body["entries"][0][field] = "false"

    with pytest.raises(ValueError, match=f"{field} must be a boolean"):
        load_ai_api_config(body)
