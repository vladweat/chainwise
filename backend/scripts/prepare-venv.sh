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

echo "Creating venv with $PYTHON ..."
"$PYTHON" -m venv .venv

# shellcheck disable=SC1091
source .venv/bin/activate

pip install -U pip
pip install -r requirements.txt

mkdir -p .libs .models
if [ ! -f .libs/libgomp.so.1 ]; then
  for src in /lib/x86_64-linux-gnu/libgomp.so.1 /usr/lib/x86_64-linux-gnu/libgomp.so.1; do
    if [ -e "${src}" ]; then
      cp -L "${src}" .libs/libgomp.so.1
      echo "Copied libgomp.so.1 into .libs (needed by onnxruntime in Docker slim)"
      break
    fi
  done
  if [ ! -f .libs/libgomp.so.1 ]; then
    echo "WARN: libgomp.so.1 not found on host; MiniLM embeddings may fail in Docker"
  fi
fi

echo "Installing solc ${SOLC_VERSION:-0.8.24} into .solc ..."
python scripts/prepare_solc.py

echo "Downloading MiniLM embeddings into .models ..."
python scripts/prepare_embeddings.py

echo ""
echo "OK: backend/.venv ready ($(pip list | wc -l) packages)"
echo "Next (offline): make up"
