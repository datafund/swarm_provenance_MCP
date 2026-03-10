"""Docker container tests for the MCP server.

Tests verify the Docker image builds correctly, container configuration is
sound, and the MCP server responds to protocol messages properly.

Requires Docker to be running. Tests are skipped if Docker is unavailable.
"""

import json
import subprocess
import time

import pytest

pytestmark = pytest.mark.docker

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


def send_mcp_messages(messages, env_vars=None, timeout=30):
    """Send MCP messages to the Docker container and return parsed responses."""
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
    """Tests for Dockerfile build and image configuration."""

    def test_image_exists(self):
        """Built image is present in local Docker."""
        result = subprocess.run(
            ["docker", "image", "inspect", IMAGE_NAME],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_build_with_version_arg(self):
        """Build accepts VERSION build arg."""
        result = subprocess.run(
            [
                "docker", "build",
                "--build-arg", "VERSION=1.2.3",
                "-t", "swarm-provenance-mcp:version-test",
                ".",
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert result.returncode == 0, f"Build with VERSION arg failed:\n{result.stderr}"
        subprocess.run(
            ["docker", "rmi", "swarm-provenance-mcp:version-test"],
            capture_output=True,
        )

    def test_runs_as_non_root(self):
        """Container runs as non-root 'mcp' user."""
        result = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", "whoami", IMAGE_NAME],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.stdout.strip() == "mcp"

    def test_entrypoint_is_mcp_server(self):
        """Entrypoint runs the MCP server binary."""
        result = subprocess.run(
            [
                "docker", "inspect",
                "--format", "{{json .Config.Entrypoint}}",
                IMAGE_NAME,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "swarm-provenance-mcp" in result.stdout

    def test_pythonunbuffered_set(self):
        """PYTHONUNBUFFERED=1 is set for stdio reliability."""
        result = subprocess.run(
            [
                "docker", "run", "--rm",
                "--entrypoint", "sh",
                IMAGE_NAME,
                "-c", "echo $PYTHONUNBUFFERED",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "1"

    def test_no_exposed_ports(self):
        """Stdio server should not expose any ports."""
        result = subprocess.run(
            [
                "docker", "inspect",
                "--format", "{{json .Config.ExposedPorts}}",
                IMAGE_NAME,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        output = result.stdout.strip()
        assert output in ("null", "<nil>", "{}", "")


class TestDockerBuildContext:
    """Tests for .dockerignore effectiveness."""

    def test_excludes_tests(self):
        """Tests directory should not be in the image."""
        result = subprocess.run(
            [
                "docker", "run", "--rm",
                "--entrypoint", "sh",
                IMAGE_NAME,
                "-c", "ls /app/tests 2>&1 || echo 'NOT_FOUND'",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert "NOT_FOUND" in result.stdout or "No such file" in result.stdout

    def test_excludes_git(self):
        """.git directory should not be in the image."""
        result = subprocess.run(
            [
                "docker", "run", "--rm",
                "--entrypoint", "sh",
                IMAGE_NAME,
                "-c", "ls /app/.git 2>&1 || echo 'NOT_FOUND'",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert "NOT_FOUND" in result.stdout or "No such file" in result.stdout

    def test_excludes_env(self):
        """.env file should not be in the image."""
        result = subprocess.run(
            [
                "docker", "run", "--rm",
                "--entrypoint", "sh",
                IMAGE_NAME,
                "-c", "ls /app/.env 2>&1 || echo 'NOT_FOUND'",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert "NOT_FOUND" in result.stdout or "No such file" in result.stdout

    def test_includes_package(self):
        """Package source should be in the image."""
        result = subprocess.run(
            [
                "docker", "run", "--rm",
                "--entrypoint", "sh",
                IMAGE_NAME,
                "-c", "ls /app/swarm_provenance_mcp/__init__.py",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0


class TestOCILabels:
    """Tests for OCI metadata labels."""

    def test_title_label(self):
        """OCI title label is set."""
        result = subprocess.run(
            [
                "docker", "inspect",
                "--format", "{{json .Config.Labels}}",
                IMAGE_NAME,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        labels = json.loads(result.stdout.strip())
        assert labels.get("org.opencontainers.image.title") == "Swarm Provenance MCP"

    def test_source_label(self):
        """OCI source label points to datafund repo."""
        result = subprocess.run(
            [
                "docker", "inspect",
                "--format",
                "{{index .Config.Labels \"org.opencontainers.image.source\"}}",
                IMAGE_NAME,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert "datafund" in result.stdout

    def test_license_label(self):
        """OCI license label is set."""
        result = subprocess.run(
            [
                "docker", "inspect",
                "--format",
                "{{index .Config.Labels \"org.opencontainers.image.licenses\"}}",
                IMAGE_NAME,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert "MIT" in result.stdout


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
