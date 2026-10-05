"""upload - send the validated local download to the immutable Azure Blob vault (Stage 2, Preserve; ADR-0013).

Workflow, after validate:
    1. webenum    - enumerate what the share contained -> manifest.json/.csv
    2. filecopy   - download every file (+ inline SHA-256) -> ./download/
    3. validate   - prove the download matches the manifest (--hash)
    4. upload     - preserve the validated files in the Blob vault   (this script)

What it does:
    * Uploads only files that passed ``validate --hash`` (read from validate_results.csv) and whose SHA-256 agrees
      with the one ``filecopy`` recorded. The vault is looked up by name first; a new file is hashed in the same
      pass that stages its blocks and must equal the validated value before the commit, so a file changed since
      ``validate`` is rejected and nothing is committed.
    * Each file goes up as staged blocks (several at once) with Azure content validation (per-block MD5/CRC64),
      then one commit that stores our SHA-256 (the evidence fingerprint, SPEC section 4) and the source UniqueId
      as blob metadata.
    * A file is "completed" only when the stored blob is read back and its size, SHA-256 metadata and
      Content-MD5 match - never merely because bytes arrived.
    * Idempotent: a blob already there with the same size and SHA-256 is skipped. A blob already there with a
      *different* hash is a ``conflict`` and is never overwritten (the container is immutable).
    * Then it preserves the acquisition record beside the evidence, under ``<prefix>/_audit/<run-id>/``: the
      manifest, filecopy/validate/upload results, a portable ``SHA256SUMS`` and a ``custody.json`` binding it.
    * Auth is the operator's own Azure identity (``DefaultAzureCredential``); no keys, SAS tokens or secrets.

Layout (one ``--dest-prefix`` per collection; never reuse one):
    <container>/<matter-id>/<collection-id>/data/<manifest path>
    <container>/<matter-id>/<collection-id>/_audit/<run-id>/...

Usage:
    uv run upload --account-url https://contoso.blob.core.windows.net --container vault \\
        --dest-prefix 2026-0042-smith/20261004-share-a
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import logging
import os
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from azure.core.exceptions import AzureError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobBlock, BlobServiceClient, ContainerClient, ContentSettings

from aqueduct import metadata
from aqueduct.errors import AqueductError, ConfigError, IntegrityError
from aqueduct.validate import _load_reference as load_reference
from aqueduct.validated import UploadEntry, load_entries

log = logging.getLogger("upload")

_HASH_CHUNK = 4 * 1024 * 1024
_MAX_BACKOFF = 60
_RETRYABLE = (AzureError, OSError, IntegrityError)
_COLUMNS = ["path", "status", "size", "sha256", "blob_name", "attempts", "seconds", "detail"]
_FLUSH_EVERY = 100  # checkpoint the results CSV every N finished files, and...
_FLUSH_SECONDS = 30  # ...at least this often
_PROGRESS_SECONDS = 30  # log a progress line this often while transfers run
_LARGE_BYTES = 256 * 1024 * 1024  # announce files at least this big when they start
_STORED = ("ok", "skip")
_FAILED = ("fail", "rejected", "conflict")
_SUMS_NAME = "SHA256SUMS"
_CUSTODY_NAME = "custody.json"
_ENV_ACCOUNT = "AQUEDUCT_BLOB_ACCOUNT_URL"
_ENV_CONTAINER = "AQUEDUCT_BLOB_CONTAINER"
_ENV_PREFIX = "AQUEDUCT_BLOB_PREFIX"


@dataclass(frozen=True)
class BlobConfig:
    account_url: str
    container: str
    prefix: str


class Progress:
    """Thread-safe files/bytes counters for the progress line."""

    def __init__(self, total_files: int = 0, total_bytes: int = 0) -> None:
        self.total_files = total_files
        self.total_bytes = total_bytes
        self.files_done = 0
        self.bytes_done = 0
        self._lock = threading.Lock()

    def add_bytes(self, count: int) -> None:
        with self._lock:
            self.bytes_done += count

    def file_done(self, extra_bytes: int = 0) -> None:
        with self._lock:
            self.files_done += 1
            self.bytes_done += extra_bytes

    def line(self) -> str:
        with self._lock:
            done, total = self.bytes_done / 1e9, self.total_bytes / 1e9
            return f"progress: {self.files_done}/{self.total_files} files, {done:.2f}/{total:.2f} GB"


@dataclass
class Ctx:
    """Run-wide state shared by every transfer."""

    container: Any  # a ContainerClient (a fake in tests)
    prefix: str
    chunk: int
    max_retries: int
    block_workers: int = 4  # blocks of one file staged concurrently
    hash_sem: threading.Semaphore = field(default_factory=lambda: threading.BoundedSemaphore(3))
    progress: Progress = field(default_factory=Progress)


class _Rejected(Exception):  # noqa: N818 - an outcome, not an error: never retried
    """The bytes read differ from the validated hash; nothing was committed."""

    def __init__(self, sha256: str) -> None:
        super().__init__("changed since validate (SHA-256 mismatch)")
        self.sha256 = sha256


@dataclass(frozen=True)
class Item:
    """One thing to put in the vault: a validated data file or an audit-record file."""

    name: str  # full blob name
    source: Path
    path: str  # label for the results CSV (manifest path, or ``_audit/<file>``)
    expected_sha256: str  # the validated hash the file must still match ("" for audit files)
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass
class UploadResult:
    path: str
    status: str  # ok | skip | fail | rejected | conflict
    size: int
    sha256: str
    blob_name: str
    attempts: int
    seconds: float
    detail: str = ""


# --- configuration and naming ----------------------------------------------------


def normalize_prefix(prefix: str) -> str:
    """The folder prefix inside the container, validated so evidence can't land outside its collection folder."""
    cleaned = prefix.replace("\\", "/").rstrip("/")
    if not cleaned:
        raise ConfigError("a destination folder prefix is required (--dest-prefix <matter-id>/<collection-id>)")
    if cleaned.startswith("/") or any(part in ("", ".", "..") for part in cleaned.split("/")):
        raise ConfigError(f"unsafe destination prefix '{prefix}': no leading '/', empty, '.' or '..' segments")
    return cleaned


