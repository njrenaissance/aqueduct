# ADR-0002 — Download via `download.aspx` (Range-capable), not `$value`

**Status:** Accepted

## Context

Downloads reuse the same web session as enumeration ([ADR-0001](0001-ENUMERATE-VIA-WEB-SESSION.md)),
so they must hit a SharePoint endpoint the cookies authorize. Two candidates serve
raw file bytes under the web session:

1. `_api/web/GetFileByServerRelativePath(DecodedUrl=@a1)/$value`
2. `_layouts/15/download.aspx?SourceUrl=<server-relative path>`

The corpus is large — **500+ GB, with individual files above 50 GB** — so two
properties are non-negotiable:

- **Streaming** to disk (never buffer a 50 GB response in memory).
- **Resumable** via HTTP `Range`, so a dropped or retried transfer of a huge file
  continues from the bytes already on disk instead of starting over.

Probing both endpoints with the real session settled it:

| Endpoint | `Range: bytes=100-199` response |
|---|---|
| `$value` | **200 OK**, full `Content-Length`, no `Accept-Ranges` — Range **ignored** |
| `download.aspx` | **206 Partial Content**, `Content-Range: bytes 100-199/…`, `Accept-Ranges: bytes` — Range **honored** |

`$value` returns the whole file regardless of `Range`, which makes resume
impossible; `download.aspx` supports byte ranges.

## Decision

Download every file through **`_layouts/15/download.aspx?SourceUrl=<percent-encoded
server-relative path>`**, streaming to a `.part` file and resuming with a `Range`
header when a partial is present. `filecopy.py` implements this with async
concurrency, a bounded semaphore, and configurable retries; each retry naturally
resumes from the `.part`. A resumed transfer that yields `200` (server ignored
Range) is detected and restarted from byte 0 to avoid corruption.

Because the source exposes no per-file hash, `filecopy` records nothing beyond
size during the pull; `validate.py --hash` computes each downloaded file's
**QuickXorHash** afterward — a *dated hash of the bytes we actually hold*, which is
the strongest evidence available given the source limitation.

## Consequences

- Resume is byte-accurate and verified: a truncated `.part` resumes and reassembles
  to the same QuickXorHash as a clean download.
- Validation against the manifest is **size + completeness** (the source has no hash
  to compare to); the recorded QuickXorHash supports later re-checks that the local
  copy hasn't changed.
- `download.aspx` is a personal-OneDrive/SharePoint web endpoint; a differently
  shaped share may need the `discover` diagnostic to confirm the right download URL.
