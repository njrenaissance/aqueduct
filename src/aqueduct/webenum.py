"""webenum — enumerate a OneDrive/SharePoint "specific people" share via the
authenticated *web* session, for shares that resolve in a browser but 403 on the
Graph API.

Why this exists:
    Some SharePoint/OneDrive shares are granted to specific people (or guests)
    in a way that authorizes only the interactive web session — the redeemed
    FedAuth/SPO cookies — and NOT a delegated Graph token from a third-party
    public client. Those shares open fine in a browser but return 403 from
    GET /v1.0/shares/{token}/driveItem. rclone hits the same wall (it is also a
    Graph client). This tool rides the browser session instead.

Sign in first with login.py (headed; saves auth_state.json); everything here is
headless and reuses that saved session:
    login <share-url>               # HEADED once; sign in; saves the session + URL
    webenum enumerate               # HEADLESS; walk the share -> manifest.json + manifest.csv
    webenum discover                # HEADLESS diagnostic; log every API the page calls -> raw/
    webenum csv -m manifest.json    # regenerate the CSV from a manifest

`enumerate` assumes a personal OneDrive share: the document library is named
"Documents" and the target folder arrives as the `id=` param on an onedrive.aspx
URL. If a *different* kind of share (e.g. a SharePoint team site, or a renamed
library) 403s or returns nothing, run `discover` first to see the real endpoint
and list path the web UI actually uses, then adapt enumerate. That is what
`discover` is for; keep it.

The saved session (~/.aqueduct/auth_state.json) is as sensitive as a password — it is
a live logged-in session for the recipient account. It lives outside the repo;
re-run login.py when it expires.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from playwright.sync_api import sync_playwright

from aqueduct import paths, shareurl

# Auth session lives in ~/.aqueduct; manifests and raw/ stay in the working directory.
AUTH_STATE_PATH = paths.AUTH_STATE_PATH
TOOL_VERSION = "0.1.0"

# URL substrings that mark an internal listing/data API worth capturing during
# discovery. Kept broad on purpose — discovery is where we learn the truth.
API_MARKERS = (
    "_api/",
    "RenderListDataAsStream",
    "/_vti_bin/",
    "/drives/",
    "/drive/",
    "graph.microsoft.com",
    "spo",
    "GetFolderByServerRelativeUrl",
    "GetListItems",
)


# --------------------------------------------------------------------------- #
# discover — headless, learns which internal API actually returns the listing
# --------------------------------------------------------------------------- #
def discover(share_url: str) -> int:
    if not AUTH_STATE_PATH.exists():
        print(f"No {AUTH_STATE_PATH.name}. Run 'login' first.", file=sys.stderr)
        return 2

    raw_dir = Path("raw")
    raw_dir.mkdir(exist_ok=True)
    captured: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(AUTH_STATE_PATH))
        page = context.new_page()

        def on_response(resp):
            url = resp.url
            if not any(m in url for m in API_MARKERS):
                return
            ct = resp.headers.get("content-type", "")
            entry = {
                "url": url,
                "method": resp.request.method,
                "status": resp.status,
                "content_type": ct,
            }
            if "json" in ct:
                try:
                    body = resp.json()
                    entry["json_keys"] = list(body)[:40] if isinstance(body, dict) else "<array>"
                    idx = len(captured)
                    (raw_dir / f"disc_{idx:03d}.json").write_text(
                        json.dumps(body, indent=2)[:2_000_000], encoding="utf-8"
                    )
                    entry["saved"] = f"disc_{idx:03d}.json"
                except Exception as exc:  # noqa: BLE001 - discovery is best-effort
                    entry["json_error"] = str(exc)
            captured.append(entry)

        page.on("response", on_response)
        print("Navigating headless to the share as saved session...", flush=True)
        page.goto(share_url, wait_until="domcontentloaded")
        # Let the SPA fire its data calls and settle.
        try:
            page.wait_for_load_state("networkidle", timeout=45_000)
        except Exception:  # noqa: BLE001
            print("  (networkidle timed out; capturing what loaded)")
        # Nudge lazy-loaded rows.
        for _ in range(6):
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(800)

        final_url = page.url
        title = page.title()
        browser.close()

    (raw_dir / "discovery_index.json").write_text(
        json.dumps(
            {"share_url": share_url, "final_url": final_url, "title": title, "responses": captured},
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nLanded on: {final_url}\nTitle: {title}")
    print(f"Captured {len(captured)} API responses -> raw/discovery_index.json (+ disc_*.json bodies)")
    if not captured:
        print("Nothing captured. If final_url is a login page, the saved session expired — re-run 'login'.")
    return 0


# --------------------------------------------------------------------------- #
# enumerate — headless; walk the whole subtree via RenderListDataAsStream
# --------------------------------------------------------------------------- #
# RecursiveAll returns every descendant (files + folders, all depths) of the
# RootFolder in paged chunks. We drive it with the browser's own cookies, so the
# grant that 403s the Graph API is honored here.
_VIEW_XML = (
    "<View Scope='RecursiveAll'>"
    "<Query><OrderBy><FieldRef Name='FileRef'/></OrderBy></Query>"
    "<RowLimit Paged='TRUE'>{row_limit}</RowLimit>"
    "</View>"
)


def _resolve_context(page) -> dict:
    """From the resolved OneDrive URL, derive the site web, list, and folder."""
    parsed = urlparse(page.url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    # Path is like /personal/<user>/_layouts/15/onedrive.aspx
    site_path = parsed.path.split("/_layouts/")[0]  # /personal/<user>
    qs = parse_qs(parsed.query)
    folder = qs.get("id", [None])[0]  # server-relative folder, already decoded
    if not folder:
        raise RuntimeError(f"Could not find folder id in resolved URL: {page.url}")
    return {
        "origin": origin,
        "web_url": origin + site_path,
        "site_path": site_path,
        "docs_decoded": site_path + "/Documents",
        "folder": folder,
    }


def _form_digest(page, web_url: str) -> str:
    resp = page.request.post(
        f"{web_url}/_api/contextinfo",
        headers={"Accept": "application/json;odata=nometadata"},
    )
    if not resp.ok:
        raise RuntimeError(f"contextinfo failed: {resp.status} {resp.text()[:300]}")
    return str(resp.json()["FormDigestValue"])


def _render_base(ctx: dict) -> str:
    """Endpoint carrying only @a1 (the list identity). RootFolder and paging
    params are added per-request: from us on page 1, from NextHref after that."""
    a1 = quote(f"'{ctx['docs_decoded']}'", safe="")
    return f"{ctx['web_url']}/_api/web/GetListUsingPath(DecodedUrl=@a1)/RenderListDataAsStream?@a1={a1}"


def _fetch_all_rows(page, ctx: dict, raw_dir: Path, row_limit: int) -> list[dict]:
    digest = _form_digest(page, ctx["web_url"])
    base = _render_base(ctx)
    first_params = f"&RootFolder={quote(ctx['folder'], safe='')}&TryNewExperienceSingle=TRUE"
    body = {
        "parameters": {
            "RenderOptions": 2,  # ListData
            "ViewXml": _VIEW_XML.format(row_limit=row_limit),
            "AllowMultipleValueFilterForTaxonomyFields": True,
        }
    }
    headers = {
        "Accept": "application/json;odata=nometadata",
        "Content-Type": "application/json;odata=nometadata",
        "X-RequestDigest": digest,
    }
    rows: list[dict] = []
    url: str | None = base + first_params
    page_idx = 0
    while url:
        resp = page.request.post(url, headers=headers, data=json.dumps(body))
        if not resp.ok:
            raise RuntimeError(f"RenderListDataAsStream failed: {resp.status} {resp.text()[:300]}")
        data = resp.json()
        (raw_dir / f"page_{page_idx:04d}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        # With RenderOptions=2 the payload IS the ListData object (rows at the
        # top level); with a richer bitmask it is nested under "ListData".
        ld = data.get("ListData", data)
        rows.extend(ld.get("Row", []))
        nxt = ld.get("NextHref")
        print(f"  page {page_idx}: {len(rows)} rows so far", flush=True)
        page_idx += 1
        url = f"{base}&{nxt.lstrip('?&')}" if nxt else None
    return rows


def _created_iso(raw: str | None):
    # Created_x0020_Date looks like '0;#2026-08-12 14:41:42' (no timezone).
    if not raw or "#" not in raw:
        return raw
    return raw.split("#", 1)[1].replace(" ", "T")


def _row_to_item(row: dict, folder_prefix: str, origin: str) -> dict:
    file_ref = row.get("FileRef", "")
    rel = file_ref[len(folder_prefix) :] if file_ref.startswith(folder_prefix) else file_ref
    is_folder = row.get("FSObjType") == "1"
    size_str = row.get("File_x0020_Size") or ("" if is_folder else "0")
    rec = {
        "path": rel,
        "type": "folder" if is_folder else "file",
        "size": int(size_str) if size_str.isdigit() else 0,
        "id": row.get("UniqueId"),
        "fileRef": file_ref,
        "created": _created_iso(row.get("Created_x0020_Date")),
        "modified": row.get("Modified.") or row.get("Modified"),
        # RenderOptions=2 omits .etag/.spItemUrl; keep them when a richer view is
        # used, and always record the version identity fields that ARE present.
        "eTag": row.get(".etag"),
        "cTag": row.get(".ctag"),
        "version": row.get("_UIVersionString") or row.get("owshiddenversion"),
        "guid": row.get("GUID"),
        "listItemId": row.get("ID"),
        "contentTypeId": row.get("ContentTypeId") or None,
        "webUrl": origin + file_ref,
        "spItemUrl": row.get(".spItemUrl") or None,
    }
    if is_folder:
        cc = row.get("ItemChildCount")
        rec["childCount"] = int(cc) if cc and cc.isdigit() else None
        rec["aggregateSize"] = int(row["SMTotalSize"]) if (row.get("SMTotalSize") or "").isdigit() else None
    else:
        # This listing does not expose a content hash; verify falls back to size.
        rec["quickXorHash"] = None
        rec["sha1Hash"] = None
    return rec


def enumerate_share(share_url: str, out_path: Path, row_limit: int) -> int:
    if not AUTH_STATE_PATH.exists():
        print(f"No {AUTH_STATE_PATH.name}. Run 'login' first.", file=sys.stderr)
        return 2
    # Echo the URL up front so a stale/wrong ONEDRIVE_SHARE_URL is caught BEFORE we
    # produce a manifest for the wrong share.
    print(f"Enumerating share: {share_url}", flush=True)
    raw_dir = out_path.parent / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(AUTH_STATE_PATH))
        page = context.new_page()
        print("Resolving share (headless)...", flush=True)
        page.goto(share_url, wait_until="domcontentloaded")
        with contextlib.suppress(Exception):  # networkidle is best-effort
            page.wait_for_load_state("networkidle", timeout=45_000)
        ctx = _resolve_context(page)
        if "/_layouts/" not in page.url and "onedrive.aspx" not in page.url:
            print(f"WARNING: resolved to {page.url} — session may have expired (re-run login).")
        print(f"Site: {ctx['web_url']}\nFolder: {ctx['folder']}", flush=True)
        rows = _fetch_all_rows(page, ctx, raw_dir, row_limit)
        try:
            me = page.request.get(
                f"{ctx['web_url']}/_api/web/currentuser?$select=Email,LoginName",
                headers={"Accept": "application/json;odata=nometadata"},
            )
            whoami = me.json().get("Email") or me.json().get("LoginName") if me.ok else "web-session"
        except Exception:  # noqa: BLE001
            whoami = "web-session"
        browser.close()

    folder_prefix = ctx["folder"].rstrip("/") + "/"
    items = [_row_to_item(r, folder_prefix, ctx["origin"]) for r in rows]
    items.sort(key=lambda r: r["path"].lower())
    counts = {"files": 0, "folders": 0, "total_bytes": 0, "no_hash": 0}
    for it in items:
        if it["type"] == "folder":
            counts["folders"] += 1
        else:
            counts["files"] += 1
            counts["total_bytes"] += it["size"]
            counts["no_hash"] += 1  # no source hash available from this endpoint
    import datetime as dt  # local import keeps top clean; only needed here

    manifest = {
        "schema_version": 1,
        "tool": "webenum",
        "tool_version": TOOL_VERSION,
        "source": "web-session (RenderListDataAsStream)",
        "share_url": share_url,
        "enumerated_at_utc": dt.datetime.now(dt.UTC).isoformat(),
        "enumerated_by": whoami,
        "root": {"name": ctx["folder"].rsplit("/", 1)[-1], "fileRef": ctx["folder"], "webUrl": ctx["web_url"]},
        "counts": counts,
        "items": items,
    }
    out_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    csv_path = out_path.with_suffix(".csv")
    write_csv(manifest, csv_path)
    print(f"\nWrote {out_path}: {counts['files']} files, {counts['folders']} folders, {counts['total_bytes']:,} bytes.")
    print(f"Wrote {csv_path} ({len(items)} rows).")
    print("NOTE: this source exposes no per-file hash; verify will be size-only.")
    return 0


# --------------------------------------------------------------------------- #
# CSV export (path + size at minimum, plus useful evidence columns)
# --------------------------------------------------------------------------- #
_CSV_COLUMNS = [
    "path",  # complete path within the share (all folders)
    "size_bytes",
    "type",  # file | folder
    "modified_utc",
    "created",
    "version",  # SharePoint _UIVersionString (version token in lieu of eTag)
    "unique_id",
    "guid",
    "web_url",
    "full_path",  # server-relative FileRef (absolute within the site)
]


def _provenance_lines(manifest: dict) -> list[str]:
    """`#`-prefixed comment lines that make the CSV self-identifying as evidence:
    which share, enumerated when, by whom. Readers that honor '#' comments skip
    them; the real header/data follow untouched."""
    c = manifest.get("counts", {})
    return [
        "# aqueduct manifest (provenance; full record in manifest.json)",
        f"# share_url: {manifest.get('share_url', '')}",
        f"# enumerated_at_utc: {manifest.get('enumerated_at_utc', '')}",
        f"# enumerated_by: {manifest.get('enumerated_by', '')}",
        f"# tool: {manifest.get('tool', '')} {manifest.get('tool_version', '')}  source: {manifest.get('source', '')}",
        f"# counts: {c.get('files', '?')} files, {c.get('folders', '?')} folders, {c.get('total_bytes', '?')} bytes",
    ]


def write_csv(manifest: dict, csv_path: Path) -> int:
    # utf-8-sig so Excel on Windows reads the (often non-ASCII) paths correctly.
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
        for line in _provenance_lines(manifest):
            fh.write(line + "\r\n")  # csv module uses \r\n; match it for the comments
        w = csv.writer(fh)
        w.writerow(_CSV_COLUMNS)
        for it in manifest["items"]:
            w.writerow(
                [
                    it["path"],
                    it["size"],
                    it["type"],
                    it.get("modified"),
                    it.get("created"),
                    it.get("version"),
                    it.get("id"),
                    it.get("guid"),
                    it.get("webUrl"),
                    it.get("fileRef"),
                ]
            )
    return len(manifest["items"])


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="Enumerate a web-only OneDrive/SharePoint share.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    # share_url is optional: it defaults to $ONEDRIVE_SHARE_URL, saved by login.py.
    url_help = f"share URL (default: ${shareurl.ENV}, set by login.py)"
    ds = sub.add_parser("discover", help="headless; log the internal listing APIs")
    ds.add_argument("share_url", nargs="?", help=url_help)

    en = sub.add_parser("enumerate", help="headless; walk the share into a manifest (+CSV)")
    en.add_argument("share_url", nargs="?", help=url_help)
    en.add_argument("-o", "--out", default="manifest.json")
    en.add_argument("--row-limit", type=int, default=1000, help="rows per page (default 1000)")

    cv = sub.add_parser("csv", help="write a CSV from an existing manifest.json")
    cv.add_argument("-m", "--manifest", default="manifest.json")
    cv.add_argument("-o", "--out", default=None, help="CSV path (default: manifest name + .csv)")

    args = ap.parse_args()
    if args.cmd == "discover":
        return discover(shareurl.resolve(args.share_url))
    if args.cmd == "enumerate":
        return enumerate_share(shareurl.resolve(args.share_url), Path(args.out), args.row_limit)
    if args.cmd == "csv":
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        out = Path(args.out) if args.out else Path(args.manifest).with_suffix(".csv")
        n = write_csv(manifest, out)
        print(f"Wrote {out} ({n} rows).")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
