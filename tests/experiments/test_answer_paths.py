from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import re

import pytest

from tokenshare.experiments.provider import (
    RESPONSE_MAX_BYTES,
    ProviderCallContextV1,
    call_provider_once,
    project_cost,
)
from tokenshare.experiments.schema import (
    LEGACY_PRICING_VERSION,
    PRICING_VERSION,
    ProviderEntryViewV1,
    ProviderRequestControlV1,
)
from tokenshare.experiments.storage import RunStore, StorageConflictError, scan_resume


class _FakeResponse:
    def __init__(self, body: bytes, *, status: int = 200) -> None:
        self._body = body
        self.status = status
        self.read_limits: list[int] = []
        self.closed = False

    def read(self, limit: int) -> bytes:
        self.read_limits.append(limit)
        return self._body[:limit]

    def close(self) -> None:
        self.closed = True


def _entry(
    *,
    family: str = "siliconflow",
    model: str = "Qwen/Qwen3-14B",
    timeout_seconds: float = 600.0,
    max_tokens: int = 300_000,
) -> ProviderEntryViewV1:
    overrides = {"enable_thinking": False}
    if family == "deepseek":
        overrides = {"thinking": {"type": "enabled"}, "reasoning_effort": "high"}
    entry = ProviderEntryViewV1(
        provider_family=family,
        entry_id=f"{family}-entry",
        base_url="https://provider.invalid",
        endpoint="/chat/completions",
        api_key_env="EXPERIMENTS_TEST_KEY",
        configured_model=model,
        request_overrides=overrides,
        supports_json_mode=True,
        timeout_seconds=timeout_seconds,
        max_tokens=max_tokens,
    )
    entry.validate()
    return entry


def _context(call_key: str = "call-1") -> ProviderCallContextV1:
    return ProviderCallContextV1(
        call_key=call_key,
        root_key=("exp1", "condition", "case", 0),
        planned_ai_unit_id="unit-0",
        attempt_ordinal=0,
    )


def _control() -> ProviderRequestControlV1:
    return ProviderRequestControlV1(
        timeout_seconds=5.0,
        max_tokens=128,
        require_json_mode=True,
    )


def _response_body(*, content: object = '{"answer":1}', model: str = "Qwen/Qwen3-14B") -> bytes:
    return json.dumps(
        {
            "id": "response-1",
            "model": model,
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 11,
                "completion_tokens": 7,
                "total_tokens": 18,
                "completion_tokens_details": {"reasoning_tokens": 3},
            },
        }
    ).encode("utf-8")


def test_provider_caller_is_single_bounded_closed_and_secret_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments import provider

    monkeypatch.setenv("EXPERIMENTS_TEST_KEY", "never-persist-this-secret")
    response = _FakeResponse(_response_body())
    opened: list[object] = []

    def fake_open(request: object, timeout_seconds: float) -> _FakeResponse:
        opened.append(request)
        assert timeout_seconds == 5.0
        return response

    monkeypatch.setattr(provider, "_open_response", fake_open)
    store = RunStore(tmp_path / "run")
    result = call_provider_once(_entry(), "prompt", _control(), _context(), store)

    assert result.ok is True
    assert result.content_text == '{"answer":1}'
    assert result.reasoning_tokens == 3
    assert len(opened) == 1
    assert response.read_limits == [RESPONSE_MAX_BYTES + 1]
    assert response.closed is True
    resume = scan_resume(store.run_dir)
    assert resume.terminal_call_keys == frozenset({"call-1"})
    assert resume.nonterminal_call_keys == frozenset()
    assert "never-persist-this-secret" not in "".join(
        path.read_text(encoding="utf-8") for path in store.run_dir.rglob("*.json")
    )


def test_provider_oversize_closes_and_never_parses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments import provider

    monkeypatch.setenv("EXPERIMENTS_TEST_KEY", "secret")
    response = _FakeResponse(b"x" * (RESPONSE_MAX_BYTES + 1))
    monkeypatch.setattr(provider, "_open_response", lambda request, timeout: response)
    monkeypatch.setattr(
        provider,
        "parse_siliconflow_response",
        lambda response: pytest.fail("oversize response entered envelope parser"),
    )

    result = call_provider_once(
        _entry(), "prompt", _control(), _context("oversize"), RunStore(tmp_path / "run")
    )

    assert result.ok is False
    assert result.error_kind == "provider_response_too_large"
    assert response.closed is True


@pytest.mark.parametrize("content", [None, 9, {"not": "text"}])
def test_provider_rejects_non_string_content_without_stringifying(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content: object,
) -> None:
    from tokenshare.experiments import provider

    monkeypatch.setenv("EXPERIMENTS_TEST_KEY", "secret")
    response = _FakeResponse(_response_body(content=content))
    monkeypatch.setattr(provider, "_open_response", lambda request, timeout: response)

    result = call_provider_once(
        _entry(), "prompt", _control(), _context("bad-content"), RunStore(tmp_path / "run")
    )

    assert result.ok is False
    assert result.error_kind == "provider_envelope_invalid"
    assert result.content_text is None
    assert response.closed is True


