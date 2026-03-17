"""Tests for the TransformationEventCache and singleton registry."""

import threading
import pytest
from unittest.mock import MagicMock

from swarm_provenance_mcp.chain.event_cache import (
    TransformationEventCache,
    get_cache,
    clear_registry,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Clear the singleton registry before each test."""
    clear_registry()
    yield
    clear_registry()


class TestTransformationEventCache:
    """Unit tests for TransformationEventCache."""

    def _make_contract(self, events=None):
        """Build a mock contract with get_all_transformations."""
        contract = MagicMock()
        contract.get_all_transformations.return_value = events or []
        return contract

    def test_first_call_full_scan(self):
        """First call should scan from deploy_block to current_block."""
        parent = bytes.fromhex("aa" * 32)
        child = bytes.fromhex("bb" * 32)
        contract = self._make_contract(
            [
                (parent, child, "Step 1"),
            ]
        )

        cache = TransformationEventCache()
        forward, reverse = cache.get_maps(contract, deploy_block=100, current_block=500)

        contract.get_all_transformations.assert_called_once_with(
            from_block=100,
            to_block=500,
        )
        assert "aa" * 32 in forward
        assert forward["aa" * 32] == [("bb" * 32, "Step 1")]
        assert "bb" * 32 in reverse
        assert reverse["bb" * 32] == [("aa" * 32, "Step 1")]
        assert cache._last_scanned_block == 500

    def test_second_call_incremental(self):
        """Second call should scan only new blocks and preserve old entries."""
        parent = bytes.fromhex("aa" * 32)
        child = bytes.fromhex("bb" * 32)
        contract = self._make_contract([(parent, child, "Step 1")])
        cache = TransformationEventCache()

        # First call: full scan
        cache.get_maps(contract, deploy_block=100, current_block=500)
        assert contract.get_all_transformations.call_count == 1

        # Add a new event for the incremental scan
        new_parent = bytes.fromhex("cc" * 32)
        new_child = bytes.fromhex("dd" * 32)
        contract.get_all_transformations.return_value = [
            (new_parent, new_child, "Step 2"),
        ]

        # Second call: incremental
        forward, reverse = cache.get_maps(contract, deploy_block=100, current_block=600)
        assert contract.get_all_transformations.call_count == 2
        # Should scan from 501 (last + 1)
        contract.get_all_transformations.assert_called_with(
            from_block=501,
            to_block=600,
        )
        # New entries present
        assert "cc" * 32 in forward
        assert "dd" * 32 in reverse
        # Old entries preserved
        assert "aa" * 32 in forward
        assert forward["aa" * 32] == [("bb" * 32, "Step 1")]
        assert "bb" * 32 in reverse
        assert cache._last_scanned_block == 600

    def test_no_new_blocks(self):
        """No scan when current_block == last_scanned_block."""
        contract = self._make_contract([])
        cache = TransformationEventCache()

        cache.get_maps(contract, deploy_block=100, current_block=500)
        assert contract.get_all_transformations.call_count == 1

        # Same block — no scan
        cache.get_maps(contract, deploy_block=100, current_block=500)
        assert contract.get_all_transformations.call_count == 1

    def test_empty_events(self):
        """Works with no events at all."""
        contract = self._make_contract([])
        cache = TransformationEventCache()

        forward, reverse = cache.get_maps(contract, deploy_block=0, current_block=1000)
        assert forward == {}
        assert reverse == {}
        assert cache._last_scanned_block == 1000

    def test_scan_failure_propagates(self):
        """Scan failure should propagate so caller can fall back."""
        contract = MagicMock()
        contract.get_all_transformations.side_effect = Exception("RPC down")
        cache = TransformationEventCache()

        with pytest.raises(Exception, match="RPC down"):
            cache.get_maps(contract, deploy_block=100, current_block=500)

        # Cache should not have recorded a last_scanned_block
        assert cache._last_scanned_block is None

    def test_multiple_events_same_original(self):
        """Multiple transformations from the same original hash."""
        parent = bytes.fromhex("aa" * 32)
        child1 = bytes.fromhex("bb" * 32)
        child2 = bytes.fromhex("cc" * 32)
        contract = self._make_contract(
            [
                (parent, child1, "Step 1"),
                (parent, child2, "Step 2"),
            ]
        )

        cache = TransformationEventCache()
        forward, reverse = cache.get_maps(contract, deploy_block=0, current_block=100)

        assert len(forward["aa" * 32]) == 2
        assert ("bb" * 32, "Step 1") in forward["aa" * 32]
        assert ("cc" * 32, "Step 2") in forward["aa" * 32]

    def test_thread_safety(self):
        """Concurrent get_maps calls should not corrupt state."""
        parent = bytes.fromhex("aa" * 32)
        child = bytes.fromhex("bb" * 32)

        call_count = 0

        def slow_scan(from_block, to_block):
            nonlocal call_count
            call_count += 1
            return [(parent, child, f"Call {call_count}")]

        contract = MagicMock()
        contract.get_all_transformations.side_effect = slow_scan

        cache = TransformationEventCache()
        results = []
        errors = []

        def worker(block):
            try:
                fwd, rev = cache.get_maps(contract, deploy_block=0, current_block=block)
                results.append((fwd, rev))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(1000 + i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # All threads should get consistent forward/reverse maps
        for fwd, rev in results:
            assert "aa" * 32 in fwd
            assert "bb" * 32 in rev

    def test_recovery_after_failed_scan(self):
        """After a failed scan, next call should retry from same block."""
        parent = bytes.fromhex("aa" * 32)
        child = bytes.fromhex("bb" * 32)
        contract = MagicMock()
        cache = TransformationEventCache()

        # First call succeeds
        contract.get_all_transformations.return_value = [
            (parent, child, "Step 1"),
        ]
        cache.get_maps(contract, deploy_block=100, current_block=500)
        assert cache._last_scanned_block == 500

        # Second call fails
        contract.get_all_transformations.side_effect = Exception("RPC down")
        with pytest.raises(Exception, match="RPC down"):
            cache.get_maps(contract, deploy_block=100, current_block=600)

        # last_scanned_block should still be 500 (not advanced)
        assert cache._last_scanned_block == 500

        # Third call succeeds — should retry from 501
        contract.get_all_transformations.side_effect = None
        contract.get_all_transformations.return_value = []
        forward, reverse = cache.get_maps(contract, deploy_block=100, current_block=700)

        contract.get_all_transformations.assert_called_with(
            from_block=501,
            to_block=700,
        )
        # Old entries still present
        assert "aa" * 32 in forward
        assert cache._last_scanned_block == 700


class TestReadonlyPathCacheIntegration:
    """Test the server.py _readonly_traverse path uses the cache."""

    async def test_readonly_traverse_uses_cache(self):
        """No-wallet path with deploy_block set should use event cache."""
        from swarm_provenance_mcp.server import create_server

        parent_hash = "aa" * 32
        child_hash = "bb" * 32
        TEST_CONTRACT = "0x9a3c6F47B69211F05891CCb7aD33596290b9fE64"

        server = create_server()

        mock_provider = MagicMock()
        mock_provider.web3 = MagicMock()
        mock_provider.web3.eth.block_number = 40_000_000
        mock_provider.contract_address = TEST_CONTRACT
        mock_provider.chain = "base-sepolia"
        mock_provider.deploy_block = 37_562_100

        mock_contract = MagicMock()
        # Event scan returns one transformation
        mock_contract.get_all_transformations.return_value = [
            (bytes.fromhex(parent_hash), bytes.fromhex(child_hash), "Anonymized"),
        ]
        # Both hashes are registered
        zero_address = "0x" + "0" * 40
        owner = "0x1234567890abcdef1234567890abcdef12345678"

        def get_data_record(h):
            h_hex = h.hex() if isinstance(h, bytes) else str(h)
            if h_hex == parent_hash:
                return (
                    bytes.fromhex(parent_hash),
                    owner,
                    1700000000,
                    "original",
                    [],
                    [],
                    0,
                )
            if h_hex == child_hash:
                return (
                    bytes.fromhex(child_hash),
                    owner,
                    1700001000,
                    "derived",
                    [],
                    [],
                    0,
                )
            return (bytes(32), zero_address, 0, "", [], [], 0)

        mock_contract.get_data_record.side_effect = get_data_record

        from unittest.mock import patch
        from tests.test_tool_execution import call_tool_directly

        with (
            patch("swarm_provenance_mcp.server.CHAIN_AVAILABLE", True),
            patch("swarm_provenance_mcp.server.chain_client", None),
            patch(
                "swarm_provenance_mcp.chain.provider.ChainProvider",
                return_value=mock_provider,
            ),
            patch(
                "swarm_provenance_mcp.chain.contract.DataProvenanceContract",
                return_value=mock_contract,
            ),
        ):
            result = await call_tool_directly(
                server,
                "get_provenance_chain",
                {"swarm_hash": parent_hash},
            )

        assert not result.isError
        text = result.content[0].text
        assert "2 entries" in text
        assert parent_hash in text
        assert child_hash in text

        # Verify it used the full scan (cache path), not per-node queries
        mock_contract.get_all_transformations.assert_called_once_with(
            from_block=37_562_100,
            to_block=40_000_000,
        )
        mock_contract.get_transformations_from.assert_not_called()
        mock_contract.get_transformations_to.assert_not_called()


class TestSingletonRegistry:
    """Tests for the get_cache singleton registry."""

    def test_same_key_returns_same_instance(self):
        """Same (chain, address) should return the same cache."""
        c1 = get_cache("base-sepolia", "0xABC")
        c2 = get_cache("base-sepolia", "0xABC")
        assert c1 is c2

    def test_different_key_returns_different_instance(self):
        """Different chain or address should return different caches."""
        c1 = get_cache("base-sepolia", "0xABC")
        c2 = get_cache("base", "0xABC")
        c3 = get_cache("base-sepolia", "0xDEF")
        assert c1 is not c2
        assert c1 is not c3

    def test_case_insensitive_address(self):
        """Address matching should be case-insensitive."""
        c1 = get_cache("base-sepolia", "0xAbCdEf")
        c2 = get_cache("base-sepolia", "0xABCDEF")
        c3 = get_cache("base-sepolia", "0xabcdef")
        assert c1 is c2
        assert c1 is c3

    def test_clear_registry(self):
        """clear_registry should remove all cached instances."""
        c1 = get_cache("base-sepolia", "0xABC")
        clear_registry()
        c2 = get_cache("base-sepolia", "0xABC")
        assert c1 is not c2
