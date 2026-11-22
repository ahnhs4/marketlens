# Shared by the scripts: prefer the project-local runtimes in .tools/ (no system install needed).
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -x "$ROOT/.tools/node/bin/node" ]; then export PATH="$ROOT/.tools/node/bin:$PATH"; fi
if [ -x "$ROOT/.tools/uv/uv" ]; then export PATH="$ROOT/.tools/uv:$PATH"; fi
export UV_PYTHON_INSTALL_DIR="$ROOT/.tools/python"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$ROOT/.tools/uv-cache}"
