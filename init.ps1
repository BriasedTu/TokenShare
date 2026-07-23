param(
    [switch] $Full,
    [switch] $LeanAudit,
    [switch] $ForceAllLeanAudit
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$CondaEnv = if ($env:TOKENSHARE_CONDA_ENV) { $env:TOKENSHARE_CONDA_ENV } else { "tokenshare" }
$VerificationMode = if ($Full) { "full" } else { "fast" }
$VerificationArgs = @("verification/run_verification.py", "--mode", $VerificationMode)
if ($LeanAudit -or $ForceAllLeanAudit) {
    $VerificationArgs += "--lean-audit"
}
if ($ForceAllLeanAudit) {
    $VerificationArgs += "--force-all-lean-audit"
}

Write-Host "Using conda environment: $CondaEnv"
conda run -n $CondaEnv python @VerificationArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Next steps:"
Write-Host "1. Read feature_list.json"
Write-Host "2. Work on exactly one feature"
Write-Host "3. Record verification evidence before marking done"
if (-not $Full) {
    Write-Host "4. Run .\init.ps1 -Full before marking a feature complete"
}
