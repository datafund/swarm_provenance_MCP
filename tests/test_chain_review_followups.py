"""Review follow-ups for the chain failover work (#171, #173): #160, #163, #178,
#179, #181."""

import json
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from swarm_provenance_mcp.chain.exceptions import (
    ChainConnectionError,
    ChainTransactionError,
)
from tests.test_chain_client import (  # noqa: F401 — fixtures
    DUMMY_ADDRESS,
    DUMMY_HASH,
    DUMMY_HASH_BYTES,
    _http_error,
    endpoints,
    mock_chain_deps,
)
from tests.test_readonly_failover import no_wallet_server  # noqa: F401
from tests.test_tool_execution import call_tool_directly

CHILD_HASH = "d" * 64
REGISTERED_V3 = (  # 8 fields: v3 contract with storageRef
    DUMMY_HASH_BYTES,
    DUMMY_ADDRESS,
    1700000000,
    "swarm-provenance",
    b"\x00" * 32,
    [],
    [],
    0,
)


class TestContractFollowsProviderSwitch:
    """#171 blocking: health_check fails over outside an operation."""

    def test_operation_after_health_check_failover(self, endpoints):
        from swarm_provenance_mcp.chain.client import ChainClient

        primary, healthy, *rest = endpoints["urls"]
        endpoints["degrade"](primary)
        for url in rest:  # only one healthy fallback, as in the review repro
            endpoints["degrade"](url)

        client = ChainClient(chain="base-sepolia")
        assert client.health_check() is True
        assert client._provider.rpc_url == healthy

        # Before the fix the contract stayed on the primary and these raised
        for _ in range(3):
            assert client.verify(DUMMY_HASH) is False
        assert client._provider.rpc_url == healthy
        assert client._contract._web3 is endpoints["web3"][healthy]

    def test_switch_by_provider_alone_is_picked_up(self, endpoints):
        """chain_health calls chain_client._provider.health_check() directly."""
        from swarm_provenance_mcp.chain.client import ChainClient

        primary, healthy = endpoints["urls"][:2]
        endpoints["degrade"](primary)

        client = ChainClient(chain="base-sepolia")
        client._provider.health_check()
        client.verify(DUMMY_HASH)

        assert client._contract._web3 is endpoints["web3"][healthy]


class TestHttpProviderRetries:
    """#171 should-fix: web3's default retries re-sent a tx five times."""

    def test_send_raw_transaction_not_retried(self):
        from swarm_provenance_mcp.chain.provider import _http_provider
        from web3 import Web3

        hits = Counter()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                hits[req["method"]] += 1
                body = b'{"jsonrpc":"2.0","id":1,"error":{"code":-32011,"message":"x"}}'
                self.send_response(503)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            w3 = Web3(_http_provider(Web3, f"http://127.0.0.1:{server.server_port}", 5))
            with pytest.raises(Exception):
                w3.eth.send_raw_transaction(b"\x01")
            with pytest.raises(Exception):
                w3.eth.gas_price
        finally:
            server.shutdown()

        assert hits["eth_sendRawTransaction"] == 1
        assert hits["eth_gasPrice"] == 2  # one retry for reads


def test_http_provider_without_web3_7_retry_api():
    """pyproject allows web3 6, which lacks ExceptionRetryConfiguration."""
    import sys

    from swarm_provenance_mcp.chain.provider import _http_provider

    web3_cls = MagicMock()
    with patch.dict(sys.modules, {"web3.providers.rpc.utils": None}):
        _http_provider(web3_cls, "http://rpc", 7)
    web3_cls.HTTPProvider.assert_called_once_with(
        "http://rpc", request_kwargs={"timeout": 7}
    )
    assert web3_cls.HTTPProvider.return_value.middlewares == ()


