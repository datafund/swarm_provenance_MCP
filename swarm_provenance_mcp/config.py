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
        description="URL of the swarm_connect FastAPI gateway"
    )

    # Default stamp parameters
    default_stamp_amount: int = Field(
        default=2000000000,
        env="DEFAULT_STAMP_AMOUNT",
        description="Default amount for new postage stamps (in wei)"
    )

    default_stamp_depth: int = Field(
        default=17,
        env="DEFAULT_STAMP_DEPTH",
        description="Default depth for new postage stamps"
    )

    # MCP Server Configuration
    mcp_server_name: str = Field(
        default="swarm-provenance-mcp",
        env="MCP_SERVER_NAME",
        description="Name of the MCP server"
    )

    mcp_server_version: str = Field(
        default="0.1.0",
        env="MCP_SERVER_VERSION",
        description="Version of the MCP server"
    )

    # Payment Configuration
    payment_mode: str = Field(
        default="free",
        env="PAYMENT_MODE",
        description="Payment mode for gateway requests (free = rate-limited free tier)"
    )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# Global settings instance
settings = Settings()