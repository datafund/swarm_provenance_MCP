"""#167: anchor_hash with owner keeps storage_ref, with the SDK input rules.

The golden cases pin the output of valid calls that do not combine owner and
storage_ref. They were captured from the code before this change
(tests/fixtures/anchor_hash_golden_167.json) and must stay byte-identical.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from swarm_provenance_mcp.server import create_server
from tests.test_tool_execution import call_tool_directly

GOLDEN_FILE = Path(__file__).parent / "fixtures" / "anchor_hash_golden_167.json"

WALLET = "0x1234567890abcdef1234567890abcdef12345678"
OWNER = "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd"
OTHER = "0x9999999999999999999999999999999999999999"
HASH = "b" * 64
REF = "c" * 64
REF2 = "d" * 64
TX = "ab" * 32


def _result(owner=WALLET, storage_ref=None, data_type="swarm-provenance"):
    result = MagicMock()
    result.swarm_hash = HASH
    result.data_type = data_type
    result.owner = owner
    result.storage_ref = storage_ref
    result.tx_hash = TX
    result.block_number = 12345678
    result.gas_used = 65000
    result.explorer_url = "https://base-sepolia.blockscout.com/tx/0x" + TX
    return result


def _wallet_info():
    info = MagicMock()
    info.address = WALLET
    info.balance_wei = 10**17
    info.balance_eth = "0.100000"
    info.chain = "base-sepolia"
    return info


def _registered(owner, storage_ref=None):
    from swarm_provenance_mcp.chain.exceptions import DataAlreadyRegisteredError

    e = DataAlreadyRegisteredError(
        "Already registered",
        data_hash=HASH,
        owner=owner,
        timestamp=1700000000,
        data_type="swarm-provenance",
    )
    # Set as an attribute so the golden capture also runs on the base code.
    e.storage_ref = storage_ref
    return e


def _client():
    client = MagicMock()
    client.address = WALLET
    client.balance.return_value = _wallet_info()
    return client


def _golden_cases():
    """name -> (arguments, configure(client))."""
    from swarm_provenance_mcp.chain.exceptions import ChainTransactionError

    def ok(method, **kw):
        return lambda c: setattr(getattr(c, method), "return_value", _result(**kw))

    def raises(method, exc):
        return lambda c: setattr(getattr(c, method), "side_effect", exc)

    return {
        "plain_success": ({"swarm_hash": HASH}, ok("anchor")),
        "plain_success_0x_upper": (
            {"swarm_hash": "0x" + HASH.upper()},
            ok("anchor"),
        ),
        "plain_data_type": (
            {"swarm_hash": HASH, "data_type": "document"},
            ok("anchor", data_type="document"),
        ),
        "owner_success": (
            {"swarm_hash": HASH, "owner": OWNER},
            ok("anchor_for", owner=OWNER),
        ),
        "owner_checksummed_success": (
            {"swarm_hash": HASH, "owner": "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"},
            ok("anchor_for", owner="0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"),
        ),
        "storage_ref_success": (
            {"swarm_hash": HASH, "storage_ref": REF},
            ok("anchor", storage_ref=REF),
        ),
        "storage_ref_empty_success": (
            {"swarm_hash": HASH, "storage_ref": ""},
            ok("anchor"),
        ),
        "plain_already_self": (
            {"swarm_hash": HASH},
            raises("anchor", _registered(WALLET)),
        ),
        "owner_already_owner": (
            {"swarm_hash": HASH, "owner": OWNER},
            raises("anchor_for", _registered(OWNER)),
        ),
        "storage_ref_already_self_same_ref": (
            {"swarm_hash": HASH, "storage_ref": REF},
            raises("anchor", _registered(WALLET, storage_ref=REF)),
        ),
        "plain_already_other": (
            {"swarm_hash": HASH},
            raises("anchor", _registered(OTHER)),
        ),
        "owner_already_other": (
            {"swarm_hash": HASH, "owner": OWNER},
            raises("anchor_for", _registered(OTHER)),
        ),
        "storage_ref_already_other": (
            {"swarm_hash": HASH, "storage_ref": REF},
            raises("anchor", _registered(OTHER, storage_ref=REF)),
        ),
        "owner_tx_failed": (
            {"swarm_hash": HASH, "owner": OWNER},
            raises(
                "anchor_for",
                ChainTransactionError("execution reverted: Not authorized delegate"),
            ),
        ),
        "plain_bad_hash": ({"swarm_hash": "xyz"}, ok("anchor")),
        "owner_bad_format": ({"swarm_hash": HASH, "owner": "0x12"}, ok("anchor_for")),
    }


async def _run(arguments, configure):
    client = _client()
    configure(client)
    with (
        patch("swarm_provenance_mcp.server.CHAIN_AVAILABLE", True),
        patch("swarm_provenance_mcp.server.chain_client", client),
    ):
        result = await call_tool_directly(create_server(), "anchor_hash", arguments)
    calls = [
        [name, [str(a) for a in args]]
        for name, args, _ in client.method_calls
        if name in ("anchor", "anchor_for")
    ]
    return {
        "isError": bool(result.isError),
        "text": result.content[0].text,
        "calls": calls,
    }, client


async def capture_golden():
    """Used once, on the base code, to write GOLDEN_FILE."""
    out = {}
    for name, (arguments, configure) in _golden_cases().items():
        out[name], _ = await _run(arguments, configure)
    return out


class TestGoldenOutputs:
    """Valid calls that do not combine owner and storage_ref keep the
    output of the code before #167."""

    @pytest.mark.parametrize("name", sorted(_golden_cases()))
    async def test_output_unchanged(self, name):
        golden = json.loads(GOLDEN_FILE.read_text())
        arguments, configure = _golden_cases()[name]
        actual, _ = await _run(arguments, configure)
        assert actual == golden[name]

    def test_every_case_has_a_golden(self):
        golden = json.loads(GOLDEN_FILE.read_text())
        assert set(golden) == set(_golden_cases())


