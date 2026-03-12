"""MCP server implementation for Swarm stamp management."""

import asyncio
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlparse

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    Tool,
    TextContent,
    CallToolRequest,
    CallToolResult,
    ListToolsRequest,
    Prompt,
    PromptArgument,
    PromptMessage,
    GetPromptResult,
)
from requests.exceptions import RequestException

from .config import settings
from .gateway_client import SwarmGatewayClient

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global gateway client instance
gateway_client = SwarmGatewayClient()

# Chain client (initialized when chain_enabled=true and dependencies available)
chain_client = None
CHAIN_AVAILABLE = False

if settings.chain_enabled:
    try:
        from .chain import CHAIN_AVAILABLE as _chain_avail, ChainClient

        CHAIN_AVAILABLE = _chain_avail
        if CHAIN_AVAILABLE:
            chain_client = ChainClient(
                chain=settings.chain_name,
                rpc_url=settings.chain_rpc_url,
                contract_address=settings.chain_contract_address,
                private_key=settings.provenance_wallet_key,
                explorer_url=settings.chain_explorer_url,
                gas_limit=settings.chain_gas_limit,
            )
            logger.info(
                "Chain client initialized: chain=%s contract=%s",
                chain_client.chain,
                chain_client.contract_address,
            )
        else:
            logger.warning(
                "Chain enabled but blockchain dependencies not installed. "
                "Run: pip install -e .[blockchain]"
            )
    except Exception as e:
        logger.warning("Chain client initialization failed: %s", e)
        CHAIN_AVAILABLE = False

# Agent-facing instructions sent during MCP initialization handshake
MCP_INSTRUCTIONS = """
Swarm Provenance MCP — decentralized storage with cryptographic provenance on the Swarm network.

PAYMENT MODEL:
The gateway uses x402 payment protocol. Free tier is configured automatically (rate limit: 3 write requests/minute). Read operations are always free and unlimited.

TYPICAL WORKFLOW:
1. health_check — verify gateway is reachable
2. list_stamps — find existing usable stamps
3. If no usable stamp: purchase_stamp, then wait ~1 minute for blockchain propagation
4. check_stamp_health — verify stamp is ready for uploads
5. upload_data — store data, get a reference hash back
6. download_data — retrieve data using the reference hash

KEY CONSTRAINTS:
- Max upload size: 4KB (4096 bytes) per request
- After purchase_stamp or extend_stamp, wait ~1 minute before using the stamp
- Stamp depth controls capacity: 17 (small, ~35KB), 20 (medium, ~500MB), 22 (large, ~6GB)
- Stamps are rented storage — data expires when the stamp TTL runs out
- A stamp can be reused for multiple uploads until capacity or TTL is exhausted

TOOL RELATIONSHIPS:
- purchase_stamp → returns stamp_id → use with upload_data, check_stamp_health, get_stamp_status, extend_stamp
- upload_data → returns reference hash → use with download_data
- check_stamp_health gives detailed diagnostics; get_stamp_status gives raw stamp data
- extend_stamp increases TTL (not capacity)

ERRORS:
- "Not usable": stamp expired or not yet propagated — wait or purchase new
- HTTP 404 on stamp: may be newly purchased, wait ~1 minute and retry
- Size exceeded: data > 4KB, reduce payload before upload

CHAIN ANCHORING (optional):
When chain_enabled=true and blockchain dependencies are installed, on-chain provenance tools become available.
These register Swarm hashes in the DataProvenance smart contract on Base Sepolia, creating an immutable on-chain record.
- chain_health — test RPC connectivity (no wallet needed)
- chain_balance — check wallet ETH balance with funding guidance when low
- anchor_hash — register a Swarm hash on-chain (costs gas, requires funded wallet)
- verify_hash — check if a Swarm hash is registered on-chain (read-only, no gas)
- get_provenance — retrieve full provenance record: owner, timestamp, data type, status, transformations, accessors (read-only)
- Additional chain tools (record_transform, get_provenance_chain, etc.) will be added when enabled.
The health_check tool reports chain status alongside gateway status.

COMPANION SERVERS:
- swarm_connect gateway (required) — the FastAPI gateway this server talks to, handles Bee node communication
- fds-id MCP (optional) — identity and signing server for cryptographic provenance chain anchoring
The health_check tool reports gateway connectivity. For full provenance workflows with signed data, an fds-id server is needed but not required for basic storage.
""".strip()

# Validation patterns
STAMP_ID_PATTERN = re.compile(r"^[a-fA-F0-9]{64}$")
REFERENCE_HASH_PATTERN = re.compile(r"^[a-fA-F0-9]{64}$")


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
    if not STAMP_ID_PATTERN.match(stamp_id):
        raise ValueError(
            f"Invalid stamp ID format. Expected 64-character hexadecimal string (without 0x prefix), got: {stamp_id}"
        )

    return stamp_id


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
    if not REFERENCE_HASH_PATTERN.match(reference):
        raise ValueError(
            f"Invalid reference hash format. Expected 64-character hexadecimal string (without 0x prefix), got: {reference}"
        )

    return reference


