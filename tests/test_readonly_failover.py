"""Read-only chain tools without a wallet get RPC failover (issue #170)."""

from unittest.mock import MagicMock, patch

import pytest

from swarm_provenance_mcp.chain.exceptions import (
    ChainConfigurationError,
    ChainConnectionError,
)
from swarm_provenance_mcp.server import create_server
from tests.test_chain_client import (  # noqa: F401 — fixtures
    DUMMY_ADDRESS,
    DUMMY_HASH,
    DUMMY_HASH_BYTES,
    UNREGISTERED_RECORD,
    _http_error,
    endpoints,
    mock_chain_deps,
)
from tests.test_tool_execution import call_tool_directly

REGISTERED_RECORD = (
    DUMMY_HASH_BYTES,
    DUMMY_ADDRESS,
    1700000000,
    "swarm-provenance",
    [],
    [],
    0,
)
STORAGE_REF = "c" * 64


@pytest.fixture
def no_wallet_server(endpoints):
    """Server with chain enabled, no wallet, and preset RPC endpoints."""
    settings = MagicMock(
        chain_name="base-sepolia",
        chain_rpc_url=None,
        chain_rpc_urls=None,
        chain_contract_address=None,
        chain_explorer_url=None,
    )
    contract = endpoints["web3"][endpoints["urls"][0]].eth.contract.return_value
    contract.functions.getDataRecord.return_value.call.return_value = REGISTERED_RECORD
    contract.functions.getDataHashByStorageRef.return_value.call.return_value = (
        DUMMY_HASH_BYTES
    )
    contract.functions.getTransformationLinks.return_value.call.return_value = []
    contract.functions.getTransformationParents.return_value.call.return_value = []
    with (
        patch("swarm_provenance_mcp.server.CHAIN_AVAILABLE", True),
        patch("swarm_provenance_mcp.server.chain_client", None),
        patch("swarm_provenance_mcp.server.settings", settings),
    ):
        yield create_server()


TOOLS = [
    ("verify_hash", {"swarm_hash": DUMMY_HASH}, "IS registered on-chain"),
    ("get_provenance", {"swarm_hash": DUMMY_HASH}, "Provenance Record"),
    ("get_provenance_chain", {"swarm_hash": DUMMY_HASH}, "Provenance Chain (1 entry)"),
    ("lookup_by_storage_ref", {"storage_ref": STORAGE_REF}, "Found"),
]


class TestReadOnlyToolsFailOver:
    @pytest.mark.parametrize("tool,args,expected", TOOLS)
    async def test_degraded_primary_served_by_fallback(
        self, no_wallet_server, endpoints, tool, args, expected
    ):
        endpoints["degrade"](endpoints["urls"][0])

        result = await call_tool_directly(no_wallet_server, tool, args)

        text = result.content[0].text
        assert not result.isError, text
        assert expected in text

    @pytest.mark.parametrize("tool,args,expected", TOOLS)
    async def test_all_degraded_is_retryable(
        self, no_wallet_server, endpoints, tool, args, expected
    ):
        for url in endpoints["urls"]:
            endpoints["degrade"](url)

        result = await call_tool_directly(no_wallet_server, tool, args)

        text = result.content[0].text
        assert result.isError
        assert "retryable: true" in text
        assert "_next: chain_health" in text

    async def test_chain_outage_is_not_reported_as_unregistered(
        self, no_wallet_server, endpoints
    ):
        """The old inline traversal swallowed RPC errors into 'not registered'."""
        for url in endpoints["urls"]:
            endpoints["degrade"](url)

        result = await call_tool_directly(
            no_wallet_server, "get_provenance_chain", {"swarm_hash": DUMMY_HASH}
        )

        assert "not registered" not in result.content[0].text


class TestReadOnlyClient:
    def test_reads_work_without_wallet_key(self, endpoints):
        from swarm_provenance_mcp.chain.client import ChainClient

        client = ChainClient(chain="base-sepolia", read_only=True)
        assert client.verify(DUMMY_HASH) is False

    def test_writes_raise_configuration_error(self, endpoints):
        from swarm_provenance_mcp.chain.client import ChainClient

        client = ChainClient(chain="base-sepolia", read_only=True)
        with pytest.raises(ChainConfigurationError, match="requires a wallet"):
            client.anchor(swarm_hash=DUMMY_HASH)


class TestFeatureDetectionTransportErrors:
    """A 503 during feature detection must not be cached as 'unsupported'."""

    @pytest.mark.parametrize(
        "method,fn",
        [
            ("supports_transformation_links", "getTransformationLinks"),
            ("supports_storage_ref", "getDataHashByStorageRef"),
        ],
    )
    def test_transport_error_propagates_and_is_not_cached(
        self, mock_chain_deps, method, fn
    ):
        from swarm_provenance_mcp.chain.contract import DataProvenanceContract

        call = getattr(mock_chain_deps["contract"].functions, fn).return_value.call
        call.side_effect = _http_error(503)
        contract = DataProvenanceContract(
            web3=mock_chain_deps["web3_instance"],
            contract_address="0x3945aDfd5Df9ab2F5cB4Ca0eb3D4384CC3650322",
        )

        with pytest.raises(Exception, match="503"):
            getattr(contract, method)()

        call.side_effect = None
        call.return_value = []
        assert getattr(contract, method)() is True

    def test_revert_still_means_unsupported(self, mock_chain_deps):
        from swarm_provenance_mcp.chain.contract import DataProvenanceContract

        fn = mock_chain_deps["contract"].functions.getTransformationLinks
        fn.return_value.call.side_effect = ValueError("execution reverted")
        contract = DataProvenanceContract(
            web3=mock_chain_deps["web3_instance"],
            contract_address="0x3945aDfd5Df9ab2F5cB4Ca0eb3D4384CC3650322",
        )
        assert contract.supports_transformation_links() is False
