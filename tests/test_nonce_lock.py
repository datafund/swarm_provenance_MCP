"""Parallel chain writes from one wallet get distinct nonces (#159).

The nonce is read with the 'pending' tag, and one lock per (chain id,
wallet) covers nonce, gas, fees, signing and broadcast. These tests use
real threads. Every wait has a timeout and every join has a limit, so a
regression fails instead of hanging the suite.
"""

import asyncio
import threading
from unittest.mock import MagicMock

import pytest
from requests import Response
from requests.exceptions import HTTPError, Timeout

from swarm_provenance_mcp.chain.exceptions import (
    ChainConnectionError,
    ChainSpendLimitError,
    ChainTransactionError,
    DataNotRegisteredError,
)
from swarm_provenance_mcp.chain.spend import SpendGuard

BASE_SEPOLIA = 84532
WALLET = "0x742d35Cc6634C0532925a3b844Bc9e7595f8fE00"
OTHER_WALLET = "0x1111111111111111111111111111111111111111"
CONTRACT = "0x3945aDfd5Df9ab2F5cB4Ca0eb3D4384CC3650322"
H1 = "a1" * 32
H2 = "b2" * 32
H3 = "c3" * 32

# How long the race barrier waits for a second thread. Without the lock
# both threads meet here after reading the same nonce; with it, the first
# thread waits this long alone and then goes on.
RACE_WINDOW = 0.5
JOIN_TIMEOUT = 15


class FakeNode:
    """A node that tracks the nonce of sent transactions.

    'latest' counts only mined transactions (none are mined while a
    receipt is pending); 'pending' also counts the ones in the mempool.
    """

    def __init__(self, race=False):
        self.sent = []  # nonces, in broadcast order
        self.mined = 0
        self.tags = []
        self._guard = threading.Lock()
        self.barrier = threading.Barrier(2, timeout=RACE_WINDOW) if race else None

    def get_transaction_count(self, address, block_identifier="latest"):
        with self._guard:
            self.tags.append(block_identifier)
            if block_identifier == "pending":
                count = len(self.sent)
            else:
                count = self.mined
        if self.barrier is not None:
            try:
                self.barrier.wait()
            except threading.BrokenBarrierError:
                pass
        return count

    def send_raw_transaction(self, raw):
        _, nonce = raw
        with self._guard:
            self.sent.append(nonce)
        return bytes([nonce % 256]) * 32

    def receipt(self, tx_hash, timeout=120):
        return {
            "status": 1,
            "transactionHash": tx_hash,
            "blockNumber": 1,
            "gasUsed": 90_000,
            "effectiveGasPrice": 6_000_000,
        }


def _web3(node):
    w3 = MagicMock()
    w3.eth.get_block.return_value = {"baseFeePerGas": 5_000_000}
    w3.eth.fee_history.return_value = {"reward": [[1_000_000]] * 5}
    w3.eth.estimate_gas.return_value = 100_000
    w3.eth.get_transaction_count.side_effect = node.get_transaction_count
    w3.eth.send_raw_transaction.side_effect = node.send_raw_transaction
    w3.eth.wait_for_transaction_receipt.side_effect = node.receipt
    return w3


def _tx(**kwargs):
    return {"from": WALLET, "to": CONTRACT, "data": "0x"}


def _client(w3, wallet=WALLET, rpc_urls=("http://rpc",)):
    """A ChainClient wired to ``w3``, bypassing __init__."""
    from swarm_provenance_mcp.chain.client import ChainClient

    client = ChainClient.__new__(ChainClient)
    client._provider = MagicMock()
    client._provider.web3 = w3
    client._provider.chain_id = BASE_SEPOLIA
    client._provider._rpc_urls = list(rpc_urls)
    client._provider.get_explorer_tx_url.return_value = None
    client._wallet = MagicMock()
    client._wallet.address = wallet
    client._wallet.sign_transaction.side_effect = lambda tx: ("raw", tx["nonce"])
    client._contract = MagicMock()
    client._contract.receipt_has_event.return_value = True
    for build in (
        "build_register_data_tx",
        "build_batch_register_data_tx",
        "build_set_data_status_tx",
        "build_batch_set_data_status_tx",
    ):
        getattr(client._contract, build).side_effect = _tx
    client._contract_web3 = w3
    client._contract_verified = True
    client._gas_limit = None
    client._gas_limit_multiplier = 1.2
    client._spend = SpendGuard(BASE_SEPOLIA, l1_fee_allowance_eth=0)
    client.get = MagicMock(side_effect=DataNotRegisteredError("not registered"))
    return client


