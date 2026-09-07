"""filecopy - high-throughput, resumable bulk download of a web-only OneDrive/
SharePoint share, driven by an already-enumerated manifest.

This is the download counterpart to webenum.py. webenum produces the dated record
of *what the share contained* (manifest.json/.csv); filecopy pulls the bytes.

Why a dedicated async downloader (and not rclone/aria2):
    This is a *logical acquisition of a remote source* - physical (block-device)
    acquisition is impossible when the data lives on someone else's datacenter disks,
    so acquisition moves up to the file/API layer. General downloaders hash to *verify
    against a supplied digest*; we hash to *produce the acquisition record*. See
    docs/adr/0011-LOGICAL-ACQUISITION-OF-A-REMOTE-SOURCE.md for the full framing.

    Concretely: these "specific people"/guest shares authorize the interactive web
    session, not a Graph token - so we ride the same saved cookies webenum uses
    (auth_state.json), never the Graph API. And the share is large (500+ GB, some
    files 50+ GB), so we download many files at once, cap concurrency with a
    semaphore, and resume partial files byte-accurately on retry.

Endpoint choice (see the probes that justified it):
    We fetch each file via SharePoint's `_layouts/15/download.aspx?SourceUrl=...`.
    Unlike `_api/web/.../$value` (which ignores Range and always returns the whole
    file), download.aspx honors HTTP Range - so a retried or resumed 50 GB pull
    picks up from the .part on disk instead of starting over.

Usage:
    uv run filecopy                 # download every file in manifest.json -> ./download/
    uv run filecopy -c 8            # 8 concurrent downloads
    uv run filecopy --retries 4     # 4 retries (5 tries total) per file
    uv run filecopy --limit 20      # smallest 20 files only (good smoke test)

Resume is automatic and safe to run repeatedly: a file already present at its full
manifest size is skipped; a half-finished `.part` continues via Range. Nothing is
deleted.

Each file's SHA-256 is computed inline as it streams (free — no extra disk read) and
recorded in the results CSV as the acquisition-time integrity fingerprint (see
docs/adr/ADR-0006). `--no-hash` disables it. A re-run reuses digests already recorded
in the same results file, so it never re-hashes finished data.

Auth note: ~/.aqueduct/auth_state.json is a live logged-in web session (as sensitive
as a password) and it expires. If downloads start returning 401/403, re-run
`login` to refresh it.
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
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx

from aqueduct import paths

_HASH_CHUNK = 4 * 1024 * 1024  # read size when hashing a file already on disk

# Auth session lives in ~/.aqueduct; manifest and download/ stay in the working dir.
AUTH_STATE_PATH = paths.AUTH_STATE_PATH

log = logging.getLogger("filecopy")


# --------------------------------------------------------------------------- #
# Session / auth - reuse webenum's saved web session (SharePoint cookies)
# --------------------------------------------------------------------------- #
def _load_spo_cookies() -> httpx.Cookies:
    """Pull the SharePoint cookies out of the saved Playwright session.

    Only *.sharepoint.com cookies matter for downloading (FedAuth is host-scoped;
    rtFa/SIMI are on .sharepoint.com); the login.microsoftonline.com cookies are
    for the sign-in redirect dance and are not sent to the SPO host.
    """
    if not AUTH_STATE_PATH.exists():
        log.error("No %s. Run 'login <share-url>' first.", AUTH_STATE_PATH.name)
        raise SystemExit(2)
    state = json.loads(AUTH_STATE_PATH.read_text(encoding="utf-8"))
    jar = httpx.Cookies()
    n = 0
    for c in state.get("cookies", []):
        if "sharepoint.com" in c["domain"]:
            jar.set(c["name"], c["value"], domain=c["domain"], path=c.get("path", "/"))
            n += 1
    if not n:
        log.error("No sharepoint.com cookies in %s - session may be wrong/expired.", AUTH_STATE_PATH.name)
        raise SystemExit(2)
    log.info("Loaded %d SharePoint session cookie(s) from %s.", n, AUTH_STATE_PATH.name)
    return jar


def _download_url(web_url: str, file_ref: str) -> str:
    # download.aspx serves the raw bytes AND honors Range (unlike .../$value).
    return f"{web_url}/_layouts/15/download.aspx?SourceUrl={quote(file_ref, safe='')}"


def _sha256_file(path: Path) -> str:
    """SHA-256 (hex) of a file already on disk, read in chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(_HASH_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def _feed_file(hasher: hashlib._Hash, path: Path) -> None:
    """Feed an existing file's bytes into `hasher` (to seed a resumed download)."""
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(_HASH_CHUNK), b""):
            hasher.update(block)