class TestLineageUnderFailover:
    """#178: transport errors mid-traversal must fail over, not truncate."""

    def _v2_contract(self, contract):
        def links(h):
            call = MagicMock()
            h_hex = h.hex() if isinstance(h, bytes) else str(h)
            call.call.return_value = (
                [(bytes.fromhex(CHILD_HASH), "Anonymized")]
                if h_hex == DUMMY_HASH
                else []
            )
            return call

        contract.functions.getTransformationLinks.side_effect = links
        contract.functions.getTransformationParents.return_value.call.return_value = []
        contract.functions.getDataRecord.return_value.call.return_value = REGISTERED_V3

    def test_complete_lineage_when_primary_fails_link_reads(self, endpoints):
        from swarm_provenance_mcp.chain.client import ChainClient

        primary = endpoints["urls"][0]
        healthy_contract = endpoints["web3"][primary].eth.contract.return_value
        self._v2_contract(healthy_contract)
        # Primary: records readable, link reads 503 (the #178 reproduction)
        broken = MagicMock()
        broken.functions.getDataRecord.return_value.call.return_value = REGISTERED_V3
        broken.functions.getTransformationLinks.return_value.call.side_effect = (
            _http_error(503)
        )
        broken.functions.getTransformationParents.return_value.call.side_effect = (
            _http_error(503)
        )
        endpoints["web3"][primary].eth.contract.return_value = broken

        client = ChainClient(chain="base-sepolia")
        chain = client.get_provenance_chain(DUMMY_HASH)

        assert [r.data_hash for r in chain] == [DUMMY_HASH, DUMMY_HASH]
        assert chain[0].transformations[0].new_data_hash == CHILD_HASH
        assert client._provider.rpc_url != primary

    def test_merge_scan_outage_does_not_advance_cache(self):
        from swarm_provenance_mcp.chain.event_cache import TransformationEventCache

        cache = TransformationEventCache()
        contract = MagicMock()
        contract.get_all_transformations.return_value = []
        contract.get_all_merge_events.side_effect = _http_error(503)

        with pytest.raises(Exception, match="503"):
            cache.get_maps(contract, deploy_block=100, current_block=200)
        assert cache._last_scanned_block is None

    def test_retry_after_merge_outage_does_not_duplicate(self):
        """The failover retry re-runs get_maps over the same block range."""
        from swarm_provenance_mcp.chain.event_cache import TransformationEventCache

        cache = TransformationEventCache()
        contract = MagicMock()
        contract.get_all_transformations.return_value = [
            (DUMMY_HASH_BYTES, bytes.fromhex(CHILD_HASH), "Anonymized")
        ]
        contract.get_all_merge_events.side_effect = [_http_error(503), []]

        with pytest.raises(Exception, match="503"):
            cache.get_maps(contract, deploy_block=100, current_block=200)
        forward, _ = cache.get_maps(contract, deploy_block=100, current_block=200)

        assert forward[DUMMY_HASH] == [(CHILD_HASH, "Anonymized")]

    @pytest.mark.parametrize(
        "error,expected",
        [(_http_error(503), "raises"), (ValueError("no DataMerged event"), [])],
    )
    def test_contract_merge_events_classify_errors(
        self, mock_chain_deps, error, expected
    ):
        """get_all_merge_events itself must not turn an outage into 'no merges'."""
        from swarm_provenance_mcp.chain.contract import DataProvenanceContract

        contract = DataProvenanceContract(
            web3=mock_chain_deps["web3_instance"],
            contract_address="0x3945aDfd5Df9ab2F5cB4Ca0eb3D4384CC3650322",
        )
        events = mock_chain_deps["contract"].events.DataMerged
        events.get_logs.side_effect = error
        if expected == "raises":
            with pytest.raises(Exception, match="503"):
                contract.get_all_merge_events(from_block=0, to_block=10)
        else:
            assert contract.get_all_merge_events(from_block=0, to_block=10) == []

    def test_cache_not_advanced_through_real_contract_wrapper(self, mock_chain_deps):
        """End to end: wrapper + cache, as the review's v1 scenario."""
        from swarm_provenance_mcp.chain.contract import DataProvenanceContract
        from swarm_provenance_mcp.chain.event_cache import TransformationEventCache

        contract = DataProvenanceContract(
            web3=mock_chain_deps["web3_instance"],
            contract_address="0x3945aDfd5Df9ab2F5cB4Ca0eb3D4384CC3650322",
        )
        abi_events = mock_chain_deps["contract"].events
        abi_events.DataTransformed.get_logs.return_value = []
        abi_events.DataMerged.get_logs.side_effect = _http_error(503)

        cache = TransformationEventCache()
        with pytest.raises(Exception, match="503"):
            cache.get_maps(contract, deploy_block=0, current_block=10)
        assert cache._last_scanned_block is None

    def test_v1_merge_scan_failure_still_skipped(self):
        from swarm_provenance_mcp.chain.event_cache import TransformationEventCache

        cache = TransformationEventCache()
        contract = MagicMock()
        contract.get_all_transformations.return_value = []
        contract.get_all_merge_events.side_effect = ValueError("no such event")

        cache.get_maps(contract, deploy_block=100, current_block=200)
        assert cache._last_scanned_block == 200


class TestNoWalletV3Records:
    """#163: wallet-less lineage on 8-field (v3, storageRef) records."""

    async def test_chain_found_for_v3_record(self, no_wallet_server, endpoints):
        contract = endpoints["web3"][endpoints["urls"][0]].eth.contract.return_value
        contract.functions.getDataRecord.return_value.call.return_value = REGISTERED_V3

        result = await call_tool_directly(
            no_wallet_server, "get_provenance_chain", {"swarm_hash": DUMMY_HASH}
        )

        text = result.content[0].text
        assert not result.isError, text
        assert "Provenance Chain (1 entry)" in text
        assert "not registered" not in text


