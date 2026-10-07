"""Shared test setup."""

import pytest


@pytest.fixture(autouse=True)
def _fresh_read_only_chain_client():
    """The server caches its read-only ChainClient; tests must not share it."""
    import swarm_provenance_mcp.server as server

    server._read_only_client = None
    yield
    server._read_only_client = None
