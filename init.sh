#!/bin/bash
set -e

FULL=0
LEAN_AUDIT=0
FORCE_ALL_LEAN_AUDIT=0
PROFILE=""
ARTIFACT_ROOT=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --full) FULL=1 ;;
    --lean-audit) LEAN_AUDIT=1 ;;
    --force-all-lean-audit)
      LEAN_AUDIT=1
      FORCE_ALL_LEAN_AUDIT=1
      ;;
    --profile)
      [ "$#" -ge 2 ] || { echo "--profile requires a value"; exit 2; }
      PROFILE="$2"
      shift
      ;;
    --artifact-root)
      [ "$#" -ge 2 ] || { echo "--artifact-root requires a value"; exit 2; }
      ARTIFACT_ROOT="$2"
      shift
      ;;
    *)
      echo "Usage: ./init.sh [--full] [--lean-audit] [--force-all-lean-audit] [--profile NAME] [--artifact-root PATH]"
      exit 2
      ;;
  esac
  shift
done

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

if [ "$FULL" -eq 1 ]; then
  VERIFICATION_MODE="full"
else
  VERIFICATION_MODE="fast"
fi

CONDA_ENV="${TOKENSHARE_CONDA_ENV:-tokenshare}"
if command -v conda >/dev/null 2>&1; then
  CONDA_CMD="conda"
elif command -v conda.exe >/dev/null 2>&1; then
  CONDA_CMD="conda.exe"
elif [ -x /mnt/c/Users/32133/anaconda3/Scripts/conda.exe ]; then
  CONDA_CMD="/mnt/c/Users/32133/anaconda3/Scripts/conda.exe"
else
  echo "Conda executable not found."
  exit 1
fi

VERIFY_ARGS=(verification/run_verification.py --mode "$VERIFICATION_MODE")
if [ "$LEAN_AUDIT" -eq 1 ]; then
  VERIFY_ARGS+=(--lean-audit)
fi
if [ "$FORCE_ALL_LEAN_AUDIT" -eq 1 ]; then
  VERIFY_ARGS+=(--force-all-lean-audit)
fi
if [ -n "$PROFILE" ]; then
  VERIFY_ARGS+=(--profile "$PROFILE")
fi
if [ -n "$ARTIFACT_ROOT" ]; then
  VERIFY_ARGS+=(--artifact-root "$ARTIFACT_ROOT")
fi

echo "Using conda environment: $CONDA_ENV"
"$CONDA_CMD" run -n "$CONDA_ENV" python "${VERIFY_ARGS[@]}"
