"""#160 (c) and (d): event scans split on a 413 and merge-scan failures fail
the event cache build."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from swarm_provenance_mcp.chain.event_cache import TransformationEventCache
from tests.test_chain_client import (  # noqa: F401 — fixtures
    DUMMY_CONTRACT,
    DUMMY_HASH,
    DUMMY_HASH_BYTES,
    _http_error,
    mock_chain_deps,
)

CHILD_HASH = "d" * 64


def _http_413():
    """The HTTPError web3's HTTPProvider raises for a 413 response."""
    from requests import Response
    from requests.exceptions import HTTPError

    response = Response()
    response.status_code = 413
    return HTTPError(
        "413 Client Error: Payload Too Large for url: https://rpc.invalid/",
        response=response,
    )


class FakeEvent:
    """Fake ``contract.events.<Name>``: one event per block, recorded calls.

    ``fail(fb, tb)`` returns an exception to raise for that request, or None.
    """

    def __init__(self, fail=lambda fb, tb: None):
        self.fail = fail
        self.calls = []  # every (from_block, to_block) requested
        self.served = []  # only the ranges that succeeded

    def get_logs(self, from_block, to_block, argument_filters=None):
        self.calls.append((from_block, to_block))
        error = self.fail(from_block, to_block)
        if error is not None:
            raise error
        self.served.append((from_block, to_block))
        return list(range(from_block, to_block + 1))


def _too_large_over(limit):
    """413 for any request wider than ``limit`` blocks."""
    return lambda fb, tb: _http_413() if tb - fb + 1 > limit else None


@pytest.fixture
def contract(mock_chain_deps):
    from swarm_provenance_mcp.chain.contract import DataProvenanceContract

    return DataProvenanceContract(
        web3=mock_chain_deps["web3_instance"], contract_address=DUMMY_CONTRACT
    )


def _assert_covers_once(served, from_block, to_block):
    """Served ranges are in order, without gaps or overlaps, and cover all."""
    expected_start = from_block
    for fb, tb in served:
        assert fb == expected_start
        assert tb >= fb
        expected_start = tb + 1
    assert expected_start == to_block + 1


class TestSplitOn413:
    def test_range_needing_several_splits_is_fully_covered(self, contract):
        event = FakeEvent(_too_large_over(1_000))

        events = contract._get_logs_chunked(event, None, 0, 25_000)

        assert events == list(range(0, 25_001))
        _assert_covers_once(event.served, 0, 25_000)
        # 10k chunks need four halvings to drop to 625 blocks.
        assert max(tb - fb + 1 for fb, tb in event.served) <= 1_000
        assert len(event.served) > 6  # more than a single halving can give

    def test_splitting_is_per_chunk(self, contract):
        """Only the dense chunk shrinks; the next chunk is asked for in full."""

        def fail(fb, tb):
            dense = fb <= 15_000 <= tb
            return _http_413() if dense and tb - fb + 1 > 100 else None

        event = FakeEvent(fail)

        events = contract._get_logs_chunked(event, None, 0, 29_999)

        assert events == list(range(0, 30_000))
        _assert_covers_once(event.served, 0, 29_999)
        assert (0, 9_999) in event.served
        assert (20_000, 29_999) in event.served

    def test_argument_filters_are_passed_to_every_split(self, contract):
        event = MagicMock()
        seen = []

        def get_logs(**kw):
            seen.append(kw)
            if kw["to_block"] - kw["from_block"] + 1 > 2_500:
                raise _http_413()
            return []

        event.get_logs.side_effect = get_logs
        filters = {"originalDataHash": DUMMY_HASH_BYTES}

        contract._get_logs_chunked(event, filters, 0, 9_999)

        assert all(kw["argument_filters"] == filters for kw in seen)

    def test_minimum_chunk_that_still_fails_raises_the_original_error(self, contract):
        error = _http_413()
        event = FakeEvent(lambda fb, tb: error)

        with pytest.raises(type(error)) as caught:
            contract._get_logs_chunked(event, None, 0, 9_999)

        assert caught.value is error
        sizes = [tb - fb + 1 for fb, tb in event.calls]
        assert sizes[-1] == contract._EVENT_MIN_CHUNK_SIZE
        # Depth-first: one request per halving level, then give up.
        assert len(event.calls) <= 16
        assert event.served == []

    def test_one_block_that_is_too_large_is_not_skipped(self, contract):
        event = FakeEvent(lambda fb, tb: _http_413() if fb <= 4_321 <= tb else None)

        with pytest.raises(Exception, match="413"):
            contract._get_logs_chunked(event, None, 0, 25_000)

        # Blocks before the bad one were fetched; nothing after it was.
        assert all(tb < 4_321 for fb, tb in event.served)

    @pytest.mark.parametrize(
        "error",
        [
            ValueError("execution reverted"),
            _http_error(500),
            _http_error(503),
        ],
        ids=["revert", "http-500", "http-503-transport"],
    )
    def test_other_errors_are_raised_without_retry(self, contract, error):
        event = FakeEvent(lambda fb, tb: error)

        with pytest.raises(type(error)) as caught:
            contract._get_logs_chunked(event, None, 0, 25_000)

        assert caught.value is error
        assert event.calls == [(0, 9_999)]


