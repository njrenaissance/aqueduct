"""Tests for the Azure Blob upload (Stage 2, Preserve): validated local download -> immutable vault.

The Azure SDK boundary is replaced by an in-memory fake container, so nothing touches the network (ADR-0013).
"""

from __future__ import annotations

import base64
import csv
import hashlib
import json
import logging
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from azure.core.exceptions import ResourceNotFoundError, ServiceRequestError

from aqueduct import blobupload as bu
from aqueduct.errors import ConfigError
from aqueduct.validated import UploadEntry

pytestmark = pytest.mark.unit

_PREFIX = "2026-0042-smith/20261004-share-a"
_ACCOUNT = "https://contoso.blob.core.windows.net"
_CHUNK = 4


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _md5(payload: bytes) -> bytes:
    return hashlib.md5(payload, usedforsecurity=False).digest()


class FakeBlob:
    """One blob client against a FakeContainer: records calls, stores committed bytes."""

    def __init__(self, container: FakeContainer, name: str) -> None:
        self.container = container
        self.name = name

    def get_blob_properties(self) -> SimpleNamespace:
        stored = self.container.blobs.get(self.name)
        if stored is None:
            raise ResourceNotFoundError("BlobNotFound")
        return SimpleNamespace(
            size=stored.size, metadata=stored.metadata, content_settings=SimpleNamespace(content_md5=stored.md5)
        )

    def stage_block(self, block_id: str, data: bytes, validate_content: bool = False) -> None:
        if self.container.on_stage:
            self.container.on_stage(block_id)
        if self.container.stage_failures:
            self.container.stage_failures -= 1
            raise ServiceRequestError("transient")
        self.container.staged_calls.append((self.name, block_id, data, validate_content))
        self.container.pending.setdefault(self.name, {})[block_id] = data

    def commit_block_list(self, blocks: list, content_settings=None, metadata=None) -> None:
        data = b"".join(self.container.pending.get(self.name, {})[b.id] for b in blocks)
        md5 = bytearray(content_settings.content_md5) if content_settings else None
        record = SimpleNamespace(data=data, size=len(data), metadata=dict(metadata or {}), md5=md5)
        if self.container.tamper:
            self.container.tamper(record)
        self.container.blobs[self.name] = record
        self.container.commits.append(self.name)


class FakeContainer:
    def __init__(self) -> None:
        self.blobs: dict[str, SimpleNamespace] = {}
        self.pending: dict[str, dict[str, bytes]] = {}
        self.staged_calls: list[tuple] = []
        self.commits: list[str] = []
        self.stage_failures = 0
        self.on_stage = None  # called with the block id at the start of every stage_block
        self.tamper = None

    def get_blob_client(self, name: str) -> FakeBlob:
        return FakeBlob(self, name)

    def seed(self, name: str, payload: bytes, *, sha256: str | None = None) -> None:
        self.blobs[name] = SimpleNamespace(
            data=payload,
            size=len(payload),
            metadata={"sha256": sha256 or _sha(payload)},
            md5=bytearray(_md5(payload)),
        )


def _ctx(container: FakeContainer, retries: int = 1, chunk: int = _CHUNK) -> bu.Ctx:
    return bu.Ctx(container=container, prefix=_PREFIX, chunk=chunk, max_retries=retries)


def _entry(path: str, payload: bytes, item_id: str = "guid-1") -> UploadEntry:
    return UploadEntry.from_manifest_path(path, len(payload), _sha(payload), item_id)


def _source(tmp_path: Path, entry: UploadEntry, payload: bytes) -> Path:
    target = entry.local_path(tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target


@pytest.fixture(autouse=True)
def _no_sleep(mocker) -> None:
    mocker.patch("aqueduct.blobupload.time.sleep")


# --- naming and configuration ---------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("m/c", "m/c", id="plain"),
        pytest.param("m/c/", "m/c", id="trailing_slash_trimmed"),
        pytest.param("m\\c", "m/c", id="backslashes_normalised"),
    ],
)
def test_normalize_prefix(raw: str, expected: str) -> None:
    assert bu.normalize_prefix(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("", id="empty"),
        pytest.param("/abs/path", id="leading_slash"),
        pytest.param("m/../c", id="parent_segment"),
        pytest.param("m//c", id="empty_segment"),
        pytest.param("./m", id="dot_segment"),
    ],
)
def test_normalize_prefix_rejects_unsafe_values(raw: str) -> None:
    with pytest.raises(ConfigError):
        bu.normalize_prefix(raw)


def test_data_blob_name_puts_manifest_path_under_data() -> None:
    assert bu.data_blob_name(_PREFIX, "Shared/Photos/a.jpg") == f"{_PREFIX}/data/Shared/Photos/a.jpg"


