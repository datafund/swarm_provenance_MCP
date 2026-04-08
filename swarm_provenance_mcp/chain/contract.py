"""
DataProvenance smart contract wrapper.

Provides typed Python methods for all DataProvenance contract functions.
Build methods return transaction dicts; read methods call directly.

Dependencies (web3, eth-account) are included in the default install.
"""

import json
import logging
from enum import IntEnum
from pathlib import Path
from typing import List, Optional, Tuple

from .exceptions import ChainConfigurationError, ChainValidationError

logger = logging.getLogger(__name__)

# --- Constants ---
# Client-side validation limits (not enforced on-chain — Solidity strings are
# dynamically sized, so these are reasonable guard rails to prevent wasted gas).
MAX_DATA_TYPE_LENGTH = 64
MAX_TRANSFORMATION_LENGTH = 256

# Client-side batch limits. The contract does not enforce per-call batch sizes
# but larger batches risk exceeding the block gas limit.
MAX_BATCH_REGISTER = 50
MAX_BATCH_ACCESS = 100
MIN_MERGE_SOURCES = 2
MAX_MERGE_SOURCES = 50

# Note: the contract also defines on-chain constants MAX_TRANSFORMATIONS (100)
# and MAX_ACCESSORS readable via contract.functions.MAX_TRANSFORMATIONS().call().
# These limit how many transformations/accessors can be stored per data record.


class DataStatus(IntEnum):
    """On-chain data status values matching the contract enum."""

    ACTIVE = 0
    RESTRICTED = 1
    DELETED = 2


# --- Validation helpers ---


def _normalize_hash(data_hash: str) -> bytes:
    """
    Normalize a hex hash string to bytes32.

    Accepts 64-char hex (with or without 0x prefix) or a raw 32-byte Swarm hash.

    Args:
        data_hash: Hex string (e.g., '0xabcd...' or 'abcd...').

    Returns:
        32-byte value suitable for bytes32 contract parameter.

    Raises:
        ChainValidationError: If hash format is invalid.
    """
    if isinstance(data_hash, bytes):
        if len(data_hash) != 32:
            raise ChainValidationError(
                f"Hash must be exactly 32 bytes, got {len(data_hash)}"
            )
        return data_hash

    h = data_hash.strip()
    if h.startswith("0x"):
        h = h[2:]

    if len(h) != 64:
        raise ChainValidationError(
            f"Hash must be 64 hex characters (32 bytes), got {len(h)} characters"
        )

    try:
        return bytes.fromhex(h)
    except ValueError as e:
        raise ChainValidationError(f"Invalid hex in hash: {e}") from e


def _validate_data_type(data_type: str) -> str:
    """
    Validate data type string length.

    Args:
        data_type: Data type/category string.

    Returns:
        The validated data type string.

    Raises:
        ChainValidationError: If string exceeds MAX_DATA_TYPE_LENGTH.
    """
    if len(data_type) > MAX_DATA_TYPE_LENGTH:
        raise ChainValidationError(
            f"data_type exceeds maximum length of {MAX_DATA_TYPE_LENGTH} characters "
            f"(got {len(data_type)})"
        )
    return data_type


def _validate_transformation(description: str) -> str:
    """
    Validate transformation description string length.

    Args:
        description: Transformation description string.

    Returns:
        The validated description string.

    Raises:
        ChainValidationError: If string exceeds MAX_TRANSFORMATION_LENGTH.
    """
    if len(description) > MAX_TRANSFORMATION_LENGTH:
        raise ChainValidationError(
            f"transformation description exceeds maximum length of "
            f"{MAX_TRANSFORMATION_LENGTH} characters (got {len(description)})"
        )
    return description


