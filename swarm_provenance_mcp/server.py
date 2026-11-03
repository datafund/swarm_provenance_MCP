"""MCP server implementation for Swarm stamp management."""

import asyncio
import hashlib
import hmac
import importlib.resources
import json
import logging
import math
import re
import secrets
import time
import traceback
from decimal import Decimal, localcontext
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.types import (
    Tool,
    TextContent,
    CallToolRequest,
    CallToolResult,
    ListToolsRequest,
    Resource,
    Prompt,
    PromptArgument,
    PromptMessage,
    GetPromptResult,
    ToolAnnotations,
)
from requests.exceptions import RequestException

from . import bmt
from .config import WALLET_KEY_HINT, is_plain_http_remote, settings
from .gateway_client import (
    GatewayAnswerUnreadable,
    PaymentRequiredError,
    SwarmGatewayClient,
)
from .redact import mask_literal_urls, redact_urls, rpc_host, url_without_userinfo
from .untrusted import (
    MAX_EXCEPTION_CHARS,
    QUOTED_NOTE,
    defang_hint_lines,
    fence,
    flatten,
    one_line,
    quote,
)


def _gw(value: Any, max_chars: int = 100) -> str:
    """A gateway or RPC field, flattened to one capped line for printing."""
    return one_line(value, max_chars)


def _flag_text(value: Any) -> str:
    """A gateway flag: True or False only for a real bool, else capped by _gw."""
    if isinstance(value, bool):
        return str(value)
    return _gw(value, 20) or "N/A"


# How a tool describes a gateway answer, or part of one, of the wrong type.
# The value itself is never printed.
_NOT_EXPECTED_FORMAT = "the gateway's answer is not in the expected format"


def _gw_text(value: Any, max_chars: int = 100) -> str:
    """A gateway text field for printing: only a str, through _gw; "" for
    any other type, so a wrong-typed value is never shown as its repr."""
    return _gw(value, max_chars) if isinstance(value, str) else ""


def _is_text(value: Any) -> bool:
    """Whether an optional gateway text field is printed: a non-empty str.

    Blank text counts, as it did before #270: it is a valid value of an
    Optional[str] field. Any other type is never printed."""
    return isinstance(value, str) and bool(value)


def _number_in(value: Any, low: float, high: float) -> Optional[Union[int, float]]:
    """A gateway number, only when it is an int or float (not a bool) from
    ``low`` to ``high``; otherwise None.

    Compares without converting, so NaN (every comparison is false), the
    infinities and ints too large for a float are rejected without raising
    (math.isfinite raises OverflowError on such an int)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if low <= value <= high:
            return value
    return None


# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global gateway client instance
gateway_client = SwarmGatewayClient()

# Chain client (initialized when chain_enabled=true and dependencies available)
chain_client = None
CHAIN_AVAILABLE = False
# Why writes are disabled although a wallet is configured (e.g. no contract
# code at the configured address). Reported by the write tools.
_chain_init_error: Optional[str] = None

# How to fix the cause in _chain_init_error, for the write tools' errors.
CONTRACT_FIX = (
    "Fix CHAIN_CONTRACT or CHAIN_NAME, then restart the server. The "
    "address is checked again before each write."
)
WALLET_KEY_INVALID_REASON = (
    "PROVENANCE_WALLET_KEY is set but is not a valid private key "
    "(expected 64 hexadecimal characters, with or without 0x)."
)
WALLET_KEY_INVALID_FIX = (
    "Fix PROVENANCE_WALLET_KEY, or remove it for read-only mode, then "
    "restart the server. Read-only chain tools work in the meantime."
)
_chain_init_fix: str = CONTRACT_FIX


def _wallet_key_is_invalid(key: Optional[str]) -> bool:
    """Whether a configured wallet key cannot be loaded. An unset or empty
    key is not invalid: it means read-only mode."""
    if not key:
        return False
    from .chain.exceptions import ChainConfigurationError
    from .chain.wallet import ChainWallet

    try:
        ChainWallet(private_key=key)
    except ChainConfigurationError:
        return True
    return False


def _parse_rpc_fallbacks():
    """Parse CHAIN_RPC_URLS into a list of fallback URLs, or None."""
    if settings.chain_rpc_urls:
        return [u.strip() for u in settings.chain_rpc_urls.split(",") if u.strip()]
    return None


_read_only_client = None


def _chain_reader():
    """
    ChainClient for the read-only tools: the wallet client when configured,
    otherwise a read-only client, so reads get the same RPC failover either way.

    The read-only client is kept across calls so a switch to a fallback RPC
    persists instead of every call starting on the degraded primary again.
    """
    global _read_only_client
    if chain_client:
        return chain_client
    if _read_only_client is not None:
        return _read_only_client
    from .chain.client import ChainClient

    _read_only_client = ChainClient(
        chain=settings.chain_name,
        rpc_url=settings.chain_rpc_url,
        contract_address=settings.chain_contract_address,
        explorer_url=settings.chain_explorer_url,
        rpc_fallbacks=_parse_rpc_fallbacks(),
        read_only=True,
    )
    return _read_only_client


# Budget per RPC endpoint for the start-up contract check and its re-check
# while writes are held back, in seconds.
STARTUP_CONTRACT_CHECK_TIMEOUT = 5.0

# Wallet client held back because the start-up check found no DataProvenance
# code. Re-checked before each write (see _recheck_write_contract), so a node
# that briefly answered "no code" does not disable writes until restart.
_unverified_write_client = None


def _contract_error_reason(client, error) -> str:
    """Reason that writes are disabled, naming the address and chain."""
    try:
        chain = f"{client.chain}, chain id {client._provider.chain_id}"
    except Exception:
        chain = settings.chain_name
    return f"{error} Chain: {chain}."


def _verify_write_contract(client):
    """
    Start-up check that the configured address holds the DataProvenance
    contract.

    Uses a short per-endpoint timeout and tries the fallback RPCs, so a hung
    primary cannot stall start-up.

    Returns:
        ``(client, None)`` when the check passes or cannot run yet (no RPC
        answered: it runs again before the first transaction), or
        ``(None, reason)`` when the address has no DataProvenance code — a
        transaction there would spend gas and record nothing. The client is
        then kept in ``_unverified_write_client`` and re-checked before the
        next write.
    """
    global _unverified_write_client
    from .chain.exceptions import ChainConfigurationError

    try:
        client.probe_contract(timeout=STARTUP_CONTRACT_CHECK_TIMEOUT)
    except ChainConfigurationError as e:
        reason = _contract_error_reason(client, e)
        logger.error("Chain writes disabled: %s", redact_urls(reason))
        _unverified_write_client = client
        return None, reason
    except Exception as e:
        logger.warning(
            "Could not verify the contract at start-up (%s); it is checked "
            "again before the first transaction",
            redact_urls(e),
        )
    return client, None


async def _recheck_write_contract() -> None:
    """
    Re-run the contract check for a wallet client that the start-up check
    held back. On success writes are enabled again; otherwise the reason is
    refreshed and writes stay disabled.
    """
    global chain_client, _chain_init_error, _unverified_write_client
    client = _unverified_write_client
    if chain_client is not None or client is None:
        return
    from .chain.exceptions import ChainConfigurationError

    try:
        await asyncio.to_thread(
            client.probe_contract, timeout=STARTUP_CONTRACT_CHECK_TIMEOUT
        )
    except ChainConfigurationError as e:
        _chain_init_error = _contract_error_reason(client, e)
        return
    except Exception as e:
        logger.warning(
            "Contract re-check failed (%s); writes stay disabled", redact_urls(e)
        )
        return
    logger.info("Contract found at %s; chain writes enabled", client.contract_address)
    chain_client = client
    _chain_init_error = None
    _unverified_write_client = None


if settings.chain_enabled:
    try:
        from .chain import CHAIN_AVAILABLE as _chain_avail, ChainClient

        CHAIN_AVAILABLE = _chain_avail
        if CHAIN_AVAILABLE and _wallet_key_is_invalid(settings.provenance_wallet_key):
            # Not read-only mode: the operator asked for writes. Neither the
            # key nor the loader's error text (which may quote it) is logged.
            _chain_init_error = WALLET_KEY_INVALID_REASON
            _chain_init_fix = WALLET_KEY_INVALID_FIX
            logger.error("Chain writes disabled: %s", WALLET_KEY_INVALID_REASON)
        elif CHAIN_AVAILABLE:
            chain_client = ChainClient(
                chain=settings.chain_name,
                rpc_url=settings.chain_rpc_url,
                contract_address=settings.chain_contract_address,
                private_key=settings.provenance_wallet_key,
                explorer_url=settings.chain_explorer_url,
                gas_limit=settings.chain_gas_limit,
                rpc_fallbacks=_parse_rpc_fallbacks(),
                max_fee_gwei=settings.chain_max_fee_gwei,
                session_budget_eth=settings.chain_session_budget_eth,
                max_tx_per_session=settings.chain_max_tx_per_session,
                l1_fee_allowance_eth=settings.chain_l1_fee_allowance_eth,
            )
            logger.info(
                "Chain client initialized: chain=%s contract=%s",
                chain_client.chain,
                chain_client.contract_address,
            )
            chain_client, _chain_init_error = _verify_write_contract(chain_client)
        else:
            logger.warning(
                "Chain enabled but blockchain dependencies not available. "
                "Reinstall with: pip install -e ."
            )
    except Exception as e:
        from .chain.exceptions import ChainIdMismatchError

        if isinstance(e, ChainIdMismatchError):
            # The RPC is on another chain than CHAIN_NAME: refuse to start
            # rather than serve reads from, or sign for, the wrong network.
            logger.error("Chain configuration refused: %s", redact_urls(e))
            raise
        # chain_client stays None but CHAIN_AVAILABLE remains True —
        # read-only tools (verify_hash, get_provenance, etc.) work
        # without a wallet via _chain_reader().
        err_msg = str(e).lower()
        if "wallet" in err_msg or "private key" in err_msg:
            logger.info(
                "Chain: enabled (%s) — read-only mode (no wallet configured)",
                settings.chain_name,
            )
        else:
            logger.warning("Chain client init failed: %s", redact_urls(e))


def _load_skills_content() -> str:
    """Load SKILLS.md for the provenance://skills resource.

    The file is package data, so it is present in a non-editable install and
    in the Docker image, not only in a source checkout.
    """
    try:
        return (
            importlib.resources.files("swarm_provenance_mcp")
            .joinpath("SKILLS.md")
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, OSError):
        return "Provenance skills guide not found. See MCP_INSTRUCTIONS for basic guidance."


_SKILLS_CONTENT = _load_skills_content()

_CHAIN_ID_NAMES = {
    84532: "Base Sepolia",
    8453: "Base mainnet",
    11155111: "Ethereum Sepolia",
    1: "Ethereum mainnet",
    31337: "a local Hardhat/Anvil chain",
    1337: "a local dev chain",
}


def _effective_chain_id(chain_name: str, client=None) -> Optional[int]:
    """
    Chain id for ``chain_name``: the client's (pinned or auto-detected) id
    when a client on that chain is available, else the preset id. None when
    the id is only known from the RPC (localhost with a custom RPC).
    """
    from .chain.provider import CHAIN_PRESETS, PINNED_CHAINS

    client = client or chain_client
    if client is not None and getattr(client, "chain", None) == chain_name:
        chain_id = getattr(getattr(client, "_provider", None), "chain_id", None)
        if isinstance(chain_id, int):
            return chain_id
    preset = CHAIN_PRESETS.get(chain_name)
    if preset is None:
        return None
    if chain_name in PINNED_CHAINS or not settings.chain_rpc_url:
        return preset["chain_id"]
    return None


def _is_testnet(chain_name: str, client=None) -> bool:
    """Testnet decision from the effective chain id (unknown → not a testnet)."""
    from .chain.provider import is_testnet_chain_id

    chain_id = _effective_chain_id(chain_name, client)
    return chain_id is not None and is_testnet_chain_id(chain_id)


def _chain_network_text() -> str:
    """The network the chain tools act on, worded from the effective chain id."""
    chain_id = _effective_chain_id(settings.chain_name)
    if chain_id is None:
        return (
            f"on the chain behind CHAIN_RPC_URL (CHAIN_NAME={settings.chain_name}; "
            "its id is read from the RPC, run chain_health to see it, and treat "
            "it as mainnet unless that reports a testnet)"
        )
    name = _CHAIN_ID_NAMES.get(chain_id, f"chain {chain_id}")
    if _is_testnet(settings.chain_name):
        return f"on {name} (chain id {chain_id}, a testnet: tokens have no value)"
    return (
        f"on {name} (chain id {chain_id}). This is MAINNET: every write spends "
        "real ETH"
    )


# Agent-facing instructions sent during MCP initialization handshake
MCP_INSTRUCTIONS = """
Swarm Provenance MCP — decentralized storage with cryptographic provenance on the Swarm network.

DISCLAIMER:
This software is in beta and provided as-is, without warranties of any kind. On-chain provenance features use Base Sepolia (testnet) by default — testnet tokens have no monetary value and testnet state may be reset. Do not rely on testnet anchoring for production data integrity.

PAYMENT MODEL:
This server uses the free tier by default (rate limit: 3 write requests/minute, reads unlimited). Paid x402 support is not yet integrated.

STAMP OWNERSHIP:
- purchase_stamp creates public stamps (accessMode: "shared"). Any gateway user can upload to them, so utilization is unpredictable. Suitable for testing and small workloads.
- This server sends no wallet, so it can upload only to "shared" stamps and stamps without an access mode. The gateway refuses its uploads to "owned" stamps (registered to the wallet that paid for them) and "pool" stamps (gateway inventory).
- list_stamps can also show "owned" and "pool" stamps and marks them "Not usable by this server". Do not pick them for upload_data. Upload only to a stamp it marks "Usable". For capacity that other users cannot fill, run your own Bee node or gateway.

