"""
High-level client for on-chain provenance operations.

Provides a facade over ChainProvider, ChainWallet, and DataProvenanceContract
that mirrors the gateway_client.py pattern: simple method calls that handle
gas estimation, signing, broadcasting, and receipt parsing.

Dependencies (web3, eth-account) are included in the default install.
"""

import logging
from typing import List, Optional

from .contract import DataStatus
from .exceptions import (
    ChainTransactionError,
    DataAlreadyRegisteredError,
    DataNotRegisteredError,
    StorageRefAlreadySetError,
    TransformationAlreadyExistsError,
)
from .models import (
    AccessResult,
    AnchorResult,
    ChainProvenanceRecord,
    ChainTransformation,
    ChainWalletInfo,
    DataStatusEnum,
    MergeTransformResult,
    TransformResult,
)

logger = logging.getLogger(__name__)


class ChainClient:
    """High-level client for DataProvenance smart contract operations.

    Wraps provider, wallet, and contract into a single interface for
    anchoring Swarm hashes on-chain, recording transformations and access,
    and querying provenance records.
    """

    def __init__(
        self,
        chain: str = "base-sepolia",
        rpc_url: Optional[str] = None,
        contract_address: Optional[str] = None,
        private_key: Optional[str] = None,
        private_key_env: str = "PROVENANCE_WALLET_KEY",
        gas_limit_multiplier: float = 1.2,
        explorer_url: Optional[str] = None,
        gas_limit: Optional[int] = None,
        rpc_fallbacks: Optional[List[str]] = None,
    ):
        """
        Initialize the chain client.

        Args:
            chain: Chain name ('base-sepolia' or 'base').
            rpc_url: Custom RPC URL. If None, uses preset.
            contract_address: Custom contract address. If None, uses preset.
            private_key: Wallet private key. If None, reads from env var.
            private_key_env: Environment variable name for private key.
            gas_limit_multiplier: Safety multiplier for gas estimates (default 1.2).
            explorer_url: Custom block explorer URL. If None, uses preset.
            gas_limit: Explicit gas limit. If set, skips estimation and multiplier.
            rpc_fallbacks: Fallback RPC URLs tried in order on failure.

        Raises:
            ChainConfigurationError: If dependencies missing or config invalid.
        """
        from .provider import ChainProvider
        from .wallet import ChainWallet
        from .contract import DataProvenanceContract

        self._provider = ChainProvider(
            chain=chain,
            rpc_url=rpc_url,
            contract_address=contract_address,
            explorer_url=explorer_url,
            rpc_fallbacks=rpc_fallbacks,
        )
        self._wallet = ChainWallet(
            private_key=private_key,
            private_key_env=private_key_env,
        )
        self._contract = DataProvenanceContract(
            web3=self._provider.web3,
            contract_address=self._provider.contract_address,
        )
        self._gas_limit_multiplier = gas_limit_multiplier
        self._gas_limit = gas_limit

    @property
    def address(self) -> str:
        """Wallet address."""
        return self._wallet.address

    @property
    def chain(self) -> str:
        """Chain name."""
        return self._provider.chain

    @property
    def contract_address(self) -> str:
        """DataProvenance contract address."""
        return self._provider.contract_address

    # --- Internal helpers ---

    def _send_transaction(self, tx: dict) -> dict:
        """
        Estimate gas, sign, broadcast, and wait for receipt.

        Args:
            tx: Unsigned transaction dict from a build_*_tx method.

        Returns:
            Transaction receipt dict.

        Raises:
            ChainTransactionError: If transaction fails.
        """
        web3 = self._provider.web3

        try:
            # Fill in nonce
            tx["nonce"] = web3.eth.get_transaction_count(self._wallet.address)
            tx["chainId"] = self._provider.chain_id

            # Set gas limit: explicit value or estimate with multiplier
            if self._gas_limit is not None:
                tx["gas"] = self._gas_limit
                logger.debug("Using explicit gas limit: %d", self._gas_limit)
            else:
                estimated_gas = web3.eth.estimate_gas(tx)
                tx["gas"] = int(estimated_gas * self._gas_limit_multiplier)
                logger.debug("Estimated gas: %d, limit: %d", estimated_gas, tx["gas"])

            # Sign and send
            raw_tx = self._wallet.sign_transaction(tx)
            tx_hash = web3.eth.send_raw_transaction(raw_tx)

            logger.debug("Transaction sent: %s", tx_hash.hex())

            # Wait for receipt
            receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

            if receipt["status"] != 1:
                raise ChainTransactionError(
                    "Transaction reverted (status=0)",
                    tx_hash=tx_hash.hex(),
                )

            logger.debug(
                "Transaction confirmed in block %d, gas used: %d",
                receipt["blockNumber"],
                receipt["gasUsed"],
            )

            return receipt

        except ChainTransactionError:
            raise
        except Exception as e:
            tx_hash_str = None
            if "tx_hash" in dir():
                tx_hash_str = tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash)
            raise ChainTransactionError(
                f"Transaction failed: {e}",
                tx_hash=tx_hash_str,
            ) from e

    def _receipt_to_explorer_url(self, receipt: dict) -> Optional[str]:
        """Get explorer URL from a transaction receipt."""
        tx_hash = receipt.get("transactionHash")
        if tx_hash:
            return self._provider.get_explorer_tx_url(tx_hash.hex())
        return None

    # --- Write operations ---

    def anchor(
        self,
        swarm_hash: str,
        data_type: str = "swarm-provenance",
        storage_ref: Optional[str] = None,
    ) -> AnchorResult:
        """
        Anchor a Swarm hash on-chain by registering it in the DataProvenance contract.

        Args:
            swarm_hash: Swarm reference hash (64 hex chars).
            data_type: Data type/category (max 64 chars, default 'swarm-provenance').
            storage_ref: Optional storage reference (64 hex chars) to link
                bidirectionally with the data hash. Enables reverse lookup.

        Returns:
            AnchorResult with transaction details.
        """
        logger.debug(
            "Anchor: hash=%s type=%s storage_ref=%s", swarm_hash, data_type, storage_ref
        )

        # Pre-check: avoid wasting gas on already-registered hashes
        try:
            record = self.get(swarm_hash)
            raise DataAlreadyRegisteredError(
                f"Data hash {swarm_hash} is already registered on-chain",
                data_hash=swarm_hash,
                owner=record.owner,
                timestamp=record.timestamp,
                data_type=record.data_type,
            )
        except DataNotRegisteredError:
            pass  # Not registered -- proceed with anchoring

        if storage_ref:
            tx = self._contract.build_register_data_with_storage_ref_tx(
                data_hash=swarm_hash,
                data_type=data_type,
                storage_ref=storage_ref,
                sender=self._wallet.address,
            )
        else:
            tx = self._contract.build_register_data_tx(
                data_hash=swarm_hash,
                data_type=data_type,
                sender=self._wallet.address,
            )
        try:
            receipt = self._send_transaction(tx)
        except ChainTransactionError as e:
            # Detect "already registered" revert that the pre-check missed
            # (can happen when public RPC serves stale reads)
            if "already registered" in str(e).lower():
                try:
                    record = self.get(swarm_hash)
                    raise DataAlreadyRegisteredError(
                        f"Data hash {swarm_hash} is already registered on-chain",
                        data_hash=swarm_hash,
                        owner=record.owner,
                        timestamp=record.timestamp,
                        data_type=record.data_type,
                    ) from e
                except DataNotRegisteredError:
                    pass  # Still not visible — re-raise original
            raise

        return AnchorResult(
            tx_hash=receipt["transactionHash"].hex(),
            block_number=receipt["blockNumber"],
            gas_used=receipt["gasUsed"],
            explorer_url=self._receipt_to_explorer_url(receipt),
            swarm_hash=swarm_hash,
            data_type=data_type,
            owner=self._wallet.address,
            storage_ref=storage_ref,
        )

    def anchor_for(
        self,
        swarm_hash: str,
        owner: str,
        data_type: str = "swarm-provenance",
    ) -> AnchorResult:
        """
        Anchor a Swarm hash on behalf of another owner.

        Caller must be an authorized delegate of the owner.

        Args:
            swarm_hash: Swarm reference hash.
            owner: Address of the actual data owner.
            data_type: Data type/category.

        Returns:
            AnchorResult with transaction details.
        """
        logger.debug("Anchor for: hash=%s owner=%s", swarm_hash, owner)

        # Pre-check: avoid wasting gas on already-registered hashes
        try:
            record = self.get(swarm_hash)
            raise DataAlreadyRegisteredError(
                f"Data hash {swarm_hash} is already registered on-chain",
                data_hash=swarm_hash,
                owner=record.owner,
                timestamp=record.timestamp,
                data_type=record.data_type,
            )
        except DataNotRegisteredError:
            pass  # Not registered -- proceed with anchoring

        tx = self._contract.build_register_data_for_tx(
            data_hash=swarm_hash,
            data_type=data_type,
            actual_owner=owner,
            sender=self._wallet.address,
        )
        receipt = self._send_transaction(tx)

        return AnchorResult(
            tx_hash=receipt["transactionHash"].hex(),
            block_number=receipt["blockNumber"],
            gas_used=receipt["gasUsed"],
            explorer_url=self._receipt_to_explorer_url(receipt),
            swarm_hash=swarm_hash,
            data_type=data_type,
            owner=owner,
        )

    def batch_anchor(
        self,
        swarm_hashes: List[str],
        data_types: List[str],
    ) -> AnchorResult:
        """
        Anchor multiple Swarm hashes in a single transaction.

        Args:
            swarm_hashes: List of Swarm reference hashes.
            data_types: List of data type strings (same length).

        Returns:
            AnchorResult for the batch transaction (swarm_hash is first hash).
        """
        logger.debug("Batch anchor: count=%d", len(swarm_hashes))

        tx = self._contract.build_batch_register_data_tx(
            data_hashes=swarm_hashes,
            data_types=data_types,
            sender=self._wallet.address,
        )
        receipt = self._send_transaction(tx)

        return AnchorResult(
            tx_hash=receipt["transactionHash"].hex(),
            block_number=receipt["blockNumber"],
            gas_used=receipt["gasUsed"],
            explorer_url=self._receipt_to_explorer_url(receipt),
            swarm_hash=swarm_hashes[0] if swarm_hashes else "",
            data_type=data_types[0] if data_types else "",
            owner=self._wallet.address,
        )

    def transform(
        self,
        original_hash: str,
        new_hash: str,
        description: str,
    ) -> TransformResult:
        """
        Record a data transformation on-chain.

        Args:
            original_hash: Hash of the original data.
            new_hash: Hash of the transformed data.
            description: Transformation description (max 256 chars).

        Returns:
            TransformResult with transaction details.
        """
        logger.debug(
            "Transform: original=%s new=%s desc=%s",
            original_hash,
            new_hash,
            description,
        )

        # Pre-check: avoid wasting gas on duplicate transformations.
        # Prefer state read on v2 contracts, fall back to event cache.
        if self._contract.supports_transformation_links():
            try:
                links = self._contract.get_transformation_links(original_hash)
                for existing_new, existing_desc in links:
                    existing_hex = (
                        existing_new.hex()
                        if isinstance(existing_new, bytes)
                        else str(existing_new)
                    )
                    if existing_hex == new_hash:
                        raise TransformationAlreadyExistsError(
                            f"Transformation {original_hash[:16]}… → {new_hash[:16]}… already exists",
                            original_hash=original_hash,
                            new_hash=new_hash,
                            existing_description=existing_desc,
                        )
            except TransformationAlreadyExistsError:
                raise
            except Exception as e:
                logger.warning("State-based duplicate check failed: %s", e)
        else:
            deploy_block = self._provider.deploy_block
            if deploy_block is not None:
                try:
                    from .event_cache import get_cache

                    cache = get_cache(
                        self._provider.chain, self._provider.contract_address
                    )
                    current_block = self._provider.web3.eth.block_number
                    forward, _ = cache.get_maps(
                        self._contract, deploy_block, current_block
                    )
                    for existing_new, existing_desc in forward.get(original_hash, []):
                        if existing_new == new_hash:
                            raise TransformationAlreadyExistsError(
                                f"Transformation {original_hash[:16]}… → {new_hash[:16]}… already exists",
                                original_hash=original_hash,
                                new_hash=new_hash,
                                existing_description=existing_desc,
                            )
                except TransformationAlreadyExistsError:
                    raise
                except Exception as e:
                    logger.warning("Duplicate check failed, proceeding: %s", e)

        tx = self._contract.build_record_transformation_tx(
            original_hash=original_hash,
            new_hash=new_hash,
            description=description,
            sender=self._wallet.address,
        )
        receipt = self._send_transaction(tx)

        return TransformResult(
            tx_hash=receipt["transactionHash"].hex(),
            block_number=receipt["blockNumber"],
            gas_used=receipt["gasUsed"],
            explorer_url=self._receipt_to_explorer_url(receipt),
            original_hash=original_hash,
            new_hash=new_hash,
            description=description,
        )

    def access(
        self,
        swarm_hash: str,
    ) -> AccessResult:
        """
        Record that data was accessed.

        Args:
            swarm_hash: Hash of the accessed data.

        Returns:
            AccessResult with transaction details.
        """
        logger.debug("Record access: hash=%s", swarm_hash)

        tx = self._contract.build_record_access_tx(
            data_hash=swarm_hash,
            sender=self._wallet.address,
        )
        receipt = self._send_transaction(tx)

        return AccessResult(
            tx_hash=receipt["transactionHash"].hex(),
            block_number=receipt["blockNumber"],
            gas_used=receipt["gasUsed"],
            explorer_url=self._receipt_to_explorer_url(receipt),
            swarm_hash=swarm_hash,
            accessor=self._wallet.address,
        )

    def batch_access(
        self,
        swarm_hashes: List[str],
    ) -> AccessResult:
        """
        Record access to multiple data hashes in a single transaction.

        Args:
            swarm_hashes: List of accessed data hashes.

        Returns:
            AccessResult for the batch transaction.
        """
        logger.debug("Batch access: count=%d", len(swarm_hashes))

        tx = self._contract.build_batch_record_access_tx(
            data_hashes=swarm_hashes,
            sender=self._wallet.address,
        )
        receipt = self._send_transaction(tx)

        return AccessResult(
            tx_hash=receipt["transactionHash"].hex(),
            block_number=receipt["blockNumber"],
            gas_used=receipt["gasUsed"],
            explorer_url=self._receipt_to_explorer_url(receipt),
            swarm_hash=swarm_hashes[0] if swarm_hashes else "",
            accessor=self._wallet.address,
        )

    def set_status(
        self,
        swarm_hash: str,
        status: int,
    ) -> AnchorResult:
        """
        Set the status of a registered data hash.

        Args:
            swarm_hash: Hash of the data.
            status: New status (0=ACTIVE, 1=RESTRICTED, 2=DELETED).

        Returns:
            AnchorResult with transaction details.
        """
        logger.debug(
            "Set status: hash=%s status=%s", swarm_hash, DataStatus(status).name
        )

        tx = self._contract.build_set_data_status_tx(
            data_hash=swarm_hash,
            status=status,
            sender=self._wallet.address,
        )
        receipt = self._send_transaction(tx)

        return AnchorResult(
            tx_hash=receipt["transactionHash"].hex(),
            block_number=receipt["blockNumber"],
            gas_used=receipt["gasUsed"],
            explorer_url=self._receipt_to_explorer_url(receipt),
            swarm_hash=swarm_hash,
            data_type="",
            owner=self._wallet.address,
        )

    def batch_set_status(
        self,
        swarm_hashes: List[str],
        statuses: List[int],
    ) -> AnchorResult:
        """
        Set the status of multiple registered data hashes in a single transaction.

        Args:
            swarm_hashes: List of data hashes.
            statuses: List of new statuses (0=ACTIVE, 1=RESTRICTED, 2=DELETED).

        Returns:
            AnchorResult with transaction details.
        """
        logger.debug("Batch set status: count=%d", len(swarm_hashes))

        tx = self._contract.build_batch_set_data_status_tx(
            data_hashes=swarm_hashes,
            statuses=statuses,
            sender=self._wallet.address,
        )
        receipt = self._send_transaction(tx)

        return AnchorResult(
            tx_hash=receipt["transactionHash"].hex(),
            block_number=receipt["blockNumber"],
            gas_used=receipt["gasUsed"],
            explorer_url=self._receipt_to_explorer_url(receipt),
            swarm_hash=swarm_hashes[0] if swarm_hashes else "",
            data_type="",
            owner=self._wallet.address,
        )

    def transfer_ownership(
        self,
        swarm_hash: str,
        new_owner: str,
    ) -> AnchorResult:
        """
        Transfer ownership of a data hash to a new address.

        Args:
            swarm_hash: Hash of the data.
            new_owner: Address of the new owner.

        Returns:
            AnchorResult with transaction details.
        """
        logger.debug("Transfer ownership: hash=%s new_owner=%s", swarm_hash, new_owner)

        tx = self._contract.build_transfer_ownership_tx(
            data_hash=swarm_hash,
            new_owner=new_owner,
            sender=self._wallet.address,
        )
        receipt = self._send_transaction(tx)

        return AnchorResult(
            tx_hash=receipt["transactionHash"].hex(),
            block_number=receipt["blockNumber"],
            gas_used=receipt["gasUsed"],
            explorer_url=self._receipt_to_explorer_url(receipt),
            swarm_hash=swarm_hash,
            data_type="",
            owner=new_owner,
        )

    def set_delegate(
        self,
        delegate: str,
        authorized: bool = True,
    ) -> AnchorResult:
        """
        Authorize or revoke a delegate address.

        Args:
            delegate: Address to authorize/revoke.
            authorized: True to authorize, False to revoke.

        Returns:
            AnchorResult with transaction details.
        """
        action = "Authorize" if authorized else "Revoke"
        logger.debug("%s delegate: %s", action, delegate)

        tx = self._contract.build_set_delegate_tx(
            delegate=delegate,
            authorized=authorized,
            sender=self._wallet.address,
        )
        receipt = self._send_transaction(tx)

        return AnchorResult(
            tx_hash=receipt["transactionHash"].hex(),
            block_number=receipt["blockNumber"],
            gas_used=receipt["gasUsed"],
            explorer_url=self._receipt_to_explorer_url(receipt),
            swarm_hash="",
            data_type="",
            owner=self._wallet.address,
        )

    def set_storage_ref(
        self,
        data_hash: str,
        storage_ref: str,
    ) -> AnchorResult:
        """
        Attach a storage reference to an existing on-chain record.

        Set-once: the storage reference cannot be changed after it is set.
        Owner-only: only the data owner can call this.

        Args:
            data_hash: Hash of the registered data (64 hex chars).
            storage_ref: Storage reference to link (64 hex chars, e.g. Swarm ref).

        Returns:
            AnchorResult with transaction details.

        Raises:
            DataNotRegisteredError: If data_hash is not registered on-chain.
            StorageRefAlreadySetError: If a storage ref is already set for this hash.
        """
        logger.debug("Set storage ref: hash=%s ref=%s", data_hash, storage_ref)

        # Pre-check: verify hash is registered and storage ref is not already set
        record = self.get(data_hash)
        if record.storage_ref:
            raise StorageRefAlreadySetError(
                f"Storage reference already set for {data_hash}",
                data_hash=data_hash,
                existing_storage_ref=record.storage_ref,
            )

        tx = self._contract.build_set_storage_ref_tx(
            data_hash=data_hash,
            storage_ref=storage_ref,
            sender=self._wallet.address,
        )
        try:
            receipt = self._send_transaction(tx)
        except ChainTransactionError as e:
            if "already set" in str(e).lower() or "storage ref" in str(e).lower():
                raise StorageRefAlreadySetError(
                    f"Storage reference already set for {data_hash}",
                    data_hash=data_hash,
                ) from e
            raise

        return AnchorResult(
            tx_hash=receipt["transactionHash"].hex(),
            block_number=receipt["blockNumber"],
            gas_used=receipt["gasUsed"],
            explorer_url=self._receipt_to_explorer_url(receipt),
            swarm_hash=data_hash,
            data_type=record.data_type,
            owner=self._wallet.address,
            storage_ref=storage_ref,
        )

    def lookup_by_storage_ref(
        self,
        storage_ref: str,
    ) -> Optional[ChainProvenanceRecord]:
        """
        Reverse lookup: find the provenance record by storage reference.

        Read-only — no wallet or gas required.

        Args:
            storage_ref: Storage reference hash (64 hex chars).

        Returns:
            ChainProvenanceRecord if found, None if no mapping exists.
        """
        logger.debug("Lookup by storage ref: %s", storage_ref)

        data_hash_bytes = self._contract.get_data_hash_by_storage_ref(storage_ref)

        # Check if result is zero bytes (no mapping)
        if data_hash_bytes == b"\x00" * 32:
            return None

        data_hash_hex = data_hash_bytes.hex()
        return self.get(data_hash_hex)

    def merge_transform(
        self,
        source_hashes: List[str],
        new_hash: str,
        description: str,
        new_data_type: str = "merged",
    ) -> MergeTransformResult:
        """
        Record an N-to-1 merge transformation on-chain.

        All source hashes must be registered. The new_hash is registered
        automatically by the contract.

        Args:
            source_hashes: List of original data hashes (2-50).
            new_hash: Hash of the merged data.
            description: Transformation description (max 256 chars).
            new_data_type: Data type for merged result (max 64 chars).

        Returns:
            MergeTransformResult with transaction details.
        """
        logger.debug(
            "Merge transform: sources=%d new=%s desc=%s",
            len(source_hashes),
            new_hash,
            description,
        )

        tx = self._contract.build_record_merge_transformation_tx(
            source_hashes=source_hashes,
            new_hash=new_hash,
            description=description,
            new_data_type=new_data_type,
            sender=self._wallet.address,
        )
        receipt = self._send_transaction(tx)

        return MergeTransformResult(
            tx_hash=receipt["transactionHash"].hex(),
            block_number=receipt["blockNumber"],
            gas_used=receipt["gasUsed"],
            explorer_url=self._receipt_to_explorer_url(receipt),
            source_hashes=source_hashes,
            new_hash=new_hash,
            description=description,
            new_data_type=new_data_type,
        )

    # --- Read operations ---

    def get(
        self,
        swarm_hash: str,
    ) -> ChainProvenanceRecord:
        """
        Get the on-chain provenance record for a Swarm hash.

        Args:
            swarm_hash: Swarm reference hash.

        Returns:
            ChainProvenanceRecord with full provenance data.

        Raises:
            DataNotRegisteredError: If hash is not registered on-chain.
        """
        logger.debug("Get record: hash=%s", swarm_hash)

        record = self._contract.get_data_record(swarm_hash)

        # record is a tuple — v3+ (with storageRef) returns 8 elements:
        #   (dataHash, owner, timestamp, dataType, storageRef,
        #    transformations, accessors, status)
        # Older contracts return 7 elements (no storageRef).
        if len(record) >= 8:
            (
                data_hash_bytes,
                owner,
                timestamp,
                data_type,
                storage_ref_bytes,
                transformations,
                accessors,
                status,
            ) = record[:8]
        else:
            (
                data_hash_bytes,
                owner,
                timestamp,
                data_type,
                transformations,
                accessors,
                status,
            ) = record[:7]
            storage_ref_bytes = None

        # Check if data is registered (owner is zero address if not)
        zero_address = "0x" + "0" * 40
        if owner == zero_address:
            raise DataNotRegisteredError(
                f"Data hash {swarm_hash} is not registered on-chain",
                data_hash=swarm_hash,
            )

        # Parse transformations — v2 contracts return TransformationLink[]
        # (list of (bytes32, string) tuples), v1 returns string[].
        chain_transformations = []
        for t in transformations:
            if isinstance(t, (tuple, list)) and len(t) >= 2:
                # V2 contract: TransformationLink(newDataHash, description)
                new_hash = t[0].hex() if isinstance(t[0], bytes) else str(t[0])
                chain_transformations.append(
                    ChainTransformation(description=str(t[1]), new_data_hash=new_hash)
                )
            else:
                # V1 contract: plain string description
                chain_transformations.append(ChainTransformation(description=str(t)))

        logger.debug(
            "Record: owner=%s type=%s status=%s accessors=%d",
            owner,
            data_type,
            DataStatus(status).name,
            len(accessors),
        )

        # Parse storage_ref — non-zero bytes32 means a ref is set
        parsed_storage_ref = None
        if storage_ref_bytes and isinstance(storage_ref_bytes, bytes):
            if storage_ref_bytes != b"\x00" * 32:
                parsed_storage_ref = storage_ref_bytes.hex()

        return ChainProvenanceRecord(
            data_hash=(
                data_hash_bytes.hex()
                if isinstance(data_hash_bytes, bytes)
                else str(data_hash_bytes)
            ),
            owner=owner,
            timestamp=timestamp,
            data_type=data_type,
            storage_ref=parsed_storage_ref,
            status=DataStatusEnum(status),
            accessors=list(accessors),
            transformations=chain_transformations,
        )

    def verify(
        self,
        swarm_hash: str,
    ) -> bool:
        """
        Verify that a Swarm hash is registered on-chain.

        Args:
            swarm_hash: Swarm reference hash.

        Returns:
            True if registered, False if not.
        """
        try:
            self.get(swarm_hash)
            return True
        except DataNotRegisteredError:
            return False

    def balance(self) -> ChainWalletInfo:
        """
        Get wallet balance and chain info.

        Returns:
            ChainWalletInfo with balance and chain details.
        """
        logger.debug("Balance check")

        balance_wei = self._wallet.get_balance(self._provider.web3)
        balance_eth = self._wallet.get_balance_eth(self._provider.web3)

        logger.debug("Address: %s Balance: %s ETH", self._wallet.address, balance_eth)

        return ChainWalletInfo(
            address=self._wallet.address,
            balance_wei=balance_wei,
            balance_eth=balance_eth,
            chain=self._provider.chain,
            contract_address=self._provider.contract_address,
        )

    def health_check(self) -> bool:
        """
        Check if the chain provider is connected and healthy.

        Returns:
            True if healthy.

        Raises:
            ChainConnectionError: If health check fails.
        """
        logger.debug(
            "Chain health check: chain=%s rpc=%s",
            self._provider.chain,
            self._provider.rpc_url,
        )

        result = self._provider.health_check()

        logger.debug("Connected, block: %d", self._provider.get_block_number())

        return result

    def get_provenance_chain(
        self,
        swarm_hash: str,
        max_depth: Optional[int] = None,
    ) -> List[ChainProvenanceRecord]:
        """
        Get the provenance chain for a data hash.

        On v2 contracts, uses state reads (``getTransformationLinks`` /
        ``getTransformationParents``) for traversal — no event scanning.
        On v1 contracts, falls back to the cached event scan path.
        Falls back to per-node event scanning when the deploy block is unknown.

        Args:
            swarm_hash: Starting Swarm reference hash.
            max_depth: Maximum traversal depth. None means no limit (capped at 50).

        Returns:
            List of ChainProvenanceRecord forming the provenance chain,
            starting with the given hash.
        """
        logger.debug(
            "Get provenance chain: hash=%s max_depth=%s", swarm_hash, max_depth
        )

        effective_max = max_depth if max_depth is not None else 50

        # Detect contract version — state reads are much faster than event scans
        use_state_reads = False
        try:
            if self._contract.supports_transformation_links():
                use_state_reads = True
        except Exception:
            pass

        # Fall back to event cache for v1 contracts
        from .event_cache import get_cache

        forward = {}  # original_hex -> [(new_hex, desc)]
        reverse = {}  # new_hex -> [(original_hex, desc)]
        deploy_block = self._provider.deploy_block
        use_local_index = False

        if not use_state_reads and deploy_block is not None:
            try:
                cache = get_cache(self._provider.chain, self._provider.contract_address)
                current_block = self._provider.web3.eth.block_number
                forward, reverse = cache.get_maps(
                    self._contract, deploy_block, current_block
                )
                use_local_index = True
            except Exception as e:
                logger.warning(
                    "Full event scan failed, falling back to per-node queries: %s",
                    e,
                )

        chain = []
        visited = set()
        to_visit = [(swarm_hash, 0)]

        while to_visit:
            current_hash, current_depth = to_visit.pop(0)
            if current_hash in visited:
                continue
            if current_depth > effective_max:
                logger.debug("Depth limit reached at %d", current_depth)
                continue
            visited.add(current_hash)

            try:
                record = self.get(current_hash)
            except DataNotRegisteredError:
                logger.debug("Hash %s not registered, skipping", current_hash)
                continue

            if use_state_reads:
                # V2 contract: state-based traversal
                try:
                    links = self._contract.get_transformation_links(current_hash)
                    if links:
                        record.transformations = [
                            ChainTransformation(
                                description=desc,
                                new_data_hash=(
                                    nh.hex() if isinstance(nh, bytes) else str(nh)
                                ),
                            )
                            for nh, desc in links
                        ]
                        for nh, _ in links:
                            nh_hex = nh.hex() if isinstance(nh, bytes) else str(nh)
                            if nh_hex not in visited:
                                to_visit.append((nh_hex, current_depth + 1))
                except Exception as e:
                    logger.warning(
                        "State-based forward read failed for %s: %s",
                        current_hash,
                        e,
                    )
                # Reverse: parents via state
                try:
                    parents = self._contract.get_transformation_parents(current_hash)
                    for p in parents:
                        p_hex = p.hex() if isinstance(p, bytes) else str(p)
                        if p_hex not in visited:
                            to_visit.append((p_hex, current_depth + 1))
                except Exception as e:
                    logger.warning(
                        "State-based reverse read failed for %s: %s",
                        current_hash,
                        e,
                    )
            elif use_local_index:
                # V1 contract: use pre-built event cache index
                fwd = forward.get(current_hash, [])
                if fwd:
                    record.transformations = [
                        ChainTransformation(description=desc, new_data_hash=nh)
                        for nh, desc in fwd
                    ]
                    for nh, _ in fwd:
                        if nh not in visited:
                            to_visit.append((nh, current_depth + 1))
                # Reverse links (parents)
                for orig_hex, _ in reverse.get(current_hash, []):
                    if orig_hex not in visited:
                        to_visit.append((orig_hex, current_depth + 1))
            else:
                # Per-node event scanning (fallback when deploy_block unknown)
                try:
                    events = self._contract.get_transformations_from(current_hash)
                    enriched = []
                    for orig_bytes, new_bytes, desc in events:
                        new_hash = (
                            new_bytes.hex()
                            if isinstance(new_bytes, bytes)
                            else str(new_bytes)
                        )
                        enriched.append(
                            ChainTransformation(
                                description=desc,
                                new_data_hash=new_hash,
                            )
                        )
                        if new_hash not in visited:
                            to_visit.append((new_hash, current_depth + 1))
                    if enriched:
                        record.transformations = enriched
                except Exception as e:
                    logger.warning("Event query failed for %s: %s", current_hash, e)
                    for t in record.transformations:
                        if t.new_data_hash and t.new_data_hash not in visited:
                            to_visit.append((t.new_data_hash, current_depth + 1))

                try:
                    reverse_events = self._contract.get_transformations_to(current_hash)
                    for orig_bytes, new_bytes, desc in reverse_events:
                        orig_hash = (
                            orig_bytes.hex()
                            if isinstance(orig_bytes, bytes)
                            else str(orig_bytes)
                        )
                        if orig_hash not in visited:
                            to_visit.append((orig_hash, current_depth + 1))
                except Exception as e:
                    logger.warning(
                        "Reverse event query failed for %s: %s",
                        current_hash,
                        e,
                    )

            chain.append(record)

        logger.debug("Chain length: %d", len(chain))

        return chain