def validate_stamp_amount(amount: int) -> None:
    """Validate stamp amount.

    Args:
        amount: The amount to validate

    Raises:
        ValueError: If amount is invalid
    """
    if amount < 1000000:
        raise ValueError(f"Stamp amount must be at least 1,000,000 wei, got: {amount}")


def validate_stamp_depth(depth: int) -> None:
    """Validate stamp depth.

    Args:
        depth: The depth to validate

    Raises:
        ValueError: If depth is invalid
    """
    if not (17 <= depth <= 22):
        raise ValueError(
            f"Stamp depth must be 17 (small), 20 (medium), or 22 (large), got: {depth}"
        )


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


def _format_error(
    message: str, retryable: bool, next_tool: Optional[str] = None
) -> str:
    """Format structured error message with retryable flag and recovery hint."""
    lines = message
    lines += f"\n\nretryable: {str(retryable).lower()}"
    if next_tool:
        lines += f"\n_next: {next_tool}"
    return lines


def _is_retryable_error(e: Exception) -> bool:
    """Determine if an exception represents a transient, retryable error."""
    from requests.exceptions import ConnectionError as ReqConnectionError, Timeout

    if isinstance(e, (ReqConnectionError, Timeout)):
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


# Chain balance thresholds and funding URLs
_MIN_BALANCE_WEI = 10**14  # 0.0001 ETH — cannot reliably transact
_LOW_BALANCE_WEI = 10**15  # 0.001 ETH — may run out soon
_FAUCET_URLS = {
    "base-sepolia": "https://www.alchemy.com/faucets/base-sepolia",
}
_BRIDGE_URL = "https://bridge.base.org"


def _mask_rpc_url(url: str) -> str:
    """Extract hostname from RPC URL for display (hide full path/keys)."""
    try:
        parsed = urlparse(url)
        return parsed.hostname or url
    except Exception:
        return url


