# Phase 7 Experimental AI API Executor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Phase 7 experimental real AI API executor for SiliconFlow through the existing `ExecutionRequest` / `ExecutionSubmission` protocol boundary.

**Architecture:** The executor is split into small files: config validation, descriptor builder, SiliconFlow transport, random provider selection, plugin parser bridge, executor orchestration, and replay guard helpers. All non-deterministic provider choices, responses, parse results, usage, cost, and errors are persisted as artifacts; replay reads artifacts and never calls the API.

**Tech Stack:** Python 3.12 standard library only, local JSON config, SQLite/JSONL existing projection flow, `ArtifactStore`, pytest, fake transport for baseline tests, optional SiliconFlow smoke test gated by local environment variables.

---

## 1. Scope Check

The Phase 7 spec is one subsystem: an experiment-scoped AI API executor. It depends on Phase 3 executor contracts and Phase 6 plugin-owned prompt packages, but it does not implement structured report, Lean, experiment runner, metrics, or replay engine end-to-end.

This plan keeps `feat-008` implementation separate from `feat-007`. If the team has not completed or explicitly paused Phase 6, record that decision before executing this plan.

## 2. File Structure

| Path | Action | Responsibility |
|---|---|---|
| `src/tokenshare/executors/ai_api_config.py` | Create | Dataclasses and validation for `phase7.ai_api_executor_config.v1`, config digest, secret env lookup without persisting key values. |
| `src/tokenshare/executors/ai_api_transport.py` | Create | SiliconFlow request/response transport boundary, fake transport for tests, stdlib HTTP transport for optional smoke. |
| `src/tokenshare/executors/ai_api_selector.py` | Create | Eligible entry filtering, seeded uniform selection, provider failover order, selection record. |
| `src/tokenshare/executors/ai_api.py` | Create | `AIAPIExecutor`, artifact persistence, parser bridge, submission mapping. |
| `src/tokenshare/executors/ai_api_replay.py` | Create | Minimal audit helper proving replay reads required artifacts and never calls transport. |
| `src/tokenshare/executors/__init__.py` | Modify | Export public Phase 7 executor APIs if the package starts exporting executor classes. |
| `tests/executors/test_ai_api_config.py` | Create | Config validation, digest, secret redaction. |
| `tests/executors/test_ai_api_descriptor.py` | Create | Descriptor builder and `ExecutorRegistry` matching. |
| `tests/executors/test_ai_api_transport.py` | Create | SiliconFlow request body, fake response mapping, error envelope mapping. |
| `tests/executors/test_ai_api_selector.py` | Create | Eligible filtering, deterministic seeded uniform selection, failover order. |
| `tests/executors/test_ai_api_executor_success.py` | Create | End-to-end success with raw, parsed, provenance, usage, cost artifacts. |
| `tests/executors/test_ai_api_executor_failover.py` | Create | 429 / 503 / timeout failover and final executor error. |
| `tests/executors/test_ai_api_executor_parser.py` | Create | Plugin-owned parser success, parse failure, raw-only mode. |
| `tests/executors/test_ai_api_replay_guard.py` | Create | Replay guard reads artifacts only and fails on missing historical output. |
| `tests/phase7_fixtures.py` | Create | Shared request, prompt, config, fake transport, and parser helpers. |
| `Doc/TechnicalDocument/2026-06-28-phase-7-ai-api-executor-code-map.md` | Create | Source/test/spec mapping and verification evidence after implementation. |
| `feature_list.json`, `progress.md`, `session-handoff.md`, `Doc/agent-navigation.md` | Modify | Status/evidence/index sync after implementation tasks. |

## 3. Shared Test Fixture Contract

Create `tests/phase7_fixtures.py` during Task 1 or Task 2. Use this exact helper shape so later tests stay small.

```python
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
        prompt_text="Return JSON: {\"answer\": \"ok\"}.",
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

    def post_chat_completion(self, *, entry, api_key: str, body: dict[str, Any], timeout_seconds: int):
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
```

## 4. Tasks

### Task 1: Config Models And Validation

**Files:**
- Create: `src/tokenshare/executors/ai_api_config.py`
- Create: `tests/executors/test_ai_api_config.py`
- Create: `tests/phase7_fixtures.py`

- [ ] **Step 1: Write the failing tests**

```python
import os

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
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\executors\test_ai_api_config.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'tokenshare.executors.ai_api_config'`.

- [ ] **Step 3: Write minimal implementation**

```python
"""Phase 7 AI API executor local config models."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from tokenshare.core.models import JsonObject


CONFIG_SCHEMA_VERSION = "phase7.ai_api_executor_config.v1"


@dataclass(frozen=True)
class AIAPIProviderEntry:
    entry_id: str
    enabled: bool
    base_url: str
    api_key_env: str
    model: str
    endpoint: str
    supports_json_mode: bool
    supports_streaming: bool
    request_overrides: JsonObject
    pricing: JsonObject
    tags: list[str]

    def resolve_api_key(self) -> str:
        value = os.environ.get(self.api_key_env, "")
        if not value:
            raise ValueError(f"missing API key env var: {self.api_key_env}")
        return value

    def to_safe_dict(self) -> JsonObject:
        return {
            "entry_id": self.entry_id,
            "enabled": self.enabled,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "model": self.model,
            "endpoint": self.endpoint,
            "supports_json_mode": self.supports_json_mode,
            "supports_streaming": self.supports_streaming,
            "request_overrides": dict(self.request_overrides),
            "pricing": dict(self.pricing),
            "tags": list(self.tags),
        }


@dataclass(frozen=True)
class AIAPIExecutorConfig:
    schema_version: str
    executor_id: str
    provider_family: str
    selection_policy: JsonObject
    defaults: JsonObject
    entries: list[AIAPIProviderEntry]
    local_concurrency: JsonObject
    metadata: JsonObject

    @property
    def config_digest(self) -> str:
        return _sha256_json(self.to_safe_dict())

    def to_safe_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "executor_id": self.executor_id,
            "provider_family": self.provider_family,
            "selection_policy": dict(self.selection_policy),
            "defaults": dict(self.defaults),
            "entries": [entry.to_safe_dict() for entry in self.entries],
            "local_concurrency": dict(self.local_concurrency),
            "metadata": dict(self.metadata),
        }


def load_ai_api_config(body: JsonObject) -> AIAPIExecutorConfig:
    if body.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise ValueError("unsupported ai api config schema")
    if body.get("provider_family") != "siliconflow":
        raise ValueError("phase7 first slice supports siliconflow only")
    entries = [_load_entry(entry) for entry in body.get("entries", [])]
    if not entries:
        raise ValueError("at least one ai api entry is required")
    seen: set[str] = set()
    for entry in entries:
        if entry.entry_id in seen:
            raise ValueError(f"duplicate ai api entry: {entry.entry_id}")
        seen.add(entry.entry_id)
    return AIAPIExecutorConfig(
        schema_version=str(body["schema_version"]),
        executor_id=str(body["executor_id"]),
        provider_family=str(body["provider_family"]),
        selection_policy=dict(body["selection_policy"]),
        defaults=dict(body["defaults"]),
        entries=entries,
        local_concurrency=dict(body.get("local_concurrency", {})),
        metadata=dict(body.get("metadata", {})),
    )


def _load_entry(body: JsonObject) -> AIAPIProviderEntry:
    if "api_key" in body:
        raise ValueError("api key value must not be stored in ai api config")
    pricing = dict(body.get("pricing", {}))
    for field in ("currency", "input_per_million_tokens", "output_per_million_tokens"):
        if field not in pricing:
            raise ValueError(f"missing pricing field: {field}")
    return AIAPIProviderEntry(
        entry_id=str(body["entry_id"]),
        enabled=bool(body["enabled"]),
        base_url=str(body["base_url"]).rstrip("/"),
        api_key_env=str(body["api_key_env"]),
        model=str(body["model"]),
        endpoint=str(body.get("endpoint", "/chat/completions")),
        supports_json_mode=bool(body.get("supports_json_mode", False)),
        supports_streaming=bool(body.get("supports_streaming", False)),
        request_overrides=dict(body.get("request_overrides", {})),
        pricing=pricing,
        tags=[str(tag) for tag in body.get("tags", [])],
    )


def _sha256_json(data: JsonObject) -> str:
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return f"sha256:{sha256(encoded).hexdigest()}"
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```powershell
$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\executors\test_ai_api_config.py -q
```

Expected: PASS with `3 passed`.

- [ ] **Step 5: Local checkpoint**

Record the RED/GREEN evidence in `progress.md` after this task. Do not stage or commit unless the user explicitly asks for git work.

### Task 2: Executor Descriptor Builder

**Files:**
- Create or modify: `src/tokenshare/executors/ai_api.py`
- Create: `tests/executors/test_ai_api_descriptor.py`

- [ ] **Step 1: Write the failing test**

```python
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
    assert descriptor.capabilities["output_modes"] == ["raw_text", "parsed_json", "parse_failure"]
