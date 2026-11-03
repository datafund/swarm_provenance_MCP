"""Swarm single-chunk address (BMT hash), following Bee's pkg/bmt and pkg/cac.

The address of a content-addressed chunk is
``keccak256(span || BMT root of the payload)``. The span is the payload
length as 8 bytes, little-endian. The BMT root is a binary Merkle tree over
the payload, zero-padded to 4096 bytes and split into 128 segments of 32
bytes, where each parent is the keccak256 of its two children.

Only payloads of one chunk (at most 4096 bytes) are supported. Larger
content is a tree of chunks, and a ``/bzz`` reference names a manifest; this
module checks neither.
"""

from eth_utils import keccak

CHUNK_SIZE = 4096
SEGMENT_SIZE = 32
SPAN_SIZE = 8


def chunk_address(payload: bytes) -> bytes:
    """Return the 32-byte Swarm address of a single chunk holding ``payload``.

    Raises:
        TypeError: If payload is not bytes.
        ValueError: If payload is longer than one chunk (4096 bytes).
    """
    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    if len(payload) > CHUNK_SIZE:
        raise ValueError(f"payload is larger than one chunk ({CHUNK_SIZE} bytes)")
    padded = payload.ljust(CHUNK_SIZE, b"\x00")
    level = [
        padded[i : i + SEGMENT_SIZE] for i in range(0, CHUNK_SIZE, SEGMENT_SIZE)
    ]
    while len(level) > 1:
        level = [keccak(level[i] + level[i + 1]) for i in range(0, len(level), 2)]
    span = len(payload).to_bytes(SPAN_SIZE, "little")
    return keccak(span + level[0])
