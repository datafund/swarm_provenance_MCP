"""Writes must not be reported as recorded unless the contract recorded them.

A transaction to an address without the DataProvenance contract is mined with
status 1 and emits nothing. These tests cover the start-up code check, the
pre-send code check, and the post-receipt event check.
"""

from unittest.mock import MagicMock

import pytest
from web3 import Web3

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
DEPLOYED_CODE = b"\x60\x80" + b"".join(
    Web3.keccak(text=s)[:4] for s in REQUIRED_SELECTORS
)

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
    topics = [Web3.keccak(text=EVENT_SIGNATURES[event])] + [_topic(v) for v in indexed]
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
    (
        "set_storage_ref",
        lambda c: c.set_storage_ref(H1, H2),
        [_log("StorageRefLinked", H1, H2)],
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

    def test_anchor_storage_ref_event_missing_is_failure(self):
        """DataRegistered alone does not prove the storage ref was linked."""
        w3 = _web3()
        w3.eth.wait_for_transaction_receipt.return_value = _receipt(
            _log("DataRegistered", H1, WALLET)
        )
        client = _client(w3)
        with pytest.raises(ChainTransactionError, match="StorageRefLinked"):
            client.anchor(H1, "t", storage_ref=H2)

    def test_error_message_carries_tx_hash(self):
        w3 = _web3()
        client = _client(w3)
        with pytest.raises(ChainTransactionError) as e:
            client.transform(H1, H2, "d")
        assert TX_HASH.hex() in str(e.value)

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


# --- A local JSON-RPC endpoint for the start-up check ---


class _FakeRpc:
    """Answers eth_chainId and eth_getCode over HTTP on 127.0.0.1."""

    def __init__(self, code: bytes = DEPLOYED_CODE, chain_id: int = 84532):
        import http.server
        import json
        import threading

        rpc = self
        self.code = code
        self.chain_id = chain_id
        self.calls = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                rpc.calls.append(req["method"])
                if req["method"] == "eth_chainId":
                    result = hex(rpc.chain_id)
                elif req["method"] == "eth_getCode":
                    result = "0x" + rpc.code.hex()
                else:
                    result = None
                body = json.dumps(
                    {"jsonrpc": "2.0", "id": req["id"], "result": result}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(
            target=self._server.serve_forever, args=(0.05,), daemon=True
        ).start()

    def close(self):
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def fake_rpc():
    rpc = _FakeRpc()
    yield rpc
    rpc.close()


@pytest.fixture
def hanging_rpc():
    """Accepts TCP connections and never answers."""
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(16)
    yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    sock.close()


def _real_client(monkeypatch, primary: str, fallbacks=()):
    """ChainClient on the base-sepolia preset with its RPCs pointed locally."""
    from swarm_provenance_mcp.chain.client import ChainClient
    from swarm_provenance_mcp.chain.provider import CHAIN_PRESETS

    monkeypatch.setitem(CHAIN_PRESETS["base-sepolia"], "rpc_url", primary)
    monkeypatch.setitem(CHAIN_PRESETS["base-sepolia"], "rpc_fallbacks", list(fallbacks))
    return ChainClient(
        chain="base-sepolia",
        contract_address=CONTRACT,
        private_key="0x" + "11" * 32,
    )


@pytest.fixture
def startup_state(monkeypatch):
    """Isolate the server's module-level write-client state."""
    from swarm_provenance_mcp import server

    monkeypatch.setattr(server, "CHAIN_AVAILABLE", True)
    monkeypatch.setattr(server, "chain_client", None)
    monkeypatch.setattr(server, "_chain_init_error", None)
    monkeypatch.setattr(server, "_unverified_write_client", None)
    return server


class TestStartupCheck:
    def test_no_code_disables_writes(self, monkeypatch, fake_rpc, startup_state):
        server = startup_state
        fake_rpc.code = b""
        client = _real_client(monkeypatch, fake_rpc.url)
        result, reason = server._verify_write_contract(client)
        assert result is None
        assert "No contract code" in reason
        assert CONTRACT in reason
        assert "chain id 84532" in reason

    def test_deployed_contract_keeps_client(self, monkeypatch, fake_rpc, startup_state):
        server = startup_state
        client = _real_client(monkeypatch, fake_rpc.url)
        assert server._verify_write_contract(client) == (client, None)
        assert client._contract_verified

    def test_unreachable_rpc_defers_check(
        self, monkeypatch, hanging_rpc, startup_state
    ):
        server = startup_state
        monkeypatch.setattr(server, "STARTUP_CONTRACT_CHECK_TIMEOUT", 0.4)
        client = _real_client(monkeypatch, hanging_rpc)
        assert server._verify_write_contract(client) == (client, None)
        assert not client._contract_verified  # checked again before sending

    def test_hanging_primary_does_not_stall_startup(
        self, monkeypatch, hanging_rpc, fake_rpc, startup_state
    ):
        """A primary that accepts and never answers used to cost ~60 s."""
        import time

        server = startup_state
        client = _real_client(monkeypatch, hanging_rpc, [fake_rpc.url])
        start = time.monotonic()
        result, reason = server._verify_write_contract(client)
        elapsed = time.monotonic() - start
        assert (result, reason) == (client, None)
        assert client._contract_verified
        assert "eth_getCode" in fake_rpc.calls  # the fallback answered
        # Default budget is 5 s for the hung primary; 30 s timeouts took 60 s.
        assert elapsed < server.STARTUP_CONTRACT_CHECK_TIMEOUT + 3

    def test_fallback_on_another_chain_is_not_asked(
        self, monkeypatch, hanging_rpc, startup_state
    ):
        server = startup_state
        monkeypatch.setattr(server, "STARTUP_CONTRACT_CHECK_TIMEOUT", 0.4)
        other = _FakeRpc(code=b"", chain_id=1)
        try:
            client = _real_client(monkeypatch, hanging_rpc, [other.url])
            assert server._verify_write_contract(client) == (client, None)
            assert "eth_getCode" not in other.calls
        finally:
            other.close()

    async def test_empty_code_at_startup_is_rechecked(
        self, monkeypatch, fake_rpc, startup_state
    ):
        """A node that briefly answers "no code" must not disable writes for good."""
        server = startup_state
        fake_rpc.code = b""
        client = _real_client(monkeypatch, fake_rpc.url)
        server.chain_client, server._chain_init_error = server._verify_write_contract(
            client
        )
        assert server.chain_client is None

        await server._recheck_write_contract()  # still no code
        assert server.chain_client is None
        assert "No contract code" in server._chain_init_error

        fake_rpc.code = DEPLOYED_CODE
        await server._recheck_write_contract()
        assert server.chain_client is client
        assert server._chain_init_error is None


def _held_back_client(server, check_side_effect=None):
    """A wallet client the start-up check held back."""
    client = MagicMock()
    client.chain = "base-sepolia"
    client._provider.chain_id = 84532
    client.check_contract.side_effect = check_side_effect
    server._unverified_write_client = client
    server._chain_init_error = (
        f"No contract code at {CONTRACT}. Chain: base-sepolia, chain id 84532."
    )
    return client


WRITE_TOOLS = [
    ("handle_record_transform", {"original_hash": H1, "new_hash": H2}),
    ("handle_record_merge_transform", {"source_hashes": [H1, H2], "new_hash": H3}),
    ("handle_anchor_hash", {"swarm_hash": H1}),
    ("handle_set_storage_ref", {"data_hash": H1, "storage_ref": H2}),
    ("handle_chain_balance", {}),
]


class TestWritesDisabledReason:
    @pytest.mark.parametrize(
        "handler,args", WRITE_TOOLS, ids=[w[0] for w in WRITE_TOOLS]
    )
    async def test_write_tool_reports_why(self, startup_state, handler, args):
        server = startup_state
        _held_back_client(
            server, ChainConfigurationError(f"No contract code at {CONTRACT}.")
        )
        result = await getattr(server, handler)(args)
        assert result.isError
        text = result.content[0].text
        assert f"No contract code at {CONTRACT}" in text
        assert "chain id 84532" in text
        assert "PROVENANCE_WALLET_KEY" not in text
        assert "requires a funded wallet" not in text

    async def test_write_proceeds_once_contract_found(self, startup_state):
        server = startup_state
        client = _held_back_client(server)  # check_contract now passes
        client.transform.side_effect = RuntimeError("reached transform")
        result = await server.handle_record_transform(
            {"original_hash": H1, "new_hash": H2}
        )
        client.transform.assert_called_once()
        assert server.chain_client is client
        assert server._chain_init_error is None
        assert "No contract code" not in result.content[0].text

    async def test_health_check_reports_why(self, monkeypatch, startup_state):
        server = startup_state
        _held_back_client(
            server, ChainConfigurationError(f"No contract code at {CONTRACT}.")
        )
        monkeypatch.setattr(server.settings, "chain_enabled", True)
        gateway = MagicMock()
        gateway.health_check.return_value = {"status": "unhealthy", "gateway_url": "x"}
        monkeypatch.setattr(server, "gateway_client", gateway)
        text = (await server.handle_health_check({})).content[0].text
        assert "writes disabled" in text
        assert f"No contract code at {CONTRACT}" in text
        assert "no wallet" not in text

    async def test_chain_health_reports_why(self, startup_state):
        server = startup_state
        _held_back_client(
            server, ChainConfigurationError(f"No contract code at {CONTRACT}.")
        )
        provider = MagicMock(
            chain="base-sepolia",
            chain_id=84532,
            contract_address=CONTRACT,
            rpc_url="http://rpc",
        )
        provider.get_block_number.return_value = 1
        from unittest.mock import patch

        with patch(
            "swarm_provenance_mcp.chain.provider.ChainProvider", return_value=provider
        ):
            text = (await server.handle_chain_health({})).content[0].text
        assert "Chain writes are disabled" in text
        assert f"No contract code at {CONTRACT}" in text


class TestHandlerMissingEvent:
    """Tool handlers must report a missing event as a failure with the hash."""

    @pytest.mark.parametrize(
        "handler,args",
        [
            ("handle_record_transform", {"original_hash": H1, "new_hash": H2}),
            ("handle_anchor_hash", {"swarm_hash": H1}),
        ],
    )
    async def test_missing_event_is_error(
        self, monkeypatch, startup_state, handler, args
    ):
        server = startup_state
        monkeypatch.setattr(server, "chain_client", _client(_web3()))
        result = await getattr(server, handler)(args)
        assert result.isError
        text = result.content[0].text
        assert "nothing was recorded" in text
        assert TX_HASH.hex() in text
        assert "recorded on-chain" not in text
        assert "Hash anchored" not in text

    async def test_restrict_original_failure_keeps_hash_and_is_error(
        self, monkeypatch, startup_state
    ):
        server = startup_state
        second_tx = bytes.fromhex("cc" * 32)
        w3 = _web3()
        w3.eth.send_raw_transaction.side_effect = [TX_HASH, second_tx]
        w3.eth.wait_for_transaction_receipt.side_effect = [
            _receipt(_log("DataTransformed", H1, H2)),
            dict(_receipt(), transactionHash=second_tx),  # set_status: no event
        ]
        monkeypatch.setattr(server, "chain_client", _client(w3))
        result = await server.handle_record_transform(
            {"original_hash": H1, "new_hash": H2, "restrict_original": True}
        )
        text = result.content[0].text
        assert "Transformation recorded" in text
        assert "failed to restrict original" in text
        assert second_tx.hex() in text
        assert result.isError


def test_tx_hash_not_repeated_when_already_in_message():
    e = ChainTransactionError("failed: 0xabc", tx_hash="0xabc")
    assert str(e) == "failed: 0xabc"
    assert str(ChainTransactionError("failed", tx_hash="0xabc")) == "failed (tx 0xabc)"
    assert str(ChainTransactionError("failed")) == "failed"
