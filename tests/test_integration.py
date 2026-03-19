"""Integration tests for the MCP server with real gateway integration."""

import os
import pytest
import requests
from swarm_provenance_mcp.gateway_client import SwarmGatewayClient

# Gateway configuration
GATEWAY_URL = os.getenv("SWARM_GATEWAY_URL", "http://127.0.0.1:8000")
GATEWAY_HEALTH_URL = f"{GATEWAY_URL}/"


def is_gateway_running():
    """Check if the gateway is running and accessible."""
    try:
        response = requests.get(GATEWAY_HEALTH_URL, timeout=5)
        return response.status_code == 200
    except (requests.RequestException, ConnectionError):
        return False


@pytest.fixture(scope="session", autouse=True)
def check_gateway():
    """Ensure gateway is running before running integration tests."""
    if not is_gateway_running():
        pytest.skip(
            f"Gateway is not running at {GATEWAY_URL}. "
            "Please start the swarm_connect gateway before running integration tests."
        )


@pytest.fixture
def gateway_client():
    """Create a gateway client for testing."""
    client = SwarmGatewayClient(GATEWAY_URL)
    yield client
    client.close()


@pytest.mark.integration
def test_gateway_client_purchase_stamp(gateway_client):
    """Test actual stamp purchase through gateway client."""
    try:
        result = gateway_client.purchase_stamp(
            amount=2000000000, depth=17, label="integration-test"
        )

        # Verify response structure
        assert "batchID" in result, "Response missing batchID"
        assert "message" in result, "Response missing message"
        assert len(result["batchID"]) == 64, "Invalid batchID format"
        assert result["message"] == "Postage stamp purchased successfully"

    except Exception as e:
        pytest.fail(f"Stamp purchase failed: {e}")


@pytest.mark.integration
def test_gateway_client_list_stamps(gateway_client):
    """Test stamp listing through gateway client."""
    try:
        result = gateway_client.list_stamps()

        # Verify response structure
        assert "stamps" in result, "Response missing stamps list"
        assert "total_count" in result, "Response missing total_count"
        assert isinstance(result["stamps"], list), "Stamps should be a list"
        assert result["total_count"] >= 0, "Total count should be non-negative"

    except Exception as e:
        pytest.fail(f"Stamp listing failed: {e}")


@pytest.mark.integration
def test_gateway_client_extend_stamp(gateway_client):
    """Test stamp extension through gateway client."""
    # First purchase a stamp to extend
    try:
        purchase_result = gateway_client.purchase_stamp(
            amount=2000000000, depth=17, label="extend-test"
        )
        stamp_id = purchase_result["batchID"]

        # Now extend it
        extend_result = gateway_client.extend_stamp(stamp_id, 2000000000)

        # Verify response
        assert "batchID" in extend_result, "Extension response missing batchID"
        assert "message" in extend_result, "Extension response missing message"
        assert (
            extend_result["batchID"] == stamp_id
        ), "Extension should return same stamp ID"
        assert extend_result["message"] == "Postage stamp extended successfully"

    except Exception as e:
        # Accept 502 errors as they indicate gateway is working but backend Swarm node is not available
        if hasattr(e, "response") and e.response.status_code == 502:
            pytest.skip(f"Backend Swarm node not available (502): {e}")
        else:
            pytest.fail(f"Stamp extension failed: {e}")


# Note: get_stamp_utilization was removed as utilization data is available in get_stamp_details


@pytest.mark.integration
def test_gateway_client_connection_failure():
    """Test that client fails properly when gateway is not available."""
    # Create client with bad URL
    bad_client = SwarmGatewayClient("http://127.0.0.1:9999")

    try:
        # This should raise an exception
        bad_client.purchase_stamp(2000000000, 17)
        pytest.fail("Expected connection error when gateway is not available")

    except requests.RequestException:
        # This is expected
        pass
    except Exception as e:
        pytest.fail(f"Expected RequestException, got {type(e)}: {e}")

    finally:
        bad_client.close()


@pytest.mark.integration
def test_gateway_client_invalid_stamp_operations(gateway_client):
    """Test operations with invalid stamp IDs."""
    fake_stamp_id = "1234567890abcdef" * 4  # 64 char fake ID

    # Test extending non-existent stamp
    try:
        gateway_client.extend_stamp(fake_stamp_id, 2000000000)
        pytest.fail("Expected error when extending non-existent stamp")
    except requests.RequestException as e:
        # This is expected - should get 404 or similar error
        assert e.response.status_code in [
            400,
            404,
            500,
            502,
        ], f"Unexpected status code: {e.response.status_code}"

    # Test getting details of non-existent stamp
    try:
        result = gateway_client.get_stamp_details(fake_stamp_id)
        pytest.fail("Expected error when getting details of non-existent stamp")
    except requests.RequestException as e:
        # This is expected - should get 404 or similar error
        assert e.response.status_code in [
            400,
            404,
            500,
            502,
        ], f"Unexpected status code: {e.response.status_code}"


if __name__ == "__main__":
    # Run a quick test to see if gateway is accessible
    if is_gateway_running():
        print(f"✅ Gateway is running at {GATEWAY_URL}")

        # Test basic stamp purchase
        client = SwarmGatewayClient(GATEWAY_URL)
        try:
            result = client.purchase_stamp(2000000000, 17, "quick-test")
            print(f"✅ Stamp purchase successful: {result['batchID'][:12]}...")
        except Exception as e:
            print(f"❌ Stamp purchase failed: {e}")
        finally:
            client.close()
    else:
        print(f"❌ Gateway is not accessible at {GATEWAY_URL}")
        print("Please start the swarm_connect gateway before running these tests.")
