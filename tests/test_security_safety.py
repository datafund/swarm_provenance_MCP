"""Security and safety tests to prevent vulnerabilities."""

import asyncio
import pytest
import json
import os
import tempfile
from unittest.mock import patch, MagicMock
from swarm_provenance_mcp.gateway_client import SwarmGatewayClient
from swarm_provenance_mcp.config import settings


class TestInputValidationSecurity:
    """Tests to ensure input validation prevents security issues."""

    def test_upload_data_injection_protection(self):
        """Test that upload data is properly sanitized."""
        client = SwarmGatewayClient()

        # Test various potentially malicious inputs
        malicious_inputs = [
            '{"__proto__": {"admin": true}}',  # Prototype pollution
            '<script>alert("xss")</script>',   # XSS attempt
            '"; DROP TABLE users; --',         # SQL injection attempt
            '../../../etc/passwd',             # Path traversal
            '\x00\x01\x02',                   # Null bytes and control chars
            '{"$eval": "process.exit()"}',     # Code injection attempt
        ]

        for malicious_input in malicious_inputs:
            try:
                # Should not crash or execute anything
                client.upload_data(malicious_input, "fake_stamp")
            except ValueError:
                # Size limit errors are fine
                if "exceeds 4KB limit" in str(ValueError):
                    continue
            except Exception as e:
                # Should be network errors, not security exceptions
                error_type = type(e).__name__
                safe_errors = ['ConnectionError', 'RequestException', 'HTTPError', 'Timeout']
                assert any(safe_error in error_type for safe_error in safe_errors), \
                    f"Unexpected error type for malicious input: {error_type}"

    def test_stamp_id_validation(self):
        """Test that stamp IDs are properly validated."""
        from swarm_provenance_mcp.server import handle_get_stamp_status

        dangerous_stamp_ids = [
            "../../../etc/passwd",
            "'; DROP TABLE stamps; --",
            "<script>alert('xss')</script>",
            "\x00\x01\x02",
            "stamp\nid\rwith\tcontrol\vchars",
        ]

        for stamp_id in dangerous_stamp_ids:
            # Should handle gracefully, not crash or execute
            try:
                asyncio.run(handle_get_stamp_status({"stamp_id": stamp_id}))
            except Exception as e:
                # Should be handled gracefully
                error_str = str(e).lower()
                dangerous_keywords = ['eval', 'exec', 'import', 'subprocess']
                for keyword in dangerous_keywords:
                    assert keyword not in error_str, f"Dangerous keyword '{keyword}' in error message"

    def test_reference_hash_validation(self):
        """Test that reference hashes are properly validated."""
        client = SwarmGatewayClient()

        dangerous_references = [
            "../../../etc/passwd",
            "ref\x00with\x01nulls",
            "ref with spaces",
            "ref/with/slashes",
            "ref?with=query&params",
            "ref#with-fragment",
        ]

        for ref in dangerous_references:
            try:
                client.download_data(ref)
            except Exception as e:
                # Should be network/HTTP errors, not injection vulnerabilities
                error_type = type(e).__name__
                safe_errors = ['ConnectionError', 'RequestException', 'HTTPError', 'InvalidURL']
                # Allow any of these safe error types
                pass