def _check_account_url(account_url: str) -> str:
    parts = urlsplit(account_url)
    if parts.scheme != "https" or not parts.netloc:
        raise ConfigError("the storage account URL must be https://<account>.blob.core.windows.net")
    if parts.query or parts.fragment:
        raise ConfigError("the storage account URL must not carry a SAS token or query string; sign in with Azure")
    return account_url.rstrip("/")


def resolve_config(account_url: str, container: str, prefix: str, env: Mapping[str, str]) -> BlobConfig:
    """Destination from flags, falling back to AQUEDUCT_BLOB_* environment variables."""
    account = account_url or env.get(_ENV_ACCOUNT, "")
    name = container or env.get(_ENV_CONTAINER, "")
    if not account or not name:
        raise ConfigError(
            f"a destination is required: --account-url and --container (or ${_ENV_ACCOUNT} and ${_ENV_CONTAINER})"
        )
    return BlobConfig(_check_account_url(account), name, normalize_prefix(prefix or env.get(_ENV_PREFIX, "")))


def data_blob_name(prefix: str, rel: str) -> str:
    return f"{prefix}/data/{rel}"


def block_id(index: int) -> str:
    """Deterministic, fixed-length block id (Azure requires every id in a blob to be the same length)."""
    return base64.b64encode(f"{index:08d}".encode()).decode()


def data_item(prefix: str, entry: UploadEntry, root: Path) -> Item:
    meta = {"sourcepath": quote(entry.path, safe="/")}  # header values must be ASCII
    if entry.item_id:
        meta["uniqueid"] = entry.item_id
    return Item(data_blob_name(prefix, entry.path), entry.local_path(root), entry.path, entry.sha256, meta)


def audit_item(prefix: str, run_id: str, source: Path) -> Item:
    return Item(f"{prefix}/_audit/{run_id}/{source.name}", source, f"_audit/{source.name}", "")


# --- hashing, blocks, verification ------------------------------------------------


def hash_file_pair(path: Path) -> tuple[str, bytes]:
    """(SHA-256 hex, MD5 digest) of a file, both from a single read of it."""
    sha = hashlib.sha256()
    md5 = hashlib.md5(usedforsecurity=False)  # Azure's transport check, not the evidence hash
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(_HASH_CHUNK), b""):
            sha.update(block)
            md5.update(block)
    return sha.hexdigest(), md5.digest()


