"""
Chain wallet for signing transactions.

Manages private key loading, address derivation, transaction signing,
and ETH balance queries for on-chain operations.

Dependencies (web3, eth-account) are included in the default install.
"""

import os
from typing import Optional

from .exceptions import ChainConfigurationError

# Lazy eth-account import
_Account = None


def _import_eth_account():
    """Lazily import eth-account to avoid import errors when not installed."""
    global _Account
    if _Account is None:
        try:
            from eth_account import Account

            _Account = Account
        except ImportError as e:
            raise ChainConfigurationError(
                "eth-account not available. " "Reinstall with: pip install -e ."
            ) from e
    return _Account


class ChainWallet:
    """Wallet for signing blockchain transactions.

    Loads a private key from an environment variable or direct parameter,
    derives the corresponding address, and provides transaction signing.
    """

    def __init__(
        self,
        private_key: Optional[str] = None,
        private_key_env: str = "PROVENANCE_WALLET_KEY",
    ):
        """
        Initialize the wallet.

        Args:
            private_key: Hex-encoded private key. If None, reads from env var.
            private_key_env: Environment variable name containing the private key.

        Raises:
            ChainConfigurationError: If no private key found or key is invalid.
        """
        Account = _import_eth_account()

        self._private_key = private_key or os.getenv(private_key_env)
        if not self._private_key:
            raise ChainConfigurationError(
                f"No wallet private key configured. "
                f"Set {private_key_env} environment variable or pass private_key parameter."
            )

        # Normalize 0x prefix
        if not self._private_key.startswith("0x"):
            self._private_key = "0x" + self._private_key

        # Validate and derive address
        try:
            self._account = Account.from_key(self._private_key)
            self.address = self._account.address
        except Exception as e:
            raise ChainConfigurationError(f"Invalid private key: {e}") from e

    def sign_transaction(self, tx: dict) -> bytes:
        """
        Sign a transaction dictionary.

        Args:
            tx: Transaction dict with fields like to, value, data, gas, etc.

        Returns:
            Raw signed transaction bytes ready for broadcast.

        Raises:
            ChainConfigurationError: If signing fails.
        """
        try:
            signed = self._account.sign_transaction(tx)
            return signed.raw_transaction
        except Exception as e:
            raise ChainConfigurationError(f"Failed to sign transaction: {e}") from e

    def get_balance(self, web3) -> int:
        """
        Get ETH balance of this wallet.

        Args:
            web3: Web3 instance connected to the target chain.

        Returns:
            Balance in wei.
        """
        return web3.eth.get_balance(self.address)

    def get_balance_eth(self, web3) -> str:
        """
        Get ETH balance formatted as a string.

        Args:
            web3: Web3 instance connected to the target chain.

        Returns:
            Balance formatted as ETH string (e.g., "0.1234").
        """
        from web3 import Web3 as Web3Class

        balance_wei = self.get_balance(web3)
        return str(Web3Class.from_wei(balance_wei, "ether"))
