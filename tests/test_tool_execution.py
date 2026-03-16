"""Tests for MCP tool execution and response validation."""

import pytest
import json
from unittest.mock import AsyncMock, patch, MagicMock
from typing import Dict, Any

from mcp.types import CallToolRequest, CallToolResult, TextContent
from swarm_provenance_mcp.server import create_server


# Valid 64-char hex IDs for tests
TEST_STAMP_ID = "a" * 64
TEST_REFERENCE = "b" * 64


async def call_tool_directly(server, name: str, arguments: Dict[str, Any]):
    """Call tool directly through the server's handler functions.

    Mirrors the server's call_tool wrapper including catch-all error handling.
    """
    from swarm_provenance_mcp.server import (
        handle_purchase_stamp, handle_get_stamp_status, handle_list_stamps,
        handle_extend_stamp, handle_upload_data, handle_download_data,
        handle_check_stamp_health, handle_get_wallet_info, handle_get_notary_info,
        handle_health_check, handle_chain_balance, handle_chain_health,
        handle_anchor_hash, handle_verify_hash, handle_get_provenance,
        handle_record_transform, handle_get_provenance_chain,
    )

    handlers = {
        "purchase_stamp": handle_purchase_stamp,
        "get_stamp_status": handle_get_stamp_status,
        "list_stamps": handle_list_stamps,
        "extend_stamp": handle_extend_stamp,
        "upload_data": handle_upload_data,
        "download_data": handle_download_data,
        "check_stamp_health": handle_check_stamp_health,
        "get_wallet_info": handle_get_wallet_info,
        "get_notary_info": handle_get_notary_info,
        "health_check": handle_health_check,
        "chain_balance": handle_chain_balance,
        "chain_health": handle_chain_health,
        "anchor_hash": handle_anchor_hash,
        "verify_hash": handle_verify_hash,
        "get_provenance": handle_get_provenance,
        "record_transform": handle_record_transform,
        "get_provenance_chain": handle_get_provenance_chain,
    }

    try:
        if name in handlers:
            return await handlers[name](arguments)
        else:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Unknown tool: {name}")],
                isError=True
            )
    except Exception as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Error executing {name}: {str(e)}")],
            isError=True
        )


class TestToolExecution:
    """Test suite for MCP tool execution and response validation."""

    @pytest.fixture
    def server(self):
        """Create MCP server instance for testing."""
        return create_server()

    @pytest.fixture
    def mock_gateway_client(self):
        """Mock gateway client for testing tool execution."""
        with patch('swarm_provenance_mcp.server.gateway_client') as mock_client:
            # Configure mock responses for different methods
            mock_client.purchase_stamp.return_value = {
                "batchID": TEST_STAMP_ID,
                "message": "Stamp purchased successfully"
            }
            mock_client.get_stamp_details.return_value = {
                "batchID": TEST_STAMP_ID,
                "amount": "2000000000",
                "depth": 17,
                "bucketDepth": 16,
                "blockNumber": 12345,
                "immutableFlag": False,
                "exists": True,
                "usable": True,
                "batchTTL": 123456
            }
            mock_client.list_stamps.return_value = {
                "stamps": [
                    {
                        "batchID": TEST_STAMP_ID,
                        "amount": "2000000000",
                        "depth": 17,
                        "utilization": 0.1,
                        "usable": True,
                        "accessMode": "owned",
                    }
                ],
                "total_count": 1
            }
            mock_client.extend_stamp.return_value = {
                "batchID": TEST_STAMP_ID,
                "message": "Stamp extended successfully"
            }
            mock_client.upload_data.return_value = {
                "reference": TEST_REFERENCE,
                "message": "Upload successful"
            }
            mock_client.download_data.return_value = b'{"test": "data content"}'
            mock_client.check_stamp_health.return_value = {
                "stamp_id": TEST_STAMP_ID,
                "can_upload": True,
                "errors": [],
                "warnings": [],
                "status": {
                    "exists": True,
                    "local": True,
                    "usable": True,
                    "utilizationPercent": 12.5,
                    "utilizationStatus": "ok",
                    "batchTTL": 86400,
                    "expectedExpiration": "2026-04-10-12-00"
                }
            }
            mock_client.get_wallet_info.return_value = {
                "walletAddress": "0x1234567890abcdef1234567890abcdef12345678",
                "bzzBalance": "5000000000000000"
            }
            mock_client.get_notary_info.return_value = {
                "enabled": True,
                "available": True,
                "address": "0xabcdef1234567890abcdef1234567890abcdef12",
                "message": "Notary service is operational"
            }
            mock_client.health_check.return_value = {
                "status": "healthy",
                "gateway_url": "http://localhost:8000",
                "response_time_ms": 15.5,
                "gateway_response": {"version": "1.0.0"}
            }

            yield mock_client

    async def test_purchase_stamp_tool(self, server, mock_gateway_client):
        """Test purchase_stamp tool execution."""
        # Test with default parameters
        result = await call_tool_directly(server, "purchase_stamp", {})

        assert isinstance(result, CallToolResult)
        assert not result.isError
        assert len(result.content) > 0
        assert isinstance(result.content[0], TextContent)

        # Verify gateway client was called
        mock_gateway_client.purchase_stamp.assert_called_once()

        # Test with custom parameters
        mock_gateway_client.reset_mock()
        result_with_params = await call_tool_directly(
            server, "purchase_stamp", {
                "amount": 5000000000,
                "depth": 18,
                "label": "test-stamp"
            }
        )
        assert not result_with_params.isError
        mock_gateway_client.purchase_stamp.assert_called_once_with(
            5000000000, 18, "test-stamp"
        )

    async def test_get_stamp_status_tool(self, server, mock_gateway_client):
        """Test get_stamp_status tool execution."""
        result = await call_tool_directly(
            server, "get_stamp_status", {"stamp_id": TEST_STAMP_ID}
        )

        assert isinstance(result, CallToolResult)
        assert not result.isError
        mock_gateway_client.get_stamp_details.assert_called_once_with(TEST_STAMP_ID)

        # Verify response contains expected information
        content_text = result.content[0].text
        assert "Stamp Details" in content_text
        assert TEST_STAMP_ID in content_text

    async def test_list_stamps_tool(self, server, mock_gateway_client):
        """Test list_stamps tool execution."""
        result = await call_tool_directly(server, "list_stamps", {})

        assert isinstance(result, CallToolResult)
        assert not result.isError
        mock_gateway_client.list_stamps.assert_called_once()

        # Verify response contains stamp information
        content_text = result.content[0].text
        assert "stamp" in content_text.lower()
        # Outdated network-visible warning must not appear
        assert "network-visible" not in content_text

    async def test_extend_stamp_tool(self, server, mock_gateway_client):
        """Test extend_stamp tool execution."""
        result = await call_tool_directly(
            server, "extend_stamp", {
                "stamp_id": TEST_STAMP_ID,
                "amount": 2000000000
            }
        )

        assert isinstance(result, CallToolResult)
        assert not result.isError
        mock_gateway_client.extend_stamp.assert_called_once_with(
            TEST_STAMP_ID, 2000000000
        )

    async def test_upload_data_tool(self, server, mock_gateway_client):
        """Test upload_data tool execution."""
        result = await call_tool_directly(
            server, "upload_data", {
                "data": "test data content",
                "stamp_id": TEST_STAMP_ID,
                "content_type": "text/plain"
            }
        )

        assert isinstance(result, CallToolResult)
        assert not result.isError
        mock_gateway_client.upload_data.assert_called_once()

    async def test_download_data_tool(self, server, mock_gateway_client):
        """Test download_data tool execution."""
        result = await call_tool_directly(
            server, "download_data", {"reference": TEST_REFERENCE}
        )

        assert isinstance(result, CallToolResult)
        assert not result.isError
        mock_gateway_client.download_data.assert_called_once_with(TEST_REFERENCE)

    async def test_check_stamp_health_tool(self, server, mock_gateway_client):
        """Test check_stamp_health tool execution."""
        result = await call_tool_directly(
            server, "check_stamp_health", {"stamp_id": TEST_STAMP_ID}
        )

        assert isinstance(result, CallToolResult)
        assert not result.isError
        mock_gateway_client.check_stamp_health.assert_called_once_with(TEST_STAMP_ID)

        content_text = result.content[0].text
        assert "healthy" in content_text.lower() or "ready" in content_text.lower()

    async def test_check_stamp_health_unhealthy(self, server, mock_gateway_client):
        """Test check_stamp_health with errors."""
        mock_gateway_client.check_stamp_health.return_value = {
            "stamp_id": TEST_STAMP_ID,
            "can_upload": False,
            "errors": [{"code": "EXPIRED", "message": "Stamp has expired", "suggestion": "Purchase a new stamp"}],
            "warnings": [],
            "status": {"exists": True, "usable": False}
        }

        result = await call_tool_directly(
            server, "check_stamp_health", {"stamp_id": TEST_STAMP_ID}
        )

        assert isinstance(result, CallToolResult)
        assert not result.isError  # Tool succeeded, stamp is unhealthy
        content_text = result.content[0].text
        assert "cannot" in content_text.lower()
        assert "EXPIRED" in content_text

    async def test_get_wallet_info_tool(self, server, mock_gateway_client):
        """Test get_wallet_info tool execution."""
        result = await call_tool_directly(server, "get_wallet_info", {})

        assert isinstance(result, CallToolResult)
        assert not result.isError
        mock_gateway_client.get_wallet_info.assert_called_once()

        content_text = result.content[0].text
        assert "0x1234" in content_text
        assert "Balance" in content_text

    async def test_get_notary_info_tool(self, server, mock_gateway_client):
        """Test get_notary_info tool execution."""
        result = await call_tool_directly(server, "get_notary_info", {})

        assert isinstance(result, CallToolResult)
        assert not result.isError
        mock_gateway_client.get_notary_info.assert_called_once()

        content_text = result.content[0].text
        assert "enabled" in content_text.lower() or "available" in content_text.lower()

    async def test_get_notary_info_disabled(self, server, mock_gateway_client):
        """Test get_notary_info when notary is disabled."""
        mock_gateway_client.get_notary_info.return_value = {
            "enabled": False,
            "available": False,
            "address": None,
            "message": "Notary service is not configured"
        }

        result = await call_tool_directly(server, "get_notary_info", {})

        assert isinstance(result, CallToolResult)
        assert not result.isError
        content_text = result.content[0].text
        assert "not enabled" in content_text.lower()

    async def test_health_check_tool(self, server, mock_gateway_client):
        """Test health_check tool execution."""
        result = await call_tool_directly(server, "health_check", {})

        assert isinstance(result, CallToolResult)
        assert not result.isError
        mock_gateway_client.health_check.assert_called_once()

        # Verify response contains expected information
        content_text = result.content[0].text
        assert "operational" in content_text.lower() or "healthy" in content_text.lower()

    async def test_invalid_tool_name(self, server, mock_gateway_client):
        """Test handling of invalid tool names."""
        result = await call_tool_directly(server, "invalid_tool_name", {})

        assert isinstance(result, CallToolResult)
        assert result.isError
        assert "Unknown tool" in result.content[0].text

    async def test_missing_required_parameters(self, server, mock_gateway_client):
        """Test handling of missing required parameters."""
        # Test get_stamp_status without required stamp_id
        result = await call_tool_directly(server, "get_stamp_status", {})

        assert isinstance(result, CallToolResult)
        assert result.isError
        error_text = result.content[0].text.lower()
        assert "stamp" in error_text or "required" in error_text

    async def test_gateway_error_handling(self, server):
        """Test error handling when gateway client raises exceptions."""
        with patch('swarm_provenance_mcp.server.gateway_client') as mock_client:
            mock_client.purchase_stamp.side_effect = Exception("Gateway connection failed")

            result = await call_tool_directly(server, "purchase_stamp", {})

            assert isinstance(result, CallToolResult)
            assert result.isError
            error_text = result.content[0].text.lower()
            assert "error" in error_text or "failed" in error_text

    async def test_response_format_consistency(self, server, mock_gateway_client):
        """Test that all tools return consistent response formats."""
        tools_to_test = [
            ("purchase_stamp", {}),
            ("get_stamp_status", {"stamp_id": TEST_STAMP_ID}),
            ("list_stamps", {}),
            ("extend_stamp", {"stamp_id": TEST_STAMP_ID, "amount": 2000000000}),
            ("upload_data", {"data": "test data", "stamp_id": TEST_STAMP_ID}),
            ("download_data", {"reference": TEST_REFERENCE}),
            ("check_stamp_health", {"stamp_id": TEST_STAMP_ID}),
            ("get_wallet_info", {}),
            ("get_notary_info", {}),
            ("health_check", {})
        ]

        for tool_name, arguments in tools_to_test:
            result = await call_tool_directly(server, tool_name, arguments)

            # All successful responses should have consistent structure
            assert isinstance(result, CallToolResult)
            assert len(result.content) > 0
            assert isinstance(result.content[0], TextContent)

            # Content should be valid (either JSON or meaningful text)
            content_text = result.content[0].text
            assert len(content_text) > 0

            # If it's JSON, it should parse correctly
            if content_text.strip().startswith('{'):
                try:
                    json.loads(content_text)
                except json.JSONDecodeError:
                    pytest.fail(f"Tool {tool_name} returned invalid JSON: {content_text}")


class TestResponseHints:
    """Test that tool responses include _next and _related hints."""

    @pytest.fixture
    def server(self):
        return create_server()

    @pytest.fixture
    def mock_gateway_client(self):
        """Mock gateway client with standard responses."""
        with patch('swarm_provenance_mcp.server.gateway_client') as mock_client:
            mock_client.purchase_stamp.return_value = {
                "batchID": TEST_STAMP_ID,
                "message": "Stamp purchased successfully"
            }
            mock_client.get_stamp_details.return_value = {
                "batchID": TEST_STAMP_ID,
                "amount": "2000000000",
                "depth": 17,
                "usable": True,
                "batchTTL": 123456
            }
            mock_client.list_stamps.return_value = {
                "stamps": [{"batchID": TEST_STAMP_ID, "usable": True}],
                "total_count": 1
            }
            mock_client.extend_stamp.return_value = {
                "batchID": TEST_STAMP_ID,
                "message": "Stamp extended"
            }
            mock_client.upload_data.return_value = {
                "reference": TEST_REFERENCE,
                "message": "Upload successful"
            }
            mock_client.download_data.return_value = b'{"test": "data"}'
            mock_client.check_stamp_health.return_value = {
                "stamp_id": TEST_STAMP_ID,
                "can_upload": True,
                "errors": [],
                "warnings": [],
                "status": {"utilizationPercent": 10, "batchTTL": 86400}
            }
            mock_client.get_wallet_info.return_value = {
                "walletAddress": "0x1234",
                "bzzBalance": "5000"
            }
            mock_client.get_notary_info.return_value = {
                "enabled": True,
                "available": True,
                "address": "0xabcdef",
                "message": "Operational"
            }
            mock_client.health_check.return_value = {
                "status": "healthy",
                "gateway_url": "http://localhost:8000",
                "response_time_ms": 15
            }
            yield mock_client

    async def test_all_success_responses_have_next_hint(self, server, mock_gateway_client):
        """Every successful tool response must include a _next hint."""
        tools_to_test = [
            ("purchase_stamp", {}),
            ("get_stamp_status", {"stamp_id": TEST_STAMP_ID}),
            ("list_stamps", {}),
            ("extend_stamp", {"stamp_id": TEST_STAMP_ID, "amount": 2000000000}),
            ("upload_data", {"data": "test", "stamp_id": TEST_STAMP_ID}),
            ("download_data", {"reference": TEST_REFERENCE}),
            ("check_stamp_health", {"stamp_id": TEST_STAMP_ID}),
            ("get_wallet_info", {}),
            ("get_notary_info", {}),
            ("health_check", {}),
        ]

        for tool_name, arguments in tools_to_test:
            result = await call_tool_directly(server, tool_name, arguments)
            content_text = result.content[0].text
            assert "_next:" in content_text, \
                f"Tool '{tool_name}' success response missing _next hint"

    async def test_purchase_stamp_hints(self, server, mock_gateway_client):
        """purchase_stamp should suggest check_stamp_health next."""
        result = await call_tool_directly(server, "purchase_stamp", {})
        text = result.content[0].text
        assert "_next: check_stamp_health" in text
        assert "_related:" in text

    async def test_list_stamps_hints_with_usable(self, server, mock_gateway_client):
        """list_stamps with usable stamps should suggest upload_data."""
        result = await call_tool_directly(server, "list_stamps", {})
        text = result.content[0].text
        assert "_next: upload_data" in text

    async def test_list_stamps_hints_no_stamps(self, server, mock_gateway_client):
        """list_stamps with no stamps should suggest purchase_stamp."""
        mock_gateway_client.list_stamps.return_value = {"stamps": [], "total_count": 0}
        result = await call_tool_directly(server, "list_stamps", {})
        text = result.content[0].text
        assert "_next: purchase_stamp" in text

    async def test_get_stamp_status_hints_usable(self, server, mock_gateway_client):
        """get_stamp_status for usable stamp should suggest upload_data."""
        result = await call_tool_directly(
            server, "get_stamp_status", {"stamp_id": TEST_STAMP_ID}
        )
        text = result.content[0].text
        assert "_next: upload_data" in text

    async def test_get_stamp_status_hints_not_usable(self, server, mock_gateway_client):
        """get_stamp_status for non-usable stamp should suggest purchase_stamp."""
        mock_gateway_client.get_stamp_details.return_value = {
            "batchID": TEST_STAMP_ID, "usable": False, "amount": "0", "depth": 17
        }
        result = await call_tool_directly(
            server, "get_stamp_status", {"stamp_id": TEST_STAMP_ID}
        )
        text = result.content[0].text
        assert "_next: purchase_stamp" in text

    async def test_upload_data_hints(self, server, mock_gateway_client):
        """upload_data should suggest download_data next."""
        result = await call_tool_directly(
            server, "upload_data", {"data": "test", "stamp_id": TEST_STAMP_ID}
        )
        text = result.content[0].text
        assert "_next: download_data" in text

    async def test_check_stamp_health_hints_healthy(self, server, mock_gateway_client):
        """Healthy stamp should suggest upload_data."""
        result = await call_tool_directly(
            server, "check_stamp_health", {"stamp_id": TEST_STAMP_ID}
        )
        text = result.content[0].text
        assert "_next: upload_data" in text

    async def test_check_stamp_health_hints_unhealthy(self, server, mock_gateway_client):
        """Unhealthy stamp should suggest purchase_stamp."""
        mock_gateway_client.check_stamp_health.return_value = {
            "stamp_id": TEST_STAMP_ID,
            "can_upload": False,
            "errors": [{"code": "EXPIRED", "message": "Expired"}],
            "warnings": [],
            "status": {}
        }
        result = await call_tool_directly(
            server, "check_stamp_health", {"stamp_id": TEST_STAMP_ID}
        )
        text = result.content[0].text
        assert "_next: purchase_stamp" in text


