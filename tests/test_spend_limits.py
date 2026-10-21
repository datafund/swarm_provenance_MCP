"""Fee cap, session budget, confirmation step, tool annotations and the
stamp duration cap (#161)."""

from unittest.mock import MagicMock, patch

import pytest
from mcp.types import ListToolsRequest

from swarm_provenance_mcp.chain.exceptions import ChainSpendLimitError
from swarm_provenance_mcp.chain.spend import (
    ETH,
    GWEI,
    MAINNET_DEFAULTS,
    TESTNET_DEFAULTS,
    SpendGuard,
)
from swarm_provenance_mcp.config import settings

BASE_MAINNET = 8453
BASE_SEPOLIA = 84532
WALLET = "0x742d35Cc6634C0532925a3b844Bc9e7595f8fE00"
H1 = "a1" * 32
H2 = "b2" * 32
TX_HASH = bytes.fromhex("bb" * 32)


def _web3(base_fee_wei=5_000_000, tip_wei=1_000_000, legacy_gas_price=None):
    """Web3 mock with Base-like fees (0.005 gwei base, 0.001 gwei tip)."""
    w3 = MagicMock()
    if legacy_gas_price is None:
        w3.eth.get_block.return_value = {"baseFeePerGas": base_fee_wei}
    else:
        w3.eth.get_block.return_value = {}
        w3.eth.gas_price = legacy_gas_price
    w3.eth.max_priority_fee = tip_wei
    w3.eth.get_transaction_count.return_value = 0
    w3.eth.estimate_gas.return_value = 100_000
    w3.eth.send_raw_transaction.return_value = TX_HASH
    w3.eth.wait_for_transaction_receipt.return_value = {
        "status": 1,
        "transactionHash": TX_HASH,
        "blockNumber": 1,
        "gasUsed": 90_000,
        "effectiveGasPrice": base_fee_wei + tip_wei,
    }
    return w3


def _client(w3, chain_id=BASE_MAINNET, **limits):
    from swarm_provenance_mcp.chain.client import ChainClient

    client = ChainClient.__new__(ChainClient)
    client._provider = MagicMock()
    client._provider.web3 = w3
    client._provider.chain_id = chain_id
    client._provider._rpc_urls = ["http://rpc"]
    client._wallet = MagicMock()
    client._wallet.address = WALLET
    client._wallet.sign_transaction.return_value = b"\x00"
    client._contract = MagicMock()
    client._contract_web3 = w3
    client._gas_limit = None
    client._gas_limit_multiplier = 1.2
    client._spend = SpendGuard(chain_id, **limits)
    return client


def _tx():
    # As built by web3: fee fields filled from the RPC's own suggestion.
    return {
        "from": WALLET,
        "to": "0x" + "11" * 20,
        "data": "0x",
        "maxFeePerGas": 5000 * GWEI,
        "maxPriorityFeePerGas": 5000 * GWEI,
    }


class TestDefaults:
    def test_mainnet_defaults_are_low(self):
        guard = SpendGuard(BASE_MAINNET)
        assert guard.mainnet
        assert guard.max_fee_wei == int(MAINNET_DEFAULTS.max_fee_gwei * GWEI)
        assert guard.budget_wei == int(MAINNET_DEFAULTS.budget_eth * ETH)
        assert guard.max_txs == MAINNET_DEFAULTS.max_txs
        assert MAINNET_DEFAULTS.max_fee_gwei <= TESTNET_DEFAULTS.max_fee_gwei
        assert MAINNET_DEFAULTS.budget_eth <= TESTNET_DEFAULTS.budget_eth

    def test_unknown_chain_treated_as_mainnet(self):
        assert SpendGuard(999_999).mainnet

    def test_base_sepolia_is_testnet(self):
        guard = SpendGuard(BASE_SEPOLIA)
        assert not guard.mainnet
        assert guard.max_txs == TESTNET_DEFAULTS.max_txs

    def test_configured_values_override(self):
        guard = SpendGuard(BASE_MAINNET, max_fee_gwei=0.1, budget_eth=0.5, max_txs=3)
        assert guard.max_fee_wei == int(0.1 * GWEI)
        assert guard.budget_wei == int(0.5 * ETH)
        assert guard.max_txs == 3


