#!/usr/bin/env bash
# One-time setup: Python virtualenv (backend) + npm packages (frontend).
set -euo pipefail
source "$(dirname "$0")/_env.sh"
cd "$ROOT/backend"
uv venv --python 3.12 --allow-existing .venv
uv pip install --python .venv/bin/python -r requirements.lock.txt
cd "$ROOT/frontend"
npm ci --no-audit --no-fund
[ -f "$ROOT/.env" ] || cp "$ROOT/.env.example" "$ROOT/.env"
echo "Setup complete. Start with: scripts/dev-backend.sh  and  scripts/dev-frontend.sh"