class TestPropagationFields:
    """Test that stamp handlers surface gateway propagation timing fields."""

    @pytest.fixture
    def server(self):
        return create_server()

    async def test_health_check_shows_propagation_status(self, server):
        """check_stamp_health should display propagationStatus and timing."""
        with patch('swarm_provenance_mcp.server.gateway_client') as mock_client:
            mock_client.check_stamp_health.return_value = {
                "stamp_id": TEST_STAMP_ID,
                "can_upload": False,
                "errors": [{"code": "NOT_USABLE", "message": "Not usable yet"}],
                "warnings": [],
                "status": {"utilizationPercent": 0},
                "propagationStatus": "propagating",
                "secondsSincePurchase": 30,
                "estimatedReadyAt": "2026-03-16T12:30:00Z",
            }
            result = await call_tool_directly(
                server, "check_stamp_health", {"stamp_id": TEST_STAMP_ID}
            )

        text = result.content[0].text
        assert "propagating" in text
        assert "30s ago" in text
        assert "2026-03-16T12:30:00Z" in text

    async def test_health_check_propagating_uses_estimated_ready(self, server):
        """Propagating stamp should show estimated ready time in hint."""
        with patch('swarm_provenance_mcp.server.gateway_client') as mock_client:
            mock_client.check_stamp_health.return_value = {
                "stamp_id": TEST_STAMP_ID,
                "can_upload": False,
                "errors": [{"code": "NOT_USABLE", "message": "Not usable"}],
                "warnings": [],
                "status": {},
                "propagationStatus": "propagating",
                "secondsSincePurchase": 15,
                "estimatedReadyAt": "2026-03-16T12:32:00Z",
            }
            result = await call_tool_directly(
                server, "check_stamp_health", {"stamp_id": TEST_STAMP_ID}
            )

        text = result.content[0].text
        assert "Estimated ready at 2026-03-16T12:32:00Z" in text
        assert "_next: check_stamp_health" in text

    async def test_health_check_no_propagation_fields(self, server):
        """Older gateway without propagation fields should fall back to heuristic."""
        with patch('swarm_provenance_mcp.server.gateway_client') as mock_client:
            mock_client.check_stamp_health.return_value = {
                "stamp_id": TEST_STAMP_ID,
                "can_upload": False,
                "errors": [{"code": "NOT_FOUND", "message": "Not found"}],
                "warnings": [],
                "status": {},
            }
            result = await call_tool_directly(
                server, "check_stamp_health", {"stamp_id": TEST_STAMP_ID}
            )

        text = result.content[0].text
        assert "propagating on the blockchain" in text
        assert "_next: check_stamp_health" in text

    async def test_stamp_status_shows_propagation(self, server):
        """get_stamp_status should display propagation fields when present."""
        with patch('swarm_provenance_mcp.server.gateway_client') as mock_client:
            mock_client.get_stamp_details.return_value = {
                "amount": "2000000000",
                "depth": 17,
                "bucketDepth": 16,
                "blockNumber": 12345,
                "batchTTL": 86400,
                "expectedExpiration": "2026-04-15",
                "usable": False,
                "utilization": 0,
                "immutableFlag": False,
                "local": True,
                "propagationStatus": "propagating",
                "secondsSincePurchase": 45,
                "estimatedReadyAt": "2026-03-16T12:35:00Z",
            }
            result = await call_tool_directly(
                server, "get_stamp_status", {"stamp_id": TEST_STAMP_ID}
            )

        text = result.content[0].text
        assert "propagating" in text
        assert "45s since purchase" in text
        assert "2026-03-16T12:35:00Z" in text

    async def test_purchase_stamp_propagating(self, server):
        """purchase_stamp should show estimated ready when propagating."""
        with patch('swarm_provenance_mcp.server.gateway_client') as mock_client:
            mock_client.purchase_stamp.return_value = {
                "batchID": TEST_STAMP_ID,
                "propagationStatus": "propagating",
                "estimatedReadyAt": "2026-03-16T12:40:00Z",
            }
            result = await call_tool_directly(server, "purchase_stamp", {})

        text = result.content[0].text
        assert "propagating" in text
        assert "2026-03-16T12:40:00Z" in text

    async def test_purchase_stamp_ready_immediately(self, server):
        """purchase_stamp from pool should show ready immediately."""
        with patch('swarm_provenance_mcp.server.gateway_client') as mock_client:
            mock_client.purchase_stamp.return_value = {
                "batchID": TEST_STAMP_ID,
                "propagationStatus": "ready",
            }
            result = await call_tool_directly(server, "purchase_stamp", {})

        text = result.content[0].text
        assert "ready immediately" in text


class TestStampAccessMode:
    """Test that list_stamps surfaces accessMode and propagationStatus from gateway."""

    @pytest.fixture
    def server(self):
        return create_server()

    async def test_list_stamps_shows_access_mode_owned(self, server):
        """Stamp with accessMode 'owned' should display 'owned' in output."""
        with patch('swarm_provenance_mcp.server.gateway_client') as mock_client:
            mock_client.list_stamps.return_value = {
                "stamps": [
                    {
                        "batchID": TEST_STAMP_ID,
                        "usable": True,
                        "accessMode": "owned",
                    }
                ],
                "total_count": 1,
            }
            result = await call_tool_directly(server, "list_stamps", {})

        text = result.content[0].text
        assert "Access: owned" in text
        assert "network-visible" not in text

    async def test_list_stamps_shows_access_mode_shared(self, server):
        """Stamp with accessMode 'shared' should display 'shared' in output."""
        with patch('swarm_provenance_mcp.server.gateway_client') as mock_client:
            mock_client.list_stamps.return_value = {
                "stamps": [
                    {
                        "batchID": TEST_STAMP_ID,
                        "usable": True,
                        "accessMode": "shared",
                    }
                ],
                "total_count": 1,
            }
            result = await call_tool_directly(server, "list_stamps", {})

        text = result.content[0].text
        assert "Access: shared" in text

    async def test_list_stamps_propagation_status(self, server):
        """Stamp with propagationStatus should display it in output."""
        with patch('swarm_provenance_mcp.server.gateway_client') as mock_client:
            mock_client.list_stamps.return_value = {
                "stamps": [
                    {
                        "batchID": TEST_STAMP_ID,
                        "usable": False,
                        "accessMode": "owned",
                        "propagationStatus": "propagating",
                    }
                ],
                "total_count": 1,
            }
            result = await call_tool_directly(server, "list_stamps", {})

        text = result.content[0].text
        assert "Propagation: propagating" in text

    async def test_list_stamps_public_only_hint(self, server):
        """When all stamps are shared (public), show guidance about dedicated stamps."""
        with patch('swarm_provenance_mcp.server.gateway_client') as mock_client:
            mock_client.list_stamps.return_value = {
                "stamps": [
                    {
                        "batchID": TEST_STAMP_ID,
                        "usable": True,
                        "accessMode": "shared",
                    },
                    {
                        "batchID": "c" * 64,
                        "usable": True,
                        "accessMode": "shared",
                    },
                ],
                "total_count": 2,
            }
            result = await call_tool_directly(server, "list_stamps", {})

        text = result.content[0].text
        assert "All stamps are public (shared)" in text
        assert "dedicated stamps" in text

    async def test_list_stamps_owned_no_public_hint(self, server):
        """When at least one stamp is owned, do NOT show the public-only hint."""
        with patch('swarm_provenance_mcp.server.gateway_client') as mock_client:
            mock_client.list_stamps.return_value = {
                "stamps": [
                    {
                        "batchID": TEST_STAMP_ID,
                        "usable": True,
                        "accessMode": "owned",
                    },
                    {
                        "batchID": "c" * 64,
                        "usable": True,
                        "accessMode": "shared",
                    },
                ],
                "total_count": 2,
            }
            result = await call_tool_directly(server, "list_stamps", {})

        text = result.content[0].text
        assert "All stamps are public" not in text


class TestStructuredErrors:
    """Test that error responses include retryable flag and recovery hints."""

    @pytest.fixture
    def server(self):
        return create_server()

    async def test_validation_error_not_retryable(self, server):
        """Validation errors should be marked retryable: false."""
        with patch('swarm_provenance_mcp.server.gateway_client'):
            result = await call_tool_directly(
                server, "get_stamp_status", {"stamp_id": "invalid!"}
            )
            assert result.isError
            text = result.content[0].text
            assert "retryable: false" in text

    async def test_network_error_retryable(self, server):
        """Network/connection errors should be marked retryable: true."""
        from requests.exceptions import ConnectionError
        with patch('swarm_provenance_mcp.server.gateway_client') as mock_client:
            mock_client.list_stamps.side_effect = ConnectionError("Connection refused")
            result = await call_tool_directly(server, "list_stamps", {})
            assert result.isError
            text = result.content[0].text
            assert "retryable: true" in text

    async def test_error_includes_next_hint(self, server):
        """Error responses should include _next recovery hint."""
        from requests.exceptions import ConnectionError
        with patch('swarm_provenance_mcp.server.gateway_client') as mock_client:
            mock_client.purchase_stamp.side_effect = ConnectionError("timeout")
            result = await call_tool_directly(server, "purchase_stamp", {})
            assert result.isError
            text = result.content[0].text
            assert "_next:" in text

    async def test_validation_error_suggests_correct_tool(self, server):
        """Validation errors should suggest the right recovery tool."""
        with patch('swarm_provenance_mcp.server.gateway_client'):
            # Bad stamp ID → should suggest list_stamps to find valid IDs
            result = await call_tool_directly(
                server, "get_stamp_status", {"stamp_id": "bad"}
            )
            assert result.isError
            text = result.content[0].text
            assert "_next: list_stamps" in text


class TestTypoCorrection:
    """Test unknown tool name typo correction with fuzzy matching."""

    @pytest.fixture
    def server(self):
        return create_server()

    @pytest.fixture
    def mock_gateway_client(self):
        with patch('swarm_provenance_mcp.server.gateway_client'):
            yield

    async def test_close_typo_suggests_correction(self, server, mock_gateway_client):
        """Close typos should get 'Did you mean' suggestions."""
        from swarm_provenance_mcp.server import _suggest_tool_name
        msg = _suggest_tool_name("purchse_stamp")
        assert "Did you mean" in msg
        assert "purchase_stamp" in msg

    async def test_multiple_close_matches(self, server, mock_gateway_client):
        """When multiple tools are close, suggest all of them."""
        from swarm_provenance_mcp.server import _suggest_tool_name
        msg = _suggest_tool_name("list_stamp")
        assert "Did you mean" in msg
        assert "list_stamps" in msg

    async def test_no_close_match_lists_all(self, server, mock_gateway_client):
        """Completely wrong name should list all available tools."""
        from swarm_provenance_mcp.server import _suggest_tool_name
        msg = _suggest_tool_name("xyzzy_foobar_baz")
        assert "Available tools:" in msg

    async def test_chain_tool_typo_suggests_correction(self, server, mock_gateway_client):
        """Chain tool typos should also get 'Did you mean' suggestions."""
        from swarm_provenance_mcp.server import _suggest_tool_name
        msg = _suggest_tool_name("anchor_has")
        assert "Did you mean" in msg
        assert "anchor_hash" in msg

        msg = _suggest_tool_name("get_provnance")
        assert "Did you mean" in msg
        assert "get_provenance" in msg

    async def test_unknown_tool_error_includes_suggestion(self, server, mock_gateway_client):
        """Unknown tool via call_tool should include suggestion and retryable: false."""
        # call_tool_directly doesn't go through create_server's call_tool wrapper,
        # so test _suggest_tool_name and _format_error directly
        from swarm_provenance_mcp.server import _suggest_tool_name, _format_error
        msg = _format_error(_suggest_tool_name("helth_check"), retryable=False)
        assert "health_check" in msg
        assert "retryable: false" in msg


