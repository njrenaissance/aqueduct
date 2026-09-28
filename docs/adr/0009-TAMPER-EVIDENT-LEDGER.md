# ADR-0009 — Tamper-evident hash ledger

**Status:** Accepted — governs the pipeline in [SPEC](../SPEC.md) (forward-looking; not
yet implemented in the shipped collection tool).

## Context

The hash record is the chain-of-custody proof: it must resist tampering, and it also
serves as the pipeline's resume/idempotency state ([ADR-0008](0008-STREAM-TO-BLOB-PRESERVATION.md)).
Two goals, often conflated, need different mechanisms:

- **Tamper-evident** — any change is *detectable*.
- **Tamper-proof** — a change *cannot be made*.

A plain database (SQL or NoSQL) is neither on its own: anyone with write access can
rewrite rows. Even a hash-chained record can be re-chained wholesale unless its head is
anchored somewhere the writer does not control.

## Decision

Store the hash record in a **tamper-evident stateful ledger, with its digest anchored in
immutable storage the operator cannot rewrite.**

- **Default: Azure SQL Database ledger tables** — cryptographically verifiable
  (Merkle-tree), tamper-evident out of the box, with familiar SQL for the pipeline's
  state/queries.
- **Anchor the ledger digest in immutable WORM Blob** (same vault family as the
  evidence), periodically, so trust lives *outside* the mutable store; optionally add a
  trusted timestamp (RFC-3161).
- **Stronger option: Azure Confidential Ledger** — an append-only, enclave-backed,
  tamper-*proof* ledger with independently verifiable receipts, for deployments that
  need it.
- **Portable minimum:** hash-chain each record (row carries the prior row's hash) so the
  record is self-verifying even outside these services.

The ledger is **managed** — provisioned once, run by Azure, never administered by the
end user ([ADR-0007](0007-DESKTOP-VM-OPERATING-MODEL.md)).

## Consequences

- Any alteration to the hash record is detectable (tamper-evident), and the external
  anchor closes the "rewrite the whole chain" hole.
- The ledger does double duty — integrity record **and** resume/idempotency state — so
  it is the single source of truth the whole pipeline turns on.
- Managed services mean no ledger administration for the counselor, at the cost of an
  Azure dependency and a one-time provisioning step.
- A NoSQL store is workable but would require hand-rolling the chaining, verification,
  and anchoring that SQL ledger tables / Confidential Ledger provide natively — so it is
  not the default.
