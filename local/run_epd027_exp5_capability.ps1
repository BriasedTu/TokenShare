param(
    [Parameter(Mandatory = $true)] [string]$PaidReceipt,
    [Parameter(Mandatory = $true)] [ValidateSet("NewRun", "Resume")] [string]$OutputMode,
    [Parameter(Mandatory = $true)] [switch]$AllowProviderCalls,
    [Parameter(Mandatory = $true)] [string]$OutputRoot,
    [Parameter(Mandatory = $true)] [string]$SupervisorRoot,
    [Parameter(Mandatory = $true)] [string]$PlanDigest,
    [Parameter(Mandatory = $true)] [string]$InventoryDigest,
    [string]$ProfilePath = "benchmarks/paper/epd027_pipeline_profile.v1.json"
)

$ErrorActionPreference = "Stop"
if (-not $AllowProviderCalls) {
    [Console]::Error.WriteLine("-AllowProviderCalls must be explicitly true")
    exit 3
}
. (Join-Path $PSScriptRoot "invoke_native_process_with_logs.ps1")
$OutputRoot = Resolve-TokenShareRuntimePath -Path $OutputRoot
$SupervisorRoot = Resolve-TokenShareRuntimePath -Path $SupervisorRoot
$PaidReceipt = Resolve-TokenShareRuntimePath -Path $PaidReceipt
$ExpectedProviderScope = "exp5_capability_smoke"
$receipt = Get-Content -Raw -Encoding UTF8 -LiteralPath $PaidReceipt | ConvertFrom-Json
if ($receipt.scope -ne $ExpectedProviderScope) {
    [Console]::Error.WriteLine("paid receipt scope mismatch for Experiment 5 capability smoke")
    exit 3
}

New-Item -ItemType Directory -Force -Path $SupervisorRoot | Out-Null
$env:PYTHONPATH = "src"
$env:PYTHONUTF8 = "1"
$modeArg = if ($OutputMode -eq "NewRun") { "--new-run" } else { "--resume" }
$commandArgs = @(
    "run", "--no-capture-output", "-n", "tokenshare",
    "python", "-m", "tokenshare.experiments.run_paper_pipeline",
    "run-exp5-capability-smoke",
    "--profile", $ProfilePath,
    "--receipt", $PaidReceipt,
    "--allow-provider-calls",
    $modeArg,
    "--output-root", $OutputRoot,
    "--plan-digest", $PlanDigest,
    "--inventory-digest", $InventoryDigest,
    "--budget-mode", "bounded"
)
$runnerExitCode = Invoke-TokenShareNativeProcessWithLogs `
    -FilePath "conda.exe" -ArgumentList $commandArgs `
    -StdoutPath (Join-Path $SupervisorRoot "runner.stdout.log") `
    -StderrPath (Join-Path $SupervisorRoot "runner.stderr.log") `
    -SecretValues @()
[ordered]@{
    schema_version = "tokenshare.epd027_launcher_terminal.v1"
    scope = $ExpectedProviderScope
    runner_exit_code = $runnerExitCode
} | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath (
    Join-Path $SupervisorRoot "wrapper_terminal.json"
)
exit $runnerExitCode