class TestAdaptiveHealthCheck:
    """Test the adaptive health_check response with ready flag and recommendations."""

    @pytest.fixture
    def server(self):
        return create_server()

    async def test_healthy_with_usable_stamps(self, server):
        """Healthy gateway + usable stamps → ready: true, _next: upload_data."""
        with patch('swarm_provenance_mcp.server.gateway_client') as mock_client:
            mock_client.health_check.return_value = {
                "status": "healthy",
                "gateway_url": "http://localhost:8000",
                "response_time_ms": 10
            }
            mock_client.list_stamps.return_value = {
                "stamps": [{"batchID": TEST_STAMP_ID, "usable": True}],
                "total_count": 1
            }
            result = await call_tool_directly(server, "health_check", {})
            text = result.content[0].text
            assert "ready: true" in text
            assert "_next: upload_data" in text

    async def test_healthy_no_stamps(self, server):
        """Healthy gateway + no stamps → ready: false, _next: purchase_stamp."""
        with patch('swarm_provenance_mcp.server.gateway_client') as mock_client:
            mock_client.health_check.return_value = {
                "status": "healthy",
                "gateway_url": "http://localhost:8000",
                "response_time_ms": 10
            }
            mock_client.list_stamps.return_value = {"stamps": [], "total_count": 0}
            result = await call_tool_directly(server, "health_check", {})
            text = result.content[0].text
            assert "ready: false" in text
            assert "_next: purchase_stamp" in text
            assert "_recommendations:" in text

    async def test_healthy_no_usable_stamps(self, server):
        """Healthy gateway + stamps but none usable → ready: false, recommends purchase."""
        with patch('swarm_provenance_mcp.server.gateway_client') as mock_client:
            mock_client.health_check.return_value = {
                "status": "healthy",
                "gateway_url": "http://localhost:8000",
                "response_time_ms": 10
            }
            mock_client.list_stamps.return_value = {
                "stamps": [{"batchID": TEST_STAMP_ID, "usable": False}],
                "total_count": 1
            }
            result = await call_tool_directly(server, "health_check", {})
            text = result.content[0].text
            assert "ready: false" in text
            assert "_next: purchase_stamp" in text

    async def test_unhealthy_gateway(self, server):
        """Unhealthy gateway → ready: false with gateway recommendation."""
        with patch('swarm_provenance_mcp.server.gateway_client') as mock_client:
            mock_client.health_check.return_value = {
                "status": "unhealthy",
                "gateway_url": "http://localhost:8000",
                "response_time_ms": 5000
            }
            result = await call_tool_directly(server, "health_check", {})
            text = result.content[0].text
            assert "ready: false" in text
            assert "_recommendations:" in text

    async def test_gateway_connection_failure(self, server):
        """Connection failure → isError with retryable flag."""
        from requests.exceptions import ConnectionError
        with patch('swarm_provenance_mcp.server.gateway_client') as mock_client:
            mock_client.health_check.side_effect = ConnectionError("refused")
            result = await call_tool_directly(server, "health_check", {})
            assert result.isError
            text = result.content[0].text
            assert "retryable: true" in text

    async def test_response_has_related_tools(self, server):
        """health_check response should list related tools."""
        with patch('swarm_provenance_mcp.server.gateway_client') as mock_client:
            mock_client.health_check.return_value = {
                "status": "healthy",
                "gateway_url": "http://localhost:8000",
                "response_time_ms": 10
            }
            mock_client.list_stamps.return_value = {
                "stamps": [{"batchID": TEST_STAMP_ID, "usable": True}],
                "total_count": 1
            }
            result = await call_tool_directly(server, "health_check", {})
            text = result.content[0].text
            assert "_related:" in text


class TestMCPPrompts:
    """Test MCP prompt definitions and workflow templates."""

    @pytest.fixture
    def server(self):
        return create_server()

    async def test_list_prompts_returns_all(self, server):
        """list_prompts should return all 4 workflow prompts."""
        handler = None
        for h in server.request_handlers.values():
            if hasattr(h, '__name__') and 'list_prompts' in str(h):
                handler = h
                break

        assert handler is not None, "list_prompts handler not registered"
        from mcp.types import ListPromptsRequest
        result = await handler(ListPromptsRequest(method="prompts/list"))
        inner = result.root if hasattr(result, 'root') else result
        prompts = inner.prompts if hasattr(inner, 'prompts') else inner

        prompt_names = {p.name for p in prompts}
        assert prompt_names == {
            "provenance-upload", "provenance-verify",
            "stamp-management", "provenance-chain-workflow",
        }

    async def test_provenance_upload_prompt_has_arguments(self, server):
        """provenance-upload prompt should require data argument."""
        handler = None
        for h in server.request_handlers.values():
            if hasattr(h, '__name__') and 'list_prompts' in str(h):
                handler = h
                break

        from mcp.types import ListPromptsRequest
        result = await handler(ListPromptsRequest(method="prompts/list"))
        inner = result.root if hasattr(result, 'root') else result
        prompts = inner.prompts if hasattr(inner, 'prompts') else inner

        upload_prompt = next(p for p in prompts if p.name == "provenance-upload")
        arg_names = [a.name for a in upload_prompt.arguments]
        assert "data" in arg_names
        data_arg = next(a for a in upload_prompt.arguments if a.name == "data")
        assert data_arg.required is True

    async def test_get_prompt_provenance_upload(self, server):
        """get_prompt for provenance-upload should return step-by-step instructions."""
        handler = None
        for h in server.request_handlers.values():
            if hasattr(h, '__name__') and 'get_prompt' in str(h):
                handler = h
                break

        assert handler is not None, "get_prompt handler not registered"
        from mcp.types import GetPromptRequest
        result = await handler(GetPromptRequest(
            method="prompts/get",
            params={"name": "provenance-upload", "arguments": {"data": "test data"}}
        ))
        inner = result.root if hasattr(result, 'root') else result
        messages = inner.messages

        assert len(messages) >= 1
        text = messages[0].content.text
        assert "health_check" in text
        assert "upload_data" in text
        assert "download_data" in text

    async def test_get_prompt_stamp_management(self, server):
        """get_prompt for stamp-management should include stamp review steps."""
        handler = None
        for h in server.request_handlers.values():
            if hasattr(h, '__name__') and 'get_prompt' in str(h):
                handler = h
                break

        from mcp.types import GetPromptRequest
        result = await handler(GetPromptRequest(
            method="prompts/get",
            params={"name": "stamp-management"}
        ))
        inner = result.root if hasattr(result, 'root') else result
        text = inner.messages[0].content.text
        assert "list_stamps" in text
        assert "check_stamp_health" in text


class TestCompanionServers:
    """Test cross-server coordination info in health_check."""

    @pytest.fixture
    def server(self):
        return create_server()

    async def test_health_check_includes_companion_servers(self, server):
        """health_check response should list companion servers."""
        with patch('swarm_provenance_mcp.server.gateway_client') as mock_client:
            mock_client.health_check.return_value = {
                "status": "healthy",
                "gateway_url": "http://localhost:8000",
                "response_time_ms": 10
            }
            mock_client.list_stamps.return_value = {
                "stamps": [{"batchID": TEST_STAMP_ID, "usable": True}],
                "total_count": 1
            }
            result = await call_tool_directly(server, "health_check", {})
            text = result.content[0].text
            assert "_companion_servers:" in text
            assert "swarm_connect" in text
            assert "fds-id" in text

    async def test_companion_server_status_reflects_gateway(self, server):
        """Companion server status should reflect actual gateway connectivity."""
        with patch('swarm_provenance_mcp.server.gateway_client') as mock_client:
            mock_client.health_check.return_value = {
                "status": "healthy",
                "gateway_url": "http://localhost:8000",
                "response_time_ms": 10
            }
            mock_client.list_stamps.return_value = {"stamps": [], "total_count": 0}
            result = await call_tool_directly(server, "health_check", {})
            text = result.content[0].text
            assert "connected" in text


class TestToolParameterValidation:
    """Test parameter validation for all tools."""

    @pytest.fixture
    def server(self):
        """Create MCP server instance for testing."""
        return create_server()

    async def test_parameter_type_validation(self, server):
        """Test that tools validate parameter types correctly."""
        with patch('swarm_provenance_mcp.server.gateway_client'):
            # Test invalid amount type for purchase_stamp
            result = await call_tool_directly(
                server, "purchase_stamp", {"amount": "not_a_number", "depth": 17}
            )

            # Should handle type conversion or return error
            assert result.isError, "Invalid type should produce an error result"

    async def test_parameter_range_validation(self, server):
        """Test parameter range validation where applicable."""
        with patch('swarm_provenance_mcp.server.gateway_client'):
            # Test negative amount (should be handled gracefully)
            result = await call_tool_directly(
                server, "purchase_stamp", {"amount": -1000, "depth": 17}
            )

            # The tool should either handle this gracefully or return a meaningful error
            assert isinstance(result, CallToolResult)
            assert len(result.content) > 0

    async def test_empty_string_parameters(self, server):
        """Test handling of empty string parameters."""
        with patch('swarm_provenance_mcp.server.gateway_client'):
            # Test empty stamp_id
            result = await call_tool_directly(
                server, "get_stamp_status", {"stamp_id": ""}
            )

            # Should return an error for empty required parameters
            assert isinstance(result, CallToolResult)
            if result.isError:
                error_text = result.content[0].text.lower()
                assert "stamp_id" in error_text or "empty" in error_text or "required" in error_text


class TestChainBalance:
    """Test chain_balance tool execution."""

    @pytest.fixture
    def server(self):
        return create_server()

    def _mock_wallet_info(self, balance_wei=10**17, chain="base-sepolia"):
        """Create a mock ChainWalletInfo."""
        mock_info = MagicMock()
        mock_info.address = "0x1234567890abcdef1234567890abcdef12345678"
        mock_info.balance_wei = balance_wei
        mock_info.balance_eth = f"{balance_wei / 10**18:.6f}"
        mock_info.chain = chain
        mock_info.contract_address = "0x9a3c6F47B69211F05891CCb7aD33596290b9fE64"
        return mock_info

    async def test_chain_balance_funded(self, server):
        """Healthy balance should show wallet info without warnings."""
        mock_client = MagicMock()
        mock_client.balance.return_value = self._mock_wallet_info(balance_wei=10**17)
        mock_client._provider = MagicMock()
        mock_client._provider.rpc_url = "https://sepolia.base.org"

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "chain_balance", {})

        assert not result.isError
        text = result.content[0].text
        assert "0x1234" in text
        assert "ETH" in text
        assert "base-sepolia" in text
        assert "CRITICAL" not in text
        assert "WARNING" not in text

    async def test_chain_balance_low(self, server):
        """Balance below LOW threshold should show warning."""
        mock_client = MagicMock()
        mock_client.balance.return_value = self._mock_wallet_info(balance_wei=5 * 10**14)
        mock_client._provider = MagicMock()
        mock_client._provider.rpc_url = "https://sepolia.base.org"

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "chain_balance", {})

        assert not result.isError
        text = result.content[0].text
        assert "WARNING" in text
        assert "low" in text.lower()

    async def test_chain_balance_insufficient(self, server):
        """Balance below MIN threshold should show critical message with faucet."""
        mock_client = MagicMock()
        mock_client.balance.return_value = self._mock_wallet_info(balance_wei=10**12)
        mock_client._provider = MagicMock()
        mock_client._provider.rpc_url = "https://sepolia.base.org"

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "chain_balance", {})

        assert not result.isError
        text = result.content[0].text
        assert "CRITICAL" in text
        assert "faucet" in text.lower() or "alchemy" in text.lower()

    async def test_chain_balance_mainnet(self, server):
        """Mainnet chain should show bridge URL instead of faucet."""
        mock_client = MagicMock()
        mock_client.balance.return_value = self._mock_wallet_info(
            balance_wei=10**12, chain="base"
        )
        mock_client._provider = MagicMock()
        mock_client._provider.rpc_url = "https://mainnet.base.org"

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "chain_balance", {})

        assert not result.isError
        text = result.content[0].text
        assert "bridge.base.org" in text

    async def test_chain_balance_not_available(self, server):
        """CHAIN_AVAILABLE=False should return error."""
        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', False):
            result = await call_tool_directly(server, "chain_balance", {})

        assert result.isError
        text = result.content[0].text
        assert "not available" in text.lower() or "not installed" in text.lower()

    async def test_chain_balance_no_client(self, server):
        """chain_client=None should return error suggesting chain_health."""
        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', None):
            result = await call_tool_directly(server, "chain_balance", {})

        assert result.isError
        text = result.content[0].text
        assert "wallet" in text.lower() or "PROVENANCE_WALLET_KEY" in text

    async def test_chain_balance_hints(self, server):
        """Response should include _next and _related hints."""
        mock_client = MagicMock()
        mock_client.balance.return_value = self._mock_wallet_info()
        mock_client._provider = MagicMock()
        mock_client._provider.rpc_url = "https://sepolia.base.org"

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "chain_balance", {})

        text = result.content[0].text
        assert "_next:" in text
        assert "_related:" in text

    async def test_chain_balance_connection_error(self, server):
        """balance() raising an exception should be retryable with chain_health hint."""
        mock_client = MagicMock()
        mock_client.balance.side_effect = Exception("RPC connection refused")

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "chain_balance", {})

        assert result.isError
        text = result.content[0].text
        assert "retryable: true" in text
        assert "_next: chain_health" in text

    async def test_chain_tools_registered_when_enabled(self, server):
        """Chain tools should appear in list_tools when chain is enabled."""
        from mcp.types import ListToolsRequest

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.settings') as mock_settings:
            mock_settings.chain_enabled = True
            mock_settings.mcp_server_name = "test"
            mock_settings.mcp_server_version = "0.1.0"
            mock_settings.default_stamp_amount = 2000000000
            mock_settings.default_stamp_depth = 17

            test_server = create_server()
            # Find the list_tools handler
            handler = None
            for h in test_server.request_handlers.values():
                if hasattr(h, '__name__') and 'list_tools' in str(h):
                    handler = h
                    break
            assert handler is not None
            result = await handler(ListToolsRequest(method="tools/list"))
            inner = result.root if hasattr(result, 'root') else result
            tools = inner.tools if hasattr(inner, 'tools') else inner
            tool_names = {t.name for t in tools}
            assert "chain_balance" in tool_names
            assert "chain_health" in tool_names


class TestChainHealth:
    """Test chain_health tool execution."""

    @pytest.fixture
    def server(self):
        return create_server()

    async def test_chain_health_connected(self, server):
        """Successful health check should show connection info."""
        mock_client = MagicMock()
        mock_client._provider = MagicMock()
        mock_client._provider.chain = "base-sepolia"
        mock_client._provider.chain_id = 84532
        mock_client._provider.rpc_url = "https://sepolia.base.org"
        mock_client._provider.contract_address = "0x9a3c6F47B69211F05891CCb7aD33596290b9fE64"
        mock_client._provider.health_check.return_value = True
        mock_client._provider.get_block_number.return_value = 12345678

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "chain_health", {})

        assert not result.isError
        text = result.content[0].text
        assert "Connected: true" in text
        assert "base-sepolia" in text
        assert "84532" in text
        assert "12,345,678" in text

    async def test_chain_health_disconnected(self, server):
        """Connection error should show disconnected status."""
        mock_client = MagicMock()
        mock_client._provider = MagicMock()
        mock_client._provider.health_check.side_effect = Exception("Connection refused")

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "chain_health", {})

        assert result.isError
        text = result.content[0].text
        assert "Connected: false" in text

    async def test_chain_health_not_available(self, server):
        """CHAIN_AVAILABLE=False should return error."""
        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', False):
            result = await call_tool_directly(server, "chain_health", {})

        assert result.isError
        text = result.content[0].text
        assert "not available" in text.lower() or "not installed" in text.lower()

    async def test_chain_health_no_wallet(self, server):
        """chain_client=None should still work by creating a temporary provider."""
        mock_provider = MagicMock()
        mock_provider.chain = "base-sepolia"
        mock_provider.chain_id = 84532
        mock_provider.rpc_url = "https://sepolia.base.org"
        mock_provider.contract_address = "0x9a3c6F47B69211F05891CCb7aD33596290b9fE64"
        mock_provider.health_check.return_value = True
        mock_provider.get_block_number.return_value = 99999

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', None), \
             patch('swarm_provenance_mcp.chain.provider.ChainProvider', return_value=mock_provider):
            result = await call_tool_directly(server, "chain_health", {})

        assert not result.isError
        text = result.content[0].text
        assert "Connected: true" in text

    async def test_chain_health_hints(self, server):
        """Response should include _next and _related hints."""
        mock_client = MagicMock()
        mock_client._provider = MagicMock()
        mock_client._provider.chain = "base-sepolia"
        mock_client._provider.chain_id = 84532
        mock_client._provider.rpc_url = "https://sepolia.base.org"
        mock_client._provider.contract_address = "0x9a3c"
        mock_client._provider.health_check.return_value = True
        mock_client._provider.get_block_number.return_value = 100

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "chain_health", {})

        text = result.content[0].text
        assert "_next: chain_balance" in text
        assert "_related:" in text

    async def test_chain_health_rpc_masked(self, server):
        """RPC URL should show only hostname, not full URL."""
        mock_client = MagicMock()
        mock_client._provider = MagicMock()
        mock_client._provider.chain = "base-sepolia"
        mock_client._provider.chain_id = 84532
        mock_client._provider.rpc_url = "https://my-secret-rpc.example.com/v1/key123"
        mock_client._provider.contract_address = "0x9a3c"
        mock_client._provider.health_check.return_value = True
        mock_client._provider.get_block_number.return_value = 100

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "chain_health", {})

        text = result.content[0].text
        assert "my-secret-rpc.example.com" in text
        assert "key123" not in text

    async def test_chain_health_provider_creation_fails(self, server):
        """Failing to create temp provider (e.g. missing contract) should return error."""
        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', None), \
             patch('swarm_provenance_mcp.server.settings') as mock_settings:
            mock_settings.chain_name = "base"
            mock_settings.chain_rpc_url = None
            mock_settings.chain_contract_address = None
            mock_settings.chain_explorer_url = None
            # ChainProvider will raise because base has no contract preset
            result = await call_tool_directly(server, "chain_health", {})

        assert result.isError
        text = result.content[0].text
        assert "Connected: false" in text

    async def test_chain_health_with_fallback_urls(self, server):
        """CHAIN_RPC_URLS config should be parsed and passed to ChainProvider."""
        mock_provider_cls = MagicMock()
        mock_provider_instance = MagicMock()
        mock_provider_instance.chain = "base-sepolia"
        mock_provider_instance.chain_id = 84532
        mock_provider_instance.rpc_url = "https://sepolia.base.org"
        mock_provider_instance.contract_address = "0x9a3c6F47B69211F05891CCb7aD33596290b9fE64"
        mock_provider_instance.health_check.return_value = True
        mock_provider_instance.get_block_number.return_value = 12345678
        mock_provider_cls.return_value = mock_provider_instance

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', None), \
             patch('swarm_provenance_mcp.server.settings') as mock_settings, \
             patch('swarm_provenance_mcp.chain.provider.ChainProvider', mock_provider_cls):
            mock_settings.chain_name = "base-sepolia"
            mock_settings.chain_rpc_url = None
            mock_settings.chain_contract_address = None
            mock_settings.chain_explorer_url = None
            mock_settings.chain_rpc_urls = "https://fallback1.com,https://fallback2.com"
            result = await call_tool_directly(server, "chain_health", {})

        assert not result.isError
        # Verify ChainProvider was called with rpc_fallbacks
        call_kwargs = mock_provider_cls.call_args
        assert call_kwargs.kwargs["rpc_fallbacks"] == [
            "https://fallback1.com",
            "https://fallback2.com",
        ]


