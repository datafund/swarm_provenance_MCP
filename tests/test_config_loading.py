"""Where settings come from: env file location (#152) and variable names (#153)."""

import logging
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from swarm_provenance_mcp import config
from swarm_provenance_mcp.config import (
    ENV_FILE_VAR,
    WALLET_KEY_HINT,
    Settings,
    default_env_file,
    load_settings,
    wallet_key_hint,
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
    "CHAIN_MAX_FEE_GWEI": ("chain_max_fee_gwei", "0.5", 0.5),
    "CHAIN_SESSION_BUDGET_ETH": ("chain_session_budget_eth", "0.01", 0.01),
    "CHAIN_MAX_TX_PER_SESSION": ("chain_max_tx_per_session", "5", 5),
    "CHAIN_L1_FEE_ALLOWANCE_ETH": ("chain_l1_fee_allowance_eth", "0.0001", 0.0001),
    "CHAIN_REQUIRE_CONFIRM": ("chain_require_confirm", "true", True),
    "MAX_STAMP_DURATION_HOURS": ("max_stamp_duration_hours", "720", 720),
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

    @pytest.mark.parametrize("value", [".env", "conf/my.env", "./.env"])
    def test_relative_explicit_env_file_is_rejected(
        self, clean_env, monkeypatch, value
    ):
        # A relative path would resolve against the working directory.
        _write(clean_env["workdir"] / value, "PAYMENT_MODE=paid\n")
        monkeypatch.setenv(ENV_FILE_VAR, value)
        with pytest.raises(ValueError, match="absolute path"):
            load_settings()

    def test_unknown_user_home_in_explicit_env_file_is_rejected(
        self, clean_env, monkeypatch
    ):
        # Path.expanduser() raises a bare RuntimeError for an unknown ~user.
        monkeypatch.setenv(ENV_FILE_VAR, "~nosuchuser-swarm-test/x.env")
        with pytest.raises(ValueError, match=ENV_FILE_VAR):
            load_settings()

    @pytest.mark.parametrize("value", [".", "relative/config"])
    def test_relative_xdg_config_home_is_ignored(self, clean_env, monkeypatch, value):
        # The XDG spec says relative values must be ignored.
        monkeypatch.setenv("XDG_CONFIG_HOME", value)
        _write(
            clean_env["workdir"] / value / "swarm-provenance-mcp" / ".env",
            "PAYMENT_MODE=paid\n",
        )
        assert default_env_file() == (
            clean_env["home"] / ".config" / "swarm-provenance-mcp" / ".env"
        )
        assert load_settings().payment_mode == "free"

    def test_home_relative_explicit_env_file_is_accepted(self, clean_env, monkeypatch):
        _write(clean_env["home"] / "my.env", "PAYMENT_MODE=paid\n")
        monkeypatch.setenv(ENV_FILE_VAR, "~/my.env")
        assert load_settings().payment_mode == "paid"

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


class TestModuleLevelSettings:
    """The global `settings` that server.py imports, built in a fresh process."""

    def test_import_ignores_env_file_in_working_directory(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        workdir = tmp_path / "work"
        workdir.mkdir()
        _write(
            workdir / ".env",
            "SWARM_GATEWAY_URL=https://evil.example\n"
            "CHAIN_ENABLED=true\n"
            "PROVENANCE_WALLET_KEY=0x" + "cd" * 32 + "\n"
            "CHAIN_CONTRACT=0x" + "11" * 20 + "\n"
            "UNKNOWN_KEY=1\n",
        )
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in DOCUMENTED_VARS
            and k not in (ENV_FILE_VAR, "XDG_CONFIG_HOME")
            and k.upper() not in DOCUMENTED_VARS
        }
        env["HOME"] = str(home)
        env["PYTHONPATH"] = str(REPO_ROOT)
        code = (
            "from swarm_provenance_mcp.config import settings as s; "
            "print(s.swarm_gateway_url, s.chain_enabled, "
            "s.provenance_wallet_key, s.chain_contract_address)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=workdir,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.split() == [
            "https://provenance-gateway.datafund.io",
            "False",
            "None",
            "None",
        ]
        assert "Ignoring" in result.stderr
        assert str(workdir / ".env") in result.stderr


class TestWalletKeyHint:
    def test_hint_names_the_places_that_are_read(self, clean_env):
        hint = wallet_key_hint()
        assert "PROVENANCE_WALLET_KEY" in hint
        assert '"env" block' in hint
        assert "~/.config/swarm-provenance-mcp/.env" in hint
        assert ENV_FILE_VAR in hint
        assert WALLET_KEY_HINT.startswith("Set PROVENANCE_WALLET_KEY")

    def test_hint_names_the_xdg_config_home_file(
        self, clean_env, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        hint = wallet_key_hint()
        assert str(tmp_path / "xdg" / "swarm-provenance-mcp" / ".env") in hint
        assert "~/.config" not in hint

    def test_hint_does_not_send_users_to_a_working_directory_env(self):
        text = WALLET_KEY_HINT.lower()
        assert "your .env" not in text
        assert "in .env" not in text
        # The only bare ".env" mentions are the config path and the warning.
        assert "not read" in text

    def test_server_uses_the_shared_hint(self):
        source = (REPO_ROOT / "swarm_provenance_mcp" / "server.py").read_text()
        assert "your .env" not in source
        assert source.count("requires a funded wallet. {WALLET_KEY_HINT}") == 5
