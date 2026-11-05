#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3.12}"

if ! command -v "$PYTHON" &>/dev/null; then
  echo "ERROR: $PYTHON not found. Install Python 3.12 (must match Docker base image)."
  exit 1
fi

PY_MINOR=$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if [ "$PY_MINOR" != "3.12" ]; then
  echo "ERROR: need Python 3.12, got $PY_MINOR"
  echo "Set PYTHON=python3.12 if installed under another name."
  exit 1
fi

echo "Creating slither venv with $PYTHON ..."
"$PYTHON" -m venv .venv

# shellcheck disable=SC1091
source .venv/bin/activate

pip install -U pip
pip install -r requirements.txt

echo ""
echo "OK: slither/.venv ready ($(pip list | wc -l) packages)"
echo "Next (offline): make up"