TYPICAL WORKFLOW:
1. health_check — verify gateway is reachable
2. purchase_stamp — create your own stamp (or use one you previously purchased)
3. The stamp may be available immediately (from the gateway's stamp pool) or may need
   up to 2 minutes to propagate on the blockchain (if the pool was empty and a fresh
   stamp was minted). Use check_stamp_health to poll — it returns can_upload: true
   when the stamp is ready.
4. upload_data — store data, get a reference hash back
5. download_data — retrieve data using the reference hash

KEY CONSTRAINTS:
- Max upload size: 4KB (4096 bytes) per request
- After purchase_stamp, the stamp may be instantly usable (pool) or take up to 2 minutes (fresh mint). Poll with check_stamp_health every 10-15 seconds until can_upload is true.
- After extend_stamp, wait up to 2 minutes for the extension to propagate.
- Stamp size controls capacity: small (~35KB), medium (~500MB), large (~6GB)
- duration_hours controls TTL — minimum 24 hours
- Stamps are rented storage — data expires when the stamp TTL runs out
- A stamp can be reused for multiple uploads until capacity or TTL is exhausted

TOOL RELATIONSHIPS:
- purchase_stamp → returns stamp_id → use with upload_data, check_stamp_health, get_stamp_status, extend_stamp
- upload_data → returns reference hash → use with download_data
- check_stamp_health gives detailed diagnostics; get_stamp_status gives raw stamp data
- extend_stamp increases TTL via duration_hours (not capacity)
- anchor_hash → optionally accepts storage_ref → use lookup_by_storage_ref to reverse-lookup
- set_storage_ref → links storage ref to an already-anchored hash (set-once) → use lookup_by_storage_ref to verify

ERRORS:
- "Not usable" / "NOT_FOUND": stamp not yet propagated — poll check_stamp_health every 15s (up to 2 min)
- Size exceeded: data > 4KB, reduce payload before upload

CHAIN ANCHORING (optional):
When chain_enabled=true, on-chain provenance tools become available (blockchain dependencies are included in the default install).
These register Swarm hashes in the DataProvenance smart contract __CHAIN_NETWORK__, creating an immutable on-chain record.
- chain_health — test RPC connectivity (no wallet needed)
- chain_balance — check wallet ETH balance with funding guidance when low
- anchor_hash — register a Swarm hash on-chain, optionally with a storage_ref for bidirectional lookup (costs gas, requires funded wallet)
- verify_hash — check if a Swarm hash is registered on-chain (read-only, no gas)
- get_provenance — retrieve full provenance record: owner, timestamp, data type, status, storage ref, transformations, accessors (read-only)
- record_transform — record data transformation linking original to new hash, creating lineage (costs gas; optionally restricts original)
- get_provenance_chain — follow transformation lineage for a hash, building a tree of how data evolved (read-only)
- set_storage_ref — attach a Swarm storage reference to an existing on-chain record (set-once, owner-only, costs gas)
- lookup_by_storage_ref — reverse lookup: find the provenance record by its Swarm storage reference (read-only, no gas)
The health_check tool reports chain status alongside gateway status.

PROVENANCE RULES (critical):
1. record_transform REGISTERS the new_hash on-chain automatically. NEVER call anchor_hash
   on a hash that will be the new_hash in record_transform — this creates a duplicate
   instead of a proper transformation link.
2. The original_hash MUST be anchored before calling record_transform.
3. Only the data owner (or authorized delegate) can record transformations.
4. Stamps (BZZ) pay for Swarm storage; chain anchoring costs ETH gas — separate systems.
5. Lineage is a DAG: one original can have multiple transformations.
6. For the full provenance guide with examples and diagrams, read the provenance://skills resource.

CHAIN WORKFLOW — store with on-chain provenance:
health_check → purchase_stamp → check_stamp_health → upload_data → anchor_hash → verify_hash

CHAIN WORKFLOW — store with storage reference linking:
health_check → purchase_stamp → upload_data → anchor_hash (with storage_ref = Swarm reference) → lookup_by_storage_ref

CHAIN WORKFLOW — record a transformation:
upload_data (transformed data) → record_transform (original, new, description) → upload_data (to Swarm) → set_storage_ref (link Swarm ref to new hash) → get_provenance_chain

COMPANION SERVERS:
- swarm_connect gateway (required) — the FastAPI gateway this server talks to, handles Bee node communication
The health_check tool reports gateway connectivity and connection status.
""".strip().replace("__CHAIN_NETWORK__", _chain_network_text())

# Values accepted for upload_data's sign argument (the schema's enum).
UPLOAD_SIGN_METHODS = frozenset({"notary"})

# Validation patterns
STAMP_ID_PATTERN = re.compile(r"^[a-fA-F0-9]{64}$")
REFERENCE_HASH_PATTERN = re.compile(r"^[a-fA-F0-9]{64}$")
# A Swarm reference to download: 64 hex characters, or 128 for an encrypted
# reference (reference plus decryption key). Chain tools take bytes32, so they
# keep REFERENCE_HASH_PATTERN.
SWARM_REFERENCE_SCHEMA_PATTERN = "^([a-fA-F0-9]{64}|[a-fA-F0-9]{128})$"
SWARM_REFERENCE_PATTERN = re.compile(SWARM_REFERENCE_SCHEMA_PATTERN)
ADDRESS_PATTERN = re.compile(r"0x[a-fA-F0-9]{40}")

# Latest Unix timestamp printed as a date (9999-12-31 23:59:59 UTC).
MAX_PRINTED_TIMESTAMP = 253402300799


def validate_address_argument(value: Any, name: str) -> str:
    """Validate an Ethereum address tool argument (0x plus 40 hex digits)."""
    if not isinstance(value, str) or not ADDRESS_PATTERN.fullmatch(value):
        raise ValueError(f"{name} must be 0x followed by 40 hexadecimal characters")
    return value


def _is_address(value: Any) -> bool:
    """Whether a chain-supplied value is a well-formed address string."""
    return isinstance(value, str) and ADDRESS_PATTERN.fullmatch(value) is not None


def _chain_address(value: Any) -> str:
    """A chain-supplied address for printing: as is when well formed,
    otherwise flattened to one capped line."""
    return value if _is_address(value) else _gw(value, 64)


def _owner_matches(record_owner: Any, expected: Any) -> bool:
    """Whether two addresses are the same, ignoring case. A value that is
    not a well-formed address never matches."""
    return (
        _is_address(record_owner)
        and _is_address(expected)
        and record_owner.lower() == expected.lower()
    )


def _format_chain_timestamp(value: Any) -> str:
    """A chain-supplied Unix timestamp as a UTC date, or "unknown" when it is
    not a positive integer in the printable range."""
    from datetime import datetime, timezone

    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 < value <= MAX_PRINTED_TIMESTAMP
    ):
        return "unknown"
    try:
        dt = datetime.fromtimestamp(value, tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return "unknown"
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def validate_and_clean_stamp_id(stamp_id: str) -> str:
    """Validate and clean stamp ID, removing 0x prefix if present.

    Args:
        stamp_id: The stamp ID to validate

    Returns:
        Cleaned stamp ID without 0x prefix

    Raises:
        ValueError: If stamp ID format is invalid
    """
    if not stamp_id:
        raise ValueError("Stamp ID cannot be empty")

    # Remove 0x prefix if present
    if stamp_id.startswith("0x") or stamp_id.startswith("0X"):
        stamp_id = stamp_id[2:]

    # Validate format
    # fullmatch: "$" also matches before a final newline.
    if not STAMP_ID_PATTERN.fullmatch(stamp_id):
        raise ValueError(
            f"Invalid stamp ID format. Expected 64-character hexadecimal string (without 0x prefix), got: {stamp_id}"
        )

    return stamp_id


def _is_gateway_batch_id(value: Any) -> bool:
    """Whether a gateway batch ID is a string that validate_and_clean_stamp_id
    accepts: exactly 64 hexadecimal characters, with or without 0x."""
    return _clean_gateway_batch_id(value) is not None


def _clean_gateway_batch_id(value: Any) -> Optional[str]:
    """A gateway batch ID as 64 hexadecimal characters without 0x, or None
    when it is not one. Every reader of a gateway batchID uses this."""
    if not isinstance(value, str):
        return None
    try:
        return validate_and_clean_stamp_id(value)
    except ValueError:
        return None


def validate_and_clean_reference_hash(reference: str) -> str:
    """Validate and clean reference hash, removing 0x prefix if present.

    Args:
        reference: The reference hash to validate

    Returns:
        Cleaned reference hash without 0x prefix

    Raises:
        ValueError: If reference hash format is invalid
    """
    if not reference:
        raise ValueError("Reference hash cannot be empty")

    # Remove 0x prefix if present
    if reference.startswith("0x") or reference.startswith("0X"):
        reference = reference[2:]

    # Validate format
    if not REFERENCE_HASH_PATTERN.fullmatch(reference):
        raise ValueError(
            f"Invalid reference hash format. Expected 64-character hexadecimal string (without 0x prefix), got: {reference}"
        )

    return reference


def validate_and_clean_swarm_reference(reference: str) -> str:
    """Validate a Swarm reference for download_data, removing a 0x prefix.

    Accepts 64 hex characters, or 128 for an encrypted reference.

    Raises:
        ValueError: If the reference format is invalid
    """
    if not reference or not isinstance(reference, str):
        raise ValueError("Reference cannot be empty")
    if reference.startswith(("0x", "0X")):
        reference = reference[2:]
    if not SWARM_REFERENCE_PATTERN.fullmatch(reference):
        raise ValueError(
            "Invalid Swarm reference format. Expected 64 or 128 hexadecimal "
            "characters (128 for an encrypted reference), without 0x prefix; "
            f"got {len(reference)} characters"
        )
    return reference


def validate_stamp_duration_hours(duration_hours: int) -> None:
    """Validate stamp duration in hours.

    Args:
        duration_hours: The duration to validate

    Raises:
        ValueError: If duration is invalid
    """
    if duration_hours < 24:
        raise ValueError(
            f"Stamp duration must be at least 24 hours, got: {duration_hours}"
        )
    if duration_hours > settings.max_stamp_duration_hours:
        raise ValueError(
            f"Stamp duration must be at most {settings.max_stamp_duration_hours} "
            f"hours (MAX_STAMP_DURATION_HOURS), got: {duration_hours}"
        )


VALID_STAMP_SIZES = ("small", "medium", "large")


def validate_stamp_size(size: str) -> None:
    """Validate stamp size preset.

    Args:
        size: The size to validate

    Raises:
        ValueError: If size is invalid
    """
    if size not in VALID_STAMP_SIZES:
        raise ValueError(
            f"Stamp size must be one of {', '.join(VALID_STAMP_SIZES)}, got: {size!r}"
        )


def validate_stamp_depth(depth: int) -> None:
    """Validate stamp depth (optional override).

    Args:
        depth: The depth to validate

    Raises:
        ValueError: If depth is invalid
    """
    if not (17 <= depth <= 22):
        raise ValueError(f"Stamp depth must be between 17 and 22, got: {depth}")


def validate_data_size(data: str) -> None:
    """Validate data size for upload.

    Args:
        data: The data to validate

    Raises:
        ValueError: If data size is invalid
    """
    data_bytes = data.encode("utf-8")
    if len(data_bytes) > 4096:
        raise ValueError(
            f"Data size {len(data_bytes)} bytes exceeds 4KB limit (4096 bytes)"
        )
    if len(data_bytes) == 0:
        raise ValueError("Data cannot be empty")


# All tool names for typo correction
ALL_TOOL_NAMES = [
    "purchase_stamp",
    "get_stamp_status",
    "list_stamps",
    "extend_stamp",
    "upload_data",
    "download_data",
    "check_stamp_health",
    "get_wallet_info",
    "get_notary_info",
    "health_check",
    "chain_balance",
    "chain_health",
    "anchor_hash",
    "verify_hash",
    "get_provenance",
    "record_transform",
    "record_merge_transform",
    "get_provenance_chain",
    "set_storage_ref",
    "lookup_by_storage_ref",
]


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row
    return prev_row[-1]


def _suggest_tool_name(name: str) -> str:
    """Build error message with typo correction suggestions for unknown tool names."""
    threshold = max(4, len(name) * 2 // 5)
    scored = [(t, _levenshtein_distance(name, t)) for t in ALL_TOOL_NAMES]
    close = sorted([(t, d) for t, d in scored if d <= threshold], key=lambda x: x[1])
    suggestions = [t for t, _ in close[:3]]

    msg = f"Unknown tool: {name}."
    if suggestions:
        if len(suggestions) == 1:
            msg += f" Did you mean: {suggestions[0]}?"
        else:
            msg += f" Did you mean one of: {', '.join(suggestions)}?"
    else:
        msg += f" Available tools: {', '.join(ALL_TOOL_NAMES)}"
    return msg


def _format_hints(next_tool: str, related: List[str]) -> str:
    """Format _next and _related hints for appending to tool responses."""
    lines = f"\n\n_next: {next_tool}"
    if related:
        lines += f"\n_related: {', '.join(related)}"
    return lines


def _writes_disabled_text() -> str:
    """Why write tools are disabled although a wallet is configured."""
    return f"Chain writes are disabled: {_chain_init_error}\n{_chain_init_fix}"


def _writes_disabled_recommendation() -> str:
    """health_check recommendation for disabled writes."""
    if _chain_init_fix == WALLET_KEY_INVALID_FIX:
        return (
            "Chain writes disabled: PROVENANCE_WALLET_KEY is not a valid "
            "private key. Fix it, or remove it for read-only mode"
        )
    return (
        "Chain writes disabled: no DataProvenance contract at the "
        "configured address — fix CHAIN_CONTRACT or CHAIN_NAME"
    )


def _no_write_client_text(no_wallet_text: str) -> str:
    """Error text for a write tool without a usable wallet client."""
    if _chain_init_error:
        return _writes_disabled_text()
    return no_wallet_text


def _format_error(
    message: str, retryable: bool, next_tool: Optional[str] = None
) -> str:
    """Format structured error message with retryable flag and recovery hint.

    The message often embeds exception text, so lines in it that look like
    the hints appended here are marked as quoted.
    """
    lines = defang_hint_lines(message)
    lines += f"\n\nretryable: {str(retryable).lower()}"
    if next_tool:
        lines += f"\n_next: {next_tool}"
    return lines


def _mask_rpc_text(value: Any, urls: List[str], *, all_urls: bool = False) -> str:
    """``str(value)`` with the RPC URLs in ``urls`` cut to scheme, host and port.

    Literal URLs are masked first: one without a scheme matches no URL
    pattern. With ``all_urls``, every URL is cut, not only those on an RPC
    host (used for logs).
    """
    hosts = None if all_urls else {rpc_host(u) for u in urls} - {""}
    return redact_urls(mask_literal_urls(value, urls), hosts)


def _exc_text(e: Any) -> str:
    """Exception text for a tool result: RPC URLs masked, one line, capped.

    Chain and RPC errors can carry a long body from the endpoint. URLs are
    masked before the cap, so a cut cannot leave part of a key that the
    later redaction of the whole result no longer recognizes.
    """
    return one_line(_mask_rpc_text(e, _rpc_urls()), MAX_EXCEPTION_CHARS)


def _exc_tx_hash(e: Any) -> str:
    """The transaction hash a chain exception carries, one line and capped.

    It can come from the RPC's answer to ``send_raw_transaction``. A real
    hash is 66 characters.
    """
    return one_line(getattr(e, "tx_hash", None) or "", 80)


def _exc_tx_line(e: Any) -> str:
    """A "Tx Hash:" line for a chain exception that carries one, else "".

    ChainTransactionError appends the hash to its text, but the cap in
    ``_exc_text`` can cut it off, so it is printed on its own line.
    """
    tx = _exc_tx_hash(e)
    return f"\n   Tx Hash: {tx}" if tx else ""


def _may_be_pending(e: Exception) -> bool:
    """Whether a failed write may still land: it was broadcast and then the
    endpoint dropped out or the receipt wait timed out."""
    if not getattr(e, "broadcast", False):
        return False
    from .chain.provider import is_transport_error

    if is_transport_error(e):
        return True
    cause = e
    while cause is not None:
        if type(cause).__name__ == "TimeExhausted":  # web3 receipt timeout
            return True
        cause = cause.__cause__ or cause.__context__
    return False


def _pending_tx_result(e: Exception, check_tool: str) -> CallToolResult:
    """Report a broadcast write with an unknown outcome as possibly pending."""
    msg = (
        "Transaction outcome unknown: it was sent, but the RPC failed or the "
        f"receipt did not arrive in time ({_exc_text(e)}).\n"
    )
    if getattr(e, "tx_hash", None):
        msg += f"   Tx Hash: {_exc_tx_hash(e)}\n"
    msg += (
        "\nIt may be pending or already mined. Do not send it again: check "
        f"with {check_tool} first, and retry only if the change is not there."
    )
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=_format_error(msg, retryable=False, next_tool=check_tool),
            )
        ],
        isError=True,
    )


def _restrict_failure_text(e: Exception, transform_tx: str, original: str) -> str:
    """
    record_transform succeeded but restricting the original did not: say what
    is recorded and what the agent should do next, so it does not re-run the
    transform.
    """
    restrict_call = (
        f"   To restrict the original, the owner wallet calls "
        f"setDataStatus(0x{original}, 1) on the DataProvenance contract "
        f'(Python: ChainClient.set_status("{original}", 1)). This server has '
        "no tool for it.\n"
    )
    recorded = (
        f"   The transformation IS recorded on-chain (tx {transform_tx}). "
        "Do not re-run record_transform: only the status change "
    )
    if _may_be_pending(e):
        tx = _exc_tx_hash(e) or "unknown"
        return (
            "\n\n   ⚠️  Restricting the original was sent, outcome unknown: it "
            f"may still be pending (tx {tx}).\n" + recorded + "is unconfirmed.\n"
            "   Do not send the status change again: check the original's Status "
            "with get_provenance first, and only if it is not RESTRICTED:\n"
            + restrict_call
            + "\nretryable: false"
        )
    return (
        f"\n\n   ⚠️  Transform succeeded but failed to restrict original: {_exc_text(e)}"
        + _exc_tx_line(e)
        + "\n"
        + recorded
        + "failed.\n"
        + restrict_call
        + "\nretryable: false"
    )


def _is_retryable_error(e: Exception) -> bool:
    """Determine if an exception represents a transient, retryable error."""
    from requests.exceptions import ConnectionError as ReqConnectionError, Timeout

    if isinstance(e, (ReqConnectionError, Timeout, GatewayAnswerUnreadable)):
        # An unreadable answer may be a one-off: reading again can work.
        return True
    if (
        isinstance(e, RequestException)
        and hasattr(e, "response")
        and e.response is not None
    ):
        return e.response.status_code in (408, 429, 502, 503, 504)
    if isinstance(e, RequestException):
        error_str = str(e).lower()
        return any(w in error_str for w in ("timeout", "connection", "connect"))
    return False


def _recovery_hint(e: Exception) -> Optional[str]:
    """Point at health_check only when it can explain or change the outcome.

    That is a transient failure, or any 5xx (health_check reports whether the
    Bee node can reach the Swarm network). For other permanent errors an agent
    following the hint would loop without progress.
    """
    response = getattr(e, "response", None)
    if response is not None and response.status_code == 429:
        return None  # the fix is to wait, which the error text says
    if response is not None and response.status_code >= 500:
        # Even when not retryable: health_check reports whether the gateway's
        # Bee node can reach the Swarm network, the usual cause of a 5xx.
        return "health_check"
    return "health_check" if _is_retryable_error(e) else None


def _is_ambiguous_write_error(e: Exception) -> bool:
    """Whether a write may have completed even though no answer came back.

    A read timeout or a proxy 502/504 means the request reached the gateway,
    which may have finished it (bought or topped up a batch) after the client
    stopped waiting. A connect failure means it never arrived, so it is not
    ambiguous. An error raised before the request is sent (for example
    requests refusing to serialise the body, a plain InvalidJSONError with
    no response) is not ambiguous either.
    """
    from requests.exceptions import (
        ChunkedEncodingError,
        ConnectTimeout,
        ContentDecodingError,
        JSONDecodeError,
        ReadTimeout,
    )
    from requests.exceptions import ConnectionError as ReqConnectionError

    response = getattr(e, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(
        e,
        (
            GatewayAnswerUnreadable,
            JSONDecodeError,
            ContentDecodingError,
            ChunkedEncodingError,
        ),
    ):
        # The answer arrived but its body could not be read or parsed. The
        # gateway client raises GatewayAnswerUnreadable for every such cause;
        # the requests classes it wraps are listed for callers that bypass
        # it. Ambiguous unless the status is known and is an error status.
        # Below 400 (2xx, or a final 3xx): the gateway did not refuse it.
        return not isinstance(status, int) or status < 400
    if isinstance(e, ReadTimeout):
        return True
    if isinstance(e, ReqConnectionError) and not isinstance(e, ConnectTimeout):
        # Dropped after sending ("Connection aborted", RemoteDisconnected), or
        # a 2xx whose body stalled: requests wraps that read timeout as a
        # ConnectionError("... Read timed out."), not ReadTimeout. Unlike a
        # refused or unresolvable connection, the write may have happened.
        text = str(e)
        return any(
            marker in text
            for marker in ("Connection aborted", "RemoteDisconnected", "Read timed out")
        )
    if response is None:
        return False
    # Any error that carries a 2xx answer came after the write succeeded.
    # 500 too: the gateway returns it for errors raised after the purchase call.
    return isinstance(status, int) and (200 <= status < 300 or status in (500, 502, 504))


def _as_sentence(reason: Any) -> str:
    """A reason (an exception or text) as one sentence: capital first
    letter, final period."""
    text = flatten(str(reason), MAX_EXCEPTION_CHARS).strip() or "No details"
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else text + "."


def _ambiguous_write_result(
    action: str, e: Union[Exception, str], check_tool: str, how_to_check: str = ""
) -> CallToolResult:
    """Tell the agent not to repeat a write whose outcome is unknown.

    The free tier carries no payment, so the gateway's Idempotency-Key support
    does not apply to it; a blind retry buys (or extends) a second time.
    """
    msg = (
        f"{action} outcome unknown. {_as_sentence(e)}\n\n"
        "The request reached the gateway but no usable answer came back, so "
        "it may have completed. Do not repeat it yet: call "
        f"{check_tool} first and only retry if the change is not there."
    )
    if how_to_check:
        msg += f"\n{how_to_check}"
    logger.error(msg)
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=_format_error(msg, retryable=False, next_tool=check_tool),
            )
        ],
        isError=True,
    )


def _stamp_baseline(stamp_id: str) -> str:
    """The stamp's expiry before an extension, to compare against afterwards."""
    # Best effort: nothing here may stop the extension itself.
    try:
        details = gateway_client.get_stamp_details(stamp_id)
    except Exception:
        return ""
    if not isinstance(details, dict):
        return ""
    # Only values that pass a check: a part that fails is left out.
    parts = []
    if _is_text(details.get("expectedExpiration")):
        parts.append(f"expiry {_gw(details['expectedExpiration'])}")
    ttl = _number_in(details.get("batchTTL"), 0, MAX_TTL_SECONDS)
    if ttl is not None:
        parts.append(f"TTL {ttl}s")
    return ", ".join(parts)


# A top-up is an on-chain transaction the node applies when it processes the
# event, so get_stamp_status may show the old expiry for a while. How long has
# not been measured against the gateway (#188); err on the side of waiting.
_TOP_UP_DELAY_NOTE = (
    " A top-up can take a few minutes to show: if the expiry has not moved, "
    "wait a few minutes and check again before extending again."
)


def _extension_hint(baseline: str, hours: Any) -> str:
    """How to tell from get_stamp_status whether the extension went through."""
    if baseline:
        return (
            f"Before this call the stamp had {baseline}. If get_stamp_status now "
            f"shows an expiry about {hours}h later, the extension went through."
            + _TOP_UP_DELAY_NOTE
        )
    return (
        "The stamp's state before this call could not be read. If its expiry is "
        f"about {hours}h later than you expected, the extension went through."
        + _TOP_UP_DELAY_NOTE
    )


def _format_age(seconds: float) -> str:
    """Compact age: 45s, 12m, 3h, 2d."""
    seconds = int(seconds)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def _label(label: Any) -> str:
    """A label as list_stamps prints it: the gateway client flattens it
    (``gateway_client._clean_strings``), then it is quoted."""
    from .gateway_client import MAX_GATEWAY_STRING_CHARS

    return quote(flatten(label, MAX_GATEWAY_STRING_CHARS))


# accessMode values of stamps this server can write to. It sends no wallet,
# so only shared stamps qualify, plus stamps without an access mode (gateways
# that do not report one). "pool" is gateway inventory and "owned" belongs to
# the wallet that paid for it: the gateway refuses both.
_WRITABLE_ACCESS_MODES = ("shared", None)


# unusableReason values (gateway) after which waiting does not help.
# "unreadable": the gateway could not parse the batch's TTL or depth.
_SPENT_REASONS = ("expired", "expiring_soon", "full", "invalid_depth", "unreadable")
# A "not_found" batch bought through the gateway less than this many seconds
# ago (its secondsSincePurchase) may not have reached the node yet: wait for
# it. Older, or of unknown age, it will not appear.
NOT_FOUND_GRACE_SECONDS = 180
# How list_stamps and health_check name a lasting unusableReason. Only these known values
# are printed; the gateway's text is never echoed.
_SPENT_REASON_TEXT = {
    "expired": "expired",
    "expiring_soon": "about to expire",
    "full": "full",
    "invalid_depth": "invalid depth",
    "unreadable": "unreadable on the node",
    "not_found": "not found on the node",
}
# Every lasting reason in one phrase, for the list_stamps description and
# the health_check recommendation.
_SPENT_REASONS_PHRASE = (
    ", ".join(list(_SPENT_REASON_TEXT.values())[:-1])
    + ", or "
    + list(_SPENT_REASON_TEXT.values())[-1]
)


def _recently_purchased(stamp: Dict[str, Any]) -> bool:
    """Whether the gateway reports the stamp as bought less than
    NOT_FOUND_GRACE_SECONDS ago. Only a plain non-negative int counts."""
    age = stamp.get("secondsSincePurchase")
    return (
        isinstance(age, int)
        and not isinstance(age, bool)
        and 0 <= age < NOT_FOUND_GRACE_SECONDS
    )


def _stamp_readiness(stamp: Any) -> Optional[str]:
    """Whether this server can upload to one stamp from a list_stamps answer.

    ``writable``: usable, with an access mode this server can write to.
    ``pending``: writable, not usable yet, for no lasting reason (it may
    still be propagating). ``spent``: writable, but the gateway reports a
    reason that waiting does not fix. ``pool``, ``owned`` or ``other``: an
    access mode the gateway refuses for this server, usable or not.
    None for an entry that is not an object."""
    if not isinstance(stamp, dict):
        return None
    mode = stamp.get("accessMode")
    writable = mode is None or (
        isinstance(mode, str) and mode in _WRITABLE_ACCESS_MODES
    )
    if not writable:
        return mode if mode in ("pool", "owned") else "other"
    if stamp.get("usable") is True:
        return "writable"
    reason = stamp.get("unusableReason")
    if isinstance(reason, str) and (
        reason in _SPENT_REASONS
        or (reason == "not_found" and not _recently_purchased(stamp))
    ):
        return "spent"
    # Not usable yet, for no lasting reason: it may still be propagating,
    # so wait for it rather than buy another.
    return "pending"


def _writable_stamp_counts(stamps: List[Any]) -> Dict[str, Any]:
    """Usable stamps in a list_stamps answer, split by whether this server can
    write to them, plus ``pending`` (writable stamps not usable yet, which may
    still be propagating; ``pending_ids`` holds their valid batch IDs) and
    ``blocked`` (every stamp this server cannot use, by kind; ``spent`` is a
    writable stamp the gateway reports as expired, full or about to expire).
    Entries that are not objects are not counted. Each stamp is classified
    by _stamp_readiness."""
    counts: Dict[str, Any] = {
        "writable": 0, "pending": 0, "pool": 0, "owned": 0, "other": 0
    }
    counts["pending_ids"] = []
    # Every stamp this server cannot use, usable or not, by kind.
    blocked = {"pool": 0, "owned": 0, "other": 0, "spent": 0}
    counts["blocked"] = blocked
    for stamp in stamps:
        kind = _stamp_readiness(stamp)
        if kind is None:
            continue
        if kind in ("pool", "owned", "other"):
            blocked[kind] += 1
            if stamp.get("usable") is True:
                counts[kind] += 1
        elif kind == "spent":
            blocked["spent"] += 1
        elif kind == "pending":
            counts["pending"] += 1
            batch_id = _clean_gateway_batch_id(stamp.get("batchID"))
            if batch_id is not None:
                counts["pending_ids"].append(batch_id)
        else:
            counts["writable"] += 1
    return counts


def _no_writable_stamp_text(blocked: Dict[str, int]) -> str:
    """health_check recommendation when no stamp is writable, naming only the
    kinds of stamps actually listed."""
    parts = []
    pool, owned, other = blocked["pool"], blocked["owned"], blocked["other"]
    if pool:
        parts.append(
            f"{pool} pool stamp{'s are' if pool != 1 else ' is'} gateway inventory"
        )
    if owned:
        parts.append(
            f"{owned} owned stamp{'s belong' if owned != 1 else ' belongs'} to the "
            f"wallet that paid for {'them' if owned != 1 else 'it'}"
        )
    if other:
        parts.append(
            f"{other} stamp{'s have' if other != 1 else ' has'} an access mode "
            "this server cannot use"
        )
    spent = blocked["spent"]
    if spent:
        parts.append(
            f"{spent} stamp{'s' if spent != 1 else ''} cannot become usable "
            f"({_SPENT_REASONS_PHRASE})"
        )
    detail = f": {'; '.join(parts)}." if parts else "."
    return f"No stamp this server can write to{detail} Purchase a stamp with purchase_stamp"


# Batch IDs listed in the pending-stamp recommendation, at most.
MAX_PENDING_IDS_SHOWN = 3


def _pending_stamp_text(count: int, batch_ids: List[str]) -> str:
    """health_check recommendation for writable stamps that are not usable
    yet. Pairs with _next: check_stamp_health."""
    ids = ""
    if batch_ids:
        shown = ", ".join(batch_ids[:MAX_PENDING_IDS_SHOWN])
        more = len(batch_ids) - MAX_PENDING_IDS_SHOWN
        ids = f" (batch ID{'s' if len(batch_ids) != 1 else ''}: {shown}" + (
            f", and {more} more)" if more > 0 else ")"
        )
    return (
        f"{count} stamp{'s' if count != 1 else ''} this server can write to "
        f"{'are' if count != 1 else 'is'} not usable yet, probably still "
        f"propagating{ids}. Poll check_stamp_health every 10 to 15 seconds: a "
        "new stamp takes up to 2 minutes. Do not buy another while it propagates"
    )


def _new_batch_hint(label: Optional[str], depth: Optional[int]) -> str:
    """How to spot, in list_stamps output, a batch the unanswered purchase made.

    Only fields list_stamps prints. Free-tier purchases are shared batches,
    so access mode does not identify them. Every purchase carries a label
    (generated when the caller gave none), so the label always identifies it.
    """
    traits = []
    if label:
        traits.append(f"'Label: {_label(label)}'")
    if depth is not None:
        traits.append(f"'Depth: {depth}'")
    traits.append("a batch ID that was not listed before this call")
    return (
        "In list_stamps, a batch from this call shows "
        + ", ".join(traits)
        + "; where the gateway reports it, 'Purchased' is a few minutes ago. "
        "It may still be propagating."
    )


def _gateway_error_text(message: str, e: Exception) -> str:
    """Append what the caller can do when the error says more than its status."""
    response = getattr(e, "response", None)
    if response is not None and response.status_code == 429:
        wait = response.headers.get("Retry-After")
        message += "\n\nRate limited by the gateway"
        message += f": wait {_gw(wait, 40)} seconds before retrying." if wait else "."
        message += " The free tier allows a few write requests per minute."
    if _is_insufficient_funds_error(e):
        message += (
            "\n\nOperator action required: the gateway's wallet cannot pay for "
            "this operation. Retrying or calling other tools will not help — "
            "report it to the gateway operator."
        )
    return message


# Decimals of the x402 assets this server can name, keyed by (network,
# lower-case asset address). These are the USDC contracts the gateway offers
# (Base mainnet and Base Sepolia), under x402 v1 and v2 network names. Any
# other asset is shown in atomic units only: its decimals are not guessed.
_KNOWN_X402_ASSETS: Dict[Tuple[str, str], Tuple[str, int]] = {}
for _net in ("base", "eip155:8453"):
    _KNOWN_X402_ASSETS[(_net, "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913")] = (
        "USDC",
        6,
    )
for _net in ("base-sepolia", "eip155:84532"):
    _KNOWN_X402_ASSETS[(_net, "0x036cbd53842c5426634e7929541ec2318f3dcf7e")] = (
        "USDC",
        6,
    )

# Longest x402 amount shown, in digits.
_MAX_AMOUNT_DIGITS = 40
# Payment options listed in a Payment Required message, at most.
_MAX_PAYMENT_OPTIONS = 5


def _format_x402_amount(amount: Any, network: Any, asset: Any) -> str:
    """An x402 amount (atomic units of the asset) for display.

    Converted to a decimal value only for an asset in _KNOWN_X402_ASSETS.
    Anything that is not a plain ASCII digit string is shown as given.
    """
    if not isinstance(amount, (str, int)) or isinstance(amount, bool):
        return _gw(amount, 80) or "not given"
    try:
        raw = str(amount).strip()
    except ValueError:  # an int too large to convert to text
        return "a number too long to show"
    # isascii(): str.isdigit() accepts characters such as "²" that int()
    # and Decimal() reject.
    if not (raw.isascii() and raw.isdigit()):
        return _gw(raw, 80)
    digits = raw.lstrip("0") or "0"
    if len(digits) > _MAX_AMOUNT_DIGITS:
        return (
            f"{_gw(digits, _MAX_AMOUNT_DIGITS)} atomic units "
            f"(a {len(digits)}-digit number, cut short)"
        )
    known = _KNOWN_X402_ASSETS.get((str(network).lower(), str(asset).lower()))
    if known is None:
        return f"{digits} atomic units of the asset (decimals unknown)"
    symbol, decimals = known
    with localcontext() as ctx:
        ctx.prec = _MAX_AMOUNT_DIGITS + 10  # exact for every amount shown
        value = Decimal(digits).scaleb(-decimals).normalize()
    return f"{digits} atomic units ({value:f} {symbol})"


def _format_payment_options(accepts: List[Dict[str, Any]]) -> str:
    """The ``accepts`` entries of an x402 payment request, for display."""
    msg = "\n📋 Payment details (from the gateway's x402 payment request):\n"
    for i, option in enumerate(accepts[:_MAX_PAYMENT_OPTIONS]):
        if len(accepts) > 1:
            msg += f"   Option {i + 1}:\n"
        network = option.get("network")
        asset = option.get("asset")
        amount = option.get("maxAmountRequired", option.get("amount"))
        pay_to = _gw(option.get("payTo"), 100) or "not given"
        msg += f"   Amount: {_format_x402_amount(amount, network, asset)}\n"
        msg += f"   Asset: {_gw(asset, 100) or 'not given'}\n"
        msg += f"   Network: {_gw(network, 60) or 'not given'}\n"
        msg += f"   Recipient (payTo): {pay_to}\n"
        if option.get("scheme"):
            msg += f"   Scheme: {_gw(option.get('scheme'), 40)}\n"
        if option.get("description"):
            msg += f"   Description: {_gw(option.get('description'), 200)}\n"
    if len(accepts) > _MAX_PAYMENT_OPTIONS:
        extra = len(accepts) - _MAX_PAYMENT_OPTIONS
        msg += f"   ... and {extra} more options not shown\n"
    return msg


def _format_free_tier(free_tier: Any) -> str:
    """The gateway's ``freeTier`` block of a 402, if it says one is available."""
    if not isinstance(free_tier, dict) or not free_tier.get("available"):
        return ""
    msg = "\n🆓 The gateway reports that it offers a free tier"
    remaining = free_tier.get("requestsRemaining")
    limit = free_tier.get("requestsLimit")
    window = free_tier.get("windowSeconds")
    if remaining is not None and limit is not None:
        msg += f": {_gw(remaining, 20)} of {_gw(limit, 20)} requests left"
        if window is not None:
            msg += f" in a {_gw(window, 20)}-second window"
    return msg + ".\n"


def _free_tier_option() -> str:
    """The free-tier next step, which depends on this server's PAYMENT_MODE.

    Settings only accept "free" or "" (normalised), so those are the cases.
    """
    if settings.payment_mode == "free":
        return (
            "Ask the gateway operator whether the free tier covers this "
            "operation: this server already requests the free tier "
            "(PAYMENT_MODE=free), and the gateway still asks for payment."
        )
    return (
        "Use the gateway's free tier, if it offers one: this server is not "
        "requesting it (PAYMENT_MODE is empty). Set PAYMENT_MODE=free "
        "and restart this server."
    )


def format_payment_required(payment_info: Dict[str, Any], action: str) -> str:
    """Explain an HTTP 402 Payment Required from the gateway.

    ``payment_info`` comes from ``gateway_client.parse_payment_required``.
    Every value in it is gateway text, so it is printed through ``_gw``.
    """
    msg = (
        "💳 Payment required: the gateway requires payment for this operation "
        f"({action}) and refused the request.\n"
    )
    if payment_info.get("error"):
        msg += f"   Gateway message: {_gw(payment_info['error'], 300)}\n"
    code = payment_info.get("code") or ""
    # The gateway's error envelope sets HTTP_<status> when an endpoint gave no
    # code of its own, as every standard x402 402 does: that adds nothing.
    if code and not re.fullmatch(r"HTTP_\d+", code):
        msg += f"   Gateway error code: {_gw(code, 60)}\n"

    accepts = [a for a in payment_info.get("accepts") or [] if isinstance(a, dict)]
    if accepts:
        msg += _format_payment_options(accepts)
    else:
        msg += "\n📋 The gateway sent no payment details.\n"
        if payment_info.get("raw"):
            msg += f"   Response body (start): {_gw(payment_info['raw'], 300)}\n"

    msg += _format_free_tier(payment_info.get("free_tier"))
    msg += (
        "\nPaid x402 access is not supported by this server yet, so it cannot "
        "pay for this request.\n"
        "\nOptions:\n"
        "   1. Ask the gateway operator which access options exist for this "
        "operation, for example whether its free tier covers it.\n"
        "   2. Use a different gateway that does not require x402 payment or that "
        "offers a free tier: set SWARM_GATEWAY_URL and restart this server.\n"
        f"   3. {_free_tier_option()}\n"
        "\nRetrying without one of these changes will not help."
    )
    return msg


def _payment_required_result(e: PaymentRequiredError, action: str) -> CallToolResult:
    """A tool error for an HTTP 402 Payment Required from the gateway."""
    text = format_payment_required(e.payment_info, action)
    logger.error(f"{action}: gateway requires payment (HTTP 402)")
    return CallToolResult(
        content=[TextContent(type="text", text=_format_error(text, retryable=False))],
        isError=True,
    )


def _bee_advisories(gateway_response: Dict[str, Any]) -> List[str]:
    """The gateway's advisory ``bee_node.warnings`` (few peers, chain lag)."""
    bee = gateway_response.get("bee_node")
    if not isinstance(bee, dict):
        return []
    warnings = bee.get("warnings")
    if not isinstance(warnings, list):
        return []
    return [w for w in warnings if isinstance(w, str)]


# connected_peers above this is not a real count.
MAX_BEE_PEERS = 10**6


def _bee_network_status(gateway_response: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Summarise whether the gateway's Bee node can reach the Swarm network.

    Gateway liveness says nothing about this: a node with no peers serves
    its own local data and accepts uploads that never propagate.

    Returns:
        None if the gateway does not report the node's network state, otherwise
        a dict with ``ok`` (bool), ``summary`` (str), ``problems`` (list of
        str) and ``advisories`` (list of str).

    ``bee_node.warnings`` are advisory (few peers, chain lag) and the gateway
    keeps ``healthy: true`` for them, so they are reported without changing
    ``ok``.
    """
    bee = gateway_response.get("bee_node")
    if not isinstance(bee, dict):
        return None
    # Only values of the right type count, for the decision and the summary.
    # A bee_node with none of them counts as not reported.
    peers = _number_in(bee.get("connected_peers"), 0, MAX_BEE_PEERS)
    availability = bee.get("network_availability")
    if not isinstance(availability, str) or not availability.strip():
        availability = None
    flags = [bee.get(k) for k in ("healthy", "warming_up")]
    if peers is None and availability is None and not any(
        isinstance(flag, bool) for flag in flags
    ):
        return None

    problems = []
    if bee.get("healthy") is False:
        problems.append("gateway reports the Bee node unhealthy")
    if availability is not None and availability != "Available":
        problems.append(f"network availability is {_gw(availability)}")
    if peers == 0:
        problems.append("Bee node has no connected peers")
    if bee.get("warming_up") is True:
        problems.append("Bee node is still warming up")
    advisories = _bee_advisories(gateway_response)

    parts = []
    if peers is not None:
        parts.append(f"{_gw(peers)} peers")
    if availability:
        parts.append(f"availability {_gw(availability)}")
    reachability = bee.get("reachability")
    if isinstance(reachability, str) and reachability:
        parts.append(f"reachability {_gw(reachability)}")
    return {
        "ok": not problems,
        "summary": ", ".join(parts) or "no details reported",
        "problems": problems,
        "advisories": advisories,
    }


# Chain balance thresholds and funding URLs
_MIN_BALANCE_WEI = 10**14  # 0.0001 ETH — cannot reliably transact
_LOW_BALANCE_WEI = 10**15  # 0.001 ETH — may run out soon
_FAUCET_URLS = {
    "base-sepolia": "https://www.alchemy.com/faucets/base-sepolia",
}
_BRIDGE_URL = "https://bridge.base.org"


def _is_insufficient_funds_error(e: Exception) -> bool:
    """Check if an exception indicates insufficient wallet funds."""
    err_str = str(e).lower()
    return "insufficient funds" in err_str or "insufficient balance" in err_str


def _format_insufficient_funds_error(tool_name: str, chain_client_ref) -> str:
    """Format a user-friendly error when a transaction fails due to empty wallet."""
    msg = f"⚠️  {tool_name} failed — wallet has insufficient funds for gas.\n"
    if chain_client_ref:
        msg += f"\n   Wallet: {chain_client_ref.address}"
        chain = chain_client_ref.chain
    else:
        chain = settings.chain_name
    msg += _funding_hint(chain, chain_client_ref)
    msg += "\n\n   Run chain_balance to check your current balance."
    return msg


def _funding_hint(chain: str, client=None) -> str:
    """Where to get ETH, decided by the effective chain id, not the name."""
    if not _is_testnet(chain, client):
        return f"\n   Bridge ETH to Base: {_BRIDGE_URL}"
    faucet = _FAUCET_URLS.get(chain)
    if faucet:
        return f"\n   Fund with testnet ETH: {faucet}"
    return "\n   Fund the account from your local test node (testnet ETH)"


def _chain_id_lines(provider) -> str:
    """
    Chain id and a testnet/mainnet label, each line starting "\n   ".

    The label comes from the effective chain id, not from CHAIN_NAME.
    """
    text = f"\n   Chain ID: {provider.chain_id}"
    if provider.is_testnet:
        return text + "\n   ⚠️  Testnet — tokens have no value, state may be reset"
    return text + "\n   ⚠️  Mainnet — transactions spend real ETH"


def _rpc_urls() -> List[str]:
    """Every configured and every in-use RPC URL."""
    urls = [settings.chain_rpc_url] + (_parse_rpc_fallbacks() or [])
    for client in (chain_client, _read_only_client, _unverified_write_client):
        provider = getattr(client, "_provider", None)
        if provider is None:
            continue
        urls.append(getattr(provider, "rpc_url", None))
        others = getattr(provider, "_rpc_urls", None)
        if isinstance(others, (list, tuple)):
            urls.extend(others)
    return [u for u in urls if isinstance(u, str) and u]


def _redact_rpc_urls(result: CallToolResult) -> CallToolResult:
    """Cut RPC URLs in a tool result down to scheme, host and port.

    Chain errors often quote the full RPC URL, whose path or query can hold
    an API key. Other URLs (faucet, explorer, gateway) are left as they are.
    """
    urls = _rpc_urls()
    if not urls or not isinstance(result, CallToolResult):
        return result
    for item in result.content:
        if isinstance(item, TextContent):
            item.text = _mask_rpc_text(item.text, urls)
    return result


def _mask_rpc_url(url: str) -> str:
    """Hostname of an RPC URL for display; never the path, query or key."""
    return rpc_host(url) or "<RPC URL hidden>"


def _format_funding_guidance(address: str, balance_wei: int, chain: str) -> str:
    """Format actionable funding guidance based on wallet balance.

    Returns empty string if balance is healthy, a warning if low,
    or a critical message with funding instructions if insufficient.
    """
    if balance_wei >= _LOW_BALANCE_WEI:
        return ""

    balance_eth = balance_wei / 10**18

    if balance_wei < _MIN_BALANCE_WEI:
        msg = f"\n\n⚠️  CRITICAL: Wallet balance too low for transactions"
        msg += f"\n   Balance: {balance_eth:.6f} ETH (minimum ~0.0001 ETH needed)"
        msg += f"\n   Wallet: {address}"
    else:
        msg = f"\n\n⚠️  WARNING: Wallet balance is low"
        msg += f"\n   Balance: {balance_eth:.6f} ETH (may run out soon)"
        msg += f"\n   Wallet: {address}"

    msg += _funding_hint(chain)

    return msg


# MCP tool annotations (hints for clients deciding whether to ask the user).
# Tools that spend funds (gas from the chain wallet, or the gateway operator's
# BZZ) are marked destructive and non-idempotent: the spend is irreversible
# and a repeated call can spend again, so clients should not auto-approve or
# auto-retry them.
_READ = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
)
_SPEND = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True
)
TOOL_ANNOTATIONS: Dict[str, ToolAnnotations] = {
    # Gateway
    "purchase_stamp": _SPEND,
    "extend_stamp": _SPEND,
    "upload_data": ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
    "get_stamp_status": _READ,
    "list_stamps": _READ,
    "download_data": _READ,
    "check_stamp_health": _READ,
    "get_wallet_info": _READ,
    "get_notary_info": _READ,
    "health_check": _READ,
    # Chain
    "chain_balance": _READ,
    "chain_health": _READ,
    "verify_hash": _READ,
    "get_provenance": _READ,
    "get_provenance_chain": _READ,
    "lookup_by_storage_ref": _READ,
    "anchor_hash": _SPEND,
    "record_transform": _SPEND,
    "record_merge_transform": _SPEND,
    "set_storage_ref": _SPEND,
}

# Chain tools that send transactions; they take a `confirm` argument.
SPENDING_CHAIN_TOOLS = (
    "anchor_hash",
    "record_transform",
    "record_merge_transform",
    "set_storage_ref",
)

CONFIRM_PROPERTY = {
    "type": "boolean",
    "description": (
        "Set to true, together with quote_id, to send the transaction. When "
        "confirmation is required (mainnet by default) a call without a "
        "matching quote sends nothing and returns a quote of the exact action "
        "with a quote_id. Get the user's approval before confirming."
    ),
    "default": False,
}

QUOTE_ID_PROPERTY = {
    "type": "string",
    "description": (
        "The quote_id returned by the quote for exactly these arguments. "
        "Single use, valid for 10 minutes."
    ),
}

QUOTE_TTL_SECONDS = 600

# Per-process key: quote ids from another process (or a restart) never match.
_QUOTE_KEY = secrets.token_bytes(32)
# Issued, unused quotes: quote_id -> (tool, canonical action, expiry).
_issued_quotes: Dict[str, tuple] = {}
MAX_ISSUED_QUOTES = 1000  # oldest are evicted beyond this

# Action fields holding hashes or addresses: compared case-insensitively.
_HEX_FIELDS = (
    "swarm_hash",
    "storage_ref",
    "owner",
    "original_hash",
    "new_hash",
    "source_hashes",
    "data_hash",
)


def _now() -> float:
    return time.monotonic()


def _canonical(action: Dict[str, Any]) -> str:
    """Canonical JSON of an action: hex fields lower-cased, empty values
    treated as absent."""
    norm = {}
    for key, value in action.items():
        if value in ("", None, []):
            value = None
        elif key in _HEX_FIELDS:
            if isinstance(value, list):
                value = [v.lower() if isinstance(v, str) else v for v in value]
            elif isinstance(value, str):
                value = value.lower()
        norm[key] = value
    return json.dumps(norm, sort_keys=True, separators=(",", ":"))


def _quote_digest(tool: str, canonical: str, expires: float) -> str:
    payload = f"{tool}\n{canonical}\n{expires!r}".encode()
    return hmac.new(_QUOTE_KEY, payload, hashlib.sha256).hexdigest()[:32]


def _issue_quote_id(tool: str, action: Dict[str, Any]) -> str:
    now = _now()
    for qid in [q for q, v in _issued_quotes.items() if v[2] <= now]:
        del _issued_quotes[qid]  # prune expired
    canonical = _canonical(action)
    expires = now + QUOTE_TTL_SECONDS
    qid = _quote_digest(tool, canonical, expires)
    _issued_quotes[qid] = (tool, canonical, expires)
    while len(_issued_quotes) > MAX_ISSUED_QUOTES:
        del _issued_quotes[next(iter(_issued_quotes))]  # oldest first
    return qid


def _redeem_quote_id(tool: str, action: Dict[str, Any], quote_id: Any) -> bool:
    """True, and the quote is used up, when quote_id was issued for exactly
    this tool and action and has not expired."""
    if not isinstance(quote_id, str):
        return False
    entry = _issued_quotes.get(quote_id)
    if entry is None:
        return False
    q_tool, q_canonical, expires = entry
    if expires <= _now():
        del _issued_quotes[quote_id]
        return False
    canonical = _canonical(action)
    if q_tool != tool or q_canonical != canonical:
        return False
    if not hmac.compare_digest(quote_id, _quote_digest(tool, canonical, expires)):
        return False
    del _issued_quotes[quote_id]  # single use
    return True


def _confirm_required() -> bool:
    """Whether gas-spending tools need a confirmed quote: CHAIN_REQUIRE_CONFIRM,
    or by default only on mainnet (any chain id not a known testnet)."""
    if settings.chain_require_confirm is not None:
        return settings.chain_require_confirm
    return bool(chain_client and chain_client.spend.mainnet)


async def _spend_confirmation(
    tool: str,
    arguments: Dict[str, Any],
    action: Dict[str, Any],
    tx_count: int = 1,
) -> Optional[CallToolResult]:
    """
    Two-step confirmation for gas-spending tools.

    ``action`` holds the validated arguments that decide what is sent. A call
    proceeds only with confirm: true and a quote_id issued for exactly that
    action, unexpired and unused. Otherwise it sends nothing and returns a
    quote of the action with a new quote_id.

    Returns None when the call may proceed.
    """
    if not _confirm_required():
        return None
    if arguments.get("confirm") is True and _redeem_quote_id(
        tool, action, arguments.get("quote_id")
    ):
        return None

    quote = await asyncio.to_thread(chain_client.fee_quote)
    quote_id = _issue_quote_id(tool, action)
    text = "Confirmation required: nothing was sent.\n\n"
    if arguments.get("confirm") is True:
        text += (
            "The quote_id is missing, expired, already used, or was issued "
            "for different arguments. A new quote follows.\n\n"
        )
    text += f"   Action: {tool}\n"
    for key in sorted(action):
        text += f"   {key}: {json.dumps(action[key])}\n"
    text += (
        f"\n   Sends {tx_count} transaction(s) paying gas from wallet "
        f"{chain_client.address} on {chain_client.chain}.\n"
        f"   Fee cap: {quote['max_fee_gwei']:g} gwei per gas\n"
    )
    if "fee_error" in quote:
        text += f"   Current fee: {_exc_text(quote['fee_error'])}\n"
    else:
        text += f"   Max fee per gas now: {quote['max_fee_per_gas_gwei']:.6g} gwei\n"
    if "max_cost_per_tx_eth" in quote:
        text += f"   Max cost per transaction: {quote['max_cost_per_tx_eth']:.6g} ETH\n"
    text += (
        f"   Session budget left: {quote['remaining_eth']:.6g} of "
        f"{quote['budget_eth']:g} ETH\n"
        f"   Transactions left this session: "
        f"{quote['max_txs'] - quote['txs_sent']} of {quote['max_txs']}\n\n"
        f"   quote_id: {quote_id}\n"
        f"   (single use, valid for {QUOTE_TTL_SECONDS // 60} minutes, only for "
        f"exactly the arguments above)\n\n"
        f"To proceed, get the user's approval for this action, then call {tool} "
        f"again with the same arguments plus confirm: true and quote_id: "
        f"{quote_id}."
    )
    return CallToolResult(content=[TextContent(type="text", text=text)])


def create_server() -> Server:
    """Create and configure the MCP server."""
    server = Server(
        settings.mcp_server_name,
        version=settings.mcp_server_version,
        instructions=MCP_INSTRUCTIONS,
    )

    @server.list_tools()
    async def list_tools() -> List[Tool]:
        """List available tools for stamp management."""
        tools = [
            Tool(
                name="purchase_stamp",
                description="Purchase a new Swarm postage stamp. Returns a 64-character hex batch ID. A stamp can be reused for multiple uploads until its capacity or TTL is exhausted. May be ready instantly (pool) or take up to 2 minutes (fresh mint). Use check_stamp_health to confirm readiness.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "duration_hours": {
                            "type": "integer",
                            "description": f"Duration in hours — controls TTL (time-to-live). Minimum 24 hours (default: {settings.default_stamp_duration_hours})",
                            "default": settings.default_stamp_duration_hours,
                            "minimum": 24,
                            "maximum": settings.max_stamp_duration_hours,
                        },
                        "size": {
                            "type": "string",
                            "enum": ["small", "medium", "large"],
                            "description": f"Size preset — controls storage capacity. small (~35KB), medium (~500MB), large (~6GB). Capacities are approximate (default: {settings.default_stamp_size})",
                            "default": settings.default_stamp_size,
                        },
                        "depth": {
                            "type": "integer",
                            "description": "Advanced: explicit depth override (17–22). When provided, sent instead of size. Only use if you need a specific depth value.",
                            "minimum": 17,
                            "maximum": 22,
                        },
                        "label": {
                            "type": "string",
                            "description": "Optional human-readable label for easier stamp identification. If omitted, a unique label (mcp-<hex>) is generated so the batch can be found in list_stamps. The result shows it as requested, not as stored: list_stamps shows the label the gateway stored.",
                            "maxLength": 100,
                        },
                    },
                    "required": [],
                },
            ),
            Tool(
                name="get_stamp_status",
                description="Get detailed information about a specific stamp including TTL, expiration time, utilization, and usability status. Use this to check whether a stamp is still valid before calling upload_data.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "stamp_id": {
                            "type": "string",
                            "description": "The 64-character hexadecimal batch ID of the stamp (without 0x prefix). Example: a1b2c3d4e5f6789abcdef0123456789abcdef0123456789abcdef0123456789a",
                            "pattern": "^[a-fA-F0-9]{64}$",
                        }
                    },
                    "required": ["stamp_id"],
                },
            ),
            Tool(
                name="list_stamps",
                description=f"List postage stamps available on the gateway. Each stamp includes its access mode: shared (public, any gateway user can upload to it), owned (registered to the wallet that paid for it) or pool (gateway inventory). This server sends no wallet, so the gateway refuses its uploads to owned and pool stamps: use a shared stamp. Each stamp's Status says whether this server can upload to it and, if not, why: not usable by this server (pool, owned or another access mode), not usable yet (may still be propagating: poll check_stamp_health, do not buy another) or cannot become usable ({_SPENT_REASONS_PHRASE}). Also shows expiration.",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            Tool(
                name="extend_stamp",
                description="Extend an existing stamp's TTL (time-to-live) by adding duration. This extends the expiration date but does NOT increase storage capacity. Changes take up to 2 minutes to propagate through the blockchain.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "stamp_id": {
                            "type": "string",
                            "description": "The 64-character hexadecimal batch ID of the stamp to extend (without 0x prefix). Example: a1b2c3d4e5f6789abcdef0123456789abcdef0123456789abcdef0123456789a",
                            "pattern": "^[a-fA-F0-9]{64}$",
                        },
                        "duration_hours": {
                            "type": "integer",
                            "description": "Additional duration in hours to add to the stamp's TTL. Minimum 24 hours.",
                            "minimum": 24,
                            "maximum": settings.max_stamp_duration_hours,
                        },
                    },
                    "required": ["stamp_id", "duration_hours"],
                },
            ),
            Tool(
                name="upload_data",
                description="Upload data to the Swarm network using a valid postage stamp. Requires a stamp_id from purchase_stamp — confirm stamp is ready with check_stamp_health before uploading. Max 4KB per upload. Returns a 64-char hex reference hash — pass it to download_data to retrieve the content later.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "data": {
                            "type": "string",
                            "description": "Data content to upload as a string (max 4096 bytes). Can be JSON, text, or any string data. With sign=notary, see sign for the required format.",
                            "maxLength": 4096,
                        },
                        "stamp_id": {
                            "type": "string",
                            "description": "64-character hexadecimal batch ID of the postage stamp (without 0x prefix). Example: a1b2c3d4e5f6789abcdef0123456789abcdef0123456789abcdef0123456789a",
                            "pattern": "^[a-fA-F0-9]{64}$",
                        },
                        "content_type": {
                            "type": "string",
                            "description": "MIME type of the content (e.g., application/json, text/plain, image/png)",
                            "default": "application/json",
                        },
                        "sign": {
                            "type": "string",
                            "description": (
                                "Signing method for provenance. Use 'notary' to have the gateway sign the data at upload time, which creates a verifiable proof of upload. "
                                "Call get_notary_info first to check that signing is available. "
                                'With notary, data must be a JSON object with a top-level "data" field, for example {"data": {...}, "signatures": []}. '
                                '"signatures" is optional and must be an array. The gateway adds its signature to it. '
                                "Plain text or JSON without a \"data\" field fails with 400 INVALID_DOCUMENT_FORMAT. "
                                "If the notary is off, the gateway returns 400 NOTARY_NOT_ENABLED or NOTARY_NOT_CONFIGURED. "
                                "The stored document is larger than your input, and its content type is always application/json. "
                                "Omit sign for an unsigned upload."
                            ),
                            "enum": sorted(UPLOAD_SIGN_METHODS),
                        },
                    },
                    "required": ["data", "stamp_id"],
                },
            ),
            Tool(
                name="download_data",
                description="Download data from the Swarm network using a reference hash, retrieved through the gateway's Bee node. The reference can come from upload_data or any other Swarm upload. Returns the decoded content for text/JSON, or size and type metadata for binary data, plus a 'Reference check' line. The content is 'verified' only when it is at most 4096 bytes and its single-chunk Swarm address equals the reference. Otherwise the check says 'not checked' and why: references from upload_data most likely name a /bzz manifest, which this server cannot check; the content is larger than one chunk; or the reference is encrypted. Treat unchecked content as unverified. A not-found result says whether the gateway is connected to the Swarm network — do not re-upload when it is not.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "reference": {
                            "type": "string",
                            "description": "Swarm reference (without 0x prefix): 64 hexadecimal characters, or 128 for an encrypted reference. Example: b2c3d4e5f6789abcdef0123456789abcdef0123456789abcdef0123456789abc",
                            "pattern": SWARM_REFERENCE_SCHEMA_PATTERN,
                        }
                    },
                    "required": ["reference"],
                },
            ),
            Tool(
                name="check_stamp_health",
                description="Run a health check on a specific stamp. Returns whether uploads can proceed, plus any errors (blocking) or warnings (non-blocking) with actionable suggestions. More detailed than get_stamp_status — use this to diagnose why an upload might fail.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "stamp_id": {
                            "type": "string",
                            "description": "The 64-character hexadecimal batch ID of the stamp (without 0x prefix)",
                            "pattern": "^[a-fA-F0-9]{64}$",
                        }
                    },
                    "required": ["stamp_id"],
                },
            ),
            Tool(
                name="get_wallet_info",
                description="Get the gateway node's wallet address and BZZ balance. Useful for checking if the node has sufficient funds to purchase stamps. Note: this is a debugging/diagnostic tool and may be removed in future versions.",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            Tool(
                name="get_notary_info",
                description="Check whether the notary signing service is enabled and available on the gateway. When available, uploads can be cryptographically signed for provenance verification.",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            Tool(
                name="health_check",
                description="Check gateway and Swarm network connectivity status. Returns gateway URL, response time, and connection status. Call this first to verify the gateway is reachable before purchasing stamps or uploading data.",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
        ]

        # Conditionally add chain tools when chain is enabled
        if CHAIN_AVAILABLE and settings.chain_enabled:
            tools.extend(
                [
                    Tool(
                        name="chain_balance",
                        description="Check the on-chain wallet ETH balance used for provenance anchoring transactions. Returns wallet address, balance, chain info, and actionable funding guidance when balance is low. No parameters required.",
                        inputSchema={
                            "type": "object",
                            "properties": {},
                            "required": [],
                        },
                    ),
                    Tool(
                        name="chain_health",
                        description="Test blockchain RPC connectivity for on-chain provenance. Returns connection status, chain name, chain ID, latest block number, RPC response time, and contract address. Does not require a wallet key. Call this to verify chain connectivity before anchoring operations.",
                        inputSchema={
                            "type": "object",
                            "properties": {},
                            "required": [],
                        },
                    ),
                    Tool(
                        name="anchor_hash",
                        description="Register a Swarm reference hash on the blockchain, creating an immutable provenance record with owner, timestamp, and data type. Costs gas — requires a funded wallet (check with chain_balance). If the hash is already registered to the expected owner (owner, or the wallet), it returns the existing record without error. If another address owns it, the result is an error. Do not use this on hashes that will be the new_hash in record_transform — record_transform auto-registers them.",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "swarm_hash": {
                                    "type": "string",
                                    "description": "64-character hexadecimal Swarm reference hash to anchor on-chain (without 0x prefix)",
                                    "pattern": "^[a-fA-F0-9]{64}$",
                                },
                                "data_type": {
                                    "type": "string",
                                    "description": "Data type/category for the on-chain record (default: 'swarm-provenance')",
                                    "default": "swarm-provenance",
                                    "maxLength": 64,
                                },
                                "owner": {
                                    "type": "string",
                                    "description": "Ethereum address to register as the data owner. If omitted, the wallet address is used. Use this to register data on behalf of another address (requires delegate authorization).",
                                    "pattern": "^0x[a-fA-F0-9]{40}$",
                                },
                                "storage_ref": {
                                    "type": "string",
                                    "description": "Optional 64-character hex storage reference (e.g. Swarm hash) to link bidirectionally with the data hash. Enables reverse lookup via lookup_by_storage_ref.",
                                    "pattern": "^[a-fA-F0-9]{64}$",
                                },
                            },
                            "required": ["swarm_hash"],
                        },
                    ),
                    Tool(
                        name="verify_hash",
                        description="Check whether a Swarm reference hash is registered on the blockchain. Returns verified status with basic provenance info (owner, timestamp, data type) if found. Pass expected_owner to fail when the record belongs to another address. Read-only — no gas or wallet key required.",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "swarm_hash": {
                                    "type": "string",
                                    "description": "64-character hexadecimal Swarm reference hash to verify (without 0x prefix)",
                                    "pattern": "^[a-fA-F0-9]{64}$",
                                },
                                "expected_owner": {
                                    "type": "string",
                                    "description": "Optional Ethereum address the record must belong to. If the hash is registered to a different owner, the result is an error that shows both addresses.",
                                    "pattern": "^0x[a-fA-F0-9]{40}$",
                                },
                            },
                            "required": ["swarm_hash"],
                        },
                    ),
                    Tool(
                        name="get_provenance",
                        description="Retrieve the full on-chain provenance record for a Swarm reference hash. Returns owner, registration timestamp, data type, status, transformations, and accessors. Read-only — no gas or wallet key required.",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "swarm_hash": {
                                    "type": "string",
                                    "description": "64-character hexadecimal Swarm reference hash to look up (without 0x prefix)",
                                    "pattern": "^[a-fA-F0-9]{64}$",
                                },
                            },
                            "required": ["swarm_hash"],
                        },
                    ),
                    Tool(
                        name="record_transform",
                        description="Record a data transformation on-chain, linking the original data to its transformed version. Creates a verifiable lineage trail. The original hash must already be anchored. The new_hash must NOT be previously anchored — record_transform registers it automatically. Optionally restricts access to the original data after transformation. Costs gas.",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "original_hash": {
                                    "type": "string",
                                    "description": "64-character hex Swarm reference of the original data (must be already anchored)",
                                    "pattern": "^[a-fA-F0-9]{64}$",
                                },
                                "new_hash": {
                                    "type": "string",
                                    "description": "64-character hex Swarm reference of the transformed data",
                                    "pattern": "^[a-fA-F0-9]{64}$",
                                },
                                "description": {
                                    "type": "string",
                                    "description": "Description of the transformation (e.g., 'Anonymized PII', 'Filtered for region EU'). Max 256 characters.",
                                    "maxLength": 256,
                                },
                                "restrict_original": {
                                    "type": "boolean",
                                    "description": "If true, set the original data status to RESTRICTED after recording the transformation. Useful when the original contains sensitive data that should no longer be accessed directly.",
                                    "default": False,
                                },
                            },
                            "required": ["original_hash", "new_hash"],
                        },
                    ),
                    Tool(
                        name="record_merge_transform",
                        description="Record an N-to-1 merge transformation on-chain, linking multiple source hashes to a single merged result. All source hashes must be already anchored. The new_hash must NOT be previously anchored — record_merge_transform registers it automatically. Costs gas. Requires v2 contract.",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "source_hashes": {
                                    "type": "array",
                                    "items": {
                                        "type": "string",
                                        "pattern": "^[a-fA-F0-9]{64}$",
                                    },
                                    "minItems": 2,
                                    "maxItems": 50,
                                    "description": "Array of 64-character hex Swarm reference hashes of the source data (2-50 items, all must be already anchored)",
                                },
                                "new_hash": {
                                    "type": "string",
                                    "description": "64-character hex Swarm reference of the merged data",
                                    "pattern": "^[a-fA-F0-9]{64}$",
                                },
                                "description": {
                                    "type": "string",
                                    "description": "Description of the merge transformation (e.g., 'Merged datasets A and B'). Max 256 characters.",
                                    "maxLength": 256,
                                },
                                "new_data_type": {
                                    "type": "string",
                                    "description": "Data type for the merged result (default: 'merged'). Max 64 characters.",
                                    "default": "merged",
                                    "maxLength": 64,
                                },
                            },
                            "required": ["source_hashes", "new_hash"],
                        },
                    ),
                    Tool(
                        name="get_provenance_chain",
                        description="Follow the transformation lineage for a Swarm hash. Walks both directions — from any node it finds parents (originals) and children (derived versions). Can be called on a leaf, middle, or root hash. Each node lists its parent hashes, and each edge is shown as 'parent_hash -> child_hash'. If a node could not be read, max_depth stopped the walk, or the event index could not be built (links then come from per-node queries that cover only recent blocks and no merges), the output starts with an INCOMPLETE banner that names those hashes, gives the reason and ends with a Retry line. Read-only, no gas or wallet key required.",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "swarm_hash": {
                                    "type": "string",
                                    "description": "64-character hex Swarm reference hash to trace lineage for (without 0x prefix)",
                                    "pattern": "^[a-fA-F0-9]{64}$",
                                },
                                "max_depth": {
                                    "type": "integer",
                                    "description": "Maximum depth to traverse (default: 10). Prevents infinite loops in circular references.",
                                    "default": 10,
                                    "minimum": 1,
                                    "maximum": 50,
                                },
                            },
                            "required": ["swarm_hash"],
                        },
                    ),
                    Tool(
                        name="set_storage_ref",
                        description="Attach a Swarm storage reference to an existing on-chain record. Set-once: cannot be changed after first write. Owner-only. Use this after record_transform to link the transformed data's storage location. Costs gas.",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "data_hash": {
                                    "type": "string",
                                    "description": "64-character hex data hash of the existing on-chain record (without 0x prefix)",
                                    "pattern": "^[a-fA-F0-9]{64}$",
                                },
                                "storage_ref": {
                                    "type": "string",
                                    "description": "64-character hex storage reference to link (e.g. Swarm reference hash, without 0x prefix)",
                                    "pattern": "^[a-fA-F0-9]{64}$",
                                },
                            },
                            "required": ["data_hash", "storage_ref"],
                        },
                    ),
                    Tool(
                        name="lookup_by_storage_ref",
                        description="Reverse lookup: find the on-chain provenance record by its Swarm storage reference. Returns the linked data hash and full provenance record if found. Read-only — no gas or wallet key required.",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "storage_ref": {
                                    "type": "string",
                                    "description": "64-character hex storage reference to look up (e.g. Swarm reference hash, without 0x prefix)",
                                    "pattern": "^[a-fA-F0-9]{64}$",
                                },
                            },
                            "required": ["storage_ref"],
                        },
                    ),
                ]
            )

        for tool in tools:
            tool.annotations = TOOL_ANNOTATIONS[tool.name]
            if tool.name in SPENDING_CHAIN_TOOLS:
                tool.inputSchema["properties"]["confirm"] = CONFIRM_PROPERTY
                tool.inputSchema["properties"]["quote_id"] = QUOTE_ID_PROPERTY
        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: Dict[str, Any]) -> CallToolResult:
        """Handle tool calls; RPC URLs in the result show the host only."""
        result = await _run_tool(name, arguments)
        if name == "download_data":
            # Fenced, capped Swarm content (#162), never chain output.
            return result
        return _redact_rpc_urls(result)

    async def _run_tool(name: str, arguments: Dict[str, Any]) -> CallToolResult:
        """Run the tool's handler; report an escaped exception as an error."""
        try:
            if name == "purchase_stamp":
                return await handle_purchase_stamp(arguments)
            elif name == "get_stamp_status":
                return await handle_get_stamp_status(arguments)
            elif name == "list_stamps":
                return await handle_list_stamps(arguments)
            elif name == "extend_stamp":
                return await handle_extend_stamp(arguments)
            elif name == "upload_data":
                return await handle_upload_data(arguments)
            elif name == "download_data":
                return await handle_download_data(arguments)
            elif name == "check_stamp_health":
                return await handle_check_stamp_health(arguments)
            elif name == "get_wallet_info":
                return await handle_get_wallet_info(arguments)
            elif name == "get_notary_info":
                return await handle_get_notary_info(arguments)
            elif name == "health_check":
                return await handle_health_check(arguments)
            # Chain tools
            elif name == "chain_balance":
                return await handle_chain_balance(arguments)
            elif name == "chain_health":
                return await handle_chain_health(arguments)
            elif name == "anchor_hash":
                return await handle_anchor_hash(arguments)
            elif name == "verify_hash":
                return await handle_verify_hash(arguments)
            elif name == "get_provenance":
                return await handle_get_provenance(arguments)
            elif name == "record_transform":
                return await handle_record_transform(arguments)
            elif name == "record_merge_transform":
                return await handle_record_merge_transform(arguments)
            elif name == "get_provenance_chain":
                return await handle_get_provenance_chain(arguments)
            elif name == "set_storage_ref":
                return await handle_set_storage_ref(arguments)
            elif name == "lookup_by_storage_ref":
                return await handle_lookup_by_storage_ref(arguments)
            else:
                return CallToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=_format_error(
                                _suggest_tool_name(name), retryable=False
                            ),
                        )
                    ],
                    isError=True,
                )
        except Exception as e:
            # The traceback as text, so it is redacted too: its frames and
            # last line can quote the RPC URL, also one without a scheme.
            urls = _rpc_urls()
            trace = traceback.format_exc().rstrip()
            logger.error(
                "Error in tool %s: %s\n%s",
                name,
                _mask_rpc_text(e, urls, all_urls=True),
                _mask_rpc_text(trace, urls, all_urls=True),
            )
            retryable = _is_retryable_error(e)
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=_format_error(
                            f"Error executing {name}: {_exc_text(e)}"
                            + _exc_tx_line(e),
                            retryable=retryable,
                            next_tool=_recovery_hint(e),
                        ),
                    )
                ],
                isError=True,
            )

    # --- MCP Prompts (workflow templates for agents) ---

    @server.list_prompts()
    async def list_prompts() -> List[Prompt]:
        """List available workflow prompts."""
        return [
            Prompt(
                name="provenance-upload",
                description="Upload data with provenance to Swarm. Guides through health check, stamp selection/purchase, upload, and a download to confirm the stored content.",
                arguments=[
                    PromptArgument(
                        name="data",
                        description="The data content to upload (text, JSON, etc.)",
                        required=True,
                    ),
                    PromptArgument(
                        name="content_type",
                        description="MIME type (default: application/json)",
                        required=False,
                    ),
                ],
            ),
            Prompt(
                name="provenance-verify",
                description="Download existing provenance data from Swarm, inspect it, and report what the reference check covered.",
                arguments=[
                    PromptArgument(
                        name="reference",
                        description="Swarm reference of the data to download and inspect",
                        required=True,
                    ),
                ],
            ),
            Prompt(
                name="stamp-management",
                description="Review and manage stamp inventory — list stamps, check health, extend or purchase as needed.",
                arguments=[],
            ),
            Prompt(
                name="provenance-chain-workflow",
                description="End-to-end on-chain provenance: store data on Swarm, anchor on-chain, and optionally record a transformation.",
                arguments=[
                    PromptArgument(
                        name="data",
                        description="The data content to store and anchor",
                        required=True,
                    ),
                    PromptArgument(
                        name="transform_description",
                        description="If provided, continues the workflow to record a transformation with this description",
                        required=False,
                    ),
                ],
            ),
        ]

    @server.get_prompt()
    async def get_prompt(
        name: str, arguments: Optional[Dict[str, str]] = None
    ) -> GetPromptResult:
        """Return workflow prompt with step-by-step instructions."""
        args = arguments or {}

        if name == "provenance-upload":
            data_desc = args.get("data", "<your data here>")
            content_type = args.get("content_type", "application/json")
            return GetPromptResult(
                description="Upload data with provenance to Swarm",
                messages=[
                    PromptMessage(
                        role="user",
                        content=TextContent(
                            type="text",
                            text=(
                                f"Upload this data to Swarm with provenance:\n\n"
                                f"Data: {data_desc}\n"
                                f"Content-Type: {content_type}\n\n"
                                f"Follow these steps:\n"
                                f"1. Call health_check to verify the gateway is reachable\n"
                                f"2. Call purchase_stamp (size 'small' for small data) to get a stamp, "
                                f"or reuse a stamp that list_stamps marks 'Usable' (this server "
                                f"cannot upload to 'owned' or 'pool' stamps).\n"
                                f"3. Poll check_stamp_health every 15 seconds until ready (up to 2 minutes)\n"
                                f"4. Call upload_data with the data and your stamp_id\n"
                                f"5. Call download_data with the returned reference and check that the content "
                                f"is the data you uploaded. Its 'Reference check' line usually says 'not checked' "
                                f"for upload_data references, because they most likely name a /bzz manifest\n"
                                f"6. Report the reference hash — this is the permanent Swarm address"
                            ),
                        ),
                    )
                ],
            )

        elif name == "provenance-verify":
            reference = args.get("reference", "<reference hash>")
            return GetPromptResult(
                description="Inspect existing provenance data from Swarm",
                messages=[
                    PromptMessage(
                        role="user",
                        content=TextContent(
                            type="text",
                            text=(
                                f"Inspect this provenance data from Swarm:\n\n"
                                f"Reference: {reference}\n\n"
                                f"Steps:\n"
                                f"1. Call download_data with the reference hash\n"
                                f"2. Read the 'Reference check' line. 'verified' means the bytes are "
                                f"the single Swarm chunk the reference names. 'not checked' means "
                                f"nothing proves the bytes belong to the reference; give the reason. "
                                f"Even when it says 'verified', check that the content is the document "
                                f"you expected: a verified chunk proves only that the bytes are the chunk "
                                f"the reference names\n"
                                f"3. Inspect the returned content: structure, fields, and whether it is well formed\n"
                                f"4. Report the content type, size, key fields, and the reference check result. "
                                f"Do not call the content cryptographically verified unless the check says 'verified'"
                            ),
                        ),
                    )
                ],
            )

        elif name == "stamp-management":
            return GetPromptResult(
                description="Review and manage stamp inventory",
                messages=[
                    PromptMessage(
                        role="user",
                        content=TextContent(
                            type="text",
                            text=(
                                "Review my Swarm stamp inventory and recommend actions:\n\n"
                                "list_stamps shows public stamps (accessMode 'shared'), which any gateway user can upload to, and can also show 'owned' and 'pool' stamps. This server sends no wallet, so the gateway refuses its uploads to 'owned' stamps (registered to the wallet that paid for them) and 'pool' stamps (gateway inventory). Only 'shared' stamps and stamps without an access mode are usable here. For production use with predictable capacity, recommend running your own Bee node or gateway.\n\n"
                                "Steps:\n"
                                "1. Call list_stamps to see all stamps. Its Status field says whether this server can upload to each stamp, and why not\n"
                                "2. For stamps this server can use, check if usable, utilization, and TTL\n"
                                "3. Call check_stamp_health on any stamp that looks concerning\n"
                                "4. Recommend actions:\n"
                                "   - Stamps with high utilization → purchase a new one\n"
                                "   - Stamps with low TTL → extend_stamp to add time\n"
                                "   - Stamps marked 'Not usable yet' → poll check_stamp_health; do not buy another while they propagate\n"
                                "   - No usable stamps and none pending → purchase_stamp\n"
                                "5. Summarize the inventory status and recommended actions"
                            ),
                        ),
                    )
                ],
            )

        elif name == "provenance-chain-workflow":
            data_desc = args.get("data", "<your data here>")
            transform_desc = args.get("transform_description")

            steps = (
                f"Store data on Swarm with on-chain provenance:\n\n"
                f"Data: {data_desc}\n\n"
                f"Steps:\n"
                f"1. Call health_check — verify gateway and chain connectivity\n"
                f"2. Call chain_balance — confirm wallet has ETH for gas\n"
                f"3. Call purchase_stamp (size 'small' for small data) to get a stamp, "
                f"or reuse a stamp that list_stamps marks 'Usable' (this server "
                f"cannot upload to 'owned' or 'pool' stamps).\n"
                f"4. Poll check_stamp_health every 15 seconds until can_upload is true (up to 2 minutes)\n"
                f"5. Call upload_data with the data and your stamp_id — save the reference hash\n"
                f"6. Call anchor_hash with the reference hash to register it on-chain. "
                f"Optionally pass storage_ref (the Swarm reference from step 5) for bidirectional lookup.\n"
                f"7. Call verify_hash to confirm the on-chain registration succeeded"
            )

            if transform_desc:
                steps += (
                    f"\n\nNow record a transformation ({transform_desc}):\n"
                    f"8. Call upload_data with the transformed data — save the new reference hash\n"
                    f"9. CRITICAL: Do NOT call anchor_hash on the new hash. "
                    f"record_transform registers it automatically.\n"
                    f"10. Call record_transform with original_hash (from step 5), "
                    f'new_hash (from step 8), and description: "{transform_desc}"\n'
                    f"11. Optionally call set_storage_ref to link the new hash to its Swarm reference (from step 8)\n"
                    f"12. Call get_provenance_chain on the original hash to verify the lineage. "
                    f"If the output starts with INCOMPLETE, the lineage is partial: "
                    f"follow its Retry line before you rely on it"
                )

            return GetPromptResult(
                description="End-to-end on-chain provenance workflow",
                messages=[
                    PromptMessage(
                        role="user",
                        content=TextContent(type="text", text=steps),
                    )
                ],
            )

        else:
            raise ValueError(f"Unknown prompt: {name}")

    # --- MCP Resources (on-demand knowledge for agents) ---

    @server.list_resources()
    async def list_resources() -> list[Resource]:
        """List available resources."""
        return [
            Resource(
                uri="provenance://skills",
                name="Provenance Skills Guide",
                description="Concepts, workflows, critical rules, and diagrams for Swarm data provenance",
                mimeType="text/markdown",
            ),
        ]

    @server.read_resource()
    async def read_resource(uri) -> list[ReadResourceContents]:
        """Return resource content by URI."""
        if str(uri) == "provenance://skills":
            return [
                ReadResourceContents(content=_SKILLS_CONTENT, mime_type="text/markdown")
            ]
        raise ValueError(f"Unknown resource: {uri}")

    return server