def _read_blocks(path: Path, chunk: int) -> Iterator[bytes]:
    with open(path, "rb") as fh:
        yield from iter(lambda: fh.read(chunk), b"")


def _verify(props: Any, size: int, sha256: str, md5: bytes, name: str) -> None:
    """Check the stored blob against what we hashed locally; raise IntegrityError on any disagreement."""
    if props.size != size:
        raise IntegrityError(f"size mismatch after upload of '{name}': stored {props.size:,}, expected {size:,}")
    stored_sha = (props.metadata or {}).get("sha256", "")
    if stored_sha != sha256:
        raise IntegrityError(f"SHA-256 mismatch after upload of '{name}': stored '{stored_sha}', local {sha256}")
    stored_md5 = props.content_settings.content_md5
    if stored_md5 is not None and bytes(stored_md5) != md5:
        raise IntegrityError(f"Content-MD5 mismatch after upload of '{name}'")


def _stage_blocks(ctx: Ctx, blob: Any, item: Item) -> tuple[list[BlobBlock], str, bytes, int]:
    """Read the file once, hashing as we go, staging blocks concurrently (Azure validates each).

    Returns (blocks in order, SHA-256 hex, MD5 digest, bytes sent). On any error the bytes already counted in the
    progress line are taken back, so a retry is not double-counted.
    """
    sha, md5 = hashlib.sha256(), hashlib.md5(usedforsecurity=False)
    window = threading.BoundedSemaphore(max(ctx.block_workers, 1))  # caps blocks held in memory
    futures: list[Future[None]] = []
    blocks: list[BlobBlock] = []
    failed = threading.Event()
    sent = 0

    def stage(ident: str, data: bytes) -> None:
        try:
            blob.stage_block(ident, data, validate_content=True)
        except BaseException:
            failed.set()
            raise
        finally:
            window.release()

    def read_hashed(chunks: Iterator[bytes]) -> bytes:
        with ctx.hash_sem:  # reading + hashing is the disk-bound part; --hash-workers caps it
            data = next(chunks, b"")
            sha.update(data)
            md5.update(data)
        return data

    try:
        with ThreadPoolExecutor(max_workers=max(ctx.block_workers, 1)) as pool:
            chunks = _read_blocks(item.source, ctx.chunk)
            while not failed.is_set() and (data := read_hashed(chunks)):
                ident = block_id(len(blocks))
                window.acquire()
                futures.append(pool.submit(stage, ident, data))
                blocks.append(BlobBlock(block_id=ident))
                sent += len(data)
                ctx.progress.add_bytes(len(data))
        for fut in futures:
            fut.result()
    except BaseException:
        ctx.progress.add_bytes(-sent)
        raise
    return blocks, sha.hexdigest(), md5.digest(), sent


def _stage_and_commit(ctx: Ctx, item: Item, size: int) -> str:
    """Stage every block while hashing, prove the bytes are the validated ones, then commit and read back.

    Nothing is committed unless the single hash pass matches the validated SHA-256 (uncommitted blocks are
    discarded by Azure). Returns the SHA-256 of what was stored.
    """
    blob = ctx.container.get_blob_client(item.name)
    blocks, sha256, md5, sent = _stage_blocks(ctx, blob, item)
    try:
        if item.expected_sha256 and sha256 != item.expected_sha256:
            raise _Rejected(sha256)
        if sent != size:
            raise IntegrityError(f"'{item.path}' changed while uploading (bytes sent differ from its size)")
        blob.commit_block_list(
            blocks,
            content_settings=ContentSettings(content_md5=bytearray(md5)),
            metadata={"sha256": sha256, **item.metadata},
        )
        _verify(blob.get_blob_properties(), size, sha256, md5, item.name)
    except BaseException:
        ctx.progress.add_bytes(-sent)
        raise
    return sha256


def _backoff_seconds(attempt: int) -> int:
    exponential: int = 2 ** (attempt - 1)
    return min(exponential, _MAX_BACKOFF)


# --- transfer ---------------------------------------------------------------------


def _result(
    item: Item, status: str, size: int, sha256: str, attempts: int, secs: float, detail: str = ""
) -> UploadResult:
    return UploadResult(item.path, status, size, sha256, item.name, attempts, secs, detail)


