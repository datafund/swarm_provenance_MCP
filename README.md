# Swarm Provenance MCP

[![Regression and Safety Tests](https://github.com/datafund/swarm_provenance_MCP/actions/workflows/regression_tests.yml/badge.svg)](https://github.com/datafund/swarm_provenance_MCP/actions/workflows/regression_tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> ⚠️ **ALPHA SOFTWARE - PROOF OF CONCEPT**
> This software is in **Alpha stage** and should be considered a **Proof of Concept**. Use for testing and experimentation only. Not recommended for production use.

> ⚠️ **DATA PERSISTENCE WARNING**
> Storage on Swarm is **rented storage** with limited time periods. The default configuration uses very short rental periods (approximately **1 day**). **Do not expect uploaded data to persist longer than the rental period.** Data will become unavailable when the postage stamp expires.

A Model Context Protocol (MCP) server for managing Swarm postage stamps and provenance data storage through a centralized FastAPI gateway. Enables AI agents to upload provenance data to the decentralized Swarm network for immutable storage and retrieve it by reference.

## Overview

This MCP server provides tools for AI agents to interact with Swarm postage stamps and provenance data storage, including purchasing and extending stamps, uploading provenance data to Swarm for immutable decentralized storage, and downloading data from the network by reference. It acts as a bridge between AI agents and the `swarm_connect` FastAPI gateway.

## Provenance & Immutable Storage

This MCP server is specifically designed for provenance data use cases, leveraging Swarm's decentralized network to provide:

- **Immutable Records**: Once uploaded, data cannot be altered, ensuring integrity
- **Decentralized Storage**: No single point of failure or central authority
- **Provenance Metadata**: Support for structured provenance records with creator, timestamp, and lineage information
- **Verifiable Authenticity**: Cryptographic integrity verification for uploaded data

## Features

- **Purchase Stamps**: Create new postage stamps with configurable amount and depth
- **Stamp Status**: Get detailed information about specific stamps
- **List Stamps**: View all available postage stamps
- **Extend Stamps**: Add additional funds to existing stamps
- **Data Upload**: Upload data to Swarm network with stamp validation
- **Data Download**: Download data from Swarm network by reference
- **Provenance Storage**: Store data with provenance metadata for immutable, verifiable records
- **Health Monitoring**: Check gateway and Swarm network connectivity

## Installation

### Prerequisites

- Python 3.8 or higher (use `python3` command)
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
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install the package:
```bash
pip install -e .
```

4. Configure environment variables:
```bash
cp .env.example .env
# Edit .env to configure your gateway URL and defaults
```

### Docker

Run the MCP server in a container with no local dependencies.

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
  -e DEFAULT_STAMP_AMOUNT=2000000000 \
  -e DEFAULT_STAMP_DEPTH=17 \
  swarm-provenance-mcp
```

**Docker Compose:**
```bash
docker compose build
docker compose run --rm swarm-provenance-mcp
```

| Variable | Description | Default |
|----------|-------------|---------|
| `SWARM_GATEWAY_URL` | Gateway endpoint URL | `https://provenance-gateway.datafund.io` |
| `DEFAULT_STAMP_AMOUNT` | Default stamp amount in wei | `2000000000` |
| `DEFAULT_STAMP_DEPTH` | Default stamp depth: 17 (small), 20 (medium), 22 (large) | `17` |
| `PAYMENT_MODE` | Gateway payment tier (`free` = 3 write req/min) | `free` |

## Configuration

Environment variables (set in `.env` file):

- `SWARM_GATEWAY_URL`: URL of the swarm_connect FastAPI gateway (default: `https://provenance-gateway.datafund.io`)
- `DEFAULT_STAMP_AMOUNT`: Default amount for new stamps in wei (default: `2000000000`)
- `DEFAULT_STAMP_DEPTH`: Default depth for new stamps (default: `17`)
- `PAYMENT_MODE`: Gateway payment tier (default: `free` — rate limited to 3 write requests/minute)

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
3. Update your `.env` file: `SWARM_GATEWAY_URL=http://localhost:8000`

## Usage

### Running the MCP Server

```bash
swarm-provenance-mcp
```

### Available Tools

#### `purchase_stamp`
Purchase a new postage stamp.

**Parameters:**
- `amount` (int): Amount in wei (optional, uses default if not provided)
- `depth` (int): Stamp depth (optional, uses default if not provided)
- `label` (string): Optional label for the stamp

**Example:**
```json
{
  "name": "purchase_stamp",
  "arguments": {
    "amount": 1000000000,
    "depth": 17,
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

**Parameters:** None

**Example:**
```json
{
  "name": "list_stamps",
  "arguments": {}
}
```

#### `extend_stamp`
Extend an existing stamp with additional funds.

**Parameters:**
- `stamp_id` (string): The batch ID of the stamp to extend
- `amount` (int): Additional amount to add in wei

**Example:**
```json
{
  "name": "extend_stamp",
  "arguments": {
    "stamp_id": "000de42079daebd58347bb38ce05bdc477701d93651d3bba318a9aee3fbd786a",
    "amount": 500000000
  }
}
```

#### `upload_data`
Upload data to the Swarm network with stamp validation.

**Parameters:**
- `data` (string): Data content to upload (max 4096 bytes)
- `stamp_id` (string): Postage stamp ID to use for upload
- `content_type` (string): MIME type of the content (optional, default: "application/json")

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

#### `download_data`
Download data from the Swarm network using a reference hash.

**Parameters:**
- `reference` (string): Swarm reference hash of the data to download

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
Check gateway and Swarm network connectivity status. Returns an adaptive status including stamp availability, a `ready` flag indicating whether uploads can proceed, and recommendations for next steps.

**Parameters:** None

**Example:**
```json
{
  "name": "health_check",
  "arguments": {}
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

Hints are contextual — for example, `list_stamps` suggests `_next: upload_data` when usable stamps exist, but `_next: purchase_stamp` when none are available.

#### Error Responses

Error responses include structured recovery information:

```
retryable: true|false            # Whether retrying the same call may succeed
_next: <recovery_tool>           # Tool to call for recovery
```

- **retryable: true** — transient errors (timeouts, rate limits, 502/503/504). Wait and retry.
- **retryable: false** — permanent errors (validation failures, unknown tools). Fix input and try a different approach.

#### Adaptive Health Check

The `health_check` tool returns additional fields:

```
ready: true|false                # Whether the system is ready for uploads
_recommendations:                # Actionable suggestions (only when issues exist)
  - No stamps found — purchase one before uploading
_companion_servers:              # Related servers in the ecosystem
  - swarm_connect gateway: <url> (connected|unreachable)
  - fds-id MCP: optional (identity/signing for provenance chain anchoring)
```

### MCP Prompts

The server provides workflow prompts that agents can invoke via `prompts/list` and `prompts/get`. These give step-by-step instructions for common tasks:

| Prompt | Description | Arguments |
|--------|-------------|-----------|
| `provenance-upload` | Upload data to Swarm: health check, stamp selection, upload, verify | `data` (required), `content_type` (optional) |
| `provenance-verify` | Download and verify existing data by reference | `reference` (required) |
| `stamp-management` | Review stamp inventory, diagnose issues, recommend actions | none |

## Docker

### Building

```bash
docker build -t swarm-provenance-mcp .
```

To tag with a specific version:

```bash
docker build --build-arg VERSION=0.1.0 -t swarm-provenance-mcp:0.1.0 .
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
  -e DEFAULT_STAMP_DEPTH=20 \
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
│ • Custom agents │    │ • Error handling│    │ • Status API    │
└─────────────────┘    └─────────────────┘    │ • Extension API │
                                              └─────────┬───────┘
                                                        │
                                              ┌─────────▼───────┐
                                              │  Swarm Network  │
                                              │   (Bee Node)    │
                                              └─────────────────┘
```

### Components

- **MCP Server**: Exposes tools via the Model Context Protocol
- **Gateway Client**: HTTP client for communicating with swarm_connect
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
cp .env.example .env
# Edit .env file if you need to customize gateway URL or defaults
```

3. **Configure MCP Server**: Add to Claude Desktop's configuration file:

**macOS/Linux** (`~/Library/Application Support/Claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "swarm-provenance": {
      "command": "/path/to/swarm_provenance_mcp/venv/bin/python",
      "args": ["-m", "swarm_provenance_mcp.server"],
      "cwd": "/path/to/swarm_provenance_mcp"
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
      "args": ["-m", "swarm_provenance_mcp.server"],
      "cwd": "C:\\path\\to\\swarm_provenance_mcp"
    }
  }
}
```

*Note: Replace `/path/to/swarm_provenance_mcp` with the actual path where you cloned the repository.*

**Alternative (if package is installed)**: You can use `"command": "swarm-provenance-mcp"` instead after running `pip install -e .`

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

> **Docker Desktop MCP Toolkit**: If you use Docker Desktop with MCP Toolkit support, the server can be discovered automatically from the Docker MCP catalog.

4. **Restart Claude Desktop** and test with: "List all available Swarm stamps"

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

## Dependencies

- **Core Dependencies**:
  - `mcp>=1.0.0`: Model Context Protocol framework
  - `requests>=2.31.0`: HTTP client for gateway communication
  - `pydantic>=2.0.0`: Data validation and settings
  - `python-dotenv>=1.0.0`: Environment configuration

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
5. **Verify data integrity**: Retrieve and verify immutable records from Swarm network
6. **Data lifecycle management**: Handle complete provenance workflows from creation to verification
7. **Automate workflows**: Integrate stamp management and data storage into larger AI workflows

## Troubleshooting

### Common Issues

1. **Connection errors**: Ensure the swarm_connect gateway is running and accessible
2. **Authentication errors**: Check that the gateway doesn't require authentication
3. **Invalid stamp IDs**: Verify stamp IDs are valid batch IDs from the Swarm network
4. **Timeout errors**: Increase timeout values if operations are taking too long

### Logging

The server logs important events and errors. To increase logging verbosity:

```python
import logging
logging.getLogger("swarm_provenance_mcp").setLevel(logging.DEBUG)
```

## License

MIT License - see LICENSE file for details.