def _format_funding_guidance(address: str, balance_wei: int, chain: str) -> str:
    """Format actionable funding guidance based on wallet balance.

    Returns empty string if balance is healthy, a warning if low,
    or a critical message with funding instructions if insufficient.
    """
    if balance_wei >= _LOW_BALANCE_WEI:
        return ""

    is_testnet = chain in _FAUCET_URLS
    balance_eth = balance_wei / 10**18

    if balance_wei < _MIN_BALANCE_WEI:
        msg = f"\n\n⚠️  CRITICAL: Wallet balance too low for transactions"
        msg += f"\n   Balance: {balance_eth:.6f} ETH (minimum ~0.0001 ETH needed)"
        msg += f"\n   Wallet: {address}"
    else:
        msg = f"\n\n⚠️  WARNING: Wallet balance is low"
        msg += f"\n   Balance: {balance_eth:.6f} ETH (may run out soon)"
        msg += f"\n   Wallet: {address}"

    if is_testnet:
        faucet_url = _FAUCET_URLS[chain]
        msg += f"\n   Fund with testnet ETH: {faucet_url}"
    else:
        msg += f"\n   Bridge ETH to Base: {_BRIDGE_URL}"

    return msg


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
                description="Purchase a new Swarm postage stamp. Returns a 64-character hex batch ID that can be used with upload_data. A stamp can be reused for multiple uploads until its capacity or TTL is exhausted. After purchase, wait ~1 minute for blockchain propagation before using the stamp.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "amount": {
                            "type": "integer",
                            "description": f"Amount in wei — controls TTL (time-to-live). Higher amount = longer before the stamp expires (default: {settings.default_stamp_amount})",
                            "default": settings.default_stamp_amount,
                            "minimum": 1000000,
                        },
                        "depth": {
                            "type": "integer",
                            "description": f"Depth — controls storage capacity. Three practical sizes: 17 (small, ~35KB), 20 (medium, ~500MB), 22 (large, ~6GB). Capacities are approximate effective volumes with erasure coding. Higher depth costs more (default: {settings.default_stamp_depth})",
                            "default": settings.default_stamp_depth,
                            "minimum": 17,
                            "maximum": 22,
                        },
                        "label": {
                            "type": "string",
                            "description": "Optional human-readable label for easier stamp identification",
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
                description="List all available postage stamps with their details including batch IDs, amounts, depths, TTL, expiration times, and utilization. Useful for finding a usable stamp for upload_data or identifying stamps that need extending.",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            Tool(
                name="extend_stamp",
                description="Extend an existing stamp by adding funds to increase its TTL (time-to-live). This extends the expiration date but does NOT increase storage capacity (depth). Changes take ~1 minute to propagate through the blockchain.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "stamp_id": {
                            "type": "string",
                            "description": "The 64-character hexadecimal batch ID of the stamp to extend (without 0x prefix). Example: a1b2c3d4e5f6789abcdef0123456789abcdef0123456789abcdef0123456789a",
                            "pattern": "^[a-fA-F0-9]{64}$",
                        },
                        "amount": {
                            "type": "integer",
                            "description": "Additional amount to add to the stamp in wei. This will extend the stamp's TTL proportionally.",
                            "minimum": 1000000,
                        },
                    },
                    "required": ["stamp_id", "amount"],
                },
            ),
            Tool(
                name="upload_data",
                description="Upload data to the Swarm network using a valid postage stamp. Requires a stamp_id from purchase_stamp (wait ~1 min after purchase). Max 4KB per upload. Returns a 64-char hex reference hash — pass it to download_data to retrieve the content later.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "data": {
                            "type": "string",
                            "description": "Data content to upload as a string (max 4096 bytes). Can be JSON, text, or any string data.",
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
                    },
                    "required": ["data", "stamp_id"],
                },
            ),
            Tool(
                name="download_data",
                description="Download data from the Swarm network using a reference hash. The reference hash is returned by upload_data after a successful upload. Returns the decoded content for text/JSON, or size and type metadata for binary data.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "reference": {
                            "type": "string",
                            "description": "64-character hexadecimal Swarm reference hash (without 0x prefix). Example: b2c3d4e5f6789abcdef0123456789abcdef0123456789abcdef0123456789ab",
                            "pattern": "^[a-fA-F0-9]{64}$",
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
                        description="Register a Swarm reference hash on the blockchain, creating an immutable provenance record with owner, timestamp, and data type. Costs gas — requires a funded wallet (check with chain_balance). If the hash is already registered, returns the existing record without error.",
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
                            },
                            "required": ["swarm_hash"],
                        },
                    ),
                    Tool(
                        name="verify_hash",
                        description="Check whether a Swarm reference hash is registered on the blockchain. Returns verified status with basic provenance info (owner, timestamp, data type) if found. Read-only — no gas or wallet key required.",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "swarm_hash": {
                                    "type": "string",
                                    "description": "64-character hexadecimal Swarm reference hash to verify (without 0x prefix)",
                                    "pattern": "^[a-fA-F0-9]{64}$",
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
                ]
            )

        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: Dict[str, Any]) -> CallToolResult:
        """Handle tool calls."""
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
            logger.error(f"Error in tool {name}: {e}", exc_info=True)
            retryable = _is_retryable_error(e)
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=_format_error(
                            f"Error executing {name}: {str(e)}",
                            retryable=retryable,
                            next_tool="health_check",
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
                description="Upload data with provenance to Swarm. Guides through health check, stamp selection/purchase, upload, and verification.",
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
                description="Verify existing provenance data by downloading and inspecting it from Swarm.",
                arguments=[
                    PromptArgument(
                        name="reference",
                        description="64-character hex Swarm reference hash to verify",
                        required=True,
                    ),
                ],
            ),
            Prompt(
                name="stamp-management",
                description="Review and manage stamp inventory — list stamps, check health, extend or purchase as needed.",
                arguments=[],
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
                                f"1. Call health_check to verify the gateway is reachable and check stamp availability\n"
                                f"2. If ready=true, pick a usable stamp from the health_check response. "
                                f"Otherwise call purchase_stamp (depth 17 for small data) and wait ~1 minute\n"
                                f"3. Call check_stamp_health on the chosen stamp to confirm it's ready\n"
                                f"4. Call upload_data with the data and stamp_id\n"
                                f"5. Call download_data with the returned reference to verify the upload\n"
                                f"6. Report the reference hash — this is the permanent Swarm address"
                            ),
                        ),
                    )
                ],
            )

        elif name == "provenance-verify":
            reference = args.get("reference", "<reference hash>")
            return GetPromptResult(
                description="Verify existing provenance data from Swarm",
                messages=[
                    PromptMessage(
                        role="user",
                        content=TextContent(
                            type="text",
                            text=(
                                f"Verify this provenance data from Swarm:\n\n"
                                f"Reference: {reference}\n\n"
                                f"Steps:\n"
                                f"1. Call download_data with the reference hash\n"
                                f"2. Inspect the returned content — check structure, fields, and integrity\n"
                                f"3. Report what was found: content type, size, key fields, "
                                f"and whether the data appears intact"
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
                                "Steps:\n"
                                "1. Call list_stamps to get all stamps\n"
                                "2. For each stamp, check if it's usable, its utilization, and TTL\n"
                                "3. Call check_stamp_health on any stamp that looks concerning\n"
                                "4. Recommend actions:\n"
                                "   - Stamps with high utilization → purchase a new one\n"
                                "   - Stamps with low TTL → extend_stamp to add time\n"
                                "   - No usable stamps → purchase_stamp\n"
                                "5. Summarize the inventory status and recommended actions"
                            ),
                        ),
                    )
                ],
            )

        else:
            raise ValueError(f"Unknown prompt: {name}")

    return server


