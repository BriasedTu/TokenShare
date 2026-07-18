param(
    [switch] $Full
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

Write-Host "=== TokenShare Startup Verification ==="

$VerificationMode = if ($Full) { "full" } else { "fast" }
Write-Host "Verification mode: $VerificationMode"

$CondaEnv = if ($env:TOKENSHARE_CONDA_ENV) { $env:TOKENSHARE_CONDA_ENV } else { "tokenshare" }
Write-Host "Using conda environment: $CondaEnv"

function Invoke-TokenSharePython {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]] $PythonArgs
    )

    conda run -n $CondaEnv python @PythonArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

function Get-TokenShareFastTests {
    $ManifestPath = "verification/fast-tests.txt"
    if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
        throw "Fast verification manifest not found: $ManifestPath"
    }

    $FastTests = @(
        Get-Content -LiteralPath $ManifestPath -Encoding UTF8 |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ -and -not $_.StartsWith("#") }
    )
    if ($FastTests.Count -eq 0) {
        throw "Fast verification manifest is empty: $ManifestPath"
    }

    $Duplicates = @($FastTests | Group-Object | Where-Object { $_.Count -gt 1 })
    if ($Duplicates.Count -gt 0) {
        $DuplicateNames = ($Duplicates | ForEach-Object { $_.Name }) -join ", "
        throw "Fast verification manifest contains duplicate paths: $DuplicateNames"
    }

    $Missing = @($FastTests | Where-Object { -not (Test-Path -LiteralPath $_) })
    if ($Missing.Count -gt 0) {
        throw "Fast verification manifest contains missing paths: $($Missing -join ', ')"
    }

    return $FastTests
}

Invoke-TokenSharePython -c "import json, sqlite3; print('python-json-sqlite-ok')"

$check = @'
import json
from pathlib import Path

required = [
    "AGENTS.md",
    "feature_list.json",
    "progress.md",
    "session-handoff.md",
    "Doc/agent-navigation.md",
    "Doc/TechnicalDocument/tokenshare_v1_complete_spec.md",
    "Doc/TechnicalDocument/tokenshare_v1_code_map.md",
    "Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md",
    "Doc/TechnicalDocument/2026-06-02-tokenshare-protocol-kernel-revised-draft.md",
]

missing = [path for path in required if not Path(path).exists()]
if missing:
    raise SystemExit(f"Missing required startup files: {missing}")

data = json.loads(Path("feature_list.json").read_text(encoding="utf-8"))
features = data.get("features", [])
if not features:
    raise SystemExit("feature_list.json has no features")
if not any(feature.get("status") == "in-progress" for feature in features):
    raise SystemExit("feature_list.json should have one active in-progress feature")

print("harness-files-ok")
'@

$encodedCheck = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($check))
Invoke-TokenSharePython -c "import base64; exec(base64.b64decode('$encodedCheck').decode('utf-8'))"

Invoke-TokenSharePython -m compileall -q -x "reference_repos" .

if (Test-Path -LiteralPath "tests") {
    $env:PYTHONPATH = "src;$env:PYTHONPATH"
    if ($Full) {
        Invoke-TokenSharePython -m pytest tests
    } else {
        $FastTests = @(Get-TokenShareFastTests)
        $PytestArgs = @("-m", "pytest", "-q") + $FastTests
        Invoke-TokenSharePython @PytestArgs
    }
} else {
    Write-Host "No tests/ directory yet; skipping pytest during startup phase."
}

Write-Host "=== Verification Complete ==="
Write-Host "Next steps:"
Write-Host "1. Read feature_list.json"
Write-Host "2. Work on exactly one feature"
Write-Host "3. Record verification evidence before marking done"
if (-not $Full) {
    Write-Host "4. Run .\init.ps1 -Full before marking a feature complete or publishing results"
}