def _load_abi() -> list:
    """
    Load the DataProvenance contract ABI from the bundled JSON file.

    Returns:
        Parsed ABI as a list of dicts.

    Raises:
        ChainConfigurationError: If ABI file cannot be loaded.
    """
    abi_path = Path(__file__).parent / "abi" / "DataProvenance.json"
    try:
        with open(abi_path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        raise ChainConfigurationError(
            f"Failed to load DataProvenance ABI from {abi_path}: {e}"
        ) from e


class DataProvenanceContract:
    """Wrapper around the DataProvenance smart contract.

    Provides build_*_tx() methods that return unsigned transaction dicts
    and read methods that call the contract directly.
    """

    def __init__(self, web3, contract_address: str):
        """
        Initialize the contract wrapper.

        Args:
            web3: Web3 instance connected to the target chain.
            contract_address: Deployed DataProvenance contract address.

        Raises:
            ChainConfigurationError: If ABI loading or contract init fails.
        """
        self._web3 = web3
        self._address = web3.to_checksum_address(contract_address)
        self._abi = _load_abi()
        self._contract = web3.eth.contract(
            address=self._address,
            abi=self._abi,
        )

    @property
    def address(self) -> str:
        """Contract address."""
        return self._address

    # --- Build transaction methods (return tx dicts) ---

    def build_register_data_tx(
        self,
        data_hash: str,
        data_type: str,
        sender: str,
    ) -> dict:
        """
        Build transaction to register a data hash on-chain.

        Args:
            data_hash: 64-char hex hash of the data (Swarm reference).
            data_type: Category/type string (max 64 chars).
            sender: Address of the transaction sender.

        Returns:
            Unsigned transaction dict.
        """
        hash_bytes = _normalize_hash(data_hash)
        _validate_data_type(data_type)
        return self._contract.functions.registerData(
            hash_bytes, data_type
        ).build_transaction(
            {
                "from": self._web3.to_checksum_address(sender),
            }
        )

    def build_register_data_for_tx(
        self,
        data_hash: str,
        data_type: str,
        actual_owner: str,
        sender: str,
    ) -> dict:
        """
        Build transaction to register data on behalf of another owner.

        The sender must be an authorized delegate of the actual owner.

        Args:
            data_hash: 64-char hex hash of the data.
            data_type: Category/type string (max 64 chars).
            actual_owner: Address of the actual data owner.
            sender: Address of the delegate sending the transaction.

        Returns:
            Unsigned transaction dict.
        """
        hash_bytes = _normalize_hash(data_hash)
        _validate_data_type(data_type)
        return self._contract.functions.registerDataFor(
            hash_bytes,
            data_type,
            self._web3.to_checksum_address(actual_owner),
        ).build_transaction(
            {
                "from": self._web3.to_checksum_address(sender),
            }
        )

    def build_batch_register_data_tx(
        self,
        data_hashes: List[str],
        data_types: List[str],
        sender: str,
    ) -> dict:
        """
        Build transaction to register multiple data hashes in one call.

        Args:
            data_hashes: List of 64-char hex hashes.
            data_types: List of data type strings (same length as hashes).
            sender: Address of the transaction sender.

        Returns:
            Unsigned transaction dict.

        Raises:
            ChainValidationError: If arrays have different lengths or exceed batch limit.
        """
        if len(data_hashes) != len(data_types):
            raise ChainValidationError(
                f"data_hashes ({len(data_hashes)}) and data_types ({len(data_types)}) "
                "must have the same length"
            )
        if len(data_hashes) > MAX_BATCH_REGISTER:
            raise ChainValidationError(
                f"Batch size {len(data_hashes)} exceeds maximum of {MAX_BATCH_REGISTER}"
            )

        hash_bytes_list = [_normalize_hash(h) for h in data_hashes]
        for dt in data_types:
            _validate_data_type(dt)

        return self._contract.functions.batchRegisterData(
            hash_bytes_list, data_types
        ).build_transaction(
            {
                "from": self._web3.to_checksum_address(sender),
            }
        )

    def build_register_data_with_storage_ref_tx(
        self,
        data_hash: str,
        data_type: str,
        storage_ref: str,
        sender: str,
    ) -> dict:
        """
        Build transaction to register a data hash with a linked storage reference.

        Calls the 3-param registerData(bytes32, string, bytes32) overload.

        Args:
            data_hash: 64-char hex hash of the data (content hash, e.g. SHA-256).
            data_type: Category/type string (max 64 chars).
            storage_ref: 64-char hex storage reference (e.g. Swarm reference).
            sender: Address of the transaction sender.

        Returns:
            Unsigned transaction dict.
        """
        hash_bytes = _normalize_hash(data_hash)
        ref_bytes = _normalize_hash(storage_ref)
        _validate_data_type(data_type)
        return self._contract.functions.registerData(
            hash_bytes, data_type, ref_bytes
        ).build_transaction(
            {
                "from": self._web3.to_checksum_address(sender),
            }
        )

    def build_set_storage_ref_tx(
        self,
        data_hash: str,
        storage_ref: str,
        sender: str,
    ) -> dict:
        """
        Build transaction to attach a storage reference to an existing record.

        Set-once: cannot be changed after first write. Owner-only.

        Args:
            data_hash: 64-char hex hash of the registered data.
            storage_ref: 64-char hex storage reference to link.
            sender: Address of the data owner.

        Returns:
            Unsigned transaction dict.
        """
        hash_bytes = _normalize_hash(data_hash)
        ref_bytes = _normalize_hash(storage_ref)
        return self._contract.functions.setStorageRef(
            hash_bytes, ref_bytes
        ).build_transaction(
            {
                "from": self._web3.to_checksum_address(sender),
            }
        )

    def build_record_transformation_tx(
        self,
        original_hash: str,
        new_hash: str,
        description: str,
        sender: str,
    ) -> dict:
        """
        Build transaction to record a data transformation.

        Args:
            original_hash: Hash of the original data.
            new_hash: Hash of the transformed data.
            description: Description of the transformation (max 256 chars).
            sender: Address of the transaction sender.

        Returns:
            Unsigned transaction dict.
        """
        orig_bytes = _normalize_hash(original_hash)
        new_bytes = _normalize_hash(new_hash)
        _validate_transformation(description)
        return self._contract.functions.recordTransformation(
            orig_bytes, new_bytes, description
        ).build_transaction(
            {
                "from": self._web3.to_checksum_address(sender),
            }
        )

    def build_record_access_tx(
        self,
        data_hash: str,
        sender: str,
    ) -> dict:
        """
        Build transaction to record that data was accessed.

        Args:
            data_hash: Hash of the accessed data.
            sender: Address of the accessor.

        Returns:
            Unsigned transaction dict.
        """
        hash_bytes = _normalize_hash(data_hash)
        return self._contract.functions.recordAccess(hash_bytes).build_transaction(
            {
                "from": self._web3.to_checksum_address(sender),
            }
        )

    def build_batch_record_access_tx(
        self,
        data_hashes: List[str],
        sender: str,
    ) -> dict:
        """
        Build transaction to record access to multiple data hashes.

        Args:
            data_hashes: List of data hashes accessed.
            sender: Address of the accessor.

        Returns:
            Unsigned transaction dict.

        Raises:
            ChainValidationError: If batch size exceeds limit.
        """
        if len(data_hashes) > MAX_BATCH_ACCESS:
            raise ChainValidationError(
                f"Batch size {len(data_hashes)} exceeds maximum of {MAX_BATCH_ACCESS}"
            )
        hash_bytes_list = [_normalize_hash(h) for h in data_hashes]
        return self._contract.functions.batchRecordAccess(
            hash_bytes_list
        ).build_transaction(
            {
                "from": self._web3.to_checksum_address(sender),
            }
        )

    def build_set_data_status_tx(
        self,
        data_hash: str,
        status: int,
        sender: str,
    ) -> dict:
        """
        Build transaction to change the status of registered data.

        Args:
            data_hash: Hash of the data.
            status: New status (0=ACTIVE, 1=RESTRICTED, 2=DELETED).
            sender: Address of the data owner.

        Returns:
            Unsigned transaction dict.
        """
        hash_bytes = _normalize_hash(data_hash)
        return self._contract.functions.setDataStatus(
            hash_bytes, status
        ).build_transaction(
            {
                "from": self._web3.to_checksum_address(sender),
            }
        )

    def build_batch_set_data_status_tx(
        self,
        data_hashes: List[str],
        statuses: List[int],
        sender: str,
    ) -> dict:
        """
        Build transaction to change the status of multiple data hashes.

        Args:
            data_hashes: List of data hashes.
            statuses: List of new statuses (0=ACTIVE, 1=RESTRICTED, 2=DELETED).
            sender: Address of the data owner.

        Returns:
            Unsigned transaction dict.

        Raises:
            ChainValidationError: If arrays have different lengths or exceed batch limit.
        """
        if len(data_hashes) != len(statuses):
            raise ChainValidationError(
                f"data_hashes ({len(data_hashes)}) and statuses ({len(statuses)}) "
                "must have the same length"
            )
        if len(data_hashes) > MAX_BATCH_REGISTER:
            raise ChainValidationError(
                f"Batch size {len(data_hashes)} exceeds maximum of {MAX_BATCH_REGISTER}"
            )
        hash_bytes_list = [_normalize_hash(h) for h in data_hashes]
        return self._contract.functions.batchSetDataStatus(
            hash_bytes_list, statuses
        ).build_transaction(
            {
                "from": self._web3.to_checksum_address(sender),
            }
        )

    def build_transfer_ownership_tx(
        self,
        data_hash: str,
        new_owner: str,
        sender: str,
    ) -> dict:
        """
        Build transaction to transfer data ownership.

        Args:
            data_hash: Hash of the data.
            new_owner: Address of the new owner.
            sender: Address of the current owner.

        Returns:
            Unsigned transaction dict.
        """
        hash_bytes = _normalize_hash(data_hash)
        return self._contract.functions.transferDataOwnership(
            hash_bytes,
            self._web3.to_checksum_address(new_owner),
        ).build_transaction(
            {
                "from": self._web3.to_checksum_address(sender),
            }
        )

    def build_set_delegate_tx(
        self,
        delegate: str,
        authorized: bool,
        sender: str,
    ) -> dict:
        """
        Build transaction to authorize or revoke a delegate.

        Args:
            delegate: Address of the delegate.
            authorized: True to authorize, False to revoke.
            sender: Address of the data owner.

        Returns:
            Unsigned transaction dict.
        """
        return self._contract.functions.setDelegate(
            self._web3.to_checksum_address(delegate),
            authorized,
        ).build_transaction(
            {
                "from": self._web3.to_checksum_address(sender),
            }
        )

    # --- Read methods (direct contract calls) ---

    def get_data_record(self, data_hash: str) -> Tuple:
        """
        Get the full on-chain record for a data hash.

        The bundled ABI uses the v2 schema (``TransformationLink[]`` /
        ``tuple[]``).  On v1 contracts where ``getDataRecord`` returns
        ``string[]`` for transformations, the ABI decoder will fail; we
        fall back to ``dataRecords()`` (scalar fields, no arrays).

        Args:
            data_hash: 64-char hex hash.

        Returns:
            Tuple of (dataHash, owner, timestamp, dataType,
                       storageRef, transformations, accessors, status)
            on v3+ contracts (with storageRef at index 4).

            On v2 contracts (no storageRef), returns
            (dataHash, owner, timestamp, dataType,
             transformations, accessors, status) — 7 elements.

            On v1 contracts, field 4 (transformations) is ``string[]``.
            On v2 contracts, field 4 is ``TransformationLink[]``
            (list of tuples ``(bytes32, string)``).
        """
        hash_bytes = _normalize_hash(data_hash)
        try:
            return self._contract.functions.getDataRecord(hash_bytes).call()
        except (OverflowError, Exception) as e:
            # V1 contracts return string[] for transformations but the ABI
            # declares tuple[] (v2), causing a decode error.  Reconstruct
            # the record from the scalar mapping.
            if self.supports_transformation_links():
                raise  # genuine error on v2 — re-raise
            logger.debug("getDataRecord decode failed on v1, using fallback: %s", e)
            return self._get_data_record_v1_fallback(hash_bytes)

    def _get_data_record_v1_fallback(self, hash_bytes: bytes) -> Tuple:
        """Reconstruct getDataRecord result for v1 contracts.

        The v2 ABI expects ``TransformationLink[]`` tuples but v1
        contracts return ``string[]``, causing a decode error.  Falls
        back to ``dataRecords()`` (scalar fields only — no arrays).
        Accessors and transformations are returned as empty lists; callers
        can still get transformations from ``DataTransformed`` events.
        """
        # dataRecords returns: (dataHash, owner, timestamp, dataType, status)
        basic = self._contract.functions.dataRecords(hash_bytes).call()
        data_hash, owner, timestamp, data_type, status = basic

        # Return the same 8-element tuple shape as getDataRecord (v3+)
        # with zero-bytes storageRef at index 4
        return (data_hash, owner, timestamp, data_type, b"\x00" * 32, [], [], status)

    def get_user_data_records(self, user: str) -> List[bytes]:
        """
        Get all data hashes registered by a user.

        Args:
            user: Ethereum address of the user.

        Returns:
            List of bytes32 data hashes.
        """
        return self._contract.functions.getUserDataRecords(
            self._web3.to_checksum_address(user)
        ).call()

    def get_user_data_records_count(self, user: str) -> int:
        """
        Get the number of data records for a user.

        Args:
            user: Ethereum address of the user.

        Returns:
            Count of registered data records.
        """
        return self._contract.functions.getUserDataRecordsCount(
            self._web3.to_checksum_address(user)
        ).call()

    def get_user_data_records_paginated(
        self,
        user: str,
        offset: int,
        limit: int,
    ) -> List[bytes]:
        """
        Get a paginated slice of a user's data records.

        Args:
            user: Ethereum address of the user.
            offset: Starting index.
            limit: Maximum number of records to return.

        Returns:
            List of bytes32 data hashes.
        """
        return self._contract.functions.getUserDataRecordsPaginated(
            self._web3.to_checksum_address(user),
            offset,
            limit,
        ).call()

    def has_address_accessed(self, data_hash: str, accessor: str) -> bool:
        """
        Check if an address has accessed a data hash.

        Args:
            data_hash: 64-char hex hash.
            accessor: Ethereum address to check.

        Returns:
            True if the address has accessed the data.
        """
        hash_bytes = _normalize_hash(data_hash)
        return self._contract.functions.hasAddressAccessed(
            hash_bytes,
            self._web3.to_checksum_address(accessor),
        ).call()

    def is_authorized_delegate(self, owner: str, delegate: str) -> bool:
        """
        Check if an address is an authorized delegate for an owner.

        Args:
            owner: Data owner address.
            delegate: Potential delegate address.

        Returns:
            True if delegate is authorized.
        """
        return self._contract.functions.isAuthorizedDelegate(
            self._web3.to_checksum_address(owner),
            self._web3.to_checksum_address(delegate),
        ).call()

    # --- V2 view methods (available on upgraded contracts only) ---

    def get_transformation_links(self, data_hash: str) -> List[Tuple[bytes, str]]:
        """
        Forward traversal via contract state (v2+).

        Args:
            data_hash: 64-char hex hash.

        Returns:
            List of (newDataHash_bytes, description) tuples.

        Raises:
            Exception: If the contract does not support this function.
        """
        hash_bytes = _normalize_hash(data_hash)
        result = self._contract.functions.getTransformationLinks(hash_bytes).call()
        return [(link[0], link[1]) for link in result]

    def get_child_hashes(self, data_hash: str) -> List[bytes]:
        """
        Lightweight forward traversal: just child hashes (v2+).

        Args:
            data_hash: 64-char hex hash.

        Returns:
            List of newDataHash bytes.

        Raises:
            Exception: If the contract does not support this function.
        """
        hash_bytes = _normalize_hash(data_hash)
        return self._contract.functions.getChildHashes(hash_bytes).call()

    def get_transformation_parents(self, data_hash: str) -> List[bytes]:
        """
        Reverse traversal via contract state (v2+).

        Args:
            data_hash: 64-char hex hash.

        Returns:
            List of parent hash bytes.

        Raises:
            Exception: If the contract does not support this function.
        """
        hash_bytes = _normalize_hash(data_hash)
        return self._contract.functions.getTransformationParents(hash_bytes).call()

    # --- Storage reference read methods ---

    def get_data_hash_by_storage_ref(self, storage_ref: str) -> bytes:
        """
        Reverse lookup: storage reference → data hash.

        Args:
            storage_ref: 64-char hex storage reference.

        Returns:
            bytes32 data hash. Returns 32 zero bytes if no mapping exists.
        """
        ref_bytes = _normalize_hash(storage_ref)
        return self._contract.functions.getDataHashByStorageRef(ref_bytes).call()

    _supports_storage_ref_cache: Optional[bool] = None

    def supports_storage_ref(self) -> bool:
        """Check if the deployed contract supports storageRef functions.

        Performs a trial call on first invocation and caches the result.
        Returns False for older contracts without setStorageRef.
        """
        if self._supports_storage_ref_cache is not None:
            return self._supports_storage_ref_cache
        try:
            test_hash = b"\x00" * 32
            self._contract.functions.getDataHashByStorageRef(test_hash).call()
            self._supports_storage_ref_cache = True
        except Exception:
            self._supports_storage_ref_cache = False
        return self._supports_storage_ref_cache

    _supports_v2: Optional[bool] = None

    def supports_transformation_links(self) -> bool:
        """Check if the deployed contract supports v2 TransformationLink functions.

        Performs a trial call on first invocation and caches the result.
        Returns False for v1 contracts where the function reverts.
        """
        if self._supports_v2 is not None:
            return self._supports_v2
        try:
            test_hash = b"\x00" * 32
            self._contract.functions.getTransformationLinks(test_hash).call()
            self._supports_v2 = True
        except Exception:
            self._supports_v2 = False
        return self._supports_v2

    # --- Merge transaction builder (v2+) ---

    def build_record_merge_transformation_tx(
        self,
        source_hashes: List[str],
        new_hash: str,
        description: str,
        new_data_type: str,
        sender: str,
    ) -> dict:
        """
        Build tx for N-to-1 merge transformation (v2+).

        Args:
            source_hashes: List of original data hashes (2-50 items).
            new_hash: Hash of the merged data.
            description: Transformation description (max 256 chars).
            new_data_type: Data type for the merged result (max 64 chars).
            sender: Address of the transaction sender.

        Returns:
            Unsigned transaction dict.

        Raises:
            ChainValidationError: If source count or formats are invalid.
        """
        if len(source_hashes) < MIN_MERGE_SOURCES:
            raise ChainValidationError(
                f"Merge requires at least {MIN_MERGE_SOURCES} sources, "
                f"got {len(source_hashes)}"
            )
        if len(source_hashes) > MAX_MERGE_SOURCES:
            raise ChainValidationError(
                f"Merge source count {len(source_hashes)} exceeds maximum "
                f"of {MAX_MERGE_SOURCES}"
            )
        source_bytes = [_normalize_hash(h) for h in source_hashes]
        new_bytes = _normalize_hash(new_hash)
        _validate_transformation(description)
        _validate_data_type(new_data_type)
        return self._contract.functions.recordMergeTransformation(
            source_bytes,
            new_bytes,
            description,
            new_data_type,
        ).build_transaction(
            {
                "from": self._web3.to_checksum_address(sender),
            }
        )

    # --- Event queries ---

    # Public RPCs enforce per-request block range limits (Base Sepolia
    # allows ~10k blocks).  We chunk large lookbacks into windows of
    # this size so that the default 50k lookback works reliably.
    _EVENT_CHUNK_SIZE = 10_000

    def _get_logs_chunked(self, event, argument_filters, from_block, to_block):
        """
        Query event logs in chunks to avoid RPC payload limits.

        Splits the range [from_block, to_block] into windows of
        ``_EVENT_CHUNK_SIZE`` and concatenates results.  On 413/payload
        errors the failing chunk is halved once before giving up.

        ``argument_filters`` may be ``None`` to fetch all events of the
        given type (unfiltered scan).
        """
        all_events = []
        start = from_block

        # Build kwargs — omit argument_filters when None so web3 uses
        # its default (no topic filtering beyond the event signature).
        def _get(fb, tb):
            kw = {"from_block": fb, "to_block": tb}
            if argument_filters:
                kw["argument_filters"] = argument_filters
            return event.get_logs(**kw)

        while start <= to_block:
            end = min(start + self._EVENT_CHUNK_SIZE - 1, to_block)
            try:
                all_events.extend(_get(start, end))
            except Exception as e:
                err_str = str(e).lower()
                if "413" in err_str or "payload" in err_str or "too large" in err_str:
                    # Halve the chunk and retry once
                    mid = (start + end) // 2
                    if mid == start:
                        raise  # Can't split further
                    try:
                        all_events.extend(_get(start, mid))
                        all_events.extend(_get(mid + 1, end))
                    except Exception:
                        raise  # Give up on this chunk
                else:
                    raise
            start = end + 1
        return all_events

    def get_transformations_from(
        self,
        data_hash: str,
        lookback_blocks: int = 50_000,
    ) -> List[Tuple[bytes, bytes, str]]:
        """
        Query DataTransformed events where data_hash is the original.

        Args:
            data_hash: 64-char hex hash.
            lookback_blocks: How many blocks to scan backwards from latest.
                Default 50,000 (~28h on Base at 2s/block). Scanned in
                chunks of 10k blocks to stay within public RPC limits.

        Returns:
            List of (originalDataHash, newDataHash, description) tuples.
        """
        hash_bytes = _normalize_hash(data_hash)
        latest = self._web3.eth.block_number
        from_block = max(0, latest - lookback_blocks)
        events = self._get_logs_chunked(
            self._contract.events.DataTransformed,
            {"originalDataHash": hash_bytes},
            from_block,
            latest,
        )
        results = []
        for evt in events:
            results.append(
                (
                    evt.args.originalDataHash,
                    evt.args.newDataHash,
                    evt.args.transformation,
                )
            )
        return results

    def get_transformations_to(
        self,
        data_hash: str,
        lookback_blocks: int = 50_000,
    ) -> List[Tuple[bytes, bytes, str]]:
        """
        Query DataTransformed events where data_hash is the new (reverse lookup).

        Args:
            data_hash: 64-char hex hash.
            lookback_blocks: How many blocks to scan backwards from latest.
                Default 50,000 (~28h on Base at 2s/block). Scanned in
                chunks of 10k blocks to stay within public RPC limits.

        Returns:
            List of (originalDataHash, newDataHash, description) tuples.
        """
        hash_bytes = _normalize_hash(data_hash)
        latest = self._web3.eth.block_number
        from_block = max(0, latest - lookback_blocks)
        events = self._get_logs_chunked(
            self._contract.events.DataTransformed,
            {"newDataHash": hash_bytes},
            from_block,
            latest,
        )
        results = []
        for evt in events:
            results.append(
                (
                    evt.args.originalDataHash,
                    evt.args.newDataHash,
                    evt.args.transformation,
                )
            )
        return results

    def get_all_transformations(
        self,
        from_block: int = 0,
        to_block: int = None,
    ) -> List[Tuple[bytes, bytes, str]]:
        """
        Query ALL DataTransformed events in a block range (no hash filter).

        Used by ``get_provenance_chain`` to build an in-memory index of
        all transformations, enabling BFS traversal without per-node
        event queries.

        Args:
            from_block: Start of the scan range (e.g. contract deploy block).
            to_block: End of the scan range. Defaults to latest block.

        Returns:
            List of (originalDataHash, newDataHash, description) tuples.
        """
        latest = to_block if to_block is not None else self._web3.eth.block_number
        events = self._get_logs_chunked(
            self._contract.events.DataTransformed,
            None,
            from_block,
            latest,
        )
        return [
            (evt.args.originalDataHash, evt.args.newDataHash, evt.args.transformation)
            for evt in events
        ]

    def get_all_merge_events(
        self,
        from_block: int = 0,
        to_block: int = None,
    ) -> list:
        """
        Query ALL DataMerged events in a block range (v2+ contracts).

        Args:
            from_block: Start of the scan range.
            to_block: End of the scan range. Defaults to latest block.

        Returns:
            List of event objects with args: newDataHash, sourceDataHashes,
            transformation, newDataType.  Returns empty list if the
            contract does not emit DataMerged events.
        """
        latest = to_block if to_block is not None else self._web3.eth.block_number
        try:
            return self._get_logs_chunked(
                self._contract.events.DataMerged,
                None,
                from_block,
                latest,
            )
        except Exception:
            # Contract may not have DataMerged event (v1)
            return []

    # --- Gas estimation ---

    def estimate_gas(self, tx: dict) -> int:
        """
        Estimate gas for a transaction.

        Args:
            tx: Transaction dict (from a build_*_tx method).

        Returns:
            Estimated gas units.
        """
        return self._web3.eth.estimate_gas(tx)