async def handle_purchase_stamp(arguments: Dict[str, Any]) -> CallToolResult:
    """Handle stamp purchase requests."""
    try:
        amount = arguments.get("amount", settings.default_stamp_amount)
        depth = arguments.get("depth", settings.default_stamp_depth)
        label = arguments.get("label")

        # Validate inputs
        validate_stamp_amount(amount)
        validate_stamp_depth(depth)

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

        result = gateway_client.purchase_stamp(amount, depth, label)

        # Check if purchase was actually successful
        batch_id = result.get("batchID")
        if not batch_id:
            error_msg = f"❌ Stamp purchase failed - no stamp ID returned!\n\nGateway response: {result}"
            logger.error(f"Purchase failed - missing batchID in response: {result}")
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=_format_error(
                            error_msg, retryable=True, next_tool="health_check"
                        ),
                    )
                ],
                isError=True,
            )

        response_text = f"🎉 Stamp purchased successfully!\n\n"
        response_text += f"📋 Your Stamp Details:\n"
        response_text += f"   Batch ID: `{batch_id}`\n"
        response_text += f"   Amount: {amount:,} wei\n"
        response_text += f"   Depth: {depth}\n"
        if label:
            response_text += f"   Label: {label}\n"
        response_text += f"\n✅ Stamp ID: `{batch_id}` (immediately available)\n"
        response_text += f"⏱️  IMPORTANT: Wait ~1 minute before using this stamp!\n"
        response_text += f"📋 The stamp info must propagate through the blockchain before it can be used for uploads."
        response_text += _format_hints(
            "check_stamp_health", ["get_stamp_status", "upload_data", "list_stamps"]
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
                        error_msg, retryable=False, next_tool="purchase_stamp"
                    ),
                )
            ],
            isError=True,
        )
    except RequestException as e:
        error_msg = f"Failed to purchase stamp: {str(e)}"
        logger.error(error_msg)
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        error_msg,
                        retryable=_is_retryable_error(e),
                        next_tool="health_check",
                    ),
                )
            ],
            isError=True,
        )


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
        response_text += f"Amount: {result.get('amount', 'N/A')}\n"
        response_text += f"Depth: {result.get('depth', 'N/A')}\n"
        response_text += f"Bucket Depth: {result.get('bucketDepth', 'N/A')}\n"
        response_text += f"Block Number: {result.get('blockNumber', 'N/A')}\n"

        # Enhanced TTL information
        batch_ttl = result.get("batchTTL", "N/A")
        if batch_ttl != "N/A":
            response_text += (
                f"Batch TTL: {batch_ttl:,} seconds ({batch_ttl/86400:.1f} days)\n"
            )
        else:
            response_text += f"Batch TTL: {batch_ttl}\n"

        response_text += (
            f"Expected Expiration: {result.get('expectedExpiration', 'N/A')}\n"
        )

        # Enhanced usability information
        usable = result.get("usable", "N/A")
        response_text += f"Usable: {usable}"
        if usable is False:
            response_text += " ⚠️  (Cannot be used for uploads)"
        elif usable is True:
            response_text += " ✅ (Ready for uploads)"
        response_text += "\n"

        utilization = result.get("utilization", "N/A")
        if utilization != "N/A" and isinstance(utilization, (int, float)):
            response_text += f"Utilization: {utilization}%\n"
        else:
            response_text += f"Utilization: {utilization}\n"

        response_text += f"Immutable: {result.get('immutableFlag', 'N/A')}\n"
        response_text += f"Local: {result.get('local', 'N/A')}\n"

        if result.get("label"):
            response_text += f"Label: {result['label']}\n"

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
                        next_tool="health_check",
                    ),
                )
            ],
            isError=True,
        )


async def handle_list_stamps(arguments: Dict[str, Any]) -> CallToolResult:
    """Handle stamp listing requests."""
    try:
        result = gateway_client.list_stamps()
        stamps = result.get("stamps", [])
        total_count = result.get("total_count", 0)

        has_usable = False
        if total_count == 0:
            response_text = "📭 No stamps found.\n\n💡 Use the 'purchase_stamp' tool to create your first stamp!"
        else:
            response_text = f"📋 Found {total_count} stamp(s):\n\n"

            # Header for table format
            response_text += f"{'Batch ID':<20} | {'Expiration':<20} | {'Status':<10}\n"
            response_text += f"{'-'*20} | {'-'*20} | {'-'*10}\n"

            for stamp in stamps:
                batch_id = stamp.get("batchID", "N/A")
                expiration = stamp.get("expectedExpiration", "N/A")
                usable = stamp.get("usable", "N/A")

                if usable is True:
                    has_usable = True

                # Truncate batch ID for table format
                display_id = (
                    batch_id[:16] + "..." if len(str(batch_id)) > 19 else batch_id
                )

                # Status with emoji
                if usable is True:
                    status = "✅ Usable"
                elif usable is False:
                    status = "❌ Expired"
                else:
                    status = "❓ Unknown"

                response_text += (
                    f"{display_id:<20} | {str(expiration):<20} | {status:<10}\n"
                )

        # Contextual hints
        if has_usable:
            response_text += _format_hints(
                "upload_data", ["get_stamp_status", "check_stamp_health"]
            )
        else:
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
                        next_tool="health_check",
                    ),
                )
            ],
            isError=True,
        )


