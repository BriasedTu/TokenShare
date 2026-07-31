from __future__ import annotations

import os
import socket
import subprocess
import sys
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = "verification.pytest_network_tripwire"
TRIPWIRE_ERROR = "network tripwire: outbound provider access forbidden"
ACTIVE_ENV = "TOKENSHARE_PYTEST_NETWORK_TRIPWIRE_ACTIVE"
COUNTER_ENV = "TOKENSHARE_PYTEST_NETWORK_TRIPWIRE_COUNTER"


class _ProcessWorkerSocketProbeExecutor:
    """真实 ProcessWorkerBackend 子进程使用的可 pickle 探针。"""

    def __init__(self, marker_path: Path) -> None:
        self.marker_path = marker_path

    def execute(self, request, *, submission_id: str, submitted_at: str):
        if not getattr(socket.create_connection, "_tokenshare_tripwire_guard", False):
            self.marker_path.write_text("guard-missing\n", encoding="utf-8")
            raise AssertionError("process worker tripwire guard missing")
        try:
            socket.create_connection(("127.0.0.1", 9), timeout=0.01)
        except RuntimeError as exc:
            self.marker_path.write_text(f"{exc}\n", encoding="utf-8")
            raise
        raise AssertionError("server response observed")


def _isolated_child_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop(ACTIVE_ENV, None)
    env.pop(COUNTER_ENV, None)
    return env


def _run_isolated_pytest(
    tmp_path: Path,
    *,
    probe_name: str,
    source: str,
) -> subprocess.CompletedProcess[str]:
    probe_path = tmp_path / f"test_{probe_name}.py"
    probe_path.write_text(textwrap.dedent(source), encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            PLUGIN,
            str(probe_path),
            "-q",
            "-s",
        ],
        cwd=ROOT,
        env=_isolated_child_env(),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _output(result: subprocess.CompletedProcess[str]) -> str:
    return f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"


def test_tripwire_blocks_socket_create_connection(tmp_path: Path) -> None:
    result = _run_isolated_pytest(
        tmp_path,
        probe_name="socket",
        source=f"""
        import socket

        ERROR = {TRIPWIRE_ERROR!r}


        def test_create_connection_is_blocked_before_connect():
            try:
                socket.create_connection(("127.0.0.1", 9), timeout=0.01)
            except RuntimeError as exc:
                assert str(exc) == ERROR
            else:
                raise AssertionError("server response observed")
            print("CREATE_CONNECTION_BLOCKED_BEFORE_SERVER_RESPONSE")


        def test_socket_connect_is_blocked_before_connect():
            candidate = socket.socket()
            try:
                try:
                    candidate.connect(("127.0.0.1", 9))
                except RuntimeError as exc:
                    assert str(exc) == ERROR
                else:
                    raise AssertionError("server response observed")
            finally:
                candidate.close()
            print("SOCKET_CONNECT_BLOCKED_BEFORE_SERVER_RESPONSE")
        """,
    )

    output = _output(result)
    assert result.returncode == 0, output
    assert "CREATE_CONNECTION_BLOCKED_BEFORE_SERVER_RESPONSE" in output
    assert "SOCKET_CONNECT_BLOCKED_BEFORE_SERVER_RESPONSE" in output
    assert "2 passed" in output


def test_tripwire_blocks_urllib_urlopen(tmp_path: Path) -> None:
    urlopen_result = _run_isolated_pytest(
        tmp_path,
        probe_name="urlopen",
        source=f"""
        import urllib.request

        ERROR = {TRIPWIRE_ERROR!r}


        def test_urlopen_is_blocked_before_server_response():
            try:
                urllib.request.urlopen("http://127.0.0.1:9/tripwire", timeout=0.01)
            except RuntimeError as exc:
                assert str(exc) == ERROR
            else:
                raise AssertionError("server response observed")
            print("URLOPEN_BLOCKED_BEFORE_SERVER_RESPONSE")
        """,
    )

    urlopen_output = _output(urlopen_result)
    assert urlopen_result.returncode == 0, urlopen_output
    assert "URLOPEN_BLOCKED_BEFORE_SERVER_RESPONSE" in urlopen_output
    assert "1 passed" in urlopen_output

    provider_result = _run_isolated_pytest(
        tmp_path,
        probe_name="production_transports",
        source=f"""
        from tokenshare.executors.ai_api_transport import (
            UrlLibDeepSeekTransport,
            UrlLibOpenAITransport,
            UrlLibSiliconFlowTransport,
        )

        ERROR = {TRIPWIRE_ERROR!r}


        def test_all_production_transports_are_blocked():
            transport_types = (
                UrlLibSiliconFlowTransport,
                UrlLibOpenAITransport,
                UrlLibDeepSeekTransport,
            )
            for transport_type in transport_types:
                try:
                    transport_type().post_chat_completion(
                        entry=None,
                        api_key="unused",
                        body={{}},
                        timeout_seconds=1,
                    )
                except RuntimeError as exc:
                    assert str(exc) == ERROR
                else:
                    raise AssertionError(
                        f"{{transport_type.__name__}} reached provider code"
                    )
                print(f"PRODUCTION_TRANSPORT_BLOCKED={{transport_type.__name__}}")
        """,
    )

    provider_output = _output(provider_result)
    assert provider_result.returncode != 0, provider_output
    for transport_name in (
        "UrlLibSiliconFlowTransport",
        "UrlLibOpenAITransport",
        "UrlLibDeepSeekTransport",
    ):
        assert f"PRODUCTION_TRANSPORT_BLOCKED={transport_name}" in provider_output
    assert "production provider call count must be zero (observed 3)" in provider_output


