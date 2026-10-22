#!/usr/bin/env bash
# Launcher for MCP clients: starts the server from this repo's venv and
# rebuilds the venv when it no longer works (e.g. the repo was moved or
# renamed, which breaks the editable install). See #131.
#
# stdout is the MCP stdio transport (JSON-RPC), so everything this script
# prints goes to stderr.
set -euo pipefail

# Resolve the real location, so a symlinked run.sh never rebuilds a venv
# next to the symlink.
SRC="${BASH_SOURCE[0]}"
while [ -L "$SRC" ]; do
  LINK_DIR="$(cd -P "$(dirname "$SRC")" && pwd)"
  SRC="$(readlink "$SRC")"
  [[ "$SRC" != /* ]] && SRC="$LINK_DIR/$SRC"
done
DIR="$(cd -P "$(dirname "$SRC")" && pwd)"
VENV="$DIR/venv"

# Does the venv resolve the package from THIS checkout? Run from / so the
# current directory is not on sys.path (it would mask a broken install).
# find_spec only locates modules, it does not import them.
venv_ok() {
  [ -x "$VENV/bin/python" ] || return 1
  (cd / && "$VENV/bin/python" - "$DIR" <<'PY'
import importlib.util, os, sys
spec = importlib.util.find_spec("swarm_provenance_mcp")
ok = spec is not None and spec.origin and os.path.realpath(spec.origin).startswith(
    os.path.realpath(sys.argv[1]) + os.sep
)
sys.exit(0 if ok else 1)
PY
  ) 2>/dev/null
}

# An interpreter that can build the venv: the one the venv was made with if
# it still exists, else python3 — but only if it meets requires-python.
pick_python() {
  local candidates=() cfg="$VENV/pyvenv.cfg" exe
  if [ -f "$cfg" ]; then
    exe="$(sed -n 's/^executable *= *//p' "$cfg")"
    [ -n "$exe" ] && candidates+=("$exe")
  fi
  candidates+=(python3.13 python3.12 python3.11 python3.10 python3)
  for exe in "${candidates[@]}"; do
    if command -v "$exe" >/dev/null 2>&1 &&
       "$exe" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
      command -v "$exe"
      return 0
    fi
  done
  return 1
}

if ! venv_ok; then
  echo "swarm-provenance-mcp: venv missing or broken — rebuilding (first run can take a few minutes)" >&2
  if ! PY="$(pick_python)"; then
    echo "swarm-provenance-mcp: no Python >= 3.10 found on PATH; venv left untouched." >&2
    echo "  Install Python 3.10+ or add it to the PATH your MCP client uses." >&2
    exit 1
  fi
  # Move the old venv aside instead of deleting it until the new one works.
  OLD=""
  if [ -e "$VENV" ]; then
    OLD="$VENV.broken.$$"
    mv "$VENV" "$OLD"
  fi
  {
    "$PY" -m venv "$VENV" &&
      "$VENV/bin/python" -m pip install --upgrade pip &&
      "$VENV/bin/python" -m pip install -e "$DIR"
  } 1>&2 || {
    echo "swarm-provenance-mcp: rebuilding the venv failed (see above).${OLD:+ The previous venv is at $OLD.}" >&2
    exit 1
  }
  [ -n "$OLD" ] && rm -rf "$OLD"
fi

# The server must import before we hand stdout to it; report why if not.
if ! err="$(cd / && "$VENV/bin/python" -c "import swarm_provenance_mcp.server" 2>&1 >/dev/null)"; then
  echo "swarm-provenance-mcp: the server cannot start in $VENV:" >&2
  printf '%s\n' "$err" | tail -n 3 >&2
  echo "  A dependency may be missing from pyproject.toml (see issue #155)." >&2
  exit 1
fi

# config.py reads .env relative to the working directory.
cd "$DIR"
exec "$VENV/bin/python" -m swarm_provenance_mcp.server "$@"
