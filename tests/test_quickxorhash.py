"""Tests for the QuickXorHash implementation (copied from the sibling ``courier`` project, ADR-0012).

The two base64 vectors below are derived by hand from Microsoft's published
algorithm (not from this implementation), so they genuinely validate the code:

* empty input -> the 20-byte hash is all zeros.
* a single 0x01 byte -> data cell 0 gets bit 0 set, and the length (1) is XORed
  into the low byte at offset 12, giving 0x01 at positions 0 and 12.
"""

from __future__ import annotations

import os

import pytest

from aqueduct.quickxorhash import DIGEST_SIZE, QuickXorHash, hash_file

pytestmark = pytest.mark.unit

_EMPTY = "AAAAAAAAAAAAAAAAAAAAAAAAAAA="
_SINGLE_ONE = "AQAAAAAAAAAAAAAAAQAAAAAAAAA="


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        pytest.param(b"", _EMPTY, id="empty"),
        pytest.param(b"\x01", _SINGLE_ONE, id="single_one_byte"),
    ],
)
def test_known_vectors(data: bytes, expected: str) -> None:
    hasher = QuickXorHash()
    hasher.update(data)
    assert hasher.base64digest() == expected


def test_digest_is_twenty_bytes() -> None:
    hasher = QuickXorHash()
    hasher.update(b"anything")
    assert len(hasher.digest()) == DIGEST_SIZE


def test_streaming_matches_single_update() -> None:
    payload = bytes((i * 37 + 5) % 256 for i in range(5000))
    whole = QuickXorHash()
    whole.update(payload)

    chunked = QuickXorHash()
    for start in range(0, len(payload), 128):
        chunked.update(payload[start : start + 128])

    assert chunked.base64digest() == whole.base64digest()


def test_hash_file_matches_in_memory(tmp_path) -> None:
    payload = os.urandom(3000)
    path = tmp_path / "blob.bin"
    path.write_bytes(payload)

    in_memory = QuickXorHash()
    in_memory.update(payload)

    assert hash_file(path) == in_memory.base64digest()