```

- [ ] **Step 2: Run test to verify RED**

Run:

```powershell
$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\executors\test_ai_api_descriptor.py -q
```

Expected: FAIL with `ImportError: cannot import name 'build_ai_api_executor_descriptor'`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/tokenshare/executors/ai_api.py`:

```python
"""Phase 7 experimental AI API executor."""

from __future__ import annotations

from tokenshare.executors.contracts import ExecutorDescriptor, ExecutorStatus


def build_ai_api_executor_descriptor(
    *,
    executor_id: str = "executor_ai_api",
    executor_version: str = "0.1.0",
) -> ExecutorDescriptor:
    return ExecutorDescriptor(
        executor_id=executor_id,
        executor_type="ai_api",
        executor_version=executor_version,
        supported_request_schema_versions=["phase3.execution_request.v1"],
        capabilities={
            "executor": "ai_api",
            "provider_family": "siliconflow",
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
            "adapter": "siliconflow_chat_completions",
            "production_platform": False,
        },
    )
```

- [ ] **Step 4: Run test to verify GREEN**

Run:

```powershell
$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\executors\test_ai_api_descriptor.py tests\executors\test_executor_registry.py -q
```

Expected: PASS with existing registry test still green.

- [ ] **Step 5: Local checkpoint**

Record the RED/GREEN evidence in `progress.md` after this task. Do not stage or commit unless the user explicitly asks for git work.

### Task 3: SiliconFlow Transport Boundary

**Files:**
- Create: `src/tokenshare/executors/ai_api_transport.py`
- Create: `tests/executors/test_ai_api_transport.py`

- [ ] **Step 1: Write failing tests**

```python
from tests.phase7_fixtures import FakeProviderResponse, make_config_dict
from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.executors.ai_api_transport import (
    SiliconFlowProviderError,
    build_siliconflow_chat_body,
    parse_siliconflow_response,
)


def test_build_siliconflow_chat_body_uses_prompt_and_json_mode() -> None:
    config = load_ai_api_config(make_config_dict())
    entry = config.entries[0]

    body = build_siliconflow_chat_body(
        entry=entry,
        prompt_text="Return JSON.",
        defaults=config.defaults,
        request_limits={"max_tokens": 64},
        soft_hints={"temperature": 0.3},
        require_json_mode=True,
    )

    assert body["model"] == entry.model
    assert body["messages"] == [{"role": "user", "content": "Return JSON."}]
    assert body["stream"] is False
    assert body["max_tokens"] == 64
    assert body["temperature"] == 0.1
    assert body["response_format"] == {"type": "json_object"}


def test_parse_siliconflow_response_extracts_content_and_usage() -> None:
    response = FakeProviderResponse(
        status_code=200,
        body={
            "id": "sf-response-1",
            "model": "deepseek-ai/DeepSeek-V3",
            "choices": [
                {"message": {"role": "assistant", "content": "{\"answer\":\"ok\"}"}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
    )

    parsed = parse_siliconflow_response(response)

    assert parsed.provider_response_id == "sf-response-1"
    assert parsed.content_text == "{\"answer\":\"ok\"}"
    assert parsed.usage["total_tokens"] == 15


def test_parse_siliconflow_response_maps_rate_limit_error() -> None:
    response = FakeProviderResponse(
        status_code=429,
        body={"code": "rate_limit", "message": "too many requests"},
    )

    try:
        parse_siliconflow_response(response)
    except SiliconFlowProviderError as exc:
        assert exc.error_kind == "rate_limited"
        assert exc.http_status == 429
    else:
        raise AssertionError("expected SiliconFlowProviderError")
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\executors\test_ai_api_transport.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'tokenshare.executors.ai_api_transport'`.

- [ ] **Step 3: Write minimal implementation**

