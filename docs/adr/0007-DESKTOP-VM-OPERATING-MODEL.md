# ADR-0007 — Desktop-VM operating model for unsupported small practices

**Status:** Accepted — governs the pipeline in [SPEC](../SPEC.md) (forward-looking; not
yet implemented in the shipped collection tool).

## Context

The intended users are **independent / small defense-counsel practices with limited
resources and no IT or support staff**. An earlier line of thinking favored running
the pipeline as a headless server/container (automated, IaC, least-privilege). That
is the "right" answer *only* for an organization with people to run it — for the
actual users, a server "someone has to manage" is a barrier that kills adoption.

The workload is also mostly interactive-by-nature at the one hard point: the
"specific people" share requires an **interactive MFA sign-in** ([ADR-0001](0001-ENUMERATE-VIA-WEB-SESSION.md)),
which is awkward on a headless server (inject/refresh a saved session) but trivial on
a desktop (sign in in the browser).

## Decision

**Optimize for a desktop VM (cloud PC) as the primary operating model**, and keep two
alternatives:

- **Primary — desktop VM.** A paralegal signs in to the share in the desktop's own
  browser and runs the tool. No server to administer, no injected credentials. The
  session lives on that machine ([ADR-0003](0003-AUTH-IN-USER-CONFIG-DIR.md)).
- **Heavy integrity is delegated to managed Azure services** the user never
  administers — the immutable WORM vault (Blob) and the tamper-evident ledger
  ([ADR-0009](0009-TAMPER-EVIDENT-LEDGER.md)). Server-grade durability, no server
  to run.
- **Option — server/container** paradigm for resourced organizations that want
  automated, scheduled, IaC-managed collection.
- **Option — local disk** download + validate, for users who just want files on a
  drive with no cloud at all.

## Consequences

- **Simplicity/teachability is now a first-class product requirement**, not a nicety:
  opinionated defaults over many flags, clear prompts, hard to misuse, and eventually
  a friendly TUI/GUI aimed at a paralegal.
- The interactive login is a **feature** (familiar browser sign-in), not a wrinkle.
- There is still a **one-time provisioning** step (stand up the cloud PC + storage
  account/container) — but that is a *provider* task to template/script, not day-to-day
  user operations. Ongoing burden on the counselor is ~zero.
- This **supersedes the earlier "headless server is the right thing" leaning** for
  this audience; the server model is retained only as an option.