async def handle_purchase_stamp(arguments: Dict[str, Any]) -> CallToolResult:
    """Handle stamp purchase requests."""
    label, depth = None, None
    try:
        duration_hours = arguments.get(
            "duration_hours", settings.default_stamp_duration_hours
        )
        size = arguments.get("size", settings.default_stamp_size)
        depth = arguments.get("depth")
        # Always label the batch: if the answer to this call is lost, the
        # label is how the agent finds the batch again instead of buying twice.
        label = arguments.get("label") or f"mcp-{secrets.token_hex(4)}"

        # Validate inputs
        validate_stamp_duration_hours(duration_hours)
        if depth is not None:
            validate_stamp_depth(depth)
        else:
            validate_stamp_size(size)

        if label and len(label) > 100:
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=_format_error(
                            "Error: Label cannot exceed 100 characters",
                            retryable=False,
                            next_tool="purchase_stamp",
                        ),
                    )
                ],
                isError=True,
            )

        result = gateway_client.purchase_stamp(
            duration_hours, size=size, depth=depth, label=label
        )

        # A 2xx whose answer cannot be read: the purchase may have happened.
        if not isinstance(result, dict):
            return _ambiguous_write_result(
                "Stamp purchase",
                _NOT_EXPECTED_FORMAT,
                "list_stamps",
                _new_batch_hint(label, depth),
            )

        # A 2xx without a valid batch ID: the purchase may still have
        # happened, so the agent must look before it buys again.
        batch_id = result.get("batchID")
        if not _is_gateway_batch_id(batch_id):
            reason = (
                "the gateway's answer has no batch ID"
                if batch_id is None
                else "the batch ID in the gateway's answer is not a "
                "64-character hexadecimal string"
            )
            return _ambiguous_write_result(
                "Stamp purchase", reason, "list_stamps", _new_batch_hint(label, depth)
            )
        # 64 hexadecimal characters, as the tool description says: no 0x.
        batch_id = _clean_gateway_batch_id(batch_id)

        response_text = f"🎉 Stamp purchased successfully!\n\n"
        response_text += f"📋 Your Stamp Details:\n"
        response_text += f"   Batch ID: `{batch_id}`\n"
        response_text += f"   Duration: {duration_hours} hours\n"
        if depth is not None:
            response_text += f"   Depth: {depth}\n"
        else:
            response_text += f"   Size: {size}\n"
        # The gateway's answer does not carry the label, and older gateways
        # dropped it without saying so: show the requested one as such.
        stored_label = result.get("label")
        if isinstance(stored_label, str) and stored_label:
            response_text += f"   Label: {_label(stored_label)}\n"
        elif label:
            response_text += (
                f"   Label requested: {_label(label)} (not confirmed by the "
                "gateway; list_stamps shows the label it stored)\n"
            )
        response_text += f"\n✅ Stamp ID: `{batch_id}`\n"

        propagation_status = result.get("propagationStatus")
        estimated_ready = _gw_text(result.get("estimatedReadyAt"))
        has_estimate = _is_text(result.get("estimatedReadyAt"))
        if propagation_status == "ready":
            response_text += "⏱️  Stamp is ready immediately (from the gateway pool).\n"
        elif propagation_status == "propagating" and has_estimate:
            response_text += f"⏱️  Stamp is propagating — estimated ready at {estimated_ready}.\n"
            response_text += (
                "   → Use check_stamp_health to confirm it's ready before uploading."
            )
        else:
            response_text += "⏱️  Your stamp may be ready immediately (from the gateway pool) or may need\n"
            response_text += "   up to 2 minutes to propagate on the blockchain.\n"
            response_text += (
                "   → Use check_stamp_health to confirm it's ready before uploading."
            )
        response_text += _format_hints(
            "check_stamp_health", ["get_stamp_status", "upload_data", "list_stamps"]
        )

        return CallToolResult(content=[TextContent(type="text", text=response_text)])

    except PaymentRequiredError as e:
        return _payment_required_result(e, "purchase_stamp")
    except RequestException as e:
        if _is_ambiguous_write_error(e):
            return _ambiguous_write_result(
                "Stamp purchase", e, "list_stamps", _new_batch_hint(label, depth)
            )
        error_msg = _gateway_error_text(f"Failed to purchase stamp: {str(e)}", e)
        logger.error(error_msg)
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        error_msg,
                        retryable=_is_retryable_error(e),
                        next_tool=_recovery_hint(e),
                    ),
                )
            ],
            isError=True,
        )
    # After RequestException on purpose: requests' JSONDecodeError is also
    # a ValueError and must reach _is_ambiguous_write_error.
    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger.error(error_msg)
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        error_msg, retryable=False, next_tool="purchase_stamp"
                    ),
                )
            ],
            isError=True,
        )


