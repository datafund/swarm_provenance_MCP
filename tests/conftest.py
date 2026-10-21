"""Shared test setup."""

import pytest


@pytest.fixture(autouse=True)
def _fresh_read_only_chain_client():
    """The server caches its read-only ChainClient; tests must not share it."""
    import swarm_provenance_mcp.server as server

    server._read_only_client = None
    yield
    server._read_only_client = None


@pytest.fixture(autouse=True)
def _confirmed_chain_writes(monkeypatch):
    """Handler tests use MagicMock chain clients and exercise what happens
    after a write is confirmed. The confirmation step itself (and its
    mainnet-only default) is tested in test_spend_limits.py, which resets
    this setting."""
    from swarm_provenance_mcp.config import settings

    monkeypatch.setattr(settings, "chain_require_confirm", False)