async def _call(arguments, configure=None):
    client = _client()
    client.anchor.return_value = _result(storage_ref=arguments.get("storage_ref"))
    client.anchor_for.return_value = _result(
        owner=arguments.get("owner"), storage_ref=arguments.get("storage_ref")
    )
    if configure:
        configure(client)
    with (
        patch("swarm_provenance_mcp.server.CHAIN_AVAILABLE", True),
        patch("swarm_provenance_mcp.server.chain_client", client),
    ):
        result = await call_tool_directly(create_server(), "anchor_hash", arguments)
    return result, result.content[0].text, client


class TestOwnerWithStorageRef:
    async def test_combined_call_links_the_storage_ref(self):
        result, text, client = await _call(
            {"swarm_hash": HASH, "owner": OWNER, "storage_ref": REF}
        )
        assert not result.isError, text
        client.anchor_for.assert_called_once_with(HASH, OWNER, "swarm-provenance", REF)
        client.anchor.assert_not_called()
        assert f"   Owner: {OWNER}\n" in text
        assert f"   Storage Ref: {REF}\n" in text
        assert "cannot be combined" not in text

    async def test_combined_call_normalises_the_ref(self):
        result, text, client = await _call(
            {"swarm_hash": HASH, "owner": OWNER, "storage_ref": "0x" + REF}
        )
        assert not result.isError, text
        client.anchor_for.assert_called_once_with(HASH, OWNER, "swarm-provenance", REF)

    async def test_owner_only_call_keeps_three_arguments(self):
        _, _, client = await _call({"swarm_hash": HASH, "owner": OWNER})
        client.anchor_for.assert_called_once_with(HASH, OWNER, "swarm-provenance")


ZERO_HASH = "0" * 64
ZERO_ADDRESS = "0x" + "0" * 40
BAD_CHECKSUM = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAeD"


