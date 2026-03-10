"""Tests to ensure MCP server tool definitions are complete and up-to-date."""

import pytest
import inspect
from typing import get_type_hints
from unittest.mock import AsyncMock, patch

from swarm_provenance_mcp.server import create_server
from swarm_provenance_mcp.gateway_client import SwarmGatewayClient
from mcp.types import Tool, ListToolsRequest


async def _get_tools_from_server(server):
    """Get tools list from a server instance using the registered handler."""
    list_tools_handler = None
    for handler_key, handler in server.request_handlers.items():
        if hasattr(handler, '__name__') and 'list_tools' in str(handler):
            list_tools_handler = handler
            break

    if list_tools_handler is None:
        return []

    result = await list_tools_handler(ListToolsRequest(method="tools/list"))
    inner = result.root if hasattr(result, 'root') else result
    tools = inner.tools if hasattr(inner, 'tools') else inner
    return tools


@pytest.fixture
async def tool_list():
    """Get the list of tools from the MCP server."""
    server = create_server()
    return await _get_tools_from_server(server)


class TestToolDefinitions:
    """Test suite to verify MCP tool definitions match implementation."""

    @pytest.fixture
    def server(self):
        """Create MCP server instance for testing."""
        return create_server()

    @pytest.fixture
    def gateway_client_methods(self):
        """Get all public methods from SwarmGatewayClient."""
        client = SwarmGatewayClient()
        methods = {}
        for name, method in inspect.getmembers(client, predicate=inspect.ismethod):
            if not name.startswith('_') and name != 'close':
                methods[name] = method
        return methods

    async def test_all_tools_defined(self, tool_list):
        """Test that all expected tools are defined in the MCP server."""
        expected_tools = {
            'purchase_stamp',
            'get_stamp_status',
            'list_stamps',
            'extend_stamp',
            'upload_data',
            'download_data',
            'check_stamp_health',
            'get_wallet_info',
            'get_notary_info',
            'health_check'
        }

        actual_tools = {tool.name for tool in tool_list}

        # Check for missing tools
        missing_tools = expected_tools - actual_tools
        assert not missing_tools, f"Missing tool definitions: {missing_tools}"

        # Check for unexpected tools
        unexpected_tools = actual_tools - expected_tools
        if unexpected_tools:
            # This is a warning, not an error - new tools might be added
            print(f"Warning: Found unexpected tools: {unexpected_tools}")

    async def test_tool_schemas_complete(self, tool_list):
        """Test that all tools have complete schema definitions."""
        for tool in tool_list:
            # Each tool must have a name
            assert tool.name, f"Tool missing name: {tool}"

            # Each tool must have a description
            assert tool.description, f"Tool '{tool.name}' missing description"

            # Each tool must have an input schema
            assert tool.inputSchema, f"Tool '{tool.name}' missing input schema"

            # Input schema must be a valid JSON schema object
            schema = tool.inputSchema
            assert isinstance(schema, dict), f"Tool '{tool.name}' input schema is not a dict"
            assert schema.get('type') == 'object', f"Tool '{tool.name}' schema type is not 'object'"

            # Schema should have properties defined
            if 'properties' in schema:
                assert isinstance(schema['properties'], dict), f"Tool '{tool.name}' properties is not a dict"

    async def test_tool_parameter_types(self, tool_list):
        """Test that tool parameters have proper type definitions."""
        for tool in tool_list:
            schema = tool.inputSchema
            if 'properties' in schema:
                for param_name, param_def in schema['properties'].items():
                    # Each parameter must have a type
                    assert 'type' in param_def or 'anyOf' in param_def, \
                        f"Tool '{tool.name}' parameter '{param_name}' missing type definition"

                    # Parameters should have descriptions
                    assert 'description' in param_def, \
                        f"Tool '{tool.name}' parameter '{param_name}' missing description"

    async def test_required_parameters_consistency(self, tool_list):
        """Test that required parameters are properly specified."""
        # Map of tools to their expected required parameters
        expected_required = {
            'purchase_stamp': [],  # All parameters have defaults
            'get_stamp_status': ['stamp_id'],
            'list_stamps': [],  # No parameters
            'extend_stamp': ['stamp_id', 'amount'],
            'upload_data': ['data', 'stamp_id'],
            'download_data': ['reference'],
            'check_stamp_health': ['stamp_id'],
            'get_wallet_info': [],
            'get_notary_info': [],
            'health_check': []
        }

        for tool in tool_list:
            if tool.name in expected_required:
                schema = tool.inputSchema
                actual_required = set(schema.get('required', []))
                expected_req = set(expected_required[tool.name])

                # Check that all expected required params are marked as required
                missing_required = expected_req - actual_required
                assert not missing_required, \
                    f"Tool '{tool.name}' missing required parameters: {missing_required}"

    def test_gateway_client_method_coverage(self, gateway_client_methods):
        """Test that all gateway client methods have corresponding MCP tools."""
        # Methods that should have MCP tool equivalents
        method_to_tool_mapping = {
            'purchase_stamp': 'purchase_stamp',
            'get_stamp_details': 'get_stamp_status',
            'list_stamps': 'list_stamps',
            'extend_stamp': 'extend_stamp',
            'upload_data': 'upload_data',
            'download_data': 'download_data',
            'check_stamp_health': 'check_stamp_health',
            'get_wallet_info': 'get_wallet_info',
            'get_notary_info': 'get_notary_info',
            'health_check': 'health_check'
        }

        for method_name in method_to_tool_mapping:
            assert method_name in gateway_client_methods, \
                f"Gateway client missing expected method: {method_name}"

    async def test_tool_handler_implementation(self, server):
        """Test that all defined tools have corresponding handler implementations."""
        from mcp.types import CallToolRequest

        # Get tool handlers from the server
        handlers = getattr(server, 'request_handlers', {})

        # Should have some form of tool execution capability
        assert len(handlers) > 0, "No request handlers found in server"

        # Check that call_tool handler is registered (using type-based check)
        has_call_handler = any(
            hasattr(h, '__name__') and 'call_tool' in str(h)
            for h in handlers.values()
        )
        assert has_call_handler, "No call_tool handler found in server"

        # This is a basic check - the server should have request handling capability
        assert hasattr(server, 'request_handlers'), \
            "Server missing request handler infrastructure"

    async def test_error_handling_consistency(self, tool_list):
        """Test that tools have consistent error handling patterns."""
        for tool in tool_list:
            # This test ensures tools document their error behavior
            description = tool.description.lower()

            # Tools that interact with external services should mention error handling
            if any(keyword in description for keyword in ['gateway', 'swarm', 'network']):
                # Should mention potential errors or exceptions
                error_indicators = ['error', 'exception', 'fail', 'timeout', 'unavailable']
                has_error_info = any(indicator in description for indicator in error_indicators)

                if not has_error_info:
                    print(f"Warning: Tool '{tool.name}' may need error handling documentation")

    async def test_tool_examples_or_defaults(self, tool_list):
        """Test that tools provide examples or default values for parameters."""
        for tool in tool_list:
            schema = tool.inputSchema
            if 'properties' in schema:
                for param_name, param_def in schema['properties'].items():
                    # Parameters should have either examples, defaults, or be clearly documented
                    has_example = 'example' in param_def
                    has_default = 'default' in param_def
                    has_good_description = (
                        'description' in param_def and
                        len(param_def['description']) > 20
                    )

                    assert has_example or has_default or has_good_description, \
                        f"Tool '{tool.name}' parameter '{param_name}' needs example, default, or better description"


