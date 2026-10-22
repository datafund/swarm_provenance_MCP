"""RPC URL paths and queries never reach a tool result (they can hold API keys).

Each chain tool is called through the server's registered call_tool handler,
with an RPC URL that has a key in its path and its query, while the chain
layer raises an error that quotes that URL.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest
import requests
from mcp.types import CallToolRequest, CallToolRequestParams

from swarm_provenance_mcp import server as srv
from swarm_provenance_mcp.chain.exceptions import ChainConnectionError
from swarm_provenance_mcp.redact import redact_urls
from tests.test_chain_client import (  # noqa: F401  (fixtures)
    endpoints,
    mock_chain_deps,
)

RPC = "https://rpc.leak.test/v2/SECRETKEY123?apikey=SECRETQ456"
RPC_HOST = "rpc.leak.test"
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def _http_error():
    """What web3's HTTPProvider raises: requests quotes the full URL."""
    response = requests.Response()
    response.status_code = 503
    return requests.exceptions.HTTPError(
        f"503 Server Error: Service Unavailable for url: {RPC}", response=response
    )


def _urllib3_error():
    """urllib3 names the host and quotes the path and query on their own."""
    return requests.exceptions.ConnectionError(
        f"HTTPSConnectionPool(host='{RPC_HOST}', port=443): Max retries "
        "exceeded with url: /v2/SECRETKEY123?apikey=SECRETQ456 (Caused by "
        "NewConnectionError('Failed to establish a new connection'))"
    )


def _chain_connection_error():
    """A chain exception whose message embeds the raw error text."""
    return ChainConnectionError(
        f"RPC endpoint unavailable and no fallback could serve the request: "
        f"503 Server Error: Service Unavailable for url: {RPC}",
        rpc_url=RPC,
    )


ERRORS = {
    "http-error": _http_error,
    "urllib3-error": _urllib3_error,
    "chain-connection-error": _chain_connection_error,
}


async def call_tool(name, arguments):
    """Call a tool through the server's registered call_tool handler."""
    server = srv.create_server()
    handler = server.request_handlers[CallToolRequest]
    result = await handler(
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name=name, arguments=arguments),
        )
    )
    return result.root


def _text(result) -> str:
    return "\n".join(c.text for c in result.content if hasattr(c, "text"))


def _assert_redacted(result):
    text = _text(result)
    assert "SECRET" not in text, text
    assert RPC_HOST in text, text


def _client():
    client = MagicMock()
    client.chain = "base-sepolia"
    client.address = "0x742d35Cc6634C0532925a3b844Bc9e7595f8fE00"
    client.contract_address = "0x3945aDfd5Df9ab2F5cB4Ca0eb3D4384CC3650322"
    client._provider = MagicMock()
    client._provider.rpc_url = RPC
    client._provider._rpc_urls = [RPC]
    client._provider.chain = "base-sepolia"
    client._provider.chain_id = 84532
    client._provider.is_testnet = True
    return client


@pytest.fixture
def chain(monkeypatch):
    """A wallet client on the secret RPC URL; tests set what raises."""
    client = _client()
    monkeypatch.setattr(srv.settings, "chain_enabled", True)
    monkeypatch.setattr(srv.settings, "chain_rpc_url", RPC)
    monkeypatch.setattr(srv, "CHAIN_AVAILABLE", True)
    monkeypatch.setattr(srv, "chain_client", client)
    monkeypatch.setattr(srv, "_chain_init_error", None)
    return client


def _healthy_gateway(gw):
    gw.health_check.return_value = {
        "status": "healthy",
        "gateway_url": "http://localhost",
        "response_time_ms": 1,
    }
    gw.list_stamps.return_value = {"stamps": [], "total_count": 0}


# Tool, arguments, and the client method that raises.
TOOL_CASES = [
    ("chain_balance", {}, "balance"),
    ("verify_hash", {"swarm_hash": HASH_A}, "get"),
    ("get_provenance", {"swarm_hash": HASH_A}, "get"),
    ("get_provenance_chain", {"swarm_hash": HASH_A}, "get_provenance_chain"),
    ("anchor_hash", {"swarm_hash": HASH_A}, "anchor"),
    ("record_transform", {"original_hash": HASH_A, "new_hash": HASH_B}, "transform"),
    (
        "record_merge_transform",
        {"source_hashes": [HASH_A, HASH_B], "new_hash": HASH_C},
        "merge_transform",
    ),
    (
        "set_storage_ref",
        {"data_hash": HASH_A, "storage_ref": HASH_B},
        "set_storage_ref",
    ),
    ("lookup_by_storage_ref", {"storage_ref": HASH_B}, "lookup_by_storage_ref"),
]


