"""Every GitHub Action a workflow uses must be pinned to a full commit SHA.

A tag or branch can be moved to other code, which then runs with the job's
token. Dependabot keeps the pins current.

PyYAML is not a test dependency, so these checks read the files line by line.
The workflows use block-style YAML only.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
DEPENDABOT = REPO_ROOT / ".github" / "dependabot.yml"

# `uses:` as a step key (`- uses:` or `uses:`) or a job key (reusable workflow).
USES_RE = re.compile(r"""^\s*(?:-\s+)?uses:\s*(?P<q>["']?)(?P<ref>[^\s"'#]+)(?P=q)\s*(?P<comment>#.*)?$""")
SHA_RE = re.compile(r"[0-9a-f]{40}")
VERSION_COMMENT_RE = re.compile(r"#\s*v\d+\.\d+\.\d+\s*$")


def _workflow_files():
    return sorted(
        p for p in WORKFLOWS_DIR.iterdir() if p.suffix in (".yml", ".yaml")
    )


def _uses_lines(text):
    """Yield (line number, ref, comment) for each `uses:` line."""
    for n, line in enumerate(text.splitlines(), start=1):
        m = USES_RE.match(line)
        if m:
            yield n, m.group("ref"), (m.group("comment") or "")


def _is_exempt(ref):
    return ref.startswith("./") or ref.startswith("docker://")


def _is_pinned(ref):
    if "@" not in ref:
        return False
    _, version = ref.rsplit("@", 1)
    return SHA_RE.fullmatch(version) is not None


def _remote_uses():
    found = []
    for path in _workflow_files():
        for n, ref, comment in _uses_lines(path.read_text()):
            if not _is_exempt(ref):
                found.append((f"{path.name}:{n}", ref, comment))
    return found


def test_workflow_scan_finds_uses_lines():
    assert _workflow_files(), "no workflow files found"
    assert _remote_uses(), "scanner found no remote `uses:` lines"


@pytest.mark.parametrize(
    "line",
    [
        "      - uses: actions/checkout@v4",
        "        uses: docker/login-action@v3.4.0",
        "      - uses: actions/checkout@main",
        "      - uses: actions/checkout@11bd719",
        "      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683x",
        "      - uses: actions/checkout@11BD71901BBE5B1630CEEA73D27597364C9AF683",
        "      - uses: actions/checkout",
        "      - uses: 'actions/checkout@v4'",
    ],
)
def test_scanner_rejects_unpinned_refs(line):
    matches = list(_uses_lines(line))
    assert len(matches) == 1, f"scanner missed: {line!r}"
    _, ref, _ = matches[0]
    assert not _is_exempt(ref)
    assert not _is_pinned(ref)


@pytest.mark.parametrize(
    "line",
    [
        "      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2",
        '        uses: "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"',
    ],
)
def test_scanner_accepts_sha_pins(line):
    (_, ref, _), = _uses_lines(line)
    assert _is_pinned(ref)


@pytest.mark.parametrize(
    "line", ["    uses: ./.github/workflows/tests.yml", "      - uses: docker://alpine:3.20"]
)
def test_scanner_exempts_local_and_docker_refs(line):
    (_, ref, _), = _uses_lines(line)
    assert _is_exempt(ref)


def test_workflow_uses_are_pinned_to_commit_sha():
    unpinned = [f"{where}: {ref}" for where, ref, _ in _remote_uses() if not _is_pinned(ref)]
    assert not unpinned, "actions not pinned to a 40-hex commit SHA:\n" + "\n".join(unpinned)


def test_pinned_uses_have_version_comment():
    missing = [
        f"{where}: {ref}"
        for where, ref, comment in _remote_uses()
        if not VERSION_COMMENT_RE.search(comment)
    ]
    assert not missing, "pinned actions without a `# vX.Y.Z` comment:\n" + "\n".join(missing)


def test_dependabot_covers_github_actions():
    assert DEPENDABOT.is_file(), ".github/dependabot.yml is missing"
    text = DEPENDABOT.read_text()
    assert re.search(r"^version:\s*2\s*$", text, re.M), "dependabot.yml needs `version: 2`"
    blocks = re.split(r"^\s*-\s+(?=package-ecosystem:)", text, flags=re.M)[1:]
    actions = [
        b for b in blocks
        if re.match(r"""package-ecosystem:\s*["']?github-actions["']?\s*$""", b, re.M)
    ]
    assert actions, "dependabot.yml has no github-actions update entry"
    block = actions[0]
    assert re.search(r"""^\s*directory:\s*["']?/["']?\s*$""", block, re.M), "github-actions entry needs directory `/`"
    assert re.search(r"""^\s*target-branch:\s*["']?development["']?\s*$""", block, re.M), (
        "github-actions entry needs `target-branch: development`"
    )
    assert re.search(r"""^\s*interval:\s*["']?(daily|weekly|monthly)["']?\s*$""", block, re.M), (
        "github-actions entry needs a schedule interval"
    )