# A batch TTL above this (about 3,000 years) is printed as the gateway sent
# it, not converted.
MAX_TTL_SECONDS = 10**11
# get_stamp_status prints a numeric amount or block number only up to these.
MAX_STAMP_AMOUNT = 10**40
MAX_BLOCK_NUMBER = 10**12


def _ttl_text(ttl: Union[int, float]) -> str:
    """A validated TTL in seconds, with the days it is."""
    return f"{ttl:,} seconds ({ttl/86400:.1f} days)"


def _format_stamp_utilization(details: Dict[str, Any]) -> str:
    """Stamp utilization as a percentage.

    Bee's ``utilization`` is the number of filled slots in the fullest
    bucket, not a percentage. Each bucket holds 2^(depth - bucketDepth) slots.
    """

    def _is_int(v: Any) -> bool:
        return isinstance(v, int) and not isinstance(v, bool)

    percent = _number_in(details.get("utilizationPercent"), 0, 100)

    raw = details.get("utilization")
    count = None
    if _is_int(raw):
        count = raw
    elif isinstance(raw, float) and math.isfinite(raw) and raw.is_integer():
        count = int(raw)

    depth = details.get("depth")
    bucket_depth = details.get("bucketDepth")
    slots = None
    if _is_int(depth) and _is_int(bucket_depth) and 0 <= bucket_depth < depth <= 255:
        slots = 2 ** (depth - bucket_depth)

    if count is not None and slots is not None and 0 <= count <= slots:
        # The gateway's utilizationPercent wins when it disagrees with the count.
        if percent is None:
            percent = count / slots * 100
        return f"{percent:.1f}% ({count} of {slots} slots in the fullest bucket)"

    detail = None
    if raw is not None:
        # Not validated against the bucket size, so cap it like any gateway value.
        shown = _gw(count if count is not None else raw, 40)
        detail = f"filled slots in the fullest bucket: {shown}"
    if percent is not None:
        return f"{percent:.1f}% ({detail})" if detail else f"{percent:.1f}%"
    if detail:
        return f"not available ({detail})"
    return "N/A"


