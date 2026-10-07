"""
Chain provider for connecting to EVM-compatible networks.

Manages Web3 connections to Base Sepolia (testnet) and Base (mainnet)
for interacting with the DataProvenance smart contract.

Dependencies (web3, eth-account) are included in the default install.
"""

import logging
from typing import List, Optional

from .exceptions import ChainConfigurationError, ChainConnectionError

logger = logging.getLogger(__name__)

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
                "web3 not available. " "Reinstall with: pip install -e ."
            ) from e
    return _Web3


# HTTP statuses that mean "this endpoint cannot serve right now" rather than
# "this request is wrong" — another endpoint may well succeed.
_TRANSPORT_HTTP_STATUSES = (429, 502, 503, 504)

# JSON-RPC errors that some providers return with HTTP 200 when their backend
# pool is unhealthy (e.g. -32011 "no backend is currently healthy...").
_TRANSPORT_RPC_MARKERS = ("-32011", "no backend is currently healthy")

# RPC methods exercised by the liveness probe. eth_chainId alone is answered
# from cache by some providers even when every state method is failing, so
# the probe also runs the state methods a write needs.
PROBE_METHODS = ("eth_chainId", "eth_gasPrice", "eth_getTransactionCount")


def is_transport_error(exc: BaseException) -> bool:
    """
    Check whether an exception means the RPC endpoint itself is unavailable.

    Walks the ``__cause__``/``__context__`` chain, so wrapped errors (e.g. a
    ``ChainTransactionError`` raised from an ``HTTPError``) are recognised.
    Reverts, validation errors and insufficient funds are not transport errors.
    """
    from requests.exceptions import ConnectionError as ReqConnectionError
    from requests.exceptions import HTTPError, Timeout

    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, (ReqConnectionError, Timeout)):
            return True
        if isinstance(exc, HTTPError):
            response = getattr(exc, "response", None)
            if response is not None:
                return response.status_code in _TRANSPORT_HTTP_STATUSES
            return any(f"{code} " in str(exc) for code in _TRANSPORT_HTTP_STATUSES)
        message = str(exc).lower()
        if any(marker in message for marker in _TRANSPORT_RPC_MARKERS):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


# Network presets for supported chains
CHAIN_PRESETS = {
    "base-sepolia": {
        "chain_id": 84532,
        "rpc_url": "https://sepolia.base.org",
        "explorer_url": "https://base-sepolia.blockscout.com",
        "contract_address": "0x3945aDfd5Df9ab2F5cB4Ca0eb3D4384CC3650322",
        "deploy_block": 39_898_628,
        "rpc_fallbacks": [
            "https://base-sepolia-rpc.publicnode.com",
            "https://base-sepolia.drpc.org",
            "https://base-sepolia.gateway.tenderly.co",
        ],
    },
    "base": {
        "chain_id": 8453,
        "rpc_url": "https://mainnet.base.org",
        "explorer_url": "https://basescan.org",
        "contract_address": None,  # Not yet deployed
        "deploy_block": None,
        "rpc_fallbacks": [
            "https://base-rpc.publicnode.com",
            "https://base.drpc.org",
        ],
    },
    "localhost": {
        "chain_id": 31337,
        "rpc_url": "http://127.0.0.1:8545",
        "explorer_url": None,
        "contract_address": "0xCf7Ed3AccA5a467e9e704C703E8D87F634fB0Fc9",
        "deploy_block": 0,
        "rpc_fallbacks": [],
    },
}


