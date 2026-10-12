"""graphclient - Microsoft Graph access for the SharePoint upload (destination side only, ADR-0012).

App-only (client-credentials) auth against the destination tenant, plus the handful of Graph calls the
upload needs: item lookup, a simple PUT, resumable upload sessions, and folder creation. The destination is
a site + document library + folder path (``resolve_destination``), or a pasted folder URL parsed into those
three (``resolve_destination_url``) - never Graph's ``/shares/`` endpoint.

The request/retry logic is copied from the sibling ``courier`` project; the auth differs (courier signs a
user in interactively, here the tool holds an app registration with ``Sites.Selected``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

import httpx

from aqueduct.errors import AuthError, GraphError, UploadError
from aqueduct.paths import GRAPH_CONFIG_PATH

log = logging.getLogger("graphclient")

SECRET_ENV_VAR = "AQUEDUCT_GRAPH_CLIENT_SECRET"
_DEFAULT_AUTHORITY_HOST = "https://login.microsoftonline.com"
_DEFAULT_GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
_GRAPH_SCOPE = "https://graph.microsoft.com/.default"
_DEFAULT_RETRY_AFTER = 10  # seconds, when Graph throttles without a Retry-After header
_MAX_SEND_ATTEMPTS = 6  # one try, one token refresh, and a few throttled retries
_TOKEN_TIMEOUT = 30.0
_SITE_ROOTS = ("sites", "teams")
_SITE_SEGMENTS = 2  # "/sites/<name>"


@dataclass(frozen=True)
class GraphConfig:
    """Destination-tenant app registration. ``client_secret`` is a credential: never log or print it."""

    tenant_id: str
    client_id: str
    client_secret: str
    authority_host: str = _DEFAULT_AUTHORITY_HOST
    graph_base_url: str = _DEFAULT_GRAPH_BASE_URL

    def __repr__(self) -> str:
        return f"GraphConfig(tenant_id={self.tenant_id!r}, client_id={self.client_id!r}, client_secret='***')"


@dataclass(frozen=True)
class DriveItemMeta:
    """The subset of a Graph driveItem this tool acts on."""

    name: str
    size: int
    quick_xor_hash: str | None
    download_url: str | None
    item_id: str = ""
    drive_id: str = ""


@dataclass(frozen=True)
class FolderRef:
    """A drive folder addressable for creating children."""

    drive_id: str
    item_id: str


@dataclass(frozen=True)
class DestinationUrl:
    """A pasted SharePoint folder URL split into the three things Graph needs."""

    site_url: str
    library: str
    folder: str


def load_config(path: Path = GRAPH_CONFIG_PATH, env: Mapping[str, str] | None = None) -> GraphConfig:
    """Read the app registration from ``path``; the secret may come from ``$AQUEDUCT_GRAPH_CLIENT_SECRET``."""
    environ = os.environ if env is None else env
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthError(f"No usable Graph config at {path} (see ADR-0012): {type(exc).__name__}") from exc
    tenant_id = str(raw.get("tenant_id", "")).strip()
    client_id = str(raw.get("client_id", "")).strip()
    secret = environ.get(SECRET_ENV_VAR, "").strip() or str(raw.get("client_secret", "")).strip()
    for field, value in (("tenant_id", tenant_id), ("client_id", client_id)):
        if not value:
            raise AuthError(f"Graph config {path} is missing {field}")
    if not secret:
        raise AuthError(f"No client secret: set {SECRET_ENV_VAR} or client_secret in {path}")
    return GraphConfig(
        tenant_id,
        client_id,
        secret,
        authority_host=str(raw.get("authority_host", _DEFAULT_AUTHORITY_HOST)),
        graph_base_url=str(raw.get("graph_base_url", _DEFAULT_GRAPH_BASE_URL)),
    )


class ClientCredentialsTokenProvider:
    """Acquires an app-only Graph access token from the destination tenant's token endpoint."""

    def __init__(self, config: GraphConfig, http: httpx.Client | None = None) -> None:
        self._config = config
        self._http = http or httpx.Client(timeout=_TOKEN_TIMEOUT)

    def token(self) -> str:
        """Return a fresh bearer token (callers refresh by calling again)."""
        cfg = self._config
        url = f"{cfg.authority_host.rstrip('/')}/{cfg.tenant_id}/oauth2/v2.0/token"
        form = {
            "grant_type": "client_credentials",
            "client_id": cfg.client_id,
            "client_secret": cfg.client_secret,
            "scope": _GRAPH_SCOPE,
        }
        resp = self._http.post(url, data=form)
        payload = _json_or_empty(resp)
        token = payload.get("access_token")
        if resp.status_code != HTTPStatus.OK or not token:
            detail = payload.get("error_description") or payload.get("error") or f"HTTP {resp.status_code}"
            raise AuthError(f"Could not acquire a Graph token: {detail}")
        return str(token)


