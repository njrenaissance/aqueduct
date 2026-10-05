"""validate: size + completeness reconciliation, SHA-256 recording, and hash
verification against a recorded reference."""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import pytest

from aqueduct import validate

pytestmark = pytest.mark.unit


def _manifest(items):
    return {"items": items}


def _read_csv_rows(path: Path) -> list[dict]:
    """Read CSV file, skipping provenance comment lines (# prefix)."""
    with open(path, encoding="utf-8-sig") as fh:
        # Skip comment lines
        for line in fh:
            if not line.strip().startswith("#"):
                # Found first non-comment line; parse as CSV
                reader = csv.DictReader(fh)
                # The line we found is part of the CSV header, so we need to
                # handle it specially. Actually, better to reopen and use a filter.
                break
    # Reopen and filter comments
    with open(path, encoding="utf-8-sig") as fh:
        filtered = (line for line in fh if not line.strip().startswith("#"))
        reader = csv.DictReader(filtered)
        return list(reader)


def test_validate_pass(tmp_path):
    dest = tmp_path / "dl"
    (dest / "sub").mkdir(parents=True)
    (dest / "sub" / "a.txt").write_bytes(b"12345")
    rc = validate.validate(
        _manifest([{"path": "sub/a.txt", "type": "file", "size": 5}]),
        dest,
        do_hash=False,
        results_path=tmp_path / "r.csv",
        reference={},
    )
    assert rc == 0


def test_validate_flags_missing_and_size_mismatch(tmp_path):
    dest = tmp_path / "dl"
    dest.mkdir()
    (dest / "b.txt").write_bytes(b"123")  # 3 bytes on disk, manifest says 5
    rc = validate.validate(
        _manifest(
            [
                {"path": "a.txt", "type": "file", "size": 5},  # missing
                {"path": "b.txt", "type": "file", "size": 5},  # size mismatch
            ]
        ),
        dest,
        do_hash=False,
        results_path=tmp_path / "r.csv",
        reference={},
    )
    assert rc == 1


def test_validate_flags_extra_file_on_disk(tmp_path):
    dest = tmp_path / "dl"
    dest.mkdir()
    (dest / "a.txt").write_bytes(b"12345")
    (dest / "extra.bin").write_bytes(b"x")  # not in the manifest
    rc = validate.validate(
        _manifest([{"path": "a.txt", "type": "file", "size": 5}]),
        dest,
        do_hash=False,
        results_path=tmp_path / "r.csv",
        reference={},
    )
    assert rc == 1


def test_validate_records_sha256_when_requested(tmp_path):
    dest = tmp_path / "dl"
    dest.mkdir()
    (dest / "a.txt").write_bytes(b"12345")
    results = tmp_path / "r.csv"
    rc = validate.validate(
        _manifest([{"path": "a.txt", "type": "file", "size": 5}]),
        dest,
        do_hash=True,
        results_path=results,
        reference={},
    )
    assert rc == 0
    rows = _read_csv_rows(results)
    assert rows[0]["sha256"] == hashlib.sha256(b"12345").hexdigest()


def test_validate_verifies_hash_match(tmp_path):
    dest = tmp_path / "dl"
    dest.mkdir()
    (dest / "a.txt").write_bytes(b"12345")
    good = hashlib.sha256(b"12345").hexdigest()
    rc = validate.validate(
        _manifest([{"path": "a.txt", "type": "file", "size": 5}]),
        dest,
        do_hash=True,
        results_path=tmp_path / "r.csv",
        reference={"a.txt": good},
    )
    assert rc == 0


def test_validate_flags_hash_mismatch(tmp_path):
    dest = tmp_path / "dl"
    dest.mkdir()
    (dest / "a.txt").write_bytes(b"12345")  # right size, wrong recorded hash
    rc = validate.validate(
        _manifest([{"path": "a.txt", "type": "file", "size": 5}]),
        dest,
        do_hash=True,
        results_path=tmp_path / "r.csv",
        reference={"a.txt": "0" * 64},
    )
    assert rc == 1  # content drift from the recorded fingerprint is a failure


