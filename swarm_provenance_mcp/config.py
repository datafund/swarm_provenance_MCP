"""Configuration management for the Swarm Provenance MCP server."""

import os
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Configuration settings for the MCP server."""

    # Swarm Gateway Configuration
    swarm_gateway_url: str = Field(
        default="https://provenance-gateway.datafund.io",
        env="SWARM_GATEWAY_URL",
        description="URL of the swarm_connect FastAPI gateway",
    )

    # Default stamp parameters
    default_stamp_duration_hours: int = Field(
        default=25,
        env="DEFAULT_STAMP_DURATION_HOURS",
        description="Default duration in hours for new postage stamps (minimum 24)",
    )

    default_stamp_size: str = Field(
        default="small",
        env="DEFAULT_STAMP_SIZE",
        description="Default size preset for new postage stamps (small, medium, large)",
    )

    # MCP Server Configuration
    mcp_server_name: str = Field(
        default="swarm-provenance-mcp",
        env="MCP_SERVER_NAME",
        description="Name of the MCP server",
    )

    mcp_server_version: str = Field(
        default="0.1.0",
        env="MCP_SERVER_VERSION",
        description="Version of the MCP server",
    )

    # Payment Configuration
    payment_mode: str = Field(
        default="free",
        env="PAYMENT_MODE",
        description="Payment mode for gateway requests (free = rate-limited free tier)",
    )

    # Chain Anchoring Configuration
    chain_enabled: bool = Field(
        default=False,
        env="CHAIN_ENABLED",
        description="Enable on-chain provenance anchoring via DataProvenance contract",
    )

    chain_name: str = Field(
        default="base-sepolia",
        env="CHAIN_NAME",
        description="Blockchain network name (base-sepolia or base)",
    )

    provenance_wallet_key: Optional[str] = Field(
        default=None,
        env="PROVENANCE_WALLET_KEY",
        description="Private key for signing chain transactions (hex, with or without 0x)",
    )

    chain_rpc_url: Optional[str] = Field(
        default=None,
        env="CHAIN_RPC_URL",
        description="Custom RPC endpoint URL (uses chain preset if not set)",
    )

    chain_rpc_urls: Optional[str] = Field(
        default=None,
        env="CHAIN_RPC_URLS",
        description="Comma-separated fallback RPC URLs, tried in order after CHAIN_RPC_URL",
    )

    chain_contract_address: Optional[str] = Field(
        default=None,
        env="CHAIN_CONTRACT",
        description="Custom DataProvenance contract address (uses chain preset if not set)",
    )

    chain_explorer_url: Optional[str] = Field(
        default=None,
        env="CHAIN_EXPLORER_URL",
        description="Custom block explorer URL (uses chain preset if not set)",
    )

    chain_gas_limit: Optional[int] = Field(
        default=None,
        env="CHAIN_GAS_LIMIT",
        description="Explicit gas limit for chain transactions (skips estimation if set)",
    )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# Global settings instance
settings = Settings()
