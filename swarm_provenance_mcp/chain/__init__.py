"""Chain subpackage for blockchain integration with DataProvenance contract."""

try:
    import eth_account  # noqa: F401
    import web3  # noqa: F401

    from .client import ChainClient

    CHAIN_AVAILABLE = True
    __all__ = ["CHAIN_AVAILABLE", "ChainClient"]
except ImportError:
    CHAIN_AVAILABLE = False
    ChainClient = None
    __all__ = ["CHAIN_AVAILABLE"]