async def handle_extend_stamp(arguments: Dict[str, Any]) -> CallToolResult:
    """Handle stamp extension requests."""
    try:
        stamp_id = arguments.get("stamp_id")
        amount = arguments.get("amount")

        if not stamp_id:
            raise ValueError("Stamp ID is required")
        if not amount:
            raise ValueError("Amount is required")

        # Validate inputs
        clean_stamp_id = validate_and_clean_stamp_id(stamp_id)
        validate_stamp_amount(amount)

        result = gateway_client.extend_stamp(clean_stamp_id, amount)

        response_text = f"✅ Stamp extended successfully!\n\n"
        response_text += f"📋 Extension Details:\n"
        batch_id = result.get("batchID", "N/A")
        response_text += f"   Batch ID: `{batch_id}`\n"
        response_text += f"   Additional Amount: {amount:,} wei\n"
        response_text += f"   Status: {result.get('message', 'Extended')}\n\n"
        response_text += f"⏱️  Important: Extension info takes ~1 minute to propagate through the blockchain.\n"
        response_text += f"🔍 Check stamp status again in about 1 minute to see the new expiration time."
        response_text += _format_hints("check_stamp_health", ["get_stamp_status"])

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
        error_msg = f"Failed to extend stamp: {str(e)}"
        logger.error(error_msg)
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        error_msg,
                        retryable=_is_retryable_error(e),
                        next_tool="health_check",
                    ),
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
            if not stamp_details.get("usable", False):
                return CallToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=_format_error(
                                f"Stamp {clean_stamp_id} exists on this gateway but is not usable for uploads. "
                                f"Please use a different stamp or create a new one.",
                                retryable=False,
                                next_tool="purchase_stamp",
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
        result = gateway_client.upload_data(data, clean_stamp_id, content_type)

        response_text = f"🎉 Data uploaded successfully to Swarm!\n\n"
        response_text += f"📄 Upload Details:\n"
        response_text += f"   Size: {len(data.encode('utf-8')):,} bytes\n"
        response_text += f"   Content Type: {content_type}\n"
        response_text += f"   Stamp Used: `{clean_stamp_id}`\n\n"
        response_text += f"🔗 Retrieval Information:\n"
        response_text += f"   Reference Hash: `{result['reference']}`\n"
        response_text += f"   💡 Copy this reference hash to download your data later using the 'download_data' tool."

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
    except RequestException as e:
        error_msg = f"Failed to upload data: {str(e)}"
        logger.error(error_msg)
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        error_msg,
                        retryable=_is_retryable_error(e),
                        next_tool="health_check",
                    ),
                )
            ],
            isError=True,
        )


async def handle_download_data(arguments: Dict[str, Any]) -> CallToolResult:
    """Handle data download requests."""
    try:
        reference = arguments.get("reference")
        if not reference:
            raise ValueError("Reference is required")

        # Validate and clean reference hash
        clean_reference = validate_and_clean_reference_hash(reference)

        result_bytes = gateway_client.download_data(clean_reference)

        # Try to decode as text, handle JSON appropriately
        try:
            result_text = result_bytes.decode("utf-8")

            # Try to parse as JSON for better presentation
            try:
                import json

                parsed_json = json.loads(result_text)

                response_text = f"📥 Successfully downloaded JSON data from `{clean_reference}`:\n\n"

                # Show JSON structure with field truncation
                response_text += "📋 JSON Structure:\n"
                for key, value in parsed_json.items():
                    if isinstance(value, str) and len(value) > 50:
                        truncated_value = value[:47] + "..."
                        response_text += f'   {key}: "{truncated_value}"\n'
                    elif isinstance(value, dict):
                        response_text += (
                            f"   {key}: {{...}} (object with {len(value)} fields)\n"
                        )
                    elif isinstance(value, list):
                        response_text += (
                            f"   {key}: [...] (array with {len(value)} items)\n"
                        )
                    else:
                        response_text += f"   {key}: {value}\n"

                response_text += f"\n💾 Size: {len(result_bytes):,} bytes"

            except json.JSONDecodeError:
                # Not JSON, show as text
                response_text = f"📥 Successfully downloaded text data from `{clean_reference}`:\n\n{result_text}"

        except UnicodeDecodeError:
            # If not valid UTF-8, show as binary data info
            response_text = (
                f"📥 Successfully downloaded binary data from `{clean_reference}`\n\n"
            )
            response_text += f"📊 File Information:\n"
            response_text += f"   Size: {len(result_bytes):,} bytes\n"
            response_text += f"   Type: Binary data\n\n"
            response_text += f"💡 This appears to be binary data (images, documents, etc.). To save it, you would need to write the bytes to a file."

        response_text += _format_hints("upload_data", ["list_stamps"])

        return CallToolResult(content=[TextContent(type="text", text=response_text)])

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
        error_msg = f"Failed to download data: {str(e)}"
        logger.error(error_msg)
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        error_msg,
                        retryable=_is_retryable_error(e),
                        next_tool="health_check",
                    ),
                )
            ],
            isError=True,
        )


