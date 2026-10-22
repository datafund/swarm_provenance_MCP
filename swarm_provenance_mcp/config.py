"""Configuration management for the Swarm Provenance MCP server."""

import logging
import os
from pathlib import Path
from typing import Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Environment variable naming an explicit env file to load.
ENV_FILE_VAR = "SWARM_PROVENANCE_ENV_FILE"
CONFIG_DIR_NAME = "swarm-provenance-mcp"


class Settings(BaseSettings):
    """Configuration settings for the MCP server."""

    # Swarm Gateway Configuration
    swarm_gateway_url: str = Field(
        default="https://provenance-gateway.datafund.io",
        description="URL of the swarm_connect FastAPI gateway",
    )

    # Default stamp parameters
    default_stamp_duration_hours: int = Field(
        default=25,
        description="Default duration in hours for new postage stamps (minimum 24)",
    )

    default_stamp_size: str = Field(
        default="small",
        description="Default size preset for new postage stamps (small, medium, large)",
    )

    # MCP Server Configuration
    mcp_server_name: str = Field(
        default="swarm-provenance-mcp",
        description="Name of the MCP server",
    )

    mcp_server_version: str = Field(
        default="0.2.0",
        description="Version of the MCP server",
    )

    # Payment Configuration
    payment_mode: str = Field(
        default="free",
        description="Payment mode for gateway requests (free = rate-limited free tier)",
    )

    # Chain Anchoring Configuration
    chain_enabled: bool = Field(
        default=False,
        description="Enable on-chain provenance anchoring via DataProvenance contract",
    )

    chain_name: str = Field(
        default="base-sepolia",
        description="Blockchain network name (base-sepolia or base)",
    )

    provenance_wallet_key: Optional[str] = Field(
        default=None,
        description="Private key for signing chain transactions (hex, with or without 0x)",
    )

    chain_rpc_url: Optional[str] = Field(
        default=None,
        description="Custom RPC endpoint URL (uses chain preset if not set)",
    )

    chain_rpc_urls: Optional[str] = Field(
        default=None,
        description="Comma-separated fallback RPC URLs, tried in order after CHAIN_RPC_URL",
    )

    chain_contract_address: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("CHAIN_CONTRACT", "CHAIN_CONTRACT_ADDRESS"),
        description=(
            "Custom DataProvenance contract address (uses chain preset if not set). "
            "Read from CHAIN_CONTRACT, or CHAIN_CONTRACT_ADDRESS; CHAIN_CONTRACT wins."
        ),
    )

    chain_explorer_url: Optional[str] = Field(
        default=None,
        description="Custom block explorer URL (uses chain preset if not set)",
    )

    chain_gas_limit: Optional[int] = Field(
        default=None,
        description="Explicit gas limit for chain transactions (skips estimation if set)",
    )

    # Spending limits for chain writes. Unset means the chain's default: low
    # on mainnet, looser on testnets (see chain/spend.py).
    chain_max_fee_gwei: Optional[float] = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        description="Fee cap per gas (maxFeePerGas) in gwei; writes above it are refused",
    )

    chain_session_budget_eth: Optional[float] = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        description="Worst-case ETH the server may spend on gas per session",
    )

    chain_max_tx_per_session: Optional[int] = Field(
        default=None,
        gt=0,
        description="Maximum chain transactions per session",
    )

    chain_l1_fee_allowance_eth: Optional[float] = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
        description="L1 data fee reserved per transaction on top of L2 gas",
    )

    chain_require_confirm: Optional[bool] = Field(
        default=None,
        description="Require confirm=true on gas-spending tools (unset: mainnet only)",
    )

    max_stamp_duration_hours: int = Field(
        default=8760,
        ge=24,
        description="Maximum duration_hours accepted by purchase_stamp and extend_stamp",
    )

    # No env file by default: only the global instance below reads one, from a
    # fixed location (see resolve_env_file). Unknown keys are ignored so a
    # stray or newer variable in the env file cannot crash start-up.
    model_config = SettingsConfigDict(
        env_file=None,
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        # pydantic-settings merges every source into one dict before resolving
        # aliases, so CHAIN_CONTRACT in the env file would beat
        # CHAIN_CONTRACT_ADDRESS in the environment. Drop a field's file keys
        # when the environment already sets it under any of its names.
        def dotenv_below_env():
            # Sources key alias-read fields by the alias as written (e.g.
            # "CHAIN_CONTRACT"), other fields by the lower-case field name.
            env_keys = {key.lower() for key in env_settings()}
            from_file = dotenv_settings()
            for names in _field_names(settings_cls):
                if names & env_keys:
                    for key in [k for k in from_file if k.lower() in names]:
                        del from_file[key]
            return from_file

        return init_settings, env_settings, dotenv_below_env, file_secret_settings


def _field_names(settings_cls) -> list:
    """Lower-cased names each field can be read from."""
    groups = []
    for name, field in settings_cls.model_fields.items():
        names = {name.lower()}
        alias = field.validation_alias
        if isinstance(alias, AliasChoices):
            names |= {c.lower() for c in alias.choices if isinstance(c, str)}
        elif isinstance(alias, str):
            names.add(alias.lower())
        groups.append(names)
    return groups


def default_env_file() -> Path:
    """Per-user env file: $XDG_CONFIG_HOME (or ~/.config)/swarm-provenance-mcp/.env.

    A relative XDG_CONFIG_HOME is ignored, as the XDG Base Directory spec
    requires: it would resolve against the working directory.
    """
    config_home = os.environ.get("XDG_CONFIG_HOME", "")
    if not os.path.isabs(config_home):
        config_home = os.path.join(os.path.expanduser("~"), ".config")
    return Path(config_home) / CONFIG_DIR_NAME / ".env"


def _short_path(path: Path) -> str:
    """Path with the home directory shown as ~."""
    home = Path(os.path.expanduser("~"))
    try:
        return "~/" + str(path.relative_to(home))
    except ValueError:
        return str(path)


def wallet_key_hint() -> str:
    """Where to put a wallet key, for the error write tools return without one."""
    return (
        'Set PROVENANCE_WALLET_KEY in the MCP client\'s "env" block, or in '
        f"{_short_path(default_env_file())} (or the file named by {ENV_FILE_VAR}). "
        "A .env in the working directory is not read."
    )


def resolve_env_file() -> Optional[Path]:
    """
    Return the env file to load, or None when there is none.

    The file is never taken from the working directory, which belongs to
    whichever program launched the server. It comes from SWARM_PROVENANCE_ENV_FILE
    when that is set (and must then be an absolute path to an existing file),
    otherwise from default_env_file() if that file exists.
    """
    explicit = os.environ.get(ENV_FILE_VAR)
    if explicit:
        try:
            path = Path(explicit).expanduser()
        except RuntimeError:  # ~user that does not exist
            path = Path(explicit)
        if not path.is_absolute():
            # A relative path would resolve against the working directory,
            # which is exactly the location this setting exists to avoid.
            raise ValueError(
                f"{ENV_FILE_VAR} must be an absolute path, got {explicit!r}."
            )
        if not path.is_file():
            raise FileNotFoundError(
                f"{ENV_FILE_VAR} points to {path}, which is not a file. "
                f"Fix the path or unset {ENV_FILE_VAR}."
            )
        return path
    path = default_env_file()
    return path if path.is_file() else None


def _warn_if_cwd_env_ignored(loaded: Optional[Path]) -> None:
    """Say so when a .env in the working directory is being ignored."""
    cwd_env = Path.cwd() / ".env"
    try:
        if not cwd_env.is_file():
            return
        if loaded is not None and cwd_env.resolve() == loaded.resolve():
            return
    except OSError:
        return
    logger.warning(
        "Ignoring %s: settings are no longer read from the working directory. "
        "Move it to %s or set %s to its path.",
        cwd_env,
        default_env_file(),
        ENV_FILE_VAR,
    )


def load_settings() -> Settings:
    """Build Settings from the process environment and the resolved env file."""
    env_file = resolve_env_file()
    _warn_if_cwd_env_ignored(env_file)
    return Settings(_env_file=env_file)


# Global settings instance
settings = load_settings()

# Error text for write tools called without a wallet key.
WALLET_KEY_HINT = wallet_key_hint()
