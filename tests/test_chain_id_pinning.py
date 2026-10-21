"""The configured network, not the RPC, decides the chain id (#157)."""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from requests.exceptions import ConnectionError as ReqConnectionError

from swarm_provenance_mcp.chain.exceptions import (
    ChainConfigurationError,
    ChainIdMismatchError,
)
from tests.test_chain_client import (  # noqa: F401 (fixture)
    DUMMY_HASH,
    mock_chain_deps,
)
from tests.test_tool_execution import call_tool_directly

BASE_MAINNET = 8453
BASE_SEPOLIA = 84532
REPO_ROOT = Path(__file__).resolve().parent.parent


def _chain_id_reads(deps, first, then):
    """Make eth_chainId raise or return ``first`` once, then return ``then``."""
    effects = iter([first])

    def read():
        value = next(effects, then)
        if isinstance(value, Exception):
            raise value
        return value

    type(deps["web3_instance"].eth).chain_id = PropertyMock(side_effect=read)


class TestStartupRefusal:
    def test_testnet_config_refuses_mainnet_rpc(self, mock_chain_deps):
        from swarm_provenance_mcp.chain.client import ChainClient

        mock_chain_deps["web3_instance"].eth.chain_id = BASE_MAINNET
        with pytest.raises(ChainIdMismatchError, match="8453"):
            ChainClient(chain="base-sepolia", rpc_url="https://rpc.example")

    def test_mainnet_config_refuses_testnet_rpc(self, mock_chain_deps):
        from swarm_provenance_mcp.chain.provider import ChainProvider

        mock_chain_deps["web3_instance"].eth.chain_id = BASE_SEPOLIA
        with pytest.raises(ChainIdMismatchError):
            ChainProvider(
                chain="base",
                rpc_url="https://rpc.example",
                contract_address="0x" + "11" * 20,
            )

    def test_mismatch_is_a_configuration_error(self):
        # Reported as non-retryable, and never failed over by _rpc_failover.
        assert issubclass(ChainIdMismatchError, ChainConfigurationError)

    def test_matching_custom_rpc_keeps_preset_id(self, mock_chain_deps):
        from swarm_provenance_mcp.chain.provider import ChainProvider

        provider = ChainProvider(chain="base-sepolia", rpc_url="https://rpc.example")
        assert provider.chain_id == BASE_SEPOLIA
        assert provider.is_testnet

    def test_unreachable_rpc_at_startup_is_not_fatal(self, mock_chain_deps):
        from swarm_provenance_mcp.chain.provider import ChainProvider

        _chain_id_reads(mock_chain_deps, ReqConnectionError("down"), BASE_SEPOLIA)
        provider = ChainProvider(chain="base-sepolia", rpc_url="https://rpc.example")
        assert provider.chain_id == BASE_SEPOLIA

    def test_localhost_custom_rpc_still_auto_detects(self, mock_chain_deps):
        from swarm_provenance_mcp.chain.provider import ChainProvider

        mock_chain_deps["web3_instance"].eth.chain_id = BASE_MAINNET
        provider = ChainProvider(chain="localhost", rpc_url="http://127.0.0.1:8545")
        assert provider.chain_id == BASE_MAINNET
        assert not provider.is_testnet  # a mainnet fork is labelled mainnet


class TestSigning:
    def test_signed_transaction_carries_preset_chain_id(self, mock_chain_deps):
        from swarm_provenance_mcp.chain.client import ChainClient

        client = ChainClient(chain="base-sepolia", rpc_url="https://rpc.example")
        client.transform(DUMMY_HASH, "b" * 64, "x")
        signed_tx = mock_chain_deps["account"].sign_transaction.call_args.args[0]
        assert signed_tx["chainId"] == BASE_SEPOLIA

    @pytest.mark.parametrize(
        "write",
        [
            lambda c: c.transform(DUMMY_HASH, "b" * 64, "x"),
            lambda c: c.merge_transform([DUMMY_HASH, "c" * 64], "b" * 64, "x"),
            lambda c: c.set_status(DUMMY_HASH, 1),
        ],
        ids=["transform", "merge_transform", "set_status"],
    )
    def test_rpc_on_another_chain_after_startup_is_refused_before_signing(
        self, mock_chain_deps, write
    ):
        from swarm_provenance_mcp.chain.client import ChainClient

        client = ChainClient(chain="base-sepolia", rpc_url="https://rpc.example")
        web3 = mock_chain_deps["web3_instance"]
        web3.eth.chain_id = BASE_MAINNET  # misrouted after start-up

        with pytest.raises(ChainIdMismatchError):
            write(client)
        mock_chain_deps["account"].sign_transaction.assert_not_called()
        web3.eth.send_raw_transaction.assert_not_called()

    def test_rpc_unreachable_at_startup_then_on_mainnet_is_refused(
        self, mock_chain_deps
    ):
        from swarm_provenance_mcp.chain.client import ChainClient

        _chain_id_reads(mock_chain_deps, ReqConnectionError("down"), BASE_MAINNET)
        client = ChainClient(chain="base-sepolia", rpc_url="https://rpc.example")

        with pytest.raises(ChainIdMismatchError):
            client.transform(DUMMY_HASH, "b" * 64, "x")
        mock_chain_deps["account"].sign_transaction.assert_not_called()
        mock_chain_deps["web3_instance"].eth.send_raw_transaction.assert_not_called()


