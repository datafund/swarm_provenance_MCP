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
cp .env.example .env
# Edit .env to set SWARM_GATEWAY_URL and stamp defaults
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
- `chain/client.py` — High-level facade (anchor, verify, transform, access)
- `chain/provider.py` — Web3 RPC connection management
- `chain/wallet.py` — Private key loading and transaction signing
- `chain/contract.py` — DataProvenance contract wrapper (build_*_tx, read methods, event queries)
- `chain/event_cache.py` — In-memory cache for DataTransformed events (singleton per chain+contract, incremental scans)
- `chain/models.py` — Pydantic models (AnchorResult, ChainProvenanceRecord, etc.)
- `chain/exceptions.py` — Standalone exception hierarchy (ChainError base)
- `chain/abi/DataProvenance.json` — Contract ABI

### Available MCP Tools

#### Swarm Gateway Tools
- `purchase_stamp` - Create new postage stamps (response includes propagation timing)
- `get_stamp_status` - Retrieve detailed stamp information (includes utilization + propagation data)
- `list_stamps` - List local stamps with access mode (owned/shared) and propagation status
  Stamps have `accessMode`: `"owned"` (dedicated, yours) or `"shared"` (public, any gateway user). Use owned stamps for production.
- `extend_stamp` - Add funds to existing stamps
- `upload_data` - Upload data to Swarm (max 4KB)
- `download_data` - Download data from Swarm by reference hash
- `check_stamp_health` - Diagnose stamp upload readiness with errors/warnings and propagation timing
- `get_wallet_info` - Node wallet address and BZZ balance (debug, may be removed)
- `get_notary_info` - Check notary signing service availability
- `health_check` - Gateway connectivity status

#### Chain Tools (requires `CHAIN_ENABLED=true`)

| Tool | Wallet Key | Gas | Description |
|------|-----------|-----|-------------|
| `chain_health` | not needed | no | Test RPC connectivity |
| `chain_balance` | **required** | no | Check wallet ETH balance with funding guidance |
| `verify_hash` | not needed | no | Check if hash is registered on-chain |
| `get_provenance` | not needed | no | Retrieve full on-chain provenance record |
| `get_provenance_chain` | not needed | no | Follow transformation lineage tree bidirectionally via event logs |
| `anchor_hash` | **required** | **yes** | Register Swarm hash on-chain |
| `record_transform` | **required** | **yes** | Record data transformation, link original → new hash |

Blockchain dependencies (web3, eth-account) are included in the default install. Set `CHAIN_ENABLED=true` to activate chain tools. Read-only tools (`verify_hash`, `get_provenance`, `get_provenance_chain`, `chain_health`) work without `PROVENANCE_WALLET_KEY` by creating a temporary provider + contract for direct contract reads. Write tools (`anchor_hash`, `record_transform`) and `chain_balance` require a funded wallet. Default RPC is `https://sepolia.base.org` (public, no API key needed); override with `CHAIN_RPC_URL`.

### Dependencies Architecture
- **MCP Framework**: Uses `mcp>=1.0.0` for protocol implementation
- **HTTP Client**: Uses `requests>=2.31.0` for gateway communication
- **Data Validation**: Uses `pydantic>=2.0.0` for settings and request validation
- **Configuration**: Uses `python-dotenv>=1.0.0` for environment management

### Testing Strategy
- **Gateway Client Tests** (`test_gateway_client.py`): Mock-based testing of HTTP client
- **Tool Execution Tests** (`test_tool_execution.py`): Handler-level tests for all MCP tools including chain tools, with mocked chain_client/CHAIN_AVAILABLE. Covers insufficient funds handling, event-based chain traversal, already-registered reverts, duplicate transformation detection, and proactive health_check balance warnings.
- **Tool Definition Tests** (`test_tool_definitions.py`): Validates tool schemas, required parameters, and registration consistency
- **Integration Tests** (`test_integration.py`): End-to-end MCP tool testing
- **Performance Tests** (`test_performance_regression.py`): Handler response time and concurrency regression tests
- **User Tests** (`user-tests/`): Live end-to-end UX audit against real gateway and blockchain (not run in CI)
- **Async Support**: Uses `pytest-asyncio` for async test execution
- **Mocking**: Uses `pytest-mock` and `unittest.mock` for external dependency mocking