class TestFundingGuidance:
    """Direct unit tests for _format_funding_guidance helper."""

    def test_healthy_balance_returns_empty(self):
        """Balance above LOW threshold should return empty string."""
        from swarm_provenance_mcp.server import _format_funding_guidance, _LOW_BALANCE_WEI
        result = _format_funding_guidance("0xabc", _LOW_BALANCE_WEI, "base-sepolia")
        assert result == ""

    def test_well_above_threshold_returns_empty(self):
        """Large balance should return empty string."""
        from swarm_provenance_mcp.server import _format_funding_guidance
        result = _format_funding_guidance("0xabc", 10**18, "base-sepolia")
        assert result == ""

    def test_low_balance_returns_warning(self):
        """Balance between MIN and LOW should return WARNING."""
        from swarm_provenance_mcp.server import (
            _format_funding_guidance, _MIN_BALANCE_WEI, _LOW_BALANCE_WEI,
        )
        balance = (_MIN_BALANCE_WEI + _LOW_BALANCE_WEI) // 2
        result = _format_funding_guidance("0xabc", balance, "base-sepolia")
        assert "WARNING" in result
        assert "CRITICAL" not in result
        assert "0xabc" in result

    def test_insufficient_balance_returns_critical(self):
        """Balance below MIN should return CRITICAL."""
        from swarm_provenance_mcp.server import _format_funding_guidance
        result = _format_funding_guidance("0xabc", 100, "base-sepolia")
        assert "CRITICAL" in result
        assert "0xabc" in result

    def test_zero_balance_returns_critical(self):
        """Zero balance should return CRITICAL."""
        from swarm_provenance_mcp.server import _format_funding_guidance
        result = _format_funding_guidance("0xabc", 0, "base-sepolia")
        assert "CRITICAL" in result

    def test_testnet_shows_faucet(self):
        """Testnet chain should include faucet URL."""
        from swarm_provenance_mcp.server import _format_funding_guidance
        result = _format_funding_guidance("0xabc", 100, "base-sepolia")
        assert "faucet" in result.lower() or "alchemy" in result.lower()

    def test_mainnet_shows_bridge(self):
        """Mainnet chain should include bridge URL."""
        from swarm_provenance_mcp.server import _format_funding_guidance
        result = _format_funding_guidance("0xabc", 100, "base")
        assert "bridge.base.org" in result

    def test_unknown_chain_shows_bridge(self):
        """Unknown chain (not in faucet list) should show bridge URL."""
        from swarm_provenance_mcp.server import _format_funding_guidance
        result = _format_funding_guidance("0xabc", 100, "base-mainnet-custom")
        assert "bridge.base.org" in result


class TestParseRpcFallbacks:
    """Unit tests for _parse_rpc_fallbacks helper."""

    def test_none_returns_none(self):
        """No CHAIN_RPC_URLS setting should return None."""
        from swarm_provenance_mcp.server import _parse_rpc_fallbacks

        with patch("swarm_provenance_mcp.server.settings") as mock_settings:
            mock_settings.chain_rpc_urls = None
            assert _parse_rpc_fallbacks() is None

    def test_empty_string_returns_none(self):
        """Empty string should return None."""
        from swarm_provenance_mcp.server import _parse_rpc_fallbacks

        with patch("swarm_provenance_mcp.server.settings") as mock_settings:
            mock_settings.chain_rpc_urls = ""
            assert _parse_rpc_fallbacks() is None

    def test_single_url(self):
        """Single URL without commas."""
        from swarm_provenance_mcp.server import _parse_rpc_fallbacks

        with patch("swarm_provenance_mcp.server.settings") as mock_settings:
            mock_settings.chain_rpc_urls = "https://fb1.io"
            assert _parse_rpc_fallbacks() == ["https://fb1.io"]

    def test_comma_separated(self):
        """Multiple comma-separated URLs."""
        from swarm_provenance_mcp.server import _parse_rpc_fallbacks

        with patch("swarm_provenance_mcp.server.settings") as mock_settings:
            mock_settings.chain_rpc_urls = "https://fb1.io,https://fb2.io,https://fb3.io"
            assert _parse_rpc_fallbacks() == [
                "https://fb1.io",
                "https://fb2.io",
                "https://fb3.io",
            ]

    def test_whitespace_trimmed(self):
        """Whitespace around URLs should be stripped."""
        from swarm_provenance_mcp.server import _parse_rpc_fallbacks

        with patch("swarm_provenance_mcp.server.settings") as mock_settings:
            mock_settings.chain_rpc_urls = " https://fb1.io , https://fb2.io "
            assert _parse_rpc_fallbacks() == ["https://fb1.io", "https://fb2.io"]

    def test_trailing_comma_ignored(self):
        """Trailing comma should not produce an empty entry."""
        from swarm_provenance_mcp.server import _parse_rpc_fallbacks

        with patch("swarm_provenance_mcp.server.settings") as mock_settings:
            mock_settings.chain_rpc_urls = "https://fb1.io,"
            assert _parse_rpc_fallbacks() == ["https://fb1.io"]


class TestMaskRpcUrl:
    """Direct unit tests for _mask_rpc_url helper."""

    def test_standard_url(self):
        from swarm_provenance_mcp.server import _mask_rpc_url
        assert _mask_rpc_url("https://sepolia.base.org") == "sepolia.base.org"

    def test_url_with_path_and_key(self):
        from swarm_provenance_mcp.server import _mask_rpc_url
        result = _mask_rpc_url("https://rpc.example.com/v1/secret-key-123")
        assert result == "rpc.example.com"

    def test_url_with_port(self):
        from swarm_provenance_mcp.server import _mask_rpc_url
        result = _mask_rpc_url("http://localhost:8545")
        assert result == "localhost"

    def test_empty_string(self):
        from swarm_provenance_mcp.server import _mask_rpc_url
        result = _mask_rpc_url("")
        assert isinstance(result, str)

    def test_garbage_input(self):
        from swarm_provenance_mcp.server import _mask_rpc_url
        result = _mask_rpc_url("not-a-url")
        assert isinstance(result, str)


class TestAnchorHash:
    """Test anchor_hash tool execution."""

    @pytest.fixture
    def server(self):
        return create_server()

    def _mock_anchor_result(self, swarm_hash=TEST_REFERENCE, data_type="swarm-provenance", owner="0x1234567890abcdef1234567890abcdef12345678"):
        """Create a mock AnchorResult."""
        mock_result = MagicMock()
        mock_result.swarm_hash = swarm_hash
        mock_result.data_type = data_type
        mock_result.owner = owner
        mock_result.tx_hash = "ab" * 32
        mock_result.block_number = 12345678
        mock_result.gas_used = 65000
        mock_result.explorer_url = "https://sepolia.basescan.org/tx/0x" + "ab" * 32
        return mock_result

    def _mock_wallet_info(self, balance_wei=10**17):
        """Create a mock ChainWalletInfo."""
        mock_info = MagicMock()
        mock_info.address = "0x1234567890abcdef1234567890abcdef12345678"
        mock_info.balance_wei = balance_wei
        mock_info.balance_eth = f"{balance_wei / 10**18:.6f}"
        mock_info.chain = "base-sepolia"
        mock_info.contract_address = "0x9a3c6F47B69211F05891CCb7aD33596290b9fE64"
        return mock_info

    async def test_anchor_success(self, server):
        """Successful anchor should show tx details."""
        mock_client = MagicMock()
        mock_client.anchor.return_value = self._mock_anchor_result()
        mock_client.balance.return_value = self._mock_wallet_info()

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "anchor_hash", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert not result.isError
        text = result.content[0].text
        assert TEST_REFERENCE in text
        assert "swarm-provenance" in text
        assert "12,345,678" in text
        assert "65,000" in text
        assert "basescan" in text
        mock_client.anchor.assert_called_once_with(TEST_REFERENCE, "swarm-provenance")

    async def test_anchor_with_data_type(self, server):
        """Custom data_type should be passed through."""
        mock_client = MagicMock()
        mock_client.anchor.return_value = self._mock_anchor_result(data_type="document")
        mock_client.balance.return_value = self._mock_wallet_info()

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "anchor_hash", {
                "swarm_hash": TEST_REFERENCE,
                "data_type": "document",
            })

        assert not result.isError
        text = result.content[0].text
        assert "document" in text
        mock_client.anchor.assert_called_once_with(TEST_REFERENCE, "document")

    async def test_anchor_for_owner(self, server):
        """Providing owner should trigger anchor_for."""
        delegate_owner = "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd"
        mock_client = MagicMock()
        mock_client.anchor_for.return_value = self._mock_anchor_result(owner=delegate_owner)
        mock_client.balance.return_value = self._mock_wallet_info()

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "anchor_hash", {
                "swarm_hash": TEST_REFERENCE,
                "owner": delegate_owner,
            })

        assert not result.isError
        text = result.content[0].text
        assert delegate_owner in text
        mock_client.anchor_for.assert_called_once_with(
            TEST_REFERENCE, delegate_owner, "swarm-provenance"
        )
        mock_client.anchor.assert_not_called()

    async def test_anchor_already_registered(self, server):
        """DataAlreadyRegisteredError should NOT be isError."""
        from swarm_provenance_mcp.chain.exceptions import DataAlreadyRegisteredError

        mock_client = MagicMock()
        mock_client.anchor.side_effect = DataAlreadyRegisteredError(
            "Already registered",
            data_hash=TEST_REFERENCE,
            owner="0xabcdefabcdefabcdefabcdefabcdefabcdefabcd",
            timestamp=1700000000,
            data_type="swarm-provenance",
        )

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "anchor_hash", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert not result.isError
        text = result.content[0].text
        assert "already registered" in text.lower()
        assert TEST_REFERENCE in text
        assert "0xabcdef" in text
        assert "2023" in text  # timestamp 1700000000 is in 2023

    async def test_anchor_transaction_error(self, server):
        """ChainTransactionError should be isError."""
        from swarm_provenance_mcp.chain.exceptions import ChainTransactionError

        mock_client = MagicMock()
        mock_client.anchor.side_effect = ChainTransactionError(
            "Transaction reverted", tx_hash="cd" * 32
        )

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "anchor_hash", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert result.isError
        text = result.content[0].text
        assert "retryable: false" in text
        assert "_next: chain_balance" in text
        assert "cd" * 32 in text

    async def test_anchor_connection_error(self, server):
        """ChainConnectionError should be retryable."""
        from swarm_provenance_mcp.chain.exceptions import ChainConnectionError

        mock_client = MagicMock()
        mock_client.anchor.side_effect = ChainConnectionError(
            "RPC unreachable", rpc_url="https://sepolia.base.org"
        )

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "anchor_hash", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert result.isError
        text = result.content[0].text
        assert "retryable: true" in text
        assert "_next: chain_health" in text

    async def test_anchor_not_available(self, server):
        """CHAIN_AVAILABLE=False should return error."""
        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', False):
            result = await call_tool_directly(server, "anchor_hash", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert result.isError
        text = result.content[0].text
        assert "not available" in text.lower() or "not installed" in text.lower()

    async def test_anchor_no_client(self, server):
        """chain_client=None should return error."""
        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', None):
            result = await call_tool_directly(server, "anchor_hash", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert result.isError
        text = result.content[0].text
        assert "wallet" in text.lower() or "PROVENANCE_WALLET_KEY" in text

    async def test_anchor_invalid_hash(self, server):
        """Invalid hash format should return validation error."""
        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', MagicMock()):
            result = await call_tool_directly(server, "anchor_hash", {
                "swarm_hash": "not-a-valid-hash",
            })

        assert result.isError
        text = result.content[0].text
        assert "retryable: false" in text

    async def test_anchor_missing_hash(self, server):
        """Missing swarm_hash should return validation error."""
        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', MagicMock()):
            result = await call_tool_directly(server, "anchor_hash", {})

        assert result.isError
        text = result.content[0].text
        assert "required" in text.lower() or "swarm_hash" in text.lower()

    async def test_anchor_data_type_too_long(self, server):
        """data_type > 64 chars should return validation error."""
        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', MagicMock()):
            result = await call_tool_directly(server, "anchor_hash", {
                "swarm_hash": TEST_REFERENCE,
                "data_type": "x" * 65,
            })

        assert result.isError
        text = result.content[0].text
        assert "64" in text

    async def test_anchor_validation_error(self, server):
        """ChainValidationError should be non-retryable."""
        from swarm_provenance_mcp.chain.exceptions import ChainValidationError

        mock_client = MagicMock()
        mock_client.anchor.side_effect = ChainValidationError("Invalid hash length")

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "anchor_hash", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert result.isError
        text = result.content[0].text
        assert "retryable: false" in text
        assert "validation" in text.lower()

    async def test_anchor_balance_check_failure_silent(self, server):
        """Post-tx balance check failure should not break the success response."""
        mock_client = MagicMock()
        mock_client.anchor.return_value = self._mock_anchor_result()
        mock_client.balance.side_effect = Exception("RPC down")

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "anchor_hash", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert not result.isError
        text = result.content[0].text
        assert "anchored" in text.lower()
        assert "_next: download_data" in text

    async def test_anchor_for_owner_with_data_type(self, server):
        """Owner + custom data_type should both be passed through."""
        delegate_owner = "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd"
        mock_client = MagicMock()
        mock_client.anchor_for.return_value = self._mock_anchor_result(
            owner=delegate_owner, data_type="document"
        )
        mock_client.balance.return_value = self._mock_wallet_info()

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "anchor_hash", {
                "swarm_hash": TEST_REFERENCE,
                "owner": delegate_owner,
                "data_type": "document",
            })

        assert not result.isError
        mock_client.anchor_for.assert_called_once_with(
            TEST_REFERENCE, delegate_owner, "document"
        )

    async def test_anchor_generic_chain_error(self, server):
        """Generic ChainError (not a specific subtype) should be caught."""
        from swarm_provenance_mcp.chain.exceptions import ChainError

        mock_client = MagicMock()
        mock_client.anchor.side_effect = ChainError("Something unexpected")

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "anchor_hash", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert result.isError
        text = result.content[0].text
        assert "retryable: false" in text

    async def test_anchor_low_balance_after_tx(self, server):
        """Post-tx balance warning should appear when balance is low."""
        mock_client = MagicMock()
        mock_client.anchor.return_value = self._mock_anchor_result()
        mock_client.balance.return_value = self._mock_wallet_info(balance_wei=5 * 10**14)

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "anchor_hash", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert not result.isError
        text = result.content[0].text
        assert "anchored" in text.lower()
        assert "WARNING" in text

    async def test_anchor_hints(self, server):
        """Response should include _next and _related hints."""
        mock_client = MagicMock()
        mock_client.anchor.return_value = self._mock_anchor_result()
        mock_client.balance.return_value = self._mock_wallet_info()

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "anchor_hash", {
                "swarm_hash": TEST_REFERENCE,
            })

        text = result.content[0].text
        assert "_next: download_data" in text
        assert "_related:" in text
        assert "chain_balance" in text

    async def test_anchor_tool_registered(self, server):
        """anchor_hash should appear in list_tools when chain is enabled."""
        from mcp.types import ListToolsRequest

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.settings') as mock_settings:
            mock_settings.chain_enabled = True
            mock_settings.mcp_server_name = "test"
            mock_settings.mcp_server_version = "0.1.0"
            mock_settings.default_stamp_amount = 2000000000
            mock_settings.default_stamp_depth = 17

            test_server = create_server()
            handler = None
            for h in test_server.request_handlers.values():
                if hasattr(h, '__name__') and 'list_tools' in str(h):
                    handler = h
                    break
            assert handler is not None
            result = await handler(ListToolsRequest(method="tools/list"))
            inner = result.root if hasattr(result, 'root') else result
            tools = inner.tools if hasattr(inner, 'tools') else inner
            tool_names = {t.name for t in tools}
            assert "anchor_hash" in tool_names


