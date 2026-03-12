"""Tests for chain configuration fields in Settings."""

import os
import pytest
from unittest.mock import patch


class TestChainConfig:
    """Tests for chain-related settings fields."""

    def test_chain_enabled_defaults_false(self):
        """Tests that chain_enabled defaults to False."""
        with patch.dict(os.environ, {}, clear=True):
            from swarm_provenance_mcp.config import Settings

            s = Settings(
                _env_file=None,
                swarm_gateway_url="https://example.com",
            )
            assert s.chain_enabled is False

    def test_chain_enabled_from_env(self):
        """Tests that CHAIN_ENABLED env var is loaded."""
        with patch.dict(os.environ, {"CHAIN_ENABLED": "true"}, clear=True):
            from swarm_provenance_mcp.config import Settings

            s = Settings(
                _env_file=None,
                swarm_gateway_url="https://example.com",
            )
            assert s.chain_enabled is True

    def test_chain_name_default(self):
        """Tests that chain_name defaults to base-sepolia."""
        with patch.dict(os.environ, {}, clear=True):
            from swarm_provenance_mcp.config import Settings

            s = Settings(
                _env_file=None,
                swarm_gateway_url="https://example.com",
            )
            assert s.chain_name == "base-sepolia"

    def test_chain_name_from_env(self):
        """Tests that CHAIN_NAME env var is loaded."""
        with patch.dict(os.environ, {"CHAIN_NAME": "base"}, clear=True):
            from swarm_provenance_mcp.config import Settings

            s = Settings(
                _env_file=None,
                swarm_gateway_url="https://example.com",
            )
            assert s.chain_name == "base"

    def test_chain_fields_default_none(self):
        """Tests that optional chain fields default to None."""
        with patch.dict(os.environ, {}, clear=True):
            from swarm_provenance_mcp.config import Settings

            s = Settings(
                _env_file=None,
                swarm_gateway_url="https://example.com",
            )
            assert s.provenance_wallet_key is None
            assert s.chain_rpc_url is None
            assert s.chain_contract_address is None
            assert s.chain_explorer_url is None
            assert s.chain_gas_limit is None

    def test_chain_gas_limit_parses_as_int(self):
        """Tests that CHAIN_GAS_LIMIT is parsed as int."""
        with patch.dict(
            os.environ, {"CHAIN_GAS_LIMIT": "500000"}, clear=True
        ):
            from swarm_provenance_mcp.config import Settings

            s = Settings(
                _env_file=None,
                swarm_gateway_url="https://example.com",
            )
            assert s.chain_gas_limit == 500000
            assert isinstance(s.chain_gas_limit, int)

    def test_chain_wallet_key_from_env(self):
        """Tests that PROVENANCE_WALLET_KEY env var is loaded."""
        test_key = "0x" + "a" * 64
        with patch.dict(
            os.environ, {"PROVENANCE_WALLET_KEY": test_key}, clear=True
        ):
            from swarm_provenance_mcp.config import Settings

            s = Settings(
                _env_file=None,
                swarm_gateway_url="https://example.com",
            )
            assert s.provenance_wallet_key == test_key