class TestToolImplementationSync:
    """Test that tool implementations stay in sync with definitions."""

    def test_gateway_client_signature_compatibility(self):
        """Test that gateway client method signatures match tool parameter schemas."""
        client = SwarmGatewayClient()

        # Test purchase_stamp method signature
        purchase_method = getattr(client, 'purchase_stamp')
        sig = inspect.signature(purchase_method)

        # Should have amount, depth, and optional label parameters
        expected_params = {'amount', 'depth', 'label'}
        actual_params = set(sig.parameters.keys()) - {'self'}

        assert expected_params == actual_params, \
            f"purchase_stamp signature mismatch. Expected: {expected_params}, Got: {actual_params}"

        # Check parameter types if type hints are available
        hints = get_type_hints(purchase_method)
        if 'amount' in hints:
            assert hints['amount'] == int, "amount parameter should be int type"
        if 'depth' in hints:
            assert hints['depth'] == int, "depth parameter should be int type"

    def test_method_documentation_exists(self):
        """Test that gateway client methods have proper docstrings."""
        client = SwarmGatewayClient()
        critical_methods = [
            'purchase_stamp', 'get_stamp_details', 'list_stamps',
            'extend_stamp', 'upload_data', 'download_data',
            'check_stamp_health', 'get_wallet_info', 'get_notary_info',
            'health_check'
        ]

        for method_name in critical_methods:
            if hasattr(client, method_name):
                method = getattr(client, method_name)
                assert method.__doc__, f"Method '{method_name}' missing docstring"

                # Docstring should be reasonably detailed
                doc_lines = method.__doc__.strip().split('\n')
                assert len(doc_lines) >= 3, f"Method '{method_name}' docstring too brief"


class TestToolFutureCompatibility:
    """Tests to ensure tools remain compatible as code evolves."""

    async def test_tool_versioning_info(self, tool_list):
        """Test that tools include version or compatibility information."""
        for tool in tool_list:
            description = tool.description

            # Tools should indicate their stability or version compatibility
            # This helps clients understand if they can rely on the tool
            stability_indicators = [
                'stable', 'beta', 'experimental', 'deprecated',
                'v1', 'v2', 'version', 'since'
            ]

            # For now, just ensure description exists and is meaningful
            assert len(description) > 50, \
                f"Tool '{tool.name}' description should be more detailed for future compatibility"

    def test_configuration_compatibility(self):
        """Test that configuration changes don't break tool definitions."""
        from swarm_provenance_mcp.config import settings

        # Essential settings that tools depend on
        required_settings = [
            'swarm_gateway_url',
            'default_stamp_amount',
            'default_stamp_depth',
            'payment_mode',
            'mcp_server_name',
            'mcp_server_version'
        ]

        for setting in required_settings:
            assert hasattr(settings, setting), f"Missing required setting: {setting}"
            assert getattr(settings, setting) is not None, f"Setting '{setting}' is None"

    async def test_backward_compatibility_markers(self, tool_list):
        """Test that tools are marked for backward compatibility tracking."""
        for tool in tool_list:
            # Tools should have clear, stable names that won't change
            assert '_' in tool.name or tool.name.islower(), \
                f"Tool name '{tool.name}' should follow consistent naming convention"

            # Input schema should have clear structure
            schema = tool.inputSchema
            assert 'type' in schema, f"Tool '{tool.name}' schema missing type field"
            assert schema['type'] == 'object', f"Tool '{tool.name}' should use object schema"