"""Client for communicating with the swarm_connect FastAPI gateway."""

import base64
import binascii
import json
from typing import Dict, List, Any, Optional
import requests
from requests.exceptions import RequestException

from .config import settings
from .untrusted import MAX_ERROR_DETAIL_CHARS, flatten, one_line

# Longest gateway string value passed on to the server, in characters.
MAX_GATEWAY_STRING_CHARS = 1000


def _clean_strings(value: Any) -> Any:
    """Flatten and cap every string in a decoded gateway JSON body.

    The server prints many gateway fields into tool results. Cleaning them
    here means no field, including ones added later, can carry a line break
    (and so forge a hint line) or a huge value. Hidden characters are left
    for the printing site (``quote``/``one_line``) to escape, once.
    """
    if isinstance(value, str):
        return flatten(value, MAX_GATEWAY_STRING_CHARS)
    if isinstance(value, dict):
        return {_clean_strings(k): _clean_strings(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean_strings(v) for v in value]
    return value


def _json(response) -> Any:
    """The response body as JSON, with its strings cleaned."""
    try:
        return _clean_strings(response.json())
    except RecursionError:
        # A body nested too deeply is not a real gateway response. Depending
        # on the Python version the RecursionError comes from json decoding
        # (3.10's scanner) or from the cleaner walking the decoded value.
        raise requests.exceptions.InvalidJSONError(
            "Gateway response is nested too deeply to process", response=response
        ) from None


# Longest PAYMENT-REQUIRED header the client decodes, in characters.
MAX_PAYMENT_HEADER_CHARS = 8192
# Longest snippet of a non-JSON 402 body kept in ``payment_info["raw"]``.
MAX_RAW_402_CHARS = 300


class PaymentRequiredError(requests.HTTPError):
    """The gateway answered HTTP 402 Payment Required (x402).

    A subclass of ``requests.HTTPError``, so code that handles any gateway
    error keeps working. ``payment_info`` is a normalised dict from
    ``parse_payment_required()``.
    """

    def __init__(self, message: str, payment_info: Dict[str, Any], response=None):
        super().__init__(message, response=response)
        self.payment_info = payment_info


def _payment_header(response) -> Optional[Dict[str, Any]]:
    """The decoded PAYMENT-REQUIRED header (base64 JSON), if any.

    The gateway sends the payment request in the body (x402 v1). x402 v2
    moves it to this header, so it is read as a fallback. Anything that does
    not decode to a JSON object is ignored.
    """
    value = response.headers.get("PAYMENT-REQUIRED") if response is not None else None
    if not value or len(value) > MAX_PAYMENT_HEADER_CHARS:
        return None
    try:
        decoded = json.loads(base64.b64decode(value.strip(), validate=True))
        if not isinstance(decoded, dict):
            return None
        return _clean_strings(decoded)
    except (binascii.Error, ValueError, RecursionError):
        return None


def parse_payment_required(response) -> Dict[str, Any]:
    """Normalise a 402 response into a payment_info dict. Never raises.

    The gateway sends several shapes:

    - the x402 v1 body at the top level (``x402Version``, ``error``,
      ``accepts``, optional ``freeTier``), with ``code``/``message`` and a
      deprecated copy under ``detail``;
    - older gateways: the same body only under ``detail``;
    - gateway-specific bodies without ``accepts``, such as
      ``{"code": "PAYMENT_SETTLEMENT_FAILED", "message": ...}``;
    - an empty or non-JSON body (a proxy page, for example).

    Returns a dict with ``x402_version``, ``error``, ``code``, ``accepts``
    (a list of dicts), ``free_tier`` (dict or None) and ``raw`` (a capped
    snippet of a non-JSON body, else "").
    """
    info: Dict[str, Any] = {
        "x402_version": None,
        "error": "",
        "code": "",
        "accepts": [],
        "free_tier": None,
        "raw": "",
    }
    body: Any = None
    try:
        body = _json(response)
    except (ValueError, requests.exceptions.InvalidJSONError):
        # Empty or not JSON (a proxy page, for example): keep the start.
        info["raw"] = flatten(getattr(response, "text", "") or "", MAX_RAW_402_CHARS)

    sources: List[Dict[str, Any]] = []
    if isinstance(body, dict):
        sources.append(body)
        if isinstance(body.get("detail"), dict):
            sources.append(body["detail"])
    elif isinstance(body, str):
        info["error"] = body
    header = _payment_header(response)
    if header is not None:
        sources.append(header)

    for src in sources:
        _merge_payment_source(info, src)
    return info


def _merge_payment_source(info: Dict[str, Any], src: Dict[str, Any]) -> None:
    """Fill the fields of ``info`` still empty from one part of a 402.

    Parts are merged in order (top level, ``detail``, PAYMENT-REQUIRED
    header), so the first part that has a field wins.
    """
    accepts = src.get("accepts")
    if not info["accepts"] and isinstance(accepts, list):
        info["accepts"] = [a for a in accepts if isinstance(a, dict)]
    version = src.get("x402Version")
    if info["x402_version"] is None and isinstance(version, (int, str)):
        info["x402_version"] = version
    if not info["code"] and isinstance(src.get("code"), str):
        info["code"] = src["code"]
    if not info["error"]:
        texts = [src.get(k) for k in ("error", "message", "detail")]
        info["error"] = next((t for t in texts if isinstance(t, str) and t), "")
    free = src.get("freeTier")
    if info["free_tier"] is None and isinstance(free, dict):
        info["free_tier"] = free


class SwarmGatewayClient:
    """Client for interacting with the Swarm gateway API."""

    def __init__(self, base_url: Optional[str] = None):
        """Initialize the gateway client.

        Args:
            base_url: Override the default gateway URL from settings
        """
        self.base_url = (base_url or settings.swarm_gateway_url).rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Content-Type": "application/json",
                "User-Agent": f"{settings.mcp_server_name}/{settings.mcp_server_version}",
            }
        )

        # Add payment mode header for x402 gateway access
        if settings.payment_mode:
            self.session.headers["X-Payment-Mode"] = settings.payment_mode

    def _raise_with_detail(self, response):
        """Raise HTTPError enriched with gateway error details."""
        if response.ok:
            return
        detail = ""
        try:
            body = response.json()
            d = body.get("detail", body)
            if isinstance(d, dict):
                detail = d.get("message", "") or d.get("error", "") or str(d)
            elif isinstance(d, list):
                detail = "; ".join(
                    item.get("msg", str(item)) if isinstance(item, dict) else str(item)
                    for item in d
                )
            elif isinstance(d, str):
                detail = d
        except Exception:
            detail = response.text or ""
        # The detail can echo request content or come from a misbehaving
        # proxy: keep it to one short line so it cannot flood the result or
        # pass for a hint line.
        detail = one_line(detail, MAX_ERROR_DETAIL_CHARS)
        try:
            response.raise_for_status()
        except requests.HTTPError as e:
            # The reason phrase comes from the status line: cap it as well.
            message = one_line(str(e), 2 * MAX_ERROR_DETAIL_CHARS)
            if response.status_code == 402:
                info = parse_payment_required(response)
                if info["error"]:
                    message += f" — {one_line(info['error'], MAX_ERROR_DETAIL_CHARS)}"
                raise PaymentRequiredError(message, info, response=response) from e
            if detail:
                message += f" — {detail}"
            raise requests.HTTPError(message, response=response) from e

    def purchase_stamp(
        self,
        duration_hours: int,
        size: str = "small",
        depth: Optional[int] = None,
        label: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Purchase a new postage stamp.

        Args:
            duration_hours: Duration in hours (minimum 24)
            size: Size preset ("small", "medium", "large")
            depth: Optional explicit depth override (sent instead of size)
            label: Optional label for the stamp

        Returns:
            Response containing batchID and message

        Raises:
            RequestException: If the request fails
        """
        url = f"{self.base_url}/api/v1/stamps/"
        if depth is not None:
            payload: Dict[str, Any] = {"duration_hours": duration_hours, "depth": depth}
        else:
            payload = {"duration_hours": duration_hours, "size": size}
        if label:
            payload["label"] = label

        # A purchase waits for an on-chain batch; giving up early turns a
        # success into an apparent failure that invites a second purchase.
        response = self.session.post(url, json=payload, timeout=120)
        self._raise_with_detail(response)
        return _json(response)

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
        self._raise_with_detail(response)
        return _json(response)

    def list_stamps(self) -> Dict[str, Any]:
        """List local stamps available on the gateway.

        Returns:
            Response containing list of stamps and total count

        Raises:
            RequestException: If the request fails
        """
        url = f"{self.base_url}/api/v1/stamps/"
        response = self.session.get(url, timeout=30)
        self._raise_with_detail(response)
        return _json(response)

    def extend_stamp(self, stamp_id: str, duration_hours: int) -> Dict[str, Any]:
        """Extend an existing stamp's TTL.

        Args:
            stamp_id: The batch ID of the stamp to extend
            duration_hours: Additional duration in hours (minimum 24)

        Returns:
            Response containing batchID and message

        Raises:
            RequestException: If the request fails
        """
        url = f"{self.base_url}/api/v1/stamps/{stamp_id}/extend"
        payload = {"duration_hours": duration_hours}

        # Like a purchase, a top-up waits on chain; a short timeout turns a
        # success into an "outcome unknown" the agent has to resolve.
        response = self.session.patch(url, json=payload, timeout=120)
        self._raise_with_detail(response)
        return _json(response)

    def upload_data(
        self,
        data: str,
        stamp_id: str,
        content_type: str = "application/json",
        sign: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Upload data to Swarm network.

        Args:
            data: Data content as string (max 4096 bytes)
            stamp_id: Postage stamp ID to use for upload
            content_type: MIME type of the content (default: application/json)
            sign: Signing method for provenance. Use 'notary' to have the gateway
                  cryptographically sign the data at upload time.

        Returns:
            Upload response with reference hash

        Raises:
            RequestException: If the request fails
            ValueError: If data exceeds size limit
        """
        # Check size limit (4KB = 4096 bytes)
        data_bytes = data.encode("utf-8")
        if len(data_bytes) > 4096:
            raise ValueError(
                f"Data size {len(data_bytes)} bytes exceeds 4KB limit (4096 bytes). Larger uploads are not currently supported."
            )

        url = f"{self.base_url}/api/v1/data/"

        # Prepare multipart form data
        files = {"file": ("data", data_bytes, content_type)}

        params = {"stamp_id": stamp_id, "content_type": content_type}
        if sign:
            params["sign"] = sign

        # For file uploads, temporarily remove Content-Type from session to let requests set multipart/form-data
        original_content_type = self.session.headers.pop("Content-Type", None)
        try:
            response = self.session.post(url, files=files, params=params, timeout=30)
        finally:
            # Restore the Content-Type header
            if original_content_type:
                self.session.headers["Content-Type"] = original_content_type
        self._raise_with_detail(response)
        return _json(response)

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
        self._raise_with_detail(response)
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
        self._raise_with_detail(response)

        # Create meaningful health status
        health_data = _json(response) if response.content else {}
        return {
            "status": "healthy",
            "gateway_url": self.base_url,
            "response_time_ms": response.elapsed.total_seconds() * 1000,
            "gateway_response": health_data,
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
        self._raise_with_detail(response)
        return _json(response)

    def get_wallet_info(self) -> Dict[str, Any]:
        """Get wallet address and BZZ balance.

        Returns:
            Response containing walletAddress and bzzBalance

        Raises:
            RequestException: If the request fails
        """
        url = f"{self.base_url}/api/v1/wallet"
        response = self.session.get(url, timeout=30)
        self._raise_with_detail(response)
        return _json(response)

    def get_notary_info(self) -> Dict[str, Any]:
        """Get notary service status and configuration.

        Returns:
            Response containing enabled, available, address, and message

        Raises:
            RequestException: If the request fails
        """
        url = f"{self.base_url}/api/v1/notary/info"
        response = self.session.get(url, timeout=30)
        self._raise_with_detail(response)
        return _json(response)

    def close(self):
        """Close the HTTP session."""
        self.session.close()