class TestVerifyHash:
    """Test verify_hash tool execution."""

    @pytest.fixture
    def server(self):
        return create_server()

    def _mock_record(self):
        """Create a mock ChainProvenanceRecord."""
        mock_rec = MagicMock()
        mock_rec.owner = "0x1234567890abcdef1234567890abcdef12345678"
        mock_rec.timestamp = 1700000000
        mock_rec.data_type = "swarm-provenance"
        mock_rec.status = MagicMock()
        mock_rec.status.name = "ACTIVE"
        return mock_rec

    async def test_verify_registered(self, server):
        """Registered hash should show provenance info."""
        mock_client = MagicMock()
        mock_client.verify.return_value = True
        mock_client.get.return_value = self._mock_record()

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "verify_hash", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert not result.isError
        text = result.content[0].text
        assert "IS registered" in text
        assert TEST_REFERENCE in text
        assert "0x1234" in text
        assert "2023" in text  # timestamp 1700000000
        assert "ACTIVE" in text
        mock_client.verify.assert_called_once_with(TEST_REFERENCE)

    async def test_verify_registered_timestamp_zero(self, server):
        """Timestamp=0 (epoch) should display as 'unknown' since it's falsy."""
        mock_client = MagicMock()
        mock_client.verify.return_value = True
        mock_rec = self._mock_record()
        mock_rec.timestamp = 0
        mock_client.get.return_value = mock_rec

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "verify_hash", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert not result.isError
        text = result.content[0].text
        assert "IS registered" in text
        assert "unknown" in text

    async def test_verify_not_registered(self, server):
        """Unregistered hash should show not-found message."""
        mock_client = MagicMock()
        mock_client.verify.return_value = False

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "verify_hash", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert not result.isError
        text = result.content[0].text
        assert "NOT registered" in text
        assert "_next: anchor_hash" in text

    async def test_verify_registered_hints(self, server):
        """Registered hash should suggest download_data next."""
        mock_client = MagicMock()
        mock_client.verify.return_value = True
        mock_client.get.return_value = self._mock_record()

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "verify_hash", {
                "swarm_hash": TEST_REFERENCE,
            })

        text = result.content[0].text
        assert "_next: download_data" in text
        assert "_related:" in text

    async def test_verify_not_registered_hints(self, server):
        """Unregistered hash should suggest anchor_hash next."""
        mock_client = MagicMock()
        mock_client.verify.return_value = False

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "verify_hash", {
                "swarm_hash": TEST_REFERENCE,
            })

        text = result.content[0].text
        assert "_next: anchor_hash" in text

    async def test_verify_not_available(self, server):
        """CHAIN_AVAILABLE=False should return error."""
        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', False):
            result = await call_tool_directly(server, "verify_hash", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert result.isError
        text = result.content[0].text
        assert "not available" in text.lower() or "not installed" in text.lower()

    async def test_verify_no_wallet(self, server):
        """verify_hash should work without wallet key (chain_client=None)."""
        mock_provider = MagicMock()
        mock_provider.web3 = MagicMock()
        mock_provider.contract_address = "0x9a3c6F47B69211F05891CCb7aD33596290b9fE64"

        mock_contract = MagicMock()
        zero_address = "0x" + "0" * 40
        mock_contract.get_data_record.return_value = (
            bytes.fromhex(TEST_REFERENCE),
            zero_address, 0, "", [], [], 0,
        )

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', None), \
             patch('swarm_provenance_mcp.chain.provider.ChainProvider', return_value=mock_provider), \
             patch('swarm_provenance_mcp.chain.contract.DataProvenanceContract', return_value=mock_contract):
            result = await call_tool_directly(server, "verify_hash", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert not result.isError
        text = result.content[0].text
        assert "NOT registered" in text

    async def test_verify_no_wallet_registered(self, server):
        """verify_hash without wallet should show record when found."""
        mock_provider = MagicMock()
        mock_provider.web3 = MagicMock()
        mock_provider.contract_address = "0x9a3c6F47B69211F05891CCb7aD33596290b9fE64"

        mock_contract = MagicMock()
        mock_contract.get_data_record.return_value = (
            bytes.fromhex(TEST_REFERENCE),
            "0x1234567890abcdef1234567890abcdef12345678",
            1700000000,
            "swarm-provenance",
            [],
            [],
            0,
        )

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', None), \
             patch('swarm_provenance_mcp.chain.provider.ChainProvider', return_value=mock_provider), \
             patch('swarm_provenance_mcp.chain.contract.DataProvenanceContract', return_value=mock_contract):
            result = await call_tool_directly(server, "verify_hash", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert not result.isError
        text = result.content[0].text
        assert "IS registered" in text
        assert "0x1234" in text

    async def test_verify_invalid_hash(self, server):
        """Invalid hash format should return validation error."""
        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', MagicMock()):
            result = await call_tool_directly(server, "verify_hash", {
                "swarm_hash": "bad-hash",
            })

        assert result.isError
        text = result.content[0].text
        assert "retryable: false" in text

    async def test_verify_missing_hash(self, server):
        """Missing swarm_hash should return validation error."""
        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', MagicMock()):
            result = await call_tool_directly(server, "verify_hash", {})

        assert result.isError

    async def test_verify_connection_error(self, server):
        """ChainConnectionError should be retryable."""
        from swarm_provenance_mcp.chain.exceptions import ChainConnectionError

        mock_client = MagicMock()
        mock_client.verify.side_effect = ChainConnectionError(
            "RPC unreachable", rpc_url="https://sepolia.base.org"
        )

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "verify_hash", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert result.isError
        text = result.content[0].text
        assert "retryable: true" in text
        assert "_next: chain_health" in text

    async def test_verify_tool_registered(self, server):
        """verify_hash should appear in list_tools when chain is enabled."""
        from mcp.types import ListToolsRequest

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.settings') as mock_settings:
            mock_settings.chain_enabled = True
            mock_settings.mcp_server_name = "test"
            mock_settings.mcp_server_version = "0.1.0"
            mock_settings.default_stamp_amount = 2000000000
            mock_settings.default_stamp_depth = 17

            test_server = create_server()
            handler = None
            for h in test_server.request_handlers.values():
                if hasattr(h, '__name__') and 'list_tools' in str(h):
                    handler = h
                    break
            assert handler is not None
            result = await handler(ListToolsRequest(method="tools/list"))
            inner = result.root if hasattr(result, 'root') else result
            tools = inner.tools if hasattr(inner, 'tools') else inner
            tool_names = {t.name for t in tools}
            assert "verify_hash" in tool_names


class TestGetProvenance:
    """Test get_provenance tool execution."""

    @pytest.fixture
    def server(self):
        return create_server()

    def _mock_record(self, **overrides):
        """Create a mock ChainProvenanceRecord."""
        mock_rec = MagicMock()
        mock_rec.owner = overrides.get(
            "owner", "0x1234567890abcdef1234567890abcdef12345678"
        )
        mock_rec.timestamp = overrides.get("timestamp", 1700000000)
        mock_rec.data_type = overrides.get("data_type", "swarm-provenance")
        mock_rec.status = MagicMock()
        mock_rec.status.value = overrides.get("status_value", 0)
        mock_rec.status.name = overrides.get("status_name", "ACTIVE")
        mock_rec.transformations = overrides.get("transformations", [])
        mock_rec.accessors = overrides.get("accessors", [])
        return mock_rec

    async def test_get_provenance_success(self, server):
        """Registered hash should return full provenance record."""
        mock_client = MagicMock()
        mock_client.get.return_value = self._mock_record()

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "get_provenance", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert not result.isError
        text = result.content[0].text
        assert "Provenance Record" in text
        assert TEST_REFERENCE in text
        assert "0x1234" in text
        assert "2023" in text  # timestamp 1700000000
        assert "swarm-provenance" in text
        assert "ACTIVE" in text
        mock_client.get.assert_called_once_with(TEST_REFERENCE)

    async def test_get_provenance_with_transformations(self, server):
        """Record with transformations should list them."""
        mock_t1 = MagicMock()
        mock_t1.description = "Anonymized personal data"
        mock_t2 = MagicMock()
        mock_t2.description = "Compressed to gzip"
        mock_client = MagicMock()
        mock_client.get.return_value = self._mock_record(
            transformations=[mock_t1, mock_t2],
        )

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "get_provenance", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert not result.isError
        text = result.content[0].text
        assert "Transformations (2)" in text
        assert "Anonymized personal data" in text
        assert "Compressed to gzip" in text

    async def test_get_provenance_with_accessors(self, server):
        """Record with accessors should list them."""
        accessors = [
            "0xaaaa567890abcdef1234567890abcdef12345678",
            "0xbbbb567890abcdef1234567890abcdef12345678",
        ]
        mock_client = MagicMock()
        mock_client.get.return_value = self._mock_record(accessors=accessors)

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "get_provenance", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert not result.isError
        text = result.content[0].text
        assert "Accessors (2)" in text
        assert "0xaaaa" in text
        assert "0xbbbb" in text

    async def test_get_provenance_not_registered(self, server):
        """Unregistered hash should return NOT isError with suggestion."""
        from swarm_provenance_mcp.chain.exceptions import DataNotRegisteredError

        mock_client = MagicMock()
        mock_client.get.side_effect = DataNotRegisteredError(
            "Not registered", data_hash=TEST_REFERENCE,
        )

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "get_provenance", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert not result.isError
        text = result.content[0].text
        assert "NOT registered" in text
        assert "_next: anchor_hash" in text

    async def test_get_provenance_restricted_status(self, server):
        """RESTRICTED status should display correct label."""
        mock_client = MagicMock()
        mock_client.get.return_value = self._mock_record(status_value=1)

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "get_provenance", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert not result.isError
        text = result.content[0].text
        assert "RESTRICTED" in text

    async def test_get_provenance_deleted_status(self, server):
        """DELETED status should display correct label."""
        mock_client = MagicMock()
        mock_client.get.return_value = self._mock_record(status_value=2)

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "get_provenance", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert not result.isError
        text = result.content[0].text
        assert "DELETED" in text

    async def test_get_provenance_unknown_status(self, server):
        """Status value outside 0-2 should display as UNKNOWN with value."""
        mock_client = MagicMock()
        mock_client.get.return_value = self._mock_record(status_value=99)

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "get_provenance", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert not result.isError
        text = result.content[0].text
        assert "UNKNOWN (99)" in text

    async def test_get_provenance_timestamp_zero(self, server):
        """Timestamp=0 should display as 'unknown'."""
        mock_client = MagicMock()
        mock_client.get.return_value = self._mock_record(timestamp=0)

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "get_provenance", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert not result.isError
        text = result.content[0].text
        assert "unknown" in text

    async def test_get_provenance_not_available(self, server):
        """CHAIN_AVAILABLE=False should return error."""
        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', False):
            result = await call_tool_directly(server, "get_provenance", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert result.isError
        text = result.content[0].text
        assert "not available" in text.lower() or "not installed" in text.lower()

    async def test_get_provenance_no_wallet(self, server):
        """get_provenance should work without wallet (chain_client=None)."""
        mock_provider = MagicMock()
        mock_provider.web3 = MagicMock()
        mock_provider.contract_address = "0x9a3c6F47B69211F05891CCb7aD33596290b9fE64"

        mock_contract = MagicMock()
        mock_contract.get_data_record.return_value = (
            bytes.fromhex(TEST_REFERENCE),
            "0x1234567890abcdef1234567890abcdef12345678",
            1700000000,
            "swarm-provenance",
            [],
            [],
            0,
        )

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', None), \
             patch('swarm_provenance_mcp.chain.provider.ChainProvider', return_value=mock_provider), \
             patch('swarm_provenance_mcp.chain.contract.DataProvenanceContract', return_value=mock_contract):
            result = await call_tool_directly(server, "get_provenance", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert not result.isError
        text = result.content[0].text
        assert "Provenance Record" in text
        assert "0x1234" in text

    async def test_get_provenance_no_wallet_not_registered(self, server):
        """get_provenance without wallet should handle unregistered hash."""
        mock_provider = MagicMock()
        mock_provider.web3 = MagicMock()
        mock_provider.contract_address = "0x9a3c6F47B69211F05891CCb7aD33596290b9fE64"

        mock_contract = MagicMock()
        zero_address = "0x" + "0" * 40
        mock_contract.get_data_record.return_value = (
            bytes.fromhex(TEST_REFERENCE),
            zero_address, 0, "", [], [], 0,
        )

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', None), \
             patch('swarm_provenance_mcp.chain.provider.ChainProvider', return_value=mock_provider), \
             patch('swarm_provenance_mcp.chain.contract.DataProvenanceContract', return_value=mock_contract):
            result = await call_tool_directly(server, "get_provenance", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert not result.isError
        text = result.content[0].text
        assert "NOT registered" in text
        assert "_next: anchor_hash" in text

    async def test_get_provenance_invalid_hash(self, server):
        """Invalid hash format should return validation error."""
        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', MagicMock()):
            result = await call_tool_directly(server, "get_provenance", {
                "swarm_hash": "not-valid",
            })

        assert result.isError
        text = result.content[0].text
        assert "retryable: false" in text

    async def test_get_provenance_missing_hash(self, server):
        """Missing swarm_hash should return validation error."""
        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', MagicMock()):
            result = await call_tool_directly(server, "get_provenance", {})

        assert result.isError

    async def test_get_provenance_connection_error(self, server):
        """ChainConnectionError should be retryable."""
        from swarm_provenance_mcp.chain.exceptions import ChainConnectionError

        mock_client = MagicMock()
        mock_client.get.side_effect = ChainConnectionError(
            "RPC unreachable", rpc_url="https://sepolia.base.org"
        )

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "get_provenance", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert result.isError
        text = result.content[0].text
        assert "retryable: true" in text
        assert "_next: chain_health" in text

    async def test_get_provenance_generic_chain_error(self, server):
        """Generic ChainError should be non-retryable."""
        from swarm_provenance_mcp.chain.exceptions import ChainError

        mock_client = MagicMock()
        mock_client.get.side_effect = ChainError("Something unexpected")

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "get_provenance", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert result.isError
        text = result.content[0].text
        assert "retryable: false" in text

    async def test_get_provenance_hints(self, server):
        """Success response should include correct hints."""
        mock_client = MagicMock()
        mock_client.get.return_value = self._mock_record()

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "get_provenance", {
                "swarm_hash": TEST_REFERENCE,
            })

        text = result.content[0].text
        assert "_next: download_data" in text
        assert "_related:" in text
        assert "verify_hash" in text

    async def test_get_provenance_tool_registered(self, server):
        """get_provenance should appear in list_tools when chain is enabled."""
        from mcp.types import ListToolsRequest

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.settings') as mock_settings:
            mock_settings.chain_enabled = True
            mock_settings.mcp_server_name = "test"
            mock_settings.mcp_server_version = "0.1.0"
            mock_settings.default_stamp_amount = 2000000000
            mock_settings.default_stamp_depth = 17

            test_server = create_server()
            handler = None
            for h in test_server.request_handlers.values():
                if hasattr(h, '__name__') and 'list_tools' in str(h):
                    handler = h
                    break
            assert handler is not None
            result = await handler(ListToolsRequest(method="tools/list"))
            inner = result.root if hasattr(result, 'root') else result
            tools = inner.tools if hasattr(inner, 'tools') else inner
            tool_names = {t.name for t in tools}
            assert "get_provenance" in tool_names


# Second valid 64-char hex hash for transform tests (distinct from TEST_REFERENCE)
TEST_NEW_HASH = "c" * 64


class TestRecordTransform:
    """Test record_transform tool execution."""

    @pytest.fixture
    def server(self):
        return create_server()

    def _mock_transform_result(self):
        """Create a mock TransformResult."""
        mock_res = MagicMock()
        mock_res.original_hash = TEST_REFERENCE
        mock_res.new_hash = TEST_NEW_HASH
        mock_res.description = "Anonymized PII"
        mock_res.tx_hash = "0xabc123"
        mock_res.block_number = 12345
        mock_res.gas_used = 85000
        mock_res.explorer_url = "https://sepolia.basescan.org/tx/0xabc123"
        return mock_res

    async def test_transform_success(self, server):
        """Basic transform should record lineage and show tx details."""
        mock_client = MagicMock()
        mock_client.transform.return_value = self._mock_transform_result()
        mock_client.balance.return_value = MagicMock(
            address="0x1234", balance_wei=10**18, chain="base-sepolia"
        )

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "record_transform", {
                "original_hash": TEST_REFERENCE,
                "new_hash": TEST_NEW_HASH,
                "description": "Anonymized PII",
            })

        assert not result.isError
        text = result.content[0].text
        assert "Transformation recorded" in text
        assert TEST_REFERENCE in text
        assert TEST_NEW_HASH in text
        assert "Anonymized PII" in text
        assert "0xabc123" in text
        assert "12,345" in text  # block_number formatted
        assert "85,000" in text  # gas_used formatted
        mock_client.transform.assert_called_once_with(
            TEST_REFERENCE, TEST_NEW_HASH, "Anonymized PII"
        )

    async def test_transform_no_description(self, server):
        """Transform without description should use empty string."""
        mock_client = MagicMock()
        mock_res = self._mock_transform_result()
        mock_res.description = ""
        mock_client.transform.return_value = mock_res
        mock_client.balance.return_value = MagicMock(
            address="0x1234", balance_wei=10**18, chain="base-sepolia"
        )

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "record_transform", {
                "original_hash": TEST_REFERENCE,
                "new_hash": TEST_NEW_HASH,
            })

        assert not result.isError
        text = result.content[0].text
        assert "Transformation recorded" in text
        # Description line should not appear when empty
        assert "Description:" not in text
        mock_client.transform.assert_called_once_with(
            TEST_REFERENCE, TEST_NEW_HASH, ""
        )

    async def test_transform_with_restrict(self, server):
        """restrict_original=True should call set_status after transform."""
        mock_client = MagicMock()
        mock_client.transform.return_value = self._mock_transform_result()
        mock_client.set_status.return_value = MagicMock()
        mock_client.balance.return_value = MagicMock(
            address="0x1234", balance_wei=10**18, chain="base-sepolia"
        )

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "record_transform", {
                "original_hash": TEST_REFERENCE,
                "new_hash": TEST_NEW_HASH,
                "description": "Filtered for EU",
                "restrict_original": True,
            })

        assert not result.isError
        text = result.content[0].text
        assert "RESTRICTED" in text
        mock_client.set_status.assert_called_once_with(TEST_REFERENCE, 1)

    async def test_transform_restrict_failure_non_fatal(self, server):
        """If restrict fails, transform should still succeed with a warning."""
        mock_client = MagicMock()
        mock_client.transform.return_value = self._mock_transform_result()
        mock_client.set_status.side_effect = Exception("set_status reverted")
        mock_client.balance.return_value = MagicMock(
            address="0x1234", balance_wei=10**18, chain="base-sepolia"
        )

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "record_transform", {
                "original_hash": TEST_REFERENCE,
                "new_hash": TEST_NEW_HASH,
                "restrict_original": True,
            })

        assert not result.isError
        text = result.content[0].text
        assert "Transformation recorded" in text
        assert "failed to restrict" in text
        assert "set_status reverted" in text

    async def test_transform_same_hash_rejected(self, server):
        """original_hash == new_hash should be rejected."""
        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', MagicMock()):
            result = await call_tool_directly(server, "record_transform", {
                "original_hash": TEST_REFERENCE,
                "new_hash": TEST_REFERENCE,
            })

        assert result.isError
        text = result.content[0].text
        assert "must be different" in text

    async def test_transform_not_registered(self, server):
        """DataNotRegisteredError should guide agent to anchor first."""
        from swarm_provenance_mcp.chain.exceptions import DataNotRegisteredError

        mock_client = MagicMock()
        mock_client.transform.side_effect = DataNotRegisteredError(
            "Not registered", data_hash=TEST_REFERENCE,
        )

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "record_transform", {
                "original_hash": TEST_REFERENCE,
                "new_hash": TEST_NEW_HASH,
            })

        # Intentionally NOT isError — guides agent to anchor first
        assert not result.isError
        text = result.content[0].text
        assert "not registered" in text.lower()
        assert "_next: anchor_hash" in text

    async def test_transform_transaction_error(self, server):
        """ChainTransactionError should be non-retryable."""
        from swarm_provenance_mcp.chain.exceptions import ChainTransactionError

        mock_client = MagicMock()
        mock_client.transform.side_effect = ChainTransactionError(
            "Reverted", tx_hash="0xfailed"
        )

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "record_transform", {
                "original_hash": TEST_REFERENCE,
                "new_hash": TEST_NEW_HASH,
            })

        assert result.isError
        text = result.content[0].text
        assert "retryable: false" in text
        assert "0xfailed" in text
        assert "_next: chain_balance" in text

    async def test_transform_connection_error(self, server):
        """ChainConnectionError should be retryable."""
        from swarm_provenance_mcp.chain.exceptions import ChainConnectionError

        mock_client = MagicMock()
        mock_client.transform.side_effect = ChainConnectionError(
            "RPC unreachable", rpc_url="https://sepolia.base.org"
        )

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "record_transform", {
                "original_hash": TEST_REFERENCE,
                "new_hash": TEST_NEW_HASH,
            })

        assert result.isError
        text = result.content[0].text
        assert "retryable: true" in text
        assert "_next: chain_health" in text

    async def test_transform_not_available(self, server):
        """CHAIN_AVAILABLE=False should return error."""
        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', False):
            result = await call_tool_directly(server, "record_transform", {
                "original_hash": TEST_REFERENCE,
                "new_hash": TEST_NEW_HASH,
            })

        assert result.isError
        text = result.content[0].text
        assert "not available" in text.lower() or "not installed" in text.lower()

    async def test_transform_no_client(self, server):
        """chain_client=None should return error."""
        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', None):
            result = await call_tool_directly(server, "record_transform", {
                "original_hash": TEST_REFERENCE,
                "new_hash": TEST_NEW_HASH,
            })

        assert result.isError
        text = result.content[0].text
        assert "wallet" in text.lower() or "PROVENANCE_WALLET_KEY" in text

    async def test_transform_invalid_original_hash(self, server):
        """Invalid original_hash format should return validation error."""
        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', MagicMock()):
            result = await call_tool_directly(server, "record_transform", {
                "original_hash": "bad-hash",
                "new_hash": TEST_NEW_HASH,
            })

        assert result.isError
        text = result.content[0].text
        assert "retryable: false" in text

    async def test_transform_invalid_new_hash(self, server):
        """Invalid new_hash format should return validation error."""
        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', MagicMock()):
            result = await call_tool_directly(server, "record_transform", {
                "original_hash": TEST_REFERENCE,
                "new_hash": "not-valid",
            })

        assert result.isError
        text = result.content[0].text
        assert "retryable: false" in text

    async def test_transform_missing_original_hash(self, server):
        """Missing original_hash should return validation error."""
        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', MagicMock()):
            result = await call_tool_directly(server, "record_transform", {
                "new_hash": TEST_NEW_HASH,
            })

        assert result.isError

    async def test_transform_missing_new_hash(self, server):
        """Missing new_hash should return validation error."""
        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', MagicMock()):
            result = await call_tool_directly(server, "record_transform", {
                "original_hash": TEST_REFERENCE,
            })

        assert result.isError

    async def test_transform_description_too_long(self, server):
        """Description over 256 chars should return validation error."""
        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', MagicMock()):
            result = await call_tool_directly(server, "record_transform", {
                "original_hash": TEST_REFERENCE,
                "new_hash": TEST_NEW_HASH,
                "description": "x" * 257,
            })

        assert result.isError
        text = result.content[0].text
        assert "256" in text

    async def test_transform_validation_error(self, server):
        """ChainValidationError should be non-retryable."""
        from swarm_provenance_mcp.chain.exceptions import ChainValidationError

        mock_client = MagicMock()
        mock_client.transform.side_effect = ChainValidationError(
            "Invalid on-chain data"
        )

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "record_transform", {
                "original_hash": TEST_REFERENCE,
                "new_hash": TEST_NEW_HASH,
            })

        assert result.isError
        text = result.content[0].text
        assert "retryable: false" in text

    async def test_transform_generic_chain_error(self, server):
        """Generic ChainError should be non-retryable."""
        from swarm_provenance_mcp.chain.exceptions import ChainError

        mock_client = MagicMock()
        mock_client.transform.side_effect = ChainError("Something unexpected")

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "record_transform", {
                "original_hash": TEST_REFERENCE,
                "new_hash": TEST_NEW_HASH,
            })

        assert result.isError
        text = result.content[0].text
        assert "retryable: false" in text

    async def test_transform_hints(self, server):
        """Success response should include correct hints."""
        mock_client = MagicMock()
        mock_client.transform.return_value = self._mock_transform_result()
        mock_client.balance.return_value = MagicMock(
            address="0x1234", balance_wei=10**18, chain="base-sepolia"
        )

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "record_transform", {
                "original_hash": TEST_REFERENCE,
                "new_hash": TEST_NEW_HASH,
            })

        text = result.content[0].text
        assert "_next: get_provenance" in text
        assert "_related:" in text
        assert "download_data" in text

    async def test_transform_balance_check_failure_silent(self, server):
        """Balance check failure after transform should not break success."""
        mock_client = MagicMock()
        mock_client.transform.return_value = self._mock_transform_result()
        mock_client.balance.side_effect = Exception("RPC down")

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "record_transform", {
                "original_hash": TEST_REFERENCE,
                "new_hash": TEST_NEW_HASH,
            })

        assert not result.isError
        text = result.content[0].text
        assert "Transformation recorded" in text

    async def test_transform_no_explorer_url(self, server):
        """When explorer_url is None, Explorer line should not appear."""
        mock_client = MagicMock()
        mock_res = self._mock_transform_result()
        mock_res.explorer_url = None
        mock_client.transform.return_value = mock_res
        mock_client.balance.return_value = MagicMock(
            address="0x1234", balance_wei=10**18, chain="base-sepolia"
        )

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "record_transform", {
                "original_hash": TEST_REFERENCE,
                "new_hash": TEST_NEW_HASH,
            })

        assert not result.isError
        text = result.content[0].text
        assert "Transformation recorded" in text
        assert "Explorer:" not in text

    async def test_transform_low_balance_warning(self, server):
        """Low balance after transform should append funding guidance."""
        mock_client = MagicMock()
        mock_client.transform.return_value = self._mock_transform_result()
        mock_client.balance.return_value = MagicMock(
            address="0x1234", balance_wei=10**14, chain="base-sepolia"
        )

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "record_transform", {
                "original_hash": TEST_REFERENCE,
                "new_hash": TEST_NEW_HASH,
            })

        assert not result.isError
        text = result.content[0].text
        assert "WARNING" in text or "CRITICAL" in text

    async def test_transform_tool_registered(self, server):
        """record_transform should appear in list_tools when chain is enabled."""
        from mcp.types import ListToolsRequest

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.settings') as mock_settings:
            mock_settings.chain_enabled = True
            mock_settings.mcp_server_name = "test"
            mock_settings.mcp_server_version = "0.1.0"
            mock_settings.default_stamp_amount = 2000000000
            mock_settings.default_stamp_depth = 17

            test_server = create_server()
            handler = None
            for h in test_server.request_handlers.values():
                if hasattr(h, '__name__') and 'list_tools' in str(h):
                    handler = h
                    break
            assert handler is not None
            result = await handler(ListToolsRequest(method="tools/list"))
            inner = result.root if hasattr(result, 'root') else result
            tools = inner.tools if hasattr(inner, 'tools') else inner
            tool_names = {t.name for t in tools}
            assert "record_transform" in tool_names


