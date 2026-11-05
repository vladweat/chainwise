#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v npm &>/dev/null; then
  echo "ERROR: npm not found. Install Node.js 20+."
  exit 1
fi

echo "Installing frontend dependencies..."
npm install

echo ""
echo "OK: frontend/node_modules ready"
echo "Next (offline): make up"