class TestFeeCap:
    def test_inflated_priority_fee_refused(self):
        guard = SpendGuard(BASE_MAINNET)
        with pytest.raises(ChainSpendLimitError, match="fee cap"):
            guard.fee_fields(_web3(tip_wei=5000 * GWEI))

    def test_high_base_fee_refused(self):
        guard = SpendGuard(BASE_MAINNET, max_fee_gwei=0.5)
        with pytest.raises(ChainSpendLimitError):
            guard.fee_fields(_web3(base_fee_wei=1 * GWEI))

    def test_normal_fee_sets_explicit_fields(self):
        guard = SpendGuard(BASE_MAINNET)
        fields, max_fee = guard.fee_fields(_web3())
        assert fields == {
            "maxFeePerGas": 2 * 5_000_000 + 1_000_000,
            "maxPriorityFeePerGas": 1_000_000,
        }
        assert max_fee == 11_000_000

    def test_max_fee_clamped_to_cap(self):
        guard = SpendGuard(BASE_MAINNET, max_fee_gwei=0.5)
        # base + tip fits under the cap, 2 * base + tip does not
        fields, max_fee = guard.fee_fields(
            _web3(base_fee_wei=int(0.3 * GWEI), tip_wei=int(0.01 * GWEI))
        )
        assert max_fee == fields["maxFeePerGas"] == int(0.5 * GWEI)

    def test_legacy_gas_price_above_cap_refused(self):
        guard = SpendGuard(BASE_MAINNET)
        with pytest.raises(ChainSpendLimitError):
            guard.fee_fields(_web3(legacy_gas_price=2 * GWEI))

    def test_legacy_gas_price_within_cap(self):
        guard = SpendGuard(BASE_MAINNET)
        fields, max_fee = guard.fee_fields(_web3(legacy_gas_price=1_000_000))
        assert fields == {"gasPrice": 1_000_000}


