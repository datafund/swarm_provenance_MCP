"""Shared test setup.

The default test run must never reach the production gateway: several tests
create stamps or upload data through the default ``SWARM_GATEWAY_URL``.

* ``SWARM_GATEWAY_URL`` is forced to ``SWARM_TEST_GATEWAY_URL`` if set, else
  to a closed local port. This runs before the package is imported, so the
  module-level settings and gateway client pick it up.
* Tests marked ``integration`` are skipped unless ``--run-integration`` is
  given (point them at a local gateway with ``SWARM_TEST_GATEWAY_URL``).
* Any HTTP request to the production gateway host fails the test that made
  it, even if the test swallows the exception. The only way past this guard
  is to name the production gateway explicitly in ``SWARM_TEST_GATEWAY_URL``.
"""

import os
from urllib.parse import urlsplit

import pytest
import requests.adapters

PRODUCTION_GATEWAY_HOSTS = frozenset({"provenance-gateway.datafund.io"})
LOCAL_TEST_GATEWAY_URL = "http://127.0.0.1:9"

TEST_GATEWAY_URL = os.environ.get("SWARM_TEST_GATEWAY_URL") or LOCAL_TEST_GATEWAY_URL
os.environ["SWARM_GATEWAY_URL"] = TEST_GATEWAY_URL

_ALLOWED_HOSTS = {urlsplit(TEST_GATEWAY_URL).hostname}


class ProductionGatewayBlocked(RuntimeError):
    """A test tried to send a request to the production gateway."""


_blocked_requests = []
_original_send = requests.adapters.HTTPAdapter.send


def _guarded_send(self, request, *args, **kwargs):
    host = urlsplit(request.url).hostname
    if host in PRODUCTION_GATEWAY_HOSTS and host not in _ALLOWED_HOSTS:
        _blocked_requests.append(f"{request.method} {request.url}")
        raise ProductionGatewayBlocked(
            f"test tried to reach the production gateway: {request.method} "
            f"{request.url}"
        )
    return _original_send(self, request, *args, **kwargs)


_guarded_send.blocks_production_gateway = True
requests.adapters.HTTPAdapter.send = _guarded_send


def pytest_addoption(parser):
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="run tests marked 'integration' against SWARM_TEST_GATEWAY_URL",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-integration"):
        return
    skip = pytest.mark.skip(reason="integration test: pass --run-integration")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _no_production_gateway_requests():
    """Fail any test that tried to reach the production gateway."""
    _blocked_requests.clear()
    yield
    attempted = list(_blocked_requests)
    _blocked_requests.clear()
    if attempted:
        pytest.fail(
            "test sent requests to the production gateway: " + "; ".join(attempted)
        )


@pytest.fixture
def blocked_production_requests():
    """The guard's record of blocked requests, for tests of the guard itself."""
    return _blocked_requests


@pytest.fixture(autouse=True)
def _fresh_read_only_chain_client():
    """The server caches its read-only ChainClient; tests must not share it."""
    import swarm_provenance_mcp.server as server

    server._read_only_client = None
    yield
    server._read_only_client = None