@pytest.mark.parametrize("error", sorted(ERRORS))
@pytest.mark.parametrize(
    "tool, arguments, method", TOOL_CASES, ids=[c[0] for c in TOOL_CASES]
)
async def test_chain_tool_error_hides_rpc_path_and_query(
    chain, tool, arguments, method, error
):
    getattr(chain, method).side_effect = ERRORS[error]()
    result = await call_tool(tool, arguments)
    assert result.isError
    _assert_redacted(result)


@pytest.mark.parametrize("error", sorted(ERRORS))
async def test_chain_health_error_hides_rpc_path_and_query(chain, error):
    chain._provider.health_check.side_effect = ERRORS[error]()
    result = await call_tool("chain_health", {})
    assert result.isError
    _assert_redacted(result)


@pytest.mark.parametrize("error", sorted(ERRORS))
async def test_chain_health_without_wallet_hides_rpc_path_and_query(
    chain, monkeypatch, error
):
    monkeypatch.setattr(srv, "chain_client", None)
    monkeypatch.setattr(srv, "_unverified_write_client", None)
    provider = _client()._provider
    provider.health_check.side_effect = ERRORS[error]()
    with patch(
        "swarm_provenance_mcp.chain.provider.ChainProvider", return_value=provider
    ):
        result = await call_tool("chain_health", {})
    assert result.isError
    _assert_redacted(result)


@pytest.mark.parametrize("error", sorted(ERRORS))
async def test_record_transform_restrict_failure_hides_rpc_path_and_query(chain, error):
    done = MagicMock()
    done.original_hash, done.new_hash = HASH_A, HASH_B
    done.tx_hash, done.block_number, done.gas_used = "0x" + "bb" * 32, 1, 1
    done.explorer_url = None
    chain.transform.return_value = done
    chain.set_status.side_effect = ERRORS[error]()
    result = await call_tool(
        "record_transform",
        {"original_hash": HASH_A, "new_hash": HASH_B, "restrict_original": True},
    )
    _assert_redacted(result)


@pytest.mark.parametrize("error", sorted(ERRORS))
async def test_health_check_wallet_chain_error_hides_rpc_path_and_query(chain, error):
    chain.health_check.side_effect = ERRORS[error]()
    with patch.object(srv, "gateway_client") as gw:
        _healthy_gateway(gw)
        result = await call_tool("health_check", {})
    _assert_redacted(result)


@pytest.mark.parametrize("error", sorted(ERRORS))
async def test_health_check_read_only_chain_error_hides_rpc_path_and_query(
    chain, monkeypatch, error
):
    monkeypatch.setattr(srv, "chain_client", None)
    monkeypatch.setattr(srv, "_unverified_write_client", None)
    with (
        patch.object(srv, "gateway_client") as gw,
        patch.object(srv, "_chain_reader", side_effect=ERRORS[error]()),
    ):
        _healthy_gateway(gw)
        result = await call_tool("health_check", {})
    _assert_redacted(result)


@pytest.mark.parametrize("error", sorted(ERRORS))
async def test_catch_all_result_and_log_hide_rpc_path_and_query(
    chain, monkeypatch, caplog, error
):
    """An exception that escapes a handler: the result and the log, traceback
    included, name the host only."""

    async def broken(arguments):
        raise ERRORS[error]()

    monkeypatch.setattr(srv, "handle_verify_hash", broken)
    caplog.set_level(logging.DEBUG)
    result = await call_tool("verify_hash", {"swarm_hash": HASH_A})
    assert result.isError
    _assert_redacted(result)

    records = [r for r in caplog.records if "Error in tool" in r.getMessage()]
    assert records
    logged = "\n".join(
        r.getMessage()
        + (logging.Formatter().formatException(r.exc_info) if r.exc_info else "")
        for r in records
    )
    assert "SECRET" not in logged
    assert "Traceback" in logged and "broken" in logged


async def test_other_urls_in_tool_output_keep_their_path(chain):
    """Only RPC URLs are cut: faucet links keep their path."""
    info = MagicMock()
    info.address = chain.address
    info.balance_wei = 10**12
    info.balance_eth = "0.000001"
    info.chain = "base-sepolia"
    info.contract_address = chain.contract_address
    chain.balance.return_value = info
    result = await call_tool("chain_balance", {})
    assert not result.isError
    text = _text(result)
    assert "https://www.alchemy.com/faucets/base-sepolia" in text
    assert "SECRET" not in text


# --- redact_urls(hosts=...) ---


