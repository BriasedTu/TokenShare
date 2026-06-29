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
