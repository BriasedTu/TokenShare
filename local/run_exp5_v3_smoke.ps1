param(
    [Parameter(Mandatory = $true)]
    [string]$RunId,
    [Parameter(Mandatory = $true)]
    [string]$OutputRoot,
    [Parameter(Mandatory = $true)]
    [string]$SupervisorRoot
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "invoke_native_process_with_logs.ps1")
$OutputRoot = Resolve-TokenShareRuntimePath -Path $OutputRoot
$SupervisorRoot = Resolve-TokenShareRuntimePath -Path $SupervisorRoot

if (Test-Path -LiteralPath $OutputRoot) {
    throw "output root already exists"
}
if (Test-Path -LiteralPath $SupervisorRoot) {
    throw "supervisor root already exists"
}
New-Item -ItemType Directory -Path $SupervisorRoot | Out-Null

$condaPath = "C:\Users\32133\anaconda3\Scripts\conda.exe"
$profilePath = "benchmarks/paper/paper_smoke_exp5_profile.v4.json"
$cohortPath = "benchmarks/paper/model_comparison_cohort.v3.json"
$entryMapPath = "benchmarks/paper/model_comparison_entry_map.v3.json"
$providerConfigPath = (
    "benchmarks/paper/exp5_siliconflow_provider_config.v3.json"
)
$localConfigPath = "local/ai_api_smoke.local.json"
$bundleRelativePath = "audit/exp5_endpoint_smoke_evidence.json"
$localConfig = Get-Content -Raw -Encoding UTF8 -LiteralPath (
    $localConfigPath
) | ConvertFrom-Json
$localSecrets = @(
    $localConfig.entries |
        Where-Object {
            $_.enabled -eq $true -and
            -not [string]::IsNullOrWhiteSpace([string]$_.api_key)
        } |
        ForEach-Object { [string]$_.api_key } |
        Sort-Object -Unique
)
if ($localSecrets.Count -eq 0) {
    throw "gitignored Exp5 local API secret is unavailable"
}

