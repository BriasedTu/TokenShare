$ErrorActionPreference = "Stop"

Write-Host "=== TokenShare Startup Verification ==="

python -c "import json, sqlite3; print('python-json-sqlite-ok')"

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

$check | python -

python -m compileall -q .

if (Test-Path -LiteralPath "tests") {
    python -m pytest
} else {
    Write-Host "No tests/ directory yet; skipping pytest during startup phase."
}

Write-Host "=== Verification Complete ==="
Write-Host "Next steps:"
Write-Host "1. Read feature_list.json"
Write-Host "2. Work on exactly one feature"
Write-Host "3. Record verification evidence before marking done"
