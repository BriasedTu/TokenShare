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
$env:PYTHONPATH = "src"
$env:PYTHONUTF8 = "1"
$identityArgs = @(
    "run",
    "--no-capture-output",
    "-n",
    "tokenshare",
    "python",
    "-m",
    "tokenshare.experiments.run_paper_experiments",
    "--smoke-profile",
    "benchmarks/paper/paper_smoke_exp1_exp4_profile.v2.json",
    "--output-root",
    $OutputRoot,
    "--ai-api-config",
    "benchmarks/paper/exp1_baseline_provider_config.v3.json",
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
    -SecretValues @()
if ($identityExitCode -ne 0) {
    throw "offline smoke identity preflight failed"
}
$identity = Get-Content -Raw -Encoding UTF8 -LiteralPath (
    $identityStdoutLog
) | ConvertFrom-Json
$semantics = $identity.preregistered_semantics
$runInstance = $identity.run_instance_identity
if ($identity.schema_version -ne "tokenshare.paper_smoke_prelaunch_identity.v1") {
    throw "unexpected smoke prelaunch identity schema"
}
if ($semantics.suite_id -ne "paper_smoke_exp1_exp4_v2") {
    throw "smoke suite identity drift"
}
if ($semantics.profile_digest -ne (
    "sha256:8ca13d1a7730ecc4166b3232d9ba089d4334addd842b1f8fe812792f7facec76"
)) {
    throw "smoke profile identity drift"
}
if ($semantics.catalog_digest -ne (
    "sha256:9293070c526d2912aebf38c85712a5572cc912c9e65f5a02754a3375957704fb"
)) {
    throw "smoke catalog identity drift"
}
if ($semantics.selection_bundle_digest -ne (
    "sha256:5ba85d89c0823a7896eddb3ed2848dd52aede6c9a706a0adcbffa25d8c1516ea"
)) {
    throw "smoke selection identity drift"
}
if ($semantics.provider_config_source_digest -ne (
    "sha256:4bdf0d330c8d01af9760f17b333d75a68f0b8bfad214e807072562e057ef3923"
)) {
    throw "smoke provider config identity drift"
}
if (($semantics.experiment_ids -join ",") -ne (
    "exp1_real_ai_feasibility,exp2_real_ai_scalability," +
    "exp3_real_ai_fault_recovery,exp4_real_ai_protocol_ablation"
)) {
    throw "smoke experiment scope drift"
}
if (
    $semantics.direct_root_runs -ne 21 -or
    $semantics.supporting_worker_death_baselines -ne 1 -or
    $semantics.actual_scheduled_root_runs -ne 22 -or
    $semantics.planned_first_attempt_ai_units -ne 114 -or
    $semantics.budget_provider_attempt_upper_bound -ne 150 -or
    $semantics.provider_retry_limit -ne 0
) {
    throw "smoke denominator or attempt budget drift"
}
if (($semantics.worker_counts -join ",") -ne "1,10,50") {
    throw "smoke worker-count drift"
}
if (($semantics.repeat_ids -join ",") -ne "0") {
    throw "smoke repeat drift"
}
if (
    $semantics.model_endpoint.provider_config_id -ne "exp1_baseline_deepseek" -or
    $semantics.model_endpoint.selected_entry_id -ne (
        "deepseek_v4_pro_exp1_baseline"
    ) -or
    $semantics.model_endpoint.provider_family -ne "deepseek" -or
    $semantics.model_endpoint.provider_model_id -ne "deepseek-v4-pro" -or
    $semantics.model_endpoint.reasoning_profile_id -ne "high" -or
    $semantics.model_endpoint.base_url -ne "https://api.deepseek.com" -or
    $semantics.model_endpoint.endpoint -ne "/chat/completions" -or
    $semantics.model_endpoint.provider_inflight_limit -ne 50
) {
    throw "smoke model endpoint drift"
}
if (
    $semantics.request_limits.max_provider_attempts -ne 1 -or
    $semantics.request_limits.max_tokens -ne 300000 -or
    $semantics.request_limits.timeout_seconds -ne 600 -or
    $semantics.request_limits.stream -ne $false -or
    $semantics.request_limits.thinking.type -ne "enabled" -or
    $semantics.request_limits.reasoning_effort -ne "high"
) {
    throw "smoke request-limit drift"
}
if ($semantics.paper_eligible -ne $false) {
    throw "smoke eligibility drift"
}
$expectedOutputRoot = [IO.Path]::GetFullPath(
    $OutputRoot
).Replace("\", "/")
if ($runInstance.output_root -ne $expectedOutputRoot) {
    throw "smoke run-instance output root drift"
}
if (
    $runInstance.execution_plan_digest -notmatch '^sha256:[0-9a-f]{64}$' -or
    $runInstance.budget_digest -notmatch '^sha256:[0-9a-f]{64}$'
) {
    throw "smoke run-instance digest format is invalid"
}
if ($identity.identity_policy.same_output_root_identity_drift -ne "fail_closed") {
    throw "smoke identity policy drift"
}

$commandArgs = @(
    "run",
    "--no-capture-output",
    "-n",
    "tokenshare",
    "python",
    "-m",
    "tokenshare.experiments.run_paper_experiments",
    "--smoke-profile",
    "benchmarks/paper/paper_smoke_exp1_exp4_profile.v2.json",
    "--output-root",
    $OutputRoot,
    "--real-transport",
    "--ai-api-config",
    "benchmarks/paper/exp1_baseline_provider_config.v3.json",
    "--unlimited-budget",
    "--expect-smoke-execution-plan-digest",
    $runInstance.execution_plan_digest,
    "--expect-smoke-budget-digest",
    $runInstance.budget_digest
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

$prelaunch = [ordered]@{
    schema_version = "tokenshare.paper_smoke_supervision_prelaunch.v1"
    recorded_at_utc = [DateTime]::UtcNow.ToString("o")
    recorded_at_local = (Get-Date).ToString("o")
    suite_id = "paper_smoke_exp1_exp4_v2"
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
    provider_config_path = (
        "benchmarks/paper/exp1_baseline_provider_config.v3.json"
    )
    provider_config_digest = (
        "sha256:4bdf0d330c8d01af9760f17b333d75a68f0b8bfad214e807072562e057ef3923"
    )
    preregistered_semantics = $semantics
    run_instance_identity = $runInstance
    identity_policy = $identity.identity_policy
    identity_preflight_stdout = $identityStdoutLog
    identity_preflight_stderr = $identityStderrLog
    selection_catalog_identity_source = (
        "runner smoke_launch_manifest.json; persisted before first provider dispatch"
    )
    model = "deepseek-v4-pro"
    provider = "deepseek_official"
    endpoint = "https://api.deepseek.com/chat/completions"
    thinking = @{type = "enabled"}
    reasoning_effort = "high"
    timeout_seconds = 600
    max_tokens = 300000
    worker_counts = @(1, 10, 50)
    provider_inflight_limit = 50
    max_provider_attempts_per_ai_unit = 1
    provider_retry_limit = 0
    budget_mode = "unlimited_total_limits_without_changing_request_limits"
    expected_direct_roots = 21
    expected_actual_scheduled_roots = 22
    output_root = $OutputRoot
    paper_eligible = $false
    explicit_exclusions = @(
        "exp5",
        "pilot",
        "formal_experiments",
        "full_experiment_matrix",
        "full_lean",
        "lean_audit",
        "force_all_lean_audit"
    )
    secret_source = (
        "existing user-scope DEEPSEEK_API_KEY; child process environment only"
    )
}
$prelaunch | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (
    Join-Path $SupervisorRoot "prelaunch.json"
) -Encoding UTF8

$stdoutLog = Join-Path $SupervisorRoot "runner.stdout.log"
$stderrLog = Join-Path $SupervisorRoot "runner.stderr.log"
$processRecord = [ordered]@{
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
}
$processRecord | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (
    Join-Path $SupervisorRoot "process.json"
) -Encoding UTF8

$userSecret = [Environment]::GetEnvironmentVariable(
    "DEEPSEEK_API_KEY",
    "User"
)
if ([string]::IsNullOrWhiteSpace($userSecret)) {
    throw "existing user-scope DeepSeek secret is unavailable"
}
$env:DEEPSEEK_API_KEY = $userSecret
$runnerExitCode = 1
$wrapperStatus = "launch_failed"
try {
    $runnerExitCode = Invoke-TokenShareNativeProcessWithLogs `
        -FilePath $condaPath `
        -ArgumentList $commandArgs `
        -StdoutPath $stdoutLog `
        -StderrPath $stderrLog `
        -SecretValues @($userSecret)
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