$env:PYTHONPATH = "src"
$env:PYTHONUTF8 = "1"
$identityArgs = @(
    "run", "--no-capture-output", "-n", "tokenshare",
    "python", "-m", "tokenshare.experiments.run_paper_experiments",
    "--smoke-profile", $profilePath,
    "--output-root", $OutputRoot,
    "--model-cohort-file", $cohortPath,
    "--model-entry-map", $entryMapPath,
    "--provider-config", ("siliconflow=" + $providerConfigPath),
    "--local-ai-api-config", $localConfigPath,
    "--unlimited-budget",
    "--smoke-identity-only"
)
$identityStdoutLog = Join-Path $SupervisorRoot "identity_preflight.stdout.log"
$identityStderrLog = Join-Path $SupervisorRoot "identity_preflight.stderr.log"
$identityExitCode = Invoke-TokenShareNativeProcessWithLogs `
    -FilePath $condaPath `
    -ArgumentList $identityArgs `
    -StdoutPath $identityStdoutLog `
    -StderrPath $identityStderrLog `
    -SecretValues $localSecrets
if ($identityExitCode -ne 0) {
    throw "offline Exp5 smoke identity preflight failed"
}

$identity = Get-Content -Raw -Encoding UTF8 -LiteralPath (
    $identityStdoutLog
) | ConvertFrom-Json
$semantics = $identity.preregistered_semantics
$runInstance = $identity.run_instance_identity
if ($identity.schema_version -ne "tokenshare.paper_smoke_prelaunch_identity.v1") {
    throw "unexpected smoke prelaunch identity schema"
}
if ($semantics.suite_id -ne "paper_smoke_exp5_v4") {
    throw "Exp5 smoke suite identity drift"
}
if ($semantics.profile_digest -ne (
    "sha256:ef3b46948dee69ff640d4e21a25c3d84979045e2a8be93d0429fd9474e6b0dd6"
)) {
    throw "Exp5 smoke profile identity drift"
}
if ($semantics.catalog_digest -ne (
    "sha256:9293070c526d2912aebf38c85712a5572cc912c9e65f5a02754a3375957704fb"
)) {
    throw "Exp5 smoke catalog identity drift"
}
if ($semantics.selection_bundle_digest -ne (
    "sha256:eb1a7c33103fe4ef514627c8bf905de5d179e5547d98328fd17b00b62591e9b1"
)) {
    # 这是 8 个 smoke item 的 selection inventory digest，不是 v4 selection 文件内的 selection_digest。
    throw "Exp5 smoke selection bundle identity drift"
}
if (($semantics.experiment_ids -join ",") -ne (
    "exp5_real_ai_model_endpoint_comparison"
)) {
    throw "Exp5 smoke experiment scope drift"
}
if (
    $semantics.direct_root_runs -ne 8 -or
    $semantics.supporting_baseline_root_runs -ne 0 -or
    $semantics.supporting_baseline_ai_units -ne 0 -or
    $semantics.supporting_provider_attempt_upper_bound -ne 0 -or
    $semantics.actual_scheduled_root_runs -ne 8 -or
    $semantics.planned_first_attempt_ai_units -ne 60 -or
    $semantics.budget_provider_attempt_upper_bound -ne 60 -or
    $semantics.provider_retry_limit -ne 0
) {
    throw "Exp5 smoke denominator or attempt budget drift"
}
if (($semantics.worker_counts -join ",") -ne "3") {
    throw "Exp5 smoke worker-count drift"
}
if (($semantics.repeat_ids -join ",") -ne "0") {
    throw "Exp5 smoke repeat drift"
}
if ($semantics.paper_eligible -ne $false) {
    throw "Exp5 smoke eligibility drift"
}

$cohort = $semantics.model_endpoint_cohort
if ($cohort.cohort_id -ne "tokenshare.paper.model_endpoint_cohort.v3") {
    throw "Exp5 cohort identity drift"
}
if ($cohort.model_cohort_digest -ne (
    "sha256:1b317c9827d87c43b974b77c469b115906da463a79064d3ffb3b949dc5257942"
)) {
    throw "Exp5 cohort digest drift"
}
$expectedModels = [ordered]@{
    glm_5_2_siliconflow = "zai-org/GLM-5.2"
    qwen3_14b_siliconflow = "Qwen/Qwen3-14B"
    minimax_m2_5_siliconflow = "MiniMaxAI/MiniMax-M2.5"
    deepseek_v3_pro_siliconflow = "Pro/deepseek-ai/DeepSeek-V3"
}
$memberEndpoints = @($cohort.member_endpoints)
if ($memberEndpoints.Count -ne 4) {
    throw "Exp5 cohort member count drift"
}
foreach ($member in $memberEndpoints) {
    $expectedModel = $expectedModels[$member.cohort_member_id]
    if (
        [string]::IsNullOrWhiteSpace([string]$expectedModel) -or
        $member.provider_config_id -ne "siliconflow" -or
        $member.provider_family -ne "siliconflow" -or
        $member.provider_model_id -ne $expectedModel -or
        $member.source_provider_config_digest -ne (
            "sha256:6b1d6ffe977340ebbf59d69368d8c977fef664007575de72593c08c038d5ffe1"
        )
    ) {
        throw "Exp5 cohort endpoint drift"
    }
    $comparable = $member.request_controls.comparable
    if (
        $comparable.max_provider_attempts -ne 1 -or
        $comparable.max_tokens -ne 32768 -or
        $comparable.timeout_seconds -ne 600 -or
        $comparable.stream -ne $false -or
        $comparable.temperature -ne 0.0 -or
        $comparable.top_p -ne 1.0
    ) {
        throw "Exp5 request-control drift"
    }
    $reasoning = $member.effective_reasoning_controls
    if ($member.cohort_member_id -eq "deepseek_v3_pro_siliconflow") {
        if ($reasoning.enable_thinking -ne $false) {
            throw "Exp5 DeepSeek nonthinking drift"
        }
    }
    elseif (
        $reasoning.enable_thinking -ne $true -or
        $reasoning.thinking_budget -ne 32768
    ) {
        throw "Exp5 thinking-budget drift"
    }
}
if ($cohort.request_controls_snapshot.max_tokens -ne 32768) {
    throw "Exp5 comparable max_tokens drift"
}
if ($cohort.request_controls_snapshot.timeout_seconds -ne 600) {
    throw "Exp5 comparable timeout_seconds drift"
}

$expectedOutputRoot = [IO.Path]::GetFullPath($OutputRoot).Replace("\", "/")
if ($runInstance.output_root -ne $expectedOutputRoot) {
    throw "Exp5 smoke run-instance output root drift"
}
if (
    $runInstance.execution_plan_digest -notmatch '^sha256:[0-9a-f]{64}$' -or
    $runInstance.budget_digest -notmatch '^sha256:[0-9a-f]{64}$'
) {
    throw "Exp5 smoke run-instance digest format is invalid"
}
if ($identity.identity_policy.same_output_root_identity_drift -ne "fail_closed") {
    throw "Exp5 smoke identity policy drift"
}

$commandArgs = @(
    "run", "--no-capture-output", "-n", "tokenshare",
    "python", "-m", "tokenshare.experiments.run_paper_experiments",
    "--smoke-profile", $profilePath,
    "--output-root", $OutputRoot,
    "--real-transport",
    "--model-cohort-file", $cohortPath,
    "--model-entry-map", $entryMapPath,
    "--provider-config", ("siliconflow=" + $providerConfigPath),
    "--local-ai-api-config", $localConfigPath,
    "--unlimited-budget",
    "--expect-smoke-execution-plan-digest", $runInstance.execution_plan_digest,
    "--expect-smoke-budget-digest", $runInstance.budget_digest
)
$commandText = $condaPath + " " + ($commandArgs -join " ")
$statusLines = @(git status --short)
$statusBytes = [Text.Encoding]::UTF8.GetBytes(($statusLines -join "`n"))
$statusDigestBytes = [Security.Cryptography.SHA256]::Create().ComputeHash(
    $statusBytes
)
$statusDigest = "sha256:" + (($statusDigestBytes | ForEach-Object {
    $_.ToString("x2")
}) -join "")

