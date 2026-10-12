# ADR-0012 — Direct upload of a validated download to SharePoint via Graph

**Status:** Accepted — does not change [ADR-0001](0001-ENUMERATE-VIA-WEB-SESSION.md); narrowly
reuses a QuickXorHash that [ADR-0010](0010-WEB-SESSION-ONLY-REMOVE-GRAPH-PATH.md) removed (its
decision stands).

## Context

Some operators need the files in SharePoint now — for review in M365/Copilot — and have
Microsoft Graph access to **their own** destination site, without going through the Azure
Blob vault ([ADR-0008](0008-STREAM-TO-BLOB-PRESERVATION.md)). The [SPEC](../SPEC.md) (§7)
models SharePoint as a *derived* review surface fed from the vault, so a direct upload is a
deliberate shortcut around the immutable store and needs an explicit decision.

[ADR-0001](0001-ENUMERATE-VIA-WEB-SESSION.md) and ADR-0010 rule out Graph for the **source**
"specific people" discovery share, which 403s the Graph path. That constraint does not apply
to the **destination**: the operator controls access to it, so Graph is appropriate there.

## Decision

- **Chain of custody.** A direct upload is a **convenience/review copy only — not
  evidence.** The vault (when used) stays the authority. The command prints a warning, and
  its results CSV and `.metadata.json` sidecar record that the vault was bypassed. Vault-first
  is **not** required; that would defeat the purpose of the shortcut. The operator is warned
  that they are stepping outside the normal process, but the command is a standalone tool,
  useful outside the standard Azure Blob → SharePoint sync.
- **Input gate.** Upload only files that passed `validate --hash` (size + SHA-256 match).
  Enforcement: the command reads `validate_results.csv` and admits a manifest file only if its
  row has `status` `ok`, a recorded SHA-256, and no `hash_check` or `segment_check` mismatch;
  everything else is rejected with a reason. A missing results file is an error. Because the
  gate reads a prior run's record, it is only as fresh as that run: the upload re-hashes each
  file and requires its SHA-256 to equal the validated value (see Verification), so a file
  that changed after `validate` is rejected rather than uploaded.
- **Auth.** **App-only (client credentials)** against the **destination tenant only**, which is
  fixed per deployment. An app registration in that tenant holds the `Sites.Selected` Graph
  application permission, granted write access to the one target site — least privilege. The
  tool requests a token itself from the tenant's `/oauth2/v2.0/token` endpoint with `httpx`
  (no `msal`/`azure-identity`). The tenant ID, client ID, and client secret are read from a
  config file in `~/.aqueduct` ([ADR-0003](0003-AUTH-IN-USER-CONFIG-DIR.md)); the secret may
  instead come from an environment variable. Certificate credentials are not supported. No secrets in the repo, logs, CSVs, or
  docs (fictional placeholders only). The `Authorization` header is never logged, and the
  token is refreshed once on a `401`.
- **Client.** `httpx` (already a dependency). Not `msgraph-sdk`: ADR-0010 just removed a
  heavier Graph dependency set for a single-path tool, and the surface needed here is small
  (simple PUT, upload session, item metadata GET).
- **Transfer.** Simple PUT for small files; Graph upload sessions (chunked, resumable) for
  large ones, resuming from the session's `nextExpectedRanges`. The upload logic is ported
  from the sibling `courier` project (`../downloader`), which already runs it against Graph.
  The manifest folder structure is preserved under a configurable library/folder. Upload
  sessions use `conflictBehavior: replace`, but an item whose size and QuickXorHash already
  match is skipped first, so a replace only happens over a mismatching file.
  Honor `429`/`Retry-After`; retry transient `5xx` with bounded backoff.
- **Destination.** The target is given as a pasted SharePoint **folder URL** (`--dest-url`) or
  explicitly as `--site-url` + `--library` [+ `--target-folder`]; passing both, or neither, is
  a usage error. Unlike `courier`, the URL is **parsed locally** and resolved with site and drive
  lookups, not Graph's `/shares/` endpoint (not verified to work with app-only `Sites.Selected`
  tokens): the first path segments give the site (`/sites/<x>`, `/teams/<x>`, or the root site),
  the next the library, the rest the folder. The library is matched to a drive by its `webUrl`,
  because a URL segment such as `Shared Documents` is not the drive's display name
  (`Documents`). A browser URL carrying an `id=` parameter (`.../Forms/AllItems.aspx?id=...`) is
  honored. Subsites are not supported. A URL the app has no access to fails with a clear error.