def test_resolve_config_prefers_flags_over_environment() -> None:
    env = {
        "AQUEDUCT_BLOB_ACCOUNT_URL": "https://other.blob.core.windows.net",
        "AQUEDUCT_BLOB_CONTAINER": "other",
        "AQUEDUCT_BLOB_PREFIX": "other/prefix",
    }

    cfg = bu.resolve_config(_ACCOUNT, "vault", "m/c", env)

    assert (cfg.account_url, cfg.container, cfg.prefix) == (_ACCOUNT, "vault", "m/c")


def test_resolve_config_falls_back_to_environment() -> None:
    env = {
        "AQUEDUCT_BLOB_ACCOUNT_URL": _ACCOUNT,
        "AQUEDUCT_BLOB_CONTAINER": "vault",
        "AQUEDUCT_BLOB_PREFIX": "m/c",
    }

    cfg = bu.resolve_config("", "", "", env)

    assert (cfg.account_url, cfg.container, cfg.prefix) == (_ACCOUNT, "vault", "m/c")


@pytest.mark.parametrize(
    ("account", "container", "prefix"),
    [
        pytest.param("", "vault", "m/c", id="no_account"),
        pytest.param(_ACCOUNT, "", "m/c", id="no_container"),
        pytest.param(_ACCOUNT, "vault", "", id="no_prefix"),
        pytest.param("http://contoso.blob.core.windows.net", "vault", "m/c", id="plain_http"),
        pytest.param(f"{_ACCOUNT}/?sv=2024&sig=abc", "vault", "m/c", id="sas_token_in_url"),
    ],
)
def test_resolve_config_rejects_missing_or_unsafe_values(account: str, container: str, prefix: str) -> None:
    with pytest.raises(ConfigError):
        bu.resolve_config(account, container, prefix, {})


# --- hashing and blocks ---------------------------------------------------------


def test_hash_file_pair_returns_sha256_and_md5_of_the_same_bytes(tmp_path: Path) -> None:
    path = tmp_path / "f.bin"
    path.write_bytes(b"evidence")

    assert bu.hash_file_pair(path) == (_sha(b"evidence"), _md5(b"evidence"))


def test_block_ids_are_deterministic_and_same_length() -> None:
    ids = [bu.block_id(i) for i in (0, 1, 10, 99999)]

    assert ids == [bu.block_id(i) for i in (0, 1, 10, 99999)]
    assert len({len(i) for i in ids}) == 1
    assert base64.b64decode(ids[0]) == b"00000000"


@pytest.mark.parametrize(
    ("payload", "expected_blocks"),
    [
        pytest.param(b"", 0, id="empty_file"),
        pytest.param(b"abcd", 1, id="exactly_one_chunk"),
        pytest.param(b"abcdefgh", 2, id="exact_multiple"),
        pytest.param(b"abcdefghij", 3, id="remainder"),
    ],
)
def test_upload_stages_the_expected_number_of_blocks(tmp_path: Path, payload: bytes, expected_blocks: int) -> None:
    container = FakeContainer()
    entry = _entry("a.bin", payload)
    _source(tmp_path, entry, payload)

    result = bu.transfer(_ctx(container), bu.data_item(_PREFIX, entry, tmp_path))

    assert result.status == "ok"
    assert len(container.staged_calls) == expected_blocks
    assert container.blobs[f"{_PREFIX}/data/a.bin"].data == payload


# --- the upload itself -----------------------------------------------------------


def test_blocks_are_staged_with_azure_content_validation(tmp_path: Path) -> None:
    container = FakeContainer()
    entry = _entry("a.bin", b"abcdefgh")
    _source(tmp_path, entry, b"abcdefgh")

    bu.transfer(_ctx(container), bu.data_item(_PREFIX, entry, tmp_path))

    assert container.staged_calls
    assert all(call[3] is True for call in container.staged_calls)


def test_commit_records_sha256_identity_and_source_path_as_metadata(tmp_path: Path) -> None:
    container = FakeContainer()
    payload = b"hello"
    entry = _entry("Shared/ü file.pdf", payload, item_id="guid-42")
    _source(tmp_path, entry, payload)

    bu.transfer(_ctx(container), bu.data_item(_PREFIX, entry, tmp_path))

    stored = container.blobs[f"{_PREFIX}/data/Shared/ü file.pdf"]
    assert stored.metadata["sha256"] == _sha(payload)
    assert stored.metadata["uniqueid"] == "guid-42"
    assert stored.metadata["sourcepath"].isascii()
    assert bytes(stored.md5) == _md5(payload)


