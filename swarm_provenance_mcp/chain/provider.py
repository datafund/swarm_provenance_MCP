"""
Chain provider for connecting to EVM-compatible networks.

Manages Web3 connections to Base Sepolia (testnet) and Base (mainnet)
for interacting with the DataProvenance smart contract.

Requires optional dependencies: pip install -e .[blockchain]
"""

from typing import Optional

from .exceptions import ChainConfigurationError, ChainConnectionError

# Lazy web3 import
_Web3 = None


def _import_web3():
    """Lazily import web3 to avoid import errors when not installed."""
    global _Web3
    if _Web3 is None:
        try:
            from web3 import Web3

            _Web3 = Web3
        except ImportError as e:
            raise ChainConfigurationError(
                "Blockchain dependencies not installed. "
                "Run: pip install -e .[blockchain]"
            ) from e
    return _Web3


# Network presets for supported chains
CHAIN_PRESETS = {
    "base-sepolia": {
        "chain_id": 84532,
        "rpc_url": "https://sepolia.base.org",
        "explorer_url": "https://sepolia.basescan.org",
        "contract_address": "0x9a3c6F47B69211F05891CCb7aD33596290b9fE64",
    },
    "base": {
        "chain_id": 8453,
        "rpc_url": "https://mainnet.base.org",
        "explorer_url": "https://basescan.org",
        "contract_address": None,  # Not yet deployed
    },
}


class ChainProvider:
    """Provider for Web3 connections to supported EVM chains.

    Handles RPC connection management, health checks, and block explorer
    URL generation for Base Sepolia and Base mainnet.
    """

    SUPPORTED_CHAINS = list(CHAIN_PRESETS.keys())

    def __init__(
        self,
        chain: str = "base-sepolia",
        rpc_url: Optional[str] = None,
        contract_address: Optional[str] = None,
        explorer_url: Optional[str] = None,
    ):
        """
        Initialize the chain provider.

        Args:
            chain: Chain name ('base-sepolia' or 'base').
            rpc_url: Custom RPC URL. If None, uses preset for chain.
            contract_address: Custom contract address. If None, uses preset.
            explorer_url: Custom block explorer URL. If None, uses preset.

        Raises:
            ChainConfigurationError: If chain is unsupported or web3 not installed.
        """
        Web3 = _import_web3()

        if chain not in CHAIN_PRESETS:
            raise ChainConfigurationError(
                f"Unsupported chain: {chain}. Supported: {self.SUPPORTED_CHAINS}"
            )

        preset = CHAIN_PRESETS[chain]
        self.chain = chain
        self.rpc_url = rpc_url or preset["rpc_url"]
        self.explorer_url = explorer_url or preset["explorer_url"]
        self.contract_address = contract_address or preset["contract_address"]
        self._custom_rpc = rpc_url is not None

        if not self.contract_address:
            raise ChainConfigurationError(
                f"No contract address configured for chain '{chain}'. "
                "Provide one via contract_address parameter or CHAIN_CONTRACT env var."
            )

        # Initialize Web3 connection
        self._web3 = Web3(Web3.HTTPProvider(self.rpc_url))

        # When a custom RPC is provided, auto-detect chain ID from the node
        # (e.g. local Hardhat uses chain ID 31337, not the preset chain ID)
        if self._custom_rpc:
            try:
                self.chain_id = self._web3.eth.chain_id
            except Exception:
                self.chain_id = preset["chain_id"]
        else:
            self.chain_id = preset["chain_id"]

    @property
    def web3(self):
        """Get the Web3 instance."""
        return self._web3

    def health_check(self) -> bool:
        """
        Check if the RPC endpoint is reachable and responding.

        Returns:
            True if connected and chain ID matches.

        Raises:
            ChainConnectionError: If connection fails.
        """
        try:
            if not self._web3.is_connected():
                raise ChainConnectionError(
                    f"Cannot connect to RPC endpoint: {self.rpc_url}",
                    rpc_url=self.rpc_url,
                )
            actual_chain_id = self._web3.eth.chain_id
            if actual_chain_id != self.chain_id:
                raise ChainConnectionError(
                    f"Chain ID mismatch: expected {self.chain_id}, got {actual_chain_id}",
                    rpc_url=self.rpc_url,
                )
            return True
        except ChainConnectionError:
            raise
        except Exception as e:
            raise ChainConnectionError(
                f"RPC health check failed: {e}",
                rpc_url=self.rpc_url,
            ) from e

    def get_block_number(self) -> int:
        """
        Get the latest block number.

        Returns:
            The current block number.

        Raises:
            ChainConnectionError: If RPC call fails.
        """
        try:
            return self._web3.eth.block_number
        except Exception as e:
            raise ChainConnectionError(
                f"Failed to get block number: {e}",
                rpc_url=self.rpc_url,
            ) from e

    def get_explorer_tx_url(self, tx_hash: str) -> str:
        """
        Get block explorer URL for a transaction.

        Args:
            tx_hash: Transaction hash (with or without 0x prefix).

        Returns:
            Full block explorer URL for the transaction.
        """
        if not tx_hash.startswith("0x"):
            tx_hash = "0x" + tx_hash
        return f"{self.explorer_url}/tx/{tx_hash}"

    def get_explorer_address_url(self, address: str) -> str:
        """
        Get block explorer URL for an address.

        Args:
            address: Ethereum address (with or without 0x prefix).

        Returns:
            Full block explorer URL for the address.
        """
        if not address.startswith("0x"):
            address = "0x" + address
        return f"{self.explorer_url}/address/{address}"
