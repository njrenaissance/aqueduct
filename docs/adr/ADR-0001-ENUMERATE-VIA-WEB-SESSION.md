# ADR-0001 — Enumerate through the interactive web session, not the Graph API

**Status:** Accepted

## Context

The goal is a **defensible, dated record** of exactly what a OneDrive/SharePoint
discovery share contained — a manifest of every file (path, size, dates, and where
possible a content hash) — and later proof that what we downloaded matches it.

The obvious path is the **Microsoft Graph API** (what `odenum.py` uses, and what
`rclone` uses under the hood): sign in, call `GET /v1.0/shares/{token}/driveItem`,
walk the children. That works for **"anyone with the link"** shares.

But sharing links are not all the same, and the difference is exactly what bites:

| Share type | Who can open it | Graph `/shares/{token}` works? |
|---|---|---|
| **Anyone with the link** | Any signed-in (sometimes anonymous) user | Yes — resolves for any account |
| **People in your organization** | Any account in the *owner's* tenant | Only if you're in that tenant |
| **Specific people** (named recipients / external guests) | Only the invited identities | **No — 403 for delegated app tokens** |

Our share is the third row. Running `odenum.py enumerate` as the correct recipient
authenticated successfully, then failed on the first data call:

```text
GET https://graph.microsoft.com/v1.0/shares/{token}/driveItem?$select=...
-> 403 Client Error: Forbidden
```

The failure code is the whole diagnosis:

- **401 Unauthorized** would mean *"we don't know who you are"* — an authentication
  problem.
- **403 Forbidden** means *"we know exactly who you are, and this identity is not
  allowed to do this."* Sign-in worked; the **authorization** for this resource is
  what's missing.

Yet the same recipient can open the identical link in a browser and see every file.
Same person, same grant, opposite result — because the two paths present **different
credentials**, and the share was granted to only one of them:

- **Browser (works).** Opening a "specific people" link *redeems* it interactively:
  SharePoint verifies the signed-in identity against the named recipients and
  establishes a **web session** (the `FedAuth`/`rtFa` cookies) scoped to that site.
  The file listing you see is the page calling SharePoint's **own** internal
  endpoints (`_api/.../RenderListDataAsStream`) with those cookies. Grant and session
  are the same mechanism.
- **Graph API (403).** A delegated Graph token from a third-party public client is a
  *different credential in a different trust context*. For a specific-people/guest
  grant the resource does not honor it. Reinforcing factors: the grant is bound to
  the interactive redemption (not to arbitrary app tokens); government/enterprise
  tenants commonly restrict which apps may touch their data while allowing the
  first-party web experience; and a cross-tenant guest token may lack the
  resource-tenant context `/shares` resolution needs.

The load-bearing fact: **the permission is for *you in a browser*, not for *any app
you point at the API*.** Changing Graph scopes, switching the public client, or
re-consenting cannot fix it — nothing is wrong with authentication; the
authorization simply does not extend to that token. `rclone` is also a Graph client,
so it hits the identical 403.

## Decision

Enumerate using the **credential the share actually honors — the interactive web
session**. `webenum.py` drives a headless browser session (saved by `login.py`) and
walks the subtree via SharePoint's own `RenderListDataAsStream` endpoint
(`Scope='RecursiveAll'`, paged) — the same internal API the web UI uses, the one the
cookies are authorized for. Output is rebuilt into the same `manifest.json` schema
`odenum.py` produces, so downstream steps are shared.

## Consequences

- We need a saved browser session (see [ADR-0003](ADR-0003-AUTH-IN-USER-CONFIG-DIR.md))
  and a browser automation dependency (Playwright).
- **Hashes are weaker evidence here.** Graph returns a server-computed `quickXorHash`
  per file; the web listing does not expose one, so validation falls back to **size**
  (see [ADR-0002](ADR-0002-DOWNLOAD-VIA-DOWNLOAD-ASPX.md) for how we still record a
  dated hash of the downloaded bytes). This is a property of the data source, not a
  bug.
- **Prefer `odenum.py` (Graph) when it works** — for "anyone with the link" shares,
  or when you are inside the owning tenant, it is simpler and gives stronger per-file
  hashes. Reach for `webenum.py` specifically when Graph returns 403 on a share a
  browser can open.
- Enumeration currently assumes a personal OneDrive share (library named "Documents";
  folder passed as the `id=` param on an `onedrive.aspx` URL). The `discover`
  subcommand logs the internal APIs a page calls, to adapt when those assumptions
  don't hold.