async def handle_get_stamp_status(arguments: Dict[str, Any]) -> CallToolResult:
    """Handle stamp status requests."""
    try:
        stamp_id = arguments.get("stamp_id")
        if not stamp_id:
            raise ValueError("Stamp ID is required")

        # Validate and clean stamp ID
        clean_stamp_id = validate_and_clean_stamp_id(stamp_id)

        result = gateway_client.get_stamp_details(clean_stamp_id)

        response_text = f"Stamp Details for {clean_stamp_id}:\n"
        if not isinstance(result, dict):
            response_text += (
                f"The stamp's state is unknown: {_NOT_EXPECTED_FORMAT}. "
                "Check it in list_stamps.\n"
            )
            response_text += _format_hints("list_stamps", ["check_stamp_health"])
            return CallToolResult(
                content=[TextContent(type="text", text=response_text)]
            )
        # Each gateway value is printed only after a check (#242). A value
        # that fails it is N/A, as a missing one is, never its repr (#270).
        # The amount may also be text, as Bee sends it.
        amount = _number_in(result.get("amount"), 0, MAX_STAMP_AMOUNT)
        if amount is None:
            amount = _gw_text(result.get("amount"), 40) or "N/A"
        response_text += f"Amount: {amount}\n"
        for name, key in (("Depth", "depth"), ("Bucket Depth", "bucketDepth")):
            depth = _plain_depth(result.get(key))
            response_text += f"{name}: {'N/A' if depth is None else depth}\n"
        block = _number_in(result.get("blockNumber"), 0, MAX_BLOCK_NUMBER)
        response_text += f"Block Number: {'N/A' if block is None else block}\n"

        # Enhanced TTL information
        ttl = _number_in(result.get("batchTTL"), 0, MAX_TTL_SECONDS)
        shown = "N/A" if ttl is None else _ttl_text(ttl)
        response_text += f"Batch TTL: {shown}\n"

        expiry = result.get("expectedExpiration")
        expiry = _gw(expiry) if isinstance(expiry, str) else "N/A"
        response_text += f"Expected Expiration: {expiry}\n"

        # Enhanced usability information
        usable = result.get("usable")
        response_text += f"Usable: {_flag_text(usable)}"
        if usable is False:
            response_text += " ⚠️  (Cannot be used for uploads)"
        elif usable is True:
            response_text += " ✅ (Ready for uploads)"
        response_text += "\n"

        response_text += f"Utilization: {_format_stamp_utilization(result)}\n"

        response_text += f"Immutable: {_flag_text(result.get('immutableFlag'))}\n"
        response_text += f"Local: {_flag_text(result.get('local'))}\n"

        if _is_text(result.get("label")):
            response_text += f"Label: {quote(result['label'])}\n"
            response_text += f"{QUOTED_NOTE}\n"

        # Propagation timing (from gateway, when available)
        propagation_status = result.get("propagationStatus")
        seconds_since = result.get("secondsSincePurchase")
        estimated_ready = _gw_text(result.get("estimatedReadyAt"))
        has_estimate = _is_text(result.get("estimatedReadyAt"))

        if isinstance(propagation_status, str) and propagation_status:
            response_text += f"Propagation: {_gw(propagation_status)}"
            # Left out when it fails validation, as when it is missing.
            age = _stamp_age(seconds_since)
            if age is not None:
                response_text += f" ({age}s since purchase)"
            if has_estimate and propagation_status == "propagating":
                response_text += f" — ready at {estimated_ready}"
            response_text += "\n"

        # Contextual hints based on usability
        if usable is True:
            response_text += _format_hints(
                "upload_data", ["extend_stamp", "check_stamp_health"]
            )
        else:
            response_text += _format_hints(
                "purchase_stamp", ["extend_stamp", "list_stamps"]
            )

        return CallToolResult(content=[TextContent(type="text", text=response_text)])

    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger.error(error_msg)
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        error_msg, retryable=False, next_tool="list_stamps"
                    ),
                )
            ],
            isError=True,
        )
    except RequestException as e:
        error_msg = f"Failed to get stamp status: {str(e)}"
        logger.error(error_msg)
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        error_msg,
                        retryable=_is_retryable_error(e),
                        next_tool=_recovery_hint(e),
                    ),
                )
            ],
            isError=True,
        )


# list_stamps status for each _stamp_readiness kind.
_STAMP_STATUS = {
    "writable": "✅ Usable",
    "pending": "⏳ Not usable yet (may still be propagating)",
    "pool": "⛔ Not usable by this server (pool stamp: gateway inventory)",
    "owned": (
        "⛔ Not usable by this server (owned stamp: registered to the wallet "
        "that paid for it)"
    ),
    "other": "⛔ Not usable by this server (access mode it cannot use)",
}
# secondsSincePurchase above this (ten years) is not printed.
MAX_STAMP_AGE_SECONDS = 10 * 365 * 86400


def _stamp_status(stamp: Dict[str, Any], kind: str) -> str:
    """The Status field list_stamps prints for one stamp."""
    if kind == "spent":
        reason = _SPENT_REASON_TEXT.get(stamp.get("unusableReason"), "")
        return f"❌ Cannot become usable ({reason})" if reason else "❌ Cannot become usable"
    return _STAMP_STATUS[kind]


def _plain_depth(value: Any) -> Optional[int]:
    """A stamp depth, only as a plain int from 0 to 255."""
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 255:
        return value
    return None


def _stamp_age(value: Any) -> Optional[float]:
    """secondsSincePurchase, only as a finite, non-negative, sane number."""
    return _number_in(value, 0, MAX_STAMP_AGE_SECONDS)


async def handle_list_stamps(arguments: Dict[str, Any]) -> CallToolResult:
    """Handle stamp listing requests.

    Uses the readiness rule health_check uses (_stamp_readiness): a stamp is
    usable here only when it is usable and its access mode is one this
    server can write to. _next follows the same order as health_check.
    """
    try:
        result = gateway_client.list_stamps()
        stamps = result.get("stamps") if isinstance(result, dict) else None
        if not isinstance(stamps, list):
            stamps = []
        # The gateway's total_count is not printed: the count is the number
        # of entries listed here.
        stamps = [s for s in stamps if isinstance(s, dict)]
        counts = _writable_stamp_counts(stamps)

        if not stamps:
            response_text = "📭 No stamps found.\n\n💡 Use the 'purchase_stamp' tool to create your first stamp!"
        else:
            response_text = f"📋 Found {len(stamps)} stamp(s):\n\n"

            for stamp in stamps:
                batch_id = _clean_gateway_batch_id(stamp.get("batchID")) or "N/A"
                expiration = _gw_text(stamp.get("expectedExpiration"), 40) or "N/A"
                kind = _stamp_readiness(stamp)
                status = _stamp_status(stamp, kind)

                response_text += f"Batch ID: {batch_id}\n"
                detail_line = f"  Expiration: {expiration} | Status: {status}"

                # Surface access mode when provided by gateway. A rejected
                # mode is always shown, even an empty one.
                access_mode = stamp.get("accessMode")
                if access_mode or kind == "other":
                    shown = (
                        _gw(access_mode, 30) or "(empty)"
                        if isinstance(access_mode, str)
                        else "N/A"
                    )
                    detail_line += f" | Access: {shown}"

                # Surface propagation status when provided
                propagation_status = _gw_text(stamp.get("propagationStatus"), 30)
                if propagation_status:
                    detail_line += f" | Propagation: {propagation_status}"

                # What identifies a batch from an unanswered purchase
                identity = []
                if _is_text(stamp.get("label")):
                    identity.append(f"Label: {_label(stamp['label'])}")
                depth = _plain_depth(stamp.get("depth"))
                if depth is not None:
                    identity.append(f"Depth: {depth}")
                age = _stamp_age(stamp.get("secondsSincePurchase"))
                if age is not None:
                    identity.append(f"Purchased: {_format_age(age)} ago")
                if identity:
                    detail_line += "\n  " + " | ".join(identity)

                response_text += detail_line + "\n\n"

        # Public-only hint: every listed stamp is shared
        if stamps and all(s.get("accessMode") == "shared" for s in stamps):
            response_text += "\n💡 All stamps are public (shared): other gateway users can fill them. For production use with predictable capacity, run your own Bee node or gateway. This server sends no wallet, so it cannot upload to wallet-owned stamps.\n"

        # Labels of shared stamps are written by other gateway users.
        if any(_is_text(s.get("label")) for s in stamps):
            response_text += f"\n{QUOTED_NOTE}\n"

        # Contextual hints, in health_check's order. Each recommendation
        # matches its _next.
        if counts["writable"]:
            response_text += _format_hints(
                "upload_data", ["get_stamp_status", "check_stamp_health"]
            )
        elif counts["pending"]:
            response_text += (
                f"\n💡 {_pending_stamp_text(counts['pending'], counts['pending_ids'])}.\n"
            )
            response_text += _format_hints("check_stamp_health", ["get_stamp_status"])
        else:
            if stamps:
                response_text += f"\n💡 {_no_writable_stamp_text(counts['blocked'])}.\n"
            response_text += _format_hints("purchase_stamp", ["get_wallet_info"])

        return CallToolResult(content=[TextContent(type="text", text=response_text)])

    except RequestException as e:
        error_msg = f"Failed to list stamps: {str(e)}"
        logger.error(error_msg)
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        error_msg,
                        retryable=_is_retryable_error(e),
                        next_tool=_recovery_hint(e),
                    ),
                )
            ],
            isError=True,
        )


async def handle_extend_stamp(arguments: Dict[str, Any]) -> CallToolResult:
    """Handle stamp extension requests."""
    baseline = ""
    try:
        stamp_id = arguments.get("stamp_id")
        duration_hours = arguments.get("duration_hours")

        if not stamp_id:
            raise ValueError("Stamp ID is required")
        if not duration_hours:
            raise ValueError("duration_hours is required")

        # Validate inputs
        clean_stamp_id = validate_and_clean_stamp_id(stamp_id)
        validate_stamp_duration_hours(duration_hours)

        # Read before writing: if the answer is lost, this is what the agent
        # compares against to tell whether the extension happened.
        baseline = _stamp_baseline(clean_stamp_id)
        result = gateway_client.extend_stamp(clean_stamp_id, duration_hours)
        # A 2xx whose answer cannot be read: the extension may have happened.
        if not isinstance(result, dict):
            return _ambiguous_write_result(
                "Stamp extension",
                _NOT_EXPECTED_FORMAT,
                "get_stamp_status",
                _extension_hint(baseline, duration_hours),
            )

        response_text = f"✅ Stamp extended successfully!\n\n"
        response_text += f"📋 Extension Details:\n"
        batch_id = result.get("batchID")
        # Printed as 64 hexadecimal characters, without 0x.
        batch_id = _clean_gateway_batch_id(batch_id) or "N/A"
        response_text += f"   Batch ID: `{batch_id}`\n"
        response_text += f"   Additional Duration: {duration_hours} hours\n"
        message = result.get("message")
        status_text = one_line(message) if isinstance(message, str) else "Extended"
        response_text += f"   Status: {status_text}\n\n"
        response_text += f"⏱️  Important: Extension takes up to 2 minutes to propagate through the blockchain.\n"
        response_text += f"🔍 Check stamp status again in about 2 minutes to see the new expiration time."
        response_text += _format_hints("check_stamp_health", ["get_stamp_status"])

        return CallToolResult(content=[TextContent(type="text", text=response_text)])

    except PaymentRequiredError as e:
        return _payment_required_result(e, "extend_stamp")
    except RequestException as e:
        if _is_ambiguous_write_error(e):
            return _ambiguous_write_result(
                "Stamp extension",
                e,
                "get_stamp_status",
                _extension_hint(baseline, arguments.get("duration_hours")),
            )
        error_msg = _gateway_error_text(f"Failed to extend stamp: {str(e)}", e)
        logger.error(error_msg)
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        error_msg,
                        retryable=_is_retryable_error(e),
                        next_tool=_recovery_hint(e),
                    ),
                )
            ],
            isError=True,
        )
    # After RequestException on purpose: requests' JSONDecodeError is also
    # a ValueError and must reach _is_ambiguous_write_error.
    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger.error(error_msg)
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        error_msg, retryable=False, next_tool="list_stamps"
                    ),
                )
            ],
            isError=True,
        )


def _is_unreadable_body(e: Exception) -> bool:
    """requests' own error for a body it cannot parse (mocked clients)."""
    from requests.exceptions import JSONDecodeError

    return isinstance(e, JSONDecodeError)


def _upload_outcome_unknown(
    cause: Union[Exception, str], sign: Optional[str]
) -> CallToolResult:
    """An upload whose answer cannot be used: it may have completed.

    The reference is unknown and cannot be looked up, so the result says
    so and does not tell the agent to upload again: a second upload uses
    stamp capacity again, and a signed one makes a second signed document."""
    if isinstance(cause, (GatewayAnswerUnreadable, str)):
        reason = cause
    else:
        reason = "The gateway's answer could not be read: it is not valid JSON"
    msg = (
        f"Upload outcome unknown. {_as_sentence(reason)}\n\n"
        "The request reached the gateway, so the upload may have completed, "
        "but the reference is unknown. "
    )
    if sign:
        msg += (
            "Do not upload the same data again: a signed upload creates a "
            "second signed document, and it uses stamp capacity again. "
        )
    else:
        msg += (
            "Swarm addresses content by its hash, so uploading the same data "
            "again normally returns the same reference; it uses stamp "
            "capacity again. "
        )
    msg += "Call check_stamp_health to confirm the stamp first."
    logger.error(msg)
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=_format_error(msg, retryable=False, next_tool="check_stamp_health"),
            )
        ],
        isError=True,
    )


async def handle_upload_data(arguments: Dict[str, Any]) -> CallToolResult:
    """Handle data upload requests."""
    try:
        data = arguments.get("data")
        stamp_id = arguments.get("stamp_id")

        if not data:
            raise ValueError("Data cannot be empty")
        if not stamp_id:
            raise ValueError("Stamp ID cannot be empty")
        content_type = arguments.get("content_type", "application/json")
        sign = arguments.get("sign") or None
        if sign is not None and sign not in UPLOAD_SIGN_METHODS:
            raise ValueError(
                "sign must be one of: " + ", ".join(sorted(UPLOAD_SIGN_METHODS))
            )

        # Validate inputs
        validate_data_size(data)
        clean_stamp_id = validate_and_clean_stamp_id(stamp_id)

        # First, check if the stamp exists on this gateway
        # Note: Newly purchased stamps may not be immediately available via get_stamp_details
        # so we'll try to validate but allow upload to proceed if validation fails with 404
        stamp_validation_failed = False
        validation_error_msg = ""

        try:
            stamp_details = gateway_client.get_stamp_details(clean_stamp_id)

            # Verify it's a usable stamp
            # An answer that is not an object says nothing: the upload goes
            # ahead and the gateway checks the stamp.
            if isinstance(stamp_details, dict) and not stamp_details.get(
                "usable", False
            ):
                return CallToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=_format_error(
                                f"Stamp {clean_stamp_id} is not yet usable. If recently purchased, "
                                f"it may still be propagating (up to 2 minutes). "
                                f"Use check_stamp_health to poll until ready.",
                                retryable=True,
                                next_tool="check_stamp_health",
                            ),
                        )
                    ],
                    isError=True,
                )

        except RequestException as e:
            # If we can't get stamp details, it might be a timing issue with newly purchased stamps
            if hasattr(e, "response") and e.response is not None:
                if e.response.status_code == 404:
                    # Don't immediately fail - the stamp might be newly purchased
                    # We'll let the upload attempt proceed and let the gateway handle validation
                    stamp_validation_failed = True
                    validation_error_msg = f"Could not validate stamp {clean_stamp_id} (it may be newly purchased)"
                else:
                    # Other HTTP errors should be re-raised
                    raise
            else:
                # Network errors should be re-raised
                raise

        # Proceed with upload if stamp validation passed
        try:
            result = gateway_client.upload_data(
                data, clean_stamp_id, content_type, sign=sign
            )
        except RequestException as e:
            # An unreadable 2xx (or unknown status): the upload may be done.
            if isinstance(e, GatewayAnswerUnreadable) or _is_unreadable_body(e):
                if _is_ambiguous_write_error(e):
                    return _upload_outcome_unknown(e, sign)
            raise
        reference = result.get("reference") if isinstance(result, dict) else None
        if not (
            isinstance(reference, str) and SWARM_REFERENCE_PATTERN.fullmatch(reference)
        ):
            return _upload_outcome_unknown(
                "The gateway's answer has no valid reference", sign
            )

        response_text = f"🎉 Data uploaded successfully to Swarm!\n\n"
        response_text += f"📄 Upload Details:\n"
        size = len(data.encode("utf-8"))
        if sign:
            # The gateway stores the signed document, always as JSON, so the
            # input size and content type do not describe what was stored.
            response_text += f"   Size: {size:,} bytes (before signing)\n"
            response_text += "   Content Type: application/json (signed document)\n"
        else:
            response_text += f"   Size: {size:,} bytes\n"
            response_text += f"   Content Type: {content_type}\n"
        response_text += f"   Stamp Used: `{clean_stamp_id}`\n"
        if sign:
            response_text += f"   Signing: {sign}\n"
        response_text += f"\n🔗 Retrieval Information:\n"
        response_text += f"   Reference Hash: `{reference}`\n"
        response_text += f"   💡 Copy this reference hash to download your data later using the 'download_data' tool."

        # Surface notary signature details when present
        notary_info = result.get("notary") or result.get("signature")
        if notary_info and isinstance(notary_info, dict):
            signer = notary_info.get("signer") or notary_info.get("signerAddress")
            if isinstance(signer, str) and signer:
                # A gateway field: printed on one line and capped.
                response_text += f"\n\n🔐 Notary Signature:\n   Signer: {_gw(signer)}"

        # Add validation warning if applicable
        if stamp_validation_failed:
            response_text += f"\nNote: {validation_error_msg}"

        response_text += _format_hints(
            "download_data", ["upload_data", "check_stamp_health"]
        )

        return CallToolResult(content=[TextContent(type="text", text=response_text)])

    except ValueError as e:
        error_msg = f"Upload validation error: {str(e)}"
        logger.error(error_msg)
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        error_msg, retryable=False, next_tool="check_stamp_health"
                    ),
                )
            ],
            isError=True,
        )
    except PaymentRequiredError as e:
        return _payment_required_result(e, "upload_data")
    except RequestException as e:
        error_msg = _gateway_error_text(f"Failed to upload data: {str(e)}", e)
        logger.error(error_msg)
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        error_msg,
                        retryable=_is_retryable_error(e),
                        next_tool=_recovery_hint(e),
                    ),
                )
            ],
            isError=True,
        )


def _download_not_found(reference: str) -> CallToolResult:
    """
    Explain a download 404 so the agent can tell its causes apart.

    "Not found" can mean the content is not on Swarm, or that the gateway's
    Bee node cannot reach the network. Re-uploading is wrong in the second
    case, so the gateway's network status is checked before answering.
    """
    network = None
    try:
        # The same guards as health_check: a part that is not an object
        # counts as not reported.
        health = gateway_client.health_check()
        gw_resp = health.get("gateway_response") if isinstance(health, dict) else None
        if isinstance(gw_resp, dict):
            network = _bee_network_status(gw_resp)
    except RequestException:
        pass

    msg = f"Not found: the gateway could not retrieve `{reference}`.\n\n"
    if network is not None and not network["ok"]:
        msg += (
            f"The gateway's Bee node cannot reliably reach the Swarm network "
            f"({'; '.join(network['problems'])}), so it can only return data "
            "stored on this node. The content may well exist on Swarm.\n"
            "Do not re-upload it. Operator action required; retry once "
            "health_check reports the Swarm network as connected."
        )
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(msg, retryable=True, next_tool="health_check"),
                )
            ],
            isError=True,
        )

    if network is not None:
        msg += (
            f"The gateway is connected to the Swarm network "
            f"({network['summary']}), so the reference is most likely not "
            "available on Swarm: it was never uploaded, its postage stamp "
            "expired, or the reference is wrong.\n"
        )
    else:
        msg += (
            "Possible causes: the reference was never uploaded, its postage "
            "stamp expired, the reference is wrong, or the gateway's Bee node "
            "cannot reach the Swarm network (not reported by this gateway).\n"
        )
    msg += "Check the reference. Re-upload only if you have the original content."
    return CallToolResult(
        content=[TextContent(type="text", text=_format_error(msg, retryable=False))],
        isError=True,
    )


# Anyone can append on-chain transformations for gas: list at most this many.
_MAX_LISTED = 50
# get_provenance_chain stops adding entries once its text passes this size.
_MAX_CHAIN_TEXT = 32 * 1024


