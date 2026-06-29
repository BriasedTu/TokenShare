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
