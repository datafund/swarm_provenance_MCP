#!/usr/bin/env python3
"""Start the MCP server over stdio and check that it answers ``tools/list``.

Usage:
    python scripts/mcp_stdio_smoke.py -- swarm-provenance-mcp
    python scripts/mcp_stdio_smoke.py -- docker run -i --rm IMAGE

Sends ``initialize``, ``notifications/initialized`` and ``tools/list``, then
checks that the server returned the core tools. Exits non-zero (and prints the
server's stderr) if the server fails to start or answers wrongly. Uses only
the standard library so it can run against an image without installing the
package.
"""

import json
import queue
import subprocess
import sys
import threading
import time

CORE_TOOLS = {"health_check", "purchase_stamp", "upload_data", "download_data"}

MESSAGES = [
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "stdio-smoke", "version": "1.0.0"},
        },
    },
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
]


def _send(proc, message):
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()


def run_smoke(cmd, timeout=120):
    """Run the handshake against ``cmd``. Returns (ok, message).

    Stdin stays open until each reply arrives: the server exits on EOF and
    would drop a request that is still in flight.
    """
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        return False, f"could not run {cmd!r}: {exc}"

    lines = queue.Queue()
    stderr_chunks = []
    threading.Thread(
        target=lambda: [lines.put(line) for line in proc.stdout] + [lines.put(None)],
        daemon=True,
    ).start()
    stderr_reader = threading.Thread(
        target=lambda: stderr_chunks.extend(proc.stderr), daemon=True
    )
    stderr_reader.start()
    deadline = time.monotonic() + timeout
    stdout_seen = []

    def wait_for(msg_id):
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                line = lines.get(timeout=remaining)
            except queue.Empty:
                return None
            if line is None:  # server closed stdout
                return None
            stdout_seen.append(line)
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict) and msg.get("id") == msg_id:
                return msg

    def finish():
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        stderr_reader.join(timeout=5)

    def fail(reason):
        finish()
        return False, (
            f"{reason}\nexit code: {proc.returncode}\n"
            f"stdout:\n{''.join(stdout_seen)[-4000:]}\n"
            f"stderr:\n{''.join(stderr_chunks)[-4000:]}"
        )

    try:
        _send(proc, MESSAGES[0])
        init = wait_for(1)
        if not init or "result" not in init:
            return fail("no initialize result")
        _send(proc, MESSAGES[1])
        _send(proc, MESSAGES[2])
        listing = wait_for(2)
    except (BrokenPipeError, OSError) as exc:
        return fail(f"server stopped reading stdin: {exc}")
    tools = (listing or {}).get("result", {}).get("tools")
    if not tools:
        return fail("no tools/list result")
    names = {t.get("name") for t in tools}
    missing = CORE_TOOLS - names
    if missing:
        return fail(f"tools/list is missing {sorted(missing)}")
    finish()
    server = init["result"].get("serverInfo", {})
    return True, (
        f"ok: {server.get('name')} {server.get('version')} answered "
        f"initialize and tools/list with {len(tools)} tools"
    )


def main(argv):
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    ok, message = run_smoke(argv)
    print(message, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