class TestGetProvenanceChain:
    """Test get_provenance_chain tool execution."""

    @pytest.fixture
    def server(self):
        return create_server()

    def _mock_record(self, data_hash=TEST_REFERENCE, **overrides):
        """Create a mock ChainProvenanceRecord."""
        mock_rec = MagicMock()
        mock_rec.data_hash = overrides.get("data_hash_val", data_hash)
        mock_rec.owner = overrides.get(
            "owner", "0x1234567890abcdef1234567890abcdef12345678"
        )
        mock_rec.timestamp = overrides.get("timestamp", 1700000000)
        mock_rec.data_type = overrides.get("data_type", "swarm-provenance")
        mock_rec.status = MagicMock()
        mock_rec.status.value = overrides.get("status_value", 0)
        mock_rec.status.name = overrides.get("status_name", "ACTIVE")
        mock_rec.transformations = overrides.get("transformations", [])
        mock_rec.accessors = overrides.get("accessors", [])
        return mock_rec

    async def test_chain_single_entry(self, server):
        """Single-entry chain should show one record."""
        mock_client = MagicMock()
        mock_client.get_provenance_chain.return_value = [
            self._mock_record(),
        ]

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "get_provenance_chain", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert not result.isError
        text = result.content[0].text
        assert "Provenance Chain (1 entry)" in text
        assert "0x1234" in text
        assert "ACTIVE" in text
        mock_client.get_provenance_chain.assert_called_once_with(
            TEST_REFERENCE, max_depth=10
        )

    async def test_chain_multi_entry(self, server):
        """Multi-entry chain should show tree with indentation."""
        mock_t = MagicMock()
        mock_t.description = "Anonymized PII"
        mock_t.new_data_hash = TEST_NEW_HASH

        mock_client = MagicMock()
        mock_client.get_provenance_chain.return_value = [
            self._mock_record(transformations=[mock_t]),
            self._mock_record(
                data_hash=TEST_NEW_HASH,
                data_hash_val=TEST_NEW_HASH,
                owner="0xabcdabcdabcdabcdabcdabcdabcdabcdabcdabcd",
            ),
        ]

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "get_provenance_chain", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert not result.isError
        text = result.content[0].text
        assert "Provenance Chain (2 entries)" in text
        assert "└─" in text  # second entry has tree prefix
        assert "Anonymized PII" in text
        assert "0xabcd" in text

    async def test_chain_custom_max_depth(self, server):
        """Custom max_depth should be passed through."""
        mock_client = MagicMock()
        mock_client.get_provenance_chain.return_value = [self._mock_record()]

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "get_provenance_chain", {
                "swarm_hash": TEST_REFERENCE,
                "max_depth": 5,
            })

        assert not result.isError
        mock_client.get_provenance_chain.assert_called_once_with(
            TEST_REFERENCE, max_depth=5
        )

    async def test_chain_empty(self, server):
        """Empty chain should show not-found message with anchor suggestion."""
        mock_client = MagicMock()
        mock_client.get_provenance_chain.return_value = []

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "get_provenance_chain", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert not result.isError
        text = result.content[0].text
        assert "No provenance chain found" in text
        assert "_next: anchor_hash" in text

    async def test_chain_max_depth_too_low(self, server):
        """max_depth < 1 should return validation error."""
        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', MagicMock()):
            result = await call_tool_directly(server, "get_provenance_chain", {
                "swarm_hash": TEST_REFERENCE,
                "max_depth": 0,
            })

        assert result.isError
        text = result.content[0].text
        assert "max_depth" in text

    async def test_chain_max_depth_too_high(self, server):
        """max_depth > 50 should return validation error."""
        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', MagicMock()):
            result = await call_tool_directly(server, "get_provenance_chain", {
                "swarm_hash": TEST_REFERENCE,
                "max_depth": 51,
            })

        assert result.isError
        text = result.content[0].text
        assert "max_depth" in text

    async def test_chain_not_available(self, server):
        """CHAIN_AVAILABLE=False should return error."""
        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', False):
            result = await call_tool_directly(server, "get_provenance_chain", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert result.isError
        text = result.content[0].text
        assert "not available" in text.lower() or "not installed" in text.lower()

    async def test_chain_no_wallet(self, server):
        """get_provenance_chain should work without wallet (chain_client=None)."""
        mock_provider = MagicMock()
        mock_provider.web3 = MagicMock()
        mock_provider.contract_address = "0x9a3c6F47B69211F05891CCb7aD33596290b9fE64"

        mock_contract = MagicMock()
        mock_contract.get_data_record.return_value = (
            bytes.fromhex(TEST_REFERENCE),
            "0x1234567890abcdef1234567890abcdef12345678",
            1700000000,
            "swarm-provenance",
            [],
            [],
            0,
        )

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', None), \
             patch('swarm_provenance_mcp.chain.provider.ChainProvider', return_value=mock_provider), \
             patch('swarm_provenance_mcp.chain.contract.DataProvenanceContract', return_value=mock_contract):
            result = await call_tool_directly(server, "get_provenance_chain", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert not result.isError
        text = result.content[0].text
        assert "Provenance Chain (1 entry)" in text
        assert "0x1234" in text

    async def test_chain_no_wallet_not_registered(self, server):
        """No-wallet path with unregistered hash should return empty chain."""
        mock_provider = MagicMock()
        mock_provider.web3 = MagicMock()
        mock_provider.contract_address = "0x9a3c6F47B69211F05891CCb7aD33596290b9fE64"

        mock_contract = MagicMock()
        zero_address = "0x" + "0" * 40
        mock_contract.get_data_record.return_value = (
            bytes.fromhex(TEST_REFERENCE),
            zero_address, 0, "", [], [], 0,
        )

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', None), \
             patch('swarm_provenance_mcp.chain.provider.ChainProvider', return_value=mock_provider), \
             patch('swarm_provenance_mcp.chain.contract.DataProvenanceContract', return_value=mock_contract):
            result = await call_tool_directly(server, "get_provenance_chain", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert not result.isError
        text = result.content[0].text
        assert "No provenance chain found" in text

    async def test_chain_invalid_hash(self, server):
        """Invalid hash format should return validation error."""
        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', MagicMock()):
            result = await call_tool_directly(server, "get_provenance_chain", {
                "swarm_hash": "bad-hash",
            })

        assert result.isError
        text = result.content[0].text
        assert "retryable: false" in text

    async def test_chain_missing_hash(self, server):
        """Missing swarm_hash should return validation error."""
        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', MagicMock()):
            result = await call_tool_directly(server, "get_provenance_chain", {})

        assert result.isError

    async def test_chain_connection_error(self, server):
        """ChainConnectionError should be retryable."""
        from swarm_provenance_mcp.chain.exceptions import ChainConnectionError

        mock_client = MagicMock()
        mock_client.get_provenance_chain.side_effect = ChainConnectionError(
            "RPC unreachable", rpc_url="https://sepolia.base.org"
        )

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "get_provenance_chain", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert result.isError
        text = result.content[0].text
        assert "retryable: true" in text
        assert "_next: chain_health" in text

    async def test_chain_generic_error(self, server):
        """Generic ChainError should be non-retryable."""
        from swarm_provenance_mcp.chain.exceptions import ChainError

        mock_client = MagicMock()
        mock_client.get_provenance_chain.side_effect = ChainError("Unexpected")

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "get_provenance_chain", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert result.isError
        text = result.content[0].text
        assert "retryable: false" in text

    async def test_chain_hints(self, server):
        """Success response should include correct hints."""
        mock_client = MagicMock()
        mock_client.get_provenance_chain.return_value = [self._mock_record()]

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "get_provenance_chain", {
                "swarm_hash": TEST_REFERENCE,
            })

        text = result.content[0].text
        assert "_next: get_provenance" in text
        assert "_related:" in text
        assert "record_transform" in text

    async def test_chain_from_leaf_node(self, server):
        """Querying from leaf should return multi-entry chain via reverse traversal."""
        parent_hash = "aa" * 32
        leaf_hash = "bb" * 32

        mock_t = MagicMock()
        mock_t.description = "Step 1"
        mock_t.new_data_hash = leaf_hash

        mock_client = MagicMock()
        # chain_client returns both parent and leaf when queried from leaf
        mock_client.get_provenance_chain.return_value = [
            self._mock_record(
                data_hash=leaf_hash, data_hash_val=leaf_hash,
                data_type="derived",
            ),
            self._mock_record(
                data_hash=parent_hash, data_hash_val=parent_hash,
                data_type="original", transformations=[mock_t],
            ),
        ]

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "get_provenance_chain", {
                "swarm_hash": leaf_hash,
            })

        assert not result.isError
        text = result.content[0].text
        assert "2 entries" in text
        assert leaf_hash in text
        assert parent_hash in text
        mock_client.get_provenance_chain.assert_called_once_with(
            leaf_hash, max_depth=10
        )

    async def test_chain_timestamp_zero(self, server):
        """Timestamp=0 should display as 'unknown'."""
        mock_client = MagicMock()
        mock_client.get_provenance_chain.return_value = [
            self._mock_record(timestamp=0),
        ]

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "get_provenance_chain", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert not result.isError
        text = result.content[0].text
        assert "unknown" in text

    async def test_chain_restricted_status(self, server):
        """RESTRICTED status should display correctly in chain."""
        mock_client = MagicMock()
        mock_client.get_provenance_chain.return_value = [
            self._mock_record(status_value=1),
        ]

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "get_provenance_chain", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert not result.isError
        text = result.content[0].text
        assert "RESTRICTED" in text

    async def test_chain_tool_registered(self, server):
        """get_provenance_chain should appear in list_tools when chain is enabled."""
        from mcp.types import ListToolsRequest

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.settings') as mock_settings:
            mock_settings.chain_enabled = True
            mock_settings.mcp_server_name = "test"
            mock_settings.mcp_server_version = "0.1.0"
            mock_settings.default_stamp_amount = 2000000000
            mock_settings.default_stamp_depth = 17

            test_server = create_server()
            handler = None
            for h in test_server.request_handlers.values():
                if hasattr(h, '__name__') and 'list_tools' in str(h):
                    handler = h
                    break
            assert handler is not None
            result = await handler(ListToolsRequest(method="tools/list"))
            inner = result.root if hasattr(result, 'root') else result
            tools = inner.tools if hasattr(inner, 'tools') else inner
            tool_names = {t.name for t in tools}
            assert "get_provenance_chain" in tool_names


class TestInsufficientFundsHelpers:
    """Test _is_insufficient_funds_error and _format_insufficient_funds_error."""

    def test_detects_insufficient_funds(self):
        from swarm_provenance_mcp.server import _is_insufficient_funds_error
        assert _is_insufficient_funds_error(Exception("insufficient funds for transfer"))
        assert _is_insufficient_funds_error(Exception("Insufficient funds for gas"))
        assert _is_insufficient_funds_error(Exception("insufficient balance"))

    def test_ignores_unrelated_errors(self):
        from swarm_provenance_mcp.server import _is_insufficient_funds_error
        assert not _is_insufficient_funds_error(Exception("reverted"))
        assert not _is_insufficient_funds_error(Exception("nonce too low"))
        assert not _is_insufficient_funds_error(Exception(""))

    def test_format_with_chain_client(self):
        from swarm_provenance_mcp.server import _format_insufficient_funds_error
        mock_client = MagicMock()
        mock_client.address = "0xABC"
        mock_client.chain = "base-sepolia"
        msg = _format_insufficient_funds_error("anchor_hash", mock_client)
        assert "anchor_hash" in msg
        assert "0xABC" in msg
        assert "faucet" in msg.lower() or "sepolia" in msg.lower()
        assert "chain_balance" in msg

    def test_format_without_chain_client(self):
        from swarm_provenance_mcp.server import _format_insufficient_funds_error
        with patch('swarm_provenance_mcp.server.settings') as mock_settings:
            mock_settings.chain_name = "base"
            msg = _format_insufficient_funds_error("record_transform", None)
        assert "record_transform" in msg
        assert "chain_balance" in msg

    def test_format_testnet_shows_faucet(self):
        from swarm_provenance_mcp.server import _format_insufficient_funds_error
        mock_client = MagicMock()
        mock_client.address = "0x1234"
        mock_client.chain = "base-sepolia"
        msg = _format_insufficient_funds_error("anchor_hash", mock_client)
        assert "faucet" in msg.lower()

    def test_format_mainnet_shows_bridge(self):
        from swarm_provenance_mcp.server import _format_insufficient_funds_error
        mock_client = MagicMock()
        mock_client.address = "0x1234"
        mock_client.chain = "base"
        msg = _format_insufficient_funds_error("anchor_hash", mock_client)
        assert "bridge" in msg.lower()


class TestAnchorInsufficientFunds:
    """Test insufficient funds error handling in anchor_hash."""

    @pytest.fixture
    def server(self):
        return create_server()

    async def test_transaction_error_insufficient_funds(self, server):
        """ChainTransactionError with 'insufficient funds' gets special formatting."""
        from swarm_provenance_mcp.chain.exceptions import ChainTransactionError

        mock_client = MagicMock()
        mock_client.anchor.side_effect = ChainTransactionError(
            "insufficient funds for transfer"
        )
        mock_client.address = "0xWALLET"
        mock_client.chain = "base-sepolia"

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "anchor_hash", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert result.isError
        text = result.content[0].text
        assert "insufficient funds" in text.lower()
        assert "chain_balance" in text

    async def test_generic_error_insufficient_funds(self, server):
        """Generic exception with 'insufficient funds' also gets special formatting."""
        mock_client = MagicMock()
        mock_client.anchor.side_effect = Exception(
            "insufficient funds for gas"
        )
        mock_client.address = "0xWALLET"
        mock_client.chain = "base-sepolia"

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "anchor_hash", {
                "swarm_hash": TEST_REFERENCE,
            })

        assert result.isError
        text = result.content[0].text
        assert "insufficient funds" in text.lower()


