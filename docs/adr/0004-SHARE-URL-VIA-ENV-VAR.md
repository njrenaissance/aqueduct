# ADR-0004 — Carry the share URL between steps in an environment variable

**Status:** Accepted

## Context

A OneDrive "specific people" share URL is long and ugly, e.g.

```text
https://contoso-my.sharepoint.com/:f:/r/personal/jdoe_contoso_com/Documents/Case%20Discovery?e=AbCd1234&sharingv2=true&fromShare=true
```

Two steps inherently need it: `login` (to open the browser) and `webenum enumerate`
(to resolve the share). Typing it twice is error-prone, and a mismatch between the
two would silently enumerate the wrong thing. The download and validate steps do
**not** need it — they read everything from `manifest.json` (which records
`share_url` and the site `root.webUrl` for provenance).

Options considered: a config file in the project (rejected — data doesn't belong in
the repo, per [ADR-0003](0003-AUTH-IN-USER-CONFIG-DIR.md)); a file in `~/.aqueduct`
(workable but another file to manage); or an environment variable (standard, visible,
easy to override per-invocation).

## Decision

Carry the share URL in the **`ONEDRIVE_SHARE_URL`** environment variable, **scoped to
the current shell session — not persisted machine-wide.**

- `login` **surfaces** the URL: it prints the one-liner to set `ONEDRIVE_SHARE_URL`
  in the current shell (PowerShell / cmd / bash). It does **not** run `setx` and does
  **not** write the variable to the persistent user environment.
- `login` and `webenum` take the share URL as an **optional** argument that defaults
  to `ONEDRIVE_SHARE_URL`; an explicit argument always wins.
- Resolution lives in one helper (`aqueduct.shareurl`): arg → env → a clear
  error telling the user to run `login` or pass a URL.
- `webenum enumerate` **echoes the resolved URL** (`Enumerating share: …`) before it
  walks, so a wrong value is visible before a manifest is produced.

### Why session-scoped, not `setx`

The share URL is **per-case**. Persisting it into the Windows user registry with
`setx` would leave it set in *every* future terminal until explicitly cleared — so
weeks later, a fresh terminal opened for a *different* case, without a re-`login`,
would silently enumerate the **previous** case's URL. For a tool whose purpose is a
correct, defensible record, that silent misattribution is a real hazard, and the
payoff (saving one `login`/URL on the second case) is small — an asymmetric trade we
resolve in favor of correctness. `setx` also cannot affect the *current* shell
anyway, so it only ever served the stale-across-cases scenario.

## Consequences

- Type the URL once per session (at `login`); running the printed one-liner carries
  it to `enumerate` in that shell. A new terminal starts clean — re-run `login` or
  pass the URL, which is the safe default for a new case.
- A user who genuinely keeps one share for a long time can opt into stickiness
  themselves (`setx`, or a shell profile export) — it's a deliberate manual choice,
  not a hidden default.
- The variable holds only a sharing URL (not a secret); it is per-session state,
  committed nowhere.