def _run_parallel(*calls):
    """Run each call in its own thread; fail if any is still running at the
    join limit. Returns the results and errors in call order."""
    results = [None] * len(calls)
    errors = [None] * len(calls)

    def runner(i, fn):
        try:
            results[i] = fn()
        except BaseException as e:  # reported to the test thread
            errors[i] = e

    threads = [
        threading.Thread(target=runner, args=(i, fn), daemon=True)
        for i, fn in enumerate(calls)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(JOIN_TIMEOUT)
    assert not any(t.is_alive() for t in threads), "a chain write hung"
    return results, errors


def _run_in_thread(fn):
    """Run one call in a thread with a join limit and return its result."""
    results, errors = _run_parallel(fn)
    if errors[0] is not None:
        raise errors[0]
    return results[0]


class TestParallelWritesGetDistinctNonces:
    def test_two_threads(self):
        node = FakeNode(race=True)
        client = _client(_web3(node))

        _, errors = _run_parallel(
            lambda: client.anchor(swarm_hash=H1, data_type="t"),
            lambda: client.set_status(swarm_hash=H2, status=1),
        )

        assert errors == [None, None]
        assert sorted(node.sent) == [0, 1]

    def test_asyncio_to_thread_like_the_server(self):
        """server.py runs chain calls with asyncio.to_thread, so the lock
        must be a thread lock."""
        node = FakeNode(race=True)
        client = _client(_web3(node))

        async def main():
            return await asyncio.wait_for(
                asyncio.gather(
                    asyncio.to_thread(client.anchor, swarm_hash=H1, data_type="t"),
                    asyncio.to_thread(client.set_status, swarm_hash=H2, status=1),
                ),
                timeout=JOIN_TIMEOUT,
            )

        asyncio.run(main())
        assert sorted(node.sent) == [0, 1]

    def test_batch_and_single_writes(self):
        node = FakeNode()
        client = _client(_web3(node))

        _, errors = _run_parallel(
            lambda: client.batch_anchor(swarm_hashes=[H1, H2], data_types=["t", "t"]),
            lambda: client.batch_set_status(swarm_hashes=[H1, H2], statuses=[1, 1]),
            lambda: client.set_status(swarm_hash=H3, status=1),
            lambda: client.anchor(swarm_hash=H3, data_type="t"),
        )

        assert errors == [None] * 4
        assert sorted(node.sent) == [0, 1, 2, 3]

    def test_two_clients_with_one_wallet_share_the_lock(self):
        node = FakeNode(race=True)
        w3 = _web3(node)
        first, second = _client(w3), _client(w3)

        _, errors = _run_parallel(
            lambda: first.set_status(swarm_hash=H1, status=1),
            lambda: second.set_status(swarm_hash=H2, status=1),
        )

        assert errors == [None, None]
        assert sorted(node.sent) == [0, 1]

    def test_different_wallets_do_not_wait_for_each_other(self):
        """The lock is per wallet: two wallets meet at the barrier inside
        the locked step, so it is not broken by a timeout."""
        node = FakeNode(race=True)
        w3 = _web3(node)
        first, second = _client(w3), _client(w3, wallet=OTHER_WALLET)

        _, errors = _run_parallel(
            lambda: first.set_status(swarm_hash=H1, status=1),
            lambda: second.set_status(swarm_hash=H2, status=1),
        )

        assert errors == [None, None]
        assert not node.barrier.broken


def test_nonce_uses_pending_tag():
    node = FakeNode()
    client = _client(_web3(node))

    client.set_status(swarm_hash=H1, status=1)
    client.set_status(swarm_hash=H2, status=1)

    assert node.tags == ["pending", "pending"]
    assert node.sent == [0, 1]


def test_receipt_wait_is_outside_the_lock():
    """A second write is signed and sent while the first waits for its
    receipt."""
    node = FakeNode()
    w3 = _web3(node)
    client = _client(w3)
    first_waiting = threading.Event()
    second_sent = threading.Event()
    seen_during_wait = []

    def send(raw):
        tx_hash = node.send_raw_transaction(raw)
        if len(node.sent) == 2:
            second_sent.set()
        return tx_hash

    def receipt(tx_hash, timeout=120):
        if not first_waiting.is_set():
            first_waiting.set()
            seen_during_wait.append(second_sent.wait(5))
        return node.receipt(tx_hash)

    w3.eth.send_raw_transaction.side_effect = send
    w3.eth.wait_for_transaction_receipt.side_effect = receipt

    def second():
        assert first_waiting.wait(5), "first write never reached the receipt wait"
        return client.set_status(swarm_hash=H2, status=1)

    _, errors = _run_parallel(
        lambda: client.set_status(swarm_hash=H1, status=1), second
    )

    assert errors == [None, None]
    assert seen_during_wait == [True]
    assert node.sent == [0, 1]


def _http_error(status):
    response = Response()
    response.status_code = status
    return HTTPError(f"{status} Server Error", response=response)


def _fail_once(mock, error, then):
    effects = iter([error])

    def side_effect(*args, **kwargs):
        for e in effects:
            raise e
        return then(*args, **kwargs)

    mock.side_effect = side_effect


def _fail_sign(client, w3):
    _fail_once(
        client._wallet.sign_transaction,
        RuntimeError("signer down"),
        client._wallet.sign_transaction.side_effect,
    )


def _fail_send_rejected(client, w3):
    _fail_once(
        w3.eth.send_raw_transaction,
        ValueError("nonce too low"),
        w3.eth.send_raw_transaction.side_effect,
    )


def _fail_send_timeout(client, w3):
    # Ambiguous: the transaction may have reached the mempool.
    _fail_once(
        w3.eth.send_raw_transaction,
        Timeout("read timed out"),
        w3.eth.send_raw_transaction.side_effect,
    )


def _fail_estimate(client, w3):
    _fail_once(
        w3.eth.estimate_gas,
        ValueError("execution reverted"),
        lambda *a, **k: 100_000,
    )


def _fail_spend_limit(client, w3):
    client._spend.reserve = MagicMock(wraps=client._spend.reserve)
    _fail_once(
        client._spend.reserve,
        ChainSpendLimitError("over budget"),
        client._spend.reserve._mock_wraps,
    )


def _fail_nonce_read(client, w3):
    _fail_once(
        w3.eth.get_transaction_count,
        _http_error(503),
        w3.eth.get_transaction_count.side_effect,
    )


@pytest.mark.parametrize(
    "break_first, expected",
    [
        (_fail_sign, ChainTransactionError),
        (_fail_send_rejected, ChainTransactionError),
        (_fail_send_timeout, ChainTransactionError),
        (_fail_estimate, ChainTransactionError),
        (_fail_spend_limit, ChainSpendLimitError),
        (_fail_nonce_read, ChainConnectionError),
    ],
    ids=["sign", "send-rejected", "send-timeout", "estimate", "spend", "nonce"],
)
def test_lock_is_released_when_a_write_fails(break_first, expected):
    node = FakeNode()
    w3 = _web3(node)
    client = _client(w3)
    break_first(client, w3)

    with pytest.raises(expected):
        _run_in_thread(lambda: client.set_status(swarm_hash=H1, status=1))
    result = _run_in_thread(lambda: client.set_status(swarm_hash=H2, status=1))

    assert result.swarm_hash == H2
    assert node.sent == [0]


def test_lock_is_released_across_rpc_failover():
    """A 503 on the nonce read fails over to the next RPC, which takes the
    lock again and sends. A following write still goes through."""
    node = FakeNode()
    primary, fallback = _web3(node), _web3(node)
    primary.eth.get_transaction_count.side_effect = _http_error(503)
    client = _client(primary, rpc_urls=("http://primary", "http://fallback"))

    def fall_back(failed_web3):
        client._provider.web3 = fallback
        client._contract_web3 = fallback  # keep the mock contract bound
        return True

    client._provider._fallback_from.side_effect = fall_back

    _run_in_thread(lambda: client.set_status(swarm_hash=H1, status=1))
    _run_in_thread(lambda: client.set_status(swarm_hash=H2, status=1))

    primary.eth.send_raw_transaction.assert_not_called()
    assert node.sent == [0, 1]
