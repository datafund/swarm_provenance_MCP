"""Where settings come from: env file location (#152) and variable names (#153)."""

import logging
import re
from pathlib import Path

import pytest

from swarm_provenance_mcp import config
from swarm_provenance_mcp.config import (
    ENV_FILE_VAR,
    Settings,
    default_env_file,
    load_settings,
    resolve_env_file,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# A value for every documented variable, and the field it must land in.
DOCUMENTED_VARS = {
    "SWARM_GATEWAY_URL": ("swarm_gateway_url", "https://gw.example", None),
    "DEFAULT_STAMP_DURATION_HOURS": ("default_stamp_duration_hours", "48", 48),
    "DEFAULT_STAMP_SIZE": ("default_stamp_size", "large", None),
    "PAYMENT_MODE": ("payment_mode", "paid", None),
    "MCP_SERVER_NAME": ("mcp_server_name", "custom-name", None),
    "MCP_SERVER_VERSION": ("mcp_server_version", "9.9.9", None),
    "CHAIN_ENABLED": ("chain_enabled", "true", True),
    "CHAIN_NAME": ("chain_name", "base", None),
    "PROVENANCE_WALLET_KEY": ("provenance_wallet_key", "0x" + "ab" * 32, None),
    "CHAIN_RPC_URL": ("chain_rpc_url", "https://rpc.example", None),
    "CHAIN_RPC_URLS": ("chain_rpc_urls", "https://a.example,https://b.example", None),
    "CHAIN_CONTRACT": ("chain_contract_address", "0x" + "11" * 20, None),
    "CHAIN_CONTRACT_ADDRESS": ("chain_contract_address", "0x" + "22" * 20, None),
    "CHAIN_EXPLORER_URL": ("chain_explorer_url", "https://explorer.example", None),
    "CHAIN_GAS_LIMIT": ("chain_gas_limit", "500000", 500000),
}

# Documented variables that are not Settings fields.
NON_FIELD_VARS = {ENV_FILE_VAR}


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """No settings in the environment, a fresh HOME/XDG dir, and a scratch cwd."""
    for var in list(DOCUMENTED_VARS) + [ENV_FILE_VAR, "XDG_CONFIG_HOME"]:
        monkeypatch.delenv(var, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    return {"home": home, "workdir": workdir}


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class TestEnvFileLocation:
    def test_env_file_in_working_directory_is_ignored(self, clean_env):
        _write(
            clean_env["workdir"] / ".env",
            "SWARM_GATEWAY_URL=https://attacker.example\n"
            "CHAIN_ENABLED=true\n"
            "PROVENANCE_WALLET_KEY=0x" + "cd" * 32 + "\n",
        )
        s = load_settings()
        assert s.swarm_gateway_url == "https://provenance-gateway.datafund.io"
        assert s.chain_enabled is False
        assert s.provenance_wallet_key is None

    def test_plain_settings_reads_no_env_file(self, clean_env):
        _write(clean_env["workdir"] / ".env", "PAYMENT_MODE=paid\n")
        assert Settings().payment_mode == "free"

    def test_ignored_cwd_env_file_is_reported(self, clean_env, caplog):
        _write(clean_env["workdir"] / ".env", "PAYMENT_MODE=paid\n")
        with caplog.at_level(logging.WARNING, logger=config.logger.name):
            load_settings()
        assert "Ignoring" in caplog.text
        assert ENV_FILE_VAR in caplog.text

    def test_default_user_config_file_is_read(self, clean_env):
        path = default_env_file()
        assert path == clean_env["home"] / ".config" / "swarm-provenance-mcp" / ".env"
        _write(path, "PAYMENT_MODE=paid\n")
        assert resolve_env_file() == path
        assert load_settings().payment_mode == "paid"

    def test_xdg_config_home_is_honoured(self, clean_env, monkeypatch, tmp_path):
        xdg = tmp_path / "xdg"
        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
        _write(xdg / "swarm-provenance-mcp" / ".env", "PAYMENT_MODE=paid\n")
        assert load_settings().payment_mode == "paid"

    def test_explicit_env_file_is_read(self, clean_env, monkeypatch, tmp_path):
        path = _write(tmp_path / "elsewhere" / "my.env", "PAYMENT_MODE=paid\n")
        monkeypatch.setenv(ENV_FILE_VAR, str(path))
        assert load_settings().payment_mode == "paid"

    def test_explicit_env_file_beats_default(self, clean_env, monkeypatch, tmp_path):
        _write(default_env_file(), "PAYMENT_MODE=from-default\n")
        path = _write(tmp_path / "my.env", "PAYMENT_MODE=from-explicit\n")
        monkeypatch.setenv(ENV_FILE_VAR, str(path))
        assert load_settings().payment_mode == "from-explicit"

    def test_missing_explicit_env_file_fails_loudly(
        self, clean_env, monkeypatch, tmp_path
    ):
        monkeypatch.setenv(ENV_FILE_VAR, str(tmp_path / "missing.env"))
        with pytest.raises(FileNotFoundError, match=ENV_FILE_VAR):
            load_settings()

    def test_process_environment_beats_env_file(self, clean_env, monkeypatch):
        _write(default_env_file(), "PAYMENT_MODE=from-file\n")
        monkeypatch.setenv("PAYMENT_MODE", "from-env")
        assert load_settings().payment_mode == "from-env"

    def test_unknown_keys_in_env_file_do_not_crash(self, clean_env):
        _write(
            default_env_file(),
            "SOME_OTHER_TOOL_SETTING=1\nCHAIN_CONTRACT=0x" + "11" * 20 + "\n",
        )
        s = load_settings()
        assert s.chain_contract_address == "0x" + "11" * 20


class TestVariableNames:
    @pytest.mark.parametrize("var", sorted(DOCUMENTED_VARS))
    def test_variable_from_environment(self, clean_env, monkeypatch, var):
        field, raw, parsed = DOCUMENTED_VARS[var]
        monkeypatch.setenv(var, raw)
        expected = raw if parsed is None else parsed
        assert getattr(load_settings(), field) == expected

    @pytest.mark.parametrize("var", sorted(DOCUMENTED_VARS))
    def test_variable_from_env_file(self, clean_env, var):
        field, raw, parsed = DOCUMENTED_VARS[var]
        _write(default_env_file(), f"{var}={raw}\n")
        expected = raw if parsed is None else parsed
        assert getattr(load_settings(), field) == expected

    def test_chain_contract_wins_over_long_name(self, clean_env, monkeypatch):
        monkeypatch.setenv("CHAIN_CONTRACT", "0x" + "11" * 20)
        monkeypatch.setenv("CHAIN_CONTRACT_ADDRESS", "0x" + "22" * 20)
        assert load_settings().chain_contract_address == "0x" + "11" * 20

    def test_chain_contract_wins_over_long_name_in_env_file(self, clean_env):
        _write(
            default_env_file(),
            "CHAIN_CONTRACT_ADDRESS=0x"
            + "22" * 20
            + "\nCHAIN_CONTRACT=0x"
            + "11" * 20
            + "\n",
        )
        assert load_settings().chain_contract_address == "0x" + "11" * 20

    @pytest.mark.parametrize(
        "env_var,file_var",
        [
            ("CHAIN_CONTRACT_ADDRESS", "CHAIN_CONTRACT"),
            ("CHAIN_CONTRACT", "CHAIN_CONTRACT_ADDRESS"),
            ("CHAIN_CONTRACT", "CHAIN_CONTRACT"),
        ],
    )
    def test_environment_beats_env_file_under_either_name(
        self, clean_env, monkeypatch, env_var, file_var
    ):
        _write(default_env_file(), f"{file_var}=0x{'22' * 20}\n")
        monkeypatch.setenv(env_var, "0x" + "11" * 20)
        assert load_settings().chain_contract_address == "0x" + "11" * 20

    def test_env_file_still_fills_fields_the_environment_leaves_unset(
        self, clean_env, monkeypatch
    ):
        _write(
            default_env_file(),
            "CHAIN_CONTRACT=0x" + "22" * 20 + "\nPAYMENT_MODE=paid\n",
        )
        monkeypatch.setenv("CHAIN_NAME", "base")
        s = load_settings()
        assert (s.chain_name, s.payment_mode) == ("base", "paid")
        assert s.chain_contract_address == "0x" + "22" * 20

    def test_field_name_still_works_as_keyword(self, clean_env):
        s = Settings(chain_contract_address="0x" + "33" * 20)
        assert s.chain_contract_address == "0x" + "33" * 20

    @pytest.mark.parametrize("doc", ["README.md", "CLAUDE.md", ".env.example"])
    def test_every_documented_variable_is_covered(self, doc):
        text = (REPO_ROOT / doc).read_text()
        if doc == ".env.example":
            names = set(re.findall(r"^#? ?([A-Z][A-Z0-9_]+)=", text, re.M))
        else:
            names = set(re.findall(r"^- `([A-Z][A-Z0-9_]+)`:", text, re.M))
        assert names, f"no variables found in {doc}"
        unknown = names - set(DOCUMENTED_VARS) - NON_FIELD_VARS
        assert not unknown, f"{doc} documents variables with no test: {unknown}"
