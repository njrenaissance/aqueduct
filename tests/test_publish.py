"""Tests for ``publish``: stream preserved files from the Azure Blob vault to SharePoint (ADR-0014).

The Azure SDK boundary is an in-memory fake container and the Graph boundary is a mocked ``GraphClient`` plus a
fake upload-session client, so nothing touches the network or writes evidence to disk.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import logging
import os
import tomllib
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from aqueduct import publish as pub
from aqueduct.graphclient import DriveItemMeta, FolderRef, GraphConfig
from aqueduct.quickxorhash import QuickXorHash
from aqueduct.validated import UploadEntry

pytestmark = pytest.mark.unit

_PREFIX = "2026-0042-smith/20261004-share-a"
_ACCOUNT = "https://contoso.blob.core.windows.net"
_DEST_URL = "https://contoso.sharepoint.com/sites/Review/Shared%20Documents/Case%2012"
_SITE = "https://contoso.sharepoint.com/sites/Review"
_DEST = FolderRef("drive-1", "dest-item")
_BASE = FolderRef("drive-1", "base")
_ALIGN = 320 * 1024
_GIB = 1024**3
_SP_LIMIT = 250 * 1024**3
_FAKE_SECRET = "not-a-real-secret-value"  # fictional placeholder
_PAYLOAD = b"helloworld"


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _qxh(payload: bytes) -> str:
    hasher = QuickXorHash()
    hasher.update(payload)
    return hasher.base64digest()


def _meta(size: int, quick_xor_hash: str | None) -> DriveItemMeta:
    return DriveItemMeta("f", size, quick_xor_hash, None, "item", "drive-1")


# --- fakes: the vault (Azure Blob) and the upload session (Graph) -----------------


class FakeVault:
    """An in-memory container: lists blobs with metadata and serves ranged reads, recording every read."""

    def __init__(self) -> None:
        self.blobs: dict[str, tuple[bytes, dict[str, str]]] = {}
        self.reads: list[tuple[str, int, int]] = []
        self.list_kwargs: dict = {}
        self.writes: list[str] = []

    def add(self, path: str, payload: bytes, *, sha256: str | None = None, uniqueid: str = "guid-1") -> str:
        name = f"{_PREFIX}/data/{path}"
        meta = {"sourcepath": path.replace(" ", "%20"), "uniqueid": uniqueid}
        if sha256 != "":
            meta["sha256"] = sha256 or _sha(payload)
        self.blobs[name] = (payload, meta)
        return name

    def list_blobs(self, name_starts_with: str | None = None, include=None):
        self.list_kwargs = {"name_starts_with": name_starts_with, "include": include}
        for name, (payload, meta) in self.blobs.items():
            if name_starts_with is None or name.startswith(name_starts_with):
                yield SimpleNamespace(name=name, size=len(payload), metadata=dict(meta))

    def get_blob_client(self, name: str) -> FakeBlobClient:
        return FakeBlobClient(self, name)

    def read_names(self) -> set[str]:
        return {name for name, _, _ in self.reads}


class FakeBlobClient:
    def __init__(self, vault: FakeVault, name: str) -> None:
        self._vault = vault
        self._name = name

    def download_blob(self, offset: int | None = None, length: int | None = None, **_kwargs) -> SimpleNamespace:
        payload = self._vault.blobs[self._name][0]
        start = offset or 0
        end = len(payload) if length is None else start + length
        self._vault.reads.append((self._name, start, end - start))
        return SimpleNamespace(readall=lambda: payload[start:end])

    def __getattr__(self, attr: str):  # any write call on the vault is a test failure
        self._vault.writes.append(attr)
        raise AssertionError(f"publish must not call {attr} on the vault")


class FakeSession:
    """A Graph upload session: accepts contiguous chunks, reports nextExpectedRanges, finalizes on the last byte.

    ``fail_puts`` holds the 0-based indexes of PUT calls that drop the connection instead of being accepted.
    """

    def __init__(self, fail_puts: tuple[int, ...] = ()) -> None:
        self.received = b""
        self.put_calls = 0
        self.ranges: list[str] = []
        self.headers: list[dict] = []
        self._fail = set(fail_puts)

    async def get(self, _url: str) -> httpx.Response:
        return httpx.Response(200, json={"nextExpectedRanges": [f"{len(self.received)}-"]}, request=_req())

    async def put(self, _url: str, content: bytes, headers: dict) -> httpx.Response:
        index = self.put_calls
        self.put_calls += 1
        self.ranges.append(headers["Content-Range"])
        self.headers.append(headers)
        if index in self._fail:
            raise httpx.ConnectError("connection reset")
        span, total = headers["Content-Range"].removeprefix("bytes ").split("/")
        start = int(span.split("-")[0])
        if start != len(self.received):
            return httpx.Response(416, json={}, request=_req())
        self.received += content
        status = 201 if len(self.received) == int(total) else 202
        return httpx.Response(status, json={}, request=_req())


def _req() -> httpx.Request:
    return httpx.Request("PUT", "https://upload.test/session")


def _graph(mocker, *child_meta):
    """A mocked GraphClient. ``child_meta`` is the sequence of lookups: pre-upload check, then post-upload verify."""
    graph = mocker.Mock()
    graph.child_meta = mocker.AsyncMock(side_effect=list(child_meta))
    graph.upload_small = mocker.AsyncMock(return_value=_meta(0, None))
    graph.create_upload_session = mocker.AsyncMock(return_value="https://upload.test/session")
    graph.ensure_child_folder = mocker.AsyncMock(side_effect=lambda parent, name: FolderRef(parent.drive_id, name))
    graph.delete_child = mocker.AsyncMock()
    return graph


def _ctx(vault: FakeVault, graph, *, session=None, retries: int = 2, chunk: int = _ALIGN, delete=False) -> pub.Ctx:
    return pub.Ctx(
        container=vault,
        graph=graph,
        client=session,
        sem=asyncio.Semaphore(2),
        max_retries=retries,
        chunk=chunk,
        delete_mismatched=delete,
    )


def _vault_blob(vault: FakeVault, path: str) -> pub.VaultBlob:
    """The VaultBlob for ``path`` as listing would describe it (built directly, so transfer tests stand alone)."""
    name = f"{_PREFIX}/data/{path}"
    payload, meta = vault.blobs[name]
    entry = UploadEntry.from_manifest_path(path, len(payload), meta.get("sha256", ""), meta.get("uniqueid", ""))
    return pub.VaultBlob(name, entry)


def _blob(path: str, size: int, sha256: str = "a" * 64) -> pub.VaultBlob:
    return pub.VaultBlob(f"{_PREFIX}/data/{path}", UploadEntry.from_manifest_path(path, size, sha256, "guid-1"))


@pytest.fixture(autouse=True)
def _no_sleep(mocker) -> None:
    mocker.patch.object(pub.asyncio, "sleep", mocker.AsyncMock())


# --- listing the vault -------------------------------------------------------------


def test_list_vault_reads_data_blobs_with_their_metadata() -> None:
    vault = FakeVault()
    vault.add("Exhibits/GJ 1/a.pdf", _PAYLOAD, uniqueid="guid-a")
    vault.blobs[f"{_PREFIX}/_audit/20261004T000000Z/manifest.json"] = (b"{}", {"sha256": _sha(b"{}")})

    blobs = pub.list_vault(vault, _PREFIX)

    assert [b.entry.path for b in blobs] == ["Exhibits/GJ 1/a.pdf"]
    blob = blobs[0]
    assert blob.name == f"{_PREFIX}/data/Exhibits/GJ 1/a.pdf"
    assert (blob.entry.size, blob.entry.sha256, blob.entry.item_id) == (len(_PAYLOAD), _sha(_PAYLOAD), "guid-a")
    assert (blob.entry.filename, blob.entry.destination) == ("a.pdf", "Exhibits/GJ 1")
    assert vault.list_kwargs["name_starts_with"] == f"{_PREFIX}/data/"
    assert "metadata" in vault.list_kwargs["include"]


def test_list_vault_falls_back_to_the_blob_name_without_sourcepath_metadata() -> None:
    vault = FakeVault()
    name = vault.add("Minutes/b.pdf", _PAYLOAD)
    del vault.blobs[name][1]["sourcepath"]

    assert [b.entry.path for b in pub.list_vault(vault, _PREFIX)] == ["Minutes/b.pdf"]


# --- storage policy: screened by name and size, before any read ---------------------


def test_default_policy_values() -> None:
    assert pub.DEFAULT_MAX_BYTES == _GIB
    expected_av = ".mp4 .m4v .mov .avi .mkv .wmv .flv .webm .mpg .mpeg .mp3 .wav .m4a .aac .flac .ogg .opus .wma .aiff"
    assert set(pub.AUDIOVISUAL_EXT) == set(expected_av.split())


@pytest.mark.parametrize(
    ("path", "size", "kwargs", "reason"),
    [
        pytest.param("big.pdf", 2 * _GIB, {}, "over size limit", id="over_default_max_bytes"),
        pytest.param("small.pdf", 101, {"max_bytes": 100}, "over size limit", id="over_explicit_max_bytes"),
        pytest.param("disk.iso", _SP_LIMIT + 1, {"max_bytes": 0}, "over size limit", id="over_sharepoint_limit"),
        pytest.param("clip.MP4", 10, {}, "audiovisual", id="video_any_case"),
        pytest.param("call.mp3", 10, {}, "audiovisual", id="audio"),
        pytest.param("tool.exe", 10, {"skip_ext": [".exe"]}, "excluded extension", id="skip_ext"),
        pytest.param("tool.EXE", 10, {"skip_ext": ["exe"]}, "excluded extension", id="skip_ext_without_dot"),
        pytest.param("huge.mp4", 2 * _GIB, {}, "over size limit", id="size_reason_wins_over_audiovisual"),
    ],
)
def test_screen_sets_aside_by_reason(path: str, size: int, kwargs: dict, reason: str) -> None:
    options = {"max_bytes": pub.DEFAULT_MAX_BYTES, "include_av": False, "skip_ext": [], **kwargs}
    keep = _blob("keep.pdf", 10)

    publishable, skipped = pub.screen_blobs([keep, _blob(path, size)], **options)

    assert publishable == [keep]
    assert [(b.entry.path, why) for b, why in skipped] == [(path, reason)]


def test_include_av_publishes_audiovisual() -> None:
    clip = _blob("clip.mp4", 10)

    publishable, skipped = pub.screen_blobs([clip], pub.DEFAULT_MAX_BYTES, include_av=True, skip_ext=[])

    assert publishable == [clip]
    assert skipped == []


@pytest.mark.parametrize(
    "max_bytes",
    [
        pytest.param(4 * _GIB, id="raised"),
        pytest.param(0, id="removed"),
        pytest.param(2 * _GIB, id="exactly_at_limit"),
    ],
)
def test_max_bytes_override(max_bytes: int) -> None:
    big = _blob("big.pdf", 2 * _GIB)

    publishable, skipped = pub.screen_blobs([big], max_bytes, include_av=False, skip_ext=[])

    assert publishable == [big]
    assert skipped == []


# --- transfer: small files in one PUT, large files through a session ---------------


def test_small_file_is_sent_in_one_put_and_verified(mocker) -> None:
    vault = FakeVault()
    vault.add("a.pdf", _PAYLOAD)
    graph = _graph(mocker, None, _meta(len(_PAYLOAD), _qxh(_PAYLOAD)))

    result = asyncio.run(pub.publish_one(_ctx(vault, graph), _vault_blob(vault, "a.pdf"), _DEST))

    assert result.status == "ok"
    assert (result.sha256, result.quick_xor_hash, result.uniqueid) == (_sha(_PAYLOAD), _qxh(_PAYLOAD), "guid-1")
    graph.upload_small.assert_awaited_once_with(_DEST, "a.pdf", _PAYLOAD)
    graph.create_upload_session.assert_not_awaited()


def test_large_file_streams_aligned_ranges_through_an_upload_session(mocker) -> None:
    payload = bytes(range(256)) * ((2 * _ALIGN + 100) // 256 + 1)
    vault = FakeVault()
    name = vault.add("big.bin", payload)
    graph = _graph(mocker, None, _meta(len(payload), _qxh(payload)))
    session = FakeSession()

    result = asyncio.run(pub.publish_one(_ctx(vault, graph, session=session), _vault_blob(vault, "big.bin"), _DEST))

    assert result.status == "ok"
    assert session.received == payload
    assert all(length % _ALIGN == 0 for _, _, length in vault.reads[:-1])
    assert [(n, o) for n, o, _ in vault.reads] == [(name, 0), (name, _ALIGN), (name, 2 * _ALIGN)]
    assert all("authorization" not in {k.lower() for k in h} for h in session.headers)
    graph.upload_small.assert_not_awaited()


def test_resume_interrupted_large_file_rereads_only_needed_range(mocker) -> None:
    payload = bytes((i * 7 + 3) % 256 for i in range(2 * _ALIGN + 100))
    vault = FakeVault()
    name = vault.add("big.bin", payload)
    graph = _graph(mocker, None, _meta(len(payload), _qxh(payload)))
    session = FakeSession(fail_puts=(1,))  # the second chunk's PUT drops the connection

    result = asyncio.run(pub.publish_one(_ctx(vault, graph, session=session), _vault_blob(vault, "big.bin"), _DEST))

    assert result.status == "ok"
    assert result.attempts == 2
    assert result.sha256 == _sha(payload)
    assert session.received == payload
    graph.create_upload_session.assert_awaited_once()  # resumed in the same session, not restarted
    assert vault.reads == [
        (name, 0, _ALIGN),
        (name, _ALIGN, _ALIGN),
        (name, _ALIGN, _ALIGN),  # the retry re-reads only the range the session still expects
        (name, 2 * _ALIGN, 100),
    ]


class ExpiringSession(FakeSession):
    """A session whose first chunk PUT finds it expired (404); a fresh session then accepts everything."""

    async def put(self, url: str, content: bytes, headers: dict) -> httpx.Response:
        if url.endswith("/expired"):
            self.put_calls += 1
            return httpx.Response(404, json={}, request=_req())
        return await super().put(url, content, headers)


def test_expired_upload_session_is_reopened_and_hashes_stay_correct(mocker) -> None:
    payload = bytes((i * 13 + 1) % 256 for i in range(_ALIGN + 50))
    vault = FakeVault()
    vault.add("big.bin", payload)
    graph = _graph(mocker, None, _meta(len(payload), _qxh(payload)))
    graph.create_upload_session = mocker.AsyncMock(
        side_effect=["https://upload.test/expired", "https://upload.test/new"]
    )
    session = ExpiringSession()

    result = asyncio.run(pub.publish_one(_ctx(vault, graph, session=session), _vault_blob(vault, "big.bin"), _DEST))

    assert result.status == "ok"
    assert result.sha256 == _sha(payload)
    assert session.received == payload
    assert graph.create_upload_session.await_count == 2


def test_empty_blob_is_published_in_one_put_without_a_ranged_read(mocker) -> None:
    vault = FakeVault()
    vault.add("empty.txt", b"")
    graph = _graph(mocker, None, _meta(0, _qxh(b"")))

    result = asyncio.run(pub.publish_one(_ctx(vault, graph), _vault_blob(vault, "empty.txt"), _DEST))

    assert result.status == "ok"
    graph.upload_small.assert_awaited_once_with(_DEST, "empty.txt", b"")
    assert vault.reads == []


# --- verification ------------------------------------------------------------------


def test_publish_fails_on_sharepoint_size_mismatch(mocker) -> None:
    vault = FakeVault()
    vault.add("a.pdf", _PAYLOAD)
    graph = _graph(mocker, None, _meta(3, _qxh(_PAYLOAD)))

    result = asyncio.run(pub.publish_one(_ctx(vault, graph), _vault_blob(vault, "a.pdf"), _DEST))

    assert result.status == "fail"
    assert "size mismatch" in result.detail


def test_publish_fails_on_sharepoint_quickxorhash_mismatch(mocker) -> None:
    vault = FakeVault()
    vault.add("a.pdf", _PAYLOAD)
    graph = _graph(mocker, None, _meta(len(_PAYLOAD), "OTHER"))

    result = asyncio.run(pub.publish_one(_ctx(vault, graph), _vault_blob(vault, "a.pdf"), _DEST))

    assert result.status == "fail"
    assert "QuickXorHash mismatch" in result.detail
    graph.delete_child.assert_not_awaited()


def test_publish_fails_when_streamed_sha256_differs_from_blob_metadata(mocker) -> None:
    payload = b"z" * (_ALIGN + 10)
    vault = FakeVault()
    vault.add("big.bin", payload, sha256=_sha(b"what was preserved"))
    graph = _graph(mocker, None, _meta(len(payload), _qxh(payload)))
    session = FakeSession()

    result = asyncio.run(pub.publish_one(_ctx(vault, graph, session=session), _vault_blob(vault, "big.bin"), _DEST))

    assert result.status == "fail"
    assert "SHA-256" in result.detail
    assert result.sha256 == _sha(b"what was preserved")  # the row carries the vault's recorded hash


def test_small_file_with_sha256_mismatch_is_not_uploaded(mocker) -> None:
    vault = FakeVault()
    vault.add("a.pdf", _PAYLOAD, sha256=_sha(b"something else"))
    graph = _graph(mocker, None, None)

    result = asyncio.run(pub.publish_one(_ctx(vault, graph), _vault_blob(vault, "a.pdf"), _DEST))

    assert result.status == "fail"
    assert "SHA-256" in result.detail
    graph.upload_small.assert_not_awaited()


def test_blob_without_recorded_sha256_fails_without_being_read_or_sent(mocker) -> None:
    vault = FakeVault()
    vault.add("a.pdf", _PAYLOAD, sha256="")
    graph = _graph(mocker, None, None)

    result = asyncio.run(pub.publish_one(_ctx(vault, graph), _vault_blob(vault, "a.pdf"), _DEST))

    assert result.status == "fail"
    assert "no SHA-256" in result.detail
    assert vault.reads == []
    graph.upload_small.assert_not_awaited()


def test_mismatch_deletes_item_when_requested(mocker) -> None:
    vault = FakeVault()
    vault.add("a.pdf", _PAYLOAD)
    graph = _graph(mocker, None, _meta(len(_PAYLOAD), "OTHER"))

    result = asyncio.run(pub.publish_one(_ctx(vault, graph, delete=True), _vault_blob(vault, "a.pdf"), _DEST))

    assert result.status == "fail"
    graph.delete_child.assert_awaited_once_with(_DEST, "a.pdf")
    assert "deleted" in result.detail


def test_publish_missing_server_hash_is_size_only(mocker, caplog) -> None:
    vault = FakeVault()
    vault.add("a.pdf", _PAYLOAD)
    graph = _graph(mocker, None, _meta(len(_PAYLOAD), None))
    caplog.set_level(logging.WARNING)

    result = asyncio.run(pub.publish_one(_ctx(vault, graph), _vault_blob(vault, "a.pdf"), _DEST))

    assert result.status == "size-only"  # never a plain "ok": SharePoint's copy was not hash-verified
    assert "hash" in caplog.text.lower()


def test_persistent_transport_failure_is_recorded_as_fail(mocker) -> None:
    vault = FakeVault()
    vault.add("a.pdf", _PAYLOAD)
    graph = _graph(mocker, None)
    graph.upload_small = mocker.AsyncMock(side_effect=httpx.ConnectError("reset"))

    result = asyncio.run(pub.publish_one(_ctx(vault, graph, retries=1), _vault_blob(vault, "a.pdf"), _DEST))

    assert result.status == "fail"
    assert result.attempts == 2
    assert "reset" in result.detail


# --- re-runs -----------------------------------------------------------------------


def test_rerun_skips_item_with_matching_size_and_quickxorhash(mocker) -> None:
    vault = FakeVault()
    vault.add("a.pdf", _PAYLOAD)
    graph = _graph(mocker, _meta(len(_PAYLOAD), _qxh(_PAYLOAD)))

    result = asyncio.run(pub.publish_one(_ctx(vault, graph), _vault_blob(vault, "a.pdf"), _DEST))

    assert result.status == "skip"
    graph.upload_small.assert_not_awaited()
    graph.create_upload_session.assert_not_awaited()


def test_existing_item_with_different_hash_is_replaced(mocker) -> None:
    vault = FakeVault()
    vault.add("a.pdf", _PAYLOAD)
    graph = _graph(mocker, _meta(len(_PAYLOAD), "STALE"), _meta(len(_PAYLOAD), _qxh(_PAYLOAD)))

    result = asyncio.run(pub.publish_one(_ctx(vault, graph), _vault_blob(vault, "a.pdf"), _DEST))

    assert result.status == "ok"
    graph.upload_small.assert_awaited_once()


def test_rerun_check_fails_a_vault_blob_that_no_longer_matches_its_recorded_sha256(mocker) -> None:
    vault = FakeVault()
    vault.add("a.pdf", _PAYLOAD, sha256=_sha(b"what was preserved"))
    graph = _graph(mocker, _meta(len(_PAYLOAD), _qxh(_PAYLOAD)))

    result = asyncio.run(pub.publish_one(_ctx(vault, graph), _vault_blob(vault, "a.pdf"), _DEST))

    assert result.status == "fail"
    assert "SHA-256" in result.detail
    graph.upload_small.assert_not_awaited()


# --- run: folders, results CSV and sidecar, exit code -------------------------------

_POLICY = {"max_bytes": _GIB, "include_av": False, "skip_ext": []}
_SOURCE = f"{_ACCOUNT}/vault/{_PREFIX}/data"


def _read_results(path: Path) -> tuple[list[str], list[dict]]:
    text = path.read_text(encoding="utf-8-sig")
    comments = [line for line in text.splitlines() if line.startswith("#")]
    body = [line for line in text.splitlines() if not line.startswith("#")]
    return comments, list(csv.DictReader(body))


def _verified_graph(mocker, payload: bytes):
    """Graph where every item is absent before upload and present + matching afterwards."""
    graph = _graph(mocker)
    seen: set[tuple[str, str]] = set()

    async def child_meta(dest: FolderRef, name: str) -> DriveItemMeta | None:
        key = (dest.item_id, name)
        if key in seen:
            return _meta(len(payload), _qxh(payload))
        seen.add(key)
        return None

    graph.child_meta = mocker.AsyncMock(side_effect=child_meta)
    return graph


def test_run_builds_folders_and_writes_results_csv_and_sidecar(tmp_path: Path, mocker) -> None:
    vault = FakeVault()
    vault.add("Exhibits/GJ 1/a.pdf", _PAYLOAD)
    vault.add("Minutes/b.pdf", _PAYLOAD)
    blobs = [_vault_blob(vault, "Exhibits/GJ 1/a.pdf"), _vault_blob(vault, "Minutes/b.pdf")]
    graph = _verified_graph(mocker, _PAYLOAD)
    results = tmp_path / "publish_results.csv"
    skipped = [(_blob("clip.mp4", 42, "c" * 64), "audiovisual")]

    code = asyncio.run(pub.run(_ctx(vault, graph), blobs, skipped, _BASE, results, _SOURCE, _POLICY))

    assert code == 0
    assert sorted(c.args[1] for c in graph.ensure_child_folder.await_args_list) == ["Exhibits", "GJ 1", "Minutes"]
    comments, rows = _read_results(results)
    assert any(line.startswith("# tool: publish") for line in comments)
    assert any("vault" in line and "read-only" in line for line in comments)
    assert any("review copy" in line for line in comments)
    assert not any("bypass" in line.lower() for line in comments)
    assert set(rows[0]) == {
        "path", "status", "size", "sha256", "quick_xor_hash", "uniqueid", "attempts", "seconds", "detail",
    }  # fmt: skip
    by_path = {r["path"]: r for r in rows}
    assert by_path["Minutes/b.pdf"]["status"] == "ok"
    expected_skip = {"status": "skipped", "detail": "audiovisual", "size": "42", "sha256": "c" * 64}
    assert expected_skip.items() <= by_path["clip.mp4"].items()
    sidecar = json.loads(Path(f"{results}.metadata.json").read_text(encoding="utf-8"))
    assert sidecar["tool"] == "publish"
    assert sidecar["vault_access"] == "read-only"
    assert sidecar["source"] == _SOURCE
    assert sidecar["policy"] == _POLICY


def test_run_failure_exits_1(tmp_path: Path, mocker) -> None:
    vault = FakeVault()
    vault.add("a.pdf", _PAYLOAD)
    graph = _graph(mocker, None, _meta(len(_PAYLOAD), "OTHER"))

    code = asyncio.run(
        pub.run(_ctx(vault, graph), [_vault_blob(vault, "a.pdf")], [], _BASE, tmp_path / "r.csv", _SOURCE, _POLICY)
    )

    assert code == 1


def test_run_summary_counts_every_status(tmp_path: Path, mocker, caplog) -> None:
    vault = FakeVault()
    vault.add("a.pdf", _PAYLOAD)
    graph = _verified_graph(mocker, _PAYLOAD)
    skipped = [(_blob("clip.mp4", 1), "audiovisual"), (_blob("big.pdf", 2 * _GIB), "over size limit")]
    caplog.set_level(logging.INFO)

    asyncio.run(
        pub.run(_ctx(vault, graph), [_vault_blob(vault, "a.pdf")], skipped, _BASE, tmp_path / "r.csv", _SOURCE, {})
    )

    summary = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("DONE"))
    for count in ("ok=1", "size-only=0", "skip=0", "fail=0", "skipped=2"):
        assert count in summary


# --- command line and end to end ------------------------------------------------------


def test_parse_args_defaults() -> None:
    args = pub.parse_args(["--dest-url", _DEST_URL])
    assert (args.max_bytes, args.include_av, args.skip_ext) == (pub.DEFAULT_MAX_BYTES, False, "")
    assert args.results == "publish_results.csv"
    assert args.delete_mismatched is False


def test_parse_args_accepts_site_form_and_policy_overrides() -> None:
    argv = ["--site-url", _SITE, "--library", "Discovery", "--target-folder", "Case 12"]
    args = pub.parse_args([*argv, "--max-bytes", "0", "--include-av", "--skip-ext", ".exe,.dll"])
    assert (args.site_url, args.library, args.target_folder) == (_SITE, "Discovery", "Case 12")
    assert (args.max_bytes, args.include_av, args.skip_ext) == (0, True, ".exe,.dll")


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param([], id="no_destination"),
        pytest.param(["--dest-url", _DEST_URL, "--site-url", _SITE, "--library", "D"], id="both_forms"),
        pytest.param(["--site-url", _SITE], id="site_without_library"),
    ],
)
def test_parse_args_rejects_bad_destinations(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        pub.parse_args(argv)
    assert excinfo.value.code == 2


def test_publish_entry_point_is_registered() -> None:
    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["scripts"]["publish"] == "aqueduct.publish:main"


@pytest.fixture
def end_to_end(tmp_path: Path, mocker, monkeypatch) -> SimpleNamespace:
    """``main()`` against a fake vault and a mocked Graph, run from an empty working directory."""
    monkeypatch.chdir(tmp_path)
    for var in ("AQUEDUCT_BLOB_ACCOUNT_URL", "AQUEDUCT_BLOB_CONTAINER", "AQUEDUCT_BLOB_PREFIX"):
        monkeypatch.delenv(var, raising=False)
    vault = FakeVault()
    vault.add("Exhibits/a.pdf", _PAYLOAD)
    vault.add("Exhibits/interview.mp4", b"video-bytes")
    vault.add("Exhibits/scan.tif", b"x" * 200)
    vault.add("Tools/setup.exe", b"binary")
    graph = _verified_graph(mocker, _PAYLOAD)
    graph.resolve_destination_url = mocker.AsyncMock(return_value=_BASE)
    mocker.patch.object(pub, "open_container", return_value=vault)
    mocker.patch.object(pub, "load_config", return_value=GraphConfig("tenant", "client", _FAKE_SECRET))
    mocker.patch.object(pub, "ClientCredentialsTokenProvider")
    mocker.patch.object(pub, "GraphClient", return_value=graph)
    argv = ["--account-url", _ACCOUNT, "--container", "vault", "--dest-prefix", _PREFIX, "--dest-url", _DEST_URL]
    argv += ["--max-bytes", "100", "--skip-ext", ".exe"]
    return SimpleNamespace(root=tmp_path, vault=vault, graph=graph, argv=argv)


def test_main_never_reads_or_uploads_skipped_blobs(end_to_end, caplog) -> None:
    caplog.set_level(logging.INFO)

    code = pub.main(end_to_end.argv)

    assert code == 0  # skipped is not a failure
    assert end_to_end.vault.read_names() == {f"{_PREFIX}/data/Exhibits/a.pdf"}
    looked_up = {c.args[1] for c in end_to_end.graph.child_meta.await_args_list}
    uploaded = {c.args[1] for c in end_to_end.graph.upload_small.await_args_list}
    assert looked_up == uploaded == {"a.pdf"}
    end_to_end.graph.create_upload_session.assert_not_awaited()
    _, rows = _read_results(end_to_end.root / "publish_results.csv")
    by_path = {r["path"]: r for r in rows}
    assert by_path["Exhibits/a.pdf"]["status"] == "ok"
    assert {p: (r["status"], r["detail"]) for p, r in by_path.items() if p != "Exhibits/a.pdf"} == {
        "Exhibits/interview.mp4": ("skipped", "audiovisual"),
        "Exhibits/scan.tif": ("skipped", "over size limit"),
        "Tools/setup.exe": ("skipped", "excluded extension"),
    }
    assert by_path["Exhibits/scan.tif"]["size"] == "200"
    assert by_path["Exhibits/scan.tif"]["sha256"] == _sha(b"x" * 200)
    summary = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("DONE"))
    assert "skipped=3" in summary


def test_main_leaves_only_results_csv_and_sidecar_on_disk(end_to_end, caplog, capsys) -> None:
    caplog.set_level(logging.DEBUG)

    pub.main(end_to_end.argv)

    assert sorted(os.listdir(end_to_end.root)) == ["publish_results.csv", "publish_results.csv.metadata.json"]
    assert end_to_end.vault.writes == []
    assert _FAKE_SECRET not in caplog.text + capsys.readouterr().out


def test_main_without_a_vault_destination_exits_2(end_to_end) -> None:
    argv = ["--dest-url", _DEST_URL]
    assert pub.main(argv) == 2


class FailingSession(FakeSession):
    """Every chunk PUT is rejected with a server error."""

    async def put(self, url: str, content: bytes, headers: dict) -> httpx.Response:
        self.put_calls += 1
        return httpx.Response(503, json={}, request=httpx.Request("PUT", url))


def test_session_failure_never_records_or_logs_the_pre_authenticated_upload_url(tmp_path: Path, mocker, caplog) -> None:
    secret_url = "https://upload.test/session?tempauth=not-a-real-token"
    payload = b"q" * (_ALIGN + 1)
    vault = FakeVault()
    vault.add("big.bin", payload)
    graph = _graph(mocker, None)
    graph.create_upload_session = mocker.AsyncMock(return_value=secret_url)
    results = tmp_path / "r.csv"
    caplog.set_level(logging.DEBUG)

    code = asyncio.run(
        pub.run(
            _ctx(vault, graph, session=FailingSession(), retries=1),
            [_vault_blob(vault, "big.bin")],
            [],
            _BASE,
            results,
            _SOURCE,
            _POLICY,
        )
    )

    assert code == 1
    _, rows = _read_results(results)
    assert rows[0]["status"] == "fail"
    assert "503" in rows[0]["detail"]
    assert "tempauth" not in results.read_text(encoding="utf-8-sig") + caplog.text


def test_main_network_failure_resolving_the_destination_exits_2(end_to_end, mocker) -> None:
    end_to_end.graph.resolve_destination_url = mocker.AsyncMock(side_effect=httpx.ConnectError("dns failure"))

    assert pub.main(end_to_end.argv) == 2
