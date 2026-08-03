import hashlib
import json
import subprocess
import time
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]

OLD_LAUNCHERS = (
    "run_exp1_exp4_v3_smoke.ps1",
    "run_exp3_exp4_v3_smoke.ps1",
    "run_exp5_v3_smoke.ps1",
)
NEW_LAUNCHERS = (
    "run_epd027_l3_checks.ps1",
    "run_epd027_bank_acquisition.ps1",
    "run_epd027_trace_matrix.ps1",
    "run_epd027_exp1_online.ps1",
    "run_epd027_exp5_capability.ps1",
    "run_epd027_exp5_online.ps1",
)


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
    for launcher_name in NEW_LAUNCHERS:
        source = (REPO_ROOT / "local" / launcher_name).read_text(encoding="utf-8-sig")
        assert "$OutputRoot = Resolve-TokenShareRuntimePath -Path $OutputRoot" in source
        assert "$SupervisorRoot = Resolve-TokenShareRuntimePath -Path $SupervisorRoot" in source
        assert "Join-Path (Get-Location) $OutputRoot" not in source


def test_native_process_helper_captures_both_streams_and_preserves_exit_code(
    tmp_path: Path,
) -> None:
    helper = REPO_ROOT / "local" / "invoke_native_process_with_logs.ps1"
    launcher = REPO_ROOT / "local" / "run_epd027_exp1_online.ps1"
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
    assert "-SecretValues @()" in launcher_text
    assert 'Join-Path $SupervisorRoot "wrapper_terminal.json"' in launcher_text


def test_new_launchers_propagate_runner_exit_code() -> None:
    for launcher_name in NEW_LAUNCHERS:
        source = (REPO_ROOT / "local" / launcher_name).read_text(encoding="utf-8-sig")

        assert "runner_exit_code = $runnerExitCode" in source
        assert source.rstrip().endswith("exit $runnerExitCode")


def test_trace_launcher_forwards_bank_and_l3_reconcile_close_authorities() -> None:
    source = (
        REPO_ROOT / "local" / "run_epd027_trace_matrix.ps1"
    ).read_text(encoding="utf-8-sig")

    for parameter, argument in (
        ("$FullBankAcquisitionReceipt", '"--full-bank-acquisition-receipt"'),
        ("$L3OnlineCheckReceipt", '"--l3-online-check-receipt"'),
        ("$L3OnlineCheckRoot", '"--l3-online-check-root"'),
    ):
        assert parameter in source
        assert argument in source
    assert "AllowProviderCalls" not in source
    assert "GetEnvironmentVariable" not in source


def test_old_launchers_exit_two_before_secret_read(tmp_path: Path) -> None:
    for launcher_name in OLD_LAUNCHERS:
        launcher = REPO_ROOT / "local" / launcher_name
        source = launcher.read_text(encoding="utf-8-sig")
        assert "EPD-027" in source
        assert "superseded" in source.lower()
        assert source.index("exit 2") < min(
            source.find(token) if token in source else len(source)
            for token in (
                "GetEnvironmentVariable",
                "ai_api_smoke.local.json",
                "Invoke-TokenShareNativeProcessWithLogs",
                "--real-transport",
            )
        )

        output_root = tmp_path / f"{launcher.stem}-output"
        supervisor_root = tmp_path / f"{launcher.stem}-supervision"
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(launcher),
                "-RunId",
                "supersession-test",
                "-OutputRoot",
                str(output_root),
                "-SupervisorRoot",
                str(supervisor_root),
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
        )
        assert result.returncode == 2
        assert "EPD-027" in result.stdout + result.stderr
        assert not output_root.exists()
        assert not supervisor_root.exists()


def test_l3_launcher_requires_exact_paid_receipt_and_allow_switch(
    tmp_path: Path,
) -> None:
    launcher = REPO_ROOT / "local" / "run_epd027_l3_checks.ps1"
    source = launcher.read_text(encoding="utf-8-sig")

    assert "[switch]$AllowProviderCalls" in source
    assert '"--allow-provider-calls"' in source
    assert '"epd027_l3_capability_and_online_checks"' in source
    assert "$receipt.scope -ne $ExpectedProviderScope" in source
    assert source.index("$receipt.scope -ne $ExpectedProviderScope") < source.index(
        "Invoke-TokenShareNativeProcessWithLogs"
    )

    receipt = tmp_path / "wrong-receipt.json"
    receipt.write_text(json.dumps({"scope": "exp1_full_online"}), encoding="utf-8")
    supervisor_root = tmp_path / "supervision"
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(launcher),
            "-PaidReceipt",
            str(receipt),
            "-OutputMode",
            "NewRun",
            "-AllowProviderCalls",
            "-OutputRoot",
            str(tmp_path / "output"),
            "-SupervisorRoot",
            str(supervisor_root),
            "-PlanDigest",
            "sha256:" + "1" * 64,
            "-InventoryDigest",
            "sha256:" + "2" * 64,
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 3
    assert "scope mismatch" in result.stdout + result.stderr
    assert not supervisor_root.exists()


@pytest.mark.parametrize(
    "launcher_name",
    (
        "run_epd027_l3_checks.ps1",
        "run_epd027_bank_acquisition.ps1",
        "run_epd027_exp1_online.ps1",
        "run_epd027_exp5_capability.ps1",
        "run_epd027_exp5_online.ps1",
    ),
)
def test_provider_launchers_reject_explicit_false_allow_before_receipt_or_child(
    tmp_path: Path,
    launcher_name: str,
) -> None:
    launcher = REPO_ROOT / "local" / launcher_name
    output_root = tmp_path / "output"
    supervisor_root = tmp_path / "supervision"

    def ps_quote(value: object) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    invocation = " ".join(
        (
            "&",
            ps_quote(launcher),
            "-PaidReceipt",
            ps_quote(tmp_path / "receipt-must-not-be-read.json"),
            "-OutputMode NewRun",
            "-AllowProviderCalls:$false",
            "-OutputRoot",
            ps_quote(output_root),
            "-SupervisorRoot",
            ps_quote(supervisor_root),
            "-PlanDigest",
            ps_quote("sha256:" + "1" * 64),
            "-InventoryDigest",
            ps_quote("sha256:" + "2" * 64),
        )
    )
    if launcher_name == "run_epd027_bank_acquisition.ps1":
        invocation += " -PlanBundleRoot " + ps_quote(tmp_path / "plan")
    invocation += "; exit $LASTEXITCODE"

    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            invocation,
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 3
    assert "AllowProviderCalls" in result.stdout + result.stderr
    assert not output_root.exists()
    assert not supervisor_root.exists()


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

    # eb1a... 是 identity-only 输出对 8-item inventory 的 digest，不属于 v4 selection。
    assert semantic_selection_digest != (
        "sha256:eb1a7c33103fe4ef514627c8bf905de5d179e5547d98328fd17b00b62591e9b1"
    )


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