```python
"""SiliconFlow chat-completions transport boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tokenshare.core.models import JsonObject
from tokenshare.executors.ai_api_config import AIAPIProviderEntry


@dataclass(frozen=True)
class SiliconFlowChatResult:
    provider_response_id: str | None
    model: str | None
    content_text: str
    finish_reason: str | None
    usage: JsonObject
    raw_response_json: JsonObject


class SiliconFlowProviderError(RuntimeError):
    def __init__(self, *, error_kind: str, http_status: int | None, message: str) -> None:
        super().__init__(message)
        self.error_kind = error_kind
        self.http_status = http_status
        self.message = message


def build_siliconflow_chat_body(
    *,
    entry: AIAPIProviderEntry,
    prompt_text: str,
    defaults: JsonObject,
    request_limits: JsonObject,
    soft_hints: JsonObject,
    require_json_mode: bool,
) -> JsonObject:
    body: JsonObject = {
        "model": entry.model,
        "messages": [{"role": "user", "content": prompt_text}],
        "stream": False,
        "temperature": _choose_number(
            entry.request_overrides.get("temperature"),
            soft_hints.get("temperature"),
            defaults.get("temperature"),
        ),
        "top_p": _choose_number(
            entry.request_overrides.get("top_p"),
            soft_hints.get("top_p"),
            defaults.get("top_p"),
        ),
        "max_tokens": int(request_limits.get("max_tokens", defaults.get("max_tokens", 1024))),
    }
    if require_json_mode:
        if not entry.supports_json_mode:
            raise ValueError(f"entry does not support json mode: {entry.entry_id}")
        body["response_format"] = {"type": "json_object"}
    return body


def parse_siliconflow_response(response: Any) -> SiliconFlowChatResult:
    status_code = int(response.status_code)
    body = dict(response.body or {})
    if status_code >= 400:
        raise SiliconFlowProviderError(
            error_kind=_map_http_error(status_code),
            http_status=status_code,
            message=str(body.get("message") or body.get("error") or response.text or status_code),
        )
    try:
        choice = body["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise SiliconFlowProviderError(
            error_kind="invalid_output",
            http_status=status_code,
            message="missing assistant message content",
        ) from exc
    return SiliconFlowChatResult(
        provider_response_id=body.get("id"),
        model=body.get("model"),
        content_text=str(content),
        finish_reason=choice.get("finish_reason"),
        usage=dict(body.get("usage", {})),
        raw_response_json=body,
    )


def _choose_number(*values: object) -> float:
    for value in values:
        if value is not None:
            return float(value)
    return 0.0


def _map_http_error(status_code: int) -> str:
    if status_code == 429:
        return "rate_limited"
    if status_code in {500, 503, 504}:
        return "provider_error"
    if status_code in {401, 403}:
        return "auth_error"
    if status_code in {400, 404}:
        return "client_error"
    return "provider_error"
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```powershell
$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\executors\test_ai_api_transport.py -q
```

Expected: PASS with `3 passed`.

- [ ] **Step 5: Local checkpoint**

Record the RED/GREEN evidence in `progress.md` after this task. Do not stage or commit unless the user explicitly asks for git work.

### Task 4: Eligible Selection And Failover Order

**Files:**
- Create: `src/tokenshare/executors/ai_api_selector.py`
- Create: `tests/executors/test_ai_api_selector.py`

- [ ] **Step 1: Write failing tests**

```python
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
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\executors\test_ai_api_selector.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'tokenshare.executors.ai_api_selector'`.

- [ ] **Step 3: Write minimal implementation**

```python
"""Provider selection for the Phase 7 AI API executor."""

from __future__ import annotations

import random
from dataclasses import dataclass
from hashlib import sha256

from tokenshare.core.models import JsonObject
from tokenshare.executors.ai_api_config import AIAPIExecutorConfig, AIAPIProviderEntry


@dataclass(frozen=True)
class AIProviderSelection:
    selection_policy_id: str
    eligible_entry_ids: list[str]
    selected_entry_id: str
    attempt_entry_ids: list[str]
    random_seed_material_digest: str
    selection_index: int

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": "phase7.ai_provider_selection.v1",
            "selection_policy_id": self.selection_policy_id,
            "eligible_entry_ids": list(self.eligible_entry_ids),
            "selected_entry_id": self.selected_entry_id,
            "attempt_entry_ids": list(self.attempt_entry_ids),
            "random_seed_material_digest": self.random_seed_material_digest,
            "selection_index": self.selection_index,
        }


def build_provider_selection(
    *,
    config: AIAPIExecutorConfig,
    request_id: str,
    environment_seed: int | None,
    require_json_mode: bool,
) -> AIProviderSelection:
    eligible = [
        entry
        for entry in config.entries
        if _entry_is_eligible(entry, require_json_mode=require_json_mode)
    ]
    if not eligible:
        raise ValueError("no eligible ai api entries")
    seed_material = f"{config.config_digest}|{request_id}|{environment_seed}"
    seed_digest = f"sha256:{sha256(seed_material.encode('utf-8')).hexdigest()}"
    rng = random.Random(seed_digest)
    ordered = list(eligible)
    rng.shuffle(ordered)
    selected = ordered[0]
    return AIProviderSelection(
        selection_policy_id=str(config.selection_policy["kind"]),
        eligible_entry_ids=[entry.entry_id for entry in eligible],
        selected_entry_id=selected.entry_id,
        attempt_entry_ids=[entry.entry_id for entry in ordered],
        random_seed_material_digest=seed_digest,
        selection_index=[entry.entry_id for entry in eligible].index(selected.entry_id),
    )


def entries_by_attempt_order(
    *,
    config: AIAPIExecutorConfig,
    selection: AIProviderSelection,
) -> list[AIAPIProviderEntry]:
    by_id = {entry.entry_id: entry for entry in config.entries}
    return [by_id[entry_id] for entry_id in selection.attempt_entry_ids]


def _entry_is_eligible(entry: AIAPIProviderEntry, *, require_json_mode: bool) -> bool:
    if not entry.enabled:
        return False
    if require_json_mode and not entry.supports_json_mode:
        return False
    try:
        entry.resolve_api_key()
    except ValueError:
        return False
    return True
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```powershell
$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\executors\test_ai_api_selector.py -q
```

Expected: PASS with `3 passed`.

- [ ] **Step 5: Local checkpoint**

Record the RED/GREEN evidence in `progress.md` after this task. Do not stage or commit unless the user explicitly asks for git work.

### Task 5: AIAPIExecutor Success Path

**Files:**
- Modify: `src/tokenshare/executors/ai_api.py`
- Create: `tests/executors/test_ai_api_executor_success.py`

- [ ] **Step 1: Write the failing test**

```python
import json

from tests.phase7_fixtures import (
    FakeProviderResponse,
    FakeSiliconFlowTransport,
    make_ai_request,
    make_config_dict,
)
from tokenshare.executors.ai_api import AIAPIExecutor
from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.storage.artifacts import ArtifactStore


def parse_answer(raw_text: str):
    return {"answer": json.loads(raw_text)["answer"]}


def test_ai_api_executor_persists_raw_parsed_provenance_usage_and_cost(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "secret-b")
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store)
    config = load_ai_api_config(make_config_dict())
    transport = FakeSiliconFlowTransport(
        [
            FakeProviderResponse(
                status_code=200,
                body={
                    "id": "sf-response-1",
                    "model": "Qwen/Qwen2.5-7B-Instruct",
                    "choices": [
                        {"message": {"content": "{\"answer\":\"ok\"}"}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                },
            )
        ]
    )
    executor = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=transport,
        parser=parse_answer,
    )

    submission = executor.execute(
        request,
        submission_id="submission_ai_1",
        submitted_at="2026-06-28T00:00:02Z",
    )

    assert submission.result_kind == "succeeded"
    assert submission.raw_output_ref is not None
    assert submission.parsed_output_ref is not None
    assert submission.provenance_ref is not None
    assert submission.candidate_output_refs["answer"] == submission.parsed_output_ref
    assert submission.usage_summary["total_tokens"] == 15
    assert submission.usage_summary["provider_attempt_count"] == 1
    assert submission.usage_summary["cost_estimate_status"] == "estimated"
    assert store.verify(submission.raw_output_ref)
    assert store.verify(submission.parsed_output_ref)
    assert store.verify(submission.provenance_ref)
    assert b"secret-a" not in store.read_bytes(submission.provenance_ref)
    assert b"secret-b" not in store.read_bytes(submission.provenance_ref)
```

- [ ] **Step 2: Run test to verify RED**

Run:

```powershell
$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\executors\test_ai_api_executor_success.py -q
```