async def handle_health_check(arguments: Dict[str, Any]) -> CallToolResult:
    """Handle health check requests with adaptive status.

    Returns a comprehensive status including gateway connectivity, stamp
    availability, and actionable recommendations for the agent's next step.
    """
    ready = False
    recommendations = []
    next_tool = "list_stamps"

    try:
        result = gateway_client.health_check()

        status = result.get("status", "unknown")
        gateway_url = result.get("gateway_url", "N/A")
        response_time = result.get("response_time_ms", "N/A")

        gateway_ok = status == "healthy"

        if gateway_ok:
            response_text = f"✅ Gateway operational\n\n"
            response_text += f"🌐 Gateway: {gateway_url}\n"
            if isinstance(response_time, (int, float)):
                response_text += f"⚡ Response Time: {response_time:.0f}ms\n"
        else:
            response_text = f"⚠️  Gateway issues detected\n\n"
            response_text += f"Status: {status}\n"
            response_text += f"Gateway: {gateway_url}\n"
            if isinstance(response_time, (int, float)):
                response_text += f"Response Time: {response_time:.0f}ms\n"
            recommendations.append(
                "Gateway not healthy — check connectivity or try again later"
            )
            next_tool = "health_check"

        if result.get("gateway_response"):
            response_text += f"\n📋 Gateway Response: {result['gateway_response']}\n"

        # Adaptive: also check stamp availability
        stamps_info = ""
        usable_count = 0
        total_stamps = 0
        if gateway_ok:
            try:
                stamps_result = gateway_client.list_stamps()
                stamps = stamps_result.get("stamps", [])
                total_stamps = len(stamps)
                usable_count = sum(1 for s in stamps if s.get("usable") is True)

                stamps_info += (
                    f"\n📋 Stamps: {usable_count} usable / {total_stamps} total\n"
                )

                if usable_count > 0:
                    ready = True
                    next_tool = "upload_data"
                elif total_stamps > 0:
                    recommendations.append(
                        f"Found {total_stamps} stamp(s) but none are usable — purchase a new one or wait for propagation"
                    )
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

        # Summary
        response_text += f"\nready: {str(ready).lower()}"

        if recommendations:
            response_text += "\n_recommendations:"
            for rec in recommendations:
                response_text += f"\n  - {rec}"

        # Chain anchoring status
        if settings.chain_enabled:
            if CHAIN_AVAILABLE and chain_client:
                try:
                    chain_client.health_check()
                    response_text += f"\n\n⛓️  Chain: {chain_client.chain} (connected)"
                    response_text += f"\n   Contract: {chain_client.contract_address}"
                    response_text += f"\n   Wallet: {chain_client.address}"
                except Exception as chain_err:
                    response_text += (
                        f"\n\n⛓️  Chain: {settings.chain_name} (error: {chain_err})"
                    )
                    recommendations.append(
                        "Chain anchoring enabled but RPC unreachable"
                    )
            else:
                response_text += (
                    f"\n\n⛓️  Chain: enabled but dependencies not installed"
                )
                recommendations.append(
                    "Install blockchain deps: pip install -e .[blockchain]"
                )

        # Cross-server coordination info
        response_text += f"\n_companion_servers:"
        response_text += f"\n  - swarm_connect gateway: {gateway_url} (required, {'connected' if gateway_ok else 'unreachable'})"
        response_text += f"\n  - fds-id MCP: optional (identity/signing for provenance chain anchoring)"

        response_text += f"\n\n_next: {next_tool}"
        response_text += f"\n_related: list_stamps, purchase_stamp, get_wallet_info"

        return CallToolResult(content=[TextContent(type="text", text=response_text)])

    except RequestException as e:
        gateway_url = settings.swarm_gateway_url
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
                        next_tool="health_check",
                    ),
                )
            ],
            isError=True,
        )


async def handle_check_stamp_health(arguments: Dict[str, Any]) -> CallToolResult:
    """Handle stamp health check requests."""
    try:
        stamp_id = arguments.get("stamp_id")
        if not stamp_id:
            raise ValueError("Stamp ID is required")

        clean_stamp_id = validate_and_clean_stamp_id(stamp_id)
        result = gateway_client.check_stamp_health(clean_stamp_id)

        can_upload = result.get("can_upload", False)
        errors = result.get("errors", [])
        warnings = result.get("warnings", [])
        status = result.get("status", {})

        if can_upload:
            response_text = f"✅ Stamp {clean_stamp_id[:16]}... is healthy and ready for uploads.\n\n"
        else:
            response_text = (
                f"❌ Stamp {clean_stamp_id[:16]}... cannot be used for uploads.\n\n"
            )

        if errors:
            response_text += "Errors:\n"
            for err in errors:
                response_text += (
                    f"   [{err.get('code', '?')}] {err.get('message', '')}\n"
                )
                if err.get("suggestion"):
                    response_text += f"   → {err['suggestion']}\n"

        if warnings:
            response_text += "Warnings:\n"
            for warn in warnings:
                response_text += (
                    f"   [{warn.get('code', '?')}] {warn.get('message', '')}\n"
                )
                if warn.get("suggestion"):
                    response_text += f"   → {warn['suggestion']}\n"

        if status:
            response_text += f"\nStatus:\n"
            if status.get("utilizationPercent") is not None:
                response_text += f"   Utilization: {status['utilizationPercent']}% ({status.get('utilizationStatus', 'unknown')})\n"
            if status.get("batchTTL") is not None:
                ttl = status["batchTTL"]
                response_text += f"   TTL: {ttl:,} seconds ({ttl/86400:.1f} days)\n"
            if status.get("expectedExpiration"):
                response_text += f"   Expires: {status['expectedExpiration']}\n"

        # Contextual hints based on health
        if can_upload:
            response_text += _format_hints(
                "upload_data", ["get_stamp_status", "extend_stamp"]
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
                        next_tool="health_check",
                    ),
                )
            ],
            isError=True,
        )