def test_result_row_carries_the_recorded_hash_and_blob_name(tmp_path: Path) -> None:
    container = FakeContainer()
    entry = _entry("a.pdf", b"hello")
    _source(tmp_path, entry, b"hello")

    result = bu.transfer(_ctx(container), bu.data_item(_PREFIX, entry, tmp_path))

    assert (result.path, result.status, result.size) == ("a.pdf", "ok", 5)
    assert result.sha256 == _sha(b"hello")
    assert result.blob_name == f"{_PREFIX}/data/a.pdf"
    assert result.attempts == 1


def test_missing_source_file_fails_without_uploading(tmp_path: Path) -> None:
    container = FakeContainer()
    entry = _entry("a.pdf", b"hello")

    result = bu.transfer(_ctx(container), bu.data_item(_PREFIX, entry, tmp_path))

    assert result.status == "fail"
    assert "not found" in result.detail
    assert container.staged_calls == []


def test_new_file_changed_since_validate_is_rejected_and_never_committed(tmp_path: Path) -> None:
    container = FakeContainer()
    entry = _entry("a.pdf", b"hello")
    _source(tmp_path, entry, b"HELLO")  # same size, different bytes

    result = bu.transfer(_ctx(container), bu.data_item(_PREFIX, entry, tmp_path))

    assert result.status == "rejected"
    assert result.attempts == 0  # not retried
    assert container.commits == []
    assert not container.blobs


def test_file_changed_since_validate_is_rejected_before_conflict_when_a_same_size_blob_exists(tmp_path: Path) -> None:
    container = FakeContainer()
    entry = _entry("a.pdf", b"hello")
    _source(tmp_path, entry, b"HELLO")
    container.seed(f"{_PREFIX}/data/a.pdf", b"hello")

    result = bu.transfer(_ctx(container), bu.data_item(_PREFIX, entry, tmp_path))

    assert result.status == "rejected"
    assert container.staged_calls == []


def test_file_that_changes_while_uploading_is_not_committed(tmp_path: Path, mocker) -> None:
    container = FakeContainer()
    entry = _entry("a.pdf", b"abcdefgh")
    source = _source(tmp_path, entry, b"abcdefgh")
    real_read = bu._read_blocks

    def mutate_then_read(path, chunk):
        source.write_bytes(b"abcdXXXX")
        return real_read(path, chunk)

    mocker.patch("aqueduct.blobupload._read_blocks", side_effect=mutate_then_read)

    result = bu.transfer(_ctx(container, retries=0), bu.data_item(_PREFIX, entry, tmp_path))

    assert result.status == "rejected"  # the single hash pass saw bytes other than the validated ones
    assert container.commits == []


# --- verification: "completed" only when verified -------------------------------


@pytest.mark.parametrize(
    ("tamper", "fragment"),
    [
        pytest.param(lambda r: setattr(r, "size", r.size + 1), "size", id="size_mismatch"),
        pytest.param(lambda r: r.metadata.update(sha256="0" * 64), "SHA-256", id="metadata_sha_mismatch"),
        pytest.param(lambda r: setattr(r, "md5", bytearray(b"x" * 16)), "MD5", id="md5_mismatch"),
    ],
)
def test_unverifiable_blob_is_retried_then_recorded_as_a_failure(tmp_path: Path, tamper, fragment: str) -> None:
    container = FakeContainer()
    container.tamper = tamper
    entry = _entry("a.pdf", b"hello")
    _source(tmp_path, entry, b"hello")

    result = bu.transfer(_ctx(container, retries=2), bu.data_item(_PREFIX, entry, tmp_path))

    assert result.status == "fail"
    assert fragment in result.detail
    assert result.attempts == 3


def test_blob_without_content_md5_is_still_verified_by_sha256(tmp_path: Path) -> None:
    container = FakeContainer()
    container.tamper = lambda r: setattr(r, "md5", None)
    entry = _entry("a.pdf", b"hello")
    _source(tmp_path, entry, b"hello")

    result = bu.transfer(_ctx(container), bu.data_item(_PREFIX, entry, tmp_path))

    assert result.status == "ok"


# --- idempotency and immutability -----------------------------------------------


def test_already_verified_blob_is_skipped_without_staging(tmp_path: Path) -> None:
    container = FakeContainer()
    entry = _entry("a.pdf", b"hello")
    _source(tmp_path, entry, b"hello")
    container.seed(f"{_PREFIX}/data/a.pdf", b"hello")

    result = bu.transfer(_ctx(container), bu.data_item(_PREFIX, entry, tmp_path))

    assert result.status == "skip"
    assert container.staged_calls == []
    assert container.commits == []


