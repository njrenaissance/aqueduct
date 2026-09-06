"""QuickXorHash — the content hash SharePoint/OneDrive reports for every file.

This is a faithful port of the algorithm Microsoft documents and that rclone
implements in Go. It is the ONLY hash we can compare against what Graph gives us
for a shared file, so getting it exactly right is what lets us prove a download
is byte-for-byte complete.

Do not trust this implementation on the strength of code review alone: run
`odenum selftest <share-url>`, which downloads a real file from the
share and checks that this code reproduces the quickXorHash that Graph reported
for it. Only a green self-test makes `verify` meaningful.
"""

from __future__ import annotations

import base64

_WIDTH_IN_BITS = 160
_SHIFT = 11
_BITS_IN_LAST_CELL = 32
_DATA_SIZE = (_WIDTH_IN_BITS - 1) // 64 + 1  # 3 x uint64
_MASK64 = 0xFFFFFFFFFFFFFFFF


class QuickXorHash:
    """Incremental QuickXorHash. Feed bytes with update(), read base64digest()."""

    def __init__(self) -> None:
        self._data = [0, 0, 0]
        self._length_so_far = 0
        self._shift_so_far = 0

    def update(self, p: bytes) -> QuickXorHash:
        data = self._data
        vector_array_index = self._shift_so_far // 64
        vector_offset = self._shift_so_far % 64
        length = len(p)
        iterations = min(length, _WIDTH_IN_BITS)

        for i in range(iterations):
            is_last_cell = vector_array_index == _DATA_SIZE - 1
            bits_in_vector_cell = _BITS_IN_LAST_CELL if is_last_cell else 64

            # XOR of every byte that lands in this bit position (they are all
            # shifted by the same amount, so fold them together first).
            xored = 0
            for j in range(i, length, _WIDTH_IN_BITS):
                xored ^= p[j]

            if vector_offset <= bits_in_vector_cell - 8:
                data[vector_array_index] ^= (xored << vector_offset) & _MASK64
            else:
                index2 = 0 if is_last_cell else vector_array_index + 1
                low = bits_in_vector_cell - vector_offset
                data[vector_array_index] ^= (xored << vector_offset) & _MASK64
                data[index2] ^= xored >> low

            vector_offset += _SHIFT
            while vector_offset >= bits_in_vector_cell:
                vector_array_index = 0 if is_last_cell else vector_array_index + 1
                vector_offset -= bits_in_vector_cell
                is_last_cell = vector_array_index == _DATA_SIZE - 1
                bits_in_vector_cell = _BITS_IN_LAST_CELL if is_last_cell else 64

        self._shift_so_far = (
            self._shift_so_far + _SHIFT * (length % _WIDTH_IN_BITS)
        ) % _WIDTH_IN_BITS
        self._length_so_far += length
        return self

    def digest(self) -> bytes:
        h = bytearray(20)
        h[0:8] = (self._data[0] & _MASK64).to_bytes(8, "little")
        h[8:16] = (self._data[1] & _MASK64).to_bytes(8, "little")
        h[16:20] = (self._data[2] & 0xFFFFFFFF).to_bytes(4, "little")
        # Fold the total length into the trailing 8 bytes.
        length_bytes = self._length_so_far.to_bytes(8, "little")
        for i in range(8):
            h[12 + i] ^= length_bytes[i]
        return bytes(h)

    def base64digest(self) -> str:
        return base64.b64encode(self.digest()).decode("ascii")

    def hexdigest(self) -> str:
        return self.digest().hex()


def hash_file(path, chunk_size: int = 1024 * 1024) -> str:
    """QuickXorHash of a file on disk, returned as base64 (Graph's format)."""
    h = QuickXorHash()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.base64digest()


if __name__ == "__main__":
    # Trivial known-answer test: empty input -> 20 zero bytes.
    empty = QuickXorHash().base64digest()
    expected_empty = base64.b64encode(bytes(20)).decode()
    assert empty == expected_empty, (empty, expected_empty)
    print("empty-input KAT passed:", empty)
    print("NOTE: this only exercises the trivial path. Run")
    print("  odenum selftest <share-url>")
    print("to validate the shift logic against a real Graph-reported hash.")
