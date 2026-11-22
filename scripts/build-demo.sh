#!/usr/bin/env bash
# Rebuild the public demo from today's data and assemble the folder to upload (resume page + read-only MarketLens).
#   scripts/build-demo.sh              freeze today's answers, check, build, check again   (backend must be running)
#   scripts/build-demo.sh --no-crawl   reuse the answers already frozen, just build
# Nothing is uploaded: the result is var/site/ for you to look at first. Stops at the first failed privacy check.
set -euo pipefail
source "$(dirname "$0")/_env.sh"
cd "$ROOT"
BASE_PATH="${DEMO_BASE_PATH:-/Hyun-Soo-Ahn-Resume/marketlens}"
SITE="$ROOT/var/site"

if [ "${1:-}" != "--no-crawl" ]; then
  curl -sf -m 5 http://127.0.0.1:8000/api/health >/dev/null || { echo "backend is not running - start scripts/dev-backend.sh first"; exit 1; }
  python3 portfolio-site/build_snapshot.py        # numbers shown on the resume page
  python3 portfolio-site/build_app_snapshot.py    # every answer the demo's pages ask for
  (cd backend && PYTHONPATH=. .venv/bin/python ../portfolio-site/build_macro.py)   # the resume page's macro & risk view
fi
python3 portfolio-site/check_public.py

(cd frontend && NEXT_PUBLIC_STATIC=1 NEXT_PUBLIC_BASE_PATH="$BASE_PATH" npx next build)
python3 portfolio-site/check_public.py            # again, now including the built files

mkdir -p "$SITE"
rsync -a --delete --exclude '*.py' --exclude '__pycache__' --exclude 'og-card.html' --exclude 'ideas-data.js' --exclude '.DS_Store' --exclude 'marketlens/' portfolio-site/ "$SITE/"
rsync -a --delete frontend/out-static/ "$SITE/marketlens/"
cp frontend/out-static/404.html "$SITE/404.html"   # GitHub Pages answers every missing path (a stock the demo lacks) with the root 404.html
echo "ready: $SITE ($(du -sh "$SITE" | cut -f1)) - not uploaded"