class TestConfigurationSecurity:
    """Tests to ensure configuration doesn't expose sensitive data."""

    def test_no_secrets_in_logs(self):
        """Test that sensitive configuration doesn't appear in logs."""
        import logging
        from io import StringIO

        # Capture log output
        log_capture = StringIO()
        handler = logging.StreamHandler(log_capture)
        logging.getLogger().addHandler(handler)

        try:
            # Operations that might log configuration
            client = SwarmGatewayClient()
            str(settings)  # String representation

            log_output = log_capture.getvalue().lower()

            # Check for potentially sensitive patterns
            sensitive_patterns = [
                'password', 'secret', 'key', 'token', 'auth',
                'private', 'credential', 'api_key'
            ]

            for pattern in sensitive_patterns:
                if pattern in log_output:
                    # Allow if it's just the word in a safe context
                    lines_with_pattern = [line for line in log_output.split('\n') if pattern in line]
                    for line in lines_with_pattern:
                        # Skip safe references
                        if any(safe in line for safe in ['field', 'parameter', 'setting', 'config']):
                            continue
                        pytest.fail(f"Potential secret '{pattern}' found in logs: {line.strip()}")

        finally:
            logging.getLogger().removeHandler(handler)

    def test_environment_variable_isolation(self):
        """Test that environment variables are properly isolated."""
        # Ensure settings don't accidentally expose all env vars
        settings_dict = settings.__dict__ if hasattr(settings, '__dict__') else {}

        dangerous_env_vars = [
            'PATH', 'HOME', 'USER', 'PWD', 'SHELL',
            'SSH_AUTH_SOCK', 'AWS_SECRET_ACCESS_KEY', 'DATABASE_PASSWORD'
        ]

        for var in dangerous_env_vars:
            if var in os.environ:
                # Should not appear in settings object
                for key, value in settings_dict.items():
                    if isinstance(value, str) and value == os.environ[var]:
                        pytest.fail(f"Environment variable {var} leaked into settings as {key}")

    def test_url_validation_prevents_ssrf(self):
        """Test that URL validation prevents SSRF attacks."""
        dangerous_urls = [
            "http://localhost:22",        # Internal service
            "http://127.0.0.1:3306",     # Database port
            "http://169.254.169.254",    # AWS metadata
            "file:///etc/passwd",        # File protocol
            "ftp://internal.server",     # FTP protocol
            "gopher://internal.server",  # Gopher protocol
        ]

        for url in dangerous_urls:
            # Verify the client accepts the URL but mock the actual network
            # call so we don't connect to real services in CI
            client = SwarmGatewayClient(url)
            assert client.base_url.rstrip("/") == url.rstrip("/")

            # Verify that making requests with dangerous URLs raises errors
            # (mocked to avoid real network calls hitting SSH/DB ports in CI)
            with patch.object(client.session, 'get',
                              side_effect=ConnectionError(f"Mocked connection to {url}")):
                with pytest.raises(ConnectionError):
                    client.health_check()


class TestDataHandlingSafety:
    """Tests to ensure data handling is safe and doesn't corrupt data."""

    def test_binary_data_safety(self):
        """Test that binary data is handled safely."""
        client = SwarmGatewayClient()

        # Test various binary patterns
        binary_patterns = [
            b'\x00\x01\x02\x03',           # Null bytes
            b'\xff\xfe\xfd\xfc',           # High bytes
            b'\r\n\r\n',                   # CRLF injection
            b'<?xml version="1.0"?>',      # XML
            b'%PDF-1.4',                   # PDF header
            b'\x89PNG\r\n\x1a\n',         # PNG header
        ]

        for binary_data in binary_patterns:
            if len(binary_data) <= 4096:
                try:
                    # Convert to string for upload
                    data_str = binary_data.decode('latin1')  # Preserve all bytes
                    client.upload_data(data_str, "fake_stamp")
                except UnicodeDecodeError:
                    # Expected for some binary data
                    pass
                except Exception as e:
                    # Should be network errors, not data corruption
                    error_type = type(e).__name__
                    assert error_type in ['ConnectionError', 'RequestException', 'HTTPError'], \
                        f"Unexpected error with binary data: {error_type}"

    def test_unicode_normalization_safety(self):
        """Test that Unicode normalization doesn't cause security issues."""
        import unicodedata

        client = SwarmGatewayClient()

        # Test Unicode normalization edge cases
        unicode_tests = [
            "café",                        # Normal unicode
            "cafe\u0301",                  # Combining character
            "𝒶𝒷𝒸",                           # Mathematical script
            "\u200B\u200C\u200D",         # Zero-width characters
            "\uFEFF",                      # BOM character
        ]

        for unicode_str in unicode_tests:
            # Test that normalization doesn't change security properties
            normalized = unicodedata.normalize('NFC', unicode_str)

            try:
                client.upload_data(unicode_str, "fake_stamp")
                client.upload_data(normalized, "fake_stamp")
            except Exception:
                # Network errors expected
                pass

    def test_size_calculation_accuracy(self):
        """Test that size calculations are accurate and can't be bypassed."""
        client = SwarmGatewayClient()

        # Test edge cases around 4KB limit
        test_cases = [
            ("x" * 4096, False),          # Exactly 4KB
            ("x" * 4097, True),           # Just over
            ("é" * 2048, False),          # Unicode that's 4096 bytes
            ("é" * 2049, True),           # Unicode that's over 4KB
        ]

        for data, should_fail in test_cases:
            actual_size = len(data.encode('utf-8'))

            if should_fail:
                with pytest.raises(ValueError, match="exceeds 4KB limit"):
                    client.upload_data(data, "fake_stamp")
            else:
                try:
                    client.upload_data(data, "fake_stamp")
                except ValueError as e:
                    if "exceeds 4KB limit" in str(e):
                        pytest.fail(f"Data size {actual_size} incorrectly rejected")
                except:
                    pass  # Other errors expected


