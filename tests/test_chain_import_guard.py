"""Tests for the chain module import guard mechanism."""

import pytest


class TestChainImportGuard:
    """Tests for CHAIN_AVAILABLE flag and import guard."""

    def test_chain_available_flag_exists(self):
        """Tests that CHAIN_AVAILABLE flag exists in chain module."""
        from swarm_provenance_mcp.chain import CHAIN_AVAILABLE

        assert isinstance(CHAIN_AVAILABLE, bool)

    def test_chain_module_loads_without_error(self):
        """Tests that chain module loads without raising ImportError."""
        # This should not raise even if web3 is not installed
        import swarm_provenance_mcp.chain

        assert hasattr(swarm_provenance_mcp.chain, "CHAIN_AVAILABLE")

    def test_chain_available_reflects_web3_presence(self):
        """Tests that CHAIN_AVAILABLE reflects whether web3 is importable."""
        from swarm_provenance_mcp.chain import CHAIN_AVAILABLE

        # If web3 is not installed (expected in base test env),
        # CHAIN_AVAILABLE should be False
        try:
            import web3  # noqa: F401

            web3_installed = True
        except ImportError:
            web3_installed = False

        # CHAIN_AVAILABLE may differ from web3_installed if eth_account
        # is also missing, but both False is the common test case
        if not web3_installed:
            assert CHAIN_AVAILABLE is False
