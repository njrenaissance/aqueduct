"""filecopy URL construction, segment hashing, and sidecar writing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aqueduct import filecopy


def test_download_url_uses_download_aspx_and_encodes_path():
    url = filecopy._download_url("https://host/personal/u", "/personal/u/Documents/a b.txt")
    assert url.startswith("https://host/personal/u/_layouts/15/download.aspx?SourceUrl=")
    # spaces and slashes in the source path are percent-encoded
    assert "%2FDocuments%2Fa%20b.txt" in url


@pytest.mark.parametrize(
    ("file_size", "segment_size", "expected_count"),
    [
        pytest.param(100, 1024, 1, id="single_small_segment"),
        pytest.param(1024, 1024, 1, id="single_exact_boundary"),
        pytest.param(2048, 1024, 2, id="two_exact_segments"),
        pytest.param(2048 + 1, 1024, 3, id="two_full_plus_partial"),
        pytest.param(1024 * 1024, 1024, 1024, id="one_mib_with_1kb_segments"),
    ],
)
def test_segment_hasher_computes_correct_segment_count(file_size, segment_size, expected_count):
    """_SegmentHasher correctly tracks segment boundaries."""
    hasher = filecopy._SegmentHasher(segment_size)
    data = b"x" * file_size
    hasher.update(data)
    segments = hasher.finalize()
    assert len(segments) == expected_count


def test_segment_hasher_tracks_offsets_and_sizes():
    """_SegmentHasher correctly records offset and size for each segment."""
    segment_size = 100
    hasher = filecopy._SegmentHasher(segment_size)
    data = b"a" * 250  # 2.5 segments
    hasher.update(data)
    segments = hasher.finalize()
    assert len(segments) == 3
    assert segments[0]["offset_bytes"] == 0
    assert segments[0]["size_bytes"] == 100
    assert segments[1]["offset_bytes"] == 100
    assert segments[1]["size_bytes"] == 100
    assert segments[2]["offset_bytes"] == 200
    assert segments[2]["size_bytes"] == 50


def test_segment_hasher_indices_sequential():
    """_SegmentHasher assigns sequential segment_index to each segment."""
    hasher = filecopy._SegmentHasher(100)
    hasher.update(b"x" * 350)  # 3.5 segments = 4 segments total
    segments = hasher.finalize()
    indices = [s["segment_index"] for s in segments]
    assert indices == [0, 1, 2, 3]


def test_segment_hasher_computes_distinct_hashes():
    """Different segment data produces different SHA-256 hashes."""
    hasher = filecopy._SegmentHasher(10)
    hasher.update(b"aaaaaaaaaa")  # segment 0: 10 a's
    hasher.update(b"bbbbbbbbbb")  # segment 1: 10 b's
    segments = hasher.finalize()
    assert segments[0]["sha256"] != segments[1]["sha256"]


def test_segments_from_part_round_trip(tmp_path: Path):
    """_segments_from_part recomputes hashes for a complete file on disk."""
    segment_size = 100
    file_path = tmp_path / "testfile.bin"
    data = b"a" * 250  # 2.5 segments
    file_path.write_bytes(data)

    segments = filecopy._segments_from_part(file_path, segment_size)
    assert len(segments) == 3
    assert segments[0]["size_bytes"] == 100
    assert segments[2]["size_bytes"] == 50
    # All segments should have sha256 computed
    assert all("sha256" in s for s in segments)


def test_write_segment_sidecar_creates_json(tmp_path: Path):
    """_write_segment_sidecar writes a properly formatted JSON sidecar."""
    target = tmp_path / "file.bin"
    target.touch()
    segments = [
        {"segment_index": 0, "offset_bytes": 0, "size_bytes": 1024, "sha256": "abc123"},
        {"segment_index": 1, "offset_bytes": 1024, "size_bytes": 512, "sha256": "def456"},
    ]
    file_size = 1536
    segment_size = 1024

    filecopy._write_segment_sidecar(target, segments, file_size, segment_size)

    sidecar_path = tmp_path / "file.bin.segments.json"
    assert sidecar_path.exists()
    data = json.loads(sidecar_path.read_text())
    assert data["file_path"] == str(target)
    assert data["file_size_bytes"] == file_size
    assert data["segment_size_bytes"] == segment_size
    assert data["segment_count"] == 2
    assert data["segments"] == segments


def test_write_segment_sidecar_empty_segments(tmp_path: Path):
    """_write_segment_sidecar returns early for empty segment list."""
    target = tmp_path / "file.bin"
    target.touch()
    filecopy._write_segment_sidecar(target, [], 100, 1024)
    sidecar_path = tmp_path / "file.bin.segments.json"
    assert not sidecar_path.exists()


def test_prior_hashes_reads_current_results_schema(tmp_path: Path):
    """Regression: a results CSV written with the segment columns must load and yield (path, size) -> sha256."""
    results = tmp_path / "filecopy_results.csv"
    row = ["a/b.pdf", "ok", "10", "1", "1.0", "ab" * 32, "1", "1073741824", "a/b.pdf.segments.json", ""]
    filecopy._write_results(results, {"a/b.pdf": row})

    prior = filecopy._load_prior_rows(results)

    assert filecopy._prior_hashes(prior) == {("a/b.pdf", 10): "ab" * 32}