class TestMergeScanFailureFailsCache:
    def test_merge_scan_error_propagates_from_contract(self, contract, mock_chain_deps):
        events = mock_chain_deps["contract"].events.DataMerged
        events.get_logs.side_effect = ValueError("could not decode log")

        with pytest.raises(ValueError, match="decode"):
            contract.get_all_merge_events(from_block=0, to_block=10)

    def test_exhausted_413_on_merge_scan_propagates(self, contract, mock_chain_deps):
        events = mock_chain_deps["contract"].events.DataMerged
        events.get_logs.side_effect = _http_413()

        with pytest.raises(Exception, match="413"):
            contract.get_all_merge_events(from_block=0, to_block=10)

    def test_abi_without_datamerged_returns_empty(self, contract, mock_chain_deps):
        contract._abi = [e for e in contract._abi if e.get("name") != "DataMerged"]
        events = mock_chain_deps["contract"].events.DataMerged

        assert contract.get_all_merge_events(from_block=0, to_block=10) == []
        events.get_logs.assert_not_called()

    def test_cache_build_fails_and_does_not_advance(self):
        cache = TransformationEventCache()
        contract = MagicMock()
        contract.get_all_transformations.return_value = [
            (DUMMY_HASH_BYTES, bytes.fromhex(CHILD_HASH), "Anonymized")
        ]
        contract.get_all_merge_events.side_effect = ValueError("could not decode")

        with pytest.raises(ValueError, match="decode"):
            cache.get_maps(contract, deploy_block=100, current_block=200)

        assert cache._last_scanned_block is None
        assert cache._forward == {} and cache._reverse == {}

    def test_retry_after_merge_failure_does_not_duplicate(self):
        cache = TransformationEventCache()
        contract = MagicMock()
        contract.get_all_transformations.return_value = [
            (DUMMY_HASH_BYTES, bytes.fromhex(CHILD_HASH), "Anonymized")
        ]
        contract.get_all_merge_events.side_effect = [ValueError("decode"), []]

        with pytest.raises(ValueError):
            cache.get_maps(contract, deploy_block=100, current_block=200)
        forward, _ = cache.get_maps(contract, deploy_block=100, current_block=200)

        assert forward[DUMMY_HASH] == [(CHILD_HASH, "Anonymized")]
        assert cache._last_scanned_block == 200

    def test_413_on_merge_scan_through_real_wrapper(self, contract, mock_chain_deps):
        abi_events = mock_chain_deps["contract"].events
        abi_events.DataTransformed.get_logs.return_value = []
        abi_events.DataMerged.get_logs.side_effect = _http_413()

        cache = TransformationEventCache()
        with pytest.raises(Exception, match="413"):
            cache.get_maps(contract, deploy_block=0, current_block=10)
        assert cache._last_scanned_block is None

    def test_merges_are_indexed_after_split_scan(self, contract, mock_chain_deps):
        """A merge scan that needs splitting still lands in the cache."""
        merge = SimpleNamespace(
            args=SimpleNamespace(
                newDataHash=bytes.fromhex(CHILD_HASH),
                sourceDataHashes=[DUMMY_HASH_BYTES],
                transformation="Merged",
            )
        )

        def merged_logs(from_block, to_block, **kw):
            if to_block - from_block + 1 > 3:
                raise _http_413()
            return [merge] if from_block <= 7 <= to_block else []

        abi_events = mock_chain_deps["contract"].events
        abi_events.DataTransformed.get_logs.return_value = []
        abi_events.DataMerged.get_logs.side_effect = merged_logs

        cache = TransformationEventCache()
        forward, reverse = cache.get_maps(contract, deploy_block=0, current_block=20)

        assert forward[DUMMY_HASH] == [(CHILD_HASH, "Merged")]
        assert reverse[CHILD_HASH] == [(DUMMY_HASH, "Merged")]