# --------------------------------------------------------------------------- #
# One file: streaming download with byte-accurate resume
# --------------------------------------------------------------------------- #
class Result:
    __slots__ = ("path", "status", "size", "attempts", "seconds", "detail", "sha256")

    def __init__(self, path, status, size, attempts, seconds, detail="", sha256=""):
        self.path = path
        self.status = status      # "ok" | "skip" | "fail"
        self.size = size          # bytes on disk at end
        self.attempts = attempts
        self.seconds = seconds
        self.detail = detail
        self.sha256 = sha256      # SHA-256 (hex) of the final file, or "" if not hashed


@dataclass
class _Ctx:
    """Run-wide state shared by every download_one() task (keeps arg counts sane)."""

    client: httpx.AsyncClient
    sem: asyncio.Semaphore            # caps concurrent downloads (network-bound)
    web_url: str
    dest: Path
    max_retries: int
    chunk: int
    counter: dict
    hash_enabled: bool
    hash_sem: asyncio.Semaphore                 # caps concurrent on-disk hashing (disk-bound)
    prior_hashes: dict[tuple[str, int], str]    # (path, size) -> sha256 from a prior run


async def _stream_to_part(
    client: httpx.AsyncClient, url: str, part: Path, expected: int, chunk: int, hash_enabled: bool
) -> tuple[int, str | None]:
    """Download `url` into `part`, resuming from whatever is already there.

    Returns (final size, sha256-hex-or-None). SHA-256 is computed inline as bytes
    stream from the network (free — no extra disk read); on a resume the existing
    `.part` prefix is read once to seed the hasher. Raises on transport/HTTP errors
    so the caller's retry loop can re-enter (and resume from the larger .part).
    """
    existing = part.stat().st_size if part.exists() else 0
    if existing > expected:  # oversized/corrupt partial - start clean
        existing = 0
    hasher = hashlib.sha256() if hash_enabled else None
    headers = {"Range": f"bytes={existing}-"} if existing else {}

    async with client.stream("GET", url, headers=headers) as resp:
        # 401/403 => the saved session is no longer authorized; don't burn retries.
        if resp.status_code in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
            await resp.aread()
            raise PermissionError(f"{resp.status_code} - session expired? re-run login")
        if resp.status_code == HTTPStatus.RANGE_NOT_SATISFIABLE:  # .part already complete
            await resp.aread()
            digest = await asyncio.to_thread(_sha256_file, part) if hash_enabled else None
            return existing, digest
        resp.raise_for_status()

        # If we asked to resume but the server ignored Range (200, not 206),
        # it's sending the whole file from byte 0 - so overwrite, don't append.
        resume = existing and resp.status_code == HTTPStatus.PARTIAL_CONTENT
        if resume and hasher is not None:
            # Seed the hash with the bytes already on disk (one read of the partial).
            await asyncio.to_thread(_feed_file, hasher, part)
        mode = "ab" if resume else "wb"
        with open(part, mode) as fh:
            async for block in resp.aiter_bytes(chunk):
                fh.write(block)
                if hasher is not None:
                    hasher.update(block)
    return part.stat().st_size, (hasher.hexdigest() if hasher is not None else None)


async def _hash_present_file(ctx: _Ctx, rel: str, expected: int, target: Path) -> str:
    """SHA-256 for a file already complete on disk: reuse a prior run's digest, else
    read it once under the disk-hash semaphore (disk-bound, so kept to a small pool)."""
    if not ctx.hash_enabled:
        return ""
    sha = ctx.prior_hashes.get((rel, expected), "")
    if not sha:
        async with ctx.hash_sem:
            sha = await asyncio.to_thread(_sha256_file, target)
    return sha


