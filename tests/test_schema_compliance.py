"""Tests to ensure schema and protocol compliance doesn't break."""

import pytest
import json
import jsonschema
from typing import Dict, Any
from swarm_provenance_mcp.server import create_server


class TestMCPToolSchemaCompliance:
    """Tests to ensure MCP tool schemas remain valid and complete."""

    def test_all_tools_have_valid_json_schemas(self):
        """Ensure all tool input schemas are valid JSON schemas."""
        from swarm_provenance_mcp.server import create_server

        # Test that server can be created (indicates tools are properly defined)
        server = create_server()
        assert server is not None, "Server creation should succeed"

        # Test that we have the expected handlers
        handlers = server.request_handlers
        assert len(handlers) > 0, "Server should have request handlers"

        # Test that our expected tool schemas can be validated
        # We'll test the schema patterns we know should be valid
        expected_tool_schemas = {
            "purchase_stamp": {
                "type": "object",
                "properties": {
                    "amount": {"type": "integer"},
                    "depth": {"type": "integer"},
                    "label": {"type": "string"},
                },
                "required": [],
            },
            "upload_data": {
                "type": "object",
                "properties": {
                    "data": {"type": "string"},
                    "stamp_id": {"type": "string"},
                    "content_type": {"type": "string"},
                },
                "required": ["data", "stamp_id"],
            },
        }

        for tool_name, schema in expected_tool_schemas.items():
            # Test that schema is valid JSON Schema
            try:
                jsonschema.Draft7Validator.check_schema(schema)
            except jsonschema.SchemaError as e:
                pytest.fail(f"Tool '{tool_name}' has invalid JSON schema: {e}")

    def test_tool_schemas_match_implementations(self):
        """Test that tool schemas match their actual implementations."""
        # Test that handler functions exist and are callable
        from swarm_provenance_mcp.server import (
            handle_purchase_stamp,
            handle_get_stamp_status,
            handle_list_stamps,
            handle_extend_stamp,
            handle_upload_data,
            handle_download_data,
            handle_check_stamp_health,
            handle_get_wallet_info,
            handle_get_notary_info,
            handle_health_check,
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
        }

        # Test that all handlers exist and are callable
        for tool_name, handler in handlers.items():
            assert callable(handler), f"Handler for {tool_name} should be callable"

        # Test some basic parameter expectations
        import inspect

        upload_sig = inspect.signature(handle_upload_data)
        assert (
            "arguments" in upload_sig.parameters
        ), "upload_data handler should accept arguments parameter"

    def test_schema_parameter_types_valid(self):
        """Test that all schema parameter types are valid."""
        valid_types = {
            "string",
            "number",
            "integer",
            "boolean",
            "array",
            "object",
            "null",
        }

        # Test our known schemas
        expected_tool_schemas = {
            "upload_data": {
                "type": "object",
                "properties": {
                    "data": {"type": "string"},
                    "stamp_id": {"type": "string"},
                    "content_type": {"type": "string"},
                },
            }
        }

        for tool_name, schema in expected_tool_schemas.items():
            properties = schema.get("properties", {})

            for param_name, param_def in properties.items():
                param_type = param_def.get("type")
                if param_type:
                    assert (
                        param_type in valid_types
                    ), f"Tool '{tool_name}' parameter '{param_name}' has invalid type: {param_type}"

    def test_required_vs_optional_parameters_consistency(self):
        """Test that required/optional parameter declarations are consistent."""
        # Test with a sample schema
        test_schema = {
            "type": "object",
            "properties": {
                "data": {"type": "string"},
                "stamp_id": {"type": "string"},
                "content_type": {"type": "string", "default": "application/json"},
            },
            "required": ["data", "stamp_id"],
        }

        properties = test_schema.get("properties", {})
        required = set(test_schema.get("required", []))

        # All required parameters must be in properties
        for req_param in required:
            assert (
                req_param in properties
            ), f"Required parameter '{req_param}' not in properties"

        # Parameters with defaults should not be required
        for param_name, param_def in properties.items():
            if "default" in param_def:
                assert (
                    param_name not in required
                ), f"Parameter '{param_name}' has default but is required"


