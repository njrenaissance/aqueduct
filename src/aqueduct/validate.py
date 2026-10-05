"""validate - reconcile a completed download against the dated manifest.

Third step of the web-only-share workflow:
    1. webenum    - enumerate what the share contained -> manifest.json/.csv
    2. filecopy   - download every file (+ inline SHA-256) -> ./download/
    3. validate   - prove the download matches the manifest  (this script)

What "validate" checks:
    The web-session listing exposes no source content hash (see ADR-0001), so the
    check against the manifest is completeness + size:
        OK        present, size matches the manifest
        MISSING   in the manifest, not on disk
        MISMATCH  present but wrong size (or a leftover .part)
        EXTRA     on disk, never listed in the manifest

    With --hash, validate also computes each file's SHA-256 (our integrity hash;
    see ADR-0006) and records it. If a reference of recorded hashes is available
    (filecopy_results.csv from the download), validate *verifies* against it and
    reports HASH-MISMATCH on any file whose bytes changed since download. Without a
    reference it just records the SHA-256 as a dated fingerprint.

    (Interruptions/truncation are caught by the size check — not by a hash, since
    there is no remote hash to compare a download against; SHA-256 is our own
    forward-looking fingerprint.)

Usage:
    uv run validate                       # size + completeness (fast)
    uv run validate --hash                # also SHA-256; verify vs filecopy_results.csv if present
    uv run validate --hash --verify-against filecopy_results.csv
    uv run validate -d download -m manifest.json
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import sys
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from aqueduct import metadata

log = logging.getLogger("validate")

_HASH_CHUNK = 4 * 1024 * 1024
_PROGRESS_EVERY = 10  # print a progress line every N files, and...
_PROGRESS_SECONDS = 30  # ...at least this often (also mid-file, so one huge file does not look hung)
_LARGE_BYTES = 256 * 1024 * 1024  # announce files at least this big when hashing starts
_COLUMNS = [
    "path",
    "status",
    "expected_bytes",
    "actual_bytes",
    "sha256",
    "hash_check",
    "segment_check",
    "corrupted_segments",
]

# What each outcome in validate_results.csv means; rendered into the run's summary page. A file is counted once,
# under the first of: its ``status`` (when not ok), a hash mismatch, a segment mismatch, else ok.
STATUS_DEFINITIONS = {
    "ok": "Present at the manifest size; any recorded SHA-256 and segment hashes agree.",
    "missing": "Listed in the manifest but not on disk (an unfinished .part file does not count).",
    "mismatch": "On disk, but its size differs from the manifest.",
    "extra": "On disk but not listed in the manifest.",
    "hash mismatch": "Right size, but its SHA-256 differs from the one filecopy recorded.",
    "segment mismatch": "Right size, but one or more segment hashes differ from the sidecar's; the file is corrupted.",
}
FAILED_STATUSES = ("missing", "mismatch", "extra", "hash mismatch", "segment mismatch")


def classify_row(row: dict[str, str]) -> str:
    """The single STATUS_DEFINITIONS outcome a results row counts under."""
    if row["status"] != "ok":
        return row["status"]
    if row.get("hash_check") == "mismatch":
        return "hash mismatch"
    if row.get("segment_check") == "mismatch":
        return "segment mismatch"
    return "ok"


class _ProgressReporter:
    """Prints progress lines by file count and on a timer; with ``with_bytes`` also by bytes hashed."""

    def __init__(
        self,
        total_files: int,
        total_bytes: int,
        with_bytes: bool,
        clock: Callable[[], float] = time.monotonic,
        interval: float = _PROGRESS_SECONDS,
        every: int = _PROGRESS_EVERY,
    ) -> None:
        self._total_files = total_files
        self._total_bytes = total_bytes
        self._with_bytes = with_bytes
        self._clock = clock
        self._interval = interval
        self._every = every
        self._last = clock()
        self._files_done = 0
        self._bytes_done = 0
        self._file_credit = 0  # bytes of the current file already counted by add_bytes

    def line(self) -> str:
        if not self._with_bytes:
            return f"  ...{self._files_done}/{self._total_files} checked"
        done, total = self._bytes_done / 1e9, self._total_bytes / 1e9
        pct = int(100 * self._bytes_done / self._total_bytes) if self._total_bytes else 100
        return f"  ...{self._files_done:,}/{self._total_files:,} files, {done:.1f} GB of {total:.1f} GB ({pct}%)"

    def file_started(self, rel: str, size: int) -> None:
        if self._with_bytes and size >= _LARGE_BYTES:
            print(f"  hashing {rel} ({size:,} B)...", flush=True)

    def add_bytes(self, count: int) -> None:
        self._bytes_done += count
        self._file_credit += count
        self._emit_if_due(force=False)

    def file_done(self, size: int) -> None:
        self._files_done += 1
        self._bytes_done += max(size - self._file_credit, 0)  # unhashed files still reach 100%
        self._file_credit = 0
        self._emit_if_due(force=self._files_done % self._every == 0)

    def _emit_if_due(self, force: bool) -> None:
        now = self._clock()
        if force or now - self._last >= self._interval:
            print(self.line(), flush=True)
            self._last = now


def _sha256_file(path: Path, on_chunk: Callable[[int], None] | None = None) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(_HASH_CHUNK), b""):
            h.update(block)
            if on_chunk:
                on_chunk(len(block))
    return h.hexdigest()


def _load_reference(path: Path) -> dict[str, str]:
    """Recorded SHA-256s from a filecopy results CSV, keyed by manifest path.

    An absent file means "no reference" (empty dict). A file that exists but can't be read, or lacks
    the expected columns, raises ValueError: silently returning an empty reference would downgrade
    "verified" to "just recorded" without the operator knowing.
    """
    ref: dict[str, str] = {}
    if not path or not path.exists():
        return ref
    try:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            # filecopy writes "# ..." provenance lines above the header; skip them.
            rows = csv.DictReader(line for line in fh if not line.lstrip().startswith("#"))
            if not rows.fieldnames or not {"path", "sha256"} <= set(rows.fieldnames):
                raise ValueError(f"{path}: not a filecopy results CSV (needs 'path' and 'sha256' columns)")
            for row in rows:
                sha = (row.get("sha256") or "").strip()
                if sha:
                    ref[row["path"]] = sha
    except (OSError, csv.Error) as exc:
        raise ValueError(f"cannot read hash reference {path}: {exc}") from exc
    return ref


def _row(
    path, status, expected="", actual="", sha256="", hash_check="", segment_check="", corrupted_segments=""
) -> dict:
    return {
        "path": path,
        "status": status,
        "expected_bytes": expected,
        "actual_bytes": actual,
        "sha256": sha256,
        "hash_check": hash_check,
        "segment_check": segment_check,
        "corrupted_segments": corrupted_segments,
    }


def _verify_hash(rel: str, target: Path, reference: dict[str, str], progress: _ProgressReporter) -> tuple[str, str]:
    """Compute SHA-256 and compare to any recorded value. Returns (digest, check)."""
    progress.file_started(rel, target.stat().st_size)
    digest = _sha256_file(target, progress.add_bytes)
    want = reference.get(rel)
    if not want:
        return digest, ""
    if want == digest:
        return digest, "ok"
    log.warning(f"HASH-MISMATCH  {rel}\n          recorded {want}\n          on disk  {digest}")
    return digest, "mismatch"


def _load_segments(rel: str, dest: Path) -> dict[str, object] | None:
    """Load segment metadata from sidecar JSON, if present."""
    sidecar_path = (dest / rel).with_suffix((dest / rel).suffix + ".segments.json")
    if not sidecar_path.exists():
        return None
    try:
        with open(sidecar_path, encoding="utf-8") as fh:
            data = json.load(fh)
            if isinstance(data, dict):
                # Verify file_size_bytes matches actual file
                target = dest / rel
                if target.exists():
                    recorded_size = data.get("file_size_bytes")
                    actual_size = target.stat().st_size
                    if recorded_size != actual_size:
                        log.warning(f"Sidecar mismatch for {rel}: recorded {recorded_size} B, actual {actual_size} B")
                return data
            return None
    except (OSError, json.JSONDecodeError):
        return None


def _verify_segments(rel: str, target: Path, segment_data: dict) -> tuple[str, str]:
    """Verify segment hashes against sidecar. Returns (check_status, corrupted_segment_indices)."""
    segments = segment_data["segments"]
    corrupted = []

    with open(target, "rb") as fh:
        for seg in segments:
            offset = seg["offset_bytes"]
            size = seg["size_bytes"]
            expected_sha = seg["sha256"]

            # Read segment bytes and compute SHA-256
            fh.seek(offset)
            data = fh.read(size)
            actual_sha = hashlib.sha256(data).hexdigest()

            if actual_sha != expected_sha:
                corrupted.append(seg["segment_index"])

    if corrupted:
        start_offset = segments[corrupted[0]]["offset_bytes"]
        end_offset = segments[corrupted[-1]]["offset_bytes"] + segments[corrupted[-1]]["size_bytes"]
        log.warning(
            f"SEGMENT-MISMATCH  {rel}  corrupted segments: {corrupted}  (offset range: {start_offset} - {end_offset}B)"
        )
        return "mismatch", str(corrupted)
    return "ok", ""


def _check_file(item: dict, dest: Path, do_hash: bool, reference: dict[str, str], progress: _ProgressReporter) -> dict:
    """One manifest file → a result row (prints any problem it finds)."""
    rel = item["path"]
    target = dest / rel
    expected = item["size"]
    if not target.exists():
        partial = target.with_suffix(target.suffix + ".part").exists()
        print(f"MISSING   {rel}{' (only .part present)' if partial else ''}")
        return _row(rel, "missing", expected)
    actual = target.stat().st_size
    if actual != expected:
        print(f"MISMATCH  {rel}  expected {expected:,} B, on disk {actual:,} B")
        return _row(rel, "mismatch", expected, actual)
    digest, hash_check = _verify_hash(rel, target, reference, progress) if do_hash else ("", "")
    # Check segments if sidecar is present
    segment_check = ""
    corrupted_segments = ""
    segment_data = _load_segments(rel, dest)
    if segment_data:
        segment_check, corrupted_segments = _verify_segments(rel, target, segment_data)
    return _row(rel, "ok", expected, actual, digest, hash_check, segment_check, corrupted_segments)


def _scan_extras(dest: Path, manifest_paths: set[str]) -> list[dict]:
    """Files on disk the manifest never listed (in-flight .part files and segment sidecars ignored)."""
    extras: list[dict] = []
    if not dest.exists():
        return extras
    for p in dest.rglob("*"):
        if p.is_file() and p.suffix not in (".part", ".json"):
            # Skip .part (in-flight downloads) and .segments.json (sidecar metadata)
            rp = str(p.relative_to(dest)).replace("\\", "/")
            if rp not in manifest_paths and not rp.endswith(".segments.json"):
                print(f"EXTRA     {rp} (on disk, not in manifest)")
                extras.append(_row(rp, "extra", actual=p.stat().st_size))
    return extras


def _write_results_csv(
    results_path: Path,
    rows: list[dict],
    run_metadata: dict | None = None,
) -> None:
    """Write validation results CSV with optional provenance headers."""
    with open(results_path, "w", newline="", encoding="utf-8-sig") as fh:
        if run_metadata:
            provenance = metadata.format_metadata_header(
                tool=run_metadata.get("tool", "validate"),
                version=run_metadata.get("tool_version", ""),
                operator=run_metadata.get("operator", "unspecified"),
                host_info=run_metadata.get("host_info", {}),
                started_at_utc=run_metadata.get("started_at_utc", ""),
                completed_at_utc=run_metadata.get("completed_at_utc", ""),
            )
            for line in provenance:
                fh.write(line + "\r\n")
        w = csv.writer(fh)
        w.writerow(_COLUMNS)
        for r in rows:
            w.writerow([r[c] for c in _COLUMNS])


def validate(
    manifest: dict,
    dest: Path,
    do_hash: bool,
    results_path: Path,
    reference: dict[str, str],
    operator_identity: str | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    with metadata.timed_run("validate", operator_identity) as run_metadata:
        files = [i for i in manifest["items"] if i["type"] == "file"]
        manifest_paths = {i["path"].replace("\\", "/") for i in files}

        mode = "size only"
        if do_hash:
            mode = f"size + SHA-256 (verifying vs {len(reference)} recorded)" if reference else "size + SHA-256"
        print(f"Validating {len(files)} files against {dest}/ ({mode})...\n", flush=True)

        progress = _ProgressReporter(len(files), sum(i["size"] for i in files), do_hash, clock)
        rows: list[dict] = []
        for item in files:
            rows.append(_check_file(item, dest, do_hash, reference, progress))
            progress.file_done(item["size"])
        rows += _scan_extras(dest, manifest_paths)

        c = Counter(r["status"] for r in rows)
        hash_mismatch = sum(1 for r in rows if r["hash_check"] == "mismatch")
        segment_mismatch = sum(1 for r in rows if r["segment_check"] == "mismatch")
        print("\n" + "-" * 60)
        summary = f"OK={c['ok']}  MISSING={c['missing']}  MISMATCH={c['mismatch']}  EXTRA={c['extra']}"
        if do_hash and reference:
            summary += f"  HASH-MISMATCH={hash_mismatch}"
        if segment_mismatch:
            summary += f"  SEGMENT-MISMATCH={segment_mismatch}"
        print(summary)
        print(f"Per-file results: {results_path}")

        failed = c["missing"] + c["mismatch"] + c["extra"] + hash_mismatch + segment_mismatch
        if failed:
            print(f"RESULT: FAIL  ({failed} problem(s)) - re-run filecopy to fill/repair.")
        else:
            tail = " SHA-256 verified." if (do_hash and reference) else (" Hashes recorded." if do_hash else "")
            print("RESULT: PASS  every manifest file is present at the expected size." + tail)

    # Write results and metadata after timing is complete
    _write_results_csv(results_path, rows, run_metadata)
    metadata.write_metadata_sidecar(results_path, run_metadata)

    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate a download against the manifest.")
    ap.add_argument("-m", "--manifest", default="manifest.json")
    ap.add_argument("-d", "--dest", default="download")
    ap.add_argument("--hash", action="store_true", help="also compute each file's SHA-256 (reads all bytes; slow)")
    ap.add_argument(
        "--verify-against",
        default="filecopy_results.csv",
        help="CSV of recorded SHA-256s to verify against (default filecopy_results.csv; skipped if absent)",
    )
    ap.add_argument(
        "--results", default="validate_results.csv", help="per-file results CSV (default validate_results.csv)"
    )
    ap.add_argument(
        "--operator",
        default=None,
        help="operator identity for validation record (email/username; opt-in to avoid PII; default: unspecified)",
    )
    args = ap.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    try:
        reference = _load_reference(Path(args.verify_against)) if args.hash else {}
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return validate(manifest, Path(args.dest), args.hash, Path(args.results), reference, args.operator)


if __name__ == "__main__":
    sys.exit(main())
