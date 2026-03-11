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
        handle_health_check
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
        "health_check": handle_health_check
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
                        "utilization": 0.1
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