def test_validate_loads_segment_sidecar(tmp_path):
    """Verify sidecar JSON is loaded correctly with expected fields."""
    dest = tmp_path / "dl"
    dest.mkdir()

    # Create test file
    file_data = b"12345" * 100  # 500 bytes
    (dest / "file.bin").write_bytes(file_data)

    # Create segment sidecar
    seg0_hash = hashlib.sha256(file_data[:256]).hexdigest()
    seg1_hash = hashlib.sha256(file_data[256:]).hexdigest()
    sidecar = {
        "file_path": str(dest / "file.bin"),
        "file_size_bytes": len(file_data),
        "segment_size_bytes": 256,
        "segment_count": 2,
        "segments": [
            {
                "segment_index": 0,
                "offset_bytes": 0,
                "size_bytes": 256,
                "sha256": seg0_hash,
            },
            {
                "segment_index": 1,
                "offset_bytes": 256,
                "size_bytes": 244,
                "sha256": seg1_hash,
            },
        ],
    }
    sidecar_path = dest / "file.bin.segments.json"
    sidecar_path.write_text(__import__("json").dumps(sidecar))

    # Validate should load and use the sidecar
    rc = validate.validate(
        _manifest([{"path": "file.bin", "type": "file", "size": len(file_data)}]),
        dest,
        do_hash=False,
        results_path=tmp_path / "r.csv",
        reference={},
    )
    assert rc == 0
    rows = _read_csv_rows(tmp_path / "r.csv")
    assert rows[0]["segment_check"] == "ok"


def test_validate_detects_segment_corruption(tmp_path):
    """Verify segment corruption is detected and reported accurately."""
    dest = tmp_path / "dl"
    dest.mkdir()

    # Create test file
    file_data = b"A" * 100 + b"B" * 100 + b"C" * 100  # 300 bytes, 3 segments
    file_path = dest / "file.bin"
    file_path.write_bytes(file_data)

    # Create segment sidecar with correct hashes
    segments = [
        {"segment_index": 0, "offset_bytes": 0, "size_bytes": 100, "sha256": hashlib.sha256(b"A" * 100).hexdigest()},
        {"segment_index": 1, "offset_bytes": 100, "size_bytes": 100, "sha256": hashlib.sha256(b"B" * 100).hexdigest()},
        {"segment_index": 2, "offset_bytes": 200, "size_bytes": 100, "sha256": hashlib.sha256(b"C" * 100).hexdigest()},
    ]
    sidecar = {
        "file_path": str(file_path),
        "file_size_bytes": 300,
        "segment_size_bytes": 100,
        "segment_count": 3,
        "segments": segments,
    }
    (dest / "file.bin.segments.json").write_text(__import__("json").dumps(sidecar))

    # Corrupt segment 1
    corrupted_data = b"A" * 100 + b"X" * 100 + b"C" * 100
    file_path.write_bytes(corrupted_data)

    # Validate should detect corruption
    rc = validate.validate(
        _manifest([{"path": "file.bin", "type": "file", "size": 300}]),
        dest,
        do_hash=False,
        results_path=tmp_path / "r.csv",
        reference={},
    )
    assert rc == 1  # Failure due to segment mismatch
    rows = _read_csv_rows(tmp_path / "r.csv")
    assert rows[0]["segment_check"] == "mismatch"
    assert "1" in rows[0]["corrupted_segments"]


def test_validate_warns_on_truncated_file(tmp_path):
    """Verify warning is logged when file size mismatches sidecar metadata."""
    dest = tmp_path / "dl"
    dest.mkdir()

    # Create test file with 100 bytes
    file_path = dest / "file.bin"
    file_path.write_bytes(b"A" * 100)

    # Create sidecar claiming 200 bytes (simulate truncation after download)
    sidecar = {
        "file_path": str(file_path),
        "file_size_bytes": 200,
        "segment_size_bytes": 100,
        "segment_count": 2,
        "segments": [
            {"segment_index": 0, "offset_bytes": 0, "size_bytes": 100, "sha256": "a" * 64},
            {"segment_index": 1, "offset_bytes": 100, "size_bytes": 100, "sha256": "b" * 64},
        ],
    }
    (dest / "file.bin.segments.json").write_text(__import__("json").dumps(sidecar))

    # Size check should fail first (actual 100, expected 200)
    rc = validate.validate(
        _manifest([{"path": "file.bin", "type": "file", "size": 200}]),
        dest,
        do_hash=False,
        results_path=tmp_path / "r.csv",
        reference={},
    )
    assert rc == 1  # Mismatch: 100 on disk, 200 expected