@pytest.mark.parametrize(
    "text, expected",
    [
        (f"for url: {RPC}", f"for url: https://{RPC_HOST}"),
        (
            "see https://www.alchemy.com/faucets/base-sepolia",
            "see https://www.alchemy.com/faucets/base-sepolia",
        ),
        (
            "500 for url: http://gateway.test/api/v1/stamps/x",
            "500 for url: http://gateway.test/api/v1/stamps/x",
        ),
        ("https://RPC.LEAK.TEST:8545/v2/SECRET", "https://RPC.LEAK.TEST:8545"),
        ("https://u:SECRET@rpc.leak.test/x", "https://rpc.leak.test"),
        (
            f"HTTPSConnectionPool(host='{RPC_HOST}', port=443): Max retries "
            "exceeded with url: /v2/SECRET (Caused by X)",
            f"HTTPSConnectionPool(host='{RPC_HOST}', port=443): Max retries "
            "exceeded with url: <path> (Caused by X)",
        ),
        (
            "HTTPConnectionPool(host='gateway.test', port=80): Max retries "
            "exceeded with url: /api/v1/stamps (Caused by X)",
            "HTTPConnectionPool(host='gateway.test', port=80): Max retries "
            "exceeded with url: /api/v1/stamps (Caused by X)",
        ),
    ],
)
def test_redact_urls_limited_to_hosts(text, expected):
    assert redact_urls(text, hosts={RPC_HOST}) == expected


def test_redact_urls_with_empty_hosts_changes_nothing():
    assert redact_urls(f"for url: {RPC}", hosts=set()) == f"for url: {RPC}"


# --- chain exception messages, at the source ---


@pytest.fixture
def secret_preset(monkeypatch):
    """Preset RPC URLs with keys, and 503s that quote the primary URL."""
    from swarm_provenance_mcp.chain.provider import CHAIN_PRESETS
    import tests.test_chain_client as tcc

    preset = dict(CHAIN_PRESETS["base-sepolia"])
    preset["rpc_url"] = RPC
    preset["rpc_fallbacks"] = []
    monkeypatch.setitem(CHAIN_PRESETS, "base-sepolia", preset)
    monkeypatch.setattr(tcc, "_http_error", lambda status: _http_error())


def test_provider_health_check_error_names_host_only(secret_preset, endpoints):
    from swarm_provenance_mcp.chain.provider import ChainProvider

    endpoints["degrade"](RPC)
    provider = ChainProvider(chain="base-sepolia")
    with pytest.raises(ChainConnectionError) as info:
        provider.health_check()
    assert "SECRET" not in str(info.value)
    assert RPC_HOST in str(info.value)


def test_provider_block_number_error_names_host_only(secret_preset, endpoints):
    from unittest.mock import PropertyMock

    from swarm_provenance_mcp.chain.provider import ChainProvider

    type(endpoints["web3"][RPC].eth).block_number = PropertyMock(
        side_effect=_http_error()
    )
    provider = ChainProvider(chain="base-sepolia")
    with pytest.raises(ChainConnectionError) as info:
        provider.get_block_number()
    assert "SECRET" not in str(info.value)
    assert RPC_HOST in str(info.value)


def test_client_failover_error_names_host_only(secret_preset, endpoints):
    from swarm_provenance_mcp.chain.client import ChainClient

    endpoints["degrade"](RPC)
    client = ChainClient(chain="base-sepolia")
    with pytest.raises(ChainConnectionError) as info:
        client.verify(HASH_A)
    assert "SECRET" not in str(info.value)
    assert RPC_HOST in str(info.value)


def test_client_transaction_error_names_host_only(secret_preset, endpoints):
    from swarm_provenance_mcp.chain.client import ChainClient
    from swarm_provenance_mcp.chain.exceptions import ChainTransactionError

    endpoints["web3"][RPC].eth.send_raw_transaction.side_effect = _http_error()
    client = ChainClient(chain="base-sepolia")
    with pytest.raises(ChainTransactionError) as info:
        client.anchor(HASH_A)
    assert "SECRET" not in str(info.value)
    assert RPC_HOST in str(info.value)


# --- an RPC URL configured without a scheme ---

SCHEMELESS = "rpc.leak.test/v2/SECRETKEY123"


def _missing_schema():
    """What requests raises for a URL without a scheme; it quotes the URL."""
    try:
        requests.get(SCHEMELESS, timeout=1)
    except requests.exceptions.MissingSchema as e:
        return e
    raise AssertionError("requests accepted a URL without a scheme")


def _assert_no_secret(text):
    assert "SECRET" not in text, text


