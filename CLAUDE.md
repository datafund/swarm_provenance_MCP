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

### Communication Flow
```
AI Agents → MCP Server → Gateway Client → swarm_connect Gateway → Swarm Network
```

### Available MCP Tools
- `purchase_stamp` - Create new postage stamps
- `get_stamp_status` - Retrieve detailed stamp information (includes utilization data)
- `list_stamps` - List all available stamps
- `extend_stamp` - Add funds to existing stamps

### Dependencies Architecture
- **MCP Framework**: Uses `mcp>=1.0.0` for protocol implementation
- **HTTP Client**: Uses `requests>=2.31.0` for gateway communication
- **Data Validation**: Uses `pydantic>=2.0.0` for settings and request validation
- **Configuration**: Uses `python-dotenv>=1.0.0` for environment management

### Testing Strategy
- **Unit Tests**: Mock-based testing of gateway client in `test_gateway_client.py`
- **Integration Tests**: End-to-end MCP tool testing in `test_integration.py`
- **Async Support**: Uses `pytest-asyncio` for async test execution
- **Mocking**: Uses `pytest-mock` for external dependency mocking

## Configuration Management

### Environment Variables
- `SWARM_GATEWAY_URL`: Gateway endpoint (default: `http://localhost:8001`)
- `DEFAULT_STAMP_AMOUNT`: Default stamp amount in wei (default: `2000000000`)
- `DEFAULT_STAMP_DEPTH`: Default stamp depth (default: `17`)
- `MCP_SERVER_NAME`: Server identification (default: `swarm-provenance-mcp`)
- `MCP_SERVER_VERSION`: Server version (default: `0.1.0`)

### Settings Management
The `config.py` module uses Pydantic Settings for type-safe configuration with automatic environment variable loading and validation.

## Development Patterns

### Error Handling
- Comprehensive error handling for HTTP requests with user-friendly messages
- Proper MCP error responses with structured error information
- Request timeout handling and retry logic in gateway client

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
- `GET /api/v1/stamps/{id}` - Get stamp details
- `PATCH /api/v1/stamps/{id}/extend` - Extend stamps
- `POST /api/v1/data/` - Upload data
- `GET /api/v1/data/{reference}` - Download data