"""Tests for the SwarmGatewayClient."""

import pytest
import requests_mock
from requests.exceptions import RequestException

from swarm_provenance_mcp.gateway_client import SwarmGatewayClient


class TestSwarmGatewayClient:
    """Test cases for SwarmGatewayClient."""

    def setup_method(self):
        """Set up test fixtures."""
        self.base_url = "http://test-gateway:8001"
        self.client = SwarmGatewayClient(base_url=self.base_url)

    def teardown_method(self):
        """Clean up after tests."""
        self.client.close()

    def test_purchase_stamp_success(self):
        """Test successful stamp purchase."""
        with requests_mock.Mocker() as m:
            expected_response = {
                "batchID": "test-batch-id-123",
                "message": "Postage stamp purchased successfully"
            }
            m.post(f"{self.base_url}/api/v1/stamps/", json=expected_response)

            result = self.client.purchase_stamp(
                amount=2000000000,
                depth=17,
                label="test-stamp"
            )

            assert result == expected_response
            assert m.last_request.json() == {
                "amount": 2000000000,
                "depth": 17,
                "label": "test-stamp"
            }

    def test_purchase_stamp_without_label(self):
        """Test stamp purchase without label."""
        with requests_mock.Mocker() as m:
            expected_response = {
                "batchID": "test-batch-id-456",
                "message": "Postage stamp purchased successfully"
            }
            m.post(f"{self.base_url}/api/v1/stamps/", json=expected_response)

            result = self.client.purchase_stamp(amount=500000000, depth=16)

            assert result == expected_response
            assert m.last_request.json() == {
                "amount": 500000000,
                "depth": 16
            }

    def test_get_stamp_details_success(self):
        """Test successful stamp details retrieval."""
        stamp_id = "test-stamp-id"
        with requests_mock.Mocker() as m:
            expected_response = {
                "batchID": stamp_id,
                "amount": "2000000000",
                "depth": 17,
                "expectedExpiration": "2024-12-31-23-59",
                "usable": True,
                "utilization": 25.5
            }
            m.get(f"{self.base_url}/api/v1/stamps/{stamp_id}", json=expected_response)

            result = self.client.get_stamp_details(stamp_id)

            assert result == expected_response

    def test_list_stamps_success(self):
        """Test successful stamp listing."""
        with requests_mock.Mocker() as m:
            expected_response = {
                "stamps": [
                    {
                        "batchID": "stamp-1",
                        "amount": "2000000000",
                        "depth": 17
                    },
                    {
                        "batchID": "stamp-2",
                        "amount": "500000000",
                        "depth": 16
                    }
                ],
                "total_count": 2
            }
            m.get(f"{self.base_url}/api/v1/stamps/", json=expected_response)

            result = self.client.list_stamps()

            assert result == expected_response

    def test_extend_stamp_success(self):
        """Test successful stamp extension."""
        stamp_id = "test-stamp-id"
        with requests_mock.Mocker() as m:
            expected_response = {
                "batchID": stamp_id,
                "message": "Postage stamp extended successfully"
            }
            m.patch(f"{self.base_url}/api/v1/stamps/{stamp_id}/extend", json=expected_response)

            result = self.client.extend_stamp(stamp_id, 500000000)

            assert result == expected_response
            assert m.last_request.json() == {"amount": 500000000}


    def test_request_failure(self):
        """Test handling of request failures."""
        with requests_mock.Mocker() as m:
            m.get(f"{self.base_url}/api/v1/stamps/", status_code=500)

            with pytest.raises(RequestException):
                self.client.list_stamps()

    def test_custom_headers(self):
        """Test that custom headers are set correctly."""
        with requests_mock.Mocker() as m:
            m.get(f"{self.base_url}/api/v1/stamps/", json={"stamps": [], "total_count": 0})

            self.client.list_stamps()

            request_headers = m.last_request.headers
            assert "Content-Type" in request_headers
            assert request_headers["Content-Type"] == "application/json"
            assert "User-Agent" in request_headers
            assert "swarm-provenance-mcp" in request_headers["User-Agent"]