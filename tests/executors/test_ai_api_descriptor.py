from tokenshare.executors.ai_api import build_ai_api_executor_descriptor
from tokenshare.executors.registry import ExecutorRegistry


def test_ai_api_executor_descriptor_matches_registry_requirements() -> None:
    descriptor = build_ai_api_executor_descriptor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
    )
    registry = ExecutorRegistry()
    registry.register(descriptor)

    matches = registry.match_available(
        executor_type="ai_api",
        hard_requirements={"executor": "ai_api", "provider_family": "siliconflow"},
        request_schema_version="phase3.execution_request.v1",
    )

    assert [item.executor_id for item in matches] == ["executor_ai_api"]
    assert descriptor.capabilities["provider_family"] == "siliconflow"
    assert descriptor.capabilities["output_modes"] == [
        "raw_text",
        "parsed_json",
        "parse_failure",
    ]


def test_ai_api_public_exports_are_available() -> None:
    from tokenshare.executors import AIAPIExecutor, build_ai_api_executor_descriptor, load_ai_api_config

    assert AIAPIExecutor.__name__ == "AIAPIExecutor"
    assert build_ai_api_executor_descriptor().executor_type == "ai_api"
    assert callable(load_ai_api_config)
