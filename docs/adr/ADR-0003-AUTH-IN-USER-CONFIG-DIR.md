# ADR-0003 — Auth tokens in `~/.odenum`; working data in the current directory

**Status:** Accepted

## Context

The tools handle two very different kinds of file:

- **Auth material** — `auth_state.json` (a live logged-in web session, as sensitive
  as a password) and `.token_cache.json` (odenum's MSAL Graph cache). These are
  per-user secrets that should be reused across runs and **never** committed.
- **Working data** — `manifest.json`/`.csv`, `download/`, `raw/`, logs, results.
  These are per-case artifacts, often large, and are not part of the project.

Originally both sat next to the code (`Path(__file__).parent`). After moving the
code into a `src/` package ([ADR-0005](ADR-0005-SRC-PACKAGE-LAYOUT.md)), "next to the
code" became a package directory deep in `site-packages` — the wrong place for
either kind of file. Two mistakes had to be designed out: writing a secret into the
repo (one `git add .` from disaster), and scattering case data inside the package.

## Decision

- **Auth tokens live in a per-user config directory, `~/.odenum`**
  (`$USERPROFILE\.odenum` on Windows), resolved by `onedrive_enum.paths`. It is
  created on demand with user-only intent and sits outside any repository, so a
  session is shared across runs from any folder and cannot be committed.
- **Working data lives in the current working directory.** Run the tools from a
  per-case data folder (e.g. `./data/`, which is git-ignored); manifests, downloads,
  and logs land there. Nothing about the project depends on that folder's contents.

`.gitignore` still lists `auth_state.json` / `.token_cache.json` as defense-in-depth,
in case a token is ever written into a working tree.

## Consequences

- Secrets are structurally separated from both the repo and the package; the
  working tree only ever holds code and (git-ignored) case data.
- One session serves every case: sign in once, then `cd` into each case's data
  folder to run enumerate/download/validate.
- Path helpers are centralized in `onedrive_enum.paths`, so the location is defined
  once. Changing it (e.g. honoring `XDG_CONFIG_HOME`) is a single-file change.