class TestTransformInsufficientFunds:
    """Test insufficient funds error handling in record_transform."""

    @pytest.fixture
    def server(self):
        return create_server()

    async def test_transaction_error_insufficient_funds(self, server):
        """ChainTransactionError with 'insufficient funds' gets special formatting."""
        from swarm_provenance_mcp.chain.exceptions import ChainTransactionError

        mock_client = MagicMock()
        mock_client.transform.side_effect = ChainTransactionError(
            "insufficient funds for transfer"
        )
        mock_client.address = "0xWALLET"
        mock_client.chain = "base-sepolia"

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "record_transform", {
                "original_hash": TEST_REFERENCE,
                "new_hash": TEST_NEW_HASH,
            })

        assert result.isError
        text = result.content[0].text
        assert "insufficient funds" in text.lower()
        assert "chain_balance" in text


class TestHealthCheckBalanceWarning:
    """Test proactive balance check in health_check."""

    @pytest.fixture
    def server(self):
        return create_server()

    async def test_low_balance_shows_warning(self, server):
        """health_check should warn when wallet balance is too low."""
        mock_chain = MagicMock()
        mock_chain.chain = "base-sepolia"
        mock_chain.contract_address = "0xCONTRACT"
        mock_chain.address = "0xWALLET"
        mock_chain._provider.health_check.return_value = True
        mock_chain._provider.get_block_number.return_value = 12345
        mock_chain._provider.rpc_response_time_ms = 100
        mock_chain._provider.rpc_url = "https://sepolia.base.org"
        mock_chain._provider.chain_id = 84532
        mock_balance = MagicMock()
        mock_balance.address = "0xWALLET"
        mock_balance.balance_wei = 0  # empty wallet
        mock_balance.chain = "base-sepolia"
        mock_chain.balance.return_value = mock_balance

        with patch('swarm_provenance_mcp.server.gateway_client') as mock_gw, \
             patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_chain):
            mock_gw.health_check.return_value = {
                "status": "healthy",
                "gateway_url": "http://localhost",
                "response_time_ms": 10,
            }
            mock_gw.list_stamps.return_value = {
                "stamps": [{"batchID": TEST_STAMP_ID, "usable": True}],
                "total_count": 1,
            }
            result = await call_tool_directly(server, "health_check", {})

        assert not result.isError
        text = result.content[0].text
        # Should contain funding guidance for empty wallet
        assert "faucet" in text.lower() or "fund" in text.lower() or "CRITICAL" in text

    async def test_balance_check_failure_doesnt_break_health(self, server):
        """If balance() throws, health_check should still succeed."""
        mock_chain = MagicMock()
        mock_chain.chain = "base-sepolia"
        mock_chain.contract_address = "0xCONTRACT"
        mock_chain.address = "0xWALLET"
        mock_chain._provider.health_check.return_value = True
        mock_chain._provider.get_block_number.return_value = 12345
        mock_chain._provider.rpc_response_time_ms = 100
        mock_chain._provider.rpc_url = "https://sepolia.base.org"
        mock_chain._provider.chain_id = 84532
        mock_chain.balance.side_effect = Exception("RPC timeout")

        with patch('swarm_provenance_mcp.server.gateway_client') as mock_gw, \
             patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_chain):
            mock_gw.health_check.return_value = {
                "status": "healthy",
                "gateway_url": "http://localhost",
                "response_time_ms": 10,
            }
            mock_gw.list_stamps.return_value = {
                "stamps": [{"batchID": TEST_STAMP_ID, "usable": True}],
                "total_count": 1,
            }
            result = await call_tool_directly(server, "health_check", {})

        assert not result.isError
        text = result.content[0].text
        assert "operational" in text.lower() or "Gateway" in text


class TestAnchorAlreadyRegisteredRevert:
    """Test anchor() catching 'already registered' reverts from stale RPC."""

    async def test_revert_converted_to_already_registered(self):
        """When pre-check misses but tx reverts, should raise DataAlreadyRegisteredError."""
        from swarm_provenance_mcp.chain.client import ChainClient
        from swarm_provenance_mcp.chain.exceptions import (
            ChainTransactionError, DataAlreadyRegisteredError, DataNotRegisteredError,
        )

        client = ChainClient.__new__(ChainClient)
        client._wallet = MagicMock()
        client._wallet.address = "0xTEST"
        client._contract = MagicMock()
        client._provider = MagicMock()
        client._gas_limit = None
        client._gas_limit_multiplier = 1.2

        # Pre-check: hash not found (stale read)
        mock_record_not_found = MagicMock(side_effect=[
            DataNotRegisteredError("not found", data_hash="ab" * 32),
        ])

        # After revert: hash IS found
        mock_record_found = MagicMock()
        mock_record_found.owner = "0xOWNER"
        mock_record_found.timestamp = 1700000000
        mock_record_found.data_type = "test"

        call_count = [0]
        def get_side_effect(h):
            call_count[0] += 1
            if call_count[0] == 1:
                raise DataNotRegisteredError("not found", data_hash=h)
            return mock_record_found

        client.get = MagicMock(side_effect=get_side_effect)
        client._send_transaction = MagicMock(
            side_effect=ChainTransactionError("Data already registered")
        )
        client._contract.build_register_data_tx.return_value = {"from": "0xTEST"}

        with pytest.raises(DataAlreadyRegisteredError) as exc_info:
            client.anchor("ab" * 32)

        assert "already registered" in str(exc_info.value).lower()


