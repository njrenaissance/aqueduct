"""publish - stream the preserved files from the Azure Blob vault into a SharePoint library (review copy; ADR-0014).

Runs after ``upload`` has preserved a collection in the vault:
    5. upload    - preserve the validated files in the Blob vault
    6. publish   - copy them from the vault to the SharePoint review library   (this script)

SharePoint cannot mount a blob container, so the bytes are copied, but never through local disk: each blob is
read by range and sent straight to Graph. The SharePoint copy derives from the preserved vault bytes, so it is a
review copy of the evidence, not a copy that bypasses the vault (contrast ``spupload``, ADR-0012).

What it does:
    * Lists ``<prefix>/data/`` with the operator's Azure identity (``DefaultAzureCredential``; read-only, needs
      only Storage Blob Data Reader) and reads each blob's ``sha256``, ``sourcepath`` and ``uniqueid`` metadata,
      which ``upload`` wrote at commit. The vault is never written.
    * Storage policy, before any byte is read: a blob larger than ``--max-bytes`` (or SharePoint's 250 GB limit),
      an audio/video file (unless ``--include-av``), or one with an extension in ``--skip-ext`` is set aside. It
      gets a ``skipped`` row and stays in the vault only. ``skipped`` is not a failure. Screening is by extension
      and size only; content is never inspected.
    * Re-runs skip an item already on SharePoint with a matching size and QuickXorHash. The vault records no
      QuickXorHash, so such a blob is read once to hash it (nothing is sent).
    * Small files go up in one PUT (their SHA-256 is checked before sending); larger ones through a Graph upload
      session in 320 KiB-aligned chunks, each read with ``download_blob(offset, length)``. A transient failure
      resumes from the session's ``nextExpectedRanges``, re-reading only the blob range still needed.
    * SHA-256 and QuickXorHash are computed while streaming. A file is ``ok`` only when the streamed SHA-256 equals
      the blob's recorded ``sha256`` and SharePoint reports the same size and QuickXorHash. A missing server hash
      is ``size-only``, never ``ok``; any disagreement is ``fail`` (``--delete-mismatched`` removes that item).
    * Writes ``publish_results.csv`` and a ``.metadata.json`` sidecar - nothing else is written locally.

Usage:
    uv run publish --account-url https://contoso.blob.core.windows.net --container vault \\
        --dest-prefix 2026-0042-smith/20261004-share-a \\
        --dest-url "https://contoso.sharepoint.com/sites/Review/Shared%20Documents/Case%2012"
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import logging
import os
import sys
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
from azure.core.exceptions import AzureError

from aqueduct import metadata
from aqueduct.blobupload import data_blob_name, open_container, resolve_config
from aqueduct.errors import AqueductError, IntegrityError, UploadError
from aqueduct.graphclient import (
    ClientCredentialsTokenProvider,
    DriveItemMeta,
    FolderRef,
    GraphClient,
    load_config,
)
from aqueduct.quickxorhash import QuickXorHash
from aqueduct.spupload import _SHAREPOINT_MAX_BYTES as SHAREPOINT_MAX_BYTES
from aqueduct.spupload import _aligned_chunk as aligned_chunk
from aqueduct.spupload import _backoff_seconds as backoff_seconds
from aqueduct.spupload import _next_expected_start as next_expected_start
from aqueduct.spupload import _verify as verify_stored_item
from aqueduct.spupload import (
    add_destination_arguments,
    check_destination,
    prepare_folders,
    resolve_target,
    screen_entries,
)
from aqueduct.validated import UploadEntry

log = logging.getLogger("publish")

DEFAULT_MAX_BYTES = 1024**3  # placeholder storage-policy limit (1 GiB), to be confirmed (ADR-0014)
AUDIOVISUAL_EXT = (
    ".mp4", ".m4v", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm", ".mpg", ".mpeg",
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".aiff",
)  # fmt: skip
_MIB = 1024 * 1024
# A failed integrity check is never retried: re-sending the same vault bytes cannot change the answer.
_RETRYABLE = (OSError, httpx.HTTPStatusError, httpx.TransportError, UploadError, AzureError)
_COLUMNS = ["path", "status", "size", "sha256", "quick_xor_hash", "uniqueid", "attempts", "seconds", "detail"]


@dataclass(frozen=True)
class VaultBlob:
    """One preserved file: its blob name in the vault and where it goes in SharePoint (as an ``UploadEntry``)."""

    name: str
    entry: UploadEntry  # path/filename/destination, size, the recorded sha256, and the source uniqueid


@dataclass
class PublishResult:
    path: str
    status: str  # ok | size-only | skip | fail | skipped
    size: int
    sha256: str  # the SHA-256 recorded on the blob
    quick_xor_hash: str
    uniqueid: str
    attempts: int
    seconds: float
    detail: str = ""


@dataclass
class Ctx:
    """Run-wide state shared by every transfer."""

    container: Any  # a read-only ContainerClient (a fake in tests)
    graph: GraphClient
    client: httpx.AsyncClient  # unauthenticated: upload-session URLs are pre-authenticated
    sem: asyncio.Semaphore
    max_retries: int
    chunk: int
    delete_mismatched: bool = False


@dataclass
class _Stream:
    """Hash state for one blob as it streams, kept across retries so a resumed range is never hashed twice."""

    sha: Any = field(default_factory=hashlib.sha256)
    qxh: QuickXorHash = field(default_factory=QuickXorHash)
    hashed: int = 0  # bytes fed to both hashes so far
    session_url: str = ""
    data: bytes | None = None  # the whole blob, for a small file sent in one PUT

    def feed(self, start: int, data: bytes) -> None:
        """Hash the part of ``data`` (read at byte ``start``) that has not been hashed yet."""
        if start > self.hashed:
            raise IntegrityError(f"upload session expects byte {start:,} but only {self.hashed:,} were streamed")
        fresh = data[self.hashed - start :]
        self.sha.update(fresh)
        self.qxh.update(fresh)
        self.hashed += len(fresh)

    @property
    def sha256(self) -> str:
        return str(self.sha.hexdigest())

    @property
    def digest(self) -> str:
        return self.qxh.base64digest()


# --- the vault: listing and ranged reads ------------------------------------------------


def list_vault(container: Any, prefix: str) -> list[VaultBlob]:
    """Every blob under ``<prefix>/data/``, with the metadata ``upload`` recorded on it."""
    root = data_blob_name(prefix, "")
    blobs: list[VaultBlob] = []
    for props in container.list_blobs(name_starts_with=root, include=["metadata"]):
        meta = props.metadata or {}
        path = unquote(meta.get("sourcepath", "")) or props.name[len(root) :]
        entry = UploadEntry.from_manifest_path(path, int(props.size), meta.get("sha256", ""), meta.get("uniqueid", ""))
        blobs.append(VaultBlob(props.name, entry))
    return blobs


def _read_range(container: Any, name: str, offset: int, length: int) -> bytes:
    if length <= 0:
        return b""
    data: bytes = container.get_blob_client(name).download_blob(offset=offset, length=length).readall()
    return data


def _read_and_hash(container: Any, blob: VaultBlob, start: int, length: int, stream: _Stream) -> bytes:
    data = _read_range(container, blob.name, start, length)
    stream.feed(start, data)
    return data


async def _read(ctx: Ctx, blob: VaultBlob, start: int, length: int, stream: _Stream) -> bytes:
    """Read one range of the blob and hash it, off the event loop (QuickXorHash is CPU-bound)."""
    return await asyncio.to_thread(_read_and_hash, ctx.container, blob, start, length, stream)


async def _hash_blob(ctx: Ctx, blob: VaultBlob) -> _Stream:
    """Read the whole blob once, in ranges, only to hash it."""
    stream = _Stream()
    size = blob.entry.size
    for start in range(0, size, ctx.chunk):
        await _read(ctx, blob, start, min(ctx.chunk, size - start), stream)
    return stream


# --- storage policy -----------------------------------------------------------------------


def _extensions(raw: Sequence[str]) -> list[str]:
    return [f".{ext.strip().lstrip('.').lower()}" for ext in raw if ext.strip().lstrip(".")]


def screen_blobs(
    blobs: list[VaultBlob], max_bytes: int, include_av: bool, skip_ext: Sequence[str]
) -> tuple[list[VaultBlob], list[tuple[VaultBlob, str]]]:
    """Set aside, by name and size only, what the storage policy keeps out of SharePoint.

    Rules apply in order, so a file matching several gets the first reason: over the size limit (``max_bytes``,
    0 = no policy limit, and always SharePoint's 250 GB), audiovisual (unless ``include_av``), excluded extension.
    """
    limit = min(max_bytes, SHAREPOINT_MAX_BYTES) if max_bytes > 0 else SHAREPOINT_MAX_BYTES
    rules: list[tuple[int, Sequence[str], str]] = [(limit, (), "over size limit")]
    if not include_av:
        rules.append((SHAREPOINT_MAX_BYTES, AUDIOVISUAL_EXT, "audiovisual"))
    rules.append((SHAREPOINT_MAX_BYTES, _extensions(skip_ext), "excluded extension"))
    by_path = {blob.entry.path: blob for blob in blobs}
    remaining = [blob.entry for blob in blobs]
    skipped: list[tuple[VaultBlob, str]] = []
    for rule_limit, extensions, reason in rules:
        remaining, set_aside = screen_entries(remaining, rule_limit, extensions)
        skipped += [(by_path[entry.path], reason) for entry, _ in set_aside]
    return [by_path[entry.path] for entry in remaining], skipped


# --- transfer -------------------------------------------------------------------------------


def _row(
    blob: VaultBlob, status: str, digest: str = "", attempts: int = 0, secs: float = 0.0, detail: str = ""
) -> PublishResult:
    entry = blob.entry
    return PublishResult(entry.path, status, entry.size, entry.sha256, digest, entry.item_id, attempts, secs, detail)


def _sha256_mismatch(blob: VaultBlob, stream: _Stream) -> str:
    return f"SHA-256 mismatch: streamed {stream.sha256}, the vault records {blob.entry.sha256}"


async def _send_small(ctx: Ctx, blob: VaultBlob, dest: FolderRef, stream: _Stream) -> None:
    """One PUT. The whole file is in hand first, so its SHA-256 is checked before anything is sent."""
    if stream.data is None:
        stream.data = await _read(ctx, blob, 0, blob.entry.size, stream)
    if stream.sha256 != blob.entry.sha256:
        raise IntegrityError(_sha256_mismatch(blob, stream) + "; not uploaded")
    await ctx.graph.upload_small(dest, blob.entry.filename, stream.data)


async def _send_session(ctx: Ctx, blob: VaultBlob, dest: FolderRef, stream: _Stream) -> None:
    """Stream the blob into an upload session in aligned chunks, resuming where the session left off."""
    size = blob.entry.size
    chunk = aligned_chunk(ctx.chunk)
    if not stream.session_url:
        stream.session_url = await ctx.graph.create_upload_session(dest, blob.entry.filename)
    start = await next_expected_start(ctx.client, stream.session_url)
    while start < size:
        end = min(start + chunk, size)
        block = await _read(ctx, blob, start, end - start, stream)
        headers = {"Content-Range": f"bytes {start}-{end - 1}/{size}"}
        resp = await ctx.client.put(stream.session_url, content=block, headers=headers)
        resp.raise_for_status()
        if resp.status_code in (HTTPStatus.OK, HTTPStatus.CREATED):  # the final chunk finalizes the item
            return
        start = end


async def _send(ctx: Ctx, blob: VaultBlob, dest: FolderRef, stream: _Stream) -> DriveItemMeta | None:
    """One attempt: send the bytes, then look up what SharePoint stored."""
    if blob.entry.size <= ctx.chunk:
        await _send_small(ctx, blob, dest, stream)
    else:
        await _send_session(ctx, blob, dest, stream)
    return await ctx.graph.child_meta(dest, blob.entry.filename)


async def _delete(ctx: Ctx, dest: FolderRef, name: str) -> str:
    try:
        await ctx.graph.delete_child(dest, name)
    except (AqueductError, httpx.HTTPError) as exc:
        return f"; could not delete the SharePoint item: {exc}"
    return "; the SharePoint item was deleted"


async def _verified(
    ctx: Ctx, blob: VaultBlob, dest: FolderRef, stream: _Stream, meta: DriveItemMeta | None, attempt: int, secs: float
) -> PublishResult:
    """Judge the finished transfer: streamed SHA-256 vs the vault record, then SharePoint's size and QuickXorHash."""
    entry = blob.entry
    try:
        if stream.hashed != entry.size:
            raise IntegrityError(f"streamed {stream.hashed:,} bytes, the vault lists {entry.size:,}")
        if stream.sha256 != entry.sha256:
            raise IntegrityError(_sha256_mismatch(blob, stream))
        hash_checked = verify_stored_item(meta, entry.size, stream.digest, entry.filename)
    except IntegrityError as exc:
        detail = str(exc) + (await _delete(ctx, dest, entry.filename) if ctx.delete_mismatched else "")
        log.error("FAIL  %s - %s", entry.path, detail)
        return _row(blob, "fail", stream.digest, attempt, secs, detail)
    log.info("ok    %s (%s B, try %d, %.1fs)", entry.path, f"{entry.size:,}", attempt, secs)
    return _row(blob, "ok" if hash_checked else "size-only", stream.digest, attempt, secs)


async def _transfer(ctx: Ctx, blob: VaultBlob, dest: FolderRef) -> PublishResult:
    """Send with retries; a retry resumes the same upload session and keeps the hash state."""
    started = time.monotonic()
    stream = _Stream()
    attempts = ctx.max_retries + 1
    for attempt in range(1, attempts + 1):
        try:
            meta = await _send(ctx, blob, dest, stream)
        except _RETRYABLE as exc:
            # An HTTPStatusError's text carries the pre-authenticated upload-session URL: never log or record it.
            reason = f"HTTP {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError) else str(exc)
            if attempt >= attempts:
                log.error("FAIL  %s - %s (after %d tries)", blob.entry.path, reason, attempt)
                return _row(blob, "fail", "", attempt, time.monotonic() - started, reason)
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == HTTPStatus.NOT_FOUND:
                stream.session_url = ""  # the session expired: open a new one
            backoff = backoff_seconds(exc, attempt)
            log.warning("retry %s - %s (try %d/%d; %ds)", blob.entry.path, reason, attempt, attempts, backoff)
            await asyncio.sleep(backoff)
        else:
            return await _verified(ctx, blob, dest, stream, meta, attempt, time.monotonic() - started)
    raise AssertionError("unreachable")  # pragma: no cover


async def _already_published(ctx: Ctx, blob: VaultBlob, dest: FolderRef) -> PublishResult | None:
    """A ``skip`` or ``fail`` row when SharePoint already has this item (or the vault blob is bad), else None."""
    entry = blob.entry
    existing = await ctx.graph.child_meta(dest, entry.filename)
    if existing is None:
        return None
    if existing.size == entry.size and existing.quick_xor_hash:
        stream = await _hash_blob(ctx, blob)
        if stream.sha256 != entry.sha256:
            log.error("FAIL  %s - the vault blob does not match its recorded SHA-256", entry.path)
            return _row(blob, "fail", stream.digest, detail=_sha256_mismatch(blob, stream))
        if stream.digest == existing.quick_xor_hash:
            log.info("skip  %s (already on SharePoint, hash matches)", entry.path)
            return _row(blob, "skip", stream.digest)
    log.warning("replacing %s: the item on SharePoint differs (size %s)", entry.path, f"{existing.size:,}")
    return None


async def publish_one(ctx: Ctx, blob: VaultBlob, dest: FolderRef) -> PublishResult:
    """Publish one blob into ``dest``: skip it if already there, else stream it across and verify it."""
    if not blob.entry.sha256:
        log.error("FAIL  %s - no SHA-256 recorded on the blob", blob.entry.path)
        return _row(blob, "fail", detail="no SHA-256 recorded on the blob; it cannot be verified, so it is not sent")
    async with ctx.sem:
        try:
            return await _already_published(ctx, blob, dest) or await _transfer(ctx, blob, dest)
        except (AqueductError, AzureError, httpx.HTTPError, OSError) as exc:
            log.error("FAIL  %s - %s", blob.entry.path, exc)
            return _row(blob, "fail", detail=str(exc))


# --- results and run -------------------------------------------------------------------------


def _write_results(results_path: Path, results: list[PublishResult], run_metadata: dict) -> None:
    policy = ", ".join(f"{key}={value}" for key, value in run_metadata["policy"].items())
    with open(results_path, "w", newline="", encoding="utf-8-sig") as fh:
        header = metadata.format_metadata_header(
            tool=run_metadata["tool"],
            version=run_metadata["tool_version"],
            operator=run_metadata["operator"],
            host_info=run_metadata["host_info"],
            started_at_utc=run_metadata["started_at_utc"],
            completed_at_utc=run_metadata["completed_at_utc"],
        )
        notes = [
            f"# source: vault {run_metadata['source']} (read-only); the SharePoint copy is a derived review copy "
            "of the preserved bytes (ADR-0014)",
            f"# policy: {policy}; skipped files exist only in the vault",
        ]
        for line in [*header, *notes]:
            fh.write(line + "\r\n")
        writer = csv.writer(fh)
        writer.writerow(_COLUMNS)
        for r in results:
            writer.writerow(
                [r.path, r.status, r.size, r.sha256, r.quick_xor_hash, r.uniqueid, r.attempts, f"{r.seconds:.1f}"]
                + [r.detail]
            )
    metadata.write_metadata_sidecar(results_path, run_metadata)


def _summarize(results: list[PublishResult], results_path: Path) -> int:
    c = Counter(r.status for r in results)
    log.info("-" * 60)
    log.info(
        "DONE  ok=%d  size-only=%d  skip=%d  fail=%d  skipped=%d",
        c["ok"], c["size-only"], c["skip"], c["fail"], c["skipped"],
    )  # fmt: skip
    log.info("Per-file results: %s", results_path)
    if c["skipped"]:
        log.info("%d file(s) were set aside by the storage policy; they are in the vault only.", c["skipped"])
    if c["size-only"]:
        log.warning(
            "%d file(s) were accepted on size alone (SharePoint reported no hash); re-check them later.", c["size-only"]
        )
    if c["fail"]:
        log.error(
            "RESULT: FAIL - %d file(s) were not published. Fix and re-run; finished files are skipped.", c["fail"]
        )
        return 1
    log.info("RESULT: PASS - every file within the storage policy is on SharePoint.")
    return 0


async def run(
    ctx: Ctx,
    blobs: list[VaultBlob],
    skipped: list[tuple[VaultBlob, str]],
    base: FolderRef,
    results_path: Path,
    source: str,
    policy: dict,
    operator: str | None = None,
) -> int:
    """Ensure the folder tree, publish every blob, write the results CSV + sidecar, return the exit code."""
    with metadata.timed_run("publish", operator) as run_metadata:
        run_metadata.update(source=source, vault_access="read-only", policy=policy)
        log.info(
            "Publishing %d file(s) from %s (%d set aside by the storage policy)...", len(blobs), source, len(skipped)
        )
        folders = await prepare_folders(ctx.graph, base, [blob.entry for blob in blobs])
        results = list(await asyncio.gather(*(publish_one(ctx, b, folders[b.entry.destination]) for b in blobs)))
    results += [_row(blob, "skipped", detail=reason) for blob, reason in skipped]
    _write_results(results_path, results, run_metadata)
    return _summarize(results, results_path)


# --- command line ------------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Publish the preserved files from the Azure Blob vault to SharePoint.")
    ap.add_argument(
        "--account-url", default="", help="https://<account>.blob.core.windows.net (or $AQUEDUCT_BLOB_ACCOUNT_URL)"
    )
    ap.add_argument("--container", default="", help="blob container (or $AQUEDUCT_BLOB_CONTAINER)")
    ap.add_argument("--dest-prefix", default="", help="<matter-id>/<collection-id> folder (or $AQUEDUCT_BLOB_PREFIX)")
    add_destination_arguments(ap)
    ap.add_argument("--results", default="publish_results.csv", help="per-file results CSV")
    ap.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_MAX_BYTES,
        help="set aside files larger than this (default 1 GiB, a placeholder; 0 = no limit beyond SharePoint's 250 GB)",
    )
    ap.add_argument("--include-av", action="store_true", help="also publish audio/video files (set aside by default)")
    ap.add_argument("--skip-ext", default="", help="comma-separated extensions to set aside (e.g. .exe,.iso)")
    ap.add_argument("--delete-mismatched", action="store_true", help="delete a SharePoint item that fails verification")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--retries", type=int, default=2)
    ap.add_argument("--chunk-mb", type=float, default=4.0)
    ap.add_argument("--operator", default=None, help="operator identity for the record (opt-in; default unspecified)")
    args = ap.parse_args(argv)
    check_destination(ap, args)
    return args


