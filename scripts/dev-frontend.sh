#!/usr/bin/env bash
# Start the Next.js dev server on :3000 (proxies /api/* to the backend).
set -euo pipefail
source "$(dirname "$0")/_env.sh"
cd "$ROOT/frontend"
exec npx next dev --port "${PORT:-3000}"
