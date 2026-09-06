"""odenum — enumerate, download, and verify a OneDrive/SharePoint share link.

Purpose: produce a defensible, dated record of exactly what a discovery share
contained, and prove that what we downloaded matches it byte-for-byte.

Workflow:
    odenum enumerate <share-url>   # walk the share -> manifest.json (+ raw/)
    odenum download                # pull every file into ./download/
    odenum verify                  # recompute hashes, reconcile vs manifest
    odenum selftest <share-url>    # prove our QuickXorHash matches Graph's
    odenum diff <old> <new>        # what changed between two enumerations

Auth: delegated device-code sign-in against a well-known Microsoft public client,
so there is no Azure app registration to create. You sign in as yourself; the
tool requests read-only Files.Read.All. An "Anyone with the link" share resolves
for any signed-in account.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import datetime as dt
import json
import os
import sys
import time
from http import HTTPStatus
from pathlib import Path

import msal
import requests

from onedrive_enum import paths
from onedrive_enum.quickxor import QuickXorHash, hash_file

TOOL_VERSION = "0.1.0"
GRAPH = "https://graph.microsoft.com/v1.0"

# Well-known Microsoft public client (the Microsoft Graph PowerShell app). It is
# a first-party, multi-tenant public client that supports device-code flow and
# delegated Graph scopes, so we can sign in without registering our own app.
CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
AUTHORITY = "https://login.microsoftonline.com/common"
SCOPES = ["Files.Read.All"]

# Token cache lives in the per-user config dir (~/.odenum), not the project.
TOKEN_CACHE_PATH = paths.TOKEN_CACHE_PATH

SELECT = (
    "id,name,size,file,folder,package,createdDateTime,"
    "lastModifiedDateTime,eTag,cTag,webUrl,parentReference"
)


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
class Auth:
    """Delegated device-code auth with an on-disk token cache."""

    def __init__(self) -> None:
        self._cache = msal.SerializableTokenCache()
        if TOKEN_CACHE_PATH.exists():
            self._cache.deserialize(TOKEN_CACHE_PATH.read_text())
        self._app = msal.PublicClientApplication(
            CLIENT_ID, authority=AUTHORITY, token_cache=self._cache
        )

    def _save(self) -> None:
        if self._cache.has_state_changed:
            paths.ensure_config_dir()
            TOKEN_CACHE_PATH.write_text(self._cache.serialize())
            with contextlib.suppress(OSError):
                os.chmod(TOKEN_CACHE_PATH, 0o600)

    def token(self) -> str:
        accounts = self._app.get_accounts()
        result = None
        if accounts:
            result = self._app.acquire_token_silent(SCOPES, account=accounts[0])
        if not result:
            flow = self._app.initiate_device_flow(scopes=SCOPES)
            if "user_code" not in flow:
                raise RuntimeError(f"Failed to start device flow: {flow}")
            print("\n" + "=" * 60)
            print(flow["message"])  # "go to microsoft.com/devicelogin and enter CODE"
            print("=" * 60 + "\n", flush=True)
            result = self._app.acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            raise RuntimeError(
                f"Auth failed: {result.get('error')}: {result.get('error_description')}"
            )
        self._save()
        return str(result["access_token"])

    def whoami(self) -> str:
        accounts = self._app.get_accounts()
        return accounts[0]["username"] if accounts else "unknown"


# --------------------------------------------------------------------------- #
# Graph plumbing (pagination + throttling are where silent data loss hides)
# --------------------------------------------------------------------------- #
class Graph:
    def __init__(self, auth: Auth) -> None:
        self._auth = auth
        self._session = requests.Session()

    def get(self, url: str, stream: bool = False, max_retries: int = 8):
        for attempt in range(max_retries):
            token = self._auth.token()
            resp = self._session.get(
                url, headers={"Authorization": f"Bearer {token}"}, stream=stream
            )
            if resp.status_code in (429, 503, 504):
                wait = int(resp.headers.get("Retry-After", 2 ** attempt))
                print(f"  throttled ({resp.status_code}); waiting {wait}s", flush=True)
                time.sleep(wait)
                continue
            if resp.status_code == HTTPStatus.UNAUTHORIZED and attempt == 0:
                continue  # token may have just expired; loop re-acquires
            resp.raise_for_status()
            return resp
        raise RuntimeError(f"Giving up on {url} after {max_retries} retries")

    def get_json(self, url: str) -> dict:
        data: dict = self.get(url).json()
        return data


def encode_share_url(share_url: str) -> str:
    """Turn a sharing URL into the Graph share token: 'u!' + base64url, unpadded."""
    b64 = base64.urlsafe_b64encode(share_url.encode("utf-8")).decode("ascii")
    return "u!" + b64.rstrip("=")


# --------------------------------------------------------------------------- #
# Enumeration
# --------------------------------------------------------------------------- #
def _quickxor_of(item: dict):
    return (item.get("file") or {}).get("hashes", {}).get("quickXorHash")


def _sha1_of(item: dict):
    return (item.get("file") or {}).get("hashes", {}).get("sha1Hash")


def enumerate_share(share_url: str, out_dir: Path) -> dict:
    auth = Auth()
    graph = Graph(auth)
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    token = encode_share_url(share_url)
    root = graph.get_json(f"{GRAPH}/shares/{token}/driveItem?$select={SELECT}")
    (raw_dir / "root.json").write_text(json.dumps(root, indent=2))

    root_drive_id = (root.get("parentReference") or {}).get("driveId")
    root_id = root["id"]

    items: list[dict] = []
    counts = {"files": 0, "folders": 0, "total_bytes": 0, "no_hash": 0}

    # Explicit stack so arbitrarily deep trees can't blow the recursion limit.
    stack = [(root_drive_id, root_id, "")]
    raw_index = 0
    while stack:
        drive_id, item_id, base_path = stack.pop()
        url: str | None = (
            f"{GRAPH}/drives/{drive_id}/items/{item_id}/children"
            f"?$top=200&$select={SELECT}"
        )
        while url:
            page = graph.get_json(url)
            (raw_dir / f"children_{raw_index:05d}.json").write_text(
                json.dumps(page, indent=2)
            )
            raw_index += 1
            for child in page.get("value", []):
                path = f"{base_path}/{child['name']}" if base_path else child["name"]
                child_drive = (child.get("parentReference") or {}).get(
                    "driveId", drive_id
                )
                is_folder = "folder" in child
                rec = {
                    "path": path,
                    "type": "folder" if is_folder else "file",
                    "size": child.get("size", 0),
                    "id": child["id"],
                    "driveId": child_drive,
                    "created": child.get("createdDateTime"),
                    "modified": child.get("lastModifiedDateTime"),
                    "eTag": child.get("eTag"),
                    "cTag": child.get("cTag"),
                    "webUrl": child.get("webUrl"),
                }
                if is_folder:
                    rec["childCount"] = child["folder"].get("childCount")
                    counts["folders"] += 1
                    stack.append((child_drive, child["id"], path))
                else:
                    rec["quickXorHash"] = _quickxor_of(child)
                    rec["sha1Hash"] = _sha1_of(child)
                    counts["files"] += 1
                    counts["total_bytes"] += child.get("size", 0)
                    if not rec["quickXorHash"]:
                        counts["no_hash"] += 1
                items.append(rec)
            url = page.get("@odata.nextLink")
            print(
                f"  {counts['files']} files / {counts['folders']} folders "
                f"/ {counts['total_bytes']:,} bytes",
                end="\r",
                flush=True,
            )

    print()
    items.sort(key=lambda r: r["path"].lower())
    manifest = {
        "schema_version": 1,
        "tool": "odenum",
        "tool_version": TOOL_VERSION,
        "share_url": share_url,
        "enumerated_at_utc": dt.datetime.now(dt.UTC).isoformat(),
        "enumerated_by": auth.whoami(),
        "root": {"name": root.get("name"), "id": root_id, "driveId": root_drive_id},
        "counts": counts,
        "items": items,
    }
    return manifest


# --------------------------------------------------------------------------- #
# Download
# --------------------------------------------------------------------------- #
def download_all(manifest: dict, dest: Path) -> None:
    auth = Auth()
    graph = Graph(auth)
    files = [i for i in manifest["items"] if i["type"] == "file"]
    total = len(files)
    for n, item in enumerate(files, 1):
        target = dest / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        # Resume: skip if a good copy is already present.
        if target.exists() and item.get("quickXorHash") and hash_file(target) == item["quickXorHash"]:
            print(f"[{n}/{total}] have  {item['path']}")
            continue
        meta = graph.get_json(
            f"{GRAPH}/drives/{item['driveId']}/items/{item['id']}"
            f"?$select=id,@microsoft.graph.downloadUrl"
        )
        dl = meta.get("@microsoft.graph.downloadUrl")
        if not dl:
            print(f"[{n}/{total}] NO DOWNLOAD URL {item['path']}")
            continue
        print(f"[{n}/{total}] get   {item['path']} ({item['size']:,} B)")
        with graph._session.get(dl, stream=True) as r:
            r.raise_for_status()
            tmp = target.with_suffix(target.suffix + ".part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    f.write(chunk)
            tmp.replace(target)


# --------------------------------------------------------------------------- #
# Verify
# --------------------------------------------------------------------------- #
def verify(manifest: dict, dest: Path) -> int:
    ok = missing = mismatch = nohash = 0
    manifest_paths = set()
    print("Verifying downloaded files against manifest...\n")
    for item in manifest["items"]:
        if item["type"] != "file":
            continue
        manifest_paths.add(item["path"].replace("\\", "/"))
        target = dest / item["path"]
        if not target.exists():
            print(f"MISSING   {item['path']}")
            missing += 1
            continue
        expected = item.get("quickXorHash")
        if not expected:
            print(f"NO-HASH   {item['path']} (source gave no hash; size-only check)")
            nohash += 1
            if target.stat().st_size != item["size"]:
                print("          ...and SIZE MISMATCH")
                mismatch += 1
            continue
        actual = hash_file(target)
        if actual == expected:
            ok += 1
        else:
            print(f"MISMATCH  {item['path']}")
            print(f"          expected {expected}")
            print(f"          actual   {actual}")
            mismatch += 1

    # Files on disk that the manifest never listed.
    extra = 0
    if dest.exists():
        for p in dest.rglob("*"):
            if p.is_file() and p.suffix != ".part":
                rel = str(p.relative_to(dest)).replace("\\", "/")
                if rel not in manifest_paths:
                    print(f"EXTRA     {rel} (on disk, not in manifest)")
                    extra += 1

    print("\n" + "-" * 50)
    print(f"OK={ok}  MISSING={missing}  MISMATCH={mismatch}  "
          f"NO-HASH={nohash}  EXTRA={extra}")
    failed = missing + mismatch + extra
    print("RESULT:", "PASS ✓" if failed == 0 else f"FAIL ✗ ({failed} problems)")
    return 1 if failed else 0


# --------------------------------------------------------------------------- #
# Self-test: prove our QuickXorHash equals Graph's on a real file
# --------------------------------------------------------------------------- #
def selftest(share_url: str, max_bytes: int = 25 * 1024 * 1024) -> int:
    auth = Auth()
    graph = Graph(auth)
    token = encode_share_url(share_url)
    root = graph.get_json(f"{GRAPH}/shares/{token}/driveItem")
    drive_id = root["parentReference"]["driveId"]

    # Find the smallest file that has a quickXorHash.
    best = None
    stack = [root["id"]]
    while stack and best is None:
        item_id = stack.pop()
        url: str | None = f"{GRAPH}/drives/{drive_id}/items/{item_id}/children?$top=200&$select={SELECT}"
        while url:
            page = graph.get_json(url)
            for c in page.get("value", []):
                if "folder" in c:
                    stack.append(c["id"])
                elif (
                    _quickxor_of(c)
                    and 0 < c.get("size", 0) <= max_bytes
                    and (best is None or c["size"] < best["size"])
                ):
                    best = c
            url = page.get("@odata.nextLink")

    if not best:
        print("No suitably small hashed file found to self-test against.")
        return 2

    expected = _quickxor_of(best)
    meta = graph.get_json(
        f"{GRAPH}/drives/{drive_id}/items/{best['id']}?$select=@microsoft.graph.downloadUrl"
    )
    print(f"Self-testing on: {best['name']} ({best['size']:,} B)")
    h = QuickXorHash()
    with graph._session.get(meta["@microsoft.graph.downloadUrl"], stream=True) as r:
        r.raise_for_status()
        for chunk in r.iter_content(chunk_size=1024 * 1024):
            h.update(chunk)
    actual = h.base64digest()
    print(f"  Graph quickXorHash: {expected}")
    print(f"  our  quickXorHash: {actual}")
    if actual == expected:
        print("SELF-TEST PASSED ✓  verify results can be trusted.")
        return 0
    print("SELF-TEST FAILED ✗  do NOT trust verify until this matches.")
    return 1


# --------------------------------------------------------------------------- #
# Diff two manifests (detect files added/changed/removed later)
# --------------------------------------------------------------------------- #
def diff_manifests(old: dict, new: dict) -> int:
    def index(m):
        return {i["path"]: i for i in m["items"] if i["type"] == "file"}

    a, b = index(old), index(new)
    added = sorted(set(b) - set(a))
    removed = sorted(set(a) - set(b))
    changed = sorted(
        p for p in set(a) & set(b)
        if a[p].get("quickXorHash") != b[p].get("quickXorHash")
        or a[p].get("eTag") != b[p].get("eTag")
    )
    print(f"OLD {old['enumerated_at_utc']}  ->  NEW {new['enumerated_at_utc']}\n")
    for p in added:
        print(f"ADDED    {p}")
    for p in removed:
        print(f"REMOVED  {p}")
    for p in changed:
        print(f"CHANGED  {p}")
    print(f"\n+{len(added)} added  -{len(removed)} removed  ~{len(changed)} changed")
    return 1 if (added or removed or changed) else 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _load(path: Path) -> dict:
    data: dict = json.loads(path.read_text(encoding="utf-8"))
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description="Enumerate/download/verify a share link.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("enumerate", help="walk the share into a manifest")
    e.add_argument("share_url")
    e.add_argument("-o", "--out", default="manifest.json")

    d = sub.add_parser("download", help="download every file in the manifest")
    d.add_argument("-m", "--manifest", default="manifest.json")
    d.add_argument("-d", "--dest", default="download")

    v = sub.add_parser("verify", help="reconcile downloaded files vs manifest")
    v.add_argument("-m", "--manifest", default="manifest.json")
    v.add_argument("-d", "--dest", default="download")

    s = sub.add_parser("selftest", help="prove our hash matches Graph's on a real file")
    s.add_argument("share_url")

    f = sub.add_parser("diff", help="compare two manifests")
    f.add_argument("old")
    f.add_argument("new")

    args = ap.parse_args()

    if args.cmd == "enumerate":
        out = Path(args.out)
        manifest = enumerate_share(args.share_url, out.parent if out.parent != Path("") else Path("."))
        out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        c = manifest["counts"]
        print(f"\nWrote {out}: {c['files']} files, {c['folders']} folders, "
              f"{c['total_bytes']:,} bytes.")
        if c["no_hash"]:
            print(f"NOTE: {c['no_hash']} file(s) had no source hash "
                  f"(will be size-only checked by verify).")
        return 0

    if args.cmd == "download":
        download_all(_load(Path(args.manifest)), Path(args.dest))
        return 0

    if args.cmd == "verify":
        return verify(_load(Path(args.manifest)), Path(args.dest))

    if args.cmd == "selftest":
        return selftest(args.share_url)

    if args.cmd == "diff":
        return diff_manifests(_load(Path(args.old)), _load(Path(args.new)))

    return 2


if __name__ == "__main__":
    sys.exit(main())