async def _run_async(
    args: argparse.Namespace,
    container: Any,
    blobs: list[VaultBlob],
    skipped: list[tuple[VaultBlob, str]],
    source: str,
    policy: dict,
) -> int:
    config = load_config(Path(args.config))
    timeout = httpx.Timeout(60.0, read=300.0)
    async with httpx.AsyncClient(timeout=timeout) as api, httpx.AsyncClient(timeout=timeout) as raw:
        graph = GraphClient(api, ClientCredentialsTokenProvider(config), config.graph_base_url)
        base = await resolve_target(graph, args)
        ctx = Ctx(
            container=container,
            graph=graph,
            client=raw,
            sem=asyncio.Semaphore(max(args.concurrency, 1)),
            max_retries=args.retries,
            chunk=max(int(args.chunk_mb * _MIB), 1),
            delete_mismatched=args.delete_mismatched,
        )
        return await run(ctx, blobs, skipped, base, Path(args.results), source, policy, args.operator)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # httpx logs pre-authenticated upload-session URLs, and the Azure SDKs request URLs and headers, at INFO.
    for noisy in ("httpx", "azure", "azure.identity", "azure.core.pipeline.policies.http_logging_policy"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        cfg = resolve_config(args.account_url, args.container, args.dest_prefix, os.environ)
        container = open_container(cfg)
        skip_ext = _extensions(args.skip_ext.split(","))
        publishable, skipped = screen_blobs(
            list_vault(container, cfg.prefix), args.max_bytes, args.include_av, skip_ext
        )
        policy = {"max_bytes": args.max_bytes, "include_av": args.include_av, "skip_ext": skip_ext}
        source = f"{cfg.account_url}/{cfg.container}/{cfg.prefix}/data"
        return asyncio.run(_run_async(args, container, publishable, skipped, source, policy))
    except (AqueductError, AzureError, httpx.HTTPError) as exc:
        log.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
