"""
Spending limits for chain transactions.

Every write goes through ``ChainClient._send_transaction``, which asks the
``SpendGuard`` for explicit fee fields (refusing a fee above the cap) and to
reserve the transaction's worst-case cost against a per-session budget and a
per-session transaction count. A session is the lifetime of the server
process.

Defaults depend on the chain: low on mainnet, looser on known testnets. Any
chain id that is not a known testnet is treated as mainnet.
"""

import threading
from dataclasses import dataclass
from typing import Optional, Tuple

from .exceptions import ChainSpendLimitError

GWEI = 10**9
ETH = 10**18

# Chain ids treated as testnets; everything else gets the mainnet defaults.
TESTNET_CHAIN_IDS = frozenset(
    {
        84532,  # Base Sepolia
        11155111,  # Ethereum Sepolia
        31337,  # Hardhat / Anvil
        1337,  # Ganache / local dev
    }
)


@dataclass(frozen=True)
class SpendDefaults:
    max_fee_gwei: float
    budget_eth: float
    max_txs: int


# Mainnet: a maxFeePerGas cap well above normal Base fees but far below a
# spike or a manipulated suggestion, a few dollars of ETH, and a small number
# of transactions per session.
MAINNET_DEFAULTS = SpendDefaults(max_fee_gwei=0.5, budget_eth=0.002, max_txs=20)
TESTNET_DEFAULTS = SpendDefaults(max_fee_gwei=10.0, budget_eth=0.05, max_txs=500)


def is_testnet(chain_id: int) -> bool:
    return chain_id in TESTNET_CHAIN_IDS


def _gwei(wei: int) -> str:
    return f"{wei / GWEI:.4g} gwei"


def _eth(wei: int) -> str:
    return f"{wei / ETH:.6g} ETH"


class SpendGuard:
    """Fee cap, per-session spend budget and per-session transaction count."""

    def __init__(
        self,
        chain_id: int,
        max_fee_gwei: Optional[float] = None,
        budget_eth: Optional[float] = None,
        max_txs: Optional[int] = None,
    ):
        self.mainnet = not is_testnet(chain_id)
        defaults = MAINNET_DEFAULTS if self.mainnet else TESTNET_DEFAULTS
        self.max_fee_wei = int(
            (defaults.max_fee_gwei if max_fee_gwei is None else max_fee_gwei) * GWEI
        )
        self.budget_wei = int(
            (defaults.budget_eth if budget_eth is None else budget_eth) * ETH
        )
        self.max_txs = defaults.max_txs if max_txs is None else int(max_txs)
        self.spent_wei = 0  # settled costs plus open worst-case reservations
        self.tx_count = 0
        self._lock = threading.Lock()

    # --- Fees ---

    def fee_fields(self, web3) -> Tuple[dict, int]:
        """
        Explicit fee fields for a transaction, within the fee cap.

        Returns:
            ``(fields, max_fee_per_gas)``: the fields to set on the tx and the
            highest price per gas it can pay.

        Raises:
            ChainSpendLimitError: If the current fee is above the cap.
        """
        block = web3.eth.get_block("latest")
        base_fee = block.get("baseFeePerGas") if hasattr(block, "get") else None
        if base_fee is None:  # pre-EIP-1559 chain
            gas_price = int(web3.eth.gas_price)
            if gas_price > self.max_fee_wei:
                raise ChainSpendLimitError(
                    f"Gas price {_gwei(gas_price)} is above the fee cap "
                    f"{_gwei(self.max_fee_wei)} (CHAIN_MAX_FEE_GWEI). Not sent."
                )
            return {"gasPrice": gas_price}, gas_price

        base_fee = int(base_fee)
        tip = int(web3.eth.max_priority_fee)
        if base_fee + tip > self.max_fee_wei:
            raise ChainSpendLimitError(
                f"Current fee (base {_gwei(base_fee)} + priority {_gwei(tip)}) "
                f"is above the fee cap {_gwei(self.max_fee_wei)} "
                "(CHAIN_MAX_FEE_GWEI). Not sent. If fees are spiking, retry "
                "later; a priority fee far above the base fee suggests a "
                "faulty RPC endpoint."
            )
        max_fee = min(2 * base_fee + tip, self.max_fee_wei)
        return {"maxFeePerGas": max_fee, "maxPriorityFeePerGas": tip}, max_fee

    # --- Budget ---

    def reserve(self, worst_case_wei: int) -> int:
        """
        Count one transaction and reserve its worst-case cost.

        Raises:
            ChainSpendLimitError: If the transaction count or the budget
                would be exceeded.
        """
        with self._lock:
            if self.tx_count >= self.max_txs:
                raise ChainSpendLimitError(
                    f"Session transaction limit reached ({self.max_txs}, "
                    "CHAIN_MAX_TX_PER_SESSION). Not sent. Restart the server "
                    "or raise the limit to continue."
                )
            if self.spent_wei + worst_case_wei > self.budget_wei:
                raise ChainSpendLimitError(
                    f"Session spend budget exceeded: this transaction may cost "
                    f"up to {_eth(worst_case_wei)}, "
                    f"{_eth(self.remaining_wei())} of "
                    f"{_eth(self.budget_wei)} left (CHAIN_SESSION_BUDGET_ETH). "
                    "Not sent."
                )
            self.tx_count += 1
            self.spent_wei += worst_case_wei
            return worst_case_wei

    def release(self, reserved_wei: int) -> None:
        """Undo a reservation for a transaction that was never broadcast."""
        with self._lock:
            self.tx_count -= 1
            self.spent_wei -= reserved_wei

    def settle(self, reserved_wei: int, receipt) -> None:
        """Replace a reservation with the receipt's actual cost, if known."""
        try:
            actual = int(receipt["gasUsed"]) * int(receipt["effectiveGasPrice"])
        except (KeyError, TypeError, ValueError):
            return  # keep the worst case
        with self._lock:
            self.spent_wei += min(actual, reserved_wei) - reserved_wei

    def remaining_wei(self) -> int:
        return max(self.budget_wei - self.spent_wei, 0)

    def summary(self) -> dict:
        return {
            "mainnet": self.mainnet,
            "max_fee_gwei": self.max_fee_wei / GWEI,
            "budget_eth": self.budget_wei / ETH,
            "remaining_eth": self.remaining_wei() / ETH,
            "max_txs": self.max_txs,
            "txs_sent": self.tx_count,
        }
