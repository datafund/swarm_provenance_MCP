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
    ("set_storage_ref", {"data_hash": HASH_A, "storage_ref": HASH_B}, "set_storage_ref"),
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
    with patch("swarm_provenance_mcp.chain.provider.ChainProvider", return_value=provider):
        result = await call_tool("chain_health", {})
    assert result.isError
    _assert_redacted(result)


@pytest.mark.parametrize("error", sorted(ERRORS))
async def test_record_transform_restrict_failure_hides_rpc_path_and_query(
    chain, error
):
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
        r.getMessage() + (logging.Formatter().formatException(r.exc_info) if r.exc_info else "")
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


from tests.test_chain_client import endpoints, mock_chain_deps  # noqa: E402,F401


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
