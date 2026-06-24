#!/usr/bin/env bash
# Start the HF dataset viewer (backend on :8100, frontend on :3100).
#
# Usage:
#   ./hf-viewer/run.sh            # run from the mh-oan-api project root
#
# Optionally export HF_TOKEN for private/gated datasets (or paste it in the UI):
#   HF_TOKEN=hf_xxx ./hf-viewer/run.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

UVICORN="${UVICORN:-$ROOT_DIR/.venv/bin/uvicorn}"

read_env() {
  # read_env VAR_NAME -> prints the value from the repo .env (no quotes/spaces)
  grep -E "^$1=" "$ROOT_DIR/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d ' '
}

# Auto-load an HF token from the repo .env if one isn't already exported.
# The org-scoped write token has access to private kenpath datasets, so prefer it.
if [ -z "${HF_TOKEN:-}" ]; then
  HF_TOKEN="$(read_env HUGGINGFACE_WRITE_TOKEN)"
  [ -z "$HF_TOKEN" ] && HF_TOKEN="$(read_env HF_TOKEN)"
  export HF_TOKEN
fi
if [ -n "${HF_TOKEN:-}" ]; then
  echo "Using HF token: ${HF_TOKEN:0:6}…"
else
  echo "No HF token found — private/gated datasets will need a token (set HF_TOKEN or paste it in the UI)."
fi

echo "Starting backend on http://localhost:8100 ..."
# Run from the backend dir because the folder name ("hf-viewer") is not an
# importable Python module path.
( cd "$ROOT_DIR/hf-viewer/backend" && "$UVICORN" server:app --port 8100 --reload ) &
BACKEND_PID=$!

cleanup() {
  echo "Stopping..."
  kill "$BACKEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Starting frontend on http://localhost:3100 ..."
cd "$ROOT_DIR/hf-viewer/frontend"
if [ ! -d node_modules ]; then
  echo "Installing frontend dependencies..."
  npm install
fi
npm run dev
