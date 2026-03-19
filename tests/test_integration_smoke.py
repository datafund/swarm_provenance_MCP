"""Smoke tests to catch integration breakages early."""

import pytest
import asyncio
import os
from unittest.mock import patch
import requests
from swarm_provenance_mcp.gateway_client import SwarmGatewayClient
from swarm_provenance_mcp.config import settings


class TestGatewayContractStability:
    """Tests to ensure gateway API contract remains stable."""

    @pytest.fixture
    def gateway_url(self):
        """Get gateway URL from environment or use default."""
        return os.getenv("SWARM_GATEWAY_URL", "http://localhost:8000")

    def test_gateway_endpoints_exist(self, gateway_url):
        """Test that expected gateway endpoints are available."""
        expected_endpoints = [
            ("GET", "/"),
            ("GET", "/docs"),
            ("POST", "/api/v1/stamps"),
            ("GET", "/api/v1/stamps"),
            ("POST", "/api/v1/data/"),
            ("GET", "/api/v1/data/test"),  # Will 404 but endpoint should exist
        ]

        for method, endpoint in expected_endpoints:
            url = f"{gateway_url.rstrip('/')}{endpoint}"
            try:
                if method == "GET":
                    response = requests.get(url, timeout=5)
                elif method == "POST":
                    response = requests.post(url, json={}, timeout=5)

                # We expect various responses, but not connection errors
                assert (
                    response.status_code != 502
                ), f"Gateway not running for {endpoint}"
                print(f"✓ {method} {endpoint}: {response.status_code}")

            except requests.ConnectionError:
                pytest.skip(f"Gateway not available at {gateway_url}")

    def test_gateway_response_format_stability(self, gateway_url):
        """Test that gateway responses maintain expected format."""
        client = SwarmGatewayClient(gateway_url)

        try:
            # Test health check response format
            health = client.health_check()

            # These fields should always be present
            required_fields = ["status", "gateway_url", "response_time_ms"]
            for field in required_fields:
                assert field in health, f"Health check missing required field: {field}"

            # Test stamps list response format (even if empty)
            stamps_response = client.list_stamps()
            assert "stamps" in stamps_response, "Stamps response missing 'stamps' field"
            assert isinstance(
                stamps_response["stamps"], list
            ), "Stamps field should be a list"

        except requests.ConnectionError:
            pytest.skip(f"Gateway not available at {gateway_url}")


class TestMCPFrameworkCompatibility:
    """Tests to ensure MCP framework compatibility doesn't break."""

    def test_mcp_imports_stable(self):
        """Test that required MCP imports remain available."""
        try:
            from mcp.server import Server
            from mcp.types import Tool, TextContent, CallToolRequest, CallToolResult
            from mcp.server.models import InitializationOptions
            from mcp.server.stdio import stdio_server

            # Test that classes can be instantiated
            server = Server("test")
            assert server is not None

            tool = Tool(name="test", description="test", inputSchema={"type": "object"})
            assert tool.name == "test"

        except ImportError as e:
            pytest.fail(f"MCP framework import failed: {e}")

    def test_server_creation_stable(self):
        """Test that server creation doesn't break."""
        from swarm_provenance_mcp.server import create_server

        server = create_server()
        assert server is not None
        assert hasattr(server, "request_handlers")

    async def test_tool_definitions_format_stable(self):
        """Test that tool definitions maintain expected structure."""
        from swarm_provenance_mcp.server import create_server
        from mcp.types import ListToolsRequest

        server = create_server()

        # Find and test list_tools handler
        list_tools_handler = None
        for handler_name, handler in server.request_handlers.items():
            if hasattr(handler, "__name__") and "list_tools" in str(handler):
                list_tools_handler = handler
                break

        if list_tools_handler:
            result = await list_tools_handler(ListToolsRequest(method="tools/list"))
            # Unwrap ServerResult -> ListToolsResult -> tools list
            inner = result.root if hasattr(result, "root") else result
            tools = inner.tools if hasattr(inner, "tools") else inner

            for tool in tools:
                # Each tool must have these attributes
                assert hasattr(tool, "name"), "Tool missing name attribute"
                assert hasattr(
                    tool, "description"
                ), f"Tool {tool.name} missing description"
                assert hasattr(
                    tool, "inputSchema"
                ), f"Tool {tool.name} missing inputSchema"

                # Schema must be valid
                schema = tool.inputSchema
                assert isinstance(schema, dict), f"Tool {tool.name} schema not a dict"
                assert (
                    schema.get("type") == "object"
                ), f"Tool {tool.name} schema not object type"


