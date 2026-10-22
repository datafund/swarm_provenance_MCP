"""Guards for issue #151: committed editor settings must not run code or auto-approve.

Shared editor settings run on every contributor's machine. A committed
``.claude/*.json`` (other than ``*.local.json``, which is gitignored) or
``.mcp.json`` must not:

* run a hook or MCP server through ``npx``, ``uvx`` or ``pipx`` without an
  exact package version, and
* auto-approve tools (``permissions.allow``) or project MCP servers
  (``enableAllProjectMcpServers``).

Per-developer hooks, servers and permissions belong in gitignored or
user-level configuration. The checks pass when no such files exist.
"""

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

PACKAGE_RUNNERS = {"npx", "uvx", "pipx"}

# npm: name@1.2.3 or @scope/name@1.2.3. Python: name==1.2.3.
PINNED_NPM = re.compile(r"^(@[^/@\s]+/)?[^/@\s]+@\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?$")
PINNED_PYTHON = re.compile(r"^[A-Za-z0-9._\[\],-]+==\d+(\.\d+)*([0-9A-Za-z.+-]*)$")


def committed_settings_files():
    """Shared editor settings files in the repository, excluding *.local.json."""
    files = [REPO_ROOT / ".mcp.json"]
    files += (REPO_ROOT / ".claude").glob("**/*.json")
    return sorted(
        path for path in files if path.is_file() and not path.name.endswith(".local.json")
    )


def command_lines(settings):
    """Every hook command and MCP server command line in a settings dict."""
    lines = []
    for groups in (settings.get("hooks") or {}).values():
        for group in groups or []:
            for hook in group.get("hooks") or []:
                if hook.get("command"):
                    lines.append(hook["command"])
    for server in (settings.get("mcpServers") or {}).values():
        parts = [server.get("command") or ""] + list(server.get("args") or [])
        lines.append(" ".join(parts))
    return lines


def is_pinned(spec):
    return bool(PINNED_NPM.match(spec) or PINNED_PYTHON.match(spec))


def unpinned_runs(command_line):
    """Package specs run by npx/uvx/pipx without an exact version."""
    # Splitting on whitespace and shell separators also reaches commands
    # wrapped in ``sh -c '...'``.
    tokens = [t.strip("'\"") for t in re.split(r"[\s;&|()]+", command_line) if t]
    problems = []
    for index, token in enumerate(tokens):
        if Path(token).name not in PACKAGE_RUNNERS:
            continue
        rest = tokens[index + 1 :]
        if Path(token).name == "pipx" and rest and rest[0] == "run":
            rest = rest[1:]
        spec = next((t for t in rest if not t.startswith("-")), "")
        if not is_pinned(spec):
            problems.append(f"{Path(token).name} {spec or '<no package>'}")
    return problems


def settings_problems(settings):
    """Everything wrong with one settings dict, as readable strings."""
    problems = []
    for line in command_lines(settings):
        problems += [f"unpinned package run: {p} (in {line!r})" for p in unpinned_runs(line)]
    if (settings.get("permissions") or {}).get("allow"):
        problems.append("auto-approve permissions: permissions.allow is set")
    if "enableAllProjectMcpServers" in settings:
        problems.append("enableAllProjectMcpServers is set")
    return problems


def test_committed_editor_settings_are_safe():
    # One test over all files, so it runs (and passes) when there are none.
    problems = {}
    for path in committed_settings_files():
        found = settings_problems(json.loads(path.read_text()))
        if found:
            problems[str(path.relative_to(REPO_ROOT))] = found
    assert problems == {}


def test_local_settings_files_are_not_scanned():
    # *.local.json is gitignored and blocked by the secret-scan workflow.
    for path in committed_settings_files():
        assert not path.name.endswith(".local.json")


@pytest.mark.parametrize(
    "command_line",
    [
        "npx @plur-ai/cli hook-inject",
        "npx @plur-ai/cli hook-observe --post",
        "/bin/sh -lc exec npx -y @plur-ai/mcp@latest",
        "npx -y @plur-ai/mcp@^1.2.0",
        "npx some-package@1",
        "uvx some-tool",
        "uvx some-tool>=1.0",
        "pipx run some-tool",
        "/usr/local/bin/npx -y",
    ],
)
def test_checker_rejects_unpinned_runs(command_line):
    assert unpinned_runs(command_line)


@pytest.mark.parametrize(
    "command_line",
    [
        "npx -y @plur-ai/mcp@1.2.3",
        "npx some-package@0.4.0 --flag",
        "uvx some-tool==1.0.0",
        "pipx run some-tool==2.1",
        "/path/to/venv/bin/python -m swarm_provenance_mcp.server",
        "swarm-provenance-mcp",
    ],
)
def test_checker_accepts_pinned_or_local_commands(command_line):
    assert unpinned_runs(command_line) == []


def test_checker_finds_hook_and_server_commands():
    settings = {
        "hooks": {
            "Stop": [{"hooks": [{"type": "command", "command": "npx @plur-ai/cli hook-learn-check"}]}]
        },
        "mcpServers": {"plur": {"command": "npx", "args": ["-y", "@plur-ai/mcp@latest"]}},
    }
    problems = settings_problems(settings)
    assert len(problems) == 2
    assert all("unpinned package run" in p for p in problems)


def test_checker_rejects_auto_approve_settings():
    assert settings_problems({"permissions": {"allow": ["Bash(git push:*)"]}})
    assert settings_problems({"enableAllProjectMcpServers": True})
    assert settings_problems({"enableAllProjectMcpServers": False})


def test_checker_accepts_minimal_settings():
    assert settings_problems({}) == []
    assert settings_problems({"permissions": {"deny": ["Bash(rm:*)"]}}) == []
    assert settings_problems({"mcpServers": {}}) == []
