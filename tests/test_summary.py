"""Tests for the run summary page (issue #16)."""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import pytest

from aqueduct import blobupload, filecopy, summary, validate


def _stage(title: str, counts: dict[str, int] | None, failed: tuple[str, ...] = ("fail",)) -> summary.Stage:
    return summary.Stage(title, counts, failed, {s: f"meaning of {s}" for s in (*(counts or {}), *failed)})


def _render(*stages: summary.Stage) -> str:
    return summary.render_summary(
        stages, tool_version="9.9", destination="vault/m/c", run_id="RUN1", total_bytes=1234, data_digest="d" * 64
    )


def test_page_states_counts_and_failures_by_status() -> None:
    page = _render(_stage("Uploaded", {"ok": 3, "fail": 1, "conflict": 2}, ("fail", "rejected", "conflict")))

    assert "| Uploaded | 6 | 3 | 3 | fail: 1, conflict: 2 |" in page


def test_page_carries_run_facts() -> None:
    page = _render(_stage("Uploaded", {"ok": 1}))

    assert "aqueduct 9.9" in page
    assert "`vault/m/c`" in page
    assert "`RUN1`" in page
    assert "1,234" in page
    assert "d" * 64 in page


def test_overall_result_is_pass_only_when_no_stage_failed() -> None:
    assert "**Overall: PASS**" in _render(_stage("A", {"ok": 2}), _stage("B", {"ok": 1}))
    assert "**Overall: FAIL**" in _render(_stage("A", {"ok": 2}), _stage("B", {"fail": 1}))


def test_a_stage_with_no_record_is_shown_as_not_run_and_the_run_does_not_pass() -> None:
    page = _render(_stage("Downloaded", None), _stage("Uploaded", {"ok": 1}))

    assert "| Downloaded | not run | not run | not run | not run |" in page
    assert "**Overall: FAIL**" in page
    assert "No results were found for: Downloaded." in page


def test_an_upload_stage_with_no_files_ran_and_shows_zero() -> None:
    page = _render(_stage("Uploaded", {}))

    assert "| Uploaded | 0 | 0 | 0 | none |" in page
    assert "**Overall: PASS**" in page


def test_unreadable_results_files_count_as_not_run_instead_of_crashing(tmp_path: Path) -> None:
    bad_validate = tmp_path / "validate_results.csv"
    bad_validate.write_text("unexpected,header\nx,y\n", encoding="utf-8")
    bad_filecopy = tmp_path / "filecopy_results.csv"
    bad_filecopy.write_text(",".join(filecopy._RESULT_COLUMNS) + "\nshort,ok\n", encoding="utf-8")

    assert summary.validate_stage(bad_validate).counts is None
    assert summary.download_stage(bad_filecopy).ran


def test_definitions_section_renders_each_stages_mapping() -> None:
    page = _render(_stage("Uploaded", {"ok": 1}, ("fail",)))

    assert "## Definitions" in page
    assert "- `ok`: meaning of ok" in page
    assert "- `fail`: meaning of fail" in page


def test_every_status_each_module_can_emit_has_a_definition() -> None:
    assert {"ok", "skip", "fail"} <= set(filecopy.STATUS_DEFINITIONS)
    assert {"ok", "missing", "mismatch", "extra", "hash mismatch", "segment mismatch"} <= set(
        validate.STATUS_DEFINITIONS
    )
    assert {"ok", "skip", "fail", "rejected", "conflict"} <= set(blobupload.STATUS_DEFINITIONS)
    for failed, defs in (
        (filecopy.FAILED_STATUSES, filecopy.STATUS_DEFINITIONS),
        (validate.FAILED_STATUSES, validate.STATUS_DEFINITIONS),
        (blobupload._FAILED, blobupload.STATUS_DEFINITIONS),
    ):
        assert set(failed) <= set(defs)


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        pytest.param({"status": "missing"}, "missing", id="not_ok_keeps_its_status"),
        pytest.param(
            {"status": "ok", "hash_check": "mismatch", "segment_check": "mismatch"}, "hash mismatch", id="hash_first"
        ),
        pytest.param(
            {"status": "ok", "hash_check": "ok", "segment_check": "mismatch"}, "segment mismatch", id="segment"
        ),
        pytest.param({"status": "ok", "hash_check": "", "segment_check": ""}, "ok", id="clean"),
    ],
)
def test_validate_rows_count_once_under_their_outcome(row: dict[str, str], expected: str) -> None:
    assert validate.classify_row(row) == expected


def test_validate_stage_counts_a_results_csv_and_missing_file_is_not_run(tmp_path: Path) -> None:
    path = tmp_path / "validate_results.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        fh.write("# provenance\r\n")
        writer = csv.writer(fh)
        writer.writerow(["path", "status", "sha256", "hash_check", "segment_check"])
        writer.writerow(["a", "ok", "x", "ok", ""])
        writer.writerow(["b", "ok", "x", "mismatch", ""])
        writer.writerow(["c", "missing", "", "", ""])

    assert summary.validate_stage(path).counts == {"ok": 1, "hash mismatch": 1, "missing": 1}
    assert summary.validate_stage(tmp_path / "absent.csv").counts is None


def test_download_stage_counts_a_filecopy_results_csv(tmp_path: Path) -> None:
    path = tmp_path / "filecopy_results.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        fh.write("# provenance\r\n")
        writer = csv.writer(fh)
        writer.writerow(filecopy._RESULT_COLUMNS)
        for name, status in (("a", "ok"), ("b", "skip"), ("c", "fail")):
            writer.writerow([name, status, 1, 1, 0.1, "", 0, 0, "", ""])

    stage = summary.download_stage(path)

    assert stage.counts == {"ok": 1, "skip": 1, "fail": 1}
    assert stage.failures == 1
    assert summary.download_stage(tmp_path / "absent.csv").counts is None


def test_data_digest_is_the_hash_of_the_data_lines_of_sha256sums() -> None:
    results = [
        blobupload.UploadResult("b.pdf", "ok", 1, "b" * 64, "n", 1, 0.0),
        blobupload.UploadResult("a.pdf", "skip", 1, "a" * 64, "n", 1, 0.0),
        blobupload.UploadResult("c.pdf", "fail", 1, "c" * 64, "n", 1, 0.0),
    ]
    expected = hashlib.sha256(f"{'a' * 64}  data/a.pdf\n{'b' * 64}  data/b.pdf\n".encode()).hexdigest()

    assert blobupload.data_digest(results) == expected