def test_existing_intent_is_fail_stop_before_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments import provider

    monkeypatch.setenv("EXPERIMENTS_TEST_KEY", "secret")
    store = RunStore(tmp_path / "run")
    store.write_call_intent("conflict", {"different": "journal"})
    monkeypatch.setattr(
        provider,
        "_open_response",
        lambda request, timeout: pytest.fail("journal conflict reached transport"),
    )

    with pytest.raises(StorageConflictError):
        call_provider_once(_entry(), "prompt", _control(), _context("conflict"), store)


@pytest.mark.parametrize(
    ("body", "status", "expected_kind"),
    [
        (_response_body(model="different-model"), 200, "provider_model_mismatch"),
        (json.dumps({"error": {"message": "busy"}}).encode("utf-8"), 429, "rate_limited"),
    ],
)
def test_model_mismatch_and_http_failure_are_terminal_attempt_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
    status: int,
    expected_kind: str,
) -> None:
    from tokenshare.experiments import provider

    monkeypatch.setenv("EXPERIMENTS_TEST_KEY", "secret")
    response = _FakeResponse(body, status=status)
    monkeypatch.setattr(provider, "_open_response", lambda request, timeout: response)
    call_key = f"terminal-{status}"

    result = call_provider_once(
        _entry(), "prompt", _control(), _context(call_key), RunStore(tmp_path / "run")
    )

    assert result.ok is False
    assert result.error_kind == expected_kind
    assert response.closed is True
    assert scan_resume(tmp_path / "run").terminal_call_keys == frozenset({call_key})


def test_provider_configuration_invalid_is_condition_fail_stop_before_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.executors.contracts import EnvironmentRef, ExecutionRequest
    from tokenshare.experiments import provider
    from tokenshare.experiments.execution import (
        ProviderConditionError,
        ProviderSubmissionAdapter,
    )
    from tokenshare.plugins.contracts import OutputContract
    from tokenshare.storage.artifacts import ArtifactStore

    monkeypatch.delenv("EXPERIMENTS_TEST_KEY", raising=False)
    transport_calls: list[object] = []

    def transport_spy(request: object, timeout: float) -> _FakeResponse:
        transport_calls.append((request, timeout))
        raise AssertionError("invalid provider configuration reached transport")

    monkeypatch.setattr(provider, "_open_response", transport_spy)
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    prompt_ref = artifact_store.save_json(
        {"prompt_text": "return JSON", "constraints": {"requires_json_mode": True}},
        artifact_id="configuration_invalid_prompt",
        artifact_type="PromptPackage",
        artifact_schema_id="phase3.prompt_package",
        artifact_schema_version="v1",
        source={"kind": "experiments_test"},
        metadata={},
        created_at="2026-08-21T00:00:00Z",
    )
    request = ExecutionRequest(
        request_id="configuration-invalid-request",
        task_id="configuration-invalid-task",
        unit_id="configuration-invalid-unit",
        attempt_id="configuration-invalid-attempt",
        lease_id="lease",
        fencing_token="fence",
        plugin={},
        executor={},
        registry_snapshot_id="registry",
        allocation_decision={},
        capability_snapshot={},
        task_unit_snapshot={},
        input_artifact_refs={},
        output_contract=OutputContract("output", [], {}, {}),
        hard_requirements={},
        soft_hints={"planned_ai_unit_id": "unit-0"},
        environment_ref=EnvironmentRef(
            "environment", "digest", "python", {}, {}, "fixture", 0,
            "fixed", "2026-08-21T00:00:00Z",
        ),
        execution_instruction_ref=None,
        prompt_package_ref=prompt_ref,
        limits={},
        created_at="2026-08-21T00:00:00Z",
    )
    store = RunStore(tmp_path / "ordinary")
    adapter = ProviderSubmissionAdapter(
        entry=_entry(),
        artifact_store=artifact_store,
        run_store=store,
        root_key=("exp1", "configuration_invalid", "case", 0),
        domain="factorization",
    )

    with pytest.raises(ProviderConditionError, match="provider_configuration_invalid"):
        adapter.execute(
            request,
            submission_id="configuration-invalid-submission",
            submitted_at="2026-08-21T00:00:00Z",
        )

    call_key = "exp1:configuration_invalid:case:0:unit-0:0"
    terminal = json.loads(store.call_terminal_path(call_key).read_text(encoding="utf-8"))
    assert store.call_intent_path(call_key).is_file()
    assert terminal["error_kind"] == "provider_configuration_invalid"
    assert terminal["provider_call_made"] is False
    assert transport_calls == []
    assert adapter.attempts == []