class TestBudget:
    def test_tx_count_limit(self):
        guard = SpendGuard(BASE_MAINNET, max_txs=2)
        guard.reserve(1)
        guard.reserve(1)
        with pytest.raises(ChainSpendLimitError, match="transaction limit"):
            guard.reserve(1)

    def test_budget_limit(self):
        guard = SpendGuard(BASE_MAINNET, budget_eth=0.001)
        guard.reserve(int(0.0006 * ETH))
        with pytest.raises(ChainSpendLimitError, match="budget"):
            guard.reserve(int(0.0006 * ETH))

    def test_settle_charges_actual_cost(self):
        guard = SpendGuard(BASE_MAINNET, budget_eth=0.001)
        reserved = guard.reserve(int(0.0009 * ETH))
        guard.settle(reserved, {"gasUsed": 100_000, "effectiveGasPrice": GWEI // 100})
        assert guard.spent_wei == 100_000 * (GWEI // 100)

    def test_settle_without_price_keeps_worst_case(self):
        guard = SpendGuard(BASE_MAINNET)
        reserved = guard.reserve(1000)
        guard.settle(reserved, {"gasUsed": 10})
        assert guard.spent_wei == 1000

    def test_release(self):
        guard = SpendGuard(BASE_MAINNET, max_txs=1)
        reserved = guard.reserve(1000)
        guard.release(reserved)
        assert guard.tx_count == 0 and guard.spent_wei == 0
        guard.reserve(1000)  # the slot is free again


class TestSendTransaction:
    def test_inflated_fee_suggestion_refused_before_signing(self):
        w3 = _web3(tip_wei=5000 * GWEI)
        client = _client(w3)
        with pytest.raises(ChainSpendLimitError):
            client._send_transaction(_tx())
        client._wallet.sign_transaction.assert_not_called()
        w3.eth.send_raw_transaction.assert_not_called()
        assert client.spend.tx_count == 0

    def test_rpc_suggested_fee_fields_replaced(self):
        w3 = _web3()
        client = _client(w3)
        client._send_transaction(_tx())
        signed = client._wallet.sign_transaction.call_args[0][0]
        assert signed["maxFeePerGas"] == 11_000_000
        assert signed["maxPriorityFeePerGas"] == 1_000_000
        assert "gasPrice" not in signed

    def test_session_tx_limit_enforced(self):
        w3 = _web3()
        client = _client(w3, max_txs=2)
        client._send_transaction(_tx())
        client._send_transaction(_tx())
        with pytest.raises(ChainSpendLimitError, match="transaction limit"):
            client._send_transaction(_tx())
        assert w3.eth.send_raw_transaction.call_count == 2

    def test_session_budget_enforced(self):
        w3 = _web3()
        # Worst case per tx: 120_000 gas x 0.011 gwei = 1.32e12 wei. The first
        # settles to 90_000 x 0.006 gwei = 0.54e12; the second would need
        # 0.54e12 + 1.32e12 > 1.5e12.
        client = _client(w3, budget_eth=1.5e-6)
        client._send_transaction(_tx())
        with pytest.raises(ChainSpendLimitError, match="budget"):
            client._send_transaction(_tx())
        assert w3.eth.send_raw_transaction.call_count == 1

    def test_sign_failure_releases_reservation(self):
        w3 = _web3()
        client = _client(w3, max_txs=1)
        client._wallet.sign_transaction.side_effect = ValueError("bad key")
        with pytest.raises(Exception):
            client._send_transaction(_tx())
        assert client.spend.tx_count == 0

    def test_spend_limit_is_not_failed_over(self):
        """A refusal must not be retried on another RPC endpoint."""
        w3 = _web3(tip_wei=5000 * GWEI)
        client = _client(w3)
        client._contract.build_set_data_status_tx.return_value = _tx()
        client._failover = MagicMock()
        with pytest.raises(ChainSpendLimitError):
            client.set_status(H1, 1)
        client._failover.assert_not_called()


@pytest.fixture
def mainnet_server(monkeypatch):
    """Server module with a wallet client on a mainnet chain id."""
    from swarm_provenance_mcp import server

    monkeypatch.setattr(settings, "chain_require_confirm", None)
    client = MagicMock()
    client.spend = SpendGuard(BASE_MAINNET)
    client.address = WALLET
    client.chain = "base"
    client.fee_quote.return_value = {
        **client.spend.summary(),
        "max_fee_per_gas_gwei": 0.011,
    }
    monkeypatch.setattr(server, "CHAIN_AVAILABLE", True)
    monkeypatch.setattr(server, "chain_client", client)
    return server, client


class TestConfirmation:
    async def test_mainnet_write_without_confirm_sends_nothing(self, mainnet_server):
        server, client = mainnet_server
        result = await server.handle_anchor_hash({"swarm_hash": H1})
        text = result.content[0].text
        assert "Confirmation required: nothing was sent" in text
        assert "confirm: true" in text
        assert "0.5 gwei" in text  # the fee cap
        client.anchor.assert_not_called()
        client.anchor_for.assert_not_called()

    @pytest.mark.parametrize(
        "handler,args,method",
        [
            ("handle_anchor_hash", {"swarm_hash": H1}, "anchor"),
            (
                "handle_record_transform",
                {"original_hash": H1, "new_hash": H2, "description": "d"},
                "transform",
            ),
            (
                "handle_record_merge_transform",
                {"source_hashes": [H1, H2], "new_hash": "c3" * 32},
                "merge_transform",
            ),
            (
                "handle_set_storage_ref",
                {"data_hash": H1, "storage_ref": H2},
                "set_storage_ref",
            ),
        ],
    )
    async def test_every_spending_tool_gated(
        self, mainnet_server, handler, args, method
    ):
        server, client = mainnet_server
        await getattr(server, handler)(args)
        getattr(client, method).assert_not_called()
        await getattr(server, handler)({**args, "confirm": True})
        getattr(client, method).assert_called_once()

    async def test_string_true_is_not_confirmation(self, mainnet_server):
        server, client = mainnet_server
        await server.handle_anchor_hash({"swarm_hash": H1, "confirm": "true"})
        client.anchor.assert_not_called()

    async def test_restrict_original_quotes_two_transactions(self, mainnet_server):
        server, client = mainnet_server
        result = await server.handle_record_transform(
            {
                "original_hash": H1,
                "new_hash": H2,
                "description": "d",
                "restrict_original": True,
            }
        )
        assert "2 transaction(s)" in result.content[0].text
        client.set_status.assert_not_called()

    async def test_testnet_default_needs_no_confirm(self, mainnet_server):
        server, client = mainnet_server
        client.spend = SpendGuard(BASE_SEPOLIA)
        await server.handle_anchor_hash({"swarm_hash": H1})
        client.anchor.assert_called_once()

    async def test_require_confirm_setting_on_testnet(
        self, mainnet_server, monkeypatch
    ):
        server, client = mainnet_server
        client.spend = SpendGuard(BASE_SEPOLIA)
        monkeypatch.setattr(settings, "chain_require_confirm", True)
        await server.handle_anchor_hash({"swarm_hash": H1})
        client.anchor.assert_not_called()

    async def test_require_confirm_false_on_mainnet(self, mainnet_server, monkeypatch):
        server, client = mainnet_server
        monkeypatch.setattr(settings, "chain_require_confirm", False)
        await server.handle_anchor_hash({"swarm_hash": H1})
        client.anchor.assert_called_once()

    async def test_spend_limit_reported_as_not_retryable(self, mainnet_server):
        server, client = mainnet_server
        client.anchor.side_effect = ChainSpendLimitError("over the fee cap")
        result = await server.handle_anchor_hash({"swarm_hash": H1, "confirm": True})
        assert result.isError
        assert "over the fee cap" in result.content[0].text
        assert "retryable: false" in result.content[0].text


async def _list_tools(monkeypatch):
    from swarm_provenance_mcp import server

    monkeypatch.setattr(server, "CHAIN_AVAILABLE", True)
    monkeypatch.setattr(settings, "chain_enabled", True)
    srv = server.create_server()
    handler = srv.request_handlers[ListToolsRequest]
    result = await handler(ListToolsRequest(method="tools/list"))
    return {t.name: t for t in result.root.tools}


class TestAnnotations:
    async def test_every_tool_annotated(self, monkeypatch):
        tools = await _list_tools(monkeypatch)
        assert len(tools) == 20
        for name, tool in tools.items():
            a = tool.annotations
            assert a is not None, name
            assert None not in (
                a.readOnlyHint,
                a.destructiveHint,
                a.idempotentHint,
                a.openWorldHint,
            ), name

    async def test_spending_tools(self, monkeypatch):
        tools = await _list_tools(monkeypatch)
        for name in (
            "anchor_hash",
            "record_transform",
            "record_merge_transform",
            "set_storage_ref",
            "purchase_stamp",
            "extend_stamp",
        ):
            a = tools[name].annotations
            assert a.readOnlyHint is False, name
            assert a.destructiveHint is True, name
            assert a.idempotentHint is False, name

    async def test_read_tools(self, monkeypatch):
        tools = await _list_tools(monkeypatch)
        for name in (
            "verify_hash",
            "get_provenance",
            "get_provenance_chain",
            "lookup_by_storage_ref",
            "chain_health",
            "chain_balance",
            "download_data",
            "list_stamps",
            "get_stamp_status",
            "check_stamp_health",
            "health_check",
        ):
            assert tools[name].annotations.readOnlyHint is True, name

    async def test_upload_is_not_read_only(self, monkeypatch):
        tools = await _list_tools(monkeypatch)
        assert tools["upload_data"].annotations.readOnlyHint is False

    async def test_spending_chain_tools_take_confirm(self, monkeypatch):
        tools = await _list_tools(monkeypatch)
        for name in (
            "anchor_hash",
            "record_transform",
            "record_merge_transform",
            "set_storage_ref",
        ):
            prop = tools[name].inputSchema["properties"]["confirm"]
            assert prop["type"] == "boolean"
        assert "confirm" not in tools["verify_hash"].inputSchema["properties"]


class TestStampDurationCap:
    async def test_schema_maximum(self, monkeypatch):
        tools = await _list_tools(monkeypatch)
        for name in ("purchase_stamp", "extend_stamp"):
            prop = tools[name].inputSchema["properties"]["duration_hours"]
            assert prop["maximum"] == settings.max_stamp_duration_hours == 8760

    async def test_purchase_above_max_refused(self):
        from swarm_provenance_mcp import server

        with patch.object(server, "gateway_client") as gw:
            result = await server.handle_purchase_stamp({"duration_hours": 8761})
        assert result.isError
        assert "at most 8760" in result.content[0].text
        gw.purchase_stamp.assert_not_called()

    async def test_extend_above_max_refused(self):
        from swarm_provenance_mcp import server

        with patch.object(server, "gateway_client") as gw:
            result = await server.handle_extend_stamp(
                {"stamp_id": "ab" * 32, "duration_hours": 10_000}
            )
        assert result.isError
        assert "at most 8760" in result.content[0].text
        gw.extend_stamp.assert_not_called()

    def test_max_allowed(self):
        from swarm_provenance_mcp.server import validate_stamp_duration_hours

        validate_stamp_duration_hours(8760)  # does not raise
