"""Guards for issue #151: committed editor settings must not run code or auto-approve.

Shared editor settings take effect on every contributor's machine. The
repository therefore commits no hooks, MCP servers or auto-approval. A
committed editor settings file is any of:

* ``.claude/**/*.json``, except ``*.local.json`` (gitignored, per developer)
* ``.mcp.json``
* ``.cursor/mcp.json`` and ``.cursor/hooks.json``

Such a file must not contain:

* a ``hooks``, ``mcpServers``, ``statusLine`` or ``apiKeyHelper`` key,
  because each one runs a command or connects to an MCP server;
* a non-empty ``permissions.allow``;
* an ``enableAllProjectMcpServers`` or ``enabledMcpjsonServers`` key;
* a ``permissions.defaultMode`` other than ``"default"``.

Per-developer hooks, servers and permissions belong in gitignored or
user-level configuration. The check passes when no such files exist.
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN_KEYS = (
    "hooks",
    "mcpServers",
    "statusLine",
    "apiKeyHelper",
    "enableAllProjectMcpServers",
    "enabledMcpjsonServers",
)


def committed_settings_files(root):
    """Shared editor settings files under ``root``, excluding *.local.json."""
    files = [root / ".mcp.json", root / ".cursor" / "mcp.json", root / ".cursor" / "hooks.json"]
    files += (root / ".claude").glob("**/*.json")
    return sorted(
        path for path in files if path.is_file() and not path.name.endswith(".local.json")
    )


def settings_problems(settings):
    """Every rule a parsed settings file breaks, as readable strings."""
    if not isinstance(settings, dict):
        return [f"top level is a {type(settings).__name__}, expected a JSON object"]
    problems = [f"{key} is set" for key in FORBIDDEN_KEYS if key in settings]
    permissions = settings.get("permissions")
    if permissions is not None and not isinstance(permissions, dict):
        problems.append("permissions is not a JSON object")
    elif permissions:
        if permissions.get("allow"):
            problems.append("permissions.allow is not empty")
        mode = permissions.get("defaultMode", "default")
        if mode != "default":
            problems.append(f"permissions.defaultMode is {mode!r}, expected 'default'")
    return problems


def file_problems(path):
    try:
        settings = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        return [f"cannot read as JSON: {error}"]
    return settings_problems(settings)


def problems_under(root):
    """Map of relative path to problems, for every settings file under ``root``."""
    report = {}
    for path in committed_settings_files(root):
        found = file_problems(path)
        if found:
            report[str(path.relative_to(root))] = found
    return report


def test_committed_editor_settings_are_safe():
    # One test over all files, so it runs (and passes) when there are none.
    assert problems_under(REPO_ROOT) == {}


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data))


def test_scan_covers_shared_files_and_skips_local_ones(tmp_path):
    hooks = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "true"}]}]}}
    _write(tmp_path / ".claude" / "settings.local.json", hooks)
    _write(tmp_path / ".claude" / "nested" / "other.local.json", hooks)
    _write(tmp_path / ".claude" / "settings.json", hooks)
    _write(tmp_path / ".mcp.json", {"mcpServers": {"x": {"url": "https://example.com/mcp"}}})
    _write(tmp_path / ".cursor" / "mcp.json", {"mcpServers": {}})
    _write(tmp_path / ".cursor" / "hooks.json", {"version": 1, "hooks": {}})
    _write(tmp_path / ".cursor" / "settings.json", hooks)  # not an MCP or hooks file

    assert sorted(problems_under(tmp_path)) == [
        ".claude/settings.json",
        ".cursor/hooks.json",
        ".cursor/mcp.json",
        ".mcp.json",
    ]


def test_scan_finds_nothing_in_an_empty_tree(tmp_path):
    assert problems_under(tmp_path) == {}


def test_removed_settings_files_would_fail():
    # The shapes of the .claude/settings.json and .mcp.json removed for #151.
    removed_settings = {
        "hooks": {
            "Stop": [{"hooks": [{"type": "command", "command": "npx @plur-ai/cli hook-learn-check"}]}]
        },
        "mcpServers": {
            "plur": {"command": "/bin/sh", "args": ["-lc", "exec npx -y @plur-ai/mcp@latest"]}
        },
    }
    removed_mcp = {"mcpServers": {"plur": {"command": "npx", "args": ["-y", "@plur-ai/mcp@latest"]}}}
    assert settings_problems(removed_settings) == ["hooks is set", "mcpServers is set"]
    assert settings_problems(removed_mcp) == ["mcpServers is set"]


def test_each_forbidden_key_is_reported():
    for key in FORBIDDEN_KEYS:
        assert settings_problems({key: {}}) == [f"{key} is set"]


def test_auto_approve_permissions_are_reported():
    assert settings_problems({"permissions": {"allow": ["Bash(git push:*)"]}}) == [
        "permissions.allow is not empty"
    ]
    assert settings_problems({"permissions": {"defaultMode": "bypassPermissions"}}) == [
        "permissions.defaultMode is 'bypassPermissions', expected 'default'"
    ]
    assert settings_problems({"permissions": {"defaultMode": "acceptEdits"}})


def test_malformed_files_give_readable_problems(tmp_path):
    assert settings_problems([]) == ["top level is a list, expected a JSON object"]
    assert settings_problems({"permissions": ["Bash"]}) == ["permissions is not a JSON object"]
    # A hooks block of the wrong shape is still just "hooks is set".
    assert settings_problems({"hooks": {"Stop": {"command": "true"}}}) == ["hooks is set"]
    bad = tmp_path / ".mcp.json"
    bad.write_text("{not json")
    assert file_problems(bad)[0].startswith("cannot read as JSON:")


def test_minimal_settings_are_accepted():
    assert settings_problems({}) == []
    assert settings_problems({"permissions": {"allow": []}}) == []
    assert settings_problems({"permissions": {"deny": ["Bash(rm:*)"], "defaultMode": "default"}}) == []
    assert settings_problems({"env": {"PYTHONDONTWRITEBYTECODE": "1"}}) == []