Expected: FAIL with `ImportError: cannot import name 'AIAPIExecutor'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/tokenshare/executors/ai_api.py` while keeping the descriptor builder:

```python
import json
from time import perf_counter
from typing import Callable

from tokenshare.core.models import JsonObject
from tokenshare.executors.ai_api_config import AIAPIExecutorConfig
from tokenshare.executors.ai_api_selector import build_provider_selection, entries_by_attempt_order
from tokenshare.executors.ai_api_transport import (
    SiliconFlowProviderError,
    build_siliconflow_chat_body,
    parse_siliconflow_response,
)
from tokenshare.executors.contracts import ExecutionRequest, ExecutionSubmission
from tokenshare.storage.artifacts import ArtifactStore


class AIAPIExecutor:
    def __init__(
        self,
        *,
        executor_id: str,
        executor_version: str,
        artifact_store: ArtifactStore,
        config: AIAPIExecutorConfig,
        transport,
        parser: Callable[[str], JsonObject] | None = None,
    ) -> None:
        self.executor_id = executor_id
        self.executor_version = executor_version
        self._artifact_store = artifact_store
        self._config = config
        self._transport = transport
        self._parser = parser

    def execute(
        self,
        request: ExecutionRequest,
        *,
        submission_id: str,
        submitted_at: str,
    ) -> ExecutionSubmission:
        if request.prompt_package_ref is None:
            raise ValueError("AI API executor requires prompt_package_ref")
        prompt = json.loads(self._artifact_store.read_bytes(request.prompt_package_ref).decode("utf-8"))
        require_json_mode = bool(prompt.get("constraints", {}).get("requires_json_mode"))
        selection = build_provider_selection(
            config=self._config,
            request_id=request.request_id,
            environment_seed=request.environment_ref.seed,
            require_json_mode=require_json_mode,
        )
        attempts: list[JsonObject] = []
        final_result = None
        final_entry = None
        for entry in entries_by_attempt_order(config=self._config, selection=selection):
            started = perf_counter()
            try:
                body = build_siliconflow_chat_body(
                    entry=entry,
                    prompt_text=str(prompt["prompt_text"]),
                    defaults=self._config.defaults,
                    request_limits=request.limits,
                    soft_hints=request.soft_hints or {},
                    require_json_mode=require_json_mode,
                )
                response = self._transport.post_chat_completion(
                    entry=entry,
                    api_key=entry.resolve_api_key(),
                    body=body,
                    timeout_seconds=int(self._config.defaults.get("timeout_seconds", 30)),
                )
                final_result = parse_siliconflow_response(response)
                final_entry = entry
                attempts.append(_attempt_record(entry, "succeeded", perf_counter() - started, response.status_code))
                break
            except TimeoutError:
                attempts.append(_attempt_record(entry, "timeout", perf_counter() - started, None))
            except SiliconFlowProviderError as exc:
                attempts.append(_attempt_record(entry, exc.error_kind, perf_counter() - started, exc.http_status))
                if exc.error_kind in {"invalid_output", "client_error"}:
                    break
        if final_result is None or final_entry is None:
            provenance_ref = self._save_provenance(
                submission_id=submission_id,
                request=request,
                selection=selection.to_dict(),
                attempts=attempts,
                final_entry_id=None,
                final_result_kind="executor_error",
                submitted_at=submitted_at,
            )
            return self._submission(
                request=request,
                submission_id=submission_id,
                submitted_at=submitted_at,
                result_kind="executor_error",
                raw_output_ref=None,
                parsed_output_ref=None,
                candidate_output_refs={},
                parse_failure_ref=None,
                provenance_ref=provenance_ref,
                usage_summary={"provider_attempt_count": len(attempts)},
                error={"kind": "executor_error", "attempts": attempts},
            )
        raw_ref = self._artifact_store.save_json(
            {
                "schema_version": "phase7.raw_model_output.v1",
                "submission_id": submission_id,
                "request_id": request.request_id,
                "provider_family": "siliconflow",
                "entry_id": final_entry.entry_id,
                "model": final_result.model or final_entry.model,
                "provider_response_id": final_result.provider_response_id,
                "content_text": final_result.content_text,
                "raw_response_json": final_result.raw_response_json,
                "finish_reason": final_result.finish_reason,
                "usage": final_result.usage,
                "created_at": submitted_at,
            },
            artifact_id=f"raw_model_output_{submission_id}",
            artifact_type="RawModelOutput",
            artifact_schema_id="phase7.raw_model_output",
            artifact_schema_version="v1",
            source={"kind": "ai_api_executor", "request_id": request.request_id},
            metadata={"executor_id": self.executor_id, "entry_id": final_entry.entry_id},
            created_at=submitted_at,
        )
        parsed_ref = None
        candidate_refs: dict[str, object] = {}
        parse_failure_ref = None
        result_kind = "succeeded"
        if self._parser is not None:
            parsed = self._parser(final_result.content_text)
            parsed_ref = self._artifact_store.save_json(
                parsed,
                artifact_id=f"parsed_model_output_{submission_id}",
                artifact_type="ParsedModelOutput",
                artifact_schema_id="phase7.parsed_model_output",
                artifact_schema_version="v1",
                source={"kind": "ai_api_executor", "raw_output_ref": raw_ref.to_dict()},
                metadata={"executor_id": self.executor_id},
                created_at=submitted_at,
            )
            candidate_refs = {name: parsed_ref for name in request.output_contract.required_outputs}
        usage_summary = _usage_summary(final_entry, final_result.usage, len(attempts))
        provenance_ref = self._save_provenance(
            submission_id=submission_id,
            request=request,
            selection=selection.to_dict(),
            attempts=attempts,
            final_entry_id=final_entry.entry_id,
            final_result_kind=result_kind,
            submitted_at=submitted_at,
        )
        return self._submission(
            request=request,
            submission_id=submission_id,
            submitted_at=submitted_at,
            result_kind=result_kind,
            raw_output_ref=raw_ref,
            parsed_output_ref=parsed_ref,
            candidate_output_refs=candidate_refs,
            parse_failure_ref=parse_failure_ref,
            provenance_ref=provenance_ref,
            usage_summary=usage_summary,
            error=None,
        )

    def _save_provenance(self, *, submission_id, request, selection, attempts, final_entry_id, final_result_kind, submitted_at):
        return self._artifact_store.save_json(
            {
                "schema_version": "phase7.ai_provider_call_provenance.v1",
                "submission_id": submission_id,
                "request_id": request.request_id,
                "config_digest": self._config.config_digest,
                "selection_record": selection,
                "attempts": attempts,
                "final_entry_id": final_entry_id,
                "final_result_kind": final_result_kind,
                "secret_redaction": {"authorization_header": False, "api_key_value": False},
            },
            artifact_id=f"ai_provider_provenance_{submission_id}",
            artifact_type="AIProviderCallProvenance",
            artifact_schema_id="phase7.ai_provider_call_provenance",
            artifact_schema_version="v1",
            source={"kind": "ai_api_executor", "request_id": request.request_id},
            metadata={"executor_id": self.executor_id},
            created_at=submitted_at,
        )

    def _submission(self, *, request, submission_id, submitted_at, result_kind, raw_output_ref, parsed_output_ref, candidate_output_refs, parse_failure_ref, provenance_ref, usage_summary, error):
        return ExecutionSubmission(
            submission_id=submission_id,
            request_id=request.request_id,
            task_id=request.task_id,
            unit_id=request.unit_id,
            attempt_id=request.attempt_id,
            lease_id=request.lease_id,
            fencing_token=request.fencing_token,
            executor_id=self.executor_id,
            executor_version=self.executor_version,
            result_kind=result_kind,
            raw_output_ref=raw_output_ref,
            parsed_output_ref=parsed_output_ref,
            candidate_output_refs=candidate_output_refs,
            parse_failure_ref=parse_failure_ref,
            log_ref=None,
            environment_ref=request.environment_ref,
            environment_summary={
                "runtime": request.environment_ref.runtime,
                "provider_family": "siliconflow",
                "config_digest": self._config.config_digest,
            },
            provenance_ref=provenance_ref,
            usage_summary=usage_summary,
            error=error,
            submitted_at=submitted_at,
        )


def _attempt_record(entry, result_kind: str, elapsed_seconds: float, http_status: int | None) -> JsonObject:
    return {
        "entry_id": entry.entry_id,
        "model": entry.model,
        "result_kind": result_kind,
        "latency_ms": int(elapsed_seconds * 1000),
        "http_status": http_status,
    }


def _usage_summary(entry, usage: JsonObject, attempt_count: int) -> JsonObject:
    prompt_tokens = int(usage.get("prompt_tokens", 0))
    completion_tokens = int(usage.get("completion_tokens", 0))
    input_cost = prompt_tokens / 1_000_000 * float(entry.pricing["input_per_million_tokens"])
    output_cost = completion_tokens / 1_000_000 * float(entry.pricing["output_per_million_tokens"])
    return {
        "provider_family": "siliconflow",
        "entry_id": entry.entry_id,
        "model": entry.model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": int(usage.get("total_tokens", prompt_tokens + completion_tokens)),
        "provider_attempt_count": attempt_count,
        "cost_estimate": input_cost + output_cost,
        "currency": entry.pricing["currency"],
        "pricing_snapshot": dict(entry.pricing),
        "cost_estimate_status": "estimated",
    }
```