@pytest.fixture
def schemeless(monkeypatch):
    """CHAIN_RPC_URL without a scheme, and no clients built yet."""
    monkeypatch.setattr(srv.settings, "chain_enabled", True)
    monkeypatch.setattr(srv.settings, "chain_name", "base-sepolia")
    monkeypatch.setattr(srv.settings, "chain_rpc_url", SCHEMELESS)
    monkeypatch.setattr(srv.settings, "chain_rpc_urls", None)
    monkeypatch.setattr(srv, "CHAIN_AVAILABLE", True)
    monkeypatch.setattr(srv, "chain_client", None)
    monkeypatch.setattr(srv, "_read_only_client", None)
    monkeypatch.setattr(srv, "_unverified_write_client", None)
    monkeypatch.setattr(srv, "_chain_init_error", None)


async def test_schemeless_rpc_url_hidden_in_chain_health(schemeless):
    result = await call_tool("chain_health", {})
    assert result.isError
    _assert_no_secret(_text(result))
    assert "rpc.leak.test" in _text(result)


async def test_schemeless_rpc_url_hidden_in_verify_hash(schemeless):
    result = await call_tool("verify_hash", {"swarm_hash": HASH_A})
    assert result.isError
    _assert_no_secret(_text(result))
    assert "rpc.leak.test" in _text(result)


async def test_schemeless_rpc_url_hidden_in_chain_balance_error(
    schemeless, monkeypatch
):
    client = _client()
    client._provider.rpc_url = SCHEMELESS
    client._provider._rpc_urls = [SCHEMELESS]
    client.balance.side_effect = _missing_schema()
    monkeypatch.setattr(srv, "chain_client", client)
    result = await call_tool("chain_balance", {})
    assert result.isError
    _assert_no_secret(_text(result))


async def test_schemeless_rpc_url_hidden_in_chain_balance_rpc_line(
    schemeless, monkeypatch
):
    client = _client()
    client._provider.rpc_url = SCHEMELESS
    client._provider._rpc_urls = [SCHEMELESS]
    info = MagicMock()
    info.address = client.address
    info.balance_wei = 10**18
    info.balance_eth = "1.0"
    info.chain = "base-sepolia"
    info.contract_address = client.contract_address
    client.balance.return_value = info
    monkeypatch.setattr(srv, "chain_client", client)
    result = await call_tool("chain_balance", {})
    assert not result.isError
    _assert_no_secret(_text(result))
    assert "RPC: rpc.leak.test" in _text(result)


async def test_schemeless_fallback_url_hidden_in_tool_result(monkeypatch):
    """A CHAIN_RPC_URLS entry without a scheme is hidden too."""
    monkeypatch.setattr(srv.settings, "chain_rpc_url", None)
    monkeypatch.setattr(
        srv.settings, "chain_rpc_urls", f"https://ok.rpc.test,{SCHEMELESS}"
    )
    monkeypatch.setattr(srv, "chain_client", None)
    monkeypatch.setattr(srv, "_read_only_client", None)
    monkeypatch.setattr(srv, "_unverified_write_client", None)
    result = srv.CallToolResult(
        content=[srv.TextContent(type="text", text=f"Error: {_missing_schema()}")],
        isError=True,
    )
    text = _text(srv._redact_rpc_urls(result))
    _assert_no_secret(text)
    assert "rpc.leak.test" in text


def test_schemeless_rpc_url_hidden_in_startup_log(caplog):
    from swarm_provenance_mcp.chain.provider import ChainProvider

    caplog.set_level(logging.DEBUG)
    ChainProvider(chain="base-sepolia", rpc_url=SCHEMELESS)
    logged = "\n".join(
        r.getMessage()
        for r in caplog.records
        if r.name.startswith("swarm_provenance_mcp")
    )
    assert "Could not read the chain id" in logged
    _assert_no_secret(logged)


def test_mask_rpc_url_never_returns_a_schemeless_url():
    assert srv._mask_rpc_url(SCHEMELESS) == "rpc.leak.test"
    assert "SECRET" not in srv._mask_rpc_url("/v2/SECRETKEY123")


@pytest.mark.parametrize(
    "url, host",
    [
        ("https://rpc.leak.test/v2/SECRET", "rpc.leak.test"),
        ("rpc.leak.test/v2/SECRET", "rpc.leak.test"),
        ("RPC.leak.test:8545?apikey=SECRET", "rpc.leak.test"),
        ("u:SECRET@rpc.leak.test/x", "rpc.leak.test"),
        ("[::1]:8545/v2/SECRET", "::1"),
        ("/v2/SECRET", ""),
        ("", ""),
    ],
)
def test_rpc_host(url, host):
    from swarm_provenance_mcp.redact import rpc_host

    assert rpc_host(url) == host


