# CLAUDE.md

Guidance for Claude Code when working in this repo.

## Imports (team standards)

- @.claude/standards/decisions.md
- @.claude/standards/git-workflow.md
- @.claude/standards/testing.md
- @.claude/standards/error-handling.md
- @.claude/standards/logging.md
- @.claude/standards/security.md

Python- and test-specific conventions live in `.claude/rules/` (`python-lang.md`,
`pytest-rules.md`) and load automatically when Claude touches matching files.

## What this project does

Produce a **defensible, dated record** of exactly what a OneDrive/SharePoint
discovery share contained, download every file, and prove the download matches the
record. Packaged as `src/onedrive_enum/`, exposing console entry points.

| Command | Module | Role |
|---|---|---|
| `login` | `login.py` | Headed sign-in; saves the web session + surfaces the URL (session-scoped) |
| `webenum` | `webenum.py` | Enumerate a "specific people" share via the **web session** (headless Playwright) → `manifest.json`/`.csv` |
| `filecopy` | `filecopy.py` | High-throughput async **download** of every file (resumable) |
| `validate` | `validate.py` | Reconcile the download against the manifest (size + optional hash) |
| `odenum` | `odenum.py` | Alternative enumerate/download/verify via the **Graph API** (device-code auth); for "anyone with the link" shares |

`quickxor.py` is QuickXorHash (OneDrive's content hash); `shareurl.py` and
`paths.py` are shared helpers. The design decisions are recorded as ADRs in
[`docs/adr/`](docs/adr/); the user-facing runbook is [`docs/WORKFLOW.md`](docs/WORKFLOW.md).

## Key constraint — read before touching share access

"Specific people"/guest shares authorize the **interactive web session**, not a
delegated Graph token from a third-party app — so Graph (and `rclone`, also a
Graph client) return **403 Forbidden** even when the recipient can open the share
in a browser. That is *authorization*, not authentication; changing scopes or
clients does not fix it. Full reasoning:
[ADR-0001](docs/adr/ADR-0001-ENUMERATE-VIA-WEB-SESSION.md).

## Python tooling: use `uv`

Use **[uv](https://docs.astral.sh/uv/)** for all Python work — not bare
`pip`/`venv`.

- `uv sync` — install/lock dependencies (also installs the console entry points)
- `uv add <pkg>` / `uv remove <pkg>` — manage dependencies (never hand-edit deps)
- `uv run <cmd>` — run in the project env, e.g. `uv run webenum enumerate`
- `uv run pytest` / `uv run ruff check .` / `uv run mypy src` — the quality gates
- One-time Playwright browser: `uv run python -m playwright install chromium`

## Data & secrets — never commit

Auth tokens live in the per-user config dir `~/.odenum` (outside the repo), not in
the working tree — see [ADR-0003](docs/adr/ADR-0003-AUTH-IN-USER-CONFIG-DIR.md).
Working data lives in the current directory (run the tools from a per-case `./data/`
folder). All of it is git-ignored; never commit, print in full, or send anywhere:

- `~/.odenum/auth_state.json` — saved web session (**as sensitive as a password**;
  expires — re-run `login` when it does)
- `~/.odenum/.token_cache.json` — MSAL token cache (odenum's Graph path)
- `data/`, `manifest*`, `raw/`, `download/`, `*.log`, `*_results.csv` — working data

## Documentation conventions

- **Markdown filenames use CAPITAL CASE** (e.g. `README.md`, `WORKFLOW.md`,
  `ADR-0001-ENUMERATE-VIA-WEB-SESSION.md`). This project rule overrides any
  lowercase-slug convention from the generic `adr-authoring` skill.
- Record significant, hard-to-reverse decisions as ADRs under
  [`docs/adr/`](docs/adr/) — see `.claude/standards/decisions.md` for *when*, and
  [`docs/adr/TEMPLATE.md`](docs/adr/TEMPLATE.md) for the format. Add each new ADR to
  the table in `docs/adr/README.md`.