- [ ] **Step 4: Run test to verify GREEN**

Run:

```powershell
$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\executors\test_ai_api_executor_success.py -q
```

Expected: PASS with `1 passed`.

- [ ] **Step 5: Local checkpoint**

Record the RED/GREEN evidence in `progress.md` after this task. Do not stage or commit unless the user explicitly asks for git work.

### Task 6: Provider Failover And Final Error

**Files:**
- Modify: `src/tokenshare/executors/ai_api.py`
- Create: `tests/executors/test_ai_api_executor_failover.py`

- [ ] **Step 1: Write failing tests**

```python
from tests.phase7_fixtures import (
    FakeProviderResponse,
    FakeSiliconFlowTransport,
    make_ai_request,
    make_config_dict,
)
from tokenshare.executors.ai_api import AIAPIExecutor
from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.storage.artifacts import ArtifactStore


def test_ai_api_executor_failover_after_rate_limit(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "secret-b")
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store)
    config = load_ai_api_config(make_config_dict())
    transport = FakeSiliconFlowTransport(
        [
            FakeProviderResponse(status_code=429, body={"message": "rate limit"}),
            FakeProviderResponse(
                status_code=200,
                body={
                    "id": "sf-response-2",
                    "model": "deepseek-ai/DeepSeek-V3",
                    "choices": [{"message": {"content": "{\"answer\":\"fallback\"}"}}],
                    "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
                },
            ),
        ]
    )
    executor = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=transport,
        parser=lambda raw: {"answer": "fallback"},
    )

    submission = executor.execute(request, submission_id="submission_failover", submitted_at="2026-06-28T00:00:02Z")

    assert submission.result_kind == "succeeded"
    assert submission.usage_summary["provider_attempt_count"] == 2
    assert len(transport.calls) == 2
    provenance = store.read_bytes(submission.provenance_ref).decode("utf-8")
    assert "rate_limited" in provenance
    assert "secret-a" not in provenance
    assert "secret-b" not in provenance


def test_ai_api_executor_returns_executor_error_after_all_entries_fail(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "secret-b")
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store, request_id="request_all_fail")
    config = load_ai_api_config(make_config_dict())
    transport = FakeSiliconFlowTransport(
        [
            FakeProviderResponse(status_code=503, body={"message": "overloaded"}),
            FakeProviderResponse(status_code=504, body={"message": "timeout"}),
        ]
    )
    executor = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=transport,
    )

    submission = executor.execute(request, submission_id="submission_all_fail", submitted_at="2026-06-28T00:00:02Z")

    assert submission.result_kind == "executor_error"
    assert submission.raw_output_ref is None
    assert submission.provenance_ref is not None
    assert submission.error["kind"] == "executor_error"
    assert submission.usage_summary["provider_attempt_count"] == 2
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\executors\test_ai_api_executor_failover.py -q
```

Expected: FAIL if current implementation does not preserve all attempt records or final error mapping.

- [ ] **Step 3: Write minimal implementation**

Adjust `AIAPIExecutor.execute()` so:

```python
FAILOVER_ERROR_KINDS = {"timeout", "rate_limited", "provider_error", "auth_error"}
NO_FAILOVER_ERROR_KINDS = {"invalid_output", "client_error"}


def _should_continue_failover(error_kind: str) -> bool:
    return error_kind in FAILOVER_ERROR_KINDS
```

In the `except SiliconFlowProviderError` block:

```python
except SiliconFlowProviderError as exc:
    attempts.append(_attempt_record(entry, exc.error_kind, perf_counter() - started, exc.http_status))
    if not _should_continue_failover(exc.error_kind):
        break
```

In the `except TimeoutError` block:

```python
except TimeoutError:
    attempts.append(_attempt_record(entry, "timeout", perf_counter() - started, None))
    continue
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```powershell
$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\executors\test_ai_api_executor_failover.py tests\executors\test_ai_api_executor_success.py -q
```

Expected: PASS with failover and success tests green.

- [ ] **Step 5: Local checkpoint**

Record the RED/GREEN evidence in `progress.md` after this task. Do not stage or commit unless the user explicitly asks for git work.

### Task 7: Plugin Parser Bridge And Raw-Only Mode

**Files:**
- Modify: `src/tokenshare/executors/ai_api.py`
- Create: `tests/executors/test_ai_api_executor_parser.py`

- [ ] **Step 1: Write failing tests**

```python
from tests.phase7_fixtures import (
    FakeProviderResponse,
    FakeSiliconFlowTransport,
    make_ai_request,
    make_config_dict,
)
from tokenshare.executors.ai_api import AIAPIExecutor
from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.storage.artifacts import ArtifactStore


def _success_response(content: str):
    return FakeProviderResponse(
        status_code=200,
        body={
            "id": "sf-response-parser",
            "model": "deepseek-ai/DeepSeek-V3",
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 4, "total_tokens": 8},
        },
    )