class TestRefusedInputs:
    """Inputs the SDK refuses, and the zero storage ref, are refused before
    any quote or transaction on every path."""

    @pytest.mark.parametrize(
        "arguments, phrase",
        [
            ({"swarm_hash": ZERO_HASH}, "swarm_hash is the zero hash"),
            ({"swarm_hash": ZERO_HASH, "owner": OWNER}, "swarm_hash is the zero hash"),
            (
                {"swarm_hash": ZERO_HASH, "storage_ref": REF},
                "swarm_hash is the zero hash",
            ),
            ({"swarm_hash": "0x" + ZERO_HASH}, "swarm_hash is the zero hash"),
            ({"swarm_hash": HASH, "owner": ZERO_ADDRESS}, "owner is the zero address"),
            (
                {"swarm_hash": HASH, "owner": ZERO_ADDRESS, "storage_ref": REF},
                "owner is the zero address",
            ),
            ({"swarm_hash": HASH, "owner": WALLET}, "cannot be its own delegate"),
            (
                {"swarm_hash": HASH, "owner": "0x" + WALLET[2:].upper()},
                "cannot be its own delegate",
            ),
            (
                {"swarm_hash": HASH, "owner": WALLET, "storage_ref": REF},
                "cannot be its own delegate",
            ),
            ({"swarm_hash": HASH, "owner": BAD_CHECKSUM}, "EIP-55 checksum is wrong"),
            (
                {"swarm_hash": HASH, "owner": BAD_CHECKSUM, "storage_ref": REF},
                "EIP-55 checksum is wrong",
            ),
            (
                {"swarm_hash": HASH, "storage_ref": HASH},
                "storage_ref must differ from swarm_hash",
            ),
            (
                {"swarm_hash": HASH, "storage_ref": "0x" + HASH.upper()},
                "storage_ref must differ from swarm_hash",
            ),
            (
                {"swarm_hash": HASH, "owner": OWNER, "storage_ref": HASH},
                "storage_ref must differ from swarm_hash",
            ),
            (
                {"swarm_hash": HASH, "storage_ref": ZERO_HASH},
                "storage_ref is the zero hash",
            ),
            (
                {"swarm_hash": HASH, "owner": OWNER, "storage_ref": ZERO_HASH},
                "storage_ref is the zero hash",
            ),
        ],
    )
    async def test_refused_before_any_chain_call(self, arguments, phrase):
        result, text, client = await _call(arguments)
        assert result.isError
        assert text.startswith("Validation error: ") or "Validation error: " in text
        assert phrase in text
        assert "quote_id" not in text
        client.anchor.assert_not_called()
        client.anchor_for.assert_not_called()
        client.fee_quote.assert_not_called()

    async def test_all_uppercase_owner_is_accepted(self):
        owner = "0x" + OWNER[2:].upper()
        result, text, client = await _call({"swarm_hash": HASH, "owner": owner})
        assert not result.isError, text
        client.anchor_for.assert_called_once()

    async def test_wallet_check_ignores_a_malformed_wallet_address(self):
        def configure(c):
            c.address = MagicMock()

        result, text, client = await _call(
            {"swarm_hash": HASH, "owner": OWNER, "storage_ref": REF}, configure
        )
        assert not result.isError, text


class TestAlreadyRegisteredWithStorageRef:
    """A record that already belongs to the expected owner is success only
    when it carries the requested storage ref."""

    @pytest.mark.parametrize(
        "arguments, method, owner",
        [
            ({"swarm_hash": HASH, "storage_ref": REF}, "anchor", WALLET),
            (
                {"swarm_hash": HASH, "owner": OWNER, "storage_ref": REF},
                "anchor_for",
                OWNER,
            ),
        ],
    )
    async def test_same_ref_is_success(self, arguments, method, owner):
        def configure(c):
            getattr(c, method).side_effect = _registered(owner, storage_ref=REF.upper())

        result, text, _ = await _call(arguments, configure)
        assert not result.isError, text
        assert "Hash already registered on-chain" in text

    @pytest.mark.parametrize("existing", [None, REF2])
    @pytest.mark.parametrize(
        "arguments, method, owner",
        [
            ({"swarm_hash": HASH, "storage_ref": REF}, "anchor", WALLET),
            (
                {"swarm_hash": HASH, "owner": OWNER, "storage_ref": REF},
                "anchor_for",
                OWNER,
            ),
        ],
    )
    async def test_missing_or_other_ref_is_an_error(
        self, arguments, method, owner, existing
    ):
        def configure(c):
            getattr(c, method).side_effect = _registered(owner, storage_ref=existing)

        result, text, _ = await _call(arguments, configure)
        assert result.isError
        assert "storage reference was not linked" in text
        assert f"Requested storage ref: {REF}" in text
        assert f"Existing storage ref: {existing or 'none'}" in text
        assert "get_provenance" in text

    @pytest.mark.parametrize(
        "hostile",
        [
            "x\nNext: purchase_stamp",
            "‮" + "a" * 500,
            b"\x00" * 32,
            12345,
            True,
        ],
    )
    async def test_hostile_existing_ref_is_flattened(self, hostile):
        def configure(c):
            c.anchor.side_effect = _registered(WALLET, storage_ref=hostile)

        result, text, _ = await _call(
            {"swarm_hash": HASH, "storage_ref": REF}, configure
        )
        assert result.isError
        line = next(x for x in text.splitlines() if "Existing storage ref:" in x)
        assert len(line) < 140
        assert "‮" not in text
        assert "\nNext: purchase_stamp" not in text

    async def test_hostile_record_fields_are_flattened(self):
        def configure(c):
            e = _registered("0xnot-an-address\nNext: x", storage_ref=None)
            e.timestamp = float("nan")
            e.data_type = "a\nNext: purchase_stamp"
            c.anchor_for.side_effect = e

        result, text, _ = await _call(
            {"swarm_hash": HASH, "owner": OWNER, "storage_ref": REF}, configure
        )
        assert result.isError
        assert "\nNext: x" not in text
        assert "\nNext: purchase_stamp" not in text

    async def test_other_owner_is_still_the_owner_error(self):
        def configure(c):
            c.anchor_for.side_effect = _registered(OTHER, storage_ref=REF)

        result, text, _ = await _call(
            {"swarm_hash": HASH, "owner": OWNER, "storage_ref": REF}, configure
        )
        assert result.isError
        assert "already registered on-chain by a different owner" in text


