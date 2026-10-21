"""Guards for issue #155: a fresh install must start, and tests stay off production.

* Every third-party module the package imports is a declared dependency, and
  the dependencies that change protocol or signing behaviour are upper-bounded.
* The installed ``swarm-provenance-mcp`` entry point answers ``initialize`` and
  ``tools/list`` over stdio.
* The test run is pointed away from the production gateway.
"""

import ast
import importlib.metadata as metadata
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import requests
import requests.adapters

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = REPO_ROOT / "swarm_provenance_mcp"
PRODUCTION_GATEWAY_URL = "https://provenance-gateway.datafund.io"

# Dependencies whose next major (or, for 0.x, minor) release can break server
# registration or change how transactions are signed.
MUST_BE_UPPER_BOUNDED = {"mcp", "web3", "eth-account", "pydantic-settings"}


def _normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _declared_dependencies():
    tomllib = pytest.importorskip("tomllib")
    with open(REPO_ROOT / "pyproject.toml", "rb") as fh:
        project = tomllib.load(fh)["project"]
    deps = {}
    for spec in project["dependencies"]:
        match = re.match(r"\s*([A-Za-z0-9_.\-]+)\s*(.*)", spec)
        deps[_normalize(match.group(1))] = match.group(2)
    return deps


def _third_party_imports():
    """Top-level module names imported anywhere in the package."""
    names = set()
    for path in PACKAGE_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names.add(node.module.split(".")[0])
    return {
        n
        for n in names
        if n not in sys.stdlib_module_names and n != "swarm_provenance_mcp"
    }


class TestDeclaredDependencies:
    def test_every_imported_package_is_declared(self):
        declared = _declared_dependencies()
        providers = metadata.packages_distributions()
        undeclared = {}
        for module in sorted(_third_party_imports()):
            dists = {_normalize(d) for d in providers.get(module, [module])}
            if not dists & declared.keys():
                undeclared[module] = sorted(dists)
        assert not undeclared, (
            "imported by swarm_provenance_mcp but missing from "
            f"[project.dependencies]: {undeclared}"
        )

    @pytest.mark.parametrize("name", sorted(MUST_BE_UPPER_BOUNDED))
    def test_dependency_has_upper_bound(self, name):
        declared = _declared_dependencies()
        assert name in declared, f"{name} is not declared"
        assert "<" in declared[name], (
            f"{name}{declared[name]} has no upper bound; a new major release "
            "could break the server or change signing without a test run"
        )

    def test_mcp_major_version_is_1(self):
        """Server registration (``@server.list_tools()``) is written for mcp 1.x."""
        declared = _declared_dependencies()
        assert re.search(r"<\s*2(\D|$)", declared["mcp"]), declared["mcp"]


def _server_command():
    script = shutil.which("swarm-provenance-mcp", path=str(Path(sys.executable).parent))
    if script:
        return [script]
    return [sys.executable, "-m", "swarm_provenance_mcp.server"]


@pytest.mark.smoke
def test_installed_server_answers_tools_list_over_stdio():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        from mcp_stdio_smoke import run_smoke
    finally:
        sys.path.pop(0)

    env_cmd = _server_command()
    # The subprocess inherits SWARM_GATEWAY_URL from conftest (not production).
    ok, message = run_smoke(env_cmd, timeout=120)
    assert ok, message


class TestTestsStayOffProduction:
    def test_default_gateway_url_is_not_production(self):
        from swarm_provenance_mcp.config import settings

        host = urlsplit(settings.swarm_gateway_url).hostname
        assert (
            host != urlsplit(PRODUCTION_GATEWAY_URL).hostname
        ), "tests are configured against the production gateway"
        assert os.environ["SWARM_GATEWAY_URL"] == settings.swarm_gateway_url

    def test_requests_to_production_gateway_are_blocked(
        self, blocked_production_requests
    ):
        # Check the guard is installed before sending anything, so this test can
        # never itself reach production.
        assert getattr(
            requests.adapters.HTTPAdapter.send, "blocks_production_gateway", False
        ), "production-gateway guard is not installed"
        with pytest.raises(RuntimeError, match="production gateway"):
            requests.get(f"{PRODUCTION_GATEWAY_URL}/", timeout=1)
        assert blocked_production_requests == [f"GET {PRODUCTION_GATEWAY_URL}/"]
        blocked_production_requests.clear()
