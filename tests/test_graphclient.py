"""Tests for the destination-side Graph client (app-only auth, uploads, folder/lookup calls).

The Graph HTTP boundary is mocked with ``httpx.MockTransport``; nothing touches the network.
"""

from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import parse_qs

import httpx
import pytest
from aqueduct.errors import AuthError, GraphError, UploadError
from aqueduct.graphclient import FolderRef, GraphClient

from aqueduct import graphclient as gc

pytestmark = pytest.mark.unit

_BASE = "https://graph.test/v1.0"
_DEST = FolderRef("drive-1", "dest-item")
_FAKE_SECRET = "not-a-real-secret-value"  # fictional placeholder
_FAKE_TOKEN = "fake-access-token-123"  # fictional placeholder


class _Tokens:
    """Stands in for the token provider; counts acquisitions."""

    def __init__(self) -> None:
        self.calls = 0

    def token(self) -> str:
        self.calls += 1
        return f"{_FAKE_TOKEN}-{self.calls}"


def _graph(handler, tokens: _Tokens | None = None) -> tuple[GraphClient, _Tokens]:
    tokens = tokens or _Tokens()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GraphClient(client, tokens, _BASE), tokens


def _item(name: str = "f.pdf", size: int = 10, qxh: str | None = "HASH") -> dict:
    payload: dict = {"id": "item-1", "name": name, "size": size, "parentReference": {"driveId": "drive-1"}}
    payload["file"] = {"hashes": {"quickXorHash": qxh} if qxh else {}}
    return payload


# --- config + token provider -------------------------------------------------


def _write_config(tmp_path, **overrides) -> object:
    data = {"tenant_id": "tenant-guid", "client_id": "client-guid", "client_secret": _FAKE_SECRET}
    data.update(overrides)
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_load_config_reads_file(tmp_path) -> None:
    cfg = gc.load_config(_write_config(tmp_path), env={})
    assert (cfg.tenant_id, cfg.client_id, cfg.client_secret) == ("tenant-guid", "client-guid", _FAKE_SECRET)


def test_load_config_secret_from_env_overrides_file(tmp_path) -> None:
    path = _write_config(tmp_path, client_secret="from-file")
    cfg = gc.load_config(path, env={"AQUEDUCT_GRAPH_CLIENT_SECRET": "from-env"})
    assert cfg.client_secret == "from-env"


def test_load_config_secret_only_in_env(tmp_path) -> None:
    path = _write_config(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["client_secret"]
    path.write_text(json.dumps(data), encoding="utf-8")
    cfg = gc.load_config(path, env={"AQUEDUCT_GRAPH_CLIENT_SECRET": "from-env"})
    assert cfg.client_secret == "from-env"


@pytest.mark.parametrize("missing", ["tenant_id", "client_id"])
def test_load_config_requires_ids(tmp_path, missing: str) -> None:
    path = _write_config(tmp_path, **{missing: ""})
    with pytest.raises(AuthError, match=missing):
        gc.load_config(path, env={})


def test_load_config_requires_a_secret(tmp_path) -> None:
    path = _write_config(tmp_path, client_secret="")
    with pytest.raises(AuthError, match="secret"):
        gc.load_config(path, env={})


def test_load_config_missing_file(tmp_path) -> None:
    with pytest.raises(AuthError, match="graph.json"):
        gc.load_config(tmp_path / "graph.json", env={})


def test_token_provider_requests_client_credentials_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"access_token": _FAKE_TOKEN, "expires_in": 3599})

    cfg = gc.GraphConfig("tenant-guid", "client-guid", _FAKE_SECRET)
    provider = gc.ClientCredentialsTokenProvider(cfg, httpx.Client(transport=httpx.MockTransport(handler)))

    assert provider.token() == _FAKE_TOKEN
    request = seen[0]
    assert request.url.path == "/tenant-guid/oauth2/v2.0/token"
    form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
    assert form["grant_type"] == "client_credentials"
    assert form["client_id"] == "client-guid"
    assert form["client_secret"] == _FAKE_SECRET
    assert form["scope"] == "https://graph.microsoft.com/.default"