[ordered]@{
    schema_version = "tokenshare.paper_smoke_supervision_prelaunch.v1"
    recorded_at_utc = [DateTime]::UtcNow.ToString("o")
    recorded_at_local = (Get-Date).ToString("o")
    suite_id = "paper_smoke_exp5_v4"
    run_id = $RunId
    generation_id = "initial_launch"
    checkpoint_identities = @()
    command = $commandText
    git_commit = (git rev-parse HEAD)
    commit_less_diff_summary = [ordered]@{
        dirty = ($statusLines.Count -gt 0)
        status_line_count = $statusLines.Count
        status_digest = $statusDigest
    }
    provider_config_path = $providerConfigPath
    provider_config_digest = (
        "sha256:6b1d6ffe977340ebbf59d69368d8c977fef664007575de72593c08c038d5ffe1"
    )
    preregistered_semantics = $semantics
    run_instance_identity = $runInstance
    identity_policy = $identity.identity_policy
    identity_preflight_stdout = $identityStdoutLog
    identity_preflight_stderr = $identityStderrLog
    model_cohort_id = "tokenshare.paper.model_endpoint_cohort.v3"
    expected_models = $expectedModels
    timeout_seconds = 600
    max_tokens = 32768
    worker_counts = @(3)
    max_in_flight_global = 3
    max_provider_attempts_per_ai_unit = 1
    provider_retry_limit = 0
    expected_direct_roots = 8
    expected_actual_scheduled_roots = 8
    expected_smoke_evidence_bundle = $bundleRelativePath
    output_root = $OutputRoot
    paper_eligible = $false
    secret_source = (
        "gitignored local/ai_api_smoke.local.json; child Python process only"
    )
} | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (
    Join-Path $SupervisorRoot "prelaunch.json"
) -Encoding UTF8

$stdoutLog = Join-Path $SupervisorRoot "runner.stdout.log"
$stderrLog = Join-Path $SupervisorRoot "runner.stderr.log"
[ordered]@{
    schema_version = "tokenshare.paper_smoke_process.v1"
    recorded_at_utc = [DateTime]::UtcNow.ToString("o")
    recorded_at_local = (Get-Date).ToString("o")
    pid = $PID
    process_name = "powershell"
    status = "started"
    command = $commandText
    output_root = $OutputRoot
    stdout_log = $stdoutLog
    stderr_log = $stderrLog
} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (
    Join-Path $SupervisorRoot "process.json"
) -Encoding UTF8

$runnerExitCode = 1
$wrapperStatus = "launch_failed"
try {
    $runnerExitCode = Invoke-TokenShareNativeProcessWithLogs `
        -FilePath $condaPath `
        -ArgumentList $commandArgs `
        -StdoutPath $stdoutLog `
        -StderrPath $stderrLog `
        -SecretValues $localSecrets
    if ($runnerExitCode -eq 0) {
        $bundlePath = Join-Path $OutputRoot $bundleRelativePath
        if (-not (Test-Path -LiteralPath $bundlePath)) {
            $runnerExitCode = 2
            throw "completed Exp5 smoke did not publish its evidence bundle"
        }
    }
    $wrapperStatus = "exited"
}
finally {
    [ordered]@{
        schema_version = "tokenshare.paper_smoke_wrapper_terminal.v1"
        recorded_at_utc = [DateTime]::UtcNow.ToString("o")
        recorded_at_local = (Get-Date).ToString("o")
        wrapper_status = $wrapperStatus
        runner_exit_code = $runnerExitCode
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (
        Join-Path $SupervisorRoot "wrapper_terminal.json"
    ) -Encoding UTF8
}
exit $runnerExitCode
