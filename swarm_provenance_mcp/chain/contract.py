"""
DataProvenance smart contract wrapper.

Provides typed Python methods for all DataProvenance contract functions.
Build methods return transaction dicts; read methods call directly.

Requires optional dependencies: pip install -e .[blockchain]
"""

import json
from enum import IntEnum
from pathlib import Path
from typing import List, Tuple

from .exceptions import ChainConfigurationError, ChainValidationError

# --- Constants ---
# Client-side validation limits (not enforced on-chain — Solidity strings are
# dynamically sized, so these are reasonable guard rails to prevent wasted gas).
MAX_DATA_TYPE_LENGTH = 64
MAX_TRANSFORMATION_LENGTH = 256

# Client-side batch limits. The contract does not enforce per-call batch sizes
# but larger batches risk exceeding the block gas limit.
MAX_BATCH_REGISTER = 50
MAX_BATCH_ACCESS = 100

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

        Args:
            data_hash: 64-char hex hash.

        Returns:
            Tuple of (dataHash, owner, timestamp, dataType,
                       transformations, accessors, status).
        """
        hash_bytes = _normalize_hash(data_hash)
        return self._contract.functions.getDataRecord(hash_bytes).call()

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