def test_token_provider_failure_does_not_leak_secret() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_client", "error_description": "bad credentials"})

    cfg = gc.GraphConfig("tenant-guid", "client-guid", _FAKE_SECRET)
    provider = gc.ClientCredentialsTokenProvider(cfg, httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(AuthError) as excinfo:
        provider.token()
    assert _FAKE_SECRET not in str(excinfo.value)
    assert "bad credentials" in str(excinfo.value)


# --- authorized requests: 401 refresh, 429 back-off, no secret in logs -------


def test_send_refreshes_token_once_on_401() -> None:
    auth_seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth_seen.append(request.headers["Authorization"])
        return httpx.Response(401) if len(auth_seen) == 1 else httpx.Response(200, json=_item())

    graph, tokens = _graph(handler)
    meta = asyncio.run(graph.child_meta(_DEST, "f.pdf"))

    assert meta is not None
    assert tokens.calls == 2
    assert auth_seen == [f"Bearer {_FAKE_TOKEN}-1", f"Bearer {_FAKE_TOKEN}-2"]


def test_send_gives_up_after_second_401() -> None:
    graph, _ = _graph(lambda _r: httpx.Response(401))
    with pytest.raises(GraphError, match="unauthorized"):
        asyncio.run(graph.child_meta(_DEST, "f.pdf"))


def test_send_honors_retry_after_on_429(mocker) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    mocker.patch.object(gc.asyncio, "sleep", fake_sleep)
    responses = iter([httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, json=_item())])
    graph, _ = _graph(lambda _r: next(responses))

    assert asyncio.run(graph.child_meta(_DEST, "f.pdf")) is not None
    assert sleeps == [7]


def test_send_uses_default_back_off_without_retry_after(mocker) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    mocker.patch.object(gc.asyncio, "sleep", fake_sleep)
    responses = iter([httpx.Response(429), httpx.Response(200, json=_item())])
    graph, _ = _graph(lambda _r: next(responses))

    asyncio.run(graph.child_meta(_DEST, "f.pdf"))
    assert sleeps == [10]


def test_authorization_header_never_logged(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    graph, _ = _graph(lambda _r: httpx.Response(200, json=_item()))

    asyncio.run(graph.child_meta(_DEST, "f.pdf"))

    assert _FAKE_TOKEN not in caplog.text
    assert "Bearer" not in caplog.text


# --- lookups -----------------------------------------------------------------


def test_child_meta_parses_size_and_quickxorhash() -> None:
    graph, _ = _graph(lambda _r: httpx.Response(200, json=_item(size=42, qxh="ABC=")))
    meta = asyncio.run(graph.child_meta(_DEST, "f.pdf"))
    assert (meta.size, meta.quick_xor_hash, meta.item_id, meta.drive_id) == (42, "ABC=", "item-1", "drive-1")


def test_child_meta_reports_missing_hash_as_none() -> None:
    graph, _ = _graph(lambda _r: httpx.Response(200, json=_item(qxh=None)))
    assert asyncio.run(graph.child_meta(_DEST, "f.pdf")).quick_xor_hash is None


def test_child_meta_returns_none_on_404() -> None:
    graph, _ = _graph(lambda _r: httpx.Response(404))
    assert asyncio.run(graph.child_meta(_DEST, "f.pdf")) is None


def test_child_meta_raises_on_other_errors() -> None:
    graph, _ = _graph(lambda _r: httpx.Response(500, text="boom"))
    with pytest.raises(UploadError, match="500"):
        asyncio.run(graph.child_meta(_DEST, "f.pdf"))


@pytest.mark.parametrize("name", ["Audio", "video", "content", "children", "Exhibit 1"])
def test_child_lookup_terminates_path_with_colon(name: str) -> None:
    """A bare ``items/{id}:/Audio`` returns the reserved facet; a trailing ':' forces a child lookup."""
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, json=_item(name=name))

    graph, _ = _graph(handler)
    asyncio.run(graph.child_meta(_DEST, name))

    assert urls[0].endswith(":")
    assert "/drives/drive-1/items/dest-item:/" in urls[0]


# --- uploads ------------------------------------------------------------------


def test_upload_small_puts_content() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json=_item(size=5))

    graph, _ = _graph(handler)
    meta = asyncio.run(graph.upload_small(_DEST, "f.pdf", b"hello"))

    assert seen[0].method == "PUT"
    assert str(seen[0].url).endswith(":/content")
    assert seen[0].content == b"hello"
    assert meta.size == 5