def _existing_properties(ctx: Ctx, name: str) -> Any | None:
    try:
        return ctx.container.get_blob_client(name).get_blob_properties()
    except ResourceNotFoundError:
        return None


def _recheck_committed(ctx: Ctx, item: Item, size: int, existing: Any) -> str:
    """Re-verify a blob an earlier attempt already committed, instead of committing over it."""
    with ctx.hash_sem:
        sha256, md5 = hash_file_pair(item.source)
    if item.expected_sha256 and sha256 != item.expected_sha256:
        raise _Rejected(sha256)
    _verify(existing, size, sha256, md5, item.name)
    return sha256


def _commit_or_recheck(ctx: Ctx, item: Item, size: int, attempt: int) -> str:
    """Stage and commit; on a retry, first check whether the previous attempt's commit actually landed.

    The commit can succeed while the read-back fails, and the vault is immutable, so committing again would
    turn a preserved file into a failure.
    """
    existing = _existing_properties(ctx, item.name) if attempt > 1 else None
    if existing is not None:
        return _recheck_committed(ctx, item, size, existing)
    return _stage_and_commit(ctx, item, size)


def _try_upload(ctx: Ctx, item: Item, size: int) -> UploadResult:
    started = time.monotonic()
    attempts = ctx.max_retries + 1
    sha256 = item.expected_sha256
    for attempt in range(1, attempts + 1):
        try:
            sha256 = _commit_or_recheck(ctx, item, size, attempt)
        except _Rejected as exc:
            log.error("REJECT  %s - changed since validate (SHA-256 differs from the validated value)", item.path)
            return _result(item, "rejected", size, exc.sha256, 0, 0.0, str(exc))
        except _RETRYABLE as exc:
            if attempt >= attempts:
                log.error("FAIL  %s - %s (after %d tries)", item.path, exc, attempt)
                return _result(item, "fail", size, sha256, attempt, time.monotonic() - started, str(exc))
            backoff = _backoff_seconds(attempt)
            log.warning("retry %s - %s (try %d/%d; %ds)", item.path, exc, attempt, attempts, backoff)
            time.sleep(backoff)
        else:
            secs = time.monotonic() - started
            log.info("ok    %s (%s B, try %d, %.1fs)", item.path, f"{size:,}", attempt, secs)
            return _result(item, "ok", size, sha256, attempt, secs)
    return _result(item, "fail", size, sha256, attempts, 0.0, "logic error")  # unreachable


def _compare_existing(ctx: Ctx, item: Item, size: int, existing: Any) -> UploadResult:
    """A blob is already at this name: skip if it is the same file, otherwise refuse to overwrite it.

    Only a same-size blob is worth reading the local file for; a different size is a conflict on its own.
    """
    if existing.size != size:
        log.error("CONFLICT  %s - a different blob already exists here; the vault is not overwritten", item.path)
        return _result(item, "conflict", size, item.expected_sha256, 0, 0.0, "a blob with a different size exists")
    try:
        with ctx.hash_sem:
            sha256, _ = hash_file_pair(item.source)
    except OSError as exc:
        log.error("FAIL  %s - could not read the source file: %s", item.path, exc)
        return _result(item, "fail", size, item.expected_sha256, 0, 0.0, f"could not read the source file: {exc}")
    if item.expected_sha256 and sha256 != item.expected_sha256:
        log.error("REJECT  %s - changed since validate (SHA-256 differs from the validated value)", item.path)
        return _result(item, "rejected", size, sha256, 0, 0.0, "changed since validate (SHA-256 mismatch)")
    if (existing.metadata or {}).get("sha256") == sha256:
        log.info("skip  %s (already preserved, hash matches)", item.path)
        return _result(item, "skip", size, sha256, 0, 0.0)
    log.error("CONFLICT  %s - a different blob already exists at this name; the vault is not overwritten", item.path)
    return _result(item, "conflict", size, sha256, 0, 0.0, "a blob with a different hash already exists")