def test_started_exp1_configuration_failure_projects_incorrect_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments import provider
    from tokenshare.experiments.execution import ProviderSubmissionAdapter
    from tokenshare.experiments.projector import project_root_result
    from tokenshare.experiments.runtime import RootAssembly, run_root_slice
    from tokenshare.local_runtime import SequentialWorkerBackend
    from tokenshare.plugins.factorization.runtime_adapter import (
        FactorizationExecutionBridge,
        FactorizationRuntimeAdapter,
    )
    from tokenshare.storage.artifacts import ArtifactStore
    from tokenshare.storage.events import EventLedger
    from tests.experiments.test_system_vertical import (
        NOW,
        _Clock,
        _ObservationClock,
        _config,
        _inventory,
    )

    monkeypatch.delenv("EXPERIMENTS_TEST_KEY", raising=False)
    transport_calls: list[object] = []

    def transport_spy(request: object, timeout: float) -> _FakeResponse:
        transport_calls.append((request, timeout))
        raise AssertionError("invalid provider configuration reached transport")

    monkeypatch.setattr(provider, "_open_response", transport_spy)
    case = _answer_path_factor_case()
    model = "deepseek-v4-pro"
    config = _config("started_configuration_failure")
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    store = RunStore(tmp_path / "ordinary")
    root_key = ("exp1", "started_configuration_failure", str(case["case_id"]), 0)
    adapter = ProviderSubmissionAdapter(
        entry=_entry(family="deepseek", model=model),
        artifact_store=artifact_store,
        run_store=store,
        root_key=root_key,
        domain="factorization",
    )
    runtime_adapter = FactorizationRuntimeAdapter(
        provider_family="deepseek",
        seed=7,
        protocol_config=config,
        created_at=NOW,
    )
    clock = _Clock()
    assembly = RootAssembly(
        run_id="started-configuration-failure",
        root_input=case,
        protocol_config=config,
        artifact_store=artifact_store,
        event_ledger=EventLedger(tmp_path / "events.jsonl"),
        plugin_runtime=runtime_adapter,
        worker_backend=SequentialWorkerBackend(
            executor=FactorizationExecutionBridge(
                plugin_runtime=runtime_adapter,
                range_executor=adapter,
            ),
            submitted_at=clock,
        ),
        now=clock,
        observation_clock=_ObservationClock(),
        submission_adapter=adapter,
    )

    protocol_result = run_root_slice(assembly)
    inventory = replace(
        _inventory(case=case, domain="factorization"),
        condition_id=root_key[1],
        provider_entry_id="deepseek-entry",
        configured_model=model,
    )
    projected = project_root_result(
        inventory=inventory,
        assembly=assembly,
        protocol_result=protocol_result,
        provider_family="deepseek",
        requested_model=model,
        resolved_model=None,
        reasoning_mode="thinking",
        attempts=adapter.attempts,
    )

    assert protocol_result.status == "failed"
    assert protocol_result.summary["experiments_condition_failure"] == {
        "failure_stage": "provider_call",
        "failure_kind": "provider_configuration_invalid",
    }
    observation = protocol_result.summary["runtime_observation"]
    assert observation["planned_ai_unit_ids"] == ["range_0", "range_1", "range_2"]
    assert observation["dispatched_ai_unit_ids"] == ["range_0", "range_1", "range_2"]
    assert observation["completed_ai_unit_ids"] == []
    assert observation["unscheduled_ai_unit_ids"] == []
    assert sum(
        fact["result_kind"] == "executor_error"
        for fact in observation["worker_execution_facts"]
    ) == 12
    assert (
        projected.protocol_started,
        projected.root_status,
        projected.final_result_present,
        projected.verified_correct,
        projected.failure_stage,
        projected.failure_kind,
        projected.failure_origin,
    ) == (
        True,
        "failed",
        False,
        False,
        "provider_call",
        "infrastructure_invalid",
        "provider_configuration_invalid",
    )
    assert adapter.attempts == []
    assert len(adapter.outcomes) == 1
    assert len(scan_resume(store.run_dir).terminal_call_keys) == 1
    assert transport_calls == []

    runtime_protocol_result = replace(
        protocol_result,
        summary={
            **{
                key: value
                for key, value in protocol_result.summary.items()
                if key not in {"experiments_condition_failure", "terminal_failure"}
            },
            "experiments_runtime_failure": {
                "failure_stage": "protocol_runtime",
                "failure_kind": "infrastructure_invalid",
                "error_kind": "UnexpectedRuntimeFailure",
                "engine_root_status": "failed",
            },
        },
    )
    runtime_projected = project_root_result(
        inventory=inventory,
        assembly=assembly,
        protocol_result=runtime_protocol_result,
        provider_family="deepseek",
        requested_model=model,
        resolved_model=None,
        reasoning_mode="thinking",
        attempts=adapter.attempts,
    )
    assert runtime_projected.failure_kind == "infrastructure_invalid"
    assert runtime_projected.failure_origin == "unexpected_runtime_error"
    assert runtime_protocol_result.summary["experiments_runtime_failure"]["error_kind"] == (
        "UnexpectedRuntimeFailure"
    )


