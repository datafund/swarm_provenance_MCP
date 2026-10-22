"""
Chain provider for connecting to EVM-compatible networks.

Manages Web3 connections to Base Sepolia (testnet) and Base (mainnet)
for interacting with the DataProvenance smart contract.

Dependencies (web3, eth-account) are included in the default install.
"""

import logging
import threading
import weakref
from typing import List, Optional
from ..redact import redact_urls, url_host

from .exceptions import (
    ChainConfigurationError,
    ChainConnectionError,
    ChainIdMismatchError,
)

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


# Serialises endpoint switches. Providers and clients are shared across
# asyncio.to_thread workers; reentrant because a client's failover holds it
# while calling _try_fallback.
SWITCH_LOCK = threading.RLock()


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


# Networks whose chain id is fixed by the preset. The RPC must report it; it is
# never taken from the RPC. Only the remaining presets (localhost) auto-detect
# the id from a custom RPC.
PINNED_CHAINS = frozenset({"base-sepolia", "base"})

# Chain ids of valueless test networks: Base Sepolia, Ethereum Sepolia, and
# local Hardhat/Anvil and Ganache. Every other id is treated as mainnet.
# The single source for testnet decisions; import it rather than copying it.
TESTNET_CHAIN_IDS = frozenset({84532, 11155111, 31337, 1337})

# Upper bound for the start-up chain id query, so an unreachable RPC does not
# hold up server start for the full request timeout.
_STARTUP_CHAIN_ID_TIMEOUT = 5


def is_testnet_chain_id(chain_id: int) -> bool:
    """Whether a chain id belongs to a known valueless test network."""
    return chain_id in TESTNET_CHAIN_IDS


def _host(url: str) -> str:
    """Hostname only, so log lines never carry an RPC path or API key."""
    return url_host(url, "?")


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
        # Web3 instances whose eth_chainId matched chain_id. Every endpoint is
        # checked once before its first read or write (ensure_chain_id).
        self._verified = weakref.WeakSet()

        if chain in PINNED_CHAINS:
            # The configured network decides the chain id; the RPC must agree.
            self.chain_id = preset["chain_id"]
            self._check_chain_id_at_startup(Web3)
        elif self._custom_rpc:
            # Local presets auto-detect (e.g. a Hardhat fork on another id).
            try:
                self.chain_id = self._startup_web3(Web3).eth.chain_id
                self._verified.add(self._web3)
            except Exception:
                self.chain_id = preset["chain_id"]
        else:
            self.chain_id = preset["chain_id"]

    def _startup_web3(self, Web3):
        """A Web3 on the primary RPC with the short start-up timeout."""
        timeout = min(self._request_timeout, _STARTUP_CHAIN_ID_TIMEOUT)
        return Web3(_http_provider(Web3, self.rpc_url, timeout))

    @property
    def is_testnet(self) -> bool:
        """Whether the effective chain id is a valueless test network."""
        return is_testnet_chain_id(self.chain_id)

    def check_chain_id(self, web3, url: Optional[str] = None) -> None:
        """
        Refuse ``web3`` unless its ``eth_chainId`` equals ``self.chain_id``.

        Args:
            web3: The Web3 instance to ask.
            url: Its endpoint, for the error (default: the current rpc_url).

        Raises:
            ChainIdMismatchError: If the endpoint reports another chain.
            Exception: Whatever the RPC call raised.
        """
        url = url or self.rpc_url
        actual = web3.eth.chain_id
        if actual != self.chain_id:
            # A once-verified endpoint that changed chain (caught by a write's
            # pre-sign check) must be re-checked by the next read too.
            self._verified.discard(web3)
            raise ChainIdMismatchError(
                f"Chain ID mismatch: CHAIN_NAME={self.chain} expects chain id "
                f"{self.chain_id}, but the RPC at {_host(url)} reports {actual}. "
                "Refusing to use it; check CHAIN_RPC_URL / CHAIN_RPC_URLS or "
                "CHAIN_NAME.",
                rpc_url=url,
            )
        self._verified.add(web3)

    def ensure_chain_id(self, web3) -> None:
        """
        Check ``web3``'s chain id once, before its first read or write.

        Covers a primary that could not be reached at start-up. Endpoints
        that already passed a check (start-up, probe) cost nothing.
        """
        if web3 not in self._verified:
            self.check_chain_id(web3)

    def _check_chain_id_at_startup(self, Web3) -> None:
        """
        Refuse to start on a primary RPC that reports another chain.

        An unreachable primary is not fatal here: every transaction checks
        the chain id again right before signing.
        """
        try:
            self.check_chain_id(self._startup_web3(Web3))
        except ChainIdMismatchError:
            raise
        except Exception as e:
            logger.warning(
                "Could not read the chain id of the RPC at start-up (%s); "
                "it is checked before first use and before every transaction",
                redact_urls(e),
            )
            return
        self._verified.add(self._web3)

    @property
    def web3(self):
        """Get the Web3 instance."""
        return self._web3

    def _probe(self, web3, url: Optional[str] = None) -> None:
        """
        Run the liveness probe (``PROBE_METHODS``) against a Web3 instance.

        Raises:
            ChainIdMismatchError: If the chain ID does not match.
            Exception: Whatever the RPC call raised if a method fails.
        """
        self.check_chain_id(web3, url)
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

        with SWITCH_LOCK:
            return self._switch_to_first_healthy(Web3)

    def _fallback_from(self, failed_web3) -> bool:
        """
        Fail over away from ``failed_web3`` unless that already happened.

        A concurrent caller may have switched since the failing call started;
        switching again would move away from the endpoint it just chose.

        Returns:
            True if the provider is now on a different endpoint.
        """
        with SWITCH_LOCK:
            if self._web3 is not failed_web3:
                return True
            return self._try_fallback()

    def _switch_to_first_healthy(self, Web3) -> bool:
        """
        Body of _try_fallback; call with SWITCH_LOCK held.

        Raises:
            ChainIdMismatchError: If every fallback tried is on another chain
                (a configuration error, not an outage).
        """
        tried = 0
        mismatches = []
        for url in self._rpc_urls:
            if url == self.rpc_url:
                continue
            tried += 1
            try:
                candidate = Web3(_http_provider(Web3, url, self._request_timeout))
                self._probe(candidate, url)
            except ChainIdMismatchError as e:
                logger.warning(
                    "RPC fallback %s refused: %s", _host(url), redact_urls(e)
                )
                mismatches.append(e)
                continue
            except Exception as e:
                logger.debug(
                    "RPC fallback %s failed probe: %s", _host(url), redact_urls(e)
                )
                continue
            logger.info(
                "RPC fallback: switched from %s to %s",
                _host(self.rpc_url),
                _host(url),
            )
            self._web3 = candidate
            self.rpc_url = url
            return True
        if tried and len(mismatches) == tried:
            raise mismatches[-1]
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
        probed = self._web3
        try:
            self._probe(probed)
            return True
        except Exception as e:
            if self._fallback_from(probed):
                return self.health_check()
            if isinstance(e, (ChainConnectionError, ChainIdMismatchError)):
                raise
            raise ChainConnectionError(
                f"RPC health check failed: {redact_urls(e)}",
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
        used = self._web3
        try:
            return used.eth.block_number
        except Exception as e:
            if self._fallback_from(used):
                return self.get_block_number()
            raise ChainConnectionError(
                f"Failed to get block number: {redact_urls(e)}",
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
