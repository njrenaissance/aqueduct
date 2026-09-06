# ADR-0006 — SHA-256 as our integrity hash (not Microsoft's QuickXorHash)

**Status:** Accepted

## Context

We want a per-file content hash for the downloaded evidence, to prove integrity now
and across later hops (local → Azure Blob → SharePoint; see [SPEC](../SPEC.md)).

The obvious candidate was **QuickXorHash**, the hash OneDrive/Graph reports, because
matching it would prove a download is byte-for-byte what the *source* holds. Two
findings killed that rationale for the web-session path:

1. **The source hash is unavailable here.** The web listing
   (`RenderListDataAsStream`) exposes no content hash, and every endpoint that does
   carry `file.hashes.quickXorHash` — Graph, and the vroom driveItem behind each
   item's `.spItemUrl` — returns **403** under our web session (the same
   authorization wall as [ADR-0001](ADR-0001-ENUMERATE-VIA-WEB-SESSION.md), verified
   by probing the live share). So there is **nothing to match** against.
2. **QuickXorHash is slow.** Our faithful pure-Python port runs at **~43 MB/s**;
   hashing 533 GB would take ~3.5 h per core.

With no source value to reconcile against, QuickXorHash loses its only advantage —
we are free to pick any hash, and should pick a fast, defensible one. Benchmarked on
target hardware (SHA-NI available):

| Hash | Throughput | 533 GB, 1 core |
|---|---|---|
| QuickXorHash (pure Python) | 43 MB/s | ~3.5 h |
| **SHA-256** | **3,417 MB/s** | **~2.7 min** |
| MD5 | 938 MB/s | minutes |
| CRC32 | 4,074 MB/s | ~2 min (but 32-bit, not tamper-evident) |

SHA-256 is ~80× faster than QuickXorHash *and* ~3.6× faster than MD5, because the CPU
hardware-accelerates it (SHA-NI) — it is faster than the disk or network can feed it,
so hashing is no longer a bottleneck.

## Decision

Use **SHA-256** as the integrity hash for the web-session path.

- `filecopy` computes it **inline as bytes stream from the network** (no extra disk
  read; `hashlib` releases the GIL so it doesn't serialize the async downloads), and
  records the hex digest in a `sha256` column in `filecopy_results.csv`. On a resume
  the existing `.part` prefix is read once to seed the hasher; already-present files
  are hashed from disk under a small (disk-bound) worker pool, reusing a prior run's
  digest when unchanged.
- `quickxor.py` is retained **only** for `odenum`'s Graph path, where the source
  *does* return a QuickXorHash and matching it is the entire point (`selftest` /
  `verify`).
- Optionally also record **MD5** later, solely to line up with Azure Blob's native
  `Content-MD5` on upload — never as the primary fingerprint.

## Consequences

- **Fast and free:** hashing overlaps the (network-bound) download at ~3.4 GB/s, so
  it adds no meaningful wall-clock and needs no extra pass.
- **Defensible:** SHA-256 is the forensic/legal standard — cryptographic and
  tamper-evident, stronger than MD5/CRC and stronger than a Microsoft-proprietary
  hash.
- **Portable:** the digest verifies with any standard tool (`sha256sum -c`,
  `Get-FileHash`, `certutil`) at any later location — the basis for verifying each
  hop in [SPEC](../SPEC.md).
- **Not a source match.** Because no source hash is obtainable, SHA-256 is a *dated
  fingerprint of the bytes we collected* (integrity from acquisition forward), not
  proof of equality with the share's own stored hash. Validation against the source
  manifest therefore stays **size + completeness**
  ([ADR-0002](ADR-0002-DOWNLOAD-VIA-DOWNLOAD-ASPX.md)); the SHA-256 is the
  chain-of-custody fingerprint on top.