def _lookup_with_retries(ctx: Ctx, item: Item) -> Any | None:
    """The vault lookup, retried like the upload: a transient 429/503 must not fail the file outright."""
    for attempt in range(1, ctx.max_retries + 2):
        try:
            return _existing_properties(ctx, item.name)
        except AzureError as exc:
            if attempt > ctx.max_retries:
                raise
            backoff = _backoff_seconds(attempt)
            log.warning("retry %s - vault lookup: %s (try %d; %ds)", item.path, exc, attempt, backoff)
            time.sleep(backoff)
    raise AssertionError("unreachable")  # pragma: no cover


def _transfer(ctx: Ctx, item: Item) -> UploadResult:
    if not item.source.exists():
        log.error("FAIL  %s - source file not found: %s", item.path, item.source)
        return _result(item, "fail", 0, item.expected_sha256, 0, 0.0, f"source file not found: {item.source}")
    size = item.source.stat().st_size
    try:
        existing = _lookup_with_retries(ctx, item)
    except AzureError as exc:
        return _result(item, "fail", size, item.expected_sha256, 0, 0.0, f"could not check the vault: {exc}")
    if existing is not None:
        return _compare_existing(ctx, item, size, existing)
    if size >= _LARGE_BYTES:
        log.info("start %s (%s B)", item.path, f"{size:,}")
    return _try_upload(ctx, item, size)


def transfer(ctx: Ctx, item: Item) -> UploadResult:
    """Put one file in the vault: look it up by name first, skip or refuse if present, else hash while sending."""
    result = _transfer(ctx, item)
    # Bytes of an uploaded file were counted as they were staged; everything else is credited in full now.
    ctx.progress.file_done(0 if result.status == "ok" else result.size)
    return result


class _ProgressReporter:
    """Logs a progress line on a timer while transfers run, so a long file does not look hung."""

    def __init__(self, progress: Progress, interval: float = _PROGRESS_SECONDS) -> None:
        self._progress = progress
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            log.info(self._progress.line())

    def __enter__(self) -> _ProgressReporter:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join()


def _local_size(item: Item) -> int:
    try:
        return item.source.stat().st_size
    except OSError:
        return 0


def run_transfers(
    ctx: Ctx, items: Sequence[Item], workers: int, on_result: Callable[[UploadResult], None] | None = None
) -> list[UploadResult]:
    """Transfer every item concurrently; results come back in item order, ``on_result`` fires as each finishes."""
    ctx.progress = Progress(len(items), sum(_local_size(i) for i in items))
    pool = ThreadPoolExecutor(max_workers=max(workers, 1))
    try:
        with _ProgressReporter(ctx.progress):
            futures = {pool.submit(transfer, ctx, item): n for n, item in enumerate(items)}
            results: list[UploadResult | None] = [None] * len(items)
            for fut in as_completed(futures):
                results[futures[fut]] = result = fut.result()
                if on_result:
                    on_result(result)
        log.info(ctx.progress.line())
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return [r for r in results if r is not None]


# --- audit bundle ------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    return hash_file_pair(path)[0]


def build_sha256sums(results: Sequence[UploadResult], audit_files: Sequence[Path]) -> str:
    """``sha256sum``-format lines for every preserved data file and every audit file, sorted for reproducibility."""
    lines = [f"{r.sha256}  data/{r.path}" for r in results if r.status in _STORED]
    lines += [f"{_sha256_file(path)}  _audit/{path.name}" for path in audit_files]
    return "\n".join(sorted(lines, key=lambda line: line.split("  ", 1)[1])) + "\n"


def audit_candidates(manifest: Path, verify_against: Path, validate_results: Path) -> list[Path]:
    """The acquisition record that exists on disk, in the order it was produced."""
    sidecar = lambda p: p.with_suffix(p.suffix + ".metadata.json")  # noqa: E731
    wanted = [
        manifest,
        manifest.with_suffix(".csv"),
        verify_against,
        sidecar(verify_against),
        validate_results,
        sidecar(validate_results),
    ]
    return [p for p in wanted if p.exists()]


def default_audit_files(root: Path) -> list[Path]:
    return audit_candidates(root / "manifest.json", root / "filecopy_results.csv", root / "validate_results.csv")


