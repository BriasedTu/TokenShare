#!/bin/bash
set -e

FULL=0
case "${1:-}" in
  "") ;;
  --full) FULL=1 ;;
  *)
    echo "Usage: ./init.sh [--full]"
    exit 2
    ;;
esac
if [ "$#" -gt 1 ]; then
  echo "Usage: ./init.sh [--full]"
  exit 2
fi

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

echo "=== TokenShare Startup Verification ==="

if [ "$FULL" -eq 1 ]; then
  VERIFICATION_MODE="full"
else
  VERIFICATION_MODE="fast"
fi
echo "Verification mode: $VERIFICATION_MODE"

CONDA_ENV="${TOKENSHARE_CONDA_ENV:-tokenshare}"
if command -v conda >/dev/null 2>&1; then
  CONDA_CMD="conda"
elif command -v conda.exe >/dev/null 2>&1; then
  CONDA_CMD="conda.exe"
elif [ -x /mnt/c/Users/32133/anaconda3/Scripts/conda.exe ]; then
  CONDA_CMD="/mnt/c/Users/32133/anaconda3/Scripts/conda.exe"
else
  echo "Conda executable not found. Install conda or set PATH so the tokenshare environment can be used."
  exit 1
fi

PYTHON=("$CONDA_CMD" run -n "$CONDA_ENV" python)

echo "Using conda environment: $CONDA_ENV"

"${PYTHON[@]}" -c "import json, sqlite3; print('python-json-sqlite-ok')"

CHECK_SCRIPT=$(cat <<'PY'
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
PY
)
ENCODED_CHECK=$(printf '%s' "$CHECK_SCRIPT" | base64 | tr -d '\n')
"${PYTHON[@]}" -c "import base64; exec(base64.b64decode('$ENCODED_CHECK').decode('utf-8'))"

"${PYTHON[@]}" -m compileall -q -x "reference_repos" .

if [ -d tests ]; then
  if [ "$FULL" -eq 1 ]; then
    PYTEST_ARGS=(tests)
  else
    FAST_MANIFEST="verification/fast-tests.txt"
    if [ ! -f "$FAST_MANIFEST" ]; then
      echo "Fast verification manifest not found: $FAST_MANIFEST"
      exit 1
    fi

    FAST_TESTS=()
    declare -A SEEN_FAST_TESTS=()
    while IFS= read -r line || [ -n "$line" ]; do
      line="${line%$'\r'}"
      if [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]]; then
        continue
      fi
      if [ -n "${SEEN_FAST_TESTS[$line]+present}" ]; then
        echo "Fast verification manifest contains duplicate path: $line"
        exit 1
      fi
      if [ ! -e "$line" ]; then
        echo "Fast verification manifest contains missing path: $line"
        exit 1
      fi
      SEEN_FAST_TESTS["$line"]=1
      FAST_TESTS+=("$line")
    done < "$FAST_MANIFEST"

    if [ "${#FAST_TESTS[@]}" -eq 0 ]; then
      echo "Fast verification manifest is empty: $FAST_MANIFEST"
      exit 1
    fi
    PYTEST_ARGS=(-q "${FAST_TESTS[@]}")
  fi

  if [[ "$CONDA_CMD" == *.exe || "$CONDA_CMD" == */conda.exe ]]; then
    if command -v wslpath >/dev/null 2>&1; then
      PROJECT_ROOT_FOR_PYTHON="$(wslpath -w "$(pwd)")"
    elif command -v cygpath >/dev/null 2>&1; then
      PROJECT_ROOT_FOR_PYTHON="$(cygpath -w "$(pwd)")"
    else
      PROJECT_ROOT_FOR_PYTHON="$(pwd)"
    fi
    SRC_FOR_PYTHON="${PROJECT_ROOT_FOR_PYTHON}\\src"
    "${PYTHON[@]}" -c "import sys, pytest; sys.path.insert(0, r'''$SRC_FOR_PYTHON'''); raise SystemExit(pytest.main(sys.argv[1:]))" "${PYTEST_ARGS[@]}"
  else
    PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}" "${PYTHON[@]}" -m pytest "${PYTEST_ARGS[@]}"
  fi
else
  echo "No tests/ directory yet; skipping pytest during startup phase."
fi

echo "=== Verification Complete ==="
echo "Next steps:"
echo "1. Read feature_list.json"
echo "2. Work on exactly one feature"
echo "3. Record verification evidence before marking done"
if [ "$FULL" -eq 0 ]; then
  echo "4. Run ./init.sh --full before marking a feature complete or publishing results"
fi
