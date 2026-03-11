"""Client for communicating with the swarm_connect FastAPI gateway."""

import json
from typing import Dict, List, Any, Optional
import requests
from requests.exceptions import RequestException

from .config import settings


class SwarmGatewayClient:
    """Client for interacting with the Swarm gateway API."""

    def __init__(self, base_url: Optional[str] = None):
        """Initialize the gateway client.

        Args:
            base_url: Override the default gateway URL from settings
        """
        self.base_url = (base_url or settings.swarm_gateway_url).rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": f"{settings.mcp_server_name}/{settings.mcp_server_version}"
        })

        # Add payment mode header for x402 gateway access
        if settings.payment_mode:
            self.session.headers["X-Payment-Mode"] = settings.payment_mode

    def purchase_stamp(
        self,
        amount: int,
        depth: int,
        label: Optional[str] = None
    ) -> Dict[str, Any]:
        """Purchase a new postage stamp.

        Args:
            amount: Amount of the stamp in wei
            depth: Depth of the stamp
            label: Optional label for the stamp

        Returns:
            Response containing batchID and message

        Raises:
            RequestException: If the request fails
        """
        url = f"{self.base_url}/api/v1/stamps/"
        payload = {
            "amount": amount,
            "depth": depth
        }
        if label:
            payload["label"] = label

        response = self.session.post(url, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()

    def get_stamp_details(self, stamp_id: str) -> Dict[str, Any]:
        """Get details for a specific stamp.

        Args:
            stamp_id: The batch ID of the stamp

        Returns:
            Stamp details including expiration and usage info

        Raises:
            RequestException: If the request fails
        """
        url = f"{self.base_url}/api/v1/stamps/{stamp_id}"
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        return response.json()

    def list_stamps(self) -> Dict[str, Any]:
        """List all available stamps.

        Returns:
            Response containing list of stamps and total count

        Raises:
            RequestException: If the request fails
        """
        url = f"{self.base_url}/api/v1/stamps/"
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        return response.json()

    def extend_stamp(self, stamp_id: str, amount: int) -> Dict[str, Any]:
        """Extend an existing stamp with additional funds.

        Args:
            stamp_id: The batch ID of the stamp to extend
            amount: Additional amount to add in wei

        Returns:
            Response containing batchID and message

        Raises:
            RequestException: If the request fails
        """
        url = f"{self.base_url}/api/v1/stamps/{stamp_id}/extend"
        payload = {"amount": amount}

        response = self.session.patch(url, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()


    def upload_data(self, data: str, stamp_id: str, content_type: str = "application/json") -> Dict[str, Any]:
        """Upload data to Swarm network.

        Args:
            data: Data content as string (max 4096 bytes)
            stamp_id: Postage stamp ID to use for upload
            content_type: MIME type of the content (default: application/json)

        Returns:
            Upload response with reference hash

        Raises:
            RequestException: If the request fails
            ValueError: If data exceeds size limit
        """
        # Check size limit (4KB = 4096 bytes)
        data_bytes = data.encode('utf-8')
        if len(data_bytes) > 4096:
            raise ValueError(f"Data size {len(data_bytes)} bytes exceeds 4KB limit (4096 bytes). Larger uploads are not currently supported.")

        url = f"{self.base_url}/api/v1/data/"

        # Prepare multipart form data
        files = {
            'file': ('data', data_bytes, content_type)
        }

        params = {
            'stamp_id': stamp_id,
            'content_type': content_type
        }

        # For file uploads, temporarily remove Content-Type from session to let requests set multipart/form-data
        original_content_type = self.session.headers.pop('Content-Type', None)
        try:
            response = self.session.post(url, files=files, params=params, timeout=30)
        finally:
            # Restore the Content-Type header
            if original_content_type:
                self.session.headers['Content-Type'] = original_content_type
        response.raise_for_status()
        return response.json()

    def download_data(self, reference: str) -> bytes:
        """Download data from Swarm network.

        Args:
            reference: Swarm reference hash of the data

        Returns:
            Raw data bytes

        Raises:
            RequestException: If the request fails
        """
        url = f"{self.base_url}/api/v1/data/{reference}"
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        return response.content

    def health_check(self) -> Dict[str, Any]:
        """Check gateway and Swarm connectivity.

        Returns:
            Health status information

        Raises:
            RequestException: If the request fails
        """
        url = f"{self.base_url}/"
        response = self.session.get(url, timeout=10)
        response.raise_for_status()

        # Create meaningful health status
        health_data = response.json() if response.content else {}
        return {
            "status": "healthy",
            "gateway_url": self.base_url,
            "response_time_ms": response.elapsed.total_seconds() * 1000,
            "gateway_response": health_data
        }

    def check_stamp_health(self, stamp_id: str) -> Dict[str, Any]:
        """Check health of a specific stamp for upload readiness.

        Args:
            stamp_id: The batch ID of the stamp to check

        Returns:
            Health check with can_upload, errors, warnings, and status

        Raises:
            RequestException: If the request fails
        """
        url = f"{self.base_url}/api/v1/stamps/{stamp_id}/check"
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        return response.json()

    def get_wallet_info(self) -> Dict[str, Any]:
        """Get wallet address and BZZ balance.

        Returns:
            Response containing walletAddress and bzzBalance

        Raises:
            RequestException: If the request fails
        """
        url = f"{self.base_url}/api/v1/wallet"
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        return response.json()

    def get_notary_info(self) -> Dict[str, Any]:
        """Get notary service status and configuration.

        Returns:
            Response containing enabled, available, address, and message

        Raises:
            RequestException: If the request fails
        """
        url = f"{self.base_url}/api/v1/notary/info"
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        return response.json()

    def close(self):
        """Close the HTTP session."""
        self.session.close()