"""#260: list_stamps uses health_check's readiness rule.

This server sends no wallet, so the gateway refuses its uploads to pool
stamps (gateway inventory) and owned stamps (registered to the wallet that
paid for them). list_stamps must not label them usable, must say why a stamp
cannot be used, and its _next must agree with its text and with health_check.
"""

from unittest.mock import patch

import pytest

from swarm_provenance_mcp.server import create_server
from tests.test_tool_execution import TEST_STAMP_ID, call_tool_directly

OTHER_ID = "c" * 64


@pytest.fixture
def server():
    return create_server()


def _stamp(access_mode, usable=True, batch_id=TEST_STAMP_ID, **extra):
    stamp = {"batchID": batch_id, "usable": usable}
    if access_mode != "absent":
        stamp["accessMode"] = access_mode
    stamp.update(extra)
    return stamp



class TestStampReadiness:
    """The per-stamp rule that health_check and list_stamps share."""

    @pytest.mark.parametrize(
        "stamp,kind",
        [
            (_stamp("shared"), "writable"),
            (_stamp("absent"), "writable"),
            (_stamp("shared", usable=False), "pending"),
            (_stamp("shared", usable="yes"), "pending"),
            (_stamp("shared", usable=False, unusableReason="full"), "spent"),
            (_stamp("shared", usable=False, unusableReason="not_found"), "spent"),
            (_stamp("shared", usable=False, unusableReason="not_found",
                    secondsSincePurchase=10), "pending"),
            (_stamp("pool"), "pool"),
            (_stamp("pool", usable=False), "pool"),
            (_stamp("owned"), "owned"),
            (_stamp("weird"), "other"),
            (_stamp(None), "writable"),
            ("not a dict", None),
        ],
    )
    def test_kind(self, stamp, kind):
        from swarm_provenance_mcp.server import _stamp_readiness

        assert _stamp_readiness(stamp) == kind

    def test_counts_unchanged(self):
        from swarm_provenance_mcp.server import _writable_stamp_counts

        stamps = [
            _stamp("shared"),
            _stamp("absent"),
            _stamp("shared", usable=False),
            _stamp("absent", usable=False, batch_id="bad"),
            _stamp("shared", usable=False, unusableReason="expired"),
            _stamp("pool"),
            _stamp("pool", usable=False),
            _stamp("owned"),
            _stamp("owned", usable=False),
            _stamp("weird"),
            _stamp("weird", usable=False),
            "junk",
        ]
        assert _writable_stamp_counts(stamps) == {
            "writable": 2,
            "pending": 2,
            "pool": 1,
            "owned": 1,
            "other": 1,
            "pending_ids": [TEST_STAMP_ID],
            "blocked": {"pool": 2, "owned": 2, "other": 2, "spent": 1},
        }
