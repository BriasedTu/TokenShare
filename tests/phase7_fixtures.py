import json
from dataclasses import dataclass
from typing import Any

from tokenshare.executors.contracts import ExecutionRequest, PromptPackage
from tokenshare.storage.artifacts import ArtifactStore
from tests.phase2_fixtures import make_unit
from tests.phase3_fixtures import make_environment_ref, make_output_contract


def make_prompt_ref(store: ArtifactStore, *, request_id: str = "request_ai_1"):
    prompt = PromptPackage(
        prompt_package_id=f"prompt_{request_id}",
        request_id=request_id,
        task_id="task_ai",
        unit_id="unit_ai",
        prompt_text='Return JSON: {"answer": "ok"}.',
        input_summary={"question": "demo"},
        output_schema={"type": "object", "required": ["answer"]},
        constraints={"format": "json", "requires_json_mode": True},
        seed=17,
        fixture_profile="phase7",
        created_at="2026-06-28T00:00:00Z",
    )
    return store.save_json(
        prompt.to_dict(),
        artifact_id=prompt.prompt_package_id,
        artifact_type="PromptPackage",
        artifact_schema_id="phase3.prompt_package",
        artifact_schema_version="v1",
        source={"kind": "phase7_test"},
        metadata={},
        created_at=prompt.created_at,
    )


def make_ai_request(store: ArtifactStore, *, request_id: str = "request_ai_1"):
    prompt_ref = make_prompt_ref(store, request_id=request_id)
    return ExecutionRequest(
        request_id=request_id,
        task_id="task_ai",
        unit_id="unit_ai",
        attempt_id="attempt_ai_1",
        lease_id="lease_ai_1",
        fencing_token="fence_ai_1",
        plugin={
            "plugin_id": "structured_report_stub",
            "plugin_version": "0.1.0",
            "plugin_descriptor_ref": {"artifact_id": "plugin_descriptor_structured"},
            "plugin_descriptor_digest": "sha256:plugin",
            "ai_output_parser_policy_id": "structured_report.answer_parser.v1",
        },
        executor={
            "executor_id": "executor_ai_api",
            "executor_version": "0.1.0",
            "executor_descriptor_ref": {"artifact_id": "executor_descriptor_ai_api"},
            "executor_descriptor_digest": "sha256:executor",
        },
        registry_snapshot_id="registry_snapshot_ai",
        allocation_decision={
            "decision_id": "allocation_ai_1",
            "selected_executor_id": "executor_ai_api",
            "eligible_executor_ids": ["executor_ai_api"],
        },
        capability_snapshot={"executor": "ai_api", "provider_family": "siliconflow"},
        task_unit_snapshot=make_unit(unit_id="unit_ai").to_dict(),
        input_artifact_refs={},
        output_contract=make_output_contract(),
        hard_requirements={"executor": "ai_api", "provider_family": "siliconflow"},
        soft_hints={"temperature": 0.2},
        environment_ref=make_environment_ref(),
        execution_instruction_ref=None,
        prompt_package_ref=prompt_ref,
        limits={"timeout_seconds": 30, "max_tokens": 128},
        created_at="2026-06-28T00:00:01Z",
    )


def make_config_dict():
    return {
        "schema_version": "phase7.ai_api_executor_config.v1",
        "executor_id": "executor_ai_api",
        "provider_family": "siliconflow",
        "selection_policy": {
            "kind": "uniform_random_without_weights",
            "seed_source": "request_or_environment_seed",
        },
        "defaults": {
            "timeout_seconds": 30,
            "max_tokens": 128,
            "temperature": 0.2,
            "top_p": 0.9,
            "stream": False,
            "max_provider_attempts": 3,
        },
        "entries": [
            {
                "entry_id": "sf_qwen",
                "enabled": True,
                "base_url": "https://api.siliconflow.cn/v1",
                "api_key_env": "SILICONFLOW_API_KEY_A",
                "model": "Qwen/Qwen2.5-7B-Instruct",
                "endpoint": "/chat/completions",
                "supports_json_mode": True,
                "supports_streaming": False,
                "request_overrides": {"temperature": 0.1},
                "pricing": {
                    "currency": "CNY",
                    "input_per_million_tokens": 0.0,
                    "output_per_million_tokens": 0.0,
                    "observed_at": "2026-06-28",
                    "source_note": "test fixture price",
                },
                "tags": ["json_mode", "test"],
            },
            {
                "entry_id": "sf_deepseek",
                "enabled": True,
                "base_url": "https://api.siliconflow.cn/v1",
                "api_key_env": "SILICONFLOW_API_KEY_B",
                "model": "deepseek-ai/DeepSeek-V3",
                "endpoint": "/chat/completions",
                "supports_json_mode": True,
                "supports_streaming": False,
                "request_overrides": {},
                "pricing": {
                    "currency": "CNY",
                    "input_per_million_tokens": 1.0,
                    "output_per_million_tokens": 2.0,
                    "observed_at": "2026-06-28",
                    "source_note": "test fixture price",
                },
                "tags": ["json_mode", "test"],
            },
        ],
        "local_concurrency": {"max_in_flight_global": 4},
        "metadata": {"purpose": "phase7-tests"},
    }


@dataclass
class FakeProviderResponse:
    status_code: int
    body: dict[str, Any] | None = None
    text: str = ""
    error: str | None = None


class FakeSiliconFlowTransport:
    def __init__(self, responses: list[FakeProviderResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def post_chat_completion(
        self,
        *,
        entry,
        api_key: str,
        body: dict[str, Any],
        timeout_seconds: int,
    ):
        self.calls.append(
            {
                "entry_id": entry.entry_id,
                "model": entry.model,
                "body": json.loads(json.dumps(body, sort_keys=True)),
                "timeout_seconds": timeout_seconds,
                "api_key_seen": bool(api_key),
            }
        )
        if not self.responses:
            raise AssertionError("fake transport has no remaining response")
        response = self.responses.pop(0)
        if response.error == "timeout":
            raise TimeoutError("fake timeout")
        return response