def _json_or_empty(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _retry_after_seconds(resp: httpx.Response) -> int:
    raw = resp.headers.get("Retry-After", "")
    return int(raw) if raw.isdigit() else _DEFAULT_RETRY_AFTER


def parse_destination_url(url: str) -> DestinationUrl:
    """Split a SharePoint folder URL into site URL, document library, and folder path.

    The first path segments are the site (``/sites/<x>``, ``/teams/<x>``, or the root site), the next is the
    library, the rest is the folder. A browser URL carrying ``?id=<server-relative path>`` is honored.
    """
    parts = urlsplit(url.strip())
    if parts.scheme != "https" or not parts.netloc:
        raise GraphError(f"Not an https SharePoint URL: {url!r}")
    path = parse_qs(parts.query).get("id", [parts.path])[0]
    segments = [unquote(s) for s in path.split("/") if s]
    site_path = ""
    if segments and segments[0].lower() in _SITE_ROOTS:
        site_path = "/" + "/".join(segments[:_SITE_SEGMENTS])
        segments = segments[_SITE_SEGMENTS:]
    if not segments:
        raise GraphError(f"No document library in URL {url!r} (expected <site>/<library>[/<folder>])")
    return DestinationUrl(f"https://{parts.netloc}{site_path}", segments[0], "/".join(segments[1:]))


class GraphClient:
    """Authorized Graph calls: refreshes the token once on 401 and backs off on 429."""

    def __init__(self, client: httpx.AsyncClient, tokens: ClientCredentialsTokenProvider, base_url: str) -> None:
        self._client = client
        self._tokens = tokens
        self._base_url = base_url.rstrip("/")
        self._bearer: str | None = None
        self._auth_lock = asyncio.Lock()

    async def _authorize(self, force: bool = False) -> str:
        if not force and self._bearer is not None:
            return self._bearer
        async with self._auth_lock:  # concurrent tasks trigger one token request
            if force or self._bearer is None:
                self._bearer = await asyncio.to_thread(self._tokens.token)
            return self._bearer

    async def _send(
        self,
        method: str,
        endpoint: str,
        json_body: dict | None = None,
        content: bytes | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Authorized request with one token refresh on 401 and ``Retry-After`` back-off on 429."""
        refreshed = False
        for _attempt in range(_MAX_SEND_ATTEMPTS):
            bearer = await self._authorize()
            headers = {"Authorization": f"Bearer {bearer}", **(extra_headers or {})}
            resp = await self._client.request(method, endpoint, headers=headers, json=json_body, content=content)
            if resp.status_code == HTTPStatus.UNAUTHORIZED:
                if refreshed:
                    raise GraphError(f"{method} {endpoint} is still unauthorized after a token refresh")
                refreshed = True
                await self._authorize(force=True)
            elif resp.status_code == HTTPStatus.TOO_MANY_REQUESTS:
                await asyncio.sleep(_retry_after_seconds(resp))
            else:
                return resp
        raise GraphError(f"{method} {endpoint} still throttled after {_MAX_SEND_ATTEMPTS} attempts")

    def _item_by_path(self, parent: FolderRef, name: str) -> str:
        return f"{self._base_url}/drives/{parent.drive_id}/items/{parent.item_id}:/{quote(name)}"

    def _child_lookup_url(self, parent: FolderRef, name: str) -> str:
        """Address for a bare metadata GET of child ``name``.

        A bare ``items/{id}:/{name}`` collides when ``name`` matches a reserved driveItem facet (``audio``,
        ``video``, ``photo``, ``content``, ``children``, ...): Graph returns that facet instead of the child.
        A trailing ``:`` terminates the path so the segment is always read as a child name.
        """
        return f"{self._item_by_path(parent, name)}:"

    async def child_meta(self, parent: FolderRef, name: str) -> DriveItemMeta | None:
        """Return the driveItem metadata (size + QuickXorHash) of child ``name``, or None."""
        resp = await self._send("GET", self._child_lookup_url(parent, name))
        if resp.status_code == HTTPStatus.NOT_FOUND:
            return None
        if resp.status_code != HTTPStatus.OK:
            raise UploadError(f"Could not look up '{name}': {resp.status_code} {resp.text[:200]}")
        return _parse_drive_item(_json_or_empty(resp))

    async def upload_small(self, dest: FolderRef, name: str, content: bytes) -> DriveItemMeta:
        """Upload a small file in a single ``PUT``, returning the created item's metadata."""
        endpoint = f"{self._item_by_path(dest, name)}:/content"
        headers = {"Content-Type": "application/octet-stream"}
        resp = await self._send("PUT", endpoint, content=content, extra_headers=headers)
        if resp.status_code not in (HTTPStatus.OK, HTTPStatus.CREATED):
            raise UploadError(f"Upload of '{name}' failed: {resp.status_code} {resp.text[:200]}")
        return _parse_drive_item(_json_or_empty(resp))

    async def create_upload_session(self, dest: FolderRef, name: str) -> str:
        """Open a resumable upload session for ``name`` under ``dest``; return its (pre-authenticated) URL."""
        endpoint = f"{self._item_by_path(dest, name)}:/createUploadSession"
        body = {"item": {"@microsoft.graph.conflictBehavior": "replace"}}
        resp = await self._send("POST", endpoint, body)
        if resp.status_code != HTTPStatus.OK:
            raise UploadError(f"Could not open upload session for '{name}': {resp.status_code} {resp.text[:200]}")
        upload_url = _json_or_empty(resp).get("uploadUrl")
        if not upload_url:
            raise UploadError(f"Upload session for '{name}' returned no uploadUrl")
        return str(upload_url)

    async def ensure_child_folder(self, parent: FolderRef, name: str) -> FolderRef:
        """Return the child folder ``name`` under ``parent``, creating it if absent."""
        return await self._ensure_folder(parent.drive_id, parent.item_id, name)

    async def _ensure_folder(self, drive_id: str, parent_id: str | None, name: str) -> FolderRef:
        """Find or create folder ``name`` under ``parent_id`` (``None`` = the drive root)."""
        existing = await self._find_folder(drive_id, parent_id, name)
        if existing is not None:
            return existing
        children = self._children_url(drive_id, parent_id)
        body = {"name": name, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"}
        resp = await self._send("POST", children, body)
        if resp.status_code == HTTPStatus.CONFLICT:  # created by a concurrent task
            created = await self._find_folder(drive_id, parent_id, name)
            if created is not None:
                return created
        if resp.status_code not in (HTTPStatus.CREATED, HTTPStatus.OK):
            raise GraphError(f"Could not create folder '{name}': {resp.status_code} {resp.text[:200]}")
        return FolderRef(drive_id, str(_json_or_empty(resp)["id"]))

    def _children_url(self, drive_id: str, parent_id: str | None) -> str:
        parent = "root" if parent_id is None else f"items/{parent_id}"
        return f"{self._base_url}/drives/{drive_id}/{parent}/children"

    async def _find_folder(self, drive_id: str, parent_id: str | None, name: str) -> FolderRef | None:
        anchor = "root" if parent_id is None else f"items/{parent_id}"
        url = f"{self._base_url}/drives/{drive_id}/{anchor}:/{quote(name)}:"
        resp = await self._send("GET", url)
        if resp.status_code == HTTPStatus.NOT_FOUND:
            return None
        if resp.status_code != HTTPStatus.OK:
            raise GraphError(f"Could not look up folder '{name}': {resp.status_code} {resp.text[:200]}")
        return FolderRef(drive_id, str(_json_or_empty(resp)["id"]))

    async def resolve_destination_url(self, url: str) -> FolderRef:
        """Resolve a pasted SharePoint folder URL (parsed locally, no ``/shares/`` call)."""
        target = parse_destination_url(url)
        return await self.resolve_destination(target.site_url, target.library, target.folder)

    async def resolve_destination(self, site_url: str, library: str, folder: str = "") -> FolderRef:
        """Resolve site + document library + folder path to a folder, creating the folders if absent."""
        site_id = await self._site_id(site_url)
        drive_id = await self._drive_id(site_id, library, site_url)
        if not folder.strip("/"):
            return await self._drive_root(drive_id)
        parent: str | None = None
        for part in (p for p in folder.split("/") if p):
            parent = (await self._ensure_folder(drive_id, parent, part)).item_id
        return FolderRef(drive_id, str(parent))

    async def _site_id(self, site_url: str) -> str:
        parts = urlsplit(site_url)
        site_path = parts.path.rstrip("/")
        endpoint = f"{self._base_url}/sites/{parts.netloc}" + (f":{site_path}" if site_path else "")
        resp = await self._send("GET", endpoint)
        if resp.status_code != HTTPStatus.OK:
            raise GraphError(
                f"Cannot access SharePoint site {site_url} ({resp.status_code}): check the URL and the app's grant"
            )
        return str(_json_or_empty(resp)["id"])

    async def _drive_id(self, site_id: str, library: str, site_url: str) -> str:
        resp = await self._send("GET", f"{self._base_url}/sites/{site_id}/drives")
        if resp.status_code != HTTPStatus.OK:
            raise GraphError(f"Could not list document libraries of {site_url}: {resp.status_code}")
        wanted = library.casefold()
        for drive in _json_or_empty(resp).get("value", []):
            if wanted in _drive_names(drive):
                return str(drive["id"])
        raise GraphError(f"Document library '{library}' not found on {site_url}")

    async def _drive_root(self, drive_id: str) -> FolderRef:
        resp = await self._send("GET", f"{self._base_url}/drives/{drive_id}/root")
        if resp.status_code != HTTPStatus.OK:
            raise GraphError(f"Could not open the library root: {resp.status_code} {resp.text[:200]}")
        return FolderRef(drive_id, str(_json_or_empty(resp)["id"]))


def _drive_names(drive: dict) -> set[str]:
    """Names a library answers to: its display name and the last segment of its URL (e.g. 'Shared Documents')."""
    names = {str(drive.get("name", "")).casefold()}
    web_url = str(drive.get("webUrl", "")).rstrip("/")
    if web_url:
        names.add(unquote(web_url.rsplit("/", 1)[-1]).casefold())
    return names


def _parse_drive_item(payload: dict) -> DriveItemMeta:
    try:
        hashes = payload.get("file", {}).get("hashes", {})
        parent = payload.get("parentReference") or {}
        return DriveItemMeta(
            name=payload["name"],
            size=int(payload["size"]),
            quick_xor_hash=hashes.get("quickXorHash"),
            download_url=payload.get("@microsoft.graph.downloadUrl"),
            item_id=payload.get("id", ""),
            drive_id=parent.get("driveId", ""),
        )
    except (KeyError, TypeError, ValueError) as err:
        raise GraphError(f"Unexpected driveItem shape: {json.dumps(payload)[:200]}") from err