- **Unsupported files.** Files over SharePoint's size limit or with blocked extensions are
  **skipped and reported with a reason**, not uploaded — consistent with SPEC §7's stub/link-only
  routing (the stubs themselves are out of scope here).
- **Verification.** SharePoint/OneDrive for Business returns only `quickXorHash` (no
  `sha256Hash`), so a file is "completed" when the item's server-side **size** matches and,
  where SharePoint reports one, its **`quickXorHash`** matches our local value (as in
  `courier`). SharePoint should always report the hash, but a missing one does not hold up
  the upload: the file is accepted on size alone and a warning is logged. That outcome is
  recorded as a distinct **`size-only`** status in the results CSV and counted separately in the
  run summary, never as a plain pass, so an operator can see how many files were not
  hash-verified and re-check them later. A reported hash that differs is always a failure.
  We compute QuickXorHash locally **before**
  uploading (which also powers the skip check below, and survives a resumed session), so
  verification needs no re-download. SHA-256 and QuickXorHash are computed **in the same
  read** of the file, and the SHA-256 must equal the value `validate` recorded; this ties the
  bytes we send (and whose QuickXorHash we compare) to the bytes that were validated, so there is no
  window in which the file can change between the two hashes. Our SHA-256 stays the evidence hash and is
  recorded alongside ([ADR-0006](0006-SHA256-INTEGRITY-HASH.md)); QuickXorHash is only the
  transport check against SharePoint. This brings back a QuickXorHash implementation that
  [ADR-0010](0010-WEB-SESSION-ONLY-REMOVE-GRAPH-PATH.md) removed, recovered from git history
  and scoped to this module's verification; ADR-0010's decision (no Graph *source* path)
  stands.
- **Idempotency.** Re-runs are driven by the **destination**, as in `courier`: an item already
  on SharePoint with a matching size and QuickXorHash is skipped, anything else is (re)uploaded.
  The results CSV is the per-run record, not the resume state, so a re-run also re-validates
  everything already uploaded.

## Consequences

- Operators get a fast path to M365/Copilot review without the vault.
- The path produces a copy outside the immutable store; reviewers must not treat it as
  evidence. The warning and sidecar flag are the only guardrails — they are not enforced.
- Adds a second Graph-facing code path (destination only) and a new auth surface to document
  and secure, after ADR-0010 had removed the previous one.
- Needs one-time tenant setup: an app registration, the `Sites.Selected` permission with admin
  consent, and a per-site grant. The client secret is a long-lived credential that must be
  rotated; moving to certificate credentials would be a future change.
- No interactive sign-in, so the tool runs unattended. This ties a deployment to one tenant
  and one app identity, which suits the fixed-tenant case.
- Verification costs one local QuickXorHash pass per file (the SPEC measures the Python port at
  ~43 MB/s, versus ~3.4 GB/s for SHA-256), but needs no re-download. It adds a second hash
  to maintain, so tests must pin it against known QuickXorHash vectors.
- The upload and QuickXorHash code is a deliberate **copy** of `courier`'s, not a shared
  dependency (the same approach `courier` took with aqueduct's download engine). The two
  copies can drift, so extracting a shared component is deferred until that cost is real.
- **Download and upload verify differently, by necessity.** Download/`validate` can only
  check size against the source manifest (the source exposes no hash, ADR-0006); its SHA-256
  is compared against our own recorded value, so it detects change since acquisition, not a
  bad download. Upload compares size and a hash against the *destination's* own report, which
  is a genuine end-to-end check of what we sent. "Validated" therefore means a stronger
  guarantee on the upload hop than on the download hop, and a `size-only` upload is no
  stronger than the download check.
- Two hashes now appear in this module's records: SHA-256 (evidence) and QuickXorHash
  (SharePoint transport check). They are never interchangeable.