async def download_one(ctx: _Ctx, item: dict) -> Result:
    rel = item["path"]
    expected = item["size"]
    target = ctx.dest / rel
    target.parent.mkdir(parents=True, exist_ok=True)

    # Already have a complete copy? (resume across runs) — reuse/compute its hash.
    if target.exists() and target.stat().st_size == expected:
        sha = await _hash_present_file(ctx, rel, expected, target)
        log.info("skip  %s (have %s B) sha256=%s", rel, f"{expected:,}", sha or "-")
        return Result(rel, "skip", expected, 0, 0.0, sha256=sha)

    part = target.with_suffix(target.suffix + ".part")
    url = _download_url(ctx.web_url, item["fileRef"])
    started = time.monotonic()

    async with ctx.sem:  # cap concurrent in-flight downloads (bandwidth/throughput knob)
        for attempt in range(1, ctx.max_retries + 2):  # 1 try + max_retries
            try:
                got, digest = await _stream_to_part(
                    ctx.client, url, part, expected, ctx.chunk, ctx.hash_enabled
                )
                if got != expected:
                    raise OSError(f"size mismatch: got {got:,}, expected {expected:,}")
                part.replace(target)
                secs = time.monotonic() - started
                ctx.counter["done_bytes"] += expected
                mbps = (expected / 1e6 / secs) if secs > 0 else 0.0
                log.info(
                    "ok    [%d/%d] %s (%s B, try %d, %.1fs, %.1f MB/s) sha256=%s",
                    ctx.counter["done"] + 1, ctx.counter["total"], rel, f"{expected:,}",
                    attempt, secs, mbps, digest or "-",
                )
                ctx.counter["done"] += 1
                return Result(rel, "ok", expected, attempt, secs, sha256=digest or "")
            except PermissionError as exc:  # auth failure - no point retrying
                log.error("FAIL  %s - %s", rel, exc)
                return Result(rel, "fail", part.stat().st_size if part.exists() else 0,
                              attempt, time.monotonic() - started, str(exc))
            except (TimeoutError, OSError, httpx.HTTPStatusError, httpx.TransportError) as exc:
                if attempt >= ctx.max_retries + 1:
                    log.error("FAIL  %s - %s (after %d tries)", rel, exc, attempt)
                    return Result(rel, "fail", part.stat().st_size if part.exists() else 0,
                                  attempt, time.monotonic() - started, str(exc))
                backoff = min(2 ** (attempt - 1), 30)
                log.warning("retry %s - %s (try %d/%d; %ds)", rel, exc,
                            attempt, ctx.max_retries + 1, backoff)
                await asyncio.sleep(backoff)
    # unreachable
    return Result(rel, "fail", 0, 0, 0.0, "logic error")


_RESULT_COLUMNS = ["path", "status", "size_bytes", "attempts", "seconds", "sha256", "detail"]
_FLUSH_EVERY = 100    # checkpoint the results CSV every N completed files, and...
_FLUSH_SECONDS = 30   # ...at least this often (so the slow big-file phase still persists)


def _load_prior_rows(results_path: Path) -> dict[str, list]:
    """A prior run's result rows, keyed by path, so an interrupted/resumed run keeps
    finished rows (and their SHA-256s) rather than starting the record over."""
    rows: dict[str, list] = {}
    if not results_path.exists():
        return rows
    try:
        with open(results_path, encoding="utf-8-sig", newline="") as fh:
            reader = csv.reader(fh)
            if next(reader, None) != _RESULT_COLUMNS:
                return rows  # older/other schema: don't trust it
            for row in reader:
                if row:
                    rows[row[0]] = row
    except (OSError, csv.Error):
        pass
    return rows


def _prior_hashes(prior_rows: dict[str, list]) -> dict[tuple[str, int], str]:
    """Derive (path, size) -> sha256 from prior rows, to skip re-hashing finished files."""
    out: dict[tuple[str, int], str] = {}
    for path, _status, size, _att, _sec, sha, _detail in prior_rows.values():
        if sha and size.isdigit():
            out[(path, int(size))] = sha
    return out


def _row_values(r: Result) -> list:
    return [r.path, r.status, r.size, r.attempts, f"{r.seconds:.1f}", r.sha256, r.detail]


