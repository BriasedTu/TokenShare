import hashlib
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
        "run_exp5_v3_smoke.ps1",
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


def test_smoke_launchers_propagate_runner_exit_code() -> None:
    for launcher_name in (
        "run_exp1_exp4_v3_smoke.ps1",
        "run_exp3_exp4_v3_smoke.ps1",
        "run_exp5_v3_smoke.ps1",
    ):
        source = (REPO_ROOT / "local" / launcher_name).read_text(encoding="utf-8-sig")

        assert "runner_exit_code = $runnerExitCode" in source
        assert source.rstrip().endswith("exit $runnerExitCode")


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


def test_exp5_v3_launcher_freezes_endpoint_identity_and_bundle_contract() -> None:
    launcher = REPO_ROOT / "local" / "run_exp5_v3_smoke.ps1"
    launcher_text = launcher.read_text(encoding="utf-8-sig")

    assert "--smoke-identity-only" in launcher_text
    assert "paper_smoke_exp5_profile.v4.json" in launcher_text
    assert "model_comparison_cohort.v3.json" in launcher_text
    assert "model_comparison_entry_map.v3.json" in launcher_text
    assert "exp5_siliconflow_provider_config.v3.json" in launcher_text
    assert "--local-ai-api-config" in launcher_text
    assert "--expect-smoke-execution-plan-digest" in launcher_text
    assert "--expect-smoke-budget-digest" in launcher_text
    assert "paper_smoke_exp5_v4" in launcher_text
    assert "sha256:ef3b46948dee69ff640d4e21a25c3d84979045e2a8be93d0429fd9474e6b0dd6" in launcher_text
    assert "sha256:eb1a7c33103fe4ef514627c8bf905de5d179e5547d98328fd17b00b62591e9b1" in launcher_text
    assert "$semantics.direct_root_runs -ne 8" in launcher_text
    assert "$semantics.actual_scheduled_root_runs -ne 8" in launcher_text
    assert "$semantics.planned_first_attempt_ai_units -ne 60" in launcher_text
    assert "$semantics.budget_provider_attempt_upper_bound -ne 60" in launcher_text
    assert "$semantics.provider_retry_limit -ne 0" in launcher_text
    assert "$semantics.worker_counts -join \",\"" in launcher_text
    assert '"3"' in launcher_text
    assert "max_in_flight_global = 3" in launcher_text
    assert "zai-org/GLM-5.2" in launcher_text
    assert "Qwen/Qwen3-14B" in launcher_text
    assert "MiniMaxAI/MiniMax-M2.5" in launcher_text
    assert "Pro/deepseek-ai/DeepSeek-V3" in launcher_text
    assert "thinking_budget -ne 32768" in launcher_text
    assert "max_tokens -ne 32768" in launcher_text
    assert "timeout_seconds -ne 600" in launcher_text
    assert "audit/exp5_endpoint_smoke_evidence.json" in launcher_text
    assert "-SecretValues $localSecrets" in launcher_text


def test_exp5_v4_selection_and_smoke_digest_layers_are_distinct_and_bound() -> None:
    selection_path = (
        REPO_ROOT / "benchmarks" / "paper" / "exp5_parent_quarter_selection.v4.json"
    )
    selection_bytes = selection_path.read_bytes()
    selection = json.loads(selection_bytes.decode("utf-8"))
    semantic_selection_digest = selection.pop("selection_digest")

    # selection_digest 绑定 canonical JSON 语义；content digest 绑定 tracked 文件原始字节。
    canonical_selection = json.dumps(
        selection,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert semantic_selection_digest == (
        "sha256:452f25dcc53a1eb0387665c6f451f1320095efb0c4bf154af62bf5e388afb6b2"
    )
    assert "sha256:" + hashlib.sha256(canonical_selection).hexdigest() == (
        semantic_selection_digest
    )
    assert "sha256:" + hashlib.sha256(selection_bytes).hexdigest() == (
        "sha256:e6c5c05e4310b385495ca3dccfcc2d7f38b71841d451726c89a1fe45200beb67"
    )

    profile_path = (
        REPO_ROOT / "benchmarks" / "paper" / "paper_smoke_exp5_profile.v4.json"
    )
    profile_bytes = profile_path.read_bytes()
    profile = json.loads(profile_bytes.decode("utf-8"))
    semantic_profile_digest = profile.pop("profile_digest")
    canonical_profile = json.dumps(
        profile,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert profile["exp5_v3_contract"]["selection_digest"] == (
        semantic_selection_digest
    )
    assert semantic_profile_digest == (
        "sha256:ef3b46948dee69ff640d4e21a25c3d84979045e2a8be93d0429fd9474e6b0dd6"
    )
    assert "sha256:" + hashlib.sha256(canonical_profile).hexdigest() == (
        semantic_profile_digest
    )
    assert "sha256:" + hashlib.sha256(profile_bytes).hexdigest() == (
        "sha256:8378ce5c5a3c76339344088a36f995eda5c862059b9c9eecf5e34347985de5df"
    )

    # launcher 的 eb1a... 是 identity-only 输出对 8-item selection_inventory 的 digest。
    launcher_text = (
        REPO_ROOT / "local" / "run_exp5_v3_smoke.ps1"
    ).read_text(encoding="utf-8-sig")
    assert "selection_bundle_digest" in launcher_text
    assert "sha256:eb1a7c33103fe4ef514627c8bf905de5d179e5547d98328fd17b00b62591e9b1" in (
        launcher_text
    )
    assert "selection bundle identity drift" in launcher_text


def test_native_process_helper_redacts_both_streams_before_live_disk_append(
    tmp_path: Path,
) -> None:
    helper = REPO_ROOT / "local" / "invoke_native_process_with_logs.ps1"
    stdout_path = tmp_path / "live.stdout.log"
    stderr_path = tmp_path / "live.stderr.log"
    ready_path = tmp_path / "child.ready"
    terminate_path = tmp_path / "child.terminate"
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
                f"$terminatePath = '{ps_quote(terminate_path)}'",
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
                "while (-not (Test-Path -LiteralPath $terminatePath)) {",
                "    Start-Sleep -Milliseconds 25",
                "}",
                "exit 17",
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
        terminate_path.write_text("terminate", encoding="utf-8")
        assert process.wait(timeout=10) == 17
    finally:
        if process.poll() is None:
            terminate_path.write_text("terminate", encoding="utf-8")
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
        process.communicate(timeout=5)
