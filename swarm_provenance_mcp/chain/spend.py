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

import contextvars
import threading
from dataclasses import dataclass
from typing import Optional, Tuple

from .exceptions import ChainSpendLimitError
from .provider import is_transport_error

GWEI = 10**9
ETH = 10**18

# Priority fee used when the node has no fee history to go by. web3's own
# fallback clamps the tip to at least 1 gwei, which is 1000x what Base needs
# and above the mainnet cap, so the tip is computed here instead.
DEFAULT_PRIORITY_FEE_WEI = 1_000_000  # 0.001 gwei

# Gas assumed per transaction when slots are held for a multi-transaction
# operation before the gas of the later transactions can be estimated.
# DataProvenance writes use about 50k-250k gas.
HOLD_GAS_PER_TX = 400_000

# L1 data fee allowance per transaction (OP-stack chains such as Base charge
# it on top of L2 gas, reported as ``l1Fee`` in the receipt). Measured
# 2026-10-21: about 1e10 wei per transaction on Base mainnet, under 1e3 wei
# on Base Sepolia, against 1e12-3e13 wei of L2 gas. The default allowance is
# 100x the mainnet measurement.
DEFAULT_L1_FEE_ALLOWANCE_ETH = 0.000001

# The hold in force for the current operation (see SpendGuard.hold). It is a
# context variable so it follows the handler into asyncio.to_thread workers.
ACTIVE_HOLD: contextvars.ContextVar = contextvars.ContextVar("spend_hold", default=None)

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


class SpendHold:
    """Transaction slots and budget set aside for a multi-transaction
    operation, so it cannot stop half-way on the session limits."""

    def __init__(self, slots: int, per_tx_wei: int):
        self.slots_left = slots
        self.per_tx_wei = per_tx_wei


