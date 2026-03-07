"""Docker container tests for the MCP server.

These tests verify that the Docker image builds correctly and the MCP server
inside the container responds to protocol messages properly.

Requires Docker to be running. Skip with: pytest -m "not docker"
"""

import json
import subprocess
import time

import pytest

IMAGE_NAME = "swarm-provenance-mcp:test"

INITIALIZE_REQUEST = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "docker-test", "version": "1.0.0"},
    },
}

INITIALIZED_NOTIFICATION = {
    "jsonrpc": "2.0",
    "method": "notifications/initialized",
}

TOOLS_LIST_REQUEST = {
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/list",
    "params": {},
}

HEALTH_CHECK_REQUEST = {
    "jsonrpc": "2.0",
    "id": 3,
    "method": "tools/call",
    "params": {"name": "health_check", "arguments": {}},
}

EXPECTED_TOOLS = [
    "purchase_stamp",
    "get_stamp_status",
    "list_stamps",
    "extend_stamp",
    "upload_data",
    "download_data",
    "health_check",
]


def docker_available():
    """Check if Docker daemon is running."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


pytestmark = pytest.mark.docker


def send_mcp_messages(messages, env_vars=None, timeout=30):
    """Send MCP messages to the Docker container and return parsed responses.

    Args:
        messages: List of JSON-RPC message dicts to send.
        env_vars: Optional dict of environment variables.
        timeout: Subprocess timeout in seconds.

    Returns:
        Tuple of (responses list, stderr string).
    """
    cmd = ["docker", "run", "-i", "--rm"]
    if env_vars:
        for key, value in env_vars.items():
            cmd.extend(["-e", f"{key}={value}"])
    cmd.append(IMAGE_NAME)

    input_data = "\n".join(json.dumps(m) for m in messages) + "\n"

    result = subprocess.run(
        cmd,
        input=input_data,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    responses = []
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if line:
            try:
                responses.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    return responses, result.stderr


@pytest.fixture(scope="module", autouse=True)
def build_docker_image():
    """Build the Docker image once before all tests in this module."""
    if not docker_available():
        pytest.skip("Docker daemon is not running")

    result = subprocess.run(
        ["docker", "build", "-t", IMAGE_NAME, "."],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, f"Docker build failed:\n{result.stderr}"


class TestDockerBuild:
    """Tests that verify the Docker image builds correctly."""

    def test_image_exists(self):
        """Built image is present in local Docker."""
        result = subprocess.run(
            ["docker", "image", "inspect", IMAGE_NAME],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_image_labels(self):
        """Image has OCI annotation labels."""
        result = subprocess.run(
            [
                "docker", "image", "inspect",
                "--format", "{{json .Config.Labels}}",
                IMAGE_NAME,
            ],
            capture_output=True,
            text=True,
        )
        labels = json.loads(result.stdout.strip())
        assert labels.get("org.opencontainers.image.title") == "Swarm Provenance MCP"
        assert "org.opencontainers.image.source" in labels

    def test_runs_as_non_root(self):
        """Container runs as non-root mcp user."""
        result = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", "whoami", IMAGE_NAME],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.stdout.strip() == "mcp"


class TestContainerStartup:
    """Tests that verify the container starts and logs correctly."""

    def test_startup_logs(self):
        """Container logs show server name and gateway URL on startup."""
        proc = subprocess.Popen(
            ["docker", "run", "-i", "--rm", IMAGE_NAME],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(3)
        proc.stdin.close()
        proc.wait(timeout=10)
        stderr = proc.stderr.read().decode()

        assert "Starting swarm-provenance-mcp" in stderr
        assert "Gateway URL:" in stderr

    def test_env_var_override(self):
        """SWARM_GATEWAY_URL environment variable is picked up."""
        custom_url = "https://custom-gateway.example.com"
        proc = subprocess.Popen(
            [
                "docker", "run", "-i", "--rm",
                "-e", f"SWARM_GATEWAY_URL={custom_url}",
                IMAGE_NAME,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(3)
        proc.stdin.close()
        proc.wait(timeout=10)
        stderr = proc.stderr.read().decode()

        assert custom_url in stderr


class TestMCPProtocol:
    """Tests that verify the MCP server responds correctly to protocol messages."""

    def test_initialize(self):
        """Server responds to initialize with correct protocol version and server info."""
        responses, _ = send_mcp_messages([INITIALIZE_REQUEST])

        assert len(responses) >= 1
        init_response = responses[0]
        assert init_response["jsonrpc"] == "2.0"
        assert init_response["id"] == 1
        result = init_response["result"]
        assert result["protocolVersion"] == "2024-11-05"
        assert result["serverInfo"]["name"] == "swarm-provenance-mcp"
        assert result["serverInfo"]["version"] == "0.1.0"

    def test_tools_list(self):
        """Server returns all 7 expected tools."""
        responses, _ = send_mcp_messages([
            INITIALIZE_REQUEST,
            INITIALIZED_NOTIFICATION,
            TOOLS_LIST_REQUEST,
        ])

        # Find the tools/list response (id=2)
        tools_response = next(r for r in responses if r.get("id") == 2)
        tool_names = [t["name"] for t in tools_response["result"]["tools"]]

        assert sorted(tool_names) == sorted(EXPECTED_TOOLS)

    def test_tools_have_schemas(self):
        """Every tool has an inputSchema with type=object."""
        responses, _ = send_mcp_messages([
            INITIALIZE_REQUEST,
            INITIALIZED_NOTIFICATION,
            TOOLS_LIST_REQUEST,
        ])

        tools_response = next(r for r in responses if r.get("id") == 2)
        for tool in tools_response["result"]["tools"]:
            assert "inputSchema" in tool, f"Tool {tool['name']} missing inputSchema"
            assert tool["inputSchema"]["type"] == "object"

    def test_health_check_tool_call(self):
        """health_check tool executes and returns a result from the gateway."""
        responses, _ = send_mcp_messages([
            INITIALIZE_REQUEST,
            INITIALIZED_NOTIFICATION,
            HEALTH_CHECK_REQUEST,
        ])

        health_response = next(r for r in responses if r.get("id") == 3)
        content = health_response["result"]["content"]
        assert len(content) >= 1
        assert content[0]["type"] == "text"
        # Should mention the gateway URL
        assert "provenance-gateway.datafund.io" in content[0]["text"]

    def test_invalid_tool_returns_error(self):
        """Calling a non-existent tool returns an error response."""
        request = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "nonexistent_tool", "arguments": {}},
        }
        responses, _ = send_mcp_messages([
            INITIALIZE_REQUEST,
            INITIALIZED_NOTIFICATION,
            request,
        ])

        error_response = next(r for r in responses if r.get("id") == 3)
        result = error_response["result"]
        assert result["isError"] is True
        assert "Unknown tool" in result["content"][0]["text"]
