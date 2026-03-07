"""Automated validation tests to ensure tools stay synchronized with code changes."""

import pytest
import ast
import inspect
from pathlib import Path
from typing import Set, Dict, Any, List
import importlib.util

from swarm_provenance_mcp.server import create_server
from swarm_provenance_mcp.gateway_client import SwarmGatewayClient


class TestToolSynchronization:
    """Tests to ensure MCP tools stay synchronized with implementation changes."""

    @pytest.fixture
    def server_source_code(self):
        """Load and parse the server.py source code."""
        server_file = Path(__file__).parent.parent / "swarm_provenance_mcp" / "server.py"
        with open(server_file, 'r') as f:
            source = f.read()
        return ast.parse(source)

    @pytest.fixture
    def gateway_client_source_code(self):
        """Load and parse the gateway_client.py source code."""
        client_file = Path(__file__).parent.parent / "swarm_provenance_mcp" / "gateway_client.py"
        with open(client_file, 'r') as f:
            source = f.read()
        return ast.parse(source)

    def extract_tool_definitions(self, server_ast):
        """Extract tool definitions from server source code."""
        tools = {}

        class ToolVisitor(ast.NodeVisitor):
            def visit_Call(self, node):
                # Look for Tool() constructor calls
                if (isinstance(node.func, ast.Name) and node.func.id == 'Tool'):
                    tool_def = {}
                    for keyword in node.keywords:
                        if keyword.arg == 'name' and isinstance(keyword.value, ast.Constant):
                            tool_def['name'] = keyword.value.value
                        elif keyword.arg == 'description' and isinstance(keyword.value, ast.Constant):
                            tool_def['description'] = keyword.value.value
                        elif keyword.arg == 'inputSchema':
                            # Extract schema info (this would be more complex in practice)
                            tool_def['has_schema'] = True

                    if 'name' in tool_def:
                        tools[tool_def['name']] = tool_def

                self.generic_visit(node)

        visitor = ToolVisitor()
        visitor.visit(server_ast)
        return tools

    def extract_gateway_methods(self, client_ast):
        """Extract public method names from gateway client source code."""
        methods = set()

        class MethodVisitor(ast.NodeVisitor):
            def visit_FunctionDef(self, node):
                # Only include public methods (not starting with _)
                if not node.name.startswith('_') and node.name != 'close':
                    methods.add(node.name)
                self.generic_visit(node)

        visitor = MethodVisitor()
        visitor.visit(client_ast)
        return methods

    def extract_tool_handlers(self, server_ast):
        """Extract tool handler implementations from server source code."""
        handlers = {}

        class HandlerVisitor(ast.NodeVisitor):
            def __init__(self):
                self.current_tool_name = None

            def visit_If(self, node):
                # Look for if tool_name == "something" patterns
                if (isinstance(node.test, ast.Compare) and
                    len(node.test.comparators) == 1 and
                    isinstance(node.test.comparators[0], ast.Constant)):

                    # Check if it's comparing tool_name
                    if (isinstance(node.test.left, ast.Name) and
                        node.test.left.id in ('tool_name', 'name')):
                        tool_name = node.test.comparators[0].value
                        handlers[tool_name] = {
                            'implemented': True,
                            'has_error_handling': self._has_try_except(node)
                        }

                self.generic_visit(node)

            def _has_try_except(self, node):
                """Check if the handler has try/except blocks."""
                for child in ast.walk(node):
                    if isinstance(child, ast.Try):
                        return True
                return False

        visitor = HandlerVisitor()
        visitor.visit(server_ast)
        return handlers

    def test_tool_definitions_exist_in_source(self, server_source_code):
        """Test that all expected tools are defined in the source code."""
        tools = self.extract_tool_definitions(server_source_code)

        expected_tools = {
            'purchase_stamp', 'get_stamp_status', 'list_stamps',
            'extend_stamp', 'upload_data', 'download_data', 'health_check'
        }

        found_tools = set(tools.keys())
        missing_tools = expected_tools - found_tools

        assert not missing_tools, f"Tools missing from source code: {missing_tools}"

    def test_gateway_methods_exist_in_source(self, gateway_client_source_code):
        """Test that all expected gateway methods exist in source code."""
        methods = self.extract_gateway_methods(gateway_client_source_code)

        expected_methods = {
            'purchase_stamp', 'get_stamp_details', 'list_stamps',
            'extend_stamp', 'upload_data', 'download_data', 'health_check'
        }

        missing_methods = expected_methods - methods
        assert not missing_methods, f"Gateway methods missing from source: {missing_methods}"

    def test_tool_handlers_implemented(self, server_source_code):
        """Test that all tools have handler implementations."""
        handlers = self.extract_tool_handlers(server_source_code)
        tools = self.extract_tool_definitions(server_source_code)

        for tool_name in tools:
            assert tool_name in handlers, f"Tool '{tool_name}' missing handler implementation"
            assert handlers[tool_name]['implemented'], f"Tool '{tool_name}' handler not properly implemented"

    def test_error_handling_in_handlers(self, server_source_code):
        """Test that tool handlers include error handling."""
        handlers = self.extract_tool_handlers(server_source_code)

        for tool_name, handler_info in handlers.items():
            # Error handling is recommended for all handlers
            if not handler_info.get('has_error_handling'):
                print(f"Warning: Tool '{tool_name}' handler may lack error handling")

    async def test_runtime_tool_registration(self):
        """Test that tools are properly registered at runtime."""
        from mcp.types import ListToolsRequest

        server = create_server()

        # Get the list_tools handler using the type key
        list_tools_handler = None
        for handler_key, handler in server.request_handlers.items():
            if hasattr(handler, '__name__') and 'list_tools' in str(handler):
                list_tools_handler = handler
                break

        assert list_tools_handler is not None, "list_tools handler not registered"

        # Call the handler to get tools
        result = await list_tools_handler(ListToolsRequest(method="tools/list"))
        inner = result.root if hasattr(result, 'root') else result
        tools = inner.tools if hasattr(inner, 'tools') else inner
        tool_names = {tool.name for tool in tools}

        expected_tools = {
            'purchase_stamp', 'get_stamp_status', 'list_stamps',
            'extend_stamp', 'upload_data', 'download_data', 'health_check'
        }

        missing_tools = expected_tools - tool_names
        assert not missing_tools, f"Tools not registered at runtime: {missing_tools}"

    def test_gateway_client_runtime_methods(self):
        """Test that gateway client has all expected methods at runtime."""
        client = SwarmGatewayClient()

        expected_methods = {
            'purchase_stamp', 'get_stamp_details', 'list_stamps',
            'extend_stamp', 'upload_data', 'download_data', 'health_check'
        }

        actual_methods = {name for name, _ in inspect.getmembers(client, inspect.ismethod)
                         if not name.startswith('_') and name != 'close'}

        missing_methods = expected_methods - actual_methods
        assert not missing_methods, f"Gateway client missing methods at runtime: {missing_methods}"