def test_mask_literal_urls():
    from swarm_provenance_mcp.redact import mask_literal_urls

    text = (
        f"Invalid URL '{SCHEMELESS}': No scheme supplied. "
        f"Perhaps you meant https://{SCHEMELESS}? path /v2/SECRETKEY123 alone"
    )
    masked = mask_literal_urls(text, [SCHEMELESS, "https://ok.rpc.test"])
    assert "SECRET" not in masked
    assert masked.startswith("Invalid URL 'rpc.leak.test'")
    # A URL with no path, query or credentials leaves the text alone.
    assert mask_literal_urls("see https://ok.rpc.test/", ["https://ok.rpc.test"]) == (
        "see https://ok.rpc.test/"
    )
    assert "SECRET" not in mask_literal_urls(
        "at u:SECRET@rpc.leak.test", ["u:SECRET@rpc.leak.test"]
    )


def test_mask_literal_urls_is_linear_on_long_input():
    import time

    from swarm_provenance_mcp.redact import mask_literal_urls

    text = (SCHEMELESS[:-1] + " ") * 100_000
    start = time.monotonic()
    mask_literal_urls(text, [SCHEMELESS, "https://h.test/v2/K", "h/x"])
    assert time.monotonic() - start < 2.0


def _raise_missing_schema():
    raise _missing_schema()


def _raise_chained_missing_schema():
    try:
        _raise_missing_schema()
    except requests.exceptions.MissingSchema as e:
        raise RuntimeError(f"RPC call failed: {e}") from e


@pytest.mark.parametrize(
    "raiser", [_raise_missing_schema, _raise_chained_missing_schema]
)
async def test_catch_all_log_hides_schemeless_rpc_url(
    schemeless, monkeypatch, caplog, raiser
):
    """A MissingSchema for a scheme-less RPC URL that escapes a handler: the
    log message and the traceback, chained cause included, hide the key."""

    async def broken(arguments):
        raiser()

    monkeypatch.setattr(srv, "handle_verify_hash", broken)
    caplog.set_level(logging.DEBUG)
    result = await call_tool("verify_hash", {"swarm_hash": HASH_A})
    assert result.isError
    _assert_no_secret(_text(result))

    records = [r for r in caplog.records if "Error in tool" in r.getMessage()]
    assert records
    logged = "\n".join(
        r.getMessage()
        + (logging.Formatter().formatException(r.exc_info) if r.exc_info else "")
        for r in records
    )
    assert "MissingSchema" in logged and "Traceback" in logged
    _assert_no_secret(logged)


@pytest.fixture
def schemeless_preset(monkeypatch):
    """The preset RPC URL without a scheme; its 503s quote it in full."""
    from swarm_provenance_mcp.chain.provider import CHAIN_PRESETS
    import tests.test_chain_client as tcc

    preset = dict(CHAIN_PRESETS["base-sepolia"])
    preset["rpc_url"] = SCHEMELESS
    preset["rpc_fallbacks"] = []
    monkeypatch.setitem(CHAIN_PRESETS, "base-sepolia", preset)

    def http_error(status=503):
        response = requests.Response()
        response.status_code = status
        return requests.exceptions.HTTPError(
            f"{status} Server Error for url: {SCHEMELESS}", response=response
        )

    monkeypatch.setattr(tcc, "_http_error", http_error)
    return http_error


def test_client_rpc_host_of_schemeless_url():
    from swarm_provenance_mcp.chain.client import _rpc_host

    assert _rpc_host(SCHEMELESS) == "rpc.leak.test"


def test_client_failover_error_hides_schemeless_url(schemeless_preset, endpoints):
    from swarm_provenance_mcp.chain.client import ChainClient

    endpoints["degrade"](SCHEMELESS)
    client = ChainClient(chain="base-sepolia")
    with pytest.raises(ChainConnectionError) as info:
        client.verify(HASH_A)
    _assert_no_secret(str(info.value))
    assert "rpc.leak.test" in str(info.value)


def test_client_transaction_error_hides_schemeless_url(schemeless_preset, endpoints):
    from swarm_provenance_mcp.chain.client import ChainClient
    from swarm_provenance_mcp.chain.exceptions import ChainTransactionError

    endpoints["web3"][
        SCHEMELESS
    ].eth.send_raw_transaction.side_effect = schemeless_preset()
    client = ChainClient(chain="base-sepolia")
    with pytest.raises(ChainTransactionError) as info:
        client.anchor(HASH_A)
    _assert_no_secret(str(info.value))
    assert "rpc.leak.test" in str(info.value)
