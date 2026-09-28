"""validate: size + completeness reconciliation, SHA-256 recording, and hash
verification against a recorded reference."""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

from aqueduct import validate


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
