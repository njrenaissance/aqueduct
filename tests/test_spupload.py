"""Tests for the SharePoint upload engine (validated local download -> SharePoint, QuickXorHash-verified).

Engine tests follow the sibling ``courier`` project's upload tests (ADR-0012); the Graph HTTP boundary is
mocked, so nothing touches the network.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import logging
from pathlib import Path

import httpx
import pytest

from aqueduct import spupload as up
from aqueduct.errors import IntegrityError, UploadError
from aqueduct.graphclient import DriveItemMeta, FolderRef
from aqueduct.quickxorhash import hash_file

pytestmark = pytest.mark.unit

_DEST = FolderRef("drive-1", "dest-item")
_BASE = FolderRef("drive-1", "base")
_CHUNK_ALIGN = 320 * 1024
_FAKE_SECRET = "not-a-real-secret-value"  # fictional placeholder


_PAYLOAD = b"helloworld"
_RUN_PAYLOAD = b"shared-bytes"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _entry(path: str = "A/B/f.pdf", payload: bytes = _PAYLOAD) -> up.UploadEntry:
    """An entry whose validated size and SHA-256 describe ``payload`` (what ``validate`` would have recorded)."""
    return up.UploadEntry.from_manifest_path(path, len(payload), _sha256(payload))


def _sized_entry(path: str, size: int) -> up.UploadEntry:
    return up.UploadEntry.from_manifest_path(path, size, "")


def _write_source(root: Path, entry: up.UploadEntry, payload: bytes) -> Path:
    source = entry.local_path(root)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(payload)
    return source


def _meta(size: int, quick_xor_hash: str | None) -> DriveItemMeta:
    return DriveItemMeta("f.pdf", size, quick_xor_hash, None, "item", "drive")


def _req() -> httpx.Request:
    return httpx.Request("PUT", "http://upload")


# --- entries ------------------------------------------------------------------


def test_entry_splits_manifest_path_into_destination_and_filename() -> None:
    entry = _sized_entry("Exhibits/GJ 1/a.pdf", 3)
    assert (entry.filename, entry.destination, entry.size) == ("a.pdf", "Exhibits/GJ 1", 3)


def test_entry_at_root_has_empty_destination() -> None:
    assert _sized_entry("a.pdf", 1).destination == ""


def test_entry_normalizes_backslashes() -> None:
    assert _sized_entry("Exhibits\\a.pdf", 1).destination == "Exhibits"


# --- gating: only files that passed `validate --hash` -------------------------


def _manifest(*paths_sizes: tuple[str, int]) -> dict:
    items = [{"path": p, "type": "file", "size": s} for p, s in paths_sizes]
    items.append({"path": "SomeFolder", "type": "folder", "size": 0})
    return {"items": items}


def _write_validate_csv(path: Path, rows: list[dict], *, provenance: bool = True) -> None:
    columns = ["path", "status", "expected_bytes", "actual_bytes", "sha256", "hash_check", "segment_check"]
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        if provenance:
            fh.write("# aqueduct acquisition (provenance; full run metadata in .metadata.json sidecar)\r\n")
            fh.write("# tool: validate 0.1.0\r\n")
        writer = csv.DictWriter(fh, fieldnames=columns, restval="")
        writer.writeheader()
        writer.writerows(rows)


def _row(
    path: str, status: str = "ok", sha256: str = "a" * 64, hash_check: str = "ok", segment_check: str = ""
) -> dict:
    return {"path": path, "status": status, "sha256": sha256, "hash_check": hash_check, "segment_check": segment_check}


def test_load_entries_only_returns_validated_files(tmp_path: Path) -> None:
    results = tmp_path / "validate_results.csv"
    _write_validate_csv(results, [_row("a.pdf"), _row("b.pdf", hash_check="ok")])

    eligible, rejected = up.load_entries(_manifest(("a.pdf", 1), ("b.pdf", 2)), results)

    assert [e.path for e in eligible] == ["a.pdf", "b.pdf"]
    assert rejected == []


def test_load_entries_carries_the_validated_sha256(tmp_path: Path) -> None:
    results = tmp_path / "validate_results.csv"
    _write_validate_csv(results, [_row("a.pdf", sha256="b" * 64)])

    eligible, _ = up.load_entries(_manifest(("a.pdf", 1)), results)

    assert eligible[0].sha256 == "b" * 64


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        pytest.param(_row("a.pdf", status="missing"), "not validated", id="missing_on_disk"),
        pytest.param(_row("a.pdf", status="mismatch"), "not validated", id="size_mismatch"),
        pytest.param(_row("a.pdf", sha256="", hash_check=""), "no SHA-256", id="validated_without_hash"),
        pytest.param(_row("a.pdf", hash_check="mismatch"), "hash mismatch", id="hash_mismatch"),
        pytest.param(_row("a.pdf", segment_check="mismatch"), "segment mismatch", id="segment_mismatch"),
    ],
)
def test_load_entries_rejects_files_that_did_not_pass(tmp_path: Path, row: dict, reason: str) -> None:
    results = tmp_path / "validate_results.csv"
    _write_validate_csv(results, [row])

    eligible, rejected = up.load_entries(_manifest(("a.pdf", 1)), results)

    assert eligible == []
    assert [(e.path, why) for e, why in rejected] == [("a.pdf", reason)]


def test_load_entries_rejects_manifest_files_absent_from_validate_results(tmp_path: Path) -> None:
    results = tmp_path / "validate_results.csv"
    _write_validate_csv(results, [_row("a.pdf")])

    eligible, rejected = up.load_entries(_manifest(("a.pdf", 1), ("b.pdf", 2)), results)

    assert [e.path for e in eligible] == ["a.pdf"]
    assert [(e.path, why) for e, why in rejected] == [("b.pdf", "not validated")]


def test_load_entries_ignores_folders_and_extras(tmp_path: Path) -> None:
    results = tmp_path / "validate_results.csv"
    _write_validate_csv(results, [_row("a.pdf"), _row("stray.txt", status="extra")])

    eligible, rejected = up.load_entries(_manifest(("a.pdf", 1)), results)

    assert [e.path for e in eligible] == ["a.pdf"]
    assert rejected == []


def test_load_entries_reads_results_without_provenance_header(tmp_path: Path) -> None:
    results = tmp_path / "validate_results.csv"
    _write_validate_csv(results, [_row("a.pdf")], provenance=False)

    eligible, _ = up.load_entries(_manifest(("a.pdf", 1)), results)

    assert [e.path for e in eligible] == ["a.pdf"]


def test_load_entries_requires_validate_results(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="validate"):
        up.load_entries(_manifest(("a.pdf", 1)), tmp_path / "missing.csv")


# --- command line: destination is a URL or site + library, never both ----------

_URL = "https://contoso.sharepoint.com/sites/Review/Shared%20Documents/Case%2012"
_SITE = "https://contoso.sharepoint.com/sites/Review"


def test_parse_args_accepts_dest_url() -> None:
    args = up.parse_args(["--dest-url", _URL])
    assert args.dest_url == _URL


def test_parse_args_accepts_site_and_library_with_optional_folder() -> None:
    args = up.parse_args(["--site-url", _SITE, "--library", "Discovery", "--target-folder", "Case 12"])
    assert (args.site_url, args.library, args.target_folder) == (_SITE, "Discovery", "Case 12")
    assert up.parse_args(["--site-url", _SITE, "--library", "Discovery"]).target_folder == ""


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param([], id="no_destination"),
        pytest.param(["--dest-url", _URL, "--site-url", _SITE, "--library", "Discovery"], id="both_forms"),
        pytest.param(["--dest-url", _URL, "--target-folder", "x"], id="url_plus_folder"),
        pytest.param(["--site-url", _SITE], id="site_without_library"),
        pytest.param(["--library", "Discovery"], id="library_without_site"),
    ],
)
def test_parse_args_rejects_bad_destination_combinations(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        up.parse_args(argv)
    assert excinfo.value.code == 2


def test_resolve_target_uses_url_form_when_given(mocker) -> None:
    graph = mocker.Mock()
    graph.resolve_destination_url = mocker.AsyncMock(return_value=_BASE)
    graph.resolve_destination = mocker.AsyncMock()

    ref = asyncio.run(up.resolve_target(graph, up.parse_args(["--dest-url", _URL])))

    assert ref == _BASE
    graph.resolve_destination_url.assert_awaited_once_with(_URL)
    graph.resolve_destination.assert_not_awaited()


def test_resolve_target_uses_site_form_when_given(mocker) -> None:
    graph = mocker.Mock()
    graph.resolve_destination_url = mocker.AsyncMock()
    graph.resolve_destination = mocker.AsyncMock(return_value=_BASE)
    args = up.parse_args(["--site-url", _SITE, "--library", "Discovery", "--target-folder", "Case 12"])

    ref = asyncio.run(up.resolve_target(graph, args))

    assert ref == _BASE
    graph.resolve_destination.assert_awaited_once_with(_SITE, "Discovery", "Case 12")
    graph.resolve_destination_url.assert_not_awaited()


# --- screening: size limit and blocked extensions -----------------------------


def test_screen_skips_blocked_extension_and_oversize() -> None:
    entries = [_sized_entry("ok.pdf", 5), _sized_entry("run.EXE", 5), _sized_entry("huge.bin", 500)]

    uploadable, skipped = up.screen_entries(entries, max_bytes=100, blocked_ext=(".exe",))

    assert [e.path for e in uploadable] == ["ok.pdf"]
    assert {e.path: why for e, why in skipped} == {"run.EXE": "blocked extension (.exe)", "huge.bin": "over size limit"}


def test_screen_passes_everything_with_no_rules() -> None:
    uploadable, skipped = up.screen_entries([_sized_entry("a.exe", 5)], max_bytes=100, blocked_ext=())
    assert len(uploadable) == 1
    assert skipped == []


# --- chunking + verification (ported from courier) ----------------------------


@pytest.mark.parametrize(
    ("chunk", "expected"),
    [
        pytest.param(_CHUNK_ALIGN * 4, _CHUNK_ALIGN * 4, id="already_aligned"),
        pytest.param(_CHUNK_ALIGN * 4 + 1, _CHUNK_ALIGN * 4, id="rounds_down"),
        pytest.param(1, _CHUNK_ALIGN, id="floor_is_one_block"),
    ],
)
def test_aligned_chunk(chunk: int, expected: int) -> None:
    assert up._aligned_chunk(chunk) == expected


def test_verify_passes_on_match_and_reports_hash_checked() -> None:
    assert up._verify(_meta(10, "HASH"), 10, "HASH", "f.pdf") is True


def test_verify_passes_when_server_reports_no_hash_but_says_so() -> None:
    assert up._verify(_meta(10, None), 10, "HASH", "f.pdf") is False  # size-only, like courier


def test_verify_rejects_missing_item() -> None:
    with pytest.raises(IntegrityError, match="not found"):
        up._verify(None, 10, "HASH", "f.pdf")


def test_verify_rejects_size_mismatch() -> None:
    with pytest.raises(IntegrityError, match="size mismatch"):
        up._verify(_meta(9, "HASH"), 10, "HASH", "f.pdf")


def test_verify_rejects_reported_hash_mismatch() -> None:
    with pytest.raises(IntegrityError, match="QuickXorHash mismatch"):
        up._verify(_meta(10, "OTHER"), 10, "HASH", "f.pdf")


@pytest.mark.parametrize(
    ("header", "seconds"),
    [pytest.param("7", 7, id="retry_after_header")],
)
def test_backoff_honors_retry_after_on_429(header: str, seconds: int) -> None:
    response = httpx.Response(429, headers={"Retry-After": header}, request=_req())
    exc = httpx.HTTPStatusError("throttled", request=_req(), response=response)
    assert up._backoff_seconds(exc, 1) == seconds


def test_backoff_is_exponential_and_capped_for_transient_errors() -> None:
    exc = httpx.ConnectError("reset")
    assert [up._backoff_seconds(exc, n) for n in (1, 2, 3)] == [1, 2, 4]
    assert up._backoff_seconds(exc, 50) == up._MAX_BACKOFF


# --- upload sessions (ported from courier) ------------------------------------


class _FakeSessionClient:
    """Stands in for the httpx client during an upload session: a GET for the
    resume point, then a PUT per chunk returning the queued statuses."""

    def __init__(self, next_ranges: list[str], put_statuses: list[int]) -> None:
        self._next_ranges = next_ranges
        self._put_statuses = list(put_statuses)
        self.ranges_seen: list[str] = []
        self.headers_seen: list[dict] = []
        self.sent = b""

    async def get(self, _url: str) -> httpx.Response:
        return httpx.Response(200, json={"nextExpectedRanges": self._next_ranges}, request=_req())

    async def put(self, _url: str, content: bytes, headers: dict) -> httpx.Response:
        self.ranges_seen.append(headers["Content-Range"])
        self.headers_seen.append(headers)
        self.sent += content
        return httpx.Response(self._put_statuses.pop(0), json={}, request=_req())


def test_upload_via_session_chunks_and_finalizes(tmp_path: Path) -> None:
    source = tmp_path / "big.bin"
    source.write_bytes(b"abcde")
    client = _FakeSessionClient(next_ranges=["0-"], put_statuses=[202, 202, 201])

    asyncio.run(up._upload_via_session(client, "http://upload", source, size=5, chunk=2))

    assert client.ranges_seen == ["bytes 0-1/5", "bytes 2-3/5", "bytes 4-4/5"]
    assert client.sent == b"abcde"


def test_upload_via_session_resumes_from_next_expected(tmp_path: Path) -> None:
    source = tmp_path / "big.bin"
    source.write_bytes(b"abcde")
    client = _FakeSessionClient(next_ranges=["3-"], put_statuses=[201])

    asyncio.run(up._upload_via_session(client, "http://upload", source, size=5, chunk=2))

    assert client.ranges_seen == ["bytes 3-4/5"]


def test_session_chunks_carry_no_authorization_header(tmp_path: Path) -> None:
    source = tmp_path / "big.bin"
    source.write_bytes(b"abcde")
    client = _FakeSessionClient(next_ranges=["0-"], put_statuses=[202, 202, 201])

    asyncio.run(up._upload_via_session(client, "http://upload", source, size=5, chunk=2))

    assert all("authorization" not in {k.lower() for k in headers} for headers in client.headers_seen)


# --- upload_one ---------------------------------------------------------------


def _ctx_for(tmp_path: Path, graph, max_retries: int = 0, chunk: int = 1024, client=None) -> up._Ctx:
    return up._Ctx(
        client=client,
        graph=graph,
        sem=asyncio.Semaphore(1),
        hash_sem=asyncio.Semaphore(1),
        source_root=tmp_path,
        max_retries=max_retries,
        chunk=chunk,
        counter={"total": 1, "done": 0},
    )


def test_upload_one_small_file_single_put_and_verifies(tmp_path: Path, mocker) -> None:
    entry, payload = _entry(), b"helloworld"
    digest = hash_file(_write_source(tmp_path, entry, payload))
    graph = mocker.Mock()
    # Absent on the pre-upload skip check, present and matching on post-upload verify.
    graph.child_meta = mocker.AsyncMock(side_effect=[None, _meta(len(payload), digest)])
    graph.upload_small = mocker.AsyncMock(return_value=_meta(len(payload), digest))
    graph.create_upload_session = mocker.AsyncMock()

    result = asyncio.run(up.upload_one(_ctx_for(tmp_path, graph), entry, _DEST))

    assert result.status == "ok"
    assert result.quick_xor_hash == digest
    assert result.sha256 == _sha256(payload)
    graph.upload_small.assert_awaited_once()
    graph.create_upload_session.assert_not_awaited()


def test_upload_one_large_file_uses_upload_session(tmp_path: Path, mocker) -> None:
    payload = b"x" * 2048
    entry = _entry("big.bin", payload)
    digest = hash_file(_write_source(tmp_path, entry, payload))
    graph = mocker.Mock()
    graph.child_meta = mocker.AsyncMock(side_effect=[None, _meta(len(payload), digest)])
    graph.upload_small = mocker.AsyncMock()
    graph.create_upload_session = mocker.AsyncMock(return_value="http://upload/session")
    client = _FakeSessionClient(next_ranges=["0-"], put_statuses=[201])

    result = asyncio.run(up.upload_one(_ctx_for(tmp_path, graph, chunk=1024, client=client), entry, _DEST))

    assert result.status == "ok"
    graph.upload_small.assert_not_awaited()
    graph.create_upload_session.assert_awaited_once()
    assert client.sent == payload


def test_upload_one_skips_and_never_replaces_when_hash_matches(tmp_path: Path, mocker) -> None:
    entry, payload = _entry(), b"helloworld"
    digest = hash_file(_write_source(tmp_path, entry, payload))
    graph = mocker.Mock()
    graph.child_meta = mocker.AsyncMock(return_value=_meta(len(payload), digest))
    graph.upload_small = mocker.AsyncMock()
    graph.create_upload_session = mocker.AsyncMock()

    result = asyncio.run(up.upload_one(_ctx_for(tmp_path, graph), entry, _DEST))

    assert result.status == "skip"
    graph.upload_small.assert_not_awaited()
    graph.create_upload_session.assert_not_awaited()


def test_upload_one_replaces_mismatching_item_and_logs_it(tmp_path: Path, mocker, caplog) -> None:
    entry, payload = _entry(), b"helloworld"
    digest = hash_file(_write_source(tmp_path, entry, payload))
    graph = mocker.Mock()
    stale = _meta(99, "STALE")
    graph.child_meta = mocker.AsyncMock(side_effect=[stale, _meta(len(payload), digest)])
    graph.upload_small = mocker.AsyncMock(return_value=_meta(len(payload), digest))
    caplog.set_level(logging.INFO)

    result = asyncio.run(up.upload_one(_ctx_for(tmp_path, graph), entry, _DEST))

    assert result.status == "ok"
    graph.upload_small.assert_awaited_once()
    assert "replac" in caplog.text.lower()


def test_upload_one_accepts_size_only_when_server_hash_missing(tmp_path: Path, mocker, caplog) -> None:
    entry, payload = _entry(), b"helloworld"
    _write_source(tmp_path, entry, payload)
    graph = mocker.Mock()
    graph.child_meta = mocker.AsyncMock(side_effect=[None, _meta(len(payload), None)])
    graph.upload_small = mocker.AsyncMock()
    caplog.set_level(logging.WARNING)

    result = asyncio.run(up.upload_one(_ctx_for(tmp_path, graph), entry, _DEST))

    assert result.status == "size-only"  # never a plain "ok": the file was not hash-verified
    assert "hash" in caplog.text.lower()


def test_upload_one_rejects_a_file_that_changed_since_validate(tmp_path: Path, mocker) -> None:
    entry = _entry()  # validated as b"helloworld"
    _write_source(tmp_path, entry, b"tampered!!")
    graph = mocker.Mock()
    graph.child_meta = mocker.AsyncMock()
    graph.upload_small = mocker.AsyncMock()
    graph.create_upload_session = mocker.AsyncMock()

    result = asyncio.run(up.upload_one(_ctx_for(tmp_path, graph), entry, _DEST))

    assert result.status == "rejected"
    assert "changed since validate" in result.detail
    graph.child_meta.assert_not_awaited()
    graph.upload_small.assert_not_awaited()
    graph.create_upload_session.assert_not_awaited()


def test_hash_file_pair_returns_sha256_and_quickxorhash_from_one_read(tmp_path: Path, mocker) -> None:
    payload = bytes((i * 31 + 7) % 256 for i in range(10_000))
    source = tmp_path / "f.bin"
    source.write_bytes(payload)
    opened: list[object] = []
    real_open = open

    def counting_open(*args, **kwargs):
        opened.append(args[0])
        return real_open(*args, **kwargs)

    mocker.patch.object(up, "open", counting_open, create=True)

    sha256, qxh = up._hash_file_pair(source)

    assert sha256 == _sha256(payload)
    assert qxh == hash_file(source)
    assert len(opened) == 1


def test_upload_one_fails_when_source_missing(tmp_path: Path, mocker) -> None:
    graph = mocker.Mock()
    graph.child_meta = mocker.AsyncMock()

    result = asyncio.run(up.upload_one(_ctx_for(tmp_path, graph), _entry(), _DEST))

    assert result.status == "fail"
    assert "not found" in result.detail
    graph.child_meta.assert_not_awaited()


def test_upload_one_fails_on_persistent_error(tmp_path: Path, mocker) -> None:
    _write_source(tmp_path, _entry(), b"helloworld")
    graph = mocker.Mock()
    graph.child_meta = mocker.AsyncMock(return_value=None)
    graph.upload_small = mocker.AsyncMock(side_effect=UploadError("rejected"))

    result = asyncio.run(up.upload_one(_ctx_for(tmp_path, graph), _entry(), _DEST))

    assert result.status == "fail"
    assert "rejected" in result.detail


def test_upload_one_retries_transient_failure_then_succeeds(tmp_path: Path, mocker) -> None:
    entry, payload = _entry(), b"helloworld"
    digest = hash_file(_write_source(tmp_path, entry, payload))
    mocker.patch.object(up.asyncio, "sleep", mocker.AsyncMock())
    graph = mocker.Mock()
    graph.child_meta = mocker.AsyncMock(side_effect=[None, _meta(len(payload), digest)])
    graph.upload_small = mocker.AsyncMock(side_effect=[httpx.ConnectError("reset"), _meta(len(payload), digest)])

    result = asyncio.run(up.upload_one(_ctx_for(tmp_path, graph, max_retries=2), entry, _DEST))

    assert result.status == "ok"
    assert result.attempts == 2


def test_upload_one_fails_on_size_mismatch_after_upload(tmp_path: Path, mocker) -> None:
    entry, payload = _entry(), b"helloworld"
    digest = hash_file(_write_source(tmp_path, entry, payload))
    graph = mocker.Mock()
    graph.child_meta = mocker.AsyncMock(side_effect=[None, _meta(3, digest)])
    graph.upload_small = mocker.AsyncMock()

    result = asyncio.run(up.upload_one(_ctx_for(tmp_path, graph), entry, _DEST))

    assert result.status == "fail"
    assert "size mismatch" in result.detail


# --- run: folders, results CSV, provenance, vault-bypass flag ------------------


def _run_graph(mocker, digest: str, size: int):
    graph = mocker.Mock()
    graph.ensure_child_folder = mocker.AsyncMock(side_effect=lambda parent, name: FolderRef(parent.drive_id, name))
    seen: set[tuple[str, str]] = set()

    async def child_meta(dest: FolderRef, name: str) -> DriveItemMeta | None:
        key = (dest.item_id, name)
        if key in seen:  # post-upload verify: present and matching
            return _meta(size, digest)
        seen.add(key)  # pre-upload skip check: absent
        return None

    graph.child_meta = mocker.AsyncMock(side_effect=child_meta)
    graph.upload_small = mocker.AsyncMock(return_value=_meta(size, digest))
    return graph


def _run_entries() -> list[up.UploadEntry]:
    return [_entry("Exhibits/GJ 1/a.pdf", _RUN_PAYLOAD), _entry("Minutes/b.pdf", _RUN_PAYLOAD)]


def _ctx_run(tmp_path: Path, graph) -> up._Ctx:
    return up._Ctx(None, graph, asyncio.Semaphore(2), asyncio.Semaphore(2), tmp_path, 0, 1024, {"total": 2, "done": 0})


def _prepare_run(tmp_path: Path, mocker, payload: bytes = _RUN_PAYLOAD):
    for entry in _run_entries():
        _write_source(tmp_path, entry, payload)
    digest = hash_file(tmp_path / "Minutes" / "b.pdf")
    return _run_graph(mocker, digest, len(payload))


def _read_results(path: Path) -> tuple[list[str], list[dict]]:
    text = path.read_text(encoding="utf-8-sig")
    comments = [line for line in text.splitlines() if line.startswith("#")]
    body = [line for line in text.splitlines() if not line.startswith("#")]
    return comments, list(csv.DictReader(body))


def test_run_uploads_all_builds_folders_and_preserves_structure(tmp_path: Path, mocker) -> None:
    graph = _prepare_run(tmp_path, mocker)

    code = asyncio.run(up.run(_ctx_run(tmp_path, graph), _run_entries(), [], _BASE, tmp_path / "r.csv"))

    assert code == 0
    assert graph.upload_small.await_count == len(_run_entries())
    created = sorted(call.args[1] for call in graph.ensure_child_folder.await_args_list)
    assert created == ["Exhibits", "GJ 1", "Minutes"]


def test_run_reports_failure_with_exit_1(tmp_path: Path, mocker) -> None:
    graph = _prepare_run(tmp_path, mocker)
    graph.upload_small = mocker.AsyncMock(side_effect=UploadError("rejected"))

    code = asyncio.run(up.run(_ctx_run(tmp_path, graph), _run_entries(), [], _BASE, tmp_path / "r.csv"))

    assert code == 1


def test_run_rejected_entries_are_recorded_and_fail_the_run(tmp_path: Path, mocker) -> None:
    graph = _prepare_run(tmp_path, mocker)
    results = tmp_path / "r.csv"
    rejected = [(_entry("c.pdf"), "not validated")]

    code = asyncio.run(up.run(_ctx_run(tmp_path, graph), _run_entries(), rejected, _BASE, results))

    _, rows = _read_results(results)
    by_path = {r["path"]: r for r in rows}
    assert by_path["c.pdf"]["status"] == "rejected"
    assert by_path["c.pdf"]["detail"] == "not validated"
    assert code == 1


def test_run_skipped_entries_are_recorded_but_do_not_fail_the_run(tmp_path: Path, mocker) -> None:
    graph = _prepare_run(tmp_path, mocker)
    results = tmp_path / "r.csv"
    skipped = [(_entry("run.exe"), "blocked extension (.exe)")]

    code = asyncio.run(up.run(_ctx_run(tmp_path, graph), _run_entries(), [], _BASE, results, skipped=skipped))

    _, rows = _read_results(results)
    assert {r["path"]: r["status"] for r in rows}["run.exe"] == "skipped"
    assert code == 0


def test_run_writes_results_csv_with_provenance_and_vault_bypass(tmp_path: Path, mocker) -> None:
    graph = _prepare_run(tmp_path, mocker)
    results = tmp_path / "r.csv"

    asyncio.run(up.run(_ctx_run(tmp_path, graph), _run_entries(), [], _BASE, results))

    comments, rows = _read_results(results)
    assert any(line.startswith("# tool: spupload") for line in comments)
    assert any("vault" in line.lower() and "bypass" in line.lower() for line in comments)
    assert {r["path"] for r in rows} == {"Exhibits/GJ 1/a.pdf", "Minutes/b.pdf"}
    assert {"path", "status", "size", "sha256", "quick_xor_hash", "attempts", "detail"} <= set(rows[0])
    assert {r["sha256"] for r in rows} == {_sha256(_RUN_PAYLOAD)}
    sidecar = json.loads(Path(f"{results}.metadata.json").read_text(encoding="utf-8"))
    assert sidecar["tool"] == "spupload"
    assert sidecar["vault_bypassed"] is True


def test_run_warns_that_the_vault_is_bypassed(tmp_path: Path, mocker, caplog) -> None:
    graph = _prepare_run(tmp_path, mocker)
    caplog.set_level(logging.WARNING)

    asyncio.run(up.run(_ctx_run(tmp_path, graph), _run_entries(), [], _BASE, tmp_path / "r.csv"))

    warning = " ".join(r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING).lower()
    assert "vault" in warning
    assert "not evidence" in warning


def _graph_without_server_hash(mocker):
    graph = _prepare_run_graph_only(mocker)
    size = len(_RUN_PAYLOAD)
    seen: set[tuple[str, str]] = set()

    async def child_meta(dest: FolderRef, name: str) -> DriveItemMeta | None:
        key = (dest.item_id, name)
        if key in seen:  # post-upload verify: present, but SharePoint reports no hash
            return _meta(size, None)
        seen.add(key)  # pre-upload skip check: absent
        return None

    graph.child_meta = mocker.AsyncMock(side_effect=child_meta)
    return graph


def _prepare_run_graph_only(mocker):
    graph = mocker.Mock()
    graph.ensure_child_folder = mocker.AsyncMock(side_effect=lambda parent, name: FolderRef(parent.drive_id, name))
    graph.upload_small = mocker.AsyncMock(return_value=_meta(len(_RUN_PAYLOAD), None))
    return graph


def test_run_records_size_only_status_distinct_from_ok_and_does_not_fail(tmp_path: Path, mocker) -> None:
    for entry in _run_entries():
        _write_source(tmp_path, entry, _RUN_PAYLOAD)
    results = tmp_path / "r.csv"

    code = asyncio.run(
        up.run(_ctx_run(tmp_path, _graph_without_server_hash(mocker)), _run_entries(), [], _BASE, results)
    )

    _, rows = _read_results(results)
    assert code == 0
    assert {r["status"] for r in rows} == {"size-only"}


def test_run_summary_counts_size_only_separately(tmp_path: Path, mocker, caplog) -> None:
    for entry in _run_entries():
        _write_source(tmp_path, entry, _RUN_PAYLOAD)
    caplog.set_level(logging.INFO)

    asyncio.run(
        up.run(_ctx_run(tmp_path, _graph_without_server_hash(mocker)), _run_entries(), [], _BASE, tmp_path / "r.csv")
    )

    summary = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("DONE"))
    assert "ok=0" in summary
    assert "size-only=2" in summary


def test_run_rejects_a_file_changed_since_validate_and_fails_the_run(tmp_path: Path, mocker) -> None:
    graph = _prepare_run(tmp_path, mocker)
    (tmp_path / "Minutes" / "b.pdf").write_bytes(b"changed-after-validate")
    results = tmp_path / "r.csv"

    code = asyncio.run(up.run(_ctx_run(tmp_path, graph), _run_entries(), [], _BASE, results))

    _, rows = _read_results(results)
    by_path = {r["path"]: r for r in rows}
    assert by_path["Minutes/b.pdf"]["status"] == "rejected"
    assert by_path["Exhibits/GJ 1/a.pdf"]["status"] == "ok"
    assert code == 1


def test_run_output_never_contains_credentials(tmp_path: Path, mocker, caplog, capsys) -> None:
    graph = _prepare_run(tmp_path, mocker)
    results = tmp_path / "r.csv"
    caplog.set_level(logging.DEBUG)

    asyncio.run(up.run(_ctx_run(tmp_path, graph), _run_entries(), [], _BASE, results))

    everything = results.read_text(encoding="utf-8-sig") + Path(f"{results}.metadata.json").read_text(encoding="utf-8")
    everything += caplog.text + capsys.readouterr().out
    assert _FAKE_SECRET not in everything
    assert "Bearer" not in everything


def test_rerun_skips_already_uploaded_files(tmp_path: Path, mocker) -> None:
    for entry in _run_entries():
        _write_source(tmp_path, entry, _RUN_PAYLOAD)
    digest = hash_file(tmp_path / "Minutes" / "b.pdf")
    graph = mocker.Mock()
    graph.ensure_child_folder = mocker.AsyncMock(side_effect=lambda parent, name: FolderRef(parent.drive_id, name))
    graph.child_meta = mocker.AsyncMock(return_value=_meta(len(_RUN_PAYLOAD), digest))
    graph.upload_small = mocker.AsyncMock()
    results = tmp_path / "r.csv"

    code = asyncio.run(up.run(_ctx_run(tmp_path, graph), _run_entries(), [], _BASE, results))

    _, rows = _read_results(results)
    assert code == 0
    assert {r["status"] for r in rows} == {"skip"}
    graph.upload_small.assert_not_awaited()
