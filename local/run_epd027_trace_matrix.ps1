param(
    [Parameter(Mandatory = $true)] [string]$OutputRoot,
    [Parameter(Mandatory = $true)] [string]$SupervisorRoot,
    [Parameter(Mandatory = $true)] [string]$ExternalBankRoot,
    [Parameter(Mandatory = $true)] [string]$PlanBundleRoot,
    [Parameter(Mandatory = $true)] [string]$PlanDigest,
    [Parameter(Mandatory = $true)] [string]$InventoryDigest,
    [string]$FullBankAcquisitionReceipt,
    [string]$L3OnlineCheckReceipt,
    [string]$L3OnlineCheckRoot,
    [string]$ProfilePath = "benchmarks/paper/epd027_pipeline_profile.v1.json"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "invoke_native_process_with_logs.ps1")
$OutputRoot = Resolve-TokenShareRuntimePath -Path $OutputRoot
$SupervisorRoot = Resolve-TokenShareRuntimePath -Path $SupervisorRoot
$ExternalBankRoot = Resolve-TokenShareRuntimePath -Path $ExternalBankRoot
$PlanBundleRoot = Resolve-TokenShareRuntimePath -Path $PlanBundleRoot
if ($FullBankAcquisitionReceipt) {
    $FullBankAcquisitionReceipt = Resolve-TokenShareRuntimePath -Path $FullBankAcquisitionReceipt
}
if ($L3OnlineCheckReceipt) {
    $L3OnlineCheckReceipt = Resolve-TokenShareRuntimePath -Path $L3OnlineCheckReceipt
}
if ($L3OnlineCheckRoot) {
    $L3OnlineCheckRoot = Resolve-TokenShareRuntimePath -Path $L3OnlineCheckRoot
}
New-Item -ItemType Directory -Force -Path $SupervisorRoot | Out-Null
$env:PYTHONPATH = "src"
$env:PYTHONUTF8 = "1"

$commandArgs = @(
    "run", "--no-capture-output", "-n", "tokenshare",
    "python", "-m", "tokenshare.experiments.run_paper_pipeline",
    "run-trace",
    "--profile", $ProfilePath,
    "--external-bank-root", $ExternalBankRoot,
    "--output-root", $OutputRoot,
    "--plan-bundle-root", $PlanBundleRoot,
    "--plan-digest", $PlanDigest,
    "--inventory-digest", $InventoryDigest
)
if ($FullBankAcquisitionReceipt) {
    $commandArgs += @("--full-bank-acquisition-receipt", $FullBankAcquisitionReceipt)
}
if ($L3OnlineCheckReceipt) {
    $commandArgs += @("--l3-online-check-receipt", $L3OnlineCheckReceipt)
}
if ($L3OnlineCheckRoot) {
    $commandArgs += @("--l3-online-check-root", $L3OnlineCheckRoot)
}
$runnerExitCode = Invoke-TokenShareNativeProcessWithLogs `
    -FilePath "conda.exe" -ArgumentList $commandArgs `
    -StdoutPath (Join-Path $SupervisorRoot "runner.stdout.log") `
    -StderrPath (Join-Path $SupervisorRoot "runner.stderr.log") `
    -SecretValues @()
[ordered]@{
    schema_version = "tokenshare.epd027_launcher_terminal.v1"
    scope = "real_model_trace_protocol_run"
    runner_exit_code = $runnerExitCode
} | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath (
    Join-Path $SupervisorRoot "wrapper_terminal.json"
)
exit $runnerExitCode
