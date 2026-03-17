"""
In-memory cache for DataTransformed event logs.

The DataProvenance contract stores transformation descriptions (string[])
but NOT the linked hashes in state — newDataHash is only in event logs.
This forces get_provenance_chain to scan the full contract history on
every call (~1.4M blocks on Base Sepolia, ~20s).

This module provides a singleton cache per (chain, contract_address) that
does a full scan on first call and incremental scans on subsequent calls,
reducing repeat queries to <1s.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

# Module-level singleton registry keyed by (chain, contract_address_lower)
_registry: dict[tuple[str, str], TransformationEventCache] = {}
_registry_lock = threading.Lock()


class TransformationEventCache:
    """Thread-safe in-memory cache of DataTransformed events.

    Builds forward and reverse lookup maps from event logs.
    First call does a full scan from deploy_block; subsequent calls
    scan only new blocks since the last scan.
    """

    def __init__(self):
        self._forward: dict[str, list[tuple[str, str]]] = {}
        self._reverse: dict[str, list[tuple[str, str]]] = {}
        self._last_scanned_block: int | None = None
        self._lock = threading.Lock()

    def get_maps(
        self,
        contract,
        deploy_block: int,
        current_block: int,
    ) -> tuple[dict, dict]:
        """Return (forward, reverse) transformation maps.

        Args:
            contract: DataProvenanceContract instance with get_all_transformations().
            deploy_block: Contract deploy block (start of first scan).
            current_block: Latest block number (end of scan range).

        Returns:
            Tuple of (forward, reverse) dicts:
              forward: original_hex -> [(new_hex, description), ...]
              reverse: new_hex -> [(original_hex, description), ...]

        Raises:
            Exception: Propagated from contract.get_all_transformations()
                on scan failure. Caller should catch and fall back to
                per-node event queries.
        """
        with self._lock:
            if self._last_scanned_block is None:
                from_block = deploy_block
            else:
                from_block = self._last_scanned_block + 1

            if from_block > current_block:
                return self._forward, self._reverse

            events = contract.get_all_transformations(
                from_block=from_block,
                to_block=current_block,
            )

            for orig_bytes, new_bytes, desc in events:
                orig_hex = (
                    orig_bytes.hex()
                    if isinstance(orig_bytes, bytes)
                    else str(orig_bytes)
                )
                new_hex = (
                    new_bytes.hex() if isinstance(new_bytes, bytes) else str(new_bytes)
                )
                self._forward.setdefault(orig_hex, []).append((new_hex, desc))
                self._reverse.setdefault(new_hex, []).append((orig_hex, desc))

            self._last_scanned_block = current_block
            logger.debug(
                "Event cache updated: scanned blocks %d-%d, "
                "%d forward entries, %d reverse entries",
                from_block,
                current_block,
                len(self._forward),
                len(self._reverse),
            )

            return self._forward, self._reverse


def get_cache(chain: str, contract_address: str) -> TransformationEventCache:
    """Get or create the singleton cache for a (chain, contract) pair.

    Args:
        chain: Chain name (e.g. 'base-sepolia').
        contract_address: Contract address (case-insensitive).

    Returns:
        TransformationEventCache instance (shared across callers).
    """
    key = (chain, contract_address.lower())
    with _registry_lock:
        if key not in _registry:
            _registry[key] = TransformationEventCache()
        return _registry[key]


def clear_registry():
    """Clear all cached instances. For testing only."""
    with _registry_lock:
        _registry.clear()
