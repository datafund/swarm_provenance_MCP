"""Fee cap, session budget, confirmation step, tool annotations and the
stamp duration cap (#161)."""

from unittest.mock import MagicMock, patch

import pytest
from mcp.types import ListToolsRequest

from swarm_provenance_mcp.chain.exceptions import ChainSpendLimitError
from swarm_provenance_mcp.chain.spend import (
    ACTIVE_HOLD,
    DEFAULT_L1_FEE_ALLOWANCE_ETH,
    DEFAULT_PRIORITY_FEE_WEI,
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
H3 = "c3" * 32
TX_HASH = bytes.fromhex("bb" * 32)


def _web3(base_fee_wei=5_000_000, tip_wei=1_000_000, legacy_gas_price=None):
    """Web3 mock with Base-like fees (0.005 gwei base, 0.001 gwei tip)."""
    w3 = MagicMock()
    if legacy_gas_price is None:
        w3.eth.get_block.return_value = {"baseFeePerGas": base_fee_wei}
    else:
        w3.eth.get_block.return_value = {}
        w3.eth.gas_price = legacy_gas_price
    # Five blocks of 50th-percentile priority fees.
    w3.eth.fee_history.return_value = {"reward": [[tip_wei]] * 5}
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
    limits.setdefault("l1_fee_allowance_eth", 0)  # L2-only budget arithmetic
    client._spend = SpendGuard(chain_id, **limits)
    return client


def _quote_id(result) -> str:
    text = result.content[0].text
    return text.split("quote_id: ")[1].split()[0].rstrip(".")


async def _quote_and_confirm(server, handler, args):
    """Get a quote, then confirm it with the same arguments."""
    quote = await getattr(server, handler)(args)
    return await getattr(server, handler)(
        {**args, "confirm": True, "quote_id": _quote_id(quote)}
    )


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
        assert f'swarm_hash: "{H1}"' in text  # the quote echoes the action
        assert "quote_id: " in text
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
        # confirm alone, without a quote_id, is not enough
        await getattr(server, handler)({**args, "confirm": True})
        getattr(client, method).assert_not_called()
        await _quote_and_confirm(server, handler, args)
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

    async def test_quote_for_other_arguments_refused(self, mainnet_server):
        """The reviewer's replay: quote H1->H2, confirm H2->H3 + restrict."""
        server, client = mainnet_server
        quote = await server.handle_record_transform(
            {"original_hash": H1, "new_hash": H2, "description": "approved"}
        )
        result = await server.handle_record_transform(
            {
                "original_hash": H2,
                "new_hash": H3,
                "description": "different",
                "restrict_original": True,
                "confirm": True,
                "quote_id": _quote_id(quote),
            }
        )
        client.transform.assert_not_called()
        client.set_status.assert_not_called()
        text = result.content[0].text
        assert "issued for different arguments" in text
        assert f'original_hash: "{H2}"' in text  # fresh quote for what was asked

    async def test_quote_for_other_tool_refused(self, mainnet_server):
        server, client = mainnet_server
        quote = await server.handle_set_storage_ref(
            {"data_hash": H1, "storage_ref": H2}
        )
        await server.handle_anchor_hash(
            {"swarm_hash": H1, "confirm": True, "quote_id": _quote_id(quote)}
        )
        client.anchor.assert_not_called()

    async def test_quote_is_single_use(self, mainnet_server):
        server, client = mainnet_server
        args = {"swarm_hash": H1}
        quote = await server.handle_anchor_hash(args)
        confirmed = {**args, "confirm": True, "quote_id": _quote_id(quote)}
        await server.handle_anchor_hash(confirmed)
        await server.handle_anchor_hash(confirmed)
        client.anchor.assert_called_once()

    async def test_expired_quote_refused(self, mainnet_server, monkeypatch):
        server, client = mainnet_server
        args = {"swarm_hash": H1}
        quote = await server.handle_anchor_hash(args)
        later = server._now() + server.QUOTE_TTL_SECONDS + 1
        monkeypatch.setattr(server, "_now", lambda: later)
        await server.handle_anchor_hash(
            {**args, "confirm": True, "quote_id": _quote_id(quote)}
        )
        client.anchor.assert_not_called()

    async def test_unknown_quote_id_refused(self, mainnet_server):
        server, client = mainnet_server
        await server.handle_anchor_hash(
            {"swarm_hash": H1, "confirm": True, "quote_id": "0" * 32}
        )
        client.anchor.assert_not_called()

    async def test_quote_id_without_confirm_refused(self, mainnet_server):
        server, client = mainnet_server
        quote = await server.handle_anchor_hash({"swarm_hash": H1})
        await server.handle_anchor_hash(
            {"swarm_hash": H1, "quote_id": _quote_id(quote)}
        )
        client.anchor.assert_not_called()

    async def test_owner_with_storage_ref_rejected(self, mainnet_server):
        server, client = mainnet_server
        result = await server.handle_anchor_hash(
            {"swarm_hash": H1, "owner": "0x" + "ab" * 20, "storage_ref": H2}
        )
        assert result.isError
        assert "storage_ref cannot be combined with owner" in result.content[0].text
        assert "quote_id" not in result.content[0].text
        client.anchor_for.assert_not_called()

    async def test_hash_case_and_empty_storage_ref_normalised(self, mainnet_server):
        server, client = mainnet_server
        quote = await server.handle_anchor_hash({"swarm_hash": H1.upper()})
        await server.handle_anchor_hash(
            {
                "swarm_hash": H1,
                "storage_ref": "",
                "confirm": True,
                "quote_id": _quote_id(quote),
            }
        )
        client.anchor.assert_called_once()

    async def test_quote_store_bounded(self, mainnet_server, monkeypatch):
        server, client = mainnet_server
        monkeypatch.setattr(server, "MAX_ISSUED_QUOTES", 3)
        monkeypatch.setattr(server, "_issued_quotes", {})
        first = await server.handle_anchor_hash({"swarm_hash": H1})
        for h in (H2, H3, "d4" * 32):
            await server.handle_anchor_hash({"swarm_hash": h})
        assert len(server._issued_quotes) == 3
        await server.handle_anchor_hash(
            {"swarm_hash": H1, "confirm": True, "quote_id": _quote_id(first)}
        )
        client.anchor.assert_not_called()  # the oldest quote was evicted

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
        result = await _quote_and_confirm(
            server, "handle_anchor_hash", {"swarm_hash": H1}
        )
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
            assert tools[name].inputSchema["properties"]["quote_id"]["type"] == "string"
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


class TestPriorityFee:
    def test_missing_fee_history_uses_small_default(self):
        """web3's own fallback clamps the tip to >= 1 gwei; ours must not."""
        w3 = _web3()
        w3.eth.fee_history.side_effect = ValueError("method not found")
        fields, _ = SpendGuard(BASE_MAINNET).fee_fields(w3)
        assert fields["maxPriorityFeePerGas"] == DEFAULT_PRIORITY_FEE_WEI
        assert DEFAULT_PRIORITY_FEE_WEI < GWEI

    def test_empty_fee_history_uses_default(self):
        w3 = _web3()
        w3.eth.fee_history.return_value = {"reward": []}
        fields, _ = SpendGuard(BASE_MAINNET).fee_fields(w3)
        assert fields["maxPriorityFeePerGas"] == DEFAULT_PRIORITY_FEE_WEI

    def test_zero_rewards_use_default(self):
        w3 = _web3()
        w3.eth.fee_history.return_value = {"reward": [[0]] * 5}
        assert SpendGuard.priority_fee(w3) == DEFAULT_PRIORITY_FEE_WEI

    def test_zero_median_uses_default(self):
        w3 = _web3()
        w3.eth.fee_history.return_value = {
            "reward": [[0], [0], [0], [5_000_000], [6_000_000]]
        }
        assert SpendGuard.priority_fee(w3) == DEFAULT_PRIORITY_FEE_WEI

    def test_median_of_history(self):
        w3 = _web3()
        w3.eth.fee_history.return_value = {
            "reward": [[1], [5_000_000], [2_000_000], [3_000_000], [4_000_000]]
        }
        assert SpendGuard.priority_fee(w3) == 3_000_000

    def test_message_points_at_rpc_only_for_outsized_tip(self):
        with pytest.raises(ChainSpendLimitError) as e:
            SpendGuard(BASE_MAINNET).fee_fields(_web3(tip_wei=5000 * GWEI))
        assert "check the endpoint" in str(e.value)
        with pytest.raises(ChainSpendLimitError) as e:
            SpendGuard(BASE_MAINNET).fee_fields(_web3(base_fee_wei=GWEI))
        assert "check the endpoint" not in str(e.value)
        assert "retry later" not in str(e.value)


class TestL1Fee:
    def test_settle_adds_l1_fee(self):
        guard = SpendGuard(BASE_MAINNET)
        reserved = guard.reserve(10**14)
        guard.settle(
            reserved,
            {"gasUsed": 100_000, "effectiveGasPrice": 6_000_000, "l1Fee": 10**10},
        )
        assert guard.spent_wei == 100_000 * 6_000_000 + 10**10

    def test_l1_fee_above_allowance_counted_in_full(self):
        """A spike beyond the reservation is still charged at its real cost."""
        guard = SpendGuard(BASE_MAINNET)
        reserved = guard.reserve(2 * 10**12)
        guard.settle(
            reserved,
            {"gasUsed": 100_000, "effectiveGasPrice": 6_000_000, "l1Fee": 5 * 10**15},
        )
        assert guard.spent_wei == 100_000 * 6_000_000 + 5 * 10**15

    def test_settle_reads_hex_l1_fee(self):
        guard = SpendGuard(BASE_MAINNET)
        reserved = guard.reserve(10**14)
        guard.settle(
            reserved,
            {"gasUsed": 1, "effectiveGasPrice": 1, "l1Fee": hex(10**10)},
        )
        assert guard.spent_wei == 1 + 10**10

    def test_reservation_includes_l1_allowance(self):
        # L2 worst case: 120_000 gas x 0.011 gwei = 1.32e12 wei
        budget_eth = 120_000 * 11_000_000 / ETH
        fits = _client(_web3(), budget_eth=budget_eth, l1_fee_allowance_eth=0)
        fits._send_transaction(_tx())
        too_small = _client(
            _web3(), budget_eth=budget_eth, l1_fee_allowance_eth=0.000001
        )
        with pytest.raises(ChainSpendLimitError, match="budget"):
            too_small._send_transaction(_tx())

    def test_default_allowance(self):
        assert SpendGuard(BASE_MAINNET).l1_allowance_wei == int(
            DEFAULT_L1_FEE_ALLOWANCE_ETH * ETH
        )


class TestRejectedBroadcast:
    def test_node_rejection_releases_slot(self):
        from web3.exceptions import Web3RPCError

        w3 = _web3()
        w3.eth.send_raw_transaction.side_effect = Web3RPCError("nonce too low")
        client = _client(w3, max_txs=1)
        with pytest.raises(Exception):
            client._send_transaction(_tx())
        assert client.spend.tx_count == 0
        assert client.spend.spent_wei == 0

    def test_transport_failure_keeps_reservation(self):
        from requests import Response
        from requests.exceptions import HTTPError

        response = Response()
        response.status_code = 503
        w3 = _web3()
        w3.eth.send_raw_transaction.side_effect = HTTPError(
            "503 Server Error", response=response
        )
        client = _client(w3, max_txs=1)
        with pytest.raises(Exception):
            client._send_transaction(_tx())
        assert client.spend.tx_count == 1  # may have reached the network

    @pytest.mark.parametrize(
        "message",
        [
            "nonce too low",
            "replacement transaction underpriced",
            "insufficient funds for gas * price + value",
            "intrinsic gas too low",
            "exceeds block gas limit",
            "invalid sender",
        ],
    )
    def test_pre_mempool_rejections_release(self, message):
        from web3.exceptions import Web3RPCError

        w3 = _web3()
        w3.eth.send_raw_transaction.side_effect = Web3RPCError(message)
        client = _client(w3, max_txs=1)
        with pytest.raises(Exception):
            client._send_transaction(_tx())
        assert client.spend.tx_count == 0

    @pytest.mark.parametrize(
        "error",
        [
            "internal error",
            "request timed out",
            "upstream request timeout",
            "header not found",
            "already known",
            "JSONDECODE",
        ],
    )
    def test_ambiguous_errors_keep_reservation(self, error):
        import json

        from web3.exceptions import Web3RPCError

        exc = (
            json.JSONDecodeError("Expecting value", "", 0)
            if error == "JSONDECODE"
            else Web3RPCError(error)
        )
        w3 = _web3()
        w3.eth.send_raw_transaction.side_effect = exc
        client = _client(w3, max_txs=1)
        with pytest.raises(Exception):
            client._send_transaction(_tx())
        assert client.spend.tx_count == 1

    def test_already_known_keeps_reservation(self):
        from web3.exceptions import Web3RPCError

        w3 = _web3()
        w3.eth.send_raw_transaction.side_effect = Web3RPCError("already known")
        client = _client(w3, max_txs=1)
        with pytest.raises(Exception):
            client._send_transaction(_tx())
        assert client.spend.tx_count == 1


def _restrict_client(max_txs):
    """ChainClient with a real SpendGuard whose writes succeed."""
    w3 = _web3()
    client = _client(w3, max_txs=max_txs)
    client._contract.supports_transformation_links.return_value = False
    client._provider.deploy_block = None
    client._provider.get_explorer_tx_url.return_value = None
    client._contract.build_record_transformation_tx.return_value = _tx()
    client._contract.build_set_data_status_tx.side_effect = lambda **kw: _tx()
    client.balance = MagicMock(side_effect=Exception("skip"))
    return client, w3


class TestRestrictOriginal:
    ARGS = {
        "original_hash": H1,
        "new_hash": H2,
        "description": "d",
        "restrict_original": True,
    }

    async def test_both_slots_checked_before_first_send(self, monkeypatch):
        from swarm_provenance_mcp import server

        client, w3 = _restrict_client(max_txs=1)
        monkeypatch.setattr(server, "CHAIN_AVAILABLE", True)
        monkeypatch.setattr(server, "chain_client", client)
        result = await server.handle_record_transform(dict(self.ARGS))
        assert result.isError
        assert "transaction limit" in result.content[0].text
        w3.eth.send_raw_transaction.assert_not_called()
        assert client.spend.tx_count == 0

    async def test_both_sent_from_the_hold(self, monkeypatch):
        from swarm_provenance_mcp import server

        client, w3 = _restrict_client(max_txs=2)
        monkeypatch.setattr(server, "CHAIN_AVAILABLE", True)
        monkeypatch.setattr(server, "chain_client", client)
        result = await server.handle_record_transform(dict(self.ARGS))
        assert not result.isError, result.content[0].text
        assert "RESTRICTED" in result.content[0].text
        assert w3.eth.send_raw_transaction.call_count == 2
        assert client.spend.tx_count == 2

    async def test_unused_hold_returned(self, monkeypatch):
        from swarm_provenance_mcp import server
        from swarm_provenance_mcp.chain.exceptions import (
            TransformationAlreadyExistsError,
        )

        client, w3 = _restrict_client(max_txs=2)
        client.transform = MagicMock(
            side_effect=TransformationAlreadyExistsError("exists", H1, H2)
        )
        monkeypatch.setattr(server, "CHAIN_AVAILABLE", True)
        monkeypatch.setattr(server, "chain_client", client)
        await server.handle_record_transform(dict(self.ARGS))
        assert client.spend.tx_count == 0
        assert client.spend.spent_wei == 0

    async def test_second_tx_failure_is_an_error(self, monkeypatch):
        from swarm_provenance_mcp import server

        client, w3 = _restrict_client(max_txs=2)
        client._contract.build_set_data_status_tx.side_effect = ChainSpendLimitError(
            "fee above the cap"
        )
        monkeypatch.setattr(server, "CHAIN_AVAILABLE", True)
        monkeypatch.setattr(server, "chain_client", client)
        result = await server.handle_record_transform(dict(self.ARGS))
        assert result.isError
        text = result.content[0].text
        assert "failed to restrict" in text
        assert f"IS recorded on-chain (tx {TX_HASH.hex()})" in text


class TestHold:
    def test_hold_reserves_all_slots(self):
        guard = SpendGuard(BASE_MAINNET, max_txs=3)
        hold = guard.hold(2, 100)
        assert guard.tx_count == 2 and guard.spent_wei == 200
        with pytest.raises(ChainSpendLimitError):
            guard.hold(2, 100)
        guard.end_hold(hold)
        assert guard.tx_count == 0 and guard.spent_wei == 0

    def test_reserve_draws_from_active_hold(self):
        guard = SpendGuard(BASE_MAINNET, max_txs=2)
        hold = guard.hold(2, 100)
        token = ACTIVE_HOLD.set(hold)
        try:
            assert guard.reserve(80) == 100
            assert guard.reserve(150) == 150  # tops up the difference
        finally:
            ACTIVE_HOLD.reset(token)
        assert guard.tx_count == 2 and guard.spent_wei == 250
        assert hold.slots_left == 0


class TestConfigValidation:
    @pytest.mark.parametrize(
        "field,value",
        [
            ("chain_max_fee_gwei", 0),
            ("chain_max_fee_gwei", -1),
            ("chain_session_budget_eth", 0),
            ("chain_max_tx_per_session", 0),
            ("chain_l1_fee_allowance_eth", -0.1),
            ("max_stamp_duration_hours", 23),
        ],
    )
    def test_invalid_limits_refused_at_startup(self, field, value):
        from pydantic import ValidationError

        from swarm_provenance_mcp.config import Settings

        with pytest.raises(ValidationError):
            Settings(**{field: value})

    @pytest.mark.parametrize(
        "field", ["chain_max_fee_gwei", "chain_session_budget_eth"]
    )
    @pytest.mark.parametrize("value", [float("inf"), float("nan")])
    def test_non_finite_limits_refused(self, field, value):
        from pydantic import ValidationError

        from swarm_provenance_mcp.config import Settings

        with pytest.raises(ValidationError):
            Settings(**{field: value})

    def test_valid_limits_accepted(self):
        from swarm_provenance_mcp.config import Settings

        cfg = Settings(
            chain_max_fee_gwei=0.1,
            chain_session_budget_eth=0.01,
            chain_max_tx_per_session=5,
            chain_l1_fee_allowance_eth=0,
            max_stamp_duration_hours=24,
        )
        assert cfg.chain_max_tx_per_session == 5
