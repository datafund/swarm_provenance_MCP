"""Custom exceptions for blockchain-related operations.

Standalone exception hierarchy for the chain module. These do not inherit
from any MCP-specific base class, keeping the chain module self-contained.
"""


class ChainError(Exception):
    """Base exception for blockchain-related errors."""

    pass


class ChainConfigurationError(ChainError):
    """Missing dependencies, invalid config, or missing wallet key."""

    pass


class ChainConnectionError(ChainError):
    """Failed to connect to RPC endpoint."""

    def __init__(self, message: str, rpc_url: str = None):
        super().__init__(message)
        self.rpc_url = rpc_url


class ChainTransactionError(ChainError):
    """Transaction reverted, ran out of gas, or otherwise failed."""

    def __init__(self, message: str, tx_hash: str = None):
        super().__init__(message)
        self.tx_hash = tx_hash


class ChainValidationError(ChainError):
    """Input validation failed (hash format, string lengths, batch limits)."""

    pass


class DataNotRegisteredError(ChainError):
    """Data hash not found on-chain."""

    def __init__(self, message: str, data_hash: str = None):
        super().__init__(message)
        self.data_hash = data_hash


class DataAlreadyRegisteredError(ChainError):
    """Data hash is already registered on-chain."""

    def __init__(
        self,
        message: str,
        data_hash: str = None,
        owner: str = None,
        timestamp: int = None,
        data_type: str = None,
    ):
        super().__init__(message)
        self.data_hash = data_hash
        self.owner = owner
        self.timestamp = timestamp
        self.data_type = data_type