def test_validate_handles_empty_file_with_segments(tmp_path):
    """Verify zero-byte files validate correctly even with segment metadata."""
    dest = tmp_path / "dl"
    dest.mkdir()

    # Create empty file
    file_path = dest / "empty.bin"
    file_path.write_bytes(b"")

    # Create sidecar for empty file (no segments)
    sidecar = {
        "file_path": str(file_path),
        "file_size_bytes": 0,
        "segment_size_bytes": 1024,
        "segment_count": 0,
        "segments": [],
    }
    (dest / "empty.bin.segments.json").write_text(__import__("json").dumps(sidecar))

    rc = validate.validate(
        _manifest([{"path": "empty.bin", "type": "file", "size": 0}]),
        dest,
        do_hash=False,
        results_path=tmp_path / "r.csv",
        reference={},
    )
    assert rc == 0  # Success
    rows = _read_csv_rows(tmp_path / "r.csv")
    assert rows[0]["status"] == "ok"
    assert rows[0]["actual_bytes"] == "0"


_PROVENANCE = "# aqueduct acquisition (provenance; full run metadata)\r\n# tool: filecopy 1.0\r\n"


def test_load_reference_skips_provenance_lines(tmp_path):
    ref_csv = tmp_path / "filecopy_results.csv"
    content = _PROVENANCE + "path,status,sha256\r\na.txt,ok," + "a" * 64 + "\r\n"
    ref_csv.write_bytes(content.encode("utf-8-sig"))  # bytes: write_text would turn \r\n into \r\r\n on Windows
    assert validate._load_reference(ref_csv) == {"a.txt": "a" * 64}


def test_load_reference_absent_file_is_empty(tmp_path):
    assert validate._load_reference(tmp_path / "missing.csv") == {}


def test_load_reference_wrong_schema_raises(tmp_path):
    ref_csv = tmp_path / "filecopy_results.csv"
    ref_csv.write_text("foo,bar\r\n1,2\r\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not a filecopy results CSV"):
        validate._load_reference(ref_csv)


def test_validate_prints_a_progress_line_every_ten_files(tmp_path, capsys):
    dest = tmp_path / "dl"
    dest.mkdir()
    items = []
    for i in range(25):
        (dest / f"f{i}.txt").write_bytes(b"x")
        items.append({"path": f"f{i}.txt", "type": "file", "size": 1})

    validate.validate(_manifest(items), dest, do_hash=False, results_path=tmp_path / "r.csv", reference={})

    out = capsys.readouterr().out
    assert "...10/25 checked" in out
    assert "...20/25 checked" in out
    assert "...25/25 checked" not in out


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _hash_dir(tmp_path, sizes: dict[str, int]):
    dest = tmp_path / "dl"
    dest.mkdir()
    for name, size in sizes.items():
        (dest / name).write_bytes(b"x" * size)
    items = [{"path": name, "type": "file", "size": size} for name, size in sizes.items()]
    return dest, items


def _run_hash(tmp_path, dest, items, clock, reference=None):
    return validate.validate(
        _manifest(items),
        dest,
        do_hash=True,
        results_path=tmp_path / "r.csv",
        reference=reference or {},
        clock=clock,
    )


def _progress_lines(out: str) -> list[str]:
    return [line.strip() for line in out.splitlines() if line.startswith("  ...")]


