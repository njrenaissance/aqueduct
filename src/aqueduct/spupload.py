"""spupload - upload the validated local download directly to a SharePoint document library via Graph.

Optional step after validate (see ADR-0012):
    1. webenum    - enumerate what the share contained -> manifest.json/.csv
    2. filecopy   - download every file (+ inline SHA-256) -> ./download/
    3. validate   - prove the download matches the manifest (--hash)
    4. spupload   - upload the validated files to SharePoint  (this script)

CHAIN OF CUSTODY: this bypasses the Azure Blob vault. The SharePoint copy is a convenience/review copy, NOT
evidence; the command warns, and the results CSV and sidecar record that the vault was bypassed.

What it does:
    * Uploads only files that passed ``validate --hash`` (read from validate_results.csv). Before sending, the
      file is re-hashed and its SHA-256 must equal the validated value, so a file changed since ``validate``
      is rejected, not uploaded.
    * Small files go up in one PUT; larger ones through a resumable Graph upload session. The manifest folder
      structure is recreated under the target library/folder.
    * Each upload is verified against SharePoint: size and, when SharePoint reports one, QuickXorHash. A
      missing server hash is accepted on size alone and recorded as ``size-only``, never as ``ok``.
    * Re-runs skip items already present with a matching size and QuickXorHash, and replace a mismatching one.
    * Auth is app-only (client credentials, ``Sites.Selected``) for the destination tenant, read from
      ~/.aqueduct/graph.json (secret optionally from $AQUEDUCT_GRAPH_CLIENT_SECRET).

The upload engine is copied from the sibling ``courier`` project, which runs it against Graph.

Usage:
    uv run spupload --dest-url "https://contoso.sharepoint.com/sites/Review/Shared%20Documents/Case%2012"
    uv run spupload --site-url https://contoso.sharepoint.com/sites/Review --library Discovery --target-folder "Case 12"
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import logging
import sys
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path

import httpx

from aqueduct import metadata
from aqueduct.errors import AqueductError, AuthError, IntegrityError, UploadError
from aqueduct.graphclient import (
    ClientCredentialsTokenProvider,
    DriveItemMeta,
    FolderRef,
    GraphClient,
    load_config,
)
from aqueduct.paths import GRAPH_CONFIG_PATH
from aqueduct.quickxorhash import QuickXorHash
from aqueduct.validated import UploadEntry, load_entries

log = logging.getLogger("spupload")

# Graph requires every upload-session chunk except the last to be a multiple of 320 KiB.
_CHUNK_ALIGN = 320 * 1024
_HASH_CHUNK = 4 * 1024 * 1024
_MAX_BACKOFF = 60
_SHAREPOINT_MAX_BYTES = 250 * 1024**3  # SharePoint's per-file limit
_RETRYABLE = (TimeoutError, OSError, httpx.HTTPStatusError, httpx.TransportError, IntegrityError, UploadError)
_VAULT_WARNING = (
    "Direct upload BYPASSES the Azure Blob vault: the SharePoint copy is a convenience/review copy, "
    "not evidence (ADR-0012)."
)
_COLUMNS = ["path", "status", "size", "sha256", "quick_xor_hash", "attempts", "seconds", "detail"]
_UPLOADED = ("ok", "size-only")
_FAILED = ("fail", "rejected")


@dataclass
class UploadResult:
    path: str
    status: str  # ok | size-only | skip | fail | rejected | skipped
    size: int
    sha256: str
    quick_xor_hash: str
    attempts: int
    seconds: float
    detail: str = ""


@dataclass
class _Ctx:
    """Run-wide state shared by every upload task."""

    client: httpx.AsyncClient  # unauthenticated: upload-session URLs are pre-authenticated
    graph: GraphClient
    sem: asyncio.Semaphore
    hash_sem: asyncio.Semaphore
    source_root: Path
    max_retries: int
    chunk: int
    counter: dict


@dataclass(frozen=True)
class _Job:
    entry: UploadEntry
    dest: FolderRef
    source: Path
    size: int
    sha256: str
    digest: str  # QuickXorHash, base64


# --- screening: files SharePoint will not take ----------------------------------


def screen_entries(
    entries: list[UploadEntry], max_bytes: int, blocked_ext: Sequence[str]
) -> tuple[list[UploadEntry], list[tuple[UploadEntry, str]]]:
    """Set aside files SharePoint will not take (blocked extension, over the size limit)."""
    blocked = {ext.lower() for ext in blocked_ext}
    uploadable: list[UploadEntry] = []
    skipped: list[tuple[UploadEntry, str]] = []
    for entry in entries:
        suffix = Path(entry.filename).suffix.lower()
        if suffix and suffix in blocked:
            skipped.append((entry, f"blocked extension ({suffix})"))
        elif entry.size > max_bytes:
            skipped.append((entry, "over size limit"))
        else:
            uploadable.append(entry)
    return uploadable, skipped


# --- hashing, chunking, verification -------------------------------------------


def _hash_file_pair(path: Path) -> tuple[str, str]:
    """(SHA-256 hex, QuickXorHash base64) of a file, both from a single read of it."""
    sha = hashlib.sha256()
    qxh = QuickXorHash()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(_HASH_CHUNK), b""):
            sha.update(block)
            qxh.update(block)
    return sha.hexdigest(), qxh.base64digest()


async def _hash_pair(ctx: _Ctx, source: Path) -> tuple[str, str]:
    async with ctx.hash_sem:
        return await asyncio.to_thread(_hash_file_pair, source)


def _aligned_chunk(chunk: int) -> int:
    """Round ``chunk`` down to a whole number of 320 KiB blocks (min one block)."""
    return max((chunk // _CHUNK_ALIGN) * _CHUNK_ALIGN, _CHUNK_ALIGN)


def _read_chunk(source: Path, start: int, length: int) -> bytes:
    with open(source, "rb") as fh:
        fh.seek(start)
        return fh.read(length)


def _backoff_seconds(exc: BaseException, attempt: int) -> int:
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
        raw = exc.response.headers.get("Retry-After", "")
        if raw.isdigit():
            return int(raw)
    exponential: int = 2 ** (attempt - 1)
    return min(exponential, _MAX_BACKOFF)


def _verify(meta: DriveItemMeta | None, size: int, digest: str, name: str) -> bool:
    """Check the stored item. Returns True if its hash was compared, False if SharePoint reported none."""
    if meta is None:
        raise IntegrityError(f"uploaded item '{name}' was not found for verification")
    if meta.size != size:
        raise IntegrityError(f"size mismatch after upload: stored {meta.size:,}, expected {size:,}")
    if not meta.quick_xor_hash:
        log.warning("SharePoint reported no QuickXorHash for %s; accepted on size alone (size-only)", name)
        return False
    if meta.quick_xor_hash != digest:
        raise IntegrityError(f"QuickXorHash mismatch after upload: stored {meta.quick_xor_hash}, local {digest}")
    return True


# --- transfer ------------------------------------------------------------------


async def _next_expected_start(client: httpx.AsyncClient, session_url: str) -> int:
    """Ask the upload session where to resume; a fresh session answers byte 0."""
    resp = await client.get(session_url)
    if resp.status_code != HTTPStatus.OK:
        return 0
    ranges = dict(resp.json()).get("nextExpectedRanges") or []
    return int(str(ranges[0]).split("-", 1)[0]) if ranges else 0


async def _upload_via_session(client: httpx.AsyncClient, session_url: str, source: Path, size: int, chunk: int) -> None:
    """PUT ``source`` to an upload session in aligned chunks, resuming where it left off."""
    start = await _next_expected_start(client, session_url)
    while start < size:
        end = min(start + chunk, size)
        block = await asyncio.to_thread(_read_chunk, source, start, end - start)
        headers = {"Content-Range": f"bytes {start}-{end - 1}/{size}"}
        resp = await client.put(session_url, content=block, headers=headers)
        resp.raise_for_status()
        if resp.status_code in (HTTPStatus.OK, HTTPStatus.CREATED):  # the final chunk finalizes the item
            return
        start = end


async def _put_file(ctx: _Ctx, job: _Job) -> None:
    """Transfer one file up - a single PUT for small files, a session for large ones."""
    if job.size <= ctx.chunk:
        content = await asyncio.to_thread(job.source.read_bytes)
        await ctx.graph.upload_small(job.dest, job.entry.filename, content)
        return
    session_url = await ctx.graph.create_upload_session(job.dest, job.entry.filename)
    await _upload_via_session(ctx.client, session_url, job.source, job.size, _aligned_chunk(ctx.chunk))


def _result(
    entry: UploadEntry, status: str, size: int, sha256: str, digest: str, attempts: int, secs: float
) -> UploadResult:
    return UploadResult(entry.path, status, size, sha256, digest, attempts, secs)


def _failure(
    entry: UploadEntry, status: str, size: int, detail: str, attempts: int = 0, secs: float = 0.0
) -> UploadResult:
    return UploadResult(entry.path, status, size, entry.sha256, "", attempts, secs, detail)


async def _try_upload(ctx: _Ctx, job: _Job) -> UploadResult:
    entry = job.entry
    started = time.monotonic()
    for attempt in range(1, ctx.max_retries + 2):  # 1 try + retries
        try:
            await _put_file(ctx, job)
            meta = await ctx.graph.child_meta(job.dest, entry.filename)
            hash_checked = _verify(meta, job.size, job.digest, entry.filename)
        except _RETRYABLE as exc:
            if attempt >= ctx.max_retries + 1:
                log.error("FAIL  %s - %s (after %d tries)", entry.path, exc, attempt)
                return _failure(entry, "fail", job.size, str(exc), attempt, time.monotonic() - started)
            backoff = _backoff_seconds(exc, attempt)
            log.warning("retry %s - %s (try %d/%d; %ds)", entry.path, exc, attempt, ctx.max_retries + 1, backoff)
            await asyncio.sleep(backoff)
        else:
            secs = time.monotonic() - started
            log.info("ok    %s (%s B, try %d, %.1fs)", entry.path, f"{job.size:,}", attempt, secs)
            status = "ok" if hash_checked else "size-only"
            return _result(entry, status, job.size, job.sha256, job.digest, attempt, secs)
    return _failure(entry, "fail", job.size, "logic error")  # unreachable


async def _lookup_existing(ctx: _Ctx, dest: FolderRef, entry: UploadEntry) -> tuple[DriveItemMeta | None, str]:
    """The item already at the destination (if any), or an error detail if the lookup failed."""
    try:
        return await ctx.graph.child_meta(dest, entry.filename), ""
    except (UploadError, httpx.HTTPError, AqueductError) as exc:
        return None, str(exc)


async def upload_one(ctx: _Ctx, entry: UploadEntry, dest: FolderRef) -> UploadResult:
    """Upload one validated file into ``dest``: re-hash, skip if already there, else send and verify."""
    source = entry.local_path(ctx.source_root)
    if not source.exists():
        log.error("FAIL  %s - source file not found: %s", entry.path, source)
        return _failure(entry, "fail", 0, f"source file not found: {source}")
    size = source.stat().st_size
    sha256, digest = await _hash_pair(ctx, source)
    if entry.sha256 and sha256 != entry.sha256:
        log.error("REJECT  %s - changed since validate (SHA-256 differs from the validated value)", entry.path)
        return _failure(entry, "rejected", size, "changed since validate (SHA-256 mismatch)")

    existing, error = await _lookup_existing(ctx, dest, entry)
    if error:
        return _failure(entry, "fail", size, error)
    if existing is not None and existing.size == size and existing.quick_xor_hash == digest:
        log.info("skip  %s (already uploaded, hash matches)", entry.path)
        return _result(entry, "skip", size, sha256, digest, 0, 0.0)
    if existing is not None:
        log.warning(
            "replacing %s: the item on SharePoint differs (size %s, hash %s)",
            entry.path,
            existing.size,
            existing.quick_xor_hash,
        )

    async with ctx.sem:
        result = await _try_upload(ctx, _Job(entry, dest, source, size, sha256, digest))
    if result.status in _UPLOADED:
        ctx.counter["done"] += 1
    return result


# --- folders and run -----------------------------------------------------------


async def prepare_folders(graph: GraphClient, base: FolderRef, entries: list[UploadEntry]) -> dict[str, FolderRef]:
    """Create every distinct destination folder once (sequentially, to avoid create races)."""
    cache: dict[str, FolderRef] = {"": base}
    for destination in dict.fromkeys(entry.destination for entry in entries):
        folder = base
        accumulated = ""
        for part in (p for p in destination.split("/") if p):
            accumulated = f"{accumulated}/{part}" if accumulated else part
            if accumulated not in cache:
                cache[accumulated] = await graph.ensure_child_folder(folder, part)
            folder = cache[accumulated]
    return {destination: cache[destination] for destination in dict.fromkeys(entry.destination for entry in entries)}


def _recorded(entry: UploadEntry, status: str, detail: str) -> UploadResult:
    """A row for a file that was not attempted (rejected by the gate, or set aside by screening)."""
    return UploadResult(entry.path, status, entry.size, entry.sha256, "", 0, 0.0, detail)


def _write_results(results_path: Path, results: list[UploadResult], run_metadata: dict) -> None:
    with open(results_path, "w", newline="", encoding="utf-8-sig") as fh:
        header = metadata.format_metadata_header(
            tool=run_metadata["tool"],
            version=run_metadata["tool_version"],
            operator=run_metadata["operator"],
            host_info=run_metadata["host_info"],
            started_at_utc=run_metadata["started_at_utc"],
            completed_at_utc=run_metadata["completed_at_utc"],
        )
        for line in [*header, "# vault: BYPASSED - convenience/review copy, not evidence (ADR-0012)"]:
            fh.write(line + "\r\n")
        writer = csv.writer(fh)
        writer.writerow(_COLUMNS)
        for r in results:
            writer.writerow(
                [r.path, r.status, r.size, r.sha256, r.quick_xor_hash, r.attempts, f"{r.seconds:.1f}", r.detail]
            )
    metadata.write_metadata_sidecar(results_path, run_metadata)


def _summarize(results: list[UploadResult], results_path: Path) -> int:
    c = Counter(r.status for r in results)
    log.info("-" * 60)
    log.info(
        "DONE  ok=%d  size-only=%d  skip=%d  fail=%d  rejected=%d  skipped=%d",
        c["ok"], c["size-only"], c["skip"], c["fail"], c["rejected"], c["skipped"],
    )  # fmt: skip
    log.info("Per-file results: %s", results_path)
    if c["size-only"]:
        log.warning(
            "%d file(s) were accepted on size alone (SharePoint reported no hash); re-check them later.", c["size-only"]
        )
    failed = c["fail"] + c["rejected"]
    if failed:
        log.error("RESULT: FAIL - %d file(s) were not uploaded. Fix and re-run; finished files are skipped.", failed)
        return 1
    log.info("RESULT: PASS - every validated file is on SharePoint (see size-only above for any not hash-verified).")
    return 0


async def run(
    ctx: _Ctx,
    entries: list[UploadEntry],
    rejected: list[tuple[UploadEntry, str]],
    base: FolderRef,
    results_path: Path,
    skipped: list[tuple[UploadEntry, str]] | None = None,
    operator: str | None = None,
) -> int:
    """Ensure the folder tree, upload every entry, write the results CSV + sidecar, return the exit code."""
    with metadata.timed_run("spupload", operator) as run_metadata:
        run_metadata["vault_bypassed"] = True
        log.warning(_VAULT_WARNING)
        log.info("Uploading %d file(s) into the destination folder...", len(entries))
        folders = await prepare_folders(ctx.graph, base, entries)
        results = list(await asyncio.gather(*(upload_one(ctx, e, folders[e.destination]) for e in entries)))
    results += [_recorded(e, "rejected", reason) for e, reason in rejected]
    results += [_recorded(e, "skipped", reason) for e, reason in skipped or []]
    _write_results(results_path, results, run_metadata)
    return _summarize(results, results_path)


# --- command line --------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Upload the validated download to a SharePoint document library (Graph).")
    ap.add_argument("-m", "--manifest", default="manifest.json")
    ap.add_argument("-d", "--source", default="download", help="local download folder (default download)")
    ap.add_argument("--validate-results", default="validate_results.csv", help="output of `validate --hash`")
    ap.add_argument("--results", default="spupload_results.csv", help="per-file results CSV")
    ap.add_argument("--dest-url", default="", help="SharePoint folder URL: <site>/<library>[/<folder>]")
    ap.add_argument("--site-url", default="", help="SharePoint site URL (with --library)")
    ap.add_argument("--library", default="", help="document library name (with --site-url)")
    ap.add_argument("--target-folder", default="", help="folder path inside the library (with --site-url)")
    ap.add_argument(
        "--config", default=str(GRAPH_CONFIG_PATH), help="app registration JSON (default ~/.aqueduct/graph.json)"
    )
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--retries", type=int, default=2)
    ap.add_argument("--chunk-mb", type=float, default=4.0)
    ap.add_argument("--hash-workers", type=int, default=3)
    ap.add_argument("--max-bytes", type=int, default=_SHAREPOINT_MAX_BYTES, help="skip files larger than this")
    ap.add_argument("--blocked-ext", default="", help="comma-separated extensions to skip (e.g. .exe,.dll)")
    ap.add_argument("--operator", default=None, help="operator identity for the record (opt-in; default unspecified)")
    args = ap.parse_args(argv)
    site_form = bool(args.site_url or args.library or args.target_folder)
    if args.dest_url and site_form:
        ap.error("give either --dest-url or --site-url/--library/--target-folder, not both")
    if not args.dest_url and not (args.site_url and args.library):
        ap.error("a destination is required: --dest-url, or --site-url together with --library")
    return args


async def resolve_target(graph: GraphClient, args: argparse.Namespace) -> FolderRef:
    """Resolve the destination folder from whichever form was given."""
    if args.dest_url:
        return await graph.resolve_destination_url(args.dest_url)
    return await graph.resolve_destination(args.site_url, args.library, args.target_folder)


async def _run_async(
    args: argparse.Namespace,
    entries: list[UploadEntry],
    rejected: list[tuple[UploadEntry, str]],
    skipped: list[tuple[UploadEntry, str]],
) -> int:
    config = load_config(Path(args.config))
    timeout = httpx.Timeout(60.0, read=300.0)
    async with httpx.AsyncClient(timeout=timeout) as api, httpx.AsyncClient(timeout=timeout) as raw:
        graph = GraphClient(api, ClientCredentialsTokenProvider(config), config.graph_base_url)
        base = await resolve_target(graph, args)
        ctx = _Ctx(
            client=raw,
            graph=graph,
            sem=asyncio.Semaphore(args.concurrency),
            hash_sem=asyncio.Semaphore(args.hash_workers),
            source_root=Path(args.source),
            max_retries=args.retries,
            chunk=int(args.chunk_mb * 1024 * 1024),
            counter={"total": len(entries), "done": 0},
        )
        return await run(ctx, entries, rejected, base, Path(args.results), skipped=skipped, operator=args.operator)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # httpx logs full request URLs at INFO, which would include pre-authenticated upload-session URLs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    try:
        eligible, rejected = load_entries(manifest, Path(args.validate_results))
    except FileNotFoundError as exc:
        log.error("%s", exc)
        return 2
    blocked = [e.strip() for e in args.blocked_ext.split(",") if e.strip()]
    entries, skipped = screen_entries(eligible, args.max_bytes, blocked)
    try:
        return asyncio.run(_run_async(args, entries, rejected, skipped))
    except (AuthError, AqueductError) as exc:
        log.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
