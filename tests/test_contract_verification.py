"""Writes must not be reported as recorded unless the contract recorded them.

A transaction to an address without the DataProvenance contract is mined with
status 1 and emits nothing. These tests cover the start-up code check, the
pre-send code check, and the post-receipt event check.
"""

from unittest.mock import MagicMock

import pytest
from eth_utils import keccak

from swarm_provenance_mcp.chain.contract import (
    REQUIRED_SELECTORS,
    DataProvenanceContract,
)
from swarm_provenance_mcp.chain.exceptions import (
    ChainConfigurationError,
    ChainTransactionError,
    DataNotRegisteredError,
)

CONTRACT = "0x3945aDfd5Df9ab2F5cB4Ca0eb3D4384CC3650322"
OTHER = "0x000000000000000000000000000000000000dEaD"
WALLET = "0x742d35Cc6634C0532925a3b844Bc9e7595f8fE00"
NEW_OWNER = "0x1111111111111111111111111111111111111111"
H1 = "a1" * 32
H2 = "b2" * 32
H3 = "c3" * 32
TX_HASH = bytes.fromhex("bb" * 32)
DEPLOYED_CODE = b"\x60\x80" + b"".join(keccak(text=s)[:4] for s in REQUIRED_SELECTORS)

EVENT_SIGNATURES = {
    "DataRegistered": "DataRegistered(bytes32,address,string)",
    "StorageRefLinked": "StorageRefLinked(bytes32,bytes32)",
    "DataTransformed": "DataTransformed(bytes32,bytes32,string)",
    "DataMerged": "DataMerged(bytes32,bytes32[],string)",
    "DataAccessed": "DataAccessed(bytes32,address)",
    "DataStatusChanged": "DataStatusChanged(bytes32,uint8,uint8)",
    "DataOwnershipTransferred": "DataOwnershipTransferred(bytes32,address,address)",
    "DelegateAuthorized": "DelegateAuthorized(address,address,bool)",
}


def _topic(value: str) -> bytes:
    raw = bytes.fromhex(value[2:] if value.startswith("0x") else value)
    return raw.rjust(32, b"\x00")


def _log(event: str, *indexed: str, address: str = CONTRACT) -> dict:
    topics = [keccak(text=EVENT_SIGNATURES[event])] + [_topic(v) for v in indexed]
    return {"address": address, "topics": topics, "data": b""}


def _receipt(*logs) -> dict:
    return {
        "status": 1,
        "transactionHash": TX_HASH,
        "blockNumber": 100,
        "gasUsed": 50_000,
        "logs": list(logs),
    }


def _web3(code: bytes = DEPLOYED_CODE):
    w3 = MagicMock()
    w3.to_checksum_address = lambda x: x
    w3.eth.get_code.return_value = code
    w3.eth.get_transaction_count.return_value = 0
    w3.eth.estimate_gas.return_value = 100_000
    w3.eth.send_raw_transaction.return_value = TX_HASH
    w3.eth.wait_for_transaction_receipt.return_value = _receipt()
    return w3


def _client(w3):
    """ChainClient on a real contract wrapper (real ABI) and a mocked RPC."""
    from swarm_provenance_mcp.chain.client import ChainClient

    contract = DataProvenanceContract(web3=w3, contract_address=CONTRACT)
    client = ChainClient.__new__(ChainClient)
    client._provider = MagicMock()
    client._provider.web3 = w3
    client._provider.chain_id = 84532
    client._provider.contract_address = CONTRACT
    client._provider._rpc_urls = ["http://rpc"]
    client._provider.deploy_block = None
    client._provider.get_explorer_tx_url.return_value = None
    client._wallet = MagicMock()
    client._wallet.address = WALLET
    client._wallet.sign_transaction.return_value = b"\x00"
    client._contract = contract
    client._contract_web3 = w3
    client._gas_limit = None
    client._gas_limit_multiplier = 1.2
    # Pre-checks read state; these tests are about the receipt.
    client.get = MagicMock(side_effect=DataNotRegisteredError("not registered"))
    contract.supports_transformation_links = MagicMock(return_value=False)
    return client