def test_parse_failure_does_not_failover_after_provider_success(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "secret-b")
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store)
    config = load_ai_api_config(make_config_dict())
    transport = FakeSiliconFlowTransport([_success_response("not-json")])

    def parser(_raw: str):
        raise ValueError("plugin parser rejected output")

    executor = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=transport,
        parser=parser,
    )

    submission = executor.execute(request, submission_id="submission_parse_fail", submitted_at="2026-06-28T00:00:02Z")

    assert submission.result_kind == "parse_failed"
    assert submission.raw_output_ref is not None
    assert submission.parsed_output_ref is None
    assert submission.parse_failure_ref is not None
    assert len(transport.calls) == 1
    assert b"plugin parser rejected output" in store.read_bytes(submission.parse_failure_ref)


def test_raw_only_mode_returns_succeeded_without_candidate_refs(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "secret-b")
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store)
    config = load_ai_api_config(make_config_dict())
    transport = FakeSiliconFlowTransport([_success_response("plain text answer")])
    executor = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=transport,
        parser=None,
    )

    submission = executor.execute(request, submission_id="submission_raw_only", submitted_at="2026-06-28T00:00:02Z")

    assert submission.result_kind == "succeeded"
    assert submission.raw_output_ref is not None
    assert submission.parsed_output_ref is None
    assert submission.candidate_output_refs == {}
    assert submission.parse_failure_ref is None
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\executors\test_ai_api_executor_parser.py -q
```

Expected: FAIL if parser exceptions currently escape or if raw-only mode incorrectly creates candidate refs.

- [ ] **Step 3: Write minimal implementation**

In `AIAPIExecutor.execute()`, wrap parser call:

```python
        if self._parser is not None:
            try:
                parsed = self._parser(final_result.content_text)
            except Exception as exc:
                parse_failure_ref = self._artifact_store.save_json(
                    {
                        "schema_version": "phase7.parse_failure_report.v1",
                        "submission_id": submission_id,
                        "request_id": request.request_id,
                        "raw_output_ref": raw_ref.to_dict(),
                        "reason": "plugin_parser_rejected_output",
                        "message": str(exc),
                    },
                    artifact_id=f"parse_failure_{submission_id}",
                    artifact_type="ParseFailureReport",
                    artifact_schema_id="phase7.parse_failure_report",
                    artifact_schema_version="v1",
                    source={"kind": "ai_api_executor", "raw_output_ref": raw_ref.to_dict()},
                    metadata={"executor_id": self.executor_id},
                    created_at=submitted_at,
                )
                provenance_ref = self._save_provenance(
                    submission_id=submission_id,
                    request=request,
                    selection=selection.to_dict(),
                    attempts=attempts,
                    final_entry_id=final_entry.entry_id,
                    final_result_kind="parse_failed",
                    submitted_at=submitted_at,
                )
                return self._submission(
                    request=request,
                    submission_id=submission_id,
                    submitted_at=submitted_at,
                    result_kind="parse_failed",
                    raw_output_ref=raw_ref,
                    parsed_output_ref=None,
                    candidate_output_refs={},
                    parse_failure_ref=parse_failure_ref,
                    provenance_ref=provenance_ref,
                    usage_summary=_usage_summary(final_entry, final_result.usage, len(attempts)),
                    error={"kind": "parse_failed", "reason": "plugin_parser_rejected_output"},
                )
