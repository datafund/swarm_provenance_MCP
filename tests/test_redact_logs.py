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
        # Bracketed IPv6 hosts, with and without a port.
        ("http://[::1]:8545/v2/SECRETKEY?apikey=Q", "http://[::1]:8545"),
        ("http://[::1]/v2/SECRETKEY", "http://[::1]"),
        ("https://u:SECRET@[fe80::1%25eth0]:8545/x", "https://[fe80::1%25eth0]:8545"),
        # Parentheses inside a path do not end it early.
        ("https://h.io/v2/KE(SECRET)", "https://h.io)"),
        ("(see https://h.io/v2/SECRET)", "(see https://h.io)"),
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


def test_redact_urls_on_http_error_with_ipv6_host():
    """requests' HTTPError text quotes the full URL after 'for url:'."""
    response = requests.Response()
    response.status_code = 503
    response.reason = "Service Unavailable"
    response.url = "http://[::1]:8545/v2/SECRETKEY?apikey=SECRETQ"
    with pytest.raises(requests.exceptions.HTTPError) as info:
        response.raise_for_status()
    assert "SECRET" in str(info.value)
    assert redact_urls(info.value) == (
        "503 Server Error: Service Unavailable for url: http://[::1]:8545"
    )


# Inputs of about 1,000,000 characters, built inside the test so that no
# test ID or assertion message contains them.
LONG_INPUTS = {
    "dotted-words": lambda: "a." * 500_000,
    "plus-runs": lambda: "ab+" * 333_333,
    "scheme-runs": lambda: "a://" * 250_000,
    "at-signs": lambda: "http://" + "@" * 1_000_000,
    "open-bracket": lambda: "http://[" + ":" * 1_000_000,
    "dot-paren-path": lambda: "http://h/" + ".(" * 500_000,
    "schemes-in-path": lambda: "http://h/" + "a://" * 250_000,
    "one-word": lambda: "a" * 1_000_000,
    "escaped-schemes": lambda: "a:\\/\\/" * 166_666,
    "long-scheme-runs": lambda: ("a" * 32 + ":") * 30_000,
}


@pytest.mark.parametrize("name", sorted(LONG_INPUTS))
def test_redact_urls_is_linear_on_long_input(name):
    import time

    text = LONG_INPUTS[name]()
    start = time.monotonic()
    redact_urls(text)
    elapsed = time.monotonic() - start
    assert elapsed < 2.0, f"{name}: {elapsed:.2f} s for {len(text):,} characters"


def test_url_host():
    assert url_host(PRIMARY) == "primary.rpc.test"
    assert url_host("http://[::1]:8545/v2/SECRET") == "::1"
    assert url_host("http://[::1") == "<rpc>"
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


def test_client_health_check_debug_logs_host_only(endpoints, caplog):
    from swarm_provenance_mcp.chain.client import ChainClient

    caplog.set_level(logging.DEBUG)
    client = ChainClient(chain="base-sepolia")
    assert client.health_check() is True

    ours = _ours(caplog)
    assert "Chain health check: chain=base-sepolia rpc=primary.rpc.test" in ours
    assert "SECRET" not in ours