def test_upload_small_rejection_raises() -> None:
    graph, _ = _graph(lambda _r: httpx.Response(403, text="denied"))
    with pytest.raises(UploadError, match="403"):
        asyncio.run(graph.upload_small(_DEST, "f.pdf", b"x"))


def test_create_upload_session_requests_replace_and_returns_url() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"uploadUrl": "https://upload.test/session-abc"})

    graph, _ = _graph(handler)
    url = asyncio.run(graph.create_upload_session(_DEST, "big.bin"))

    assert url == "https://upload.test/session-abc"
    assert str(seen[0].url).endswith(":/createUploadSession")
    assert json.loads(seen[0].content)["item"]["@microsoft.graph.conflictBehavior"] == "replace"


def test_create_upload_session_without_url_raises() -> None:
    graph, _ = _graph(lambda _r: httpx.Response(200, json={}))
    with pytest.raises(UploadError, match="uploadUrl"):
        asyncio.run(graph.create_upload_session(_DEST, "big.bin"))


# --- folders ------------------------------------------------------------------


def test_ensure_child_folder_returns_existing() -> None:
    graph, _ = _graph(lambda _r: httpx.Response(200, json={"id": "existing"}))
    assert asyncio.run(graph.ensure_child_folder(_DEST, "Exhibits")) == FolderRef("drive-1", "existing")


def test_ensure_child_folder_creates_when_absent() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(404)
        return httpx.Response(201, json={"id": "new-folder"})

    graph, _ = _graph(handler)
    folder = asyncio.run(graph.ensure_child_folder(_DEST, "Exhibits"))

    assert folder == FolderRef("drive-1", "new-folder")
    body = json.loads(calls[-1].content)
    assert body["name"] == "Exhibits"
    assert body["@microsoft.graph.conflictBehavior"] == "fail"


# --- destination URL parsing ---------------------------------------------------


@pytest.mark.parametrize(
    ("url", "site_url", "library", "folder"),
    [
        pytest.param(
            "https://contoso.sharepoint.com/sites/Review/Shared%20Documents/Case%2012/Exhibits",
            "https://contoso.sharepoint.com/sites/Review",
            "Shared Documents",
            "Case 12/Exhibits",
            id="site_library_nested_folder",
        ),
        pytest.param(
            "https://contoso.sharepoint.com/sites/Review/Shared%20Documents",
            "https://contoso.sharepoint.com/sites/Review",
            "Shared Documents",
            "",
            id="library_root",
        ),
        pytest.param(
            "https://contoso.sharepoint.com/teams/Legal/Discovery/Case%2012/",
            "https://contoso.sharepoint.com/teams/Legal",
            "Discovery",
            "Case 12",
            id="teams_site_trailing_slash",
        ),
        pytest.param(
            "https://contoso.sharepoint.com/Shared%20Documents/Case",
            "https://contoso.sharepoint.com",
            "Shared Documents",
            "Case",
            id="root_site",
        ),
        pytest.param(
            "https://contoso.sharepoint.com/sites/Review/Shared%20Documents/Case%2012?web=1",
            "https://contoso.sharepoint.com/sites/Review",
            "Shared Documents",
            "Case 12",
            id="query_string_ignored",
        ),
        pytest.param(
            "https://contoso.sharepoint.com/sites/Review/Shared%20Documents/Forms/AllItems.aspx"
            "?id=%2Fsites%2FReview%2FShared%20Documents%2FCase%2012%2FExhibits&viewid=abc",
            "https://contoso.sharepoint.com/sites/Review",
            "Shared Documents",
            "Case 12/Exhibits",
            id="browser_url_with_id_parameter",
        ),
    ],
)
def test_parse_destination_url(url: str, site_url: str, library: str, folder: str) -> None:
    parsed = gc.parse_destination_url(url)
    assert (parsed.site_url, parsed.library, parsed.folder) == (site_url, library, folder)


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("http://contoso.sharepoint.com/sites/Review/Shared%20Documents", id="not_https"),
        pytest.param("https://contoso.sharepoint.com/sites/Review", id="site_without_library"),
        pytest.param("https://contoso.sharepoint.com", id="host_only"),
        pytest.param("not a url", id="garbage"),
    ],
)
def test_parse_destination_url_rejects_unusable_urls(url: str) -> None:
    with pytest.raises(GraphError):
        gc.parse_destination_url(url)


# --- destination resolution from a URL (no /shares/) ---------------------------