@pytest.mark.parametrize(
    (
        "family", "model", "started", "hit", "miss", "prompt",
        "expected_version", "expected_tier", "expected",
    ),
    [
        ("deepseek", "deepseek-v4-flash", "2026-08-21T09:30:00+08:00", 10, 20, 30, PRICING_VERSION, "flat", 0.0000755),
        ("deepseek", "deepseek-v4-flash", None, 10, 20, 30, PRICING_VERSION, "flat", 0.0000755),
        ("deepseek", "deepseek-v4-pro", "2026-08-21T09:30:00+08:00", 10, 20, 30, LEGACY_PRICING_VERSION, "peak", 0.000453),
        ("deepseek", "deepseek-v4-pro", "2026-08-21T13:00:00+08:00", None, None, 30, LEGACY_PRICING_VERSION, "off_peak", None),
        ("siliconflow", "Qwen/Qwen3-14B", "2026-08-21T01:00:00Z", None, None, 30, LEGACY_PRICING_VERSION, "flat", 0.000035),
    ],
)
def test_project_cost_frozen_authority(
    family: str,
    model: str,
    started: str,
    hit: int | None,
    miss: int | None,
    prompt: int,
    expected_version: str,
    expected_tier: str,
    expected: float | None,
) -> None:
    projection = project_cost(
        provider_family=family,
        configured_model=model,
        provider_request_started_at_utc=started,
        prompt_tokens=prompt,
        prompt_cache_hit_tokens=hit,
        prompt_cache_miss_tokens=miss,
        completion_tokens=10,
    )

    assert projection.pricing_version == expected_version
    assert projection.pricing_tier == expected_tier
    assert projection.cost_estimate_cny == pytest.approx(expected) if expected is not None else projection.cost_estimate_cny is None