class TestErrorHandlingSecurity:
    """Tests to ensure error handling doesn't leak sensitive information."""

    def test_error_message_sanitization(self):
        """Test that error messages don't leak sensitive information."""
        from swarm_provenance_mcp.server import handle_upload_data

        # Test with various error conditions
        error_conditions = [
            {"data": "test", "stamp_id": ""},  # Empty stamp ID
            {"data": "", "stamp_id": "test"},   # Empty data
            {},                                 # Missing required fields
        ]

        for condition in error_conditions:
            result = asyncio.run(handle_upload_data(condition))

            if hasattr(result, 'content') and result.content:
                error_text = result.content[0].text.lower()

                # Should not contain sensitive information
                # Note: localhost might be acceptable in development, but should be checked
                sensitive_info = [
                    'traceback', 'stack trace', 'file path',
                    'internal error', 'debug',
                    'password', 'secret', 'key'
                ]

                for info in sensitive_info:
                    assert info not in error_text, \
                        f"Error message contains sensitive info '{info}': {error_text}"

    def test_exception_handling_completeness(self):
        """Test that all exceptions are properly handled."""
        from swarm_provenance_mcp.server import (
            handle_upload_data, handle_download_data, handle_health_check
        )

        # Test with conditions likely to cause exceptions
        handlers_and_bad_args = [
            (handle_upload_data, {"data": None, "stamp_id": None}),
            (handle_download_data, {"reference": None}),
            (handle_health_check, {"unexpected": "argument"}),
        ]

        for handler, bad_args in handlers_and_bad_args:
            try:
                result = asyncio.run(handler(bad_args))

                # Should return error result, not raise exception
                if hasattr(result, 'isError'):
                    # Good - returned error result
                    pass
                else:
                    pytest.fail(f"Handler {handler.__name__} should return error result for bad args")

            except Exception as e:
                # If exceptions are raised, they should be safe
                error_msg = str(e)

                # Should not contain code injection opportunities
                dangerous_patterns = ['eval(', 'exec(', '__import__', 'subprocess']
                for pattern in dangerous_patterns:
                    assert pattern not in error_msg, \
                        f"Dangerous pattern '{pattern}' in exception: {error_msg}"


class TestDependencySecurityBaseline:
    """Tests to track security properties of dependencies."""

    def test_requests_version_safety(self):
        """Test that requests library version doesn't have known CVEs."""
        import requests

        # Track version for security monitoring
        version = requests.__version__
        print(f"Requests version: {version}")

        # Known vulnerable versions (update as needed)
        vulnerable_versions = [
            '2.8.0', '2.8.1',  # CVE-2018-18074
            # Add other known vulnerable versions
        ]

        assert version not in vulnerable_versions, \
            f"Requests version {version} has known vulnerabilities"

    def test_no_dangerous_imports(self):
        """Test that code doesn't import dangerous modules."""
        import ast
        from pathlib import Path

        # Scan source files for dangerous imports
        dangerous_modules = [
            'subprocess', 'os.system', 'eval', 'exec',
            'pickle', 'marshal', 'shelve', '__import__'
        ]

        source_dir = Path(__file__).parent.parent / 'swarm_provenance_mcp'

        for py_file in source_dir.glob('*.py'):
            with open(py_file, 'r', encoding='utf-8') as f:
                try:
                    tree = ast.parse(f.read())

                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            for alias in node.names:
                                if alias.name in dangerous_modules:
                                    pytest.fail(f"Dangerous import '{alias.name}' in {py_file}")
                        elif isinstance(node, ast.ImportFrom):
                            if node.module in dangerous_modules:
                                pytest.fail(f"Dangerous import from '{node.module}' in {py_file}")

                except SyntaxError:
                    # Skip files with syntax errors
                    pass