class TestGetTransformationsFrom:
    """Test contract.get_transformations_from event query."""

    def test_returns_event_tuples(self):
        from swarm_provenance_mcp.chain.contract import DataProvenanceContract

        contract = DataProvenanceContract.__new__(DataProvenanceContract)
        contract._web3 = MagicMock()
        contract._web3.eth.block_number = 10000
        contract._contract = MagicMock()

        mock_event = MagicMock()
        mock_event.args.originalDataHash = b'\xab' * 32
        mock_event.args.newDataHash = b'\xcd' * 32
        mock_event.args.transformation = "Anonymized"

        contract._contract.events.DataTransformed.get_logs.return_value = [mock_event]

        results = contract.get_transformations_from("ab" * 32)

        assert len(results) == 1
        orig, new, desc = results[0]
        assert orig == b'\xab' * 32
        assert new == b'\xcd' * 32
        assert desc == "Anonymized"

    def test_empty_events(self):
        from swarm_provenance_mcp.chain.contract import DataProvenanceContract

        contract = DataProvenanceContract.__new__(DataProvenanceContract)
        contract._web3 = MagicMock()
        contract._web3.eth.block_number = 10000
        contract._contract = MagicMock()
        contract._contract.events.DataTransformed.get_logs.return_value = []

        results = contract.get_transformations_from("ab" * 32)
        assert results == []

    def test_lookback_blocks_calculation(self):
        from swarm_provenance_mcp.chain.contract import DataProvenanceContract

        contract = DataProvenanceContract.__new__(DataProvenanceContract)
        contract._web3 = MagicMock()
        contract._web3.eth.block_number = 100
        contract._contract = MagicMock()
        contract._contract.events.DataTransformed.get_logs.return_value = []

        contract.get_transformations_from("ab" * 32, lookback_blocks=5000)

        call_kwargs = contract._contract.events.DataTransformed.get_logs.call_args
        # from_block should be max(0, 100-5000) = 0
        assert call_kwargs.kwargs["from_block"] == 0
        assert call_kwargs.kwargs["to_block"] == 100

    def test_custom_lookback(self):
        from swarm_provenance_mcp.chain.contract import DataProvenanceContract

        contract = DataProvenanceContract.__new__(DataProvenanceContract)
        contract._web3 = MagicMock()
        contract._web3.eth.block_number = 50000
        contract._contract = MagicMock()
        contract._contract.events.DataTransformed.get_logs.return_value = []

        contract.get_transformations_from("ab" * 32, lookback_blocks=1000)

        call_kwargs = contract._contract.events.DataTransformed.get_logs.call_args
        assert call_kwargs.kwargs["from_block"] == 49000
        assert call_kwargs.kwargs["to_block"] == 50000

    def test_default_lookback_is_50000(self):
        """Default lookback should be 50,000 blocks (~28h on Base)."""
        from swarm_provenance_mcp.chain.contract import DataProvenanceContract

        contract = DataProvenanceContract.__new__(DataProvenanceContract)
        contract._web3 = MagicMock()
        contract._web3.eth.block_number = 100_000
        contract._contract = MagicMock()
        contract._contract.events.DataTransformed.get_logs.return_value = []

        contract.get_transformations_from("ab" * 32)

        call_kwargs = contract._contract.events.DataTransformed.get_logs.call_args
        assert call_kwargs.kwargs["from_block"] == 50_000
        assert call_kwargs.kwargs["to_block"] == 100_000


class TestGetTransformationsTo:
    """Test contract.get_transformations_to reverse event query."""

    def test_returns_event_tuples(self):
        from swarm_provenance_mcp.chain.contract import DataProvenanceContract

        contract = DataProvenanceContract.__new__(DataProvenanceContract)
        contract._web3 = MagicMock()
        contract._web3.eth.block_number = 10000
        contract._contract = MagicMock()

        mock_event = MagicMock()
        mock_event.args.originalDataHash = b'\xab' * 32
        mock_event.args.newDataHash = b'\xcd' * 32
        mock_event.args.transformation = "Anonymized"

        contract._contract.events.DataTransformed.get_logs.return_value = [mock_event]

        results = contract.get_transformations_to("cd" * 32)

        assert len(results) == 1
        orig, new, desc = results[0]
        assert orig == b'\xab' * 32
        assert new == b'\xcd' * 32
        assert desc == "Anonymized"

        # Verify filter used newDataHash, not originalDataHash
        call_kwargs = contract._contract.events.DataTransformed.get_logs.call_args
        assert "newDataHash" in call_kwargs.kwargs["argument_filters"]

    def test_empty_events(self):
        from swarm_provenance_mcp.chain.contract import DataProvenanceContract

        contract = DataProvenanceContract.__new__(DataProvenanceContract)
        contract._web3 = MagicMock()
        contract._web3.eth.block_number = 10000
        contract._contract = MagicMock()
        contract._contract.events.DataTransformed.get_logs.return_value = []

        results = contract.get_transformations_to("cd" * 32)
        assert results == []

    def test_lookback_blocks_calculation(self):
        from swarm_provenance_mcp.chain.contract import DataProvenanceContract

        contract = DataProvenanceContract.__new__(DataProvenanceContract)
        contract._web3 = MagicMock()
        contract._web3.eth.block_number = 100
        contract._contract = MagicMock()
        contract._contract.events.DataTransformed.get_logs.return_value = []

        contract.get_transformations_to("cd" * 32, lookback_blocks=5000)

        call_kwargs = contract._contract.events.DataTransformed.get_logs.call_args
        assert call_kwargs.kwargs["from_block"] == 0
        assert call_kwargs.kwargs["to_block"] == 100

    def test_default_lookback_is_50000(self):
        """Default lookback should be 50,000 blocks (~28h on Base)."""
        from swarm_provenance_mcp.chain.contract import DataProvenanceContract

        contract = DataProvenanceContract.__new__(DataProvenanceContract)
        contract._web3 = MagicMock()
        contract._web3.eth.block_number = 100_000
        contract._contract = MagicMock()
        contract._contract.events.DataTransformed.get_logs.return_value = []

        contract.get_transformations_to("cd" * 32)

        call_kwargs = contract._contract.events.DataTransformed.get_logs.call_args
        assert call_kwargs.kwargs["from_block"] == 50_000
        assert call_kwargs.kwargs["to_block"] == 100_000


class TestProvenanceChainEventTraversal:
    """Test that get_provenance_chain uses events to follow transformation links."""

    @pytest.fixture
    def server(self):
        return create_server()

    async def test_chain_follows_events(self, server):
        """get_provenance_chain should return multi-entry chain via event traversal."""
        mock_rec1 = MagicMock()
        mock_rec1.data_hash = "aa" * 32
        mock_rec1.owner = "0xOWNER"
        mock_rec1.timestamp = 1700000000
        mock_rec1.data_type = "original"
        mock_rec1.status = MagicMock(value=0)
        mock_rec1.transformations = [MagicMock(description="Step 1", new_data_hash="bb" * 32)]

        mock_rec2 = MagicMock()
        mock_rec2.data_hash = "bb" * 32
        mock_rec2.owner = "0xOWNER"
        mock_rec2.timestamp = 1700001000
        mock_rec2.data_type = "derived"
        mock_rec2.status = MagicMock(value=0)
        mock_rec2.transformations = []

        mock_client = MagicMock()
        mock_client.get_provenance_chain.return_value = [mock_rec1, mock_rec2]

        with patch('swarm_provenance_mcp.server.CHAIN_AVAILABLE', True), \
             patch('swarm_provenance_mcp.server.chain_client', mock_client):
            result = await call_tool_directly(server, "get_provenance_chain", {
                "swarm_hash": "aa" * 32,
            })

        assert not result.isError
        text = result.content[0].text
        assert "2 entries" in text
        assert "aa" * 32 in text
        assert "bb" * 32 in text

    async def test_chain_event_fallback_on_error(self):
        """When event query fails, should fall back to record transformations."""
        from swarm_provenance_mcp.chain.client import ChainClient
        from swarm_provenance_mcp.chain.exceptions import DataNotRegisteredError
        from swarm_provenance_mcp.chain.models import (
            ChainProvenanceRecord, ChainTransformation, DataStatusEnum,
        )

        client = ChainClient.__new__(ChainClient)
        client._contract = MagicMock()
        # Event query fails
        client._contract.get_transformations_from.side_effect = Exception("RPC error")
        client._contract.get_transformations_to.side_effect = Exception("RPC error")

        record = ChainProvenanceRecord(
            data_hash="aa" * 32,
            owner="0xOWNER",
            timestamp=1700000000,
            data_type="test",
            status=DataStatusEnum(0),
            transformations=[
                ChainTransformation(description="Step 1", new_data_hash="bb" * 32),
            ],
            accessors=[],
        )

        def get_side_effect(h):
            if h == "aa" * 32:
                return record
            raise DataNotRegisteredError("not found", data_hash=h)

        client.get = MagicMock(side_effect=get_side_effect)

        chain = client.get_provenance_chain("aa" * 32)
        # Should still include the first record (bb*32 not found but attempted)
        assert len(chain) == 1
        assert chain[0].data_hash == "aa" * 32

    async def test_chain_from_leaf_walks_backward(self):
        """Querying a leaf node should walk backward to find parents."""
        from swarm_provenance_mcp.chain.client import ChainClient
        from swarm_provenance_mcp.chain.exceptions import DataNotRegisteredError
        from swarm_provenance_mcp.chain.models import (
            ChainProvenanceRecord, ChainTransformation, DataStatusEnum,
        )

        client = ChainClient.__new__(ChainClient)
        client._contract = MagicMock()

        parent_hash = "aa" * 32
        leaf_hash = "bb" * 32

        parent_record = ChainProvenanceRecord(
            data_hash=parent_hash,
            owner="0xOWNER",
            timestamp=1700000000,
            data_type="original",
            status=DataStatusEnum(0),
            transformations=[],
            accessors=[],
        )
        leaf_record = ChainProvenanceRecord(
            data_hash=leaf_hash,
            owner="0xOWNER",
            timestamp=1700001000,
            data_type="derived",
            status=DataStatusEnum(0),
            transformations=[],
            accessors=[],
        )

        def get_side_effect(h):
            if h == parent_hash:
                return parent_record
            if h == leaf_hash:
                return leaf_record
            raise DataNotRegisteredError("not found", data_hash=h)

        client.get = MagicMock(side_effect=get_side_effect)

        # Forward: leaf has no children
        def fwd_side_effect(h):
            return []
        client._contract.get_transformations_from = MagicMock(
            side_effect=fwd_side_effect
        )

        # Reverse: leaf was produced from parent
        def rev_side_effect(h):
            if h == leaf_hash:
                return [(bytes.fromhex(parent_hash), bytes.fromhex(leaf_hash), "Step 1")]
            return []
        client._contract.get_transformations_to = MagicMock(
            side_effect=rev_side_effect
        )

        chain = client.get_provenance_chain(leaf_hash)
        hashes = [r.data_hash for r in chain]
        assert len(chain) == 2
        assert leaf_hash in hashes
        assert parent_hash in hashes


class TestProvenanceChainWorkflowPrompt:
    """Test the provenance-chain-workflow prompt."""

    @pytest.fixture
    def server(self):
        return create_server()

    async def test_chain_workflow_prompt_basic(self, server):
        """provenance-chain-workflow prompt should return anchoring steps."""
        handler = None
        for h in server.request_handlers.values():
            if hasattr(h, '__name__') and 'get_prompt' in str(h):
                handler = h
                break

        assert handler is not None, "get_prompt handler not registered"
        from mcp.types import GetPromptRequest
        result = await handler(GetPromptRequest(
            method="prompts/get",
            params={"name": "provenance-chain-workflow", "arguments": {"data": "test data"}}
        ))
        inner = result.root if hasattr(result, 'root') else result
        text = inner.messages[0].content.text

        assert "health_check" in text
        assert "chain_balance" in text
        assert "anchor_hash" in text
        assert "verify_hash" in text
        # Should NOT include transform steps without transform_description
        assert "record_transform" not in text

    async def test_chain_workflow_prompt_with_transform(self, server):
        """provenance-chain-workflow with transform_description should include transform steps."""
        handler = None
        for h in server.request_handlers.values():
            if hasattr(h, '__name__') and 'get_prompt' in str(h):
                handler = h
                break

        from mcp.types import GetPromptRequest
        result = await handler(GetPromptRequest(
            method="prompts/get",
            params={
                "name": "provenance-chain-workflow",
                "arguments": {
                    "data": "original data",
                    "transform_description": "Anonymized PII",
                },
            }
        ))
        inner = result.root if hasattr(result, 'root') else result
        text = inner.messages[0].content.text

        assert "record_transform" in text
        assert "Anonymized PII" in text
        assert "Do NOT call anchor_hash on the new hash" in text
        assert "get_provenance_chain" in text

    async def test_chain_workflow_prompt_has_arguments(self, server):
        """provenance-chain-workflow should have data (required) and transform_description (optional)."""
        handler = None
        for h in server.request_handlers.values():
            if hasattr(h, '__name__') and 'list_prompts' in str(h):
                handler = h
                break

        from mcp.types import ListPromptsRequest
        result = await handler(ListPromptsRequest(method="prompts/list"))
        inner = result.root if hasattr(result, 'root') else result
        prompts = inner.prompts if hasattr(inner, 'prompts') else inner

        chain_prompt = next(p for p in prompts if p.name == "provenance-chain-workflow")
        arg_names = {a.name for a in chain_prompt.arguments}
        assert arg_names == {"data", "transform_description"}
        data_arg = next(a for a in chain_prompt.arguments if a.name == "data")
        assert data_arg.required is True
        transform_arg = next(a for a in chain_prompt.arguments if a.name == "transform_description")
        assert transform_arg.required is False


class TestMCPResources:
    """Test MCP resource handlers."""

    @pytest.fixture
    def server(self):
        return create_server()

    async def test_list_resources_includes_skills(self, server):
        """list_resources should include the provenance://skills resource."""
        handler = None
        for h in server.request_handlers.values():
            if hasattr(h, '__name__') and 'list_resources' in str(h):
                handler = h
                break

        assert handler is not None, "list_resources handler not registered"
        from mcp.types import ListResourcesRequest
        result = await handler(ListResourcesRequest(method="resources/list"))
        inner = result.root if hasattr(result, 'root') else result
        resources = inner.resources if hasattr(inner, 'resources') else inner

        uris = [str(r.uri) for r in resources]
        assert "provenance://skills" in uris

        skills_resource = next(r for r in resources if str(r.uri) == "provenance://skills")
        assert skills_resource.mimeType == "text/markdown"
        assert "provenance" in skills_resource.description.lower()

    async def test_read_skills_resource_returns_content(self, server):
        """read_resource for provenance://skills should return SKILLS.md content."""
        handler = None
        for h in server.request_handlers.values():
            if hasattr(h, '__name__') and 'read_resource' in str(h):
                handler = h
                break

        assert handler is not None, "read_resource handler not registered"
        from pydantic import AnyUrl
        from mcp.types import ReadResourceRequest
        result = await handler(ReadResourceRequest(
            method="resources/read",
            params={"uri": "provenance://skills"},
        ))
        inner = result.root if hasattr(result, 'root') else result
        contents = inner if isinstance(inner, list) else inner.contents if hasattr(inner, 'contents') else [inner]

        assert len(contents) >= 1
        text = contents[0].content if hasattr(contents[0], 'content') else str(contents[0])
        assert len(text) > 100
        # Check for key sections from SKILLS.md
        assert "Critical Rules" in text or "critical" in text.lower()
        assert "Workflow" in text or "workflow" in text.lower()

    async def test_read_unknown_resource_raises(self, server):
        """read_resource for unknown URI should raise ValueError."""
        handler = None
        for h in server.request_handlers.values():
            if hasattr(h, '__name__') and 'read_resource' in str(h):
                handler = h
                break

        assert handler is not None
        from mcp.types import ReadResourceRequest
        with pytest.raises((ValueError, Exception)):
            await handler(ReadResourceRequest(
                method="resources/read",
                params={"uri": "provenance://unknown"},
            ))