def _write_results(results_path: Path, table: dict[str, list]) -> None:
    """Atomically rewrite the results CSV (temp + replace), failures first then path."""
    tmp = results_path.with_suffix(results_path.suffix + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(_RESULT_COLUMNS)
        for row in sorted(table.values(), key=lambda row: (row[1] != "fail", row[0])):
            w.writerow(row)
    tmp.replace(results_path)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
async def run(manifest: dict, dest: Path, concurrency: int, retries: int, chunk: int,
              limit: int | None, results_path: Path, hash_enabled: bool, hash_workers: int) -> int:
    web_url = manifest["root"]["webUrl"]
    host = urlparse(web_url).netloc
    files = [i for i in manifest["items"] if i["type"] == "file"]
    files.sort(key=lambda i: i["size"])  # smallest first: quick wins + fast smoke test
    if limit:
        files = files[:limit]
    total_bytes = sum(i["size"] for i in files)
    counter = {"total": len(files), "done": 0, "done_bytes": 0}
    prior_rows = _load_prior_rows(results_path)
    prior_hashes = _prior_hashes(prior_rows) if hash_enabled else {}
    table: dict[str, list] = dict(prior_rows)

    log.info("Target host: %s", host)
    log.info("Downloading %d files (%s bytes) -> %s  [concurrency=%d, retries=%d, hash=%s]",
             len(files), f"{total_bytes:,}", dest, concurrency, retries,
             f"sha256 (reusing {len(prior_hashes)} prior)" if hash_enabled else "off")

    # Generous read timeout: it's the gap *between* chunks, not the whole file.
    timeout = httpx.Timeout(connect=30.0, read=120.0, write=120.0, pool=None)
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    # Results are written incrementally (atomic temp+replace), checkpointed every
    # _FLUSH_EVERY files and once more in `finally`, so an interruption keeps finished
    # rows and their SHA-256s instead of losing the whole run's work.
    results: list[Result] = []
    async with httpx.AsyncClient(
        cookies=_load_spo_cookies(), timeout=timeout, limits=limits,
        follow_redirects=True, headers={"User-Agent": "filecopy/0.1"},
    ) as client:
        ctx = _Ctx(
            client=client, sem=asyncio.Semaphore(concurrency), web_url=web_url, dest=dest,
            max_retries=retries, chunk=chunk, counter=counter, hash_enabled=hash_enabled,
            hash_sem=asyncio.Semaphore(hash_workers), prior_hashes=prior_hashes,
        )
        tasks = [asyncio.ensure_future(download_one(ctx, item)) for item in files]
        last_flush = time.monotonic()
        try:
            for done, fut in enumerate(asyncio.as_completed(tasks), 1):
                r = await fut
                results.append(r)
                table[r.path] = _row_values(r)
                now = time.monotonic()
                if done % _FLUSH_EVERY == 0 or now - last_flush >= _FLUSH_SECONDS:
                    _write_results(results_path, table)
                    last_flush = now
        finally:
            _write_results(results_path, table)

    ok = sum(r.status == "ok" for r in results)
    skip = sum(r.status == "skip" for r in results)
    fail = sum(r.status == "fail" for r in results)
    hashed = sum(1 for r in results if r.sha256)
    log.info("-" * 60)
    log.info("DONE  ok=%d  skip=%d  fail=%d  hashed=%d  (%s of %s bytes new)",
             ok, skip, fail, hashed, f"{counter['done_bytes']:,}", f"{total_bytes:,}")
    log.info("Per-file results: %s", results_path)
    if fail:
        log.error("RESULT: FAIL - %d file(s) did not download. Re-run to resume "
                  "(completed files are skipped).", fail)
    else:
        log.info("RESULT: PASS - every targeted file is present at its manifest size.")
    return 1 if fail else 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _setup_logging(log_path: Path) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(sh)
    root.addHandler(fh)
    # httpx logs a line per request (full URL, i.e. every file path) at INFO -
    # far too noisy for thousands of files; keep only its warnings/errors.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def main() -> int:
    ap = argparse.ArgumentParser(description="High-throughput resumable downloader for a web-only share.")
    ap.add_argument("-m", "--manifest", default="manifest.json")
    ap.add_argument("-d", "--dest", default="download")
    ap.add_argument("-c", "--concurrency", type=int, default=4,
                    help="max simultaneous downloads (Semaphore); the throughput/bandwidth knob (default 4)")
    ap.add_argument("--retries", type=int, default=2,
                    help="retries after the first attempt (default 2 => 3 tries total)")
    ap.add_argument("--chunk-mb", type=float, default=4.0, help="stream chunk size in MB (default 4)")
    ap.add_argument("--limit", type=int, default=None,
                    help="only the N smallest files (smoke test)")
    ap.add_argument("--no-hash", action="store_true",
                    help="skip SHA-256 hashing (no integrity fingerprint recorded)")
    ap.add_argument("--hash-workers", type=int, default=3,
                    help="concurrent on-disk hashing of already-present files (disk-bound; default 3)")
    ap.add_argument("--log", default="filecopy.log", help="log file path (default filecopy.log)")
    ap.add_argument("--results", default="filecopy_results.csv",
                    help="per-file results CSV, incl. SHA-256 (default filecopy_results.csv)")
    args = ap.parse_args()

    _setup_logging(Path(args.log))
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    try:
        return asyncio.run(run(
            manifest, dest, args.concurrency, args.retries,
            int(args.chunk_mb * 1024 * 1024), args.limit, Path(args.results),
            not args.no_hash, args.hash_workers,
        ))
    except KeyboardInterrupt:
        log.warning("Interrupted - partial .part files are kept; re-run to resume.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
