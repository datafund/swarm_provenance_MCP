# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

This is a Model Context Protocol (MCP) server that enables AI agents to manage Swarm postage stamps through a centralized FastAPI gateway. The server provides tools for purchasing, extending, monitoring, and utilizing Swarm postage stamps via natural language interactions.

**IMPORTANT**: This repository is part of the DataFund organization. When referencing repository URLs in documentation, examples, or instructions, always use the DataFund repository: `https://github.com/datafund/swarm_provenance_MCP.git`. Do not use upstream or fork repositories.

## Common Development Commands

### Environment Setup
```bash
# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate  # Windows

# Install package in development mode
pip install -e .

# Install with development dependencies
pip install -e .[dev]

# Configure environment
mkdir -p ~/.config/swarm-provenance-mcp
cp .env.example ~/.config/swarm-provenance-mcp/.env
# Edit it to set SWARM_GATEWAY_URL and stamp defaults
```

### Development Workflow
```bash
# Run the MCP server
swarm-provenance-mcp

# Run tests
pytest

# Run tests with coverage
pytest --cov=swarm_provenance_mcp

# Format code
black swarm_provenance_mcp/

# Lint code
ruff check swarm_provenance_mcp/

# Run specific test file
pytest tests/test_gateway_client.py
pytest tests/test_integration.py

# Run tests with verbose output
pytest -v
```

### Docker
```bash
# Build image
docker build -t swarm-provenance-mcp .

# Run container (stdio mode)
docker run -i --rm swarm-provenance-mcp

# Run with custom gateway
docker run -i --rm -e SWARM_GATEWAY_URL=https://provenance-gateway.datafund.io swarm-provenance-mcp

# Docker Compose
docker compose build
docker compose run --rm swarm-provenance-mcp

# Run Docker tests (requires Docker daemon)
pytest tests/test_docker.py -v -m docker
```

### Editor settings

The repository commits no `.claude/settings.json` or `.mcp.json`. Put hooks, MCP servers and permissions in per-developer configuration: `.claude/settings.local.json` (gitignored), `~/.claude/settings.json`, or `claude mcp add --scope local|user`. A committed `.claude/**/*.json` (except `*.local.json`), `.mcp.json`, `.cursor/mcp.json` or `.cursor/hooks.json` must not contain a `hooks`, `mcpServers`, `statusLine`, `apiKeyHelper`, `enableAllProjectMcpServers` or `enabledMcpjsonServers` key, a non-empty `permissions.allow`, or a `permissions.defaultMode` other than `"default"`. `tests/test_editor_settings.py` enforces this. See "Editor settings" in README.md.

## Architecture Overview

### Core Components
- **MCP Server** (`server.py`): Main server implementing Model Context Protocol with tool handlers
- **Gateway Client** (`gateway_client.py`): HTTP client for communicating with swarm_connect FastAPI gateway
- **Configuration** (`config.py`): Pydantic-based settings management with environment variable support
- **Chain Module** (`chain/`): Optional on-chain provenance anchoring via DataProvenance smart contract

### Communication Flow
```
AI Agents → MCP Server → Gateway Client → swarm_connect Gateway → Swarm Network
                       → Chain Client  → Base Sepolia RPC → DataProvenance Contract
```

