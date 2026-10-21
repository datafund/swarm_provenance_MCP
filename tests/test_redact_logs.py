"""RPC URL paths and queries never reach our log lines (they can hold API keys)."""

import logging

import pytest
import requests

from swarm_provenance_mcp.redact import redact_urls, url_host
from tests.test_chain_client import (  # noqa: F401  (fixtures)
    DUMMY_HASH,
    endpoints,
    mock_chain_deps,
)

PRIMARY = "https://primary.rpc.test/v2/SECRETPRIMARY?apikey=SECRETQ1"
FALLBACK_1 = "https://fb1.rpc.test/v2/SECRETFB1"
FALLBACK_2 = "https://fb2.rpc.test/?apikey=SECRETQ2"


# --- redact_urls / url_host ---


@pytest.mark.parametrize(
    "text, expected",
    [
        ("https://h.io/v2/SECRET", "https://h.io"),
        ("http://h.io:8545/?apikey=SECRET", "http://h.io:8545"),
        ("wss://h.io/ws/SECRET#frag", "wss://h.io"),
        ("https://user:SECRET@h.io/x", "https://h.io"),
        (
            "for url: https://h.io/v2/SECRET, retrying",
            "for url: https://h.io, retrying",
        ),
        ("(see 'https://h.io/v2/SECRET')", "(see 'https://h.io')"),
        ("https://h.io", "https://h.io"),
        ("no url: a/b?c", "no url: a/b?c"),
    ],
)
def test_redact_urls(text, expected):
    assert redact_urls(text) == expected


def test_redact_urls_on_requests_connection_error():
    """urllib3 names the path alone: '... with url: /v2/<key> (Caused by ...'."""
    import socket

    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    port = closed.getsockname()[1]
    closed.close()
    with pytest.raises(requests.exceptions.ConnectionError) as info:
        requests.get(f"http://127.0.0.1:{port}/v2/SECRETPATH?apikey=SECRETQ", timeout=2)
    redacted = redact_urls(info.value)
    assert "SECRET" not in redacted
    assert "127.0.0.1" in redacted


def test_url_host():
    assert url_host(PRIMARY) == "primary.rpc.test"
    assert url_host("not a url") == "<rpc>"
    assert url_host("not a url", "?") == "?"


# --- failover log lines ---


@pytest.fixture(autouse=True)
def secret_urls(monkeypatch):
    """Preset RPC URLs that carry keys in the path and the query."""
    from swarm_provenance_mcp.chain.provider import CHAIN_PRESETS

    preset = dict(CHAIN_PRESETS["base-sepolia"])
    preset["rpc_url"] = PRIMARY
    preset["rpc_fallbacks"] = [FALLBACK_1, FALLBACK_2]
    monkeypatch.setitem(CHAIN_PRESETS, "base-sepolia", preset)

    # The degraded endpoints' 503 errors quote the full URL, as requests does.
    import tests.test_chain_client as tcc

    def http_error(status):
        response = requests.Response()
        response.status_code = status
        return requests.exceptions.HTTPError(
            f"{status} Server Error: Service Unavailable for url: {PRIMARY}",
            response=response,
        )

    monkeypatch.setattr(tcc, "_http_error", http_error)


def _ours(caplog) -> str:
    return "\n".join(
        r.getMessage()
        for r in caplog.records
        if r.name.startswith("swarm_provenance_mcp")
    )


def test_provider_fallback_logs_hosts_only(endpoints, caplog):
    from swarm_provenance_mcp.chain.provider import ChainProvider

    assert endpoints["urls"] == [PRIMARY, FALLBACK_1, FALLBACK_2]
    endpoints["degrade"](PRIMARY)
    endpoints["degrade"](FALLBACK_1)
    caplog.set_level(logging.DEBUG)

    provider = ChainProvider(chain="base-sepolia")
    assert provider.health_check() is True
    assert provider.rpc_url == FALLBACK_2

    ours = _ours(caplog)
    assert "failed probe" in ours
    assert "switched from primary.rpc.test to fb2.rpc.test" in ours
    assert "SECRET" not in ours


def test_client_failover_warning_logs_hosts_only(endpoints, caplog):
    from swarm_provenance_mcp.chain.client import ChainClient

    endpoints["degrade"](PRIMARY)
    caplog.set_level(logging.DEBUG)

    client = ChainClient(chain="base-sepolia")
    client.verify(DUMMY_HASH)
    assert client._provider.rpc_url == FALLBACK_1

    ours = _ours(caplog)
    assert "unavailable" in ours and "retrying on fb1.rpc.test" in ours
    assert "SECRET" not in ours