def _url_handler(drives: list[dict], folder_status: int = 200, site_status: int = 200):
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(str(request.url))
        path = request.url.path
        if path.endswith("/sites/contoso.sharepoint.com:/sites/Review"):
            return httpx.Response(site_status, json={"id": "site-1"} if site_status == 200 else {})
        if path.endswith("/sites/site-1/drives"):
            return httpx.Response(200, json={"value": drives})
        if "/drives/d1/root:" in str(request.url):
            return httpx.Response(folder_status, json={"id": "folder-1"})
        return httpx.Response(404)

    return handler, paths


_DOCS_DRIVE = {
    "id": "d1",
    "name": "Documents",
    "webUrl": "https://contoso.sharepoint.com/sites/Review/Shared%20Documents",
}
_OTHER_DRIVE = {"id": "d0", "name": "Other", "webUrl": "https://contoso.sharepoint.com/sites/Review/Other"}
_REVIEW_URL = "https://contoso.sharepoint.com/sites/Review/Shared%20Documents/Case%2012"


def test_resolve_destination_url_matches_library_by_weburl_not_display_name() -> None:
    handler, paths = _url_handler([_OTHER_DRIVE, _DOCS_DRIVE])
    graph, _ = _graph(handler)

    ref = asyncio.run(graph.resolve_destination_url(_REVIEW_URL))

    assert ref == FolderRef("d1", "folder-1")  # URL says "Shared Documents"; the drive is named "Documents"
    assert not any("/shares/" in p for p in paths)


def test_resolve_destination_url_unknown_library_raises() -> None:
    handler, _ = _url_handler([_OTHER_DRIVE])
    graph, _ = _graph(handler)
    with pytest.raises(GraphError, match="Shared Documents"):
        asyncio.run(graph.resolve_destination_url(_REVIEW_URL))


@pytest.mark.parametrize("status", [403, 404])
def test_resolve_destination_url_without_site_access_raises_clear_error(status: int) -> None:
    handler, _ = _url_handler([_DOCS_DRIVE], site_status=status)
    graph, _ = _graph(handler)
    with pytest.raises(GraphError, match="site"):
        asyncio.run(graph.resolve_destination_url(_REVIEW_URL))


def test_resolve_destination_url_creates_a_missing_folder_path() -> None:
    created: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/sites/contoso.sharepoint.com:/sites/Review"):
            return httpx.Response(200, json={"id": "site-1"})
        if path.endswith("/sites/site-1/drives"):
            return httpx.Response(200, json={"value": [_DOCS_DRIVE]})
        if request.method == "GET":
            return httpx.Response(404)
        created.append(json.loads(request.content))
        return httpx.Response(201, json={"id": f"made-{len(created)}"})

    graph, _ = _graph(handler)
    ref = asyncio.run(graph.resolve_destination_url(_REVIEW_URL))

    assert ref.drive_id == "d1"
    assert [c["name"] for c in created] == ["Case 12"]


# --- destination resolution (site + library + folder; no /shares/) -------------


def test_resolve_destination_walks_site_library_folder() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        paths.append(path)
        if path.endswith("/sites/contoso.sharepoint.com:/sites/Review"):
            return httpx.Response(200, json={"id": "site-1"})
        if path.endswith("/sites/site-1/drives"):
            return httpx.Response(
                200, json={"value": [{"id": "d0", "name": "Other"}, {"id": "d1", "name": "Discovery"}]}
            )
        if "/drives/d1/root:/Case%2012" in str(request.url) or "/drives/d1/root:/Case 12" in path:
            return httpx.Response(200, json={"id": "folder-1"})
        return httpx.Response(404)

    graph, _ = _graph(handler)
    ref = asyncio.run(graph.resolve_destination("https://contoso.sharepoint.com/sites/Review", "Discovery", "Case 12"))

    assert ref == FolderRef("d1", "folder-1")
    assert not any("/shares/" in p for p in paths)


def test_resolve_destination_unknown_library_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/drives"):
            return httpx.Response(200, json={"value": [{"id": "d0", "name": "Other"}]})
        return httpx.Response(200, json={"id": "site-1"})

    graph, _ = _graph(handler)
    with pytest.raises(GraphError, match="Discovery"):
        asyncio.run(graph.resolve_destination("https://contoso.sharepoint.com/sites/Review", "Discovery", ""))
