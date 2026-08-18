#!/usr/bin/env bash
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$DIR/venv"

# Recreate venv if broken (e.g., after moving/renaming the repo directory)
if ! "$VENV/bin/python" -c "import sys" 2>/dev/null; then
  echo "venv broken or missing — recreating..." >&2
  rm -rf "$VENV"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -e "$DIR"
fi

exec "$VENV/bin/python" -m swarm_provenance_mcp.server "$@"