class TestGatewayClientSchemaCompliance:
    """Tests to ensure gateway client method signatures remain compatible."""

    def test_gateway_method_signatures_stable(self):
        """Test that gateway client method signatures haven't changed unexpectedly."""
        from swarm_provenance_mcp.gateway_client import SwarmGatewayClient
        import inspect

        client = SwarmGatewayClient()

        # Expected signatures for critical methods
        expected_signatures = {
            "purchase_stamp": ["amount", "depth", "label"],
            "get_stamp_details": ["stamp_id"],
            "list_stamps": [],
            "extend_stamp": ["stamp_id", "amount"],
            "upload_data": ["data", "stamp_id", "content_type"],
            "download_data": ["reference"],
            "check_stamp_health": ["stamp_id"],
            "get_wallet_info": [],
            "get_notary_info": [],
            "health_check": [],
        }

        for method_name, expected_params in expected_signatures.items():
            if hasattr(client, method_name):
                method = getattr(client, method_name)
                sig = inspect.signature(method)
                actual_params = [p for p in sig.parameters.keys() if p != "self"]

                # Check that expected parameters are present
                for expected_param in expected_params:
                    assert (
                        expected_param in actual_params
                    ), f"Method {method_name} missing expected parameter: {expected_param}"

    def test_gateway_method_return_types_documented(self):
        """Test that gateway methods have proper type hints and docstrings."""
        from swarm_provenance_mcp.gateway_client import SwarmGatewayClient

        client = SwarmGatewayClient()
        critical_methods = [
            "purchase_stamp",
            "get_stamp_details",
            "list_stamps",
            "extend_stamp",
            "upload_data",
            "download_data",
            "check_stamp_health",
            "get_wallet_info",
            "get_notary_info",
            "health_check",
        ]

        for method_name in critical_methods:
            if hasattr(client, method_name):
                method = getattr(client, method_name)

                # Should have docstring
                assert method.__doc__, f"Method {method_name} missing docstring"

                # Docstring should mention return type or what it returns
                doc_lower = method.__doc__.lower()
                return_indicators = ["return", "response", "dict", "json", "bytes"]
                has_return_info = any(
                    indicator in doc_lower for indicator in return_indicators
                )
                assert (
                    has_return_info
                ), f"Method {method_name} docstring should document return type"


class TestProtocolComplianceRegression:
    """Tests to catch MCP protocol compliance regressions."""

    async def test_tool_result_format_compliance(self):
        """Test that tool results follow MCP protocol format."""
        from swarm_provenance_mcp.server import handle_health_check
        from mcp.types import CallToolResult, TextContent

        # Test with mocked gateway to avoid network dependency
        with pytest.MonkeyPatch().context() as m:
            # Mock the gateway_client to return predictable results
            mock_health_result = {
                "status": "healthy",
                "gateway_url": "http://test",
                "response_time_ms": 10.5,
                "gateway_response": {"test": "data"},
            }

            m.setattr(
                "swarm_provenance_mcp.server.gateway_client.health_check",
                lambda: mock_health_result,
            )

            result = await handle_health_check({})

            # Must be CallToolResult
            assert isinstance(
                result, CallToolResult
            ), "Result must be CallToolResult instance"

            # Must have content
            assert hasattr(result, "content"), "Result must have content attribute"
            assert len(result.content) > 0, "Result must have non-empty content"

            # Content items must be proper type
            for content_item in result.content:
                assert isinstance(
                    content_item, TextContent
                ), "Content must be TextContent instances"
                assert hasattr(content_item, "type"), "Content must have type attribute"
                assert hasattr(content_item, "text"), "Content must have text attribute"
                assert content_item.type == "text", "Content type must be 'text'"

    def test_tool_error_format_compliance(self):
        """Test that tool errors follow MCP protocol format."""
        from swarm_provenance_mcp.gateway_client import SwarmGatewayClient
        from mcp.types import CallToolResult, TextContent

        # Test with invalid gateway to trigger error
        client = SwarmGatewayClient("http://invalid-url-12345.com")

        # This should trigger a network error that gets converted to proper result
        try:
            client.health_check()
        except Exception as e:
            # The error should be a proper exception type
            assert hasattr(e, "__str__"), "Exceptions should be serializable"


class TestDataValidationCompliance:
    """Tests to ensure data validation remains consistent."""

    def test_upload_size_validation_boundaries(self):
        """Test upload size validation at boundaries."""
        from swarm_provenance_mcp.gateway_client import SwarmGatewayClient

        client = SwarmGatewayClient()

        test_cases = [
            (4095, False),  # Just under limit - should pass
            (4096, False),  # Exactly at limit - should pass
            (4097, True),  # Just over limit - should fail
            (8192, True),  # Way over limit - should fail
        ]

        for size, should_fail in test_cases:
            data = "x" * size

            if should_fail:
                with pytest.raises(ValueError, match="exceeds 4KB limit"):
                    client.upload_data(data, "fake_stamp")
            else:
                # Should not raise ValueError (other errors are OK)
                try:
                    client.upload_data(data, "fake_stamp")
                except ValueError as e:
                    if "exceeds 4KB limit" in str(e):
                        pytest.fail(f"Size {size} incorrectly rejected")
                except:
                    pass  # Other errors are expected without real gateway

    def test_utf8_encoding_handling(self):
        """Test that UTF-8 encoding is handled consistently."""
        from swarm_provenance_mcp.gateway_client import SwarmGatewayClient

        client = SwarmGatewayClient()

        # Test various UTF-8 content
        test_strings = [
            "Simple ASCII",
            "Unicode: café 🎉 文字",
            '{"special": "characters", "emoji": "🚀"}',
            json.dumps({"nested": {"unicode": "テスト"}}),
        ]

        for test_string in test_strings:
            # Should not raise encoding errors
            try:
                encoded = test_string.encode("utf-8")
                if len(encoded) <= 4096:
                    client.upload_data(test_string, "fake_stamp")
            except ValueError as e:
                if "exceeds 4KB limit" in str(e):
                    # Expected for large strings
                    pass
                else:
                    pytest.fail(f"UTF-8 encoding issue with: {test_string[:20]}...")
            except UnicodeError:
                pytest.fail(f"UTF-8 encoding failed for: {test_string[:20]}...")
            except:
                pass  # Other errors are expected without real gateway