def _http_provider(Web3, url: str, timeout: int):
    """
    Build an HTTPProvider with explicit retry behaviour.

    web3's default retries each failing call 5 times with backoff (~1.9 s per
    503), including eth_sendRawTransaction, so one 503 re-sent the same signed
    transaction five times and every failover waited out the backoff first.
    Reads retry once; sends never do — failover handles the rest.
    """
    from requests.exceptions import ConnectionError as ReqConnectionError
    from requests.exceptions import HTTPError, Timeout

    try:
        from web3.providers.rpc.utils import (
            REQUEST_RETRY_ALLOWLIST,
            ExceptionRetryConfiguration,
        )
    except ImportError:
        # web3 6 (pyproject allows it) retries through a provider middleware
        # instead — 5 attempts, eth_sendRawTransaction included. Drop it and
        # leave retries to failover.
        provider = Web3.HTTPProvider(url, request_kwargs={"timeout": timeout})
        provider.middlewares = ()
        return provider

    return Web3.HTTPProvider(
        url,
        request_kwargs={"timeout": timeout},
        exception_retry_configuration=ExceptionRetryConfiguration(
            errors=(ReqConnectionError, HTTPError, Timeout),
            retries=2,  # total attempts: one retry
            backoff_factor=0.1,
            method_allowlist=[
                m for m in REQUEST_RETRY_ALLOWLIST if m != "eth_sendRawTransaction"
            ],
        ),
    )


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
        rpc_fallbacks: Optional[List[str]] = None,
        request_timeout: int = 30,
    ):
        """
        Initialize the chain provider.

        Args:
            chain: Chain name ('base-sepolia' or 'base').
            rpc_url: Custom RPC URL. If None, uses preset for chain.
            contract_address: Custom contract address. If None, uses preset.
            explorer_url: Custom block explorer URL. If None, uses preset.
            rpc_fallbacks: Fallback RPC URLs tried in order on failure.
                If None and no custom rpc_url, uses preset fallbacks.
            request_timeout: HTTP request timeout in seconds (default 30).

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
        self.deploy_block = preset.get("deploy_block")
        self._custom_rpc = rpc_url is not None
        self._request_timeout = request_timeout

        # Build ordered list of RPC URLs to try
        if rpc_fallbacks is not None:
            # Explicit fallbacks provided — use them regardless of custom RPC
            self._rpc_urls = [self.rpc_url] + list(rpc_fallbacks)
        elif not self._custom_rpc:
            # No custom RPC — use preset fallbacks
            self._rpc_urls = [self.rpc_url] + preset.get("rpc_fallbacks", [])
        else:
            # Custom RPC with no explicit fallbacks — primary only
            self._rpc_urls = [self.rpc_url]

        if not self.contract_address:
            raise ChainConfigurationError(
                f"No contract address configured for chain '{chain}'. "
                "Provide one via contract_address parameter or CHAIN_CONTRACT env var."
            )

        # Initialize Web3 connection
        self._web3 = Web3(_http_provider(Web3, self.rpc_url, self._request_timeout))

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

    def _probe(self, web3) -> None:
        """
        Run the liveness probe (``PROBE_METHODS``) against a Web3 instance.

        Raises:
            ChainConnectionError: If the chain ID does not match.
            Exception: Whatever the RPC call raised if a method fails.
        """
        actual_chain_id = web3.eth.chain_id
        if actual_chain_id != self.chain_id:
            raise ChainConnectionError(
                f"Chain ID mismatch: expected {self.chain_id}, got {actual_chain_id}",
                rpc_url=self.rpc_url,
            )
        _ = web3.eth.gas_price  # the call is the probe; the value is unused
        web3.eth.get_transaction_count(self.contract_address)

    def _try_fallback(self) -> bool:
        """
        Try fallback RPC URLs when the current one fails.

        Iterates through remaining URLs in ``_rpc_urls`` (skipping the
        current ``rpc_url``), runs the liveness probe against each, and
        switches ``_web3`` and ``rpc_url`` on the first that passes.

        Returns:
            True if a working fallback was found and switched to.
        """
        Web3 = _import_web3()

        for url in self._rpc_urls:
            if url == self.rpc_url:
                continue
            try:
                candidate = Web3(_http_provider(Web3, url, self._request_timeout))
                self._probe(candidate)
            except Exception as e:
                logger.debug("RPC fallback %s failed probe: %s", url, e)
                continue
            logger.info(
                "RPC fallback: switched from %s to %s",
                self.rpc_url,
                url,
            )
            self._web3 = candidate
            self.rpc_url = url
            return True
        return False

    def health_check(self) -> bool:
        """
        Check if the RPC endpoint can serve the methods a write needs.

        Runs ``PROBE_METHODS`` rather than a cached-method liveness check, so
        an endpoint that answers ``eth_chainId`` but fails state methods is
        reported unhealthy (and a fallback is tried).

        Returns:
            True if every probe method succeeded and chain ID matches.

        Raises:
            ChainConnectionError: If the probe fails (after trying fallbacks).
        """
        try:
            self._probe(self._web3)
            return True
        except Exception as e:
            if self._try_fallback():
                return self.health_check()
            if isinstance(e, ChainConnectionError):
                raise
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
            ChainConnectionError: If RPC call fails (after trying fallbacks).
        """
        try:
            return self._web3.eth.block_number
        except Exception as e:
            if self._try_fallback():
                return self.get_block_number()
            raise ChainConnectionError(
                f"Failed to get block number: {e}",
                rpc_url=self.rpc_url,
            ) from e

    def get_explorer_tx_url(self, tx_hash: str) -> Optional[str]:
        """
        Get block explorer URL for a transaction.

        Args:
            tx_hash: Transaction hash (with or without 0x prefix).

        Returns:
            Full block explorer URL for the transaction, or None if no explorer configured.
        """
        if not self.explorer_url:
            return None
        if not tx_hash.startswith("0x"):
            tx_hash = "0x" + tx_hash
        return f"{self.explorer_url}/tx/{tx_hash}"

    def get_explorer_address_url(self, address: str) -> Optional[str]:
        """
        Get block explorer URL for an address.

        Args:
            address: Ethereum address (with or without 0x prefix).

        Returns:
            Full block explorer URL for the address, or None if no explorer configured.
        """
        if not self.explorer_url:
            return None
        if not address.startswith("0x"):
            address = "0x" + address
        return f"{self.explorer_url}/address/{address}"