@pytest.mark.parametrize(
    ("with_bytes", "total_bytes", "done_bytes", "expected"),
    [
        pytest.param(True, 91_500_000_000, 38_200_000_000, "...3/1,380 files, 38.2 GB of 91.5 GB (41%)", id="bytes"),
        pytest.param(True, 0, 0, "...3/1,380 files, 0.0 GB of 0.0 GB (100%)", id="zero_total_no_divide_by_zero"),
        pytest.param(False, 5, 0, "...3/1380 checked", id="size_only_line_unchanged"),
    ],
)
def test_progress_line_format(with_bytes, total_bytes, done_bytes, expected):
    reporter = validate._ProgressReporter(1380, total_bytes, with_bytes, clock=_FakeClock(), every=10_000)
    reporter.add_bytes(done_bytes)
    for _ in range(3):
        reporter.file_done(0)
    assert reporter.line().strip() == expected


def test_hash_progress_every_ten_files_with_frozen_clock(tmp_path, capsys):
    dest, items = _hash_dir(tmp_path, {f"f{i}.txt": 1 for i in range(25)})

    _run_hash(tmp_path, dest, items, _FakeClock())

    lines = _progress_lines(capsys.readouterr().out)
    assert [ln.split(" files")[0] for ln in lines] == ["...10/25", "...20/25"]
    assert lines[0].endswith("(40%)")


def test_hash_progress_on_timer_inside_one_large_file(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(validate, "_HASH_CHUNK", 10)
    dest, items = _hash_dir(tmp_path, {"big.bin": 40})
    clock = _FakeClock()
    real_add = validate._ProgressReporter.add_bytes

    def ticking_add(self, count):
        clock.now += 20  # 20 s per chunk: due after every second chunk
        real_add(self, count)

    monkeypatch.setattr(validate._ProgressReporter, "add_bytes", ticking_add)

    _run_hash(tmp_path, dest, items, clock)

    lines = _progress_lines(capsys.readouterr().out)
    assert [ln.split(" files")[0] for ln in lines] == ["...0/1", "...0/1"]  # chunks 2 and 4, none on 1 and 3
    assert lines[0].endswith("(50%)")
    assert lines[1].endswith("(100%)")


def test_timer_resets_after_each_line():
    clock = _FakeClock()
    reporter = validate._ProgressReporter(5, 100, True, clock=clock, interval=30, every=1000)
    clock.now = 31
    reporter.add_bytes(1)  # emits, resets the timer
    clock.now = 50
    reporter.add_bytes(1)  # only 19 s since the last line: silent
    assert reporter._last == 31


@pytest.mark.parametrize(
    ("do_hash", "name", "announced"),
    [
        pytest.param(True, "big.bin", True, id="large_hashed_file_announced"),
        pytest.param(True, "small.bin", False, id="small_file_not_announced"),
        pytest.param(False, "big.bin", False, id="not_announced_without_hash"),
    ],
)
def test_large_file_announced_when_hashing_starts(tmp_path, capsys, monkeypatch, do_hash, name, announced):
    monkeypatch.setattr(validate, "_LARGE_BYTES", 100)
    dest, items = _hash_dir(tmp_path, {"big.bin": 200, "small.bin": 5})
    items = [i for i in items if i["path"] == name]

    validate.validate(
        _manifest(items), dest, do_hash=do_hash, results_path=tmp_path / "r.csv", reference={}, clock=_FakeClock()
    )

    assert (f"hashing {name}" in capsys.readouterr().out) is announced


def test_bytes_reach_100_percent_with_missing_and_mismatched_files(tmp_path, capsys):
    sizes = {f"ok{i}.txt": 1 for i in range(8)}
    dest, items = _hash_dir(tmp_path, {**sizes, "short.txt": 3})
    items[-1]["size"] = 8  # on disk: 3
    items.append({"path": "gone.txt", "type": "file", "size": 2})  # missing; 10 files in all

    _run_hash(tmp_path, dest, items, _FakeClock())

    lines = _progress_lines(capsys.readouterr().out)
    assert lines[-1].endswith("(100%)")


def test_progress_does_not_change_results_csv(tmp_path):
    dest, items = _hash_dir(tmp_path, {"a.txt": 5})
    _run_hash(tmp_path, dest, items, _FakeClock(), reference={"a.txt": hashlib.sha256(b"x" * 5).hexdigest()})

    row = _read_csv_rows(tmp_path / "r.csv")[0]
    assert (row["status"], row["sha256"], row["hash_check"]) == ("ok", hashlib.sha256(b"x" * 5).hexdigest(), "ok")