def test_existing_blob_with_a_different_hash_is_a_conflict_and_is_not_overwritten(tmp_path: Path) -> None:
    container = FakeContainer()
    entry = _entry("a.pdf", b"hello")
    _source(tmp_path, entry, b"hello")
    container.seed(f"{_PREFIX}/data/a.pdf", b"OTHER")

    result = bu.transfer(_ctx(container), bu.data_item(_PREFIX, entry, tmp_path))

    assert result.status == "conflict"
    assert container.commits == []
    assert container.blobs[f"{_PREFIX}/data/a.pdf"].data == b"OTHER"


def test_existing_blob_with_same_size_but_different_metadata_hash_is_a_conflict(tmp_path: Path) -> None:
    container = FakeContainer()
    entry = _entry("a.pdf", b"hello")
    _source(tmp_path, entry, b"hello")
    container.seed(f"{_PREFIX}/data/a.pdf", b"hello", sha256="f" * 64)

    result = bu.transfer(_ctx(container), bu.data_item(_PREFIX, entry, tmp_path))

    assert result.status == "conflict"


def test_rerun_only_uploads_files_that_are_not_yet_in_the_vault(tmp_path: Path) -> None:
    container = FakeContainer()
    done, todo = _entry("done.pdf", b"aaaa"), _entry("todo.pdf", b"bbbb")
    _source(tmp_path, done, b"aaaa")
    _source(tmp_path, todo, b"bbbb")
    container.seed(f"{_PREFIX}/data/done.pdf", b"aaaa")
    items = [bu.data_item(_PREFIX, e, tmp_path) for e in (done, todo)]

    results = bu.run_transfers(_ctx(container), items, workers=2)

    assert {r.path: r.status for r in results} == {"done.pdf": "skip", "todo.pdf": "ok"}
    assert container.commits == [f"{_PREFIX}/data/todo.pdf"]


# --- retry ---------------------------------------------------------------------


def test_transient_azure_error_is_retried_and_succeeds(tmp_path: Path) -> None:
    container = FakeContainer()
    container.stage_failures = 1
    entry = _entry("a.pdf", b"hello")
    _source(tmp_path, entry, b"hello")

    result = bu.transfer(_ctx(container, retries=2), bu.data_item(_PREFIX, entry, tmp_path))

    assert result.status == "ok"
    assert result.attempts == 2


def test_retries_exhausted_is_recorded_as_a_failure(tmp_path: Path) -> None:
    container = FakeContainer()
    container.stage_failures = 99
    entry = _entry("a.pdf", b"hello")
    _source(tmp_path, entry, b"hello")

    result = bu.transfer(_ctx(container, retries=1), bu.data_item(_PREFIX, entry, tmp_path))

    assert result.status == "fail"
    assert result.attempts == 2
    assert container.commits == []


# --- audit bundle ----------------------------------------------------------------


def _results(*rows: tuple[str, str, str]) -> list[bu.UploadResult]:
    return [bu.UploadResult(path, status, 1, sha, f"{_PREFIX}/data/{path}", 1, 0.0) for path, status, sha in rows]


