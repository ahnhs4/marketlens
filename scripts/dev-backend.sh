#!/usr/bin/env bash
# Start the FastAPI backend on :8000 (applies database migrations on startup).
set -euo pipefail
source "$(dirname "$0")/_env.sh"
cd "$ROOT/backend"
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8000}"
