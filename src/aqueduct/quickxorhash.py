"""QuickXorHash - Microsoft's content hash for OneDrive/SharePoint files.

SharePoint exposes each file's ``quickXorHash`` in the Graph ``file.hashes`` facet. To *verify* an upload
we compute the same hash locally over the bytes we send and compare it to the value Graph reports. This is
a faithful port of Microsoft's published algorithm (the same one rclone implements): a 160-bit rolling XOR
of the input at a shifting bit offset, with the total length XORed into the low bytes at the end.

Copied from the sibling ``courier`` project (ADR-0012), where it is pinned by hand-derived test vectors.
It is used only to verify uploads against SharePoint; SHA-256 stays the evidence hash (ADR-0006).

The class streams: feed bytes with :meth:`update`, then read :meth:`digest` / :meth:`base64digest` once.
Hashing in chunks yields the same result as hashing the whole input at once.
"""

from __future__ import annotations

import base64
import struct
from pathlib import Path

_WIDTH_IN_BITS = 160
_SHIFT = 11
_BITS_IN_LAST_CELL = 32
_CELLS = (_WIDTH_IN_BITS - 1) // 64 + 1  # 3 x uint64 (last cell uses only 32 bits)
_U64 = 0xFFFFFFFFFFFFFFFF

DIGEST_SIZE = 20  # 160 bits
_HASH_CHUNK = 4 * 1024 * 1024


class QuickXorHash:
    """Incremental QuickXorHash. Not thread-safe; one instance per file."""

    def __init__(self) -> None:
        self._data = [0] * _CELLS
        self._length_so_far = 0
        self._shift_so_far = 0

    def update(self, data: bytes) -> None:
        """Feed the next chunk of bytes into the hash."""
        n = len(data)
        if n == 0:
            return
        vector_array_index = self._shift_so_far // 64
        vector_offset = self._shift_so_far % 64
        iterations = min(n, _WIDTH_IN_BITS)

        for i in range(iterations):
            is_last_cell = vector_array_index == _CELLS - 1
            bits_in_vector_cell = _BITS_IN_LAST_CELL if is_last_cell else 64
            # XOR of every byte that lands on this bit offset (stride = hash width).
            xored = 0
            for j in range(i, n, _WIDTH_IN_BITS):
                xored ^= data[j]

            if vector_offset <= bits_in_vector_cell - 8:
                self._data[vector_array_index] ^= xored << vector_offset
            else:
                index2 = 0 if is_last_cell else vector_array_index + 1
                low = bits_in_vector_cell - vector_offset
                self._data[vector_array_index] ^= xored << vector_offset
                self._data[index2] ^= xored >> low

            vector_offset += _SHIFT
            while vector_offset >= bits_in_vector_cell:
                vector_array_index = 0 if is_last_cell else vector_array_index + 1
                vector_offset -= bits_in_vector_cell

        self._shift_so_far = (self._shift_so_far + _SHIFT * (n % _WIDTH_IN_BITS)) % _WIDTH_IN_BITS
        self._length_so_far += n

    def digest(self) -> bytes:
        """Return the 20-byte QuickXorHash of everything fed so far."""
        buf = bytearray(_CELLS * 8)
        for i in range(_CELLS):
            struct.pack_into("<Q", buf, i * 8, self._data[i] & _U64)
        rgb = bytearray(buf[:DIGEST_SIZE])
        length_bytes = struct.pack("<Q", self._length_so_far & _U64)
        offset = DIGEST_SIZE - len(length_bytes)  # XOR length into the low 8 bytes
        for i in range(len(length_bytes)):
            rgb[offset + i] ^= length_bytes[i]
        return bytes(rgb)

    def base64digest(self) -> str:
        """Return the hash as base64 - the exact form Graph reports it in."""
        return base64.b64encode(self.digest()).decode("ascii")


def hash_file(path: Path) -> str:
    """base64 QuickXorHash of a file already on disk, read in chunks."""
    hasher = QuickXorHash()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(_HASH_CHUNK), b""):
            hasher.update(block)
    return hasher.base64digest()
