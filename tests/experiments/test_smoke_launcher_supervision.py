import json
import subprocess
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _read_native_log(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig")


def test_runtime_path_helper_preserves_absolute_and_resolves_relative(
    tmp_path: Path,
) -> None:
    helper = REPO_ROOT / "local" / "invoke_native_process_with_logs.ps1"
    absolute = (tmp_path / "absolute-output").resolve()
    relative = Path("relative-output")
    quote = lambda value: str(value).replace("'", "''")
    command = (
        f". '{quote(helper)}'; "
        f"$absolute = Resolve-TokenShareRuntimePath -Path '{quote(absolute)}'; "
        f"$relative = Resolve-TokenShareRuntimePath -Path '{quote(relative)}'; "
        "@{absolute=$absolute;relative=$relative} | ConvertTo-Json"
    )

    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    body = json.loads(result.stdout)

    assert Path(body["absolute"]) == absolute
    assert Path(body["relative"]) == (REPO_ROOT / relative).resolve()
    for launcher_name in (
        "run_exp1_exp4_v3_smoke.ps1",
        "run_exp3_exp4_v3_smoke.ps1",
    ):
        source = (REPO_ROOT / "local" / launcher_name).read_text(encoding="utf-8-sig")
        assert "$OutputRoot = Resolve-TokenShareRuntimePath -Path $OutputRoot" in source
        assert "$SupervisorRoot = Resolve-TokenShareRuntimePath -Path $SupervisorRoot" in source
        assert "Join-Path (Get-Location) $OutputRoot" not in source


def test_native_process_helper_captures_both_streams_and_preserves_exit_code(
    tmp_path: Path,
) -> None:
    helper = REPO_ROOT / "local" / "invoke_native_process_with_logs.ps1"
    launcher = REPO_ROOT / "local" / "run_exp1_exp4_v3_smoke.ps1"
    stdout_path = tmp_path / "runner.stdout.log"
    stderr_path = tmp_path / "runner.stderr.log"
    wrapper_path = tmp_path / "wrapper_terminal.json"
    prelaunch_path = tmp_path / "prelaunch.json"
    process_path = tmp_path / "process.json"
    quote = lambda value: str(value).replace("'", "''")
    command = (
        f". '{quote(helper)}'; "
        "$sentinel = 'sentinel-super-secret-1234567890'; "
        "$env:TOKENSHARE_SENTINEL_SECRET = $sentinel; "
        "$suffix = $sentinel.Substring($sentinel.Length - 8); "
        "$native = Join-Path $PSHOME 'powershell.exe'; "
        "$nativeArgs = @('-NoProfile', '-Command', "
        "\"Write-Output 'stdout-marker'; Write-Output '$sentinel'; "
        "[Console]::Error.WriteLine('stderr-marker'); "
        "[Console]::Error.WriteLine('$suffix'); exit 7\"); "
        f"@{{schema_version='prelaunch.v1';command='dummy'}} | ConvertTo-Json | "
        f"Set-Content -Encoding UTF8 -LiteralPath '{quote(prelaunch_path)}'; "
        f"@{{schema_version='process.v1';command='dummy'}} | ConvertTo-Json | "
        f"Set-Content -Encoding UTF8 -LiteralPath '{quote(process_path)}'; "
        "$code = Invoke-TokenShareNativeProcessWithLogs "
        f"-FilePath $native -ArgumentList $nativeArgs "
        f"-StdoutPath '{quote(stdout_path)}' "
        f"-StderrPath '{quote(stderr_path)}' -SecretValues @($sentinel); "
        f"@{{exit_code=$code}} | ConvertTo-Json | Set-Content "
        f"-Encoding UTF8 -LiteralPath '{quote(wrapper_path)}'"
    )

    subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    assert "stdout-marker" in _read_native_log(stdout_path)
    assert "stderr-marker" in _read_native_log(stderr_path)
    assert json.loads(wrapper_path.read_text(encoding="utf-8-sig"))["exit_code"] == 7
    sentinel = "sentinel-super-secret-1234567890"
    fragments = (sentinel, sentinel[:8], sentinel[-8:])
    for path in (stdout_path, stderr_path):
        content = _read_native_log(path)
        assert all(fragment not in content for fragment in fragments)
    for path in (wrapper_path, prelaunch_path, process_path):
        content = path.read_text(encoding="utf-8-sig")
        assert all(fragment not in content for fragment in fragments)
    launcher_text = launcher.read_text(encoding="utf-8-sig")
    assert "Invoke-TokenShareNativeProcessWithLogs" in launcher_text
    assert "-SecretValues @($userSecret)" in launcher_text
    assert 'Join-Path $SupervisorRoot "wrapper_terminal.json"' in launcher_text


def test_exp1_exp4_launcher_freezes_run_instance_identity_before_secret() -> None:
    launcher = REPO_ROOT / "local" / "run_exp1_exp4_v3_smoke.ps1"
    launcher_text = launcher.read_text(encoding="utf-8-sig")

    assert "--smoke-identity-only" in launcher_text
    assert "preregistered_semantics" in launcher_text
    assert "run_instance_identity" in launcher_text
    assert "same_output_root_identity_drift" in launcher_text
    assert "$semantics.provider_retry_limit -ne 0" in launcher_text
    assert "--expect-smoke-execution-plan-digest" in launcher_text
    assert "--expect-smoke-budget-digest" in launcher_text
    assert launcher_text.index("--smoke-identity-only") < launcher_text.index(
        "GetEnvironmentVariable"
    )
    assert (
        "7c8fcd1cec22432e104639c619b97a756ab0c8a865f5049786eca5db44ec11c1"
        not in launcher_text
    )
    assert (
        "8b206868d2072fded8f0c87e748c418ebd5b3e4a3442e949859460abaab5e978"
        not in launcher_text
    )


def test_native_process_helper_redacts_both_streams_before_live_disk_append(
    tmp_path: Path,
) -> None:
    helper = REPO_ROOT / "local" / "invoke_native_process_with_logs.ps1"
    stdout_path = tmp_path / "live.stdout.log"
    stderr_path = tmp_path / "live.stderr.log"
    ready_path = tmp_path / "child.ready"
    child_path = tmp_path / "long_lived_child.ps1"
    wrapper_path = tmp_path / "invoke_wrapper.ps1"
    long_secret = "sentinel-live-secret-1234567890"
    short_secret = "tiny-key"

    def ps_quote(value: object) -> str:
        return str(value).replace("'", "''")

    child_path.write_text(
        "\n".join(
            (
                f"$longSecret = '{ps_quote(long_secret)}'",
                f"$shortSecret = '{ps_quote(short_secret)}'",
                "[Console]::Out.WriteLine('stdout-marker')",
                "[Console]::Out.WriteLine($longSecret)",
                "[Console]::Out.WriteLine($longSecret.Substring(0, 8))",
                "[Console]::Out.WriteLine($shortSecret)",
                "[Console]::Out.WriteLine('empty-secret-marker')",
                "[Console]::Out.Flush()",
                "[Console]::Error.WriteLine('stderr-marker')",
                "[Console]::Error.WriteLine($longSecret)",
                "[Console]::Error.WriteLine($longSecret.Substring($longSecret.Length - 8))",
                "[Console]::Error.WriteLine($shortSecret)",
                "[Console]::Error.WriteLine('empty-secret-marker')",
                "[Console]::Error.Flush()",
                f"Set-Content -Encoding UTF8 -LiteralPath '{ps_quote(ready_path)}' -Value $PID",
                "while ($true) {",
                "    Start-Sleep -Milliseconds 25",
                "}",
            )
        ),
        encoding="utf-8-sig",
    )
    wrapper_path.write_text(
        "\n".join(
            (
                f". '{ps_quote(helper)}'",
                "$native = Join-Path $PSHOME 'powershell.exe'",
                f"$nativeArgs = @('-NoProfile', '-File', '{ps_quote(child_path)}')",
                f"$code = Invoke-TokenShareNativeProcessWithLogs -FilePath $native -ArgumentList $nativeArgs -StdoutPath '{ps_quote(stdout_path)}' -StderrPath '{ps_quote(stderr_path)}' -SecretValues @('{ps_quote(long_secret)}', '{ps_quote(short_secret)}', '')",
                "exit $code",
            )
        ),
        encoding="utf-8-sig",
    )

    process = subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-File", str(wrapper_path)],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if ready_path.exists() and stdout_path.exists() and stderr_path.exists():
                stdout = _read_native_log(stdout_path)
                stderr = _read_native_log(stderr_path)
                if "stdout-marker" in stdout and "stderr-marker" in stderr:
                    break
            if process.poll() is not None:
                raise AssertionError("long-lived child exited before live-log inspection")
            time.sleep(0.025)
        else:
            raise AssertionError("timed out waiting for live stdout/stderr markers")

        assert process.poll() is None
        fragments = (
            long_secret,
            long_secret[:8],
            long_secret[-8:],
            short_secret,
        )
        assert all(fragment not in stdout for fragment in fragments)
        assert all(fragment not in stderr for fragment in fragments)
        assert "empty-secret-marker" in stdout
        assert "empty-secret-marker" in stderr
    finally:
        if ready_path.exists():
            child_pid = int(ready_path.read_text(encoding="utf-8-sig").strip())
            subprocess.run(
                ["taskkill.exe", "/PID", str(child_pid), "/F"],
                check=False,
                text=True,
                capture_output=True,
            )
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.communicate(timeout=10)