def test_sha256sums_lists_every_verified_data_file_and_audit_file_in_sha256sum_format(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(b"{}")
    results = _results(("a.pdf", "ok", "a" * 64), ("b.pdf", "skip", "b" * 64), ("c.pdf", "fail", "c" * 64))

    text = bu.build_sha256sums(results, [manifest])

    lines = text.splitlines()
    assert f"{'a' * 64}  data/a.pdf" in lines
    assert f"{'b' * 64}  data/b.pdf" in lines
    assert f"{_sha(b'{}')}  _audit/manifest.json" in lines
    assert not any("c.pdf" in line for line in lines)


def test_sha256sums_is_sorted_so_the_bundle_is_reproducible(tmp_path: Path) -> None:
    results = _results(("z.pdf", "ok", "z" * 64), ("a.pdf", "ok", "a" * 64))

    lines = bu.build_sha256sums(results, []).splitlines()

    assert lines == sorted(lines, key=lambda line: line.split("  ", 1)[1])


def test_audit_items_are_named_under_a_per_run_audit_folder(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(b"{}")

    item = bu.audit_item(_PREFIX, "20261004T221500Z", manifest)

    assert item.name == f"{_PREFIX}/_audit/20261004T221500Z/manifest.json"
    assert item.expected_sha256 == ""


def test_audit_files_default_to_the_standard_record_names(tmp_path: Path) -> None:
    for name in ("manifest.json", "manifest.csv", "filecopy_results.csv", "validate_results.csv"):
        (tmp_path / name).write_text("x")
    (tmp_path / "filecopy_results.csv.metadata.json").write_text("{}")

    found = bu.default_audit_files(tmp_path)

    assert [p.name for p in found] == [
        "manifest.json",
        "manifest.csv",
        "filecopy_results.csv",
        "filecopy_results.csv.metadata.json",
        "validate_results.csv",
    ]


def test_custody_record_binds_the_sha256sums_hash_and_run_counts() -> None:
    meta = {"tool": "upload", "tool_version": "0.1.0", "operator": "unspecified", "host_info": {}}
    results = _results(("a.pdf", "ok", "a" * 64), ("b.pdf", "fail", "b" * 64))

    record = bu.build_custody(meta, _ACCOUNT, "vault", _PREFIX, results, "deadbeef")

    assert record["sha256sums_sha256"] == "deadbeef"
    assert record["container"] == "vault"
    assert record["prefix"] == _PREFIX
    assert record["counts"] == {"ok": 1, "fail": 1}


# --- command line -----------------------------------------------------------------


def _write_validate_results(path: Path, rows: list[tuple[str, str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        fh.write("# aqueduct acquisition (provenance)\r\n")
        writer = csv.writer(fh)
        writer.writerow(["path", "status", "sha256", "hash_check", "segment_check"])
        for rel, sha in rows:
            writer.writerow([rel, "ok", sha, "ok", ""])


@pytest.fixture
def workspace(tmp_path: Path, mocker, monkeypatch) -> SimpleNamespace:
    """A download folder + manifest + validate results, with the Azure SDK replaced by a FakeContainer."""
    monkeypatch.chdir(tmp_path)
    container = FakeContainer()
    mocker.patch("aqueduct.blobupload.open_container", return_value=container)
    for var in ("AQUEDUCT_BLOB_ACCOUNT_URL", "AQUEDUCT_BLOB_CONTAINER", "AQUEDUCT_BLOB_PREFIX"):
        monkeypatch.delenv(var, raising=False)
    payload = b"hello"
    (tmp_path / "download").mkdir()
    (tmp_path / "download" / "a.pdf").write_bytes(payload)
    manifest = {"items": [{"path": "a.pdf", "type": "file", "size": 5, "id": "guid-1"}]}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _write_validate_results(tmp_path / "validate_results.csv", [("a.pdf", _sha(payload))])
    argv = ["--account-url", _ACCOUNT, "--container", "vault", "--dest-prefix", _PREFIX]
    return SimpleNamespace(root=tmp_path, container=container, argv=argv, payload=payload)


def test_main_uploads_data_then_writes_results_and_audit_bundle(workspace) -> None:
    code = bu.main(workspace.argv)

    names = set(workspace.container.blobs)
    assert code == 0
    assert f"{_PREFIX}/data/a.pdf" in names
    audit = {n.rsplit("/", 1)[1] for n in names if "/_audit/" in n}
    assert {"manifest.json", "validate_results.csv", "upload_results.csv", "SHA256SUMS", "custody.json"} <= audit
    assert (workspace.root / "upload_results.csv.metadata.json").exists()


def test_main_uploads_audit_files_after_the_data_files(workspace) -> None:
    bu.main(workspace.argv)

    commits = workspace.container.commits
    data_at = commits.index(f"{_PREFIX}/data/a.pdf")
    assert all(commits.index(n) > data_at for n in commits if "/_audit/" in n)


def test_main_results_csv_has_provenance_header_and_columns(workspace) -> None:
    bu.main(workspace.argv)

    text = (workspace.root / "upload_results.csv").read_text(encoding="utf-8-sig")
    lines = [line for line in text.splitlines() if line]
    assert lines[0].startswith("# ")
    header = next(line for line in lines if not line.startswith("#"))
    assert header == "path,status,size,sha256,blob_name,attempts,seconds,detail"


def test_main_sha256sums_in_vault_matches_local_file_hashes(workspace) -> None:
    bu.main(workspace.argv)

    sums_name = next(n for n in workspace.container.blobs if n.endswith("/SHA256SUMS"))
    text = workspace.container.blobs[sums_name].data.decode()
    assert f"{_sha(workspace.payload)}  data/a.pdf" in text.splitlines()


def test_main_rerun_skips_data_and_adds_a_new_audit_folder_without_conflict(workspace, mocker) -> None:
    bu.main(workspace.argv)
    mocker.patch("aqueduct.blobupload._run_id", return_value="20991231T000000Z")

    code = bu.main(workspace.argv)

    assert code == 0
    assert workspace.container.commits.count(f"{_PREFIX}/data/a.pdf") == 1
    assert any("/_audit/20991231T000000Z/" in n for n in workspace.container.blobs)


def test_main_refuses_files_that_did_not_pass_validate(workspace) -> None:
    _write_validate_results(workspace.root / "validate_results.csv", [])

    code = bu.main(workspace.argv)

    assert code == 1
    assert f"{_PREFIX}/data/a.pdf" not in workspace.container.blobs
    rows = list(
        csv.DictReader(
            line
            for line in (workspace.root / "upload_results.csv").read_text("utf-8-sig").splitlines()
            if not line.startswith("#")
        )
    )
    assert [(r["path"], r["status"]) for r in rows] == [("a.pdf", "rejected")]


def test_main_conflict_exits_nonzero(workspace) -> None:
    workspace.container.seed(f"{_PREFIX}/data/a.pdf", b"OTHER")

    assert bu.main(workspace.argv) == 1


def test_main_missing_validate_results_exits_2(workspace) -> None:
    (workspace.root / "validate_results.csv").unlink()

    assert bu.main(workspace.argv) == 2


def test_main_missing_destination_config_exits_2(workspace) -> None:
    assert bu.main(["--container", "vault", "--dest-prefix", _PREFIX]) == 2


def test_main_missing_manifest_exits_2(workspace) -> None:
    (workspace.root / "manifest.json").unlink()

    assert bu.main(workspace.argv) == 2


def test_logs_never_contain_sas_tokens_or_credentials(workspace, caplog) -> None:
    caplog.set_level(logging.DEBUG)

    bu.main(workspace.argv)

    assert "sig=" not in caplog.text
    assert "AccountKey" not in caplog.text


# --- size-first lookup and the single hash pass (Issue #18) ----------------------


def _spy_hashing(mocker):
    return mocker.patch("aqueduct.blobupload.hash_file_pair", wraps=bu.hash_file_pair)


def test_new_file_is_hashed_in_one_pass_while_staging(tmp_path: Path, mocker) -> None:
    spy = _spy_hashing(mocker)
    container = FakeContainer()
    entry = _entry("a.pdf", b"hello world")
    _source(tmp_path, entry, b"hello world")

    result = bu.transfer(_ctx(container), bu.data_item(_PREFIX, entry, tmp_path))

    assert result.status == "ok"
    assert result.sha256 == _sha(b"hello world")
    spy.assert_not_called()


def test_existing_blob_of_a_different_size_is_a_conflict_without_reading_the_file(tmp_path: Path, mocker) -> None:
    spy = _spy_hashing(mocker)
    container = FakeContainer()
    entry = _entry("a.pdf", b"hello")
    _source(tmp_path, entry, b"hello")
    container.seed(f"{_PREFIX}/data/a.pdf", b"hello world")

    result = bu.transfer(_ctx(container), bu.data_item(_PREFIX, entry, tmp_path))

    assert result.status == "conflict"
    spy.assert_not_called()
    assert container.staged_calls == []


def test_existing_matching_blob_is_skipped_after_one_hash(tmp_path: Path, mocker) -> None:
    spy = _spy_hashing(mocker)
    container = FakeContainer()
    entry = _entry("a.pdf", b"hello")
    _source(tmp_path, entry, b"hello")
    container.seed(f"{_PREFIX}/data/a.pdf", b"hello")

    result = bu.transfer(_ctx(container), bu.data_item(_PREFIX, entry, tmp_path))

    assert result.status == "skip"
    spy.assert_called_once()


# --- parallel block staging ----------------------------------------------------------


def test_blocks_of_one_file_are_staged_concurrently_and_committed_in_order(tmp_path: Path) -> None:
    container = FakeContainer()
    barrier = threading.Barrier(2, timeout=5)  # two blocks must be inside stage_block at the same time
    container.on_stage = lambda _ident: barrier.wait()
    payload = b"abcdefgh"  # two 4-byte blocks
    entry = _entry("a.pdf", payload)
    _source(tmp_path, entry, payload)
    ctx = _ctx(container)
    ctx.block_workers = 2

    result = bu.transfer(ctx, bu.data_item(_PREFIX, entry, tmp_path))

    assert result.status == "ok"
    assert container.blobs[f"{_PREFIX}/data/a.pdf"].data == payload


def test_a_failed_block_among_parallel_blocks_is_retried_and_the_file_verified(tmp_path: Path) -> None:
    container = FakeContainer()
    container.stage_failures = 1
    payload = b"abcdefghijkl"
    entry = _entry("a.pdf", payload)
    _source(tmp_path, entry, payload)
    ctx = _ctx(container, retries=1)
    ctx.block_workers = 3

    result = bu.transfer(ctx, bu.data_item(_PREFIX, entry, tmp_path))

    assert result.status == "ok"
    assert result.attempts == 2  # noqa: PLR2004
    assert container.blobs[f"{_PREFIX}/data/a.pdf"].data == payload


# --- progress -------------------------------------------------------------------------


def test_progress_counts_files_and_bytes_for_every_outcome(tmp_path: Path) -> None:
    container = FakeContainer()
    new, done = _entry("new.pdf", b"hello"), _entry("done.pdf", b"world!")
    _source(tmp_path, new, b"hello")
    _source(tmp_path, done, b"world!")
    container.seed(f"{_PREFIX}/data/done.pdf", b"world!")
    items = [bu.data_item(_PREFIX, e, tmp_path) for e in (new, done)]
    ctx = _ctx(container)

    bu.run_transfers(ctx, items, workers=2)

    assert (ctx.progress.files_done, ctx.progress.bytes_done) == (2, 11)
    assert (ctx.progress.total_files, ctx.progress.total_bytes) == (2, 11)


def test_failed_attempt_bytes_are_not_double_counted(tmp_path: Path) -> None:
    container = FakeContainer()
    container.stage_failures = 1
    entry = _entry("a.pdf", b"abcdefgh")
    _source(tmp_path, entry, b"abcdefgh")
    ctx = _ctx(container, retries=1)

    bu.run_transfers(ctx, [bu.data_item(_PREFIX, entry, tmp_path)], workers=1)

    assert ctx.progress.bytes_done == 8  # noqa: PLR2004


def test_large_files_are_announced_when_they_start(tmp_path: Path, mocker, caplog) -> None:
    mocker.patch("aqueduct.blobupload._LARGE_BYTES", 1)
    entry = _entry("big.bin", b"hello")
    _source(tmp_path, entry, b"hello")

    with caplog.at_level(logging.INFO, logger="upload"):
        bu.transfer(_ctx(FakeContainer()), bu.data_item(_PREFIX, entry, tmp_path))

    assert any(line.startswith("start ") and "big.bin" in line for line in caplog.messages)


def test_progress_line_is_logged_on_a_timer_and_the_thread_stops(caplog) -> None:
    progress = bu.Progress(total_files=3, total_bytes=3_000_000_000)

    with caplog.at_level(logging.INFO, logger="upload"), bu._ProgressReporter(progress, interval=0.01) as reporter:
        threading.Event().wait(0.1)  # real wait: the autouse fixture stubs time.sleep

    assert any("progress: 0/3 files, 0.00/3.00 GB" in m for m in caplog.messages)
    assert not reporter._thread.is_alive()


# --- results checkpoint -----------------------------------------------------------------

_RUN_META = {"tool": "upload", "tool_version": "1", "operator": "x", "host_info": {}, "started_at_utc": "t0"}


def _result_row(name: str) -> bu.UploadResult:
    return bu.UploadResult(name, "ok", 1, "a" * 64, f"{_PREFIX}/data/{name}", 1, 0.1)


def test_checkpoint_rewrites_the_results_csv_every_n_files(tmp_path: Path, mocker) -> None:
    mocker.patch("aqueduct.blobupload._FLUSH_EVERY", 2)
    path = tmp_path / "upload_results.csv"
    checkpoint = bu._Checkpoint(path, _RUN_META, "vault/m/c")

    checkpoint(_result_row("a.pdf"))
    assert not path.exists()
    checkpoint(_result_row("b.pdf"))

    text = path.read_text(encoding="utf-8-sig")
    assert "a.pdf" in text
    assert "b.pdf" in text
    assert "in progress" in text


def test_checkpoint_rewrites_the_results_csv_after_the_time_interval(tmp_path: Path, mocker) -> None:
    mocker.patch("aqueduct.blobupload._FLUSH_SECONDS", 0)
    path = tmp_path / "upload_results.csv"

    bu._Checkpoint(path, _RUN_META, "vault/m/c")(_result_row("a.pdf"))

    assert "a.pdf" in path.read_text(encoding="utf-8-sig")


def test_interrupted_run_leaves_a_results_csv_behind(workspace, mocker) -> None:
    mocker.patch("aqueduct.blobupload.run_transfers", side_effect=KeyboardInterrupt)

    with pytest.raises(KeyboardInterrupt):
        bu.main(workspace.argv)

    assert (workspace.root / "upload_results.csv").exists()


# --- hash and block worker options -------------------------------------------------------


def test_hash_workers_cap_how_many_files_are_hashed_at_once(tmp_path: Path, mocker) -> None:
    container = FakeContainer()
    items = []
    for n in range(4):
        entry = _entry(f"f{n}.bin", b"hello")
        _source(tmp_path, entry, b"hello")
        container.seed(f"{_PREFIX}/data/f{n}.bin", b"hello")  # same size -> each must be hashed
        items.append(bu.data_item(_PREFIX, entry, tmp_path))
    live, peak, lock = [0], [0], threading.Lock()
    real = bu.hash_file_pair

    def counting(path):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        threading.Event().wait(0.05)
        try:
            return real(path)
        finally:
            with lock:
                live[0] -= 1

    mocker.patch("aqueduct.blobupload.hash_file_pair", side_effect=counting)
    ctx = _ctx(container)
    ctx.hash_sem = threading.BoundedSemaphore(1)

    bu.run_transfers(ctx, items, workers=4)

    assert peak[0] == 1


def test_parse_args_defaults_for_worker_options() -> None:
    args = bu.parse_args([])

    assert (args.block_workers, args.hash_workers) == (4, 3)


# --- review findings -------------------------------------------------------------------


def test_unreadable_source_with_an_existing_same_size_blob_is_a_fail_row_not_a_crash(tmp_path: Path, mocker) -> None:
    container = FakeContainer()
    entry = _entry("a.pdf", b"hello")
    _source(tmp_path, entry, b"hello")
    container.seed(f"{_PREFIX}/data/a.pdf", b"hello")
    mocker.patch("aqueduct.blobupload.hash_file_pair", side_effect=PermissionError("locked"))

    result = bu.transfer(_ctx(container), bu.data_item(_PREFIX, entry, tmp_path))

    assert result.status == "fail"
    assert "locked" in result.detail


def test_transient_vault_lookup_error_is_retried(tmp_path: Path, mocker) -> None:
    container = FakeContainer()
    entry = _entry("a.pdf", b"hello")
    _source(tmp_path, entry, b"hello")
    real = bu._existing_properties
    calls = []

    def flaky(ctx, name):
        calls.append(name)
        if len(calls) == 1:
            raise ServiceRequestError("503")
        return real(ctx, name)

    mocker.patch("aqueduct.blobupload._existing_properties", side_effect=flaky)

    result = bu.transfer(_ctx(container, retries=1), bu.data_item(_PREFIX, entry, tmp_path))

    assert result.status == "ok"


def test_vault_lookup_that_keeps_failing_is_recorded_as_a_failure(tmp_path: Path, mocker) -> None:
    entry = _entry("a.pdf", b"hello")
    _source(tmp_path, entry, b"hello")
    mocker.patch("aqueduct.blobupload._existing_properties", side_effect=ServiceRequestError("503"))

    result = bu.transfer(_ctx(FakeContainer(), retries=1), bu.data_item(_PREFIX, entry, tmp_path))

    assert result.status == "fail"
    assert "could not check the vault" in result.detail


def test_retry_after_a_committed_but_unreadable_blob_does_not_commit_again(tmp_path: Path, mocker) -> None:
    container = FakeContainer()
    entry = _entry("a.pdf", b"hello")
    _source(tmp_path, entry, b"hello")
    real_get = FakeBlob.get_blob_properties
    reads = []

    def flaky_readback(self):
        reads.append(self.name)
        if len(reads) == 2:  # the read-back right after the first commit (the first read is the lookup)
            raise ServiceRequestError("read-back lost")
        return real_get(self)

    mocker.patch.object(FakeBlob, "get_blob_properties", flaky_readback)

    result = bu.transfer(_ctx(container, retries=1), bu.data_item(_PREFIX, entry, tmp_path))

    assert result.status == "ok"
    assert container.commits == [f"{_PREFIX}/data/a.pdf"]  # committed once, then re-verified


def test_new_uploads_are_also_held_to_the_hash_workers_cap(tmp_path: Path) -> None:
    container = FakeContainer()
    items = []
    for n in range(4):
        entry = _entry(f"f{n}.bin", b"abcdefgh")
        _source(tmp_path, entry, b"abcdefgh")
        items.append(bu.data_item(_PREFIX, entry, tmp_path))
    ctx = _ctx(container)
    held = []
    ctx.hash_sem = threading.BoundedSemaphore(1)
    real_read = bu._read_blocks

    def watching(path, chunk):
        for data in real_read(path, chunk):
            held.append(ctx.hash_sem._value)  # 0 while a reader holds the only permit
            yield data

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(bu, "_read_blocks", watching)
        results = bu.run_transfers(ctx, items, workers=4)

    assert all(r.status == "ok" for r in results)
    assert held
    assert set(held) == {0}