def _more_note(items: Sequence[Any], indent: str = "     ") -> str:
    """Say how many entries of a capped list were left out."""
    extra = len(items) - _MAX_LISTED
    return f"\n{indent}... and {extra} more not shown" if extra > 0 else ""


def _is_chain_hash(value: Any) -> bool:
    """Whether a chain-supplied value is a 64-character hex hash string."""
    return (
        isinstance(value, str) and REFERENCE_HASH_PATTERN.fullmatch(value) is not None
    )


def _chain_hash(value: Any) -> str:
    """A chain-supplied hash for printing: as is when it is 64 hex
    characters, otherwise flattened to one capped line."""
    return value if _is_chain_hash(value) else _gw(value, 80)


def _lineage_banner(result: Any, queried_hash: str, max_depth: int) -> str:
    """The INCOMPLETE banner for a get_provenance_chain result, or "".

    It names each node that could not be read and each node the depth limit
    stopped at, and ends with a "Retry:" line. A plain list (no traversal
    status) is read from the records' own fields.
    """
    failed = getattr(result, "failed", None)
    failed = dict(failed) if isinstance(failed, dict) else {}
    for record in result:
        read_error = getattr(record, "read_error", None)
        if isinstance(read_error, str) and record.data_hash not in failed:
            failed[record.data_hash] = read_error
    unexpanded = [
        r.data_hash for r in result if getattr(r, "expanded", True) is False
    ]
    scan_note = getattr(result, "scan_note", None)
    if not isinstance(scan_note, str):
        scan_note = None
    if not failed and not unexpanded and scan_note is None:
        return ""

    summary = []
    if failed:
        n = len(failed)
        summary.append(f"{n} {'node' if n == 1 else 'nodes'} could not be read")
    if unexpanded:
        n = len(unexpanded)
        summary.append(
            f"{n} {'node' if n == 1 else 'nodes'} not expanded (max_depth reached)"
        )
    if scan_note:
        summary.append("links were read from a limited block range")
    lines = [
        "INCOMPLETE: this lineage is partial: " + "; ".join(summary) + ".",
        "   Do not treat the entries below as the full provenance.",
    ]
    if scan_note:
        lines.append(f"   {_gw(scan_note, 300)}")
    items = [f"   {_chain_hash(h)}: {_gw(label, 120)}" for h, label in failed.items()]
    items += [
        f"   {_chain_hash(h)}: not expanded (max_depth reached)" for h in unexpanded
    ]
    lines.extend(items[:_MAX_LISTED])
    more = _more_note(items, "   ")
    if more:
        lines.append(more.lstrip("\n"))

    # Retry on a failing node, else deeper from the queried hash, else (at
    # the max_depth ceiling) from a node the depth limit stopped at.
    retry = "   Retry: call get_provenance_chain with swarm_hash {}"
    if failed or scan_note:
        retry_hash = next((h for h in failed if _is_chain_hash(h)), queried_hash)
        lines.append(retry.format(retry_hash))
    elif max_depth < 50:
        retry_hash = queried_hash
        lines.append(
            f"   Retry: call get_provenance_chain with swarm_hash {retry_hash} "
            f"and a max_depth larger than {max_depth}"
        )
    else:
        retry_hash = next((h for h in unexpanded if _is_chain_hash(h)), queried_hash)
        lines.append(retry.format(retry_hash))
    if scan_note:
        lines.append(
            "   A retry may return the same result while the event index cannot "
            "be built; check chain_health."
        )
    return "\n".join(lines)


def _describe_download(reference: str, data: bytes) -> Tuple[str, Optional[str]]:
    """Present downloaded content as fenced, size-capped, untrusted data.

    Anyone can upload to Swarm, so the content may be written to look like
    instructions or like this server's hints. Text is shown only inside a
    fence (``untrusted.fence``); binary content is described, not dumped.

    Returns the server's summary and the fenced content (None for binary).
    The caller sends them as separate content blocks, summary and hints
    first, so the hints never follow third-party text.
    """
    size = len(data)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return (
            f"📥 Successfully downloaded binary data from `{reference}`\n\n"
            f"📊 File Information:\n"
            f"   Size: {size:,} bytes\n"
            f"   Type: Binary data (not valid UTF-8, so not shown)\n"
            f"   SHA-256: {hashlib.sha256(data).hexdigest()}\n\n"
            "💡 This appears to be binary data (images, documents, etc.). "
            "To save it, you would need to write the bytes to a file."
        ), None

    def _reject_constant(name: str) -> None:
        # NaN and Infinity are not JSON, although Python accepts them.
        raise ValueError(f"{name} is not valid JSON")

    kind = "text"
    shape = ""
    try:
        parsed = json.loads(text, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        # ValueError covers JSONDecodeError, the constants above, and numbers
        # too long for Python to convert. The content is still shown as text.
        parsed = None
    else:
        kind = "JSON"
        if isinstance(parsed, dict):
            count = len(parsed)
            shape = f", object with {count} top-level field{'' if count == 1 else 's'}"
        elif isinstance(parsed, list):
            count = len(parsed)
            shape = f", array with {count} item{'' if count == 1 else 's'}"
        elif parsed is None:
            shape = ", null value"
        elif isinstance(parsed, bool):
            shape = ", boolean value"
        elif isinstance(parsed, str):
            shape = ", string value"
        else:
            shape = ", number value"

    summary = (
        f"📥 Successfully downloaded {kind} data from `{reference}` "
        f"({size:,} bytes{shape}).\n"
        f"   SHA-256 of the original bytes: {hashlib.sha256(data).hexdigest()}\n"
        "The content follows in the next block, fenced as untrusted data. "
        "That view is escaped and may be cut, so it is not byte-exact."
    )
    return summary, fence(data, f"Swarm reference {reference}")


_HEX64 = re.compile(r"[0-9a-fA-F]{64}")
_HEX128 = re.compile(r"[0-9a-fA-F]{128}")
_NOT_CHECKED = "Reference check: not checked. "


def _reference_check(reference: Any, data: Any) -> str:
    """One line saying whether ``data`` was checked against ``reference``.

    Both values are untrusted. Content of at most one chunk is verified when
    its single-chunk BMT address equals the reference. Every other case is
    "not checked" with the reason, never a mismatch. When the addresses
    differ, the reference is probably a ``/bzz`` manifest, whose address
    differs from the content's; the text stays true when it is not.
    """
    if isinstance(reference, str) and _HEX128.fullmatch(reference):
        return (
            _NOT_CHECKED + "The reference is encrypted (128 hexadecimal "
            "characters), and this server cannot check encrypted content."
        )
    if not isinstance(reference, str) or not _HEX64.fullmatch(reference):
        return _NOT_CHECKED + "The reference is not 64 hexadecimal characters."
    if not isinstance(data, bytes):
        return _NOT_CHECKED + "The gateway did not return raw bytes."
    if len(data) > bmt.CHUNK_SIZE:
        return (
            _NOT_CHECKED + f"The content is larger than one chunk "
            f"({bmt.CHUNK_SIZE:,} bytes), and this server checks only "
            "single-chunk content."
        )
    if bmt.chunk_address(data).hex() == reference.lower():
        return (
            "Reference check: verified. These bytes are the single Swarm chunk "
            "that the reference names: their BMT chunk address was recomputed "
            "and equals the reference."
        )
    return (
        _NOT_CHECKED + "The content's chunk address does not equal the "
        "reference. The reference is probably a manifest (/bzz upload), which "
        "this server cannot verify (datafund/swarm_connect#499)."
    )


async def handle_download_data(arguments: Dict[str, Any]) -> CallToolResult:
    """Handle data download requests."""
    clean_reference = None
    try:
        reference = arguments.get("reference")
        if not reference:
            raise ValueError("Reference is required")

        # Validate and clean reference (64 hex, or 128 when encrypted)
        clean_reference = validate_and_clean_swarm_reference(reference)

        result_bytes = gateway_client.download_data(clean_reference)
        summary, fenced = _describe_download(clean_reference, result_bytes)
        summary += "\n" + _reference_check(clean_reference, result_bytes)
        content = [
            TextContent(
                type="text",
                text=summary + _format_hints("upload_data", ["list_stamps"]),
            )
        ]
        if fenced is not None:
            content.append(TextContent(type="text", text=fenced))
        return CallToolResult(content=content)

    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger.error(error_msg)
        return CallToolResult(
            content=[
                TextContent(type="text", text=_format_error(error_msg, retryable=False))
            ],
            isError=True,
        )
    except RequestException as e:
        response = getattr(e, "response", None)
        if response is not None and response.status_code == 404:
            return _download_not_found(clean_reference)
        error_msg = f"Failed to download data: {str(e)}"
        logger.error(error_msg)
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        error_msg,
                        retryable=_is_retryable_error(e),
                        next_tool=_recovery_hint(e),
                    ),
                )
            ],
            isError=True,
        )


_NETWORK_DEGRADED_TEXT = (
    "The gateway's Bee node cannot reliably reach the Swarm network: uploads "
    "may never propagate and only data stored on this node can be downloaded."
)


async def handle_health_check(arguments: Dict[str, Any]) -> CallToolResult:
    """Handle health check requests with adaptive status.

    Returns a comprehensive status including gateway connectivity, stamp
    availability, and actionable recommendations for the agent's next step.
    """
    ready = False
    recommendations = []
    next_tool = "list_stamps"
    network_rec = None  # index of the degraded-network recommendation

    try:
        result = gateway_client.health_check()

        status = result.get("status", "unknown")
        gateway_url = result.get("gateway_url", "N/A")
        response_time = _number_in(result.get("response_time_ms"), 0, 10**9)

        gateway_ok = status == "healthy"

        if gateway_ok:
            response_text = f"✅ Gateway operational\n\n"
            response_text += f"🌐 Gateway: {gateway_url}\n"
            if response_time is not None:
                response_text += f"⚡ Response Time: {response_time:.0f}ms\n"
        else:
            response_text = f"⚠️  Gateway issues detected\n\n"
            response_text += f"Status: {status}\n"
            response_text += f"Gateway: {gateway_url}\n"
            if response_time is not None:
                response_text += f"Response Time: {response_time:.0f}ms\n"
            recommendations.append(
                "Gateway not healthy — check connectivity or try again later"
            )
            next_tool = "health_check"

        # Advisory only: does not change readiness
        if is_plain_http_remote(settings.swarm_gateway_url):
            recommendations.append(
                "SWARM_GATEWAY_URL uses plain http to a remote host: requests and "
                "uploads are sent unencrypted. Use https unless the gateway is on "
                "a trusted network"
            )

        # Parse payment / free-tier info from gateway response
        # A part that is not an object counts as not reported.
        gw_resp = result.get("gateway_response")
        if not isinstance(gw_resp, dict):
            gw_resp = {}
        x402 = gw_resp.get("x402")
        if not isinstance(x402, dict):
            x402 = {}
        free_tier = x402.get("free_tier")
        if not isinstance(free_tier, dict):
            free_tier = {}
        # Only a sane count says free tier; any other value counts as absent.
        rate_limit = _number_in(free_tier.get("rate_limit_per_minute"), 1, 10**6)
        if rate_limit is not None:
            response_text += f"\n💰 Payment: free tier ({rate_limit} write req/min, reads unlimited)\n"
        elif x402:
            response_text += "\n💰 Payment: x402 enabled\n"

        # Swarm network reachability — gateway liveness does not imply the
        # Bee node can publish uploads or retrieve data it did not store.
        network = _bee_network_status(gw_resp) if gateway_ok else None
        if network is None:
            if gateway_ok:
                response_text += "\n🐝 Swarm network: not reported by gateway\n"
        elif network["ok"]:
            response_text += f"\n🐝 Swarm network: connected ({network['summary']})\n"
        else:
            response_text += (
                f"\n🐝 Swarm network: DEGRADED ({network['summary']})\n"
                f"   {'; '.join(network['problems'])}\n"
            )
            network_rec = len(recommendations)
            recommendations.append(
                _NETWORK_DEGRADED_TEXT + " Operator action required — retrying "
                "will not help."
            )
        advisories = _bee_advisories(gw_resp) if gateway_ok else []
        if advisories:
            # Advisory only: does not change readiness
            advisory = one_line("; ".join(advisories), 500)
            response_text += f"   ⚠️  Gateway advisory: {advisory}\n"

        # Adaptive: also check stamp availability
        stamps_info = ""
        usable_count = 0
        total_stamps = 0
        if gateway_ok:
            try:
                stamps_result = gateway_client.list_stamps()
                # The same rule as list_stamps: an answer or a stamp list
                # that is not the expected type counts as no stamps, and
                # entries that are not objects are skipped.
                stamps = (
                    stamps_result.get("stamps")
                    if isinstance(stamps_result, dict)
                    else None
                )
                if not isinstance(stamps, list):
                    stamps = []
                stamps = [s for s in stamps if isinstance(s, dict)]
                total_stamps = len(stamps)
                counts = _writable_stamp_counts(stamps)
                usable_count = counts["writable"]

                stamps_info += (
                    f"\n📋 Stamps: {usable_count} usable by this server / "
                    f"{total_stamps} local"
                )
                others = []
                if counts["pool"]:
                    others.append(f"{counts['pool']} pool")
                owned = counts["owned"]
                if owned:
                    payer = (
                        "wallets that paid for them" if owned != 1
                        else "wallet that paid for it"
                    )
                    others.append(f"{owned} owned (registered to the x402 {payer})")
                if counts["other"]:
                    others.append(f"{counts['other']} with another access mode")
                if others:
                    stamps_info += f" (usable but not by this server: {', '.join(others)})"
                stamps_info += "\n"

                if usable_count > 0 and (network is None or network["ok"]):
                    ready = True
                    next_tool = "upload_data"
                elif usable_count > 0:
                    # The degraded network blocks uploads to a usable stamp.
                    # One recommendation that agrees with _next.
                    recommendations[network_rec] = (
                        _NETWORK_DEGRADED_TEXT + " The gateway operator must fix "
                        "the Bee node's network connection. Call health_check again "
                        "after that fix, and do not upload until health_check "
                        "shows the Swarm network as connected."
                    )
                    next_tool = "health_check"
                elif counts["pending"]:
                    recommendations.append(
                        _pending_stamp_text(counts["pending"], counts["pending_ids"])
                    )
                    next_tool = "check_stamp_health"
                elif total_stamps > 0:
                    recommendations.append(_no_writable_stamp_text(counts["blocked"]))
                    next_tool = "purchase_stamp"
                else:
                    recommendations.append(
                        "No stamps found — purchase one before uploading"
                    )
                    next_tool = "purchase_stamp"
            except RequestException:
                stamps_info += "\n📋 Stamps: unable to check (gateway error)\n"
                recommendations.append(
                    "Could not check stamps — try list_stamps separately"
                )

        response_text += stamps_info

        # Chain anchoring status. Built before the summary so that its
        # recommendations reach the _recommendations list (#212).
        chain_text = ""
        if settings.chain_enabled:
            await _recheck_write_contract()
            if CHAIN_AVAILABLE and chain_client:
                try:
                    chain_client.health_check()
                    chain_text += f"\n\n⛓️  Chain: {chain_client.chain} (connected)"
                    chain_text += _chain_id_lines(chain_client._provider)
                    chain_text += f"\n   Contract: {chain_client.contract_address}"
                    chain_text += f"\n   Wallet: {chain_client.address}"
                    # Proactive balance check — warn if wallet can't transact
                    try:
                        wallet_info = chain_client.balance()
                        guidance = _format_funding_guidance(
                            wallet_info.address,
                            wallet_info.balance_wei,
                            wallet_info.chain,
                        )
                        if guidance:
                            chain_text += guidance
                            recommendations.append(
                                "Wallet balance too low for write operations — "
                                "fund it before using anchor_hash or record_transform"
                            )
                    except Exception:
                        pass  # Non-critical — don't fail health_check
                except Exception as chain_err:
                    from .chain.exceptions import ChainIdMismatchError

                    chain_text += (
                        f"\n\n⛓️  Chain: {settings.chain_name} "
                        f"(error: {_exc_text(chain_err)})"
                    )
                    chain_text += _chain_id_lines(chain_client._provider)
                    recommendations.append(
                        "RPC is on another chain than CHAIN_NAME — fix "
                        "CHAIN_RPC_URL / CHAIN_RPC_URLS or CHAIN_NAME"
                        if isinstance(chain_err, ChainIdMismatchError)
                        else "Chain anchoring enabled but RPC unreachable"
                    )
            elif CHAIN_AVAILABLE and _chain_init_error:
                # Wallet configured, but the key or the contract check failed
                chain_text += (
                    f"\n\n⛓️  Chain: {settings.chain_name} (writes disabled)\n   "
                    + _writes_disabled_text().replace("\n", "\n   ")
                )
                recommendations.append(_writes_disabled_recommendation())
            elif CHAIN_AVAILABLE:
                # Dependencies present but no wallet → read-only mode
                chain_text += (
                    f"\n\n⛓️  Chain: {settings.chain_name} "
                    f"(read-only, no wallet — set PROVENANCE_WALLET_KEY for write tools)"
                )
                try:
                    reader = await asyncio.to_thread(_chain_reader)
                    chain_text += _chain_id_lines(reader._provider)
                except Exception as chain_err:
                    chain_text += f"\n   Chain error: {_exc_text(chain_err)}"
                    configured = _effective_chain_id(settings.chain_name)
                    if configured is not None:
                        chain_text += (
                            f"\n   Chain ID: {configured} "
                            "(configured, not verified against the RPC)"
                        )
            else:
                chain_text += "\n\n⛓️  Chain: enabled but dependencies not available"
                recommendations.append(
                    "Reinstall with: pip install -e . (web3/eth-account should be included)"
                )

        # Summary
        response_text += f"\nready: {str(ready).lower()}"

        if recommendations:
            response_text += "\n_recommendations:"
            for rec in recommendations:
                response_text += f"\n  - {rec}"

        response_text += chain_text

        # Gateway status
        response_text += f"\n_gateway: {gateway_url} ({'connected' if gateway_ok else 'unreachable'})"

        response_text += f"\n\n_next: {next_tool}"
        response_text += f"\n_related: list_stamps, purchase_stamp, get_wallet_info"

        return CallToolResult(content=[TextContent(type="text", text=response_text)])

    except GatewayAnswerUnreadable as e:
        # The gateway answered: the connection works, the answer does not.
        gateway_url = url_without_userinfo(settings.swarm_gateway_url)
        error_msg = (
            f"❌ Gateway answer unreadable\n\nError: {str(e)}\n"
            f"Gateway: {gateway_url}\n\n"
            "The gateway answered, but this server cannot read the answer. Call "
            "health_check again; if this repeats, report it to the gateway operator."
        )
        logger.error(f"Health check failed: {str(e)}")
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(error_msg, retryable=True, next_tool="health_check"),
                )
            ],
            isError=True,
        )
    except RequestException as e:
        gateway_url = url_without_userinfo(settings.swarm_gateway_url)
        error_msg = f"❌ Connection failed!\n\n"
        error_msg += f"Error: {str(e)}\n"
        error_msg += f"Gateway: {gateway_url}\n\n"
        error_msg += f"🔧 Troubleshooting:\n"
        error_msg += f"   • Check if the gateway server is running\n"
        error_msg += f"   • Verify the gateway URL: {gateway_url}\n"
        error_msg += f"   • Check your internet connection"

        logger.error(f"Health check failed: {str(e)}")
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        error_msg,
                        retryable=_is_retryable_error(e),
                        next_tool=_recovery_hint(e),
                    ),
                )
            ],
            isError=True,
        )


def _health_entries(title: str, entries: Any) -> str:
    """The Errors or Warnings section of check_stamp_health. Only text
    fields are printed: a code of another type shows as ?, a message or
    suggestion of another type is left out."""
    if isinstance(entries, list) and not entries:
        return ""
    text = f"{title}:\n"
    if not isinstance(entries, list):
        return text + f"   [?] The {title.lower()} list is not in the expected format\n"
    for entry in entries:
        if not isinstance(entry, dict):
            text += "   [?] An entry is not in the expected format\n"
            continue
        code = entry.get("code", "?")
        code = one_line(code, 40) if isinstance(code, str) else "?"
        text += f"   [{code}] {_gw_text(entry.get('message', ''), 500)}\n"
        if _is_text(entry.get("suggestion")):
            text += f"   → {one_line(entry['suggestion'], 500)}\n"
    return text


def _error_list_unreadable(errors: Any) -> bool:
    """Whether check_stamp_health cannot read every error code: the list is
    not a list, or an entry is not an object with a text code."""
    if not isinstance(errors, list):
        return True
    return any(
        not isinstance(e, dict) or not isinstance(e.get("code"), str) for e in errors
    )


async def handle_check_stamp_health(arguments: Dict[str, Any]) -> CallToolResult:
    """Handle stamp health check requests."""
    try:
        stamp_id = arguments.get("stamp_id")
        if not stamp_id:
            raise ValueError("Stamp ID is required")

        clean_stamp_id = validate_and_clean_stamp_id(stamp_id)
        result = gateway_client.check_stamp_health(clean_stamp_id)
        if not isinstance(result, dict):
            response_text = (
                f"⚠️  Stamp {clean_stamp_id[:16]}...: {_NOT_EXPECTED_FORMAT}, so "
                "its upload readiness is unknown. Check it in list_stamps.\n"
            )
            response_text += _format_hints("list_stamps", ["get_stamp_status"])
            return CallToolResult(
                content=[TextContent(type="text", text=response_text)]
            )

        # Only a real True counts: a string such as "false" is not a yes.
        can_upload = result.get("can_upload") is True
        errors = result.get("errors", [])
        warnings = result.get("warnings", [])
        status = result.get("status", {})

        if can_upload:
            response_text = f"✅ Stamp {clean_stamp_id[:16]}... is healthy and ready for uploads.\n\n"
        else:
            response_text = (
                f"❌ Stamp {clean_stamp_id[:16]}... cannot be used for uploads.\n\n"
            )

        response_text += _health_entries("Errors", errors)
        response_text += _health_entries("Warnings", warnings)

        # The utilization the Utilization line and the propagation check
        # use: only a number from 0 to 100.
        percent = None
        utilization = None
        if isinstance(status, dict):
            utilization = status.get("utilizationPercent")
            percent = _number_in(utilization, 0, 100)
        elif status:
            response_text += "\nStatus: not in the expected format\n"
            status = {}

        if status:
            response_text += f"\nStatus:\n"
            if utilization is not None:
                shown = f"{percent}%" if percent is not None else "N/A"
                # Missing: "unknown"; null (a valid Optional) prints as before:
                # empty. Any other type: "unknown".
                util_status = status.get("utilizationStatus", "unknown")
                if util_status is None:
                    util_status = ""
                elif isinstance(util_status, str):
                    util_status = _gw(util_status)
                else:
                    util_status = "unknown"
                response_text += f"   Utilization: {shown} ({util_status})\n"
            # Left out when it fails validation, as when it is missing.
            ttl = _number_in(status.get("batchTTL"), 0, MAX_TTL_SECONDS)
            if ttl is not None:
                response_text += f"   TTL: {_ttl_text(ttl)}\n"
            if _is_text(status.get("expectedExpiration")):
                response_text += f"   Expires: {_gw(status['expectedExpiration'])}\n"

        # Propagation timing (from gateway, when available)
        propagation_status = result.get("propagationStatus")
        seconds_since = result.get("secondsSincePurchase")
        estimated_ready = _gw_text(result.get("estimatedReadyAt"))
        has_estimate = _is_text(result.get("estimatedReadyAt"))

        if isinstance(propagation_status, str) and propagation_status:
            response_text += f"\nPropagation:\n"
            response_text += f"   Status: {_gw(propagation_status, 40)}\n"
            age = _stamp_age(seconds_since)
            if age is not None:
                response_text += f"   Purchased: {age}s ago\n"
            if has_estimate:
                response_text += f"   Estimated ready: {estimated_ready}\n"

        # Contextual hints based on health
        if can_upload:
            response_text += _format_hints(
                "upload_data", ["get_stamp_status", "extend_stamp"]
            )
        else:
            # Detect propagation: prefer gateway field, fall back to heuristic
            is_propagating = propagation_status == "propagating"
            # An error list the server cannot read may hide NOT_FOUND: check
            # again rather than buy.
            errors_unreadable = not is_propagating and _error_list_unreadable(errors)
            if not is_propagating and not errors_unreadable:
                # Only string codes count; an unhashable code cannot raise.
                error_codes = {
                    e["code"]
                    for e in (errors if isinstance(errors, list) else [])
                    if isinstance(e, dict) and isinstance(e.get("code"), str)
                }
                # A value that fails validation counts as not reported, so
                # malformed data never leads to purchase_stamp.
                util_pct = 0 if percent is None else percent
                is_propagating = ("NOT_FOUND" in error_codes) or (
                    "NOT_USABLE" in error_codes and util_pct == 0
                )
            if errors_unreadable:
                response_text += (
                    "\n💡 The gateway's error list is not in the expected format, so "
                    "the cause is unknown. Retry check_stamp_health in 15 seconds. "
                    "If the same answer repeats, call get_stamp_status to see the "
                    "stamp's state."
                )
                response_text += _format_hints(
                    "check_stamp_health", ["list_stamps", "get_stamp_status"]
                )
            elif is_propagating:
                if has_estimate:
                    response_text += (
                        f"\n💡 Stamp is propagating. Estimated ready at {estimated_ready}. "
                        "Retry check_stamp_health in 15 seconds."
                    )
                else:
                    response_text += (
                        "\n💡 If recently purchased, the stamp is propagating on the blockchain. "
                        "This typically takes up to 2 minutes. Retry check_stamp_health in 15 seconds."
                    )
                response_text += _format_hints(
                    "check_stamp_health", ["list_stamps", "extend_stamp"]
                )
            else:
                response_text += _format_hints(
                    "purchase_stamp", ["list_stamps", "extend_stamp"]
                )

        return CallToolResult(content=[TextContent(type="text", text=response_text)])

    except ValueError as e:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        f"Validation error: {str(e)}",
                        retryable=False,
                        next_tool="list_stamps",
                    ),
                )
            ],
            isError=True,
        )
    except RequestException as e:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        f"Failed to check stamp health: {str(e)}",
                        retryable=_is_retryable_error(e),
                        next_tool=_recovery_hint(e),
                    ),
                )
            ],
            isError=True,
        )


