# ADR-0008 — Stream-to-Blob preservation with ledger-driven restart

**Status:** Accepted — governs the pipeline in [SPEC](../SPEC.md) (forward-looking; not
yet implemented in the shipped collection tool).

## Context

On the primary host — a cloud desktop ([ADR-0007](ADR-0007-DESKTOP-VM-OPERATING-MODEL.md))
— local disk is small and volatile (FSLogix profile container), and evidence should
**not persist on the desktop** any longer than necessary (custody + cleanup). Landing
500+ GB locally, then uploading, is doubly wrong there.

## Decision

**Stream each file straight to the Azure Blob WORM vault; never land it locally** on the
primary path. Keep **local-disk download+validate as an explicit option** for users who
want files on a drive.

Streaming mechanics (Azure block blobs):

- Download from `download.aspx` in chunks; per chunk, in one pass: **update SHA-256**
  and **`stage_block`** to Azure. `commit_block_list` at the end. Only the current
  chunk is in memory (RAM ≈ concurrency × chunk size); nothing hits local disk.
- Use the async Blob SDK so it composes with the existing asyncio download loop.

**Two hash layers, different jobs — they do not compete:**

- **SHA-256 = the evidence fingerprint** (ours; [ADR-0006](ADR-0006-SHA256-INTEGRITY-HASH.md)):
  cryptographic, tamper-evident, portable, recorded in the ledger. This is what proves
  byte integrity across every hop and at the destination.
- **MD5 = Azure's native transport/content check**: stage each block with content
  validation so Azure verifies a per-block MD5/CRC64 **on receipt** (catches corruption
  during the upload), and set the blob's `Content-MD5` on commit so Azure-native tools
  can verify the blob without our tooling. MD5 is *not* the evidence hash; it guards the
  wire and enables Azure-side verification.

**Restart/resume** — the resume state is the **ledger** (per-file status), not a local
`.part`. Every file is in one of three states:

1. **Not started** → upload normally.
2. **Committed + verified** (ledger says so; backstopped by `blob.exists()` + size +
   the SHA-256 stored as blob metadata) → **skip**.
3. **Interrupted mid-file** (blocks staged, never committed):
   - **v1 (default):** re-download the file and commit fresh. The ledger skips every
     completed file, so only the interrupted one is redone.
   - **v2 (large-file optimization):** read the uncommitted block list, **Range-resume**
     the source download from the staged offset, stage the remainder, commit. Needs
     deterministic block sizing/IDs.

**Hashing across a resume:** the in-flight SHA-256 cannot resume — staged (uncommitted)
blocks are not readable, and the local hash state is gone. So: **hash inline for files
uploaded in one shot** (free); **for resumed files, compute the SHA-256 by reading the
committed blob back** (an in-region read — fast, cheap same-region, no WAN re-download).
This mirrors the disk path's "inline for a fresh download, from-disk for a resumed one."

## Consequences

- **Evidence never persists on the desktop** — only transient in-RAM chunks; a strong
  custody property and the answer to the FSLogix constraint.
- The **ledger is load-bearing** for restarts (state) as well as integrity — reinforcing
  [ADR-0009](ADR-0009-TAMPER-EVIDENT-LEDGER.md).
- Resume is more involved than the disk `.part` model, and **validation shifts** from
  disk-vs-manifest to **Blob-vs-manifest** (size + our SHA-256, plus Azure's `Content-MD5`).
- Resumed files cost one extra in-region read-back to hash; one-shot files stay free.
