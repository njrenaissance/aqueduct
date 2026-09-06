"""QuickXorHash correctness: the hash is our byte-for-byte evidence, so it must be
stable regardless of how the input is chunked."""

from __future__ import annotations

import base64

from onedrive_enum.quickxor import QuickXorHash, hash_file


def test_empty_input_is_twenty_zero_bytes():
    assert QuickXorHash().base64digest() == base64.b64encode(bytes(20)).decode()


def test_chunk_boundaries_do_not_change_digest():
    # A resumed download feeds bytes in different-sized pieces than a fresh one;
    # the digest must be identical either way.
    data = bytes((i * 37) % 256 for i in range(5000))
    whole = QuickXorHash()
    whole.update(data)
    pieces = QuickXorHash()
    for i in range(0, len(data), 101):
        pieces.update(data[i : i + 101])
    assert whole.base64digest() == pieces.base64digest()


def test_hash_file_matches_incremental(tmp_path):
    data = b"the quick brown fox " * 1000
    p = tmp_path / "f.bin"
    p.write_bytes(data)
    h = QuickXorHash()
    h.update(data)
    assert hash_file(p) == h.base64digest()