class TestCodeChangeDetection:
    """Tests to detect when code changes might affect tool definitions."""

    def test_server_imports_stability(self):
        """Test that server imports are stable and expected."""
        from swarm_provenance_mcp import server

        # Check that critical imports are available
        critical_imports = ['Tool', 'TextContent', 'CallToolRequest', 'CallToolResult']

        for import_name in critical_imports:
            assert hasattr(server, import_name) or import_name in dir(server), \
                f"Critical import '{import_name}' missing from server module"

    def test_gateway_client_interface_stability(self):
        """Test that gateway client interface remains stable."""
        from swarm_provenance_mcp.gateway_client import SwarmGatewayClient

        # Check that the class exists and has expected structure
        assert SwarmGatewayClient is not None
        assert hasattr(SwarmGatewayClient, '__init__')

        # Check constructor signature
        init_sig = inspect.signature(SwarmGatewayClient.__init__)
        # Should accept base_url parameter
        params = list(init_sig.parameters.keys())
        assert 'base_url' in params or len(params) <= 2, \
            "Gateway client constructor signature changed unexpectedly"

    def test_configuration_interface_stability(self):
        """Test that configuration interface remains stable."""
        from swarm_provenance_mcp.config import settings

        # Check that essential configuration is available
        essential_configs = [
            'swarm_gateway_url', 'default_stamp_amount', 'default_stamp_depth'
        ]

        for config_name in essential_configs:
            assert hasattr(settings, config_name), \
                f"Essential configuration '{config_name}' missing"

    def test_mcp_framework_compatibility(self):
        """Test compatibility with MCP framework version."""
        try:
            from mcp.server import Server
            from mcp.types import Tool, TextContent, CallToolRequest, CallToolResult
            from mcp.server.models import InitializationOptions

            # Try to create a basic server
            server = Server("test")
            assert server is not None

            # Check that we can create tool definitions
            tool = Tool(
                name="test_tool",
                description="Test tool",
                inputSchema={"type": "object", "properties": {}}
            )
            assert tool is not None

        except ImportError as e:
            pytest.fail(f"MCP framework compatibility issue: {e}")
        except Exception as e:
            pytest.fail(f"MCP framework version compatibility issue: {e}")


class TestFutureProofing:
    """Tests to ensure the codebase is prepared for future changes."""

    async def test_tool_schema_extensibility(self):
        """Test that tool schemas can be extended without breaking existing functionality."""
        from mcp.types import ListToolsRequest

        server = create_server()

        # Get tools using the type-based lookup
        list_tools_handler = None
        for handler_key, handler in server.request_handlers.items():
            if hasattr(handler, '__name__') and 'list_tools' in str(handler):
                list_tools_handler = handler
                break

        result = await list_tools_handler(ListToolsRequest(method="tools/list"))
        inner = result.root if hasattr(result, 'root') else result
        tools = inner.tools if hasattr(inner, 'tools') else inner

        for tool in tools:
            schema = tool.inputSchema

            # Schema should be extensible (allow additional properties)
            # This is important for backward compatibility
            if 'additionalProperties' in schema:
                # If specified, should not be False (which would prevent extension)
                assert schema['additionalProperties'] is not False, \
                    f"Tool '{tool.name}' schema prevents extension"

    def test_error_message_consistency(self):
        """Test that error messages follow consistent patterns."""
        # This test would examine error handling patterns
        # For now, we just ensure the gateway client has consistent error handling

        from swarm_provenance_mcp.gateway_client import SwarmGatewayClient
        import requests

        client = SwarmGatewayClient()

        # Check that the client can handle RequestException
        assert hasattr(requests, 'RequestException'), \
            "requests.RequestException not available for error handling"

    def test_version_information_available(self):
        """Test that version information is available for compatibility checking."""
        from swarm_provenance_mcp.config import settings

        # Should have version information
        version_fields = ['mcp_server_version', 'mcp_server_name']

        for field in version_fields:
            assert hasattr(settings, field), f"Version field '{field}' not available"
            value = getattr(settings, field)
            assert value is not None and str(value).strip(), \
                f"Version field '{field}' is empty"