class SpendGuard:
    """Fee cap, per-session spend budget and per-session transaction count."""

    def __init__(
        self,
        chain_id: int,
        max_fee_gwei: Optional[float] = None,
        budget_eth: Optional[float] = None,
        max_txs: Optional[int] = None,
        l1_fee_allowance_eth: Optional[float] = None,
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
        self.l1_allowance_wei = int(
            (
                DEFAULT_L1_FEE_ALLOWANCE_ETH
                if l1_fee_allowance_eth is None
                else l1_fee_allowance_eth
            )
            * ETH
        )
        self.spent_wei = 0  # settled costs plus open worst-case reservations
        self.tx_count = 0
        self._lock = threading.Lock()

    # --- Fees ---

    @staticmethod
    def priority_fee(web3) -> int:
        """
        Median priority fee paid over the last few blocks, or
        DEFAULT_PRIORITY_FEE_WEI when the node reports none.
        """
        try:
            history = web3.eth.fee_history(5, "latest", [50])
            rewards = sorted(int(r[0]) for r in (history["reward"] or []) if r)
        except Exception as e:
            if is_transport_error(e):
                raise
            rewards = []
        if not rewards:
            return DEFAULT_PRIORITY_FEE_WEI
        # A zero tip (empty blocks report zero rewards) may never be
        # included, so never go below the default.
        return max(rewards[len(rewards) // 2], DEFAULT_PRIORITY_FEE_WEI)

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
                    f"{_gwei(self.max_fee_wei)} (CHAIN_MAX_FEE_GWEI). Not sent, "
                    "and not retried automatically: wait for fees to fall or "
                    "raise the cap."
                )
            return {"gasPrice": gas_price}, gas_price

        base_fee = int(base_fee)
        tip = self.priority_fee(web3)
        if base_fee + tip > self.max_fee_wei:
            msg = (
                f"Current fee (base {_gwei(base_fee)} + priority {_gwei(tip)}) "
                f"is above the fee cap {_gwei(self.max_fee_wei)} "
                "(CHAIN_MAX_FEE_GWEI). Not sent, and not retried automatically: "
                "wait for fees to fall or raise the cap."
            )
            if tip > 10 * base_fee:
                msg += (
                    " The priority fee reported by the RPC endpoint is far "
                    "above the base fee; check the endpoint."
                )
            raise ChainSpendLimitError(msg)
        max_fee = min(2 * base_fee + tip, self.max_fee_wei)
        return {"maxFeePerGas": max_fee, "maxPriorityFeePerGas": tip}, max_fee

    def worst_case(self, gas: int, max_fee_wei: int) -> int:
        """Most a transaction can cost: L2 gas at the max fee plus the L1
        data fee allowance."""
        return int(gas) * int(max_fee_wei) + self.l1_allowance_wei

    # --- Budget ---

    def _check(self, n: int, cost_wei: int) -> None:
        if self.tx_count + n > self.max_txs:
            left = max(self.max_txs - self.tx_count, 0)
            raise ChainSpendLimitError(
                f"Session transaction limit reached: {n} transaction(s) needed, "
                f"{left} of {self.max_txs} left (CHAIN_MAX_TX_PER_SESSION). "
                "Not sent. Restart the server or raise the limit to continue."
            )
        if self.spent_wei + cost_wei > self.budget_wei:
            raise ChainSpendLimitError(
                f"Session spend budget exceeded: {n} transaction(s) may cost "
                f"up to {_eth(cost_wei)}, "
                f"{_eth(self.remaining_wei())} of "
                f"{_eth(self.budget_wei)} left (CHAIN_SESSION_BUDGET_ETH). "
                "Not sent."
            )

    def hold(self, n: int, per_tx_wei: int) -> SpendHold:
        """
        Set aside ``n`` transaction slots and their worst-case cost before
        the first of them is sent.

        Raises:
            ChainSpendLimitError: If they do not all fit.
        """
        with self._lock:
            self._check(n, n * per_tx_wei)
            self.tx_count += n
            self.spent_wei += n * per_tx_wei
            return SpendHold(n, per_tx_wei)

    def end_hold(self, hold: SpendHold) -> None:
        """Give back the slots and budget of a hold that were not used."""
        with self._lock:
            self.tx_count -= hold.slots_left
            self.spent_wei -= hold.slots_left * hold.per_tx_wei
            hold.slots_left = 0

    def reserve(self, worst_case_wei: int) -> int:
        """
        Count one transaction and reserve its worst-case cost, from the
        active hold when there is one.

        Raises:
            ChainSpendLimitError: If the transaction count or the budget
                would be exceeded.
        """
        hold = ACTIVE_HOLD.get()
        with self._lock:
            if hold is not None and hold.slots_left > 0:
                extra = max(worst_case_wei - hold.per_tx_wei, 0)
                if extra and self.spent_wei + extra > self.budget_wei:
                    self._check(0, extra)  # raises the budget message
                hold.slots_left -= 1
                self.spent_wei += extra
                return hold.per_tx_wei + extra
            self._check(1, worst_case_wei)
            self.tx_count += 1
            self.spent_wei += worst_case_wei
            return worst_case_wei

    def release(self, reserved_wei: int) -> None:
        """Undo a reservation for a transaction that never reached the
        network."""
        with self._lock:
            self.tx_count -= 1
            self.spent_wei -= reserved_wei

    def settle(self, reserved_wei: int, receipt) -> None:
        """Replace a reservation with the receipt's actual cost (L2 gas plus
        the L1 data fee when the receipt reports one), if known."""
        try:
            actual = int(receipt["gasUsed"]) * int(receipt["effectiveGasPrice"])
        except (KeyError, TypeError, ValueError):
            return  # keep the worst case
        l1_fee = receipt.get("l1Fee") if hasattr(receipt, "get") else None
        if l1_fee is not None:
            try:
                actual += int(l1_fee, 16) if isinstance(l1_fee, str) else int(l1_fee)
            except (TypeError, ValueError):
                return  # unreadable: keep the worst case
        with self._lock:
            # The actual cost, even above the reservation (an L1 fee spike
            # beyond the allowance must still be counted).
            self.spent_wei += actual - reserved_wei

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
