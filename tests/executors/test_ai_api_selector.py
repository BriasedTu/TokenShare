import pytest

from tests.phase7_fixtures import make_config_dict
from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.executors.ai_api_selector import build_provider_selection


def test_provider_selection_filters_missing_secret(monkeypatch) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    monkeypatch.delenv("SILICONFLOW_API_KEY_B", raising=False)
    config = load_ai_api_config(make_config_dict())

    selection = build_provider_selection(
        config=config,
        request_id="request_ai_1",
        environment_seed=7,
        require_json_mode=True,
    )

    assert selection.eligible_entry_ids == ["sf_qwen"]
    assert selection.selected_entry_id == "sf_qwen"
    assert selection.attempt_entry_ids == ["sf_qwen"]


def test_provider_selection_is_stable_for_seed(monkeypatch) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "secret-b")
    config = load_ai_api_config(make_config_dict())

    first = build_provider_selection(
        config=config,
        request_id="request_ai_1",
        environment_seed=7,
        require_json_mode=True,
    )
    second = build_provider_selection(
        config=config,
        request_id="request_ai_1",
        environment_seed=7,
        require_json_mode=True,
    )

    assert first.to_dict() == second.to_dict()
    assert set(first.attempt_entry_ids) == {"sf_qwen", "sf_deepseek"}


def test_provider_selection_requires_json_mode(monkeypatch) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    body = make_config_dict()
    body["entries"][0]["supports_json_mode"] = False
    body["entries"][1]["enabled"] = False
    config = load_ai_api_config(body)

    with pytest.raises(ValueError, match="no eligible ai api entries"):
        build_provider_selection(
            config=config,
            request_id="request_ai_1",
            environment_seed=7,
            require_json_mode=True,
        )
