"""公开 executor descriptor 构造器。"""

from tokenshare.executors.contracts import (
    ExecutorDescriptor,
    ExecutorStatus,
)


def build_ai_api_executor_descriptor(
    *,
    executor_id: str = "executor_ai_api",
    executor_version: str = "0.1.0",
    provider_family: str = "siliconflow",
) -> ExecutorDescriptor:
    if provider_family not in {"siliconflow", "openai", "deepseek"}:
        raise ValueError(f"unsupported ai api provider_family: {provider_family}")
    return ExecutorDescriptor(
        executor_id=executor_id,
        executor_type="ai_api",
        executor_version=executor_version,
        supported_request_schema_versions=[
            "phase3.execution_request.v1",
            "phase3.execution_request.v2",
        ],
        capabilities={
            "executor": "ai_api",
            "provider_family": provider_family,
            "output_modes": ["raw_text", "parsed_json", "parse_failure"],
            "provider_failover": "request_scoped_bounded",
        },
        environment_policy={
            "runtime": "python",
            "network": "optional_real_api",
            "secret_source": "environment_variables_only",
        },
        status=ExecutorStatus.AVAILABLE,
        metadata={
            "phase": "phase7",
            "adapter": f"{provider_family}_chat_completions",
            "production_platform": False,
        },
    )