async def handle_get_wallet_info(arguments: Dict[str, Any]) -> CallToolResult:
    """Handle wallet info requests."""
    try:
        result = gateway_client.get_wallet_info()

        response_text = f"Wallet Information:\n"
        response_text += f"   Address: {result.get('walletAddress', 'N/A')}\n"
        response_text += f"   BZZ Balance: {result.get('bzzBalance', 'N/A')}\n"
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
                        next_tool="health_check",
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
                        "Chain module not available. Install blockchain dependencies: pip install -e .[blockchain]",
                        retryable=False,
                    ),
                )
            ],
            isError=True,
        )
    if not chain_client:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        "Chain client not initialized. Set PROVENANCE_WALLET_KEY to enable wallet operations.",
                        retryable=False,
                        next_tool="chain_health",
                    ),
                )
            ],
            isError=True,
        )

    try:
        wallet_info = chain_client.balance()

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
                        f"Failed to get chain balance: {str(e)}",
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
                        "Chain module not available. Install blockchain dependencies: pip install -e .[blockchain]",
                        retryable=False,
                    ),
                )
            ],
            isError=True,
        )

    try:
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
            )

        start = time.monotonic()
        provider.health_check()
        elapsed_ms = (time.monotonic() - start) * 1000

        block_number = provider.get_block_number()

        response_text = f"⛓️  Chain RPC Health\n\n"
        response_text += f"   Connected: true\n"
        response_text += f"   Chain: {provider.chain}\n"
        response_text += f"   Chain ID: {provider.chain_id}\n"
        response_text += f"   Latest Block: {block_number:,}\n"
        response_text += f"   RPC Response: {elapsed_ms:.0f}ms\n"
        response_text += f"   Contract: {provider.contract_address}\n"
        response_text += f"   RPC: {_mask_rpc_url(provider.rpc_url)}"
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
        error_msg += f"   Error: {str(e)}"

        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        error_msg,
                        retryable=is_connection_error,
                        next_tool="health_check",
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
                        "Chain module not available. Install blockchain dependencies: pip install -e .[blockchain]",
                        retryable=False,
                    ),
                )
            ],
            isError=True,
        )
    if not chain_client:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        "Chain client not initialized. Set PROVENANCE_WALLET_KEY to enable wallet operations.",
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

        owner = arguments.get("owner")

        if owner:
            result = chain_client.anchor_for(clean_hash, owner, data_type)
        else:
            result = chain_client.anchor(clean_hash, data_type)

        response_text = f"⛓️  Hash anchored on-chain\n\n"
        response_text += f"   Swarm Hash: {result.swarm_hash}\n"
        response_text += f"   Data Type: {result.data_type}\n"
        response_text += f"   Owner: {result.owner}\n"
        response_text += f"   Tx Hash: {result.tx_hash}\n"
        response_text += f"   Block: {result.block_number:,}\n"
        response_text += f"   Gas Used: {result.gas_used:,}"
        if result.explorer_url:
            response_text += f"\n   Explorer: {result.explorer_url}"

        # Post-tx balance check
        try:
            wallet_info = chain_client.balance()
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
                        f"Validation error: {str(e)}",
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
                        text=_format_error(
                            f"Chain error: {str(e)}", retryable=False
                        ),
                    )
                ],
                isError=True,
            )

        if isinstance(e, DataAlreadyRegisteredError):
            # Intentionally NOT isError — idempotent anchoring: if the hash is
            # already on-chain the agent should see the existing record and proceed.
            from datetime import datetime, timezone

            timestamp_str = "unknown"
            if e.timestamp:
                try:
                    dt = datetime.fromtimestamp(e.timestamp, tz=timezone.utc)
                    timestamp_str = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
                except (ValueError, OSError):
                    timestamp_str = str(e.timestamp)

            response_text = f"⛓️  Hash already registered on-chain\n\n"
            response_text += f"   Swarm Hash: {e.data_hash}\n"
            response_text += f"   Owner: {e.owner}\n"
            response_text += f"   Registered: {timestamp_str}\n"
            response_text += f"   Data Type: {e.data_type}"
            response_text += _format_hints(
                "download_data", ["chain_balance", "health_check"]
            )
            return CallToolResult(
                content=[TextContent(type="text", text=response_text)]
            )

        if isinstance(e, ChainTransactionError):
            msg = f"Transaction failed: {str(e)}"
            if e.tx_hash:
                msg += f"\n   Tx Hash: {e.tx_hash}"
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
                            f"Chain connection error: {str(e)}",
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
                            f"Chain validation error: {str(e)}",
                            retryable=False,
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
                    text=_format_error(
                        f"Chain error: {str(e)}", retryable=False
                    ),
                )
            ],
            isError=True,
        )