### Chain Module (`chain/`)
On-chain provenance module. Dependencies (web3, eth-account) included in default install. Enable with `CHAIN_ENABLED=true`.
- `chain/__init__.py` — Import guard (`CHAIN_AVAILABLE` flag)
- `chain/client.py` — High-level facade (anchor, verify, transform, merge_transform, access, set_storage_ref, lookup_by_storage_ref)
- `chain/provider.py` — Web3 RPC connection management (includes `localhost` preset for local hardhat). Liveness probe runs state methods, not just `eth_chainId`; `is_transport_error()` classifies endpoint-unavailable errors (429/502/503/504, timeouts, `-32011`). For `base-sepolia` and `base` the chain id is pinned to the preset: an RPC reporting another id is refused at start-up (`ChainIdMismatchError`, the server will not start) and again right before every signature; only `localhost` with a custom RPC auto-detects. Every endpoint (primary or fallback) is also checked once before its first read (`ensure_chain_id`, cached per Web3 instance; any mismatch, e.g. from a write's pre-sign check, evicts the endpoint so later reads re-check and are refused). A fallback on the wrong chain is logged at WARNING and, if every fallback is, `ChainIdMismatchError` is raised instead of a retryable "unavailable". Deliberate asymmetry: a primary that changes chain mid-session makes writes refuse without failing over, while `health_check` fails over to a correct fallback; do not "fix" writes to fail over on a mismatch. Testnet/mainnet labels, the MCP instructions and funding hints come from the effective chain id; `TESTNET_CHAIN_IDS`/`is_testnet_chain_id()` in `chain/provider.py` is the single source for testnet decisions
- `chain/wallet.py` — Private key loading and transaction signing
- `chain/contract.py` — DataProvenance contract wrapper (build_*_tx, read methods, event queries, v2 state reads, storageRef support, feature detection)
- `chain/event_cache.py` — In-memory cache for DataTransformed and DataMerged events (singleton per chain+contract, incremental scans)
- `chain/models.py` — Pydantic models (AnchorResult, ChainProvenanceRecord, etc.)
- `chain/exceptions.py` — Standalone exception hierarchy (ChainError base)
- `chain/abi/DataProvenance.json` — Contract ABI

### Available MCP Tools

#### Swarm Gateway Tools
- `purchase_stamp` - Create new postage stamps (response includes propagation timing)
- `get_stamp_status` - Retrieve detailed stamp information (includes utilization + propagation data)
- `list_stamps` - List local stamps with access mode (owned/shared) and propagation status
  Stamps have `accessMode`: `"owned"` (dedicated, yours) or `"shared"` (public, any gateway user). Use owned stamps for production.
- `extend_stamp` - Extend stamp duration
- `upload_data` - Upload data to Swarm (max 4KB)
- `download_data` - Download data from Swarm by reference hash
- `check_stamp_health` - Diagnose stamp upload readiness with errors/warnings and propagation timing
- `get_wallet_info` - Node wallet address and BZZ balance (debug, may be removed)
- `get_notary_info` - Check notary signing service availability
- `health_check` - Gateway connectivity status

#### Chain Tools (requires `CHAIN_ENABLED=true`)

| Tool | Wallet Key | Gas | Description |
|------|-----------|-----|-------------|
| `chain_health` | not needed | no | Probe RPC with the state methods a write needs (`eth_chainId`, `eth_gasPrice`, `eth_getTransactionCount`) |
| `chain_balance` | **required** | no | Check wallet ETH balance with funding guidance |
| `verify_hash` | not needed | no | Check if hash is registered on-chain |
| `get_provenance` | not needed | no | Retrieve full on-chain provenance record |
| `get_provenance_chain` | not needed | no | Follow transformation lineage tree bidirectionally (state reads on v2, event logs on v1) |
| `anchor_hash` | **required** | **yes** | Register Swarm hash on-chain (optional `storage_ref` for bidirectional lookup) |
| `record_transform` | **required** | **yes** | Record data transformation, link original → new hash |
| `record_merge_transform` | **required** | **yes** | Record N-to-1 merge transformation (2–50 sources → new hash) |
| `set_storage_ref` | **required** | **yes** | Attach Swarm storage ref to existing record (set-once, owner-only) |
| `lookup_by_storage_ref` | not needed | no | Reverse lookup: storage ref → data hash + full provenance record |

Blockchain dependencies (web3, eth-account) are included in the default install. Set `CHAIN_ENABLED=true` to activate chain tools. Read-only tools (`verify_hash`, `get_provenance`, `get_provenance_chain`, `lookup_by_storage_ref`, `chain_health`) work without `PROVENANCE_WALLET_KEY` by creating a temporary provider + contract for direct contract reads. Write tools (`anchor_hash`, `record_transform`, `record_merge_transform`, `set_storage_ref`) and `chain_balance` require a funded wallet. Default RPC is `https://sepolia.base.org` (public, no API key needed); override with `CHAIN_RPC_URL`. The contract supports v1 (event-log only), v2 (state-based traversal with `TransformationLink` struct), and v3+ (storageRef bidirectional linking) — versions are auto-detected via `supports_transformation_links()` and `supports_storage_ref()`.

### Dependencies Architecture
- **MCP Framework**: Uses `mcp>=1.26,<2` for protocol implementation (mcp 2.x breaks `@server.list_tools()`)
- **HTTP Client**: Uses `requests>=2.31.0,<3` for gateway communication
- **Data Validation**: Uses `pydantic>=2,<3` and `pydantic-settings>=2,<3` for settings and request validation
- **Configuration**: Uses `python-dotenv>=1,<2` for environment management
- **Chain**: Uses `web3>=7,<8`, `eth-account>=0.13,<0.14` and `eth-utils>=5,<6` (bounded: a new release can change signing or hashing)
- Every third-party import must be declared in `pyproject.toml`, and `mcp`, `web3`, `eth-account`, `eth-utils` and `pydantic-settings` must keep an upper bound; `tests/test_install_and_release_safety.py` enforces both

### Testing Strategy
- **Gateway Client Tests** (`test_gateway_client.py`): Mock-based testing of HTTP client
- **Tool Execution Tests** (`test_tool_execution.py`): Handler-level tests for all MCP tools including chain tools, with mocked chain_client/CHAIN_AVAILABLE. Covers insufficient funds handling, event-based chain traversal, already-registered reverts, duplicate transformation detection, and proactive health_check balance warnings.
- **Tool Definition Tests** (`test_tool_definitions.py`): Validates tool schemas, required parameters, and registration consistency
- **Integration Tests** (`test_integration.py`): End-to-end MCP tool testing
- **Performance Tests** (`test_performance_regression.py`): Handler response time and concurrency regression tests
- **User Tests** (`user-tests/`): Live end-to-end UX audit against real gateway and blockchain (not run in CI)
- **Production gateway guard** (`tests/conftest.py`): the test run forces `SWARM_GATEWAY_URL` to `SWARM_TEST_GATEWAY_URL` (default: a closed local port), skips `integration` tests unless `--run-integration` is passed, and fails any test that sends a request to the production gateway
- **Release gate**: `.github/workflows/tests.yml` (unit tests, fresh-install stdio smoke, image stdio smoke) runs on PRs to `main`/`development` and is called by `docker-publish.yml` before any push; `scripts/mcp_stdio_smoke.py` is the stdio `initialize` + `tools/list` check
- **Async Support**: Uses `pytest-asyncio` for async test execution
- **Mocking**: Uses `pytest-mock` and `unittest.mock` for external dependency mocking

## Configuration Management

### Environment Variables
Read from the process environment, then an env file. The env file is never taken from the working directory: it is `SWARM_PROVENANCE_ENV_FILE` if set (must be an absolute path to an existing file), else `~/.config/swarm-provenance-mcp/.env` (or under `$XDG_CONFIG_HOME`). Unknown keys are ignored (`extra="ignore"`). pydantic-settings v2 ignores `Field(env=...)`: a variable name that differs from the field name needs `validation_alias`. `tests/test_config_loading.py` checks every documented name.
- `SWARM_PROVENANCE_ENV_FILE`: Explicit env file path
- `SWARM_GATEWAY_URL`: Gateway endpoint (default: `https://provenance-gateway.datafund.io`)
- `DEFAULT_STAMP_DURATION_HOURS`: Default stamp duration in hours, minimum 24 (default: `25`)
- `DEFAULT_STAMP_SIZE`: Default stamp size — `small`, `medium`, or `large` (default: `small`)
- `PAYMENT_MODE`: Gateway payment tier — `free` for rate-limited free tier (default: `free`)
- `MCP_SERVER_NAME`: Server identification (default: `swarm-provenance-mcp`)
- `MCP_SERVER_VERSION`: Server version (default: `0.2.0`)
- `CHAIN_ENABLED`: Enable on-chain provenance anchoring (default: `false`)
- `CHAIN_NAME`: Blockchain network (`base-sepolia`, `base`, or `localhost`, default: `base-sepolia`)
- `PROVENANCE_WALLET_KEY`: Private key for chain transactions (hex, with or without 0x)
- `CHAIN_RPC_URL`: Custom RPC endpoint (uses chain preset if not set)
- `CHAIN_RPC_URLS`: Comma-separated fallback RPC URLs, tried in order after `CHAIN_RPC_URL`
- `CHAIN_CONTRACT`: Custom DataProvenance contract address (uses chain preset if not set); `CHAIN_CONTRACT_ADDRESS` also accepted, `CHAIN_CONTRACT` wins
- `CHAIN_EXPLORER_URL`: Custom block explorer URL (uses chain preset if not set)
- `CHAIN_GAS_LIMIT`: Explicit gas limit for chain transactions (skips estimation if set)
- `CHAIN_MAX_FEE_GWEI`: Fee cap per gas (`maxFeePerGas`). Fee fields are set explicitly and a write is refused when the current fee is above the cap (default: `0.5` on mainnet, `10` on testnets)
- `CHAIN_SESSION_BUDGET_ETH`: Worst-case spend allowed per server session; each transaction reserves `gas limit x max fee` plus the L1 data fee allowance, and is charged its actual cost (L2 gas plus the receipt's `l1Fee` on Base) once mined (default: `0.002` on mainnet, `0.05` on testnets)
- `CHAIN_L1_FEE_ALLOWANCE_ETH`: L1 data fee reserved per transaction on top of L2 gas (default: `0.000001`, about 100x the ~1e-8 ETH measured on Base mainnet on 2026-10-21; L2 gas there was 1e-6 to 3e-5 ETH per transaction)
- `CHAIN_MAX_TX_PER_SESSION`: Chain transactions allowed per server session (default: `20` on mainnet, `500` on testnets)
- `CHAIN_REQUIRE_CONFIRM`: Require a confirmed quote on gas-spending tools. Without `confirm: true` and a matching `quote_id` they send nothing and return a quote of the exact action (arguments, fee cap, remaining budget) with a `quote_id`: an HMAC over the tool and its validated arguments under a per-process key, single use, valid 10 minutes (default: mainnet only — any chain id other than 84532, 11155111, 31337 or 1337 counts as mainnet)
- `MAX_STAMP_DURATION_HOURS`: Maximum `duration_hours` for `purchase_stamp` / `extend_stamp` (default: `8760`)

### Settings Management
The `config.py` module uses Pydantic Settings for type-safe configuration with automatic environment variable loading and validation.

## Development Patterns

### Error Handling
- Comprehensive error handling for HTTP requests with user-friendly messages
- Proper MCP error responses with structured error information
- Request timeout handling and retry logic in gateway client
- RPC failover: public `ChainClient` methods are wrapped in `@_rpc_failover` — on a transport error they switch to the next healthy RPC, rebind the contract, and re-run. Never re-runs once `send_raw_transaction` was attempted (`ChainTransactionError.broadcast`). With no usable fallback they raise `ChainConnectionError` (reported `retryable: true`)
- Spending limits (`chain/spend.py`, `SpendGuard`): `ChainClient._send_transaction` sets explicit fee fields within `CHAIN_MAX_FEE_GWEI` (priority fee from `eth_feeHistory`, default 0.001 gwei, not web3's 1 gwei fallback) and reserves the worst-case cost (L2 gas plus L1 allowance) against the session budget and transaction count before signing; a node-rejected broadcast releases it. A refusal raises `ChainSpendLimitError` (never failed over, reported `retryable: false`). Multi-transaction operations (`record_transform` with `restrict_original`) hold all their slots up front via `hold_transactions()` / `using_hold()`; if the second transaction still fails the tool returns `isError: true` with the first tx hash. Gas-spending tools take `confirm` + `quote_id`; when confirmation is required (mainnet by default) the quote is bound to the exact arguments (`_spend_confirmation` in `server.py`). All tools carry MCP `ToolAnnotations` (`TOOL_ANNOTATIONS` in `server.py`); tools that spend funds are marked destructive and non-idempotent
- Write confirmation: before the first transaction `ChainClient.verify_contract()` requires DataProvenance code at the configured address (`eth_getCode` plus function selectors; also run at start-up through each RPC with a 5 s budget per endpoint — an endpoint answering "no code" is outvoted by one on the same chain that has the code, and the client switches to it — where a failure disables the write tools, which report why in their errors, `health_check` and `chain_health`, and re-check before each write). After each write the receipt must contain the expected event from the contract address (`DataProvenanceContract.receipt_has_event()`); a status-1 receipt without it raises `ChainTransactionError` instead of reporting success. `str(ChainTransactionError)` includes its `tx_hash`
- Log hygiene: RPC URLs can carry API keys in the path or query. Log lines show hosts only: `redact.url_host()` for URLs, `redact.redact_urls()` for exception text from web3/requests calls (it cuts every URL to scheme, host and port)
- Chain-specific error handling: insufficient funds detection with faucet/bridge guidance, "already registered" revert catch, duplicate transformation detection via state read (v2) or event cache (v1), proactive balance warnings in health_check

### Agent Guidance (MCP Design Guidelines)
- **Adaptive health_check**: Returns `ready` boolean, `_recommendations`, `_gateway`, and contextual `_next` based on stamp availability
- **Response hints**: All success responses append `_next: <tool>` and `_related: <tools>` guiding agents to the logical next step
- **Structured errors**: All error responses include `retryable: true|false` and `_next` recovery hint
- **Typo correction**: Unknown tool names get Levenshtein-based "Did you mean?" suggestions
- **MCP Prompts**: 4 workflow prompts (`provenance-upload`, `provenance-verify`, `stamp-management`, `provenance-chain-workflow`) registered via `@server.list_prompts()` / `@server.get_prompt()`
- **MCP Resources**: `provenance://skills` resource (SKILLS.md content) via `@server.list_resources()` / `@server.read_resource()`
- **Gateway status**: health_check reports swarm_connect gateway connectivity in `_gateway` field
- **Insufficient funds**: `_is_insufficient_funds_error()` and `_format_insufficient_funds_error()` provide faucet/bridge URLs
- **Provenance lineage**: `get_provenance_chain` uses state reads on v2 contracts (`getTransformationLinks`/`getTransformationParents`) or `DataTransformed` event logs on v1 contracts for bidirectional traversal from any node. Events are cached in-memory (`chain/event_cache.py`): full scan on first call, incremental scans on subsequent calls (<1s vs ~20s). Also scans `DataMerged` events for merge traversal.
- **Storage references**: v3+ contracts support bidirectional linking between data hashes and storage references (e.g. Swarm hashes). `anchor_hash` accepts optional `storage_ref`, `set_storage_ref` attaches a ref post-hoc (set-once), `lookup_by_storage_ref` does reverse lookup. Feature detected via `supports_storage_ref()`.
- Helper functions: `_format_hints()`, `_format_error()`, `_is_retryable_error()`, `_suggest_tool_name()`
- **Untrusted content** (`untrusted.py`): text anyone can write (Swarm downloads, stamp labels, on-chain `dataType` and transformation descriptions) must never sit bare next to the hints. Multi-line content goes through `fence()` (nonce markers, warning line, 16 KB cap, control/bidi characters escaped); short third-party fields through `quote()` (single line, 200-char cap) plus `QUOTED_NOTE`; gateway fields through `one_line()` / `_gw()` when printed. `gateway_client` also flattens (no escaping) and caps every string in every JSON body it returns (`_clean_strings`), so fields added later cannot carry a line break; printing sites escape once. `_format_error()` and the fence body mark hint-like lines as quoted, matching on an NFKD + confusables fold so homoglyph keys and colons are caught. `download_data` returns the summary and hints as the first content block and the fenced content as a second block. Binary downloads are described (size, SHA-256), never dumped. On-chain lists (transformations, accessors) are capped at 50 entries; `get_provenance_chain` output is capped at about 32 KB.

### Code Quality
- Black formatting with 88-character line length
- Ruff linting with comprehensive rule set (ignores specific rules for MCP compatibility)
- Type hints throughout codebase
- Async/await patterns for MCP tool handlers

### Gateway Integration
- All Swarm operations go through the centralized gateway
- HTTP client handles authentication, retries, and error responses
- Gateway URL is configurable via environment variables
- Proper JSON request/response handling

## Testing Requirements

Before submitting changes:
1. Run `pytest` to ensure all tests pass
2. Run `black swarm_provenance_mcp/` to format code
3. Run `ruff check swarm_provenance_mcp/` to lint code
4. Verify MCP server starts successfully with `swarm-provenance-mcp`

## Integration Dependencies

This MCP server requires a running `swarm_connect` FastAPI gateway service. The gateway must be accessible at the configured `SWARM_GATEWAY_URL` and provide the following endpoints:
- `POST /api/v1/stamps/` - Purchase stamps
- `GET /api/v1/stamps/` - List stamps
- `GET /api/v1/stamps/{id}` - Get stamp details (includes `propagationStatus`, `secondsSincePurchase`, `estimatedReadyAt`)
- `GET /api/v1/stamps/{id}/check` - Stamp health check (includes propagation timing fields)
- `PATCH /api/v1/stamps/{id}/extend` - Extend stamps
- `POST /api/v1/data/` - Upload data
- `GET /api/v1/data/{reference}` - Download data
- `GET /api/v1/wallet` - Wallet info
- `GET /api/v1/notary/info` - Notary service info

## Writing style

Write all communication in the style of the [Microsoft Writing Style Guide](https://learn.microsoft.com/en-us/style-guide/welcome/). This covers issues, pull requests, review and issue comments, commit messages, and documentation.

- Use plain, direct language, with one idea per sentence. State what happened and why. Don't use figurative or clever phrasing.
- Use active voice and present tense. In documentation, address the reader as "you".
- Use sentence-case headings, and keep paragraphs short.
- Use the exact technical terms: endpoint, flag, field and function names as they appear in the code. Define any term the reader might not know, and use one term per concept.
- Spell out an abbreviation the first time you use it. Don't use slang or filler.
- Use bulleted lists for parallel items, numbered lists for steps, and tables for comparisons.
- Say whether each claim is verified, and how, or whether it is a hypothesis.

## PLUR Domain Scoping

When calling `plur_learn`, always set:
- `domain`: `provenance.mcp`
- `scope`: `project:swarm-provenance-mcp`

This ensures engrams are tagged for retrieval in the right context across the global store.

## PLUR Memory

You have persistent memory via PLUR. Corrections, preferences, and conventions persist across sessions as engrams.

> **PLUR is its own MCP server.** The tools below come from the `plur` MCP server registered by `plur init` in your own per-developer configuration, not in this repository — `plur_session_start`, `plur_learn`, `plur_recall_hybrid`, `plur_feedback`, `plur_session_end`. If you do not see these exact tool names, **PLUR is not connected**: stop and run `plur doctor` to diagnose. Do **not** substitute tools from other MCP servers (e.g. `datacore_*`) — those belong to a different system and will not persist anything for PLUR.

### Session Workflow

1. **Start**: Call `plur_session_start` with task description — injects relevant engrams
2. **Learn**: When corrected or discovering something new, call `plur_learn` immediately
3. **Recall**: Before answering factual questions, call `plur_recall_hybrid` — check memory first
4. **Feedback**: Rate injected engrams with `plur_feedback` (positive/negative) — trains relevance
5. **End**: Call `plur_session_end` with summary + engram_suggestions

Do not ask permission to use these tools — they are your memory system.

### When to check memory

Before reaching for web search, file reads, or guessing — apply this priority:
1. Is the answer already in engrams? → `plur_recall_hybrid`
2. Is the answer in the local filesystem? → Read/Grep/Glob
3. Is the answer derivable from context already loaded? → Just answer
4. Only if 1-3 fail → Use external tools

| Domain | When to recall |
|--------|----------------|
| Decisions | Past design choices, architecture rationale |
| Corrections | API quirks, bugs, wrong assumptions |
| Preferences | Formatting, tone, workflow, tool choices |
| Conventions | Tag formats, file routing, naming rules |
| Infrastructure | Server IPs, SSH configs, deployment targets |

### When corrected

When the user corrects you ("no, use X not Y", "that's wrong"):
1. Call `plur_learn` immediately — before continuing the task
2. Call `plur_feedback` with negative signal on the wrong engram if one was injected
3. Then continue with the corrected approach

### Verification

When recalling facts that will drive actions:
1. State the recalled fact explicitly before acting on it
2. Include the engram ID or search that produced it
3. If no engram matches, say so and verify from the filesystem
4. Never interpolate between two engrams to produce a "probably correct" composite
