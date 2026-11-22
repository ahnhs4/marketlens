#!/usr/bin/env bash
# Checks that every external data source is reachable from this Mac and records
# the live response formats to var/probe_results.json (used to verify parsers).
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv (Python package/runtime manager) into ~/.local/bin ..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
mkdir -p var
uv run --python 3.12 --no-project --with "yfinance>=1.0" --with httpx --with lxml --with pandas \
  python scripts/probe_data_sources.py 2>&1 | tee var/probe.log
echo "PROBE_DONE" >> var/probe.log