class TestLabels:
    """Testnet/mainnet wording follows the effective chain id."""

    @pytest.fixture
    def server(self):
        from swarm_provenance_mcp.server import create_server

        return create_server()

    def _client_on(self, chain_id, chain="localhost"):
        from swarm_provenance_mcp.chain.provider import ChainProvider

        provider = ChainProvider(chain=chain, rpc_url="http://127.0.0.1:8545")
        assert provider.chain_id == chain_id
        client = MagicMock()
        client.chain = provider.chain
        client._provider = MagicMock(wraps=provider)
        client._provider.chain = provider.chain
        client._provider.chain_id = provider.chain_id
        client._provider.is_testnet = provider.is_testnet
        client._provider.rpc_url = provider.rpc_url
        client._provider.contract_address = provider.contract_address
        client._provider.health_check.return_value = True
        client._provider.get_block_number.return_value = 1
        return client

    async def test_chain_health_labels_mainnet_id_as_mainnet(
        self, server, mock_chain_deps
    ):
        mock_chain_deps["web3_instance"].eth.chain_id = BASE_MAINNET
        client = self._client_on(BASE_MAINNET)
        with (
            patch("swarm_provenance_mcp.server.CHAIN_AVAILABLE", True),
            patch("swarm_provenance_mcp.server.chain_client", client),
        ):
            result = await call_tool_directly(server, "chain_health", {})
        text = result.content[0].text
        assert "Chain ID: 8453" in text
        assert "Mainnet" in text
        assert "Testnet" not in text

    async def test_chain_health_labels_testnet_id_as_testnet(
        self, server, mock_chain_deps
    ):
        client = self._client_on(BASE_SEPOLIA, chain="base-sepolia")
        with (
            patch("swarm_provenance_mcp.server.CHAIN_AVAILABLE", True),
            patch("swarm_provenance_mcp.server.chain_client", client),
        ):
            result = await call_tool_directly(server, "chain_health", {})
        text = result.content[0].text
        assert "Chain ID: 84532" in text
        assert "Testnet" in text
        assert "Mainnet" not in text

    async def test_health_check_shows_chain_id_and_label(self, server, mock_chain_deps):
        from swarm_provenance_mcp.server import settings

        mock_chain_deps["web3_instance"].eth.chain_id = BASE_MAINNET
        client = self._client_on(BASE_MAINNET)
        client.balance.side_effect = Exception("skip")
        with (
            patch.object(settings, "chain_enabled", True),
            patch("swarm_provenance_mcp.server.gateway_client") as gw,
            patch("swarm_provenance_mcp.server.CHAIN_AVAILABLE", True),
            patch("swarm_provenance_mcp.server.chain_client", client),
        ):
            gw.health_check.return_value = {
                "status": "healthy",
                "gateway_url": "http://localhost",
                "response_time_ms": 1,
            }
            gw.list_stamps.return_value = {"stamps": [], "total_count": 0}
            result = await call_tool_directly(server, "health_check", {})
        text = result.content[0].text
        assert "Chain ID: 8453" in text
        assert "Mainnet" in text
        assert "Testnet" not in text


class _FakeRpc(BaseHTTPRequestHandler):
    chain_id = BASE_MAINNET

    def do_POST(self):  # noqa: N802 (http.server API)
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        reply = {"jsonrpc": "2.0", "id": body.get("id"), "result": hex(self.chain_id)}
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_rpc():
    server = HTTPServer(("127.0.0.1", 0), _FakeRpc)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


def _start_server_process(rpc_url, tmp_path):
    """Import the server module the way start-up does, in a clean process."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path),
        "PYTHONPATH": str(REPO_ROOT),
        "CHAIN_ENABLED": "true",
        "CHAIN_NAME": "base-sepolia",
        "CHAIN_RPC_URL": rpc_url,
    }
    return subprocess.run(
        [sys.executable, "-c", "import swarm_provenance_mcp.server"],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )


class TestServerStartup:
    def test_server_refuses_to_start_on_mainnet_rpc(self, fake_rpc, tmp_path):
        _FakeRpc.chain_id = BASE_MAINNET
        url = f"http://127.0.0.1:{fake_rpc.server_port}"
        proc = _start_server_process(url, tmp_path)
        assert proc.returncode != 0
        assert "ChainIdMismatchError" in proc.stderr

    def test_server_starts_on_matching_rpc(self, fake_rpc, tmp_path):
        _FakeRpc.chain_id = BASE_SEPOLIA
        url = f"http://127.0.0.1:{fake_rpc.server_port}"
        proc = _start_server_process(url, tmp_path)
        assert proc.returncode == 0, proc.stderr