def build_custody(
    run_metadata: Mapping[str, Any],
    account_url: str,
    container: str,
    prefix: str,
    results: Sequence[UploadResult],
    sums_sha256: str,
) -> dict[str, Any]:
    """The record that binds the bundle: who ran what where, the outcome counts, and the hash of SHA256SUMS."""
    return {
        "tool": run_metadata.get("tool"),
        "tool_version": run_metadata.get("tool_version"),
        "operator": run_metadata.get("operator"),
        "host_info": run_metadata.get("host_info"),
        "started_at_utc": run_metadata.get("started_at_utc"),
        "completed_at_utc": run_metadata.get("completed_at_utc"),
        "account_url": account_url,
        "container": container,
        "prefix": prefix,
        "counts": dict(Counter(r.status for r in results)),
        "sha256sums_sha256": sums_sha256,
    }


def _run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


# --- results ------------------------------------------------------------------------


def _write_results_csv(
    results_path: Path, results: Sequence[UploadResult], run_metadata: Mapping[str, Any], destination: str
) -> None:
    """Write the results CSV atomically (temp file + replace), so an interruption never leaves half a file."""
    temp = results_path.with_name(results_path.name + ".tmp")
    with open(temp, "w", newline="", encoding="utf-8-sig") as fh:
        header = metadata.format_metadata_header(
            tool=run_metadata["tool"],
            version=run_metadata["tool_version"],
            operator=run_metadata["operator"],
            host_info=run_metadata["host_info"],
            started_at_utc=run_metadata["started_at_utc"],
            completed_at_utc=run_metadata["completed_at_utc"],
        )
        for line in [*header, f"# vault: {destination}"]:
            fh.write(line + "\r\n")
        writer = csv.writer(fh)
        writer.writerow(_COLUMNS)
        for r in results:
            writer.writerow([r.path, r.status, r.size, r.sha256, r.blob_name, r.attempts, f"{r.seconds:.1f}", r.detail])
    os.replace(temp, results_path)


def _write_results(results_path: Path, results: Sequence[UploadResult], run_metadata: dict, destination: str) -> None:
    _write_results_csv(results_path, results, run_metadata, destination)
    metadata.write_metadata_sidecar(results_path, run_metadata)


class _Checkpoint:
    """Collects finished results and rewrites the results CSV every N files or every few seconds."""

    def __init__(self, results_path: Path, run_meta: Mapping[str, Any], destination: str) -> None:
        self._path = results_path
        self._meta = {**run_meta, "completed_at_utc": "in progress"}
        self._destination = destination
        self._results: list[UploadResult] = []
        self._last = time.monotonic()

    def __call__(self, result: UploadResult) -> None:
        self._results.append(result)
        if len(self._results) % _FLUSH_EVERY == 0 or time.monotonic() - self._last >= _FLUSH_SECONDS:
            self.flush()

    def flush(self) -> None:
        _write_results_csv(self._path, self._results, self._meta, self._destination)
        self._last = time.monotonic()


def _recorded(entry: UploadEntry, reason: str) -> UploadResult:
    """A row for a file the gate refused to upload."""
    return UploadResult(entry.path, "rejected", entry.size, entry.sha256, "", 0, 0.0, reason)


def _summarize(results: Sequence[UploadResult], audit: Sequence[UploadResult], results_path: Path) -> int:
    c = Counter(r.status for r in results)
    log.info("-" * 60)
    log.info(
        "DONE  ok=%d  skip=%d  fail=%d  rejected=%d  conflict=%d",
        c["ok"],
        c["skip"],
        c["fail"],
        c["rejected"],
        c["conflict"],
    )
    log.info("Per-file results: %s", results_path)
    failed = sum(c[s] for s in _FAILED) + sum(1 for r in audit if r.status in _FAILED)
    if failed:
        log.error("RESULT: FAIL - %d file(s) were not preserved. Fix and re-run; finished files are skipped.", failed)
        return 1
    log.info("RESULT: PASS - every validated file is preserved and verified; the audit record is stored beside it.")
    return 0