class _PromptResponseFactory:
    def __init__(self, model: str) -> None:
        self.model = model
        self.responses: list[_FakeResponse] = []
        self.request_bodies: list[dict[str, object]] = []
        self.timeout_seconds: list[float] = []

    def __call__(self, request: object, timeout_seconds: float) -> _FakeResponse:
        request_body = json.loads(request.data.decode("utf-8"))
        self.request_bodies.append(request_body)
        self.timeout_seconds.append(timeout_seconds)
        prompt = request_body["messages"][-1]["content"]
        if "IMMUTABLE TASK AND RESPONSE SKELETON" in prompt:
            match = re.search(
                r"IMMUTABLE TASK AND RESPONSE SKELETON:\n(\{[^\n]+\})",
                prompt,
            )
            assert match is not None
            bound = json.loads(match.group(1))
            start = int(bound["range_start"])
            end = int(bound["range_end"])
            target = int(bound["target_n"])
            divisor = next((value for value in range(start, end + 1) if target % value == 0), None)
            content = {
                **bound,
                "range_result_id": f"range_result:{bound['coverage_id']}:{bound['child_index']}",
                "result_kind": "found_factor" if divisor is not None else "no_factor_in_range",
                "found_factor": str(divisor) if divisor is not None else None,
                "cofactor": str(target // divisor) if divisor is not None else None,
                "checked_divisor_count": (divisor - start + 1) if divisor is not None else end - start + 1,
                "executor_summary": {"fake_transport": True},
                "created_at": "2026-08-21T00:00:00Z",
            }
        else:
            candidate = re.search(r"Use this exact proof_candidate_id: (\S+)", prompt)
            digest = re.search(r"Theorem payload digest: (\S+)", prompt)
            assert candidate is not None and digest is not None
            content = {
                "schema_version": "lean_proof.proof_candidate.v1",
                "proof_candidate_id": candidate.group(1),
                "theorem_payload_digest": digest.group(1),
                "proof_source": "by\n  trivial",
                "created_at": "2026-08-21T00:00:00Z",
            }
        body = json.dumps(
            {
                "id": f"fake-{len(self.responses)}",
                "model": self.model,
                "choices": [{"message": {"content": json.dumps(content), "reasoning_content": ""}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 12,
                    "prompt_cache_hit_tokens": 2,
                    "prompt_cache_miss_tokens": 10,
                    "completion_tokens": 8,
                    "total_tokens": 20,
                    "completion_tokens_details": {"reasoning_tokens": 2},
                },
            }
        ).encode("utf-8")
        response = _FakeResponse(body)
        self.responses.append(response)
        return response


def _answer_path_factor_case() -> dict[str, object]:
    return {
        "schema_version": "tokenshare.paper_factorization_case.v1",
        "case_id": "answer_path_factor_91",
        "difficulty": "easy",
        "target_n": "91",
        "candidate_start": "2",
        "candidate_end": "9",
        "split_params": {
            "strategy_id": "factorization.candidate_range_partition.v1",
            "requested_child_count": 3,
        },
        "oracle_prime_factors": [
            {"prime": "7", "exponent": 1},
            {"prime": "13", "exponent": 1},
        ],
    }


def test_provider_submission_adapter_completes_factorization_root_and_fixed_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments.execution import (
        FixedTraceSubmissionAdapter,
        ProviderSubmissionAdapter,
    )
    from tokenshare.experiments.runtime import (
        RootAssembly,
        materialize_protocol_traces,
        run_coverage_tail,
        run_root_slice,
    )
    from tokenshare.local_runtime import SequentialWorkerBackend
    from tokenshare.plugins.factorization.runtime_adapter import (
        FactorizationExecutionBridge,
        FactorizationRuntimeAdapter,
    )
    from tokenshare.plugins.factorization.models import FactorSearchRangeInput
    from tokenshare.plugins.factorization.prompt_builder import (
        build_factor_search_prompt_package,
    )
    from tokenshare.plugins.factorization.validator import (
        build_factor_search_instruction,
        verify_range_result,
    )
    from tokenshare.storage.artifacts import ArtifactStore
    from tokenshare.storage.events import EventLedger
    from tests.experiments.test_system_vertical import (
        NOW,
        _Clock,
        _ObservationClock,
        _config,
        _inventory,
    )
    from tokenshare.experiments import provider

    model = "deepseek-v4-pro"
    monkeypatch.setenv("EXPERIMENTS_TEST_KEY", "secret")
    factory = _PromptResponseFactory(model)
    monkeypatch.setattr(provider, "_open_response", factory)
    case = _answer_path_factor_case()
    artifact_store = ArtifactStore(tmp_path / "factor")
    config = _config("answer_path_factor")
    runtime_adapter = FactorizationRuntimeAdapter(
        provider_family="deepseek", seed=7, protocol_config=config, created_at=NOW
    )
    root_key = ("exp1", "answer_path_factor", str(case["case_id"]), 0)
    ordinary = RunStore(tmp_path / "ordinary")
    submission_adapter = ProviderSubmissionAdapter(
        entry=_entry(family="deepseek", model=model),
        artifact_store=artifact_store,
        run_store=ordinary,
        root_key=root_key,
        domain="factorization",
    )
    clock = _Clock()
    assembly = RootAssembly(
        run_id="answer-path-factor",
        root_input=case,
        protocol_config=config,
        artifact_store=artifact_store,
        event_ledger=EventLedger(tmp_path / "factor" / "events.jsonl"),
        plugin_runtime=runtime_adapter,
        worker_backend=SequentialWorkerBackend(
            executor=FactorizationExecutionBridge(
                plugin_runtime=runtime_adapter, range_executor=submission_adapter
            ),
            submitted_at=clock,
        ),
        now=clock,
        observation_clock=_ObservationClock(),
    )

    result = run_root_slice(assembly)

    assert result.status == "completed", (
        [attempt.result_kind for attempt in submission_adapter.attempts],
        [attempt.parse_result for attempt in submission_adapter.attempts],
        submission_adapter.errors,
        [(outcome.ok, outcome.error_kind, outcome.error_message, outcome.resolved_model) for outcome in submission_adapter.outcomes],
        len(factory.responses),
        [event.to_dict() for event in assembly.event_ledger.read_all() if "error" in json.dumps(event.to_dict()).lower()][-3:],
        result,
    )
    assert len(submission_adapter.requests) == 2
    assert len(factory.responses) == 2
    assert all(response.closed for response in factory.responses)
    from tokenshare.experiments.projector import (
        RootProjectionError,
        project_root_result,
    )

    inventory = replace(
        _inventory(case=case, domain="factorization"),
        condition_id=root_key[1],
        provider_entry_id="deepseek-entry",
        configured_model=model,
    )
    with pytest.raises(RootProjectionError, match="require a completed coverage-tail"):
        project_root_result(
            inventory=inventory,
            assembly=assembly,
            protocol_result=result,
            provider_family="deepseek",
            requested_model=model,
            resolved_model=model,
            reasoning_mode="thinking",
            attempts=submission_adapter.attempts,
        )
    materialize_protocol_traces(
        store=ordinary,
        root_key=root_key,
        protocol_result=result,
        submission_adapter=submission_adapter,
        event_ledger=assembly.event_ledger,
    )
    protocol_attempts = tuple(submission_adapter.attempts)
    assert all(
        (attempt.started_at_ms, attempt.ended_at_ms) == (None, None)
        for attempt in protocol_attempts
    )
    protocol_before = ordinary.root_protocol_path(*root_key).read_bytes()
    source_request = submission_adapter.requests[0]
    source_range = json.loads(
        artifact_store.read_bytes(source_request.input_artifact_refs["range_input"]).decode("utf-8")
    )
    tail_range = FactorSearchRangeInput(
        target_n="91",
        range_start="8",
        range_end="9",
        coverage_id=source_range["coverage_id"],
        child_index=2,
        child_count=source_range["child_count"],
        partition_params_digest=source_range["partition_params_digest"],
    )
    tail_range_ref = artifact_store.save_json(
        tail_range.to_dict(),
        artifact_id="answer_path_tail_range_2",
        artifact_type="range_input",
        artifact_schema_id="factorization.factor_search_range_input",
        artifact_schema_version="v1",
        source={"kind": "experiments_test_tail_fixture"},
        metadata={"planned_ai_unit_id": "range_2"},
        created_at=NOW,
    )
    range_2_snapshot = next(
        event.payload["task_unit"]
        for event in assembly.event_ledger.read_all()
        if isinstance(event.payload.get("task_unit"), dict)
        and event.payload["task_unit"].get("plugin_payload", {}).get("summary", {}).get("child_index") == 2
    )
    range_2_unit_id = str(range_2_snapshot["unit_id"])
    tail_instruction = build_factor_search_instruction(
        request_id="answer-path-tail-range-2",
        unit_id=range_2_unit_id,
        range_input=tail_range,
    )
    tail_prompt = build_factor_search_prompt_package(
        request_id="answer-path-tail-range-2",
        task_id=source_request.task_id,
        unit_id=range_2_unit_id,
        range_input=tail_range,
        instruction=tail_instruction,
        created_at=NOW,
        seed=7,
    )
    tail_prompt_ref = artifact_store.save_json(
        tail_prompt.to_dict(),
        artifact_id="answer_path_tail_prompt_range_2",
        artifact_type="PromptPackage",
        artifact_schema_id="phase3.prompt_package",
        artifact_schema_version="v1",
        source={"kind": "experiments_test_tail_fixture"},
        metadata={"planned_ai_unit_id": "range_2"},
        created_at=NOW,
    )
    tail_request = replace(
        source_request,
        unit_id=range_2_unit_id,
        task_unit_snapshot=range_2_snapshot,
        input_artifact_refs={
            **source_request.input_artifact_refs,
            "range_input": tail_range_ref,
        },
        prompt_package_ref=tail_prompt_ref,
        soft_hints={
            **dict(source_request.soft_hints or {}),
            "planned_ai_unit_id": "range_2",
        },
    )
    def evaluate_tail(request: object, submission: object) -> dict[str, bool]:
        body = json.loads(
            artifact_store.read_bytes(submission.candidate_output_refs["range_result"]).decode("utf-8")
        )
        checked = verify_range_result(body, child_input=tail_range)
        return {"accepted": checked.accepted, "reached_domain_check": True}

    ticks = iter((100, 130))
    tail = run_coverage_tail(
        store=ordinary,
        case_id=str(case["case_id"]),
        target_requests={"range_2": tail_request, "not_protocol_unscheduled": tail_request},
        submission_adapter=submission_adapter,
        evaluate_submission=evaluate_tail,
        clock_ms=lambda: next(ticks),
        protocol_result=result,
    )
    assert result.status == "completed"
    assert tail.trace_tail_target_ai_unit_ids == ["range_2"]
    assert tail.trace_tail_recorded_ai_unit_ids == ["range_2"]
    assert tail.trace_tail_provider_attempt_count == 1
    assert factory.timeout_seconds == [600.0, 600.0, 600.0]
    assert all(body["max_tokens"] == 300_000 for body in factory.request_bodies)
    assert ordinary.root_protocol_path(*root_key).read_bytes() == protocol_before
    assert scan_resume(ordinary.run_dir).trace_keys == frozenset(
        {
            (str(case["case_id"]), 0, "range_0"),
            (str(case["case_id"]), 0, "range_1"),
            (str(case["case_id"]), 0, "range_2"),
        }
    )
    projected = project_root_result(
        inventory=inventory,
        assembly=assembly,
        protocol_result=result,
        provider_family="deepseek",
        requested_model=model,
        resolved_model=model,
        reasoning_mode="thinking",
        attempts=protocol_attempts,
        tail_summary=tail,
    )
    assert (projected.root_status, projected.final_result_present, projected.verified_correct) == (
        "completed",
        True,
        True,
    )
    assert projected.runtime_wall_clock_ms == 0
    assert projected.trace_tail_wall_clock_ms == 30
    assert len(projected.attempts) == 2
    tail_trace = ordinary.read_trace(str(case["case_id"]), 0, "range_2")
    assert (tail_trace.candidate_start, tail_trace.candidate_end) == (8, 9)
    assert tail_trace.attempts[0].verifier_result == "passed"
    assert (tail_trace.attempts[0].started_at_ms, tail_trace.attempts[0].ended_at_ms) == (
        100,
        130,
    )
    assert "attempts[].started_at_ms" not in tail_trace.attempts[0].missing_reason
    assert "attempts[].ended_at_ms" not in tail_trace.attempts[0].missing_reason

    provider_call_count = len(factory.responses)
    resumed_tail = run_coverage_tail(
        store=ordinary,
        case_id=str(case["case_id"]),
        target_requests={"range_2": tail_request},
        submission_adapter=submission_adapter,
        evaluate_submission=evaluate_tail,
        clock_ms=lambda: pytest.fail("a fully covered resume must not read the live clock"),
        protocol_result=result,
    )
    assert resumed_tail.trace_tail_status == "completed"
    assert resumed_tail.trace_tail_target_ai_unit_ids == ["range_2"]
    assert resumed_tail.trace_tail_recorded_ai_unit_ids == ["range_2"]
    assert resumed_tail.trace_tail_success_unit_count == 1
    assert resumed_tail.trace_tail_failure_unit_count == 0
    assert resumed_tail.trace_tail_provider_attempt_count == 1
    assert resumed_tail.trace_tail_total_tokens == tail.trace_tail_total_tokens
    assert resumed_tail.trace_tail_cost_estimate_cny == tail.trace_tail_cost_estimate_cny
    assert (
        resumed_tail.trace_tail_started_at_ms,
        resumed_tail.trace_tail_terminal_at_ms,
        resumed_tail.trace_tail_wall_clock_ms,
    ) == (100, 130, 30)
    assert len(factory.responses) == provider_call_count
    resumed_projected = project_root_result(
        inventory=inventory,
        assembly=assembly,
        protocol_result=result,
        provider_family="deepseek",
        requested_model=model,
        resolved_model=model,
        reasoning_mode="thinking",
        attempts=protocol_attempts,
        tail_summary=resumed_tail,
    )
    assert resumed_projected.trace_tail_target_ai_unit_ids == ["range_2"]
    assert resumed_projected.trace_tail_recorded_ai_unit_ids == ["range_2"]

    fixed = FixedTraceSubmissionAdapter(
        source_store=ordinary,
        artifact_store=artifact_store,
        case_id=str(case["case_id"]),
        domain="factorization",
        provider_entry_id="deepseek-entry",
        configured_model=model,
    )
    fallback_request = tail_request.__class__(
        **{**tail_request.__dict__, "attempt_ordinal": 2, "attempt_id": "fixed-fallback-attempt"}
    )
    before = len(factory.responses)
    submission = fixed.execute(
        fallback_request,
        submission_id="fixed-fallback-submission",
        submitted_at=NOW,
    )

    assert submission.result_kind == "succeeded"
    assert submission.usage_summary["provider_call_made"] is False
    assert submission.usage_summary["source_attempt_ordinal"] == 0
    assert submission.usage_summary["source_attempt_fallback_used"] is True
    assert len(factory.responses) == before


def test_provider_submission_adapter_completes_lean_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments import provider
    from tokenshare.experiments.execution import ProviderSubmissionAdapter
    from tokenshare.experiments.projector import project_root_result
    from tokenshare.experiments.runtime import RootAssembly, run_root_slice
    from tokenshare.local_runtime import SequentialWorkerBackend
    from tokenshare.plugins.lean_proof.runtime_adapter import LeanExecutionBridge, LeanRuntimeAdapter
    from tokenshare.storage.artifacts import ArtifactStore
    from tokenshare.storage.events import EventLedger
    from tests.experiments.test_system_vertical import (
        NOW,
        _Clock,
        _ObservationClock,
        _config,
        _lean_case,
        _test_lean_environment,
    )
    from tests.support.lean_checker import RecordingLeanChecker

    model = "deepseek-v4-pro"
    monkeypatch.setenv("EXPERIMENTS_TEST_KEY", "secret")
    factory = _PromptResponseFactory(model)
    monkeypatch.setattr(provider, "_open_response", factory)
    case = _lean_case("lean_v2_medium_lemma_dag_01")
    artifact_store = ArtifactStore(tmp_path / "lean")
    config = _config("answer_path_lean")
    checker = RecordingLeanChecker()
    runtime_adapter = LeanRuntimeAdapter(
        provider_family="deepseek",
        environment_manifest=_test_lean_environment(),
        checker=checker,
        protocol_config=config,
        created_at=NOW,
    )
    submission_adapter = ProviderSubmissionAdapter(
        entry=_entry(family="deepseek", model=model),
        artifact_store=artifact_store,
        run_store=RunStore(tmp_path / "ordinary"),
        root_key=("exp1", "answer_path_lean", str(case["case_id"]), 0),
        domain="lean",
    )
    clock = _Clock()
    assembly = RootAssembly(
        run_id="answer-path-lean",
        root_input=case,
        protocol_config=config,
        artifact_store=artifact_store,
        event_ledger=EventLedger(tmp_path / "lean" / "events.jsonl"),
        plugin_runtime=runtime_adapter,
        worker_backend=SequentialWorkerBackend(
            executor=LeanExecutionBridge(
                plugin_runtime=runtime_adapter,
                proof_candidate_executor=submission_adapter,
            ),
            submitted_at=clock,
        ),
        now=clock,
        observation_clock=_ObservationClock(),
    )

    result = run_root_slice(assembly)

    assert result.status == "completed", submission_adapter.errors
    assert submission_adapter.requests
    assert len(factory.responses) == len(submission_adapter.requests)
    assert factory.timeout_seconds == [600.0] * len(factory.responses)
    assert all(body["max_tokens"] == 300_000 for body in factory.request_bodies)
    assert all(attempt.parse_result == "parsed" for attempt in submission_adapter.attempts)


@pytest.mark.parametrize(
    ("model", "thinking"),
    [
        ("zai-org/GLM-5.2", True),
        ("Qwen/Qwen3-14B", True),
        ("MiniMaxAI/MiniMax-M2.5", True),
    ],
)
def test_exp5_three_siliconflow_entries_complete_without_tail_or_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model: str,
    thinking: bool,
) -> None:
    from tokenshare.experiments import provider
    from tokenshare.experiments.execution import ProviderSubmissionAdapter
    from tokenshare.experiments.projector import project_root_result
    from tokenshare.experiments.runtime import RootAssembly, run_root_slice
    from tokenshare.local_runtime import ProtocolMechanismPolicy, SequentialWorkerBackend
    from tokenshare.plugins.factorization.runtime_adapter import (
        FactorizationExecutionBridge,
        FactorizationRuntimeAdapter,
    )
    from tokenshare.storage.artifacts import ArtifactStore
    from tokenshare.storage.events import EventLedger
    from tests.experiments.test_system_vertical import (
        NOW,
        _Clock,
        _ObservationClock,
        _config,
        _factorization_case,
        _inventory,
    )

    monkeypatch.setenv("EXPERIMENTS_TEST_KEY", "secret")
    factory = _PromptResponseFactory(model)
    monkeypatch.setattr(provider, "_open_response", factory)
    entry = _entry(model=model, timeout_seconds=1200.0, max_tokens=4096)
    entry.request_overrides = (
        {"enable_thinking": True, "thinking_budget": 32768}
        if thinking
        else {"enable_thinking": False}
    )
    case = _factorization_case()
    safe_model = model.replace("/", "_")
    artifact_store = ArtifactStore(tmp_path / safe_model)
    config = replace(_config(f"exp5_{safe_model}"), max_retries=2)
    runtime_adapter = FactorizationRuntimeAdapter(
        provider_family="siliconflow", seed=7, protocol_config=config, created_at=NOW
    )
    ordinary = RunStore(tmp_path / f"ordinary_{safe_model}")
    submission_adapter = ProviderSubmissionAdapter(
        entry=entry,
        artifact_store=artifact_store,
        run_store=ordinary,
        root_key=("exp5", f"model_{safe_model}", str(case["case_id"]), 0),
        domain="factorization",
    )
    clock = _Clock()
    assembly = RootAssembly(
        run_id=f"exp5-{safe_model}",
        root_input=case,
        protocol_config=config,
        artifact_store=artifact_store,
        event_ledger=EventLedger(tmp_path / safe_model / "events.jsonl"),
        plugin_runtime=runtime_adapter,
        worker_backend=SequentialWorkerBackend(
            executor=FactorizationExecutionBridge(
                plugin_runtime=runtime_adapter,
                range_executor=submission_adapter,
            ),
            submitted_at=clock,
        ),
        now=clock,
        observation_clock=_ObservationClock(),
        mechanism_policy=ProtocolMechanismPolicy(replacement_attempts_allowed=True),
    )

    result = run_root_slice(assembly)

    assert result.status == "completed", submission_adapter.errors
    assert config.max_retries == 2
    assert assembly.mechanism_policy.replacement_attempts_allowed is True
    assert len(factory.responses) == len(submission_adapter.requests) == 3
    assert factory.timeout_seconds == [1200.0, 1200.0, 1200.0]
    assert all(body["max_tokens"] == 4096 for body in factory.request_bodies)
    assert not scan_resume(ordinary.run_dir).trace_keys
    assert not scan_resume(ordinary.run_dir).protocol_root_keys
    assert all(body["model"] == model for body in factory.request_bodies)
    assert all(body.get("enable_thinking") is thinking for body in factory.request_bodies)
    if thinking:
        assert all(body.get("thinking_budget") == 32768 for body in factory.request_bodies)
    inventory = replace(
        _inventory(case=case, domain="factorization"),
        experiment_id="exp5",
        condition_id=f"model_{safe_model}",
        provider_entry_id="siliconflow-entry",
        configured_model=model,
    )
    projected = project_root_result(
        inventory=inventory,
        assembly=assembly,
        protocol_result=result,
        provider_family="siliconflow",
        requested_model=model,
        resolved_model=model,
        reasoning_mode="thinking" if thinking else "nonthinking",
        attempts=submission_adapter.attempts,
    )
    assert projected.root_status == "completed"
    assert projected.trace_tail_status is None
    assert len(projected.attempts) == 3