def _wallet_text(value: Any) -> str:
    """walletAddress for printing: only text."""
    return _gw(value) if isinstance(value, str) else "N/A"


def _wallet_balance(value: Any) -> str:
    """bzzBalance for printing: text, or a number from 0 to 10**40. Null
    (a valid Optional) prints empty, as before #270."""
    if value is None or isinstance(value, str):
        return _gw(value)
    number = _number_in(value, 0, 10**40)
    return "N/A" if number is None else _gw(number)


async def handle_get_wallet_info(arguments: Dict[str, Any]) -> CallToolResult:
    """Handle wallet info requests."""
    try:
        result = gateway_client.get_wallet_info()

        response_text = f"Wallet Information:\n"
        if isinstance(result, dict):
            response_text += (
                f"   Address: {_wallet_text(result.get('walletAddress', 'N/A'))}\n"
                f"   BZZ Balance: {_wallet_balance(result.get('bzzBalance', 'N/A'))}\n"
            )
        else:
            response_text += (
                f"   The wallet details are unknown: {_NOT_EXPECTED_FORMAT}.\n"
            )
        response_text += _format_hints("purchase_stamp", ["list_stamps"])

        return CallToolResult(content=[TextContent(type="text", text=response_text)])

    except RequestException as e:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        f"Failed to get wallet info: {str(e)}",
                        retryable=_is_retryable_error(e),
                        next_tool=_recovery_hint(e),
                    ),
                )
            ],
            isError=True,
        )


async def handle_chain_balance(arguments: Dict[str, Any]) -> CallToolResult:
    """Handle chain wallet balance requests."""
    if not CHAIN_AVAILABLE:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        "Chain module not available. Reinstall with: pip install -e . (web3/eth-account should be included)",
                        retryable=False,
                    ),
                )
            ],
            isError=True,
        )
    await _recheck_write_contract()
    if not chain_client:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        _no_write_client_text(
                            f"chain_balance requires a funded wallet. {WALLET_KEY_HINT}\n"
                            "Read-only chain tools (verify_hash, get_provenance, get_provenance_chain, chain_health) "
                            "work without a wallet."
                        ),
                        retryable=False,
                        next_tool="chain_health",
                    ),
                )
            ],
            isError=True,
        )

    try:
        wallet_info = await asyncio.to_thread(chain_client.balance)

        response_text = f"⛓️  Chain Wallet Balance\n\n"
        response_text += f"   Wallet: {wallet_info.address}\n"
        response_text += f"   Balance: {wallet_info.balance_eth} ETH ({wallet_info.balance_wei:,} wei)\n"
        response_text += f"   Chain: {wallet_info.chain}\n"
        response_text += f"   Contract: {wallet_info.contract_address}\n"
        response_text += f"   RPC: {_mask_rpc_url(chain_client._provider.rpc_url)}"

        guidance = _format_funding_guidance(
            wallet_info.address, wallet_info.balance_wei, wallet_info.chain
        )
        response_text += guidance
        response_text += _format_hints("anchor_hash", ["chain_health", "health_check"])

        return CallToolResult(content=[TextContent(type="text", text=response_text)])

    except Exception as e:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        f"Failed to get chain balance: {_exc_text(e)}",
                        retryable=True,
                        next_tool="chain_health",
                    ),
                )
            ],
            isError=True,
        )


async def handle_chain_health(arguments: Dict[str, Any]) -> CallToolResult:
    """Handle chain RPC health check requests."""
    if not CHAIN_AVAILABLE:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        "Chain module not available. Reinstall with: pip install -e . (web3/eth-account should be included)",
                        retryable=False,
                    ),
                )
            ],
            isError=True,
        )

    await _recheck_write_contract()
    try:
        from .chain.provider import PROBE_METHODS

        # Use chain_client if available, otherwise create a temporary provider
        if chain_client:
            provider = chain_client._provider
        else:
            from .chain.provider import ChainProvider

            provider = ChainProvider(
                chain=settings.chain_name,
                rpc_url=settings.chain_rpc_url,
                contract_address=settings.chain_contract_address,
                explorer_url=settings.chain_explorer_url,
                rpc_fallbacks=_parse_rpc_fallbacks(),
            )

        start = time.monotonic()
        await asyncio.to_thread(provider.health_check)
        elapsed_ms = (time.monotonic() - start) * 1000

        block_number = await asyncio.to_thread(provider.get_block_number)

        response_text = f"⛓️  Chain RPC Health\n\n"
        response_text += f"   Connected: true\n"
        response_text += f"   Chain: {provider.chain}"
        response_text += _chain_id_lines(provider) + "\n"
        response_text += f"   Latest Block: {block_number:,}\n"
        response_text += f"   RPC Response: {elapsed_ms:.0f}ms\n"
        response_text += f"   Probe: {', '.join(PROBE_METHODS)}\n"
        response_text += f"   Contract: {provider.contract_address}\n"
        response_text += f"   RPC: {_mask_rpc_url(provider.rpc_url)}"
        if _chain_init_error:
            response_text += "\n   " + _writes_disabled_text().replace("\n", "\n   ")
        response_text += _format_hints("chain_balance", ["health_check"])

        return CallToolResult(content=[TextContent(type="text", text=response_text)])

    except Exception as e:
        # Import ChainConnectionError for better error detection
        try:
            from .chain.exceptions import ChainConnectionError

            is_connection_error = isinstance(e, ChainConnectionError)
        except ImportError:
            is_connection_error = False

        error_msg = f"⛓️  Chain RPC Health\n\n"
        error_msg += f"   Connected: false\n"
        error_msg += f"   Error: {_exc_text(e)}"

        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        error_msg,
                        retryable=is_connection_error,
                    ),
                )
            ],
            isError=True,
        )


async def handle_anchor_hash(arguments: Dict[str, Any]) -> CallToolResult:
    """Handle anchor_hash requests — register a Swarm hash on-chain."""
    if not CHAIN_AVAILABLE:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        "Chain module not available. Reinstall with: pip install -e . (web3/eth-account should be included)",
                        retryable=False,
                    ),
                )
            ],
            isError=True,
        )
    await _recheck_write_contract()
    if not chain_client:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        _no_write_client_text(
                            f"anchor_hash requires a funded wallet. {WALLET_KEY_HINT}\n"
                            "Read-only chain tools (verify_hash, get_provenance, get_provenance_chain, chain_health) "
                            "work without a wallet."
                        ),
                        retryable=False,
                        next_tool="chain_health",
                    ),
                )
            ],
            isError=True,
        )

    try:
        swarm_hash = arguments.get("swarm_hash")
        if not swarm_hash:
            raise ValueError("swarm_hash is required")
        clean_hash = validate_and_clean_reference_hash(swarm_hash)

        data_type = arguments.get("data_type", "swarm-provenance")
        if len(data_type) > 64:
            raise ValueError("data_type must be 64 characters or fewer")

        # Only a missing key or None means "no owner": any other value,
        # including "", False and 0, must be a valid address.
        owner = arguments.get("owner")
        if owner is not None:
            owner = validate_address_argument(owner, "owner")
        storage_ref = arguments.get("storage_ref") or None
        if storage_ref:
            storage_ref = validate_and_clean_reference_hash(storage_ref)
        if owner and storage_ref:
            # anchor_for has no storage ref variant: it would be dropped.
            raise ValueError(
                "storage_ref cannot be combined with owner: registering on "
                "behalf of another owner does not link a storage reference. "
                "Anchor without storage_ref, then have the owner call "
                "set_storage_ref."
            )

        gate = await _spend_confirmation(
            "anchor_hash",
            arguments,
            {
                "swarm_hash": clean_hash,
                "data_type": data_type,
                "owner": owner,
                "storage_ref": storage_ref,
            },
        )
        if gate:
            return gate

        if owner:
            result = await asyncio.to_thread(
                chain_client.anchor_for, clean_hash, owner, data_type
            )
        else:
            result = await asyncio.to_thread(
                chain_client.anchor, clean_hash, data_type, storage_ref
            )

        response_text = f"⛓️  Hash anchored on-chain\n\n"
        response_text += f"   Swarm Hash: {result.swarm_hash}\n"
        response_text += f"   Data Type: {result.data_type}\n"
        response_text += f"   Owner: {result.owner}\n"
        if result.storage_ref:
            response_text += f"   Storage Ref: {result.storage_ref}\n"
        response_text += f"   Tx Hash: {result.tx_hash}\n"
        response_text += f"   Block: {result.block_number:,}\n"
        response_text += f"   Gas Used: {result.gas_used:,}"
        if result.explorer_url:
            response_text += f"\n   Explorer: {result.explorer_url}"

        # Post-tx balance check
        try:
            wallet_info = await asyncio.to_thread(chain_client.balance)
            guidance = _format_funding_guidance(
                wallet_info.address, wallet_info.balance_wei, wallet_info.chain
            )
            response_text += guidance
        except Exception:
            pass  # Non-critical — don't fail the success response

        response_text += _format_hints(
            "download_data", ["chain_balance", "health_check"]
        )

        return CallToolResult(content=[TextContent(type="text", text=response_text)])

    except ValueError as e:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        f"Validation error: {_exc_text(e)}",
                        retryable=False,
                    ),
                )
            ],
            isError=True,
        )
    except Exception as e:
        # Import chain exceptions for targeted handling
        try:
            from .chain.exceptions import (
                DataAlreadyRegisteredError,
                ChainTransactionError,
                ChainConnectionError,
                ChainValidationError,
                ChainError,
            )
        except ImportError:
            # Shouldn't happen since CHAIN_AVAILABLE is True, but be safe
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=_format_error(f"Chain error: {_exc_text(e)}", retryable=False),
                    )
                ],
                isError=True,
            )

        if isinstance(e, DataAlreadyRegisteredError):
            # Registration is first-come and a Swarm reference is a content
            # address, so someone else may own the record (#164). Only a record
            # owned by the expected owner counts as success (idempotent
            # anchoring); any other owner is an error.
            expected_owner = owner or chain_client.address
            timestamp_str = _format_chain_timestamp(e.timestamp)

            if not _owner_matches(e.owner, expected_owner):
                msg = "Hash already registered on-chain by a different owner\n\n"
                msg += f"   Swarm Hash: {clean_hash}\n"
                msg += f"   Record owner: {_chain_address(e.owner)}\n"
                msg += f"   Expected owner: {_chain_address(expected_owner)}\n"
                msg += f"   Registered: {timestamp_str}\n"
                msg += f"   Data Type: {quote(e.data_type)}\n\n"
                msg += (
                    "No transaction was sent. Another address owns this "
                    "record, so owner-only tools for this hash fail for the "
                    "expected owner. To anchor a record you own, change the "
                    "content, upload it again, and anchor the new hash.\n\n"
                )
                msg += QUOTED_NOTE
                return CallToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=_format_error(
                                msg, retryable=False, next_tool="get_provenance"
                            ),
                        )
                    ],
                    isError=True,
                )

            # Not isError: the hash is already on-chain with the expected
            # owner, so the agent sees the existing record and proceeds.
            response_text = f"⛓️  Hash already registered on-chain\n\n"
            response_text += f"   Swarm Hash: {clean_hash}\n"
            response_text += f"   Owner: {_chain_address(e.owner)}\n"
            response_text += f"   Registered: {timestamp_str}\n"
            response_text += f"   Data Type: {quote(e.data_type)}"
            response_text += f"\n\n{QUOTED_NOTE}"
            response_text += _format_hints(
                "download_data", ["chain_balance", "health_check"]
            )
            return CallToolResult(
                content=[TextContent(type="text", text=response_text)]
            )

        if isinstance(e, ChainTransactionError):
            if _may_be_pending(e):
                return _pending_tx_result(e, "verify_hash")
            if _is_insufficient_funds_error(e):
                msg = _format_insufficient_funds_error("anchor_hash", chain_client)
            else:
                msg = f"Transaction failed: {_exc_text(e)}"
                if e.tx_hash:
                    msg += f"\n   Tx Hash: {_exc_tx_hash(e)}"
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=_format_error(
                            msg, retryable=False, next_tool="chain_balance"
                        ),
                    )
                ],
                isError=True,
            )

        if isinstance(e, ChainConnectionError):
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=_format_error(
                            f"Chain connection error: {_exc_text(e)}",
                            retryable=True,
                            next_tool="chain_health",
                        ),
                    )
                ],
                isError=True,
            )

        if isinstance(e, ChainValidationError):
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=_format_error(
                            f"Chain validation error: {_exc_text(e)}",
                            retryable=False,
                        ),
                    )
                ],
                isError=True,
            )

        # Catch-all: still check for insufficient funds in generic errors
        if _is_insufficient_funds_error(e):
            msg = _format_insufficient_funds_error("anchor_hash", chain_client)
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=_format_error(
                            msg, retryable=False, next_tool="chain_balance"
                        ),
                    )
                ],
                isError=True,
            )

        # Generic ChainError or unexpected Exception
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(f"Chain error: {_exc_text(e)}", retryable=False),
                )
            ],
            isError=True,
        )


async def handle_verify_hash(arguments: Dict[str, Any]) -> CallToolResult:
    """Handle verify_hash requests — check if a Swarm hash is registered on-chain.

    Read-only: works without PROVENANCE_WALLET_KEY via a read-only
    ChainClient (no signing needed), with the same RPC failover.
    """
    if not CHAIN_AVAILABLE:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        "Chain module not available. Reinstall with: pip install -e . (web3/eth-account should be included)",
                        retryable=False,
                    ),
                )
            ],
            isError=True,
        )

    try:
        swarm_hash = arguments.get("swarm_hash")
        if not swarm_hash:
            raise ValueError("swarm_hash is required")
        clean_hash = validate_and_clean_reference_hash(swarm_hash)

        # Only a missing key or None means "no expected owner".
        expected_owner = arguments.get("expected_owner")
        if expected_owner is not None:
            expected_owner = validate_address_argument(
                expected_owner, "expected_owner"
            )

        from .chain.exceptions import DataNotRegisteredError

        try:
            record = await asyncio.to_thread(_chain_reader().get, clean_hash)
            is_registered = True
        except DataNotRegisteredError:
            record = None
            is_registered = False

        if is_registered and record:
            timestamp_str = _format_chain_timestamp(record.timestamp)

            if expected_owner and not _owner_matches(record.owner, expected_owner):
                msg = "Owner mismatch: the hash is registered to a different owner\n\n"
                msg += f"   Swarm Hash: {clean_hash}\n"
                msg += f"   Record owner: {_chain_address(record.owner)}\n"
                msg += f"   Expected owner: {expected_owner}\n"
                msg += f"   Registered: {timestamp_str}\n"
                msg += f"   Data Type: {quote(record.data_type)}\n\n"
                msg += QUOTED_NOTE
                return CallToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=_format_error(
                                msg, retryable=False, next_tool="get_provenance"
                            ),
                        )
                    ],
                    isError=True,
                )

            response_text = f"⛓️  Verified — hash IS registered on-chain\n\n"
            response_text += f"   Swarm Hash: {clean_hash}\n"
            response_text += f"   Owner: {_chain_address(record.owner)}\n"
            if expected_owner:
                response_text += "   Expected owner: matches\n"
            response_text += f"   Registered: {timestamp_str}\n"
            response_text += f"   Data Type: {quote(record.data_type)}\n"
            response_text += f"   Status: {record.status.name}"
            if record.storage_ref:
                response_text += f"\n   Storage Ref: {record.storage_ref}"
            response_text += f"\n\n{QUOTED_NOTE}"
            response_text += _format_hints(
                "download_data", ["anchor_hash", "health_check"]
            )
        else:
            response_text = f"⛓️  Not found — hash is NOT registered on-chain\n\n"
            response_text += f"   Swarm Hash: {clean_hash}"
            response_text += _format_hints(
                "anchor_hash", ["upload_data", "health_check"]
            )

        return CallToolResult(content=[TextContent(type="text", text=response_text)])

    except ValueError as e:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(f"Validation error: {_exc_text(e)}", retryable=False),
                )
            ],
            isError=True,
        )
    except Exception as e:
        try:
            from .chain.exceptions import ChainConnectionError
        except ImportError:
            ChainConnectionError = None

        is_conn_error = ChainConnectionError and isinstance(e, ChainConnectionError)

        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        f"Chain error: {_exc_text(e)}",
                        retryable=is_conn_error,
                        next_tool="chain_health" if is_conn_error else None,
                    ),
                )
            ],
            isError=True,
        )


async def handle_get_provenance(arguments: Dict[str, Any]) -> CallToolResult:
    """Handle get_provenance requests — retrieve full on-chain provenance record.

    Read-only: works without PROVENANCE_WALLET_KEY via a read-only
    ChainClient (no signing needed), with the same RPC failover.
    """
    if not CHAIN_AVAILABLE:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        "Chain module not available. Reinstall with: pip install -e . (web3/eth-account should be included)",
                        retryable=False,
                    ),
                )
            ],
            isError=True,
        )

    try:
        swarm_hash = arguments.get("swarm_hash")
        if not swarm_hash:
            raise ValueError("swarm_hash is required")
        clean_hash = validate_and_clean_reference_hash(swarm_hash)

        record = await asyncio.to_thread(_chain_reader().get, clean_hash)

        from datetime import datetime, timezone

        timestamp_str = "unknown"
        if record.timestamp:
            try:
                dt = datetime.fromtimestamp(record.timestamp, tz=timezone.utc)
                timestamp_str = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
            except (ValueError, OSError):
                timestamp_str = str(record.timestamp)

        status_labels = {
            0: "ACTIVE — data is live and accessible",
            1: "RESTRICTED — data access is restricted",
            2: "DELETED — data marked as deleted",
        }
        status_text = status_labels.get(
            record.status.value, f"UNKNOWN ({record.status.value})"
        )

        response_text = f"⛓️  Provenance Record\n\n"
        response_text += f"   Swarm Hash: {clean_hash}\n"
        response_text += f"   Owner: {record.owner}\n"
        response_text += f"   Registered: {timestamp_str}\n"
        response_text += f"   Data Type: {quote(record.data_type)}\n"
        response_text += f"   Status: {status_text}"
        if record.storage_ref:
            response_text += f"\n   Storage Ref: {record.storage_ref}"

        if record.transformations:
            response_text += f"\n\n   Transformations ({len(record.transformations)}):"
            for t in record.transformations[:_MAX_LISTED]:
                response_text += f"\n     - {quote(t.description)}"
            response_text += _more_note(record.transformations)

        if record.accessors:
            response_text += f"\n\n   Accessors ({len(record.accessors)}):"
            for addr in record.accessors[:_MAX_LISTED]:
                response_text += f"\n     - {addr}"
            response_text += _more_note(record.accessors)

        response_text += f"\n\n{QUOTED_NOTE}"
        response_text += _format_hints("download_data", ["verify_hash", "anchor_hash"])

        return CallToolResult(content=[TextContent(type="text", text=response_text)])

    except ValueError as e:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(f"Validation error: {_exc_text(e)}", retryable=False),
                )
            ],
            isError=True,
        )
    except Exception as e:
        try:
            from .chain.exceptions import (
                DataNotRegisteredError,
                ChainConnectionError,
            )
        except ImportError:
            DataNotRegisteredError = None
            ChainConnectionError = None

        if DataNotRegisteredError and isinstance(e, DataNotRegisteredError):
            # Intentionally NOT isError — "not found" is a valid query result,
            # not a failure. Guide the agent toward anchor_hash instead.
            response_text = f"⛓️  Not found — hash is NOT registered on-chain\n\n"
            response_text += f"   Swarm Hash: {clean_hash}"
            response_text += _format_hints(
                "anchor_hash", ["upload_data", "health_check"]
            )
            return CallToolResult(
                content=[TextContent(type="text", text=response_text)]
            )

        is_conn_error = ChainConnectionError and isinstance(e, ChainConnectionError)

        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        f"Chain error: {_exc_text(e)}",
                        retryable=is_conn_error,
                        next_tool="chain_health" if is_conn_error else None,
                    ),
                )
            ],
            isError=True,
        )