## Configuration Management

### Environment Variables
- `SWARM_GATEWAY_URL`: Gateway endpoint (default: `https://provenance-gateway.datafund.io`)
- `DEFAULT_STAMP_AMOUNT`: Default stamp amount in wei (default: `2000000000`)
- `DEFAULT_STAMP_DEPTH`: Default stamp depth (default: `17`)
- `PAYMENT_MODE`: Gateway payment tier — `free` for rate-limited free tier (default: `free`)
- `MCP_SERVER_NAME`: Server identification (default: `swarm-provenance-mcp`)
- `MCP_SERVER_VERSION`: Server version (default: `0.1.0`)
- `CHAIN_ENABLED`: Enable on-chain provenance anchoring (default: `false`)
- `CHAIN_NAME`: Blockchain network (`base-sepolia` or `base`, default: `base-sepolia`)
- `PROVENANCE_WALLET_KEY`: Private key for chain transactions (hex, with or without 0x)
- `CHAIN_RPC_URL`: Custom RPC endpoint (uses chain preset if not set)
- `CHAIN_RPC_URLS`: Comma-separated fallback RPC URLs, tried in order after `CHAIN_RPC_URL`
- `CHAIN_CONTRACT`: Custom DataProvenance contract address (uses chain preset if not set)
- `CHAIN_EXPLORER_URL`: Custom block explorer URL (uses chain preset if not set)
- `CHAIN_GAS_LIMIT`: Explicit gas limit for chain transactions (skips estimation if set)

### Settings Management
The `config.py` module uses Pydantic Settings for type-safe configuration with automatic environment variable loading and validation.

## Development Patterns

### Error Handling
- Comprehensive error handling for HTTP requests with user-friendly messages
- Proper MCP error responses with structured error information
- Request timeout handling and retry logic in gateway client
- Chain-specific error handling: insufficient funds detection with faucet/bridge guidance, "already registered" revert catch, duplicate transformation detection via event cache, proactive balance warnings in health_check

### Agent Guidance (MCP Design Guidelines)
- **Adaptive health_check**: Returns `ready` boolean, `_recommendations`, `_companion_servers`, and contextual `_next` based on stamp availability
- **Response hints**: All success responses append `_next: <tool>` and `_related: <tools>` guiding agents to the logical next step
- **Structured errors**: All error responses include `retryable: true|false` and `_next` recovery hint
- **Typo correction**: Unknown tool names get Levenshtein-based "Did you mean?" suggestions
- **MCP Prompts**: 4 workflow prompts (`provenance-upload`, `provenance-verify`, `stamp-management`, `provenance-chain-workflow`) registered via `@server.list_prompts()` / `@server.get_prompt()`
- **MCP Resources**: `provenance://skills` resource (SKILLS.md content) via `@server.list_resources()` / `@server.read_resource()`
- **Cross-server coordination**: health_check reports companion servers (swarm_connect gateway status, fds-id MCP availability)
- **Insufficient funds**: `_is_insufficient_funds_error()` and `_format_insufficient_funds_error()` provide faucet/bridge URLs
- **Event-based lineage**: `get_provenance_chain` uses `DataTransformed` contract events bidirectionally (forward via `originalDataHash`, reverse via `newDataHash`) for accurate transformation traversal from any node. Events are cached in-memory (`chain/event_cache.py`): full scan on first call, incremental scans on subsequent calls (<1s vs ~20s)
- Helper functions: `_format_hints()`, `_format_error()`, `_is_retryable_error()`, `_suggest_tool_name()`

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