"""Executor 合同、离线实现与保留的 API transport 公共面。"""

from tokenshare.executors.ai_api_artifacts import (
    RawModelIdentityEvidence,
    build_raw_model_identity_fields,
    classify_response_model,
    read_raw_model_identity_evidence,
)
from tokenshare.executors.ai_api_config import (
    AIAPIExecutorConfig,
    AIAPIProviderEntry,
    load_ai_api_config,
)
from tokenshare.executors.ai_api_transport import (
    DeepSeekChatResult,
    DeepSeekProviderError,
    OpenAIChatResult,
    OpenAIProviderError,
    SiliconFlowChatResult,
    SiliconFlowProviderError,
    UrlLibDeepSeekTransport,
    UrlLibOpenAITransport,
    UrlLibSiliconFlowTransport,
    build_deepseek_chat_body,
    build_openai_chat_body,
    build_siliconflow_chat_body,
    parse_deepseek_response,
    parse_openai_response,
    parse_siliconflow_response,
)
from tokenshare.executors.contracts import (
    EnvironmentRef,
    ExecutionRequest,
    ExecutionSubmission,
    ExecutorDescriptor,
    ExecutorStatus,
    PromptPackage,
)
from tokenshare.executors.descriptors import build_ai_api_executor_descriptor
from tokenshare.executors.deterministic import DeterministicLocalExecutor
from tokenshare.executors.mock_ai import MockAIExecutor, MockAIExecutorProfile
from tokenshare.executors.registry import ExecutorRegistry

__all__ = [
    "AIAPIExecutorConfig",
    "AIAPIProviderEntry",
    "DeepSeekChatResult",
    "DeepSeekProviderError",
    "DeterministicLocalExecutor",
    "EnvironmentRef",
    "ExecutionRequest",
    "ExecutionSubmission",
    "ExecutorDescriptor",
    "ExecutorRegistry",
    "ExecutorStatus",
    "MockAIExecutor",
    "MockAIExecutorProfile",
    "OpenAIChatResult",
    "OpenAIProviderError",
    "PromptPackage",
    "RawModelIdentityEvidence",
    "SiliconFlowChatResult",
    "SiliconFlowProviderError",
    "UrlLibDeepSeekTransport",
    "UrlLibOpenAITransport",
    "UrlLibSiliconFlowTransport",
    "build_ai_api_executor_descriptor",
    "build_deepseek_chat_body",
    "build_openai_chat_body",
    "build_raw_model_identity_fields",
    "build_siliconflow_chat_body",
    "classify_response_model",
    "load_ai_api_config",
    "parse_deepseek_response",
    "parse_openai_response",
    "parse_siliconflow_response",
    "read_raw_model_identity_evidence",
]