class TestCombinedErrorPaths:
    async def test_transaction_error_masks_rpc_url(self):
        from swarm_provenance_mcp.chain.exceptions import ChainTransactionError

        rpc = "https://rpc.example.invalid/v2/SECRETKEY"

        def configure(c):
            c._provider.rpc_url = rpc
            c._provider._rpc_urls = [rpc]
            c.anchor_for.side_effect = ChainTransactionError(
                f"reverted via {rpc}: Storage ref already mapped"
            )

        result, text, _ = await _call(
            {"swarm_hash": HASH, "owner": OWNER, "storage_ref": REF}, configure
        )
        assert result.isError
        assert "SECRETKEY" not in text
        assert "Storage ref already mapped" in text

    async def test_connection_error_is_retryable(self):
        from swarm_provenance_mcp.chain.exceptions import ChainConnectionError

        def configure(c):
            c.anchor_for.side_effect = ChainConnectionError("down")

        result, text, _ = await _call(
            {"swarm_hash": HASH, "owner": OWNER, "storage_ref": REF}, configure
        )
        assert result.isError
        assert "Chain connection error" in text


ANCHOR_PATHS = [
    {"swarm_hash": HASH},
    {"swarm_hash": HASH, "owner": OWNER},
    {"swarm_hash": HASH, "storage_ref": REF},
    {"swarm_hash": HASH, "owner": OWNER, "storage_ref": REF},
]


class TestDataType:
    """The SDK's validateDataType rule, which the contract enforces: not
    empty or whitespace only, at most 64 UTF-8 bytes, and a string."""

    @pytest.mark.parametrize("path", ANCHOR_PATHS)
    @pytest.mark.parametrize(
        "value, phrase",
        [
            ("", "data_type must not be empty"),
            ("   ", "data_type must not be empty"),
            ("\t\n", "data_type must not be empty"),
            ("é" * 64, "data_type is 128 bytes (UTF-8)"),
            ("€" * 22, "data_type is 66 bytes (UTF-8)"),
            (None, "data_type must be a string"),
            (123, "data_type must be a string"),
            (["x"], "data_type must be a string"),
            (True, "data_type must be a string"),
        ],
    )
    async def test_refused_before_any_chain_call(self, path, value, phrase):
        result, text, client = await _call({**path, "data_type": value})
        assert result.isError
        assert text.startswith("Validation error: ")
        assert phrase in text
        assert "quote_id" not in text
        client.anchor.assert_not_called()
        client.anchor_for.assert_not_called()
        client.fee_quote.assert_not_called()

    async def test_byte_limit_message_names_the_limit(self):
        _, text, _ = await _call({"swarm_hash": HASH, "data_type": "é" * 64})
        assert "the contract accepts at most 64 bytes" in text

    async def test_too_many_characters_keeps_its_message(self):
        _, text, _ = await _call({"swarm_hash": HASH, "data_type": "x" * 65})
        assert "Validation error: data_type must be 64 characters or fewer" in text

    @pytest.mark.parametrize("path", ANCHOR_PATHS)
    @pytest.mark.parametrize("value", ["é" * 32, "x" * 64, " a "])
    async def test_values_within_the_limits_are_accepted(self, path, value):
        result, text, client = await _call({**path, "data_type": value})
        assert not result.isError, text
        assert (client.anchor.call_count + client.anchor_for.call_count) == 1