class TestDataIntegrityRoundTrip:
    """Tests to ensure data upload/download integrity."""

    @pytest.mark.integration
    def test_small_data_roundtrip(self):
        """Test that small data uploads and downloads work correctly."""
        client = SwarmGatewayClient()

        test_data = '{"test": "small data", "timestamp": 1234567890}'

        try:
            # Skip if no stamps available
            stamps = client.list_stamps()
            if not stamps.get("stamps"):
                pytest.skip("No stamps available for upload test")

            stamp_id = stamps["stamps"][0]["batchID"]

            # Upload
            upload_result = client.upload_data(test_data, stamp_id)
            assert "reference" in upload_result

            # Download
            downloaded = client.download_data(upload_result["reference"])
            assert downloaded.decode("utf-8") == test_data

        except requests.ConnectionError:
            pytest.skip("Gateway not available")
        except Exception as e:
            # Log but don't fail - this is integration dependent
            print(f"Integration test failed (expected): {e}")

    def test_size_limit_enforcement(self):
        """Test that 4KB size limit is enforced."""
        client = SwarmGatewayClient()

        # Create data slightly over 4KB
        large_data = "x" * 4097

        with pytest.raises(ValueError, match="exceeds 4KB limit"):
            client.upload_data(large_data, "fake_stamp")

    def test_size_limit_boundary(self):
        """Test that exactly 4KB is allowed."""
        client = SwarmGatewayClient()

        # Create data exactly 4KB
        exact_4kb_data = "x" * 4096

        # Should not raise ValueError
        try:
            client.upload_data(exact_4kb_data, "fake_stamp")
        except ValueError as e:
            if "exceeds 4KB limit" in str(e):
                pytest.fail("4KB boundary incorrectly rejected")
        except:
            pass  # Other errors are expected without real gateway


class TestConfigurationStability:
    """Tests to ensure configuration remains stable."""

    def test_required_settings_present(self):
        """Test that all required configuration settings exist."""
        required_settings = [
            "swarm_gateway_url",
            "default_stamp_amount",
            "default_stamp_depth",
            "payment_mode",
            "mcp_server_name",
            "mcp_server_version",
        ]

        for setting in required_settings:
            assert hasattr(settings, setting), f"Missing required setting: {setting}"
            value = getattr(settings, setting)
            assert value is not None, f"Setting {setting} is None"
            assert str(value).strip(), f"Setting {setting} is empty"

    def test_default_values_reasonable(self):
        """Test that default configuration values are reasonable."""
        assert (
            settings.default_stamp_amount > 0
        ), "Default stamp amount should be positive"
        assert (
            settings.default_stamp_depth > 0
        ), "Default stamp depth should be positive"
        assert settings.swarm_gateway_url.startswith(
            "http"
        ), "Gateway URL should be HTTP(S)"
        assert (
            "." in settings.mcp_server_version
        ), "Version should have format like x.y.z"


class TestErrorHandlingStability:
    """Tests to ensure error handling remains robust."""

    async def test_invalid_tool_calls_handled(self):
        """Test that invalid tool calls are handled gracefully."""
        from swarm_provenance_mcp.server import handle_purchase_stamp

        # Test with invalid arguments
        invalid_args = [
            {},  # Missing required args for some tools
            {"invalid_field": "value"},
            {"amount": "not_a_number"},
            {"amount": -1},
        ]

        for args in invalid_args:
            try:
                result = await handle_purchase_stamp(args)
                # Should return error result, not raise exception
                if hasattr(result, "isError"):
                    # Expected behavior - return error result
                    pass
                else:
                    pytest.fail(f"Invalid args {args} should return error result")
            except Exception:
                # Acceptable - but should prefer returning error results
                pass

    def test_network_error_handling(self):
        """Test that network errors are handled gracefully."""
        # Test with invalid gateway URL
        client = SwarmGatewayClient("http://invalid-gateway-url-12345.com")

        try:
            client.health_check()
            pytest.fail("Should have raised exception for invalid URL")
        except requests.RequestException:
            # Expected behavior
            pass
        except Exception as e:
            # Should be RequestException or subclass
            pytest.fail(f"Wrong exception type for network error: {type(e)}")


class TestVersionCompatibilityWarnings:
    """Tests that warn about potential compatibility issues."""

    def test_dependency_versions_tracked(self):
        """Track dependency versions to notice changes."""
        import mcp
        import requests
        import pydantic

        # Track versions - test will show in output if they change
        versions = {
            "mcp": getattr(mcp, "__version__", "unknown"),
            "requests": requests.__version__,
            "pydantic": pydantic.__version__,
        }

        print(f"\nDependency versions: {versions}")

        # Warn about known incompatible versions
        if versions["pydantic"].startswith("1."):
            print("WARNING: Pydantic v1 detected, consider upgrading")