# (operation, call, logs that prove it was recorded)
WRITES = [
    (
        "anchor",
        lambda c: c.anchor(H1, "t"),
        [_log("DataRegistered", H1, WALLET)],
    ),
    (
        "anchor_with_storage_ref",
        lambda c: c.anchor(H1, "t", storage_ref=H2),
        [_log("DataRegistered", H1, WALLET), _log("StorageRefLinked", H1, H2)],
    ),
    (
        "anchor_for",
        lambda c: c.anchor_for(H1, NEW_OWNER, "t"),
        [_log("DataRegistered", H1, NEW_OWNER)],
    ),
    (
        "batch_anchor",
        lambda c: c.batch_anchor([H1, H2], ["t", "t"]),
        [_log("DataRegistered", H1, WALLET), _log("DataRegistered", H2, WALLET)],
    ),
    (
        "transform",
        lambda c: c.transform(H1, H2, "d"),
        [_log("DataTransformed", H1, H2)],
    ),
    (
        "merge_transform",
        lambda c: c.merge_transform([H1, H2], H3, "d"),
        [_log("DataMerged", H3)],
    ),
    (
        "access",
        lambda c: c.access(H1),
        [_log("DataAccessed", H1, WALLET)],
    ),
    (
        "batch_access",
        lambda c: c.batch_access([H1, H2]),
        [_log("DataAccessed", H1, WALLET), _log("DataAccessed", H2, WALLET)],
    ),
    (
        "set_status",
        lambda c: c.set_status(H1, 1),
        [_log("DataStatusChanged", H1)],
    ),
    (
        "batch_set_status",
        lambda c: c.batch_set_status([H1, H2], [1, 1]),
        [_log("DataStatusChanged", H1), _log("DataStatusChanged", H2)],
    ),
    (
        "transfer_ownership",
        lambda c: c.transfer_ownership(H1, NEW_OWNER),
        [_log("DataOwnershipTransferred", H1, WALLET, NEW_OWNER)],
    ),
    (
        "set_delegate",
        lambda c: c.set_delegate(NEW_OWNER, True),
        [_log("DelegateAuthorized", WALLET, NEW_OWNER)],
    ),
]
WRITE_IDS = [w[0] for w in WRITES]


def _prepare(client, name):
    """Per-operation pre-check stubs (state the operation expects)."""
    if name == "set_storage_ref":
        record = MagicMock(storage_ref=None, data_type="t")
        client.get = MagicMock(return_value=record)


class TestCheckDeployed:
    def test_no_code_refused(self):
        contract = DataProvenanceContract(web3=_web3(b""), contract_address=CONTRACT)
        with pytest.raises(ChainConfigurationError, match="No contract code"):
            contract.check_deployed()

    def test_unrelated_contract_refused(self):
        contract = DataProvenanceContract(
            web3=_web3(b"\x60\x80\x60\x40\x52"), contract_address=CONTRACT
        )
        with pytest.raises(ChainConfigurationError, match="not a DataProvenance"):
            contract.check_deployed()

    def test_data_provenance_code_accepted(self):
        contract = DataProvenanceContract(web3=_web3(), contract_address=CONTRACT)
        contract.check_deployed()  # does not raise


class TestWriteRequiresContractCode:
    def test_no_code_sends_nothing(self):
        w3 = _web3(b"")
        client = _client(w3)
        with pytest.raises(ChainConfigurationError):
            client.transform(H1, H2, "d")
        w3.eth.send_raw_transaction.assert_not_called()

    def test_code_checked_once(self):
        w3 = _web3()
        w3.eth.wait_for_transaction_receipt.return_value = _receipt(
            _log("DataTransformed", H1, H2)
        )
        client = _client(w3)
        client.transform(H1, H2, "d")
        client.transform(H1, H2, "d")
        assert w3.eth.get_code.call_count == 1