# --- command line --------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Upload the validated download to the immutable Azure Blob vault.")
    ap.add_argument("-m", "--manifest", default="manifest.json")
    ap.add_argument("-d", "--source", default="download", help="local download folder (default download)")
    ap.add_argument("--validate-results", default="validate_results.csv", help="output of `validate --hash`")
    ap.add_argument("--verify-against", default="filecopy_results.csv", help="filecopy's recorded SHA-256s")
    ap.add_argument("--results", default="upload_results.csv", help="per-file results CSV")
    ap.add_argument("--account-url", default="", help=f"https://<account>.blob.core.windows.net (or ${_ENV_ACCOUNT})")
    ap.add_argument("--container", default="", help=f"blob container (or ${_ENV_CONTAINER})")
    ap.add_argument("--dest-prefix", default="", help=f"<matter-id>/<collection-id> folder (or ${_ENV_PREFIX})")
    ap.add_argument("--audit-file", action="append", default=[], help="extra file for _audit/ (repeatable)")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--block-workers", type=int, default=4, help="blocks of one file staged at once (default 4)")
    ap.add_argument("--hash-workers", type=int, default=3, help="files hashed at once, to spare the disk (default 3)")
    ap.add_argument("--retries", type=int, default=2)
    ap.add_argument("--chunk-mb", type=float, default=4.0)
    ap.add_argument("--operator", default=None, help="operator identity for the record (opt-in; default unspecified)")
    return ap.parse_args(argv)


def open_container(config: BlobConfig) -> ContainerClient:
    service = BlobServiceClient(config.account_url, credential=DefaultAzureCredential())
    return service.get_container_client(config.container)


def _preserve_audit(
    ctx: Ctx, args: argparse.Namespace, run_meta: dict, results: list[UploadResult], cfg: BlobConfig
) -> list[UploadResult]:
    """Write SHA256SUMS + custody.json locally, then put the whole acquisition record in the vault."""
    results_path = Path(args.results)
    record = [
        *audit_candidates(Path(args.manifest), Path(args.verify_against), Path(args.validate_results)),
        results_path,
        results_path.with_suffix(results_path.suffix + ".metadata.json"),
        *(Path(p) for p in args.audit_file),
    ]
    sums_path = results_path.with_name(_SUMS_NAME)
    sums_path.write_text(build_sha256sums(results, record), encoding="utf-8", newline="\n")
    custody = build_custody(run_meta, cfg.account_url, cfg.container, cfg.prefix, results, _sha256_file(sums_path))
    custody_path = results_path.with_name(_CUSTODY_NAME)
    custody_path.write_text(json.dumps(custody, indent=2), encoding="utf-8", newline="\n")
    run_id = _run_id()
    items = [audit_item(cfg.prefix, run_id, p) for p in [*record, sums_path, custody_path]]
    log.info("Preserving the acquisition record under %s/_audit/%s/ ...", cfg.prefix, run_id)
    return run_transfers(ctx, items, args.concurrency)


def _run(args: argparse.Namespace, cfg: BlobConfig, manifest: dict) -> int:
    entries, rejected = load_entries(manifest, Path(args.validate_results), load_reference(Path(args.verify_against)))
    chunk = max(int(args.chunk_mb * 1024 * 1024), 1)
    hash_sem = threading.BoundedSemaphore(max(args.hash_workers, 1))
    ctx = Ctx(open_container(cfg), cfg.prefix, chunk, args.retries, max(args.block_workers, 1), hash_sem)
    items = [data_item(cfg.prefix, e, Path(args.source)) for e in entries]
    destination = f"{cfg.container}/{cfg.prefix}"
    with metadata.timed_run("upload", args.operator) as run_meta:
        log.info("Uploading %d file(s) to %s/%s ...", len(items), cfg.container, cfg.prefix)
        checkpoint = _Checkpoint(Path(args.results), run_meta, destination)
        try:
            results = run_transfers(ctx, items, args.concurrency, on_result=checkpoint)
        except BaseException:
            checkpoint.flush()  # an interrupted run still leaves a record of what finished
            raise
    results += [_recorded(e, reason) for e, reason in rejected]
    _write_results(Path(args.results), results, run_meta, destination)
    audit = _preserve_audit(ctx, args, run_meta, results, cfg)
    return _summarize(results, audit, Path(args.results))


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # The Azure SDKs log full request URLs and headers at INFO; keep them out of the record.
    for noisy in ("azure", "azure.identity", "azure.core.pipeline.policies.http_logging_policy"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        cfg = resolve_config(args.account_url, args.container, args.dest_prefix, os.environ)
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        return _run(args, cfg, manifest)
    except (AqueductError, FileNotFoundError, ValueError) as exc:
        log.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