def test_tripwire_reports_zero_provider_calls_when_unused(tmp_path: Path) -> None:
    result = _run_isolated_pytest(
        tmp_path,
        probe_name="nonproduction_transports",
        source="""
        class FakeTransport:
            def post_chat_completion(self, **kwargs):
                return "fake-response"


        class CapturingTransport:
            tokenshare_offline_capturing_transport = True

            def __init__(self):
                self.calls = []

            def post_chat_completion(self, **kwargs):
                self.calls.append(kwargs)
                return "captured-response"


        def test_fake_and_capturing_transports_are_unaffected():
            fake = FakeTransport()
            capturing = CapturingTransport()

            assert fake.post_chat_completion(body={}) == "fake-response"
            assert capturing.post_chat_completion(body={}) == "captured-response"
            assert capturing.calls == [{"body": {}}]
            print("FAKE_AND_CAPTURING_TRANSPORTS_UNAFFECTED")
        """,
    )

    output = _output(result)
    assert result.returncode == 0, output
    assert "FAKE_AND_CAPTURING_TRANSPORTS_UNAFFECTED" in output
    assert "1 passed" in output


def test_sitecustomize_is_inactive_without_plugin() -> None:
    verification_root = ROOT / "verification"
    env = _isolated_child_env()
    env["PYTHONPATH"] = os.pathsep.join(
        (str(verification_root), str(ROOT), str(ROOT / "src"))
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                f"""
                import socket
                import sitecustomize
                import urllib.request

                assert sitecustomize.__file__ == {str(verification_root / 'sitecustomize.py')!r}
                assert not getattr(
                    socket.create_connection,
                    "_tokenshare_tripwire_guard",
                    False,
                )
                assert not getattr(
                    urllib.request.urlopen,
                    "_tokenshare_tripwire_guard",
                    False,
                )
                print("SITECUSTOMIZE_INACTIVE_WITHOUT_FLAG")
                """
            ),
        ],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    output = _output(result)
    assert result.returncode == 0, output
    assert "SITECUSTOMIZE_INACTIVE_WITHOUT_FLAG" in output


def test_tripwire_propagates_to_plain_subprocess(tmp_path: Path) -> None:
    child_code = textwrap.dedent(
        f"""
        import socket

        assert getattr(socket.create_connection, "_tokenshare_tripwire_guard", False)
        try:
            socket.create_connection(("127.0.0.1", 9), timeout=0.01)
        except RuntimeError as exc:
            assert str(exc) == {TRIPWIRE_ERROR!r}
        else:
            raise AssertionError("server response observed")
        print("PLAIN_SUBPROCESS_BLOCKED_BEFORE_SERVER_RESPONSE")
        """
    )
    result = _run_isolated_pytest(
        tmp_path,
        probe_name="plain_subprocess",
        source=f"""
        import subprocess
        import sys

        ERROR = {TRIPWIRE_ERROR!r}
        CHILD_CODE = {child_code!r}


        def test_plain_subprocess_inherits_tripwire():
            child = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    CHILD_CODE,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert child.returncode == 0, child.stdout + child.stderr
            assert "PLAIN_SUBPROCESS_BLOCKED_BEFORE_SERVER_RESPONSE" in child.stdout
        """,
    )

    output = _output(result)
    assert result.returncode == 0, output
    assert "1 passed" in output


