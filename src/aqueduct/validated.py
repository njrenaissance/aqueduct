"""validated - the gate shared by the upload commands: only files that passed ``validate --hash`` may be sent.

``spupload`` (SharePoint, ADR-0012) and ``upload`` (Azure Blob vault, ADR-0013) both read the manifest and
``validate_results.csv`` through :func:`load_entries`, so the rule for "validated" lives in one place.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class UploadEntry:
    """One validated manifest file: where it lives locally and where it goes at the destination."""

    path: str  # manifest-relative, "/"-separated
    filename: str
    destination: str  # parent folder under the target ("" = the target itself)
    size: int
    sha256: str  # the value ``validate`` recorded
    item_id: str = ""  # the source item's UniqueId, when the manifest has one

    @classmethod
    def from_manifest_path(cls, path: str, size: int, sha256: str = "", item_id: str = "") -> UploadEntry:
        normalized = norm(path)
        destination, _, filename = normalized.rpartition("/")
        return cls(normalized, filename, destination, size, sha256, item_id)

    def local_path(self, root: Path) -> Path:
        return root.joinpath(*self.path.split("/"))


def norm(path: str) -> str:
    return path.replace("\\", "/").strip("/")


def _read_validate_rows(results_path: Path) -> dict[str, dict[str, str]]:
    if not results_path.exists():
        raise FileNotFoundError(f"{results_path} not found - run `validate --hash` before uploading")
    text = results_path.read_text(encoding="utf-8-sig")
    lines = [line for line in text.splitlines() if not line.startswith("#")]  # skip provenance comments
    return {norm(row["path"]): row for row in csv.DictReader(lines)}


def _rejection_reason(row: dict[str, str] | None, reference_sha: str = "") -> str | None:
    if row is None or row.get("status") != "ok":
        return "not validated"
    if not row.get("sha256"):
        return "no SHA-256"
    if row.get("hash_check") == "mismatch":
        return "hash mismatch"
    if row.get("segment_check") == "mismatch":
        return "segment mismatch"
    if reference_sha and reference_sha != row["sha256"]:
        return "differs from filecopy record"
    return None


def load_entries(
    manifest: dict, results_path: Path, reference: dict[str, str] | None = None
) -> tuple[list[UploadEntry], list[tuple[UploadEntry, str]]]:
    """Split the manifest's files into those that passed ``validate --hash`` and those that did not.

    ``reference`` (the SHA-256s ``filecopy`` recorded, keyed by manifest path) is a second opinion: a validated
    hash that disagrees with it is rejected even though ``validate`` said ``ok``.
    """
    rows = _read_validate_rows(results_path)
    recorded = {norm(path): sha for path, sha in (reference or {}).items()}
    eligible: list[UploadEntry] = []
    rejected: list[tuple[UploadEntry, str]] = []
    for item in manifest["items"]:
        if item["type"] != "file":
            continue
        key = norm(item["path"])
        row = rows.get(key)
        entry = UploadEntry.from_manifest_path(
            item["path"], int(item["size"]), (row or {}).get("sha256", ""), str(item.get("id") or "")
        )
        reason = _rejection_reason(row, recorded.get(key, ""))
        if reason is None:
            eligible.append(entry)
        else:
            rejected.append((entry, reason))
    return eligible, rejected