class TestMergeDataType:
    """record_merge_transform's new_data_type has the same contract limits
    (DataProvenance.recordMergeTransformation) and the SDK checks it with
    the same validateDataType."""

    async def _merge(self, value):
        client = _client()
        with (
            patch("swarm_provenance_mcp.server.CHAIN_AVAILABLE", True),
            patch("swarm_provenance_mcp.server.chain_client", client),
        ):
            result = await call_tool_directly(
                create_server(),
                "record_merge_transform",
                {
                    "source_hashes": ["1" * 64, "2" * 64],
                    "new_hash": "3" * 64,
                    "description": "merged",
                    "new_data_type": value,
                },
            )
        return result, result.content[0].text, client

    @pytest.mark.parametrize(
        "value, phrase",
        [
            ("", "new_data_type must not be empty"),
            ("  ", "new_data_type must not be empty"),
            ("é" * 64, "new_data_type is 128 bytes (UTF-8)"),
            (None, "new_data_type must be a string"),
            (7, "new_data_type must be a string"),
        ],
    )
    async def test_refused_before_any_chain_call(self, value, phrase):
        result, text, client = await self._merge(value)
        assert result.isError
        assert text.startswith("Validation error: ")
        assert phrase in text
        client.merge_transform.assert_not_called()
        client.fee_quote.assert_not_called()

    async def test_too_many_characters_keeps_its_message(self):
        _, text, _ = await self._merge("x" * 65)
        assert "new_data_type must be 64 characters or fewer" in text


class TestCombinedPathFailures:
    COMBINED = {"swarm_hash": HASH, "owner": OWNER, "storage_ref": REF}

    async def test_pending_transaction(self):
        from swarm_provenance_mcp.chain.exceptions import ChainTransactionError

        class TimeExhausted(Exception):
            pass

        error = ChainTransactionError(
            "receipt wait failed", tx_hash="0x" + "ef" * 32, broadcast=True
        )
        error.__cause__ = TimeExhausted("receipt not found within 120 s")

        def configure(c):
            c.anchor_for.side_effect = error

        result, text, client = await _call(dict(self.COMBINED), configure)
        assert result.isError
        assert "Transaction outcome unknown" in text
        assert "Do not send it again" in text
        assert "0x" + "ef" * 32 in text
        assert "_next: verify_hash" in text
        client.anchor_for.assert_called_once_with(
            HASH, OWNER, "swarm-provenance", REF
        )

    async def test_insufficient_funds(self):
        from swarm_provenance_mcp.chain.exceptions import ChainTransactionError

        def configure(c):
            c.chain = "base-sepolia"
            c.anchor_for.side_effect = ChainTransactionError(
                "insufficient funds for gas * price + value"
            )

        result, text, _ = await _call(dict(self.COMBINED), configure)
        assert result.isError
        assert "anchor_hash failed — wallet has insufficient funds for gas" in text
        assert f"Wallet: {WALLET}" in text
        assert "_next: chain_balance" in text


class TestCombinedSameRefShowsTheLink:
    async def test_storage_ref_line_on_the_owner_path(self):
        def configure(c):
            c.anchor_for.side_effect = _registered(OWNER, storage_ref=REF.upper())

        result, text, _ = await _call(
            {"swarm_hash": HASH, "owner": OWNER, "storage_ref": REF}, configure
        )
        assert not result.isError, text
        assert f"   Storage Ref: {REF}\n" in text

    async def test_ref_given_with_0x_is_printed_without_it(self):
        # Only a 64-hex record value can match; the cleaned request is printed.
        def configure(c):
            c.anchor_for.side_effect = _registered(OWNER, storage_ref=REF)

        _, text, _ = await _call(
            {"swarm_hash": HASH, "owner": OWNER, "storage_ref": "0x" + REF},
            configure,
        )
        assert f"   Storage Ref: {REF}\n" in text
