"""Tests for Docker containerization."""

import subprocess
import pytest


@pytest.fixture(scope="module")
def docker_available():
    """Check if Docker is available."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            pytest.skip("Docker daemon not running")
    except FileNotFoundError:
        pytest.skip("Docker not installed")


@pytest.fixture(scope="module")
def built_image(docker_available):
    """Build the Docker image once for all tests."""
    result = subprocess.run(
        ["docker", "build", "-t", "swarm-provenance-mcp:test", "."],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, f"Docker build failed:\n{result.stderr}"
    yield "swarm-provenance-mcp:test"
    # Cleanup
    subprocess.run(
        ["docker", "rmi", "swarm-provenance-mcp:test"],
        capture_output=True,
    )


class TestDockerBuild:
    """Tests for Dockerfile build."""

    def test_build_succeeds(self, built_image):
        """Docker build completes without errors."""
        assert built_image == "swarm-provenance-mcp:test"

    def test_build_with_version_arg(self, docker_available):
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


class TestDockerRuntime:
    """Tests for container runtime behavior."""

    def test_runs_as_non_root(self, built_image):
        """Container runs as non-root 'mcp' user."""
        result = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", "whoami", built_image],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "mcp"

    def test_entrypoint_is_mcp_server(self, built_image):
        """Entrypoint runs the MCP server binary."""
        result = subprocess.run(
            [
                "docker", "inspect",
                "--format", "{{json .Config.Entrypoint}}",
                built_image,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "swarm-provenance-mcp" in result.stdout

    def test_pythonunbuffered_set(self, built_image):
        """PYTHONUNBUFFERED=1 is set for stdio reliability."""
        result = subprocess.run(
            [
                "docker", "run", "--rm",
                "--entrypoint", "sh",
                built_image,
                "-c", "echo $PYTHONUNBUFFERED",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "1"

    def test_env_vars_respected(self, built_image):
        """Container respects environment variable overrides."""
        custom_url = "https://custom-gateway.example.com"
        result = subprocess.run(
            [
                "docker", "run", "--rm",
                "-e", f"SWARM_GATEWAY_URL={custom_url}",
                "--entrypoint", "sh",
                built_image,
                "-c", "echo $SWARM_GATEWAY_URL",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == custom_url

    def test_no_exposed_ports(self, built_image):
        """Stdio server should not expose any ports."""
        result = subprocess.run(
            [
                "docker", "inspect",
                "--format", "{{json .Config.ExposedPorts}}",
                built_image,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        # Should be null/empty - no ports exposed
        output = result.stdout.strip()
        assert output in ("null", "<nil>", "{}", "")


class TestDockerBuildContext:
    """Tests for .dockerignore effectiveness."""

    def test_build_context_excludes_tests(self, built_image):
        """Tests directory should not be in the image."""
        result = subprocess.run(
            [
                "docker", "run", "--rm",
                "--entrypoint", "sh",
                built_image,
                "-c", "ls /app/tests 2>&1 || echo 'NOT_FOUND'",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert "NOT_FOUND" in result.stdout or "No such file" in result.stdout

    def test_build_context_excludes_git(self, built_image):
        """.git directory should not be in the image."""
        result = subprocess.run(
            [
                "docker", "run", "--rm",
                "--entrypoint", "sh",
                built_image,
                "-c", "ls /app/.git 2>&1 || echo 'NOT_FOUND'",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert "NOT_FOUND" in result.stdout or "No such file" in result.stdout

    def test_build_context_excludes_env(self, built_image):
        """.env file should not be in the image."""
        result = subprocess.run(
            [
                "docker", "run", "--rm",
                "--entrypoint", "sh",
                built_image,
                "-c", "ls /app/.env 2>&1 || echo 'NOT_FOUND'",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert "NOT_FOUND" in result.stdout or "No such file" in result.stdout

    def test_build_context_includes_package(self, built_image):
        """Package source should be in the image."""
        result = subprocess.run(
            [
                "docker", "run", "--rm",
                "--entrypoint", "sh",
                built_image,
                "-c", "ls /app/swarm_provenance_mcp/__init__.py",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0


class TestDockerOCILabels:
    """Tests for OCI metadata labels."""

    def test_oci_labels_present(self, built_image):
        """OCI labels are set on the image."""
        result = subprocess.run(
            [
                "docker", "inspect",
                "--format",
                "{{index .Config.Labels \"org.opencontainers.image.title\"}}",
                built_image,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "swarm-provenance-mcp" in result.stdout

    def test_oci_source_label(self, built_image):
        """OCI source label points to datafund repo."""
        result = subprocess.run(
            [
                "docker", "inspect",
                "--format",
                "{{index .Config.Labels \"org.opencontainers.image.source\"}}",
                built_image,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "datafund" in result.stdout

    def test_oci_license_label(self, built_image):
        """OCI license label is set."""
        result = subprocess.run(
            [
                "docker", "inspect",
                "--format",
                "{{index .Config.Labels \"org.opencontainers.image.licenses\"}}",
                built_image,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "MIT" in result.stdout