class TestReadOnlyClientReuse:
    """#179: one read-only client across calls, one read per verify_hash."""

    async def test_client_built_once(self, no_wallet_server, endpoints):
        from swarm_provenance_mcp.chain import client as client_module

        with patch.object(
            client_module, "ChainClient", wraps=client_module.ChainClient
        ) as built:
            for _ in range(3):
                await call_tool_directly(
                    no_wallet_server, "verify_hash", {"swarm_hash": DUMMY_HASH}
                )
        assert built.call_count == 1

    async def test_failover_persists_across_calls(self, no_wallet_server, endpoints):
        primary = endpoints["urls"][0]
        endpoints["degrade"](primary)

        for _ in range(3):
            result = await call_tool_directly(
                no_wallet_server, "verify_hash", {"swarm_hash": DUMMY_HASH}
            )
            assert not result.isError

        # Only the first call hit the degraded primary's record read
        degraded = endpoints["web3"][primary].eth.contract.return_value
        assert degraded.functions.getDataRecord.return_value.call.call_count == 1

    def test_read_only_wallet_hasattr_is_false(self, endpoints):
        from swarm_provenance_mcp.chain.client import ChainClient

        client = ChainClient(chain="base-sepolia", read_only=True)
        assert hasattr(client._wallet, "address") is False


class TestPendingTransactionReporting:
    """#181 / #160(a): a broadcast tx with an unknown outcome is not 'failed'."""

    @pytest.fixture
    def server(self):
        from swarm_provenance_mcp.server import create_server

        return create_server()

    def _broadcast_error(self, cause):
        try:
            try:
                raise cause
            except Exception as c:
                raise ChainTransactionError(
                    f"Transaction failed: {c}", tx_hash="0xabc", broadcast=True
                ) from c
        except ChainTransactionError as e:
            return e

    @pytest.mark.parametrize(
        "tool,method,args,check",
        [
            ("anchor_hash", "anchor", {"swarm_hash": DUMMY_HASH}, "verify_hash"),
            (
                "record_transform",
                "transform",
                {
                    "original_hash": DUMMY_HASH,
                    "new_hash": CHILD_HASH,
                    "description": "x",
                },
                "get_provenance_chain",
            ),
            (
                "set_storage_ref",
                "set_storage_ref",
                {"data_hash": DUMMY_HASH, "storage_ref": CHILD_HASH},
                "get_provenance",
            ),
        ],
    )
    async def test_transport_failure_after_broadcast(
        self, server, tool, method, args, check
    ):
        mock_client = MagicMock()
        getattr(mock_client, method).side_effect = self._broadcast_error(
            _http_error(503)
        )
        mock_client.get.side_effect = Exception("skip duplicate pre-checks")
        with (
            patch("swarm_provenance_mcp.server.CHAIN_AVAILABLE", True),
            patch("swarm_provenance_mcp.server.chain_client", mock_client),
        ):
            result = await call_tool_directly(server, tool, args)
        text = result.content[0].text
        assert "may be pending or already mined" in text, text
        assert "0xabc" in text
        assert "retryable: false" in text
        assert f"_next: {check}" in text

    async def test_receipt_timeout_is_pending(self, server):
        from web3.exceptions import TimeExhausted

        mock_client = MagicMock()
        mock_client.anchor.side_effect = self._broadcast_error(TimeExhausted("120s"))
        with (
            patch("swarm_provenance_mcp.server.CHAIN_AVAILABLE", True),
            patch("swarm_provenance_mcp.server.chain_client", mock_client),
        ):
            result = await call_tool_directly(
                server, "anchor_hash", {"swarm_hash": DUMMY_HASH}
            )
        assert "may be pending" in result.content[0].text

    async def test_revert_still_reported_as_failed(self, server):
        mock_client = MagicMock()
        mock_client.anchor.side_effect = self._broadcast_error(
            ValueError("execution reverted")
        )
        with (
            patch("swarm_provenance_mcp.server.CHAIN_AVAILABLE", True),
            patch("swarm_provenance_mcp.server.chain_client", mock_client),
        ):
            result = await call_tool_directly(
                server, "anchor_hash", {"swarm_hash": DUMMY_HASH}
            )
        text = result.content[0].text
        assert "Transaction failed" in text
        assert "may be pending" not in text


class TestChainHealthHint:
    async def test_failure_does_not_point_at_gateway_health_check(self):
        from swarm_provenance_mcp.server import create_server

        mock_client = MagicMock()
        mock_client._provider.health_check.side_effect = ChainConnectionError(
            "all RPCs down"
        )
        with (
            patch("swarm_provenance_mcp.server.CHAIN_AVAILABLE", True),
            patch("swarm_provenance_mcp.server.chain_client", mock_client),
        ):
            result = await call_tool_directly(create_server(), "chain_health", {})
        text = result.content[0].text
        assert "retryable: true" in text
        assert "_next: health_check" not in text
