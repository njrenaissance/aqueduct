# ADR-0014 — Publish the vault to SharePoint by streaming, with a storage policy

**Status:** Proposed — extends [ADR-0012](0012-DIRECT-GRAPH-UPLOAD-TO-SHAREPOINT.md) (the Graph destination and
upload engine) and reads the vault laid out by [ADR-0013](0013-UPLOAD-TO-IMMUTABLE-BLOB.md). The numeric default in
the storage policy is a placeholder to confirm before this is Accepted.

## Context

SharePoint is the review surface and the immutable Azure Blob vault is the source of truth (SPEC §7). SharePoint
Online cannot mount a blob container, so the review copy has to be copied in. Going Blob → local disk →
`spupload` needs the local disk again, which is the wrong shape on the small desktop VM
([ADR-0007](0007-DESKTOP-VM-OPERATING-MODEL.md)).

SharePoint storage is also a limited, billed quota, and a review library does not need everything the vault holds.
Very large files and audio/video files consume a disproportionate share of it and are rarely reviewed as documents.
[ADR-0012](0012-DIRECT-GRAPH-UPLOAD-TO-SHAREPOINT.md) screens only what SharePoint itself refuses (its 250 GB
limit, tenant-blocked extensions) and leaves both off by default. That is a technical limit, not a storage policy.

## Decision

- **A new `publish` command** streams each blob from `<prefix>/data/` straight into a SharePoint library through
  the ADR-0012 Graph client and upload engine: simple PUT for small files, a resumable upload session for large
  ones, each chunk read from the blob by range. No local copy. Blob access is `DefaultAzureCredential` with read-only
  rights; the vault is never written. Destination flags are the same as `spupload`.
- **Storage policy: some files are not published.** Before any bytes are read, each blob is screened by name and
  size, and a file is set aside (not copied) when it:
  - is larger than `--max-bytes` (default **placeholder: 1 GiB**, to be confirmed), or
  - has an audio or video extension (default set: `.mp4 .m4v .mov .avi .mkv .wmv .flv .webm .mpg .mpeg .mp3 .wav
    .m4a .aac .flac .ogg .opus .wma .aiff`), or
  - has an extension in the operator's `--skip-ext` list, or exceeds SharePoint's own 250 GB limit.
  The audiovisual rule is on by default and is switched off only explicitly (`--include-av`); the size limit is
  raised or removed explicitly (`--max-bytes`). Screening is by extension and size only; content is never inspected.
- **A skipped file is recorded, not hidden.** It gets a `skipped` row in `publish_results.csv` with the reason
  (`over size limit`, `audiovisual`, `excluded extension`), its size and its SHA-256 from the blob metadata, and a
  count in the run summary. It stays in the vault untouched. `skipped` is not a failure: it does not change the
  exit code. A reviewer-facing placeholder or link for skipped files is out of scope here (SPEC §7's stub routing).
- **Verification** is the ADR-0012 contract, applied to a stream. SHA-256 and QuickXorHash are computed while the
  bytes pass through. A file is `ok` only when the streamed SHA-256 equals the blob's stored `sha256` metadata and
  SharePoint reports the same size and QuickXorHash; a missing server hash is `size-only`; any disagreement is
  `fail`. The hash cannot be checked before sending without reading each blob twice, so a failed transfer is
  flagged and the SharePoint item is not trusted.
- **Idempotency.** An item already on SharePoint with a matching size and QuickXorHash is skipped; policy
  screening runs first, so a file excluded by a later, stricter policy is not uploaded on a re-run.
- **Record.** `publish_results.csv` and a metadata sidecar, in the style of the other stages. Because the source is
  the vault, the ADR-0012 "vault bypassed" warning does not apply, but the record still states that the SharePoint
  copy is a derived review copy and that policy-skipped files exist only in the vault.

## Consequences

- No local disk is needed to build the review library, and the copy provably derives from the preserved bytes.
- The review library is intentionally incomplete. Reviewers, and anyone reading the results, must know that
  audio/video and very large files are in the vault only; the `skipped` rows and the summary count are the only
  guardrail, and they are not enforced in SharePoint itself.
- Extension screening is a heuristic: a video renamed `.dat` is published, and a legitimate small audio exhibit is
  skipped until `--include-av` is used. The size limit is crude for the same reason.
- `spupload` keeps its ADR-0012 behavior (technical limits only). The two commands will disagree about what belongs
  in SharePoint unless the same policy is applied to `spupload` too; that is a follow-up decision, not made here.
- A hash mismatch after upload leaves an untrusted item on SharePoint until it is replaced or removed.
- Adds one entry point and reuses the Graph and Azure dependencies already present; the QuickXorHash cost per file
  is the ~43 MB/s Python port noted in ADR-0012.
