# ADR-0013 — Upload the validated download to the immutable Azure Blob vault

**Status:** Accepted — implements Stage 2 (Preserve) of the [SPEC](../SPEC.md) from the local download;
streaming straight from the share ([ADR-0008](0008-STREAM-TO-BLOB-PRESERVATION.md), issue #14) is a separate,
later path that reuses this one's conventions.

## Context

`filecopy` + `validate` prove the local download is complete and unchanged since acquisition, but the local disk
is not the vault. The [SPEC](../SPEC.md) (§1, §3) makes an immutable (WORM) Azure Blob container the source of
truth, and says a stage is "completed" only when a hash matches — never merely because bytes arrived (§4).
Without an upload step there is no defensible preserved copy and nothing for later stages to build on.

The operators are small defense-counsel practices with no IT staff ([ADR-0007](0007-DESKTOP-VM-OPERATING-MODEL.md)),
so the command must be teachable, resumable, and must not ask them to handle keys. The tamper-evident ledger
([ADR-0009](0009-TAMPER-EVIDENT-LEDGER.md)) does not exist yet, so the record of what was preserved has to travel
with the evidence for now.

## Decision

- **Dependencies.** Add `azure-storage-blob` and `azure-identity`. The sync SDK is driven by a thread pool
  (`--concurrency`): the work is disk/network bound, and it avoids the async SDK's extra weight. (ADR-0008's async
  choice is for the streaming path, which composes with the async download loop.)
- **Auth.** The operator's own Azure identity through `DefaultAzureCredential` (e.g. after `az login`). Account
  URL, container, and folder prefix come from `--account-url`/`--container`/`--dest-prefix` or
  `AQUEDUCT_BLOB_ACCOUNT_URL`/`AQUEDUCT_BLOB_CONTAINER`/`AQUEDUCT_BLOB_PREFIX`. Account keys, connection strings,
  and SAS tokens are not accepted (a URL carrying a query string is rejected), so no secret enters the repo, the
  shell history, or a log. The Azure SDK loggers are held at WARNING because they log request URLs at INFO.
- **Input gate.** Shared with `spupload` (`aqueduct.validated`): a file is admitted only if its
  `validate_results.csv` row is `ok`, carries a SHA-256, and has no hash/segment mismatch, and its hash agrees
  with `filecopy_results.csv`. Each file is re-hashed before sending and must equal the validated value, so a file
  changed since `validate` is `rejected`. While staging, the SHA-256 of the bytes actually sent is compared with
  the hash taken first, so a file that changes mid-upload is never committed.
- **Naming — one folder per collection.** `<prefix>/data/<manifest path>` for evidence and
  `<prefix>/_audit/<run-id>/<file>` for the record, where the prefix is `<matter-id>/<collection-id>`. The prefix
  is **required** (evidence never lands at the container root by accident) and is rejected if it has a leading `/`
  or an empty, `.`, or `..` segment. A new collection of the same share gets a new `<collection-id>`.
- **Transfer.** Fixed-size blocks (default 4 MiB) with deterministic block IDs, staged with
  `validate_content=True` so Azure verifies a per-block transport hash on receipt, then one `commit_block_list`
  that sets `Content-MD5` and the blob metadata. Metadata is written at commit because it cannot be amended once a
  blob is under an immutability policy. Keys: `sha256` (the evidence fingerprint), `uniqueid` (the source item's
  UniqueId, SPEC §5's identity key; omitted if the manifest has none), `sourcepath` (the manifest path,
  percent-encoded because header values must be ASCII). An interrupted file is re-sent whole; Azure discards
  uncommitted blocks.
- **Verification.** After commit the blob's properties are read back: size, the `sha256` metadata, and the
  Content-MD5 (when present) must match what we hashed locally. Only then is the file `ok`; a mismatch is retried
  with bounded backoff and finally recorded as `fail`. SHA-256 is authoritative; MD5 is Azure's transport check
  (SPEC §4). This compares against metadata *we* wrote — it proves the commit matches what we sent, not that the
  stored bytes cannot have changed; a deep re-read of the blob is deferred.
- **Idempotency and immutability.** A blob already at the name with the same size and `sha256` is skipped. One with
  a *different* hash is a **`conflict`**: reported, exit status non-zero, and never overwritten or versioned.
  Re-collection semantics (SPEC §12) stay an open question; the operator uses a new `<collection-id>`.
- **Audit record in the vault.** After the data, the command stores the acquisition record under
  `_audit/<run-id>/`: `manifest.json`/`.csv`, `filecopy_results.csv`, `validate_results.csv`,
  `upload_results.csv` (each with its `.metadata.json` sidecar), any `--audit-file`, a `SHA256SUMS` in
  `sha256sum` format covering every preserved data file and every audit file except itself and `custody.json`, and
  `custody.json` (tool, version, operator, host, times, destination, outcome counts, and the SHA-256 of
  `SHA256SUMS`). `upload_results.csv` only exists once the data phase ends, so the audit phase is second and its
  own uploads are not rows in it. The `<run-id>` (UTC timestamp) gives each run its own immutable folder, so a
  re-run never collides with an earlier run's differing `upload_results.csv`.
- **Out of scope** (separate issues): streaming from the share without a local copy (ADR-0008, #14), the ledger
  (ADR-0009), classification (Stage 3), SharePoint review sync (Stage 4).

## Consequences

- The operator gets a preserved, hash-verified, immutable copy and a self-describing record of how it got there,
  with no database to run and no secrets to hold.
- The `_audit/` bundle is only as trustworthy as the container's immutability policy plus an out-of-band copy of
  the `SHA256SUMS` hash (the workflow tells the operator to record it). It cannot prove custody events after the
  upload or resets; it is not the ledger, which can later ingest it as its seed.
- Every run adds an `_audit/<run-id>/` folder, including re-runs that skip all data; the vault accumulates small
  audit copies, and that is the point.
- The local copy must exist until the upload finishes, which is wrong for the small-disk desktop VM at scale —
  the reason ADR-0008's streaming path (#14) exists.
- The sync SDK means one thread per in-flight file; very high `--concurrency` costs memory (one block per thread).
- Adds the Azure SDK to a tool that had only `httpx` and Playwright, and a one-time Azure setup (container with a
  retention policy or legal hold, a write role for the uploader) that this ADR does not automate.
