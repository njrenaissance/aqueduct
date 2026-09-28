# ADR-0010 — Web-session only: remove the Graph-API (odenum) path

**Status:** Accepted — supersedes the `odenum`/`quickxor`-specific parts of
[ADR-0001](0001-ENUMERATE-VIA-WEB-SESSION.md), [ADR-0005](0005-SRC-PACKAGE-LAYOUT.md),
and [ADR-0006](0006-SHA256-INTEGRITY-HASH.md). Their core decisions still stand.

## Context

Aqueduct originally shipped two enumeration/download paths:

- the **web session** (`login` → `webenum` → `filecopy` → `validate`), and
- a **Graph-API path** (`odenum.py`, plus `quickxor.py` implementing OneDrive's
  QuickXorHash so downloads could be matched against Graph's reported hash).

But Aqueduct's whole purpose is **"specific people" discovery shares**, and those
**403 the Graph path** ([ADR-0001](0001-ENUMERATE-VIA-WEB-SESSION.md)) — so `odenum`
only ever worked for "anyone with the link" shares, a case outside the mission. Once
we standardized on **SHA-256** as our own integrity hash
([ADR-0006](0006-SHA256-INTEGRITY-HASH.md)), QuickXorHash lost its only reason to
exist (it mattered solely for matching Graph's value, which we can't get anyway).
So the Graph path was a second, rarely-usable code path carrying its own
dependencies (`msal`, `requests`) and a hand-ported hash, for no mission benefit.

## Decision

**Remove the Graph-API path entirely.** Delete `odenum.py` and `quickxor.py`;
Aqueduct is **web-session only**. Integrity is SHA-256, computed directly. Drop the
now-unused `msal` and `requests` dependencies (runtime deps are `httpx` + `playwright`).

## Consequences

- A smaller, single-path tool: one workflow to learn and teach, fewer dependencies,
  no dead second path.
- **No built-in "anyone with the link" Graph shortcut** anymore — but that was never
  the target case; for such a share, a standard Graph client (e.g. `rclone`) works.
- Supersedes: ADR-0001's framing of `odenum.py` as the Graph alternative (the
  web-session decision stands); ADR-0005's package listing of `odenum.py`/`quickxor.py`
  (the src-layout decision stands); ADR-0006's note that `quickxor.py` is retained for
  `odenum`'s Graph path (the SHA-256 decision stands).
