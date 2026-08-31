from pathlib import Path

import pytest

import tokenshare.executors as executors
from tokenshare.executors.contracts import ExecutorStatus
from tokenshare.executors.descriptors import build_ai_api_executor_descriptor
from tokenshare.executors.registry import ExecutorRegistry


@pytest.mark.parametrize(
    ("provider_family", "expected_adapter"),
    (
        ("siliconflow", "siliconflow_chat_completions"),
        ("openai", "openai_chat_completions"),
        ("deepseek", "deepseek_chat_completions"),
    ),
)
def test_ai_api_descriptor_matches_frozen_provider_contract(
    provider_family: str,
    expected_adapter: str,
) -> None:
    descriptor = build_ai_api_executor_descriptor(
        executor_id=f"executor_ai_api_{provider_family}",
        executor_version="0.1.0",
        provider_family=provider_family,
    )
    registry = ExecutorRegistry()
    registry.register(descriptor)

    matches = registry.match_available(
        executor_type="ai_api",
        hard_requirements={"executor": "ai_api", "provider_family": provider_family},
        request_schema_version="phase3.execution_request.v1",
    )

    assert [item.executor_id for item in matches] == [
        f"executor_ai_api_{provider_family}"
    ]
    assert descriptor.executor_id == f"executor_ai_api_{provider_family}"
    assert descriptor.executor_type == "ai_api"
    assert descriptor.executor_version == "0.1.0"
    assert descriptor.supported_request_schema_versions == [
        "phase3.execution_request.v1",
        "phase3.execution_request.v2",
    ]
    assert descriptor.capabilities == {
        "executor": "ai_api",
        "provider_family": provider_family,
        "output_modes": ["raw_text", "parsed_json", "parse_failure"],
        "provider_failover": "request_scoped_bounded",
    }
    assert descriptor.environment_policy == {
        "runtime": "python",
        "network": "optional_real_api",
        "secret_source": "environment_variables_only",
    }
    assert descriptor.normalized_status is ExecutorStatus.AVAILABLE
    assert descriptor.metadata == {
        "phase": "phase7",
        "adapter": expected_adapter,
        "production_platform": False,
    }


def test_ai_api_descriptor_rejects_unknown_provider_family() -> None:
    with pytest.raises(
        ValueError,
        match="^unsupported ai api provider_family: unknown$",
    ):
        build_ai_api_executor_descriptor(provider_family="unknown")


def test_executor_package_exports_only_the_retained_descriptor_surface() -> None:
    assert executors.build_ai_api_executor_descriptor is build_ai_api_executor_descriptor
    assert not hasattr(executors, "AIAPIExecutor")


def test_plugin_adapters_import_descriptor_from_public_module() -> None:
    source_root = Path(__file__).parents[2] / "src" / "tokenshare" / "plugins"
    for relative_path in (
        Path("factorization/runtime_adapter.py"),
        Path("lean_proof/runtime_adapter.py"),
    ):
        source = (source_root / relative_path).read_text(encoding="utf-8")
        assert "tokenshare.executors.ai_api" not in source
        assert "tokenshare.executors.descriptors" in source
