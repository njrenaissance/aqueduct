"""Tests for the shared validate gate (only files that passed ``validate --hash`` may be uploaded)."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from aqueduct import validated

pytestmark = pytest.mark.unit

_SHA = "a" * 64


def _write_results(path: Path, rows: list[dict]) -> None:
    columns = ["path", "status", "sha256", "hash_check", "segment_check"]
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        fh.write("# aqueduct acquisition (provenance)\r\n")
        writer = csv.DictWriter(fh, fieldnames=columns, restval="")
        writer.writeheader()
        writer.writerows(rows)


def _row(path: str, sha256: str = _SHA) -> dict:
    return {"path": path, "status": "ok", "sha256": sha256, "hash_check": "ok"}


def _manifest(path: str = "a.pdf", item_id: str | None = "guid-1") -> dict:
    return {"items": [{"path": path, "type": "file", "size": 3, "id": item_id}]}


def test_entry_carries_the_manifest_unique_id(tmp_path: Path) -> None:
    results = tmp_path / "validate_results.csv"
    _write_results(results, [_row("a.pdf")])

    eligible, _ = validated.load_entries(_manifest(item_id="guid-1"), results)

    assert eligible[0].item_id == "guid-1"


def test_entry_without_a_manifest_id_has_empty_item_id(tmp_path: Path) -> None:
    results = tmp_path / "validate_results.csv"
    _write_results(results, [_row("a.pdf")])

    eligible, _ = validated.load_entries(_manifest(item_id=None), results)

    assert eligible[0].item_id == ""


def test_reference_hash_that_disagrees_with_validated_hash_is_rejected(tmp_path: Path) -> None:
    results = tmp_path / "validate_results.csv"
    _write_results(results, [_row("a.pdf", sha256=_SHA)])

    eligible, rejected = validated.load_entries(_manifest(), results, reference={"a.pdf": "b" * 64})

    assert eligible == []
    assert [(e.path, reason) for e, reason in rejected] == [("a.pdf", "differs from filecopy record")]


def test_reference_hash_that_agrees_is_admitted(tmp_path: Path) -> None:
    results = tmp_path / "validate_results.csv"
    _write_results(results, [_row("a.pdf", sha256=_SHA)])

    eligible, rejected = validated.load_entries(_manifest(), results, reference={"a.pdf": _SHA})

    assert [e.path for e in eligible] == ["a.pdf"]
    assert rejected == []


def test_file_absent_from_reference_is_not_rejected_for_it(tmp_path: Path) -> None:
    results = tmp_path / "validate_results.csv"
    _write_results(results, [_row("a.pdf")])

    eligible, _ = validated.load_entries(_manifest(), results, reference={"other.pdf": "b" * 64})

    assert [e.path for e in eligible] == ["a.pdf"]