async def handle_verify_hash(arguments: Dict[str, Any]) -> CallToolResult:
    """Handle verify_hash requests — check if a Swarm hash is registered on-chain.

    Read-only: works without PROVENANCE_WALLET_KEY by creating a temporary
    provider + contract for direct contract reads (no signing needed).
    """
    if not CHAIN_AVAILABLE:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        "Chain module not available. Install blockchain dependencies: pip install -e .[blockchain]",
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

        # Use chain_client if available, otherwise create temporary provider + contract
        if chain_client:
            is_registered = chain_client.verify(clean_hash)
            if is_registered:
                record = chain_client.get(clean_hash)
            else:
                record = None
        else:
            from .chain.provider import ChainProvider
            from .chain.contract import DataProvenanceContract
            from .chain.exceptions import DataNotRegisteredError

            provider = ChainProvider(
                chain=settings.chain_name,
                rpc_url=settings.chain_rpc_url,
                contract_address=settings.chain_contract_address,
                explorer_url=settings.chain_explorer_url,
            )
            contract = DataProvenanceContract(
                web3=provider.web3,
                contract_address=provider.contract_address,
            )
            raw = contract.get_data_record(clean_hash)
            zero_address = "0x" + "0" * 40
            if raw[1] == zero_address:
                is_registered = False
                record = None
            else:
                is_registered = True
                from .chain.models import (
                    ChainProvenanceRecord,
                    ChainTransformation,
                    DataStatusEnum,
                )

                record = ChainProvenanceRecord(
                    data_hash=(
                        raw[0].hex() if isinstance(raw[0], bytes) else str(raw[0])
                    ),
                    owner=raw[1],
                    timestamp=raw[2],
                    data_type=raw[3],
                    status=DataStatusEnum(raw[6]),
                    accessors=list(raw[5]),
                    transformations=[
                        ChainTransformation(description=str(t)) for t in raw[4]
                    ],
                )

        if is_registered and record:
            from datetime import datetime, timezone

            timestamp_str = "unknown"
            if record.timestamp:
                try:
                    dt = datetime.fromtimestamp(record.timestamp, tz=timezone.utc)
                    timestamp_str = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
                except (ValueError, OSError):
                    timestamp_str = str(record.timestamp)

            response_text = f"⛓️  Verified — hash IS registered on-chain\n\n"
            response_text += f"   Swarm Hash: {clean_hash}\n"
            response_text += f"   Owner: {record.owner}\n"
            response_text += f"   Registered: {timestamp_str}\n"
            response_text += f"   Data Type: {record.data_type}\n"
            response_text += f"   Status: {record.status.name}"
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
                    text=_format_error(
                        f"Validation error: {str(e)}", retryable=False
                    ),
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
                        f"Chain error: {str(e)}",
                        retryable=is_conn_error,
                        next_tool="chain_health" if is_conn_error else None,
                    ),
                )
            ],
            isError=True,
        )


async def handle_get_provenance(arguments: Dict[str, Any]) -> CallToolResult:
    """Handle get_provenance requests — retrieve full on-chain provenance record.

    Read-only: works without PROVENANCE_WALLET_KEY by creating a temporary
    provider + contract for direct contract reads (no signing needed).
    """
    if not CHAIN_AVAILABLE:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        "Chain module not available. Install blockchain dependencies: pip install -e .[blockchain]",
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

        # Use chain_client if available, otherwise create temporary provider + contract
        if chain_client:
            record = chain_client.get(clean_hash)
        else:
            from .chain.provider import ChainProvider
            from .chain.contract import DataProvenanceContract
            from .chain.exceptions import DataNotRegisteredError
            from .chain.models import (
                ChainProvenanceRecord,
                ChainTransformation,
                DataStatusEnum,
            )

            provider = ChainProvider(
                chain=settings.chain_name,
                rpc_url=settings.chain_rpc_url,
                contract_address=settings.chain_contract_address,
                explorer_url=settings.chain_explorer_url,
            )
            contract = DataProvenanceContract(
                web3=provider.web3,
                contract_address=provider.contract_address,
            )
            raw = contract.get_data_record(clean_hash)
            zero_address = "0x" + "0" * 40
            if raw[1] == zero_address:
                raise DataNotRegisteredError(
                    f"Data hash {clean_hash} is not registered on-chain",
                    data_hash=clean_hash,
                )
            record = ChainProvenanceRecord(
                data_hash=(
                    raw[0].hex() if isinstance(raw[0], bytes) else str(raw[0])
                ),
                owner=raw[1],
                timestamp=raw[2],
                data_type=raw[3],
                status=DataStatusEnum(raw[6]),
                accessors=list(raw[5]),
                transformations=[
                    ChainTransformation(description=str(t)) for t in raw[4]
                ],
            )

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
        response_text += f"   Data Type: {record.data_type}\n"
        response_text += f"   Status: {status_text}"

        if record.transformations:
            response_text += f"\n\n   Transformations ({len(record.transformations)}):"
            for t in record.transformations:
                response_text += f"\n     - {t.description}"

        if record.accessors:
            response_text += f"\n\n   Accessors ({len(record.accessors)}):"
            for addr in record.accessors:
                response_text += f"\n     - {addr}"

        response_text += _format_hints(
            "download_data", ["verify_hash", "anchor_hash"]
        )

        return CallToolResult(content=[TextContent(type="text", text=response_text)])

    except ValueError as e:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_format_error(
                        f"Validation error: {str(e)}", retryable=False
                    ),
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
                        f"Chain error: {str(e)}",
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
            response_text += f"   Address: {result.get('address', 'N/A')}\n"
        elif enabled and not available:
            response_text = (
                f"⚠️  Notary service is enabled but not currently available.\n\n"
            )
        else:
            response_text = f"Notary service is not enabled on this gateway.\n\n"

        response_text += f"   Status: {result.get('message', 'N/A')}\n"
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
                        next_tool="health_check",
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
            logger.info(f"Gateway URL: {settings.swarm_gateway_url}")
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
