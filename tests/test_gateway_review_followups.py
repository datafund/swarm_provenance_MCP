"""Review follow-ups for the gateway error handling (#172, #174): #175, #176, #177."""

import socket
import threading
from unittest.mock import patch

import pytest
import requests

from swarm_provenance_mcp.server import _bee_network_status, create_server
from tests.test_tool_execution import (
    TEST_REFERENCE,
    TEST_STAMP_ID,
    _bee_node,
    _gateway_health,
    _http_error,
    call_tool_directly,
)


@pytest.fixture
def server():
    return create_server()


class TestAdvisoryWarnings:
    """#175: gateway warnings are advisory, not an outage."""

    def test_healthy_with_chain_lag_warning_is_ok(self):
        status = _bee_network_status(
            {"bee_node": _bee_node(warnings=["Chain sync lag is 150 blocks"])}
        )
        assert status["ok"] is True
        assert status["advisories"] == ["Chain sync lag is 150 blocks"]

    async def test_health_check_stays_ready_and_shows_advisory(self, server):
        with patch("swarm_provenance_mcp.server.gateway_client") as gw:
            gw.health_check.return_value = _gateway_health(
                _bee_node(warnings=["Only 42 connected peers"])
            )
            gw.list_stamps.return_value = {
                "stamps": [{"batchID": TEST_STAMP_ID, "usable": True}]
            }
            result = await call_tool_directly(server, "health_check", {})
        text = result.content[0].text
        assert "Swarm network: connected" in text
        assert "Gateway advisory: Only 42 connected peers" in text
        assert "ready: true" in text
        assert "_next: upload_data" in text

    async def test_download_404_with_advisory_is_not_degraded(self, server):
        with patch("swarm_provenance_mcp.server.gateway_client") as gw:
            gw.download_data.side_effect = _http_error(404)
            gw.health_check.return_value = _gateway_health(
                _bee_node(warnings=["Chain sync lag is 150 blocks"])
            )
            result = await call_tool_directly(
                server, "download_data", {"reference": TEST_REFERENCE}
            )
        text = result.content[0].text
        assert "connected to the Swarm network" in text
        assert "retryable: false" in text

    def test_bee_node_without_state_is_not_reported(self):
        """{} must not read as 'connected (no details reported)'."""
        assert _bee_network_status({"bee_node": {}}) is None
        assert _bee_network_status({"bee_node": {"version": "2.8.1"}}) is None


class TestServerErrorsPointAtHealthCheck:
    """#176: a 5xx on upload/download has health_check as its next step."""

    @pytest.mark.parametrize(
        "tool,method,args",
        [
            ("upload_data", "upload_data", {"data": "x", "stamp_id": TEST_STAMP_ID}),
            ("download_data", "download_data", {"reference": TEST_REFERENCE}),
        ],
    )
    async def test_500(self, server, tool, method, args):
        with patch("swarm_provenance_mcp.server.gateway_client") as gw:
            getattr(gw, method).side_effect = _http_error(500)
            result = await call_tool_directly(server, tool, args)
        text = result.content[0].text
        assert "retryable: false" in text
        assert "_next: health_check" in text

    async def test_4xx_still_has_no_hint(self, server):
        with patch("swarm_provenance_mcp.server.gateway_client") as gw:
            gw.upload_data.side_effect = _http_error(400, "bad request")
            result = await call_tool_directly(
                server, "upload_data", {"data": "x", "stamp_id": TEST_STAMP_ID}
            )
        assert "_next: health_check" not in result.content[0].text


@pytest.fixture
def hang_up_gateway():
    """A gateway that reads the request and closes without answering."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(5)
    stop = threading.Event()

    def serve():
        sock.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = sock.accept()
            except OSError:
                continue
            conn.recv(65536)
            conn.close()

    threading.Thread(target=serve, daemon=True).start()
    yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    stop.set()
    sock.close()


class TestMoreUnknownOutcomes:
    """#177: failures that can follow a completed purchase."""

    async def test_connection_dropped_after_send(self, server, hang_up_gateway):
        from swarm_provenance_mcp.gateway_client import SwarmGatewayClient

        with patch(
            "swarm_provenance_mcp.server.gateway_client",
            SwarmGatewayClient(base_url=hang_up_gateway),
        ):
            result = await call_tool_directly(
                server, "purchase_stamp", {"label": "run-42"}
            )
        text = result.content[0].text
        assert "outcome unknown" in text, text
        assert "retryable: false" in text
        assert "_next: list_stamps" in text
        assert "label 'run-42'" in text

    @pytest.mark.parametrize(
        "error",
        [
            _http_error(500, "Bee returned an unparseable response"),
            requests.exceptions.ChunkedEncodingError("Connection broken"),
            requests.exceptions.JSONDecodeError("Expecting value", "<html>", 0),
        ],
        ids=["http-500", "chunked", "non-json-2xx"],
    )
    async def test_purchase(self, server, error):
        with patch("swarm_provenance_mcp.server.gateway_client") as gw:
            gw.purchase_stamp.side_effect = error
            result = await call_tool_directly(server, "purchase_stamp", {})
        text = result.content[0].text
        assert "outcome unknown" in text, text
        assert "retryable: false" in text
        assert "Validation error" not in text

    async def test_extension_non_json_and_ttl_hint(self, server):
        with patch("swarm_provenance_mcp.server.gateway_client") as gw:
            gw.extend_stamp.side_effect = requests.exceptions.JSONDecodeError(
                "Expecting value", "", 0
            )
            result = await call_tool_directly(
                server,
                "extend_stamp",
                {"stamp_id": TEST_STAMP_ID, "duration_hours": 24},
            )
        text = result.content[0].text
        assert "Stamp extension outcome unknown" in text
        assert "TTL" in text
        assert "_next: get_stamp_status" in text

    async def test_refused_connection_stays_retryable(self, server):
        from swarm_provenance_mcp.gateway_client import SwarmGatewayClient

        with patch(
            "swarm_provenance_mcp.server.gateway_client",
            SwarmGatewayClient(base_url="http://127.0.0.1:1"),
        ):
            result = await call_tool_directly(server, "purchase_stamp", {})
        text = result.content[0].text
        assert "outcome unknown" not in text
        assert "retryable: true" in text


def test_extend_waits_at_least_two_minutes():
    from swarm_provenance_mcp.gateway_client import SwarmGatewayClient

    client = SwarmGatewayClient(base_url="http://gw")
    with patch.object(client.session, "patch") as patch_call:
        patch_call.return_value.ok = True
        patch_call.return_value.json.return_value = {"batchID": TEST_STAMP_ID}
        client.extend_stamp(TEST_STAMP_ID, 24)
    assert patch_call.call_args.kwargs["timeout"] >= 120
