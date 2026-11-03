# Swarm Provenance MCP

[![Regression and Safety Tests](https://github.com/datafund/swarm_provenance_MCP/actions/workflows/regression_tests.yml/badge.svg)](https://github.com/datafund/swarm_provenance_MCP/actions/workflows/regression_tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> ⚠️ **ALPHA SOFTWARE - PROOF OF CONCEPT**
> This software is in **Alpha stage** and should be considered a **Proof of Concept**. Use for testing and experimentation only. Not recommended for production use.

> ⚠️ **DATA PERSISTENCE WARNING**
> Storage on Swarm is **rented storage** with limited time periods. The default configuration uses very short rental periods (approximately **1 day**). **Do not expect uploaded data to persist longer than the rental period.** Data will become unavailable when the postage stamp expires.

> ⚠️ **TESTNET NOTICE**
> On-chain provenance features use **Base Sepolia (testnet)** by default. Testnet tokens have no monetary value and testnet state may be reset at any time. Do not rely on testnet anchoring for production data integrity. No warranties of any kind are provided.

A Model Context Protocol (MCP) server for managing Swarm postage stamps and provenance data storage through a centralized FastAPI gateway. Enables AI agents to upload provenance data to the decentralized Swarm network for immutable storage and retrieve it by reference.

## Overview

This MCP server provides tools for AI agents to interact with Swarm postage stamps and provenance data storage, including purchasing and extending stamps, uploading provenance data to Swarm for immutable decentralized storage, and downloading data from the network by reference. It acts as a bridge between AI agents and the `swarm_connect` FastAPI gateway.

## Provenance & Immutable Storage

This MCP server is specifically designed for provenance data use cases, leveraging Swarm's decentralized network to provide:

- **Immutable Records**: Swarm addresses content by its hash, so the data stored under a reference cannot be changed. `download_data` checks content against its reference only in the cases its [reference check](#download_data) lists
- **Decentralized Storage**: No single point of failure or central authority
- **Provenance Metadata**: Support for structured provenance records with creator, timestamp, and lineage information
- **Reference Check on Download**: `download_data` recomputes the Swarm chunk address of content up to 4 KB and reports the content as verified only when that address equals the reference. References from `/bzz` uploads name a manifest and are reported as not checked; see [datafund/swarm_connect#499](https://github.com/datafund/swarm_connect/issues/499)

## Features

- **Purchase Stamps**: Create new postage stamps with configurable duration and size
- **Stamp Status**: Get detailed information about specific stamps
- **List Stamps**: View all available postage stamps
- **Extend Stamps**: Extend the duration of existing stamps
- **Data Upload**: Upload data to Swarm network with stamp validation
- **Data Download**: Download data from Swarm network by reference
- **Provenance Storage**: Store data with provenance metadata as immutable records whose on-chain registration you can check with `verify_hash`
- **Health Monitoring**: Check gateway and Swarm network connectivity
- **Chain Diagnostics** (optional): Check on-chain wallet balance and RPC connectivity for provenance anchoring
- **On-Chain Anchoring** (optional): Register Swarm hashes on-chain for immutable provenance records
- **Transformation Lineage** (optional): Record and trace data transformations through on-chain state reads (v2) or event logs (v1)
- **Merge Transformations** (optional): Record N-to-1 merge transformations combining multiple source hashes into one

## Installation

### Prerequisites

- Python 3.10 or higher (use `python3` command)
- Internet connection (uses public gateway by default)
- Optional: Self-hosted `swarm_connect` gateway service (see Gateway Options below)

### Setup

1. Clone the repository:
```bash
git clone https://github.com/datafund/swarm_provenance_MCP.git
cd swarm_provenance_mcp
```

2. Create and activate a virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install the package:
```bash
pip install -e .
```

4. Configure environment variables:
```bash
mkdir -p ~/.config/swarm-provenance-mcp
cp .env.example ~/.config/swarm-provenance-mcp/.env
# Edit that file to configure your gateway URL and defaults
```

### Docker

Run the MCP server in a container with no local dependencies.

> **Registry**: The pre-built image is published to **GitHub Container Registry** only — always use the full `ghcr.io/` prefix. It is **not** available on Docker Hub.

**Quick start** (pre-built image):
```bash
docker pull ghcr.io/datafund/swarm-provenance-mcp:latest
docker run -i --rm ghcr.io/datafund/swarm-provenance-mcp
```

**Build from source:**
```bash
docker build -t swarm-provenance-mcp .
docker run -i --rm swarm-provenance-mcp
```

**With environment variables:**
```bash
docker run -i --rm \
  -e SWARM_GATEWAY_URL=https://provenance-gateway.datafund.io \
  -e DEFAULT_STAMP_DURATION_HOURS=25 \
  -e DEFAULT_STAMP_SIZE=small \
  swarm-provenance-mcp
```

**Docker Compose:**
```bash
docker compose build
docker compose run --rm swarm-provenance-mcp
```

Compose takes its variables from `./.env` next to `docker-compose.yml` (for `${VAR}`
substitution and as the container's `env_file`) and passes them to the server as
environment variables. That is Compose's own file, separate from the server's env file
described under [Configuration](#configuration): the container never sees
`~/.config/swarm-provenance-mcp/.env` on the host.

| Variable | Description | Default |
|----------|-------------|---------|
| `SWARM_GATEWAY_URL` | Gateway endpoint URL (`http://` or `https://`; plain http to a remote host logs a warning) | `https://provenance-gateway.datafund.io` |
| `DEFAULT_STAMP_DURATION_HOURS` | Default stamp duration in hours (min 24) | `25` |
| `DEFAULT_STAMP_SIZE` | Default stamp size: `small`, `medium`, or `large` | `small` |
| `PAYMENT_MODE` | Gateway payment tier: `free` (3 write req/min) or empty (no payment header); other values stop the server at start-up | `free` |

## Configuration

Settings come from environment variables, or from an env file. The env file is
**never** read from the working directory (whichever program launches the server
controls that). It is read from, in order:

1. the path in `SWARM_PROVENANCE_ENV_FILE`, if set. It must be an absolute path (`~/...` is fine); start-up fails if it is relative or the file does not exist;
2. otherwise `~/.config/swarm-provenance-mcp/.env` (or `$XDG_CONFIG_HOME/swarm-provenance-mcp/.env`), if it exists.

Real environment variables (for example an MCP client's `"env"` block) override the env file.
Unknown keys in the env file are ignored. If a `.env` in the working directory is found and
ignored, the server logs a warning at start-up.

- `SWARM_PROVENANCE_ENV_FILE`: Explicit absolute path of the env file to load (see above)

- `SWARM_GATEWAY_URL`: URL of the swarm_connect FastAPI gateway (default: `https://provenance-gateway.datafund.io`). Must be `http://` or `https://` with a host and, if given, a numeric port (0 to 65535), or the server does not start. Plain `http://` to a host other than this machine logs a warning at start-up and adds a `health_check` recommendation. The `X-Payment-Mode` header is not sent on a redirect to another origin
- `DEFAULT_STAMP_DURATION_HOURS`: Default stamp duration in hours, minimum 24 (default: `25`)
- `DEFAULT_STAMP_SIZE`: Default stamp size — `small`, `medium`, or `large` (default: `small`)
- `PAYMENT_MODE`: Gateway payment tier (default: `free`, rate limited to 3 write requests/minute). Set it to empty to send no payment header. Any other value stops the server at start-up, because this server cannot pay x402 requests

#### Chain Anchoring (Optional)

- `CHAIN_ENABLED`: Enable on-chain provenance anchoring (default: `false`)
- `CHAIN_NAME`: Blockchain network — `base-sepolia` (testnet), `base` (mainnet), or `localhost` (local hardhat, chain 31337) (default: `base-sepolia`)
- `PROVENANCE_WALLET_KEY`: Private key for chain transactions (hex, with or without 0x prefix). A key that is set but invalid is logged as an error at start-up; the server still starts with chain writes disabled, and the write tools, `chain_health` and `health_check` say why. Read-only chain tools keep working. Use each key in one server process at a time. Two server processes that sign with the same key, for example one started by a desktop client and one by a command-line tool, can pick the same nonce, and one of the transactions then fails
- `CHAIN_RPC_URL`: Custom RPC endpoint (uses chain preset if not set). For `base-sepolia` and `base` the endpoint must report the preset's chain ID, or the server refuses it; see [Safety behavior](#safety-behavior)
- `CHAIN_RPC_URLS`: Comma-separated fallback RPC URLs, tried in order after `CHAIN_RPC_URL`. The same chain ID check applies to each one; see [Safety behavior](#safety-behavior)
- `CHAIN_CONTRACT`: Custom DataProvenance contract address (uses chain preset if not set). `CHAIN_CONTRACT_ADDRESS` is accepted too; `CHAIN_CONTRACT` wins if both are set
- `CHAIN_EXPLORER_URL`: Custom block explorer URL (uses chain preset if not set)
- `CHAIN_GAS_LIMIT`: Explicit gas limit for chain transactions (skips estimation if set)
- `CHAIN_MAX_FEE_GWEI`: Fee cap per gas (`maxFeePerGas`). Fee fields are set explicitly and a write is refused when the current fee is above the cap (default: `0.5` on mainnet, `10` on testnets)
- `CHAIN_SESSION_BUDGET_ETH`: Worst-case spend allowed per server session; each transaction reserves `gas limit x max fee` plus the L1 data fee allowance, and is charged its actual cost (L2 gas plus the receipt's `l1Fee` on Base) once mined (default: `0.002` on mainnet, `0.05` on testnets)
- `CHAIN_L1_FEE_ALLOWANCE_ETH`: L1 data fee reserved per transaction on top of L2 gas (default: `0.000001`, about 100x the ~1e-8 ETH measured on Base mainnet on 2026-10-21; L2 gas there was 1e-6 to 3e-5 ETH per transaction)
- `CHAIN_MAX_TX_PER_SESSION`: Chain transactions allowed per server session (default: `20` on mainnet, `500` on testnets)
- `CHAIN_REQUIRE_CONFIRM`: Require a confirmed quote on gas-spending tools. Without `confirm: true` and a matching `quote_id` they send nothing and return a quote of the exact action (arguments, fee cap, remaining budget) with a `quote_id`: an HMAC over the tool and its validated arguments under a per-process key, single use, valid 10 minutes (default: mainnet only — any chain id other than 84532, 11155111, 31337 or 1337 counts as mainnet). See [Safety behavior](#safety-behavior)
- `MAX_STAMP_DURATION_HOURS`: Maximum `duration_hours` for `purchase_stamp` / `extend_stamp` (default: `8760`)

When chain is enabled, additional tools become available: `chain_balance`, `chain_health`, `anchor_hash`, `verify_hash`, `get_provenance`, `record_transform`, `record_merge_transform`, `get_provenance_chain`, `set_storage_ref`, `lookup_by_storage_ref`. Blockchain dependencies (web3, eth-account) are included in the default install. Read-only tools (`verify_hash`, `get_provenance`, `get_provenance_chain`, `lookup_by_storage_ref`, `chain_health`) work without a wallet key; write tools (`anchor_hash`, `record_transform`, `record_merge_transform`, `set_storage_ref`) and `chain_balance` require `PROVENANCE_WALLET_KEY` with a funded wallet.

### Upgrading from a `.env` in the repository

Earlier versions read `.env` from the working directory, so the old setup was
`cp .env.example .env` in the repository plus `"cwd": "/path/to/swarm_provenance_mcp"`
in `claude_desktop_config.json`. That file is no longer read: after upgrading, the server
starts with the defaults (no wallet, `CHAIN_ENABLED=false`) and only logs a warning.

- Move the file: `mkdir -p ~/.config/swarm-provenance-mcp && mv .env ~/.config/swarm-provenance-mcp/.env`
  (or under `$XDG_CONFIG_HOME` if you set it),
  or keep it where it is and set `SWARM_PROVENANCE_ENV_FILE` to its absolute path in the client's `"env"` block.
- The `"env"` block in `claude_desktop_config.json` is unaffected and still overrides the env file.
- `"cwd"` no longer has any effect on configuration. You can remove it.

### Gateway Options

#### Public Gateway (Recommended)
The MCP server uses the public gateway hosted by DataFund by default at `https://provenance-gateway.datafund.io`. This gateway provides:
- High availability and reliability
- No setup or maintenance required
- Direct access to the Swarm network
- Free to use for development and testing

#### Self-Hosted Gateway
You can also run your own gateway instance for:
- Custom configurations
- Private or isolated environments
- Local development

To use a self-hosted gateway:
1. Clone the gateway repository: `git clone https://github.com/datafund/swarm_connect`
2. Follow the setup instructions in that repository
3. Set `SWARM_GATEWAY_URL=http://localhost:8000` in your env file (`~/.config/swarm-provenance-mcp/.env`) or environment

## Usage

### Running the MCP Server

```bash
swarm-provenance-mcp
```

### Available Tools

#### `purchase_stamp`
Purchase a new postage stamp.

**Parameters:**
- `duration_hours` (int): Duration in hours, minimum 24 (optional, uses default if not provided)
- `size` (string): Stamp size — `"small"`, `"medium"`, or `"large"` (optional, uses default if not provided)
- `depth` (int): Explicit depth override, 17–22, bypasses size (optional, advanced use only)
- `label` (string): Optional label for the stamp, at most 100 characters. If you omit it, the server generates a label of the form `mcp-<hex>`. The result shows it as "Label requested", because the gateway's answer does not confirm it; `list_stamps` shows the label the gateway stored

**Example:**
```json
{
  "name": "purchase_stamp",
  "arguments": {
    "duration_hours": 48,
    "size": "small",
    "label": "my-test-stamp"
  }
}
```

#### `get_stamp_status`
Get detailed information about a specific stamp.

**Parameters:**
- `stamp_id` (string): The batch ID of the stamp

**Example:**
```json
{
  "name": "get_stamp_status",
  "arguments": {
    "stamp_id": "000de42079daebd58347bb38ce05bdc477701d93651d3bba318a9aee3fbd786a"
  }
}
```

#### `list_stamps`
List all available postage stamps.

Each stamp's `Status` uses the same rule as `health_check`:
- `Usable`: usable, and its `accessMode` is `shared` or absent. Only these stamps work with `upload_data`.
- `Not usable by this server`: a `pool` stamp (gateway inventory), an `owned` stamp (registered to the wallet that paid for it) or another access mode. This server sends no wallet, so the gateway refuses its uploads.
- `Not usable yet`: a writable stamp that may still be propagating. Poll `check_stamp_health`; do not buy another.
- `Cannot become usable`: expired, about to expire, full, invalid depth, unreadable on the node, or not found on the node.

`_next` is `upload_data` when a stamp is usable, `check_stamp_health` when a stamp is still propagating, and `purchase_stamp` otherwise.

**Parameters:** None

**Example:**
```json
{
  "name": "list_stamps",
  "arguments": {}
}
```

#### `extend_stamp`
Extend an existing stamp with additional duration.

**Parameters:**
- `stamp_id` (string): The batch ID of the stamp to extend
- `duration_hours` (int): Additional duration in hours, minimum 24

**Example:**
```json
{
  "name": "extend_stamp",
  "arguments": {
    "stamp_id": "000de42079daebd58347bb38ce05bdc477701d93651d3bba318a9aee3fbd786a",
    "duration_hours": 48
  }
}
```

#### `upload_data`
Upload data to the Swarm network with stamp validation.

**Parameters:**
- `data` (string): Data content to upload (max 4096 bytes)
- `stamp_id` (string): Postage stamp ID to use for upload
- `content_type` (string): MIME type of the content (optional, default: "application/json")
- `sign` (string): Signing method (optional). The only accepted value is `"notary"`; see **Signing with the notary** below. Omit it for an unsigned upload. Any other value returns a validation error

**Example:**
```json
{
  "name": "upload_data",
  "arguments": {
    "data": "{\"message\": \"Hello Swarm!\"}",
    "stamp_id": "000de42079daebd58347bb38ce05bdc477701d93651d3bba318a9aee3fbd786a",
    "content_type": "application/json"
  }
}
```

**Signing with the notary:** Set `sign` to `"notary"` to have the gateway sign the data at upload time. Call `get_notary_info` first to check that signing is available. With `sign: "notary"`, `data` must be a JSON object with a top-level `data` field, for example `{"data": {...}, "signatures": []}`. The `signatures` field is optional and must be an array. The gateway adds its signature to it. The gateway returns these errors:
- 400 `INVALID_DOCUMENT_FORMAT` when `data` is not valid UTF-8 JSON, is not a JSON object with a `data` field, or has a `signatures` field that is not an array.
- 400 `NOTARY_NOT_ENABLED` or `NOTARY_NOT_CONFIGURED` when the notary is off.

The gateway stores the signed document as `application/json`, whatever `content_type` you send. The result reports the input size as "before signing", because the stored document is larger.

#### `download_data`
Download data from the Swarm network using a reference hash.

**Parameters:**
- `reference` (string): Swarm reference of the data to download: 64 hexadecimal characters, or 128 for an encrypted reference. Chain tools take only 64-character hashes

**Result:** For text and JSON content, the result has two content blocks. Read both:
1. A summary with the size, the SHA-256 of the original bytes, the `Reference check:` line (below), and the `_next` hints.
2. The content, fenced as untrusted data. Control and bidirectional characters are escaped and the content is cut at about 16 KB, so this view is not byte-exact.

A client that reads only the first block gets the summary, not the payload. Binary (non-UTF-8) content returns a single block that gives the size and SHA-256 and does not include the bytes. See [Safety behavior](#safety-behavior).

**Reference check:** the result has one `Reference check:` line that says what was checked.
- `verified`: the content is at most 4096 bytes, and its single-chunk Swarm address (BMT hash) equals the 64-character reference. The bytes are the chunk that the reference names.
- `not checked`, with the reason. Nothing proves that the bytes belong to the reference:
  - The content's chunk address does not equal the reference. The reference is then probably a manifest. A `/bzz` reference names a manifest root, whose address differs from the content's own chunk address. A Bee 2.8.2 upload recorded for the tests shows this (`tests/fixtures/bee_chunks.json`). The gateway stores data through Bee's `/bzz` endpoint, so references from `upload_data` most likely name a manifest. Only that last point comes from reading the gateway source; it was not confirmed with a live upload through the gateway. This server cannot follow a manifest to its content. A raw chunk endpoint on the gateway that would allow this is tracked in [datafund/swarm_connect#499](https://github.com/datafund/swarm_connect/issues/499).
  - The content is larger than one chunk (4096 bytes).
  - The reference is encrypted (128 hexadecimal characters).

A `not checked` result is not a mismatch. It means the gateway's bytes are shown as received. The result also reports the SHA-256 of the bytes, which you can compare with a hash you trust.

**Example:**
```json
{
  "name": "download_data",
  "arguments": {
    "reference": "a1b2c3d4e5f6789abcdef0123456789abcdef0123456789abcdef0123456789a"
  }
}
```

#### `check_stamp_health`
Run a health check on a specific stamp. Returns whether uploads can proceed, plus any errors or warnings with actionable suggestions.

**Parameters:**
- `stamp_id` (string): The batch ID of the stamp to check

**Example:**
```json
{
  "name": "check_stamp_health",
  "arguments": {
    "stamp_id": "000de42079daebd58347bb38ce05bdc477701d93651d3bba318a9aee3fbd786a"
  }
}
```

#### `get_wallet_info`
Get the gateway node's wallet address and BZZ balance. Useful for checking if the node has sufficient funds. Note: this is a debugging/diagnostic tool and may be removed in future versions.

**Parameters:** None

**Example:**
```json
{
  "name": "get_wallet_info",
  "arguments": {}
}
```

#### `get_notary_info`
Check whether the notary signing service is enabled and available on the gateway.

**Parameters:** None

**Example:**
```json
{
  "name": "get_notary_info",
  "arguments": {}
}
```

#### `health_check`
Check gateway and Swarm network connectivity status. Returns an adaptive status including stamp availability, a `ready` flag indicating whether uploads can proceed, and recommendations for next steps. `ready` is `false` when the gateway's Bee node reports no network (for example no connected peers), even if usable stamps exist. The output includes a `Swarm network:` line with the Bee node state.

**Parameters:** None

**Example:**
```json
{
  "name": "health_check",
  "arguments": {}
}
```

#### `chain_balance` *(optional — requires `CHAIN_ENABLED=true` and blockchain dependencies)*
Check the on-chain wallet ETH balance used for provenance anchoring. Returns wallet address, balance, chain info, and actionable funding guidance when balance is low.

**Parameters:** None

**Example:**
```json
{
  "name": "chain_balance",
  "arguments": {}
}
```

#### `chain_health` *(optional — requires `CHAIN_ENABLED=true` and blockchain dependencies)*
Test blockchain RPC connectivity for on-chain provenance. Returns connection status, chain name, chain ID, latest block number, and RPC response time. Does not require a wallet key.

**Parameters:** None

**Example:**
```json
{
  "name": "chain_health",
  "arguments": {}
}
```

#### `anchor_hash` *(optional — requires `CHAIN_ENABLED=true` and `PROVENANCE_WALLET_KEY`)*
Register a Swarm reference hash on the blockchain, creating an immutable provenance record with owner, timestamp, and data type. Costs gas. If the hash is already registered to the expected owner (`owner`, or the wallet address), it returns the existing record without error. With `storage_ref`, this holds only when the record already links the same storage reference; otherwise it returns an error that shows the existing reference. If another address owns the record, it returns an error that shows both addresses, and it sends no transaction.

**Parameters:**
- `swarm_hash` (string, required): 64-character hex Swarm reference hash to anchor
- `data_type` (string): Data type/category (default: `swarm-provenance`, max 64 chars)
- `owner` (string): Ethereum address to register as owner (defaults to wallet address; requires delegate authorization for other addresses). The wallet's own address and the zero address are refused; a mixed-case address must have a valid EIP-55 checksum
- `storage_ref` (string): Optional 64-character hex storage reference to link bidirectionally. Enables reverse lookup via `lookup_by_storage_ref`. Works with or without `owner` (with `owner`, the contract's `registerDataFor` overload that takes a storage reference). A reference equal to `swarm_hash` or all zeros is refused
- `confirm` (boolean): Set to `true`, together with `quote_id`, to send the transaction. See [Safety behavior](#safety-behavior)
- `quote_id` (string): The `quote_id` from the quote for exactly these arguments (single use, valid for 10 minutes)

**Example:**
```json
{
  "name": "anchor_hash",
  "arguments": {
    "swarm_hash": "a1b2c3d4e5f6789abcdef0123456789abcdef0123456789abcdef0123456789a",
    "data_type": "provenance-metadata"
  }
}
```

#### `verify_hash` *(optional — requires `CHAIN_ENABLED=true`)*
Check whether a Swarm reference hash is registered on the blockchain. Returns verified status with basic provenance info (owner, timestamp, data type) if found. Read-only — no gas or wallet key required.

**Parameters:**
- `swarm_hash` (string, required): 64-character hex Swarm reference hash to verify
- `expected_owner` (string): Ethereum address the record must belong to. If the record belongs to another address, the result is an error that shows both addresses

**Example:**
```json
{
  "name": "verify_hash",
  "arguments": {
    "swarm_hash": "a1b2c3d4e5f6789abcdef0123456789abcdef0123456789abcdef0123456789a"
  }
}
```

#### `get_provenance` *(optional — requires `CHAIN_ENABLED=true`)*
Retrieve the full on-chain provenance record for a Swarm reference hash. Returns owner, registration timestamp, data type, status, transformations, and accessors. Read-only — no gas or wallet key required.

**Parameters:**
- `swarm_hash` (string, required): 64-character hex Swarm reference hash to look up

**Example:**
```json
{
  "name": "get_provenance",
  "arguments": {
    "swarm_hash": "a1b2c3d4e5f6789abcdef0123456789abcdef0123456789abcdef0123456789a"
  }
}
```

#### `record_transform` *(optional — requires `CHAIN_ENABLED=true` and `PROVENANCE_WALLET_KEY`)*
Record a data transformation on-chain, linking the original data to its transformed version. Creates a verifiable lineage trail. The original hash must already be anchored. Costs gas. If the same `(original → new)` pair is already recorded, returns the existing link without spending gas (idempotent).

**Parameters:**
- `original_hash` (string, required): 64-character hex Swarm reference of the original data (must be already anchored)
- `new_hash` (string, required): 64-character hex Swarm reference of the transformed data
- `description` (string): Description of the transformation (max 256 chars, e.g., "Anonymized PII")
- `restrict_original` (boolean): If true, set the original data status to RESTRICTED after recording the transformation (default: false). This sends two transactions, and a quote covers both
- `confirm` (boolean): Set to `true`, together with `quote_id`, to send the transaction. See [Safety behavior](#safety-behavior)
- `quote_id` (string): The `quote_id` from the quote for exactly these arguments (single use, valid for 10 minutes)

**Example:**
```json
{
  "name": "record_transform",
  "arguments": {
    "original_hash": "a1b2c3d4e5f6789abcdef0123456789abcdef0123456789abcdef0123456789a",
    "new_hash": "b2c3d4e5f6789abcdef0123456789abcdef0123456789abcdef0123456789abc",
    "description": "Filtered for region EU",
    "restrict_original": false
  }
}
```

#### `record_merge_transform` *(optional — requires `CHAIN_ENABLED=true` and `PROVENANCE_WALLET_KEY`)*
Record an N-to-1 merge transformation on-chain, combining multiple source hashes into a single new hash. All source hashes must be already anchored. Costs gas. Requires a v2 contract (`localhost` or upgraded deployments).

**Parameters:**
- `source_hashes` (array of strings, required): 2–50 source Swarm reference hashes to merge (each 64-character hex)
- `new_hash` (string, required): 64-character hex Swarm reference of the merged result
- `description` (string): Description of the merge transformation (max 256 chars)
- `new_data_type` (string): Data type for the merged result (default: `"merged"`, max 64 chars)
- `confirm` (boolean): Set to `true`, together with `quote_id`, to send the transaction. See [Safety behavior](#safety-behavior)
- `quote_id` (string): The `quote_id` from the quote for exactly these arguments (single use, valid for 10 minutes)

**Example:**
```json
{
  "name": "record_merge_transform",
  "arguments": {
    "source_hashes": [
      "a1b2c3d4e5f6789abcdef0123456789abcdef0123456789abcdef0123456789a",
      "b2c3d4e5f6789abcdef0123456789abcdef0123456789abcdef0123456789abc"
    ],
    "new_hash": "c3d4e5f6789abcdef0123456789abcdef0123456789abcdef0123456789abcde",
    "description": "Merged EU and US datasets",
    "new_data_type": "merged-dataset"
  }
}
```

#### `get_provenance_chain` *(optional — requires `CHAIN_ENABLED=true`)*
Follow the transformation lineage for a Swarm hash. On v2 contracts, uses state reads for fast traversal; on v1 contracts, walks through event logs. Shows how data evolved — from original to each derived version. Read-only — no gas or wallet key required. If every configured RPC endpoint is unavailable or rate-limits the event-log queries that v1 traversal needs, the tool returns a retryable error instead of a partial lineage.

Each node in the output lists its parent hashes, and each transformation prints as an edge: `Edge: <parent_hash> -> <child_hash>: "<description>"`.

The tool returns the nodes it could read. When the lineage is partial, the output starts with an `INCOMPLETE` banner and `_next` is `get_provenance_chain`. The banner lists:
- each node whose record or links could not be read, with a fixed label and the exception class name;
- each node that `max_depth` stopped at, marked "not expanded" (this is not an error);
- a note when the v1 event index could not be built and links came from per-node event queries, which cover only the last 50,000 blocks and no merges.

The banner ends with a `Retry:` line: call `get_provenance_chain` on the named hash, or with a larger `max_depth`. RPC transport errors are not reported this way: the server fails over to the next RPC endpoint, or returns a retryable error.

**Parameters:**
- `swarm_hash` (string, required): 64-character hex Swarm reference hash to trace lineage for
- `max_depth` (integer): Maximum depth to traverse (default: 10, range: 1–50)

**Example:**
```json
{
  "name": "get_provenance_chain",
  "arguments": {
    "swarm_hash": "a1b2c3d4e5f6789abcdef0123456789abcdef0123456789abcdef0123456789a",
    "max_depth": 10
  }
}
```

#### `set_storage_ref` *(optional — requires `CHAIN_ENABLED=true` and `PROVENANCE_WALLET_KEY`)*
Attach a Swarm storage reference to an existing on-chain record. Set-once: cannot be changed after first write. Owner-only. Useful after `record_transform` to link the transformed data's Swarm storage location.

**Parameters:**
- `data_hash` (string, required): 64-character hex data hash of the existing on-chain record
- `storage_ref` (string, required): 64-character hex storage reference to link (e.g. Swarm reference)
- `confirm` (boolean): Set to `true`, together with `quote_id`, to send the transaction. See [Safety behavior](#safety-behavior)
- `quote_id` (string): The `quote_id` from the quote for exactly these arguments (single use, valid for 10 minutes)

**Example:**
```json
{
  "name": "set_storage_ref",
  "arguments": {
    "data_hash": "a1b2c3d4e5f6789abcdef0123456789abcdef0123456789abcdef0123456789a",
    "storage_ref": "b2c3d4e5f6789abcdef0123456789abcdef0123456789abcdef0123456789abc"
  }
}
```

#### `lookup_by_storage_ref` *(optional — requires `CHAIN_ENABLED=true`)*
Reverse lookup: find the on-chain provenance record by its Swarm storage reference. Returns the linked data hash and full provenance record if found. Read-only — no gas or wallet key required.

**Parameters:**
- `storage_ref` (string, required): 64-character hex storage reference to look up

**Example:**
```json
{
  "name": "lookup_by_storage_ref",
  "arguments": {
    "storage_ref": "b2c3d4e5f6789abcdef0123456789abcdef0123456789abcdef0123456789abc"
  }
}
```

### Response Format

All tool responses include structured metadata to help agents chain operations efficiently:

#### Success Responses

Every successful response appends workflow hints:

```
_next: <recommended_tool>        # The logical next tool to call
_related: <tool1>, <tool2>       # Other relevant tools
```

Hints are contextual. For example, `list_stamps` suggests `_next: upload_data` when a stamp this server can write to is usable, `_next: check_stamp_health` when such a stamp is still propagating, and `_next: purchase_stamp` otherwise.

#### Error Responses

Error responses include structured recovery information:

```
retryable: true|false            # Whether retrying the same call may succeed
_next: <recovery_tool>           # Tool to call for recovery
```

- **retryable: true** — transient errors (timeouts, rate limits, 502/503/504). Wait and retry.
- **retryable: false** — permanent errors (validation failures, unknown tools). Fix input and try a different approach.
- **Payment required (HTTP 402)** — the gateway has x402 payment enabled and refused a write (`purchase_stamp`, `upload_data`, `extend_stamp`). The error shows the gateway's payment details (amount, asset, network, recipient) and the options. This server cannot pay x402 requests yet.

#### Adaptive Health Check

The `health_check` tool returns additional fields:

```
ready: true|false                # Whether the system is ready for uploads
_recommendations:                # Actionable suggestions (only when issues exist)
  - No stamps found — purchase one before uploading
_gateway: <url> (connected|unreachable)
```

The output also has a `Swarm network:` line: `connected (...)`, `DEGRADED (...)`, or `not reported by gateway`. When it is `DEGRADED`, `ready` is `false` even if stamps are usable, because uploads may not propagate.

`ready` is true only when the server has a usable stamp it can write to: a `shared` stamp, or one without an `accessMode` (older gateways). `pool` stamps (gateway inventory) and `owned` stamps (registered to the wallet that paid for them) are counted separately, because the gateway refuses writes from this server to them. A writable stamp that is not usable yet (still propagating) leads to `_next: check_stamp_health`, not to a purchase.

### Safety behavior

These behaviors apply when `CHAIN_ENABLED=true` unless noted.

#### Confirmed quotes for spending tools

`anchor_hash`, `record_transform`, `record_merge_transform` and `set_storage_ref` accept `confirm` and `quote_id`. A confirmed quote is required on mainnet (any chain ID other than 84532, 11155111, 31337 or 1337), or on any chain when `CHAIN_REQUIRE_CONFIRM=true`. `CHAIN_REQUIRE_CONFIRM=false` turns it off. On the testnet default it is off. When confirmation is off, there is no quote step: `confirm` and `quote_id` are ignored and an ordinary call sends the transaction directly.

When confirmation is required:

1. Call the tool with its normal arguments. The server sends no transaction and returns a quote: the action, the number of transactions, the fee cap, the current fee, the remaining session budget, and a `quote_id`.
2. Get the user's approval.
3. Call the tool again with the same arguments, `confirm: true`, and the `quote_id`.

A `quote_id` is valid for 10 minutes, works once, and works only for the tool and arguments it was issued for. Hash and address values are compared without regard to letter case. It is valid only in the server process that issued it, so a restart invalidates it. If `confirm` is `true` but the `quote_id` is missing, expired, used or for different arguments, the server sends nothing and returns a new quote. This no-send behavior applies only when confirmation is required.

#### Untrusted content

Text that anyone can write is treated as data, not instructions: `download_data` content, stamp labels, and on-chain data types and transformation descriptions. Multi-line content, such as a download, is placed in a fenced block with a warning, escaped, and capped at 16 KB. Single-line fields are quoted and capped at 200 characters. Lines in this content that look like server hints are marked as quoted. On-chain lists (transformations, accessors) show at most 50 entries.

#### RPC address redaction

An RPC URL can carry an API key in its path or query. Chain tool results show the configured RPC URLs as scheme, host and port only. In server logs, RPC URLs in error text are cut the same way: credentials, path and query are removed and scheme, host and port are kept. Connection labels in log messages show the host only. The gateway URL appears in the start-up log and in `health_check` without credentials, query or fragment. Faucet and explorer links are not changed.

#### Chain ID pinning

For `CHAIN_NAME=base-sepolia` and `CHAIN_NAME=base`, the chain ID is fixed by the preset and never taken from the RPC. At start-up the server checks the primary RPC. If it reports another chain, the server refuses to start. Your MCP client may then show only a generic message such as "server disconnected"; the reason is in the server's stderr log. Each endpoint, including each `CHAIN_RPC_URLS` fallback, is also checked before its first read, and a write checks the chain ID again before signing. If every fallback is on the wrong chain, the tool returns a chain ID mismatch error. Only `CHAIN_NAME=localhost` with a custom RPC detects the chain ID from the RPC. Fix `CHAIN_RPC_URL`, `CHAIN_RPC_URLS` or `CHAIN_NAME` so they match.

#### Contract check

Before the first write, the server checks that `CHAIN_CONTRACT` (or the preset address) holds DataProvenance contract code. If every endpoint that answers reports no code, the write tools are disabled and report the reason, `health_check` and `chain_health` report it too, and the server checks again before each write. Read-only tools are not disabled by this write check and can still be called. They use the same configured contract, so they can return their own RPC or contract-read errors. If no RPC answers in time, the check runs again before the first transaction.

### MCP Prompts

The server provides workflow prompts that agents can invoke via `prompts/list` and `prompts/get`. These give step-by-step instructions for common tasks:

| Prompt | Description | Arguments |
|--------|-------------|-----------|
| `provenance-upload` | Upload data to Swarm: health check, stamp selection, upload, download to confirm | `data` (required), `content_type` (optional) |
| `provenance-verify` | Download existing data by reference, inspect it, and report what the reference check covered | `reference` (required) |
| `stamp-management` | Review stamp inventory, diagnose issues, recommend actions | none |
| `provenance-chain-workflow` | End-to-end on-chain provenance: store, anchor, and optionally record a transformation | `data` (required), `transform_description` (optional) |

### MCP Resources

The server exposes on-demand knowledge resources that agents can load via `resources/list` and `resources/read`:

| Resource URI | MIME Type | Description |
|-------------|-----------|-------------|
| `provenance://skills` | `text/markdown` | Provenance skills guide — concepts, critical rules, workflows, diagrams, and error recovery |

## Docker

### Building

```bash
docker build -t swarm-provenance-mcp .
```

To tag with a specific version:

```bash
docker build --build-arg VERSION=0.2.0 -t swarm-provenance-mcp:0.2.0 .
```

### Running

The server communicates via stdio (no ports). Pass `-i` for interactive stdin:

```bash
docker run -i --rm swarm-provenance-mcp
```

Configure the gateway URL and other settings via environment variables:

```bash
docker run -i --rm \
  -e SWARM_GATEWAY_URL=https://provenance-gateway.datafund.io \
  -e DEFAULT_STAMP_SIZE=medium \
  swarm-provenance-mcp
```

### Claude Desktop with Docker

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "swarm-provenance": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "swarm-provenance-mcp"]
    }
  }
}
```

## Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   AI Agents     │◄──►│  MCP Server     │◄──►│ swarm_connect   │
│                 │    │                 │    │   Gateway       │
│ • Claude        │    │ • Tool handlers │    │                 │
│ • Other LLMs    │    │ • Gateway client│    │ • Purchase API  │
│ • Custom agents │    │ • Chain client  │    │ • Status API    │
└─────────────────┘    │ • Error handling│    │ • Extension API │
                       └────────┬────────┘    └─────────┬───────┘
                                │                       │
                       ┌────────▼────────┐    ┌─────────▼───────┐
                       │  Base Sepolia   │    │  Swarm Network  │
                       │  (DataProv.     │    │   (Bee Node)    │
                       │   Contract)     │    └─────────────────┘
                       └─────────────────┘
```

### Components

- **MCP Server**: Exposes tools via the Model Context Protocol
- **Gateway Client**: HTTP client for communicating with swarm_connect
- **Chain Client** (optional): On-chain provenance via DataProvenance smart contract on Base Sepolia
- **Configuration**: Environment-based settings management
- **Error Handling**: Comprehensive error handling and logging

## Claude Desktop Integration

### Setup Instructions

1. **Install Claude Desktop**: Download from [claude.ai](https://claude.ai/download)

2. **Clone and set up this repository**:
```bash
# Clone the repository
git clone https://github.com/datafund/swarm_provenance_MCP.git

# Navigate to the project directory
cd swarm_provenance_mcp

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install the package in development mode
pip install -e .

# Configure environment variables
mkdir -p ~/.config/swarm-provenance-mcp
cp .env.example ~/.config/swarm-provenance-mcp/.env
# Edit that file if you need to customize gateway URL or defaults
```

3. **Configure MCP Server**: Add to Claude Desktop's configuration file:

**macOS/Linux** (`~/Library/Application Support/Claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "swarm-provenance": {
      "command": "/path/to/swarm_provenance_mcp/run.sh"
    }
  }
}
```

**Windows** (`%APPDATA%\Claude\claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "swarm-provenance": {
      "command": "C:\\path\\to\\swarm_provenance_mcp\\venv\\Scripts\\python.exe",
      "args": ["-m", "swarm_provenance_mcp.server"]
    }
  }
}
```

*Note: Replace `/path/to/swarm_provenance_mcp` with the actual path where you cloned the repository.*

The `run.sh` launcher (macOS/Linux) starts the server from the repo's venv, with the repo as working directory. The working directory does not affect configuration: settings come from the env file described above, and a `.env` in the repository is no longer read (see [Upgrading from a `.env` in the repository](#upgrading-from-a-env-in-the-repository); the server logs a warning if it finds one). If the venv stops working — typically after moving or renaming the repo directory — it rebuilds it automatically. Make it executable once with `chmod +x run.sh`.

The rebuild installs all dependencies and can take a few minutes, longer than an MCP client waits for the server on startup. If the first launch after a move times out, run `./run.sh < /dev/null` once in a terminal to finish the rebuild, then restart the client. The Windows config above does not use the launcher yet; after moving the repo on Windows, recreate the venv manually.

**Alternative (if package is installed)**: You can use `"command": "swarm-provenance-mcp"` instead after running `pip install -e .`

#### With on-chain provenance

To enable blockchain anchoring, add an `"env"` block to the config. You can use this instead of (or alongside) the env file at `~/.config/swarm-provenance-mcp/.env`:

```json
{
  "mcpServers": {
    "swarm-provenance": {
      "command": "/path/to/swarm_provenance_mcp/run.sh",
      "env": {
        "CHAIN_ENABLED": "true",
        "PROVENANCE_WALLET_KEY": "0x...your_private_key_here..."
      }
    }
  }
}
```

Read-only chain tools (`verify_hash`, `get_provenance`, `get_provenance_chain`, `lookup_by_storage_ref`, `chain_health`) work without `PROVENANCE_WALLET_KEY`. Write tools (`anchor_hash`, `record_transform`, `record_merge_transform`, `set_storage_ref`) and `chain_balance` require a funded wallet — see [Chain Anchoring](#chain-anchoring-optional) for details.

#### Docker-based

Use Docker for a zero-install experience — no Python, venv, or pip required:

```json
{
  "mcpServers": {
    "swarm-provenance": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-e", "SWARM_GATEWAY_URL=https://provenance-gateway.datafund.io",
        "ghcr.io/datafund/swarm-provenance-mcp"
      ]
    }
  }
}
```

To enable chain anchoring with Docker, add the chain env vars:

```json
{
  "mcpServers": {
    "swarm-provenance": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-e", "SWARM_GATEWAY_URL=https://provenance-gateway.datafund.io",
        "-e", "CHAIN_ENABLED=true",
        "-e", "PROVENANCE_WALLET_KEY=0x...your_private_key_here...",
        "ghcr.io/datafund/swarm-provenance-mcp"
      ]
    }
  }
}
```

> **Docker Desktop MCP Toolkit**: If you use Docker Desktop with MCP Toolkit support, the server can be discovered automatically from the Docker MCP catalog.

4. **Restart Claude Desktop** and verify the connection.

### Getting Started

After setup, try these prompts in Claude Desktop to verify everything works:

**Check connectivity:**
> "Run a health check on the Swarm gateway"

Expected: a status showing `healthy`, gateway URL, and response time. If chain is enabled, you'll also see RPC connectivity and wallet balance info.

**List stamps:**
> "List all available Swarm stamps"

Expected: a list of postage stamps with batch IDs, or a message that no stamps exist yet (with a suggestion to purchase one).

**Full upload workflow:**
> "Upload the text 'Hello Swarm!' to the Swarm network"

Claude will walk through: purchasing a stamp, waiting for it to propagate, uploading the data, and returning the Swarm reference hash.

**Verify on-chain (if chain enabled):**
> "Anchor the uploaded hash on-chain and verify it"

Claude will register the hash on the blockchain and confirm the provenance record.

### Troubleshooting Setup

If Claude Desktop doesn't show the Swarm tools:
1. Check the config file path is correct for your OS
2. Verify the `command` path points to `run.sh` in your clone (macOS/Linux), or to the Python executable inside your venv (Windows)
3. Check Claude Desktop logs: **Help > Show Logs** (look for MCP connection errors)
4. Test manually: run `./run.sh` (or `swarm-provenance-mcp`) in your terminal — it should start without errors and wait for MCP input. Launcher messages, including a venv rebuild, go to stderr

## Development

### Testing

```bash
# Install development dependencies
pip install -e .[dev]

# Run tests
pytest

# Run with coverage
pytest --cov=swarm_provenance_mcp
```

### Docker Testing

Requires Docker daemon to be running:

```bash
# Run all Docker tests (builds image, tests MCP protocol, tool calls)
pytest tests/test_docker.py -v -m docker

# Verify manually
docker build -t swarm-provenance-mcp .
docker run -i --rm swarm-provenance-mcp                           # starts, waits for MCP input
docker run -i --rm -e SWARM_GATEWAY_URL=https://provenance-gateway.datafund.io swarm-provenance-mcp  # env override
```

### Code Quality

```bash
# Format code
black swarm_provenance_mcp/

# Lint code
ruff check swarm_provenance_mcp/
```

### Editor settings

The repository commits no editor hooks, MCP servers or auto-approve settings. Shared editor settings take effect on every contributor's machine, so keep your own in per-developer configuration:

- `.claude/settings.local.json` for project hooks and permissions. It is gitignored, and so is any `.claude/*.local.json`.
- `~/.claude/settings.json` for settings that apply to all your projects.
- `claude mcp add --scope local` or `--scope user` for MCP servers, instead of a committed `.mcp.json`.

These files count as committed editor settings: `.claude/**/*.json` except `*.local.json`, `.mcp.json`, `.cursor/mcp.json` and `.cursor/hooks.json`. They must not contain:

- A `hooks`, `mcpServers`, `statusLine` or `apiKeyHelper` key. Each one runs a command or connects to an MCP server.
- A non-empty `permissions.allow`.
- An `enableAllProjectMcpServers` or `enabledMcpjsonServers` key.
- A `permissions.defaultMode` other than `"default"`.

`tests/test_editor_settings.py` enforces these rules.

## Dependencies

- **Core Dependencies**:
  - `mcp>=1.26,<2`: Model Context Protocol framework (the server is written for mcp 1.x)
  - `requests>=2.31.0,<3`: HTTP client for gateway communication
  - `pydantic>=2.0.0,<3` and `pydantic-settings>=2.0,<3`: Data validation and settings
  - `python-dotenv>=1.0.0,<2`: Environment configuration
  - `web3>=7,<8`: Ethereum blockchain interaction for on-chain provenance
  - `eth-account>=0.13,<0.14`: Wallet and transaction signing for chain anchoring
  - `eth-utils>=5,<6`: Hashing helpers (keccak) used directly by the chain module

  Upper bounds are deliberate. Raise one only together with a passing test run
  against the new version.

- **Development Dependencies**:
  - `pytest`: Testing framework
  - `pytest-asyncio`: Async testing support
  - `pytest-mock`: Mocking utilities
  - `black`: Code formatting
  - `ruff`: Linting

## Integration with AI Agents

This MCP server is designed to work with AI agents that support the Model Context Protocol. Agents can use the provided tools to:

1. **Manage stamp inventory**: Purchase and extend stamps as needed
2. **Monitor usage**: Check stamp status and utilization
3. **Optimize costs**: List stamps to find the most suitable ones for tasks
4. **Store provenance data**: Upload data with provenance metadata to immutable decentralized storage
5. **Retrieve records**: Download records from the Swarm network by reference. Content up to 4 KB is checked against a reference that names a single chunk; see the `download_data` reference check
6. **Data lifecycle management**: Handle complete provenance workflows from creation to retrieval and on-chain checks
7. **Track data lineage**: Record transformations and trace the full provenance chain of derived data
8. **Automate workflows**: Integrate stamp management and data storage into larger AI workflows

## Troubleshooting

### Common Issues

1. **Connection errors**: Ensure the swarm_connect gateway is running and accessible
2. **Authentication errors**: Check that the gateway doesn't require authentication
3. **Invalid stamp IDs**: Verify stamp IDs are valid batch IDs from the Swarm network
4. **Timeout errors**: Increase timeout values if operations are taking too long
5. **Chain: "wallet key not configured"**: Set `PROVENANCE_WALLET_KEY` in the MCP client's `"env"` block or in the env file (`~/.config/swarm-provenance-mcp/.env`, see [Configuration](#configuration)) for write operations (`anchor_hash`, `record_transform`, `set_storage_ref`). Read-only tools work without it. If the error says the key is not a valid private key, fix the value (64 hexadecimal characters, with or without 0x) and restart the server.
6. **Chain: "insufficient funds"**: Fund your wallet with testnet ETH (Base Sepolia faucet) or bridge ETH to Base mainnet. Run `chain_balance` for guidance.
7. **Chain: "already registered"**: The hash is already anchored on-chain. If the result is an error, another address owns the record. Registration is first-come, so to anchor a record you own, change the content, upload it again, and anchor the new hash. Use `get_provenance` to view the existing record.
8. **Chain: "transformation already recorded"**: The `(original → new)` link already exists on-chain. No gas spent — use `get_provenance_chain` to verify the lineage.
9. **Chain: "too few/many sources"**: `record_merge_transform` requires 2–50 source hashes. Adjust the `source_hashes` array accordingly.

### Logging

The server logs important events and errors. To increase logging verbosity:

```python
import logging
logging.getLogger("swarm_provenance_mcp").setLevel(logging.DEBUG)
```

## License

MIT License - see LICENSE file for details.