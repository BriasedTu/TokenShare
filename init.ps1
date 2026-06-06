$ErrorActionPreference = "Stop"

Write-Host "=== TokenShare Startup Verification ==="

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

Invoke-TokenSharePython -c "import json, sqlite3; print('python-json-sqlite-ok')"

$check = @'
import json
from pathlib import Path

required = [
    "AGENTS.md",
    "feature_list.json",
    "progress.md",
    "session-handoff.md",
    "Doc/TechnicalDocument/2026-06-03-tokenshare-protocol-technical-design.md",
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
    Invoke-TokenSharePython -m pytest tests
} else {
    Write-Host "No tests/ directory yet; skipping pytest during startup phase."
}

Write-Host "=== Verification Complete ==="
Write-Host "Next steps:"
Write-Host "1. Read feature_list.json"
Write-Host "2. Work on exactly one feature"
Write-Host "3. Record verification evidence before marking done"