def test_tripwire_propagates_to_multiprocessing_spawn(tmp_path: Path) -> None:
    result = _run_isolated_pytest(
        tmp_path,
        probe_name="multiprocessing_spawn",
        source=f"""
        import multiprocessing
        import socket
        from pathlib import Path

        ERROR = {TRIPWIRE_ERROR!r}


        def _spawn_probe(marker_path):
            marker = Path(marker_path)
            if not getattr(
                socket.create_connection,
                "_tokenshare_tripwire_guard",
                False,
            ):
                marker.write_text("guard-missing\\n", encoding="utf-8")
                return
            try:
                socket.create_connection(("127.0.0.1", 9), timeout=0.01)
            except RuntimeError as exc:
                marker.write_text(f"{{exc}}\\n", encoding="utf-8")
                return
            marker.write_text("server-response-observed\\n", encoding="utf-8")


        def test_spawn_child_inherits_tripwire(tmp_path):
            marker = tmp_path / "spawn-marker.txt"
            process = multiprocessing.get_context("spawn").Process(
                target=_spawn_probe,
                args=(marker,),
            )
            process.start()
            process.join(timeout=30)
            assert process.exitcode == 0
            assert marker.read_text(encoding="utf-8") == ERROR + "\\n"
        """,
    )

    output = _output(result)
    assert result.returncode == 0, output
    assert "1 passed" in output


def test_tripwire_propagates_to_process_worker_backend(tmp_path: Path) -> None:
    result = _run_isolated_pytest(
        tmp_path,
        probe_name="process_worker_backend",
        source=f"""
        from tests.phase3_fixtures import (
            make_environment_ref,
            make_executor_descriptor,
            make_output_contract,
            make_plugin_descriptor,
        )
        from tests.test_paper_network_tripwire import (
            _ProcessWorkerSocketProbeExecutor,
        )
        from tokenshare.executors.contracts import ExecutionRequest
        from tokenshare.local_runtime.workers import ProcessWorkerBackend

        ERROR = {TRIPWIRE_ERROR!r}


        def _request():
            return ExecutionRequest(
                request_id="request_tripwire",
                task_id="task_tripwire",
                unit_id="unit_tripwire",
                attempt_id="attempt_tripwire",
                lease_id="lease_tripwire",
                fencing_token="1",
                plugin=make_plugin_descriptor().to_dict(),
                executor=make_executor_descriptor().to_dict(),
                registry_snapshot_id="registry_tripwire",
                allocation_decision={{"client_id": "client_local"}},
                capability_snapshot={{"executor": "mock_ai"}},
                task_unit_snapshot={{"unit_id": "unit_tripwire"}},
                input_artifact_refs={{}},
                output_contract=make_output_contract(),
                hard_requirements={{"executor": "mock_ai"}},
                soft_hints={{}},
                environment_ref=make_environment_ref(),
                execution_instruction_ref=None,
                prompt_package_ref=None,
                limits={{}},
                created_at="2026-08-01T00:00:00Z",
            )


        def test_real_process_worker_child_inherits_tripwire(tmp_path):
            marker = tmp_path / "process-worker-marker.txt"
            backend = ProcessWorkerBackend(
                executor=_ProcessWorkerSocketProbeExecutor(marker),
                capacity=1,
                submitted_at=lambda: "2026-08-01T00:00:01Z",
            )

            outcome = backend.execute_batch((_request(),))[0]

            assert outcome.submission is None
            assert outcome.failure_kind == "executor_error"
            assert outcome.error_message == "RuntimeError: " + ERROR
            assert marker.read_text(encoding="utf-8") == ERROR + "\\n"
        """,
    )

    output = _output(result)
    assert result.returncode == 0, output
    assert "1 passed" in output


def test_tripwire_counts_provider_attempt_from_plain_subprocess(
    tmp_path: Path,
) -> None:
    child_code = textwrap.dedent(
        f"""
        import urllib.request
        from tokenshare.executors.ai_api_transport import UrlLibDeepSeekTransport

        assert getattr(urllib.request.urlopen, "_tokenshare_tripwire_guard", False)
        try:
            UrlLibDeepSeekTransport().post_chat_completion(
                entry=None,
                api_key="unused",
                body={{}},
                timeout_seconds=1,
            )
        except RuntimeError as exc:
            assert str(exc) == {TRIPWIRE_ERROR!r}
        else:
            raise AssertionError("provider call was not blocked")
        print("CHILD_PROVIDER_ATTEMPT_BLOCKED")
        """
    )
    result = _run_isolated_pytest(
        tmp_path,
        probe_name="child_provider_counter",
        source=f"""
        import subprocess
        import sys

        CHILD_CODE = {child_code!r}

        def test_child_provider_attempt_is_blocked():
            child = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    CHILD_CODE,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert child.returncode == 0, child.stdout + child.stderr
            assert "CHILD_PROVIDER_ATTEMPT_BLOCKED" in child.stdout
        """,
    )

    output = _output(result)
    assert result.returncode != 0, output
    assert "production provider call count must be zero (observed 1)" in output