async def handle_record_transform(arguments: Dict[str, Any]) -> CallToolResult:
    """Handle record_transform requests — record a data transformation on-chain.

    Write operation: requires PROVENANCE_WALLET_KEY and gas. Links an original
    Swarm hash to a transformed version, creating verifiable data lineage.
    Optionally restricts the original data after transformation.
    """
    if not CHAIN_AVAILABLE:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        "Chain module not available. Reinstall with: pip install -e . (web3/eth-account should be included)",
                        retryable=False,
                    ),
                )
            ],
            isError=True,
        )
    await _recheck_write_contract()
    if not chain_client:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        _no_write_client_text(
                            f"record_transform requires a funded wallet. {WALLET_KEY_HINT}\n"
                            "Read-only chain tools (verify_hash, get_provenance, get_provenance_chain, chain_health) "
                            "work without a wallet."
                        ),
                        retryable=False,
                        next_tool="chain_health",
                    ),
                )
            ],
            isError=True,
        )

    try:
        original_hash = arguments.get("original_hash")
        if not original_hash:
            raise ValueError("original_hash is required")
        clean_original = validate_and_clean_reference_hash(original_hash)

        new_hash = arguments.get("new_hash")
        if not new_hash:
            raise ValueError("new_hash is required")
        clean_new = validate_and_clean_reference_hash(new_hash)

        if clean_original == clean_new:
            raise ValueError("original_hash and new_hash must be different")

        description = arguments.get("description", "")
        if len(description) > 256:
            raise ValueError("description must be 256 characters or fewer")

        restrict_original = arguments.get("restrict_original", False)

        gate = await _spend_confirmation(
            "record_transform",
            arguments,
            {
                "original_hash": clean_original,
                "new_hash": clean_new,
                "description": description,
                "restrict_original": bool(restrict_original),
            },
            tx_count=2 if restrict_original else 1,
        )
        if gate:
            return gate

        # With restrict_original, both transactions are reserved before the
        # first is sent, so the session limits cannot stop it half-way.
        hold = None
        restrict_failed = False
        if restrict_original:
            hold = await asyncio.to_thread(chain_client.hold_transactions, 2)
        with chain_client.using_hold(hold):
            result = await asyncio.to_thread(
                chain_client.transform, clean_original, clean_new, description
            )

            response_text = f"⛓️  Transformation recorded on-chain\n\n"
            response_text += f"   Original: {result.original_hash}\n"
            response_text += f"   Transformed: {result.new_hash}\n"
            if description:
                response_text += f"   Description: {description}\n"
            response_text += f"   Tx Hash: {result.tx_hash}\n"
            response_text += f"   Block: {result.block_number:,}\n"
            response_text += f"   Gas Used: {result.gas_used:,}"
            if result.explorer_url:
                response_text += f"\n   Explorer: {result.explorer_url}"

            # Optionally restrict the original data after transformation
            if restrict_original:
                try:
                    await asyncio.to_thread(
                        chain_client.set_status, clean_original, 1
                    )  # 1 = RESTRICTED
                    response_text += (
                        f"\n\n   ⚠️  Original data status set to RESTRICTED"
                    )
                except Exception as restrict_err:
                    restrict_failed = True
                    response_text += _restrict_failure_text(
                        restrict_err, result.tx_hash, clean_original
                    )

        # Post-tx balance check
        try:
            wallet_info = await asyncio.to_thread(chain_client.balance)
            guidance = _format_funding_guidance(
                wallet_info.address, wallet_info.balance_wei, wallet_info.chain
            )
            response_text += guidance
        except Exception:
            pass  # Non-critical — don't fail the success response

        response_text += _format_hints(
            "get_provenance", ["download_data", "chain_balance"]
        )

        return CallToolResult(
            content=[TextContent(type="text", text=response_text)],
            isError=restrict_failed,
        )

    except ValueError as e:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        f"Validation error: {_exc_text(e)}",
                        retryable=False,
                    ),
                )
            ],
            isError=True,
        )
    except Exception as e:
        try:
            from .chain.exceptions import (
                DataNotRegisteredError,
                ChainTransactionError,
                ChainConnectionError,
                ChainValidationError,
                TransformationAlreadyExistsError,
            )
        except ImportError:
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=_format_error(f"Chain error: {_exc_text(e)}", retryable=False),
                    )
                ],
                isError=True,
            )

        if isinstance(e, TransformationAlreadyExistsError):
            # Intentionally NOT isError: recording the same link again is idempotent.
            response_text = f"⛓️  Transformation already recorded on-chain\n\n"
            response_text += f"   Original: {e.original_hash}\n"
            response_text += f"   Transformed: {e.new_hash}\n"
            if e.existing_description:
                response_text += f"   Description: {quote(e.existing_description)}\n"
            response_text += (
                f"\n   The (original → new) link is already on-chain. No gas spent."
            )
            if e.existing_description:
                response_text += f"\n\n{QUOTED_NOTE}"
            response_text += _format_hints("get_provenance", ["get_provenance_chain"])
            return CallToolResult(
                content=[TextContent(type="text", text=response_text)]
            )

        if isinstance(e, DataNotRegisteredError):
            # Intentionally NOT isError — guide the agent to anchor first.
            response_text = f"⛓️  Original hash is not registered on-chain\n\n"
            response_text += f"   The original data must be anchored before recording a transformation.\n"
            response_text += f"   Hash: {e.data_hash}"
            response_text += _format_hints(
                "anchor_hash", ["upload_data", "health_check"]
            )
            return CallToolResult(
                content=[TextContent(type="text", text=response_text)]
            )

        if isinstance(e, ChainTransactionError):
            if _may_be_pending(e):
                return _pending_tx_result(e, "get_provenance_chain")
            if _is_insufficient_funds_error(e):
                msg = _format_insufficient_funds_error("record_transform", chain_client)
            else:
                msg = f"Transaction failed: {_exc_text(e)}"
                if e.tx_hash:
                    msg += f"\n   Tx Hash: {_exc_tx_hash(e)}"
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=_format_error(
                            msg, retryable=False, next_tool="chain_balance"
                        ),
                    )
                ],
                isError=True,
            )

        if isinstance(e, ChainConnectionError):
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=_format_error(
                            f"Chain connection error: {_exc_text(e)}",
                            retryable=True,
                            next_tool="chain_health",
                        ),
                    )
                ],
                isError=True,
            )

        if isinstance(e, ChainValidationError):
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=_format_error(
                            f"Chain validation error: {_exc_text(e)}",
                            retryable=False,
                        ),
                    )
                ],
                isError=True,
            )

        # Catch-all: still check for insufficient funds in generic errors
        if _is_insufficient_funds_error(e):
            msg = _format_insufficient_funds_error("record_transform", chain_client)
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=_format_error(
                            msg, retryable=False, next_tool="chain_balance"
                        ),
                    )
                ],
                isError=True,
            )

        # Generic ChainError or unexpected Exception
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(f"Chain error: {_exc_text(e)}", retryable=False),
                )
            ],
            isError=True,
        )


async def handle_record_merge_transform(arguments: Dict[str, Any]) -> CallToolResult:
    """Handle record_merge_transform requests — record N-to-1 merge on-chain.

    Write operation: requires PROVENANCE_WALLET_KEY and gas. Links multiple
    source hashes to a single merged result with a transformation description.
    """
    if not CHAIN_AVAILABLE:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        "Chain module not available. Reinstall with: pip install -e . (web3/eth-account should be included)",
                        retryable=False,
                    ),
                )
            ],
            isError=True,
        )
    await _recheck_write_contract()
    if not chain_client:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        _no_write_client_text(
                            f"record_merge_transform requires a funded wallet. {WALLET_KEY_HINT}\n"
                            "Read-only chain tools (verify_hash, get_provenance, get_provenance_chain, chain_health) "
                            "work without a wallet."
                        ),
                        retryable=False,
                        next_tool="chain_health",
                    ),
                )
            ],
            isError=True,
        )

    try:
        source_hashes = arguments.get("source_hashes")
        if not source_hashes or not isinstance(source_hashes, list):
            raise ValueError("source_hashes is required and must be an array")
        if len(source_hashes) < 2:
            raise ValueError("source_hashes must contain at least 2 items")
        if len(source_hashes) > 50:
            raise ValueError("source_hashes must contain at most 50 items")
        clean_sources = [validate_and_clean_reference_hash(h) for h in source_hashes]

        new_hash = arguments.get("new_hash")
        if not new_hash:
            raise ValueError("new_hash is required")
        clean_new = validate_and_clean_reference_hash(new_hash)

        if clean_new in clean_sources:
            raise ValueError("new_hash must not be one of the source_hashes")

        description = arguments.get("description", "")
        if len(description) > 256:
            raise ValueError("description must be 256 characters or fewer")

        new_data_type = arguments.get("new_data_type", "merged")
        if len(new_data_type) > 64:
            raise ValueError("new_data_type must be 64 characters or fewer")

        gate = await _spend_confirmation(
            "record_merge_transform",
            arguments,
            {
                "source_hashes": clean_sources,
                "new_hash": clean_new,
                "description": description,
                "new_data_type": new_data_type,
            },
        )
        if gate:
            return gate

        result = await asyncio.to_thread(
            chain_client.merge_transform,
            clean_sources,
            clean_new,
            description,
            new_data_type,
        )

        response_text = f"⛓️  Merge transformation recorded on-chain\n\n"
        response_text += f"   Sources ({len(clean_sources)}):\n"
        for i, src in enumerate(result.source_hashes, 1):
            response_text += f"     {i}. {src}\n"
        response_text += f"   Merged: {result.new_hash}\n"
        if description:
            response_text += f"   Description: {description}\n"
        response_text += f"   Data Type: {result.new_data_type}\n"
        response_text += f"   Tx Hash: {result.tx_hash}\n"
        response_text += f"   Block: {result.block_number:,}\n"
        response_text += f"   Gas Used: {result.gas_used:,}"
        if result.explorer_url:
            response_text += f"\n   Explorer: {result.explorer_url}"

        # Post-tx balance check
        try:
            wallet_info = await asyncio.to_thread(chain_client.balance)
            guidance = _format_funding_guidance(
                wallet_info.address, wallet_info.balance_wei, wallet_info.chain
            )
            response_text += guidance
        except Exception:
            pass

        response_text += _format_hints(
            "get_provenance_chain", ["get_provenance", "chain_balance"]
        )

        return CallToolResult(content=[TextContent(type="text", text=response_text)])

    except ValueError as e:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        f"Validation error: {_exc_text(e)}",
                        retryable=False,
                    ),
                )
            ],
            isError=True,
        )
    except Exception as e:
        try:
            from .chain.exceptions import (
                DataNotRegisteredError,
                ChainTransactionError,
                ChainConnectionError,
                ChainValidationError,
            )
        except ImportError:
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=_format_error(f"Chain error: {_exc_text(e)}", retryable=False),
                    )
                ],
                isError=True,
            )

        if isinstance(e, DataNotRegisteredError):
            response_text = f"⛓️  Source hash is not registered on-chain\n\n"
            response_text += f"   All source hashes must be anchored before recording a merge transformation.\n"
            response_text += f"   Hash: {e.data_hash}"
            response_text += _format_hints(
                "anchor_hash", ["upload_data", "health_check"]
            )
            return CallToolResult(
                content=[TextContent(type="text", text=response_text)]
            )

        if isinstance(e, ChainTransactionError):
            if _may_be_pending(e):
                return _pending_tx_result(e, "get_provenance_chain")
            if _is_insufficient_funds_error(e):
                msg = _format_insufficient_funds_error(
                    "record_merge_transform", chain_client
                )
            else:
                msg = f"Transaction failed: {_exc_text(e)}"
                if e.tx_hash:
                    msg += f"\n   Tx Hash: {_exc_tx_hash(e)}"
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=_format_error(
                            msg, retryable=False, next_tool="chain_balance"
                        ),
                    )
                ],
                isError=True,
            )

        if isinstance(e, ChainConnectionError):
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=_format_error(
                            f"Chain connection error: {_exc_text(e)}",
                            retryable=True,
                            next_tool="chain_health",
                        ),
                    )
                ],
                isError=True,
            )

        if isinstance(e, ChainValidationError):
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=_format_error(
                            f"Chain validation error: {_exc_text(e)}",
                            retryable=False,
                        ),
                    )
                ],
                isError=True,
            )

        if _is_insufficient_funds_error(e):
            msg = _format_insufficient_funds_error(
                "record_merge_transform", chain_client
            )
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=_format_error(
                            msg, retryable=False, next_tool="chain_balance"
                        ),
                    )
                ],
                isError=True,
            )

        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(f"Chain error: {_exc_text(e)}", retryable=False),
                )
            ],
            isError=True,
        )


async def handle_get_provenance_chain(arguments: Dict[str, Any]) -> CallToolResult:
    """Handle get_provenance_chain requests — traverse transformation lineage.

    Read-only: works without PROVENANCE_WALLET_KEY via a read-only
    ChainClient (no signing needed), with the same RPC failover.
    """
    if not CHAIN_AVAILABLE:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        "Chain module not available. Reinstall with: pip install -e . (web3/eth-account should be included)",
                        retryable=False,
                    ),
                )
            ],
            isError=True,
        )

    try:
        swarm_hash = arguments.get("swarm_hash")
        if not swarm_hash:
            raise ValueError("swarm_hash is required")
        clean_hash = validate_and_clean_reference_hash(swarm_hash)

        max_depth = arguments.get("max_depth", 10)
        if not isinstance(max_depth, int) or max_depth < 1 or max_depth > 50:
            raise ValueError("max_depth must be an integer between 1 and 50")

        reader = _chain_reader()
        chain_records = await asyncio.to_thread(
            lambda: reader.get_provenance_chain(clean_hash, max_depth=max_depth)
        )

        if not chain_records:
            response_text = f"⛓️  No provenance chain found\n\n"
            response_text += f"   Swarm Hash: {clean_hash}\n"
            response_text += f"   The hash is not registered on-chain."
            response_text += _format_hints(
                "anchor_hash", ["upload_data", "health_check"]
            )
            return CallToolResult(
                content=[TextContent(type="text", text=response_text)]
            )

        banner = _lineage_banner(chain_records, clean_hash, max_depth)

        response_text = f"⛓️  Provenance Chain ({len(chain_records)} "
        response_text += f"{'entry' if len(chain_records) == 1 else 'entries'})\n"

        status_labels = {
            0: "ACTIVE",
            1: "RESTRICTED",
            2: "DELETED",
        }
        for i, record in enumerate(chain_records):
            if len(response_text) > _MAX_CHAIN_TEXT:
                response_text += (
                    f"\n\n   ... output capped: showing {i} of "
                    f"{len(chain_records)} entries. Use get_provenance on a "
                    "hash for its full record."
                )
                break

            node = _chain_hash(record.data_hash)
            status_text = status_labels.get(
                record.status.value, f"UNKNOWN ({_gw(record.status.value, 20)})"
            )

            response_text += f"\n\n{node}\n" if i else f"\n{node}\n"
            response_text += f"   Owner: {_chain_address(record.owner)}\n"
            response_text += (
                f"   Registered: {_format_chain_timestamp(record.timestamp)}\n"
            )
            response_text += f"   Type: {quote(record.data_type)}\n"
            response_text += f"   Status: {status_text}"

            parents = getattr(record, "parents", None)
            if isinstance(parents, list):
                shown = ", ".join(_chain_hash(p) for p in parents[:_MAX_LISTED])
                response_text += f"\n   Parents: {shown or 'none'}"
                response_text += _more_note(parents, "   ")

            if record.transformations:
                for t in record.transformations[:_MAX_LISTED]:
                    child = getattr(t, "new_data_hash", None)
                    if isinstance(child, str) and child:
                        response_text += (
                            f"\n   Edge: {node} -> {_chain_hash(child)}: "
                            f"{quote(t.description)}"
                        )
                    else:
                        response_text += (
                            f"\n   Transform (child hash not recorded): "
                            f"{quote(t.description)}"
                        )
                response_text += _more_note(record.transformations, "   ")

            read_error = getattr(record, "read_error", None)
            if isinstance(read_error, str):
                response_text += f"\n   Read error: {_gw(read_error, 120)}"
            if getattr(record, "expanded", True) is False:
                response_text += "\n   Not expanded: max_depth reached"

        response_text += f"\n\n{QUOTED_NOTE}"
        if banner:
            response_text = banner + "\n\n" + response_text
            response_text += _format_hints(
                "get_provenance_chain", ["get_provenance", "chain_health"]
            )
        else:
            response_text += _format_hints(
                "get_provenance", ["download_data", "record_transform"]
            )

        return CallToolResult(content=[TextContent(type="text", text=response_text)])

    except ValueError as e:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(f"Validation error: {_exc_text(e)}", retryable=False),
                )
            ],
            isError=True,
        )
    except Exception as e:
        try:
            from .chain.exceptions import ChainConnectionError
        except ImportError:
            ChainConnectionError = None

        is_conn_error = ChainConnectionError and isinstance(e, ChainConnectionError)

        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        f"Chain error: {_exc_text(e)}",
                        retryable=is_conn_error,
                        next_tool="chain_health" if is_conn_error else None,
                    ),
                )
            ],
            isError=True,
        )


async def handle_set_storage_ref(arguments: Dict[str, Any]) -> CallToolResult:
    """Handle set_storage_ref requests — attach a storage reference to an on-chain record.

    Write operation: requires PROVENANCE_WALLET_KEY and gas. Set-once, owner-only.
    """
    if not CHAIN_AVAILABLE:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        "Chain module not available. Reinstall with: pip install -e . (web3/eth-account should be included)",
                        retryable=False,
                    ),
                )
            ],
            isError=True,
        )
    await _recheck_write_contract()
    if not chain_client:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        _no_write_client_text(
                            f"set_storage_ref requires a funded wallet. {WALLET_KEY_HINT}\n"
                            "Read-only chain tools (verify_hash, get_provenance, lookup_by_storage_ref, chain_health) "
                            "work without a wallet."
                        ),
                        retryable=False,
                        next_tool="chain_health",
                    ),
                )
            ],
            isError=True,
        )

    try:
        data_hash = arguments.get("data_hash")
        if not data_hash:
            raise ValueError("data_hash is required")
        clean_hash = validate_and_clean_reference_hash(data_hash)

        storage_ref = arguments.get("storage_ref")
        if not storage_ref:
            raise ValueError("storage_ref is required")
        clean_ref = validate_and_clean_reference_hash(storage_ref)

        gate = await _spend_confirmation(
            "set_storage_ref",
            arguments,
            {"data_hash": clean_hash, "storage_ref": clean_ref},
        )
        if gate:
            return gate

        result = await asyncio.to_thread(
            chain_client.set_storage_ref, clean_hash, clean_ref
        )

        response_text = f"⛓️  Storage reference linked\n\n"
        response_text += f"   Data Hash: {clean_hash}\n"
        response_text += f"   Storage Ref: {clean_ref}\n"
        response_text += f"   Tx Hash: {result.tx_hash}\n"
        response_text += f"   Block: {result.block_number:,}\n"
        response_text += f"   Gas Used: {result.gas_used:,}"
        if result.explorer_url:
            response_text += f"\n   Explorer: {result.explorer_url}"

        # Post-tx balance check
        try:
            wallet_info = await asyncio.to_thread(chain_client.balance)
            guidance = _format_funding_guidance(
                wallet_info.address, wallet_info.balance_wei, wallet_info.chain
            )
            response_text += guidance
        except Exception:
            pass

        response_text += _format_hints(
            "lookup_by_storage_ref", ["get_provenance", "verify_hash"]
        )

        return CallToolResult(content=[TextContent(type="text", text=response_text)])

    except ValueError as e:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(f"Validation error: {_exc_text(e)}", retryable=False),
                )
            ],
            isError=True,
        )
    except Exception as e:
        try:
            from .chain.exceptions import (
                DataNotRegisteredError,
                StorageRefAlreadySetError,
                ChainTransactionError,
                ChainConnectionError,
            )
        except ImportError:
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=_format_error(f"Chain error: {_exc_text(e)}", retryable=False),
                    )
                ],
                isError=True,
            )

        if isinstance(e, StorageRefAlreadySetError):
            response_text = f"⛓️  Storage reference already set\n\n"
            response_text += f"   Data Hash: {e.data_hash}"
            if e.existing_storage_ref:
                response_text += f"\n   Existing Ref: {e.existing_storage_ref}"
            response_text += (
                "\n\n   Storage references are set-once and cannot be changed."
            )
            response_text += _format_hints("get_provenance", ["lookup_by_storage_ref"])
            return CallToolResult(
                content=[TextContent(type="text", text=response_text)]
            )

        if isinstance(e, DataNotRegisteredError):
            response_text = f"⛓️  Not found — data hash is NOT registered on-chain\n\n"
            response_text += f"   Data Hash: {e.data_hash}"
            response_text += "\n\n   Anchor the hash first with anchor_hash, then set the storage reference."
            response_text += _format_hints("anchor_hash", ["health_check"])
            return CallToolResult(
                content=[TextContent(type="text", text=response_text)],
                isError=True,
            )

        if isinstance(e, ChainTransactionError):
            if _may_be_pending(e):
                return _pending_tx_result(e, "get_provenance")
            if _is_insufficient_funds_error(e):
                msg = _format_insufficient_funds_error("set_storage_ref", chain_client)
            else:
                msg = f"Transaction failed: {_exc_text(e)}"
                if e.tx_hash:
                    msg += f"\n   Tx Hash: {_exc_tx_hash(e)}"
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=_format_error(
                            msg, retryable=False, next_tool="chain_balance"
                        ),
                    )
                ],
                isError=True,
            )

        if isinstance(e, ChainConnectionError):
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=_format_error(
                            f"Chain connection error: {_exc_text(e)}",
                            retryable=True,
                            next_tool="chain_health",
                        ),
                    )
                ],
                isError=True,
            )

        if _is_insufficient_funds_error(e):
            msg = _format_insufficient_funds_error("set_storage_ref", chain_client)
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=_format_error(
                            msg, retryable=False, next_tool="chain_balance"
                        ),
                    )
                ],
                isError=True,
            )

        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(f"Chain error: {_exc_text(e)}", retryable=False),
                )
            ],
            isError=True,
        )


async def handle_lookup_by_storage_ref(arguments: Dict[str, Any]) -> CallToolResult:
    """Handle lookup_by_storage_ref requests — reverse lookup by storage reference.

    Read-only: works without PROVENANCE_WALLET_KEY via a read-only
    ChainClient (no signing needed), with the same RPC failover.
    """
    if not CHAIN_AVAILABLE:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        "Chain module not available. Reinstall with: pip install -e . (web3/eth-account should be included)",
                        retryable=False,
                    ),
                )
            ],
            isError=True,
        )

    try:
        storage_ref = arguments.get("storage_ref")
        if not storage_ref:
            raise ValueError("storage_ref is required")
        clean_ref = validate_and_clean_reference_hash(storage_ref)

        from .chain.exceptions import DataNotRegisteredError

        try:
            record = await asyncio.to_thread(
                _chain_reader().lookup_by_storage_ref, clean_ref
            )
        except DataNotRegisteredError:
            # Mapping exists but points at an unregistered hash
            record = None

        if record:
            from datetime import datetime, timezone

            timestamp_str = "unknown"
            if record.timestamp:
                try:
                    dt = datetime.fromtimestamp(record.timestamp, tz=timezone.utc)
                    timestamp_str = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
                except (ValueError, OSError):
                    timestamp_str = str(record.timestamp)

            response_text = (
                f"⛓️  Found — storage reference is linked to a provenance record\n\n"
            )
            response_text += f"   Storage Ref: {clean_ref}\n"
            response_text += f"   Data Hash: {record.data_hash}\n"
            response_text += f"   Owner: {record.owner}\n"
            response_text += f"   Registered: {timestamp_str}\n"
            response_text += f"   Data Type: {quote(record.data_type)}\n"
            response_text += f"   Status: {record.status.name}"
            response_text += f"\n\n{QUOTED_NOTE}"
            response_text += _format_hints(
                "download_data", ["get_provenance", "verify_hash"]
            )
        else:
            response_text = f"⛓️  Not found — no provenance record linked to this storage reference\n\n"
            response_text += f"   Storage Ref: {clean_ref}"
            response_text += _format_hints(
                "anchor_hash", ["upload_data", "health_check"]
            )

        return CallToolResult(content=[TextContent(type="text", text=response_text)])

    except ValueError as e:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(f"Validation error: {_exc_text(e)}", retryable=False),
                )
            ],
            isError=True,
        )
    except Exception as e:
        try:
            from .chain.exceptions import ChainConnectionError
        except ImportError:
            ChainConnectionError = None

        is_conn_error = ChainConnectionError and isinstance(e, ChainConnectionError)

        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        f"Chain error: {_exc_text(e)}",
                        retryable=is_conn_error,
                        next_tool="chain_health" if is_conn_error else None,
                    ),
                )
            ],
            isError=True,
        )


async def handle_get_notary_info(arguments: Dict[str, Any]) -> CallToolResult:
    """Handle notary info requests."""
    try:
        result = gateway_client.get_notary_info()

        enabled = result.get("enabled", False)
        available = result.get("available", False)

        if enabled and available:
            response_text = f"✅ Notary service is enabled and available.\n\n"
            response_text += f"   Address: {_gw(result.get('address', 'N/A'))}\n"
        elif enabled and not available:
            response_text = (
                f"⚠️  Notary service is enabled but not currently available.\n\n"
            )
        else:
            response_text = f"Notary service is not enabled on this gateway.\n\n"

        response_text += f"   Status: {one_line(result.get('message', 'N/A'))}\n"
        response_text += _format_hints("upload_data", ["health_check"])

        return CallToolResult(content=[TextContent(type="text", text=response_text)])

    except RequestException as e:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        f"Failed to get notary info: {str(e)}",
                        retryable=_is_retryable_error(e),
                        next_tool=_recovery_hint(e),
                    ),
                )
            ],
            isError=True,
        )


async def main():
    """Main entry point for the MCP server."""
    server = create_server()

    # Set up cleanup
    def cleanup():
        logger.info("Shutting down MCP server...")
        gateway_client.close()

    try:
        async with stdio_server() as (read_stream, write_stream):
            logger.info(
                f"Starting {settings.mcp_server_name} v{settings.mcp_server_version}"
            )
            logger.info(
                "Gateway URL: %s", url_without_userinfo(settings.swarm_gateway_url)
            )
            if settings.chain_enabled:
                if chain_client:
                    logger.info("Chain: %s (wallet connected)", chain_client.chain)
                elif CHAIN_AVAILABLE and _chain_init_error:
                    logger.info(
                        "Chain: writes disabled — %s", redact_urls(_chain_init_error)
                    )
                elif CHAIN_AVAILABLE:
                    logger.info("Chain: %s (read-only, no wallet)", settings.chain_name)
            await server.run(
                read_stream, write_stream, server.create_initialization_options()
            )
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    finally:
        cleanup()


def main_sync():
    """Synchronous entry point for CLI script."""
    asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())