class TestWriteRequiresEvent:
    @pytest.mark.parametrize("name,call,logs", WRITES, ids=WRITE_IDS)
    def test_status_1_without_logs_is_failure(self, name, call, logs):
        w3 = _web3()
        client = _client(w3)
        _prepare(client, name)
        with pytest.raises(ChainTransactionError, match="nothing was recorded") as e:
            call(client)
        assert e.value.broadcast is True
        assert e.value.tx_hash == TX_HASH.hex()

    @pytest.mark.parametrize("name,call,logs", WRITES, ids=WRITE_IDS)
    def test_success_with_expected_events(self, name, call, logs):
        w3 = _web3()
        w3.eth.wait_for_transaction_receipt.return_value = _receipt(*logs)
        client = _client(w3)
        _prepare(client, name)
        result = call(client)
        assert result.tx_hash == TX_HASH.hex()

    @pytest.mark.parametrize("name,call,logs", WRITES, ids=WRITE_IDS)
    def test_event_from_another_address_is_failure(self, name, call, logs):
        w3 = _web3()
        foreign = [dict(log, address=OTHER) for log in logs]
        w3.eth.wait_for_transaction_receipt.return_value = _receipt(*foreign)
        client = _client(w3)
        _prepare(client, name)
        with pytest.raises(ChainTransactionError, match="nothing was recorded"):
            call(client)

    def test_event_for_another_hash_is_failure(self):
        w3 = _web3()
        w3.eth.wait_for_transaction_receipt.return_value = _receipt(
            _log("DataTransformed", H1, H3)
        )
        client = _client(w3)
        with pytest.raises(ChainTransactionError, match="DataTransformed"):
            client.transform(H1, H2, "d")

    def test_batch_missing_one_event_is_failure(self):
        w3 = _web3()
        w3.eth.wait_for_transaction_receipt.return_value = _receipt(
            _log("DataRegistered", H1, WALLET)
        )
        client = _client(w3)
        with pytest.raises(ChainTransactionError, match="DataRegistered"):
            client.batch_anchor([H1, H2], ["t", "t"])

    def test_set_storage_ref_without_event_is_failure(self):
        w3 = _web3()
        client = _client(w3)
        _prepare(client, "set_storage_ref")
        with pytest.raises(ChainTransactionError, match="StorageRefLinked"):
            client.set_storage_ref(H1, H2)

    def test_set_storage_ref_with_event_succeeds(self):
        w3 = _web3()
        w3.eth.wait_for_transaction_receipt.return_value = _receipt(
            _log("StorageRefLinked", H1, H2)
        )
        client = _client(w3)
        _prepare(client, "set_storage_ref")
        assert client.set_storage_ref(H1, H2).storage_ref == H2

    def test_hex_string_topics_accepted(self):
        """Some RPC clients return topics as 0x-hex strings."""
        contract = DataProvenanceContract(web3=_web3(), contract_address=CONTRACT)
        log = _log("DataMerged", H3)
        log["topics"] = ["0x" + t.hex() for t in log["topics"]]
        assert contract.receipt_has_event(_receipt(log), "DataMerged", newDataHash=H3)


class TestStartupCheck:
    def test_no_code_disables_writes(self):
        from swarm_provenance_mcp import server

        client = _client(_web3(b""))
        result, reason = server._verify_write_contract(client)
        assert result is None
        assert "No contract code" in reason

    def test_deployed_contract_keeps_client(self):
        from swarm_provenance_mcp import server

        client = _client(_web3())
        assert server._verify_write_contract(client) == (client, None)

    def test_unreachable_rpc_defers_check(self):
        from swarm_provenance_mcp import server

        w3 = _web3()
        w3.eth.get_code.side_effect = ConnectionError("rpc down")
        client = _client(w3)
        assert server._verify_write_contract(client) == (client, None)

    async def test_write_tool_reports_why(self, monkeypatch):
        from swarm_provenance_mcp import server

        monkeypatch.setattr(server, "CHAIN_AVAILABLE", True)
        monkeypatch.setattr(server, "chain_client", None)
        monkeypatch.setattr(server, "_chain_init_error", "No contract code at 0xabc")
        result = await server.handle_record_transform(
            {"original_hash": H1, "new_hash": H2, "description": "d"}
        )
        assert result.isError
        assert "No contract code at 0xabc" in result.content[0].text
