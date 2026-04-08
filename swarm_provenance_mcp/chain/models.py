"""Pydantic models for chain/blockchain operations.

Kept in the chain module (not shared models.py) to maintain a clean
optional dependency boundary — these models are only used when
blockchain features are enabled.
"""

from enum import IntEnum
from typing import List, Optional

from pydantic import BaseModel, Field


class DataStatusEnum(IntEnum):
    """On-chain data status values."""

    ACTIVE = 0
    RESTRICTED = 1
    DELETED = 2


class ChainTransformation(BaseModel):
    """A transformation recorded on-chain."""

    description: str = Field(description="Description of the transformation")
    new_data_hash: Optional[str] = Field(
        default=None, description="Hash of the transformed data (if available)"
    )


class ChainProvenanceRecord(BaseModel):
    """On-chain provenance record for a data hash."""

    data_hash: str = Field(description="Swarm reference hash (bytes32 hex)")
    owner: str = Field(description="Ethereum address of data owner")
    timestamp: int = Field(description="Unix timestamp when registered")
    data_type: str = Field(description="Type/category of the data")
    status: DataStatusEnum = Field(description="Current data status")
    accessors: List[str] = Field(
        default_factory=list, description="Addresses that accessed this data"
    )
    storage_ref: Optional[str] = Field(
        default=None,
        description="Swarm storage reference linked to this data hash (bytes32 hex, if set)",
    )
    transformations: List[ChainTransformation] = Field(
        default_factory=list, description="Transformations derived from this data"
    )


class AnchorResult(BaseModel):
    """Result from anchoring a Swarm hash on-chain."""

    tx_hash: str = Field(description="Transaction hash")
    block_number: int = Field(description="Block number containing the transaction")
    gas_used: int = Field(description="Gas consumed by the transaction")
    explorer_url: Optional[str] = Field(
        default=None, description="Block explorer URL for the transaction"
    )
    swarm_hash: str = Field(description="The anchored Swarm reference hash")
    data_type: str = Field(description="Data type registered on-chain")
    owner: str = Field(description="Owner address of the registered data")
    storage_ref: Optional[str] = Field(
        default=None,
        description="Storage reference linked during anchoring (if provided)",
    )


class TransformResult(BaseModel):
    """Result from recording a data transformation on-chain."""

    tx_hash: str = Field(description="Transaction hash")
    block_number: int = Field(description="Block number containing the transaction")
    gas_used: int = Field(description="Gas consumed by the transaction")
    explorer_url: Optional[str] = Field(
        default=None, description="Block explorer URL for the transaction"
    )
    original_hash: str = Field(description="Original data hash")
    new_hash: str = Field(description="New (transformed) data hash")
    description: str = Field(description="Transformation description")


class MergeTransformResult(BaseModel):
    """Result from recording an N-to-1 merge transformation on-chain."""

    tx_hash: str = Field(description="Transaction hash")
    block_number: int = Field(description="Block number containing the transaction")
    gas_used: int = Field(description="Gas consumed by the transaction")
    explorer_url: Optional[str] = Field(
        default=None, description="Block explorer URL for the transaction"
    )
    source_hashes: List[str] = Field(
        description="Original data hashes that were merged"
    )
    new_hash: str = Field(description="New (merged) data hash")
    description: str = Field(description="Transformation description")
    new_data_type: str = Field(description="Data type of the merged result")


class AccessResult(BaseModel):
    """Result from recording a data access on-chain."""

    tx_hash: str = Field(description="Transaction hash")
    block_number: int = Field(description="Block number containing the transaction")
    gas_used: int = Field(description="Gas consumed by the transaction")
    explorer_url: Optional[str] = Field(
        default=None, description="Block explorer URL for the transaction"
    )
    swarm_hash: str = Field(description="The accessed Swarm reference hash")
    accessor: str = Field(description="Address of the accessor")


class ChainWalletInfo(BaseModel):
    """Wallet information for chain operations."""

    address: str = Field(description="Ethereum wallet address")
    balance_wei: int = Field(description="Balance in wei")
    balance_eth: str = Field(description="Balance formatted as ETH string")
    chain: str = Field(description="Chain name (e.g., 'base-sepolia')")
    contract_address: str = Field(description="DataProvenance contract address")
