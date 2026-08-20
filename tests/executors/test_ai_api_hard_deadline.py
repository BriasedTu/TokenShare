from __future__ import annotations

import json
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from tokenshare.experiments.run_paper_pipeline import _CountingRepresentativeTransport
from tokenshare.executors import ai_api_hard_deadline
from tokenshare.executors.ai_api import AIAPIExecutor, dispatch_prepared_request_once
from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.executors.ai_api_request_identity import PreparedOutboundRequestFactory
from tokenshare.executors.ai_api_transport import (
    UrlLibDeepSeekTransport,
    UrlLibSiliconFlowTransport,
)
from tokenshare.storage.artifacts import ArtifactStore
from tests.phase7_fixtures import make_ai_request, make_config_dict


_FAKE_SECRET = "focused-hard-deadline-fake-key"


class _ScenarioServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _ScenarioHandler)
        self.calls = 0
        self.request_started = threading.Event()
        self.release_response = threading.Event()


class _ScenarioHandler(BaseHTTPRequestHandler):
    server: _ScenarioServer

    def log_message(self, _format: str, *_args: object) -> None:
        return None

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler ABI
        self.server.calls += 1
        self.server.request_started.set()
        body = _provider_body()
        if self.path == "/never":
            time.sleep(4.0)
            return
        if self.path == "/race":
            self.server.release_response.wait(timeout=4.0)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.path == "/slow-body":
                midpoint = max(1, len(body) // 2)
                self.wfile.write(body[:midpoint])
                self.wfile.flush()
                time.sleep(4.0)
                self.wfile.write(body[midpoint:])
                return
            if self.path == "/trickle":
                for byte in body:
                    self.wfile.write(bytes((byte,)))
                    self.wfile.flush()
                    time.sleep(0.03)
                return
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return


@pytest.fixture
def scenario_server():
    server = _ScenarioServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


@pytest.mark.parametrize("scenario", ["never", "slow-body", "trickle"])
def test_hard_deadline_reaps_nonreturning_child_without_late_result(
    scenario_server: _ScenarioServer,
    scenario: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processes: list[subprocess.Popen[bytes]] = []
    real_popen = subprocess.Popen

    def popen_spy(*args: Any, **kwargs: Any):
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", popen_spy)
    started = time.monotonic()
    evidence = _dispatch(
        scenario_server,
        scenario=scenario,
        provider_family="deepseek",
    )
    elapsed = time.monotonic() - started

    assert elapsed < 2.5
    assert scenario_server.calls == 1
    assert len(processes) == 1
    assert processes[0].poll() is not None
    assert evidence.terminal_kind == "provider_failure"
    assert evidence.failure_kind == "no_response"
    assert evidence.usage is None
    assert evidence.latency_ms is None
    assert evidence.transport_call_count == 1
    assert evidence.latency_timing_source == "unknown_no_response"
    proof = evidence.hard_deadline_evidence
    assert proof == {
        **proof,
        "schema_version": "tokenshare.ai_api_hard_deadline_quiescence.v1",
        "deadline_enforced": True,
        "child_reaped": True,
        "result_observed_before_deadline": False,
        "post_reap_late_result_absent": True,
        "parent_only_evidence_consumer": True,
        "result_commit_count": 0,
        "accepted_result_commit_count": 0,
    }
    time.sleep(0.1)
    assert processes[0].poll() is not None
    assert proof["post_reap_late_result_absent"] is True


def test_success_at_deadline_race_has_one_parent_result(
    scenario_server: _ScenarioServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processes: list[subprocess.Popen[bytes]] = []
    real_popen = subprocess.Popen

    def popen_spy(*args: Any, **kwargs: Any):
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", popen_spy)

    def release_near_deadline() -> None:
        assert scenario_server.request_started.wait(timeout=2.0)
        # 保持靠近 1 秒截止，但为 Windows child bootstrap 留出稳定余量。
        time.sleep(0.45)
        scenario_server.release_response.set()

    release = threading.Thread(target=release_near_deadline, daemon=True)
    release.start()
    evidence = _dispatch(
        scenario_server,
        scenario="race",
        provider_family="deepseek",
    )
    release.join(timeout=2.0)

    assert scenario_server.calls == 1
    assert len(processes) == 1
    assert processes[0].poll() is not None
    assert evidence.terminal_kind == "success"
    assert evidence.transport_call_count == 1
    assert evidence.latency_ms is not None
    assert evidence.latency_timing_source == "provider_child_observed"
    proof = evidence.hard_deadline_evidence
    assert proof["deadline_enforced"] is True
    assert proof["child_reaped"] is True
    assert proof["result_commit_count"] == 1
    assert proof["accepted_result_commit_count"] == 1
    assert proof["result_observed_before_deadline"] is True
    assert proof["post_reap_late_result_absent"] is True
    assert proof["parent_only_evidence_consumer"] is True


def test_counting_representative_transport_observes_one_no_response_attempt(
    scenario_server: _ScenarioServer,
) -> None:
    counting = _CountingRepresentativeTransport(UrlLibDeepSeekTransport())
    transport_starts: list[str] = []

    evidence = _dispatch(
        scenario_server,
        scenario="slow-body",
        provider_family="deepseek",
        transport=counting,
        on_transport_start=lambda: transport_starts.append("started"),
    )

    assert evidence.terminal_kind == "provider_failure"
    assert evidence.failure_kind == "no_response"
    assert evidence.transport_call_count == 1
    assert counting.provider_calls == 1
    assert len(counting.observations) == 1
    assert scenario_server.calls == 1
    assert transport_starts == ["started"]


def test_exp5_ai_api_executor_counts_one_hard_deadline_attempt_without_retry(
    scenario_server: _ScenarioServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_key_env = "TOKENSHARE_FOCUSED_EXP5_KEY"
    monkeypatch.setenv(api_key_env, _FAKE_SECRET)
    counting = _CountingRepresentativeTransport(UrlLibSiliconFlowTransport())
    store = ArtifactStore(tmp_path)

    submission = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=load_ai_api_config(
            _exp5_config_body(
                scenario_server,
                api_key_env=api_key_env,
                scenario="slow-body",
            )
        ),
        transport=counting,
    ).execute(
        make_ai_request(store, request_id="focused_exp5_hard_deadline"),
        submission_id="focused_exp5_hard_deadline_submission",
        submitted_at="2026-08-16T00:00:00Z",
    )

    assert submission.result_kind == "no_response"
    assert submission.usage_summary["provider_attempt_count"] == 1
    assert counting.provider_calls == 1
    assert len(counting.observations) == 1
    assert scenario_server.calls == 1


def test_pre_network_child_exit_is_call_zero_executor_failure(
    scenario_server: _ScenarioServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_environment = ai_api_hard_deadline._minimal_child_environment

    def environment_without_internal_key(api_key: str) -> dict[str, str]:
        environment = real_environment(api_key)
        environment.pop(ai_api_hard_deadline.HARD_DEADLINE_API_KEY_ENV)
        return environment

    monkeypatch.setattr(
        ai_api_hard_deadline,
        "_minimal_child_environment",
        environment_without_internal_key,
    )
    counting = _CountingRepresentativeTransport(UrlLibDeepSeekTransport())
    transport_starts: list[str] = []

    evidence = _dispatch(
        scenario_server,
        scenario="success",
        provider_family="deepseek",
        transport=counting,
        on_transport_start=lambda: transport_starts.append("started"),
    )

    assert evidence.terminal_kind == "provider_failure"
    assert evidence.failure_kind == "executor_error"
    assert evidence.transport_call_count == 0
    assert evidence.latency_ms is None
    assert evidence.latency_timing_source == "unavailable_pre_dispatch"
    assert counting.provider_calls == 0
    assert scenario_server.calls == 0
    assert transport_starts == []
    assert evidence.hard_deadline_evidence["network_start_acknowledged"] is False

    api_key_env = "TOKENSHARE_FOCUSED_EXP5_PREBOOT_KEY"
    monkeypatch.setenv(api_key_env, _FAKE_SECRET)
    exp5_counting = _CountingRepresentativeTransport(UrlLibSiliconFlowTransport())
    store = ArtifactStore(tmp_path)
    submission = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=load_ai_api_config(
            _exp5_config_body(
                scenario_server,
                api_key_env=api_key_env,
                scenario="success",
            )
        ),
        transport=exp5_counting,
    ).execute(
        make_ai_request(store, request_id="focused_exp5_preboot_failure"),
        submission_id="focused_exp5_preboot_failure_submission",
        submitted_at="2026-08-16T00:00:00Z",
    )

    assert submission.result_kind == "executor_error"
    assert submission.usage_summary["provider_attempt_count"] == 0
    assert exp5_counting.provider_calls == 0
    assert scenario_server.calls == 0
    provenance = json.loads(
        store.read_bytes(submission.provenance_ref).decode("utf-8")
    )
    assert provenance["attempts"] == [
        {
            **provenance["attempts"][0],
            "latency_ms": None,
            "latency_timing_source": "unavailable_pre_dispatch",
            "transport_call_count": 0,
        }
    ]


def test_pre_dispatch_result_visible_only_after_deadline_is_not_accepted(
    scenario_server: _ScenarioServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminate_started = threading.Event()
    real_popen = subprocess.Popen
    real_is_file = Path.is_file
    real_environment = ai_api_hard_deadline._minimal_child_environment

    def environment_without_internal_key(api_key: str) -> dict[str, str]:
        environment = real_environment(api_key)
        environment.pop(ai_api_hard_deadline.HARD_DEADLINE_API_KEY_ENV)
        return environment

    def delayed_popen(*args: Any, **kwargs: Any):
        return _DelayedTerminateProcess(
            real_popen(*args, **kwargs),
            terminate_started=terminate_started,
            mask_poll_until_terminate=True,
        )

    def hide_pre_dispatch_result_until_deadline(path: Path) -> bool:
        if path.name == "result.v1.json" and not terminate_started.is_set():
            return False
        return real_is_file(path)

    monkeypatch.setattr(
        ai_api_hard_deadline,
        "_minimal_child_environment",
        environment_without_internal_key,
    )
    monkeypatch.setattr(subprocess, "Popen", delayed_popen)
    monkeypatch.setattr(Path, "is_file", hide_pre_dispatch_result_until_deadline)

    evidence = _dispatch(
        scenario_server,
        scenario="success",
        provider_family="deepseek",
        transport=_CountingRepresentativeTransport(UrlLibDeepSeekTransport()),
    )

    assert evidence.failure_kind == "executor_error"
    assert evidence.transport_call_count == 0
    proof = evidence.hard_deadline_evidence
    assert proof["result_observed_before_deadline"] is False
    assert proof["accepted_result_commit_count"] == 0
    assert proof["result_commit_count"] == 1
    assert proof["late_result_rejected"] is True
    assert proof["post_reap_late_result_absent"] is True


def test_result_committed_after_parent_deadline_is_rejected_and_reaped(
    scenario_server: _ScenarioServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processes: list[_DelayedTerminateProcess] = []
    real_popen = subprocess.Popen
    terminate_started = threading.Event()

    def delayed_terminate_popen(*args: Any, **kwargs: Any):
        process = _DelayedTerminateProcess(
            real_popen(*args, **kwargs), terminate_started=terminate_started
        )
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", delayed_terminate_popen)

    def release_after_deadline() -> None:
        assert scenario_server.request_started.wait(timeout=2.0)
        assert terminate_started.wait(timeout=2.0)
        scenario_server.release_response.set()

    release = threading.Thread(target=release_after_deadline, daemon=True)
    release.start()
    evidence = _dispatch(
        scenario_server,
        scenario="race",
        provider_family="deepseek",
    )
    release.join(timeout=2.0)

    assert scenario_server.calls == 1
    assert len(processes) == 1
    assert processes[0].poll() is not None
    assert evidence.terminal_kind == "provider_failure"
    assert evidence.failure_kind == "no_response"
    assert evidence.transport_call_count == 1
    proof = evidence.hard_deadline_evidence
    assert proof["result_observed_before_deadline"] is False
    assert proof["post_reap_late_result_absent"] is True
    assert proof["late_result_rejected"] is True
    assert proof["result_commit_count"] == 1
    assert proof["accepted_result_commit_count"] == 0
    assert proof["child_reaped"] is True


def test_result_committed_before_deadline_between_path_check_and_child_exit_is_accepted(
    scenario_server: _ScenarioServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_is_file = Path.is_file
    hidden_committed_result = False
    result_hidden = threading.Event()
    real_popen = subprocess.Popen

    def wait_for_exit_after_hidden_result_popen(*args: Any, **kwargs: Any):
        return _DelayedTerminateProcess(
            real_popen(*args, **kwargs),
            terminate_started=threading.Event(),
            wait_for_exit_on_poll=result_hidden,
        )

    def hide_first_committed_result(path: Path) -> bool:
        nonlocal hidden_committed_result
        actual = real_is_file(path)
        if (
            path.name == "result.v1.json"
            and actual
            and not hidden_committed_result
        ):
            hidden_committed_result = True
            result_hidden.set()
            return False
        return actual

    monkeypatch.setattr(subprocess, "Popen", wait_for_exit_after_hidden_result_popen)
    monkeypatch.setattr(Path, "is_file", hide_first_committed_result)

    evidence = _dispatch(
        scenario_server,
        scenario="success",
        provider_family="deepseek",
        transport=_CountingRepresentativeTransport(UrlLibDeepSeekTransport()),
    )

    assert hidden_committed_result is True
    assert evidence.terminal_kind == "success"
    assert evidence.transport_call_count == 1
    proof = evidence.hard_deadline_evidence
    assert proof["result_observed_before_deadline"] is True
    assert proof["accepted_result_commit_count"] == 1
    assert proof["late_result_rejected"] is False


def test_transport_start_callback_failure_never_releases_child_or_retries(
    scenario_server: _ScenarioServer,
) -> None:
    callback_calls = 0
    counting = _CountingRepresentativeTransport(UrlLibDeepSeekTransport())

    def fail_before_transport() -> None:
        nonlocal callback_calls
        callback_calls += 1
        raise RuntimeError("focused callback failure")

    with pytest.raises(RuntimeError, match="focused callback failure"):
        _dispatch(
            scenario_server,
            scenario="success",
            provider_family="deepseek",
            transport=counting,
            on_transport_start=fail_before_transport,
        )

    assert callback_calls == 1
    assert counting.provider_calls == 0
    assert scenario_server.calls == 0


def test_direct_transport_start_callback_runs_once_immediately_before_post() -> None:
    events: list[str] = []

    class DirectTransport:
        def post_chat_completion(self, **_kwargs: Any):
            events.append("post")
            return _DirectResponse()

    prepared = _prepared_request(
        base_url="https://focused.invalid",
        endpoint="/chat/completions",
        provider_family="deepseek",
    )
    evidence = dispatch_prepared_request_once(
        prepared_request=prepared,
        provider_family="deepseek",
        transport=DirectTransport(),
        api_key=_FAKE_SECRET,
        timeout_seconds=1,
        on_transport_start=lambda: events.append("start"),
    )

    assert evidence.terminal_kind == "success"
    assert events == ["start", "post"]


@pytest.mark.parametrize(
    ("provider_family", "transport_type"),
    [
        ("deepseek", UrlLibDeepSeekTransport),
        ("siliconflow", UrlLibSiliconFlowTransport),
    ],
)
def test_deepseek_and_siliconflow_share_one_hard_total_seam(
    scenario_server: _ScenarioServer,
    provider_family: str,
    transport_type: type,
) -> None:
    evidence = _dispatch(
        scenario_server,
        scenario="slow-body",
        provider_family=provider_family,
        transport=transport_type(),
    )

    assert evidence.terminal_kind == "provider_failure"
    assert evidence.failure_kind == "no_response"
    assert evidence.transport_call_count == 1
    assert evidence.hard_deadline_evidence["deadline_enforced"] is True
    assert evidence.hard_deadline_evidence["child_reaped"] is True


def test_child_receives_only_internal_key_env_and_secret_never_enters_evidence(
    scenario_server: _ScenarioServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launches: list[tuple[tuple[object, ...], dict[str, Any]]] = []
    real_popen = subprocess.Popen

    def popen_spy(*args: Any, **kwargs: Any):
        launches.append((args, kwargs))
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", popen_spy)
    evidence = _dispatch(
        scenario_server,
        scenario="success",
        provider_family="deepseek",
    )

    assert evidence.terminal_kind == "success"
    assert len(launches) == 1
    args, kwargs = launches[0]
    assert _FAKE_SECRET not in repr(args)
    child_env = kwargs["env"]
    assert child_env["TOKENSHARE_HARD_DEADLINE_API_KEY"] == _FAKE_SECRET
    assert "DEEPSEEK_API_KEY" not in child_env
    assert "SILICONFLOW_API_KEY" not in child_env
    assert [name for name, value in child_env.items() if value == _FAKE_SECRET] == [
        "TOKENSHARE_HARD_DEADLINE_API_KEY"
    ]
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL
    assert _FAKE_SECRET not in json.dumps(evidence.__dict__, default=str)
    assert evidence.hard_deadline_evidence["ephemeral_files_secret_free"] is True


def _dispatch(
    server: _ScenarioServer,
    *,
    scenario: str,
    provider_family: str,
    transport: object | None = None,
    on_transport_start: Any | None = None,
):
    model = (
        "deepseek-v4-pro"
        if provider_family == "deepseek"
        else "Qwen/Qwen3-14B"
    )
    prepared = PreparedOutboundRequestFactory.prepare(
        body_obj={"model": model, "messages": [{"role": "user", "content": "x"}]},
        base_url=f"http://127.0.0.1:{server.server_port}",
        endpoint=f"/{scenario}",
        provider_config_digest="sha256:" + "1" * 64,
        entry_id="focused-hard-deadline",
        configured_model=model,
        effective_controls_digest="sha256:" + "2" * 64,
        plugin_id="factorization",
        plugin_version="1.0.0",
        prompt_profile_id="focused-hard-deadline",
        prompt_serialization_schema="tokenshare.test.prompt.v1",
        body_serialization_schema="tokenshare.test.body.v1",
        case_id="focused-case",
        planned_ai_unit_id="focused-node",
        sample_slot_index=0,
        replacement_slot=0,
    )
    if transport is None:
        transport = UrlLibDeepSeekTransport()
    dispatch_kwargs = {
        "prepared_request": prepared,
        "provider_family": provider_family,
        "transport": transport,
        "api_key": _FAKE_SECRET,
        "timeout_seconds": 1,
    }
    if on_transport_start is not None:
        dispatch_kwargs["on_transport_start"] = on_transport_start
    return dispatch_prepared_request_once(
        **dispatch_kwargs,
    )


def _prepared_request(
    *,
    base_url: str,
    endpoint: str,
    provider_family: str,
):
    model = "deepseek-v4-pro" if provider_family == "deepseek" else "Qwen/Qwen3-14B"
    return PreparedOutboundRequestFactory.prepare(
        body_obj={"model": model, "messages": [{"role": "user", "content": "x"}]},
        base_url=base_url,
        endpoint=endpoint,
        provider_config_digest="sha256:" + "1" * 64,
        entry_id="focused-hard-deadline",
        configured_model=model,
        effective_controls_digest="sha256:" + "2" * 64,
        plugin_id="factorization",
        plugin_version="1.0.0",
        prompt_profile_id="focused-hard-deadline",
        prompt_serialization_schema="tokenshare.test.prompt.v1",
        body_serialization_schema="tokenshare.test.body.v1",
        case_id="focused-case",
        planned_ai_unit_id="focused-node",
        sample_slot_index=0,
        replacement_slot=0,
    )


def _provider_body() -> bytes:
    return json.dumps(
        {
            "id": "local-fake-response",
            "model": "deepseek-v4-pro",
            "choices": [
                {
                    "message": {
                        "content": '{"answer":"ok"}',
                        "reasoning_content": "local-only",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
        sort_keys=True,
    ).encode("utf-8")


def _exp5_config_body(
    server: _ScenarioServer,
    *,
    api_key_env: str,
    scenario: str,
) -> dict[str, Any]:
    config_body = make_config_dict()
    config_body["defaults"] = {
        **config_body["defaults"],
        "timeout_seconds": 1,
        "max_provider_attempts": 1,
    }
    config_body["entries"] = [
        {
            **config_body["entries"][0],
            "base_url": f"http://127.0.0.1:{server.server_port}",
            "endpoint": f"/{scenario}",
            "api_key_env": api_key_env,
            "model": "Qwen/Qwen3-14B",
            "request_overrides": {
                "enable_thinking": True,
                "thinking_budget": 32768,
            },
        }
    ]
    return config_body


class _DelayedTerminateProcess:
    """放大 deadline/result 竞态，但仍使用并回收真实 child。"""

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        *,
        terminate_started: threading.Event,
        mask_poll_until_terminate: bool = False,
        wait_for_exit_on_poll: threading.Event | None = None,
    ) -> None:
        self._process = process
        self._terminate_started = terminate_started
        self._mask_poll_until_terminate = mask_poll_until_terminate
        self._wait_for_exit_on_poll = wait_for_exit_on_poll

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def returncode(self) -> int | None:
        return self._process.returncode

    def poll(self) -> int | None:
        if self._mask_poll_until_terminate and not self._terminate_started.is_set():
            return None
        if (
            self._wait_for_exit_on_poll is not None
            and self._wait_for_exit_on_poll.is_set()
            and self._process.poll() is None
        ):
            try:
                self._process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
        return self._process.poll()

    def terminate(self) -> None:
        self._terminate_started.set()
        time.sleep(0.30)
        if self._process.poll() is None:
            self._process.terminate()

    def kill(self) -> None:
        if self._process.poll() is None:
            self._process.kill()

    def wait(self, timeout: float | None = None) -> int:
        return self._process.wait(timeout=timeout)


class _DirectResponse:
    status_code = 200
    body = json.loads(_provider_body().decode("utf-8"))
    text = _provider_body().decode("utf-8")