```

Keep raw-only mode as `result_kind="succeeded"` with empty `candidate_output_refs`.

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```powershell
$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\executors\test_ai_api_executor_parser.py tests\executors\test_ai_api_executor_success.py tests\executors\test_ai_api_executor_failover.py -q
```

Expected: PASS with parser, success, and failover suites green.

- [ ] **Step 5: Local checkpoint**

Record the RED/GREEN evidence in `progress.md` after this task. Do not stage or commit unless the user explicitly asks for git work.

### Task 8: Secret Redaction And Artifact Scan Regression

**Files:**
- Modify: `src/tokenshare/executors/ai_api.py`
- Modify: `src/tokenshare/executors/ai_api_transport.py`
- Create or extend: `tests/executors/test_ai_api_executor_success.py`

- [ ] **Step 1: Add a failing redaction regression**

```python
def test_ai_api_executor_never_persists_api_key_values(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "super-secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "super-secret-b")
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store, request_id="request_secret_scan")
    config = load_ai_api_config(make_config_dict())
    transport = FakeSiliconFlowTransport(
        [
            FakeProviderResponse(
                status_code=200,
                body={
                    "id": "sf-secret-scan",
                    "model": "Qwen/Qwen2.5-7B-Instruct",
                    "choices": [{"message": {"content": "{\"answer\":\"safe\"}"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            )
        ]
    )
    executor = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=transport,
        parser=lambda raw: {"answer": "safe"},
    )

    executor.execute(request, submission_id="submission_secret_scan", submitted_at="2026-06-28T00:00:02Z")

    for path in store.artifact_dir.glob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert b"super-secret-a" not in data
            assert b"super-secret-b" not in data
```

- [ ] **Step 2: Run test to verify RED or GREEN with explicit evidence**

Run:

```powershell
$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\executors\test_ai_api_executor_success.py::test_ai_api_executor_never_persists_api_key_values -q
```

Expected: If the test passes immediately, inspect implementation and confirm no API key value is serialized. If it fails, proceed to Step 3.

- [ ] **Step 3: Implement redaction if needed**

Ensure only `api_key_seen: True` or env var names appear in fake transport call records. Never store `api_key` in provenance:

```python
"secret_redaction": {
    "authorization_header": False,
    "api_key_value": False,
    "api_key_env_names": [entry.api_key_env for entry in self._config.entries],
}
```

Do not include `api_key` in any attempt record or error payload.

- [ ] **Step 4: Run redaction and executor suite**

Run:

```powershell
$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\executors\test_ai_api_executor_success.py tests\executors\test_ai_api_executor_failover.py tests\executors\test_ai_api_executor_parser.py -q
```

Expected: PASS.

- [ ] **Step 5: Local checkpoint**

Record the RED/GREEN evidence in `progress.md` after this task. Do not stage or commit unless the user explicitly asks for git work.

### Task 9: Replay No-Call Guard

**Files:**
- Create: `src/tokenshare/executors/ai_api_replay.py`
- Create: `tests/executors/test_ai_api_replay_guard.py`

- [ ] **Step 1: Write failing tests**

```python
import pytest

from tests.phase7_fixtures import (
    FakeProviderResponse,
    FakeSiliconFlowTransport,
    make_ai_request,
    make_config_dict,
)
from tokenshare.executors.ai_api import AIAPIExecutor
from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.executors.ai_api_replay import verify_ai_api_submission_artifacts
from tokenshare.storage.artifacts import ArtifactStore


def test_replay_guard_verifies_artifacts_without_calling_transport(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "secret-b")
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store)
    config = load_ai_api_config(make_config_dict())
    transport = FakeSiliconFlowTransport(
        [
            FakeProviderResponse(
                status_code=200,
                body={
                    "id": "sf-replay",
                    "model": "Qwen/Qwen2.5-7B-Instruct",
                    "choices": [{"message": {"content": "{\"answer\":\"ok\"}"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            )
        ]
    )
    executor = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=transport,
        parser=lambda raw: {"answer": "ok"},
    )
    submission = executor.execute(request, submission_id="submission_replay", submitted_at="2026-06-28T00:00:02Z")
    transport.calls.clear()

    assert verify_ai_api_submission_artifacts(store, submission) is True
    assert transport.calls == []


def test_replay_guard_fails_on_missing_raw_artifact(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "secret-b")
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store)
    config = load_ai_api_config(make_config_dict())
    transport = FakeSiliconFlowTransport(
        [
            FakeProviderResponse(
                status_code=200,
                body={
                    "id": "sf-replay-missing",
                    "model": "Qwen/Qwen2.5-7B-Instruct",
                    "choices": [{"message": {"content": "{\"answer\":\"ok\"}"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            )
        ]
    )
    executor = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=transport,
        parser=lambda raw: {"answer": "ok"},
    )
    submission = executor.execute(request, submission_id="submission_replay_missing", submitted_at="2026-06-28T00:00:02Z")
    (store.root_path / submission.raw_output_ref.uri).unlink()

    with pytest.raises(FileNotFoundError, match="missing AI API artifact"):
        verify_ai_api_submission_artifacts(store, submission)
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\executors\test_ai_api_replay_guard.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'tokenshare.executors.ai_api_replay'`.

- [ ] **Step 3: Write minimal implementation**

```python
"""Replay guard helpers for Phase 7 AI API artifacts."""

from __future__ import annotations

from tokenshare.core.models import ArtifactRef
from tokenshare.executors.contracts import ExecutionSubmission
from tokenshare.storage.artifacts import ArtifactStore


def verify_ai_api_submission_artifacts(
    artifact_store: ArtifactStore,
    submission: ExecutionSubmission,
) -> bool:
    required_refs: list[ArtifactRef] = []
    if submission.raw_output_ref is not None:
        required_refs.append(submission.raw_output_ref)
    if submission.parsed_output_ref is not None:
        required_refs.append(submission.parsed_output_ref)
    if submission.parse_failure_ref is not None:
        required_refs.append(submission.parse_failure_ref)
    if submission.provenance_ref is not None:
        required_refs.append(submission.provenance_ref)
    if submission.result_kind in {"succeeded", "parse_failed"} and submission.raw_output_ref is None:
        raise FileNotFoundError("missing AI API artifact: raw_output_ref")
    if submission.provenance_ref is None:
        raise FileNotFoundError("missing AI API artifact: provenance_ref")
    for artifact_ref in required_refs:
        if not artifact_store.verify(artifact_ref):
            raise FileNotFoundError(f"missing AI API artifact: {artifact_ref.artifact_id}")
    return True
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```powershell
$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\executors\test_ai_api_replay_guard.py -q
```

Expected: PASS with `2 passed`.

- [ ] **Step 5: Local checkpoint**

Record the RED/GREEN evidence in `progress.md` after this task. Do not stage or commit unless the user explicitly asks for git work.

### Task 10: Package Exports And Executor Suite

**Files:**
- Modify: `src/tokenshare/executors/__init__.py`
- Test: all `tests/executors/test_ai_api_*.py`

- [ ] **Step 1: Write failing package export test**

Add to `tests/executors/test_ai_api_descriptor.py`:

```python
def test_ai_api_public_exports_are_available() -> None:
    from tokenshare.executors.ai_api import AIAPIExecutor, build_ai_api_executor_descriptor
    from tokenshare.executors.ai_api_config import load_ai_api_config

    assert AIAPIExecutor.__name__ == "AIAPIExecutor"
    assert build_ai_api_executor_descriptor().executor_type == "ai_api"
    assert callable(load_ai_api_config)
```

- [ ] **Step 2: Run all Phase 7 executor tests**

Run:

```powershell
$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\executors\test_ai_api_config.py tests\executors\test_ai_api_descriptor.py tests\executors\test_ai_api_transport.py tests\executors\test_ai_api_selector.py tests\executors\test_ai_api_executor_success.py tests\executors\test_ai_api_executor_failover.py tests\executors\test_ai_api_executor_parser.py tests\executors\test_ai_api_replay_guard.py -q
```

Expected: FAIL only if exports or integration assumptions are missing.

- [ ] **Step 3: Add package-level exports if needed**

If `src/tokenshare/executors/__init__.py` remains intentionally empty, leave it empty. If the project starts exporting executor public APIs, use:

```python
"""Executor contracts and local executor implementations."""

from tokenshare.executors.ai_api import AIAPIExecutor, build_ai_api_executor_descriptor
from tokenshare.executors.ai_api_config import load_ai_api_config

__all__ = [
    "AIAPIExecutor",
    "build_ai_api_executor_descriptor",
    "load_ai_api_config",
]
```

- [ ] **Step 4: Run executor suite and startup verification**

Run:

```powershell
$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\executors -q
powershell -ExecutionPolicy Bypass -File .\init.ps1
```

Expected: all executor tests pass, then full startup verification passes.

- [ ] **Step 5: Local checkpoint**

Record the RED/GREEN evidence in `progress.md` after this task. Do not stage or commit unless the user explicitly asks for git work.

### Task 11: Optional Real SiliconFlow Smoke Test Gate

**Files:**
- Create: `tests/executors/test_ai_api_siliconflow_smoke.py`
- Modify: `src/tokenshare/executors/ai_api_transport.py`

- [ ] **Step 1: Write skipped-by-default smoke test**

```python
import os

import pytest

from tests.phase7_fixtures import make_ai_request, make_config_dict
from tokenshare.executors.ai_api import AIAPIExecutor
from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.executors.ai_api_transport import UrlLibSiliconFlowTransport
from tokenshare.storage.artifacts import ArtifactStore


@pytest.mark.skipif(
    os.environ.get("TOKENSHARE_RUN_SILICONFLOW_SMOKE") != "1",
    reason="real SiliconFlow smoke test is opt-in",
)
def test_real_siliconflow_smoke_returns_artifact_backed_submission(tmp_path) -> None:
    if not os.environ.get("SILICONFLOW_API_KEY"):
        pytest.skip("SILICONFLOW_API_KEY is not set")
    body = make_config_dict()
    body["entries"] = [body["entries"][0]]
    body["entries"][0]["api_key_env"] = "SILICONFLOW_API_KEY"
    body["defaults"]["max_tokens"] = 32
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store, request_id="request_real_smoke")
    config = load_ai_api_config(body)
    executor = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=UrlLibSiliconFlowTransport(),
        parser=None,
    )

    submission = executor.execute(
        request,
        submission_id="submission_real_smoke",
        submitted_at="2026-06-28T00:00:02Z",
    )

    assert submission.raw_output_ref is not None or submission.provenance_ref is not None
    assert store.verify(submission.provenance_ref)
```

- [ ] **Step 2: Run smoke test without env to verify SKIP**

Run:

```powershell
$env:PYTHONPATH='src'; Remove-Item Env:\TOKENSHARE_RUN_SILICONFLOW_SMOKE -ErrorAction SilentlyContinue; conda run -n tokenshare python -m pytest tests\executors\test_ai_api_siliconflow_smoke.py -q
```

Expected: SKIPPED, not failed.

- [ ] **Step 3: Implement stdlib HTTP transport**

Add to `src/tokenshare/executors/ai_api_transport.py`:

```python
import json
import urllib.error
import urllib.request


class UrlLibSiliconFlowTransport:
    def post_chat_completion(self, *, entry, api_key: str, body: JsonObject, timeout_seconds: int):
        url = f"{entry.base_url}{entry.endpoint}"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                text = response.read().decode("utf-8")
                return _UrlLibResponse(response.status, json.loads(text), text)
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            try:
                body_json = json.loads(text)
            except json.JSONDecodeError:
                body_json = {"message": text}
            return _UrlLibResponse(exc.code, body_json, text)


class _UrlLibResponse:
    def __init__(self, status_code: int, body: JsonObject, text: str) -> None:
        self.status_code = status_code
        self.body = body
        self.text = text
```

- [ ] **Step 4: Run smoke skip and full executor tests**

Run:

```powershell
$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\executors\test_ai_api_siliconflow_smoke.py tests\executors -q
```

Expected: smoke skipped unless env explicitly set; all non-network executor tests pass.

- [ ] **Step 5: Local checkpoint**

Record the RED/GREEN evidence in `progress.md` after this task. Do not stage or commit unless the user explicitly asks for git work.

### Task 12: Code Map And Status Sync

**Files:**
- Create: `Doc/TechnicalDocument/2026-06-28-phase-7-ai-api-executor-code-map.md`
- Modify: `Doc/agent-navigation.md`
- Modify: `feature_list.json`
- Modify: `progress.md`
- Modify: `session-handoff.md`

- [ ] **Step 1: Write code map**

Create `Doc/TechnicalDocument/2026-06-28-phase-7-ai-api-executor-code-map.md`:

```markdown
# Phase 7 AI API Executor 代码映射

日期：2026-06-28

状态：Phase 7 Experimental AI API Executor 已实现并完成验证。本文映射 SiliconFlow-only 第一版 executor 的 source、tests、字段规格章节、验证证据和协议边界。

## 1. Source Map

| 文件 | 规格章节 | 当前内容 |
|---|---|---|
| `src/tokenshare/executors/ai_api_config.py` | 第 6 节 | 本地 config dataclasses、schema validation、safe digest、secret env lookup。 |
| `src/tokenshare/executors/ai_api_transport.py` | 第 8 节、第 12 节 | SiliconFlow chat completions request/response boundary、HTTP status error mapping、opt-in stdlib transport。 |
| `src/tokenshare/executors/ai_api_selector.py` | 第 7 节 | Eligible filtering、seeded uniform random selection、bounded failover order。 |
| `src/tokenshare/executors/ai_api.py` | 第 4 节、第 9-13 节 | Descriptor builder、AIAPIExecutor orchestration、raw/parsed/parse failure/provenance/usage artifact persistence。 |
| `src/tokenshare/executors/ai_api_replay.py` | 第 14 节 | Replay guard helper that verifies historical artifacts without calling transport。 |

## 2. Test Map

| 测试文件 | 覆盖内容 |
|---|---|
| `tests/executors/test_ai_api_config.py` | Config validation、secret boundary、digest、duplicate entry rejection。 |
| `tests/executors/test_ai_api_descriptor.py` | ExecutorDescriptor builder and registry matching。 |
| `tests/executors/test_ai_api_transport.py` | SiliconFlow body construction and response/error mapping。 |
| `tests/executors/test_ai_api_selector.py` | Eligible filtering、seeded selection、JSON mode filtering。 |
| `tests/executors/test_ai_api_executor_success.py` | Success path, artifact persistence, usage/cost, redaction。 |
| `tests/executors/test_ai_api_executor_failover.py` | 429/503/504/timeout request-scoped provider failover and final executor error。 |
| `tests/executors/test_ai_api_executor_parser.py` | Plugin parser success, parse failure, raw-only mode。 |
| `tests/executors/test_ai_api_replay_guard.py` | Replay no-call artifact checks and missing artifact failure。 |
| `tests/executors/test_ai_api_siliconflow_smoke.py` | Opt-in real SiliconFlow smoke gate; skipped by default。 |

## 3. Boundary Notes

- API key values are read only from environment variables and are not persisted.
- Provider failover is request-scoped and does not create new protocol attempts.
- Plugin parsing remains plugin-owned; executor does not embed task-domain rules.
- Replay reads historical artifacts and never calls SiliconFlow.

## 4. Verification Evidence

- Phase 7 targeted executor suite: record exact pytest output here.
- `git diff --check`: record exact output summary here.
- Final `.\init.ps1`: record collected item count and pass result here.
```

- [ ] **Step 2: Update indexes and feature state**

Update:

```text
Doc/agent-navigation.md
feature_list.json
progress.md
session-handoff.md
```

Rules:

- Add the code map to `feature_list.json.source_documents`.
- Keep `feat-008.status` as `in-progress` until all tests and final startup verification pass.
- Mark `feat-008.status` as `done` only after all done criteria are satisfied and evidence is written.
- Do not mark `feat-009` active until `feat-008` is done.

- [ ] **Step 3: Run metadata checks**

Run:

```powershell
conda run -n tokenshare python -c "import json; from pathlib import Path; json.loads(Path('feature_list.json').read_text(encoding='utf-8')); print('feature-list-json-ok')"
git diff --check
```

Expected: `feature-list-json-ok`; `git diff --check` exits 0 except possible LF/CRLF warnings.

- [ ] **Step 4: Run final verification**

Run:

```powershell
powershell -ExecutionPolicy Bypass -File .\init.ps1
```

Expected: `python-json-sqlite-ok`, `harness-files-ok`, and all pytest tests pass.

- [ ] **Step 5: Local checkpoint**

Record final verification evidence in `progress.md`, `feature_list.json`, and `session-handoff.md`. Do not stage or commit unless the user explicitly asks for git work.

## 5. Self-Review Checklist

- Spec coverage: Tasks 1-12 cover config, descriptor, transport, selection, failover, raw/parsed/parse failure/provenance/usage/cost artifacts, secret redaction, replay no-call, smoke gate, code map, and status sync.
- Placeholder scan: This plan intentionally avoids unresolved placeholder tokens and uses concrete file paths, function names, tests, and commands.
- Type consistency: `AIAPIExecutorConfig`, `AIAPIProviderEntry`, `AIProviderSelection`, `AIAPIExecutor`, `SiliconFlowProviderError`, and `verify_ai_api_submission_artifacts()` are introduced before later tasks use them.
- TDD order: Every production behavior starts with a failing test and an expected failure signal before implementation.
- Boundary check: No task modifies task graph, canonical binding, reward, settlement, Phase 8 experiment runner, or production-grade provider management.

## 6. Execution Handoff

Plan complete when this document is saved. Recommended execution mode:

1. Subagent-driven execution for Tasks 1-4, review.
2. Subagent-driven execution for Tasks 5-9, review.
3. Inline execution for Tasks 10-12 metadata and final verification.

The implementing agent must use `superpowers:test-driven-development` before writing production code and must keep proof of RED and GREEN runs in `progress.md` / `feature_list.json`.
